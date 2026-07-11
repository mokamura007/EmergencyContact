# 安否確認システム 個人情報取扱運用ドキュメント

- **対象読者**: 法務部門 / 情報セキュリティ部門 / SRE / オペレーター（Cognito `Administrator` グループ所属）/ 開発者
- **関連要件**: Requirement 1（認証・認可）/ Requirement 2 / 3（社員マスタ）/ Requirement 6（録音）/ Requirement 10（録音 / Transcript 保管）/ Requirement 13（インバウンド）/ Requirement 15.1〜15.6（個人情報取扱）/ Requirement 16（ログ）/ NFR3（セキュリティ）
- **関連設計**: design.md「ネットワーク・セキュリティ境界」/「Property 2 / 20 / 21 / 22 / 23 / 24」/「Data Models D1〜D8」
- **関連運用ドキュメント**:
  - [`runbook.md`](./runbook.md)：通常運用 / SNS 通知一次対応
  - [`incident-response.md`](./incident-response.md)：インシデント対応 8 件（DLQ 滞留・録音欠落・Transcript 欠落・SLA 違反・辞書誤更新・Lambda 大量エラー・ロックアウト・インバウンド未登録番号大量着信）
  - [`monitoring.md`](./monitoring.md)：CloudWatch アラーム 6 件 + Metric Filter 1 件
  - [`deploy.md`](./deploy.md)：第三者デプロイ手順書（dev / stg / prod）
- **想定環境**: AWS リージョン `ap-northeast-1`、AWS CLI Profile `AWS-security-check`

> 本ドキュメントは個人情報保護法および社内規程への適合判断を法務 / 情報セキュリティ部門が行うための一次資料である。本書で言及するすべてのリソース名・暗号化方式・保管期間は `infrastructure/template.yaml` および `backend/shared/` 配下の実装を真として整理する。

---

## 1. 概要 / 想定読者

本書は安否確認システムが取り扱う個人情報の収集範囲・保管・暗号化・アクセス制御・削除依頼への対応・マスキング・監査ログ閲覧手順を一元的にまとめたものである。法務 / 情報セキュリティ部門は §11 のレビュー観点チェックリストを使って準拠性を判定し、SRE / オペレーターは日常運用および削除要請受領時の手順書として参照する。開発者は実装変更時に本書とのズレが発生していないかを §11 のチェックリストで自己点検する。

---

## 2. 個人情報の取扱範囲

### 2.1 収集する個人情報

| 区分                         | 項目                                                                   | 格納先                                 | 暗号化                            | 用途                                                                 |
| ---------------------------- | ---------------------------------------------------------------------- | -------------------------------------- | --------------------------------- | -------------------------------------------------------------------- |
| 社員情報（D1）               | 氏名（漢字 / かな）、電話番号（E.164）、社員番号（任意）、所属（任意） | DynamoDB `Employee-<env>`              | SSE-KMS                           | 安否確認サイクルの対象者選定、Inbound_Handler の発信者番号一致判定   |
| 管理者認証情報（Cognito）    | E-mail、Cognito sub、`Administrator` グループ所属                      | Cognito User Pool                      | AWS 管理                          | Admin Console へのログイン（Req 1）                                  |
| 認証失敗履歴（LockoutTable） | 失敗時刻配列 `failedAts`、`expireAt`                                   | DynamoDB `Lockout-<env>`               | SSE-KMS                           | アカウントロックアウト判定（5 回失敗 / 30 分、Req 1.6 / Property 8） |
| 通話録音（D5）               | 音声 WAV ファイル本体                                                  | S3 Recordings バケット                 | SSE-KMS                           | Transcribe 入力、Voice_Status 判定の根拠保管（Req 6 / 10）           |
| 通話録音メタ（D4）           | S3 オブジェクトキー、録音開始・終了時刻、通話時間                      | DynamoDB `RecordingMeta-<env>`         | SSE-KMS                           | 署名付き URL 発行、再生要求時の有効期限判定                          |
| 音声認識テキスト（D6）       | Transcript 本文、抜粋（先頭 100 文字）、信頼度                         | S3 Transcripts + DynamoDB              | SSE-KMS                           | Keyword_Matcher による Voice_Status 判定                             |
| インバウンド発信者番号（D8） | `callerNumber`（原本 E.164）、`callerNumberMasked`（マスク済）         | DynamoDB `InboundContact-<env>`        | SSE-KMS                           | 折り返し電話の社員特定、Cycle 紐付け                                 |
| 監査ログ（CloudWatch Logs）  | イベント種別、UTC timestamp、principal、target、outcome、`phoneMasked` | `/aws/safety-confirmation/audit-<env>` | KMS（CloudWatch Logs マネージド） | 操作監査（Req 15.5 / 16.3 / 16.4）                                   |

### 2.2 収集しない情報

以下は本システムでは収集・保管しない：

- 住所、生年月日、本籍地
- 社員番号以外の識別子（マイナンバー等）
- 生体情報（指紋、顔画像、声紋）
- 位置情報、IP アドレス（CloudFront / API Gateway のアクセスログは AWS が保持するが本システムは独自保存しない）
- SNS アカウント、外部連携 ID
- クレジットカード番号、銀行口座情報

### 2.3 取得経路

- **社員情報**：管理者が SPA から個別入力 または CSV 一括投入（`POST /employees/import`、Req 3）
- **管理者認証情報**：管理者が初回作成時、別管理者から Cognito 経由で招待を受ける
- **録音 / Transcript**：Amazon Connect 自動架電またはインバウンド着信時に自動取得
- **インバウンド発信者番号**：Amazon Connect が Caller ID から自動取得（社員本人の操作で発信された電話）

---

## 3. 保管・暗号化ポリシー（Req 15.1〜15.3 / NFR3）

### 3.1 KMS CMK

- **CMK エイリアス**：`alias/${EnvironmentName}-safety-confirmation`（CloudFormation `KmsCmkAlias`）
- **用途**：DynamoDB 全テーブル（SSE-KMS）、S3 録音 / Transcript バケット（SSE-KMS）、CloudWatch Logs（一部）の共用 CMK
- **キーポリシー**：暗号化対象サービス（`kms:ViaService`）と Lambda 実行ロールのみに `kms:Encrypt` / `kms:Decrypt` / `kms:GenerateDataKey` を限定する（design.md「ネットワーク・セキュリティ境界」）

### 3.2 DynamoDB

`infrastructure/template.yaml` で全テーブルに `SSESpecification: { SSEEnabled: true, SSEType: KMS, KMSMasterKeyId: !Ref KmsCmk }` を適用：

- `Employee-<env>`（D1）
- `Cycle-<env>`（D2）
- `Response-<env>`（D3）
- `RecordingMeta-<env>`（D4）
- `TranscriptMeta-<env>`（D6 メタ）
- `KeywordDictionary-<env>` / `KeywordDictionaryHistory-<env>`（D7）
- `InboundContact-<env>`（D8）
- `Lockout-<env>`（認証失敗履歴）

### 3.3 S3 バケット

- **`safety-confirmation-recordings-<env>-<account>-<region>`**：SSE-KMS、Block Public Access ON、ライフサイクル 90 日（後述 §4）、Versioning OFF
- **`safety-confirmation-transcripts-<env>-<account>-<region>`**：同上

バケットポリシーで `aws:PrincipalArn` が許可リスト外の場合はすべて Deny する（Req 10.6）。

### 3.4 Cognito

Cognito User Pool 内部のユーザー属性および認証情報は AWS マネージド暗号化により保護される。本システムは Cognito の標準暗号化に依存し、独自の暗号化レイヤは追加しない。

### 3.5 通信経路

- CloudFront / API Gateway / Cognito エンドポイントすべてで TLS 1.2 以上を強制する（Req 1.1, NFR3）
- API Gateway は Cognito User Pool Authorizer を使用し、JWT 内の `cognito:groups` で `Administrator` を必須とする
- VPC は使用しない構成のため、Lambda → DynamoDB / S3 / KMS の通信は AWS 内部 API（HTTPS）を経由する

---

## 4. 保管期間ポリシー（Req 6.5 / 10.4 / 13.5 / 16.5）

| 対象データ                     | 保管期間                                                               | 削除方式                                                                                                      | 根拠                                                                                           |
| ------------------------------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 録音ファイル本体（D5）         | **90 日固定**                                                          | S3 Lifecycle Rule `DeleteRecordingsAfter90Days`                                                               | Req 10.4、CFn Parameter `RecordingsRetentionDays`（AllowedValues = `[90]`、固定）、Property 23 |
| Transcript 本体（D6）          | **90 日固定**                                                          | S3 Lifecycle Rule `DeleteTranscriptsAfter90Days`                                                              | Req 6.5、CFn Parameter `TranscriptsRetentionDays`（AllowedValues = `[90]`、固定）、Property 23 |
| Recording_Metadata（D4）       | 録音本体と同期（90 日以降は参照不可と扱う）                            | DynamoDB レコード本体は無期限保持、署名付き URL 発行時に Cycle 起動から 90 日経過判定で 410 Gone              | Req 10.7、Property 23                                                                          |
| Transcript Metadata（D6 抜粋） | 録音本体と同期                                                         | 同上                                                                                                          | Req 6.5                                                                                        |
| Cycle / Response（D2 / D3）    | **無期限**（履歴保持）                                                 | 論理削除なし                                                                                                  | Req 4 / Req 7（履歴閲覧）                                                                      |
| Employee（D1）                 | 退職時点で論理削除（電話番号 NULL 化）/ レコード本体は無期限           | `deleted = true` + `phoneNumber = null`                                                                       | Req 15.3 / 15.4 / Property 20                                                                  |
| InboundContact（D8）           | **30 日（実質的な参照可能期間）** + レコード自体は無期限               | レコード本体は保持。Inbound 受付可否判定で完了 Cycle から 30 日経過時は新規 Inbound を `flow=NO_CYCLE` で切断 | Req 13.5 / 13.6                                                                                |
| 認証失敗履歴（LockoutTable）   | **30 分**（`expireAt` TTL）                                            | DynamoDB TTL による自動削除                                                                                   | Req 1.6 / Property 8                                                                           |
| CloudWatch Logs（監査含む）    | **既定 90 日**（CFn Parameter `LogRetentionDays`、1〜3653 日で変更可） | LogGroup 保持期間設定                                                                                         | Req 16.5                                                                                       |
| 監査ログ（AuditLogGroup）      | `LogRetentionDays` と同一（既定 90 日）                                | LogGroup 保持期間設定                                                                                         | Req 15.5 / 16.5                                                                                |

**重要**：録音 / Transcript の 90 日は **要件 10.4 / 6.5 により 90 日固定** とし、CFn Parameter の AllowedValues も `[90]` のみ受け付ける。短縮 / 延長は仕様変更扱い（要件改訂が必要）。

---

## 5. アクセス制御（Req 15.2 / NFR3 / Property 2）

### 5.1 SPA / API 経由のアクセス

- SPA は Cognito User Pool で認証された `Administrator` グループのユーザーのみ全画面にアクセス可能（Req 1.3 / 1.4 / 1.9）
- 一般社員ロールは本システムの管理画面上では存在しない。社員は電話発着信のみのインターフェースで本システムと接する
- API Gateway は Cognito User Pool Authorizer を経由し、JWT 内の `cognito:groups` に `Administrator` がないリクエストは 401 / 403 で拒否
- `/auth/record-failure` のみ Authorizer 不要（認証失敗報告用パブリックエンドポイント）

### 5.2 Lambda 実行ロール

- Lambda 関数ごとに専用 IAM Role を作成し、必要最小限の DynamoDB / S3 / KMS / Connect / Transcribe 権限のみを付与する
- ロール ARN を CMK キーポリシーの `Principal` に限定列挙する

### 5.3 開発者の AWS Console アクセス

- 開発者の AWS Console アクセスは IAM 最小権限ポリシーで管理する。本番（prod）環境では：
  - DynamoDB / S3 への直接読取権限は付与しない（必要時のみ time-bound で発行）
  - KMS CMK への `Decrypt` 権限は通常付与しない（DynamoDB / S3 経由のアプリケーション読取りでのみ復号される）
  - CloudWatch Logs の読取権限は SRE および監査担当者に限定する
- 開発（dev）/ ステージング（stg）環境では SRE / 開発者の参照権限を許容するが、本番（prod）環境では §11 のレビュー観点に従い、原則として開発者の prod Console アクセスを禁止する

### 5.4 監査ログのアクセス制御

- AuditLogGroup `/aws/safety-confirmation/audit-<env>` の `logs:GetLogEvents` / `logs:FilterLogEvents` / `logs:StartQuery` 権限は SRE / 監査担当者のみに付与する
- CloudWatch Logs Insights のクエリ実行履歴は CloudTrail に残るため、監査担当者の閲覧自体も追跡可能

---

## 6. 削除依頼への対応（Req 15.3 / 15.4 / NFR3 / Property 20）

### 6.1 退職者の通常削除フロー

> 対称的な **新規管理者登録** の日常運用手順は
> [`admin-user-management.md`](./admin-user-management.md) §2 を参照。
> 退職時 Cognito 削除（本節 step 6）と、新規登録時 Cognito 作成（
> [`admin-user-management.md`](./admin-user-management.md) §2.2）は、
> それぞれ独立した監査イベント `COGNITO_USER_DELETE` / `COGNITO_USER_CREATE`
> を AuditLogGroup に出力する（対称）。

1. 管理者が SPA「社員管理」画面で対象社員を選択し「削除」を押下
2. SPA は `DELETE /employees/{id}` を呼出
3. EmployeeApi Lambda が以下を実行：
   - `deleted = true` を書込
   - `phoneNumber = null` を書込（または論理削除フラグ付きで更新）
   - 監査ログ（`EMPLOYEE_DELETE`、`phoneMasked` 含む）を AuditLogGroup に出力
4. 完了は要求受信から **5 秒以内**（Req 15.3、Property 20）
5. 以降の Cycle で当該社員は対象者抽出から除外され、Inbound_Handler の発信者番号一致判定からも除外される（Req 15.4、Property 2 / Property 20）
6. **管理者ロール社員（`isAdmin=true`）の退職時のみ追加実施（Task 15.16）**：
   - SPA「社員管理」画面の「論理削除済社員も表示」トグルを ON にし、対象退職社員行に表示される「Cognito 削除」ボタンを押下
   - SPA は `DELETE /employees/{id}/cognito-user` を呼出（Administrator 限定）
   - EmployeeApi Lambda は次を実行：(1) 対象社員が論理削除済かつ `cognitoSub` 属性を持つことを検証（不在は 404、未削除は 409）、(2) `admin_delete_user(UserPoolId, Username=<cognitoSub>)` を実行、(3) DynamoDB `Employee-<env>` レコードから `cognitoSub` 属性を `REMOVE`、(4) 監査ログ `COGNITO_USER_DELETE` を AuditLogGroup に出力
   - **不可逆操作**：Cognito User Pool には Soft Delete 機能が無く、削除後に元に戻すには手動で `admin_create_user` を再実行する必要がある

### 6.2 削除後のデータ残存範囲

| 残存データ                       | 削除可否                                  | 説明                                                                                                                                                                                                                                                                                     |
| -------------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 氏名 / 社員番号 / 所属（D1）     | 残存                                      | 過去 Cycle / Response の参照整合性のため保持。論理削除フラグで運用上は不可視                                                                                                                                                                                                             |
| 電話番号（D1）                   | **NULL 化済**                             | 復元不可                                                                                                                                                                                                                                                                                 |
| Cognito アカウント               | 別途手動削除                              | 管理者ロールユーザーが退職する場合は Cognito User Pool から個別削除。**Task 15.16 で SPA「社員管理」画面の論理削除済社員一覧（「論理削除済社員も表示」トグル ON）から「Cognito 削除」ボタンで実行可能**。SPA から実行できない場合の代替手段として AWS Console / AWS CLI 経由でも実施可能 |
| 過去 Cycle / Response（D2 / D3） | 残存                                      | Voice_Status / Transcript 抜粋を含む。削除は §6.3 の特別フローを要する                                                                                                                                                                                                                   |
| 録音 / Transcript（D5 / D6）     | **最長 90 日で自動削除**                  | S3 Lifecycle により自動削除されるため、明示削除は通常不要                                                                                                                                                                                                                                |
| InboundContact（D8）             | 残存（30 日経過後は新規受付対象外となる） | レコード本体は保持される                                                                                                                                                                                                                                                                 |
| 監査ログ（AuditLogGroup）        | 残存（`LogRetentionDays` で自動削除）     | 既定 90 日。`phoneMasked` のためマスク済みで原本は含まれない                                                                                                                                                                                                                             |

### 6.3 個人からの削除要請（GDPR 等の本人請求相当）

社員本人または法定代理人から削除請求を受領した場合：

1. **法務確認**：請求の正当性、適用法令（個人情報保護法第 35 条、GDPR Right to Erasure 等）、削除対象範囲を法務部門が確認
2. **対象社員レコード論理削除**：§6.1 の手順を実行（5 秒以内）
3. **過去 Cycle Response の匿名化（Anonymization）**：
   - 対象社員 ID を不可逆ハッシュに置換する処理は **Task 15.12 で実装済**。Administrator が `POST /employees/{id}/anonymize` 管理 API を発行して実行する
   - 内部処理は `backend/shared/privacy/anonymize.py` の純粋関数 `anonymize_employee_id(employee_id, salt) -> "ANON_<32 hex chars>"`（SHA-256 + system-wide salt、不可逆）
   - 事前条件：対象社員レコードが論理削除済（§6.1 step 2 が先行していること）。未削除の場合は 409 を返却し anonymize を拒否
   - Salt は CFn Parameter `EmployeeAnonymizeSalt`（NoEcho=true）経由で `EMPLOYEE_ANONYMIZE_SALT` 環境変数に注入。空文字列の場合は 503 で fail-fast（誤 salt で匿名化すると過去レコードと逆引き不能になり復旧不可）
   - Salt のローテーションは過去匿名化レコードの逆引き不能化と等価（不可逆操作）。運用上 Salt は長期固定で扱う
   - 監査ログ `EMPLOYEE_ANONYMIZE` イベントを出力（principal / target=元 employee*id / outcome / responseCountUpdated / anonymizedIdPrefix="ANON*"）。**ハッシュ化後の ID は監査ログに含めない**（含めると監査ログ自体が逆引きチャネルとなり Property 21 / 不可逆性を毀損するため）
4. **録音 / Transcript の明示削除**：
   - 90 日経過前に削除が必要な場合、法務承認のうえ S3 オブジェクトを SRE が AWS Console / CLI で削除する
   - 具体的な削除手順（法務承認 → S3 オブジェクト特定 → `aws s3api delete-object` → DynamoDB メタの論理削除 → 監査ログ手動投入）は [`incident-response.md`](./incident-response.md) §9 を参照
   - 削除実行記録は §9.7 の方針（法務承認文書 + 実施記録を 7 年保管）に従う
5. **Cognito アカウント削除**：該当があれば **SPA「社員管理」画面の論理削除済社員一覧から「Cognito 削除」ボタンで実行**（Task 15.16）。SPA 経由が困難な場合は AWS Console / CLI で実施

### 6.4 削除依頼の監査ログ

すべての削除操作（社員レコード論理削除、録音明示削除、Cognito アカウント削除）は監査ログとして AuditLogGroup に残る。監査ログ自体は `LogRetentionDays` 期間内は保持されるため、削除要請の処理履歴は同期間内に追跡可能。

---

## 7. マスキング・匿名化（Property 21 / 22）

### 7.1 電話番号マスキング仕様（Property 22）

`backend/shared/audit/mask.py` の `mask_phone(s: str) -> str` は以下を満たす（Property 22）：

- 先頭 1 文字（`+`）と末尾 4 桁は保持
- 中間文字はすべて `*` に置換
- 出力長 = 入力長
- E.164 本体（`+` 以降）が 4 桁以下の場合は変更なしで返却（短い番号はマスク不可）

例：

| 入力            | 出力                                                                          |
| --------------- | ----------------------------------------------------------------------------- |
| `+819012345678` | `+********5678`                                                               |
| `+8190****1234` | `+**********1234`（中間の `*` も `*` のまま保持されるが文字列としてマスク済） |
| `+12345`        | `+*2345`                                                                      |
| `+1234`         | `+1234`（本体 4 桁、短すぎてマスクしない）                                    |
| `+1`            | `+1`                                                                          |

非 E.164 文字列（先頭が `+` でない）に対しても best-effort で「末尾 4 文字以外を `*` に置換」する経路があり、テストで保証されている。

### 7.2 マスキング適用箇所

| 適用箇所                                 | 実装                                                                                      | 表示形式        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------- | --------------- |
| 監査ログの `phoneMasked` フィールド      | `shared/audit/logger.py` `write_audit_log` が `mask_phone` を内部呼出（Property 21）      | `+********5678` |
| InboundContact `callerNumberMasked` 属性 | `lambdas/inbound_handler/handler.py` で書込時に `mask_phone` を適用                       | `+********5678` |
| SPA 表示（電話番号一覧 / 詳細）          | SPA 実装責務（管理者ロールでも常時マスク表記、原本展開はホバー / クリック操作で行う設計） | `+********5678` |
| 通話 / 録音時の Connect への入力         | マスクなし（実通話に必要）                                                                | E.164 原本      |

### 7.3 匿名化（Anonymization）の現状

過去 Cycle Response の社員 ID 匿名化（不可逆ハッシュ化）は **Task 15.12 で実装済**。SHA-256 + system-wide salt（CFn Parameter `EmployeeAnonymizeSalt`、NoEcho）を用いた一方向ハッシュで、本人請求受領時に Administrator が `POST /employees/{id}/anonymize` API 経由で実行する。

- 純粋関数：`backend/shared/privacy/anonymize.py` の `anonymize_employee_id(employee_id: str, salt: str) -> str`（戻り値は `"ANON_" + <32 hex chars>` 形式）
- 管理 API：`POST /employees/{id}/anonymize`（Administrator 限定）
- 前提：対象社員レコードが論理削除済（`deleted=true`）であること。未削除の場合は 409 で拒否
- Salt 未設定（空文字列）の場合は 503 で fail-fast
- 監査ログ `EMPLOYEE_ANONYMIZE` を出力（`target` は元 employee_id、ハッシュ済 ID は記録しない）
- Salt ローテーションは過去匿名化レコードの逆引き不能化と等価（不可逆操作）

---

## 8. 監査ログ閲覧手順（Req 16.3 / NFR3）

### 8.1 AuditLogGroup の場所

- LogGroup 名：`/aws/safety-confirmation/audit-<env>`
- 保持期間：CFn Parameter `LogRetentionDays`（既定 90 日、1〜3653 日で変更可、Req 16.5）
- 集約元 Lambda：`auth-pre-auth` / `auth-post-auth` / `auth-pre-signup` / `auth-failure-reporter` / `dictionary-api` / `employee-api` / `cycle-api` / `inbound-handler`（Phase 12.3 で 6 Lambda + 補助 2 Lambda の Role に `AuditLogWriteManagedPolicy` を付与済）

### 8.2 監査ログのフィールド（Property 21）

JSON 1 行 = 1 イベントで以下の必須 5 フィールドを保持：

| フィールド  | 内容                                                                                                                                                                                                                          |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `event`     | イベント種別（例：`EMPLOYEE_ADD` / `EMPLOYEE_DELETE` / `EMPLOYEE_ANONYMIZE` / `COGNITO_USER_CREATE` / `COGNITO_USER_DELETE` / `DICTIONARY_UPDATE` / `AUTH_SUCCESS` / `AUTH_FAILURE` / `CYCLE_START` / `INBOUND_RECEIVED` 等） |
| `timestamp` | UTC ISO 8601 形式（末尾 `Z`、ミリ秒精度）                                                                                                                                                                                     |
| `principal` | 実行者（Cognito sub。未認証は `<anonymous>`、Amazon Connect Contact Flow 呼出は `<connect-service>`）                                                                                                                         |
| `target`    | 対象識別子（社員 ID / `category#keyword` / Cycle ID / Contact ID 等）                                                                                                                                                         |
| `outcome`   | 結果（`SUCCESS` / `REJECTED` / `RECORDED` / `FAILED` 等の短い識別子）                                                                                                                                                         |

条件付きフィールド：

- `phoneMasked`：電話番号を含むイベントに限り、`mask_phone` 適用後の値が格納される（生の電話番号は他のいかなるフィールドにも露出しない）

### 8.3 代表的なクエリ例

#### 社員追加 / 削除イベントの抽出

```sql
fields @timestamp, event, principal, target, outcome, phoneMasked
| filter event in ["EMPLOYEE_ADD", "EMPLOYEE_DELETE", "EMPLOYEE_UPDATE"]
| sort @timestamp desc
| limit 100
```

#### 認証成功 / 失敗の抽出

```sql
fields @timestamp, event, principal, outcome
| filter event in ["AUTH_SUCCESS", "AUTH_FAILURE"]
| sort @timestamp desc
| limit 200
```

#### キーワード辞書更新の抽出

```sql
fields @timestamp, event, principal, target, outcome
| filter event in ["DICTIONARY_ADD", "DICTIONARY_DELETE", "DICTIONARY_UPDATE"]
| sort @timestamp desc
| limit 100
```

#### インバウンド受信の抽出

```sql
fields @timestamp, event, principal, target, outcome, phoneMasked
| filter event = "INBOUND_RECEIVED"
| sort @timestamp desc
| limit 100
```

#### 特定社員に関する全イベントの追跡（社員 ID で絞り込み）

```sql
fields @timestamp, event, principal, target, outcome, phoneMasked
| filter target = "<employeeId>"
| sort @timestamp desc
| limit 100
```

### 8.4 監査担当者への権限付与

> **Task 15.15 で CFn 管理化済**：監査担当者向け読取権限は CFn リソース
> `AuditReaderManagedPolicy` (`AWS::IAM::ManagedPolicy`) として
> `infrastructure/template.yaml` で管理されており、ManagedPolicy ARN は
> `Outputs.AuditReaderManagedPolicyArn` 経由で参照可能です。CFn 管理対象は
> ManagedPolicy のみで、監査担当者用 IAM User / Role / Group は本 Stack
> では作成しません（IAM ライフサイクルを Cognito User 管理から切り離す
> A 採用方針）。運用配信時は ARN を控えた上で AWS Console / CLI から
> 監査担当者の IAM プリンシパルへ手動アタッチしてください。

監査担当者（情報セキュリティ部門等）に AuditLogGroup の読取権限を付与する手順：

1. CFn デプロイ後、Outputs から ManagedPolicy ARN を取得：

   ```pwsh
   aws cloudformation describe-stacks `
     --stack-name <stack-name> `
     --query "Stacks[0].Outputs[?OutputKey=='AuditReaderManagedPolicyArn'].OutputValue" `
     --output text
   ```

2. 取得した ARN を監査担当者の IAM User / Role / Group にアタッチ：

   ```pwsh
   # IAM User の場合
   aws iam attach-user-policy --user-name <auditor-user> --policy-arn <arn>
   # IAM Group の場合
   aws iam attach-group-policy --group-name <auditor-group> --policy-arn <arn>
   # IAM Role の場合
   aws iam attach-role-policy --role-name <auditor-role> --policy-arn <arn>
   ```

3. 監査担当者の閲覧操作自体も CloudTrail に記録されるため、二重監査が可能

#### 参考：`AuditReaderManagedPolicy` が付与する Action と Resource

CFn で実装されている内容（手動で同等ポリシーを作成する場合の参照、template.yaml と二重保守しないこと）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAuditLogRead",
      "Effect": "Allow",
      "Action": [
        "logs:GetLogEvents",
        "logs:FilterLogEvents",
        "logs:StartQuery",
        "logs:StopQuery",
        "logs:GetQueryResults",
        "logs:DescribeLogStreams",
        "logs:DescribeLogGroups"
      ],
      "Resource": [
        "arn:aws:logs:ap-northeast-1:<account>:log-group:/aws/safety-confirmation/audit-<env>:*"
      ]
    }
  ]
}
```

Resource は `!GetAtt AuditLogGroup.Arn` で AuditLogGroup ARN に限定され、
ワイルドカードは含まない（他 LogGroup への横展開不可、最小権限原則）。

---

## 9. インバウンド発信者番号の特別取扱（Req 13.7 / NFR3）

### 9.1 着信時の処理フロー

1. 社員（または未登録番号からの発信者）が本システムのインバウンド代表電話番号に発信
2. Amazon Connect が Caller ID（E.164）を取得し、Inbound Contact Flow が `InboundHandler` Lambda を Invoke
3. `InboundHandler` は以下を実施：
   - 原本番号 `callerNumber` を取得
   - `mask_phone(callerNumber)` でマスク済み `callerNumberMasked` を生成
   - 両者を `InboundContact-<env>` テーブルに書込（KMS 暗号化、Req 13.7）
   - 監査ログ（`INBOUND_RECEIVED`、`phoneMasked` フィールドあり）を AuditLogGroup に出力
4. Employee_Master の `PhoneNumberIndex` GSI で発信者番号と一致する社員レコードを検索し、対応 Cycle に紐付ける（Req 13.4）

### 9.2 SPA 表示ポリシー

- SPA のインバウンド受信履歴一覧画面では **常にマスク済の `callerNumberMasked`** を表示する
- 原本番号 `callerNumber` の参照は管理者ロールのみ可能だが、SPA 上ではホバー / クリック操作で展開する設計（実装は SPA 側責務、Phase 10）
- SPA から API 経由で原本取得する場合も、API Gateway は Cognito Authorizer で `Administrator` グループを必須とする

### 9.3 監査ログへの記録方針

- 監査ログにはマスク済の `phoneMasked` フィールドのみ記録する
- 原本の `callerNumber` は監査ログには **絶対に出力しない**（Property 21 の reserved keys 衝突保護および raw 数字列の不在保証により担保）

---

## 10. データ流出時のインシデント対応

### 10.1 検知契機

- AWS GuardDuty / Macie のアラート（本システムでは **未導入**。[`docs/decisions/0008-guardduty-macie-evaluation.md`](../decisions/0008-guardduty-macie-evaluation.md) で **Deferred** 判断、再評価期日あり：stg / prod 本番稼働開始後 6 ヶ月以内 / 社員数 1,000 名超 / Cycle 件数 月 50 件超への拡張時 / 重大インシデント発生時のいずれか早い方）
- 監査ログの異常パターン（大量の `EMPLOYEE_LIST` 呼出、未認証アクセスの増加等）
- 外部からの通報（社員 / 第三者）
- CloudTrail の異常 API 呼出パターン

### 10.2 初動対応（受領後 1 時間以内）

1. **隔離**：影響を受けた IAM User / Role の権限を一時停止（IAM Policy `Deny *`）
2. **法務 / 情報セキュリティ部門への通知**：別途確認
   - 通知テンプレート例：

     ```
     【緊急】安否確認システム 個人情報流出疑い

     検知日時：YYYY-MM-DD HH:MM (JST)
     検知契機：（GuardDuty / 監査ログ / 外部通報 / その他）
     影響範囲（暫定）：社員数 N 名、データ種別（電話番号 / 録音 / Transcript）
     対応状況：影響範囲特定中、対象 IAM User の権限を一時停止済
     担当 SRE：（氏名）
     ```

3. **影響範囲特定**：[`incident-response.md`](./incident-response.md) §8 のクエリ例（別タスクで追記予定）および §8.3 の Insights クエリで影響範囲を確認
4. **証跡保全**：CloudTrail / AuditLogGroup のスナップショットを別 S3 バケットにエクスポートして法務 / 情報セキュリティ部門の調査に供する

### 10.3 影響範囲特定用クエリ

#### CloudTrail で異常 API 呼出を検索

```sql
fields eventTime, eventName, userIdentity.principalId, sourceIPAddress, errorCode
| filter eventSource = "dynamodb.amazonaws.com" or eventSource = "s3.amazonaws.com" or eventSource = "kms.amazonaws.com"
| filter eventName in ["GetItem", "Query", "Scan", "GetObject", "Decrypt"]
| filter userIdentity.principalId != "<expected-role-arn>"
| sort eventTime desc
| limit 200
```

#### AuditLogGroup で特定期間の全アクセスを抽出

```sql
fields @timestamp, event, principal, target, outcome
| filter @timestamp >= "<incident-start>" and @timestamp <= "<incident-end>"
| stats count() by principal, event
| sort count() desc
```

### 10.4 ステークホルダー連絡

- 法務部門：影響範囲確定後、個人情報保護委員会への報告要否判断
- 情報セキュリティ部門：再発防止策の策定
- 影響を受けた社員本人：法務承認後、個別通知（電話 / メール）

---

## 11. 法務 / 情報セキュリティ部門レビュー観点チェックリスト

本書を法務 / 情報セキュリティ部門に渡す際は、以下のチェックリストを併用する。各項目は本書の対応章節を参照することで確認できる。

### 11.1 法令準拠

- [ ] 個人情報保護法（日本）への適合
  - [ ] 利用目的の明示：本書 §2.3 取得経路に記載
  - [ ] 利用目的の範囲内利用：§2.1 用途列に記載
  - [ ] 第三者提供の不存在：本システムは外部第三者へ個人情報を提供しない（明記要）
- [ ] GDPR 等の海外法令適用判断（社員に EU 在住者が含まれるか）：法務確認事項
- [ ] 改正個人情報保護法の漏えい等報告義務（個情委規則）の対応プロセス：§10 に記載

### 11.2 収集範囲

- [ ] 最小収集原則の遵守：§2.1 と §2.2 で収集 / 非収集を明示
- [ ] 機微情報（要配慮個人情報）の不取得：本システムは生体情報・思想信条等を取得しない
- [ ] 取得方法の正当性：§2.3

### 11.3 保管期間

- [ ] 録音 / Transcript 90 日固定の根拠の妥当性：Req 10.4 / 6.5、業務上の再聴取必要性とのバランス
- [ ] InboundContact 30 日の受付期間：Req 13.5 / 13.6
- [ ] 認証失敗履歴 30 分 TTL：ロックアウト目的に必要十分か
- [ ] 監査ログ 90 日既定：内部監査 / 訴訟対応のための妥当性

### 11.4 暗号化

- [ ] DynamoDB 全テーブル SSE-KMS 適用：§3.2
- [ ] S3 録音 / Transcript SSE-KMS 適用：§3.3
- [ ] KMS CMK のキーポリシーの最小権限性：§3.1
- [ ] TLS 1.2 以上強制：§3.5

### 11.5 アクセス制御

- [ ] Administrator グループのみ全機能アクセス可：§5.1
- [ ] Lambda 実行ロールの関数別最小権限：§5.2
- [ ] 開発者の本番（prod）Console アクセス制限：§5.3
- [ ] 監査ログ閲覧権限の SRE / 監査担当限定：§5.4
- [ ] KMS Decrypt 権限の通常非付与（prod）：§5.3

### 11.6 削除依頼への対応

- [ ] 退職時の論理削除 + 電話番号 NULL 化が 5 秒以内に完了：§6.1、Property 20
- [ ] 削除後の対象者抽出 / Inbound 一致判定からの除外：§6.1、Property 2
- [ ] 個人からの削除請求への対応プロセス：§6.3（法務確認 → 論理削除 → 監査ログ残存）
- [ ] 過去 Cycle Response の匿名化は **実装済（Task 15.12）**。`POST /employees/{id}/anonymize` API で実行可能：§6.3 / §7.3
- [ ] **Cognito アカウント削除の SPA 動線は実装済（Task 15.16）**。`DELETE /employees/{id}/cognito-user` API + SPA「社員管理」画面の論理削除済社員一覧からボタン操作で実行可能。Administrator 限定 + 論理削除済 + `cognitoSub` 存在を順序立てて検証し、監査ログ `COGNITO_USER_DELETE` を出力：§6.1 step 6 / §6.2 / §6.3 step 5
- [ ] 録音 / Transcript の明示削除手順の妥当性：§6.3 / [`incident-response.md`](./incident-response.md) §9

### 11.7 マスキング・匿名化

- [ ] 電話番号マスキング仕様の妥当性（Property 22 準拠）：§7.1
- [ ] マスキングの適用箇所網羅（監査ログ / InboundContact / SPA 表示）：§7.2
- [ ] 監査ログに生の電話番号が露出しないことの保証（Property 21）：§8.2 / §7.2
- [ ] 匿名化（不可逆ハッシュ化）の現状ギャップの明示：§7.3（Task 15.12 で実装済）

### 11.8 監査ログ

- [ ] 監査対象イベントの網羅性：Property 21 が監査必要 7 種を保証
- [ ] 監査ログのフィールド完全性（5 必須 + `phoneMasked`）：§8.2
- [ ] 監査担当者への権限付与手順の明確性：§8.4
- [ ] 監査担当者の閲覧操作自体が CloudTrail に残ること：§5.4

### 11.9 インバウンド発信者番号

- [ ] 原本の KMS 暗号化保管：§9.1 / §3.2
- [ ] マスキング表示の徹底（SPA / 監査ログ）：§9.2 / §9.3
- [ ] 原本の監査ログ非露出：§9.3 / Property 21

### 11.10 データ流出時の対応

- [ ] 初動対応プロセスの明確性（隔離・通知・影響範囲特定）：§10
- [ ] 通知テンプレートの整備：§10.2
- [ ] 影響範囲特定用クエリの実用性：§10.3
- [ ] 個人情報保護委員会への報告判断プロセス：§11.1

### 11.11 開発・運用変更時の点検

- [ ] CFn Parameter `RecordingsRetentionDays` / `TranscriptsRetentionDays` の AllowedValues が `[90]` に固定されていることのテンプレート確認
- [ ] 新規 Lambda 追加時の AuditLogGroup 書込ポリシー付与忘れがないこと
- [ ] 新規 DynamoDB テーブル追加時の SSE-KMS 設定漏れがないこと
- [ ] mask_phone を介さない電話番号ログ出力経路の不存在（コードレビュー時の点検項目）

---

## 12. 改訂履歴

| 日付       | 改訂者 | 内容                                                                                                                                                                                                                 |
| ---------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-06-28 | Kiro   | 初版作成。Phase 15.5 個人情報取扱の運用整備（Req 15.1〜15.6 / NFR3 / Property 21 / 22 / 23 整合）                                                                                                                    |
| 2026-06-29 | Kiro   | Task 15.16：Cognito アカウント削除の SPA 動線実装に伴い §6.1 step 6 / §6.2 / §6.3 step 5 / §8.2 / §11.6 を更新。`DELETE /employees/{id}/cognito-user` API および「Cognito 削除」ボタン経由で実行可能となった旨を反映 |

---

> 本書のレビュー観点（§11）は法務 / 情報セキュリティ部門の判定資料として使用する。レビュー通過判定は Phase 15.6 受入テストの一部として、または個別タスクとして実施する。本書の更新は実装変更（Lambda 関数追加、DynamoDB テーブル追加、新規個人情報項目追加、保管期間変更）と同期して行うこと。
