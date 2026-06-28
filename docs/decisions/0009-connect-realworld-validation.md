# ADR-0009: Amazon Connect 実機検証 findings + 料金合意（Phase 14 / 15 着手前提）

- ステータス: **Accepted**（2026-06-27 セッション 18 合意取得、§6 全 24 項目 ✅）
- 決定日: 2026-06-27（セッション 18、Phase 14 / 15 着手前合意）
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md`（Req 5.1 / 13.1 / NFR3）、`.../design.md`（Connect_Caller / Inbound_Handler / Voice_Transcriber）、`.../tasks.md`（Phase 14.1〜14.11 / 15.2 / 15.6）
- 関連 ADR: [`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md)（§6.1 保留条項を本 ADR で解除）、[`docs/decisions/0001-runtime-selection.md`](./0001-runtime-selection.md)、[`docs/decisions/0003-kms-cmk-staged-rollout.md`](./0003-kms-cmk-staged-rollout.md)、[`docs/decisions/0004-handoff-notes-2026-06-25.md`](./0004-handoff-notes-2026-06-25.md)、[`docs/decisions/0007-acm-cert-issuance.md`](./0007-acm-cert-issuance.md)、[`docs/decisions/0008-guardduty-macie-evaluation.md`](./0008-guardduty-macie-evaluation.md)
- 関連運用ドキュメント: [`docs/operations/deploy.md`](../operations/deploy.md)、[`docs/operations/runbook.md`](../operations/runbook.md)、[`docs/operations/privacy.md`](../operations/privacy.md)

---

## 1. コンテキスト

ADR-0005「Amazon Connect mock 試作 — テスト戦略 findings（代替案で代行）」§6.1「残課題と保留事項」は、Amazon Connect の実機検証を **課金合意取得後に保留** として明記している。引用：

> 以下の検証は本 ADR の代替案では達成不可であり、別途実施が必要：
>
> - 自席電話への Outbound 発信成功（`StartOutboundVoiceContact` の実 API 呼出 → DID 番号からの発呼 → 受話）
> - 自席からの Inbound 着信成功（DID 番号への着信 → Inbound Contact Flow 起動 → Lambda Invoke）
> - Polly TTS による音声合成と通話への乗せ込み
> - 録音 S3 出力の実生成（`s3:ObjectCreated` イベントが TranscribeStarter Lambda へ届く）
> - Transcribe `language-code=ja-JP` ジョブの実起動と結果 JSON の S3 出力
> - 通話結果コード（RECORDED / NO_ANSWER / BUSY / VOICEMAIL）の Contact Flow からの実値配信
>
> これらは Phase 7 ConnectDispatcher と Contact Flow の結合検証時、もしくは Phase 14 統合テスト時に、課金合意取得後にまとめて実施する。実施時は **別 ADR（仮称 ADR-0006: Amazon Connect 実機検証 findings）** として記録する。

ADR-0005 §7.3 は更に、**「料金体系合意の grill-me 自体は将来別途実施（ユーザー判断者）」** と明示。

本プロジェクトの現状（`docs/notes/_progress.md` セッション 17 末）：

- 全 131 タスク中 119 タスクが完了済（90.77%）
- 残 12 タスクはすべて「ADR-0005 課金合意取得待ち」状態
- 残 12 件の内訳：Phase 14.1〜14.4 / 14.7〜14.9 / 14.11（dev 環境 E2E 統合テスト 8 件）+ Phase 14.5 / 14.6（実機検証待ち）+ Phase 15.2（dev 初回デプロイ）+ Phase 15.6（受入テスト）

本 ADR はこの保留条項を解除し、Phase 14 / 15 着手の前提条件（事前準備手順 + 検証範囲 + 合意チェックリスト）を文書化することを目的とする。

### 1.1 本 ADR の前提となるユーザー判断（2026-06-27 セッション 18 のすり合わせ結果）

| 確認事項                         | ユーザー回答                                                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| ADR-0005 §6.1 保留条項の解除方針 | (上) 元の tasks.md 通り **実 Connect インスタンス購入 + 実 deploy** で進める（課金許容）                     |
| 着手順序                         | (A) **本 ADR 起票 → ユーザー Connect 購入 → parameters/dev.json 投入 → 15.2 deploy → 14.x 統合 → 15.6 受入** |
| 料金試算の取り方                 | **(ii) 料金確認はユーザー責任**、本 ADR にはフレームワークだけ記載（具体的な金額は ADR に記載しない）        |

このことからこう考えます。本 ADR は「料金額」を扱わず、「**料金確認フロー + 検証範囲 + 完了条件 + 合意チェックリスト**」のみを扱う設計とする。

## 2. 決定

| 項目                             | 値                                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Phase 14 / 15 残 12 件の着手方針 | **実 Amazon Connect インスタンス購入 + 実機検証で消化**                                                                   |
| 料金確認の責任                   | **ユーザー責任**（本 ADR 内では具体的な金額を扱わない、§5 参照）                                                          |
| 課金開始の権限                   | **ユーザーのみ**（AI / 自動エージェントは Connect インスタンスの購入 / DID 番号取得 / 通話発信を独断で行わない）          |
| 検証完了時に作成する追加文書     | 本 ADR 末尾 §6 のチェックリストへの ✅ 記入 + `docs/notes/_progress.md` 末尾セクションへの実機検証結果記録                |
| ADR-0005 §6.1 の取扱             | **本 ADR で解除**（合意取得 + Accepted 遷移後）。ADR-0005 §6.1 本文は変更せず、本 ADR が後継 ADR として保留条項を引き継ぐ |

### 2.1 採用しない方針とその理由

| 方針                                                                       | 不採用理由                                                                                                                                                                                                                             |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 14 / 15 を Mock 化（tasks.md / design.md 改訂）                      | ユーザーが「(上) 誘いのまま実 Connect で進める」を選択（§1.1）。Mock 化は受入テストの解釈変更を伴い、要件 5.1 / 13.1 の「自席電話への発信成功」「自席からの着信成功」が達成不能になる                                                  |
| Connect を後回しにして 15.2 deploy を Connect parameter placeholder で先行 | parameters/dev.json で `ConnectInstanceArn` / `OutboundContactFlowId` 等 6 項目が template.yaml の Parameter バリデーション（`AllowedPattern` / 非空文字列）に抵触する設計。placeholder 値ではバリデーション通過しない構造になっている |
| Connect インスタンスを AI が自動購入                                       | 第 19 原則 (d)「常にユーザーに監視されていることを念頭に」+ 第 6 原則「書き込み系の承認必須」+ 不可逆性（DID 番号取得は実電話番号の予約）から、AI 単独実行は不適切                                                                     |

## 3. 事前準備手順（ユーザー手動作業）

本セクションは、ユーザーが本 ADR 合意後に **手動で実施する** 作業を順序付きで記述する。AI / 自動エージェントは関与しない。

### 3.1 Step 1: Amazon Connect インスタンスの購入

1. AWS Management Console にログイン（Profile `AWS-security-check` で使用しているアカウント `214046906694` を想定、`ap-northeast-1`）
2. Amazon Connect サービスを開き、「インスタンスの作成」を選択
3. インスタンスエイリアスを `safety-confirmation-dev` 等で命名（後続の parameters/dev.json に投入する `ConnectInstanceId` / `ConnectInstanceArn` の元になる）
4. アクセス URL（テナント名）を確定
5. 管理者を Amazon Connect 内 User として作成（Cognito とは別ユーザー、Connect 管理画面ログイン用）
6. テレフォニーオプション：**インバウンド有効 / アウトバウンド有効**（両方必須）
7. データストレージ：録音 → S3 バケット `safety-confirmation-recordings-dev-214046906694-ap-northeast-1`（Phase 2.10 で CFn deploy 済を再利用）
8. 暗号化キー：本システムの `alias/dev-safety-confirmation`（Phase 1.5 で deploy 済）
9. インスタンス作成完了後、以下の値を控える：
   - `ConnectInstanceId`（UUID 形式）
   - `ConnectInstanceArn`（`arn:aws:connect:ap-northeast-1:214046906694:instance/...`）

### 3.2 Step 2: DID 電話番号の取得（2 本）

1. Connect インスタンスのコンソールで「電話番号」→「電話番号の請求」
2. **Outbound 用**：DID（直通番号）、Japan、Toll Free または Local（料金プランをユーザー確認後に選択）
3. **Inbound 用**：DID（直通番号）、Japan、Outbound とは別の番号を取得
4. 取得した 2 本それぞれの ARN を控える：
   - `ConnectOutboundPhoneNumberArn`
   - `ConnectInboundPhoneNumberArn`

### 3.3 Step 3: Contact Flow の Import + Publish

1. リポジトリの既存 Contact Flow JSON を準備：
   - `infrastructure/contact-flows/outbound.json`（Phase 7.1 で実装済）
   - `infrastructure/contact-flows/inbound.json`（Phase 9.x で実装済、要存在確認）
2. Connect コンソールで「ルーティング」→「フロー」→「コンタクトフローの作成」
3. インポート機能で `outbound.json` をアップロード → DefinitionSubstitutions の placeholder（`{{CallEndHandlerFunctionArn}}` 等）を実 ARN に置換 → Publish
4. 同様に `inbound.json` をインポート → placeholder 置換 → Publish
5. 公開後、各 Contact Flow の ID を控える：
   - `OutboundContactFlowId`
   - `InboundContactFlowId`
6. Inbound 番号と Inbound Contact Flow の紐付け（電話番号管理画面で「コンタクトフロー」を Inbound Contact Flow に設定）

### 3.4 Step 4: parameters/dev.json への実値投入

1. `infrastructure/parameters/dev.json` を編集
2. Step 1〜3 で控えた 6 項目を投入：
   ```json
   {
     "ConnectInstanceId": "<Step 1 で控えた UUID>",
     "ConnectInstanceArn": "<Step 1 で控えた ARN>",
     "ConnectOutboundPhoneNumberArn": "<Step 2 で控えた Outbound ARN>",
     "ConnectInboundPhoneNumberArn": "<Step 2 で控えた Inbound ARN>",
     "OutboundContactFlowId": "<Step 3 で控えた Outbound Flow ID>",
     "InboundContactFlowId": "<Step 3 で控えた Inbound Flow ID>"
   }
   ```
3. その他の Parameter（`EmployeeAnonymizeSalt` 実値、`OperatorEmail` 実メールアドレス等）も同時に投入（`docs/notes/_progress.md` セッション 17 末「次セッション着手指示 2」参照）
4. Git commit して dev 環境の deploy に備える

### 3.5 Step 5: Lambda → Contact Flow の双方向配線確認

1. Phase 6.3 CallEndHandler の `AWS::Lambda::Permission`（SourceArn=`!Ref ConnectInstanceArn`）が deploy 後に有効になることを確認
2. Phase 9.x InboundHandler の同種 Permission も同様
3. Contact Flow から Lambda Invoke する際の event スキーマ（`Details.ContactData.Attributes.*` 入れ子）が CallEndHandler の handler 入力期待形と整合することを **コードレビューで** 確認（実機呼出は Step 6 以降）
4. `_progress.md` Phase 7.1 申し送り「Connect の `InvokeLambdaFunction` は Lambda イベントを `Details.ContactData.Attributes.*` の入れ子で渡すが、現在の CallEndHandler は flat 入力を期待。Phase 7.4 or 14 で CallEndHandler の入力パーシングを入れ子対応に拡張する必要がある」の解消是非をここで判断

### 3.6 Step 6: 自席電話の準備

1. 検証用の自席電話番号を確定（携帯 / 固定どちらでも可、E.164 形式 `+81...`）
2. ダミー社員 5〜10 名分の電話番号を準備（全員同じ自席番号でも可、Phase 14.1 の Done When「全員に発信 → 全員から応答」を満たすため）
3. ダミー社員データを Phase 15.2 完了後に SPA 経由で投入する手順（または CSV インポート手順）を確定

## 4. 検証範囲と完了条件（Phase 14.x / 15.2 / 15.6 マッピング）

本セクションは、残 12 件の各タスクで「**何を実機検証するか**」「**完了条件は何か**」を tasks.md / requirements.md から逆引きしたマッピング表である。

### 4.1 Phase 14 統合 / 性能テスト（10 件）

| タスク | 検証対象                                                                             | Done When（tasks.md 引用）                                                                 | 関連 Requirement      |
| ------ | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ | --------------------- |
| 14.1   | mode=ALL でサイクル起動 → 発信 → 録音 → Transcribe → KeywordMatcher → 完了           | dev 環境で End-to-End シナリオが green、CloudWatch Logs に各ステップが記録される           | 4, 5, 6, 7, 11.4      |
| 14.2   | mode=UNREACHABLE_ONLY でサイクル起動                                                 | 対象社員数が想定通りで、ALL モードでは含まれていた他社員が対象外になっている               | 4.4, 4.5              |
| 14.3   | 無応答 → 再発信 → 応答取得                                                           | Response の callAttempts が 2、最終 voiceStatus が応答取得（SAFE/INJURED/UNAVAILABLE）     | 5.5, 9.1, 9.2, 9.3    |
| 14.4   | OTHER 判定での再発信（3 回 OTHER 連続で UNREACHABLE 確定）                           | OTHER → 再発信 → 上限 → UNREACHABLE 確定の流れが動作                                       | 9.4, 9.5              |
| 14.5   | 60 分タイムアウト（実装済・実機検証待ち）                                            | TIMEOUT シナリオが完走し、メール通知が届く                                                 | 14.4, 14.5            |
| 14.6   | 録音 → S3 → Transcribe → メタ書込 → 署名付き URL（実装済・実機検証待ち）             | 各タイミング測定で要件を満たし、再生で音声・テキストが取得できる                           | 6.3, 10.2, 10.5, 10.7 |
| 14.7   | 録音 / Transcript 90 日 LCM（テスト用に LCM を 1 日に短縮）                          | 短縮 LCM で削除確認、API が 410 を返す                                                     | 6.5, 10.4, 12.3       |
| 14.8   | インバウンド 4 シナリオ（登録番号+進行中 / 登録番号+30 日以内 / 未登録 / 30 日超過） | 4 シナリオが dev 環境で動作確認できる                                                      | 13.2〜13.8            |
| 14.9   | キーワード辞書追加 → 新規 Cycle 起動 → スナップショット辞書バージョン使用            | Cycle 起動時にスナップショットが取得され、判定が一貫する                                   | 8.5                   |
| 14.11  | 性能テスト：300 名 60 分 SLA                                                         | 60 分以内に COMPLETED 到達、同時アクティブコール 10 を超えない、30 分時点で発信完了率 100% | 14.1〜14.3            |

### 4.2 Phase 15 デプロイ / 受入（2 件）

| タスク | 検証対象                                                                 | Done When（tasks.md 引用）                                                       | 関連 Requirement |
| ------ | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------- | ---------------- |
| 15.2   | dev 環境への初回デプロイと動作確認                                       | dev 環境にログインしてサイクル起動 UI を表示でき、辞書初期データが投入されている | 17.1, 18.1, 18.2 |
| 15.6   | 受入テストの実施（全 Requirement 1〜18、Property 1〜25 を 1 回以上踏破） | 受入テストレポートが作成され、全件 PASS（不合格項目は別チケットで管理）          | 全件             |

### 4.3 検証完了の最終基準

- 上記 12 件すべての Done When 達成
- `docs/notes/_progress.md` 末尾セクションに各タスクの実機検証ログ（実行日時 / 実施者 / 結果 / 課金実績概算）を記録
- 不合格項目があれば別チケット起票（`tasks.md` に新規 Phase 16 起票 or `_progress.md` 副次発見メモ）
- 本 ADR の §6 合意チェックリストに ✅ が全項目入る

## 5. 料金確認の責任分担

本 ADR §1.1 のすり合わせ結果に従い、料金額は本 ADR では扱わない。代わりに **料金確認のフレームワーク** を以下に示す。

### 5.1 課金が発生する AWS リソース（概要のみ、金額記載なし）

| リソース                                   | 課金単位                                            | 確認タイミング                            |
| ------------------------------------------ | --------------------------------------------------- | ----------------------------------------- |
| Amazon Connect インスタンス本体            | 通常は無料、機能利用時のみ課金                      | §3.1 Step 1 実施前                        |
| DID 電話番号（Outbound / Inbound 各 1 本） | **月額固定**（番号タイプにより異なる）              | §3.2 Step 2 実施前                        |
| Outbound 通話                              | **分単位課金**（発信先で異なる、Japan 国内 / 国際） | §3.6 Step 6 実施前                        |
| Inbound 通話                               | **分単位課金**                                      | §3.6 Step 6 実施前                        |
| Amazon Polly TTS                           | **文字数単位課金**（Standard / Neural で異なる）    | §3.3 Step 3 Contact Flow 動作確認時       |
| Amazon Transcribe                          | **音声時間単位課金**（言語コード ja-JP）            | Phase 14.6 実機検証時                     |
| S3 録音ストレージ                          | **GB 月課金**（90 日 LCM で自動削除）               | Phase 14.7 で削除確認、それまでは継続課金 |
| S3 データ転送                              | **アウトバウンド転送量**                            | 署名付き URL ダウンロード時               |
| CloudWatch Logs                            | **取り込み量 + ストレージ**                         | Phase 14 全期間                           |
| SNS（OperatorTopic）                       | **Publish 数 + 通知数**                             | Phase 14.4 / 14.5 タイムアウト通知時      |
| CloudFormation Change Set 実行             | **無料**（リソース実体の作成のみ課金）              | Phase 15.2 deploy 時                      |

### 5.2 料金確認の責任所在

| 確認項目                                                                                        | 責任者       | 確認方法                                     |
| ----------------------------------------------------------------------------------------------- | ------------ | -------------------------------------------- |
| 上記各リソースの最新料金                                                                        | **ユーザー** | AWS 公式料金ページを直接参照                 |
| 検証 1 回あたりの概算予算                                                                       | **ユーザー** | AWS Pricing Calculator 等で試算              |
| 検証中の継続課金（DID 月額 / S3 ストレージ等）の許容範囲                                        | **ユーザー** | 月額上限を本 ADR §6 合意チェックリストで明示 |
| AWS Budgets / Billing Alarm の設定                                                              | **ユーザー** | §6 合意チェックリストの前提として推奨        |
| 検証完了後のリソース整理（DID 番号解放 / Connect インスタンス削除 / S3 オブジェクト削除）の判断 | **ユーザー** | §7 リスクとロールバック参照                  |

### 5.3 AI / 自動エージェントの責任範囲

| 項目                           | 対応                                                                                                                                   |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Connect インスタンスの自動購入 | **行わない**                                                                                                                           |
| DID 番号の自動取得             | **行わない**                                                                                                                           |
| 通話発信の自動実行             | Phase 14 統合テスト実施時に `aws connect start-outbound-voice-contact` を呼ぶ可能性があるが、**事前にユーザー承認を取る**（第 6 原則） |
| 料金確認                       | **行わない**（本 ADR §5.1 の項目を提示するだけ）                                                                                       |
| Budgets / Billing Alarm の設定 | ユーザー指示があれば AWS CLI で実施可能、ただし第 6 原則の y/n を取る                                                                  |

## 6. 合意チェックリスト（ユーザー記入）

本 ADR を **Accepted** に遷移させるには、以下のチェック項目すべてに ✅ が入る必要がある。各項目はユーザーが手動で記入する。

**記入日**: 2026-06-27（セッション 18、ユーザー段階的インタビューを通じて記入完了）

### 6.1 料金関連（全 10 項目）

- [x] Amazon Connect インスタンス本体の料金構造を確認した
- [x] DID 電話番号（Outbound / Inbound 各 1 本）の月額料金を確認した
- [x] Outbound / Inbound 通話の分単位料金（Japan 国内）を確認した
- [x] Amazon Polly TTS の文字数単位料金（Standard or Neural）を確認した
- [x] Amazon Transcribe の音声時間単位料金（ja-JP）を確認した
- [x] S3 録音ストレージ + データ転送の料金を確認した（90 日 LCM 自動削除後の継続コストも含む）
- [x] CloudWatch Logs / SNS の料金を確認した（Phase 14 期間中の規模感）
- [x] **検証完了までの総予算上限**：**上限設けず**（中断 / 解約は都度判断方針、ユーザー記入）
- [x] **月額継続コストの許容範囲**：**上限設けず**（中断 / 解約は都度判断方針、ユーザー記入）
- [x] AWS Budgets または Billing Alarm を設定済（推奨、未設定の場合は明示）：設定状況 **設定しない**（都度判断方針のためアラート不要）

### 6.2 検証範囲関連（全 3 項目）

- [x] §4.1 Phase 14 統合 / 性能テスト 10 件すべてを実施することに合意
- [x] §4.2 Phase 15.2 / 15.6 を実施することに合意
- [x] §4.3 検証完了の最終基準（4 項目）を確認し、合意

### 6.3 事前準備関連（全 6 項目）

- [x] §3.1 Connect インスタンス購入を自身で実施することに合意（AI 自動購入は行わない）
- [x] §3.2 DID 番号 2 本の取得を自身で実施することに合意
- [x] §3.3 Contact Flow Import + Publish を自身で実施することに合意
- [x] §3.4 parameters/dev.json への実値投入は AI 支援可(投入手順はユーザー指示で実行)
- [x] §3.5 Lambda → Contact Flow 配線の `Details.ContactData.Attributes.*` 入れ子対応の要否を判断（スタンス：**実機検証時に発覚すれば対応**、事前改修は行わない）
- [x] §3.6 自席電話番号 + ダミー社員データを準備済（または検証時に準備）

### 6.4 リスク受容関連（§7 参照、全 3 項目）

- [x] §7 リスクとロールバックを読み、リスクシナリオに同意
- [x] 検証完了後のリソース整理（DID 番号解放 / Connect インスタンス削除）の判断方針を確定：**都度判断**（検証完了時に判断、§7.2.4 部分ロールバックも選択肢として保持）
- [x] 万一の検証失敗時の予算超過対応方針を確定：**都度判断**（上限設けず方針と整合、§6.1 と一貫）

### 6.5 ADR-0005 §6.1 解除関連（全 2 項目）

- [x] 本 ADR が Accepted に遷移することで ADR-0005 §6.1 の保留条項が解除されることに合意
- [x] ADR-0005 §7.3「料金体系合意の grill-me 自体は将来別途実施（ユーザー判断者）」を本 ADR 合意取得で完了とみなすことに合意

### 6.6 採用方針メモ（後続セッション参照用）

| 項目                                   | 採用方針                                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------------------- |
| 予算 / 月額継続コスト                  | **上限設けず**、中断 / 解約は **都度判断**（状況を見て決める）                            |
| AWS Budgets / Billing Alarm            | **設定しない**（都度判断方針のためアラート不要）                                          |
| §3.5 Lambda 入力スキーマ（入れ子対応） | **事前改修なし**、実機検証時（Phase 14.1）に発覚すれば改修                                |
| リソース整理タイミング                 | **都度判断**、§7.2.4 部分ロールバック（DID のみ解放、インスタンス保持）も選択肢として保持 |
| ADR-0005 §6.1 保留条項                 | 本 ADR Accepted で **解除**                                                               |
| ADR-0005 §7.3 grill-me                 | 本 ADR 合意取得で **完了**                                                                |

**全 24 項目すべてに ✅ が入った（2026-06-27 セッション 18）。本 ADR のステータスを `Proposed` → `Accepted` に変更可能な状態。実際の遷移はユーザーからの明示的な「Accepted 遷移指示」を受領した時点で §1.1 表のステータス欄を更新する。**

## 7. リスクとロールバック

### 7.1 想定リスク

| リスク                                                                              | 影響                                      | 対策                                                                                                                                                         |
| ----------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| DID 番号取得後に検証が完了せず月額課金が継続                                        | 想定外の継続コスト                        | 検証完了後速やかに DID 番号を解放（手順 §7.2.1）                                                                                                             |
| Contact Flow の `InvokeLambdaFunction` event 形式が CallEndHandler の期待形と不整合 | Phase 14.1 で実機統合テスト失敗           | §3.5 Step 5 で事前にコードレビュー、必要なら CallEndHandler を `Details.ContactData.Attributes.*` 入れ子対応に改修（Phase 7.4 申し送り参照）                 |
| Connect 通話料金の予想外の急増（無限ループ等）                                      | 予算超過                                  | (a) ダミー社員数を 5〜10 名に抑制、(b) MaxConcurrentCalls=10 で同時接続制限（既 deploy 済）、(c) AWS Budgets で日次アラート設定（推奨）                      |
| Transcribe ジョブの失敗連鎖（音声品質不良 → TRANSCRIBE_FAILED 連発）                | Phase 14.4 OTHER 判定での再発信検証に影響 | Polly TTS で生成した既知音声で再現性を確保、自席発信時はクリアな音声で応答                                                                                   |
| dev 環境への deploy 失敗（CFn ロールバック）                                        | parameters/dev.json の実値投入を再実施    | `deploy.ps1 -EnvironmentName dev -NoExecuteChangeset` で changeset 事前確認、不整合があれば修正してから再 deploy                                             |
| 認証情報漏洩（Connect インスタンス管理者 / Cognito 管理者）                         | 第三者による不正利用                      | (a) IAM Role Profile=`AWS-security-check` 内で完結、(b) Cognito 管理者は MFA OFF 状態（Phase 3.1 で OFF）→ 検証期間中のみ強制ログアウト + 一時パスワード運用 |

### 7.2 ロールバック手順

#### 7.2.1 DID 番号の解放

1. Connect コンソールで「電話番号」一覧を開く
2. 検証用に取得した DID 2 本それぞれの「解放」ボタンを押下
3. 月額課金停止（解放時点でその月の日割り課金は確定）

#### 7.2.2 Connect インスタンスの削除

1. Connect コンソールで「インスタンスのエイリアス」一覧を開く
2. 検証用インスタンスを選択 → 削除
3. 削除前に「録音 S3 オブジェクト / Transcript / DynamoDB レコード」の保管要否を確認（90 日 LCM 自動削除に任せる場合は削除前のコピー不要）
4. **削除は不可逆**。再構築する場合は §3.1 から再実施

#### 7.2.3 CloudFormation スタックのロールバック

1. `aws cloudformation delete-stack --stack-name safety-confirmation-dev` で deploy 全リソース削除
2. **削除は不可逆**、KMS CMK は 7 日待機期間後に削除（即時取消可能）
3. S3 バケット内オブジェクトは事前に削除（Versioning OFF のため空にすれば即時削除可）
4. 再構築する場合は Phase 15.2 deploy から再実施

#### 7.2.4 部分ロールバック（DID 番号のみ解放、インスタンスは保持）

検証中断時の中間状態。月額課金は DID 解放分のみ停止、Connect インスタンス本体は保持（再開時の再構築コスト回避）。

## 8. 採用範囲・影響

### 8.1 採用範囲

| 区分                  | 対象                                                                                                                                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 本 ADR の対象タスク   | Phase 14.1〜14.4 / 14.5 / 14.6 / 14.7〜14.9 / 14.11 / 15.2 / 15.6（計 12 件）                                                                                        |
| 解除対象の保留条項    | ADR-0005 §6.1 全項目、ADR-0005 §7.3「料金体系合意の grill-me」                                                                                                       |
| 検証対象 AWS サービス | Amazon Connect / Polly / Transcribe / S3（録音 + Transcript）/ CloudFront / Cognito / Lambda / DynamoDB / SFN / SNS / EventBridge / CloudWatch（Logs + Alarms）/ KMS |
| ロールバック対象      | DID 番号 / Connect インスタンス / CloudFormation Stack `safety-confirmation-dev`（§7.2 参照）                                                                        |

### 8.2 採用範囲外

- 本番（prod）環境へのデプロイ（Phase 15.6 受入テストは dev or stg、prod は別 ADR で判断）
- マルチリージョン展開（requirements.md スコープ外）
- SMS / Email / Push 通知統合（requirements.md スコープ外）
- LLM ベース意図判定 / 声紋認証（requirements.md スコープ外）
- 商用運用前提のキャパシティプランニング（本プロジェクトは個人学習スコープ）

### 8.3 影響を受ける後続タスク

| タスク                    | 影響                                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Phase 14.1〜14.11         | 本 ADR Accepted 後に着手可                                                                                            |
| Phase 15.2                | 本 ADR Accepted + §3.1〜3.4 完了後に deploy 実行可                                                                    |
| Phase 15.6                | Phase 14 完了後に着手                                                                                                 |
| `docs/notes/_progress.md` | 本 ADR Accepted 時に「ADR-0005 課金合意取得待ち」表現を「ADR-0009 合意済」に書き換え                                  |
| `tasks.md`                | 各タスク完了時に `[ ]` → `[x]` 化、`[~]` のものは検証完了で `[x]` 化（14.5 / 14.6）                                   |
| 別 ADR の起票候補         | Phase 14 検証中の追加発見があれば ADR-0010 以降で起票（例：Connect Contact Flow 設計改修、Lambda 入力スキーマ調整等） |

## 9. 残課題と未確定事項

### 9.1 ステータス遷移待ち

本 ADR の現在のステータスは `Proposed`。§6 合意チェックリスト 25 項目すべてに ✅ が入り、ユーザーから明示的な「Accepted 遷移指示」を受領した時点で、本 ADR §1.1 表内ステータスを `Accepted` に更新する。

### 9.2 ADR-0006 / 0007 / 0008 採番との関係

`docs/notes/_progress.md` セッション 17 末では本 ADR を **「仮称 ADR-0006」** と記述していたが、その後 0006（dictionary-patch-semantics）/ 0007（acm-cert-issuance）/ 0008（guardduty-macie-evaluation）が既に発行済のため、本 ADR は **採番ズレを引継ぎ 0009 を採用**（ADR-0005 採番ズレと同じ運用方針、ADR-0005 §6.2 参照）。本文中の引用が「ADR-0006」になっている過去ドキュメント（ADR-0005 §6.1 / 進捗ノート等）の文言修正は **別タスクで実施判断**（軽量、優先度低）。

### 9.3 grill-me モードでの再点検

本 ADR §6 合意チェックリストの記入時に、ユーザーが追加で深掘り質問したい項目があれば、`.kiro/steering/grill-me.md` モードで再点検を実施可能。料金試算 / 検証範囲 / リスク受容のいずれの観点でも実施可。

### 9.4 ADR-0005 本文の文言修正

ADR-0005 §6.2 で言及されている「`tasks.md` Phase 0.3 本文の番号不整合」と同様に、ADR-0005 §6.1 内の「別 ADR（仮称 ADR-0006: Amazon Connect 実機検証 findings）」表現は実際には本 ADR (ADR-0009) を指す。**この本文修正は別途のテキスト修正タスクとしてユーザー判断による**（ADR-0005 §6.2 と同じ運用方針）。本 ADR の存在自体で保留条項解除の意図は伝達できるため、ADR-0005 本文の修正必須性は低い。

## 10. 参照

- `.kiro/specs/safety-confirmation-system/requirements.md` / Requirement 5.1, 13.1（Connect Outbound / Inbound）, 14.1〜14.5, 17.1
- `.kiro/specs/safety-confirmation-system/design.md` / Connect_Caller, Inbound_Handler, Voice_Transcriber, Testing Strategy
- `.kiro/specs/safety-confirmation-system/tasks.md` / Phase 14.1〜14.11, Phase 15.2, Phase 15.6
- [`docs/decisions/0001-runtime-selection.md`](./0001-runtime-selection.md) / Python 3.12 + boto3 + Hypothesis 採用
- [`docs/decisions/0003-kms-cmk-staged-rollout.md`](./0003-kms-cmk-staged-rollout.md) / KMS CMK 設計
- [`docs/decisions/0004-handoff-notes-2026-06-25.md`](./0004-handoff-notes-2026-06-25.md) / 議題拡張（料金合意 + 代替案検討）
- [`docs/decisions/0005-connect-mock-findings.md`](./0005-connect-mock-findings.md) / 本 ADR で §6.1 保留条項を解除
- [`docs/decisions/0007-acm-cert-issuance.md`](./0007-acm-cert-issuance.md) / Phase 11.1 ACM 証明書発行手順
- [`docs/decisions/0008-guardduty-macie-evaluation.md`](./0008-guardduty-macie-evaluation.md) / 検知層の現状整理
- [`docs/operations/deploy.md`](../operations/deploy.md) / Phase 15.1 デプロイ手順書
- [`docs/operations/runbook.md`](../operations/runbook.md) / 運用ドキュメント
- [`docs/operations/privacy.md`](../operations/privacy.md) / 個人情報取扱運用
- `docs/notes/_progress.md` / セッション 17 末「次セッション着手指示」「ADR-0005 課金合意取得待ち」表現の原典
- `backend/tests/lambdas/connect_dispatcher/`、`backend/tests/lambdas/call_end_handler/`、`backend/tests/lambdas/transcribe_starter/` / ADR-0005 採用の unittest.mock パターン実装
- AWS 公式 — Amazon Connect 料金: https://aws.amazon.com/connect/pricing/
- AWS 公式 — Amazon Polly 料金: https://aws.amazon.com/polly/pricing/
- AWS 公式 — Amazon Transcribe 料金: https://aws.amazon.com/transcribe/pricing/
- AWS 公式 — Amazon S3 料金: https://aws.amazon.com/s3/pricing/
- AWS 公式 — AWS Budgets: https://aws.amazon.com/aws-cost-management/aws-budgets/
