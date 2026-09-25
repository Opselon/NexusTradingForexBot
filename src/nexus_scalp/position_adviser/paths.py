"""Path sanitizers for the position adviser (CodeQL ``py/path-injection``).

The adviser's artifact paths originate in HTTP request bodies: a dataset to
read, a checkpoint to load, an output directory to write into, a ``model_id``
joined into written filenames. Containment checks answer "is the resolved path
inside the root?"; the sanitizers here answer the different question the sink
asks first: "can the string carry a path *component* at all?"

A request-supplied string is reduced to a whitelist of plain filename
characters into a NEW value, and that value is the ONLY thing a ``Path`` is
built from downstream. The taint chain therefore ends at the sanitizer rather
than at the containment check, which is why this module exists separately from
the ``is_relative_to`` barriers the callers also keep as defense-in-depth.

Nothing else here touches the filesystem: the other functions are pure string
-> Path reductions (``Path.relative_to`` included, which is a PurePath
operation); ``resolve_within_trusted_roots`` resolves only its TRUSTED ROOTS
(the value itself is resolved inside ``resolve_under_root``), because
containment must be answered against the real, symlink-followed root paths.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Trusted containment root for adviser artifacts. Derived from the package
#: location only (never from request input), so it is untainted by construction.
ADVISER_ROOT = Path(__file__).resolve().parents[3]

#: A single path component: identifier characters only. Must START and END with
#: an alphanumeric or underscore (no leading/trailing dot or dash), so it can
#: never be an all-dots component (``.``, ``..``, ``...``) or a DOS-style name.
#: Bounded to keep the compiled automaton and the written filename sane.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_])?")

#: Rejects a component that is only dots (``.``, ``..``, ``...``). The anchored
#: ``_SAFE_NAME`` above already admits no all-dots component; this is the
#: belt-and-braces assertion the trainers and tests pin behaviour against.
_DOTTY = re.compile(r"\A\.{1,}\Z")

#: A relative path: identifier components joined by separators. Every component
#: must be a ``_SAFE_NAME``, so ``..`` traversal and a leading separator (which
#: escapes the root on POSIX and leaves the result unanchored on Windows) are
#: impossible by construction — as are a drive letter, a NUL, a shell
#: metacharacter or a quote. Bounded so a sanitized value cannot be an
#: unbounded chain.
_SAFE_REL = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9._\\/ -]{0,510}[A-Za-z0-9_])?")

#: Ceiling on path depth, so a sanitized value cannot be an unbounded chain.
_MAX_SEGMENTS = 32


class AdviserPathError(ValueError):
    """A request-supplied path could not be reduced to a safe form.

    Raised rather than silently substituted: a path that cannot be sanitized is
    an explicit-but-invalid request, and substituting something else would hide
    both traversal attempts and stale UI state.
    """


def sanitize_rel_path(raw: str | Path, *, label: str) -> Path:
    """Reduce a request-supplied string to an UNTAINTED relative ``Path``.

    Returns a relative path whose every component matches the whitelist; raises
    :class:`AdviserPathError` when the input is empty or carries anything
    outside the safe set. The message never contains the offending value, so a
    caller that surfaces it cannot echo the payload back to a client.

    The result is relative on purpose: it names something *inside* whichever
    root the caller anchors it to, and cannot name a root of its own.
    """
    s = str(raw or "").strip()
    if not s:
        raise AdviserPathError(f"{label} must be a non-empty path")
    m = _SAFE_REL.fullmatch(s)
    if m is None:
        raise AdviserPathError(f"{label} has characters outside the safe set")
    parts = [seg for seg in re.split(r"[\\/]+", m.group(0)) if seg not in ("", ".")]
    if len(parts) > _MAX_SEGMENTS:
        raise AdviserPathError(f"{label} has too many path segments")
    if any(_DOTTY.fullmatch(p) for p in parts):
        raise AdviserPathError(f"{label} must not contain a parent-directory reference")
    return Path(*parts)


def sanitize_repo_relative(raw: str | Path, root: Path, *, label: str) -> Path:
    """Reduce an absolute-or-relative path to an UNTAINTED root-relative ``Path``.

    Handles both shapes a caller may hand over: a repo-relative name from a
    request body, and an already-absolute path produced by an earlier barrier
    (the web layer's ``_safe_under_repo``). An absolute path is narrowed to its
    root-relative form — rejected when it is not under ``root`` at all — and
    every component is then whitelisted, so ``..`` traversal cannot survive into
    the value a caller joins to the root.

    ``root`` is read from the module location by the callers, never from request
    input. ``Path.is_relative_to`` is deliberately run BEFORE any traversal is
    possible: a path containing ``..`` is caught by the whitelist below even when
    the prefix check alone would admit it, so no unvalidated absolute path is
    ever resolved.
    """
    s = str(raw or "").strip()
    if not s:
        raise AdviserPathError(f"{label} must be a non-empty path")
    if any(part == ".." for part in Path(s).parts):
        raise AdviserPathError(f"{label} must not contain a parent-directory reference")
    candidate = Path(s)
    if candidate.is_absolute():
        if not candidate.is_relative_to(root):
            raise AdviserPathError(f"{label} must stay inside the repository root")
        candidate = candidate.relative_to(root)
    return sanitize_rel_path(candidate, label=label)


def resolve_under_root(raw: str | Path, root: Path, *, label: str) -> Path:
    """Resolve ``raw`` to an absolute path inside ``root``, or raise.

    Sanitizes first (``sanitize_repo_relative``), then anchors the untainted
    relative value under ``root`` and resolves it once. ``Path.resolve`` follows
    symlinks, so a symlink payload pointing outside the root produces a value
    this rejects rather than opens. Callers must use the RETURNED value at the
    sink — not the input — so the taint chain ends here.
    """
    return (root.resolve() / sanitize_repo_relative(raw, root=root, label=label)).resolve()


def sanitize_name(raw: str | None, *, fallback: str) -> str:
    """Reduce a request-supplied name to an UNTAINTED single path component.

    Falls back to ``fallback`` for an empty or unsafe input rather than raising:
    a name is cosmetic (it becomes part of a filename), so substitution is the
    safe behaviour, unlike a path that must point at a real artifact.
    """
    if raw is None:
        return fallback
    s = str(raw).strip()
    if not s:
        return fallback
    if _DOTTY.fullmatch(s):
        return fallback
    m = _SAFE_NAME.fullmatch(s)
    return m.group(0) if m is not None else fallback


def resolve_within_trusted_roots(
    raw: str | Path, roots: list[Path], *, label: str = "path"
) -> Path | None:
    """Resolve ``raw`` through ``resolve_under_root`` and confine it to ``roots``.

    Every value the path machinery sees comes out of :func:`resolve_under_root`
    — the single-root sanitizer the trainer already uses, whose whitelist match
    builds the returned value (so the taint chain ends there rather than at a
    sink here; this helper deliberately performs no ``resolve`` of its own).

    Absolute values are tried against each trusted root in turn (both the
    caller's spelling and its canonical form, so a ``/tmp`` -> ``/private/tmp``
    style symlink between candidate and root cannot cause a false refusal);
    relative values are anchored at the CWD — repo-relative when the app runs
    from the repo — matching how callers hand over ``data/raw/...`` names.

    Each candidate is then re-checked AFTER resolution against every root's
    canonical form: the single-root sanitizer's own containment runs
    PRE-resolve, so this second check is what rejects a symlink under one root
    that points outside all of them. Returns ``None`` when nothing accepts the
    value (fail-closed). ``roots`` come from trusted constants (package
    location, ``REPO_ROOT``, ``tempdir``) or from env values each caller has
    already shape-guarded.
    """
    s = str(raw or "").strip()
    if not s or "\x00" in s:
        return None
    resolved_roots: list[Path] = []
    sanitize_roots: list[Path] = []
    for r in roots:
        candidate_root = Path(r)
        sanitize_roots.append(candidate_root)
        try:
            canonical = candidate_root.resolve()
        except (OSError, ValueError):
            continue
        sanitize_roots.append(canonical)
        resolved_roots.append(canonical)
    if not resolved_roots:
        return None

    def _inside(candidate: Path) -> bool:
        return any(candidate.is_relative_to(rr) for rr in resolved_roots)

    if Path(s).is_absolute():
        attempts = sanitize_roots
    else:
        attempts = [Path.cwd()]
    for root in attempts:
        try:
            resolved = resolve_under_root(s, root, label=label)
        except (AdviserPathError, OSError, ValueError):
            continue
        if _inside(resolved):
            return resolved
    return None


__all__ = [
    "ADVISER_ROOT",
    "AdviserPathError",
    "resolve_under_root",
    "resolve_within_trusted_roots",
    "sanitize_name",
    "sanitize_rel_path",
    "sanitize_repo_relative",
]
