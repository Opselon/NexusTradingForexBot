#!/usr/bin/env python3
"""
div_balance_check.py — strict div-balance stack parser for Nexus HTML files.

Contract (per repo convention):
  * index.html is pure LF
  * After ANY HTML edit we must guarantee there are NO mismatched/extra closing
    divs and ZERO unclosed divs at EOF.

This script runs a real tag-stack parse over every <div> / </div> in the file
(ignoring content inside <script>/<style>/<template> raw text and inside HTML
comments) and reports:
  - any MISMATCHED close (closing tag that does not match the open on top of the
    stack, e.g. closing </div> when the stack top is something else is still a
    div-close but if a closer appears with an empty stack that is an EXTRA close),
  - any EXTRA close (</div> with nothing open),
  - any UNCLOSED div at EOF (non-empty stack).

Exit code 0 = balanced; 1 = imbalance found.

Usage:
    python scripts/div_balance_check.py Web/index.html [more files...]
    python scripts/div_balance_check.py   # checks Web/index.html by default
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tags we track as a nesting stack (open/close pairs). The contract mandates div
# balance, but we also validate that other common block elements are not left
# unbalanced in a way that would indicate a structural edit error.
PAIRED_TAGS = {
    "div",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "aside",
    "nav",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "form",
    "button",
    "select",
    "script",
    "style",
    "template",
}

TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*?)(/?)>", re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_ignored(text: str) -> str:
    """Remove comments, script/style/template raw text so we only balance markup."""
    # Remove HTML comments.
    text = COMMENT_RE.sub(" ", text)
    # Remove raw-text containers that legitimately contain '<' that is not markup.
    text = re.sub(
        r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", " ", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<\s*style\b[^>]*>.*?<\s*/\s*style\s*>", " ", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<\s*template\b[^>]*>.*?<\s*/\s*template\s*>", " ", text, flags=re.DOTALL | re.IGNORECASE
    )
    return text


def check_file(path: Path) -> tuple[bool, list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # LF convention check for index.html (repo rule).
    issues: list[str] = []
    if path.name == "index.html" and "\r\n" in raw:
        issues.append("index.html is NOT pure LF (contains CRLF)")

    body = _strip_ignored(raw)

    stack: list[tuple[str, int]] = []  # (tag, line_number)
    line = 1
    for m in TAG_RE.finditer(body):
        slash, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        line = raw.count("\n", 0, m.start()) + 1
        if tag not in PAIRED_TAGS:
            continue
        if slash == "/":
            if not stack:
                issues.append(f"line {line}: EXTRA closing </{tag}> (nothing open)")
                continue
            # The contract focuses on div, but report mismatched close for divs.
            top_tag, top_line = stack[-1]
            if top_tag != tag:
                issues.append(
                    f"line {line}: MISMATCHED close </{tag}> (top of stack is <{top_tag}> opened at line {top_line})"
                )
                # Do not pop a mismatched frame; we still try to recover by removing
                # the matching open if it exists deeper in the stack.
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i]
                        break
            else:
                stack.pop()
        else:
            if selfclose == "/":
                continue  # self-closing, no push
            stack.append((tag, line))

    if stack:
        for tag, ln in stack:
            issues.append(f"line {ln}: UNCLOSED <{tag}> at EOF")
        # Focus the contract: div imbalance is the hard failure.
    return (len(issues) == 0), issues


def main(argv: list[str]) -> int:
    if argv:
        files = [Path(a) for a in argv]
    else:
        files = [REPO_ROOT / "Web" / "index.html"]
    ok = True
    for f in files:
        if not f.exists():
            print(f"[SKIP] {f} (not found)")
            continue
        balanced, issues = check_file(f)
        if balanced:
            print(f"[OK]   {f} — div/block balance clean")
        else:
            ok = False
            print(f"[FAIL] {f}")
            for i in issues:
                print(f"        - {i}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
