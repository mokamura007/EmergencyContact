/**
 * サイクル管理画面の公開窓口（barrel）。
 *
 * `routing/AppRouter.tsx` から子ルートとして読み込むコンポーネントを束ねる。
 */

export {
  CycleStartPage,
  type CycleStartPageProps,
  DEFAULT_RETRY_COUNT,
  DEFAULT_RETRY_INTERVAL_MINUTES,
} from './CycleStartPage';

export {
  CycleStatusPage,
  type CycleStatusPageProps,
  STATUS_POLLING_INTERVAL_MS,
} from './CycleStatusPage';

export { CyclesListPage, type CyclesListPageProps, CYCLES_PAGE_SIZE } from './CyclesListPage';

export { CycleDetailPage, type CycleDetailPageProps } from './CycleDetailPage';

export {
  TranscriptViewerPage,
  type TranscriptViewerPageProps,
  type PlainFetch,
} from './TranscriptViewerPage';

export { parseTranscript, type ParsedTranscript } from './transcriptParser';

export { isWithinRetentionWindow, isRetentionExpired, RETENTION_WINDOW_MS } from './cycleExpiry';

export {
  statusViewerReducer,
  initialStatusViewerState,
  isTerminalStatus,
  renderDegraded,
  type CycleStatusSnapshot,
  type CycleStatusSummary,
  type CycleStatusItem,
  type CycleDegradedEntry,
  type CycleTopStatus,
  type StatusViewerEvent,
  type StatusViewerState,
  type VoiceStatusValue,
} from './statusReducer';
