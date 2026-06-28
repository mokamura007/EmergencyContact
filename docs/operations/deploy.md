# 安否確認システム デプロイ手順書（dev / stg / prod）

- **対象読者**: 新規参画 SRE / 開発者、第三者として本手順のみで dev / stg / prod に CloudFormation デプロイを実施する人
- **対象環境**: AWS アカウント 214046906694（既存）または同等の検証アカウント、東京リージョン（`ap-northeast-1`）固定
- **関連要件**: Requirement 17（IaC / CloudFormation）— 特に AC 17.5（Connect インスタンス ID / アウトバウンド電話番号 ARN / インバウンド電話番号 ARN / Outbound Contact Flow ID / Inbound Contact Flow ID を Parameters として受領）
- **関連設計**: design.md「CloudFormation テンプレート設計 / Parameters」
- **関連 ADR**:
  - [`ADR-0005`](../decisions/0005-connect-mock-findings.md)：実機 Amazon Connect 検証は課金合意取得後に保留（stg / prod 実デプロイ前に必読）
  - [`ADR-0007`](../decisions/0007-acm-cert-issuance.md)：ACM 証明書の発行手順と CloudFormation Parameter 連携（カスタムドメイン利用時のみ）
- **関連運用ドキュメント**（**初回デプロイ後の通常運用は必ずこちらを参照**）:
  - [`runbook.md`](./runbook.md)：サイクル起動 / 中断 / タイムアウト / SNS 通知対応 / 日次チェック / 通常リリース
  - [`incident-response.md`](./incident-response.md)：DLQ 滞留 / 録音欠落 / Transcript 欠落 / SLA 違反 / 辞書誤更新 / Lambda 大量エラー / ロックアウト / インバウンド未登録番号大量着信
  - [`monitoring.md`](./monitoring.md)：CloudWatch アラーム 6 件 + Metric Filter 1 件の一覧

> 本書のスコープは **初回デプロイ（CREATE_COMPLETE 到達）+ 環境別差分 + ロールバック** に限定する。通常のリリース運用と通常運用は [`runbook.md`](./runbook.md) に従うこと。重複を避けるため、本書では CFn デプロイ前後の検証手順と、CFn 管理外リソース（Connect / 電話番号 / Contact Flow）の事前準備を集中的に記載する。

---

## 0. 全体像

```
[ Step 1 ]  AWS CLI / Python / cfn-lint / PowerShell or bash をセットアップ
              │
              ▼
[ Step 2 ]  AWS CLI Profile (`AWS-security-check` 推奨) を設定
              │
              ▼
[ Step 3 ]  ★ CFn 管理外リソースの事前準備 ★  (AC 17.5)
              ├ Connect インスタンス作成
              ├ アウトバウンド発信用電話番号 (DID) 取得
              ├ インバウンド代表電話番号 (DID) 取得
              ├ Outbound Contact Flow JSON 取込
              ├ Inbound Contact Flow JSON 取込
              └ 取得した ID / ARN を控える
              │
              ▼
[ Step 4 ]  CFn アーティファクト用 S3 バケット作成（先回りでも可）
              │
              ▼
[ Step 5 ]  parameters/{env}.json の TBD プレースホルダを実値に置換
              │
              ▼
[ Step 6 ]  validate.ps1 / validate.sh で cfn-lint パス確認
              │
              ▼
[ Step 7 ]  deploy.ps1 -DryRun → deploy.ps1 で CFn デプロイ
              │
              ▼
[ Step 8 ]  デプロイ後検証（アラーム / Lambda / SPA 動作）
              │
              ▼
[ Step 9 ]  初期データ投入（管理者ユーザー / 辞書）  ※ stg / prod は dev と同手順
              │
              ▼
       通常運用へ ( runbook.md )
```

---

## 1. 前提環境のセットアップ

### 1.1 必須ツール

| ツール                   | バージョン          | 用途                                                                               |
| ------------------------ | ------------------- | ---------------------------------------------------------------------------------- |
| AWS CLI                  | v2 系最新           | `cloudformation package` / `deploy`、`s3 cp`、`stepfunctions` 等                   |
| Python                   | 3.12                | `cfn-lint` 実行に必要、`parameters/*.json` の JSON 構文検証にも使用                |
| `cfn-lint`               | `1.52.0` 以上       | CloudFormation テンプレートのローカル検証                                          |
| PowerShell 7 系 (`pwsh`) | Windows 環境で必須  | `infrastructure/scripts/*.ps1` 実行、`build_layer.ps1` の SharedLayer ステージング |
| bash                     | macOS / Linux / WSL | `infrastructure/scripts/*.sh` 実行                                                 |

> Windows + PowerShell の場合、`build_layer.ps1` は `pwsh` 必須（`Get-ChildItem -Recurse -Directory -Filter` 等を使用）。bash 版 `deploy.sh` も内部で `pwsh -NoProfile -File build_layer.ps1` を呼ぶため、bash 環境でも PowerShell 7 のインストールが必要。

### 1.2 cfn-lint のインストール

```powershell
# Windows / PowerShell
python -m pip install --user 'cfn-lint==1.52.0'
```

```bash
# macOS / Linux / WSL
python3 -m pip install --user 'cfn-lint==1.52.0'
```

インストール先のパスは PowerShell 版 `validate.ps1` 内で
`C:\Users\<USER>\AppData\Local\Programs\Python\Python312\Scripts\cfn-lint.exe` を期待している。別パスにインストールした場合は `validate.ps1` 冒頭の `$CfnLintExe` を該当パスに書き換えること。

### 1.3 AWS CLI Profile の設定

本プロジェクト既定 Profile 名は **`AWS-security-check`**。`infrastructure/scripts/deploy.ps1` / `deploy.sh` / `validate.ps1` / `validate.sh` はこの Profile 名を **既定値** として埋め込み済みである。

```powershell
# 既定 Profile を使う場合
aws configure --profile AWS-security-check
# AWS Access Key ID, Secret Access Key, Default region (ap-northeast-1), Default output format (json)
```

#### 1.3.1 既定 Profile 名の override（Task 15.11）

`AWS-security-check` 以外の Profile を使う場合は、環境変数 `AWS_PROFILE` を設定してからスクリプトを実行する。4 スクリプトすべてが `AWS_PROFILE` を尊重し、未設定なら既定値 `AWS-security-check` にフォールバックする。

```powershell
# PowerShell：別 Profile を使う
$env:AWS_PROFILE = 'my-other-profile'
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev -DryRun

# 一時的に override してすぐ戻す
$env:AWS_PROFILE = 'my-other-profile'
try {
    pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev -DryRun
} finally {
    Remove-Item Env:\AWS_PROFILE
}
```

```bash
# bash：別 Profile を使う
AWS_PROFILE=my-other-profile bash infrastructure/scripts/deploy.sh dev --dry-run

# または export して一連のコマンドで共有
export AWS_PROFILE=my-other-profile
bash infrastructure/scripts/validate.sh
bash infrastructure/scripts/deploy.sh dev --dry-run
```

スクリプトはサマリー出力に `AwsProfile : <選択された Profile>` 行を表示するので、想定どおりの Profile で動いているか目視確認できる。

> AssumeRole 構成を使う場合は AWS CLI の `~/.aws/config` で `source_profile` / `role_arn` を組み合わせて構成し、その Profile 名を `AWS_PROFILE` に設定すること。AWS CLI 側で credential の解決が完結する。

### 1.4 Windows の文字コード設定（運用課題 #7）

PowerShell + Python の混在環境では、`PYTHONUTF8=1` を設定しないと `cfn-lint` 等が CP932 で stdout を解釈し UnicodeDecodeError を起こすケースがある。

```powershell
$env:PYTHONUTF8 = "1"
```

`deploy.ps1` / `validate.ps1` / `deploy.sh` / `validate.sh` は **スクリプト冒頭で自動的に `PYTHONUTF8=1` を設定** するため、これらのスクリプト経由でデプロイする限り手動設定は不要。ただし AWS CLI を直叩きする場面（後述ロールバック手順等）では Windows ターミナルセッションごとに上記を実行すること。

### 1.5 リポジトリ取得

```powershell
git clone <repository-url> safety-confirmation
cd safety-confirmation
```

リポジトリルートで以下のディレクトリが揃っていることを確認する：

```
safety-confirmation/
  backend/shared/              # Lambda Layer 原本
  infrastructure/
    template.yaml              # 192 KB、CFn 単一テンプレート
    parameters/{dev,stg,prod}.json
    scripts/{validate,deploy}.{ps1,sh}
    contact-flows/{inbound,outbound}.json   # Phase 7.1 / Phase 9.1 成果物
    .cfnlintrc                 # ignore_checks 4 件（運用課題 #4）
  scripts/build_layer.ps1      # SharedLayer ステージング
  docs/operations/{runbook,incident-response,monitoring}.md
  docs/decisions/0005-connect-mock-findings.md
  docs/decisions/0007-acm-cert-issuance.md
```

---

## 2. 事前準備：CloudFormation 管理外リソース（AC 17.5）

`infrastructure/template.yaml` は以下 5 リソースを **CFn 管理外** として扱い、Parameter で ID / ARN を受領する設計である（design.md「CloudFormation テンプレート設計 / Resources」末尾の注記、および requirements.md Acceptance Criterion 17.5 に基づく）。**本書で扱う ID / ARN は Step 3 で取得して控え、Step 5 で `parameters/{env}.json` に転記する。**

> ⚠️ **ADR-0005 課金合意の取得が前提**：Amazon Connect インスタンス購入 + DID 電話番号取得 + 通話 + Polly TTS + 録音 S3 は同時に課金が発生する。本プロジェクトは「課金合意取得後に保留」状態（[`ADR-0005`](../decisions/0005-connect-mock-findings.md) §6.1）。stg / prod の実機デプロイは **必ず課金合意を取得してから着手する**。

### 2.1 Amazon Connect インスタンスの作成

1. AWS マネジメントコンソールで対象アカウント・**東京リージョン（ap-northeast-1）** に切り替える。
2. Amazon Connect → 「インスタンスの追加」を開く。
3. **ID 管理**：「Amazon Connect 内でユーザーを管理」を選択（本システムは Cognito 認証側で完結するため Connect 側ユーザーは管理者 1 名のみで可）。
4. **アクセス URL**：環境を識別できる名前を入力（例：`safety-confirmation-stg`、`safety-confirmation-prod`）。
5. **管理者の作成**：暫定の管理者ユーザーを 1 名作成（Connect 設定変更用、本システム運用には使わない）。
6. **電話**：「**着信電話を許可**」「**発信電話を許可**」**両方を有効化** する（design.md「Connect_Caller / Inbound_Handler」より、Outbound / Inbound 両方を使う）。
7. **データストレージ**：既定（Amazon Connect 管理の S3 バケット）のままで構わない。録音は CFn 側の `RecordingsBucket` に **Connect Storage Config 経由で書き込む** 設計（Phase 7.2、Parameter `ManageConnectStorageConfig=false` の初期既定では本システムが Storage Config を作成せず、運用者が手動で Connect コンソールから「`recordings` バケットを指す Storage Config」を作成する必要がある）。
8. インスタンス作成完了後、コンソールで **インスタンス ARN** と **インスタンス ID** を控える。
   - インスタンス ARN 形式：`arn:aws:connect:ap-northeast-1:{account-id}:instance/{instance-uuid}`
   - インスタンス ID = ARN 末尾の UUID 部分

> `ManageConnectStorageConfig` を `true` にする場合は、Connect インスタンスに既存の CALL_RECORDINGS 用 Storage Config が **無いこと** を必ず確認する。`AWS::Connect::InstanceStorageConfig` は 1 インスタンスに 1 個までしか作成できない仕様で、既存があるとスタック更新時に失敗する（template.yaml の `ManageConnectStorageConfig` Parameter 説明を参照）。

### 2.2 アウトバウンド発信用電話番号（DID）の取得

1. Connect コンソール内 → 「ルーティング」→ 「電話番号」→ 「電話番号を取得」。
2. **国**：日本、**タイプ**：DID（直接ダイヤル）、**プレフィックス**：050 推奨。
3. 取得した電話番号を控える。Connect コンソールの電話番号一覧から、当該番号をクリックすると **電話番号 ARN** が表示される。
   - 電話番号 ARN 形式：`arn:aws:connect:ap-northeast-1:{account-id}:instance/{instance-uuid}/phone-number/{phone-number-uuid}`
   - **電話番号本体**（E.164 形式）：`+81XXXXXXXXXX`

### 2.3 インバウンド代表電話番号（DID）の取得

1. Step 2.2 と同手順で、**もう 1 件** 別の DID 電話番号を取得する。
2. 電話番号 ARN を控える（電話番号本体は CFn Parameter には不要、Inbound Contact Flow が処理する）。

> Outbound と Inbound で **電話番号を分ける** こと。Outbound 番号は SourcePhoneNumber として発信元表示に使われ、Inbound 番号は社員からの折返し受付に使われるため、用途が異なる（design.md「Connect_Caller」「Inbound_Handler」）。

### 2.4 Outbound Contact Flow の取込

1. Phase 7.1 の成果物 `infrastructure/contact-flows/outbound.json` をコピー。
2. Connect コンソール内 → 「ルーティング」→ 「フロー」→ 「フローを作成」→ 「インポート」。
3. ファイル選択で `outbound.json` を指定し、フロー名を `safety-confirmation-outbound` 等で保存。
4. 保存後、フロー編集画面 URL からフロー ID を抽出（URL 末尾の UUID）。
   - URL 例：`https://{instance-alias}.my.connect.aws/contact-flows/edit?id={contact-flow-uuid}`

### 2.5 Inbound Contact Flow の取込

1. Phase 9.1 の成果物 `infrastructure/contact-flows/inbound.json` を Step 2.4 と同手順でインポート。
2. フロー名を `safety-confirmation-inbound` 等で保存し、フロー ID を控える。

### 2.6 インバウンド電話番号への Contact Flow バインド

1. Connect コンソール内 → 「ルーティング」→ 「電話番号」で **インバウンド代表番号** を選択。
2. 「コンタクトフロー」プルダウンで `safety-confirmation-inbound` を選択し保存。

> アウトバウンド番号には Contact Flow をバインドする必要はない（CFn が `ConnectDispatcherFn` 経由で `StartOutboundVoiceContact` に `ContactFlowId` を直接指定するため）。

### 2.7 取得した ID / ARN のチェックリスト

Step 5 のパラメータ転記で使うので、以下を全て揃えてから次のステップに進む。

| キー                            | 形式                                                                                        | 取得元                      |
| ------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------- |
| `ConnectInstanceId`             | UUID（例：`12345678-aaaa-bbbb-cccc-1234567890ab`）                                          | 2.1 で控えたインスタンス ID |
| `ConnectInstanceArn`            | `arn:aws:connect:ap-northeast-1:{account}:instance/{instance-uuid}`                         | 2.1                         |
| `ConnectOutboundPhoneNumberArn` | `arn:aws:connect:ap-northeast-1:{account}:instance/{instance-uuid}/phone-number/{out-uuid}` | 2.2                         |
| `ConnectOutboundPhoneNumber`    | E.164（例：`+819012345678`）                                                                | 2.2 の電話番号本体          |
| `ConnectInboundPhoneNumberArn`  | `arn:aws:connect:ap-northeast-1:{account}:instance/{instance-uuid}/phone-number/{in-uuid}`  | 2.3                         |
| `OutboundContactFlowId`         | UUID                                                                                        | 2.4 のフロー ID             |
| `InboundContactFlowId`          | UUID                                                                                        | 2.5 のフロー ID             |

---

## 3. CFn アーティファクトバケットの準備

`aws cloudformation package` は Lambda コードや SharedLayer 等のローカルパス参照を S3 にアップロードして URL に書き換える。本プロジェクトでは **アカウント・リージョン固定** のバケット名を使う運用：

```
safety-confirmation-cfn-artifacts-{Account}-ap-northeast-1
```

本書執筆時点で **Account = 214046906694** 既存環境にバケット既存。新規 AWS アカウントで運用する場合は、デプロイ前にバケットを先回り作成する。

> ⚠️ 運用課題 #8（template > 51,200 bytes）：本プロジェクトの `template.yaml` は 192 KB あり、AWS CFn API `--template-body` の上限 51,200 bytes を超える。**S3 経由 deploy が必須** で、`deploy.ps1` / `deploy.sh` は `aws cloudformation package --s3-bucket ...` を使い、`packaged-template.yaml` を出力したうえで `aws cloudformation deploy --template-file packaged-template.yaml` を呼ぶ流れを実装済み。

### 3.1 バケット作成（既存の場合はスキップ）

```powershell
$env:PYTHONUTF8 = "1"
$Account = '214046906694'   # 対象アカウント
$Region  = 'ap-northeast-1'
$Bucket  = "safety-confirmation-cfn-artifacts-$Account-$Region"

aws s3 mb "s3://$Bucket" `
  --profile AWS-security-check `
  --region $Region
```

### 3.2 SSE-S3 / Versioning / Public Access Block の設定

```powershell
# SSE-S3 (AES256)
aws s3api put-bucket-encryption `
  --bucket $Bucket `
  --profile AWS-security-check --region $Region `
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Versioning Enabled
aws s3api put-bucket-versioning `
  --bucket $Bucket `
  --profile AWS-security-check --region $Region `
  --versioning-configuration Status=Enabled

# Public Access Block 全 true
aws s3api put-public-access-block `
  --bucket $Bucket `
  --profile AWS-security-check --region $Region `
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

### 3.3 設定確認

```powershell
aws s3api get-bucket-encryption     --bucket $Bucket --profile AWS-security-check --region $Region
aws s3api get-bucket-versioning     --bucket $Bucket --profile AWS-security-check --region $Region
aws s3api get-public-access-block   --bucket $Bucket --profile AWS-security-check --region $Region
```

期待値：

- Encryption: `AES256`（または KMS-aws/s3 でも可）
- Versioning: `Status=Enabled`
- Public Access Block: 4 つすべて `true`

---

## 4. パラメータ準備（CFn Parameters 全 24 項目）

`infrastructure/template.yaml` の Parameters セクションは **全 24 項目**。`infrastructure/parameters/{env}.json` がその実値を保持する。

- `dev.json`：実値投入済（Account 214046906694 既存 `safety-confirmation-dev` スタック現在値、Connect 関連はダミー UUID `00000000-...` で許容、ADR-0005 で実機検証保留中のため）
- `stg.json` / `prod.json`：`TBD-{ENV}-...` プレースホルダ済（[`infrastructure/parameters/README.md`](../../infrastructure/parameters/README.md)）

### 4.1 24 項目一覧と取得元

下表で「**Step**」列は本書内の取得手順、「**置換要否**」列は stg / prod で **必ず実値置換が必要** か / **既定値のままで可** かを示す。

| #   | ParameterKey                    | 型     | 既定 / 制約                         | 取得元 (Step) / 既定値判断                                                                                                 | 置換要否       |
| --- | ------------------------------- | ------ | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------- |
| 1   | `EnvironmentName`               | String | `dev` / `stg` / `prod`              | デプロイ環境名そのまま                                                                                                     | 必須           |
| 2   | `ConnectInstanceId`             | String | （任意 UUID）                       | Step 2.1                                                                                                                   | **必須**       |
| 3   | `ConnectInstanceArn`            | String | （ARN）                             | Step 2.1                                                                                                                   | **必須**       |
| 4   | `ConnectOutboundPhoneNumberArn` | String | （ARN）                             | Step 2.2                                                                                                                   | **必須**       |
| 5   | `ConnectOutboundPhoneNumber`    | String | E.164 (`^\+[1-9]\d{1,14}$`)         | Step 2.2 電話番号本体                                                                                                      | **必須**       |
| 6   | `ConnectInboundPhoneNumberArn`  | String | （ARN）                             | Step 2.3                                                                                                                   | **必須**       |
| 7   | `OutboundContactFlowId`         | String | UUID                                | Step 2.4                                                                                                                   | **必須**       |
| 8   | `InboundContactFlowId`          | String | UUID                                | Step 2.5                                                                                                                   | **必須**       |
| 9   | `DefaultRetryCount`             | Number | `3`（0〜5）                         | サイクル起動既定。要件 4.6                                                                                                 | 任意           |
| 10  | `DefaultRetryIntervalMinutes`   | Number | `5`（1〜60）                        | 要件 4.7                                                                                                                   | 任意           |
| 11  | `OutboundGuidanceText`          | String | 既定本文（template.yaml 既定）      | 災害文言を変えたい場合のみ                                                                                                 | 任意           |
| 12  | `InboundGuidanceText`           | String | 既定本文                            | 同上                                                                                                                       | 任意           |
| 13  | `LogRetentionDays`              | Number | `90`（CloudWatch Logs 許容値）      | dev / stg = `90`、prod = `365` 推奨（運用判断）                                                                            | 任意（環境別） |
| 14  | `OperatorEmail`                 | String | （メールアドレス）                  | SRE 個人 / 配信先メーリングリスト。stg は SRE 個人、prod は運用配信先                                                      | **必須**       |
| 15  | `RecordingsRetentionDays`       | Number | `90` 固定（AllowedValues=[90]）     | 要件 10.4                                                                                                                  | 不可変         |
| 16  | `TranscriptsRetentionDays`      | Number | `90` 固定（AllowedValues=[90]）     | 要件 6.5                                                                                                                   | 不可変         |
| 17  | `InboundReceptionWindowDays`    | Number | `30`（1〜90）                       | 要件 13.5 / 13.6                                                                                                           | 任意           |
| 18  | `DomainName`                    | String | 空 = CloudFront 既定ドメイン使用    | カスタムドメイン採用時のみ。[ADR-0007](../decisions/0007-acm-cert-issuance.md) 参照                                        | 任意           |
| 19  | `AcmCertificateArn`             | String | 空 = 既定証明書                     | カスタムドメイン採用時のみ。**us-east-1 必須**（CloudFront 仕様）。[ADR-0007](../decisions/0007-acm-cert-issuance.md)      | 任意           |
| 20  | `HostedZoneId`                  | String | 空 = ALIAS RecordSet 作成しない     | Route 53 ALIAS 自動作成時のみ。[ADR-0007](../decisions/0007-acm-cert-issuance.md) §DNS 切替                                | 任意           |
| 21  | `MaxConcurrentCalls`            | Number | `10`（1〜10、Connect 東京クォータ） | 要件 9.6                                                                                                                   | 任意           |
| 22  | `TranscribeLanguageCode`        | String | `ja-JP` 固定                        | 要件 6.2                                                                                                                   | 不可変         |
| 23  | `ManageConnectStorageConfig`    | String | `false`（true / false）             | true にする場合は **Connect インスタンスに既存の CALL_RECORDINGS Storage Config が無いこと** を確認。初期は `false` 推奨。 | 任意           |
| 24  | `ConnectRecordingsPrefix`       | String | `connect-raw/`                      | Connect 録音書込先 S3 prefix（trailing slash 必須）                                                                        | 任意           |

> 全 24 項目をカバーしているか、CFn 標準の `[{"ParameterKey":..., "ParameterValue":...}]` 配列形式かを `parameters/README.md` で確認すること。`_comment` キー等を混入させると AWS CLI のパースで弾かれる（[`parameters/README.md`](../../infrastructure/parameters/README.md) DRY 設計）。

### 4.2 stg.json / prod.json の TBD 置換

#### 4.2.1 PowerShell ワンライナーで置換（推奨）

```powershell
$env:PYTHONUTF8 = "1"
$path = 'infrastructure/parameters/stg.json'    # or prod.json
$content = Get-Content -Raw -Encoding UTF8 $path

# 1 つずつ置換（例）
$content = $content.Replace('TBD-STG-CONNECT-INSTANCE-ID',           '12345678-aaaa-bbbb-cccc-1234567890ab')
$content = $content.Replace('TBD-STG-OUTBOUND-PHONE-NUMBER-ID',      'abcdef01-2345-6789-abcd-ef0123456789')
$content = $content.Replace('TBD-STG-INBOUND-PHONE-NUMBER-ID',       'fedcba98-7654-3210-fedc-ba9876543210')
$content = $content.Replace('TBD-STG-OUTBOUND-CONTACT-FLOW-ID',      '11111111-2222-3333-4444-555555555555')
$content = $content.Replace('TBD-STG-INBOUND-CONTACT-FLOW-ID',       '66666666-7777-8888-9999-aaaaaaaaaaaa')

# OperatorEmail / ConnectOutboundPhoneNumber は ParameterValue を直接書き換え
$content = $content -replace '"ParameterValue":\s*"\+810000000000"', '"ParameterValue": "+819012345678"'
$content = $content -replace '"ParameterValue":\s*"stg-operator@example\.com"', '"ParameterValue": "sre-team-stg@your-domain.example"'

Set-Content -Path $path -Value $content -Encoding UTF8 -NoNewline
```

#### 4.2.2 エディタで直接編集

`stg.json` を開き、以下のキーの `ParameterValue` を実値に置換：

- `ConnectInstanceId`: `TBD-STG-CONNECT-INSTANCE-ID` → 実 UUID
- `ConnectInstanceArn`: `arn:...:instance/TBD-STG-CONNECT-INSTANCE-ID` → 実 ARN
- `ConnectOutboundPhoneNumberArn`: `arn:...:instance/.../phone-number/TBD-STG-OUTBOUND-PHONE-NUMBER-ID` → 実 ARN
- `ConnectOutboundPhoneNumber`: `+810000000000` → 実 E.164 番号
- `ConnectInboundPhoneNumberArn`: `arn:...:instance/.../phone-number/TBD-STG-INBOUND-PHONE-NUMBER-ID` → 実 ARN
- `OutboundContactFlowId`: `TBD-STG-OUTBOUND-CONTACT-FLOW-ID` → 実 UUID
- `InboundContactFlowId`: `TBD-STG-INBOUND-CONTACT-FLOW-ID` → 実 UUID
- `OperatorEmail`: `stg-operator@example.com` → 運用先メール

prod.json も同様。`TBD-PROD-...` を実値に置換する。

### 4.3 置換後の検証

```powershell
# TBD トークンが残っていないか確認
Select-String -Path 'infrastructure/parameters/stg.json'  -Pattern 'TBD-'
Select-String -Path 'infrastructure/parameters/prod.json' -Pattern 'TBD-'
```

期待値：**何も出力されない**こと。1 件でも残っていれば CFn デプロイ時に `Invalid parameter value` 等で失敗する可能性が高い。

```powershell
# JSON 構文チェック
python -c "import json; json.load(open('infrastructure/parameters/stg.json',encoding='utf-8'))"
python -c "import json; json.load(open('infrastructure/parameters/prod.json',encoding='utf-8'))"
```

---

## 5. デプロイ前検証

### 5.1 cfn-lint（必須）

```powershell
pwsh -NoProfile -File infrastructure/scripts/validate.ps1
```

```bash
bash infrastructure/scripts/validate.sh
```

期待出力（末尾）：

```
cfn-lint exit code: 0
=== validate.ps1 END (OK) ===
```

cfn-lint で警告が出る既知の項目は `infrastructure/.cfnlintrc` で `ignore_checks` 4 件として明示的に許容済み（Task 14.10、運用課題 #4）。

### 5.2 aws cloudformation validate-template（任意、S3 経由）

```powershell
pwsh -NoProfile -File infrastructure/scripts/validate.ps1 -ValidateOnAws
```

```bash
bash infrastructure/scripts/validate.sh --validate-on-aws
```

template.yaml > 51,200 bytes なので、AWS CLI v2 の `validate-template --template-body` は使えず、`s3 cp` 経由 `--template-url` で検証する。検証用 S3 オブジェクトは `s3://safety-confirmation-cfn-artifacts-{Account}-{Region}/validation/template-{timestamp}.yaml` に書かれる。

期待出力（要約）：

```
{
  "Parameters": [ ... 24 entries ... ],
  "Description": "Safety Confirmation System - ...",
  "Capabilities": ["CAPABILITY_NAMED_IAM"]
}
=== validate.ps1 END (OK) ===
```

---

## 6. デプロイ実行

### 6.1 DryRun（実 AWS API を呼ばずに発行コマンド確認）

```powershell
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg -DryRun
```

```bash
bash infrastructure/scripts/deploy.sh stg --dry-run
```

期待挙動：

- 入力ファイル存在チェック（`template.yaml` / `parameters/stg.json`）
- JSON 構文チェック・`EnvironmentName` 一致確認
- `[DryRun] would invoke: pwsh -NoProfile -File scripts/build_layer.ps1`
- `[DryRun] skipping aws cloudformation package`
- `[DryRun] skipping aws cloudformation deploy`
- `=== deploy.ps1 END (DryRun, OK) ===`

DryRun で `EnvironmentName mismatch` 等のエラーが出た場合は、`parameters/{env}.json` の `EnvironmentName` 値と引数 `-EnvironmentName` が一致しているか確認する。

### 6.2 ChangeSet レビュー（推奨、特に prod）

stg / prod では、まず `--no-execute-changeset` で ChangeSet を作成・レビューする。

```powershell
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg -NoExecuteChangeset
```

```bash
bash infrastructure/scripts/deploy.sh stg --no-execute-changeset
```

ChangeSet が作成される（実行はされない）。CloudFormation コンソールで `safety-confirmation-stg` スタック → 「変更セット」タブから差分を確認し、想定外の Replace / Delete が無いことをレビューする。

ChangeSet を **手動で実行する** 場合：

```powershell
aws cloudformation execute-change-set `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name safety-confirmation-stg `
  --change-set-name '<ChangeSet名（コンソールで確認）>'
```

ChangeSet を **破棄する** 場合：

```powershell
aws cloudformation delete-change-set `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name safety-confirmation-stg `
  --change-set-name '<ChangeSet名>'
```

### 6.3 本番デプロイ

```powershell
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg
```

```bash
bash infrastructure/scripts/deploy.sh stg
```

実行内容（`deploy.ps1` / `deploy.sh` 共通）：

1. **Step 1**：入力ファイル存在・JSON 構文・`EnvironmentName` 一致チェック
2. **Step 2**：`scripts/build_layer.ps1` 実行（`backend/shared/` を `infrastructure/build/layers/shared/python/shared/` にステージング）
3. **Step 3**：`aws cloudformation package --template-file template.yaml --s3-bucket safety-confirmation-cfn-artifacts-{Account}-ap-northeast-1 --output-template-file packaged-template.yaml`
4. **Step 4**：`aws cloudformation deploy --template-file packaged-template.yaml --stack-name safety-confirmation-{env} --parameter-overrides file://parameters/{env}.json --capabilities CAPABILITY_NAMED_IAM`

失敗時は `deploy.ps1` / `deploy.sh` 自身が `describe-stack-events` の直近 10 件を自動ダンプして失敗終了する。

### 6.4 進行状況のモニタリング

別ターミナルで以下を実行し、CFn の進行を確認する：

```powershell
$env:PYTHONUTF8 = "1"
# 直近 20 件のイベントを継続表示
while ($true) {
    aws cloudformation describe-stack-events `
        --profile AWS-security-check --region ap-northeast-1 `
        --stack-name safety-confirmation-stg `
        --max-items 20 `
        --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' `
        --output table
    Start-Sleep -Seconds 30
}
```

または CloudFormation コンソールで `safety-confirmation-stg` スタック → 「イベント」タブをポーリングする。

### 6.5 想定所要時間

| 項目                                       | 目安      | 備考                                                                      |
| ------------------------------------------ | --------- | ------------------------------------------------------------------------- |
| `build_layer.ps1`                          | 30 秒以内 | `backend/shared/*` を再帰コピー + `__pycache__` 削除                      |
| `aws cloudformation package`               | 1〜3 分   | Lambda コード ZIP 化と S3 アップロード（Lambda 19 個 + SharedLayer 1 個） |
| `aws cloudformation deploy`（初回 CREATE） | 15〜25 分 | CloudFront ディストリビューションの伝搬が支配的（通常 10〜15 分）         |
| `aws cloudformation deploy`（差分 UPDATE） | 3〜10 分  | 差分次第。CloudFront 設定変更を伴う場合は再度 10〜15 分                   |

CloudFront ディストリビューションの作成・更新は AWS 側の伝搬待ちが発生するため、進行が止まったように見えても 15 分程度は待つこと。`describe-stack-events` で「In Progress」のまま 30 分経過した場合は AWS Service Health Dashboard を確認する。

---

## 7. デプロイ後検証

> **[`runbook.md` §7.3「デプロイ後検証」](./runbook.md#73-デプロイ後検証) を必ず実施する。** 本書では初回 stg / prod デプロイで追加で行う検証を補足する。

### 7.1 必須チェック（runbook.md §7.3 から）

```powershell
$env:PYTHONUTF8 = "1"
$env_name = 'stg'   # or prod

# (a) アラーム 6 件が OK 状態
aws cloudwatch describe-alarms `
  --profile AWS-security-check --region ap-northeast-1 `
  --alarm-name-prefix safety-confirmation `
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

期待値：以下 6 件すべて `OK` または `INSUFFICIENT_DATA`（初期は INSUFFICIENT_DATA が正常）

| AlarmName                                            | Req  |
| ---------------------------------------------------- | ---- |
| `safety-confirmation-sla-warning-30min-{env}`        | 14.6 |
| `safety-confirmation-cycle-timeout-{env}`            | 14.5 |
| `safety-confirmation-lambda-errors-{env}`            | 運用 |
| `safety-confirmation-recording-upload-failure-{env}` | 10.9 |
| `safety-confirmation-transcribe-failed-{env}`        | 6.6  |
| `safety-confirmation-inbound-unauthorized-{env}`     | 警戒 |

詳細は [`monitoring.md`](./monitoring.md) を参照。

```powershell
# (b) Lambda 19 件揃っていること
aws lambda list-functions `
  --profile AWS-security-check --region ap-northeast-1 `
  --query "Functions[?starts_with(FunctionName,'safety-confirmation-')].FunctionName" `
  --output table
```

期待値：19 件（design.md「要件対応マッピング」記載の Lambda 群 — Auth Pre/Post、Employee/Cycle/Response/Recording/Dictionary API、LoadTargets / ConnectDispatcher / CallEndHandler / TranscribeStarter / KeywordMatcher / RetryEvaluator / CycleFinalizer / RecordingMetadataWriter / InboundHandler 等）

```powershell
# (c) Stack Outputs 確認
aws cloudformation describe-stacks `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name "safety-confirmation-$env_name" `
  --query 'Stacks[0].Outputs' --output table
```

期待値：`CognitoUserPoolId` / `CognitoUserPoolClientId` / `CloudFrontDomainName` / `ApiBaseUrl` / `RecordingsBucketName` / `TranscriptsBucketName` / `KmsCmkArn` / `StateMachineArn` / `OperatorTopicArn` の 9 件が揃う（design.md「Outputs」）。

### 7.2 SNS Subscription 確認（OperatorEmail）

CFn 初回作成後、`OperatorEmail` 宛に AWS から **`Subscription Confirmation`** メールが届く。受信メールの `ConfirmSubscription` リンクをクリックして購読を確定する。確認後、テストパブリッシュ：

```powershell
$topic = aws cloudformation describe-stacks `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name "safety-confirmation-$env_name" `
  --query 'Stacks[0].Outputs[?OutputKey==`OperatorTopicArn`].OutputValue' --output text

aws sns publish --profile AWS-security-check --region ap-northeast-1 `
  --topic-arn $topic `
  --subject "test: deploy verification" `
  --message "Subscription verification publish"
```

`OperatorEmail` 宛にテストメールが届けば SNS 配線 OK。

### 7.3 SPA 動作確認（dev / stg / prod 共通、初回のみ）

CFn は SPA バケット（`SpaBucket`）と CloudFront ディストリビューションを作成するが、**SPA ビルド成果物のアップロードは CFn 管理外**。Phase 11.2 で整備した手順に従う：

1. `frontend/` ディレクトリで `npm run build`（Vite 等のビルド）。
2. `dist/` を `aws s3 sync dist/ s3://{SpaBucketName}/ --profile AWS-security-check --delete`。
3. CloudFront キャッシュ無効化 `aws cloudfront create-invalidation --distribution-id {DistId} --paths "/*"`。

その後、`CloudFrontDomainName`（または `DomainName` 設定時はカスタムドメイン）にブラウザでアクセスし、Cognito ログイン画面が表示されることを確認する。

### 7.4 初期データ投入（Phase 15.2 と同手順）

> stg / prod も dev と同様、CFn は Cognito ユーザーや辞書初期データを作成しない。**手動投入が必須**。

1. **管理者ユーザー作成**（Cognito）

```powershell
$env:PYTHONUTF8 = "1"
$env_name = 'stg'

$userpool = aws cloudformation describe-stacks `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name "safety-confirmation-$env_name" `
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' --output text

aws cognito-idp admin-create-user `
  --profile AWS-security-check --region ap-northeast-1 `
  --user-pool-id $userpool `
  --username admin-stg `
  --user-attributes Name=email,Value="admin-stg@your-domain.example" Name=email_verified,Value=true `
  --temporary-password '<8 文字以上の暫定パスワード>' `
  --message-action SUPPRESS

aws cognito-idp admin-add-user-to-group `
  --profile AWS-security-check --region ap-northeast-1 `
  --user-pool-id $userpool `
  --username admin-stg `
  --group-name Administrator
```

初回ログイン時に SPA から強制パスワード変更を実施する。

2. **キーワード辞書の初期登録**（SPA 管理画面から、Phase 15.2 と同じ）
   - `SAFE`: `["無事", "大丈夫"]`
   - `INJURED`: `["怪我", "痛い"]`
   - `UNAVAILABLE`: `["動けない", "出社不可"]`

   等を SPA「辞書管理」画面から登録する。初回登録後、`KeywordDictionary-{env}` テーブルに `version` メタレコードが作成されることを `aws dynamodb get-item` で確認すると確実。

3. **動作確認**：SPA に管理者でログイン → サイクル管理画面が表示 → サイクル起動ボタンが操作可能であることを確認。

---

## 8. ロールバック手順

### 8.1 CFn 自動ロールバック（既定挙動）

`aws cloudformation deploy` は **既定で `OnFailure=ROLLBACK`** が有効。新規 CREATE 時にリソース作成途中で失敗すれば、それまで作成したリソースを自動削除して `ROLLBACK_COMPLETE` 状態に戻す。

UPDATE 時にリソース更新が失敗した場合は、変更前の状態にロールバックされ `UPDATE_ROLLBACK_COMPLETE` に到達する。

> ⚠️ ロールバック失敗時は **`UPDATE_ROLLBACK_FAILED`** 状態に陥り、スタックは「使用不能」になる。この場合は §8.4 を参照。

### 8.2 手動ロールバック：UPDATE 進行中のキャンセル

UPDATE 中に問題に気付いた場合、進行中の更新をキャンセル可能。

```powershell
aws cloudformation cancel-update-stack `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name safety-confirmation-stg
```

`UPDATE_ROLLBACK_IN_PROGRESS` → `UPDATE_ROLLBACK_COMPLETE` の遷移を `describe-stack-events` で確認する。

### 8.3 手動ロールバック：明示的に前バージョンへ戻す

CFn は「N 個前のバージョン」を直接指定する機能を持たない。前バージョンに戻すには：

1. Git で前バージョンの `template.yaml` / `parameters/{env}.json` をチェックアウト：

```powershell
git log --oneline infrastructure/template.yaml infrastructure/parameters/stg.json
git checkout '<previous-commit-sha>' -- infrastructure/template.yaml infrastructure/parameters/stg.json
```

2. 通常デプロイ手順（§6.3）を再実行：

```powershell
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg
```

3. 完了後、Git ワーキングツリーを最新に戻す（チェックアウトを取り消す）：

```powershell
git checkout HEAD -- infrastructure/template.yaml infrastructure/parameters/stg.json
```

### 8.4 ロールバック失敗時の `UPDATE_ROLLBACK_FAILED` リカバリ

スキップ可能リソースを指定して `continue-update-rollback` を実行する。

```powershell
# まず失敗しているリソースを特定
aws cloudformation describe-stack-events `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name safety-confirmation-stg `
  --query 'StackEvents[?ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' `
  --output table

# 該当リソースをスキップしてロールバック継続
aws cloudformation continue-update-rollback `
  --profile AWS-security-check --region ap-northeast-1 `
  --stack-name safety-confirmation-stg `
  --resources-to-skip <LogicalResourceId1> <LogicalResourceId2>
```

> スキップしたリソースは **スタック管理外（drift 状態）** になる。リカバリ後、当該リソースを手動で AWS コンソール / CLI で削除 or 整合させ、次回 deploy で再作成させる。

### 8.5 ロールバック不可な変更（**事前計画必須**）

以下の変更は **データロストを伴う** ため、CFn のロールバックでは復旧できない。**変更チケットに事前計画として書くこと**：

| 変更内容                                     | 影響                                                                      | 対策                                                   |
| -------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------ |
| DynamoDB テーブルの **論理 ID 変更 or 削除** | テーブル削除 → 全データ喪失（PITR 未取得期間分含む）                      | 事前に PITR Backup を確認し、Restore 計画を立てる      |
| S3 バケットの **論理 ID 変更 or 削除**       | バケット削除（空でないと CFn は失敗、ただし強制削除時はオブジェクト消失） | 事前にレプリケーションまたはバックアップを準備         |
| Cognito UserPool の **論理 ID 変更 or 削除** | 全ユーザー / グループ削除                                                 | ユーザー輸出 + 再登録手順を準備                        |
| KMS CMK の **削除**                          | 既存データの暗号化 / 復号不能（pending 7 日待機後完全削除）               | 事前に AliasName を再点検、削除時は pending 期間を活用 |

これらの破壊的変更を含む UPDATE は **必ず stg で先行検証** し、prod では §6.2 ChangeSet レビューで Replace 表示が無いことを確認してから実行する。

### 8.6 SPA ロールバック

SPA のバージョンロールバックは CFn 管理外（S3 オブジェクトの上書き）なので、`s3 sync` で前バージョンを再 upload + CloudFront Invalidation で対応する：

```powershell
# 過去ビルドの dist を保持していない場合は Git で当時のコミットからリビルド
git checkout '<previous-tag>'
cd frontend
npm ci
npm run build
aws s3 sync dist/ "s3://<SpaBucketName>/" --profile AWS-security-check --region ap-northeast-1 --delete
aws cloudfront create-invalidation --profile AWS-security-check `
  --distribution-id <DistId> --paths "/*"
```

---

## 9. 環境別の注意事項

### 9.1 dev

- 既存 `safety-confirmation-dev` スタックが UPDATE_COMPLETE 状態を維持中（Account 214046906694、Region ap-northeast-1）。
- `dev.json` の Connect 関連 ID は **ダミー UUID (`00000000-...`)** で許容（ADR-0005 実機検証保留中、template.yaml の AllowedPattern 制約をパスする最小値）。実機検証着手時に Step 2 で実値取得・置換する。
- `OperatorEmail` は `placeholder@example.com` のまま。実通知が必要な場合は dev でも実 SRE メールに置換する。

### 9.2 stg

- **本番相当の構成**：Connect インスタンス / 電話番号 / Contact Flow は **prod とは別 ID を用意** する（同一にすると prod データに誤接続するリスク）。
- `OperatorEmail` は SRE 個人 or stg 専用配信先メーリングリストを設定。**勿論 prod 運用配信先と混ぜない**。
- `LogRetentionDays` は dev と同じ `90` を推奨。
- ADR-0005 課金合意取得後にデプロイすること。

### 9.3 prod

- **変更チケット起票必須**：L4 経営層への事前通知、影響時間帯の調整、ロールバック計画の事前合意。
- **業務時間外推奨**：CloudFront 設定変更は 10〜15 分の伝搬待ちが発生。サイクル起動中の停止は §6.2 ChangeSet レビューで影響範囲を必ず確認。
- `LogRetentionDays` は `365` を推奨（運用ログ長期保存方針、暫定値）。
- `OperatorEmail` は運用配信先メーリングリスト（個人アドレスは避ける）。
- ADR-0005 課金合意取得後にデプロイすること。
- 通常運用 / 通常リリースは [`runbook.md`](./runbook.md) に従う。
- インシデント発生時は [`incident-response.md`](./incident-response.md) に遷移する。

### 9.4 共通注意事項

- **東京リージョン（ap-northeast-1）固定**：本テンプレートは ap-northeast-1 を前提とした構成（design.md「リージョン制約」、requirements.md AC 17.5 / NFR 18.5）。他リージョンへのデプロイは Out of Scope。
- **AWS アカウントは 1 つ**：dev / stg / prod は **同一アカウント内のスタック名で分離**する設計（運用課題、本プロジェクト個人学習スコープ）。マルチアカウントへの展開は Out of Scope。
- 東京リージョンの Amazon Connect 同時通話クォータは **10** が初期値。これを上限とした設計（`MaxConcurrentCalls` 既定 10、Requirement 9.6）。クォータ拡張申請は本書スコープ外。

---

## 10. トラブルシューティング

| 症状                                                                                                                   | 想定原因                                                                                              | 対処                                                                                                                                            |
| ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `S3 bucket safety-confirmation-cfn-artifacts-... already exists`                                                       | 別アカウントが同名バケットを保有                                                                      | バケット名は AWS グローバルで一意。`Account` 部分が正しいか、deploy スクリプト内の `$S3Bucket` を確認                                           |
| `Stack [safety-confirmation-stg] does not exist`                                                                       | 初回デプロイ時、または別アカウント / 別リージョンで実行中                                             | `aws cloudformation list-stacks --profile AWS-security-check --region ap-northeast-1` で対象スタックの存在を確認。Profile / Region 設定を再確認 |
| `AccessDenied: User is not authorized to perform: cloudformation:CreateStack`                                          | IAM Profile 権限不足                                                                                  | デプロイ実行者の IAM Role / User に CloudFormation / IAM / Lambda / S3 / DynamoDB / Cognito 等の管理権限が付与されているか確認                  |
| `Parameter validation failed: parameter value for parameter name ConnectInstanceId does not match the defined pattern` | template.yaml の AllowedPattern と `parameters/{env}.json` の値が不一致                               | UUID 形式 / E.164 形式 / ARN 形式が正しいかを確認。`TBD-...` プレースホルダの取り残しもこのエラーになる                                         |
| `Resource creation cancelled` の連鎖                                                                                   | 別リソースの作成失敗で連鎖キャンセル                                                                  | `describe-stack-events` で先頭の `CREATE_FAILED` イベントを探し、その `ResourceStatusReason` が根本原因                                         |
| CloudFormation deploy が 30 分以上「In Progress」                                                                      | CloudFront の伝搬待ち、または ACM 証明書検証待ち（カスタムドメイン使用時）                            | 通常 15 分以内。ACM 検証待ちなら Route 53 の CNAME レコードが正しく設定されているか確認（[ADR-0007](../decisions/0007-acm-cert-issuance.md)）   |
| `Cannot create a phone number storage configuration: instance already has one`                                         | `ManageConnectStorageConfig=true` で Connect インスタンスに既存 CALL_RECORDINGS Storage Config がある | `ManageConnectStorageConfig=false` に戻すか、Connect コンソールから既存 Storage Config を削除                                                   |
| `cfn-lint exit code: 4` (W2001/W3002/W3037/W8001 のいずれか)                                                           | `.cfnlintrc` の ignore_checks 4 件と異なる別チェックがヒット                                          | テンプレ修正 or `.cfnlintrc` 更新（要レビュー）。安易に ignore を増やさない                                                                     |
| デプロイ後に SNS 通知が届かない                                                                                        | OperatorEmail の Subscription Confirmation 未承認                                                     | 受信箱から `Subscription Confirmation` メールの ConfirmSubscription リンクをクリック（§7.2）                                                    |
| SPA にアクセス → CORS / 403                                                                                            | CloudFront キャッシュが古い、または SPA バケットのオブジェクトポリシーが OAC 経由のみを許可           | `aws cloudfront create-invalidation --paths "/*"`、`aws s3 sync` のやり直し                                                                     |

**運用後のインシデント対応**（DLQ 滞留 / 録音欠落 / Transcript 欠落 / SLA 違反 等）は [`incident-response.md`](./incident-response.md) を参照。

---

## 11. デプロイ完了チェックリスト

第三者が本書のみでデプロイを完了したと判断するための最終確認項目：

- [ ] Step 1：AWS CLI / Python / cfn-lint / PowerShell or bash がインストール済
- [ ] Step 1：Profile `AWS-security-check` で `aws sts get-caller-identity` が成功
- [ ] Step 2：Connect インスタンス / 電話番号 2 個 / Contact Flow 2 個を取得済、ID / ARN 7 件をメモ済
- [ ] Step 3：CFn アーティファクトバケット `safety-confirmation-cfn-artifacts-{Account}-ap-northeast-1` 作成済、SSE / Versioning / BPA 設定済
- [ ] Step 4：`parameters/{env}.json` の `TBD-...` 取り残しなし（`Select-String -Pattern 'TBD-'` で確認）
- [ ] Step 5：`validate.ps1` / `validate.sh` が exit 0
- [ ] Step 6：`deploy.ps1 -DryRun` が成功 → `deploy.ps1` 本実行で `CREATE_COMPLETE` または `UPDATE_COMPLETE` 到達
- [ ] Step 7：アラーム 6 件が OK or INSUFFICIENT_DATA、Lambda 19 件揃う、Outputs 9 件揃う、SNS Subscription Confirmation 完了、テスト publish 受信、SPA でログイン画面表示
- [ ] Step 7：管理者ユーザー作成済、SPA から辞書初期データ登録済、サイクル起動 UI が操作可能
- [ ] 完了後の通常運用は [`runbook.md`](./runbook.md)、インシデント対応は [`incident-response.md`](./incident-response.md)、アラーム詳細は [`monitoring.md`](./monitoring.md) に従う運用が確立

---

## 改訂履歴

| 日付       | 改訂内容                                                                                                                                                                                                                                                  | 起票者 |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 2026-06-28 | 初版作成（Task 15.3）。Requirement 17.5 / design.md「CloudFormation テンプレート設計 / Parameters」に整合。Connect 事前準備、parameters 全 24 項目転記、validate / deploy / DryRun / ChangeSet / ロールバック、環境別差分、トラブルシューティングを網羅。 | kiro   |
