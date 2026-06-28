# Task 14.7a — RecordingApi / TranscriptApi 90 日経過時 410 Gone 応答検証

**作成日**: 2026-06-27 セッション継続
**spec**: `safety-confirmation-system`
**対象タスク**: tasks.md 14.7a（元タスク 14.7 から切出、Bii 方針）
**Requirements**: 10.7, 12.3, 13.7, Property 23
**Design**: Recording_Store / インタフェース / Property 23

---

## 1. 背景

元タスク 14.7「統合テスト：録音 / Transcript 90 日 LCM」は以下の 2 部構成。

| 部  | 内容                                 | 必要環境                             |
| --- | ------------------------------------ | ------------------------------------ |
| (a) | LCM 1 日短縮 → 24 時間後自動削除確認 | 実 S3 オブジェクト + 実 Connect 録音 |
| (b) | 90 日超過時 410 Gone 応答            | メタデータ書換 or 時刻 mock          |

Bii 方針（Connect 非依存範囲で実運用品質に到達）に従い、本タスク 14.7a は **(b) のみ** を実施。(a) は元タスク 14.7 に残置し、ADR-0009 §3 完了後に実 Connect 環境で実施する。

---

## 2. 実施項目

### 2.1 検証方式

タスク本文記載の選択肢のうち **(i) Local moto/boto3 stubber + handler 直接呼出** を採用。  
ただし既存テストパターン（`backend/tests/lambdas/recording_api/test_handler.py`）が `unittest.mock.MagicMock` + `monkeypatch` で DDB / S3 / 時刻取得を全置換する DRY 形になっていたため、moto 導入は不要と判断（第 19 原則 (a) DRY 原則）。

実装方式：

- `handler._CYCLE_TABLE` / `handler._INBOUND_TABLE` / `handler._S3` → `MagicMock` で `monkeypatch.setattr`
- `handler.now_iso_utc` → 固定 ISO 文字列を返す lambda で `monkeypatch.setattr`（アンカー時刻 `2024-01-15T12:00:00Z`）
- 4 境界点を `pytest.mark.parametrize` で網羅、4 エンドポイント × 4 境界 = **16 ケース**

### 2.2 既存実装との整合確認

| 観点                 | 確認結果                                                                                                                                                                                                                      |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 判定式               | `backend/shared/recording/expiry.py` の `can_issue_url`：`(now - ref) <= timedelta(days=90)` → True なら 200、False なら 410                                                                                                  |
| 設計図書整合         | design.md Property 23「`now - t_start ≤ 90 日` のときに限り true」と完全一致                                                                                                                                                  |
| 4 エンドポイント実装 | `lambdas/recording_api/handler.py` で `_cycle_artifact × 2`（recordings / transcripts）+ `_inbound_artifact × 2`（recording / transcript）の 4 ルートを実装、共通の `can_issue_url(reference, now_iso_utc())` 経由で 410 判定 |
| 410 レスポンスボディ | `error`（"90-day" を含むメッセージ）+ `cycleId`/`contactId` + `startedAt`/`receivedAt`。本テストで全フィールドを検証                                                                                                          |

### 2.3 新規ファイル

- `backend/tests/lambdas/recording_api/test_lifecycle_410.py`（新規、16 ケース）

---

## 3. 境界条件テスト結果

各境界点における `ref = now - delta` の delta と、観測された HTTP status の対応表。

### 3.1 境界点定義

| ラベル                   | delta（now と ref の差）                    | 期待 status  | 根拠                            |
| ------------------------ | ------------------------------------------- | ------------ | ------------------------------- |
| `exactly_90_days`        | 90 日ちょうど                               | **200 OK**   | 判定式は `<= 90日` で inclusive |
| `90_days_minus_1_second` | 90 日 - 1 秒（= 89日 23:59:59、わずか若い） | **200 OK**   | window 内                       |
| `90_days_plus_1_second`  | 90 日 + 1 秒（= 90日 0:00:01、わずか古い）  | **410 Gone** | window 外                       |
| `91_days`                | 91 日ちょうど                               | **410 Gone** | window 外                       |

タスク本文の表現「`now - 90 日 = 200 OK` / `now - 90 日 + 1 秒 = 200 OK` / `now - 91 日 = 410 Gone` / `now - 90 日 - 1 秒 = 410 Gone`」を ref-anchor で読み替えた形（ref が若い側 → 短い delta → 200、ref が古い側 → 長い delta → 410）。

### 3.2 16 ケース チェックリスト

#### (A) GET /cycles/{id}/recordings/{employeeId}/{seq}

- [x] `test_cycle_recording_boundary[exactly_90_days]` → 200 OK（presigned URL 発行、bucket=RecordingsBucket、key=`cycles/cycle-1/emp-001#1.wav`、TTL=900s）
- [x] `test_cycle_recording_boundary[90_days_minus_1_second]` → 200 OK
- [x] `test_cycle_recording_boundary[90_days_plus_1_second]` → 410 Gone（error に "90-day" 含む、cycleId・startedAt 返却、S3 未呼出）
- [x] `test_cycle_recording_boundary[91_days]` → 410 Gone

#### (B) GET /cycles/{id}/transcripts/{employeeId}/{seq}

- [x] `test_cycle_transcript_boundary[exactly_90_days]` → 200 OK（bucket=TranscriptsBucket、key=`cycles/cycle-1/emp-001#1.json`）
- [x] `test_cycle_transcript_boundary[90_days_minus_1_second]` → 200 OK
- [x] `test_cycle_transcript_boundary[90_days_plus_1_second]` → 410 Gone
- [x] `test_cycle_transcript_boundary[91_days]` → 410 Gone

#### (C) GET /inbound/{contactId}/recording

- [x] `test_inbound_recording_boundary[exactly_90_days]` → 200 OK（bucket=RecordingsBucket、key=`inbound/contact-abc.wav`）
- [x] `test_inbound_recording_boundary[90_days_minus_1_second]` → 200 OK
- [x] `test_inbound_recording_boundary[90_days_plus_1_second]` → 410 Gone（error "90-day"、contactId・receivedAt 返却）
- [x] `test_inbound_recording_boundary[91_days]` → 410 Gone

#### (D) GET /inbound/{contactId}/transcript

- [x] `test_inbound_transcript_boundary[exactly_90_days]` → 200 OK（bucket=TranscriptsBucket、key=`inbound/contact-abc.json`）
- [x] `test_inbound_transcript_boundary[90_days_minus_1_second]` → 200 OK
- [x] `test_inbound_transcript_boundary[90_days_plus_1_second]` → 410 Gone
- [x] `test_inbound_transcript_boundary[91_days]` → 410 Gone

**合計**: 16 / 16 件 PASS。

### 3.3 実行コマンドと出力（要約）

```powershell
cd backend
$env:PYTHONUTF8="1"
uv run pytest tests/lambdas/recording_api/test_lifecycle_410.py -v
```

出力末尾：

```
============= 16 passed in 0.95s ==============
```

---

## 4. 累積テスト件数差分

| 項目                | Before |                          After | 差分 |
| ------------------- | -----: | -----------------------------: | ---: |
| backend pytest 件数 | 877 件 |                     **893 件** |  +16 |
| frontend 件数       | 286 件 |             286 件（変更なし） |   ±0 |
| 既存テスト破壊      |      — | **なし**（全 877 件継続 PASS） |    — |

全件実行確認：

```powershell
cd backend
$env:PYTHONUTF8="1"
uv run pytest tests/ --tb=short -q
```

出力末尾：

```
893 passed in 44.07s
```

---

## 5. 推奨追加検証（dev 環境 curl 実機検証）

本タスクのスコープ外（オプション）。15.2a の placeholder deploy が完了している前提で、実 API Gateway 経由の 410 応答を確認する場合の手順を以下に残す。

### 5.1 前提

- dev 環境 RecordingApi / TranscriptApi が deploy 済み
- Cognito 管理者ユーザでログイン可能、ID トークン取得済み
- CycleTable / InboundContactTable に書込権限を持つ AWS Profile（`AWS-security-check`）

### 5.2 手順（概略）

1. ダミーレコード投入（`startedAt = now - 91 days`）：

   ```powershell
   $env:AWS_PROFILE = "AWS-security-check"
   $now = [DateTimeOffset]::UtcNow
   $expired = $now.AddDays(-91).ToString("yyyy-MM-ddTHH:mm:ssZ")
   aws dynamodb put-item `
     --table-name Cycle-dev `
     --item "{\""cycleId\"":{\""S\"":\""test-cycle-410\""},\""startedAt\"":{\""S\"":\""$expired\""}}" `
     --region ap-northeast-1
   ```

2. RecordingApi `/cycles/{id}/recordings/{employeeId}/{seq}` への GET：

   ```powershell
   $token = "<管理者の Cognito ID トークン>"
   $apiBase = "https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/dev"
   curl.exe -i -H "Authorization: Bearer $token" `
     "$apiBase/cycles/test-cycle-410/recordings/emp-001/1"
   # 期待: HTTP/1.1 410 Gone, body に "90-day" を含む
   ```

3. 同様に他 3 エンドポイント（transcripts / inbound recording / inbound transcript）を確認。

4. クリーンアップ：
   ```powershell
   aws dynamodb delete-item `
     --table-name Cycle-dev `
     --key "{\""cycleId\"":{\""S\"":\""test-cycle-410\""}}" `
     --region ap-northeast-1
   ```

### 5.3 実施判断

- 14.7a の Done When（4 エンドポイントの 410 Gone 境界条件検証 + 結果記録）は本ドキュメントで充足済み
- 実機検証は **元タスク 14.7（実 S3 LCM + 実 Connect 録音）と統合実施** が DRY（API Gateway 経由の認証 + 90 日判定 + LCM の三点同時確認）
- 単独実施するメリット：API Gateway Cognito Authorizer + Lambda の組合せ動作確認、CORS / エラーボディフォーマットの実機確認
- 推奨：別タスク化、または 14.7 の一部として残置

---

## 6. tasks.md 14.7a 状態

- Done When：**充足**
  - [x] 4 エンドポイントの 410 Gone 応答が境界条件含め確認
  - [x] テスト結果が `docs/notes/14-7a-410-validation.md` に記録
- orchestrator 側で `tasks.md` の `- [ ] 14.7a` → `- [x] 14.7a` への変更を推奨

---

## 7. 第 7 原則ズレ検知

なし。

- 判定式（`(now - ref) <= 90日`）は requirements.md / design.md Property 23 / `shared/recording/expiry.py` の三者で完全一致
- 既存テスト（91日 / 95日 → 410、5〜45日 → 200）は本テストの 91日 / 90日+1秒 → 410 と整合
- handler の 4 ルート実装が requirements 10.7 / 12.3 / 13.7 の対象（cycle 系 2 ルート + inbound 系 2 ルート）と完全対応

---

## 8. 関連ファイル

- 新規：`backend/tests/lambdas/recording_api/test_lifecycle_410.py`
- 参照：
  - `backend/lambdas/recording_api/handler.py`
  - `backend/shared/recording/expiry.py`
  - `backend/tests/lambdas/recording_api/test_handler.py`（既存 happy path / 91d / 95d / 404 ケース）
  - `backend/tests/shared/recording/test_expiry_property23.py`（既存 PBT、純粋関数レベル）
  - `.kiro/specs/safety-confirmation-system/tasks.md` 14.7 / 14.7a
  - `.kiro/specs/safety-confirmation-system/design.md` Property 23 / Recording_Store
  - `.kiro/specs/safety-confirmation-system/requirements.md` Requirement 10 / 12 / 13
