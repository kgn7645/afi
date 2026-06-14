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


def test_batch_dedup_key():
    from core import batch
    assert batch.dedup_key(brand="COMFEE'", model_number="CFS-12") == "comfee'|cfs-12"
    assert batch.dedup_key(brand="RANVOO", category="ネッククーラー") == "ranvoo|ネッククーラー"
    assert batch.dedup_key(product_name="謎の商品") == "謎の商品"


def test_batch_run_skips_duplicates(monkeypatch, tmp_path):
    """run_batch が重複スキップとlimitを守ることを、pipelineをスタブして検証。"""
    from core import batch
    from core.models import Article, PipelineResult, Product

    calls = []

    def fake_run(*, url, manual, affiliate_link_html, post_to_wp, wp_status, gemini):
        calls.append(manual.get("brand"))
        p = Product(brand=manual.get("brand", ""), category=manual.get("category", ""))
        return PipelineResult(product=p, article=Article(title=f"記事 {p.brand}"), selection_ok=True, wp_post_id=1)

    monkeypatch.setattr(batch.pipeline, "run", fake_run)

    q = tmp_path / "q.csv"
    q.write_text(
        "brand,category,model_number,product_name,price,company_hint,url,affiliate_link_html\n"
        "A,扇風機,,,,,,\n"
        "A,扇風機,,,,,,\n"   # 重複
        "B,除湿機,,,,,,\n",
        encoding="utf-8",
    )
    s = batch.run_batch(queue_path=str(q), limit=5, post_to_wp=False, skip_dedup=False)
    assert s["generated"] == 2          # A, B
    assert s["skipped_dup"] == 1        # 2行目のA
    assert calls == ["A", "B"]

    # limitの尊重
    s2 = batch.run_batch(queue_path=str(q), limit=1, post_to_wp=False, skip_dedup=True)
    assert s2["generated"] == 1


def test_indexnow_key_and_host():
    from core import indexnow
    key = indexnow.generate_key()
    assert len(key) == 32 and all(c in "0123456789abcdef" for c in key)
    assert indexnow.key_file_content(key) == key
    assert indexnow.host_of("https://ouchibase.com/foo/") == "ouchibase.com"
    assert indexnow.default_key_location("ouchibase.com", "abc") == "https://ouchibase.com/abc.txt"


def test_indexnow_submit_payload(monkeypatch):
    from core import indexnow

    captured = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    def fake_post(url, json, headers, timeout):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(indexnow.requests, "post", fake_post)
    res = indexnow.submit(
        ["https://ouchibase.com/a/", "https://ouchibase.com/b/"],
        key="deadbeef", key_location="https://ouchibase.com/deadbeef.txt",
    )
    assert res["ok"] and res["count"] == 2 and res["host"] == "ouchibase.com"
    assert captured["json"]["host"] == "ouchibase.com"
    assert captured["json"]["key"] == "deadbeef"
    assert captured["json"]["urlList"] == ["https://ouchibase.com/a/", "https://ouchibase.com/b/"]


def test_indexnow_submit_noop():
    from core import indexnow
    assert indexnow.submit([], key="x")["status"] == "noop"


def test_sheet_queue_is_url():
    from core import sheet_queue
    assert sheet_queue.is_url("https://docs.google.com/x") is True
    assert sheet_queue.is_url("data/queue.csv") is False


def test_sheet_queue_fetch_rows(monkeypatch):
    from core import sheet_queue

    class FakeResp:
        content = "brand,category\nKLOUDIC,除湿機\nRANVOO,ネッククーラー\n".encode("utf-8")
        def raise_for_status(self):
            pass

    monkeypatch.setattr(sheet_queue.requests, "get", lambda url, timeout: FakeResp())
    rows = sheet_queue.fetch_rows("https://docs.google.com/x/pub?output=csv")
    assert len(rows) == 2
    assert rows[0]["brand"] == "KLOUDIC" and rows[1]["category"] == "ネッククーラー"


def test_note_export_markdown():
    from core import note_export
    from core.models import Article, Product
    art = Article(
        title="【X】はどこの国？", affiliate_click_url="https://af.moshimo.com/af/c/click?a_id=1&url=u",
        raw_sections={"body_md": "## はじめに\n本文です。"},
    )
    prod = Product(brand="X", category="扇風機")
    md = note_export.build_note_markdown(art, prod)
    assert note_export.DISCLOSURE in md          # 広告表記
    assert "# 【X】はどこの国？" in md            # タイトル見出し
    assert "## はじめに" in md                    # 本文
    assert "af.moshimo.com" in md                 # プレーン成果リンク
    assert "<script" not in md and "msmaflink" not in md  # JSウィジェットは含めない


def test_note_export_html():
    from core import note_export
    from core.models import Article, Product
    art = Article(
        title="T", affiliate_click_url="https://af.moshimo.com/af/c/click?a_id=1&url=u",
        raw_sections={"body_md": "## 見出し\n本文**強調**です。\n- 項目"},
    )
    html, length = note_export.build_note_html(art, Product(brand="X", category="扇風機"))
    assert '<h2 name=' in html and 'id=' in html          # 見出しにUUID付与
    assert "<strong>強調</strong>" in html                # 太字
    assert 'href="https://af.moshimo.com' in html         # 成果リンク
    assert "msmaflink" not in html and "<script" not in html
    assert length > 0


def test_batch_load_queue_dispatches(monkeypatch, tmp_path):
    from core import batch
    # URL → sheet_queue.fetch_rows
    monkeypatch.setattr(batch.sheet_queue, "fetch_rows", lambda url: [{"brand": "X"}])
    assert batch.load_queue("https://docs.google.com/x")[0]["brand"] == "X"
    # パス → ローカルCSV
    q = tmp_path / "q.csv"
    q.write_text("brand,category\nY,扇風機\n", encoding="utf-8")
    assert batch.load_queue(str(q))[0]["brand"] == "Y"
