"""Shared data structures and TranslatePress constants."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

NOT_TRANSLATED = 0
MACHINE_TRANSLATED = 1
HUMAN_REVIEWED = 2
SIMILAR_TRANSLATED = 3
BLOCK_TYPE_REGULAR_STRING = 0

STATUS_NAMES = {
    NOT_TRANSLATED: "not_translated",
    MACHINE_TRANSLATED: "machine_translated",
    HUMAN_REVIEWED: "human_reviewed",
    SIMILAR_TRANSLATED: "similar_translated",
}


@dataclass(frozen=True)
class DictRow:
    """One row from a TranslatePress dictionary table."""

    id: int
    original: str
    translated: str | None
    status: int
    block_type: int = BLOCK_TYPE_REGULAR_STRING
    original_id: int | None = None

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.original.encode("utf-8")).hexdigest()

    @property
    def has_translation(self) -> bool:
        return bool((self.translated or "").strip())


@dataclass(frozen=True)
class TableInfo:
    """A discovered TranslatePress dictionary table and its language pair."""

    name: str
    prefix: str
    source_locale: str
    target_locale: str
    row_count: int = 0
    untranslated_count: int = 0
    machine_count: int = 0
    human_count: int = 0
    similar_count: int = 0
    other_translated_count: int = 0


@dataclass
class ValidationResult:
    status: str
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.failures


@dataclass
class TranslationRecord:
    """One review-report row and potential SQL update."""

    row: DictRow
    source_language: str
    target_language: str
    translated_text: str = ""
    new_status: int = MACHINE_TRANSLATED
    translation_status: str = "skipped"
    validation_status: str = "not_run"
    warnings: list[str] = field(default_factory=list)
    failure_reason: str = ""
    model: str = ""
    reasoning_effort: str = ""

    def as_report_row(self) -> dict[str, object]:
        return {
            "row_id": self.row.id,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "source_text": self.row.original,
            "translated_text": self.translated_text,
            "previous_translation": self.row.translated or "",
            "previous_status": STATUS_NAMES.get(self.row.status, self.row.status),
            "new_status": STATUS_NAMES.get(self.new_status, self.new_status),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "translation_status": self.translation_status,
            "validation_status": self.validation_status,
            "warning": "; ".join(self.warnings),
            "failure_reason": self.failure_reason,
            "source_hash": self.row.source_hash,
        }


REPORT_FIELDS = [
    "row_id",
    "source_language",
    "target_language",
    "source_text",
    "translated_text",
    "previous_translation",
    "previous_status",
    "new_status",
    "model",
    "reasoning_effort",
    "translation_status",
    "validation_status",
    "warning",
    "failure_reason",
    "source_hash",
]
