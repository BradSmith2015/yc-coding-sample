#!/usr/bin/env python3
"""Scan a Markdown export for secrets and PII before YC submission.

Usage:
    scan_secrets.py <path-to-export.md>

WARNING: When findings are present, matched values are printed to stdout —
including the secret values themselves. That output ends up in your terminal
scrollback and, if you run this inside Claude Code, in the conversation
transcript. For real findings, run this in a separate terminal.

Exit codes:
    0 — clean, or only minor findings (review before submitting)
    1 — file not found
    2 — bad usage
    3 — blockers found (do not submit)
"""
import argparse
import getpass
import re
import sys
from pathlib import Path

# (label, pattern). Each entry is one logical category — splitting the old
# API-keys mega-row by provider so per-category counts are meaningful and a
# typo in one row can't silently break adjacent matching.
BLOCKERS = [
    ("OpenAI / Anthropic API keys", r"sk-[a-zA-Z0-9]{20,}"),
    ("Stripe API keys", r"(?:pk|sk)_(?:live|test)_[a-zA-Z0-9]{20,}"),
    ("AWS access key IDs", r"AKIA[0-9A-Z]{16}"),
    ("GitHub personal access tokens", r"ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{80,}"),
    ("Slack tokens", r"xox[bpoa]-[0-9]{10,}"),
    ("Google API keys", r"AIza[0-9A-Za-z_-]{35}"),
    ("Bearer / Authorization headers with credentials",
     r"bearer [a-zA-Z0-9._-]{20,}|authorization:\s*[a-z]+ [a-zA-Z0-9._-]{20,}"),
    ("JWT tokens",
     r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("Database connection strings with credentials",
     r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^/\s]*:[^@\s]+@"),
    ("Env-var assignments with secret-like names",
     r"(?:SECRET|API_KEY|PRIVATE_KEY|PASSWORD|TOKEN|CREDENTIAL)\s*=\s*[A-Za-z0-9_/+=\-]{16,}"),
    ("Stripe IDs (real, 24+ chars after prefix)",
     r"(?:cus|sub|pi|price)_[A-Za-z0-9]{24,}"),
    ("Clerk IDs (real, 20+ chars after prefix)",
     r"(?:user|org)_[A-Za-z0-9]{20,}"),
]

# (label, pattern, exclude_pattern_or_None)
MINORS = [
    ("Email addresses (excluding common test fixtures)",
     r"[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
     r"@(?:example\.com|noreply|anthropic\.com|test\.com)|^a@b\.com$"),
    ("Public IP addresses (excluding loopback / broadcast)",
     r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b",
     r"127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255"),
    ("Local file paths revealing username",
     r"/Users/[a-zA-Z0-9._-]+/",
     r"/Users/founder|/Users/example|/Users/test"),
]

# Phone numbers minus UUID-like substrings (real UUIDs, dashed or undashed,
# all-numeric fixture UUIDs, runs of zeros).
PHONE_PATTERN = r"(?:\+?1[ -]?)?\(?[0-9]{3}\)?[ -]?[0-9]{3}[ -]?[0-9]{4}"
UUID_FILTER = (
    r"[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}"
    r"|[a-f0-9]{32,}"
    r"|[0-9]{4,}-[0-9]{4,}-[0-9]{4,}"
    r"|0{8,}"
)


def find_lines(lines, pattern, exclude=None):
    pat = re.compile(pattern, re.IGNORECASE)
    excl = re.compile(exclude, re.IGNORECASE) if exclude else None
    hits = []
    for i, line in enumerate(lines, 1):
        if pat.search(line):
            if excl and excl.search(line):
                continue
            hits.append((i, line))
    return hits


def find_high_entropy(lines):
    """32+ alphanum tokens with BOTH letters AND digits (rules out camelCase,
    constraint names, all-numeric IDs)."""
    token_re = re.compile(r"[A-Za-z0-9]{32,}")
    has_letter = re.compile(r"[A-Za-z]")
    has_digit = re.compile(r"[0-9]")
    hits = []
    for i, line in enumerate(lines, 1):
        for token in token_re.findall(line):
            if has_letter.search(token) and has_digit.search(token):
                hits.append((i, token))
                break  # one report per line is enough
    return hits


def report(label, hits, max_show=30):
    """Print a section header and up to `max_show` hits. Return total count."""
    print(f"\n=== {label} ===")
    if not hits:
        print("(none)")
        return 0
    for line_no, content in hits[:max_show]:
        print(f"{line_no}:{content}")
    if len(hits) > max_show:
        print(f"… [{len(hits) - max_show} more matches not shown]")
    return len(hits)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", type=Path, help="Path to the exported Markdown")
    args = ap.parse_args()

    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    print(
        "WARNING: Matched secret values are printed below — they land in your "
        "terminal scrollback and any agent transcript. For real findings, run "
        "this in a separate terminal."
    )

    lines = args.file.read_text(errors="replace").splitlines()

    blockers = sum(report(label, find_lines(lines, pat)) for label, pat in BLOCKERS)
    minors = sum(report(label, find_lines(lines, pat, exclude=excl))
                 for label, pat, excl in MINORS)

    minors += report(
        "Phone numbers (UUID-substring false positives filtered)",
        find_lines(lines, PHONE_PATTERN, exclude=UUID_FILTER),
        max_show=10,
    )
    minors += report(
        "High-entropy strings (32+ chars, letters+digits, no separators) — review for keys",
        find_high_entropy(lines),
    )

    print("\n=== VERDICT ===")
    if blockers > 0:
        print(f"BLOCKERS: {blockers} — DO NOT SUBMIT until resolved")
        sys.exit(3)
    if minors > 0:
        user = getpass.getuser()
        print(f"MINOR: {minors} finding(s) — review before submitting")
        print()
        print("Tip: anonymize local username with:")
        print(
            f'  sed -i.bak \'s|/Users/{user}|/Users/founder|g\' '
            f'"{args.file}" && rm "{args.file}.bak"'
        )
        sys.exit(0)
    print("CLEAN — safe to submit")
    sys.exit(0)


if __name__ == "__main__":
    main()
