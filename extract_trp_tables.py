"""Pull just the wp_trp_* statements out of a full WordPress dump.

Usage: python extract_trp_tables.py OUT.sql [IN.sql]

The full dump cannot be imported as-is: phpMyAdmin mis-exported a Wordfence
table (`DEFAULT x AS ...`), which aborts the import long after the
TranslatePress tables have loaded. This pulls out only what we need.
"""
import re, sys

src = sys.argv[2] if len(sys.argv) > 2 else "dump.sql"
out = sys.argv[1]

text = open(src, encoding="utf-8", errors="replace").read()

# Split into top-level statements on semicolons outside string literals / comments.
stmts, buf, i, in_str, in_line_comment = [], [], 0, False, False
while i < len(text):
    c = text[i]
    if in_line_comment:
        buf.append(c)
        if c == "\n":
            in_line_comment = False
        i += 1
        continue
    if in_str:
        if c == "\\" and i + 1 < len(text):
            buf.append(c); buf.append(text[i+1]); i += 2; continue
        if c == "'":
            in_str = False
        buf.append(c); i += 1; continue
    if c == "'":
        in_str = True; buf.append(c); i += 1; continue
    if text.startswith("--", i) or text.startswith("#", i):
        in_line_comment = True; buf.append(c); i += 1; continue
    if c == ";":
        stmts.append("".join(buf).strip()); buf = []; i += 1; continue
    buf.append(c); i += 1
if "".join(buf).strip():
    stmts.append("".join(buf).strip())

keep = [s for s in stmts if re.search(r"`wp_trp_\w+`", s)
        and re.match(r"^\s*(--[^\n]*\n|\s)*(CREATE TABLE|INSERT INTO|ALTER TABLE)", s, re.I)]

with open(out, "w", encoding="utf-8") as fh:
    fh.write("SET NAMES utf8mb4;\nSET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n\n")
    for s in keep:
        fh.write(s + ";\n")

kinds = {}
for s in keep:
    m = re.search(r"(CREATE TABLE|INSERT INTO|ALTER TABLE)", s, re.I)
    kinds[m.group(1).upper()] = kinds.get(m.group(1).upper(), 0) + 1
print(f"extracted {len(keep)} statements {kinds} -> {out}")
