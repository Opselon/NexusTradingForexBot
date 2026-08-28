import pathlib
import re
import sys

roots = [pathlib.Path("src"), pathlib.Path("configs"), pathlib.Path("docs")]
pat = re.compile(
    r"(api[_-]key\s*[=:]\s*['\"][A-Za-z0-9_\-]{12,}|bot[_-]token\s*[=:]\s*['\"]?\d{6,}:[A-Za-z0-9_\-]{25,}|BEGIN\s+(RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY)"
)
hits: list[str] = []
for root in roots:
    if not root.exists():
        continue
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            txt = f.read_text(errors="ignore")
        except Exception:
            continue
        if pat.search(txt):
            hits.append(str(f).replace(chr(92), "/"))
if hits:
    print("::error::Secret-shaped strings found in source/config: " + ", ".join(hits[:10]))
    sys.exit(1)
print("Source scan clean.")
