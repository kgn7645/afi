"""
Gemini用プロンプト集。
スプレッドシート＆実記事(COMFEE'扇風機)の解析から抽出した
「どこの国のメーカー？評判レビュー」系の定型フォーマットを再現する。
"""
from __future__ import annotations

import json

from .models import Product

# --- 共通の世界観・文体ガイド（config.yaml > prompts.style_guide で上書き可） ---
STYLE_GUIDE_DEFAULT = """\
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


# タイトルの型（config.yaml > prompts.title_format で上書き可）。
# 差し込み語: {brand} {category} {product_name} {model} {year}
DEFAULT_TITLE_FORMAT = "【{year}】{brand}の{category}は買い？評判・口コミ・実力を徹底レビュー"


def _style_guide() -> str:
    from .config import get_rules
    return (get_rules().get("prompts", {}) or {}).get("style_guide") or STYLE_GUIDE_DEFAULT


def _extra_instructions() -> str:
    from .config import get_rules
    return (get_rules().get("prompts", {}) or {}).get("extra_instructions", "").strip()


def _title_format() -> str:
    from .config import get_rules
    return (get_rules().get("prompts", {}) or {}).get("title_format") or DEFAULT_TITLE_FORMAT


def title_and_meta_prompt(product: Product) -> str:
    """タイトル・キャッチコピー・メタ情報を一括生成（JSON出力）。"""
    from datetime import datetime
    cur_year = datetime.now().year
    return f"""{_style_guide()}

# 商品情報
- ブランド名: {product.brand}
- カテゴリー: {product.category or "（未指定。商品名から簡潔なカテゴリー名を推定）"}
- 品番/型番: {product.model_number}
- 商品名: {product.product_name}
- 企業ヒント: {product.company_hint or "（不明。一般的な推測でよいが断定は避ける）"}

# タイトルの型（"title" はこの型に沿って作る）
{_title_format()}
（差し込み語を実際の値に置換: {{brand}}=ブランド名 / {{category}}=カテゴリー /
 {{product_name}}=商品名 / {{model}}=型番 / {{year}}=西暦（今は{cur_year}）。
 型に無い差し込み語は無視。日本語として自然に整え、不自然な空欄や記号残りを作らない）

# タスク
以下を生成し、**JSONのみ**を出力してください（前後の説明文やコードフェンスは禁止）。

{{
  "category": "この商品の簡潔な大カテゴリー名。例『除湿機』『DCモーター扇風機』『スマホ冷却ファン』。10字前後。",
  "title": "記事タイトル。上の【タイトルの型】に従う。30〜45字程度、煽りすぎず検索されやすく。",
  "catch_copy": "アイキャッチ用キャッチコピー。20〜30字。感情を動かす一文。例『「熱っ！」を「快適」に。夏のスマホ、放置は危険。』",
  "meta_description": "メタディスクリプション。100〜150字。『ブランドの正体（メーカーの背景・信頼性）＋製品特徴＋大手との比較＋どんな人におすすめか』を凝縮。",
  "meta_keywords": ["5語前後", "ブランド名", "カテゴリー", "関連語", "関連語"]
}}
"""


def trust_rating_prompt(product: Product, rules: dict) -> str:
    axes = rules.get("article", {}).get(
        "trust_axes",
        ["企業の安定性・規模", "製品の品質・技術力", "日本市場でのサポート体制", "価格競争力"],
    )
    return f"""{_style_guide()}

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
    min_chars = rules.get("article", {}).get("min_chars", 6000)
    reviews_each = rules.get("article", {}).get("reviews_each", 5)
    extra = _extra_instructions()
    extra_block = f"\n# 追加の指示（編集者より）\n{extra}\n" if extra else ""
    specs_md = "\n".join(f"  - {s}" for s in product.specs) if product.specs else "  - （スペックは商品名から妥当に推定。断定しすぎない）"

    return f"""{_style_guide()}

# 商品情報
- ブランド名: {product.brand}
- カテゴリー: {product.category}
- 品番/型番: {product.model_number}
- 商品名: {product.product_name}
- 企業ヒント: {product.company_hint or "（不明。断定を避ける）"}
- 既知スペック:
{specs_md}

# タスク
下記の【固定構成】に厳密に従って、Markdownで本文を書いてください。**本文合計{min_chars}字以上**（薄い記事はNG。各セクションを具体例・数字・生活シーンで厚く）。
- 見出しは指定の通り（##=大見出し, ###=小見出し）。
- 「企業詳細」と「★企業信頼度評価」のセクションには、後述の【信頼度評価ブロック】を**そのまま差し込む**こと（再生成しない）。
- 「商品スペック」は与えられた既知スペックを箇条書きで。
- **導入**は読者の生活シーン・あるあるの悩みから入り、比喩を1つ使って引き込む（例：住宅事情／猛暑／在宅ワークなど、カテゴリーに合う情景）。
- **口コミは良い/気になるを各{reviews_each}件**、実在しそうな自然な体験談を「」付きで（使う場面・期間・具体的な数字感を含める）。
- **ペルソナ**を明確に（「一人暮らし向け」「在宅ワークの人向け」等、誰に刺さるかを言い切る）。
- **利用シミュレーション**で、買った後の使い方・設置/収納・1日の使い方を具体的に描く。
- メリット/デメリットは双方を厚く。デメリットも正直に書くことで信頼を得る。
- 比較は文章のみ。比較対象の大手: {", ".join(competitors)} から、カテゴリーに合うものを2系統選ぶ。
- 最後に必ずまとめで「どんな人に買いか／どんな人は避けるべきか」を提示。
- **【薬機法・景表法ガードレール（化粧品・スキンケア・日焼け止め・食品・飲料・サプリ等の消耗品で厳守）】**:
  効果効能を断定しない。NG例「シミが消える」「シワがなくなる」「日焼けを完全に防ぐ」「飲むだけで痩せる」「免疫力が上がる」「アンチエイジング」「デトックス」「副作用なし」「医薬品レベル」。
  言い換え「うるおいを与える」「メイクのりを助ける」「自分に合うか様子を見ながら」等、化粧品で認められた範囲＋"個人の感想"の体に留める。
  医薬品的な治療・治癒・身体機能の改善は書かない。最大級表現(最高/No.1/絶対)も根拠なく使わない。
- **企業セクションの扱い**: 無名・新興・海外系ブランドなら「どこの国の会社か」をはっきり明らかにして不安を解消する。誰もが知る国内大手・有名ブランドなら出自を煽らず、実績・サポート体制・信頼性を中心に語る（不自然に「どこの国？」と書かない）。

【固定構成】
## はじめに：{product.brand}の{product.category}が話題の理由
（生活シーン＋悩み＋比喩で引き込む導入。読者の悩み→このブランドに注目する理由）

## {product.brand}とは？：メーカーの正体と信頼性を深掘り
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
### こんな人におすすめ（ペルソナ）
### 使ってみたら？利用シミュレーション
（設置/収納・1日の使い方・買った後の生活の変化を具体的に）

## 他メーカーと比較！{product.brand}の{product.category}はどんな人にぴったり？
### {product.brand} vs. 国内大手メーカー
### {product.brand} vs. コスパ重視メーカー

## まとめ：{product.brand}の{product.category}はあなたにとって「買い」か？
（買うべき人／避けるべき人を言い切って締める）

# 【信頼度評価ブロック】（このまま該当セクションに差し込む）
{trust_block_md}

# 出力
Markdown本文のみ。先頭にタイトル行は不要（h2から始める）。コードフェンス禁止。
{extra_block}"""


def company_grounding_prompt(brand: str, category: str) -> str:
    """Web検索で企業情報を裏付けるためのプロンプト（Issue #15: どこの国の誤生成対策）。"""
    return f"""次のブランドについて、Web検索で事実を確認し、日本の消費者向けに簡潔にまとめてください。

# ブランド
{brand}（カテゴリー: {category or "不明"}）

# 知りたいこと
- どこの国の企業か（本社所在地・国）
- 設立年・親会社/グループ（分かれば）
- 主な事業・取り扱い製品ジャンル
- 日本での販売状況・サポートの有無（分かれば）

# 厳守
- 検索で**確認できた事実のみ**。確証が無い項目は「公開情報では確認できず」と明記し、推測で断定しない。
- 同名の別企業と混同しない（{category or "該当ジャンル"}を扱うブランドに限定）。
- 国・本社が特定できない場合は「国・本社は公開情報では特定できず」と明記する。

# 出力
箇条書きで5行以内。誇張なし。事実とその確度のみ。
"""


def category_pick_prompt(product: Product, categories: list[dict], site_concept: str) -> str:
    """既存カテゴリの中から記事に最も合うものを1つ選ばせる（slugを返す）。"""
    cat_lines = "\n".join(f"- {c['slug']}: {c['name']}" for c in categories)
    return f"""あなたは「{site_concept}」というメディアの編集者です。
次の商品レビュー記事を、既存カテゴリの中で最も自然な1つに分類してください。
在宅ワーク・おうち時間の文脈で「読者にとってどの棚にあると探しやすいか」で選びます。

# 商品
- ブランド: {product.brand}
- カテゴリー: {product.category}
- 商品名: {product.product_name}

# 既存カテゴリ（slug: 名前）
{cat_lines}

# 出力
最適なカテゴリの **slug を1つだけ** 出力（説明・記号・引用符なし。例: desk）。
該当が薄い場合でも、最も近いものを必ず1つ選ぶこと。
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
