# Patch 210 (Phase 5a #18) — Auto-Sync fuer Huginns RAG-Selbstwissen
# ----------------------------------------------------------------------
# Wrapper um tools/sync_huginn_rag.py. Wird im Marathon-Push-Zyklus VOR
# `sync_repos.ps1` aufgerufen — Coda macht das automatisch am Session-
# Ende, Chris muss die Datei NIE manuell laden.
#
# Schritte (alle vom Python-Modul):
#   1. DELETE /hel/admin/rag/document?source=huginn_kennt_zerberus.md
#      (Soft-Delete alter Chunks, idempotent — 404 ist OK)
#   2. POST   /hel/admin/rag/upload mit category=system
#      (neue Datei laedt, Stand-Anker-Block landet als erster Chunk)
#   3. (Optional, mit -Reindex) POST /hel/admin/rag/reindex
#      (physische Bereinigung soft-deleted Chunks)
#
# Auth:
#   $env:HUGINN_RAG_AUTH = "User:Pass"   (oder in .env am Repo-Root)
#
# Server-URL (optional, Default http://localhost:5000):
#   $env:ZERBERUS_URL = "http://localhost:5000"
#
# Exit-Codes:
#   0 = Sync erfolgreich
#   1 = Sync fehlgeschlagen (Server down, Auth fehlt, HTTP-Error)
#   2 = Plan-Fehler (Datei fehlt, Stand-Anker-Header fehlt)
#
# Aufruf:
#   powershell -ExecutionPolicy Bypass -File scripts/sync_huginn_rag.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/sync_huginn_rag.ps1 -Reindex
#   powershell -ExecutionPolicy Bypass -File scripts/sync_huginn_rag.ps1 -DryRun

[CmdletBinding()]
param(
    [switch]$Reindex,
    [switch]$DryRun,
    [string]$Source,
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'

# Repo-Root finden (PSScriptRoot = scripts/, ein Level hoch)
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pyArgs = @('-m', 'tools.sync_huginn_rag')
    if ($Reindex) { $pyArgs += '--reindex' }
    if ($DryRun) { $pyArgs += '--dry-run' }
    if ($Source) { $pyArgs += @('--source', $Source) }
    if ($BaseUrl) { $pyArgs += @('--base-url', $BaseUrl) }

    Write-Host "[SYNC-210] python $($pyArgs -join ' ')"
    & python @pyArgs
    $rc = $LASTEXITCODE
    exit $rc
}
finally {
    Pop-Location
}
