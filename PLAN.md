# ved — Development Plan

A modal, vi-inspired terminal text editor. ANSI escape codes only. Radical simplicity.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                    ved.py                       │
│                                                 │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐ │
│  │ Terminal   │   │  Buffer   │   │  Modes    │ │
│  │           │   │           │   │           │ │
│  │ raw mode   │◄──│ lines[]   │◄──│ NORMAL    │ │
│  │ read key   │   │ cursor    │   │ INSERT    │ │
│  │ ANSI write │   │ scroll    │   │ COMMAND   │ │
│  │           │   │ dirty     │   │ VISUAL    │ │
│  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘ │
│        │               │               │       │
│        └───────┬───────┘               │       │
│                ▼                       │       │
│        ┌───────────────┐               │       │
│        │   Renderer    │◄──────────────┘       │
│        │               │                       │
│        │ draw buffer   │                       │
│        │ status line   │                       │
│        │ command line   │                       │
│        └───────────────┘                       │
└─────────────────────────────────────────────────┘
```

**Single file**: `ved.py` (~500-800 lines target).  
**No dependencies** beyond Python 3 stdlib.  
**No curses** — all terminal control via ANSI escape sequences and `termios`.  
**Full-window**: uses entire terminal, targeting standard sizes (80×24 through ~250×60).

---

## Data Structures

### Buffer
```python
class Buffer:
    lines: list[str]   # text content, one string per line (no newlines stored)
    path: str | None    # file path, None if unsaved
    dirty: bool         # modified since last save
```

#### Insert-mode efficiency

Python strings are immutable, so every character typed creates a new string for
that line.  For typical editing this is fast enough — `str[:i] + ch + str[i:]`
is O(n) where n is the length of one line (usually <200 chars), and CPython's
memory allocator handles small transient strings well.

If profiling ever shows this as a bottleneck, the line under the cursor could
be temporarily stored as a `list[str]` (one element per char) during Insert
mode and joined back to a `str` on Esc.  This keeps the common case (reading
lines for rendering) simple while amortizing insert cost.  We'll start with
plain string slicing and only add this optimisation if needed.

### Editor (top-level state)
```python
class Editor:
    buf: Buffer
    cx, cy: int         # cursor column, row (0-indexed, within buffer)
    scroll: int         # first visible line index
    mode: Mode          # NORMAL | INSERT | COMMAND | VISUAL
    cmd: str            # command-line input buffer (for ":" mode)
    msg: str            # status message
    vx, vy: int         # visual mode anchor point
    rows, cols: int     # terminal dimensions
    running: bool
```

---

## Mode State Machine

```
         ":" ──────► COMMAND ──────► (execute/Esc) ─┐
         ▲                                          │
         │                                          ▼
  ┌──► NORMAL ◄────────────────────────────────────────┘
  │      │  │
  │      │  │  "v"/"V"
  │      │  └──────► VISUAL ──────► (Esc) ──► NORMAL
  │      │
  │      │  "i"/"I"/"a"/"A"
  │      └─────────► INSERT
  │                     │
  │        Esc          │
  └─────────────────────┘
```

---

## Terminal I/O Layer

### Raw Mode
- `termios.tcgetattr` / `tcsetattr` to disable canonical mode, echo, signals
- Restore on exit via `atexit` and signal handlers (`SIGTERM`, `SIGWINCH`)

### Key Reading
- `sys.stdin.read(1)` in raw mode
- Escape sequences decoded:
  - `\x1b[A/B/C/D` → arrow keys (mapped internally, not required by spec)
  - `\x1b` alone (after timeout) → Escape key
- Simple: read byte, if `\x1b` try to read 2 more bytes for bracket sequences

### ANSI Output (all rendering)
| Code | Purpose |
|------|---------|
| `\x1b[2J` | Clear screen |
| `\x1b[H` | Cursor home |
| `\x1b[{r};{c}H` | Move cursor to row, col (1-indexed) |
| `\x1b[K` | Clear to end of line |
| `\x1b[7m` | Reverse video (visual selection, status) |
| `\x1b[m` | Reset attributes |
| `\x1b[?25h/l` | Show/hide cursor |
| `\x1b[6n` | Query cursor position (for terminal size fallback) |

### Terminal Size
- `shutil.get_terminal_size()` primary
- Handle `SIGWINCH` to update on resize

---

## Rendering

One full redraw per keystroke (simple, fast enough for text editing):

```
1. Hide cursor
2. Move cursor to home (1,1)
3. For each screen row:
   a. If buffer line exists: print line (truncated to cols), clear rest
   b. Else: print "~", clear rest
4. Draw status bar (reverse video): filename, mode, line/col, dirty flag
5. Draw message/command bar:
   - COMMAND mode: ":..." prompt
   - Otherwise: status message (clears after next key)
6. Position cursor at (cy - scroll + 1, cx + 1)
7. Show cursor
8. Flush stdout
```

All output built as a single string, written in one `sys.stdout.write()` call to avoid flicker.

---

## Modes & Commands — Full Specification

### Normal Mode

#### Motions
| Key | Action |
|-----|--------|
| `h` | Move cursor left |
| `l` | Move cursor right |
| `j` | Move cursor down |
| `k` | Move cursor up |
| `w` | Forward to start of next word (punctuation is a word) |
| `W` | Forward to start of next WORD (whitespace-delimited) |
| `b` | Backward to start of word |
| `B` | Backward to start of WORD |
| `e` | Forward to end of word |
| `E` | Forward to end of WORD |

**Word** = contiguous run of `[a-zA-Z0-9_]` or contiguous run of punctuation.  
**WORD** = contiguous run of non-whitespace.

#### Mode Switches
| Key | Action |
|-----|--------|
| `i` | Enter INSERT before cursor |
| `I` | Move to first non-blank, enter INSERT |
| `a` | Enter INSERT after cursor |
| `A` | Move to end of line, enter INSERT |
| `v` | Enter VISUAL (character-wise) |
| `V` | Enter VISUAL LINE |
| `:` | Enter COMMAND mode |

### Insert Mode

| Key | Action |
|-----|--------|
| `Esc` | Return to NORMAL mode (cursor stays in place — unlike vi) |
| `Enter` | Split line at cursor |
| `Backspace` | Delete char before cursor (join lines if at col 0) |
| _any other_ | Insert character at cursor position |

### Command Mode

Commands entered after `:` prompt, executed on `Enter`, cancelled with `Esc`.

| Command | Action |
|---------|--------|
| `:new` | Open a new empty buffer |
| `:e[dit] <path>` | Open file at path (replace current buffer) |
| `:w[rite] [path]` | Write buffer to file (path optional if buffer has one) |
| `:q[uit]` | Quit (refuse if dirty, unless `:q!`) |
| `:wq` | Write and quit |

Command parsing: split on whitespace, match first token against commands.  
Abbreviations: `:e` = `:edit`, `:w` = `:write`, `:q` = `:quit`.  
`!` suffix on `:q` forces quit without saving.

### Visual Mode

| Key | Action |
|-----|--------|
| `Esc` | Return to NORMAL mode |
| _motions_ | Extend selection (same motions as Normal mode) |

Selection is highlighted with reverse video during rendering.  
`v` = character-wise selection, `V` = full-line selection.

---

## Implementation Phases

### Phase 1 — Scaffold (target: working skeleton)
- [ ] Terminal raw mode enter/exit
- [ ] Key reading (single bytes + escape sequences)
- [ ] Buffer class (load file, list of lines)
- [ ] Editor class (state, main loop)
- [ ] Basic rendering (buffer lines, `~` for empty, cursor positioning)
- [ ] Normal mode: `h j k l` movement
- [ ] Quit with `:q`

**Milestone**: open a file, move around, quit.

### Phase 2 — Editing
- [ ] Insert mode: `i`, `a`, typing, `Esc` to return
- [ ] `I`, `A` variants
- [ ] `Enter` to split lines, `Backspace` to delete/join
- [ ] `:w` to save
- [ ] `:e` to open files
- [ ] `:new` for empty buffer
- [ ] Dirty flag tracking

**Milestone**: open, edit, save files.

### Phase 3 — Word Motions
- [ ] `w`, `b`, `e` (word = alnum/underscore or punctuation)
- [ ] `W`, `B`, `E` (WORD = non-whitespace)

**Milestone**: fast navigation.

### Phase 4 — Visual Mode
- [ ] `v` character-wise selection (anchor + cursor)
- [ ] `V` line-wise selection
- [ ] Render selection with reverse video
- [ ] `Esc` to cancel
- [ ] All motions work within visual mode

**Milestone**: visual selection visible and navigable.

### Phase 5 — Polish
- [ ] Status bar (mode, filename, dirty, position)
- [ ] Message bar (error messages, info)
- [ ] Edge cases (empty file, last line, line longer than screen)
- [ ] `:wq`, `:q!`
- [ ] Scroll follows cursor (scroll up/down when cursor moves off-screen)

**Milestone**: robust, informative UI.

### Phase 6 — Resize
- [ ] Handle `SIGWINCH` to detect terminal resize
- [ ] Re-query `shutil.get_terminal_size()`, update `rows`/`cols`
- [ ] Re-clamp scroll and cursor, trigger full redraw
- [ ] Verify status bar and content reflow after resize

**Milestone**: resize terminal window mid-session without corruption.

### Phase 7 — Count Prefixes
- [ ] Accumulate digit keys (`1`-`9`, then `0`-`9`) into a pending count
- [ ] Apply count as repeat factor to motions: `3j`, `5w`, `10l`, etc.
- [ ] Apply count to insert-mode entry: `3i` types inserted text 3 times on Esc
- [ ] `0` without pending count = move to column 0 (future, if desired)
- [ ] Count resets on Esc, mode change, or after execution

**Milestone**: `12j` moves down 12 lines.

---

## File Layout

```
ved/
├── ved.py          # the entire editor
├── test_ved.py     # smoke tests (PTY-based, plain asserts)
├── PLAN.md         # this document
└── README.md       # usage instructions
```

Two source files. That's the point.

---

## Key Design Decisions

1. **Single string buffer for output** — build entire frame as one string, one `write()` call. No flicker.

2. **List of strings for text** — `lines[y]` gives line `y`. Insertions/deletions are `list.insert()` / `list.pop()`. Simple, correct, fast enough for any reasonable file.

3. **Count prefixes** — added in Phase 7. Digits accumulate into a repeat
   count applied to motions. Implemented as a thin wrapper, not interleaved
   into every motion handler.

4. **No registers, no undo** — out of scope. Radical simplicity means leaving things out.

5. **No syntax highlighting** — plain text only. The ANSI layer is for UI chrome (status bar, selection), not content decoration.

6. **Word motion algorithm**:
   - Classify each character: WORD_CHAR (`[a-zA-Z0-9_]`), PUNCT (non-whitespace, non-word), SPACE
   - `w`: skip current class, skip spaces, land on next class start
   - `b`: move back, skip spaces, skip current class, land on class start
   - `e`: move forward, skip spaces, skip current class, land on class end

7. **Cursor bounds** — `cx` clamped to `0..len(line)` in all modes
   (one-past-end is allowed — diverges from vi). `cy` clamped to
   `0..len(lines)-1`.

8. **`i`/`a` → Esc keeps cursor in place** — vi moves left one column on Esc
   from Insert mode; ved does not. The cursor stays exactly where it was when
   Esc was pressed. This eliminates a common source of confusion.

9. **Full terminal window** — ved uses all `rows` for content (minus 2 for
   status + command bars) and all `cols`. Rendering always fills every cell
   or clears to EOL, so no stale content appears.

---

## Dependencies

- Python 3.8+
- `sys`, `os`, `termios`, `tty`, `atexit`, `signal`, `shutil` (all stdlib)

Zero external packages.

---

## How to Run

```bash
python3 ved.py [filename]
```

---

## Smoke Tests

All tests live in `test_ved.py`.  Each test launches `ved.py` in a **PTY**
(`pty.openpty()`), sends keystrokes via `os.write()`, reads output, and checks
results with plain `assert`.  No external test framework.

### Harness Design

```python
def run_ved(keys: str | bytes, file: str | None = None, timeout: float = 2.0) -> tuple[str, str]:
    """
    Launch ved in a PTY, send `keys`, wait for exit or timeout.
    Returns (screen_output, file_contents_after).
    """
```

- `keys`: raw bytes to feed (e.g., `b"ihello\x1b:wq\r"`)
- Creates a temp file (or uses provided path) so file I/O can be verified
- Captures final screen output for display assertions
- Kills ved on timeout to prevent hangs

### Tests by Phase

#### Phase 1 — Scaffold
| # | Test | Keys | Assert |
|---|------|------|--------|
| 1 | Open & quit | `:q\r` | Exit code 0 |
| 2 | Open file, content visible | _(open, read screen)_ `:q\r` | First line of file appears in output |
| 3 | `j`/`k` movement | `jjk:q\r` | No crash, clean exit |
| 4 | `h`/`l` movement | `llh:q\r` | No crash, clean exit |
| 5 | Scroll down | `j` × 30 + `:q\r` | Cursor stays on screen |

#### Phase 2 — Editing
| # | Test | Keys | Assert |
|---|------|------|--------|
| 6 | Insert text | `ihello\x1b:wq\r` | File contains "hello" |
| 7 | `a` appends | `$ahello\x1b:wq\r` | Text appended |
| 8 | `I` / `A` | `Istart\x1b` / `Aend\x1b` | Correct position |
| 9 | Enter splits line | `ihello\rworld\x1b:wq\r` | File has 2 lines |
| 10 | Backspace joins | _(setup 2 lines)_ `jI\x7f\x1b:wq\r` | Lines joined |
| 11 | `:e` opens file | `:e other.txt\r:q\r` | No crash |
| 12 | `:w` saves | `ix\x1b:w\r:q\r` | File modified |
| 13 | `:q` dirty refuse | `ix\x1b:q\r` | Error message, still running |
| 14 | `:new` | `:new\r:q\r` | Clean exit, empty buffer |

#### Phase 3 — Word Motions
| # | Test | Keys | Assert |
|---|------|------|--------|
| 15 | `w` forward word | _(file: "hello world")_ `w` | Cursor on 'w' |
| 16 | `b` backward word | `$b` | Cursor on 'w' (back from end) |
| 17 | `e` end of word | `e` | Cursor on 'o' of "hello" |
| 18 | `W`/`B`/`E` WORD | _(file: "a.b c.d")_ `W` | Cursor on 'c' |

#### Phase 4 — Visual Mode
| # | Test | Keys | Assert |
|---|------|------|--------|
| 19 | `v` enters visual | `v` | Screen shows reverse video |
| 20 | `V` line select | `V` | Full line highlighted |
| 21 | Esc cancels | `v\x1b` | Back to normal, no highlight |
| 22 | Motion extends | `vll` | 3 chars highlighted |

#### Phase 5 — Polish
| # | Test | Keys | Assert |
|---|------|------|--------|
| 23 | Status bar shown | _(open file)_ | Screen contains filename |
| 24 | `:wq` | `ix\x1b:wq\r` | File saved, exit 0 |
| 25 | `:q!` forces | `ix\x1b:q!\r` | Exit 0, file unchanged |
| 26 | Empty file | _(open /dev/null)_ `:q\r` | Tildes visible, no crash |

#### Phase 6 — Resize
| # | Test | Keys | Assert |
|---|------|------|--------|
| 27 | SIGWINCH | _(send SIGWINCH after open)_ | No crash, redraws |
| 28 | Shrink + grow | _(resize PTY)_ | Content intact |

#### Phase 7 — Count Prefixes
| # | Test | Keys | Assert |
|---|------|------|--------|
| 29 | `3j` | _(file: 10 lines)_ `3j` | Cursor on line 4 |
| 30 | `5l` | _(file: "abcdefgh")_ `5l` | Cursor on 'f' |
| 31 | Count resets | `3\x1b:q\r` | Normal quit, no stale count |

---

## Divergences from vi

ved intentionally differs from vi in these ways:

| Behavior | vi | ved | Rationale |
|----------|-----|------|----------|
| Esc from Insert | Cursor moves left 1 | Cursor stays in place | Less surprising |
| Cursor past EOL | Forbidden in Normal | Allowed in all modes | Consistent, simpler clamping |
| Count prefixes | Phase 1 feature | Phase 7 (last) | Build simple first |
| Registers & undo | Core features | Not implemented | Radical simplicity |
| Ex commands | Dozens | 5 (new/edit/write/quit/wq) | Minimal viable set |

---

## AI Workflow Notes

- **Front-load questions**: gather all ambiguous requirements before starting
  each phase, then proceed autonomously through implementation.
- **Phase gate**: after each phase, run that phase's smoke tests.  Compare
  actual vs. expected.  If all pass, move on.  If failures are minor, fix and
  re-run.  If stuck in a fix→fail loop for >3 attempts on the same issue,
  stop and request guidance.
- **Incremental commits**: each phase is a working, testable increment.
- **No speculative features**: implement only what the plan specifies.
