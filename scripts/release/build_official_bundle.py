#!/usr/bin/env python3
"""Publish an OFFICIAL Nexus model bundle (PATH A producer, BUG-293).

Operator tool: packages a TRAINED serving bundle (canonical 70D variant)
plus its dataset identity into the signed ``nexus_model_bundle_v1`` layout
the client verifies (`nexus model-official`). Signing uses the SAME
Ed25519 trust root as the update manifests — run in CI with
``NSE_UPDATE_SIGNING_KEY`` or locally with the operator escrow key.

    python scripts/release/build_official_bundle.py \
        --bundle-dir artifacts/models/scalp/XAUUSD/70d_liquidity \
        --dataset data/raw/XAUUSD_M1.parquet \
        --out dist/official-bundle \
        [--archive dist/model-bundle.zip] \
        --bundle-id official-xauusd-scalp_v3-$(date +%Y%m%d)

The tool REFUSES to publish anything that fails the client-side chain
(verify_bundle_manifest against the EMBEDDED trust root after signing +
re-download simulation via local re-verify): a bundle that its own clients
would reject can never leave this script. Hosting (Google Drive / any HTTPS
directory / GitHub release assets) is then a plain file upload by the
operator; set the base URL via NEXUS_OFFICIAL_MODEL_BASE_URL.

Exit codes: 0 published, 1 verification refused, 2 usage/material missing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.model_provisioning import official as off  # noqa: E402
from nexus_scalp.release import bootstrap as rb  # noqa: E402


def _sha(path: Path) -> str:
    return rb.sha256_file(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle-dir", required=True, type=Path,
                    help="serving bundle dir (model.pt + model.scaler.npz + model.meta.json)")
    ap.add_argument("--dataset", type=Path, default=None,
                    help="optional dataset file to ship beside the weights (provenance)")
    ap.add_argument("--out", required=True, type=Path, help="output bundle directory")
    ap.add_argument("--archive", type=Path, default=None, help="also emit a .zip for Drive hosting")
    ap.add_argument("--bundle-id", default="", help="official bundle id (default: auto)")
    ap.add_argument("--model-version", default="1.0.0")
    ap.add_argument("--key-id", default=None, help="signing key_id (default: ACTIVE_TRUST_ROOT)")
    ap.add_argument("--signing-key", default=None,
                    help="Ed25519 private seed hex (else $NSE_UPDATE_SIGNING_KEY)")
    args = ap.parse_args()

    src = args.bundle_dir
    missing = [n for n in ("model.pt", "model.scaler.npz", "model.meta.json") if not (src / n).exists()]
    if missing:
        print(f"usage error: bundle-dir missing {missing}", file=sys.stderr)
        return 2
    seed = args.signing_key or os.environ.get("NSE_UPDATE_SIGNING_KEY", "").strip()
    if not seed:
        print("usage error: no signing key (pass --signing-key or set NSE_UPDATE_SIGNING_KEY)", file=sys.stderr)
        return 2

    from nexus_scalp.release.signing.trusted_keys import ACTIVE_TRUST_ROOT

    key_id = args.key_id or ACTIVE_TRUST_ROOT

    meta = json.loads((src / "model.meta.json").read_text(encoding="utf-8"))
    dim = int(meta.get("feature_schema_dimension") or 0)
    classes = int(meta.get("model_head_classes") or meta.get("num_classes") or 0)
    if (dim, classes) != (70, 3):
        print(f"refused: bundle geometry {dim}D/{classes}c is not the canonical 70D/3-class", file=sys.stderr)
        return 1

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for name in ("model.pt", "model.scaler.npz", "model.meta.json"):
        shutil.copy2(src / name, out / name)
    files: dict[str, dict[str, object]] = {
        name: {"sha256": _sha(out / name), "size": (out / name).stat().st_size}
        for name in ("model.pt", "model.scaler.npz", "model.meta.json")
    }
    dataset_block: dict[str, object] = {"id": "", "version": "", "sha256": ""}
    if args.dataset is not None:
        if not args.dataset.exists():
            print(f"usage error: dataset file missing: {args.dataset}", file=sys.stderr)
            return 2
        shutil.copy2(args.dataset, out / args.dataset.name)
        files[args.dataset.name] = {"sha256": _sha(out / args.dataset.name), "size": args.dataset.stat().st_size}
        dataset_block = {
            "id": f"ds-{args.dataset.name}",
            "version": "1",
            "sha256": _sha(out / args.dataset.name),
        }

    import datetime

    bundle_id = args.bundle_id or (
        f"official-xauusd-scalp_v3-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d')}"
    )
    git_commit = ""
    try:
        git_commit = subprocess_commit()
    except Exception:
        pass
    manifest = {
        "schema": off.BUNDLE_MANIFEST_SCHEMA,
        "bundle_id": bundle_id,
        "model_version": args.model_version,
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "feature_schema_id": str(meta.get("feature_schema_id") or "scalp_v3"),
        "feature_schema_hash": str(meta.get("feature_schema_hash") or ""),
        "dimension": 70,
        "class_count": 3,
        "model_sha256": files["model.pt"]["sha256"],
        "metadata_sha256": files["model.meta.json"]["sha256"],
        "scaler_sha256": files["model.scaler.npz"]["sha256"],
        "dataset": dataset_block,
        "training": {
            "command": "scripts/release/build_official_bundle.py",
            "git_commit": git_commit,
            "published_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "provenance_note": "built from a governed serving bundle — see registry for "
            "walk-forward evidence (fold metrics ride in the trainer-side manifest)",
        },
        "files": files,
        "key_id": key_id,
        "signature": "",
    }
    import nacl.signing

    payload = off._canonical_payload(manifest)
    manifest["signature"] = nacl.signing.SigningKey(bytes.fromhex(seed)).sign(payload).signature.hex()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # CLIENT-SIDE SIMULATION: verify exactly what a client would verify,
    # using the EMBEDDED trust root (the private key cannot make our own
    # client trust a bundle it would reject).
    try:
        verified = off.verify_bundle_manifest(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
        for name, entry in verified["files"].items():
            if _sha(out / name) != str(entry["sha256"]).lower():
                raise off.OfficialBundleError("SHA256_MISMATCH", name)
    except off.OfficialBundleError as exc:
        print(f"REFUSED (client would reject): {exc}", file=sys.stderr)
        shutil.rmtree(out, ignore_errors=True)
        return 1

    if args.archive is not None:
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out.iterdir()):
                zf.write(f, f.name)
        print(f"archive: {args.archive} ({args.archive.stat().st_size:,} bytes)")

    print(f"official bundle published: {out}")
    print(f"bundle_id={bundle_id} model_sha256={manifest['model_sha256'][:16]}… key_id={key_id}")
    print("next: upload the directory (or the zip) to your host, then set")
    print("      NEXUS_OFFICIAL_MODEL_BASE_URL=<https base url or drive:<fileid>>")
    return 0


def subprocess_commit() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
