"""Eligibility and protected-content validation for machine translations."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from .models import ValidationResult

HTML_TAG = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
PRINTF = re.compile(
    r"%(?:\d+\$)?[-+0 #]*(?:\d+|\*)?(?:\.\d+|\.\*)?[hlLzjt]*[diuoxXfFeEgGaAcspn%]"
)
PERCENT_TEMPLATE = re.compile(r"%(?:[A-Za-z_][A-Za-z0-9_.:-]*)")
BRACE_TOKEN = re.compile(r"\{\{\{?[A-Za-z0-9_.:-]+\}?\}\}|\{[A-Za-z0-9_.:-]+\}")
SHORTCODE = re.compile(r"\[/?[A-Za-z][^\]\r\n]*\]")
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+")
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{6,}\d)(?!\w)")
FILE_PATH = re.compile(
    r"(?<!\w)(?:[A-Za-z]:\\[^\s<>]+|/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|"
    r"[A-Za-z0-9_-]+\.(?:php|js|jsx|ts|tsx|css|scss|json|xml|html?|md|txt|pdf|csv|xlsx|zip))(?!\w)"
)
CODE_TOKEN = re.compile(r"`[^`\r\n]+`|```.*?```", re.DOTALL)
CSS_SELECTOR = re.compile(r"(?<!\w)(?:#[A-Za-z_-][\w-]*|\.[A-Za-z_-][\w-]*)(?!\w)")
JSON_KEY = re.compile(r'"(?:[^"\\]|\\.)+"\s*:')
CODE_LIKE = re.compile(
    r"<\?php|</?script\b|\b(?:function|const|let|var)\s+[A-Za-z_$][\w$]*\s*(?:=|\()|"
    r"\b(?:class|interface)\s+[A-Za-z_$][\w$]*|=>|\{\s*[.#]?[A-Za-z_-][\w-]*\s*:",
    re.IGNORECASE,
)
BARE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+/?")
DATEISH = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
)
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
ASCII_WORD_BEFORE = re.compile(r"[A-Za-z]+$")
ASCII_WORD_AFTER = re.compile(r"[A-Za-z]+")
APOSTROPHE_CONTRACTION_SUFFIXES = {"s", "d", "ll", "m", "re", "ve"}
OPENING_QUOTE_BEFORE_WORD = re.compile(
    r"(?:['\"‘“]|&#(?:39|8216);|&#x2018;|&(?:apos|lsquo|quot);)$",
    re.IGNORECASE,
)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _BalanceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() not in VOID_TAGS:
            self.stack.append(tag.casefold())

    def handle_startendtag(self, tag: str, attrs) -> None:
        return None

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if not self.stack:
            self.errors.append(f"unexpected closing tag </{tag}>")
            return
        expected = self.stack.pop()
        if expected != normalized:
            self.errors.append(f"closing tag </{tag}> does not match <{expected}>")

    def finish(self) -> list[str]:
        if self.stack:
            self.errors.append("unclosed tags: " + ", ".join(self.stack))
        return self.errors


def html_balance_errors(value: str) -> list[str]:
    parser = _BalanceParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - report malformed fragments as validation data
        return [f"HTML parser error: {exc}"]
    return parser.finish()


def strip_markup(value: str) -> str:
    return html.unescape(HTML_TAG.sub(" ", value))


def word_count(value: str) -> int:
    return len(WORD.findall(strip_markup(value)))


def _is_standalone_match(pattern: re.Pattern[str], value: str) -> bool:
    return bool(pattern.fullmatch(value.strip()))


def eligibility_reason(
    value: str, protected_names: list[str] | None = None
) -> str | None:
    """Return why a source should not be sent to the model, or None."""
    stripped = strip_markup(value).strip()
    if not stripped:
        return "empty_or_markup_only"
    if _is_standalone_match(URL, stripped):
        return "protected_url"
    if _is_standalone_match(EMAIL, stripped):
        return "protected_email"
    if _is_standalone_match(PHONE, stripped):
        return "protected_phone"
    if _is_standalone_match(FILE_PATH, stripped):
        return "protected_file_or_path"
    if (
        _is_standalone_match(BRACE_TOKEN, stripped)
        or _is_standalone_match(PRINTF, stripped)
        or _is_standalone_match(PERCENT_TEMPLATE, stripped)
    ):
        return "protected_placeholder"
    if _is_standalone_match(SHORTCODE, stripped):
        return "protected_shortcode"
    if BARE_SLUG.fullmatch(stripped):
        return "slug_deferred"
    if DATEISH.search(stripped) and word_count(stripped) <= 5:
        return "protected_date"
    if JSON_KEY.search(stripped) and stripped[:1] in "[{":
        return "protected_code"
    if CODE_LIKE.search(value):
        return "protected_code"
    names = protected_names or []
    if any(stripped == name for name in names):
        return "protected_brand"
    if not WORD.search(stripped):
        return "no_translatable_words"
    return None


def _ordered_matches(pattern: re.Pattern[str], value: str) -> list[str]:
    return [match.group(0) for match in pattern.finditer(value)]


def _is_english_grammatical_apostrophe_entity(value: str, match: re.Match[str]) -> bool:
    """Recognize an encoded apostrophe used by English grammar, not quotation."""
    if html.unescape(match.group(0)) not in {"'", "’"}:
        return False

    before = value[: match.start()]
    after = value[match.end() :]
    stem_match = ASCII_WORD_BEFORE.search(before)
    if not stem_match:
        return False
    stem = stem_match.group(0)
    suffix_match = ASCII_WORD_AFTER.match(after)
    if suffix_match:
        suffix = suffix_match.group(0).casefold()
        if suffix in APOSTROPHE_CONTRACTION_SUFFIXES:
            return True
        return suffix == "t" and stem.casefold().endswith("n")

    # Plural possessives and singular names ending in s place the apostrophe
    # after the word. Require a following word and reject an immediately quoted
    # token so a closing quotation mark cannot be mistaken for possession.
    if stem.casefold().endswith("s") and re.match(r"\s+[A-Za-z]", after):
        prefix = before[: stem_match.start()]
        return not OPENING_QUOTE_BEFORE_WORD.search(prefix)
    return False


def _protected_html_entities(value: str) -> tuple[list[str], list[str]]:
    protected: list[str] = []
    grammatical_apostrophes: list[str] = []
    for match in HTML_ENTITY.finditer(value):
        entity = match.group(0)
        if _is_english_grammatical_apostrophe_entity(value, match):
            grammatical_apostrophes.append(entity)
        else:
            protected.append(entity)
    return protected, grammatical_apostrophes


def _compare_tokens(
    label: str,
    pattern: re.Pattern[str],
    source: str,
    target: str,
    failures: list[str],
) -> None:
    before = _ordered_matches(pattern, source)
    after = _ordered_matches(pattern, target)
    if before != after:
        failures.append(f"{label} changed: expected {before!r}, got {after!r}")


def validate_translation(
    source: str,
    target: str,
    protected_names: list[str] | None = None,
) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []
    if not isinstance(target, str) or not target.strip():
        return ValidationResult("failed", failures=["translation is empty"])

    for label, pattern in (
        ("HTML tags and attributes", HTML_TAG),
        ("printf placeholders", PRINTF),
        ("percent template variables", PERCENT_TEMPLATE),
        ("template variables", BRACE_TOKEN),
        ("WordPress shortcodes", SHORTCODE),
        ("URLs", URL),
        ("email addresses", EMAIL),
        ("phone numbers", PHONE),
        ("file paths or names", FILE_PATH),
        ("code spans", CODE_TOKEN),
        ("CSS selectors", CSS_SELECTOR),
        ("JSON keys", JSON_KEY),
    ):
        _compare_tokens(label, pattern, source, target, failures)

    source_entities, grammatical_apostrophes = _protected_html_entities(source)
    target_entities = _ordered_matches(HTML_ENTITY, target)
    if source_entities != target_entities:
        failures.append(
            "HTML entities changed: "
            f"expected protected entities {source_entities!r}, got {target_entities!r}"
        )
    if grammatical_apostrophes:
        warnings.append(
            "allowed English contraction/possessive apostrophe entity to change grammatically: "
            + ", ".join(grammatical_apostrophes)
        )

    source_leading = source[: len(source) - len(source.lstrip())]
    target_leading = target[: len(target) - len(target.lstrip())]
    source_trailing = source[len(source.rstrip()) :]
    target_trailing = target[len(target.rstrip()) :]
    if source_leading != target_leading:
        failures.append("leading whitespace changed")
    if source_trailing != target_trailing:
        failures.append("trailing whitespace changed")
    if source.count("\n") != target.count("\n"):
        failures.append("newline count changed")

    source_html_errors = html_balance_errors(source)
    target_html_errors = html_balance_errors(target)
    if not source_html_errors and target_html_errors:
        failures.append(
            "translated HTML is malformed: " + "; ".join(target_html_errors)
        )
    elif source_html_errors:
        warnings.append("source HTML is already fragmentary or malformed")

    for brand in protected_names or []:
        if source.count(brand) != target.count(brand):
            failures.append(f"protected name changed: {brand}")

    if source == target and word_count(source) > 1:
        warnings.append("translation is unchanged from source")
    if len(target) > max(80, len(source) * 3):
        warnings.append("translation is unusually long compared with source")

    return ValidationResult(
        "passed" if not failures else "failed",
        warnings=warnings,
        failures=failures,
    )
