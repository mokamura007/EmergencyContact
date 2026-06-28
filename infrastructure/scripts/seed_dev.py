"""Phase 16.5 前提データ投入 seed script (ADR-0010 §4.1 / tasks.md 16.5).

dev 環境 Outbound 1 巡 E2E（mock 経路）の検証前提として、以下を投入する:

- EmployeeTable (Employee-dev): ダミー社員 10 名（末尾文字 0〜9 を網羅）
- KeywordDictionaryTable (KeywordDictionary-dev): 自然日本語キーワード 6 件
  + META.currentVersion を 6 → 7 に更新
- KeywordDictionaryHistoryTable (KeywordDictionaryHistory-dev):
  version=7 のスナップショット 12 件（既存 a/d/v 6 件 + 新規 6 件）

ADR-0010 §3.2 マッピング表との整合（classify_voice_status での判定）:

| 末尾 | 擬似 transcript     | 期待 Voice_Status | 投入辞書での判定                  |
|------|---------------------|-------------------|----------------------------------|
| 0/2  | 「無事です」        | SAFE              | SAFE「無事」マッチ → SAFE ✓      |
| 1    | 「大丈夫です」      | SAFE              | SAFE「大丈夫」マッチ → SAFE ✓    |
| 3    | 「怪我をしました」  | INJURED           | INJURED「怪我」マッチ → INJURED ✓ |
| 4    | 「痛いです」        | INJURED           | INJURED「痛い」マッチ → INJURED ✓ |
| 5    | 「動けません」      | UNAVAILABLE       | UNAVAILABLE「動け」 → UNAVAILABLE ✓ |
| 6    | 「出社不可です」    | UNAVAILABLE       | UNAVAILABLE「出社不可」 → UNAVAILABLE ✓ |
| 9    | 「あいうえお」      | OTHER             | 全カテゴリ非該当 → OTHER ✓        |

末尾 7 (NO_ANSWER) / 8 (BUSY) は録音なし → KeywordMatcher 不発火、
RetryEvaluator が UNREACHABLE 確定。

実行: ``uv run python infrastructure/scripts/seed_dev.py`` (cwd=workspace root)

第 19 原則 (b) フォールバック禁止: エラーは raise してそのまま伝播する。
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

import boto3
from boto3.dynamodb.conditions import Key

# --- 設定 ----------------------------------------------------------------

ENV = "dev"
REGION = "ap-northeast-1"
PROFILE = os.environ.get("AWS_PROFILE") or "AWS-security-check"

EMPLOYEE_TABLE = f"Employee-{ENV}"
DICT_TABLE = f"KeywordDictionary-{ENV}"
HISTORY_TABLE = f"KeywordDictionaryHistory-{ENV}"

NEW_VERSION = 7  # META.currentVersion=6 の次

NOW_ISO = _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z")

# --- 投入データ ----------------------------------------------------------

EMPLOYEES: list[dict[str, str]] = [
    {"id": f"EMP-000{i}", "phone": f"+819012340{i:03d}", "name": f"テスト社員{i}"}
    for i in range(10)
]

NEW_KEYWORDS: list[tuple[str, str]] = [
    ("SAFE", "無事"),
    ("SAFE", "大丈夫"),
    ("INJURED", "怪我"),
    ("INJURED", "痛い"),
    ("UNAVAILABLE", "動け"),
    ("UNAVAILABLE", "出社不可"),
]

# HistoryTable v=7 スナップショット（既存 a/d/v 6 件 + 新規 6 件 = 12 件）。
# 既存 KeywordDictionaryTable の Phase 4 / 13 テスト用 1 文字キーワードを残し、
# 自然日本語キーワードを追加した「合算」スナップショットを v=7 として書く。
SNAPSHOT_V7: list[tuple[str, str]] = [
    ("SAFE", "a"),
    ("SAFE", "d"),
    ("INJURED", "d"),
    ("INJURED", "v"),
    ("UNAVAILABLE", "d"),
    ("UNAVAILABLE", "v"),
] + NEW_KEYWORDS


# --- 実装 ---------------------------------------------------------------


def _session() -> boto3.Session:
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def seed_employees(session: boto3.Session) -> int:
    table = session.resource("dynamodb").Table(EMPLOYEE_TABLE)
    count = 0
    for emp in EMPLOYEES:
        table.put_item(
            Item={
                "employeeId": emp["id"],
                "phoneNumber": emp["phone"],
                "name": emp["name"],
                "isDeleted": False,
                "createdAt": NOW_ISO,
                "updatedAt": NOW_ISO,
            }
        )
        count += 1
    return count


def seed_dictionary(session: boto3.Session) -> int:
    table = session.resource("dynamodb").Table(DICT_TABLE)
    count = 0
    for category, keyword in NEW_KEYWORDS:
        table.put_item(
            Item={
                "category": category,
                "keyword": keyword,
                "version": NEW_VERSION,
                "createdAt": NOW_ISO,
            }
        )
        count += 1
    # META.currentVersion を 7 に update
    table.update_item(
        Key={"category": "META", "keyword": "META"},
        UpdateExpression="SET currentVersion = :v",
        ExpressionAttributeValues={":v": NEW_VERSION},
    )
    return count


def seed_history(session: boto3.Session) -> int:
    table = session.resource("dynamodb").Table(HISTORY_TABLE)
    count = 0
    with table.batch_writer() as batch:
        for category, keyword in SNAPSHOT_V7:
            batch.put_item(
                Item={
                    "version": NEW_VERSION,
                    "categoryKeyword": f"{category}#{keyword}",
                    "category": category,
                    "keyword": keyword,
                    "snapshotAt": NOW_ISO,
                }
            )
            count += 1
    return count


def verify(session: boto3.Session) -> dict[str, int]:
    ddb = session.client("dynamodb")
    emp = ddb.scan(TableName=EMPLOYEE_TABLE, Select="COUNT")
    dct = ddb.scan(TableName=DICT_TABLE, Select="COUNT")
    hist = ddb.query(
        TableName=HISTORY_TABLE,
        KeyConditionExpression="#v = :v",
        ExpressionAttributeNames={"#v": "version"},
        ExpressionAttributeValues={":v": {"N": str(NEW_VERSION)}},
        Select="COUNT",
    )
    return {
        "EmployeeTable_total": emp["Count"],
        "DictTable_total": dct["Count"],
        "HistoryTable_v7": hist["Count"],
    }


def main() -> int:
    print(f"[seed_dev] Profile={PROFILE} Region={REGION} Env={ENV}")
    print(f"[seed_dev] Tables: {EMPLOYEE_TABLE} / {DICT_TABLE} / {HISTORY_TABLE}")
    print(f"[seed_dev] New version: {NEW_VERSION}")
    print()

    session = _session()

    print("[seed_dev] (1) Employee 10 名 投入中...")
    emp_count = seed_employees(session)
    print(f"[seed_dev]     => {emp_count} put_item 完了")

    print("[seed_dev] (2) KeywordDictionary 6 件 投入 + META.currentVersion=7 update 中...")
    dict_count = seed_dictionary(session)
    print(f"[seed_dev]     => {dict_count} put_item + 1 update_item 完了")

    print(f"[seed_dev] (3) KeywordDictionaryHistory v={NEW_VERSION} スナップショット 12 件 投入中...")
    hist_count = seed_history(session)
    print(f"[seed_dev]     => {hist_count} batch put_item 完了")

    print()
    print("[seed_dev] 投入後検証 (scan/query COUNT):")
    counts = verify(session)
    for k, v in counts.items():
        print(f"[seed_dev]   {k} = {v}")

    print()
    print("[seed_dev] DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
