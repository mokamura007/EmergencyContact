# Phase 15.2a — CORS 対応漏れ修正 完了報告

## 背景

Phase 5.7 で 24 個の API Gateway Method を追加し ApiDeployment / ApiStage を組んだ際、
SPA（CloudFront 配信）から呼ぶ際に必要な以下 3 点が漏れていた。

1. Lambda 応答ヘッダに `Access-Control-Allow-*` が無い → ブラウザが応答を破棄
2. OPTIONS preflight が API Gateway に未定義 → preflight 自体が 4xx で落ちる
3. API Gateway の `GatewayResponse`（401 / 4xx / 5xx）も CORS ヘッダを持たない →
   Cognito Authorizer の弾き返しや内部エラー時にブラウザが詳細を読めない

採用案：**C（DRY 共通モジュール + 6 handler 一括改修 + CFn フル CORS 対応）**

## 実施項目チェックリスト

| Step | 内容                                                                           | 結果                                                      |
| ---- | ------------------------------------------------------------------------------ | --------------------------------------------------------- |
| 1    | `backend/shared/api/cors.py` + 単体テスト 5 件新設                             | ✓ 5/5 PASS                                                |
| 2    | 6 handler の `_response` を `with_cors_headers()` 経由に差替                   | ✓ 完了                                                    |
| 3    | 既存 handler テストの期待値更新                                                | 対象ゼロ（既存テストは headers 中身を検証していなかった） |
| 4    | CFn template.yaml に 18 OPTIONS + 3 GatewayResponse + ApiDeploymentV2Cors 追加 | ✓ 完了                                                    |
| 5    | `validate.ps1` cfn-lint                                                        | ✓ ExitCode=0、新規 warning なし                           |
| 6    | `deploy.ps1 -EnvironmentName dev`                                              | ✓ Stack `safety-confirmation-dev` UPDATE_COMPLETE         |
| 7    | curl OPTIONS preflight 動作確認                                                | ✓ 10/10 endpoint で 200 + 3 ヘッダ揃い                    |
| 8    | backend / frontend 既存テスト回帰                                              | ✓ backend 877/877、frontend 270/270                       |
| 9    | 本ノート作成                                                                   | ✓ 完了                                                    |

## 変更ファイル一覧

### 追加

- `backend/shared/api/__init__.py`
- `backend/shared/api/cors.py`
- `backend/tests/shared/api/__init__.py`
- `backend/tests/shared/api/test_cors.py`
- `docs/notes/15-2a-cors-fix.md`（本ファイル）

### 修正

- `backend/lambdas/dictionary_api/handler.py`（import + `_response`）
- `backend/lambdas/cycle_api/handler.py`（import + `_response`）
- `backend/lambdas/employee_api/handler.py`（import + `_response`）
- `backend/lambdas/response_api/handler.py`（import + `_response`）
- `backend/lambdas/recording_api/handler.py`（import + `_response`）
- `backend/lambdas/auth_failure_reporter/handler.py`（import + `_response`）
- `infrastructure/template.yaml`（+18 OPTIONS Method、+3 GatewayResponse、+ApiDeploymentV2Cors、ApiStage の DeploymentId 差替）
- `infrastructure/packaged-template.yaml`（aws cloudformation package の出力）

### 削除

- なし（既存 `ApiDeployment` は rollback 用に残置）

## 累積テスト件数差分

| 区分            | 修正前 | 修正後 | 差分 | 内訳                                      |
| --------------- | ------ | ------ | ---- | ----------------------------------------- |
| backend pytest  | 872    | 877    | +5   | shared/api/cors の新規ユニットテスト 5 件 |
| frontend vitest | 270    | 270    | 0    | 変化なし                                  |

全件 green、回帰失敗ゼロ。

## cfn-lint 結果

- ExitCode: 0
- 出力行数: 0 行（W2001 / W3002 / W3037 / W8001 は `.cfnlintrc` で suppress 済、新規 warning なし）

## Stack ステータス

- StackName: `safety-confirmation-dev`
- StackStatus: `UPDATE_COMPLETE`
- LastUpdatedTime: 2026-06-27T13:16:04Z (UTC)
- Region: ap-northeast-1
- Account: 214046906694

## CORS preflight curl 結果サマリ

検証コマンド形式：

```pwsh
curl.exe -X OPTIONS `
  -H "Origin: https://dn8bulnup9krf.cloudfront.net" `
  -H "Access-Control-Request-Method: GET" `
  -H "Access-Control-Request-Headers: Authorization,Content-Type" `
  -D - https://bev0uk24s0.execute-api.ap-northeast-1.amazonaws.com/dev/<path>
```

10 endpoint 検証結果（全 200 + ヘッダ 3 種：`Access-Control-Allow-Origin: *` /
`Access-Control-Allow-Headers: Content-Type,Authorization,X-Idempotency-Key` /
`Access-Control-Allow-Methods: GET,POST,PUT,DELETE,PATCH,OPTIONS`）：

| #   | path                             | HTTP | Allow-Origin |
| --- | -------------------------------- | ---- | ------------ |
| 1   | `/keyword-dictionary`            | 200  | `*`          |
| 2   | `/cycles`                        | 200  | `*`          |
| 3   | `/employees`                     | 200  | `*`          |
| 4   | `/auth/record-failure`           | 200  | `*`          |
| 5   | `/employees/import`              | 200  | `*`          |
| 6   | `/keyword-dictionary/version`    | 200  | `*`          |
| 7   | `/keyword-dictionary/SAFE/test`  | 200  | `*`          |
| 8   | `/cycles/abc123`                 | 200  | `*`          |
| 9   | `/cycles/abc123/status`          | 200  | `*`          |
| 10  | `/inbound/contact-xyz/recording` | 200  | `*`          |

## 設計上の判断記録

1. **`shared/api/cors.py` の `Allow-Origin` 既定値**
   - 開発期は `"*"`。将来本番運用時に CloudFront / カスタムドメインへ絞る前提。
   - 切替手段：Lambda 環境変数 `CORS_ALLOWED_ORIGIN` を設定すれば差し替わる
     （SAM/CFn 側で `Environment.Variables.CORS_ALLOWED_ORIGIN` を渡すだけ）。
   - CFn の OPTIONS Mock と GatewayResponse も同じく `'*'` でハードコード中。
     本番では template 側も連動して書き換える必要あり（本タスクスコープ外）。

2. **既存 `ApiDeployment` の残置**
   - 新 `ApiDeploymentV2Cors` で `ApiStage.DeploymentId` を差し替えた。
   - 障害時は ApiStage の参照を `!Ref ApiDeployment` に戻すだけで Phase 5.7 時点に
     即時ロールバック可能。今後 Method を追加する際に 3 個目以降の Deployment を作る
     ルールを継続する。

3. **既存テスト破壊なし**
   - handler 系テストは `response["statusCode"]` と `json.loads(response["body"])`
     しか見ておらず、`response["headers"]` のキー集合を比較していなかった。
     そのため Step 3 は 0 件の更新で済んだ。

## ユーザー手動確認依頼

以下、CLI / curl では完結しないブラウザ越し検証をお願いします。

1. **SPA から実 API 呼び出し**
   - https://dn8bulnup9krf.cloudfront.net にログイン
   - サイクル一覧 / 従業員一覧 / キーワード辞書ページを開く
   - DevTools の Network タブで `OPTIONS → 200`、続く `GET → 200` の 2 段で動くことを確認

2. **401 応答時の CORS ヘッダ**
   - 期限切れ等で Cognito 401 になる導線（再現可能であれば）でエラーメッセージが
     ブラウザに正しく表示されること

3. **CORS_ALLOWED_ORIGIN の本番絞り込みタイミング**
   - 本番リリース前に Lambda 環境変数と CFn の `Allow-Origin` リテラルを
     CloudFront ドメインに絞り込む対応をどの Phase に積むか方針決定（提案：Phase 12.2 系）

何かエラー / 違和感あればロールバック手順（ApiStage の DeploymentId を `!Ref ApiDeployment` へ戻して redeploy）を案内します。
