"""アプリ内バックグラウンドジョブ。重いAI/収集処理をUIから切り離す。

単一ワーカーで直列実行＝WP overrides の load→merge→save 競合を避けつつ、
UIは投入後すぐ返せる（次の操作にすぐ移れる）。状態は /jobs/status でポーリング。
プロセス内メモリ保持（Render/ローカルとも、起動中のプロセスで完結）。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_MAX = 200  # 完了ジョブの保持上限（古いものから破棄）


def submit(kind: str, account: str, fn: Callable[[], Any], *, label: str = "") -> str:
    """fnをバックグラウンドで実行し、即座にjob_idを返す。"""
    jid = uuid.uuid4().hex[:12]
    now = int(time.time())
    with _lock:
        _jobs[jid] = {"id": jid, "kind": kind, "account": account, "label": label,
                      "state": "queued", "message": "", "result": None,
                      "created": now, "finished": 0}
        _prune_locked()

    def run() -> None:
        with _lock:
            if jid in _jobs:
                _jobs[jid]["state"] = "running"
        state, msg, res = "done", "", None
        try:
            res = fn()
            msg = _summary(kind, res)
        except Exception as e:  # noqa: BLE001
            state, msg = "error", str(e)[:200]
        with _lock:
            if jid in _jobs:
                _jobs[jid].update(state=state, result=res, message=msg,
                                  finished=int(time.time()))

    _executor.submit(run)
    return jid


def _summary(kind: str, res: Any) -> str:
    if kind == "articleize":
        return {"done": "記事化しました（投稿タブで承認）",
                "queued": "生成待ちに追加しました",
                "fail": "記事化に失敗しました"}.get(str(res), str(res))
    if kind == "collect":
        return f"{res}件を候補に収集しました"
    if kind == "generate":
        return f"つぶやきを{res}件生成しました"
    return str(res)


def for_account(account: str, limit: int = 15) -> list[dict]:
    with _lock:
        js = [dict(j) for j in _jobs.values() if j.get("account") == account]
    js.sort(key=lambda j: j["created"], reverse=True)
    return js[:limit]


def active_count(account: str | None = None) -> int:
    with _lock:
        return sum(1 for j in _jobs.values()
                   if j["state"] in ("queued", "running")
                   and (account is None or j.get("account") == account))


def _prune_locked() -> None:
    if len(_jobs) <= _MAX:
        return
    done = sorted((j for j in _jobs.values() if j["state"] in ("done", "error")),
                  key=lambda j: j["finished"])
    for j in done[:len(_jobs) - _MAX]:
        _jobs.pop(j["id"], None)
