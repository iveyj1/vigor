"""Raw terminal ownership and logical input decoding."""

import atexit
import os
import select
import sys
import termios
import tty


class Terminal:
    """Raw mode management and key reading."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_attrs = termios.tcgetattr(self.fd)
        atexit.register(self.restore)

    def enter_raw(self):
        tty.setraw(self.fd)
        # Disable autowrap while vigor owns the terminal. The renderer clears
        # each line after writing it; if a full-width line autowraps first,
        # that clear can hit the next row and smear scrolling/status text.
        sys.stdout.write("\x1b[?2004h\x1b[?7l")
        sys.stdout.flush()

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_attrs)
        # Disable bracketed paste, restore autowrap, show cursor, clear screen on exit
        sys.stdout.write("\x1b[?2004l\x1b[?7h\x1b[?25h\x1b[2J\x1b[H")
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
        sys.stdout.write("\x1b[?2004l\x1b[?7h\x1b[0 q\x1b[?25h")
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
