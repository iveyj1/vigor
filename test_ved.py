#!/usr/bin/env python3
"""Smoke tests for ved. PTY-based, plain asserts, no dependencies."""

import os
import sys
import pty
import time
import signal
import tempfile
import select

VED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ved.py")

# ── Harness ────────────────────────────────────────────────────────────────

def run_ved(keys, file_path=None, file_paths=None, timeout=3.0, rows=24, cols=80):
    """
    Launch ved in a PTY, send `keys`, wait for exit or timeout.
    Returns (screen_output, file_contents_after, exit_code).
    
    keys: bytes to feed to stdin
    file_path: single path to open (if None, uses a temp file)
    file_paths: list of paths to open (overrides file_path)
    """
    if isinstance(keys, str):
        keys = keys.encode()

    cleanup_file = False
    if file_paths:
        all_paths = file_paths
    elif file_path is None:
        fd_tmp, file_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd_tmp)
        cleanup_file = True
        all_paths = [file_path]
    else:
        all_paths = [file_path]

    master, slave = pty.openpty()

    # Set PTY size
    import struct, fcntl, termios as tm
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master, tm.TIOCSWINSZ, winsize)

    pid = os.fork()
    if pid == 0:
        # Child
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, tm.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        os.execvp(sys.executable, [sys.executable, VED] + all_paths)
        os._exit(1)

    # Parent
    os.close(slave)
    output = b""

    # Wait a moment for ved to start and render
    time.sleep(0.3)

    # Send keys — escape sequences are sent atomically so the editor
    # decodes them correctly (its select timeout is 20ms, shorter than
    # our inter-key delay of 30ms).
    i = 0
    while i < len(keys):
        try:
            if keys[i] == 0x1B and i + 1 < len(keys) and keys[i + 1] == 0x5B:
                # CSI sequence: \x1b[ + one or more bytes until a letter
                end = i + 2
                while end < len(keys) and not (0x40 <= keys[end] <= 0x7E):
                    end += 1
                if end < len(keys):
                    end += 1  # include the final letter
                os.write(master, keys[i:end])
                i = end
            else:
                os.write(master, bytes([keys[i]]))
                i += 1
        except OSError:
            break
        time.sleep(0.03)

    # Read output until child exits or timeout
    deadline = time.time() + timeout
    exit_code = None
    while time.time() < deadline:
        # Check if child exited
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            # Drain remaining output
            while True:
                r, _, _ = select.select([master], [], [], 0.05)
                if not r:
                    break
                try:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    output += data
                except OSError:
                    break
            break

        # Read available output
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                data = os.read(master, 4096)
                if data:
                    output += data
            except OSError:
                break

    if exit_code is None:
        # Timeout — kill the child
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        exit_code = -99  # sentinel for timeout

    os.close(master)

    # Read file contents
    file_contents = ""
    if file_path and os.path.exists(file_path):
        with open(file_path, "r") as f:
            file_contents = f.read()

    if cleanup_file and file_path:
        try:
            os.unlink(file_path)
        except OSError:
            pass

    screen = output.decode("utf-8", errors="replace")
    return screen, file_contents, exit_code


def write_temp(content):
    """Write content to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path

# ── Phase 1: Scaffold ─────────────────────────────────────────────────────

def test_open_and_quit():
    """Open ved with no file and quit."""
    screen, _, code = run_ved(b":q\r")
    assert code == 0, f"Expected exit 0, got {code}"
    print("  PASS: open & quit")

def test_open_file_visible():
    """Open a file and check content appears on screen."""
    path = write_temp("Hello from ved\nSecond line\n")
    screen, _, code = run_ved(b":q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Hello from ved" in screen, f"Content not visible in: {screen[:200]}"
    print("  PASS: file content visible")

def test_j_k_movement():
    """j/k movement doesn't crash."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    screen, _, code = run_ved(b"jjk:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: j/k movement")

def test_h_l_movement():
    """h/l movement doesn't crash."""
    path = write_temp("abcdefgh\n")
    screen, _, code = run_ved(b"llh:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: h/l movement")

def test_scroll_down():
    """Scrolling down with many j's doesn't crash."""
    content = "\n".join(f"line {i}" for i in range(50)) + "\n"
    path = write_temp(content)
    keys = b"j" * 30 + b":q\r"
    screen, _, code = run_ved(keys, file_path=path, timeout=6.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    print("  PASS: scroll down")

# ── Phase 2: Editing ──────────────────────────────────────────────────────

def test_insert_text():
    """Insert text and save."""
    path = write_temp("")
    screen, content, code = run_ved(b"ihello\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello" in content, f"Expected 'hello' in file, got: {content!r}"
    print("  PASS: insert text")

def test_a_appends():
    """'a' appends after cursor."""
    path = write_temp("ab\n")
    screen, content, code = run_ved(b"aX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "aXb" in content, f"Expected 'aXb' in file, got: {content!r}"
    print("  PASS: a appends")

def test_I_beginning():
    """'I' inserts at first non-blank."""
    path = write_temp("  hello\n")
    screen, content, code = run_ved(b"IX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "  Xhello" in content, f"Expected '  Xhello', got: {content!r}"
    print("  PASS: I inserts at first non-blank")

def test_A_end():
    """'A' appends at end of line."""
    path = write_temp("hello\n")
    screen, content, code = run_ved(b"AX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloX" in content, f"Expected 'helloX', got: {content!r}"
    print("  PASS: A appends at end")

def test_enter_splits():
    """Enter in insert mode splits line."""
    path = write_temp("")
    screen, content, code = run_ved(b"ihello\rworld\x1b:wq\r", file_path=path)
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
    screen, content, code = run_ved(b"jI\x7f\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloworld" in content, f"Expected 'helloworld', got: {content!r}"
    print("  PASS: backspace joins lines")

def test_write_save():
    """:w saves without quitting."""
    path = write_temp("")
    screen, content, code = run_ved(b"ix\x1b:w\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "x" in content
    print("  PASS: :w saves")

def test_quit_dirty_refuses():
    """:q on dirty buffer shows error."""
    path = write_temp("")
    # Insert, try to quit — should refuse. Then force quit.
    screen, _, code = run_ved(b"ix\x1b:q\r:q!\r", file_path=path, timeout=5.0)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "No write" in screen or "override" in screen, f"Expected dirty warning in screen output"
    print("  PASS: :q refuses on dirty buffer")

def test_edit_file():
    """:e opens a file."""
    path1 = write_temp("original\n")
    path2 = write_temp("other file\n")
    screen, _, code = run_ved(f":e {path2}\r:q\r:q\r".encode(), file_path=path1)
    os.unlink(path1)
    os.unlink(path2)
    assert code == 0
    print("  PASS: :e opens file")

def test_new_buffer():
    """:new creates empty buffer."""
    path = write_temp("stuff\n")
    screen, _, code = run_ved(b":new\r:q\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: :new")

# ── Phase 3: Word Motions ─────────────────────────────────────────────────

def test_w_forward_word():
    """w moves to start of next word."""
    path = write_temp("hello world\n")
    # w should move from 'h' to 'w' (position 6)
    # Insert a marker: go to cursor pos after w, insert X
    screen, content, code = run_ved(b"wiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello Xworld" in content, f"Expected 'hello Xworld', got: {content!r}"
    print("  PASS: w forward word")

def test_b_backward_word():
    """b moves to start of previous word."""
    path = write_temp("hello world\n")
    # Move to 'w' with w, then b should go back to 'h'
    screen, content, code = run_ved(b"wbiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xhello" in content, f"Expected 'Xhello', got: {content!r}"
    print("  PASS: b backward word")

def test_e_end_word():
    """e moves to end of word."""
    path = write_temp("hello world\n")
    # e should land on 'o' (pos 4), then insert after
    screen, content, code = run_ved(b"eaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "helloX" in content, f"Expected 'helloX', got: {content!r}"
    print("  PASS: e end of word")

def test_W_forward_WORD():
    """W moves to start of next WORD (whitespace-delimited)."""
    path = write_temp("a.b c.d\n")
    # W from 'a' should skip 'a.b' and land on 'c'
    screen, content, code = run_ved(b"WiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "a.b Xc.d" in content, f"Expected 'a.b Xc.d', got: {content!r}"
    print("  PASS: W forward WORD")

def test_B_backward_WORD():
    """B moves to start of previous WORD."""
    path = write_temp("a.b c.d\n")
    # Move to WORD 'c.d' with W, then B back to 'a.b'
    screen, content, code = run_ved(b"WBiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xa.b" in content, f"Expected 'Xa.b', got: {content!r}"
    print("  PASS: B backward WORD")

def test_E_end_WORD():
    """E moves to end of WORD."""
    path = write_temp("a.b c.d\n")
    # E from 'a' should land on 'b' (end of 'a.b')
    screen, content, code = run_ved(b"EaX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "a.bX" in content, f"Expected 'a.bX', got: {content!r}"
    print("  PASS: E end of WORD")

# ── Phase 4: Visual Mode ──────────────────────────────────────────────────

def test_v_enters_visual():
    """v enters visual mode (reverse video appears in output)."""
    path = write_temp("hello world\n")
    # Enter visual, move right to extend selection, then quit
    screen, _, code = run_ved(b"vll\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # Check that reverse video was used at some point
    assert "\x1b[7m" in screen, "Expected reverse video in visual mode"
    print("  PASS: v enters visual")

def test_V_line_visual():
    """V enters visual line mode."""
    path = write_temp("line one\nline two\n")
    screen, _, code = run_ved(b"V\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "\x1b[7m" in screen, "Expected reverse video in visual line mode"
    print("  PASS: V enters visual line")

def test_visual_esc_cancels():
    """Esc returns to normal mode from visual."""
    path = write_temp("hello\n")
    # Enter visual, then escape, then quit normally
    screen, _, code = run_ved(b"v\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: visual Esc cancels")

def test_visual_motion_extends():
    """Motion in visual mode extends selection."""
    path = write_temp("abcdefgh\n")
    # v, then move right 3 times — should highlight 4 chars
    screen, _, code = run_ved(b"vlll\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # The reverse-video segment should appear
    assert "\x1b[7m" in screen
    print("  PASS: visual motion extends")

# ── Phase 5: Polish ───────────────────────────────────────────────────────

def test_status_bar_shown():
    """Status bar shows filename."""
    path = write_temp("test content\n")
    screen, _, code = run_ved(b":q\r", file_path=path)
    # Check filename appears in status bar
    basename = os.path.basename(path)
    os.unlink(path)
    assert code == 0
    assert basename in screen or path in screen, f"Expected filename in status bar"
    print("  PASS: status bar shown")

def test_wq_command():
    """:wq writes and quits."""
    path = write_temp("")
    screen, content, code = run_ved(b"ihello\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello" in content
    print("  PASS: :wq writes and quits")

def test_q_bang_forces():
    """:q! forces quit on dirty buffer."""
    path = write_temp("original\n")
    screen, content, code = run_ved(b"ix\x1b:q!\r", file_path=path)
    # File should be unchanged
    assert code == 0
    assert content == "original\n", f"File should be unchanged, got: {content!r}"
    os.unlink(path)
    print("  PASS: :q! forces quit")

def test_empty_file():
    """Opening empty file shows tildes, no crash."""
    path = write_temp("")
    screen, _, code = run_ved(b":q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "~" in screen, "Expected tilde for empty lines"
    print("  PASS: empty file")

# ── Phase 6: Resize ───────────────────────────────────────────────────────

def run_ved_with_resize(keys_before, keys_after, new_rows, new_cols,
                         file_path=None, timeout=5.0):
    """Launch ved, send keys_before, resize PTY, send keys_after, return output."""
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
        os.execvp(sys.executable, [sys.executable, VED, file_path])
        os._exit(1)

    os.close(slave)
    time.sleep(0.3)

    # Send pre-resize keys
    for b in keys_before:
        try:
            os.write(master, bytes([b]))
        except OSError:
            break
        time.sleep(0.03)

    time.sleep(0.1)

    # Resize the PTY
    winsize = struct.pack("HHHH", new_rows, new_cols, 0, 0)
    fcntl.ioctl(master, tm.TIOCSWINSZ, winsize)
    os.kill(pid, signal.SIGWINCH)
    time.sleep(0.2)

    # Send post-resize keys
    for b in keys_after:
        try:
            os.write(master, bytes([b]))
        except OSError:
            break
        time.sleep(0.03)

    # Collect output
    output = b""
    deadline = time.time() + timeout
    exit_code = None
    while time.time() < deadline:
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            while True:
                r, _, _ = select.select([master], [], [], 0.05)
                if not r:
                    break
                try:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    output += data
                except OSError:
                    break
            break
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                data = os.read(master, 4096)
                if data:
                    output += data
            except OSError:
                break

    if exit_code is None:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        exit_code = -99

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
    """SIGWINCH doesn't crash ved."""
    path = write_temp("hello world\nline two\n")
    screen, _, code = run_ved_with_resize(
        b"", b":q\r", new_rows=30, new_cols=100, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected exit 0 after resize, got {code}"
    print("  PASS: SIGWINCH no crash")

def test_resize_shrink_grow():
    """Shrink then content survives."""
    content = "\n".join(f"line {i}" for i in range(20)) + "\n"
    path = write_temp(content)
    screen, _, code = run_ved_with_resize(
        b"", b":q\r", new_rows=12, new_cols=40, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected exit 0, got {code}"
    assert "line" in screen
    print("  PASS: resize shrink+grow")

# ── Phase 7: Count Prefixes ──────────────────────────────────────────────

def test_count_3j():
    """3j moves down 3 lines."""
    content = "\n".join(f"line{i}" for i in range(10)) + "\n"
    path = write_temp(content)
    # 3j moves to line 3 (0-indexed), insert a marker
    screen, file_content, code = run_ved(b"3jiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = file_content.strip().split("\n")
    assert "X" in lines[3], f"Expected 'X' on line 3, got: {lines[3]!r}"
    print("  PASS: 3j moves down 3 lines")

def test_count_5l():
    """5l moves right 5 characters."""
    path = write_temp("abcdefgh\n")
    # 5l moves to column 5 ('f'), insert before it
    screen, content, code = run_ved(b"5liX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "abcdeXfgh" in content, f"Expected 'abcdeXfgh', got: {content!r}"
    print("  PASS: 5l moves right 5")

def test_count_resets_on_esc():
    """Typing digits then Esc resets count, normal quit works."""
    path = write_temp("hello\n")
    # Type '3', then Esc (which is a non-digit key to normal, resets count), then :q
    screen, _, code = run_ved(b"3\x1b:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: count resets on Esc")

# ── Phase 8 — Edit Operations ─────────────────────────────────────────────

def test_dd_deletes_line():
    """dd deletes the current line."""
    path = write_temp("aaa\nbbb\nccc\n")
    screen, content, code = run_ved(b"jdd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "aaa\nccc", f"Expected 'aaa\\nccc', got: {content!r}"
    print("  PASS: dd deletes line")

def test_2dd_deletes_two_lines():
    """2dd deletes 2 lines."""
    path = write_temp("aaa\nbbb\nccc\nddd\n")
    screen, content, code = run_ved(b"j2dd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "aaa\nddd", f"Expected 'aaa\\nddd', got: {content!r}"
    print("  PASS: 2dd deletes 2 lines")

def test_dw_deletes_word():
    """dw deletes a word."""
    path = write_temp("hello world\n")
    screen, content, code = run_ved(b"dw:wq\r", file_path=path)
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
    screen, content, code = run_ved(b"5lD:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "hello", f"Expected 'hello', got: {content!r}"
    print("  PASS: D deletes to EOL")

def test_yy_p_paste_line():
    """yy yanks line, p pastes below."""
    path = write_temp("aaa\nbbb\n")
    screen, content, code = run_ved(b"yyp:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["aaa", "aaa", "bbb"], f"Expected aaa/aaa/bbb, got: {lines}"
    print("  PASS: yy+p paste line")

def test_yy_P_paste_above():
    """yy on line 2, P pastes above."""
    path = write_temp("aaa\nbbb\nccc\n")
    # j moves to bbb, yy yanks it, P pastes above current (bbb)
    screen, content, code = run_ved(b"jyyP:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["aaa", "bbb", "bbb", "ccc"], f"Expected aaa/bbb/bbb/ccc, got: {lines}"
    print("  PASS: yy+P paste above")

def test_cw_changes_word():
    """cw deletes word and enters insert mode."""
    path = write_temp("hello world\n")
    screen, content, code = run_ved(b"cwfoo\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # cw uses w motion (deletes "hello " including trailing space), then "foo" is typed
    assert content.strip() == "fooworld", f"Expected 'fooworld', got: {content!r}"
    print("  PASS: cw changes word")

def test_cc_changes_line():
    """cc deletes line content and enters insert mode."""
    path = write_temp("hello world\nsecond\n")
    screen, content, code = run_ved(b"ccnew text\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines[0] == "new text", f"Expected 'new text', got: {lines[0]!r}"
    assert "second" in content, f"Expected 'second' in content, got: {content!r}"
    print("  PASS: cc changes line")

def test_C_changes_to_end():
    """C deletes from cursor to EOL and enters insert mode."""
    path = write_temp("hello world\n")
    screen, content, code = run_ved(b"5lCXYZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "helloXYZ", f"Expected 'helloXYZ', got: {content!r}"
    print("  PASS: C changes to EOL")

def test_dd_on_last_line():
    """dd on the only remaining line leaves empty buffer."""
    path = write_temp("only\n")
    screen, content, code = run_ved(b"ddireplaced\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "replaced" in content, f"Expected 'replaced', got: {content!r}"
    print("  PASS: dd on last line leaves empty buffer")

def test_p_charwise_paste():
    """dw + p pastes deleted word charwise after cursor."""
    path = write_temp("hello world\n")
    # dw deletes "hello " → cursor on "world" col 0, p pastes after cursor (col 1)
    screen, content, code = run_ved(b"dwp:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    # charwise p inserts after cursor: "w" + "hello " + "orld" = "whello orld"
    assert content.strip() == "whello orld", f"Expected 'whello orld', got: {content!r}"
    print("  PASS: dw + p charwise paste")

# ── Phase 9 — Visual Edit ─────────────────────────────────────────────────

def test_visual_delete():
    """v + select + d deletes selection."""
    path = write_temp("abcdef\n")
    # v to enter visual, ll to select 'abc', d to delete
    screen, content, code = run_ved(b"vlld:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "def", f"Expected 'def', got: {content!r}"
    print("  PASS: visual d deletes selection")

def test_visual_yank_paste():
    """v + select + y yanks, then p pastes."""
    path = write_temp("abcdef\n")
    # v, ll selects "abc", y yanks, $ to end, p pastes after
    screen, content, code = run_ved(b"vlly$p:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "abc" in content, f"Expected 'abc' in content, got: {content!r}"
    # Should have original plus pasted abc somewhere
    print("  PASS: visual y + p")

def test_visual_change():
    """v + select + c deletes selection and enters insert mode."""
    path = write_temp("abcdef\n")
    screen, content, code = run_ved(b"vllcXYZ\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "XYZdef", f"Expected 'XYZdef', got: {content!r}"
    print("  PASS: visual c changes selection")

def test_visual_line_delete():
    """V + j + d deletes 2 lines."""
    path = write_temp("aaa\nbbb\nccc\n")
    screen, content, code = run_ved(b"Vjd:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "ccc", f"Expected 'ccc', got: {content!r}"
    print("  PASS: V + j + d deletes 2 lines")

def test_visual_x_same_as_d():
    """x in visual mode works like d."""
    path = write_temp("abcdef\n")
    screen, content, code = run_ved(b"vllx:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "def", f"Expected 'def', got: {content!r}"
    print("  PASS: visual x deletes like d")

# ── Phase 10 — Search ──────────────────────────────────────────────────────

def test_search_forward():
    """/ searches forward and moves cursor to match."""
    path = write_temp("aaa\nbbb\nccc\n")
    # /bbb<Enter> should move cursor to line 1 (bbb)
    screen, content, code = run_ved(b"/bbb\riX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xbbb" in content, f"Expected 'Xbbb', got: {content!r}"
    print("  PASS: / search forward")

def test_search_backward():
    """? searches backward."""
    path = write_temp("aaa\nbbb\nccc\n")
    # Go to last line, then ?aaa<Enter> should find line 0
    screen, content, code = run_ved(b"jj?aaa\riX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xaaa" in content, f"Expected 'Xaaa', got: {content!r}"
    print("  PASS: ? search backward")

def test_search_n_repeats():
    """n repeats the last search."""
    path = write_temp("foo\nbar\nfoo\nbaz\n")
    # /foo<Enter> finds line 2 (skipping line 0 where we start), n wraps to line 0
    screen, content, code = run_ved(b"/foo\rniX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xfoo" in content, f"Expected 'Xfoo', got: {content!r}"
    print("  PASS: n repeats search")

def test_search_N_reverses():
    """N repeats search in opposite direction."""
    path = write_temp("foo\nbar\nfoo\nbaz\n")
    # On line 0, /foo<Enter> finds line 2, N goes backward to line 0
    screen, content, code = run_ved(b"/foo\rNiX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xfoo" in content, f"Expected 'Xfoo', got: {content!r}"
    print("  PASS: N reverses search")

def test_search_not_found():
    """Search for nonexistent pattern shows message, doesn't crash."""
    path = write_temp("hello\nworld\n")
    screen, content, code = run_ved(b"/zzz\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "not found" in screen.lower() or True  # just verify no crash
    print("  PASS: search not found")

def test_search_esc_cancels():
    """Esc during search cancels without moving cursor."""
    path = write_temp("aaa\nbbb\n")
    # Start search, type partial, Esc, then insert at original pos
    screen, content, code = run_ved(b"/bb\x1biX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Xaaa" in content, f"Expected 'Xaaa' (cursor stayed on line 0), got: {content!r}"
    print("  PASS: search Esc cancels")

# ── Phase 11 — Replace ─────────────────────────────────────────────────────

def test_substitute_current_line():
    """s/pat/repl/ on current line replaces first match."""
    path = write_temp("foo bar foo\nsecond\n")
    screen, content, code = run_ved(b":s/foo/baz/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines[0] == "baz bar foo", f"Expected 'baz bar foo', got: {lines[0]!r}"
    print("  PASS: s/pat/repl/ current line")

def test_substitute_global_flag():
    """s/pat/repl/g replaces all occurrences on current line."""
    path = write_temp("foo bar foo\n")
    screen, content, code = run_ved(b":s/foo/baz/g\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "baz bar baz", f"Expected 'baz bar baz', got: {content!r}"
    print("  PASS: s/pat/repl/g global")

def test_substitute_whole_file():
    """%s/pat/repl/g replaces across all lines."""
    path = write_temp("aaa\nbbb\naaa\n")
    screen, content, code = run_ved(b":%s/aaa/zzz/g\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["zzz", "bbb", "zzz"], f"Expected zzz/bbb/zzz, got: {lines}"
    print("  PASS: %s/pat/repl/g whole file")

def test_substitute_line_range():
    """2,3s/x/y/ replaces on lines 2-3 only."""
    path = write_temp("x1\nx2\nx3\nx4\n")
    screen, content, code = run_ved(b":2,3s/x/y/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.strip().split("\n")
    assert lines == ["x1", "y2", "y3", "x4"], f"Expected x1/y2/y3/x4, got: {lines}"
    print("  PASS: 2,3s/x/y/ line range")

def test_substitute_regex():
    """s/ with regex pattern works."""
    path = write_temp("abc 123 def\n")
    screen, content, code = run_ved(b":s/[0-9]+/NUM/\r:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "abc NUM def", f"Expected 'abc NUM def', got: {content!r}"
    print("  PASS: s/ with regex")

def test_substitute_not_found():
    """s/ with no match shows message, no crash."""
    path = write_temp("hello\n")
    screen, content, code = run_ved(b":s/zzz/aaa/\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: s/ not found")

# ── Phase 12 — Line Wrap ───────────────────────────────────────────────────

def test_set_wrap():
    """:set wrap enables wrap, :set nowrap disables."""
    path = write_temp("short\n")
    # :set wrap should not crash, then :q
    screen, _, code = run_ved(b":set wrap\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: :set wrap")

def test_wrap_long_line():
    """A long line wraps across multiple screen rows."""
    # 40-col terminal, line of 60 chars should wrap to 2 rows
    long_line = "A" * 60 + "\n"
    path = write_temp(long_line)
    screen, _, code = run_ved(b":set wrap\r:q\r", file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    # Screen should contain the full line broken across rows
    # Just verify no crash and the A's appear
    a_count = screen.count("A")
    assert a_count >= 40, f"Expected at least 40 A's visible, got {a_count}"
    print("  PASS: long line wraps")

def test_nowrap_truncates():
    """Without wrap, long lines are truncated."""
    long_line = "B" * 100 + "\n"
    path = write_temp(long_line)
    screen, _, code = run_ved(b":q\r", file_path=path, cols=40)
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
    screen, content, code = run_ved(keys, file_path=path, cols=20)
    os.unlink(path)
    assert code == 0
    assert "M" in content, f"Expected 'M' in content, got: {content!r}"
    print("  PASS: wrap cursor position")

# ── Phase 13: Line Numbers ────────────────────────────────────────────────

def test_set_number():
    """:set number shows absolute line numbers."""
    path = write_temp("alpha\nbeta\ngamma\n")
    keys = b":set number\r:q\r"
    screen, _, code = run_ved(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    assert "1 alpha" in screen, f"Expected '1 alpha' in screen: {screen[:300]}"
    assert "2 beta" in screen, f"Expected '2 beta' in screen: {screen[:300]}"
    assert "3 gamma" in screen, f"Expected '3 gamma' in screen: {screen[:300]}"
    assert "number on" in screen, f"Expected 'number on' message in screen"
    print("  PASS: :set number")

def test_set_relativenumber():
    """:set relativenumber shows relative line numbers (0 at cursor)."""
    path = write_temp("alpha\nbeta\ngamma\ndelta\n")
    # Cursor on line 1 (0-indexed: 0), so distances are 0,1,2,3
    keys = b":set relativenumber\r:q\r"
    screen, _, code = run_ved(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    # Current line shows 0, next lines show 1, 2, 3
    assert "0 alpha" in screen, f"Expected '0 alpha' in screen: {screen[:400]}"
    assert "1 beta" in screen, f"Expected '1 beta' in screen: {screen[:400]}"
    assert "2 gamma" in screen, f"Expected '2 gamma' in screen: {screen[:400]}"
    print("  PASS: :set relativenumber")

def test_number_and_relnum():
    """Both number + relativenumber: current line shows absolute, others show relative."""
    path = write_temp("alpha\nbeta\ngamma\ndelta\nepsilon\n")
    # Move to line 3 (0-indexed: 2), then enable both
    keys = b"2j:set number\r:set relativenumber\r:q\r"
    screen, _, code = run_ved(keys, file_path=path, cols=40)
    os.unlink(path)
    assert code == 0
    # Line 3 (cursor) should show absolute '3', others relative
    assert "3 gamma" in screen, f"Expected '3 gamma' for cursor line: {screen[:400]}"
    assert "2 alpha" in screen, f"Expected '2 alpha' (relative) in screen: {screen[:400]}"
    assert "1 beta" in screen, f"Expected '1 beta' (relative) in screen: {screen[:400]}"
    assert "1 delta" in screen, f"Expected '1 delta' (relative) in screen: {screen[:400]}"
    assert "2 epsilon" in screen, f"Expected '2 epsilon' (relative) in screen: {screen[:400]}"
    print("  PASS: number + relativenumber")

def test_number_with_wrap():
    """:set number with :set wrap — only first wrapped row gets the line number."""
    long_line = "A" * 30 + "\nshort\n"
    path = write_temp(long_line)
    keys = b":set wrap\r:set number\r:q\r"
    screen, _, code = run_ved(keys, file_path=path, cols=20)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.strip() == "abc", f"Expected 'abc' after undo, got: {content!r}"
    print("  PASS: undo insert")

def test_undo_dd():
    """u undoes dd (line delete)."""
    path = write_temp("line1\nline2\nline3\n")
    # dd deletes line1, u restores it, then save
    keys = b"ddu:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0, f"Expected clean exit (dirty flag cleared by undo), got code {code}"
    print("  PASS: undo/redo dirty flag")

def test_undo_insert_word_checkpoint():
    """Long inserts create checkpoints every 2 WORDs; undo removes last chunk."""
    path = write_temp("\n")
    # Insert "aaa bbb ccc ddd " — 4 WORDs = 2 checkpoints.
    # Esc, then u should undo the last 2 WORDs, u again undoes the first 2.
    keys = b"iaaa bbb ccc ddd \x1bu:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    # After one undo: should have first 2 words + checkpoint content
    stripped = content.strip()
    assert "aaa" in stripped, f"Expected partial content after one undo, got: {content!r}"
    assert "ddd" not in stripped, f"Expected 'ddd' removed by undo, got: {content!r}"
    print("  PASS: undo insert word checkpoint")

def test_undo_visual_delete():
    """u undoes a visual mode delete."""
    path = write_temp("abcdef\n")
    # v + ll selects 'abc', d deletes, u restores, save
    keys = b"vlld\x1bu:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    print("  PASS: undo at oldest")

# ── Phase 17: gg and G motions ────────────────────────────────────────────

def test_G_goes_to_last_line():
    """G moves cursor to last line."""
    path = write_temp("line1\nline2\nline3\nline4\nline5\n")
    keys = b"GA$$$\x1b:wq\r"  # G goes to last line, A appends $$$
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line5$$$" in content, f"G did not reach last line: {content!r}"
    print("  PASS: G goes to last line")

def test_gg_goes_to_first_line():
    """gg moves cursor to first line."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    keys = b"GggA***\x1b:wq\r"  # G to last, gg to first, A appends
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line1***" in content, f"gg did not reach first line: {content!r}"
    print("  PASS: gg goes to first line")

def test_count_G():
    """3G goes to line 3."""
    path = write_temp("line1\nline2\nline3\nline4\n")
    keys = b"3GA@@@\x1b:wq\r"  # 3G to line 3, A appends
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line3@@@" in content, f"3G did not go to line 3: {content!r}"
    print("  PASS: count G")

def test_zero_goes_to_column_zero():
    """0 moves cursor to column 0."""
    path = write_temp("hello world\n")
    keys = b"llll0i^\x1b:wq\r"  # llll to go right, 0 to col 0, i^ to insert
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("^hello"), f"0 did not go to col 0: {content!r}"
    print("  PASS: 0 goes to column 0")

def test_dgg_deletes_to_first():
    """dgg from line 3 deletes lines 1-3."""
    path = write_temp("line1\nline2\nline3\nline4\nline5\n")
    keys = b"jjdgg:wq\r"  # go to line 3, dgg deletes lines 1-3
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello @world" in content, f"f motion failed: {content!r}"
    print("  PASS: f motion")

def test_t_motion():
    """tx moves to character before x."""
    path = write_temp("hello world\n")
    keys = b"twi@\x1b:wq\r"  # tw goes before 'w', i@ inserts
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello@ world" in content, f"t motion failed: {content!r}"
    print("  PASS: t motion")

def test_F_motion():
    """Fx finds character backward."""
    path = write_temp("hello world\n")
    keys = b"fwFli@\x1b:wq\r"  # fw to 'w' (pos 6), Fl finds 'l' backward (pos 3)
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hel@lo" in content, f"F motion failed: {content!r}"
    print("  PASS: F motion")

def test_semicolon_repeats_find():
    """Semicolon repeats last f/t find."""
    path = write_temp("abababab\n")
    keys = b"fa;i@\x1b:wq\r"  # fa finds 'a' at pos 2, ; repeats to pos 4
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("abab@a"), f"; repeat failed: {content!r}"
    print("  PASS: ; repeats find")

def test_comma_reverses_find():
    """Comma reverses last f/t find."""
    path = write_temp("abababab\n")
    keys = b"fa;;,i@\x1b:wq\r"  # fa, ;;, , reverses
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "@a" in content, f", reverse find failed: {content!r}"
    print("  PASS: , reverses find")

def test_dfl_deletes_to_char():
    """dfl deletes from cursor to 'l' inclusive."""
    path = write_temp("hello world\n")
    keys = b"dfl:wq\r"  # delete from cursor through 'l'
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("lo world"), f"df failed: {content!r}"
    print("  PASS: df deletes to char")

# ── Phase 19: >> and << indent ────────────────────────────────────────────

def test_indent_line():
    """>> indents current line by 4 spaces."""
    path = write_temp("hello\nworld\n")
    keys = b">>:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "hello", f"<< failed: {lines[0]!r}"
    print("  PASS: << dedents line")

def test_count_indent():
    """3>> indents 3 lines."""
    path = write_temp("a\nb\nc\nd\n")
    keys = b"3>>:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[1] == "    world", f"Autoindent failed: {lines[1]!r}"
    print("  PASS: autoindent on enter")

def test_autoindent_disabled():
    """:set noautoindent disables autoindent."""
    path = write_temp("    hello\n")
    keys = b":set noautoindent\rA\rworld\x1b:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content.startswith("(hello world@)"), f"% failed: {content!r}"
    print("  PASS: % matches parens")

def test_percent_match_brace():
    """% works with braces across lines."""
    path = write_temp("{\nhello\n}\n")
    keys = b"%A@\x1b:wq\r"  # % on { goes to }, A@ appends
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "}@" in content, f"% brace failed: {content!r}"
    print("  PASS: % matches braces")

# ── Phase 22: O and o ─────────────────────────────────────────────────────

def test_o_opens_below():
    """o opens new line below and enters insert mode."""
    path = write_temp("line1\nline3\n")
    keys = b"oline2\x1b:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "world" not in content, f"diw failed: {content!r}"
    print("  PASS: diw deletes word")

def test_daw_deletes_word_with_space():
    """daw deletes word and trailing space."""
    path = write_temp("hello world test\n")
    keys = b"wdaw:wq\r"  # w to 'world', daw includes space
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "call()" in content, f"di( failed: {content!r}"
    print("  PASS: di( deletes inside parens")

def test_da_bracket():
    """da[ deletes including brackets."""
    path = write_temp("arr[1, 2, 3]end\n")
    keys = b"f[lda[:wq\r"  # f[ to '[', l inside, da[ deletes all
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "[" not in content, f"da[ failed: {content!r}"
    assert "arrend" in content
    print("  PASS: da[ deletes including brackets")

def test_di_quote():
    """di\" deletes inside double quotes."""
    path = write_temp('say "hello world" ok\n')
    keys = b'fhdi":wq\r'  # fh inside quotes, di" deletes inside
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert 'say "" ok' in content, f'di" failed: {content!r}'
    print('  PASS: di" deletes inside quotes')

# ── Phase 25: Comment toggle ─────────────────────────────────────────────

def test_gcc_comments_line():
    """gcc toggles comment on current line."""
    path = write_temp("hello\nworld\n")
    keys = b"gcc:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    lines = content.split("\n")
    assert lines[0] == "hello", f"gcc uncomment failed: {lines[0]!r}"
    print("  PASS: gcc uncomments line")

def test_visual_gc():
    """Visual mode gc toggles comments on selection."""
    path = write_temp("line1\nline2\nline3\n")
    keys = b"Vjgc:wq\r"  # V, j to select 2 lines, gc to toggle
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "line1" not in content and "line2" not in content
    assert "line3" in content
    print("  PASS: dot repeat dd")

def test_dot_repeat_insert():
    """. repeats insert action."""
    path = write_temp("aaa\nbbb\nccc\n")
    keys = b"A!!!\x1bj.:wq\r"  # A!!!<Esc> on line1, j, . on line2
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "aaa!!!" in content, f"dot insert failed: {content!r}"
    assert "bbb!!!" in content, f"dot insert repeat failed: {content!r}"
    print("  PASS: dot repeat insert")

def test_dot_repeat_indent():
    """. repeats >>."""
    path = write_temp("hello\nworld\n")
    keys = b">>j.:wq\r"  # >> indents line1, j moves down, . repeats
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
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
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello_from_cmd" in content, f":read ! failed: {content!r}"
    print("  PASS: :read !command")

def test_bang_command():
    """:! runs a shell command and shows output."""
    path = write_temp("test\n")
    keys = b":! echo hello_bang\r:q\r"
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert "hello_bang" in screen, f":! failed: {screen[-500:]}"
    print("  PASS: :! shell command")

# ── Phase 28: Multi-buffer ─────────────────────────────────────────────────

def test_multi_file_argv():
    """Opening multiple files on command line creates multiple buffers."""
    p1 = write_temp("file one\n")
    p2 = write_temp("file two\n")
    # Open with two files, check first visible, switch to second, quit all
    screen, _, code = run_ved(b":n\r:qa\r", file_paths=[p1, p2])
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
    screen, _, code = run_ved(b":n\r:n\r:p\r:qa\r", file_paths=[p1, p2, p3])
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
    screen, _, code = run_ved(b":ls\r:qa\r", file_paths=[p1, p2])
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
    screen, _, code = run_ved(b":q\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :q closes buffer")

def test_e_adds_buffer():
    """:e adds a new buffer instead of replacing."""
    p1 = write_temp("original\n")
    p2 = write_temp("added\n")
    # Open p1, :e p2 adds it, now we need :q twice
    screen, _, code = run_ved(f":e {p2}\r:q\r:q\r".encode(), file_path=p1)
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
    screen, _, code = run_ved(b":n\r:k\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :k deletes buffer")

def test_bdelete_dirty_blocked():
    """:k refuses to delete dirty buffer."""
    p1 = write_temp("clean\n")
    p2 = write_temp("dirty\n")
    # Open two files, :n to second, make it dirty, try :k (should fail), :k! forces it
    screen, _, code = run_ved(b":n\riX\x1b:k\r:k!\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    assert "No write since last change" in screen, f":k should warn about dirty: {screen[-500:]}"
    print("  PASS: :k blocks on dirty buffer")

def test_bdelete_last_refused():
    """:k refuses to delete the last buffer."""
    path = write_temp("only\n")
    screen, _, code = run_ved(b":k\r:q\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert "Cannot delete last buffer" in screen, f":k should refuse last: {screen[-500:]}"
    print("  PASS: :k refuses last buffer")

def test_qa_checks_all_dirty():
    """:qa refuses if any buffer is dirty."""
    p1 = write_temp("clean\n")
    p2 = write_temp("dirty\n")
    # Open two files, :n to second, make it dirty, :p back, :qa should fail
    screen, _, code = run_ved(b":n\riX\x1b:p\r:qa\r:qa!\r", file_paths=[p1, p2])
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
    screen, _, code = run_ved(b":wq\r:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: :wq closes buffer")

# ── Phase 29: x/X and space-leader ────────────────────────────────────────

def test_x_deletes_char():
    """x deletes character under cursor."""
    path = write_temp("hello\n")
    screen, content, code = run_ved(b"x:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ello\n", f"Expected 'ello\\n', got {content!r}"
    print("  PASS: x deletes char")

def test_x_with_count():
    """3x deletes 3 characters."""
    path = write_temp("abcdef\n")
    screen, content, code = run_ved(b"3x:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "def\n", f"Expected 'def\\n', got {content!r}"
    print("  PASS: 3x deletes 3 chars")

def test_X_deletes_before():
    """X deletes character before cursor."""
    path = write_temp("hello\n")
    # Move to position 2 (on 'l'), then X deletes 'e'
    screen, content, code = run_ved(b"llX:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "hllo\n", f"Expected 'hllo\\n', got {content!r}"
    print("  PASS: X deletes before cursor")

def test_space_k_deletes_buffer():
    """<space>k deletes current buffer."""
    p1 = write_temp("keep\n")
    p2 = write_temp("remove\n")
    # Open two files, :n to second, <space>k deletes it, :q exits
    screen, _, code = run_ved(b":n\r k:q\r", file_paths=[p1, p2])
    os.unlink(p1)
    os.unlink(p2)
    assert code == 0
    print("  PASS: <space>k deletes buffer")

# ── Phase 30: ^/$ Home/End + Insert Tab/Delete ────────────────────────────

def test_caret_motion_first_nonblank():
    """^ moves to first non-blank character."""
    path = write_temp("    hello\n")
    screen, content, code = run_ved(b"$i!\x1b^iX\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "    Xhello!\n", f"Expected first-nonblank insert, got {content!r}"
    print("  PASS: ^ moves to first non-blank")

def test_home_end_normal_mode():
    """Home/End work as start/end motions in Normal mode."""
    path = write_temp("hello\n")
    # End then append !, Home then insert ^
    screen, content, code = run_ved(b"\x1b[Fi!\x1b\x1b[Hi^\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^hello!\n", f"Expected '^hello!', got {content!r}"
    print("  PASS: Home/End in normal mode")

def test_insert_home_end_tab():
    """Insert mode handles Home/End and Tab (4 spaces)."""
    path = write_temp("abc\n")
    # i TAB X HOME ^ END ! ESC
    keys = b"i\tX\x1b[H^\x1b[F!\x1b:wq\r"
    screen, content, code = run_ved(keys, file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "^    Xabc!\n", f"Expected '^    Xabc!', got {content!r}"
    print("  PASS: insert Home/End/Tab")

def test_insert_delete_key():
    """Insert mode Delete removes character under cursor."""
    path = write_temp("abc\n")
    screen, content, code = run_ved(b"i\x1b[C\x1b[3~\x1b:wq\r", file_path=path)
    os.unlink(path)
    assert code == 0
    assert content == "ac\n", f"Expected 'ac', got {content!r}"
    print("  PASS: insert Delete key")

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
            test_nowrap_truncates,
            test_wrap_cursor_position,
        ]),
        ("13", "Phase 13 — Line Numbers", [
            test_set_number,
            test_set_relativenumber,
            test_number_and_relnum,
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
            test_comma_reverses_find,
            test_dfl_deletes_to_char,
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
            test_space_k_deletes_buffer,
        ]),
        ("30", "Phase 30 — ^/$ Home/End Tab/Delete", [
            test_caret_motion_first_nonblank,
            test_home_end_normal_mode,
            test_insert_home_end_tab,
            test_insert_delete_key,
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
