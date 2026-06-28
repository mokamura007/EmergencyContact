#!/usr/bin/env bash
# infrastructure/template.yaml をローカル検証する（cfn-lint）。
#
# Task 15.1 で整備されたローカル検証スクリプト（bash 版）。
# AWS API を呼ばずに完結する（オプションで -a / --validate-on-aws 指定時のみ
# S3 経由で aws cloudformation validate-template を呼ぶ）。
#
# Exit:
#   0  : 全検証パス
#   非0: いずれかの検証で失敗
#
# 実行例:
#   bash infrastructure/scripts/validate.sh
#   bash infrastructure/scripts/validate.sh --validate-on-aws

set -euo pipefail

VALIDATE_ON_AWS=0
for arg in "$@"; do
    case "$arg" in
        -a|--validate-on-aws)
            VALIDATE_ON_AWS=1
            ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMPLATE_PATH="${REPO_ROOT}/infrastructure/template.yaml"
S3_BUCKET="safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1"
AWS_REGION="ap-northeast-1"
# AWS CLI Profile: 環境変数 AWS_PROFILE が設定されていればそれを使用、未設定なら既定値
# (Task 15.11、NFR5：AssumeRole / 別アカウント検証 / 個人開発環境での運用柔軟性のため)
AWS_PROFILE="${AWS_PROFILE:-AWS-security-check}"

# Windows + Git Bash: PYTHONUTF8 を強制
export PYTHONUTF8=1

echo "=== validate.sh START ==="
echo "RepoRoot     : ${REPO_ROOT}"
echo "TemplatePath : ${TEMPLATE_PATH}"
echo "AwsProfile   : ${AWS_PROFILE}"

if [[ ! -f "${TEMPLATE_PATH}" ]]; then
    echo "ERROR: Template not found: ${TEMPLATE_PATH}" >&2
    exit 2
fi

# --- Step 1: cfn-lint ---
echo
echo "--- Step 1: cfn-lint ---"
if ! command -v cfn-lint >/dev/null 2>&1; then
    echo "ERROR: cfn-lint not found in PATH" >&2
    echo "Hint: pip install cfn-lint==1.52.0" >&2
    exit 2
fi
# .cfnlintrc は infrastructure/ にあるため cwd をそこに置く
( cd "${REPO_ROOT}/infrastructure" && cfn-lint template.yaml )
echo "cfn-lint OK"

# --- Step 2 (optional): aws validate-template via S3 ---
if [[ "${VALIDATE_ON_AWS}" -eq 1 ]]; then
    echo
    echo "--- Step 2: aws cloudformation validate-template (via S3) ---"
    S3_KEY="validation/template-$(date -u +%Y%m%d-%H%M%S).yaml"
    aws s3 cp "${TEMPLATE_PATH}" "s3://${S3_BUCKET}/${S3_KEY}" \
        --profile "${AWS_PROFILE}" --region "${AWS_REGION}"
    aws cloudformation validate-template \
        --template-url "https://s3.${AWS_REGION}.amazonaws.com/${S3_BUCKET}/${S3_KEY}" \
        --profile "${AWS_PROFILE}" --region "${AWS_REGION}"
else
    echo
    echo "(Skipping aws validate-template; pass --validate-on-aws to enable.)"
fi

echo
echo "=== validate.sh END (OK) ==="
exit 0
