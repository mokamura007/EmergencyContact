# Amazon Connect 契約・接続時タスク

> 本文書は Amazon Connect の正式契約後に実施すべきタスクを記録する。

---

## 前提

- Amazon Connect インスタンスの契約・購入が承認済みであること
- 電話番号（発信用・着信用）が取得済みであること

---

## タスク一覧

### 1. InboundContact テーブルのフィールドレベル暗号化

**優先度: 高**

Employee テーブルと同様に、InboundContact テーブルの `callerNumber` フィールドをアプリケーション層で暗号化する。

対象:
- `callerNumber`（E.164形式の電話番号）

実装手順:
1. `backend/lambdas/inbound_handler/handler.py` の `_put_provisional_inbound_contact()` 内で `callerNumber` を `encrypt_field()` で暗号化して保存
2. InboundHandler Lambda に `FIELD_ENCRYPTION_ENABLED`, `FIELD_ENCRYPTION_KEY_ENCRYPTED`, `BLIND_INDEX_HMAC_KEY` 環境変数を追加（template.yaml）
3. InboundHandler の IAM ロールに KMS Decrypt 権限を追加
4. `callerNumberBlindIndex` フィールドを追加し、着信時の電話番号逆引き検索に使用
5. Response API の `/inbound` 一覧取得で `callerNumber` を復号して返す（ただし `callerNumberMasked` は既にマスク済みなので一覧表示にはマスク版を使用）

注意事項:
- `callerNumberMasked` フィールドはマスク済み（例: `***-****-5678`）なので暗号化不要
- InboundHandler は Amazon Connect Contact Flow から呼ばれるため、API Gateway 経由ではない

### 2. Connect インスタンスの設定

- Connect Instance ID を `parameters/dev.json` のダミー値から実値に更新
- 発信用電話番号 ARN を更新
- 着信用電話番号 ARN を更新
- Outbound Contact Flow ID を更新
- Inbound Contact Flow ID を更新
- `MockMode` を `false` に変更

### 3. Contact Flow のデプロイ

- `infrastructure/contact-flows/outbound.json` を Connect にインポート
- `infrastructure/contact-flows/inbound.json` を Connect にインポート
- Polly TTS の音声設定（ja-JP）を確認
- 録音出力先 S3 バケットの設定

### 4. 実通話テスト

- テスト番号への発信動作確認
- TTS ガイダンスの再生確認
- 録音→S3保存→Transcribe→KeywordMatcher の経路確認
- インバウンド着信→Cycle紐付け→Response更新の経路確認
- 再発信（リトライ）動作の確認

### 5. Staging/Production 環境構築

- Staging 用パラメータファイル `parameters/stg.json` を作成
- Production 用パラメータファイル `parameters/prod.json` を作成（MockMode=false 必須）
- 各環境のSSMパラメータ（暗号化キー・HMACキー）を生成
- 各環境のSecrets Manager シークレット（anonymize salt）を作成
- KMS CMK を各環境で作成

---

## 完了条件

- [ ] InboundContact の callerNumber が暗号化されている
- [ ] Connect Instance ID / 電話番号が実値に更新されている
- [ ] MockMode = false でサイクル起動→通話→応答収集の全経路が動作する
- [ ] Staging 環境で1サイクル（10名以上）の実通話テストが成功する
