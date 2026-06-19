"""プロンプトのA/B/C名前付きプリセット（項目ごとに切替・追加・削除）。

保存形: overrides["prompt_presets"][field] = {"active": name, "items": {name: content}}
プリセット未作成の項目は、従来の単一値(legacy)/既定値を 'デフォルト' として自動シードして見せる。
生成側は active_value(field) を読むだけ（未設定なら従来どおりの解決値にフォールバック）。
"""
from __future__ import annotations

from . import overrides

# 媒体ごとの管理対象フィールド
BLOG_FIELDS = ["og_style_guide", "og_extra", "og_title_format"]
THREADS_FIELDS = ["th_pr_prompt", "th_musing_prompt"]
FIELD_LABELS = {
    "og_style_guide": "文体ガイド",
    "og_extra": "追加指示",
    "og_title_format": "タイトル形式",
    "th_pr_prompt": "PR投稿プロンプト",
    "th_musing_prompt": "つぶやきプロンプト",
}
_VALID = set(FIELD_LABELS)


def _fallbacks() -> dict:
    """プリセット未設定時の値（従来の解決ロジックと一致）。再帰回避のため定数/legacyを直接参照。"""
    from .config import get_rules
    from . import prompts, threads_pipeline
    r = get_rules()
    pr = r.get("prompts", {}) or {}
    th = r.get("threads", {}) or {}
    return {
        "og_style_guide": pr.get("style_guide") or prompts.STYLE_GUIDE_DEFAULT,
        "og_extra": pr.get("extra_instructions", "") or "",
        "og_title_format": pr.get("title_format") or prompts.DEFAULT_TITLE_FORMAT,
        "th_pr_prompt": (th.get("pr_prompt") or "").strip() or threads_pipeline._DEFAULT_PR_PROMPT,
        "th_musing_prompt": (th.get("musing_prompt") or "").strip()
        or threads_pipeline._DEFAULT_MUSING_PROMPT,
    }


def _all() -> dict:
    return overrides.load().get("prompt_presets", {}) or {}


def active_value(field: str) -> str:
    """生成で使う有効値。アクティブなプリセット内容、未設定なら従来の解決値。"""
    s = _all().get(field, {})
    items, act = s.get("items", {}), s.get("active", "")
    if act and act in items:
        return items[act]
    return _fallbacks().get(field, "")


def view(field: str) -> dict:
    """UI用 {field,label,active,names[],content}。未設定なら現行値を 'デフォルト' として見せる。"""
    s = _all().get(field, {})
    items = dict(s.get("items", {}))
    act = s.get("active", "")
    if not items:
        items = {"デフォルト": _fallbacks().get(field, "")}
        act = "デフォルト"
    if act not in items:
        act = next(iter(items))
    return {"field": field, "label": FIELD_LABELS.get(field, field),
            "active": act, "names": list(items.keys()), "content": items[act]}


def views(fields: list) -> list:
    return [view(f) for f in fields]


def _mutate(field: str, fn) -> bool:
    if field not in _VALID:
        return False
    data = overrides.load(force=True)
    pp = data.setdefault("prompt_presets", {})
    s = pp.setdefault(field, {"active": "", "items": {}})
    if not s.get("items"):                       # 未シードなら現行値を 'デフォルト' で確定（消失防止）
        s["items"] = {"デフォルト": _fallbacks().get(field, "")}
        s["active"] = "デフォルト"
    fn(s)
    return overrides.save(data)


def save_content(field: str, name: str, content: str) -> bool:
    name = (name or "").strip() or "デフォルト"

    def fn(s):
        s["items"][name] = content
        s["active"] = name
    return _mutate(field, fn)


def add(field: str, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False

    def fn(s):
        if name not in s["items"]:
            s["items"][name] = s["items"].get(s.get("active"), "")   # 現アクティブを複製＝試しやすい
        s["active"] = name
    return _mutate(field, fn)


def set_active(field: str, name: str) -> bool:
    def fn(s):
        if name in s["items"]:
            s["active"] = name
    return _mutate(field, fn)


def delete(field: str, name: str) -> bool:
    def fn(s):
        if name in s["items"] and len(s["items"]) > 1:    # 最後の1つは消さない
            s["items"].pop(name)
            if s.get("active") == name:
                s["active"] = next(iter(s["items"]))
    return _mutate(field, fn)
