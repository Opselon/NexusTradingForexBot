#!/usr/bin/env python3
"""validate_frontend_dist.py — frontend/dist asset-contract validator.

CONTRACT frozen decision #11 (NSE END-USER-RUNTIME-UI-INTEGRATION wave):
packaging/release lane. Stdlib only (no third-party imports), import-clean
and side-effect free: importing this module runs no checks and writes
nothing.

Gate usage (called fail-loud by .github/workflows/js-tests.yml and
release.yml, and by scripts/build/build_release.ps1):

    python scripts/build/validate_frontend_dist.py frontend/dist

Exit 0 + exactly one OK line when every assertion holds. Exit 1 with one
explicit `MISSING: ...` or `BROKEN: ...` line per violation.

Assertions:
  (a) index.html exists, non-empty, and references at least one hashed
      assets/*.js AND one hashed assets/*.css (present on disk, referenced).
  (b) every relative / root-absolute asset src|href (and og/twitter image
      content) in index.html resolves to a real file INSIDE frontend/dist —
      no external CDN for boot, no `..`/backslash escapes, no missing files.
      Root-absolute URLs are mapped through the Vite `base` first (dist root
      is served both at `/` and at the configured base), then tried directly.
  (c) at least one favicon/apple-touch-icon link resolves to an existing
      file; a manifest.webmanifest or manifest.json is present (linked from
      index.html or in the dist root) and every manifest icon exists.
  (d) no external http(s):// URL anywhere in index.html (offline local-first,
      wave section 40) and no protocol-relative / exotic-scheme references.
  (e) every file under frontend/dist/assets that index.html references is
      non-empty.
  (f) every local url()/@import target named by a bundled stylesheet exists
      inside dist — a CSS that points at a dropped /assets/* is a broken
      /assets ref just like an index.html one (contract wording).
  (g) exit 0 + one OK line on success; exit 1 + explicit MISSING/BROKEN
      lines on failure.

Reference resolution must not depend on WHERE the dist directory sits: the
dual-serve contract (decision #2) mounts the same bundle at "/" and at
"/alt", so a "/alt/..." URL is accepted whether or not a sibling
vite.config.ts declares that base. Files still have to exist inside dist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Vite content hashes: -<8+ chars of [A-Za-z0-9_-]> right before the ext.
# (Lane B emits hashes like `index-6Ej7IQ23.js`, `Bd-cP8d_.js`.)
HASHED_ASSET_RE = re.compile(r"-[A-Za-z0-9_-]{8,}\.(?:js|css)$", re.IGNORECASE)
EXTERNAL_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)
VITE_BASE_RE = re.compile(
    r"""(?:^|\n)\s*(?:export\s+default\s+)?defineConfig\(\{[\s\S]{0,4096}?base\s*:\s*["']([^"']+)["']"""
)
SKIP_SCHEMES = ("data:", "mailto:", "javascript:", "about:", "blob:", "tel:")
ICON_RELS = {"icon", "shortcut", "apple-touch-icon", "apple-touch-icon-precomposed"}
IMAGE_META_PROPS = {"og:image", "og:image:url", "og:image:secure_url", "twitter:image"}
MANIFEST_FILENAMES = ("manifest.webmanifest", "manifest.json")


class _IndexCollector(HTMLParser):
    """Collects src/href references (and image meta content) from index.html."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []  # (kind, url); kind: asset|icon|manifest|meta
        self.declared_manifest: str | None = None
        self.declared_icons: list[str] = []
        self.script_srcs: list[str] = []
        self.stylesheet_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        rel_tokens = set((a.get("rel") or "").lower().split())
        if tag == "script" and a.get("src", "").strip():
            self.script_srcs.append(a["src"].strip())
        if tag == "link" and "stylesheet" in rel_tokens and a.get("href", "").strip():
            self.stylesheet_hrefs.append(a["href"].strip())
        for name in ("src", "href"):
            url = a.get(name, "").strip()
            if not url:
                continue
            if tag == "link" and "manifest" in rel_tokens:
                kind = "manifest"
                self.declared_manifest = url
            elif tag == "link" and rel_tokens & ICON_RELS:
                kind = "icon"
                self.declared_icons.append(url)
            else:
                kind = "asset"
            self.refs.append((kind, url))
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() in IMAGE_META_PROPS:
            url = (a.get("content") or "").strip()
            if url:
                self.refs.append(("meta", url))


def _vite_base(dist: Path) -> str:
    """Read `base` from the sibling vite.config.ts (default '/' per decision #1)."""
    cfg = dist.parent / "vite.config.ts"
    try:
        src = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "/"
    m = VITE_BASE_RE.search(src)
    if not m:
        return "/"
    base = m.group(1).strip()
    if base.startswith("./") or base == ".":  # relative base -> no URL prefix
        return "/"
    if not base.startswith("/"):
        base = "/" + base
    return base if base.endswith("/") else base + "/"


def _resolve_ref(dist: Path, base: str, url: str) -> tuple[str, Path | str | None]:
    """Map one index.html reference onto a file inside `dist`.

    Returns (status, payload):
      ok       -> (status, Path) real file inside dist
      missing  -> (status, primary dist-relative candidate that does not exist)
      external -> (status, raw url) http(s)/protocol-relative/other scheme
      escape   -> (status, raw url) `..` / backslash / resolved outside dist
      skip     -> (status, None) empty / fragment-only / data: etc.
    """
    raw = url.strip()
    if not raw:
        return ("skip", None)
    low = raw.lower()
    if low.startswith(SKIP_SCHEMES):
        return ("skip", None)
    if low.startswith("//") or SCHEME_RE.match(low):
        return ("external", raw)
    path = raw.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return ("skip", None)
    if "\\" in path or ".." in path:
        return ("escape", raw)

    candidates: list[str] = []
    if path.startswith("/"):
        rel = path.lstrip("/")
        prefix = base.lstrip("/")
        if prefix and rel.startswith(prefix):
            stripped = rel[len(prefix) :]
            if stripped:
                candidates.append(stripped)
        # Dual-serve (decision #2): the SAME dist is mounted at "/" and at
        # "/alt", so a legacy /alt/-prefixed URL resolves even when no
        # vite.config.ts sits next to this dist (relocated / CI copies).
        if rel == "alt":
            candidates.append("index.html")
        elif rel.startswith("alt/"):
            candidates.append(rel[4:])
        if rel:
            candidates.append(rel)
    else:
        # index.html lives at the dist root, so relative refs resolve there.
        candidates.append(path.lstrip("/"))

    seen: set[str] = set()
    ordered = [c for c in candidates if c and not (c in seen or seen.add(c))]
    if not ordered:
        return ("missing", path)

    dist_root = dist.resolve()
    primary = ordered[0]
    for cand in ordered:
        target = dist / cand
        try:
            target.resolve().relative_to(dist_root)
        except ValueError:
            return ("escape", raw)
        if target.is_file():
            return ("ok", target)
    return ("missing", primary)


def _check_manifest(
    dist: Path, base: str, declared: str | None, resolved: Path | None, problems: list[str]
) -> None:
    """(c) second half: manifest present + every declared icon exists."""
    manifest_path: Path | None = resolved
    if declared is None:
        for name in MANIFEST_FILENAMES:
            cand = dist / name
            if cand.is_file():
                manifest_path = cand
                break
        if manifest_path is None:
            problems.append(
                "MISSING: manifest.webmanifest or manifest.json not present in frontend/dist"
            )
            return
    elif manifest_path is None:
        # Declared but unresolvable — the MISSING line was already recorded
        # while resolving index.html refs.
        return

    if manifest_path.stat().st_size == 0:
        problems.append(f"BROKEN: manifest file is empty: {manifest_path.name}")
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"BROKEN: manifest is not valid JSON: {manifest_path.name} ({exc})")
        return
    if not isinstance(data, dict):
        problems.append(f"BROKEN: manifest root is not an object: {manifest_path.name}")
        return
    if not str(data.get("name") or "").strip() and not str(data.get("short_name") or "").strip():
        problems.append(f"BROKEN: manifest declares no name/short_name: {manifest_path.name}")
    icons = data.get("icons")
    if not isinstance(icons, list) or not icons:
        problems.append(f"BROKEN: manifest declares no icons: {manifest_path.name}")
        return
    for icon in icons:
        src = icon.get("src") if isinstance(icon, dict) else None
        if not src or not isinstance(src, str):
            problems.append(f"BROKEN: manifest icon entry without src: {manifest_path.name}")
            continue
        status, _payload = _resolve_ref(dist, base, src)
        if status != "ok":
            problems.append(f"MISSING: manifest icon not in frontend/dist: {src}")


_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")
_CSS_IMPORT_RE = re.compile(r"""@import\s+(['"])([^'"]+)\1""")


def _check_stylesheet(dist: Path, css: Path, problems: list[str]) -> None:
    """(f) every local url()/@import target of a bundled stylesheet exists."""
    try:
        body = css.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - unreadable file
        problems.append(f"BROKEN: stylesheet unreadable: {css.name} ({exc})")
        return
    rel = css.relative_to(dist).as_posix()
    urls = [m.group(2).strip() for m in _CSS_URL_RE.finditer(body)]
    urls += [m.group(2).strip() for m in _CSS_IMPORT_RE.finditer(body)]
    for url in sorted(set(urls)):
        if not url or url.startswith(SKIP_SCHEMES) or url.startswith("//"):
            continue
        if SCHEME_RE.match(url.lower()):
            problems.append(f"BROKEN: external reference in stylesheet: {rel} {url}")
            continue
        path = url.split("#", 1)[0].split("?", 1)[0]
        if not path or ".." in path or "\\" in path:
            problems.append(f"BROKEN: reference escapes frontend/dist: {rel} {url}")
            continue
        if path.startswith("/"):
            status, _payload = _resolve_ref(dist, _vite_base(dist), path)
            if status != "ok":
                problems.append(f"MISSING: stylesheet target not in frontend/dist: {rel} {url}")
            continue
        target = (css.parent / path).resolve()
        try:
            target.relative_to(dist.resolve())
        except ValueError:
            problems.append(f"BROKEN: reference escapes frontend/dist: {rel} {url}")
            continue
        if not target.is_file():
            problems.append(f"MISSING: stylesheet target not in frontend/dist: {rel} {url}")


def validate(dist: Path) -> tuple[list[str], str]:
    """Run every CONTRACT #11 assertion. Returns (problems, OK-line summary)."""
    if not dist.is_dir():
        return ([f"MISSING: frontend dist directory not found: {dist}"], "")

    index = dist / "index.html"
    if not index.is_file():
        return ([f"MISSING: index.html not found in {dist}"], "")
    if index.stat().st_size == 0:
        return ([f"MISSING: index.html is empty: {index}"], "")

    problems: list[str] = []
    text = index.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return ([f"MISSING: index.html is empty: {index}"], "")

    # (d) no external http(s) URL anywhere in index.html (offline boot).
    for url in sorted({m.group(0) for m in EXTERNAL_URL_RE.finditer(text)}):
        problems.append(f"BROKEN: external http(s) URL in index.html: {url}")

    collector = _IndexCollector()
    try:
        collector.feed(text)
        collector.close()
    except Exception as exc:  # malformed HTML must fail loud, never crash CI
        return ([f"BROKEN: index.html could not be parsed: {exc}"], "")

    base = _vite_base(dist)
    resolved: list[tuple[str, Path]] = []  # (rel-path posix, file)
    for kind, url in collector.refs:
        status, payload = _resolve_ref(dist, base, url)
        if status == "skip":
            continue
        if status == "external":
            problems.append(f"BROKEN: external reference in index.html: {url}")
            continue
        if status == "escape":
            problems.append(f"BROKEN: reference escapes frontend/dist: {url}")
            continue
        if status == "missing":
            problems.append(f"MISSING: referenced file not in frontend/dist: {url}")
            continue
        assert isinstance(payload, Path)
        if kind == "manifest" and collector.declared_manifest == url:
            manifest_resolved: Path | None = payload
        resolved.append((payload.relative_to(dist).as_posix(), payload))

    # (b)+(a): collect hashed asset refs actually referenced by index.html.
    hashed_js = [
        rel
        for rel, _ in resolved
        if rel.startswith("assets/") and rel.endswith(".js") and HASHED_ASSET_RE.search(rel)
    ]
    hashed_css = [
        rel
        for rel, _ in resolved
        if rel.startswith("assets/") and rel.endswith(".css") and HASHED_ASSET_RE.search(rel)
    ]
    if not hashed_js:
        problems.append("MISSING: no hashed assets/*.js referenced by index.html")
    if not hashed_css:
        problems.append("MISSING: no hashed assets/*.css referenced by index.html")
    # A page that only modulepreload-links its JS/CSS never executes or
    # applies it — the contract's "JS/CSS missing" must mean an actual load.
    if not collector.script_srcs:
        problems.append("MISSING: index.html declares no <script src> (JS never loads)")
    if not collector.stylesheet_hrefs:
        problems.append("MISSING: index.html declares no stylesheet <link> (CSS never applies)")

    # (a) referenced hashed assets must exist (done above) — non-empty check (e).
    for rel, path in resolved:
        if rel.startswith("assets/") and path.stat().st_size == 0:
            problems.append(f"BROKEN: referenced asset file is empty: {rel}")

    # (c) favicon links.
    if not collector.declared_icons:
        problems.append("MISSING: index.html declares no favicon/apple-touch-icon link")

    # (c) manifest + icons.
    manifest_resolved: Path | None = None
    for kind, url in collector.refs:
        if kind == "manifest":
            status, payload = _resolve_ref(dist, base, url)
            if status == "ok" and isinstance(payload, Path):
                manifest_resolved = payload
    _check_manifest(dist, base, collector.declared_manifest, manifest_resolved, problems)

    # (f) stylesheet url()/@import targets must exist inside dist (a CSS that
    # points at a dropped asset is a broken /assets ref, same class as (b)).
    for css in sorted(p for p in dist.rglob("*.css") if p.is_file()):
        _check_stylesheet(dist, css, problems)

    # De-duplicate while preserving order (one line per distinct defect).
    unique: list[str] = []
    seen_problems: set[str] = set()
    for p in problems:
        if p not in seen_problems:
            seen_problems.add(p)
            unique.append(p)

    summary = (
        f"index.html + {len(resolved)} resolved refs "
        f"({len(hashed_js)} hashed js / {len(hashed_css)} hashed css), "
        f"favicon + manifest, no external URLs, all refs inside dist"
    )
    return (unique, summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a built frontend/dist against CONTRACT frozen decision #11."
    )
    parser.add_argument(
        "dist",
        nargs="?",
        default="frontend/dist",
        help="path to the built dist directory (default: frontend/dist)",
    )
    args = parser.parse_args(argv)
    problems, summary = validate(Path(args.dist))
    if problems:
        for line in problems:
            print(line)
        return 1
    print(f"OK: frontend/dist validated — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
