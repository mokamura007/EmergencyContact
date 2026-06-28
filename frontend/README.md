# 安否確認システム フロントエンド SPA

管理者専用の Single Page Application。React + TypeScript + Vite で構築する。

- 仕様: `.kiro/specs/safety-confirmation-system/`
  - design.md `Status_Viewer / 構成` および `SPA 配信用 S3`
  - tasks.md Phase 10
- 関連 ADR: `docs/decisions/0001-runtime-selection.md`（バックエンドは Python 3.12、本フロントは別系統）

## 前提

| 項目         | バージョン                                       |
| ------------ | ------------------------------------------------ |
| Node.js      | 20.x（LTS）                                      |
| npm          | 10.x                                             |
| TypeScript   | 5.6.x（`strict`、`noUncheckedIndexedAccess` ON） |
| ビルドツール | Vite 5.4.x + `@vitejs/plugin-react`              |
| Lint         | ESLint 9.x（flat config、`strictTypeChecked`）   |
| Formatter    | Prettier 3.3.x                                   |

## セットアップ

```pwsh
cd frontend
npm install
Copy-Item .env.example .env.local   # 値は dev 環境のものを入れる
```

## スクリプト

| コマンド               | 用途                                        |
| ---------------------- | ------------------------------------------- |
| `npm run dev`          | ローカル開発サーバ（http://localhost:5173） |
| `npm run build`        | 型チェック + 本番ビルド（`dist/` へ出力）   |
| `npm run preview`      | ビルド済アセットのプレビュー                |
| `npm run lint`         | ESLint 実行                                 |
| `npm run lint:fix`     | ESLint 自動修正                             |
| `npm run format`       | Prettier 整形                               |
| `npm run format:check` | Prettier チェックのみ                       |
| `npm run typecheck`    | `tsc -b --noEmit`                           |

## 環境変数

すべて Vite 規約に従い `VITE_` プレフィックスを必須とする（バンドルに埋め込まれる）。

| 変数名                      | 必須 | 用途                                             |
| --------------------------- | ---- | ------------------------------------------------ |
| `VITE_API_BASE_URL`         | 必須 | API Gateway ステージ URL（末尾 `/` なし）        |
| `VITE_COGNITO_USER_POOL_ID` | 必須 | Cognito User Pool ID                             |
| `VITE_COGNITO_CLIENT_ID`    | 必須 | Cognito App Client ID（SPA、Client Secret 無し） |
| `VITE_AWS_REGION`           | 任意 | 既定 `ap-northeast-1`（NFR5 リージョン固定）     |

`.env.example` を雛形としてコピーし、`.env.local`（git 管理対象外）に実値を記入する。

## 出力（Done When）

`npm run build` 成功時、`frontend/dist/` 配下に静的アセット（`index.html` + `assets/*`）が生成される。生成物は Phase 11 で CloudFront + `SpaBucket`（design.md `SPA 配信用 S3`）へデプロイする。
