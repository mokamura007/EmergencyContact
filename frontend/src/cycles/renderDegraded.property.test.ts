/**
 * Property 25：縮退表示（Phase 13.25）。
 *
 * **Validates: Requirements 18.4**
 *
 * design.md の定義：
 *   _For all_ 縮退情報配列 `D = [{component, since}, ...]` について、
 *   Status_Viewer の `renderDegraded(D)` の出力には `D` 中のすべての
 *   `component` 名が含まれ、`D = []` のときに限り出力にはいかなる
 *   コンポーネント名も含まれない。
 *
 * Done When（tasks.md 13.25）：
 *   - 配列内の全 component 名が出力されている。
 *   - 空配列で出力にコンポーネント名が出ない。
 *
 * 実装対象：`frontend/src/cycles/statusReducer.ts::renderDegraded`
 * （Phase 10.6 で Property 25 受け皿として実装済の純粋関数）。
 *
 * テスト戦略：
 *   - fast-check で 100 イテレーション（design.md PBT 設定準拠）。
 *   - 入力は `CycleDegradedEntry[]`（`{component: string, since: ISO8601}`）。
 *   - component は任意の Unicode 文字列を許容（空文字含む）。空文字は API
 *     スキーマ上稀だが、Property の不変式は文字列同値性のみに依存するため
 *     PBT 観点で除外する必要はない。
 *   - since は ISO 8601 風文字列で生成（PBT 観点では `renderDegraded` は
 *     since を参照しないが、入力の型を満たす目的で生成する）。
 *
 * 注意：本 PBT は `renderDegraded` の戻り値が `readonly string[]` であるため、
 *      「出力に component 名が含まれる」を「戻り配列に component 文字列が
 *      要素として存在する」と解釈する。これは renderDegraded の契約：
 *      「コンポーネント名の配列を返す」と整合する。
 */

import { describe, it } from 'vitest';
import fc from 'fast-check';

import { renderDegraded, type CycleDegradedEntry } from './statusReducer';

/** Property 25 用の入力アービトラリ：CycleDegradedEntry。 */
const degradedEntryArb: fc.Arbitrary<CycleDegradedEntry> = fc.record({
  component: fc.string(),
  since: fc
    .date({ min: new Date('2020-01-01T00:00:00Z'), max: new Date('2030-12-31T23:59:59Z') })
    .map((d) => d.toISOString()),
});

describe('Property 25: 縮退表示 (renderDegraded)', () => {
  it('property: D 中のすべての component 名が出力に含まれる', () => {
    fc.assert(
      fc.property(fc.array(degradedEntryArb), (degraded) => {
        const output = renderDegraded(degraded);
        // (1) 出力長は入力長と一致（順序保持の構造的同値性確認）。
        if (output.length !== degraded.length) {
          return false;
        }
        // (2) 各エントリの component 名が、対応する出力要素として存在する。
        for (let i = 0; i < degraded.length; i += 1) {
          const entry = degraded[i];
          const outElem = output[i];
          if (entry === undefined || outElem === undefined) {
            // length 一致を確認済のため通常到達不能。型ガードのみの分岐。
            return false;
          }
          if (outElem !== entry.component) {
            return false;
          }
        }
        // (3) 「すべての component 名が出力に含まれる」を集合包含として
        //     も検証（順序保持に依らない弱い性質も並行確認）。
        const outSet = new Set(output);
        for (const entry of degraded) {
          if (!outSet.has(entry.component)) {
            return false;
          }
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('property: D = [] のときに限り出力にいかなるコンポーネント名も含まれない', () => {
    // 等価性「D = [] ⇔ 出力に component 名なし」を双方向で検証（第 17 原則）。
    fc.assert(
      fc.property(fc.array(degradedEntryArb), (degraded) => {
        const output = renderDegraded(degraded);
        const isEmptyInput = degraded.length === 0;
        const hasNoComponentNamesInOutput = output.length === 0;
        // (forward) D = [] ⇒ 出力に component 名が一切ない。
        // (backward) 出力に component 名が一切ない ⇒ D = [] でなければならない。
        //   （renderDegraded は入力 1 要素ごとに 1 要素を出力するため、
        //     出力が空であることと入力が空であることは同値。）
        return isEmptyInput === hasNoComponentNamesInOutput;
      }),
      { numRuns: 100 },
    );
  });
});
