# Visual Block Mode Proposal

### Status

Proposal only. No implementation has been started, and the initial command scope remains undecided.

- `vig.py` only has `VISUAL` and `VISUAL_LINE`.
- `Terminal.read_key()` currently discards Ctrl-V as an unrecognized control character.
- `todo.md` defers the feature and points here for scope and design decisions.

### Full Implementation

**Core mode and rendering**

 - Add Mode.VISUAL_BLOCK.
 - Decode byte 0x16 as CTRL_V.
 - Enter block mode from Normal with Ctrl-V.
 - Include block mode in visual dispatch and cancellation.
 - Add _block_bounds() returning normalized top, left, bottom, right coordinates.
 - Update _render_visible() to highlight the same column range on every selected line, including wrapped and horizontally scrolled lines.

**Block operations**

 - d / x: remove the selected slice independently from each line, shifting remaining text left.
 - y: copy each selected row as a separate register row.
 - p / P: insert or replace rectangular text row-by-row.
 - r{char}: replace selected existing characters.
 - c / s: delete the rectangle and support block insertion across every selected line.

 The last item is the most involved because current Insert mode edits only one line. A block-change implementation needs to record inserted text and replicate it across selected rows,
 including handling Esc, Backspace, Delete, Enter, and bracketed paste.

**Register changes**

 The register currently distinguishes only characterwise versus linewise using reg_linewise. It should become three-way:

 - characterwise
 - linewise
 - blockwise

 Blockwise paste must retain a list of rows and the rectangle width. Clipboard output can remain newline-separated text.

**Short-line behavior**

 Operations need defined behavior when a selected or pasted rectangle extends beyond a line’s end. Recommended Vim-like behavior:

 - Highlight and delete only characters that exist.
 - Yank missing portions as spaces so the rectangle retains its width.
 - Paste pads destination lines with spaces up to the insertion column.
 - Block change pads short lines before inserting replicated text.

### Tests and Documentation

Add PTY tests covering:

 - Ctrl-V entry and Esc exit.
 - Rectangular highlight.
 - Movement in all directions and reversed anchors.
 - Delete, yank, paste, change, substitute, and replace.
 - Unequal line lengths.
 - Blockwise register paste above/below or before/after.
 - Undo/redo and dot repeat.
 - Wrap, nowrap horizontal scrolling, and line-number gutters.
 - Clipboard text from block yanks.
 - Ctrl-V remaining literal during bracketed paste.

 Update reference.md and AGENTS.md.

### Estimate

- Highlight plus `d`/`y`: roughly 80–120 runtime lines plus tests.
- Broader `d`/`y`/`p`/`c`/`s`/`r` scope: roughly 180–280 runtime lines plus 100–180 test lines.
 - Block change/insertion and blockwise paste account for most of the complexity.

 Before implementation, the main decisions are:

 1. Whether p replaces the selected block or only inserts the register.
 2. Whether c/s replicate inserted text on Esc, Vim-style.
 3. Whether short lines are space-padded.
 4. Whether to include obvious visual aliases/operators now: x, ~, g~, gU, gu, I, and A.

### Limited Alternative

**Proposed semantics**

 Without full block mode:

 - Normal: [n]<Space>x deletes up to n characters from the cursor on the current line.
 - Visual/Visual Line: [n]<Space>x deletes that column range from every selected line.
 - The visual selection’s horizontal width is ignored; deletion starts at the current cursor column.
 - Short lines delete only available characters; no padding.
 - The unnamed register and clipboard remain unchanged.
 - The operation gets one undo snapshot and exits Visual mode.

 Example, cursor at column 2 with three lines selected:

 ```text
   abcdef
   123456
   xyz
 ```

 2<Space>x produces:

 ```text
   abef
   1256
   xy
 ```

**Code required**

 Approximately 35–60 runtime lines:

 - Preserve the count while the Space leader awaits its second key. Currently the count is reset when Space is processed.
 - Add a compact helper that slices line[cx:cx+n] from a range of lines without calling _delete_range().
 - Add <Space>x to the Normal leader dispatch.
 - Add count and Space-leader handling to handle_visual().
 - Take one snapshot, set dirty only if something changed, clamp the cursor, and return to Normal.
 - Reset the additional pending state in _cancel_pending().

 No register redesign, blockwise paste, new mode, or rendering changes are required.

**Tests and documentation**

Roughly 30–50 test lines covering:

 - Default one-character deletion.
 - Counted deletion.
 - Multiple selected lines.
 - Short and empty lines.
 - Register remains unchanged.
 - Undo restores all affected lines.
 - Reversed visual selection.
 - Normal-mode behavior.

 Update reference.md, AGENTS.md, and the backlog entry.

 The only semantic point to confirm before implementation is whether the deletion column should be the current cursor column or the visual anchor column. Current cursor column is the more
 direct reading of “starting under the cursor.”

