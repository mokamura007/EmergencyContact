<#
.SYNOPSIS
  CognitoUserPool の実機状態を snapshot 取得し、CFn deploy による副作用が
  無いことを検証する。

.DESCRIPTION
  Phase 15.22 の副次発見（`docs/notes/15-22-user-pool-side-effects.md`）：
  AWS CloudFormation drift detection は AWS::Cognito::UserPool の
  AdminCreateUserConfig / LambdaConfig を比較対象にしない。加えて
  `update-user-pool` API は明示指定しなかった一部フィールドを AWS デフォルト
  にリセットする挙動がある。CFn stack update 経由でも同種の副作用が発生
  しない保証は AWS 公式には明文化されていない。

  本スクリプトは deploy 前後で `describe-user-pool` を snapshot 取得し、
  重要フィールドの状態を人間可読な形で表示する。

  実行モード:
    - Snapshot mode（BaselinePath 未指定）:
        1. Stack Output `CognitoUserPoolId` を取得
        2. `describe-user-pool` を実行し JSON snapshot をファイル出力
        3. 重要フィールドの現在値をコンソールに表示
        4. exit 0

    - Compare mode（BaselinePath 指定）:
        1〜3 は Snapshot mode と同じ
        4. BaselinePath の JSON と新 snapshot を比較し差分表示
        5. 差分ゼロ    → exit 0
        6. 差分あり    → exit 1

.PARAMETER EnvironmentName
  対象環境（dev / stg / prod）。CFn Stack 名 `safety-confirmation-<env>` から
  `CognitoUserPoolId` Output を取得する。

.PARAMETER BaselinePath
  比較対象の baseline snapshot JSON ファイルパス。指定すると Compare mode
  で動作する。deploy 前に Snapshot mode で保存した JSON を指定する想定。

.PARAMETER Region
  AWS リージョン。既定 `ap-northeast-1`。

.PARAMETER AwsProfile
  AWS CLI プロファイル名。未指定なら環境変数 `AWS_PROFILE`、それも未設定
  なら `AWS-security-check`（`validate.ps1` と同じ既定値）。

.PARAMETER OutputPath
  新 snapshot の出力先。未指定なら
  `cognito-userpool-<env>-<yyyyMMdd-HHmmss>.json`（カレントディレクトリ）。

.EXAMPLE
  # deploy 前 snapshot 取得
  pwsh -File infrastructure/scripts/verify-cognito.ps1 -EnvironmentName dev `
      -OutputPath cognito-before.json

.EXAMPLE
  # deploy 後 snapshot 取得 + 前回との比較
  pwsh -File infrastructure/scripts/verify-cognito.ps1 -EnvironmentName dev `
      -BaselinePath cognito-before.json -OutputPath cognito-after.json

.NOTES
  読取り専用スクリプト。AWS への書き込みは行わない。`describe-user-pool`
  は無料 API 呼出。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'stg', 'prod')]
    [string]$EnvironmentName,

    [Parameter(Mandatory = $false)]
    [string]$BaselinePath,

    [Parameter(Mandatory = $false)]
    [string]$Region = 'ap-northeast-1',

    [Parameter(Mandatory = $false)]
    [string]$AwsProfile,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'

# --- Profile resolution (aligned with validate.ps1) ---
if (-not $AwsProfile) {
    if ($env:AWS_PROFILE) {
        $AwsProfile = $env:AWS_PROFILE
    } else {
        $AwsProfile = 'AWS-security-check'
    }
}

# --- Output path default ---
if (-not $OutputPath) {
    $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $OutputPath = "cognito-userpool-$EnvironmentName-$timestamp.json"
}

$StackName = "safety-confirmation-$EnvironmentName"

Write-Host '=== verify-cognito.ps1 START ===' -ForegroundColor Cyan
Write-Host ("EnvironmentName : {0}" -f $EnvironmentName)
Write-Host ("StackName       : {0}" -f $StackName)
Write-Host ("Region          : {0}" -f $Region)
Write-Host ("AwsProfile      : {0}" -f $AwsProfile)
Write-Host ("OutputPath      : {0}" -f $OutputPath)
if ($BaselinePath) {
    Write-Host ("BaselinePath    : {0}" -f $BaselinePath)
    if (-not (Test-Path $BaselinePath)) {
        Write-Error "Baseline snapshot not found: $BaselinePath"
        exit 2
    }
}

# --- Step 1: fetch CognitoUserPoolId from Stack Outputs ---
Write-Host "`n--- Step 1: Resolve CognitoUserPoolId from Stack Outputs ---" -ForegroundColor Cyan
$userPoolId = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --query "Stacks[0].Outputs[?OutputKey==``CognitoUserPoolId``].OutputValue" `
    --output text `
    --profile $AwsProfile `
    --region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Error "aws cloudformation describe-stacks failed (exit=$LASTEXITCODE)"
    exit $LASTEXITCODE
}
if (-not $userPoolId -or $userPoolId -eq 'None') {
    Write-Error ("CognitoUserPoolId Output not found in stack {0}" -f $StackName)
    exit 3
}
Write-Host ("CognitoUserPoolId: {0}" -f $userPoolId)

# --- Step 2: describe-user-pool ---
Write-Host "`n--- Step 2: aws cognito-idp describe-user-pool ---" -ForegroundColor Cyan
$snapshotJson = aws cognito-idp describe-user-pool `
    --user-pool-id $userPoolId `
    --output json `
    --profile $AwsProfile `
    --region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Error "aws cognito-idp describe-user-pool failed (exit=$LASTEXITCODE)"
    exit $LASTEXITCODE
}
$snapshotJson | Out-File -FilePath $OutputPath -Encoding utf8
Write-Host ("Snapshot saved  : {0}" -f (Resolve-Path $OutputPath).Path)

$snapshot = $snapshotJson | ConvertFrom-Json
$pool = $snapshot.UserPool

# --- Step 3: Print key fields ---
Write-Host "`n--- Step 3: Key fields (current state) ---" -ForegroundColor Cyan
$adminCfg = $pool.AdminCreateUserConfig
$imt = $adminCfg.InviteMessageTemplate

$summary = [ordered]@{
    'AllowAdminCreateUserOnly'                     = $adminCfg.AllowAdminCreateUserOnly
    'UnusedAccountValidityDays'                    = $adminCfg.UnusedAccountValidityDays
    'InviteMessageTemplate.EmailSubject (present)' = [bool]$imt.EmailSubject
    'InviteMessageTemplate.EmailMessage (present)' = [bool]$imt.EmailMessage
    'MfaConfiguration'                             = $pool.MfaConfiguration
    'UsernameAttributes'                           = ($pool.UsernameAttributes -join ',')
    'AutoVerifiedAttributes'                       = ($pool.AutoVerifiedAttributes -join ',')
    'LambdaConfig.PreAuthentication (present)'     = [bool]$pool.LambdaConfig.PreAuthentication
    'LambdaConfig.PostAuthentication (present)'    = [bool]$pool.LambdaConfig.PostAuthentication
    'LambdaConfig.PreSignUp (present)'             = [bool]$pool.LambdaConfig.PreSignUp
    'Policies.PasswordPolicy.MinimumLength'        = $pool.Policies.PasswordPolicy.MinimumLength
}
$summary.GetEnumerator() | ForEach-Object {
    Write-Host ("  {0,-52} : {1}" -f $_.Key, $_.Value)
}

# --- Step 3.5: Regression check on Phase 15.22 hazard fields ---
# LambdaConfig の 3 Trigger と AllowAdminCreateUserOnly は
# `update-user-pool` の副作用でリセットされることが実証済。ここで明示的に
# 期待値を assert し、fatal な状態を Compare mode に依存せず検出する。
$fatal = @()
if ($adminCfg.AllowAdminCreateUserOnly -ne $true) {
    $fatal += 'AdminCreateUserConfig.AllowAdminCreateUserOnly must be true (Requirement 1.9)'
}
if (-not $pool.LambdaConfig.PreAuthentication) {
    $fatal += 'LambdaConfig.PreAuthentication missing (auth lockout gate broken)'
}
if (-not $pool.LambdaConfig.PostAuthentication) {
    $fatal += 'LambdaConfig.PostAuthentication missing (audit log emission broken)'
}
if (-not $pool.LambdaConfig.PreSignUp) {
    $fatal += 'LambdaConfig.PreSignUp missing (self-signup defense broken)'
}
if (-not $imt.EmailSubject) {
    $fatal += 'InviteMessageTemplate.EmailSubject missing (Japanese admin invitation mail)'
}
if (-not $imt.EmailMessage) {
    $fatal += 'InviteMessageTemplate.EmailMessage missing (Japanese admin invitation mail)'
}

if ($fatal.Count -gt 0) {
    Write-Host "`n--- FATAL regressions ---" -ForegroundColor Red
    $fatal | ForEach-Object { Write-Host ("  {0}" -f $_) -ForegroundColor Red }
    Write-Error "Cognito UserPool regression detected. See list above."
    exit 4
}

# --- Step 4 (Compare mode only): diff against baseline ---
if ($BaselinePath) {
    Write-Host "`n--- Step 4: Compare against baseline ---" -ForegroundColor Cyan
    $baselineText = Get-Content -Path $BaselinePath -Raw
    $currentText = $snapshotJson

    if ($baselineText -eq $currentText) {
        Write-Host 'Baseline and current snapshots are byte-identical.' -ForegroundColor Green
        Write-Host "`n=== verify-cognito.ps1 END (OK, no diff) ===" -ForegroundColor Green
        exit 0
    }

    # Compare-Object works on line-by-line; normalise via JSON re-serialise
    # (sorted keys) so cosmetic key-order changes are ignored.
    $baselineObj = $baselineText | ConvertFrom-Json
    $currentObj = $snapshotJson | ConvertFrom-Json
    $baselineNorm = ($baselineObj | ConvertTo-Json -Depth 20 -Compress)
    $currentNorm = ($currentObj | ConvertTo-Json -Depth 20 -Compress)

    if ($baselineNorm -eq $currentNorm) {
        Write-Host 'Baseline and current snapshots are semantically identical (whitespace-only diff).' -ForegroundColor Green
        Write-Host "`n=== verify-cognito.ps1 END (OK, no semantic diff) ===" -ForegroundColor Green
        exit 0
    }

    Write-Host 'DIFFERENCES DETECTED between baseline and current snapshot.' -ForegroundColor Yellow
    $baseLines = ($baselineObj | ConvertTo-Json -Depth 20).Split("`n")
    $currLines = ($currentObj | ConvertTo-Json -Depth 20).Split("`n")
    $diff = Compare-Object -ReferenceObject $baseLines -DifferenceObject $currLines
    $diff | ForEach-Object {
        $marker = if ($_.SideIndicator -eq '<=') { '- baseline' } else { '+ current ' }
        Write-Host ("{0}: {1}" -f $marker, $_.InputObject)
    }
    Write-Host "`n=== verify-cognito.ps1 END (DIFF, exit 1) ===" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n=== verify-cognito.ps1 END (OK) ===" -ForegroundColor Green
exit 0
