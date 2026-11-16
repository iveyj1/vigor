### Visual Block Selection

**Status:** Phase 1 implemented. Phase 2 is deferred pending approval.

### Phase 1

- Normal Ctrl-V enters Visual Block mode and switches Markdown to literal source.
- Selection uses inclusive display columns across logical lines, not soft-wrapped screen rows.
- h/l step through display cells, including tab interiors; j/k preserve the desired column across logical lines regardless of wrapmove. Short-line cursors sit at EOL while retaining the desired column.
- Only existing cells are highlighted or deleted. Selection/deletion never pad buffer lines.
- y yanks a rectangle; d/x deletes it without joining lines. Readonly and delcopy retain their existing meaning.
- Partial-tab edits expand tabs cut by a boundary to preserve unselected cells. Tabs outside the edited boundaries remain literal.
- The single unnamed register stores newline-separated rows plus block width. Selected tabs expand to spaces; short rows are padded in the register.
- Normal p/P inserts a block after/before the source character under the cursor. It pads short target lines and extends EOF. Paste inside a target tab expands that tab.
- Delete and paste each take one undo snapshot; no-op deletion does not snapshot. Redo works.
- Esc/Ctrl-C/Ctrl-V exit; gv restores the last selection per buffer, including its display columns.
- Normal paste retains existing dot repeat. Replaying block selections/deletions is not part of Phase 1.

**Architecture**

- `Mode.VISUAL_BLOCK` plus a display-column endpoint; `vx` is the display-column anchor in this mode.
- `reg_block_width` distinguishes block text; ordinary register writes clear it.
- `ViewportLayout` accepts an exact display-column cursor override for partial tabs, keeping wrapped cursor mapping centralized.
- No new configuration options or runtime dependencies.

### Phase 2 (Deferred)

- c, I, and A: record insertion once and apply it across selected rows on leaving Insert mode.
- Define editing, newline, bracketed-paste, cancellation, undo grouping, and dot-repeat semantics for replicated insertion.
- Block selection/edit dot repeat.
- Other Visual transforms are outside the approved scope; do not silently apply characterwise operators to block selections.

### Tests

Phase 89 covers directional selection, counts, partial/full tabs, exact cursor placement within wrapped tabs, logical-line wrap behavior, highlight columns, yank/delete/paste, short and empty lines, EOF extension, undo/redo, gv and buffer switching, Markdown literal view, readonly/delcopy, register shape reset, and no-op edits.
