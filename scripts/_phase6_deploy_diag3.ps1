$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"

Write-Host "=== describe-stack-events (full 50 events, looking for hook failure) ==="
aws cloudformation describe-stack-events `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --max-items 50 `
    --query "StackEvents[?Timestamp>='2026-06-25T11:59:00Z'].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason,HookType,HookStatus,HookFailureMode,HookStatusReason]" `
    --output json | Out-String | Write-Host

Write-Host "=== look for any hook events ==="
aws cloudformation describe-stack-events `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --max-items 100 `
    --query "StackEvents[?HookType!=null].[Timestamp,LogicalResourceId,HookType,HookStatus,HookStatusReason]" `
    --output json | Out-String | Write-Host
