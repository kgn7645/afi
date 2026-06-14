"""
Issue #5: アイキャッチ画像の自動生成。
キャッチコピー＋商品画像を Pillow で合成して 1200x630(OGP) のPNGを作る。
Canva APIは使わずローカル合成（フォントが見つからない環境では None を返し、
呼び出し側は商品画像そのまま(#42)にフォールバックする）。
"""
from __future__ import annotations

import io
import os

from .config import get_rules

# 日本語対応フォント候補（mac / 主要Linux）。configで明示も可。
_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
]

CANVAS_W, CANVAS_H = 1200, 630


def _find_font_path() -> str:
    cfg = get_rules().get("eyecatch", {}).get("font_path", "")
    for p in ([cfg] if cfg else []) + _FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return ""


def available() -> bool:
    """Pillowと日本語フォントが揃っていれば True。"""
    try:
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(_find_font_path())


def _wrap_jp(text: str, font, draw, max_width: int) -> list[str]:
    """日本語向けに文字単位で折り返す。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) <= max_width:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _fit_font(font_path: str, text: str, area_w: int, area_h: int,
              draw, max_size: int = 66, min_size: int = 32):
    """折り返してエリアに収まる最大のフォントサイズを選ぶ。"""
    from PIL import ImageFont

    size = max_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap_jp(text, font, draw, area_w)
        line_h = int(size * 1.35)
        if len(lines) * line_h <= area_h:
            return font, lines, line_h
        size -= 4
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap_jp(text, font, draw, area_w), int(min_size * 1.35)


def build_eyecatch(catch_copy: str, product_image: bytes,
                   *, brand: str = "", site_name: str = "") -> bytes | None:
    """アイキャッチPNG(bytes)を生成。フォント無し/失敗時は None。"""
    font_path = _find_font_path()
    if not font_path or not (catch_copy or "").strip():
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        cfg = get_rules().get("eyecatch", {})
        bg = cfg.get("bg_color", "#0d1b2a")
        accent = cfg.get("accent_color", "#ff9900")
        text_color = cfg.get("text_color", "#ffffff")

        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), bg)
        draw = ImageDraw.Draw(canvas)
        # 左端アクセント帯
        draw.rectangle([0, 0, 18, CANVAS_H], fill=accent)

        # 右側に商品画像（白カードに乗せる）
        if product_image:
            try:
                prod = Image.open(io.BytesIO(product_image)).convert("RGBA")
                prod.thumbnail((470, 450))
                pad = 24
                panel_w, panel_h = prod.width + pad * 2, prod.height + pad * 2
                panel = Image.new("RGB", (panel_w, panel_h), "#ffffff")
                px = CANVAS_W - panel_w - 60
                py = (CANVAS_H - panel_h) // 2
                canvas.paste(panel, (px, py))
                canvas.paste(prod, (px + pad, py + pad), prod)
            except Exception:  # noqa: BLE001
                pass

        # 左側にキャッチコピー
        margin_x, area_w = 70, 540
        area_h = 360
        font, lines, line_h = _fit_font(font_path, catch_copy, area_w, area_h, draw)
        total_h = len(lines) * line_h
        y = (CANVAS_H - total_h) // 2 - 20
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill=text_color)
            y += line_h

        # 左下にサイト名/ブランドのバッジ
        label = site_name or brand
        if label:
            small = ImageFont.truetype(font_path, 28)
            tw = draw.textlength(label, font=small)
            bx, by = margin_x, CANVAS_H - 70
            draw.rectangle([bx - 12, by - 8, bx + tw + 12, by + 40], fill=accent)
            draw.text((bx, by), label, font=small, fill="#111111")

        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None
