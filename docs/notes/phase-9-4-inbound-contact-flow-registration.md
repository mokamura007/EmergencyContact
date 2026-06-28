# Phase 9.4 Inbound Contact Flow の Connect インスタンスへの登録 - 運用手順

- ステータス: Active（CFn 管理範囲は完了、実機登録は ADR-0005 課金合意取得後に保留）
- 作成日: 2026-06-25
- 関連: `.kiro/specs/safety-confirmation-system/tasks.md` Phase 9.1 / 9.2 / 9.3 / 9.4、`docs/decisions/0005-connect-mock-findings.md`、`infrastructure/template.yaml` Parameters、`infrastructure/contact-flows/inbound.json`
- 目的: Inbound 用代表電話番号と `SafetyConfirmationInboundFlow-${env}` の紐付けを、CFn 管理外の実機操作として再現可能な形で文書化する。

---

## 1. 本ドキュメントの位置づけ

`tasks.md` Phase 9.4 の本文は以下を規定する：

> - Connect コンソールまたは CLI で Inbound 用電話番号を本 Contact Flow に紐付け
> - CFn は Parameters で番号 ARN と Contact Flow ID を受領するのみ（CFn 管理外）

Amazon Connect 仕様上、電話番号と Contact Flow の紐付けは **インスタンス内の設定操作** であり、CFn の `AWS::Connect::*` リソース群（2026 年現在）では完全には宣言的に管理できない領域である（`AWS::Connect::ContactFlow` は Contact Flow 自体の作成・更新を担うが、`PhoneNumber` への紐付けはコンソール／API 経由が主流）。したがって本タスクは：

1. **CFn 管理範囲**: `ConnectInboundPhoneNumberArn` と `InboundContactFlowId` を Parameters として外部受領する（Phase 1.2 で実装済）
2. **CFn 管理外**: 実機 Connect インスタンス上での「電話番号 → Contact Flow」紐付け操作

の 2 層構造になっており、本ドキュメントは後者の運用手順を整備する。

---

## 2. 前提条件

実機紐付け作業に着手する前に、以下が全て成立している必要がある：

| #   | 前提                                                          | 確認方法                                                                                                                                                    |
| --- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | ADR-0005 に基づく **課金合意がユーザーから取得済み**          | `docs/decisions/0005-connect-mock-findings.md` セクション 2「実 Amazon Connect 検証」が「保留解除」に更新されていること                                     |
| 2   | Amazon Connect インスタンスが東京リージョンに購入済           | コンソール / `aws connect list-instances --region ap-northeast-1`                                                                                           |
| 3   | DID 電話番号（Inbound 代表番号）が取得済み                    | `aws connect list-phone-numbers --instance-id <id> --region ap-northeast-1`                                                                                 |
| 4   | Phase 9.1 inbound.json から Inbound Contact Flow が作成済み   | `aws connect list-contact-flows --instance-id <id> --region ap-northeast-1`                                                                                 |
| 5   | Phase 9.2 InboundHandler Lambda がデプロイ済                  | `aws lambda get-function --function-name SafetyConfirmation-InboundHandler-<env>`                                                                           |
| 6   | inbound.json の `${InboundHandlerFnArn}` プレースホルダ置換済 | デプロイスクリプト（Phase 15）の出力ログ                                                                                                                    |
| 7   | CFn Stack デプロイ時に Inbound Parameters を実値で指定済      | `aws cloudformation describe-stacks --query "Stacks[0].Parameters"` で `ConnectInboundPhoneNumberArn` と `InboundContactFlowId` が placeholder ではないこと |

前提 1 が未充足の場合、本作業は実施せず Phase 9.4 を `[x]` のままドキュメント整備のみで止める（実機検証は Phase 14 統合テストまたは ADR-0005 の保留解除タイミングまで延期）。

---

## 3. 実機紐付け手順

### 3.1 Console 手順

1. AWS マネジメントコンソールにログインし、東京リージョン（ap-northeast-1）の Amazon Connect サービスを開く
2. 該当インスタンスのアクセス URL（`https://<instance-alias>.my.connect.aws/`）に Administrator 権限で入る
3. 左サイドバー「ルーティング」→「電話番号」を選択
4. Inbound 用代表電話番号（`ConnectInboundPhoneNumberArn` 末尾と一致するもの）の行をクリック
5. 編集画面で「Contact Flow / IVR」のドロップダウンから `SafetyConfirmationInboundFlow-${env}` を選択
6. 画面下部の「Save」をクリック

### 3.2 CLI 手順

実機構成変更を IaC 化したい場合、`associate-phone-number-contact-flow` API で同等の操作が可能。

```powershell
# 環境変数の準備（CFn Outputs / Parameters から取得）
$INSTANCE_ID  = "<ConnectInstanceId>"
$PHONE_NUMBER_ID = "<電話番号ID（ARN末尾のUUID）>"
$CONTACT_FLOW_ID = "<InboundContactFlowId>"
$REGION = "ap-northeast-1"
$PROFILE = "<AWS CLI profile>"

# 電話番号と Contact Flow の紐付け
aws connect associate-phone-number-contact-flow `
  --instance-id $INSTANCE_ID `
  --phone-number-id $PHONE_NUMBER_ID `
  --contact-flow-id $CONTACT_FLOW_ID `
  --region $REGION `
  --profile $PROFILE
```

> Note: `associate-phone-number-contact-flow` は 2024 年に追加された比較的新しい API。古い CLI（< 2.13）では未サポートのため `aws --version` を確認すること。代替として `update-phone-number` でも紐付け変更は可能だが API 仕様が異なる。

---

## 4. 紐付け後の動作検証

### 4.1 静的検証

1. `aws connect describe-phone-number --phone-number-id $PHONE_NUMBER_ID --region $REGION` を実行
2. 返却 JSON の `ClaimedPhoneNumberSummary.TargetArn` が Inbound Contact Flow の ARN と一致することを確認

### 4.2 着信検証（課金発生）

1. 自席または任意の検証用電話から Inbound 代表番号に発信
2. Connect の CTR（Contact Trace Records）コンソールで該当通話を検索
3. 期待される動作（Phase 9.1 / 9.2 の Done When）：
   - 着信直後に `Polly Mizuki ja-JP` でガイダンス再生（4 分岐のいずれか）
   - `flow` Contact Attribute が `ACTIVE_CYCLE` / `NO_CYCLE` / `NOT_REGISTERED` / `CYCLE_TERMINATED` のいずれかに設定される
   - InboundHandler Lambda の CloudWatch Logs（`/aws/lambda/SafetyConfirmation-InboundHandler-${env}`）に identify ステップが記録される
   - ACTIVE_CYCLE 分岐では録音終了後に finalize ステップが追加で実行され、`Inbound_Contact` テーブルに `voiceStatus=PENDING` 行が出現する
4. 4 シナリオ全てを順次検証する（テスト用 Employee_Master 行と Cycle 行を事前に投入しておく）

### 4.3 失敗時の切り戻し

紐付けは Console / CLI の `disassociate-phone-number-contact-flow` で即時解除可能。Contact Flow 自体や Phone Number 自体には影響を与えないため切り戻しは可逆操作。

```powershell
aws connect disassociate-phone-number-contact-flow `
  --instance-id $INSTANCE_ID `
  --phone-number-id $PHONE_NUMBER_ID `
  --region $REGION `
  --profile $PROFILE
```

---

## 5. CFn Parameters との対応関係

| Parameter 名                   | 用途                                                    | 受領経路                                                                                                           |
| ------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `ConnectInboundPhoneNumberArn` | IAM Policy `Resource` 限定、Lambda 環境変数等で参照     | 実機 Connect で取得した DID 番号 ARN を `aws cloudformation deploy` 時に `--parameter-overrides` で渡す            |
| `InboundContactFlowId`         | inbound.json デプロイ後の Contact Flow ID を CFn に通知 | デプロイスクリプト（Phase 15）が `aws connect create-contact-flow` 実行後に出力された ID を CFn 再デプロイ時に渡す |

両 Parameter は `template.yaml` 行 40 と行 48 に `Type: String` で定義済（Phase 1.2）。`ConnectInboundPhoneNumberArn` の Description は「インバウンド代表電話番号 ARN（CFn 管理外、Requirement 13.1）」と Requirement 13.1 を明示参照している。

---

## 6. ADR-0005 課金保留との関係

本ドキュメント作成時点（2026-06-25）では、ADR-0005 に基づき「実 Amazon Connect 検証は課金合意取得後に保留」が決定済み。したがって：

- **本ドキュメントの作成**: Phase 9.4 の Done When「実機の Inbound 番号への発信が Inbound Contact Flow を経由する」のうち、**手順ドキュメント整備までを達成**
- **実機紐付け実施**: Phase 14 統合テスト着手時または ADR-0005 保留解除時に、本ドキュメントの手順 3 に従って実施
- **検証結果の記録**: 実機検証実施時は ADR-0005 セクション 6.1 で予告されている「別 ADR（仮称 ADR-0006: Amazon Connect 実機検証 findings）」に統合的に記録する

Phase 9.1 inbound.json / Phase 9.2 InboundHandler Lambda / Phase 9.3 PBT が全て `[x]` 完了済（`unittest.mock` ベースのユニットテスト + Hypothesis ベースの Property 11 PBT で 7 件 green、回帰 663 件 green）であるため、**実機検証以外のあらゆる準備は完了**しており、課金合意取得後は本ドキュメントに従って紐付け 1 操作 + 着信 1 回検証のみで Phase 9.4 の Done When を完全達成できる状態にある。

---

## 7. 参照

- `tasks.md` / Phase 9.1, 9.2, 9.3, 9.4
- `requirements.md` / Requirement 13.1, Requirement 17.5
- `design.md` / Inbound_Handler / 構成、CloudFormation テンプレート設計 / Parameters
- `infrastructure/template.yaml` 行 40〜51（Inbound 関連 Parameters）
- `infrastructure/contact-flows/inbound.json`（Phase 9.1 で版管理化、`${InboundHandlerFnArn}` / `${InboundGuidanceText}` プレースホルダ含む）
- `docs/decisions/0005-connect-mock-findings.md`
- AWS 公式 — `associate-phone-number-contact-flow`: https://docs.aws.amazon.com/cli/latest/reference/connect/associate-phone-number-contact-flow.html
- AWS 公式 — Amazon Connect Phone Number 管理: https://docs.aws.amazon.com/connect/latest/adminguide/contact-flow-phone-number-assign.html
