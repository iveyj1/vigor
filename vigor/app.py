#!/usr/bin/env python3

import sys
import os
import signal
import shutil
import select
import time

from .commands import CommandMixin
from .editing import EditingMixin
from .layout import RenderMixin
from .modes import ModeMixin
from .state import BufferState, Mode
from .terminal import Terminal

# ── Editor ─────────────────────────────────────────────────────────────────

class Editor(CommandMixin, ModeMixin, EditingMixin, RenderMixin):
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
        self.md_languages = bs.md_languages
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
        self.opt_mouse = "off"  # :set mouse=off|scroll|cursor|visual
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
        self._mouse_anchor = None  # source position saved on a possible Visual drag
        self._mouse_dragged = False
        self._yank_flash = None     # (expires, sy, sx, ey, ex, linewise)
        self.quickfix_state = None  # BufferState holding last quickfix results
        self.quickfix_cwd = os.getcwd()
        self.last_key = ""  # last decoded key read from terminal
        self._load_config()
        self.term = Terminal(self.opt_mouse)
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
        self.md_view, self.md_lines, self.md_maps, self.md_languages = False, None, None, None
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
        self._mouse_anchor = None
        self._mouse_dragged = False

    # ── Buffer management ──────────────────────────────────────────────

    def _save_buf_state(self):
        """Save working attributes back into current BufferState."""
        bs = self.buffers[self.buf_idx]
        bs.buf = self.buf
        bs.cx, bs.cy, bs.scroll = self.cx, self.cy, self.scroll
        bs.wrap_skip = self._wrap_skip
        bs.md_view, bs.md_lines, bs.md_maps = self.md_view, self.md_lines, self.md_maps
        bs.md_languages = self.md_languages
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
        self.md_languages = bs.md_languages
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
        """Move the viewport top by one displayed row."""
        self.scroll, self._wrap_skip, moved = self._viewport_layout().move_top(delta)
        return moved

    def _cursor_view_row(self):
        """Return the cursor's display row relative to the viewport top."""
        return self._viewport_layout().source_view_row(self.cy, self.cx)

    def _position_at_view_row(self, target, col):
        """Move cursor to a viewport display row, preserving column where possible."""
        position = self._viewport_layout().position_at_view_row(target, col)
        if position:
            self.cy, self.cx = position

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
        layout = self._viewport_layout()
        target = layout.nearest_visible(self.cy, 1)
        if target is None:
            return
        self.scroll = target
        self._wrap_skip = (self._cursor_wrap_row(self._wrap_cols())
                           if self.opt_wrap and target == self.cy else 0)
        for _ in range(self.rows // 2):
            if not self._move_view_top(-1):
                break

    def _ensure_scroll(self):
        """Adjust viewport only as far as needed to keep cursor visible."""
        origin, skip = self._viewport_layout().origin()
        if origin is None:
            return
        self.scroll, self._wrap_skip = origin, skip
        margin = min(self.opt_scrolloff, max(0, (self.rows - 1) // 2))
        row = self._cursor_view_row()
        while row < margin and self._move_view_top(-1):
            row += 1
        bottom = self.rows - 1 - margin
        while row > bottom and self._move_view_top(1):
            row -= 1

    # ── Main loop ──────────────────────────────────────────────────────

    def _mouse_position(self, x, y):
        """Map a content-area mouse cell to a source position."""
        if y < 0 or y >= self.rows:
            return None
        layout = self._viewport_layout()
        hscroll = 0 if self.opt_wrap else max(
            0, self._cursor_display_col() - layout.content_cols + 1,
        )
        return layout.screen_to_source(y, x, hscroll)

    def _handle_mouse(self, event):
        """Handle wheel, click-to-position, and characterwise Visual drag."""
        _, button, action, x, y, _modifiers = event
        if button == "wheel":
            self._scroll_view(-1 if action == "up" else 1, 3)
            return
        if self.opt_mouse not in ("cursor", "visual") or button != "left":
            return
        position = self._mouse_position(x, y)
        if not position:
            if action == "release":
                self._mouse_anchor = None
                self._mouse_dragged = False
            return
        if action == "press":
            self.cy, self.cx = position
            self._sticky_cx = None
            if self.opt_mouse == "visual":
                self._mouse_anchor = position
                self._mouse_dragged = False
        elif action == "drag" and self.opt_mouse == "visual" and self._mouse_anchor:
            if not self._mouse_dragged:
                if self.mode == Mode.INSERT:
                    self._save_dot()
                elif self.mode in (Mode.COMMAND, Mode.SEARCH):
                    self.cmd = ""
                    self.cmd_cx = 0
                    self._reset_history_nav()
                    self._clear_completion()
                self.vy, self.vx = self._mouse_anchor
                self.mode = Mode.VISUAL
                self._mouse_dragged = True
            self.cy, self.cx = position
        elif action == "release" and self._mouse_anchor:
            if self._mouse_dragged:
                self.cy, self.cx = position
            self._mouse_anchor = None
            self._mouse_dragged = False
        self._clamp_cursor()

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
                if isinstance(key, tuple):
                    if key[0] == "PASTE":
                        self.last_key = "PASTE"
                        self.handle_paste(key[1])
                    elif key[0] == "MOUSE":
                        self.last_key = key
                        self._handle_mouse(key)
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
