# ADR-0006: キーワード辞書 PATCH のセマンティクス — design.md と実装のズレ記録

- ステータス: Accepted
- 決定日: 2026-06-26
- 関連仕様: `.kiro/specs/safety-confirmation-system/requirements.md` / `.../design.md` / `.../tasks.md`（Phase 4.1、Phase 10.9）
- 関連 ADR: `docs/decisions/0001-runtime-selection.md`、`docs/decisions/0005-connect-mock-findings.md`

## 1. コンテキスト

`design.md` の Dictionary_Manager セクションは、キーワード辞書管理 API の PATCH 操作を「**有効フラグ更新**」として規定している。一方、Phase 4.1 で実装された `backend/lambdas/dictionary_api/handler.py` の `_update_keyword` は、PATCH を「**version stamp only**（既存 keyword 行を保持したまま META.currentVersion を進めて、当該行の `version` 属性を新 version に更新するだけ）」として実装している。

該当コードを引用する：

```python
# PATCH semantics: refresh the row (e.g., touch the version stamp) but
# do NOT mutate the (category, keyword) primary key. The existing row
# must already exist.
existing = _DICT_TABLE.get_item(Key={"category": category, "keyword": keyword}).get("Item")
if existing is None:
    raise ValueError(f"Keyword not found: {category}#{keyword}")

new_version = _increment_version(expected_version)
_DICT_TABLE.update_item(
    Key={"category": category, "keyword": keyword},
    UpdateExpression="SET version = :v",
    ExpressionAttributeValues={":v": new_version},
)
```

このことからこう考えます：design.md と実装の間に**セマンティクス上の解釈差**が存在する。「有効フラグ更新」は (a) 既存 keyword 行に新たな属性（例：`enabled: bool`）を導入する設計、または (b) 既存 keyword 文字列の置換、のいずれかを意味するが、現実装はそのどちらでもなく「version 更新のみ」である。

Phase 10.9（管理者画面：キーワード辞書管理 UI）の SPA 実装に着手するにあたり、本 UI が PATCH をどう提示するかを決める必要がある。本 ADR はその決定を記録する。

## 2. 決定

| 項目                                           | 値                                                                                                                                                                                                                               |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 10.9 SPA での PATCH の扱い               | **「touch（バージョン更新）」ボタンとして提示する**。キーワード文字列を編集する UI は提供しない（**案 B 採用**）。                                                                                                               |
| Phase 10.9 SPA で提供する操作 3 種             | (1) 追加（POST）、(2) 無効化（DELETE）、(3) touch（PATCH = version stamp only）+ 現在バージョン番号表示                                                                                                                          |
| キーワード文字列の編集が必要になった場合の運用 | 「**DELETE → POST**」の 2 ステップで実施する。これにより META.currentVersion は 2 増加するが、編集頻度は低いため許容する。                                                                                                       |
| design.md「有効フラグ更新」の解釈              | 現時点では **未着手の設計案**として保留。`enabled: bool` 属性は Employee_Master の `is_active` パターンと類似だが、KeywordDictionary には属性追加されていない。将来「有効フラグ属性」が要件追加された時点で別 ADR で再検討する。 |
| バックエンド即時拡張                           | **採用しない**。Phase 4.1 への逆流を回避するため、`handler.py` の `_update_keyword` 改修は本 ADR の範囲外とする。                                                                                                                |
| 本 ADR を起票することによる Phase 4.1 への影響 | **なし**。`handler.py` 既存実装はそのまま運用、Phase 14.x 統合テストで動作確認する。                                                                                                                                             |

## 3. 代替案比較

| 案                                                                                     | 概要                                                                                             | 採用判定                                                                                  |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **案 A：UI で「有効フラグ編集」を提供（design.md 原文通り）**                          | UI に「有効化トグル」を表示。バックエンドに `enabled: bool` 属性を追加する PATCH 拡張が必要。    | **不採用**（Phase 4.1 への逆流が発生、編集系列の振る舞いが現状不明確）                    |
| **案 B：UI で「touch（バージョン更新）」のみ提供、キーワード文字列の編集なし**（採用） | UI 操作は 3 種（追加 / 無効化 / touch + バージョン表示）。文字列編集は DELETE+POST の 2 段運用。 | **採用**                                                                                  |
| **案 C：UI で「キーワード文字列の編集」を提供し、内部で DELETE+POST に変換**           | UI に編集 UI を出すが、Submit 時に DELETE+POST を 2 回呼ぶ。                                     | **不採用**（version が 2 増えるが UI 上では「1 操作」に見える、楽観ロック挙動が説明困難） |
| **案 D：バックエンド即時拡張（PATCH に keyword 置換セマンティクスを追加）**            | `_update_keyword` を「PK 変更」できる形に改修。                                                  | **不採用**（DynamoDB の PK 不変ルール違反、別の Item として PUT が必要）                  |

このことからこう考えます：案 B は、(a) Phase 4.1 を改修せず、(b) UI 上で「version 番号を進めるだけの操作」をユーザーに直感的に提示でき、(c) 編集系列が必要な場合は明示的に 2 段操作として運用、(d) 楽観ロックの挙動が「1 操作 = META.currentVersion +1」として一貫する、という 4 点で本プロジェクトに最も合致する。

## 4. 推奨アプローチ（採用方針の確定）

### 4.1 SPA 側の UI 仕様（Phase 10.9）

- ページ：`/dictionary`（`AdminHome` ダッシュボードからリンク）
- 表示：
  - 現在の辞書バージョン番号（META.currentVersion）
  - 3 カテゴリ別テーブル（SAFE / INJURED / UNAVAILABLE）
  - 各行：キーワード文字列 + **touch（バージョン更新）** ボタン + **無効化** ボタン
  - 各カテゴリ下：追加フォーム（テキスト入力 + 追加ボタン）
- 操作セマンティクス：
  - 追加（POST）：新規キーワード文字列の追加
  - 無効化（DELETE）：既存キーワード文字列の削除
  - touch（PATCH）：既存キーワード文字列を残したまま version を進める（説明テキスト「バージョン更新（touch）」）
- 409 Conflict 時の挙動：
  - `DictionaryConflictError` を捕捉した時点で自動で `list()` を再取得
  - バナー「他の管理者が辞書を更新しました。最新の状態を表示します。再度操作してください。」を表示
  - ユーザーが再操作する形に誘導
- API クライアント：`frontend/src/api/dictionaryClient.ts`（新規）
  - `list()` / `getVersion()` / `add(category, keyword, expectedVersion)` / `remove(...)` / `touch(...)`
  - `DictionaryApiError` / `DictionaryConflictError`（409 専用、`latestVersion: number | null`、本バックエンドの現実装では常に null）

### 4.2 採用しない手法とその理由

| 手法                                              | 不採用理由                                                                                                                                                                                                 |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI でキーワード文字列を編集できる入力欄を提供する | 文字列編集は DynamoDB の PK 変更を伴うため「DELETE → POST」の 2 ステップが必要。UI 上で 1 操作に見せるとユーザーが楽観ロックの挙動を予測しづらくなる。明示的に 2 段操作として運用する方が安全。            |
| バックエンドの `_update_keyword` を即時改修する   | Phase 4.1 への逆流（実装変更 + 既存テスト 23 件の影響範囲確認 + 新 PBT 検討）が発生する。Phase 10.9 完了優先度を高めるため、本 ADR で「現実装のままで運用」を確定。将来再検討は別 ADR / 別タスクで切出す。 |

## 5. 既存実装の確認

### 5.1 backend 側（Phase 4.1 既存実装）

- ファイル：`backend/lambdas/dictionary_api/handler.py`
- 関数：`_update_keyword`
- セマンティクス：(category, keyword) PK を保持したまま META.currentVersion を新値に進め、対象行の `version` 属性を新 version で更新する。
- バリデーション：(a) category は SAFE/INJURED/UNAVAILABLE のみ、(b) keyword は path parameter として受領、(c) expectedVersion は body の整数、(d) 既存行が存在しない場合は 400。
- 履歴スナップショット：`_write_history_snapshot(new_version)` で KeywordDictionaryHistory テーブルに全アクティブ行を新 version で書き込む。
- 監査ログ：`_audit_log(principal, "DICTIONARY_UPDATE", ...)` で CloudWatch Logs に JSON 出力。
- ユニットテスト：`backend/tests/lambdas/dictionary_api/`（Phase 4.1 で 23 件 PASS）。

### 5.2 frontend 側（Phase 10.9 新規実装）

- ファイル：`frontend/src/api/dictionaryClient.ts`
- メソッド：`touch(category, keyword, expectedVersion) → Promise<MutationResult>`
- URL 組立：`PATCH /keyword-dictionary/{encodeURIComponent(category)}/{encodeURIComponent(keyword)}`、body `{ expectedVersion }`
- 200 応答：`{ category, keyword, version: <new> }` を返す
- 409 応答：`DictionaryConflictError(latestVersion=null)` を throw
- UI ボタンテキスト：「バージョン更新（touch）」

## 6. 残課題と保留事項

### 6.1 design.md 原文の改訂

`design.md` の Dictionary_Manager セクションは「PATCH = 有効フラグ更新」と記載されているが、本 ADR で「PATCH = version stamp only（touch）」へ運用変更した。design.md 原文の改訂は **別途のドキュメント整備タスク**としてユーザー判断による（本 ADR が記録として優先する形）。Phase 14.x 統合テスト時、もしくは Phase 15.x ドキュメント整備時に design.md 原文を本 ADR と整合する形へ改訂する候補とする。

### 6.2 「有効フラグ属性」が要件追加された場合の再検討

将来、キーワード辞書に「一時的に無効化する（DELETE せず Soft-Delete する）」要件が追加された場合は、本 ADR を再検討する。以下のいずれかを採用候補とする：

- 候補 1：`enabled: bool` 属性を KeywordDictionary に追加し、PATCH を「enabled 切替」に拡張
- 候補 2：DELETE を Soft-Delete（`deletedAt` タイムスタンプ付与）に変更し、`_list_all` でフィルタ
- 候補 3：別テーブル `KeywordDictionaryDisabled` を新設し、Migration で振り分け

いずれの場合も別 ADR を起票して採用根拠を記録する。

### 6.3 文字列編集の運用ドキュメント整備

Phase 15.x で運用ドキュメント（`docs/operations/runbook.md` 等）に「キーワード文字列を編集する手順」として「DELETE → POST の 2 ステップ運用」を記載する。これにより、管理者ユーザーが UI 上で文字列編集ボタンが見つからない場合に運用手順を参照できるようにする。

## 7. 採用範囲・影響

### 7.1 採用範囲

| 区分                       | 対象                                                                                            |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| Phase 10.9 SPA 実装        | `frontend/src/api/dictionaryClient.ts` / `frontend/src/dictionary/DictionaryManagementPage.tsx` |
| 採用する操作セマンティクス | 案 B（追加 / 無効化 / touch + バージョン表示の 3 操作）                                         |
| 409 Conflict 時の UI 挙動  | 自動再取得 + バナー表示（「他の管理者が辞書を更新しました...」）                                |
| バックエンド改修           | **対象外**（Phase 4.1 実装をそのまま運用）                                                      |

### 7.2 採用範囲外

- design.md 原文の改訂（別途のドキュメント整備タスクで対応）
- `_update_keyword` のセマンティクス拡張（将来「有効フラグ属性」要件追加時に別 ADR で再検討）
- キーワード文字列を 1 操作で編集する UI（明示的に「DELETE → POST」2 段運用に分離）

### 7.3 影響を受ける後続タスク

- **Phase 10.9**：本 ADR の方針に従い案 B で実装（DictionaryManagementPage は touch / 無効化 / 追加の 3 ボタン + バージョン表示 + 409 バナー）
- **Phase 14.x（統合 / 性能テスト）**：辞書管理 UI の動作確認（追加 / 無効化 / touch / 409 競合シナリオ）を統合テストで実施
- **Phase 15.x（運用ドキュメント整備）**：「文字列編集は DELETE → POST 2 段運用」を runbook に記載
- **将来の Phase（要件追加時）**：「有効フラグ属性」要件追加 → 別 ADR 起票 → backend `_update_keyword` 改修検討

## 8. 参照

- `tasks.md` / Phase 4.1（DictionaryApi Lambda）、Phase 10.9（管理者画面：キーワード辞書管理 UI）
- `requirements.md` / Requirement 8.1〜8.4, 8.7（辞書 CRUD + 楽観ロック + 監査）
- `design.md` / Dictionary_Manager（PATCH のセマンティクス記述）
- `backend/lambdas/dictionary_api/handler.py`（`_update_keyword`、Phase 4.1 既存実装）
- `frontend/src/api/dictionaryClient.ts`（Phase 10.9 新規実装、本 ADR 採用方針の実装）
- `frontend/src/dictionary/DictionaryManagementPage.tsx`（Phase 10.9 新規実装、案 B UI 実装）
- `docs/decisions/0001-runtime-selection.md`（Python 3.12 + DynamoDB 採用）
- `docs/decisions/0005-connect-mock-findings.md`（Phase 0.3 代替案で代行、ADR フォーマット参照元）
- DynamoDB Primary Key 不変ルール: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.CoreComponents.html
