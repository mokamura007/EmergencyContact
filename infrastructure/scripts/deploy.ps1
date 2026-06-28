<#
.SYNOPSIS
  指定環境（dev/stg/prod）に safety-confirmation スタックをデプロイする。

.DESCRIPTION
  Task 15.1 で整備されたデプロイスクリプト。実行内容:

    1. 引数 EnvironmentName を {dev, stg, prod} で検証
    2. infrastructure/parameters/{env}.json の存在チェック
    3. SharedLayer ステージング（scripts/build_layer.ps1）
    4. aws cloudformation package で packaged-template.yaml を生成
       （S3 バケット: safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1）
    5. aws cloudformation deploy で safety-confirmation-{env} スタックを作成/更新

  -DryRun スイッチを指定すると、AWS API 呼出の手前で発行コマンドだけ表示して終了する。
  Task 15.1 の本セッションでは実 deploy 検証は Phase 15.2 へ委譲されているため、
  動作確認は -DryRun のみで行う。

.PARAMETER EnvironmentName
  デプロイ先環境名。dev / stg / prod のいずれか。

.PARAMETER DryRun
  実 AWS API を呼ばずに発行予定コマンドを表示して終了する。スクリプト整備の
  ローカル動作確認用。

.PARAMETER NoExecuteChangeset
  aws cloudformation deploy に --no-execute-changeset を付与する（changeset を
  作成するが実行はしない）。-DryRun より一段踏み込んだ確認用。

.PARAMETER SkipBuildLayer
  SharedLayer ステージング（build_layer.ps1）をスキップする。差分のみの再デプロイ時に。

.EXAMPLE
  # ローカル動作確認（AWS API 呼出なし）
  pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev -DryRun

.EXAMPLE
  # 実 deploy（本セッションでは実行しないこと。Phase 15.2 で実施）
  pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('dev', 'stg', 'prod')]
    [string]$EnvironmentName,

    [switch]$DryRun,
    [switch]$NoExecuteChangeset,
    [switch]$SkipBuildLayer
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'

# Repo root = scripts/ の 2 階層上
$RepoRoot       = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$InfraDir       = Join-Path $RepoRoot 'infrastructure'
$TemplatePath   = Join-Path $InfraDir 'template.yaml'
$PackagedPath   = Join-Path $InfraDir 'packaged-template.yaml'
$ParamsPath     = Join-Path $InfraDir ("parameters\{0}.json" -f $EnvironmentName)
$BuildLayerPs1  = Join-Path $RepoRoot 'scripts\build_layer.ps1'

$StackName      = "safety-confirmation-$EnvironmentName"
$S3Bucket       = 'safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1'
$AwsRegion      = 'ap-northeast-1'
# AWS CLI Profile: 環境変数 AWS_PROFILE が設定されていればそれを使用、未設定なら既定値
# （Task 15.11、NFR5：AssumeRole / 別アカウント検証 / 個人開発環境での運用柔軟性のため）
if ($env:AWS_PROFILE) {
    $AwsProfile = $env:AWS_PROFILE
} else {
    $AwsProfile = 'AWS-security-check'
}

Write-Host '=== deploy.ps1 START ===' -ForegroundColor Cyan
Write-Host ("Environment        : {0}" -f $EnvironmentName)
Write-Host ("StackName          : {0}" -f $StackName)
Write-Host ("TemplatePath       : {0}" -f $TemplatePath)
Write-Host ("ParamsPath         : {0}" -f $ParamsPath)
Write-Host ("PackagedPath       : {0}" -f $PackagedPath)
Write-Host ("AwsProfile         : {0}" -f $AwsProfile)
Write-Host ("DryRun             : {0}" -f $DryRun)
Write-Host ("NoExecuteChangeset : {0}" -f $NoExecuteChangeset)

# --- Step 1: 入力ファイル存在チェック ---
Write-Host "`n--- Step 1: Validate inputs ---" -ForegroundColor Cyan
if (-not (Test-Path $TemplatePath)) {
    Write-Error "Template not found: $TemplatePath"
    exit 2
}
if (-not (Test-Path $ParamsPath)) {
    Write-Error "Parameters file not found: $ParamsPath"
    exit 2
}

# JSON 構文チェック（CFn 標準形式：[{ParameterKey,ParameterValue}, ...]）
try {
    $params = Get-Content -Raw -Encoding UTF8 $ParamsPath | ConvertFrom-Json
} catch {
    Write-Error "parameters/$EnvironmentName.json is not valid JSON: $_"
    exit 2
}
if (-not ($params -is [System.Array])) {
    Write-Error "parameters/$EnvironmentName.json must be a JSON array"
    exit 2
}
foreach ($p in $params) {
    if (-not $p.PSObject.Properties.Match('ParameterKey').Count -or `
        -not $p.PSObject.Properties.Match('ParameterValue').Count) {
        Write-Error "Each parameter entry must have ParameterKey and ParameterValue"
        exit 2
    }
}
Write-Host ("Parameters file OK ({0} entries)" -f $params.Count)

# EnvironmentName 一致チェック（事故防止）
$envParam = $params | Where-Object { $_.ParameterKey -eq 'EnvironmentName' }
if (-not $envParam) {
    Write-Error "parameters/$EnvironmentName.json is missing 'EnvironmentName' entry"
    exit 2
}
if ($envParam.ParameterValue -ne $EnvironmentName) {
    Write-Error ("EnvironmentName mismatch: argument={0} parameters file={1}" -f `
        $EnvironmentName, $envParam.ParameterValue)
    exit 2
}

# --- Step 2: SharedLayer staging ---
if (-not $SkipBuildLayer) {
    Write-Host "`n--- Step 2: build_layer.ps1 (SharedLayer staging) ---" -ForegroundColor Cyan
    if (-not (Test-Path $BuildLayerPs1)) {
        Write-Error "build_layer.ps1 not found: $BuildLayerPs1"
        exit 2
    }
    if ($DryRun) {
        Write-Host ("[DryRun] would invoke: pwsh -NoProfile -File {0}" -f $BuildLayerPs1) -ForegroundColor Yellow
    } else {
        & pwsh -NoProfile -File $BuildLayerPs1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "build_layer.ps1 failed (exit=$LASTEXITCODE)"
            exit $LASTEXITCODE
        }
    }
} else {
    Write-Host "`n(Skipping build_layer.ps1 per -SkipBuildLayer)" -ForegroundColor DarkGray
}

# --- Step 3: aws cloudformation package ---
Write-Host "`n--- Step 3: aws cloudformation package ---" -ForegroundColor Cyan
$packageCmd = @(
    'aws', 'cloudformation', 'package',
    '--template-file',           $TemplatePath,
    '--s3-bucket',               $S3Bucket,
    '--output-template-file',    $PackagedPath,
    '--region',                  $AwsRegion,
    '--profile',                 $AwsProfile
)
Write-Host ("Command: {0}" -f ($packageCmd -join ' '))
if ($DryRun) {
    Write-Host "[DryRun] skipping aws cloudformation package" -ForegroundColor Yellow
} else {
    & $packageCmd[0] $packageCmd[1..($packageCmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        Write-Error "aws cloudformation package failed (exit=$LASTEXITCODE)"
        exit $LASTEXITCODE
    }
}

# --- Step 4: aws cloudformation deploy ---
Write-Host "`n--- Step 4: aws cloudformation deploy ---" -ForegroundColor Cyan
$deployCmd = [System.Collections.Generic.List[string]]::new()
$deployCmd.AddRange([string[]]@(
    'aws', 'cloudformation', 'deploy',
    '--template-file',     $PackagedPath,
    '--s3-bucket',         $S3Bucket,
    '--stack-name',        $StackName,
    '--parameter-overrides', ("file://{0}" -f $ParamsPath),
    '--capabilities',      'CAPABILITY_NAMED_IAM',
    '--region',            $AwsRegion,
    '--profile',           $AwsProfile
))
if ($NoExecuteChangeset) {
    $deployCmd.Add('--no-execute-changeset')
}
Write-Host ("Command: {0}" -f ($deployCmd -join ' '))
if ($DryRun) {
    Write-Host "[DryRun] skipping aws cloudformation deploy" -ForegroundColor Yellow
    Write-Host "`n=== deploy.ps1 END (DryRun, OK) ===" -ForegroundColor Green
    exit 0
}

& $deployCmd[0] $deployCmd[1..($deployCmd.Count - 1)]
$deployExit = $LASTEXITCODE

if ($deployExit -ne 0) {
    Write-Host "`n--- Failure: dumping last 10 stack events ---" -ForegroundColor Red
    aws cloudformation describe-stack-events `
        --stack-name $StackName `
        --region $AwsRegion --profile $AwsProfile `
        --max-items 10 `
        --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' `
        --output table
    Write-Error "aws cloudformation deploy failed (exit=$deployExit)"
    exit $deployExit
}

Write-Host "`n=== deploy.ps1 END (OK) ===" -ForegroundColor Green
exit 0
