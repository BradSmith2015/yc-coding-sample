# yc-coding-sample

A Claude Code skill that prepares a real coding session transcript for a YC application. It picks the strongest candidate from your local session history, scores it through the eyes of a YC partner, exports it as readable Markdown, and runs a security sweep before you submit.

## What it does

A six-phase pipeline with a user checkpoint between every phase — the skill never auto-completes the full run.

1. **Shortlist.** Surveys `~/.claude/projects/`, ranks recent sessions by size, recency, and substance. Outputs a Markdown table of 6–10 candidates.
2. **Deep dive.** For 1–3 sessions you pick, an `Explore` subagent reads the JSONL and summarizes the problem, narrative arc, technical substance, AI-collaboration moments, outcome, and weaknesses.
3. **Independent YC-investor review.** A fresh, un-briefed `general-purpose` subagent reads the JSONL with the rubric in `references/yc-rubric.md` and scores 7 dimensions, gives a composite score, a YC tier verdict, the strongest and weakest signals as verbatim quotes, and two interview questions tied to specific transcript moments.
4. **Comparative ranking** *(optional)*. Run Phase 3 in parallel across the rest of the shortlist for calibrated scoring.
5. **Export.** `scripts/jsonl_to_md.py` converts the chosen JSONL to Markdown — system instructions stripped, tool calls inlined, Edits as diffs, tool results in collapsibles.
6. **Security scan.** `scripts/scan_secrets.sh` sweeps the export for API keys, tokens, connection strings, env values, real emails, IPs, Stripe/Clerk IDs, and PII. Reports blockers vs. minor concerns.

## Why

Surfacing the right Claude Code session out of dozens or hundreds of workspaces is a real problem when you're applying to YC. Picking by hand without an investor's lens means you ship a session that *feels* impressive but isn't the strongest signal you have. This skill packages the workflow so the next iteration is one command.

## Install

### Via `npx skills` (recommended)

```bash
npx skills add BradSmith2015/yc-coding-sample -g -y
```

`-g` installs globally to `~/.claude/skills/`, `-y` skips confirmation prompts. The CLI clones the repo, finds the `SKILL.md` under `skills/yc-coding-sample/`, and symlinks it into your skills directory.

### Symlink (for local development / hacking on the skill)

```bash
git clone https://github.com/BradSmith2015/yc-coding-sample ~/code/yc-coding-sample
ln -s ~/code/yc-coding-sample/skills/yc-coding-sample ~/.claude/skills/yc-coding-sample
```

### Copy

```bash
cp -r ~/code/yc-coding-sample/skills/yc-coding-sample ~/.claude/skills/
```

After any install, run `/reload-plugins` in Claude Code so the skill is loaded.

## Usage

Explicit invocation only — the skill will not auto-trigger on generic portfolio or interview-prep talk.

```
use the yc-coding-sample skill to prep a YC submission
```

or

```
/yc-coding-sample
```

## Configuration

`scripts/shortlist.sh` accepts:

- `--days N` — only consider sessions modified in the last N days (default: 30)
- `--min-size KB` — minimum file size in KB (default: 500)
- `--top N` — how many candidates to surface (default: 8)
- `--workspace-glob 'pattern'` — narrow the search to a glob pattern under `~/.claude/projects/` (default: scan every project directory)
- `--rank-by size|recency` — sort the shortlist by file size (default — better proxy for substance) or by mtime

## Customizing the rubric

Edit `yc-coding-sample/references/yc-rubric.md` to retune dimension weights or tier thresholds. Defaults: judgment 20, AI-collab 20, velocity 20, debugging 15, product taste 15, communication 5, founder-signal 5. Tier thresholds: ≥85 Top 5%, ≥75 Top 10%, ≥60 Top 25%, ≥45 Median, <45 Below bar.

## Out of scope

- No GitHub auto-push, no auto-submit-to-YC. The skill stops at "ready to submit."
- No PDF or non-Markdown output.
- No scheduled / cron use. Manual invocation only.

## License

MIT. See `LICENSE`.
