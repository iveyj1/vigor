"""Display-column and viewport layout shared by rendering and mouse input."""


TABSTOP = 4


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
                 rows, cols, gutter_width, wrap, wrapcol, scroll, wrap_skip):
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

    @property
    def content_cols(self):
        return max(1, self.cols - self.gutter_width)

    @property
    def wrap_cols(self):
        available = self.content_cols
        return min(available, self.wrapcol) if self.wrapcol else available

    def line_rows(self, source_y):
        if not self.wrap:
            return 1
        return len(self.view_line(source_y)) // self.wrap_cols + 1

    def visible_rows(self, hscroll=0):
        """Return content rows from the current viewport origin."""
        result = []
        source_y = self.scroll
        while len(result) < self.rows and source_y < self.line_count:
            line = self.view_line(source_y)
            if self.wrap:
                start_row = self.wrap_skip if source_y == self.scroll else 0
                for wrap_row in range(start_row, self.line_rows(source_y)):
                    if len(result) >= self.rows:
                        break
                    start = wrap_row * self.wrap_cols
                    text = line[start:start + self.wrap_cols]
                    result.append(VisibleRow(len(result), source_y, wrap_row, start, text))
            else:
                start = max(0, hscroll)
                text = line[start:start + self.content_cols]
                result.append(VisibleRow(len(result), source_y, 0, start, text))
            source_y += 1
        return result

    def source_to_screen(self, source_y, source_x, hscroll=0):
        """Return zero-based content screen coordinates or None if not visible."""
        display_x = self.source_to_display(source_y, source_x)
        wanted_wrap = display_x // self.wrap_cols if self.wrap else 0
        for row in self.visible_rows(hscroll):
            if row.source_y == source_y and row.wrap_row == wanted_wrap:
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
