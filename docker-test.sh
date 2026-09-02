#!/usr/bin/env bash
# Disposable MariaDB integration checks using synthetic TranslatePress data.

set -euo pipefail

cd "$(dirname "$0")"

CONTAINER=${CONTAINER:-trp-test}
PORT=${PORT:-13306}
DB=${DB:-wptest}
PASS=${PASS:-rootpw}
DUMP=${DUMP:-tests/fixtures/dictionary.sql}
TABLE=${TABLE:-acme_trp_dictionary_en_us_de_de}
PY=${PY:-.venv/bin/python}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mysql_() {
    docker exec -i "$CONTAINER" mariadb -uroot -p"$PASS" "$DB" "$@"
}

connection=(
    --db-host 127.0.0.1
    --db-port "$PORT"
    --db-user root
    --db-pass "$PASS"
    --db-name "$DB"
    --table "$TABLE"
)

up() {
    test -f "$DUMP" || { echo "error: dump not found: $DUMP" >&2; exit 1; }
    test -x "$PY" || { echo "error: create .venv and install requirements.txt" >&2; exit 1; }
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d \
        --name "$CONTAINER" \
        -e MARIADB_ROOT_PASSWORD="$PASS" \
        -e MARIADB_DATABASE="$DB" \
        -p "$PORT":3306 \
        mariadb:11.8 >/dev/null
    printf 'waiting for MariaDB'
    until docker exec "$CONTAINER" mariadb -uroot -p"$PASS" -e "SELECT 1" >/dev/null 2>&1; do
        printf '.'
        sleep 1
    done
    echo
    mysql_ < "$DUMP"
    mysql_ -e "SELECT COUNT(*) AS rows_ FROM \`$TABLE\`;"
}

verify() {
    # Every verification starts from the synthetic snapshot so a previous
    # apply, rollback, failure, or interrupted run cannot influence the result.
    up

    echo "1/4 unit tests"
    "$PY" -m unittest discover -v

    echo "2/4 dump parser matches live MySQL"
    "$PY" trp_translate.py export --dump "$DUMP" --table "$TABLE" --all --out "$WORK/dump.xlsx" >/dev/null
    "$PY" trp_translate.py export "${connection[@]}" --all --out "$WORK/live.xlsx" >/dev/null
    "$PY" - "$WORK/dump.xlsx" "$WORK/live.xlsx" <<'PY'
import sys
import openpyxl

def load(path):
    book = openpyxl.load_workbook(path, read_only=True)
    rows = [tuple(row) for row in book.active.iter_rows(values_only=True)]
    book.close()
    return rows

assert load(sys.argv[1]) == load(sys.argv[2]), "dump and live reads differ"
PY

    echo "3/4 guarded direct apply and rollback"
    "$PY" - "$WORK/import.csv" <<'PY'
import csv
import sys

with open(sys.argv[1], "w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["row_id", "source_text", "target_text"])
    writer.writerow([1, "Get Started", "Jetzt starten"])
PY
    "$PY" trp_translate.py import \
        "${connection[@]}" \
        --excel "$WORK/import.csv" \
        --backup "$WORK/rollback.sql" \
        --apply >/dev/null
    value=$(mysql_ -N -e "SELECT CONCAT(translated, ':', status) FROM \`$TABLE\` WHERE id=1;")
    test "$value" = "Jetzt starten:2"
    mysql_ < "$WORK/rollback.sql" >/dev/null
    value=$(mysql_ -N -e "SELECT CONCAT(COALESCE(translated, 'NULL'), ':', status) FROM \`$TABLE\` WHERE id=1;")
    test "$value" = ":0"

    echo "4/4 SQL escaping and stale snapshot guard"
    mysql_ -e "UPDATE \`$TABLE\` SET translated='manual', status=2 WHERE id=1;" >/dev/null
    "$PY" trp_translate.py import \
        --dump "$DUMP" \
        --table "$TABLE" \
        --excel "$WORK/import.csv" \
        --sql-out "$WORK/patch.sql" \
        --backup "$WORK/patch-rollback.sql" >/dev/null
    mysql_ < "$WORK/patch.sql" >/dev/null
    value=$(mysql_ -N -e "SELECT CONCAT(translated, ':', status) FROM \`$TABLE\` WHERE id=1;")
    test "$value" = "manual:2"

    echo "all Docker integration checks passed"
}

case "${1:-verify}" in
    up) up ;;
    verify) verify ;;
    shell) docker exec -it "$CONTAINER" mariadb -uroot -p"$PASS" "$DB" ;;
    down) docker rm -f "$CONTAINER" >/dev/null 2>&1 && echo "removed $CONTAINER" ;;
    *) echo "usage: $0 {up|verify|shell|down}" >&2; exit 1 ;;
esac
