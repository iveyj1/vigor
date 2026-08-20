"""Display-column, viewport mapping, and full-frame rendering."""

import os
import re
import sys
import time

from . import BUILD_ID, VERSION
from .highlight import (
    CURRENT_SEARCH_COLOR, SEARCH_COLOR, literal_ignorecase, markdown_spans,
    search_spans, syntax_spans,
)
from .state import Mode


TABSTOP = 4
SPLASH = (
    " _    ___                 ",
    "| |  / (_)___ _____  _____",
    "| | / / / __ `/ __ \\/ ___/",
    "| |/ / / /_/ / /_/ / /    ",
    "|___/_/\\__, /\\____/_/     ",
    "      /____/",
    "  -- markdown style -- ",
)
SPLASH_BG = "\x1b[49m"
SPLASH_FRAME = "\x1b[96m"
SPLASH_FG = "\x1b[97m"


def display_col(line, index):
    """Display column of a source index, expanding tabs to fixed stops."""
    col = 0
    for ch in line[:index]:
        col += TABSTOP - col % TABSTOP if ch == "\t" else 1
    return col


def display_index(line, target):
    """Source index at or immediately before a target display column."""
    col = 0
    for i, ch in enumerate(line):
        width = TABSTOP - col % TABSTOP if ch == "\t" else 1
        if col + width > target:
            return i
        col += width
        if col == target:
            return i + 1
    return len(line)


class VisibleRow:
    """One rendered content row and its source/display coordinates."""

    __slots__ = ("screen_row", "source_y", "wrap_row", "display_start", "text")

    def __init__(self, screen_row, source_y, wrap_row, display_start, text):
        self.screen_row = screen_row
        self.source_y = source_y
        self.wrap_row = wrap_row
        self.display_start = display_start
        self.text = text


class ViewportLayout:
    """Authoritative visible rows and bidirectional coordinate mapping.

    View callbacks keep Markdown projection policy outside this module while all
    viewport, wrap, gutter, and horizontal-offset arithmetic remains here.
    """

    def __init__(self, line_count, view_line, source_to_display, display_to_source,
                 rows, cols, gutter_width, wrap, wrapcol, scroll, wrap_skip,
                 hidden_line=None):
        self.line_count = line_count
        self.view_line = view_line
        self.source_to_display = source_to_display
        self.display_to_source = display_to_source
        self.rows = rows
        self.cols = cols
        self.gutter_width = gutter_width
        self.wrap = wrap
        self.wrapcol = wrapcol
        self.scroll = scroll
        self.wrap_skip = wrap_skip
        self.hidden_line = hidden_line or (lambda _y: False)

    @property
    def content_cols(self):
        return max(1, self.cols - self.gutter_width)

    @property
    def wrap_cols(self):
        available = self.content_cols
        return min(available, self.wrapcol) if self.wrapcol else available

    def line_rows(self, source_y):
        if self.hidden_line(source_y):
            return 0
        if not self.wrap:
            return 1
        return len(self.view_line(source_y)) // self.wrap_cols + 1

    def next_visible(self, source_y, direction):
        """Return the next non-hidden source line strictly in one direction."""
        y = source_y + direction
        while 0 <= y < self.line_count:
            if not self.hidden_line(y):
                return y
            y += direction
        return None

    def nearest_visible(self, source_y, direction=1):
        """Return a visible line at/near source_y, preferring direction."""
        if not self.line_count:
            return None
        y = max(0, min(source_y, self.line_count - 1))
        if not self.hidden_line(y):
            return y
        found = self.next_visible(y, direction)
        return found if found is not None else self.next_visible(y, -direction)

    def origin(self):
        """Return a normalized visible viewport origin and wrapped-row skip."""
        y = self.nearest_visible(self.scroll, 1)
        if y is None:
            return None, 0
        skip = self.wrap_skip if y == self.scroll and self.wrap else 0
        return y, min(max(0, skip), self.line_rows(y) - 1)

    def move_top(self, delta):
        """Move the viewport origin by one displayed row."""
        y, skip = self.origin()
        if y is None:
            return self.scroll, self.wrap_skip, False
        if delta > 0:
            if skip + 1 < self.line_rows(y):
                return y, skip + 1, True
            target = self.next_visible(y, 1)
            return (target, 0, True) if target is not None else (y, skip, False)
        if skip > 0:
            return y, skip - 1, True
        target = self.next_visible(y, -1)
        if target is None:
            return y, skip, False
        return target, self.line_rows(target) - 1, True

    def visible_rows(self, hscroll=0):
        """Return content rows from the normalized viewport origin."""
        result = []
        source_y, first_skip = self.origin()
        while source_y is not None and len(result) < self.rows:
            line = self.view_line(source_y)
            if self.wrap:
                for wrap_row in range(first_skip, self.line_rows(source_y)):
                    if len(result) >= self.rows:
                        break
                    start = wrap_row * self.wrap_cols
                    text = line[start:start + self.wrap_cols]
                    result.append(VisibleRow(len(result), source_y, wrap_row, start, text))
            else:
                start = max(0, hscroll)
                text = line[start:start + self.content_cols]
                result.append(VisibleRow(len(result), source_y, 0, start, text))
            source_y = self.next_visible(source_y, 1)
            first_skip = 0
        return result

    def source_view_row(self, source_y, source_x):
        """Return a source position's display row relative to the viewport."""
        origin_y, skip = self.origin()
        target_y = self.nearest_visible(source_y, 1)
        if origin_y is None or target_y is None or target_y < origin_y:
            return -1
        row, y = -skip, origin_y
        while y < target_y:
            row += self.line_rows(y)
            y = self.next_visible(y, 1)
            if y is None:
                return self.rows
        display_x = self.source_to_display(source_y, source_x) if target_y == source_y else 0
        return row + (display_x // self.wrap_cols if self.wrap else 0)

    def position_at_view_row(self, target, col):
        """Map a viewport display row and desired column to a source position."""
        y, skip = self.origin()
        while y is not None:
            available = self.line_rows(y) - skip
            if target < available:
                display_x = (skip + target) * self.wrap_cols + col if self.wrap else col
                return y, self.display_to_source(y, display_x)
            target -= available
            y, skip = self.next_visible(y, 1), 0
        return None

    def source_to_screen(self, source_y, source_x, hscroll=0):
        """Return zero-based content screen coordinates or None if not visible."""
        target_y = self.nearest_visible(source_y, 1)
        if target_y is None:
            return None
        display_x = self.source_to_display(source_y, source_x) if target_y == source_y else 0
        wanted_wrap = display_x // self.wrap_cols if self.wrap else 0
        for row in self.visible_rows(hscroll):
            if row.source_y == target_y and row.wrap_row == wanted_wrap:
                screen_x = display_x - row.display_start + self.gutter_width
                return row.screen_row, screen_x
        return None

    def screen_to_source(self, screen_y, screen_x, hscroll=0):
        """Map a zero-based content cell to a source position, clamped to text."""
        rows = self.visible_rows(hscroll)
        if screen_y < 0 or screen_y >= len(rows):
            return None
        row = rows[screen_y]
        content_x = max(0, screen_x - self.gutter_width)
        target = min(row.display_start + content_x, len(self.view_line(row.source_y)))
        return row.source_y, self.display_to_source(row.source_y, target)


class RenderMixin:
    """Full-frame rendering over the shared viewport layout."""

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
        """Return persistent or live-preview search spans for a buffer line."""
        try:
            if self.mode == Mode.SEARCH:
                if not self.cmd:
                    return ()
                flags = re.IGNORECASE if literal_ignorecase(self.cmd) else 0
                return search_spans(line, re.compile(self.cmd, flags))
            if self.opt_hlsearch and self.search_pattern:
                return search_spans(line, self._compile_search())
        except re.error:
            pass
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
        cursor_col = self._view_col(buf_line, self.cx) if buf_line == self.cy else -1
        current_search = next(((sx, ex) for sx, ex in search_spans if sx <= cursor_col < ex), None)
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
            current = current_search is not None and current_search[0] <= left < current_search[1]
            selected = select_start is not None and select_start <= left < select_end
            if color:
                out.append(color)
            if searched:
                out.append(CURRENT_SEARCH_COLOR if current else SEARCH_COLOR)
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
