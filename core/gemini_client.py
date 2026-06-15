"""Gemini（Google AI Studio 無料枠）クライアント。
複数APIキー対応：プライマリが枠切れ(429)になったら次のキーへ自動フォールバック。"""
from __future__ import annotations

import time

from .config import get_settings

# 無料枠のRPM(1分あたりリクエスト)上限に当たらないための最小間隔(秒)
_MIN_INTERVAL = 4.5


def _is_quota(e: Exception) -> bool:
    m = str(e)
    return "429" in m or "RESOURCE_EXHAUSTED" in m


class GeminiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._keys = list(self.settings.gemini_api_keys)
        self._ki = 0  # 現在使用中のキーindex（429で前進し戻らない）
        self._clients: dict[str, object] = {}
        self._last_call = 0.0

    def _client(self, key: str):
        if key not in self._clients:
            from google import genai  # 遅延import

            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def _generate(self, prompt: str, config) -> str:
        """キーをローテーションしながら生成。各キーで429なら次キーへ。"""
        if not self._keys:
            raise RuntimeError("GEMINI_API_KEY が未設定です。.env に設定してください。")
        last_err: Exception | None = None
        while self._ki < len(self._keys):
            client = self._client(self._keys[self._ki])
            for attempt in range(3):
                self._throttle()
                try:
                    resp = client.models.generate_content(
                        model=self.settings.gemini_model, contents=prompt, config=config)
                    return (resp.text or "").strip()
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if _is_quota(e):
                        break  # このキーは枠切れ → 次のキーへ
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))  # 一時エラーは指数バックオフ
            self._ki += 1  # 次のキーへ前進
        n = len(self._keys)
        raise RuntimeError(f"Gemini生成に失敗しました（{n}キーすべて枠切れ/失敗）: {last_err}")

    def generate(self, prompt: str, *, temperature: float = 0.8, max_retries: int = 3) -> str:
        return self._generate(prompt, {"temperature": temperature})

    def generate_grounded(self, prompt: str, *, temperature: float = 0.4,
                          max_retries: int = 2) -> str:
        """Google検索グラウンディング付き生成（Issue #15）。

        グラウンディング不可（SDK/モデル非対応・全キー枠切れ等）は通常生成にフォールバック。
        """
        try:
            from google.genai import types

            tool = types.Tool(google_search=types.GoogleSearch())
            config = types.GenerateContentConfig(temperature=temperature, tools=[tool])
            return self._generate(prompt, config)
        except Exception:  # noqa: BLE001
            return self.generate(prompt, temperature=temperature)
