# 安否確認システム mock 経路 結合テスト手順書

- 作成日: 2026-06-29
- 関連: [ADR-0010](../decisions/0010-mock-on-aws-dev.md) / [Phase 16.5 検証ログ](../notes/16-5-mock-e2e-validation.md) / tasks.md Phase 17.8

---

## 0. 概要

### 0.1 目的

ブラウザの SPA から **Outbound 1 巡 mock 経路** を起動し、結果画面までを画面操作で踏破できることを確認する。Phase 16.5 で実機 dev 環境の SFN 経路は 9/9 達成済（boto3 直叩き）、本手順書はその **ブラウザ操作版** にあたる。

### 0.2 対象範囲

| 含む                                        | 含まない                                     |
| ------------------------------------------- | -------------------------------------------- |
| 管理者ユーザー登録（新規 / 既存いずれも可） | 実 Amazon Connect 発信（ADR-0009 §3.1 待ち） |
| ブラウザログイン + 初回パスワード変更       | Inbound 着信経路（Phase 16 スコープ外）      |
| Cycle 起動 + 進行確認 + 結果確認            | Phase 15.6 受入テスト全件 (Property 1〜25)   |
| dev 環境 DDB データ準備の現状確認 + 再投入  | stg / prod 環境                              |

### 0.3 想定所要時間

- 管理者ユーザー登録〜画面ログインまで: 約 5 分
- 1 巡実行（Cycle 起動 → COMPLETED → 結果表示）: 約 5〜10 分
- **合計: 約 10〜15 分**

### 0.4 課金概算（Phase 16.5 §5 と同一試算）

| サービス                                  | 概算               | 備考                                   |
| ----------------------------------------- | ------------------ | -------------------------------------- |
| Lambda + SFN + DDB + S3 + CloudWatch Logs | < 0.3 円           | mock 経路、Connect / Transcribe 不発火 |
| Amazon Connect                            | **0 円**           | mock 経路、API 未呼出                  |
| Amazon Transcribe                         | **0 円**           | mock 経路、API 未呼出                  |
| **合計**                                  | **約 0.2〜0.5 円** | 試算、実費は Cost Explorer で別途確認  |

### 0.5 前提環境

| 項目                        | 値                                                                |
| --------------------------- | ----------------------------------------------------------------- |
| AWS Account                 | 214046906694                                                      |
| Region                      | ap-northeast-1                                                    |
| Stack                       | safety-confirmation-dev (`UPDATE_COMPLETE` 維持)                  |
| CloudFront 配信 URL         | `https://dn8bulnup9krf.cloudfront.net`                            |
| API endpoint                | `https://bev0uk24s0.execute-api.ap-northeast-1.amazonaws.com/dev` |
| Cognito User Pool ID        | `ap-northeast-1_5uYfaQMLJ`                                        |
| Cognito App Client ID (SPA) | `7h8mt6jrieu5grm9s8uqdn94en`                                      |
| MockMode                    | `true` (parameters/dev.json で deploy 反映済)                     |

---

## 1. 前提ツールの確認

ローカル PC で以下が利用可能であること。

```pwsh
# PowerShell 7 (pwsh) で実行
aws --version           # AWS CLI v2 推奨
aws sts get-caller-identity --profile AWS-security-check
```

`get-caller-identity` で Account=`214046906694` が返れば OK。

---

## 2. 管理者ユーザー登録（新規メールアドレス）

> 既存ユーザー `tomita@g-wise.co.jp`（Administrator group, CONFIRMED）でログインできる場合は本章を **スキップして §3 へ** 進んで構いません。

### 2.1 メールアドレスの決定

本手順書では **`integration-test-admin@example.com`** を採用します。

採用根拠：

- `@example.com` は [RFC 2606](https://datatracker.ietf.org/doc/html/rfc2606) 予約ドメイン、実メールが届かない placeholder
- Cognito は **email + password** で SRP 認証成立、メールが実際に届く必要はない
- `--message-action SUPPRESS` で確認メール送信を抑止 → 誤送信リスク無し

別アドレスを使いたい場合は、本章のコマンド中の `integration-test-admin@example.com` を置換してください。

### 2.2 環境変数準備（PowerShell）

```pwsh
$env:AWS_PROFILE = "AWS-security-check"
$env:PYTHONUTF8 = "1"

$USER_POOL_ID = "ap-northeast-1_5uYfaQMLJ"
$TEST_EMAIL   = "integration-test-admin@example.com"
$TEMP_PASS    = "TempPass!2026"   # 一時パスワード（後で必ず変更される、英記号数字大小混在）
```

### 2.3 ユーザー作成（確認メール送信なし）

```pwsh
aws cognito-idp admin-create-user `
    --user-pool-id $USER_POOL_ID `
    --username $TEST_EMAIL `
    --user-attributes "Name=email,Value=$TEST_EMAIL" "Name=email_verified,Value=true" "Name=name,Value=Integration Test Admin" `
    --temporary-password $TEMP_PASS `
    --message-action SUPPRESS `
    --region ap-northeast-1
```

成功すると JSON で `"UserStatus": "FORCE_CHANGE_PASSWORD"` のユーザーが返ります。

### 2.4 Administrator グループへ追加

```pwsh
aws cognito-idp admin-add-user-to-group `
    --user-pool-id $USER_POOL_ID `
    --username $TEST_EMAIL `
    --group-name Administrator `
    --region ap-northeast-1
```

成功時は出力なし（exit code 0）。

### 2.5 確認

```pwsh
aws cognito-idp list-users-in-group `
    --user-pool-id $USER_POOL_ID `
    --group-name Administrator `
    --query "Users[].[Username,UserStatus,Attributes[?Name=='email'].Value|[0]]" `
    --output table `
    --region ap-northeast-1
```

`integration-test-admin@example.com` が `FORCE_CHANGE_PASSWORD` 状態で表示されれば OK。

---

## 3. 1 巡のためのデータ準備

### 3.1 現状確認（dev 環境 DDB）

```pwsh
$env:AWS_PROFILE = "AWS-security-check"

# Employee-dev / Cycle-dev / KeywordDictionary-dev の件数確認
aws dynamodb scan --table-name Employee-dev          --select COUNT --output text --query "Count" --region ap-northeast-1
aws dynamodb scan --table-name Cycle-dev             --select COUNT --output text --query "Count" --region ap-northeast-1
aws dynamodb scan --table-name KeywordDictionary-dev --select COUNT --output text --query "Count" --region ap-northeast-1
```

期待値（2026-06-29 時点）：

| Table                   | 期待件数 | 内訳                                                       |
| ----------------------- | -------- | ---------------------------------------------------------- |
| `Employee-dev`          | **10**   | EMP-0000〜EMP-0009、Phase 16.5 投入分                      |
| `Cycle-dev`             | 1 以上   | 過去 Cycle 履歴、新規起動には影響なし                      |
| `KeywordDictionary-dev` | **13**   | 自然日本語キーワード 6 件 + テスト 1 文字 6 件 + META 1 件 |

### 3.2 件数が期待値を下回る場合の再投入

`Employee-dev` が 10 未満、または `KeywordDictionary-dev` が 13 未満なら、seed スクリプトで再投入します。

```pwsh
# workspace root で実行
$env:AWS_PROFILE = "AWS-security-check"
$env:PYTHONUTF8 = "1"
uv run python infrastructure/scripts/seed_dev.py
```

実行ログの最終行が `[seed_dev] DONE.` で、`EmployeeTable_total = 10` / `DictTable_total = 13` / `HistoryTable_v7 = 12` が表示されれば OK。

### 3.3 MockMode 確認（既に deploy 済の確認のみ、再 deploy 不要）

```pwsh
aws cloudformation describe-stacks `
    --stack-name safety-confirmation-dev `
    --query "Stacks[0].Parameters[?ParameterKey=='MockMode'].ParameterValue|[0]" `
    --output text `
    --region ap-northeast-1
```

`true` が返れば OK。`false` または空なら本手順書のスコープ外（template.yaml / parameters/dev.json の修正 + redeploy が必要）。

---

## 4. ブラウザでの 1 巡実行

### 4.1 SPA アクセス

ブラウザ（Chrome / Edge 推奨）で以下にアクセス。

```
https://dn8bulnup9krf.cloudfront.net
```

CloudFront 経由で SPA が読み込まれ、未認証なので **管理者ログイン画面** が表示されます。

### 4.2 初回ログイン

| 入力欄         | 入力値                                                  |
| -------------- | ------------------------------------------------------- |
| メールアドレス | `integration-test-admin@example.com`（§2 で登録した値） |
| パスワード     | `TempPass!2026`（§2.2 で設定した値）                    |

「ログイン」ボタン押下 → 初回ログインのため自動的に **初回パスワード変更画面** へ遷移します。

### 4.3 パスワード変更

| 入力欄                     | 入力値（例）                                                                 |
| -------------------------- | ---------------------------------------------------------------------------- |
| 新しいパスワード           | 任意（8 文字以上、英大文字・小文字・数字・記号を含む。例: `IntegTest!2026`） |
| 新しいパスワード（確認用） | 同上                                                                         |

「パスワードを設定」ボタン押下 → 成功時はダッシュボードへ自動遷移。失敗時は画面に Cognito 側エラーが表示されるので、パスワードポリシー（8 文字以上、英大小数字記号必須）を満たしているか確認してください。

### 4.4 サイクル起動

ダッシュボード → 左メニューまたは画面遷移で **「サイクル起動」** ページへ移動。

| 操作                         | 説明                                    |
| ---------------------------- | --------------------------------------- |
| 「全員」チェックボックス     | **ON のまま** にする（mode=`ALL` 相当） |
| Retry_Count                  | `3`（表示のみ、変更不可）               |
| Retry_Interval（分）         | `5`（表示のみ、変更不可）               |
| 「サイクルを起動する」ボタン | 押下                                    |

成功時、画面下部に以下が表示されます。

```
Cycle ID:           <UUID>
Dictionary Version: 7
Status:             RUNNING
Started At:         2026-XX-XXTXX:XX:XXZ
```

`Dictionary Version = 7` であることを **必ず確認** してください（§3.1 で 13 件状態 = META.currentVersion=7 のはず）。

### 4.5 進行確認

サイクル起動結果の **「サイクルのステータスを見る」リンク** をクリック → サイクル状況ページに遷移。

- 起動直後: Status=`RUNNING`、Summary に未確定件数が表示される
- 経過観察: ページを **リロード** すると状態が更新される（自動更新無し、手動リロード前提）
- 通常完走: 約 **5 分程度** で Status=`COMPLETED` に遷移

> **注意**: Retry_Interval=5 分なので、末尾 7/8/9 の社員（mock では NO_ANSWER / BUSY / OTHER → リトライ対象）の再試行に時間を要します。気長に待つか、CloudWatch Logs で SFN 実行状況を直接確認してください。

参考: SFN execution 直接確認（任意）:

```pwsh
$env:AWS_PROFILE = "AWS-security-check"
aws stepfunctions list-executions `
    --state-machine-arn "arn:aws:states:ap-northeast-1:214046906694:stateMachine:safety-confirmation-cycle-dev" `
    --max-items 5 `
    --query "executions[].[status,startDate,executionArn]" `
    --output table `
    --region ap-northeast-1
```

### 4.6 結果確認

Status=`COMPLETED` 遷移後、「サイクル詳細を見る」または `/cycles/{cycleId}` URL を直接訪問。

#### 期待値（ADR-0010 §3.2 マッピング表通り）

10 名の Voice_Status が以下のように表示されること：

| 社員 ID  | 末尾 | 期待 Voice_Status | 想定マッチキーワード      |
| -------- | ---- | ----------------- | ------------------------- |
| EMP-0000 | 0    | **SAFE**          | `無事`                    |
| EMP-0001 | 1    | **SAFE**          | `大丈夫`                  |
| EMP-0002 | 2    | **SAFE**          | `無事`                    |
| EMP-0003 | 3    | **INJURED**       | `怪我`                    |
| EMP-0004 | 4    | **INJURED**       | `痛い`                    |
| EMP-0005 | 5    | **UNAVAILABLE**   | `動け`                    |
| EMP-0006 | 6    | **UNAVAILABLE**   | `出社不可`                |
| EMP-0007 | 7    | **UNREACHABLE**   | （3 回リトライ後）        |
| EMP-0008 | 8    | **UNREACHABLE**   | （3 回リトライ後）        |
| EMP-0009 | 9    | **UNREACHABLE**   | （3 回 OTHER 後上限到達） |

集計サマリ:

```
SAFE         : 3
INJURED      : 2
UNAVAILABLE  : 2
UNREACHABLE  : 3
total        : 10
```

**10/10 が期待値通り** であれば、画面からの mock 1 巡結合テストは **成功** です。

---

## 5. トラブルシュート

### 5.1 ログイン画面で「メールアドレスまたはパスワードが正しくありません」が表示される

- §2.5 で `FORCE_CHANGE_PASSWORD` 状態のユーザーが Administrator group に存在するか確認
- 一時パスワード `TempPass!2026` は SUPPRESS でメール送信していないため、§2.2 の値そのままを使う
- パスワードを忘れた場合は `admin-set-user-password` で再発行:

```pwsh
aws cognito-idp admin-set-user-password `
    --user-pool-id $USER_POOL_ID `
    --username $TEST_EMAIL `
    --password "TempPass!2026" `
    --no-permanent `
    --region ap-northeast-1
```

### 5.2 「サイクルを起動する」で 400 / 409 / 500 が返る

| HTTP | 主因                    | 対処                                                                   |
| ---- | ----------------------- | ---------------------------------------------------------------------- |
| 400  | 辞書 0 件               | §3.1 で `KeywordDictionary-dev` ≥ 13 を確認、不足なら §3.2 で再 seed   |
| 409  | 別 Cycle が RUNNING     | サイクル一覧ページで RUNNING を確認、終わるまで待つか TIMEOUT 待ち     |
| 500  | SFN StartExecution 失敗 | CloudWatch Logs `/aws/lambda/safety-confirmation-cycle-api-dev` を確認 |

### 5.3 Status が `RUNNING` のまま 60 分以上進まない

CycleFinalizer の 60 分タイムアウトで自動的に Status=`TIMEOUT` に遷移します。発生時は SFN execution status を確認:

```pwsh
aws stepfunctions describe-execution `
    --execution-arn "<list-executions で取得した最新の executionArn>" `
    --region ap-northeast-1
```

`FAILED` の場合は SFN history で失敗ステップを特定してください。

### 5.4 Voice_Status が期待値と異なる

末尾 0〜6 の SAFE / INJURED / UNAVAILABLE は **辞書のキーワードマッチ結果** に依存します。`KeywordDictionary-dev` の中身を直接確認:

```pwsh
aws dynamodb scan --table-name KeywordDictionary-dev `
    --query "Items[*].[category.S,keyword.S]" `
    --output table --region ap-northeast-1
```

期待される 13 件（カテゴリ + キーワード）が揃っているか確認してください。

---

## 6. 後始末（任意）

### 6.1 新規管理者ユーザーの削除

テスト完了後、§2 で作成した `integration-test-admin@example.com` を削除する場合：

```pwsh
aws cognito-idp admin-delete-user `
    --user-pool-id $USER_POOL_ID `
    --username $TEST_EMAIL `
    --region ap-northeast-1
```

`admin-remove-user-from-group` は不要（`admin-delete-user` がグループ所属も解除）。

### 6.2 テスト Cycle データの削除（任意）

テスト Cycle / Response は **90 日 LCM** で自動削除されます（S3 録音 / Transcript 含む）。即時削除したい場合は `Cycle-dev` / `Response-dev` から該当 `cycleId` を `delete-item` で個別削除してください。

---

## 7. 参考リンク

- [ADR-0010: mock-on-aws-dev](../decisions/0010-mock-on-aws-dev.md) — mock 経路の設計判断
- [Phase 16.5 検証ログ](../notes/16-5-mock-e2e-validation.md) — boto3 直叩き版 9/9 達成記録
- [ADR-0009: Connect 実機検証 findings](../decisions/0009-connect-realworld-validation.md) — 本手順書スコープ外（実 Connect）の設計
- tasks.md Phase 17.8 — 本手順書の起票元タスク
