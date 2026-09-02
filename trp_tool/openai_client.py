"""Direct OpenAI Responses API translation client."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "none"
DEFAULT_INPUT_PRICE = 0.20
DEFAULT_OUTPUT_PRICE = 1.20


class TranslationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_id: str
    translated_text: str


class TranslationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    translations: list[TranslationItem]


class TranslationError(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens


def build_instructions(
    source_language: str,
    target_language: str,
    context: str,
    glossary: dict[str, str],
    protected_names: list[str],
) -> str:
    lines = [
        f"Translate website copy from {source_language} into natural {target_language}.",
        f"Context: {context}",
        "Use terminology familiar to WordPress privacy and consent users.",
        "Preserve meaning and concise wording. Do not invent functionality or strengthen legal claims.",
        "Preserve every HTML tag and attribute, HTML entity, shortcode, placeholder, template variable, URL, email, phone number, code token, file path, and leading or trailing whitespace exactly.",
        "Return one translation for every row_id and no other rows. Do not rely on input order.",
    ]
    if glossary:
        lines.append(
            "Terminology guidance, applied grammatically rather than by naive replacement: "
            + json.dumps(glossary, ensure_ascii=False, separators=(",", ":"))
        )
    if protected_names:
        lines.append(
            "Never translate or alter these names: " + ", ".join(protected_names)
        )
    return "\n".join(lines)


class OpenAITranslator:
    """Stable-ID translation through the official OpenAI SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        source_language: str,
        target_language: str,
        context: str,
        glossary: dict[str, str] | None = None,
        protected_names: list[str] | None = None,
        timeout: float = 180,
        retries: int = 3,
        client=None,
    ) -> None:
        if not api_key and client is None:
            raise TranslationError("OPENAI_API_KEY is not configured")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise TranslationError(
                    "the official openai package is required; install requirements.txt"
                ) from exc
            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.client = client
        self.instructions = build_instructions(
            source_language,
            target_language,
            context,
            glossary or {},
            protected_names or [],
        )
        self.retries = max(1, retries)
        self.usage = Usage()

    def _usage(self, response) -> Usage:
        usage = getattr(response, "usage", None)
        if not usage:
            return Usage()
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            cached_input_tokens=getattr(input_details, "cached_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
        )

    def translate_batch(
        self,
        items: list[tuple[str, str]],
        correction: str = "",
    ) -> dict[str, str]:
        requested = [row_id for row_id, _ in items]
        if len(requested) != len(set(requested)):
            raise TranslationError("duplicate input row IDs")
        payload = {
            "rows": [
                {"row_id": row_id, "source_text": source_text}
                for row_id, source_text in items
            ]
        }
        if correction:
            payload["correction"] = correction

        last_error = "translation failed"
        for _attempt in range(self.retries):
            try:
                response = self.client.responses.parse(
                    model=DEFAULT_MODEL,
                    reasoning={"effort": DEFAULT_REASONING_EFFORT},
                    instructions=self.instructions,
                    input=json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                    text_format=TranslationBatch,
                    store=False,
                    prompt_cache_key="translatepress-bulk-translate-v1",
                )
                self.usage.add(self._usage(response))
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    raise TranslationError(
                        "OpenAI response did not contain parsed output"
                    )
                pairs = [
                    (item.row_id, item.translated_text) for item in parsed.translations
                ]
                ids = [row_id for row_id, _ in pairs]
                if len(ids) != len(set(ids)):
                    raise TranslationError("OpenAI response contains duplicate row IDs")
                missing = sorted(set(requested) - set(ids))
                unexpected = sorted(set(ids) - set(requested))
                if missing or unexpected:
                    raise TranslationError(
                        f"OpenAI response ID mismatch; missing={missing}, unexpected={unexpected}"
                    )
                result = {row_id: text for row_id, text in pairs}
                empty = [row_id for row_id, text in result.items() if not text.strip()]
                if empty:
                    raise TranslationError(
                        f"OpenAI response has empty translations: {empty}"
                    )
                return result
            except TranslationError as exc:
                last_error = str(exc)
            except Exception as exc:  # noqa: BLE001 - normalize official SDK failures
                # The SDK already performs bounded transport and rate-limit retries.
                # Never switch model or provider after an API failure.
                last_error = f"OpenAI request failed: {type(exc).__name__}: {exc}"
        raise TranslationError(last_error)
