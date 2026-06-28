<#
.SYNOPSIS
  infrastructure/template.yaml をローカル検証する（cfn-lint）。

.DESCRIPTION
  本スクリプトは Task 15.1 で整備されたローカル検証スクリプト。
  AWS API を呼ばずに完結する。

  実行内容:
    1. cfn-lint で infrastructure/template.yaml を検証
       （`.cfnlintrc` の ignore_checks=W2001/W3002/W3037/W8001 が自動適用される）

  オプション:
    -ValidateOnAws  ：AWS API `validate-template` も呼ぶ（S3 アップロード経由、
                      template > 51,200 bytes のため）。本セッションでは通常 false。

  Exit コード:
    0  : 全検証パス
    非0: いずれかの検証で失敗

.PARAMETER ValidateOnAws
  AWS `aws cloudformation validate-template` も実行する場合に指定。S3 アップロードが
  発生する（無料利用枠内）。実機 deploy は伴わない。

.EXAMPLE
  pwsh -NoProfile -File infrastructure/scripts/validate.ps1
#>
[CmdletBinding()]
param(
    [switch]$ValidateOnAws
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'

# Repo root = scripts/ の 2 階層上
$RepoRoot      = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TemplatePath  = Join-Path $RepoRoot 'infrastructure\template.yaml'
$CfnLintExe    = 'C:\Users\m_okamura\AppData\Local\Programs\Python\Python312\Scripts\cfn-lint.exe'
$S3Bucket      = 'safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1'
$AwsRegion     = 'ap-northeast-1'
# AWS CLI Profile: 環境変数 AWS_PROFILE が設定されていればそれを使用、未設定なら既定値
# （Task 15.11、NFR5：AssumeRole / 別アカウント検証 / 個人開発環境での運用柔軟性のため）
if ($env:AWS_PROFILE) {
    $AwsProfile = $env:AWS_PROFILE
} else {
    $AwsProfile = 'AWS-security-check'
}

Write-Host '=== validate.ps1 START ===' -ForegroundColor Cyan
Write-Host ("RepoRoot     : {0}" -f $RepoRoot)
Write-Host ("TemplatePath : {0}" -f $TemplatePath)
Write-Host ("AwsProfile   : {0}" -f $AwsProfile)

if (-not (Test-Path $TemplatePath)) {
    Write-Error "Template not found: $TemplatePath"
    exit 2
}

# --- Step 1: cfn-lint ---
Write-Host "`n--- Step 1: cfn-lint ---" -ForegroundColor Cyan
if (-not (Test-Path $CfnLintExe)) {
    Write-Error "cfn-lint not found: $CfnLintExe"
    exit 2
}
# .cfnlintrc は infrastructure/ にあるため cwd をそこに置く
Push-Location (Join-Path $RepoRoot 'infrastructure')
try {
    & $CfnLintExe 'template.yaml'
    $cfnExit = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Host ("cfn-lint exit code: {0}" -f $cfnExit)
if ($cfnExit -ne 0) {
    Write-Error "cfn-lint failed (exit=$cfnExit)"
    exit $cfnExit
}

# --- Step 2 (optional): aws validate-template via S3 ---
if ($ValidateOnAws) {
    Write-Host "`n--- Step 2: aws cloudformation validate-template (via S3) ---" -ForegroundColor Cyan
    $S3Key = "validation/template-{0:yyyyMMdd-HHmmss}.yaml" -f (Get-Date).ToUniversalTime()
    aws s3 cp $TemplatePath ("s3://{0}/{1}" -f $S3Bucket, $S3Key) `
        --profile $AwsProfile --region $AwsRegion
    if ($LASTEXITCODE -ne 0) {
        Write-Error "aws s3 cp failed (exit=$LASTEXITCODE)"
        exit $LASTEXITCODE
    }
    aws cloudformation validate-template `
        --template-url ("https://s3.{0}.amazonaws.com/{1}/{2}" -f $AwsRegion, $S3Bucket, $S3Key) `
        --profile $AwsProfile --region $AwsRegion
    if ($LASTEXITCODE -ne 0) {
        Write-Error "aws cloudformation validate-template failed (exit=$LASTEXITCODE)"
        exit $LASTEXITCODE
    }
} else {
    Write-Host "`n(Skipping aws validate-template; pass -ValidateOnAws to enable.)" -ForegroundColor DarkGray
}

Write-Host "`n=== validate.ps1 END (OK) ===" -ForegroundColor Green
exit 0
