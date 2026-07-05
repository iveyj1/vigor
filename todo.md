** Do

** Done
1) Fix Ctrl-Z suspension so ved actually stops and returns control to the shell
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
0.5) config file
0.75) configurable keymaps
1) Leverage shell utilities. e.g. support ripgrep better
2) simple quickfix list
3) configurable keymaps, settings (e.g. set number)
4) :g commands 
6) simple syntax highlighting mechanism, C, Python, and Bash to start.  If too much added code, consider comment-and-string only.
7) autosave
11) tab complete for filenames in : and :! commands
12) : command history
13) filtering on partially typed command entry for : command history
7) add 'kjk' alias for <esc> in insert mode(s)
