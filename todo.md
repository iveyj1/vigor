** Sticky notes
1) Do not support legacy configurations, file formats, or removed behaviors.  Remove any dead code due to changes.  There are no existing implementations or configuration files. 
2) Review proposed changes for estimated change size.  If the net increase in number of lines of code for an individual item exceeds about 50, notify me before implementation.

** Do

** On hold
** Done
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
3) add column-select mode. From normal mode <ctrl>-v to enter, esc, y, or d, to exit.  y copies, d deletes  (interaction with yd?) moving non-deleted text on affected lines to the left.  
3) configurable keymaps
4) :g commands 
6) simple syntax highlighting mechanism, C, Python, and Bash to start.  If too much added code, consider comment-and-string only.
7) autosave
11) tab complete for filenames in : and :! commands
12) : command history
13) filtering on partially typed command entry for : command history
7) add 'kjk' alias for <esc> in insert mode(s)
