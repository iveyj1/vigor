#!/usr/bin/env python3
"""ved — a modal, vi-inspired terminal text editor. ANSI only. Radical simplicity."""

import sys
import os
import re
import base64
import termios
import tty
import atexit
import signal
import shutil
from enum import Enum

# ── Modes ──────────────────────────────────────────────────────────────────

class Mode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    COMMAND = "COMMAND"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL LINE"

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

# ── Terminal ───────────────────────────────────────────────────────────────

class Terminal:
    """Raw mode management and key reading."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_attrs = termios.tcgetattr(self.fd)
        atexit.register(self.restore)

    def enter_raw(self):
        tty.setraw(self.fd)

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_attrs)
        # Show cursor, clear screen on exit
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()

    def read_key(self):
        """Read a single keypress. Decode escape sequences."""
        b = os.read(self.fd, 1)
        if not b:
            return ""
        ch = b[0]
        if ch == 0x1B:  # ESC
            # Try to read escape sequence
            seq = os.read(self.fd, 2) if self._has_data() else b""
            if len(seq) == 0:
                return "ESC"
            if seq[0:1] == b"[":
                code = seq[1:2]
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
                # Read more for extended sequences (e.g. \x1b[3~)
                if code and code[0:1].isdigit():
                    extra = os.read(self.fd, 1) if self._has_data() else b""
                    if extra == b"~":
                        if code == b"3":
                            return "DEL"
                return "ESC"
            return "ESC"
        if ch == 127 or ch == 8:
            return "BACKSPACE"
        if ch == 13:
            return "ENTER"
        if ch == 9:
            return "TAB"
        if ch < 32:
            return ""
        return chr(ch)

    def _has_data(self):
        """Check if stdin has data available (non-blocking)."""
        import select
        r, _, _ = select.select([self.fd], [], [], 0.02)
        return bool(r)

# ── Editor ─────────────────────────────────────────────────────────────────

class Editor:
    def __init__(self, path=None):
        self.buf = Buffer(path)
        self.cx = 0  # cursor column
        self.cy = 0  # cursor row (buffer line)
        self.scroll = 0  # first visible line
        self.mode = Mode.NORMAL
        self.cmd = ""  # command-line input
        self.msg = ""  # status message
        self.vx = 0  # visual anchor column
        self.vy = 0  # visual anchor row
        self.rows = 24
        self.cols = 80
        self.running = True
        self.count = 0  # pending count prefix (0 = no count)
        self.pending_op = ""  # operator-pending: 'd', 'y', 'c', or ""
        self.pending_count = 0  # count saved when entering operator-pending
        self.register = ""  # unnamed register (last yank/delete text)
        self.reg_linewise = False  # was last register content linewise?
        self.search_pattern = ""  # last / or ? search
        self.search_dir = 1  # 1=forward, -1=backward
        self.opt_wrap = False  # :set wrap
        self.opt_number = False  # :set number
        self.opt_relnum = False  # :set relativenumber
        self.term = Terminal()
        self._update_size()

    def _update_size(self):
        sz = shutil.get_terminal_size()
        self.cols = sz.columns
        self.rows = sz.lines - 2  # reserve 2 lines: status + command

    def _handle_resize(self):
        """Called on SIGWINCH. Update size, re-clamp, and redraw."""
        self._update_size()
        self._clamp_cursor()
        self._ensure_scroll()
        self.render()

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
        if self.cy < self.scroll:
            self.scroll = self.cy
        if self.cy >= self.scroll + self.rows:
            self.scroll = self.cy - self.rows + 1

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
        ch = self._get_char(self.cy, self.cx)
        if ch is None:
            # On empty line or past end — try next line
            if self.cy + 1 < len(self.buf.lines):
                self.cy += 1
                self.cx = 0
            return
        cur_class = classify(ch)
        pos = (self.cy, self.cx)
        # Skip current class
        while pos:
            c = self._get_char(*pos)
            if c is None or classify(c) != cur_class:
                break
            pos = self._forward(*pos)
        # Skip spaces
        while pos:
            c = self._get_char(*pos)
            if c is None or classify(c) != 0:
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
            if c is None or classify(c) != 0:
                break
            pos = self._backward(*pos)
        if pos is None:
            self.cy, self.cx = 0, 0
            return
        # Now on the last char of the prev word — find its start
        target_class = classify(self._get_char(*pos))
        while True:
            prev = self._backward(*pos)
            if prev is None:
                break
            c = self._get_char(*prev)
            if c is None or classify(c) != target_class:
                break
            pos = prev
        self.cy, self.cx = pos

    def motion_e(self, big=False):
        """Move to end of word (e) or WORD (E)."""
        classify = self._WORD_class if big else self._char_class
        # Step forward one position first
        pos = self._forward(self.cy, self.cx)
        if pos is None:
            return
        # Skip spaces
        while pos:
            c = self._get_char(*pos)
            if c is None or classify(c) != 0:
                break
            pos = self._forward(*pos)
        if pos is None:
            return
        # Now on the first char of a word — find its end
        target_class = classify(self._get_char(*pos))
        while True:
            nxt = self._forward(*pos)
            if nxt is None:
                break
            c = self._get_char(*nxt)
            if c is None or classify(c) != target_class:
                break
            pos = nxt
        self.cy, self.cx = pos

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

    def render(self):
        out = []
        out.append("\x1b[?25l")  # hide cursor
        out.append("\x1b[H")     # cursor home

        sel = self._selection_range()

        for row in range(self.rows):
            buf_line = self.scroll + row
            if buf_line < len(self.buf.lines):
                line = self.buf.lines[buf_line]
                visible = line[:self.cols]
                if sel:
                    sy, sx, ey, ex = sel
                    if sy <= buf_line <= ey:
                        # Compute highlight range for this line
                        hl_start = sx if buf_line == sy else 0
                        hl_end = ex if buf_line == ey else len(visible)
                        # Clamp
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
                    else:
                        out.append(visible)
                else:
                    out.append(visible)
            else:
                out.append("~")
            out.append("\x1b[K")  # clear to end of line
            out.append("\r\n")

        # Status bar (reverse video)
        out.append("\x1b[7m")
        fname = self.buf.path or "[No Name]"
        dirty = " [+]" if self.buf.dirty else ""
        mode_str = self.mode.value
        count_str = str(self.count) if self.count > 0 else ""
        left = f" {mode_str} | {fname}{dirty}"
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
        else:
            out.append(self.msg[:self.cols] if self.msg else "")
        out.append("\x1b[K")

        # Position real cursor
        screen_y = self.cy - self.scroll + 1  # 1-indexed
        screen_x = self.cx + 1                # 1-indexed
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

    def _set_register(self, text, linewise=False):
        """Store text in unnamed register and copy to system clipboard."""
        self.register = text
        self.reg_linewise = linewise
        self._osc52_copy(text)

    # ── Operator-pending motion execution ──────────────────────────────

    def _apply_motion(self, motion_key, n):
        """Execute a motion n times from current position.
        Returns (new_cy, new_cx) without modifying cursor."""
        saved_cy, saved_cx = self.cy, self.cx
        for _ in range(n):
            if motion_key == "h" or motion_key == "LEFT":
                self.cx -= 1
                self._clamp_cursor()
            elif motion_key == "l" or motion_key == "RIGHT":
                self.cx += 1
                self._clamp_cursor()
            elif motion_key == "j" or motion_key == "DOWN":
                self.cy += 1
                self._clamp_cursor()
            elif motion_key == "k" or motion_key == "UP":
                self.cy -= 1
                self._clamp_cursor()
            elif motion_key == "w":
                self.motion_w(big=False)
            elif motion_key == "W":
                self.motion_w(big=True)
            elif motion_key == "b":
                self.motion_b(big=False)
            elif motion_key == "B":
                self.motion_b(big=True)
            elif motion_key == "e":
                self.motion_e(big=False)
            elif motion_key == "E":
                self.motion_e(big=True)
            else:
                # Unknown motion — abort
                self.cy, self.cx = saved_cy, saved_cx
                return None
        result = (self.cy, self.cx)
        self.cy, self.cx = saved_cy, saved_cx
        return result

    def _is_linewise_motion(self, key):
        """j, k, and doubled operators are linewise."""
        return key in ("j", "k", "DOWN", "UP")

    # ── Delete/Yank/Change helpers ─────────────────────────────────────

    def _delete_range(self, sy, sx, ey, ex, linewise=False):
        """Delete text from (sy,sx) to (ey,ex). Returns deleted text."""
        if linewise:
            # Delete entire lines sy..ey
            deleted = self.buf.lines[sy:ey + 1]
            text = "\n".join(deleted)
            del self.buf.lines[sy:ey + 1]
            if not self.buf.lines:
                self.buf.lines = [""]
            self.cy = min(sy, len(self.buf.lines) - 1)
            self.cx = 0
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
                    text += "\n" + self.buf.lines[sy + 1]
                    # Don't delete yet, indices shift
                text += "\n" + last[:ex]
                # Now rebuild: keep first[:sx] + last[ex:], delete middle
                self.buf.lines[sy] = first[:sx] + last[ex:]
                del self.buf.lines[sy + 1:ey + 1]
            self.cy = sy
            self.cx = sx
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
        return text

    def _exec_operator(self, op, motion_key, n):
        """Execute operator (d/y/c) with a motion."""
        linewise = self._is_linewise_motion(motion_key)
        target = self._apply_motion(motion_key, n)
        if target is None:
            return
        ty, tx = target
        sy, sx = self.cy, self.cx
        # Normalize range
        if (sy, sx) > (ty, tx):
            sy, sx, ty, tx = ty, tx, sy, sx
        # Inclusive motions (e, E): include the end character
        if motion_key in ("e", "E"):
            tx += 1
            if not linewise and ty < len(self.buf.lines):
                tx = min(tx, len(self.buf.lines[ty]))

        if op == "d":
            self._delete_range(sy, sx, ty, tx, linewise)
        elif op == "y":
            self._yank_range(sy, sx, ty, tx, linewise)
            self.msg = f"{ty - sy + 1} lines yanked" if linewise else "yanked"
        elif op == "c":
            self._delete_range(sy, sx, ty, tx, linewise)
            self.mode = Mode.INSERT

    # ── Normal mode ────────────────────────────────────────────────────

    def handle_normal(self, key):
        # Count prefix accumulation
        if key.isdigit() and (self.count > 0 or key != "0"):
            self.count = self.count * 10 + int(key)
            return

        n = max(self.count, 1)
        self.count = 0  # reset after consuming

        # Operator-pending: waiting for a motion after d/y/c
        if self.pending_op:
            op = self.pending_op
            op_n = self.pending_count
            self.pending_op = ""
            self.pending_count = 0
            # Doubled operator = line-wise (dd, yy, cc)
            if key == op:
                if op == "d":
                    self._delete_lines(self.cy, op_n)
                elif op == "y":
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    self._yank_range(self.cy, 0, end, 0, linewise=True)
                    self.msg = f"{op_n} line(s) yanked"
                elif op == "c":
                    # cc: yank lines, clear to single empty line, insert
                    end = min(self.cy + op_n - 1, len(self.buf.lines) - 1)
                    text = "\n".join(self.buf.lines[self.cy:end + 1])
                    self._set_register(text, linewise=True)
                    del self.buf.lines[self.cy + 1:end + 1]
                    self.buf.lines[self.cy] = ""
                    self.cx = 0
                    self.buf.dirty = True
                    self.mode = Mode.INSERT
            else:
                self._exec_operator(op, key, op_n * n)
            self._clamp_cursor()
            self._ensure_scroll()
            return

        # Standard motions
        if key == "h" or key == "LEFT":
            for _ in range(n):
                self.cx -= 1
                self._clamp_cursor()
        elif key == "l" or key == "RIGHT":
            for _ in range(n):
                self.cx += 1
                self._clamp_cursor()
        elif key == "j" or key == "DOWN":
            for _ in range(n):
                self.cy += 1
                self._clamp_cursor()
        elif key == "k" or key == "UP":
            for _ in range(n):
                self.cy -= 1
                self._clamp_cursor()
        elif key == "w":
            for _ in range(n):
                self.motion_w(big=False)
        elif key == "W":
            for _ in range(n):
                self.motion_w(big=True)
        elif key == "b":
            for _ in range(n):
                self.motion_b(big=False)
        elif key == "B":
            for _ in range(n):
                self.motion_b(big=True)
        elif key == "e":
            for _ in range(n):
                self.motion_e(big=False)
        elif key == "E":
            for _ in range(n):
                self.motion_e(big=True)
        # Operators — enter pending state
        elif key == "d":
            self.pending_op = "d"
            self.pending_count = n
            return  # wait for motion
        elif key == "y":
            self.pending_op = "y"
            self.pending_count = n
            return
        elif key == "c":
            self.pending_op = "c"
            self.pending_count = n
            return
        # Line-wise shortcuts
        elif key == "D":
            # Delete from cursor to end of line
            line = self.buf.lines[self.cy]
            text = line[self.cx:]
            self.buf.lines[self.cy] = line[:self.cx]
            self._set_register(text, linewise=False)
            self.buf.dirty = True
        elif key == "Y":
            # Yank entire line (like yy)
            end = min(self.cy + n - 1, len(self.buf.lines) - 1)
            self._yank_range(self.cy, 0, end, 0, linewise=True)
            self.msg = f"{n} line(s) yanked"
        elif key == "C":
            # Change from cursor to end of line
            line = self.buf.lines[self.cy]
            text = line[self.cx:]
            self.buf.lines[self.cy] = line[:self.cx]
            self._set_register(text, linewise=False)
            self.buf.dirty = True
            self.mode = Mode.INSERT
        # Paste
        elif key == "p":
            self._paste_after()
        elif key == "P":
            self._paste_before()
        elif key == ":":
            self.mode = Mode.COMMAND
            self.cmd = ""
        elif key == "i":
            self.mode = Mode.INSERT
        elif key == "a":
            self.cx += 1
            self.mode = Mode.INSERT
        elif key == "I":
            line = self.buf.lines[self.cy]
            self.cx = len(line) - len(line.lstrip())
            self.mode = Mode.INSERT
        elif key == "A":
            self.cx = len(self.buf.lines[self.cy])
            self.mode = Mode.INSERT
        elif key == "v":
            self.vx, self.vy = self.cx, self.cy
            self.mode = Mode.VISUAL
        elif key == "V":
            self.vx, self.vy = self.cx, self.cy
            self.mode = Mode.VISUAL_LINE
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

    def handle_insert(self, key):
        if key == "ESC":
            # Stay in place — ved divergence from vi
            self.mode = Mode.NORMAL
            self._clamp_cursor()
            return
        if key == "ENTER":
            line = self.buf.lines[self.cy]
            self.buf.lines[self.cy] = line[:self.cx]
            self.buf.lines.insert(self.cy + 1, line[self.cx:])
            self.cy += 1
            self.cx = 0
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
        elif len(key) == 1:
            line = self.buf.lines[self.cy]
            self.buf.lines[self.cy] = line[:self.cx] + key + line[self.cx:]
            self.cx += 1
            self.buf.dirty = True
        self._clamp_cursor()
        self._ensure_scroll()

    # ── Command mode ───────────────────────────────────────────────────

    def handle_command(self, key):
        if key == "ESC":
            self.mode = Mode.NORMAL
            self.cmd = ""
            return
        if key == "ENTER":
            self._exec_command(self.cmd)
            self.cmd = ""
            return
        if key == "BACKSPACE":
            if self.cmd:
                self.cmd = self.cmd[:-1]
            else:
                self.mode = Mode.NORMAL
            return
        if len(key) == 1:
            self.cmd += key

    def _exec_command(self, raw):
        parts = raw.strip().split(None, 1)
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
            self.running = False
        elif cmd in ("q!", "quit!"):
            self.running = False
        elif cmd in ("w", "write"):
            path = arg or self.buf.path
            if self.buf.save(path):
                n = len(self.buf.lines)
                self.msg = f'"{self.buf.path}" {n}L written'
            else:
                self.msg = "No file name"
            self.mode = Mode.NORMAL
        elif cmd == "wq":
            path = arg or self.buf.path
            if self.buf.save(path):
                self.running = False
            else:
                self.msg = "No file name"
                self.mode = Mode.NORMAL
        elif cmd in ("e", "edit"):
            if arg:
                self.buf = Buffer(arg)
                self.cx = 0
                self.cy = 0
                self.scroll = 0
                self.msg = f'"{arg}"'
            else:
                self.msg = "No file name"
            self.mode = Mode.NORMAL
        elif cmd == "new":
            self.buf = Buffer()
            self.cx = 0
            self.cy = 0
            self.scroll = 0
            self.msg = "[New]"
            self.mode = Mode.NORMAL
        else:
            self.msg = f"Not a command: {cmd}"
            self.mode = Mode.NORMAL

    # ── Visual mode ────────────────────────────────────────────────────

    def handle_visual(self, key):
        if key == "ESC":
            self.mode = Mode.NORMAL
            return
        # Edit operations on selection
        if key in ("d", "x"):
            self._visual_delete()
            return
        if key == "y":
            self._visual_yank()
            return
        if key == "c":
            self._visual_delete()
            self.mode = Mode.INSERT
            return
        # Motions — same as normal
        if key == "h" or key == "LEFT":
            self.cx -= 1
        elif key == "l" or key == "RIGHT":
            self.cx += 1
        elif key == "j" or key == "DOWN":
            self.cy += 1
        elif key == "k" or key == "UP":
            self.cy -= 1
        elif key == "w":
            self.motion_w(big=False)
        elif key == "W":
            self.motion_w(big=True)
        elif key == "b":
            self.motion_b(big=False)
        elif key == "B":
            self.motion_b(big=True)
        elif key == "e":
            self.motion_e(big=False)
        elif key == "E":
            self.motion_e(big=True)
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

    # ── Main loop ──────────────────────────────────────────────────────

    def run(self):
        self.term.enter_raw()
        signal.signal(signal.SIGWINCH, lambda *_: self._handle_resize())

        while self.running:
            self.render()
            key = self.term.read_key()
            if not key:
                continue
            # Clear message on any key (unless entering command mode)
            if self.mode != Mode.COMMAND:
                self.msg = ""

            if self.mode == Mode.NORMAL:
                self.handle_normal(key)
            elif self.mode == Mode.INSERT:
                self.handle_insert(key)
            elif self.mode == Mode.COMMAND:
                self.handle_command(key)
            elif self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
                self.handle_visual(key)

        self.term.restore()

# ── Entry point ────────────────────────────────────────────────────────────

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    ed = Editor(path)
    ed.run()

if __name__ == "__main__":
    main()
