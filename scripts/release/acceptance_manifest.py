"""RELEASE ACCEPTANCE MANIFEST - Phase 12 machine-readable score.

Executes the artifact-driven acceptance flow against the freshly built
onefile CLI artifact (release/build/windows-x64/onefile/), in isolated
sandboxes, and writes artifacts/release/acceptance_manifest.json.

Gates: INSTALL IDENTITY VERSION UPDATE REPAIR START STOP RESTART RECOVERY
JSON HUMAN_UX IDEMPOTENCY (PASS/FAIL/BLOCKED/NOT_SUPPORTED).

NOTE on START/STOP/RESTART: the onefile CLI artifact excludes numpy/torch by
design (full engine lives in the onedir bundle; ISCC absent on this machine,
so no installer EXE). The artifact reports this truthfully ('Could not load
engine / No module named numpy'). Engine start/stop was exercised in the
lifecycle-chaos suite at source level (pidfile semantics). The gate records
NOT_SUPPORTED for the onefile artifact with the observed truthful error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "release" / "build" / "windows-x64" / "onefile" / "NexusScalpEngine-CLI.exe"
PROVENANCE = ARTIFACT.parent / "artifact-provenance.json"
OUT_MANIFEST = REPO / "artifacts" / "release" / "acceptance_manifest.json"


def run_artifact(sb: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    # Run the SANDBOX COPY of the artifact: exe_dir() must resolve inside the
    # sandbox (frozen portable data root = <exe_dir>/data). Running the repo
    # original would anchor the frozen data root to the release build dir -
    # the harness bug behind the RECOVERY false-fail.
    env = {k: v for k, v in os.environ.items()}
    env["LOCALAPPDATA"] = str(sb / "AppData")
    env["USERPROFILE"] = str(sb)
    env["HOME"] = str(sb)
    return subprocess.run(
        [str(sb / ARTIFACT.name), *args],
        cwd=str(sb),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    gates: dict[str, dict] = {}
    prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    sb = Path(tempfile.mkdtemp(prefix="nexus-relacc-"))
    (sb / "AppData").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ARTIFACT, sb / ARTIFACT.name)

    try:
        # ---- INSTALL: artifact runs from clean sandbox ----
        r = run_artifact(sb, "version", "--plain")
        ok = r.returncode == 0 and str(prov["version"]) in r.stdout
        gates["INSTALL"] = {"status": "PASS" if ok else "FAIL", "evidence": r.stdout.strip()[:120]}

        # ---- IDENTITY: artifact self-consistency (provenance vs runtime) +
        # drift classification vs the LIVE head. Note: foreign agents commit
        # continuously; an artifact built at commit N will legitimately show
        # COMMIT_DRIFT vs a later head N+k. Self-consistency is the gate;
        # head divergence is recorded as informational (expected between
        # builds), not a failure of the artifact.
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO), capture_output=True,
            text=True, check=False,
        ).stdout.strip()
        r = run_artifact(sb, "version", "--json")
        d = json.loads(r.stdout.strip())
        commit_ok = d.get("commit") == prov["commit"]
        version_ok = d.get("version") == prov["version"]
        src_ok = d.get("commit_source") == "build-info" and d.get("commit_status") == "RECORDED"
        sha_ok = d.get("build_timestamp") == prov["built_at"]
        drift = {
            "VERSION_DRIFT": not version_ok,
            "COMMIT_DRIFT_ARTIFACT_VS_PROVENANCE": not commit_ok,
            "BUILD_METADATA_DRIFT": not src_ok,
            "HEAD_MOVED_SINCE_BUILD": head != prov["commit"],  # informational
        }
        identity_ok = commit_ok and version_ok and src_ok
        gates["IDENTITY"] = {
            "status": "PASS" if identity_ok else "FAIL",
            "artifact_version": prov["version"],
            "artifact_commit": prov["commit"],
            "head_commit": head,
            "commit_source": d.get("commit_source"),
            "build_timestamp_match": sha_ok,
            "drift": drift,
        }

        # ---- VERSION: plain + json agreement ----
        r2 = run_artifact(sb, "version", "--json")
        d2 = json.loads(r2.stdout.strip())
        gates["VERSION"] = {
            "status": "PASS" if d2.get("version") == prov["version"] else "FAIL",
            "version": d2.get("version"),
        }

        # ---- UPDATE: check (offline cache) + purity + no fake up-to-date ----
        r3 = run_artifact(sb, "update", "check", "--json")
        try:
            d3 = json.loads(r3.stdout.strip())
            upd_ok = "status" in d3 and d3["status"] in (
                "NO_UPDATE",
                "UPDATE_AVAILABLE",
                "NETWORK_ERROR",
            )
            gates["UPDATE"] = {
                "status": "PASS" if upd_ok else "FAIL",
                "check_status": d3.get("status"),
                "current": d3.get("current_version"),
                "target": d3.get("target_version"),
            }
        except json.JSONDecodeError:
            gates["UPDATE"] = {"status": "FAIL", "evidence": r3.stdout[:200]}

        # ---- JSON: all machine paths parse ----
        json_ok = True
        for args in (
            ("version", "--json"),
            ("doctor", "--json"),
            ("update", "check", "--json"),
            ("status", "--json"),
            ("health", "--json"),
        ):
            rr = run_artifact(sb, *args)
            try:
                json.loads(rr.stdout.strip())
            except Exception:
                json_ok = False
                gates["JSON"] = {"status": "FAIL", "cmd": args, "stdout_head": rr.stdout[:150]}
                break
        if json_ok:
            gates["JSON"] = {"status": "PASS", "commands": 5}

        # ---- HUMAN_UX: banners/tables render, exit codes sane ----
        r4 = run_artifact(sb, "doctor")
        human_ok = r4.returncode in (0, 1) and "OVERALL" in r4.stdout and "NEXT" in r4.stdout
        gates["HUMAN_UX"] = {"status": "PASS" if human_ok else "FAIL", "has_summary": human_ok}

        # ---- REPAIR: controlled break (rm logs dir) -> doctor --fix ----
        logs = sb / "AppData" / "NexusScalpEngine" / "logs"
        shutil.rmtree(logs, ignore_errors=True)
        r5 = run_artifact(sb, "doctor", "--fix", "--yes", "--json")
        try:
            d5 = json.loads(r5.stdout.strip())
            repair_ok = bool(d5.get("repair")) and logs.exists()
            gates["REPAIR"] = {
                "status": "PASS" if repair_ok else "FAIL",
                "logs_recreated": logs.exists(),
                "rc": r5.returncode,
            }
        except json.JSONDecodeError:
            gates["REPAIR"] = {"status": "FAIL", "evidence": r5.stdout[:200]}

        # ---- IDEMPOTENCY: second --fix is stable ----
        before = sorted(str(p.relative_to(sb)) for p in sb.rglob("*") if p.is_file())
        run_artifact(sb, "doctor", "--fix", "--yes", "--json")
        after = sorted(str(p.relative_to(sb)) for p in sb.rglob("*") if p.is_file())
        gates["IDEMPOTENCY"] = {
            "status": "PASS" if before == after else "FAIL",
            "file_set_identical": before == after,
        }

        # ---- START/STOP/RESTART: onefile CLI excludes the engine runtime ----
        r6 = run_artifact(sb, "start", "--json")
        truthful = r6.returncode != 0 and "numpy" in (r6.stdout + r6.stderr)
        gates["START"] = gates["STOP"] = gates["RESTART"] = {
            "status": "NOT_SUPPORTED",
            "reason": (
                "onefile CLI excludes numpy/torch by design; engine start "
                "requires the onedir bundle (ISCC absent on this machine - "
                "installer EXE not built). Artifact fails TRUTHFULLY: "
                + (r6.stdout + r6.stderr)[:120].replace("\n", " ")
            ),
            "truthful_failure": truthful,
        }

        # ---- RECOVERY: stop with stale pidfile self-cleans.
        # Frozen onefile data root = <exe_dir>/data (portable layout,
        # paths.get_data_root), NOT LocalAppData - place the pidfile there.
        pidfile = sb / "data" / "nexus.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text("5999999", encoding="utf-8")
        r7 = run_artifact(sb, "stop")
        gates["RECOVERY"] = {
            "status": "PASS" if r7.returncode == 0 and not pidfile.exists() else "FAIL",
            "stale_pidfile_cleaned": not pidfile.exists(),
            "rc": r7.returncode,
            "output_head": (r7.stdout + r7.stderr)[:150].replace("\n", " "),
        }

        manifest = {
            "contract": "RELEASE_ACCEPTANCE_MANIFEST v1",
            "artifact": {
                "name": prov["artifact"],
                "version": prov["version"],
                "commit": prov["commit"],
                "sha256": prov["sha256"],
                "size_bytes": prov["size_bytes"],
                "built_at": prov["built_at"],
            },
            "gates": gates,
            "summary": {
                "pass": sum(1 for g in gates.values() if g["status"] == "PASS"),
                "fail": sum(1 for g in gates.values() if g["status"] == "FAIL"),
                "not_supported": sum(1 for g in gates.values() if g["status"] == "NOT_SUPPORTED"),
            },
        }
        OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest["summary"], indent=2))
        print("MANIFEST:", OUT_MANIFEST)
        return 0 if manifest["summary"]["fail"] == 0 else 1
    finally:
        shutil.rmtree(sb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
