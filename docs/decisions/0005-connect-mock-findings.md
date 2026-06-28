# ADR-0005: Amazon Connect mock 試作 — テスト戦略 findings（代替案で代行）

- ステータス: Accepted
- 決定日: 2026-06-25
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md` / `.../design.md` / `.../tasks.md`（Phase 0.3、6.2、6.3、6.4、7.x）
- 関連 ADR: `docs/decisions/0001-runtime-selection.md`、`docs/decisions/0003-kms-cmk-staged-rollout.md`、`docs/decisions/0004-handoff-notes-2026-06-25.md`

## 1. コンテキスト

`tasks.md` Phase 0.3「Amazon Connect mock 試作」は当初、開発アカウントに最小構成の Amazon Connect インスタンスを購入し、Outbound / Inbound の Hello World Contact Flow を手動構築して自席電話で発信・着信を確認する **実機検証タスク** として規定されていた。Done When は「開発者の自席電話に Outbound 発信が届き、自席から Inbound 着信できることを 1 度確認、知見ドキュメントが作成されている」である。

しかし以下の制約が顕在化した：

- Amazon Connect Tokyo は「インスタンス購入 + DID 電話番号取得 + 通話 + Polly TTS + 録音 S3」の課金が同時に発生し、本プロジェクト（個人学習・資格対策スコープ）の課金許容範囲を超える可能性がある（`docs/decisions/0004-handoff-notes-2026-06-25.md` 議題 (a)）
- 2026-06-25 セッション 5 でユーザー判断により議題が拡張され、(a) 料金体系合意に加え (b) 代替案検討（Connect 非経由・LocalStack / モック等での動作確認手段）が併記された
- Phase 6.2 ConnectDispatcher / 6.3 CallEndHandler / 6.4 TranscribeStarter のテンプレ実装は既に完了済（`docs/notes/_progress.md` Phase 6 = 8/8）。ユニットテストは `unittest.mock` ベースで多数 PASS（6.2: 24/24、6.3: 10/10、6.4: 13/13 + 純粋関数 25/25）しており、ローカルでの Lambda ロジック検証は既に達成されている
- 一方、実 Amazon Connect API（`StartOutboundVoiceContact`、Contact Flow からの Invoke、録音 S3 出力、Transcribe 連動）の **エンドツーエンド実機検証** は依然未達成

このため Phase 0.3 は「**実機検証は課金合意取得後に保留**」とし、代わりに「**ローカルテスト戦略（moto / boto3 stubber / unittest.mock の比較）と findings 整備**」をもって **代替案で代行** することをユーザーが選択した（2026-06-25 セッション 7 続き、Option A）。

本 ADR はその findings を記録するものである。

## 2. 決定

| 項目                                                            | 値                                                                                                                                                                            |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 0.3 の完了方式                                            | **代替案で代行（findings 整備）**                                                                                                                                             |
| Connect 関連 Lambda（6.2 / 6.3 / 6.4）の **ローカルテスト戦略** | **`unittest.mock.patch` で boto3 client を差し替える方式**（既存採用、本 ADR で確定）                                                                                         |
| `moto` の採用                                                   | **採用しない**（Connect サポートは S3 / DynamoDB 等と比べ限定的、`StartOutboundVoiceContact` の挙動模倣は不完全）                                                             |
| `boto3.stub.Stubber` の採用                                     | **将来候補として保留**（Phase 14 統合テストで `client.exceptions.LimitExceededException` 等 SDK 例外の現実的なエラーパスを確認したい場合に検討）                              |
| **実機 Amazon Connect 検証**                                    | **課金合意取得後に保留**（インスタンス購入 + DID 番号取得 + 自席電話への発信 / 着信確認の 1 回検証は、本プロジェクトの予算判断が確定し次第、別 ADR / 別タスクで切出して実施） |
| Done When 解釈                                                  | tasks.md 原文（「Outbound 発信が届き、Inbound 着信できることを 1 度確認、知見ドキュメントが作成されている」）のうち **「知見ドキュメントが作成されている」のみ達成**          |

## 3. 代替案比較（moto / boto3 stubber / unittest.mock）

本プロジェクトの Connect 関連 Lambda（Python 3.12、boto3、ADR-0001）に対する 3 つのローカルテスト手法を、Phase 6.2 / 6.3 / 6.4 のユニットテスト実装経験を踏まえて比較した。

| 観点                               | `unittest.mock.patch`                           | `boto3.stub.Stubber`                 | `moto`                                     |
| ---------------------------------- | ----------------------------------------------- | ------------------------------------ | ------------------------------------------ |
| 課金影響                           | **ゼロ**                                        | **ゼロ**                             | **ゼロ**                                   |
| Connect API サポート               | 任意（呼出元コードが期待するだけ）              | boto3 SDK が定義する全 API           | 限定的（S3 / DynamoDB 中心、Connect は薄） |
| `StartOutboundVoiceContact` の模倣 | 戻り値辞書を自由設計                            | 戻り値 + 例外を SDK スキーマで検証   | `moto.mock_aws` ではほぼ未サポート         |
| Contact Flow ↔ Lambda 連携         | event 辞書を直接組立て注入                      | Lambda event とは独立、API 呼出のみ  | 同上                                       |
| 例外パスの再現                     | 任意（`side_effect=ClientError(...)`）          | SDK 定義の例外と一致した形で生成可能 | 部分的                                     |
| テストの読みやすさ                 | 高（呼出元視点の期待値が明示）                  | 中（API レベルでの順序・引数検証）   | 低〜中（裏で何が動いてるか不透明）         |
| 保守性                             | 高（SDK バージョン非依存）                      | 中（SDK バージョン依存）             | 低〜中（moto バージョン依存 + サポート差） |
| エンドツーエンド再現性             | 低（呼出元ロジックのみ検証）                    | 中（API 呼出の正当性検証）           | 中（複数サービス連携の擬似的検証）         |
| 既存実装での採用実績               | **Phase 6.2 / 6.3 / 6.4 で多用、全テスト PASS** | 未採用                               | 未採用                                     |

このことからこう考えます：本プロジェクトの Connect 関連 Lambda は「**呼出元ロジックの正しさ**」（リトライ判定、ConditionExpression、SFN SendTaskSuccess 呼出順序、callResultCode の遷移）を検証することが主眼であり、SDK API の引数スキーマ厳密性まで都度検証する必要性は低い。`unittest.mock` の自由度・読みやすさ・保守性が最も適合する。

## 4. 推奨アプローチ（採用方針の確定）

### 4.1 Connect 関連 Lambda の **ユニットテスト**（既存採用、本 ADR で公式化）

- **手法**: `unittest.mock.patch` で `boto3.client("connect")` / `boto3.client("transcribe")` / `boto3.resource("dynamodb")` 等を差し替える
- **テストファイル配置**: `backend/tests/lambdas/<lambda_name>/test_handler.py` + `conftest.py`
- **テスト対象**: handler 本体（event 入出力、リトライ判定、ConditionExpression、SFN SendTaskSuccess 呼出順序）
- **純粋関数の分離**: `backend/shared/` 配下に純粋関数（`backoff.py`、`evaluator.py`、`finalize.py`、`s3_keys.py` 等）を切出し、Hypothesis での PBT 検証可能にする
- **mock の責務**: boto3 client / SDK 呼出のみ（純粋関数は mock しない）

### 4.2 **将来の検討候補**（必要時に追加検討）

| 候補                               | 用途                                                                                                                                                 | 検討タイミング                        |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `boto3.stub.Stubber`               | Phase 14 統合テストで SDK 例外（`LimitExceededException` / `ThrottlingException` / `InvalidParameterException`）の現実的なエラーパスを確認したい場合 | Phase 14.x 着手時                     |
| `moto.mock_aws` + S3 / DynamoDB    | Recording S3 / DynamoDB との連携を含めた擬似的なエンドツーエンドテスト（Connect 部分は依然 `unittest.mock` 必要）                                    | Phase 14 統合テスト、ユーザー判断     |
| **実 Amazon Connect インスタンス** | エンドツーエンド実機検証（Outbound 発信 / Inbound 着信 / Polly TTS / 録音 S3 / Transcribe 連動）                                                     | **課金合意取得後**（本 ADR では保留） |

### 4.3 採用しない手法とその理由

| 手法                         | 不採用理由                                                                                                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `moto` 単独で Connect 模倣   | Connect サポートが薄く、`StartOutboundVoiceContact` のリアルな挙動模倣には不向き                                                                                                                           |
| LocalStack                   | OSS 版は Connect サポートが限定的、Pro 版は有料。本プロジェクトの「課金ゼロ最優先」方針（2026-06-25 セッション 7、Option A）と合致しない                                                                   |
| 実機 Amazon Connect 早期購入 | 課金合意未取得状態での購入は本プロジェクト方針に反する。実機検証は Phase 7 ConnectDispatcher と Contact Flow の結合検証時、もしくは Phase 14 統合テスト時に課金合意取得後にまとめて 1 回実施するのが効率的 |

## 5. 既存テストパターン参照（Phase 6.2 / 6.3 / 6.4）

本 ADR が公式化する「`unittest.mock.patch` ベース」のパターンは、既に以下のテストファイルで多数の PASS 実績がある（`docs/notes/_progress.md` セッション 7 までの記録による）。

| Phase    | テストファイル                                                             | パターンの要点                                                                                                                                                                       | 件数         |
| -------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| 6.2      | `backend/tests/lambdas/connect_dispatcher/test_handler.py` + `conftest.py` | `patch("boto3.client")` で Connect client を MagicMock 差し替え、`StartOutboundVoiceContact` の戻り値辞書を組立、`ThrottlingException` を `side_effect` で注入してリトライ判定を確認 | 24/24 PASSED |
| 6.3      | `backend/tests/lambdas/call_end_handler/test_handler.py` + `conftest.py`   | DynamoDB Table の `update_item` を MagicMock 化、`ConditionalCheckFailedException` を `side_effect` で発生させて二重書込防止を検証、SFN `send_task_success` の呼出順序確認           | 10/10 PASSED |
| 6.4      | `backend/tests/lambdas/transcribe_starter/test_handler.py` + `conftest.py` | S3 EventBridge event を直接 dict として注入、Transcribe `start_transcription_job` を MagicMock 化、ジョブ命名・出力先・言語コードの正当性を検証                                      | 13/13 PASSED |
| 6.4 純粋 | `backend/tests/shared/recording/test_s3_keys.py`                           | 純粋関数 `parse_recording_key` / `derive_transcribe_job_name` を Hypothesis で property 検証（path-shape 不変条件、命名規則）                                                        | 25/25 PASSED |

このことからこう考えます：パターンは既に確立されており、Phase 7 以降の Lambda 追加・Phase 9 Inbound 系・Phase 14 統合テストでも同じ方針で書き続ければよい。新規ユニットテスト作成時の追加判断コストを抑える効果がある。

### 5.1 推奨テンプレ（`unittest.mock` パターンの骨格）

```python
# backend/tests/lambdas/<lambda_name>/conftest.py
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture
def mock_connect_client():
    """Amazon Connect client を MagicMock 差し替え"""
    client = MagicMock()
    client.start_outbound_voice_contact.return_value = {
        "ContactId": "test-contact-id-0001",
    }
    return client

@pytest.fixture
def patch_boto3_client(mock_connect_client):
    with patch("backend.lambdas.connect_dispatcher.handler.boto3.client") as mock:
        mock.return_value = mock_connect_client
        yield mock
```

```python
# backend/tests/lambdas/<lambda_name>/test_handler.py
def test_dispatch_success(patch_boto3_client, mock_connect_client):
    event = {"cycleId": "C001", "employeeId": "E001", "phoneNumber": "+819012345678", ...}
    result = handler.handler(event, None)
    mock_connect_client.start_outbound_voice_contact.assert_called_once()
    assert result["contactId"] == "test-contact-id-0001"
```

## 6. 残課題と保留事項

### 6.1 **実 Amazon Connect 検証は課金合意取得後に保留**

以下の検証は本 ADR の代替案では達成不可であり、別途実施が必要：

- 自席電話への Outbound 発信成功（`StartOutboundVoiceContact` の実 API 呼出 → DID 番号からの発呼 → 受話）
- 自席からの Inbound 着信成功（DID 番号への着信 → Inbound Contact Flow 起動 → Lambda Invoke）
- Polly TTS による音声合成と通話への乗せ込み
- 録音 S3 出力の実生成（`s3:ObjectCreated` イベントが TranscribeStarter Lambda へ届く）
- Transcribe `language-code=ja-JP` ジョブの実起動と結果 JSON の S3 出力
- 通話結果コード（RECORDED / NO_ANSWER / BUSY / VOICEMAIL）の Contact Flow からの実値配信

これらは Phase 7 ConnectDispatcher と Contact Flow の結合検証時、もしくは Phase 14 統合テスト時に、課金合意取得後にまとめて実施する。実施時は **別 ADR（仮称 ADR-0006: Amazon Connect 実機検証 findings）** として記録する。

### 6.2 `tasks.md` Phase 0.3 本文の番号不整合

`tasks.md` Phase 0.3 本文は依然「`docs/decisions/0002-connect-mock-findings.md` に記録する」と記述しているが、実際のファイル名は `0005-connect-mock-findings.md`（本 ADR）である。**この本文修正は別途のテキスト修正タスクとしてユーザー判断による**（2026-06-25 セッション 7 続き、ADR 採番ズレ確認時の Option A 採用結果）。本 ADR の存在自体は Phase 0.3 の Done When を満たすために十分とする。

### 6.3 `boto3.stub.Stubber` の Phase 14 検討

Phase 14.x 統合テスト着手時に、SDK 例外パスの現実的な再現が必要と判断された場合は `boto3.stub.Stubber` の導入を検討する。ただし既存 `unittest.mock` パターンとの併用がコード複雑度を増す懸念があるため、導入判断時には別 ADR を起こすこと。

## 7. 採用範囲・影響

### 7.1 採用範囲

| 区分                                     | 対象                                                                                                           |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Connect 関連 Lambda のユニットテスト     | `ConnectDispatcher`（6.2）、`CallEndHandler`（6.3）、`TranscribeStarter`（6.4）、`InboundHandler`（Phase 9.x） |
| テスト手法                               | `unittest.mock.patch` で boto3 client / resource を MagicMock 差し替え                                         |
| 純粋関数の検証                           | Hypothesis（PBT）、`backend/shared/` 配下に切出した純粋関数のみ                                                |
| Phase 7 着手前のテレフォニーロジック検証 | 本 ADR の手法のみで充足（実機検証は課金合意取得後）                                                            |

### 7.2 採用範囲外

- `moto` の導入（本 ADR で不採用確定）
- LocalStack の導入（本 ADR で不採用確定）
- 実機 Amazon Connect インスタンスの先行購入（本 ADR で保留、課金合意取得後に別タスク化）
- Connect Contact Flow 自体のテスト（Contact Flow JSON の構文検証は Phase 7.1 で別途）

### 7.3 影響を受ける後続タスク

- **Phase 7.1〜7.4（テレフォニー）**: 本 ADR のテスト方針に従う。Outbound Contact Flow JSON は構文検証のみ、Connect ↔ Lambda 結合検証は **課金合意取得後** に保留
- **Phase 8.x（音声処理）**: Transcribe ジョブの実機検証は 6.4 と同様、課金合意取得後
- **Phase 9.x（インバウンド）**: InboundHandler は本 ADR の `unittest.mock` パターンで実装
- **Phase 13.x（PBT）**: 純粋関数は Hypothesis で網羅検証（既に多数完了）
- **Phase 14.x（統合 / 性能テスト）**: 必要に応じ `boto3.stub.Stubber` 導入の判断（別 ADR 起票）
- **Phase 0.3 の grill-me（2026-06-25 セッション 5 で議題拡張済）**: 本 ADR をもって代替案完了。料金体系合意の grill-me 自体は将来別途実施（ユーザー判断者）

## 8. 参照

- `tasks.md` / Phase 0.3、Phase 6.2、Phase 6.3、Phase 6.4
- `requirements.md` / Requirement 5.1, 13.1（Connect Outbound / Inbound）
- `design.md` / Connect_Caller、Inbound_Handler、Voice_Transcriber
- `docs/decisions/0001-runtime-selection.md`（Python 3.12 + boto3 + Hypothesis 採用）
- `docs/decisions/0004-handoff-notes-2026-06-25.md` / 議題範囲拡張（料金合意 + 代替案検討）
- `docs/notes/_progress.md` / Phase 6 完了状況、Phase 13 PBT 進捗
- `backend/tests/lambdas/connect_dispatcher/`、`backend/tests/lambdas/call_end_handler/`、`backend/tests/lambdas/transcribe_starter/`（既存テスト実装）
- AWS 公式 — boto3 Stubber: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#examples
- AWS 公式 — Amazon Connect API: https://docs.aws.amazon.com/connect/latest/APIReference/welcome.html
- moto GitHub: https://github.com/getmoto/moto
