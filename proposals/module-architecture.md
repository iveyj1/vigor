# Module Architecture Plan

### Status

Accepted for implementation on `architecture/module-split`.

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
vig.py                     temporary compatibility entry point
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

The compatibility `vig.py` remains only while tests and external invocation move to `python3 -m vigor`. It is removed before the migration is considered complete.

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

**Phase A — package scaffold and low-coupling state**

- Add `vigor` package and module entry point.
- Extract `Buffer`, `BufferState`, and `Terminal` without behavioral changes.
- Move the existing editor to `vigor/app.py`.
- Keep a temporary `vig.py` entry point.
- Update installation and build stamping.

**Phase B — highlighting and presentation**

- Extract syntax, search-span, and Markdown projection logic.
- Keep spans in source coordinates.
- Preserve line-local highlighting behavior.

**Phase C — shared layout**

- Introduce visible-row records and bidirectional coordinate mapping.
- Move viewport, wrap, gutter, rendering, and cursor placement together.
- Preserve exact-width EOL and oversized-line behavior.

**Phase D — editing core**

- Extract motions, range normalization, register operations, undo, and mutations.
- Enforce the buffer invariant that each `Buffer.lines` item contains no newline.
- Remove no-op undo snapshots.

**Phase E — modes and commands**

- Split modal dispatch from editing primitives.
- Extract ex parsing, completion, quickfix, clipboard, and subprocess execution.
- Keep orchestration in `app.py`.

**Phase F — remove compatibility entry point**

- Make the launcher and PTY harness use `python3 -m vigor`.
- Remove `vig.py`.
- Update documentation and installer manifests.

### Phase Gates

For every phase:

- `python3 -m py_compile` passes for all runtime and test files.
- The full `python3 test_vig.py` suite passes.
- `git diff --check` passes.
- No behavior change is combined with a structural move unless required to preserve an invariant.
- Update `AGENTS.md` architecture and file inventory immediately.

### Size Expectation

Module interfaces and explicit state boundaries are expected to add approximately 100–250 runtime lines during migration. This exceeds the normal 50-line notification threshold and is explicitly approved as architecture work, not feature expansion.
