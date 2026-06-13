# Issue #17 インデックス促進

公開記事を検索エンジンに早く拾わせるための仕組み。収益（月15万円）は検索流入が前提なので、新規記事のインデックスを促進する。

## 手段と方針
| 手段 | 効果 | 自動化 | 本ツールの対応 |
|------|------|--------|----------------|
| **IndexNow**（Bing/Yandex等） | 即時インデックス通知 | ◎ 完全自動 | `index_submit.py` で実装 |
| XMLサイトマップ | クロール網羅性 | WP標準で自動生成 | GSCへ一度登録（手順下記） |
| Google 即時インデックス | — | ✕ | 一般ページ向けの公式APIは無い（後述） |

> ⚠️ **Googleについて正直な注意**: Googleには一般的なWebページを「今すぐインデックス」する公式APIはありません（Indexing APIはJobPosting/BroadcastEvent限定）。Googleはサイトマップ＋自然クロールに任せ、IndexNowでBing等を即時化する、という役割分担が現実的です。

## ⚠️ 重要：キーファイルは「サイトのルート」に置く
IndexNowは**キーファイルの場所より下の階層のURLしか認証しない**。記事URLはサイト直下
（`https://ouchibase.com/記事名/`）なので、キーファイルは**ルート**に置く必要がある。
`/wp-content/uploads/...` に置くと422エラー（URL未認証）になる。

WordPress REST APIではルート直下にファイルを置けないため、以下のいずれかで設置する。

### 方法A（推奨・非属人）: SEOプラグインのIndexNow機能を使う
Rank Math（`config.yaml`の既定）やBing公式「IndexNow」プラグインのIndexNowを有効化すると、
**キー設置と公開時の自動送信をプラグインが代行**する。本スクリプトは不要になり、最も手間がない。

### 方法B: 本スクリプト＋手動でルート設置
```bash
python index_submit.py --setup
```
- キーを生成し、`data/<key>.txt` を出力
- このファイルを**サイトのルート**（`https://ouchibase.com/<key>.txt`）にFTP等で設置
- 表示された `INDEXNOW_KEY` / `INDEXNOW_KEY_LOCATION`（ルートURL）を `.env` に追記

## 通常運用
```bash
python index_submit.py          # 前回以降に公開された記事をIndexNowへ送信
python index_submit.py --all    # 状態を無視して直近の公開記事を再送信
```
- 公開はWordアプリで人が行うため、本スクリプトは「公開済み(status=publish)」を検知して送信する
- 送信済みの位置は `data/index_state.json`（公開日時）で管理し、重複送信を避ける

### cron例（30分毎）
```
*/30 * * * *  cd /path/to/affiliate-automation && .venv/bin/python index_submit.py >> data/index.log 2>&1
```

## XMLサイトマップ（GSCへ一度だけ登録）
1. WordPressは標準でサイトマップを生成: `https://ouchibase.com/wp-sitemap.xml`
   （Rank Math/Yoast使用時はそちらのサイトマップURL）
2. Google Search Console → サイトマップ → `wp-sitemap.xml` を送信（初回のみ）
3. 以降はGoogleが自動巡回。インデックス状況の監視は Issue #20 で扱う。

## 残タスク（フォロー）
- [ ] `--setup` を実環境で実行しキーファイル設置を確認
- [ ] GSCにサイトマップ登録（手動・一度きり）
- [ ] 公開→IndexNow送信の実地確認（Bing Webmaster Toolsで受理を確認）
