#!/usr/bin/env bash
# Survey ~/.claude/projects/ and surface the strongest candidate sessions.
#
# Usage: shortlist.sh [--days N] [--min-size KB] [--top N] [--workspace-glob 'pattern'] [--rank-by size|recency]
set -euo pipefail
shopt -s nullglob   # glob with no matches expands to empty, not literal pattern

DAYS=30
MIN_SIZE_KB=500
TOP=8
RANK_BY=size  # size | recency — within the recency window, size correlates with substance
# Default: every project directory under ~/.claude/projects/. Narrow with --workspace-glob.
WORKSPACE_GLOBS=("${HOME}/.claude/projects/"*)
CUSTOM_GLOB=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2;;
    --min-size) MIN_SIZE_KB="$2"; shift 2;;
    --top) TOP="$2"; shift 2;;
    --workspace-glob) CUSTOM_GLOB="$2"; shift 2;;
    --rank-by) RANK_BY="$2"; shift 2;;
    -h|--help) sed -n '2,5p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

case "$RANK_BY" in
  size|recency) ;;
  *) echo "Unknown --rank-by: $RANK_BY (expected: size | recency)" >&2; exit 2;;
esac

if [[ -n "$CUSTOM_GLOB" ]]; then
  WORKSPACE_GLOBS=("${HOME}/.claude/projects/${CUSTOM_GLOB}")
fi

if ! command -v jq >/dev/null; then
  echo "shortlist.sh requires jq. Install with: brew install jq  (or apt install jq)" >&2
  exit 1
fi

# Portable date helpers: BSD (macOS) vs GNU (Linux).
days_ago_epoch() { date -v-"$1"d +%s 2>/dev/null || date -d "$1 days ago" +%s; }
epoch_to_date()  { date -r "$1" +%Y-%m-%d 2>/dev/null || date -d "@$1" +%Y-%m-%d; }
file_size()      { stat -f %z "$1" 2>/dev/null || stat -c %s "$1"; }
file_mtime()     { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1"; }

CUTOFF_EPOCH=$(days_ago_epoch "$DAYS")
MIN_SIZE_BYTES=$(( MIN_SIZE_KB * 1024 ))

candidates=()
for pattern in "${WORKSPACE_GLOBS[@]}"; do
  for dir in $pattern; do  # unquoted to glob-expand
    [[ -d "$dir" ]] || continue
    for jsonl in "$dir"/*.jsonl; do
      [[ -f "$jsonl" ]] || continue
      mtime=$(file_mtime "$jsonl")
      size=$(file_size "$jsonl")
      (( mtime >= CUTOFF_EPOCH )) || continue
      (( size >= MIN_SIZE_BYTES )) || continue
      candidates+=("$mtime|$size|$jsonl")
    done
  done
done

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No candidates found. Try --days 60, --min-size 200, or --workspace-glob 'your-pattern-*'." >&2
  exit 0
fi

# Pick sort keys based on --rank-by, then take top N. `awk 'NR<=N'` doesn't
# SIGPIPE its input, so we avoid the pipefail dance that `sort | head` requires.
if [[ "$RANK_BY" == "size" ]]; then
  sort_keys=(-t'|' -k2,2nr -k1,1nr)
else
  sort_keys=(-t'|' -k1,1nr -k2,2nr)
fi
ranked=$(printf '%s\n' "${candidates[@]}" | sort "${sort_keys[@]}" | awk -v n="$TOP" 'NR<=n')

# Markdown table.
printf '| Rank | Date | Size | Workspace | Path | Opening prompt |\n'
printf '|-----:|------|------|-----------|------|----------------|\n'

rank=0
while IFS='|' read -r mtime size path; do
  rank=$((rank + 1))
  date_str=$(epoch_to_date "$mtime")
  size_mb=$(awk -v s="$size" 'BEGIN{printf "%.1fMB", s/1024/1024}')
  workspace=$(basename "$(dirname "$path")")
  workspace=${workspace#-Users-*-conductor-workspaces-}

  # Pull first plain-string user message via jq, strip wrapping <tag>...</tag>
  # blocks (system instructions, command-name, local-command-stdout, etc.),
  # collapse whitespace, take first 100 chars. All in jq — no perl/tr/sed pipe.
  first_user=$(jq -rs '
    def strip_tags: gsub("(?s)<(?<t>[\\w-]+)\\b[^>]*>.*?</\\k<t>>"; "");
    def collapse_ws: gsub("\\s+"; " ") | ltrimstr(" ");
    map(select(.type=="user" and .isMeta != true and (.message.content|type)=="string"))
    | first
    | (.message.content // "")
    | strip_tags | collapse_ws | .[0:100]
  ' "$path" 2>/dev/null || echo "")
  [[ -z "$first_user" ]] && first_user="(no plain-text user message)"
  first_user=$(echo "$first_user" | sed 's/|/\\|/g')   # escape pipes for Markdown

  printf '| %s | %s | %s | %s | `%s` | %s |\n' "$rank" "$date_str" "$size_mb" "$workspace" "$path" "$first_user"
done <<< "$ranked"
