# Module Architecture Plan

### Status

Module split implemented on `architecture/module-split`; pending review and merge. Follow-up cleanup of remaining no-op undo boundaries is tracked separately from the structural migration.

The installed editor may contain multiple source files. Python stdlib-only and raw-ANSI constraints remain. No packaging framework or pip installation is required.

### Goals

- Preserve current behavior and all PTY tests during migration.
- Replace the monolithic runtime with modules organized by responsibility.
- Establish one authoritative source/display layout for rendering, collapsed Markdown rows, and mouse coordinate mapping.
- Give autosave and other timed behavior one main-loop deadline mechanism.
- Keep dependencies direct and inspectable; do not introduce a framework, plugin API, or unnecessary abstraction.

### Target Layout

```text
vig
vighelp
vigor/
    __init__.py             version and build identification
    __main__.py             module entry point
    app.py                  editor orchestration and event loop
    state.py                buffers and per-buffer state
    terminal.py             raw terminal and input decoding
    layout.py               viewport, rendering, and coordinate mapping
    editing.py              motions, ranges, operators, and mutations
    modes.py                modal key dispatch
    commands.py             ex commands, completion, quickfix, and processes
    highlight.py            search, syntax, and Markdown spans
```

The launcher and PTY harness invoke `python3 -m vigor`; no compatibility `vig.py` remains.

### Dependency Direction

- `state.py` has no editor dependencies.
- `terminal.py` has no editor dependencies.
- `editing.py`, `highlight.py`, and `layout.py` depend on state, not application orchestration.
- `modes.py` and `commands.py` may call a narrow application interface for buffer switching and process-level actions.
- `app.py` composes the modules and owns the event loop.
- Circular imports are not acceptable.

### Layout Contract

`layout.py` must eventually provide both directions of coordinate mapping:

```python
layout.source_to_screen(line, column)
layout.screen_to_source(row, column)
```

The same visible-row representation must drive:

- Rendering and cursor placement
- Tabs, gutters, wrapping, and horizontal scrolling
- Hidden Markdown projection rows
- Mouse cursor positioning and Visual selection
- Search and syntax span clipping

Mouse or Markdown features must not add parallel coordinate arithmetic.

### Timer Contract

The application loop owns deadlines for splash dismissal, yank flash, and future autosave. Autosave must not use a background thread. The loop waits until either terminal input or the nearest deadline.

### Migration Phases

**Phase A — package scaffold and low-coupling state — complete**

- Add `vigor` package and module entry point.
- Extract `Buffer`, `BufferState`, and `Terminal` without behavioral changes.
- Move the existing editor to `vigor/app.py`.
- Keep a temporary `vig.py` entry point.
- Update installation and build stamping.

**Phase B — highlighting and presentation — complete**

- Extract syntax, search-span, and Markdown projection logic.
- Keep spans in source coordinates.
- Preserve line-local highlighting behavior.

**Phase C — shared layout — complete**

- Introduce visible-row records and bidirectional coordinate mapping.
- Move viewport, wrap, gutter, rendering, and cursor placement together.
- Preserve exact-width EOL and oversized-line behavior.

**Phase D — editing core — structurally complete**

- Extract motions, range normalization, register operations, undo, and mutations.
- Enforce the buffer invariant that each `Buffer.lines` item contains no newline.
- Empty-register paste no longer creates an undo snapshot; complete no-op undo cleanup is deferred as a focused behavior change.

**Phase E — modes and commands — complete**

- Split modal dispatch from editing primitives.
- Extract ex parsing, completion, quickfix, clipboard, and subprocess execution.
- Keep orchestration in `app.py`.

**Phase F — module entry point — complete**

- The launcher and PTY harness use `python3 -m vigor`.
- The compatibility `vig.py` has been removed.
- Documentation and installer manifests use the package runtime.

### Phase Gates

For every phase:

- `python3 -m py_compile` passes for all runtime and test files.
- The full `python3 test_vig.py` suite passes.
- `git diff --check` passes.
- No behavior change is combined with a structural move unless required to preserve an invariant.
- Update `AGENTS.md` architecture and file inventory immediately.

### Size Expectation

Module interfaces and explicit state boundaries are expected to add approximately 100–250 runtime lines during migration. This exceeds the normal 50-line notification threshold and is explicitly approved as architecture work, not feature expansion.
