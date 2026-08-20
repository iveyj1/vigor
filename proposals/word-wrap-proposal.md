# Word-Boundary Line Wrapping Proposal

### Status

Proposal only. No implementation has been started.

### Summary

Add an optional word-boundary layout for wrapped lines. When enabled with `wrap`, vigor should break display rows at whitespace where possible instead of always splitting at the terminal column boundary.

The option changes only screen layout. It does not insert or remove characters in the buffer.

### Proposed Options

Recommended names:

- `:set wordwrap` — prefer word boundaries when wrapping.
- `:set nowordwrap` — use the existing fixed-column wrapping.

`nowordwrap` should remain the default. `wordwrap` should be dormant while `nowrap` is active, allowing users to configure it before enabling wrapping.

Vim-style `linebreak` / `nolinebreak` would be an alternative naming choice.

### Proposed Behavior

With `wrap wordwrap`:

- Break a display row at whitespace when a suitable boundary exists.
- Hard-wrap a word that is longer than the available content width.
- Recalculate boundaries after edits, terminal resizing, or gutter-width changes.
- Show the line number only on the first display row of a logical line.
- Use blank gutter padding on continuation rows.
- Preserve syntax and visual highlighting across display rows.
- Make `wrapmove` navigate the resulting word-wrapped display rows.
- Leave buffer contents and saved files unchanged.

With ten content columns, this line:

```text
alpha beta gamma
```

should display as:

```text
alpha beta
gamma
```

### Boundary Whitespace

The recommended behavior is to consume separator whitespace visually at a soft-wrap boundary. The whitespace remains in the buffer but does not appear at the beginning of the continuation row.

This produces conventional word wrapping, but requires explicit cursor mapping for hidden boundary whitespace. A cursor positioned on consumed whitespace should map to the end of the preceding display row.

A simpler alternative would preserve every whitespace character visually. Depending on available width, that can leave trailing whitespace on one row or leading whitespace on the next and may produce less natural wrapping.

This behavior must be decided before implementation.

### Current Architecture

Current wrapping expands tabs to display columns, then divides each logical line into fixed-width display segments. The fixed-segment assumption appears in related paths:

- `_line_screen_rows()` calculates the number of display rows.
- `_render_line()` renders chunks beginning at fixed display-column multiples.
- Cursor helpers calculate wrapped row and column with division and modulo.
- `_motion_display_row()` implements `wrapmove` using fixed row offsets.
- `_position_at_view_row()` maps viewport rows back to source positions.

Changing only `_render_line()` would leave cursor placement, viewport scrolling, and movement inconsistent. These paths need one shared variable-segment layout.

### Proposed Design

**Central segment calculation**

Add a helper such as:

```python
_wrap_segments(line, content_cols)
```

It should return the source-coordinate range represented by each display row, including enough information to account for visually consumed boundary whitespace.

The helper should:

- Produce one segment for an empty line.
- Choose the last suitable whitespace boundary within the available width.
- Avoid producing empty segments for leading whitespace.
- Hard-wrap when no suitable boundary exists.
- Guarantee forward progress for every input and terminal width.

No persistent cache is initially necessary. Layout is already recomputed on each redraw, and scanning visible lines is acceptable for the editor's intended scale.

**Coordinate mapping**

Add shared mapping helpers for:

- Buffer column to display-row index and screen column.
- Display-row index and desired screen column to buffer column.

These mappings must define behavior for:

- The first and last character of a segment.
- Boundary whitespace.
- One-past-end-of-line cursor positions.
- Empty lines.
- Words longer than the content width.

**Rendering**

Update `_render_line()` to iterate calculated segments rather than fixed-width chunks. Each rendered segment should retain its original source offset so `_render_visible()` can continue applying syntax and visual spans using buffer columns.

**Cursor placement**

Replace the current division/modulo cursor calculation in `render()` with the shared buffer-to-display mapping.

This avoids placing the cursor on a different row from the rendered character when segment widths vary.

**Displayed-row movement**

Update `_motion_display_row()` to use calculated segments when `wrap` and `wrapmove` are enabled.

Movement should preserve a sticky display column:

- Moving within one logical line selects the corresponding source column in the adjacent segment.
- Moving beyond the first or last segment enters the adjacent logical line.
- Moving to a shorter segment clamps to its displayed end.

Normal `j` and `k` behavior remains logical-line movement when `nowrapmove` is active.

**Scrolling**

Update `_line_screen_rows()` to return the number of calculated segments. Existing wrapped scrolling can then count word-wrapped rows consistently.

### Wrapped Viewport State

Vigor now stores both a logical top line (`scroll`) and a wrapped-row offset (`wrap_skip`) per buffer. Oversized logical lines can therefore begin partway through the viewport and remain scrollable.

A word-wrap implementation must preserve that model while replacing fixed row arithmetic with shared segment boundaries. No separate oversized-line scrolling fix is currently required.

### Option and Configuration Work

Implementation requires:

- Add `self.opt_wordwrap`, defaulting to `False`.
- Add `wordwrap` and `nowordwrap` handling to `_exec_set()`.
- Allow both options in startup configuration through the existing `:set` path.
- Add the options to command documentation and `AGENTS.md`.
- Document interaction with `wrap`, `nowrap`, `wrapmove`, and `nowrapmove`.

### Edge Cases

The implementation should explicitly handle:

- Empty lines.
- Leading indentation.
- Multiple consecutive spaces.
- Tabs already present in loaded or bracketed-pasted text.
- Exact-width words and lines.
- Words longer than the content width.
- Trailing whitespace.
- One-column content areas.
- Five-column and expanded line-number gutters.
- Terminal resize while the cursor is on a continuation row.
- Visual selections crossing soft-wrap boundaries.
- Syntax spans crossing soft-wrap boundaries.
- Insertions and deletions that move an existing boundary.

Vigor expands tabs to four-column stops and maps them back to source indices. It still treats other Python characters as one display column, so wide and combining Unicode characters are not fully supported. Word wrapping can retain that limitation rather than expanding the scope.

### Testing Plan

Add PTY tests for:

- `wordwrap` and `nowordwrap` option messages.
- Dormant `wordwrap` behavior under `nowrap`.
- Basic wrapping at whitespace.
- Exact-width boundaries.
- Multiple spaces and indentation.
- Long-word hard wrapping.
- Empty and trailing-whitespace lines.
- Number gutter width affecting boundaries.
- Expanded number gutter for files over 99,999 lines.
- Cursor placement around a boundary.
- One-past-EOL cursor placement.
- `wrapmove` forward and backward within a logical line.
- `wrapmove` crossing logical-line boundaries.
- Sticky display-column behavior across unequal segments.
- Visual and syntax highlighting across wrapped segments.
- Boundary recalculation after editing.
- Boundary recalculation after terminal resize.

Include a regression test where one logical line occupies more display rows than the terminal content area.

### Estimated Size

A correct implementation integrated with rendering, cursor placement, scrolling, and `wrapmove` is estimated at:

- 80–130 runtime lines.
- 60–100 test lines.
- Small documentation and option-wiring changes.

### Decisions Required

Before implementation, decide:

1. Whether the option is named `wordwrap` / `nowordwrap` or `linebreak` / `nolinebreak`.
2. Whether boundary whitespace is visually consumed or displayed on one of the adjacent rows.
3. How a cursor on visually consumed whitespace is positioned.
