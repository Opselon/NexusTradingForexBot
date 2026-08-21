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

import check_workflows as c  # noqa: E402


def _write(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(body, encoding="utf-8")
    return p


def test_clean_workflow_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "good.yml", """
name: good
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      x: ${{ steps.s.outcome }}
    steps:
      - uses: actions/checkout@v4
      - id: s
        run: echo hi
  consume:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ needs.build.outputs.x }}
""")
        models, ec = c.run(d)
        assert ec == 0, f"expected clean, got {ec}"
        assert not any(f.severity == "ERROR" for m in models for f in m.findings)


def test_undefined_job_output_reference_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "bad.yml", """
name: bad
on: [push]
jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        # detect never declares outputs.lane
  consume:
    needs: detect
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ needs.detect.outputs.lane }}
""")
        models, ec = c.run(d)
        assert ec == 1, f"expected ERROR exit, got {ec}"
        found = any(
            f.severity == "ERROR" and f.check == "undefined-output"
            for m in models for f in m.findings
        )
        assert found, "undefined-output check did not fire"


def test_undefined_job_reference_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "bad2.yml", """
name: bad2
on: [push]
jobs:
  consume:
    needs: ghost
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ needs.ghost.outputs.x }}
""")
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and "does not exist" in f.message
            for m in models for f in m.findings
        )


def test_local_action_without_checkout_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "local.yml", """
name: local
on: [push]
jobs:
  use-it:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/my-action
""")
        models, ec = c.run(d)
        assert ec == 1
        assert any(
            f.severity == "ERROR" and f.check == "local-action-no-checkout"
            for m in models for f in m.findings
        )


def test_local_action_with_checkout_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "localok.yml", """
name: localok
on: [push]
jobs:
  use-it:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/my-action
""")
        models, ec = c.run(d)
        assert ec == 0


def test_matrix_artifact_without_dim_warns():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "matrix.yml", """
name: matrix
on: [push]
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-artifact@v4
        with:
          name: results        # no matrix dim -> collision
          path: out
""")
        models, ec = c.run(d)
        # Warning only -> exit 0 unless strict
        assert ec == 0
        assert any(f.check == "matrix-artifact-collision" for m in models for f in m.findings)
        _, ec_s = c.run(d, strict=True)
        assert ec_s == 1, "strict mode should fail on collision warning"


def test_matrix_artifact_with_dim_passes():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "matrixok.yml", """
name: matrixok
on: [push]
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-artifact@v4
        with:
          name: results-${{ matrix.os }}
          path: out
""")
        models, ec = c.run(d)
        assert ec == 0
        assert not any(f.check == "matrix-artifact-collision" for m in models for f in m.findings)


def test_empty_if_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "emptyif.yml", """
name: emptyif
on: [push]
jobs:
  gate:
    if: ""
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo x
""")
        models, ec = c.run(d)
        assert ec == 1
        assert any(f.check == "empty-if" for m in models for f in m.findings)


def test_always_false_if_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "falseif.yml", """
name: falseif
on: [push]
jobs:
  gate:
    if: false
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo x
""")
        models, ec = c.run(d)
        assert ec == 1
        assert any(f.check == "always-false-if" for m in models for f in m.findings)


def test_yaml_syntax_error_fails():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "broken.yml", "name: broken\non: [push\njobs: : :\n")
        models, ec = c.run(d)
        assert ec == 1
        assert any(f.severity == "ERROR" and "parse" in f.message.lower() for m in models for f in m.findings)


def test_lane_if_undeclared_warns():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "lane.yml", """
name: lane
on: [push]
jobs:
  classify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
  gate:
    needs: classify
    if: needs.classify.outputs.python == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        # classify declares no outputs.python
""")
        models, ec = c.run(d)
        # warning only
        assert any(f.check == "lane-if-undeclared" for m in models for f in m.findings)


def test_self_watch_poller_detected():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "poller.yml", """
name: poller
on: [pull_request]
jobs:
  watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: gh run watch ${{ github.run_id }} --exit-status
        env:
          GITHUB_RUN_ID: ${{ github.run_id }}
        # uses GITHUB_RUN_ID inside a polling command -> INFO
""")
        models, _ = c.run(d)
        assert any(f.check == "self-watch" for m in models for f in m.findings)


def test_self_watch_metadata_not_flagged():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d, "meta.yml", """
name: meta
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    env:
      RUN_ID: ${{ github.run_id }}
    steps:
      - uses: actions/checkout@v4
      - run: echo "run is $RUN_ID"
        # run id only in metadata/env, no polling command -> no self-watch
""")
        models, _ = c.run(d)
        assert not any(f.check == "self-watch" for m in models for f in m.findings)
