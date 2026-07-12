# vig — Command Reference

vig is a compact, single-file, vi-style terminal editor. Runtime code lives in `vig.py`, uses Python stdlib only, and talks to the terminal with raw ANSI escape codes rather than curses.

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
| `yd` + motion | delete and yank over motion (useful with `:set nodelcopy`) |
| `dd` `yy` `cc` | linewise delete / yank / change |
| `D` `C` | delete / change to end of line |
| `Y` | yank entire line |
| `x` / Delete | delete char at cursor |
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
| `Ctrl-C Ctrl-C` | quit all buffers (`:qall`) |
| `Ctrl-C q` | force quit all buffers (`:qall!`) |
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
| `/pattern` | search forward (Python regular expression), including later hits on the current line |
| `?pattern` | search backward, including earlier hits on the current line |
| `n` / `N` | next / previous match |
| `:[range]s/pat/repl/[g]` | substitute (any delimiter; range: `%`, `N,M`) |

## Command/Search Input
- In `:` command mode, Up/Down browse command history.
- In `/` and `?` search prompts, Up/Down browse shared search history.
- Tab completes path arguments for `:e`, `:w`, `:read`, and shell paths in `:!` commands.
- A single completion fills the command line. Multiple completions show a vertical menu above the status bar; Up/Down moves the reverse-video selection, Tab advances it, Enter copies the selected filename into the command line, and Esc hides the menu.

## Ex Commands
| Command | Action |
|---------|--------|
| `:w` [path] | write file; prompts before creating missing parent directories |
| `:q` | quit (closes buffer if >1, else exits) |
| `:q!` | force quit |
| `:wq` | write and quit |
| `:qa` / `:qall` | quit all buffers |
| `:qa!` / `:qall!` | force quit all |
| `:e <path>` | open file in new buffer |
| `:e!` | reload current buffer from disk, discarding unsaved changes; errors if unnamed |
| `:new` | create empty buffer |
| `:n` / `:next` / `:bn` | next buffer |
| `:p` / `:prev` / `:bp` | previous buffer |
| `:ls` | list buffers |
| `:k` / `:bdelete` | close buffer (`:k!` / `:bdelete!` to force) |
| `:rg <pattern> [path]` | run `rg -n --column` into quickfix buffer |
| `:read <file>` | insert file contents below cursor |
| `:r !<cmd>` | insert command output below cursor |
| `:! <cmd>` / `:!<cmd>` | run shell command and show one-line truncated output in message bar |
| `:set wrap` / `nowrap` | toggle line wrapping |
| `:set wrapmove` / `nowrapmove` | with wrap on, make `j`/`k`/Up/Down move by displayed rows |
| `:set number` / `nonumber` | toggle absolute line numbers |
| `:set relativenumber` / `norelativenumber` | toggle relative line numbers |
| `:set autoindent` / `noautoindent` | toggle autoindent |
| `:set comment=<str>` | set comment prefix (default `#`) |
| `:set scrolloff=<N>` | keep N-line vertical margin around cursor |
| `:set clipboard=osc52|auto|off` | clipboard copy mode (current default `osc52`) |
| `:set yankflash=<ms>` | yank highlight duration in milliseconds (`0` disables) |
| `:set delcopy` / `nodelcopy` | choose whether `d` updates the unnamed register; `yd` always does |
| `:set rghidden` / `norghidden` | add `-H` to `:rg` command when set |

Path semantics: `:e`/`:w` expand `~`; relative paths resolve from current buffer directory. If `:w` targets a missing parent directory, vig asks `Create directory ...? (y/n)` before calling `mkdir -p` and writing.

## Multi-Buffer
| Key / Command | Action |
|---------------|--------|
| `:n` `:bn` | next buffer |
| `:p` `:bp` | previous buffer |
| `:ls` | list all buffers |
| `<space>k` | close current buffer |
| `<space>n` / `<space>N` | next / previous buffer |
| `<space>c` | switch to quickfix buffer, if any |
| `<space>o` | open `file:line:column:` location under cursor |
| Status bar `[N/M]` | shown when >1 buffer open |

## Insert Mode
| Key | Action |
|-----|--------|
| Printable chars | insert at cursor |
| Bracketed paste | insert pasted text literally; tabs/newlines are not treated as typed keys |
| Tab | insert spaces to the next 4-column tab stop |
| Enter | split line (copies indent if autoindent) |
| Backspace | delete char / join with previous line |
| Delete | delete char at cursor |
| Arrow keys | move cursor |
| Home / End | start / end of line |
| Escape | return to Normal mode |

## Startup Config
- Unless `VIG_NO_CONFIG` is set, vig reads `~/.vigrc` then `$XDG_CONFIG_HOME/vig/config`.
- `VIG_CONFIG=/path/to/file` reads only that file.
- Lines are simple set-style options: `set number`, `relativenumber`, `scrolloff=3`, etc.
- Blank lines and lines starting with `#` are ignored.

## Terminal Features
- Cursor shape: block (Normal/Visual), bar (Insert)
- Single `write()` render — no flicker
- The visible window horizontally scrolls in nowrap mode to keep the cursor visible
- SIGWINCH-aware terminal resize
- Ctrl-Z moves the terminal cursor to the bottom line, suspends vig, and restores raw mode when foregrounded
- Ctrl-C cancels pending input/state and returns to Normal mode; Normal-mode `Ctrl-C Ctrl-C` = `:qall`, `Ctrl-C q` = `:qall!`
- Undo stack: 100 snapshot limit
