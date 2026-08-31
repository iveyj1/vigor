"""Source-coordinate syntax, search, and Markdown presentation helpers."""

import os
import re


# Named ANSI colors and semantic maps are intentionally plain dictionaries so
# the palette can be changed without touching lexer or rendering code.
NAMED_COLORS = {
    "green": "\x1b[32m", "yellow": "\x1b[33m", "blue": "\x1b[34m",
    "magenta": "\x1b[35m", "cyan": "\x1b[36m", "bright_blue": "\x1b[94m",
    "bright_magenta": "\x1b[95m", "bold_cyan": "\x1b[1;36m",
    "bold_yellow": "\x1b[1;33m", "dim_cyan": "\x1b[2;36m",
    "search": "\x1b[43;30m", "current_search": "\x1b[45;97m",
}
SYNTAX_COLOR_NAMES = {
    "comment": "green", "string": "yellow", "number": "magenta",
    "keyword": "bright_blue", "type": "cyan", "constant": "magenta",
    "definition": "bold_cyan", "function": "cyan", "decorator": "bright_magenta",
    "preprocessor": "bright_magenta", "variable": "bright_magenta",
}
MARKDOWN_COLOR_NAMES = {
    "header": "bold_cyan", "marker": "bold_yellow", "table": "cyan",
    "table_rule": "dim_cyan",
}
SYNTAX_COLORS = {kind: NAMED_COLORS[name] for kind, name in SYNTAX_COLOR_NAMES.items()}
MD_HEADER = NAMED_COLORS[MARKDOWN_COLOR_NAMES["header"]]
MD_MARKER = NAMED_COLORS[MARKDOWN_COLOR_NAMES["marker"]]
MD_TABLE = NAMED_COLORS[MARKDOWN_COLOR_NAMES["table"]]
MD_TABLE_RULE = NAMED_COLORS[MARKDOWN_COLOR_NAMES["table_rule"]]
SEARCH_COLOR = NAMED_COLORS["search"]
CURRENT_SEARCH_COLOR = NAMED_COLORS["current_search"]
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _words(items):
    return r"\b(?:" + "|".join(items.split()) + r")\b"


def _lexer(rules):
    return re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in rules))


IDENT = r"[A-Za-z_]\w*"
NUMBER = r"\b(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|0[oO][0-7]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)\b"
C_NUMBER = r"\b(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)[uUlLfF]*\b"
PY_STRING = r"(?:[rRuUbBfF]{0,2})(?:\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
C_STRING = r"(?:u8|[uUL])?\"(?:\\.|[^\"\\])*\"|(?:u8|[uUL])?'(?:\\.|[^'\\])*'"
SH_STRING = r"\$?'(?:[^']*)'|\$?\"(?:\\.|[^\"\\])*\""

PYTHON_RULES = (
    ("string", PY_STRING), ("comment", r"#.*"),
    ("decorator", r"@" + IDENT + r"(?:\." + IDENT + r")*"),
    ("definition", r"(?<=def )" + IDENT + r"|(?<=class )" + IDENT),
    ("constant", _words("True False None NotImplemented Ellipsis")),
    ("keyword", _words("and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield")),
    ("number", NUMBER),
    ("function", IDENT + r"(?=\s*\()"),
)
C_KEYWORDS = "alignas alignof auto break case const continue default do else enum extern for goto if inline register restrict return sizeof static struct switch typedef union volatile while _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert _Thread_local"
CPP_KEYWORDS = C_KEYWORDS + " and and_eq asm bitand bitor catch class compl concept consteval constexpr constinit const_cast co_await co_return co_yield decltype delete dynamic_cast explicit export false friend mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public reinterpret_cast requires static_assert static_cast template this thread_local throw true try typeid typename using virtual wchar_t xor xor_eq"
C_TYPES = "bool char double float int long short signed unsigned void size_t ptrdiff_t FILE int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t"
C_BASE_RULES = (
    ("comment", r"//.*|/\*.*?(?:\*/|$)"), ("string", C_STRING),
    ("preprocessor", r"^\s*#\s*" + IDENT), ("constant", _words("NULL true false nullptr")),
    ("number", C_NUMBER),
)
C_RULES = C_BASE_RULES + (
    ("definition", r"(?<=struct )" + IDENT + r"|(?<=union )" + IDENT + r"|(?<=enum )" + IDENT),
    ("keyword", _words(C_KEYWORDS)), ("type", _words(C_TYPES)),
    ("function", IDENT + r"(?=\s*\()"),
)
CPP_RULES = C_BASE_RULES + (
    ("definition", r"(?<=class )" + IDENT + r"|(?<=struct )" + IDENT + r"|(?<=namespace )" + IDENT),
    ("keyword", _words(CPP_KEYWORDS)), ("type", _words(C_TYPES + " string nullptr_t")),
    ("function", IDENT + r"(?=\s*\()"),
)
BASH_RULES = (
    ("string", SH_STRING), ("comment", r"(?<!\S)#.*"),
    ("variable", r"\$(?:\{[^}\n]+\}|[A-Za-z_]\w*|\d+|[?#@*!$-])"),
    ("definition", IDENT + r"(?=\s*\(\s*\))"),
    ("keyword", _words("if then elif else fi for while until do done case esac in function select time coproc")),
    ("function", _words("alias bg bind break builtin caller cd command compgen complete continue declare dirs disown echo enable eval exec exit export fc fg getopts hash help history jobs kill let local logout mapfile popd printf pushd pwd read readarray readonly return set shift shopt source suspend test times trap type typeset ulimit umask unalias unset wait")),
    ("number", NUMBER),
)
LANGUAGE_LEXERS = {
    "python": _lexer(PYTHON_RULES), "c": _lexer(C_RULES),
    "cpp": _lexer(CPP_RULES), "bash": _lexer(BASH_RULES),
}
EXTENSION_LANGUAGES = {
    ".py": "python", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    ".sh": "bash", ".bash": "bash",
}
LANGUAGE_ALIASES = {
    "python": "python", "py": "python", "bash": "bash", "sh": "bash",
    "shell": "bash", "c": "c", "cpp": "cpp", "c++": "cpp", "cc": "cpp",
    "cxx": "cpp",
}
MD_FENCE = object()
MD_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SHELL_SHEBANG_RE = re.compile(
    r"^#!\s*(?:(?:\S*/)?env(?:\s+-S)?\s+)?(?:\S*/)?(?:bash|sh)(?=\s|$)"
)


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


def markdown_fence_languages(lines):
    """Map Markdown rows to a fenced-code language, marker sentinel, or prose."""
    result, active, marker, minimum = [None] * len(lines), None, None, 0
    for y, line in enumerate(lines):
        match = MD_FENCE_RE.match(line)
        if active is not None and match and match.group(1)[0] == marker:
            run, tail = match.groups()
            if len(run) >= minimum and not tail.strip():
                result[y], active, marker = MD_FENCE, None, None
                continue
        if active is None and match:
            run, tail = match.groups()
            info = tail.strip().split(maxsplit=1)[0].lower() if tail.strip() else ""
            active = LANGUAGE_ALIASES.get(info, "")
            marker, minimum, result[y] = run[0], len(run), MD_FENCE
        elif active is not None:
            result[y] = active
    return result


def build_markdown_view(lines):
    """Return non-destructive Markdown display lines, mappings, and fence metadata."""
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
    return ([item[0] for item in projected], [item[1] for item in projected],
            markdown_fence_languages(lines))


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


def language_for_path(path, first_line=""):
    """Select a language by extension, then an extensionless shell shebang."""
    extension = os.path.splitext(path or "")[1].lower()
    language = EXTENSION_LANGUAGES.get(extension)
    if language or extension or not path:
        return language
    return "bash" if SHELL_SHEBANG_RE.match(first_line) else None


def filetype_for_path(path, first_line=""):
    """Return an automatically detected file type, falling back to text."""
    name = os.path.basename(path or "")
    extension = os.path.splitext(path or "")[1].lower()
    if name in ("Makefile", "makefile", "GNUmakefile") or extension == ".mk":
        return "make"
    if extension in (".md", ".markdown"):
        return "markdown"
    return language_for_path(path, first_line) or "text"


def syntax_spans(path, line, language=None):
    """Return line-local syntax spans in source coordinates."""
    lexer = LANGUAGE_LEXERS.get(language or language_for_path(path))
    if not lexer:
        return ()
    return tuple((m.start(), m.end(), SYNTAX_COLORS[m.lastgroup]) for m in lexer.finditer(line))


def literal_ignorecase(pattern):
    """Return whether a literal search pattern uses lowercase smart-case."""
    meta = ".^$*+?{}[]\\|()"
    return not any(ch.isupper() for ch in pattern) and not any(ch in meta for ch in pattern)


def search_spans(line, pattern):
    """Return non-empty regex match spans in source coordinates."""
    return tuple((m.start(), m.end()) for m in pattern.finditer(line) if m.start() != m.end())
