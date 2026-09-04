from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from trp_tool.spreadsheet import export_dictionary, load_sheet, match_sheet
from trp_tool.sql import DumpSource

FIXTURE = Path(__file__).parent / "fixtures" / "dictionary.sql"
TABLE = "acme_trp_dictionary_en_us_de_de"


class SpreadsheetTests(unittest.TestCase):
    def setUp(self):
        self.rows = DumpSource(FIXTURE).dictionary(TABLE)

    def test_csv_and_xlsx_round_trip_exact_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("csv", "xlsx"):
                with self.subTest(extension=extension):
                    path = Path(directory) / f"translations.{extension}"
                    export_dictionary(path, self.rows[:4], "English", "German")
                    loaded = load_sheet(path)
                    self.assertEqual(loaded[0].row_id, 1)
                    self.assertEqual(loaded[0].source_text, self.rows[0].original)

    def test_configurable_columns_and_existing_translation_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Source copy", "German copy", "dictionary_id"])
                writer.writerow(["Get Started", "Jetzt starten", 1])
                writer.writerow(["Privacy Policy", "Neue Fassung", 2])
            loaded = load_sheet(
                path, source_column="Source copy", target_column="German copy"
            )
            matches = match_sheet(loaded, self.rows)
            self.assertEqual(matches[0].outcome, "translated")
            self.assertEqual(matches[1].outcome, "already_translated")

    def test_stale_source_hash_key_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stale.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["row_id", "source_text", "target_text"])
                writer.writerow([1, "Changed source", "Geändert"])
            match = match_sheet(load_sheet(path), self.rows)[0]
            self.assertEqual(match.outcome, "stale_source")


if __name__ == "__main__":
    unittest.main()
