"""Source-coordinate syntax, search, and Markdown presentation helpers."""

import os
import re


SYNTAX_PATTERNS = {
    ".py": re.compile(r"(?P<string>(?:[rRuUbBfF]{0,2})(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'))|(?P<comment>#.*)"),
    ".c": re.compile(r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|(?P<comment>//.*|/\*.*?\*/)"),
    ".h": re.compile(r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|(?P<comment>//.*|/\*.*?\*/)"),
    ".sh": re.compile(r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|(?P<comment>(?<!\S)#.*)"),
    ".bash": re.compile(r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|(?P<comment>(?<!\S)#.*)"),
}
SYNTAX_COLORS = {"string": "\x1b[33m", "comment": "\x1b[32m"}
MD_HEADER = "\x1b[1;36m"
MD_MARKER = "\x1b[1;33m"
MD_TABLE = "\x1b[36m"
MD_TABLE_RULE = "\x1b[2;36m"
SEARCH_COLOR = "\x1b[43;30m"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def expand_with_map(line, padding=None):
    """Return tab-expanded text and source-index to display-column mapping."""
    padding = padding or {}
    out, mapping, col = [], [], 0
    for i, ch in enumerate(line):
        pad = padding.get(i, 0)
        if pad:
            out.append(" " * pad)
            col += pad
        mapping.append(col)
        width = 4 - col % 4 if ch == "\t" else 1
        out.append(" " * width if ch == "\t" else ch)
        col += width
    pad = padding.get(len(line), 0)
    if pad:
        out.append(" " * pad)
        col += pad
    mapping.append(col)
    return "".join(out), mapping


def markdown_cells(line):
    """Return Markdown table cell ranges, or None for a non-table row."""
    pipes = [i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")]
    if not pipes:
        return None
    leading = not line[:pipes[0]].strip()
    trailing = not line[pipes[-1] + 1:].strip()
    starts = ([] if leading else [0]) + [p + 1 for p in pipes if not (trailing and p == pipes[-1])]
    ends = (pipes[1:] if leading else pipes[:]) + ([] if trailing else [len(line)])
    cells = list(zip(starts, ends))
    return cells if len(cells) >= 2 else None


def markdown_rule(line, cells):
    return all(re.fullmatch(r":?-{3,}:?", line[a:b].strip()) for a, b in cells)


def build_markdown_view(lines):
    """Return non-destructive Markdown display lines and source mappings."""
    projected = [expand_with_map(line) for line in lines]
    parsed = [markdown_cells(line) for line in lines]
    used = set()
    for rule_y, cells in enumerate(parsed):
        if not cells or not markdown_rule(lines[rule_y], cells) or rule_y in used:
            continue
        count, start, end = len(cells), rule_y, rule_y
        while start > 0 and parsed[start - 1] and len(parsed[start - 1]) == count:
            start -= 1
        while end + 1 < len(parsed) and parsed[end + 1] and len(parsed[end + 1]) == count:
            end += 1
        if start == end:
            continue
        widths = [0] * count
        for y in range(start, end + 1):
            for i, (a, b) in enumerate(parsed[y]):
                widths[i] = max(widths[i], len(lines[y][a:b].expandtabs(4)))
        for y in range(start, end + 1):
            padding = {}
            for i, (a, b) in enumerate(parsed[y]):
                width = len(lines[y][a:b].expandtabs(4))
                padding[b] = max(padding.get(b, 0), widths[i] - width)
            projected[y] = expand_with_map(lines[y], padding)
            used.add(y)
    return [item[0] for item in projected], [item[1] for item in projected]


def markdown_spans(lines, line, y):
    """Return restrained header, list, and table styles."""
    if re.match(r"^ {0,3}#{1,6}(?:\s|$)", line):
        return ((0, len(line), MD_HEADER),)
    marker = re.match(r"^\s*(?:[-+*]|\d+[.)])(?=\s)", line)
    if marker:
        return ((marker.start(), marker.end(), MD_MARKER),)
    cells = markdown_cells(line)
    if not cells:
        return ()
    count, top, bottom = len(cells), y, y
    while top > 0 and markdown_cells(lines[top - 1]) and len(markdown_cells(lines[top - 1])) == count:
        top -= 1
    while bottom + 1 < len(lines) and markdown_cells(lines[bottom + 1]) and len(markdown_cells(lines[bottom + 1])) == count:
        bottom += 1
    valid = any(markdown_rule(lines[i], markdown_cells(lines[i])) for i in range(top, bottom + 1))
    if not valid:
        return ()
    if markdown_rule(line, cells):
        return ((0, len(line), MD_TABLE_RULE),)
    return tuple((i, i + 1, MD_TABLE) for i, ch in enumerate(line)
                 if ch == "|" and (i == 0 or line[i - 1] != "\\"))


def syntax_spans(path, line):
    """Return line-local syntax spans in source coordinates."""
    pattern = SYNTAX_PATTERNS.get(os.path.splitext(path or "")[1].lower())
    if not pattern:
        return ()
    return tuple((m.start(), m.end(), SYNTAX_COLORS[m.lastgroup]) for m in pattern.finditer(line))


def search_spans(line, pattern):
    """Return non-empty regex match spans in source coordinates."""
    return tuple((m.start(), m.end()) for m in pattern.finditer(line) if m.start() != m.end())
