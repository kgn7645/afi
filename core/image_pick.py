"""画像候補のAI不使用フィルタ/整形（Threads用）。

- 文字/バナーが多い画像を除外（エッジ密度ヒューリスティック）
- 白背景・商品単体っぽいものを上位に（四隅の白さ）
- 白ふち除去（near-whiteの外周をトリム）
PILのみ。Geminiなどは使わない。
"""
from __future__ import annotations

import io
import urllib.request

from PIL import Image, ImageFilter, ImageStat


def _fetch(url: str, timeout: int = 15) -> Image.Image | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Image.open(io.BytesIO(r.read())).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _metrics(img: Image.Image) -> tuple[float, float]:
    """(四隅の白さ0-1, エッジ密度) を返す。エッジ密度が高い=文字/バナーが多い。"""
    w, h = img.size
    pts = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]
    white = sum(1 for x, y in pts if min(img.getpixel((x, y))[:3]) > 232) / 4.0
    g = img.convert("L").resize((128, 128))
    edge = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    return white, edge


def _trim_white(img: Image.Image, thresh: int = 238) -> Image.Image:
    """near-whiteの外周をトリム（白ふち除去）。商品が消えない範囲で。"""
    gray = img.convert("L")
    # 白(>thresh)を0、それ以外を255にした2値マスクのbboxでクロップ
    mask = gray.point(lambda p: 0 if p > thresh else 255)
    bbox = mask.getbbox()
    if not bbox:
        return img
    # 少しだけ余白を残す
    pad = 6
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(img.width, x1 + pad); y1 = min(img.height, y1 + pad)
    # トリムしすぎ（極端に小さい）は元画像を返す
    if (x1 - x0) < img.width * 0.3 or (y1 - y0) < img.height * 0.3:
        return img
    return img.crop((x0, y0, x1, y1))


def rank_urls(urls: list[str], *, limit: int = 8, edge_max: float = 50.0) -> list[str]:
    """候補URLをDLしてスコア。文字/バナー(高エッジ)を除外し、きれいな順(白背景・低エッジ)に。

    失敗/未取得のURLは末尾に温存（除外しすぎ防止）。返りはURL（原本のまま）。
    """
    scored, failed = [], []
    for u in urls:
        img = _fetch(u)
        if img is None:
            failed.append(u)
            continue
        white, edge = _metrics(img)
        # きれいさスコア: 白背景は加点、エッジ(文字)は減点
        score = white * 40 - edge
        scored.append((u, score, edge))
    # 文字過多(高エッジ)を除外。ただし全滅回避で最低3枚は残す
    kept = [s for s in scored if s[2] <= edge_max]
    if len(kept) < 3:
        kept = sorted(scored, key=lambda s: s[2])[:max(3, len(scored))]
    kept.sort(key=lambda s: s[1], reverse=True)
    out = [u for u, _, _ in kept] + failed
    return out[:limit]


def trim_white_bytes(url: str) -> bytes | None:
    """URLの画像の白ふちを除去してPNGバイトで返す（公開ホスティング用）。"""
    img = _fetch(url, timeout=20)
    if img is None:
        return None
    img = _trim_white(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
