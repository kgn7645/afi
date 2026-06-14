# Issue #2 note への投稿対応

note（https://note.com/ouchibase）への投稿対応。

## 調査結論：完全自動投稿は非推奨
- noteには**公式の投稿APIが無い**（2026年時点・公開予定未定）。
- 非公式API（ログインCookie利用）での自動投稿は技術的には可能だが、
  **note利用規約のリスク・Cookie失効・仕様変更による破綻リスク**が高い。
- サーバー（エックスサーバー共有）は**ヘッドレスブラウザを動かせない**ため、ブラウザ自動化も非現実的。

→ 本ツールは「**note用に整形した本文を自動生成**し、投稿はnoteエディタへ貼り付け」という
半自動方式を採用する（生成は自動、投稿のみ人手で数秒）。

## note版の特徴
- **もしものかんたんリンク(JS widget)はnoteで表示できない** → プレーンな成果リンク
  （`af.moshimo.com/af/c/click...`）をテキストリンクで掲載（noteは素URLを自動リンク化）。
- 冒頭に**広告表記**（景表法/note規約対応）。
- 本文はWordPress版と同じ「どこの国？」構成のMarkdown。

## 使い方
```bash
# 記事生成と同時にnote用Markdownを出力（data/note/ に保存＋端末に表示）
python cli.py --brand RANVOO --category ネッククーラー --no-wp --note
```
- `data/note/<日時>_<slug>.md` に保存され、端末にも貼り付け用テキストが表示される。
- それをnoteエディタに貼り付け → タイトルを設定 → 投稿。

## 運用フロー
1. WordPress下書きをレビュー（既存フロー）
2. note にも出す記事は、`--note` で生成したMarkdownをnoteへ貼り付け
3. note側で公開

## 自動下書き作成（非公式API・実装済み）
ブラウザのキャプチャから判明した内部APIで、note下書きを自動作成できる（**公開はしない**）。

⚠️ **非公式・規約上非推奨・仕様変更で予告なく壊れうる。自分のアカウントの自動化用。**

### 内部APIの仕様（キャプチャで確認）
1. `POST https://note.com/api/v1/text_notes`  body `{"template_key": null}` → 下書きid取得
2. `POST https://note.com/api/v1/text_notes/draft_save?id={id}&is_temp_saved=true`
   body `{"body": <HTML>, "body_length": N, "name": <title>, "index": false, "is_lead_form": false}`
- 認証: Cookie `_note_session_v5` ＋ ヘッダ `x-requested-with: XMLHttpRequest`（XSRF不要）
- 本文: HTML（各ブロックに `name`/`id` のUUID）

### セットアップ
`.env` に Cookie を設定:
```
NOTE_SESSION=<ブラウザの _note_session_v5 の値>
```
取得: note にログイン → DevTools → Application → Cookies → `note.com` → `_note_session_v5` の値。
**セッションは時間が経つと失効する**ため、失効したら取り直して更新する。

### 使い方
```bash
python note_post.py --test                                  # セッション確認
python note_post.py --brand SOUNDPEATS --category ワイヤレスイヤホン   # 生成→note下書き作成
```
作成後、noteの「下書き」一覧に入る → 内容を確認して**note側で公開**する。
（cronでの無人投稿はCookie失効リスクのため非推奨。投稿したい時に手動実行する運用を推奨）

### 記事の構成（参考記事 amaviser に準拠）
- **アフィリエイトリンクを3箇所**配置（はじめに後／商品紹介後／まとめ後）。
  もしも経由の収益を維持するため、noteのリンクカード（note独自Amazonタグ）ではなく
  `af.moshimo.com` のプレーンリンクを使う。
- **商品画像を最大3枚**、商品紹介セクションに埋め込み。
  楽天APIで得た商品画像を note のCDN(assets.st-note.com)へアップロードして使用。
  - 画像アップロードは note の presigned POST 方式（`/api/v3/images/upload/presigned_post`
    → S3へPOST）。`core/note_client.py:upload_image`。
- PR表記は末尾。

> 見出し画像(eyecatch)は未対応（本文の先頭画像がサムネイルに使われることが多い）。必要なら別途対応。
