"""Merge wave-2 lane verdicts (new schema with `category`/`tier_correct`)
and wave-1 verdicts into scratch/recon/verdicts_merged.json, keyed by file.
Newer schema wins on conflict.

One-time wave-2 salvage tool: it reads the Claude Code hook-persisted tool
results for THIS session (path via NSE_HOOK_RESULTS_DIR; the hard-coded
default is the session that produced the verdicts — other machines/agents
regenerate verdicts via the forensic lanes instead, so this file is archival
tooling, not a CI surface).
"""

import html
import json
import os
import re
from pathlib import Path

BS = chr(92)
BASE = Path(
    os.environ.get(
        "NSE_HOOK_RESULTS_DIR",
        "C:/Users/Capsizer/.claude/projects/"
        "C--Users-Capsizer/aa24b1c4-c859-4990-8d94-985fbd61e9f3/tool-results",
    )
)
OUT = Path("scratch/recon/verdicts_merged.json")
VALID = {'"': '"', BS: BS, "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def find_prompt(txt: str):
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
    s = txt[start:i]
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == BS and i + 1 < n:
            nx = s[i + 1]
            if nx == "u" and i + 6 <= n:
                try:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nx in VALID:
                out.append(VALID[nx])
                i += 2
                continue
            out.append(nx)
            i += 2
            continue
        out.append(c)
        i += 1
    return html.unescape("".join(out))


def scan_top_level_objects(arr: str):
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


def fix_bullets(s: str) -> str:
    return re.sub(r"\\(?![" + re.escape('"' + BS + "/bfnrtu") + r"])", BS + BS, s)


def try_parse(o: str):
    for cand in (o, fix_bullets(o)):
        for attempt in (cand, cand.replace("\n", " ")):
            try:
                return json.loads(attempt, strict=False)
            except Exception:
                continue
    return None


def harvest(prompt: str):
    got = []
    for rm in re.finditer(r"<result>\n?(.*?)(?:</result>|<usage>|$)", prompt, re.S):
        body = rm.group(1).strip()
        body = re.sub(r"^```json\s*", "", body)
        body = re.sub(r"```\s*$", "", body.rstrip("`").strip())
        idxs = [p for p in (body.find("["),) if p >= 0]
        if not idxs:
            continue
        for o in scan_top_level_objects(body[idxs[0] :]):
            d = try_parse(o)
            if isinstance(d, dict) and "file" in d and d["file"].startswith("tests/"):
                got.append(d)
    return got


def main():
    verdicts = {}
    # wave-1 base (old schema)
    w1 = Path("scratch/recon/classifications.json")
    if w1.exists():
        for f, d in json.loads(w1.read_text(encoding="utf-8")).items():
            verdicts[f] = d
    # all hook files: newer-schema entries (with 'category') override
    for hf in sorted(BASE.glob("hook-*.txt")):
        txt = hf.read_text(encoding="utf-8", errors="replace")
        prompt = find_prompt(txt)
        if not prompt or "<result>" not in prompt:
            continue
        for d in harvest(prompt):
            key = d["file"]
            prev = verdicts.get(key)
            new_schema = "category" in d
            if prev is None or (new_schema and "category" not in prev):
                verdicts[key] = d
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(verdicts, indent=0), encoding="utf-8")
    from collections import Counter

    tally = Counter(v.get("classification", "?") for v in verdicts.values())
    cats = Counter(v.get("category", "-") for v in verdicts.values())
    print("merged verdicts:", len(verdicts))
    print("classifications:", dict(tally.most_common()))
    print("categories:", dict(cats.most_common()))


if __name__ == "__main__":
    main()
