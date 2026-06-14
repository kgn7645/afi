"""パイプラインで受け渡すデータ構造。"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class Product(BaseModel):
    """B作業: 基本情報整理で確定する商品データ。"""

    source_url: str = Field("", description="参照元のAmazon商品ページURL")
    brand: str = Field("", description="メーカー名（ブランド名）")
    category: str = Field("", description="大カテゴリー名 例: DCモーター扇風機")
    model_number: str = Field("", description="品番/型番/ASIN")
    product_name: str = Field("", description="商品名")
    price: Optional[int] = Field(None, description="価格(円)")
    in_stock: bool = Field(True, description="在庫あり")
    specs: list[str] = Field(default_factory=list, description="商品スペック箇条書き")
    company_hint: str = Field("", description="企業情報のヒント（任意・誤生成防止用）")

    @property
    def full_name(self) -> str:
        """商品名 メーカー名/カテゴリー名/品番（シートの自動連結列に相当）。

        品番がASIN（10桁英数字）の場合は見出しに出すと不自然なので除外する。
        """
        model = "" if _ASIN_RE.match(self.model_number) else self.model_number
        parts = [p for p in (self.brand, self.category, model) if p]
        return " ".join(parts) if parts else (self.product_name or self.brand)


class TrustRating(BaseModel):
    axis: str
    stars: float
    comment: str = ""


class Article(BaseModel):
    """C/E作業で生成される記事一式。"""

    title: str = ""                     # 記事タイトル
    catch_copy: str = ""                # アイキャッチ キャッチコピー
    meta_description: str = ""          # メタディスクリプション
    meta_keywords: list[str] = Field(default_factory=list)
    body_html: str = ""                 # WordPress投入用HTML本文
    affiliate_click_url: str = ""       # note用などのプレーンな成果リンク(af.moshimo.com)
    product_image_urls: list[str] = Field(default_factory=list)  # 商品画像URL(楽天)。note埋め込み用
    trust_total: Optional[float] = None # 総合信頼度（★）
    raw_sections: dict = Field(default_factory=dict)  # デバッグ用の生成中間物


class PipelineResult(BaseModel):
    product: Product
    article: Optional[Article] = None
    selection_ok: bool = True
    selection_reason: str = ""
    wp_post_id: Optional[int] = None
    wp_edit_link: str = ""
    warnings: list[str] = Field(default_factory=list)
