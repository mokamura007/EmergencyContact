# ADR-0001: 実装ランタイム選定

- ステータス: Accepted
- 決定日: 2026-06-19
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md`, `.../design.md`, `.../tasks.md`（Phase 0.1）

## コンテキスト

本プロジェクト「安否確認システム」は AWS Lambda を中心とするサーバレス構成で実装される。`design.md` / **Lambda 関数一覧** では 16 個の Lambda 関数（`EmployeeApi`, `CycleApi`, `ResponseApi`, `RecordingApi`, `DictionaryApi`, `LoadTargets`, `ConnectDispatcher`, `CallEndHandler`, `TranscribeStarter`, `KeywordMatcher`, `RetryEvaluator`, `CycleFinalizer`, `RecordingMetadataWriter`, `InboundHandler`, `AuthPreAuth`, `AuthPostAuth`）について次のように規定されている：

> すべての Lambda は Node.js 20.x または Python 3.12（Phase 0 で確定）、ARM64、メモリ 512 MB を既定とし、I/O ヘビーな Lambda（CSV インポート等）のみ 1024 MB を割当する。

`design.md` / **PBT 設定** では：

> 言語・ライブラリ：Phase 0 で実装言語を確定する。Lambda が TypeScript の場合は `fast-check`、Python の場合は `Hypothesis` を採用。19 原則(a) DRY に従い、ライブラリは確立されたものを使用しゼロから実装しない。

`tasks.md` / Phase 0.1 の Done When は「ADR 文書が承認され、Lambda の Runtime 選定が確定。Phase 1 以降のすべての Lambda タスクで採用言語が一意に参照可能になる」とされる。

## 決定

| 項目               | 値                                       |
| ------------------ | ---------------------------------------- |
| 言語               | **Python 3.12**                          |
| AWS Lambda Runtime | `python3.12`                             |
| アーキテクチャ     | `arm64`                                  |
| AWS SDK            | `boto3`（最新安定版）                    |
| PBT ライブラリ     | `Hypothesis`（最新安定版）               |
| 静的型検査         | `mypy`（strict モード）                  |
| Lint               | `Ruff`                                   |
| Formatter          | `Black`                                  |
| 依存管理           | `uv` または `poetry`（Phase 0.2 で確定） |
| メモリ既定         | 512 MB（CSV インポート系のみ 1024 MB）   |

## 選定根拠

### Python 3.12 + Hypothesis を採用した主因

1. **PBT のステートフル戦略の親和性**：Hypothesis の `RuleBasedStateMachine` は Property 12（再発信判定）、Property 13（再発信間隔）、Property 17（タイムアウト）、Property 18（ポーリング状態機械）など、状態遷移検証で強い
2. **AWS SDK `boto3` の成熟度**：例外設計・ドキュメント・運用実績が豊富。Amazon Transcribe・DynamoDB・Step Functions・Connect の各 SDK インタフェースが安定
3. **静的型 + Hypothesis の組合せ**：型ヒント + `@given` による型ベース property 自動生成が可能
4. **ユーザー方針**：grill-me 段階で Python 3.12 + Hypothesis を選択

### 却下案：Node.js 20.x + fast-check

採用しなかった主因：

- PBT のステートフル戦略は `fast-check` の `fc.commands` で表現可能だが、Hypothesis の `RuleBasedStateMachine` ほど成熟していない
- AWS SDK の成熟度では `boto3` が優位

参考として認識した利点（採用しなかったが）：

- AWS 公式ブログ / サンプルコードは Amazon Connect 関連で Node.js が多い
- コールドスタートが Python より軽量（同条件で 500ms 程度の差）

## 結果（トレードオフ）

### ポジティブ

- Hypothesis による Property 12 / 13 / 17 / 18 のステートフル検証が記述容易
- `boto3` のエラーハンドリング厚く、AWS API 呼出の堅牢性が確保される
- 型ヒント + `mypy --strict` で型安全
- `Ruff` + `Black` でコードスタイル統一

### ネガティブ / リスク

- **コールドスタート**：Python 3.12 は Node.js 20.x より遅い傾向。NFR1（30 分以内に発信開始）への影響は限定的だが、API Gateway 経由の同期呼出（`CycleApi.StartExecution` 等）でユーザー知覚遅延の可能性
  - 緩和策：Provisioned Concurrency または Lambda SnapStart（Python 対応済の場合）を Phase 12 で計測のうえ判定
- **Connect Contact Flow Lambda の Python サンプル不足**：AWS 公式サンプルが Node.js 中心
  - 緩和策：Phase 0.3 の Connect mock 試作で Python 実装の動作検証を最優先。重大な障害が発覚した場合、本 ADR の Status を `Superseded` にして言語切替の再 ADR を作成する逆走可能性を担保

## 採用範囲

| 区分                           | 対象                                                                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lambda（API）                  | `EmployeeApi`, `CycleApi`, `ResponseApi`, `RecordingApi`, `DictionaryApi`                                                                                  |
| Lambda（オーケストレーション） | `LoadTargets`, `ConnectDispatcher`, `CallEndHandler`, `TranscribeStarter`, `KeywordMatcher`, `RetryEvaluator`, `CycleFinalizer`, `RecordingMetadataWriter` |
| Lambda（インバウンド）         | `InboundHandler`                                                                                                                                           |
| Lambda（認証 Cognito Trigger） | `AuthPreAuth`, `AuthPostAuth`                                                                                                                              |
| PBT テストスイート             | Property 1〜25 全件                                                                                                                                        |
| 運用 / バッチスクリプト        | 将来追加されるもの                                                                                                                                         |

### 採用範囲外

- フロント SPA：React + TypeScript（Phase 10 で別途）
- IaC：CloudFormation YAML（言語選定対象外）
- Amazon Connect Contact Flow：Connect 独自フォーマット JSON

## 影響を受ける後続タスク

`tasks.md` 内で本 ADR を前提とするタスク：

- **0.2** リポジトリ構成（`backend/` を Python パッケージとして構成、`Ruff` + `Black` 設定）
- **1.6** 共通 IAM ロール雛形（Lambda Runtime 指定）
- **Phase 4 〜 9** の全 Lambda タスク（CFn の `Runtime: python3.12`, `Architectures: [arm64]`）
- **Phase 13** PBT 実装（`hypothesis` 依存追加、`@given` ベース）
- **Phase 14** 統合 / 性能テスト（`pytest`、必要に応じ `locust` 等）
- **Phase 15** デプロイ / ドキュメント

## 参照

- `requirements.md` / NFR6（利用前提 AWS サービス）
- `design.md` / Lambda 関数一覧
- `design.md` / Testing Strategy / PBT 設定
- `tasks.md` / Phase 0.1
- Hypothesis 公式ドキュメント（https://hypothesis.readthedocs.io/）
- boto3 公式ドキュメント（https://boto3.amazonaws.com/v1/documentation/api/latest/index.html）
