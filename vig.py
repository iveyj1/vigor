#!/usr/bin/env python3

import sys
import os
import re
import base64
import termios
import tty
import atexit
import signal
import shutil
import select
import shlex
import time
from enum import Enum

# ── Modes ──────────────────────────────────────────────────────────────────

class Mode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    COMMAND = "COMMAND"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL LINE"
    SEARCH = "SEARCH"

# ── Buffer ─────────────────────────────────────────────────────────────────

class Buffer:
    __slots__ = ("lines", "path", "dirty")

    def __init__(self, path=None):
        self.path = path
        self.dirty = False
        if path and os.path.exists(path):
            with open(path, "r") as f:
                self.lines = f.read().splitlines()
            if not self.lines:
                self.lines = [""]
        else:
            self.lines = [""]

    def save(self, path=None):
        p = path or self.path
        if not p:
            return False
        with open(p, "w") as f:
            for line in self.lines:
                f.write(line + "\n")
        self.path = p
        self.dirty = False
        return True


class BufferState:
    """Per-buffer state: buffer content, cursor, scroll, and undo history."""
    __slots__ = ("buf", "cx", "cy", "scroll",
                 "_undo_stack", "_redo_stack",
                 "_undo_save_depth", "_undo_branched")

    def __init__(self, path=None):
        self.buf = Buffer(path)
        self.cx = 0
        self.cy = 0
        self.scroll = 0
        self._undo_stack = []
        self._redo_stack = []
        self._undo_save_depth = 0
        self._undo_branched = False

# ── Terminal ───────────────────────────────────────────────────────────────

class Terminal:
    """Raw mode management and key reading."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_attrs = termios.tcgetattr(self.fd)
        atexit.register(self.restore)

    def enter_raw(self):
        tty.setraw(self.fd)
        sys.stdout.write("\x1b[?2004h")  # enable bracketed paste
        sys.stdout.flush()

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_attrs)
        # Disable bracketed paste, show cursor, clear screen on exit
        sys.stdout.write("\x1b[?2004l\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()

    def suspend_restore(self):
        """Restore terminal state before job-control suspension."""
        attrs = [x[:] if isinstance(x, list) else x for x in self.old_attrs]
        attrs[0] |= termios.BRKINT | termios.ICRNL | termios.IXON
        attrs[0] &= ~(termios.IGNBRK | termios.INLCR | termios.IGNCR)
        attrs[1] |= termios.OPOST
        if hasattr(termios, "ONLCR"):
            attrs[1] |= termios.ONLCR
        attrs[2] |= termios.CREAD
        attrs[3] |= termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, attrs)
        sys.stdout.write("\x1b[?2004l\x1b[0 q\x1b[?25h")
        sys.stdout.flush()

    def read_key(self):
        """Read a single keypress. Decode escape sequences."""
        b = os.read(self.fd, 1)
        if not b:
            return ""
        ch = b[0]
        if ch == 0x1B:  # ESC
            # Try to read escape sequence incrementally.
            if not self._has_data():
                return "ESC"
            first = os.read(self.fd, 1)
            if first == b"[":
                if not self._has_data():
                    return "ESC"
                seq = bytearray()
                while True:
                    c = os.read(self.fd, 1)
                    if not c:
                        return "ESC"
                    seq.extend(c)
                    if 0x40 <= c[0] <= 0x7E:
                        break
                    if len(seq) > 16:
                        return "ESC"
                code = bytes(seq)
                if code == b"A":
                    return "UP"
                if code == b"B":
                    return "DOWN"
                if code == b"C":
                    return "RIGHT"
                if code == b"D":
                    return "LEFT"
                if code == b"H":
                    return "HOME"
                if code == b"F":
                    return "END"
                if code == b"200~":
                    return ("PASTE", self._read_bracketed_paste())
                if code in (b"1~", b"7~"):
                    return "HOME"
                if code in (b"4~", b"8~"):
                    return "END"
                if code == b"3~":
                    return "DEL"
                return "ESC"
            if first == b"O":
                if not self._has_data():
                    return "ESC"
                code = os.read(self.fd, 1)
                if code == b"H":
                    return "HOME"
                if code == b"F":
                    return "END"
                return "ESC"
            return "ESC"
        if ch == 127 or ch == 8:
            return "BACKSPACE"
        if ch == 13:
            return "ENTER"
        if ch == 9:
            return "TAB"
        if ch == 3:  # Ctrl-C
            return "CTRL_C"
        if ch == 4:  # Ctrl-D
            return "CTRL_D"
        if ch == 21:  # Ctrl-U
            return "CTRL_U"
        if ch == 18:  # Ctrl-R
            return "CTRL_R"
        if ch == 26:  # Ctrl-Z
            return "CTRL_Z"
        if ch < 32:
            return ""
        return chr(ch)

    def _read_bracketed_paste(self):
        """Read bytes until the bracketed-paste end marker."""
        end = b"\x1b[201~"
        data = bytearray()
        while True:
            b = os.read(self.fd, 1)
            if not b:
                break
            data.extend(b)
            if data.endswith(end):
                del data[-len(end):]
                break
        return bytes(data).decode("utf-8", errors="replace")

    def _has_data(self):
        """Check if stdin has data available (non-blocking)."""
        r, _, _ = select.select([self.fd], [], [], 0.02)
        return bool(r)

# ── Editor ─────────────────────────────────────────────────────────────────

class Editor:
    def __init__(self, paths=None):
        # Buffer list — always at least one buffer
        if paths:
            self.buffers = [BufferState(self._resolve_startup_path(p)) for p in paths]
        else:
            self.buffers = [BufferState()]
        self.buf_idx = 0
        # Load first buffer's state into working attributes
        bs = self.buffers[0]
        self.buf = bs.buf
        self.cx = bs.cx
        self.cy = bs.cy
        self.scroll = bs.scroll
        self._undo_stack = bs._undo_stack
        self._redo_stack = bs._redo_stack
        self._undo_save_depth = bs._undo_save_depth
        self._undo_branched = bs._undo_branched
        self.mode = Mode.NORMAL
        self.cmd = ""  # command-line input
        self.cmd_history = []
        self.search_history = []
        self._hist_idx = None
        self._hist_draft = ""
        self.comp_matches = []
        self.comp_index = 0
        self.comp_head = ""
        self.comp_token = ""
        self.comp_base_dir = ""
        self.comp_shell = False
        self.msg = ""  # status message
        self.vx = 0  # visual anchor column
        self.vy = 0  # visual anchor row
        self.rows = 24
        self.cols = 80
        self.running = True
        self.count = 0  # pending count prefix (0 = no count)
        self.pending_op = ""  # operator-pending: 'd', 'y', 'c', or ""
        self.pending_count = 0  # count saving when entering operator-pending
        self.pending_extra_n = None  # raw count for G/gg motions
        self.register = ""  # unnamed register (last yank/delete text)
        self.reg_linewise = False  # was last register content linewise?
        self.search_pattern = ""  # last / or ? search
        self.search_dir = 1  # 1=forward, -1=backward
        self.opt_wrap = False  # :set wrap
        self.opt_number = False  # :set number
        self.opt_relnum = False  # :set relativenumber
        self.opt_scrolloff = 0  # :set scrolloff=N
        self.opt_clipboard = "osc52"  # :set clipboard=osc52|auto|off
        self.opt_yankflash = 300  # :set yankflash=N milliseconds
        self.opt_delcopy = True  # :set delcopy/nodelcopy
        self.opt_wrapmove = False  # :set wrapmove/nowrapmove
        self.opt_rghidden = False  # :set rghidden/norghidden
        self._insert_word_count = 0 # WORD boundaries since last snapshot
        self._insert_last_space = True  # for WORD boundary counting
        self.last_find = None       # (cmd, ch) for f/t/F/T repeat
        self.opt_autoindent = True  # autoindent on Enter
        self.opt_comment = "#"      # comment character for toggle
        self._last_action = None    # (count, keys) for dot repeat
        self._recording_keys = []   # keys being recorded for dot
        self._recording = False     # currently recording for dot
        self._replaying_dot = False # currently replaying a dot action
        self._dot_count = 0         # count when recording started
        self._pending_g = False     # waiting for second key after 'g'
        self._pending_space = False # space-leader: waiting for next key
        self._pending_g_op = False  # 'g' prefix inside operator-pending
        self._pending_find = None   # 'f'/'t'/'F'/'T' waiting for char
        self._pending_find_for_op = None  # (cmd, ch) find for operator
        self._pending_textobj = None  # 'i'/'a' waiting for object key
        self._pending_replace = 0    # count for normal-mode r{char}
        self._pending_ctrl_c = False # Ctrl-C prefix for quit-all shortcuts
        self._pending_mkdir_write = None  # (path, close_after) waiting for y/n
        self._yank_flash = None     # (expires, sy, sx, ey, ex, linewise)
        self.quickfix_state = None  # BufferState holding last :rg results
        self.last_key = ""  # last decoded key read from terminal
        self._load_config()
        self.term = Terminal()
        self._update_size()

    def _format_exception_report(self, exc):
        """Build a plain-text crash report for unexpected exceptions."""
        lines = [
            "vig crash report",
            f"pid: {os.getpid()}",
            f"cwd: {os.getcwd()}",
            f"mode: {self.mode.value}",
            f"last_key: {self.last_key!r}",
            f"cursor: cy={self.cy} cx={self.cx}",
            f"exception: {exc.__class__.__name__}: {exc}",
            "traceback:",
        ]
        tb = exc.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            code = frame.f_code
            lines.append(
                f"  File \"{code.co_filename}\", line {tb.tb_lineno}, in {code.co_name}"
            )
            tb = tb.tb_next
        return "\n".join(lines) + "\n"

    def _write_crash_report(self, exc):
        """Persist crash report to disk. Returns report path or None."""
        report = self._format_exception_report(exc)
        candidates = [
            os.path.expanduser("~/.vig-crash.log"),
            os.path.abspath(".vig-crash.log"),
            "/tmp/vig-crash.log",
        ]
        for report_path in candidates:
            try:
                with open(report_path, "a") as f:
                    f.write(report)
                    f.write("\n")
                return report_path
            except Exception:
                continue
        return None

    @staticmethod
    def _resolve_startup_path(path):
        """Resolve command-line path with ~ and cwd semantics."""
        return os.path.abspath(os.path.expanduser(path))

    def _resolve_cmd_path(self, path):
        """Resolve :e/:w paths relative to current buffer directory."""
        p = os.path.expanduser(path.strip())
        if os.path.isabs(p):
            return os.path.normpath(p)
        if self.buf.path:
            return os.path.normpath(os.path.join(os.path.dirname(self.buf.path), p))
        return os.path.abspath(p)

    def _write_buffer_to_path(self, path, close_after=False):
        """Write current buffer, prompting first if parent directories are missing."""
        parent = os.path.dirname(path) or "."
        if parent and not os.path.isdir(parent):
            self._pending_mkdir_write = (path, close_after)
            self.msg = f'Create directory "{parent}"? (y/n)'
            self.mode = Mode.NORMAL
            return False
        try:
            if self.buf.save(path):
                self._undo_save_depth = len(self._undo_stack)
                self._undo_branched = False
                self._update_dirty()
                if close_after:
                    if len(self.buffers) > 1:
                        self._close_buffer()
                    else:
                        self.running = False
                else:
                    n = len(self.buf.lines)
                    self.msg = f'"{self.buf.path}" {n}L written'
            else:
                self.msg = "No file name"
        except OSError as e:
            self.msg = f"Can't write \"{path}\": {e.strerror or str(e)}"
        self.mode = Mode.NORMAL
        return True

    def _answer_mkdir_prompt(self, key):
        """Handle y/n answer for missing-directory write prompt."""
        if not self._pending_mkdir_write:
            return False
        path, close_after = self._pending_mkdir_write
        if key.lower() not in ("y", "n"):
            self.msg = "Create directory? (y/n)"
            return True
        self._pending_mkdir_write = None
        if key.lower() == "n":
            self.msg = "Write cancelled"
            return True
        parent = os.path.dirname(path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            self.msg = f"Can't create \"{parent}\": {e.strerror or str(e)}"
            return True
        self._write_buffer_to_path(path, close_after=close_after)
        return True

    def _reload_current_buffer(self):
        """Reload current buffer from disk, discarding unsaved changes."""
        if not self.buf.path:
            self.msg = "No file name"
            self.mode = Mode.NORMAL
            return
        else:
            try:
                with open(self.buf.path, "r") as f:
                    self.buf.lines = f.read().splitlines() or [""]
            except OSError as e:
                self.msg = f'Cannot reload "{self.buf.path}": {e.strerror or str(e)}'
                self.mode = Mode.NORMAL
                return
        self.buf.dirty = False
        self.cx = self.cy = self.scroll = 0
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._undo_save_depth = 0
        self._undo_branched = False
        self.msg = f'"{self.buf.path}" reloaded' if self.buf.path else "[No Name] reloaded"
        self.mode = Mode.NORMAL

    def _update_size(self):
        sz = shutil.get_terminal_size()
        self.cols = sz.columns
        self.rows = sz.lines - 2  # reserve 2 lines: status + command

    def _config_paths(self):
        """Return config files to read, in increasing precedence."""
        explicit = os.environ.get("VIG_CONFIG")
        if explicit:
            return [os.path.expanduser(explicit)]
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        return [
            os.path.expanduser("~/.vigrc"),
            os.path.join(xdg, "vig", "config"),
        ]

    def _load_config(self):
        """Load simple startup settings from ~/.vigrc or XDG config.
        Each non-empty, non-comment line is either `set <option>` or `<option>`.
        """
        if os.environ.get("VIG_NO_CONFIG"):
            return
        for path in self._config_paths():
            try:
                with open(path, "r") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                continue
            except OSError as e:
                self.msg = f"Config error {path}: {e.strerror or str(e)}"
                continue
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(":"):
                    line = line[1:].lstrip()
                if line.startswith("set "):
                    line = line[4:].strip()
                self._exec_set(line)

    def _suspend(self):
        """Suspend vig with Ctrl-Z, then restore raw mode on foreground."""
        self.term.suspend_restore()
        sys.stdout.write(f"\x1b[{self.rows + 2};1H\x1b[K")
        sys.stdout.flush()
        own_session = os.getsid(0)
        try:
            parent_session = os.getsid(os.getppid())
        except OSError:
            parent_session = None
        if parent_session == own_session:
            old_tstp = signal.getsignal(signal.SIGTSTP)
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            os.kill(0, signal.SIGTSTP)
            signal.signal(signal.SIGTSTP, old_tstp)
        else:
            # PTY tests often run vig as an orphaned process group, where
            # SIGTSTP may be discarded. SIGSTOP keeps this path testable.
            os.kill(os.getpid(), signal.SIGSTOP)
        self.term.enter_raw()
        self._update_size()
        self._clamp_cursor()
        self._ensure_scroll()
        self.msg = ""

    def _handle_resize(self):
        """Called on SIGWINCH. Update size, re-clamp, and redraw."""
        self._update_size()
        self._clamp_cursor()
        self._ensure_scroll()
        self.render()

    # ── Buffer management ──────────────────────────────────────────────

    def _save_buf_state(self):
        """Save working attributes back into current BufferState."""
        bs = self.buffers[self.buf_idx]
        bs.buf = self.buf
        bs.cx, bs.cy, bs.scroll = self.cx, self.cy, self.scroll
        bs._undo_stack = self._undo_stack
        bs._redo_stack = self._redo_stack
        bs._undo_save_depth = self._undo_save_depth
        bs._undo_branched = self._undo_branched

    def _load_buf_state(self, idx):
        """Load BufferState at idx into working attributes."""
        self.buf_idx = idx
        bs = self.buffers[idx]
        self.buf = bs.buf
        self.cx, self.cy, self.scroll = bs.cx, bs.cy, bs.scroll
        self._undo_stack = bs._undo_stack
        self._redo_stack = bs._redo_stack
        self._undo_save_depth = bs._undo_save_depth
        self._undo_branched = bs._undo_branched

    def _switch_buffer(self, idx):
        """Switch to buffer at idx, saving current state first."""
        if idx == self.buf_idx:
            return
        if idx < 0 or idx >= len(self.buffers):
            return
        self._save_buf_state()
        self._load_buf_state(idx)
        self._clamp_cursor()
        self._ensure_scroll()
        self.mode = Mode.NORMAL

    def _close_buffer(self):
        """Remove current buffer and load an adjacent one."""
        self._save_buf_state()
        old = self.buffers.pop(self.buf_idx)
        if old is self.quickfix_state:
            self.quickfix_state = None
        if self.buf_idx >= len(self.buffers):
            self.buf_idx = len(self.buffers) - 1
        self._load_buf_state(self.buf_idx)
        self._clamp_cursor()
        self._ensure_scroll()
        self.mode = Mode.NORMAL

    def _find_buffer_path(self, path):
        """Return index of an open buffer for path, else None."""
        target = os.path.abspath(path)
        for i, bs in enumerate(self.buffers):
            if bs.buf.path and os.path.abspath(bs.buf.path) == target:
                return i
        return None

    def _goto_file_location(self, path, line=1, col=1):
        """Open or switch to path, then move to 1-based line/column."""
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            self.msg = f'Cannot edit directory: "{path}"'
            return False
        idx = self._find_buffer_path(path)
        if idx is None:
            try:
                bs = BufferState(path)
            except OSError as e:
                self.msg = f'Cannot edit "{path}": {e.strerror or str(e)}'
                return False
            self._save_buf_state()
            self.buffers.insert(self.buf_idx + 1, bs)
            self._load_buf_state(self.buf_idx + 1)
        else:
            self._switch_buffer(idx)
        self.cy = max(0, line - 1)
        self.cx = max(0, col - 1)
        self._clamp_cursor()
        self._ensure_scroll()
        self.mode = Mode.NORMAL
        return True

    def _quit_all(self, force=False):
        """Quit all buffers, respecting dirty buffers unless forced."""
        if not force:
            dirty = [bs for bs in self.buffers if bs.buf.dirty]
            if dirty:
                self.msg = f"{len(dirty)} buffer(s) have unsaved changes (add ! to override)"
                self.mode = Mode.NORMAL
                return False
        self.running = False
        return True

    # ── Cursor clamping ────────────────────────────────────────────────

    def _clamp_cursor(self):
        # cy bounds
        if self.cy < 0:
            self.cy = 0
        if self.cy >= len(self.buf.lines):
            self.cy = len(self.buf.lines) - 1
        # cx bounds — allow cursor past end-of-line in all modes
        line_len = len(self.buf.lines[self.cy])
        if self.cx < 0:
            self.cx = 0
        if self.cx > line_len:
            self.cx = line_len

    def _ensure_scroll(self):
        """Adjust scroll so cursor is visible."""
        if not self.opt_wrap:
            max_scroll = max(0, len(self.buf.lines) - self.rows)
            margin = min(self.opt_scrolloff, max(0, (self.rows - 1) // 2))
            min_cy = self.scroll + margin
            max_cy = self.scroll + self.rows - 1 - margin
            if self.cy < min_cy:
                self.scroll = self.cy - margin
            elif self.cy > max_cy:
                self.scroll = self.cy - (self.rows - 1 - margin)
            if self.scroll < 0:
                self.scroll = 0
            if self.scroll > max_scroll:
                self.scroll = max_scroll
        else:
            if self.cy < self.scroll:
                self.scroll = self.cy
            # With wrap, count screen rows from scroll to cursor
            # If cursor line doesn't fit, scroll forward
            while True:
                screen_rows = 0
                for i in range(self.scroll, self.cy + 1):
                    screen_rows += self._line_screen_rows(i)
                if screen_rows <= self.rows:
                    break
                self.scroll += 1

    # ── Undo / Redo ───────────────────────────────────────────────────

    def _snapshot(self):
        """Save current state for undo. Call before any mutation."""
        current_depth = len(self._undo_stack)
        self._undo_stack.append((self.buf.lines[:], self.cx, self.cy))
        # If clearing redo discards the save point, mark branched
        if self._redo_stack and self._undo_save_depth > current_depth:
            self._undo_branched = True
        self._redo_stack.clear()
        # Limit stack size
        while len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
            self._undo_save_depth -= 1
            if self._undo_save_depth < 0:
                self._undo_branched = True

    def _undo(self):
        """Restore previous state from undo stack."""
        if not self._undo_stack:
            self.msg = "Already at oldest change"
            return
        self._redo_stack.append((self.buf.lines[:], self.cx, self.cy))
        self.buf.lines, self.cx, self.cy = self._undo_stack.pop()
        self._update_dirty()
        self._clamp_cursor()
        self._ensure_scroll()

    def _redo(self):
        """Restore next state from redo stack."""
        if not self._redo_stack:
            self.msg = "Already at newest change"
            return
        self._undo_stack.append((self.buf.lines[:], self.cx, self.cy))
        self.buf.lines, self.cx, self.cy = self._redo_stack.pop()
        self._update_dirty()
        self._clamp_cursor()
        self._ensure_scroll()

    def _update_dirty(self):
        """Recalculate dirty flag based on undo stack position."""
        if self._undo_branched:
            self.buf.dirty = True
        else:
            self.buf.dirty = len(self._undo_stack) != self._undo_save_depth

    def _enter_insert(self):
        """Enter insert mode, resetting word-count tracking."""
        self._insert_word_count = 0
        self._insert_last_space = True
        self.mode = Mode.INSERT

    def _open_line(self, below=True):
        """Open a new line below (o) or above (O) and enter insert mode."""
        indent = ""
        if self.opt_autoindent:
            line = self.buf.lines[self.cy]
            indent = line[:len(line) - len(line.lstrip())]
        if below:
            self.buf.lines.insert(self.cy + 1, indent)
            self.cy += 1
        else:
            self.buf.lines.insert(self.cy, indent)
        self.cx = len(indent)
        self.buf.dirty = True
        self._enter_insert()

    def _join_lines(self, count=2):
        """Join current line with the next (count-1) lines."""
        joins = max(1, count - 1)
        did_join = False
        for _ in range(joins):
            if self.cy >= len(self.buf.lines) - 1:
                break
            left = self.buf.lines[self.cy].rstrip()
            right = self.buf.lines[self.cy + 1].lstrip()
            sep = " " if left and right else ""
            self.buf.lines[self.cy] = left + sep + right
            del self.buf.lines[self.cy + 1]
            did_join = True
        if did_join:
            self.buf.dirty = True
            self.cx = min(self.cx, len(self.buf.lines[self.cy]))
        return did_join

    def _enter_op_pending(self, op, n, extra_n, dot=True):
        """Enter operator-pending mode for op, optionally starting dot recording."""
        if dot:
            self._start_dot(n, op)
        self.pending_op = op
        self.pending_count = n
        self.pending_extra_n = extra_n

    # ── Dot repeat helpers ─────────────────────────────────────────────

    def _start_dot(self, count, first_keys=None):
        """Start recording a dot-repeatable action.
        first_keys: list of keys already consumed for this action."""
        if not self._replaying_dot:
            self._recording = True
            self._recording_keys = list(first_keys) if first_keys else []
            self._dot_count = count

    def _save_dot(self):
        """Save the recorded keys as the last action."""
        if self._recording and not self._replaying_dot:
            self._recording = False
            self._last_action = (self._dot_count, self._recording_keys[:])

    def _dot_repeat(self, n, extra_n):
        """Replay the last change action."""
        if not self._last_action:
            return
        saved_count, keys = self._last_action
        use_count = n if extra_n is not None else saved_count
        self._replaying_dot = True
        self.count = use_count
        for key in keys:
            if self.mode == Mode.NORMAL:
                self.handle_normal(key)
            elif self.mode == Mode.INSERT:
                self.handle_insert(key)
        self._replaying_dot = False

    # ── Character classification for word motions ──────────────────────

    @staticmethod
    def _char_class(ch):
        """0=space, 1=word ([a-zA-Z0-9_]), 2=punct (everything else)."""
        if ch.isspace():
            return 0
        if ch.isalnum() or ch == "_":
            return 1
        return 2

    @staticmethod
    def _WORD_class(ch):
        """0=space, 1=non-space."""
        return 0 if ch.isspace() else 1

    def _flat_pos(self):
        """Return (cy, cx) as a flat index into the buffer for iteration."""
        return self.cy, self.cx

    def _get_char(self, y, x):
        """Get character at position, or None if out of bounds."""
        if y < 0 or y >= len(self.buf.lines):
            return None
        line = self.buf.lines[y]
        if x < 0 or x >= len(line):
            return None
        return line[x]

    def _forward(self, y, x):
        """Move one position forward. Returns (y, x) or None at end."""
        line = self.buf.lines[y]
        if x + 1 < len(line):
            return y, x + 1
        if y + 1 < len(self.buf.lines):
            return y + 1, 0
        return None

    def _backward(self, y, x):
        """Move one position backward. Returns (y, x) or None at start."""
        if x > 0:
            return y, x - 1
        if y > 0:
            prev_len = len(self.buf.lines[y - 1])
            return y - 1, max(prev_len - 1, 0)
        return None

    def motion_w(self, big=False):
        """Move to start of next word (w) or WORD (W)."""
        classify = self._WORD_class if big else self._char_class
        pos = (self.cy, self.cx)
        ch = self._get_char(*pos)
        cur_class = classify(ch) if ch is not None else 0
        # Skip current non-space class.
        if cur_class != 0:
            while pos:
                py, px = pos
                c = self._get_char(*pos)
                if c is None or classify(c) != cur_class:
                    break
                nxt = self._forward(*pos)
                if nxt is None:
                    pos = None
                    break
                # Newline is always a word boundary, even if the next line
                # starts with the same character class.
                if nxt[0] != py:
                    pos = nxt
                    break
                pos = nxt
        # Skip spaces
        while pos:
            c = self._get_char(*pos)
            if c is not None and classify(c) != 0:
                break
            pos = self._forward(*pos)
        if pos:
            self.cy, self.cx = pos

    def motion_b(self, big=False):
        """Move to start of previous word (b) or WORD (B)."""
        classify = self._WORD_class if big else self._char_class
        # Step back one position first
        pos = self._backward(self.cy, self.cx)
        if pos is None:
            return
        # Skip spaces
        while pos:
            c = self._get_char(*pos)
            if c is not None and classify(c) != 0:
                break
            pos = self._backward(*pos)
        if pos is None:
            self.cy, self.cx = 0, 0
            return
        # Now on the last char of the prev word — find its start
        target_char = self._get_char(*pos)
        if target_char is None:
            return
        target_class = classify(target_char)
        while True:
            py, px = pos
            prev = self._backward(*pos)
            if prev is None:
                break
            if prev[0] != py:
                break
            c = self._get_char(*prev)
            if c is None or classify(c) != target_class:
                break
            pos = prev
        self.cy, self.cx = pos

    def motion_e(self, big=False):
        """Move to end of word (e) or WORD (E)."""
        classify = self._WORD_class if big else self._char_class
        line = self.buf.lines[self.cy]
        # Cursor can sit one past EOL in vig. In this state, land on the
        # last non-space token on the current line before crossing lines.
        if self.cx >= len(line):
            i = len(line) - 1
            while i >= 0 and classify(line[i]) == 0:
                i -= 1
            if i >= 0:
                self.cx = i
                return
        # Step forward one position first
        pos = self._forward(self.cy, self.cx)
        if pos is None:
            return
        # Skip spaces
        while pos:
            c = self._get_char(*pos)
            if c is not None and classify(c) != 0:
                break
            pos = self._forward(*pos)
        if pos is None:
            return
        # Now on the first char of a word — find its end
        target_char = self._get_char(*pos)
        if target_char is None:
            return
        target_class = classify(target_char)
        while True:
            py, px = pos
            nxt = self._forward(*pos)
            if nxt is None:
                break
            if nxt[0] != py:
                break
            c = self._get_char(*nxt)
            if c is None or classify(c) != target_class:
                break
            pos = nxt
        self.cy, self.cx = pos

    # ── Motion dispatch (shared by normal, visual, operator-pending) ──

    _MOTION_KEYS = frozenset(
        "h l j k w W b B e E G 0 ^ $".split()
        + ["LEFT", "RIGHT", "DOWN", "UP", "HOME", "END", "gg", "CTRL_D", "CTRL_U"]
    )

    def _motion_h(self):
        self.cx -= 1
        self._clamp_cursor()

    def _motion_l(self):
        self.cx += 1
        self._clamp_cursor()

    def _wrap_cols(self):
        return max(1, self.cols - self._gutter_width())

    def _motion_display_row(self, delta):
        """Move by one displayed row when wrapmove is enabled."""
        if not (self.opt_wrap and self.opt_wrapmove):
            self.cy += delta
            self._clamp_cursor()
            return
        cols = self._wrap_cols()
        row = self.cx // cols
        col = self.cx % cols
        if delta > 0:
            rows = self._line_screen_rows(self.cy)
            if row + 1 < rows:
                self.cx = min((row + 1) * cols + col, len(self.buf.lines[self.cy]))
            elif self.cy < len(self.buf.lines) - 1:
                self.cy += 1
                self.cx = min(col, len(self.buf.lines[self.cy]))
        else:
            if row > 0:
                self.cx = (row - 1) * cols + col
            elif self.cy > 0:
                self.cy -= 1
                prev_rows = self._line_screen_rows(self.cy)
                self.cx = min((prev_rows - 1) * cols + col, len(self.buf.lines[self.cy]))
        self._clamp_cursor()

    def _motion_j(self):
        self._motion_display_row(1)

    def _motion_k(self):
        self._motion_display_row(-1)

    def _motion_G_count(self, n, extra_n):
        self.cy = min(n - 1, len(self.buf.lines) - 1) if extra_n is not None else len(self.buf.lines) - 1
        self.cx = 0

    def _motion_gg_count(self, n, extra_n):
        self.cy = min(n - 1, len(self.buf.lines) - 1) if extra_n is not None else 0
        self.cx = 0

    def _motion_zero(self):
        self.cx = 0

    def _motion_caret(self):
        line = self.buf.lines[self.cy]
        self.cx = len(line) - len(line.lstrip())

    def _motion_dollar(self):
        self.cx = len(self.buf.lines[self.cy])

    def _motion_home(self):
        self.cx = 0

    def _motion_end(self):
        self.cx = len(self.buf.lines[self.cy])

    def _motion_ctrl_d(self):
        half = max(1, self.rows // 2)
        self.cy = min(len(self.buf.lines) - 1, self.cy + half)

    def _motion_ctrl_u(self):
        half = max(1, self.rows // 2)
        self.cy = max(0, self.cy - half)

    def _exec_motion(self, key, n=1, extra_n=None):
        """Execute a motion key n times. Returns True if key was a motion.
        extra_n is the raw count (None if no count given) for motions like G/gg."""
        if key not in self._MOTION_KEYS:
            return False
        handlers = {
            "h": self._motion_h,
            "LEFT": self._motion_h,
            "l": self._motion_l,
            "RIGHT": self._motion_l,
            "j": self._motion_j,
            "DOWN": self._motion_j,
            "k": self._motion_k,
            "UP": self._motion_k,
            "w": lambda: self.motion_w(big=False),
            "W": lambda: self.motion_w(big=True),
            "b": lambda: self.motion_b(big=False),
            "B": lambda: self.motion_b(big=True),
            "e": lambda: self.motion_e(big=False),
            "E": lambda: self.motion_e(big=True),
            "G": lambda: self._motion_G_count(n, extra_n),
            "gg": lambda: self._motion_gg_count(n, extra_n),
            "0": self._motion_zero,
            "^": self._motion_caret,
            "$": self._motion_dollar,
            "HOME": self._motion_home,
            "END": self._motion_end,
            "CTRL_D": self._motion_ctrl_d,
            "CTRL_U": self._motion_ctrl_u,
        }
        repeat = 1 if key in ("G", "gg", "0", "^", "$", "HOME", "END") else n
        for _ in range(repeat):
            handlers[key]()
        return True

    # ── Find character motions (f/t/F/T) ─────────────────────────────

    def _motion_f(self, ch, n=1):
        """Move to nth occurrence of ch to the right on current line."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.find(ch, pos + 1)
            if idx == -1:
                return
            pos = idx
        self.cx = pos

    def _motion_F(self, ch, n=1):
        """Move to nth occurrence of ch to the left on current line."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.rfind(ch, 0, pos)
            if idx == -1:
                return
            pos = idx
        self.cx = pos

    def _motion_t(self, ch, n=1):
        """Move to just before nth occurrence of ch to the right."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.find(ch, pos + 1)
            if idx == -1:
                return
            pos = idx
        self.cx = pos - 1 if pos > 0 else 0

    def _motion_T(self, ch, n=1):
        """Move to just after nth occurrence of ch to the left."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.rfind(ch, 0, pos)
            if idx == -1:
                return
            pos = idx
        self.cx = pos + 1

    _FIND_DISPATCH = {"f": "_motion_f", "F": "_motion_F",
                       "t": "_motion_t", "T": "_motion_T"}
    _FIND_REVERSE = {"f": "F", "F": "f", "t": "T", "T": "t"}

    def _exec_find(self, cmd, ch, n=1):
        """Execute a find-char motion and save for repeat."""
        self.last_find = (cmd, ch)
        getattr(self, self._FIND_DISPATCH[cmd])(ch, n)

    def _repeat_find(self, reverse=False, n=1):
        """Repeat last f/t/F/T. If reverse, swap direction."""
        if not self.last_find:
            return
        cmd, ch = self.last_find
        if reverse:
            cmd = self._FIND_REVERSE[cmd]
        elif cmd in ("t", "T"):
            # For till motions, skip the previously matched char on repeat.
            n += 1
        getattr(self, self._FIND_DISPATCH[cmd])(ch, n)

    # ── Match bracket (%) ────────────────────────────────────────────

    _BRACKETS = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{"}
    _OPEN_BRACKETS = frozenset("([{")

    def _motion_percent(self):
        """Move to matching bracket."""
        line = self.buf.lines[self.cy]
        if self.cx >= len(line):
            return
        ch = line[self.cx]
        if ch not in self._BRACKETS:
            # Scan forward on current line for a bracket
            for i in range(self.cx + 1, len(line)):
                if line[i] in self._BRACKETS:
                    self.cx = i
                    ch = line[i]
                    break
            else:
                return
        match = self._BRACKETS[ch]
        forward = ch in self._OPEN_BRACKETS
        depth = 1
        y, x = self.cy, self.cx
        while depth > 0:
            if forward:
                x += 1
                if x >= len(self.buf.lines[y]):
                    y += 1
                    x = 0
                if y >= len(self.buf.lines):
                    return
            else:
                x -= 1
                if x < 0:
                    y -= 1
                    if y < 0:
                        return
                    x = len(self.buf.lines[y]) - 1
                    if x < 0:
                        continue
            c = self.buf.lines[y][x] if x < len(self.buf.lines[y]) else ""
            if c == ch:
                depth += 1
            elif c == match:
                depth -= 1
        self.cy, self.cx = y, x

    # ── Indent / Dedent ──────────────────────────────────────────────

    def _indent_lines(self, start, count):
        """Add 4 spaces to beginning of count lines starting at start."""
        for i in range(start, min(start + count, len(self.buf.lines))):
            self.buf.lines[i] = "    " + self.buf.lines[i]
        self.buf.dirty = True

    def _dedent_lines(self, start, count):
        """Remove up to 4 leading spaces from count lines starting at start."""
        for i in range(start, min(start + count, len(self.buf.lines))):
            line = self.buf.lines[i]
            remove = 0
            while remove < 4 and remove < len(line) and line[remove] == " ":
                remove += 1
            if remove > 0:
                self.buf.lines[i] = line[remove:]
        self.buf.dirty = True

    def _toggle_comment(self, start, count):
        """Toggle line comments using opt_comment prefix."""
        prefix = self.opt_comment + " "
        end = min(start + count, len(self.buf.lines))
        lines = self.buf.lines[start:end]
        # If all non-empty lines are commented, uncomment; otherwise comment
        all_commented = all(
            ln.lstrip().startswith(self.opt_comment) or ln.strip() == ""
            for ln in lines
        )
        for i in range(start, end):
            line = self.buf.lines[i]
            if all_commented:
                # Remove first occurrence of comment prefix
                stripped = line.lstrip()
                indent = line[:len(line) - len(stripped)]
                if stripped.startswith(prefix):
                    self.buf.lines[i] = indent + stripped[len(prefix):]
                elif stripped.startswith(self.opt_comment):
                    self.buf.lines[i] = indent + stripped[len(self.opt_comment):]
            else:
                if line.strip():  # don't comment empty lines
                    indent = line[:len(line) - len(line.lstrip())]
                    self.buf.lines[i] = indent + prefix + line.lstrip()
        self.buf.dirty = True

    # ── Text object helpers ──────────────────────────────────────────

    def _find_word_object(self, big=False, around=False):
        """Return (sy, sx, ey, ex) for inner/around word at cursor."""
        classify = self._WORD_class if big else self._char_class
        ch = self._get_char(self.cy, self.cx)
        if ch is None:
            return None
        cur_class = classify(ch)
        # Find start of word
        sx = self.cx
        while sx > 0:
            c = self._get_char(self.cy, sx - 1)
            if c is None or classify(c) != cur_class:
                break
            sx -= 1
        # Find end of word
        ex = self.cx
        line = self.buf.lines[self.cy]
        while ex + 1 < len(line):
            c = self._get_char(self.cy, ex + 1)
            if c is None or classify(c) != cur_class:
                break
            ex += 1
        ex += 1  # exclusive end
        if around:
            # Include trailing spaces, or leading if no trailing
            while ex < len(line) and line[ex] == " ":
                ex += 1
            if ex == self.cx + 1:  # no trailing, try leading
                while sx > 0 and line[sx - 1] == " ":
                    sx -= 1
        return self.cy, sx, self.cy, ex

    def _find_bracket_object(self, open_ch, close_ch, around=False):
        """Return (sy, sx, ey, ex) for inner/around bracket pair."""
        # Search backward for opening bracket
        depth = 0
        y, x = self.cy, self.cx
        # Check if cursor is on a bracket
        found = False
        while True:
            if y < 0:
                return None
            line = self.buf.lines[y]
            while x >= 0:
                if x < len(line):
                    c = line[x]
                    if c == close_ch:
                        depth += 1
                    elif c == open_ch:
                        if depth == 0:
                            found = True
                            break
                        depth -= 1
                x -= 1
            if found:
                break
            y -= 1
            if y < 0:
                return None
            x = len(self.buf.lines[y]) - 1

        oy, ox = y, x  # opening bracket position
        # Search forward for closing bracket
        depth = 0
        y, x = oy, ox + 1
        found = False
        while y < len(self.buf.lines):
            line = self.buf.lines[y]
            while x < len(line):
                c = line[x]
                if c == open_ch:
                    depth += 1
                elif c == close_ch:
                    if depth == 0:
                        found = True
                        break
                    depth -= 1
                x += 1
            if found:
                break
            y += 1
            x = 0

        if not found:
            return None
        cy, cx = y, x  # closing bracket position
        if around:
            return oy, ox, cy, cx + 1
        else:
            # Inner: from char after open to char before close
            sx, sy2 = ox + 1, oy
            ex, ey2 = cx, y
            return sy2, sx, ey2, ex

    def _find_quote_object(self, quote_ch, around=False):
        """Return (sy, sx, ey, ex) for inner/around quote pair on current line."""
        line = self.buf.lines[self.cy]
        # Find pairs of quotes on current line
        positions = [i for i, c in enumerate(line) if c == quote_ch]
        if len(positions) < 2:
            return None
        # Find which pair the cursor is inside
        for i in range(0, len(positions) - 1, 2):
            start, end = positions[i], positions[i + 1]
            if start <= self.cx <= end:
                if around:
                    return self.cy, start, self.cy, end + 1
                else:
                    return self.cy, start + 1, self.cy, end
        return None

    # ── Visual selection helpers ─────────────────────────────────────

    def _selection_range(self):
        """Return (start_y, start_x, end_y, end_x) for current selection.
        Returns None if not in a visual mode."""
        if self.mode not in (Mode.VISUAL, Mode.VISUAL_LINE):
            return None
        ay, ax = self.vy, self.vx
        by, bx = self.cy, self.cx
        if (ay, ax) > (by, bx):
            ay, ax, by, bx = by, bx, ay, ax
        if self.mode == Mode.VISUAL_LINE:
            ax = 0
            bx = len(self.buf.lines[by]) if by < len(self.buf.lines) else 0
        return ay, ax, by, bx

    # ── Rendering ──────────────────────────────────────────────────────

    def _gutter_width(self):
        """Width of line number gutter (0 if line numbers disabled)."""
        if not self.opt_number and not self.opt_relnum:
            return 0
        return max(3, len(str(len(self.buf.lines)))) + 1

    def _gutter_str(self, buf_line, gutter_width):
        """Format the line number string for a given buffer line."""
        if gutter_width == 0:
            return ""
        if self.opt_relnum:
            if buf_line == self.cy:
                num = (buf_line + 1) if self.opt_number else 0
            else:
                num = abs(buf_line - self.cy)
        else:
            num = buf_line + 1
        num_s = str(num)
        pad = gutter_width - 1 - len(num_s)
        if self.opt_relnum and buf_line == self.cy and pad > 0:
            return " " * (pad - 1) + num_s + "  "
        return " " * max(0, pad) + num_s + " "

    def _line_screen_rows(self, line_idx):
        """How many screen rows does buffer line `line_idx` occupy?"""
        if not self.opt_wrap or self.cols == 0:
            return 1
        content_cols = self.cols - self._gutter_width()
        if content_cols <= 0:
            return 1
        line_len = len(self.buf.lines[line_idx]) if line_idx < len(self.buf.lines) else 0
        if line_len == 0:
            return 1
        return (line_len + content_cols - 1) // content_cols

    def _render_line(self, line, buf_line, sel, out, gutter_width=0, max_rows=None, hscroll=0):
        """Render a single buffer line (possibly wrapped). Returns number of screen rows used.
        max_rows limits output to at most that many screen rows (for partial rendering)."""
        gutter = self._gutter_str(buf_line, gutter_width)
        gutter_pad = " " * gutter_width
        content_cols = self.cols - gutter_width
        if content_cols <= 0:
            content_cols = 1
        if not self.opt_wrap:
            hscroll = max(0, hscroll)
            visible = line[hscroll:hscroll + content_cols]
            out.append(gutter)
            self._render_visible(visible, buf_line, hscroll, sel, out)
            out.append("\x1b[K\r\n")
            return 1
        else:
            # Wrap: split line into chunks of content_cols
            if not line:
                out.append(gutter)
                self._render_visible("", buf_line, 0, sel, out)
                out.append("\x1b[K\r\n")
                return 1
            rows_used = 0
            for chunk_start in range(0, len(line), content_cols):
                if max_rows is not None and rows_used >= max_rows:
                    break
                chunk = line[chunk_start:chunk_start + content_cols]
                if rows_used == 0:
                    out.append(gutter)
                else:
                    out.append(gutter_pad)
                self._render_visible(chunk, buf_line, chunk_start, sel, out)
                out.append("\x1b[K\r\n")
                rows_used += 1
            return rows_used

    def _render_visible(self, visible, buf_line, col_offset, sel, out):
        """Render a visible string segment with optional selection highlight."""
        if sel:
            sy, sx, ey, ex = sel
            if sy <= buf_line <= ey:
                hl_start = (sx - col_offset) if buf_line == sy else 0
                hl_end = (ex - col_offset) if buf_line == ey else len(visible)
                hl_start = max(0, min(hl_start, len(visible)))
                hl_end = max(0, min(hl_end, len(visible)))
                before = visible[:hl_start]
                highlighted = visible[hl_start:hl_end]
                after = visible[hl_end:]
                out.append(before)
                out.append("\x1b[7m")
                out.append(highlighted)
                out.append("\x1b[m")
                out.append(after)
                return
        out.append(visible)

    def render(self):
        out = []
        out.append("\x1b[?25l")  # hide cursor
        out.append("\x1b[H")     # cursor home

        sel = self._selection_range()
        if sel is None and self._yank_flash:
            expires, sy, sx, ey, ex, linewise = self._yank_flash
            if time.monotonic() < expires:
                if linewise:
                    sel = (sy, 0, ey, len(self.buf.lines[ey]))
                else:
                    sel = (sy, sx, ey, ex)
            else:
                self._yank_flash = None
        gw = self._gutter_width()
        content_cols = max(1, self.cols - gw)

        screen_rows_used = 0
        cursor_screen_y = 0
        cursor_screen_x = self.cx + gw
        buf_line = self.scroll
        window_hscroll = 0 if self.opt_wrap else max(0, self.cx - content_cols + 1)
        comp_rows = min(len(self.comp_matches), max(0, self.rows - 1)) if self.mode == Mode.COMMAND else 0
        content_limit = self.rows - comp_rows

        while screen_rows_used < content_limit and buf_line < len(self.buf.lines):
            line = self.buf.lines[buf_line]
            if buf_line == self.cy:
                # Track cursor screen position
                if self.opt_wrap and content_cols > 0:
                    wrap_row = self.cx // content_cols
                    cursor_screen_y = screen_rows_used + wrap_row
                    cursor_screen_x = self.cx % content_cols + gw
                else:
                    cursor_screen_y = screen_rows_used
                    cursor_screen_x = self.cx - window_hscroll + gw

            rows_available = content_limit - screen_rows_used
            if self.opt_wrap:
                used = self._render_line(line, buf_line, sel, out, gw, max_rows=rows_available)
                screen_rows_used += used
            else:
                self._render_line(line, buf_line, sel, out, gw, hscroll=window_hscroll)
                screen_rows_used += 1
            buf_line += 1

        # Fill remaining content rows with tildes
        while screen_rows_used < content_limit:
            out.append("~")
            out.append("\x1b[K\r\n")
            screen_rows_used += 1

        if comp_rows:
            start = max(0, min(self.comp_index - comp_rows + 1, len(self.comp_matches) - comp_rows))
            for i, name in enumerate(self.comp_matches[start:start + comp_rows], start):
                text = name[:self.cols]
                if i == self.comp_index:
                    out.append("\x1b[7m" + text + "\x1b[m")
                else:
                    out.append(text)
                out.append("\x1b[K\r\n")
                screen_rows_used += 1

        # Status bar (reverse video)
        out.append("\x1b[7m")
        fname = self.buf.path or "[No Name]"
        dirty = " [+]" if self.buf.dirty else ""
        mode_str = self.mode.value
        count_str = str(self.count) if self.count > 0 else ""
        buf_info = f"[{self.buf_idx + 1}/{len(self.buffers)}] " if len(self.buffers) > 1 else ""
        left = f" {mode_str} | {buf_info}{fname}{dirty}"
        right = f" {count_str} {self.cy + 1}:{self.cx + 1} "
        pad = self.cols - len(left) - len(right)
        if pad < 0:
            pad = 0
        status = left + " " * pad + right
        out.append(status[:self.cols])
        out.append("\x1b[m")  # reset
        out.append("\x1b[K\r\n")

        # Command / message bar
        if self.mode == Mode.COMMAND:
            cmd_display = ":" + self.cmd
            out.append(cmd_display[:self.cols])
        elif self.mode == Mode.SEARCH:
            prompt = "/" if self.search_dir == 1 else "?"
            cmd_display = prompt + self.cmd
            out.append(cmd_display[:self.cols])
        else:
            out.append(self.msg[:self.cols] if self.msg else "")
        out.append("\x1b[K")

        # Cursor shape: block for normal/visual/command, bar for insert
        if self.mode == Mode.INSERT:
            out.append("\x1b[6 q")  # steady bar
        else:
            out.append("\x1b[2 q")  # steady block

        # Position real cursor (use tracked values from render loop)
        screen_y = cursor_screen_y + 1  # 1-indexed
        screen_x = cursor_screen_x + 1  # 1-indexed
        out.append(f"\x1b[{screen_y};{screen_x}H")
        out.append("\x1b[?25h")  # show cursor

        sys.stdout.write("".join(out))
        sys.stdout.flush()

    # ── Clipboard (OSC 52) ─────────────────────────────────────────

    def _osc52_copy(self, text):
        """Copy text to system clipboard via OSC 52 escape sequence."""
        encoded = base64.b64encode(text.encode()).decode()
        sys.stdout.write(f"\x1b]52;c;{encoded}\x07")
        sys.stdout.flush()

    def _external_clipboard_cmd(self):
        """Return first available external clipboard command or None."""
        if shutil.which("pbcopy"):
            return ["pbcopy"]
        if shutil.which("wl-copy"):
            return ["wl-copy"]
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
        if shutil.which("clip.exe"):
            return ["clip.exe"]
        return None

    def _external_copy(self, text):
        """Try copying via external clipboard command. Returns bool success."""
        cmd = self._external_clipboard_cmd()
        if not cmd:
            return False
        try:
            import subprocess
            subprocess.run(cmd, input=text, text=True, check=False, timeout=1)
            return True
        except Exception:
            return False

    def _copy_to_system_clipboard(self, text):
        """Copy using configured clipboard mode. Never raises."""
        mode = self.opt_clipboard
        if mode == "off":
            return
        if mode == "osc52":
            try:
                self._osc52_copy(text)
            except Exception:
                pass
            return
        if mode == "auto":
            try:
                self._osc52_copy(text)
            except Exception:
                pass
            self._external_copy(text)

    def _set_register(self, text, linewise=False):
        """Store text in unnamed register and copy to system clipboard."""
        self.register = text
        self.reg_linewise = linewise
        self._copy_to_system_clipboard(text)

    def _flash_yank(self, sy, sx, ey, ex, linewise=False):
        """Briefly highlight freshly yanked text."""
        if self.opt_yankflash <= 0:
            return
        self._yank_flash = (time.monotonic() + self.opt_yankflash / 1000.0, sy, sx, ey, ex, linewise)
        self.render()

    # ── Operator-pending motion execution ──────────────────────────────

    def _apply_motion(self, motion_key, n, extra_n=None):
        """Execute a motion n times from current position.
        Returns (new_cy, new_cx) without modifying cursor.
        Also handles find-char motions stored in _pending_find_for_op."""
        saved_cy, saved_cx = self.cy, self.cx
        if self._pending_find_for_op:
            cmd, ch = self._pending_find_for_op
            self._pending_find_for_op = None
            self._exec_find(cmd, ch, n)
        elif not self._exec_motion(motion_key, n, extra_n=extra_n):
            return None
        result = (self.cy, self.cx)
        self.cy, self.cx = saved_cy, saved_cx
        return result

    def _is_linewise_motion(self, key):
        """j, k, G, gg, and doubled operators are linewise."""
        return key in ("j", "k", "DOWN", "UP", "G", "gg", "CTRL_D", "CTRL_U")

    # ── Delete/Yank/Change helpers ─────────────────────────────────────

    def _delete_range(self, sy, sx, ey, ex, linewise=False, copy=True):
        """Delete text from (sy,sx) to (ey,ex). Returns deleted text."""
        if not linewise and (sy, sx) == (ey, ex):
            return ""
        if linewise:
            # Delete entire lines sy..ey
            deleted = self.buf.lines[sy:ey + 1]
            text = "\n".join(deleted)
            del self.buf.lines[sy:ey + 1]
            if not self.buf.lines:
                self.buf.lines = [""]
            self.cy = min(sy, len(self.buf.lines) - 1)
            self.cx = 0
            if copy:
                self._set_register(text, linewise=True)
        else:
            # Character-wise delete
            if sy == ey:
                line = self.buf.lines[sy]
                text = line[sx:ex]
                self.buf.lines[sy] = line[:sx] + line[ex:]
            else:
                first = self.buf.lines[sy]
                last = self.buf.lines[ey]
                text = first[sx:]
                for mid_y in range(sy + 1, ey):
                    text += "\n" + self.buf.lines[mid_y]
                text += "\n" + last[:ex]
                # Now rebuild: keep first[:sx] + last[ex:], delete middle
                self.buf.lines[sy] = first[:sx] + last[ex:]
                del self.buf.lines[sy + 1:ey + 1]
            self.cy = sy
            self.cx = sx
            if copy:
                self._set_register(text, linewise=False)
        self.buf.dirty = True
        self._clamp_cursor()
        return text

    def _delete_lines(self, start, count):
        """Delete `count` lines starting at `start`."""
        end = min(start + count - 1, len(self.buf.lines) - 1)
        return self._delete_range(start, 0, end, 0, linewise=True)

    def _yank_range(self, sy, sx, ey, ex, linewise=False):
        """Yank text from (sy,sx) to (ey,ex) without deleting."""
        if linewise:
            text = "\n".join(self.buf.lines[sy:ey + 1])
            self._set_register(text, linewise=True)
            self._flash_yank(sy, 0, ey, len(self.buf.lines[ey]), linewise=True)
        else:
            if sy == ey:
                text = self.buf.lines[sy][sx:ex]
            else:
                parts = [self.buf.lines[sy][sx:]]
                for mid_y in range(sy + 1, ey):
                    parts.append(self.buf.lines[mid_y])
                parts.append(self.buf.lines[ey][:ex])
                text = "\n".join(parts)
            self._set_register(text, linewise=False)
            self._flash_yank(sy, sx, ey, ex, linewise=False)
        return text

    def _delete_to_eol(self):
        """Delete from cursor to end of line, store in register."""
        line = self.buf.lines[self.cy]
        text = line[self.cx:]
        self.buf.lines[self.cy] = line[:self.cx]
        self._set_register(text, linewise=False)
        self.buf.dirty = True
        return text

    def _exec_operator(self, op, motion_key, n, extra_n=None):
        """Execute operator (d/y/c) with a motion."""
        linewise = self._is_linewise_motion(motion_key)
        target = self._apply_motion(motion_key, n, extra_n=extra_n)
        if target is None:
            return
        ty, tx = target
        sy, sx = self.cy, self.cx
        # Normalize range
        if (sy, sx) > (ty, tx):
            sy, sx, ty, tx = ty, tx, sy, sx
        # Inclusive motions (e, E, f, t): include the end character
        if motion_key in ("e", "E", "f", "t"):
            tx += 1
            if not linewise and ty < len(self.buf.lines):
                tx = min(tx, len(self.buf.lines[ty]))

        if not linewise and sy != ty and motion_key in ("w", "W"):
            ty = sy
            tx = len(self.buf.lines[sy])

        if op == "d":
            self._delete_range(sy, sx, ty, tx, linewise, copy=self.opt_delcopy)
        elif op == "yd":
            self._delete_range(sy, sx, ty, tx, linewise, copy=True)
        elif op == "y":
            self._yank_range(sy, sx, ty, tx, linewise)
            self.msg = f"{ty - sy + 1} lines yanked" if linewise else "yanked"
        elif op == "c":
            self._delete_range(sy, sx, ty, tx, linewise)
            self._enter_insert()

    # ── Normal mode ────────────────────────────────────────────────────

    def handle_normal(self, key):
        if self._pending_ctrl_c:
            self._pending_ctrl_c = False
            if key == "CTRL_C":
                self._quit_all(force=False)
                return
            if key == "q":
                self._quit_all(force=True)
                return

        if key == "CTRL_C":
            self._pending_ctrl_c = True
            self.msg = "^C"
            return

        # r{char}: replace character(s) under cursor. This must run before
        # count-prefix parsing so digits can be replacement characters.
        if self._pending_replace:
            repl_n = self._pending_replace
            self._pending_replace = 0
            if key == "ESC":
                self._recording = False
                self._recording_keys = []
                return
            if len(key) == 1 and self.cx < len(self.buf.lines[self.cy]):
                self._snapshot()
                line = self.buf.lines[self.cy]
                end = min(self.cx + repl_n, len(line))
                self.buf.lines[self.cy] = line[:self.cx] + key * (end - self.cx) + line[end:]
                self.buf.dirty = True
                self._save_dot()
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # Count prefix accumulation
        if key.isdigit() and (self.count > 0 or key != "0"):
            self.count = self.count * 10 + int(key)
            return

        n = max(self.count, 1)
        extra_n = self.count if self.count > 0 else None
        self.count = 0  # reset after consuming

        # Dot repeat recording — record keys (not count digits) while active
        if self._recording and not self._replaying_dot:
            self._recording_keys.append(key)

        # Space leader: wait for next key
        if self._pending_space:
            self._pending_space = False
            if key == "k":
                # <space>k — delete current buffer
                if self.buf.dirty:
                    self.msg = "No write since last change (add ! to override)"
                elif len(self.buffers) <= 1:
                    self.msg = "Cannot delete last buffer"
                else:
                    self._close_buffer()
            elif key == "n":
                if len(self.buffers) > 1:
                    self._switch_buffer((self.buf_idx + 1) % len(self.buffers))
            elif key == "N":
                if len(self.buffers) > 1:
                    self._switch_buffer((self.buf_idx - 1) % len(self.buffers))
            elif key == "c":
                if self.quickfix_state in self.buffers:
                    self._switch_buffer(self.buffers.index(self.quickfix_state))
                else:
                    self.msg = "No quickfix buffer"
            elif key == "o":
                self._open_quickfix_location()
            return

        # 'g' prefix: wait for next key (gg, gc)
        if self._pending_g:
            self._pending_g = False
            if key == "g":
                key = "gg"
            elif key == "c":
                # gcc — toggle comment (enter pending for second c)
                self._enter_op_pending("gc", n, extra_n)
                return
            else:
                return
        elif key == "g" and not self.pending_op:
            self._pending_g = True
            self.count = 0 if extra_n is None else n
            return

        # gcc / gc+motion: toggle comment
        if self.pending_op == "gc":
            op_n = self.pending_count
            self.pending_op = ""
            self.pending_count = 0
            self.pending_extra_n = None
            if key == "c":
                # gcc — toggle comment on current line(s)
                self._snapshot()
                self._toggle_comment(self.cy, op_n)
                self._save_dot()
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # f/t/F/T prefix: wait for target character
        if self._pending_find:
            cmd = self._pending_find
            self._pending_find = None
            if self.pending_op:
                # In operator-pending mode: route through _exec_operator
                op = self.pending_op
                op_n = self.pending_count
                self.pending_op = ""
                self.pending_count = 0
                self.pending_extra_n = None
                if op in ("d", "yd", "c"):
                    self._snapshot()
                self._pending_find_for_op = (cmd, key)
                self._exec_operator(op, cmd, op_n)
                if op in ("d", "yd"):
                    self._save_dot()
            else:
                self._exec_find(cmd, key, n)
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # Operator-pending: waiting for a motion after d/y/c
        if self.pending_op:
            op = self.pending_op
            op_n = self.pending_count
            op_extra_n = self.pending_extra_n
            if op == "y" and key == "d":
                self._start_dot(op_n, "yd")
                self.pending_op = "yd"
                return
            # Handle 'g' prefix in operator-pending (e.g. dgg)
            if self._pending_g_op:
                self._pending_g_op = False
                if key == "g":
                    key = "gg"
            elif key == "g":
                self._pending_g_op = True
                return
            # f/t/F/T in operator-pending
            if key in ("f", "t", "F", "T"):
                self._pending_find = key
                return
            # Text objects in operator-pending (i/a + w/W/(/)/[/]/{/}/'/"/)
            if key in ("i", "a"):
                self._pending_textobj = key
                return
            if self._pending_textobj:
                obj_type = self._pending_textobj
                self._pending_textobj = None
                around = obj_type == "a"
                rng = None
                if key in ("w",):
                    rng = self._find_word_object(big=False, around=around)
                elif key in ("W",):
                    rng = self._find_word_object(big=True, around=around)
                elif key in ("(", ")", "b"):
                    rng = self._find_bracket_object("(", ")", around=around)
                elif key in ("[", "]"):
                    rng = self._find_bracket_object("[", "]", around=around)
                elif key in ("{", "}", "B"):
                    rng = self._find_bracket_object("{", "}", around=around)
                elif key == '"':
                    rng = self._find_quote_object('"', around=around)
                elif key == "'":
                    rng = self._find_quote_object("'", around=around)
                if rng:
                    sy, sx, ey, ex = rng
                    if op in ("d", "yd", "c"):
                        self._snapshot()
                    if op == "d":
                        self._delete_range(sy, sx, ey, ex, copy=self.opt_delcopy)
                        self._save_dot()
                    elif op == "yd":
                        self._delete_range(sy, sx, ey, ex, copy=True)
                        self._save_dot()
                    elif op == "y":
                        self._yank_range(sy, sx, ey, ex)
                    elif op == "c":
                        self._delete_range(sy, sx, ey, ex)
                        self._enter_insert()
                else:
                    self._save_dot()
                self.pending_op = ""
                self.pending_count = 0
                self.pending_extra_n = None
                self._clamp_cursor()
                self._ensure_scroll()
                return
            self.pending_op = ""
            self.pending_count = 0
            self.pending_extra_n = None
            # Doubled operator = line-wise (dd, yy, cc, >>, <<)
            if key == op or (op == "yd" and key == "d"):
                if op == "d":
                    self._snapshot()
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    self._delete_range(self.cy, 0, end, 0, linewise=True, copy=self.opt_delcopy)
                    self._save_dot()
                elif op == "yd":
                    self._snapshot()
                    self._delete_lines(self.cy, op_n)
                    self._save_dot()
                elif op == "y":
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    self._yank_range(self.cy, 0, end, 0, linewise=True)
                    self.msg = f"{op_n} line(s) yanked"
                elif op == "c":
                    self._snapshot()
                    # cc: yank lines, clear to single empty line, insert
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    text = "\n".join(self.buf.lines[self.cy:end + 1])
                    self._set_register(text, linewise=True)
                    del self.buf.lines[self.cy + 1:end + 1]
                    self.buf.lines[self.cy] = ""
                    self.cx = 0
                    self.buf.dirty = True
                    self._enter_insert()
                elif op == ">":
                    self._snapshot()
                    self._indent_lines(self.cy, op_n)
                    self._save_dot()
                elif op == "<":
                    self._snapshot()
                    self._dedent_lines(self.cy, op_n)
                    self._save_dot()
            else:
                if op in ("d", "yd", "c"):
                    self._snapshot()
                self._exec_operator(op, key, op_n * n, extra_n=extra_n)
                if op in ("d", "yd"):
                    self._save_dot()
                # c enters insert — recording continues
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # Standard motions
        if self._exec_motion(key, n, extra_n=extra_n):
            pass  # motion already executed
        # f/t/F/T — wait for target char
        elif key in ("f", "t", "F", "T"):
            self._pending_find = key
            return
        # ; and , — repeat last find
        elif key == ";":
            self._repeat_find(reverse=False, n=n)
        elif key == ",":
            self._repeat_find(reverse=True, n=n)
        # % — match bracket
        elif key == "%":
            self._motion_percent()
        # Operators — enter pending state
        elif key == "d":
            self._enter_op_pending("d", n, extra_n)
            return
        elif key == "y":
            self._enter_op_pending("y", n, extra_n, dot=False)
            return
        elif key == "c":
            self._enter_op_pending("c", n, extra_n)
            return
        # >> indent, << dedent
        elif key == ">":
            self._enter_op_pending(">", n, extra_n)
            return
        elif key == "<":
            self._enter_op_pending("<", n, extra_n)
            return
        # Line-wise shortcuts
        elif key == "D":
            self._start_dot(n, "D")
            self._snapshot()
            self._delete_to_eol()
            self._save_dot()
        elif key == "Y":
            end = min(self.cy + n - 1, len(self.buf.lines) - 1)
            self._yank_range(self.cy, 0, end, 0, linewise=True)
            self.msg = f"{n} line(s) yanked"
        elif key == "C":
            self._start_dot(n, "C")
            self._snapshot()
            self._delete_to_eol()
            self._enter_insert()
        elif key == "J":
            self._start_dot(n, "J")
            self._snapshot()
            if not self._join_lines(n):
                self._undo_stack.pop()
            self._save_dot()
        # Paste
        elif key in ("x", "DEL"):
            self._start_dot(n, key)
            self._snapshot()
            line = self.buf.lines[self.cy]
            if line and self.cx < len(line):
                end = min(self.cx + n, len(line))
                self._delete_range(self.cy, self.cx, self.cy, end)
            self._save_dot()
        elif key == "X" or key == "BACKSPACE":
            self._start_dot(n, [key])
            self._snapshot()
            if self.cx > 0:
                start = max(self.cx - n, 0)
                self._delete_range(self.cy, start, self.cy, self.cx)
            self._save_dot()
        elif key == "r":
            self._start_dot(n, "r")
            self._pending_replace = n
            return
        elif key == "s":
            self._start_dot(n, "s")
            self._snapshot()
            line = self.buf.lines[self.cy]
            if self.cx < len(line):
                end = min(self.cx + n, len(line))
                self._delete_range(self.cy, self.cx, self.cy, end)
            self._enter_insert()
        elif key == "p":
            self._start_dot(n, "p")
            self._snapshot()
            self._paste_after()
            self._save_dot()
        elif key == "P":
            self._start_dot(n, "P")
            self._snapshot()
            self._paste_before()
            self._save_dot()
        # O/o — open line
        elif key == "o":
            self._start_dot(n, "o")
            self._snapshot()
            self._open_line(below=True)
        elif key == "O":
            self._start_dot(n, "O")
            self._snapshot()
            self._open_line(below=False)
        elif key == ":":
            self.mode = Mode.COMMAND
            self.cmd = ""
        elif key == "i":
            self._start_dot(n, "i")
            self._snapshot()
            self._enter_insert()
        elif key == "a":
            self._start_dot(n, "a")
            self._snapshot()
            self.cx += 1
            self._enter_insert()
        elif key == "I":
            self._start_dot(n, "I")
            self._snapshot()
            line = self.buf.lines[self.cy]
            self.cx = len(line) - len(line.lstrip())
            self._enter_insert()
        elif key == "A":
            self._start_dot(n, "A")
            self._snapshot()
            self.cx = len(self.buf.lines[self.cy])
            self._enter_insert()
        elif key == "v":
            self.vx, self.vy = self.cx, self.cy
            self.mode = Mode.VISUAL
        elif key == "V":
            self.vx, self.vy = self.cx, self.cy
            self.mode = Mode.VISUAL_LINE
        elif key == "/":
            self.search_dir = 1
            self.mode = Mode.SEARCH
            self.cmd = ""
        elif key == "?":
            self.search_dir = -1
            self.mode = Mode.SEARCH
            self.cmd = ""
        elif key == "n":
            self._search_next(self.search_dir)
        elif key == "N":
            self._search_next(-self.search_dir)
        elif key == "u":
            self._undo()
        elif key == "CTRL_R":
            self._redo()
        # . — dot repeat
        elif key == ".":
            self._dot_repeat(n, extra_n)
        elif key == " ":
            self._pending_space = True
            return
        elif key == "ESC":
            self.pending_op = ""
        self._clamp_cursor()
        self._ensure_scroll()

    # ── Paste ──────────────────────────────────────────────────────────

    def _paste_after(self):
        if not self.register:
            return
        if self.reg_linewise:
            lines = self.register.split("\n")
            for i, line in enumerate(lines):
                self.buf.lines.insert(self.cy + 1 + i, line)
            self.cy += 1
            self.cx = 0
        else:
            line = self.buf.lines[self.cy]
            pos = min(self.cx + 1, len(line))
            self.buf.lines[self.cy] = line[:pos] + self.register + line[pos:]
            self.cx = pos + len(self.register) - 1
        self.buf.dirty = True

    def _paste_before(self):
        if not self.register:
            return
        if self.reg_linewise:
            lines = self.register.split("\n")
            for i, line in enumerate(lines):
                self.buf.lines.insert(self.cy + i, line)
            self.cx = 0
        else:
            line = self.buf.lines[self.cy]
            self.buf.lines[self.cy] = line[:self.cx] + self.register + line[self.cx:]
            self.cx = self.cx + len(self.register) - 1
        self.buf.dirty = True

    # ── Insert mode ────────────────────────────────────────────────────

    def handle_paste(self, text):
        """Handle bracketed paste without interpreting bytes as commands."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.mode == Mode.INSERT:
            if not text:
                return
            line = self.buf.lines[self.cy]
            before, after = line[:self.cx], line[self.cx:]
            parts = text.split("\n")
            if len(parts) == 1:
                self.buf.lines[self.cy] = before + parts[0] + after
                self.cx += len(parts[0])
            else:
                self.buf.lines[self.cy] = before + parts[0]
                insert = parts[1:-1]
                tail = parts[-1] + after
                self.buf.lines[self.cy + 1:self.cy + 1] = insert + [tail]
                self.cy += len(parts) - 1
                self.cx = len(parts[-1])
            self.buf.dirty = True
            self._clamp_cursor()
            self._ensure_scroll()
        elif self.mode in (Mode.COMMAND, Mode.SEARCH):
            self.cmd += text.replace("\n", " ")
        else:
            self.msg = "Paste ignored outside Insert/Command/Search"

    def handle_insert(self, key):
        # Dot repeat recording in insert mode
        if self._recording and not self._replaying_dot:
            self._recording_keys.append(key)
        if key == "ESC":
            # Save dot recording if active
            self._save_dot()
            # Stay in place — vig divergence from vi
            self.mode = Mode.NORMAL
            self._clamp_cursor()
            return
        if key == "ENTER":
            line = self.buf.lines[self.cy]
            self.buf.lines[self.cy] = line[:self.cx]
            indent = ""
            if self.opt_autoindent:
                indent = line[:len(line) - len(line.lstrip())]
            self.buf.lines.insert(self.cy + 1, indent + line[self.cx:])
            self.cy += 1
            self.cx = len(indent)
            self.buf.dirty = True
        elif key == "BACKSPACE":
            if self.cx > 0:
                line = self.buf.lines[self.cy]
                self.buf.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
                self.cx -= 1
                self.buf.dirty = True
            elif self.cy > 0:
                # Join with previous line
                prev = self.buf.lines[self.cy - 1]
                cur = self.buf.lines.pop(self.cy)
                self.cy -= 1
                self.cx = len(prev)
                self.buf.lines[self.cy] = prev + cur
                self.buf.dirty = True
        elif key in ("LEFT", "RIGHT", "UP", "DOWN", "HOME", "END"):
            self._exec_motion(key, 1)
        elif key == "TAB":
            line = self.buf.lines[self.cy]
            spaces = 4 - (self.cx % 4)
            self.buf.lines[self.cy] = line[:self.cx] + " " * spaces + line[self.cx:]
            self.cx += spaces
            self.buf.dirty = True
        elif key == "DEL":
            line = self.buf.lines[self.cy]
            if self.cx < len(line):
                self.buf.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
                self.buf.dirty = True
        elif len(key) == 1:
            # WORD boundary checkpoint: snapshot every 2 WORDs
            is_space = key.isspace()
            if not is_space and self._insert_last_space:
                self._insert_word_count += 1
                if self._insert_word_count >= 2:
                    self._snapshot()
                    self._insert_word_count = 0
            self._insert_last_space = is_space
            line = self.buf.lines[self.cy]
            self.buf.lines[self.cy] = line[:self.cx] + key + line[self.cx:]
            self.cx += 1
            self.buf.dirty = True
        self._clamp_cursor()
        self._ensure_scroll()

    # ── Command mode ───────────────────────────────────────────────────

    def _reset_history_nav(self):
        self._hist_idx = None
        self._hist_draft = ""

    def _history_nav(self, hist, older):
        if not hist:
            return
        if self._hist_idx is None:
            self._hist_draft = self.cmd
            self._hist_idx = len(hist)
        self._hist_idx += -1 if older else 1
        if self._hist_idx < 0:
            self._hist_idx = 0
        if self._hist_idx >= len(hist):
            self._hist_idx = None
            self.cmd = self._hist_draft
        else:
            self.cmd = hist[self._hist_idx]

    def _add_history(self, hist, text):
        if text and (not hist or hist[-1] != text):
            hist.append(text)

    def _clear_completion(self):
        self.comp_matches = []
        self.comp_index = 0

    def _completion_context(self):
        s = self.cmd
        if s.startswith("!"):
            body = s[1:].lstrip()
            if " " in body:
                before, token = body.rsplit(None, 1)
                head = "!" + before + " "
            else:
                head, token = "!", body
            return head, token, os.getcwd(), True
        parts = s.split(None, 1)
        if not parts or parts[0] not in ("e", "edit", "w", "write", "r", "read"):
            return None
        base_dir = os.path.dirname(self.buf.path) if self.buf.path else os.getcwd()
        return parts[0], (parts[1] if len(parts) > 1 else ""), base_dir, False

    def _completion_names(self, token, base_dir):
        expanded = os.path.expanduser(token)
        dpart, prefix = os.path.split(expanded)
        search_dir = dpart if os.path.isabs(dpart) else os.path.join(base_dir, dpart)
        try:
            names = sorted(n for n in os.listdir(search_dir or ".") if n.startswith(prefix))
        except OSError:
            return [], search_dir
        shown = [n + ("/" if os.path.isdir(os.path.join(search_dir, n)) else "") for n in names]
        return shown, search_dir

    def _set_completed_token(self, name):
        new_token = os.path.join(os.path.dirname(self.comp_token), name) if os.path.dirname(self.comp_token) else name
        sep = "" if self.comp_shell else " "
        self.cmd = (self.comp_head + sep + new_token).strip() if self.comp_head else new_token

    def _start_completion(self):
        ctx = self._completion_context()
        if not ctx:
            return
        self.comp_head, self.comp_token, self.comp_base_dir, self.comp_shell = ctx
        self.comp_matches, _ = self._completion_names(self.comp_token, self.comp_base_dir)
        self.comp_index = 0
        if len(self.comp_matches) == 1:
            self._set_completed_token(self.comp_matches[0])
            self._clear_completion()

    def _refresh_completion(self):
        if not self.comp_matches:
            return
        ctx = self._completion_context()
        if not ctx:
            self._clear_completion()
            return
        self.comp_head, self.comp_token, self.comp_base_dir, self.comp_shell = ctx
        self.comp_matches, _ = self._completion_names(self.comp_token, self.comp_base_dir)
        self.comp_index = min(self.comp_index, max(0, len(self.comp_matches) - 1))

    def _accept_completion(self):
        if self.comp_matches:
            self._set_completed_token(self.comp_matches[self.comp_index])
            self._clear_completion()

    def handle_command(self, key):
        if self.comp_matches:
            if key == "ESC":
                self._clear_completion()
                return
            if key == "ENTER":
                self._accept_completion()
                return
            if key == "UP":
                self.comp_index = max(0, self.comp_index - 1)
                return
            if key in ("DOWN", "TAB"):
                self.comp_index = min(len(self.comp_matches) - 1, self.comp_index + 1)
                return
        if key in ("ESC", "CTRL_C"):
            self.mode = Mode.NORMAL
            self.cmd = ""
            self._reset_history_nav()
            self._clear_completion()
            return
        if key == "UP":
            self._history_nav(self.cmd_history, older=True)
            return
        if key == "DOWN":
            self._history_nav(self.cmd_history, older=False)
            return
        if key == "TAB":
            self._start_completion()
            return
        if key == "ENTER":
            cmd = self.cmd
            self._add_history(self.cmd_history, cmd.strip())
            self._reset_history_nav()
            self._clear_completion()
            self._exec_command(cmd)
            self.cmd = ""
            return
        if key == "BACKSPACE":
            if self.cmd:
                self._reset_history_nav()
                self.cmd = self.cmd[:-1]
                self._refresh_completion()
            else:
                self.mode = Mode.NORMAL
            return
        if len(key) == 1:
            self._reset_history_nav()
            self.cmd += key
            self._refresh_completion()

    def _exec_command(self, raw):
        stripped = raw.strip()

        # ── Substitute command: [range]s/pat/repl/[g] ──
        sub_match = re.match(
            r'^(%|(\d+)(,(\d+))?)?s([^a-zA-Z0-9\s])(.*?)\5(.*?)(?:\5([g]*))?$',
            stripped
        )
        if sub_match:
            self._exec_substitute(sub_match)
            return

        if stripped.startswith("!") and stripped != "!":
            self._exec_bang(stripped[1:].strip())
            self.mode = Mode.NORMAL
            return

        parts = stripped.split(None, 1)
        if not parts:
            self.mode = Mode.NORMAL
            return
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else None

        if cmd in ("q", "quit"):
            if self.buf.dirty:
                self.msg = "No write since last change (add ! to override)"
                self.mode = Mode.NORMAL
                return
            if len(self.buffers) > 1:
                self._close_buffer()
            else:
                self.running = False
        elif cmd in ("q!", "quit!"):
            if len(self.buffers) > 1:
                self._close_buffer()
            else:
                self.running = False
        elif cmd in ("qa", "qa!", "qall", "qall!", "quitall", "quitall!"):
            self._quit_all(force=cmd.endswith("!"))
        elif cmd in ("w", "write"):
            path = self._resolve_cmd_path(arg) if arg else self.buf.path
            if not path:
                self.msg = "No file name"
                self.mode = Mode.NORMAL
                return
            self._write_buffer_to_path(path)
        elif cmd == "wq":
            path = self._resolve_cmd_path(arg) if arg else self.buf.path
            if not path:
                self.msg = "No file name"
                self.mode = Mode.NORMAL
                return
            self._write_buffer_to_path(path, close_after=True)
        elif cmd in ("e!", "edit!"):
            self._reload_current_buffer()
        elif cmd in ("e", "edit"):
            if arg:
                # Add new buffer and switch to it
                path = self._resolve_cmd_path(arg)
                if os.path.isdir(path):
                    self.msg = f'Cannot edit directory: "{path}"'
                else:
                    try:
                        new_bs = BufferState(path)
                    except OSError as e:
                        self.msg = f'Cannot edit "{path}": {e.strerror or str(e)}'
                    else:
                        self._save_buf_state()
                        self.buffers.insert(self.buf_idx + 1, new_bs)
                        self._load_buf_state(self.buf_idx + 1)
                        self.msg = f'"{path}"'
            else:
                self.msg = "No file name"
            self.mode = Mode.NORMAL
        elif cmd == "new":
            self._save_buf_state()
            new_bs = BufferState()
            self.buffers.insert(self.buf_idx + 1, new_bs)
            self._load_buf_state(self.buf_idx + 1)
            self.msg = "[New]"
            self.mode = Mode.NORMAL
        elif cmd in ("n", "next", "bn"):
            if len(self.buffers) > 1:
                idx = (self.buf_idx + 1) % len(self.buffers)
                self._switch_buffer(idx)
            self.mode = Mode.NORMAL
        elif cmd in ("p", "prev", "bp"):
            if len(self.buffers) > 1:
                idx = (self.buf_idx - 1) % len(self.buffers)
                self._switch_buffer(idx)
            self.mode = Mode.NORMAL
        elif cmd == "ls":
            parts_list = []
            for i, bs in enumerate(self.buffers):
                marker = "%" if i == self.buf_idx else " "
                dirty = "+" if bs.buf.dirty else " "
                name = bs.buf.path or "[No Name]"
                parts_list.append(f"{i+1}{marker}{dirty} {name}")
            self.msg = "  ".join(parts_list)
            self.mode = Mode.NORMAL
        elif cmd in ("k", "bdelete"):
            if self.buf.dirty:
                self.msg = "No write since last change (add ! to override)"
                self.mode = Mode.NORMAL
                return
            if len(self.buffers) <= 1:
                self.msg = "Cannot delete last buffer"
                self.mode = Mode.NORMAL
                return
            self._close_buffer()
        elif cmd in ("k!", "bdelete!"):
            if len(self.buffers) <= 1:
                self.msg = "Cannot delete last buffer"
                self.mode = Mode.NORMAL
                return
            self._close_buffer()
        elif cmd == "set":
            self._exec_set(arg)
            self.mode = Mode.NORMAL
        elif cmd == "rg":
            self._exec_rg(arg)
            self.mode = Mode.NORMAL
        elif cmd == "read" or cmd == "r":
            self._exec_read(arg)
            self.mode = Mode.NORMAL
        elif cmd == "!":
            self._exec_bang(arg)
            self.mode = Mode.NORMAL
        else:
            self.msg = f"Not a command: {cmd}"
            self.mode = Mode.NORMAL

    def _exec_rg(self, arg):
        """Run ripgrep and capture results in the quickfix buffer."""
        if not arg:
            self.msg = "Usage: rg <pattern> [path]"
            return
        try:
            parts = shlex.split(arg)
        except ValueError as e:
            self.msg = f"rg: {e}"
            return
        if not parts or len(parts) > 2:
            self.msg = "Usage: rg <pattern> [path]"
            return
        pattern = parts[0]
        path = self._resolve_cmd_path(parts[1]) if len(parts) == 2 else None
        cmd = ["rg", "-n", "--column"]
        if self.opt_rghidden:
            cmd.append("-H")
        cmd.append(pattern)
        if path:
            cmd.append(path)
        import subprocess
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, cwd=os.getcwd()
            )
        except FileNotFoundError:
            self.msg = "rg: command not found"
            return
        except Exception as e:
            self.msg = f"rg: {e}"
            return
        output = result.stdout if result.returncode in (0, 1) else result.stdout + result.stderr
        lines = output.splitlines()
        if not lines:
            lines = ["(no matches)"]
        if self.quickfix_state in self.buffers:
            bs = self.quickfix_state
            bs.buf.lines = lines
            bs.buf.dirty = False
            bs.cx = bs.cy = bs.scroll = 0
            self._switch_buffer(self.buffers.index(bs))
            self.cx = self.cy = self.scroll = 0
        else:
            bs = BufferState()
            bs.buf.path = "[quickfix]"
            bs.buf.lines = lines
            bs.buf.dirty = False
            self.quickfix_state = bs
            self._save_buf_state()
            self.buffers.insert(self.buf_idx + 1, bs)
            self._load_buf_state(self.buf_idx + 1)
        self.msg = f"rg: {len(lines)} line(s)"
        self._clamp_cursor()
        self._ensure_scroll()

    def _open_quickfix_location(self):
        """Open the file:line:column location under the cursor, if present."""
        line = self.buf.lines[self.cy]
        m = re.match(r"^(.+?):(\d+):(\d+):", line)
        if not m:
            self.msg = "No quickfix location"
            return
        path = m.group(1)
        if not path:
            self.msg = "No quickfix location"
            return
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        self._goto_file_location(path, int(m.group(2)), int(m.group(3)))

    def _exec_bang(self, arg):
        """Run a shell command and show compact output in the message bar."""
        if arg:
            import subprocess
            try:
                result = subprocess.run(
                    arg, shell=True, capture_output=True, text=True, timeout=10
                )
                output = result.stdout + result.stderr
                if output.strip():
                    lines = output.replace("\r\n", "\n").replace("\r", "\n").splitlines()
                    self.msg = " | ".join(line for line in lines if line)[:200]
                else:
                    self.msg = "(no output)"
            except Exception as e:
                self.msg = str(e)
        else:
            self.msg = "No command given"

    def _exec_read(self, arg):
        """Handle :read [file] and :read ![command]."""
        if not arg:
            self.msg = "Argument required"
            return
        arg = arg.strip()
        if arg.startswith("!"):
            # :read !command — insert command output below cursor
            shell_cmd = arg[1:].strip()
            if not shell_cmd:
                self.msg = "No command given"
                return
            import subprocess
            try:
                result = subprocess.run(
                    shell_cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                output = result.stdout
                if output:
                    self._snapshot()
                    lines = output.splitlines()
                    for i, line in enumerate(lines):
                        self.buf.lines.insert(self.cy + 1 + i, line)
                    self.cy += 1
                    self.cx = 0
                    self.buf.dirty = True
                    self.msg = f"{len(lines)} line(s) inserted"
                else:
                    self.msg = "(no output)"
            except Exception as e:
                self.msg = str(e)
        else:
            # :read file — insert file contents below cursor
            try:
                with open(arg, "r") as f:
                    content = f.read()
                self._snapshot()
                lines = content.splitlines()
                if not lines:
                    lines = [""]
                for i, line in enumerate(lines):
                    self.buf.lines.insert(self.cy + 1 + i, line)
                self.cy += 1
                self.cx = 0
                self.buf.dirty = True
                self.msg = f"{len(lines)} line(s) inserted"
            except FileNotFoundError:
                self.msg = f"Can't open \"{arg}\""
            except Exception as e:
                self.msg = str(e)

    def _exec_set(self, arg):
        """Handle :set <option> commands."""
        if not arg:
            self.msg = "Argument required"
            return
        opt = arg.strip()
        if opt == "wrap":
            self.opt_wrap = True
            self.msg = "wrap on"
        elif opt == "nowrap":
            self.opt_wrap = False
            self.msg = "wrap off"
        elif opt == "number":
            self.opt_number = True
            self.msg = "number on"
        elif opt == "nonumber":
            self.opt_number = False
            self.msg = "number off"
        elif opt == "relativenumber":
            self.opt_relnum = True
            self.msg = "relativenumber on"
        elif opt == "norelativenumber":
            self.opt_relnum = False
            self.msg = "relativenumber off"
        elif opt == "autoindent":
            self.opt_autoindent = True
            self.msg = "autoindent on"
        elif opt == "noautoindent":
            self.opt_autoindent = False
            self.msg = "autoindent off"
        elif opt.startswith("comment="):
            self.opt_comment = opt[8:]
            self.msg = f"comment={self.opt_comment}"
        elif opt.startswith("scrolloff="):
            try:
                val = int(opt[len("scrolloff="):])
                if val < 0:
                    raise ValueError
            except ValueError:
                self.msg = "scrolloff must be >= 0"
                return
            self.opt_scrolloff = val
            self.msg = f"scrolloff={val}"
        elif opt.startswith("clipboard="):
            val = opt[len("clipboard="):]
            if val not in ("osc52", "auto", "off"):
                self.msg = "clipboard must be osc52, auto, or off"
                return
            self.opt_clipboard = val
            self.msg = f"clipboard={val}"
        elif opt.startswith("yankflash="):
            try:
                val = int(opt[len("yankflash="):])
                if val < 0:
                    raise ValueError
            except ValueError:
                self.msg = "yankflash must be >= 0"
                return
            self.opt_yankflash = val
            self.msg = f"yankflash={val}"
        elif opt == "delcopy":
            self.opt_delcopy = True
            self.msg = "delcopy on"
        elif opt == "nodelcopy":
            self.opt_delcopy = False
            self.msg = "delcopy off"
        elif opt == "wrapmove":
            self.opt_wrapmove = True
            self.msg = "wrapmove on"
        elif opt == "nowrapmove":
            self.opt_wrapmove = False
            self.msg = "wrapmove off"
        elif opt == "rghidden":
            self.opt_rghidden = True
            self.msg = "rghidden on"
        elif opt == "norghidden":
            self.opt_rghidden = False
            self.msg = "rghidden off"
        else:
            self.msg = f"Unknown option: {opt}"

    def _exec_substitute(self, m):
        """Execute :[range]s/pat/repl/[g] substitute command."""
        range_spec = m.group(1)  # '%' or '10' or '10,20' or None
        start_str = m.group(2)   # first line number or None
        end_str = m.group(4)     # second line number or None
        pattern = m.group(6)
        replacement = m.group(7)
        flags_str = m.group(8) or ""

        # Determine line range
        if range_spec == "%":
            start_line = 0
            end_line = len(self.buf.lines) - 1
        elif start_str is not None:
            start_line = max(0, int(start_str) - 1)  # 1-indexed to 0-indexed
            if end_str is not None:
                end_line = min(int(end_str) - 1, len(self.buf.lines) - 1)
            else:
                end_line = start_line
        else:
            # No range: current line only
            start_line = self.cy
            end_line = self.cy

        try:
            pat = re.compile(pattern)
        except re.error as e:
            self.msg = f"Invalid regex: {e}"
            self.mode = Mode.NORMAL
            return

        global_flag = "g" in flags_str
        total_subs = 0

        self._snapshot()
        for line_idx in range(start_line, end_line + 1):
            line = self.buf.lines[line_idx]
            if global_flag:
                new_line, count = pat.subn(replacement, line)
            else:
                new_line, count = pat.subn(replacement, line, count=1)
            if count > 0:
                self.buf.lines[line_idx] = new_line
                total_subs += count

        if total_subs > 0:
            self.buf.dirty = True
            self.msg = f"{total_subs} substitution(s)"
        else:
            self._undo_stack.pop()  # remove no-op snapshot
            self.msg = "Pattern not found"
        self.mode = Mode.NORMAL

    # ── Visual mode ────────────────────────────────────────────────────

    def handle_visual(self, key):
        if key == "ESC":
            self.mode = Mode.NORMAL
            return
        # Resolve pending find-char
        if self._pending_find:
            cmd = self._pending_find
            self._pending_find = None
            self._exec_find(cmd, key, 1)
            self._clamp_cursor()
            self._ensure_scroll()
            return
        # 'g' prefix for gg and gc
        if self._pending_g:
            self._pending_g = False
            if key == "g":
                key = "gg"
            elif key == "c":
                # gc in visual — toggle comment on selected lines
                sel = self._selection_range()
                if sel:
                    sy, sx, ey, ex = sel
                    self._snapshot()
                    self._toggle_comment(sy, ey - sy + 1)
                self.mode = Mode.NORMAL
                return
            else:
                return
        if key == "g":
            self._pending_g = True
            return
        # f/t/F/T — wait for target char
        if key in ("f", "t", "F", "T"):
            self._pending_find = key
            return
        # Edit operations on selection
        if key in ("d", "x"):
            self._snapshot()
            self._visual_delete()
            return
        if key == "y":
            self._visual_yank()
            return
        if key == "c":
            self._snapshot()
            self._visual_delete()
            self._enter_insert()
            return
        # ; and , — repeat last find
        if key == ";":
            self._repeat_find(reverse=False, n=1)
        elif key == ",":
            self._repeat_find(reverse=True, n=1)
        # % — match bracket
        elif key == "%":
            self._motion_percent()
        # Motions — same dispatch as normal mode
        else:
            self._exec_motion(key)
        self._clamp_cursor()
        self._ensure_scroll()

    def _visual_delete(self):
        """Delete the visual selection."""
        sel = self._selection_range()
        if not sel:
            return
        sy, sx, ey, ex = sel
        linewise = self.mode == Mode.VISUAL_LINE
        if not linewise:
            # Include the end character
            ex = min(ex + 1, len(self.buf.lines[ey]))
        self._delete_range(sy, sx, ey, ex, linewise)
        self.mode = Mode.NORMAL

    def _visual_yank(self):
        """Yank the visual selection."""
        sel = self._selection_range()
        if not sel:
            return
        sy, sx, ey, ex = sel
        linewise = self.mode == Mode.VISUAL_LINE
        if not linewise:
            ex = min(ex + 1, len(self.buf.lines[ey]))
        self._yank_range(sy, sx, ey, ex, linewise)
        self.cy, self.cx = sy, sx
        self.mode = Mode.NORMAL
        self.msg = "yanked"

    # ── Search ─────────────────────────────────────────────────────────

    def handle_search(self, key):
        """Handle input in search mode (/ or ?)."""
        if key == "ESC":
            self.mode = Mode.NORMAL
            self.cmd = ""
            self._reset_history_nav()
            return
        if key == "UP":
            self._history_nav(self.search_history, older=True)
            return
        if key == "DOWN":
            self._history_nav(self.search_history, older=False)
            return
        if key == "ENTER":
            pattern = self.cmd
            self.cmd = ""
            self.mode = Mode.NORMAL
            self._reset_history_nav()
            if pattern:
                self.search_pattern = pattern
                self._add_history(self.search_history, pattern)
            if self.search_pattern:
                self._search_next(self.search_dir)
            return
        if key == "BACKSPACE":
            if self.cmd:
                self._reset_history_nav()
                self.cmd = self.cmd[:-1]
            else:
                self.mode = Mode.NORMAL
            return
        if len(key) == 1:
            self._reset_history_nav()
            self.cmd += key

    def _search_next(self, direction):
        """Search for self.search_pattern in the given direction.
        direction: 1=forward, -1=backward."""
        if not self.search_pattern:
            self.msg = "No previous search"
            return
        try:
            pat = re.compile(self.search_pattern)
        except re.error as e:
            self.msg = f"Invalid regex: {e}"
            return

        total = len(self.buf.lines)
        for i in range(total + 1):
            line_idx = (self.cy + i * direction) % total
            line = self.buf.lines[line_idx]
            if direction == 1:
                start = self.cx + 1 if i == 0 else 0
                m = pat.search(line, start)
            else:
                limit = self.cx if i == 0 else len(line)
                m = None
                for m_candidate in pat.finditer(line):
                    if m_candidate.start() >= limit:
                        break
                    m = m_candidate
            if m:
                self.cy = line_idx
                self.cx = m.start()
                self._clamp_cursor()
                self._ensure_scroll()
                return
        self.msg = f"Pattern not found: {self.search_pattern}"

    # ── Main loop ──────────────────────────────────────────────────────

    def run(self):
        self.term.enter_raw()
        signal.signal(signal.SIGWINCH, lambda *_: self._handle_resize())
        try:
            while self.running:
                self.render()
                if self._yank_flash:
                    timeout = max(0.0, self._yank_flash[0] - time.monotonic())
                    ready, _, _ = select.select([self.term.fd], [], [], timeout)
                    if not ready:
                        self._yank_flash = None
                        continue
                key = self.term.read_key()
                if not key:
                    continue
                if isinstance(key, tuple) and key[0] == "PASTE":
                    self.last_key = "PASTE"
                    self.handle_paste(key[1])
                    continue
                self.last_key = key
                if key == "CTRL_Z":
                    self._suspend()
                    continue
                if key == "CTRL_C" and self.mode != Mode.NORMAL:
                    self.pending_op = ""
                    self._pending_g = False
                    self._pending_space = False
                    self._pending_g_op = False
                    self._pending_find = None
                    self._pending_textobj = None
                    self._pending_replace = 0
                    self._pending_ctrl_c = False
                    self.mode = Mode.NORMAL
                    self.cmd = ""
                    continue
                if self.mode == Mode.NORMAL and self._pending_mkdir_write:
                    self._answer_mkdir_prompt(key)
                    continue

                # Clear message on any key (unless entering command/search mode)
                if self.mode not in (Mode.COMMAND, Mode.SEARCH):
                    self.msg = ""

                if self.mode == Mode.NORMAL:
                    self.handle_normal(key)
                elif self.mode == Mode.INSERT:
                    self.handle_insert(key)
                elif self.mode == Mode.COMMAND:
                    self.handle_command(key)
                elif self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
                    self.handle_visual(key)
                elif self.mode == Mode.SEARCH:
                    self.handle_search(key)
        finally:
            sys.stdout.write("\x1b[0 q")  # reset cursor shape to default
            sys.stdout.flush()
            self.term.restore()

# ── Entry point ────────────────────────────────────────────────────────────

def main():
    paths = sys.argv[1:] if len(sys.argv) > 1 else None
    ed = Editor(paths)
    try:
        ed.run()
    except Exception as e:
        report_path = ed._write_crash_report(e)
        if report_path:
            sys.stderr.write(f"vig crashed: {e}\n")
            sys.stderr.write(f"crash report written to {report_path}\n")
        else:
            sys.stderr.write(f"vig crashed: {e}\n")
            sys.stderr.write("failed to write crash report\n")
        sys.stderr.flush()
        raise

if __name__ == "__main__":
    main()
