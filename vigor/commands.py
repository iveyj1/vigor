"""Command prompt, ex commands, completion, quickfix, and processes."""

import os
import re
import shlex
import shutil

from .highlight import ANSI_ESCAPE
from .state import BufferState, Mode


class CommandMixin:
    """Command subsystem mixed into the application orchestrator."""

    _FILETYPES = frozenset(("auto", "text", "bash", "c", "cpp", "python", "markdown"))

    # ── Command mode ───────────────────────────────────────────────────

    def _reset_history_nav(self):
        self._hist_idx = None
        self._hist_draft = ""

    def _history_nav(self, hist, older):
        if not hist:
            return
        if self._hist_idx is None:
            self._hist_draft = self.cmd
            self._hist_idx = len(hist)
        self._hist_idx += -1 if older else 1
        if self._hist_idx < 0:
            self._hist_idx = 0
        if self._hist_idx >= len(hist):
            self._hist_idx = None
            self.cmd = self._hist_draft
        else:
            self.cmd = hist[self._hist_idx]
        self.cmd_cx = len(self.cmd)

    def _add_history(self, hist, text):
        if text and (not hist or hist[-1] != text):
            hist.append(text)

    def _edit_prompt(self, key, completion=False):
        """Edit self.cmd. Return False only for an unhandled key or empty Backspace."""
        changed = False
        if key == "LEFT":
            self.cmd_cx = max(0, self.cmd_cx - 1)
        elif key == "RIGHT":
            self.cmd_cx = min(len(self.cmd), self.cmd_cx + 1)
        elif key == "BACKSPACE":
            if not self.cmd_cx:
                return bool(self.cmd)
            self.cmd = self.cmd[:self.cmd_cx - 1] + self.cmd[self.cmd_cx:]
            self.cmd_cx -= 1
            changed = True
        elif key == "DEL":
            if self.cmd_cx < len(self.cmd):
                self.cmd = self.cmd[:self.cmd_cx] + self.cmd[self.cmd_cx + 1:]
                changed = True
        elif len(key) == 1:
            self.cmd = self.cmd[:self.cmd_cx] + key + self.cmd[self.cmd_cx:]
            self.cmd_cx += 1
            changed = True
        else:
            return False
        if changed:
            self._reset_history_nav()
            if completion:
                self._refresh_completion()
        return True

    def _clear_completion(self):
        self.comp_matches = []
        self.comp_index = 0

    def _completion_context(self):
        s = self.cmd
        if s.startswith("!"):
            body = s[1:].lstrip()
            if " " in body:
                before, token = body.rsplit(None, 1)
                head = "!" + before + " "
            else:
                head, token = "!", body
            return head, token, os.getcwd(), True
        parts = s.split(None, 1)
        if not parts or parts[0] not in ("e", "edit", "w", "write", "r", "read", "rgf", "cd"):
            return None
        return parts[0], (parts[1] if len(parts) > 1 else ""), os.getcwd(), False

    def _completion_names(self, token, base_dir):
        expanded = os.path.expanduser(token)
        dpart, prefix = os.path.split(expanded)
        search_dir = dpart if os.path.isabs(dpart) else os.path.join(base_dir, dpart)
        try:
            names = sorted(n for n in os.listdir(search_dir or ".") if n.startswith(prefix))
        except OSError:
            return [], search_dir
        shown = [n + ("/" if os.path.isdir(os.path.join(search_dir, n)) else "") for n in names]
        return shown, search_dir

    def _set_completed_token(self, name):
        new_token = os.path.join(os.path.dirname(self.comp_token), name) if os.path.dirname(self.comp_token) else name
        sep = "" if self.comp_shell else " "
        self.cmd = (self.comp_head + sep + new_token).strip() if self.comp_head else new_token
        self.cmd_cx = len(self.cmd)

    def _start_completion(self):
        ctx = self._completion_context()
        if not ctx:
            return
        self.comp_head, self.comp_token, self.comp_base_dir, self.comp_shell = ctx
        self.comp_matches, _ = self._completion_names(self.comp_token, self.comp_base_dir)
        self.comp_index = 0
        if len(self.comp_matches) == 1:
            self._set_completed_token(self.comp_matches[0])
            self._clear_completion()

    def _refresh_completion(self):
        if not self.comp_matches:
            return
        ctx = self._completion_context()
        if not ctx:
            self._clear_completion()
            return
        self.comp_head, self.comp_token, self.comp_base_dir, self.comp_shell = ctx
        self.comp_matches, _ = self._completion_names(self.comp_token, self.comp_base_dir)
        self.comp_index = min(self.comp_index, max(0, len(self.comp_matches) - 1))

    def _accept_completion(self):
        if self.comp_matches:
            self._set_completed_token(self.comp_matches[self.comp_index])
            self._clear_completion()

    def handle_command(self, key):
        if self._startup_completion and key in ("ESC", "CTRL_C"):
            self._startup_completion = False
            self.mode = Mode.NORMAL
            self.cmd = ""
            self.cmd_cx = 0
            self._reset_history_nav()
            self._clear_completion()
            return
        if self.comp_matches:
            if key == "ESC":
                self._clear_completion()
                return
            if key == "ENTER":
                self._accept_completion()
                return
            if key == "UP":
                self.comp_index = max(0, self.comp_index - 1)
                return
            if key == "DOWN":
                self.comp_index = min(len(self.comp_matches) - 1, self.comp_index + 1)
                return
            if key == "TAB":
                self.comp_index = (self.comp_index + 1) % len(self.comp_matches)
                return
            if key == "SHIFT_TAB":
                self.comp_index = (self.comp_index - 1) % len(self.comp_matches)
                return
        if key in ("ESC", "CTRL_C"):
            self.mode = Mode.NORMAL
            self.cmd = ""
            self.cmd_cx = 0
            self._reset_history_nav()
            self._clear_completion()
            return
        if key == "UP":
            self._history_nav(self.cmd_history, older=True)
            return
        if key == "DOWN":
            self._history_nav(self.cmd_history, older=False)
            return
        if key == "TAB":
            self._start_completion()
            return
        if key == "ENTER":
            cmd = self.cmd
            self._startup_completion = False
            self._add_history(self.cmd_history, cmd.strip())
            self._reset_history_nav()
            self._clear_completion()
            self._exec_command(cmd)
            self.cmd = ""
            self.cmd_cx = 0
            return
        if self._edit_prompt(key, completion=True):
            return
        if key == "BACKSPACE":
            self.mode = Mode.NORMAL

    def _exec_command(self, raw):
        stripped = raw.strip()

        # ── Substitute command: [range]s/pat/repl/[g] ──
        sub_match = re.match(
            r'^(%|(\d+)(,(\d+))?)?s([^a-zA-Z0-9\s])(.*?)\5(.*?)(?:\5([g]*))?$',
            stripped
        )
        if sub_match:
            self._exec_substitute(sub_match)
            return

        filter_match = re.match(r'^(%|[.$0-9]+(?:,[.$0-9]+)?)?(!!?)(.*)$', stripped)
        if filter_match and (filter_match.group(1) or filter_match.group(2) == "!!"):
            self._exec_filter(filter_match.group(1), filter_match.group(3).strip(), new_buffer=filter_match.group(2) == "!!")
            self.mode = Mode.NORMAL
            return

        if stripped.startswith("!") and stripped != "!":
            self._exec_bang(stripped[1:].strip())
            self.mode = Mode.NORMAL
            return

        parts = stripped.split(None, 1)
        if not parts:
            self.mode = Mode.NORMAL
            return
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else None

        if cmd in ("q", "quit"):
            if self.buf.dirty:
                self.msg = "No write since last change (add ! to override)"
                self.mode = Mode.NORMAL
                return
            if len(self.buffers) > 1:
                self._close_buffer()
            else:
                self.running = False
        elif cmd in ("q!", "quit!"):
            if len(self.buffers) > 1:
                self._close_buffer()
            else:
                self.running = False
        elif cmd in ("qa", "qa!", "qall", "qall!", "quitall", "quitall!"):
            self._quit_all(force=cmd.endswith("!"))
        elif cmd in ("w", "write"):
            path = self._resolve_cmd_path(arg) if arg else self.buf.path
            if not path:
                self.msg = "No file name"
                self.mode = Mode.NORMAL
                return
            self._write_buffer_to_path(path)
        elif cmd == "wq":
            path = self._resolve_cmd_path(arg) if arg else self.buf.path
            if not path:
                self.msg = "No file name"
                self.mode = Mode.NORMAL
                return
            self._write_buffer_to_path(path, close_after=True)
        elif cmd in ("e!", "edit!"):
            self._reload_current_buffer()
        elif cmd in ("md", "markdown", "nomd"):
            enabled = False if cmd == "nomd" else not self.md_view
            self._set_markdown_view(enabled)
            self.msg = "markdown view on" if enabled else "markdown view off"
            self.mode = Mode.NORMAL
        elif cmd in ("filetype", "ft"):
            if arg is None:
                source = ("forced" if self.filetype_override else
                          "auto" if self.buffer_autodetect else "disabled")
                self.msg = f"filetype={self._effective_filetype()} ({source})"
            else:
                value = arg.strip().lower()
                if value not in self._FILETYPES:
                    self.msg = f"Unknown file type: {value}"
                else:
                    self.filetype_override = None if value == "auto" else value
                    if value == "auto":
                        self.buffer_autodetect = True
                    effective = self._effective_filetype()
                    self._set_markdown_view(effective == "markdown")
                    source = "auto" if value == "auto" else "forced"
                    self.msg = f"filetype={effective} ({source})"
            self.mode = Mode.NORMAL
        elif cmd == "help":
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vighelp")
            if not os.path.isfile(path):
                self.msg = "Help file not found"
            else:
                self._add_buffer(BufferState(path))
                self.msg = '"vighelp"'
            self.mode = Mode.NORMAL
        elif cmd in ("e", "edit"):
            if arg:
                # Add new buffer and switch to it
                path = self._resolve_cmd_path(arg)
                if os.path.isdir(path):
                    self.msg = f'Cannot edit directory: "{path}"'
                else:
                    try:
                        new_bs = BufferState(path)
                    except OSError as e:
                        self.msg = f'Cannot edit "{path}": {e.strerror or str(e)}'
                    else:
                        self._add_buffer(new_bs)
                        self.msg = f'"{path}"'
            else:
                self.msg = "No file name"
            self.mode = Mode.NORMAL
        elif cmd == "new":
            self._add_buffer(BufferState())
            self.msg = "[New]"
            self.mode = Mode.NORMAL
        elif cmd == "pwd":
            self.msg = os.getcwd()
            self.mode = Mode.NORMAL
        elif cmd in ("cd", "cdb"):
            if cmd == "cdb":
                arg = None if self.buffers[self.buf_idx] is self.quickfix_state else self.buf.path
                target = os.path.dirname(arg) if arg else None
            else:
                target = self._resolve_cmd_path(arg) if arg else None
            if not target:
                self.msg = "No directory"
            else:
                try:
                    os.chdir(target)
                    self.msg = os.getcwd()
                except OSError as e:
                    self.msg = f'Cannot change directory: {e.strerror or str(e)}'
            self.mode = Mode.NORMAL
        elif cmd in ("n", "next", "bn"):
            if len(self.buffers) > 1:
                idx = (self.buf_idx + 1) % len(self.buffers)
                self._switch_buffer(idx)
            self.mode = Mode.NORMAL
        elif cmd in ("p", "prev", "bp"):
            if len(self.buffers) > 1:
                idx = (self.buf_idx - 1) % len(self.buffers)
                self._switch_buffer(idx)
            self.mode = Mode.NORMAL
        elif cmd == "ls":
            parts_list = []
            for i, bs in enumerate(self.buffers):
                marker = "%" if i == self.buf_idx else " "
                dirty = "+" if bs.buf.dirty else " "
                name = bs.buf.path or "[No Name]"
                parts_list.append(f"{i+1}{marker}{dirty} {name}")
            self.msg = "  ".join(parts_list)
            self.mode = Mode.NORMAL
        elif cmd in ("k", "bdelete"):
            if self.buf.dirty:
                self.msg = "No write since last change (add ! to override)"
                self.mode = Mode.NORMAL
                return
            if len(self.buffers) <= 1:
                self.msg = "Cannot delete last buffer"
                self.mode = Mode.NORMAL
                return
            self._close_buffer()
        elif cmd in ("k!", "bdelete!"):
            if len(self.buffers) <= 1:
                self.msg = "Cannot delete last buffer"
                self.mode = Mode.NORMAL
                return
            self._close_buffer()
        elif cmd == "set":
            self._exec_set(arg)
            self.mode = Mode.NORMAL
        elif cmd == "qf":
            if not arg or not arg.startswith("!"):
                self.msg = "Usage: qf !<command>"
            else:
                self._exec_qf_command(arg[1:].strip(), "qf")
            self.mode = Mode.NORMAL
        elif cmd == "make":
            command = self.opt_makeprg + ((" " + arg) if arg else "")
            self._exec_qf_command(command, "make")
            self.mode = Mode.NORMAL
        elif cmd == "rg":
            self._exec_rg(arg)
            self.mode = Mode.NORMAL
        elif cmd == "rgf":
            self._exec_rgf(arg)
            self.mode = Mode.NORMAL
        elif cmd == "read" or cmd == "r":
            self._exec_read(arg)
            self.mode = Mode.NORMAL
        elif cmd == "!":
            self._exec_bang(arg)
            self.mode = Mode.NORMAL
        else:
            self.msg = f"Not a command: {cmd}"
            self.mode = Mode.NORMAL

    def _range_line(self, token):
        if token == ".":
            return self.cy
        if token == "$":
            return len(self.buf.lines) - 1
        if token.isdigit():
            n = int(token)
            if 1 <= n <= len(self.buf.lines):
                return n - 1
        return None

    def _parse_filter_range(self, spec, default_all=False):
        if not spec:
            return (0, len(self.buf.lines) - 1) if default_all else None
        if spec == "%":
            return 0, len(self.buf.lines) - 1
        parts = spec.split(",", 1)
        start = self._range_line(parts[0])
        end = self._range_line(parts[1]) if len(parts) == 2 else start
        if start is None or end is None:
            self.msg = f"Invalid range: {spec}"
            return None
        if start > end:
            start, end = end, start
        return start, end

    def _exec_filter(self, range_spec, cmd, new_buffer=False):
        """Pipe a line range through a shell command, replacing it or opening output."""
        if not cmd:
            self.msg = "Shell command required"
            return
        rng = self._parse_filter_range(range_spec, default_all=new_buffer)
        if rng is None:
            return
        sy, ey = rng
        text = "\n".join(self.buf.lines[sy:ey + 1]) + "\n"
        import subprocess
        try:
            result = subprocess.run(cmd, input=text, capture_output=True, text=True, shell=True, timeout=10)
        except Exception as e:
            self.msg = f"filter: {e}"
            return
        if result.returncode != 0:
            err = (result.stderr or result.stdout or f"exit {result.returncode}").replace("\n", " ").strip()
            self.msg = err[:self.cols]
            return
        new_lines = result.stdout.splitlines() or [""]
        if new_buffer:
            bs = BufferState()
            bs.buf.lines = new_lines
            bs.buf.dirty = True
            self._add_buffer(bs)
            self.msg = "[Filter output]"
            return
        self._snapshot()
        self.buf.lines[sy:ey + 1] = new_lines
        if not self.buf.lines:
            self.buf.lines = [""]
        self.cy, self.cx = sy, 0
        self.buf.dirty = True
        self._clamp_cursor()
        self._ensure_scroll()

    @staticmethod
    def _normalize_diagnostic(line):
        """Add column 1 to path:line:message diagnostics."""
        if re.match(r"^.+?:\d+:\d+:", line):
            return line
        m = re.match(r"^(.+?):(\d+):(.*)$", line)
        return f"{m.group(1)}:{m.group(2)}:1:{m.group(3)}" if m else line

    def _exec_qf_command(self, cmd, source):
        """Run a diagnostic producer and load its merged output into quickfix."""
        if not cmd:
            self.msg = f"{source}: command required"
            return
        import subprocess
        try:
            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.msg = f"{source}: {e}"
            return
        lines = [self._normalize_diagnostic(ANSI_ESCAPE.sub("", line))
                 for line in result.stdout.splitlines()]
        if not lines:
            self.msg = f"{source}: " + ("success" if result.returncode == 0 else f"exit {result.returncode}")
            return
        self._show_quickfix(lines, source)
        self.msg = f"{source}: exit {result.returncode}, {len(lines)} line(s)"

    def _exec_rg(self, arg):
        """Run ripgrep and capture results in the quickfix buffer."""
        if not arg:
            self.msg = "Usage: rg <pattern> [path]"
            return
        try:
            parts = shlex.split(arg)
        except ValueError as e:
            self.msg = f"rg: {e}"
            return
        if not parts or len(parts) > 2:
            self.msg = "Usage: rg <pattern> [path]"
            return
        pattern = parts[0]
        path = self._resolve_cmd_path(parts[1]) if len(parts) == 2 else None
        cmd = ["rg", "-n", "--column"]
        if self.opt_rghidden:
            cmd.append("-H")
        cmd.append(pattern)
        if path:
            cmd.append(path)
        import subprocess
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, cwd=os.getcwd()
            )
        except FileNotFoundError:
            self.msg = "rg: command not found"
            return
        except Exception as e:
            self.msg = f"rg: {e}"
            return
        output = result.stdout if result.returncode in (0, 1) else result.stdout + result.stderr
        lines = output.splitlines()
        if not lines:
            self.msg = "rg: no matches"
            return
        self._show_quickfix(lines, "rg")

    def _show_quickfix(self, lines, source):
        """Replace the quickfix buffer with lines and make it current."""
        self.quickfix_cwd = os.getcwd()
        if self.quickfix_state in self.buffers:
            bs = self.quickfix_state
            bs.buf.lines = lines
            bs.buf.dirty = False
            bs.cx = bs.cy = bs.scroll = bs.wrap_skip = 0
            self._switch_buffer(self.buffers.index(bs))
            self.cx = self.cy = self.scroll = self._wrap_skip = 0
        else:
            bs = BufferState()
            bs.buf.path = "[quickfix]"
            bs.buf.lines = lines
            bs.buf.dirty = False
            self.quickfix_state = bs
            self._add_buffer(bs)
        self.msg = f"{source}: {len(lines)} line(s)"
        self._clamp_cursor()
        self._ensure_scroll()

    def _exec_rgf(self, arg):
        """Launch an fzf-backed live ripgrep picker into quickfix."""
        try:
            parts = shlex.split(arg or "")
        except ValueError as e:
            self.msg = f"rgf: {e}"
            return
        if len(parts) > 1:
            self.msg = "Usage: rgf [path]"
            return
        if not shutil.which("fzf"):
            self.msg = "fzf: command not found"
            return
        if not shutil.which("rg"):
            self.msg = "rg: command not found"
            return
        path = self._resolve_cmd_path(parts[0]) if parts else os.getcwd()
        rg_cmd = "rg --line-number --column --no-heading --color=always"
        if self.opt_rghidden:
            rg_cmd += " -H"
        reload_cmd = f"{rg_cmd} -- {{q}} {shlex.quote(path)} || true"
        cmd = ["fzf", "--ansi", "--phony", "--disabled", "--multi",
               "--bind", f"change:reload:{reload_cmd}",
               "--bind", f"start:reload:{reload_cmd}",
               "--bind", "enter:select-all+accept"]
        import subprocess
        self.term.suspend_restore()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception as e:
            self.msg = f"rgf: {e}"
            return
        finally:
            self.term.enter_raw()
        if result.returncode not in (0, 1, 130):
            self.msg = f"fzf: exited {result.returncode}"
            return
        lines = [ANSI_ESCAPE.sub("", line) for line in result.stdout.splitlines() if line]
        if not lines:
            self.msg = "rgf cancelled"
            return
        self._show_quickfix(lines, "rgf")

    @staticmethod
    def _quickfix_location(line):
        m = re.match(r"^(.+?):(\d+):(\d+):", line)
        return (m.group(1), int(m.group(2)), int(m.group(3))) if m and m.group(1) else None

    def _open_quickfix_location(self):
        """Open the file:line:column location under the cursor, if present."""
        location = self._quickfix_location(self.buf.lines[self.cy])
        if not location:
            self.msg = "No quickfix location"
            return
        text = self.buf.lines[self.cy]
        path, line, col = location
        base = self.quickfix_cwd if self.buffers[self.buf_idx] is self.quickfix_state else os.getcwd()
        if self._goto_file_location(path if os.path.isabs(path) else os.path.join(base, path), line, col):
            self.msg = text

    def _quickfix_step(self, delta):
        """Open the next or previous valid location in the remembered quickfix."""
        if self.quickfix_state not in self.buffers:
            self.msg = "No quickfix buffer"
            return
        qf = self.quickfix_state
        current = self.cy if self.buffers[self.buf_idx] is qf else qf.cy
        target = current + delta
        while 0 <= target < len(qf.buf.lines):
            if self._quickfix_location(qf.buf.lines[target]):
                self._switch_buffer(self.buffers.index(qf))
                self.cy, self.cx = target, 0
                self._ensure_scroll()
                self._open_quickfix_location()
                return
            target += delta
        self.msg = "No next quickfix item" if delta > 0 else "No previous quickfix item"

    def _exec_bang(self, arg):
        """Run a shell command and show compact output in the message bar."""
        if arg:
            import subprocess
            try:
                result = subprocess.run(
                    arg, shell=True, capture_output=True, text=True, timeout=10
                )
                output = result.stdout + result.stderr
                if output.strip():
                    lines = output.replace("\r\n", "\n").replace("\r", "\n").splitlines()
                    self.msg = " | ".join(line for line in lines if line)[:200]
                else:
                    self.msg = "(no output)"
            except Exception as e:
                self.msg = str(e)
        else:
            self.msg = "No command given"

    def _exec_read(self, arg):
        """Handle :read [file] and :read ![command]."""
        if not arg:
            self.msg = "Argument required"
            return
        arg = arg.strip()
        if arg.startswith("!"):
            # :read !command — insert command output below cursor
            shell_cmd = arg[1:].strip()
            if not shell_cmd:
                self.msg = "No command given"
                return
            import subprocess
            try:
                result = subprocess.run(
                    shell_cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                output = result.stdout
                if output:
                    self._snapshot()
                    lines = output.splitlines()
                    for i, line in enumerate(lines):
                        self.buf.lines.insert(self.cy + 1 + i, line)
                    self.cy += 1
                    self.cx = 0
                    self.buf.dirty = True
                    self.msg = f"{len(lines)} line(s) inserted"
                else:
                    self.msg = "(no output)"
            except Exception as e:
                self.msg = str(e)
        else:
            # :read file — insert file contents below cursor
            try:
                with open(self._resolve_cmd_path(arg), "r") as f:
                    content = f.read()
                self._snapshot()
                lines = content.splitlines()
                if not lines:
                    lines = [""]
                for i, line in enumerate(lines):
                    self.buf.lines.insert(self.cy + 1 + i, line)
                self.cy += 1
                self.cx = 0
                self.buf.dirty = True
                self.msg = f"{len(lines)} line(s) inserted"
            except FileNotFoundError:
                self.msg = f"Can't open \"{arg}\""
            except Exception as e:
                self.msg = str(e)

    def _exec_set(self, arg):
        """Handle :set <option> commands."""
        if not arg:
            self.msg = "Argument required"
            return
        opt = arg.strip()
        if opt == "wrap":
            self.opt_wrap = True
            self.msg = "wrap on"
            self._ensure_scroll()
        elif opt == "nowrap":
            self.opt_wrap = False
            self.msg = "wrap off"
            self._ensure_scroll()
        elif opt.startswith("wrapcol="):
            try:
                val = int(opt[len("wrapcol="):])
                if val < 0:
                    raise ValueError
            except ValueError:
                self.msg = "wrapcol must be >= 0"
                return
            self.opt_wrapcol = val
            self.msg = f"wrapcol={val}"
            self._ensure_scroll()
        elif opt == "number":
            self.opt_number = True
            self.msg = "number on"
        elif opt == "nonumber":
            self.opt_number = False
            self.msg = "number off"
        elif opt == "relativenumber":
            self.opt_relnum = True
            self.msg = "relativenumber on"
        elif opt == "norelativenumber":
            self.opt_relnum = False
            self.msg = "relativenumber off"
        elif opt == "autoindent":
            self.opt_autoindent = True
            self.msg = "autoindent on"
        elif opt == "noautoindent":
            self.opt_autoindent = False
            self.msg = "autoindent off"
        elif opt.startswith("comment="):
            self.opt_comment = opt[8:]
            self.msg = f"comment={self.opt_comment}"
        elif opt.startswith("scrolloff="):
            try:
                val = int(opt[len("scrolloff="):])
                if val < 0:
                    raise ValueError
            except ValueError:
                self.msg = "scrolloff must be >= 0"
                return
            self.opt_scrolloff = val
            self.msg = f"scrolloff={val}"
        elif opt.startswith("clipboard="):
            val = opt[len("clipboard="):]
            if val not in ("osc52", "auto", "off"):
                self.msg = "clipboard must be osc52, auto, or off"
                return
            self.opt_clipboard = val
            self.msg = f"clipboard={val}"
        elif opt.startswith("mouse="):
            val = opt[len("mouse="):]
            if val not in ("off", "scroll", "cursor", "visual"):
                self.msg = "mouse must be off, scroll, cursor, or visual"
                return
            self.opt_mouse = val
            term = getattr(self, "term", None)
            if term:
                term.set_mouse(val)
            self.msg = f"mouse={val}"
        elif opt.startswith("yankflash="):
            try:
                val = int(opt[len("yankflash="):])
                if val < 0:
                    raise ValueError
            except ValueError:
                self.msg = "yankflash must be >= 0"
                return
            self.opt_yankflash = val
            self.msg = f"yankflash={val}"
        elif opt == "delcopy":
            self.opt_delcopy = True
            self.msg = "delcopy on"
        elif opt == "nodelcopy":
            self.opt_delcopy = False
            self.msg = "delcopy off"
        elif opt == "wrapmove":
            self.opt_wrapmove = True
            self.msg = "wrapmove on"
        elif opt == "nowrapmove":
            self.opt_wrapmove = False
            self.msg = "wrapmove off"
        elif opt == "markdownfences":
            self.opt_markdownfences = True
            self.msg = "markdownfences on"
        elif opt == "nomarkdownfences":
            self.opt_markdownfences = False
            self.msg = "markdownfences off"
        elif opt == "autodetect":
            self.opt_autodetect = True
            self.msg = "autodetect on"
        elif opt == "noautodetect":
            self.opt_autodetect = False
            self.msg = "autodetect off"
        elif opt == "rghidden":
            self.opt_rghidden = True
            self.msg = "rghidden on"
        elif opt == "norghidden":
            self.opt_rghidden = False
            self.msg = "rghidden off"
        elif opt == "hlsearch":
            self.opt_hlsearch = True
            self.msg = "hlsearch on"
        elif opt == "nohlsearch":
            self.opt_hlsearch = False
            self.msg = "hlsearch off"
        elif opt.startswith("makeprg="):
            self.opt_makeprg = opt[len("makeprg="):]
            self.msg = f"makeprg={self.opt_makeprg}"
        else:
            self.msg = f"Unknown option: {opt}"

    def _exec_substitute(self, m):
        """Execute :[range]s/pat/repl/[g] substitute command."""
        range_spec = m.group(1)  # '%' or '10' or '10,20' or None
        start_str = m.group(2)   # first line number or None
        end_str = m.group(4)     # second line number or None
        pattern = m.group(6)
        replacement = m.group(7)
        flags_str = m.group(8) or ""

        # Determine line range
        if range_spec == "%":
            start_line = 0
            end_line = len(self.buf.lines) - 1
        elif start_str is not None:
            start_line = max(0, int(start_str) - 1)  # 1-indexed to 0-indexed
            if end_str is not None:
                end_line = min(int(end_str) - 1, len(self.buf.lines) - 1)
            else:
                end_line = start_line
        else:
            # No range: current line only
            start_line = self.cy
            end_line = self.cy

        try:
            pat = re.compile(pattern)
        except re.error as e:
            self.msg = f"Invalid regex: {e}"
            self.mode = Mode.NORMAL
            return

        global_flag = "g" in flags_str
        total_subs = 0

        self._snapshot()
        for line_idx in range(start_line, end_line + 1):
            line = self.buf.lines[line_idx]
            if global_flag:
                new_line, count = pat.subn(replacement, line)
            else:
                new_line, count = pat.subn(replacement, line, count=1)
            if count > 0:
                self.buf.lines[line_idx] = new_line
                total_subs += count

        if total_subs > 0:
            self.buf.dirty = True
            self.msg = f"{total_subs} substitution(s)"
        else:
            self._undo_stack.pop()  # remove no-op snapshot
            self.msg = "Pattern not found"
        self.mode = Mode.NORMAL

