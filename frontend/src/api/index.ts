/**
 * HTTP クライアント層の公開窓口（barrel）。
 *
 * 上位コンポーネントは本モジュール経由でのみ API 呼出を行う。
 * 認証ヘッダ付与・自動リフレッシュ・401 ハンドリングは全て本層に集約する。
 */

export {
  createAuthFetch,
  getApiBaseUrl,
  type AuthFetch,
  type AuthFetchOptions,
} from './httpClient';

export {
  EmployeeClient,
  EmployeeApiError,
  type EmployeeSummary,
  type EmployeeDetail,
  type CreateEmployeePayload,
  type UpdateEmployeePayload,
  type ImportCsvResult,
  type ImportCsvError,
  type EmployeeClientOptions,
} from './employeeClient';

export {
  RecordingClient,
  RecordingApiError,
  type PresignedArtifact,
  type RecordingClientOptions,
} from './recordingClient';

export {
  InboundClient,
  InboundApiError,
  type InboundContactRow,
  type InboundContactsPage,
  type InboundFlow,
  type InboundClientOptions,
} from './inboundClient';

export {
  DictionaryClient,
  DictionaryApiError,
  DictionaryConflictError,
  VALID_CATEGORIES,
  type DictionaryCategory,
  type DictionarySnapshot,
  type DictionaryClientOptions,
} from './dictionaryClient';
