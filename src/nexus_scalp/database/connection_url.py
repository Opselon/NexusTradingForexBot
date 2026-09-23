"""Pure parse / build / redact helpers for PostgreSQL connection URLs.

DATABASE PORTABILITY — connection-string entry (2026-09-23): an operator may
type ONE URL (``postgresql://user@host:port/db?sslmode=require``) instead of
filling six discrete fields.  This module is the server-side authority for
that string; the react console mirrors it by contract
(``frontend/.../connectionUrl.ts``), never by import.

INVARIANTS (contract §3.1):
  * the accepted scheme set is EXACTLY ``{"postgresql", "postgres", "pgsql"}``
    — the same set ``DatabaseProvider.parse`` accepts;
  * the module NEVER raises at the configuration boundary: a bad URL returns
    a ``ParseFailure`` with a human sentence, never an exception traceback
    (exception text may embed credentials or internals);
  * ``build_pg_url`` NEVER writes a password into the emitted URL;
  * ``redact_url`` replaces any password with ``***`` for display/logs;
  * no import of the web layer — this is a pure dependency-free module.

EXTEND: add a new accepted field by teaching ``_apply_query_args`` and
``build_pg_url`` about it, and mirror the change in the client module.
"""

from __future__ import annotations

from typing import TypedDict
from urllib.parse import unquote

from typing_extensions import TypeIs

#: Schemes accepted on both sides of the wire (contract §3.1).
#: Mirrors ``DatabaseProvider.parse``'s postgres-family set exactly.
PG_URL_SCHEMES: frozenset[str] = frozenset({"postgresql", "postgres", "pgsql"})

#: Query-string keys understood by the parser (lower-cased on read).
_SSL_MODE_KEYS: frozenset[str] = frozenset({"sslmode", "ssl_mode"})

#: SQL-standard SSL mode values (used only to normalize case/spelling of a
#: value the operator already typed — never to invent one).
_SSL_MODES: frozenset[str] = frozenset(
    {
        "disable",
        "allow",
        "prefer",
        "require",
        "verify-ca",
        "verify-full",
    }
)


class ParsedPgConfig(TypedDict):
    """Discrete PostgreSQL fields derived from a URL (password EXCLUDED).

    ``password`` is deliberately absent: the route layer routes it to the
    OS SecretStore and this dict is what travels back to the browser.
    """

    host: str
    port: int
    database: str
    username: str
    ssl_mode: str


class ParseFailure(TypedDict):
    """A refusal, as a human sentence (never an exception text)."""

    reason: str


def _sentence(reason: str) -> ParseFailure:
    return {"reason": reason}


def _norm_scheme(scheme: str | None) -> str:
    return (scheme or "").strip().lower()


def _fail_bad_scheme(scheme: str) -> ParseFailure:
    pretty = scheme or "(none)"
    return _sentence(
        f"Connection URL must use the postgresql scheme (postgresql, postgres "
        f"or pgsql); got '{pretty}'."
    )


def _parse_port(raw: str | None) -> int | None:
    """Return the port as int, or None when it is not a usable 1..65535 value."""
    if raw is None or raw == "":
        return None
    text = raw.strip()
    if not text.isdigit():
        return None
    value = int(text)
    if not 1 <= value <= 65535:
        return None
    return value


def _apply_query_args(fields: dict[str, object], query: str) -> None:
    """Fold the query string's sslmode into ``fields`` (in place)."""
    if not query:
        return
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        name = unquote(key).strip().lower()
        if name in _SSL_MODE_KEYS:
            fields["ssl_mode"] = unquote(value).strip()


def _normalize_ssl_mode(value: str) -> str:
    """Canonicalize a typed ssl mode; empty stays empty (never invented)."""
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    return lowered if lowered in _SSL_MODES else text


def parse_pg_url(raw: str) -> ParsedPgConfig | ParseFailure:
    """Parse ``postgresql://user[:***@host:port/db?sslmode=require``.

    Accepts the ``postgres://`` and ``pgsql://`` spellings too.  Rejects:
    a scheme outside the postgres family, a missing host, a non-numeric port,
    a port outside 1..65535 and a URL with no database name in its path.
    Never raises.
    """
    if not isinstance(raw, str) or not raw.strip():
        return _sentence("A connection URL is required.")

    text = raw.strip()
    if "://" not in text:
        return _fail_bad_scheme(text.split(":", 1)[0] if ":" in text else "")

    scheme, rest = text.split("://", 1)
    scheme = _norm_scheme(scheme)
    if scheme not in PG_URL_SCHEMES:
        return _fail_bad_scheme(scheme)

    if not rest:
        return _sentence("Connection URL is missing its host and database name.")

    # Split off the query string before touching credentials/host.
    path_and_query = rest.split("?", 1)
    authority_path = path_and_query[0]
    query = path_and_query[1] if len(path_and_query) > 1 else ""

    # rsplit: an IPv6 host contains ':' but the userinfo '@' is the real seam.
    creds_sep = authority_path.rfind("@")
    if creds_sep >= 0:
        userinfo = authority_path[:creds_sep]
        hostport_path = authority_path[creds_sep + 1 :]
    else:
        userinfo = ""
        hostport_path = authority_path

    username = unquote(userinfo.split(":", 1)[0]).strip() if userinfo else ""

    # netloc may carry a port AND a path: split the first '/'.
    path_sep = hostport_path.find("/")
    if path_sep >= 0:
        hostport = hostport_path[:path_sep]
        path = hostport_path[path_sep:]
    else:
        hostport = hostport_path
        path = ""

    host: str
    port: int
    if hostport.startswith("["):
        # IPv6 literal: [::1]:5432
        close = hostport.find("]")
        if close < 0:
            return _sentence("Connection URL has an unterminated IPv6 host literal.")
        host = hostport[1:close]
        tail = hostport[close + 1 :]
        port_value = _parse_port(tail.lstrip(":") if tail else None)
        if tail and port_value is None:
            return _sentence("Connection URL port must be a number between 1 and 65535.")
        port = port_value if port_value is not None else 5432
    elif ":" in hostport:
        host_text, _, port_text = hostport.partition(":")
        host = host_text.strip()
        port_value = _parse_port(port_text)
        if port_value is None:
            return _sentence("Connection URL port must be a number between 1 and 65535.")
        port = port_value
    else:
        host = hostport.strip()
        port = 5432

    if not host:
        return _sentence("Connection URL is missing its host.")

    database = unquote(path.lstrip("/")).strip()
    if not database:
        return _sentence("Connection URL is missing its database name.")

    fields: dict[str, object] = {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "ssl_mode": "",
    }
    _apply_query_args(fields, query)
    fields["ssl_mode"] = _normalize_ssl_mode(str(fields["ssl_mode"]))

    return fields  # type: ignore[return-value]


def is_parse_failure(result: ParsedPgConfig | ParseFailure) -> TypeIs[ParseFailure]:
    """Discriminate the parse_pg_url union for type checkers.

    mypy does not narrow a TypedDict union from a literal `in` test, so the
    routes layer guards with this TypeGuard instead of `"reason" in parsed`.
    """
    return "reason" in result


def build_pg_url(cfg: ParsedPgConfig) -> str:
    """Inverse of :func:`parse_pg_url`: fields -> a display URL.

    The emitted URL NEVER carries a password: it is a round-trip of the
    discrete fields only, safe to show in the UI and safe to log.
    """
    host = str(cfg.get("host") or "")
    host_part = host
    if ":" in host_part and not host_part.startswith("["):
        host_part = f"[{host_part}]"
    username = str(cfg.get("username") or "")
    user_part = f"{username}@" if username else ""
    port = cfg.get("port") or 5432
    database = str(cfg.get("database") or "")
    ssl_mode = _normalize_ssl_mode(str(cfg.get("ssl_mode") or ""))
    url = f"postgresql://{user_part}{host_part}:{port}/{database}"
    if ssl_mode:
        url = f"{url}?sslmode={ssl_mode}"
    return url


def redact_url(raw: str) -> str:
    """Mask any password in a URL for display: ``user:***@host``.

    Used for the UI echo of what the operator typed.  A URL with no
    userinfo or no password separator is returned unchanged.
    """
    if not isinstance(raw, str) or "://" not in raw:
        return raw
    scheme, rest = raw.split("://", 1)
    if "@" not in rest:
        return raw
    creds, tail = rest.rsplit("@", 1)
    if ":" not in creds:
        return raw
    user, _pw = creds.split(":", 1)
    return f"{scheme}://{user}:***@{tail}"
