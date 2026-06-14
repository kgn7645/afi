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

import html as _html
import re
import uuid

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


def _block(tag: str, inner: str) -> str:
    """noteのブロック要素（name/id にUUIDを付与）。"""
    uid = str(uuid.uuid4())
    return f'<{tag} name="{uid}" id="{uid}">{inner}</{tag}>'


def _inline(text: str) -> str:
    """**bold** と [text](url) を最低限HTML化し、それ以外はエスケープ。"""
    # 先にエスケープ → マーカーを復元する方式で安全に変換
    parts: list[str] = []
    # リンク [t](u)
    pos = 0
    for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)]+)\)", text):
        parts.append(_bold(_html.escape(text[pos:m.start()])))
        parts.append(f'<a href="{_html.escape(m.group(2))}">{_html.escape(m.group(1))}</a>')
        pos = m.end()
    parts.append(_bold(_html.escape(text[pos:])))
    return "".join(parts)


def _bold(escaped: str) -> str:
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped.replace("**", "")  # 対になっていない ** が文字列として残らないよう除去


def build_note_html(article: Article, product: Product) -> tuple[str, int]:
    """note内部API投入用のHTML本文と本文文字数を返す。"""
    body_md = article.raw_sections.get("body_md", "")
    blocks: list[str] = []
    text_len = 0

    def add(tag: str, raw_text: str) -> None:
        nonlocal text_len
        blocks.append(_block(tag, _inline(raw_text)))
        text_len += len(raw_text)

    add("p", DISCLOSURE)

    for line in body_md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            add("h3", s[4:])
        elif s.startswith("## "):
            add("h2", s[3:])
        elif s.startswith("# "):
            add("h2", s[2:])
        elif s.startswith(("- ", "* ", "・")):
            add("p", "・" + s.lstrip("-*・ ").strip())
        else:
            add("p", s)

    if article.affiliate_click_url:
        label = " ".join(p for p in (product.brand, product.category) if p) or "この商品"
        link = f'▼ <a href="{_html.escape(article.affiliate_click_url)}">{_html.escape(label)}をチェック</a>'
        uid = str(uuid.uuid4())
        blocks.append(f'<p name="{uid}" id="{uid}">{link}</p>')
        text_len += len(label) + 8

    return "".join(blocks), text_len
