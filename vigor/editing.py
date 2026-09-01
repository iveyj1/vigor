"""Undo, motions, operators, registers, and buffer mutations."""

import base64
import shutil
import sys
import time

from .highlight import build_markdown_view, filetype_for_path
from .layout import ViewportLayout, display_col, display_index, expand_tabs_for_display
from .state import Mode


def delete_range(lines, sy, sx, ey, ex, linewise=False):
    """Delete a half-open range and return text, cursor row/column, and change flag."""
    if not linewise and (sy, sx) == (ey, ex):
        return "", sy, sx, False
    if linewise:
        deleted = lines[sy:ey + 1]
        text = "\n".join(deleted)
        del lines[sy:ey + 1]
        if not lines:
            lines.append("")
        return text, min(sy, len(lines) - 1), 0, True
    if sy == ey:
        line = lines[sy]
        text = line[sx:ex]
        if not text:
            return "", sy, sx, False
        lines[sy] = line[:sx] + line[ex:]
    else:
        first, last = lines[sy], lines[ey]
        parts = [first[sx:]] + lines[sy + 1:ey] + [last[:ex]]
        text = "\n".join(parts)
        lines[sy] = first[:sx] + last[ex:]
        del lines[sy + 1:ey + 1]
    return text, sy, sx, True


def yank_range(lines, sy, sx, ey, ex, linewise=False):
    """Return text from a half-open range without changing the buffer."""
    if linewise:
        return "\n".join(lines[sy:ey + 1])
    if sy == ey:
        return lines[sy][sx:ex]
    return "\n".join([lines[sy][sx:]] + lines[sy + 1:ey] + [lines[ey][:ex]])


def change_case(lines, sy, sx, ey, ex, transform):
    """Transform a half-open range and report whether content changed."""
    changed = False
    for y in range(sy, ey + 1):
        line = lines[y]
        start = sx if y == sy else 0
        end = ex if y == ey else len(line)
        replacement = transform(line[start:end])
        if replacement != line[start:end]:
            changed = True
        lines[y] = line[:start] + replacement + line[end:]
    return changed


def paste(lines, cy, cx, text, linewise=False, before=False):
    """Paste register text while preserving the one-string-per-line invariant."""
    if not text:
        return cy, cx, False
    parts = text.split("\n")
    if linewise:
        at = cy if before else cy + 1
        lines[at:at] = parts
        return at, 0, True

    line = lines[cy]
    at = cx if before else min(cx + 1, len(line))
    if len(parts) == 1:
        lines[cy] = line[:at] + text + line[at:]
        return cy, at + len(text) - 1, True

    lines[cy] = line[:at] + parts[0]
    lines[cy + 1:cy + 1] = parts[1:-1] + [parts[-1] + line[at:]]
    return cy + len(parts) - 1, max(0, len(parts[-1]) - 1), True



class EditingMixin:
    """Undo, motions, operators, registers, and buffer mutations."""

    _MOTION_KEYS = frozenset(
        "h l j k w W b B e E G 0 ^ $".split()
        + ["LEFT", "RIGHT", "DOWN", "UP", "HOME", "END", "gg", "g0", "g^", "g$", "CTRL_D", "CTRL_U"]
    )
    _FIND_DISPATCH = {"f": "_motion_f", "F": "_motion_F", "t": "_motion_t", "T": "_motion_T"}
    _FIND_REVERSE = {"f": "F", "F": "f", "t": "T", "T": "t"}
    _BRACKETS = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{"}
    _OPEN_BRACKETS = frozenset("([{")

    def _snapshot(self):
        """Save current state for undo. Call before any mutation."""
        self.md_view, self.md_lines, self.md_maps, self.md_languages = False, None, None, None
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
        self.md_view, self.md_lines, self.md_maps, self.md_languages = False, None, None, None
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
        self.md_view, self.md_lines, self.md_maps, self.md_languages = False, None, None, None
        if not self._redo_stack:
            self.msg = "Already at newest change"
            return
        self._undo_stack.append((self.buf.lines[:], self.cx, self.cy))
        self.buf.lines, self.cx, self.cy = self._redo_stack.pop()
        self._update_dirty()
        self._clamp_cursor()
        self._ensure_scroll()

    def _update_dirty(self):
        """Recalculate dirty state; undoing to disk also retires recovery."""
        dirty = self._undo_branched or len(self._undo_stack) != self._undo_save_depth
        self.buf.dirty = dirty
        if not dirty:
            self._delete_recovery(self.buffers[self.buf_idx])

    def _enter_insert(self, snapshot=False):
        """Enter Insert, deferring its undo boundary until the first mutation."""
        self._sticky_cx = None
        self._insert_word_count = 0
        self._insert_last_space = True
        self._insert_snapshot_pending = snapshot
        self.mode = Mode.INSERT

    def _prepare_insert_change(self):
        if self._insert_snapshot_pending:
            self._snapshot()
            self._insert_snapshot_pending = False

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

    def _motion_h(self):
        self.cx -= 1
        self._clamp_cursor()

    def _motion_l(self):
        self.cx += 1
        self._clamp_cursor()

    def _effective_filetype(self):
        """Return the forced or automatically detected current-buffer type."""
        if self.filetype_override:
            return self.filetype_override
        if self.buffer_autodetect:
            return filetype_for_path(self.buf.path, self.buf.lines[0])
        return "text"

    def _set_markdown_view(self, enabled):
        self.md_view = enabled
        if enabled:
            self.md_lines, self.md_maps, self.md_languages = build_markdown_view(self.buf.lines)
        else:
            self.md_lines = self.md_maps = self.md_languages = None
        self._wrap_skip = 0
        self._ensure_scroll()

    def _view_line(self, y):
        if self._is_markdown_fence_line(y):
            return ""
        visible_tabs = self.opt_list or self._effective_filetype() == "make"
        return self.md_lines[y] if self.md_view else expand_tabs_for_display(self.buf.lines[y], visible_tabs)

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
            self.opt_wrapcol, self.opt_wordwrap, self.scroll, self._wrap_skip,
            hidden_line=self._is_markdown_fence_line,
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
        layout = self._viewport_layout()
        row, local_col = layout.wrap_position(self.cy, display_x)
        if self._sticky_cx is None:
            self._sticky_cx = local_col
        col = self._sticky_cx
        current_rows = layout.line_rows(self.cy)
        if current_rows == 0:
            target_y = layout.next_visible(self.cy, delta)
            if target_y is not None:
                self.cy, self.cx = target_y, self._view_index(target_y, col)
        elif delta > 0:
            if row + 1 < current_rows:
                start, end = layout.wrap_segments(self.cy)[row + 1]
                self.cx = self._view_index(self.cy, min(start + col, end))
            else:
                target_y = layout.next_visible(self.cy, 1)
                if target_y is not None:
                    start, end = layout.wrap_segments(target_y)[0]
                    self.cy, self.cx = target_y, self._view_index(target_y, min(start + col, end))
        elif row > 0:
            start, end = layout.wrap_segments(self.cy)[row - 1]
            self.cx = self._view_index(self.cy, min(start + col, end))
        else:
            target_y = layout.next_visible(self.cy, -1)
            if target_y is not None:
                segments = layout.wrap_segments(target_y)
                start, end = segments[-1]
                self.cy = target_y
                self.cx = self._view_index(target_y, min(start + col, end))
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

    def _motion_display_edge(self, key):
        """Move to an edge/nonblank position of the current wrapped row."""
        layout = self._viewport_layout()
        row = layout.wrap_position(self.cy, self._cursor_display_col())[0]
        start, end = layout.wrap_segments(self.cy)[row]
        target = start
        if key == "g^":
            line = self._view_line(self.cy)
            target = next((i for i in range(start, end) if not line[i].isspace()), start)
        elif key == "g$":
            target = max(start, end - 1)
        self.cx = self._view_index(self.cy, target)

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
            "g0": lambda: self._motion_display_edge("g0"),
            "g^": lambda: self._motion_display_edge("g^"),
            "g$": lambda: self._motion_display_edge("g$"),
            "0": self._motion_zero,
            "^": self._motion_caret,
            "$": self._motion_dollar,
            "HOME": self._motion_home,
            "END": self._motion_end,
            "CTRL_D": self._motion_ctrl_d,
            "CTRL_U": self._motion_ctrl_u,
        }
        repeat = 1 if key in ("G", "gg", "g0", "g^", "g$", "0", "^", "$", "HOME", "END") else n
        for _ in range(repeat):
            handlers[key]()
        return True

    def _motion_f(self, ch, n=1):
        """Move to nth occurrence of ch to the right on current line."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.find(ch, pos + 1)
            if idx == -1:
                return False
            pos = idx
        self.cx = pos
        return True

    def _motion_F(self, ch, n=1):
        """Move to nth occurrence of ch to the left on current line."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.rfind(ch, 0, pos)
            if idx == -1:
                return False
            pos = idx
        self.cx = pos
        return True

    def _motion_t(self, ch, n=1):
        """Move to just before nth occurrence of ch to the right."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.find(ch, pos + 1)
            if idx == -1:
                return False
            pos = idx
        self.cx = pos - 1 if pos > 0 else 0
        return True

    def _motion_T(self, ch, n=1):
        """Move to just after nth occurrence of ch to the left."""
        line = self.buf.lines[self.cy]
        pos = self.cx
        for _ in range(n):
            idx = line.rfind(ch, 0, pos)
            if idx == -1:
                return False
            pos = idx
        self.cx = pos + 1
        return True

    def _exec_find(self, cmd, ch, n=1):
        """Execute a find-char motion, returning whether it found the target."""
        self.last_find = (cmd, ch)
        return getattr(self, self._FIND_DISPATCH[cmd])(ch, n)

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

    def _indent_lines(self, start, count):
        """Add 4 spaces to beginning of count lines starting at start."""
        for i in range(start, min(start + count, len(self.buf.lines))):
            self.buf.lines[i] = "    " + self.buf.lines[i]
        self.buf.dirty = True

    def _dedent_lines(self, start, count):
        """Remove up to 4 leading spaces, creating undo only when needed."""
        end = min(start + count, len(self.buf.lines))
        if not any(self.buf.lines[i].startswith(" ") for i in range(start, end)):
            return False
        self._snapshot()
        for i in range(start, end):
            line = self.buf.lines[i]
            remove = min(4, len(line) - len(line.lstrip(" ")))
            self.buf.lines[i] = line[remove:]
        self.buf.dirty = True
        return True

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
        changed = []
        for line in lines:
            new = line
            if all_commented:
                stripped = line.lstrip()
                indent = line[:len(line) - len(stripped)]
                if stripped.startswith(prefix):
                    new = indent + stripped[len(prefix):]
                elif stripped.startswith(self.opt_comment):
                    new = indent + stripped[len(self.opt_comment):]
            elif line.strip():
                indent = line[:len(line) - len(line.lstrip())]
                new = indent + prefix + line.lstrip()
            changed.append(new)
        if changed == lines:
            return False
        self._snapshot()
        self.buf.lines[start:end] = changed
        self.buf.dirty = True
        return True

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

    def _apply_motion(self, motion_key, n, extra_n=None):
        """Return an operator motion destination, or None when the motion fails."""
        saved_cy, saved_cx = self.cy, self.cx
        if self._pending_find_for_op:
            cmd, ch = self._pending_find_for_op
            self._pending_find_for_op = None
            if not self._exec_find(cmd, ch, n):
                return None
        elif motion_key in ("w", "W"):
            for _ in range(n):
                before = (self.cy, self.cx)
                self.motion_w(big=motion_key == "W")
                if (self.cy, self.cx) == before:
                    if self.cy == len(self.buf.lines) - 1 and self.cx < len(self.buf.lines[self.cy]):
                        self.cx = len(self.buf.lines[self.cy])
                    break
        elif not self._exec_motion(motion_key, n, extra_n=extra_n):
            return None
        self._clamp_cursor()
        result = (self.cy, self.cx)
        self.cy, self.cx = saved_cy, saved_cx
        failed_if_still = ("h", "LEFT", "l", "RIGHT", "j", "DOWN", "k", "UP",
                           "w", "W", "b", "B", "CTRL_D", "CTRL_U")
        return None if result == (saved_cy, saved_cx) and motion_key in failed_if_still else result

    def _is_linewise_motion(self, key):
        """j, k, G, gg, and doubled operators are linewise."""
        return key in ("j", "k", "DOWN", "UP", "G", "gg", "CTRL_D", "CTRL_U")

    def _range_changes(self, sy, sx, ey, ex, linewise=False):
        return (not (self.buf.lines == [""] and sy == ey == 0) if linewise
                else (sy, sx) != (ey, ex))

    def _delete_range(self, sy, sx, ey, ex, linewise=False, copy=True):
        """Delete text from (sy,sx) to (ey,ex). Returns deleted text."""
        text, self.cy, self.cx, changed = delete_range(
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
        text = yank_range(self.buf.lines, sy, sx, ey, ex, linewise)
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
        if not text:
            return ""
        self.buf.lines[self.cy] = line[:self.cx]
        self._set_register(text, linewise=False)
        self.buf.dirty = True
        return text

    def _case_func(self, op):
        """Return the character transform for a case operator."""
        return {"g~": str.swapcase, "gU": str.upper, "gu": str.lower}[op]

    def _change_case_range(self, sy, sx, ey, ex, func):
        """Apply a case transform, creating undo only when content changes."""
        parts = (self.buf.lines[y][sx if y == sy else 0:ex if y == ey else len(self.buf.lines[y])]
                 for y in range(sy, ey + 1))
        if all(func(part) == part for part in parts):
            return False
        self._snapshot()
        change_case(self.buf.lines, sy, sx, ey, ex, func)
        self.buf.dirty = True
        return True

    def _exec_operator(self, op, motion_key, n, extra_n=None):
        """Execute operator (d/y/c or case conversion) with a motion."""
        linewise = self._is_linewise_motion(motion_key)
        target = self._apply_motion(motion_key, n, extra_n=extra_n)
        if target is None:
            return False
        ty, tx = target
        sy, sx = self.cy, self.cx
        # Normalize range
        if (sy, sx) > (ty, tx):
            sy, sx, ty, tx = ty, tx, sy, sx
        # Inclusive motions include their end character.
        if motion_key in ("e", "E", "f", "t", "g$"):
            tx += 1
            if not linewise and ty < len(self.buf.lines):
                tx = min(tx, len(self.buf.lines[ty]))

        if not linewise and sy != ty and motion_key in ("w", "W"):
            ty = sy
            tx = len(self.buf.lines[sy])

        changes = self._range_changes(sy, sx, ty, tx, linewise)
        if op == ">" or changes and op in ("d", "yd", "c"):
            self._snapshot()

        if op == "d":
            if changes:
                self._delete_range(sy, sx, ty, tx, linewise, copy=self.opt_delcopy)
        elif op == "yd":
            if changes:
                self._delete_range(sy, sx, ty, tx, linewise, copy=True)
        elif op == "y":
            self._yank_range(sy, sx, ty, tx, linewise)
            self.msg = f"{ty - sy + 1} lines yanked" if linewise else "yanked"
        elif op == "c":
            if changes:
                self._delete_range(sy, sx, ty, tx, linewise)
            self._enter_insert(snapshot=not changes)
        elif op in (">", "<"):
            (self._indent_lines if op == ">" else self._dedent_lines)(sy, ty - sy + 1)
        elif op in ("g~", "gU", "gu"):
            func = self._case_func(op)
            if linewise:
                sy, sx, ty, tx = sy, 0, ty, len(self.buf.lines[ty])
            self._change_case_range(sy, sx, ty, tx, func)
        return True

    def _paste_after(self):
        self.cy, self.cx, changed = paste(
            self.buf.lines, self.cy, self.cx, self.register, self.reg_linewise,
        )
        if changed:
            self.buf.dirty = True
        return changed

    def _paste_before(self):
        self.cy, self.cx, changed = paste(
            self.buf.lines, self.cy, self.cx, self.register, self.reg_linewise, before=True,
        )
        if changed:
            self.buf.dirty = True
        return changed
