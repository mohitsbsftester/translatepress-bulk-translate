"""Command-line interface for safe TranslatePress bulk translation."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

from .models import (
    BLOCK_TYPE_REGULAR_STRING,
    HUMAN_REVIEWED,
    MACHINE_TRANSLATED,
    DictRow,
    TranslationRecord,
)
from .openai_client import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_PRICE,
    DEFAULT_REASONING_EFFORT,
    OpenAITranslator,
    TranslationError,
    Usage,
    build_instructions,
    output_token_limit,
)
from .reports import write_review
from .spreadsheet import export_dictionary, load_sheet, match_sheet
from .sql import (
    DumpSource,
    MySQLSource,
    Source,
    inspect_tables,
    select_table,
    validate_identifier,
    write_patch,
    write_rollback,
)
from .validation import (
    BRACE_TOKEN,
    HTML_ENTITY,
    HTML_TAG,
    PERCENT_TEMPLATE,
    PRINTF,
    SHORTCODE,
    eligibility_reason,
    validate_translation,
    word_count,
)

DEFAULT_CACHED_INPUT_PRICE = 0.02
DEFAULT_CONTEXT = (
    "SureCookie, a WordPress cookie consent and privacy compliance product for "
    "European website owners"
)
DEFAULT_PROTECTED_NAMES = [
    "SureCookie",
    "WordPress",
    "Google Consent Mode",
    "Google Analytics",
    "Google Ads",
    "Google Tag Manager",
    "CMP",
]


def use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def read_wp_config(path: str) -> dict[str, object]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    config: dict[str, object] = {}
    for key, constant in (
        ("name", "DB_NAME"),
        ("user", "DB_USER"),
        ("password", "DB_PASSWORD"),
        ("host", "DB_HOST"),
    ):
        match = re.search(
            rf"define\(\s*['\"]{constant}['\"]\s*,\s*['\"](.*?)['\"]\s*\)", text
        )
        if match:
            config[key] = match.group(1)
    host = str(config.get("host", "localhost"))
    if ":" in host:
        host_name, _, port = host.partition(":")
        config["host"] = host_name
        if port.isdigit():
            config["port"] = int(port)
    return config


def open_source(args) -> Source:
    if args.dump:
        return DumpSource(args.dump)
    config: dict[str, object] = read_wp_config(args.wp_config) if args.wp_config else {}
    user = args.db_user or config.get("user")
    password = args.db_pass if args.db_pass is not None else config.get("password")
    database = args.db_name or config.get("name")
    host = args.db_host or config.get("host") or "localhost"
    port = args.db_port or config.get("port") or 3306
    if not user or not database:
        raise ValueError(
            "no source configured; use --dump FILE.sql or database connection options"
        )
    return MySQLSource(
        str(host), str(user), str(password or ""), str(database), int(port)
    )


def load_json_object(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{path} must contain a JSON object of string keys and values")
    return value


def load_protected_names(path: str | None) -> list[str]:
    if not path:
        return list(DEFAULT_PROTECTED_NAMES)
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{path} must contain a JSON array of strings")
    return value


def resolve_table(source: Source, args):
    return select_table(
        source,
        table=args.table,
        source_locale=args.source_locale,
        target_locale=args.target_locale,
    )


def eligible_rows(
    rows: list[DictRow], protected_names: list[str], retranslate_machine: bool = False
) -> tuple[list[DictRow], Counter[str]]:
    candidates: list[DictRow] = []
    skipped: Counter[str] = Counter()
    for row in rows:
        if row.block_type != BLOCK_TYPE_REGULAR_STRING:
            skipped["non_regular_block"] += 1
            continue
        if row.has_translation and not (
            retranslate_machine and row.status == MACHINE_TRANSLATED
        ):
            skipped[
                "human_reviewed"
                if row.status == HUMAN_REVIEWED
                else "already_translated"
            ] += 1
            continue
        reason = eligibility_reason(row.original, protected_names)
        if reason:
            skipped[reason] += 1
            continue
        candidates.append(row)
    return candidates, skipped


def representative_sample(rows: list[DictRow], limit: int | None) -> list[DictRow]:
    if not limit or limit >= len(rows):
        return list(rows)
    selected: list[DictRow] = []
    seen: set[int] = set()

    def add(row: DictRow | None) -> None:
        if row and row.id not in seen and len(selected) < limit:
            selected.append(row)
            seen.add(row.id)

    add(next((row for row in rows if HTML_TAG.search(row.original)), None))
    add(
        next(
            (
                row
                for row in rows
                if PRINTF.search(row.original)
                or PERCENT_TEMPLATE.search(row.original)
                or BRACE_TOKEN.search(row.original)
                or SHORTCODE.search(row.original)
            ),
            None,
        )
    )
    add(
        next(
            (
                row
                for row in rows
                if re.search(
                    r"\b(?:GDPR|privacy|consent)\b", row.original, re.IGNORECASE
                )
            ),
            None,
        )
    )
    add(
        next(
            (
                row
                for row in rows
                if re.search(
                    r"\b(?:get|start|try|download|learn|see)\b",
                    row.original,
                    re.IGNORECASE,
                )
                and word_count(row.original) <= 8
            ),
            None,
        )
    )
    add(next((row for row in rows if HTML_ENTITY.search(row.original)), None))
    add(next((row for row in rows if row.original.rstrip().endswith("?")), None))
    add(min(rows, key=lambda row: (word_count(row.original), row.id), default=None))
    add(max(rows, key=lambda row: (word_count(row.original), -row.id), default=None))
    add(
        next(
            (
                row
                for row in rows
                if 18 <= word_count(row.original) <= 35
                and not row.original.rstrip().endswith("?")
            ),
            None,
        )
    )
    add(
        next(
            (
                row
                for row in rows
                if word_count(row.original) >= 35
                and re.search(
                    r"\b(?:article|guide|step|settings|dashboard)\b",
                    row.original,
                    re.IGNORECASE,
                )
            ),
            None,
        )
    )
    # Fill remaining slots from evenly spaced positions so a sample is not
    # dominated by adjacent navigation strings at the start of the dump.
    stride = max(1, len(rows) // max(1, limit - len(selected)))
    for index in range(0, len(rows), stride):
        add(rows[index])
    for row in rows:
        add(row)
    return selected


def estimate_tokens(
    rows: list[DictRow], batch_size: int, instructions: str
) -> tuple[int, int, int]:
    if not rows:
        return 0, 0, 0
    batches = math.ceil(len(rows) / batch_size)
    input_chars = 0
    for start in range(0, len(rows), batch_size):
        payload = {
            "rows": [
                {"row_id": str(row.id), "source_text": row.original}
                for row in rows[start : start + batch_size]
            ]
        }
        input_chars += len(instructions) + len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    input_tokens = math.ceil(input_chars / 4)
    output_tokens = math.ceil(
        sum(len(row.original) for row in rows) / 3.2 + len(rows) * 12
    )
    return input_tokens, output_tokens, batches


def estimated_cost(
    input_tokens: int, output_tokens: int, input_price: float, output_price: float
) -> float:
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def actual_cost(
    usage: Usage, input_price: float, cached_input_price: float, output_price: float
) -> float:
    uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
    return (
        uncached * input_price
        + usage.cached_input_tokens * cached_input_price
        + usage.output_tokens * output_price
    ) / 1_000_000


def print_estimate(
    *,
    rows: list[DictRow],
    skipped: Counter[str],
    source_language: str,
    target_language: str,
    input_tokens: int,
    output_tokens: int,
    batches: int,
    estimate: float,
    max_cost: float,
) -> None:
    print("Provider: OpenAI API")
    print(f"Model: {DEFAULT_MODEL}")
    print(f"Reasoning: {DEFAULT_REASONING_EFFORT}")
    print(f"Source: {source_language}")
    print(f"Target: {target_language}")
    print(f"Eligible strings: {len(rows):,}")
    print(f"Source words: {sum(word_count(row.original) for row in rows):,}")
    print(f"Characters: {sum(len(row.original) for row in rows):,}")
    print(f"Estimated input tokens: {input_tokens:,}")
    print(f"Estimated output tokens: {output_tokens:,}")
    print(f"Number of batches: {batches:,}")
    print(f"Estimated API cost: ${estimate:.4f}")
    print(f"Maximum approved cost: ${max_cost:.2f}")
    if skipped:
        print("Skipped strings:")
        for reason, count in sorted(skipped.items()):
            print(f"  {reason}: {count:,}")


def cmd_inspect(args) -> int:
    source = open_source(args)
    try:
        tables = inspect_tables(source)
        if not tables:
            raise ValueError(
                "no regular TranslatePress dictionary tables were discovered"
            )
        print(f"Discovered {len(tables)} regular TranslatePress dictionary table(s):")
        protected_names = load_protected_names(args.protected_names)
        for table in tables:
            rows = source.dictionary(table.name)
            eligible, skipped = eligible_rows(rows, protected_names)
            print(f"\nTable: {table.name}")
            print(f"WordPress prefix: {table.prefix}")
            print(f"Source locale: {table.source_locale}")
            print(f"Target locale: {table.target_locale}")
            print(f"Total rows: {table.row_count:,}")
            print(f"Untranslated rows: {table.untranslated_count:,}")
            print(f"Machine-translated rows: {table.machine_count:,}")
            print(f"Human-reviewed rows: {table.human_count:,}")
            print(f"Similar-translated rows: {table.similar_count:,}")
            print(f"Other translated rows: {table.other_translated_count:,}")
            print(f"Eligible regular strings: {len(eligible):,}")
            print(
                f"Eligible source words: {sum(word_count(row.original) for row in eligible):,}"
            )
            print(
                f"Eligible characters: {sum(len(row.original) for row in eligible):,}"
            )
            if skipped:
                print("Skipped:")
                for reason, count in sorted(skipped.items()):
                    print(f"  {reason}: {count:,}")
        return 0
    finally:
        source.close()


def cmd_export(args) -> int:
    source = open_source(args)
    try:
        table = resolve_table(source, args)
        rows = source.dictionary(table.name)
        if not args.all:
            rows = [row for row in rows if not row.has_translation]
        export_dictionary(args.out, rows, args.source_language, args.target_language)
        print(f"Wrote {len(rows):,} row(s) from `{table.name}` to {args.out}")
        return 0
    finally:
        source.close()


def _record_from_match(
    match, args, protected_names: list[str]
) -> TranslationRecord | None:
    if not match.target:
        return None
    record = TranslationRecord(
        row=match.target,
        source_language=args.source_language,
        target_language=args.target_language,
        translated_text=match.sheet_row.target_text,
        new_status=args.status,
        translation_status=match.outcome,
        validation_status="not_run",
    )
    if match.note:
        record.warnings.append(match.note)
    if match.outcome == "translated":
        validation = validate_translation(
            match.target.original, match.sheet_row.target_text, protected_names
        )
        record.validation_status = validation.status
        record.warnings.extend(validation.warnings)
        if validation.failures:
            record.translation_status = "failed_validation"
            record.failure_reason = "; ".join(validation.failures)
    return record


def apply_records(
    source: Source, table: str, records: list[TranslationRecord]
) -> tuple[int, int]:
    if not isinstance(source, MySQLSource):
        raise TypeError("--apply requires a live database source, not --dump")
    validate_identifier(table)
    applied = 0
    stale = 0
    with source.conn.cursor() as cursor:
        for record in records:
            if (
                record.translation_status != "translated"
                or record.validation_status != "passed"
            ):
                continue
            row = record.row
            cursor.execute(
                f"UPDATE `{table}` SET translated=%s, status=%s WHERE id=%s AND original <=> %s AND translated <=> %s AND status=%s",
                (
                    record.translated_text,
                    record.new_status,
                    row.id,
                    row.original,
                    row.translated,
                    row.status,
                ),
            )
            if cursor.rowcount == 1:
                applied += 1
            else:
                stale += 1
    source.conn.commit()
    return applied, stale


def cmd_import(args) -> int:
    source = open_source(args)
    try:
        table = resolve_table(source, args)
        dictionary = source.dictionary(table.name)
        sheet_rows = load_sheet(
            args.excel,
            sheet_name=args.sheet,
            source_column=args.source_col,
            target_column=args.target_col,
        )
        matches = match_sheet(
            sheet_rows,
            dictionary,
            max_tier=args.max_tier,
            skip_translated=not args.overwrite_existing,
        )
        protected_names = load_protected_names(args.protected_names)
        records = [
            record
            for match in matches
            if (record := _record_from_match(match, args, protected_names)) is not None
        ]
        counts = Counter(match.outcome for match in matches)
        print(f"Table: {table.name}")
        print(f"Spreadsheet rows: {len(sheet_rows):,}")
        for outcome, count in sorted(counts.items()):
            print(f"  {outcome}: {count:,}")
        if args.report:
            write_review(args.report, records)
            print(f"Review report: {args.report}")
        valid = [
            record
            for record in records
            if record.translation_status == "translated"
            and record.validation_status == "passed"
        ]
        if args.sql_out:
            if not args.backup:
                raise ValueError("--sql-out requires --backup")
            write_patch(args.sql_out, table.name, valid, "human_spreadsheet_import")
            write_rollback(args.backup, table.name, valid)
            print(f"Patch: {args.sql_out}")
            print(f"Rollback: {args.backup}")
        elif args.apply:
            if not args.backup:
                raise ValueError("--apply requires --backup")
            write_rollback(args.backup, table.name, valid)
            applied, stale = apply_records(source, table.name, valid)
            print(f"Rollback: {args.backup}")
            print(f"Applied: {applied:,}; stale/conflicted: {stale:,}")
        elif args.backup:
            raise ValueError("--backup requires --sql-out or --apply")
        else:
            print(
                f"DRY RUN: {len(valid):,} row(s) would be updated. Nothing was written."
            )
        return 0
    finally:
        source.close()


def cmd_translate(args) -> int:
    source = open_source(args)
    try:
        table = resolve_table(source, args)
        dictionary = source.dictionary(table.name)
        protected_names = load_protected_names(args.protected_names)
        glossary = load_json_object(args.glossary)
        candidates, skipped = eligible_rows(
            dictionary, protected_names, retranslate_machine=args.retranslate_machine
        )
        selected = representative_sample(candidates, args.limit)
        instructions = build_instructions(
            args.source_language,
            args.target_language,
            args.context,
            glossary,
            protected_names,
        )
        input_tokens, output_tokens, batches = estimate_tokens(
            selected, args.batch_size, instructions
        )
        estimate = estimated_cost(
            input_tokens, output_tokens, args.price_input, args.price_output
        )
        print(f"Table: {table.name}")
        print(f"Total rows: {len(dictionary):,}")
        print(
            f"Untranslated rows: {sum(not row.has_translation for row in dictionary):,}"
        )
        print_estimate(
            rows=selected,
            skipped=skipped,
            source_language=args.source_language,
            target_language=args.target_language,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            batches=batches,
            estimate=estimate,
            max_cost=args.max_cost,
        )
        if not selected:
            print("Nothing eligible to translate.")
            return 0
        if estimate > args.max_cost:
            raise ValueError(
                f"estimated ${estimate:.4f} exceeds --max-cost ${args.max_cost:.2f}; model={DEFAULT_MODEL}, estimated_tokens={input_tokens + output_tokens:,}"
            )
        if args.estimate_only or not args.execute:
            print("DRY RUN: no API request was made and no files were written.")
            print("Use --execute for paid API requests after reviewing this estimate.")
            return 0
        if args.limit is None and not args.approve_full:
            raise ValueError(
                "an unlimited translation run requires --approve-full after the sample is reviewed"
            )
        if args.sql_out and not args.backup:
            raise ValueError("--sql-out requires --backup")
        if args.apply and not args.backup:
            raise ValueError("--apply requires --backup")
        if args.backup and not (args.sql_out or args.apply):
            raise ValueError("--backup requires --sql-out or --apply")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                'OPENAI_API_KEY is not configured. In this terminal run: export OPENAI_API_KEY="..."'
            )

        translator = OpenAITranslator(
            api_key=api_key,
            source_language=args.source_language,
            target_language=args.target_language,
            context=args.context,
            glossary=glossary,
            protected_names=protected_names,
            timeout=args.timeout,
            retries=args.retries,
        )
        records: list[TranslationRecord] = []
        for start in range(0, len(selected), args.batch_size):
            chunk = selected[start : start + args.batch_size]
            batch_number = start // args.batch_size + 1
            batch_input, _batch_output, _ = estimate_tokens(
                chunk, args.batch_size, instructions
            )
            conservative_next = estimated_cost(
                batch_input * args.retries,
                output_token_limit([(str(row.id), row.original) for row in chunk])
                * args.retries,
                args.price_input,
                args.price_output,
            )
            spent = actual_cost(
                translator.usage,
                args.price_input,
                args.price_cached_input,
                args.price_output,
            )
            if spent + conservative_next > args.max_cost:
                for row in chunk:
                    records.append(
                        TranslationRecord(
                            row=row,
                            source_language=args.source_language,
                            target_language=args.target_language,
                            model=DEFAULT_MODEL,
                            reasoning_effort=DEFAULT_REASONING_EFFORT,
                            translation_status="api_failure",
                            validation_status="not_run",
                            failure_reason="maximum approved cost would be exceeded by retries",
                        )
                    )
                print(f"Batch {batch_number}/{batches}: stopped by hard cost guard")
                break
            try:
                translated = translator.translate_batch(
                    [(str(row.id), row.original) for row in chunk]
                )
            except TranslationError as exc:
                for row in chunk:
                    records.append(
                        TranslationRecord(
                            row=row,
                            source_language=args.source_language,
                            target_language=args.target_language,
                            model=DEFAULT_MODEL,
                            reasoning_effort=DEFAULT_REASONING_EFFORT,
                            translation_status="api_failure",
                            validation_status="not_run",
                            failure_reason=str(exc),
                        )
                    )
                print(f"Batch {batch_number}/{batches}: failed, no model fallback used")
                continue

            valid_count = 0
            for row in chunk:
                target = translated[str(row.id)]
                validation = validate_translation(row.original, target, protected_names)
                if not validation.valid and args.validation_retry:
                    correction = (
                        "The previous translation failed protected-content validation. Correct every listed issue while translating the human-readable text: "
                        + "; ".join(validation.failures)
                    )
                    retry_input, _retry_output, _ = estimate_tokens(
                        [row], 1, instructions + correction
                    )
                    retry_cost = estimated_cost(
                        retry_input * args.retries,
                        output_token_limit([(str(row.id), row.original)])
                        * args.retries,
                        args.price_input,
                        args.price_output,
                    )
                    spent = actual_cost(
                        translator.usage,
                        args.price_input,
                        args.price_cached_input,
                        args.price_output,
                    )
                    if spent + retry_cost <= args.max_cost:
                        try:
                            target = translator.translate_batch(
                                [(str(row.id), row.original)], correction=correction
                            )[str(row.id)]
                            validation = validate_translation(
                                row.original, target, protected_names
                            )
                        except TranslationError as exc:
                            validation.failures.append(str(exc))
                record = TranslationRecord(
                    row=row,
                    source_language=args.source_language,
                    target_language=args.target_language,
                    translated_text=target,
                    new_status=MACHINE_TRANSLATED,
                    model=DEFAULT_MODEL,
                    reasoning_effort=DEFAULT_REASONING_EFFORT,
                    warnings=validation.warnings,
                )
                if validation.valid:
                    record.translation_status = "translated"
                    record.validation_status = "passed"
                    valid_count += 1
                else:
                    record.translation_status = "failed_validation"
                    record.validation_status = "failed"
                    record.failure_reason = "; ".join(validation.failures)
                records.append(record)
            print(
                f"Batch {batch_number}/{batches}: {valid_count}/{len(chunk)} passed validation"
            )

        spent = actual_cost(
            translator.usage,
            args.price_input,
            args.price_cached_input,
            args.price_output,
        )
        print(f"Actual input tokens: {translator.usage.input_tokens:,}")
        print(f"Actual cached input tokens: {translator.usage.cached_input_tokens:,}")
        print(f"Actual output tokens: {translator.usage.output_tokens:,}")
        print(f"Actual reasoning tokens: {translator.usage.reasoning_tokens:,}")
        print(f"Actual API cost: ${spent:.4f}")
        valid = [
            record
            for record in records
            if record.translation_status == "translated"
            and record.validation_status == "passed"
        ]
        if args.report:
            write_review(args.report, records)
            print(f"Review report: {args.report}")
        if args.sql_out:
            write_patch(args.sql_out, table.name, valid, DEFAULT_MODEL)
            write_rollback(args.backup, table.name, valid)
            print(f"Patch: {args.sql_out}")
            print(f"Rollback: {args.backup}")
        elif args.apply:
            write_rollback(args.backup, table.name, valid)
            applied, stale = apply_records(source, table.name, valid)
            print(f"Rollback: {args.backup}")
            print(f"Applied: {applied:,}; stale/conflicted: {stale:,}")
        else:
            print(
                "No database or SQL output was requested. Translations exist only in the report."
            )
        failures = len(records) - len(valid)
        print(f"Validated translations: {len(valid):,}; failures: {failures:,}")
        return 1 if failures else 0
    finally:
        source.close()


def add_source_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("source")
    group.add_argument("--dump", help="offline SQL dump")
    group.add_argument(
        "--wp-config", help="wp-config.php for live database credentials"
    )
    group.add_argument("--db-host")
    group.add_argument("--db-port", type=int)
    group.add_argument("--db-name")
    group.add_argument("--db-user")
    group.add_argument("--db-pass")
    group = parser.add_argument_group("dictionary table")
    group.add_argument("--table", help="exact discovered dictionary table")
    group.add_argument("--source-locale", help="source locale used to select a table")
    group.add_argument("--target-locale", help="target locale used to select a table")


def add_language_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-language", default="English")
    parser.add_argument("--target-language", default="German")
    parser.add_argument(
        "--protected-names", help="JSON array of names that must not change"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trp_translate.py",
        description="Safe offline-first TranslatePress bulk translation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspector = subparsers.add_parser(
        "inspect", help="discover and summarize dictionary tables"
    )
    add_source_args(inspector)
    inspector.add_argument("--protected-names", help="JSON array of protected names")
    inspector.set_defaults(func=cmd_inspect)
    exporter = subparsers.add_parser(
        "export", help="export exact source strings to CSV/XLSX"
    )
    add_source_args(exporter)
    add_language_args(exporter)
    exporter.add_argument("--out", default="translations.xlsx")
    exporter.add_argument(
        "--all", action="store_true", help="include existing translations"
    )
    exporter.set_defaults(func=cmd_export)
    importer = subparsers.add_parser(
        "import", help="validate and import a translated sheet"
    )
    add_source_args(importer)
    add_language_args(importer)
    importer.add_argument("--excel", required=True)
    importer.add_argument("--sheet")
    importer.add_argument("--source-col")
    importer.add_argument("--target-col")
    importer.add_argument("--max-tier", type=int, default=2, choices=range(4))
    importer.add_argument("--overwrite-existing", action="store_true")
    importer.add_argument(
        "--status",
        type=int,
        default=HUMAN_REVIEWED,
        choices=(MACHINE_TRANSLATED, HUMAN_REVIEWED),
    )
    importer.add_argument("--report")
    importer.add_argument("--sql-out")
    importer.add_argument("--backup")
    importer.add_argument("--apply", action="store_true")
    importer.set_defaults(func=cmd_import)
    translator = subparsers.add_parser(
        "translate", help=f"translate through OpenAI {DEFAULT_MODEL}"
    )
    add_source_args(translator)
    add_language_args(translator)
    translator.add_argument("--context", default=DEFAULT_CONTEXT)
    translator.add_argument(
        "--glossary", help="JSON source-to-target terminology guidance"
    )
    translator.add_argument("--batch-size", type=int, default=20)
    translator.add_argument("--limit", type=int, help="representative sample size")
    translator.add_argument("--retranslate-machine", action="store_true")
    translator.add_argument("--price-input", type=float, default=DEFAULT_INPUT_PRICE)
    translator.add_argument(
        "--price-cached-input", type=float, default=DEFAULT_CACHED_INPUT_PRICE
    )
    translator.add_argument("--price-output", type=float, default=DEFAULT_OUTPUT_PRICE)
    translator.add_argument("--max-cost", type=float, default=5.0)
    translator.add_argument("--estimate-only", action="store_true")
    translator.add_argument(
        "--execute", action="store_true", help="authorize paid API requests"
    )
    translator.add_argument(
        "--approve-full",
        action="store_true",
        help="approve an unlimited run after the sample has been reviewed",
    )
    translator.add_argument("--timeout", type=float, default=180)
    translator.add_argument("--retries", type=int, default=3)
    translator.add_argument(
        "--no-validation-retry", action="store_false", dest="validation_retry"
    )
    translator.add_argument("--report")
    translator.add_argument("--sql-out")
    translator.add_argument("--backup")
    translator.add_argument("--apply", action="store_true")
    translator.set_defaults(func=cmd_translate, validation_retry=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dump and any(
        getattr(args, name, None)
        for name in ("wp_config", "db_host", "db_name", "db_user", "db_pass")
    ):
        parser.error("--dump cannot be combined with live database options")
    try:
        return args.func(args)
    except (ValueError, TypeError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
