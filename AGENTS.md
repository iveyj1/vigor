# vigor — Agent Guidance

> Use `###` headings, **bold** for subheadings within sections, minimal dividers.
> Keep paragraphs short. Prefer lists over prose where possible.
> No tables unless genuinely tabular data. No horizontal rules between sections.


### Project Overview

vigor is a compact, module-oriented, vi-style terminal text editor written in Python. It uses raw ANSI escape codes for terminal interaction — no curses library and no third-party Python packages.

The project goal is a practical editor that remains inspectable despite a feature set that has grown well beyond the original minimal prototype. It intentionally includes common vi-style editing features while avoiding plugin systems, macros, unnecessary abstraction, and required external runtime tools. It includes line-local regex highlighting for comments, strings, numbers, keywords, types, constants, definitions, and related entities in Python, C, C++, and Bash files; extensionless files with exact Bash/sh shebangs use Bash highlighting. Optional Markdown fence hiding removes ```/~~~ marker rows from Markdown layout while `:md` view is active, without changing source text; supported fence information strings select embedded Bash, C, C++, or Python highlighting.

**Files**

- `vig` — source-tree and installed launcher for `python3 -m vigor`
- `vigor/app.py` — editor state composition, buffer orchestration, viewport control, and event loop
- `vigor/state.py` — buffer content and per-buffer state
- `vigor/terminal.py` — raw terminal ownership and input decoding
- `vigor/highlight.py` — source-coordinate syntax, search, and Markdown presentation helpers
- `vigor/layout.py` — display columns, visible rows, bidirectional coordinate mapping, and rendering
- `vigor/editing.py` — undo, motions, operators, registers, and buffer mutations
- `vigor/commands.py` — prompts, ex commands, completion, quickfix, and subprocesses
- `vigor/modes.py` — Normal, Insert, Visual, and Search input dispatch
- `vigor/__main__.py` — `python3 -m vigor` entry point
- `vighelp` — concise in-editor command and option help opened by `:help`
- `example-config` — every supported startup option shown at its runtime default
- `test_vig.py` — PTY-based smoke tests and focused layout checks (plain asserts, no framework, 354 test functions)
- `AGENTS.md` — current requirements, architecture, and contributor guidance
- `reference.md` — full command reference
- `tutor` — exercise-driven vigor tutorial, opened with `vig tutor`
- `todo.md` — active, deferred, and completed work
- `proposals/` — accepted and deferred feature designs, including `module-architecture.md`
- `archive/PLAN.md` — retired original development plan, kept for history only
- `scripts/install` — installs the runtime, launcher, and help file while stamping build identification
- `scripts/vig-diagnostics` — optional GCC/Clang and Python diagnostic-producer wrapper
- `scripts/update_cloc_by_commit.sh` — saves per-commit runtime Python cloc history to `scripts/cloc_by_commit.md`

### Management

Requirements may arrive as numbered development phases. Review all supplied phases before implementation and ask any necessary questions together. Add requested features to the requirements.

Commit at the end of each completed development phase. Do not leave partial or failing work in a phase commit.

### General Guidance

**Keep it compact.** Every feature and every line of code must justify its existence. Compact now means proportionate to the implemented feature set, not adherence to the original prototype's size.

**Module-oriented runtime.** Runtime code lives in the `vigor` package according to `proposals/module-architecture.md`. Keep dependency direction explicit, avoid circular imports, and prefer cohesive modules over either one monolith or many tiny files. The `vig` launcher invokes `python3 -m vigor`.

**Stdlib only.** Runtime code uses Python stdlib modules only: currently `sys`, `os`, `re`, `base64`, `termios`, `tty`, `atexit`, `signal`, `shutil`, `select`, `shlex`, `time`, `enum`, and local `subprocess` imports for shell/clipboard commands. No pip packages. No curses. Tests add PTY/tempfile/terminal-control helpers.

**ANSI, not curses.** All terminal control uses escape sequences written to stdout. This gives us complete control over what bytes hit the terminal and keeps the rendering logic transparent.

**Config and help maintenance.** Whenever a config option is added, removed, renamed, or its default changes, update `example-config` so it lists every supported option at its runtime default. Update `vighelp`, `reference.md`, and the option lists in this file in the same phase.


### Requirements

**Modes**

`Mode` is defined in `vigor.state` so application and command dispatch share it without circular imports.

- NORMAL — navigation, mode switching, count prefixes
- INSERT — text entry, Esc returns to NORMAL
- COMMAND — `:` prefix, Enter executes, Esc cancels
- VISUAL / VISUAL LINE — selection with reverse video highlight
- SEARCH — `/` or `?` prompt for pattern input, Enter executes

**Normal mode commands** — `h j k l` (movement), `w W b B e E` (word motions), `gg` / `G` (go to first/last line, or line N with count), `0` (column 0), `^` (first non-blank), `$` (end of line), `Home` / `End` (start/end of line), `Ctrl-D` / `Ctrl-U` (half-page down/up), `Ctrl-E` / `Ctrl-Y` (scroll viewport down/up), `f t F T` (find char on line), `;` `,` (repeat/reverse find), `%` (match bracket), `i I a A` (enter insert), `o` / `O` (open line below/above), `v V` (enter visual), `:` (enter command), `/` `?` (search forward/backward), `*` / `#` (whole-word search under cursor forward/backward), `g*` / `g#` (partial-word search under cursor forward/backward), `n` `N` (repeat search same/opposite direction), `u` (undo), `Ctrl-R` (redo), `Ctrl-C Ctrl-C` (`:qall`), `Ctrl-C q` (`:qall!`), `.` (dot repeat last change), `x` / Delete (delete char under cursor), `X` / Backspace (delete char before cursor), `r{char}` (replace char under cursor; count replaces N chars), `s` (substitute char and enter Insert; count deletes N chars before Insert), `~` (toggle case under cursor; count applies to N chars), `g~` / `gU` / `gu` (toggle/upper/lower-case operators), `J` (join with next line), `<space>` (leader key for shortcuts: `<space>d` deletes the current buffer, `<space>w` toggles wrap, `<space>n` next buffer, `<space>N` previous buffer, `<space>c` quickfix buffer, `<space>o` opens the current quickfix location, and `<space>j` / `<space>k` open the next/previous quickfix location). Movement and editing commands accept count prefixes where documented (`3j`, `5w`, `3G`, etc.). Operators `d y c` enter operator-pending mode and combine with a motion (`dw`, `cw`, `yj`). `yd{motion}` deletes and copies, which is useful when `nodelcopy` is set. Operators also combine with text objects (`diw`, `ci(`, `da"`, etc.). Doubled operators (`dd`, `yy`, `cc`) act linewise. `>>` / `<<` indent/dedent lines by 4 spaces. `gcc` toggles line comment. Shortcuts `D Y C` operate from the cursor to end-of-line. `p` / `P` paste from the unnamed register after/before the cursor. For an unrecognized one-key Space combination, Space is a no-op and the following key is dispatched normally.

**Command mode** — `vigor.commands.CommandMixin` owns prompt editing, completion, ex dispatch, quickfix, and command subprocesses. Left/Right, Backspace, and Delete edit the prompt, with the terminal cursor on the prompt; Up/Down browse command history and Tab completes path arguments for `:e`, `:w`, `:read`, `:rgf`, `:cd`, and shell paths in `:!` commands. A single completion fills the command line; multiple matches show a centered rounded-border menu with reverse-video selection. Up/Down moves the selection, Tab/Shift-Tab advance/reverse it with wrapping, Enter copies the selected filename into the command line, and Esc hides the menu. `:new`, `:help` (open executable-directory `vighelp`), `:md` / `:markdown` (toggle non-destructive Markdown presentation), `:nomd` (literal source display), `:filetype` / `:ft [auto|text|bash|c|cpp|python|markdown]` (report or force the per-buffer type), `:cd <path>` (change the single global working directory), `:cdb` (change to the focused file's directory), `:pwd` (show the working directory), `:e[dit] <path>` (adds a new buffer), `:e[dit]!` (reloads current named buffer from disk and discards unsaved changes; errors if unnamed), `:w[rite] [path]`, `:q[uit]` (closes buffer if >1, else quits; refuses if dirty), `:q!` (force), `:wq` (write and close buffer/quit), `:qa` / `:qall` / `:qa!` / `:qall!` (quit all buffers), `:n` / `:next` / `:bn` (next buffer), `:p` / `:prev` / `:bp` (prev buffer), `:ls` (list buffers), `:k` / `:bdelete` (delete buffer, blocks if dirty), `:k!` / `:bdelete!` (force delete buffer), `:[range]s/pat/repl/[g]` (substitute), `:set <option>` (set wrap/nowrap/wrapcol=N/wrapmove/nowrapmove/number/nonumber/relativenumber/norelativenumber/autoindent/noautoindent/comment=X/scrolloff=N/clipboard=osc52|auto|off/yankflash=N/delcopy/nodelcopy/rghidden/norghidden/hlsearch/nohlsearch/markdownfences/nomarkdownfences/autodetect/noautodetect/saveversions=N/autosave/noautosave/autosavedelay=N/mouse=off|scroll|cursor|visual/makeprg=CMD), `:make [args]` (run `makeprg`, default `make`, into quickfix), `:qf !<cmd>` (run any diagnostic producer into quickfix), `:rg <pattern> [path]` (run `rg -n --column`, plus `-H` when `rghidden` is set, into a quickfix buffer), `:rgf [path]` (launch optional `fzf`; Enter sends all filtered ripgrep results into quickfix), `:read <file>` (insert file below cursor), `:read !<cmd>` (insert command output below cursor), `:! <cmd>` / `:!<cmd>` (run shell command and show one-line truncated output in the message bar), `:[range]!<cmd>` (pipe lines to shell command stdin and replace the range with stdout), and `:[range]!!<cmd>` / `:!!<cmd>` (pipe range, or whole buffer without a range, to shell command and open stdout in a new buffer). Explicit relative paths resolve from the single process working directory, initially the invocation directory and changed globally by `:cd`/`:cdb`; `~` expands. Quickfix remembers its producer working directory so later directory changes do not reinterpret results. If `:w`/`:wq` targets a missing parent directory, vigor prompts to create it before writing.

**Insert mode** — printable characters insert at cursor. Bracketed paste inserts pasted text literally, normalizing CRLF/CR to LF and not interpreting tabs, Esc, or newlines as typed keys. Tab inserts spaces to the next 4-column tab stop. Enter splits the line (with autoindent, copies leading whitespace). Backspace deletes backward or joins lines. Delete removes the character under cursor. Arrow keys and Home/End move the cursor via `_exec_motion`, same as in Normal mode. Esc returns to NORMAL without moving the cursor.

**Mouse** — `:set mouse=off|scroll|cursor|visual` controls SGR mouse reporting; default is `off`. Wheel scrolling works in every editor mode and moves three display rows per event. With `cursor` or `visual`, a left click maps through `ViewportLayout` and repositions the source cursor while retaining the current editing or prompt mode. Status/message clicks are ignored. With `mouse=visual`, left press plus motion enters characterwise Visual mode from the press anchor; release leaves the selection active without yanking. A press/release without motion remains cursor positioning. Shift-drag remains the documented terminal-native selection escape hatch where supported by the terminal.

**Full terminal** — vigor uses the entire terminal window. Content rows = terminal height minus 2 (status bar + command/message bar). Long lines are truncated by default and wrapped when `:set wrap` is enabled; nonzero `wrapcol` caps wrapping at that display column, while terminal content width remains the hard maximum. In nowrap mode, the visible window horizontally scrolls as needed to keep the cursor visible. With `wrapmove`, vertical motions (`j`/`k`/Up/Down) move by displayed rows inside wrapped lines. At startup, vigor renders the initial editor frame and overlays a horizontally centered, rounded, colored rectangle high on the screen, approximately one and a half times the logo's width and height. A footer shows the semantic version and build identifier (`development` in source, commit/date in installed copies). The overlay remains until a keypress for an unnamed buffer, or for up to two seconds when command-line files are opened; the dismissing key still executes normally. An existing directory argument instead opens the directory immediately in the filename-completion menu with no splash. Markdown files automatically enter `:md` presentation when detection is enabled. Other arguments open as buffers, later directory arguments are ignored, and Esc cancels directory completion without closing those buffers.


**Project tooling**

- `scripts/vig-diagnostics [--cwd DIR] <command> [args...]` runs a command directly, preserves its exit status and merged output, strips ANSI, and normalizes GCC/Clang locations and Python traceback frames to absolute quickfix paths. It is optional tooling, not an editor runtime dependency.
- `scripts/update_cloc_by_commit.sh` records cloc counts and short commit subjects for every commit reachable from the current branch.

### Divergences from vi

vigor is vi-inspired, not vi-compatible. These differences are intentional:

**Esc from Insert keeps cursor in place.** vi moves left one column on Esc. vigor does not. The cursor stays exactly where it was when Esc was pressed. This eliminates a common source of confusion and is the single most important divergence.

**Cursor past end-of-line is allowed in all modes.** vi clamps the cursor to the last character in Normal mode. vigor allows the cursor on the position after the last character in every mode. This simplifies the clamping logic and makes cursor behavior consistent regardless of mode.

**Single unnamed register, no macros.** vigor has one unnamed register that holds the last deleted or yanked text. Clipboard copy mode is configurable via `:set clipboard=osc52|auto|off` (current default `auto`). There are no named registers and no macros.

**Minimal ex commands.** vi has dozens of ex commands. vigor supports only: new, edit, write, quit, wq, qa, next, prev, ls, k/bdelete, cd/cdb/pwd, set, filetype, substitute, read, bang, make, qf, rg, and rgf. Abbreviations (`:e`, `:w`, `:q`, `:r`, `:n`, `:p`, `:k`) work.


### Architecture

**Buffer** — a `list[str]` where each element is one line of text (no trailing newline stored). A `path` and `dirty` flag track file association and modification state. Saving writes each line followed by `\n`. With nonzero `saveversions`, changed existing targets of explicit writes first rotate raw adjacent copies named `.vigor-bak.<basename>.N`; backup failure blocks the write.

**BufferState** — bundles a `Buffer` with per-buffer state: cursor position (`cx`, `cy`), logical-line scroll offset, wrapped-row top offset (`wrap_skip`), Markdown presentation state/projection (`md_view`, `md_lines`, `md_maps`, `md_languages`), a per-buffer file-type override (`filetype_override`), the detection policy captured when the buffer opened (`autodetect`), an autosave deadline, and undo/redo history (`_undo_stack`, `_redo_stack`, `_undo_save_depth`, `_undo_branched`). Uses `__slots__` for efficiency. Created once per opened file. Opening or creating a buffer replaces an untouched initial unnamed buffer rather than retaining it.

**Editor** — top-level state container in `vigor/app.py`. Holds a list of `BufferState` objects (`self.buffers`) and a current index (`self.buf_idx`). Working attributes (`self.buf`, `self.cx`, `self.cy`, `self.scroll`, undo stacks) point to the current buffer's state. `_save_buf_state()` syncs working attrs back to the current `BufferState`; `_load_buf_state(idx)` loads from a `BufferState` into working attrs; `_switch_buffer(idx)` does save + load + clamp + scroll + reset mode. Also holds current mode, command-line input, status message, visual anchor, terminal dimensions, count prefix accumulator, and run flag. One instance, created in `main()`. The unnamed register is shared across all buffers.

**Terminal** — `vigor/terminal.py` manages raw mode via `termios`, reads keys and structured paste/mouse events with escape-sequence decoding, and restores terminal state on exit via `atexit`. Mouse reporting is disabled on exit, suspension, and temporary handoff, then restored with raw mode.

**Rendering** — `vigor.layout.RenderMixin` performs one full redraw per keystroke. The entire frame is built as a list of strings, joined, and written in a single `sys.stdout.write()` call. This eliminates flicker without requiring double-buffering. The frame consists of content rows (with optional line-number gutter, syntax/visual highlighting, and line wrapping), a reverse-video status bar, and a command/message bar. `vigor.layout.ViewportLayout` produces visible rows and maps source positions to/from screen cells; `_render_visible` applies source-coordinate spans to each visible segment. `vigor.highlight` builds per-buffer Markdown display lines and source-to-display maps and returns source-coordinate search/syntax spans. Its `NAMED_COLORS`, `SYNTAX_COLOR_NAMES`, and `MARKDOWN_COLOR_NAMES` dictionaries keep ANSI values separate from easily edited semantic color choices. Markdown presentation styles headers/list markers and virtually pads valid pipe tables without modifying source or dirty state. Its fence metadata distinguishes prose, markers, unknown code, and supported embedded languages; Bash, C, C++, and Python fences use the same line-local lexers as source files. With `markdownfences`, `ViewportLayout` omits matched marker source rows from display while retaining source line numbers, search positions, mouse mapping, and wrapped scrolling. A cursor on a hidden marker maps to the nearest visible row; any mutation disables the projection before editing.

**Line numbers** — `_gutter_width()` returns the gutter width (0 when disabled, otherwise a five-column number field plus one separator, expanding when the file exceeds 99,999 lines). `_gutter_str(buf_line, gutter_width)` right-aligns absolute numbers when `relativenumber` is off. With `relativenumber`, the cursor row shows its absolute number flush left while other rows show right-aligned relative distances. Content columns are reduced by the gutter width. In wrap mode, only the first wrapped row of a line shows the number; continuation rows get blank padding.

**Line wrap / horizontal scroll** — tabs are rendered as spaces to four-column stops, with `_display_col` / `_display_index` translating between buffer indices and display columns for cursor placement, highlighting, horizontal scrolling, and vertical sticky-column motion. When `opt_wrap` is true, each logical line is split into display rows at `_wrap_cols()`: terminal content width (total cols minus gutter), capped by nonzero `opt_wrapcol`. The one-past-EOL cursor cell participates in wrapping, so an exact-width logical line gets a blank continuation display row for EOL. `_line_screen_rows(line_idx)` computes this layout. The render loop tracks `screen_rows_used` and `cursor_screen_y`/`cursor_screen_x`; `_ensure_scroll` keeps the cursor visible. The per-buffer `_wrap_skip` skips display rows within the top logical line, persists manual viewport scrolling across buffer switches, and supports narrow resizes. With `wrapmove`, vertical motions preserve a display column and cross between the last/first display rows of adjacent logical lines symmetrically. When wrap is off, all visible buffer lines share one horizontal offset based on the cursor column.

**Growth seams** — anticipated mouse positioning/selection and collapsed Markdown rows must share one authoritative layout that maps source positions to screen cells and screen cells back to source positions. Do not add independent coordinate calculations for each feature. Autosave deadlines share the main loop's existing `select` wait with splash and yank-flash timers; no background thread is used. Multiline syntax highlighting would require per-line lexical state or a state cache; the current enhanced lexers intentionally remain line-local.

**Mode handlers** — `vigor.modes.ModeMixin` owns Normal, Insert, Visual, and Search dispatch; `vigor.commands.CommandMixin` owns Command dispatch. The application loop dispatches based on `self.mode`. Handlers remain direct `if/elif` chains and call editing/orchestration helpers through the composed `Editor`.

**Motion dispatch** — `vigor.editing.EditingMixin._exec_motion(key, n)` is the single source of truth for all motion execution (`h l j k w W b B e E` and arrow keys). It is called by `handle_normal`, `handle_visual`, and `_apply_motion` (which wraps it with cursor save/restore for operator-pending). Vertical motion preserves `_sticky_cx`, restoring the desired column after a shorter line. The `_MOTION_KEYS` frozenset provides O(1) membership checks.

**Operator-pending** — typing `d`, `y`, or `c` in Normal mode sets `pending_op` and saves the current count in `pending_count`. The next key is treated as a motion. The operator then acts on the range from the original cursor to where the motion would land. Doubled operators (e.g., `dd`) are linewise. `_exec_operator` coordinates motion simulation (via `_apply_motion`), range normalization, and the delete/yank/change action. Text objects (`iw`, `aw`, `i(`, `a"`, etc.) are handled as a sub-state within operator-pending via `_pending_textobj`.

**Register and clipboard** — `_set_register(text, linewise)` stores text in the unnamed register and copies to system clipboard according to `opt_clipboard`: `osc52` (OSC 52), `auto` (best-effort external command, then OSC 52 fallback), or `off`. Yank operations briefly highlight yanked text for `opt_yankflash` milliseconds (default 300; `:set yankflash=0` disables it). When `delcopy` is set (default), delete operators update the unnamed register; with `nodelcopy`, `d{motion}` deletes without changing it and `yd{motion}` deletes while updating it. `_paste_after` / `_paste_before` delegate to `vigor.editing.paste`; linewise paste inserts whole lines above/below, while multiline characterwise paste splits into logical buffer lines without storing embedded newlines. `reg_linewise` tracks whether the register holds lines or characters, which determines paste behavior.

**Visual edit ops** — `d`/`x`, `y`, and `c` work in both VISUAL and VISUAL_LINE modes. `_visual_delete` and `_visual_yank` normalize the selection via `_selection_range`, then delegate to `_delete_range` / `_yank_range`. After the operation, mode returns to NORMAL (or INSERT for `c`).

**Search** — `/` and `?` enter SEARCH mode, which captures a regex pattern in the command bar. Left/Right, Backspace, and Delete edit the prompt, with the terminal cursor on the prompt; Up/Down browse a shared `/` and `?` search history. On Enter, lowercase literal patterns compile with `re.IGNORECASE`; patterns containing an uppercase letter or regex metacharacter remain case-sensitive. `_search_next(direction)` searches from the position after/before the cursor, including additional hits on the current line before moving to other lines and wrapping around. Forward search uses `re.search`; backward search uses `re.finditer` to find the last match before the cursor. `*`/`#` set `search_pattern` to a regex-escaped whole-word search for the small word under the cursor and search forward/backward; `g*`/`g#` do the same without word boundaries for partial matches. Lowercase word searches also ignore case. `n` repeats in the same direction; `N` reverses. Successful searches center the match vertically when file boundaries permit. While `/` or `?` is being typed, all visible matches preview without moving the cursor; incomplete regexes quietly show no preview, and Esc clears it. The last pattern is stored in `search_pattern` and reused when Enter is pressed with an empty prompt. `:set hlsearch` persists all non-empty matches of the active regex after search; default is off. Ordinary matches use yellow and the match containing the cursor uses magenta.

**Substitute** — `_exec_command` detects `:[range]s/pat/repl/[g]` via a regex match before the generic command parser. The delimiter must be a non-alphanumeric, non-whitespace character (this prevents `:set number` from being misinterpreted as a substitute command). `_exec_substitute` parses the range (current line, `%` for whole file, or `N,M` line numbers), compiles the pattern, and runs `re.subn` on each line in range. The `g` flag controls whether all matches or just the first are replaced. The delimiter is captured dynamically (any punctuation after `s`), so `s|pat|repl|` also works.

**Undo / Redo** — full-buffer snapshots stored on two stacks (`_undo_stack` and `_redo_stack`). Each snapshot is a tuple of `(lines[:], cx, cy)`. `_snapshot()` pushes to the undo stack and clears the redo stack. `_undo()` pops from undo, pushes current state to redo. `_redo()` does the reverse. Stack is capped at 100 entries.

**Snapshot placement** — snapshots are taken at two granularities:

- Atomic: before any destructive Normal/Visual mode operation (`dd`, `d{motion}`, `D`, `C`, `cc`, `c{motion}`, `p`, `P`, visual `d`/`x`/`c`, substitute, `>>`, `<<`, `gcc`). Also before entering Insert mode from `i`/`a`/`I`/`A`/`o`/`O`.
- Periodic during Insert: every 2 WORD boundaries (space→non-space transitions) typed from the keyboard. This breaks long insert sessions into undoable chunks of ~2 words each.

**Dirty flag with undo** — `_undo_save_depth` records `len(_undo_stack)` at the last save. `_undo_branched` is set `True` when clearing the redo stack would discard the save point (i.e., the user undid past the save, then made a new edit). `_update_dirty()` sets `buf.dirty = (len(_undo_stack) != _undo_save_depth) or _undo_branched`. On save, `_undo_save_depth` is updated and `_undo_branched` is cleared. Each buffer has its own undo/redo stacks stored in its `BufferState`.

**Word motions** — characters are classified as word (`[a-zA-Z0-9_]`), punctuation, or space. Small word motions (`w b e`) treat punctuation runs as separate words. Big WORD motions (`W B E`) only split on whitespace. The algorithm uses `_forward`/`_backward` helpers to step through the buffer one character at a time, crossing line boundaries.

**Autosave** — `opt_autosave` defaults off; `opt_autosavedelay` defaults to 1000 milliseconds. Dirty assignments invoke a lightweight buffer callback that resets the owning `BufferState` deadline. The main loop waits for the earliest timer and saves every due dirty named buffer, including unfocused buffers. Success updates its undo save point and clears dirty state; failure leaves it dirty, reports once, and waits for another mutation before retrying. Unnamed buffers are silently skipped, explicit writes clear deadlines, missing parents are errors, and autosaves bypass manual version rotation.

**Manual-save versions** — `opt_saveversions` retains 0–100 prior disk versions for explicit writes only; default `0` disables it. Existing changed targets are copied with metadata before overwrite, newest at `.vigor-bak.<basename>.1`. Unchanged/new targets and backup-named files are skipped, lowering retention removes excess generations on the next versioned write, and any preservation failure blocks the write.

**Automatic file types** — `opt_autodetect` defaults on and controls extension/shebang syntax recognition plus automatic Markdown presentation for newly opened buffers. Each buffer captures the option when opened, so changing it does not retroactively alter open buffers. `:ft auto` explicitly enables and reruns detection for the current buffer; forced types and explicit `:md`/`:nomd` remain available when global detection is off.

**Startup config** — unless `VIG_NO_CONFIG` is set, vigor reads simple set-style config from `~/.vigrc` and `$XDG_CONFIG_HOME/vig/config` (later files override earlier ones). `VIG_CONFIG=/path` reads only that file. Non-empty, non-comment lines may be `set <option>`, `:<set command>`, or just `<option>` using the same options accepted by `:set`. 

**Count prefixes** — digits `1-9` (and subsequent `0-9`) accumulate in `self.count`. When a motion key arrives, it executes `max(count, 1)` times. Count resets to 0 after any non-digit key.

**Find-char motions** — `f t F T` set `_pending_find` and wait for the next key as the target character. `_exec_find(cmd, ch, n)` executes the motion and saves it in `last_find` for `;` (repeat) and `,` (reverse). `_motion_f/_motion_F` scan forward/backward on the current line; `_motion_t/_motion_T` stop one position short.

**Bracket matching** — `%` invokes `_motion_percent()`, which scans forward from the cursor for any bracket character (`({[]})`), then uses depth counting to find the matching bracket, scanning across lines.

**Indent / Dedent** — `>>` adds 4 spaces to the start of `n` lines. `<<` removes up to 4 leading spaces. Both accept a count prefix.

**Autoindent** — when `opt_autoindent` is True (default), Enter in Insert mode copies the leading whitespace from the current line to the new line. Also applies to `o`/`O` (open line below/above).

**Comment toggle** — `gcc` toggles line comments for `n` lines using `opt_comment` (default `#`). `_toggle_comment` checks whether all non-empty lines in the range are already commented; if so, it removes the comment prefix, otherwise adds it. `gc` also works in Visual mode. The comment character is configurable via `:set comment=X`.

**Text objects** — `_find_word_object(big, around)` handles `iw`/`iW`/`aw`/`aW`. `_find_bracket_object(open_ch, close_ch, around)` handles `i(`/`a(`/`i[`/`a[`/`i{`/`a{` using depth counting. `_find_quote_object(quote_ch, around)` handles `i"`/`a"`/`i'`/`a'` on a single line. All return `(sy, sx, ey, ex)` tuples consumed by operator-pending.

**Dot repeat** — `_start_dot(count, first_keys)` begins recording keystrokes for the current action, pre-populating with any keys already consumed (e.g., the `d` in `dd`). `_save_dot()` stores the recording as `_last_action = (count, keys)`. `.` invokes `_dot_repeat(n, extra_n)` which replays the saved keys through `handle_normal`/`handle_insert` with `_replaying_dot = True` to prevent nested recording. The dot count can override the original count.

**Read, bang, filter, build, and ripgrep** — implementations live in `vigor.commands`. `:read <file>` inserts file contents below the cursor. `:read !<cmd>` inserts command output. `:! <cmd>` / `:!<cmd>` runs a shell command and shows one-line truncated output in the message bar. `:[range]!<cmd>` pipes the selected lines to a shell command and replaces the range with stdout; `:[range]!!<cmd>` does the same but opens stdout in a new unnamed buffer, with `:!!<cmd>` defaulting to the whole buffer. Filter ranges support `%`, `.`, `$`, line numbers, and `N,M`. `:qf !<cmd>` runs a shell diagnostic producer, merges stdout/stderr, strips ANSI, normalizes `path:line:message` to column 1, retains all output in quickfix, and records the producer cwd for later relative-location opening. `:make [args]` runs configurable `makeprg` through the same path without a timeout. `:rg <pattern> [path]` runs ripgrep and captures hits in the remembered quickfix buffer; no hits leave the current buffer active and show a status message. `:rgf [path]` temporarily hands the terminal to optional `fzf` for a live ripgrep picker; Enter loads all filtered rows into quickfix. `<space>o` parses `file:line:column:` under the cursor, opens/switches to that location, and shows the acted-on quickfix line in the message bar. `<space>j` / `<space>k` advance the remembered quickfix row without wrapping and open the next/previous valid location. `_exec_read(arg)`, filters, bang, and rg commands use `subprocess.run`.


### Implementation Notes

**Raw mode** — `tty.setraw()` disables canonical mode, echo, and signal generation. Bracketed paste is enabled while vigor owns the terminal and disabled on restore/suspend. The original `termios` attributes are saved and restored via `atexit`. The SIGWINCH handler re-queries terminal size and triggers a redraw. Ctrl-Z restores terminal state, moves the terminal cursor to the bottom line, sends `SIGTSTP` for normal job control, and re-enters raw mode when the process returns to the foreground. Ctrl-C cancels pending input/state and returns to Normal mode; in Normal mode `Ctrl-C Ctrl-C` aliases `:qall` and `Ctrl-C q` aliases `:qall!`.

**Key reading** — `os.read(fd, 1)` gets one byte. If it's `0x1B`, a `select` with 20ms timeout checks for follow-up bytes to decode arrow keys and other escape sequences. Bare Esc (no follow-up) returns `"ESC"`. SGR mouse reports return `("MOUSE", button, action, x, y, modifiers)` with zero-based coordinates. This approach avoids blocking on ambiguous escape sequences.

**Cursor clamping** — `_clamp_cursor` runs after every action. `cy` is clamped to `0..len(lines)-1`. `cx` is clamped to `0..len(line)` (one past end). `_ensure_scroll` adjusts the scroll offset so the cursor row is always visible.

**Insert efficiency** — each character typed creates a new string for the current line via `str[:cx] + ch + str[cx:]`. This is O(n) per line length, which is fast for any reasonable line. If profiling showed this as a bottleneck, the line under the cursor could temporarily become a `list` of characters during Insert mode, joined to `str` on Esc. This hasn't been necessary.

**Visual selection** — `_selection_range` normalizes the anchor/cursor into a `(start_y, start_x, end_y, end_x)` tuple. The renderer checks each visible line against this range and wraps the overlapping portion in `\x1b[7m` (reverse video) / `\x1b[m` (reset).

**Cursor shape** — DECSCUSR escape sequences switch cursor appearance per mode: `\x1b[2 q` (steady block) in Normal/Visual/Command, `\x1b[6 q` (steady bar) in Insert. On exit, `\x1b[0 q` resets to the terminal's default cursor.

**Status bar** — reverse-video full-width bar showing mode, filename, dirty flag, pending count, and cursor position. When multiple buffers are open, shows `[N/M]` indicator (current/total). Built as a padded string exactly `cols` characters wide.

**Resize** — `SIGWINCH` triggers `_handle_resize`, which re-queries `shutil.get_terminal_size()`, re-clamps cursor and scroll, and calls `render()` immediately.


### Testing

**Harness** — each test forks a child process connected via `pty.openpty()`. The child execs the `vig` launcher, which runs `python3 -m vigor`. The parent sends logical keys via `os.write()` and accumulates screen output from `os.read()` in a `bytearray`. No test framework — plain `assert`.

**PTY sizing** — the harness sets the PTY window size to 24×80 via `TIOCSWINSZ` before forking. Resize tests change the size and send `SIGWINCH` to the child.

**Timing** — the harness waits for vigor's full-frame marker instead of sleeping after fork. It tokenizes input into logical keys (single bytes, complete CSI/SS3 sequences, or complete bracketed pastes), sends one key atomically, drains PTY output, and advances when the next frame begins. This naturally honors the editor's 20ms bare-Esc timeout without fixed inter-key delays and prevents full redraws from filling the PTY output buffer. Child exit is polled every 20ms; explicit longer timeouts remain only for tests that intentionally wait or invoke external tools.

**Phase-selective runs** — `test_vig.py` accepts optional phase selectors (e.g. `python3 test_vig.py 29` or `python3 test_vig.py 17 29`) to run only selected phases during development. Running with no arguments executes the full suite.

**Assertions** — tests check exit code, file contents after `:wq`, and screen output for markers like reverse video escapes, filenames, or tilde rows. Screen output is decoded as UTF-8 with replacement.

**Coverage** — 354 test functions organized into 72 phase groups (selectors 1–73, with retired phase 16 absent), covering scaffold, editing, motions, visual mode, ex commands, wrapping, line numbers, undo/redo, operators, text objects, comments, dot repeat, shell/read commands, multi-buffer behavior, path handling, scrolloff, clipboard modes, small command/edit fixes, quit aliases, startup config, ripgrep quickfix, completion/history, splash, help, fzf ripgrep selection, syntax highlighting, initial-buffer replacement, search polish, Markdown presentation, and recent polish. Run with `python3 test_vig.py`.


### Workflow for AI Agents

**Front-load clarification.** Before starting a phase or significant change, gather all ambiguous requirements in a single batch of questions. Then proceed through implementation autonomously without stopping for confirmation on routine decisions.

**Phase gate.** After implementing a phase, run its smoke tests. Compare actual vs expected. If all pass, move on. If failures are minor (off-by-one, timing), fix and re-run. If stuck in a fix-fail loop for more than 3 attempts on the same issue, stop and ask the user for guidance rather than thrashing.

**Incremental progress.** Each phase produces a working, testable editor. Never leave the codebase in a broken state between steps. If a change is too large to land cleanly, break it into smaller changes that each pass all existing tests.

**No speculative features.** Implement only what's specified in the plan. Don't add undo "because it might be useful" or syntax highlighting "while we're at it." If a feature isn't in the plan, it doesn't exist until the user asks for it.

**Track progress visibly.** Use a todo list for multi-step work. Mark items in-progress before starting, completed immediately after finishing. This gives the user visibility into what's happening and prevents backtracking.

**Test before declaring done.** Run the full test suite after any change, not just the tests for the current phase. Regressions in earlier phases are bugs.

**Phase status summary.** At the end of a phase, report work done, whether a commit was made, uncompleted work from that phase, and next steps. For a user-facing feature, give brief demo instructions.
