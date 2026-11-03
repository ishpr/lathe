#!/usr/bin/env bash
# Maintainer check: every markdown document in docs/epic/examples.md must lint with 0 errors
# (the "before" epic is expected to fail and is skipped); documents with a Defaults block are linted with --agent. Run after changing the examples or the linter.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
python3 -I - "$HERE/../../docs/epic/examples.md" "$TMP" <<'EOF'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
blocks = re.findall(r"```markdown\n(.*?)```", text, re.S)
for i, block in enumerate(blocks, 1):
    if block.lstrip().startswith("# Epic: Build"):
        continue  # the deliberately bad example
    open(f"{sys.argv[2]}/example-{i}.md", "w", encoding="utf-8").write(block)
print(f"{len(blocks)} blocks found")
EOF
status=0
for f in "$TMP"/example-*.md; do
  flags=""
  # Agent-ready documents (they carry a Defaults block) are also held to the --agent rules.
  if grep -q 'Defaults when ambiguous' "$f"; then flags="--agent"; fi
  python3 -I "$HERE/../../scripts/lint_epic.py" $flags "$f" | tail -1
  python3 -I "$HERE/../../scripts/lint_epic.py" $flags "$f" > /dev/null || status=1
done
rm -rf "$TMP"
exit $status
