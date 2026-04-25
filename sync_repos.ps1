# sync_repos.ps1 — Ratatoskr + Claude Repo Sync
# Aufrufen aus Zerberus-Root:
#   powershell -ExecutionPolicy Bypass -File sync_repos.ps1
#
# Regel (siehe CLAUDE_ZERBERUS.md "Repo-Sync-Pflicht"):
# - Nach jedem Patch Zerberus selbst committen + pushen
# - Nach jedem 5. Patch oder am Ende jeder Session dieses Script laufen
# - Ratatoskr NIEMALS manuell editieren — immer nur via Kopie aus Zerberus

$ErrorActionPreference = "Stop"

$zerberus  = "C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus"
$ratatoskr = "C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr"
$claude    = "C:\Users\chris\Python\Claude"

# Letzte Zerberus-Commit-Message als Sync-Betreff
$patchMsg = git -C $zerberus log -1 --format="%s"
if (-not $patchMsg) { $patchMsg = "Sync" }

# --- Ratatoskr Sync ---
Write-Host "=== Ratatoskr Sync ===" -ForegroundColor Cyan
# (srcRel, dstRel) — Quelle relativ zu Zerberus, Ziel relativ zu Ratatoskr.
# PROJEKTDOKUMENTATION.md liegt unter docs/ in Zerberus, wird aber flach nach Ratatoskr-Root kopiert.
$ratatoskrFiles = @(
    @("SUPERVISOR_ZERBERUS.md",      "SUPERVISOR_ZERBERUS.md"),
    @("CLAUDE_ZERBERUS.md",          "CLAUDE_ZERBERUS.md"),
    @("docs\PROJEKTDOKUMENTATION.md","PROJEKTDOKUMENTATION.md"),
    @("lessons.md",                  "lessons.md"),
    @("backlog_nach_patch83.md",     "backlog_nach_patch83.md"),
    @("README.md",                   "README.md"),
    # Patch 161 follow-up: Huginn-Roadmap + Review als Referenz auch in Ratatoskr,
    # damit der Supervisor (claude.ai) sie ohne Zerberus-Checkout sieht. Bleibt
    # unter docs/ statt flach nach Root, damit kuenftige Doku-Dateien dort
    # logisch gruppiert sind und keine Root-Namenskollision riskieren.
    @("docs\huginn_roadmap_v2.md",   "docs\huginn_roadmap_v2.md"),
    @("docs\huginn_review_final.md", "docs\huginn_review_final.md")
)
foreach ($pair in $ratatoskrFiles) {
    $src = Join-Path $zerberus $pair[0]
    $dst = Join-Path $ratatoskr $pair[1]
    if (Test-Path $src) {
        # Zielverzeichnis ggf. anlegen (z. B. docs/ in Ratatoskr beim ersten Sync)
        $dstDir = Split-Path $dst -Parent
        if ($dstDir -and -not (Test-Path $dstDir)) {
            New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
        }
        Copy-Item $src $dst -Force
        Write-Host "  copy: $($pair[0]) -> $($pair[1])"
    } else {
        Write-Host "  skip (nicht vorhanden): $($pair[0])" -ForegroundColor DarkGray
    }
}

Set-Location $ratatoskr
git add -A
$ratatoskrDiff = git diff --cached --stat
if ($ratatoskrDiff) {
    git commit -m "Sync: $patchMsg"
    git push
    Write-Host "Ratatoskr gepusht." -ForegroundColor Green
} else {
    Write-Host "Ratatoskr: nichts zu committen." -ForegroundColor Yellow
}

# --- Claude Repo Sync (nur wenn lessons.md projektuebergreifende Erkenntnisse enthaelt) ---
Write-Host "=== Claude Repo Sync ===" -ForegroundColor Cyan
$lessonsSrc = Join-Path $zerberus "lessons.md"
if (Test-Path $lessonsSrc) {
    # Ablage als zerberus_lessons.md im Claude-Repo (projektspezifisch markiert)
    $lessonsDst = Join-Path $claude "lessons\zerberus_lessons.md"
    Copy-Item $lessonsSrc $lessonsDst -Force
    Write-Host "  copy: lessons.md → lessons\zerberus_lessons.md"
}

Set-Location $claude
git add -A
$claudeDiff = git diff --cached --stat
if ($claudeDiff) {
    git commit -m "Lessons sync: $patchMsg"
    git push
    Write-Host "Claude-Repo gepusht." -ForegroundColor Green
} else {
    Write-Host "Claude-Repo: nichts zu committen." -ForegroundColor Yellow
}

Set-Location $zerberus
Write-Host "=== Sync komplett ===" -ForegroundColor Green
