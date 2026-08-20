"""Raw terminal ownership and logical input decoding."""

import atexit
import os
import re
import select
import sys
import termios
import tty


MOUSE_DISABLE = "\x1b[?1002l\x1b[?1000l\x1b[?1006l"


class Terminal:
    """Raw mode management and key reading."""

    def __init__(self, mouse_mode="off"):
        self.fd = sys.stdin.fileno()
        self.old_attrs = termios.tcgetattr(self.fd)
        self.mouse_mode = mouse_mode
        atexit.register(self.restore)

    def _mouse_enable(self):
        if self.mouse_mode == "off":
            return ""
        motion = "\x1b[?1002h" if self.mouse_mode == "visual" else ""
        return "\x1b[?1000h" + motion + "\x1b[?1006h"

    def set_mouse(self, mode):
        """Apply configured SGR mouse reporting while the terminal is owned."""
        self.mouse_mode = mode
        sys.stdout.write(MOUSE_DISABLE + self._mouse_enable())
        sys.stdout.flush()

    def enter_raw(self):
        tty.setraw(self.fd)
        # Disable autowrap while vigor owns the terminal. The renderer clears
        # each line after writing it; if a full-width line autowraps first,
        # that clear can hit the next row and smear scrolling/status text.
        sys.stdout.write("\x1b[?2004h\x1b[?7l" + self._mouse_enable())
        sys.stdout.flush()

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_attrs)
        # Disable reporting/paste, restore autowrap, show cursor, clear screen on exit.
        sys.stdout.write(MOUSE_DISABLE + "\x1b[?2004l\x1b[?7h\x1b[?25h\x1b[2J\x1b[H")
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
        sys.stdout.write(MOUSE_DISABLE + "\x1b[?2004l\x1b[?7h\x1b[0 q\x1b[?25h")
        sys.stdout.flush()

    def read_key(self):
        """Read a single keypress. Decode escape sequences."""
        b = os.read(self.fd, 1)
        if not b:
            return ""
        ch = b[0]
        if ch == 0x1B:  # ESC
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
                    if len(seq) > 48:
                        return ""
                code = bytes(seq)
                if code.startswith(b"<"):
                    return self._decode_mouse(code)
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
                if code == b"Z":
                    return "SHIFT_TAB"
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
        if ch == 3:
            return "CTRL_C"
        if ch == 4:
            return "CTRL_D"
        if ch == 5:
            return "CTRL_E"
        if ch == 21:
            return "CTRL_U"
        if ch == 25:
            return "CTRL_Y"
        if ch == 18:
            return "CTRL_R"
        if ch == 26:
            return "CTRL_Z"
        if ch < 32:
            return ""
        return chr(ch)

    @staticmethod
    def _decode_mouse(code):
        """Decode one SGR mouse sequence into a structured logical event."""
        match = re.fullmatch(rb"<(\d+);(\d+);(\d+)([Mm])", code)
        if not match:
            return ""
        value, x, y = (int(match.group(i)) for i in range(1, 4))
        modifiers = value & (4 | 8 | 16)
        if value & 64:
            button = "wheel"
            action = "down" if value & 1 else "up"
        else:
            button = ("left", "middle", "right", "none")[value & 3]
            action = "release" if match.group(4) == b"m" else ("drag" if value & 32 else "press")
        return ("MOUSE", button, action, max(0, x - 1), max(0, y - 1), modifiers)

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
        ready, _, _ = select.select([self.fd], [], [], 0.02)
        return bool(ready)
