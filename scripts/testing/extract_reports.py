"""Salvage subagent forensic report arrays from hook-persisted files.

The persisted copies suffer chunk-boundary corruption (shell echo spliced into
JSON strings). Full-document parsing fails; object-level parsing mostly
survives. Strategy: locate the array text, split on `},{` object boundaries,
repair each object independently (splice removal + escape normalization),
json-parse; write survivors + a salvage manifest.
"""

import html
import json
import re
import sys
from pathlib import Path

BS = chr(92)
BASE = Path(
    "C:/Users/Capsizer/.claude/projects/"
    "C--Users-Capsizer/aa24b1c4-c859-4990-8d94-985fbd61e9f3/tool-results"
)
OUTDIR = Path("scratch/recon/reports")

ECHO = re.compile(r"\n*C:.{0,90}?test_recon>\n*", re.S)


def unescape_once(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    valid = {'"': '"', BS: BS, "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while i < n:
        c = s[i]
        if c == BS and i + 1 < n:
            nx = s[i + 1]
            if nx == "u":
                try:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nx in valid:
                out.append(valid[nx])
                i += 2
                continue
            # invalid escape: keep both chars literally
            out.append(BS)
            out.append(nx)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def prompt_span(txt: str):
    m = re.search(r'"prompt":\s*"', txt)
    if not m:
        return None
    i = m.end()
    start = i
    while i < len(txt):
        if txt[i] == BS:
            i += 2
            continue
        if txt[i] == '"':
            break
        i += 1
    return txt[start:i]


def fix_string_backslashes(s: str) -> str:
    """Escape backslashes that are not valid JSON escapes (Windows paths)."""
    return re.sub(r'\\(?!["\\/bfnrtu])', BS + BS, s)


def try_obj(o: str):
    for cand in (o, fix_string_backslashes(o)):
        for attempt in (cand, cand.replace("\n", " ")):
            try:
                return json.loads(attempt, strict=False)
            except Exception:
                continue
    return None


def scan_top_level_objects(arr: str):
    """Yield each top-level {...} object in a JSON array text, tracking string
    state so nested objects survive (regex splitting would break them)."""
    objs = []
    depth = 0
    in_str = False
    start = -1
    i = 0
    n = len(arr)
    while i < n:
        c = arr[i]
        if in_str:
            if c == BS:
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                objs.append(arr[start : i + 1])
                start = -1
        i += 1
    return objs


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    totals = {}
    for f in sorted(BASE.glob("hook-*.txt")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        span = prompt_span(txt)
        if not span:
            continue
        prompt = html.unescape(unescape_once(span))
        if "<result>" not in prompt:
            continue
        rm = re.search(r"<result>(.*?)(?:</result>|<usage>|$)", prompt, re.S)
        body = rm.group(1).strip()
        body = re.sub(r"^```json\s*", "", body)
        body = body.rstrip("`").strip()
        body = re.sub(r"```\s*$", "", body)
        start = body.find("[")
        if start < 0:
            continue
        arr = ECHO.sub("", body[start:])
        chunks = scan_top_level_objects(arr)
        good = []
        bad = 0
        for o in chunks:
            data = try_obj(o)
            if data is None:
                bad += 1
                continue
            good.append(data)
        if not good:
            print("nothing salvaged", f.name[:26])
            continue
        blob = json.dumps(good)
        if "critical_gate_candidates" in blob or '"runtime_guess"' in blob:
            tag = "nonunit"
        elif '"production_surface"' in blob:
            tag = "risk"
        elif '"state_machine"' in blob:
            tag = "execution"
        elif '"model_market"' in blob:
            tag = "dbmodel"
        elif "workflows" in (good[0] if isinstance(good[0], dict) else {}):
            tag = "ci"
        else:
            first = good[0].get("file", "") if isinstance(good[0], dict) else ""
            letter = Path(first).name.replace("test_", "")[:1].lower() if first else "z"
            tag = "unitA" if letter < "m" else "unitB"
        prev = totals.get(tag, (0, 0))
        if len(good) >= prev[0]:
            totals[tag] = (len(good), bad)
        out = OUTDIR / f"inv_{tag}.json"
        out.write_text(json.dumps(good), encoding="utf-8")
        print("OK", f.name[:26], tag, f"good={len(good)} bad={bad}")
    print("BEST PER TAG:", json.dumps(totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
