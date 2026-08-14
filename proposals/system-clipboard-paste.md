# System Clipboard Paste Proposal

### Status

Proposal only. No implementation has been started.

### Summary

Vigor already copies yanked or deleted text to the system clipboard and accepts terminal bracketed paste in Insert mode. It does not currently retrieve the system clipboard when Normal-mode `p` or `P` is used.

Add explicit system-clipboard paste mappings without changing the deterministic behavior of ordinary `p` and `P`:

```text
<space>p    read system clipboard and paste after
<space>P    read system clipboard and paste before
```

The feature should use optional platform clipboard commands and retain terminal paste as the portable fallback, especially over SSH.

### Existing Copy Behavior

Vigor's unnamed register is set by yanks and, when `delcopy` is enabled, deletes. `_set_register()` forwards the text to the configured clipboard mode:

```vim
:set clipboard=auto
:set clipboard=osc52
:set clipboard=off
```

`auto` currently tries the first available external command:

- macOS: `pbcopy`
- Wayland: `wl-copy`
- X11: `xclip -selection clipboard`
- X11 alternative: `xsel --clipboard --input`
- Windows/WSL: `clip.exe`
- OSC 52 fallback

`nodelcopy` prevents ordinary delete operators from replacing the register and system clipboard. `yd{motion}` remains an explicit delete-and-copy operation.

### Existing Terminal Paste

Bracketed terminal paste already works in Insert, Command, and Search modes.

For system text in a buffer, the portable sequence is:

1. Enter Insert mode with `i`, `a`, or another Insert command.
2. Invoke the terminal's paste shortcut, commonly Ctrl-Shift-V or Cmd-V.

The terminal reads its own clipboard and sends a bracketed paste. Vigor inserts it literally, normalizes CRLF and CR to LF, and does not interpret embedded tabs, escape bytes, or newlines as commands.

This is the preferred remote behavior because a local terminal can paste into vigor running over SSH without requiring a clipboard utility on the remote host.

### Missing Behavior

Normal-mode `p` and `P` paste only vigor's internal unnamed register. Copying text in another application and then pressing `p` does not import the current system clipboard.

The proposal adds an explicit import path rather than making every ordinary paste invoke an external process.

### External Clipboard Readers

Add a helper that returns a platform command for reading the clipboard:

- macOS: `pbpaste`
- Wayland: `wl-paste --no-newline`
- X11: `xclip -selection clipboard -o`
- X11 alternative: `xsel --clipboard --output`
- Windows/WSL: PowerShell `Get-Clipboard -Raw`

A second helper should execute it and return clipboard text or `None`:

```python
_read_system_clipboard()
```

Requirements:

- Use `subprocess.run()` with captured output.
- Use a short timeout, initially one second.
- Decode invalid text with replacement rather than crashing.
- Treat successful empty output as an empty clipboard.
- Return `None` when no provider exists, execution fails, or timeout expires.
- Never allow provider errors to terminate vigor.

Estimated runtime increase for provider detection and reading: **25–40 lines**.

### Proposed Mappings

```text
<space>p    system paste after
<space>P    system paste before
```

On a successful non-empty read:

1. Infer characterwise or linewise register type.
2. Store the imported text in the unnamed register without copying it back to the system clipboard.
3. Snapshot the buffer for undo.
4. Delegate to existing `_paste_after()` or `_paste_before()`.
5. Participate in dot repeat in the same way as ordinary paste, subject to the decision below about clipboard re-reading.

On empty clipboard text, report:

```text
System clipboard empty
```

When unavailable, report:

```text
System clipboard unavailable
```

Estimated total runtime increase for explicit mappings: **35–50 lines**.

### Clipboard Mode Interaction

Recommended behavior:

- `clipboard=off` — do not invoke a provider; report `System clipboard disabled`.
- `clipboard=auto` — try the first available external read command.
- `clipboard=osc52` — remain copy-only; report `System clipboard paste unavailable`.

OSC 52 should not silently fall through to external reading when the user explicitly selected `osc52`.

### Characterwise and Linewise Inference

System clipboards carry text but no vigor register metadata.

Recommended heuristic:

- Text ending in LF is linewise.
- Other text is characterwise.
- For linewise text, remove exactly one final LF before storing it in the unnamed register.
- Preserve internal newlines and any additional trailing blank logical lines.

Examples:

```text
"alpha"          characterwise
"alpha\nbeta"    characterwise multiline
"alpha\nbeta\n"  linewise
```

This matches common whole-line clipboard behavior but cannot be perfect because some applications always add or remove a final newline.

Treating every imported clipboard as characterwise would save a few lines but would make whole-line Normal-mode paste less useful.

### Ordinary `p` and `P`

Do not make ordinary `p` or `P` automatically synchronize from the system clipboard.

Automatic synchronization would:

- Start a subprocess for every paste.
- Unexpectedly replace vigor's internal register.
- Make behavior depend on installed desktop utilities.
- Read a remote clipboard rather than the local terminal clipboard over SSH.
- Require fallback semantics whenever the provider fails.

Users who want portable local-terminal paste can continue using Insert mode and the terminal paste shortcut. Users who explicitly want provider-backed Normal-mode paste can use the leader mappings.

Automatic `p`/`P` synchronization is estimated at **40–60 runtime lines** and is not recommended.

### OSC 52 Clipboard Reading

OSC 52 copy is already useful because vigor can send clipboard contents to the terminal without receiving a response.

OSC 52 readback would require:

- Sending a clipboard query.
- Waiting asynchronously for a terminal response.
- Parsing base64 response data from the keyboard input stream.
- Distinguishing responses from ordinary input and paste.
- Applying a timeout.
- Handling tmux, screen, and terminal-specific forwarding.
- Handling terminals that disable clipboard reads for security.

Estimated runtime increase: **50–90 lines**, with unreliable portability.

OSC 52 should remain copy-only.

### Undo and Dot Repeat

System paste must create one undo snapshot, matching ordinary `p` and `P`.

Dot repeat requires a decision:

**Repeat imported text**

Store the imported clipboard in the unnamed register and let `.` replay the same text. This is deterministic and matches vigor's existing keystroke replay model.

**Read clipboard again**

Let `.` invoke `<space>p` or `<space>P` again and import whatever is currently on the system clipboard. This is less predictable and runs another subprocess.

Recommended behavior is to repeat the already imported register text. The leader mapping should record an ordinary paste action after a successful read rather than record another clipboard import.

### Mouse Selection Interaction

With proposed `mouse=visual` support:

1. Drag to create a vigor Visual selection.
2. Press `y` to use existing system-copy behavior.
3. Use `<space>p` or `<space>P` to import externally copied text.

Automatic copying on mouse release is outside this proposal.

With terminal-native selection, the terminal owns copying. Terminal bracketed paste remains the natural matching paste mechanism.

### Testing

Provider commands should be tested with temporary fake executables and controlled `PATH` values.

Tests should cover:

- Provider precedence on each simulated platform environment
- Characterwise import and paste after/before
- Linewise inference from a final LF
- Multiline characterwise text without a final LF
- Empty clipboard output
- Missing provider
- Provider nonzero exit
- Provider timeout
- `clipboard=off`
- `clipboard=osc52`
- One undo snapshot
- Deterministic dot repeat without a second provider invocation
- No copy-back subprocess after import
- Normal register paste remains unchanged
- Bracketed terminal paste remains unchanged

### Scope Boundaries

Do not include initially:

- OSC 52 clipboard queries
- Named clipboard registers
- Automatic synchronization of ordinary `p` and `P`
- Clipboard history
- Multiple MIME types
- Image or rich-text paste
- Primary-selection support separate from the clipboard selection
- Mouse-release automatic copy
- Configurable clipboard provider commands

### Decisions Required Before Implementation

- Confirm `<space>p` and `<space>P` mappings.
- Confirm `clipboard=off` disables import and `clipboard=osc52` remains copy-only.
- Confirm the final-LF linewise heuristic.
- Confirm empty clipboard reports a message and performs no edit.
- Confirm dot repeat reuses imported register text without reading the provider again.
- Confirm one-second provider timeout.
