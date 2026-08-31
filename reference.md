# vig — Command Reference

vig is a compact, vi-style terminal editor. Runtime code lives in the `vigor` package, uses Python stdlib only, and talks to the terminal with raw ANSI escape codes rather than curses.

The splash footer identifies the source as `v0.1.0 · development`; `scripts/install` stamps installed copies with the Git commit and commit date. The launcher explicitly prioritizes its adjacent package without changing cwd, so running an installed `vig` from inside a source checkout still uses the stamped installed runtime.

Run `vig tutor` for an exercise-driven introduction. On invocation, file arguments open as buffers. The first existing directory argument opens an `:edit` filename-completion menu without the splash; later directory arguments are ignored. Esc cancels the directory item while retaining any file buffers.

**Modes:** NORMAL, INSERT, VISUAL, VISUAL LINE, COMMAND, SEARCH

### Movement
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

### Operators + Motions / Text Objects
| Key | Action |
|-----|--------|
| `d` `y` `c` + motion | delete / yank / change over motion |
| `yd` + motion | delete and yank over motion (useful with `:set nodelcopy`) |
| `>` `<` + motion or text object | indent / dedent every touched logical line by 4 spaces |
| `dd` `yy` `cc` | linewise delete / yank / change |
| `D` `C` | delete / change to end of line |
| `Y` | yank from cursor to end of logical line |
| `x` / Delete | delete char at cursor |
| `X` / Backspace | delete char before cursor |
| `r{c}` | replace char(s) under cursor with `c` (count: N chars) |
| `s` | substitute char(s): delete and enter Insert (count: N chars) |
| `J` | join current line with next |
| Count prefix | `3dd`, `5j`, `2>>`, `2dw`, etc. |

At the final word in a file, `w`/`W` operator motions extend to one-past-EOL, so `cw`, `dw`, and related operators consume the remainder. A motion that cannot move cancels its operator without editing or entering Insert mode.

**Text objects** (used with `d`/`y`/`c` in operator-pending):
| Object | Scope |
|--------|-------|
| `iw` `aw` `iW` `aW` | inner / around word |
| `i(` `a(` `i[` `a[` `i{` `a{` | inner / around brackets |
| `i"` `a"` `i'` `a'` | inner / around quotes |

### Editing
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

### Visual Mode
| Key | Action |
|-----|--------|
| `v` | enter character-wise visual |
| `V` | enter line-wise visual |
| `gv` | restore the current buffer's last Visual mode, anchor, and endpoint |
| All motions | `h` `j` `k` `l` `w` `b` `e` `W` `B` `E` `0` `$` `^` `G` `gg` |
| `d` `x` | delete selection |
| `y` | yank selection |
| `c` | change selection |
| `gc` | toggle comment on selected lines |
| `>` `<` | indent / dedent every selected logical line, then return to Normal mode |

### Syntax Highlighting

Recognized Python (`.py`), C (`.c`, `.h`), C++ (`.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx`), and Bash (`.sh`, `.bash`) files receive automatic line-local highlighting. A named file without an extension also uses Bash highlighting when its first line selects exact `bash` or `sh` through a direct path, `env`, or `env -S`; recognized extensions remain authoritative. Recognized entities include comments, strings, numbers, keywords, constants, types, definitions, function names, decorators, preprocessor directives, and shell variables where appropriate. Multiline strings, block comments spanning lines, raw strings, and heredocs are intentionally not tracked.

File-type overrides are per-buffer and survive buffer switches and reloads. `text` disables syntax and Markdown presentation; `markdown` enables Markdown presentation, including optional fence collapsing. `:nomd` only changes presentation and does not clear the detected or forced type.

The hard-coded `NAMED_COLORS` palette and semantic `SYNTAX_COLOR_NAMES` / `MARKDOWN_COLOR_NAMES` maps near the top of `vigor/highlight.py` can be edited independently.

### Search & Replace
| Key / Command | Action |
|---------------|--------|
| `/pattern` | search forward (Python regular expression), including later hits on the current line |
| `?pattern` | search backward, including earlier hits on the current line |
| `*` / `#` | search the whole word under cursor forward / backward |
| `g*` / `g#` | search the word under cursor as a partial match forward / backward |
| `n` / `N` | next / previous match; successful searches center when practical |
| `:[range]s/pat/repl/[g]` | substitute (any delimiter; range: `%`, `N,M`) |

### Command/Search Input
- In `:` command mode, Up/Down browse command history.
- In `/` and `?` search prompts, Up/Down browse shared search history.
- Tab completes path arguments for `:e`, `:w`, `:read`, `:rgf`, `:cd`, and shell paths in `:!` commands.
- A single completion fills the command line. Multiple completions show a centered rounded-border menu; Up/Down moves the reverse-video selection, Tab/Shift-Tab advance/reverse it with wrapping, Enter copies the selected filename into the command line, and Esc hides the menu.

Lowercase literal `/` and `?` patterns ignore case. A capital letter or any regex metacharacter makes the search case-sensitive. Lowercase `*`, `#`, `g*`, and `g#` word searches also ignore case. Repeats and `hlsearch` retain the active search's case behavior.

While a `/` or `?` prompt is being typed, visible matches preview without moving the cursor. Esc clears the preview, and incomplete regular expressions quietly show no matches. After Enter, `hlsearch` controls persistence. Ordinary matches are yellow; the match containing the cursor is magenta.

### Ex Commands
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
| `:md` / `:markdown` | toggle non-destructive Markdown presentation for the current buffer |
| `:nomd` | return the current buffer to literal source display |
| `:filetype` / `:ft` | report the current effective file type and whether it is automatic or forced |
| `:filetype TYPE` / `:ft TYPE` | force `text`, `bash`, `c`, `cpp`, `python`, or `markdown`; `auto` clears the override and redetects |
| `:cd <path>` | change the single global working directory |
| `:cdb` | change to the focused file's directory; errors for unnamed/quickfix buffers |
| `:pwd` | show the current working directory |
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
| `:set wrapcol=<N>` | wrap at most N content display columns; `0` uses terminal width |
| `:set list` / `nolist` | show literal tabs as visible `›···` cells while preserving source tabs |
| `:set wrapmove` / `nowrapmove` | with wrap on, make `j`/`k`/Up/Down move by displayed rows |
| `:set number` / `nonumber` | toggle absolute line numbers |
| `:set relativenumber` / `norelativenumber` | toggle relative line numbers |
| `:set autoindent` / `noautoindent` | toggle autoindent |
| `:set comment=<str>` | set comment prefix (default `#`) |
| `:set scrolloff=<N>` | keep N-line vertical margin around cursor |
| `:set clipboard=osc52|auto|off` | clipboard copy mode (current default `auto`) |
| `:set mouse=off|scroll|cursor|visual` | SGR mouse mode; wheel scrolls three rows, clicks position the cursor, and `visual` enables characterwise drag selection (`off` default) |
| `:set yankflash=<ms>` | yank highlight duration in milliseconds (`0` disables) |
| `:set delcopy` / `nodelcopy` | choose whether `d` updates the unnamed register; `yd` always does |
| `:set rghidden` / `norghidden` | add `-H` to `:rg` command when set |
| `:set hlsearch` / `nohlsearch` | highlight active search-regex matches (default off) |
| `:set markdownfences` / `nomarkdownfences` | while `:md` is active in Markdown files, omit matched ``` or ~~~ opening/closing marker rows |
| `:set autodetect` / `noautodetect` | enable or disable syntax and automatic Markdown recognition for subsequently opened buffers (default on) |
| `:set saveversions=<N>` | retain 0–100 prior disk versions on explicit writes (`0` disables; default `0`) |
| `:set autosave` / `noautosave` | toggle idle autosave for dirty named buffers (default off) |
| `:set autosavedelay=<N>` | idle milliseconds after the last mutation before autosave (default 1000) |
| `:set makeprg=<cmd>` | shell command used by `:make` (default `make`) |

Line numbers use a five-column field that expands for files over 99,999 lines, followed by one separator space. Absolute numbers are right-aligned. With `relativenumber`, the cursor row shows its absolute number flush left and other rows show right-aligned relative distances.

With `autodetect` enabled, opening `.md` or `.markdown` automatically enters Markdown presentation; reload preserves automatically detected or forced Markdown presentation. Changing `autodetect` affects only buffers opened afterward. `:ft auto` reruns detection for the current buffer, while explicit `:ft`, `:md`, and `:nomd` remain available when automatic detection is disabled.

Markdown presentation styles ATX headers, list markers, and valid pipe tables. Tables containing a separator row are virtually padded to align columns; source text and the dirty flag are unchanged. Fenced code uses Bash, C, C++, or Python highlighting when the information string is `bash`/`sh`/`shell`, `c`, `cpp`/`c++`/`cc`/`cxx`, or `python`/`py`. Unknown and unlabeled fences remain unstyled code rather than receiving Markdown prose styles. Closing markers must use the opening marker character and at least its length. With `markdownfences`, matched fence-marker source rows occupy no display row, while line numbers, search, scrolling, wrapping, and mouse mapping retain source coordinates. The status bar shows `[MD]`. Navigation, search, and yank continue to use source positions and text. The first modifying action returns the whole buffer to literal source display before editing.

Autosave uses main-loop deadlines rather than a thread. It writes every due dirty named buffer, including unfocused buffers; unnamed buffers are silently skipped. Explicit writes clear pending deadlines. Success updates the undo save point and clears dirty state. Failure leaves the buffer dirty, reports once in the message bar, and retries only after another mutation. Missing parent directories are errors, and autosaves never rotate `saveversions` backups.

With nonzero `saveversions`, an explicit write that changes an existing target first preserves its raw prior contents and metadata beside it as `.vigor-bak.<basename>.1`; older generations rotate upward to N. New and unchanged targets are skipped, backups do not version themselves, and a backup failure blocks the write. There is no restore command; versions can be opened or copied directly. Add `.vigor-bak.*` to project ignore files as needed.

Path semantics: all explicit relative file paths, completion, and shell commands use one process working directory. It starts as the invocation directory and changes globally with `:cd` or `:cdb`; `~` expands. Open buffer paths remain absolute. If `:w` targets a missing parent directory, vig asks `Create directory ...? (y/n)` before calling `mkdir -p` and writing.

### Build Diagnostics

`:qf !<cmd>` and `:make` retain ordinary output for context while quickfix navigation skips non-location rows. Navigable producers emit `path:line:column: message`; `path:line: message` is normalized to column 1. ANSI escapes are stripped, nonzero output is retained, and silent successful builds leave the current buffer active.

The optional producer wrapper normalizes GCC/Clang output and Python traceback frames while preserving context and command exit status:

```vim
:set makeprg=./scripts/vig-diagnostics make
:make clean
:qf !./scripts/vig-diagnostics python3 -m pytest
:qf !./scripts/vig-diagnostics --cwd subproject make
```

It executes arguments directly rather than through a shell; use `sh -c '...'` explicitly for pipelines or redirection. `--cwd DIR` changes the producer command's directory. Recognized relative GCC/Clang and Python paths become absolute. Vigor also records each quickfix producer's cwd, so later `:cd` commands do not reinterpret generic relative results. See `proposals/build-diagnostics-proposal.md` for the protocol.

### Multi-Buffer
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

Use `j`/`k` or arrow keys in the quickfix buffer to choose a row, then `<space>o` to open it. `<space>j`/`<space>k` navigate and open remembered quickfix items from either quickfix or source buffers. A successful open shows the acted-on quickfix line in the message bar. `<space>c` returns to quickfix.
| Status bar `[N/M]` | shown when >1 buffer open |

### Insert Mode
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

### Startup Config
- Unless `VIG_NO_CONFIG` is set, vig reads `~/.vigrc` then `$XDG_CONFIG_HOME/vig/config`.
- `VIG_CONFIG=/path/to/file` reads only that file.
- Lines are simple set-style options: `set number`, `relativenumber`, `scrolloff=3`, etc.
- Blank lines and lines starting with `#` are ignored.

### Terminal Features
- `mouse=scroll|cursor|visual` enables SGR wheel reporting in every editor mode; `cursor` adds click positioning and `visual` adds characterwise drag selection
- Mouse release leaves a dragged Visual selection active without yanking; click without drag only positions, and status/message clicks are ignored
- Mouse reporting is disabled during exit, suspension, and temporary terminal handoff; Shift-drag remains the terminal-native selection escape hatch where supported
- Startup first renders the editor, then overlays a horizontally centered rounded logo frame high on the screen. Input dismisses it and still executes; command-line file startup also dismisses it after two seconds.
- Cursor shape: block (Normal/Visual), bar (Insert)
- Single `write()` render — no flicker
- Existing tab characters render as spaces to four-column stops; cursor movement, wrapping, scrolling, and highlighting use those display columns
- The visible window horizontally scrolls in nowrap mode to keep the cursor visible
- SIGWINCH-aware terminal resize
- Ctrl-Z moves the terminal cursor to the bottom line, suspends vig, and restores raw mode when foregrounded
- Ctrl-C cancels pending input/state and returns to Normal mode; Normal-mode `Ctrl-C Ctrl-C` = `:qall`, `Ctrl-C q` = `:qall!`
- Undo stack: 100 snapshot limit
