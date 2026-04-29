#!/usr/bin/env python3
"""Convert a Claude Code session JSONL into readable Markdown.

Usage:
    jsonl_to_md.py <input.jsonl> <output.md> [--title "Custom Title"]
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

# Truncation budgets — keep the rendered Markdown scannable.
MAX_BASH_CMD = 2000
MAX_EDIT_SIDE = 800
MAX_WRITE = 1500
MAX_AGENT_PROMPT = 1500
MAX_THINKING = 3000
MAX_TOOL_RESULT = 2500
MAX_GENERIC_INPUT = 1500

# One regex collapses four near-identical strippers (system_instruction,
# system-instruction, system-reminder, local-command-caveat). Backreference
# matches the closing tag's name.
WRAPPER_BLOCK_RE = re.compile(
    r"<(system_instruction|system-instruction|system-reminder|local-command-caveat)>.*?</\1>",
    re.DOTALL,
)
COMMAND_NAME_RE = re.compile(
    r"<command-name>(.*?)</command-name>\s*<command-message>.*?</command-message>\s*<command-args>(.*?)</command-args>",
    re.DOTALL,
)
LOCAL_CMD_OUT_RE = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.DOTALL)

# Keys whose values should be redacted in the generic tool-input fallback.
# Defends against unknown future tools that might accept secret-shaped inputs.
REDACT_KEY_RE = re.compile(r"(?i)(secret|token|password|authorization|api[-_]?key|credential)")

# Defense-in-depth: the export script runs before scan_secrets.sh and may show
# rendered output in places (terminals, IDE preview) before the scan happens.
# Redact known-shaped tokens inside Bash commands, Write content, and Edit
# bodies at render time. Patterns mirror the blockers in scan_secrets.sh.
TEXT_SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "<redacted-openai-or-anthropic-key>"),
    (re.compile(r"(?:pk|sk)_(?:live|test)_[a-zA-Z0-9]{20,}"), "<redacted-stripe-key>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted-aws-key>"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "<redacted-github-token>"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{80,}"), "<redacted-github-token>"),
    (re.compile(r"xox[bpoa]-[0-9]{10,}-[a-zA-Z0-9-]+"), "<redacted-slack-token>"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "<redacted-google-key>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "<redacted-jwt>"),
    (
        re.compile(r"(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://([^/\s:]+):([^@\s]+)@"),
        r"\1://\2:<redacted-password>@",
    ),
    (
        re.compile(r"((?:SECRET|API_KEY|PRIVATE_KEY|PASSWORD|TOKEN|CREDENTIAL)\s*=\s*)([A-Za-z0-9_/+=\-]{16,})"),
        r"\1<redacted>",
    ),
]


def redact_secrets_in_text(text: str) -> str:
    """Redact known-shaped tokens inside a free-form string."""
    if not text:
        return text
    for pattern, replacement in TEXT_SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def clean_user_text(text: str) -> str:
    text = WRAPPER_BLOCK_RE.sub("", text)

    def cmd_replace(m):
        name = m.group(1).strip()
        args = m.group(2).strip()
        return f"`/{name}{(' ' + args) if args else ''}`"

    text = COMMAND_NAME_RE.sub(cmd_replace, text)

    def out_replace(m):
        out = m.group(1).strip()
        if not out:
            return ""
        return f"\n```\n{out}\n```\n"

    text = LOCAL_CMD_OUT_RE.sub(out_replace, text)
    return text.strip()


def truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n… [truncated {len(s) - n} chars]"


def redact(value):
    """Walk a JSON value and replace secret-keyed entries with `<redacted>`."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if REDACT_KEY_RE.search(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _fmt_bash(_name, inp):
    cmd = redact_secrets_in_text(inp.get("command", ""))
    desc = inp.get("description", "")
    header = "**Bash**" + (f" — {desc}" if desc else "")
    return f"{header}\n```bash\n{truncate(cmd, MAX_BASH_CMD)}\n```"


def _fmt_read(_name, inp):
    path = inp.get("file_path", "")
    offset = inp.get("offset")
    limit = inp.get("limit")
    extra = f" (offset={offset}, limit={limit})" if offset is not None or limit is not None else ""
    return f"**Read** `{path}`{extra}"


def _fmt_edit(_name, inp):
    path = inp.get("file_path", "")
    old = truncate(redact_secrets_in_text(inp.get("old_string", "")), MAX_EDIT_SIDE)
    new = truncate(redact_secrets_in_text(inp.get("new_string", "")), MAX_EDIT_SIDE)
    old_lines = "\n".join(f"- {line}" for line in old.split("\n"))
    new_lines = "\n".join(f"+ {line}" for line in new.split("\n"))
    return f"**Edit** `{path}`\n```diff\n{old_lines}\n{new_lines}\n```"


def _fmt_write(_name, inp):
    path = inp.get("file_path", "")
    content = redact_secrets_in_text(inp.get("content", ""))
    return f"**Write** `{path}`\n```\n{truncate(content, MAX_WRITE)}\n```"


def _fmt_grep(_name, inp):
    return f"**Grep** `{inp.get('pattern', '')}` in `{inp.get('path', '')}`"


def _fmt_glob(_name, inp):
    return f"**Glob** `{inp.get('pattern', '')}`"


def _fmt_agent(_name, inp):
    desc = inp.get("description", "")
    sub = inp.get("subagent_type", "general-purpose")
    prompt = inp.get("prompt", "")
    return f"**Agent** ({sub}) — {desc}\n> {truncate(prompt, MAX_AGENT_PROMPT)}"


def _fmt_task(name, inp):
    return f"**{name}**\n```json\n{json.dumps(inp, indent=2)[:800]}\n```"


def _fmt_todowrite(_name, inp):
    todos = inp.get("todos", [])
    marks = {"completed": "x", "in_progress": "~", "pending": " "}
    lines = [f"- [{marks.get(t.get('status', ''), ' ')}] {t.get('content', '')}" for t in todos]
    return "**TodoWrite**\n" + "\n".join(lines)


def _fmt_generic(name, inp):
    safe = redact(inp)
    return f"**{name}**\n```json\n{truncate(json.dumps(safe, indent=2), MAX_GENERIC_INPUT)}\n```"


TOOL_FORMATTERS = {
    "Bash": _fmt_bash,
    "Read": _fmt_read,
    "Edit": _fmt_edit,
    "Write": _fmt_write,
    "Grep": _fmt_grep,
    "Glob": _fmt_glob,
    "Agent": _fmt_agent,
    "TaskCreate": _fmt_task,
    "TaskUpdate": _fmt_task,
    "TodoWrite": _fmt_todowrite,
}


def fmt_tool_input(name: str, inp: dict) -> str:
    return TOOL_FORMATTERS.get(name, _fmt_generic)(name, inp)


def fmt_tool_result(content) -> str:
    if isinstance(content, str):
        return truncate(content, MAX_TOOL_RESULT)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(block)[:500])
            else:
                parts.append(str(block))
        return truncate("\n".join(parts), MAX_TOOL_RESULT)
    return truncate(str(content), MAX_TOOL_RESULT)


def fmt_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Input JSONL session log")
    ap.add_argument("output", type=Path, help="Output Markdown path")
    ap.add_argument("--title", default=None, help="Override session title (defaults to aiTitle from JSONL)")
    args = ap.parse_args()

    events = []
    with args.input.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    out = []
    first_ts = next((e.get("timestamp") for e in events if e.get("timestamp")), "")
    last_ts = next((e.get("timestamp") for e in reversed(events) if e.get("timestamp")), "")
    cwd = next((e.get("cwd") for e in events if e.get("cwd")), "")
    branch = next((e.get("gitBranch") for e in events if e.get("gitBranch")), "")
    ai_title = next((e.get("aiTitle") for e in events if e.get("type") == "ai-title"), "")

    title = args.title or ai_title or "Claude Code Session"
    out.append(f"# {title}\n")
    out.append(f"- **Workspace:** `{cwd}`")
    out.append(f"- **Branch:** `{branch}`")
    out.append(f"- **Started:** {fmt_ts(first_ts)}")
    out.append(f"- **Ended:** {fmt_ts(last_ts)}")
    out.append(f"- **Total events:** {len(events)}")
    out.append("\n---\n")

    # `prev_was_assistant` suppresses the `## 🤖 Assistant` header when consecutive
    # assistant turns are split across events (e.g. text block + tool call in
    # separate events) — they should render as one section.
    prev_was_assistant = False

    for ev in events:
        t = ev.get("type")
        if t in ("system", "attachment", "queue-operation", "last-prompt", "ai-title"):
            continue
        if ev.get("isSidechain"):
            continue

        msg = ev.get("message", {}) or {}

        if t == "user":
            if ev.get("isMeta"):
                continue
            content = msg.get("content")

            if isinstance(content, list):
                tool_blocks = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]
                text_blocks = [c for c in content if isinstance(c, dict) and c.get("type") == "text"]
                if tool_blocks:
                    for tb in tool_blocks:
                        result_content = tb.get("content", "")
                        prefix = "**Tool result (error)**" if tb.get("is_error") else "**Tool result**"
                        out.append(
                            f"\n<details><summary>{prefix}</summary>\n\n```\n{fmt_tool_result(result_content)}\n```\n\n</details>\n"
                        )
                    continue
                if text_blocks:
                    text = clean_user_text("\n".join(b.get("text", "") for b in text_blocks))
                    if text:
                        out.append(f"\n## 👤 User\n\n{text}\n")
                        prev_was_assistant = False
                continue

            if isinstance(content, str):
                text = clean_user_text(content)
                if text:
                    out.append(f"\n## 👤 User\n\n{text}\n")
                    prev_was_assistant = False
            continue

        if t == "assistant":
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            chunks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        chunks.append(text)
                elif btype == "thinking":
                    thinking = block.get("thinking", "").strip()
                    if thinking:
                        chunks.append(
                            f"<details><summary>💭 Thinking</summary>\n\n{truncate(thinking, MAX_THINKING)}\n\n</details>"
                        )
                elif btype == "tool_use":
                    chunks.append(fmt_tool_input(block.get("name", ""), block.get("input", {}) or {}))

            if chunks:
                if not prev_was_assistant:
                    out.append("\n## 🤖 Assistant\n")
                out.append("\n" + "\n\n".join(chunks) + "\n")
                prev_was_assistant = True

    args.output.write_text("\n".join(out))
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
