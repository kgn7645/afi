# サムネイル / OGP 設定ガイド

記事一覧・SNSシェアのサムネ（OGP画像）を正しく出すための設定。コードで自動化される部分と、Cocoon/RankMath側の手動設定をまとめる。

## 1. アイキャッチ（featured image）

- **新規記事**: パイプラインが商品画像を自動でアイキャッチ設定（#42）。対応不要。
- **過去記事の補完**: 本文に画像がある投稿は次で一括補完できる。
  ```
  python scripts/backfill_thumbnails.py --dry-run   # 対象確認
  python scripts/backfill_thumbnails.py             # 実行
  ```
- **本文に画像が無い旧記事**（#41のカード導入前に生成したもの）は補完元が無いため、
  新パイプラインで**再生成**するのが確実（カード＋アイキャッチ＋グラウンディング＋QAが付く）。

## 2. OGP の重複を解消（重要）

現状、**Cocoon と RankMath の両方が OGPタグを出力**しており重複している。どちらかに一本化する。

**推奨：RankMath に一本化**
- WordPress管理 > Cocoon設定 > OGP > 「OGPタグの自動挿入」を **OFF**
- RankMath > 一般設定 > リンク/ソーシャル で OGP/Twitter を有効に

（RankMath を使わない場合は逆に、RankMath のソーシャルメタを切って Cocoon に寄せる）

## 3. Twitterカードを大きく（summary_large_image）

記事ページの Twitterカードが `summary`（小）になっている。大きい画像付きにする。

- **RankMath**: 管理 > RankMath > タイトル&メタ > ソーシャルメタ > 「Twitterカードタイプ」= **Summary Card with Large Image**
- **Cocoon の場合**: Cocoon設定 > OGP > Twitterカードタイプ = **summary_large_image**

## 4. og:image の既定画像

アイキャッチ未設定ページの og:image がテーマ既定（`cocoon-master/screenshot.jpg`）になる。
- アイキャッチを設定すればページ個別の og:image はそれに置き換わる（上記1で解消）。
- トップ等の既定 og:image を変えたい場合: RankMath > タイトル&メタ > ソーシャルメタ > 「デフォルト画像（OG画像）」を設定。

## 5. 確認方法

```
curl -s -A "Mozilla/5.0" https://ouchibase.com/<記事URL> \
  | grep -oiE '<meta (property="og:image"|name="twitter:card"|name="twitter:image")[^>]*>'
```
`og:image` が実画像、`twitter:card` が `summary_large_image` になっていればOK。
SNS側のキャッシュは [Facebookシェアデバッガー] / [X Card Validator] で更新できる。
