<#
Dump the skills that herdr and playwright-cli ship inside themselves into
.agents/skills/, where coding agents discover them as normal skills.

Usage: ./scripts/install_skills.ps1

Re-run after installing or updating herdr or playwright-cli so the skills match
the installed versions. .agents/skills/ is generated and gitignored.
#>
Set-Location (Join-Path $PSScriptRoot '..')
$dest = '.agents/skills'
$status = 0

# herdr prints its SKILL.md to stdout.
if (Get-Command herdr -ErrorAction SilentlyContinue) {
    New-Item -ItemType Directory -Force "$dest/herdr" | Out-Null
    $text = (herdr --skill) -join "`n"
    if ($LASTEXITCODE -eq 0 -and $text) {
        [IO.File]::WriteAllText((Join-Path (Resolve-Path "$dest/herdr") 'SKILL.md'), $text + "`n", [Text.UTF8Encoding]::new($false))
        Write-Host "herdr: $dest/herdr/SKILL.md"
    } else { Write-Error "herdr: 'herdr --skill' failed"; $status = 1 }
} else { Write-Error 'herdr: not installed (see spec/prerequisites.md)'; $status = 1 }

# playwright-cli lists the path of its bundled skill directory in --help.
if (Get-Command playwright-cli -ErrorAction SilentlyContinue) {
    $line = playwright-cli --help | Where-Object { $_ -match '^Agent skill:' } | Select-Object -First 1
    $skillFile = if ($line) { ($line -replace '^Agent skill:\s*', '').Trim() } else { '' }
    if ($skillFile -and (Test-Path $skillFile)) {
        Remove-Item -Recurse -Force "$dest/playwright-cli" -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force "$dest/playwright-cli" | Out-Null
        Copy-Item -Recurse -Force "$(Split-Path $skillFile)/*" "$dest/playwright-cli/"
        Write-Host "playwright-cli: $dest/playwright-cli/SKILL.md"
    } else { Write-Error 'playwright-cli: bundled skill not found'; $status = 1 }
} else { Write-Error 'playwright-cli: not installed (see spec/prerequisites.md)'; $status = 1 }

exit $status
