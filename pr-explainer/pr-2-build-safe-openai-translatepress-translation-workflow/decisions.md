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

English apostrophe entities receive a user-approved grammatical exception. The validator decodes each entity and examines its position in the source token. It relaxes recognized contraction suffixes, singular possessives, and conservatively detected plural possessives, then records a review warning. It does not globally whitelist apostrophe entities: standalone quotation, immediately quoted words, `O’Reilly`-style names, and every non-apostrophe entity stay exact. This avoids rejecting natural German solely because English contraction or possessive grammar disappeared, without weakening formatting or technical-token protection.

Printf placeholder recognition now requires a token boundary after the conversion specifier. Without that boundary, ordinary prose such as `80% of problems` was misread as the valid-looking `% o` placeholder and a German `80 % der Probleme` was misread as `% d`. The boundary keeps real placeholders including `%s`, `%1$s`, `% d`, `%.2f`, and `%08x` protected while excluding percentage words such as `80% of`, `100% complete`, and `20% discount`.

## Snapshot-safe patch and rollback

Each patch statement matches the row ID, exact source, previous translation including NULL, and previous status. Rollback performs the inverse check against the generated translation and machine status. A stale live row therefore produces zero affected rows instead of overwriting newer work. The generated SQL emits `ROW_COUNT()` after each guarded update for phpMyAdmin review.

An optional generated preflight uses a session-scoped temporary table containing the same exact guard snapshot. It performs no update against the TranslatePress table, reports missing rows and source, translation, or status changes, summarizes matched and stale counts, and removes the temporary table. A temporary table was preferred over a large derived `UNION` because it preserves LONGTEXT and NULL values without type inference or truncation risk and gives one auditable mismatch report before import.

## SQL parsing

SQL statements are split with quote, backtick, and comment awareness before INSERT tuples are parsed. A regex-only statement boundary was rejected after the real SureCookie export proved that semicolons inside source strings truncated the table from 2,793 rows to 2,067. The corrected parser returns all 2,793 rows and has a regression test for embedded semicolons and comment markers.

## Representative sample

The limited run selects available HTML, embedded placeholders, privacy or consent text, CTA copy, entities, questions, very short UI text, long text, medium paragraphs, documentation text, and evenly spaced rows. Standalone `%title`-style tokens are protected and skipped.

The SureCookie German profile explicitly uses formal `Sie`/`Ihr` address. The first live sample otherwise mixed formal copy with one informal `dein/verbinde` result. Consistent formal address better fits a German B2B privacy and WordPress product while remaining a narrowly target-specific instruction that does not affect later French, Spanish, Italian, Dutch, or Polish runs.

## Batch API

GPT-5.6 Luna supports Batch API, but V1 keeps ordinary Responses API requests for immediate sample review and simpler failure handling. Batch submission is deferred until the synchronous workflow is proven and must retain identical ID, validation, reporting, and cost safeguards.

## Production artifacts remain local

The supplied SQL export, credentials, reports, patches, rollbacks, and production logs will remain ignored and uncommitted. Only synthetic fixtures may enter version control.

The CLI reads `OPENAI_API_KEY` from the inherited environment first, then the repository's ignored `.env` file without overriding an existing shell value. This supports agent-run processes that cannot inherit an export from a separate terminal while keeping the key out of arguments, logs, reports, SQL, Git, and committed configuration.

An OpenAI batch error that remains after bounded SDK and application retries stops the translation loop. Rows in the failed batch retain the provider error, while all later selected rows are recorded as unattempted. Continuing could spend more credits during a model outage or unsupported-model error and would violate the explicit no-fallback stop requirement.

## Sample approval gate

The CLI will support limiting a representative sample and producing review artifacts, but the full SureCookie translation will not run until the user approves the sample.
