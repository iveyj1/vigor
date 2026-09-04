# Build Diagnostics Quickfix Proposal

### Status

Accepted and implemented.

### Summary

Add a compact diagnostic-producer interface rather than teaching vigor how to parse every compiler, build system, linter, and test runner.

Vigor runs a command, captures its merged output, and stores that output in the remembered quickfix buffer. Tool-specific translation remains optional project tooling, so `vig.py` stays self-contained and has no external runtime dependency.

### Commands

**Generic producer**

```vim
:qf !<shell-command>
```

Examples:

```vim
:qf !make
:qf !ninja
:qf !./tools/build-for-vigor debug
```

**Configured build**

```vim
:set makeprg=make
:make
:make clean
```

`:make [args]` runs `makeprg`, appending the supplied arguments. The default is `make`.

Startup configuration uses the existing `:set` path:

```text
set makeprg=ninja
```

### Diagnostic Producer Protocol

Navigable diagnostics use one line per location:

```text
path:line:column: message
```

Examples:

```text
src/main.c:42:9: error: expected expression
lib/util.c:18:1: warning: unused function
```

A producer may omit the column:

```text
Makefile:12: missing separator
```

Vigor normalizes this to column 1:

```text
Makefile:12:1: missing separator
```

Paths should be absolute or relative to vigor's process working directory. Non-diagnostic output is retained for build context, but quickfix next/previous navigation skips rows that do not match the location format.

### Execution Behavior

- Commands run through the shell, matching existing bang-command flexibility.
- Standard error is merged into standard output to retain build ordering.
- ANSI control sequences are removed.
- There is no build timeout; builds may legitimately run longer than existing short shell helpers.
- Nonzero exit status does not discard output.
- The message bar reports exit status and captured line count.
- A successful command with no output leaves the current buffer active and reports success.
- A command with no output and nonzero status reports that status without replacing the previous quickfix.
- Each quickfix result remembers its producer working directory, so later `:cd` commands do not reinterpret relative locations.

### Quickfix Interaction

Captured output uses vigor's existing remembered quickfix buffer.

- `<space>c` opens the quickfix buffer.
- `<space>o` opens the location on the current quickfix row.
- `<space>j` opens the next valid diagnostic.
- `<space>k` opens the previous valid diagnostic.
- Opening a location shows the acted-on quickfix line in the message bar.
- Context rows remain visible but are skipped by next/previous navigation.

### External Translators

The optional `scripts/vig-diagnostics [--cwd DIR] <command> [args...]` wrapper, installed beside `vig` as `vig-diag`, implements the protocol for GCC/Clang output, Python traceback frames, and Bash `path: line N:` errors. It runs the command directly, merges output, preserves exit status, strips ANSI, and converts recognized relative paths to absolute paths. `--cwd` changes the command directory before execution and provides the base for those paths.

Projects may provide other wrappers for formats that are not already compatible, including Rust JSON, pytest output, or MSVC diagnostics.

A wrapper should preserve the build's exit status. A portable pattern is:

```sh
#!/bin/sh

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

make "$@" >"$tmp" 2>&1
status=$?
./tools/diagnostics-to-vigor <"$tmp"
exit "$status"
```

This is preferable to a simple pipeline because a pipeline commonly returns the translator's status rather than the build's status.

### Scope Boundaries

The core `vig.py` implementation intentionally omits:

- Vim-style configurable `errorformat` parsing
- Python, Rust, MSVC, and test-framework-specific parsers inside the editor
- Live or asynchronous build output
- Build cancellation UI
- Automatic project-root discovery
- A mandatory bundled translation script

These can be considered independently without expanding the core diagnostic protocol.

### Single-File Principle

`vig.py` remains the only required runtime file. External translators are optional project-owned producers, not vigor dependencies. The editor implements only command capture, minimal normalization, and existing quickfix integration.
