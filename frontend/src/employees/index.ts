/**
 * 社員マスタ管理画面の公開窓口（barrel）。
 *
 * `routing/AppRouter.tsx` から子ルートとして読み込むコンポーネントと、
 * 単体テスト時に直接参照する純粋関数バリデータを束ねる。
 */

export { EmployeeListPage, type EmployeeListPageProps } from './EmployeeListPage';
export { EmployeeFormPage, type EmployeeFormPageProps } from './EmployeeFormPage';
export { EmployeeCsvImportPage, type EmployeeCsvImportPageProps } from './EmployeeCsvImportPage';
export {
  isValidE164,
  isValidName,
  validateCsvFile,
  encodeBase64,
  MAX_NAME_LENGTH,
  CSV_MAX_BYTES,
  CSV_MAX_DATA_ROWS,
  CSV_REQUIRED_HEADER,
  type CsvFileValidationResult,
} from './validation';
