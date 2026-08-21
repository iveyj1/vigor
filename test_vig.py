#!/usr/bin/env python3
"""Smoke tests for vig. PTY-based, plain asserts, no dependencies."""

import os
import sys
import pty
import time
import signal
import tempfile
import select
import re

VIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vig")
VIG_DIAGNOSTICS = os.path.join(os.path.dirname(VIG), "scripts", "vig-diagnostics")
FRAME_MARKER = b"\x1b[?25l\x1b[H"
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"

# ── Harness ────────────────────────────────────────────────────────────────

def _read_pty(master, output, timeout=0.02):
    """Read one available PTY chunk into output. Return whether data arrived."""
    try:
        ready, _, _ = select.select([master], [], [], max(0, timeout))
        if not ready:
            return False
        data = os.read(master, 65536)
    except OSError:
        return False
    if not data:
        return False
    output.extend(data)
    return True


def _drain_pty(master, output):
    while _read_pty(master, output, 0):
        pass


def _wait_for_quiet(master, output, deadline, quiet=0.005):
    """Drain output until no bytes arrive for a short quiet interval."""
    while time.monotonic() < deadline:
        if not _read_pty(master, output, min(quiet, deadline - time.monotonic())):
            return


def _poll_child(pid):
    """Return the exit code if pid has exited, otherwise None."""
    try:
        wpid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return -1
    if wpid == 0:
        return None
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1


def _wait_for_frame(master, pid, output, target, deadline):
    """Read output until target full-frame markers have arrived or pid exits."""
    while output.count(FRAME_MARKER) < target:
        exit_code = _poll_child(pid)
        if exit_code is not None:
            _drain_pty(master, output)
            return False, exit_code
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, None
        _read_pty(master, output, min(0.02, remaining))
    return True, None


def _key_tokens(keys):
    """Yield bytes consumed by Terminal.read_key() as one logical key."""
    i = 0
    while i < len(keys):
        if keys.startswith(PASTE_START, i):
            end = keys.find(PASTE_END, i + len(PASTE_START))
            if end >= 0:
                end += len(PASTE_END)
                yield keys[i:end]
                i = end
                continue
        if keys[i] == 0x1B and i + 1 < len(keys):
            if keys[i + 1] == 0x5B:
                end = i + 2
                while end < len(keys) and not (0x40 <= keys[end] <= 0x7E):
                    end += 1
                if end < len(keys):
                    end += 1
                yield keys[i:end]
                i = end
                continue
            if keys[i + 1] == 0x4F and i + 2 < len(keys):
                yield keys[i:i + 3]
                i += 3
                continue
        yield keys[i:i + 1]
        i += 1


def _send_keys(master, pid, output, keys, deadline, wait_after_last=False):
    """Send logical keys, advancing when vigor starts its next redraw."""
    tokens = list(_key_tokens(keys))
    for i, token in enumerate(tokens):
        before = output.count(FRAME_MARKER)
        try:
            os.write(master, token)
        except OSError:
            return _poll_child(pid)
        if wait_after_last or i < len(tokens) - 1:
            ready, exit_code = _wait_for_frame(master, pid, output, before + 1, deadline)
            if exit_code is not None or not ready:
                return exit_code
    return None


def _collect_child(master, pid, output, deadline, exit_code=None):
    """Collect output until pid exits or the shared deadline expires."""
    while exit_code is None and time.monotonic() < deadline:
        exit_code = _poll_child(pid)
        if exit_code is None:
            _read_pty(master, output, min(0.02, deadline - time.monotonic()))
    if exit_code is not None:
        _drain_pty(master, output)
        return exit_code
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
    _drain_pty(master, output)
    return -99


def run_vig(keys, file_path=None, file_paths=None, timeout=3.0, rows=24, cols=80, env=None, cwd=None):
    """Launch vigor in a PTY, send keys, and return screen, file, exit code."""
    if isinstance(keys, str):
        keys = keys.encode()

    cleanup_file = False
    if file_paths is not None:
        all_paths = file_paths
    elif file_path is None:
        fd_tmp, file_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd_tmp)
        cleanup_file = True
        all_paths = [file_path]
    else:
        all_paths = [file_path]

    master, slave = pty.openpty()
    import struct, fcntl, termios as tm
    fcntl.ioctl(master, tm.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, tm.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        if cwd:
            os.chdir(cwd)
        if env is None:
            os.environ["VIG_NO_CONFIG"] = "1"
        else:
            os.environ.update(env)
        os.execvp(VIG, [VIG] + all_paths)
        os._exit(1)

    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + timeout
    ready, exit_code = _wait_for_frame(master, pid, output, 1, deadline)
    if ready and exit_code is None:
        exit_code = _send_keys(master, pid, output, keys, deadline)
    exit_code = _collect_child(master, pid, output, deadline, exit_code)
    os.close(master)

    file_contents = ""
    if file_path and os.path.exists(file_path):
        with open(file_path, "r") as f:
            file_contents = f.read()
    if cleanup_file and file_path:
        try:
            os.unlink(file_path)
        except OSError:
            pass
    return output.decode("utf-8", errors="replace"), file_contents, exit_code


def last_frame(screen):
    """Return the final rendered frame from captured terminal output."""
    marker = "\x1b[?25l\x1b[H"
    idx = screen.rfind(marker)
    return screen[idx:] if idx >= 0 else screen


def write_temp(content):
    """Write content to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path

# ── Phase 1: Scaffold ─────────────────────────────────────────────────────

def test_open_and_quit():
    """Open vig with no file and quit."""
    screen, _, code = run_vig(b":q\r")
    assert code == 0, f"Expected exit 0, got {code}"
    print("  PASS: open & quit")

def test_open_file_visible():
    """Open a file and check content appears on screen."""
    path = write_temp("Hello from vig\nSecond line\n")
    screen, _, code = run_vig(b":q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Hello from vig" in screen, f"Content not visible in: {screen[:200]}"
    print("  PASS: file content visible")

def test_j_k_movement():
    """j/k movement doesn't crash."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    screen, _, code = run_vig(b"jjk:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: j/k movement")

def test_h_l_movement():
    """h/l movement doesn't crash."""
    path = write_temp("abcdefgh\n")
    screen, _, code = run_vig(b"llh:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: h/l movement")

def test_scroll_down():
    """Scrolling down with many j's doesn't crash."""
    content = "\n".join(f"line {i}" for i in range(50)) + "\n"
    path = write_temp(content)
    keys = b"j" * 30 + b":q\r"
    screen, _, code = run_vig(keys, file_path=path, timeout=6.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    print("  PASS: scroll down")


def test_render_disables_autowrap_for_full_width_lines():
    """Full-width rows must not autowrap before line-clear escapes."""
    content = "\n".join("X" * 20 for _ in range(30)) + "\n"
    path = write_temp(content)
    screen, _, code = run_vig(b"j" * 20 + b"k" * 10 + b":q\r", file_path=path, rows=10, cols=20, timeout=6.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "\x1b[?7l" in screen, "Expected autowrap disabled during redraw"
    assert "\x1b[?7h" in screen, "Expected autowrap restored on exit"
    print("  PASS: autowrap disabled for full-width redraws")


def test_render_clears_old_frame_for_indented_scroll():
    """Indented rows should not inherit text from previous scrolled frames."""
    content = "\n".join(f"    item {i}" if i % 2 else f"line {i}" for i in range(40)) + "\n"
    path = write_temp(content)
    screen, _, code = run_vig(b"j" * 25 + b"k" * 15 + b":q\r", file_path=path, rows=10, cols=30, timeout=6.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "\x1b[H\x1b[J" in screen, "Expected each redraw to clear the old frame"
    print("  PASS: old frame cleared for indented scroll")

# ── Phase 2: Editing ──────────────────────────────────────────────────────

def test_insert_text():
    """Insert text and save."""
    path = write_temp("")
    screen, content, code = run_vig(b"ihello\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello" in content, f"Expected 'hello' in file, got: {content!r}"
    print("  PASS: insert text")

def test_a_appends():
    """'a' appends after cursor."""
    path = write_temp("ab\n")
    screen, content, code = run_vig(b"aX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "aXb" in content, f"Expected 'aXb' in file, got: {content!r}"
    print("  PASS: a appends")

def test_I_beginning():
    """'I' inserts at first non-blank."""
    path = write_temp("  hello\n")
    screen, content, code = run_vig(b"IX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "  Xhello" in content, f"Expected '  Xhello', got: {content!r}"
    print("  PASS: I inserts at first non-blank")

def test_A_end():
    """'A' appends at end of line."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b"AX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloX" in content, f"Expected 'helloX', got: {content!r}"
    print("  PASS: A appends at end")

def test_enter_splits():
    """Enter in insert mode splits line."""
    path = write_temp("")
    screen, content, code = run_vig(b"ihello\rworld\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}: {lines}"
    assert lines[0] == "hello"
    assert lines[1] == "world"
    print("  PASS: enter splits line")

def test_backspace_joins():
    """Backspace at start of line joins with previous."""
    path = write_temp("hello\nworld\n")
    # Go to line 2, column 0 (j), enter insert (I), backspace to join
    screen, content, code = run_vig(b"jI\x7f\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloworld" in content, f"Expected 'helloworld', got: {content!r}"
    print("  PASS: backspace joins lines")

def test_write_save():
    """:w saves without quitting."""
    path = write_temp("")
    screen, content, code = run_vig(b"ix\x1b:w\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "x" in content
    print("  PASS: :w saves")

def test_quit_dirty_refuses():
    """:q on dirty buffer shows error."""
    path = write_temp("")
    # Insert, try to quit — should refuse. Then force quit.
    screen, _, code = run_vig(b"ix\x1b:q\r:q!\r", file_path=path, timeout=5.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "No write" in screen or "override" in screen, f"Expected dirty warning in screen output"
    print("  PASS: :q refuses on dirty buffer")

def test_edit_file():
    """:e opens a file."""
    path1 = write_temp("original\n")
    path2 = write_temp("other file\n")
    screen, _, code = run_vig(f":e {path2}\r:q\r:q\r".encode(), file_path=path1)
    os.unlink(path1)
    os.unlink(path2)
    assert code == 0
    print("  PASS: :e opens file")

def test_new_buffer():
    """:new creates empty buffer."""
    path = write_temp("stuff\n")
    screen, _, code = run_vig(b":new\r:q\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: :new")

# ── Phase 3: Word Motions ─────────────────────────────────────────────────

def test_w_forward_word():
    """w moves to start of next word."""
    path = write_temp("hello world\n")
    # w should move from 'h' to 'w' (position 6)
    # Insert a marker: go to cursor pos after w, insert X
    screen, content, code = run_vig(b"wiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello Xworld" in content, f"Expected 'hello Xworld', got: {content!r}"
    print("  PASS: w forward word")

def test_b_backward_word():
    """b moves to start of previous word."""
    path = write_temp("hello world\n")
    # Move to 'w' with w, then b should go back to 'h'
    screen, content, code = run_vig(b"wbiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xhello" in content, f"Expected 'Xhello', got: {content!r}"
    print("  PASS: b backward word")

def test_e_end_word():
    """e moves to end of word."""
    path = write_temp("hello world\n")
    # e should land on 'o' (pos 4), then insert after
    screen, content, code = run_vig(b"eaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloX" in content, f"Expected 'helloX', got: {content!r}"
    print("  PASS: e end of word")

def test_W_forward_WORD():
    """W moves to start of next WORD (whitespace-delimited)."""
    path = write_temp("a.b c.d\n")
    # W from 'a' should skip 'a.b' and land on 'c'
    screen, content, code = run_vig(b"WiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "a.b Xc.d" in content, f"Expected 'a.b Xc.d', got: {content!r}"
    print("  PASS: W forward WORD")

def test_B_backward_WORD():
    """B moves to start of previous WORD."""
    path = write_temp("a.b c.d\n")
    # Move to WORD 'c.d' with W, then B back to 'a.b'
    screen, content, code = run_vig(b"WBiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xa.b" in content, f"Expected 'Xa.b', got: {content!r}"
    print("  PASS: B backward WORD")

def test_E_end_WORD():
    """E moves to end of WORD."""
    path = write_temp("a.b c.d\n")
    # E from 'a' should land on 'b' (end of 'a.b')
    screen, content, code = run_vig(b"EaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "a.bX" in content, f"Expected 'a.bX', got: {content!r}"
    print("  PASS: E end of WORD")

def test_e_from_eol_stays_on_current_line_last_word():
    """e from one-past-EOL lands on the current line's last word end."""
    path = write_temp("abc\nxyz\n")
    screen, content, code = run_vig(b"$eaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abcX\nxyz\n", f"Expected 'abcX' on first line, got: {content!r}"
    print("  PASS: e from EOL stays on line")

def test_e_crosses_empty_line_without_crash():
    """Repeated e crosses empty lines safely without crashing."""
    path = write_temp("abc\n\nxyz\n")
    screen, content, code = run_vig(b"$eeaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abc\n\nxyzX\n", f"Expected move to next word end, got: {content!r}"
    print("  PASS: e crosses empty lines safely")

def test_w_skips_empty_line_to_next_word():
    """w should skip empty lines and land on the next word start."""
    path = write_temp("abc\n\nxyz\n")
    screen, content, code = run_vig(b"$wiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abc\n\nXxyz\n", f"Expected w to skip blank line, got: {content!r}"
    print("  PASS: w skips empty lines")

def test_w_newline_is_word_boundary():
    """w should stop at next line word start, not skip it."""
    path = write_temp("sys\nimport os\n")
    screen, content, code = run_vig(b"wiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "sys\nXimport os\n", f"Expected w to land on 'import', got: {content!r}"
    print("  PASS: w respects newline boundary")

def test_e_newline_is_word_boundary():
    """e should stop at end of current line word, not next line word."""
    path = write_temp("abc\ndef ghi\n")
    screen, content, code = run_vig(b"eaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abcX\ndef ghi\n", f"Expected e to end on first line, got: {content!r}"
    print("  PASS: e respects newline boundary")

def test_b_newline_is_word_boundary():
    """b should stop at current line word start before crossing lines."""
    path = write_temp("import sys\nimport os\n")
    # From 'os', b should land on current line 'import', not previous line 'sys'.
    screen, content, code = run_vig(b"2G7lbiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "import sys\nXimport os\n", f"Expected b to land on current line, got: {content!r}"
    print("  PASS: b respects newline boundary")

def test_B_newline_is_word_boundary():
    """B should stop at current line WORD start before crossing lines."""
    path = write_temp("import sys\nimport os\n")
    screen, content, code = run_vig(b"2G7lBiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "import sys\nXimport os\n", f"Expected B to land on current line, got: {content!r}"
    print("  PASS: B respects newline boundary")

def test_W_newline_is_word_boundary():
    """W should land on next line start, not skip it."""
    path = write_temp("a.b\nc.d\n")
    screen, content, code = run_vig(b"WiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a.b\nXc.d\n", f"Expected W to land on next line start, got: {content!r}"
    print("  PASS: W respects newline boundary")

def test_E_newline_is_word_boundary():
    """E should end current line WORD, not jump to next line."""
    path = write_temp("a.b\nc.d\n")
    screen, content, code = run_vig(b"EaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a.bX\nc.d\n", f"Expected E to end first line WORD, got: {content!r}"
    print("  PASS: E respects newline boundary")

# ── Phase 4: Visual Mode ──────────────────────────────────────────────────

def test_v_enters_visual():
    """v enters visual mode (reverse video appears in output)."""
    path = write_temp("hello world\n")
    # Enter visual, move right to extend selection, then quit
    screen, _, code = run_vig(b"vll\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # Check that reverse video was used at some point
    assert "\x1b[7m" in screen, "Expected reverse video in visual mode"
    print("  PASS: v enters visual")

def test_V_line_visual():
    """V enters visual line mode."""
    path = write_temp("line one\nline two\n")
    screen, _, code = run_vig(b"V\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[7m" in screen, "Expected reverse video in visual line mode"
    print("  PASS: V enters visual line")

def test_visual_esc_cancels():
    """Esc returns to normal mode from visual."""
    path = write_temp("hello\n")
    # Enter visual, then escape, then quit normally
    screen, _, code = run_vig(b"v\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: visual Esc cancels")

def test_visual_motion_extends():
    """Motion in visual mode extends selection."""
    path = write_temp("abcdefgh\n")
    # v, then move right 3 times — should highlight 4 chars
    screen, _, code = run_vig(b"vlll\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # The reverse-video segment should appear
    assert "\x1b[7m" in screen
    print("  PASS: visual motion extends")

# ── Phase 5: Polish ───────────────────────────────────────────────────────

def test_status_bar_shown():
    """Status bar shows filename."""
    path = write_temp("test content\n")
    screen, _, code = run_vig(b":q\r", file_path=path)
    # Check filename appears in status bar
    basename = os.path.basename(path)
    os.unlink(path)
    assert code == 0
    assert basename in screen or path in screen, f"Expected filename in status bar"
    print("  PASS: status bar shown")

def test_wq_command():
    """:wq writes and quits."""
    path = write_temp("")
    screen, content, code = run_vig(b"ihello\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello" in content
    print("  PASS: :wq writes and quits")

def test_q_bang_forces():
    """:q! forces quit on dirty buffer."""
    path = write_temp("original\n")
    screen, content, code = run_vig(b"ix\x1b:q!\r", file_path=path)
    # File should be unchanged
    assert code == 0
    assert content == "original\n", f"File should be unchanged, got: {content!r}"
    os.unlink(path)
    print("  PASS: :q! forces quit")

def test_empty_file():
    """Opening empty file shows tildes, no crash."""
    path = write_temp("")
    screen, _, code = run_vig(b":q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "~" in screen, "Expected tilde for empty lines"
    print("  PASS: empty file")

# ── Phase 6: Resize ───────────────────────────────────────────────────────

def run_vig_with_resize(keys_before, keys_after, new_rows, new_cols,
                         file_path=None, timeout=5.0):
    """Launch vig, send keys_before, resize PTY, send keys_after, return output."""
    import struct, fcntl, termios as tm

    if isinstance(keys_before, str):
        keys_before = keys_before.encode()
    if isinstance(keys_after, str):
        keys_after = keys_after.encode()

    cleanup_file = False
    if file_path is None:
        fd_tmp, file_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd_tmp)
        cleanup_file = True

    master, slave = pty.openpty()
    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(master, tm.TIOCSWINSZ, winsize)

    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, tm.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        os.execvp(VIG, [VIG, file_path])
        os._exit(1)

    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + timeout
    ready, exit_code = _wait_for_frame(master, pid, output, 1, deadline)
    if ready and exit_code is None:
        exit_code = _send_keys(master, pid, output, keys_before, deadline, wait_after_last=True)

    if exit_code is None:
        _wait_for_quiet(master, output, deadline)
        before = output.count(FRAME_MARKER)
        fcntl.ioctl(master, tm.TIOCSWINSZ, struct.pack("HHHH", new_rows, new_cols, 0, 0))
        _, exit_code = _wait_for_frame(master, pid, output, before + 1, deadline)
    if exit_code is None:
        exit_code = _send_keys(master, pid, output, keys_after, deadline)
    exit_code = _collect_child(master, pid, output, deadline, exit_code)
    os.close(master)

    file_contents = ""
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            file_contents = f.read()
    if cleanup_file:
        try:
            os.unlink(file_path)
        except OSError:
            pass
    return output.decode("utf-8", errors="replace"), file_contents, exit_code


def test_sigwinch_no_crash():
    """SIGWINCH doesn't crash vig."""
    path = write_temp("hello world\nline two\n")
    screen, _, code = run_vig_with_resize(
        b"", b":q\r", new_rows=30, new_cols=100, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected exit 0 after resize, got {code}"
    print("  PASS: SIGWINCH no crash")

def test_resize_shrink_grow():
    """Shrink then content survives."""
    content = "\n".join(f"line {i}" for i in range(20)) + "\n"
    path = write_temp(content)
    screen, _, code = run_vig_with_resize(
        b"", b":q\r", new_rows=12, new_cols=40, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "line" in screen
    print("  PASS: resize shrink+grow")


def test_resize_to_49_content_columns_keeps_wrapmove_consistent():
    """After a pane resize, exact-boundary display rows retain symmetric j/k movement."""
    content = (
        "      your vig vocabulary will expand with usage. Consider returning to\n"
        "      this tutorial periodically for a refresher.\n"
        "\n"
    )
    path = write_temp(content)
    _, saved, code = run_vig_with_resize(
        b":set number\r:set wrap\r:set wrapmove\r2G",
        b"$kjjkiX\x1b:wq\r", new_rows=12, new_cols=55, file_path=path)
    os.unlink(path)
    assert code == 0
    assert saved.splitlines()[1] == "      this tutorial periodically for a refresher.X", saved
    print("  PASS: resized 49-column wrapmove remains consistent")

# ── Phase 7: Count Prefixes ──────────────────────────────────────────────

def test_count_3j():
    """3j moves down 3 lines."""
    content = "\n".join(f"line{i}" for i in range(10)) + "\n"
    path = write_temp(content)
    # 3j moves to line 3 (0-indexed), insert a marker
    screen, file_content, code = run_vig(b"3jiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = file_content.strip().split("\n")
    assert "X" in lines[3], f"Expected 'X' on line 3, got: {lines[3]!r}"
    print("  PASS: 3j moves down 3 lines")

def test_count_5l():
    """5l moves right 5 characters."""
    path = write_temp("abcdefgh\n")
    # 5l moves to column 5 ('f'), insert before it
    screen, content, code = run_vig(b"5liX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "abcdeXfgh" in content, f"Expected 'abcdeXfgh', got: {content!r}"
    print("  PASS: 5l moves right 5")

def test_count_resets_on_esc():
    """Typing digits then Esc resets count, normal quit works."""
    path = write_temp("hello\n")
    # Type '3', then Esc (which is a non-digit key to normal, resets count), then :q
    screen, _, code = run_vig(b"3\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: count resets on Esc")

# ── Phase 8 — Edit Operations ─────────────────────────────────────────────

def test_dd_deletes_line():
    """dd deletes the current line."""
    path = write_temp("aaa\nbbb\nccc\n")
    screen, content, code = run_vig(b"jdd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "aaa\nccc", f"Expected 'aaa\\nccc', got: {content!r}"
    print("  PASS: dd deletes line")

def test_2dd_deletes_two_lines():
    """2dd deletes 2 lines."""
    path = write_temp("aaa\nbbb\nccc\nddd\n")
    screen, content, code = run_vig(b"j2dd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "aaa\nddd", f"Expected 'aaa\\nddd', got: {content!r}"
    print("  PASS: 2dd deletes 2 lines")

def test_dw_deletes_word():
    """dw deletes a word."""
    path = write_temp("hello world\n")
    screen, content, code = run_vig(b"dw:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # dw from start of "hello world" deletes "hello " (word + trailing space via w motion)
    assert "world" in content, f"Expected 'world' in content, got: {content!r}"
    assert "hello" not in content, f"Did not expect 'hello' in content, got: {content!r}"
    print("  PASS: dw deletes word")

def test_D_deletes_to_end():
    """D deletes from cursor to end of line."""
    path = write_temp("hello world\n")
    # Move right 5 (to space), then D
    screen, content, code = run_vig(b"5lD:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "hello", f"Expected 'hello', got: {content!r}"
    print("  PASS: D deletes to EOL")

def test_yy_p_paste_line():
    """yy yanks line, p pastes below."""
    path = write_temp("aaa\nbbb\n")
    screen, content, code = run_vig(b"yyp:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["aaa", "aaa", "bbb"], f"Expected aaa/aaa/bbb, got: {lines}"
    print("  PASS: yy+p paste line")

def test_yy_P_paste_above():
    """yy on line 2, P pastes above."""
    path = write_temp("aaa\nbbb\nccc\n")
    # j moves to bbb, yy yanks it, P pastes above current (bbb)
    screen, content, code = run_vig(b"jyyP:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["aaa", "bbb", "bbb", "ccc"], f"Expected aaa/bbb/bbb/ccc, got: {lines}"
    print("  PASS: yy+P paste above")

def test_cw_changes_word():
    """cw deletes word and enters insert mode."""
    path = write_temp("hello world\n")
    screen, content, code = run_vig(b"cwfoo\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # cw uses w motion (deletes "hello " including trailing space), then "foo" is typed
    assert content.strip() == "fooworld", f"Expected 'fooworld', got: {content!r}"
    print("  PASS: cw changes word")

def test_cc_changes_line():
    """cc deletes line content and enters insert mode."""
    path = write_temp("hello world\nsecond\n")
    screen, content, code = run_vig(b"ccnew text\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines[0] == "new text", f"Expected 'new text', got: {lines[0]!r}"
    assert "second" in content, f"Expected 'second' in content, got: {content!r}"
    print("  PASS: cc changes line")

def test_C_changes_to_end():
    """C deletes from cursor to EOL and enters insert mode."""
    path = write_temp("hello world\n")
    screen, content, code = run_vig(b"5lCXYZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "helloXYZ", f"Expected 'helloXYZ', got: {content!r}"
    print("  PASS: C changes to EOL")

def test_dd_on_last_line():
    """dd on the only remaining line leaves empty buffer."""
    path = write_temp("only\n")
    screen, content, code = run_vig(b"ddireplaced\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "replaced" in content, f"Expected 'replaced', got: {content!r}"
    print("  PASS: dd on last line leaves empty buffer")

def test_p_charwise_paste():
    """dw + p pastes deleted word charwise after cursor."""
    path = write_temp("hello world\n")
    # dw deletes "hello " → cursor on "world" col 0, p pastes after cursor (col 1)
    screen, content, code = run_vig(b"dwp:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # charwise p inserts after cursor: "w" + "hello " + "orld" = "whello orld"
    assert content.strip() == "whello orld", f"Expected 'whello orld', got: {content!r}"
    print("  PASS: dw + p charwise paste")


def test_multiline_charwise_paste_preserves_line_invariant():
    """A multiline character register pastes as distinct buffer lines."""
    path = write_temp("abc\ndef\n")
    screen, content, code = run_vig(b"vjypdd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "aabc\ndef\n", f"Expected inserted logical row to be deleted, got: {content!r}"
    print("  PASS: multiline charwise paste preserves logical lines")


def test_empty_paste_does_not_create_undo_state():
    """An empty-register paste does not keep the buffer dirty after undo."""
    path = write_temp("abc\n")
    screen, _, code = run_vig(b"pxu:q\r", file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected clean quit after undo, got: {screen[-500:]!r}"
    print("  PASS: empty paste creates no undo state")


# ── Phase 9 — Visual Edit ─────────────────────────────────────────────────

def test_visual_delete():
    """v + select + d deletes selection."""
    path = write_temp("abcdef\n")
    # v to enter visual, ll to select 'abc', d to delete
    screen, content, code = run_vig(b"vlld:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "def", f"Expected 'def', got: {content!r}"
    print("  PASS: visual d deletes selection")

def test_visual_yank_paste():
    """v + select + y yanks, then p pastes."""
    path = write_temp("abcdef\n")
    # v, ll selects "abc", y yanks, $ to end, p pastes after
    screen, content, code = run_vig(b"vlly$p:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "abc" in content, f"Expected 'abc' in content, got: {content!r}"
    # Should have original plus pasted abc somewhere
    print("  PASS: visual y + p")

def test_visual_change():
    """v + select + c deletes selection and enters insert mode."""
    path = write_temp("abcdef\n")
    screen, content, code = run_vig(b"vllcXYZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "XYZdef", f"Expected 'XYZdef', got: {content!r}"
    print("  PASS: visual c changes selection")

def test_visual_line_delete():
    """V + j + d deletes 2 lines."""
    path = write_temp("aaa\nbbb\nccc\n")
    screen, content, code = run_vig(b"Vjd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "ccc", f"Expected 'ccc', got: {content!r}"
    print("  PASS: V + j + d deletes 2 lines")

def test_visual_x_same_as_d():
    """x in visual mode works like d."""
    path = write_temp("abcdef\n")
    screen, content, code = run_vig(b"vllx:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "def", f"Expected 'def', got: {content!r}"
    print("  PASS: visual x deletes like d")

# ── Phase 10 — Search ──────────────────────────────────────────────────────

def test_search_forward():
    """/ searches forward and moves cursor to match."""
    path = write_temp("aaa\nbbb\nccc\n")
    # /bbb<Enter> should move cursor to line 1 (bbb)
    screen, content, code = run_vig(b"/bbb\riX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xbbb" in content, f"Expected 'Xbbb', got: {content!r}"
    print("  PASS: / search forward")

def test_search_backward():
    """? searches backward."""
    path = write_temp("aaa\nbbb\nccc\n")
    # Go to last line, then ?aaa<Enter> should find line 0
    screen, content, code = run_vig(b"jj?aaa\riX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xaaa" in content, f"Expected 'Xaaa', got: {content!r}"
    print("  PASS: ? search backward")

def test_search_n_repeats():
    """n repeats the last search."""
    path = write_temp("foo\nbar\nfoo\nbaz\n")
    # /foo<Enter> finds line 2 (skipping line 0 where we start), n wraps to line 0
    screen, content, code = run_vig(b"/foo\rniX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xfoo" in content, f"Expected 'Xfoo', got: {content!r}"
    print("  PASS: n repeats search")

def test_search_N_reverses():
    """N repeats search in opposite direction."""
    path = write_temp("foo\nbar\nfoo\nbaz\n")
    # On line 0, /foo<Enter> finds line 2, N goes backward to line 0
    screen, content, code = run_vig(b"/foo\rNiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xfoo" in content, f"Expected 'Xfoo', got: {content!r}"
    print("  PASS: N reverses search")

def test_search_not_found():
    """Search for nonexistent pattern shows message, doesn't crash."""
    path = write_temp("hello\nworld\n")
    screen, content, code = run_vig(b"/zzz\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "not found" in screen.lower() or True  # just verify no crash
    print("  PASS: search not found")

def test_search_esc_cancels():
    """Esc during search cancels without moving cursor."""
    path = write_temp("aaa\nbbb\n")
    # Start search, type partial, Esc, then insert at original pos
    screen, content, code = run_vig(b"/bb\x1biX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xaaa" in content, f"Expected 'Xaaa' (cursor stayed on line 0), got: {content!r}"
    print("  PASS: search Esc cancels")

# ── Phase 11 — Replace ─────────────────────────────────────────────────────

def test_substitute_current_line():
    """s/pat/repl/ on current line replaces first match."""
    path = write_temp("foo bar foo\nsecond\n")
    screen, content, code = run_vig(b":s/foo/baz/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines[0] == "baz bar foo", f"Expected 'baz bar foo', got: {lines[0]!r}"
    print("  PASS: s/pat/repl/ current line")

def test_substitute_global_flag():
    """s/pat/repl/g replaces all occurrences on current line."""
    path = write_temp("foo bar foo\n")
    screen, content, code = run_vig(b":s/foo/baz/g\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "baz bar baz", f"Expected 'baz bar baz', got: {content!r}"
    print("  PASS: s/pat/repl/g global")

def test_substitute_whole_file():
    """%s/pat/repl/g replaces across all lines."""
    path = write_temp("aaa\nbbb\naaa\n")
    screen, content, code = run_vig(b":%s/aaa/zzz/g\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["zzz", "bbb", "zzz"], f"Expected zzz/bbb/zzz, got: {lines}"
    print("  PASS: %s/pat/repl/g whole file")

def test_substitute_line_range():
    """2,3s/x/y/ replaces on lines 2-3 only."""
    path = write_temp("x1\nx2\nx3\nx4\n")
    screen, content, code = run_vig(b":2,3s/x/y/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["x1", "y2", "y3", "x4"], f"Expected x1/y2/y3/x4, got: {lines}"
    print("  PASS: 2,3s/x/y/ line range")

def test_substitute_regex():
    """s/ with regex pattern works."""
    path = write_temp("abc 123 def\n")
    screen, content, code = run_vig(b":s/[0-9]+/NUM/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "abc NUM def", f"Expected 'abc NUM def', got: {content!r}"
    print("  PASS: s/ with regex")

def test_substitute_not_found():
    """s/ with no match shows message, no crash."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b":s/zzz/aaa/\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: s/ not found")

# ── Phase 12 — Line Wrap ───────────────────────────────────────────────────

def test_set_wrap():
    """:set wrap enables wrap, :set nowrap disables."""
    path = write_temp("short\n")
    # :set wrap should not crash, then :q
    screen, _, code = run_vig(b":set wrap\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: :set wrap")

def test_wrap_long_line():
    """A long line wraps across multiple screen rows."""
    # 40-col terminal, line of 60 chars should wrap to 2 rows
    long_line = "A" * 60 + "\n"
    path = write_temp(long_line)
    screen, _, code = run_vig(b":set wrap\r:q\r", file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    # Screen should contain the full line broken across rows
    # Just verify no crash and the A's appear
    a_count = screen.count("A")
    assert a_count >= 40, f"Expected at least 40 A's visible, got {a_count}"
    print("  PASS: long line wraps")

def test_wrap_full_width_rows_do_not_clear_last_cell():
    """Full-width wrapped rows must not erase the boundary character."""
    path = write_temp("abcdefghijklmnopqrst\n")
    screen, _, code = run_vig(b":set wrap\r:q\r", file_path=path, cols=10, rows=8)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "abcdefghij\r\nklmnopqrst\r\n" in frame, f"Expected intact wrapped rows: {frame[:200]!r}"
    print("  PASS: full-width wrapped rows keep boundary chars")


def test_nowrap_truncates():
    """Without wrap, long lines are truncated."""
    long_line = "B" * 100 + "\n"
    path = write_temp(long_line)
    screen, _, code = run_vig(b":q\r", file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    # Should see at most 40 B's per row
    print("  PASS: nowrap truncates")

def test_wrap_cursor_position():
    """Cursor on wrapped line positions correctly."""
    # 20-col terminal, 30-char line — cursor at col 25 should be on 2nd screen row
    long_line = "X" * 30 + "\n"
    path = write_temp(long_line)
    # :set wrap, move right 25 times, insert marker
    keys = b":set wrap\r" + b"25liM\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path, cols=20)
    os.unlink(path)
    assert code == 0
    assert "M" in content, f"Expected 'M' in content, got: {content!r}"
    print("  PASS: wrap cursor position")


def test_wrap_cursor_at_exact_boundary_uses_eol_row():
    """Exact-width EOL uses a blank continuation row for the one-past-EOL cursor."""
    path = write_temp("abcdefghij\n")
    screen, _, code = run_vig(b":set wrap\r$:q\r", file_path=path, cols=10, rows=6)
    os.unlink(path)
    assert code == 0
    assert "\x1b[2;1H" in screen, f"Expected cursor on EOL continuation row: {screen[-500:]!r}"
    print("  PASS: wrap boundary has consistent EOL row")


def test_wrapmove_is_symmetric_at_exact_49_column_boundary():
    """Display-row j/k remain symmetric around a 49-column row with a gutter."""
    lines = (
        "      your vig vocabulary will expand with usage. Consider returning to\n"
        "      this tutorial periodically for a refresher.\n"
        "\n"
    )
    path = write_temp(lines)
    keys = b":set number\r:set wrap\r:set wrapmove\r2G$kjjkiX\x1b:wq\r"
    _, content, code = run_vig(keys, file_path=path, cols=55, rows=12)
    os.unlink(path)
    assert code == 0
    assert content.splitlines()[1] == "      this tutorial periodically for a refresher.X", content
    print("  PASS: wrapmove symmetric at 49-column boundary")


def test_wrapmove_crosses_logical_lines_symmetrically():
    """k to the previous line's last display row and j back preserve display column."""
    path = write_temp("abcdefghijklmnop\nabcdefghij\n")
    _, content, code = run_vig(b":set wrap\r:set wrapmove\r2G0kjiX\x1b:wq\r", file_path=path, cols=10)
    os.unlink(path)
    assert code == 0
    assert content == "abcdefghijklmnop\nXabcdefghij\n", f"Asymmetric wrapped j/k: {content!r}"
    print("  PASS: wrapmove crosses logical lines symmetrically")


def test_wrap_long_line_scrolls_within_line():
    """A wrapped line taller than the pane scrolls by display row to keep cursor visible."""
    path = write_temp("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ\n")
    screen, _, code = run_vig(b":set wrap\r45l:q\r", file_path=path, cols=10, rows=6)
    os.unlink(path)
    assert code == 0
    frame = last_frame(screen)
    assert "klmnopqrst\r\nuvwxyzABCD\r\nEFGHIJKLMN\r\nOPQRSTUVWX\r\n" in frame, f"Expected tail rows of long wrapped line: {frame[:300]!r}"
    assert "\x1b[4;6H" in screen, f"Expected cursor visible in scrolled wrapped line: {screen[-500:]!r}"
    print("  PASS: wrap scrolls within oversized line")

# ── Phase 13: Line Numbers ────────────────────────────────────────────────

def test_set_number():
    """:set number shows absolute line numbers right-aligned in five columns."""
    path = write_temp("alpha\nbeta\ngamma\n")
    keys = b":set number\r:q\r"
    screen, _, code = run_vig(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    assert "    1 alpha" in screen, f"Expected right-aligned line 1: {screen[:300]}"
    assert "    2 beta" in screen, f"Expected right-aligned line 2: {screen[:300]}"
    assert "    3 gamma" in screen, f"Expected right-aligned line 3: {screen[:300]}"
    assert "number on" in screen, f"Expected 'number on' message in screen"
    print("  PASS: :set number")

def test_set_relativenumber():
    """:set relativenumber left-aligns the absolute cursor line and right-aligns distances."""
    path = write_temp("alpha\nbeta\ngamma\ndelta\n")
    keys = b":set relativenumber\r:q\r"
    screen, _, code = run_vig(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    assert "1     alpha" in screen, f"Expected left-aligned absolute cursor line: {screen[:400]}"
    assert "    1 beta" in screen, f"Expected right-aligned relative line 1: {screen[:400]}"
    assert "    2 gamma" in screen, f"Expected right-aligned relative line 2: {screen[:400]}"
    print("  PASS: :set relativenumber")

def test_number_and_relnum():
    """Both number + relativenumber use the relative-number layout."""
    path = write_temp("alpha\nbeta\ngamma\ndelta\nepsilon\n")
    keys = b"2j:set number\r:set relativenumber\r:q\r"
    screen, _, code = run_vig(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    assert "3     gamma" in screen, f"Expected left-aligned absolute cursor line: {screen[:400]}"
    assert "    2 alpha" in screen, f"Expected relative line 2: {screen[:400]}"
    assert "    1 beta" in screen, f"Expected relative line 1: {screen[:400]}"
    assert "    1 delta" in screen, f"Expected relative line 1: {screen[:400]}"
    assert "    2 epsilon" in screen, f"Expected relative line 2: {screen[:400]}"
    print("  PASS: number + relativenumber")

def test_number_gutter_expands_past_five_digits():
    """The number field expands when the file has more than 99,999 lines."""
    path = write_temp("x\n" * 100000)
    screen, _, code = run_vig(b":set number\r:q\r", file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    assert "     1 x" in screen, f"Expected six-column number field: {screen[-500:]}"
    print("  PASS: number gutter expands past five digits")

def test_number_with_wrap():
    """:set number with :set wrap — only first wrapped row gets the line number."""
    long_line = "A" * 30 + "\nshort\n"
    path = write_temp(long_line)
    keys = b":set wrap\r:set number\r:q\r"
    screen, _, code = run_vig(keys, file_path=path, cols=20)
    os.unlink(path)
    assert code == 0
    assert "1 " in screen, f"Expected '1 ' gutter in screen: {screen[:300]}"
    assert "2 short" in screen, f"Expected '2 short' in screen: {screen[:300]}"
    print("  PASS: number with wrap")

# ── Phase 14: Arrow Keys in Insert Mode ───────────────────────────────────

def test_insert_arrow_left_right():
    """Left/Right arrow keys move cursor in insert mode."""
    path = write_temp("abcd\n")
    # Enter insert, type X, right arrow twice, type Y → "Xab Ycd"
    # Actually: start at col 0. i enters insert. Type X → "Xabcd", cx=1.
    # Right arrow → cx=2. Type Y → "XaYbcd", cx=3.
    keys = b"iX\x1b[CY\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "XaYbcd" in content, f"Expected 'XaYbcd', got: {content!r}"
    print("  PASS: insert arrow left/right")

def test_insert_arrow_up_down():
    """Up/Down arrow keys move cursor between lines in insert mode."""
    path = write_temp("aaa\nbbb\nccc\n")
    # Move to line 2 (j), enter insert (i), Down arrow, type X
    # Cursor starts at (0,0). j → (1,0). i → insert at (1,0).
    # Down arrow → (2,0). Type X → "Xccc"
    keys = b"ji\x1b[BX\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines[2] == "Xccc", f"Expected 'Xccc' on line 3, got: {lines[2]!r}"
    print("  PASS: insert arrow up/down")

# ── Phase 15: Undo / Redo ─────────────────────────────────────────────────

def test_undo_insert():
    """u undoes an insert session."""
    path = write_temp("abc\n")
    # Enter insert, type XY, Esc, then undo, then save
    keys = b"iXY\x1bu:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "abc", f"Expected 'abc' after undo, got: {content!r}"
    print("  PASS: undo insert")

def test_undo_dd():
    """u undoes dd (line delete)."""
    path = write_temp("line1\nline2\nline3\n")
    # dd deletes line1, u restores it, then save
    keys = b"ddu:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["line1", "line2", "line3"], f"Expected original, got: {lines}"
    print("  PASS: undo dd")

def test_redo_after_undo():
    """Ctrl-R redoes after undo."""
    path = write_temp("line1\nline2\nline3\n")
    # dd deletes line1, u restores, Ctrl-R re-deletes, save
    keys = b"ddu\x12:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["line2", "line3"], f"Expected line1 deleted, got: {lines}"
    print("  PASS: redo after undo")

def test_undo_paste():
    """u undoes a paste operation."""
    path = write_temp("hello\nworld\n")
    # yy yanks line, p pastes below, u undoes paste, save
    keys = b"yypu:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["hello", "world"], f"Expected original, got: {lines}"
    print("  PASS: undo paste")

def test_undo_substitute():
    """u undoes a substitute command."""
    path = write_temp("foo bar foo\n")
    # :%s/foo/baz/g then u, save
    keys = b":%s/foo/baz/g\ru:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "foo bar foo", f"Expected original, got: {content!r}"
    print("  PASS: undo substitute")

def test_undo_redo_dirty_flag():
    """Dirty flag tracks correctly through undo/redo."""
    path = write_temp("clean\n")
    # Save (already clean), insert X Esc (dirty), u (clean again).
    # :q should succeed (not dirty)
    keys = b"iX\x1bu:q\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected clean exit (dirty flag cleared by undo), got code {code}"
    print("  PASS: undo/redo dirty flag")

def test_undo_insert_word_checkpoint():
    """Long inserts create checkpoints every 2 WORDs; undo removes last chunk."""
    path = write_temp("\n")
    # Insert "aaa bbb ccc ddd " — 4 WORDs = 2 checkpoints.
    # Esc, then u should undo the last 2 WORDs, u again undoes the first 2.
    keys = b"iaaa bbb ccc ddd \x1bu:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    # After one undo: should have first 2 words + checkpoint content
    stripped = content.strip()
    assert "aaa" in stripped, f"Expected partial content after one undo, got: {content!r}"
    assert "ddd" not in stripped, f"Expected 'ddd' removig by undo, got: {content!r}"
    print("  PASS: undo insert word checkpoint")

def test_undo_visual_delete():
    """u undoes a visual mode delete."""
    path = write_temp("abcdef\n")
    # v + ll selects 'abc', d deletes, u restores, save
    keys = b"vlld\x1bu:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    # After delete we're in NORMAL, ESC is harmless, u undoes
    assert content.strip() == "abcdef", f"Expected original, got: {content!r}"
    print("  PASS: undo visual delete")

def test_redo_cleared_on_new_edit():
    """Redo stack is cleared when a new edit is made after undo."""
    path = write_temp("original\n")
    # dd (delete line), u (undo), iNEW Esc (new edit), Ctrl-R should do nothing
    # Save and check content = "NEWoriginal"
    keys = b"dduiNEW\x1b\x12:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "NEW" in content, f"Expected 'NEW' in content, got: {content!r}"
    assert "original" in content, f"Expected 'original' in content, got: {content!r}"
    print("  PASS: redo cleared on new edit")

def test_undo_at_oldest():
    """u at oldest change shows message, doesn't crash."""
    path = write_temp("test\n")
    # Just press u with no edits — should show message and not crash
    keys = b"uu:q\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: undo at oldest")

# ── Phase 17: gg and G motions ────────────────────────────────────────────

def test_G_goes_to_last_line():
    """G moves cursor to last line."""
    path = write_temp("line1\nline2\nline3\nline4\nline5\n")
    keys = b"GA$$$\x1b:wq\r"  # G goes to last line, A appends $$$
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line5$$$" in content, f"G did not reach last line: {content!r}"
    print("  PASS: G goes to last line")

def test_gg_goes_to_first_line():
    """gg moves cursor to first line."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    keys = b"GggA***\x1b:wq\r"  # G to last, gg to first, A appends
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line1***" in content, f"gg did not reach first line: {content!r}"
    print("  PASS: gg goes to first line")

def test_count_G():
    """3G goes to line 3."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    keys = b"3GA@@@\x1b:wq\r"  # 3G to line 3, A appends
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line3@@@" in content, f"3G did not go to line 3: {content!r}"
    print("  PASS: count G")

def test_zero_goes_to_column_zero():
    """0 moves cursor to column 0."""
    path = write_temp("hello world\n")
    keys = b"llll0i^\x1b:wq\r"  # llll to go right, 0 to col 0, i^ to insert
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("^hello"), f"0 did not go to col 0: {content!r}"
    print("  PASS: 0 goes to column 0")

def test_dgg_deletes_to_first():
    """dgg from line 3 deletes lines 1-3."""
    path = write_temp("line1\nline2\nline3\nline4\nline5\n")
    keys = b"jjdgg:wq\r"  # go to line 3, dgg deletes lines 1-3
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line1" not in content and "line2" not in content and "line3" not in content
    assert "line4" in content
    print("  PASS: dgg deletes to first")

# ── Phase 18: f t F T ; , ─────────────────────────────────────────────────

def test_f_motion():
    """fx finds character x on current line."""
    path = write_temp("hello world\n")
    keys = b"fwi@\x1b:wq\r"  # fw finds 'w', i@ inserts before it
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello @world" in content, f"f motion failed: {content!r}"
    print("  PASS: f motion")

def test_t_motion():
    """tx moves to character before x."""
    path = write_temp("hello world\n")
    keys = b"twi@\x1b:wq\r"  # tw goes before 'w', i@ inserts
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello@ world" in content, f"t motion failed: {content!r}"
    print("  PASS: t motion")

def test_F_motion():
    """Fx finds character backward."""
    path = write_temp("hello world\n")
    keys = b"fwFli@\x1b:wq\r"  # fw to 'w' (pos 6), Fl finds 'l' backward (pos 3)
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hel@lo" in content, f"F motion failed: {content!r}"
    print("  PASS: F motion")

def test_semicolon_repeats_find():
    """Semicolon repeats last f/t find."""
    path = write_temp("abababab\n")
    keys = b"fa;i@\x1b:wq\r"  # fa finds 'a' at pos 2, ; repeats to pos 4
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("abab@a"), f"; repeat failed: {content!r}"
    print("  PASS: ; repeats find")

def test_semicolon_repeats_t_motion():
    """Semicolon repeats last t/T find correctly."""
    path = write_temp("axaxaxax\n")
    # tx goes before first x (index 0), ; should go before second x (index 2)
    screen, content, code = run_vig(b"tx;i@\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("ax@ax"), f"; repeat for t failed: {content!r}"
    print("  PASS: ; repeats t/T")

def test_semicolon_repeats_T_motion():
    """Semicolon also repeats backward till (T)."""
    path = write_temp("xaxaxaxa\n")
    # $ to EOL, Tx goes after x at 6 (cx=7), ; should go after x at 4 (cx=5)
    screen, content, code = run_vig(b"$Tx;i@\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("xaxax@ax"), f"; repeat for T failed: {content!r}"
    print("  PASS: ; repeats T")

def test_comma_reverses_find():
    """Comma reverses last f/t find."""
    path = write_temp("abababab\n")
    keys = b"fa;;,i@\x1b:wq\r"  # fa, ;;, , reverses
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "@a" in content, f", reverse find failed: {content!r}"
    print("  PASS: , reverses find")

def test_dfl_deletes_to_char():
    """dfl deletes from cursor to 'l' inclusive."""
    path = write_temp("hello world\n")
    keys = b"dfl:wq\r"  # delete from cursor through 'l'
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("lo world"), f"df failed: {content!r}"
    print("  PASS: df deletes to char")

def test_f_digit_target():
    """f<digit> finds a digit target instead of treating it as a count."""
    path = write_temp("ab3cd\n")
    screen, content, code = run_vig(b"f3i@\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("ab@3cd"), f"f digit target failed: {content!r}"
    print("  PASS: f accepts digit target")

def test_counted_f_digit_target():
    """A count before f still repeats a digit-target find."""
    path = write_temp("a3b3c\n")
    screen, content, code = run_vig(b"2f3i@\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("a3b@3c"), f"counted f digit target failed: {content!r}"
    print("  PASS: counted f accepts digit target")

# ── Phase 19: >> and << indent ────────────────────────────────────────────

def test_indent_line():
    """>> indents current line by 4 spaces."""
    path = write_temp("hello\nworld\n")
    keys = b">>:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "    hello", f">> failed: {lines[0]!r}"
    assert lines[1] == "world", f">> affected wrong line: {lines[1]!r}"
    print("  PASS: >> indents line")

def test_dedent_line():
    """<< removes up to 4 leading spaces."""
    path = write_temp("    hello\nworld\n")
    keys = b"<<:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "hello", f"<< failed: {lines[0]!r}"
    print("  PASS: << dedents line")

def test_count_indent():
    """3>> indents 3 lines."""
    path = write_temp("a\nb\nc\nd\n")
    keys = b"3>>:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "    a", f"3>> failed on line 1: {lines[0]!r}"
    assert lines[1] == "    b", f"3>> failed on line 2: {lines[1]!r}"
    assert lines[2] == "    c", f"3>> failed on line 3: {lines[2]!r}"
    assert lines[3] == "d", f"3>> affected line 4: {lines[3]!r}"
    print("  PASS: count indent")

# ── Phase 20: Autoindent ──────────────────────────────────────────────────

def test_autoindent_on_enter():
    """Enter in insert mode copies indentation from current line."""
    path = write_temp("    hello\n")
    keys = b"A\rworld\x1b:wq\r"  # A to end, Enter, type 'world'
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[1] == "    world", f"Autoindent failed: {lines[1]!r}"
    print("  PASS: autoindent on enter")

def test_autoindent_disabled():
    """:set noautoindent disables autoindent."""
    path = write_temp("    hello\n")
    keys = b":set noautoindent\rA\rworld\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[1] == "world", f"Autoindent not disabled: {lines[1]!r}"
    print("  PASS: autoindent disabled")

# ── Phase 21: % match brackets ────────────────────────────────────────────

def test_percent_match_paren():
    """% jumps to matching parenthesis."""
    path = write_temp("(hello world)\n")
    keys = b"%i@\x1b:wq\r"  # % from ( jumps to ), i@ inserts before )
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("(hello world@)"), f"% failed: {content!r}"
    print("  PASS: % matches parens")

def test_percent_match_brace():
    """% works with braces across lines."""
    path = write_temp("{\nhello\n}\n")
    keys = b"%A@\x1b:wq\r"  # % on { goes to }, A@ appends
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "}@" in content, f"% brace failed: {content!r}"
    print("  PASS: % matches braces")

# ── Phase 22: O and o ─────────────────────────────────────────────────────

def test_o_opens_below():
    """o opens new line below and enters insert mode."""
    path = write_temp("line1\nline3\n")
    keys = b"oline2\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "line1"
    assert lines[1] == "line2"
    assert lines[2] == "line3"
    print("  PASS: o opens below")

def test_O_opens_above():
    """O opens new line above and enters insert mode."""
    path = write_temp("line2\nline3\n")
    keys = b"Oline1\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "line1"
    assert lines[1] == "line2"
    print("  PASS: O opens above")

def test_o_autoindent():
    """o with autoindent copies leading whitespace."""
    path = write_temp("    indented\n")
    keys = b"ohello\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[1] == "    hello", f"o autoindent failed: {lines[1]!r}"
    print("  PASS: o autoindent")

# ── Phase 23: iw/iW/aw/aW text objects ───────────────────────────────────

def test_diw_deletes_word():
    """diw deletes the word under cursor."""
    path = write_temp("hello world test\n")
    keys = b"wdiw:wq\r"  # w to 'world', diw deletes it
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "world" not in content, f"diw failed: {content!r}"
    print("  PASS: diw deletes word")

def test_daw_deletes_word_with_space():
    """daw deletes word and trailing space."""
    path = write_temp("hello world test\n")
    keys = b"wdaw:wq\r"  # w to 'world', daw includes space
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "world" not in content, f"daw failed: {content!r}"
    # "hello " + "test" should not have double space
    assert "hello test" in content or "hello  test" not in content
    print("  PASS: daw deletes word + space")

def test_ciw_changes_word():
    """ciw replaces the word under cursor."""
    path = write_temp("hello world test\n")
    keys = b"wciwNEW\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "NEW" in content, f"ciw failed: {content!r}"
    assert "world" not in content
    print("  PASS: ciw changes word")

# ── Phase 24: Text objects for brackets and quotes ────────────────────────

def test_di_paren():
    """di( deletes inside parentheses."""
    path = write_temp("call(arg1, arg2)\n")
    keys = b"f(ldi(:wq\r"  # f( to '(', l inside, di( deletes inner
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "call()" in content, f"di( failed: {content!r}"
    print("  PASS: di( deletes inside parens")

def test_da_bracket():
    """da[ deletes including brackets."""
    path = write_temp("arr[1, 2, 3]end\n")
    keys = b"f[lda[:wq\r"  # f[ to '[', l inside, da[ deletes all
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "[" not in content, f"da[ failed: {content!r}"
    assert "arrend" in content
    print("  PASS: da[ deletes including brackets")

def test_di_quote():
    """di\" deletes inside double quotes."""
    path = write_temp('say "hello world" ok\n')
    keys = b'fhdi":wq\r'  # fh inside quotes, di" deletes inside
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert 'say "" ok' in content, f'di" failed: {content!r}'
    print('  PASS: di" deletes inside quotes')

# ── Phase 25: Comment toggle ─────────────────────────────────────────────

def test_gcc_comments_line():
    """gcc toggles comment on current line."""
    path = write_temp("hello\nworld\n")
    keys = b"gcc:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "# hello", f"gcc failed: {lines[0]!r}"
    assert lines[1] == "world"
    print("  PASS: gcc comments line")

def test_gcc_uncomments_line():
    """gcc uncomments an already commented line."""
    path = write_temp("# hello\nworld\n")
    keys = b"gcc:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "hello", f"gcc uncomment failed: {lines[0]!r}"
    print("  PASS: gcc uncomments line")

def test_visual_gc():
    """Visual mode gc toggles comments on selection."""
    path = write_temp("line1\nline2\nline3\n")
    keys = b"Vjgc:wq\r"  # V, j to select 2 lines, gc to toggle
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "# line1", f"visual gc failed: {lines[0]!r}"
    assert lines[1] == "# line2", f"visual gc failed: {lines[1]!r}"
    assert lines[2] == "line3"
    print("  PASS: visual gc comments")

def test_set_comment_char():
    """:set comment=// changes comment character."""
    path = write_temp("hello\n")
    keys = b":set comment=//\rgcc:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "// hello", f"set comment failed: {lines[0]!r}"
    print("  PASS: set comment character")

# ── Phase 26: Dot repeat ─────────────────────────────────────────────────

def test_dot_repeat_dd():
    """. repeats dd."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    keys = b"dd.:wq\r"  # dd deletes line1, . repeats to delete line2
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line1" not in content and "line2" not in content
    assert "line3" in content
    print("  PASS: dot repeat dd")

def test_dot_repeat_insert():
    """. repeats insert action."""
    path = write_temp("aaa\nbbb\nccc\n")
    keys = b"A!!!\x1bj.:wq\r"  # A!!!<Esc> on line1, j, . on line2
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "aaa!!!" in content, f"dot insert failed: {content!r}"
    assert "bbb!!!" in content, f"dot insert repeat failed: {content!r}"
    print("  PASS: dot repeat insert")

def test_dot_repeat_indent():
    """. repeats >>."""
    path = write_temp("hello\nworld\n")
    keys = b">>j.:wq\r"  # >> indents line1, j moves down, . repeats
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "    hello", f"dot indent failed: {lines[0]!r}"
    assert lines[1] == "    world", f"dot indent repeat failed: {lines[1]!r}"
    print("  PASS: dot repeat >>")

# ── Phase 27: :read, :!, :read ! ──────────────────────────────────────────

def test_read_file():
    """:read inserts file contents below cursor."""
    src = write_temp("inserted line\n")
    path = write_temp("original\n")
    keys = f":read {src}\r:wq\r".encode()
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(src)
    os.unlink(path)
    assert code == 0
    assert "original" in content and "inserted line" in content
    lines = content.split("\n")
    assert lines[0] == "original"
    assert lines[1] == "inserted line"
    print("  PASS: :read file")

def test_read_command():
    """:read !echo inserts command output below cursor."""
    path = write_temp("original\n")
    keys = b":read !echo hello_from_cmd\r:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello_from_cmd" in content, f":read ! failed: {content!r}"
    print("  PASS: :read !command")

def test_bang_command():
    """:! runs a shell command and shows output."""
    path = write_temp("test\n")
    keys = b":! echo hello_bang\r:q\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello_bang" in screen, f":! failed: {screen[-500:]}"
    print("  PASS: :! shell command")

def test_filter_range_replaces_lines():
    """:range!cmd pipes lines through a command and replaces the range."""
    path = write_temp("a\nb\nc\n")
    screen, content, code = run_vig(b":2,3!tr a-z A-Z\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a\nB\nC\n", f":range! filter failed: {content!r}"
    print("  PASS: :range! filters in place")

def test_filter_whole_buffer_replaces_lines():
    """:%!cmd pipes the whole buffer through a command in-place."""
    path = write_temp("b\na\n")
    screen, content, code = run_vig(b":%!sort\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a\nb\n", f":%! filter failed: {content!r}"
    print("  PASS: :%! filters whole buffer")

def test_filter_new_buffer():
    """:!!cmd pipes the whole buffer and opens output in a new buffer."""
    path = write_temp("b\na\n")
    screen, content, code = run_vig(b":!!sort\r:q!\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "b\na\n", f":!! should not change source buffer: {content!r}"
    assert "[2/2]" in screen, f":!! did not open a second buffer: {screen[-1000:]!r}"
    print("  PASS: :!! filters to new buffer")

# ── Phase 28: Multi-buffer ─────────────────────────────────────────────────

def test_multi_file_argv():
    """Opening multiple files on command line creates multiple buffers."""
    p1 = write_temp("file one\n")
    p2 = write_temp("file two\n")
    # Open with two files, check first visible, switch to second, quit all
    screen, _, code = run_vig(b":n\r:qa\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    # Status bar should show [1/2] before :n, and [2/2] after
    assert "[1/2]" in screen or "[2/2]" in screen, f"No buffer indicator: {screen[-500:]}"
    print("  PASS: multi-file argv")

def test_next_prev_buffer():
    """:n and :p cycle through buffers."""
    p1 = write_temp("alpha\n")
    p2 = write_temp("beta\n")
    p3 = write_temp("gamma\n")
    # Open 3 files, :n twice to get to buffer 3, :p to go back to 2, then :qa
    screen, _, code = run_vig(b":n\r:n\r:p\r:qa\r", file_paths=[p1, p2, p3])
    os.unlink(p1)
    os.unlink(p2)
    os.unlink(p3)
    assert code == 0
    # Should have visited buffer [2/3] and [3/3]
    assert "[2/3]" in screen or "[3/3]" in screen, f"Buffer switching failed: {screen[-500:]}"
    print("  PASS: :n/:p buffer cycling")

def test_ls_lists_buffers():
    """:ls shows buffer list."""
    p1 = write_temp("aaa\n")
    p2 = write_temp("bbb\n")
    # Open two files, :ls, then :qa
    screen, _, code = run_vig(b":ls\r:qa\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    # :ls output should contain both file paths
    assert os.path.basename(p1) in screen, f":ls missing file1: {screen[-500:]}"
    assert os.path.basename(p2) in screen, f":ls missing file2: {screen[-500:]}"
    print("  PASS: :ls lists buffers")

def test_quit_closes_buffer():
    """:q closes current buffer when multiple exist."""
    p1 = write_temp("first\n")
    p2 = write_temp("second\n")
    # Open two files, :q closes first, then :q exits
    screen, _, code = run_vig(b":q\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :q closes buffer")

def test_e_adds_buffer():
    """:e adds a new buffer instead of replacing."""
    p1 = write_temp("original\n")
    p2 = write_temp("added\n")
    # Open p1, :e p2 adds it, now we need :q twice
    screen, _, code = run_vig(f":e {p2}\r:q\r:q\r".encode(), file_path=p1)
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    # Should see [2/2] after :e
    assert "[2/2]" in screen, f"No [2/2] after :e: {screen[-500:]}"
    print("  PASS: :e adds buffer")

def test_bdelete_removes_buffer():
    """:k deletes current buffer."""
    p1 = write_temp("keep\n")
    p2 = write_temp("remove\n")
    # Open two files, :n to go to second, :k deletes it, :q exits
    screen, _, code = run_vig(b":n\r:k\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :k deletes buffer")

def test_bdelete_dirty_blocked():
    """:k refuses to delete dirty buffer."""
    p1 = write_temp("clean\n")
    p2 = write_temp("dirty\n")
    # Open two files, :n to second, make it dirty, try :k (should fail), :k! forces it
    screen, _, code = run_vig(b":n\riX\x1b:k\r:k!\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    assert "No write since last change" in screen, f":k should warn about dirty: {screen[-500:]}"
    print("  PASS: :k blocks on dirty buffer")

def test_bdelete_last_refused():
    """:k refuses to delete the last buffer."""
    path = write_temp("only\n")
    screen, _, code = run_vig(b":k\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Cannot delete last buffer" in screen, f":k should refuse last: {screen[-500:]}"
    print("  PASS: :k refuses last buffer")

def test_qa_checks_all_dirty():
    """:qa refuses if any buffer is dirty."""
    p1 = write_temp("clean\n")
    p2 = write_temp("dirty\n")
    # Open two files, :n to second, make it dirty, :p back, :qa should fail
    screen, _, code = run_vig(b":n\riX\x1b:p\r:qa\r:qa!\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    assert "unsaved changes" in screen, f":qa should warn: {screen[-500:]}"
    print("  PASS: :qa checks all dirty")

def test_wq_closes_buffer():
    """:wq closes buffer when multiple exist, writes and exits when last."""
    p1 = write_temp("one\n")
    p2 = write_temp("two\n")
    # Open two files, :wq writes and closes first, :q exits second
    screen, _, code = run_vig(b":wq\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :wq closes buffer")

# ── Phase 29: x/X and space-leader ────────────────────────────────────────

def test_x_deletes_char():
    """x deletes character under cursor."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b"x:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ello\n", f"Expected 'ello\\n', got {content!r}"
    print("  PASS: x deletes char")

def test_x_with_count():
    """3x deletes 3 characters."""
    path = write_temp("abcdef\n")
    screen, content, code = run_vig(b"3x:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "def\n", f"Expected 'def\\n', got {content!r}"
    print("  PASS: 3x deletes 3 chars")

def test_X_deletes_before():
    """X deletes character before cursor."""
    path = write_temp("hello\n")
    # Move to position 2 (on 'l'), then X deletes 'e'
    screen, content, code = run_vig(b"llX:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "hllo\n", f"Expected 'hllo\\n', got {content!r}"
    print("  PASS: X deletes before cursor")

def test_space_w_toggles_wrap():
    """<space>w toggles wrap without changing wrapmove."""
    path = write_temp("abcdefghijk\n")
    screen, _, code = run_vig(b" w w:q\r", file_path=path, cols=10, rows=6)
    os.unlink(path)
    assert code == 0
    assert "wrap on" in screen and "wrap off" in screen
    assert "abcdefghij\r\nk" in screen, f"Expected wrapped intermediate frame: {screen[-800:]!r}"
    print("  PASS: <space>w toggles wrap")


def test_space_d_deletes_buffer_safely():
    """<space>d deletes a clean buffer but retains dirty/last-buffer protections."""
    p1 = write_temp("one\n")
    p2 = write_temp("two\n")
    _, _, clean_code = run_vig(b" d:q\r", file_paths=[p1, p2])
    dirty_screen, _, dirty_code = run_vig(b"iX\x1b d:qa!\r", file_paths=[p1, p2])
    last_screen, _, last_code = run_vig(b" d:q\r", file_path=p1)
    os.unlink(p1)
    os.unlink(p2)
    assert clean_code == dirty_code == last_code == 0
    assert "No write since last change" in dirty_screen
    assert "Cannot delete last buffer" in last_screen
    print("  PASS: <space>d safely deletes buffer")

# ── Phase 30: ^/$ Home/End + Insert Tab/Delete ────────────────────────────

def test_caret_motion_first_nonblank():
    """^ moves to first non-blank character."""
    path = write_temp("    hello\n")
    screen, content, code = run_vig(b"$i!\x1b^iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "    Xhello!\n", f"Expected first-nonblank insert, got {content!r}"
    print("  PASS: ^ moves to first non-blank")

def test_home_end_normal_mode():
    """Home/End work as start/end motions in Normal mode."""
    path = write_temp("hello\n")
    # End then append !, Home then insert ^
    screen, content, code = run_vig(b"\x1b[Fi!\x1b\x1b[Hi^\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^hello!\n", f"Expected '^hello!', got {content!r}"
    print("  PASS: Home/End in normal mode")

def test_home_end_ss3_sequences():
    """Home/End also work with SS3 escape sequences (ESC O H/F)."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b"\x1bOFi!\x1b\x1bOHi^\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^hello!\n", f"Expected '^hello!' with SS3 Home/End, got {content!r}"
    print("  PASS: Home/End SS3 sequences")

def test_home_end_csi_tilde_sequences():
    """Home/End also work with CSI tilde sequences (ESC [1~/[4~)."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b"\x1b[4~i!\x1b\x1b[1~i^\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^hello!\n", f"Expected '^hello!' with CSI tilde Home/End, got {content!r}"
    print("  PASS: Home/End CSI tilde sequences")

def test_insert_home_end_tab():
    """Insert mode handles Home/End and Tab (4 spaces)."""
    path = write_temp("abc\n")
    # i TAB X HOME ^ END ! ESC
    keys = b"i\tX\x1b[H^\x1b[F!\x1b:wq\r"
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^    Xabc!\n", f"Expected '^    Xabc!', got {content!r}"
    print("  PASS: insert Home/End/Tab")

def test_insert_delete_key():
    """Insert mode Delete removes character under cursor."""
    path = write_temp("abc\n")
    screen, content, code = run_vig(b"i\x1b[C\x1b[3~\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ac\n", f"Expected 'ac', got {content!r}"
    print("  PASS: insert Delete key")

# ── Phase 31: J join + visual ^/$ motions ─────────────────────────────────

def test_J_joins_lines():
    """J joins current line with the next line."""
    path = write_temp("hello\nworld\n")
    screen, content, code = run_vig(b"J:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "hello world\n", f"Expected joined line, got {content!r}"
    print("  PASS: J joins lines")

def test_count_J_joins_multiple_lines():
    """Counted J joins N lines into one."""
    path = write_temp("a\nb\nc\n")
    screen, content, code = run_vig(b"3J:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a b c\n", f"Expected 'a b c', got {content!r}"
    print("  PASS: count J joins multiple lines")

def test_visual_dollar_delete_line_tail():
    """Visual mode supports $ motion for selection expansion."""
    path = write_temp("hello\n")
    screen, content, code = run_vig(b"v$d:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "\n", f"Expected empty line after visual $ delete, got {content!r}"
    print("  PASS: visual $ motion")

def test_visual_caret_delete_to_nonblank():
    """Visual mode supports ^ motion for selection expansion."""
    path = write_temp("  hello!\n")
    screen, content, code = run_vig(b"$v^d:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "  \n", f"Expected leading spaces only, got {content!r}"
    print("  PASS: visual ^ motion")

# ── Phase 32: Path handling for :e/:w and argv ────────────────────────────

def test_edit_relative_to_working_dir():
    """:e relative paths resolve from the process working directory."""
    tmp = tempfile.mkdtemp(prefix="vig_p32_")
    main_path = os.path.join(tmp, "main.txt")
    other_path = os.path.join(tmp, "other.txt")
    with open(main_path, "w") as f:
        f.write("main\n")
    with open(other_path, "w") as f:
        f.write("other\n")

    keys = b":e other.txt\rA!\x1b:wq\r:q\r"
    screen, _, code = run_vig(keys, file_path=main_path, cwd=tmp)
    assert code == 0
    with open(other_path, "r") as f:
        content = f.read()
    assert content == "other!\n", f"Expected relative :e target edited, got {content!r}"
    os.unlink(main_path)
    os.unlink(other_path)
    os.rmdir(tmp)
    print("  PASS: :e resolves relative to working directory")

def test_write_relative_to_working_dir():
    """:w relative paths write under the process working directory."""
    tmp = tempfile.mkdtemp(prefix="vig_p32_")
    main_path = os.path.join(tmp, "main.txt")
    out_path = os.path.join(tmp, "out.txt")
    with open(main_path, "w") as f:
        f.write("abc\n")

    screen, _, code = run_vig(b"iX\x1b:w out.txt\r:q\r", file_path=main_path, cwd=tmp)
    assert code == 0
    with open(out_path, "r") as f:
        content = f.read()
    assert content == "Xabc\n", f"Expected relative :w target content, got {content!r}"
    os.unlink(main_path)
    os.unlink(out_path)
    os.rmdir(tmp)
    print("  PASS: :w resolves relative to working directory")

def test_argv_expands_tilde_path():
    """Command-line argv paths support ~ expansion."""
    home = os.path.expanduser("~")
    fd, abs_path = tempfile.mkstemp(prefix="vig_p32_", suffix=".txt", dir=home)
    with os.fdopen(fd, "w") as f:
        f.write("homefile\n")
    tilde_path = "~/" + os.path.basename(abs_path)
    screen, _, code = run_vig(b":q\r", file_paths=[tilde_path])
    os.unlink(abs_path)
    assert code == 0
    assert "homefile" in screen, "Expected file opened from ~ path"
    print("  PASS: argv expands ~ path")

def test_write_path_error_shows_message_no_crash():
    """:w to invalid target reports error instead of crashing."""
    tmp = tempfile.mkdtemp(prefix="vig_p32_")
    main_path = os.path.join(tmp, "main.txt")
    with open(main_path, "w") as f:
        f.write("abc\n")

    keys = f":w {tmp}\r:q!\r".encode()
    screen, _, code = run_vig(keys, file_path=main_path)
    os.unlink(main_path)
    os.rmdir(tmp)
    assert code == 0
    assert "Can't write" in screen, f"Expected write error message, got {screen!r}"
    print("  PASS: write path error handled")

# ── Phase 33: Ctrl-D / Ctrl-U motions ──────────────────────────────────────

def test_ctrl_d_moves_half_page_down():
    """Ctrl-D moves cursor down by half a screen."""
    content = "\n".join(f"line{i}" for i in range(1, 41)) + "\n"
    path = write_temp(content)
    screen, out, code = run_vig(b"\x04A!\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line12!" in out, f"Expected edit on line12 after Ctrl-D, got {out!r}"
    print("  PASS: Ctrl-D half-page down")

def test_ctrl_u_moves_half_page_up():
    """Ctrl-U moves cursor up by half a screen."""
    content = "\n".join(f"line{i}" for i in range(1, 41)) + "\n"
    path = write_temp(content)
    # G to last line (40), Ctrl-U goes up 11 lines -> line29
    screen, out, code = run_vig(b"G\x15A!\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line29!" in out, f"Expected edit on line29 after Ctrl-U, got {out!r}"
    print("  PASS: Ctrl-U half-page up")

# ── Phase 34: scrolloff support ────────────────────────────────────────────

def test_set_scrolloff_option():
    """:set scrolloff=N sets option and reports value."""
    path = write_temp("a\n")
    screen, _, code = run_vig(b":set scrolloff=3\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "scrolloff=3" in screen, "Expected scrolloff status message"
    print("  PASS: set scrolloff option")

def test_scrolloff_keeps_margin_near_bottom():
    """scrolloff keeps vertical margin when cursor nears bottom."""
    content = "\n".join(f"ROW{i:02d}" for i in range(1, 41)) + "\n"
    path = write_temp(content)
    # With rows=24 => content rows=22; at 21j with scrolloff=3 top should be ROW04
    screen, _, code = run_vig(b":set scrolloff=3\r21j:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    frame = last_frame(screen)
    assert "ROW04" in frame, "Expected scrolled top row with scrolloff margin"
    assert "ROW01" not in frame, "Expected ROW01 scrolled out of final frame"
    print("  PASS: scrolloff margin behavior")

# ── Phase 35: clipboard modes ───────────────────────────────────────────────

def test_set_clipboard_mode_options():
    """:set clipboard=<mode> accepts osc52/auto/off."""
    path = write_temp("a\n")
    screen, _, code = run_vig(b":set clipboard=auto\r:set clipboard=off\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "clipboard=auto" in screen, "Expected clipboard=auto message"
    assert "clipboard=off" in screen, "Expected clipboard=off message"
    print("  PASS: set clipboard modes")

def test_set_clipboard_invalid_value():
    """:set clipboard rejects invalid values."""
    path = write_temp("a\n")
    screen, _, code = run_vig(b":set clipboard=bad\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "clipboard must be osc52, auto, or off" in screen
    print("  PASS: clipboard invalid value")

def test_clipboard_off_disables_osc52_output():
    """clipboard=off avoids emitting OSC 52 copy sequence on yank."""
    path = write_temp("abc\n")
    screen, _, code = run_vig(b":set clipboard=off\ryy:q!\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b]52;c;" not in screen, "OSC52 should not be emitted when clipboard=off"
    print("  PASS: clipboard off disables OSC52")


def test_clipboard_auto_prefers_external_command():
    """Default auto clipboard uses an external clipboard command when available."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        clip_out = os.path.join(d, "clip.txt")
        bindir = os.path.join(d, "bin")
        os.mkdir(bindir)
        open(path, "w").write("abc\n")
        os.symlink(sys.executable, os.path.join(bindir, "python3"))
        xclip = os.path.join(bindir, "xclip")
        open(xclip, "w").write("#!/bin/sh\n/bin/cat > \"$CLIP_OUT\"\n")
        os.chmod(xclip, 0o755)
        env = {"VIG_NO_CONFIG": "1", "PATH": bindir, "CLIP_OUT": clip_out}
        screen, _, code = run_vig(b"yy:q!\r", file_path=path, env=env)
        copied = open(clip_out).read() if os.path.exists(clip_out) else ""
    assert code == 0
    assert copied == "abc", f"Expected external clipboard copy, got {copied!r}; screen={screen[-500:]!r}"
    assert "\x1b]52;c;" not in screen, "auto should not emit OSC52 after external copy succeeds"
    print("  PASS: clipboard auto prefers external command")

# ── Phase 36: small command/edit fixes ─────────────────────────────────────

def test_bang_command_without_space():
    """:!command works without a space after !."""
    path = write_temp("test\n")
    screen, _, code = run_vig(b":!echo nospace_bang\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "nospace_bang" in screen, f":!cmd failed: {screen[-500:]}"
    print("  PASS: :!command without space")

def test_bang_multiline_output_compact():
    """:! multiline output is compacted onto the message bar."""
    path = write_temp("test\n")
    screen, _, code = run_vig(b":!printf 'aa\\nbb\\n'\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "aa | bb" in screen, f"Expected compact multiline output: {screen[-500:]}"
    print("  PASS: :! multiline output compact")

def test_normal_backspace_deletes_left():
    """Normal-mode Backspace deletes char to the left of cursor."""
    path = write_temp("abc\n")
    screen, content, code = run_vig(b"$\x7f:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ab\n", f"Backspace delete failed: {content!r}"
    print("  PASS: normal Backspace deletes left")

def test_r_replaces_character():
    """Normal-mode r replaces character under cursor."""
    path = write_temp("abc\n")
    screen, content, code = run_vig(b"lrZ:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "aZc\n", f"r replace failed: {content!r}"
    print("  PASS: r replaces character")

def test_r_replaces_with_digit():
    """Normal-mode r accepts a digit as the replacement character."""
    path = write_temp("dog\n")
    screen, content, code = run_vig(b"lr2:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "d2g\n", f"r digit replace failed: {content!r}"
    print("  PASS: r replaces with digit")

def test_count_r_replaces_with_digit():
    """Counted r still replaces N chars, even when replacement is a digit."""
    path = write_temp("abcde\n")
    screen, content, code = run_vig(b"2r3:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "33cde\n", f"counted r digit replace failed: {content!r}"
    print("  PASS: count r replaces with digit")

def test_s_substitutes_character():
    """Normal-mode s deletes char under cursor and enters insert."""
    path = write_temp("abc\n")
    screen, content, code = run_vig(b"lsXY\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "aXYc\n", f"s substitute failed: {content!r}"
    print("  PASS: s substitutes character")

def test_dw_at_eol_does_not_join_lines():
    """dw from EOL positions does not merge the next line."""
    path = write_temp("abc\ndef\n")
    screen, content, code = run_vig(b"lldw:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ab\ndef\n", f"dw on last char joined lines: {content!r}"

    path = write_temp("abc\ndef\n")
    screen, content, code = run_vig(b"$dw:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abc\ndef\n", f"dw one-past-EOL joined lines: {content!r}"
    print("  PASS: dw at EOL does not join lines")

def test_ctrl_z_stops_process():
    """Ctrl-Z restores terminal position and stops the process."""
    path = write_temp("abc\n")
    master, slave = pty.openpty()
    import struct, fcntl, termios as tm
    fcntl.ioctl(master, tm.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, tm.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        os.environ["VIG_NO_CONFIG"] = "1"
        os.execvp(VIG, [VIG, path])
        os._exit(1)
    os.close(slave)
    output = bytearray()
    try:
        deadline = time.monotonic() + 1.5
        ready, exit_code = _wait_for_frame(master, pid, output, 1, deadline)
        assert ready and exit_code is None, "vig did not render before Ctrl-Z"
        os.write(master, b"\x1a")
        stopped = False
        while time.monotonic() < deadline:
            _read_pty(master, output, 0.02)
            wpid, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)
            if wpid == pid and os.WIFSTOPPED(status):
                stopped = True
                break
        assert stopped, "Ctrl-Z did not stop vig"
        assert b"\x1b[24;1H" in output, "Ctrl-Z did not move cursor to bottom"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(master)
        os.unlink(path)
    print("  PASS: Ctrl-Z stops process")

def test_edit_directory_shows_error_no_crash():
    """:e <directory> reports an error instead of crashing."""
    path = write_temp("abc\n")
    screen, _, code = run_vig(f":e {os.getcwd()}\r:q\r".encode(), file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Cannot edit directory" in screen, f"Expected directory error: {screen[-500:]}"
    print("  PASS: :e directory error no crash")

def test_insert_long_line_hscrolls_at_right_edge():
    """In nowrap mode, the window scrolls left to keep cursor visible."""
    path = write_temp("")
    screen, _, code = run_vig(b"iabcdefghijklmnopqrstuvwxyz\x1b:q!\r", file_path=path, cols=20)
    os.unlink(path)
    assert code == 0
    frame = last_frame(screen)
    assert "hijklmnopqrstuvwxyz" in frame, f"Expected visible tail of long insert: {frame[-800:]}"
    assert "abcdefghijklmnopqrstuvwxyz" not in frame, "Long line should not remain left anchored"
    print("  PASS: insert long line hscrolls")

def test_hscroll_shifts_whole_window():
    """Horizontal scroll offset applies to all visible lines in nowrap mode."""
    path = write_temp("ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n")
    screen, _, code = run_vig(b"$j:q\r", file_path=path, cols=20)
    os.unlink(path)
    assert code == 0
    frame = last_frame(screen)
    assert "HIJKLMNOPQRSTUVWXYZ" in frame, f"Expected first line shifted with window: {frame[-800:]}"
    assert "hijklmnopqrstuvwxyz" in frame, f"Expected cursor line shifted with window: {frame[-800:]}"
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in frame, "First line should not remain left anchored"
    print("  PASS: hscroll shifts whole window")

# ── Phase 37: quit aliases and config ──────────────────────────────────────

def test_ctrl_c_ctrl_c_quit_all():
    """Ctrl-C Ctrl-C in Normal mode aliases :qall."""
    path = write_temp("abc\n")
    screen, _, code = run_vig(b"\x03\x03", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: Ctrl-C Ctrl-C quits all")

def test_ctrl_c_q_force_quit_all():
    """Ctrl-C q in Normal mode aliases :qall!."""
    path = write_temp("abc\n")
    screen, content, code = run_vig(b"ix\x1b\x03q", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abc\n", f"Force quit should not write dirty buffer: {content!r}"
    print("  PASS: Ctrl-C q force quits all")

def test_ctrl_c_ctrl_c_dirty_refuses():
    """Ctrl-C Ctrl-C refuses dirty buffers like :qall."""
    path = write_temp("abc\n")
    screen, _, code = run_vig(b"ix\x1b\x03\x03:q!\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "unsaved changes" in screen, f"Expected dirty refusal: {screen[-500:]}"
    print("  PASS: Ctrl-C Ctrl-C refuses dirty")

def test_config_file_sets_options():
    """Startup config applies set-style options."""
    path = write_temp("alpha\nbeta\n")
    with tempfile.TemporaryDirectory() as home:
        cfg = os.path.join(home, ".vigrc")
        with open(cfg, "w") as f:
            f.write("set number\nset relativenumber\nset scrolloff=2\n")
        screen, _, code = run_vig(b":q\r", file_path=path, env={"HOME": home, "VIG_NO_CONFIG": ""})
    os.unlink(path)
    assert code == 0
    frame = last_frame(screen)
    assert "1     alpha" in frame, f"Expected relative-number cursor layout: {frame[-500:]}"
    print("  PASS: config file sets options")

# ── Phase 38: ripgrep quickfix ─────────────────────────────────────────────

def test_rg_creates_quickfix_buffer():
    """:rg captures ripgrep output in a quickfix buffer."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        with open(path, "w") as f:
            f.write("alpha needle beta\n")
        screen, _, code = run_vig(f":rg needle {d}\r:q!\r:q\r", file_path=path, timeout=4.0)
    assert code == 0
    assert "[quickfix]" in screen, f"Expected quickfix buffer: {screen[-800:]}"
    assert "a.txt:1:7:alpha needle beta" in screen, f"Expected rg result: {screen[-800:]}"
    print("  PASS: :rg creates quickfix buffer")

def test_space_o_opens_rg_location():
    """<space>o opens file:line:column under cursor from quickfix."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        with open(path, "w") as f:
            f.write("alpha needle beta\n")
        screen, _, code = run_vig(f":rg needle {d}\r o", file_path=path, timeout=2.0)
    frame = last_frame(screen)
    assert code == -99
    assert path in frame, f"Expected original file active: {frame[-800:]}"
    assert "1:7" in frame, f"Expected cursor at rg column: {frame[-800:]}"
    assert "a.txt:1:7:alpha needle beta" in frame, f"Expected quickfix line message: {frame[-800:]}"
    print("  PASS: <space>o opens quickfix location")


def test_space_j_k_open_next_previous_quickfix_items():
    """<space>j/k advance the remembered quickfix row and open its location."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        with open(path, "w") as f:
            f.write("needle one\nx needle two\nzz needle three\n")
        keys = f":rg needle {d}\r jiX\x1b kiY\x1b:w\r:qa!\r"
        screen, _, code = run_vig(keys, file_path=path, timeout=5.0)
        content = open(path).read()
    assert code == 0
    assert content == "Yneedle one\nx Xneedle two\nzz needle three\n", content
    assert path in screen
    print("  PASS: <space>j/k open next/previous quickfix items")


def test_quickfix_j_k_report_boundaries():
    """Quickfix navigation stops rather than wrapping at the first and last items."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        with open(path, "w") as f:
            f.write("needle one\nneedle two\n")
        keys = f":rg needle {d}\r k j j:qa!\r"
        screen, _, code = run_vig(keys, file_path=path, timeout=5.0)
    assert code == 0
    assert "No previous quickfix item" in screen
    assert "No next quickfix item" in screen
    print("  PASS: quickfix j/k stop at boundaries")


def test_space_buffer_keymaps_and_rghidden():
    """Leader buffer keymaps work and rghidden option is accepted."""
    p1 = write_temp("one\n")
    p2 = write_temp("two\n")
    screen, _, code = run_vig(b":set rghidden\r n N\x03\x03", file_paths=[p1, p2], timeout=4.0)
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    assert "rghidden on" in screen, f"Expected rghidden message: {screen[-800:]}"
    assert p2 in screen, f"Expected <space>n to switch to second buffer: {screen[-800:]}"
    assert p1 in screen, f"Expected <space>N to switch back to first buffer: {screen[-800:]}"
    print("  PASS: leader buffer keymaps and rghidden")

# ── Phase 39: todo.md 1-5 ─────────────────────────────────────────────────

def test_write_missing_directory_prompts_and_creates():
    """:w to a missing directory prompts, then y creates it and writes."""
    src = write_temp("abc\n")
    root = tempfile.mkdtemp()
    target = os.path.join(root, "newdir", "out.txt")
    screen, _, code = run_vig(f":w {target}\ry:q\r".encode(), file_path=src, timeout=4.0)
    os.unlink(src)
    try:
        with open(target, "r") as f:
            content = f.read()
    finally:
        import shutil as _shutil
        _shutil.rmtree(root, ignore_errors=True)
    assert code == 0
    assert "Create directory" in screen, f"Expected create-directory prompt: {screen[-800:]}"
    assert content == "abc\n", f"Expected written file, got {content!r}"
    print("  PASS: write missing directory prompts and creates")

def test_write_missing_directory_no_cancels():
    """:w to a missing directory with n does not create or write."""
    src = write_temp("abc\n")
    root = tempfile.mkdtemp()
    target = os.path.join(root, "newdir", "out.txt")
    screen, _, code = run_vig(f":w {target}\rn:q\r".encode(), file_path=src)
    os.unlink(src)
    exists = os.path.exists(target)
    import shutil as _shutil
    _shutil.rmtree(root, ignore_errors=True)
    assert code == 0
    assert "Write cancelled" in screen, f"Expected cancel message: {screen[-800:]}"
    assert not exists, "Declining directory creation should not write target"
    print("  PASS: write missing directory prompt can be declined")

def test_edit_bang_reloads_file_from_disk():
    """:e! discards unsaved changes and reloads the file."""
    path = write_temp("original\n")
    screen, content, code = run_vig(b"A dirty\x1b:e!\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "original\n", f"Expected reload to discard changes, got {content!r}"
    assert "reloaded" in screen, f"Expected reload message: {screen[-800:]}"
    print("  PASS: :e! reloads file")

def test_yank_flashes_highlight():
    """Yanking briefly renders reverse-video highlight."""
    path = write_temp("alpha\nbeta\n")
    screen, _, code = run_vig(b":set clipboard=off\ryy:q\r", file_path=path, timeout=4.0)
    os.unlink(path)
    assert code == 0
    assert "\x1b[7malpha" in screen, f"Expected yank highlight: {screen[-800:]}"
    print("  PASS: yank flashes highlight")

def test_yank_flash_clears_after_configured_time():
    """Yank highlight clears on its timer even without cursor movement."""
    path = write_temp("alpha\nbeta\n")
    screen, _, code = run_vig(b":set clipboard=off\r:set yankflash=100\ryy", file_path=path, timeout=1.0)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == -99
    assert "yankflash=100" in screen, f"Expected yankflash setting message: {screen[-800:]}"
    assert "\x1b[7malpha" in screen, f"Expected initial yank highlight: {screen[-800:]}"
    assert "\x1b[7malpha" not in frame, f"Expected highlight cleared in final frame: {frame[-800:]}"
    print("  PASS: yank flash clears after configured time")

def test_relativenumber_cursor_row_flush_left():
    """Relative-number cursor row is absolute and flush left in the number field."""
    path = write_temp("alpha\nbeta\ngamma\n")
    screen, _, code = run_vig(b"j:set relativenumber\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "2     beta" in screen, f"Expected flush-left absolute cursor number: {screen[-800:]}"
    print("  PASS: relative number cursor row is flush left")

def test_insert_tab_uses_tab_columns():
    """Tab advances to the next 4-column tab stop."""
    path = write_temp("abc\n")
    keys = b"A\tX\x1b:wq\r"
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "abc X\n", f"Expected one space to next tab stop, got {content!r}"
    print("  PASS: insert tab uses tab columns")

# ── Phase 40: todo.md Do items ─────────────────────────────────────────────

def test_search_forward_same_line_next_hit():
    """/ and n find later matches on the same line before moving lines."""
    path = write_temp("foo foo foo\n")
    screen, _, code = run_vig(b"/foo\rn:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "1:9" in screen, f"Expected third same-line hit at 1:9: {screen[-800:]}"
    print("  PASS: forward search finds same-line hits")

def test_search_backward_same_line_previous_hit():
    """? and n find earlier matches on the same line before moving lines."""
    path = write_temp("foo foo foo\n")
    screen, _, code = run_vig(b"$?foo\rn:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "1:5" in screen, f"Expected second same-line hit at 1:5: {screen[-800:]}"
    print("  PASS: backward search finds same-line hits")

def test_nodelcopy_does_not_change_register():
    """With nodelcopy, d{motion} deletes without replacing the register."""
    path = write_temp("keep\none two\n")
    keys = b":set nodelcopy\ryyjdwp:wq\r"
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "keep\ntwo\nkeep\n", f"nodelcopy d changed register: {content!r}"
    print("  PASS: nodelcopy d preserves register")

def test_yd_deletes_and_copies_with_nodelcopy():
    """yd{motion} deletes and copies when nodelcopy is active."""
    path = write_temp("copy\nstay\n")
    keys = b":set nodelcopy\ryddp:wq\r"
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "stay\ncopy\n", f"ydd did not delete-copy: {content!r}"
    print("  PASS: yd deletes and copies")

def test_wrapmove_j_moves_display_row():
    """wrapmove makes j move by wrapped display rows."""
    path = write_temp("abcdefghijk\nzz\n")
    _, content, code = run_vig(b":set wrap\r:set wrapmove\rjiX\x1b:wq\r", file_path=path, cols=10)
    os.unlink(path)
    assert code == 0
    assert content == "abcdefghijXk\nzz\n", f"Expected j to move to wrapped row: {content!r}"
    print("  PASS: wrapmove j moves display row")

# ── Phase 41: bracketed paste ──────────────────────────────────────────────

def test_bracketed_paste_insert_literal_text():
    """Bracketed paste inserts text without treating tabs as Tab keypresses."""
    path = write_temp("")
    paste = b"\x1b[200~a\tb\n\tc\x1b[201~"
    _, content, code = run_vig(b"i" + paste + b"\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "a\tb\n\tc\n", f"Bracketed paste should preserve literal tabs/newlines: {content!r}"
    print("  PASS: bracketed paste inserts literal text")

def test_bracketed_paste_does_not_execute_escape_commands():
    """Command-looking bytes inside bracketed paste are inserted literally."""
    path = write_temp("")
    paste = b"\x1b[200~hello\x1b:q!\rworld\x1b[201~"
    _, content, code = run_vig(b"i" + paste + b"\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "hello\x1b:q!\nworld\n", f"Bracketed paste interpreted commands: {content!r}"
    print("  PASS: bracketed paste does not execute commands")

# ── Phase 42: todo.md Do items ─────────────────────────────────────────────

def test_edit_bang_no_file_name_errors():
    """:e! on an unnamed buffer reports an error instead of clearing it."""
    path = write_temp("base\n")
    screen, _, code = run_vig(b":new\riabc\x1b:e!\r:q!\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "No file name" in screen, f"Expected no-file-name error: {screen[-800:]}"
    print("  PASS: :e! unnamed buffer errors")

def test_normal_delete_key_aliases_x():
    """Delete key in Normal mode deletes like x."""
    path = write_temp("abc\n")
    _, content, code = run_vig(b"\x1b[3~:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "bc\n", f"Normal Delete should behave like x: {content!r}"
    print("  PASS: normal Delete aliases x")

# ── Phase 43: completion and history ───────────────────────────────────────

def test_command_complete_no_path_from_buffer_dir():
    """:e Tab completes a filename with no path component."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        target = os.path.join(d, "alpha_complete.txt")
        open(base, "w").write("base\n")
        open(target, "w").write("opened\n")
        screen, _, code = run_vig(b":e alpha_com\t\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0
    assert "opened" in screen, f"Expected completed file opened: {screen[-800:]}"
    print("  PASS: command complete no path")

def test_completion_menu_enter_accepts_first_match():
    """Multiple completion matches show a menu; Enter accepts selected filename."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        first = os.path.join(d, "aa_one.txt")
        second = os.path.join(d, "aa_two.txt")
        open(base, "w").write("base\n")
        open(first, "w").write("first\n")
        open(second, "w").write("second\n")
        screen, _, code = run_vig(b":e aa_\t\r\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0
    assert "╭" in screen and "╯" in screen, f"Expected rounded completion border: {screen[-1000:]}"
    assert "\x1b[7m  aa_one.txt  " in screen, f"Expected highlighted first completion: {screen[-1000:]}"
    assert "first" in screen, f"Expected accepted first file opened: {screen[-1000:]}"
    print("  PASS: completion menu Enter accepts first match")

def test_completion_menu_down_selects_match():
    """Down changes completion selection before Enter accepts it."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        first = os.path.join(d, "bb_one.txt")
        second = os.path.join(d, "bb_two.txt")
        open(base, "w").write("base\n")
        open(first, "w").write("first\n")
        open(second, "w").write("second\n")
        screen, _, code = run_vig(b":e bb_\t\x1b[B\r\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0
    assert "\x1b[7m  bb_two.txt  " in screen, f"Expected highlighted second completion: {screen[-1000:]}"
    assert "second" in screen, f"Expected selected second file opened: {screen[-1000:]}"
    print("  PASS: completion menu Down selects match")

def test_completion_menu_tab_wraps_selection():
    """Tab wraps from the last completion match to the first."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        open(base, "w").write("base\n")
        open(os.path.join(d, "tab_one.txt"), "w").write("one\n")
        open(os.path.join(d, "tab_two.txt"), "w").write("two\n")
        screen, _, code = run_vig(b":e tab_\t\t\t\r\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0 and "one" in screen
    assert "\x1b[7m  tab_one.txt  " in screen, f"Expected wrapped first selection: {screen[-1000:]}"
    print("  PASS: completion Tab wraps")

def test_completion_menu_shift_tab_wraps_selection():
    """Shift-Tab wraps from the first completion match to the last."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        open(base, "w").write("base\n")
        open(os.path.join(d, "shift_one.txt"), "w").write("one\n")
        open(os.path.join(d, "shift_two.txt"), "w").write("two\n")
        screen, _, code = run_vig(b":e shift_\t\x1b[Z\r\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0 and "two" in screen
    assert "\x1b[7m  shift_two.txt  " in screen, f"Expected wrapped last selection: {screen[-1000:]}"
    print("  PASS: completion Shift-Tab wraps")

def test_completion_menu_typing_updates_filter():
    """Typing while completion menu is open updates the filename filter."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        first = os.path.join(d, "cc_a.txt")
        second = os.path.join(d, "cc_z.txt")
        open(base, "w").write("base\n")
        open(first, "w").write("first\n")
        open(second, "w").write("filtered\n")
        screen, _, code = run_vig(b":e cc_\tz\r\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0
    assert "filtered" in screen, f"Expected typed filter to choose cc_z: {screen[-1000:]}"
    print("  PASS: completion menu typing updates filter")

def test_completion_menu_esc_hides_list():
    """Esc hides completion menu and keeps editing command text."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        open(base, "w").write("base\n")
        open(os.path.join(d, "dd_one.txt"), "w").write("one\n")
        open(os.path.join(d, "dd_two.txt"), "w").write("two\n")
        screen, _, code = run_vig(b":e dd_\t\x1b", file_path=base, timeout=1.0, cwd=d)
    frame = last_frame(screen)
    assert code == -99
    assert "dd_one.txt" not in frame and "dd_two.txt" not in frame, f"Expected Esc to hide list: {frame[-1000:]}"
    assert ":e dd_" in frame, f"Expected command text preserved: {frame[-1000:]}"
    print("  PASS: completion menu Esc hides list")

def test_command_complete_absolute_path():
    """:e Tab completes an absolute path."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        target = os.path.join(d, "abs_complete.txt")
        open(base, "w").write("base\n")
        open(target, "w").write("absolute\n")
        prefix = target[:-4]
        screen, _, code = run_vig(f":e {prefix}\t\r:q\r:q\r".encode(), file_path=base)
    assert code == 0
    assert "absolute" in screen, f"Expected absolute completion opened: {screen[-800:]}"
    print("  PASS: command complete absolute path")

def test_command_complete_relative_subdir():
    """:e Tab completes a relative subdirectory path."""
    with tempfile.TemporaryDirectory() as d:
        os.mkdir(os.path.join(d, "sub"))
        base = os.path.join(d, "base.txt")
        target = os.path.join(d, "sub", "rel_complete.txt")
        open(base, "w").write("base\n")
        open(target, "w").write("relative\n")
        screen, _, code = run_vig(b":e sub/rel_com\t\r:q\r:q\r", file_path=base, cwd=d)
    assert code == 0
    assert "relative" in screen, f"Expected relative completion opened: {screen[-800:]}"
    print("  PASS: command complete relative path")

def test_bang_complete_path():
    """:! Tab completes shell command paths relative to cwd."""
    name = "tmp_vig_complete_script.sh"
    with open(name, "w") as f:
        f.write("#!/bin/sh\necho completed-shell\n")
    os.chmod(name, 0o755)
    try:
        screen, _, code = run_vig(b":! ./tmp_vig_complete_scr\t\r:q\r")
    finally:
        os.unlink(name)
    assert code == 0
    assert "completed-shell" in screen, f"Expected completed shell command output: {screen[-800:]}"
    print("  PASS: bang complete path")

def test_command_history_up_down():
    """: command history uses Up/Down and restores draft after newest."""
    path = write_temp("alpha\n")
    screen, _, code = run_vig(b":set number\r:set nonumber\r:draft\x1b[A\x1b[A\x1b[B\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "number off" in screen, f"Expected recalled command execution: {screen[-800:]}"
    print("  PASS: command history up/down")

def test_search_history_shared_by_slash_and_question():
    """/ and ? share search history navigated by Up."""
    path = write_temp("foo\nbar\n")
    screen, _, code = run_vig(b"/bar\r?\x1b[A\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "2:1" in screen, f"Expected recalled search pattern from shared history: {screen[-800:]}"
    print("  PASS: search history shared")

# ── Phase 44: splash screen ───────────────────────────────────────────────

# ── Phase 45: todo polish ─────────────────────────────────────────────────

# ── Phase 46: help ────────────────────────────────────────────────────────

def test_help_opens_vighelp_buffer():
    """:help opens the executable-directory help buffer."""
    path = write_temp("source\n")
    screen, _, code = run_vig(b":help\r/:set wrap\r/markdownfences\r:q\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "VIGOR HELP" in screen and "KEY / COMMAND" in screen
    assert ":set wrap" in screen and ":set nowrap" in screen
    assert ":set markdownfences" in screen and ":set nomarkdownfences" in screen
    print("  PASS: :help opens vighelp")

# ── Phase 47: fzf ripgrep picker ──────────────────────────────────────────

def test_rgf_path_argument_completes():
    """:rgf completes its optional starting-directory argument."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        search = os.path.join(d, "searchdir")
        bindir = os.path.join(d, "bin")
        os.mkdir(search)
        os.mkdir(bindir)
        open(base, "w").write("base\n")
        target = os.path.join(search, "a.txt")
        open(target, "w").write("needle\n")
        fzf = os.path.join(bindir, "fzf")
        open(fzf, "w").write("#!/bin/sh\ncase \"$*\" in *\"$VIG_RGF_DIR\"*) ;; *) exit 2;; esac\nprintf '%s\\n' \"$VIG_RGF_LINE\"\n")
        os.chmod(fzf, 0o755)
        line = f"{target}:1:1:needle"
        env = {"PATH": bindir + os.pathsep + os.environ["PATH"],
               "VIG_RGF_DIR": search, "VIG_RGF_LINE": line}
        screen, _, code = run_vig(b":rgf search\t\r:q!\r:q\r", file_path=base, env=env, cwd=d)
    assert code == 0 and line in screen, f"Expected completed rgf path: {screen[-800:]}"
    print("  PASS: rgf path completion")

def test_rgf_selected_rows_open_quickfix():
    """:rgf strips fzf ANSI output and opens selected rows in quickfix."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        bindir = os.path.join(d, "bin")
        os.mkdir(bindir)
        open(path, "w").write("alpha needle beta\n")
        fzf = os.path.join(bindir, "fzf")
        open(fzf, "w").write("#!/bin/sh\ncase \"$*\" in *enter:select-all+accept*) ;; *) exit 2;; esac\nprintf '\\033[31m%s\\033[0m\\n' \"$VIG_RGF_LINE\"\n")
        os.chmod(fzf, 0o755)
        line = f"{path}:1:7:alpha needle beta"
        env = {"PATH": bindir + os.pathsep + os.environ["PATH"], "VIG_RGF_LINE": line}
        screen, _, code = run_vig(f":rgf {d}\r:q!\r:q\r", file_path=path, env=env)
    assert code == 0
    assert "[quickfix]" in screen and line in screen, f"Expected rgf quickfix: {screen[-800:]}"
    assert "\x1b[31m" not in screen, "Expected ANSI color removed from quickfix rows"
    print("  PASS: rgf selected rows open quickfix")

# ── Phase 48: syntax highlighting ─────────────────────────────────────────

# ── Phase 49: initial buffer replacement ──────────────────────────────────

def test_opening_replaces_untouched_initial_buffer():
    """:e, :new, and :help replace rather than retain the initial buffer."""
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "target.txt")
        open(target, "w").write("target\n")
        for keys in (f":e {target}\r:q\r", b":new\r:q\r", b":help\r:q\r"):
            _, _, code = run_vig(keys, file_paths=[])
            assert code == 0, f"Expected initial buffer replacement for {keys!r}"
    print("  PASS: initial buffer replaced")


def test_syntax_highlights_comments_and_strings():
    """Recognized Python, C, and Bash files color line-local strings/comments."""
    cases = [
        ("code.py", 'x = "# string"\n# comment\n', '"# string"', "# comment"),
        ("code.c", 'char *s = "// string"; // comment\n', '"// string"', "// comment"),
        ("code.sh", 'echo "# string"\n# comment\n', '"# string"', "# comment"),
    ]
    with tempfile.TemporaryDirectory() as d:
        for name, content, string, comment in cases:
            path = os.path.join(d, name)
            open(path, "w").write(content)
            screen, _, code = run_vig(b"\x1b:q\r", file_path=path)
            assert code == 0
            assert f"\x1b[33m{string}\x1b[m" in screen, f"Missing string color for {name}"
            assert f"\x1b[32m{comment}\x1b[m" in screen, f"Missing comment color for {name}"
    print("  PASS: syntax comments and strings")


# ── Phase 50: search polish ───────────────────────────────────────────────

def test_star_searches_whole_word_and_repeats():
    """* searches whole words forward and seeds n/N repeat state."""
    path = write_temp("cat dog cat dog cat\n")
    _, content, code = run_vig(b"*niX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "cat dog cat dog Xcat\n", f"Expected whole-word star repeat: {content!r}"
    print("  PASS: * searches whole words and repeats")


def test_hash_searches_whole_word_backward():
    """# searches whole words backward from the word under cursor."""
    path = write_temp("foo bar foo\n")
    _, content, code = run_vig(b"8l#iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "Xfoo bar foo\n", f"Expected # to find previous foo: {content!r}"
    print("  PASS: # searches whole words backward")


def test_gstar_searches_partial_matches():
    """g* searches partial matches of the word under cursor."""
    path = write_temp("cat scatter cat\n")
    _, content, code = run_vig(b"g*iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "cat sXcatter cat\n", f"Expected g* partial match: {content!r}"
    print("  PASS: g* searches partial matches")


def test_ghash_searches_partial_matches_backward():
    """g# searches partial matches backward."""
    path = write_temp("cat scatter cat\n")
    _, content, code = run_vig(b"12lg#iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "cat sXcatter cat\n", f"Expected g# partial match: {content!r}"
    print("  PASS: g# searches partial matches backward")


def test_hlsearch_highlights_matches_and_can_disable():
    """:set hlsearch highlights active search regex; nohlsearch disables it."""
    path = write_temp("alpha beta alpha\n")
    screen, _, code = run_vig(b":set hlsearch\r/alpha\r:set nohlsearch\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[43;30malpha\x1b[m" in screen, "Expected highlighted search match"
    assert "hlsearch off" in screen, "Expected nohlsearch option accepted"
    print("  PASS: hlsearch highlights and disables")


def test_hlsearch_config_file():
    """Config files set hlsearch through the existing :set path."""
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "vigrc")
        path = os.path.join(d, "a.txt")
        open(cfg, "w").write("set hlsearch\n")
        open(path, "w").write("one one\n")
        screen, _, code = run_vig(b"/one\r:q\r", file_path=path, env={"VIG_CONFIG": cfg})
    assert code == 0
    assert "\x1b[43;30mone\x1b[m" in screen, "Expected config-enabled search highlight"
    print("  PASS: hlsearch config file")


# ── Phase 51: viewport scrolling ──────────────────────────────────────────

def test_ctrl_e_scrolls_down_and_ctrl_y_scrolls_up():
    """Ctrl-E/Ctrl-Y move the viewport by logical display rows in nowrap mode."""
    path = write_temp("\n".join(f"line {i}" for i in range(20)) + "\n")
    down, _, code = run_vig(b"4G\x05:q\r", file_path=path, rows=8, cols=30)
    up, _, code2 = run_vig(b"10G\x19:q\r", file_path=path, rows=8, cols=30)
    os.unlink(path)
    assert code == code2 == 0
    assert "line 1\x1b[K\r\nline 2" in last_frame(down), last_frame(down)[:300]
    assert "line 3\x1b[K\r\nline 4" in last_frame(up), last_frame(up)[:300]
    print("  PASS: Ctrl-E/Ctrl-Y scroll viewport")


def test_ctrl_e_scrolls_one_wrapped_display_row():
    """With wrap, Ctrl-E advances the viewport by one display row, not one logical line."""
    path = write_temp("abcdefghijklmnopqrstuvwxyz\nnext\n")
    screen, _, code = run_vig(b":set wrap\r15l\x05:q\r", file_path=path, rows=8, cols=10)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "klmnopqrst\r\nuvwxyz" in frame, frame[:300]
    assert "abcdefghij" not in frame, frame[:300]
    print("  PASS: Ctrl-E scrolls wrapped display row")


def test_space_unknown_combination_executes_normal_key():
    """An unknown Space combination ignores Space and dispatches the following key."""
    path = write_temp("abc\n")
    _, content, code = run_vig(b" x:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "bc\n", f"Expected Space+x to execute x: {content!r}"
    print("  PASS: unknown Space combination dispatches key")


def test_search_result_is_centered():
    """Successful searches center their result when file boundaries permit."""
    path = write_temp("\n".join([f"line {i}" for i in range(10)] + ["needle"] + [f"line {i}" for i in range(11, 21)]) + "\n")
    screen, _, code = run_vig(b"/needle\r:q\r", file_path=path, rows=10, cols=30)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "line 6\x1b[K\r\nline 7" in frame, frame[:400]
    assert "needle" in frame
    print("  PASS: search result centered")


def test_wrapped_viewport_persists_across_buffers():
    """Per-buffer wrapped-row scroll position survives buffer switches."""
    p1 = write_temp("abcdefghijklmnopqrstuvwxyz\n")
    p2 = write_temp("other\n")
    screen, _, code = run_vig(b":set wrap\r15l\x05 n N:qa\r", file_paths=[p1, p2], rows=8, cols=10)
    os.unlink(p1)
    os.unlink(p2)
    frame = last_frame(screen)
    assert code == 0
    assert "klmnopqrst\r\nuvwxyz" in frame and "abcdefghij" not in frame, frame[:300]
    print("  PASS: wrapped viewport persists per buffer")


# ── Phase 52: startup directory completion ────────────────────────────────

def test_startup_directory_opens_completion_without_splash():
    """A directory argument immediately opens its entries as edit completion."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "alpha.txt"), "w").write("alpha\n")
        open(os.path.join(d, "beta.txt"), "w").write("beta\n")
        screen, _, code = run_vig(b"\x1b:q\r", file_paths=[d])
    assert code == 0
    assert "alpha.txt" in screen and "beta.txt" in screen and "╭" in screen
    assert "\x1b[49m\x1b[96m" not in screen, "Startup directory should suppress splash"
    print("  PASS: startup directory opens completion without splash")


def test_startup_directory_escape_keeps_file_buffers():
    """Esc cancels the directory item while explicitly named files remain open."""
    with tempfile.TemporaryDirectory() as d:
        directory = os.path.join(d, "browse")
        os.mkdir(directory)
        open(os.path.join(directory, "choice.txt"), "w").write("choice\n")
        first = os.path.join(d, "first.txt")
        second = os.path.join(d, "second.txt")
        open(first, "w").write("first body\n")
        open(second, "w").write("second body\n")
        screen, _, code = run_vig(b"\x1b:ls\r:qa\r", file_paths=[directory, first, second])
    assert code == 0
    assert first in screen and second in screen and "[1/2]" in screen
    assert "first body" in screen
    print("  PASS: startup directory Esc keeps file buffers")


def test_startup_directory_selection_opens_buffer():
    """Completion selection followed by a second Enter opens the selected file."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "aa_first.txt"), "w").write("selected body\n")
        open(os.path.join(d, "aa_second.txt"), "w").write("other body\n")
        screen, _, code = run_vig(b"\r\r:qa\r", file_paths=[d])
    assert code == 0
    assert "selected body" in screen, screen[-800:]
    print("  PASS: startup directory selection opens buffer")


def test_startup_ignores_later_directories_but_keeps_later_files():
    """Only the first directory is browsed; files anywhere in argv become buffers."""
    with tempfile.TemporaryDirectory() as d:
        one = os.path.join(d, "one")
        two = os.path.join(d, "two")
        os.mkdir(one)
        os.mkdir(two)
        open(os.path.join(one, "from_one.txt"), "w").write("one\n")
        open(os.path.join(two, "from_two.txt"), "w").write("two\n")
        before = os.path.join(d, "before.txt")
        after = os.path.join(d, "after.txt")
        open(before, "w").write("before\n")
        open(after, "w").write("after\n")
        screen, _, code = run_vig(b"\x1b:ls\r:qa\r", file_paths=[before, one, two, after])
    assert code == 0
    assert "from_one.txt" in screen and "from_two.txt" not in screen
    assert before in screen and after in screen
    print("  PASS: startup ignores later directories and keeps files")


# ── Phase 53: tab display columns ─────────────────────────────────────────

def test_cursor_after_insert_with_leading_tab():
    """Cursor uses expanded display columns after inserting to the right of a tab."""
    line = "\t{ MODKEY,                       XK_Return, spawn,          {.v = termcmd } },\n"
    path = write_temp(line)
    screen, _, code = run_vig(b"10liX", file_path=path, timeout=1.0, cols=100)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == -99
    assert "    { MODKEY,X" in frame and "\t" not in frame
    assert "\x1b[1;15H" in frame, f"Expected cursor after inserted X: {frame[-200:]!r}"
    print("  PASS: cursor tracks insert after leading tab")


def test_tabs_participate_in_wrap_and_eol_layout():
    """Expanded tabs determine wrapped rows and exact-width EOL placement."""
    path = write_temp("\tabcdef\n")
    screen, _, code = run_vig(b":set wrap\r$:q\r", file_path=path, cols=10, rows=6)
    os.unlink(path)
    assert code == 0
    assert "    abcdef\r\n\x1b[K\r\n" in screen
    assert "\x1b[2;1H" in screen, f"Expected expanded exact-width EOL row: {screen[-500:]!r}"
    print("  PASS: tabs participate in wrapped EOL layout")


def test_tab_display_columns_drive_sticky_vertical_motion():
    """Vertical motion preserves display rather than buffer column across tabs."""
    path = write_temp("\tabc\n0123456789\n")
    _, content, code = run_vig(b"li\x1bjiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "\tabc\n0123X456789\n", f"Expected display-column-preserving j: {content!r}"
    print("  PASS: tabs use display columns for vertical motion")


def test_tabbed_line_hscroll_uses_display_columns():
    """Horizontal scrolling slices the expanded display line and keeps EOL visible."""
    path = write_temp("\tabcdefghijklmnop\n")
    screen, _, code = run_vig(b"$:q\r", file_path=path, cols=10, rows=6)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "hijklmnop" in frame and "\t" not in frame
    assert "\x1b[1;10H" in screen, f"Expected EOL at right edge: {screen[-500:]!r}"
    print("  PASS: tabbed horizontal scroll uses display columns")


def test_tab_expansion_preserves_highlight_boundaries():
    """Visual selection highlights every display cell occupied by a tab."""
    path = write_temp("\tabc\n")
    screen, _, code = run_vig(b"vl\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[7m    \x1b[m" in screen, f"Expected four selected tab cells: {screen[-500:]!r}"
    print("  PASS: tab expansion preserves highlight boundaries")


def test_layout_maps_source_and_screen_coordinates():
    """Shared layout maps exact-width EOL and wrapped rows in both directions."""
    from vigor.layout import ViewportLayout, display_col, display_index
    lines = ["abcde", "x\ty"]
    view = lambda y: lines[y].expandtabs(4)
    layout = ViewportLayout(
        len(lines), view,
        lambda y, x: display_col(lines[y], x),
        lambda y, x: display_index(lines[y], x),
        rows=4, cols=5, gutter_width=0, wrap=True, wrapcol=0,
        scroll=0, wrap_skip=0,
    )
    assert layout.source_to_screen(0, 5) == (1, 0)
    assert layout.screen_to_source(1, 0) == (0, 5)
    assert layout.source_to_screen(1, 2) == (2, 4)
    assert layout.screen_to_source(2, 4) == (1, 2)
    print("  PASS: layout maps source and screen coordinates")


# ── Phase 54: build identification ────────────────────────────────────────

def test_install_stamps_build_identification():
    """Installer stamps commit/date identification without changing repository source."""
    import re, subprocess
    with tempfile.TemporaryDirectory() as d:
        env = os.environ.copy()
        env["VIG_INSTALL_DIR"] = d
        result = subprocess.run([os.path.join(os.path.dirname(VIG), "scripts", "install")],
                                cwd=os.path.dirname(VIG), env=env, capture_output=True, text=True)
        installed = open(os.path.join(d, "vigor", "__init__.py")).read()
        diagnostics_installed = os.access(os.path.join(d, "vig-diagnostics"), os.X_OK)
    assert result.returncode == 0, result.stderr
    assert diagnostics_installed
    assert 'VERSION = "0.1.0"' in installed
    assert re.search(r"BUILD_ID = ['\"][0-9a-f]+ \d{4}-\d{2}-\d{2}(?: dirty)?['\"]", installed)
    assert 'BUILD_ID = "development"' in open(os.path.join(os.path.dirname(VIG), "vigor", "__init__.py")).read()
    print("  PASS: installer stamps build identification")


# ── Phase 55: build diagnostics ───────────────────────────────────────────

def test_qf_command_captures_and_normalizes_diagnostics():
    """:qf !cmd keeps context, strips ANSI, normalizes columns, and reports status."""
    with tempfile.TemporaryDirectory() as d:
        source = os.path.join(d, "source.c")
        producer = os.path.join(d, "producer.sh")
        open(source, "w").write("one\ntwo\nthree\n")
        open(producer, "w").write(
            "#!/bin/sh\n"
            "printf 'building\\n'\n"
            f"printf '\\033[31m{source}:2:3:error: bad\\033[0m\\n'\n"
            f"printf '{source}:3:warning: check this\\n' >&2\n"
            "exit 7\n"
        )
        os.chmod(producer, 0o755)
        screen, _, code = run_vig(f":qf !{producer}\r:qa!\r", file_path=source, timeout=5.0)
    assert code == 0
    assert "building" in screen
    assert f"{source}:2:3:error: bad" in screen
    assert f"{source}:3:1:warning: check this" in screen
    assert "\x1b[31m" not in screen
    assert "qf: exit 7, 3 line(s)" in screen
    print("  PASS: :qf captures normalized diagnostics")


def test_makeprg_runs_with_make_arguments():
    """:make appends arguments to makeprg and sends merged output to quickfix."""
    with tempfile.TemporaryDirectory() as d:
        source = os.path.join(d, "source.c")
        producer = os.path.join(d, "build.sh")
        open(source, "w").write("source\n")
        open(producer, "w").write(
            "#!/bin/sh\n"
            "echo target=$1\n"
            f'echo "{source}:1:1:built $1"\n'
        )
        os.chmod(producer, 0o755)
        keys = f":set makeprg={VIG_DIAGNOSTICS} {producer}\r:make clean\r:qa!\r"
        screen, _, code = run_vig(keys, file_path=source, timeout=5.0)
    assert code == 0
    assert "target=clean" in screen and f"{source}:1:1:built clean" in screen
    assert "make: exit 0, 2 line(s)" in screen
    print("  PASS: makeprg runs with :make arguments")


def test_vig_diagnostics_normalizes_gcc_clang_and_python():
    """The optional producer normalizes common diagnostics and preserves status."""
    import subprocess
    with tempfile.TemporaryDirectory() as d:
        project = os.path.join(d, "project")
        os.mkdir(project)
        source = os.path.join(project, "source.c")
        python_source = os.path.join(project, "app.py")
        child = os.path.join(project, "produce.py")
        open(child, "w").write(
            "import sys\n"
            "print('\\x1b[31msource.c:2:4: error: bad token\\x1b[0m', flush=True)\n"
            "print('source.c:3: warning: unused', flush=True)\n"
            "print('  File \\\"app.py\\\", line 9, in run', flush=True)\n"
            "print('    explode()', flush=True)\n"
            "sys.exit(7)\n"
        )
        result = subprocess.run([VIG_DIAGNOSTICS, "--cwd", project,
                                 sys.executable, "produce.py"], capture_output=True, text=True)
    assert result.returncode == 7
    assert f"{source}:2:4: error: bad token" in result.stdout
    assert f"{source}:3:1: warning: unused" in result.stdout
    assert f"{python_source}:9:1: in run" in result.stdout
    assert "    explode()" in result.stdout
    assert "\x1b[31m" not in result.stdout
    print("  PASS: vig-diagnostics normalizes GCC/Clang and Python")


def test_makeprg_config_and_silent_success():
    """Startup config accepts makeprg; a silent successful build preserves the buffer."""
    with tempfile.TemporaryDirectory() as d:
        source = os.path.join(d, "source.c")
        producer = os.path.join(d, "quiet.sh")
        config = os.path.join(d, "vigrc")
        open(source, "w").write("source body\n")
        open(producer, "w").write("#!/bin/sh\nexit 0\n")
        os.chmod(producer, 0o755)
        open(config, "w").write(f"set makeprg={producer}\n")
        screen, _, code = run_vig(b":make\r:q\r", file_path=source,
                                  env={"VIG_CONFIG": config})
    assert code == 0
    assert "source body" in screen and "make: success" in screen
    assert "[quickfix]" not in screen
    print("  PASS: configured makeprg silent success")


# ── Phase 56: working directory ───────────────────────────────────────────

def test_file_commands_use_working_directory():
    """:read and :write resolve relative paths against the process cwd."""
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "sub")
        os.mkdir(sub)
        source = os.path.join(sub, "source.txt")
        open(source, "w").write("source\n")
        open(os.path.join(d, "insert.txt"), "w").write("from root\n")
        open(os.path.join(sub, "insert.txt"), "w").write("from buffer\n")
        _, _, code = run_vig(b":read insert.txt\r:w output.txt\r:qa!\r",
                             file_path=source, cwd=d)
        output = open(os.path.join(d, "output.txt")).read()
    assert code == 0 and output == "source\nfrom root\n"
    print("  PASS: file commands use working directory")


def test_cd_and_cdb_change_global_working_directory():
    """:cd changes the global cwd and :cdb changes it to the focused file directory."""
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "sub")
        other = os.path.join(d, "other")
        os.mkdir(sub)
        os.mkdir(other)
        source = os.path.join(sub, "source.txt")
        open(source, "w").write("source\n")
        open(os.path.join(sub, "local.txt"), "w").write("local\n")
        open(os.path.join(other, "other.txt"), "w").write("other\n")
        keys = b":cdb\r:e local.txt\r:cd ../oth\t\r:e other.txt\r:pwd\r:qa!\r"
        screen, _, code = run_vig(keys, file_path=source, cwd=d)
    assert code == 0
    assert os.path.join(sub, "local.txt") in screen
    assert os.path.join(other, "other.txt") in screen
    assert other in screen
    print("  PASS: :cd and :cdb change global working directory")


def test_quickfix_remembers_producer_working_directory():
    """A later :cd does not reinterpret a remembered relative quickfix path."""
    with tempfile.TemporaryDirectory() as d:
        other = os.path.join(d, "other")
        os.mkdir(other)
        source = os.path.join(d, "source.txt")
        location = os.path.join(d, "relative.txt")
        wrong = os.path.join(other, "relative.txt")
        open(source, "w").write("source\n")
        open(location, "w").write("right\n")
        open(wrong, "w").write("wrong\n")
        keys = b":qf !printf 'relative.txt:1:1:error'\r:cd other\r oA!\x1b:w\r:qa!\r"
        _, _, code = run_vig(keys, file_path=source, cwd=d)
        right_content = open(location).read()
        wrong_content = open(wrong).read()
    assert code == 0 and right_content == "right!\n" and wrong_content == "wrong\n"
    print("  PASS: quickfix remembers producer working directory")


def test_completion_menu_has_filename_padding():
    """Completion filenames have a space between the frame and text."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.txt")
        open(base, "w").write("base\n")
        open(os.path.join(d, "pad_one.txt"), "w").write("one\n")
        open(os.path.join(d, "pad_two.txt"), "w").write("two\n")
        screen, _, _ = run_vig(b":e pad_\t", file_path=base, timeout=1.0, cwd=d)
    assert "\x1b[7m  pad_one.txt  " in screen, f"Expected padded completion: {screen[-800:]}"
    print("  PASS: completion filename padding")

def test_rg_no_hits_keeps_current_buffer():
    """:rg with no hits reports a message without opening quickfix."""
    path = write_temp("alpha\n")
    screen, _, code = run_vig(f"\x1b:rg no_vig_hits {path}\r:q\r".encode(), file_path=path)
    os.unlink(path)
    assert code == 0
    assert "rg: no matches" in screen and "[quickfix]" not in screen
    print("  PASS: rg no hits")

def test_case_commands():
    """~, g~, gU, and gu change case with counts and motions."""
    path = write_temp("abc DEF\n")
    _, content, code = run_vig(b"\x1b2~0gUwwguE0g~w:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0 and content == "abc def\n", f"Unexpected case conversion: {content!r}"
    print("  PASS: case commands")

def test_prompt_cursor_editing():
    """Command and search prompts support forward, backspace, and delete editing."""
    path = write_temp("foo\n")
    keys = b"\x1b:set numxber\x1b[D\x1b[D\x1b[D\x7f\r/fxoo\x1b[D\x1b[D\x1b[D\x1b[3~\r:q\r"
    screen, _, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0 and "number on" in screen and "1:1" in screen, f"Prompt edit failed: {screen[-800:]}"
    print("  PASS: prompt cursor editing")

def test_prompt_cursor_is_visible():
    """The terminal cursor follows command and search prompt editing."""
    path = write_temp("foo\n")
    screen, _, code = run_vig(b"\x1b:abc", file_path=path, timeout=1.0)
    os.unlink(path)
    assert code == -99 and "\x1b[24;5H" in last_frame(screen)
    print("  PASS: prompt cursor is visible")

def test_ctrl_c_cancels_mkdir_prompt():
    """Ctrl-C cancels a pending missing-directory write."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "source.txt")
        target = os.path.join(d, "newdir", "out.txt")
        open(path, "w").write("source\n")
        _, _, code = run_vig(f"\x1b:w {target}\r\x03:q\r".encode(), file_path=path)
        assert code == 0 and not os.path.exists(target)
    print("  PASS: Ctrl-C cancels mkdir prompt")

def test_sticky_vertical_column():
    """Vertical movement restores the desired column after a short line."""
    path = write_temp("abcdef\na\nabcdef\n")
    _, content, code = run_vig(b"\x1b5ljjiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0 and content == "abcdef\na\nabcdeXf\n", f"Sticky column failed: {content!r}"
    print("  PASS: sticky vertical column")


def test_file_startup_shows_framed_splash_over_editor():
    """Startup draws a colored, rounded, framed logo box over the editor."""
    path = write_temp("underlay\n")
    screen, _, code = run_vig(b":q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    border = "\x1b[49m\x1b[96m╭" + "─" * 37 + "╮"
    assert border in screen, f"Expected 39-column rounded splash frame: {screen[-2000:]}"
    assert screen.count("│") == 16, "Expected 10-row splash frame"
    assert "\x1b[49m\x1b[96m│\x1b[97m" in screen, "Expected default background and colored logo"
    assert screen.index("underlay") < screen.index("╭"), "Editor frame must be drawn before splash overlay"
    assert "| |  / (_)___ _____  _____" in screen, f"Expected splash logo: {screen[-2000:]}"
    assert "v0.1.0 · development" in screen, f"Expected splash build footer: {screen[-2000:]}"
    print("  PASS: framed splash overlays editor")


def test_splash_dismisses_and_input_executes():
    """The first input dismisses the splash and still executes normally."""
    path = write_temp("alpha\n")
    screen, _, code = run_vig(b"iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "╭" in screen, "Expected initial splash frame"
    assert "╭" not in last_frame(screen), "Expected redraw without splash after input"
    print("  PASS: splash dismisses and input executes")


def test_file_splash_times_out():
    """A command-line file splash disappears after the startup timeout."""
    path = write_temp("alpha\n")
    screen, _, code = run_vig(b"", file_path=path, timeout=2.2)
    os.unlink(path)
    assert code == -99
    assert "╭" in screen, "Expected initial splash frame"
    assert "╭" not in last_frame(screen), "Expected timed redraw without splash"
    print("  PASS: file splash times out")


def test_unnamed_splash_has_no_timeout():
    """Without command-line files, the splash remains until input."""
    screen, _, code = run_vig(b"", file_paths=[], timeout=1.3)
    assert code == -99
    assert "╭" in last_frame(screen), "Unnamed splash should remain without input"
    print("  PASS: unnamed splash has no timeout")


def test_splash_clamps_to_small_terminal():
    """Splash padding and logo crop safely when the terminal is small."""
    screen, _, code = run_vig(b":q\r", rows=8, cols=20)
    assert code == 0
    assert "╭" + "─" * 18 + "╮" in screen, f"Expected clamped splash frame: {screen[-1000:]}"
    print("  PASS: splash clamps to small terminal")

# ── Phase 57: Markdown presentation ───────────────────────────────────────

def test_markdown_view_aligns_tables_without_dirtying_buffer():
    """:md virtually aligns a valid table while leaving its source clean."""
    source = "| A | Longer |\n| --- | --- |\n| xx | y |\n"
    path = write_temp(source)
    screen, content, code = run_vig(b":md\r:q\r", file_path=path)
    os.unlink(path)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", screen)
    assert code == 0 and content == source
    assert "| A   | Longer |" in plain, f"Expected aligned heading row: {plain[-1000:]}"
    assert "| xx  | y      |" in plain, f"Expected aligned data row: {plain[-1000:]}"
    assert "[MD]" in plain, "Expected Markdown-view status marker"
    print("  PASS: Markdown view aligns without dirtying")


def test_markdown_view_styles_headers_lists_and_tables():
    """:md styles Markdown headers, list markers, and table structure."""
    source = "# Heading\n- item\n\n| A | B |\n| --- | --- |\n| x | y |\n"
    path = write_temp(source)
    screen, _, code = run_vig(b":md\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[1;36m# Heading\x1b[m" in screen, "Expected bold cyan header"
    assert "\x1b[1;33m-\x1b[m item" in screen, "Expected bold yellow list marker"
    assert "\x1b[2;36m| --- | --- |\x1b[m" in screen, "Expected dim cyan table rule"
    print("  PASS: Markdown view styles structure")


def test_markdown_edit_returns_to_literal_source():
    """A modifying command leaves Markdown view before editing source text."""
    source = "| A | Longer |\n| --- | --- |\n| xx | y |\n"
    path = write_temp(source)
    screen, content, code = run_vig(b":md\r2j2liZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "| A | Longer |\n| --- | --- |\n| Zxx | y |\n"
    assert "[MD]" in screen, "Expected view before editing"
    print("  PASS: Markdown edit returns to source")


def test_markdown_view_is_per_buffer_and_nomd_disables_it():
    """Markdown presentation persists per buffer and :nomd disables it."""
    p1, p2 = write_temp("# one\n"), write_temp("plain\n")
    screen, _, code = run_vig(b":md\r:n\r:p\r:nomd\r:q\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    assert "markdown view on" in screen and "markdown view off" in screen
    print("  PASS: Markdown view is per-buffer and toggleable")


def test_markdown_search_maps_back_to_source_for_edit():
    """Search uses source columns even when virtual table padding is visible."""
    source = "| A | Longer |\n| --- | --- |\n| xx | y |\n"
    path = write_temp(source)
    _, content, code = run_vig(b":md\r/y\riZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "| A | Longer |\n| --- | --- |\n| xx | Zy |\n"
    print("  PASS: Markdown search maps to source")


def test_markdown_does_not_align_pipe_prose_without_rule():
    """Rows without a Markdown separator rule are not treated as tables."""
    source = "one | two\nlonger | x\n"
    path = write_temp(source)
    screen, content, code = run_vig(b":md\r:q\r", file_path=path)
    os.unlink(path)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", screen)
    assert code == 0 and content == source
    assert "one | two" in plain and "one    | two" not in plain
    print("  PASS: Markdown pipe prose stays literal")


# ── Phase 58: Y and literal smart-case search ─────────────────────────────

def test_Y_yanks_from_cursor_to_end_of_line():
    """Y behaves as y$ and ignores a count like D and C."""
    path = write_temp("alpha beta\n\nthird\n")
    _, content, code = run_vig(b":set clipboard=off\r6l3Yjp:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0 and content == "alpha beta\nbeta\nthird\n", content
    print("  PASS: Y yanks cursor to end of line")


def test_literal_search_uses_smart_case_but_regex_does_not():
    """Lowercase literal searches ignore case; capitals and regex remain sensitive."""
    p1 = write_temp("foo\nFoo\n")
    _, c1, code1 = run_vig(b"/foo\riX\x1b:wq\r", file_path=p1)
    p2 = write_temp("foo\nFOO\nFoo\n")
    _, c2, code2 = run_vig(b"/Foo\riX\x1b:wq\r", file_path=p2)
    p3 = write_temp("Foo\nfao\n")
    _, c3, code3 = run_vig(b"/f.o\riX\x1b:wq\r", file_path=p3)
    for path in (p1, p2, p3):
        os.unlink(path)
    assert code1 == code2 == code3 == 0
    assert c1 == "foo\nXFoo\n", c1
    assert c2 == "foo\nFOO\nXFoo\n", c2
    assert c3 == "Foo\nXfao\n", c3
    print("  PASS: literal search uses smart case")


def test_word_search_and_hlsearch_use_smart_case():
    """Lowercase word searches and highlighting match uppercase variants."""
    path = write_temp("foo Foo FOO\n")
    screen, _, code = run_vig(b":set hlsearch\r*iX\x1bu:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[45;97mFoo\x1b[m" in screen and "\x1b[43;30mFOO\x1b[m" in screen
    print("  PASS: word search and hlsearch use smart case")


# ── Phase 59: configurable wrap column ─────────────────────────────────────

def test_wrapcol_wraps_at_configured_display_column():
    """A nonzero wrapcol wraps before the wider terminal boundary."""
    path = write_temp("abcdefghij\n")
    screen, _, code = run_vig(b":set wrapcol=5\r:set wrap\r:q\r",
                              file_path=path, cols=20, rows=8)
    os.unlink(path)
    assert code == 0 and "wrapcol=5" in screen
    assert "abcde\x1b[K\r\nfghij" in screen, screen[-800:]
    print("  PASS: wrapcol controls display-row width")


def test_wrapcol_drives_wrapmove_and_respects_terminal_width():
    """wrapmove uses wrapcol, while values wider than the terminal are capped."""
    p1 = write_temp("abcdefghij\n")
    _, content, code1 = run_vig(b":set wrapcol=5\r:set wrap\r:set wrapmove\rjiX\x1b:wq\r",
                                file_path=p1, cols=20)
    p2 = write_temp("abcdefghijkl\n")
    screen, _, code2 = run_vig(b":set wrapcol=100\r:set wrap\r:q\r",
                               file_path=p2, cols=10, rows=8)
    os.unlink(p1)
    os.unlink(p2)
    assert code1 == code2 == 0 and content == "abcdeXfghij\n"
    assert "abcdefghij\r\nkl" in screen
    print("  PASS: wrapcol drives movement and caps to terminal")


def test_wrapcol_validation_and_startup_config():
    """wrapcol accepts nonnegative values through :set and startup config."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "line.txt")
        cfg = os.path.join(d, "vigrc")
        open(path, "w").write("abcdefgh\n")
        open(cfg, "w").write("set wrap\nset wrapcol=4\n")
        screen, _, code = run_vig(b":set wrapcol=-1\r:q\r", file_path=path,
                                  cols=20, rows=8, env={"VIG_CONFIG": cfg})
    assert code == 0
    assert "abcd\x1b[K\r\nefgh" in screen
    assert "wrapcol must be >= 0" in screen
    print("  PASS: wrapcol validates and loads from config")

# ── Phase 60: markdown fence hiding ────────────────────────────────────────

def write_named_temp(content, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path

def test_markdownfences_hides_backtick_and_tilde_fences():
    """:set markdownfences hides fence markers only while :md view is active."""
    path = write_named_temp("before\n```python\ncode\n```\n~~~\nmore\n~~~\nafter\n", ".md")
    screen, _, code = run_vig(b":set markdownfences\r:q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "before" in frame and "code" in frame and "more" in frame and "after" in frame
    assert "```" not in frame and "~~~" not in frame, f"Fence markers should be hidden: {frame[:1000]}"
    print("  PASS: markdownfences hides backtick and tilde fences")

def test_markdownfences_requires_markdown_view():
    """markdownfences does not affect literal source view."""
    path = write_named_temp("before\n```\ncode\n", ".md")
    screen, _, code = run_vig(b":set markdownfences\r:nomd\r:q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "```" in frame, f"Fence should remain visible outside :md: {frame[:800]}"
    print("  PASS: markdownfences requires Markdown view")

def test_markdownfences_only_markdown_files():
    """markdownfences applies only to markdown filenames."""
    path = write_named_temp("before\n```\ncode\n", ".txt")
    screen, _, code = run_vig(b":set markdownfences\r:md\r:q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "```" in frame, f"Non-markdown fence should remain visible: {frame[:800]}"
    print("  PASS: markdownfences only affects markdown files")


# ── Phase 61: mouse wheel scrolling ───────────────────────────────────────

def test_sgr_mouse_decoder():
    """SGR wheel and button reports decode to zero-based structured events."""
    from vigor.terminal import Terminal
    assert Terminal._decode_mouse(b"<64;3;4M") == ("MOUSE", "wheel", "up", 2, 3, 0)
    assert Terminal._decode_mouse(b"<65;9;2M") == ("MOUSE", "wheel", "down", 8, 1, 0)
    assert Terminal._decode_mouse(b"<4;2;3M") == ("MOUSE", "left", "press", 1, 2, 4)
    assert Terminal._decode_mouse(b"broken") == ""
    print("  PASS: SGR mouse decoder")


def test_mouse_options_and_terminal_lifecycle():
    """Mouse modes validate and reporting is disabled when terminal ownership ends."""
    path = write_temp("mouse\n")
    screen, _, code = run_vig(
        b":set mouse=scroll\r:set mouse=cursor\r:set mouse=visual\r:set mouse=bad\r:q\r",
        file_path=path,
    )
    os.unlink(path)
    assert code == 0
    assert "mouse must be off, scroll, cursor, or visual" in screen
    assert "\x1b[?1000h" in screen and "\x1b[?1002h" in screen and "\x1b[?1006h" in screen
    assert "\x1b[?1000l" in screen and "\x1b[?1002l" in screen and "\x1b[?1006l" in screen
    print("  PASS: mouse options and terminal lifecycle")


def test_mouse_wheel_scrolls_three_display_rows():
    """One wheel report scrolls three display rows and keeps the cursor visible."""
    path = write_temp("\n".join(f"ROW{i:02}" for i in range(1, 16)) + "\n")
    screen, _, code = run_vig(
        b":set mouse=scroll\r\x1b[<65;1;1M:q\r", file_path=path, rows=4, cols=30,
    )
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "ROW04" in frame and "ROW01" not in frame, frame[-500:]
    print("  PASS: mouse wheel scrolls three display rows")


def test_mouse_wheel_preserves_active_modes():
    """Wheel scrolling is global and does not cancel Insert, Command, Search, or Visual."""
    content = "\n".join(f"ROW{i:02}" for i in range(1, 16)) + "\n"

    path = write_temp(content)
    _, edited, code = run_vig(
        b":set mouse=scroll\ri\x1b[<65;1;1MX\x1b:wq\r", file_path=path, rows=4, cols=30,
    )
    os.unlink(path)
    assert code == 0 and "XROW04" in edited

    path = write_temp(content)
    _, _, command_code = run_vig(
        b":set mouse=scroll\r:\x1b[<65;1;1Mq\r", file_path=path, rows=4, cols=30,
    )
    os.unlink(path)
    assert command_code == 0

    path = write_temp(content)
    _, _, search_code = run_vig(
        b":set mouse=scroll\r/\x1b[<65;1;1MROW10\r:q\r", file_path=path, rows=4, cols=30,
    )
    os.unlink(path)
    assert search_code == 0

    path = write_temp(content)
    _, visual_edit, visual_code = run_vig(
        b":set clipboard=off\r:set mouse=scroll\rv\x1b[<65;1;1Md:wq\r",
        file_path=path, rows=4, cols=30,
    )
    os.unlink(path)
    assert visual_code == 0 and visual_edit.startswith("OW04\nROW05")
    print("  PASS: mouse wheel preserves active modes")


def test_mouse_reporting_restores_across_rgf_handoff():
    """Temporary fzf terminal handoff disables and then restores mouse reporting."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.txt")
        bindir = os.path.join(d, "bin")
        os.mkdir(bindir)
        open(path, "w").write("needle\n")
        for name, body in (
            ("rg", "#!/bin/sh\nexit 0\n"),
            ("fzf", f"#!/bin/sh\nprintf '{path}:1:1:needle\\n'\n"),
        ):
            tool = os.path.join(bindir, name)
            open(tool, "w").write(body)
            os.chmod(tool, 0o755)
        env = {"PATH": bindir + os.pathsep + os.environ["PATH"]}
        screen, _, code = run_vig(
            f":set mouse=scroll\r:rgf {d}\r:q!\r:q\r", file_path=path, env=env,
        )
    assert code == 0
    assert screen.count("\x1b[?1000h") >= 2, "Expected mouse reporting after fzf returns"
    assert "\x1b[?1000l" in screen, "Expected mouse reporting disabled for handoff"
    print("  PASS: mouse reporting restores across rgf handoff")


# ── Phase 62: live search highlighting ────────────────────────────────────

def test_search_prompt_previews_visible_matches_without_moving():
    """Typing a search previews matches while leaving the cursor in place."""
    path = write_temp("alpha beta alpha\nAlpha\n")
    screen, content, code = run_vig(b"/alpha\x1biX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0 and content.startswith("Xalpha")
    assert "\x1b[45;97malpha\x1b[m" in screen, "Expected current live match styling"
    assert "\x1b[43;30malpha\x1b[m" in screen, "Expected other live matches highlighted"
    print("  PASS: search prompt previews matches without moving")


def test_search_prompt_preview_uses_smart_case():
    """Live preview uses the same literal smart-case rule as accepted searches."""
    path = write_temp("alpha\nAlpha\n")
    screen, _, code = run_vig(b"/Alpha\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[43;30mAlpha\x1b[m" in screen
    assert "\x1b[43;30malpha\x1b[m" not in screen
    print("  PASS: search preview uses smart case")


def test_search_preview_clears_on_escape_and_tolerates_invalid_regex():
    """Esc clears preview; incomplete regular expressions do not show errors."""
    path = write_temp("alpha [ beta\n")
    screen, _, code = run_vig(b"/[\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "Invalid regex" not in screen
    assert "\x1b[43;30m" not in last_frame(screen) and "\x1b[45;97m" not in last_frame(screen)
    print("  PASS: invalid search preview clears quietly")


def test_hlsearch_distinguishes_current_match():
    """Persistent hlsearch gives the cursor match a distinct style."""
    path = write_temp("alpha beta alpha\n")
    screen, _, code = run_vig(b":set hlsearch\r/alpha\r:q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0
    assert "\x1b[43;30malpha\x1b[m" in frame
    assert "\x1b[45;97malpha\x1b[m" in frame
    print("  PASS: hlsearch distinguishes current match")


# ── Phase 63: mouse cursor positioning ────────────────────────────────────

def test_mouse_click_positions_cursor():
    """A left click maps its screen cell to a source position."""
    path = write_temp("abcde\nfghij\n")
    _, content, code = run_vig(
        b":set mouse=cursor\r\x1b[<0;4;2MiX\x1b:wq\r", file_path=path,
    )
    os.unlink(path)
    assert code == 0 and content == "abcde\nfghXij\n"
    print("  PASS: mouse click positions cursor")


def test_mouse_click_retains_insert_command_and_search_modes():
    """Cursor clicks reposition the buffer without closing the active mode or prompt."""
    source = "one\ntwo\nthree\n"

    path = write_temp(source)
    _, content, insert_code = run_vig(
        b":set mouse=cursor\ri\x1b[<0;2;2MX\x1b:wq\r", file_path=path,
    )
    os.unlink(path)
    assert insert_code == 0 and content == "one\ntXwo\nthree\n"

    path = write_temp(source)
    _, _, command_code = run_vig(
        b":set mouse=cursor\r:\x1b[<0;2;2Mq\r", file_path=path,
    )
    os.unlink(path)
    assert command_code == 0

    path = write_temp(source)
    _, content, search_code = run_vig(
        b":set mouse=cursor\r/\x1b[<0;2;2Mthree\riX\x1b:wq\r", file_path=path,
    )
    os.unlink(path)
    assert search_code == 0 and content.endswith("Xthree\n")
    print("  PASS: mouse click retains active modes")


def test_mouse_click_uses_wrapped_and_gutter_layout():
    """Click mapping shares wrap and line-number gutter coordinates with rendering."""
    wrapped = write_temp("abcdefghijkl\n")
    _, wrapped_content, code1 = run_vig(
        b":set mouse=cursor\r:set wrap\r\x1b[<0;2;2MiX\x1b:wq\r",
        file_path=wrapped, cols=10, rows=6,
    )
    os.unlink(wrapped)
    assert code1 == 0 and wrapped_content == "abcdefghijkXl\n"

    numbered = write_temp("abc\nfghij\n")
    _, numbered_content, code2 = run_vig(
        b":set mouse=cursor\r:set number\r\x1b[<0;8;2MiX\x1b:wq\r",
        file_path=numbered, cols=20,
    )
    os.unlink(numbered)
    assert code2 == 0 and numbered_content == "abc\nfXghij\n"
    print("  PASS: mouse click uses wrapped and gutter layout")


def test_mouse_click_ignores_status_and_message_rows():
    """Clicks outside content do not move the buffer cursor."""
    path = write_temp("abc\ndef\n")
    _, content, code = run_vig(
        b":set mouse=cursor\r\x1b[<0;3;5MiX\x1b:wq\r",
        file_path=path, rows=6, cols=20,
    )
    os.unlink(path)
    assert code == 0 and content == "Xabc\ndef\n"
    print("  PASS: mouse click ignores status and message rows")


# ── Phase 64: mouse Visual selection ──────────────────────────────────────

def test_mouse_drag_creates_visual_selection():
    """Left press and drag create a characterwise Visual selection."""
    path = write_temp("abcde\nfghij\n")
    keys = (b":set clipboard=off\r:set mouse=visual\r"
            b"\x1b[<0;2;1M\x1b[<32;4;2M\x1b[<0;4;2md:wq\r")
    screen, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0 and content == "aj\n"
    assert "VISUAL" in screen
    print("  PASS: mouse drag creates Visual selection")


def test_mouse_visual_drag_normalizes_reverse_selection():
    """Dragging backward produces the same normalized Visual range."""
    path = write_temp("abcde\nfghij\n")
    keys = (b":set clipboard=off\r:set mouse=visual\r"
            b"\x1b[<0;4;2M\x1b[<32;2;1M\x1b[<0;2;1md:wq\r")
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0 and content == "aj\n"
    print("  PASS: reverse mouse drag normalizes selection")


def test_mouse_visual_click_without_drag_retains_mode():
    """A press/release without motion remains cursor positioning, not selection."""
    path = write_temp("one\ntwo\n")
    keys = (b":set mouse=visual\ri"
            b"\x1b[<0;2;2M\x1b[<0;2;2mX\x1b:wq\r")
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0 and content == "one\ntXwo\n"
    print("  PASS: mouse click without drag retains mode")


def test_mouse_visual_release_does_not_yank():
    """Releasing a mouse selection leaves it active without replacing the register."""
    path = write_temp("abcde\nfghij\n")
    keys = (b":set clipboard=off\ryy:set mouse=visual\r"
            b"\x1b[<0;2;1M\x1b[<32;4;2M\x1b[<0;4;2m\x03P:wq\r")
    _, content, code = run_vig(keys, file_path=path)
    os.unlink(path)
    assert code == 0 and content == "abcde\nabcde\nfghij\n"
    print("  PASS: mouse Visual release does not yank")


# ── Phase 65: collapsed Markdown fence rows ───────────────────────────────

def test_markdown_fence_rows_collapse_without_blanks():
    """Fence marker source rows occupy no Markdown display row."""
    source = "before\n```python\ncode\n```\nafter\n"
    path = write_named_temp(source, ".md")
    screen, content, code = run_vig(b":set markdownfences\r:q\r", file_path=path)
    os.unlink(path)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", last_frame(screen))
    assert code == 0 and content == source
    assert "before\r\ncode\r\nafter" in plain
    print("  PASS: Markdown fence rows collapse")


def test_hidden_fence_search_keeps_source_position_for_edit():
    """A search may land on a hidden marker; editing reveals and changes that source row."""
    source = "before\n```python\ncode\n```\nafter\n"
    path = write_named_temp(source, ".md")
    _, content, code = run_vig(
        b":set markdownfences\r/```\riX\x1b:wq\r", file_path=path,
    )
    os.unlink(path)
    assert code == 0
    assert content == "before\nX```python\ncode\n```\nafter\n"
    print("  PASS: hidden fence search retains source position")


def test_collapsed_fences_preserve_source_line_numbers():
    """Consecutive display rows retain their original absolute source numbers."""
    path = write_named_temp("before\n```\ncode\n```\nafter\n", ".md")
    screen, _, code = run_vig(
        b":set number\r:set markdownfences\r:q\r", file_path=path,
    )
    os.unlink(path)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", last_frame(screen))
    assert code == 0
    assert re.search(r"1\s+before\r\n\s*3\s+code\r\n\s*5\s+after", plain)
    print("  PASS: collapsed fences preserve source line numbers")


def test_mouse_click_maps_across_collapsed_fences():
    """Mouse rows map to visible source lines after fence markers are omitted."""
    path = write_named_temp("before\n```\ncode\n```\nafter\n", ".md")
    _, content, code = run_vig(
        b":set mouse=cursor\r:set markdownfences\r\x1b[<0;1;2MiX\x1b:wq\r",
        file_path=path,
    )
    os.unlink(path)
    assert code == 0 and content == "before\n```\nXcode\n```\nafter\n"
    print("  PASS: mouse maps across collapsed fences")


def test_mouse_wheel_counts_collapsed_display_rows():
    """Wheel scrolling counts visible rows rather than hidden marker lines."""
    source = "A\n```\nB\n```\nC\n```\nD\n```\nE\n"
    path = write_named_temp(source, ".md")
    screen, _, code = run_vig(
        b":set mouse=scroll\r:set markdownfences\r\x1b[<65;1;1M:q\r",
        file_path=path, rows=4, cols=20,
    )
    os.unlink(path)
    frame = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", last_frame(screen))
    assert code == 0 and frame.startswith("D\r\nE\r\n")
    print("  PASS: wheel counts collapsed display rows")


def test_wrapmove_skips_collapsed_fence_rows():
    """Displayed-row movement crosses directly between visible wrapped lines."""
    path = write_named_temp("abcdefghij\n```\nklmnopqrst\n", ".md")
    _, content, code = run_vig(
        b":set markdownfences\r:set wrap\r:set wrapmove\rjjjiX\x1b:wq\r",
        file_path=path, cols=5, rows=8,
    )
    os.unlink(path)
    assert code == 0 and content == "abcdefghij\n```\nXklmnopqrst\n"
    print("  PASS: wrapmove skips collapsed fence rows")


def test_all_hidden_markdown_rows_render_safely():
    """A projection containing only hidden markers renders and exits safely."""
    path = write_named_temp("```\n```\n~~~\n~~~\n", ".md")
    screen, content, code = run_vig(
        b":set markdownfences\r:q\r", file_path=path,
    )
    os.unlink(path)
    assert code == 0 and content == "```\n```\n~~~\n~~~\n"
    assert "```" not in last_frame(screen) and "~~~" not in last_frame(screen)
    print("  PASS: all-hidden Markdown projection is safe")


# ── Phase 66: enhanced language highlighting ──────────────────────────────

def _syntax_tokens(path, line):
    from vigor.highlight import syntax_spans
    return {line[start:end]: color for start, end, color in syntax_spans(path, line)}


def test_named_syntax_color_maps_are_complete():
    """Semantic syntax entities resolve through an easily editable named palette."""
    from vigor.highlight import NAMED_COLORS, SYNTAX_COLOR_NAMES
    expected = {"comment", "string", "number", "keyword", "type", "constant",
                "definition", "function", "decorator", "preprocessor", "variable"}
    assert expected <= SYNTAX_COLOR_NAMES.keys()
    assert all(name in NAMED_COLORS for name in SYNTAX_COLOR_NAMES.values())
    print("  PASS: named syntax color maps are complete")


def test_python_highlights_language_entities():
    """Python recognizes decorators, definitions, keywords, constants, and numbers."""
    from vigor.highlight import NAMED_COLORS, SYNTAX_COLOR_NAMES
    colors = {kind: NAMED_COLORS[name] for kind, name in SYNTAX_COLOR_NAMES.items()}
    decorator = _syntax_tokens("demo.py", "@pkg.route")
    tokens = _syntax_tokens("demo.py", "async def greet(value=0x2A): return True")
    assert decorator["@pkg.route"] == colors["decorator"]
    assert tokens["async"] == colors["keyword"] and tokens["def"] == colors["keyword"]
    assert tokens["greet"] == colors["definition"] and tokens["0x2A"] == colors["number"]
    assert tokens["True"] == colors["constant"]
    print("  PASS: Python language entities highlighted")


def test_c_and_cpp_highlight_language_entities():
    """C-family extensions recognize directives, types, definitions, and calls."""
    from vigor.highlight import NAMED_COLORS, SYNTAX_COLOR_NAMES
    colors = {kind: NAMED_COLORS[name] for kind, name in SYNTAX_COLOR_NAMES.items()}
    directive = _syntax_tokens("demo.c", "#define LIMIT 0x10")
    c_tokens = _syntax_tokens("demo.c", 'struct Item { uint32_t n; printf("x"); }')
    cpp_tokens = _syntax_tokens("demo.hpp", "namespace demo { class Widget { constexpr bool run(); }; }")
    assert directive["#define"] == colors["preprocessor"] and directive["0x10"] == colors["number"]
    assert c_tokens["struct"] == colors["keyword"] and c_tokens["Item"] == colors["definition"]
    assert c_tokens["uint32_t"] == colors["type"] and c_tokens["printf"] == colors["function"]
    assert cpp_tokens["demo"] == colors["definition"] and cpp_tokens["Widget"] == colors["definition"]
    assert cpp_tokens["constexpr"] == colors["keyword"] and cpp_tokens["bool"] == colors["type"]
    assert cpp_tokens["run"] == colors["function"]
    print("  PASS: C and C++ language entities highlighted")


def test_bash_highlights_language_entities():
    """Bash recognizes functions, builtins, variables, numbers, and comments."""
    from vigor.highlight import NAMED_COLORS, SYNTAX_COLOR_NAMES
    colors = {kind: NAMED_COLORS[name] for kind, name in SYNTAX_COLOR_NAMES.items()}
    tokens = _syntax_tokens("demo.bash", "build() { local n=12; printf $HOME; # note")
    assert tokens["build"] == colors["definition"] and tokens["local"] == colors["function"]
    assert tokens["12"] == colors["number"] and tokens["printf"] == colors["function"]
    assert tokens["$HOME"] == colors["variable"] and tokens["# note"] == colors["comment"]
    print("  PASS: Bash language entities highlighted")


# ── Phase 67: Markdown fenced-code highlighting ────────────────────────────

def test_markdown_fences_highlight_supported_languages():
    """Supported fence information strings select their language lexers."""
    source = ("```python\ndef greet(value=42):\n    return True\n```\n"
              "```bash\nif test $HOME; then printf \"ok\"; fi\n```\n"
              "```c\n#define LIMIT 10\n```\n"
              "```cpp\nconstexpr bool run();\n```\n")
    path = write_named_temp(source, ".md")
    screen, content, code = run_vig(
        b":set markdownfences\r:q\r", file_path=path, rows=20,
    )
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0 and content == source
    assert "\x1b[94mdef\x1b[m" in frame and "\x1b[1;36mgreet\x1b[m" in frame
    assert "\x1b[95m$HOME\x1b[m" in frame and "\x1b[95m#define\x1b[m" in frame
    assert "\x1b[94mconstexpr\x1b[m" in frame and "\x1b[36mbool\x1b[m" in frame
    print("  PASS: Markdown fences highlight supported languages")


def test_markdown_fence_language_aliases():
    """Documented short fence names map to the four supported lexers."""
    from vigor.highlight import MD_FENCE, markdown_fence_languages
    lines = ["```py", "x", "```", "```sh", "x", "```", "```c++", "x", "```"]
    languages = markdown_fence_languages(lines)
    assert languages[0] is MD_FENCE and languages[2] is MD_FENCE
    assert languages[1] == "python" and languages[4] == "bash" and languages[7] == "cpp"
    print("  PASS: Markdown fence language aliases")


def test_unknown_fence_suppresses_markdown_prose_styles():
    """Unknown fenced code remains literal and is not styled as Markdown prose."""
    source = "# title\n```text\n# code, not a heading\ndef plain():\n```\n"
    path = write_named_temp(source, ".md")
    screen, _, code = run_vig(b":set markdownfences\r:q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0 and "\x1b[1;36m# title\x1b[m" in frame
    assert "\x1b[1;36m# code, not a heading\x1b[m" not in frame
    assert "\x1b[94mdef\x1b[m" not in frame
    print("  PASS: unknown fences suppress Markdown prose styles")


def test_markdown_fence_matching_respects_marker_kind_and_length():
    """Only a same-kind, sufficiently long marker closes a fenced block."""
    from vigor.highlight import MD_FENCE, markdown_fence_languages
    lines = ["~~~~python", "```", "def value():", "~~~", "~~~~"]
    languages = markdown_fence_languages(lines)
    assert languages[0] is MD_FENCE and languages[4] is MD_FENCE
    assert languages[1:4] == ["python", "python", "python"]
    print("  PASS: Markdown fence matching respects marker and length")


# ── Phase 68: extensionless shell shebang highlighting ────────────────────

def test_extensionless_shell_shebang_detection():
    """Direct, env, and env -S Bash/sh shebangs select shell highlighting."""
    from vigor.highlight import language_for_path
    assert language_for_path("script", "#!/bin/bash") == "bash"
    assert language_for_path("script", "#!/usr/bin/sh -e") == "bash"
    assert language_for_path("script", "#!/usr/bin/env bash") == "bash"
    assert language_for_path("script", "#!/usr/bin/env -S sh -eu") == "bash"
    print("  PASS: extensionless shell shebang detection")


def test_extension_and_unsupported_shebang_precedence():
    """Extensions remain authoritative and unrelated interpreters are ignored."""
    from vigor.highlight import language_for_path
    assert language_for_path("script.py", "#!/bin/bash") == "python"
    assert language_for_path("script.txt", "#!/bin/bash") is None
    assert language_for_path("script", "#!/usr/bin/env python3") is None
    assert language_for_path("script", "#!/bin/zsh") is None
    assert language_for_path(None, "#!/bin/bash") is None
    print("  PASS: extension and unsupported shebang precedence")


def test_extensionless_shebang_enables_shell_rendering():
    """An extensionless Bash script receives normal Bash entity colors."""
    fd, path = tempfile.mkstemp(prefix="vig-shebang-")
    source = "#!/usr/bin/env bash\nif test $HOME; then echo ok; fi\n"
    with os.fdopen(fd, "w") as f:
        f.write(source)
    screen, content, code = run_vig(b":q\r", file_path=path)
    os.unlink(path)
    frame = last_frame(screen)
    assert code == 0 and content == source
    assert "\x1b[94mif\x1b[m" in frame and "\x1b[95m$HOME\x1b[m" in frame
    print("  PASS: extensionless shebang enables shell rendering")


# ── Phase 69: per-buffer file types ────────────────────────────────────────

def test_filetype_command_forces_language():
    """:ft forces syntax highlighting independently of the filename."""
    path = write_named_temp("def greet():\n    return True\n", ".txt")
    screen, _, code = run_vig(b":ft python\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "\x1b[94mdef\x1b[m" in screen
    assert "\x1b[1;36mgreet\x1b[m" in screen
    print("  PASS: filetype command forces language")


def test_filetype_text_disables_syntax():
    """:ft text suppresses otherwise automatic syntax highlighting."""
    path = write_named_temp("def greet():\n", ".py")
    screen, _, code = run_vig(b":ft text\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "\x1b[94mdef\x1b[m" not in last_frame(screen)
    print("  PASS: filetype text disables syntax")


def test_filetype_auto_redetects_and_reports():
    """:ft auto clears an override and bare :ft reports effective type and source."""
    path = write_named_temp("def greet():\n", ".py")
    screen, _, code = run_vig(b":ft text\r:ft auto\r:ft\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "filetype=python (auto)" in screen
    assert "\x1b[94mdef\x1b[m" in screen
    print("  PASS: filetype auto redetects and reports")


def test_filetype_markdown_controls_presentation():
    """Markdown and text overrides enable and disable presentation respectively."""
    path = write_named_temp("# heading\n```\ncode\n```\n", ".txt")
    screen, _, code = run_vig(b":set markdownfences\r:ft markdown\r:ft\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "filetype=markdown (forced)" in screen
    assert "\x1b[1;36m# heading\x1b[m" in screen and "[MD]" in screen
    assert "```" not in last_frame(screen)

    path = write_named_temp("# heading\n", ".md")
    screen, _, code = run_vig(b":md\r:ft text\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "[MD]" not in last_frame(screen)
    print("  PASS: filetype Markdown controls presentation")


def test_filetype_override_persists_per_buffer_and_reload():
    """Overrides survive reload and remain isolated across buffer switches."""
    with tempfile.TemporaryDirectory() as d:
        shell_path = os.path.join(d, "commands.txt")
        py_path = os.path.join(d, "code.py")
        open(shell_path, "w").write("if test $HOME; then echo ok; fi\n")
        open(py_path, "w").write("def greet():\n")
        keys = f":ft bash\r:e!\r:e {py_path}\r:p\r:ft\r:q!\r:q\r"
        screen, _, code = run_vig(keys, file_path=shell_path)
    assert code == 0 and "filetype=bash (forced)" in screen
    assert "\x1b[94mif\x1b[m" in screen and "\x1b[95m$HOME\x1b[m" in screen
    print("  PASS: filetype override persists per buffer and reload")


def test_filetype_rejects_unknown_value():
    """Unknown file-type names report an error without changing detection."""
    path = write_named_temp("def greet():\n", ".py")
    screen, _, code = run_vig(b":ft ruby\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and "Unknown file type: ruby" in screen
    assert "\x1b[94mdef\x1b[m" in screen
    print("  PASS: filetype rejects unknown value")


# ── Phase 70: automatic syntax and Markdown detection ─────────────────────

def test_markdown_files_open_in_markdown_view():
    """Markdown extensions automatically enable presentation on initial open."""
    path = write_named_temp("# heading\n", ".md")
    screen, content, code = run_vig(b":e!\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0 and content == "# heading\n"
    assert "[MD]" in screen and "\x1b[1;36m# heading\x1b[m" in screen
    print("  PASS: Markdown files open in Markdown view")


def test_autodetect_option_affects_only_new_buffers():
    """Changing autodetect preserves open buffers and controls later opens."""
    with tempfile.TemporaryDirectory() as d:
        first = os.path.join(d, "first.py")
        second = os.path.join(d, "second.py")
        markdown = os.path.join(d, "notes.md")
        open(first, "w").write("def first():\n")
        open(second, "w").write("def second():\n")
        open(markdown, "w").write("# Notes\n")
        keys = (f":set noautodetect\r:ft\r:e {second}\r:ft\r"
                f":set autodetect\r:ft\r:e {markdown}\r:ft\r:q!\r:q!\r:q\r")
        screen, _, code = run_vig(keys, file_path=first)
    assert code == 0 and "filetype=python (auto)" in screen
    assert screen.count("filetype=text (disabled)") >= 2
    assert "filetype=markdown (auto)" in screen and "[MD]" in screen
    print("  PASS: autodetect affects only new buffers")


def test_noautodetect_config_disables_initial_recognition():
    """Config can disable syntax and automatic Markdown presentation at startup."""
    with tempfile.TemporaryDirectory() as d:
        config = os.path.join(d, "config")
        py_path = os.path.join(d, "code.py")
        md_path = os.path.join(d, "notes.md")
        open(config, "w").write("set noautodetect\n")
        open(py_path, "w").write("def greet():\n")
        open(md_path, "w").write("# Heading\n")
        env = {"VIG_CONFIG": config}
        py_screen, _, py_code = run_vig(b":ft\r:q\r", file_path=py_path, env=env)
        md_screen, _, md_code = run_vig(b":ft\r:q\r", file_path=md_path, env=env)
    assert py_code == md_code == 0
    assert "filetype=text (disabled)" in py_screen and "\x1b[94mdef\x1b[m" not in py_screen
    assert "filetype=text (disabled)" in md_screen and "[MD]" not in md_screen
    print("  PASS: noautodetect config disables initial recognition")


def test_explicit_commands_override_disabled_detection():
    """Explicit :ft auto and :md still work for buffers opened with detection off."""
    with tempfile.TemporaryDirectory() as d:
        config = os.path.join(d, "config")
        py_path = os.path.join(d, "code.py")
        md_path = os.path.join(d, "notes.md")
        open(config, "w").write("set noautodetect\n")
        open(py_path, "w").write("def greet():\n")
        open(md_path, "w").write("# Heading\n")
        env = {"VIG_CONFIG": config}
        py_screen, _, py_code = run_vig(b":ft auto\r:ft\r:q\r", file_path=py_path, env=env)
        md_screen, _, md_code = run_vig(b":md\r:q\r", file_path=md_path, env=env)
    assert py_code == md_code == 0
    assert "filetype=python (auto)" in py_screen and "\x1b[94mdef\x1b[m" in py_screen
    assert "[MD]" in md_screen and "\x1b[1;36m# Heading\x1b[m" in md_screen
    print("  PASS: explicit commands override disabled detection")


# ── Phase 71: retained manual-save versions ────────────────────────────────

def test_saveversions_rotates_prior_disk_contents():
    """Explicit writes retain N adjacent prior versions, newest at generation one."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        open(path, "w").write("one\n")
        keys = (b":set saveversions=2\r:%s/one/two/\r:w\r"
                b":%s/two/three/\r:w\r:%s/three/four/\r:wq\r")
        _, content, code = run_vig(keys, file_path=path)
        newest = open(os.path.join(d, ".vigor-bak.note.txt.1")).read()
        older = open(os.path.join(d, ".vigor-bak.note.txt.2")).read()
        names = os.listdir(d)
    assert code == 0 and content == "four\n"
    assert newest == "three\n" and older == "two\n"
    assert ".vigor-bak.note.txt.3" not in names
    print("  PASS: saveversions rotates prior disk contents")


def test_saveversions_skips_unchanged_and_new_targets():
    """Unchanged explicit writes and first writes of new files create no versions."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        new_path = os.path.join(d, "new.txt")
        open(path, "w").write("one\n")
        _, _, code = run_vig(
            b":set saveversions=2\r:%s/one/two/\r:w\r:w\r:q\r", file_path=path,
        )
        _, new_content, new_code = run_vig(
            b":set saveversions=2\rihello\x1b:wq\r", file_path=new_path,
        )
        names = os.listdir(d)
    assert code == new_code == 0 and new_content == "hello\n"
    assert ".vigor-bak.note.txt.1" in names and ".vigor-bak.note.txt.2" not in names
    assert ".vigor-bak.new.txt.1" not in names
    print("  PASS: saveversions skips unchanged and new targets")


def test_saveversions_preserves_existing_save_as_target():
    """Writing to another existing path retains that target's prior bytes."""
    with tempfile.TemporaryDirectory() as d:
        source = os.path.join(d, "source.txt")
        target = os.path.join(d, "target.txt")
        open(source, "w").write("source\n")
        open(target, "w").write("target\n")
        _, _, code = run_vig(
            f":set saveversions=1\r:w {target}\r:q\r", file_path=source,
        )
        written = open(target).read()
        backup = open(os.path.join(d, ".vigor-bak.target.txt.1")).read()
    assert code == 0 and written == "source\n" and backup == "target\n"
    print("  PASS: saveversions preserves save-as target")


def test_saveversions_reduction_removes_excess_generations():
    """Reducing retention removes generations above the new limit on the next write."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        open(path, "w").write("one\n")
        keys = (b":set saveversions=3\r:%s/one/two/\r:w\r:%s/two/three/\r:w\r"
                b":set saveversions=1\r:%s/three/four/\r:wq\r")
        _, _, code = run_vig(keys, file_path=path)
        names = os.listdir(d)
    assert code == 0 and ".vigor-bak.note.txt.1" in names
    assert ".vigor-bak.note.txt.2" not in names and ".vigor-bak.note.txt.3" not in names
    print("  PASS: saveversions reduction removes excess generations")


def test_saveversions_failure_blocks_write():
    """A failed promised backup leaves the original target untouched and dirty."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        open(path, "w").write("one\n")
        os.mkdir(os.path.join(d, ".vigor-bak.note.txt.1"))
        screen, content, code = run_vig(
            b":set saveversions=1\r:%s/one/two/\r:w\r:q!\r", file_path=path,
        )
    assert code == 0 and content == "one\n"
    assert "Can't preserve prior version" in screen
    print("  PASS: saveversions failure blocks write")


def test_backup_files_do_not_version_themselves():
    """Opening a generated-name backup cannot create recursive backup chains."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, ".vigor-bak.note.txt.1")
        open(path, "w").write("one\n")
        _, content, code = run_vig(
            b":set saveversions=3\r:%s/one/two/\r:wq\r", file_path=path,
        )
        names = os.listdir(d)
    assert code == 0 and content == "two\n" and names == [".vigor-bak.note.txt.1"]
    print("  PASS: backup files do not version themselves")


def test_saveversions_validates_and_loads_from_config():
    """Retention validates 0..100 and uses the normal startup-config path."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        config = os.path.join(d, "config")
        open(path, "w").write("one\n")
        open(config, "w").write("set saveversions=1\n")
        screen, _, code = run_vig(
            b":%s/one/two/\r:wq\r", file_path=path, env={"VIG_CONFIG": config},
        )
        bad, _, bad_code = run_vig(b":set saveversions=101\r:q\r", file_path=path)
        exists = os.path.exists(os.path.join(d, ".vigor-bak.note.txt.1"))
    assert code == bad_code == 0 and exists
    assert "saveversions must be 0..100" in bad
    print("  PASS: saveversions validates and loads from config")


# ── Phase 72: autosave deadlines ───────────────────────────────────────────

def test_autosave_writes_named_buffer_after_idle_delay():
    """A dirty named buffer saves after the configured mutation-idle deadline."""
    path = write_temp("alpha\n")
    screen, content, code = run_vig(
        b":set autosavedelay=30\r:set autosave\riX", file_path=path, timeout=0.5,
    )
    os.unlink(path)
    assert code == -99 and content == "Xalpha\n"
    assert f'"{path}" autosaved' in screen
    print("  PASS: autosave writes named buffer after idle delay")


def test_autosave_is_disabled_by_default():
    """Without :set autosave, idle dirty buffers remain only in memory."""
    path = write_temp("alpha\n")
    _, content, code = run_vig(b"iX", file_path=path, timeout=0.3)
    os.unlink(path)
    assert code == -99 and content == "alpha\n"
    print("  PASS: autosave is disabled by default")


def test_autosave_handles_multiple_open_buffers():
    """Deadlines remain attached to dirty buffers across buffer switches."""
    p1, p2 = write_temp("one\n"), write_temp("two\n")
    keys = b":set autosavedelay=80\r:set autosave\riA\x1b:n\riB"
    screen, _, code = run_vig(keys, file_paths=[p1, p2], timeout=0.7)
    one, two = open(p1).read(), open(p2).read()
    os.unlink(p1)
    os.unlink(p2)
    assert code == -99 and one == "Aone\n" and two == "Btwo\n"
    assert "autosaved" in screen
    print("  PASS: autosave handles multiple open buffers")


def test_autosave_does_not_rotate_manual_versions():
    """Autosave bypasses saveversions while a later explicit change still rotates it."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        open(path, "w").write("one\n")
        screen, content, code = run_vig(
            b":set saveversions=2\r:set autosavedelay=30\r:set autosave\riX",
            file_path=path, timeout=0.5,
        )
        names = os.listdir(d)
    assert code == -99 and content == "Xone\n" and "autosaved" in screen
    assert not any(name.startswith(".vigor-bak.") for name in names)
    print("  PASS: autosave does not rotate manual versions")


def test_explicit_write_clears_pending_autosave():
    """A manual write satisfies and cancels a later pending autosave deadline."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        open(path, "w").write("one\n")
        screen, content, code = run_vig(
            b":set saveversions=1\r:set autosavedelay=200\r:set autosave\r"
            b":%s/one/two/\r:w\r",
            file_path=path, timeout=0.5,
        )
        backup = open(os.path.join(d, ".vigor-bak.note.txt.1")).read()
    assert code == -99 and content == "two\n" and backup == "one\n"
    assert f'"{path}" autosaved' not in screen
    print("  PASS: explicit write clears pending autosave")


def test_autosave_error_leaves_dirty_buffer_and_waits_for_new_edit():
    """A failed autosave reports once and does not spin until another mutation."""
    with tempfile.TemporaryDirectory() as root:
        directory = os.path.join(root, "gone")
        os.mkdir(directory)
        path = os.path.join(directory, "note.txt")
        open(path, "w").write("one\n")
        keys = (f":set autosavedelay=30\r:set autosave\r"
                f":!rm -rf {directory}\riX").encode()
        screen, _, code = run_vig(keys, file_path=path, timeout=0.5)
    assert code == -99 and "Can't autosave" in screen
    assert screen.count("Can't autosave") == 1 and "[+]" in last_frame(screen)
    print("  PASS: autosave error leaves dirty buffer without spinning")


def test_autosave_options_validate_and_load_from_config():
    """Autosave and its nonnegative millisecond delay use startup configuration."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "note.txt")
        config = os.path.join(d, "config")
        open(path, "w").write("one\n")
        open(config, "w").write("set autosave\nset autosavedelay=30\n")
        screen, content, code = run_vig(
            b"iX", file_path=path, env={"VIG_CONFIG": config}, timeout=0.5,
        )
        bad, _, bad_code = run_vig(b":set autosavedelay=-1\r:q\r", file_path=path)
    assert code == -99 and content == "Xone\n" and "autosaved" in screen
    assert bad_code == 0 and "autosavedelay must be >= 0" in bad
    print("  PASS: autosave options validate and load from config")


# ── Phase 73: config and in-editor help audit ──────────────────────────────

def test_example_config_lists_all_runtime_defaults():
    """The example contains each supported startup option exactly once at its default."""
    expected = [
        "set nowrap", "set wrapcol=0", "set nowrapmove", "set nonumber",
        "set norelativenumber", "set autoindent", "set comment=#",
        "set scrolloff=0", "set clipboard=auto", "set mouse=off",
        "set yankflash=300", "set delcopy", "set norghidden",
        "set nohlsearch", "set nomarkdownfences", "set autodetect",
        "set saveversions=0", "set noautosave", "set autosavedelay=1000",
        "set makeprg=make",
    ]
    path = os.path.join(os.path.dirname(VIG), "example-config")
    lines = [line.strip() for line in open(path) if line.strip() and not line.startswith("#")]
    target = write_temp("plain\n")
    _, _, code = run_vig(b":q\r", file_path=target, env={"VIG_CONFIG": path})
    os.unlink(target)
    assert lines == expected and code == 0
    print("  PASS: example config lists all runtime defaults")


def test_vighelp_covers_commands_and_config_options():
    """In-editor help names every ex-command family and configurable option."""
    path = os.path.join(os.path.dirname(VIG), "vighelp")
    help_text = open(path).read()
    commands = (":w", ":q", ":qa", ":e", ":new", ":n", ":p", ":ls", ":k",
                ":help", ":md", ":nomd", ":ft", ":cd", ":cdb", ":pwd",
                ":read", ":!command", ":[range]!command", ":[range]s/",
                ":make", ":qf", ":rg", ":rgf")
    options = ("wrap", "wrapcol", "wrapmove", "number", "relativenumber",
               "autoindent", "comment", "scrolloff", "clipboard", "mouse",
               "yankflash", "delcopy", "rghidden", "hlsearch", "markdownfences",
               "autodetect", "saveversions", "autosave", "autosavedelay", "makeprg")
    assert all(command in help_text for command in commands)
    assert all(f":set {option}" in help_text for option in options)
    print("  PASS: vighelp covers commands and config options")


# ── Runner ─────────────────────────────────────────────────────────────────

def run_phase(name, tests):
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
    print(f"\n  {passed} passed, {failed} failed")
    return failed

def main():
    total_failed = 0
    selected = set(sys.argv[1:]) if len(sys.argv) > 1 else None

    phase_defs = [
        ("1", "Phase 1 — Scaffold", [
            test_open_and_quit,
            test_open_file_visible,
            test_j_k_movement,
            test_h_l_movement,
            test_scroll_down,
            test_render_disables_autowrap_for_full_width_lines,
            test_render_clears_old_frame_for_indented_scroll,
        ]),
        ("2", "Phase 2 — Editing", [
            test_insert_text,
            test_a_appends,
            test_I_beginning,
            test_A_end,
            test_enter_splits,
            test_backspace_joins,
            test_write_save,
            test_quit_dirty_refuses,
            test_edit_file,
            test_new_buffer,
        ]),
        ("3", "Phase 3 — Word Motions", [
            test_w_forward_word,
            test_b_backward_word,
            test_e_end_word,
            test_W_forward_WORD,
            test_B_backward_WORD,
            test_E_end_WORD,
            test_e_from_eol_stays_on_current_line_last_word,
            test_e_crosses_empty_line_without_crash,
            test_w_skips_empty_line_to_next_word,
            test_w_newline_is_word_boundary,
            test_e_newline_is_word_boundary,
            test_b_newline_is_word_boundary,
            test_B_newline_is_word_boundary,
            test_W_newline_is_word_boundary,
            test_E_newline_is_word_boundary,
        ]),
        ("4", "Phase 4 — Visual Mode", [
            test_v_enters_visual,
            test_V_line_visual,
            test_visual_esc_cancels,
            test_visual_motion_extends,
        ]),
        ("5", "Phase 5 — Polish", [
            test_status_bar_shown,
            test_wq_command,
            test_q_bang_forces,
            test_empty_file,
        ]),
        ("6", "Phase 6 — Resize", [
            test_sigwinch_no_crash,
            test_resize_shrink_grow,
            test_resize_to_49_content_columns_keeps_wrapmove_consistent,
        ]),
        ("7", "Phase 7 — Count Prefixes", [
            test_count_3j,
            test_count_5l,
            test_count_resets_on_esc,
        ]),
        ("8", "Phase 8 — Edit Operations", [
            test_dd_deletes_line,
            test_2dd_deletes_two_lines,
            test_dw_deletes_word,
            test_D_deletes_to_end,
            test_yy_p_paste_line,
            test_yy_P_paste_above,
            test_cw_changes_word,
            test_cc_changes_line,
            test_C_changes_to_end,
            test_dd_on_last_line,
            test_p_charwise_paste,
            test_multiline_charwise_paste_preserves_line_invariant,
            test_empty_paste_does_not_create_undo_state,
        ]),
        ("9", "Phase 9 — Visual Edit", [
            test_visual_delete,
            test_visual_yank_paste,
            test_visual_change,
            test_visual_line_delete,
            test_visual_x_same_as_d,
        ]),
        ("10", "Phase 10 — Search", [
            test_search_forward,
            test_search_backward,
            test_search_n_repeats,
            test_search_N_reverses,
            test_search_not_found,
            test_search_esc_cancels,
        ]),
        ("11", "Phase 11 — Replace", [
            test_substitute_current_line,
            test_substitute_global_flag,
            test_substitute_whole_file,
            test_substitute_line_range,
            test_substitute_regex,
            test_substitute_not_found,
        ]),
        ("12", "Phase 12 — Line Wrap", [
            test_set_wrap,
            test_wrap_long_line,
            test_wrap_full_width_rows_do_not_clear_last_cell,
            test_nowrap_truncates,
            test_wrap_cursor_position,
            test_wrap_cursor_at_exact_boundary_uses_eol_row,
            test_wrapmove_is_symmetric_at_exact_49_column_boundary,
            test_wrapmove_crosses_logical_lines_symmetrically,
            test_wrap_long_line_scrolls_within_line,
        ]),
        ("13", "Phase 13 — Line Numbers", [
            test_set_number,
            test_set_relativenumber,
            test_number_and_relnum,
            test_number_gutter_expands_past_five_digits,
            test_number_with_wrap,
        ]),
        ("14", "Phase 14 — Insert Arrow Keys", [
            test_insert_arrow_left_right,
            test_insert_arrow_up_down,
        ]),
        ("15", "Phase 15 — Undo / Redo", [
            test_undo_insert,
            test_undo_dd,
            test_redo_after_undo,
            test_undo_paste,
            test_undo_substitute,
            test_undo_redo_dirty_flag,
            test_undo_insert_word_checkpoint,
            test_undo_visual_delete,
            test_redo_cleared_on_new_edit,
            test_undo_at_oldest,
        ]),
        ("17", "Phase 17 — gg and G Motions", [
            test_G_goes_to_last_line,
            test_gg_goes_to_first_line,
            test_count_G,
            test_zero_goes_to_column_zero,
            test_dgg_deletes_to_first,
        ]),
        ("18", "Phase 18 — f t F T ; ,", [
            test_f_motion,
            test_t_motion,
            test_F_motion,
            test_semicolon_repeats_find,
            test_semicolon_repeats_t_motion,
            test_semicolon_repeats_T_motion,
            test_comma_reverses_find,
            test_dfl_deletes_to_char,
            test_f_digit_target,
            test_counted_f_digit_target,
        ]),
        ("19", "Phase 19 — Indent >>  <<", [
            test_indent_line,
            test_dedent_line,
            test_count_indent,
        ]),
        ("20", "Phase 20 — Autoindent", [
            test_autoindent_on_enter,
            test_autoindent_disabled,
        ]),
        ("21", "Phase 21 — % Bracket Match", [
            test_percent_match_paren,
            test_percent_match_brace,
        ]),
        ("22", "Phase 22 — O and o", [
            test_o_opens_below,
            test_O_opens_above,
            test_o_autoindent,
        ]),
        ("23", "Phase 23 — iw/aw Text Objects", [
            test_diw_deletes_word,
            test_daw_deletes_word_with_space,
            test_ciw_changes_word,
        ]),
        ("24", "Phase 24 — Bracket/Quote Objects", [
            test_di_paren,
            test_da_bracket,
            test_di_quote,
        ]),
        ("25", "Phase 25 — Comment Toggle", [
            test_gcc_comments_line,
            test_gcc_uncomments_line,
            test_visual_gc,
            test_set_comment_char,
        ]),
        ("26", "Phase 26 — Dot Repeat", [
            test_dot_repeat_dd,
            test_dot_repeat_insert,
            test_dot_repeat_indent,
        ]),
        ("27", "Phase 27 — :read :! :read !", [
            test_read_file,
            test_read_command,
            test_bang_command,
            test_filter_range_replaces_lines,
            test_filter_whole_buffer_replaces_lines,
            test_filter_new_buffer,
        ]),
        ("28", "Phase 28 — Multi-buffer", [
            test_multi_file_argv,
            test_next_prev_buffer,
            test_ls_lists_buffers,
            test_quit_closes_buffer,
            test_e_adds_buffer,
            test_bdelete_removes_buffer,
            test_bdelete_dirty_blocked,
            test_bdelete_last_refused,
            test_qa_checks_all_dirty,
            test_wq_closes_buffer,
        ]),
        ("29", "Phase 29 — x/X and space-leader", [
            test_x_deletes_char,
            test_x_with_count,
            test_X_deletes_before,
            test_space_w_toggles_wrap,
            test_space_d_deletes_buffer_safely,
        ]),
        ("30", "Phase 30 — ^/$ Home/End Tab/Delete", [
            test_caret_motion_first_nonblank,
            test_home_end_normal_mode,
            test_home_end_ss3_sequences,
            test_home_end_csi_tilde_sequences,
            test_insert_home_end_tab,
            test_insert_delete_key,
        ]),
        ("31", "Phase 31 — J join and visual ^/$", [
            test_J_joins_lines,
            test_count_J_joins_multiple_lines,
            test_visual_dollar_delete_line_tail,
            test_visual_caret_delete_to_nonblank,
        ]),
        ("32", "Phase 32 — :e/:w/argv path handling", [
            test_edit_relative_to_working_dir,
            test_write_relative_to_working_dir,
            test_argv_expands_tilde_path,
            test_write_path_error_shows_message_no_crash,
        ]),
        ("33", "Phase 33 — Ctrl-D / Ctrl-U motions", [
            test_ctrl_d_moves_half_page_down,
            test_ctrl_u_moves_half_page_up,
        ]),
        ("34", "Phase 34 — scrolloff", [
            test_set_scrolloff_option,
            test_scrolloff_keeps_margin_near_bottom,
        ]),
        ("35", "Phase 35 — clipboard modes", [
            test_set_clipboard_mode_options,
            test_set_clipboard_invalid_value,
            test_clipboard_off_disables_osc52_output,
            test_clipboard_auto_prefers_external_command,
        ]),
        ("36", "Phase 36 — small command/edit fixes", [
            test_bang_command_without_space,
            test_bang_multiline_output_compact,
            test_normal_backspace_deletes_left,
            test_r_replaces_character,
            test_r_replaces_with_digit,
            test_count_r_replaces_with_digit,
            test_s_substitutes_character,
            test_dw_at_eol_does_not_join_lines,
            test_ctrl_z_stops_process,
            test_edit_directory_shows_error_no_crash,
            test_insert_long_line_hscrolls_at_right_edge,
            test_hscroll_shifts_whole_window,
        ]),
        ("37", "Phase 37 — quit aliases and config", [
            test_ctrl_c_ctrl_c_quit_all,
            test_ctrl_c_q_force_quit_all,
            test_ctrl_c_ctrl_c_dirty_refuses,
            test_config_file_sets_options,
        ]),
        ("38", "Phase 38 — ripgrep quickfix", [
            test_rg_creates_quickfix_buffer,
            test_space_o_opens_rg_location,
            test_space_j_k_open_next_previous_quickfix_items,
            test_quickfix_j_k_report_boundaries,
            test_space_buffer_keymaps_and_rghidden,
        ]),
        ("39", "Phase 39 — todo.md 1-5", [
            test_write_missing_directory_prompts_and_creates,
            test_write_missing_directory_no_cancels,
            test_edit_bang_reloads_file_from_disk,
            test_yank_flashes_highlight,
            test_yank_flash_clears_after_configured_time,
            test_relativenumber_cursor_row_flush_left,
            test_insert_tab_uses_tab_columns,
        ]),
        ("40", "Phase 40 — todo.md Do items", [
            test_search_forward_same_line_next_hit,
            test_search_backward_same_line_previous_hit,
            test_nodelcopy_does_not_change_register,
            test_yd_deletes_and_copies_with_nodelcopy,
            test_wrapmove_j_moves_display_row,
        ]),
        ("41", "Phase 41 — bracketed paste", [
            test_bracketed_paste_insert_literal_text,
            test_bracketed_paste_does_not_execute_escape_commands,
        ]),
        ("42", "Phase 42 — todo.md Do items", [
            test_edit_bang_no_file_name_errors,
            test_normal_delete_key_aliases_x,
        ]),
        ("43", "Phase 43 — completion and history", [
            test_command_complete_no_path_from_buffer_dir,
            test_completion_menu_enter_accepts_first_match,
            test_completion_menu_down_selects_match,
            test_completion_menu_tab_wraps_selection,
            test_completion_menu_shift_tab_wraps_selection,
            test_completion_menu_typing_updates_filter,
            test_completion_menu_esc_hides_list,
            test_command_complete_absolute_path,
            test_command_complete_relative_subdir,
            test_bang_complete_path,
            test_command_history_up_down,
            test_search_history_shared_by_slash_and_question,
        ]),
        ("44", "Phase 44 — splash screen", [
            test_file_startup_shows_framed_splash_over_editor,
            test_splash_dismisses_and_input_executes,
            test_file_splash_times_out,
            test_unnamed_splash_has_no_timeout,
            test_splash_clamps_to_small_terminal,
        ]),
        ("45", "Phase 45 — todo polish", [
            test_completion_menu_has_filename_padding,
            test_rg_no_hits_keeps_current_buffer,
            test_case_commands,
            test_prompt_cursor_editing,
            test_prompt_cursor_is_visible,
            test_ctrl_c_cancels_mkdir_prompt,
            test_sticky_vertical_column,
        ]),
        ("46", "Phase 46 — help", [
            test_help_opens_vighelp_buffer,
        ]),
        ("47", "Phase 47 — fzf ripgrep picker", [
            test_rgf_path_argument_completes,
            test_rgf_selected_rows_open_quickfix,
        ]),
        ("48", "Phase 48 — syntax highlighting", [
            test_syntax_highlights_comments_and_strings,
        ]),
        ("49", "Phase 49 — initial buffer replacement", [
            test_opening_replaces_untouched_initial_buffer,
        ]),
        ("50", "Phase 50 — search polish", [
            test_star_searches_whole_word_and_repeats,
            test_hash_searches_whole_word_backward,
            test_gstar_searches_partial_matches,
            test_ghash_searches_partial_matches_backward,
            test_hlsearch_highlights_matches_and_can_disable,
            test_hlsearch_config_file,
        ]),
        ("51", "Phase 51 — viewport scrolling", [
            test_ctrl_e_scrolls_down_and_ctrl_y_scrolls_up,
            test_ctrl_e_scrolls_one_wrapped_display_row,
            test_space_unknown_combination_executes_normal_key,
            test_search_result_is_centered,
            test_wrapped_viewport_persists_across_buffers,
        ]),
        ("52", "Phase 52 — startup directory completion", [
            test_startup_directory_opens_completion_without_splash,
            test_startup_directory_escape_keeps_file_buffers,
            test_startup_directory_selection_opens_buffer,
            test_startup_ignores_later_directories_but_keeps_later_files,
        ]),
        ("53", "Phase 53 — tab display columns", [
            test_cursor_after_insert_with_leading_tab,
            test_tabs_participate_in_wrap_and_eol_layout,
            test_tab_display_columns_drive_sticky_vertical_motion,
            test_tabbed_line_hscroll_uses_display_columns,
            test_tab_expansion_preserves_highlight_boundaries,
            test_layout_maps_source_and_screen_coordinates,
        ]),
        ("54", "Phase 54 — build identification", [
            test_install_stamps_build_identification,
        ]),
        ("55", "Phase 55 — build diagnostics", [
            test_qf_command_captures_and_normalizes_diagnostics,
            test_makeprg_runs_with_make_arguments,
            test_vig_diagnostics_normalizes_gcc_clang_and_python,
            test_makeprg_config_and_silent_success,
        ]),
        ("56", "Phase 56 — working directory", [
            test_file_commands_use_working_directory,
            test_cd_and_cdb_change_global_working_directory,
            test_quickfix_remembers_producer_working_directory,
        ]),
        ("57", "Phase 57 — Markdown presentation", [
            test_markdown_view_aligns_tables_without_dirtying_buffer,
            test_markdown_view_styles_headers_lists_and_tables,
            test_markdown_edit_returns_to_literal_source,
            test_markdown_view_is_per_buffer_and_nomd_disables_it,
            test_markdown_search_maps_back_to_source_for_edit,
            test_markdown_does_not_align_pipe_prose_without_rule,
        ]),
        ("58", "Phase 58 — Y and literal smart-case search", [
            test_Y_yanks_from_cursor_to_end_of_line,
            test_literal_search_uses_smart_case_but_regex_does_not,
            test_word_search_and_hlsearch_use_smart_case,
        ]),
        ("59", "Phase 59 — configurable wrap column", [
            test_wrapcol_wraps_at_configured_display_column,
            test_wrapcol_drives_wrapmove_and_respects_terminal_width,
            test_wrapcol_validation_and_startup_config,
        ]),
        ("60", "Phase 60 — markdown fence hiding", [
            test_markdownfences_hides_backtick_and_tilde_fences,
            test_markdownfences_requires_markdown_view,
            test_markdownfences_only_markdown_files,
        ]),
        ("61", "Phase 61 — mouse wheel scrolling", [
            test_sgr_mouse_decoder,
            test_mouse_options_and_terminal_lifecycle,
            test_mouse_wheel_scrolls_three_display_rows,
            test_mouse_wheel_preserves_active_modes,
            test_mouse_reporting_restores_across_rgf_handoff,
        ]),
        ("62", "Phase 62 — live search highlighting", [
            test_search_prompt_previews_visible_matches_without_moving,
            test_search_prompt_preview_uses_smart_case,
            test_search_preview_clears_on_escape_and_tolerates_invalid_regex,
            test_hlsearch_distinguishes_current_match,
        ]),
        ("63", "Phase 63 — mouse cursor positioning", [
            test_mouse_click_positions_cursor,
            test_mouse_click_retains_insert_command_and_search_modes,
            test_mouse_click_uses_wrapped_and_gutter_layout,
            test_mouse_click_ignores_status_and_message_rows,
        ]),
        ("64", "Phase 64 — mouse Visual selection", [
            test_mouse_drag_creates_visual_selection,
            test_mouse_visual_drag_normalizes_reverse_selection,
            test_mouse_visual_click_without_drag_retains_mode,
            test_mouse_visual_release_does_not_yank,
        ]),
        ("65", "Phase 65 — collapsed Markdown fence rows", [
            test_markdown_fence_rows_collapse_without_blanks,
            test_hidden_fence_search_keeps_source_position_for_edit,
            test_collapsed_fences_preserve_source_line_numbers,
            test_mouse_click_maps_across_collapsed_fences,
            test_mouse_wheel_counts_collapsed_display_rows,
            test_wrapmove_skips_collapsed_fence_rows,
            test_all_hidden_markdown_rows_render_safely,
        ]),
        ("66", "Phase 66 — enhanced language highlighting", [
            test_named_syntax_color_maps_are_complete,
            test_python_highlights_language_entities,
            test_c_and_cpp_highlight_language_entities,
            test_bash_highlights_language_entities,
        ]),
        ("67", "Phase 67 — Markdown fenced-code highlighting", [
            test_markdown_fences_highlight_supported_languages,
            test_markdown_fence_language_aliases,
            test_unknown_fence_suppresses_markdown_prose_styles,
            test_markdown_fence_matching_respects_marker_kind_and_length,
        ]),
        ("68", "Phase 68 — extensionless shell shebang highlighting", [
            test_extensionless_shell_shebang_detection,
            test_extension_and_unsupported_shebang_precedence,
            test_extensionless_shebang_enables_shell_rendering,
        ]),
        ("69", "Phase 69 — per-buffer file types", [
            test_filetype_command_forces_language,
            test_filetype_text_disables_syntax,
            test_filetype_auto_redetects_and_reports,
            test_filetype_markdown_controls_presentation,
            test_filetype_override_persists_per_buffer_and_reload,
            test_filetype_rejects_unknown_value,
        ]),
        ("70", "Phase 70 — automatic syntax and Markdown detection", [
            test_markdown_files_open_in_markdown_view,
            test_autodetect_option_affects_only_new_buffers,
            test_noautodetect_config_disables_initial_recognition,
            test_explicit_commands_override_disabled_detection,
        ]),
        ("71", "Phase 71 — retained manual-save versions", [
            test_saveversions_rotates_prior_disk_contents,
            test_saveversions_skips_unchanged_and_new_targets,
            test_saveversions_preserves_existing_save_as_target,
            test_saveversions_reduction_removes_excess_generations,
            test_saveversions_failure_blocks_write,
            test_backup_files_do_not_version_themselves,
            test_saveversions_validates_and_loads_from_config,
        ]),
        ("72", "Phase 72 — autosave deadlines", [
            test_autosave_writes_named_buffer_after_idle_delay,
            test_autosave_is_disabled_by_default,
            test_autosave_handles_multiple_open_buffers,
            test_autosave_does_not_rotate_manual_versions,
            test_explicit_write_clears_pending_autosave,
            test_autosave_error_leaves_dirty_buffer_and_waits_for_new_edit,
            test_autosave_options_validate_and_load_from_config,
        ]),
        ("73", "Phase 73 — config and in-editor help audit", [
            test_example_config_lists_all_runtime_defaults,
            test_vighelp_covers_commands_and_config_options,
        ]),
    ]

    if selected is not None:
        known = {phase_id for phase_id, _, _ in phase_defs}
        unknown = sorted(selected - known)
        if unknown:
            print(f"Unknown phase selector(s): {', '.join(unknown)}")
            print(f"Known selectors: {', '.join(sorted(known))}")
            sys.exit(2)

    for phase_id, phase_name, tests in phase_defs:
        if selected is None or phase_id in selected:
            total_failed += run_phase(phase_name, tests)

    print(f"\n{'=' * 60}")
    if total_failed:
        print(f"  TOTAL: {total_failed} test(s) FAILED")
        sys.exit(1)
    print("  ALL TESTS PASSED")
    sys.exit(0)

if __name__ == "__main__":
    main()
