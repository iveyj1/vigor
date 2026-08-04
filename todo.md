** Sticky notes
1) Do not support legacy configurations, file formats, or removed behaviors.  Remove any dead code due to changes.  There are no existing implementations or configuration files. 
2) Review proposed changes for estimated change size.  If the net increase in number of lines of code in the runtime for an individual item exceeds about 50, notify me before implementation.
3) If minor changes to proposed functionality would result in significant code savings, bring that to light before implementation.

** Do
1) When inserting to the left of a tab, then typing, odd behavior results
2) Warning message in status when first edit is made to a R/O file. Indicate R/O file in status bar.
** On hold
1) Add a blank space left right top bottom betweem the text and the frame
2. Proposal: Visual Block mode via Ctrl-V, with rectangle selection and d/y/I/Ctrl-A/g Ctrl-A. Requires decisions on short-line padding, blockwise register/paste semantics, numeric scope/format/progression, tabs, and whether first scope excludes A/c/r/paste/case operators. Estimated 150–250 net lines plus tests.
4. Proposal: Pipe stdin text into the initial unnamed buffer, then use `/dev/tty` for interactive terminal input; define non-interactive fallback behavior. Estimated 60–90 net lines.
** Done
1. On invocation, open the first directory argument as an `:edit` filename-completion menu without a splash, ignore later directories, and retain all file arguments as buffers when completion is cancelled.
1. Add `Ctrl-E` / `Ctrl-Y` counted viewport scrolling by display rows, with wrapped-row position stored per buffer.
2. In Normal mode, execute a recognized one-key Space command; otherwise treat Space as a no-op and dispatch the following key normally.
3. Center successful search results vertically when file boundaries permit.
1. Fix wrapped rendering and `wrapmove` after narrow pane resizes: preserve full-width boundary characters, keep oversized wrapped lines scrollable, give exact-width logical lines a consistent one-past-EOL display row, and make `j`/`k` cross display rows symmetrically while preserving display column.
1. Add `*`, `#`, `g*`, and `g#` word-under-cursor searches. `*`/`#` search whole words forward/backward; `g*`/`g#` search partial matches forward/backward, using existing search state and repeat commands.
2. Add configurable active search-regex highlighting with `:set hlsearch` / `:set nohlsearch` and config-file support through the existing `:set` path. Default is off; current cursor match has no distinct styling; failed searches keep the previous active pattern.
1. Add `:[range]!cmd` to pipe lines through shell commands in-place and `:[range]!!cmd` / `:!!cmd` to open filter output in a new buffer.
1. Fix normal/operator-pending `f`/`t`/`F`/`T` so digit targets like `f3` are accepted instead of being parsed as a count.
2. Merge the old `backlog` file into `todo.md` and retire the separate backlog file.
1. Add `:rgf [path]` directory argument completion.
2. Add Shift-Tab reverse filename-completion selection.
3. Wrap Tab/Shift-Tab filename-completion selection.
1) Replace an untouched initial unnamed buffer when opening or creating a buffer.
1. Add `:help` to open executable-directory `vighelp`, with terse two-column guidance.
2. Add `:rgf [path]`: an fzf-backed live ripgrep picker with Enter selecting filtered rows into quickfix; document quickfix navigation.
3. Add automatic line-local regex syntax highlighting for comments and strings in Python, C, and Bash files.
1. Update project documentation counts and phase references.
2. Consolidate pending-input cancellation.
3. Speed up the PTY test harness without weakening escape-sequence handling.
1. Extract shared command/search prompt editing and centralize case-operation lookup.
1. Show a cursor on `:`, `/`, and `?` command lines.
1. Add one space around completion dialog filenames between text and frame.
2. Make `:rg` report no hits without opening quickfix.
3. Add `~`, `g~`, `gU`, and `gu` case commands.
4. Add forward/backspace/delete editing of `:`, `/`, and `?` prompts.
5. Add sticky cursor-column tracking for vertical navigation.
1) Filename completion shows a vertical match menu for multiple matches, supports selection with Up/Down/Tab, Enter accepts the selected filename, Esc hides the menu, and typing updates the filter.
1) Add tab filename complete for appropriate : and :! operations. Support no-path (pwd), absolute, and relative path cases.
2) Add history for : / ? operations. / and ? share a history list. Up-down arrow scrolls through list, enter accepts, esc cancels.
1) :e! command rereads the current buffer from disk. If no file name show an error.
2) Add <del> as alias for x in normal mode.
1) / and ? s searches find second hits on the same line in the direction of search.
2) Add delcopy/nodelcopy (delcopy == default == vim behavior) option that changes semantics of normal d<motion> operator and adds yd<motion>.  When delcopy is set, behavior is vim-like.  When nodelcopy is set, d<motion> deletes without modifying the default copy register, and yd<motion> deletes and copies.
3) Add wrapmove option that modifies line up/down movement to move up and down by displayed rows rather than text lines.
1) When writing file, if directory doesn't exist, prompt to create
2) :e! resets the file to its state when last saved or first opened
3) When yanking, highlight the yanked text for about 300ms.
4) When there is room, and relativenumber is active, shift the line number of the cursor row left 1 character.
5) Make tab respect tab columns rather than adding 4 spaces from current cursor pos
1) Add <ctrl>-c <ctrl>-c in normal mode as an alias for :qall and <ctrl>-c q as an alias for :qall!
2) Add startup config files with default settings (`~/.vigrc`, `$XDG_CONFIG_HOME/vig/config`, or `VIG_CONFIG`)
1) Fix Ctrl-Z suspension so vig actually stops and returns control to the shell
2) Ctrl-C cancels pending editor state and returns to Normal mode
3) :e <directory> reports an error instead of crashing
4) dw at end of line no longer merges next line from last-character or one-past-EOL positions
5) On ^z, move terminal cursor to the bottom of the screen before suspending
6) Improve documents with r/s count behavior, Ctrl-Z resume behavior, and one-line shell output scope
7) :! ls produces compact message-bar output instead of raw newlines without carriage returns
8) Add normal mode backspace that deletes char to left of cursor and moves cursor
9) Add normal mode r,s commands
10) Allow :!<command> with no space between ! and <command>
11) Allow ^z backgrounding of app
12) Fix dw at end of line so it does not merge lines

** Hold for further definition
1) marks
2) macros
3) configurable keymaps
7) autosave
12) : command history
13) filtering on partially typed command entry for : command history
7) add 'kjk' alias for <esc> in insert mode(s)
