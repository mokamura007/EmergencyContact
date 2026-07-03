# deploy_dev.ps1 — Full deployment pipeline for the dev environment.
#
# Steps:
#   1. Build the shared Lambda Layer
#   2. CloudFormation package (upload artifacts to S3)
#   3. CloudFormation deploy (create/update the stack)
#
# Usage: pwsh -File ./scripts/deploy_dev.ps1

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..")).Path

$Profile   = "AWS-security-check"
$Region    = "ap-northeast-1"
$StackName = "safety-confirmation-dev"
$S3Bucket  = "safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1"

$TemplateFile   = Join-Path $RepoRoot "infrastructure\template.yaml"
$BuildDir       = Join-Path $RepoRoot "infrastructure\build"
$PackagedFile   = Join-Path $BuildDir "packaged.yaml"
$ParameterFile  = Join-Path $RepoRoot "infrastructure\parameters\dev.json"

# --- Step 1: Build Lambda Layer ---
Write-Host "`n=== Step 1: Building shared Lambda Layer ===" -ForegroundColor Cyan
& pwsh -File (Join-Path $ScriptDir "build_layer.ps1")
if ($LASTEXITCODE -ne 0) { throw "build_layer.ps1 failed (exit $LASTEXITCODE)" }

# --- Step 2: CloudFormation package ---
Write-Host "`n=== Step 2: CloudFormation package ===" -ForegroundColor Cyan
if (-not (Test-Path $BuildDir)) {
    New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
}

aws cloudformation package `
    --template-file $TemplateFile `
    --s3-bucket $S3Bucket `
    --output-template-file $PackagedFile `
    --profile $Profile `
    --region $Region

if ($LASTEXITCODE -ne 0) { throw "cloudformation package failed (exit $LASTEXITCODE)" }
Write-Host "Packaged template: $PackagedFile"

# --- Step 3: CloudFormation deploy ---
Write-Host "`n=== Step 3: CloudFormation deploy ===" -ForegroundColor Cyan
aws cloudformation deploy `
    --template-file $PackagedFile `
    --stack-name $StackName `
    --parameter-overrides file://$ParameterFile `
    --s3-bucket $S3Bucket `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset `
    --profile $Profile `
    --region $Region

if ($LASTEXITCODE -ne 0) { throw "cloudformation deploy failed (exit $LASTEXITCODE)" }
Write-Host "`n=== Deployment complete ===" -ForegroundColor Green
Write-Host "Stack: $StackName"
Write-Host "Region: $Region"
