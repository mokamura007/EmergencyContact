/**
 * Amazon Transcribe 形式 JSON のパーサ（Phase 10.7、Requirement 12.2）。
 *
 * バックエンド `backend/shared/keyword/transcript.py::extract_transcript_payload` と
 * 同等の挙動：`results.transcripts[0].transcript` を本文として取り出し、
 * `results.items[*].alternatives[0].confidence` の算術平均を信頼度とする。
 *
 * 設計判断：
 *   - DOM 非依存の純粋関数として独立させ、fast-refresh 警告を避ける。
 *   - 19原則(b)：パース失敗は握り潰さず `Error` で throw。
 */

export interface ParsedTranscript {
  readonly text: string;
  readonly confidence: number | null;
}

/**
 * Transcribe 形式の JSON 文字列から本文 + 平均信頼度を抽出する。
 *
 * @param rawJson Amazon Transcribe が出力した JSON 全文。
 * @returns `{ text, confidence }`。信頼度は items が無い場合 null。
 * @throws JSON パース失敗 / 期待スキーマ不一致のとき Error。
 */
export function parseTranscript(rawJson: string): ParsedTranscript {
  const parsed = parseJson(rawJson);
  if (parsed === null || typeof parsed !== 'object') {
    throw new Error('Transcript JSON のルートが object ではありません。');
  }
  const root = parsed as Record<string, unknown>;
  const results = root.results;
  if (results === null || typeof results !== 'object') {
    throw new Error('Transcript JSON に results フィールドがありません。');
  }
  const resultsObj = results as Record<string, unknown>;
  const transcripts = resultsObj.transcripts;
  if (!isUnknownArray(transcripts) || transcripts.length === 0) {
    throw new Error('Transcript JSON の results.transcripts が空です。');
  }
  const first = transcripts[0];
  if (first === null || typeof first !== 'object') {
    throw new Error('Transcript JSON の results.transcripts[0] が object ではありません。');
  }
  const text = (first as Record<string, unknown>).transcript;
  if (typeof text !== 'string') {
    throw new Error(
      'Transcript JSON の results.transcripts[0].transcript が文字列ではありません。',
    );
  }

  const items = resultsObj.items;
  let confidence: number | null = null;
  if (isUnknownArray(items)) {
    const values = collectConfidences(items);
    if (values.length > 0) {
      confidence = values.reduce((acc, v) => acc + v, 0) / values.length;
    }
  }
  return { text, confidence };
}

function parseJson(rawJson: string): unknown {
  try {
    return JSON.parse(rawJson) as unknown;
  } catch (err) {
    throw new Error(
      `Transcript JSON のパースに失敗しました: ${err instanceof Error ? err.message : 'unknown'}`,
    );
  }
}

function isUnknownArray(value: unknown): value is readonly unknown[] {
  return Array.isArray(value);
}

function collectConfidences(items: readonly unknown[]): number[] {
  const out: number[] = [];
  for (const it of items) {
    if (it === null || typeof it !== 'object') continue;
    const alts = (it as Record<string, unknown>).alternatives;
    if (!isUnknownArray(alts) || alts.length === 0) continue;
    const a0 = alts[0];
    if (a0 === null || typeof a0 !== 'object') continue;
    const c = (a0 as Record<string, unknown>).confidence;
    const num = typeof c === 'number' ? c : typeof c === 'string' ? Number.parseFloat(c) : NaN;
    if (!Number.isNaN(num)) out.push(num);
  }
  return out;
}
