# Mouse Scrolling and Selection Proposal

### Status

Proposal only. No implementation has been started.

### Summary

Add optional terminal mouse reporting for editor-aware wheel scrolling and, in a later phase, click-drag Visual selection.

Mouse reporting changes how the terminal handles ordinary text selection, so it must be configurable and remain disabled by default.

### Two Kinds of Selection

**Terminal-native selection**

With mouse reporting disabled, the terminal normally owns click-drag selection. This already works without vigor support, but copies rendered screen text rather than buffer text. It may include line-number gutters, omit horizontally hidden text, or preserve wrapped display rows.

**Editor-aware selection**

With mouse reporting enabled, vigor can translate a drag into buffer coordinates and Visual mode. The resulting selection works with existing `y`, `d`, `c`, and other Visual operations.

The two approaches conflict because terminals normally stop handling ordinary selection while an application requests mouse events. Most terminals reserve Shift-drag as an override for native terminal selection, but this behavior is terminal-specific.

### Proposed Configuration

Recommended option:

```vim
:set mouse=off
:set mouse=scroll
:set mouse=visual
```

Semantics:

- `off` — do not request mouse events; preserve current terminal-native behavior.
- `scroll` — wheel events scroll vigor; click events are ignored.
- `visual` — wheel scrolling plus left-button click and drag into Visual mode.

`off` should remain the default.

A smaller `mouse` / `nomouse` boolean would save a few lines but would not let users request wheel handling without editor-aware selection.

### Terminal Protocol

Use SGR extended mouse reporting while vigor owns the terminal:

```text
ESC[?1000h    button and wheel reporting
ESC[?1002h    button-motion reporting when visual selection is enabled
ESC[?1006h    SGR coordinates and button encoding
```

Disable every enabled mode during:

- Normal process exit
- `atexit` restoration
- Ctrl-Z suspension
- Any temporary terminal handoff such as `:rgf`

Re-enable the configured modes when vigor returns to raw mode.

Typical SGR wheel events are:

```text
ESC[<64;x;yM    wheel up
ESC[<65;x;yM    wheel down
```

Button press, motion, and release use the same variable-length CSI form with button and modifier bits.

### Key Reader Changes

`Terminal.read_key()` currently decodes keyboard escape sequences. It would need to recognize a complete SGR mouse sequence and return a structured event, for example:

```python
("MOUSE", button, action, x, y, modifiers)
```

Coordinates should be converted from terminal one-based values to zero-based screen coordinates at the decoding boundary.

Malformed or incomplete mouse sequences should be ignored rather than dispatched as keyboard input.

Bracketed paste tuples already demonstrate that the main loop can accept structured input without changing every mode handler.

### Wheel Scrolling

Mouse events should be dispatched in the main loop before Normal, Insert, Command, Search, or Visual mode handlers.

Recommended behavior:

- One wheel event scrolls three display rows.
- Wheel up calls `_scroll_view(-1, 3)`.
- Wheel down calls `_scroll_view(1, 3)`.
- Scrolling works in Normal, Insert, and Visual modes.
- Command and Search prompts remain active while the content viewport scrolls.
- The cursor moves only when needed to keep it within the visible viewport and configured `scrolloff` margin.

The existing `_scroll_view()`, `_move_view_top()`, and wrapped viewport state already provide the required logical-line and display-row behavior. Mouse scrolling should reuse them rather than introduce a second viewport path.

Estimated runtime increase for `mouse=off|scroll`: **25–45 lines**.

### Screen-to-Buffer Mapping

Editor-aware clicking and dragging require one shared inverse mapping from screen coordinates to buffer coordinates.

The mapping must account for:

- Content rows versus status and message rows
- Absolute or relative line-number gutter width
- `scroll` and `_wrap_skip`
- Wrapped display rows
- Horizontal scrolling in nowrap mode
- Tab expansion
- Empty logical lines
- One-past-end-of-line cursor positions
- Terminal width changes

**Nowrap mode**

The basic mapping is:

```text
logical line = scroll + screen row
display column = horizontal offset + screen column - gutter width
buffer column = _display_index(line, display column)
```

Coordinates left of the content area should clamp to column zero. Coordinates beyond rendered text should clamp to one-past-end-of-line.

**Wrap mode**

Starting from `scroll` and `_wrap_skip`, walk visible display rows until the clicked wrapped segment is reached. Its buffer display column is:

```text
wrapped segment offset + screen column - gutter width
```

Use `_display_index()` to translate that display column back to a Python string index.

This helper must use the same row calculations as rendering and `_position_at_view_row()` so clicking cannot disagree with the displayed cursor layout.

### Click and Drag Behavior

Recommended initial `mouse=visual` behavior:

**Left press in content**

- Translate screen coordinates to a buffer position.
- Move the cursor there.
- Set `visual_anchor` to that position.
- Enter characterwise Visual mode.

**Left-button motion**

- Translate the latest coordinates.
- Update the cursor and visible selection.

**Left release**

- Leave the selection active in Visual mode.
- Do not yank automatically.

**Clicks outside content**

- Ignore status-bar and message-bar clicks.
- Clamp content-column clicks to valid buffer positions.

Leaving the selection active follows vigor's keyboard Visual model. Automatically copying on release would resemble terminal selection but would be a separate behavior and should not be included initially.

Estimated runtime increase:

- Click-to-position: **35–60 lines**
- Drag-to-Visual selection: another **25–50 lines**
- Combined robust scrolling and selection: approximately **90–140 lines**

The combined feature exceeds the project's approximately 50-runtime-line notification threshold.

### Deferred Mouse Features

Do not include these in the first implementation:

- Double-click word selection
- Triple-click logical-line selection
- Right-click menus or paste
- Middle-click paste
- Automatic yank on release
- Visual Line selection by mouse gesture
- Horizontal wheel events
- Configurable wheel speed
- Edge-drag autoscrolling
- Mouse interaction with completion menus
- Status-bar or buffer-tab actions

### Edge-Drag Autoscrolling

Terminals generally clamp pointer coordinates to the terminal. Continuing a selection beyond the first or last content row would therefore require a timer in the editor loop:

- Remember that a button remains pressed at an edge.
- Wake periodically without new terminal input.
- Scroll one or more display rows.
- Recalculate the Visual endpoint after each scroll.
- Stop on release or when the file boundary is reached.

This likely adds another **20–35 runtime lines** and should be considered separately.

### Unicode and Tabs

Tabs can use the existing `_display_index()` and display-column helpers.

Vigor currently treats Python characters as display-width units except for its explicit tab expansion. Mouse mapping should initially retain that same limitation rather than introduce a separate width model. Full-width and combining Unicode correctness belongs to a broader rendering change.

### Testing

PTY tests can inject SGR mouse sequences directly.

**Scrolling tests**

- Wheel down and up in nowrap mode
- Three-display-row movement
- Wrapped logical-line scrolling
- Cursor visibility and `scrolloff`
- Wheel events while Insert or Visual mode remains active
- Mouse mode enable and disable escape sequences
- Restoration across Ctrl-Z or temporary terminal suspension

**Selection tests**

- Click positioning without a gutter
- Absolute and relative line-number gutters
- Horizontal scrolling
- Wrapped display rows and `_wrap_skip`
- Tab-expanded text
- Forward and backward drag selection
- Drag followed by `y`, `d`, and `c`
- Empty logical lines and one-past-EOL positions
- Ignoring status and message rows
- Terminal resize after mouse support is enabled

Terminal-native Shift-drag behavior should be documented rather than asserted in PTY tests because it belongs to the terminal emulator.

### Recommended Delivery

**Phase 1 — wheel scrolling**

- Add `mouse=off|scroll` parsing.
- Add SGR mouse enable/restore handling.
- Decode wheel events.
- Dispatch through `_scroll_view()` with a three-display-row step.
- Keep `off` as default.

**Phase 2 — Visual selection**

- Add `mouse=visual`.
- Add one shared screen-to-buffer coordinate helper.
- Support left press, drag motion, and release.
- Leave the result active in Visual mode.
- Exclude edge autoscroll and multi-click gestures.

This split lands useful wheel support with a compact first change and isolates the substantially more complex coordinate mapping and selection behavior.

### Decisions Required Before Implementation

- Confirm `mouse=off|scroll|visual` names and default `off`.
- Confirm three display rows per wheel event.
- Confirm wheel scrolling remains active in Insert, Command, Search, and Visual modes.
- Confirm mouse release leaves Visual mode active without yanking.
- Confirm Shift-drag is the documented terminal-native selection escape hatch.
- Confirm status/message clicks are ignored.
