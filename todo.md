** Sticky notes
1) Do not support legacy configurations, file formats, or removed behaviors.  Remove any dead code due to changes.  There are no existing implementations or configuration files. 
2) Review proposed changes for estimated change size.  If the net increase in number of lines of code in the runtime for an individual item exceeds about 50, notify me before implementation.
3) If minor changes to proposed functionality would result in significant code savings, bring that to light before implementation.

** Do

** On hold
1. Make search non-case-sensitive if search terms are all-lower-case (and no regexp chars?)
1. Add \v search modifier
1. Add `.`, `+<number>`, and `-<number>` as relative line specifiers for range commands. 
1. Warning message in status when first edit is made to a R/O file. Indicate R/O file in status bar.
1. Proposal: Visual Block mode via Ctrl-V, with rectangle selection and d/y/I/Ctrl-A/g Ctrl-A. Requires decisions on short-line padding, blockwise register/paste semantics, numeric scope/format/progression, tabs, and whether first scope excludes A/c/r/paste/case operators. Estimated 150–250 net lines plus tests.
1. Proposal: Pipe stdin text into the initial unnamed buffer, then use `/dev/tty` for interactive terminal input; define non-interactive fallback behavior. Estimated 60–90 net lines.
1. marks
1. configurable keymaps
1. autosave
1. filtering on partially typed command entry for : command history
1. add 'kjk' alias for <esc> in insert mode(s)

** Done
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
