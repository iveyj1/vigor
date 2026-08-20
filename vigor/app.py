#!/usr/bin/env python3

import sys
import os
import re
import base64
import signal
import shutil
import select
import time

from . import BUILD_ID, VERSION
from .commands import CommandMixin
from .highlight import (
    SEARCH_COLOR, build_markdown_view, markdown_spans, search_spans,
    syntax_spans,
)
from .editing import (
    change_case as edit_change_case,
    delete_range as edit_delete_range,
    paste as edit_paste,
    yank_range as edit_yank_range,
)
from .layout import ViewportLayout, display_col, display_index
from .modes import ModeMixin
from .state import BufferState, Mode
from .terminal import Terminal

SPLASH = (
    " _    ___                 ",
    "| |  / (_)___ _____  _____",
    "| | / / / __ `/ __ \\/ ___/",
    "| |/ / / /_/ / /_/ / /    ",
    "|___/_/\\__, /\\____/_/     ",
    "      /____/",
    "  -- markdown style -- ",
)
SPLASH_BG = "\x1b[49m"  # terminal default background
SPLASH_FRAME = "\x1b[96m"
SPLASH_FG = "\x1b[97m"

# ── Editor ─────────────────────────────────────────────────────────────────

class Editor(CommandMixin, ModeMixin):
    def __init__(self, paths=None):
        # Existing directories open one startup completion; other paths are buffers.
        file_paths, startup_dir = [], None
        for p in paths or ():
            resolved = self._resolve_startup_path(p)
            if os.path.isdir(resolved):
                if startup_dir is None:
                    startup_dir = resolved
            else:
                file_paths.append(resolved)
        self.buffers = [BufferState(p) for p in file_paths] if file_paths else [BufferState()]
        self.buf_idx = 0
        # Load first buffer's state into working attributes
        bs = self.buffers[0]
        self.buf = bs.buf
        self.cx = bs.cx
        self.cy = bs.cy
        self.scroll = bs.scroll
        self.md_view = bs.md_view
        self.md_lines = bs.md_lines
        self.md_maps = bs.md_maps
        self._undo_stack = bs._undo_stack
        self._redo_stack = bs._redo_stack
        self._undo_save_depth = bs._undo_save_depth
        self._undo_branched = bs._undo_branched
        self.mode = Mode.NORMAL
        self.cmd = ""  # command-line input
        self.cmd_cx = 0  # command/search prompt cursor column
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
        self.search_ignorecase = False  # smart-case flag for active search
        self.search_dir = 1  # 1=forward, -1=backward
        self.opt_wrap = False  # :set wrap
        self.opt_wrapcol = 0  # :set wrapcol=N (0 uses terminal width)
        self.opt_number = False  # :set number
        self.opt_relnum = False  # :set relativenumber
        self.opt_scrolloff = 0  # :set scrolloff=N
        self.opt_clipboard = "auto"  # :set clipboard=osc52|auto|off
        self.opt_yankflash = 300  # :set yankflash=N milliseconds
        self.opt_delcopy = True  # :set delcopy/nodelcopy
        self.opt_wrapmove = False  # :set wrapmove/nowrapmove
        self.opt_markdownfences = False  # :set markdownfences/nomarkdownfences
        self.opt_rghidden = False  # :set rghidden/norghidden
        self.opt_hlsearch = False  # :set hlsearch/nohlsearch
        self.opt_makeprg = "make"  # :set makeprg=<shell command>
        self._wrap_skip = 0  # wrapped display rows to skip at top line
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
        self._sticky_cx = None      # desired column during vertical movement
        self._pending_space = False # space-leader: waiting for next key
        self._pending_g_op = False  # 'g' prefix inside operator-pending
        self._pending_find = None   # (cmd, count) for 'f'/'t'/'F'/'T' waiting for char
        self._pending_find_for_op = None  # (cmd, ch) find for operator
        self._pending_textobj = None  # 'i'/'a' waiting for object key
        self._pending_replace = 0    # count for normal-mode r{char}
        self._pending_ctrl_c = False # Ctrl-C prefix for quit-all shortcuts
        self._pending_mkdir_write = None  # (path, close_after) waiting for y/n
        self._yank_flash = None     # (expires, sy, sx, ey, ex, linewise)
        self.quickfix_state = None  # BufferState holding last quickfix results
        self.quickfix_cwd = os.getcwd()
        self.last_key = ""  # last decoded key read from terminal
        self._load_config()
        self.term = Terminal()
        self._update_size()
        self._startup_completion = startup_dir is not None
        self._splash = not self._startup_completion
        self._splash_until = time.monotonic() + 2 if file_paths else None
        if startup_dir:
            self.mode = Mode.COMMAND
            self.cmd = "edit " + startup_dir.rstrip(os.sep) + os.sep
            self.cmd_cx = len(self.cmd)
            self._start_completion()

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
        """Resolve a command path against the process working directory."""
        return os.path.abspath(os.path.expanduser(path.strip()))

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
        self.md_view, self.md_lines, self.md_maps = False, None, None
        self.cx = self.cy = self.scroll = self._wrap_skip = 0
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
            os.path.join(xdg, "vigor", "config"),
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

    def _cancel_pending(self):
        """Clear incomplete normal-mode input sequences."""
        self.count = 0
        self.pending_op = ""
        self.pending_count = 0
        self.pending_extra_n = None
        self._pending_g = self._pending_space = self._pending_g_op = False
        self._pending_find = self._pending_find_for_op = self._pending_textobj = None
        self._pending_replace = 0
        self._pending_ctrl_c = False
        self._pending_mkdir_write = None

    # ── Buffer management ──────────────────────────────────────────────

    def _save_buf_state(self):
        """Save working attributes back into current BufferState."""
        bs = self.buffers[self.buf_idx]
        bs.buf = self.buf
        bs.cx, bs.cy, bs.scroll = self.cx, self.cy, self.scroll
        bs.wrap_skip = self._wrap_skip
        bs.md_view, bs.md_lines, bs.md_maps = self.md_view, self.md_lines, self.md_maps
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
        self._wrap_skip = bs.wrap_skip
        self.md_view, self.md_lines, self.md_maps = bs.md_view, bs.md_lines, bs.md_maps
        self._undo_stack = bs._undo_stack
        self._redo_stack = bs._redo_stack
        self._undo_save_depth = bs._undo_save_depth
        self._undo_branched = bs._undo_branched

    def _add_buffer(self, bs):
        """Add and switch to bs, replacing an untouched initial buffer."""
        if (len(self.buffers) == 1 and self.buf.path is None
                and self.buf.lines == [""] and not self.buf.dirty):
            self.buffers[0] = bs
            self._load_buf_state(0)
        else:
            self._save_buf_state()
            self.buffers.insert(self.buf_idx + 1, bs)
            self._load_buf_state(self.buf_idx + 1)

    def _switch_buffer(self, idx):
        """Switch to buffer at idx, saving current state first."""
        if idx == self.buf_idx:
            return
        if idx < 0 or idx >= len(self.buffers):
            return
        self._save_buf_state()
        self._load_buf_state(idx)
        self._sticky_cx = None
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
        self._sticky_cx = None
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
            self._add_buffer(bs)
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

    def _move_view_top(self, delta):
        """Move the viewport top by one logical line or wrapped display row."""
        if not self.opt_wrap:
            new = max(0, min(self.scroll + delta, max(0, len(self.buf.lines) - self.rows)))
            moved = new != self.scroll
            self.scroll, self._wrap_skip = new, 0
            return moved
        self.scroll = max(0, min(self.scroll, len(self.buf.lines) - 1))
        self._wrap_skip = min(self._wrap_skip, self._line_screen_rows(self.scroll) - 1)
        if delta > 0:
            if self._wrap_skip + 1 < self._line_screen_rows(self.scroll):
                self._wrap_skip += 1
            elif self.scroll < len(self.buf.lines) - 1:
                self.scroll += 1
                self._wrap_skip = 0
            else:
                return False
        elif self._wrap_skip > 0:
            self._wrap_skip -= 1
        elif self.scroll > 0:
            self.scroll -= 1
            self._wrap_skip = self._line_screen_rows(self.scroll) - 1
        else:
            return False
        return True

    def _cursor_view_row(self):
        """Return the cursor's display row relative to the viewport top."""
        if not self.opt_wrap:
            return self.cy - self.scroll
        if self.cy < self.scroll:
            return -1
        if self.cy == self.scroll:
            return self._cursor_wrap_row(self._wrap_cols()) - self._wrap_skip
        row = self._line_screen_rows(self.scroll) - self._wrap_skip
        for y in range(self.scroll + 1, self.cy):
            row += self._line_screen_rows(y)
        return row + self._cursor_wrap_row(self._wrap_cols())

    def _position_at_view_row(self, target, col):
        """Move cursor to a viewport display row, preserving column where possible."""
        if not self.opt_wrap:
            self.cy = min(self.scroll + target, len(self.buf.lines) - 1)
            self.cx = self._view_index(self.cy, col)
            return
        y, wrap_row = self.scroll, self._wrap_skip
        while y < len(self.buf.lines) - 1:
            available = self._line_screen_rows(y) - wrap_row
            if target < available:
                break
            target -= available
            y, wrap_row = y + 1, 0
        wrap_row += target
        self.cy = y
        self.cx = self._view_index(y, wrap_row * self._wrap_cols() + col)

    def _scroll_view(self, delta, n=1):
        """Scroll viewport by display rows, moving cursor only to keep it visible."""
        display_x = self._cursor_display_col()
        col = display_x % self._wrap_cols() if self.opt_wrap else display_x
        for _ in range(n):
            if not self._move_view_top(delta):
                break
        row = self._cursor_view_row()
        margin = min(self.opt_scrolloff, max(0, (self.rows - 1) // 2))
        if row < margin:
            self._position_at_view_row(margin, col)
        elif row > self.rows - 1 - margin:
            self._position_at_view_row(self.rows - 1 - margin, col)
        self._clamp_cursor()

    def _center_cursor(self):
        """Center cursor vertically as closely as file boundaries permit."""
        if not self.opt_wrap:
            self.scroll = max(0, min(self.cy - self.rows // 2,
                                     max(0, len(self.buf.lines) - self.rows)))
            self._wrap_skip = 0
            return
        self.scroll = self.cy
        self._wrap_skip = self._cursor_wrap_row(self._wrap_cols())
        for _ in range(self.rows // 2):
            if not self._move_view_top(-1):
                break

    def _ensure_scroll(self):
        """Adjust viewport only as far as needed to keep cursor visible."""
        if not self.opt_wrap:
            self._wrap_skip = 0
            max_scroll = max(0, len(self.buf.lines) - self.rows)
            margin = min(self.opt_scrolloff, max(0, (self.rows - 1) // 2))
            if self.cy < self.scroll + margin:
                self.scroll = self.cy - margin
            elif self.cy > self.scroll + self.rows - 1 - margin:
                self.scroll = self.cy - (self.rows - 1 - margin)
            self.scroll = max(0, min(self.scroll, max_scroll))
            return
        self.scroll = max(0, min(self.scroll, len(self.buf.lines) - 1))
        self._wrap_skip = max(0, min(self._wrap_skip, self._line_screen_rows(self.scroll) - 1))
        if self.cy < self.scroll:
            self.scroll, self._wrap_skip = self.cy, 0
        margin = min(self.opt_scrolloff, max(0, (self.rows - 1) // 2))
        row = self._cursor_view_row()
        while row < margin and self._move_view_top(-1):
            row += 1
        bottom = self.rows - 1 - margin
        while row > bottom and self._move_view_top(1):
            row -= 1

    # ── Undo / Redo ───────────────────────────────────────────────────

    def _snapshot(self):
        """Save current state for undo. Call before any mutation."""
        self.md_view, self.md_lines, self.md_maps = False, None, None
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
        self.md_view, self.md_lines, self.md_maps = False, None, None
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
        self.md_view, self.md_lines, self.md_maps = False, None, None
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
        self._sticky_cx = None
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

    def _set_markdown_view(self, enabled):
        self.md_view = enabled
        if enabled:
            self.md_lines, self.md_maps = build_markdown_view(self.buf.lines)
        else:
            self.md_lines = self.md_maps = None
        self._wrap_skip = 0
        self._ensure_scroll()

    def _view_line(self, y):
        if self._is_markdown_fence_line(self.buf.lines[y]):
            return ""
        return self.md_lines[y] if self.md_view else self.buf.lines[y].expandtabs(4)

    def _view_col(self, y, index):
        if self.md_view:
            return self.md_maps[y][min(index, len(self.md_maps[y]) - 1)]
        return display_col(self.buf.lines[y], index)

    def _view_index(self, y, target):
        if not self.md_view:
            return display_index(self.buf.lines[y], target)
        mapping = self.md_maps[y]
        for i, col in enumerate(mapping):
            if col >= target:
                return i if col == target else max(0, i - 1)
        return len(mapping) - 1

    def _cursor_display_col(self):
        return self._view_col(self.cy, self.cx)

    def _viewport_layout(self):
        return ViewportLayout(
            len(self.buf.lines), self._view_line, self._view_col, self._view_index,
            self.rows, self.cols, self._gutter_width(), self.opt_wrap,
            self.opt_wrapcol, self.scroll, self._wrap_skip,
        )

    def _wrap_cols(self):
        return self._viewport_layout().wrap_cols

    def _motion_display_row(self, delta):
        """Move vertically while preserving a display column."""
        display_x = self._cursor_display_col()
        if not (self.opt_wrap and self.opt_wrapmove):
            if self._sticky_cx is None:
                self._sticky_cx = display_x
            self.cy += delta
            self._clamp_cursor()
            self.cx = self._view_index(self.cy, self._sticky_cx)
            return
        cols = self._wrap_cols()
        if self._sticky_cx is None:
            self._sticky_cx = display_x % cols
        col, row = self._sticky_cx, display_x // cols
        if delta > 0:
            if row + 1 < self._line_screen_rows(self.cy):
                self.cx = self._view_index(self.cy, (row + 1) * cols + col)
            elif self.cy < len(self.buf.lines) - 1:
                self.cy += 1
                self.cx = self._view_index(self.cy, col)
        elif row > 0:
            self.cx = self._view_index(self.cy, (row - 1) * cols + col)
        elif self.cy > 0:
            self.cy -= 1
            target = (self._line_screen_rows(self.cy) - 1) * cols + col
            self.cx = self._view_index(self.cy, target)
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
        if key not in ("j", "k", "DOWN", "UP"):
            self._sticky_cx = None
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
        return max(5, len(str(len(self.buf.lines)))) + 1

    def _gutter_str(self, buf_line, gutter_width):
        """Format the line number string for a given buffer line."""
        if gutter_width == 0:
            return ""
        width = gutter_width - 1
        if self.opt_relnum and buf_line == self.cy:
            return str(buf_line + 1).ljust(width) + " "
        num = abs(buf_line - self.cy) if self.opt_relnum else buf_line + 1
        return str(num).rjust(width) + " "

    def _cursor_wrap_row(self, content_cols):
        """Displayed wrap row for the cursor within its buffer line."""
        return self._cursor_display_col() // content_cols if content_cols > 0 else 0

    def _cursor_wrap_col(self, content_cols):
        """Displayed wrap column for the cursor within its buffer line."""
        return self._cursor_display_col() % content_cols if content_cols > 0 else 0

    def _line_screen_rows(self, line_idx):
        """How many screen rows does buffer line `line_idx` occupy?"""
        if self.cols == 0:
            return 1
        return self._viewport_layout().line_rows(line_idx)

    def _end_screen_row(self, out, cells):
        """End a rendered row without erasing a full-width final cell."""
        if cells < self.cols:
            out.append("\x1b[K")
        out.append("\r\n")

    def _is_markdown_fence_line(self, line):
        if not (self.md_view and self.opt_markdownfences) or not self.buf.path:
            return False
        lower = self.buf.path.lower()
        if not (lower.endswith(".md") or lower.endswith(".markdown")):
            return False
        stripped = line.lstrip()
        return stripped.startswith("```") or stripped.startswith("~~~")

    def _syntax_spans(self, line, y=None):
        """Return styled syntax spans for the current buffer's language."""
        if self.md_view:
            return markdown_spans(self.buf.lines, line, y)
        return syntax_spans(self.buf.path, line)

    def _search_spans(self, line):
        """Return active search highlight spans for a buffer line."""
        if not (self.opt_hlsearch and self.search_pattern):
            return ()
        try:
            return search_spans(line, self._compile_search())
        except re.error:
            return ()

    def _render_visible(self, visible, buf_line, col_offset, sel, out):
        """Render a segment with line-local syntax and optional reverse video."""
        if not visible:
            return
        start, end = col_offset, col_offset + len(visible)
        line = self.buf.lines[buf_line]
        spans = tuple((self._view_col(buf_line, sx), self._view_col(buf_line, ex), color)
                      for sx, ex, color in self._syntax_spans(line, buf_line))
        search_spans = tuple((self._view_col(buf_line, sx), self._view_col(buf_line, ex))
                             for sx, ex in self._search_spans(line))
        bounds = {start, end}
        for sx, ex, _ in spans:
            if sx < end and ex > start:
                bounds.update((max(start, sx), min(end, ex)))
        for sx, ex in search_spans:
            if sx < end and ex > start:
                bounds.update((max(start, sx), min(end, ex)))
        select_start = select_end = None
        if sel:
            sy, sx, ey, ex = sel
            if sy <= buf_line <= ey:
                select_start = max(start, self._view_col(buf_line, sx) if buf_line == sy else start)
                select_end = min(end, self._view_col(buf_line, ex) if buf_line == ey else end)
                if select_start < select_end:
                    bounds.update((select_start, select_end))
        spans = iter(spans)
        active = next(spans, None)
        for left, right in zip(sorted(bounds), sorted(bounds)[1:]):
            while active and active[1] <= left:
                active = next(spans, None)
            color = active[2] if active and active[0] <= left < active[1] else ""
            searched = any(sx <= left < ex for sx, ex in search_spans)
            selected = select_start is not None and select_start <= left < select_end
            if color:
                out.append(color)
            if searched:
                out.append(SEARCH_COLOR)
            if selected:
                out.append("\x1b[7m")
            out.append(visible[left - start:right - start])
            if color or searched or selected:
                out.append("\x1b[m")

    def _append_completion_box(self, out):
        """Overlay centered completion menu with a rounded border."""
        if self.mode != Mode.COMMAND or not self.comp_matches or self.cols < 8 or self.rows < 5:
            return
        hpad = 2
        vpad = 1
        max_items = max(1, self.rows - 4 - 2 * vpad)
        item_rows = min(len(self.comp_matches), max_items)
        text_w = min(max(len(n) for n in self.comp_matches), self.cols - 2 - 2 * hpad)
        inner_w = text_w + 2 * hpad
        box_w = inner_w + 2
        box_h = item_rows + 2 * vpad + 2
        top = max(1, (self.rows - box_h) // 2 + 1)
        left = max(1, (self.cols - box_w) // 2 + 1)
        start = max(0, min(self.comp_index - item_rows + 1, len(self.comp_matches) - item_rows))
        out.append(f"\x1b[{top};{left}H╭" + "─" * inner_w + "╮")
        for i in range(vpad):
            out.append(f"\x1b[{top + 1 + i};{left}H│" + " " * inner_w + "│")
        item_top = top + 1 + vpad
        for row, idx in enumerate(range(start, start + item_rows), item_top):
            text = (" " * hpad + self.comp_matches[idx][:text_w] + " " * hpad).ljust(inner_w)
            out.append(f"\x1b[{row};{left}H│")
            if idx == self.comp_index:
                out.append("\x1b[7m" + text + "\x1b[m")
            else:
                out.append(text)
            out.append("│")
        bottom_pad_top = item_top + item_rows
        for i in range(vpad):
            out.append(f"\x1b[{bottom_pad_top + i};{left}H│" + " " * inner_w + "│")
        out.append(f"\x1b[{top + box_h - 1};{left}H╰" + "─" * inner_w + "╯")

    def _append_splash(self, out):
        """Overlay a centered framed logo on the completed editor frame."""
        total_rows = self.rows + 2
        if self.cols < 2 or total_rows < 2:
            return
        logo_width = max(len(line) for line in SPLASH)
        box_width = min(self.cols, max(logo_width + 2, (logo_width * 3) // 2))
        box_height = min(total_rows, max(len(SPLASH) + 2, (len(SPLASH) * 3) // 2))
        inner_width, inner_height = box_width - 2, box_height - 2
        top = (2 * (total_rows - box_height)) // 10 + 1  # place box high on the screen
        left = (self.cols - box_width) // 2 + 1
        out.append(f"\x1b[{top};{left}H{SPLASH_BG}{SPLASH_FRAME}╭" + "─" * inner_width + "╮\x1b[m")

        footer_row = inner_height - 1 if inner_height > len(SPLASH) else None
        logo_rows = min(len(SPLASH), inner_height - (footer_row is not None))
        logo_start = max(0, (len(SPLASH) - logo_rows) // 2)
        logo_top = max(0, (inner_height - (footer_row is not None) - logo_rows) // 2)
        crop = max(0, (logo_width - inner_width) // 2)
        for i in range(inner_height):
            text = " " * inner_width
            if i == footer_row:
                text = f"v{VERSION} · {BUILD_ID}"[:inner_width].center(inner_width)
            elif logo_top <= i < logo_top + logo_rows:
                line = SPLASH[logo_start + i - logo_top].ljust(logo_width)
                line = line[crop:crop + inner_width]
                text = line.center(inner_width)
            row = top + i + 1
            out.append(f"\x1b[{row};{left}H{SPLASH_BG}{SPLASH_FRAME}│{SPLASH_FG}{text}{SPLASH_FRAME}│\x1b[m")
        out.append(f"\x1b[{top + box_height - 1};{left}H{SPLASH_BG}{SPLASH_FRAME}╰" + "─" * inner_width + "╯\x1b[m")

    def render(self):
        out = []
        out.append("\x1b[?25l")  # hide cursor
        out.append("\x1b[H")     # cursor home
        out.append("\x1b[J")     # clear old frame before drawing leading blanks
        out.append("\x1b[?7l")   # no autowrap during full-frame redraws

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
        layout = self._viewport_layout()
        gw = layout.gutter_width
        cursor_display_x = self._cursor_display_col()
        window_hscroll = 0 if self.opt_wrap else max(0, cursor_display_x - layout.content_cols + 1)
        cursor_screen_y = cursor_screen_x = 0
        cursor_position = layout.source_to_screen(self.cy, self.cx, window_hscroll)
        if cursor_position:
            cursor_screen_y, cursor_screen_x = cursor_position

        visible_rows = layout.visible_rows(window_hscroll)
        for row in visible_rows:
            out.append(self._gutter_str(row.source_y, gw) if row.wrap_row == 0 else " " * gw)
            self._render_visible(row.text, row.source_y, row.display_start, sel, out)
            self._end_screen_row(out, gw + len(row.text))
        screen_rows_used = len(visible_rows)

        # Fill remaining rows with tildes
        while screen_rows_used < self.rows:
            out.append("~")
            self._end_screen_row(out, 1)
            screen_rows_used += 1

        self._append_completion_box(out)
        out.append(f"\x1b[{self.rows + 1};1H")

        # Status bar (reverse video)
        out.append("\x1b[7m")
        fname = self.buf.path or "[No Name]"
        dirty = " [+]" if self.buf.dirty else ""
        presentation = " [MD]" if self.md_view else ""
        mode_str = self.mode.value
        count_str = str(self.count) if self.count > 0 else ""
        buf_info = f"[{self.buf_idx + 1}/{len(self.buffers)}] " if len(self.buffers) > 1 else ""
        left = f" {mode_str} | {buf_info}{fname}{dirty}{presentation}"
        right = f" {count_str} {self.cy + 1}:{self.cx + 1} "
        pad = self.cols - len(left) - len(right)
        if pad < 0:
            pad = 0
        status = left + " " * pad + right
        status = status[:self.cols]
        out.append(status)
        out.append("\x1b[m")  # reset
        self._end_screen_row(out, len(status))

        # Command / message bar
        if self.mode == Mode.COMMAND:
            cmd_display = (":" + self.cmd)[:self.cols]
        elif self.mode == Mode.SEARCH:
            prompt = "/" if self.search_dir == 1 else "?"
            cmd_display = (prompt + self.cmd)[:self.cols]
        else:
            cmd_display = (self.msg[:self.cols] if self.msg else "")
        out.append(cmd_display)
        if len(cmd_display) < self.cols:
            out.append("\x1b[K")

        if self._splash:
            self._append_splash(out)
        else:
            # Cursor shape: block for normal/visual/command, bar for insert
            if self.mode == Mode.INSERT:
                out.append("\x1b[6 q")  # steady bar
            else:
                out.append("\x1b[2 q")  # steady block

            # Position real cursor (use prompt cursor while editing a prompt)
            if self.mode in (Mode.COMMAND, Mode.SEARCH):
                screen_y, screen_x = self.rows + 2, min(self.cols, self.cmd_cx + 2)
            else:
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
            res = subprocess.run(cmd, input=text, text=True, check=False, timeout=1)
            return res.returncode == 0
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
            if self._external_copy(text):
                return
            try:
                self._osc52_copy(text)
            except Exception:
                pass

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
        text, self.cy, self.cx, changed = edit_delete_range(
            self.buf.lines, sy, sx, ey, ex, linewise,
        )
        if not changed:
            return ""
        if copy:
            self._set_register(text, linewise=linewise)
        self.buf.dirty = True
        self._clamp_cursor()
        return text

    def _delete_lines(self, start, count):
        """Delete `count` lines starting at `start`."""
        end = min(start + count - 1, len(self.buf.lines) - 1)
        return self._delete_range(start, 0, end, 0, linewise=True)

    def _yank_range(self, sy, sx, ey, ex, linewise=False):
        """Yank text from (sy,sx) to (ey,ex) without deleting."""
        text = edit_yank_range(self.buf.lines, sy, sx, ey, ex, linewise)
        self._set_register(text, linewise=linewise)
        if linewise:
            self._flash_yank(sy, 0, ey, len(self.buf.lines[ey]), linewise=True)
        else:
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

    def _case_func(self, op):
        """Return the character transform for a case operator."""
        return {"g~": str.swapcase, "gU": str.upper, "gu": str.lower}[op]

    def _change_case_range(self, sy, sx, ey, ex, func):
        """Apply func to the half-open character range without touching registers."""
        if edit_change_case(self.buf.lines, sy, sx, ey, ex, func):
            self.buf.dirty = True

    def _exec_operator(self, op, motion_key, n, extra_n=None):
        """Execute operator (d/y/c or case conversion) with a motion."""
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
        elif op in ("g~", "gU", "gu"):
            func = self._case_func(op)
            if linewise:
                sy, sx, ty, tx = sy, 0, ty, len(self.buf.lines[ty])
            self._change_case_range(sy, sx, ty, tx, func)

    # ── Normal mode ────────────────────────────────────────────────────


    # ── Paste ──────────────────────────────────────────────────────────

    def _paste_after(self):
        self.cy, self.cx, changed = edit_paste(
            self.buf.lines, self.cy, self.cx, self.register, self.reg_linewise,
        )
        if changed:
            self.buf.dirty = True
        return changed

    def _paste_before(self):
        self.cy, self.cx, changed = edit_paste(
            self.buf.lines, self.cy, self.cx, self.register, self.reg_linewise, before=True,
        )
        if changed:
            self.buf.dirty = True
        return changed

    # ── Insert mode ────────────────────────────────────────────────────



    # ── Visual mode ────────────────────────────────────────────────────




    # ── Search ─────────────────────────────────────────────────────────






    # ── Main loop ──────────────────────────────────────────────────────

    def run(self):
        self.term.enter_raw()
        signal.signal(signal.SIGWINCH, lambda *_: self._handle_resize())
        try:
            while self.running:
                self.render()
                if self._splash:
                    timeout = None if self._splash_until is None else max(0.0, self._splash_until - time.monotonic())
                    ready, _, _ = select.select([self.term.fd], [], [], timeout)
                    if not ready:
                        self._splash = False
                        continue
                    self._splash = False
                elif self._yank_flash:
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
                    self._cancel_pending()
                    self._startup_completion = False
                    self._clear_completion()
                    self.mode = Mode.NORMAL
                    self.cmd = ""
                    self.cmd_cx = 0
                    continue
                if self.mode == Mode.NORMAL and self._pending_mkdir_write:
                    if key == "CTRL_C":
                        self._cancel_pending()
                        self.msg = "Write cancelled"
                    else:
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
