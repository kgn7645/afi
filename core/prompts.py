"""
Gemini用プロンプト集。
スプレッドシート＆実記事(COMFEE'扇風機)の解析から抽出した
「どこの国のメーカー？評判レビュー」系の定型フォーマットを再現する。
"""
from __future__ import annotations

import json

from .models import Product

# --- 共通の世界観・文体ガイド ---
STYLE_GUIDE = """\
あなたは日本語のアフィリエイトブログ「アマバイザー」の記事ライターです。
扱うのはAmazonで見つかる無名・新興・中華系メーカーのガジェットや季節家電です。
読者の「このブランド、どこの国の会社？怪しくない？」という不安を解消し、
最終的に「これなら買ってもいいかも」と背中を押すのが目的です。

文体ルール:
- 親しみやすく、友達とおしゃべりしているような口語調。「〜ですよね」「〜なんです」を適度に。
- 誇大広告・断定的な医療/効果効能の表現は避ける。
- 比較は必ず文章で説明する。HTMLテーブル(表)は絶対に使わない。
- 嘘の仕様・幻のレビューを"事実"として断定しない。口コミは「こんな声があります」という体で自然に。
- 企業情報は与えられたヒントを最優先し、不確かな点は断定を避け「〜とされています」とぼかす。
"""


def title_and_meta_prompt(product: Product) -> str:
    """タイトル・キャッチコピー・メタ情報を一括生成（JSON出力）。"""
    return f"""{STYLE_GUIDE}

# 商品情報
- ブランド名: {product.brand}
- カテゴリー: {product.category or "（未指定。商品名から簡潔なカテゴリー名を推定）"}
- 品番/型番: {product.model_number}
- 商品名: {product.product_name}
- 企業ヒント: {product.company_hint or "（不明。一般的な推測でよいが断定は避ける）"}

# タスク
以下を生成し、**JSONのみ**を出力してください（前後の説明文やコードフェンスは禁止）。

{{
  "category": "この商品の簡潔な大カテゴリー名。例『除湿機』『DCモーター扇風機』『スマホ冷却ファン』。10字前後。",
  "title": "記事タイトル。『【...】<ブランド>はどこの国の企業？評判・口コミを徹底レビュー』系。30〜45字程度。煽りすぎず検索されやすく。",
  "catch_copy": "アイキャッチ用キャッチコピー。20〜30字。感情を動かす一文。例『「熱っ！」を「快適」に。夏のスマホ、放置は危険。』",
  "meta_description": "メタディスクリプション。100〜150字。『ブランドの正体(どこの国か)＋製品特徴＋大手との比較＋どんな人におすすめか』を凝縮。",
  "meta_keywords": ["5語前後", "ブランド名", "カテゴリー", "関連語", "関連語"]
}}
"""


def trust_rating_prompt(product: Product, rules: dict) -> str:
    axes = rules.get("article", {}).get(
        "trust_axes",
        ["企業の安定性・規模", "製品の品質・技術力", "日本市場でのサポート体制", "価格競争力"],
    )
    return f"""{STYLE_GUIDE}

# 商品情報
- ブランド名: {product.brand}
- カテゴリー: {product.category}
- 企業ヒント: {product.company_hint or "（不明）"}

# タスク
このブランドの「★当ブログのオリジナル企業信頼度評価(5つ星評価)」を作成します。
次の評価軸それぞれに 1.0〜5.0 の星(0.5刻み可)と一言コメントを付け、総合評価も出してください。
評価軸: {", ".join(axes)}

**JSONのみ**を出力（説明文・コードフェンス禁止）:
{{
  "ratings": [
    {{"axis": "評価軸名", "stars": 4.5, "comment": "一言"}}
  ],
  "total": 4.3,
  "total_comment": "総合評価の締めの一文（無名でも信頼できる、等）"
}}
"""


def article_body_prompt(product: Product, rules: dict, trust_block_md: str) -> str:
    """本文をMarkdownで生成。固定の見出し構成に従わせる。"""
    competitors = rules.get("article", {}).get(
        "competitor_brands", ["パナソニック", "シャープ", "アイリスオーヤマ"]
    )
    min_chars = rules.get("article", {}).get("min_chars", 3000)
    specs_md = "\n".join(f"  - {s}" for s in product.specs) if product.specs else "  - （スペックは商品名から妥当に推定。断定しすぎない）"

    return f"""{STYLE_GUIDE}

# 商品情報
- ブランド名: {product.brand}
- カテゴリー: {product.category}
- 品番/型番: {product.model_number}
- 商品名: {product.product_name}
- 企業ヒント: {product.company_hint or "（不明。断定を避ける）"}
- 既知スペック:
{specs_md}

# タスク
下記の【固定構成】に厳密に従って、Markdownで本文を書いてください。本文合計{min_chars}字以上。
- 見出しは指定の通り（##=大見出し, ###=小見出し）。
- 「企業詳細」と「★企業信頼度評価」のセクションには、後述の【信頼度評価ブロック】を**そのまま差し込む**こと（再生成しない）。
- 「商品スペック」は与えられた既知スペックを箇条書きで。
- 良い口コミ/気になる口コミは、それぞれ4〜5件、実在しそうな自然な体験談を「」付きで。
- 比較は文章のみ。比較対象の大手: {", ".join(competitors)} から、カテゴリーに合うものを2系統選ぶ。
- 最後に必ずまとめで「どんな人に買いか」を提示。

【固定構成】
## はじめに：{product.brand}の{product.category}が話題の理由
（導入。読者の悩み→このブランドに注目する理由）

## {product.brand}とは？：どこの国の企業か、その正体を深掘り
### 企業詳細
（ここに信頼度評価ブロックの企業説明を反映）
### ★当ブログのオリジナル企業信頼度評価(5つ星評価)
（ここに【信頼度評価ブロック】をそのまま差し込む）

## おすすめ商品「{product.full_name}」徹底レビュー
### 商品スペック
### 良い口コミ
### 気になる口コミ
### ポジティブな特色
### ネガティブな特色：購入前に知っておきたい注意点

## 他メーカーと比較！{product.brand}の{product.category}はどんな人にぴったり？
### {product.brand} vs. 国内大手メーカー
### {product.brand} vs. コスパ重視メーカー

## まとめ：{product.brand}の{product.category}はあなたにとって「買い」か？

# 【信頼度評価ブロック】（このまま該当セクションに差し込む）
{trust_block_md}

# 出力
Markdown本文のみ。先頭にタイトル行は不要（h2から始める）。コードフェンス禁止。
"""


def parse_json_response(text: str) -> dict:
    """GeminiのJSON応答を頑健にパース（コードフェンス除去など）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    t = t.strip()
    # 最初の { から最後の } までを抽出
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start : end + 1]
    return json.loads(t)
