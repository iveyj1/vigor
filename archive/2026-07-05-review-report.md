### Review Summary

**Current state**

- `ved.py`: 2,346 lines.
- `test_ved.py`: 165 test functions, 35 phase groups.
- Full test suite passes: `ALL TESTS PASSED`.
- The editor is functional and broadly conforms to the expanded spec in `AGENTS.md` / `reference.md`.

**Working tree note**

`git status` shows existing uncommitted changes:

- Deleted: `.vscode/settings.json`
- Modified: `AGENTS.md`, `notes`
- Added: `ved`

I did not change anything except creating this report file.

### Non-Conformance / Documentation Drift

**`PLAN.md` is materially outdated**

`PLAN.md` describes the original minimal editor, not the current product.

Major mismatches:

- Says target size is `~500-800 lines`; actual `ved.py` is 2,346 lines.
- Says “No registers, no undo”; current editor has:
  - unnamed register
  - yank/delete/paste
  - undo/redo
  - dot repeat
  - clipboard integration
- Says `:e[dit] <path>` replaces current buffer; current code adds a new buffer, matching `AGENTS.md`.
- Lists only early phases through Phase 7; test suite now covers phases through Phase 35.
- Says dependencies are only `sys`, `os`, `termios`, `tty`, `atexit`, `signal`, `shutil`; current code also uses `re`, `base64`, `select`, `enum`, and imports `subprocess` inside shell/clipboard paths.
- Mentions `README.md`, but there is no `README.md` in the repo.

Recommendation: treat `PLAN.md` as historical, or rewrite it as a current architecture/spec document.

**`AGENTS.md` mostly matches current implementation, but has small drift**

Issues found:

- Says `ved.py` is `~2060 lines`; actual is 2,346.
- Says test suite has 149 tests; actual number of `test_*` functions is 165.
- Says stdlib imports are exactly:
  - `sys`, `os`, `re`, `base64`, `termios`, `tty`, `atexit`, `signal`, `shutil`, `select`, `enum`
- But `ved.py` also imports `subprocess` locally for:
  - external clipboard commands
  - `:read !cmd`
  - `:! cmd`

This is still stdlib, so the architectural goal is intact, but the documented import allowlist is inaccurate.

**Clipboard default mismatch**

`AGENTS.md` and `reference.md` say default clipboard mode is `auto`.

Current code sets:

```python
self.opt_clipboard = "osc52"
```

This is a real spec/code mismatch.

Given commit history includes `Phase 35: add clipboard modes and default to auto`, this looks like either a regression or stale code.

**`:bd` abbreviation mismatch**

`reference.md` documents:

- `:k`
- `:bd`
- `:bdelete`

Current code supports:

- `:k`
- `:bdelete`

It does not support `:bd`.

`AGENTS.md` does not mention `:bd`, so this is specifically a `reference.md` mismatch.

**`:!` behavior documentation mismatch**

`AGENTS.md` says `:! <cmd>` runs shell command and shows output. Later it says bang commands “wait for Enter.”

Current implementation:

- runs command
- puts truncated output in `self.msg`
- immediately returns to NORMAL mode
- does not enter a “press Enter to continue” state

Tests pass against current behavior. Either docs should drop “waits for Enter,” or code needs a command-output pause mode.

**Search regex wording**

`reference.md` says “POSIX extended regex.”

Implementation uses Python `re`.

This is acceptable technically, but the wording should say “Python regular expression” unless POSIX compatibility is intentionally required.

### Code-Level Observations

**Architecture still matches the one-file design**

The code preserves the core intended structure:

- `Buffer`
- `BufferState`
- `Terminal`
- `Editor`
- visual section markers
- raw ANSI / no curses
- stdlib-only runtime
- full redraw per keypress
- PTY smoke tests

**Complexity is concentrated in a few large areas**

The largest maintenance risks are:

- `handle_normal`
  - many modal sub-states:
    - counts
    - pending operator
    - pending `g`
    - pending find-char
    - pending text object
    - dot repeat recording
    - leader key
- `_exec_command`
  - command parsing and command execution are intertwined
- rendering / wrapping / scroll logic
  - correct but now non-trivial
- undo/dirty tracking
  - snapshot depth model is simple but easy to break when adding new mutating operations

No urgent correctness issue appears from the tests, but these areas are where future bugs are most likely.

### Refactoring Need

**Need: yes, but conservative**

I would not split the project into modules yet, because “single source file” is still an explicit goal and still viable.

Recommended refactoring scope:

- Keep `ved.py` single-file.
- Add clearer internal sectioning.
- Extract command handlers from `_exec_command`, e.g.:
  - `_cmd_write`
  - `_cmd_quit`
  - `_cmd_edit`
  - `_cmd_bdelete`
  - `_cmd_bang`
- Introduce small dispatch maps for ex commands where it does not obscure behavior.
- Split `handle_normal` into helper blocks:
  - `_handle_count`
  - `_handle_pending_space`
  - `_handle_pending_g`
  - `_handle_pending_operator`
  - `_handle_normal_motion_or_action`
- Add a small “mutation contract” comment near snapshot/dirty handling:
  - every mutating normal/visual/ex operation must snapshot before mutation
  - every insert entry snapshots before entering insert
  - save updates `_undo_save_depth`

Avoid heavy abstractions. The current editor benefits from being greppable and direct.

### Are the Original Goals Still Viable?

**Still viable**

These goals remain intact:

- single executable Python file
- no pip dependencies
- no curses
- raw ANSI terminal control
- simple list-of-strings buffer
- PTY smoke tests
- vi-inspired, not vi-compatible
- small enough to understand in one file

**Changed in spirit**

The original “radical simplicity” goal has evolved.

Originally, ved was a tiny modal editor with:

- basic movement
- insert
- visual selection
- a few ex commands

Now it includes:

- multi-buffer editing
- undo/redo
- operators and text objects
- search/substitute
- wrapping and line numbers
- clipboard
- shell commands
- dot repeat
- comment toggling
- scrolloff
- path resolution

That is no longer “minimal viable editor”; it is now a compact vi-like editor.

**Main risk**

The main risk is not performance or architecture. It is feature interaction complexity.

Future features will increasingly collide with:

- dot repeat
- undo boundaries
- dirty tracking
- visual/operator ranges
- multi-buffer state
- rendering with wrap + gutter + scrolloff

So the original single-file goal is still viable, but the “keep every handler flat and simple” approach is reaching its limit.

### Recommended Next Actions

**Documentation cleanup**

- Update `PLAN.md` or mark it historical.
- Update `AGENTS.md` line counts, test counts, and import list.
- Fix clipboard default documentation or code.
- Decide whether `:bd` is supported; update code or `reference.md`.
- Clarify `:!` behavior.

**Small code fixes**

- Change default clipboard mode to `auto` if docs are authoritative.
- Add `bd` / `bd!` aliases if `reference.md` is authoritative.
- Consider whether `:!` should pause for Enter.

**Refactor later, not immediately**

Since all tests pass, I would first correct spec drift and add tests for the mismatches above. Then do conservative internal refactoring with full-suite runs after each step.
