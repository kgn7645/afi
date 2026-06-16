"""
バッチ実行（Issue #9）＋ 重複防止（Issue #10）。

キューCSV（商品リスト）を読み、1日N件まで無人で
「選定→記事生成→もしもリンク自動生成→WP下書き」を実行する。
- 既出商品（articles_log.csv）と同一バッチ内の重複はスキップ
- 個別の失敗でバッチ全体は止めない（スキップして継続）
"""
from __future__ import annotations

import csv
from pathlib import Path

from . import pipeline, sheet_queue
from .config import ROOT
from .gemini_client import GeminiClient

QUEUE_FIELDS = [
    "brand", "category", "model_number", "product_name",
    "price", "company_hint", "url", "affiliate_link_html",
]


def dedup_key(brand: str = "", category: str = "", model_number: str = "",
              product_name: str = "", **_: object) -> str:
    """重複判定キー。ブランド+型番 > ブランド+カテゴリ > 商品名 の優先で正規化。"""
    brand = (brand or "").strip().lower()
    model = (model_number or "").strip().lower()
    category = (category or "").strip().lower()
    if brand and model:
        return f"{brand}|{model}"
    if brand and category:
        return f"{brand}|{category}"
    return (product_name or "").strip().lower()


def load_processed_keys() -> set[str]:
    """過去に生成済みの商品キー集合を articles_log.csv から作る。"""
    log = pipeline.LOG_PATH
    keys: set[str] = set()
    if not log.exists():
        return keys
    with log.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add(dedup_key(
                brand=row.get("brand", ""), category=row.get("category", ""),
                model_number=row.get("model_number", ""),
            ))
    keys.discard("")
    return keys


def read_queue(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"キューが見つかりません: {p}")
    with p.open(encoding="utf-8-sig") as f:
        return [dict(row) for row in csv.DictReader(f)]


def load_queue(source: str | Path) -> list[dict]:
    """キューを読み込む。URLならGoogleスプレッドシート公開CSV、それ以外はローカルCSV。"""
    if sheet_queue.is_url(str(source)):
        return sheet_queue.fetch_rows(str(source))
    return read_queue(source)


def run_batch(
    *,
    queue_path: str | Path,
    limit: int = 15,
    post_to_wp: bool = True,
    wp_status: str = "draft",
    skip_dedup: bool = False,
) -> dict:
    """キューを処理。 {generated, skipped_dup, failed, items[]} を返す。"""
    rows = load_queue(queue_path)
    processed = set() if skip_dedup else load_processed_keys()
    seen: set[str] = set()
    gemini = GeminiClient()  # レート制御を共有するため使い回す

    summary = {"generated": 0, "skipped_dup": 0, "failed": 0, "items": []}

    for row in rows:
        if summary["generated"] >= limit:
            break

        key = dedup_key(**row)
        if not skip_dedup and key and (key in processed or key in seen):
            summary["skipped_dup"] += 1
            summary["items"].append({"key": key, "status": "skipped_dup"})
            continue
        seen.add(key)

        manual = {
            "brand": row.get("brand", "").strip(),
            "category": row.get("category", "").strip(),
            "model_number": row.get("model_number", "").strip(),
            "product_name": row.get("product_name", "").strip(),
            "company_hint": row.get("company_hint", "").strip(),
            "price": int(row["price"]) if str(row.get("price", "")).strip().isdigit() else None,
            "specs": [],
        }
        try:
            result = pipeline.run(
                url=row.get("url", "").strip(),
                manual=manual,
                affiliate_link_html=row.get("affiliate_link_html", "").strip(),
                post_to_wp=post_to_wp,
                wp_status=wp_status,
                gemini=gemini,
            )
            if not result.selection_ok:
                summary["failed"] += 1
                summary["items"].append({"key": key, "status": "selection_ng", "reason": result.selection_reason})
                continue
            summary["generated"] += 1
            summary["items"].append({
                "key": key, "status": "ok",
                "title": result.article.title if result.article else "",
                "wp_post_id": result.wp_post_id,
                "warnings": result.warnings,
            })
        except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
            summary["failed"] += 1
            summary["items"].append({"key": key, "status": "error", "error": str(e)})

    summary["gemini_usage"] = gemini.usage_summary()
    return summary


def run_candidates_batch(
    *, limit: int = 10, post_to_wp: bool = True, wp_status: str = "draft",
) -> dict:
    """承認済み(approved)候補から記事化（Issue #3/#12 Phase3）。

    成功した候補は status=generated に更新（再生成防止）。
    候補プール(CANDIDATES_WEBHOOK_URL)未設定なら空サマリ。
    """
    from . import candidates, overrides

    approved = candidates.list_by_status("approved", limit=limit * 3)
    # 手動選定したASINは足切りをバイパス（ユーザーが意図して選んだもの）
    try:
        manual_asins = set(overrides.load(force=True).get("_manual_asins", []) or [])
    except Exception:  # noqa: BLE001
        manual_asins = set()
    gemini = GeminiClient()
    summary = {"generated": 0, "skipped_dup": 0, "failed": 0, "items": []}

    for cand in approved:
        if summary["generated"] >= limit:
            break
        asin = cand.get("asin", "")
        title = cand.get("title", "")
        cand_url = cand.get("url", "").strip()
        is_rakuten = "rakuten" in cand_url  # 楽天候補はURLで判別（source列に依存しない）
        is_manual = asin in manual_asins
        rk_item = ({"name": title, "url": cand_url, "image": cand.get("image", "")}
                   if is_rakuten else None)
        try:
            result = pipeline.run(
                url=cand_url,
                manual={"product_name": title, "specs": [], "price": cand.get("price")},
                rakuten_item=rk_item, skip_selection_gate=is_manual,
                post_to_wp=post_to_wp, wp_status=wp_status, gemini=gemini,
            )
            if not result.selection_ok and not is_manual:
                # 選定NGは決定論的（毎回必ず失敗）。rejectedにして無限リトライを防ぐ
                # ※手動選定(is_manual)は足切りをバイパスしてそのまま記事化する
                candidates.set_status(asin, "rejected")
                summary["failed"] += 1
                summary["items"].append(
                    {"key": asin, "status": "selection_ng", "reason": result.selection_reason})
                continue
            candidates.set_status(asin, "generated")
            summary["generated"] += 1
            summary["items"].append({
                "key": asin, "status": "ok",
                "title": result.article.title if result.article else "",
                "wp_post_id": result.wp_post_id, "warnings": result.warnings,
            })
        except Exception as e:  # noqa: BLE001
            summary["failed"] += 1
            summary["items"].append({"key": asin, "status": "error", "error": str(e)})
            # Gemini無料枠切れ(429)なら、以降も確実に失敗するので早期打ち切り
            # （承認分はapprovedのまま残り、次回ポーリングで再試行される）
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                summary["items"].append(
                    {"key": "-", "status": "quota_exhausted",
                     "error": "Geminiの枠切れのため中断（次回再試行）"})
                break

    summary["gemini_usage"] = gemini.usage_summary()
    return summary


def queue_template_path() -> Path:
    return ROOT / "data" / "queue.example.csv"
