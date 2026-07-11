/**
 * `translateLoginError` 純粋関数のテスト。
 *
 * Validates: Requirements 2.2 / 2.3 / 2.4 / 2.5
 *
 * カバレッジ方針：エラー分類表（design.md）に列挙された既知コード全 6 種、
 * 未知コード、`MissingAuthConfigError`、`NewPasswordRequiredError`、
 * 非 Error 値（string / null / undefined / plain object）を網羅する。
 */

import { describe, expect, it } from 'vitest';

import {
  AuthenticationFailedError,
  MissingAuthConfigError,
  NewPasswordRequiredError,
} from '../auth/errors';

import { extractErrorIdentifier, translateLoginError } from './loginErrors';

describe('translateLoginError', () => {
  describe('MissingAuthConfigError', () => {
    it('専用メッセージを返す（Requirement 2.3）', () => {
      const err = new MissingAuthConfigError('VITE_COGNITO_USER_POOL_ID');
      expect(translateLoginError(err)).toBe('認証設定が未構成です。管理者に連絡してください。');
    });
  });

  describe('AuthenticationFailedError — 既知コード（Requirement 2.4）', () => {
    it('NotAuthorizedException は非特定の資格情報エラーメッセージ', () => {
      const err = new AuthenticationFailedError(
        'Incorrect username or password.',
        'NotAuthorizedException',
      );
      expect(translateLoginError(err)).toBe('メールアドレスまたはパスワードが正しくありません。');
    });

    it('UserNotFoundException も NotAuthorized と同じ非特定メッセージ（列挙攻撃防止）', () => {
      const err = new AuthenticationFailedError('user not found', 'UserNotFoundException');
      expect(translateLoginError(err)).toBe('メールアドレスまたはパスワードが正しくありません。');
    });

    it('PasswordResetRequiredException は管理者連絡誘導メッセージ', () => {
      const err = new AuthenticationFailedError('reset required', 'PasswordResetRequiredException');
      expect(translateLoginError(err)).toBe(
        'パスワードのリセットが必要です。システム管理者にお問い合わせください。',
      );
    });

    it('UserLambdaValidationException は PostAuth 障害を示すメッセージ + コード併記', () => {
      const err = new AuthenticationFailedError(
        'PostAuthentication failed',
        'UserLambdaValidationException',
      );
      expect(translateLoginError(err)).toBe(
        'ログイン後処理でエラーが発生しました。システム管理者にお問い合わせください。（コード: UserLambdaValidationException）',
      );
    });

    it('TooManyRequestsException はレート制限メッセージ', () => {
      const err = new AuthenticationFailedError('rate limit', 'TooManyRequestsException');
      expect(translateLoginError(err)).toBe(
        '短時間に多くのリクエストが行われました。しばらく待って再度お試しください。',
      );
    });

    it('LimitExceededException もレート制限メッセージ', () => {
      const err = new AuthenticationFailedError('limit exceeded', 'LimitExceededException');
      expect(translateLoginError(err)).toBe(
        '短時間に多くのリクエストが行われました。しばらく待って再度お試しください。',
      );
    });

    it('UserNotConfirmedException はアカウント未確認メッセージ', () => {
      const err = new AuthenticationFailedError('not confirmed', 'UserNotConfirmedException');
      expect(translateLoginError(err)).toBe(
        'アカウントが有効化されていません。管理者にお問い合わせください。',
      );
    });
  });

  describe('AuthenticationFailedError — 未知コード（Requirement 2.2）', () => {
    it('未分類コードは汎用メッセージ + コード併記', () => {
      const err = new AuthenticationFailedError('boom', 'UnknownInternalError');
      expect(translateLoginError(err)).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: UnknownInternalError）',
      );
    });

    it('InvalidParameterException も未知経路でコード併記される（既知分岐に含めない）', () => {
      const err = new AuthenticationFailedError('invalid', 'InvalidParameterException');
      expect(translateLoginError(err)).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: InvalidParameterException）',
      );
    });

    it('ResourceNotFoundException も未知経路でコード併記される', () => {
      const err = new AuthenticationFailedError('not found', 'ResourceNotFoundException');
      expect(translateLoginError(err)).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: ResourceNotFoundException）',
      );
    });
  });

  describe('NewPasswordRequiredError — 隠れバグ検知', () => {
    it('ε-2 修正後は本経路に到達しないはずだが、旧経路混入時は矛盾メッセージを返す', () => {
      const err = new NewPasswordRequiredError();
      expect(translateLoginError(err)).toBe(
        'システム状態が矛盾しています。管理者にお問い合わせください。',
      );
    });
  });

  describe('予期しない値（Requirement 2.5、issue #3 再修正）', () => {
    it('string 値は汎用メッセージ + typeof 識別子（string）を併記', () => {
      expect(translateLoginError('unexpected string')).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: string）',
      );
    });

    it('null は汎用メッセージ + 識別子（null）を併記', () => {
      expect(translateLoginError(null)).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: null）',
      );
    });

    it('undefined は汎用メッセージ + 識別子（undefined）を併記', () => {
      expect(translateLoginError(undefined)).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: undefined）',
      );
    });

    it('plain object with code は code を識別子として採用', () => {
      expect(translateLoginError({ code: 'X' })).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: X）',
      );
    });

    it('Cognito SDK 生エラー風の __type オブジェクトは __type を識別子として採用', () => {
      expect(translateLoginError({ __type: 'com.amazon.coral.service#InternalServerError' })).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: com.amazon.coral.service#InternalServerError）',
      );
    });

    it('code / __type がない Error 派生は name を識別子として採用', () => {
      class CustomError extends Error {
        public override readonly name = 'CustomError';
      }
      expect(translateLoginError(new CustomError('x'))).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: CustomError）',
      );
    });

    it('code / __type / name いずれも空文字の Error は constructor.name を識別子として採用', () => {
      class NoNameError extends Error {
        public override readonly name = '';
      }
      expect(translateLoginError(new NoNameError('x'))).toBe(
        'ログインに失敗しました。時間をおいて再度お試しください。（コード: NoNameError）',
      );
    });
  });
});

describe('extractErrorIdentifier', () => {
  it('code を持つオブジェクトは code を返す', () => {
    expect(extractErrorIdentifier({ code: 'MyCode' })).toBe('MyCode');
  });

  it('code が無く __type を持つオブジェクトは __type を返す', () => {
    expect(extractErrorIdentifier({ __type: 'MyType' })).toBe('MyType');
  });

  it('code / __type が無く name を持つオブジェクトは name を返す', () => {
    expect(extractErrorIdentifier({ name: 'MyName' })).toBe('MyName');
  });

  it('code / __type / name が全て空文字なら constructor.name を返す', () => {
    class Foo {
      public code = '';
      public __type = '';
      public name = '';
    }
    expect(extractErrorIdentifier(new Foo())).toBe('Foo');
  });

  it('null → "null"', () => {
    expect(extractErrorIdentifier(null)).toBe('null');
  });

  it('undefined → "undefined"', () => {
    expect(extractErrorIdentifier(undefined)).toBe('undefined');
  });

  it('string → "string"', () => {
    expect(extractErrorIdentifier('hello')).toBe('string');
  });

  it('number → "number"', () => {
    expect(extractErrorIdentifier(42)).toBe('number');
  });

  it('code を優先する（__type / name より）', () => {
    expect(extractErrorIdentifier({ code: 'A', __type: 'B', name: 'C' })).toBe('A');
  });

  it('__type を優先する（name より）', () => {
    expect(extractErrorIdentifier({ __type: 'B', name: 'C' })).toBe('B');
  });
});
