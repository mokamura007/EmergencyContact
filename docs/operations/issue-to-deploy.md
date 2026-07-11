# Issue → 修正 → デプロイ 手順ガイド

- **対象読者**: GitHub Issue を起点に修正／機能追加を行い、dev 環境で検証してから main まで反映する開発者。
- **対象環境**: dev 環境（Account `214046906694`、`ap-northeast-1`）を基準に記述。stg / prod は該当箇所で明示する。
- **前提知識**: 既存 [`docs/operations/deploy.md`](./deploy.md) の CFn デプロイ手順、`.kiro/steering/principles.md` の AI 運用 19 原則。
- **関連ドキュメント**:
  - [`deploy.md`](./deploy.md): CFn デプロイの詳細手順
  - [`admin-user-management.md`](./admin-user-management.md): 管理者ユーザーの Cognito 運用
  - [`privacy.md`](./privacy.md): 個人情報取扱い方針
  - [`.kiro/steering/principles.md`](../../.kiro/steering/principles.md): AI 運用 19 原則

## 1. 概要

### 1.1 目的

Issue 起点の変更を **安全・再現可能** に main まで届ける手順を定める。個々の判断（spec を作るか、PR をどう分けるか、コンフリクトをどう解消するか）で迷わないよう、判断基準と落とし穴を Task 1（Issue #3 認証フロー修正）／Task 4（PR 分割）の実績から抽出した。

### 1.2 全体フロー

```
[Step 1] Issue 選定・重要度判定
   │
[Step 2] Spec 作成（.kiro/specs/<feature>/{requirements,design,tasks}.md）
   │
[Step 3] ブランチ作成（feat/... or fix/...、起点は develop）
   │
[Step 4] 実装 + テスト（backend pytest / frontend vitest all green）
   │
[Step 5] ローカル検証（npm run dev、localhost:5173）
   │
[Step 6] dev デプロイ（S3 Cache-Control 強制 + CloudFront invalidation）
   │
[Step 7] 実機検証（dev URL、evidence 記録）
   │
[Step 8] PR チェーン（feature → develop → main → develop back-merge）
   │
[Step 9] Issue クローズと後処理
```

### 1.3 スコープ外

- **通常運用リリース**（本番デプロイ、監視、インシデント対応）は [`runbook.md`](./runbook.md) / [`incident-response.md`](./incident-response.md)（存在する場合）に委譲。
- **CFn 管理外リソース**（Connect インスタンス、電話番号 ARN 等）の事前準備は [`deploy.md`](./deploy.md) §3 を参照。
- **spec の書き方**（要件・設計・タスク分割）自体のガイドは本ドキュメントの範囲外（既存 `.kiro/specs/*` の実例を参照）。

---

## 2. Step 1: Issue の重要度判定と選定

### 2.1 選定基準（複数 Issue から 1 つを選ぶ場合）

| 観点         | 高優先度               | 低優先度             |
| ------------ | ---------------------- | -------------------- |
| 影響ユーザー | 全員／初回ログイン全員 | 一部管理者のみ       |
| 業務影響     | 機能が全く使えない     | UX 劣化のみ          |
| 回避策       | なし／複雑             | ワークアラウンドあり |
| 修正コスト   | 小〜中                 | 大（設計変更を伴う） |

同じ優先度なら **修正コスト小** を先に着手（quick win で開発リズムを保つ）。

### 2.2 Issue 内容の初動確認

Issue コメントを最新順に読み、以下を明文化してから Step 2 に進む。

1. **再現手順**（reproducer が Issue に無ければ、コメントで確認要求）
2. **期待動作**（Issue タイトルからの推測ではなく、報告者の言葉で確認）
3. **既知の回避策**（ワークアラウンド）
4. **関連 Issue / PR**（過去に類似修正があれば重複回避）

---

## 3. Step 2: Spec 作成（必須）

すべての Issue 対応で `.kiro/specs/<feature-slug>/` に spec を作成する。

### 3.1 spec の 3 点セット

| ファイル          | 内容                                                                   |
| ----------------- | ---------------------------------------------------------------------- |
| `requirements.md` | Issue の背景、ユーザーストーリー、Acceptance Criteria（EARS 形式推奨） |
| `design.md`       | 変更対象コンポーネント、データフロー、影響範囲、代替案の比較と選定理由 |
| `tasks.md`        | 実装タスクを 30 分〜数時間単位に分解、依存関係、検証手順               |

### 3.2 spec を作る利点（Task 1 の実績）

- Issue #3 の Cognito 初回ログインフロー修正は 21 タスクに分割された。7 巡目で真因（`userAttributes.email` 送信で `Cannot modify an already provided email`）が判明したが、spec の tasks.md に各巡目の試行と証跡が残っていたため、次巡目で同じ仮説を検証せずに済んだ。
- design.md に「AWS 側の直接検証を SPA 推測より先行させる」方針を書いておくと、第 16 原則（仮説検証義務）／第 17 原則（対称性推論）の適用が徹底される。

### 3.3 spec テンプレート参照先

既存 spec（`.kiro/specs/fix-initial-login-flow/*`、`.kiro/specs/safety-confirmation-system/*`）を雛形に流用してよい。

---

## 4. Step 3: ブランチ作成

### 4.1 命名規則

| プレフィックス | 用途                          | 例                                    |
| -------------- | ----------------------------- | ------------------------------------- |
| `feat/`        | 新機能                        | `feat/admin-user-cognito-integration` |
| `fix/`         | 不具合修正                    | `fix/issue-3-initial-login-flow`      |
| `chore/`       | ドキュメント／設定／rename 等 | `chore/rename-steering-file`          |
| `refactor/`    | 動作を変えないリファクタ      | `refactor/employee-validator`         |

- スラッシュ後は kebab-case、Issue 番号を含めると追跡容易（`fix/issue-3-...`）。
- **起点は必ず `develop`**（main 起点にすると main の先行を巻き込む）。

### 4.2 コマンド

```powershell
git checkout develop
git pull origin develop
git checkout -b fix/issue-N-<short-slug>
```

---

## 5. Step 4: 実装 + テスト

### 5.1 実装中のルール

- **DRY 原則**（第 19 原則 a）：既存ヘルパー・validator・constants を先に検索してから新規追加。
- **フォールバック禁止**（第 19 原則 b）：エラーは握りつぶさず、上位に伝播。
- **仮説検証義務**（第 16 原則）：「たぶんこの API がこう返す」で進めず、実際の返却値を確認してから前進。

### 5.2 テスト実行

| 対象             | コマンド                                                                                                   | 期待値                                                             |
| ---------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| backend 全体     | `python -m pytest backend/tests -q`                                                                        | all green（環境変数の設定が必要な場合は tests 側の conftest 参照） |
| backend スコープ | `python -m pytest backend/tests/shared/employee backend/tests/lambdas/employee_api backend/tests/smoke -q` | 変更範囲に絞って高速確認                                           |
| frontend         | `npm --prefix frontend test`                                                                               | 32+ files / 361+ tests passed                                      |

Windows で backend の venv を使う場合は絶対パス指定が確実：

```powershell
& "backend\.venv\Scripts\python.exe" -m pytest backend\tests\... -q
```

### 5.3 テストが赤い時

- **1〜2 回で赤なら**：assertion の期待値ずれ／仕様変更を確認。
- **3 回同じアプローチで赤なら**：別視点で見直す（詳細はトラブルシューティング §11-4）。

---

## 6. Step 5: ローカル検証

### 6.1 なぜ必要か

Task 1 で「デプロイ→キャッシュクリア→実機確認」を 5 回以上繰り返した。ローカルで再現できる不具合（DataCloneError、JSON schema mismatch 等）はローカルで直せば分単位、デプロイ経由なら 10 分単位。

### 6.2 手順

```powershell
npm --prefix frontend run dev
# → Vite dev server が http://localhost:5173 で起動
```

- `frontend/.env.local` に dev 環境の Cognito ID / API URL が定義済み。
- Cognito SRP 認証は **Origin 制約なし** で localhost から即動作する。
- **API Gateway 呼び出しは CORS 制約あり**。以下いずれかで対処：
  - Vite proxy（`vite.config.ts` に proxy 定義追加）
  - MSW でモック
  - dev API Gateway の CORS 設定に `http://localhost:5173` を追加（推奨しない、恒久設定になる）

### 6.3 ローカル検証で確認すること

- 変更した画面が想定通り描画されるか（コンパイルエラーなし）
- ユーザー操作を通じて機能が動くか（ログイン、フォーム送信、遷移）
- ブラウザ DevTools Console にエラー・警告が出ていないか
- Network タブで API リクエスト／レスポンスが想定通りか

---

## 7. Step 6: デプロイ（dev 環境）

### 7.1 デプロイコマンド

詳細は [`deploy.md`](./deploy.md) を参照。要点：

```powershell
# CFn テンプレート更新（infrastructure 側変更がある場合）
aws cloudformation deploy `
  --template-file infrastructure/template.yaml `
  --stack-name safety-confirmation-dev `
  --capabilities CAPABILITY_NAMED_IAM `
  --profile AWS-security-check

# frontend の S3 sync
npm --prefix frontend run build
aws s3 sync frontend/dist s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ `
  --profile AWS-security-check
```

### 7.2 **必須チェックリスト**（Task 1 で判明した落とし穴）

デプロイ後に必ず以下 3 項目を確認する。省略すると実機で「古いバンドルが動き続ける」現象が発生する。

- [ ] **S3 `index.html` の Cache-Control**：`no-cache, no-store, must-revalidate` を強制設定
  ```powershell
  aws s3 cp s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/index.html `
            s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/index.html `
            --metadata-directive REPLACE `
            --cache-control "no-cache, no-store, must-revalidate" `
            --content-type "text/html" `
            --profile AWS-security-check
  ```
- [ ] **CloudFront invalidation** の発行と完了確認
  ```powershell
  aws cloudfront create-invalidation `
    --distribution-id EAXOBS3AIJQHH `
    --paths "/*" `
    --profile AWS-security-check
  ```
- [ ] **実機ブラウザでハードリロード**（Ctrl+Shift+R）：ローカルキャッシュを無視してリソースを再取得

### 7.3 なぜここまで厳しくするか

Task 1 の 5〜7 巡目で「SPA コード修正済みなのに実機の Console には古いバンドル `index-B5ShzSMP.js` からのエラーが出続けた」現象があった。原因はブラウザキャッシュ + CloudFront のオブジェクトキャッシュ。3 項目セットで潰さないと、修正が実機に届かない。

---

## 8. Step 7: 実機検証

### 8.1 検証環境

- dev URL: CloudFront distribution `EAXOBS3AIJQHH` の URL（詳細は `deploy.md` の Outputs 参照）
- テストユーザー: dev Cognito User Pool `ap-northeast-1_5uYfaQMLJ` に作成した検証専用ユーザー

### 8.2 evidence 記録

`docs/notes/<feature-slug>-verification.md` に以下を残す。

- 検証日時と検証者
- 実施手順（Step 1: ログイン画面を開く → Step 2: ... のように再現手順として記述）
- 期待動作と実際の動作
- スクリーンショット or Console ログ（機微情報はマスク）
- 判定（PASS / FAIL）と、FAIL の場合は再修正 Issue 起票

### 8.3 実機検証で発見した不具合の扱い

- **修正で対応可能な軽微な不具合**: 同一 PR で修正、evidence 記録に「修正した」旨を追記。
- **設計変更を伴う不具合**: PR は一旦 open のまま、別 Issue を起票して優先度判定に戻る。

---

## 9. Step 8: PR チェーン

### 9.1 全体像

```
feature branch ─(squash merge)─▶ develop ─(merge commit)─▶ main
                                     ▲                        │
                                     └──(fast-forward)────────┘
                                       back-merge
```

- **feature → develop**: `--squash --delete-branch`（履歴を綺麗に保つ、feature ブランチは使い捨て）
- **develop → main**: `--merge`（merge commit で main への合流点を残す、develop は永続）
- **main → develop**: `--ff-only`（back-merge、履歴改変なし、develop tip を main tip に揃える）

### 9.2 PR 分割の判断基準

Task 4 で 19 ファイルの diff を 2 PR に分割した実績から、以下 3 観点で判断する。

| 観点                   | 説明                                                                                                |
| ---------------------- | --------------------------------------------------------------------------------------------------- |
| **機能単位**           | 1 PR = 1 機能変更。「管理者ユーザー Cognito 統合」と「steering rename」は別機能なので別 PR          |
| **相互依存性**         | Backend / Frontend / Infra / Docs を分離しても、統合されないと動かないなら分けない（Plan Y は却下） |
| **独立レビュー可能性** | レビュアーが単独で理解・承認できる粒度か                                                            |

**分けすぎるとダメな例**（Task 4 の Plan Y）：Backend / Frontend / Infra / Docs / steering を 5 PR に分けると、各 PR 単独では動作せず、レビュアーは「これ動くの？」で足止め。→ 相互依存する変更は 1 PR に集約。

### 9.3 feature → develop（squash merge）

```powershell
# feature ブランチで
git push -u origin feat/<slug>

# PR 作成（GitHub CLI）
gh pr create --base develop --head feat/<slug> `
  --title "feat: <説明>" `
  --body-file .pr_body.md

# squash merge + branch 削除
gh pr merge <PR番号> --repo <owner>/<repo> --squash --delete-branch

# ローカルの後始末
git checkout develop
git pull origin develop
git branch -D feat/<slug>  # squash merge のため -d は拒否、-D で強制
```

### 9.4 develop → main（merge commit）

```powershell
gh pr create --base main --head develop `
  --title "release: <一言まとめ>" `
  --body "..."

gh pr merge <PR番号> --repo <owner>/<repo> --merge
```

### 9.5 main → develop（back-merge、必須）

**main の先行を放置すると次の feature の起点が古くなる**。develop → main merge の直後に必ず back-merge する。

```powershell
git checkout develop
git pull origin develop
git fetch origin
git merge origin/main --ff-only
git push origin develop
```

`--ff-only` が失敗する場合は、develop 側に独自 commit がある（PR chain が非典型的）ため、状況を確認してから対処（トラブルシューティング §11-1 参照）。

---

## 10. Step 9: Issue クローズと後処理

### 10.1 自動クローズ

feature → develop の PR 説明に `Closes #N` を記載しておくと、develop → main merge 時に Issue が自動クローズされる。

### 10.2 ローカルクリーンアップ

```powershell
# feature ブランチが残っていれば削除
git branch -D feat/<slug>

# 一時ファイル（コミットメッセージ下書き等）を削除
Remove-Item .pr_body_*.md, .commit_msg_*.txt, .pytest_out.txt, .vitest_out.txt -Force -ErrorAction SilentlyContinue
```

### 10.3 evidence の final commit

`docs/notes/<slug>-verification.md` に「実機検証 PASS」の最終確認を追記し、develop に別 PR で反映（chore/... ブランチ）するか、次の feature PR に含めて記録する。

---

## 11. トラブルシューティング

### 11.1 main 発散を検知したら

**検知方法**：

```powershell
git fetch origin
git log --oneline origin/develop..origin/main
```

上記の出力に **PR merge commit 以外の commit** があれば main が発散している（例：Task 4 で `feature/domestic-phone-format` が develop を経由せず main に直接 merge されていた）。

**対応手順**：

1. **main を develop に merge back**
   ```powershell
   git checkout develop
   git pull origin develop
   git merge origin/main --no-ff -m "chore: main の直接コミットを develop に取り込み"
   ```
2. **conflict が出た場合は §11-2 の方針で resolve**
3. **テスト再実行**（backend pytest + frontend vitest）で回帰確認
4. **push して既存 PR の mergeable 状態を再判定させる**
   ```powershell
   git push origin develop
   ```

### 11.2 content conflict の resolve 方針

**基本方針**: **両方の変更を統合する**（片方を捨てない）。

- 「HEAD 側（develop）を採用」「origin/main 側を採用」の 2 択で判断せず、両方が意図した変更をコードに残す。
- Import 文が conflict した場合は両方の import を追加してソート。
- 関数ロジックが conflict した場合は、両方の分岐が動くように if/else を再構成（例：Task 4 で電話番号の E.164 判定と Domestic JP 判定を or で結合）。

**resolve 後に必ずやること**：

1. `git status` で `UU` フラグが残っていないか確認
2. Ripgrep or `Select-String` で conflict marker (`<<<<<<<` / `=======` / `>>>>>>>`) が本体コードに残っていないか確認
3. **テスト再実行**（両方の変更が意図した通り動くことを test で担保）
4. 影響を受けた test の assertion 更新（Task 4 で `phone-not-e164` の入力値を `0812345678` → `12345` に変更した実例）

### 11.3 デプロイしても実機に反映されない

**症状**: SPA コード修正 → デプロイ済みなのに、実機ブラウザで古い挙動が続く。

**診断手順**（順に確認）:

1. **ブラウザキャッシュ**: DevTools を開いた状態で Ctrl+Shift+R（ハードリロード）
2. **S3 の index.html の Cache-Control**:
   ```powershell
   aws s3api head-object `
     --bucket safety-confirmation-spa-dev-214046906694-ap-northeast-1 `
     --key index.html `
     --profile AWS-security-check
   ```
   出力の `CacheControl` が `no-cache, no-store, must-revalidate` になっているか確認。なっていなければ §7.2 のコマンドで再設定。
3. **CloudFront invalidation の状態**:
   ```powershell
   aws cloudfront list-invalidations `
     --distribution-id EAXOBS3AIJQHH `
     --profile AWS-security-check
   ```
   直近の invalidation が `Completed` になっているか。`InProgress` なら数分待つ。
4. **バンドルハッシュの確認**: 実機ブラウザで Network タブを開き、`index-<hash>.js` のハッシュが最新の `frontend/dist/assets/` 内のものと一致するか。

### 11.4 3 回同じアプローチで失敗したら

Task 1 で SPA コード起点の推測を 3 回外した後、ユーザーの「別視点で見直せ」指示で **AWS 側の直接検証** に切り替え、真因（Cognito が `Cannot modify an already provided email` を返す）が確定した実績がある。

**別視点の切り替え方**：

1. **仮説の逆方向を検証**（第 17 原則の対称性推論）
   - 「SPA が悪い」→「SPA を疑わず、API / AWS 側が正しく動いているか直接確認」
   - 「A なら B」→「B なら A」も成り立つか確認
2. **仮説を検証してから前進**（第 16 原則の仮説検証義務）
   - 「たぶんこの API がこう返す」→ 実際に AWS CLI で呼んで返却値を確認
3. **AWS 側の直接検証を SPA 推測より先行させる**
   - Cognito 関連なら `admin-respond-to-auth-challenge` を CLI から直接叩く
   - Lambda 関連なら CloudWatch Logs / X-Ray で実行結果を確認
   - DynamoDB 関連なら `get-item` で実データを確認

**Task 1 での実例**（第 17 原則適用）：

- 5 巡目まで：「SPA の `completeNewPasswordChallenge` に何を渡すか」を試行錯誤 → 全部失敗
- 6 巡目：「そもそも Cognito は `userAttributes.email` を受け取ると何を返すのか」を CLI で直接確認 → `NotAuthorizedException: Cannot modify an already provided email` が即判明
- 7 巡目：修正 → 真因解消

---

## 12. 付録

### 12.1 関連ドキュメント

- [`deploy.md`](./deploy.md): CFn デプロイの詳細手順、AWS リソース ID 一覧
- [`admin-user-management.md`](./admin-user-management.md): 管理者ユーザーの Cognito 運用
- [`privacy.md`](./privacy.md): 個人情報取扱い方針
- [`mock-integration-test.md`](./mock-integration-test.md): モック統合テスト手順
- [`runbook.md`](./runbook.md)（存在する場合）: 通常運用リリース
- [`incident-response.md`](./incident-response.md)（存在する場合）: インシデント対応

### 12.2 AWS 環境情報

dev / stg / prod の AWS リソース ID（CloudFront distribution / S3 bucket / Cognito User Pool 等）は [`deploy.md`](./deploy.md) の環境別セクションを **唯一の情報源** とする。本書に埋め込むと環境変更時に更新漏れが発生するため、重複記載しない。

### 12.3 チェックリスト（Issue 対応のクロージング前）

- [ ] Step 2: spec 3 点セット（requirements / design / tasks）が完成
- [ ] Step 4: backend pytest / frontend vitest all green
- [ ] Step 5: ローカル検証 PASS
- [ ] Step 6: デプロイ後の 3 項目（S3 Cache-Control / CloudFront invalidation / ハードリロード）実施
- [ ] Step 7: 実機検証 PASS、evidence 記録
- [ ] Step 8: PR チェーン（feature → develop → main → develop back-merge）完遂
- [ ] Step 9: Issue クローズ、ローカル cleanup
