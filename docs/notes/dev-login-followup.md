# dev環境ログインセットアップ 暫定対処と残作業

- 記録日: 2026-06-26
- 対応者: tomita（ログインユーザー作成・ログイン動作確認）
- 関連環境: dev（Stack `safety-confirmation-dev`、Account 214046906694、ap-northeast-1）

---

## 1. 経緯

別資料作成のためdev環境にログインして動作確認を行いたく、CloudFrontで配信される管理画面（https://dn8bulnup9krf.cloudfront.net）からのログインを試みた。

以下を順に実施し、最終的にログイン成功までこぎつけた:

1. Cognito User Pool にユーザー `tomita@g-wise.co.jp` を新規作成し、Administratorグループに追加
2. CloudFront配信用S3バケットが空だったため、`frontend/` で `.env.local` を作成 → `npm run build` → `aws s3 sync` → CloudFront invalidation を実施
3. `FORCE_CHANGE_PASSWORD` 状態のままだとSPAが初回変更チャレンジを処理できず「ログインに失敗しました」となったため、`admin-set-user-password --permanent` で `CONFIRMED` 化
4. それでもCognitoが400を返すため、F12開発者ツールのNetworkタブで真因を確認したところ、**PostAuthentication Lambdaの import エラー** が判明
5. PostAuthentication Trigger を User Pool から一時的に外して回避 → ログイン成功

---

## 2. 暫定対処の現状（dev環境のみ）

以下はdev環境にのみ適用されている **暫定設定**。本来の設計仕様からは外れているため、根本対応のうえ復元すること。

### 2.1 Cognito User Pool

- **PostAuthentication Trigger が外れている** → **2026-06-27 再アタッチ済（tasks.md 15.20、セッション 21）**
  - 元: `arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-post-auth-dev`
  - 影響（当時）: ログイン成功時の監査ログ（AUTH_SUCCESS）がAuditLogGroupに書き込まれない。LockoutTableのfailedAtsがクリアされず、5回失敗 + 30分のロックアウト仕様が正しく機能しない
  - 復元実施: 2026-06-27、`aws cognito-idp update-user-pool --lambda-config "PreSignUp=...,PreAuthentication=...,PostAuthentication=..."` 実行。`describe-user-pool` で 3 Trigger 復活確認済、CFn template.yaml L1031-1035 定義と再同期（drift 解消）。詳細は `docs/notes/15-20-postauth-import-fix.md` §5.2 参照

### 2.2 検証用ログインユーザー

- Username: `tomita@g-wise.co.jp`
- 表示名: `tomita`
- パスワード: 永続設定済（`--permanent`、本来仕様の「初回変更強制」を回避）
- 状態: `CONFIRMED`
- グループ: `Administrator`
- パスワード値: 別途口頭/共有メモを参照（本ファイルには記載しない）

---

## 3. 根本対応すべき残作業

### 残作業 ① PostAuth Lambda の `shared.audit.logger` import失敗 — **完了（2026-06-27、tasks.md 15.20、セッション 21）**

**完了サマリ**: 真因は当初仮説（PostAuth Lambda 特有問題）と異なり、**過去の SharedLayer リビルド（推定: 15.2a placeholder deploy セッション 19/20）で Layer 9 が新規作成された時点で import エラーは自動解消されていた**（Case D）。残っていたのは Cognito User Pool への PostAuthentication Trigger 再アタッチのみ。`aws lambda invoke` で StatusCode=200, AuditLogGroup へ AUTH_SUCCESS 書込確認、Trigger 再アタッチ実施で復元完了。詳細は `docs/notes/15-20-postauth-import-fix.md`。

---

**当初の症状**:

```
UserLambdaValidationException: PostAuthentication failed with error
Unable to import module 'handler': No module named 'shared.audit.logger'.
```

**仮説**:
Phase 12.3 で `backend/shared/audit/logger.py` を新規作成し、PostAuth Lambda の handler に組み込んだが、Lambda Layer（SharedLayer）の中身に `shared/audit/` パッケージが含まれていない、または PostAuth Lambda が新Layerバージョンを指せていない可能性。

**調査・対応手順**:

1. SharedLayer ビルドスクリプト（`infrastructure/build/layers/shared/`）を確認し、`backend/shared/audit/` 配下をLayerに含めているかを検証
2. ビルド済みLayerアーティファクトの中身を解凍して `shared/audit/logger.py` が存在するかを直接確認
3. PostAuth Lambda が指している Layer ARN（バージョン番号）と、SharedLayerリソースの最新バージョン番号を `aws lambda get-function-configuration` / `aws lambda list-layer-versions` で照合
4. 不整合があればLayerをリビルドして再デプロイ、Lambdaを更新
5. ローカルで `pytest tests/lambdas/auth_post_auth/` を実行して回帰確認

**完了条件**:

- `aws lambda invoke --function-name safety-confirmation-auth-post-auth-dev --payload <Cognito Post Auth テストペイロード>` で正常終了 ✅
- User Pool に PostAuthentication Trigger を再アタッチ ✅
- ログイン成功時にAuditLogGroupに `AUTH_SUCCESS` イベントが書き込まれることを確認 ✅（lambda invoke 経由で確認、SPA 経由 E2E は元タスク 15.6 委譲）

**完了マーク**: ✅ **2026-06-27 達成**（`docs/notes/15-20-postauth-import-fix.md` §6）

### 残作業 ② SPA側のSRP実装の再検証（優先度: 中、本タスク 15.20 時点では部分対応済）

**観測された問題**:

- `FORCE_CHANGE_PASSWORD` 状態のユーザーがログインを試みると、SPAは「ログインに失敗しました」の汎用メッセージを表示
- 本来 `newPasswordRequired` チャレンジを `NewPasswordRequiredError` として捕捉し「初期パスワードからの変更が必要です」と案内するはずだが、汎用エラー分岐に落ちている

**調査・対応手順**:

1. `frontend/src/auth/cognitoAuthProvider.ts` の `newPasswordRequired` コールバック実装を確認
2. amazon-cognito-identity-js 6.3.12 の `FORCE_CHANGE_PASSWORD` 状態への挙動を整理
3. ローカルで `FORCE_CHANGE_PASSWORD` 状態のテストユーザーを作って手動再現
4. 必要に応じてエラー分岐の見直し or E2E テスト追加

### 残作業 ③ dev環境の本番相当性の回復

①②が完了したら以下を実施:

- tomita ユーザーを `--permanent` ではなく初回変更強制状態に戻すか、または新規ユーザーを `FORCE_CHANGE_PASSWORD` 状態で作成して動作確認
- PostAuthentication Trigger 再アタッチ後、ログイン → AuditLogGroup書込 → LockoutTable failedAtsクリア の3点が動くことを実機検証
- 必要なら本ファイルを更新 or アーカイブ

**現状（2026-06-27 セッション 21、tasks.md 15.20 完了時点）**:

- (a) tomita ユーザーの `--permanent` 設定はそのまま残置（本番化時に再生成想定、本タスクスコープ外）
- (b) PostAuthentication Trigger は **再アタッチ済**（§2.1 / §3 ①）
- (c) AuditLogGroup 書込 / LockoutTable failedAts クリアの 3 点動作は `lambda invoke` ベースで論理的に確認済（`docs/notes/15-20-postauth-import-fix.md` §3.2.5〜§3.2.7）。**SPA 経由 E2E は元タスク 15.6（実機受入テスト）に委譲**（Connect 依存範囲とまとめて実施）

---

## 4. 参考: 今回使用したAWS CLI操作

```powershell
# ユーザー作成
aws cognito-idp admin-create-user `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username tomita@g-wise.co.jp `
  --user-attributes Name=email,Value=tomita@g-wise.co.jp Name=email_verified,Value=true Name=name,Value=tomita `
  --temporary-password "<仮パスワード>" `
  --message-action SUPPRESS `
  --profile AWS-security-check --region ap-northeast-1

# Administratorグループ追加
aws cognito-idp admin-add-user-to-group `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username tomita@g-wise.co.jp `
  --group-name Administrator `
  --profile AWS-security-check --region ap-northeast-1

# 永続パスワード設定（暫定対処、本来は --permanent を使わない）
aws cognito-idp admin-set-user-password `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username tomita@g-wise.co.jp `
  --password "<新パスワード>" `
  --permanent `
  --profile AWS-security-check --region ap-northeast-1

# PostAuth Trigger を外す（暫定対処）
aws cognito-idp update-user-pool `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --lambda-config "PreSignUp=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-signup-dev,PreAuthentication=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-auth-dev" `
  --profile AWS-security-check --region ap-northeast-1

# PostAuth Trigger を戻す（根本対応完了後）
aws cognito-idp update-user-pool `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --lambda-config "PreSignUp=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-signup-dev,PreAuthentication=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-auth-dev,PostAuthentication=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-post-auth-dev" `
  --profile AWS-security-check --region ap-northeast-1

# SPAビルド・アップロード
cd C:\_oka\a_資格関係\1_AWS\1_AS部\kiro\frontend
npm run build
aws s3 sync dist/ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete `
  --profile AWS-security-check --region ap-northeast-1
aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" `
  --profile AWS-security-check
```

---

## 5. 同内容の控え

本ファイルと同内容を以下にも置いている（一方が消えたとき用の冗長化）:

`C:\_oka\1_DK-SISリニューアル開発\1_開発\claude\2026\6\25\2_kiroに作らせているものの解説\残作業メモ.md`
