#!/usr/bin/env python3
"""Owner-only official-model publication; candidate data is never executable."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def fetch_candidate(api, tag: str, dest_dir) -> None:
    """Fetch only allowlisted candidate assets by fixed asset ID into a fresh dir."""
    import shutil

    record = api.release(tag)
    selected = candidate_assets(record, tag)
    dest_dir = Path(dest_dir)
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True)
    try:
        for name, asset in selected.items():
            out = dest_dir / name
            api.download(asset["id"], out, asset["size"])
            actual = out.stat().st_size
            if actual != asset["size"] or actual > CANDIDATE_FILES[name]:
                raise ValueError(f"downloaded {name} size {actual} != declared {asset['size']}")
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise


REPOSITORY = "Opselon/NexusTradingForexBot"
CANDIDATE_FILES = {
    "model.pt": 512 * 1024 * 1024,
    "model.scaler.npz": 1024 * 1024,
    "model.meta.json": 256 * 1024,
    "trainer-manifest.json": 256 * 1024,
    "provenance.json": 256 * 1024,
    "compatibility.json": 256 * 1024,
}


def candidate_assets(record: dict, tag: str) -> dict:
    if record.get("draft") is not True or record.get("tag_name") != tag:
        raise ValueError("candidate must be the exact authorized draft release")
    assets = record.get("assets", [])
    selected = {}
    for asset in assets:
        name = asset.get("name")
        size = asset.get("size")
        if (
            name not in CANDIDATE_FILES
            or name in selected
            or type(size) is not int
            or not 0 < size <= CANDIDATE_FILES[name]
            or type(asset.get("id")) is not int
            or asset["id"] <= 0
            or asset.get("state") != "uploaded"
        ):
            raise ValueError("unexpected, duplicate, oversized or incomplete candidate asset")
        selected[name] = asset
    if set(selected) != set(CANDIDATE_FILES):
        raise ValueError("candidate must contain exactly the six documented assets")
    return selected


def runtime_spec(compatibility: dict) -> tuple[str, str]:
    runtime = compatibility.get("verification_runtime", {})
    python = runtime.get("python", "")
    torch = runtime.get("pytorch", "")
    if python != "3.11" or not re.fullmatch(r"2\.(?:[6-9]|1[0-9])\.[0-9]{1,2}", torch):
        raise ValueError(
            "verification runtime requires Python 3.11 and stable PyTorch 2.6-2.19 CPU"
        )
    return python, torch


def publish_bundle(api, bundle_dir, version: str) -> None:
    """Versioned release first, verified assets, then channel manifest LAST."""
    import json

    from nexus_scalp.model_provisioning import official as off

    release_tag = f"model-{version}"
    if api.release(release_tag) is not None:
        raise ValueError(f"release {release_tag} already exists; immutable identity violated")
    api.create(release_tag, draft=True)
    assets = sorted(
        p for p in Path(bundle_dir).iterdir() if p.is_file() and p.name != "manifest.json"
    )
    api.upload(release_tag, assets)
    api.verify_assets(release_tag, assets)
    manifest = json.loads((Path(bundle_dir) / "manifest.json").read_text())
    off.verify_bundle_manifest(manifest)
    api.upload(release_tag, [Path(bundle_dir) / "manifest.json"], clobber=False)
    api.verify_assets(release_tag, [Path(bundle_dir) / "manifest.json"])
    api.publish(release_tag)
    # The stable channel IS the signed manifest itself: clients read
    # .../official-model-stable/manifest.json and every artifact URL inside
    # it points at the immutable model-<version> release.
    api.upload("official-model-stable", [Path(bundle_dir) / "manifest.json"], clobber=True)


def validate_request(version: str, candidate: str, ref: str, repo: str) -> str:
    if ref != "refs/heads/main" or repo != REPOSITORY:
        raise ValueError("publication requires the canonical repository main branch")
    if not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})", version
    ):
        raise ValueError("version must be X.Y.Z (no v prefix)")
    if candidate != f"model-candidate-{version}":
        raise ValueError("candidate tag must equal model-candidate-<version>")
    return f"model-{version}"


def main(argv: list[str] | None = None) -> int:
    """Owner publication CLI: validate / fetch / publish; all fail closed."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    for name in ("version", "candidate", "ref", "repo"):
        validate.add_argument(f"--{name}", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--candidate", required=True)
    fetch.add_argument("--dest", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--bundle", required=True)
    publish.add_argument("--version", required=True)
    rs = commands.add_parser("runtime-spec")
    rs.add_argument("--compatibility", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            print(validate_request(args.version, args.candidate, args.ref, args.repo))
            return 0
        if args.command == "fetch":
            api = GhApi()
            fetch_candidate(api, args.candidate, args.dest)
            print(f"fetched {len(CANDIDATE_FILES)} authorized assets into {args.dest}")
            return 0
        if args.command == "runtime-spec":
            compatibility = json.loads(Path(args.compatibility).read_text(encoding="utf-8"))
            python, torch = runtime_spec(compatibility)
            print(f"{python},{torch}")
            return 0
        if args.command == "publish":
            api = GhApi()
            publish_bundle(api, args.bundle, args.version)
            print(f"published model-{args.version}; official-model-stable channel updated")
            return 0
    except Exception as exc:  # fail-closed: any refusal exits nonzero
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2  # argparse guarantees a subcommand; defensive only


class GhApi:
    """Minimal gh-backed release API for the publication workflow."""

    def __init__(self, repo: str = REPOSITORY) -> None:
        self.repo = repo

    def _gh(self, *args: str, **kw):
        result = subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            check=False,
            **kw,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
        return result.stdout

    def release(self, tag: str) -> dict | None:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--repo", self.repo, "--json", "assets,draft,tagName"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        record = json.loads(result.stdout)
        return {
            "draft": record.get("draft") is True,
            "tag_name": record.get("tagName", ""),
            "assets": [
                {
                    "id": asset["id"],
                    "name": asset["name"],
                    "size": asset["size"],
                    "state": asset.get("state", "uploaded"),
                }
                for asset in record.get("assets", [])
            ],
        }

    def create(self, tag: str, draft: bool) -> None:
        self._gh(f"repos/{self.repo}/releases", "-f", f"tag_name={tag}", "-F", f"draft={draft}")

    def upload(self, tag: str, paths, clobber: bool = False) -> None:
        for path in paths:
            cmd = ["gh", "release", "upload", tag, str(path), "--repo", self.repo]
            if clobber:
                cmd.append("--clobber")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"upload {Path(path).name}: {result.stderr.strip()[:300]}")

    def download(self, asset_id: int, dest, max_bytes: int) -> None:
        dest = Path(dest)
        record = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{self.repo}/releases/assets/{asset_id}",
                "-H",
                "Accept: application/octet-stream",
            ],
            stdout=open(dest, "wb"),
            check=False,
        )
        if record.returncode != 0:
            raise RuntimeError(f"asset {asset_id} download failed")
        if dest.stat().st_size > max_bytes:
            raise ValueError(f"asset {asset_id} exceeds declared bound {max_bytes}")

    def verify_assets(self, tag: str, paths) -> None:
        record = self.release(tag)
        if record is None:
            raise ValueError(f"release {tag} missing for verification")
        remote = {a["name"]: a for a in record["assets"]}
        for local_path in paths:
            path = Path(local_path)
            asset = remote.get(path.name)
            if asset is None:
                raise ValueError(f"{path.name}: missing on remote {tag}")
            if asset["size"] != path.stat().st_size:
                raise ValueError(f"{path.name}: remote size differs from local")

    def publish(self, tag: str) -> None:
        result = subprocess.run(
            ["gh", "release", "edit", tag, "--draft=false", "--repo", self.repo],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"publish {tag}: {result.stderr.strip()[:300]}")


if __name__ == "__main__":
    raise SystemExit(main())
