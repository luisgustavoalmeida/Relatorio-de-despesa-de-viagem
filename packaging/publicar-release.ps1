# Publica a release v1.0.0 no GitHub (requer GitHub CLI: https://cli.github.com/).
# Uso (na raiz do projeto):
#   .\packaging\publicar-release.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Zip = Join-Path $Root "dist\RelatorioDespesaViagem-v1.0.0-Windows.zip"
$Notes = Join-Path $Root "docs\releases\v1.0.0.md"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "Instale o GitHub CLI (gh) e rode: gh auth login"
    Write-Host "https://cli.github.com/"
    exit 1
}
if (-not (Test-Path -LiteralPath $Zip)) {
    Write-Host "ZIP nao encontrado: $Zip"
    exit 1
}
if (-not (Test-Path -LiteralPath $Notes)) {
    Write-Host "Notas nao encontradas: $Notes"
    exit 1
}

gh release create v1.0.0 $Zip `
    --title "v1.0.0 - Gerador de Relatorios de Despesa de Viagem" `
    --notes-file $Notes `
    --latest

Write-Host "Release publicada."
