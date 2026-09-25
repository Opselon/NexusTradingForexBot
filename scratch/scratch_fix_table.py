"""Fix the generated route table so Go's ServeMux can register every pattern.

Go 1.22 ServeMux patterns are ambiguous when two templates for the same
method differ at exactly one segment where one is a literal and the other is
a wildcard (e.g. GET /api/news/health vs GET /api/news/{article_id}): both
match /api/news/health-like paths and neither is more specific.

Go resolves this by REGISTRATION ORDER: the pattern registered first wins on
the overlapping path. FastAPI resolves it by static-route priority. Sorting
the table so literal segments precede wildcard segments reproduces the
priority Python applies, and also fixes the sibling-branch ambiguity
(/api/news/{article_id}/restore vs /api/news/analyze/{article_id}).
"""
from __future__ import annotations

import re
from pathlib import Path

TABLE = Path(__file__).resolve().parent / "go-api" / "internal" / "api" / "routes" / "table_gen.go"


def rank(path: str) -> tuple[bool, ...]:
    """One bool per segment: True where the segment is a LITERAL."""
    return tuple(not s.startswith("{") for s in path.split("/"))


def main() -> None:
    src = TABLE.read_text(encoding="utf-8")
    crlf = "\r\n" in src
    rows = []
    for line in src.splitlines():
        m = re.match(r"(\s*)\{Method: \"([A-Z]+)\", Path: \"([^\"]+)\", IsV1: (true|false)\},?\s*$",
                     line)
        if not m:
            continue
        rows.append((m.group(2), m.group(3), m.group(4)))

    # Group by method, then sort each group so literals precede wildcards at
    # the FIRST differing segment (dominant-segment priority, like FastAPI).
    by_method: dict[str, list] = {}
    for m, p, v in rows:
        by_method.setdefault(m, []).append((p, v))

    ordered = []
    for m in sorted(by_method):
        group = sorted(by_method[m],
                       key=lambda pv: tuple(
                           (1 if seg.startswith("{") else 0, seg)
                           for seg in pv[0].split("/")))
        for p, v in group:
            ordered.append((m, p, v))

    head_end = src.index("var generatedRoutes = []routeEntry{")
    head = src[:head_end + len("var generatedRoutes = []routeEntry{")]
    lines = [head]
    for m, p, v in ordered:
        lines.append(f'\t{{Method: "{m}", Path: "{p}", IsV1: {v}}},')
    lines.append("}")
    out = ("\r\n" if crlf else "\n").join(lines) + ("\r\n" if crlf else "\n")
    TABLE.write_text(out, encoding="utf-8", newline="")
    print(f"rewrote {len(ordered)} rows, literals-first per method")


if __name__ == "__main__":
    main()
