# 安否確認システム（Safety Confirmation System）

`.kiro/specs/safety-confirmation-system/` で要件・設計・実装計画が確定した、AWS Lambda + Amazon Connect ベースの安否確認システム。

## ステータス

実装フェーズ進行中。Phase 1（IaC）〜 Phase 13（PBT 実装）まで完了。Phase 0.3（Connect mock 実機検証）は課金合意（ADR-0005 / ADR-0009 §3）保留中で findings 整備で代行済。残作業は Phase 14（統合 / スモーク / 性能テスト、実 Connect 通話を伴う）と Phase 15（デプロイ運用、ADR-0009 §3 連動タスク群）。SPA は共有 dev 環境（`safety-confirmation-spa-dev-*` S3 + CloudFront）に配信稼働中。詳細は `.kiro/specs/safety-confirmation-system/tasks.md`。

## ディレクトリ構成

```
.
├── infrastructure/   # CloudFormation テンプレート、Contact Flow JSON 等(Phase 1 以降)
├── backend/          # Lambda 関数群と共通コード(Phase 5 以降に充実)
│   ├── pyproject.toml
│   ├── lambdas/      # Lambda ごとのパッケージ
│   └── shared/       # Lambda Layer 用共通コード
├── frontend/         # 管理者向け SPA (React + TypeScript, Phase 10)
├── docs/             # ドキュメンテーション
│   └── decisions/    # ADR (0001-runtime-selection.md ほか)
├── kiro/             # 進捗ノートおよび要件整理ドラフトの保管(コピー運用は廃止、進捗ノートのみ随時更新)
└── .kiro/            # Kiro spec(正式な要件・設計・タスク)
```

## 前提

| 項目           | 値                                                                   |
| -------------- | -------------------------------------------------------------------- |
| 言語           | Python 3.12                                                          |
| 依存管理       | [uv](https://docs.astral.sh/uv/getting-started/installation/)        |
| Lambda Runtime | python3.12 / arm64                                                   |
| AWS CLI        | プロファイル `AWS-security-check` を `~/.aws/credentials` に設定済み |
| OS             | Windows / macOS / Linux 全対応                                       |

## 開発環境構築

```powershell
# 1. uv をインストール(未導入の場合)
#    https://docs.astral.sh/uv/getting-started/installation/

# 2. backend ディレクトリで依存同期(仮想環境作成 + 依存導入)
cd backend
uv sync

# 3. 動作確認
uv run python --version    # Python 3.12.x
uv run ruff --version      # ruff 0.6.x 以上
```

## 開発フロー

dev 環境は**単一スタック共有**。共有 dev を汚す前にローカルで最大限確認する運用。

### 1. ローカル確認

| 変更対象         | コマンド                                                                     |
| ---------------- | ---------------------------------------------------------------------------- |
| backend Lambda   | `cd backend; uv run pytest` (handler 直接呼出 + boto3 stubber + Hypothesis)  |
| frontend SPA     | `cd frontend; npm run dev` (http://localhost:5173、dev API を叩く)           |
| CFn テンプレート | `.\.venv\Scripts\cfn-lint.exe ..\infrastructure\template.yaml` (課題 2 参照) |

SAM local / LocalStack 等の統合ローカル起動口は**意図的に未整備**。統合検証は下記の dev デプロイで実施する。

### 2. git push

品質チェック（後述）通過後、`git push origin develop`。

### 3. dev 環境への反映（変更内容に応じて選択）

- **backend / CFn 変更**: `pwsh -File scripts/deploy_dev.ps1`（Lambda Layer build + CFn package + deploy）
- **frontend 変更**: 以下 3 コマンドを順に実行

  ```powershell
  cd frontend
  npm run build
  aws s3 sync .\dist\ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete --profile AWS-security-check --region ap-northeast-1
  aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" --profile AWS-security-check
  ```

- **両方の変更**: 上記を両方実行

### 共有 dev 運用の注意

- 複数人が同時にデプロイすると後勝ちでコード上書き / DynamoDB 状態干渉が起きる。事前調整を推奨
- CloudFront invalidation を忘れると古いバンドルが配信され続ける。`create-invalidation` の Status が `Completed` になるまで数分待つ
- backend の pytest は「共有 dev を汚す前の防波堤」として機能する設計。push 前に必ず通す

## 品質チェック(手動コマンド)

CI ホスティングを使用しないため、ローカルで手動実行する。

```powershell
cd backend

# Lint
uv run ruff check .

# Format チェック(書き換えはしない、確認のみ)
uv run black --check .

# 型検査
uv run mypy .

# テスト(テスト追加後)
uv run pytest
```

すべて 0 件エラーで完了すれば品質基準を満たす。
書き込み(自動修正)を行う場合は以下：

```powershell
uv run ruff check . --fix
uv run black .
```

## Windows 運用上の注意点

Windows 環境（PowerShell 7 / AWS CLI for Windows 2.12.6 / uv 0.9.30）で確認した既知の運用課題を記載する。新セッション・新メンバー参画時の再ハマり防止用。

### 課題1: AWS CLI for Windows の `file://` UTF-8 デコードエラー

CloudFormation テンプレート（`infrastructure/template.yaml`）に日本語コメントが含まれる場合、`--template-body file://...` 経由の検証が UTF-8 デコード失敗で動作しない。

**症状**:

```
aws cloudformation validate-template --template-body file://infrastructure/template.yaml ...
→ Error: Unable to load paramfile, text contents could not be decoded
```

**回避策（動作確認済み）**: PowerShell の `Get-Content -Raw -Encoding UTF8` で読み込んだ内容を直接渡す。

```powershell
$body = Get-Content infrastructure/template.yaml -Raw -Encoding UTF8
aws cloudformation validate-template --template-body $body --profile AWS-security-check --region ap-northeast-1
```

恒久対策候補としては、CFn テンプレートのコメントを英語に統一する方法がある。

### 課題2: `uv run cfn-lint` が `program not found`

`backend/.venv\Scripts\cfn-lint.exe` 自体は存在するにもかかわらず、`uv run cfn-lint` 経由で発見されない（Windows + uv 0.9.30 の挙動）。

**症状**:

```
uv run cfn-lint ../infrastructure/template.yaml
→ error: Failed to spawn: cfn-lint, Caused by: program not found
```

**回避策（動作確認済み）**: 仮想環境内の実行ファイルを直接呼び出す。

```powershell
.\.venv\Scripts\cfn-lint.exe ..\infrastructure\template.yaml
```

なお、`python -m cfnlint` も使用不可（`__main__` を持たないパッケージのため）。

## AWS 認証情報

AWS CLI のプロファイル `AWS-security-check` を使用する。
コマンド実行時は環境変数 `AWS_PROFILE=AWS-security-check` を設定するか、`--profile AWS-security-check` を付与する。

```powershell
$env:AWS_PROFILE = "AWS-security-check"
aws sts get-caller-identity   # 動作確認
```

## 関連ドキュメント

| 種別               | パス                                                     |
| ------------------ | -------------------------------------------------------- |
| 要件               | `.kiro/specs/safety-confirmation-system/requirements.md` |
| 設計               | `.kiro/specs/safety-confirmation-system/design.md`       |
| 実装計画           | `.kiro/specs/safety-confirmation-system/tasks.md`        |
| ランタイム選定 ADR | `docs/decisions/0001-runtime-selection.md`               |

## ライセンス

社内システム。社外公開しない。
