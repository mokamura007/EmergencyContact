$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"

Write-Host "=== Existing stack parameters (Connect related) ==="
aws cloudformation describe-stacks `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "Stacks[0].Parameters[?contains(ParameterKey, 'Connect')]" `
    --output json | Out-String | Write-Host

Write-Host "=== All existing parameters ==="
aws cloudformation describe-stacks `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "Stacks[0].Parameters" `
    --output json | Out-String | Write-Host
