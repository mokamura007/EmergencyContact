# ADR-0008: AWS GuardDuty / Macie 導入の評価（Phase 15.14）

- ステータス: **Deferred**（再評価期日あり、本文 §6 を参照）
- 決定日: 2026-06-28
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md`（Req 15.5 / NFR3）、`.../design.md`、`.../tasks.md`（Phase 15.5 / 15.14）
- 関連 ADR: `docs/decisions/0001-runtime-selection.md`、`docs/decisions/0003-kms-cmk-staged-rollout.md`、`docs/decisions/0005-connect-mock-findings.md`、`docs/decisions/0007-acm-cert-issuance.md`
- 関連運用ドキュメント: `docs/operations/privacy.md`（§10「データ流出時のインシデント対応」）、`docs/operations/runbook.md`、`docs/operations/incident-response.md`、`docs/operations/monitoring.md`

---

## 1. コンテキスト

`docs/operations/privacy.md` §10.1「データ流出時のインシデント対応 — 検知契機」は、本システムの個人情報流出検知契機として以下 4 件を列挙している：

1. **AWS GuardDuty / Macie のアラート（本システムでは未導入。検討事項）**
2. 監査ログの異常パターン（大量の `EMPLOYEE_LIST` 呼出、未認証アクセスの増加等）
3. 外部からの通報（社員 / 第三者）
4. CloudTrail の異常 API 呼出パターン

このうち (1) は「未導入」と明記されており、Phase 15.5（個人情報取扱運用整備）の副次発見として「データ流出自動検知の観点で導入を検討すべき項目」として残された。本 ADR はこの検討事項を **検討フェーズで意思決定** し、Accepted（実装別タスク起票）/ Rejected（永久不採用）/ Deferred（再評価期日設定）のいずれかを文書化することを目的とする。

### 1.1 本システムの脅威モデルと現状の検知層

| 検知層                        | 対象                                                                          | 実装状況                                             | 限界                                                 |
| ----------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------- |
| (A) 監査ログ（AuditLogGroup） | EMPLOYEE_ADD / DELETE、DICTIONARY_UPDATE、AUTH_FAILURE 等 7 種                | **実装済**（Phase 12.3、`shared/audit/logger.py`）   | 監査対象イベントの発生時のみ記録、未認証経路は限定的 |
| (B) DLQ                       | Lambda 失敗、SQS / EventBridge 配信失敗                                       | **実装済**（Phase 9 / 14 各種 DLQ）                  | エラー検知のみ。正常な不正アクセスは検知不可         |
| (C) CloudWatch Alarm          | 異常系メトリクス 6 件 + Metric Filter 1 件                                    | **実装済**（Phase 14、`monitoring.md`）              | 数値閾値ベース、ふるまい検知ではない                 |
| (D) CloudTrail                | 全 AWS API 呼出履歴                                                           | **AWS 既定で取得済**（90 日保持、Insights クエリ可） | 異常パターン検出は手動クエリ依存、自動アラートはなし |
| (E) **GuardDuty**             | 異常 API 呼出 / Reconnaissance / 暗号通貨マイニング / DNS 異常 / 認証情報漏洩 | **未導入**（本 ADR の対象）                          | —                                                    |
| (F) **Macie**                 | S3 オブジェクト内の機密情報自動検知                                           | **未導入**（本 ADR の対象）                          | —                                                    |

このことからこう考えます：(A)〜(D) の組合せで「ログ層・エラー層・閾値層・履歴層」の 4 軸は部分カバー済だが、「**ふるまい異常の自動検知**」(E) と「**S3 オブジェクト内容のスキャン**」(F) は欠落している。これが本 ADR が解こうとしている検知ギャップである。

### 1.2 本プロジェクトの制約

- **個人学習スコープ**：本プロジェクトは AWS 資格対策および学習目的で構築されており、商用運用前提ではない（[`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md) §1）
- **課金合意保留中**：ADR-0005 で Amazon Connect の実機検証も課金合意未取得のため代替案で代行している。本 ADR の対象も同様の課金感度を持つ
- **検知対象が小規模**：DynamoDB 9 テーブル + S3 3 バケット（録音 / Transcript / SPA）、Lambda 約 15 関数、Cycle 件数は学習用途で月数件規模

---

## 2. 決定

| 項目                     | 値                                                                                                                                  |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| ステータス               | **Deferred**                                                                                                                        |
| 再評価期日               | **stg / prod 本番稼働開始後 6 ヶ月以内**、または **社員数 1,000 名超 / Cycle 件数 月 50 件超への拡張時**、いずれか早い方            |
| 再評価時の予定アクション | 評価軸 (a)(b)(c) を本番運用実績データで再算定し、Accepted / Rejected を別 ADR で確定                                                |
| 現状の代替策             | (A) 監査ログ + (B) DLQ + (C) CloudWatch Alarm 6 件 + Metric Filter 1 件 + (D) CloudTrail Insights クエリ手動運用（runbook.md 経由） |
| 採用しなかった選択肢     | Accepted（実装別タスク起票）、Rejected（永久不採用）                                                                                |

### 2.1 Deferred を採用した根拠

1. **本プロジェクトのスコープが個人学習・課金感度が高い**：GuardDuty / Macie はいずれも **常時稼働型** の課金モデル（イベント量 / オブジェクト量に応じた従量課金）であり、商用運用前の本フェーズで有効化すると検知対象がほぼ無い状態で月額課金だけが発生する
2. **ADR-0005 課金合意取得と独立だが整合**：ADR-0005 で Amazon Connect 実機検証も課金合意取得後に保留されており、課金保留中の他項目と整合させる方が運用判断が一貫する
3. **検知効果が現状の代替層で部分カバー済**：(A) 監査ログで個人情報操作 7 種、(C) CloudWatch Alarm 6 件で運用上の異常、(D) CloudTrail Insights で API 異常パターン手動検出と、検知すべき主要シナリオは現状の組合せで一定範囲はカバーされている
4. **本番運用フェーズで再評価する方が合理的**：脅威モデルは社員数・Cycle 件数・データ保管量の実績に応じて変化するため、それらの実績データが蓄積されない段階で課金を発生させる意義が薄い
5. **Rejected を選ばない理由**：本番運用フェーズで脅威モデルが変化した場合に再検討する余地を残す必要がある。Rejected は再評価不要を意味するため不適切
6. **Accepted を選ばない理由**：実装別タスク起票後の課金発生が現フェーズでは正当化できない。検知対象がほぼ無い状態での課金は ROI が悪い

---

## 3. 評価軸 3 項目

### 3.1 (a) 課金影響

#### 3.1.1 GuardDuty 東京リージョン課金構造

GuardDuty 東京リージョンの主な課金対象は以下のとおり（AWS 公式料金体系、目安）：

| データソース                    | 課金単位                 | 本プロジェクトでの想定                                                                                                                                                                       |
| ------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CloudTrail Management Events    | 100 万イベントあたり料金 | 本プロジェクトは Lambda / DynamoDB / S3 / KMS の自動操作が多く、Cycle 起動・録音アップロード・Transcribe 起動で **1 Cycle あたり数十〜数百イベント** 発生する想定。月 Cycle 数件規模では低額 |
| VPC Flow Logs                   | 1 GB あたり料金          | **本システム VPC 不使用**（[design.md ネットワーク・セキュリティ境界] 参照）のため課金対象外                                                                                                 |
| DNS Logs                        | 1 GB あたり料金          | Lambda → AWS API（DynamoDB / S3 / KMS）の HTTPS 呼出で DNS 問い合わせは発生するが、量は限定的                                                                                                |
| S3 Data Events（オプション）    | 100 万イベントあたり料金 | 録音 / Transcript S3 バケットへの PutObject / GetObject で発生、90 日 LCM で常時数千オブジェクト程度なら低〜中程度                                                                           |
| Kubernetes / Runtime Monitoring | EKS / EC2 用             | 本プロジェクト不使用、課金対象外                                                                                                                                                             |

#### 3.1.2 Macie 課金構造

Macie の主な課金対象（AWS 公式料金体系、目安）：

| 課金対象                     | 課金単位                         | 本プロジェクトでの想定                                                                                                |
| ---------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| S3 バケットメタデータ評価    | バケット数 × 月額                | 録音 / Transcript / SPA 計 3 バケット、低額                                                                           |
| **機密データ自動検知ジョブ** | 1 GB あたり料金 + オブジェクト数 | 録音 WAV / Transcript JSON は 90 日 LCM で常時数千オブジェクト程度。**ジョブ実行で月額数千円規模** に達する可能性あり |

#### 3.1.3 課金影響の総合評価

このことからこう考えます：本プロジェクト規模では GuardDuty は **月数百円程度の低額**で済む可能性が高い一方、Macie は **機密データ自動検知ジョブの実行頻度次第で月額数千円規模** に達し得る。商用運用前の本フェーズでは Macie のコスト感が特に正当化しにくい。

> **注意**：具体的な月額試算は AWS 公式料金ページの最新情報および本プロジェクトの実稼働量に応じて再算定が必要。本 ADR では「課金感度の方向性」のみを示し、Deferred 期間中の本番運用実績で再算定する方針とする。

### 3.2 (b) 検知効果

#### 3.2.1 GuardDuty の検知範囲と本システム脅威モデルの一致度

GuardDuty が検知する主要 Finding カテゴリと本システムでの一致度：

| GuardDuty Finding カテゴリ | 検知内容例                               | 本システムでの一致度                                            |
| -------------------------- | ---------------------------------------- | --------------------------------------------------------------- |
| Reconnaissance             | ポートスキャン、API 列挙                 | 中（管理者 IAM User の漏洩時に有効）                            |
| UnauthorizedAccess         | 異常な API 呼出元、認証情報の不正使用    | **高**（電話番号・録音・Transcript への不正アクセス検知に直結） |
| CredentialAccess           | 認証情報スキャン、IAM 認証情報の異常使用 | **高**（Cognito sub・IAM User Access Key の漏洩検知）           |
| Impact                     | データ破壊、リソース消費異常             | 中（DynamoDB 全件削除等）                                       |
| CryptoCurrency             | 暗号通貨マイニング                       | 低（Lambda のみで EC2 / EKS 不使用、攻撃面が狭い）              |
| Backdoor / Trojan          | C2 通信、マルウェア通信                  | 低（VPC 不使用、Lambda は AWS API しか叩かない）                |
| Discovery / Penetration    | S3 / DynamoDB の探索的 API 呼出          | **高**（個人情報を保持するため重要）                            |

このことからこう考えます：GuardDuty の検知範囲のうち **UnauthorizedAccess / CredentialAccess / Discovery** が本システムの脅威モデルと高度に一致する。一方 CryptoCurrency / Backdoor 系は VPC 不使用・Lambda のみの構成で攻撃面が狭く、一致度は低い。

#### 3.2.2 Macie の機密データ検知と本システム S3 内容の一致度

Macie がデフォルトで検知する機密データ種別と本システム S3 オブジェクトとの一致度：

| Macie 検知種別                            | 本システム S3 オブジェクト内容                   | 一致度                                               |
| ----------------------------------------- | ------------------------------------------------ | ---------------------------------------------------- |
| クレジットカード番号                      | 録音 WAV / Transcript JSON                       | 低（業務上クレジットカード番号は登場しない）         |
| 個人識別情報（PII：氏名・住所・電話番号） | Transcript JSON に氏名・電話番号が含まれる可能性 | **中〜高**（社員氏名・安否確認応答に含まれる可能性） |
| 認証情報（AWS アクセスキー等）            | 本来含まれない                                   | 低                                                   |
| 銀行口座番号 / SSN 等                     | 業務上登場しない                                 | 低                                                   |
| 健康情報                                  | 災害時の体調報告で部分的に含まれる可能性         | 中                                                   |

このことからこう考えます：Macie の検知種別のうち **PII（氏名・電話番号）** が Transcript JSON に含まれる可能性があり一致度は中〜高。ただし、現状 §7「マスキング・匿名化」で電話番号は監査ログでマスク済、Transcript 本体は SSE-KMS + S3 Block Public Access + バケットポリシー Deny で保護されており、**Macie が検知すべき「設定ミスで漏洩可能性のある状態」が現状発生しにくい**設計になっている。

#### 3.2.3 既存検知層との重複度

| 検知シナリオ                                  | 既存層でのカバー                        | GuardDuty 追加効果       | Macie 追加効果 |
| --------------------------------------------- | --------------------------------------- | ------------------------ | -------------- |
| 大量の DynamoDB Scan 実行（個人情報漏洩試行） | (D) CloudTrail Insights クエリ（手動）  | **高**（自動アラート化） | 低             |
| S3 オブジェクト大量 Get（録音持ち出し試行）   | (D) CloudTrail Insights クエリ（手動）  | **高**                   | 低             |
| 設定ミスで S3 バケットが Public 化            | (C) CloudWatch Alarm 未網羅             | 中                       | **高**         |
| Transcript に予期せぬ PII が含まれる          | §7 マスキング適用範囲外で部分的にカバー | 低                       | **高**         |
| 漏洩した IAM Access Key の悪用                | (A) AUTH_FAILURE 監査 + (D) CloudTrail  | **高**                   | 低             |
| Lambda 実行ロールの異常な振舞                 | (B) DLQ + (C) Alarm（一部）             | 中                       | 低             |

このことからこう考えます：GuardDuty は (D) CloudTrail Insights の手動運用を自動化する効果が大きく、Macie は S3 設定ミス検知で独自価値がある。ただし両者とも **本プロジェクトの現状の保護設計（SSE-KMS + Block Public Access + バケットポリシー Deny + 監査ログ）が機能している前提では検知すべきイベントが希少** であり、Deferred 期間中は現状層で運用継続可能と判断する。

### 3.3 (c) 運用負荷

#### 3.3.1 False Positive 対応

| 項目                             | GuardDuty                                                                                             | Macie                                                        |
| -------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Finding 発生頻度（学習スコープ） | 月数件〜数十件想定（管理者 IAM User の AWS Console アクセスで Reconnaissance 系が誤検知される可能性） | 月数件（バケットポリシー変更時にメタデータ評価が更新される） |
| 1 件あたりの調査時間目安         | 15〜60 分（Finding 詳細確認 + CloudTrail 突合）                                                       | 10〜30 分（検知された機密データの確認 + マスキング状況確認） |
| Severity 設定の調整負荷          | 中（High / Medium / Low の閾値カスタマイズ要）                                                        | 低（既定検知種別で十分）                                     |
| 自動抑制（Suppression）の設定    | 既知の管理者 IP からの Finding を抑制する設定が必要                                                   | バケット単位の自動ジョブ除外設定が可能                       |

#### 3.3.2 ロール権限拡張

GuardDuty / Macie を導入する場合、追加で必要となる IAM 権限：

- SRE / 監査担当者ロール：`guardduty:ListFindings`, `guardduty:GetFindings`, `guardduty:UpdateFindingsFeedback`, `macie2:ListFindings`, `macie2:GetFindings`
- アーカイブ管理：`s3:PutObject` 権限を Findings エクスポート用 S3 バケットに対して付与
- SNS 通知連携：既存の SNS Topic（`OperationalAlertsTopic` 等）に GuardDuty / Macie Finding を流すための EventBridge Rule 設定

#### 3.3.3 運用ドキュメント拡張

- `runbook.md` に GuardDuty / Macie Finding 確認手順を追加（日次運用チェックリスト経由）
- `incident-response.md` §10 に GuardDuty / Macie 起因のインシデント対応フローを追加
- `monitoring.md` に GuardDuty / Macie のメトリクス監視を追加（既存 6 アラームに 2〜4 件追加想定）

このことからこう考えます：運用負荷は導入後 1〜2 ヶ月の Tuning 期間で False Positive 抑制設定を整備すれば月数時間程度に収束する想定だが、Tuning 期間自体に管理コストが発生する。本番運用フェーズで脅威モデルが安定してからの導入の方が Tuning コストが抑えられる。

---

## 4. 検討した代替案

### 4.1 代替案 1：CloudTrail + 監査ログのみで部分カバー（**現状方針、Deferred 時の継続案**）

- 採用理由（Deferred 時）：現状の (A)+(B)+(C)+(D) 検知層で個人情報操作・エラー・閾値・履歴の 4 軸はカバー済。検知ギャップは「ふるまい異常自動検知」と「S3 内容スキャン」のみだが、本プロジェクトの現状の保護設計（SSE-KMS + Block Public Access + バケットポリシー Deny + マスキング）が機能している前提では希少イベント
- 限界：手動運用依存、自動アラート化されていない、本番運用フェーズの脅威モデル変化時は再評価が必要

### 4.2 代替案 2：GuardDuty のみ導入（Macie は除外）

- 検討理由：GuardDuty は本システム脅威モデル（UnauthorizedAccess / CredentialAccess / Discovery）との一致度が高く、Macie より検知 ROI が良い
- **不採用理由（本 ADR では）**：Deferred を選択したため現フェーズでは導入しないが、再評価時の有力候補として残す。本番運用フェーズで Accepted 判断する場合の **段階的導入の第 1 候補**

### 4.3 代替案 3：Macie のみ導入（GuardDuty は除外）

- 検討理由：S3 オブジェクト内 PII 検知は §7 マスキング適用範囲外の補完として独自価値あり
- **不採用理由（本 ADR では）**：機密データ自動検知ジョブの課金が GuardDuty より高額になる傾向があり、コスト ROI が悪い。Transcript の PII は §7 マスキングと SSE-KMS 保護で一定範囲はカバー済

### 4.4 代替案 4：両方導入（Accepted 時の方針）

- 検討理由：検知ギャップを最大限カバーし、自動化された脅威検知層を完成させる
- **不採用理由（本 ADR では）**：本フェーズでは課金正当化が困難。本番運用フェーズで脅威モデル + 実稼働量 + 課金許容が確定した時点で Accepted 判断する候補

### 4.5 代替案 5：両方不採用（Rejected 時の方針、CloudTrail のみ）

- 検討理由：本プロジェクトが個人学習スコープから外れることがなく、商用展開もないと確定した場合の選択肢
- **不採用理由（本 ADR では）**：本プロジェクトが将来的に商用ユースケース（複数組織展開・社員数増加）へ展開する余地を残しているため、再評価機会を奪う Rejected は時期尚早。本番運用フェーズで明確に「商用展開しない」が確定した時点で Rejected 判断する候補

---

## 5. 影響（Consequences）

### 5.1 採用しない場合の不足点

- **データ流出の自動検知不可**：CloudTrail の異常 API 呼出検知は手動 Insights クエリ依存。runbook.md 日次運用チェックリストでの SRE 手動確認が必要
- **S3 オブジェクト内の機密情報自動検知不可**：Macie 非導入のため、Transcript JSON に予期せぬ PII が混入した場合の検知は §11 法務 / 情報セキュリティ部門レビュー観点チェックリストでのコードレビュー段階に依存
- **設定ミス検知の自動化欠落**：S3 バケットポリシー誤更新による Public 化等の検知は CloudWatch Alarm（既存 6 件）で網羅されていない領域がある

### 5.2 代替策の継続運用

Deferred 期間中は以下の代替策で部分カバーを継続する：

| 検知ギャップ               | 代替策                                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------------------ |
| ふるまい異常自動検知       | (D) CloudTrail Insights クエリを `runbook.md` 日次運用チェックリストに組込む（手動運用）         |
| S3 オブジェクト内 PII 検知 | §7 マスキング + SSE-KMS + Block Public Access + バケットポリシー Deny で **発生源で抑止**        |
| 設定ミス検知               | (C) CloudWatch Alarm 6 件 + Metric Filter 1 件 + 既存運用ドキュメント `monitoring.md` の閾値運用 |
| データ流出インシデント対応 | `incident-response.md` §10 + `privacy.md` §10 のフローを継続運用                                 |

### 5.3 再評価期日と再評価アクション

**再評価期日**：以下のいずれか早い方

1. **stg / prod 本番稼働開始後 6 ヶ月以内**（本番運用実績データの蓄積を待つ）
2. **社員数 1,000 名超 / Cycle 件数 月 50 件超への拡張時**（規模拡大による脅威モデル変化）
3. **重大な個人情報インシデント発生時**（脅威モデルの即時見直しが必要な場合）

**再評価アクション**：

1. 評価軸 (a) 課金影響を本番運用実績（CloudTrail イベント量 / S3 オブジェクト数）で再算定
2. 評価軸 (b) 検知効果を実運用で発生した既存層の検知漏れケースで再評価
3. 評価軸 (c) 運用負荷を SRE 体制の人員配置で再評価
4. 結果を新規 ADR（仮称 ADR-00XX: GuardDuty / Macie 導入決定）で **Accepted / Rejected** のいずれかに確定し、本 ADR の Deferred を上書き

### 5.4 影響を受けない範囲

- 既存の暗号化・アクセス制御・マスキング・監査ログ設計は本 ADR の Deferred 判断によって変更されない（[`privacy.md`](../operations/privacy.md) §3〜§9 がすべて維持される）
- CloudFormation テンプレートに GuardDuty / Macie リソースを追加しない（Phase 1 IaC スコープに影響なし）
- 既存 6 件の CloudWatch Alarm + Metric Filter 1 件は継続稼働

---

## 6. 完了確認

- [x] ADR-0008 が `docs/decisions/0008-guardduty-macie-evaluation.md` に作成された
- [x] Status が **Deferred** で文書化された
- [x] 評価軸 3 項目 (a)(b)(c) が論理的に展開された（§3.1 / §3.2 / §3.3）
- [x] 代替案 5 件が検討された（§4.1〜§4.5、各不採用理由を明示）
- [x] 再評価期日が具体的に記載された（§5.3：本番稼働後 6 ヶ月 / 規模拡大時 / 重大インシデント時）
- [x] `docs/operations/privacy.md` §10.1 から本 ADR へのリンクが追加された（別作業で実施）

---

## 7. 参照

- [`docs/operations/privacy.md`](../operations/privacy.md) §10「データ流出時のインシデント対応」
- [`docs/operations/runbook.md`](../operations/runbook.md)（日次運用チェックリスト）
- [`docs/operations/incident-response.md`](../operations/incident-response.md)（インシデント対応 8 件）
- [`docs/operations/monitoring.md`](../operations/monitoring.md)（CloudWatch Alarm 6 件 + Metric Filter 1 件）
- [`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md)（課金合意取得保留方針）
- `.kiro/specs/safety-confirmation-system/requirements.md`（Req 15.5 / NFR3）
- AWS 公式：[Amazon GuardDuty 料金](https://aws.amazon.com/guardduty/pricing/)
- AWS 公式：[Amazon Macie 料金](https://aws.amazon.com/macie/pricing/)
- AWS 公式：[GuardDuty Finding types](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html)
- AWS 公式：[Macie sensitive data types](https://docs.aws.amazon.com/macie/latest/user/managed-data-identifiers.html)
