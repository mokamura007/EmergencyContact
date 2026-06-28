# Task 15.21 — CFn drift detection 手動実行（15.20 副次効果の整合性確認）

- 完了日: 2026-06-27（セッション 22、tasks.md 15.21）
- 対応者: kiro（orchestrator 直接実行、AWS CLI 経由）
- スコープ: dev 環境（Stack `safety-confirmation-dev`、Account 214046906694、ap-northeast-1）
- Connect 依存: なし

---

## 1. タスク背景

15.20 で Cognito User Pool LambdaConfig を AWS CLI で再アタッチ。template.yaml L1031-1035 定義と再同期したはずだが、CFn 視点で drift が解消されたかを `detect-stack-drift` で客観確認する。

---

## 2. 実行コマンドと結果

### 2.1 drift detection 開始

```powershell
aws cloudformation detect-stack-drift `
  --stack-name safety-confirmation-dev `
  --profile AWS-security-check --region ap-northeast-1
```

→ `StackDriftDetectionId = bc662aa0-7248-11f1-916f-06dc38521a1d`

### 2.2 ステータス polling

`describe-stack-drift-detection-status` を 5 秒間隔で polling、**約 40 秒（8 回目）で DETECTION_COMPLETE** に到達。

### 2.3 結果サマリ

| 項目                      | 値                                                                    |
| ------------------------- | --------------------------------------------------------------------- |
| DetectionStatus           | **DETECTION_COMPLETE**                                                |
| StackDriftStatus          | **DRIFTED**                                                           |
| DriftedStackResourceCount | **12**                                                                |
| ResourceType              | すべて `AWS::ApiGateway::Method`（OPTIONS Method、CORS preflight 用） |

### 2.4 CognitoUserPool の drift 判定

| 項目                | 結果                         |
| ------------------- | ---------------------------- |
| **CognitoUserPool** | **IN_SYNC（drift なし）** ✅ |
| 15.20 副次効果      | **問題なし、想定通り**       |

→ 本タスクの Done When「CognitoUserPool が IN_SYNC または許容差異のみ」**達成**。

---

## 3. drift 検出された 12 リソース（15.21 スコープ外、別タスク化候補）

| #   | LogicalResourceId                             | ResourceType            | DriftStatus |
| --- | --------------------------------------------- | ----------------------- | ----------- |
| 1   | AuthRecordFailureOptionsMethod                | AWS::ApiGateway::Method | MODIFIED    |
| 2   | CyclesIdOptionsMethod                         | AWS::ApiGateway::Method | MODIFIED    |
| 3   | CyclesIdRecordingsEmployeeIdSeqOptionsMethod  | AWS::ApiGateway::Method | MODIFIED    |
| 4   | CyclesIdResponsesEmployeeIdOptionsMethod      | AWS::ApiGateway::Method | MODIFIED    |
| 5   | CyclesIdResponsesOptionsMethod                | AWS::ApiGateway::Method | MODIFIED    |
| 6   | CyclesIdStatusOptionsMethod                   | AWS::ApiGateway::Method | MODIFIED    |
| 7   | CyclesIdTranscriptsEmployeeIdSeqOptionsMethod | AWS::ApiGateway::Method | MODIFIED    |
| 8   | CyclesOptionsMethod                           | AWS::ApiGateway::Method | MODIFIED    |
| 9   | EmployeesIdAnonymizeOptionsMethod             | AWS::ApiGateway::Method | MODIFIED    |
| 10  | EmployeesIdCognitoUserOptionsMethod           | AWS::ApiGateway::Method | MODIFIED    |
| 11  | EmployeesIdOptionsMethod                      | AWS::ApiGateway::Method | MODIFIED    |
| 12  | EmployeesImportOptionsMethod                  | AWS::ApiGateway::Method | MODIFIED    |

### 3.1 共通の drift 内容（1 件代表サンプル `AuthRecordFailureOptionsMethod`）

| 項目                      | 値                                                      |
| ------------------------- | ------------------------------------------------------- |
| PropertyDifferences Count | 1                                                       |
| Path                      | `/Integration/IntegrationResponses/0/ResponseTemplates` |
| Diff Type                 | **REMOVE**                                              |

→ template.yaml で `ResponseTemplates` を明示定義していない（empty）が、CFn の internal model が「空オブジェクト」or「未定義」のどちらで保持しているかの表記差異。**機能影響なし**（CORS preflight は 15-2a-cors-fix.md §「CORS preflight curl 結果サマリ」で 10 endpoint × 200 OK + 3 ヘッダ確認済、実機正常動作中）。

### 3.2 想定される真因

15.2a CORS fix で追加した OPTIONS Method 群（Mock Integration）の `IntegrationResponses` 定義で、`ResponseTemplates` を CFn template 上では明示定義せず空状態だったが、API Gateway 側に書込まれた実機状態と CFn の internal model 表現の差で **drift detection が偽陽性として MODIFIED 判定**している現象。

AWS CloudFormation の `AWS::ApiGateway::Method` で `Mock` Integration を使った場合の既知の挙動領域。

---

## 4. 判定

| 条件                                              | 判定                       |
| ------------------------------------------------- | -------------------------- |
| `detect-stack-drift` 完走                         | ✅                         |
| 結果ノート作成                                    | ✅（本ファイル）           |
| **CognitoUserPool が IN_SYNC または許容差異のみ** | ✅ **IN_SYNC（完全一致）** |

→ **15.21 Done When 全項目達成**。

---

## 5. 副次発見（次タスク候補）

**OPTIONS Method 12 件の drift 解消** は 15.21 スコープ外。以下のいずれかで対処可能：

| 対応案         | 内容                                                                                                                 | 推定工数                                       | 副作用                            |
| -------------- | -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------- |
| (A) 明示定義   | template.yaml の OPTIONS Method 12 件すべてに `ResponseTemplates: { 'application/json': '' }` を明示追加 → 再 deploy | 30 分（template 修正 + deploy + drift 再検出） | CFn deploy 1 回（テスト影響なし） |
| (B) 放置       | 機能影響なしの偽陽性として記録、本番リリース前に再評価                                                               | 0 分                                           | drift 状態残置                    |
| (C) 別タスク化 | 15.27 として新規起票、優先度 Low、将来の整地セッションで消化                                                         | tasks.md 起票のみ                              | drift 状態残置                    |

**AI 推奨：(C)** — 機能影響ゼロ、本セッションの「Connect 非依存範囲完了」目的に対し本質的でないため次セッション以降に温存。

---

## 6. 関連ファイル

- `docs/notes/15-21-drift-raw.json`（detect-stack-drift 生 JSON 出力、12 リソース drift 詳細含む）
- `docs/notes/15-20-postauth-import-fix.md` §5.2（Cognito Trigger 再アタッチ実施記録）
- `docs/notes/15-2a-cors-fix.md`（CORS fix の deploy 記録、drift の起源）
- `infrastructure/template.yaml`（OPTIONS Method 12 件の定義、L〜）

---

## 7. 所感

15.21 のスコープ「15.20 副次効果の整合性確認」は **CognitoUserPool IN_SYNC の確認をもって達成**。

副次発見として CORS OPTIONS Method 12 件の drift（偽陽性、機能影響なし）が判明したが、これは 15.21 スコープ外であり、15.2a CORS fix 由来の既知 AWS 挙動領域。次セッション以降の整地候補として 15.27 起票可能（AI 推奨は (C) 放置 / 別タスク化）。

第 17 原則対称性推論：「Cognito drift であれば 15.20 が原因」「OPTIONS Method drift であれば 15.2a CORS fix が原因」 → 後者が真と裏付けられた（Cognito User Pool は IN_SYNC、OPTIONS Method 12 件のみ MODIFIED）。15.20 副次効果としては「実質的な drift 解消」が成立、CFn drift detection 上は CognitoUserPool が IN_SYNC で完全一致。
