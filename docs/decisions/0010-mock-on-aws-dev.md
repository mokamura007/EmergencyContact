---
title: AWS dev 環境上の Connect/Transcribe Mock 経路
status: Accepted
date: 2026-06-28
supersedes: 部分的 ADR-0005 §5（Outbound 1 巡 E2E に限り AWS dev 環境で mock を許容）
extends: ADR-0009（Connect 購入前の中間ステップとして位置づける、ADR-0009 §3〜§4 の実 E2E は引き続き別途実施）
---

# ADR-0010: AWS dev 環境上の Connect/Transcribe Mock 経路（Outbound 1 巡 E2E）

- ステータス: **Accepted**（§6 全 11 項目合意取得済、2026-06-28 セッション 20 にユーザー承認）
- 決定日: 2026-06-28（起票 + 同日 Accepted 遷移）
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md`（Req 5.1 / 6.x / 9.x / NFR3）、`.../design.md`（Connect_Caller / Voice_Transcriber / KeywordMatcher）、`.../tasks.md`（Phase 16 新規起票予定）
- 関連 ADR: [`docs/decisions/0001-runtime-selection.md`](./0001-runtime-selection.md)、[`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md)（§5 方針を **部分的に上書き**、§6.1 保留条項は ADR-0009 で解除済）、[`docs/decisions/0009-connect-realworld-validation.md`](./0009-connect-realworld-validation.md)（**本 ADR の前提**、Connect 購入は ADR-0009 で合意済 / 実 E2E は ADR-0009 §4 で別途実施）
- 関連運用ドキュメント: [`docs/operations/deploy.md`](../operations/deploy.md)、[`docs/operations/runbook.md`](../operations/runbook.md)

---

## 1. コンテキスト

### 1.1 既存 ADR が定めている方針

[ADR-0005](./0005-connect-mock-findings.md) §5 は「Connect 関連 Lambda の **ユニットテスト** は `unittest.mock.patch` で boto3 client を差し替える方式に統一する」と決定している。同 ADR §7.2 は更に **採用範囲外** として「実機 Amazon Connect インスタンスの先行購入」「`moto` / LocalStack の導入」を明示。すなわち ADR-0005 の射程は「**ローカル pytest 内の単体テスト**」に限定されていた。

[ADR-0009](./0009-connect-realworld-validation.md) §1.1 / §2 / §3 はその後継として「**実 Amazon Connect インスタンス購入 + 実機検証**」を採用し、ADR-0005 §6.1 の保留条項を解除している。ADR-0009 §3.1〜§3.6 は事前準備手順（Step 1〜6）を逐次規定し、§4.1 で Phase 14.1〜14.11 の検証範囲を表形式で明示している。

### 1.2 本 ADR の動機

ADR-0009 §3.1「Step 1: Amazon Connect インスタンスの購入」は **ユーザー手動作業** であり、AI / 自動エージェントは関与しない（ADR-0009 §2.1 / §5.3）。

`docs/notes/_progress.md` の現状記録によれば、AI / 自動エージェントは Connect 非依存範囲を全て消化済（Phase 15.27 系 4 件完了、本日達成）。次の自然な進路として、Connect インスタンス購入を待たずに **AWS dev 環境上で実 Lambda / 実 SFN / 実 DynamoDB / 実 S3 を用いた Outbound 1 巡 E2E** を確認することがプロジェクトの停滞を防ぐ。

既存の SLA 系統合テスト資産 `backend/tests/integration/test_sla_300_mock.py` はローカル pytest 内のみで動作する in-memory fakes ベースであり、AWS dev 環境にデプロイされた実 Lambda の挙動を検証できない。

このことからこう考えます。本 ADR は **新規 mock 経路** として、AWS dev 環境上で実 Lambda / 実 SFN / 実 DynamoDB / 実 S3 を使用し、課金が大きい Connect / Transcribe のみを Lambda 内 env 分岐で mock 化する経路を確立する。これは ADR-0005 §5 が想定していなかった射程（ローカル pytest を超えた AWS dev 環境上の mock）であり、§5 方針を **部分的に上書き** する。

### 1.3 本 ADR のスコープ境界

| 項目                                                                                                                                                                                       | 本 ADR 内外                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Outbound 1 巡 E2E（Cycle 起動 → SFN → ConnectDispatcher mock → S3 擬似 wav → TranscribeStarter mock → 擬似 transcript JSON → KeywordMatcher → Response 更新 → CycleFinalizer → COMPLETED） | **本 ADR スコープ内**                                                  |
| Inbound 着信（NOT_REGISTERED / NO_CYCLE / ACTIVE_CYCLE / CYCLE_TERMINATED の 4 分岐）                                                                                                      | **本 ADR スコープ外**（§9.1 で後日対応）                               |
| 実 Connect 発信 / 実 Polly TTS / 実録音 / 実 Transcribe ジョブ                                                                                                                             | **本 ADR スコープ外**（ADR-0009 §4 で別途実施、本 ADR で代替されない） |
| stg / prod 環境での mock 動作                                                                                                                                                              | **本 ADR スコープ外**（§3.4 prod ガードで強制 OFF）                    |

### 1.4 本 ADR の前提となるユーザー判断（インタビュー確定済）

| 確認事項            | ユーザー回答                                                                                                         |
| ------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Q1 検証範囲         | **(β) Outbound 1 巡 + Transcribe も mock**（実 Transcribe 課金回避、擬似 transcript JSON を S3 直接投入）            |
| Q2 mock 組込方式    | **(a) 本番 Lambda 内に env 分岐追加**（実装小、ConnectDispatcher / TranscribeStarter に env `MOCK_MODE` で if 分岐） |
| Q3 擬似応答パターン | **(ii) employeeId 末尾文字で決定論的に分岐**（§3.2 マッピング表）                                                    |
| Q4 進め方           | **(1) ADR-0010 起票 → tasks.md 新規起票 → 実装**                                                                     |

## 2. 決定

| 項目                            | 値                                                                                                                                                    |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 採用範囲                        | **Outbound 1 巡 E2E** のみ（§1.3 / §4.1）                                                                                                             |
| 採用範囲外（本 ADR スコープ外） | Inbound 着信 / 実 Connect 発信 / 実 Transcribe ジョブ（§1.3 / §4.2）                                                                                  |
| mock 組込方式                   | **本番 Lambda 内に env `MOCK_MODE` 分岐追加**（§3.1）                                                                                                 |
| 対象 Lambda                     | `ConnectDispatcher`（Phase 6.2）、`TranscribeStarter`（Phase 6.4）（§3.1）                                                                            |
| 擬似応答パターン                | **employeeId 末尾文字で決定論的に分岐**（§3.2 マッピング表）                                                                                          |
| 純粋関数の配置                  | `backend/shared/connect/mock.py` に `derive_mock_response` を切出、PBT 候補（§3.2）                                                                   |
| 環境制御方式                    | **CFn Parameter `MockMode`**（`AllowedValues=["true","false"]`、`Default="false"`）+ Lambda env `MOCK_MODE` 注入（§3.3）                              |
| prod 環境での強制 OFF           | **2 段防御**（CFn AllowedValues + handler 内 `EnvironmentName=prod` チェック）、任意で 3 段目（CFn Rules）（§3.4）                                    |
| 擬似 wav 投入経路               | S3 RecordingsBucket に PutObject、既存 `TranscribeStarterEventRule` を流用（§3.5 / §3.6）                                                             |
| 擬似 transcript JSON 投入経路   | S3 TranscriptsBucket に PutObject、既存 `KeywordMatcherEventRule` を流用（§3.5 / §3.6）                                                               |
| TaskToken 経路の擬似化          | ConnectDispatcher mock mode 内で **SFN SendTaskSuccess 直接呼出**（CallEndHandler 経路を擬似、§3.7）                                                  |
| ADR-0005 §5 との関係            | **Outbound 1 巡 E2E に限り部分的上書き**。Inbound と stg/prod は引き続き ADR-0005 §5 方針を継続（§8.1）                                               |
| ADR-0009 §3〜§4 との関係        | **本 ADR は ADR-0009 を否定しない**。実 Connect E2E は ADR-0009 §4 で引き続き別途実施、本 ADR は Connect 購入前の中間ステップとして位置づける（§8.1） |

### 2.1 採用しない方針とその理由

| 方針                                                                     | 不採用理由                                                                                                                                                                                |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (α) ConnectDispatcher のみ mock、Transcribe は実ジョブ                   | Transcribe ja-JP は音声時間単位課金（ADR-0009 §5.1）が発生し、開発中の反復確認コストが許容できない。本 ADR では Transcribe も擬似 transcript JSON 直接投入で課金ゼロを維持                |
| (b) **別 Lambda（MockConnectDispatcher / MockTranscribeStarter）を新設** | 本番 Lambda と mock Lambda の divergence リスクが本ハンドラ内 env 分岐より高い。CFn Resources が二重化し保守性も低下。`MOCK_MODE` の if 分岐が最も実装小                                  |
| (i) 完全乱数で擬似応答を生成                                             | 再現性がなく、テスト failure 時の原因特定が困難。employeeId 末尾文字での決定論的分岐（ii）は同じ employeeId に対して常に同じ擬似応答が返るため、Phase 16.5 E2E 確認時のシナリオ設計が容易 |
| (iii) 別 DynamoDB テーブルで擬似応答マッピングを管理                     | データソースが増え、CFn Resources / 投入運用が複雑化。決定論的マッピングは純粋関数で十分（DRY、§3.2）                                                                                     |
| (γ) Connect 購入を待たず Inbound mock も並行                             | Inbound は外部から着信する設計上、mock の現実感が低い（§9.1）。本 ADR は **段階的に進める** ため Outbound のみに絞る                                                                      |

## 3. 設計詳細

### 3.1 mock 対象 Lambda

| Lambda                                                                            | mock 化対象 API                     | mock 動作                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------------------------------------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ConnectDispatcher`（`backend/lambdas/connect_dispatcher/handler.py`、Phase 6.2） | `connect:StartOutboundVoiceContact` | (1) 擬似 ContactId 生成（`mock-{cycleId}-{employeeId}-{attempt}` 等）、(2) 擬似 wav（1 KB placeholder バイト列）を S3 RecordingsBucket に PutObject、(3) Response テーブルに `contactId` / `dispatchedAt` 書込 + `callAttempts` インクリメント、(4) 末尾 0/1/2/3/4/5/6/9 は `RECORDED` 系で SFN は **TaskToken 経路を CallEndHandler 同等で完結**（§3.7）、(5) 末尾 7/8 は `NO_ANSWER` / `BUSY` で SendTaskSuccess に `{"retry": true, ...}` を返す |
| `TranscribeStarter`（`backend/lambdas/transcribe_starter/handler.py`、Phase 6.4） | `transcribe:StartTranscriptionJob`  | (1) 擬似 transcript JSON を TranscriptsBucket に PutObject、(2) employeeId 末尾文字で `transcript` テキスト確定（§3.2）、(3) TranscriptMetadata テーブルに `transcribeJobId` / `transcriptS3Key` を書込（既存 `_record_transcript_meta` 経路を流用）、(4) Transcribe API は呼ばないので KMS 暗号化や OutputBucketName 指定は擬似 PutObject 側で対応                                                                                                 |

mock mode 時には Connect API / Transcribe API の env（`CONNECT_INSTANCE_ID` / `OUTBOUND_CONTACT_FLOW_ID` / `OUTBOUND_PHONE_NUMBER` / `TRANSCRIBE_LANGUAGE_CODE` 等）が空文字列でも handler が起動できるよう、handler 内 module top の `os.environ["..."]` 強取得を mock mode 時のみ緩和する必要がある（§3.4 実装ノート参照）。

### 3.2 擬似応答パターンマッピング（推奨案、§6 で確定）

| employeeId 末尾文字 | 通話結果コード（`callResultCode`） | KeywordMatcher 判定後 `Voice_Status`          | 擬似 transcript 内容例                  |
| ------------------- | ---------------------------------- | --------------------------------------------- | --------------------------------------- |
| 0, 1, 2             | `RECORDED`                         | `SAFE`                                        | 「無事です」「大丈夫です」              |
| 3, 4                | `RECORDED`                         | `INJURED`                                     | 「怪我をしました」「痛いです」          |
| 5, 6                | `RECORDED`                         | `UNAVAILABLE`                                 | 「動けません」「出社不可です」          |
| 7                   | `NO_ANSWER`                        | `UNREACHABLE`（リトライ上限到達後）           | -（無応答なので録音 / transcript なし） |
| 8                   | `BUSY`                             | `UNREACHABLE`（リトライ上限到達後）           | -（話中なので録音 / transcript なし）   |
| 9                   | `RECORDED`                         | `OTHER` → `UNREACHABLE`（リトライ上限到達後） | 「あいうえお」（辞書非該当）            |

擬似応答決定ロジックは純粋関数 `derive_mock_response(employee_id: str) -> tuple[str, str | None]` として `backend/shared/connect/mock.py` に切出（PBT 候補、Phase 16.1 で実装）。シグネチャ案：

```python
def derive_mock_response(employee_id: str) -> tuple[str, str | None]:
    """employeeId 末尾文字から (callResultCode, transcript or None) を返す純粋関数。

    Returns:
        (callResultCode, transcript_text or None)
        - callResultCode: "RECORDED" / "NO_ANSWER" / "BUSY"
        - transcript_text: callResultCode == "RECORDED" のときのみ非 None
    """
```

末尾文字が 0〜9 以外の場合（UUID 等）の動作は §6.2.1 で確定。デフォルト案：**末尾を 10 進数として hash → mod 10 で 0〜9 に正規化**（決定論性を保ちつつ任意の employeeId に対応）。

### 3.3 CFn Parameter `MockMode` 定義

```yaml
Parameters:
  MockMode:
    Type: String
    AllowedValues: ["true", "false"]
    Default: "false"
    Description: |
      ConnectDispatcher / TranscribeStarter mock mode for dev only.
      必ず false を prod へ deploy すること（§3.4 prod ガード参照）。
      dev 環境で Connect 購入前の Outbound 1 巡 E2E 確認に使用。
```

- `parameters/dev.json` で `"MockMode": "true"` を明示
- `parameters/stg.json` / `parameters/prod.json` で `"MockMode": "false"` を明示
- ConnectDispatcher / TranscribeStarter の `Environment.Variables` に `MOCK_MODE: !Ref MockMode` を注入
- ConnectDispatcher は SFN SendTaskSuccess を直接呼ぶ経路のため CallEndHandler への env 注入は不要（§3.7）

### 3.4 prod 環境ガード（2 段防御 + 任意の 3 段目）

handler module top に追加するパターン（DRY 化のため共有ヘルパ化候補）：

```python
import os

ENVIRONMENT_NAME = os.environ["ENVIRONMENT_NAME"]  # 既存 env を新規に注入
_MOCK_MODE_ENABLED = (
    os.environ.get("MOCK_MODE", "false").lower() == "true"
    and ENVIRONMENT_NAME != "prod"
)
```

| 防御層          | 実装場所                                    | 内容                                                                                                                  |
| --------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 第 1 段         | CFn Parameter `MockMode` の `AllowedValues` | `"true"` / `"false"` の文字列制約（任意の文字列を排除）                                                               |
| 第 2 段         | handler 内 `_MOCK_MODE_ENABLED` 算出時      | `ENVIRONMENT_NAME == "prod"` なら強制 `False`（CFn parameters/prod.json が誤って `MockMode=true` でも実行時に無効化） |
| 第 3 段（任意） | CFn `Rules` セクション                      | `EnvironmentName=prod` かつ `MockMode=true` の組合せを deploy 時に拒否（`AssertDescription` で明示）                  |

第 3 段の CFn Rules 案：

```yaml
Rules:
  ProdMockModeForbidden:
    RuleCondition: !Equals [!Ref EnvironmentName, "prod"]
    Assertions:
      - Assert: !Equals [!Ref MockMode, "false"]
        AssertDescription: "MockMode は prod 環境で 'false' でなければならない（ADR-0010 §3.4）"
```

第 3 段の採否は §6.3.3 で確定。**推奨**：採用する（deploy 失敗で気付ける方が事後障害より低リスク）。

### 3.5 擬似 wav / transcript 投入経路

#### 3.5.1 擬似 wav

- ConnectDispatcher mock mode：S3 RecordingsBucket に `recordings/{cycleId}/{employeeId}/{seq}.wav` の S3 key で PutObject
- wav の中身：**1 KB の placeholder バイト列**で十分（既存 EventBridge ルール起動が目的、Transcribe を呼ばないので中身は問わない）
- 投入後、既存 EventBridge ルール `TranscribeStarterEventRule`（`template.yaml` L2365、Phase 6.4 で実装済）が S3 ObjectCreated を検知 → TranscribeStarter mock mode を起動
- `recordings/` prefix にマッチするため、既存 EventBridge `EventPattern` の改修は不要

#### 3.5.2 擬似 transcript JSON

- TranscribeStarter mock mode：TranscriptsBucket に `transcripts/{cycleId}/{employeeId}/{seq}.json` の S3 key で PutObject
- JSON の構造：実 Transcribe ジョブの output JSON 形式（`results.transcripts[0].transcript` と `results.items[]`）に従う
- 内容：`derive_mock_response(employeeId)` の戻り値 transcript_text を `results.transcripts[0].transcript` に投入。`results.items[]` は最小構成（`type: "pronunciation"` + `alternatives.[0].content` + `start_time` / `end_time`）で 1 トークンのみ
- 投入後、既存 EventBridge ルール `KeywordMatcherEventRule`（`template.yaml` L3101、Phase 8.1 で実装済）が S3 ObjectCreated を検知 → KeywordMatcher を起動

擬似 transcript JSON のテンプレ案：

```json
{
  "jobName": "mock-{cycleId}-{employeeId}-{seq}",
  "accountId": "214046906694",
  "results": {
    "transcripts": [{ "transcript": "{擬似 transcript テキスト}" }],
    "items": [
      {
        "start_time": "0.0",
        "end_time": "1.0",
        "alternatives": [
          { "confidence": "1.0", "content": "{擬似 transcript テキスト}" }
        ],
        "type": "pronunciation"
      }
    ]
  },
  "status": "COMPLETED"
}
```

### 3.6 既存 EventBridge ルール流用

| 既存リソース                                                   | 流用方法                                                                          | 改修要否                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TranscribeStarterEventRule`（template.yaml L2365、Phase 6.4） | RecordingsBucket への擬似 wav PutObject を検知 → TranscribeStarter 起動           | **改修不要**（`recordings/` prefix にマッチ）                                                                                                                                                                                                                                       |
| `KeywordMatcherEventRule`（template.yaml L3101、Phase 8.1）    | TranscriptsBucket への擬似 transcript JSON PutObject を検知 → KeywordMatcher 起動 | **改修不要**（`transcripts/` / `inbound/` prefix にマッチ。Phase 16.3 実装で擬似 transcript の S3 key prefix を実装の `parse_recording_key` 戻り値 `transcripts/{cycleId}/{employeeId}/{seq}.json` に揃え済、Phase 16.5 実機検証で 10 件 `transcripts/{cycleId}/...` 投入を確認済） |

副次発見：`KeywordMatcherEventRule` の prefix は template.yaml L3120 で `transcripts/` + `inbound/`、`TranscribeStarter._record_transcript_meta` が書込む `transcript_s3_key` の実 prefix は `parse_recording_key` の戻り値（アウトバウンドは `transcripts/`）に依存。Phase 16.3 実装で mock 投入 key prefix を実 prefix に揃え済（**Phase 17.3 で本 ADR の `outbound/` 表記を `transcripts/` に修正、2026-06-28 セッション 22**）。

### 3.7 CallEndHandler の TaskToken 経路の擬似化（重要）

SFN State Machine は通常、ConnectDispatcher 後に「`Dispatch` 状態が `.waitForTaskToken` で待機 → CallEndHandler が `SendTaskSuccess` で送信」のパターン（template.yaml + Phase 6.8 ASL 参照）。

mock mode では Connect を介さないため CallEndHandler 経路は使えない。ConnectDispatcher 既存実装には `_send_retry_task_success`（handler.py L249）として **SFN SendTaskSuccess を直接呼ぶパターン** が既に存在する（retry exhausted 時の `{"retry": True, "reason": "DISPATCH_FAILED"}` 送出）。

mock mode では同じパターンを **正常系で再利用** する：

- ConnectDispatcher mock mode の末尾 0〜6 / 9：擬似 ContactId + 擬似 wav PutObject 完了後、SFN SendTaskSuccess を呼出し output payload に `{"retry": false, "contactId": <擬似>, "callResultCode": "RECORDED"}` を送出
- ConnectDispatcher mock mode の末尾 7（NO_ANSWER）/ 8（BUSY）：擬似 wav 投入なし、Response テーブルに `callResultCode = "NO_ANSWER"` or `"BUSY"` を書込、SFN SendTaskSuccess に `{"retry": true, "callResultCode": <擬似>}` を送出（リトライ判定は本番経路の `RetryEvaluator` が処理、リトライ上限到達で `UNREACHABLE` 確定）

output payload は CallEndHandler の正常終了時と同形を維持し、SFN ASL の改修は不要。

#### 3.7.1 SFN State Machine 定義との整合性

ConnectDispatcher は元々 `.waitForTaskToken` で `taskToken` を event に受け取る前提（`_parse_event` の `_REQUIRED_INPUT_KEYS` に `taskToken` あり）。mock mode でも同じ event スキーマを使うため、SFN State Machine 定義（`infrastructure/state-machines/cycle.asl.json` 等）の改修は不要。

### 3.8 CFn Resource 変更点（実装サマリ、Phase 16 タスクへの引継ぎ）

| Resource                                       | 変更内容                                                                                                                                                                                                    |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Parameters.MockMode`                          | **新規追加**（§3.3）                                                                                                                                                                                        |
| `Rules.ProdMockModeForbidden`                  | **新規追加（任意）**（§3.4 第 3 段）                                                                                                                                                                        |
| `ConnectDispatcherFn.Environment.Variables`    | `MOCK_MODE: !Ref MockMode`、`ENVIRONMENT_NAME: !Ref EnvironmentName` を追加                                                                                                                                 |
| `TranscribeStarterFn.Environment.Variables`    | `MOCK_MODE: !Ref MockMode`、`ENVIRONMENT_NAME: !Ref EnvironmentName` を追加                                                                                                                                 |
| `ConnectDispatcherFnExecutionRole.Policies`    | S3 RecordingsBucket への `s3:PutObject` 権限を追加（mock mode での擬似 wav 投入用）                                                                                                                         |
| `TranscribeStarterFnExecutionRole.Policies`    | S3 TranscriptsBucket への `s3:PutObject` 権限を追加（mock mode での擬似 transcript JSON 投入用、既存 Transcribe → S3 出力で OutputBucketName 経由の権限はあるが、Lambda 直接 PutObject 用の権限は別途必要） |
| `parameters/dev.json`                          | `"MockMode": "true"` を追加                                                                                                                                                                                 |
| `parameters/stg.json` / `parameters/prod.json` | `"MockMode": "false"` を追加                                                                                                                                                                                |

IAM 追加権限は本番経路（実 Connect / 実 Transcribe）でも害がない範囲（PutObject 単体）に留め、ADR-0003 KMS CMK 設計と矛盾しない（既存 ViaService 制約を維持）。

## 4. 検証範囲と完了条件

### 4.1 検証範囲（Outbound 1 巡）

| Phase                     | 検証項目                                      | 完了条件                                                                                                                    |
| ------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 1. SPA ログイン           | 管理者として Cognito 認証                     | dev 環境にログイン成功（Phase 15.2a で確認済の場合は skip 可）                                                              |
| 2. Cycle 起動             | SPA から Cycle 起動ボタン押下 / API 直叩き    | SFN StartExecution 成功、Cycle `status=RUNNING`                                                                             |
| 3. LoadTargets            | 対象社員抽出                                  | 想定 N 名（5〜10 名想定）が Map に投入                                                                                      |
| 4. ConnectDispatcher mock | 擬似 wav 投入 + Response 書込                 | 各社員の Response に擬似 `callResultCode` が書込まれる（末尾 0〜6 / 9 → `RECORDED`、末尾 7 → `NO_ANSWER`、末尾 8 → `BUSY`） |
| 5. TranscribeStarter mock | 擬似 transcript JSON 投入                     | TranscriptsBucket に N 件の JSON が投入される（末尾 7 / 8 は投入されない）                                                  |
| 6. KeywordMatcher         | 擬似 transcript から `Voice_Status` 判定      | Response に `Voice_Status` が書込まれる（末尾 0〜2 → `SAFE`、3〜4 → `INJURED`、5〜6 → `UNAVAILABLE`、9 → `OTHER`）          |
| 7. RetryEvaluator         | `NO_ANSWER` / `BUSY` / `OTHER` のリトライ判定 | 末尾 7 / 8 / 9 の社員が 3 回リトライ後 `UNREACHABLE` 確定                                                                   |
| 8. CycleFinalizer         | MAP_COMPLETED で Cycle 完了                   | Cycle `status=COMPLETED`、SLA 30 分 / 60 分メトリクス未発火                                                                 |
| 9. ResponseApi            | SPA から Cycle 詳細取得                       | 全社員の `Voice_Status` が SPA に表示される                                                                                 |

### 4.2 採用範囲外（本 ADR スコープ外）

- Inbound 着信（NOT_REGISTERED / NO_CYCLE / ACTIVE_CYCLE / CYCLE_TERMINATED の 4 分岐、§9.1 で後日対応）
- 実 Connect 発信 / 実 Polly TTS / 実録音 / 実 Transcribe ジョブ（ADR-0009 §4 で別途実施、本 ADR で代替されない）
- 90 日 LCM / 410 Gone（Phase 14.7a で確認済）
- 60 分タイムアウト（Phase 14.5 / ADR-0009 で別途実施）

### 4.3 検証完了の最終基準

- §4.1 Phase 1〜9 すべての完了条件達成
- `docs/notes/_progress.md` 末尾に Phase 16.5 の実機検証ログ（実行日時 / 結果 / 課金実績概算）を記録
- 本 ADR の §6 合意チェックリストに ✅ が全項目入る
- 不合格項目があれば別チケット起票（`tasks.md` に新規 Phase 17 起票 or `_progress.md` 副次発見メモ）

## 5. 課金影響と責任境界

| サービス              | 課金影響                                                                                          | 責任     |
| --------------------- | ------------------------------------------------------------------------------------------------- | -------- |
| AWS Lambda            | 実 invocation 課金（micro、5〜10 invocation/cycle 想定）                                          | ユーザー |
| AWS Step Functions    | 実 state transition 課金（無料枠内見込）                                                          | ユーザー |
| Amazon DynamoDB       | 実 read/write 課金（PAY_PER_REQUEST、micro）                                                      | ユーザー |
| Amazon S3             | 実 PutObject / GetObject 課金（micro、擬似 wav 1 KB × 数十件）                                    | ユーザー |
| AWS CloudWatch Logs   | 実 ingestion + storage 課金（micro）                                                              | ユーザー |
| Amazon SNS            | OperatorEmail Subscription Confirmation（Phase 12.4 で送信済）+ Cycle 完了通知（実メール、micro） | ユーザー |
| **Amazon Connect**    | **使わない**（`StartOutboundVoiceContact` 未呼出）                                                | 課金ゼロ |
| **Amazon Transcribe** | **使わない**（`StartTranscriptionJob` 未呼出）                                                    | 課金ゼロ |

### 5.1 責任境界（ADR-0009 §5 と一貫）

- 料金確認はユーザー責任、本 ADR 内で具体的な金額は扱わない
- AI / 自動エージェントの Connect / Transcribe 自動購入禁止（本 ADR では購入自体が不要）
- CFn deploy は y/n 承認必須（第 6 原則）
- 想定総額：CFn deploy 1 回 + 1 サイクル 10 名 mock 実行で **数円〜数十円程度**（ADR-0009 §6.1 と同じ責任境界、金額記載なし、ユーザー責任で確認）

### 5.2 AI / 自動エージェントの責任範囲

| 項目                                           | 対応                                                                        |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| 擬似 wav / 擬似 transcript JSON の Lambda 実装 | AI 実装可（Phase 16.1〜16.3）                                               |
| CFn Parameter / parameters/\*.json 変更        | AI 実装可、ただし第 6 原則の y/n を取る（Phase 16.4）                       |
| dev 環境 deploy                                | AI が `deploy.ps1 -EnvironmentName dev` を実行、第 6 原則 y/n（Phase 16.5） |
| dev 環境 deploy 後の検証実行                   | AI が SFN StartExecution / DDB GetItem 等で確認可、第 6 原則 y/n            |
| prod / stg への deploy                         | **本 ADR スコープ外**、別 ADR / 別タスクで判断                              |

## 6. 合意チェックリスト（ユーザー記入、推奨 11 項目）

本 ADR を **Accepted** に遷移させるには、以下のチェック項目すべてに ✅ が入る必要がある。各項目はユーザーが手動で記入する。**現時点は Proposed、未記入のままで起票**。

**記入日**: 2026-06-28（セッション 20、AI 推奨案を採用）

### 6.1 mock 対象 Lambda / TaskToken 経路（2 項目）

- [x] 6.1.1 mock 対象 Lambda（`ConnectDispatcher` / `TranscribeStarter`）の方針同意 — **承認根拠**: 既存 `_send_retry_task_success`（handler.py L249）が SFN SendTaskSuccess 直接呼出パターンを実装済、mock 経路に流用可能で新規追加コード最小限
- [x] 6.1.2 CallEndHandler の TaskToken 経路擬似化（`ConnectDispatcher` 内で SFN `SendTaskSuccess` 直接呼出）の方針同意 — **承認根拠**: 既存パターン流用、SFN ASL 改修不要、CallEndHandler 自体は ADR-0009 §4 実 Connect E2E で別途確認する設計と一貫

### 6.2 擬似応答パターン（2 項目）

- [x] 6.2.1 擬似応答パターン §3.2 マッピング表の同意（末尾文字が 0〜9 以外の場合のデフォルト動作含む） — **承認根拠**: 末尾 0〜9 以外は SHA-256 hash 最下位バイト mod 10 で正規化（決定論性保持、UUID employeeId 対応、PBT 全網羅可能）
- [x] 6.2.2 純粋関数 `derive_mock_response` の `backend/shared/connect/mock.py` への配置同意 — **承認根拠**: 既存 `shared/connect/backoff.py` / `call_result.py` と一貫、DRY 原則 19(a) に整合

### 6.3 CFn Parameter / prod ガード（3 項目）

- [x] 6.3.1 CFn Parameter `MockMode` 定義（`Type=String` / `AllowedValues=["true","false"]` / `Default="false"`）同意 — **承認根拠**: 文字列制約 + 安全デフォルト（false）、parameters/dev.json で明示的に true を投入
- [x] 6.3.2 prod 環境ガード 2 段防御（handler 内 `EnvironmentName=prod` チェック）同意 — **承認根拠**: 多層防御の基本、CFn 誤投入と実行時逸脱の両方を防ぐ
- [x] 6.3.3 CFn `Rules` セクションで `prod + MockMode=true` を拒否する 3 段目防御の要否（任意、**推奨：採用**） — **承認根拠**: 採用（CFn Rules で prod+MockMode=true を deploy 時拒否）— deploy 失敗で気付ける方が事後障害より低リスク、ADR §3.4 第 3 段案を実装

### 6.4 擬似 wav / transcript（2 項目）

- [x] 6.4.1 擬似 wav 投入経路（S3 PutObject + 既存 `TranscribeStarterEventRule` 流用）同意 — **承認根拠**: 既存 `TranscribeStarterEventRule` の `recordings/` prefix にマッチ、EventBridge ルール改修不要
- [x] 6.4.2 擬似 transcript JSON 構造（実 Transcribe output JSON 形式に従う）同意 — **承認根拠**: 実 Transcribe output JSON 形式（`results.transcripts[0].transcript` + `results.items[]` 最小構成）採用、KeywordMatcher が改修不要で受理

### 6.5 課金許容（1 項目）

- [x] 6.5.1 課金許容（§5 の極小課金）同意、ユーザー責任明示 — **承認根拠**: ADR-0009 §5 と一貫の責任境界、極小課金（数円〜数十円）、Connect/Transcribe 課金ゼロ

### 6.6 ADR-0005 §5 上書き（1 項目）

- [x] 6.6.1 ADR-0005 §5「Mock は単体テストレベルに限定」方針の本 ADR による **部分的上書き** 同意（Outbound 1 巡 E2E に限り AWS dev で許容、Inbound と stg/prod は引き続き ADR-0005 §5 方針継続） — **承認根拠**: Outbound 1 巡 E2E に限定の部分的上書き、Inbound と stg/prod は ADR-0005 §5 継続、影響範囲限定

### 6.7 採用方針メモ（後続セッション参照用、2026-06-28 記入）

| 項目                                  | 採用方針                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 末尾文字が 0〜9 以外の場合の動作      | **SHA-256 hash 最下位バイト mod 10** で 0〜9 に正規化。実装は `hashlib.sha256(employee_id.encode("utf-8")).digest()[0] % 10` を採用。決定論性保持、任意の employeeId（UUID 含む）に対応、PBT で全分岐網羅可能。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 3 段目防御（CFn Rules）の採否         | **採用**。`Rules.ProdMockModeForbidden` を template.yaml に追加し、`EnvironmentName=prod` かつ `MockMode=true` の組合せを deploy 時に拒否。`AssertDescription` で「ADR-0010 §3.4 / §6.3.3 に基づき prod 環境で MockMode=true は禁止」と明示。                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 擬似 transcript JSON の S3 key prefix | **`transcripts/{cycleId}/{employeeId}/{seq}.json`**（`KeywordMatcherEventRule` の `transcripts/` prefix にマッチ、§3.5.2 / §3.6 / §9.5 で明示）。実装は既存 `parse_recording_key` 戻り値（アウトバウンドは `transcripts/`）に揃え済（**Phase 17.3 で本セルの `outbound/` 表記を `transcripts/` に修正、2026-06-28 セッション 22**）。                                                                                                                                                                                                                                                                                                                                                                                     |
| Phase 16 着手時期                     | **ADR-0010 Accepted 遷移直後（本セッション 2026-06-28 以降）からユーザー指示時に着手可能**。Connect インスタンス購入（ADR-0009 §3.1）待ちと並行進行可能。`tasks.md` Phase 16 新規起票は本 ADR Accepted 後の別タスクで実施。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| dev 環境 deploy 時期                  | **Phase 16.1〜16.4 実装完了後、第 6 原則 y/n を都度取る**。`deploy.ps1 -EnvironmentName dev` で `parameters/dev.json` の `"MockMode": "true"` を投入してデプロイ。Phase 16.5 で Outbound 1 巡 E2E 確認を実施。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| その他特記事項                        | (a) CallEndHandler 経路は本 mock 経路では確認されない（ADR-0009 §4 で実 Connect E2E 確認）。(b) KeywordMatcher 擬似 transcript と dev 環境 KeywordDictionary*Table*（DynamoDB）のキーワードのドライラン検証は Phase 16.5 deploy 前に実施推奨（`backend/shared/dictionary/` には実キーワードは存在せず `active_count.py` / `snapshot.py` のヘルパのみ。実キーワードは KeywordDictionaryTable + KeywordDictionaryHistoryTable に投入されている。**Phase 17.4 で本セルの誤表記を修正、2026-06-28 セッション 22**）。(c) ADR-0010 完了時点で本 ADR は Accepted、後続 Phase 16 実装は別タスクで進行（本 ADR は計画 / 設計書、実装は tasks.md Phase 16）。(d) Inbound mock は本 ADR §9.1 の通り後日 ADR 改訂 or 別 ADR で対応。 |

## 7. リスクとロールバック

### 7.1 想定リスク

| リスク                                                                    | 確率 | 影響                                  | 対応                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------- | ---- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| prod で `MockMode=true` を誤 deploy                                       | 低   | 大（本番が mock 動作 = 安否確認失敗） | §3.4 2 段防御 + 任意 3 段目（CFn Rules）+ CI で `parameters/prod.json` の `MockMode` を pre-deploy チェック                                                                                                                                                                                            |
| 擬似 wav が S3 ストレージを圧迫                                           | 極低 | 極小                                  | 1 KB × 数十件、既存 90 日 LCM（`RecordingsBucket.LifecycleConfiguration`）で自動削除                                                                                                                                                                                                                   |
| 既存 EventBridge ルール再利用で副作用                                     | 低   | 中                                    | `TranscribeStarterEventRule` の `EventPattern` は mock 投入と実 Connect 録音を区別しないが、dev のみのため本 ADR スコープ外で問題なし                                                                                                                                                                  |
| ConnectDispatcher / TranscribeStarter の mock 経路と本番経路が divergence | 中   | 中                                    | 単体テスト（既存 `unittest.mock` パターン、ADR-0005 §5）+ 本 ADR の mock E2E + 実 Connect E2E（ADR-0009 §4）の **3 段** で検出                                                                                                                                                                         |
| TaskToken 経路の擬似が SFN State Machine 定義と不整合                     | 中   | 中                                    | SFN ASL 改修不要、`ConnectDispatcher` 内で `SendTaskSuccess` を呼ぶことで CallEndHandler 経路を skip する形（§3.7）。output payload schema を CallEndHandler 正常終了時と同形に維持                                                                                                                    |
| `derive_mock_response` の末尾文字 0〜9 以外の動作未定義                   | 中   | 小                                    | §6.2.1 で確定（hash + mod 10 推奨）。PBT で全 employeeId 入力に対して必ず `(callResultCode, transcript)` を返すことを保証                                                                                                                                                                              |
| KeywordMatcher が擬似 transcript で誤判定                                 | 低   | 小                                    | §3.2 マッピング表の transcript テキストは KeywordDictionary（dev DynamoDB に投入される SAFE / INJURED / UNAVAILABLE カテゴリ）と一致するワードを採用、Phase 16.5 deploy 前にローカル `classify_voice_status` × dev DDB scan 辞書のドライラン検証を実施（**Phase 17.4 / 16.5 セッション 21 で実施済**） |

### 7.2 ロールバック手順

#### 7.2.1 dev 環境で問題発生時

1. `parameters/dev.json` の `"MockMode"` を `"true"` → `"false"` に変更
2. `deploy.ps1 -EnvironmentName dev` で再 deploy
3. mock 経路無効化（本番経路へ戻る）
4. ただし実 Connect Arn 未投入の場合、`StartOutboundVoiceContact` が失敗する想定（5xx エラー）→ ADR-0009 §3 の Step 1〜4 完了が前提

#### 7.2.2 stg / prod への影響

- stg / prod は元から `MockMode=false`（§3.3）+ 2 段防御（§3.4）のため、本 ADR のロールバックでは **影響なし**
- 万一 stg / prod で `MockMode=true` が deploy された場合、handler 内第 2 段防御で実行時無効化、または第 3 段（CFn Rules）で deploy 自体が拒否される

#### 7.2.3 mock コードの完全削除

将来 mock 経路が不要になった場合：

1. `backend/shared/connect/mock.py` 削除
2. `ConnectDispatcher` / `TranscribeStarter` handler 内の `_MOCK_MODE_ENABLED` 分岐削除
3. CFn `Parameters.MockMode` 削除、`parameters/*.json` から `MockMode` キー削除
4. IAM Role の S3 PutObject 権限が本番経路で不要なら削除
5. `deploy.ps1 -EnvironmentName dev` で再 deploy → mock コード完全撤去

## 8. 採用範囲・影響

### 8.1 採用範囲

| 区分                     | 対象                                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| 本 ADR の対象環境        | **dev 環境限定**（stg / prod は §3.4 で強制 OFF）                                                                |
| 本 ADR の対象シナリオ    | **Outbound 1 巡 E2E**（§4.1）                                                                                    |
| ADR-0005 §5 との関係     | **部分的上書き**（Outbound 1 巡 E2E に限定、Inbound と stg/prod は引き続き ADR-0005 §5 方針継続）                |
| ADR-0009 §3〜§4 との関係 | **本 ADR は ADR-0009 を否定しない**（実 Connect E2E は引き続き別途実施、本 ADR は Connect 購入前の中間ステップ） |

### 8.2 影響を受ける後続タスク

新規起票予定タスク（`tasks.md` Phase 16 として、本 ADR Accepted 後に起票）：

| タスク | 概要                                                                                                                                        |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 16.1   | `derive_mock_response` 純粋関数実装 + PBT（`backend/shared/connect/mock.py`、`backend/tests/shared/connect/test_mock.py`）                  |
| 16.2   | `ConnectDispatcher` mock 分岐実装（`MOCK_MODE` env 分岐、擬似 ContactId / 擬似 wav PutObject / SFN SendTaskSuccess 直接呼出、IAM 追加権限） |
| 16.3   | `TranscribeStarter` mock 分岐実装（`MOCK_MODE` env 分岐、擬似 transcript JSON PutObject、TranscriptMetadata 書込、IAM 追加権限）            |
| 16.4   | CFn `Parameter MockMode` 追加 + `parameters/{dev,stg,prod}.json` 投入 + 任意の `Rules` セクション追加                                       |
| 16.5   | dev 環境 deploy + Outbound 1 巡 E2E 確認（§4.1 Phase 1〜9）                                                                                 |

ADR-0009 §4.1 / §4.2 Phase 14 系タスクは引き続き実 Connect E2E（本 ADR で代替されない）。本 ADR は **ADR-0009 完了前の中間ステップ** として並行進行可能。

## 9. 残課題と未確定事項

### 9.1 Inbound mock の後日対応

- 本 ADR は Outbound のみスコープ
- Inbound mock（API Gateway 経由で擬似 ContactFlowEvent を `InboundHandler` に投げる、または直接 Lambda Invoke）は別 ADR or 本 ADR 改訂で追加検討
- 優先度：中〜低（Inbound は外部から着信する設計上、mock の現実感が低い。SPA からの操作で擬似 Inbound を生成する経路もない）
- 後日対応の候補設計：
  - (a) `InboundHandler` に `MOCK_MODE` env 分岐追加、API Gateway 経由で擬似 event を投入
  - (b) `aws lambda invoke` で擬似 event を直接 Invoke、API Gateway を介さない
  - (c) AWS CLI から `aws connect put-user-status` 等で Connect インスタンスを擬似する（実 Connect 購入後に限定）

### 9.2 CallEndHandler の TaskToken 経路の詳細

- 本 ADR §3.7 で「`ConnectDispatcher` mock mode 内で SFN `SendTaskSuccess` 直接呼出」と方針記載
- 実装詳細（`CallEndHandler` を呼ぶか、呼ばずに `ConnectDispatcher` が完結させるか）は §6.1.2 / Phase 16.2 実装時に確定
- 既存 `_send_retry_task_success`（handler.py L249）パターンを正常系で再利用する設計を推奨

### 9.3 SPA からの操作

- 本 ADR は **API 直叩きでの 1 巡 E2E を最低要件**（§4.1 Phase 2）
- SPA「Cycle 起動」ボタンからの 1 巡確認は §4.1 Phase 1〜9 に含まれるが、Phase 1（SPA ログイン）は Phase 15.2a で確認済
- SPA からの Cycle 起動が動作しない場合は **API 直叩きに fallback**（curl / aws CLI / Postman 等）

### 9.4 ステータス遷移完了（2026-06-28 セッション 20 末）

本 ADR のステータスは **2026-06-28 セッション 20** にユーザー承認により `Proposed` → `Accepted` へ遷移完了。§6 合意チェックリスト 11 項目すべてに ✅、§6.7 採用方針メモ 6 欄すべて記入済。以降の Phase 16.1〜16.5 実装は本 ADR を根拠として進行可能。

### 9.5 擬似 transcript JSON の S3 key prefix 確定

- §3.5.2 / §3.6 で言及した通り、`KeywordMatcherEventRule` の `EventPattern` prefix は `transcripts/` + `inbound/`
- 既存 `TranscribeStarter._record_transcript_meta` の書込先 prefix は `parse_recording_key` 戻り値に依存（アウトバウンドは `transcripts/`）
- Phase 16.3 実装で mock 投入 key prefix を実 prefix（`transcripts/`）に揃え済、Phase 16.5 実機検証で 10 件投入を確認
- **Phase 17.3 で本節含む ADR §3.5.2 / §3.6 / §6.7 の `outbound/` 表記を `transcripts/` に修正、2026-06-28 セッション 22**

### 9.6 ADR-0009 §6.1 保留条項との関係

ADR-0009 §6.1 「保留条項」は ADR-0009 自身で解除済（ADR-0009 §1 引用部参照）。本 ADR は ADR-0009 を **前提** とするため、ADR-0009 §6.1 の保留条項解除を再度繰り返す必要はない。本 ADR は ADR-0009 の **後続** として位置づけ、ADR-0009 完了前でも並行進行可能。

## 10. 参照

- [`docs/decisions/0001-runtime-selection.md`](./0001-runtime-selection.md) / Python 3.12 + boto3 + Hypothesis 採用
- [`docs/decisions/0003-kms-cmk-staged-rollout.md`](./0003-kms-cmk-staged-rollout.md) / KMS CMK 設計（IAM 権限追加時の整合性確認用）
- [`docs/decisions/0004-handoff-notes-2026-06-25.md`](./0004-handoff-notes-2026-06-25.md) / 議題拡張（料金合意 + 代替案検討）
- [`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md) / **本 ADR で §5 方針を部分的に上書き**
- [`docs/decisions/0006-dictionary-patch-semantics.md`](./0006-dictionary-patch-semantics.md) / KeywordDictionary パッチ運用（KeywordMatcher 擬似 transcript 判定整合性確認用）
- [`docs/decisions/0007-acm-cert-issuance.md`](./0007-acm-cert-issuance.md) / ACM 証明書発行手順
- [`docs/decisions/0008-guardduty-macie-evaluation.md`](./0008-guardduty-macie-evaluation.md) / 検知層の現状整理
- [`docs/decisions/0009-connect-realworld-validation.md`](./0009-connect-realworld-validation.md) / **本 ADR の前提**、Connect 購入は ADR-0009 で合意済、実 E2E は ADR-0009 §4 で別途実施
- `.kiro/specs/safety-confirmation-system/requirements.md` / Requirement 5.1, 6.1, 6.2, 6.6, 9.1〜9.6, 14.x
- `.kiro/specs/safety-confirmation-system/design.md` / Connect_Caller, Voice_Transcriber, KeywordMatcher
- `.kiro/specs/safety-confirmation-system/tasks.md` / Phase 16（新規起票予定）
- `infrastructure/template.yaml` / `ConnectDispatcherFn`（L2073）, `TranscribeStarterFn`（L2323）, `TranscribeStarterEventRule`（L2365）, `KeywordMatcherEventRule`（L3101）, `IsDev` Condition（L280）
- `backend/lambdas/connect_dispatcher/handler.py` / `_send_retry_task_success`（L249、TaskToken 直接呼出パターン）
- `backend/lambdas/transcribe_starter/handler.py` / `_record_transcript_meta`（L237、TranscriptMetadata 書込パターン）
- `backend/shared/recording/s3_keys.py` / `parse_recording_key`, `derive_transcribe_job_name`
- `docs/notes/_progress.md` / Phase 15.27 系完了状況、ADR-0010 起票経緯
- [AWS::Connect::StartOutboundVoiceContact API](https://docs.aws.amazon.com/connect/latest/APIReference/API_StartOutboundVoiceContact.html)
- [AWS::Transcribe::StartTranscriptionJob API](https://docs.aws.amazon.com/transcribe/latest/APIReference/API_StartTranscriptionJob.html)
- [AWS::StepFunctions::SendTaskSuccess API](https://docs.aws.amazon.com/step-functions/latest/apireference/API_SendTaskSuccess.html)
- [Amazon Transcribe Output JSON Format](https://docs.aws.amazon.com/transcribe/latest/dg/how-it-works-output.html)
