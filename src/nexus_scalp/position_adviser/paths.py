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
operation); only ``resolve_within_trusted_roots`` resolves, because containment
must be answered against the real, symlink-followed path.
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


#: Shape barrier for a path string entering ``resolve_within_trusted_roots``:
#: optional drive + safe nested dirs/filenames (hyphens and spaces allowed, so
#: ``C:\\Users\\John Doe\\...`` matches). A segment can never START with ``.``,
#: so ``..`` traversal, a NUL, a newline and shell metacharacters can never
#: match — the same whitelist contract as ``_SAFE_REL`` above, extended with a
#: drive prefix for absolute values.
_SAFE_ABS_OR_REL = re.compile(
    r"(?:[A-Za-z]:[\\/]{1,2})?(?:[A-Za-z0-9_ -][A-Za-z0-9_ .-]{0,127}[\\/])*"
    r"[A-Za-z0-9_ -][A-Za-z0-9_ .-]{0,191}"
)


def resolve_within_trusted_roots(raw: str | Path, roots: list[Path]) -> Path | None:
    """Canonicalize ``raw`` and return it only when inside one of ``roots``.

    The whitelist match runs IN THIS FUNCTION before any ``Path`` is built from
    the input, so the taint chain ends at the ``fullmatch`` barrier rather than
    at the containment check — the same barrier pattern as the other
    sanitizers. ``Path.resolve`` then follows symlinks and normalizes the
    value, and the containment loop below is the trust boundary. Returns
    ``None`` on a shape violation, an unresolvable path or a value outside
    every root (fail-closed). ``roots`` are supplied by the callers from
    trusted constants (package location, ``REPO_ROOT``, ``tempdir``) or from
    env values each caller has already shape-guarded.

    This is the one helper in this module that touches the filesystem:
    resolution is what makes the containment answer authoritative against
    symlinks.
    """
    s = str(raw or "").strip()
    if not s or not _SAFE_ABS_OR_REL.fullmatch(s):
        return None
    try:
        resolved = Path(s).expanduser().resolve()
    except (OSError, ValueError):
        return None
    for root in roots:
        try:
            if resolved.is_relative_to(Path(root).resolve()):
                return resolved
        except (OSError, ValueError):
            continue
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
