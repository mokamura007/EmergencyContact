/**
 * 認証層の公開窓口（barrel）。
 *
 * SPA の他層からは本モジュール経由でのみ認証 API にアクセスする。
 * Cognito 実装の差替やテスト時のフェイク注入は `getAuthProvider` /
 * `setAuthProvider` を介して行う。
 */

import { CognitoAuthProvider, loadCognitoAuthConfigFromEnv } from './cognitoAuthProvider';
import type { AuthSessionProvider } from './types';

let providerInstance: AuthSessionProvider | null = null;

/**
 * シングルトンの `AuthSessionProvider` を返す。
 * 初回呼出時に環境変数から Cognito 設定を読み込んで構築する（遅延初期化）。
 */
export function getAuthProvider(): AuthSessionProvider {
  if (providerInstance === null) {
    const config = loadCognitoAuthConfigFromEnv();
    providerInstance = new CognitoAuthProvider(config);
  }
  return providerInstance;
}

/**
 * テスト用：プロバイダを差し替える。本番コードから呼んではいけない。
 *
 * `null` を渡すとシングルトンが再初期化対象に戻る（環境変数からの構築）。
 */
export function setAuthProviderForTest(provider: AuthSessionProvider | null): void {
  providerInstance = provider;
}

export type {
  AuthSessionProvider,
  NewPasswordRequiredChallenge,
  SignInResult,
  TokenSet,
} from './types';
export {
  AuthenticationFailedError,
  MissingAuthConfigError,
  NewPasswordRequiredError,
  SessionExpiredError,
} from './errors';
export {
  subscribeSessionExpired,
  notifySessionExpired,
  _resetSessionExpiredListenersForTest,
} from './sessionExpiredEvent';
export { CognitoAuthProvider, loadCognitoAuthConfigFromEnv } from './cognitoAuthProvider';
export { decodeJwtPayload, extractCognitoGroups, isAdministrator, type JwtClaims } from './roles';
