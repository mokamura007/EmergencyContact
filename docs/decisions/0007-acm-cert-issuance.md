# ADR-0007: ACM 証明書の発行手順と CloudFormation Parameter 連携（Phase 11.1）

- 状態: Accepted
- 作成日: 2026-06-26（セッション 10、Phase 11.1 着手）
- 関連: Phase 11.1（tasks.md）、Phase 11.2 CloudFrontDistribution の `ViewerCertificate`、Requirements NFR3、Design / Architecture / Edge

---

## 1. 背景

`safety-confirmation` SPA をカスタムドメインで配信する場合、CloudFront に ACM 証明書を関連付ける必要がある。CloudFront は **`us-east-1` リージョン**の ACM 証明書のみを受け付けるという AWS 仕様上の制約があり、本システムの配信スタックが置かれる `ap-northeast-1` とはリージョンが異なる。

Phase 1.2 で `AcmCertificateArn`（任意、`Default: ""`）および `DomainName`（任意、`Default: ""`）の 2 つの Parameter を `infrastructure/template.yaml` に定義済。Phase 11.2 で CloudFront ディストリビューションの `ViewerCertificate` が `UseCustomCert` Condition で分岐し、Parameter 経由で ACM 証明書を参照する。

本 ADR は Phase 11.1 の Done When「ACM 証明書 ARN が Parameter で受領可能で、CloudFront から参照される」を運用観点で達成するため、ACM 証明書の発行手順 + CloudFormation Parameter への値注入手順を整備する。

## 2. 決定

カスタムドメイン配信時の ACM 証明書発行は以下の標準手順に従う。CloudFormation スタック更新は本 ADR の手順外で発行済の ARN を Parameter に注入することで完結させる（証明書発行プロセスを CloudFormation で自動化することはしない）。

### 2.1 us-east-1 ACM 証明書の発行手順（DNS 検証推奨）

1. 配信ドメイン名（例：`safety-confirmation.example.com`）を確定する
2. AWS CLI または AWS Console で **`us-east-1`** リージョンを選択（CloudFront 用必須、`ap-northeast-1` で発行した証明書は CloudFront に紐付けられない）
3. 証明書をリクエスト：
   ```powershell
   aws acm request-certificate `
     --region us-east-1 `
     --domain-name safety-confirmation.example.com `
     --validation-method DNS `
     --idempotency-token safety-confirmation-spa-2026
   ```
4. レスポンスの `CertificateArn` を控える（例：`arn:aws:acm:us-east-1:214046906694:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee`）
5. 検証用 CNAME レコードを取得：
   ```powershell
   aws acm describe-certificate `
     --region us-east-1 `
     --certificate-arn <上記 ARN> `
     --query "Certificate.DomainValidationOptions[0].ResourceRecord"
   ```
6. レスポンスに含まれる `Name` / `Value` / `Type: CNAME` を、Route 53 ホストゾーンの該当ドメイン配下に作成する（Phase 11.3 で同じホストゾーンに ALIAS レコードも作成されるため、同一ホストゾーンに DNS 検証用 CNAME を配置する想定）
7. ACM が DNS 検証を完了するまで待機（通常 5〜30 分、`describe-certificate` の `Certificate.Status` が `PENDING_VALIDATION` → `ISSUED` に遷移）
8. `Status: ISSUED` を確認したら、ARN を Parameter 値として CloudFormation スタックに注入する（次節 2.3）

### 2.2 Email 検証経路（代替案、運用上は非推奨）

Route 53 を使わない場合は `--validation-method EMAIL` で発行可能だが、`admin@`、`hostmaster@`、`postmaster@`、`webmaster@` 等の管理メール宛にだけ検証メールが送信されるため、メール受信体制が確立されていない組織では使えない。本システムは Phase 11.3 で Route 53 を Parameter `HostedZoneId` 経由で使う前提のため、DNS 検証を標準とする。

### 2.3 CloudFormation Parameter への値注入

発行された ARN を以下の 2 経路のいずれかで `aws cloudformation deploy` / `update-stack` に渡す：

#### 経路 A：CLI `--parameter-overrides` 直接指定

```powershell
aws cloudformation deploy `
  --region ap-northeast-1 `
  --stack-name safety-confirmation-dev `
  --template-file infrastructure/template.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    DomainName=safety-confirmation.example.com `
    AcmCertificateArn=arn:aws:acm:us-east-1:214046906694:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee `
    HostedZoneId=Z1234567890ABCDEF
```

#### 経路 B：Parameter ファイル経由（推奨、Phase 15 deploy script でも採用想定）

`infrastructure/params/dev.json` 等に値を保存し、`--parameter-overrides file://...` で読み込む。差分管理が git で完結する利点あり。

### 2.4 Pending Validation 状態のハンドリング

- ACM 証明書が `PENDING_VALIDATION` のまま CloudFormation スタックを更新しようとすると、CloudFront リソース作成時に `CNAMEAlreadyExists` ではなく `InvalidViewerCertificate`（`CertificateNotFound` 系）が返ることがある
- スタック更新前に必ず `describe-certificate --certificate-arn <ARN>` で `Status: ISSUED` を確認する
- 自動化スクリプト（Phase 15）では `aws acm wait certificate-validated --region us-east-1 --certificate-arn <ARN>` を CloudFormation deploy の前段に挟む

### 2.5 既定値での動作（Parameter 未指定経路）

`DomainName` と `AcmCertificateArn` の双方が空文字（既定値）の場合：

- Conditions `HasCustomDomain` = false、`UseCustomCert` = false
- CloudFront ディストリビューションは `CloudFrontDefaultCertificate: true` で AWS 既定証明書（`*.cloudfront.net` ドメイン）を使用
- Route 53 RecordSet は作成されない（Condition `HasCustomDomainAndHostedZone` = false で skip）
- カスタムドメインなしでも `https://<distribution-id>.cloudfront.net/` で SPA 配信が機能する

これにより、課金合意取得前 / カスタムドメイン未確定状態でも Phase 11 のスタック更新が完結する設計とした。

## 3. 代替案の検討

### 3.1 ACM 証明書の CloudFormation 自動発行（不採用）

- `AWS::CertificateManager::Certificate` + `ValidationMethod: DNS` でテンプレ内発行することは技術的に可能
- 不採用理由：
  - CloudFormation スタックは `ap-northeast-1` に存在し、ACM 証明書は `us-east-1` 必須のため、**StackSets またはネストスタックでクロスリージョン分離が必要**になり複雑化する
  - 本システムの規模では Phase 15 deploy script で one-off に発行 → ARN 控え → Parameter 注入のフローで十分

### 3.2 ワイルドカード証明書（運用判断、案件次第）

- `*.example.com` のワイルドカード証明書を発行すれば複数 SPA / 環境（dev/stg/prod）で 1 枚を共有可能
- ただし、`dev.safety-confirmation.example.com` のような **2 階層サブドメイン**を発行する場合は `*.safety-confirmation.example.com` のような階層付きワイルドカードが必要
- 本 ADR では運用判断（複数環境を 1 枚に集約 vs 環境別に発行）に踏み込まず、案件で必要になった時点で運用者判断とする

## 4. 影響

- Phase 11.2 CloudFront `SpaDistribution` の `ViewerCertificate` 実装が `!If [UseCustomCert, ...]` 分岐で本 ADR の手順と整合する
- Phase 11.3 Route 53 ALIAS の `HostedZoneId` Parameter が本 ADR で言及される
- Phase 15 deploy script で `aws acm wait certificate-validated` を deploy 前段に組込む TODO が追加される
- 実機検証（dev 環境での実 ACM 証明書発行）は **本 Phase では実施せず**、Phase 14 統合テストまたは Phase 15 デプロイ着手時に運用者判断で実施

## 5. 完了確認

- [x] `AcmCertificateArn` / `DomainName` Parameter が Phase 1.2 で定義済（実装：`infrastructure/template.yaml`）
- [x] `UseCustomCert` / `HasCustomDomain` Conditions が Phase 1.4 で定義済（実装：`infrastructure/template.yaml`）
- [x] Phase 11.2 CloudFront `SpaDistribution.ViewerCertificate` が本 ADR と整合する `!If` 分岐で実装される（Phase 11.2 タスクで同セッション実装）
- [x] 本 ADR で発行手順 / 検証経路 / Parameter 注入 / Pending 状態ハンドリング / 既定値経路を文書化
