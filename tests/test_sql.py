from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extract_trp_tables import extract_statements
from trp_tool.models import DictRow, TranslationRecord
from trp_tool.sql import (
    DumpSource,
    guarded_rollback_statement,
    guarded_update_statement,
    inspect_tables,
    parse_table_pair,
    split_sql_statements,
    sql_quote,
    write_patch,
    write_rollback,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dictionary.sql"
TABLE = "acme_trp_dictionary_en_us_de_de"


class SQLTests(unittest.TestCase):
    def test_discovers_language_pair_and_status_counts(self):
        source = DumpSource(FIXTURE)
        self.assertEqual(source.list_dictionary_tables(), [TABLE])
        info = inspect_tables(source)[0]
        self.assertEqual(
            (info.prefix, info.source_locale, info.target_locale),
            ("acme_", "en_us", "de_de"),
        )
        self.assertEqual(info.row_count, 18)
        self.assertEqual(info.machine_count, 1)
        self.assertEqual(info.human_count, 1)
        self.assertEqual(info.similar_count, 1)
        self.assertEqual(len(source.dictionary(TABLE)), 18)

    def test_parse_locale_variants(self):
        self.assertEqual(
            parse_table_pair("wp_trp_dictionary_en_us_de_de"), ("wp_", "en_us", "de_de")
        )
        self.assertEqual(
            parse_table_pair("wp_trp_dictionary_ar_en_gb"), ("wp_", "ar", "en_gb")
        )

    def test_sql_escaping_preserves_utf8_quotes_and_newlines(self):
        value = "It's ä ö ü Ä Ö Ü ß\nC:\\path\x00"
        quoted = sql_quote(value)
        self.assertTrue(quoted.startswith("'"))
        self.assertIn("It\\'s", quoted)
        self.assertIn("\\n", quoted)
        self.assertIn("C:\\\\path", quoted)
        self.assertIn("ß", quoted)

    def test_statement_splitter_ignores_semicolons_and_comment_markers_in_strings(self):
        sql = (
            "INSERT INTO `wp_trp_dictionary_en_us_de_de` "
            "(`id`,`original`,`translated`,`status`) VALUES "
            "(1, 'Text; -- still source', '', 0);\n"
            "-- real comment\nSELECT 1;"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("Text; -- still source", statements[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sql"
            path.write_text(sql, encoding="utf-8")
            rows = DumpSource(path).dictionary("wp_trp_dictionary_en_us_de_de")
            self.assertEqual(rows[0].original, "Text; -- still source")

    def test_extract_supports_custom_prefixes(self):
        text = (
            "CREATE TABLE `custom_trp_dictionary_en_us_de_de` (`id` int);"
            "INSERT INTO `custom_trp_dictionary_en_us_de_de` (`id`) VALUES (1);"
            "CREATE TABLE `wp_posts` (`id` int);"
        )
        statements = extract_statements(text)
        self.assertEqual(len(statements), 2)
        self.assertTrue(all("custom_trp_" in statement for statement in statements))

    def test_patch_and_rollback_are_snapshot_guarded(self):
        row = DictRow(7, "It's <strong>safe</strong>", "", 0)
        record = TranslationRecord(
            row=row,
            source_language="English",
            target_language="German",
            translated_text="Es ist <strong>sicher</strong>",
            translation_status="translated",
            validation_status="passed",
        )
        update = guarded_update_statement(TABLE, record)
        rollback = guarded_rollback_statement(TABLE, record)
        for statement in (update, rollback):
            self.assertIn("original <=>", statement)
            self.assertIn("translated <=>", statement)
            self.assertIn("status =", statement)
            self.assertNotRegex(statement, r"(?i)\b(?:DROP|TRUNCATE|DELETE|ALTER)\b")
        with tempfile.TemporaryDirectory() as directory:
            patch = Path(directory) / "patch.sql"
            backup = Path(directory) / "rollback.sql"
            write_patch(patch, TABLE, [record], "gpt-5.6-luna")
            write_rollback(backup, TABLE, [record])
            for path in (patch, backup):
                text = path.read_text(encoding="utf-8")
                self.assertIn("SET NAMES utf8mb4", text)
                self.assertIn("START TRANSACTION", text)
                self.assertIn("COMMIT", text)
                self.assertIn("ROW_COUNT()", text)

    def test_failed_validation_never_enters_patch(self):
        row = DictRow(1, "Hello %s", "", 0)
        record = TranslationRecord(
            row=row,
            source_language="English",
            target_language="German",
            translated_text="Hallo",
            translation_status="failed_validation",
            validation_status="failed",
        )
        with tempfile.TemporaryDirectory() as directory:
            patch = Path(directory) / "patch.sql"
            write_patch(patch, TABLE, [record], "gpt-5.6-luna")
            text = patch.read_text(encoding="utf-8")
            self.assertIn("statements: 0", text)
            self.assertNotIn("\nUPDATE `", text)


if __name__ == "__main__":
    unittest.main()
