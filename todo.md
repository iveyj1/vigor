** Do
1) :! ls produces output where next line starts at column where last ended, i.e. no carriage returns
2) Add normal mode backspace that deletes char to left of cursor and moves cursor
3) add normal mode r,s commands
4) allow :!<command> with no space between ! and <command>
5) allow ^z backgrounding of app
6) fix dw at end of line (currently merges lines)

** Done


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
