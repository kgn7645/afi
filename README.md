# アフィリエイト記事 自動化ツール

Amazonの無名・中華系メーカー商品を題材にした「**○○はどこの国のメーカー？評判・口コミレビュー**」系の定型記事を、**Gemini（無料枠）** で自動生成し、**WordPress（`amaviser.com`）へ下書き投稿**するツールです。
元のスプレッドシート運用（A〜E作業）を解析し、その作業フローを自動化しています。

## 対応している作業工程

| 工程 | 元シート | 本ツールの自動化 |
|------|----------|------------------|
| A | 商品選定 | 価格3000円以上 / 在庫あり / 消え物・化粧品・薬品の除外を自動判定（`config.yaml`） |
| B | 基本情報整理 | Amazon URLからブランド・型番・スペック抽出（手動入力も可）＋タイトル・キャッチコピー生成 |
| C | AI記事作成 | Geminiで「企業の正体→★5つ星信頼度→レビュー→大手比較→まとめ」の定型本文を生成 |
| D | アフィリエイトリンク | もしもかんたんリンクHTMLを本文へ自動挿入（未取得時はプレースホルダ） |
| E | WordPress作業 | メタディスクリプション/キーワード生成 → REST APIで下書き投稿 |

> note投稿・ショート動画・Canvaアイキャッチは現時点で対象外（拡張余地として後述）。

## セットアップ

```bash
cd affiliate-automation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 値を埋める
```

### .env に設定するもの
- `GEMINI_API_KEY` … [Google AI Studio](https://aistudio.google.com/app/apikey) で無料発行
- `GEMINI_MODEL` … **`gemini-2.5-flash-lite` 推奨**（量産向け）。`gemini-2.5-flash`は無料枠が1日20件と極端に少なく、`gemini-2.0-flash`は無料枠対象外（実測）。安定した量産が必要なら従量課金(Tier1・月数百円規模)も検討
- `WP_BASE_URL` / `WP_USERNAME` / `WP_APP_PASSWORD` … WordPress管理画面 > ユーザー > プロフィール > **アプリケーションパスワード** で発行

### config.yaml
選定ルール（最低価格・除外キーワード）、記事の文体、比較対象の大手メーカー、SEOプラグイン種別（`rankmath`/`yoast`）を調整できます。

## 使い方

### Web UI（推奨）
```bash
python app.py
# → http://127.0.0.1:8000 をブラウザで開く
```
フォームにAmazon URL（または手動で商品情報）を入力 → 「記事を生成する」→ プレビュー確認 → WordPressに下書き保存。

### CLI（単発）
```bash
python cli.py --url "https://www.amazon.co.jp/dp/XXXXXXXXXX" --category "DCモーター扇風機"
python cli.py --brand COMFEE' --category 扇風機 --model CFS-12 --name "..." --no-wp   # WP送らず確認のみ
```

### バッチ（量産・無人運用 / Issue #9・#10）
キューCSV（`data/queue.example.csv` をコピーして `data/queue.csv` を作成）から、
1日N件をまとめて生成→WP下書き。既出商品（`articles_log.csv`）は自動でスキップ。
```bash
cp data/queue.example.csv data/queue.csv   # 商品リストを編集
python batch.py --limit 15                  # 15件まで生成
python batch.py --no-wp                      # WP送らず確認のみ
```
キューCSVの列: `brand,category,model_number,product_name,price,company_hint,url,affiliate_link_html`
（`brand`+`category` か `url` があれば最小限でOK。リンクは未指定なら楽天検索で自動生成）

**Googleスプレッドシートをキューにする（推奨・Issue #32）**：シートを「ウェブに公開（CSV）」し、
そのURLを `.env` の `QUEUE_SHEET_CSV_URL` に設定すると、レビュー担当がブラウザで商品を追記するだけで
バッチが拾う。設定・運用は [docs/issue-32-sheet-queue.md](./docs/issue-32-sheet-queue.md) 参照。

cron例（毎朝6時に15件・サーバー常駐運用、Issue #21で整備）:
```
0 6 * * *  cd /path/to/affiliate-automation && .venv/bin/python batch.py --limit 15 >> data/batch.log 2>&1
```

### note用Markdownの出力（Issue #2）
noteは公式投稿APIが無いため、note用に整形した本文（プレーン成果リンク＋広告表記）を生成し、
noteエディタへ貼り付ける運用。`--note` で出力できる。
```bash
python cli.py --brand RANVOO --category ネッククーラー --no-wp --note
```
詳細: [docs/issue-02-note.md](./docs/issue-02-note.md)

### もしもリンクの自動生成（Issue #8）
`.env` に `MOSHIMO_AID`（もしもの成果ID）と `RAKUTEN_APP_ID`/`RAKUTEN_ACCESS_KEY`
（楽天ウェブサービス・無料）を設定すると、リンク未指定時にブランド+カテゴリで
楽天検索し、もしもかんたんリンクを自動生成・挿入する。

## 設計ドキュメント
- [アーキテクチャ設計書](./docs/design/architecture.md)（全体像・データフロー・責務）
- [インフラ／デプロイ設計書](./docs/design/infrastructure.md)（実行環境・cron・シークレット）
- [運用体制・役割定義書](./docs/design/operations.md)（管理者／レビュー担当・1日の流れ）

## 運用（スマホでチェックのみ）
担当者はスマホのWordPress公式アプリで下書きを確認→公開するだけ。手順とチェック観点：
- [スマホ承認 運用手順書（Runbook）](./docs/runbook-review.md)
- [記事チェックリスト（公開前確認）](./docs/checklist-review.md)

## 注意・既知の制約
- **Amazon自動抽出**はbot対策で失敗することがあります。その場合はフォームの手動入力で補完してください（両対応設計）。商用の安定取得が必要なら Amazon PA-API への差し替えを推奨。
- **企業情報の正確性**: Gemini無料枠は検索グラウンディングが弱く、企業の国籍・沿革を誤る可能性があります。`企業ヒント`欄に正しい情報を渡すと精度が上がります。**公開前に必ず人がチェック**してください（既定が下書き保存なのはこのため）。
- **もしもアフィリエイト**は公開APIが無いため、リンクHTMLは手動取得して貼り付けるか、プレースホルダのまま投稿し後で差し替えます。
- 薬機法・景表法に触れる表現が出ないようプロンプトで抑制していますが、最終確認は人が行ってください。

## 今後の拡張余地
- Canvaアイキャッチ自動生成（Canva API / 画像生成）
- note自動投稿
- Amazon売れ筋ランキングからの商品候補自動収集（A作業の完全自動化）
- Googleスプレッドシートへの実績書き戻し（現在は `data/articles_log.csv` に記録）

## ディレクトリ
```
core/        ロジック（抽出・選定・生成・WP投稿・オーケストレーション）
web/         Web UIテンプレート
data/        投稿ログ(CSV)
tests/       スモークテスト
app.py       Web UI起動
cli.py       コマンド実行
config.yaml  生成ルール
```
