"""
Issue #5: アイキャッチ画像の自動生成（Pillowローカル合成）。
キャッチコピー＋商品画像から 1200x630(OGP) のPNGを作る。
グラデ背景／「どこの国？」バッジ／星評価／角丸カード＋影で素人感を排除。
フォントが見つからない環境では None を返し、呼び出し側は商品画像(#42)に
フォールバックする。Canva版は core/canva.py を参照（pipelineで優先）。
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


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _vertical_gradient(c1: str, c2: str, w: int, h: int):
    from PIL import Image

    a, b = _hex(c1), _hex(c2)
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return col.resize((w, h))


def _rounded(size, radius, fill):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                         radius=radius, fill=fill)
    return im


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
              draw, max_size: int = 60, min_size: int = 34):
    """折り返してエリアに収まる最大のフォントサイズを選ぶ。"""
    from PIL import ImageFont

    size = max_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap_jp(text, font, draw, area_w)
        line_h = int(size * 1.34)
        if len(lines) * line_h <= area_h:
            return font, lines, line_h
        size -= 4
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap_jp(text, font, draw, area_w), int(min_size * 1.34)


def _star_glyphs(stars: float) -> str:
    full = int(round(stars))
    full = max(0, min(5, full))
    return "★" * full + "☆" * (5 - full)


def build_eyecatch(catch_copy: str, product_image: bytes,
                   *, brand: str = "", site_name: str = "",
                   stars: float | None = None) -> bytes | None:
    """アイキャッチPNG(bytes)を生成。フォント無し/失敗時は None。"""
    font_path = _find_font_path()
    if not font_path or not (catch_copy or "").strip():
        return None
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont

        cfg = get_rules().get("eyecatch", {})
        bg1 = cfg.get("bg_color", "#0d1b2a")
        bg2 = cfg.get("bg_color2", "#16324f")
        accent = cfg.get("accent_color", "#ff9900")
        text_color = cfg.get("text_color", "#ffffff")

        canvas = _vertical_gradient(bg1, bg2, CANVAS_W, CANVAS_H)
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, 16, CANVAS_H], fill=accent)  # 左端アクセント帯

        # 右側に商品画像（角丸白カード＋ソフトシャドウ）
        if product_image:
            try:
                prod = Image.open(io.BytesIO(product_image)).convert("RGBA")
                prod.thumbnail((430, 420))
                pad = 26
                cw, ch = prod.width + pad * 2, prod.height + pad * 2
                px = CANVAS_W - cw - 70
                py = (CANVAS_H - ch) // 2
                shadow = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
                ImageDraw.Draw(shadow).rounded_rectangle(
                    [px + 8, py + 12, px + cw + 8, py + ch + 12], radius=24, fill=(0, 0, 0, 120))
                canvas.paste(shadow.filter(ImageFilter.GaussianBlur(12)), (0, 0),
                             shadow.filter(ImageFilter.GaussianBlur(12)))
                panel = _rounded((cw, ch), 24, (255, 255, 255, 255))
                canvas.paste(panel, (px, py), panel)
                canvas.paste(prod, (px + pad, py + pad), prod)
            except Exception:  # noqa: BLE001
                pass

        margin_x, area_w = 70, 560

        # 「<brand>はどこの国？」バッジ
        top_y = 70
        if brand:
            bf = ImageFont.truetype(font_path, 30)
            btxt = f"{brand}はどこの国？"
            bw = int(draw.textlength(btxt, font=bf))
            badge = _rounded((bw + 44, 56), 28, _hex(accent) + (255,))
            canvas.paste(badge, (margin_x, top_y), badge)
            draw.text((margin_x + 22, top_y + 11), btxt, font=bf, fill="#111111")

        # キャッチコピー
        copy_top = 165
        font, lines, line_h = _fit_font(font_path, catch_copy, area_w, 300, draw)
        y = copy_top
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill=text_color)
            y += line_h

        # 星評価
        if stars:
            sf = ImageFont.truetype(font_path, 40)
            draw.text((margin_x, y + 8), f"{_star_glyphs(stars)}  {stars}/5.0",
                      font=sf, fill="#ffd166")

        # 左下サイト名
        if site_name:
            sf2 = ImageFont.truetype(font_path, 28)
            draw.text((margin_x + 2, CANVAS_H - 64), site_name, font=sf2, fill="#ffffff")

        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None
