# Phase 16.5 Outbound 1 巡 E2E mock 経路 検証ログ

- 検証日: 2026-06-28（セッション 21）
- 検証範囲: ADR-0010 §4.1 Phase 1〜9（Outbound 1 巡 mock 経路）
- 結果: **9/9 完全達成**（再検証 Cycle `662df8ba-78ff-4feb-83b5-7bf00828bed3` で全項目 OK）
- 完了基準（ADR-0010 §4.3）: 充足
- 関連 ADR: [ADR-0010](../decisions/0010-mock-on-aws-dev.md)
- 関連タスク: tasks.md Phase 16.5

---

## 1. 前提準備（Step 0）

### 1.1 dev 環境状態（検証着手前）

| 項目                          | 検証着手前                                                                                                             | 投入後                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| EmployeeTable                 | **0 名**                                                                                                               | **10 名**（EMP-0000〜EMP-0009、末尾 0〜9 網羅、E.164 phoneNumber、isDeleted=false） |
| KeywordDictionaryTable        | テスト 1 文字キーワード 7 件のみ（SAFE: `a`, `d` / INJURED: `d`, `v` / UNAVAILABLE: `d`, `v` / META.currentVersion=6） | **13 件**（既存 7 件 + 自然日本語 6 件、META.currentVersion=**7**）                 |
| KeywordDictionaryHistoryTable | v=1〜6 unknown                                                                                                         | **v=7 スナップショット 12 件**追加（既存 6 件 + 新規 6 件）                         |
| MockMode Parameter            | Phase 16.4 で template.yaml 追加済、未 deploy                                                                          | template.yaml + parameters/dev.json の `MockMode=true` を deploy 反映               |

### 1.2 投入スクリプト

- `infrastructure/scripts/seed_dev.py`（新規、boto3 直接、約 150 行）
- 投入辞書（自然日本語キーワード 6 件、ADR-0010 §3.2 マッピング表と部分文字列マッチ整合）:
  - SAFE: 「無事」「大丈夫」
  - INJURED: 「怪我」「痛い」
  - UNAVAILABLE: 「動け」「出社不可」

### 1.3 事前ドライラン（ローカル `classify_voice_status` × 投入後辞書）

| 末尾 | 擬似 transcript    | 期待 Voice_Status | classify 結果                      | 整合 |
| ---- | ------------------ | ----------------- | ---------------------------------- | ---- |
| 0    | 「無事です」       | SAFE              | SAFE（matched: `無事`）            | ✓    |
| 1    | 「大丈夫です」     | SAFE              | SAFE（matched: `大丈夫`）          | ✓    |
| 2    | 「無事です」       | SAFE              | SAFE                               | ✓    |
| 3    | 「怪我をしました」 | INJURED           | INJURED（matched: `怪我`）         | ✓    |
| 4    | 「痛いです」       | INJURED           | INJURED（matched: `痛い`）         | ✓    |
| 5    | 「動けません」     | UNAVAILABLE       | UNAVAILABLE（matched: `動け`）     | ✓    |
| 6    | 「出社不可です」   | UNAVAILABLE       | UNAVAILABLE（matched: `出社不可`） | ✓    |
| 9    | 「あいうえお」     | OTHER             | OTHER（matched: []）               | ✓    |

→ **8/8 ローカル整合確認 OK**

---

## 2. dev 環境 deploy（Step 1）

### 2.1 deploy.ps1 実行

- コマンド: `pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev`
- 主な変更内容（Phase 16.4 で template.yaml に追加されていた未 deploy 内容を反映）:
  - `Parameters.MockMode` 追加
  - `Rules.ProdMockModeForbidden` 追加
  - ConnectDispatcherFn の Environment.Variables に `MOCK_MODE` / `ENVIRONMENT_NAME` 追加
  - TranscribeStarterFn の Environment.Variables に `MOCK_MODE` / `ENVIRONMENT_NAME` 追加
  - ConnectDispatcherFnExecutionRole に S3 RecordingsBucket PutObject 権限追加
  - TranscribeStarterFnExecutionRole に S3 TranscriptsBucket PutObject 権限追加

### 2.2 deploy 結果

- Stack: `safety-confirmation-dev`
- Status: **UPDATE_COMPLETE**
- LastUpdatedTime: 2026-06-28T07:34:55 UTC（初回反映）/ 2026-06-28T08:00 頃 UTC（bug 修正後 2 回目）
- 所要時間: 各回 ~3 分

---

## 3. Outbound 1 巡 E2E 検証（Step 2）

### 3.1 検証 script

- `infrastructure/scripts/run_phase16_5_e2e.py`（新規、boto3 直接、約 270 行）
- 起動パラメータ: `mode=ALL`, `retryCount=3`, `retryIntervalMinutes=1`

### 3.2 検証 Cycle（最終、bug fix 後）

- **cycleId: `662df8ba-78ff-4feb-83b5-7bf00828bed3`**
- Idempotency-Key: `phase-16-5-mock-e2e-1782633936`
- startedAt: 2026-06-28T08:05:37Z
- SFN execution status: **SUCCEEDED**
- 所要時間: 320 秒（Cycle 起動 → SFN Map 完了 → CycleFinalizer → COMPLETED）

### 3.3 Phase 1〜9 検証結果

| Phase                    | 検証項目                           | 結果         | 備考                                                                                                                |
| ------------------------ | ---------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------- |
| 1 SPA ログイン           | 管理者 Cognito 認証                | ✅ Skip 許容 | ADR §9.3、Lambda invoke で API 直叩き fallback                                                                      |
| 2 Cycle 起動             | CycleApi POST → SFN StartExecution | ✅           | Cycle status=RUNNING, dictionaryVersion=7                                                                           |
| 3 LoadTargets            | 対象社員抽出                       | ✅           | targetCount=10、is_visible で論理削除済除外                                                                         |
| 4 ConnectDispatcher mock | 擬似 wav + Response 書込           | ✅           | RecordingsBucket 10 wav 投入、Response.callResultCode 書込                                                          |
| 5 TranscribeStarter mock | 擬似 transcript JSON 投入          | ✅           | TranscriptsBucket `transcripts/` prefix で 10 JSON 投入（ADR §3.5.2 表記 `outbound/` とのズレあり、副次 17.3 起票） |
| 6 KeywordMatcher         | Voice_Status 判定                  | ✅           | **10/10 期待通り（SAFE 3 / INJURED 2 / UNAVAILABLE 2 / UNREACHABLE 3）**                                            |
| 7 RetryEvaluator         | 末尾 7/8/9 リトライ                | ✅           | 3 回リトライ後 UNREACHABLE 確定（EMP-0009 は録音 3 件 + transcript 3 件で OTHER → UNREACHABLE）                     |
| 8 CycleFinalizer         | Cycle.status=COMPLETED             | ✅           | SFN MAP_COMPLETED → compute_summary → Cycle status=COMPLETED                                                        |
| 9 ResponseApi            | DDB Response 全件取得              | ✅           | 10 件 Query 成功、voiceStatus / matchedKeywords / transcriptExcerpt 全フィールド整合                                |

### 3.4 ADR-0010 §3.2 マッピング表 突合（10/10 OK）

```
employeeId   | digit | expected     | actual       | result
--------------------------------------------------------------------------------
EMP-0000     | 0     | SAFE         | SAFE         | OK
EMP-0001     | 1     | SAFE         | SAFE         | OK
EMP-0002     | 2     | SAFE         | SAFE         | OK
EMP-0003     | 3     | INJURED      | INJURED      | OK
EMP-0004     | 4     | INJURED      | INJURED      | OK
EMP-0005     | 5     | UNAVAILABLE  | UNAVAILABLE  | OK
EMP-0006     | 6     | UNAVAILABLE  | UNAVAILABLE  | OK
EMP-0007     | 7     | UNREACHABLE  | UNREACHABLE  | OK
EMP-0008     | 8     | UNREACHABLE  | UNREACHABLE  | OK
EMP-0009     | 9     | UNREACHABLE  | UNREACHABLE  | OK
summary: 10 OK / 0 NG / total 10
```

---

## 4. 副次発見 bug（本セッションで応急対応、根本修正は 17.x として別タスク起票）

### 4.1 bug A: SFN ASL `referencedCycleId.$` 必須

- 症状: `mode=ALL` で Cycle 起動 → SFN execution が即時 FAILED → `States.Runtime: The JSONPath '$.referencedCycleId' specified for the field 'referencedCycleId.$' could not be found in the input`
- 原因: SFN ASL の LoadTargets state が `referencedCycleId.$: "$.referencedCycleId"` で無条件キー解決を要求。CycleApi は `mode=ALL` の場合 `referencedCycleId` を SFN 入力に含めない設計（handler.py L271-274）。
- 影響範囲: `mode=ALL` での Cycle 起動が全件失敗
- 本セッション応急対応: CycleApi handler.py の `_create_cycle` 内 `sfn_input` 生成箇所に `"referencedCycleId": None` を default で追加（1 行）→ CFn redeploy で反映
- 根本修正案（17.1 として起票）: SFN ASL に Pass state + `States.JsonMerge` で `referencedCycleId` に null defaults を補完、または LoadTargets Lambda Payload から `referencedCycleId.$` を削除して Lambda 側で `event.get` で取得（既存実装は対応済）

### 4.2 bug B: CycleFinalizer `compute_summary` の Decimal 非対応

- 症状: SFN Map 完了 → CycleFinalizer 起動 → `TypeError: responses[0].callAttempts must be int; got Decimal`
- 原因: `backend/shared/cycle/finalize.py:198` / `:297` で `callAttempts` の型を `isinstance(_, int)` で厳密チェック。boto3 DynamoDB read は `Decimal` 型を返すため、Phase 6 / 13 単体テストでは int 直接渡しで検出されず、dev 実機 SFN で初めて発覚。
- 影響範囲: Map 完了経路（MAP_COMPLETED）+ 30 分 SLA 経路（is_first_dispatch_incomplete）両方
- 本セッション応急対応: 2 箇所の isinstance を `(int, Decimal)` に拡張 + `int(raw_attempts)` で変換（from decimal import Decimal 追加、計 ~10 行変更）→ CFn redeploy で反映
- 根本修正の補強（17.2 として起票）: `finalize.py` 全関数で Decimal 経路を unit test カバレッジ追加、`shared/dynamodb/numeric.py` 等の共通 helper 切り出しも検討

### 4.3 ADR §3.5.2 表記と実装の prefix 不整合

- 症状: ADR-0010 §3.5.2 / §6.7 で「`outbound/{cycleId}/{employeeId}/{seq}.json`」と表記、実装は `transcripts/{cycleId}/{employeeId}/{seq}.json`
- 影響: ADR 表記と実装のズレ、検証 script が `outbound/` prefix を期待した結果 Transcript count=0 で出力（実態は `transcripts/` prefix で 10 件存在）
- 17.3 として起票：ADR §3.5.2 表記を `transcripts/` に修正、または実装を `outbound/` に揃える（後者は KeywordMatcherEventRule の EventPattern prefix 確認必要）

---

## 5. 課金実績概算（ADR-0010 §5 整合）

| サービス              | 概算             | 備考                                                                                                                                            |
| --------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| AWS Lambda            | < 0.05 円        | CycleApi + LoadTargets + ConnectDispatcher × 10 + TranscribeStarter × 10 + KeywordMatcher × 10 + RetryEvaluator × ~13 + CycleFinalizer + 再検証 |
| AWS Step Functions    | < 0.05 円        | Standard、~100 state transitions                                                                                                                |
| Amazon DynamoDB       | < 0.01 円        | PAY_PER_REQUEST、~100 read/write                                                                                                                |
| Amazon S3             | < 0.01 円        | PutObject ~20 件、GetObject ~10 件、各 1 KB                                                                                                     |
| CloudWatch Logs       | < 0.05 円        | LogGroup 19 個 / 各 ~数 KB                                                                                                                      |
| Amazon SNS            | 0 円             | OperatorTopic publish なし（CycleFinalizer COMPLETED 通知は SNS 未配信）                                                                        |
| **Amazon Connect**    | **0 円**         | mock 経路、API 未呼出                                                                                                                           |
| **Amazon Transcribe** | **0 円**         | mock 経路、API 未呼出                                                                                                                           |
| **CFn deploy**        | < 0.1 円         | 2 回 deploy（初回 + bug 修正後）、artifacts S3 PUT 含む                                                                                         |
| **合計**              | **~0.2〜0.3 円** | 試算、実費は AWS Cost Explorer で別途確認                                                                                                       |

ADR-0010 §5「数円〜数十円程度」の範囲内、ADR-0009 §5 と同じ「ユーザー責任 + 金額記載なし」運用方針整合。

---

## 6. 関連ファイル（本セッション新規 / 変更）

### 6.1 新規ファイル

- `infrastructure/scripts/seed_dev.py`（dev 環境 seed、~150 行）
- `infrastructure/scripts/run_phase16_5_e2e.py`（Phase 16.5 検証、~270 行）
- `docs/notes/16-5-mock-e2e-validation.md`（本ファイル）

### 6.2 変更ファイル

- `backend/lambdas/cycle_api/handler.py`：`_create_cycle` 内 sfn_input に `referencedCycleId: None` 追加（bug A 応急対応）
- `backend/shared/cycle/finalize.py`：`from decimal import Decimal` 追加、`compute_summary` / `is_first_dispatch_incomplete` の callAttempts 判定を Decimal 対応に拡張（bug B 応急対応）

### 6.3 dev 環境 DDB 投入（seed_dev.py 経由、本セッション内）

- EmployeeTable: 10 件追加
- KeywordDictionaryTable: 6 件追加 + META.currentVersion 6 → 7
- KeywordDictionaryHistoryTable: v=7 スナップショット 12 件
- CycleTable: 検証 Cycle 3 件作成（b190fd39 = START_FAILED / 927ed86b = TIMEOUT / 662df8ba = COMPLETED）
- ResponseTable: 検証 Cycle ごとに 10 件 ≈ 30 件
- RecordingsBucket: ~30 wav オブジェクト（90 日 LCM で自動削除）
- TranscriptsBucket: ~30 transcript JSON オブジェクト（90 日 LCM で自動削除）

---

## 7. 完了確認（ADR-0010 §4.3「検証完了の最終基準」整合）

- [x] §4.1 Phase 1〜9 すべての完了条件達成（9/9）
- [x] 実行ログ（本ファイル）作成
- [x] 課金実績概算記録（§5）
- [x] 副次発見 bug の別タスク化（17.1 / 17.2 / 17.3 起票）

→ **Phase 16.5 Done When 完全充足、tasks.md [ ] 16.5 を [x] に更新**

---

## 8. 後続対応（17.x 副次タスク + 既知の副次タスク）

| 番号 | 内容                                                                  | 起票元                    | 優先度 |
| ---- | --------------------------------------------------------------------- | ------------------------- | ------ |
| 17.1 | SFN ASL `referencedCycleId` 根本修正（Pass state + States.JsonMerge） | 本セッション bug A        | 中     |
| 17.2 | `finalize.py` 全関数 Decimal 対応 review + unit test 追加             | 本セッション bug B        | 中     |
| 17.3 | TranscribeStarter mock S3 key prefix と ADR §3.5.2 表記の整合         | 本セッション 4.3 副次発見 | 低     |
| 17.4 | ADR-0010 §6.7 訂正（既知、ユーザー指示）                              | ユーザー指示              | 低     |
| 17.5 | `parameters/README.md` 整備                                           | ユーザー指示              | 低     |
| 17.6 | `template.yaml` レガシーコメント整地                                  | 前セッション副次発見      | 低     |

---

## 9. 所感

本セッションの最大の成果は、ADR-0010 で計画した **mock 経路 1 巡 E2E** が dev 環境で完全に動作することを確認できた点。`derive_mock_response` の純粋関数（Phase 16.1）+ ConnectDispatcher / TranscribeStarter の mock 分岐（Phase 16.2 / 16.3）+ MockMode Parameter / Rules（Phase 16.4）+ 自然日本語辞書 6 件投入が、ADR-0010 §3.2 マッピング表の 10/10 期待値と完全に一致して動作。

同時に、dev 実機 SFN を初めて Map 完了まで走らせたことで、既存 production 経路の 2 つの bug（A: SFN ASL referencedCycleId 必須 / B: finalize.py Decimal 非対応）を発見・応急対応できた点も重要な副次成果。これらは Phase 6 / Phase 13 単体テストでは検出不可能で、Phase 16.5 mock E2E が本来意図していた以上の品質担保を提供した形になる。

第 6 / 第 7 / 第 9 / 第 10 / 第 11 / 第 13 / 第 15 / 第 17 / 第 18 / 第 19 原則を実運用で全発動。ユーザー方針「計画承認済選択肢は AI 推奨案を採用」+「ステップ別 y/n」+「不可逆操作 / 課金発生 / 失敗時は停止」運用で、Step 0 → 1 → 2 → 3 を順次進行、途中 bug A / B 発見時も第 7 原則「即停止 → ユーザー報告 → 新計画」を厳守した結果、本セッション内で Phase 16.5 完全達成に至った。
