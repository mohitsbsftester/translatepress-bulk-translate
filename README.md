# trp-translate

Bulk translation tooling for [TranslatePress](https://translatepress.com/).

Export your untranslated strings to Excel, translate them (by hand, by
translator, or by machine), and write them back — without clicking through the
TranslatePress editor one string at a time.

Runs entirely on your own machine. Nothing is installed on the web server, so
**cPanel access alone is enough**: you export a `.sql` file, and the tool hands
you a `.sql` patch to import back.

---

## Table of contents

- [Quick run](#quick-run)
- [What this solves](#what-this-solves)
- [How TranslatePress stores translations](#how-translatepress-stores-translations)
- [Install](#install)
- [Which table am I working on?](#which-table-am-i-working-on)
- [Getting your data out](#getting-your-data-out)
- [Workflow A — translate in Excel](#workflow-a--translate-in-excel)
- [Workflow B — machine translation](#workflow-b--machine-translation)
- [Applying changes](#applying-changes)
- [Undoing a change](#undoing-a-change)
- [Command reference](#command-reference)
- [How matching works](#how-matching-works)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)
- [Safety notes](#safety-notes)

---

## Quick run

Machine-translate everything still untranslated and apply it through
phpMyAdmin. No server shell needed — cPanel is enough.

Total time is about ten minutes, and the model cost for a typical site is under
one cent.

### 1. Install (once)

**macOS / Linux**

```bash
git clone https://github.com/Sajith-K-Sasi/translatepress-bulk-translate.git
cd translatepress-bulk-translate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Sajith-K-Sasi/translatepress-bulk-translate.git
cd translatepress-bulk-translate
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Once the environment is active your prompt shows `(.venv)`, and every command
below works as written on both platforms. Opening a new terminal later? Re-run
the activate line first.

> **PowerShell blocks the activate script?** Run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window
> and try again, or use `.venv\Scripts\activate.bat` from `cmd.exe`.

### 2. Export the dictionary table

**cPanel → phpMyAdmin →** select your WordPress database → click the
`wp_trp_dictionary_..._...` table → **Export** → Format **SQL** → **Go**.

Save it as `dump.sql` inside the project folder.

> Not sure which table? See
> [Which table am I working on?](#which-table-am-i-working-on). With Arabic as
> the default language and English as the target it is
> `wp_trp_dictionary_ar_en_gb`.

### 3. Price the job

No API key needed for this step, and it writes nothing.

```bash
python trp_translate.py translate --dump dump.sql --estimate-only
```

```
532 candidate(s), 36 contain Arabic, 496 skipped as non-Arabic
36 string(s), 5,428 chars, 4 batch(es)
Estimated: ~3.5k in + ~2.2k out tokens = ~$0.0021
```

If the candidate count looks wrong, stop and check the table name before
spending anything.

### 4. Add your API key

Create a key at [openrouter.ai](https://openrouter.ai) and add a dollar of
credit.

**macOS / Linux**

```bash
export OPENROUTER_API_KEY=sk-or-...
```

**Windows (PowerShell)**

```powershell
$env:OPENROUTER_API_KEY = "sk-or-..."
```

Either way this lasts for the current terminal only. Set it again in a new one.

### 5. Translate five strings first

Never do the full run blind.

> **On PowerShell**, the `\` at the end of each line is a Unix continuation and
> will not work. Use a backtick `` ` `` instead, or put the whole command on
> one line.

```bash
python trp_translate.py translate --dump dump.sql \
    --context "a commercial printing press in Riyadh" \
    --limit 5 \
    --sql-out patch-sample.sql \
    --backup rollback.sql \
    --report sample.csv
```

Open `sample.csv` and read the five translations. `--context` matters: it is
the difference between stiff literal output and copy that reads like the rest
of your site.

### 6. Apply the sample

**phpMyAdmin →** your database → **Import** → choose `patch-sample.sql` → **Go**.

Then clear any page cache or CDN and load one of the affected pages in the
target language (e.g. `https://yoursite.com/en/contact/`). Confirm the text
reads correctly and the layout still holds.

### 7. Translate the rest

Happy with the sample? Drop `--limit` and run the remainder.

```bash
python trp_translate.py translate --dump dump.sql \
    --context "a commercial printing press in Riyadh" \
    --sql-out patch.sql \
    --backup rollback-full.sql \
    --report review.csv
```

Already-translated rows are skipped, so the five from step 5 are not redone and
nothing you translated by hand is overwritten.

### 8. Apply and clear cache

**phpMyAdmin → Import →** `patch.sql` → **Go**, then clear your page cache and
CDN.

The patch is wrapped in `START TRANSACTION` / `COMMIT`, so it either applies
completely or not at all.

### 9. Review in WordPress

Everything written here has `status = 1` (machine translated), so it shows as
distinct from human-reviewed strings in **TranslatePress → Translate Site**.
Work through anything that reads awkwardly and mark it reviewed.

---

**If something looks wrong,** import `rollback-full.sql` through phpMyAdmin the
same way. It restores every row in the table to its state before the patch.

**Before your first real run,** take a full database backup through cPanel
(**cPanel → Backup Wizard**). The rollback file covers this one table; a real
backup covers everything else.

**Re-export `dump.sql` before each session.** Patches target rows by `id` from
your snapshot, so if someone edits translations in the TranslatePress editor in
between, you would overwrite their work.

---

## What this solves

The TranslatePress editor is fine for a handful of strings. It stops being fine
at a few hundred, and a modest Elementor site generates a *lot* of strings — a
typical 11-page site produces 800+.

This tool gives you three things the editor does not:

- **Bulk export/import** through Excel, so a translator who has never seen
  WordPress can do the work in a spreadsheet.
- **Machine translation** of everything still empty, at roughly a tenth of a
  cent per page, via any model on [OpenRouter](https://openrouter.ai).
- **A rollback file** for every change, so a bad import is one import away from
  being undone.

Every command defaults to a **dry run**. Nothing is written unless you pass
`--apply` or `--sql-out`.

---

## How TranslatePress stores translations

Translations live in your WordPress database, not in files. The table that
matters is:

```
wp_trp_dictionary_<default-language>_<target-language>
```

One row per string:

| column | meaning |
|---|---|
| `id` | primary key |
| `original` | source text, **exactly as scraped from the rendered page** |
| `translated` | the translation |
| `status` | `0` untranslated, `1` machine translated, `2` human reviewed |
| `original_id` | link to `wp_trp_original_strings` |

**The critical detail:** TranslatePress only substitutes a translation when
`original` matches the page HTML byte for byte. The stored copy carries HTML
entities (`&#8217;`), non-breaking spaces, invisible bidi marks and stray
diacritics that you will never reproduce by typing.

That is why the workflow is **export first, then fill in**. Round-tripping an
export guarantees every row matches. Writing a spreadsheet from scratch works
too, but expect some rows to land in the "unmatched" report.

### What status gets written

`status` is how TranslatePress tells reviewed copy from unreviewed. Each
command defaults to the value that matches where the text came from:

| command | default `status` | why |
|---|---|---|
| `import` | **`2`** human reviewed | the text came from a person — a translator filled in the spreadsheet |
| `translate` | **`1`** machine translated | the text came from a model and nobody has read it yet |

That difference is the point. Machine output stays visually distinct in
**TranslatePress → Translate Site**, so you can see at a glance what still
needs a human pass. Once you have reviewed a string there, TranslatePress
promotes it to `2` itself.

Override on either command with `--status N` — for example `--status 1` on an
`import` whose spreadsheet was filled by Google Translate rather than a
translator.

---

## Install

Requires Python 3.9+. Works on macOS, Linux and Windows.

**macOS / Linux**

```bash
git clone https://github.com/Sajith-K-Sasi/translatepress-bulk-translate.git
cd translatepress-bulk-translate

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Sajith-K-Sasi/translatepress-bulk-translate.git
cd translatepress-bulk-translate

py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Activation is what makes every example in this README portable: with the
environment active, plain `python trp_translate.py ...` resolves to the
virtual environment on either platform. Your prompt shows `(.venv)` when it is
on, and each new terminal needs the activate line again.

Prefer not to activate? Call the interpreter by path instead —
`.venv/bin/python` on macOS and Linux, `.venv\Scripts\python.exe` on Windows.

| package | needed for |
|---|---|
| `openpyxl` | reading and writing `.xlsx` — always |
| `pymysql` | connecting to a live database — optional |

If you only ever work from `.sql` dumps, `openpyxl` alone is enough.

### Windows notes

- Use **PowerShell** or **Windows Terminal**. Everything works in `cmd.exe`
  too, but activation is `.venv\Scripts\activate.bat` there.
- `docker-test.sh` is a bash script. It needs **WSL**, **Git Bash**, or Docker
  Desktop's WSL backend. It is optional — the tool itself does not need it.
- Console output includes source-language text. The tool forces UTF-8 on its
  own output, so Arabic renders rather than crashing on legacy code pages.

---

## Which table am I working on?

In WordPress: **Settings → TranslatePress → General**. Note the *Default
Language* and the language you are translating **into**.

If the default is Arabic and you translate into British English, the table is
`wp_trp_dictionary_ar_en_gb`, and each row is Arabic → English. That is this
tool's default. Anything else:

```bash
--default-lang ar --target-lang en_GB     # or --table wp_trp_dictionary_xx_yy
```

> **Watch out:** if the site's default language was ever changed, old tables are
> left behind and are no longer used. Only the table matching the *current*
> default language has any effect. Writing to a stale one does nothing.

---

## Getting your data out

You need a `.sql` export of the dictionary table. You do **not** need a full
site backup.

**phpMyAdmin (cPanel → Databases → phpMyAdmin):**

1. Select your WordPress database in the left sidebar.
2. Click the `wp_trp_dictionary_..._...` table.
3. **Export** tab → Format **SQL** → **Go**.
4. Save the file as `dump.sql` next to this tool.

A full-site dump also works — the tool finds the table inside it.

**Alternatively, connect directly.** If your host offers **Remote MySQL** in
cPanel, allowlist your IP and skip the export/import cycle entirely:

```bash
--db-host yourserver.com --db-name wp_xxxx --db-user wp_xxxx --db-pass '...'
```

Or read the credentials straight out of a local copy of `wp-config.php`:

```bash
--wp-config /path/to/wp-config.php
```

---

## Workflow A — translate in Excel

### 1. Export what still needs translating

```bash
python trp_translate.py export --dump dump.sql --out todo.xlsx
```

You get a formatted spreadsheet, right-to-left aligned where appropriate:

| id | Arabic (original) | English (translation) | status |
|---|---|---|---|
| 42 | مرحبًا بكم في… | | not translated |

Add `--all` to include rows that already have a translation.

### 2. Fill in the translation column

Give `todo.xlsx` to your translator. One rule:

> **Do not edit the original column.** It is the matching key. Change it and the
> row will not match.

### 3. Preview the import

```bash
python trp_translate.py import --excel todo.xlsx --dump dump.sql \
    --report review.csv
```

Nothing is written. You get a summary:

```
Match results
  fill           412
  overwrite        3
  unchanged      288
  unmatched        7
  matched via   exact=703  normalized=9
```

| outcome | meaning |
|---|---|
| `fill` | empty row gets a translation |
| `overwrite` | replaces an existing translation |
| `status-only` | same text, status changes (e.g. machine → reviewed) |
| `unchanged` | already identical |
| `unmatched` | **no matching original — will have no effect** |
| `conflict` | two sheet rows target the same row with different text |
| `skipped` | blank translation, or `--skip-translated` |

Open `review.csv` to see every row and why it landed where it did. Investigate
`unmatched` before proceeding — those are usually edited originals.

### 4. Write it

See [Applying changes](#applying-changes).

---

## Workflow B — machine translation

Translates everything still empty using a model of your choice on OpenRouter.
Cheap models handle website copy well, and the volume is small: a typical site
costs well under one cent.

### 1. Get an API key

Sign up at [openrouter.ai](https://openrouter.ai), create a key, top up a dollar.

```bash
export OPENROUTER_API_KEY=sk-or-...          # macOS / Linux
```

```powershell
$env:OPENROUTER_API_KEY = "sk-or-..."        # Windows PowerShell
```

### 2. Price the job first — no key required

```bash
python trp_translate.py translate --dump dump.sql --estimate-only
```

```
532 candidate(s), 36 contain Arabic, 496 skipped as non-Arabic
36 string(s), 5,428 chars, 4 batch(es)
Estimated: ~3.5k in + ~2.2k out tokens = ~$0.0021
```

> **Why so many are skipped:** only strings actually containing source-language
> text are sent. Rows whose "original" is already in the target language need no
> translation, and sending them just invites the model to rewrite copy nobody
> asked it to touch.

### 3. Do a small run and read the output

```bash
python trp_translate.py translate --dump dump.sql \
    --limit 5 --report sample.csv --sql-out sample-patch.sql
```

Read `sample.csv` before trusting the rest.

### 4. Translate the remainder

```bash
python trp_translate.py translate --dump dump.sql \
    --context "a commercial printing press in Riyadh" \
    --glossary glossary.json \
    --sql-out patch.sql --backup rollback.sql --report review.csv
```

`--context` steers register and is worth setting. `--glossary` pins terms you
want translated consistently:

```json
{
  "مطبعة الفضلي": "Al-Fadli Printing Press",
  "الطباعة الأوفست": "offset printing"
}
```

Results are written with `status = 1` (machine translated), so they stay
visually distinct from human-reviewed strings in the TranslatePress editor.
Existing translations are never touched unless you pass `--retranslate`.

### Choosing a model

Default is `google/gemini-3.1-flash-lite`. Any OpenRouter model id works:

```bash
--model qwen/qwen3.7-flash          # very cheap, strong on Arabic
--model google/gemini-2.5-flash-lite
--model openai/gpt-5-nano
```

Pricing changes; set `--price-in` / `--price-out` (dollars per million tokens)
so the estimate stays honest, and cap spend with `--max-cost`.

---

## Applying changes

Three ways, in increasing order of directness.

### Option 1 — SQL patch (works with cPanel only)

```bash
... --sql-out patch.sql --backup rollback.sql
```

Then **phpMyAdmin → your database → Import → choose `patch.sql` → Go**.

The file is wrapped in `START TRANSACTION` / `COMMIT`, so it applies
all-or-nothing, and declares `SET NAMES utf8mb4` so non-Latin text imports
intact.

> **Re-export before each run.** The patch targets rows by `id` from your
> snapshot. If someone edits translations in the TranslatePress editor between
> your export and your import, you would overwrite their work. Export → patch →
> import in one sitting.

### Option 2 — direct write

Needs a reachable database (Remote MySQL, an SSH tunnel, or running on the
server):

```bash
... --wp-config wp-config.php --backup rollback.sql --apply
```

### Option 3 — dry run

The default. Prints what would change and exits.

**After applying:** clear any page cache and CDN. TranslatePress reads from the
database, but cached HTML still serves the old text.

---

## Undoing a change

Always pass `--backup rollback.sql`. It captures the current `translated` and
`status` of **every** row in the table before anything changes.

To undo: import `rollback.sql` the same way you imported the patch.

`--backup` works with `--sql-out` and with `--apply`, so you get a rollback even
when you cannot reach the database directly.

> This covers one table. Take a full database backup through cPanel before your
> first real run — that covers the mistake nobody predicted.

---

## Command reference

### Shared options

```
--dump FILE              read from a .sql dump (offline)
--wp-config FILE         read DB credentials from a wp-config.php
--db-host / --db-port / --db-name / --db-user / --db-pass
--prefix wp_             table prefix
--default-lang ar        TranslatePress default language
--target-lang en_GB      language being translated into
--table NAME             override the table name entirely
```

### `export`

```
--out FILE               .xlsx or .csv (default: translations.xlsx)
--all                    include rows that already have a translation
```

### `import`

```
--excel FILE             .xlsx, .csv or .tsv                     [required]
--sheet NAME             worksheet name (default: first)
--arabic-col / --english-col    column by header, number or letter
--status N               status to write (default: 2, human reviewed | see "What status gets written")
--max-tier N             loosest match tier, 0-3 (default: 2)
--skip-translated        never touch rows that already have a translation
--report FILE            per-row CSV report
--sql-out FILE           write UPDATE statements instead of applying
--backup FILE            write a rollback .sql
--apply                  write to the database
```

Columns are auto-detected from headers, or by which script the content is
actually in. Override when in doubt: `--arabic-col B --english-col C`.

### `translate`

```
--api-key KEY            default: $OPENROUTER_API_KEY
--model ID               default: google/gemini-3.1-flash-lite
--context TEXT           one-line description of the site
--glossary FILE          JSON of {source: target} terms to pin
--batch-size N           strings per request (default: 25)
--limit N                translate at most N strings
--retranslate            include rows that already have a translation
--status N               status to write (default: 1, machine translated | see "What status gets written")
--price-in / --price-out dollars per 1M tokens, for the estimate
--max-cost N             abort if the estimate exceeds this (default: 5.00)
--estimate-only          price the job and exit
--report / --sql-out / --backup / --apply    as above
```

---

## How matching works

Spreadsheet rows are matched to database rows through four tiers, strictest
first. The report tells you which tier each row hit.

| tier | name | what it tolerates |
|---|---|---|
| 0 | `exact` | nothing — byte-identical |
| 1 | `normalized` | Unicode NFC, invisible/bidi characters, trimming |
| 2 | `entity-folded` | HTML entities, smart quotes, collapsed whitespace |
| 3 | `fuzzy` | Arabic diacritics, tatweel, alef/yeh variants, digit forms |

Default is `--max-tier 2`. Tier 3 is opt-in because folding diacritics can
merge genuinely different strings — use it, then read the report.

Two safety behaviours worth knowing:

- **Ambiguity.** If several database rows share one normalised original, an
  untranslated one is preferred and the row is flagged `ambiguous`.
- **Conflicts.** If two spreadsheet rows resolve to the same database row with
  different text, the first wins and the rest are reported rather than silently
  overwriting each other.

---

## Troubleshooting

**Everything comes back `unmatched`.**
The originals were edited, or you are pointed at the wrong table. Run `export
--all` and compare against your sheet. Check the table name matches your
*current* default language.

**Translations do not appear on the site.**
Clear your page cache and CDN. Confirm you wrote to the table for the current
default language. Confirm `status` is not `0`.

**Arabic shows as `Ø£Ø®Ø±` after importing.**
A charset problem in phpMyAdmin. The generated patches declare `SET NAMES
utf8mb4` — if you hand-edited the file, keep that line, and make sure it is
saved as UTF-8.

**`--apply` refuses to run.**
It needs a live connection. With `--dump` you must use `--sql-out`.

**`error: could not identify the Arabic and English columns`.**
Pass them explicitly: `--arabic-col B --english-col C`.

**The model returns fewer strings than sent.**
Handled automatically: the batch is retried, then each string individually. Any
genuine failures are listed at the end. Lower `--batch-size` if it is frequent.

---

## Testing

A Docker harness loads your dictionary table into a throwaway MariaDB and
rehearses the whole flow against a copy of your own data.

```bash
./docker-test.sh up       # start MariaDB 11.8, load the dump
./docker-test.sh verify   # run the checks
./docker-test.sh shell    # mariadb prompt
./docker-test.sh down     # destroy it
```

```
==> 1/5 dump parser vs live MySQL driver
    PASS  823 rows byte-identical
==> 2/5 export -> import round-trip is a no-op
    PASS  3 outcome classes, no content changes
==> 3/5 apply 50 translations, then roll back
    PASS  rollback restored byte-for-byte
==> 4/5 SQL escaping via --sql-out + mariadb CLI
    PASS  8/8 hostile strings stored verbatim
==> 5/5 injection string did not execute
    PASS  all 9 tables intact
```

Check 1 matters most: it proves the offline `.sql` parser returns exactly what
the real MySQL driver returns, so dry runs against a dump are trustworthy.
Check 4 pushes quotes, backslashes, newlines and `; DROP TABLE y;` through the
patch path and back out of the database.

The harness uses a throwaway container password and never touches your live
site.

---

## Safety notes

- **Never commit dumps, backups or `wp-config.php`.** They contain credentials,
  password hashes and customer data. The included `.gitignore` blocks `*.sql`,
  `*.zip`, `wp-config.php`, spreadsheets and generated patches — keep it.
- **Take a full database backup** through cPanel before your first real run.
- **Start with `--limit`.** Apply a handful, look at the site, then do the rest.
- **Keep the rollback file** until you are satisfied.
- **API keys go in the environment**, never on the command line in shared
  shells, and never in the repo.

---

## Licence

MIT. See [LICENSE](LICENSE).
