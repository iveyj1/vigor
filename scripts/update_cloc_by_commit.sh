#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(git -C "$script_dir" rev-parse --show-toplevel)
out=${1:-"$script_dir/cloc_by_commit.md"}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

{
  printf '### Runtime Python cloc by commit\n\n'
  printf 'Generated with `scripts/cloc.pl` against `vigor/*.py`, or the historical `vig.py`/`ved.py` runtime.\n\n'
  printf '| Commit | Code | Blank | Comment | Added | Subject |\n'
  printf '|---|---:|---:|---:|---:|---|\n'

  prev_code=0
  for c in $(git -C "$repo_dir" rev-list --reverse --abbrev-commit HEAD); do
    rm -rf "$tmp/runtime"
    mkdir -p "$tmp/runtime"
    files=$(git -C "$repo_dir" ls-tree -r --name-only "$c" -- vigor 2>/dev/null \
      | grep -E '^vigor/.*\.py$' || true)
    if [[ -n "$files" ]]; then
      while IFS= read -r file; do
        dest="$tmp/runtime/${file#vigor/}"
        mkdir -p "$(dirname "$dest")"
        git -C "$repo_dir" show "$c:$file" > "$dest"
      done <<< "$files"
    elif git -C "$repo_dir" show "$c:vig.py" > "$tmp/runtime/vig.py" 2>/dev/null; then
      :
    elif git -C "$repo_dir" show "$c:ved.py" > "$tmp/runtime/ved.py" 2>/dev/null; then
      :
    else
      continue
    fi
    stats=$(perl "$script_dir/cloc.pl" --quiet "$tmp/runtime" \
      | awk '$1=="Python" {print $5"|"$3"|"$4}')
    code=${stats%%|*}
    rest=${stats#*|}
    blank=${rest%%|*}
    comment=${rest#*|}
    added=$((code - prev_code))
    prev_code=$code
    subj=$(git -C "$repo_dir" log -1 --format=%s "$c" | sed 's/|/\\|/g')
    printf '| `%s` | %s | %s | %s | %s | %s |\n' "$c" "$code" "$blank" "$comment" "$added" "$subj"
  done
} > "$out"

printf 'Wrote %s\n' "$out"
