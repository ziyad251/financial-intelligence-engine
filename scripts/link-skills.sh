#!/usr/bin/env sh
# Link every skill in skills/ into .claude/skills/ so Claude Code discovers it (macOS/Linux).
# The links are gitignored: skills/ is the only copy under version control. Safe to re-run.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/.claude/skills"
for dir in "$root"/skills/*/; do
  name=$(basename "$dir")
  link="$root/.claude/skills/$name"
  if [ -L "$link" ]; then echo "ok      $link"
  elif [ -e "$link" ]; then echo "warning: $link exists and is not a link; leaving it alone" >&2
  else ln -s "../../skills/$name" "$link" && echo "linked  $link -> skills/$name"
  fi
done
