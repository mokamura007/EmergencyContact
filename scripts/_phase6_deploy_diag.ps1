$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"

Write-Host "=== Latest ChangeSet ==="
$csList = aws cloudformation list-change-sets `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "Summaries[-1:].[ChangeSetName,ChangeSetId,Status,StatusReason,CreationTime]" `
    --output json
$csList | Out-String | Write-Host

# Get the latest changeset id
$csId = (aws cloudformation list-change-sets `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "Summaries[-1].ChangeSetId" `
    --output text)
Write-Host "csId=$csId"

Write-Host "=== ChangeSet detail (full describe-change-set) ==="
aws cloudformation describe-change-set `
    --change-set-name "$csId" `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "{Status:Status,StatusReason:StatusReason,ExecutionStatus:ExecutionStatus}" `
    --output json | Out-String | Write-Host

Write-Host "=== Recent stack events (last 10) ==="
aws cloudformation describe-stack-events `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "StackEvents[:10].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" `
    --output json | Out-String | Write-Host

Write-Host "=== Stack status now ==="
aws cloudformation describe-stacks `
    --stack-name safety-confirmation-dev `
    --profile AWS-security-check `
    --region ap-northeast-1 `
    --query "Stacks[0].StackStatus" `
    --output text | Out-String | Write-Host
