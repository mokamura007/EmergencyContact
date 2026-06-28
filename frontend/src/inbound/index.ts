/**
 * インバウンド着信履歴画面の公開窓口（barrel）。
 *
 * `routing/AppRouter.tsx` から子ルートとして読み込むコンポーネントを束ねる。
 */

export { InboundListPage, type InboundListPageProps } from './InboundListPage';

export {
  InboundTranscriptViewerPage,
  type InboundTranscriptViewerPageProps,
} from './InboundTranscriptViewerPage';
