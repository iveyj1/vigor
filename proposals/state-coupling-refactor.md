# State-Coupling Refactor Plan

### Status

Proposed. Not started.

### Motivation

The module layout from `proposals/module-architecture.md` has held up: features land at the
right seams and `ViewportLayout` unification made wordwrap, fence hiding, and mouse mapping
cheap. The bugs that proved hard to fix were not module-boundary problems. They were
implicit-state problems:

- The `Buffer.dirty` setter callback performs filesystem policy (recovery-file deletion),
  so any code path assigning `dirty` can silently delete user data. This caused the
  startup-config recovery-file deletion bug.
- `Editor.__init__` is a long order-sensitive sequence (buffers → config → detection →
  message → splash) where later steps clobber earlier ones. This caused the missing
  startup recovery warning.
- `_save_buf_state`/`_load_buf_state` mirror ~12 fields between `Editor` working
  attributes and `BufferState`. Every per-buffer field must be maintained in three
  places, inviting stale-state bugs.
- Each new `:set` option touches `commands.py`, `app.py`, `example-config`, `vighelp`,
  `reference.md`, `AGENTS.md`, and two audit tests.

This plan makes those couplings explicit without adding frameworks, observers, or new
modules. Behavior is unchanged; the full PTY suite is the gate for every phase.

### Non-Goals

- No module splits or merges; eight runtime modules is right.
- No event/observer system; the one observer we have caused the worst bug.
- No rework of the if/elif key dispatch in `modes.py`.
- No behavior changes visible to users or tests, except where a test encodes the
  refactored internals directly.

### Phase R1 — Option Table

**Problem.** `_exec_set` is a ~180-line if/elif chain; config parsing and the
example-config/vighelp audit tests duplicate the option list.

**Change.** Add one declarative table in `commands.py`:

```python
OPTIONS = {
    # name: (kind, attr, default, validate/values, on_change)
    "wrap":        ("bool", "opt_wrap", False, None, "_ensure_scroll"),
    "wrapcol":     ("int",  "opt_wrapcol", 0, ">=0", "_ensure_scroll"),
    "clipboard":   ("enum", "opt_clipboard", "auto", ("osc52", "auto", "off"), None),
    "makeprg":     ("str",  "opt_makeprg", "make", None, None),
    ...
}
```

`_exec_set` becomes a small interpreter: bool options accept `name`/`noname`, int options
validate ranges and report `X must be ...`, enum options validate membership, str options
copy the tail. Special cases stay explicit in code, not in the table: bare `wrapcol`
(cursor column) and `mouse` (terminal side effect) get small pre/post hooks.

Editor `__init__` derives every `opt_*` default from the table, removing the parallel
assignment block. The audit tests derive expected `example-config` lines and `vighelp`
option names from the table instead of hardcoded lists, so adding an option means: one
table row, one `example-config` line, one `vighelp` line, docs.

**Risk.** Low. Message strings must stay byte-identical where tests assert them
(`wrapcol must be >= 0`, `clipboard must be osc52, auto, or off`, etc.).

**Estimate.** Net −60 to −100 runtime lines.

**Gate.** Full suite, plus phases 37, 59, 72, 73 read closely for message wording.

### Phase R2 — Narrow the Dirty Callback

**Problem.** `Buffer.dirty` assignment triggers `_dirty_changed`, which both reschedules
timers and deletes recovery files. Deletion-on-clean is policy, and it fires from every
code path that assigns `dirty`, including paths that merely refresh state (quickfix
reload, `:e!`, startup).

**Change.**

- The dirty callback only manages deadlines: set `autosave_deadline`/`recovery_deadline`
  on dirty named buffers, clear both otherwise. It never touches the filesystem.
- Recovery-file deletion becomes an explicit `_delete_recovery(bs)` call at exactly the
  places that mean "this buffer's contents are now safely on disk":
  - successful explicit write (`_write_buffer_to_path`)
  - successful autosave (`_autosave_state`)
  - buffer reload from disk (`:e!`), which discards the edits the backup protected
- Document the rule in AGENTS.md: timed protectors are scheduled by the callback;
  recovery files are deleted only by named save paths.

**Risk.** Medium. Enumerate every current deletion trigger first (`rg "dirty = False"`,
`dirty=False`, `save(`) and decide keep/drop per site. The undo-driven
`_update_dirty()` transition to clean (user undoes back to the save point) currently
deletes the backup; keep that one — it is a real "matches disk" state — but route it
through the explicit helper with a comment.

**Estimate.** Net ±0 to +10 lines.

**Gate.** Full suite, plus a new test: toggling options in startup config with an
existing recovery file present must not delete it (regression for the original bug).

### Phase R3 — Property-Based Buffer State

**Problem.** `_save_buf_state`/`_load_buf_state` copy cursor, scroll, Markdown
projection, filetype, autodetect, and four undo fields between `Editor` and
`BufferState`. Adding a per-buffer field requires touching `__slots__`, save, load, and
sometimes `_add_buffer`. Stale mirrors are the most likely source of the next hard bug.

**Change.** Make the working attributes delegating properties:

```python
@property
def buf(self):
    return self.buffers[self.buf_idx].buf

@property
def cx(self):
    return self.buffers[self.buf_idx].cx

@cx.setter
def cx(self, value):
    self.buffers[self.buf_idx].cx = value
```

- Cover: `buf`, `cx`, `cy`, `scroll`, `_wrap_skip`, `md_view`, `md_lines`, `md_maps`,
  `md_languages`, `filetype_override`, `buffer_autodetect`, `_undo_stack`,
  `_redo_stack`, `_undo_save_depth`, `_undo_branched`.
- Delete `_save_buf_state` and `_load_buf_state`; `_switch_buffer` reduces to index
  assignment plus clamp/scroll/mode reset. `_add_buffer` and `_close_buffer` simplify.
- Truly global state (mode, register, search, options, count, pending_*) stays as plain
  attributes; the split between per-buffer and global becomes visible in the property
  list.

**Sequencing within the phase.** Mechanical and single-commit-able, but do it in two
steps: (1) introduce properties while keeping save/load as no-ops, run suite; (2) delete
save/load and fix the handful of callers that relied on load-time side effects
(`_load_buf_state` currently also sets `buf_idx`; `dot repeat` and quickfix paths call
switch helpers).

**Risk.** Medium-high in surface area, low in depth: failures show up immediately and
loudly in the PTY suite. Watch `_undo_stack` identity: code appends to the working
list in place, which the property preserves, but any site that *rebinds*
`self._undo_stack = []` must rebind through the setter.

**Estimate.** Net −30 to −50 lines.

**Gate.** Full suite twice (once per step), with special attention to phases 15
(undo/redo), 28 (multi-buffer), 38 (quickfix), 72 (autosave deadlines across buffers).

### Phase R4 — Startup Sequence (deferred)

**Problem.** `Editor.__init__` interleaves buffer creation, config, detection, messages,
and splash policy; ordering bugs are found by symptom.

**Decision.** Do not restructure now. R1 removes the option-default block and R2 removes
the destructive side effect, which together eliminate the known hazards. If startup
ordering bites again, extract a small explicit sequence then:

```python
self._create_buffers(paths)
self._load_config()
self._init_detection()
self.msg = self._startup_message()   # one place decides priority
self._init_terminal_and_splash()
```

### Order and Dependencies

1. **R1 option table** — independent, biggest ongoing payoff, do first.
2. **R2 dirty callback** — independent of R1; fixes the demonstrated bug class.
3. **R3 buffer-state properties** — do last, in a quiet stretch; touches the most code.
4. **R4** — deferred until motivated by a concrete failure.

Each phase is one commit, full suite green, docs (AGENTS.md architecture notes) updated
in the same phase. No phase changes user-visible behavior.
