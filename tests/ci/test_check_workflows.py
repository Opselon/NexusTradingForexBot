"""Deterministic tests for the NSE CI workflow integrity analyzer.

These are the self-tests that make the CI self-defending:
  * correct input  -> no ERROR            (the repo passes today)
  * broken input    -> ERROR               (a regressed workflow fails the scan)

Each test builds a tiny synthetic workflow YAML in a temp dir and asserts the
scanner's verdict, so the analyzer's invariants are locked down independently
of the live .github/workflows tree.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import check_workflows as c


def _write(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(body, encoding="utf-8")
    return p


def test_clean_workflow_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "good.yml",
            """
name: good
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      x: ${{ steps.s.outcome }}
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - id: s
        run: echo hi
  consume:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo ${{ needs.build.outputs.x }}
""",
        )
        models, ec = c.run(d)
        assert ec == 0, f"expected clean, got {ec}"
        assert not any(f.severity == "ERROR" for m in models for f in m.findings)


def test_undefined_job_output_reference_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "bad.yml",
            """
name: bad
on: [push]
jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
        # detect never declares outputs.lane
  consume:
    needs: detect
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo ${{ needs.detect.outputs.lane }}
""",
        )
        models, ec = c.run(d)
        assert ec == 1, f"expected ERROR exit, got {ec}"
        found = any(
            f.severity == "ERROR" and f.check == "undefined-output"
            for m in models
            for f in m.findings
        )
        assert found, "undefined-output check did not fire"


def test_undefined_job_reference_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "bad2.yml",
            """
name: bad2
on: [push]
jobs:
  consume:
    needs: ghost
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo ${{ needs.ghost.outputs.x }}
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and "does not exist" in f.message
            for m in models
            for f in m.findings
        )


def test_local_action_without_checkout_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "local.yml",
            """
name: local
on: [push]
jobs:
  use-it:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/my-action
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and f.check == "local-action-no-checkout"
            for m in models
            for f in m.findings
        )


def test_local_action_with_checkout_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "localok.yml",
            """
name: localok
on: [push]
jobs:
  use-it:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - uses: ./.github/actions/my-action
""",
        )
        _models, ec = c.run(d)
        assert ec == 0


def test_matrix_artifact_without_dim_warns():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "matrix.yml",
            """
name: matrix
on: [push]
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - uses: actions/upload-artifact@0000000000000000000000000000000000000000
        with:
          name: results        # no matrix dim -> collision
          path: out
""",
        )
        models, ec = c.run(d)
        # Warning only -> exit 0 unless strict
        assert ec == 0
        assert any(f.check == "matrix-artifact-collision" for m in models for f in m.findings)
        _, ec_s = c.run(d, strict=True)
        assert ec_s == 1, "strict mode should fail on collision warning"


def test_matrix_artifact_with_dim_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "matrixok.yml",
            """
name: matrixok
on: [push]
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - uses: actions/upload-artifact@0000000000000000000000000000000000000000
        with:
          name: results-${{ matrix.os }}
          path: out
""",
        )
        models, ec = c.run(d)
        assert ec == 0
        assert not any(f.check == "matrix-artifact-collision" for m in models for f in m.findings)


def test_empty_if_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "emptyif.yml",
            """
name: emptyif
on: [push]
jobs:
  gate:
    if: ""
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo x
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        assert any(f.check == "empty-if" for m in models for f in m.findings)


def test_always_false_if_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "falseif.yml",
            """
name: falseif
on: [push]
jobs:
  gate:
    if: false
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo x
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        assert any(f.check == "always-false-if" for m in models for f in m.findings)


def test_yaml_syntax_error_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "broken.yml", "name: broken\non: [push\njobs: : :\n")
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and "parse" in f.message.lower()
            for m in models
            for f in m.findings
        )


def test_lane_if_undeclared_warns():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "lane.yml",
            """
name: lane
on: [push]
jobs:
  classify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
  gate:
    needs: classify
    if: needs.classify.outputs.python == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
        # classify declares no outputs.python
""",
        )
        models, _ec = c.run(d)
        # warning only
        assert any(f.check == "lane-if-undeclared" for m in models for f in m.findings)


def test_self_watch_poller_detected():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "poller.yml",
            """
name: poller
on: [pull_request]
jobs:
  watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: gh run watch ${{ github.run_id }} --exit-status
        env:
          GITHUB_RUN_ID: ${{ github.run_id }}
        # uses GITHUB_RUN_ID inside a polling command -> INFO
""",
        )
        models, _ = c.run(d)
        assert any(f.check == "self-watch" for m in models for f in m.findings)


def test_self_watch_metadata_not_flagged():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "meta.yml",
            """
name: meta
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    env:
      RUN_ID: ${{ github.run_id }}
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: echo "run is $RUN_ID"
        # run id only in metadata/env, no polling command -> no self-watch
""",
        )
        models, _ = c.run(d)
        assert not any(f.check == "self-watch" for m in models for f in m.findings)


# --------------------------------------------------------------------------- #
# Check 8 — action pin shape (the #139-wave phantom-pin regression lock)
# --------------------------------------------------------------------------- #
def test_tag_ref_pin_fails():
    """`@v4`/'# v4.4.0'-style tag refs are mutable claims, not pins -> ERROR."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "tagref.yml",
            """
name: tagref
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4  # v4.4.0
      - run: echo hi
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and f.check == "action-pin-shape"
            for m in models
            for f in m.findings
        )


def test_refless_and_short_sha_pins_fail():
    """No @ref at all, or a truncated (7-hex) sha, both fail the shape rule."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "nostart.yml",
            """
name: nostart
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout
  b:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@1234567
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        hits = [
            f
            for m in models
            for f in m.findings
            if f.severity == "ERROR" and f.check == "action-pin-shape"
        ]
        assert len(hits) == 2, f"expected 2 shape errors, got {hits}"


def test_fake_40hex_pin_passes_shape_rule():
    """A well-formed 40-hex SHA passes the offline shape rule (and the local
    composite-action exemption keeps `./.github/actions/*` out of scope)."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "pinned.yml",
            """
name: pinned
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000 # v4.4.0
      - uses: ./.github/actions/my-action
""",
        )
        models, ec = c.run(d)
        assert ec == 0
        assert not any(
            f.check in ("action-pin-shape", "action-pin-phantom")
            for m in models
            for f in m.findings
        )


def test_phantom_tag_object_pins_are_rejected():
    """The three #139-wave phantoms must NEVER ship again.

    They are all 40-hex (so a naive regex-only scan waves them through), but
    two are *annotated-tag objects* (not commits) and the third is a
    download-artifact sha pasted under upload-artifact. Verified 2026-09-12:
      git ls-remote https://github.com/astral-sh/setup-uv 'refs/tags/v7.6.0*' 'refs/tags/v5.4.2*' 'refs/tags/v7*' 'refs/tags/v5*'
      git ls-remote https://github.com/actions/upload-artifact 'refs/tags/v4*'
    -> 94527f2e... = refs/tags/v7 object (v7.6.0 commit is 37802adc...)
    -> e58605a9... = refs/tags/v5 object (v5.4.2 commit is d4b2f3b6...)
    -> d3f86a10... = absent from upload-artifact; it is download-artifact v4.3.0
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "phantom.yml",
            """
name: phantom
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7.6.0
  b:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@e58605a9b6da7c637471fab8847a5e5a6b8df081 # v5.4.2
  c:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
""",
        )
        models, ec = c.run(d)
        assert ec == 1
        phantoms = [f for m in models for f in m.findings if f.check == "action-pin-phantom"]
        assert len(phantoms) == 3, f"expected 3 phantom errors, got {phantoms}"
        # shape rule alone must NOT be the thing catching them — they are
        # valid hex; the ledger is the catching layer
        assert not any(f.check == "action-pin-shape" for m in models for f in m.findings)


def test_phantom_correction_shas_are_not_ledgered():
    """The corrected commits stay usable: not ledger-blocked (their upstream
    existence was verified manually per the docstring's audit contract)."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(
            d,
            "fixed.yml",
            """
name: fixed
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78 # v7.6.0
      - uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5.4.2
      - uses: actions/upload-artifact@26f96dfa697d77e81fd5907df203aa23a56210a8 # v4.3.0
""",
        )
        models, ec = c.run(d)
        assert ec == 0
        assert not any(f.check.startswith("action-pin") for m in models for f in m.findings)
