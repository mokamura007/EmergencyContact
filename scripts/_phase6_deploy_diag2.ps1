$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"

$csId = "arn:aws:cloudformation:ap-northeast-1:214046906694:changeSet/awscli-cloudformation-package-deploy-1782388787/cf629669-1618-4a8b-96a4-65fac5ce4458"

Write-Host "=== describe-change-set FULL (json) ==="
aws cloudformation describe-change-set `
    --change-set-name "$csId" `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --output json | Out-File -FilePath "c:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\build\_changeset_full.json" -Encoding UTF8
Write-Host "Saved to _changeset_full.json"

Write-Host "=== describe-change-set-hooks ==="
aws cloudformation describe-change-set-hooks `
    --change-set-name "$csId" `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --output json | Out-String | Write-Host
