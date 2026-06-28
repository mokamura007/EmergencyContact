# Parameters

`infrastructure/parameters/{dev,stg,prod}.json` は `infrastructure/scripts/deploy.ps1` から `aws cloudformation deploy --parameter-overrides file://...` 経由で `template.yaml` の `Parameters` セクションに投入される CFn Parameters 値ファイル群。本 README は全 Parameter の一覧 + 3 環境差分 + 投入時のチェックリストをまとめる（Phase 17.5、2026-06-28 セッション 22）。

- 対象 Parameter：**26 件**（うち 22 件は環境間で共通、4 件で環境差分あり、1 件は dev のみ）
- 関連ファイル：
  - `infrastructure/template.yaml`（Parameters 定義 + AllowedValues / MinValue / MaxValue / Default）
  - `infrastructure/scripts/deploy.ps1`（parameters/{env}.json 投入処理）
- 関連 ADR：[ADR-0009](../../docs/decisions/0009-connect-realworld-validation.md)（Connect 系 Arn の投入手順）、[ADR-0010](../../docs/decisions/0010-mock-on-aws-dev.md)（MockMode の dev 限定方針）

---

## 1. Parameter 一覧（カテゴリ別）

### 1.1 環境選択（1 件）

| Parameter         | Type   | AllowedValues | Default | dev   | stg   | prod   | 説明                                                                                                                       |
| ----------------- | ------ | ------------- | ------- | ----- | ----- | ------ | -------------------------------------------------------------------------------------------------------------------------- |
| `EnvironmentName` | String | dev/stg/prod  | -       | `dev` | `stg` | `prod` | 環境名。Stack 名 `safety-confirmation-${EnvironmentName}` / Lambda 関数名サフィックス / DDB テーブル名サフィックス等で参照 |

### 1.2 外部 Amazon Connect 参照（6 件、CFn 管理外）

ADR-0009 §3.1〜§3.6 のユーザー手動作業で取得する識別子。**実 Connect インスタンス購入前は placeholder 値**で deploy する（MockMode=true なら実 Connect 呼出は行われないため動作可能）。

| Parameter                       | Type   | dev              | stg             | prod            | 説明                                                                   |
| ------------------------------- | ------ | ---------------- | --------------- | --------------- | ---------------------------------------------------------------------- |
| `ConnectInstanceId`             | String | placeholder UUID | TBD-STG-...     | TBD-PROD-...    | Connect インスタンス ID                                                |
| `ConnectInstanceArn`            | String | placeholder ARN  | TBD-STG-...     | TBD-PROD-...    | Connect インスタンス ARN（IAM Resource 限定用）                        |
| `ConnectOutboundPhoneNumberArn` | String | placeholder ARN  | TBD-STG-...     | TBD-PROD-...    | アウトバウンド発信元電話番号 ARN                                       |
| `ConnectOutboundPhoneNumber`    | String | `+810000000000`  | `+810000000000` | `+810000000000` | アウトバウンド発信元電話番号（E.164 形式、`SourcePhoneNumber` に使用） |
| `ConnectInboundPhoneNumberArn`  | String | placeholder ARN  | TBD-STG-...     | TBD-PROD-...    | インバウンド代表電話番号 ARN                                           |
| `OutboundContactFlowId`         | String | placeholder UUID | TBD-STG-...     | TBD-PROD-...    | アウトバウンド Contact Flow ID                                         |
| `InboundContactFlowId`          | String | placeholder UUID | TBD-STG-...     | TBD-PROD-...    | インバウンド Contact Flow ID                                           |

### 1.3 Cycle 既定値（2 件）

| Parameter                     | Type   | Min | Max | Default | dev | stg | prod | 説明                                                    |
| ----------------------------- | ------ | --- | --- | ------- | --- | --- | ---- | ------------------------------------------------------- |
| `DefaultRetryCount`           | Number | 0   | 5   | 3       | 3   | 3   | 3    | Cycle 起動時の Retry_Count 既定値（Requirement 4.6）    |
| `DefaultRetryIntervalMinutes` | Number | 1   | 60  | 5       | 5   | 5   | 5    | Cycle 起動時の Retry_Interval 既定値（Requirement 4.7） |

### 1.4 TTS ガイダンス（2 件）

| Parameter              | Type   | dev                      | stg  | prod | 説明                                                  |
| ---------------------- | ------ | ------------------------ | ---- | ---- | ----------------------------------------------------- |
| `OutboundGuidanceText` | String | 日本語ガイダンス（共通） | 同じ | 同じ | Polly TTS のアウトバウンド再生本文（Requirement 5.x） |
| `InboundGuidanceText`  | String | 日本語ガイダンス（共通） | 同じ | 同じ | Polly TTS のインバウンド再生本文（Requirement 13.x）  |

### 1.5 ログ / 保管期間（3 件）

| Parameter                  | Type   | AllowedValues                                                       | Default | dev | stg | prod    | 説明                                                       |
| -------------------------- | ------ | ------------------------------------------------------------------- | ------- | --- | --- | ------- | ---------------------------------------------------------- |
| `LogRetentionDays`         | Number | CloudWatch Logs 標準値群（1/3/5/7/14/30/60/90/120/150/180/365/...） | 90      | 90  | 90  | **365** | CloudWatch LogGroup の保持期間。prod のみ 1 年（監査要件） |
| `RecordingsRetentionDays`  | Number | [90]                                                                | 90      | 90  | 90  | 90      | 録音ファイル保管期間（要件 10.4 により 90 日固定）         |
| `TranscriptsRetentionDays` | Number | [90]                                                                | 90      | 90  | 90  | 90      | Transcript 保管期間（要件 6.5 により 90 日固定）           |

### 1.6 運用者通知（1 件）

| Parameter       | Type   | dev                       | stg                        | prod                        | 説明                                                                                    |
| --------------- | ------ | ------------------------- | -------------------------- | --------------------------- | --------------------------------------------------------------------------------------- |
| `OperatorEmail` | String | `placeholder@example.com` | `stg-operator@example.com` | `prod-operator@example.com` | SNS 通知先 Email（CycleFinalizer COMPLETED / TIMEOUT 等で発信、要 ConfirmSubscription） |

### 1.7 インバウンド受付（1 件）

| Parameter                    | Type   | Min | Max | Default | dev | stg | prod | 説明                                                                  |
| ---------------------------- | ------ | --- | --- | ------- | --- | --- | ---- | --------------------------------------------------------------------- |
| `InboundReceptionWindowDays` | Number | 1   | 90  | 30      | 30  | 30  | 30   | Cycle 完了後にインバウンド受付を継続する日数（Requirement 13.5/13.6） |

### 1.8 カスタムドメイン（3 件、任意）

| Parameter           | Type   | Default | dev  | stg  | prod | 説明                                                            |
| ------------------- | ------ | ------- | ---- | ---- | ---- | --------------------------------------------------------------- |
| `DomainName`        | String | `""`    | `""` | `""` | `""` | CloudFront カスタムドメイン名（空時は CloudFront 既定ドメイン） |
| `AcmCertificateArn` | String | `""`    | `""` | `""` | `""` | ACM 証明書 ARN（us-east-1 リージョン、CloudFront 用）           |
| `HostedZoneId`      | String | `""`    | `""` | `""` | `""` | Route 53 Hosted Zone ID（任意）                                 |

### 1.9 並行制御（1 件）

| Parameter            | Type   | Min | Max | Default | dev | stg | prod | 説明                                                                                     |
| -------------------- | ------ | --- | --- | ------- | --- | --- | ---- | ---------------------------------------------------------------------------------------- |
| `MaxConcurrentCalls` | Number | 1   | 10  | 10      | 10  | 10  | 10   | SFN Map state の MaxConcurrency（Connect Tokyo region quota = 10 上限、Requirement 9.6） |

### 1.10 Transcribe 言語（1 件）

| Parameter                | Type   | AllowedValues | Default | dev   | stg   | prod  | 説明                                                             |
| ------------------------ | ------ | ------------- | ------- | ----- | ----- | ----- | ---------------------------------------------------------------- |
| `TranscribeLanguageCode` | String | [ja-JP]       | ja-JP   | ja-JP | ja-JP | ja-JP | Amazon Transcribe 言語コード（Requirement 6.2、現在 ja-JP 固定） |

### 1.11 Connect Storage Config 管理（2 件）

| Parameter                    | Type   | AllowedValues  | Default        | dev            | stg            | prod           | 説明                                                                                                                         |
| ---------------------------- | ------ | -------------- | -------------- | -------------- | -------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `ManageConnectStorageConfig` | String | [true / false] | false          | false          | false          | false          | CFn が `AWS::Connect::InstanceStorageConfig` を作成するか（既存インスタンスの CALL_RECORDINGS 設定衝突を防ぐため既定 false） |
| `ConnectRecordingsPrefix`    | String | -              | `connect-raw/` | `connect-raw/` | `connect-raw/` | `connect-raw/` | Connect 録音の S3 投入 prefix（RecordingRelocator が `recordings/` へ rename）                                               |

### 1.12 EmployeeAnonymizeSalt（Phase 17.7 で Secrets Manager 移行済、parameters/{env}.json には存在しない）

**Phase 17.7（2026-06-28 セッション 23）で本 Parameter は CFn から削除され、AWS Secrets Manager 経由の Dynamic Reference に移行**されました。`parameters/{dev,stg,prod}.json` には EmployeeAnonymizeSalt キーは含まれません。

| 項目                      | 内容                                                                                                                                                                                                                               |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 取得元                    | AWS Secrets Manager `safety-confirmation/{env}/employee-anonymize-salt`（SecretString = `{"salt": "<32 文字>"}`）                                                                                                                  |
| Lambda 注入経路           | `template.yaml` の `EmployeeApiFn.Environment.Variables.EMPLOYEE_ANONYMIZE_SALT` に Dynamic Reference 直書き：`!Sub "{{resolve:secretsmanager:safety-confirmation/${EnvironmentName}/employee-anonymize-salt:SecretString:salt}}"` |
| CFn deploy 時の挙動       | CFn が Secrets Manager から secret 値を resolved して Lambda Environment Variables に plain text で注入                                                                                                                            |
| Lambda 実行ロール権限     | `secretsmanager:GetSecretValue` 権限は **不要**（CFn が resolved するので Lambda は Secrets Manager API を呼ばない）                                                                                                               |
| CFn deploy 実行ロール権限 | `secretsmanager:GetSecretValue` 権限が必要（AWS-security-check Profile に既存付与済）                                                                                                                                              |
| dev 環境 secret           | **作成済**（Phase 17.7、2026-06-28 セッション 22 で作成、月額 ~60 円継続課金）                                                                                                                                                     |
| stg / prod 環境 secret    | **未作成**（実 deploy 前にユーザー作成必要、§3.2 参照）                                                                                                                                                                            |

### 1.13 MockMode（1 件、dev 限定）

| Parameter  | Type   | AllowedValues  | Default | dev      | stg   | prod  | 説明                                                                                                                                                                                                            |
| ---------- | ------ | -------------- | ------- | -------- | ----- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MockMode` | String | [true / false] | false   | **true** | false | false | ConnectDispatcher / TranscribeStarter mock 経路の有効化。**ADR-0010 §3.4 / §6.3.3 で prod 強制 false**。`Rules.ProdMockModeForbidden` で `EnvironmentName=prod` かつ `MockMode=true` の組合せを deploy 時に拒否 |

---

## 2. 3 環境差分サマリ

実質差分は以下のみ（21 件は共通値、Phase 17.7 で EmployeeAnonymizeSalt は Secrets Manager 移行済のため parameters/{env}.json には存在しない、計 25 entries）：

| Parameter                   | dev              | stg          | prod          | 差分理由                                                          |
| --------------------------- | ---------------- | ------------ | ------------- | ----------------------------------------------------------------- |
| `EnvironmentName`           | dev              | stg          | prod          | 環境名                                                            |
| `ConnectInstanceId` 系 6 件 | placeholder UUID | TBD-STG-...  | TBD-PROD-...  | Connect インスタンスは環境別取得（ADR-0009 §3.1）                 |
| `LogRetentionDays`          | 90               | 90           | 365           | prod のみ監査要件で 1 年保持                                      |
| `OperatorEmail`             | placeholder      | stg-operator | prod-operator | 環境別運用者                                                      |
| `MockMode`                  | **true**         | false        | false         | ADR-0010 dev 限定 mock 経路                                       |
| ~~`EmployeeAnonymizeSalt`~~ | ~~実値 32 文字~~ | ~~未設定~~   | ~~未設定~~    | **Phase 17.7 で削除済、Secrets Manager 経由に移行**（§1.12 参照） |

---

## 3. CFn deploy 前チェックリスト

dev/stg/prod 各環境への CFn deploy 前に以下を確認：

### 3.1 共通チェック

- [ ] parameters/{env}.json の 26 Parameter 全件揃っているか（不足するとデフォルトが適用されるが、CFn warning または `Required` Property 不足エラー）
- [ ] EnvironmentName が ファイル名 / Stack 名と整合しているか（deploy.ps1 が事故防止チェック実施）
- [ ] OperatorEmail が実在の運用者アドレスか（deploy 後に ConfirmSubscription メールが送信される）

### 3.2 stg / prod 追加チェック

- [ ] **AWS Secrets Manager `safety-confirmation/{env}/employee-anonymize-salt` を deploy 前に作成したか**（Phase 17.7 で Secrets Manager 移行済、`parameters/{env}.json` には EmployeeAnonymizeSalt キーは含まれない）
  - 作成手順（stg / prod のみ、dev は作成済）：
    ```bash
    # 32 文字のランダム salt 生成
    SALT=$(openssl rand -base64 24 | tr -d '+/=' | head -c 32)
    # AWS Secrets Manager に作成
    aws secretsmanager create-secret \
      --name safety-confirmation/stg/employee-anonymize-salt \
      --description "Employee anonymize salt for safety-confirmation stg environment" \
      --secret-string "{\"salt\":\"$SALT\"}" \
      --profile AWS-security-check \
      --region ap-northeast-1
    ```
  - **secret 値の不変性**：一度作成した salt 値は変更すると過去の匿名化 ID が解決不能になる（hashes from the old salt cannot be re-derived from the new salt）。secret 作成時に生成した値はパスワードマネージャ等で確実に保管すること
  - 月額課金：Secrets Manager 1 secret × ~0.40 USD/月 ≒ 60 円/月（環境ごと、ADR-0009 課金合意の都度判断方針）
- [ ] `MockMode` が `false` か（true で deploy しようとすると `Rules.ProdMockModeForbidden` が prod では deploy 拒否、stg では拒否されないため要手動確認）

### 3.3 prod 追加チェック

- [ ] `MockMode=false` か（`Rules.ProdMockModeForbidden` で CFn 側でも拒否、二重防御の確認）
- [ ] `LogRetentionDays=365` か（監査要件）
- [ ] Connect Arn 系 6 件が実値 ARN か（placeholder のままだと Lambda 起動時に Connect API 呼出が失敗）

### 3.4 ACM 証明書を使う場合の追加チェック

- [ ] `DomainName` / `AcmCertificateArn` / `HostedZoneId` が揃っているか（3 つは not-empty で同時に揃える必要、CFn Conditions で個別チェック）
- [ ] AcmCertificateArn が **us-east-1 リージョンの ACM** であることを確認（CloudFront 制約）

---

## 4. deploy フロー

```powershell
# dev 環境への deploy（mock 経路含む、Phase 16.5 検証済）
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev

# stg 環境への deploy（事前に safety-confirmation/stg/employee-anonymize-salt 作成必須）
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg

# prod 環境への deploy
pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName prod
```

deploy.ps1 のオプション：

- `-DryRun`：AWS API 呼出なしで発行コマンドのみ表示
- `-NoExecuteChangeset`：changeset 作成のみで実行しない
- `-SkipBuildLayer`：SharedLayer ステージング省略（差分のみの再 deploy 時）

詳細は `infrastructure/scripts/deploy.ps1` のヘッダ docstring 参照。

---

## 5. 副次発見メモ（後日改善候補）

- ~~**EmployeeAnonymizeSalt の secret 管理（Phase 17.7 で対応中、未完）**~~ → **Phase 17.7 完了**（2026-06-28 セッション 23）：CFn Parameter 削除 + Lambda Environment Variables 内 Dynamic Reference 直書き経由で AWS Secrets Manager から取得する設計に移行済。dev redeploy で Lambda env への注入を確認済（`Dp7SOwyczfKuhGmbCrsTYaWkILEXRqxZ`）。詳細は §1.12 参照
- **TBD- placeholder の整地**：stg/prod の Connect 系 Arn が `TBD-STG-...` `TBD-PROD-...` のまま git 管理されている。実 Connect インスタンス購入後（ADR-0009 §3.1 完了後）に実 ARN に置換する運用フローを明文化する候補
- **parameters/{env}.json の lint**：jsonschema 等で 25 Parameter の必須性 / 型を deploy 前に検証する CI ステップ追加候補（現状は deploy.ps1 のシンプルな JSON 構文チェックのみ）
