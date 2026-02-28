# ved — Agent Guidance

> Use `##` headings, **bold** for subheadings within sections, minimal dividers.
> Keep paragraphs short. Prefer lists over prose where possible.
> No tables unless genuinely tabular data. No horizontal rules between sections.


## Project Overview

ved is a modal, vi-inspired terminal text editor written in Python. It uses raw ANSI escape codes for all terminal interaction — no curses library. The guiding principle is radical simplicity: a single source file, zero external dependencies, and only the features that matter.

**Files**

- `ved.py` — the entire editor (~1040 lines)
- `test_ved.py` — PTY-based smoke tests (plain asserts, no framework)
- `PLAN.md` — phased development plan with specifications
- `AGENTS.md` — this document


## General Guidance

**Simplicity is the constraint, not a goal.** Every feature, every line of code must justify its existence. If something can be left out without losing core editing capability, leave it out.

**One file.** The editor lives entirely in `ved.py`. Classes and functions are organized by visual section markers (`# ── Section ──`) rather than by module. This keeps the call graph obvious, searchable, and greppable.

**Stdlib only.** The only imports are `sys`, `os`, `re`, `base64`, `termios`, `tty`, `atexit`, `signal`, `shutil`, `select`, and `enum`. No pip packages. No curses. Tests add `pty`, `tempfile`, `fcntl`, `struct`, and `time`.

**ANSI, not curses.** All terminal control uses escape sequences written to stdout. The ANSI codes used are documented in PLAN.md. This gives us complete control over what bytes hit the terminal and keeps the rendering logic transparent.


## Requirements

**Modes** — four modes form a simple state machine:

- NORMAL — navigation, mode switching, count prefixes
- INSERT — text entry, Esc returns to NORMAL
- COMMAND — `:` prefix, Enter executes, Esc cancels
- VISUAL / VISUAL LINE — selection with reverse video highlight
- SEARCH — `/` or `?` prompt for pattern input, Enter executes

**Normal mode commands** — `h j k l` (movement), `w W b B e E` (word motions), `i I a A` (enter insert), `v V` (enter visual), `:` (enter command), `/` `?` (search forward/backward), `n` `N` (repeat search same/opposite direction). All motions accept a count prefix (`3j`, `5w`, etc.). Operators `d y c` enter operator-pending mode and combine with a motion (`dw`, `cw`, `yj`). Doubled operators (`dd`, `yy`, `cc`) act linewise. Shortcuts `D Y C` operate from cursor to end-of-line (D/C) or yank the whole line (Y). `p` / `P` paste from the unnamed register after/before the cursor.

**Command mode** — `:new`, `:e[dit] <path>`, `:w[rite] [path]`, `:q[uit]` (refuses if dirty), `:q!` (force), `:wq`, `:[range]s/pat/repl/[g]` (substitute).

**Insert mode** — printable characters insert at cursor. Enter splits the line. Backspace deletes backward or joins lines. Esc returns to NORMAL without moving the cursor.

**Full terminal** — ved uses the entire terminal window. Content rows = terminal height minus 2 (status bar + command/message bar). Lines longer than the terminal width are truncated at the screen edge.


## Divergences from vi

ved is vi-inspired, not vi-compatible. These differences are intentional:

**Esc from Insert keeps cursor in place.** vi moves left one column on Esc. ved does not. The cursor stays exactly where it was when Esc was pressed. This eliminates a common source of confusion and is the single most important divergence.

**Cursor past end-of-line is allowed in all modes.** vi clamps the cursor to the last character in Normal mode. ved allows the cursor on the position after the last character in every mode. This simplifies the clamping logic and makes cursor behavior consistent regardless of mode.

**Single unnamed register, no undo, no macros.** ved has one unnamed register that holds the last deleted or yanked text. Every yank/delete also copies to the system clipboard via OSC 52. There are no named registers, no undo tree, and no macros. If you need undo, use version control.

**Minimal ex commands.** vi has dozens of ex commands. ved supports only: new, edit, write, quit, wq. Abbreviations (`:e`, `:w`, `:q`) work. That's it.


## Architecture

**Buffer** — a `list[str]` where each element is one line of text (no trailing newline stored). A `path` and `dirty` flag track file association and modification state. Saving writes each line followed by `\n`.

**Editor** — top-level state container. Holds the buffer, cursor position (`cx`, `cy`), scroll offset, current mode, command-line input, status message, visual anchor, terminal dimensions, count prefix accumulator, and run flag. One instance, created in `main()`.

**Terminal** — manages raw mode via `termios`, reads keys one at a time with escape sequence decoding, and restores terminal state on exit via `atexit`.

**Rendering** — one full redraw per keystroke. The entire frame is built as a list of strings, joined, and written in a single `sys.stdout.write()` call. This eliminates flicker without requiring double-buffering. The frame consists of: content rows (with optional visual selection highlighting), a reverse-video status bar, and a command/message bar.

**Mode handlers** — `handle_normal`, `handle_insert`, `handle_command`, `handle_visual`. Each is a flat `if/elif` chain. The main loop dispatches based on `self.mode`.

**Motion dispatch** — `_exec_motion(key, n)` is the single source of truth for all motion execution (`h l j k w W b B e E` and arrow keys). It is called by `handle_normal`, `handle_visual`, and `_apply_motion` (which wraps it with cursor save/restore for operator-pending). The `_MOTION_KEYS` frozenset provides O(1) membership checks.

**Operator-pending** — typing `d`, `y`, or `c` in Normal mode sets `pending_op` and saves the current count in `pending_count`. The next key is treated as a motion. The operator then acts on the range from the original cursor to where the motion would land. Doubled operators (e.g., `dd`) are linewise. `_exec_operator` coordinates motion simulation (via `_apply_motion`), range normalization, and the delete/yank/change action.

**Register and clipboard** — `_set_register(text, linewise)` stores text in the unnamed register and writes it to the system clipboard via OSC 52 (`\x1b]52;c;<base64>\x07`). `_paste_after` / `_paste_before` insert register contents — linewise paste inserts whole lines above/below; charwise paste inserts inline. `reg_linewise` tracks whether the register holds lines or characters, which determines paste behavior.

**Visual edit ops** — `d`/`x`, `y`, and `c` work in both VISUAL and VISUAL_LINE modes. `_visual_delete` and `_visual_yank` normalize the selection via `_selection_range`, then delegate to `_delete_range` / `_yank_range`. After the operation, mode returns to NORMAL (or INSERT for `c`).

**Search** — `/` and `?` enter SEARCH mode, which captures a regex pattern in the command bar. On Enter, `_search_next(direction)` compiles the pattern with `re.compile` and iterates through buffer lines from the position after the cursor (wrapping around). Forward search uses `re.search`; backward search uses `re.finditer` to find the last match before the cursor. `n` repeats in the same direction; `N` reverses. The last pattern is stored in `search_pattern` and reused when Enter is pressed with an empty prompt.

**Substitute** — `_exec_command` detects `:[range]s/pat/repl/[g]` via a regex match before the generic command parser. `_exec_substitute` parses the range (current line, `%` for whole file, or `N,M` line numbers), compiles the pattern, and runs `re.subn` on each line in range. The `g` flag controls whether all matches or just the first are replaced. The delimiter is captured dynamically (any character after `s`), so `s|pat|repl|` also works.

**Word motions** — characters are classified as word (`[a-zA-Z0-9_]`), punctuation, or space. Small word motions (`w b e`) treat punctuation runs as separate words. Big WORD motions (`W B E`) only split on whitespace. The algorithm uses `_forward`/`_backward` helpers to step through the buffer one character at a time, crossing line boundaries.

**Count prefixes** — digits `1-9` (and subsequent `0-9`) accumulate in `self.count`. When a motion key arrives, it executes `max(count, 1)` times. Count resets to 0 after any non-digit key.


## Implementation Notes

**Raw mode** — `tty.setraw()` disables canonical mode, echo, and signal generation. The original `termios` attributes are saved and restored via `atexit`. The SIGWINCH handler re-queries terminal size and triggers a redraw.

**Key reading** — `os.read(fd, 1)` gets one byte. If it's `0x1B`, a `select` with 20ms timeout checks for follow-up bytes to decode arrow keys and other escape sequences. Bare Esc (no follow-up) returns `"ESC"`. This approach avoids blocking on ambiguous escape sequences.

**Cursor clamping** — `_clamp_cursor` runs after every action. `cy` is clamped to `0..len(lines)-1`. `cx` is clamped to `0..len(line)` (one past end). `_ensure_scroll` adjusts the scroll offset so the cursor row is always visible.

**Insert efficiency** — each character typed creates a new string for the current line via `str[:cx] + ch + str[cx:]`. This is O(n) per line length, which is fast for any reasonable line. If profiling showed this as a bottleneck, the line under the cursor could temporarily become a `list` of characters during Insert mode, joined to `str` on Esc. This hasn't been necessary.

**Visual selection** — `_selection_range` normalizes the anchor/cursor into a `(start_y, start_x, end_y, end_x)` tuple. The renderer checks each visible line against this range and wraps the overlapping portion in `\x1b[7m` (reverse video) / `\x1b[m` (reset).

**Cursor shape** — DECSCUSR escape sequences switch cursor appearance per mode: `\x1b[2 q` (steady block) in Normal/Visual/Command, `\x1b[6 q` (steady bar) in Insert. On exit, `\x1b[0 q` resets to the terminal's default cursor.

**Status bar** — reverse-video full-width bar showing mode, filename, dirty flag, pending count, and cursor position. Built as a padded string exactly `cols` characters wide.

**Resize** — `SIGWINCH` triggers `_handle_resize`, which re-queries `shutil.get_terminal_size()`, re-clamps cursor and scroll, and calls `render()` immediately.


## Testing

**Harness** — each test forks a child process connected via `pty.openpty()`. The child exec's `python3 ved.py <file>`. The parent sends keystrokes byte-by-byte via `os.write()` and reads screen output via `os.read()`. No test framework — plain `assert`.

**PTY sizing** — the harness sets the PTY window size to 24×80 via `TIOCSWINSZ` before forking. Resize tests change the size and send `SIGWINCH` to the child.

**Timing** — a 300ms delay after fork lets ved start and render. Keys are sent one byte at a time with 30ms inter-key delay. Tests that send many keys (scroll test) use a longer timeout.

**Assertions** — tests check exit code, file contents after `:wq`, and screen output for markers like reverse video escapes, filenames, or tilde rows. Screen output is decoded as UTF-8 with replacement.

**Coverage** — 62 tests across 11 phases: scaffold (5), editing (10), word motions (6), visual mode (4), polish (4), resize (2), count prefixes (3), edit operations (11), visual edit (5), search (6), replace (6). Run with `python3 test_ved.py`.


## Workflow for AI Agents

**Front-load clarification.** Before starting a phase or significant change, gather all ambiguous requirements in a single batch of questions. Then proceed through implementation autonomously without stopping for confirmation on routine decisions.

**Phase gate.** After implementing a phase, run its smoke tests. Compare actual vs expected. If all pass, move on. If failures are minor (off-by-one, timing), fix and re-run. If stuck in a fix-fail loop for more than 3 attempts on the same issue, stop and ask the user for guidance rather than thrashing.

**Incremental progress.** Each phase produces a working, testable editor. Never leave the codebase in a broken state between steps. If a change is too large to land cleanly, break it into smaller changes that each pass all existing tests.

**No speculative features.** Implement only what's specified in the plan. Don't add undo "because it might be useful" or syntax highlighting "while we're at it." If a feature isn't in the plan, it doesn't exist until the user asks for it.

**Track progress visibly.** Use a todo list for multi-step work. Mark items in-progress before starting, completed immediately after finishing. This gives the user visibility into what's happening and prevents backtracking.

**Test before declaring done.** Run the full test suite after any change, not just the tests for the current phase. Regressions in earlier phases are bugs.
