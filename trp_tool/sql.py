"""TranslatePress SQL parsing, discovery, live reads, and guarded SQL output."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .models import (
    HUMAN_REVIEWED,
    MACHINE_TRANSLATED,
    SIMILAR_TRANSLATED,
    DictRow,
    TableInfo,
    TranslationRecord,
)

TABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")
DICTIONARY_MARKER = "trp_dictionary_"


def validate_identifier(value: str) -> str:
    if not TABLE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def sql_quote(value: object) -> str:
    """Quote a Python value as a MySQL utf8mb4-safe literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x1a", "\\Z")
        .replace("\x00", "\\0")
    )
    return f"'{escaped}'"


def sql_unescape(value: str) -> str:
    mapping = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "b": "\b",
        "0": "\0",
        "Z": "\x1a",
        "\\": "\\",
        "'": "'",
        '"': '"',
    }
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            output.append(mapping.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            output.append(char)
            index += 1
    return "".join(output)


def split_sql_tuples(blob: str) -> list[list[str | None]]:
    """Parse a MySQL VALUES body without losing quotes, commas, or newlines."""
    rows: list[list[str | None]] = []
    current: list[str | None] = []
    buffer: list[str] = []
    is_string = False
    in_string = False
    depth = 0
    index = 0

    def flush() -> None:
        raw = "".join(buffer)
        if is_string:
            current.append(sql_unescape(raw))
        else:
            stripped = raw.strip()
            current.append(None if stripped.upper() == "NULL" else stripped)
        buffer.clear()

    while index < len(blob):
        char = blob[index]
        if in_string:
            if char == "\\" and index + 1 < len(blob):
                buffer.extend((char, blob[index + 1]))
                index += 2
                continue
            if char == "'":
                if index + 1 < len(blob) and blob[index + 1] == "'":
                    buffer.append("'")
                    index += 2
                    continue
                in_string = False
                index += 1
                continue
            buffer.append(char)
            index += 1
            continue
        if char == "'":
            if not "".join(buffer).strip():
                buffer.clear()
            in_string = True
            is_string = True
            index += 1
            continue
        if char == "(" and depth == 0:
            depth = 1
            current = []
            buffer.clear()
            is_string = False
            index += 1
            continue
        if depth == 1 and char == ",":
            flush()
            is_string = False
            index += 1
            continue
        if char == ")" and depth == 1:
            flush()
            rows.append(current)
            depth = 0
            is_string = False
            index += 1
            continue
        if depth == 1:
            buffer.append(char)
        index += 1
    return rows


def _field(
    fields: list[str | None],
    positions: dict[str, int],
    name: str,
    default: str | None = None,
) -> str | None:
    position = positions.get(name)
    return fields[position] if position is not None else default


def split_sql_statements(text: str) -> list[str]:
    """Split SQL on top-level semicolons, preserving literals and comments safely."""
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    in_string = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                buffer.append(char)
            index += 1
            continue
        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if in_string:
            buffer.append(char)
            if char == "\\" and nxt:
                buffer.append(nxt)
                index += 2
                continue
            if char == "'":
                if nxt == "'":
                    buffer.append(nxt)
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if in_backtick:
            buffer.append(char)
            if char == "`":
                in_backtick = False
            index += 1
            continue
        if char == "'":
            in_string = True
            buffer.append(char)
            index += 1
            continue
        if char == "`":
            in_backtick = True
            buffer.append(char)
            index += 1
            continue
        if char == "#" or (char == "-" and nxt == "-"):
            in_line_comment = True
            index += 1 if char == "#" else 2
            continue
        if char == "/" and nxt == "*":
            in_block_comment = True
            index += 2
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
            index += 1
            continue
        buffer.append(char)
        index += 1
    final = "".join(buffer).strip()
    if final:
        statements.append(final)
    return statements


def parse_table_pair(table: str) -> tuple[str, str, str]:
    """Return prefix, source locale, and target locale from a dictionary table."""
    marker = table.lower().find(DICTIONARY_MARKER)
    if marker < 0:
        raise ValueError(f"not a TranslatePress dictionary table: {table}")
    prefix = table[:marker]
    locale_blob = table[marker + len(DICTIONARY_MARKER) :]
    parts = locale_blob.split("_")
    if len(parts) < 2:
        raise ValueError(f"cannot determine locale pair from table: {table}")

    # TranslatePress locale parts are normally ll or ll_CC. Prefer two-part
    # locales on each side, then fall back to a one-part source.
    if len(parts) >= 4:
        source = "_".join(parts[:2])
        target = "_".join(parts[2:])
    elif len(parts) == 3:
        source = parts[0]
        target = "_".join(parts[1:])
    else:
        source, target = parts
    return prefix, source, target


class Source:
    def list_dictionary_tables(self) -> list[str]:
        raise NotImplementedError

    def dictionary(self, table: str) -> list[DictRow]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class DumpSource(Source):
    """Read one or more dictionary tables from an offline SQL dump."""

    INSERT_PATTERN = re.compile(
        r"^\s*INSERT\s+INTO\s+`([^`]+)`\s*\(([^)]*)\)\s*VALUES\s*(.*)\s*$",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.text = Path(path).read_text(encoding="utf-8", errors="replace")
        self.statements = split_sql_statements(self.text)

    def list_dictionary_tables(self) -> list[str]:
        names: set[str] = set()
        for statement in self.statements:
            match = re.match(
                r"\s*(?:CREATE\s+TABLE|INSERT\s+INTO|ALTER\s+TABLE)\s+`([^`]*trp_dictionary_[^`]+)`",
                statement,
                re.IGNORECASE,
            )
            if match:
                names.add(match.group(1))
        return sorted(names)

    def dictionary(self, table: str) -> list[DictRow]:
        validate_identifier(table)
        rows: list[DictRow] = []
        found = False
        for statement in self.statements:
            match = self.INSERT_PATTERN.match(statement)
            if not match:
                continue
            if match.group(1) != table:
                continue
            found = True
            columns = [
                column.strip().strip("`") for column in match.group(2).split(",")
            ]
            positions = {name: position for position, name in enumerate(columns)}
            required = {"id", "original", "translated", "status"}
            missing = required - positions.keys()
            if missing:
                raise ValueError(
                    f"table `{table}` INSERT is missing columns: {', '.join(sorted(missing))}"
                )
            for fields in split_sql_tuples(match.group(3)):
                if len(fields) != len(columns):
                    raise ValueError(
                        f"table `{table}` row has {len(fields)} fields, expected {len(columns)}"
                    )

                rows.append(
                    DictRow(
                        id=int(_field(fields, positions, "id") or 0),
                        original=_field(fields, positions, "original") or "",
                        translated=_field(fields, positions, "translated"),
                        status=int(_field(fields, positions, "status") or 0),
                        block_type=int(_field(fields, positions, "block_type") or 0),
                        original_id=int(_field(fields, positions, "original_id"))
                        if _field(fields, positions, "original_id")
                        else None,
                    )
                )
        if not found:
            available = ", ".join(self.list_dictionary_tables()) or "none"
            raise ValueError(
                f"table `{table}` has no INSERT data in {self.path}; discovered: {available}"
            )
        return rows


class MySQLSource(Source):
    def __init__(self, host: str, user: str, password: str, database: str, port: int):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("pymysql is required for live database access") from exc
        self.conn = pymysql.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port,
            charset="utf8mb4",
            autocommit=False,
        )

    def list_dictionary_tables(self) -> list[str]:
        with self.conn.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            return sorted(
                row[0]
                for row in cursor.fetchall()
                if DICTIONARY_MARKER in row[0].lower()
            )

    def dictionary(self, table: str) -> list[DictRow]:
        validate_identifier(table)
        with self.conn.cursor() as cursor:
            cursor.execute(
                f"SELECT id, original, translated, status, block_type, original_id FROM `{table}`"
            )
            return [
                DictRow(
                    id=row[0],
                    original=row[1] or "",
                    translated=row[2],
                    status=row[3] or 0,
                    block_type=row[4] or 0,
                    original_id=row[5],
                )
                for row in cursor.fetchall()
            ]

    def close(self) -> None:
        self.conn.close()


def inspect_tables(source: Source) -> list[TableInfo]:
    output: list[TableInfo] = []
    for table in source.list_dictionary_tables():
        try:
            prefix, source_locale, target_locale = parse_table_pair(table)
        except ValueError:
            continue
        rows = source.dictionary(table)
        output.append(
            TableInfo(
                name=table,
                prefix=prefix,
                source_locale=source_locale,
                target_locale=target_locale,
                row_count=len(rows),
                untranslated_count=sum(not row.has_translation for row in rows),
                machine_count=sum(
                    row.has_translation and row.status == MACHINE_TRANSLATED
                    for row in rows
                ),
                human_count=sum(
                    row.has_translation and row.status == HUMAN_REVIEWED for row in rows
                ),
                similar_count=sum(
                    row.has_translation and row.status == SIMILAR_TRANSLATED
                    for row in rows
                ),
                other_translated_count=sum(
                    row.has_translation
                    and row.status
                    not in (MACHINE_TRANSLATED, HUMAN_REVIEWED, SIMILAR_TRANSLATED)
                    for row in rows
                ),
            )
        )
    return output


def select_table(
    source: Source,
    table: str | None = None,
    source_locale: str | None = None,
    target_locale: str | None = None,
) -> TableInfo:
    tables = inspect_tables(source)
    if table:
        matches = [candidate for candidate in tables if candidate.name == table]
    else:
        matches = tables
        if source_locale:
            matches = [
                candidate
                for candidate in matches
                if candidate.source_locale.casefold() == source_locale.casefold()
            ]
        if target_locale:
            matches = [
                candidate
                for candidate in matches
                if candidate.target_locale.casefold() == target_locale.casefold()
            ]
    if len(matches) == 1:
        return matches[0]
    discovered = (
        ", ".join(
            f"{item.name} ({item.source_locale}->{item.target_locale})"
            for item in tables
        )
        or "none"
    )
    if not matches:
        raise ValueError(f"no matching dictionary table; discovered: {discovered}")
    raise ValueError(
        f"multiple dictionary tables match; pass --table or both locales: {discovered}"
    )


def guarded_update_statement(table: str, record: TranslationRecord) -> str:
    validate_identifier(table)
    row = record.row
    return (
        f"UPDATE `{table}` SET translated = {sql_quote(record.translated_text)}, "
        f"status = {record.new_status} WHERE id = {row.id} "
        f"AND original <=> {sql_quote(row.original)} "
        f"AND translated <=> {sql_quote(row.translated)} AND status = {row.status};"
    )


def guarded_rollback_statement(table: str, record: TranslationRecord) -> str:
    validate_identifier(table)
    row = record.row
    return (
        f"UPDATE `{table}` SET translated = {sql_quote(row.translated)}, status = {row.status} "
        f"WHERE id = {row.id} AND original <=> {sql_quote(row.original)} "
        f"AND translated <=> {sql_quote(record.translated_text)} "
        f"AND status = {record.new_status};"
    )


def write_sql_file(
    path: str | Path,
    statements: Iterable[str],
    comments: Iterable[str],
) -> None:
    statement_list = list(statements)
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        for comment in comments:
            safe_comment = str(comment).replace("\n", " ").replace("\r", " ")
            handle.write(f"-- {safe_comment}\n")
        handle.write(f"-- statements: {len(statement_list)}\n")
        handle.write(
            "-- Each UPDATE verifies the snapshot source, translation, and status.\n"
            "-- A stale or manually edited row is left unchanged. Check affected rows in phpMyAdmin.\n\n"
        )
        handle.write("SET NAMES utf8mb4;\n")
        handle.write("SET SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';\n\n")
        handle.write("START TRANSACTION;\n\n")
        for index, statement in enumerate(statement_list, start=1):
            handle.write(statement + "\n")
            handle.write(
                f"SELECT {index} AS patch_item, ROW_COUNT() AS affected_rows;\n"
            )
        handle.write("\nCOMMIT;\n")


def write_patch(
    path: str | Path, table: str, records: Iterable[TranslationRecord], model: str
) -> None:
    valid = [
        record
        for record in records
        if record.translation_status == "translated"
        and record.validation_status == "passed"
    ]
    write_sql_file(
        path,
        (guarded_update_statement(table, record) for record in valid),
        (
            "TranslatePress machine translation patch",
            f"table: {table}",
            f"model: {model}",
        ),
    )


def write_rollback(
    path: str | Path, table: str, records: Iterable[TranslationRecord]
) -> None:
    valid = [
        record
        for record in records
        if record.translation_status == "translated"
        and record.validation_status == "passed"
    ]
    write_sql_file(
        path,
        (guarded_rollback_statement(table, record) for record in valid),
        ("TranslatePress guarded rollback", f"table: {table}"),
    )


def write_preflight(
    path: str | Path, table: str, records: Iterable[TranslationRecord]
) -> None:
    """Write a non-persistent snapshot check for every guarded patch row."""
    validate_identifier(table)
    valid = [
        record
        for record in records
        if record.translation_status == "translated"
        and record.validation_status == "passed"
    ]
    temp_table = "_trp_preflight_expected"
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("-- TranslatePress guarded patch preflight\n")
        handle.write(f"-- table: {table}\n")
        handle.write(f"-- expected rows: {len(valid)}\n")
        handle.write(
            "-- Run in one database session immediately before importing the patch.\n"
        )
        handle.write(
            "-- The detail query must return zero rows and stale_rows must be 0.\n\n"
        )
        handle.write("SET NAMES utf8mb4;\n\n")
        handle.write(f"DROP TEMPORARY TABLE IF EXISTS `{temp_table}`;\n")
        handle.write(
            f"CREATE TEMPORARY TABLE `{temp_table}` (\n"
            "  patch_item INT UNSIGNED NOT NULL,\n"
            "  row_id BIGINT UNSIGNED NOT NULL,\n"
            "  original LONGTEXT NOT NULL,\n"
            "  translated LONGTEXT NULL,\n"
            "  status INT NOT NULL,\n"
            "  PRIMARY KEY (row_id)\n"
            ") CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n\n"
        )
        if valid:
            handle.write(
                f"INSERT INTO `{temp_table}` "
                "(patch_item, row_id, original, translated, status) VALUES\n"
            )
            for index, record in enumerate(valid, start=1):
                row = record.row
                suffix = "," if index < len(valid) else ";"
                handle.write(
                    f"({index}, {row.id}, {sql_quote(row.original)}, "
                    f"{sql_quote(row.translated)}, {row.status}){suffix}\n"
                )
        mismatch = (
            "c.id IS NULL OR NOT (c.original <=> e.original) OR "
            "NOT (c.translated <=> e.translated) OR NOT (c.status <=> e.status)"
        )
        handle.write(
            "\nSELECT e.patch_item, e.row_id,\n"
            "  CASE\n"
            "    WHEN c.id IS NULL THEN 'missing_row'\n"
            "    WHEN NOT (c.original <=> e.original) THEN 'source_changed'\n"
            "    WHEN NOT (c.translated <=> e.translated) THEN 'translation_changed'\n"
            "    WHEN NOT (c.status <=> e.status) THEN 'status_changed'\n"
            "  END AS mismatch\n"
            f"FROM `{temp_table}` e\n"
            f"LEFT JOIN `{table}` c ON c.id = e.row_id\n"
            f"WHERE {mismatch}\n"
            "ORDER BY e.patch_item;\n\n"
            "SELECT COUNT(*) AS expected_rows,\n"
            f"  SUM(NOT ({mismatch})) AS matched_rows,\n"
            f"  SUM({mismatch}) AS stale_rows\n"
            f"FROM `{temp_table}` e\n"
            f"LEFT JOIN `{table}` c ON c.id = e.row_id;\n\n"
            f"DROP TEMPORARY TABLE `{temp_table}`;\n"
        )
