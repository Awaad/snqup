#!/usr/bin/env bash
# 0001_initial_schema embeds its DDL verbatim rather than reading schema.sql,
# because a migration must be immutable: reading the file would let an edit
# retroactively change what the revision does.
#
# The cost of that is drift. This catches it. If schema.sql changes after the
# baseline has been applied anywhere, the correct move is a NEW migration plus
# an update to schema.sql, never an edit to 0001.
set -euo pipefail

python3 - <<'PYEOF'
import pathlib, sys

mig = pathlib.Path("apps/api/migrations/versions/0001_initial_schema.py").read_text()
ref = pathlib.Path("docs/schema/schema.sql").read_text()

marker = '-- =============================================================================\n-- REVIEW NOTES'
expected = ref[: ref.index(marker)].rstrip()

start = mig.index('SCHEMA = """\\\n') + len('SCHEMA = """\\\n')
end = mig.index('\n"""\n\n# Reverse creation order')
actual = mig[start:end].replace("\\\\", "\\").rstrip()

if actual != expected:
    import difflib
    print("error: 0001_initial_schema DDL has diverged from docs/schema/schema.sql")
    print()
    diff = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile="docs/schema/schema.sql", tofile="0001_initial_schema.py",
        lineterm="",
    ))
    print("\n".join(diff[:40]))
    print()
    print("If the baseline has already been applied anywhere, do NOT edit 0001.")
    print("Write a new migration and update schema.sql to match.")
    sys.exit(1)

print("baseline migration matches schema.sql")
PYEOF
