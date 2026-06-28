# Requirements Document

## Introduction

本機能は、災害発生時における自社内（最大300人規模）の社員安否を、AWS サーバーレス基盤上で自動架電により確認する仕組みである。従来の電話連絡網運用を、Amazon Connect の自動架電と音声認識（Amazon Transcribe）による応答取得に置き換え、結果を管理サイトで集約・可視化する。AWS 利用リージョンは東京リージョン（ap-northeast-1）に固定する。本要件文書は機能要件・非機能要件・データ要件・スコープ外を定義する。

## Glossary

- **System**: 本安否確認システム全体を指す総称。AWS 上に構築されるサーバーレスコンポーネント群と管理サイトを含む。
- **Admin_Console**: 管理者がブラウザから操作する管理サイト（CloudFront + S3 配信、Cognito 認証）。
- **Auth_Service**: Amazon Cognito ユーザープールおよび付随する認可機構。本システムでは管理者ロールのみ運用する。
- **Cycle_Manager**: 安否確認サイクルの起動・進捗管理を担当するコンポーネント（Step Functions ステートマシンおよび Lambda 関数群）。
- **Connect_Caller**: Amazon Connect から自動発信し、音声録音および音声認識結果を取得するアウトバウンド系コンポーネント（Connect コンタクトフロー + Lambda）。
- **Inbound_Handler**: Amazon Connect への着信（折り返し電話）を処理するインバウンド系コンポーネント（Connect コンタクトフロー + Lambda）。
- **Voice_Transcriber**: 通話音声を Amazon Transcribe でテキスト化するコンポーネント（Lambda + Transcribe ジョブ管理）。
- **Keyword_Matcher**: Transcribe の出力テキストに対してキーワード辞書を適用し、安否ステータスを判定するコンポーネント（Lambda）。
- **Recording_Store**: 通話録音、Transcribe 出力テキスト、および両者のメタデータを保管するコンポーネント（S3 バケット、DynamoDB、関連 Lambda）。
- **Status_Viewer**: 管理サイト上で進捗・結果をポーリング表示するフロントエンド機能および対応する API。
- **Employee_Master**: 社員の氏名・社員番号・所属・連絡先電話番号等を保持する DynamoDB テーブル。管理画面の入力項目は名前と電話番号のみとし、その他属性は内部運用用に保持する。
- **Cycle**: 1回の安否確認実行単位。起動時刻・対象者一覧・再発信パラメータ・対象者選定モード・最終ステータスを保持する。
- **Response**: 1人の社員に対する1サイクル中の応答結果（音声テキスト、判定ステータス、タイムスタンプ、通話結果コード）。
- **Recording_Metadata**: 録音ファイルへの参照情報（S3 オブジェクトキー・録音時刻・対応 Cycle ID・対応社員 ID 等）。
- **Transcript**: Amazon Transcribe による音声認識結果テキスト。S3 または DynamoDB に保管する。
- **Keyword_Dictionary**: 音声テキストから安否ステータスを判定するためのキーワード辞書。管理画面で動的編集可能とする。
- **Inbound_Contact**: 社員の登録電話番号からの折り返し電話イベント。Cycle に紐づく場合は Response を更新する。
- **Administrator**: Cognito ユーザープールの「管理者」ロールを保有する利用者。本システムでは管理者ロールのみが存在する。
- **Voice_Status**: 音声認識結果から判定される安否ステータス。本システムでは `SAFE` / `INJURED` / `UNAVAILABLE` / `OTHER` の 4 区分とする。
- **Retry_Count**: 1サイクル内で1人の社員に発信する最大回数（初回 + 再発信）。
- **Retry_Interval**: 再発信と再発信の間に空ける最小時間（分）。
- **KMS_Key**: 録音および個人情報暗号化に用いる AWS KMS カスタマー管理キー（CMK）。
- **CloudFormation_Template**: 本システム一式を構成する単一の CloudFormation テンプレート。Parameters により dev/stg/prod を切り替える。

## Requirements

### Requirement 1: 管理者認証

**User Story:** 管理者として、自身に割り当てられた認証情報で管理サイトにログインしたい。それにより、安否確認サイクルの起動および社員マスタ管理に正当な権限でアクセスできる。

#### Acceptance Criteria

1. WHEN 管理者が Admin_Console にアクセスする, THE Auth_Service SHALL TLS 1.2 以上の通信路上で Cognito ユーザープールに対する認証フローを要求する。
2. WHEN 管理者が正しい認証情報を入力する, THE Auth_Service SHALL JWT 形式の ID トークン（有効期限 1 時間）およびアクセストークン（有効期限 1 時間）、リフレッシュトークン（有効期限 30 日）を発行する。
3. IF 認証トークンに「管理者」ロールが含まれている, THEN THE Admin_Console SHALL 管理者向け機能（社員マスタ管理・サイクル起動・全件ステータス閲覧・キーワード辞書管理）を表示する。
4. IF 認証トークンに「管理者」ロールが含まれていない, THEN THE Admin_Console SHALL 全機能へのアクセスを拒否する。
5. IF 認証情報が不正である, THEN THE Auth_Service SHALL 認証エラーを返却し、当該イベントを CloudWatch Logs に監査ログとして記録する。
6. IF 同一アカウントに対して 5 回連続で認証失敗が発生する, THEN THE Auth_Service SHALL 当該アカウントを 30 分間ロックアウトする。
7. IF アクセストークンの有効期限が切れている, THEN THE Auth_Service SHALL リフレッシュトークンによる再発行を要求する。
8. WHEN 認証成功または認証失敗が発生する, THE Auth_Service SHALL 当該イベント（プリンシパル識別子・イベント種別・タイムスタンプ・送信元 IP）を CloudWatch Logs に監査ログとして記録する。
9. THE Auth_Service SHALL 一般社員ロールおよび一般社員向け画面・API を提供しない。

### Requirement 2: 社員マスタ管理（手入力）

**User Story:** 管理者として、社員マスタを管理サイト上で個別に追加・更新・削除したい。それにより、入退社や連絡先変更を即時反映できる。

#### Acceptance Criteria

1. WHEN 管理者が新規社員追加画面を開く, THE Admin_Console SHALL 入力項目として「氏名（1〜128 文字、必須）」および「電話番号（E.164 形式、最大 16 文字、必須）」のみを表示し、各項目に対する形式・必須バリデーションを実施する。
2. WHEN 管理者が社員追加を確定し、全入力項目のバリデーションが成功する, THE System SHALL Employee_Master に当該レコードを書き込み、社員 ID（UUID）を新規採番し、CloudWatch Logs に追加操作の監査ログ（実行管理者識別子・対象社員 ID・操作種別・タイムスタンプを含む）を記録する。
3. IF 入力された電話番号が Employee_Master に既に登録されている（論理削除済みレコードを含む）, THEN THE System SHALL 重複を示すバリデーションエラーを返却し、Employee_Master に対する変更を行わない。
4. WHEN 管理者が既存社員の更新を確定し、全入力項目のバリデーションが成功する, THE System SHALL Employee_Master の対象レコードを更新し、CloudWatch Logs に更新操作の監査ログ（実行管理者識別子・対象社員 ID・更新前後の値・タイムスタンプを含む）を記録する。
5. WHEN 管理者が既存社員を削除する, THE System SHALL Employee_Master の対象レコードに論理削除フラグを設定し、CloudWatch Logs に削除操作の監査ログ（実行管理者識別子・対象社員 ID・タイムスタンプを含む）を記録する。
6. IF 削除対象の社員レコードが Employee_Master に存在しない、または既に論理削除済みである, THEN THE System SHALL 対象不在または既削除を示すエラーを返却し、Employee_Master に対する変更を行わない。
7. IF 入力された電話番号が E.164 形式（先頭が + で始まり、その後に 1〜15 桁の数字が続く形式）に準拠していない, THEN THE System SHALL 電話番号形式違反を示すバリデーションエラーを返却し、Employee_Master に対する変更を行わない。

### Requirement 3: 社員マスタ管理（CSVインポート）

**User Story:** 管理者として、社員マスタを CSV ファイルで一括投入したい。それにより、初期構築および年次入替を効率的に行える。

#### Acceptance Criteria

1. WHEN 管理者が CSV ファイルをアップロードする, THE System SHALL ヘッダ行を含む UTF-8 エンコードの CSV ファイルを受け付け、データ行数が 300 行以下、ファイルサイズが 1 MiB 以下の場合のみ後続処理に進める。
2. THE System SHALL CSV のヘッダ行を除く 1 行を 1 社員レコードとして解釈し、各列に「氏名、電話番号」を必須項目として割り当てる。
3. WHEN CSV の全行について必須項目欠落が無く、かつ電話番号が E.164 形式に準拠し、かつ電話番号の重複が CSV 内および既存 Employee_Master との間で存在しない, THE System SHALL Employee_Master に対して全行を 1 トランザクションとして書き込む。
4. IF CSV のいずれかの行で必須項目欠落、電話番号形式不正（E.164 形式違反）、または電話番号重複が検出される, THEN THE System SHALL 当該行番号とエラー内容を含むレポートを返却し、Employee_Master へのインポートを 1 件も実施しない。
5. IF CSV ファイルが UTF-8 でない、ヘッダ行が欠落している、または 300 行を超える, THEN THE System SHALL ファイルレベルのエラーを示すメッセージを返却し、Employee_Master に対する変更を行わない。
6. WHEN CSV インポート処理が成功または中止により完了する, THE System SHALL 取り込み件数（試行）・成功件数・失敗件数の 3 値および各失敗行のエラー内容を管理者画面に表示する。
7. WHEN Employee_Master への一括書き込み中に DynamoDB 側の書き込みエラーが発生する, THE System SHALL 当該インポートに紐づく全行の書き込みを取り消し、管理者にロールバック完了とエラー内容を示すメッセージを返却する。

### Requirement 4: 安否確認サイクルの起動

**User Story:** 管理者として、災害発生時に管理サイトから安否確認サイクルを手動で起動したい。それにより、必要なタイミングで自動架電を開始できる。

#### Acceptance Criteria

1. WHEN 管理者がサイクル起動画面で「対象者選定モード」「Retry_Count」「Retry_Interval」を入力し起動を確定する, THE Cycle_Manager SHALL 新しい Cycle レコードを「実行中」状態で作成し、Step Functions ステートマシンの実行を開始する。
2. THE Admin_Console SHALL 対象者選定の操作 UI として、サイクル起動画面の左側にチェックボックスを 1 個（ラベル「全員」）配置する。
3. WHEN 起動時にチェックボックス「全員」がチェックされている, THE Cycle_Manager SHALL 対象者選定モードを `ALL` とし、論理削除されていない全社員を対象とする。
4. WHEN 起動時にチェックボックス「全員」がチェックされていない, THE Cycle_Manager SHALL 対象者選定モードを `UNREACHABLE_ONLY` とし、直近で完了した Cycle における未到達者（最終ステータス `UNREACHABLE` または `OTHER`）のみを対象とする。
5. IF 対象者選定モードが `UNREACHABLE_ONLY` であるが直近完了 Cycle が存在しないか、未到達者が 0 名である, THEN THE Cycle_Manager SHALL 起動を拒否し、対象者が存在しないことを示すエラーを返却し、Cycle レコードを作成しない。
6. THE Cycle_Manager SHALL Retry_Count として 0 以上 5 以下の整数を有効入力として受け付ける。
7. THE Cycle_Manager SHALL Retry_Interval として 1 分以上 60 分以下の整数を有効入力として受け付ける。
8. IF Cycle ステータスが「実行中」のレコードが既に 1 件以上存在する状態で別の Cycle 起動が要求される, THEN THE Cycle_Manager SHALL 起動を拒否し、既存 Cycle の ID と重複起動である旨を示すエラーを返却し、新規 Cycle レコードを作成しない。
9. WHERE 自動トリガー（地震速報連携・スケジューラ起動）が要求されない構成である, THE Cycle_Manager SHALL 起動契機を管理者操作のみに限定する。
10. IF Retry_Count が 0 未満もしくは 5 を超える値、または Retry_Interval が 1 分未満もしくは 60 分を超える値、または整数以外の値が入力される, THEN THE Cycle_Manager SHALL 起動要求を拒否し、入力値が許容範囲外であることを示すエラーを返却し、Cycle レコードを作成しない。
11. IF Step Functions ステートマシンの実行開始に失敗する, THEN THE Cycle_Manager SHALL 作成された Cycle レコードを「起動失敗」状態に更新し、起動失敗を示すエラーを返却する。

### Requirement 5: Amazon Connect による自動架電（アウトバウンド）

**User Story:** 管理者として、対象社員に Amazon Connect から自動発信し、音声テキストによる応答で安否を取得したい。それにより、人手を介さず一斉に状況把握を行える。

#### Acceptance Criteria

1. WHEN Cycle が開始される, THE Connect_Caller SHALL 対象社員 1 人につき 1 コールを Amazon Connect から発信する。
2. WHEN 通話が接続される, THE Connect_Caller SHALL Text-to-Speech（Amazon Polly）による安否確認ガイダンスを再生し、社員の応答音声を録音状態へ遷移する。
3. THE Connect_Caller SHALL ガイダンスのテキストを CloudFormation_Template の Parameters により変更可能とする。
4. WHEN 社員の発話または無発話を含む音声区間が確定する, THE Connect_Caller SHALL 通話結果コードを「録音完了」として確定し、通話を切断する。
5. IF 発信から呼出が 30 秒継続しても応答されない、留守番電話に接続される、または通話中で発信が完了しない, THEN THE Connect_Caller SHALL 「無応答」「留守番電話」「通話中」を区別する通話結果コードを Response に記録し、当該社員を再発信対象としてマークする。
6. THE Connect_Caller SHALL DTMF（プッシュボタン）入力を受け付けず、Contact Flow 内の DTMF 取得ブロックを使用しない。

### Requirement 6: 音声認識（Amazon Transcribe）

**User Story:** 管理者として、社員の音声応答を自動でテキスト化したい。それにより、ステータス判定の自動化と監査用エビデンスの可読化を実現できる。

#### Acceptance Criteria

1. WHEN 録音ファイルが S3 に保存される, THE Voice_Transcriber SHALL 当該ファイルに対して Amazon Transcribe ジョブを開始する。
2. THE Voice_Transcriber SHALL Transcribe ジョブで日本語（`ja-JP`）を言語コードとして指定する。
3. WHEN Transcribe ジョブが完了する, THE Voice_Transcriber SHALL ジョブ完了から 30 秒以内に Transcript（テキスト本文・信頼度・対応 Cycle ID・対応社員 ID）を保管する。
4. THE Voice_Transcriber SHALL Transcript を SSE-KMS（KMS_Key）で暗号化下に保管する。
5. THE Voice_Transcriber SHALL Transcript の保管期間を Cycle 起動時刻から 90 日とし、90 日経過後にライフサイクルポリシーで自動削除する。
6. IF Transcribe ジョブが失敗する, THEN THE Voice_Transcriber SHALL 最大 3 回まで再試行し、全ての再試行が失敗した場合は通話結果コードを `TRANSCRIBE_FAILED` として Response に記録する。

### Requirement 7: キーワードマッチングによる安否ステータス判定

**User Story:** 管理者として、音声テキストから安否ステータスを自動判定したい。それにより、応答取得後の集計・可視化を即時に進められる。

#### Acceptance Criteria

1. WHEN Transcript が保管される, THE Keyword_Matcher SHALL Keyword_Dictionary に基づいて当該テキストから Voice_Status を判定する。
2. THE Keyword_Matcher SHALL Voice_Status の値域として `SAFE` / `INJURED` / `UNAVAILABLE` / `OTHER` の 4 区分のみを採用する。
3. WHEN テキスト中に `SAFE` カテゴリのキーワードが 1 個以上含まれ、かつ `INJURED` および `UNAVAILABLE` カテゴリのキーワードがいずれも含まれていない, THE Keyword_Matcher SHALL Voice_Status を `SAFE` と判定する。
4. WHEN テキスト中に `INJURED` カテゴリのキーワードが 1 個以上含まれている, THE Keyword_Matcher SHALL Voice_Status を `INJURED` と判定する。
5. WHEN テキスト中に `INJURED` カテゴリのキーワードが含まれず、かつ `UNAVAILABLE` カテゴリのキーワードが 1 個以上含まれている, THE Keyword_Matcher SHALL Voice_Status を `UNAVAILABLE` と判定する。
6. IF テキスト中にいずれのカテゴリのキーワードも含まれていない, THEN THE Keyword_Matcher SHALL Voice_Status を `OTHER` と判定する。
7. THE Keyword_Matcher SHALL 判定結果（Voice_Status・マッチしたキーワード一覧・適用した辞書バージョン）を Response に書き込む。
8. THE System SHALL `INJURED` を `UNAVAILABLE` よりも優先するステータス判定優先順位（INJURED > UNAVAILABLE > SAFE > OTHER）を採用する。

### Requirement 8: キーワード辞書管理

**User Story:** 管理者として、キーワード辞書を管理画面上で動的に追加・更新・削除したい。それにより、運用しながら辞書を改善できる。

#### Acceptance Criteria

1. THE Admin_Console SHALL キーワード辞書の閲覧・追加・更新・削除を行う管理画面を提供する。
2. THE System SHALL Keyword_Dictionary を `SAFE` / `INJURED` / `UNAVAILABLE` の 3 カテゴリで管理する。
3. WHEN 管理者がキーワードを追加する, THE System SHALL 当該キーワードに `カテゴリ`、`キーワード文字列（1〜64 文字）`、`有効フラグ`、`作成時刻`、`作成者` を保存し、辞書バージョンを 1 増やす。
4. WHEN 管理者がキーワードを更新または無効化する, THE System SHALL 当該キーワードの履歴を保持しつつ最新内容を反映し、辞書バージョンを 1 増やす。
5. WHEN Cycle が起動される, THE Keyword_Matcher SHALL 当該 Cycle 起動時点の辞書バージョンをスナップショットとして固定し、Cycle 内のすべての判定で同一バージョンを使用する。
6. IF キーワード辞書が空である状態で Cycle 起動が要求される, THEN THE System SHALL 起動を拒否し、辞書未設定を示すエラーを返却する。
7. WHEN 辞書の追加・更新・削除が発生する, THE System SHALL 操作主体・操作種別・対象キーワード・操作タイムスタンプを CloudWatch Logs に監査ログとして記録する。

### Requirement 9: 再発信制御

**User Story:** 管理者として、初回応答が得られなかった社員に対し、設定した回数と間隔で自動的に再発信したい。それにより、不在・通話中の社員に対する到達率を高められる。

#### Acceptance Criteria

1. WHEN 1 回の発信が「無応答」「通話中」「留守番電話」「発信エラー」「TRANSCRIBE_FAILED」のいずれかの結果で終了し、かつ当該社員への累積発信回数が Retry_Count 未満である, THE Cycle_Manager SHALL 当該社員を再発信キューに追加する。
2. THE Cycle_Manager SHALL 同一社員への次回発信開始時刻が、前回発信終了時刻から Retry_Interval 以上経過していることを保証する。
3. WHEN ある社員から有効な Voice_Status（`SAFE` / `INJURED` / `UNAVAILABLE` のいずれか）が記録される, THE Cycle_Manager SHALL 当該社員に対する以降の再発信を中止し、再発信キューから当該社員を除外する。
4. WHEN ある社員の Voice_Status が `OTHER` であり、かつ累積発信回数が Retry_Count 未満である, THE Cycle_Manager SHALL 当該社員を再発信キューに追加する。
5. WHEN 当該社員への発信回数が Retry_Count に達し、かつ有効な Voice_Status が記録されていない, THE Cycle_Manager SHALL 当該社員のステータスを `UNREACHABLE` として確定する。
6. THE Cycle_Manager SHALL Amazon Connect 東京リージョンの既定クォータ（同時アクティブコール 10）を超えない範囲で発信を並列化し、上限到達時は新規発信を待機キューに保持して空きが発生し次第発信を再開する。

### Requirement 10: 通話録音および S3 保管

**User Story:** 管理者として、全通話を録音して所定期間保管したい。それにより、応答内容の事後確認および監査要求に対応できる。

#### Acceptance Criteria

1. THE Connect_Caller SHALL 通話開始時点から通話終了時点までの全区間を中断なく録音する。
2. WHEN 通話が終了する, THE Recording_Store SHALL 通話終了から 60 秒以内に録音ファイルを S3 バケットに保存する。
3. THE Recording_Store SHALL 録音ファイルを KMS_Key によるサーバーサイド暗号化（SSE-KMS）で保管する。
4. THE Recording_Store SHALL 録音ファイル格納用 S3 バケットに、オブジェクト作成日から 90 日経過後に当該オブジェクトを自動削除するライフサイクルポリシーを適用する。
5. WHEN 録音ファイルの S3 保存が完了する, THE Recording_Store SHALL 当該保存完了から 10 秒以内に Recording_Metadata（S3 オブジェクトキー・Cycle ID・社員 ID・録音時刻・通話時間）を DynamoDB に書き込む。
6. IF Lambda 実行ロールおよび管理者 Cognito ロール以外のプリンシパルが録音ファイルへのアクセスを要求する, THEN THE System SHALL S3 バケットポリシーおよび KMS_Key ポリシーにより当該アクセス要求を拒否し、要求元にアクセス拒否を示すエラー応答を返す。
7. WHEN 管理者が録音ファイルの再生を要求する, THE System SHALL 有効期限を 15 分以内とする署名付き URL を発行し、当該署名付き URL 経由でのみ録音ファイルへのアクセスを許可する。
8. IF 録音ファイルの S3 保存が失敗する, THEN THE Recording_Store SHALL 最大 3 回まで再試行し、全ての再試行が失敗した場合は失敗を示すエラーを記録し、Recording_Metadata の DynamoDB 書き込みを行わない。
9. IF Recording_Metadata の DynamoDB 書き込みが失敗する, THEN THE Recording_Store SHALL 最大 3 回まで再試行し、全ての再試行が失敗した場合は失敗を示すエラーを記録する。

### Requirement 11: ステータス見える化

**User Story:** 管理者として、進行中サイクルの状況を管理サイトでほぼリアルタイムに把握したい。それにより、対応の優先順位付けや追加施策の判断ができる。

#### Acceptance Criteria

1. WHILE Cycle のステータスが「実行中」である, THE Status_Viewer SHALL 10 秒間隔（±1 秒以内）で最新ステータスを API Gateway 経由で取得し、応答受領から 3 秒以内に画面表示を更新する。
2. THE Status_Viewer SHALL 「対象者総数」「発信完了数（少なくとも 1 回の発信試行が完了した対象者の人数）」「応答取得数（Voice_Status が `SAFE` / `INJURED` / `UNAVAILABLE` のいずれかに確定した人数）」「未到達数（最大発信回数に達しても有効な Voice_Status が得られなかった対象者の人数）」「ステータス別内訳（SAFE／INJURED／UNAVAILABLE／OTHER／UNREACHABLE の各人数）」を画面上に表示する。
3. THE Status_Viewer SHALL 個別社員ごとに、最新 Voice_Status、発信回数、最終応答時刻（応答未受領の場合は「未応答」と表示）、最終 Transcript 抜粋（先頭 100 文字）を一覧表で表示する。
4. WHEN Cycle に含まれるすべての対象者について、応答受領または最大発信回数到達のいずれかにより最終ステータスが確定する, THE Cycle_Manager SHALL Cycle のステータスを「完了」に更新する。
5. WHILE Cycle のステータスが「完了」である, THE Status_Viewer SHALL ポーリングを停止し、最後に取得した最新ステータスを画面上に保持表示する。
6. IF Status_Viewer が API Gateway からのステータス取得に失敗する, THEN THE Status_Viewer SHALL 取得失敗を示すエラー表示を画面上に提示し、直前に取得したステータス情報を保持したまま次回ポーリング周期で再取得を継続する。

### Requirement 12: 履歴閲覧

**User Story:** 管理者として、全社員の過去応答履歴を閲覧したい。それにより、過去の安否確認結果を後から確認できる。

#### Acceptance Criteria

1. WHEN 管理者が履歴画面を開く, THE Admin_Console SHALL 過去 Cycle 一覧および各 Cycle の対象者別 Response（対象者識別子・最終 Voice_Status・発信回数・最終応答時刻・Transcript 抜粋）を、Cycle 起動時刻の降順で 1 ページあたり最大 50 件のページング形式で表示する。
2. WHERE 録音ファイルが Cycle 起動時刻から 90 日以内である, THE Admin_Console SHALL 有効期限 15 分の署名付き URL を介した当該録音ファイルの再生機能および対応 Transcript の全文表示機能を提供する。
3. IF Cycle 起動時刻から 90 日を超過した録音ファイルへの再生要求が行われる, THEN THE Admin_Console SHALL 当該再生要求を拒否し、保管期間を超過した旨を示すメッセージを表示する。

### Requirement 13: インバウンド（折り返し電話）受付

**User Story:** 管理者として、社員からの折り返し電話を本システムで受け付けて応答内容を Cycle に紐付けたい。それにより、自動架電で繋がらなかった社員からの応答も取得できる。

#### Acceptance Criteria

1. THE Inbound_Handler SHALL Amazon Connect の着信用 Contact Flow を介して、本システムの代表電話番号への着信を受け付ける。
2. WHEN 着信を受信する, THE Inbound_Handler SHALL 発信者番号（Caller ID）を E.164 形式で取得し、Employee_Master の電話番号と完全一致するレコードを検索する。
3. IF 発信者番号が Employee_Master のいずれの電話番号とも一致しない, THEN THE Inbound_Handler SHALL ガイダンス「番号が登録されていないため受付できません」を再生し、通話を切断する。
4. WHEN 発信者番号が Employee_Master の有効レコード（論理削除されていない）と一致する, THE Inbound_Handler SHALL Voice_Transcriber へのフローへ遷移し、ガイダンス再生・録音・Transcribe・Keyword_Matcher による Voice_Status 判定を、Requirement 5〜7 と同等の手順で実施する。
5. WHEN 発信者番号一致から判定までが成功し、かつ Cycle ステータスが「実行中」または「完了」（完了から 30 日以内）の Cycle が存在する, THE Inbound_Handler SHALL 直近の該当 Cycle 内の当該社員 Response を最新の Voice_Status で更新し、累積発信回数および通話結果コード一覧を維持する。
6. IF 直近完了 Cycle の完了時刻から 30 日を超過している、または該当する Cycle が存在しない, THEN THE Inbound_Handler SHALL ガイダンス「現在受付対象のサイクルがありません」を再生し、通話を切断する。
7. THE Inbound_Handler SHALL Inbound_Contact レコード（着信時刻・発信者番号・対応 Cycle ID・対応社員 ID・通話結果コード・Transcript 参照）を専用テーブルに記録する。
8. THE Inbound_Handler SHALL Cycle ステータスが「タイムアウト」または「起動失敗」の Cycle に対しては Response を更新せず、Inbound_Contact のみ記録する。

### Requirement 14: SLA（架電完了時間）

**User Story:** 管理者として、サイクル起動後に所定時間内で発信および結果集約を完了させたい。それにより、災害初動の意思決定に間に合わせられる。

#### Acceptance Criteria

1. WHEN Cycle が起動される, THE Cycle_Manager SHALL 起動時刻から 1800 秒（30 分）以内に対象者全員に対して Amazon Connect への発信指示引き渡しを完了し、Response レコードを生成する。
2. WHEN Cycle が起動される, THE Cycle_Manager SHALL 起動時刻から 3600 秒（60 分）以内に Cycle のステータスを、全対象者の最終ステータス確定（`SAFE` / `INJURED` / `UNAVAILABLE` / `OTHER` / `UNREACHABLE`）により「完了」に到達させる。
3. THE Cycle_Manager SHALL 対象者数 300 名・Retry_Count 3・Retry_Interval 5 分・同時アクティブコール 10 の条件下で前項の 60 分 SLA を達成する処理能力を備える。
4. IF 起動時刻から 3600 秒（60 分）を経過しても Cycle が完了しない, THEN THE Cycle_Manager SHALL Cycle ステータスを「タイムアウト」とし、進行中の発信および未消化の再発信キューを停止し、未確定の社員のステータスを `UNREACHABLE` として確定する。
5. WHEN Cycle ステータスが「タイムアウト」に遷移する, THE Cycle_Manager SHALL 起動時の管理者および Admin_Console に対しタイムアウト発生を通知する。
6. IF 初回発信完了が起動時刻から 1800 秒（30 分）を経過しても達成されない, THEN THE Cycle_Manager SHALL 起動時の管理者および Admin_Console に対し SLA 遅延警告を通知する。

### Requirement 15: 個人情報（電話番号・録音・テキスト）取扱

**User Story:** 管理者として、保有する電話番号・録音・音声テキストを個人情報として適切に保管・アクセス制御・削除したい。それにより、社内規程および個人情報保護法への対応を担保できる。

#### Acceptance Criteria

1. THE Employee_Master SHALL 電話番号フィールドを含む全レコードを KMS_Key によるサーバーサイド暗号化下で DynamoDB に保管する。
2. THE System SHALL 電話番号・録音・Transcript フィールドへの読取・書込・削除の各操作を、Lambda 実行ロールおよび Cognito 認証済み管理者プリンシパルに限定する IAM ポリシーで制御する。
3. WHEN 管理者が社員レコードを削除する, THE System SHALL 5 秒以内に当該レコードの電話番号フィールドを NULL 化または論理削除フラグ付きで更新する。
4. WHEN 電話番号フィールドが NULL 化または論理削除フラグ付きで更新された, THE System SHALL 当該社員レコードを以降のすべての Cycle の通知対象から除外し、かつ Inbound_Handler の発信者番号一致判定の対象から除外する。
5. WHEN 電話番号、録音、または Transcript の追加・更新・削除イベントが発生する, THE System SHALL 操作主体のプリンシパル識別子・操作種別・対象社員 ID・操作タイムスタンプを含む監査ログを CloudWatch Logs に記録する。
6. THE System SHALL 録音ファイル、Recording_Metadata、Transcript について Requirement 10 および Requirement 6 の保管・削除ポリシー（90 日）を適用する。

### Requirement 16: ログ要件

**User Story:** 運用者として、システム動作および監査必要事象を CloudWatch Logs で確認したい。それにより、障害解析および操作監査を行える。

#### Acceptance Criteria

1. THE System SHALL 全 Lambda 関数の実行ログ（呼び出し開始・終了・例外発生時のスタックトレースを含む）を CloudWatch Logs に出力する。
2. THE System SHALL Step Functions ステートマシンの実行履歴（状態遷移・入力・出力・エラーを含む全イベント）を CloudWatch Logs に出力する。
3. WHEN 認証イベント、社員マスタ更新、Cycle 起動、電話番号更新、キーワード辞書更新、または Inbound_Contact が発生する, THE System SHALL イベント種別・発生時刻（UTC, ISO 8601 形式）・実行者識別子・対象識別子を含む監査ログを CloudWatch Logs に出力する。
4. WHEN 監査ログに電話番号を含むイベントを出力する, THE System SHALL 電話番号をマスキングした形式（先頭 + と末尾 4 桁を除き \* で置換）で出力する。
5. THE System SHALL CloudWatch Logs のロググループに保持期間を設定し、既定値を 90 日、CloudFormation Parameters による変更可能範囲を 1 日以上 3653 日以下とする。
6. WHERE 高度な監査要件（CloudTrail データイベント・Athena 分析・S3 Object Lock 等）が要求される, THE System SHALL これを本要件のスコープ外とする。

### Requirement 17: IaC（CloudFormation）

**User Story:** 運用者として、システム一式を単一の CloudFormation テンプレートで dev/stg/prod に展開したい。それにより、環境差分を一元管理し再現性を確保できる。

#### Acceptance Criteria

1. THE System SHALL 単一の CloudFormation_Template によりデプロイ可能とし、`aws cloudformation validate-template` を通過し、かつスタックが CREATE_COMPLETE 状態に到達する構成を有する。
2. THE CloudFormation_Template SHALL 環境名 Parameter として `dev` `stg` `prod` の 3 値のいずれかのみを許容する。
3. WHEN 環境名 Parameter が指定される, THE CloudFormation_Template SHALL 環境ごとに異なる値（Cognito ユーザープール名・S3 バケット名・DynamoDB テーブル名・KMS_Key エイリアス・CloudWatch Logs 保持期間（日単位）・Retry_Count 既定値・Retry_Interval 既定値（分単位）・ガイダンス本文）を Parameters または Mappings で切替する。
4. IF 環境名 Parameter として `dev` `stg` `prod` 以外の値が指定される, THEN THE CloudFormation_Template SHALL スタック作成を行わずバリデーションエラーを返す。
5. THE CloudFormation_Template SHALL 同一 AWS アカウントの東京リージョン（ap-northeast-1）に対するデプロイを前提とし、Amazon Connect インスタンス ID、アウトバウンド電話番号 ARN、インバウンド代表電話番号 ARN、Outbound Contact Flow ID、Inbound Contact Flow ID を Parameters として受領する。
6. IF 別アカウントまたは東京リージョン以外への展開が要求される, THEN THE System SHALL スタック作成を行わずエラーを返し、本要件のスコープ外であることを示す。

### Requirement 18: 可用性

**User Story:** 運用者として、災害発生という最重要シナリオにおいて、システムが利用可能であることを確保したい。それにより、肝心な時に動かない事態を回避する。

#### Acceptance Criteria

1. THE System SHALL Lambda・DynamoDB・S3・API Gateway・Cognito・Amazon Connect・Amazon Transcribe・Step Functions・EventBridge のマネージドサービスを使用し、OS パッチ適用および基盤インスタンスのプロビジョニング作業を不要とする構成を採る。
2. THE System SHALL 東京リージョン内で AWS が提供する 2 つ以上のアベイラビリティゾーンを跨いだ可用性を享受する構成とし、月間可用性目標を 99.9%（月間ダウンタイム 43.2 分以内）とする。
3. WHEN 単一のアベイラビリティゾーンに障害が発生する, THE System SHALL 残存アベイラビリティゾーンのみで管理サイト・API・Cycle 起動・Connect 自動架電・録音保管・Transcribe・Inbound 受付の継続提供を行う。
4. IF 一部マネージドサービスに縮退が発生する, THEN THE Status_Viewer SHALL 影響を受ける機能の名称と縮退状態を Admin_Console 上に表示する。
5. WHERE 別リージョンへの DR（ディザスタリカバリ）が要求される, THE System SHALL これを本要件のスコープ外とする。

## Data Requirements

### D1: Employee_Master（社員マスタ）

- **格納先**: DynamoDB（東京リージョン）
- **キー設計**: パーティションキー = 社員 ID（UUID）
- **属性**: 氏名（必須、UI 入力対象）、電話番号（E.164 形式、必須、UI 入力対象）、社員番号（任意、内部運用）、所属（任意、内部運用）、Cognito sub（管理者の場合のみ）、ロール（管理者 / 社員）、論理削除フラグ、作成時刻、更新時刻
- **GSI**: `PhoneNumberIndex`（PK = 電話番号、Inbound_Handler の発信者番号一致判定用）
- **暗号化**: SSE-KMS（KMS_Key）

### D2: Cycle（安否確認サイクル）

- **格納先**: DynamoDB
- **キー設計**: パーティションキー = Cycle ID（UUID）
- **属性**: 起動者（管理者 ID）、起動時刻、対象者選定モード（`ALL` / `UNREACHABLE_ONLY`）、参照元 Cycle ID（`UNREACHABLE_ONLY` 時のみ）、Retry_Count、Retry_Interval、対象者総数、ステータス（実行中／完了／タイムアウト／起動失敗）、完了時刻、辞書バージョン（スナップショット）
- **GSI**: `StatusStartedAtIndex`（PK = ステータス、SK = 起動時刻）
- **暗号化**: SSE-KMS（KMS_Key）

### D3: Response（応答結果）

- **格納先**: DynamoDB
- **キー設計**: パーティションキー = Cycle ID、ソートキー = 社員 ID
- **属性**: Voice_Status（`SAFE` / `INJURED` / `UNAVAILABLE` / `OTHER` / `UNREACHABLE` / `PENDING`）、発信回数、最終発信時刻、最終応答時刻、通話結果コード一覧、Transcript 抜粋（先頭 100 文字）、マッチしたキーワード一覧
- **暗号化**: SSE-KMS（KMS_Key）

### D4: Recording_Metadata（録音メタデータ）

- **格納先**: DynamoDB
- **キー設計**: パーティションキー = Cycle ID、ソートキー = 社員 ID + 通話シーケンス番号
- **属性**: S3 オブジェクトキー、録音開始時刻、録音終了時刻、通話時間（秒）、KMS_Key ID、対応 Transcript 参照
- **暗号化**: SSE-KMS（KMS_Key）

### D5: Recording_File（録音ファイル本体）

- **格納先**: S3（東京リージョン、専用バケット）
- **オブジェクトキー命名**: `recordings/{cycle_id}/{employee_id}/{seq}.wav`（アウトバウンド）／`inbound/{yyyymm}/{employee_id}/{contact_id}.wav`（インバウンド）
- **暗号化**: SSE-KMS（KMS_Key）
- **ライフサイクル**: 90 日経過後に自動削除

### D6: Transcript（音声認識結果テキスト）

- **格納先**: S3（東京リージョン、専用バケット）および DynamoDB（抜粋とメタ情報）
- **S3 オブジェクトキー命名**: `transcripts/{cycle_id}/{employee_id}/{seq}.json`（アウトバウンド）／`inbound/{yyyymm}/{employee_id}/{contact_id}.json`（インバウンド）
- **DynamoDB テーブル名**: `TranscriptMetadata-{env}`、PK = Cycle ID または `INBOUND#{contact_id}`、SK = 社員 ID + 通話シーケンス番号
- **属性**: テキスト本文（S3）、抜粋（DynamoDB、先頭 100 文字）、信頼度、言語コード（`ja-JP`）、Transcribe ジョブ ID、対応録音オブジェクトキー
- **暗号化**: SSE-KMS（KMS_Key）
- **ライフサイクル**: 90 日経過後に自動削除（S3 / DynamoDB TTL）

### D7: KeywordDictionary（キーワード辞書）

- **格納先**: DynamoDB
- **キー設計**: パーティションキー = カテゴリ（`SAFE` / `INJURED` / `UNAVAILABLE`）、ソートキー = キーワード文字列
- **属性**: 有効フラグ、作成時刻、作成者、更新時刻、更新者、辞書バージョン（テーブル全体で 1 個のメタレコードに保持）
- **暗号化**: SSE-KMS（KMS_Key）

### D8: Inbound_Contact（着信記録）

- **格納先**: DynamoDB
- **キー設計**: パーティションキー = Contact ID（UUID）、GSI = `EmployeeReceivedAtIndex`（PK = 社員 ID、SK = 着信時刻）
- **属性**: 着信時刻、発信者番号（マスキング前後を分離保管 — マスキング前は IAM 制限で管理者のみ参照可）、対応 Cycle ID（紐付かない場合は NULL）、対応社員 ID、通話結果コード、Voice_Status、Transcript 参照、対応録音参照
- **暗号化**: SSE-KMS（KMS_Key）

## Non-Functional Requirements

### NFR1: SLA

Requirement 14 に従う。初回発信完了は 30 分以内、サイクル全体は 60 分以内に完了する。タイムアウト時および 30 分 SLA 違反時は管理者に通知する。

### NFR2: 可用性

Requirement 18 に従う。マネージドサービスによる構成を採用し、東京リージョン内で 2 つ以上の AZ を跨ぐ構成とする。月間可用性目標は 99.9%。単一 AZ 障害時は残存 AZ で継続提供する。

### NFR3: セキュリティ

- 個人情報（電話番号）の取扱は Requirement 15 に従う。
- 録音、Recording_Metadata、Transcript は Requirement 6・10・15 に従う。
- 認証・認可は Requirement 1 に従う（管理者ロールのみ）。
- 認証失敗 5 回連続でアカウントを 30 分間ロックアウトする。
- 全データストア（DynamoDB・S3）に対し SSE-KMS を適用する。
- 通信経路は HTTPS（TLS 1.2 以上）を必須とする（CloudFront・API Gateway・Cognito エンドポイントすべて）。

### NFR4: ログ

Requirement 16 に従う。CloudWatch Logs に集約し、保持期間は CloudFormation Parameters で 1 日以上 3653 日以下の範囲で制御する。電話番号は監査ログ出力時にマスキングする。

### NFR5: IaC

Requirement 17 に従う。単一 CloudFormation_Template で dev/stg/prod を Parameters により切替可能とする。Amazon Connect インスタンス ID、アウトバウンド電話番号 ARN、インバウンド代表電話番号 ARN、Outbound Contact Flow ID、Inbound Contact Flow ID は Parameters で外部受領する。

### NFR6: 利用前提 AWS サービス

THE System SHALL 以下の AWS サービスを構成要素として用いる： Route 53、AWS Certificate Manager、CloudFront、Amazon S3、Amazon Cognito、Amazon Connect、Amazon Transcribe、AWS Step Functions、AWS Lambda、Amazon EventBridge、Amazon DynamoDB、Amazon API Gateway、AWS KMS、Amazon CloudWatch Logs。

## Out of Scope

本要件において、以下は対象外とする。

1. 別 AWS アカウントまたは東京リージョン以外への展開（マルチリージョン DR を含む）。
2. SMS、電子メール、モバイルプッシュ通知による安否確認、およびメール通知による安否確認サイクルの起動。
3. DTMF（プッシュボタン）による応答取得。本要件では音声認識（Amazon Transcribe）のみを採用する。
4. 既存 Active Directory または Microsoft Entra ID との SSO 連携。Cognito スタンドアロンとする。
5. 自動トリガー起動（緊急地震速報連携、EventBridge Scheduler 等の外部イベント駆動）。
6. 高度な監査ログ（CloudTrail データイベント、Athena による分析、S3 Object Lock 等のコンプライアンス用途機能）。
7. 多言語ガイダンス、部署別レポート、訓練モード、外部者・委託社員への連絡、多拠点同期、その他の追加機能。
8. 一般社員ロールおよび一般社員向けセルフサービス画面（自身の連絡先のセルフ更新、自身の履歴閲覧、を含む）。社員マスタは管理者が管理する。
9. 端末登録（モバイルアプリ等を介した受信端末の事前登録）に基づく安否確認。
10. LLM（Bedrock 等）による応答内容の意図判定。本要件ではキーワードマッチングのみを採用する。
11. 着信時の音声本人認証（声紋）等、発信者番号一致以外の身元確認手段。
