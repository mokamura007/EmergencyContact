# 安否確認システム 操作手順書

> 対象読者: システム管理者・運用担当者  
> 最終更新: 2026-07

---

## 1. 前提条件

### 1.1 必要なソフトウェア

| ソフトウェア | バージョン | 用途 |
|-------------|-----------|------|
| AWS CLI | v2.12+ | AWS リソース操作 |
| PowerShell 7 | 7.x | デプロイスクリプト実行 |
| Node.js | 18+ | フロントエンドビルド |
| uv | 0.9+ | Python パッケージ管理 |
| Python | 3.12 | バックエンド開発 |

### 1.2 AWS プロファイル設定

`~/.aws/credentials` に以下のプロファイルが必要:

```ini
[AWS-security-check]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
region = ap-northeast-1
```

動作確認:

```powershell
aws sts get-caller-identity --profile AWS-security-check
```

### 1.3 リポジトリ準備

```powershell
git clone <リポジトリURL>
cd EmergencyContact-main

# バックエンド依存インストール
cd backend
uv sync

# フロントエンド依存インストール
cd ../frontend
npm install
```

---

## 2. デプロイ手順

### 2.1 バックエンド (Lambda + インフラ) のデプロイ

```powershell
# リポジトリルートで実行
pwsh -File scripts/deploy_dev.ps1
```

このスクリプトは以下を自動実行する:
1. Lambda Layer のビルド
2. CloudFormation パッケージング（S3 アップロード）
3. CloudFormation デプロイ（スタック作成/更新）

所要時間: 約 3〜10 分

### 2.2 フロントエンドのデプロイ

```powershell
cd frontend

# 1. ビルド
npm run build

# 2. S3 にアップロード
aws s3 sync .\dist\ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete --profile AWS-security-check --region ap-northeast-1

# 3. CloudFront キャッシュ無効化
aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" --profile AWS-security-check
```

注意: Invalidation の完了まで数分かかる場合がある。

### 2.3 全体デプロイ（バックエンド + フロントエンド）

両方に変更がある場合は、2.1 → 2.2 の順で実行する。

---

## 3. スタック更新とロールバック

### 3.1 スタック状態確認

```powershell
aws cloudformation describe-stacks --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1 --query "Stacks[0].StackStatus"
```

### 3.2 スタックイベント確認（エラー時）

```powershell
aws cloudformation describe-stack-events --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1 --query "StackEvents[?ResourceStatus=='CREATE_FAILED' || ResourceStatus=='UPDATE_FAILED']"
```

### 3.3 手動ロールバック

デプロイ失敗で `UPDATE_ROLLBACK_FAILED` になった場合:

```powershell
aws cloudformation continue-update-rollback --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1
```

### 3.4 変更セットで差分確認（慎重なデプロイ）

```powershell
aws cloudformation create-change-set --stack-name safety-confirmation-dev --change-set-name my-changeset --template-body file://infrastructure/build/packaged.yaml --parameter-overrides file://infrastructure/parameters/dev.json --capabilities CAPABILITY_NAMED_IAM --profile AWS-security-check --region ap-northeast-1

# 差分確認
aws cloudformation describe-change-set --stack-name safety-confirmation-dev --change-set-name my-changeset --profile AWS-security-check --region ap-northeast-1

# 実行
aws cloudformation execute-change-set --stack-name safety-confirmation-dev --change-set-name my-changeset --profile AWS-security-check --region ap-northeast-1
```

---

## 4. Cognito ユーザー管理

### 4.1 管理者ユーザーの作成

```powershell
# 1. ユーザー作成（一時パスワード付き）
aws cognito-idp admin-create-user --user-pool-id <USER_POOL_ID> --username admin@example.com --temporary-password "TempP@ss1234!" --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true --profile AWS-security-check --region ap-northeast-1

# 2. Administrator グループに追加
aws cognito-idp admin-add-user-to-group --user-pool-id <USER_POOL_ID> --username admin@example.com --group-name Administrator --profile AWS-security-check --region ap-northeast-1
```

注意: ユーザーは初回ログイン時に NEW_PASSWORD_REQUIRED チャレンジに対応する必要がある（SPA の `/new-password` 画面）。

### 4.2 User Pool ID の確認

```powershell
aws cloudformation describe-stacks --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1 --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text
```

### 4.3 ユーザー一覧

```powershell
aws cognito-idp list-users --user-pool-id <USER_POOL_ID> --profile AWS-security-check --region ap-northeast-1
```

### 4.4 パスワードリセット

```powershell
aws cognito-idp admin-set-user-password --user-pool-id <USER_POOL_ID> --username admin@example.com --password "NewP@ssw0rd!" --permanent --profile AWS-security-check --region ap-northeast-1
```

### 4.5 アカウントロック解除

Lockout テーブルから該当レコードを削除:

```powershell
aws dynamodb delete-item --table-name Lockout-dev --key '{"userId":{"S":"admin@example.com"}}' --profile AWS-security-check --region ap-northeast-1
```

---

## 5. DynamoDB データ確認

### 5.1 テーブル一覧

```powershell
aws dynamodb list-tables --profile AWS-security-check --region ap-northeast-1 --query "TableNames[?contains(@, 'dev')]"
```

### 5.2 社員データ確認

```powershell
# 全件取得（300件以下想定）
aws dynamodb scan --table-name Employee-dev --profile AWS-security-check --region ap-northeast-1

# 特定社員
aws dynamodb get-item --table-name Employee-dev --key '{"employeeId":{"S":"<UUID>"}}' --profile AWS-security-check --region ap-northeast-1
```

### 5.3 サイクルデータ確認

```powershell
# 実行中サイクル
aws dynamodb query --table-name Cycle-dev --index-name StatusStartedAtIndex --key-condition-expression "#s = :status" --expression-attribute-names '{"#s":"status"}' --expression-attribute-values '{":status":{"S":"RUNNING"}}' --profile AWS-security-check --region ap-northeast-1
```

### 5.4 応答データ確認

```powershell
# 特定サイクルの全応答
aws dynamodb query --table-name Response-dev --key-condition-expression "cycleId = :cid" --expression-attribute-values '{":cid":{"S":"<CYCLE_UUID>"}}' --profile AWS-security-check --region ap-northeast-1
```

### 5.5 キーワード辞書確認

```powershell
aws dynamodb scan --table-name KeywordDictionary-dev --profile AWS-security-check --region ap-northeast-1
```

---

## 6. CloudWatch ログ確認

### 6.1 ログ確認

```powershell
# Lambda ログ一覧
aws logs describe-log-groups --profile AWS-security-check --region ap-northeast-1 --query "logGroups[?contains(logGroupName, 'safety-confirmation')]"

# 最新ログ確認（例: CycleApi）
aws logs tail /aws/lambda/safety-confirmation-dev-CycleApi --since 1h --profile AWS-security-check --region ap-northeast-1
```

### 6.2 エラーのみ抽出

```powershell
aws logs filter-log-events --log-group-name /aws/lambda/safety-confirmation-dev-CycleApi --filter-pattern "ERROR" --start-time (Get-Date).AddHours(-1).ToUnixTimeMilliseconds() --profile AWS-security-check --region ap-northeast-1
```

### 6.3 Step Functions 実行確認

```powershell
# 実行一覧
aws stepfunctions list-executions --state-machine-arn <STATE_MACHINE_ARN> --status-filter RUNNING --profile AWS-security-check --region ap-northeast-1

# 実行詳細
aws stepfunctions describe-execution --execution-arn <EXECUTION_ARN> --profile AWS-security-check --region ap-northeast-1

# 実行履歴
aws stepfunctions get-execution-history --execution-arn <EXECUTION_ARN> --profile AWS-security-check --region ap-northeast-1
```

---

## 7. トラブルシューティング

### 7.1 デプロイ失敗

| 症状 | 原因 | 対処 |
|------|------|------|
| `Unable to load paramfile, text contents could not be decoded` | template.yaml の日本語コメントが原因 | PowerShell で `Get-Content -Raw -Encoding UTF8` 経由で渡す |
| `uv run cfn-lint` → `program not found` | Windows + uv の既知問題 | `.\.venv\Scripts\cfn-lint.exe` を直接実行 |
| `UPDATE_ROLLBACK_FAILED` | リソース削除失敗 | `continue-update-rollback` を実行 |
| `No changes to deploy` | テンプレートに変更なし | 正常動作（`--no-fail-on-empty-changeset` により成功扱い） |

### 7.2 認証エラー

| 症状 | 原因 | 対処 |
|------|------|------|
| ログイン画面でエラー | パスワード間違い | 正しいパスワードで再試行 |
| ロックアウト | 5回連続失敗 | Lockout テーブルのレコード削除（§4.5参照） |
| `/forbidden` 表示 | Administrator グループ未所属 | `admin-add-user-to-group` で追加 |
| API 401 | トークン期限切れ | ブラウザリロードで再認証 |

### 7.3 サイクル関連

| 症状 | 原因 | 対処 |
|------|------|------|
| サイクル起動エラー「実行中のサイクルが存在」 | 前回サイクルが完了していない | Cycle テーブルの status を確認、手動更新が必要な場合あり |
| サイクルが TIMEOUT | 60分 SLA 超過 | EventBridge → CycleFinalizer のログ確認 |
| ステータスが PENDING のまま | Transcribe/KeywordMatcher 処理待ち | CloudWatch ログで各 Lambda のエラーを確認 |

### 7.4 フロントエンド

| 症状 | 原因 | 対処 |
|------|------|------|
| 古い画面が表示される | CloudFront キャッシュ | Invalidation 実行（§2.2 手順3） |
| API エラー | CORS or バックエンド障害 | ブラウザ DevTools で Network タブ確認 |
| ビルドエラー | npm 依存不整合 | `rm -rf node_modules && npm install` |

---

## 8. キーワード辞書管理 (API 経由)

### 8.1 辞書取得

```powershell
# Bearer トークンは Cognito から取得したもの
$token = "<ID_TOKEN>"
$apiUrl = "<API_GATEWAY_URL>"

Invoke-RestMethod -Uri "$apiUrl/keyword-dictionary" -Headers @{Authorization="Bearer $token"} -Method GET
```

### 8.2 キーワード追加

```powershell
$body = @{category="SAFE"; keyword="無事です"} | ConvertTo-Json
Invoke-RestMethod -Uri "$apiUrl/keyword-dictionary" -Headers @{Authorization="Bearer $token"} -Method POST -Body $body -ContentType "application/json"
```

### 8.3 キーワード無効化

```powershell
Invoke-RestMethod -Uri "$apiUrl/keyword-dictionary/SAFE/無事です" -Headers @{Authorization="Bearer $token"} -Method PATCH -Body '{"enabled":false}' -ContentType "application/json"
```

---

## 9. 監視

### 9.1 主要監視ポイント

| 対象 | 確認方法 | 注意点 |
|------|----------|--------|
| Lambda エラー率 | CloudWatch Metrics → Lambda → Errors | 閾値超過でアラート |
| API Gateway 5xx | CloudWatch Metrics → API Gateway → 5XXError | バックエンド障害検知 |
| DynamoDB スロットリング | CloudWatch Metrics → DynamoDB → ThrottledRequests | 容量不足 |
| Step Functions 失敗 | CloudWatch Metrics → States → ExecutionsFailed | サイクル実行障害 |

### 9.2 ログ保持

全 CloudWatch Logs のリテンション: 90 日（`LogRetentionDays` パラメータで設定）

---

## 10. バックアップとリカバリ

### 10.1 DynamoDB

全テーブルで PITR (Point-in-Time Recovery) が有効。任意の時点（最大 35 日前）に復元可能。

```powershell
aws dynamodb restore-table-to-point-in-time --source-table-name Employee-dev --target-table-name Employee-dev-restored --restore-date-time "2026-07-01T00:00:00Z" --profile AWS-security-check --region ap-northeast-1
```

### 10.2 S3

- 録音・Transcript: 90 日ライフサイクルで自動削除
- Versioning: OFF（コスト削減）
- 復元が必要な場合は S3 のアクセスログから確認

---

## 11. 定期メンテナンス

| タスク | 頻度 | 手順 |
|--------|------|------|
| Cognito ユーザー確認 | 月次 | 不要ユーザーの無効化 |
| キーワード辞書レビュー | 四半期 | 精度確認と辞書更新 |
| CloudWatch コスト確認 | 月次 | ログ量・API 呼出数確認 |
| セキュリティパッチ | 随時 | Python/Node.js バージョン確認 |
