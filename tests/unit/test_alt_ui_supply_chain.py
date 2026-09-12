"""Supply-chain and data-exposure audit gate for the NSE Alternative UI (SEC-3/3).

Lanes covered, all enforceable OFFLINE (no network, no backend, no browser):

  1. Dependency allowlist / pin gate  — frontend/package.json declared deps must
     match DEP_ALLOWLIST exactly (name -> semver range as declared). A new
     dependency is only admissible by editing DEP_ALLOWLIST in THIS file, which
     makes every supply-chain addition a visible, reviewable code change.
  2. Install-script / lifecycle-hook audit — no preinstall/postinstall/prepare
     in the project's own package.json; no `file:`/`git:`/link deps; no
     `scripts` blocks on package-lock entries; every `bin` hook must be on the
     known-bin allowlist (reported, not silently accepted).
  3. Lockfile + gitignore pin — package-lock.json committed and integrity-pinned;
     frontend/dist ignored (per TASK-ALT-UI row: dist is a build artifact).
  4. dist hygiene — vite build config must have sourcemap:false; no *.map
     emitted; no sourceMappingURL refs; secret scan over dist + src.
  5. No-CDN rule (BUG-047 lineage) — index.html must carry no remote
     script/link/img src; src tree carries no remote URL literal; dist URL
     literals must be on the benign allowlist (W3C namespace URIs / library
     doc-string URLs only — never a fetch target).
  6. Privacy: operator payloads never leave the authenticated origin — every
     network target in the UI source is a root-relative path; no absolute-URL
     fetch/XHR/EventSource/WebSocket/beacon sink; the WEB-AUTH-P0 token lives in
     sessionStorage only; localStorage keys are a visual-preference allowlist.

Secret/URL findings report ``file:line`` (plus a truncated, non-secret shape
hint) and NEVER the matched value.

The raw-``fetch()``-outside-src/api rule is already pinned by
tests/unit/test_alt_ui_runtime_contract.py::TestUiSourceContract
/test_no_scattered_fetch_outside_api_layer. This module does NOT re-implement
it; it asserts that guard still exists and adds the orthogonal checks (absolute
URL sinks, remote refs, secret material, dependency policy).

Run (repo root, repo venv only):
  C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe \
      -m pytest -q tests/unit/test_alt_ui_supply_chain.py
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
PKG_JSON = FRONTEND / "package.json"
LOCK_JSON = FRONTEND / "package-lock.json"
VITE_CONFIG = FRONTEND / "vite.config.ts"
INDEX_HTML = FRONTEND / "index.html"
SRC_DIR = FRONTEND / "src"
DIST_DIR = FRONTEND / "dist"

SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".css"}

# ---------------------------------------------------------------------------
# 1. Dependency allowlist (the pin gate). Edit ONLY here, with a rationale in
#    the PR body + a row in docs/alt-ui-supply-chain.md.
# ---------------------------------------------------------------------------

#: Declared dependencies of frontend/package.json, name -> declared semver
#: range (exactly as committed 2026-09-13 after the React 19 / Vite 8 / TS 7
#: upgrade wave landed mid-audit at 01:52 local — this allowlist gate flagged
#: that change, see docs/alt-ui-supply-chain.md §10).
DEP_ALLOWLIST: dict[str, str] = {
    # runtime
    "@tanstack/react-query": "^5.62.0",
    "react": "^19.2.8",
    "react-dom": "^19.2.8",
    "react-router-dom": "^7.18.3",
    "zustand": "^5.0.2",
    # build / typecheck only (never shipped to the browser)
    "@types/react": "^19.2.18",
    "@types/react-dom": "^19.2.7",
    "@vitejs/plugin-react": "^6.1.1",
    "typescript": "~7.0.2",
    "vite": "^8.2.2",
}

#: npm lifecycle hooks that must never appear in the project's own scripts.
FORBIDDEN_LIFECYCLE_HOOKS = ("preinstall", "install", "postinstall", "prepare", "prepublish")

#: Known CLI shims that package-lock may declare as ``bin`` entries. These are
#: ordinary (they only materialise inside node_modules/.bin during a local
#: build; nothing is served to the browser). Any NEW bin hook fails the gate
#: and must be reviewed, then added here with a rationale.
#: (Vite 8 is rolldown-based: ``rollup``/``esbuild``/browserslist-era shims are
#: gone from the graph; ``rolldown`` is their successor build-tool shim.)
ALLOWED_TOP_LEVEL_BINS = {
    "nanoid",
    "rolldown",
    "tsc",  # provided by typescript
    "tsserver",  # provided by typescript (where published)
    "vite",
}

#: Install-phase scripts that are tolerated in the resolved graph because they
#: are platform-conditional and upstream-standard. Reported by the gate, never
#: silently: a new one fails. The project's own package.json must have none.
ALLOWED_HAS_INSTALL_SCRIPT = {
    # optional, darwin-only file watcher; inert on this Windows build host and
    # on CI Linux. (Vite 8's rolldown replaces esbuild with pure optional
    # platform bindings that carry NO install script — confirmed by lock scan.)
    "node_modules/fsevents": "2.3.3",
}


# ---------------------------------------------------------------------------
# 5. No-CDN allowlists
# ---------------------------------------------------------------------------

#: Absolute-URL hosts permitted inside the BUILT bundle. Each one appears only as
#: an XML namespace identifier (React DOM's namespace table) or inside a library
#: error/warning string — never as a network target. The gate separately proves no
#: ``fetch``/``XHR``/``EventSource`` argument is an absolute URL.
ALLOWED_DIST_URL_HOSTS = {
    "localhost",  # react-router's URL-parsing probe base (never fetched)
    "www.w3.org",  # XML/MathML/SVG/XHTML namespace identifiers
    "reactjs.org",  # React error-decoder doc link
    "reactrouter.com",  # router warning doc link
}

#: Hosts that may never appear anywhere in the UI (BUG-047 CDN lineage).
FORBIDDEN_CDN_HOSTS = (
    "cdn.tailwindcss.com",
    "unpkg.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "ajax.googleapis.com",
    "esm.sh",
    "sheetjs.com",
)

# ---------------------------------------------------------------------------
# 6. Privacy allowlists
# ---------------------------------------------------------------------------

#: localStorage keys the UI may write — visual preferences only, never NSE or
#: operator state, never credentials (same rule as the legacy dashboard).
ALLOWED_LOCALSTORAGE_KEYS = {
    "nexus.ui.lang",  # language pref, shared with legacy Web/ dashboard
    "nse.altui.sidebar",
    "nse.altui.dense",
}

#: sessionStorage key reserved for the WEB-AUTH-P0 token. Token material must
#: not appear in localStorage, cookies, or any persisted operator payload.
TOKEN_STORAGE_KEY = "nse.altui.token"

API_LAYER = "src/api"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _source_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix in SOURCE_SUFFIXES]


def _strip_comments(text: str) -> str:
    """Remove //, /* */ and HTML comment content so doc URLs in prose never
    count as code. Conservative: only used for the remote-URL code scan."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", " ", text, flags=re.M)
    return text


def _rel(p: Path) -> str:
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _entropy(sample: str) -> float:
    n = len(sample)
    if n == 0:
        return 0.0
    counts = Counter(sample)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


@pytest.fixture(scope="module")
def pkg() -> dict:
    assert PKG_JSON.is_file(), f"missing {_rel(PKG_JSON)}"
    return json.loads(_read(PKG_JSON))


@pytest.fixture(scope="module")
def lock() -> dict:
    assert LOCK_JSON.is_file(), f"missing lockfile {_rel(LOCK_JSON)}"
    return json.loads(_read(LOCK_JSON))


# ---------------------------------------------------------------------------
# 1. dependency allowlist / pin gate
# ---------------------------------------------------------------------------


class TestDependencyAllowlist:
    def test_package_json_exists_and_is_private(self, pkg: dict) -> None:
        assert pkg.get("private") is True, "frontend must stay private (never publishable)"

    def test_declared_deps_match_allowlist(self, pkg: dict) -> None:
        declared = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }
        unexpected = {k: v for k, v in declared.items() if k not in DEP_ALLOWLIST}
        removed = {k: v for k, v in DEP_ALLOWLIST.items() if k not in declared}
        drifted = {
            k: (declared[k], DEP_ALLOWLIST[k])
            for k in declared
            if k in DEP_ALLOWLIST and declared[k] != DEP_ALLOWLIST[k]
        }
        assert declared, "no dependencies declared — did package.json move?"
        assert not unexpected, (
            f"NON-ALLOWLISTED npm dependency: {unexpected} — add it to DEP_ALLOWLIST in "
            f"{_rel(Path(__file__))} with a written rationale (docs/alt-ui-supply-chain.md) "
            "before it may be installed."
        )
        assert not drifted, f"declared semver range drifted from the audited pin: {drifted}"
        assert not removed, (
            f"allowlist names a dependency no longer declared: {removed} — update the "
            "allowlist and the audit doc in the same change."
        )

    def test_no_optional_or_bundled_dep_channels(self, pkg: dict) -> None:
        for key in ("optionalDependencies", "bundledDependencies", "bundleDependencies", "overrides"):
            assert key not in pkg, f"frontend/package.json must not declare {key}"

    def test_no_file_or_git_or_link_specs(self, pkg: dict) -> None:
        declared = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        bad = {
            k: v
            for k, v in declared.items()
            if re.match(r"^(file:|link:|workspace:|git\+|git:|github:|https?://|git://)", str(v))
        }
        assert not bad, f"non-registry dependency spec (unauditable source): {bad}"

    def test_scripts_are_build_only_and_free_of_lifecycle_hooks(self, pkg: dict) -> None:
        scripts = pkg.get("scripts", {})
        assert set(scripts) <= {"dev", "build", "preview", "typecheck"}, (
            f"unexpected npm scripts: {sorted(set(scripts) - {'dev', 'build', 'preview', 'typecheck'})}"
        )
        hooks = sorted(k for k in scripts if k in FORBIDDEN_LIFECYCLE_HOOKS)
        assert not hooks, f"npm lifecycle hooks in frontend/package.json: {hooks}"

    def test_build_script_typechecks_before_bundling(self, pkg: dict) -> None:
        # `tsc -b && vite build` — a failing type check must not yield a dist.
        assert "tsc" in pkg["scripts"]["build"], "build must run tsc before vite build"


# ---------------------------------------------------------------------------
# 2. lockfile audit (scripts / bin hooks / integrity)
# ---------------------------------------------------------------------------


class TestLockfileAudit:
    def test_lockfile_is_committed_and_versioned(self, lock: dict) -> None:
        assert lock.get("lockfileVersion") in (2, 3), "expected npm lockfile v2/v3"
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "frontend/package-lock.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,  # non-zero is the finding (untracked lockfile), not an error
        )
        assert tracked.returncode == 0, "package-lock.json must be committed (pin gate)"

    def test_no_scripts_blocks_on_any_lock_entry(self, lock: dict) -> None:
        offenders = [
            f"{k}#{json.dumps(v.get('scripts'))}"
            for k, v in lock.get("packages", {}).items()
            if v.get("scripts")
        ]
        assert not offenders, f"lockfile entries carrying install scripts: {offenders}"

    def test_every_resolved_package_is_registry_pinned_with_integrity(self, lock: dict) -> None:
        pk = lock.get("packages", {})
        offenders: list[str] = []
        for name, entry in pk.items():
            if name == "":  # the root project entry
                continue
            if entry.get("link"):
                offenders.append(f"{name}: link dependency")
                continue
            resolved = entry.get("resolved") or ""
            if not resolved.startswith("https://registry.npmjs.org/"):
                offenders.append(f"{name}: resolved host {resolved.split('/')[2:3] or ['<none>']}")
            if not entry.get("integrity", "").startswith("sha512-"):
                offenders.append(f"{name}: missing sha512 integrity")
        assert not offenders, f"unpinned / foreign-origin packages: {offenders}"

    def test_top_level_bin_hooks_are_the_known_set(self, lock: dict) -> None:
        pk = lock.get("packages", {})
        found: dict[str, str] = {}
        offenders: list[str] = []
        for name, entry in pk.items():
            if not name.startswith("node_modules/") or name.count("/") != 1:
                continue
            bin_spec = entry.get("bin")
            if not bin_spec:
                continue
            names = [name.rsplit("/", 1)[-1]] if isinstance(bin_spec, str) else sorted(bin_spec)
            for b in names:
                found[b] = name
                if b not in ALLOWED_TOP_LEVEL_BINS:
                    offenders.append(f"{b} (from {name})")
        assert not offenders, (
            "NEW top-level bin hook in the lockfile — npm bin shims are the usual "
            f"install-time execution vector: {sorted(offenders)}. Review, then add to "
            "ALLOWED_TOP_LEVEL_BINS with a rationale."
        )
        assert found, "expected some build-tool bin shims (vite/tsc)"

    def test_install_phase_scripts_are_the_known_set(self, lock: dict) -> None:
        pk = lock.get("packages", {})
        found = {k: v.get("version") for k, v in pk.items() if v.get("hasInstallScript")}
        unknown = {k: ver for k, ver in found.items() if k not in ALLOWED_HAS_INSTALL_SCRIPT}
        assert not unknown, (
            f"NEW package with an install-phase script: {unknown}. These run code on "
            "`npm install`; they need explicit review (see docs/alt-ui-supply-chain.md)."
        )
        for k, ver in ALLOWED_HAS_INSTALL_SCRIPT.items():
            assert found.get(k) == ver or k not in found, (
                f"{k} install-script pin drifted: lock={found.get(k)!r} audited={ver!r}"
            )

    def test_dev_tree_stays_a_build_tool_only_surface(self, lock: dict) -> None:
        """Every non-prod top-level package must be reachable from the 5 audited
        dev tools (vite/typescript/@vitejs plugin) — i.e. no orphan dev dep."""
        pk = lock.get("packages", {})
        roots = {f"node_modules/{name}" for name in ("vite", "typescript", "@vitejs/plugin-react")}
        seen: set[str] = set()

        def direct_deps(name: str) -> set[str]:
            entry = pk.get(name, {})
            out: set[str] = set()
            scope = name.rsplit("/", 1)
            parent = scope[0] if name.count("/") > 1 else ""
            all_deps = {
                **entry.get("dependencies", {}),
                **entry.get("peerDependencies", {}),
                **entry.get("optionalDependencies", {}),  # e.g. vite -> fsevents (darwin-only)
            }
            for dep in all_deps:
                out.add(f"{parent}/node_modules/{dep}" if parent else f"node_modules/{dep}")
                out.add(f"node_modules/{dep}")
            return out

        frontier = set(roots)
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            frontier |= direct_deps(cur)

        top_dev = {
            k
            for k, v in pk.items()
            if k.startswith("node_modules/") and k.count("/") == 1 and v.get("dev")
        }
        orphans = sorted(top_dev - seen)
        assert not orphans, (
            f"dev top-level packages not reachable from vite/typescript: {orphans} — "
            "they are unaccounted build-time supply surface."
        )


# ---------------------------------------------------------------------------
# 3. gitignore pin (dist is a build artifact, never committed)
# ---------------------------------------------------------------------------


class TestDistIsNotCommitted:
    def test_gitignore_covers_frontend_dist(self) -> None:
        rules: list[str] = []
        for gi in (REPO_ROOT / ".gitignore", FRONTEND / ".gitignore"):
            if gi.is_file():
                for raw in _read(gi).splitlines():
                    rule = raw.strip()
                    if rule and not rule.startswith("#"):
                        rules.append(rule)
        covers = {"frontend/dist/", "frontend/dist", "dist/"}
        assert covers & set(rules), (
            f"no .gitignore rule keeps frontend/dist out of git (TASK-ALT-UI row). Got: {rules[:50]}"
        )

    def test_no_dist_or_node_modules_files_tracked(self) -> None:
        proc = subprocess.run(
            ["git", "ls-files", "frontend/dist", "frontend/node_modules"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,  # returncode inspected below
        )
        if proc.returncode != 0:
            pytest.skip(f"git ls-files unavailable: {proc.stderr.strip()[:120]}")
        assert not proc.stdout.strip(), (
            f"build artifacts committed to git: {proc.stdout.split()[:10]}"
        )


# ---------------------------------------------------------------------------
# 4. dist hygiene: sourcemaps + secret scan
# ---------------------------------------------------------------------------

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"(?i)\baws_access_key(?:_id|_secret)?\b[\"']?\s*[:=]\s*[\"']?[A-Z0-9+/]{12,}")),
    ("private_key_block", re.compile(r"-----BEGIN\s+(?:[A-Z]+\s+)?PRIVATE\s+KEY-----")),
    ("password_literal", re.compile(r"(?i)\bpassword\b[\"']?\s*[:=]\s*[\"'][^\"'\n]{6,}[\"']")),
    ("secret_literal", re.compile(r"(?i)\b(?:client_)?secret\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9+/=_\-]{8,}[\"']")),
    (
        "token_literal",
        re.compile(r"(?i)\b[a-z_]*token\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9+/=_\-]{20,}[\"']"),
    ),
    ("bearer_literal", re.compile(r"(?i)\bauthorization\b[\"']?\s*[:=]\s*[\"']\s*bearer\s+[A-Za-z0-9+_.\-]{20,}")),
    ("nse_token_env_value", re.compile(r"(?i)NSE_WEB_AUTH_TOKEN\s*=\s*[\"']?[A-Za-z0-9+/=_\-]{16,}")),
)


def _secret_hits(files: list[Path]) -> list[str]:
    """Return ``pattern:file:line[: shape hint]`` — never the secret value."""
    out: list[str] = []
    for path in files:
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            for name, pat in SECRET_PATTERNS:
                m = pat.search(line)
                if m:
                    out.append(f"{name}:{_rel(path)}:{lineno}:{len(m.group(0))}chars")
    return out


class TestDistHygiene:
    def test_vite_config_disables_sourcemaps(self) -> None:
        cfg = _read(VITE_CONFIG)
        assert re.search(r"sourcemap\s*:\s*false", cfg), (
            "vite.config.ts must keep build.sourcemap:false — source maps ship the "
            "operator console's source to any browser client"
        )
        assert not re.search(r"sourcemap\s*:\s*(true|\{)", cfg), "no sourcemap mode may be re-enabled"
        assert re.search(r'base\s*:\s*"/alt/"', cfg), "alt console must stay mounted under /alt/"

    def test_vite_config_declares_no_dev_only_leak_into_build(self) -> None:
        cfg = _read(VITE_CONFIG)
        # The dev proxy target is env-driven and lives in `server`, never `build`.
        assert re.search(r"NSE_API_ORIGIN", cfg)
        build_block = cfg[cfg.index("build:") :] if "build:" in cfg else ""
        assert "127.0.0.1" not in build_block, "backend origin must not be baked into build config"

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_no_sourcemap_files_in_dist(self) -> None:
        maps = [p for p in DIST_DIR.rglob("*") if p.suffix == ".map"]
        assert not maps, f"sourcemaps emitted into dist: {[_rel(p) for p in maps]}"
        refs: list[str] = []
        for p in _source_files(DIST_DIR):
            for lineno, line in enumerate(_read(p).splitlines(), 1):
                if "sourceMappingURL" in line:
                    refs.append(f"{_rel(p)}:{lineno}")
        assert not refs, f"dist references a sourcemap: {refs}"

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_dist_contains_only_expected_artifacts(self) -> None:
        files = [p for p in sorted(DIST_DIR.rglob("*")) if p.is_file()]
        offenders = [
            _rel(p)
            for p in files
            if not re.fullmatch(r"(index\.html|assets/[A-Za-z0-9._-]+\.(js|css))", p.relative_to(DIST_DIR).as_posix())
        ]
        assert not offenders, f"unexpected files in dist (over-release surface): {offenders}"

    def test_no_secret_material_in_src(self) -> None:
        hits = _secret_hits([*_source_files(SRC_DIR), *([INDEX_HTML] if INDEX_HTML.is_file() else []), VITE_CONFIG])
        assert not hits, f"possible secret material in UI source: {hits}"

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_no_secret_material_in_dist(self) -> None:
        hits = _secret_hits(_source_files(DIST_DIR))
        assert not hits, f"possible secret material in built bundle: {hits}"

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_no_high_entropy_base64_lookalikes_in_dist(self) -> None:
        """Second-pass heuristic: long base64-ish runs with high entropy. Minified
        bundles contain identifiers, so the gate requires BOTH a strong base64
        alphabet signal (+=/ present) and entropy > 4.3 bits/char."""
        cand = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
        offenders: list[str] = []
        for path in _source_files(DIST_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                for m in cand.finditer(line):
                    s = m.group(0)
                    if "=" in s and "+" in s and "/" in s and _entropy(s) > 4.3:
                        offenders.append(f"{_rel(path)}:{lineno}:{len(s)}chars")
        assert not offenders, f"base64-ish high-entropy blobs in dist: {offenders[:10]}"

    def test_repo_env_files_are_not_in_the_bundle_lane(self) -> None:
        """frontend/ must not carry its own .env (a secret-sink next to a build)."""
        leaks = [p for p in FRONTEND.rglob(".env*") if p.is_file() and "node_modules" not in p.parts]
        assert not leaks, f"env files inside frontend/ tree: {[_rel(p) for p in leaks]}"


# ---------------------------------------------------------------------------
# 5. no-CDN / remote-reference rule (BUG-047 lineage)
# ---------------------------------------------------------------------------


class TestNoCdnRule:
    def test_index_html_has_no_remote_refs(self) -> None:
        html = _read(INDEX_HTML)
        offenders: list[str] = []
        for lineno, line in enumerate(html.splitlines(), 1):
            for attr in ("src", "href"):
                for m in re.finditer(rf'{attr}\s*=\s*["\']([^"\']+)["\']', line):
                    val = m.group(1)
                    if re.match(r"^(https?:)?//", val):
                        offenders.append(f"index.html:{lineno} {attr}={val[:40]}")
            for m in re.finditer(r"//([a-z0-9.-]+\.[a-z]{2,})", line, flags=re.I):
                offenders.append(f"index.html:{lineno} host {m.group(1)}")
        assert not offenders, f"remote reference in index.html (no-CDN rule BUG-047): {offenders}"

    def test_index_html_script_is_local_source_only(self) -> None:
        html = _read(INDEX_HTML)
        for m in re.finditer(r"<script[^>]*\ssrc=[\"']([^\"']+)[\"']", html):
            src = m.group(1)
            assert src.startswith("/"), f"script src must be root-relative, got {src}"

    def test_src_tree_has_no_url_literals(self) -> None:
        offenders: list[str] = []
        for path in _source_files(SRC_DIR):
            stripped = _strip_comments(_read(path))
            for lineno, line in enumerate(stripped.splitlines(), 1):
                if re.search(r"(https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]", line):
                    offenders.append(f"{_rel(path)}:{lineno}")
        assert not offenders, f"absolute/protocol-relative URL in UI source: {offenders}"

    def test_no_forbidden_cdn_hosts_anywhere_in_ui_lane(self) -> None:
        targets = [*_source_files(SRC_DIR), INDEX_HTML, VITE_CONFIG, PKG_JSON]
        if DIST_DIR.is_dir():
            targets += _source_files(DIST_DIR)
        offenders: list[str] = []
        for path in targets:
            if not path.is_file():
                continue
            text = _read(path).lower()
            for host in FORBIDDEN_CDN_HOSTS:
                if host in text:
                    offenders.append(f"{_rel(path)}:{host}")
        assert not offenders, f"CDN host present in the alt console lane: {offenders}"

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_dist_url_literals_are_allowlisted_doc_uris(self) -> None:
        """Any scheme-ful absolute URL inside the built bundle must sit on the
        allowlist by *host* (namespace/doc URIs only). Regex-internal fragments
        like ``//g`` or the react-router ``server://singlefetch/`` sentinel are
        not scheme-bearing http(s)/ws URLs and cannot reach the network."""
        url_pat = re.compile(
            r"(?:https?|wss?|ftp)://[A-Za-z0-9._~:/?%&=#@+\-]+",
            re.I,
        )
        offenders: list[str] = []
        for path in _source_files(DIST_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                for m in url_pat.finditer(line):
                    base = m.group(0).rstrip("\\'\"`,);}")
                    host = re.split(r"://", base, maxsplit=1, flags=re.I)[1].split("/")[0]
                    host = host.split(":")[0].lower()
                    if host not in ALLOWED_DIST_URL_HOSTS:
                        offenders.append(f"{_rel(path)}:{lineno}:host={host}")
        assert not offenders, (
            f"absolute URL with non-allowlisted host inside the built bundle: {offenders[:8]} — a "
            "remote URL in dist means either a CDN dependency or an exfiltration sink."
        )

    @pytest.mark.skipif(not DIST_DIR.is_dir(), reason="frontend/dist not built on this host")
    def test_dist_has_no_absolute_url_used_as_a_network_sink(self) -> None:
        """In the minified bundle, no fetch/XHR/open/EventSource argument may be a
        string literal with a scheme. API paths must stay root-relative."""
        sink = re.compile(r"(?:fetch|open|EventSource)\s*\(\s*[\"'](https?://|//[A-Za-z])")
        offenders: list[str] = []
        for path in _source_files(DIST_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if sink.search(line):
                    offenders.append(f"{_rel(path)}:{lineno}")
        assert not offenders, f"absolute-URL network sink in built bundle: {offenders}"
        assert any('/api' in _read(p) for p in _source_files(DIST_DIR)), (
            "built bundle lost its relative /api paths — serving contract broken"
        )

    def test_css_has_no_remote_import_or_url(self) -> None:
        offenders: list[str] = []
        for path in _source_files(SRC_DIR):
            if path.suffix != ".css":
                continue
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if re.search(r"@import\s+.*https?://|url\(\s*['\"]?(https?:)?//", line):
                    offenders.append(f"{_rel(path)}:{lineno}")
        assert not offenders, f"remote CSS import/url (CDN leak): {offenders}"


# ---------------------------------------------------------------------------
# 6. privacy: operator payloads never leave the authenticated origin
# ---------------------------------------------------------------------------


class TestOperatorPayloadStaysOnOrigin:
    def test_runtime_contract_fetch_guard_still_present(self) -> None:
        """Cross-check (not duplicate) the existing no-fetch-outside-api guard."""
        sibling = REPO_ROOT / "tests" / "unit" / "test_alt_ui_runtime_contract.py"
        assert sibling.is_file(), "sibling runtime-contract test moved — re-point this cross-check"
        text = _read(sibling)
        assert "test_no_scattered_fetch_outside_api_layer" in text, (
            "the raw-fetch-outside-src/api guard was removed; this lane does not "
            "re-implement it, so the protection would silently disappear."
        )

    def test_all_api_transport_targets_are_relative(self) -> None:
        transport = FRONTEND / "src" / "api" / "client.ts"
        text = _read(transport)
        assert re.search(r"fetch\(\s*path\b", text), (
            "client.ts must fetch the caller-supplied path verbatim — no base-URL concat"
        )
        assert not re.search(r"(https?://)[A-Za-z0-9.\-]+\s*\+|baseUrl\s*=\s*[\"']https?://", text), (
            "transport layer must not carry an absolute base URL"
        )
        offenders: list[str] = []
        for path in _source_files(FRONTEND / "src" / "api"):
            stripped = _strip_comments(_read(path))
            for m in re.finditer(r"""["'`](/[A-Za-z0-9/_.{}\-]*)["'`]""", stripped):
                if not m.group(1).startswith("/api") and not m.group(1).startswith("/health"):
                    offenders.append(f"{_rel(path)}:{m.group(1)}")
        assert not offenders, f"api-layer path outside the backend route families: {offenders}"

    def test_api_layer_paths_are_backend_routes_that_exist(self) -> None:
        """Operator payloads only travel to routes the FastAPI app actually serves —
        proves there is no invented endpoint (a classic exfil sink shape)."""
        server = REPO_ROOT / "src" / "nexus_scalp" / "web" / "server.py"
        assert server.is_file()
        served = set(re.findall(r"""["'](/api[A-Za-z0-9/_.\-{}]*)["']""", _read(server)))
        wanted: set[str] = set()
        for path in _source_files(FRONTEND / "src" / "api"):
            wanted |= set(re.findall(r"""["'](/api[A-Za-z0-9/_.\-{}]*)["']""", _read(path)))
        wanted |= {"/api/ticks/stream"}
        unknown = sorted(
            w for w in wanted if w not in served and not any(s.startswith(w) or w.startswith(s) for s in served)
        )
        assert not unknown, f"UI calls backend routes that do not exist: {unknown}"

    def test_no_telemetry_or_beacon_sinks(self) -> None:
        """Out-of-band network sinks and third-party telemetry must be absent from
        the whole UI lane (source AND built bundle).

        ``postMessage`` is scoped deliberately: only ``window.postMessage`` /
        ``parent.postMessage`` / ``iframe.contentWindow.postMessage`` can cross an
        origin boundary. React's scheduler uses a MessageChannel *port* postMessage
        as a purely local yield primitive — no data leaves the page — so a blanket
        ban would be a false positive that gets switched off.
        """
        files = _source_files(SRC_DIR) + ([INDEX_HTML] if INDEX_HTML.is_file() else [])
        if DIST_DIR.is_dir():
            files += _source_files(DIST_DIR)
        cross_origin = re.compile(
            r"(?:window|parent|top|contentWindow|self)\s*\.\s*postMessage\s*\("
        )
        needles = (
            "sendBeacon",
            "new XMLHttpRequest",
            "google-analytics",
            "gtag(",
            "sentry",
            "new WebSocket(",
            "navigator.clipboard.write",
        )
        offenders: list[str] = []
        for path in files:
            text = _read(path)
            for lineno, line in enumerate(text.splitlines(), 1):
                for n in needles:
                    if n in line:
                        offenders.append(f"{_rel(path)}:{lineno}:{n}")
                if cross_origin.search(line):
                    offenders.append(f"{_rel(path)}:{lineno}:cross-window postMessage")
        assert not offenders, f"out-of-band / cross-origin sink present in the UI lane: {offenders}"

    def test_realtime_transport_is_same_origin_sse(self) -> None:
        """Every string path the realtime client can dial must be a root-relative
        path on this same origin (so the browser resolves it against the
        authenticated origin, never an absolute target)."""
        rt = _strip_comments(_read(FRONTEND / "src" / "websocket" / "realtimeSocket.ts"))
        for m in re.finditer(r"""["'`]/(api|ws)[A-Za-z0-9/_.\-{}]*""", rt):
            assert m.group(0).lstrip("`\"'").startswith(("/api/", "/ws")), (
                f"unexpected dial target: {m.group(0)}"
            )
        assert "EventSource(" in rt
        assert not re.search(r"""EventSource\(\s*["'`]?(https?:|//)""", rt), (
            "SSE target must be same-origin (no scheme, no protocol-relative host)"
        )
        assert not re.search(r"(https?|ws|wss)://", _strip_comments(rt)), (
            "absolute URL literal in the realtime client"
        )

    def test_token_never_persists_outside_session_storage(self) -> None:
        offenders: list[str] = []
        for path in _source_files(SRC_DIR):
            text = _read(path)
            for lineno, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if "token" in low and (
                    "localstorage" in low or "document.cookie" in low or "indexeddb" in low
                ):
                    offenders.append(f"{_rel(path)}:{lineno}")
        assert not offenders, (
            f"WEB-AUTH-P0 token material persisted beyond sessionStorage: {offenders}"
        )
        client = _read(FRONTEND / "src" / "api" / "client.ts")
        assert f'"{TOKEN_STORAGE_KEY}"' in client or f"'{TOKEN_STORAGE_KEY}'" in client
        assert "searchParams.delete(\"token\")" in client, "token must be scrubbed from the URL"

    def test_localstorage_writes_are_visual_prefs_only(self) -> None:
        offenders: list[str] = []
        for path in _source_files(SRC_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if re.search(r"localStorage\.(setItem|getItem)\(", line) and "readPref" not in line:
                    keys = re.findall(r"""["']([A-Za-z0-9._]+)["']""", line)
                    # A call with no literal key would be dynamic — the only
                    # such helper in the lane is uiStore's readPref/writePref,
                    # whose literal call-sites are enumerated below.
                    if keys and not all(k in ALLOWED_LOCALSTORAGE_KEYS for k in keys):
                        offenders.append(f"{_rel(path)}:{lineno}:{keys}")
        assert not offenders, f"non-preference localStorage key: {offenders}"
        # Constants used as keys (i18n.ts uses LANG_KEY) must themselves resolve
        # to allowlisted literals.
        consts: dict[str, str] = {}
        for path in _source_files(SRC_DIR):
            consts.update(dict(re.findall(r"const\s+([A-Z0-9_]+)\s*=\s*[\"']([A-Za-z0-9._]+)[\"']", _read(path))))
        for path in _source_files(SRC_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if re.search(r"localStorage\.(setItem|getItem)\(", line):
                    for name, value in consts.items():
                        if re.search(rf"\b{re.escape(name)}\b", line) and value not in ALLOWED_LOCALSTORAGE_KEYS:
                            offenders.append(f"{_rel(path)}:{lineno}:{name}={value}")
        assert not offenders, f"non-preference localStorage key constant: {offenders}"
        # The store uses a key-variable helper; enumerate the literal keys it passes.
        store = _read(FRONTEND / "src" / "stores" / "uiStore.ts")
        used = set(re.findall(r"""(readPref|writePref)\(\s*["']([^"']+)["']""", store))
        keys = {k for _, k in used}
        assert keys and keys <= ALLOWED_LOCALSTORAGE_KEYS, (
            f"uiStore localStorage keys outside the visual-pref allowlist: {sorted(keys - ALLOWED_LOCALSTORAGE_KEYS)}"
        )
        assert "nse.altui.token" not in keys, "token must never be a localStorage pref"

    def test_no_operator_state_is_serialised_to_storage(self) -> None:
        """Persistence is prefs-only: no snapshot/position/order payloads."""
        needles = ("snapshot", "positions", "order", "ticket", "token")
        offenders: list[str] = []
        for path in _source_files(SRC_DIR):
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if re.search(r"(localStorage|sessionStorage)\.(setItem|getItem)\(", line):
                    low = line.lower()
                    for n in needles:
                        if n in low and "token" not in (TOKEN_STORAGE_KEY.lower() if n == "token" else ""):
                            if n == "token" and TOKEN_STORAGE_KEY.lower() not in low:
                                offenders.append(f"{_rel(path)}:{lineno}:{n}")
                            elif n != "token":
                                offenders.append(f"{_rel(path)}:{lineno}:{n}")
        assert not offenders, f"operator payload written to web storage: {offenders}"


# ---------------------------------------------------------------------------
# documentation cross-check (keeps the audit doc honest)
# ---------------------------------------------------------------------------


class TestAuditDocIsCurrent:
    DOC = REPO_ROOT / "docs" / "alt-ui-supply-chain.md"

    def test_audit_doc_exists(self) -> None:
        assert self.DOC.is_file(), "docs/alt-ui-supply-chain.md missing"

    def test_doc_declares_same_allowlist_size_as_gate(self) -> None:
        text = _read(self.DOC)
        assert f"{len(DEP_ALLOWLIST)}" in text, (
            "audit doc must state the audited dependency count so a allowlist edit "
            "shows up as a doc/test mismatch"
        )

    def test_doc_contains_real_pytest_tail_marker(self) -> None:
        assert "Automated gate" in _read(self.DOC), "missing 'Automated gate' evidence section"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
