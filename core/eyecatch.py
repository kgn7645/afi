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


def _fit_single(font_path: str, text: str, max_w: int, draw,
                max_size: int, min_size: int):
    from PIL import ImageFont

    size = max_size
    while size > min_size:
        f = ImageFont.truetype(font_path, size)
        if draw.textlength(text, font=f) <= max_w:
            return f, size
        size -= 6
    return ImageFont.truetype(font_path, min_size), min_size


def build_eyecatch(catch_copy: str, product_image: bytes,
                   *, brand: str = "", site_name: str = "",
                   stars: float | None = None) -> bytes | None:
    """アイキャッチPNG(bytes)を生成。フォント無し/失敗時は None。

    style は config.eyecatch.style: amaviser(既定) | card。
    """
    font_path = _find_font_path()
    if not font_path or not (catch_copy or "").strip():
        return None
    try:
        cfg = get_rules().get("eyecatch", {})
        style = cfg.get("style", "amaviser")
        if style == "amaviser":
            canvas = _render_amaviser(font_path, cfg, catch_copy, product_image, brand, site_name)
        else:
            canvas = _render_card(font_path, cfg, catch_copy, product_image,
                                  brand, site_name, stars)
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _render_amaviser(font_path, cfg, catch_copy, product_image, brand, site_name):
    """amaviser風: 商品を薄く敷く＋ブランド名を特大＋下にキャッチコピー。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    # amaviser風は明るい背景固定（card用のbg_color(暗色)に引っ張られないよう専用キー）
    bg1 = cfg.get("amaviser_bg", "#ffffff")
    bg2 = cfg.get("amaviser_bg2", "#ece9ff")
    canvas = _vertical_gradient(bg1, bg2, CANVAS_W, CANVAS_H).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # 商品背後のソフトな楕円ハロー
    halo = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([CANVAS_W // 2 - 360, 70, CANVAS_W // 2 + 360, 560],
                                 fill=(200, 190, 255, 130))
    canvas.paste(halo.filter(ImageFilter.GaussianBlur(40)), (0, 0),
                 halo.filter(ImageFilter.GaussianBlur(40)))

    # 商品画像を中央に（やや薄く＝白ベール）
    if product_image:
        try:
            prod = Image.open(io.BytesIO(product_image)).convert("RGBA")
            prod.thumbnail((520, 400))
            veil = Image.new("RGBA", prod.size, (255, 255, 255, 70))
            prod = Image.alpha_composite(prod, veil)
            px = (CANVAS_W - prod.width) // 2
            py = (CANVAS_H - prod.height) // 2 - 35
            canvas.paste(prod, (px, py), prod)
        except Exception:  # noqa: BLE001
            pass

    # ブランド名を特大・中央（白フチで可読性確保）
    if brand:
        bf, _ = _fit_single(font_path, brand, CANVAS_W - 140, draw,
                            max_size=210, min_size=90)
        bw = draw.textlength(brand, font=bf)
        bbox = draw.textbbox((0, 0), brand, font=bf)
        bh = bbox[3] - bbox[1]
        bx = (CANVAS_W - bw) / 2
        by = (CANVAS_H - bh) / 2 - 60
        draw.text((bx, by), brand, font=bf, fill="#141414",
                  stroke_width=10, stroke_fill="#ffffff")

    # 下部にキャッチコピー（白フチ）
    cf, lines, line_h = _fit_font(font_path, catch_copy, CANVAS_W - 160, 130, draw,
                                  max_size=40, min_size=28)
    cy = CANVAS_H - len(lines) * line_h - 30
    for ln in lines:
        lw = draw.textlength(ln, font=cf)
        draw.text(((CANVAS_W - lw) / 2, cy), ln, font=cf, fill="#23204a",
                  stroke_width=3, stroke_fill="#ffffff")
        cy += line_h

    # 右上に小さくサイト名
    if site_name:
        sf = ImageFont.truetype(font_path, 24)
        sw = draw.textlength(site_name, font=sf)
        draw.text((CANVAS_W - sw - 30, 24), site_name, font=sf, fill="#6b6b8a")
    return canvas


def _render_card(font_path, cfg, catch_copy, product_image, brand, site_name, stars):
    """カード型: 左にコピー＋右に商品カード＋「どこの国？」バッジ＋星評価。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    bg1 = cfg.get("bg_color", "#0d1b2a")
    bg2 = cfg.get("bg_color2", "#16324f")
    accent = cfg.get("accent_color", "#ff9900")
    text_color = cfg.get("text_color", "#ffffff")

    canvas = _vertical_gradient(bg1, bg2, CANVAS_W, CANVAS_H)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, 16, CANVAS_H], fill=accent)

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
            blurred = shadow.filter(ImageFilter.GaussianBlur(12))
            canvas.paste(blurred, (0, 0), blurred)
            panel = _rounded((cw, ch), 24, (255, 255, 255, 255))
            canvas.paste(panel, (px, py), panel)
            canvas.paste(prod, (px + pad, py + pad), prod)
        except Exception:  # noqa: BLE001
            pass

    margin_x = 70
    if brand:
        bf = ImageFont.truetype(font_path, 30)
        btxt = f"{brand}はどこの国？"
        bw = int(draw.textlength(btxt, font=bf))
        badge = _rounded((bw + 44, 56), 28, _hex(accent) + (255,))
        canvas.paste(badge, (margin_x, 70), badge)
        draw.text((margin_x + 22, 81), btxt, font=bf, fill="#111111")

    font, lines, line_h = _fit_font(font_path, catch_copy, 560, 300, draw)
    y = 165
    for ln in lines:
        draw.text((margin_x, y), ln, font=font, fill=text_color)
        y += line_h
    if stars:
        sf = ImageFont.truetype(font_path, 40)
        draw.text((margin_x, y + 8), f"{_star_glyphs(stars)}  {stars}/5.0",
                  font=sf, fill="#ffd166")
    if site_name:
        sf2 = ImageFont.truetype(font_path, 28)
        draw.text((margin_x + 2, CANVAS_H - 64), site_name, font=sf2, fill="#ffffff")
    return canvas
