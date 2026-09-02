# Decisions

## Repository and contribution target

The upstream repository exposes only `main`. To comply with the required pull request base policy, development is taking place in a maintained fork with `staging` as the base and `codex-mohit/surecookie-openai-translation` as the feature branch. Issue 1 tracks the work and PR 2 contains the implementation.

## Preserve the existing workflow where it is sound

The existing SQL literal parser, spreadsheet round-trip, tiered matching, dry-run behavior, optional live MySQL source, transaction wrappers, utf8mb4 setup, and TranslatePress status constants are useful foundations. The implementation will refactor language-specific naming and unsafe write details instead of discarding these working paths.

The large single script was separated into focused modules for SQL, spreadsheets, OpenAI, validation, reports, shared models, and CLI orchestration. This makes the safety boundaries independently testable while preserving the command-line entry point.

## Official OpenAI API defaults

Translation will use the official OpenAI Python SDK and Responses API. The production defaults will be the exact model ID `gpt-5.6-luna` and reasoning effort `none`, with no provider or model fallback. Current default price estimates use the official standard rates of $0.20 per million input tokens and $1.20 per million output tokens, while command-line overrides remain available for future price changes.

The implementation is verified against OpenAI Python SDK 3.7.0, the current release observed during development. The dependency will use a compatible lower bound instead of an exact pin so security and maintenance releases remain installable.

## Structured output shape

Strict Structured Outputs will return a list of objects containing `row_id` and `translated_text`, wrapped in a batch object. A JSON object with dynamic row IDs would express arbitrary keys through `additionalProperties`, which does not give the same closed-schema guarantees. The list schema can forbid extra fields, and application validation will enforce exact set equality, uniqueness, and type correctness without relying on response order.

## Retry ownership

The official SDK already retries connection failures, timeouts, HTTP 408, 409, 429, and server errors twice by default. The translation layer will add bounded retries for semantic failures such as a missing ID, unexpected ID, duplicate ID, empty text, or validation failure. It will not use a different provider or model during retries.

## Dry run and approval gates

`translate` performs only inspection and estimation unless `--execute` is supplied. An unlimited run additionally requires `--approve-full`. This makes a paid sample explicit and prevents a complete site run before human review.

Every API request also receives a conservative `max_output_tokens` ceiling derived from that batch's source characters and row count. The pre-request hard-cost check uses this ceiling across semantic retries, while normal displayed estimates continue to show expected usage. This limits anomalously verbose structured output from exceeding the approved spend.

## Protected-content validation

Eligibility filtering avoids spending tokens on standalone URLs, emails, phones, paths, slugs, dates, code, placeholders, shortcodes, and protected names. Returned translations must preserve exact HTML tags and attributes, entities, printf and template placeholders, shortcodes, URLs, emails, phones, paths, code spans, CSS selectors, JSON keys, protected brands, boundary whitespace, and newline counts. Invalid rows remain in the review report but are excluded from SQL.

## Snapshot-safe patch and rollback

Each patch statement matches the row ID, exact source, previous translation including NULL, and previous status. Rollback performs the inverse check against the generated translation and machine status. A stale live row therefore produces zero affected rows instead of overwriting newer work. The generated SQL emits `ROW_COUNT()` after each guarded update for phpMyAdmin review.

## SQL parsing

SQL statements are split with quote, backtick, and comment awareness before INSERT tuples are parsed. A regex-only statement boundary was rejected after the real SureCookie export proved that semicolons inside source strings truncated the table from 2,793 rows to 2,067. The corrected parser returns all 2,793 rows and has a regression test for embedded semicolons and comment markers.

## Representative sample

The limited run selects available HTML, embedded placeholders, privacy or consent text, CTA copy, entities, questions, very short UI text, long text, medium paragraphs, documentation text, and evenly spaced rows. Standalone `%title`-style tokens are protected and skipped.

## Batch API

GPT-5.6 Luna supports Batch API, but V1 keeps ordinary Responses API requests for immediate sample review and simpler failure handling. Batch submission is deferred until the synchronous workflow is proven and must retain identical ID, validation, reporting, and cost safeguards.

## Production artifacts remain local

The supplied SQL export, credentials, reports, patches, rollbacks, and production logs will remain ignored and uncommitted. Only synthetic fixtures may enter version control.

## Sample approval gate

The CLI will support limiting a representative sample and producing review artifacts, but the full SureCookie translation will not run until the user approves the sample.
