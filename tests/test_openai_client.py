from __future__ import annotations

import unittest
from types import SimpleNamespace

from trp_tool.openai_client import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    OpenAITranslator,
    TranslationBatch,
    TranslationError,
    TranslationItem,
)


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(
            output_parsed=output,
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=30,
                input_tokens_details=SimpleNamespace(cached_tokens=20),
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        )


class FakeClient:
    def __init__(self, outputs):
        self.responses = FakeResponses(outputs)


def batch(*pairs):
    return TranslationBatch(
        translations=[
            TranslationItem(row_id=row_id, translated_text=text)
            for row_id, text in pairs
        ]
    )


class OpenAIClientTests(unittest.TestCase):
    def translator(self, outputs, retries=3):
        return OpenAITranslator(
            api_key="",
            source_language="English",
            target_language="German",
            context="SureCookie",
            protected_names=["SureCookie"],
            retries=retries,
            client=FakeClient(outputs),
        )

    def test_uses_exact_model_reasoning_and_stable_ids(self):
        translator = self.translator([batch(("2", "Zwei"), ("1", "Eins"))])
        result = translator.translate_batch([("1", "One"), ("2", "Two")])
        self.assertEqual(result, {"2": "Zwei", "1": "Eins"})
        call = translator.client.responses.calls[0]
        self.assertEqual(call["model"], DEFAULT_MODEL)
        self.assertEqual(call["reasoning"], {"effort": DEFAULT_REASONING_EFFORT})
        self.assertIs(call["text_format"], TranslationBatch)
        self.assertFalse(call["store"])
        self.assertEqual(translator.usage.input_tokens, 100)
        self.assertEqual(translator.usage.cached_input_tokens, 20)

    def test_retries_missing_unexpected_duplicate_empty_and_malformed(self):
        invalid = [
            batch(("1", "Eins")),
            batch(("1", "Eins"), ("2", "Zwei"), ("3", "Drei")),
            batch(("1", "Eins"), ("1", "Noch eins"), ("2", "Zwei")),
            batch(("1", ""), ("2", "Zwei")),
            None,
        ]
        for output in invalid:
            with self.subTest(output=output):
                translator = self.translator([output], retries=2)
                with self.assertRaises(TranslationError):
                    translator.translate_batch([("1", "One"), ("2", "Two")])
                self.assertEqual(len(translator.client.responses.calls), 2)

    def test_transport_failure_never_falls_back(self):
        translator = self.translator([RuntimeError("rate limited")], retries=2)
        with self.assertRaisesRegex(TranslationError, "OpenAI request failed"):
            translator.translate_batch([("1", "One")])
        self.assertTrue(
            all(
                call["model"] == "gpt-5.6-luna"
                for call in translator.client.responses.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
