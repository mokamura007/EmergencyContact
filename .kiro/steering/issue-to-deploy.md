---
inclusion: manual
---

# Issue → 修正 → デプロイ 手順ガイド（参照エントリ）

Issue 起点の変更を dev で検証してから main まで反映する手順は下記ドキュメントに集約している。ユーザーが `#issue-to-deploy` で明示参照した際に、以下ファイルを読み込んで手順に従うこと。

#[[file:../../docs/operations/issue-to-deploy.md]]

## 適用トリガー

以下のいずれかに該当する場合、本手順ガイドに従う。

- ユーザーが Issue 番号（`#N`）を挙げて「対応する」「修正する」「デプロイする」旨を指示した時
- Issue → PR → デプロイ の流れで作業を進める意図が明示された時
- ユーザーが `#issue-to-deploy` を明示参照した時

## 特に注意する箇所

- **Step 2**: spec 作成は必須。Issue のサイズに関わらず `.kiro/specs/<slug>/{requirements,design,tasks}.md` を作る。
- **Step 5**: ローカル検証（`npm run dev` @ localhost:5173）はデプロイ前に必ず実施。デプロイ→実機確認の往復を避ける。
- **Step 6**: デプロイ後は **3 項目チェックリスト**（S3 Cache-Control 強制 / CloudFront invalidation / 実機ハードリロード）を省略しない。
- **Step 8**: PR チェーン（feature → develop → main → develop back-merge）は最後の back-merge まで完遂する。main の先行を放置しない。
- **§11-4**: 3 回同じアプローチで失敗したら、SPA コード推測ではなく AWS 側の直接検証（CLI / CloudWatch Logs 等）に切り替える（Task 1 の教訓）。
