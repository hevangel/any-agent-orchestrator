#!/usr/bin/env bash
# Dump the skills that herdr and playwright-cli ship inside themselves into
# .agents/skills/, where coding agents discover them as normal skills.
#
# Usage: ./scripts/install_skills.sh
#
# Re-run after installing or updating herdr or playwright-cli so the skills match
# the installed versions. .agents/skills/ is generated and gitignored.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
dest=".agents/skills"
status=0

# herdr prints its SKILL.md to stdout.
if command -v herdr >/dev/null 2>&1; then
  mkdir -p "$dest/herdr"
  if herdr --skill > "$dest/herdr/SKILL.md.tmp" && [[ -s "$dest/herdr/SKILL.md.tmp" ]]; then
    mv "$dest/herdr/SKILL.md.tmp" "$dest/herdr/SKILL.md"
    echo "herdr: $dest/herdr/SKILL.md"
  else
    rm -f "$dest/herdr/SKILL.md.tmp"; echo "herdr: 'herdr --skill' failed" >&2; status=1
  fi
else
  echo "herdr: not installed (see spec/prerequisites.md)" >&2; status=1
fi

# playwright-cli lists the path of its bundled skill directory in --help.
if command -v playwright-cli >/dev/null 2>&1; then
  skill_file=$(playwright-cli --help | sed -n 's/^Agent skill: *//p' | tr -d '\r' | head -n1)
  # Git Bash on Windows: convert C:\... to a path bash can read.
  command -v cygpath >/dev/null 2>&1 && skill_file=$(cygpath -u "$skill_file")
  if [[ -n "$skill_file" && -f "$skill_file" ]]; then
    rm -rf "$dest/playwright-cli"; mkdir -p "$dest/playwright-cli"
    cp -R "$(dirname "$skill_file")/." "$dest/playwright-cli/"
    echo "playwright-cli: $dest/playwright-cli/SKILL.md"
  else
    echo "playwright-cli: bundled skill not found" >&2; status=1
  fi
else
  echo "playwright-cli: not installed (see spec/prerequisites.md)" >&2; status=1
fi

exit $status
