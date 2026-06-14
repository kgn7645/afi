# Issue #45 デザイン・CTA・回遊の改善ガイド

記事のCTA・商品カード・著者ボックスはツール側で**インラインCSS付きHTML**を出力するため、テーマ非依存で最低限の見た目は整います。
さらに **hover・アニメーション・モバイル微調整・関連記事の装飾**を効かせるため、以下を **Cocoon の「外観 > カスタマイズ > 追加CSS」** に貼り付けてください（テーマ更新で消えません）。

## 1. 追加CSS（コピペ）

```css
/* === アフィリエイトCTAボタン === */
.amazon-cta-btn{
  transition: transform .12s ease, box-shadow .12s ease, filter .12s ease;
}
.amazon-cta-btn:hover{
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,.22);
  filter: brightness(1.03);
}

/* === 商品カード === */
.amazon-card{
  transition: box-shadow .15s ease;
}
.amazon-card:hover{ box-shadow: 0 4px 16px rgba(0,0,0,.14); }
.amazon-card-title:hover{ text-decoration: underline; }

/* スマホ: 画像を中央に、テキストは下へ回り込み */
@media (max-width: 480px){
  .amazon-card{ justify-content:center; text-align:center; }
  .amazon-card > a:first-child{ flex-basis:100%; text-align:center; }
  .amazon-card img{ margin:0 auto; }
}

/* === 著者ボックス === */
.author-box{ font-size:.95em; }
.author-box a{ color:#0a58ca; }

/* === 関連記事(回遊)を少し強調（Cocoon標準ブロック） === */
.related-entry-card-title{ font-weight:bold; }
```

## 2. Cocoon 設定チェックリスト（管理画面で実施）

- [ ] **目次**: Cocoon設定 > 目次 > 「目次を表示する」ON（H2/H3、本文の最初の見出し前）
- [ ] **関連記事**: Cocoon設定 > 関連記事 > 表示ON（カテゴリ自動割当=#44済みなので関連が出やすい）
- [ ] **モバイル**: Cocoon設定 > モバイル > メニュー/ボタン表示を確認（ファーストビュー重視）
- [ ] **サイトカラー**: スキン/全体 でアクセント色を1色に統一（CTAの#ff9900と喧嘩しない配色）
- [ ] **記事下**: Cocoon設定 > 投稿 > 「SNSシェア」「関連記事」「前後記事」を表示で回遊導線を確保
- [ ] **ファーストビュー**: 不要なウィジェット/サイドバー項目を整理（離脱低減）

## 3. ツール側で自動化済み（コード）

- CTAボタン／商品カードに **box-shadow と hover用クラス**（`.amazon-cta-btn` / `.amazon-card`）を付与
- 商品カードを **flex-wrap でモバイル折り返し対応**
- 記事末尾に **著者情報ボックス**（#44）
- **カテゴリ自動割当**（#44）により Cocoon の関連記事が機能 = 回遊改善

## 4. 残（任意・別Issue）

- 本文中の**関連記事への内部リンク自動挿入** → #18
- 価格・Prime表示付きのリッチカード → PA-API 移行（アソシエイト審査通過後）
