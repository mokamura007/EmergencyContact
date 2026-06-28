$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
aws cloudformation package `
    --template-file "c:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\template.yaml" `
    --s3-bucket safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1 `
    --output-template-file "c:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\build\packaged.yaml" `
    --profile AWS-security-check `
    --region ap-northeast-1
Write-Host "EXIT:$LASTEXITCODE"
