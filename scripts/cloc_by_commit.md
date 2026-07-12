### vig.py cloc by commit

Generated with `perl cloc.pl` against each commit version of `vig.py` (or historical `ved.py`).

| Commit | Code | Blank | Comment | Added | Subject |
|---|---:|---:|---:|---:|---|
| `5745f9c` | 759 | 79 | 89 | 759 | minimal vi-inspired editor in python.  Works including basic motions, dycpv operators |
| `9b7e6af` | 720 | 84 | 91 | -39 | fix cursor, refactor |
| `651a532` | 792 | 88 | 99 | 72 | Phase 10: search (/ ? n N) with regex, SEARCH mode, 6 tests |
| `52aedcf` | 842 | 96 | 103 | 50 | Phase 11: substitute :[range]s/pat/repl/[g], 6 tests |
| `9866242` | 930 | 103 | 111 | 88 | Phase 12: line wrap (:set wrap/nowrap), wrap-aware rendering and scroll, 4 tests |
| `f234314` | 969 | 105 | 113 | 39 | Phase 13: line numbers (:set number/relativenumber), substitute delimiter fix, 4 tests |
| `c42ae30` | 971 | 105 | 113 | 2 | Phase 14: arrow keys in insert mode, fix test harness CSI sequence delivery, 2 tests |
| `f0e2dcc` | 971 | 105 | 113 | 0 | updated document and added management section |
| `04a324e` | 971 | 105 | 113 | 0 | Remove accidentally committed __pycache__ |
| `286635e` | 1050 | 111 | 122 | 79 | Phase 15: undo/redo with full-buffer snapshots |
| `a389002` | 1625 | 140 | 182 | 575 | Phases 17-27: gg/G/0 motions, f/t/F/T/;/, find-char, >>/<<indent, autoindent, % bracket match, o/O open line, iw/iW/aw/aW word objects, bracket/quote text objects, gcc/gc comment toggle, dot repeat, :read/:/exitead ! |
| `22ad344` | 1614 | 141 | 183 | -11 | Refactor 1: Extract _enter_op_pending() helper |
| `d12dbdf` | 1620 | 141 | 183 | 6 | Refactor 2: Init all pending flags in __init__, drop hasattr |
| `266b390` | 1609 | 142 | 183 | -11 | Refactor 3: Deduplicate _exec_find/_repeat_find dispatch |
| `65ce880` | 1607 | 143 | 184 | -2 | Refactor 4: Extract _open_line(below) helper |
| `fdcfca8` | 1598 | 143 | 184 | -9 | Refactor 5: Route find motions through _exec_operator |
| `aa2d3fe` | 1596 | 143 | 185 | -2 | Refactor 6: Share pending flags between normal and visual |
| `8bcfe1e` | 1580 | 143 | 185 | -16 | Refactor 7: Consolidate render wrap paths |
| `1c9b310` | 1714 | 150 | 194 | 134 | Phase 28: Multi-buffer support |
| `3b4520b` | 1688 | 151 | 194 | -26 | Refactor: extract _close_buffer, deduplicate :q/:k, add :qall/:qall! |
| `8140a7d` | 1717 | 152 | 196 | 29 | Phase 29: x/X delete and space-leader |
| `e4f3751` | 1726 | 152 | 196 | 9 | Phase 29.5: Refactor motion keys - add ^, $, Home, End to _MOTION_KEYS |
| `355d2c8` | 1739 | 163 | 196 | 13 | Refactor motion dispatch and add phase test selection |
| `fa48ed8` | 1749 | 163 | 196 | 10 | Phase 30: add ^/$, Home/End, Tab, and Delete support |
| `0461c16` | 1771 | 164 | 197 | 22 | Phase 31: add J line join and visual ^/$ coverage |
| `9c41142` | 1782 | 166 | 199 | 11 | Phase 32: resolve :e/:w and argv paths with ~ and buffer-relative rules |
| `56e7acd` | 1794 | 168 | 199 | 12 | Phase 33: add Ctrl-D/Ctrl-U half-page motions |
| `d6754c7` | 1815 | 168 | 199 | 21 | Phase 34: add scrolloff option and margin-aware scrolling |
| `cafadab` | 1828 | 168 | 200 | 13 | Fix Home/End SS3 decode and ; repeat for t/T motions |
| `01126cb` | 1832 | 168 | 200 | 4 | Fix home and end for various terminals |
| `329c3cd` | 1847 | 168 | 200 | 15 | Fix :w/:wq write errors to avoid crash and report message |
| `6042e93` | 1847 | 168 | 200 | 0 | delete asdf and foo |
| `44c772d` | 1893 | 171 | 203 | 46 | Phase 35: add clipboard modes and default to auto |
| `4847417` | 1938 | 171 | 204 | 45 | add crash log |
| `689ff40` | 1968 | 171 | 207 | 30 | fix word boundary crash bug w W b B e E and associated tests and diagnostics |
| `1554ef7` | 1968 | 171 | 207 | 0 | interim AGENTS changes |
| `578da32` | 1968 | 171 | 207 | 0 | update specs |
| `6cd1455` | 2029 | 177 | 211 | 61 | Implement small editor fixes from todo |
| `93becd1` | 2032 | 177 | 211 | 3 | Finish todo fixes for dw and suspension docs |
| `3e3ce22` | 2053 | 177 | 211 | 21 | Fix Ctrl-Z suspension and directory edit errors |
| `f690e83` | 2074 | 177 | 213 | 21 | Improve Ctrl-Z job control suspension |
| `a72f356` | 2074 | 177 | 213 | 0 | Remove notes file |
| `bb57cf4` | 2077 | 177 | 213 | 3 | Horizontally scroll current line at right edge |
| `7087262` | 2076 | 177 | 213 | -1 | Apply horizontal scroll to full window |
| `9d1427f` | 2076 | 177 | 213 | 0 | install script, update todo.md |
| `8184aac` | 2122 | 182 | 218 | 46 | Add quit aliases and startup config |
| `b195bc5` | 2122 | 182 | 218 | 0 | add cloc script |
| `4ce6d85` | 2245 | 186 | 222 | 123 | Add ripgrep quickfix buffer |
| `86ca3f9` | 2245 | 186 | 222 | 0 | rename to vigor/vig from ved |
| `5c0ef02` | 2312 | 191 | 226 | 67 | Implement todo items 1-5 |
| `e81851f` | 2331 | 191 | 226 | 19 | Clear yank highlight on timer |
| `fe88fb0` | 2331 | 191 | 226 | 0 | add cloc, fix update_cloc_by_commit.sh |
| `2e68b02` | 2331 | 191 | 227 | 0 | Allow r to replace with digits |
| `d67f8cd` | 2331 | 191 | 227 | 0 | update cloc |
| `dc0563f` | 2383 | 193 | 224 | 52 | Implement current todo items |
| `161a708` | 2383 | 193 | 224 | 0 | update cloc script, todo |
| `8003884` | 2435 | 195 | 225 | 52 | Add bracketed paste support |
| `d1897a7` | 2437 | 195 | 225 | 2 | Implement current todo items |
| `265d215` | 2519 | 199 | 225 | 82 | Add command completion and history |
| `dab7ad6` | 2578 | 206 | 225 | 59 | Add completion menu |
| `e4bcefc` | 2589 | 207 | 226 | 11 | Center completion menu |
