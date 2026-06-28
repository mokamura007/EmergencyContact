/**
 * Vitest setup file. テストランナー起動時の前処理を集約する。
 *
 * 設定：
 *   - `@testing-library/jest-dom` のカスタムマッチャを Vitest 用に拡張する。
 *     `toBeInTheDocument` などのアサーションが利用可能になる。
 *   - 各テスト終了時に React Testing Library の DOM をクリーンアップする
 *     （Vitest は Jest と異なり自動クリーンアップが効かないため明示する）。
 *   - jsdom の `Blob`/`File` には `arrayBuffer` 実装が無いため、Node の
 *     `Response` をラッパとした polyfill を一律にあてる。本ポリフィルは
 *     ブラウザ実装と同じ仕様（Promise<ArrayBuffer> を返す）であり、
 *     `EmployeeCsvImportPage` などのファイル読込テストで参照される。
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});

if (typeof Blob !== 'undefined') {
  const polyfill = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (): void => {
        const result = reader.result;
        if (result instanceof ArrayBuffer) {
          resolve(result);
        } else {
          reject(new Error('FileReader did not return ArrayBuffer'));
        }
      };
      reader.onerror = (): void => {
        reject(reader.error ?? new Error('FileReader failed'));
      };
      reader.readAsArrayBuffer(this);
    });
  };
  // jsdom 25 では File が Blob.prototype の `arrayBuffer` を継承しない
  // ケースがあるため、Blob と File 双方に直接ぶら下げる。
  Object.defineProperty(Blob.prototype, 'arrayBuffer', {
    value: polyfill,
    writable: true,
    configurable: true,
  });
  if (typeof File !== 'undefined') {
    Object.defineProperty(File.prototype, 'arrayBuffer', {
      value: polyfill,
      writable: true,
      configurable: true,
    });
  }
}
