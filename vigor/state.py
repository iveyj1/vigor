"""Buffer content and per-buffer editor state."""

import os
from enum import Enum


class Mode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    COMMAND = "COMMAND"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL LINE"
    SEARCH = "SEARCH"


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

    def serialized(self):
        return "".join(line + "\n" for line in self.lines)

    def save(self, path=None):
        p = path or self.path
        if not p:
            return False
        with open(p, "w") as f:
            f.write(self.serialized())
        self.path = p
        self.dirty = False
        return True


class BufferState:
    """Per-buffer state: buffer content, cursor, scroll, and undo history."""

    __slots__ = (
        "buf", "cx", "cy", "scroll", "wrap_skip",
        "md_view", "md_lines", "md_maps", "md_languages", "filetype_override", "autodetect",
        "_undo_stack", "_redo_stack",
        "_undo_save_depth", "_undo_branched",
    )

    def __init__(self, path=None):
        self.buf = Buffer(path)
        self.cx = 0
        self.cy = 0
        self.scroll = 0
        self.wrap_skip = 0
        self.md_view = False
        self.md_lines = None
        self.md_maps = None
        self.md_languages = None
        self.filetype_override = None
        self.autodetect = None
        self._undo_stack = []
        self._redo_stack = []
        self._undo_save_depth = 0
        self._undo_branched = False
