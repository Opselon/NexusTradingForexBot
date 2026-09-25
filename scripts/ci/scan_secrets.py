import pathlib
import re
import sys

roots = [pathlib.Path("src"), pathlib.Path("configs"), pathlib.Path("docs")]
# Governance hardening (agents/git_governance.md §17): GitHub token prefixes
# are now matched in addition to api-key/bot-token/PEM shapes. The embedded
# credential risk in a git remote URL is covered by scripts/git/preflight.py
# (REMOTE checks, which strip any userinfo before reporting) — a scanner
# cannot safely read .git/config without risking secret exposure in CI logs.
pat = re.compile(
    r"(api[_-]key\s*[=:]\s*['\"][A-Za-z0-9_\-]{12,}"
    r"|bot[_-]token\s*[=:]\s*['\"]?\d{6,}:[A-Za-z0-9_\-]{25,}"
    r"|gho_[0-9A-Za-z]{36}"
    r"|ghp_[0-9A-Za-z]{36}"
    r"|github_pat_[0-9A-Za-z_]{59,}"
    r"|BEGIN\s+(RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY)"
)
ignored_subdirs = {"__pycache__"}
hits: list[str] = []
for root in roots:
    if not root.exists():
        continue
    for f in root.rglob("*"):
        if (
            not f.is_file()
            or any(part in ignored_subdirs for part in f.parts)
            or f.suffix == ".pyc"
        ):
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
