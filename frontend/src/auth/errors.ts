/**
 * 認証層で発生する代表的なエラー型。
 *
 * 19原則 (b)（フォールバック禁止 / エラーはエラーのまま返す）に従い、
 * Cognito SDK の native エラーは `cause` に保持しつつ、SPA 内では
 * 型で識別可能な独自エラーに薄くラップして再送する。
 */

export class AuthenticationFailedError extends Error {
  public override readonly name = 'AuthenticationFailedError';
  public readonly code: string;

  /**
   * @param code Cognito の `__type` / `code`（例: `NotAuthorizedException`,
   *             `UserNotFoundException`, `PasswordResetRequiredException`）。
   *             UI 側はこの値でメッセージ分岐できる。
   */
  public constructor(message: string, code: string, cause?: unknown) {
    super(message);
    this.code = code;
    if (cause !== undefined) {
      // ES2022 `Error.cause` 標準。tsconfig が ES2022 のため利用可能。
      (this as { cause?: unknown }).cause = cause;
    }
  }
}

export class NewPasswordRequiredError extends Error {
  public override readonly name = 'NewPasswordRequiredError';

  public constructor() {
    super('A new password is required for this user.');
  }
}

export class SessionExpiredError extends Error {
  public override readonly name = 'SessionExpiredError';

  public constructor(message = 'Authentication session has expired.', cause?: unknown) {
    super(message);
    if (cause !== undefined) {
      (this as { cause?: unknown }).cause = cause;
    }
  }
}

export class MissingAuthConfigError extends Error {
  public override readonly name = 'MissingAuthConfigError';

  public constructor(missingKey: string) {
    super(
      `Cognito configuration is missing: "${missingKey}". ` +
        `Set the corresponding VITE_* environment variable before building the SPA.`,
    );
  }
}
