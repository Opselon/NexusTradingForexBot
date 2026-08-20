"""DDL porting — translate SQLite CREATE TABLE statements into PostgreSQL.

Used by the SQLite→PostgreSQL migration engine to create destination tables
with provider-correct types and identity semantics:

  * ``INTEGER PRIMARY KEY AUTOINCREMENT``  → ``BIGSERIAL PRIMARY KEY``
  * ``INTEGER PRIMARY KEY`` (rowid alias) → ``BIGSERIAL PRIMARY KEY``
  * ``REAL`` / ``FLOAT`` / ``DOUBLE``      → ``DOUBLE PRECISION``
  * ``BLOB``                               → ``BYTEA``
  * ``DATETIME`` / ``TIMESTAMP``           → ``TIMESTAMPTZ``
  * boolean-ish ``INTEGER NOT NULL DEFAULT 0/1`` stays INTEGER (the app maps
    them explicitly in code; changing to BOOLEAN would alter SELECT results)
  * ``WITHOUT ROWID`` is dropped (PG has no rowid concept)

Index statements are left untouched (portable syntax: CREATE INDEX ... ON
table(cols)).
"""

from __future__ import annotations

import re

from nexus_scalp.database.drivers.postgres_driver import PG_TYPE_MAP


def _type_map() -> dict[str, str]:
    return PG_TYPE_MAP


def port_column_type(declared: str) -> str:
    """Translate one column type token (post parenthesized params removal)."""
    name = (declared or "TEXT").strip().upper()
    if "(" in name:
        name = name.split("(", 1)[0]
    mapped = _type_map().get(name)
    return mapped or (name if name in {"DOUBLE PRECISION", "TIMESTAMPTZ", "BYTEA", "JSONB", "BIGSERIAL", "SERIAL"} else "TEXT")


def port_create_table(ddl: str) -> str | None:
    """Port a SQLite CREATE TABLE statement to PostgreSQL.

    Returns None when the statement is not a CREATE TABLE (caller skips).
    Preserves constraints, defaults, unique clauses and column order.
    """
    m = re.match(r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\"\w.]+)", ddl, re.I)
    if not m:
        return None
    table = m.group(1)
    # locate the column block (first '(' to matching ')')
    start = ddl.find("(")
    if start == -1:
        return ddl
    depth = 0
    end = -1
    for k in range(start, len(ddl)):
        if ddl[k] == "(":
            depth += 1
        elif ddl[k] == ")":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end == -1:
        return ddl
    head = ddl[:start]
    body = ddl[start + 1 : end]
    tail = ddl[end + 1 :]

    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    out_lines: list[str] = []
    for ln in lines:
        stripped = ln.rstrip(",")
        upper = stripped.upper()
        # table-level constraint lines pass through
        if upper.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK", "CONSTRAINT")):
            out_lines.append(stripped)
            continue
        # column-level: name + type + constraints
        mcol = re.match(r'(["\w]+)\s+(.+)$', stripped)
        if not mcol:
            out_lines.append(stripped)
            continue
        colname = mcol.group(1)
        rest = mcol.group(2)
        # split first token (type) from remaining constraints
        tokens = rest.split(None, 1)
        coltype = tokens[0] if tokens else "TEXT"
        constraints = tokens[1] if len(tokens) > 1 else ""
        # INTEGER PRIMARY KEY AUTOINCREMENT / INTEGER PRIMARY KEY (rowid
        # alias) -> BIGSERIAL PRIMARY KEY; drop any AUTOINCREMENT suffix.
        if coltype.upper() in {"INTEGER", "INT"} and re.match(r"PRIMARY\s+KEY\b", constraints, re.I):
            constraints = re.sub(r"AUTOINCREMENT", "", constraints, flags=re.I)
            constraints = re.sub(r"\bPRIMARY\s+KEY\b", "", constraints, flags=re.I).strip()
            out_lines.append(f"{colname} BIGSERIAL PRIMARY KEY" + (f" {constraints}" if constraints else ""))
            continue
        ported = port_column_type(coltype)
        out_lines.append(f"{colname} {ported}" + (f" {constraints}" if constraints else ""))
    if not out_lines:
        return ddl
    # strip any trailing 'WITHOUT ROWID' / 'STRICT' in tail
    tail = re.sub(r"WITHOUT\s+ROWID", "", tail, flags=re.I)
    tail = re.sub(r"STRICT", "", tail, flags=re.I)
    tail = tail.rstrip().rstrip(",") if tail.strip() else tail
    return f"{head}({', '.join(out_lines)}){tail}"


def _drop_autoincrement(constraints: str) -> str:
    """Remove AUTOINCREMENT from remaining constraints."""
    return re.sub(r"\s*AUTOINCREMENT", "", constraints, flags=re.I).rstrip()