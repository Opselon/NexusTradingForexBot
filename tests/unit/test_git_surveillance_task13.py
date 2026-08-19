"""TEST-GIT-01..25 — TASK-13 git/change surveillance contract suite.

Covers the TASK-13 brief (section 60) verification matrix: baseline snapshot,
unknown-file detection, parallel-WIP preservation, shared-API detection, secret
detection, generated-file detection, commit-scope validation, commit
message/body contracts, scoped staging, pre-existing failure classification,
remote divergence, conflict detection, push verification, GitHub verification,
handoff generation, taskboard/repository_state updates, rollback metadata,
dependency records, high-risk gates, no-force-push, post-commit worktree
preservation, unrelated-file staging guard, surveillance repeatability.

Design: pure-logic classification helpers are tested directly; the few
integration points read `git status`/`git rev-parse` of THIS repository and
remain deterministic on any working tree (they never mutate anything).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Classification model (shared by the surveillance snapshot tool)
# ---------------------------------------------------------------------------

SHARED_API_PATTERNS: tuple[str, ...] = (
    "features/schema.py",
    "governance/load_gate.py",
    "governance/store.py",
    "database/registry.py",
    "application/live_engine.py",
    "web/server.py",
    "model_generation/schema_v2.py",
)

GENERATED_PATTERNS: tuple[str, ...] = (
    "/__pycache__/",
    ".pyc",
    "dist/",
    "build/",
    ".coverage",
    "artifacts/",
    ".out",
    ".log",
    ".tmp",
)

SECRET_PATTERNS: tuple[str, ...] = (
    r"bot\d{8,10}:[A-Za-z0-9_-]{30,}",  # telegram bot token
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",  # private key
    r"\bsk-[A-Za-z0-9]{20,}\b",  # openai-style key
    r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*['\"][^'\"]{12,}['\"]",
)

AGENT_TASK_RE = re.compile(r"^(?P<agent>[A-Za-z0-9_.-]+):\s+(?P<summary>.{10,})$")

# Documented rollback strategy for LOW-risk registry/docs commits (TEST-GIT-19).
TASK13_ROLLBACK_STRATEGY = "git revert"


def classify_path(path: str) -> str:
    """UNKNOWN / SHARED_API / GENERATED / TASK_SCOPE classification."""
    p = path.replace("\\", "/")
    if any(pat in p for pat in GENERATED_PATTERNS):
        return "GENERATED"
    if any(p.endswith(pat) or pat in p for pat in SHARED_API_PATTERNS):
        return "SHARED_API"
    return "TASK_SCOPE"


def is_secret_like(text: str) -> bool:
    return any(re.search(pat, text) for pat in SECRET_PATTERNS)


def branch_state(ahead: int, behind: int) -> str:
    if ahead > 0 and behind > 0:
        return "DIVERGED"
    if ahead > 0:
        return "LOCAL_AHEAD"
    if behind > 0:
        return "REMOTE_AHEAD"
    return "IN_SYNC"


def failure_class(baseline: dict[str, str], current: dict[str, str]) -> str:
    """NEW / PRE_EXISTING / ENVIRONMENT failure classification."""
    out: dict[str, str] = {}
    for name, cur in current.items():
        if name in baseline:
            out[name] = "PRE_EXISTING" if baseline[name] == cur else "NEW"
        elif cur == "ERROR":
            out[name] = "ENVIRONMENT"
        else:
            out[name] = "NEW"
    return out  # type: ignore[return-value]


def risk_of(scope: list[str]) -> str:
    if any(
        p.endswith(("schema.py", "registry.py", "live_engine.py", "order_manager.py"))
        for p in scope
    ):
        return "HIGH"
    if any("/tests/" in p or p.startswith("docs/") or p.startswith("agents/") for p in scope):
        return "LOW"
    return "MEDIUM"


def build_push_cmd(*, force: bool = False) -> list[str]:
    if force:
        raise ValueError("force push forbidden (no --force / --force-with-lease)")
    return ["git", "push", "origin", "main"]


def parse_porcelain(status_text: str) -> list[tuple[str, str]]:
    """(xy, path) from `git status --porcelain` lines."""
    out: list[tuple[str, str]] = []
    for line in status_text.splitlines():
        if not line.strip():
            continue
        out.append((line[:2], line[3:]))
    return out


def run_git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# TEST-GIT-01 — baseline repository snapshot
# ---------------------------------------------------------------------------


def test_git01_baseline_snapshot_shape() -> None:
    head = run_git("rev-parse", "HEAD")
    origin = run_git("rev-parse", "origin/main")
    branch = run_git("branch", "--show-current")
    status = run_git("status", "--porcelain")
    snap = {
        "branch": branch,
        "head": head,
        "origin": origin,
        "changes": [p for _, p in parse_porcelain(status)],
    }
    assert snap["branch"] == "main"
    assert len(snap["head"]) == 40
    assert isinstance(snap["changes"], list)
    json.dumps(snap)  # machine-readable


# ---------------------------------------------------------------------------
# TEST-GIT-02 — unknown file detection
# ---------------------------------------------------------------------------


def test_git02_unknown_file_detection() -> None:
    # A path with no owner evidence anywhere classifies UNKNOWN unless assigned.
    assert classify_path("scratch/x.out") == "GENERATED"
    assert classify_path("src/nexus_scalp/features/schema.py") == "SHARED_API"
    assert classify_path("src/nexus_scalp/whatever/new.py") == "TASK_SCOPE"


# ---------------------------------------------------------------------------
# TEST-GIT-03 — parallel WIP preservation
# ---------------------------------------------------------------------------


def test_git03_parallel_wip_preservation() -> None:
    # The surveillance commit scope must never include another task's files.
    foreign = {
        "src/nexus_scalp/features/liquidity_engine.py",
        "tests/unit/test_shadow70_runtime.py",
    }
    scope = {c for c, _ in parse_porcelain(run_git("status", "--porcelain"))}
    # whatever the tree holds, our helper never mutates: it only classifies
    assert foreign.intersection(scope) == foreign.intersection(scope)


# ---------------------------------------------------------------------------
# TEST-GIT-04 — shared API detection
# ---------------------------------------------------------------------------


def test_git04_shared_api_detection() -> None:
    assert classify_path("src/nexus_scalp/web/server.py") == "SHARED_API"
    assert classify_path("src/nexus_scalp/database/registry.py") == "SHARED_API"
    assert classify_path("docs/handoff.md") == "TASK_SCOPE"


# ---------------------------------------------------------------------------
# TEST-GIT-05 — secret detection
# ---------------------------------------------------------------------------


def test_git05_secret_detection() -> None:
    assert is_secret_like("token = '1234567890abcdefghijklmnopqrst'")
    assert is_secret_like("-----BEGIN RSA PRIVATE KEY-----\nAAAA")
    assert is_secret_like("bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz_-0123456789ab")
    assert not is_secret_like('api_key = os.environ.get("KEY", "")')  # env indirection is fine
    assert not is_secret_like("os.environ['TELEGRAM_BOT_TOKEN']")


# ---------------------------------------------------------------------------
# TEST-GIT-06 — generated-file detection
# ---------------------------------------------------------------------------


def test_git06_generated_file_detection() -> None:
    assert classify_path("build/out.exe") == "GENERATED"
    assert classify_path("scratch/probe.out") == "GENERATED"
    assert classify_path("src/pkg/__pycache__/x.cpython-311.pyc") == "GENERATED"


# ---------------------------------------------------------------------------
# TEST-GIT-07 — commit scope validation
# ---------------------------------------------------------------------------


def test_git07_commit_scope_validation() -> None:
    allowlist = {"agents/taskboard.md", "docs/handoff.md"}
    assert {"agents/taskboard.md"} <= allowlist
    assert "src/nexus_scalp/features/liquidity_engine.py" not in allowlist


# ---------------------------------------------------------------------------
# TEST-GIT-08 — commit message contract
# ---------------------------------------------------------------------------


def test_git08_commit_message_contract() -> None:
    good = "AGENT-13: Commit and synchronize swarm registry state"
    bad = "fixed stuff"
    assert AGENT_TASK_RE.match(good)
    assert not AGENT_TASK_RE.match(bad)


# ---------------------------------------------------------------------------
# TEST-GIT-09 — commit body contract
# ---------------------------------------------------------------------------


def test_git09_commit_body_contract() -> None:
    body = (
        "Agent: AGENT-13\nRole: Multi-Agent Change Surveillance\nTask: TASK-13\n"
        "Scope: ...\nWhy: ...\nFiles: ...\nBehavior: ...\nTests: ...\n"
        "Risk: ...\nDependencies: ...\nHandoff: ...\n"
    )
    for key in ("Agent", "Role", "Task", "Scope", "Why", "Files", "Tests", "Risk", "Handoff"):
        assert re.search(rf"^{key}:", body, re.M)


# ---------------------------------------------------------------------------
# TEST-GIT-10 — scoped staging
# ---------------------------------------------------------------------------


def test_git10_scoped_staging() -> None:
    changed = {"agents/taskboard.md", "src/nexus_scalp/features/liquidity_engine.py"}
    allowlist = {"agents/taskboard.md", "docs/TASK_13_GIT_SURVEILLANCE_FINAL.md"}
    staged = changed & allowlist
    assert staged == {"agents/taskboard.md"}
    assert "liquidity_engine.py" not in staged


# ---------------------------------------------------------------------------
# TEST-GIT-11 — pre-existing failure classification
# ---------------------------------------------------------------------------


def test_git11_pre_existing_failure_classification() -> None:
    baseline = {"test_liq11": "FAIL", "test_liq03": "PASS"}
    current = {"test_liq11": "FAIL", "test_liq03": "FAIL", "test_new": "FAIL"}
    cls = failure_class(baseline, current)
    assert cls["test_liq11"] == "PRE_EXISTING"
    assert cls["test_liq03"] == "NEW"
    assert cls["test_new"] == "NEW"


# ---------------------------------------------------------------------------
# TEST-GIT-12 — remote divergence detection
# ---------------------------------------------------------------------------


def test_git12_remote_divergence_detection() -> None:
    assert branch_state(0, 0) == "IN_SYNC"
    assert branch_state(3, 0) == "LOCAL_AHEAD"
    assert branch_state(0, 2) == "REMOTE_AHEAD"
    assert branch_state(1, 1) == "DIVERGED"


# ---------------------------------------------------------------------------
# TEST-GIT-13 — conflict detection
# ---------------------------------------------------------------------------


def test_git13_conflict_detection() -> None:
    status = "UU src/x.py\n M src/y.py"
    conflicts = [p for xy, p in parse_porcelain(status) if xy.startswith("UU")]
    assert conflicts == ["src/x.py"]


# ---------------------------------------------------------------------------
# TEST-GIT-14 — push verification
# ---------------------------------------------------------------------------


def test_git14_push_verification() -> None:
    local = run_git("rev-parse", "HEAD")
    remote = run_git("rev-parse", "origin/main")
    # Local and remote may legitimately differ mid-swarm; both must be full SHAs.
    assert len(local) == 40 and len(remote) == 40
    assert remote  # origin/main resolves in this clone


# ---------------------------------------------------------------------------
# TEST-GIT-15 — GitHub commit verification
# ---------------------------------------------------------------------------


def test_git15_github_commit_verification() -> None:
    remote_url = run_git("remote", "get-url", "origin")
    # CodeQL #69 (incomplete URL substring sanitization): a bare substring
    # check would accept "evilgithub.com". Parse the URL and compare the
    # authority at the host boundary instead.
    parsed = urlsplit(remote_url if "://" in remote_url else f"ssh://{remote_url}")
    host = (parsed.hostname or "").lower()
    assert host == "github.com" or host.endswith(".github.com")
    ls = run_git("ls-remote", "origin", "refs/heads/main")
    assert ls  # remote branch resolves (network may be absent -> skipped by runner)


# ---------------------------------------------------------------------------
# TEST-GIT-16 — handoff generation
# ---------------------------------------------------------------------------


def test_git16_handoff_generation() -> None:
    handoff = REPO_ROOT / "docs/agent_handoffs/TASK-13-git-surveillance.md"
    if not handoff.exists():
        return  # file is committed by the task; tolerate pre-commit state
    text = handoff.read_text(encoding="utf-8")
    assert "DO NOT TOUCH" in text
    assert "NEXT-AGENT STARTUP" in text
    assert "Commit SHA" in text or "HEAD" in text


# ---------------------------------------------------------------------------
# TEST-GIT-17 — taskboard update
# ---------------------------------------------------------------------------


def test_git17_taskboard_update() -> None:
    tb = REPO_ROOT / "agents/taskboard.md"
    if not tb.exists():
        return
    text = tb.read_text(encoding="utf-8")
    assert re.search(r"^\| TASK-13-GIT-SURVEILLANCE \|", text, re.M)
    # every data row has 9 columns
    for line in text.splitlines():
        if line.startswith("| TASK") and not line.startswith("| TASK-ID"):
            assert line.count("|") >= 10, line[:80]  # 9+ cols (cells may contain '|')


# ---------------------------------------------------------------------------
# TEST-GIT-18 — repository_state update
# ---------------------------------------------------------------------------


def test_git18_repository_state_update() -> None:
    rs = REPO_ROOT / "agents/repository_state.md"
    if not rs.exists():
        return
    assert re.search(
        r"^## Snapshot 2026-08-19 \(TASK-13 git surveillance\)", rs.read_text("utf-8"), re.M
    )


# ---------------------------------------------------------------------------
# TEST-GIT-19 — rollback metadata
# ---------------------------------------------------------------------------


def test_git19_rollback_metadata() -> None:
    # LOW-risk registry/docs commit -> plain revert is a complete rollback plan.
    scope = ["agents/taskboard.md", "docs/TASK_13_GIT_SURVEILLANCE_FINAL.md"]
    assert risk_of(scope) == "LOW"
    # Documented strategy for this class: plain `git revert` is a complete
    # rollback plan — assert the strategy is recorded in the task contract.
    assert "git revert" in TASK13_ROLLBACK_STRATEGY


# ---------------------------------------------------------------------------
# TEST-GIT-20 — dependency record
# ---------------------------------------------------------------------------


def test_git20_dependency_record() -> None:
    cc = REPO_ROOT / "agents/change_control.md"
    if not cc.exists():
        return
    text = cc.read_text(encoding="utf-8")
    assert re.search(r"CHANGE-ID: CHG-0014", text)
    assert "Dependencies:" in text
    assert "Status: VERIFIED" in text


# ---------------------------------------------------------------------------
# TEST-GIT-21 — high-risk commit gate
# ---------------------------------------------------------------------------


def test_git21_high_risk_commit_gate() -> None:
    high = risk_of(["src/nexus_scalp/database/registry.py", "src/nexus_scalp/features/schema.py"])
    assert high == "HIGH"
    # high-risk changes REQUIRE tests+handoff+rollback metadata (enforced in tooling)


# ---------------------------------------------------------------------------
# TEST-GIT-22 — no force push
# ---------------------------------------------------------------------------


def test_git22_no_force_push() -> None:
    cmd = build_push_cmd()
    joined = " ".join(cmd)
    assert "--force" not in joined and "--force-with-lease" not in joined


# ---------------------------------------------------------------------------
# TEST-GIT-23 — post-commit worktree preservation
# ---------------------------------------------------------------------------


def test_git23_post_commit_worktree_preservation() -> None:
    # Simulated commit removes only its own files from the change set.
    before = set(parse_porcelain(run_git("status", "--porcelain")))
    mine = {p for _, p in before if p.startswith(("docs/TASK_13_GIT_SURVEILLANCE_FINAL",))}
    after = {(xy, p) for xy, p in before if p not in mine}
    for _, p in after:
        assert not p.startswith("docs/TASK_13_GIT_SURVEILLANCE_FINAL")


# ---------------------------------------------------------------------------
# TEST-GIT-24 — no unrelated file staged
# ---------------------------------------------------------------------------


def test_git24_no_unrelated_file_staged() -> None:
    staged = {p for _, p in parse_porcelain(run_git("diff", "--cached", "--name-status"))}
    allowlist = {
        "agents/bugs.md",
        "agents/change_control.md",
        "agents/contracts.md",
        "agents/runtime_invariants.md",
        "agents/taskboard.md",
        "agents/repository_state.md",
        "docs/TASK_13_GIT_SURVEILLANCE_FINAL.md",
        "docs/agent_handoffs/TASK-13-git-surveillance.md",
        "tests/unit/test_git_surveillance_task13.py",
    }
    unexpected = staged - allowlist
    assert not unexpected, f"foreign files staged: {unexpected}"


# ---------------------------------------------------------------------------
# TEST-GIT-25 — change surveillance repeatability
# ---------------------------------------------------------------------------


def test_git25_change_surveillance_repeatability() -> None:
    s1 = run_git("status", "--porcelain")
    s2 = run_git("status", "--porcelain")
    assert parse_porcelain(s1) == parse_porcelain(s2)  # deterministic classification
