"""
note 用に記事を整形する（Issue #2）。

noteには公式の投稿APIが無く、非公式自動投稿は規約・安定性リスクが高い。
そこで本モジュールは「note用に整形した本文（Markdown）」を生成し、
投稿はnoteエディタへ貼り付ける運用とする（生成は自動・投稿のみ手動）。

note特有の考慮:
- もしもの「かんたんリンク」JS widgetはnoteで表示できない
  → プレーンな成果リンク(af.moshimo.com/af/c/click)をテキストリンクで置く
- 景表法/note規約に沿い、冒頭に広告表記を入れる
"""
from __future__ import annotations

from .models import Article, Product

DISCLOSURE = "※この記事はアフィリエイト広告（もしもアフィリエイト）を含みます。"


def build_note_markdown(article: Article, product: Product) -> str:
    """note貼り付け用のMarkdownを生成。"""
    body_md = article.raw_sections.get("body_md", "")
    parts: list[str] = [DISCLOSURE, ""]

    # noteのタイトルは別入力欄なので本文先頭にも見出しとして置く
    if article.title:
        parts.append(f"# {article.title}")
        parts.append("")

    parts.append(body_md.strip())

    # 商品リンク（プレーンな成果URL）。note は素のURLを自動でリンク化する
    if article.affiliate_click_url:
        label = " ".join(p for p in (product.brand, product.category) if p) or "この商品"
        parts.append("")
        parts.append("---")
        parts.append(f"▼ {label}をチェック")
        parts.append(article.affiliate_click_url)

    return "\n".join(parts).strip() + "\n"
