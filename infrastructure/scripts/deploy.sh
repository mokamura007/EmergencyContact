#!/usr/bin/env bash
# 指定環境（dev/stg/prod）に safety-confirmation スタックをデプロイする。
#
# Task 15.1 で整備されたデプロイスクリプト（bash 版）。
#
# 使用法:
#   bash infrastructure/scripts/deploy.sh <env> [--dry-run] [--no-execute-changeset] [--skip-build-layer]
#
# 例:
#   bash infrastructure/scripts/deploy.sh dev --dry-run
#   bash infrastructure/scripts/deploy.sh dev
#
# Exit:
#   0  : 成功
#   2  : 引数不正 / 入力ファイル不在
#   非0: aws CLI 失敗

set -euo pipefail

# --- 引数パース ---
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <env> [--dry-run] [--no-execute-changeset] [--skip-build-layer]" >&2
    exit 2
fi

ENVIRONMENT_NAME="$1"
shift

DRY_RUN=0
NO_EXECUTE_CHANGESET=0
SKIP_BUILD_LAYER=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)              DRY_RUN=1 ;;
        --no-execute-changeset) NO_EXECUTE_CHANGESET=1 ;;
        --skip-build-layer)     SKIP_BUILD_LAYER=1 ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

case "$ENVIRONMENT_NAME" in
    dev|stg|prod) ;;
    *)
        echo "EnvironmentName must be one of dev|stg|prod (got: $ENVIRONMENT_NAME)" >&2
        exit 2
        ;;
esac

# --- パス確定 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INFRA_DIR="${REPO_ROOT}/infrastructure"
TEMPLATE_PATH="${INFRA_DIR}/template.yaml"
PACKAGED_PATH="${INFRA_DIR}/packaged-template.yaml"
PARAMS_PATH="${INFRA_DIR}/parameters/${ENVIRONMENT_NAME}.json"
BUILD_LAYER_PS1="${REPO_ROOT}/scripts/build_layer.ps1"

STACK_NAME="safety-confirmation-${ENVIRONMENT_NAME}"
S3_BUCKET="safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1"
AWS_REGION="ap-northeast-1"
# AWS CLI Profile: 環境変数 AWS_PROFILE が設定されていればそれを使用、未設定なら既定値
# (Task 15.11、NFR5：AssumeRole / 別アカウント検証 / 個人開発環境での運用柔軟性のため)
AWS_PROFILE="${AWS_PROFILE:-AWS-security-check}"

export PYTHONUTF8=1

echo "=== deploy.sh START ==="
echo "Environment        : ${ENVIRONMENT_NAME}"
echo "StackName          : ${STACK_NAME}"
echo "TemplatePath       : ${TEMPLATE_PATH}"
echo "ParamsPath         : ${PARAMS_PATH}"
echo "PackagedPath       : ${PACKAGED_PATH}"
echo "AwsProfile         : ${AWS_PROFILE}"
echo "DryRun             : ${DRY_RUN}"
echo "NoExecuteChangeset : ${NO_EXECUTE_CHANGESET}"
echo "SkipBuildLayer     : ${SKIP_BUILD_LAYER}"

# --- Step 1: 入力ファイル存在チェック ---
echo
echo "--- Step 1: Validate inputs ---"
[[ -f "${TEMPLATE_PATH}" ]] || { echo "ERROR: Template not found: ${TEMPLATE_PATH}" >&2; exit 2; }
[[ -f "${PARAMS_PATH}"   ]] || { echo "ERROR: Parameters file not found: ${PARAMS_PATH}" >&2; exit 2; }

# JSON 構文チェック（python があれば使う、なければスキップ）
if command -v python >/dev/null 2>&1; then
    python -c "import json,sys; d=json.load(open(r'${PARAMS_PATH}', encoding='utf-8')); assert isinstance(d, list); [(_['ParameterKey'], _['ParameterValue']) for _ in d]"
    echo "JSON syntax OK"
elif command -v jq >/dev/null 2>&1; then
    jq -e 'type == "array" and all(.[]; has("ParameterKey") and has("ParameterValue"))' "${PARAMS_PATH}" >/dev/null
    echo "JSON syntax OK (via jq)"
else
    echo "WARN: python / jq not found; skipping JSON syntax validation" >&2
fi

# EnvironmentName 一致チェック
if command -v python >/dev/null 2>&1; then
    PARAM_ENV=$(python -c "import json; d=json.load(open(r'${PARAMS_PATH}', encoding='utf-8')); print(next((p['ParameterValue'] for p in d if p['ParameterKey']=='EnvironmentName'), ''))")
    if [[ "${PARAM_ENV}" != "${ENVIRONMENT_NAME}" ]]; then
        echo "ERROR: EnvironmentName mismatch: arg=${ENVIRONMENT_NAME} parameters=${PARAM_ENV}" >&2
        exit 2
    fi
fi

# --- Step 2: SharedLayer staging (PowerShell script) ---
if [[ "${SKIP_BUILD_LAYER}" -eq 0 ]]; then
    echo
    echo "--- Step 2: build_layer.ps1 (SharedLayer staging) ---"
    [[ -f "${BUILD_LAYER_PS1}" ]] || { echo "ERROR: ${BUILD_LAYER_PS1} not found" >&2; exit 2; }
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[DryRun] would invoke: pwsh -NoProfile -File ${BUILD_LAYER_PS1}"
    else
        pwsh -NoProfile -File "${BUILD_LAYER_PS1}"
    fi
else
    echo
    echo "(Skipping build_layer.ps1 per --skip-build-layer)"
fi

# --- Step 3: aws cloudformation package ---
echo
echo "--- Step 3: aws cloudformation package ---"
PACKAGE_CMD=(
    aws cloudformation package
    --template-file "${TEMPLATE_PATH}"
    --s3-bucket "${S3_BUCKET}"
    --output-template-file "${PACKAGED_PATH}"
    --region "${AWS_REGION}"
    --profile "${AWS_PROFILE}"
)
echo "Command: ${PACKAGE_CMD[*]}"
if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[DryRun] skipping aws cloudformation package"
else
    "${PACKAGE_CMD[@]}"
fi

# --- Step 4: aws cloudformation deploy ---
echo
echo "--- Step 4: aws cloudformation deploy ---"
DEPLOY_CMD=(
    aws cloudformation deploy
    --template-file "${PACKAGED_PATH}"
    --stack-name "${STACK_NAME}"
    --parameter-overrides "file://${PARAMS_PATH}"
    --capabilities CAPABILITY_NAMED_IAM
    --region "${AWS_REGION}"
    --profile "${AWS_PROFILE}"
)
if [[ "${NO_EXECUTE_CHANGESET}" -eq 1 ]]; then
    DEPLOY_CMD+=(--no-execute-changeset)
fi
echo "Command: ${DEPLOY_CMD[*]}"
if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[DryRun] skipping aws cloudformation deploy"
    echo
    echo "=== deploy.sh END (DryRun, OK) ==="
    exit 0
fi

set +e
"${DEPLOY_CMD[@]}"
DEPLOY_EXIT=$?
set -e

if [[ "${DEPLOY_EXIT}" -ne 0 ]]; then
    echo
    echo "--- Failure: dumping last 10 stack events ---" >&2
    aws cloudformation describe-stack-events \
        --stack-name "${STACK_NAME}" \
        --region "${AWS_REGION}" --profile "${AWS_PROFILE}" \
        --max-items 10 \
        --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' \
        --output table >&2 || true
    echo "ERROR: aws cloudformation deploy failed (exit=${DEPLOY_EXIT})" >&2
    exit "${DEPLOY_EXIT}"
fi

echo
echo "=== deploy.sh END (OK) ==="
exit 0
