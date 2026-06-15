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

import time

from core import batch, notify
from core.config import ROOT, get_rules, get_settings

LOG_PATH = ROOT / "data" / "batch.log"
_GEN_MARKER = ROOT / "data" / ".last_gen"


def _interval_gate() -> bool:
    """設定の generation.interval_minutes 未満なら False（cronは5分毎・実間隔はここで制御）。"""
    iv = int(get_rules().get("generation", {}).get("interval_minutes", 20))
    last = 0.0
    try:
        last = float(_GEN_MARKER.read_text().strip())
    except Exception:  # noqa: BLE001
        last = 0.0
    if time.time() - last < iv * 60 - 30:   # 30秒の余裕（cronの早発火対策）
        return False
    try:
        _GEN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _GEN_MARKER.write_text(str(time.time()))
    except Exception:  # noqa: BLE001
        pass
    return True


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
    p.add_argument("--limit", type=int, default=0, help="1回の生成数（0=設定値を使用）")
    p.add_argument("--no-wp", action="store_true", help="WordPressへ送らない")
    p.add_argument("--status", default="draft", choices=["draft", "publish"], help="投稿ステータス")
    p.add_argument("--skip-dedup", action="store_true", help="重複チェックを無効化")
    p.add_argument("--candidates", action="store_true",
                   help="承認済み候補(スワイプ選定)から生成する（CSVキューの代わり）")
    args = p.parse_args()

    # 手動選定の短縮リンク解決（毎回＝5分毎）。生成ゲートより前に回して取りこぼさない。
    if args.candidates:
        try:
            from core import manual_resolve
            added = manual_resolve.resolve_pending()
            if added:
                print(f"[batch] 手動選定の短縮リンクを解決: {added}件を選定済みへ")
        except Exception as e:  # noqa: BLE001
            print(f"[batch] 短縮リンク解決でエラー（継続）: {e}")

    # 承認済み候補モードは「設定の実行間隔」でゲート（cronは5分毎に回す前提）
    if args.candidates and not _interval_gate():
        print("[batch] 実行間隔に未到達のためスキップ")
        return

    gen = get_rules().get("generation", {})
    limit = args.limit or (gen.get("per_run", 2) if args.candidates else 15)
    src = "承認済み候補" if args.candidates else args.queue
    print(f"[batch] source={src} limit={limit} wp={not args.no_wp} status={args.status}")
    try:
        if args.candidates:
            s = batch.run_candidates_batch(
                limit=limit, post_to_wp=not args.no_wp, wp_status=args.status)
        else:
            s = batch.run_batch(
                queue_path=args.queue, limit=limit,
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

    # 通知（Issue #21）: 生成があった時だけ通知（短間隔ポーリングのスパム回避）。
    # 空振り/枠切れの連続失敗では通知しない。異常終了は上のexceptで別途通知。
    # NOTIFY_ON_SUCCESS=false なら成功時も黙る（失敗のみ運用にしたい場合）。
    if notify.enabled() and s["generated"] > 0 and get_settings().notify_on_success:
        notify.send(notify.summarize_batch(s))


if __name__ == "__main__":
    main()
