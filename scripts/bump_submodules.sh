#!/usr/bin/env bash
# Bump every git submodule in external/ to the latest commit of its remote default branch.
#
# Usage:
#   ./scripts/bump_submodules.sh             # fetch and check out the latest commits
#   ./scripts/bump_submodules.sh --dry-run   # only list which submodules have newer commits
#
# The new pins are left modified in the working tree (git status shows them);
# nothing is committed. external/ is a read-only reference: only the pins move.

set -uo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

cd "$(dirname "${BASH_SOURCE[0]}")/.."
git submodule update --init >/dev/null || exit 1

if (( DRY_RUN )); then
  git submodule foreach --quiet '
    git fetch --quiet origin || exit 0
    head=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD || echo origin/HEAD)
    n=$(git rev-list --count HEAD.."$head" 2>/dev/null || echo "?")
    echo "$name: $n commit(s) behind $head"'
  exit 0
fi

git submodule update --remote --merge || exit 1
echo
git submodule status
echo
echo "Review with 'git diff --submodule=log', then commit the updated pins."
