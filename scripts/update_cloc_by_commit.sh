#!/usr/bin/env bash
set -euo pipefail

out=${1:-cloc_by_commit.md}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

{
  printf '### vig.py cloc by commit\n\n'
  printf 'Generated with `perl cloc.pl` against each commit version of `vig.py` (or historical `ved.py`).\n\n'
  printf '| Commit | Code | Blank | Comment | Added | Subject |\n'
  printf '|---|---:|---:|---:|---:|---|\n'

  prev_code=0
  for c in $(git rev-list --reverse --abbrev-commit HEAD); do
    if git show "$c:vig.py" > "$tmp/file.py" 2>/dev/null \
      || git show "$c:ved.py" > "$tmp/file.py" 2>/dev/null; then
      stats=$(perl cloc.pl --quiet "$tmp/file.py" \
        | awk '$1=="Python" {print $5"|"$3"|"$4}')
      code=${stats%%|*}
      rest=${stats#*|}
      blank=${rest%%|*}
      comment=${rest#*|}
      added=$((code - prev_code))
      prev_code=$code
      subj=$(git log -1 --format=%s "$c" | sed 's/|/\\|/g')
      printf '| `%s` | %s | %s | %s | %s | %s |\n' "$c" "$code" "$blank" "$comment" "$added" "$subj"
    fi
  done
} > "$out"

printf 'Wrote %s\n' "$out"
