$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"

Write-Host "=== Re-create change set explicitly to capture full error ==="
$body = Get-Content -Path "c:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\build\packaged.yaml" -Raw -Encoding UTF8

# Upload packaged template to S3 for body reference
$key = "validate/phase6-debug-$(Get-Date -Format yyyyMMddHHmmss).yaml"
$tmp = "$env:TEMP\phase6-debug-packaged.yaml"
$body | Out-File -FilePath $tmp -Encoding UTF8
aws s3 cp "$tmp" "s3://safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1/$key" --profile AWS-security-check --region ap-northeast-1 | Out-Null
$templateUrl = "https://safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1.s3.ap-northeast-1.amazonaws.com/$key"
Write-Host "URL: $templateUrl"

Write-Host "=== validate-template ==="
aws cloudformation validate-template `
    --template-url "$templateUrl" `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --output json | Out-String | Write-Host

Write-Host "=== Done ==="
