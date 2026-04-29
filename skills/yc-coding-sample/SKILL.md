---
name: yc-coding-sample
description: >-
  Find, score, and package your strongest Claude Code session as a
  YC-application coding sample. Walks through a 6-phase pipeline with a user
  checkpoint between every phase — shortlist candidates from ~/.claude/projects,
  deep-dive top picks, independent YC-investor scoring, comparative ranking,
  Markdown export, and security scan. Use when the user mentions YC
  (Y Combinator) alongside a coding sample, a coding session, a transcript,
  a Claude Code session, or "best work". Also triggers on /yc-coding-sample.
  Do NOT auto-trigger on generic portfolio talk, interview prep, or code review
  when YC is not mentioned.
license: MIT
metadata:
  version: "0.1.0"
---

# yc-coding-sample

Six-phase pipeline. **Stop and wait for user confirmation between every phase.** Never auto-complete the full run.

The skill directory is the directory containing this `SKILL.md`. All script paths below are relative to that directory.

## When NOT to trigger

Skip this skill if the user is:

- Asking a generic portfolio / interview-prep question without mentioning YC.
- Asking how to export *the current* session (use Claude Code's `/export` instead).
- Asking for a generic code review of a session (use a code-review skill instead).
- Asking how to find a file in `~/.claude/projects/` for any other purpose.

If unsure, ask: "Is this for a YC application specifically?" before running.

## Phase 1 — Shortlist

Goal: surface 6–10 ranked candidate sessions from the user's local Claude Code history.

There is no script for this — do it inline using `find`, `stat`, `jq`, and `sort`. This phase is intentionally model-driven so you can adapt the filter to the user's situation (e.g., dedupe sessions in the same workspace, exclude reviewer-only sessions, narrow to one product).

### Defaults

| Filter | Default | Why |
|---|---|---|
| Recency | last 30 days | recent enough to be honest about current skill |
| Min size | ≥500 KB | smaller sessions are usually trivial Q&A |
| Top N | 8 | enough to choose from, few enough to scan |
| Workspace glob | every dir under `~/.claude/projects/` | broad scan; user can narrow |
| Rank | size DESC, mtime DESC tiebreaker | within a recency window, size correlates with substance much better than mtime — users open many small churn sessions per day |

Honor explicit overrides if the user mentions them ("only the last 7 days", "just my Threadline workspaces", "rank by recency").

### Procedure

1. **Find candidates.** Use a single Bash call. Compute the cutoff with `date` (BSD `date -v-Nd` falls back to GNU `date -d 'N days ago'`) so `find -newermt` gets an ISO date — the natural-language form is rejected by `bfs` and other find variants:

   ```bash
   CUTOFF=$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d '30 days ago' +%Y-%m-%d)
   find ~/.claude/projects -type f -name '*.jsonl' -newermt "$CUTOFF" -size +500k -print0 \
   | while IFS= read -r -d '' f; do
       size=$(stat -f %z "$f" 2>/dev/null || stat -c %s "$f")
       mtime=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f")
       printf '%s\t%s\t%s\n' "$size" "$mtime" "$f"
     done \
   | sort -k1,1nr -k2,2nr | head -8
   ```

2. **Sample one line per candidate** (first plain-string user message, stripped of `<system_instruction>`-style wrappers). Note the doubled backslashes — jq's regex literals require them when invoked via the shell:

   ```bash
   jq -rs '
     def strip_tags: gsub("(?s)<(?<t>[\\w-]+)\\b[^>]*>.*?</\\k<t>>"; "");
     map(select(.type=="user" and .isMeta != true and (.message.content|type)=="string"))
     | first | (.message.content // "")
     | strip_tags | gsub("\\s+"; " ") | ltrimstr(" ") | .[0:100]
   ' "$path"
   ```

3. **Render a Markdown table** with columns: Rank, Date, Size, Workspace (the dirname's trailing segment), Path, Opening prompt.

4. **Stop and ask the user to pick 1–3 sessions for deep-dive.** Do not proceed without an answer.

### Edge cases

- **No candidates** — suggest loosening filters: `--days 60`, `--min-size 200`, or a custom workspace glob.
- **All candidates from the same workspace** — offer to dedupe or to widen the search; one workspace dominating the list often means the user has a tight feedback loop on one product, and you should surface variety.
- **Brand-new account** — if `~/.claude/projects/` is empty or all sessions are tiny, ask if there's a different machine where they've been working.

## Phase 2 — Deep dive

For each chosen session, spawn an `Explore` agent (parallel if multiple). Use this prompt template:

> Read the JSONL session log at `<absolute path>`. The user is preparing this for a YC application and wants both AI-collaboration and software-engineering signals surfaced. Sample beginning, middle, and end — do not read linearly. Use `jq` to filter user messages, assistant text, and tool-use stats.
>
> Report under 600 words covering: (1) opening problem statement (quote first user message); (2) narrative arc — exploration → design → implementation → debugging → verification; (3) technical substance — specific files, components, libraries, 3–5 representative changes; (4) AI collaboration highlights — subagent use, parallel calls, course corrections, quality of specs; (5) engineering quality signals — tests, commits, debugging discipline; (6) final outcome — last meaningful state, commit hash, "tests pass" message; (7) length stats; (8) 2–3 standout moments with verbatim quotes; (9) weaknesses or dead ends.

Aggregate into a comparative summary if multiple sessions.

**Stop. Ask the user to confirm the lead candidate.**

## Phase 3 — Independent YC-investor review

Spawn a fresh `general-purpose` agent — **no prior briefing, no opinions from earlier in this conversation**. The agent must form its opinion from the file alone.

Prompt template:

> You are role-playing as a YC partner reviewing a coding session transcript that a founder has submitted as evidence of their technical chops. Be honest, specific, and slightly skeptical — YC partners are pattern-matchers who've seen thousands of founders, not cheerleaders.
>
> The artifact: `<absolute path>` — JSONL Claude Code session log. Founder is `<name>`. Project is `<one-line project description>`.
>
> You have not been briefed by anyone else. Form your own opinion from the file.
>
> [Insert the rubric from `references/yc-rubric.md` verbatim here.]
>
> Length: ~700–900 words. Specific quotes beat generic praise. Direct, slightly skeptical. No fluff intro/outro.

**Stop. Confirm the review with the user.**

## Phase 4 — Comparative ranking *(optional)*

If the shortlist had multiple strong candidates, run Phase 3 against each of them so scoring is calibrated against the same rubric.

### How to spawn the agents — read this carefully

**Run them in parallel. In a single tool-call block.**

Concretely: in one assistant message, emit multiple `Agent` tool invocations — one per remaining session — and stop the message. The runtime executes them concurrently; you receive all results together when they finish. **Do not** await one before sending the next. **Do not** call them in series across multiple messages.

Why this matters:

- **Calibration integrity.** The agents must form independent opinions. If they see each other's scores, the second agent anchors on the first and the comparison is meaningless.
- **Wall-clock time.** Sequential spawns multiply latency by N. A 4-session compare takes ~4 minutes serial vs. ~1 minute parallel.
- **It's a teaching moment for the user.** This skill exists to surface AI-collaboration craft. Demonstrating the right shape here is part of the value — not a footnote.

If you find yourself reaching for `await` between agent spawns, stop and reconsider — the only reason to serialize agents is when the second's prompt depends on the first's output. That is not the case here.

Present a comparison table: rank | session | composite | tier | strongest signal one-liner.

**Stop. Ask whether to proceed with the originally chosen lead or switch.**

## Phase 5 — Export

Convert the chosen JSONL to readable Markdown:

```bash
python3 scripts/jsonl_to_md.py <jsonl-path> <output-path> [--title "Custom Session Title"]
```

Default output path: `~/Desktop/yc-<workspace>-session.md` where `<workspace>` is the trailing component of the workspace path (e.g., `dallas`, `baghdad`).

The script:

- Strips `<system_instruction>`, `<system-instruction>`, `<system-reminder>`, and `<local-command-caveat>` blocks from user content.
- Renders `<command-name>` blocks as inline `` `/command` ``.
- Renders tool calls per type: `Bash` (description + fenced shell), `Edit` (as diff), `Read` (path + offset/limit), `Write` (path + truncated content), `Grep`/`Glob` (pattern), `Agent` (subagent_type + description + truncated prompt), `TodoWrite` (checkbox list), `TaskCreate`/`TaskUpdate` (compact JSON), generic fallback for others.
- Renders `thinking` blocks inside `<details>` collapsibles.
- Renders tool results inside `<details>` collapsibles, truncated to 2500 chars.
- Skips `system`, `attachment`, `queue-operation`, `last-prompt`, `ai-title` events and sidechain (subagent internal) turns.
- Header includes title (from `aiTitle` event or `--title` flag), workspace, branch, start/end timestamps, total event count.

**Stop. Ask the user to inspect the file.** Suggest `open <path>`.

## Phase 6 — Security scan

```bash
python3 scripts/scan_secrets.py <output-path>
```

Reports findings grouped by category, each with line numbers and ≤30 results. Final verdict line is one of: `BLOCKERS — do not submit`, `MINOR — review before submitting`, `CLEAN — safe to submit`. Exit code 3 when blockers are found, 0 otherwise.

If `BLOCKERS` or `MINOR` findings present, suggest the anonymization one-liner. Use the portable form (BSD `sed -i ''` and GNU `sed -i` differ — `-i.bak` + cleanup works on both):

```bash
sed -i.bak 's|/Users/<actual-username>|/Users/founder|g' <output-path> && rm <output-path>.bak
```

Tell the user this is the final step. The file is ready to submit.

## Defaults to remember

- Output directory: `~/Desktop/`.
- Rubric weights: judgment 20, AI-collab 20, velocity 20, debugging 15, product taste 15, communication 5, founder-signal 5.
- Tier thresholds: ≥85 Top 5%, ≥75 Top 10%, ≥60 Top 25%, ≥45 Median, <45 Below bar.

Phase 1 filters live in this file (above). Rubric is in `references/yc-rubric.md`.
