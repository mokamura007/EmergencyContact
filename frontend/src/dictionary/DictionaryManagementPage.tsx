/**
 * キーワード辞書管理ページ（Phase 10.9、Requirement 8.1〜8.4 / 8.7）。
 *
 * 表示要素：
 *   - ページヘッダ（ダッシュボード戻りリンクは AdminLayout 共通ヘッダで提供）
 *   - 現在の辞書バージョン番号（META.currentVersion）
 *   - 3 カテゴリ（SAFE / INJURED / UNAVAILABLE）別テーブル
 *     - 各行：キーワード + touch（バージョン更新）ボタン + 無効化（DELETE）ボタン
 *     - 各カテゴリ下：追加フォーム（テキスト入力 + 追加ボタン）
 *
 * 操作セマンティクス（案 B：キーワード文字列の編集 UI は提供しない）：
 *   - **追加（POST）**：新規キーワード文字列を追加。
 *   - **無効化（DELETE）**：既存キーワード文字列を削除。
 *   - **touch（PATCH）**：既存キーワード文字列をそのままに version を進める。
 *     キーワード文字列の編集は「DELETE → POST」の 2 ステップ運用とし、
 *     UI には専用編集フィールドを提供しない。design.md「PATCH = 有効
 *     フラグ更新」と handler.py「PATCH = version stamp only」のズレは
 *     ADR-0006 で記録、Phase 10.9 では touch ボタンとして UI 提示する。
 *
 * 409 Conflict ハンドリング：
 *   - `DictionaryConflictError` を捕捉した時点で、自動で `list()` を
 *     再取得し最新スナップショットを表示。バナーで「他の管理者が辞書を
 *     更新しました。最新の状態を表示します。再度操作してください。」を表示。
 *   - その他の HTTP エラーは `DictionaryApiError.serverMessage` を
 *     `role="alert"` バナーに開示（19原則(b) 準拠、握り潰さない）。
 *
 * 設計判断：
 *   - `DictionaryClient` を props DI（テスト容易化、`InboundListPage` 同型）。
 *   - 操作中（add/remove/touch/list）は全ボタンを disabled にして連打防止。
 *   - useEffect 内でマウント時に list() を 1 回発火（cleanup で cancelled
 *     フラグを立てて unmount 後 setState 抑止）。
 *   - 19原則(a) DRY：エラー翻訳は専用 `translateError` ヘルパに集約。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
  type JSX,
} from 'react';

import {
  DictionaryApiError,
  DictionaryClient,
  DictionaryConflictError,
  VALID_CATEGORIES,
  type DictionaryCategory,
  type DictionarySnapshot,
} from '../api/dictionaryClient';

export interface DictionaryManagementPageProps {
  /** テスト DI：未指定なら `new DictionaryClient()`。 */
  readonly dictionaryClient?: DictionaryClient;
}

const CATEGORY_LABELS: Readonly<Record<DictionaryCategory, string>> = {
  SAFE: 'SAFE(無事)',
  INJURED: 'INJURED(怪我)',
  UNAVAILABLE: 'UNAVAILABLE(行動不能)',
};

const CONFLICT_BANNER_MESSAGE =
  '他の管理者が辞書を更新しました。最新の状態を表示します。再度操作してください。';

export function DictionaryManagementPage(props: DictionaryManagementPageProps = {}): JSX.Element {
  const client = useMemo(
    () => props.dictionaryClient ?? new DictionaryClient(),
    [props.dictionaryClient],
  );

  const [snapshot, setSnapshot] = useState<DictionarySnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [conflictBanner, setConflictBanner] = useState(false);
  const [drafts, setDrafts] = useState<Readonly<Record<DictionaryCategory, string>>>({
    SAFE: '',
    INJURED: '',
    UNAVAILABLE: '',
  });

  const reloadSnapshot = useCallback(
    async (options: { readonly preserveConflictBanner?: boolean } = {}): Promise<void> => {
      try {
        const next = await client.list();
        setSnapshot(next);
        if (options.preserveConflictBanner !== true) {
          setConflictBanner(false);
        }
      } catch (err) {
        setErrorMessage(translateError(err, '辞書の取得に失敗しました'));
      }
    },
    [client],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErrorMessage(null);
    const safeAction = (fn: () => void): void => {
      if (cancelled) return;
      fn();
    };
    void (async () => {
      try {
        const next = await client.list();
        safeAction(() => {
          setSnapshot(next);
        });
      } catch (err) {
        safeAction(() => {
          setErrorMessage(translateError(err, '辞書の取得に失敗しました'));
        });
      } finally {
        safeAction(() => {
          setLoading(false);
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  /**
   * 任意の mutation を実行し、競合検出 + 再取得 + バナー制御を共通化する。
   */
  const runMutation = useCallback(
    async (
      mutate: (expectedVersion: number) => Promise<{ readonly version: number }>,
      onSuccess?: () => void,
    ): Promise<void> => {
      if (snapshot === null) return;
      setBusy(true);
      setErrorMessage(null);
      try {
        await mutate(snapshot.version);
        setConflictBanner(false);
        if (onSuccess !== undefined) {
          onSuccess();
        }
        await reloadSnapshot();
      } catch (err) {
        if (err instanceof DictionaryConflictError) {
          setConflictBanner(true);
          await reloadSnapshot({ preserveConflictBanner: true });
        } else {
          setErrorMessage(translateError(err, '辞書の更新に失敗しました'));
        }
      } finally {
        setBusy(false);
      }
    },
    [snapshot, reloadSnapshot],
  );

  const handleDraftChange = useCallback(
    (category: DictionaryCategory) => (event: ChangeEvent<HTMLInputElement>) => {
      const value = event.currentTarget.value;
      setDrafts((prev) => ({ ...prev, [category]: value }));
    },
    [],
  );

  const handleRemove = useCallback(
    (category: DictionaryCategory, keyword: string) => () => {
      void runMutation((expectedVersion) => client.remove(category, keyword, expectedVersion));
    },
    [client, runMutation],
  );

  const handleTouch = useCallback(
    (category: DictionaryCategory, keyword: string) => () => {
      void runMutation((expectedVersion) => client.touch(category, keyword, expectedVersion));
    },
    [client, runMutation],
  );

  const submitAdd = useCallback(
    (category: DictionaryCategory) =>
      (event: FormEvent<HTMLFormElement>): void => {
        event.preventDefault();
        const trimmed = drafts[category].trim();
        if (trimmed === '') {
          setErrorMessage('追加するキーワードを入力してください。');
          return;
        }
        void runMutation(
          (expectedVersion) => client.add(category, trimmed, expectedVersion),
          () => {
            setDrafts((prev) => ({ ...prev, [category]: '' }));
          },
        );
      },
    [client, drafts, runMutation],
  );

  return (
    <section>
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
        }}
      >
        <h1>キーワード辞書管理</h1>
      </header>

      {conflictBanner && (
        <p
          role="alert"
          data-testid="dictionary-conflict-banner"
          style={{
            color: '#92400e',
            background: '#fef3c7',
            padding: '0.5rem',
            border: '1px solid #fbbf24',
          }}
        >
          {CONFLICT_BANNER_MESSAGE}
        </p>
      )}

      {errorMessage !== null && (
        <p role="alert" style={{ color: '#b91c1c' }} data-testid="dictionary-error">
          {errorMessage}
        </p>
      )}

      {loading ? (
        <p role="status" aria-live="polite">
          読み込み中…
        </p>
      ) : snapshot === null ? (
        <p data-testid="dictionary-empty-fallback">辞書を取得できませんでした。</p>
      ) : (
        <>
          <p data-testid="dictionary-version">
            現在の辞書バージョン: <strong>{snapshot.version.toString()}</strong>
          </p>
          {VALID_CATEGORIES.map((category) => (
            <CategorySection
              key={category}
              category={category}
              keywords={snapshot.categories[category]}
              draft={drafts[category]}
              busy={busy}
              onChangeDraft={handleDraftChange(category)}
              onSubmitAdd={submitAdd(category)}
              onRemove={handleRemove}
              onTouch={handleTouch}
            />
          ))}
        </>
      )}
    </section>
  );
}

function CategorySection({
  category,
  keywords,
  draft,
  busy,
  onChangeDraft,
  onSubmitAdd,
  onRemove,
  onTouch,
}: {
  readonly category: DictionaryCategory;
  readonly keywords: readonly string[];
  readonly draft: string;
  readonly busy: boolean;
  readonly onChangeDraft: (event: ChangeEvent<HTMLInputElement>) => void;
  readonly onSubmitAdd: (event: FormEvent<HTMLFormElement>) => void;
  readonly onRemove: (category: DictionaryCategory, keyword: string) => () => void;
  readonly onTouch: (category: DictionaryCategory, keyword: string) => () => void;
}): JSX.Element {
  return (
    <section data-testid={`dictionary-category-${category}`} style={{ marginBottom: '1.5rem' }}>
      <h2>{CATEGORY_LABELS[category]}</h2>
      {keywords.length === 0 ? (
        <p data-testid={`dictionary-empty-${category}`}>
          このカテゴリにキーワードはまだありません。
        </p>
      ) : (
        <table style={tableStyle} data-testid={`dictionary-table-${category}`}>
          <thead>
            <tr>
              <th style={cellStyle}>キーワード</th>
              <th style={cellStyle}>操作</th>
            </tr>
          </thead>
          <tbody>
            {keywords.map((keyword) => (
              <tr key={keyword} data-testid={`dictionary-row-${category}-${keyword}`}>
                <td style={cellStyle}>{keyword}</td>
                <td style={cellStyle}>
                  <button
                    type="button"
                    onClick={onTouch(category, keyword)}
                    disabled={busy}
                    data-testid={`dictionary-touch-${category}-${keyword}`}
                  >
                    バージョン更新(touch)
                  </button>{' '}
                  <button
                    type="button"
                    onClick={onRemove(category, keyword)}
                    disabled={busy}
                    data-testid={`dictionary-remove-${category}-${keyword}`}
                  >
                    無効化
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        onSubmit={onSubmitAdd}
        style={{ marginTop: '0.5rem' }}
        data-testid={`dictionary-add-form-${category}`}
      >
        <label>
          新規キーワード：
          <input
            type="text"
            value={draft}
            onChange={onChangeDraft}
            disabled={busy}
            data-testid={`dictionary-add-input-${category}`}
            style={{ marginLeft: '0.5rem' }}
          />
        </label>{' '}
        <button type="submit" disabled={busy} data-testid={`dictionary-add-button-${category}`}>
          追加
        </button>
      </form>
    </section>
  );
}

/**
 * 例外を画面表示用文字列へ翻訳する。19原則(b)：握り潰さず serverMessage を開示。
 */
function translateError(err: unknown, prefix: string): string {
  if (err instanceof DictionaryApiError) {
    return `${prefix}(HTTP ${err.status.toString()}): ${err.serverMessage}`;
  }
  if (err instanceof Error) {
    return `${prefix}: ${err.message}`;
  }
  return `${prefix}。`;
}

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
};

const cellStyle: React.CSSProperties = {
  border: '1px solid #d1d5db',
  padding: '0.5rem',
  textAlign: 'left',
  verticalAlign: 'top',
};
