#!/usr/bin/env bash
# Scan an exported transcript Markdown for secrets and PII before submission.
#
# Usage: scan_secrets.sh <path-to-export.md>
#
# WARNING: When findings are present, matched lines are printed to stdout —
# including the secret values themselves. That output ends up in your terminal
# scrollback and, if you run this inside Claude Code, in the conversation
# transcript. For real findings, run this in a separate terminal.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <path-to-export.md>" >&2
  exit 2
fi

FILE="$1"
if [[ ! -f "$FILE" ]]; then
  echo "File not found: $FILE" >&2
  exit 1
fi

blockers=0
minor=0

section() { printf '\n=== %s ===\n' "$1"; }

# `grep -c` outputs "0" AND exits 1 when there are no matches, so the naive
# `count=$(grep -c ... || echo 0)` produces "0\n0" which breaks `(( count > 0 ))`.
# These wrappers swallow grep's failure cleanly: empty stdout → default to "0".
count_matches() {
  local result
  result=$(grep -cEi "$1" "$FILE" 2>/dev/null || true)
  echo "${result:-0}"
}
count_matches_excluding() {
  local result
  result=$(grep -Ei "$1" "$FILE" 2>/dev/null | grep -cviE "$2" || true)
  echo "${result:-0}"
}

# Each scanner runs the regex twice: once for a true count (drives the verdict),
# once with -n | head for the truncated display. Earlier versions counted by
# `wc -l` of the displayed slice, which capped severity at 30.
run_scan() {
  local kind="$1"; shift   # blocker | minor
  local label="$1"; shift
  local pattern="$1"; shift
  local exclude="${1:-}"   # may be empty
  section "$label"

  local count display
  if [[ -n "$exclude" ]]; then
    count=$(count_matches_excluding "$pattern" "$exclude")
    display=$(grep -nEi "$pattern" "$FILE" 2>/dev/null | grep -viE "$exclude" | head -n 30 || true)
  else
    count=$(count_matches "$pattern")
    display=$(grep -nEi "$pattern" "$FILE" 2>/dev/null | head -n 30 || true)
  fi

  if (( count > 0 )); then
    echo "$display"
    if (( count > 30 )); then
      echo "… [$((count - 30)) more matches not shown]"
    fi
    if [[ "$kind" == "blocker" ]]; then
      blockers=$((blockers + count))
    else
      minor=$((minor + count))
    fi
  else
    echo "(none)"
  fi
}

# --- BLOCKERS: real secrets ---
#
# Pattern table format: kind|label|pattern. The patterns themselves contain `|`
# for regex alternation, so the parser splits only the first two `|` and treats
# the rest as the pattern. One scanner per logical category (not one
# mega-scanner) so the per-category count is meaningful and a typo in one row
# doesn't silently break another category's matching.

SCANNERS=(
  "blocker|OpenAI / Anthropic API keys|sk-[a-zA-Z0-9]{20,}"
  "blocker|Stripe API keys|(pk|sk)_(live|test)_[a-zA-Z0-9]{20,}"
  "blocker|AWS access key IDs|AKIA[0-9A-Z]{16}"
  "blocker|GitHub personal access tokens|ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{80,}"
  "blocker|Slack tokens|xox[bpoa]-[0-9]{10,}"
  "blocker|Google API keys|AIza[0-9A-Za-z_-]{35}"
  "blocker|Bearer / Authorization headers with credentials|bearer [a-zA-Z0-9._-]{20,}|authorization:\\s*[a-z]+ [a-zA-Z0-9._-]{20,}"
  "blocker|JWT tokens|eyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}"
  "blocker|Database connection strings with credentials|(postgres(ql)?|mysql|mongodb(\\+srv)?|redis)://[^/[:space:]]*:[^@[:space:]]+@"
  "blocker|Env-var assignments with secret-like names|(SECRET|API_KEY|PRIVATE_KEY|PASSWORD|TOKEN|CREDENTIAL)\\s*=\\s*[A-Za-z0-9_/+=-]{16,}"
  "blocker|Stripe IDs (real, 24+ chars after prefix)|(cus|sub|pi|price)_[A-Za-z0-9]{24,}"
  "blocker|Clerk IDs (real, 20+ chars after prefix)|(user|org)_[A-Za-z0-9]{20,}"
)

for entry in "${SCANNERS[@]}"; do
  kind="${entry%%|*}"; rest="${entry#*|}"
  label="${rest%%|*}"; pattern="${rest#*|}"
  run_scan "$kind" "$label" "$pattern"
done

# --- MINOR: review-before-submit findings ---

run_scan "minor" "Email addresses (excluding common test fixtures)" \
  '[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' \
  '@(example\.com|noreply|anthropic\.com|test\.com)|^a@b\.com$'

run_scan "minor" "Public IP addresses (excluding loopback / broadcast)" \
  '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' \
  '127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255'

run_scan "minor" "Local file paths revealing username" \
  '/Users/[a-zA-Z0-9._-]+/' \
  '/Users/founder|/Users/example|/Users/test'

# Phone-number scan: filter out hits inside hex sequences (real UUIDs, with or
# without dashes) or all-numeric fixture UUIDs.
section "Phone numbers (UUID-substring false positives filtered)"
phone_pattern='(\+?1[ -]?)?\(?[0-9]{3}\)?[ -]?[0-9]{3}[ -]?[0-9]{4}'
uuid_filter='[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}|[a-f0-9]{32,}|[0-9]{4,}-[0-9]{4,}-[0-9]{4,}|0{8,}'
phone_count=$(count_matches_excluding "$phone_pattern" "$uuid_filter")
phone_display=$(grep -niE "$phone_pattern" "$FILE" 2>/dev/null | grep -viE "$uuid_filter" | head -n 10 || true)
if (( phone_count > 0 )); then
  echo "$phone_display"
  if (( phone_count > 10 )); then echo "… [$((phone_count - 10)) more]"; fi
  minor=$((minor + phone_count))
else
  echo "(none / all matches were UUID substrings)"
fi

# High-entropy scan: 32+ alphanum tokens with BOTH letters AND digits (rules
# out camelCase identifiers, all-letter constraint names, and pure-numeric IDs).
section "High-entropy strings (32+ chars, letters+digits, no separators) — review for keys"
entropy_hits=$(grep -nE '\b[A-Za-z0-9]{32,}\b' "$FILE" 2>/dev/null | awk -F: '
  {
    line_no = $1
    rest = substr($0, length($1) + 2)
    while (match(rest, /[A-Za-z0-9]{32,}/)) {
      token = substr(rest, RSTART, RLENGTH)
      if (token ~ /[A-Za-z]/ && token ~ /[0-9]/) {
        print line_no ":" token
        break
      }
      rest = substr(rest, RSTART + RLENGTH)
    }
  }' || true)
# Count non-empty lines without grep's exit-1-on-no-match noise.
if [[ -z "$entropy_hits" ]]; then
  entropy_count=0
else
  entropy_count=$(printf '%s\n' "$entropy_hits" | wc -l | tr -d ' ')
fi
if (( entropy_count > 0 )); then
  echo "$entropy_hits" | head -n 30
  if (( entropy_count > 30 )); then echo "… [$((entropy_count - 30)) more]"; fi
  minor=$((minor + entropy_count))
else
  echo "(none)"
fi

# --- Verdict ---

section "VERDICT"

# Portable sed -i: BSD (macOS) requires a backup-suffix arg, GNU does not.
# `-i.bak` + cleanup works on both.
suggest_anonymize() {
  local user
  user="$(whoami)"
  echo ""
  echo "Tip: anonymize local username with:"
  echo "  sed -i.bak 's|/Users/${user}|/Users/founder|g' \"$FILE\" && rm \"$FILE.bak\""
}

if (( blockers > 0 )); then
  echo "BLOCKERS: $blockers — DO NOT SUBMIT until resolved"
  exit 3
elif (( minor > 0 )); then
  echo "MINOR: $minor finding(s) — review before submitting"
  suggest_anonymize
  exit 0
else
  echo "CLEAN — safe to submit"
  exit 0
fi
