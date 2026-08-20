"""Buffer-level ranges, transformations, and paste operations."""


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
