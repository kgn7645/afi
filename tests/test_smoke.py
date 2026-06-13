"""APIキー無しでも動く部分のスモークテスト。 python -m pytest tests/ で実行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import affiliate, product_selector  # noqa: E402
from core.content_generator import markdown_to_html, _stars_md  # noqa: E402
from core.models import Product  # noqa: E402
from core.product_extractor import extract_asin  # noqa: E402
from core.prompts import parse_json_response  # noqa: E402


def test_extract_asin():
    url = "https://www.amazon.co.jp/dp/B0GR3SSCSF/ref=foo"
    assert extract_asin(url) == "B0GR3SSCSF"


def test_selection_rejects_cheap():
    p = Product(brand="X", category="扇風機", product_name="ミニ扇風機", price=500)
    ok, reason = product_selector.evaluate(p)
    assert ok is False
    assert "最低" in reason


def test_selection_rejects_excluded_category():
    p = Product(brand="X", category="化粧品", product_name="美容液", price=5000)
    ok, _ = product_selector.evaluate(p)
    assert ok is False


def test_selection_ok():
    p = Product(brand="COMFEE'", category="扇風機", product_name="DCモーター扇風機", price=8000)
    ok, _ = product_selector.evaluate(p)
    assert ok is True


def test_markdown_to_html():
    html = markdown_to_html("## 見出し\n本文です。\n- 項目1\n- 項目2")
    assert "<h2>見出し</h2>" in html
    assert "<li>項目1</li>" in html
    assert "<p>本文です。</p>" in html


def test_stars():
    assert _stars_md(4.5) == "★★★★☆"
    assert _stars_md(5.0) == "★★★★★"
    assert _stars_md(3.0) == "★★★☆☆"


def test_parse_json_with_fence():
    data = parse_json_response('```json\n{"a": 1, "b": "x"}\n```')
    assert data["a"] == 1 and data["b"] == "x"


def test_affiliate_insert():
    body = "<h2>おすすめ商品</h2><h3>商品スペック</h3><p>x</p><h3>良い口コミ</h3>"
    out = affiliate.insert_into_body(body, "<a>link</a>")
    assert "affiliate-link" in out
    assert "<a>link</a>" in out


def test_moshimo_click_url():
    from core import moshimo_link as ml
    url = ml.build_click_url(5633316, "https://item.rakuten.co.jp/e-kurashi/s1k76/", "rakuten")
    assert url.startswith("https://af.moshimo.com/af/c/click?")
    assert "a_id=5633316" in url
    assert "p_id=54&pc_id=54&pl_id=27059" in url
    assert "url=https%3A%2F%2Fitem.rakuten.co.jp%2Fe-kurashi%2Fs1k76%2F" in url


def test_moshimo_easylink_roundtrip():
    """実リンクの商品データから再生成し、payloadが一致することを確認。"""
    import json
    import re

    from core import moshimo_link as ml

    real = (Path(__file__).resolve().parent.parent / "data" / "link.txt")
    if not real.exists():
        return  # 実リンク未配置の環境ではスキップ
    payload = json.loads(re.search(r"msmaflink\((\{.*\})\);", real.read_text(encoding="utf-8"), re.S).group(1).replace("\\/", "/"))
    html = ml.build_easylink_html(
        a_id=payload["b_l"][0]["a_id"], name=payload["n"], product_url=payload["u"]["u"],
        image_domain=payload["d"], image_path_prefix=payload["c_p"], image_paths=payload["p"],
        program="rakuten", eid=payload["eid"],
    )
    gen = json.loads(re.search(r"msmaflink\((\{.*\})\);", html, re.S).group(1).replace("\\/", "/"))
    assert gen == payload


def test_moshimo_easylink_html_shape():
    from core import moshimo_link as ml
    html = ml.build_easylink_html(a_id=123, name="テスト", product_url="https://item.rakuten.co.jp/s/x/", program="rakuten")
    ok, issues = affiliate.validate_moshimo_link(html)
    assert ok, issues
