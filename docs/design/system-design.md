# 安否確認システム 設計書

> 対象読者: 後任エンジニア・保守担当者  
> 最終更新: 2026-07

---

## 1. システム概要

本システムは、災害発生時に社員（最大 300 名）の安否を自動架電で確認する AWS サーバーレスシステムである。

管理者が管理画面からサイクルを起動すると、Amazon Connect（または Mock モード）を介して社員に自動発信し、音声応答を Amazon Transcribe でテキスト化、キーワード辞書に基づき安否ステータス（SAFE / INJURED / UNAVAILABLE / OTHER）を自動判定する。

| 項目 | 値 |
|------|-----|
| AWS リージョン | ap-northeast-1（東京）固定 |
| 対象規模 | 最大 300 名 |
| バックエンド言語 | Python 3.12 |
| フロントエンド | React + TypeScript + Pico CSS |
| IaC | 単一 CloudFormation テンプレート |
| 配信 | CloudFront + S3（SPA） |
| 認証 | Amazon Cognito（管理者ロールのみ） |

---

## 2. アーキテクチャ全体図

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          クライアント（ブラウザ）                          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTPS
                ┌───────────────▼───────────────┐
                │   CloudFront + S3 (SPA配信)    │
                │   React + TypeScript           │
                └───────────────┬───────────────┘
                                │ REST API (JWT)
                ┌───────────────▼───────────────┐
                │   API Gateway (REST)           │
                │   + Cognito Authorizer         │
                └───────────────┬───────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
┌───────▼──────┐  ┌─────────────▼────────────┐  ┌──────▼───────┐
│ EmployeeApi  │  │ CycleApi / ResponseApi   │  │ DictionaryApi│
│ RecordingApi │  │                          │  │              │
└───────┬──────┘  └────────────┬─────────────┘  └──────┬───────┘
        │                      │                        │
        │         ┌────────────▼─────────────┐          │
        │         │  Step Functions           │          │
        │         │  (Map MaxConcurrency=10)  │          │
        │         └────────────┬─────────────┘          │
        │                      │                        │
        │         ┌────────────▼─────────────┐          │
        │         │  ConnectDispatcher       │          │
        │         │  → Amazon Connect 発信    │          │
        │         └────────────┬─────────────┘          │
        │                      │                        │
        │         ┌────────────▼─────────────┐          │
        │         │  CallEndHandler          │          │
        │         │  → 録音 S3 保存           │          │
        │         └────────────┬─────────────┘          │
        │                      │                        │
        │         ┌────────────▼─────────────┐          │
        │         │  TranscribeStarter       │          │
        │         │  → Transcribe ジョブ起動  │          │
        │         └────────────┬─────────────┘          │
        │                      │                        │
        │         ┌────────────▼─────────────┐          │
        │         │  KeywordMatcher          │          │
        │         │  → Voice_Status 判定     │          │
        │         └────────────┬─────────────┘          │
        │                      │                        │
        ▼                      ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│              DynamoDB テーブル群 (SSE-KMS暗号化)               │
│  Employee / Cycle / Response / RecordingMeta /               │
│  TranscriptMeta / KeywordDictionary / InboundContact /       │
│  Lockout                                                     │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│              S3 バケット (SSE-KMS + 90日ライフサイクル)         │
│  Recordings バケット / Transcripts バケット                    │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. AWS リソース一覧

### 3.1 コンピューティング

| リソース | 役割 |
|----------|------|
| API Gateway (REST) | フロントエンドからの API リクエスト受付、Cognito Authorizer |
| Lambda x 20+ 関数 | ビジネスロジック実行（Python 3.12 / ARM64） |
| Step Functions (Standard) | 安否確認サイクルの並列発信制御 |
| EventBridge | 30分/60分 SLA タイマー |

### 3.2 ストレージ

| リソース | 役割 |
|----------|------|
| DynamoDB x 9 テーブル | 社員マスタ、サイクル、応答、メタデータ等 |
| S3 Recordings バケット | 通話録音保管（90日ライフサイクル） |
| S3 Transcripts バケット | 音声認識結果テキスト保管（90日ライフサイクル） |
| S3 SPA バケット | フロントエンド静的ファイル配信 |
| S3 CFn Artifacts バケット | デプロイ用パッケージ保管 |

### 3.3 認証・セキュリティ

| リソース | 役割 |
|----------|------|
| Cognito User Pool | 管理者認証、JWT 発行 |
| KMS CMK | DynamoDB / S3 の暗号化キー |
| IAM ロール | Lambda 関数ごとの最小権限 |

### 3.4 配信・通信

| リソース | 役割 |
|----------|------|
| CloudFront | SPA 配信 + HTTPS 終端 |
| Amazon Connect | 自動架電（アウトバウンド）、着信受付（インバウンド） |
| Amazon Transcribe | 音声→テキスト変換 |
| Amazon Polly | TTS ガイダンス再生 |

---

## 4. データモデル（DynamoDB テーブル）

### 4.1 Employee テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `Employee-{env}` |
| PK | `employeeId` (UUID) |
| GSI | `PhoneNumberIndex` (PK=phoneNumber) |
| 暗号化 | SSE-KMS |
| PITR | ON |

主要属性: `employeeId`, `name`, `phoneNumber` (E.164), `employeeNumber`, `department`, `role`, `deleted`, `createdAt`, `updatedAt`

### 4.2 Cycle テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `Cycle-{env}` |
| PK | `cycleId` (UUID) |
| GSI | `StatusStartedAtIndex` (PK=status, SK=startedAt) |
| 暗号化 | SSE-KMS |

主要属性: `cycleId`, `startedBy`, `startedAt`, `mode` (ALL/UNREACHABLE_ONLY), `retryCount`, `retryIntervalMinutes`, `targetCount`, `dictionaryVersion`, `status`, `completedAt`, `executionArn`

### 4.3 Response テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `Response-{env}` |
| PK | `cycleId` |
| SK | `employeeId` |
| 暗号化 | SSE-KMS |

主要属性: `voiceStatus`, `callAttempts`, `callResultCodes` (List), `lastDispatchAt`, `lastResponseAt`, `transcriptExcerpt`, `matchedKeywords`

### 4.4 RecordingMetadata テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `RecordingMetadata-{env}` |
| PK | `cycleId` (インバウンド: `INBOUND#{contactId}`) |
| SK | `employeeIdSeq` |
| 暗号化 | SSE-KMS |

### 4.5 TranscriptMetadata テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `TranscriptMetadata-{env}` |
| PK | `cycleId` or `INBOUND#{contactId}` |
| SK | `employeeIdSeq` |
| 暗号化 | SSE-KMS |

### 4.6 KeywordDictionary テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `KeywordDictionary-{env}` |
| PK | `category` (SAFE/INJURED/UNAVAILABLE/META) |
| SK | `keyword` |
| 暗号化 | SSE-KMS |

META レコード (PK=META, SK=META) に `currentVersion` を保持。バージョン管理は `ConditionExpression` で原子的インクリメント。

### 4.7 KeywordDictionaryHistory テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `KeywordDictionaryHistory-{env}` |
| PK | `version` (N) |
| SK | `categoryKeyword` |

### 4.8 InboundContact テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `InboundContact-{env}` |
| PK | `contactId` |
| 暗号化 | SSE-KMS |

### 4.9 Lockout テーブル

| 項目 | 値 |
|------|-----|
| テーブル名 | `Lockout-{env}` |
| PK | ユーザー識別子 |
| TTL | `expireAt` (30分) |

---

## 5. API エンドポイント一覧

全エンドポイントは Cognito Authorizer (Administrator グループ必須) で保護される。

### 5.1 社員管理

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/employees` | 社員一覧取得 |
| POST | `/employees` | 社員新規追加 |
| GET | `/employees/{id}` | 社員詳細取得 |
| PUT | `/employees/{id}` | 社員情報更新 |
| DELETE | `/employees/{id}` | 社員論理削除 |
| POST | `/employees/import` | CSV 一括インポート |

### 5.2 安否確認サイクル

| メソッド | パス | 説明 |
|----------|------|------|
| POST | `/cycles` | サイクル起動 |
| GET | `/cycles` | サイクル一覧 |
| GET | `/cycles/{id}` | サイクル詳細 |
| GET | `/cycles/{id}/status` | リアルタイムステータス（ポーリング用） |

### 5.3 録音・Transcript

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/cycles/{id}/recordings/{employeeId}/{seq}` | 録音署名付き URL |
| GET | `/cycles/{id}/transcripts/{employeeId}/{seq}` | Transcript 全文 |
| GET | `/inbound/{contactId}/recording` | インバウンド録音 URL |
| GET | `/inbound/{contactId}/transcript` | インバウンド Transcript |

### 5.4 キーワード辞書

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/keyword-dictionary` | 辞書全体取得 |
| POST | `/keyword-dictionary` | キーワード追加 |
| DELETE | `/keyword-dictionary/{category}/{keyword}` | キーワード削除 |
| PATCH | `/keyword-dictionary/{category}/{keyword}` | 有効フラグ更新 |
| GET | `/keyword-dictionary/version` | 辞書バージョン取得 |

### 5.5 認証補助

| メソッド | パス | 説明 |
|----------|------|------|
| POST | `/auth/record-failure` | 認証失敗記録（Authorizer なし） |

---

## 6. Lambda 関数一覧

| Lambda 名 | 責務 | タイムアウト |
|-----------|------|-------------|
| `EmployeeApi` | 社員 CRUD + CSV インポート | 30秒 (CSV: 60秒) |
| `CycleApi` | サイクル起動・状態取得 | 15秒 |
| `ResponseApi` | 応答履歴取得 | 10秒 |
| `RecordingApi` | 署名付き URL 発行 | 5秒 |
| `DictionaryApi` | キーワード辞書 CRUD | 10秒 |
| `LoadTargets` | 対象者抽出 (SFN内) | 30秒 |
| `ConnectDispatcher` | Connect 発信 / Mock発信 | 15秒 |
| `CallEndHandler` | 通話終了処理 + SendTaskSuccess | 10秒 |
| `TranscribeStarter` | Transcribe ジョブ起動 / Mock | 15秒 |
| `KeywordMatcher` | テキスト→安否ステータス判定 | 15秒 |
| `RetryEvaluator` | 再発信要否判定 | 5秒 |
| `CycleFinalizer` | 完了/タイムアウト処理 | 30秒 |
| `RecordingMetadataWriter` | 録音メタデータ書込 | 15秒 |
| `RecordingRelocator` | Connect録音→管理バケット移動 | 15秒 |
| `InboundHandler` | 着信受付・Cycle 紐付け | 15秒 |
| `AuthPreAuth` | 認証前ロックアウト判定 | 5秒 |
| `AuthPostAuth` | 認証成功ログ + ロック解除 | 5秒 |
| `AuthPreSignup` | 自己サインアップ拒否 | 5秒 |
| `AuthFailureReporter` | 認証失敗記録 | 5秒 |

全 Lambda 共通: Python 3.12 / ARM64 / メモリ 512MB（一部 1024MB）

---

## 7. Step Functions フロー

### 7.1 安否確認サイクル実行フロー

```
[開始]
  │
  ▼
LoadTargets（対象者抽出: ALL or UNREACHABLE_ONLY）
  │
  ▼
StartTimers（EventBridge: 30分/60分タイマー設定）
  │
  ▼
CallMap (Map MaxConcurrency=10)
  ├─ InitAttempt（Response初期化）
  ├─ Dispatch（ConnectDispatcher: .waitForTaskToken）
  ├─ AwaitResult（TaskToken待ち: 最大90秒）
  ├─ WaitForTranscribe（Transcribe完了待ち: 最大60秒）
  ├─ EvaluateRetry（RetryEvaluator判定）
  │   ├── 再発信必要 → WaitInterval → Dispatch に戻る
  │   └── 完了 or 上限 → FinalizeOne
  └─ FinalizeOne
  │
  ▼
Aggregate（全Response集計）
  │
  ▼
Finalize（Cycleステータス更新 → COMPLETED/TIMEOUT）
```

### 7.2 再発信ロジック

- 再発信する: `OTHER`, `NO_ANSWER`, `BUSY`, `VOICEMAIL`, `ERROR`, `TRANSCRIBE_FAILED`
- 再発信しない: `SAFE`, `INJURED`, `UNAVAILABLE`
- 上限: `callAttempts >= retryCount + 1` で打ち切り → `UNREACHABLE`

---

## 8. Mock モード（ADR-0010）

### 8.1 概要

dev 環境では Amazon Connect / Transcribe の契約前でもシステム全体を動作確認できるよう、Mock モードを実装している。

### 8.2 有効化条件

```python
MOCK_MODE_ENABLED = (
    os.environ.get("MOCK_MODE", "false").lower() == "true"
    and os.environ.get("ENVIRONMENT_NAME", "") != "prod"
)
```

- `parameters/dev.json` で `MockMode: true` を設定
- prod 環境では環境名チェックにより強制 OFF（二重ガード）
- CFn テンプレートの `Rules.ProdMockModeForbidden` で三重ガード

### 8.3 Mock 時の動作

| コンポーネント | 本番動作 | Mock 動作 |
|---------------|----------|-----------|
| ConnectDispatcher | Connect API で発信 | S3 に擬似 wav (1KB) を PutObject |
| TranscribeStarter | Transcribe ジョブ起動 | S3 に擬似 transcript JSON を PutObject |
| KeywordMatcher | 変更なし（実 S3 から transcript を読む） | 同左 |
| CallEndHandler | Connect から呼出 | ConnectDispatcher が直接呼出 |

### 8.4 擬似応答パターン (ADR-0010 §3.2)

`employeeId` 末尾文字で決定論的に Voice_Status を分岐:
- 末尾 0-7: SAFE
- 末尾 8-9: INJURED
- 末尾 a-d: UNAVAILABLE
- 末尾 e-f: OTHER (→ 再発信対象)

---

## 9. 認証・認可

### 9.1 Cognito User Pool

| 設定 | 値 |
|------|-----|
| プール名 | `safety-confirmation-{env}` |
| 自己サインアップ | 無効（管理者作成のみ） |
| グループ | `Administrator` のみ |
| パスワード | 12文字以上, 大文字/小文字/数字/記号必須 |
| ID Token有効期限 | 1時間 |
| Access Token有効期限 | 1時間 |
| Refresh Token有効期限 | 30日 |
| App Client | 1個 (SPA用, USER_SRP_AUTH, Client Secret なし) |

### 9.2 ロックアウト

- PreAuthentication Lambda で Lockout テーブル参照
- 5回連続失敗 → 30分ロックアウト
- 認証成功時に PostAuthentication Lambda が `failedAts` クリア
- SPA 側で認証失敗検知時に `POST /auth/record-failure` を呼出

### 9.3 API 認可

全 API エンドポイント（`/auth/record-failure` を除く）は Cognito Authorizer で保護。Lambda 内で `cognito:groups` に `Administrator` が含まれることを検証。

---

## 10. フロントエンドアーキテクチャ

### 10.1 技術スタック

| 項目 | 値 |
|------|-----|
| フレームワーク | React 18 + TypeScript |
| CSS | Pico CSS（クラスレス CSS） |
| ルーティング | React Router v6 |
| ビルド | Vite |
| 認証 | AWS Amplify (Cognito) |

### 10.2 画面構成（ルーティング）

| パス | 画面 | 認証 |
|------|------|------|
| `/login` | ログイン | 不要 |
| `/new-password` | 初回パスワード変更 | 不要 |
| `/forbidden` | 403 | 不要 |
| `/` | ダッシュボード | 必要 |
| `/employees` | 社員一覧 | 必要 |
| `/employees/new` | 社員追加 | 必要 |
| `/employees/:id/edit` | 社員編集 | 必要 |
| `/employees/import` | CSV インポート | 必要 |
| `/cycles/new` | サイクル起動 | 必要 |
| `/cycles` | サイクル一覧 | 必要 |
| `/cycles/:cycleId` | サイクル詳細 | 必要 |
| `/cycles/:cycleId/status` | リアルタイムステータス | 必要 |
| `/cycles/:cycleId/transcripts/:employeeId/:seq` | Transcript 閲覧 | 必要 |
| `/inbound` | インバウンド一覧 | 必要 |
| `/inbound/:contactId/transcript` | インバウンド Transcript | 必要 |
| `/dictionary` | キーワード辞書管理 | 必要 |

### 10.3 認証フロー

1. 未認証ユーザーは `AuthGuard` により `/login` へリダイレクト
2. ログイン成功後、Cognito グループチェック
3. Administrator 以外は `/forbidden` 表示
4. セッション期限切れは `SessionExpiredListener` が検知しログアウト

### 10.4 配信

- S3 バケット: `safety-confirmation-spa-dev-214046906694-ap-northeast-1`
- CloudFront: `https://dn8bulnup9krf.cloudfront.net`
- SPA fallback: CloudFront で 403/404 → `/index.html` に転送

---

## 11. 設計判断と主要トレードオフ

| 判断 | 理由 |
|------|------|
| 単一 CFn テンプレート | 環境差分を Parameters に集約。スタック間依存を排除 |
| VPC 不使用 | マネージドサービスのみ使用。VPC は複雑さとコスト増のみ |
| DTMF 不採用 | 音声認識+キーワードマッチングに一本化。高齢者にも対応容易 |
| Step Functions Standard | Map ステートの並列制御が必要。Express では不可 |
| 10 並列上限 | Connect 同時アクティブコール制限に合わせる |
| 辞書バージョンスナップショット | サイクル実行中の辞書変更で判定結果が変わることを防止 |
| 論理削除 | 物理削除すると過去サイクルの参照整合性が壊れる |
| Mock モード (ADR-0010) | Connect 契約前に全体 E2E を確認可能 |
| Python 3.12 (ADR-0001) | チーム習熟度、boto3 親和性 |
| 部分文字列マッチ | 日本語は形態素解析が複雑。シンプルな部分一致で十分 |

---

## 12. 環境設定（parameters/dev.json）

主要パラメータ:

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| EnvironmentName | dev | 環境識別子 |
| MockMode | true | Mock モード有効 |
| MaxConcurrentCalls | 10 | 同時発信上限 |
| DefaultRetryCount | 3 | デフォルト再発信回数 |
| DefaultRetryIntervalMinutes | 5 | 再発信間隔(分) |
| RecordingsRetentionDays | 90 | 録音保持日数 |
| TranscriptsRetentionDays | 90 | Transcript 保持日数 |
| InboundReceptionWindowDays | 30 | インバウンド受付期間 |
| TranscribeLanguageCode | ja-JP | 音声認識言語 |
| LogRetentionDays | 90 | ログ保持日数 |
| ConnectInstanceId | (ダミーUUID) | Mock 時はダミー値 |

---

## 13. デプロイメント

### 13.1 バックエンド (CFn + Lambda)

```powershell
pwsh -File scripts/deploy_dev.ps1
```

内部処理:
1. Lambda Layer ビルド (`scripts/build_layer.ps1`)
2. `aws cloudformation package` → S3 にアーティファクト Upload
3. `aws cloudformation deploy` → スタック作成/更新

### 13.2 フロントエンド (S3 + CloudFront)

```powershell
cd frontend
npm run build
aws s3 sync .\dist\ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete --profile AWS-security-check --region ap-northeast-1
aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" --profile AWS-security-check
```

### 13.3 スタック情報

| 項目 | 値 |
|------|-----|
| スタック名 | `safety-confirmation-dev` |
| S3 Artifacts バケット | `safety-confirmation-cfn-artifacts-214046906694-ap-northeast-1` |
| AWS プロファイル | `AWS-security-check` |
| CloudFront Distribution ID | `EAXOBS3AIJQHH` |

---

## 14. 依存管理

### 14.1 バックエンド

- パッケージマネージャ: [uv](https://docs.astral.sh/uv/)
- 設定ファイル: `backend/pyproject.toml`
- 仮想環境: `backend/.venv`
- 品質ツール: ruff (lint), black (format), mypy (型検査), pytest (テスト)

### 14.2 フロントエンド

- パッケージマネージャ: npm
- 設定ファイル: `frontend/package.json`
- ビルド: Vite
- テスト: Vitest
