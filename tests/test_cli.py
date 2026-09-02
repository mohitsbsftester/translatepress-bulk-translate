from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trp_tool.cli import load_openai_api_key, main
from trp_tool.openai_client import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "dictionary.sql"


class FakeTranslator:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeTranslator.last_kwargs = kwargs
        self.usage = Usage()

    def translate_batch(self, items, correction=""):
        self.usage.input_tokens += 120
        self.usage.output_tokens += 50
        return {row_id: source + " übersetzt" for row_id, source in items}


class CLITests(unittest.TestCase):
    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_inspect_discovers_real_prefix_and_locales(self):
        code, output, error = self.run_cli("inspect", "--dump", str(FIXTURE))
        self.assertEqual(code, 0, error)
        self.assertIn("Table: acme_trp_dictionary_en_us_de_de", output)
        self.assertIn("WordPress prefix: acme_", output)
        self.assertIn("Source locale: en_us", output)
        self.assertIn("Target locale: de_de", output)
        self.assertIn("Human-reviewed rows: 1", output)

    def test_local_dotenv_loads_without_overriding_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OPENAI_API_KEY=local-test-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_openai_api_key(env_file), "local-test-key")
            with patch.dict(
                os.environ, {"OPENAI_API_KEY": "shell-test-key"}, clear=True
            ):
                self.assertEqual(load_openai_api_key(env_file), "shell-test-key")

    def test_translate_is_dry_run_and_reports_required_cost_fields(self):
        code, output, error = self.run_cli(
            "translate", "--dump", str(FIXTURE), "--limit", "10", "--max-cost", "1"
        )
        self.assertEqual(code, 0, error)
        for label in (
            "Provider: OpenAI API",
            "Model: gpt-5.6-luna",
            "Reasoning: none",
            "Source: English",
            "Target: German",
            "Eligible strings:",
            "Source words:",
            "Characters:",
            "Estimated input tokens:",
            "Estimated output tokens:",
            "Number of batches:",
            "Estimated API cost:",
            "Maximum approved cost:",
        ):
            self.assertIn(label, output)
        self.assertIn("no API request was made", output)

    def test_max_cost_stops_before_api_or_key_check(self):
        code, _output, error = self.run_cli(
            "translate",
            "--dump",
            str(FIXTURE),
            "--execute",
            "--limit",
            "2",
            "--max-cost",
            "0",
        )
        self.assertEqual(code, 2)
        self.assertIn("exceeds --max-cost", error)
        self.assertNotIn("OPENAI_API_KEY", error)

    def test_full_run_requires_sample_approval(self):
        code, _output, error = self.run_cli(
            "translate", "--dump", str(FIXTURE), "--execute", "--max-cost", "5"
        )
        self.assertEqual(code, 2)
        self.assertIn("requires --approve-full", error)

    def test_sample_creates_review_patch_and_rollback_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "sample.xlsx"
            sql_out = Path(directory) / "patch.sql"
            backup = Path(directory) / "rollback.sql"
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "secret-test-key"}),
                patch("trp_tool.cli.OpenAITranslator", FakeTranslator),
            ):
                code, output, error = self.run_cli(
                    "translate",
                    "--dump",
                    str(FIXTURE),
                    "--execute",
                    "--limit",
                    "8",
                    "--report",
                    str(report),
                    "--sql-out",
                    str(sql_out),
                    "--backup",
                    str(backup),
                )
            self.assertEqual(code, 0, error)
            self.assertTrue(report.exists())
            self.assertTrue(sql_out.exists())
            self.assertTrue(backup.exists())
            combined = (
                output + error + report.name + sql_out.read_text(encoding="utf-8")
            )
            self.assertNotIn("secret-test-key", combined)
            self.assertNotIn("status = 2", sql_out.read_text(encoding="utf-8"))
            self.assertIn("gpt-5.6-luna", sql_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
