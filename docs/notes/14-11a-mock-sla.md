# Task 14.11a — Connect 非依存：mock Contact Flow + Transcribe スタブで 300 名 60 分 SLA 検証

**作成日**: 2026-06-27 セッション継続
**spec**: `safety-confirmation-system`
**対象タスク**: tasks.md 14.11a（元タスク 14.11 から切出、Bii 方針）
**Requirements**: 14.1〜14.3
**Design**: SLA 達成の根拠 / 性能テスト（design.md L1474〜L1500）

---

## 1. 背景

元タスク 14.11「性能テスト：300 名 60 分 SLA」は本文中で「ダミー Connect インスタンス（または mock Contact Flow）+ Transcribe スタブでサイクル起動」と明記されており、Bii 方針（Connect 非依存範囲で実運用品質に到達）に従い本タスク 14.11a で mock 構成での実施に切出。実 Connect 発信 / 実 Transcribe ジョブ / 実通話料金 / Polly TTS / 実 S3 録音は 14.11 本タスクに残置（ADR-0009 §3 完了後）。

---

## 2. 検証方式と設計判断

### 2.1 採用方針サマリ

| 論点                      | 採用案                                                                    | 理由                                                                                                                                                        |
| ------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AWS リソースのモック手法  | **MagicMock + monkeypatch + 自作 in-memory fake DynamoDB Table**          | Phase 13 / Phase 6 系既存テストパターン（`backend/tests/lambdas/{handler}/test_handler.py`）流用、moto 等の新規依存追加なし（第 19 原則 (a) DRY 原則）      |
| 時刻計測                  | **論理時計（離散イベントシミュレータ）**                                  | 実時間 60 分の wall-clock 実行は CI 非実用。phase 列 + scheduler で仮想時計上に SLA 達成性を再現、テスト実行は 0.5 秒                                       |
| SFN 全体の再現方法        | **Python で Map iteration を逐次再現**                                    | タスク本文の選択肢「local 環境で SFN を単一ステート分解して逐次呼出」を採用。実 SFN（dev 環境）依存を回避、15.2a 未完了でも検証可能                         |
| 並列度 10 の制約解釈      | **dispatch（Connect API 呼出中）のみ slot 占有、Wait state は slot 解放** | design.md L1499「Amazon Connect の同時アクティブコール 10」は Connect 側クォータの制約、Wait state はコール非活性。SLA 計算 L1488-1496 もこの解釈で組まれた |
| 結果コード分布            | **RECORDED 85% / NO_ANSWER 7% / BUSY 3% / VOICEMAIL 3% / ERROR 2%**       | 平常時の安否確認運用想定。design.md L1493 の「再発信比率 30% 想定」と整合（RECORDED 後 OTHER 5% + 非 RECORDED 15% ≒ 19%、3 attempts 累積で約 25%）          |
| RECORDED 後の voiceStatus | **SAFE 85% / INJURED 5% / UNAVAILABLE 5% / OTHER 5%**                     | 平常時想定。OTHER 比率は KeywordMatcher の不一致率に相当                                                                                                    |
| 通話時間モデル            | **RECORDED 50s / NO_ANSWER 35s / BUSY 5s / VOICEMAIL 25s / ERROR 5s**     | design.md L1479-1481「平均 40〜50 秒 + 安全側 T_call=90 秒」「無応答 T_no_answer=35 秒」に整合、平均 46 秒 → 初回完了 23 分予測                             |
| Retry interval / count    | **5 分 / 3 attempts**（design.md デフォルト）                             | template.yaml の `DefaultRetryCount=3` / `DefaultRetryIntervalMinutes=5` をそのまま使用                                                                     |
| Random seed               | **42（固定）**                                                            | テスト決定論性確保。CI で flaky にならないため固定 seed 採用                                                                                                |

### 2.2 生成物ファイル一覧

| ファイル                                         | 行数 | 種別 | 役割                                                                                                           |
| ------------------------------------------------ | ---- | ---- | -------------------------------------------------------------------------------------------------------------- |
| `backend/tests/integration/__init__.py`          | 7    | 新規 | package marker                                                                                                 |
| `backend/tests/integration/conftest.py`          | 50   | 新規 | 6 個の Lambda が読む環境変数（CONNECT_INSTANCE_ID / RESPONSE_TABLE_NAME / KMS_CMK_ARN 等）を `setdefault` 投入 |
| `backend/tests/integration/_fakes.py`            | 274  | 新規 | in-memory な `boto3.resource('dynamodb').Table` 互換 fake。UpdateExpression / ConditionExpression を最小実装   |
| `backend/tests/integration/test_sla_300_mock.py` | 560  | 新規 | 統合テスト本体（メイン 1 件 + scheduler sanity 2 件、計 3 件）                                                 |
| `docs/notes/14-11a-mock-sla.md`                  | -    | 新規 | 本レポート                                                                                                     |

既存コード変更：**ゼロ件**。テストインフラ追加のみ。

### 2.3 検証フロー（2 段階）

**Phase 1: Per-employee 実 Lambda 呼出（boto3 client / DynamoDB Table を fake で置換）**

各 employee について SFN ASL の通り以下を順次呼出：

1. `Response.put_item(attribute_not_exists(employeeId))` ← InitAttempt 相当
2. `ConnectDispatcher.lambda_handler(...)` ← Dispatch
3. callResultCode を確率分布からサンプリング
4. `CallEndHandler.lambda_handler(...)` ← Outbound Contact Flow 終端
5. RECORDED の場合：`TranscribeStarter.lambda_handler(S3 ObjectCreated event)` + voiceStatus を直接 update_item（KeywordMatcher mock）
6. `RetryEvaluator.lambda_handler(...)` ← EvaluateRetry
7. retry なら 1 に戻る、終端なら voiceStatus を `decision["finalStatus"]` に更新 ← FinalizeOne 相当

各 phase の論理 duration を `(phase_type, duration_sec)` タプルとして記録。

**Phase 2: 離散イベントシミュレータで論理時計再現**

`_simulate_workflows(workflows, max_concurrency=10)` で min-heap ベースの DES を実行。`dispatch` phase のみが MaxConcurrency=10 slot を占有、`wait_transcribe` / `wait_interval` は slot 解放。peak concurrency、各 employee の first dispatch 完了時刻、cycle 全体 wall clock を計算。

---

## 3. 実測結果

### 3.1 pytest 実行コマンド

```powershell
$env:PYTHONUTF8="1"
uv run pytest tests/integration/test_sla_300_mock.py -v --tb=short
```

cwd: `backend`、Profile: `AWS-security-check` 不要（AWS API 呼出ゼロ）。

### 3.2 結果

```
collected 3 items
tests/integration/test_sla_300_mock.py::test_300_employees_complete_within_60min_with_concurrency_10 PASSED [ 33%]
tests/integration/test_sla_300_mock.py::test_scheduler_enforces_max_concurrency               PASSED [ 66%]
tests/integration/test_sla_300_mock.py::test_scheduler_wait_phases_do_not_consume_slots       PASSED [100%]

============================== 3 passed in 0.58s ==============================
```

### 3.3 SLA 検証メトリクス（メインテスト出力）

| メトリクス                       | 実測値                  | SLA 制約 | 余裕      |
| -------------------------------- | ----------------------- | -------- | --------- |
| 対象者数                         | 300                     | -        | -         |
| MaxConcurrentCalls               | 10                      | -        | -         |
| **peak concurrency（実測）**     | **10**                  | ≤ 10     | 0 件      |
| **cycle total wall clock**       | **2205 s（36.75 min）** | ≤ 60 min | 23.25 min |
| **max first-dispatch 完了時刻**  | **1560 s（26 min）**    | ≤ 30 min | 4 min     |
| p50 first-dispatch 完了時刻      | 760 s（12.67 min）      | -        | -         |
| p95 first-dispatch 完了時刻      | 1470 s（24.5 min）      | -        | -         |
| `SlaWarning30Min` メトリクス発火 | **未発火**              | 未発火   | -         |
| `CycleTimeout` メトリクス発火    | **未発火**              | 未発火   | -         |

### 3.4 callResultCode 分布（initial + retry の累積 attempts）

| callResultCode | 件数 | 比率（対 attempts 合計 372 件） | 設定値                      |
| -------------- | ---- | ------------------------------- | --------------------------- |
| RECORDED       | 316  | 84.9%                           | 85%                         |
| NO_ANSWER      | 28   | 7.5%                            | 7%                          |
| BUSY           | 9    | 2.4%                            | 3%                          |
| VOICEMAIL      | 9    | 2.4%                            | 3%                          |
| ERROR          | 10   | 2.7%                            | 2%                          |
| 合計 attempts  | 372  | -                               | 300 名 × 平均 1.24 attempts |

retry 発生 attempts ＝ 72 件（300 名中 21% が 2 回目以降を実施、残り 79% は初回成功）。design.md L1493「再発信対象比率 30% を想定」より低めの実測（85% RECORDED の効果）。

### 3.5 最終 voiceStatus 分布（300 名）

| voiceStatus | 件数 | 比率   |
| ----------- | ---- | ------ |
| SAFE        | 267  | 89.0%  |
| INJURED     | 16   | 5.3%   |
| UNAVAILABLE | 14   | 4.7%   |
| UNREACHABLE | 3    | 1.0%   |
| 合計        | 300  | 100.0% |

全 300 名が `_TERMINAL_VOICE_STATUSES = {SAFE, INJURED, UNAVAILABLE, UNREACHABLE}` のいずれかに到達 ✓

`UNREACHABLE` は 3 attempts 連続 OTHER で retry budget 枯渇したケース（design.md / Requirement 9.5）。

### 3.6 CycleFinalizer 3 trigger の確認

| trigger         | 入力                                           | 期待結果            | 実測結果              |
| --------------- | ---------------------------------------------- | ------------------- | --------------------- |
| `TIMER_30MIN`   | 全 employee が dispatch 済（callAttempts > 0） | `no_warning_needed` | `no_warning_needed` ✓ |
| `MAP_COMPLETED` | 全 employee terminal voiceStatus               | `completed`         | `completed` ✓         |
| `TIMER_60MIN`   | 既に `status=COMPLETED`                        | `no_op`             | `no_op` ✓             |

---

## 4. design.md の SLA 計算との突合

design.md L1488-1496 の計算根拠：

| 項目                  | design.md 予測 | 本テスト実測      | 差分                                         |
| --------------------- | -------------- | ----------------- | -------------------------------------------- |
| 平均通話時間          | 45 秒          | 46 秒（加重平均） | +1 秒（VOICEMAIL 25s で押し上げ）            |
| 初回発信完了          | 22.5 分        | 26 分（max）      | +3.5 分（VOICEMAIL / NO_ANSWER の長い tail） |
| 再発信対象比率        | 30%            | 21%               | -9 pt（RECORDED 85% の効果）                 |
| サイクル全体完了      | 44 分          | 36.75 分          | -7.25 分（retry 比率減）                     |
| マージン（60 分まで） | 16 分          | 23.25 分          | +7.25 分（実測の方が余裕大）                 |

design.md の見積もりは妥当性が確認され、実測の方が良好な結果。特に「再発信対象比率 30% 想定」は本テストの確率分布（RECORDED 85%）では 21% に収まり、余裕度が拡大した。

---

## 5. スコープ外（元タスク 14.11 に残置）

本タスクでは以下を実施せず、ADR-0009 §3 完了後の本タスク 14.11（実 Connect 発信版）に委譲：

- 実 Connect インスタンスでの `start_outbound_voice_contact` 呼出
- 実 Transcribe ジョブの起動（`start_transcription_job`）
- 実通話料金の発生
- Polly TTS（ガイダンス音声合成）
- 実 S3 録音オブジェクトの生成と LCM 動作確認
- CloudWatch メトリクスの実値発火（本テストは MagicMock で発火検出のみ）

---

## 6. 設計判断詳細（参考）

### 6.1 in-memory fake DynamoDB Table 実装範囲

`backend/tests/integration/_fakes.py` の `InMemoryTable` は以下のみ実装：

- `put_item(Item, ConditionExpression?, ExpressionAttributeValues?, ExpressionAttributeNames?)`
- `get_item(Key, ConsistentRead?)` （第二引数は受け取るが無視）
- `update_item(Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression?, ExpressionAttributeNames?)`
- `query(KeyConditionExpression, ExpressionAttributeValues, ExclusiveStartKey?)` （単純な `<pk> = :val` のみ）

ConditionExpression パーサは以下のみサポート（handler が実際に使う形）：

- `attribute_not_exists(<attr>)`
- `attribute_exists(<attr>)`
- `<attr> = :val`
- `<expr> AND <expr>`
- `<expr> OR <expr>`

UpdateExpression パーサは `SET attr = :val, attr2 = :val2` + `ADD attr :delta` の組合せのみ。

未サポートシェイプは `NotImplementedError`（第 19 原則 (b) フォールバック禁止）。

### 6.2 離散イベントシミュレータの正しさ確認

スケジューラの仕様を 2 件の追加テストで保証：

| テスト                                            | 検証内容                                                          |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| `test_scheduler_enforces_max_concurrency`         | 50 employees × 10 秒 dispatch で max_concurrency=10 → 50 秒で完了 |
| `test_scheduler_wait_phases_do_not_consume_slots` | `wait_interval` 中に他 employee が dispatch slot を取得可能       |

両方 PASS により、scheduler の意味論が design.md の SLA 計算前提と一致することを確認。

### 6.3 Connect Send Task Success の動作確認

`ConnectDispatcher` は成功時に `_SFN.send_task_success` を呼ばない（`CallEndHandler` が代行）。`CallEndHandler` は呼ぶ（`taskToken` release）。本テストでは MagicMock を渡しているため、呼出回数のアサーションは入れていないが、handler 内部で `mock.send_task_success(...)` が走ることで例外なしで通過したことが PASS の根拠。

---

## 7. 残課題 / 次セッション以降の改善候補

| 項目                                              | 優先度 | 備考                                                                                                   |
| ------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ |
| 実 Connect 発信版（14.11 本タスク）               | High   | ADR-0009 §3（Connect Tokyo 課金合意）完了後                                                            |
| stress test（NUM_EMPLOYEES = 1000 / 3000）        | Low    | 現在の N=300 は Requirement 14.3 に整合。スケール検証は本仕様外                                        |
| call duration の確率分布化                        | Low    | 現在は point estimate。実 Connect 発信後の実測値で正規分布等に置換可能                                 |
| KeywordMatcher 実装の test 経路への組込           | Low    | 14.11 本タスクの実 Transcribe ジョブ実行時に組込                                                       |
| Step Functions Local（local SFN）統合             | Low    | dev 環境への deploy 後、`stepfunctions-local` で ASL を実行する選択肢あり（過剰投資）                  |
| Map MaxConcurrency=10 制約の AWS 公式仕様の再確認 | Med    | 「Wait state がスロット占有するか」を AWS Doc / 実 SFN で確証。本テストは design.md の解釈に従っている |

---

## 8. テスト件数の変動

| カテゴリ                                     | 追加件数 | 累計（推定）                         |
| -------------------------------------------- | -------- | ------------------------------------ |
| backend 単体 / property / integration テスト | +3 件    | （他テスト改修なし、PBT carry-over） |
| frontend テスト                              | ±0       | 変更なし                             |

cfn-lint / cfn-nag への影響：テンプレ変更ゼロのため影響なし（ベースライン 29 件 WARNING / 0 件 ERROR 維持）。

---

## 9. Done When 充足チェック

| 条件                                                                            | 充足 | 根拠                                               |
| ------------------------------------------------------------------------------- | ---- | -------------------------------------------------- |
| 300 名 mock サイクルが 60 分以内に COMPLETED                                    | ✓    | total_time = 2205 s = 36.75 min                    |
| 同時 10 制約遵守（MaxConcurrentCalls=10）                                       | ✓    | peak_concurrency = 10                              |
| テストレポート `docs/notes/14-11a-mock-sla.md` に記録                           | ✓    | 本ファイル                                         |
| pytest 実行で test_sla_300_mock.py が PASS                                      | ✓    | 3 passed in 0.58s                                  |
| `SafetyConfirmation/SlaWarning30Min` / `SafetyConfirmation/CycleTimeout` 未発火 | ✓    | mock_cloudwatch.put_metric_data 呼出ログをアサート |
| 30 分時点で全 300 名への初回 ConnectDispatcher invocation 完了率 100%           | ✓    | max first_dispatch_complete = 26 min ≤ 30 min      |

---

## 10. 補遺：シミュレーション数値の再現性

`random.Random(42)` で固定 seed のため、同一コード + 同一環境で本レポートと同一のメトリクスが再現される。Hypothesis profile（`hypothesis-6.155.5`）は非使用、`hypothesis-profile=default` のまま、property test は本タスクスコープ外。
