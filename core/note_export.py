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


def get_image_size(data: bytes) -> tuple[int, int]:
    """PNG/JPEGのバイト列から (幅, 高さ) を読む。失敗時は (620, 620)。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:2] == b"\xff\xd8":  # JPEG
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:  # noqa: BLE001
        pass
    return 620, 620


def _image_figure(url: str, width: int, height: int, link: str = "") -> str:
    uid = str(uuid.uuid4())
    # note本文の表示幅(約620px)に合わせて縮小
    if width > 620:
        height = max(1, round(height * 620 / width))
        width = 620
    img = f'<img src="{_html.escape(url)}" alt="" width="{width}" height="{height}">'
    if link:  # 画像自体をクリックで成果リンクへ（バナー的に誘導）
        img = f'<a href="{_html.escape(link)}" rel="nofollow noopener" target="_blank">{img}</a>'
    return f'<figure name="{uid}" id="{uid}">{img}<figcaption></figcaption></figure>'


# アフィリエイトリンクを挿入するh2見出しの位置（1始まり）。
# テンプレ構成: 1=はじめに 2=とは 3=おすすめ商品 4=他メーカー比較 5=まとめ
# → 2番目(はじめに後)と4番目(商品紹介後)の見出し直前、および末尾(まとめ後)の計3箇所。
_LINK_BEFORE_H2 = {2, 4}


def build_note_html(article: Article, product: Product,
                    note_images: list[tuple[str, int, int]] | None = None) -> tuple[str, int]:
    """note内部API投入用のHTML本文と本文文字数を返す。

    - アフィリエイトリンクを本文中の複数箇所(計3箇所)に配置（参考記事に合わせ）
    - note_images（アップロード済みの(URL,幅,高さ)）があれば商品紹介セクションに画像を挿入
    """
    body_md = article.raw_sections.get("body_md", "")
    note_images = note_images or []
    blocks: list[str] = []
    text_len = 0

    label = " ".join(p for p in (product.brand, product.category) if p) or "この商品"
    promo_idx = 0  # 誘導ブロックを置いた回数（画像の巡回に使用）

    def add(tag: str, raw_text: str) -> None:
        nonlocal text_len
        blocks.append(_block(tag, _inline(raw_text)))
        text_len += len(raw_text)

    def add_promo() -> None:
        """商品画像＋リンクの『誘導ブロック』を1つ追加（画像→リンクでクリック誘導）。"""
        nonlocal text_len, promo_idx
        if not article.affiliate_click_url:
            return
        # 画像（あれば巡回して使う・画像自体もリンク化）→ テキストリンクの順で誘導力を上げる
        if note_images:
            url, w, h = note_images[promo_idx % len(note_images)]
            blocks.append(_image_figure(url, w, h, link=article.affiliate_click_url))
        uid = str(uuid.uuid4())
        inner = (f'👉 <a href="{_html.escape(article.affiliate_click_url)}">'
                 f"{_html.escape(label)}を見てみる</a>")
        blocks.append(f'<p name="{uid}" id="{uid}">{inner}</p>')
        text_len += len(label) + 8
        promo_idx += 1

    h2_count = 0
    for line in body_md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            add("h3", s[4:])
        elif s.startswith("## ") or s.startswith("# "):
            h2_count += 1
            if h2_count in _LINK_BEFORE_H2:
                add_promo()  # 直前のセクション末尾に「画像＋リンク」
            add("h2", s[3:] if s.startswith("## ") else s[2:])
        elif s.startswith(("- ", "* ", "・")):
            add("p", "・" + s.lstrip("-*・ ").strip())
        else:
            add("p", s)

    add_promo()           # まとめ後（末尾）の「画像＋リンク」
    add("p", DISCLOSURE)  # PR表記は末尾（参考記事に合わせる）

    return "".join(blocks), text_len
