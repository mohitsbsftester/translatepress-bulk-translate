# TranslatePress Bulk Translate

Offline-first bulk translation tooling for [TranslatePress](https://translatepress.com/). It discovers dictionary tables in SQL exports, estimates OpenAI cost, translates only eligible rows, validates protected content, and generates review, patch, and rollback artifacts.

The project is based on the original MIT-licensed work by Sajith K. Sasi. The MIT license and repository history are preserved.

## Safety model

- SQL exports are the production workflow. Direct production database access is not required.
- Dry-run and cost estimation are the default. A paid request requires `--execute`.
- The production model is exactly `gpt-5.6-luna` with reasoning effort `none`.
- There is no model or provider fallback.
- Full translation requires `--approve-full`, intended only after a sample review.
- Existing translations are preserved by default. Human-reviewed rows are never selected by machine translation.
- Failed validation never enters `patch.sql`.
- Patch and rollback statements verify the row ID, exact source, previous translation, and previous status.
- Dumps, reports, credentials, patches, rollbacks, and `.env` files are ignored by Git.

## Install

Python 3.10 or newer is recommended.

```bash
git clone https://github.com/mohitsbsftester/translatepress-bulk-translate.git
cd translatepress-bulk-translate
git switch staging
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The official OpenAI SDK reads `OPENAI_API_KEY` from the environment. The key is not accepted as a command-line argument and is never written to a report or SQL file.

```bash
export OPENAI_API_KEY="your-key"
```

For agent-run commands whose process cannot inherit a separate terminal export, copy `.env.example` to `.env` and fill the value locally. The CLI loads only this ignored project file and never overrides an existing shell value.

Do not commit `.env`.

## SureCookie English to German

The included defaults are optimized for SureCookie:

- Source language: English
- Target language: German
- Provider: OpenAI API
- Model: `gpt-5.6-luna`
- Reasoning effort: `none`
- Context: SureCookie as a WordPress cookie consent and privacy product
- Glossary: `glossary.de.json`
- Protected names: `protected-names.json`

The locale codes and WordPress prefix are not hardcoded. The tool derives them from the supplied SQL.

### 1. Inspect the export

```bash
python trp_translate.py inspect \
  --dump /path/to/wp_trp_dictionary_en_us_de_de.sql
```

Inspection reports every discovered regular dictionary table, its prefix, locale pair, total rows, untranslated rows, machine translations, human-reviewed translations, eligible strings, words, characters, and skip reasons.

If several regular dictionaries exist, select one on later commands with `--table`, or with both `--source-locale` and `--target-locale`.

### 2. Estimate the complete job

```bash
python trp_translate.py translate \
  --dump /path/to/wp_trp_dictionary_en_us_de_de.sql \
  --source-locale en_us \
  --target-locale de_de \
  --glossary glossary.de.json \
  --protected-names protected-names.json \
  --estimate-only \
  --max-cost 5
```

Before any paid request, the command prints:

```text
Provider: OpenAI API
Model: gpt-5.6-luna
Reasoning: none
Source: English
Target: German
Eligible strings: ...
Source words: ...
Characters: ...
Estimated input tokens: ...
Estimated output tokens: ...
Number of batches: ...
Estimated API cost: ...
Maximum approved cost: ...
```

Current default estimates use the published standard prices for GPT-5.6 Luna: $0.20 per million input tokens, $0.02 per million cached input tokens, and $1.20 per million output tokens. Pricing can change, so verify the [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) and override `--price-input`, `--price-cached-input`, or `--price-output` when needed.

If the estimate exceeds `--max-cost`, the command stops before checking the API key or creating a request.

### 3. Translate a representative sample

```bash
python trp_translate.py translate \
  --dump /path/to/wp_trp_dictionary_en_us_de_de.sql \
  --source-locale en_us \
  --target-locale de_de \
  --glossary glossary.de.json \
  --protected-names protected-names.json \
  --limit 15 \
  --max-cost 1 \
  --execute \
  --report sample.xlsx \
  --sql-out sample-patch.sql \
  --backup sample-rollback.sql
```

The sample selector prefers a useful mix when the dump contains it: HTML, placeholders, privacy or consent wording, short calls to action, questions, short UI text, and long text. Open `sample.xlsx` and review English and German side by side.

Do not run the full job until the sample is approved.

### 4. Translate the complete eligible set

After sample approval:

```bash
python trp_translate.py translate \
  --dump /path/to/wp_trp_dictionary_en_us_de_de.sql \
  --source-locale en_us \
  --target-locale de_de \
  --glossary glossary.de.json \
  --protected-names protected-names.json \
  --max-cost 5 \
  --execute \
  --approve-full \
  --report review.xlsx \
  --sql-out patch.sql \
  --backup rollback.sql
```

Review `review.xlsx` before importing. Machine output is written with TranslatePress status `1`, never status `2`.

### 5. Import and verify

1. Take a fresh full WordPress database backup.
2. Re-export the TranslatePress dictionary if the site changed after the reviewed snapshot.
3. In phpMyAdmin, select the correct database and import `patch.sql`.
4. Review each `affected_rows` result. `1` means the guarded update applied. `0` means the live row differed from the snapshot and was safely left unchanged.
5. Clear WordPress page/object caches and the CDN.
6. Visit representative German pages and test headings, buttons, forms, banners, docs, responsive layouts, and consent flows.
7. Review machine translations in TranslatePress and promote them to human-reviewed only after a person checks them.

If the patch must be undone, import `rollback.sql`. Rollback is also guarded: it restores a row only if it still contains the exact machine translation and status written by the patch. Later manual work is not silently overwritten.

## What validation protects

Every returned row must have exactly one requested stable row ID. Missing, duplicate, unexpected, empty, or malformed structured output is rejected and retried without changing the model.

The content validator compares source and target for:

- HTML tags, nesting, and attributes
- HTML entities
- printf placeholders such as `%s`, `%1$s`, and `%2$d`
- template variables such as `{service}` and `{{name}}`
- WordPress shortcodes
- URLs, email addresses, and phone numbers
- file paths and file names
- inline and fenced code
- CSS selectors and JSON keys
- protected product names
- leading/trailing whitespace and newline counts
- utf8mb4 text including `ä`, `ö`, `ü`, `Ä`, `Ö`, `Ü`, and `ß`

Standalone URLs, emails, phone numbers, slugs, dates, JSON/code, placeholders, shortcodes, and protected brand names are skipped rather than sent for translation. Slug localization, SEO localization, and Gettext are separate later phases.

## Review report

CSV, TSV, and XLSX reports include:

- row ID and source hash
- source and target languages
- exact stored source and translated text
- previous translation and status
- intended new status
- exact model and reasoning effort
- translation and validation status
- warnings and failure reason

Possible outcomes include `translated`, `already_translated`, `skipped`, `unchanged`, `failed_validation`, `api_failure`, `protected_content`, `conflict`, and `stale_source`.

## Spreadsheet workflow

Export untranslated rows:

```bash
python trp_translate.py export \
  --dump dump.sql \
  --source-locale en_us \
  --target-locale de_de \
  --out translations.xlsx
```

After a human fills `target_text`, preview the import:

```bash
python trp_translate.py import \
  --dump dump.sql \
  --source-locale en_us \
  --target-locale de_de \
  --excel translations.xlsx \
  --report import-review.xlsx
```

Generate a guarded patch and rollback:

```bash
python trp_translate.py import \
  --dump dump.sql \
  --source-locale en_us \
  --target-locale de_de \
  --excel translations.xlsx \
  --report import-review.xlsx \
  --sql-out import-patch.sql \
  --backup import-rollback.sql
```

Human spreadsheet imports default to status `2`. Use `--status 1` if the sheet contains unreviewed machine output. Existing translations are preserved unless `--overwrite-existing` is explicitly supplied.

Matching first uses `row_id` plus the exact source. If IDs are absent, it tries exact, Unicode-normalized, and entity/typography-normalized source matching. A row ID whose exact source changed becomes `stale_source`.

## Optional live database workflow

Offline SQL remains the recommended production path. For local or throwaway environments, commands can read from MySQL with `--wp-config` or `--db-host`, `--db-name`, `--db-user`, and `--db-pass`.

Direct `--apply` requires `--backup`. Updates use the same snapshot conditions and report stale conflicts.

## Batch API evaluation

GPT-5.6 Luna supports the OpenAI Batch API. It can be useful for a later, very large run, but it is intentionally not the only or default workflow here. The ordinary Responses API is used for the 10 to 20 string sample because it is immediate and easier to validate.

Batch submission is deferred until the normal SureCookie workflow is proven. Any future Batch mode must preserve the same stable IDs, strict schema, cost guard, validation, partial-failure reporting, and patch exclusion rules. Reliability and reviewability take priority over asynchronous throughput.

## Architecture

```text
trp_translate.py
  -> trp_tool.cli             command orchestration and safety gates
  -> trp_tool.sql             dump/live reads, discovery, guarded SQL
  -> trp_tool.spreadsheet     CSV/XLSX and matching
  -> trp_tool.openai_client   Responses API and strict output models
  -> trp_tool.validation      eligibility and protected content
  -> trp_tool.reports         review CSV/XLSX
  -> trp_tool.models          shared TranslatePress records and statuses
```

## Tests

The automated suite uses synthetic data and no credentials:

```bash
python -m unittest discover -v
```

It covers configurable languages, German Unicode, SQL parsing/escaping, quotes, apostrophes, newlines, utf8mb4, HTML, entities, placeholders, variables, shortcodes, URLs, emails, protected brands, malformed API output, ID mismatches, retries, existing/human translations, dry-run, cost guards, spreadsheet round-trips, guarded patch/rollback, and stale source protection.

The Docker harness adds a disposable MariaDB integration check:

```bash
./docker-test.sh verify
./docker-test.sh down
```

`verify` recreates the disposable container from the synthetic fixture on every run.

## License

MIT. See [LICENSE](LICENSE).
