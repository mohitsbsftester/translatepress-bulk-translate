# Execution Flow

## Before this PR

`main()`

→ `cmd_translate()` assumes Arabic source and English target

→ `looks_translatable()` rejects strings without Arabic script

→ `OpenRouterTranslator.translate_batch()` calls OpenRouter Chat Completions with loose JSON mode

→ non-empty responses become unconditional `UPDATE ... WHERE id = ...` statements

→ optional CSV report, patch, rollback, or direct database write

The export and import paths similarly expose Arabic and English column names and default locale assumptions.

## Flow after this PR

`main()`

→ `cmd_inspect()` calls `DumpSource` or `MySQLSource`

→ `split_sql_statements()` respects quoted semicolons and comments

→ `inspect_tables()` discovers regular TranslatePress dictionary tables and locale pairs

→ `cmd_translate()` uses `select_table()` and loads the selected dictionary

→ `eligible_rows()` and `eligibility_reason()` preserve existing work and skip non-prose protected content without requiring a specific script

→ `representative_sample()` selects a mixed limited sample when requested

→ `estimate_tokens()` and `print_estimate()` report provider, exact model, reasoning effort, languages, words, characters, tokens, batches, estimated cost, and maximum approved cost

→ default dry-run or `--estimate-only` exits without an API call

→ `--execute` authorizes a paid sample; unlimited execution also requires `--approve-full`

→ the hard-cost guard reserves the batch's capped maximum output across retries before each request

→ `OpenAITranslator.translate_batch()` sends stable row IDs through the official OpenAI Responses API and Pydantic strict Structured Outputs

→ exact ID-set checks reject missing, duplicate, unexpected, empty, or malformed output

→ `validate_translation()` checks protected content and HTML structure; a bounded individual retry receives the validation reason

→ `write_review()` records successes and failures

→ `write_patch()` includes only passed machine translations and calls `guarded_update_statement()`

→ `write_rollback()` restores the exact snapshot state only when the generated translation still exists

→ the user reviews the limited sample before any complete run

## Unchanged supporting paths

Spreadsheet export

→ `export_dictionary()` writes exact stored source strings and source hashes

→ configurable source and target columns

→ CSV or XLSX reviewer workflow

Spreadsheet import

→ `load_sheet()` resolves configurable generic source and target columns

→ `match_sheet()` prefers row ID plus exact source, then tiered source matching

→ conflict and existing-translation checks

→ dry run, guarded SQL output, rollback, or optional live database write
