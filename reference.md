# ved — Command Reference

ved is a compact, single-file, vi-style terminal editor. Runtime code lives in `ved.py`, uses Python stdlib only, and talks to the terminal with raw ANSI escape codes rather than curses.

**Modes:** NORMAL, INSERT, VISUAL, VISUAL LINE, COMMAND, SEARCH

## Movement
| Key | Action |
|-----|--------|
| `h` `j` `k` `l` | left / down / up / right |
| `w` `W` `b` `B` `e` `E` | word motions (small / big WORD) |
| `0` | column 0 |
| `^` | first non-blank character |
| `$` | end of line |
| `gg` / `G` | first / last line (with count: line N) |
| `f{c}` `t{c}` `F{c}` `T{c}` | find char forward / backward (`t`/`T` stop before) |
| `;` `,` | repeat / reverse last find-char |
| `%` | jump to matching bracket `()` `{}` `[]` |
| `Ctrl-D` / `Ctrl-U` | half-page down / up |
| Arrow keys | work in Normal, Insert, Visual |
| Home / End | start / end of line (Normal & Insert) |

## Operators + Motions / Text Objects
| Key | Action |
|-----|--------|
| `d` `y` `c` + motion | delete / yank / change over motion |
| `dd` `yy` `cc` | linewise delete / yank / change |
| `D` `C` | delete / change to end of line |
| `Y` | yank entire line |
| `x` | delete char at cursor |
| `X` / Backspace | delete char before cursor |
| `r{c}` | replace char(s) under cursor with `c` (count: N chars) |
| `s` | substitute char(s): delete and enter Insert (count: N chars) |
| `J` | join current line with next |
| Count prefix | `3dd`, `5j`, `2>>`, `2dw`, etc. |

**Text objects** (used with `d`/`y`/`c` in operator-pending):
| Object | Scope |
|--------|-------|
| `iw` `aw` `iW` `aW` | inner / around word |
| `i(` `a(` `i[` `a[` `i{` `a{` | inner / around brackets |
| `i"` `a"` `i'` `a'` | inner / around quotes |

## Editing
| Key | Action |
|-----|--------|
| `i` `I` `a` `A` | enter Insert mode (at cursor / first col / after cursor / end of line) |
| `o` `O` | open line below / above (copies indent if autoindent) |
| `p` `P` | paste after / before cursor |
| `>>` `<<` | indent / dedent by 4 spaces |
| `gcc` | toggle line comment (count: N lines) |
| `u` | undo |
| `Ctrl-R` | redo |
| `.` | dot-repeat last change |

## Visual Mode
| Key | Action |
|-----|--------|
| `v` | enter character-wise visual |
| `V` | enter line-wise visual |
| All motions | `h` `j` `k` `l` `w` `b` `e` `W` `B` `E` `0` `$` `^` `G` `gg` |
| `d` `x` | delete selection |
| `y` | yank selection |
| `c` | change selection |
| `gc` | toggle comment on selected lines |

## Search & Replace
| Key / Command | Action |
|---------------|--------|
| `/pattern` | search forward (Python regular expression) |
| `?pattern` | search backward |
| `n` / `N` | next / previous match |
| `:[range]s/pat/repl/[g]` | substitute (any delimiter; range: `%`, `N,M`) |

## Ex Commands
| Command | Action |
|---------|--------|
| `:w` [path] | write file |
| `:q` | quit (closes buffer if >1, else exits) |
| `:q!` | force quit |
| `:wq` | write and quit |
| `:qa` / `:qall` | quit all buffers |
| `:qa!` / `:qall!` | force quit all |
| `:e <path>` | open file in new buffer |
| `:new` | create empty buffer |
| `:n` / `:next` / `:bn` | next buffer |
| `:p` / `:prev` / `:bp` | previous buffer |
| `:ls` | list buffers |
| `:k` / `:bdelete` | close buffer (`:k!` / `:bdelete!` to force) |
| `:read <file>` | insert file contents below cursor |
| `:r !<cmd>` | insert command output below cursor |
| `:! <cmd>` / `:!<cmd>` | run shell command and show one-line truncated output in message bar |
| `:set wrap` / `nowrap` | toggle line wrapping |
| `:set number` / `nonumber` | toggle absolute line numbers |
| `:set relativenumber` / `norelativenumber` | toggle relative line numbers |
| `:set autoindent` / `noautoindent` | toggle autoindent |
| `:set comment=<str>` | set comment prefix (default `#`) |
| `:set scrolloff=<N>` | keep N-line vertical margin around cursor |
| `:set clipboard=osc52|auto|off` | clipboard copy mode (current default `osc52`) |

Path semantics: `:e`/`:w` expand `~`; relative paths resolve from current buffer directory.

## Multi-Buffer
| Key / Command | Action |
|---------------|--------|
| `:n` `:bn` | next buffer |
| `:p` `:bp` | previous buffer |
| `:ls` | list all buffers |
| `<space>k` | close current buffer |
| Status bar `[N/M]` | shown when >1 buffer open |

## Insert Mode
| Key | Action |
|-----|--------|
| Printable chars | insert at cursor |
| Tab | insert 4 spaces |
| Enter | split line (copies indent if autoindent) |
| Backspace | delete char / join with previous line |
| Delete | delete char at cursor |
| Arrow keys | move cursor |
| Home / End | start / end of line |
| Escape | return to Normal mode |

## Terminal Features
- Cursor shape: block (Normal/Visual), bar (Insert)
- Single `write()` render — no flicker
- SIGWINCH-aware terminal resize
- Ctrl-Z moves the terminal cursor to the bottom line, suspends ved, and restores raw mode when foregrounded
- Undo stack: 100 snapshot limit
