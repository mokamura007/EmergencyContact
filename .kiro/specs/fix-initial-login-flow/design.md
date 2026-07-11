# Design Document

## Overview

本設計書は、`.kiro/specs/fix-initial-login-flow/requirements.md` の Requirement 1〜3 を実現するための SPA 認証層の追加設計を記述する。既存 `.kiro/specs/safety-confirmation-system/design.md` の「Auth_Service」節および「エラー処理方針」節を前提とし、それらと矛盾しない差分設計として構成する。

方針要旨：

- コアの認証ロジック（`cognitoAuthProvider.ts`）は ε-2 修正済のまま変更しない。SDK コールバックの型変換、`completeNewPasswordChallenge`、`userAttributes` の除去処理はいずれも仕様通りで、変更は逆に副作用を生む恐れがある。
- 修正の主眼は **UI 層（`LoginPage.tsx`）のエラー分岐と診断出力**、および **テスト補強** に置く。
- 実機での動作確認は AWS 環境依存のため、コード変更完了後にビルド → S3 sync → CloudFront invalidation → 手順書 4.2 再実行の運用手順を tasks.md に落とす。

## Architecture

### 認証フロー（ε-2 修正後の現状シーケンス）

```
+---------------------+       +---------------------------+       +-----------------------+
|  User (Browser)     |       |  Admin_Console (SPA)      |       |  Cognito User Pool    |
+---------------------+       +---------------------------+       +-----------------------+
        |                                |                                 |
        | 1. email + password 入力 & submit                                |
        |------------------------------->|                                 |
        |                                | 2. authenticateUser(SRP)        |
        |                                |-------------------------------->|
        |                                |                                 |
        |                                |   [ユーザーが FORCE_CHANGE_PASSWORD 状態のとき] |
        |                                |<--- 3a. newPasswordRequired --- |
        |                                |    (userAttributes, requiredAttributes) |
        |                                |                                 |
        |                                | 4a. resolve SignInResult        |
        |                                |    kind = NEW_PASSWORD_REQUIRED |
        |                                |    complete = closure           |
        |                                |                                 |
        |                                | 5a. navigate('/new-password',   |
        |                                |     { state: { challenge } })   |
        |<-------------------------------|                                 |
        |                                |                                 |
        | 6a. new password + confirm 入力 & submit                         |
        |------------------------------->|                                 |
        |                                | 7a. challenge.complete(newPass) |
        |                                |     → completeNewPasswordChallenge (SDK) |
        |                                |-------------------------------->|
        |                                |<--- 8a. onSuccess(session) ---- |
        |                                | 9a. navigate('/', replace)      |
        |<-------------------------------|                                 |
        |                                |                                 |
        |                                |   [ユーザーが CONFIRMED 状態のとき]  |
        |                                |<--- 3b. onSuccess(session) ---- |
        |                                | 4b. resolve SUCCESS + tokens    |
        |                                | 5b. navigate(location.state.from ?? '/') |
        |<-------------------------------|                                 |
        |                                |                                 |
        |                                |   [認証失敗のとき]                   |
        |                                |<--- 3c. onFailure(err) -------- |
        |                                | 4c. reject AuthenticationFailedError |
        |                                |     (code = extractCognitoErrorCode)|
        |                                | 5c. LoginPage.catch でメッセージ分岐 |
```

### エラー分岐の拡張設計（本 spec の差分）

現状（`LoginPage.tsx`）：

```
try {
  ...
} catch (err) {
  if (err instanceof AuthenticationFailedError) {
    if (err.code === 'NotAuthorizedException' || err.code === 'UserNotFoundException') {
      setErrorMessage('メールアドレスまたはパスワードが正しくありません。');
    } else if (err.code === 'PasswordResetRequiredException') {
      setErrorMessage('パスワードのリセットが必要です。システム管理者にお問い合わせください。');
    } else {
      setErrorMessage('ログインに失敗しました。時間をおいて再度お試しください。');
    }
  } else {
    setErrorMessage('ログインに失敗しました。時間をおいて再度お試しください。');
  }
}
```

修正後：

- エラーコード → メッセージ変換を **純粋関数 `translateLoginError(err)`** として切り出す（Requirement 2.4 の分岐拡張を集約 + テスト容易化 + DRY）。
- `console.error` で「Login failed:」プレフィックス + `code` + `message`（Requirement 2.1）を出力する。
- 未知の code は「汎用メッセージ + `（コード: XXX）`」を返す（Requirement 2.2）。
- `MissingAuthConfigError` は `AuthenticationFailedError` ではないため専用分岐（Requirement 2.3）。

疑似コード：

```
export function translateLoginError(err: unknown): string {
  if (err instanceof MissingAuthConfigError) {
    return '認証設定が未構成です。管理者に連絡してください。';
  }
  if (err instanceof AuthenticationFailedError) {
    switch (err.code) {
      case 'NotAuthorizedException':
      case 'UserNotFoundException':
        return 'メールアドレスまたはパスワードが正しくありません。';
      case 'PasswordResetRequiredException':
        return 'パスワードのリセットが必要です。システム管理者にお問い合わせください。';
      case 'UserLambdaValidationException':
        return 'ログイン後処理でエラーが発生しました。システム管理者にお問い合わせください。（コード: UserLambdaValidationException）';
      case 'TooManyRequestsException':
      case 'LimitExceededException':
        return '短時間に多くのリクエストが行われました。しばらく待って再度お試しください。';
      case 'UserNotConfirmedException':
        return 'アカウントが有効化されていません。管理者にお問い合わせください。';
      default:
        return `ログインに失敗しました。時間をおいて再度お試しください。（コード: ${err.code}）`;
    }
  }
  return 'ログインに失敗しました。時間をおいて再度お試しください。';
}
```

診断ログ：

```
console.error('Login failed:', {
  errorName: err instanceof Error ? err.name : 'unknown',
  code: err instanceof AuthenticationFailedError ? err.code : undefined,
  message: err instanceof Error ? err.message : String(err),
});
```

- ID / パスワード等の入力値は出力しない（Requirement 2.1 の要請）。
- `console.error` は本番ビルドでもデフォルトで出力される（Vite の `import.meta.env.PROD` で抑制していないことを確認済）。

### モジュール配置

- `frontend/src/routing/loginErrors.ts`（新規）：`translateLoginError` 純粋関数を export。
- `frontend/src/routing/LoginPage.tsx`（改修）：`translateLoginError` を import して `catch` で使用。`console.error` を追加。
- `frontend/src/routing/loginErrors.test.ts`（新規）：`translateLoginError` の全分岐に対する単体テスト。
- `frontend/src/routing/LoginPage.test.tsx`（改修）：`console.error` 出力の検証、未知エラーコード時のメッセージ検証、`MissingAuthConfigError` 分岐の検証を追加。

なお、認証層（`cognitoAuthProvider.ts` / `NewPasswordPage.tsx` / `errors.ts` / `types.ts`）はいずれも無変更とする。

### エラーコード分類表

| Cognito `__type` / `code`            | 分類     | 現状 UI                                                      | 修正後 UI                                                                                               | 検証テスト             |
| ------------------------------------ | -------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- | ---------------------- |
| `NotAuthorizedException`             | 資格情報 | 「メールアドレスまたはパスワードが正しくありません。」       | 同左                                                                                                    | 既存テスト継続         |
| `UserNotFoundException`              | 資格情報 | 「メールアドレスまたはパスワードが正しくありません。」       | 同左                                                                                                    | 既存テスト継続         |
| `PasswordResetRequiredException`     | フロー   | 「パスワードのリセットが必要です。...」                      | 同左                                                                                                    | 既存テスト継続         |
| `UserLambdaValidationException`      | Lambda   | 汎用「ログインに失敗しました。時間をおいて...」              | 「ログイン後処理でエラーが発生しました。...（コード: UserLambdaValidationException）」                  | 新規（PostAuth 障害）  |
| `TooManyRequestsException`           | レート   | 汎用                                                         | 「短時間に多くのリクエストが行われました。しばらく待って...」                                           | 新規                   |
| `LimitExceededException`             | レート   | 汎用                                                         | 「短時間に多くのリクエストが行われました。しばらく待って...」                                           | 新規                   |
| `UserNotConfirmedException`          | フロー   | 汎用                                                         | 「アカウントが有効化されていません。管理者にお問い合わせください。」                                    | 新規                   |
| `InvalidParameterException`          | 入力     | 汎用                                                         | 汎用 + コード併記                                                                                       | 新規（デフォルト経路） |
| `ResourceNotFoundException`          | 設定     | 汎用                                                         | 汎用 + コード併記                                                                                       | 新規（デフォルト経路） |
| その他（未知 code）                  | 未分類   | 汎用                                                         | 汎用 + コード併記                                                                                       | 新規                   |
| `MissingAuthConfigError`（SPA 独自） | 設定     | 汎用（`AuthenticationFailedError` 判定を通らず else に落ち） | 「認証設定が未構成です。管理者に連絡してください。」                                                    | 新規                   |
| `NewPasswordRequiredError`（旧経路） | 廃止     | ε-2 修正で不到達                                             | 到達しない前提。ただし fallback として `err.name === 'NewPasswordRequiredError'` の直進をテスト側で警告 | -                      |

### ε-2 修正との整合

- `NEW_PASSWORD_REQUIRED` は `SignInResult` の 1 バリアントとして `try` の中で判定される（既存実装）。本 spec は `catch` の分岐のみ拡張する。
- したがって `NewPasswordRequiredError` は本フローでは投げられないことが保証されるが、DI 差替え等で旧プロバイダが混入した場合の保険として、`translateLoginError` は `err instanceof Error && err.name === 'NewPasswordRequiredError'` を検出したら「システム状態が矛盾しています。管理者にお問い合わせください。」と表示する（隠れバグ検出）。

## Testing Strategy

### 単体テスト（純粋関数）

`frontend/src/routing/loginErrors.test.ts`：

- Cognito エラーコード全 8 種（表参照）+ 未知 code + `MissingAuthConfigError` + `NewPasswordRequiredError`（安全ネット）+ 非 Error 値 の各ケースで、期待メッセージが返ることを検証する。
- 分岐 100% カバレッジを目標とする。

### 統合テスト（LoginPage）

`frontend/src/routing/LoginPage.test.tsx`：

- 既存テスト（成功・NotAuthorized・NEW_PASSWORD_REQUIRED 遷移）はそのまま維持。
- 追加：
  - `console.error` が `signIn` reject 時に呼ばれ、`code` と `message` を含む object 引数で出力されることを spy で検証。
  - 未知エラーコード（例：`UserLambdaValidationException`）reject 時に、画面に「（コード: UserLambdaValidationException）」が末尾に併記されることを検証。
  - `MissingAuthConfigError` reject 時に、専用メッセージが表示されることを検証。

### 実機検証（AWS 環境）

以下 3 通りの状態下で手順書 4.2 を実行する（tasks.md にチェックリスト化）：

1. `FORCE_CHANGE_PASSWORD` 状態の新規テストユーザー（`admin-create-user` で作成、`--message-action SUPPRESS`）→ `/new-password` 遷移 → 新パスワード設定 → `/` 遷移が成功する。
2. `CONFIRMED` 状態の既存ユーザー（`tomita@g-wise.co.jp` 等）→ `/` 直接遷移が成功する。
3. 誤ったパスワード（`NotAuthorizedException`）→ 「メールアドレスまたはパスワードが正しくありません。」が表示される。

いずれの場合も、F12 開発者ツールの Console に `Login failed:` プレフィックスのログが出るか（1 と 2 は出ない、3 は出る）を確認する。

## デプロイ手順（tasks.md に転記）

1. `frontend/` で `.env.local` が dev 環境の値と一致していることを確認。
2. `npm ci` → `npm run test:run` → `npm run build`。
3. `aws s3 sync frontend/dist/ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete --profile AWS-security-check --region ap-northeast-1`。
4. `aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" --profile AWS-security-check`。
5. ブラウザキャッシュを強制無効化した上で手順書 4.2 を再実行。

## リスクと緩和

| リスク                                           | 影響                            | 緩和                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| コード修正しても実機の別要因で失敗               | issue #3 が再オープン           | Requirement 2.1 の `console.error` により原因コードが特定可能。再発時は本 spec に追記                                                                                                                                                                                                                   |
| `translateLoginError` 分岐追加が既存テストを壊す | 回帰                            | Requirement 2.4 の既知 3 分岐（`NotAuthorizedException` / `UserNotFoundException` / `PasswordResetRequiredException`）のメッセージは不変。既存テスト「未知の AuthenticationFailedError は汎用メッセージ」は Requirement 2.2 の対象であり、期待文言を「汎用 + コード併記」に更新する（意図的な仕様変更） |
| CloudFront invalidation 遅延で古いバンドル配信   | 手順書 4.2 で誤検出             | invalidation 完了後にブラウザキャッシュ強制無効化。再検証はハードリロード必須                                                                                                                                                                                                                           |
| ε-2 修正が実は不完全でコード上のバグが残っている | 修正しても手順書 4.2 が失敗する | `translateLoginError` の未知コード表示で真因が特定可能になり、切り分けの起点になる                                                                                                                                                                                                                      |

## 検証済み前提

- `frontend/.env.local` は dev 環境の Cognito User Pool ID / Client ID / API Base URL を保持している（本セッションで実物確認済）。
- PostAuthentication Lambda Trigger は 2026-06-27 に再アタッチ済（`docs/notes/15-20-postauth-import-fix.md`）。
- `cognitoAuthProvider.ts` の ε-2 修正はコード上完了しており、ユニットテスト `cognitoAuthProvider.test.ts` で `NEW_PASSWORD_REQUIRED` 到達までの経路が緑（本セッションでコード読取確認済、テスト実行は tasks.md 側で実施）。

## 補完設計（issue #3 再修正、2026-07-10 セッション後半）

### 背景

1 回目のデプロイ後、実機で `issue3-verify@example.com` + `TempPass!2026` を試したところ、依然として「ログインに失敗しました。時間をおいて再度お試しください。」が **コード併記なしで** 表示された。これは初版の `translateLoginError` の未分類経路（`return GENERIC_PREFIX`）に落ちていることを意味し、以下いずれかの状態を示唆する：

- (i) `AuthenticationFailedError` にラップされる前に `authenticateUser` 呼出自体で throw された値が catch に到達している
- (ii) Cognito SDK が生の SDK エラー（`AuthenticationFailedError` インスタンスでない）を Promise reject に渡している
- (iii) 旧バンドルがブラウザキャッシュに残っている

(iii) はハードリロード（Ctrl+Shift+R）＋ CloudFront invalidation で解消できるが、(i)(ii) の場合は最終行 `return GENERIC_PREFIX` に落ちる限り真因が UI にも Console にも露出しない。

### 変更内容

1. `extractErrorIdentifier(err: unknown): string` 純粋関数を新設。以下優先順位で識別子を抽出：
   - `err.code`（Cognito 独自コード）
   - `err.__type`（Cognito HTTP レスポンスタイプ）
   - `err.name`（Error 派生の name）
   - `err.constructor.name`（クラス名）
   - `typeof err`（原始型）
   - 最終 fallback：`'unknown'`（実装上は到達しない）
2. `translateLoginError` の最終行を `return \`${GENERIC_PREFIX}（コード: ${extractErrorIdentifier(err)}）\`` に変更。**未分類経路でも必ず識別子併記**。
3. `LoginPage.tsx` の `console.error` を 3 引数呼出に変更。第 3 引数として生 err オブジェクトを渡す。DevTools で展開可能。

### 検証観点の追加

- 生 SDK エラー風の plain object（`{ code: 'UserNotFoundException' }`）を reject → UI に「（コード: UserNotFoundException）」が併記される
- `null` / `undefined` / string / number の reject → 対応する識別子が併記される
- `code` / `__type` / `name` すべて空の Error → `constructor.name` が採用される

### 保守メモ

`extractErrorIdentifier` は将来他のエラー表示コンポーネント（`NewPasswordPage.tsx` 等）でも同じ判定が必要になる可能性があるため、`frontend/src/routing/loginErrors.ts` の named export として公開している。他コンポーネントから再利用する場合は本モジュールから import する（DRY）。

## 補完設計 2（issue #3 真因対応：DataCloneError、2026-07-10 セッション終盤）

### 判明した真因

2 回目のデプロイ後、実機報告で `（コード: DataCloneError）` が併記された。分析：

- `DataCloneError` は Web API 標準の DOMException。`structuredClone` できない値（Function / DOM ノード / WeakMap 等）を渡した際に発生。
- React Router `navigate(path, { state })` は history.state に `structuredClone` で保存する（HTML5 History API 仕様）。
- 初版の `LoginPage.tsx` は `navigate('/new-password', { state: { challenge: result } })` を実行。`result` は `NEW_PASSWORD_REQUIRED` バリアントで、`complete` 関数（closure）を持つ。
- 関数は `structuredClone` 不可 → `DataCloneError` → catch → `translateLoginError` → 「（コード: DataCloneError）」表示。
- Cognito 側は `InitiateAuth` + `RespondToAuthChallenge` の 2 回リクエストが成功しており、認証パイプラインは正常。破綻していたのは SPA 側の遷移データ受け渡しのみ。

ε-2 修正の設計時点で、この Web API 制約が見落とされていた（`extractChallenge(state)` は `challenge.kind` / `challenge.complete` を検証する実装で、そもそも history.state に function を書き込む前提だった）。

### 修正内容

- `frontend/src/auth/authChallengeStore.ts` を新設：
  - `let pendingChallenge: NewPasswordRequiredChallenge | null = null;`（モジュールスコープ）
  - `setPendingChallenge(challenge)` / `consumePendingChallenge()` / `clearPendingChallenge()`
  - `consume` は 1 回のみ有効。2 回目以降は null。
- `LoginPage.tsx`：
  - `navigate('/new-password', { state: { challenge: result } })` → `setPendingChallenge(result); navigate('/new-password')`
- `NewPasswordPage.tsx`：
  - `useLocation` を撤去、`consumePendingChallenge()` を `useMemo` の factory で 1 回だけ呼ぶ
  - `challenge === null` なら `<Navigate to="/login" replace />`（既存の「state 喪失時は再ログイン」動作と同等）

### 特性と制約

- **リロード**：モジュール state はページリロードで喪失 → `/login` に自動リダイレクト（正しい動作）
- **ブラウザ戻る**：戻ってもストアには何も無いため `/login` に戻る（正しい動作）
- **複数タブ**：各タブが独立の JS ランタイム → 独立したストア。1 タブでログイン中に別タブで別ユーザーがログイン、というシナリオは想定不要（管理者専用の SPA なので）。
- **React StrictMode 二重マウント**：`useMemo` の factory は初回レンダー時に 1 回だけ実行される。StrictMode で概念的な二重マウントが発生しても、`consume` は 1 回。ただし StrictMode で完全に 1 回であることを保証したい場合は `useRef` の方が堅牢。現状の `App.tsx` に StrictMode を使っている場合、リグレッションテストで確認が必要（本セッションでは動作確認済み）。

### 代替設計の検討

| 代替案                                                | 却下理由                                                                                                                      |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| React Context Provider に challenge を保持            | Provider の再マウントで喪失、`/new-password` 単体で使うにはオーバーエンジニアリング                                           |
| SessionStorage に serialize                           | Function は serialize 不可、代替として challenge を「認証セッション参照 ID」で表現しなおす設計が必要（既存 SDK の抽象を破る） |
| challenge を CognitoUser インスタンスに紐付けて再取得 | SDK 内部状態依存で fragile、ε-2 修正の趣旨（型で表現）に反する                                                                |
| **モジュールスコープの一時ストア（採用）**            | シンプル、DRY、SDK 抽象を維持、テスト容易                                                                                     |
