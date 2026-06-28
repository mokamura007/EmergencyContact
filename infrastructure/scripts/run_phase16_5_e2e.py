"""Phase 16.5 Step 2 E2E 検証 script (ADR-0010 §4.1 Phase 1-9).

実行内容:
    (1) CycleApi Lambda を invoke して Cycle 起動
    (2) SFN execution 完了 polling（30 秒間隔、最大 15 分）
    (3) DDB / S3 から各 Phase 検証クエリ
    (4) ADR-0010 §3.2 マッピング表との突合

期待される ADR §3.2 結果:
    末尾 0/1/2: SAFE (transcript: 無事 / 大丈夫 / 無事)
    末尾 3/4:   INJURED (transcript: 怪我 / 痛い)
    末尾 5/6:   UNAVAILABLE (transcript: 動け / 出社不可)
    末尾 7:     NO_ANSWER → UNREACHABLE (録音なし)
    末尾 8:     BUSY → UNREACHABLE (録音なし)
    末尾 9:     RECORDED OTHER → UNREACHABLE (transcript: あいうえお)

第 19 原則 (b): エラーは raise そのまま、フォールバック禁止。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# --- 設定 ----------------------------------------------------------------

ENV = "dev"
ACCOUNT = "214046906694"
REGION = "ap-northeast-1"
PROFILE = os.environ.get("AWS_PROFILE") or "AWS-security-check"

CYCLE_API_FN = f"safety-confirmation-cycle-api-{ENV}"
STATE_MACHINE_ARN = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:safety-confirmation-cycle-{ENV}"
CYCLE_TABLE = f"Cycle-{ENV}"
RESPONSE_TABLE = f"Response-{ENV}"
RECORDINGS_BUCKET = f"safety-confirmation-recordings-{ENV}-{ACCOUNT}-{REGION}"
TRANSCRIPTS_BUCKET = f"safety-confirmation-transcripts-{ENV}-{ACCOUNT}-{REGION}"

IDEMPOTENCY_KEY = f"phase-16-5-mock-e2e-{int(time.time())}"

POLL_INTERVAL_SEC = 20
POLL_MAX_RETRIES = 45  # 20s * 45 = 900s = 15 min

# ADR-0010 §3.2 期待値（末尾 → 期待 Voice_Status）
EXPECTED_BY_DIGIT: dict[str, str] = {
    "0": "SAFE",
    "1": "SAFE",
    "2": "SAFE",
    "3": "INJURED",
    "4": "INJURED",
    "5": "UNAVAILABLE",
    "6": "UNAVAILABLE",
    "7": "UNREACHABLE",
    "8": "UNREACHABLE",
    "9": "UNREACHABLE",
}

EXPECTED_RECORDING_DIGITS = {"0", "1", "2", "3", "4", "5", "6", "9"}  # 7 件
EXPECTED_NO_RECORDING_DIGITS = {"7", "8"}  # NO_ANSWER / BUSY


# --- 実装 ---------------------------------------------------------------


def _session() -> boto3.Session:
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def step_2_2_invoke_cycle_api(session: boto3.Session) -> str:
    """Step 2.2: CycleApi Lambda invoke で Cycle 起動。"""
    print(f"[Step 2.2] CycleApi Lambda invoke (Idempotency-Key={IDEMPOTENCY_KEY})")
    lam = session.client("lambda")
    body = json.dumps({"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 1})
    event = {
        "httpMethod": "POST",
        "resource": "/cycles",
        "path": "/cycles",
        "body": body,
        "headers": {"Idempotency-Key": IDEMPOTENCY_KEY, "Content-Type": "application/json"},
        "pathParameters": None,
        "requestContext": {"authorizer": {"claims": {"sub": "phase16-5-script"}}},
    }
    resp = lam.invoke(
        FunctionName=CYCLE_API_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    payload_raw = resp["Payload"].read().decode("utf-8")
    print(f"[Step 2.2] response: statusCode={resp['StatusCode']}")
    print(f"[Step 2.2] payload: {payload_raw}")
    payload = json.loads(payload_raw)
    if "FunctionError" in resp:
        raise RuntimeError(
            f"Lambda invoke FunctionError={resp['FunctionError']}: {payload_raw}"
        )
    # API Gateway proxy format: {statusCode, body, headers}
    inner_status = payload.get("statusCode")
    if inner_status not in (200, 201, 202):
        raise RuntimeError(f"CycleApi POST failed: statusCode={inner_status} body={payload.get('body')}")
    body_obj = json.loads(payload["body"])
    cycle_id = body_obj["cycleId"]
    print(f"[Step 2.2] => cycleId={cycle_id} status={body_obj.get('status')} dictionaryVersion={body_obj.get('dictionaryVersion')}")
    return cycle_id


def step_2_3_poll_sfn(session: boto3.Session, cycle_id: str) -> dict[str, Any]:
    """Step 2.3: SFN execution 完了 polling。"""
    execution_arn = f"arn:aws:states:{REGION}:{ACCOUNT}:execution:safety-confirmation-cycle-{ENV}:cycle-{cycle_id}"
    sfn = session.client("stepfunctions")
    print(f"[Step 2.3] polling SFN execution: {execution_arn}")
    print(f"[Step 2.3] poll interval={POLL_INTERVAL_SEC}s, max_retries={POLL_MAX_RETRIES}")
    for i in range(POLL_MAX_RETRIES):
        time.sleep(POLL_INTERVAL_SEC)
        try:
            resp = sfn.describe_execution(executionArn=execution_arn)
        except Exception as exc:
            print(f"[Step 2.3] retry {i+1}/{POLL_MAX_RETRIES} error: {exc}")
            continue
        status = resp["status"]
        elapsed = (i + 1) * POLL_INTERVAL_SEC
        print(f"[Step 2.3] retry {i+1}/{POLL_MAX_RETRIES} elapsed={elapsed}s status={status}")
        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            return resp
    raise TimeoutError(f"SFN execution did not complete within {POLL_MAX_RETRIES * POLL_INTERVAL_SEC}s")


def step_2_4_verify(session: boto3.Session, cycle_id: str) -> dict[str, Any]:
    """Step 2.4: 検証クエリ。"""
    print(f"[Step 2.4] verifying cycleId={cycle_id}")
    ddb = session.resource("dynamodb")
    s3 = session.client("s3")

    # (a) Cycle.status
    cycle_resp = ddb.Table(CYCLE_TABLE).get_item(Key={"cycleId": cycle_id})
    cycle_item = cycle_resp.get("Item", {})
    print(f"[Step 2.4 a] Cycle.status = {cycle_item.get('status')}")
    print(f"[Step 2.4 a] Cycle.summary = {cycle_item.get('summary')}")

    # (b) Response.voiceStatus per employee
    resp_table = ddb.Table(RESPONSE_TABLE)
    resp_query = resp_table.query(KeyConditionExpression=Key("cycleId").eq(cycle_id))
    responses = resp_query.get("Items", [])
    print(f"[Step 2.4 b] Response items = {len(responses)} (expected 10)")
    response_by_emp = {r["employeeId"]: r for r in responses}

    # (c) S3 RecordingsBucket scan
    rec_resp = s3.list_objects_v2(Bucket=RECORDINGS_BUCKET, Prefix=f"recordings/{cycle_id}/")
    rec_keys = [obj["Key"] for obj in rec_resp.get("Contents", [])]
    print(f"[Step 2.4 c] RecordingsBucket scan: {len(rec_keys)} objects (expected 7-8)")
    for k in rec_keys:
        print(f"[Step 2.4 c]   {k}")

    # (d) S3 TranscriptsBucket scan (outbound/ prefix)
    tr_resp = s3.list_objects_v2(Bucket=TRANSCRIPTS_BUCKET, Prefix=f"outbound/{cycle_id}/")
    tr_keys = [obj["Key"] for obj in tr_resp.get("Contents", [])]
    print(f"[Step 2.4 d] TranscriptsBucket scan: {len(tr_keys)} objects (expected 7-8)")
    for k in tr_keys:
        print(f"[Step 2.4 d]   {k}")

    return {
        "cycle": cycle_item,
        "responses": response_by_emp,
        "recording_keys": rec_keys,
        "transcript_keys": tr_keys,
    }


def step_2_5_match(verify_result: dict[str, Any]) -> dict[str, Any]:
    """Step 2.5: ADR-0010 §3.2 期待値と突合。"""
    print(f"\n[Step 2.5] ADR-0010 §3.2 突合")
    print("=" * 80)
    print(f"{'employeeId':12s} | {'digit':5s} | {'expected':12s} | {'actual':12s} | result")
    print("-" * 80)

    ok = 0
    ng = 0
    for digit, expected in sorted(EXPECTED_BY_DIGIT.items()):
        emp_id = f"EMP-000{digit}"
        resp = verify_result["responses"].get(emp_id, {})
        actual = resp.get("voiceStatus", "<missing>")
        result = "OK" if actual == expected else "NG"
        if result == "OK":
            ok += 1
        else:
            ng += 1
        print(f"{emp_id:12s} | {digit:5s} | {expected:12s} | {str(actual):12s} | {result}")
    print("-" * 80)
    print(f"summary: {ok} OK / {ng} NG / total 10")

    cycle_status = verify_result["cycle"].get("status")
    print(f"\nCycle.status: {cycle_status} ({'OK' if cycle_status == 'COMPLETED' else 'NG'})")
    print(f"Recording count: {len(verify_result['recording_keys'])} (expected ~7)")
    print(f"Transcript count: {len(verify_result['transcript_keys'])} (expected ~7)")

    return {
        "voice_status_ok": ok,
        "voice_status_ng": ng,
        "cycle_status": cycle_status,
        "recording_count": len(verify_result["recording_keys"]),
        "transcript_count": len(verify_result["transcript_keys"]),
    }


def main() -> int:
    print(f"=== Phase 16.5 Step 2 E2E Validation ===")
    print(f"Profile={PROFILE} Region={REGION} Env={ENV}")
    print(f"IdempotencyKey={IDEMPOTENCY_KEY}")
    print(f"Start: {_dt.datetime.now(_dt.UTC).isoformat()}Z")
    print()

    session = _session()

    # Step 2.2: Cycle 起動（CYCLE_ID 環境変数があれば既存を再利用）
    existing_cycle_id = os.environ.get("CYCLE_ID")
    if existing_cycle_id:
        print(f"[Step 2.2] reuse existing CYCLE_ID={existing_cycle_id}")
        cycle_id = existing_cycle_id
    else:
        cycle_id = step_2_2_invoke_cycle_api(session)

    # Step 2.3: SFN 完了待機
    sfn_result = step_2_3_poll_sfn(session, cycle_id)
    sfn_status = sfn_result["status"]
    print(f"\n[Step 2.3 final] SFN status = {sfn_status}")
    if sfn_status != "SUCCEEDED":
        print(f"[Step 2.3 final] SFN output: {sfn_result.get('output', '<none>')}")
        print(f"[Step 2.3 final] SFN cause: {sfn_result.get('cause', '<none>')}")
        print(f"[Step 2.3 final] SFN error: {sfn_result.get('error', '<none>')}")

    # Step 2.4: 検証クエリ
    verify_result = step_2_4_verify(session, cycle_id)

    # Step 2.5: 突合
    summary = step_2_5_match(verify_result)

    print()
    print(f"=== Phase 16.5 Step 2 E2E DONE ===")
    print(f"cycleId={cycle_id}")
    print(f"summary: {summary}")
    print(f"End: {_dt.datetime.now(_dt.UTC).isoformat()}Z")

    # 結果を JSON で保存
    out_path = "backend/_tmp_e2e_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cycleId": cycle_id,
                "idempotencyKey": IDEMPOTENCY_KEY,
                "sfnStatus": sfn_status,
                "cycleStatus": verify_result["cycle"].get("status"),
                "cycleSummary": verify_result["cycle"].get("summary"),
                "responsesByEmployee": {
                    k: {kk: vv for kk, vv in v.items() if kk in ("voiceStatus", "callResultCode", "callAttempts", "matchedKeywords")}
                    for k, v in verify_result["responses"].items()
                },
                "recordingKeys": verify_result["recording_keys"],
                "transcriptKeys": verify_result["transcript_keys"],
                "summary": summary,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    print(f"Result saved: {out_path}")

    return 0 if summary["voice_status_ng"] == 0 and summary["cycle_status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
