### Standing Guidance
1) Do not support legacy configurations, file formats, or removed behaviors.  Remove any dead code due to changes.  There are no existing implementations or configuration files. 
2) Review proposed changes for estimated change size.  If the net increase in number of lines of code in the runtime for an individual item exceeds about 50, notify user before implementation.
3) If minor changes to proposed functionality would result in significant code savings, bring that to light before implementation.

### Active
None

### Implement
None

### On Hold

Ordered by recommended implementation sequence, balancing feasibility, effort, ambiguity, and dependencies.

1. Detect when a named file changed on disk after it was opened or written.
   **Moderate feasibility; 35–60 lines.** Store a per-buffer disk signature and check on buffer focus and before writes. Specify whether detection only warns, blocks writes, or offers reload; account for deletion, replacement, autosave, and vigor's own writes.
1. Add optional incremental-search scrolling when no preview hit is visible.
   **Moderate feasibility; 30–50 lines.** Existing preview spans and `ViewportLayout` make finding/centering practical, but Search must save and restore the original viewport on Esc or a failed pattern. Add it as an option only after defining that cancellation behavior.
1. Add a `\v` search modifier.
   **Small implementation after specification; 15–35 lines.** Python regex syntax is already close to Vim's “very magic” mode, so first define exactly which vigor escapes and metacharacters `\v` changes; avoid a modifier that is merely ignored.
1. Pipe stdin into the initial unnamed buffer, then use `/dev/tty` for interaction.
   **Moderate feasibility; estimated 60–90 lines, so notify before implementation.** First write the missing proposal defining non-interactive fallback, stdin/filename precedence, terminal-open failures, and behavior under pipes and redirection.
1. Add in-session location history.
   **Moderate feasibility; 40–70 lines.** Establish one jump-stack abstraction before last-location commands or marks. Define which actions create entries and whether quickfix/search/buffer switches participate.
1. Open or jump to the last location.
   **Depends on location history; 20–50 lines in-session, more if persistent.** Decide whether “last” means the prior jump, the last cursor position per open buffer, or a location persisted across invocations; persistent state needs a separate storage design.
1. Add marks.
   **Moderate-to-large; 60–100 lines.** Build on location-history jumps and per-buffer state. Specify local/global names, deletion behavior, path persistence, motions, operators, and what happens when edited text invalidates a mark.
1. Add named registers.
   **Large; 80–130 lines.** Extend the current unnamed-register and operator-prefix state before macros or Visual Block. Specify numbered/small-delete/clipboard registers only if they are actually wanted; a compact first phase could support named character registers plus the existing unnamed register.
1. Add Visual Block mode via Ctrl-V; see `proposals/block-select.md`.
   **Large; 80–120 lines for highlighting plus delete/yank, 180–280 for broader editing.** Implement after register semantics are settled. Short-line padding, tabs, insert/change behavior, and numeric operations remain design blockers.
1. Add configurable keymaps.
   **Large and architecturally broad; likely 120+ lines.** Define scope before implementation: remap versus recursive mapping, mode-specific maps, multi-key timeout, config syntax, and interaction with counts/operator-pending. Keep this below editor-state and command dispatch rather than introducing a plugin system.
1. Add `kjk` as an Insert-mode Esc alias.
   **Small alone, but implement after the keymap decision.** A hard-coded sequence requires buffering/rollback semantics and could conflict with later configurable mappings; preferably express it through the chosen mapping mechanism.
1. Add macros.
   **Largest dependency item; likely 120–200 lines.** Do after named registers and the keymap decision. Reuse the existing dot/input replay path where practical, but specify recording registers, recursion, counts, cancellation, and replay of prompts or subprocess commands.

### Completed
1. Add `<space>(` / `{` / `[` / `"` / `'` surround operators for motions and text objects, with counts, inline linewise ranges, atomic undo, dot repeat, and unchanged registers.
1. Keep excess Backspaces at the start of the `:` prompt in Command mode so they cannot reach Normal-mode buffer deletion.
1. Install the optional diagnostic producer beside `vig` as `vig-diag` and normalize Bash `path: line N:` errors into absolute quickfix locations.
1. Centralize recovery and manual-save versions under `protectdir=auto|file|PATH` using basename/path-hash identities without path metadata; keep autosave writing the original and fall back adjacent only when automatic XDG storage is unavailable.
1. Filter `:` command history by the typed prefix while preserving and restoring the unsubmitted draft.
1. Share filter/substitute range parsing with absolute, `.`, `$`, and current-line-relative `+N`/`-N` endpoints.
1. Mark files with no write mode bits as `[RO]`, allow in-memory editing, and warn once per buffer on the first mutation.
1. Add `g0`, `g^`, and `g$` wrapped-row motions in Normal, Visual, and operator-pending modes; defer special `I`/`A` behavior.
1. Remove no-op undo boundaries without clearing redo history, including empty Insert sessions, unchanged case/comment transforms, unindented dedents, empty character/line ranges, identity filters, and ineffective single-key edits.
1. Complete the state-coupling refactor: declarative `:set` option metadata, timer-only dirty callbacks with explicit recovery cleanup, and property-based per-buffer state with no save/load mirrors; see `proposals/state-coupling-refactor.md`. Startup-sequence restructuring remains deferred until a concrete failure motivates it.
1. Add per-buffer `gv` restoration of the last characterwise or linewise Visual selection, including clamping after mutations.
1. Add `>` / `<` indentation to both Visual modes and complete the existing Normal operator forms for motions and text objects.
1. Fix operator motion boundaries: `cw`/`dw`/yank/case operations consume the final word through one-past-EOL, while failed motions cancel without edits, Insert entry, snapshots, or redo loss.
1. Make search non-case-sensitive if search terms are all-lower-case (and no regexp chars?)
1. Proposal: Explicit `<space>p` / `<space>P` system-clipboard import using optional platform readers; see `proposals/system-clipboard-paste.md`. Estimated 35–50 runtime lines; OSC 52 readback is explicitly excluded.
1. Proposal: Optional `mouse=off|scroll|visual` SGR mouse support in two phases; see `proposals/mouse-support.md`. Wheel-only estimate 25–45 runtime lines; robust scrolling plus drag Visual selection estimate 90–140 lines.
1. Add concise Python-regex, smart-case, replacement-group, and alternate substitute-delimiter tips to `vighelp`, including the delimiter-escaping limitation.
1. Make the launcher prioritize its adjacent installed package without changing cwd, preventing a source checkout in cwd from shadowing a stamped installation.
1. Audit `example-config` against every runtime default, expand `vighelp` across all ex-command and option families, and add regression checks plus standing maintenance guidance.
1. Add main-loop deadline autosave for dirty named buffers with configurable idle delay, per-buffer scheduling across switches, explicit-write cancellation, one-shot error reporting, and no manual-version rotation.
1. Add `saveversions=N` retained prior-disk versions for changed explicit writes, using ignored adjacent `.vigor-bak.<basename>.N` files; block writes when promised preservation fails.
1. Add global `autodetect` / `noautodetect` policy for newly opened buffers, automatic `.md`/`.markdown` presentation, startup config support, and explicit current-buffer redetection through `:ft auto`.
1. Add per-buffer `:filetype` / `:ft` reporting and overrides for auto, text, Bash, C, C++, Python, and Markdown, with persistence across switches and reloads.
1. Detect direct, `env`, and `env -S` Bash/sh shebangs for highlighting named extensionless files while keeping recognized extensions authoritative.
1. Highlight supported Bash, C, C++, and Python Markdown fences according to their information strings while keeping unknown fences separate from Markdown prose styling.
1. Expand line-local Bash, C, C++, and Python highlighting with keywords, numbers, types, constants, definitions, functions, variables, decorators, and preprocessors; keep named ANSI colors separate from semantic color maps.
1. Collapse Markdown fence-marker rows from `:md` layout while preserving source line numbers, hidden-row search/edit positions, wrapping, scrolling, and mouse mapping.
1. Add `mouse=visual` characterwise drag selection: click without drag positions, drag enters Visual, reverse drags normalize, and release leaves selection active without yanking.
1. Add mouse cursor positioning for `mouse=cursor|visual`, sharing `ViewportLayout` mapping across wrapping, gutters, and prompt/editing modes; ignore status/message clicks.
1. Add live `/` and `?` search preview without cursor movement; Esc clears it, invalid regexes are quiet, `hlsearch` controls persistence, and the cursor match has a distinct style.
1. Add Phase 1 SGR mouse support with `mouse=off|scroll|cursor|visual`; wheel events scroll three display rows in every mode, and reporting restores across terminal handoffs.
1. Add `:set wrapcol=N`; nonzero values cap wrap width in display columns and `0` follows terminal content width.
1. Make `Y` behave as `y$`, and use case-insensitive matching for lowercase literal and lowercase word searches while keeping capitalized or regex searches case-sensitive.
1. Replace the inherited Nvim/Vim tutor with an exercise-driven tutorial limited to vigor's implemented behavior and deliberate divergences.
1. Show the acted-on quickfix line in the message bar after opening its location.
1. Use one global invocation/current working directory for relative paths; add `:cd`, `:cdb`, and `:pwd`, and preserve each quickfix producer cwd.
1. Extend `scripts/vig-diagnostics` with `--cwd` and absolute normalized diagnostic paths.
1. Add optional `scripts/vig-diagnostics` producer for GCC/Clang locations and Python traceback frames.
1. Add `:qf !<cmd>` and configurable `makeprg` / `:make [args]` using the external diagnostic-producer protocol documented in `proposals/build-diagnostics-proposal.md`.
1. Add `<space>d` as the protected current-buffer delete shortcut.
1. Add `<space>w` to toggle wrap and `<space>j` / `<space>k` to open the next/previous remembered quickfix item without wrapping.
1. Add semantic version and stampable commit/date identification to the splash footer; `scripts/install` stamps installed copies while source checkouts show `development`.
Render tabs at four-column stops and use display-column mapping for cursor placement, wrapping, scrolling, highlighting, and vertical motion.
On invocation, open the first directory argument as an `:edit` filename-completion menu without a splash, ignore later directories, and retain all file arguments as buffers when completion is cancelled.
Add `Ctrl-E` / `Ctrl-Y` counted viewport scrolling by display rows, with wrapped-row position stored per buffer.
In Normal mode, execute a recognized one-key Space command; otherwise treat Space as a no-op and dispatch the following key normally.
Center successful search results vertically when file boundaries permit.
Fix wrapped rendering and `wrapmove` after narrow pane resizes: preserve full-width boundary characters, keep oversized wrapped lines scrollable, give exact-width logical lines a consistent one-past-EOL display row, and make `j`/`k` cross display rows symmetrically while preserving display column.
Add `*`, `#`, `g*`, and `g#` word-under-cursor searches. `*`/`#` search whole words forward/backward; `g*`/`g#` search partial matches forward/backward, using existing search state and repeat commands.
Add configurable active search-regex highlighting with `:set hlsearch` / `:set nohlsearch` and config-file support through the existing `:set` path. Default is off; current cursor match has no distinct styling; failed searches keep the previous active pattern.
Add `:[range]!cmd` to pipe lines through shell commands in-place and `:[range]!!cmd` / `:!!cmd` to open filter output in a new buffer.
Fix normal/operator-pending `f`/`t`/`F`/`T` so digit targets like `f3` are accepted instead of being parsed as a count.
Merge the old `backlog` file into `todo.md` and retire the separate backlog file.
Add `:rgf [path]` directory argument completion.
Add Shift-Tab reverse filename-completion selection.
Wrap Tab/Shift-Tab filename-completion selection.
Replace an untouched initial unnamed buffer when opening or creating a buffer.
Add `:help` to open executable-directory `vighelp`, with terse two-column guidance.
Add `:rgf [path]`: an fzf-backed live ripgrep picker with Enter selecting filtered rows into quickfix; document quickfix navigation.
Add automatic line-local regex syntax highlighting for comments and strings in Python, C, and Bash files.
Update project documentation counts and phase references.
Consolidate pending-input cancellation.
Speed up the PTY test harness without weakening escape-sequence handling.
Extract shared command/search prompt editing and centralize case-operation lookup.
Show a cursor on `:`, `/`, and `?` command lines.
Add one space around completion dialog filenames between text and frame.
Make `:rg` report no hits without opening quickfix.
Add `~`, `g~`, `gU`, and `gu` case commands.
Add forward/backspace/delete editing of `:`, `/`, and `?` prompts.
Add sticky cursor-column tracking for vertical navigation.
Filename completion shows a vertical match menu for multiple matches, supports selection with Up/Down/Tab, Enter accepts the selected filename, Esc hides the menu, and typing updates the filter.
Add tab filename complete for appropriate : and :! operations. Support no-path (pwd), absolute, and relative path cases.
Add history for : / ? operations. / and ? share a history list. Up-down arrow scrolls through list, enter accepts, esc cancels.
:e! command rereads the current buffer from disk. If no file name show an error.
Add <del> as alias for x in normal mode.
/ and ? s searches find second hits on the same line in the direction of search.
Add delcopy/nodelcopy (delcopy == default == vim behavior) option that changes semantics of normal d<motion> operator and adds yd<motion>.  When delcopy is set, behavior is vim-like.  When nodelcopy is set, d<motion> deletes without modifying the default copy register, and yd<motion> deletes and copies.
Add wrapmove option that modifies line up/down movement to move up and down by displayed rows rather than text lines.
When writing file, if directory doesn't exist, prompt to create
:e! resets the file to its state when last saved or first opened
When yanking, highlight the yanked text for about 300ms.
When there is room, and relativenumber is active, shift the line number of the cursor row left 1 character.
Make tab respect tab columns rather than adding 4 spaces from current cursor pos
Add <ctrl>-c <ctrl>-c in normal mode as an alias for :qall and <ctrl>-c q as an alias for :qall!
Add startup config files with default settings (`~/.vigrc`, `$XDG_CONFIG_HOME/vig/config`, or `VIG_CONFIG`)
Fix Ctrl-Z suspension so vig actually stops and returns control to the shell
Ctrl-C cancels pending editor state and returns to Normal mode
:e <directory> reports an error instead of crashing
dw at end of line no longer merges next line from last-character or one-past-EOL positions
On ^z, move terminal cursor to the bottom of the screen before suspending
Improve documents with r/s count behavior, Ctrl-Z resume behavior, and one-line shell output scope
:! ls produces compact message-bar output instead of raw newlines without carriage returns
Add normal mode backspace that deletes char to left of cursor and moves cursor
Add normal mode r,s commands
Allow :!<command> with no space between ! and <command>
Allow ^z backgrounding of app
Fix dw at end of line so it does not merge lines
