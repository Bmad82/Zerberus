# Patch 166 - verify_sync.ps1
# ----------------------------------------------------------------------
# Pflicht-Schritt NACH `sync_repos.ps1`. Prueft fuer alle drei Repos
# (Zerberus, Ratatoskr, Claude), dass:
#   1. das Working-Tree clean ist (keine uncommitted Aenderungen)
#   2. `git log origin/main..HEAD` leer ist (nichts unpushed)
#
# Exit-Codes:
#   0 = alles synchron mit GitHub
#   1 = mindestens ein Repo nicht synchron — Coda darf nicht weitermachen
#
# Aufruf:
#   powershell -ExecutionPolicy Bypass -File scripts/verify_sync.ps1
#
# Hintergrund (CLAUDE_ZERBERUS.md, P166):
#   sync_repos.ps1 hatte sich als unzuverlaessig erwiesen (Drift bis zu
#   65 Patches). Stille Misserfolge kann Coda dadurch fangen, dass nach
#   jedem Sync ein Verifikations-Schritt laeuft, der harte Exit-Codes
#   liefert statt nur Hoffnung.

$ErrorActionPreference = 'Stop'

$repos = @(
    @{ Name = 'Zerberus';   Path = 'C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus' },
    @{ Name = 'Ratatoskr';  Path = 'C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr' },
    @{ Name = 'Claude';     Path = 'C:\Users\chris\Python\Claude' }
)

$failures = @()

foreach ($repo in $repos) {
    $name = $repo.Name
    $path = $repo.Path

    if (-not (Test-Path $path)) {
        $failures += "[$name] Pfad existiert nicht: $path"
        continue
    }

    Push-Location $path
    try {
        # Aktuellen Branch ermitteln (i. d. R. main).
        $branch = (git rev-parse --abbrev-ref HEAD).Trim()

        # 1) Working-Tree-Status: porcelain-Output muss leer sein.
        $statusLines = git status --porcelain
        if ($statusLines) {
            $count = ($statusLines | Measure-Object).Count
            $failures += "[$name] $count uncommitted Aenderung(en) im Working-Tree"
            Write-Host "[FAIL] $name : working-tree dirty ($count Datei[en])"
        }
        else {
            Write-Host "[ OK ] $name : working-tree clean"
        }

        # 2) Unpushed Commits: git log origin/$branch..HEAD muss leer sein.
        # Bei nicht-existenter Remote-Ref behandeln wir das als Fehler.
        $aheadLines = git log "origin/$branch..HEAD" --oneline 2>$null
        if ($LASTEXITCODE -ne 0) {
            $failures += "[$name] git log origin/$branch..HEAD fehlgeschlagen (Remote-Ref?)"
            Write-Host "[FAIL] $name : git log origin/$branch..HEAD failed (rc=$LASTEXITCODE)"
        }
        elseif ($aheadLines) {
            $count = ($aheadLines | Measure-Object).Count
            $failures += "[$name] $count unpushed Commit(s) auf $branch"
            Write-Host "[FAIL] $name : $count unpushed commit(s)"
            $aheadLines | ForEach-Object { Write-Host "         $_" }
        }
        else {
            Write-Host "[ OK ] $name : 0 unpushed commits ($branch)"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host ''
if ($failures.Count -eq 0) {
    Write-Host '[OK] Alle 3 Repos synchron mit GitHub' -ForegroundColor Green
    exit 0
}
else {
    Write-Host '[FAIL] SYNC FEHLGESCHLAGEN' -ForegroundColor Red
    foreach ($msg in $failures) {
        Write-Host "       - $msg" -ForegroundColor Red
    }
    Write-Host ''
    Write-Host 'Patch gilt NICHT als abgeschlossen. Sync-Problem erst loesen.' -ForegroundColor Red
    exit 1
}
