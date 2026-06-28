/**
 * CSV インポート画面（管理者画面 / Phase 10.4）。
 *
 * 対応要件：
 *   - Requirement 3.1〜3.7 : UTF-8 / ヘッダ必須 / 300 行 / 1 MiB / 行レベル
 *     バリデーション / 全件ロールバック / 試行・成功・失敗 3 値の画面表示。
 *
 * 設計判断：
 *   - 送信前にクライアント側で `validateCsvFile` を実行し、ファイルレベル
 *     違反が確実な場合は API を呼ばずに即時エラー表示する（無駄な PUT を防ぐ）。
 *   - 成功・失敗にかかわらずバックエンドからの imported / attempted / errors
 *     3 値を取り出して画面表示する（Done When「CSV インポート結果が画面表示される」）。
 *   - `File.arrayBuffer()` で生バイト列を取得し base64 変換して送信。
 *     `csvBase64` フィールドはバックエンド `handler._import_csv` と一致。
 *   - 19原則(b)：バックエンドが返した行番号 + 理由はそのまま表示する。
 *     翻訳テーブルを SPA に持たせない（仕様の DRY 維持）。
 */

import { useCallback, useMemo, useState, type ChangeEvent, type JSX } from 'react';
import { Link } from 'react-router-dom';

import {
  EmployeeApiError,
  EmployeeClient,
  type ImportCsvError,
  type ImportCsvResult,
} from '../api/employeeClient';

import {
  CSV_MAX_BYTES,
  CSV_MAX_DATA_ROWS,
  CSV_REQUIRED_HEADER,
  encodeBase64,
  validateCsvFile,
} from './validation';

export interface EmployeeCsvImportPageProps {
  /** テスト DI：未指定なら `new EmployeeClient()`。 */
  readonly client?: EmployeeClient;
}

interface ImportStatus {
  readonly attempted: number;
  readonly imported: number;
  readonly failed: number;
  readonly errors: readonly ImportCsvError[];
  readonly isSuccess: boolean;
}

export function EmployeeCsvImportPage({ client }: EmployeeCsvImportPageProps = {}): JSX.Element {
  const employeeClient = useMemo(() => client ?? new EmployeeClient(), [client]);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preflightErrors, setPreflightErrors] = useState<readonly string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportStatus | null>(null);

  const onFileChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setPreflightErrors([]);
    setServerError(null);
    setResult(null);
  }, []);

  const onSubmit = useCallback(() => {
    if (selectedFile === null) return;
    setSubmitting(true);
    setServerError(null);
    setPreflightErrors([]);
    setResult(null);

    void (async () => {
      try {
        const buf = await selectedFile.arrayBuffer();
        const bytes = new Uint8Array(buf);
        const preflight = validateCsvFile(bytes);
        if (!preflight.ok) {
          setPreflightErrors(preflight.errors);
          return;
        }

        const csvBase64 = encodeBase64(bytes);
        const apiResult = await employeeClient.importCsv(csvBase64);
        setResult(buildStatus(apiResult, true));
      } catch (err) {
        if (err instanceof EmployeeApiError) {
          // 400/409/500 等、バックエンドが imported/attempted/errors を返している場合は表示。
          const apiResult = err.importErrors
            ? ({ imported: 0, attempted: 0, errors: err.importErrors } satisfies ImportCsvResult)
            : null;
          setServerError(`HTTP ${err.status.toString()}: ${err.serverMessage}`);
          if (apiResult !== null) {
            setResult(buildStatus(apiResult, false));
          }
        } else if (err instanceof Error) {
          setServerError(err.message);
        } else {
          setServerError('インポートに失敗しました。');
        }
      } finally {
        setSubmitting(false);
      }
    })();
  }, [selectedFile, employeeClient]);

  return (
    <section>
      <header style={{ marginBottom: '1rem' }}>
        <h1>社員マスタ CSV インポート</h1>
        <p>
          UTF-8 / ヘッダ行 <code>{CSV_REQUIRED_HEADER}</code> / データ行は最大{' '}
          {CSV_MAX_DATA_ROWS.toString()} 行 / サイズ {CSV_MAX_BYTES.toString()} バイト以下。
        </p>
        <p>
          <Link to="/employees">← 社員一覧へ戻る</Link>
        </p>
      </header>

      <div style={{ marginBottom: '1rem' }}>
        <label htmlFor="csv-file" style={{ display: 'block', marginBottom: '0.25rem' }}>
          CSV ファイル
        </label>
        <input
          id="csv-file"
          name="csvFile"
          type="file"
          accept=".csv,text/csv"
          onChange={onFileChange}
        />
      </div>

      <button
        type="button"
        onClick={onSubmit}
        disabled={selectedFile === null || submitting}
        style={{ padding: '0.5rem 1rem' }}
      >
        {submitting ? 'インポート中…' : 'インポート実行'}
      </button>

      {preflightErrors.length > 0 && (
        <div role="alert" style={{ color: '#b91c1c', marginTop: '1rem' }}>
          <p>ファイルレベルの検査に失敗しました:</p>
          <ul>
            {preflightErrors.map((msg) => (
              <li key={msg}>{msg}</li>
            ))}
          </ul>
        </div>
      )}

      {serverError !== null && (
        <p role="alert" style={{ color: '#b91c1c', marginTop: '1rem' }}>
          サーバーエラー: {serverError}
        </p>
      )}

      {result !== null && (
        <section aria-labelledby="csv-import-result-title" style={{ marginTop: '1.5rem' }}>
          <h2 id="csv-import-result-title">インポート結果</h2>
          <p>
            <span data-testid="result-attempted">試行件数: {result.attempted.toString()}</span>
            {' / '}
            <span data-testid="result-imported">成功件数: {result.imported.toString()}</span>
            {' / '}
            <span data-testid="result-failed">失敗件数: {result.failed.toString()}</span>
            {result.isSuccess ? '（成功）' : '（失敗）'}
          </p>
          {result.errors.length > 0 && (
            <div>
              <p>失敗行詳細：</p>
              <ul>
                {result.errors.map((e, idx) => (
                  <li key={`${(e.line ?? -1).toString()}-${idx.toString()}`}>
                    {e.line !== undefined ? `行 ${e.line.toString()}: ` : ''}
                    {e.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </section>
  );
}

function buildStatus(api: ImportCsvResult, isSuccess: boolean): ImportStatus {
  const failed = Math.max(api.attempted - api.imported, api.errors.length);
  return {
    attempted: api.attempted,
    imported: api.imported,
    failed,
    errors: api.errors,
    isSuccess,
  };
}
