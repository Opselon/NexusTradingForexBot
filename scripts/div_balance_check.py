#!/usr/bin/env python3
"""
div_balance_check.py — strict div-balance stack parser for Nexus HTML files using HTMLParser.

Contract (per repo convention):
  * index.html is pure LF
  * After ANY HTML edit we must guarantee there are NO mismatched/extra closing
    divs and ZERO unclosed divs at EOF.

This script uses html.parser to strip script/style/template raw text and comments
robustly before running a real tag-stack parse.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PAIRED_TAGS = {
    "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th", "form",
    "button", "select", "script", "style", "template",
}
TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*?)(/?)>", re.DOTALL)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fed: list[str] = []
        self.ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "template"):
            self.ignore_depth += 1
        if self.ignore_depth == 0:
            attr_str = "".join(f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs)
            self.fed.append(f"<{t}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "template"):
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if self.ignore_depth == 0:
            self.fed.append(f"</{t}>")

    def handle_comment(self, data: str) -> None:
        pass


def _strip_ignored(text: str) -> str:
    parser = _HTMLStripper()
    parser.feed(text)
    parser.close()
    return "".join(parser.fed)


def check_file(path: Path) -> tuple[bool, list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    issues: list[str] = []
    if path.name == "index.html" and "\r\n" in raw:
        issues.append("index.html is NOT pure LF (contains CRLF)")
    stack: list[tuple[str, int]] = []
    for m in TAG_RE.finditer(_strip_ignored(raw)):
        slash, tag, _attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        line = raw.count("\n", 0, m.start()) + 1
        if tag not in PAIRED_TAGS:
            continue
        if slash == "/":
            if not stack:
                issues.append(f"line {line}: EXTRA closing </{tag}> (nothing open)")
                continue
            top_tag, top_line = stack[-1]
            if top_tag != tag:
                issues.append(f"line {line}: MISMATCHED close </{tag}> (top of stack is <{top_tag}> opened at line {top_line})")
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i]
                        break
            else:
                stack.pop()
        elif selfclose != "/":
            stack.append((tag, line))
    if stack:
        issues.extend(f"line {ln}: UNCLOSED <{tag}> at EOF" for tag, ln in stack)
    return (not issues), issues


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] if argv else [REPO_ROOT / "Web" / "index.html"]
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
            for issue in issues:
                print(f"        - {issue}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

