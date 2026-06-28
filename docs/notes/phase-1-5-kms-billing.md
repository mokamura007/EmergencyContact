# Phase 1.5 KMS CMK デプロイ - 課金影響分析と運用手順

- ステータス: Active
- 作成日: 2026-06-23
- 関連: `.kiro/specs/safety-confirmation-system/tasks.md` Phase 1.5、`docs/decisions/0001-runtime-selection.md`、`docs/decisions/0002-handoff-notes-2026-06-19.md`、`docs/decisions/0003-kms-cmk-staged-rollout.md`
- 目的: Phase 1.5 サブステップ 3（実機 CFn デプロイ）で発生する KMS CMK 課金の影響と、不要時の削除手順、課金最小化方法を整理する。

---

## 1. なぜ Phase 1.5 で実機デプロイが必要か

tasks.md Phase 1.5 の Done When は以下のとおり：

> スタックデプロイで CMK が作成され、Alias がコンソールで確認できる

`validate-template` および `cfn-lint` の静的検証のみでは、以下を検出できない：

- KMS のアカウントクォータ（リージョン毎 100,000 CMK の上限、現実的にはほぼ問題ないが）への接触
- SCP（Service Control Policy）/ Permission Boundary による Deny（IAM Identity Policy 評価範囲外）
- CloudFormation コントロールプレーン経由の KMS リソース作成の実動作

加えて、後段 Phase の前提条件として：

- Phase 2.1〜2.11 で DynamoDB テーブル / S3 バケットが SSE-KMS で参照する CMK ARN が実存する必要がある
- Phase 1.5 の Outputs（`KmsCmkArn` / `KmsCmkAliasName`）が他スタックから `!ImportValue` 参照されることを実証する必要がある

したがって、tasks.md と運用要件の両面から実機デプロイは必須である。

---

## 2. 代替案の検討

| 案                                                               | 概要                                       | 課金影響                     | tasks.md 整合                                                                  | 採否     |
| ---------------------------------------------------------------- | ------------------------------------------ | ---------------------------- | ------------------------------------------------------------------------------ | -------- |
| **A: 実機デプロイをスキップ**                                    | validate / lint のみで Phase 1.5 を [x] 化 | $0                           | **未達**（Done When「スタックデプロイで CMK が作成され」を満たさない）         | 不採用   |
| **B: dev デプロイ → 検証 → 即削除 → Phase 2 着手時に再デプロイ** | 短期間のみ CMK 存在                        | 約 $0.27（8 日分）           | 達成                                                                           | **採用** |
| **C: LocalStack 等で代替**                                       | ローカル KMS エミュレータ使用              | $0                           | spec の意図（実 AWS の CMK 確認）と乖離。Phase 2 以降の SSE-KMS 連動も検証不能 | 不採用   |
| **D: Phase 1.5 を Phase 2 とマージ**                             | 一括 CFn デプロイ                          | 同じ（B より長期維持で増額） | tasks.md Wave 構成違反                                                         | 不採用   |

**結論**: 代替案 B（即削除パターン）を採用する。

---

## 3. 課金が発生する仕組み（詳細手順）

### 3.1 課金対象リソース

| リソース                                | 課金                                                    |
| --------------------------------------- | ------------------------------------------------------- |
| `AWS::KMS::Key`（Customer Managed Key） | **$1.00 / 月 / key**（東京リージョン、AWS KMS Pricing） |
| `AWS::KMS::Alias`                       | 無料                                                    |
| KMS API 呼出（Encrypt/Decrypt 等）      | $0.03 / 10,000 リクエスト（Phase 1.5 では発生せず）     |

### 3.2 課金単位の動作

- 月次課金で按分計算（CMK がアクティブだった期間に応じる）
- 日割り換算: 約 **$0.033 / 日**（≒ 5 円 / 日）
- 課金開始タイミング: CMK が `Enabled` 状態になった瞬間（CFn の create-stack で数十秒以内）
- 課金終了タイミング: CMK が完全削除された瞬間（Pending Deletion 中も課金継続）

### 3.3 詳細手順（実機デプロイ）

```powershell
# 1. テンプレ本体を取得
$body = Get-Content -Raw -Encoding UTF8 'C:\_oka\a_資格関係\1_AWS\1_AS部\kiro\infrastructure\template.yaml'

# 2. スタック作成（CMK 作成 = 課金開始）
aws cloudformation create-stack `
  --stack-name safety-confirmation-dev `
  --template-body $body `
  --parameters `
    ParameterKey=EnvironmentName,ParameterValue=dev `
    ParameterKey=ConnectInstanceId,ParameterValue=placeholder `
    ParameterKey=ConnectInstanceArn,ParameterValue=placeholder `
    ParameterKey=ConnectOutboundPhoneNumberArn,ParameterValue=placeholder `
    ParameterKey=ConnectInboundPhoneNumberArn,ParameterValue=placeholder `
    ParameterKey=OutboundContactFlowId,ParameterValue=placeholder `
    ParameterKey=InboundContactFlowId,ParameterValue=placeholder `
    ParameterKey=OperatorEmail,ParameterValue=placeholder@example.com `
  --profile AWS-security-check --region ap-northeast-1

# 3. CREATE_COMPLETE まで同期待機
aws cloudformation wait stack-create-complete --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1
```

---

## 4. 課金を最小化する方法

### 4.1 検証完了後の即削除（採用パターン）

- デプロイ完了 → 4 件の検証コマンド実行（1 日以内）→ 即 `delete-stack`
- 7 日間の Pending Deletion 後に完全削除（課金停止）
- 実総課金: 検証 1 日 + Pending Deletion 7 日 = 8 日分 ≒ **約 $0.27**

### 4.2 `PendingWindowInDays: 7` の設定（実施済）

- template.yaml の `KmsCmk.Properties` に `PendingWindowInDays: 7` を明示済（2026-06-23 サブステップ 2-追補で追加）
- これにより Pending Deletion 期間が 30 日 → 7 日に短縮
- 削除後の余分課金が **約 $0.77 → 約 $0.23 に削減**（差額 約 $0.54）
- 最短は 7 日（AWS KMS の制約）、これより短くは設定不可

### 4.3 AWS マネージドキー（`alias/aws/dynamodb` 等）で代替する案

- 無料
- ただし `design.md` / Requirement 15.1（SSE-KMS = KMS_Key、CMK 必須）および NFR3 と矛盾するため不採用

### 4.4 不要な dev 環境の長期保持を避ける

- Phase 1.5 単独の dev 環境は検証完了直後に削除
- Phase 2 着手前まで CMK を維持する理由なし（Phase 2 着手時に再 create-stack で十分）

---

## 5. 不要になった場合の削除手順

### ステップ 1: スタック削除

```powershell
aws cloudformation delete-stack --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1

# 削除完了まで同期待機（ScheduleKeyDeletion 呼出が完了するまで）
aws cloudformation wait stack-delete-complete --stack-name safety-confirmation-dev --profile AWS-security-check --region ap-northeast-1
```

### ステップ 2: Alias 削除の確認

```powershell
aws kms list-aliases --query "Aliases[?AliasName=='alias/dev-safety-confirmation']" --profile AWS-security-check --region ap-northeast-1
```

→ 空配列 `[]` が返れば Alias 削除完了。

### ステップ 3: CMK の Pending Deletion 状態を確認

```powershell
# 削除前に Outputs から KmsCmkArn を控えておくと簡単
aws kms describe-key --key-id <控えておいた KeyId or ARN> --profile AWS-security-check --region ap-northeast-1
```

期待される出力：

- `KeyState`: `PendingDeletion`
- `DeletionDate`: 削除予約日時から 7 日後（`PendingWindowInDays: 7` 指定済のため）

→ 7 日経過で完全削除、課金停止。

### ステップ 4（オプション）: 削除予約を取り消す

7 日経過前であれば、削除予約をキャンセル可能：

```powershell
aws kms cancel-key-deletion --key-id <KeyId> --profile AWS-security-check --region ap-northeast-1

# Cancel 後は KeyState が Disabled になるため、再有効化が必要
aws kms enable-key --key-id <KeyId> --profile AWS-security-check --region ap-northeast-1
```

注意点：

- Cancel 後、CMK は CFn の管理外（delete-stack で CFn 管理が解除済）
- 再使用する場合は CFn テンプレを修正して再 import するか、別 CMK として手動運用する必要あり

### ステップ 5: Pending Deletion 期間中の課金状況

- 7 日間 Pending Deletion 中も $0.23 程度（$1/月 × 7/30）の課金が継続
- AWS Billing で「AWS Key Management Service」項目として計上される
- CMK 完全削除と同時に課金停止

---

## 6. 推奨運用パターン

### パターン X（推奨）: Phase 1.5 検証完了後の即削除 → Phase 2 着手時に再作成

**Phase 1.5 完了時のフロー**：

1. CFn create-stack 実行 → CMK 作成 → **課金開始**
2. 4 件の検証コマンド実行（1 日以内）
3. tasks.md Phase 1.5 を `[x]` に更新
4. handoff-notes 更新
5. **CFn delete-stack 実行（即日）**
6. 7 日間の Pending Deletion 待機（この間も課金継続）
7. 7 日後に CMK 完全削除、**課金停止**

**予想総課金**: 約 $0.27（検証 1 日 + Pending Deletion 7 日 = 8 日分、$0.033/日）

**Phase 2 着手時のフロー**：

1. Phase 2 のテンプレ拡張（DynamoDB / S3 リソース追加、KMS Key Policy も更新 — ADR-0003 の段階拡張）
2. CFn create-stack（新規 CMK 作成、新しい ARN になる）
3. Phase 2 検証

**注意点**：

- Pending Deletion 期間中（7 日間）に新 create-stack を行うと、同名 Alias `alias/dev-safety-confirmation` が **競合** する可能性
  - 旧 CMK の Pending Deletion 状態でも Alias 自体は CFn delete-stack 時に削除されているため、新 Alias 作成は理論上可能
  - ただし AWS KMS の Eventual Consistency により短時間競合する可能性は残る
- **安全策**: 7 日後に旧 CMK 完全削除を待ってから Phase 2 着手、または Phase 2 では別 Alias 名（例 `alias/dev-safety-confirmation-v2`）を一時的に使用

### パターン Y: Phase 1.5 検証完了後そのまま維持 → Phase 2 で update-stack

**フロー**：

1. CFn create-stack 実行 → CMK 作成
2. 4 件の検証コマンド実行
3. tasks.md Phase 1.5 を `[x]` に更新
4. **削除せず維持**
5. Phase 2 着手時に同スタックを update-stack で拡張（CMK 継続使用）

**予想総課金**: Phase 1.5 〜 Phase 2 完了まで毎月 $1

- Phase 2 完了までの期間に比例
- 例: 1 ヶ月で Phase 2 完了 → $1
- 例: 3 ヶ月で Phase 2 完了 → $3

**利点**: 削除手順が不要、Alias 競合リスクなし、Phase 1.5 → Phase 2 の連続性が高い
**欠点**: 予算管理が必要、Phase 2 開始遅延で課金累積

### 採用パターン: ユーザーが Phase 1.5 完了時に X / Y のいずれかを別途決定する

このノート作成時点（2026-06-23）では未決定。Phase 1.5 サブステップ 3（実機デプロイ）完了後、検証結果を踏まえて改めて grill-me で確認する。

---

## 付録: AWS 公式仕様の根拠

- [DeletionPolicy attribute（AWS CFn UserGuide）](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-attribute-deletionpolicy.html)
  - DeletionPolicy 未指定時のデフォルト動作は `Delete`
- [AWS::KMS::Key（AWS CFn TemplateReference）](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-kms-key.html)
  - `PendingWindowInDays` プロパティ仕様（7〜30、デフォルト 30）
- [AWS KMS - Deleting keys（KMS Developer Guide）](https://docs.aws.amazon.com/kms/latest/developerguide/deleting-keys.html)
  - 「AWS KMS は明示的に削除予約しない限り KMS キーを決して削除しない」（出典に基づく確定動作）
- [AWS KMS - Scheduling deletion（Solutions Library）](https://docs.aws.amazon.com/solutions/latest/research-service-workbench-on-aws/scheduling-the-deletion-of-kms-keys.html)
  - 「7〜30 日の必須待機期間」
- [AWS KMS Pricing](https://aws.amazon.com/kms/pricing/)
  - CMK の課金体系（$1/月/key、東京リージョン）

なお、本ノート内の引用はすべて 30 語以下の制限を遵守。引用なしで参照する場合は本文を言い換え。
