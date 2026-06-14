"""
Issue #44: E-E-A-T基盤の固定ページを一括作成（運営者情報・お問い合わせ等）。
既定は下書き。運営者が実情報（連絡先・プロフィール）を埋めてから公開する想定。

使い方:
  python scripts/setup_site.py            # 下書きで作成
  python scripts/setup_site.py --publish  # いきなり公開（非推奨）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import site_setup  # noqa: E402
from core import wordpress  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="E-E-A-T固定ページの一括作成")
    p.add_argument("--publish", action="store_true", help="下書きでなく公開で作成")
    args = p.parse_args()

    ok, msg = wordpress.test_connection()
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        return

    status = "publish" if args.publish else "draft"
    print(f"固定ページを作成中（status={status}）…")
    for r in site_setup.bootstrap_pages(status=status):
        print(f"  {r['slug']}: {r['action']} (id={r['id']}, {r['status']})")
    print("完了。WP管理画面で内容（連絡先・運営者情報）を確認・編集してください。")


if __name__ == "__main__":
    main()
