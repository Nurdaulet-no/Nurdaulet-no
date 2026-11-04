#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

README = "README.md"
API_URL = os.environ.get("API_URL", "https://api.groq.com/openai/v1/chat/completions")
MODEL = os.environ.get("MODEL", "openai/gpt-oss-120b")
TOKEN = os.environ["GROQ_API_KEY"]

MIN_BYTES = 200
MAX_BYTES = 20_000

ALLOWED_DOMAINS = {
    "github.com",
    "githubusercontent.com",
    "shields.io",
    "img.shields.io",
    "linkedin.com",
    "t.me",
    "twitter.com",
    "x.com",
}
ALLOWED_DOMAINS |= {d.strip() for d in os.environ.get("EXTRA_DOMAINS", "").split(",") if d.strip()}

REQUIRED = [s.strip() for s in os.environ.get("REQUIRED_STRINGS", "").split(",") if s.strip()]

SYSTEM_PROMPT = """You are the author and sole editor of a developer's GitHub profile README.md.
You have complete creative freedom over how the profile looks, sounds and feels.
Every day, rewrite it with a fresh visual identity and voice. Watching the
profile evolve over time is the whole point of this project, so be bold. Big
changes are encouraged: a new theme, a new layout, a new personality each day.

You are free to:
- Redesign the whole visual style: layout, section order, headings, emoji,
  dividers, tables, badges, centered HTML blocks, collapsible <details>,
  ASCII/Unicode art, box drawings, terminal or retro or minimalist or
  dramatic aesthetics, anything that renders in GitHub Markdown.
- Rewrite all prose in any tone you like: playful, poetic, deadpan, cinematic,
  nerdy, mysterious, a fake terminal session, a changelog, a newspaper front
  page, a game menu, etc.
- Rephrase, reorder, split, merge, rename or drop sections.
- Invent small recurring or one-off features: thought of the day, mini
  changelog, fake system logs, fortune cookies, ASCII mascots, jokes, quizzes.
  Purely decorative or fictional-fun content is fine as long as it makes no
  claims about the owner.
- Ignore yesterday's style entirely. You do not need to preserve it.

The only things you must NOT touch (the core info):
- Facts about the owner: employers, dates, job titles, education, languages,
  metrics (e.g. benchmark numbers) and project descriptions must stay exactly
  true to the current README. You may reword the wording and style, but the
  meaning and numbers must not change. Do not add new jobs, skills,
  achievements or projects that are not already there.
- Links and URLs: never invent URLs. Reuse existing links and image URLs
  exactly, character for character. Do not create new ones.
- Contact links and the email address must stay.

Technical rules:
- Reply with ONLY the complete new content of README.md. No explanations
  before or after, and do not wrap the answer in a ``` code block.
- Write in English.
- Keep all HTML tags properly opened and closed.
- Keep a short final section "Last edit" with one line saying what you changed
  and why. Replace it every day.
- Keep the file under 15,000 characters.
"""


def call_model(current: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "model": MODEL,
        "temperature": 1.0,
        "max_tokens": 6000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Сегодня {today}. Вот текущий README.md:\n\n{current}",
            },
        ],
    }
    cmd = [
        "curl", "-sS", "-L", "--max-time", "120",
        "-X", "POST", API_URL,
        "-H", "Accept: application/json",
        "-H", f"Authorization: Bearer {TOKEN}",
        "-H", "Content-Type: application/json",
        "--data-binary", "@-",
        "-w", "\n%{http_code}",
    ]
    try:
        proc = subprocess.run(
            cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=150
        )
    except Exception as e:
        fail(f"Could not run curl: {e}")
    if proc.returncode != 0:
        fail(f"curl failed ({proc.returncode}): {proc.stderr.strip()[:500]}")

    body, _, status = proc.stdout.rpartition("\n")
    print(f"HTTP status: {status}")
    if not status.startswith("2"):
        fail(f"API error {status}: {body[:500]}")
    try:
        data = json.loads(body)
    except ValueError:
        fail(f"Response is not JSON (HTTP {status}). First 500 chars: {body[:500]!r}")
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            fail("Model output was truncated (token limit). Nothing written.")
        return choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        fail(f"Unexpected API response: {str(data)[:500]}")


def clean(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    return text + "\n"


def urls_in(text: str) -> set:
    return set(re.findall(r"https?://[^\s)\]>\"'<]+", text))


def host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def validate(old: str, new: str) -> None:
    size = len(new.encode("utf-8"))
    if size < MIN_BYTES:
        fail(f"New README too small ({size} bytes)")
    if size > MAX_BYTES:
        fail(f"New README too large ({size} bytes)")
    if new.strip() == old.strip():
        print("No changes from the model, nothing to do.")
        sys.exit(0)

    known = urls_in(old)
    for url in urls_in(new) - known:
        if not host_allowed(url):
            fail(f"Rejected: new link to a non-allowed domain: {url}")

    for needle in REQUIRED:
        if needle not in new:
            fail(f"Rejected: required string missing: {needle}")

    for tag in ("table", "tr", "td", "div", "p", "ul"):
        opened = len(re.findall(rf"<{tag}[\s>]", new, re.IGNORECASE))
        closed = len(re.findall(rf"</{tag}>", new, re.IGNORECASE))
        if opened != closed:
            fail(f"Rejected: unbalanced <{tag}> tags ({opened} open, {closed} closed)")

    if re.search(r"<\s*(script|iframe|object|embed)\b", new, re.IGNORECASE):
        fail("Rejected: forbidden HTML tag in output")


def fail(msg: str):
    print(f"::error::{msg}")
    sys.exit(1)


def main() -> None:
    with open(README, "r", encoding="utf-8") as f:
        old = f.read()

    new = clean(call_model(old))
    validate(old, new)

    with open(README, "w", encoding="utf-8") as f:
        f.write(new)
    print("README.md updated.")


if __name__ == "__main__":
    main()
