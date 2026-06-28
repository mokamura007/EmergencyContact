# CloudFormation Package / Deploy Pipeline

The CloudFormation template references local source paths
(`AWS::Lambda::Function.CodeUri`, `AWS::Lambda::LayerVersion.Content`).
These local paths must be **zipped and uploaded to S3** by
`aws cloudformation package` before deployment.

## Prerequisites

1. **S3 deploy bucket** (one-time setup, region = ap-northeast-1):

   ```pwsh
   aws s3 mb s3://safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1 `
       --region ap-northeast-1
   aws s3api put-bucket-versioning `
       --bucket safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1 `
       --versioning-configuration Status=Enabled
   ```

   This bucket is **not managed by the application stack** — it is the
   bootstrap container for stack artifacts (Lambda ZIPs, Layer ZIPs).

2. **uv environment** is active (`backend/.venv/Scripts/activate`).

3. **AWS CLI** v2 is installed and credentials configured.

## Build & Deploy Sequence

```pwsh
# 1. Stage SharedLayer content (backend/shared/ -> infrastructure/build/layers/shared/python/shared/)
pwsh -File ./scripts/build_layer.ps1

# 2. Package: rewrite local paths in template.yaml -> S3 references in packaged.yaml
aws cloudformation package `
    --template-file infrastructure/template.yaml `
    --s3-bucket safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1 `
    --output-template-file infrastructure/build/packaged.yaml `
    --region ap-northeast-1

# 3. Deploy
aws cloudformation deploy `
    --template-file infrastructure/build/packaged.yaml `
    --stack-name safety-confirmation-dev `
    --capabilities CAPABILITY_NAMED_IAM `
    --parameter-overrides `
        EnvironmentName=dev `
        ConnectInstanceId=<id> `
        ConnectInstanceArn=<arn> `
        ConnectOutboundPhoneNumberArn=<arn> `
        ConnectInboundPhoneNumberArn=<arn> `
        OutboundContactFlowId=<id> `
        InboundContactFlowId=<id> `
        OperatorEmail=<email> `
    --region ap-northeast-1
```

## What `aws cloudformation package` Rewrites

The CLI walks the template and rewrites the following property values when
they are local paths, uploading the directory contents (zipped) to S3:

- `AWS::Lambda::Function.Code` → `{ S3Bucket, S3Key }`
- `AWS::Lambda::LayerVersion.Content` → `{ S3Bucket, S3Key }`
- `AWS::Serverless::Function.CodeUri` (SAM transform, not used here)

Other properties (e.g. `Resource` ARNs, intrinsic functions) are left
untouched.

## .gitignore

The staging tree `infrastructure/build/` is git-ignored. It is regenerated
by `build_layer.ps1` on each deploy.
