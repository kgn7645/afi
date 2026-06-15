"""
バッチ実行CLI（Issue #9）。キューCSVから1日N件を無人生成→WP下書き。

例:
  python batch.py                                  # data/queue.csv を15件まで処理
  python batch.py --queue data/queue.csv --limit 10
  python batch.py --no-wp                          # WP送らず生成のみ（動作確認）
  python batch.py --status publish                 # 即公開（非推奨・通常はdraft）

cron例（毎朝6時に15件）:
  0 6 * * *  cd /path/to/affiliate-automation && .venv/bin/python batch.py --limit 15 >> data/batch.log 2>&1
"""
from __future__ import annotations

import argparse

from core import batch, notify
from core.config import ROOT, get_settings

LOG_PATH = ROOT / "data" / "batch.log"


def _rotate_log(max_bytes: int = 2_000_000, keep_lines: int = 1000) -> None:
    """batch.log が肥大化したら末尾だけ残す簡易ローテーション（Issue #21）。"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > max_bytes:
            tail = LOG_PATH.read_text(errors="ignore").splitlines()[-keep_lines:]
            LOG_PATH.write_text("\n".join(tail) + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    _rotate_log()
    # 既定キュー: QUEUE_SHEET_CSV_URL があればスプレッドシート公開CSV、なければローカルCSV
    from core.config import get_settings
    default_queue = get_settings().queue_sheet_csv_url or str(ROOT / "data" / "queue.csv")

    p = argparse.ArgumentParser(description="アフィリエイト記事のバッチ生成")
    p.add_argument("--queue", default=default_queue, help="キューCSVのパス or 公開CSV URL")
    p.add_argument("--limit", type=int, default=15, help="1回で生成する最大件数")
    p.add_argument("--no-wp", action="store_true", help="WordPressへ送らない")
    p.add_argument("--status", default="draft", choices=["draft", "publish"], help="投稿ステータス")
    p.add_argument("--skip-dedup", action="store_true", help="重複チェックを無効化")
    p.add_argument("--candidates", action="store_true",
                   help="承認済み候補(スワイプ選定)から生成する（CSVキューの代わり）")
    args = p.parse_args()

    src = "承認済み候補" if args.candidates else args.queue
    print(f"[batch] source={src} limit={args.limit} wp={not args.no_wp} status={args.status}")
    try:
        if args.candidates:
            s = batch.run_candidates_batch(
                limit=args.limit, post_to_wp=not args.no_wp, wp_status=args.status)
        else:
            s = batch.run_batch(
                queue_path=args.queue, limit=args.limit,
                post_to_wp=not args.no_wp, wp_status=args.status, skip_dedup=args.skip_dedup,
            )
    except FileNotFoundError as e:
        # cronで毎日走るため、キュー未配置でも異常終了させず静かに終える
        print(f"[batch] キューが無いためスキップ: {e}")
        return
    except Exception as e:  # noqa: BLE001
        # 想定外の異常終了は必ず通知して非ゼロ終了（cron監視で検知）
        import traceback
        tb = traceback.format_exc()
        print(f"[batch] 🔥 異常終了: {e}\n{tb}")
        notify.send(f"🔥 記事バッチが異常終了しました\n{e}\n```\n{tb[-700:]}\n```")
        raise SystemExit(1)

    print("=" * 60)
    print(f"生成: {s['generated']} / 重複スキップ: {s['skipped_dup']} / 失敗: {s['failed']}")
    for it in s["items"]:
        if it["status"] == "ok":
            wid = it.get("wp_post_id")
            warn = f"  ⚠{len(it['warnings'])}件" if it.get("warnings") else ""
            print(f"  ✅ {it.get('title','')[:40]}  (WP#{wid}){warn}")
        elif it["status"] == "skipped_dup":
            print(f"  ⏭  重複スキップ: {it['key']}")
        elif it["status"] == "selection_ng":
            print(f"  ⛔ 選定NG: {it['key']} - {it.get('reason','')}")
        else:
            print(f"  ❌ 失敗: {it['key']} - {it.get('error', it.get('status'))}")
    print("=" * 60)

    # 通知（Issue #21）: 失敗があれば必ず、成功時はNOTIFY_ON_SUCCESSに従う
    if notify.enabled() and (s["failed"] > 0 or get_settings().notify_on_success):
        notify.send(notify.summarize_batch(s))


if __name__ == "__main__":
    main()
