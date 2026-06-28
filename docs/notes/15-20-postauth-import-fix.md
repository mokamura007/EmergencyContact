# Task 15.20 — PostAuth Lambda `shared.audit.logger` import 復旧（dev-login-followup §3 残作業 ① 別タスク化分）

- 完了日: 2026-06-27（セッション 21、tasks.md 15.20）
- 対応者: kiro（subagent、orchestrator 指示「計画承認なしで進めて + AI 推奨案採用」配下）
- スコープ: dev 環境（Stack `safety-confirmation-dev`、Account 214046906694、ap-northeast-1）
- Connect 依存: なし（Bii 方針継続、ADR-0009 §3 実施前でも実施可能タスクとして整理されていた）

---

## 1. タスク背景（tasks.md 15.20 から逐語）

2026-06-26 セッションで dev 環境ログイン時に
`UserLambdaValidationException: PostAuthentication failed with error Unable to import module 'handler': No module named 'shared.audit.logger'`
が発生し、暫定対処として **Cognito User Pool から PostAuthentication Trigger を一時的に外した状態**で運用していた（`docs/notes/dev-login-followup.md` §2.1）。

本タスクで根本対応する。

---

## 2. 当初仮説（tasks.md 15.20 / 第 17 原則対称性推論）

> 同 SharedLayer を使う他 5 Lambda（dictionary_api / employee_api / cycle_api / inbound_handler / auth_failure_reporter）が dev 環境で動作実績ありのため、SharedLayer build script 自体は正常に `shared/audit/` を含めている可能性が高い。**PostAuth Lambda が古い Layer Version を指している**（Phase 12.3 deploy 後の Lambda configuration 更新漏れ）が真因の蓋然性大

→ 仮説 A: PostAuth Lambda 特有問題（Layer Version 不整合）
→ 仮説 B: SharedLayer 全体問題（build に audit が含まれない）
→ 仮説 C: コード自体の問題（import 文 typo 等）

---

## 3. 漸進調査の生情報（第 18 原則 JIT）

### 3.1 ローカルコード確認

- `backend/shared/audit/` 配下: `__init__.py / logger.py / mask.py` 実在（list_directory 確認）
- `backend/lambdas/auth_post_auth/handler.py` L33: `from shared.audit.logger import write_audit_log`（grep 確認）
- 他 5 Lambda（dictionary_api / employee_api / cycle_api / inbound_handler / auth_failure_reporter）も同じ import 文（grep 確認）= **6 Lambda 全てが同じ import を持つ**
- `scripts/build_layer.ps1`: `backend/shared/*` を Recurse コピー → `infrastructure/build/layers/shared/python/shared/`（read_file 確認）
- `infrastructure/template.yaml` L1235 `AuthPostAuthFn` の Layers: `!Ref SharedLayer`、他 Lambda と同一参照

### 3.2 AWS 実機確認（AWS_PROFILE=AWS-security-check）

#### 3.2.1 PostAuth Lambda の Layer 参照

```
$ aws lambda get-function-configuration --function-name safety-confirmation-auth-post-auth-dev
{
  "Layers": [{"Arn": "arn:aws:lambda:ap-northeast-1:214046906694:layer:safety-confirmation-shared-dev:9", "CodeSize": 64753}],
  "LastModified": "2026-06-27T13:16:17.000+0000",
  "Runtime": "python3.12"
}
```

#### 3.2.2 SharedLayer 最新 Version

```
$ aws lambda list-layer-versions --layer-name safety-confirmation-shared-dev
[[9, "arn:aws:lambda:ap-northeast-1:214046906694:layer:safety-confirmation-shared-dev:9", "2026-06-27T13:16:12.139+0000"]]
```

→ **PostAuth Lambda は最新かつ唯一の Layer Version 9 を指している**（仮説 A: 「古い Version を指している」**反証**）
→ Lambda LastModified と Layer CreatedDate が 4 秒差 = 同一 CFn deploy（おそらく 15.2a placeholder deploy セッション 19/20 のいずれかでリビルド済）

#### 3.2.3 他 Lambda の Layer 参照（対称性検証）

```
dictionary-api-dev      → safety-confirmation-shared-dev:9
employee-api-dev        → safety-confirmation-shared-dev:9
cycle-api-dev           → safety-confirmation-shared-dev:9
inbound-handler-dev     → safety-confirmation-shared-dev:9
auth-failure-reporter   → (Layer なし、self-contained 設計、template.yaml コメント済)
auth-pre-auth-dev       → safety-confirmation-shared-dev:9
```

→ 全 Lambda が同じ Layer:9 を共有、PostAuth 固有問題ではない

#### 3.2.4 Layer 9 中身の直接確認

```
$ aws lambda get-layer-version --layer-name safety-confirmation-shared-dev --version-number 9
# Content.Location URL からダウンロード → Expand-Archive
$ Get-ChildItem $env:TEMP\layer9\python\shared\audit
Name        Length
----        ------
__init__.py     47
logger.py     6879
mask.py       1567
```

→ **Layer 9 には `python/shared/audit/logger.py`（6879 byte）が含まれている**（仮説 B: 「build に audit が含まれない」**反証**）

#### 3.2.5 PostAuth Lambda 実 invoke

```
$ aws lambda invoke --function-name safety-confirmation-auth-post-auth-dev --payload <Cognito PostAuth テストペイロード>
{
  "StatusCode": 200,
  "ExecutedVersion": "$LATEST"
}
# response.json:
{"userName": "tomita@g-wise.co.jp", "userPoolId": "ap-northeast-1_5uYfaQMLJ",
 "request": {"userAttributes": {"email": "tomita@g-wise.co.jp"}}, "response": {}}
```

→ **import エラー一切なし、handler 正常完走、event そのまま return**（仮説 C: コード問題 **反証**）

#### 3.2.6 CloudWatch Logs（直近 10 分）

```
$ aws logs filter-log-events --log-group-name /aws/lambda/safety-confirmation-auth-post-auth-dev
INIT_START Runtime Version: python:3.12.mainlinev2.v11
[INFO] Found credentials in environment variables.
START RequestId: e3119ee4-8eda-4824-8fd3-514e601861f9 Version: $LATEST
[INFO] e3119ee4-...: No lockout record to clear: user=tomita@g-wise.co.jp
END RequestId: e3119ee4-...
REPORT RequestId: e3119ee4-... Duration: 267.80 ms Init Duration: 506.56 ms Max Memory Used: 99 MB
```

→ **import エラーの痕跡なし**、Init Duration 506ms（コールドスタートで Layer を含む module をロード成功）、`_clear_lockout` の ConditionalCheckFailedException 経路（benign）も期待通り

#### 3.2.7 AuditLogGroup AUTH_SUCCESS 書込確認

```
$ aws logs filter-log-events --log-group-name /aws/safety-confirmation/audit-dev --filter-pattern '{ $.event = "AUTH_SUCCESS" }'
[
  "{\"event\": \"AUTH_SUCCESS\", \"timestamp\": \"2026-06-27T15:38:08Z\", \"principal\": \"tomita@g-wise.co.jp\", \"target\": \"tomita@g-wise.co.jp\", \"outcome\": \"SUCCESS\", \"sourceIp\": null}"
]
```

→ 上記 invoke で生成された AUTH_SUCCESS イベントが `AuditLogGroup`（PhysicalId: `/aws/safety-confirmation/audit-dev`）に書込済、Requirement 16.3 / Property 21 の構造（event / timestamp / principal / target / outcome）+ Phase 12.3 設計通り `sourceIp: null`（Req 1.8 既知ギャップ、α 採用方針）

#### 3.2.8 Cognito User Pool LambdaConfig（再アタッチ前）

```
$ aws cognito-idp describe-user-pool --user-pool-id ap-northeast-1_5uYfaQMLJ
{
  "PreSignUp": "...auth-pre-signup-dev",
  "PreAuthentication": "...auth-pre-auth-dev"
}
```

→ PostAuthentication は外れたまま（dev-login-followup §2.1 暫定対処継続中）

---

## 4. 真因判定：Case D「過去の deploy で自動解消済」

| Case                | 内容                                                                                             | 判定                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| A                   | PostAuth Lambda が古い Layer Version を指している                                                | ❌ 反証（Layer 9 のみ、Lambda は Layer 9 参照）                   |
| B                   | SharedLayer 全体問題（build に audit が含まれない）                                              | ❌ 反証（Layer 9 中身に `shared/audit/logger.py` 6879 byte 実在） |
| C                   | コード自体の問題（import 文 typo 等）                                                            | ❌ 反証（invoke StatusCode 200, FunctionError なし）              |
| **D**（新カテゴリ） | **過去の SharedLayer リビルド + CFn deploy で自動解消済**、残るは Cognito Trigger 再アタッチのみ | ✅ **採用**                                                       |

**根拠**：Lambda LastModified (`2026-06-27T13:16:17`) と Layer 9 CreatedDate (`2026-06-27T13:16:12`) の 4 秒差は同一 CFn deploy。最も近いセッションは **15.2a placeholder deploy（セッション 19/20）** で、その時点で SharedLayer がリビルドされ Layer 9 が新規作成、PostAuth Lambda も Layer 9 に自動更新されていた。当時はユーザーが Trigger を外したままだったため、import 復旧の事実が認識されないまま 15.6a セッション 20 末まで「未復旧」として記録され続けた。

第 7 原則ズレ検知 (a)（最初の認識合わせの想定になかった情報）+ (c)（ユーザー指示と実際のコード・ファイル状態が矛盾）に該当。

---

## 5. 対応内容

### 5.1 コード変更 / template.yaml 変更

**なし**（Case D の判定により、コード・テンプレ変更は不要）。

### 5.2 AWS リソース変更（Cognito User Pool）

ユーザー選択肢 A（AWS CLI 再アタッチ、dev-login-followup §4 同手順）採用：

```powershell
aws cognito-idp update-user-pool `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --lambda-config "PreSignUp=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-signup-dev,PreAuthentication=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-pre-auth-dev,PostAuthentication=arn:aws:lambda:ap-northeast-1:214046906694:function:safety-confirmation-auth-post-auth-dev" `
  --profile AWS-security-check --region ap-northeast-1
```

再アタッチ後の確認：

```
$ aws cognito-idp describe-user-pool --user-pool-id ap-northeast-1_5uYfaQMLJ
{
  "PreSignUp": "...auth-pre-signup-dev",
  "PreAuthentication": "...auth-pre-auth-dev",
  "PostAuthentication": "...auth-post-auth-dev"   ← 復活
}
```

→ template.yaml L1030-1035 `LambdaConfig` 定義と再同期、CFn drift 解消（手動 detect-stack-drift 実行は副次タスク）。

### 5.3 動作確認

§3.2.5〜§3.2.7 で実施済み（再アタッチ前の invoke で既に StatusCode 200 + AuditLogGroup AUTH_SUCCESS 書込確認済）。再アタッチ後の SPA 経由 E2E 確認は元タスク 15.6（実機受入テスト）に委譲（Connect 非依存範囲で本タスクの責任外、tasks.md 本文「SPA 経由のログイン動作確認は不要」と整合）。

---

## 6. Done When 充足

| 条件                                                          | 状態 | 証跡                                                   |
| ------------------------------------------------------------- | ---- | ------------------------------------------------------ |
| `aws lambda invoke` で StatusCode=200 で完走                  | ✅   | §3.2.5                                                 |
| CloudWatch Logs に import エラーなし                          | ✅   | §3.2.6                                                 |
| PostAuth Trigger 再アタッチ済                                 | ✅   | §5.2                                                   |
| AuditLogGroup に AUTH_SUCCESS イベント書込確認                | ✅   | §3.2.7                                                 |
| `docs/notes/dev-login-followup.md` §3 残作業 ① 完了マーク追記 | ✅   | dev-login-followup.md §3 ①                             |
| 進捗ノート 15.20 完了所感記録                                 | ✅   | `docs/notes/_progress.md` 末尾セッション 21 セクション |

---

## 7. 残課題（dev-login-followup.md §3 残作業 ②/③ の状況更新含む）

| 元残作業                                                   | 現状                                                                                                  | 次対応                                                                                                                                                                                                                                                           |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **§3 ①** PostAuth Lambda `shared.audit.logger` import 復旧 | ✅ **本タスク 15.20 で完了**                                                                          | —                                                                                                                                                                                                                                                                |
| §3 ② SPA 側 SRP 実装の `newPasswordRequired` 再検証        | 部分対応済（15-2a-navigation-and-password-fix.md で ε-2 として修正、`NewPasswordPage` 8 件 PBT 追加） | 元タスク 15.6 / 15.6a の実機 E2E で確認可能、追加 PBT は frontend 286 件に統合済                                                                                                                                                                                 |
| §3 ③ dev 環境の本番相当性回復                              | 一部残存                                                                                              | (a) tomita@g-wise.co.jp の `--permanent` 設定はそのまま（本番化時に再生成）、(b) PostAuth Trigger は本タスクで再アタッチ済、(c) AuditLogGroup 書込 / LockoutTable failedAts クリアの 3 点動作は本タスク §3.2 で論理的に確認済、SPA 経由 E2E は元タスク 15.6 委譲 |

---

## 8. 第 7 原則ズレ検知ログ

| #   | 内容                                                                                                                        | 対応                                                                                                        |
| --- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| ①   | 当初仮説（PostAuth Lambda 特有問題 = Case A）が反証された（Layer 9 のみ、Lambda は Layer 9 参照）                           | 停止 → user_input で再アタッチ手法 y/n 取得 → A 採用                                                        |
| ②   | `aws lambda invoke` で既に StatusCode 200 が返り import エラー痕跡なし                                                      | 同上の停止フローに統合、Case D（既に解消済）として記録                                                      |
| ③   | `_progress.md` セッション 20 末（15.6a 完了所感）の「PostAuth Lambda `shared.audit.logger` import 復旧 = High」記述との整合 | 本ノート §4 / §7 で「セッション 20 時点では未認識のまま、実は 15.2a deploy 時点で自動解消されていた」と訂正 |

---

## 9. 関連ファイル

- `docs/notes/dev-login-followup.md`（§2.1 / §3 ① / §4 / §5）
- `backend/lambdas/auth_post_auth/handler.py`（L33 import 文）
- `backend/shared/audit/logger.py`（Phase 12.3、`AuditLogGroup` への put_log_events）
- `backend/shared/audit/__init__.py` / `backend/shared/audit/mask.py`
- `infrastructure/template.yaml`（L370-376 AuditLogGroup / L1082-1108 SharedLayer / L1235-1256 AuthPostAuthFn / L1031-1035 CognitoUserPool.LambdaConfig）
- `scripts/build_layer.ps1`（staging build）
- `docs/notes/_progress.md`（セッション 21 末セクション）
- `docs/notes/15-2a-placeholder-deploy.md`（15.2a 関連、本タスクの真因解消トリガーと推定）

---

## 10. 所感

調べたこと：

1. ローカルの `shared/audit/logger.py` は実在し、build_layer.ps1 は `backend/shared/*` を Recurse コピー
2. PostAuth Lambda の Layer ARN は `safety-confirmation-shared-dev:9`、これは listing で返る唯一の Version でもあり、他 5 Lambda も同一参照
3. Layer 9 アーティファクトを実ダウンロードし展開した結果、`python/shared/audit/logger.py` 6879 byte が含まれている
4. `aws lambda invoke` で StatusCode 200, FunctionError なし、AuditLogGroup に AUTH_SUCCESS が書き込まれた
5. Cognito User Pool LambdaConfig は依然として PostAuthentication が外されたまま

このことからこう考えます：当初仮説の Case A/B/C はいずれも反証され、真因は Case D「過去（おそらく 15.2a placeholder deploy セッション 19/20）の SharedLayer リビルドで Layer 9 が新規作成され、PostAuth Lambda も Layer 9 にバインドされたタイミングで import エラーは自動解消されていた」というシナリオが整合的。第 17 原則対称性推論で「他 Lambda は動作実績あり、PostAuth だけ失敗」を逆方向検証した時点で「他 Lambda と同条件 = 同じ Layer = PostAuth でも import 通るはず」が導かれ、実機確認で裏付けられた。

本タスクの実コード変更ゼロ、template.yaml 変更ゼロ、CFn deploy 未実行。AWS リソース変更は Cognito User Pool LambdaConfig の PostAuthentication 再アタッチ 1 操作のみ。

副次効果：本タスクで Cognito User Pool が template.yaml の定義と再同期され、CFn drift 状態が解消された（手動 detect-stack-drift 実行は副次タスクとして残るが、定義一致なので問題なし）。これにより `_progress.md` セッション 20 末で「Connect 非依存範囲は完了状態に到達、残 12 件すべて Connect 依存」と記録されていた節目に **Connect 非依存範囲の追加ピース（実機環境の暫定対処解除）が一つ加算された**。元タスク 15.6 着手前提条件 5 件中 (ii)「PostAuth Lambda の `shared.audit.logger` import 復旧」が解除済、残 4 件はユーザー手動 + Connect 依存。

第 6 / 第 7 / 第 11 / 第 13 / 第 14 / 第 15 / 第 17 / 第 18 / 第 19 原則を実運用で発動。
