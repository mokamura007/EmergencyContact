# build_layer.ps1 — Stage backend/shared/ into the Lambda Layer ZIP layout.
#
# AWS Lambda's Python runtime expects Layer content at /opt/python/<module>/.
# `aws cloudformation package` zips the directory referenced by
# AWS::Lambda::LayerVersion.Content as-is, so the staging directory must
# already contain `python/<module>/...`.
#
# Source:      <repo>/backend/shared/
# Destination: <repo>/infrastructure/build/layers/shared/python/shared/
#
# Invoke this script BEFORE `aws cloudformation package`:
#     pwsh -File ./scripts/build_layer.ps1
#
# The destination tree is git-ignored (see .gitignore).

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..")).Path

$Source     = Join-Path $RepoRoot "backend\shared"
$LayerRoot  = Join-Path $RepoRoot "infrastructure\build\layers\shared"
$DestPython = Join-Path $LayerRoot "python\shared"

if (-not (Test-Path $Source)) {
    throw "Source directory not found: $Source"
}

# Clean previous build output (idempotent).
if (Test-Path $LayerRoot) {
    Remove-Item -Recurse -Force $LayerRoot
}

New-Item -ItemType Directory -Path $DestPython -Force | Out-Null

# Copy backend/shared/* into the staging tree.
Copy-Item -Path (Join-Path $Source "*") -Destination $DestPython -Recurse -Force

# Strip __pycache__ and *.pyc that may have been copied along.
Get-ChildItem -Path $LayerRoot -Recurse -Directory -Filter "__pycache__" `
    | ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
Get-ChildItem -Path $LayerRoot -Recurse -File -Filter "*.pyc" `
    | ForEach-Object { Remove-Item -Force $_.FullName }

Write-Host "SharedLayer staging built at: $LayerRoot"
Write-Host "  -> Layer content root:      $LayerRoot"
Write-Host "  -> Lambda PYTHONPATH entry: /opt/python (contains 'shared/' package)"
