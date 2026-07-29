#!/usr/bin/env bash
#
# Spin up a throwaway MariaDB matching production (11.8), load the site's
# TranslatePress tables into it, and rehearse a bulk import end to end.
#
# This exists so you can practise the real `--apply` against a copy of your own
# data before touching the live site, and so the import can be re-verified
# whenever the script or the dump changes.
#
#   ./docker-test.sh up       start the container and load the dump
#   ./docker-test.sh verify   run the full check suite (safe, self-cleaning)
#   ./docker-test.sh shell    open a mariadb prompt against the copy
#   ./docker-test.sh down     destroy the container
#
# Connection details once up:
#   host 127.0.0.1  port 13306  user root  password rootpw  database wptest
#
set -euo pipefail

cd "$(dirname "$0")"

CONTAINER=trp-test
PORT=13306
DB=wptest
PASS=rootpw
DUMP=${DUMP:-dump.sql}
PY=${PY:-.venv/bin/python}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mysql_() { docker exec -i "$CONTAINER" mariadb -uroot -p"$PASS" "$DB" "$@"; }
conn=(--db-host 127.0.0.1 --db-port "$PORT" --db-user root --db-pass "$PASS" --db-name "$DB")

up() {
    [ -f "$DUMP" ] || { echo "error: dump not found: $DUMP" >&2; exit 1; }
    [ -x "$PY" ] || { echo "error: no venv. python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }

    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    echo "==> starting MariaDB 11.8 on port $PORT"
    docker run -d --name "$CONTAINER" \
        -e MARIADB_ROOT_PASSWORD="$PASS" \
        -e MARIADB_DATABASE="$DB" \
        -p "$PORT":3306 mariadb:11.8 >/dev/null

    printf '==> waiting for mysqld'
    until docker exec "$CONTAINER" mariadb -uroot -p"$PASS" -e "SELECT 1" >/dev/null 2>&1; do
        printf '.'; sleep 2
    done
    echo

    # The full dump aborts on a mis-exported Wordfence table, so load only the
    # TranslatePress statements.
    echo "==> extracting TranslatePress tables"
    "$PY" extract_trp_tables.py "$WORK/trp.sql" "$DUMP"
    echo "==> loading"
    mysql_ < "$WORK/trp.sql"
    mysql_ -e "SELECT COUNT(*) AS rows_, SUM(translated<>'' AND translated IS NOT NULL) AS translated
               FROM wp_trp_dictionary_ar_en_gb;"
    echo "==> ready. ${conn[*]}"
}

verify() {
    docker exec "$CONTAINER" mariadb -uroot -p"$PASS" -e "SELECT 1" >/dev/null 2>&1 \
        || { echo "error: container not running. ./docker-test.sh up" >&2; exit 1; }

    # Snapshot up front and restore on the way out, so verify is re-runnable.
    # Several checks below deliberately mutate the table; without this, the
    # second run fails check 1 because live no longer matches the dump.
    mysql_ -N -e "SELECT CONCAT('UPDATE \`wp_trp_dictionary_ar_en_gb\` SET translated=',
        QUOTE(translated), ', status=', status, ' WHERE id=', id, ';')
        FROM wp_trp_dictionary_ar_en_gb;" > "$WORK/restore.sql"
    trap 'mysql_ < "$WORK/restore.sql" 2>/dev/null || true; rm -rf "$WORK"' EXIT

    echo "==> 1/5 dump parser vs live MySQL driver"
    "$PY" trp_translate.py export --dump "$DUMP" --all --out "$WORK/a.xlsx" >/dev/null 2>&1
    "$PY" trp_translate.py export "${conn[@]}" --all --out "$WORK/b.xlsx" >/dev/null 2>&1
    "$PY" - "$WORK/a.xlsx" "$WORK/b.xlsx" <<'PYEOF'
import sys, openpyxl
load = lambda p: [tuple(r) for r in openpyxl.load_workbook(p, read_only=True).active.iter_rows(values_only=True)]
a, b = load(sys.argv[1]), load(sys.argv[2])
assert a == b, "dump parse and live read disagree"
print(f"    PASS  {len(a)-1} rows byte-identical")
PYEOF

    echo "==> 2/5 export -> import round-trip is a no-op"
    # Capture first: piping into `grep -q` would SIGPIPE the script and, under
    # pipefail, fail the pipeline even on a match.
    roundtrip=$("$PY" trp_translate.py import --excel "$WORK/b.xlsx" "${conn[@]}" --max-tier 0 2>&1)
    if grep -qE "^  (unmatched|conflict|fill|overwrite) " <<<"$roundtrip"; then
        echo "    FAIL  re-importing an untouched export proposed real changes"
        sed -n '/Match results/,/^$/p' <<<"$roundtrip"
        exit 1
    fi
    echo "    PASS  $(grep -cE '^  (unchanged|skipped|status-only) ' <<<"$roundtrip") outcome classes, no content changes"

    echo "==> 3/5 apply 50 translations, then roll back"
    "$PY" trp_translate.py export "${conn[@]}" --out "$WORK/todo.xlsx" >/dev/null 2>&1
    "$PY" - "$WORK/todo.xlsx" <<'PYEOF'
import sys, openpyxl
wb = openpyxl.load_workbook(sys.argv[1]); ws = wb.active
for n, row in enumerate(ws.iter_rows(min_row=2)):
    if n >= 50: break
    row[2].value = f"[EN-{row[0].value}] test"
wb.save(sys.argv[1])
PYEOF
    before=$(mysql_ -N -e "SELECT SUM(translated<>'' AND translated IS NOT NULL) FROM wp_trp_dictionary_ar_en_gb;")
    "$PY" trp_translate.py import --excel "$WORK/todo.xlsx" "${conn[@]}" \
        --backup "$WORK/rollback.sql" --apply >/dev/null 2>&1
    after=$(mysql_ -N -e "SELECT SUM(translated<>'' AND translated IS NOT NULL) FROM wp_trp_dictionary_ar_en_gb;")
    [ "$after" -eq "$((before + 50))" ] && echo "    PASS  translated $before -> $after"

    mysql_ < "$WORK/rollback.sql"
    "$PY" trp_translate.py export "${conn[@]}" --all --out "$WORK/c.xlsx" >/dev/null 2>&1
    "$PY" - "$WORK/b.xlsx" "$WORK/c.xlsx" <<'PYEOF'
import sys, openpyxl
load = lambda p: [tuple(r) for r in openpyxl.load_workbook(p, read_only=True).active.iter_rows(values_only=True)]
assert load(sys.argv[1]) == load(sys.argv[2]), "rollback did not restore the table"
print("    PASS  rollback restored byte-for-byte")
PYEOF

    echo "==> 4/5 SQL escaping via --sql-out + mariadb CLI"
    "$PY" - "$DUMP" "$WORK/nasty.xlsx" <<'PYEOF'
import sys, openpyxl
sys.path.insert(0, ".")
from trp_translate import DumpSource
empty = [r for r in DumpSource(sys.argv[1]).dictionary("wp_trp_dictionary_ar_en_gb")
         if not (r.translated or "").strip()][:8]
nasty = ["It's a test", 'He said "print it"', "Back\\slash C:\\path",
         "Line one\nLine two", "Tab\there", "100% -- x; DROP TABLE y;",
         "Em\u2014dash caf\u00e9 na\u00efve", "\u0645\u0637\u0628\u0639\u0629 \u2014 mixed 50\u066a"]
wb = openpyxl.Workbook(); ws = wb.active; ws.append(["Arabic", "English"])
for r, en in zip(empty, nasty): ws.append([r.original, en])
wb.save(sys.argv[2])
PYEOF
    "$PY" trp_translate.py import --excel "$WORK/nasty.xlsx" "${conn[@]}" \
        --sql-out "$WORK/nasty.sql" >/dev/null 2>&1
    mysql_ < "$WORK/nasty.sql"
    "$PY" - "$WORK/nasty.xlsx" "$PORT" "$PASS" "$DB" <<'PYEOF'
import sys, openpyxl, pymysql
want = [r[1] for r in openpyxl.load_workbook(sys.argv[1], read_only=True).active.iter_rows(min_row=2, values_only=True)]
conn = pymysql.connect(host="127.0.0.1", port=int(sys.argv[2]), user="root",
                       password=sys.argv[3], database=sys.argv[4], charset="utf8mb4")
with conn.cursor() as cur:
    cur.execute("SELECT translated, status FROM wp_trp_dictionary_ar_en_gb WHERE id BETWEEN 1 AND 8 ORDER BY id")
    got = cur.fetchall()
for (actual, status), expected in zip(got, want):
    assert actual == expected, f"mangled: {actual!r} != {expected!r}"
    assert status == 2, f"bad status {status}"
print(f"    PASS  {len(got)}/8 hostile strings stored verbatim")
PYEOF

    echo "==> 5/5 injection string did not execute"
    n=$(mysql_ -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB';")
    [ "$n" -eq 9 ] && echo "    PASS  all 9 tables intact"

    echo
    echo "==> all checks passed. Reload a clean copy with: ./docker-test.sh up"
}

case "${1:-verify}" in
    up)     up ;;
    verify) verify ;;
    shell)  docker exec -it "$CONTAINER" mariadb -uroot -p"$PASS" "$DB" ;;
    down)   docker rm -f "$CONTAINER" >/dev/null 2>&1 && echo "removed $CONTAINER" ;;
    *)      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
