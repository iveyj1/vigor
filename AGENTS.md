# ved — Agent Guidance

> Use `###` headings, **bold** for subheadings within sections, minimal dividers.
> Keep paragraphs short. Prefer lists over prose where possible.
> No tables unless genuinely tabular data. No horizontal rules between sections.


## Project Overview

ved is a compact, single-file, vi-style terminal text editor written in Python. It uses raw ANSI escape codes for terminal interaction — no curses library and no third-party packages.

The project goal is a practical, small editor that remains easy to inspect, run, and modify as one file. It is no longer a tiny minimal prototype; it intentionally includes common vi-style editing features while avoiding plugin systems, syntax highlighting, macros, multiple source modules, and external runtime dependencies.

**Files**

- `ved.py` — the entire editor (~2400 lines)
- `test_ved.py` — PTY-based smoke tests (plain asserts, no framework, 174 test functions)
- `archive/PLAN.md` — retired original development plan, kept for history only
- `AGENTS.md` — this document
- `reference.md` — command reference

## Management
In this chat, I'll provide requirements for numbered development phases.  When each phase is complete and functional, update AGENTS, commit the code, and move to the next phase.  Review the phases for guidance when they are provided and ask for any needed clarifications.  If a feature is asked for in the chat, add it to the requirements.

## General Guidance

**Keep it compact.** Every feature and every line of code must justify its existence. If something can be left out without losing specified vi-style editing capability, leave it out.

**One file.** The editor lives entirely in `ved.py`. Classes and functions are organized by visual section markers (`# ── Section ──`) rather than by module. This keeps the call graph obvious, searchable, and greppable.

**Stdlib only.** Runtime code uses Python stdlib modules only: currently `sys`, `os`, `re`, `base64`, `termios`, `tty`, `atexit`, `signal`, `shutil`, `select`, `enum`, and local `subprocess` imports for shell/clipboard commands. No pip packages. No curses. Tests add PTY/tempfile/terminal-control helpers.

**ANSI, not curses.** All terminal control uses escape sequences written to stdout. This gives us complete control over what bytes hit the terminal and keeps the rendering logic transparent.


## Requirements

**Modes**

- NORMAL — navigation, mode switching, count prefixes
- INSERT — text entry, Esc returns to NORMAL
- COMMAND — `:` prefix, Enter executes, Esc cancels
- VISUAL / VISUAL LINE — selection with reverse video highlight
- SEARCH — `/` or `?` prompt for pattern input, Enter executes

**Normal mode commands** — `h j k l` (movement), `w W b B e E` (word motions), `gg` / `G` (go to first/last line, or line N with count), `0` (column 0), `^` (first non-blank), `$` (end of line), `Home` / `End` (start/end of line), `Ctrl-D` / `Ctrl-U` (half-page down/up), `f t F T` (find char on line), `;` `,` (repeat/reverse find), `%` (match bracket), `i I a A` (enter insert), `o` / `O` (open line below/above), `v V` (enter visual), `:` (enter command), `/` `?` (search forward/backward), `n` `N` (repeat search same/opposite direction), `u` (undo), `Ctrl-R` (redo), `.` (dot repeat last change), `x` (delete char under cursor), `X` / Backspace (delete char before cursor), `r{char}` (replace char under cursor; count replaces N chars), `s` (substitute char and enter Insert; count deletes N chars before Insert), `J` (join with next line), `<space>` (leader key for shortcuts: `<space>k` deletes buffer). All motions accept a count prefix (`3j`, `5w`, `3G`, etc.). Operators `d y c` enter operator-pending mode and combine with a motion (`dw`, `cw`, `yj`). Operators also combine with text objects (`diw`, `ci(`, `da"`, etc.). Doubled operators (`dd`, `yy`, `cc`) act linewise. `>>` / `<<` indent/dedent lines by 4 spaces. `gcc` toggles line comment. Shortcuts `D Y C` operate from cursor to end-of-line (D/C) or yank the whole line (Y). `p` / `P` paste from the unnamed register after/before the cursor.

**Command mode** — `:new`, `:e[dit] <path>` (adds a new buffer), `:w[rite] [path]`, `:q[uit]` (closes buffer if >1, else quits; refuses if dirty), `:q!` (force), `:wq` (write and close buffer/quit), `:qa` / `:qall` / `:qa!` / `:qall!` (quit all buffers), `:n` / `:next` / `:bn` (next buffer), `:p` / `:prev` / `:bp` (prev buffer), `:ls` (list buffers), `:k` / `:bdelete` (delete buffer, blocks if dirty), `:k!` / `:bdelete!` (force delete buffer), `:[range]s/pat/repl/[g]` (substitute), `:set <option>` (set wrap/nowrap/number/nonumber/relativenumber/norelativenumber/autoindent/noautoindent/comment=X/scrolloff=N/clipboard=osc52|auto|off), `:read <file>` (insert file below cursor), `:read !<cmd>` (insert command output below cursor), `:! <cmd>` / `:!<cmd>` (run shell command and show one-line truncated output in the message bar). Path arguments for `:e`/`:w` expand `~`; relative paths resolve from the current buffer's directory.

**Insert mode** — printable characters insert at cursor. Tab inserts 4 spaces. Enter splits the line (with autoindent, copies leading whitespace). Backspace deletes backward or joins lines. Delete removes the character under cursor. Arrow keys and Home/End move the cursor via `_exec_motion`, same as in Normal mode. Esc returns to NORMAL without moving the cursor.

**Full terminal** — ved uses the entire terminal window. Content rows = terminal height minus 2 (status bar + command/message bar). Long lines are truncated by default and wrapped when `:set wrap` is enabled. In nowrap mode, the current line horizontally scrolls as needed to keep the cursor visible.


## Divergences from vi

ved is vi-inspired, not vi-compatible. These differences are intentional:

**Esc from Insert keeps cursor in place.** vi moves left one column on Esc. ved does not. The cursor stays exactly where it was when Esc was pressed. This eliminates a common source of confusion and is the single most important divergence.

**Cursor past end-of-line is allowed in all modes.** vi clamps the cursor to the last character in Normal mode. ved allows the cursor on the position after the last character in every mode. This simplifies the clamping logic and makes cursor behavior consistent regardless of mode.

**Single unnamed register, no macros.** ved has one unnamed register that holds the last deleted or yanked text. Clipboard copy mode is configurable via `:set clipboard=osc52|auto|off` (current default `osc52`). There are no named registers and no macros.

**Minimal ex commands.** vi has dozens of ex commands. ved supports only: new, edit, write, quit, wq, qa, next, prev, ls, k/bdelete, set, substitute, read, and bang. Abbreviations (`:e`, `:w`, `:q`, `:r`, `:n`, `:p`, `:k`) work. That's it.


## Architecture

**Buffer** — a `list[str]` where each element is one line of text (no trailing newline stored). A `path` and `dirty` flag track file association and modification state. Saving writes each line followed by `\n`.

**BufferState** — bundles a `Buffer` with per-buffer state: cursor position (`cx`, `cy`), scroll offset, and undo/redo history (`_undo_stack`, `_redo_stack`, `_undo_save_depth`, `_undo_branched`). Uses `__slots__` for efficiency. Created once per opened file.

**Editor** — top-level state container. Holds a list of `BufferState` objects (`self.buffers`) and a current index (`self.buf_idx`). Working attributes (`self.buf`, `self.cx`, `self.cy`, `self.scroll`, undo stacks) point to the current buffer's state. `_save_buf_state()` syncs working attrs back to the current `BufferState`; `_load_buf_state(idx)` loads from a `BufferState` into working attrs; `_switch_buffer(idx)` does save + load + clamp + scroll + reset mode. Also holds current mode, command-line input, status message, visual anchor, terminal dimensions, count prefix accumulator, and run flag. One instance, created in `main()`. The unnamed register is shared across all buffers.

**Terminal** — manages raw mode via `termios`, reads keys one at a time with escape sequence decoding, and restores terminal state on exit via `atexit`.

**Rendering** — one full redraw per keystroke. The entire frame is built as a list of strings, joined, and written in a single `sys.stdout.write()` call. This eliminates flicker without requiring double-buffering. The frame consists of: content rows (with optional line number gutter, visual selection highlighting, and line wrapping), a reverse-video status bar, and a command/message bar. Rendering is split into `_render_line` (handles wrap/truncate for a buffer line, prepends gutter) and `_render_visible` (applies selection highlighting to a visible segment).

**Line numbers** — `_gutter_width()` returns the gutter width (0 when disabled, otherwise `max(3, digits_in_total_lines) + 1`). `_gutter_str(buf_line, gutter_width)` formats the number: absolute when `opt_number` only, relative distance from cursor when `opt_relnum` only, or hybrid (absolute on cursor line, relative elsewhere) when both are set. Content columns are reduced by the gutter width. In wrap mode, only the first wrapped row of a line shows the number; continuation rows get blank padding.

**Line wrap / horizontal scroll** — when `opt_wrap` is true, lines longer than content columns (total cols minus gutter) are split into chunks at the column boundary. `_line_screen_rows(line_idx)` computes how many screen rows a buffer line occupies. The render loop tracks `screen_rows_used` and `cursor_screen_y`/`cursor_screen_x` so cursor positioning works correctly on wrapped lines. `_ensure_scroll` sums wrapped screen rows from scroll to cursor to keep the cursor visible. When wrap is off, only the current line receives a horizontal column offset so the cursor remains visible at the right edge.

**Mode handlers** — `handle_normal`, `handle_insert`, `handle_command`, `handle_visual`. Each is a flat `if/elif` chain. The main loop dispatches based on `self.mode`.

**Motion dispatch** — `_exec_motion(key, n)` is the single source of truth for all motion execution (`h l j k w W b B e E` and arrow keys). It is called by `handle_normal`, `handle_visual`, and `_apply_motion` (which wraps it with cursor save/restore for operator-pending). The `_MOTION_KEYS` frozenset provides O(1) membership checks.

**Operator-pending** — typing `d`, `y`, or `c` in Normal mode sets `pending_op` and saves the current count in `pending_count`. The next key is treated as a motion. The operator then acts on the range from the original cursor to where the motion would land. Doubled operators (e.g., `dd`) are linewise. `_exec_operator` coordinates motion simulation (via `_apply_motion`), range normalization, and the delete/yank/change action. Text objects (`iw`, `aw`, `i(`, `a"`, etc.) are handled as a sub-state within operator-pending via `_pending_textobj`.

**Register and clipboard** — `_set_register(text, linewise)` stores text in the unnamed register and copies to system clipboard according to `opt_clipboard`: `osc52` (OSC 52), `auto` (OSC 52 then best-effort external command), or `off`. `_paste_after` / `_paste_before` insert register contents — linewise paste inserts whole lines above/below; charwise paste inserts inline. `reg_linewise` tracks whether the register holds lines or characters, which determines paste behavior.

**Visual edit ops** — `d`/`x`, `y`, and `c` work in both VISUAL and VISUAL_LINE modes. `_visual_delete` and `_visual_yank` normalize the selection via `_selection_range`, then delegate to `_delete_range` / `_yank_range`. After the operation, mode returns to NORMAL (or INSERT for `c`).

**Search** — `/` and `?` enter SEARCH mode, which captures a regex pattern in the command bar. On Enter, `_search_next(direction)` compiles the pattern with `re.compile` and iterates through buffer lines from the position after the cursor (wrapping around). Forward search uses `re.search`; backward search uses `re.finditer` to find the last match before the cursor. `n` repeats in the same direction; `N` reverses. The last pattern is stored in `search_pattern` and reused when Enter is pressed with an empty prompt.

**Substitute** — `_exec_command` detects `:[range]s/pat/repl/[g]` via a regex match before the generic command parser. The delimiter must be a non-alphanumeric, non-whitespace character (this prevents `:set number` from being misinterpreted as a substitute command). `_exec_substitute` parses the range (current line, `%` for whole file, or `N,M` line numbers), compiles the pattern, and runs `re.subn` on each line in range. The `g` flag controls whether all matches or just the first are replaced. The delimiter is captured dynamically (any punctuation after `s`), so `s|pat|repl|` also works.

**Undo / Redo** — full-buffer snapshots stored on two stacks (`_undo_stack` and `_redo_stack`). Each snapshot is a tuple of `(lines[:], cx, cy)`. `_snapshot()` pushes to the undo stack and clears the redo stack. `_undo()` pops from undo, pushes current state to redo. `_redo()` does the reverse. Stack is capped at 100 entries.

**Snapshot placement** — snapshots are taken at two granularities:

- Atomic: before any destructive Normal/Visual mode operation (`dd`, `d{motion}`, `D`, `C`, `cc`, `c{motion}`, `p`, `P`, visual `d`/`x`/`c`, substitute, `>>`, `<<`, `gcc`). Also before entering Insert mode from `i`/`a`/`I`/`A`/`o`/`O`.
- Periodic during Insert: every 2 WORD boundaries (space→non-space transitions) typed from the keyboard. This breaks long insert sessions into undoable chunks of ~2 words each.

**Dirty flag with undo** — `_undo_save_depth` records `len(_undo_stack)` at the last save. `_undo_branched` is set `True` when clearing the redo stack would discard the save point (i.e., the user undid past the save, then made a new edit). `_update_dirty()` sets `buf.dirty = (len(_undo_stack) != _undo_save_depth) or _undo_branched`. On save, `_undo_save_depth` is updated and `_undo_branched` is cleared. Each buffer has its own undo/redo stacks stored in its `BufferState`.

**Word motions** — characters are classified as word (`[a-zA-Z0-9_]`), punctuation, or space. Small word motions (`w b e`) treat punctuation runs as separate words. Big WORD motions (`W B E`) only split on whitespace. The algorithm uses `_forward`/`_backward` helpers to step through the buffer one character at a time, crossing line boundaries.

**Count prefixes** — digits `1-9` (and subsequent `0-9`) accumulate in `self.count`. When a motion key arrives, it executes `max(count, 1)` times. Count resets to 0 after any non-digit key.

**Find-char motions** — `f t F T` set `_pending_find` and wait for the next key as the target character. `_exec_find(cmd, ch, n)` executes the motion and saves it in `last_find` for `;` (repeat) and `,` (reverse). `_motion_f/_motion_F` scan forward/backward on the current line; `_motion_t/_motion_T` stop one position short.

**Bracket matching** — `%` invokes `_motion_percent()`, which scans forward from the cursor for any bracket character (`({[]})`), then uses depth counting to find the matching bracket, scanning across lines.

**Indent / Dedent** — `>>` adds 4 spaces to the start of `n` lines. `<<` removes up to 4 leading spaces. Both accept a count prefix.

**Autoindent** — when `opt_autoindent` is True (default), Enter in Insert mode copies the leading whitespace from the current line to the new line. Also applies to `o`/`O` (open line below/above).

**Comment toggle** — `gcc` toggles line comments for `n` lines using `opt_comment` (default `#`). `_toggle_comment` checks whether all non-empty lines in the range are already commented; if so, it removes the comment prefix, otherwise adds it. `gc` also works in Visual mode. The comment character is configurable via `:set comment=X`.

**Text objects** — `_find_word_object(big, around)` handles `iw`/`iW`/`aw`/`aW`. `_find_bracket_object(open_ch, close_ch, around)` handles `i(`/`a(`/`i[`/`a[`/`i{`/`a{` using depth counting. `_find_quote_object(quote_ch, around)` handles `i"`/`a"`/`i'`/`a'` on a single line. All return `(sy, sx, ey, ex)` tuples consumed by operator-pending.

**Dot repeat** — `_start_dot(count, first_keys)` begins recording keystrokes for the current action, pre-populating with any keys already consumed (e.g., the `d` in `dd`). `_save_dot()` stores the recording as `_last_action = (count, keys)`. `.` invokes `_dot_repeat(n, extra_n)` which replays the saved keys through `handle_normal`/`handle_insert` with `_replaying_dot = True` to prevent nested recording. The dot count can override the original count.

**Read and bang** — `:read <file>` inserts file contents below the cursor. `:read !<cmd>` inserts command output. `:! <cmd>` / `:!<cmd>` runs a shell command and shows one-line truncated output in the message bar. `_exec_read(arg)` handles reads; bang commands use `subprocess.run`.


## Implementation Notes

**Raw mode** — `tty.setraw()` disables canonical mode, echo, and signal generation. The original `termios` attributes are saved and restored via `atexit`. The SIGWINCH handler re-queries terminal size and triggers a redraw. Ctrl-Z restores terminal state, moves the terminal cursor to the bottom line, sends `SIGTSTP` for normal job control, and re-enters raw mode when the process returns to the foreground. Ctrl-C cancels pending input/state and returns to Normal mode.

**Key reading** — `os.read(fd, 1)` gets one byte. If it's `0x1B`, a `select` with 20ms timeout checks for follow-up bytes to decode arrow keys and other escape sequences. Bare Esc (no follow-up) returns `"ESC"`. This approach avoids blocking on ambiguous escape sequences.

**Cursor clamping** — `_clamp_cursor` runs after every action. `cy` is clamped to `0..len(lines)-1`. `cx` is clamped to `0..len(line)` (one past end). `_ensure_scroll` adjusts the scroll offset so the cursor row is always visible.

**Insert efficiency** — each character typed creates a new string for the current line via `str[:cx] + ch + str[cx:]`. This is O(n) per line length, which is fast for any reasonable line. If profiling showed this as a bottleneck, the line under the cursor could temporarily become a `list` of characters during Insert mode, joined to `str` on Esc. This hasn't been necessary.

**Visual selection** — `_selection_range` normalizes the anchor/cursor into a `(start_y, start_x, end_y, end_x)` tuple. The renderer checks each visible line against this range and wraps the overlapping portion in `\x1b[7m` (reverse video) / `\x1b[m` (reset).

**Cursor shape** — DECSCUSR escape sequences switch cursor appearance per mode: `\x1b[2 q` (steady block) in Normal/Visual/Command, `\x1b[6 q` (steady bar) in Insert. On exit, `\x1b[0 q` resets to the terminal's default cursor.

**Status bar** — reverse-video full-width bar showing mode, filename, dirty flag, pending count, and cursor position. When multiple buffers are open, shows `[N/M]` indicator (current/total). Built as a padded string exactly `cols` characters wide.

**Resize** — `SIGWINCH` triggers `_handle_resize`, which re-queries `shutil.get_terminal_size()`, re-clamps cursor and scroll, and calls `render()` immediately.


## Testing

**Harness** — each test forks a child process connected via `pty.openpty()`. The child exec's `python3 ved.py <file>`. The parent sends keystrokes byte-by-byte via `os.write()` and reads screen output via `os.read()`. No test framework — plain `assert`.

**PTY sizing** — the harness sets the PTY window size to 24×80 via `TIOCSWINSZ` before forking. Resize tests change the size and send `SIGWINCH` to the child.

**Timing** — a 300ms delay after fork lets ved start and render. Keys are sent one byte at a time with 30ms inter-key delay. CSI escape sequences (e.g. `\x1b[C` for Right arrow) are written atomically as a single chunk so the editor's 20ms `select` timeout decodes them correctly. Tests that send many keys (scroll test) use a longer timeout.

**Phase-selective runs** — `test_ved.py` accepts optional phase selectors (e.g. `python3 test_ved.py 29` or `python3 test_ved.py 17 29`) to run only selected phases during development. Running with no arguments executes the full suite.

**Assertions** — tests check exit code, file contents after `:wq`, and screen output for markers like reverse video escapes, filenames, or tilde rows. Screen output is decoded as UTF-8 with replacement.

**Coverage** — 174 test functions organized into 36 phase groups, covering scaffold, editing, motions, visual mode, ex commands, wrapping, line numbers, undo/redo, operators, text objects, comments, dot repeat, shell/read commands, multi-buffer behavior, path handling, scrolloff, clipboard modes, and small command/edit fixes. Run with `python3 test_ved.py`.


## Workflow for AI Agents

**Front-load clarification.** Before starting a phase or significant change, gather all ambiguous requirements in a single batch of questions. Then proceed through implementation autonomously without stopping for confirmation on routine decisions.

**Phase gate.** After implementing a phase, run its smoke tests. Compare actual vs expected. If all pass, move on. If failures are minor (off-by-one, timing), fix and re-run. If stuck in a fix-fail loop for more than 3 attempts on the same issue, stop and ask the user for guidance rather than thrashing.

**Incremental progress.** Each phase produces a working, testable editor. Never leave the codebase in a broken state between steps. If a change is too large to land cleanly, break it into smaller changes that each pass all existing tests.

**No speculative features.** Implement only what's specified in the plan. Don't add undo "because it might be useful" or syntax highlighting "while we're at it." If a feature isn't in the plan, it doesn't exist until the user asks for it.

**Track progress visibly.** Use a todo list for multi-step work. Mark items in-progress before starting, completed immediately after finishing. This gives the user visibility into what's happening and prevents backtracking.

**Test before declaring done.** Run the full test suite after any change, not just the tests for the current phase. Regressions in earlier phases are bugs.
