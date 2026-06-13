"""
もしもかんたんリンクの自動生成（Issue #8）。

調査結果:
- 成果トラッキングは bundle.js が以下のクリックURLを生成して行う:
    https://af.moshimo.com/af/c/click?a_id=<A_ID>&p_id=<P_ID>&pc_id=<PC_ID>&pl_id=<PL_ID>&url=<商品URL>
- 成果計上はアカウント固有の `a_id` で行われる。
- p_id/pc_id/pl_id は「プログラム共通の定数」（bundle.js にハードコードされた既定値）。

したがって「自分の a_id ＋ 商品URL」だけで、もしも管理画面を使わずに
かんたんリンクHTML（および素のクリック追跡URL）を生成できる。

⚠️ 注意（コンプライアンス）:
リンクHTMLを自前生成する運用が、もしもアフィリエイトの利用規約上問題ないかは
利用者自身で確認すること。本実装は「利用者自身の a_id」と「公開された
プログラム定数」のみを用い、もしもが生成するのと同一の追跡URLを再現する。
"""
from __future__ import annotations

import json
import random
import string
from urllib.parse import quote

# bundle.js (https://dn.msmstatic.com/site/cardlink/bundle.js) から抽出したプログラム定数
PROGRAM_DEFAULTS: dict[str, dict] = {
    "rakuten": {"p_id": 54, "pc_id": 54, "pl_id": 27059, "button_text": "楽天市場で見る", "button_color": "#f76956"},
    "amazon": {"p_id": 170, "pc_id": 185, "pl_id": 27060, "button_text": "Amazonで見る", "button_color": "#ff9900"},
    "yahoo": {"p_id": 1225, "pc_id": 1925, "pl_id": 27061, "button_text": "Yahoo!で見る", "button_color": "#ff0033"},
}

MOSHIMO_DOMAIN = "moshimo.com"
_BUNDLE = "//dn.msmstatic.com/site/cardlink/bundle.js?20220329"


def build_click_url(a_id: int, product_url: str, program: str = "rakuten") -> str:
    """もしも経由の成果追跡URL（クリック先）を組み立てる。

    カードを使わず、素のテキスト/ボタンリンクにも使える。
    """
    prog = PROGRAM_DEFAULTS[program]
    return (
        f"https://af.{MOSHIMO_DOMAIN}/af/c/click"
        f"?a_id={a_id}&p_id={prog['p_id']}&pc_id={prog['pc_id']}"
        f"&pl_id={prog['pl_id']}&url={quote(product_url, safe='')}"
    )


def _gen_eid(n: int = 5) -> str:
    """カードのDOM要素ID（もしもは5桁の英数字を使用）。"""
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _to_moshimo_json(obj: dict) -> str:
    """もしもの出力に合わせ、ASCII非エスケープ＋スラッシュを \\/ にして直列化。"""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("/", "\\/")


def build_easylink_html(
    *,
    a_id: int,
    name: str,
    product_url: str,
    image_domain: str = "",
    image_path_prefix: str = "",
    image_paths: list[str] | None = None,
    program: str = "rakuten",
    eid: str | None = None,
) -> str:
    """もしもかんたんリンクのHTML（カード）を生成する。

    name/product_url/画像は商品ごとの可変部、a_id はアカウント固有。
    画像情報が無くてもリンク自体は機能する（カードの見栄えが簡素になるだけ）。
    """
    prog = PROGRAM_DEFAULTS[program]
    eid = eid or _gen_eid()

    payload = {
        "n": name,
        "b": "",
        "t": "",
        "d": image_domain,
        "c_p": image_path_prefix,
        "p": image_paths or [],
        "u": {"u": product_url, "t": program, "r_v": ""},
        "v": "2.1",
        "b_l": [
            {
                "id": 1,
                "u_tx": prog["button_text"],
                "u_bc": prog["button_color"],
                "u_url": product_url,
                "a_id": a_id,
                "p_id": prog["p_id"],
                "pl_id": prog["pl_id"],
                "pc_id": prog["pc_id"],
                "s_n": program,
                "u_so": 1,
            }
        ],
        "eid": eid,
        "s": "xs",
    }

    loader = (
        '(function(b,c,f,g,a,d,e){b.MoshimoAffiliateObject=a;'
        "b[a]=b[a]||function(){arguments.currentScript=c.currentScript"
        "||c.scripts[c.scripts.length-2];(b[a].q=b[a].q||[]).push(arguments)};"
        "c.getElementById(a)||(d=c.createElement(f),d.src=g,"
        'd.id=a,e=c.getElementsByTagName("body")[0],e.appendChild(d))})'
        f'(window,document,"script","{_BUNDLE}","msmaflink");'
    )

    return (
        "<!-- START MoshimoAffiliateEasyLink -->"
        '<script type="text/javascript">'
        f"{loader}msmaflink({_to_moshimo_json(payload)});"
        "</script>"
        f'<div id="msmaflink-{eid}">リンク</div>'
        "<!-- MoshimoAffiliateEasyLink END -->"
    )


def build_rakuten_link_by_keyword(keyword: str, a_id: int | None = None) -> dict | None:
    """キーワード→楽天商品検索→もしもかんたんリンクHTMLまでを一括生成。

    返り値: {html, click_url, product} / 見つからなければ None。
    a_id 未指定時は .env の MOSHIMO_AID を使う。
    """
    from . import rakuten
    from .config import get_settings

    a_id = a_id or get_settings().moshimo_aid
    if not a_id:
        raise RuntimeError("MOSHIMO_AID が未設定です（.env）。")

    item = rakuten.search_item(keyword)
    if not item:
        return None

    html = build_easylink_html(
        a_id=a_id,
        name=item["name"],
        product_url=item["url"],
        image_domain=item["image_domain"],
        image_paths=item["image_paths"],
        program="rakuten",
    )
    return {
        "html": html,
        "click_url": build_click_url(a_id, item["url"], "rakuten"),
        "product": item,
    }
