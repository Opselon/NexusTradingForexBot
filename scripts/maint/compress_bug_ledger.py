#!/usr/bin/env python3
"""Compress agents/bugs.md (the NSE forensic bug ledger) in place.

Reads agents/bugs.md, writes:
  - agents/bugs.md          compressed ledger (condensed entries, archive pointers)
  - agents/bugs_archive.md  verbatim copy of the pre-compression original
  - agents/bug_index.json   machine-readable index (id -> title/sev/status/files/tests/line)

Deterministic and idempotent: re-running on an already-compressed ledger is a no-op
(the guard comment near the top is detected and the script exits 0).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "agents" / "bugs.md"
ARCHIVE = ROOT / "agents" / "bugs_archive.md"
INDEX = ROOT / "agents" / "bug_index.json"

GUARD = "<!-- NSE-LEDGER-COMPRESSED"
MAX_TITLE = 160
MAX_FIELD = 420
MAX_BODY = 2600
MAX_BLOCKS = 7

H2 = re.compile(r"^## ")
BUG_H = re.compile(r"^## BUG-(\d+)")
AGENT_TOK = re.compile(
    r"(Hermes|NSE[-_]Swarm|Agent|TASK|role-?\s?\d|wave|lane|directive|E2E|CHG-\d+|2026)"
)

# label line forms: "- **Status**: X", "- STATUS: X", "**Status:** X", "### Status: X"
FIELD_PATTERNS = {
    "status": [
        r"^#{0,3}\s*-?\s*\*\*Status\*\*[:\s]*(?P<v>.+?)\s*$",
        r"^-\s*STATUS[:\s]*(?P<v>.+?)\s*$",
        r"^###?\s*Status[:\s]*(?P<v>.+?)\s*$",
        r"^\*\*Status[:\s]*\*\*(?P<v>.+?)\s*$",
    ],
    "severity": [
        r"^#{0,3}\s*-?\s*\*\*Severity\*\*[:\s]*(?P<v>.+?)\s*$",
        r"^-\s*SEVERITY[:\s]*(?P<v>.+?)\s*$",
        r"^\*\*Severity[:\s]*\*\*(?P<v>.+?)\s*$",
    ],
    "confidence": [r"^#{0,3}\s*-?\s*\*\*Confidence\*\*[:\s]*(?P<v>.+?)\s*$"],
    "discovered": [
        r"^#{0,3}\s*-?\s*\*\*Discovered\*\*[:\s]*(?P<v>.+?)\s*$",
        r"^-\s*Found by[:\s]*(?P<v>.+?)\s*$",
    ],
    "fixed": [r"^#{0,3}\s*-?\s*\*\*Fixed\*\*[:\s]*(?P<v>.+?)\s*$"],
    "verified": [r"^#{0,3}\s*-?\s*\*\*Verified\*\*[:\s]*(?P<v>.+?)\s*$"],
}

# block labels we keep as condensed prose (lowercased, matched as line prefixes)
BLOCK_LABELS = [
    "surface", "surfaces", "symptom", "symptoms", "root cause", "root causes",
    "problem", "impact", "evidence", "fix", "resolution", "verification",
    "runtime verification", "regression", "regression tests", "regression test",
    "regression guards", "lesson", "lessons", "found", "found by", "mechanism",
    "failure scenario", "execution path", "category", "reproduction",
    "trigger", "residual", "invariants", "tests", "note", "operator note",
]
# labels already surfaced in the structured metadata line — not repeated as prose
META_LABELS = {"status", "severity", "confidence", "discovered", "fixed", "verified"}


def parse(text: str):
    """Split the ledger into ordered sections (bug entries + other H2 blocks)."""
    lines = [l.rstrip("\r") for l in text.split("\n")]
    h2 = [(i, l.strip()) for i, l in enumerate(lines) if H2.match(l)]
    if not h2:
        return [], lines
    counts: dict[str, int] = {}
    sections = []
    for i, header in h2:
        m = BUG_H.match(header)
        if m:
            base = "BUG-" + m.group(1)
            counts[base] = counts.get(base, 0) + 1
            cid = base if counts[base] == 1 else f"{base}.{counts[base]}"
            sections.append({"line0": i, "header": header, "cid": cid, "kind": "bug"})
        else:
            sections.append({"line0": i, "header": header, "cid": None, "kind": "other"})
    for k, s in enumerate(sections):
        s["end"] = sections[k + 1]["line0"] if k + 1 < len(sections) else len(lines)
        s["lines"] = lines[s["line0"] : s["end"]]
    return sections, lines


def short_title(header: str) -> str:
    """Drop the trailing provenance parenthetical, then cap at MAX_TITLE chars."""
    t = header[3:].strip() if header.startswith("## ") else header.strip()
    m = re.search(r"\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*$", t)
    if m and AGENT_TOK.search(m.group(1)):
        t = t[: m.start()].rstrip().rstrip(" -—")
    t = t.strip()
    if len(t) <= MAX_TITLE:
        return t
    cut = t[:MAX_TITLE]
    sp = cut.rfind(" ")
    if sp > 80:
        cut = cut[:sp]
    return cut.rstrip(" ,;:—-") + " …"


def clip(text: str, limit: int = MAX_FIELD) -> str:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:—-") + " …"


def field(body: str, name: str):
    for pat in FIELD_PATTERNS[name]:
        m = re.search(pat, body, re.M)
        if m:
            return m.group("v").strip()
    return None


BLOCK_PREFIX = re.compile(
    r"^(?:-\s*(?:\*\*)?|\*\*|#{2,3}\s*)(?P<label>[A-Za-z][A-Za-z0-9 /&()+-]{2,40})"
    r"(?:\*\*)?\s*[:\-]?\s*(?P<rest>.*)$"
)
STOPWORDS = {"a", "an", "the", "this", "that", "it"}


def blocks(entry_lines):
    """Yield (label, text) blocks: labelled lines start a block that swallows
    continuation lines (indented or non-label lines) until the next label."""
    label = None
    buf = []
    out = []
    for l in entry_lines:
        s = l.strip()
        if not s:
            continue
        if s.startswith("```"):
            continue
        m = BLOCK_PREFIX.match(s)
        if m and not s.startswith("#!"):
            low = m.group("label").strip().lower().rstrip(" :-").strip()
            words = low.split()
            if 3 <= len(low) and words[0] not in STOPWORDS and ":" in s:
                if label:
                    out.append((label, " ".join(buf)))
                label = low
                buf = [m.group("rest")] if m.group("rest").strip() else []
                continue
        if label is not None:
            buf.append(s)
    if label:
        out.append((label, " ".join(buf)))
    return out


def condense(entry: dict) -> str:
    """Render one bug entry as a condensed markdown block."""
    body_lines = entry["lines"][1:]  # skip the H2 itself
    body = "\n".join(body_lines)
    out = [f"## {short_title(entry['header'])}", ""]

    meta = []
    for key in ("severity", "status", "confidence", "discovered", "fixed", "verified"):
        v = field(body, key)
        if v:
            meta.append(f"**{key.capitalize()}**: {clip(v)}")
    if meta:
        out.append("- " + " | ".join(meta))

    bl = blocks(body_lines)
    # prefer canonical names in a stable order, then any other labelled block
    order = [
        "surface", "surfaces", "symptom", "symptoms", "problem", "impact",
        "root cause", "root causes", "found", "mechanism", "evidence",
        "failure scenario", "execution path", "fix", "resolution",
        "verification", "runtime verification", "regression", "regression tests",
        "regression test", "regression guards", "residual", "note",
        "operator note", "invariants", "tests", "category", "trigger",
        "reproduction", "lesson", "lessons",
    ]
    seen = set()
    chosen = []
    for want in order:
        for label, txt in bl:
            if label == want and label not in seen:
                seen.add(label)
                chosen.append((label, txt))
    for label, txt in bl:
        if label not in seen:
            seen.add(label)
            chosen.append((label, txt))
    chosen = [c for c in chosen if c[0] not in META_LABELS]

    total = 0
    for label, txt in chosen[:MAX_BLOCKS]:
        flat = re.sub(r"\s*\n\s*", " ", txt)
        flat = re.sub(r"```[a-z]*|```", "`", flat)
        flat = re.sub(r"\s+", " ", flat).strip()
        if len(flat) < 15:
            continue
        cap = max(120, MAX_BODY - total)
        piece = clip(flat, cap)
        if len(piece) < 25:
            continue
        # a parenthesised qualifier left open by label-chopping must not leak
        if label.count("(") > label.count(")"):
            label = label.rsplit("(", 1)[0].strip()
        label = clip(label, 60)
        if piece.count("**") % 2:
            piece = piece.replace("**", "")
        # a qualifier that the label left behind must not start the sentence
        piece = re.sub(r"^[\s,;:]+", "", piece).strip()
        if not piece:
            continue
        out.append(f"- **{label.title()}**: {piece}")
        total += len(piece)
        if total >= MAX_BODY:
            break

    # entries with no labelled block at all still keep their substance
    if not any(l.startswith("- **") and not l.startswith("- **Orig") for l in out):
        flat = re.sub(r"\s*\n\s*", " ", " ".join(body_lines))
        flat = re.sub(r"```[a-z]*|```", "`", flat)
        flat = re.sub(r"\s+", " ", flat).strip()
        if flat.count("**") % 2:
            flat = flat.replace("**", "")
        out.insert(1, "- **Detail**: " + clip(flat, MAX_FIELD * 3))

    tests = find_tests(body)
    if tests:
        out.append("- **Tests**: " + clip("; ".join(tests), 400))
    files = find_files(body)
    if files:
        out.append("- **Files**: " + clip("; ".join(files), 400))
    inv = find_invariants(body)
    if inv:
        out.append("- **Invariants**: " + ", ".join(inv))
    commits = find_commits(body)
    if commits:
        out.append("- **Commits**: " + clip(", ".join(commits), 200))
    out.append(f"- **Orig**: agents/bugs_archive.md:{entry['line0'] + 1}")
    out.append("")
    return "\n".join(out)


def find_tests(body: str):
    hits = set(re.findall(r"tests/[A-Za-z0-9_./]+\.py", body))
    hits |= set(re.findall(r"\b(test_[A-Za-z0-9_]+)\b", body))
    return sorted(hits)


def find_files(body: str):
    hits = set(re.findall(r"\b(?:src/)?(nexus_scalp/[A-Za-z0-9_./]+\.py)", body))
    hits |= set(re.findall(r"\b(src/[A-Za-z0-9_./]+\.py)\b", body))
    return sorted(hits)


def find_commits(body: str):
    return sorted(set(re.findall(r"\b([0-9a-f]{7,8})\b", body)))


def find_invariants(body: str):
    return sorted(set(re.findall(r"\b(INV-\d{3})\b", body)))


def main():
    if not LEDGER.exists():
        print(f"missing {LEDGER}", file=sys.stderr)
        return 2
    text = LEDGER.read_text(encoding="utf-8")
    if GUARD in text[:4000]:
        print("ledger already compressed; nothing to do")
        return 0

    sections, lines = parse(text)
    if not sections:
        print("no H2 sections found; refusing to compress", file=sys.stderr)
        return 2

    bugs = [s for s in sections if s["kind"] == "bug"]
    others = [s for s in sections if s["kind"] != "bug"]
    print(f"sections: {len(bugs)} bug entries, {len(others)} other blocks")

    # preserve the verbatim original before overwriting the ledger
    ARCHIVE.write_text(text, encoding="utf-8")
    print(f"wrote {ARCHIVE}")

    head = []
    for l in lines:
        if l.startswith("## Forensic Bug Ledger"):
            break
        head.append(l)
    if not any(h.startswith("## ") for h in head):
        head = lines[:34]

    body_parts = []
    index = []
    for s in sections:
        if s["kind"] == "bug":
            body_parts.append(condense(s))
            b = "\n".join(s["lines"])
            index.append(
                {
                    "id": s["cid"],
                    "title": short_title(s["header"]),
                    "line": s["line0"] + 1,
                    "severity": field(b, "severity"),
                    "status": field(b, "status"),
                    "confidence": field(b, "confidence"),
                    "discovered": field(b, "discovered"),
                    "fixed": field(b, "fixed"),
                    "verified": field(b, "verified"),
                    "files": find_files(b),
                    "tests": find_tests(b),
                    "commits": find_commits(b),
                    "invariants": find_invariants(b),
                }
            )
        else:
            body_parts.append("\n".join(s["lines"]).strip() + "\n")

    guard = (
        f"{GUARD} v1 — generated by scripts/maint/compress_bug_ledger.py. "
        "The verbatim pre-compression ledger lives in agents/bugs_archive.md; "
        "every entry below links to its archive line via its `Orig:` pointer. "
        "Add NEW entries at the end in the same one-entry-per-bug form. -->"
    )
    out = (
        "\n".join(head).rstrip()
        + "\n\n"
        + guard
        + "\n\n## Forensic Bug Ledger (condensed)\n\n"
        + "\n".join(body_parts).rstrip()
        + "\n"
    )
    LEDGER.write_text(out, encoding="utf-8")
    print(f"wrote {LEDGER}: {len(out):,} bytes ({len(out.splitlines())} lines)")

    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {INDEX}: {len(index)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
