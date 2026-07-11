/**
 * Cognito User Pool（USER_SRP_AUTH）ベースの `AuthSessionProvider` 実装。
 *
 * 対応要件：
 *   - Requirement 1.1 / 1.2 : TLS 上で Cognito ユーザープールに対して
 *     SRP 認証を行い、ID/Access/Refresh トークンを取得する。
 *   - Requirement 1.9       : 一般社員ロールは存在しないため、本クラスは
 *     Cognito ユーザープールへ "管理者として" 認証することしか想定しない。
 *     ロール確認（`cognito:groups` に `Administrator` を含むか）は本層では
 *     行わず、ルーティング層（Phase 10.3）の責務とする。
 *   - Done When             : ログイン → API 呼出 → トークン期限切れ →
 *     自動リフレッシュが動作。`getCurrentSession` 内で
 *     `CognitoUser.getSession` が refresh token による自動更新を行うため、
 *     呼び出し側はリフレッシュを意識せずに最新セッションを取得できる。
 *
 * 設計判断：
 *   - `amazon-cognito-identity-js` の `CognitoUserPool` / `CognitoUser` は
 *     `localStorage` に refresh token を保存する標準ストレージを採用する。
 *     SSE / XSS 緩和は CSP / CloudFront 配信ヘッダ側で別途実施するため、
 *     ここでは標準ストレージのまま使う（DRY、SDK 既定挙動踏襲）。
 *   - SDK のコールバック地獄を Promise に閉じ込めるが、内部例外は薄く
 *     ラップして `AuthenticationFailedError` 等で再送する（19原則 (b)）。
 */

import { AuthenticationDetails, CognitoUser, CognitoUserPool } from 'amazon-cognito-identity-js';
import type { CognitoUserSession, ICognitoUserPoolData } from 'amazon-cognito-identity-js';

import { getEnv } from '../config/env';

import { AuthenticationFailedError, MissingAuthConfigError, SessionExpiredError } from './errors';
import type {
  AuthSessionProvider,
  NewPasswordRequiredChallenge,
  SignInResult,
  TokenSet,
} from './types';

/**
 * SDK が投げてくる素の Error から、Cognito 固有の `name`/`code` を抽出する。
 * 該当しない場合は 'UnknownError' を返す（フォールバックではなく分類のみ）。
 */
function extractCognitoErrorCode(err: unknown): string {
  if (err !== null && typeof err === 'object') {
    const record = err as Record<string, unknown>;
    if (typeof record.code === 'string') return record.code;
    if (typeof record.name === 'string') return record.name;
  }
  return 'UnknownError';
}

function toTokenSet(session: CognitoUserSession): TokenSet {
  const idToken = session.getIdToken();
  const accessToken = session.getAccessToken();
  // expiration は unix 秒。
  // CognitoIdToken / CognitoAccessToken はどちらも getExpiration を持つ。
  // ID トークンの期限を採用（API 認可に使うのが ID トークンのため）。
  const expiresAtEpochSeconds = idToken.getExpiration();
  return {
    idToken: idToken.getJwtToken(),
    accessToken: accessToken.getJwtToken(),
    expiresAtEpochSeconds,
  };
}

export interface CognitoAuthProviderConfig {
  readonly userPoolId: string;
  readonly clientId: string;
}

/**
 * 環境変数（getEnv()）から `CognitoAuthProviderConfig` を作る。
 * 未設定値があれば `MissingAuthConfigError` を投げる（fail-fast）。
 */
export function loadCognitoAuthConfigFromEnv(): CognitoAuthProviderConfig {
  const env = getEnv();
  if (!env.cognitoUserPoolId) {
    throw new MissingAuthConfigError('VITE_COGNITO_USER_POOL_ID');
  }
  if (!env.cognitoClientId) {
    throw new MissingAuthConfigError('VITE_COGNITO_CLIENT_ID');
  }
  return {
    userPoolId: env.cognitoUserPoolId,
    clientId: env.cognitoClientId,
  };
}

/**
 * `CognitoUserPool` 生成を関数化（テスト時に DI で差し替え可能にするため）。
 */
export type CognitoUserPoolFactory = (data: ICognitoUserPoolData) => CognitoUserPool;

const defaultUserPoolFactory: CognitoUserPoolFactory = (data) => new CognitoUserPool(data);

export class CognitoAuthProvider implements AuthSessionProvider {
  private readonly pool: CognitoUserPool;

  public constructor(
    config: CognitoAuthProviderConfig,
    userPoolFactory: CognitoUserPoolFactory = defaultUserPoolFactory,
  ) {
    this.pool = userPoolFactory({
      UserPoolId: config.userPoolId,
      ClientId: config.clientId,
    });
  }

  public signIn(email: string, password: string): Promise<SignInResult> {
    const cognitoUser = new CognitoUser({ Username: email, Pool: this.pool });
    const authDetails = new AuthenticationDetails({ Username: email, Password: password });

    return new Promise<SignInResult>((resolve, reject) => {
      cognitoUser.authenticateUser(authDetails, {
        onSuccess: (session) => {
          resolve({ kind: 'SUCCESS', tokens: toTokenSet(session) });
        },
        onFailure: (err: unknown) => {
          const code = extractCognitoErrorCode(err);
          const message = err instanceof Error ? err.message : 'Authentication failed';
          reject(new AuthenticationFailedError(message, code, err));
        },
        newPasswordRequired: (userAttributes, requiredAttributes) => {
          // ε-2 修正（15.2a 実機検証）：FORCE_CHANGE_PASSWORD ユーザーの
          // 初回ログインを UI フローで完了させるため、challenge を例外
          // ではなく型として SignInResult に乗せる。`complete(newPassword)`
          // が呼ばれた時点で、同一 CognitoUser に対して
          // `completeNewPasswordChallenge` を行う。
          //
          // 注意：amazon-cognito-identity-js の API は
          // issue #3 6 巡目訂正：`newPasswordRequired` コールバックが返す
          // `userAttributes` を `completeNewPasswordChallenge` に渡すのは
          // Amplify の "SDK 慣例" として広く紹介されているが、実機 dev 環境
          // で試したところ、Cognito から `NotAuthorizedException` が返って
          // ユーザーは `FORCE_CHANGE_PASSWORD` のまま更新されなかった。
          //
          // 原因仮説：`userAttributes` には `sub` などの immutable な
          // サーバー生成属性が含まれる場合があり、これを含む更新試行を
          // Cognito が拒否している可能性が高い（`email_verified` /
          // `phone_number_verified` を delete するだけでは不十分）。
          //
          // SPA は初回パスワード変更時に追加属性の入力を要求しない仕様
          // （`requiredAttributes` が非空の場合はエラーで即拒否）なので、
          // 更新すべき属性は無い。よって `{}` を渡す。
          //
          // 参照：`docs/notes/fix-initial-login-flow-verification.md` §14.5,
          // Issue #3 コメント履歴（6 巡目）。
          //
          // なお、`userAttributes` / `requiredAttributes` は将来「追加属性
          // 入力 UI」が必要になった際に再利用可能なため、コールバック引数
          // としては受け取り続ける（引数名先頭に `_` を付けて未使用を明示）。
          const _userAttributes = userAttributes;
          void _userAttributes;
          const challenge: NewPasswordRequiredChallenge = {
            kind: 'NEW_PASSWORD_REQUIRED',
            complete: (newPassword: string): Promise<TokenSet> => {
              return new Promise<TokenSet>((resolveComplete, rejectComplete) => {
                if (requiredAttributes.length > 0) {
                  rejectComplete(
                    new AuthenticationFailedError(
                      `Cognito requires additional user attributes (${requiredAttributes.join(', ')}) ` +
                        `which this SPA does not support. Contact administrator.`,
                      'RequiredAttributesUnsupported',
                    ),
                  );
                  return;
                }
                cognitoUser.completeNewPasswordChallenge(
                  newPassword,
                  {},
                  {
                    onSuccess: (session) => {
                      resolveComplete(toTokenSet(session));
                    },
                    onFailure: (err: unknown) => {
                      const code = extractCognitoErrorCode(err);
                      const message =
                        err instanceof Error ? err.message : 'New password challenge failed';
                      rejectComplete(new AuthenticationFailedError(message, code, err));
                    },
                  },
                );
              });
            },
          };
          resolve(challenge);
        },
      });
    });
  }

  public getCurrentSession(): Promise<TokenSet | null> {
    const cognitoUser = this.pool.getCurrentUser();
    if (cognitoUser === null) {
      return Promise.resolve(null);
    }

    return new Promise<TokenSet | null>((resolve, reject) => {
      // `getSession` は ID/Access が切れていても refresh token が有効
      // ならば自動でリフレッシュして新しい session を返す（SDK 仕様）。
      cognitoUser.getSession((err: Error | null, session: CognitoUserSession | null) => {
        if (err) {
          // refresh token も切れている / ネットワークエラー等。
          // 「セッション失効」として 401 ハンドリングに乗せる。
          reject(new SessionExpiredError(err.message, err));
          return;
        }
        if (!session?.isValid()) {
          // SDK が refresh しても無効セッションしか得られないケース。
          // 19原則 (b) に従いフォールバックせず、失効として扱う。
          resolve(null);
          return;
        }
        resolve(toTokenSet(session));
      });
    });
  }

  public signOut(): Promise<void> {
    const cognitoUser = this.pool.getCurrentUser();
    if (cognitoUser === null) {
      // 既にサインアウト済。冪等とする。
      return Promise.resolve();
    }
    // `signOut` はサーバー側 refresh token を revoke する（v6 系の挙動）。
    return new Promise<void>((resolve) => {
      cognitoUser.signOut(() => {
        resolve();
      });
    });
  }
}
