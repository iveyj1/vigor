# ved — Command Reference

**Modes:** NORMAL, INSERT, VISUAL, VISUAL LINE, COMMAND, SEARCH

**Movement**
- `h j k l` — left/down/up/right
- `w W b B e E` — word motions (small/big)
- `0` — column 0
- `gg` / `G` — first/last line (or line N with count)
- `f t F T` — find char forward/backward (t stops short)
- `;` `,` — repeat/reverse last find
- `%` — jump to matching bracket `(){}[]`
- Arrow keys — work in Normal, Insert, Visual

**Operators + Motions/Objects**
- `d` `y` `c` + motion — delete/yank/change
- `dd` `yy` `cc` — linewise
- `D` `C` — to end of line; `Y` — yank line
- Text objects: `iw` `iW` `aw` `aW` — word
- Text objects: `i(` `a(` `i[` `a[` `i{` `a{` — brackets
- Text objects: `i"` `a"` `i'` `a'` — quotes

**Editing**
- `i I a A` — enter insert
- `o` `O` — open line below/above
- `p` `P` — paste after/before
- `>>` `<<` — indent/dedent 4 spaces
- `gcc` — toggle line comment (`gc` in visual)
- `u` / `Ctrl-R` — undo/redo
- `.` — dot repeat last change
- Count prefix on all: `3dd`, `5j`, `2>>`, etc.

**Visual Mode**
- `v` — character, `V` — line
- `d` `x` `y` `c` — operate on selection
- `gc` — toggle comment on selection

**Search**
- `/pattern` — search forward
- `?pattern` — search backward
- `n` `N` — next/previous match

**Commands**
- `:w` `:q` `:wq` `:q!` — write/quit
- `:e <path>` — edit file; `:new` — new buffer
- `:%s/pat/repl/g` — substitute (any delimiter)
- `:read <file>` — insert file below cursor
- `:read !<cmd>` — insert command output
- `:! <cmd>` — run shell command
- `:set wrap|nowrap|number|nonumber|relativenumber|norelativenumber`
- `:set autoindent|noautoindent|comment=<char>`

**Insert Mode**
- Printable chars insert; Enter splits (with autoindent)
- Backspace deletes/joins; arrows move; Esc exits
