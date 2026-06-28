# ADR-0003: KMS CMK の段階的キーポリシー実装

- ステータス: Accepted（改訂版）
- 初版決定日: 2026-06-23
- **改訂日: 2026-06-25**（段階 2 を廃止、実機検証結果を反映）
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md` / `.../design.md` / `.../tasks.md`（Phase 1.5、Phase 2.1〜2.11、Phase 6.x）
- 関連 ADR: `docs/decisions/0001-runtime-selection.md`、`docs/decisions/0002-handoff-notes-2026-06-19.md`、`docs/decisions/0004-handoff-notes-2026-06-25.md`

## 改訂サマリ（2026-06-25）

2026-06-25 のセッション 3 における実機検証（`safety-confirmation-dev` スタックの update-stack 実行、`UPDATE_COMPLETE` 確認）の結果、**初版で「段階 2」として記載していた「Phase 2.x（DynamoDB / S3 着手）でのキーポリシー追加」は不要であることが判明**した。本改訂では：

- 段階 2 を **DEPRECATED**（廃止）として明示記録し、削除はしない（歴史保全）
- Phase 6.x の Lambda / SFN / Transcribe 用 Statement 追加を「**段階 2（旧段階 3）**」に繰り上げ
- 結果（トレードオフ）セクションに実機検証エビデンスを追記
- 採用範囲表から Phase 2.x 行を削除、Phase 6.x 行を段階 2 に繰り上げ

初版本文（コンテキストおよび当時の選定根拠）は保全する。

## コンテキスト

`design.md` の **KMS CMK 設計** セクションには、キーポリシーの主要要点として以下 4 件が確定設計として記述されている。

| #   | キーポリシー要点                                                                                    |
| --- | --------------------------------------------------------------------------------------------------- |
| (1) | ルート全権                                                                                          |
| (2) | Lambda / SFN 実行ロール / Transcribe サービスロールへ `Encrypt/Decrypt/GenerateDataKey/DescribeKey` |
| (3) | DynamoDB / S3 サービスからの `kms:ViaService` 制限付き使用許可                                      |
| (4) | 上記以外は Deny                                                                                     |

一方、`tasks.md` の依存関係および Wave 構成では、Phase 1.5 着手時点で (2) と (3) で参照すべき AWS リソースが **まだ存在しない**：

- (2) で参照する Lambda / SFN / Transcribe 実行ロール → Phase 1.6（共通 IAM ロール雛形）以降で作成予定
- (3) で参照する DynamoDB テーブル群（D1〜D8）および S3 バケット群（D5, D6, SpaBucket） → Phase 2.1〜2.11 で作成予定

すなわち、Phase 1.5 タスクのスコープ内のみで `design.md` の完成形キーポリシーを実装することは **物理的に不可能**である。

セッション 3（2026-06-23）の grill-me において、本乖離の取扱方針を 3 択（(i) ADR 文書化のみ・design.md 不変／(ii) design.md に段階注記／(iii) ADR と design.md 両方更新）でユーザーに確認した結果、**(i)** が選択された。本 ADR の初版はその決定を記録したものである。

## 決定（改訂版、2026-06-25）

| 項目                                                                 | 値                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 1.5 でのキーポリシー実装範囲（**段階 1**）                     | **(1) ルート全権のみ**                                                                                                                                                                                                                      |
| ~~Phase 2.x（DynamoDB / S3 着手）でのキーポリシー追加（旧段階 2）~~  | **DEPRECATED**（2026-06-25 廃止）。詳細は「結果（トレードオフ）」セクションの実機検証結果を参照                                                                                                                                             |
| Phase 6.x（Lambda 着手）でのキーポリシー追加（**段階 2、旧段階 3**） | **(2) Lambda / SFN / Transcribe 実行ロール用 Statement（`Encrypt/Decrypt/GenerateDataKey/DescribeKey`）を UpdateKeyPolicy**。実装方式（IAM Principal + `kms:ViaService` / Service Principal / 両方併用）は Phase 6 着手時の grill-me で確定 |
| (4) 明示の Deny ステートメント                                       | **追加しない**。KMS のデフォルト動作（許可していないアクションは Deny）に委ねる                                                                                                                                                             |
| design.md の修正                                                     | **行わない**（初版 (i) の決定を維持）                                                                                                                                                                                                       |

## 選定根拠

### A 案（本決定）を採用した主因（初版時点）

1. **物理的制約**：Phase 1.5 で参照する必要がある IAM Role ARN / DynamoDB Table ARN / S3 Bucket ARN がいずれも未生成のため、design.md の完成形ポリシーを Phase 1.5 単独で書ききることはできない
2. **Wave 順保護**：tasks.md の依存グラフは Phase 1 → 2 → 3 → 6 と段階的に拡張する設計で、Wave 順を入れ替えると後段の依存検証コストが増える
3. **責務分離**：CMK 自体の作成・有効化（Phase 1.5）と、特定リソースへの使用許可付与（Phase 2 / 6）は本来分離可能で、段階的に組み立てる方が変更レビューが容易

### 改訂時の追加根拠（2026-06-25）

4. **実機検証エビデンス**：Key Policy 拡張なし（ルート全権 Statement のみ）+ AdministratorAccess の組合せで、Phase 2.x の DynamoDB 9 + S3 3 = 12 リソースの追加デプロイが `UPDATE_COMPLETE` で完了した。すなわち、初版段階 2 で前提としていた「Phase 2 着手時に UpdateKeyPolicy が必須」は誤認だった。

### 却下案（初版時点、改訂後も維持）

**B 案：Phase 1.5 で `Principal: AWS: '*'` + 任意 Condition で仮実装**

- 却下理由：暫定状態とはいえ「全プリンシパル許可」は一時的にでも作りたくない。Condition でガードしても、レビュー時のセキュリティ判断を曖昧にする

**C 案：Phase 1.6（IAM 雛形）を Phase 1.5 より先行**

- 却下理由：tasks.md の Wave 2 内では 1.5 と 1.6 が同 Wave で並列可能とされているが、handoff の進捗管理（1.5 → 1.6 の順）に従う方針が確立済み。順序入れ替えは別途 grill-me が必要

### (4) 明示 Deny を採用しなかった理由

- KMS キーポリシーには「許可していないアクションは Deny」というデフォルト動作があり、(1) ルート全権の Statement のみでは他プリンシパルからの操作は自動的に拒否される
- 明示 Deny を書くと、後段 Phase で (2) を追加した際に Allow が Deny を上書きできない（IAM では Deny は常に優先）リスクが残る。design.md (4) の「上記以外は Deny」は、運用上 KMS のデフォルト動作で意図を満たすため明示記述は省略する

## 結果（トレードオフ）

### ポジティブ（初版時点）

- Phase 1.5 の Done When（「スタックデプロイで CMK が作成され、Alias がコンソールで確認できる」）を単独で素直に満たせる
- Phase 6 着手時点で必要な情報がそろってから順次キーポリシーを拡張するため、各段階のレビューが容易

### ネガティブ / リスク（初版時点、後に**反証**）

- ~~**Phase 2 着手時に UpdateKeyPolicy を忘れた場合、DynamoDB / S3 のスタックデプロイが KMS 関連エラーで失敗する**~~
  - **2026-06-25 実機検証で反証**：詳細は次セクション
- **CMK の運用開始（実暗号化）は Phase 2 以降まで遅延**する
  - 影響：CMK 単体存在中は実害なし（CMK の利用料金は発生するが、月数百円程度）

### 実機検証結果（2026-06-25 追記）

| 項目                  | 内容                                                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 実施日時              | 2026-06-25T00:44:10 UTC（JST 09:44:10）                                                                                                             |
| 対象スタック          | `safety-confirmation-dev`（ap-northeast-1、214046906694）                                                                                           |
| 実行コマンド          | `aws cloudformation update-stack` with template containing DynamoDB 9 + S3 3 = 12 new resources、`UsePreviousValue=true` for all 20 Parameters      |
| Key Policy 拡張の有無 | **なし**（ルート全権 Statement のみ）                                                                                                               |
| 結果                  | **`UPDATE_COMPLETE`** ✅（仮説 A 確定）                                                                                                             |
| 追加検証              | 全 9 テーブル PITR=ENABLED、SSE-KMS = CMK Arn 一致、LockoutTable TTL=ENABLED (expireAt)、RecordingsBucket Encryption = aws:kms + CMK + BucketKey ON |
| 想定外の事象          | なし                                                                                                                                                |

### 検証から導かれる理解

- **KMS Default Key Policy のルート全権 Statement は「IAM ポリシー経由のアクセスを許可する委譲スイッチ」**（[公式ドキュメント](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html) より：「Instead, it allows the account to use IAM policies to delegate the permissions specified in the policy statement.」）
- AdministratorAccess を持つ IAM User は `kms:*` を IAM Policy 経由で持つため、ルート全権 Statement のおかげで CMK にアクセス可能になる
- DynamoDB / S3 の SSE-KMS リソース作成時、サービスが CMK を使うために必要な `kms:CreateGrant` は、呼出元 IAM Principal の IAM Policy 経由で評価される（Aurora の[公式ドキュメント](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Overview.Encryption.Keys.html) の類推：「a user must have permissions to call the following operations on the customer managed key: kms:CreateGrant, kms:DescribeKey. You can specify these required permissions in a key policy, or in an IAM policy if the key policy allows it.」）
- 従って、ルート全権 Statement + AdministratorAccess の組合せで DynamoDB / S3 の CreateTable / CreateBucket（SSE-KMS）は通る

### 未検証領域（後続段階で確認）

- **Lambda Role が CMK で暗号化されたデータを実 I/O する場合の権限**：Phase 6 着手時の段階 2（旧段階 3）grill-me で別途確認
- **EventBridge / Step Functions / Transcribe Service Principal が CMK を呼ぶ場合の権限**：同上

## 採用範囲（改訂版）

| 区分                              | 対象                                                                                                                                                                                                     |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 1.5（**段階 1**）           | KmsCmk（ルート全権のみ）、KmsCmkAlias                                                                                                                                                                    |
| ~~Phase 2.x（旧段階 2）~~         | **DEPRECATED**：実機検証で不要と判明（2026-06-25）。本改訂で削除                                                                                                                                         |
| Phase 6.x（**段階 2、旧段階 3**） | キーポリシー更新（Lambda 実行ロール群 / SFN 実行ロール / Transcribe サービスロールへの Encrypt/Decrypt/GenerateDataKey/DescribeKey 許可ステートメント追加）。実装方式は Phase 6 着手時の grill-me で確定 |

### 採用範囲外

- (4) の明示 Deny ステートメント（KMS デフォルト Deny に委ねる）
- Multi-Region Key の使用（Requirement 18.5 によりマルチリージョン展開はスコープ外）
- 顧客管理鍵以外（AWS マネージド鍵）の併用

## 影響を受ける後続タスク（改訂版）

- ~~**Phase 2.1〜2.11**：DynamoDB テーブル / S3 バケット作成タスク。着手時にキーポリシー更新サブステップを実施~~ → **不要、2026-06-25 完了済み**
- **Phase 6.1〜6.8**：Lambda 関数および Step Functions 実行ロール作成タスク。着手時にキーポリシー更新サブステップを実施（段階 2、旧段階 3）
- **Phase 15**：デプロイスクリプト作成。スタックの新規作成 / 更新の両モードで KMS キーポリシーが正しく反映されることを確認する手順を含める

## 参照

- `design.md` / **KMS CMK 設計**
- `requirements.md` / Requirement 6.4, 10.3, 15.1, NFR3
- `tasks.md` / Phase 1.5（完了）、~~Phase 2.1〜2.11（完了、Key Policy 拡張不要が確認済）~~、Phase 6.x（未着手、段階 2 で対応）
- `docs/decisions/0004-handoff-notes-2026-06-25.md` / 実機検証経緯
- `docs/notes/_progress.md` / セクション 4「実機デプロイ済構成」
- AWS 公式：[Default key policy](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html)
- AWS 公式：[kms:ViaService condition key](https://docs.aws.amazon.com/kms/latest/developerguide/policy-conditions.html#conditions-kms-via-service)
- AWS 公式：[Authorizing use of a customer managed key (Aurora)](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Overview.Encryption.Keys.html)（DynamoDB の挙動類推根拠）
