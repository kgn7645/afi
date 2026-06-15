"""
note 下書き自動作成（Issue #2・非公式API）。
記事を生成し、note に「下書き」を作成する（公開はしない＝安全）。

⚠️ 非公式・規約上非推奨・仕様変更で壊れうる。自分のアカウントの自動化用。

事前に .env へ:  NOTE_SESSION=<ブラウザの _note_session_v5 の値>

例:
  python note_post.py --brand RANVOO --category ネッククーラー
  python note_post.py --test          # セッション有効性の確認のみ
"""
from __future__ import annotations

import argparse

from core import note_client, note_publish, pipeline


def main() -> None:
    p = argparse.ArgumentParser(description="note下書きの自動作成")
    p.add_argument("--brand", default="")
    p.add_argument("--category", default="")
    p.add_argument("--url", default="", help="Amazon商品URL")
    p.add_argument("--company-hint", dest="company_hint", default="")
    p.add_argument("--test", action="store_true", help="セッション確認のみ")
    args = p.parse_args()

    if args.test:
        ok, msg = note_client.test_connection()
        print(("✅ " if ok else "❌ ") + msg)
        return

    # 記事生成（WPには送らない。note専用）
    result = pipeline.run(
        url=args.url,
        manual={"brand": args.brand, "category": args.category,
                "company_hint": args.company_hint, "specs": []},
        post_to_wp=False,
    )
    if not result.article:
        print("記事生成に失敗:", result.selection_reason)
        for w in result.warnings:
            print("⚠", w)
        return

    print(f"タイトル: {result.article.title}")
    nd = note_publish.create_note_draft(
        result.article, result.product, source_url=args.url, result=result)
    if nd:
        print("✅ note下書きを作成しました")
        print(f"   下書きID: {nd['id']}  編集URL: {nd['edit_url']}")
        print("   → note の「下書き」一覧で確認し、問題なければnote側で公開してください。")
    else:
        print("❌ note下書きの作成に失敗（NOTE_SESSION未設定/期限切れの可能性）")
        for w in result.warnings:
            print("⚠", w)


if __name__ == "__main__":
    main()
