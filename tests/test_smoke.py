"""APIキー無しでも動く部分のスモークテスト。 python -m pytest tests/ で実行。"""
import os
import sys
from pathlib import Path

# テストでは設定の外部オーバーライド（WP通信）を無効化
os.environ["CONFIG_OVERRIDES"] = "0"

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


def test_amazon_button_has_tag_and_attrs():
    url = "https://www.amazon.co.jp/dp/B0GS1PQ22W?tag=chance274-22"
    btn = affiliate.build_amazon_button(url, "Amazonで見る")
    assert "tag=chance274-22" in btn
    assert "Amazonで見る" in btn
    assert 'rel="nofollow noopener sponsored"' in btn   # アフィリのrel必須


def test_insert_amazon_buttons_three_spots():
    body = ("<h2>はじめに</h2><p>x</p>"
            "<h2>おすすめ商品「X」徹底レビュー</h2><p>x</p>"
            "<h2>他メーカーと比較</h2><p>x</p>"
            "<h2>まとめ</h2><p>x</p>")
    url = "https://www.amazon.co.jp/dp/B0GS1PQ22W?tag=chance274-22"
    out = affiliate.insert_amazon_buttons(body, url)
    assert out.count("amazon-cta-btn") == 3              # レビュー前・比較前・末尾
    assert out.count("tag=chance274-22") == 3
    # レビュー見出しの前に最初のボタンが入る（導入の後）
    assert out.index("amazon-cta-btn") < out.index("おすすめ商品")


def test_insert_amazon_buttons_fallback_when_no_anchor():
    out = affiliate.insert_amazon_buttons("<p>本文だけ</p>", "https://amzn/dp/X?tag=t")
    assert out.count("amazon-cta-btn") == 1              # アンカー無しでも末尾に1つ


def test_amazon_url_alive(monkeypatch):
    from core import product_extractor as pe

    class Resp:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

    # 404 = 死んだASIN → False
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(404))
    assert pe.amazon_url_alive("https://www.amazon.co.jp/dp/DEAD0ASIN0") is False
    # 200だが「何かお探し」ページ = 無効ASIN → False
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(200, "何かお探しですか？"))
    assert pe.amazon_url_alive("https://www.amazon.co.jp/dp/GHOST00000") is False
    # 200で実商品 → True
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(200, "<title>RANVOO...</title>"))
    assert pe.amazon_url_alive("https://www.amazon.co.jp/dp/B0D7C999LG") is True
    # 503(bot対策) は誤判定回避のため True 扱い
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(503))
    assert pe.amazon_url_alive("https://www.amazon.co.jp/dp/B0D7C999LG") is True


def test_build_amazon_card():
    card = affiliate.build_amazon_card(
        "https://www.amazon.co.jp/dp/B0D7C999LG?tag=chance274-22",
        "RANVOO AICE3 ネッククーラー",
        "https://m.media-amazon.com/images/I/61ILah3+YKL._AC_SL1500_.jpg",
    )
    assert "amazon-card" in card
    assert "<img" in card and "61ILah3" in card               # 商品画像
    assert "RANVOO AICE3 ネッククーラー" in card               # 商品名
    assert "tag=chance274-22" in card
    assert 'rel="nofollow noopener sponsored"' in card


def test_insert_amazon_cards_three_spots():
    body = ("<h2>はじめに</h2><p>x</p>"
            "<h2>おすすめ商品「X」</h2><p>x</p>"
            "<h2>他メーカーと比較</h2><p>x</p>"
            "<h2>まとめ</h2><p>x</p>")
    out = affiliate.insert_amazon_cards(body, '<div class="amazon-card">CARD</div>')
    assert out.count("amazon-card") == 3


def test_fetch_amazon_product_card(monkeypatch):
    from core import product_extractor as pe

    class Resp:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

    live = ('<html><span id="productTitle"> RANVOO AICE3 </span>'
            '..."hiRes":"https://m.media-amazon.com/images/I/61ILah3+YKL._AC_SL1500_.jpg"...')
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(200, live))
    card = pe.fetch_amazon_product_card("https://www.amazon.co.jp/dp/B0D7C999LG")
    assert card == {"title": "RANVOO AICE3",
                    "image": "https://m.media-amazon.com/images/I/61ILah3+YKL._AC_SL1500_.jpg"}

    # 無効ASINページ → None
    monkeypatch.setattr(pe.requests, "get", lambda *a, **k: Resp(200, "何かお探しですか？"))
    assert pe.fetch_amazon_product_card("https://www.amazon.co.jp/dp/GHOST00000") is None
    # 画像が取れない → None
    monkeypatch.setattr(pe.requests, "get",
                        lambda *a, **k: Resp(200, '<span id="productTitle">X</span>'))
    assert pe.fetch_amazon_product_card("https://www.amazon.co.jp/dp/X") is None


def test_author_box_and_category_prompt():
    from core import site_setup, prompts
    from core.models import Product

    box = site_setup.author_box_html()
    assert "この記事を書いた人" in box
    assert "運営者情報" in box                       # プロフィールへの導線

    cats = [{"id": 3, "name": "デスク・作業環境", "slug": "desk"},
            {"id": 7, "name": "健康・運動不足", "slug": "health"}]
    p = prompts.category_pick_prompt(Product(brand="RANVOO", category="ネッククーラー"),
                                     cats, "在宅ワークメディア")
    assert "desk" in p and "health" in p
    assert "slug" in p


def test_bootstrap_pages(monkeypatch):
    from core import site_setup, wordpress as wp

    # privacy-policyは既存、about/contactは無い想定
    existing = {"privacy-policy": {"id": 10, "status": "draft"}}
    monkeypatch.setattr(wp, "get_page_by_slug", lambda slug, **k: existing.get(slug))
    created = []

    def fake_create(title, content, *, slug="", status="draft", **k):
        created.append(slug)
        return {"id": 100 + len(created), "link": "", "status": status}

    monkeypatch.setattr(wp, "create_page", fake_create)
    res = site_setup.bootstrap_pages()
    actions = {r["slug"]: r["action"] for r in res}
    assert "created" in actions["about"] and "created" in actions["contact"]
    assert "exists" in actions["privacy-policy"]      # 既存は再作成しない
    assert created == ["about", "contact"]


def test_pick_category_ids(monkeypatch):
    from core import pipeline
    from core.models import Product, PipelineResult

    cats = [{"id": 1, "name": "未分類", "slug": "uncategorized"},
            {"id": 3, "name": "デスク・作業環境", "slug": "desk"},
            {"id": 4, "name": "作業ツール・効率化", "slug": "tools"}]
    monkeypatch.setattr(pipeline.wordpress, "list_categories", lambda **k: cats)

    class FakeGemini:
        def __init__(self, out):
            self.out = out

        def generate(self, *a, **k):
            return self.out

    res = PipelineResult(product=Product())
    # Geminiが有効slugを返す → そのID
    ids = pipeline._pick_category_ids(Product(brand="X"), res, FakeGemini("desk"))
    assert ids == [3]
    # 不正な返答 → default_category_slug(tools)にフォールバック
    ids = pipeline._pick_category_ids(Product(brand="X"), res, FakeGemini("???"))
    assert ids == [4]


def test_qa_clean_article():
    from core import qa
    from core.models import Article
    body = ("<h2>はじめに</h2><p>" + "あ" * 6000 + "</p>"
            "<h2>RANVOOとは</h2><p>x</p><h2>徹底レビュー</h2>"
            '<a class="amazon-cta-btn" href="#">Amazonで見る</a>'
            "<h2>他メーカーと比較</h2><h2>まとめ</h2>")
    art = Article(title="T", body_html=body)
    issues = qa.check_article(art, Product(), rules={})
    assert not qa.has_errors(issues)                    # 重大問題なし


def test_qa_detects_problems():
    from core import qa
    from core.models import Article
    # 薬機法NG・誇大・アフィリ無し・タイトル無し・薄い
    art = Article(title="", body_html="<p>この除湿機を使えば肩こりが治る。日本一の効果。**強調**</p>")
    issues = qa.check_article(art, Product(), rules={})
    codes = {i["code"] for i in issues}
    assert "pharma" in codes                            # 「治る」
    assert "exaggeration" in codes                      # 「日本一」
    assert "no_affiliate" in codes                      # リンク無し
    assert "no_title" in codes                          # タイトル無し
    assert "markdown_leftover" in codes                 # ** 残り
    assert qa.has_errors(issues)                        # errorあり（no_affiliate/no_title）
    msgs = qa.format_issues(issues)
    assert any("QA" in m for m in msgs)


def test_company_grounding_prompt():
    from core import prompts
    p = prompts.company_grounding_prompt("RANVOO", "ネッククーラー")
    assert "RANVOO" in p
    assert "確認できた事実のみ" in p                 # 推測禁止
    assert "公開情報では" in p                       # 不明時の明記指示


def test_ground_company(monkeypatch):
    from core import pipeline
    from core.models import Product, PipelineResult

    class FakeGemini:
        def __init__(self, out=None, exc=None):
            self.out = out
            self.exc = exc

        def generate_grounded(self, *a, **k):
            if self.exc:
                raise self.exc
            return self.out

    res = PipelineResult(product=Product())
    # 取得成功 → company_hintに反映＋warning
    hint = pipeline._ground_company(
        Product(brand="RANVOO", category="ネッククーラー"),
        FakeGemini(out="・中国の企業\n・2018年設立"), res)
    assert "中国" in hint
    assert any("グラウンディング" in w for w in res.warnings)
    # 失敗 → 空文字で継続
    res2 = PipelineResult(product=Product())
    hint2 = pipeline._ground_company(
        Product(brand="X"), FakeGemini(exc=RuntimeError("429")), res2)
    assert hint2 == ""


def test_article_body_prompt_depth():
    from core import prompts
    from core.models import Product

    rules = {"article": {"min_chars": 6000, "reviews_each": 5,
                         "competitor_brands": ["パナソニック", "アイリスオーヤマ"]}}
    p = prompts.article_body_prompt(
        Product(brand="RANVOO", category="ネッククーラー"), rules, "（評価ブロック）")
    assert "6000字以上" in p                       # ボリューム指定が反映
    assert "各5件" in p                            # 口コミ件数
    assert "ペルソナ" in p and "利用シミュレーション" in p   # 厚みセクション
    assert "比喩" in p                             # 導入の引き込み


def test_eyecatch_wrap_jp():
    # 折り返しはPillow非依存の純ロジックなので常に検証
    from core import eyecatch

    class FakeFont:
        pass

    class FakeDraw:
        def textlength(self, s, font=None):
            return len(s) * 10  # 1文字=10px

    lines = eyecatch._wrap_jp("あいうえおかきくけこ", FakeFont(), FakeDraw(), 50)
    assert all(len(l) <= 5 for l in lines)            # 50px/10px=5文字で折り返し
    assert "".join(lines) == "あいうえおかきくけこ"


def test_eyecatch_build():
    import io
    from core import eyecatch
    if not eyecatch.available():
        import pytest
        pytest.skip("Pillow/日本語フォントが無い環境")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), "#cccccc").save(buf, format="PNG")
    png = eyecatch.build_eyecatch("夏の暑さを快適に！テスト用キャッチコピー", buf.getvalue(),
                                  brand="TestBrand", site_name="おうちベース")
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"   # PNGシグネチャ
    im = Image.open(io.BytesIO(png))
    assert im.size == (1200, 630)                      # OGPサイズ


def test_note_publish(monkeypatch):
    from core import note_publish
    from core.models import Article, Product

    s = note_publish.get_settings()
    # NOTE_SESSION未設定 → None（no-op、WPは止めない）
    monkeypatch.setattr(s, "note_session", "", raising=False)
    assert note_publish.create_note_draft(Article(title="T"), Product()) is None

    # 設定あり＋Amazonソース → カードモードで下書き作成
    monkeypatch.setattr(s, "note_session", "cookie", raising=False)
    monkeypatch.setattr(s, "amazon_associate_tag", "chance274-22", raising=False)
    monkeypatch.setattr(note_publish.note_client, "create_empty_note",
                        lambda: {"id": 50, "key": "K9"})
    monkeypatch.setattr(note_publish.note_client, "get_external_embed",
                        lambda key, url: {"key": "e" + url[-1], "html_for_embed": "<x>"})
    saved = {}
    monkeypatch.setattr(note_publish.note_client, "save_draft",
                        lambda nid, title, body, length: saved.update(id=nid, title=title))
    monkeypatch.setattr(note_publish.note_export, "build_note_html",
                        lambda art, prod, amazon_embeds=None: ("<body>", 1234))
    out = note_publish.create_note_draft(
        Article(title="RANVOOレビュー"), Product(),
        source_url="https://www.amazon.co.jp/dp/B0D7C999LG")
    assert out["id"] == 50 and "editor.note.com" in out["edit_url"]
    assert saved["title"] == "RANVOOレビュー"


def test_overrides_update(monkeypatch):
    from core import overrides
    # 既存に candidates があり、_crawl_request は維持しつつ candidates を差し替え
    monkeypatch.setattr(overrides, "load",
                        lambda force=False: {"_crawl_request": 111, "selection": {"min_price": 3000}})
    saved = {}
    monkeypatch.setattr(overrides, "save", lambda data: saved.update(d=data) or True)
    overrides.update({"candidates": {"keywords": ["A", "B"]}})
    assert saved["d"]["_crawl_request"] == 111                  # 既存を保持
    assert saved["d"]["candidates"]["keywords"] == ["A", "B"]   # 追加
    assert saved["d"]["selection"]["min_price"] == 3000         # 既存を保持


def test_config_deep_merge_and_prompt_override(monkeypatch):
    from core import config, prompts
    from core.models import Product

    base = {"selection": {"min_price": 3000, "require_in_stock": True},
            "prompts": {"style_guide": ""}}
    over = {"selection": {"min_price": 5000}, "prompts": {"style_guide": "独自ガイド"}}
    merged = config._deep_merge(base, over)
    assert merged["selection"]["min_price"] == 5000          # 上書き
    assert merged["selection"]["require_in_stock"] is True   # 既存は保持
    assert merged["prompts"]["style_guide"] == "独自ガイド"

    # プロンプトのstyle_guideがオーバーライドで差し替わる
    monkeypatch.setattr(config, "get_rules",
                        lambda: {"prompts": {"style_guide": "★カスタム文体★", "extra_instructions": "追加ルール"},
                                 "article": {"min_chars": 6000, "competitor_brands": ["X"]}})
    p = prompts.article_body_prompt(Product(brand="B", category="C"),
                                    config.get_rules(), "（評価）")
    assert "★カスタム文体★" in p          # 文体ガイド差し替え
    assert "追加ルール" in p               # 追加指示が反映


def test_internal_links(monkeypatch):
    from core import internal_links as il

    rel = [{"id": 2, "title": "記事B", "link": "http://x/b"},
           {"id": 3, "title": "記事<C>", "link": "http://x/c"}]
    block = il.build_block(rel)
    assert "あわせて読みたい" in block
    assert "http://x/b" in block and "記事&lt;C&gt;" in block      # XSSエスケープ
    assert il.build_block([]) == ""

    # upsert: 既存ブロックを置換（重複しない）
    body = "<p>本文</p>" + il.build_block([{"id": 9, "title": "旧", "link": "u"}])
    out = il.upsert_block(body, rel)
    assert out.count("related-links") == 2          # START/ENDマーカー1組
    assert "旧" not in out and "記事B" in out
    assert out.startswith("<p>本文</p>")

    # add_related: 同カテゴリ記事から関連リンク付与（自分は除外）
    monkeypatch.setattr(il.wordpress, "posts_in_category",
                        lambda cat_id, **k: [{"id": 1, "title": "自分", "link": "s"},
                                             {"id": 2, "title": "他1", "link": "o1"},
                                             {"id": 3, "title": "他2", "link": "o2"}])
    res = il.add_related("<p>x</p>", category_id=5, exclude_id=1)
    assert "他1" in res and "他2" in res and "自分" not in res


def test_run_candidates_batch(monkeypatch):
    from core import batch, candidates, pipeline
    from core.models import Article, PipelineResult, Product

    monkeypatch.setattr(candidates, "list_by_status",
                        lambda status, **k: [{"asin": "B01", "title": "RANVOO ネッククーラー",
                                              "url": "https://www.amazon.co.jp/dp/B01"}])
    marked = {}
    monkeypatch.setattr(candidates, "set_status",
                        lambda asin, status: marked.update({asin: status}))
    monkeypatch.setattr(batch, "GeminiClient", lambda: object())

    def fake_run(*, url, manual, post_to_wp, wp_status, gemini):
        art = Article(title="生成タイトル")
        return PipelineResult(product=Product(brand="RANVOO"), article=art,
                              selection_ok=True, wp_post_id=99)

    monkeypatch.setattr(pipeline, "run", fake_run)
    s = batch.run_candidates_batch(limit=5)
    assert s["generated"] == 1
    assert marked == {"B01": "generated"}        # 生成済みにマーク
    assert s["items"][0]["wp_post_id"] == 99


def test_amazon_rank(monkeypatch):
    from core import amazon_rank as ar

    class Resp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    # 検索: data-asin から抽出（重複除去・limit）
    search_html = '<div data-asin="B0AAAAAAA1"></div><div data-asin="B0AAAAAAA1"></div>' \
                  '<div data-asin="B0BBBBBBB2"></div>'
    monkeypatch.setattr(ar.requests, "get", lambda *a, **k: Resp(200, search_html))
    assert ar.search_asins("x", limit=5) == ["B0AAAAAAA1", "B0BBBBBBB2"]

    # ランキング: /dp/ から抽出
    rank_html = 'a href="/dp/B0CCCCCCC3" b /dp/B0CCCCCCC3 /dp/B0DDDDDDD4'
    monkeypatch.setattr(ar.requests, "get", lambda *a, **k: Resp(200, rank_html))
    assert ar.ranking_asins("kitchen/1", limit=5) == ["B0CCCCCCC3", "B0DDDDDDD4"]

    # build_candidate: タイトル/画像/価格を抽出
    prod = ('<span id="productTitle"> RANVOO AICE3 </span>'
            '..."hiRes":"https://m.media-amazon.com/images/I/x.jpg"...'
            '"priceAmount": 6980.0')
    monkeypatch.setattr(ar.requests, "get", lambda *a, **k: Resp(200, prod))
    c = ar.build_candidate("B0CCCCCCC3")
    assert c["title"] == "RANVOO AICE3" and c["price"] == 6980
    assert c["image"].endswith("x.jpg") and c["asin"] == "B0CCCCCCC3"
    # 無効ページ → None
    monkeypatch.setattr(ar.requests, "get", lambda *a, **k: Resp(200, "何かお探し"))
    assert ar.build_candidate("B0DEAD0000") is None


def test_candidates_client(monkeypatch):
    from core import candidates
    s = candidates.get_settings()
    monkeypatch.setattr(s, "candidates_webhook_url", "", raising=False)
    assert candidates.enabled() is False
    assert candidates.push([{"asin": "x"}]) is False
    assert candidates.list_by_status("pending") == []

    monkeypatch.setattr(s, "candidates_webhook_url", "https://cand.test/exec", raising=False)
    sent = {}
    monkeypatch.setattr(candidates.requests, "post",
                        lambda url, **k: sent.update(json=k.get("json")) or type("R", (), {})())
    assert candidates.enabled() is True
    candidates.push([{"asin": "B01"}])
    assert sent["json"]["action"] == "append" and sent["json"]["candidates"][0]["asin"] == "B01"
    candidates.set_status("B01", "approved")
    assert sent["json"] == {"action": "status", "asin": "B01", "status": "approved"}


def test_notify(monkeypatch):
    from core import notify
    s = notify.get_settings()
    # 未設定なら no-op
    monkeypatch.setattr(s, "notify_webhook_url", "", raising=False)
    assert notify.enabled() is False
    assert notify.send("x") is False

    # 設定ありならSlack/Discord両対応payloadでPOST
    monkeypatch.setattr(s, "notify_webhook_url", "https://hook.test/x", raising=False)
    sent = {}
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, **k: sent.update(url=url, json=k.get("json")) or type("R", (), {})())
    assert notify.send("やあ") is True
    assert sent["json"] == {"text": "やあ", "content": "やあ"}

    # バッチ要約の整形
    msg = notify.summarize_batch({
        "generated": 2, "skipped_dup": 1, "failed": 1,
        "items": [
            {"status": "ok", "warnings": ["w1"]},
            {"status": "error", "key": "BrandX", "error": "429"},
            {"status": "selection_ng", "key": "BrandY", "reason": "安すぎ"},
        ],
    })
    assert "生成 2" in msg and "失敗 1" in msg
    assert "❌ 失敗: BrandX - 429" in msg
    assert "⛔ 選定NG: BrandY" in msg
    assert "警告 計1件" in msg


def test_sheet_log(monkeypatch):
    from core import sheet_log
    s = sheet_log.get_settings()

    # 未設定なら no-op（POSTしない）
    monkeypatch.setattr(s, "sheet_log_webhook_url", "", raising=False)
    assert sheet_log.enabled() is False
    assert sheet_log.log_status(7, "publish") is False

    # 設定ありならPOSTされ、payloadが正しい
    monkeypatch.setattr(s, "sheet_log_webhook_url", "https://script.test/exec", raising=False)
    sent = {}

    def fake_post(url, **k):
        sent["url"] = url
        sent["json"] = k.get("json")

        class R:
            pass
        return R()

    monkeypatch.setattr(sheet_log.requests, "post", fake_post)
    assert sheet_log.enabled() is True
    assert sheet_log.log_generation(post_id=7, datetime_iso="2026-06-15T10:00:00",
                                    brand="X", category="Y", title="T",
                                    status="draft", url="http://x/edit")
    assert sent["url"] == "https://script.test/exec"
    assert sent["json"]["action"] == "upsert" and sent["json"]["post_id"] == 7
    sheet_log.log_status(7, "publish")
    assert sent["json"] == {"action": "status", "post_id": 7, "status": "publish"}


def test_review_token_and_password(monkeypatch):
    from core import review
    s = review.get_settings()
    monkeypatch.setattr(s, "review_password", "secret", raising=False)
    monkeypatch.setattr(s, "session_secret", "sess", raising=False)
    t = review.make_token(ttl=100, now=1000)
    assert review.valid_token(t, now=1050)              # 有効
    assert not review.valid_token(t, now=2000)          # 期限切れ
    assert not review.valid_token("1100.deadbeef", now=1050)  # 署名不一致
    assert not review.valid_token("", now=1050)
    assert review.enabled()
    assert review.check_password("secret")
    assert not review.check_password("wrong")


def test_list_review_items(monkeypatch):
    from core import review, wordpress
    monkeypatch.setattr(wordpress, "list_posts", lambda **k: [{
        "id": 7, "title": {"rendered": "テスト記事"},
        "excerpt": {"rendered": "<p>抜粋テキスト</p>"}, "link": "http://x/7",
        "featured_media": 3,
        "content": {"raw": '<h2>はじめに</h2><a class="amazon-cta-btn">x</a>'},
    }])
    monkeypatch.setattr(wordpress, "get_media_url",
                        lambda mid, **k: "http://x/img.jpg" if mid else "")
    items = review.list_review_items()
    assert items[0]["id"] == 7
    assert items[0]["thumb"] == "http://x/img.jpg"
    assert items[0]["excerpt"] == "抜粋テキスト"
    assert "errors" in items[0] and "warns" in items[0]


def test_canva_available_and_fallback(monkeypatch):
    from core import canva
    s = canva.get_settings()
    # 既定（config canva.enabled=false）では無効
    monkeypatch.setattr(canva, "_cfg", lambda: {"enabled": False})
    assert canva.available() is False
    # build_eyecatchは無効時にネットアクセスせずNone
    assert canva.build_eyecatch("コピー", b"img") is None

    # 設定が揃えば有効と判定
    monkeypatch.setattr(canva, "_cfg",
                        lambda: {"enabled": True, "brand_template_id": "BT123"})
    monkeypatch.setattr(s, "canva_client_id", "cid", raising=False)
    monkeypatch.setattr(s, "canva_client_secret", "sec", raising=False)
    monkeypatch.setattr(s, "canva_refresh_token", "rt", raising=False)
    monkeypatch.setattr(canva, "_stored_refresh_token", lambda: "")
    assert canva.available() is True


def test_first_image_src():
    from core import wordpress as wp
    body = '<p>x</p><figure><img alt="a" src="https://x/img1.jpg" width="100"></figure><img src="https://x/2.jpg">'
    assert wp.first_image_src(body) == "https://x/img1.jpg"
    assert wp.first_image_src("<p>no image</p>") == ""


def test_set_featured_media(monkeypatch):
    from core import wordpress as wp
    s = wp.get_settings()
    monkeypatch.setattr(s, "wp_base_url", "https://example.test", raising=False)
    monkeypatch.setattr(s, "wp_username", "u", raising=False)
    monkeypatch.setattr(s, "wp_app_password", "p", raising=False)

    captured = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": 30, "featured_media": captured.get("fm")}

    def fake_post(url, **k):
        captured["url"] = url
        captured["fm"] = k["json"]["featured_media"]
        return Resp()

    monkeypatch.setattr(wp.requests, "post", fake_post)
    out = wp.set_featured_media(30, 99)
    assert captured["url"].endswith("/posts/30")
    assert out["featured_media"] == 99


def test_upload_image_and_featured_media(monkeypatch):
    from core import wordpress as wp
    from core.models import Article

    # .envに依存せずWP設定をスタブ
    s = wp.get_settings()
    monkeypatch.setattr(s, "wp_base_url", "https://example.test", raising=False)
    monkeypatch.setattr(s, "wp_username", "u", raising=False)
    monkeypatch.setattr(s, "wp_app_password", "p", raising=False)

    class Resp:
        def __init__(self, json_data=None, content=b"", headers=None):
            self._j = json_data or {}
            self.content = content
            self.headers = headers or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._j

    monkeypatch.setattr(wp.requests, "get",
                        lambda *a, **k: Resp(content=b"IMG",
                                             headers={"content-type": "image/jpeg"}))
    captured = {}

    def fake_post(url, **k):
        captured.setdefault("posts", []).append((url, k.get("json")))
        if url.endswith("/media"):
            return Resp(json_data={"id": 99, "source_url": "https://example.test/img.jpg"})
        return Resp(json_data={"id": 30, "link": "https://example.test/p", "status": "draft"})

    monkeypatch.setattr(wp.requests, "post", fake_post)

    media = wp.upload_image_from_url("https://m.media-amazon.com/images/I/x.jpg")
    assert media["id"] == 99

    out = wp.create_draft(Article(title="t", body_html="<p>x</p>"), featured_media=99)
    assert out["id"] == 30
    # 投稿payloadにアイキャッチが入る
    post_payload = next(j for u, j in captured["posts"] if u.endswith("/posts"))
    assert post_payload["featured_media"] == 99


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


def test_note_html_links_and_images():
    from core import note_export
    from core.models import Article, Product
    body = "\n".join(["## はじめに", "x", "## とは", "x", "## おすすめ商品レビュー",
                       "x", "## 他メーカー比較", "x", "## まとめ", "x"])
    art = Article(affiliate_click_url="https://af.moshimo.com/af/c/click?a_id=1&url=u",
                  raw_sections={"body_md": body})
    imgs = [("https://assets.st-note.com/img/a.png", 1240, 826)]
    html, _ = note_export.build_note_html(art, Product(brand="X", category="Y"), imgs)
    assert html.count('id="') >= 3                            # 誘導ブロック3箇所
    assert 'src="https://assets.st-note.com/img/a.png"' in html  # 画像埋め込み
    assert 'width="620"' in html                              # 620pxへ縮小
    assert '<a href="https://af.moshimo.com' in html          # 成果リンク
    assert 'noopener" target="_blank"><img' in html           # 画像自体がクリック可能


def test_amazon_affiliate_url():
    from core import product_extractor as pe
    u = pe.amazon_affiliate_url("https://www.amazon.co.jp/dp/B0GS1PQ22W/ref=xxx", "chance274-22")
    assert u == "https://www.amazon.co.jp/dp/B0GS1PQ22W?tag=chance274-22"


def test_note_html_amazon_card_mode():
    from core import note_export
    from core.models import Article, Product
    body = "\n".join(["## はじめに", "x", "## とは", "x", "## おすすめ商品レビュー",
                       "x", "## 他メーカー比較", "x", "## まとめ", "x"])
    art = Article(raw_sections={"body_md": body})
    embeds = [{"url": "https://www.amazon.co.jp/dp/B0GS1PQ22W?tag=chance274-22",
               "key": f"emb{i}", "html": f'<span><div class="external-article-widget">c{i}</div></span>'}
              for i in range(3)]
    html, _ = note_export.build_note_html(art, Product(brand="X", category="Y"), amazon_embeds=embeds)
    # 3箇所すべて固有キー
    for i in range(3):
        assert f'embedded-content-key="emb{i}"' in html
    assert html.count("external-article-widget") == 3
    assert "af.moshimo.com" not in html


def test_get_image_size_png():
    from core import note_export
    # PNGヘッダ(幅=300,高さ=200)を最小構成で作る
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (300).to_bytes(4, "big") + (200).to_bytes(4, "big")
    assert note_export.get_image_size(png) == (300, 200)


def test_no_stray_bold_markers():
    """対になっていない ** が文字列として残らない（note/WP両方）。"""
    from core import note_export
    from core.content_generator import _inline as wp_inline
    from core.models import Article, Product
    # 閉じない ** が混ざった本文でも、リテラルの ** は残さない
    art = Article(raw_sections={"body_md": "コスパ重視の方**におすすめ。安心の**正規品**です。"})
    html, _ = note_export.build_note_html(art, Product(brand="X", category="Y"))
    assert "**" not in html
    # WP側: 正しく対になっていれば太字化、余分な ** は消える
    assert "**" not in wp_inline("未対応の**マーカー")
    assert "<strong>太字</strong>" in wp_inline("これは**太字**です")


def test_batch_load_queue_dispatches(monkeypatch, tmp_path):
    from core import batch
    # URL → sheet_queue.fetch_rows
    monkeypatch.setattr(batch.sheet_queue, "fetch_rows", lambda url: [{"brand": "X"}])
    assert batch.load_queue("https://docs.google.com/x")[0]["brand"] == "X"
    # パス → ローカルCSV
    q = tmp_path / "q.csv"
    q.write_text("brand,category\nY,扇風機\n", encoding="utf-8")
    assert batch.load_queue(str(q))[0]["brand"] == "Y"
