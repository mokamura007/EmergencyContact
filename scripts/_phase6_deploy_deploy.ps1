$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
aws cloudformation deploy `
    --template-file "c:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\build\packaged.yaml" `
    --stack-name safety-confirmation-dev `
    --s3-bucket safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1 `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset `
    --profile AWS-security-check `
    --region ap-northeast-1
Write-Host "EXIT:$LASTEXITCODE"
