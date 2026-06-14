"""
Issue #44: E-E-A-T基盤。
- 記事末尾の著者情報ボックス（運営者プロフィールへの導線）
- 運営者情報 / お問い合わせ などの固定ページのテンプレと一括作成
"""
from __future__ import annotations

import html

from . import wordpress
from .config import get_rules, get_settings


def _eeat() -> dict:
    return get_rules().get("eeat", {})


def author_box_html() -> str:
    """記事末尾に差し込む著者情報ボックス（E-E-A-T）。"""
    e = _eeat()
    s = get_settings()
    name = html.escape(e.get("author_name", "編集部"))
    bio = html.escape(e.get("author_bio", ""))
    profile_url = f"{s.wp_base_url}/{e.get('profile_slug', 'about')}/"
    return (
        '\n<div class="author-box" style="border:1px solid #e3e3e3;border-radius:12px;'
        'padding:16px 20px;margin:32px 0;background:#fafafa;">\n'
        f'  <p style="margin:0 0 6px;font-weight:bold;">この記事を書いた人：{name}</p>\n'
        f'  <p style="margin:0 0 10px;font-size:0.95em;line-height:1.7;">{bio}</p>\n'
        f'  <p style="margin:0;font-size:0.9em;">'
        f'<a href="{html.escape(profile_url, quote=True)}">運営者情報・運営方針はこちら</a></p>\n'
        "</div>\n"
    )


def append_author_box(body_html: str) -> str:
    """本文末尾に著者ボックスを付与。"""
    return body_html + author_box_html()


# --- 固定ページのテンプレ（下書きで作成。運営者が実情報を埋めて公開する想定） ---

def _about_html() -> str:
    e = _eeat()
    name = html.escape(e.get("author_name", "編集部"))
    site = html.escape(e.get("site_name", "当サイト"))
    concept = html.escape(e.get("site_concept", ""))
    return (
        f"<h2>{site}について</h2>\n"
        f"<p>{site}は、{concept}です。</p>\n"
        "<h2>運営方針・レビュー基準</h2>\n"
        "<ul>\n"
        "<li>メーカーの素性（どこの国の企業か等）や保証・サポート体制まで調べたうえで紹介します。</li>\n"
        "<li>良い点だけでなく、気になる点・デメリットも正直に記載します。</li>\n"
        "<li>価格や仕様は変動するため、購入前に各販売ページで最新情報をご確認ください。</li>\n"
        "</ul>\n"
        "<h2>運営者</h2>\n"
        f"<p>{name}（※プロフィール詳細をここに記入してください）</p>\n"
        "<h2>お問い合わせ</h2>\n"
        "<p>ご連絡はお問い合わせページよりお願いいたします。</p>\n"
    )


def _contact_html() -> str:
    return (
        "<p>当サイトへのお問い合わせは、以下の方法でお願いいたします。</p>\n"
        "<p>メール：<strong>（連絡用メールアドレスをここに記入）</strong></p>\n"
        "<p>※ お問い合わせフォームを設置する場合は Contact Form 7 等のプラグインを利用してください。</p>\n"
    )


def _affiliate_disclosure_html() -> str:
    """ステマ規制対応のアフィリエイト表記（プライバシーポリシーに追記する想定）。"""
    return (
        "<h2>アフィリエイトプログラムについて</h2>\n"
        "<p>当サイトは、Amazon.co.jpを宣伝しリンクすることによって"
        "サイトが紹介料を獲得できる手段を提供することを目的に設定された"
        "アフィリエイトプログラム「Amazonアソシエイト・プログラム」の参加者です。"
        "また、その他の事業者のアフィリエイトプログラムにも参加する場合があります。</p>\n"
        "<p>記事内の商品リンクには広告（アフィリエイトリンク）が含まれます。</p>\n"
    )


# 作成する固定ページ定義（slug, title, html, ステマ表記を足すか）
_PAGES = [
    ("about", "運営者情報", _about_html, False),
    ("contact", "お問い合わせ", _contact_html, False),
]


def bootstrap_pages(*, status: str = "draft") -> list[dict]:
    """運営者情報・お問い合わせを作成し、プライバシーポリシーにアフィリ表記を追記。

    既存スラッグはスキップ（重複作成しない）。結果リストを返す。
    """
    results: list[dict] = []
    for slug, title, builder, _ in _PAGES:
        existing = wordpress.get_page_by_slug(slug)
        if existing:
            results.append({"slug": slug, "action": "skip(既存)", "id": existing["id"],
                            "status": existing["status"]})
            continue
        page = wordpress.create_page(title, builder(), slug=slug, status=status)
        results.append({"slug": slug, "action": "created", "id": page["id"],
                        "status": page["status"]})

    # プライバシーポリシー: 既存ページにアフィリ表記が無ければ追記
    pp = wordpress.get_page_by_slug("privacy-policy")
    if pp:
        results.append({"slug": "privacy-policy", "action": "exists(アフィリ表記を手動追記推奨)",
                        "id": pp["id"], "status": pp["status"]})
    else:
        page = wordpress.create_page("プライバシーポリシー", _affiliate_disclosure_html(),
                                     slug="privacy-policy", status=status)
        results.append({"slug": "privacy-policy", "action": "created", "id": page["id"],
                        "status": page["status"]})
    return results
