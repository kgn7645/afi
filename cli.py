"""
CLI実行（Web UIを使わずコマンドから1記事生成）。
cron等での自動量産にも使える。

例:
  python cli.py --url "https://www.amazon.co.jp/dp/XXXXXXXXXX" --category "DCモーター扇風機"
  python cli.py --brand COMFEE' --category 扇風機 --model CFS-12 --name "..." --no-wp
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from core import pipeline
from core.config import ROOT


def main() -> None:
    p = argparse.ArgumentParser(description="アフィリエイト記事の自動生成")
    p.add_argument("--url", default="", help="Amazon商品URL")
    p.add_argument("--brand", default="")
    p.add_argument("--category", default="")
    p.add_argument("--model", dest="model_number", default="")
    p.add_argument("--name", dest="product_name", default="")
    p.add_argument("--price", type=int, default=None)
    p.add_argument("--company-hint", dest="company_hint", default="")
    p.add_argument("--link", dest="affiliate_link_html", default="")
    p.add_argument("--no-wp", action="store_true", help="WordPressへ送らない")
    p.add_argument("--publish", action="store_true", help="下書きでなく即公開")
    p.add_argument("--force", action="store_true", help="選定NGでも生成")
    p.add_argument("--note", action="store_true", help="note貼り付け用Markdownも出力(data/note/)")
    args = p.parse_args()

    result = pipeline.run(
        url=args.url,
        manual={
            "brand": args.brand, "category": args.category,
            "model_number": args.model_number, "product_name": args.product_name,
            "price": args.price, "company_hint": args.company_hint, "specs": [],
        },
        affiliate_link_html=args.affiliate_link_html,
        post_to_wp=not args.no_wp,
        wp_status="publish" if args.publish else "draft",
        skip_selection_gate=args.force,
    )

    print("=" * 60)
    print(f"選定: {'OK' if result.selection_ok else 'NG'} - {result.selection_reason}")
    for w in result.warnings:
        print(f"⚠ {w}")
    if result.article:
        print(f"タイトル: {result.article.title}")
        print(f"コピー  : {result.article.catch_copy}")
        print(f"メタ    : {result.article.meta_description}")
        print(f"KW      : {', '.join(result.article.meta_keywords)}")
    if result.wp_post_id:
        print(f"WP投稿ID: {result.wp_post_id}  編集: {result.wp_edit_link}")

    # 本文プレビューをHTMLファイルに保存（ブラウザで全文確認用）
    if result.article and result.article.body_html:
        slug = re.sub(r"[^\w]+", "-", (result.article.title or "article"))[:40].strip("-")
        out_dir = ROOT / "data" / "previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{slug}.html"
        a = result.article
        kw = ", ".join(a.meta_keywords)
        out.write_text(
            f"<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
            f"<title>{a.title}</title>"
            f"<style>body{{font-family:sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.8}}"
            f".meta{{background:#f5f5f7;border-radius:8px;padding:12px 16px;font-size:13px;color:#444}}"
            f"h1{{font-size:22px}} h2{{border-left:4px solid #ff9900;padding-left:8px}} h3{{font-size:15px}}</style></head><body>"
            f"<h1>{a.title}</h1>"
            f"<div class='meta'><b>キャッチコピー:</b> {a.catch_copy}<br>"
            f"<b>メタ説明:</b> {a.meta_description}<br><b>キーワード:</b> {kw}<br>"
            f"<b>信頼度(総合):</b> ★{a.trust_total or '-'}/5.0</div>"
            f"{a.body_html}</body></html>",
            encoding="utf-8",
        )
        print(f"📄 本文プレビュー保存: {out}")

    # note用Markdown出力
    if args.note and result.article:
        from core import note_export
        note_md = note_export.build_note_markdown(result.article, result.product)
        note_dir = ROOT / "data" / "note"
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{slug}.md"
        note_path.write_text(note_md, encoding="utf-8")
        print(f"📝 note用Markdown保存: {note_path}")
        print("----- note貼り付け用（ここから）-----")
        print(note_md)
        print("----- note貼り付け用（ここまで）-----")
    print("=" * 60)


if __name__ == "__main__":
    main()
