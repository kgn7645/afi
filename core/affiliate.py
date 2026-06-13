"""
D作業: もしもアフィリエイトのリンク埋め込み。
もしもには公開APIが無いため、
- 手動で取得した「もしもかんたんリンク」HTMLがあればそれを使う
- 無ければプレースホルダを挿入（後で人が差し替え）
記事本文の「商品スペック」直後にCTAとして差し込む。
"""
from __future__ import annotations

import re

from .config import get_settings


def validate_moshimo_link(html: str) -> tuple[bool, list[str]]:
    """もしもかんたんリンクHTMLの構造を簡易検証。 (ok, 問題リスト) を返す。

    完全なJSONパースはしない（JS内のため）が、コピー欠落で頻発する
    破損パターンを検出して投稿前に気づけるようにする。
    """
    issues: list[str] = []
    h = html.strip()
    if not h:
        return False, ["空です。"]

    if "MoshimoAffiliateEasyLink" not in h:
        issues.append("もしものコメントタグ（MoshimoAffiliateEasyLink）が見当たりません。")
    if "msmaflink(" not in h:
        issues.append("msmaflink(...) 本体が見当たりません。")

    # 波括弧・角括弧の対応をチェック
    if h.count("{") != h.count("}"):
        issues.append(f"波括弧 {{}} の数が不一致（{h.count('{')} 対 {h.count('}')}）。コピー欠落の疑い。")
    if h.count("[") != h.count("]"):
        issues.append(f"角括弧 [] の数が不一致（{h.count('[')} 対 {h.count(']')}）。コピー欠落の疑い。")

    # リンク情報ブロックの存在（もしも v2.1 形式: "u":{...} ＋ "b_l":[{...}]）
    if '"u":{' not in h and '"u": {' not in h and '"u":[{' not in h:
        issues.append('リンク情報 "u" ブロックが見つかりません。')

    # 商品URLの存在確認（エスケープされたスラッシュ \/ も考慮し、ドメインで判定）
    if not re.search(r'(item\.rakuten\.co\.jp|amazon\.co\.jp|shopping\.yahoo)', h):
        issues.append("楽天/Amazon/Yahooの商品URLが見当たりません。")

    # 成果報酬の追跡ID（a_id等）が無いと収益が計上されない恐れ
    if not re.search(r'"(a_id|rakuten_id|amazon_id)":\s*\d', h):
        issues.append("成果報酬の追跡ID（a_id等）が見当たりません。報酬が計上されない恐れ。")

    # よくある破損: 値が途中で切れて "r_v3316 のように引用符が壊れている
    if re.search(r'"r_v[0-9]', h) or re.search(r'"[a-z_]+\d+,', h):
        issues.append("値の途中欠落（引用符の閉じ忘れ）の疑いがあります。")

    return (len(issues) == 0), issues



def build_link_block(custom_link_html: str = "") -> str:
    settings = get_settings()
    inner = custom_link_html.strip() or settings.moshimo_placeholder
    return (
        '\n<div class="affiliate-link" style="text-align:center;margin:24px 0;">\n'
        f"{inner}\n"
        "</div>\n"
    )


def insert_into_body(body_html: str, link_html: str) -> str:
    """商品スペック見出しの後ろにリンクを挿入。なければ末尾に追加。"""
    block = build_link_block(link_html)
    anchor_candidates = ["<h3>商品スペック", "<h2>おすすめ商品", "<h3>良い口コミ"]
    for anchor in anchor_candidates:
        idx = body_html.find(anchor)
        if idx != -1:
            # 次の見出し直前まで進めて挿入
            nxt = body_html.find("<h", idx + len(anchor))
            insert_at = nxt if nxt != -1 else len(body_html)
            return body_html[:insert_at] + block + body_html[insert_at:]
    return body_html + block
