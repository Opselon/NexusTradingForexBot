"""TRAINING ENVIRONMENT CONTRACT (BUG-301) — resolve / provision / validate.

Architecture per the operator directive (2026-09-16):

  * DISCOVERY is separate from PROVISIONING. ``status()`` performs ZERO
    mutation (no pip, no venv writes beyond reading) and answers the wizard:
    python / environment / pytorch / gpu / training_ready + per-check pass/fail
    + the exact missing set. Only ``install()`` changes the machine, and
    only after explicit user action.
  * Training is GATED: the pipeline refuses to start unless the resolved
    environment reached READY (typed ``TRAINING_ENV_BLOCKED`` with the full
    checklist otherwise). PyTorch is never installed at application startup
    — PATH A (official download) and ordinary PAPER inference do not need
    the training stack at all.
  * Versions come from ONE canonical pinned source:
    ``configs/training_environment.json`` (schema nexus_training_env_v1,
    shipped with the package; the same file is mirrored into the packaged
    tree). CPU and CUDA are two REAL paths: CUDA requires a detected NVIDIA
    GPU + driver capability, resolves the exact +cu wheel index, and must
    pass an actual GPU allocation smoke test — ``is_available()`` alone is
    NOT READY. CPU must pass a real tensor-operation smoke test.
  * Every failure is TYPED and actionable (``EnvError`` codes below) — raw
    exception strings never surface as the user-facing reason, and nothing
    is hidden behind a generic ValueError/VALIDATION_FAILED.

Stage ladder (READY requires every applicable stage green):
    PYTHON -> VENV -> PIP -> BACKEND -> TORCH -> VERSION -> SMOKE -> READY
    (INSTALL sits between TORCH and VERSION: explicit opt-in step)

Subprocess commands target a resolved interpreter; a source/dev checkout can
run training there directly (PYTHONPATH=src), while the packaged EXE carries
its own bundled CPU torch — mismatches surface as TRAINING_ENV_MISMATCH
with the exact remedy instead of silent degradation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_provisioning.training_env")


def _contract_candidates() -> list[Path]:
    """Source checkout root + packaged locations (mirrored by bootstrap)."""
    from nexus_scalp.release.paths import exe_dir, get_runtime_workspace, is_frozen

    name = "training_environment.json"
    cands = [Path(__file__).resolve().parents[3] / "configs" / name]
    if is_frozen():
        root = get_runtime_workspace()
        for base in (root, exe_dir(), root.parent):
            cands.append(Path(base) / "configs" / name)
            cands.append(Path(base) / "_internal" / "configs" / name)
    return cands


def canonical_contract_path() -> Path:
    for c in _contract_candidates():
        if c.exists():
            return c
    return _contract_candidates()[0]


BACKEND_ENV = "NEXUS_TRAINING_BACKEND"  # cpu | cuda (user's explicit choice)
TRAINING_PYTHON_ENV = "NEXUS_TRAINING_PYTHON"
TRAINING_ENV_DIR_ENV = "NEXUS_TRAINING_ENV"


class EnvStage(StrEnum):
    PYTHON = "python"
    VENV = "environment"
    PIP = "pip"
    BACKEND = "backend"
    TORCH = "pytorch"
    VERSION = "torch_version"
    SMOKE = "smoke"
    READY = "ready"


class EnvCode(StrEnum):
    OK = "OK"
    PYTHON_NOT_FOUND = "PYTHON_NOT_FOUND"
    PYTHON_VERSION_UNSUPPORTED = "PYTHON_VERSION_UNSUPPORTED"
    VENV_NOT_FOUND = "VENV_NOT_FOUND"
    VENV_CREATE_FAILED = "VENV_CREATE_FAILED"
    PIP_UNAVAILABLE = "PIP_UNAVAILABLE"
    CONTRACT_MISSING = "CONTRACT_MISSING"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    BACKEND_UNSUPPORTED = "BACKEND_UNSUPPORTED"
    DRIVER_TOO_OLD = "DRIVER_TOO_OLD"
    TORCH_NOT_INSTALLED = "TORCH_NOT_INSTALLED"
    TORCH_IMPORT_FAILED = "TORCH_IMPORT_FAILED"
    TORCH_VERSION_MISMATCH = "TORCH_VERSION_MISMATCH"
    TORCH_LOCAL_TAG_MISMATCH = "TORCH_LOCAL_TAG_MISMATCH"
    SMOKE_TENSOR_FAILED = "SMOKE_TENSOR_FAILED"
    SMOKE_CUDA_UNAVAILABLE = "SMOKE_CUDA_UNAVAILABLE"
    SMOKE_CUDA_ALLOC_FAILED = "SMOKE_CUDA_ALLOC_FAILED"
    NETWORK_UNREACHABLE = "NETWORK_UNREACHABLE"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    PACKAGE_VERSION_UNSATISFIED = "PACKAGE_VERSION_UNSATISFIED"
    INSTALL_PERMISSION_DENIED = "INSTALL_PERMISSION_DENIED"
    INSTALL_FAILED = "INSTALL_FAILED"
    USER_CANCELLED = "USER_CANCELLED"
    TRAINING_ENV_MISMATCH = "TRAINING_ENV_MISMATCH"


#: Actionable remedy per code (UI renders this verbatim next to the check).
REMEDIES: dict[str, str] = {
    EnvCode.PYTHON_NOT_FOUND: "Install Python 3.11+ x64 from python.org (check 'Add python.exe to PATH'), relaunch, press Re-check.",
    EnvCode.PYTHON_VERSION_UNSUPPORTED: "Training requires the supported Python range in the canonical contract — install it or point NEXUS_TRAINING_PYTHON at a supported interpreter.",
    EnvCode.VENV_NOT_FOUND: "No training environment yet — press Install to create (or reuse) one.",
    EnvCode.VENV_CREATE_FAILED: "The managed training environment could not be created. Check disk space/permissions, or point NEXUS_TRAINING_ENV at an existing venv.",
    EnvCode.PIP_UNAVAILABLE: "Python found, pip unavailable. Repair it: <python> -m ensurepip --upgrade, or recreate the environment.",
    EnvCode.BACKEND_UNSUPPORTED: "No NVIDIA GPU detected by nvidia-smi. Choose the CPU backend, or connect the GPU and re-check.",
    EnvCode.DRIVER_TOO_OLD: "The NVIDIA driver does not expose the CUDA capability the pinned wheel needs. Update the driver, or choose CPU.",
    EnvCode.TORCH_NOT_INSTALLED: "PyTorch is missing in the resolved environment. Press 'Install required dependencies'.",
    EnvCode.TORCH_IMPORT_FAILED: "PyTorch is installed but fails to import — reinstall it, or recreate the environment.",
    EnvCode.TORCH_VERSION_MISMATCH: "PyTorch version does not match the canonical pin. Press 'Install required dependencies' to align it.",
    EnvCode.TORCH_LOCAL_TAG_MISMATCH: "PyTorch build variant mismatch (e.g. +cpu installed, +cu12x required). Press Install for the chosen backend.",
    EnvCode.SMOKE_TENSOR_FAILED: "CPU tensor smoke test failed — the installation is not usable. Reinstall or recreate the environment.",
    EnvCode.SMOKE_CUDA_UNAVAILABLE: "torch reports CUDA unavailable after install — driver/CUDA mismatch. Re-check the driver or fall back to CPU.",
    EnvCode.SMOKE_CUDA_ALLOC_FAILED: "A real CUDA allocation/matmul failed — the GPU path is not functional. Check VRAM/driver, or fall back to CPU.",
    EnvCode.NETWORK_UNREACHABLE: "Package download failed: network unavailable. Training has NOT started. Retry once connectivity is restored.",
    EnvCode.INDEX_UNAVAILABLE: "The PyTorch wheel index could not be reached (blocked by proxy/firewall?). Training has NOT started.",
    EnvCode.PACKAGE_VERSION_UNSATISFIED: "The pinned version is not installable for this Python/platform — see the canonical contract variants.",
    EnvCode.INSTALL_PERMISSION_DENIED: "Installation needs write access to the environment (or run --user). Training has NOT started.",
    EnvCode.INSTALL_FAILED: "pip reported a failure the taxonomy does not classify — the exact pip message is in the local log.",
    EnvCode.TRAINING_ENV_MISMATCH: "The prepared environment is not the one this process runs. Use the printed command under that interpreter, or choose a backend the current environment supports.",
}


@dataclass
class EnvCheck:
    stage: str
    ok: bool
    code: str = EnvCode.OK.value
    detail: str = ""
    remedy: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail[:200],
            "remedy": self.remedy,
        }


@dataclass
class EnvironmentReport:
    """The wizard-facing truth (user-schema: python/environment/pytorch/gpu)."""

    backend: str = "cpu"
    python: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    pytorch: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)
    contract: dict[str, Any] = field(default_factory=dict)
    checks: list[EnvCheck] = field(default_factory=list)
    training_ready: bool = False
    #: READY *and* the current process itself satisfies it (training can run
    #: in-process). False with training_ready=True => run under `training_command`.
    in_process_ready: bool = False
    training_command: str = ""
    install_attempted: bool = False
    install_result: str = ""

    def failing(self) -> list[EnvCheck]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "python": self.python,
            "environment": self.environment,
            "pytorch": self.pytorch,
            "gpu": self.gpu,
            "contract": self.contract,
            "checks": [c.as_dict() for c in self.checks],
            "training_ready": self.training_ready,
            "in_process_ready": self.in_process_ready,
            "training_command": self.training_command,
            "install_attempted": self.install_attempted,
            "install_result": self.install_result,
        }


class TrainingEnvironmentError(RuntimeError):
    """Typed, actionable provisioning failure (never a bare exception)."""

    def __init__(self, code: EnvCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail[:300]
        self.remedy = REMEDIES.get(code.value, "")


def load_contract(path: Path | None = None) -> dict[str, Any]:
    """Read + sanity-check the canonical pinned contract (single version source)."""
    p = Path(path or os.environ.get("NEXUS_TRAINING_CONTRACT", "") or canonical_contract_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrainingEnvironmentError(EnvCode.CONTRACT_MISSING, str(p)) from exc
    except (OSError, ValueError) as exc:
        raise TrainingEnvironmentError(EnvCode.CONTRACT_INVALID, f"{p}: {exc}") from exc
    for key in ("schema", "python", "variants"):
        if key not in raw:
            raise TrainingEnvironmentError(EnvCode.CONTRACT_INVALID, f"{p}: missing '{key}'")
    if raw.get("schema") != "nexus_training_env_v1":
        raise TrainingEnvironmentError(EnvCode.CONTRACT_INVALID, f"schema {raw.get('schema')!r}")
    return raw


def detect_nvidia_gpu() -> dict[str, Any]:
    """Real NVIDIA probe via nvidia-smi (never a guess; absent => available=False)."""
    out: dict[str, Any] = {"vendor": None, "name": None, "available": False}
    exe = shutil.which("nvidia-smi")
    if not exe:
        return out
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    if proc.returncode != 0 or not proc.stdout.strip():
        return out
    first = proc.stdout.strip().splitlines()[0]
    parts = [x.strip() for x in first.split(",")]
    out["vendor"] = "NVIDIA"
    if parts:
        out["name"] = parts[0]
    if len(parts) > 1:
        out["driver_version"] = parts[1]
    if len(parts) > 2:
        out["compute_cap"] = parts[2]
    out["available"] = True
    return out


def current_torch_facts() -> dict[str, Any]:
    """Torch facts for THIS process (import test included; never trusts metadata)."""
    facts: dict[str, Any] = {"installed": False, "version": None, "local_tag": None}
    try:
        import torch

        facts["installed"] = True
        ver = str(torch.__version__)
        facts["version"] = ver.split("+", 1)[0]
        facts["local_tag"] = ver.split("+", 1)[1] if "+" in ver else "none"
        facts["version_full"] = ver
    except Exception:
        pass
    return facts


def current_process_facts() -> dict[str, Any]:
    """pip + torch facts for THIS process (import-level truth)."""
    import importlib.util

    tf = current_torch_facts()
    return {
        "version": f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}",
        "minor": sys.version_info[1],
        "pip": importlib.util.find_spec("pip") is not None,
        "torch": tf.get("version_full"),
    }


def is_interpreter_process() -> bool:
    """A frozen EXE is NOT a script interpreter — never probe it as one."""
    from nexus_scalp.release.paths import is_frozen

    return not is_frozen()


def probe_python_interpreter(python_exe: str) -> dict[str, Any]:
    """Version + pip + torch facts of ANOTHER interpreter (subprocess probe).

    The CURRENT interpreter short-circuits to in-process facts (a packaged
    frozen exe cannot run ``-c`` scripts, and spawning sys.executable would
    re-launch the engine itself)."""
    info: dict[str, Any] = {
        "path": python_exe,
        "found": False,
        "version": None,
        "minor": None,
        "pip": False,
        "torch": None,
    }
    if python_exe and Path(python_exe) == Path(sys.executable):
        if not is_interpreter_process():
            return info
        info.update(found=True, **current_process_facts())
        return info
    if not python_exe or not Path(python_exe).exists():
        return info
    try:
        proc = subprocess.run(
            [
                python_exe,
                "-c",
                "import sys,json;"
                "d={'version': '%d.%d.%d' % sys.version_info[:3], 'minor': sys.version_info[1]};"
                "d['pip']=__import__('importlib.util').find_spec('pip') is not None;"
                "t=None\n"
                "try:\n"
                "    import torch as _t\n"
                "    t=str(_t.__version__)\n"
                "except Exception:\n"
                "    pass\nd['torch']=t\n"
                "print(json.dumps(d))",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            info.update(
                found=True,
                version=data.get("version"),
                minor=data.get("minor"),
                pip=bool(data.get("pip")),
                torch=data.get("torch"),
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return info


class TrainingEnvironmentManager:
    """Resolves, validates and (on explicit request) provisions the training env."""

    def __init__(
        self, contract: dict[str, Any] | None = None, workspace: Path | None = None
    ) -> None:
        self._contract = contract
        self.workspace = Path(workspace or Path.cwd())

    @property
    def contract(self) -> dict[str, Any]:
        if self._contract is None:
            self._contract = load_contract()
        return self._contract

    # -- resolution ---------------------------------------------------------
    def chosen_backend(self) -> str:
        backend = str(os.environ.get(BACKEND_ENV, "auto")).strip().lower()
        if backend in ("cpu", "cuda"):
            return backend
        gpu = detect_nvidia_gpu()
        return "cuda" if gpu["available"] else "cpu"

    def _python_pin(self) -> tuple[int, int]:
        py = self.contract.get("python", {})
        return int(py.get("min_minor", 11)), int(py.get("max_minor", 13))

    def resolve_python(self) -> tuple[str | None, EnvCheck]:
        """Explicit override > current interpreter (if supported) > py launcher probe."""
        lo, hi = self._python_pin()
        candidates: list[str] = []
        override = str(os.environ.get(TRAINING_PYTHON_ENV, "")).strip()
        if override:
            candidates.append(override)
        if is_interpreter_process():
            candidates.append(sys.executable)
        candidates.append("python")
        candidates.append("python3")
        for exe in candidates:
            resolved = shutil.which(exe) or (exe if Path(exe).exists() else None)
            if not resolved:
                continue
            info = probe_python_interpreter(resolved)
            if not info["found"] or info["minor"] is None:
                continue
            minor = int(info["minor"])
            if lo <= minor <= hi:
                return resolved, EnvCheck(
                    EnvStage.PYTHON.value, True, detail=f"{resolved} (3.{minor})"
                )
            return resolved, EnvCheck(
                EnvStage.PYTHON.value,
                False,
                EnvCode.PYTHON_VERSION_UNSUPPORTED.value,
                f"{resolved} is 3.{minor}; supported 3.{lo}-3.{hi}",
                REMEDIES[EnvCode.PYTHON_VERSION_UNSUPPORTED.value],
            )
        return None, EnvCheck(
            EnvStage.PYTHON.value,
            False,
            EnvCode.PYTHON_NOT_FOUND.value,
            "no supported interpreter found",
            str(
                self.contract.get("python", {}).get(
                    "guidance", REMEDIES[EnvCode.PYTHON_NOT_FOUND.value]
                )
            ),
        )

    def resolve_env(self, python_exe: str | None) -> tuple[str | None, dict[str, Any]]:
        """DISCOVERY-only (never creates): managed venv > NEXUS_TRAINING_ENV >
        the current process's active venv > not-found.

        A dev/source checkout training inside its own .venv REUSES it (the
        directive's "existing environment wins"); a packaged app finds no
        environment and reports VENV_NOT_FOUND until the user installs."""
        managed_py = self.venv_python()
        if managed_py:
            return managed_py, {
                "found": True,
                "path": str(self.managed_venv_path()),
                "type": "managed-venv",
            }
        override = str(os.environ.get(TRAINING_ENV_DIR_ENV, "")).strip()
        if override:
            p = Path(override)
            exepath = p / "Scripts" / "python.exe" if os.name == "nt" else p / "bin" / "python"
            if exepath.exists():
                return str(exepath), {"found": True, "path": str(p), "type": "external-venv"}
        in_venv = (
            getattr(sys, "prefix", "") != getattr(sys, "base_prefix", "")
            or getattr(sys, "base_executable", "") != sys.executable
        )
        if (
            python_exe
            and Path(python_exe) == Path(sys.executable)
            and in_venv
            and is_interpreter_process()
        ):
            return python_exe, {"found": True, "path": sys.prefix, "type": "active-venv"}
        return None, {"found": False, "path": None, "type": None}

    def managed_venv_path(self) -> Path:
        name = str(self.contract.get("venv", {}).get("dir_name", "training-env"))
        return self.workspace / name

    def create_managed_venv(self, base_python: str) -> EnvCheck:
        target = self.managed_venv_path()
        if (target / "Scripts" / "python.exe").exists() or (target / "bin" / "python").exists():
            return EnvCheck(EnvStage.VENV.value, True, detail=f"reuse {target}")
        try:
            proc = subprocess.run(
                [base_python, "-m", "venv", str(target)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return EnvCheck(
                EnvStage.VENV.value,
                False,
                EnvCode.VENV_CREATE_FAILED.value,
                str(exc)[:200],
                REMEDIES[EnvCode.VENV_CREATE_FAILED.value],
            )
        if proc.returncode != 0:
            return EnvCheck(
                EnvStage.VENV.value,
                False,
                EnvCode.VENV_CREATE_FAILED.value,
                (proc.stderr or proc.stdout)[:200],
                REMEDIES[EnvCode.VENV_CREATE_FAILED.value],
            )
        return EnvCheck(EnvStage.VENV.value, True, detail=f"created {target}")

    def venv_python(self) -> str | None:
        target = self.managed_venv_path()
        for cand in (target / "Scripts" / "python.exe", target / "bin" / "python"):
            if cand.exists():
                return str(cand)
        return None

    # -- discovery (NEVER mutates) ------------------------------------------
    def status(self, backend: str | None = None) -> EnvironmentReport:
        contract = self.contract
        backend = (backend or self.chosen_backend()).lower()
        rep = EnvironmentReport(backend=backend)
        rep.contract = {
            "path": str(canonical_contract_path()),
            "variants": {
                k: dict(v.get("packages", {})) for k, v in contract.get("variants", {}).items()
            },
        }
        rep.gpu = detect_nvidia_gpu()

        python_exe, python_check = self.resolve_python()
        rep.checks.append(python_check)
        rep.python = {
            "found": python_check.ok,
            "version": python_check.detail.rsplit(" (", 1)[-1].rstrip(")")
            if python_check.ok
            else None,
            "path": python_exe,
        }
        if not python_check.ok:
            rep.environment = {"found": False, "path": None, "type": None}
            rep.pytorch = {"installed": False, "compatible": False}
            return rep

        env_exe, env_block = self.resolve_env(python_exe)
        if env_exe is None or not env_block["found"]:
            # DISCOVERY reports; only install() creates (explicit user action).
            rep.checks.append(
                EnvCheck(
                    EnvStage.VENV.value,
                    False,
                    EnvCode.VENV_NOT_FOUND.value,
                    "no training environment yet",
                    REMEDIES[EnvCode.VENV_NOT_FOUND.value],
                )
            )
            rep.environment = {"found": False, "path": None, "type": None}
            rep.pytorch = {"installed": False, "compatible": False}
            return rep
        rep.checks.append(EnvCheck(EnvStage.VENV.value, True, detail=str(env_block.get("path"))))
        rep.environment = env_block

        facts = probe_python_interpreter(env_exe or "")
        rep.checks.append(
            EnvCheck(
                EnvStage.PIP.value,
                bool(facts["pip"]),
                "" if facts["pip"] else EnvCode.PIP_UNAVAILABLE.value,
                "pip present" if facts["pip"] else "pip not importable in the resolved interpreter",
                "" if facts["pip"] else REMEDIES[EnvCode.PIP_UNAVAILABLE.value],
            )
        )
        if backend == "cuda":
            gpu_ok = bool(rep.gpu.get("available"))
            min_ver = str(
                contract.get("variants", {}).get("cuda", {}).get("min_driver_cuda_version", "")
            )
            rep.checks.append(
                EnvCheck(
                    EnvStage.BACKEND.value,
                    gpu_ok,
                    "" if gpu_ok else EnvCode.BACKEND_UNSUPPORTED.value,
                    f"NVIDIA GPU {rep.gpu.get('name')} driver {rep.gpu.get('driver_version')} (needs CUDA {min_ver})"
                    if gpu_ok
                    else "nvidia-smi found no GPU",
                    "" if gpu_ok else REMEDIES[EnvCode.BACKEND_UNSUPPORTED.value],
                )
            )
        else:
            rep.checks.append(EnvCheck(EnvStage.BACKEND.value, True, detail="CPU backend"))

        required = str(
            contract.get("variants", {}).get(backend, {}).get("packages", {}).get("torch", "")
        )
        installed = facts.get("torch")
        rep.pytorch = {
            "installed": bool(installed),
            "path": env_exe,
            "detected": installed,
            "required": required,
            "compatible": False,
            "version": None,
        }
        if not installed:
            rep.checks.append(
                EnvCheck(
                    EnvStage.TORCH.value,
                    False,
                    EnvCode.TORCH_NOT_INSTALLED.value,
                    f"required torch{required}",
                    REMEDIES[EnvCode.TORCH_NOT_INSTALLED.value],
                )
            )
            return rep
        req_base = required.lstrip("=").strip()
        got_full = str(installed)
        got_base = got_full.split("+", 1)[0]
        got_tag = got_full.split("+", 1)[1] if "+" in got_full else "cpu"
        if backend == "cuda" and got_tag.startswith("cpu"):
            rep.checks.append(
                EnvCheck(EnvStage.TORCH.value, True, detail=f"torch {got_full} present")
            )
            rep.checks.append(
                EnvCheck(
                    EnvStage.VERSION.value,
                    False,
                    EnvCode.TORCH_LOCAL_TAG_MISMATCH.value,
                    f"installed build +{got_tag}, CUDA backend needs a +cu wheel",
                    REMEDIES[EnvCode.TORCH_LOCAL_TAG_MISMATCH.value],
                )
            )
            return rep
        if got_base != req_base:
            rep.checks.append(
                EnvCheck(
                    EnvStage.TORCH.value,
                    True,
                    detail=f"torch {installed} present (version gate below)",
                )
            )
            rep.checks.append(
                EnvCheck(
                    EnvStage.VERSION.value,
                    False,
                    EnvCode.TORCH_VERSION_MISMATCH.value,
                    f"detected {got_base}, required {req_base}",
                    REMEDIES[EnvCode.TORCH_VERSION_MISMATCH.value],
                )
            )
            return rep
        rep.checks.append(EnvCheck(EnvStage.TORCH.value, True, detail=f"torch {installed}"))
        rep.checks.append(EnvCheck(EnvStage.VERSION.value, True, detail=f"matches pin {req_base}"))

        if env_exe == sys.executable:
            smoke = self.run_in_process_smoke(backend)
            rep.checks.append(smoke)
            rep.pytorch["compatible"] = smoke.ok
            rep.training_ready = all(c.ok for c in rep.checks)
            rep.in_process_ready = rep.training_ready
        else:
            # Cross-interpreter smoke (import + tensor/alloc test under THAT python).
            smoke = self.run_subprocess_smoke(env_exe or "", backend)
            rep.checks.append(smoke)
            rep.pytorch["compatible"] = smoke.ok
            rep.training_ready = all(c.ok for c in rep.checks)
            rep.in_process_ready = False
            rep.training_command = (
                f'"{env_exe}" -m nexus_scalp.cli.main model-train-local '
                f"--input <your export>  (with PYTHONPATH set to a nexus_scalp checkout)"
            )
        if rep.training_ready and not rep.in_process_ready:
            rep.checks.append(
                EnvCheck(
                    EnvStage.READY.value,
                    True,
                    detail=f"READY in {env_block['path']} — run training under: {rep.training_command}",
                )
            )
        elif rep.training_ready:
            rep.checks.append(EnvCheck(EnvStage.READY.value, True, detail="READY (in-process)"))
        return rep

    # -- smoke tests (REAL operations, both paths) ---------------------------
    def run_in_process_smoke(self, backend: str) -> EnvCheck:
        try:
            import torch

            x = torch.ones(64, 70)
            w = torch.randn(70, 3)
            y = x @ w
            if not bool(torch.isfinite(y).all()):
                return EnvCheck(
                    EnvStage.SMOKE.value,
                    False,
                    EnvCode.SMOKE_TENSOR_FAILED.value,
                    "non-finite tensor output",
                    REMEDIES[EnvCode.SMOKE_TENSOR_FAILED.value],
                )
            if backend == "cuda":
                if not torch.cuda.is_available():
                    return EnvCheck(
                        EnvStage.SMOKE.value,
                        False,
                        EnvCode.SMOKE_CUDA_UNAVAILABLE.value,
                        "torch.cuda.is_available() False",
                        REMEDIES[EnvCode.SMOKE_CUDA_UNAVAILABLE.value],
                    )
                dev = torch.device("cuda:0")
                a = torch.ones(128, 70, device=dev) * 2.0
                b = torch.randn(70, 3, device=dev)
                c = a @ b
                ok = bool(torch.isfinite(c).all())
                name = torch.cuda.get_device_name(0)
                if not ok:
                    return EnvCheck(
                        EnvStage.SMOKE.value,
                        False,
                        EnvCode.SMOKE_CUDA_ALLOC_FAILED.value,
                        "cuda matmul produced non-finite output",
                        REMEDIES[EnvCode.SMOKE_CUDA_ALLOC_FAILED.value],
                    )
                return EnvCheck(
                    EnvStage.SMOKE.value,
                    True,
                    detail=f"cpu+tensor ok; CUDA allocation ok on {name}",
                )
            return EnvCheck(EnvStage.SMOKE.value, True, detail="cpu tensor op ok")
        except Exception as exc:
            return EnvCheck(
                EnvStage.SMOKE.value,
                False,
                EnvCode.TORCH_IMPORT_FAILED.value,
                type(exc).__name__,
                REMEDIES[EnvCode.TORCH_IMPORT_FAILED.value],
            )

    def run_subprocess_smoke(self, python_exe: str, backend: str) -> EnvCheck:
        script = (
            "import torch,sys\n"
            "x=torch.ones(64,70); w=torch.randn(70,3); y=x@w\n"
            "assert bool(torch.isfinite(y).all()), 'tensor'\n"
            + (
                "assert torch.cuda.is_available(), 'cuda-avail'\n"
                "a=torch.ones(128,70,device='cuda:0')*2.0; b=torch.randn(70,3,device='cuda:0')\n"
                "assert bool(torch.isfinite((a@b)).all()), 'cuda-alloc'\n"
                "print('OK cuda:'+torch.cuda.get_device_name(0))\n"
                if backend == "cuda"
                else "print('OK cpu')\n"
            )
        )
        try:
            proc = subprocess.run(
                [python_exe, "-c", script], capture_output=True, text=True, timeout=180, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return EnvCheck(
                EnvStage.SMOKE.value,
                False,
                EnvCode.TORCH_IMPORT_FAILED.value,
                str(exc)[:150],
                REMEDIES[EnvCode.TORCH_IMPORT_FAILED.value],
            )
        if proc.returncode == 0:
            return EnvCheck(EnvStage.SMOKE.value, True, detail=proc.stdout.strip()[-120:])
        err = (proc.stderr or "").lower()
        if "cudaaavail" in err or "cuda-avail" in err:
            code, msg = EnvCode.SMOKE_CUDA_UNAVAILABLE, proc.stderr
        elif "cuda-alloc" in err:
            code, msg = EnvCode.SMOKE_CUDA_ALLOC_FAILED, proc.stderr
        elif "modulenot" in err:
            code, msg = EnvCode.TORCH_IMPORT_FAILED, proc.stderr
        else:
            code, msg = EnvCode.SMOKE_TENSOR_FAILED, proc.stderr
        return EnvCheck(EnvStage.SMOKE.value, False, code.value, msg[:200], REMEDIES[code.value])

    # -- provisioning (explicit user action ONLY) ----------------------------
    def install(self, backend: str | None = None, *, progress: Any = None) -> EnvironmentReport:
        """Resolve the target env, pip-install the exact pinned variant, re-verify.

        Classifies pip failures into the taxonomy (network / index / version /
        permission). Never claims success: the return is a FRESH status()."""
        contract = self.contract
        backend = (backend or self.chosen_backend()).lower()
        variant = contract.get("variants", {}).get(backend)
        if not variant:
            raise TrainingEnvironmentError(EnvCode.CONTRACT_INVALID, f"no variant '{backend}'")
        # Provisioning steps run IN ORDER, each typed: base python -> venv.
        base_python, pchk = self.resolve_python()
        if not pchk.ok:
            try:
                raise TrainingEnvironmentError(EnvCode(pchk.code), pchk.detail)
            except ValueError:
                raise TrainingEnvironmentError(EnvCode.PYTHON_NOT_FOUND, pchk.detail) from None
        if not self.venv_python() and not self.status(backend=backend).environment.get("found"):
            vchk = self.create_managed_venv(base_python or "")
            if not vchk.ok:
                raise TrainingEnvironmentError(EnvCode.VENV_CREATE_FAILED, vchk.detail)
        rep = self.status(backend=backend)
        python_exe = (rep.pytorch or {}).get("path") or sys.executable
        if not rep.environment.get("found"):
            first = rep.failing()[0] if rep.failing() else None
            try:
                code = EnvCode(first.code) if first and first.code else EnvCode.VENV_CREATE_FAILED
            except ValueError:
                code = EnvCode.VENV_CREATE_FAILED
            raise TrainingEnvironmentError(
                code, "environment not resolved: " + (first.detail if first else "")
            )
        specs = [f"{pkg}{op}{ver}" for pkg, (op, ver) in variant.get("packages", {}).items()]
        # Install THROUGH the target interpreter's own pip — the packages
        # land in THAT environment (no --target hacks).
        args = [python_exe, "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
        args += [
            *specs,
            "--extra-index-url",
            str(variant.get("index_url", "")),
            "--extra-index-url",
            "https://pypi.org/simple",
        ]
        if callable(progress):
            progress(f"installing {', '.join(specs)} ({backend})")
        logger.info(
            "[TRAINING-ENV] event=INSTALL_STARTED backend=%s specs=%s target=%s",
            backend,
            specs,
            (rep.environment or {}).get("path"),
        )
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=3600, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TrainingEnvironmentError(
                EnvCode.NETWORK_UNREACHABLE, f"pip timed out: {exc}"
            ) from exc
        except OSError as exc:
            raise TrainingEnvironmentError(EnvCode.PIP_UNAVAILABLE, str(exc)) from exc
        if proc.returncode != 0:
            raise TrainingEnvironmentError(*classify_pip_failure(proc.stderr + proc.stdout))
        logger.info("[TRAINING-ENV] event=INSTALL_FINISHED backend=%s", backend)
        out = self.status(backend=backend)
        out.install_attempted = True
        out.install_result = "installed" if out.training_ready else "installed-but-not-ready"
        return out


def classify_pip_failure(text: str) -> tuple[EnvCode, str]:
    t = (text or "").lower()
    if "could not find a version" in t or "no matching distribution" in t:
        return EnvCode.PACKAGE_VERSION_UNSATISFIED, text.strip()[-200:]
    if "permission denied" in t or "readonly" in t or ("operating system" in t and "denied" in t):
        return EnvCode.INSTALL_PERMISSION_DENIED, text.strip()[-200:]
    if (
        "maximum retry" in t
        or "connection" in t
        or "network" in t
        or "timed out" in t
        or "nameorresolution" in t
    ):
        return EnvCode.NETWORK_UNREACHABLE, text.strip()[-200:]
    if "403" in t or ("index" in t and ("unreachable" in t or "error" in t)):
        return EnvCode.INDEX_UNAVAILABLE, text.strip()[-200:]
    return EnvCode.INSTALL_FAILED, text.strip()[-200:]


__all__ = [
    "BACKEND_ENV",
    "REMEDIES",
    "TRAINING_ENV_DIR_ENV",
    "TRAINING_PYTHON_ENV",
    "EnvCheck",
    "EnvCode",
    "EnvStage",
    "EnvironmentReport",
    "TrainingEnvironmentError",
    "TrainingEnvironmentManager",
    "canonical_contract_path",
    "classify_pip_failure",
    "current_torch_facts",
    "detect_nvidia_gpu",
    "load_contract",
    "probe_python_interpreter",
]
