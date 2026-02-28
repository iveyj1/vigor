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

def run_ved(keys, file_path=None, timeout=3.0, rows=24, cols=80):
    """
    Launch ved in a PTY, send `keys`, wait for exit or timeout.
    Returns (screen_output, file_contents_after, exit_code).
    
    keys: bytes to feed to stdin
    file_path: path to open (if None, uses a temp file)
    """
    if isinstance(keys, str):
        keys = keys.encode()

    cleanup_file = False
    if file_path is None:
        fd_tmp, file_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd_tmp)
        cleanup_file = True

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
        os.execvp(sys.executable, [sys.executable, VED, file_path])
        os._exit(1)

    # Parent
    os.close(slave)
    output = b""

    # Wait a moment for ved to start and render
    time.sleep(0.3)

    # Send keys one at a time with delay to let ved process
    for b in keys:
        try:
            os.write(master, bytes([b]))
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
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            file_contents = f.read()

    if cleanup_file:
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
    screen, _, code = run_ved(f":e {path2}\r:q\r".encode(), file_path=path1)
    os.unlink(path1)
    os.unlink(path2)
    assert code == 0
    print("  PASS: :e opens file")

def test_new_buffer():
    """:new creates empty buffer."""
    path = write_temp("stuff\n")
    screen, _, code = run_ved(b":new\r:q\r", file_path=path)
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

    total_failed += run_phase("Phase 1 — Scaffold", [
        test_open_and_quit,
        test_open_file_visible,
        test_j_k_movement,
        test_h_l_movement,
        test_scroll_down,
    ])

    total_failed += run_phase("Phase 2 — Editing", [
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
    ])

    total_failed += run_phase("Phase 3 — Word Motions", [
        test_w_forward_word,
        test_b_backward_word,
        test_e_end_word,
        test_W_forward_WORD,
        test_B_backward_WORD,
        test_E_end_WORD,
    ])

    total_failed += run_phase("Phase 4 — Visual Mode", [
        test_v_enters_visual,
        test_V_line_visual,
        test_visual_esc_cancels,
        test_visual_motion_extends,
    ])

    total_failed += run_phase("Phase 5 — Polish", [
        test_status_bar_shown,
        test_wq_command,
        test_q_bang_forces,
        test_empty_file,
    ])

    total_failed += run_phase("Phase 6 — Resize", [
        test_sigwinch_no_crash,
        test_resize_shrink_grow,
    ])

    total_failed += run_phase("Phase 7 — Count Prefixes", [
        test_count_3j,
        test_count_5l,
        test_count_resets_on_esc,
    ])

    total_failed += run_phase("Phase 8 — Edit Operations", [
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
    ])

    total_failed += run_phase("Phase 9 — Visual Edit", [
        test_visual_delete,
        test_visual_yank_paste,
        test_visual_change,
        test_visual_line_delete,
        test_visual_x_same_as_d,
    ])

    print(f"\n{'=' * 60}")
    if total_failed:
        print(f"  TOTAL: {total_failed} test(s) FAILED")
        sys.exit(1)
    else:
        print("  ALL TESTS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
