# vig — Command Reference

vig is a compact, single-file, vi-style terminal editor. Runtime code lives in `vig.py`, uses Python stdlib only, and talks to the terminal with raw ANSI escape codes rather than curses.

The splash footer identifies the source as `v0.1.0 · development`; `scripts/install` stamps installed copies with the Git commit and commit date.

On invocation, file arguments open as buffers. The first existing directory argument opens an `:edit` filename-completion menu without the splash; later directory arguments are ignored. Esc cancels the directory item while retaining any file buffers.

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
| `Ctrl-D` / `Ctrl-U` | move cursor half-page down / up |
| `Ctrl-E` / `Ctrl-Y` | scroll viewport one display row down / up (count accepted) |
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

## Syntax Highlighting

Recognized `.py`, `.c`, `.h`, `.sh`, and `.bash` files automatically highlight line-local strings in yellow and comments in green. Multiline strings, block comments spanning lines, and heredocs are intentionally not tracked.

## Search & Replace
| Key / Command | Action |
|---------------|--------|
| `/pattern` | search forward (Python regular expression), including later hits on the current line |
| `?pattern` | search backward, including earlier hits on the current line |
| `*` / `#` | search the whole word under cursor forward / backward |
| `g*` / `g#` | search the word under cursor as a partial match forward / backward |
| `n` / `N` | next / previous match; successful searches center when practical |
| `:[range]s/pat/repl/[g]` | substitute (any delimiter; range: `%`, `N,M`) |

## Command/Search Input
- In `:` command mode, Up/Down browse command history.
- In `/` and `?` search prompts, Up/Down browse shared search history.
- Tab completes path arguments for `:e`, `:w`, `:read`, `:rgf`, and shell paths in `:!` commands.
- A single completion fills the command line. Multiple completions show a centered rounded-border menu; Up/Down moves the reverse-video selection, Tab/Shift-Tab advance/reverse it with wrapping, Enter copies the selected filename into the command line, and Esc hides the menu.

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
| `:help` | open `vighelp` beside the vigor executable |
| `:n` / `:next` / `:bn` | next buffer |
| `:p` / `:prev` / `:bp` | previous buffer |
| `:ls` | list buffers |
| `:k` / `:bdelete` | close buffer (`:k!` / `:bdelete!` to force) |
| `:make [args]` | run configured `makeprg` and capture merged output in quickfix |
| `:qf !<cmd>` | run a generic diagnostic producer and capture output in quickfix |
| `:rg <pattern> [path]` | run `rg -n --column` into quickfix buffer |
| `:rgf [path]` | open optional `fzf` live ripgrep picker; Enter sends all filtered rows to quickfix |
| `:read <file>` | insert file contents below cursor |
| `:r !<cmd>` | insert command output below cursor |
| `:! <cmd>` / `:!<cmd>` | run shell command and show one-line truncated output in message bar |
| `:[range]!<cmd>` | pipe lines to shell command stdin and replace the range with stdout (`%`, `.`, `$`, `N,M`) |
| `:[range]!!<cmd>` / `:!!<cmd>` | pipe range, or whole buffer without a range, to shell command and open stdout in a new buffer |
| `:set wrap` / `nowrap` | toggle line wrapping |
| `:set wrapmove` / `nowrapmove` | with wrap on, make `j`/`k`/Up/Down move by displayed rows |
| `:set number` / `nonumber` | toggle absolute line numbers |
| `:set relativenumber` / `norelativenumber` | toggle relative line numbers |
| `:set autoindent` / `noautoindent` | toggle autoindent |
| `:set comment=<str>` | set comment prefix (default `#`) |
| `:set scrolloff=<N>` | keep N-line vertical margin around cursor |
| `:set clipboard=osc52|auto|off` | clipboard copy mode (current default `auto`) |
| `:set yankflash=<ms>` | yank highlight duration in milliseconds (`0` disables) |
| `:set delcopy` / `nodelcopy` | choose whether `d` updates the unnamed register; `yd` always does |
| `:set rghidden` / `norghidden` | add `-H` to `:rg` command when set |
| `:set hlsearch` / `nohlsearch` | highlight active search-regex matches (default off) |
| `:set makeprg=<cmd>` | shell command used by `:make` (default `make`) |

Line numbers use a five-column field that expands for files over 99,999 lines, followed by one separator space. Absolute numbers are right-aligned. With `relativenumber`, the cursor row shows its absolute number flush left and other rows show right-aligned relative distances.

Path semantics: `:e`/`:w` expand `~`; relative paths resolve from current buffer directory. If `:w` targets a missing parent directory, vig asks `Create directory ...? (y/n)` before calling `mkdir -p` and writing.

## Build Diagnostics

`:qf !<cmd>` and `:make` retain ordinary output for context while quickfix navigation skips non-location rows. Navigable producers emit `path:line:column: message`; `path:line: message` is normalized to column 1. ANSI escapes are stripped, nonzero output is retained, and silent successful builds leave the current buffer active.

The optional producer wrapper normalizes GCC/Clang output and Python traceback frames while preserving context and command exit status:

```vim
:set makeprg=./scripts/vig-diagnostics make
:make clean
:qf !./scripts/vig-diagnostics python3 -m pytest
```

It executes arguments directly rather than through a shell; use `sh -c '...'` explicitly for pipelines or redirection. See `build-diagnostics-proposal.md` for the protocol.

## Multi-Buffer
| Key / Command | Action |
|---------------|--------|
| `:n` `:bn` | next buffer |
| `:p` `:bp` | previous buffer |
| `:ls` | list all buffers |
| `<space>d` | close current buffer, refusing dirty or last buffers |
| `<space>w` | toggle `wrap` / `nowrap` |
| `<space>n` / `<space>N` | next / previous buffer |
| `<space>c` | switch to quickfix buffer, if any |
| `<space>o` | open `file:line:column:` location under cursor |
| `<space>j` / `<space>k` | open next / previous remembered quickfix item without wrapping |

For an unrecognized one-key Space combination, Space is a no-op and the following key executes normally.

Use `j`/`k` or arrow keys in the quickfix buffer to choose a row, then `<space>o` to open it. `<space>j`/`<space>k` navigate and open remembered quickfix items from either quickfix or source buffers. `<space>c` returns to quickfix.
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
- Startup first renders the editor, then overlays a centered rounded logo frame. Input dismisses it and still executes; command-line file startup also dismisses it after one second.
- Cursor shape: block (Normal/Visual), bar (Insert)
- Single `write()` render — no flicker
- Existing tab characters render as spaces to four-column stops; cursor movement, wrapping, scrolling, and highlighting use those display columns
- The visible window horizontally scrolls in nowrap mode to keep the cursor visible
- SIGWINCH-aware terminal resize
- Ctrl-Z moves the terminal cursor to the bottom line, suspends vig, and restores raw mode when foregrounded
- Ctrl-C cancels pending input/state and returns to Normal mode; Normal-mode `Ctrl-C Ctrl-C` = `:qall`, `Ctrl-C q` = `:qall!`
- Undo stack: 100 snapshot limit
