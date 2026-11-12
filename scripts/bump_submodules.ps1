<#
Bump every git submodule in external/ to the latest commit of its remote default branch.

Usage:
  ./scripts/bump_submodules.ps1             # fetch and check out the latest commits
  ./scripts/bump_submodules.ps1 -DryRun     # only list which submodules have newer commits

Nothing is committed; review with 'git diff --submodule=log'.
#>
param([switch]$DryRun)

Set-Location (Join-Path $PSScriptRoot '..')
git submodule update --init | Out-Null
if ($LASTEXITCODE -ne 0) { exit 1 }

if ($DryRun) {
    git submodule foreach --quiet 'git fetch --quiet origin; n=$(git rev-list --count HEAD..origin/HEAD 2>/dev/null || echo ?); echo "$name: $n commit(s) behind origin/HEAD"'
    exit $LASTEXITCODE
}

git submodule update --remote --merge
if ($LASTEXITCODE -ne 0) { exit 1 }
git submodule status
Write-Host "`nReview with 'git diff --submodule=log', then commit the updated pins."
