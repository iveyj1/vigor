"""Normal, Insert, Visual, and Search mode dispatch."""

import re

from .highlight import literal_ignorecase
from .state import Mode


class ModeMixin:
    """Modal input handlers mixed into the application orchestrator."""

    def handle_normal(self, key):
        if key not in ("j", "k", "DOWN", "UP"):
            self._sticky_cx = None
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
                line = self.buf.lines[self.cy]
                end = min(self.cx + repl_n, len(line))
                replacement = key * (end - self.cx)
                if line[self.cx:end] != replacement:
                    self._snapshot()
                    self.buf.lines[self.cy] = line[:self.cx] + replacement + line[end:]
                    self.buf.dirty = True
                self._save_dot()
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # f/t/F/T prefix: wait for target character. This must run before
        # count-prefix parsing so digit targets like f3 are accepted.
        if self._pending_find:
            cmd, find_n = self._pending_find
            self._pending_find = None
            if self.pending_op:
                # In operator-pending mode: route through _exec_operator
                op = self.pending_op
                self.pending_op = ""
                self.pending_count = 0
                self.pending_extra_n = None
                self._pending_find_for_op = (cmd, key)
                applied = self._exec_operator(op, cmd, find_n)
                if applied and op in ("d", "yd", ">", "<", "g~", "gU", "gu"):
                    self._save_dot()
                elif not applied:
                    self._recording = False
                    self._recording_keys = []
            else:
                self._exec_find(cmd, key, find_n)
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
            if key == "d":
                if self.buf.dirty:
                    self.msg = "No write since last change (add ! to override)"
                elif len(self.buffers) <= 1:
                    self.msg = "Cannot delete last buffer"
                else:
                    self._close_buffer()
            elif key == "j":
                self._quickfix_step(1)
            elif key == "k":
                self._quickfix_step(-1)
            elif key == "w":
                self.opt_wrap = not self.opt_wrap
                self.msg = "wrap on" if self.opt_wrap else "wrap off"
                self._ensure_scroll()
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
            else:
                # Unknown leader combination: Space is a no-op and this key
                # continues through normal dispatch.
                pass
            if key in ("d", "j", "k", "w", "n", "N", "c", "o"):
                return

        # 'g' prefix: wait for second key
        if self._pending_g:
            self._pending_g = False
            if key == "g":
                key = "gg"
            elif key == "c":
                # gcc — toggle comment (enter pending for second c)
                self._enter_op_pending("gc", n, extra_n)
                return
            elif key in ("~", "U", "u"):
                self._enter_op_pending("g" + key, n, extra_n)
                return
            elif key in ("*", "#"):
                self._search_word_under_cursor(1 if key == "*" else -1, whole=False)
                return
            elif key == "v":
                self._restore_visual_selection()
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
                self._toggle_comment(self.cy, op_n)
                self._save_dot()
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
                self._pending_find = (key, op_n * n)
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
                    changes = self._range_changes(sy, sx, ey, ex)
                    if op == ">" or changes and op in ("d", "yd", "c"):
                        self._snapshot()
                    if op == "d":
                        if changes:
                            self._delete_range(sy, sx, ey, ex, copy=self.opt_delcopy)
                        self._save_dot()
                    elif op == "yd":
                        if changes:
                            self._delete_range(sy, sx, ey, ex, copy=True)
                        self._save_dot()
                    elif op == "y":
                        self._yank_range(sy, sx, ey, ex)
                    elif op == "c":
                        if changes:
                            self._delete_range(sy, sx, ey, ex)
                        self._enter_insert(snapshot=not changes)
                    elif op in (">", "<"):
                        (self._indent_lines if op == ">" else self._dedent_lines)(sy, ey - sy + 1)
                        self._save_dot()
                    else:
                        self._change_case_range(sy, sx, ey, ex, self._case_func(op))
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
            # Doubled operator = line-wise (dd, yy, cc, >>, <<, g~~, gUU, guu)
            if key == (op[-1] if op in ("g~", "gU", "gu") else op) or (op == "yd" and key == "d"):
                if op == "d":
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    if self._range_changes(self.cy, 0, end, 0, linewise=True):
                        self._snapshot()
                        self._delete_range(self.cy, 0, end, 0, linewise=True, copy=self.opt_delcopy)
                    self._save_dot()
                elif op == "yd":
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    if self._range_changes(self.cy, 0, end, 0, linewise=True):
                        self._snapshot()
                        self._delete_lines(self.cy, op_n)
                    self._save_dot()
                elif op == "y":
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    self._yank_range(self.cy, 0, end, 0, linewise=True)
                    self.msg = f"{op_n} line(s) yanked"
                elif op == "c":
                    # cc: yank lines, clear to single empty line, insert
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    changed = end > self.cy or bool(self.buf.lines[self.cy])
                    if changed:
                        self._snapshot()
                        text = "\n".join(self.buf.lines[self.cy:end + 1])
                        self._set_register(text, linewise=True)
                        del self.buf.lines[self.cy + 1:end + 1]
                        self.buf.lines[self.cy] = ""
                        self.cx = 0
                        self.buf.dirty = True
                    self._enter_insert(snapshot=not changed)
                elif op == ">":
                    self._snapshot()
                    self._indent_lines(self.cy, op_n)
                    self._save_dot()
                elif op == "<":
                    self._dedent_lines(self.cy, op_n)
                    self._save_dot()
                elif op in ("g~", "gU", "gu"):
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    self._change_case_range(self.cy, 0, end, len(self.buf.lines[end]), self._case_func(op))
                    self._save_dot()
            else:
                applied = self._exec_operator(op, key, op_n * n, extra_n=extra_n)
                if applied and op in ("d", "yd", ">", "<", "g~", "gU", "gu"):
                    self._save_dot()
                elif not applied:
                    self._recording = False
                    self._recording_keys = []
                # c enters insert — recording continues
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # Standard motions
        if self._exec_motion(key, n, extra_n=extra_n):
            pass  # motion already executed
        # f/t/F/T — wait for target char
        elif key in ("f", "t", "F", "T"):
            self._pending_find = (key, n)
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
            if self.cx < len(self.buf.lines[self.cy]):
                self._snapshot()
                self._delete_to_eol()
            self._save_dot()
        elif key == "Y":
            self._yank_range(self.cy, self.cx, self.cy, len(self.buf.lines[self.cy]))
            self.msg = "yanked"
        elif key == "C":
            self._start_dot(n, "C")
            changed = self.cx < len(self.buf.lines[self.cy])
            if changed:
                self._snapshot()
                self._delete_to_eol()
            self._enter_insert(snapshot=not changed)
        elif key == "~":
            self._start_dot(n, "~")
            line = self.buf.lines[self.cy]
            end = min(self.cx + n, len(line))
            if self.cx < end:
                self._change_case_range(self.cy, self.cx, self.cy, end, str.swapcase)
                self.cx = end
            self._save_dot()
        elif key == "J":
            self._start_dot(n, "J")
            if self.cy < len(self.buf.lines) - 1:
                self._snapshot()
                self._join_lines(n)
            self._save_dot()
        # Paste
        elif key in ("x", "DEL"):
            self._start_dot(n, key)
            line = self.buf.lines[self.cy]
            if line and self.cx < len(line):
                self._snapshot()
                end = min(self.cx + n, len(line))
                self._delete_range(self.cy, self.cx, self.cy, end)
            self._save_dot()
        elif key == "X" or key == "BACKSPACE":
            self._start_dot(n, [key])
            if self.cx > 0:
                self._snapshot()
                start = max(self.cx - n, 0)
                self._delete_range(self.cy, start, self.cy, self.cx)
            self._save_dot()
        elif key == "r":
            self._start_dot(n, "r")
            self._pending_replace = n
            return
        elif key == "s":
            self._start_dot(n, "s")
            line = self.buf.lines[self.cy]
            changed = self.cx < len(line)
            if changed:
                self._snapshot()
                self._delete_range(self.cy, self.cx, self.cy, min(self.cx + n, len(line)))
            self._enter_insert(snapshot=not changed)
        elif key == "p":
            if self.register:
                self._start_dot(n, "p")
                self._snapshot()
                self._paste_after()
                self._save_dot()
        elif key == "P":
            if self.register:
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
            self.cmd_cx = 0
        elif key == "i":
            self._start_dot(n, "i")
            self._enter_insert(snapshot=True)
        elif key == "a":
            self._start_dot(n, "a")
            self.cx += 1
            self._enter_insert(snapshot=True)
        elif key == "I":
            self._start_dot(n, "I")
            line = self.buf.lines[self.cy]
            self.cx = len(line) - len(line.lstrip())
            self._enter_insert(snapshot=True)
        elif key == "A":
            self._start_dot(n, "A")
            self.cx = len(self.buf.lines[self.cy])
            self._enter_insert(snapshot=True)
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
            self.cmd_cx = 0
        elif key == "?":
            self.search_dir = -1
            self.mode = Mode.SEARCH
            self.cmd = ""
            self.cmd_cx = 0
        elif key in ("*", "#"):
            self._search_word_under_cursor(1 if key == "*" else -1, whole=True)
        elif key == "n":
            self._search_next(self.search_dir)
        elif key == "N":
            self._search_next(-self.search_dir)
        elif key == "CTRL_E":
            self._scroll_view(1, n)
        elif key == "CTRL_Y":
            self._scroll_view(-1, n)
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

    def handle_paste(self, text):
        """Handle bracketed paste without interpreting bytes as commands."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.mode == Mode.INSERT:
            if not text:
                return
            self._prepare_insert_change()
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
            text = text.replace("\n", " ")
            self.cmd = self.cmd[:self.cmd_cx] + text + self.cmd[self.cmd_cx:]
            self.cmd_cx += len(text)
        else:
            self.msg = "Paste ignored outside Insert/Command/Search"

    def handle_insert(self, key):
        # Dot repeat recording in insert mode
        if self._recording and not self._replaying_dot:
            self._recording_keys.append(key)
        if key not in ("UP", "DOWN"):
            self._sticky_cx = None
        if key == "ESC":
            # Save dot recording if active
            self._save_dot()
            self._insert_snapshot_pending = False
            # Stay in place — vig divergence from vi
            self.mode = Mode.NORMAL
            self._clamp_cursor()
            return
        if key == "ENTER":
            self._prepare_insert_change()
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
                self._prepare_insert_change()
                line = self.buf.lines[self.cy]
                self.buf.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
                self.cx -= 1
                self.buf.dirty = True
            elif self.cy > 0:
                # Join with previous line
                self._prepare_insert_change()
                prev = self.buf.lines[self.cy - 1]
                cur = self.buf.lines.pop(self.cy)
                self.cy -= 1
                self.cx = len(prev)
                self.buf.lines[self.cy] = prev + cur
                self.buf.dirty = True
        elif key in ("LEFT", "RIGHT", "UP", "DOWN", "HOME", "END"):
            self._exec_motion(key, 1)
        elif key == "TAB":
            self._prepare_insert_change()
            line = self.buf.lines[self.cy]
            if self._effective_filetype() == "make":
                self.buf.lines[self.cy] = line[:self.cx] + "\t" + line[self.cx:]
                self.cx += 1
            else:
                spaces = 4 - (self.cx % 4)
                self.buf.lines[self.cy] = line[:self.cx] + " " * spaces + line[self.cx:]
                self.cx += spaces
            self.buf.dirty = True
        elif key == "DEL":
            line = self.buf.lines[self.cy]
            if self.cx < len(line):
                self._prepare_insert_change()
                self.buf.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
                self.buf.dirty = True
        elif len(key) == 1:
            self._prepare_insert_change()
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

    def _remember_visual_selection(self):
        """Save the current Visual mode, anchor, and endpoint for gv."""
        self.buffers[self.buf_idx].last_visual = (
            self.mode, self.vx, self.vy, self.cx, self.cy,
        )

    def _restore_visual_selection(self):
        """Restore the current buffer's last Visual selection."""
        saved = self.buffers[self.buf_idx].last_visual
        if not saved:
            self.msg = "No previous Visual selection"
            return
        self.mode, self.vx, self.vy, self.cx, self.cy = saved
        self.vy = max(0, min(self.vy, len(self.buf.lines) - 1))
        self.vx = max(0, min(self.vx, len(self.buf.lines[self.vy])))
        self._clamp_cursor()
        self._ensure_scroll()

    def handle_visual(self, key):
        if key not in ("j", "k", "DOWN", "UP"):
            self._sticky_cx = None
        if key == "ESC":
            self._remember_visual_selection()
            self.mode = Mode.NORMAL
            return
        # Resolve pending find-char
        if self._pending_find:
            cmd, find_n = self._pending_find
            self._pending_find = None
            self._exec_find(cmd, key, find_n)
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
                    self._remember_visual_selection()
                    self._toggle_comment(sy, ey - sy + 1)
                self.mode = Mode.NORMAL
                return
            elif key in ("~", "U", "u"):
                sel = self._selection_range()
                if sel:
                    sy, sx, ey, ex = sel
                    self._remember_visual_selection()
                    self._change_case_range(sy, sx, ey, min(ex + 1, len(self.buf.lines[ey])), self._case_func("g" + key))
                self.mode = Mode.NORMAL
                return
            else:
                return
        if key == "g":
            self._pending_g = True
            return
        # f/t/F/T — wait for target char
        if key in ("f", "t", "F", "T"):
            self._pending_find = (key, 1)
            return
        # Edit operations on selection
        if key == "~":
            sel = self._selection_range()
            if sel:
                sy, sx, ey, ex = sel
                self._remember_visual_selection()
                self._change_case_range(sy, sx, ey, min(ex + 1, len(self.buf.lines[ey])), str.swapcase)
            self.mode = Mode.NORMAL
            return
        if key in ("d", "x"):
            self._remember_visual_selection()
            self._visual_delete()
            return
        if key == "y":
            self._remember_visual_selection()
            self._visual_yank()
            return
        if key == "c":
            self._remember_visual_selection()
            changed = self._visual_delete()
            self._enter_insert(snapshot=not changed)
            return
        if key in (">", "<"):
            sel = self._selection_range()
            if sel:
                sy, _, ey, _ = sel
                self._remember_visual_selection()
                if key == ">":
                    self._snapshot()
                (self._indent_lines if key == ">" else self._dedent_lines)(sy, ey - sy + 1)
            self.mode = Mode.NORMAL
            self._clamp_cursor()
            self._ensure_scroll()
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
        changed = self._range_changes(sy, sx, ey, ex, linewise)
        if changed:
            self._snapshot()
            self._delete_range(sy, sx, ey, ex, linewise)
        self.mode = Mode.NORMAL
        return changed

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

    def _word_under_cursor(self):
        """Return the small-word text under or just before the cursor."""
        line = self.buf.lines[self.cy]
        if not line:
            return ""
        x = min(self.cx, len(line) - 1)
        if self._char_class(line[x]) != 1 and x > 0 and self.cx >= len(line):
            x -= 1
        if self._char_class(line[x]) != 1:
            return ""
        start = x
        while start > 0 and self._char_class(line[start - 1]) == 1:
            start -= 1
        end = x + 1
        while end < len(line) and self._char_class(line[end]) == 1:
            end += 1
        return line[start:end]

    def _search_word_under_cursor(self, direction, whole):
        """Set search state from the word under cursor and jump to the next hit."""
        word = self._word_under_cursor()
        if not word:
            self.msg = "No word under cursor"
            return
        escaped = re.escape(word)
        self.search_pattern = rf"(?<!\w){escaped}(?!\w)" if whole else escaped
        self.search_ignorecase = not any(ch.isupper() for ch in word)
        self.search_dir = direction
        self._add_history(self.search_history, self.search_pattern)
        self._search_next(direction)

    def handle_search(self, key):
        """Handle input in search mode (/ or ?)."""
        if key == "ESC":
            self.mode = Mode.NORMAL
            self.cmd = ""
            self.cmd_cx = 0
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
            self.cmd_cx = 0
            self.mode = Mode.NORMAL
            self._reset_history_nav()
            if pattern:
                self.search_pattern = pattern
                self.search_ignorecase = literal_ignorecase(pattern)
                self._add_history(self.search_history, pattern)
            if self.search_pattern:
                self._search_next(self.search_dir)
            return
        if self._edit_prompt(key):
            return
        if key == "BACKSPACE":
            self.mode = Mode.NORMAL

    def _compile_search(self):
        """Compile the active search with its literal smart-case setting."""
        return re.compile(self.search_pattern, re.IGNORECASE if self.search_ignorecase else 0)

    def _search_next(self, direction):
        """Search for self.search_pattern in the given direction.
        direction: 1=forward, -1=backward."""
        if not self.search_pattern:
            self.msg = "No previous search"
            return
        try:
            pat = self._compile_search()
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
                self._center_cursor()
                return
        self.msg = f"Pattern not found: {self.search_pattern}"

