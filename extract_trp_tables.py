#!/usr/bin/env python3
"""Extract TranslatePress table statements from a full WordPress SQL dump."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from trp_tool.sql import split_sql_statements

TRP_TABLE = re.compile(r"`[^`]*trp_[^`]+`", re.IGNORECASE)
ALLOWED = re.compile(
    r"^\s*(?:CREATE\s+TABLE|INSERT\s+INTO|ALTER\s+TABLE)", re.IGNORECASE
)


def extract_statements(text: str) -> list[str]:
    return [
        statement
        for statement in split_sql_statements(text)
        if ALLOWED.match(statement) and TRP_TABLE.search(statement)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="destination SQL file")
    parser.add_argument("input", nargs="?", default="dump.sql", help="source dump")
    args = parser.parse_args(argv)
    source = Path(args.input).read_text(encoding="utf-8", errors="replace")
    statements = extract_statements(source)
    with Path(args.output).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("SET NAMES utf8mb4;\nSET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n\n")
        handle.writelines(statement + ";\n" for statement in statements)
    kinds: dict[str, int] = {}
    for statement in statements:
        match = re.match(
            r"\s*(CREATE\s+TABLE|INSERT\s+INTO|ALTER\s+TABLE)", statement, re.IGNORECASE
        )
        if match:
            kind = match.group(1).upper()
            kinds[kind] = kinds.get(kind, 0) + 1
    print(f"extracted {len(statements)} statements {kinds} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
