"""Threadsドラフトの生成（cron）。config threads.accounts ごとに per_run 件のドラフトを作る。

  python scripts/threads_generate.py          # threads.enabled=true のとき生成
  python scripts/threads_generate.py --force   # enabled無視で生成（確認用）
承認は Web の /threads で人が画像を選んで行う。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import threads_pipeline  # noqa: E402
from core.config import get_rules  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    tc = get_rules().get("threads", {}) or {}
    if not args.force and not tc.get("enabled", False):
        print("[threads] 生成は無効（threads.enabled=false）")
        return
    accounts = tc.get("accounts", []) or []
    if not accounts:
        print("[threads] アカウント未設定（threads.accounts）")
        return
    total = 0
    for acc in accounts:
        try:
            n = threads_pipeline.generate_drafts(acc, int(acc.get("per_run", 3)))
            print(f"[threads] {acc.get('id')}: ドラフト {n} 件")
            total += n
        except Exception as e:  # noqa: BLE001
            print(f"[threads] {acc.get('id')}: 失敗 {e}")
    print(f"[threads] 合計 {total} 件生成")


if __name__ == "__main__":
    main()
