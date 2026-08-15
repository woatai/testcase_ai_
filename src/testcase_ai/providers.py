"""封装 OpenAI-compatible Embedding 与 Chat Completions HTTP 接口。"""

from __future__ import annotations

import json
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """模型服务未配置、请求失败或响应格式异常时抛出的统一异常。"""

    pass


def _endpoint(base_url: str, path: str) -> str:
    """安全拼接服务根地址与接口路径，避免重复或缺失斜杠。"""

    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


class OpenAICompatibleEmbeddingProvider:
    """调用 ``/embeddings``，把一批文本转换成顺序一致的浮点向量。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        """保存 Embedding 服务地址、鉴权信息、模型名和请求超时。"""

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成向量，并校验响应数量与输入文本数量严格一致。"""

        if not self.model:
            raise ProviderError("embedding model is not configured")
        if not texts:
            return []
        try:
            response = httpx.post(
                _endpoint(self.base_url, "embeddings"),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            ordered = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"embedding request failed: {exc}") from exc
        if len(vectors) != len(texts) or not vectors:
            raise ProviderError(
                f"embedding response count mismatch: expected={len(texts)} actual={len(vectors)}"
            )
        return vectors


class OpenAICompatibleGenerationProvider:
    """调用 ``/chat/completions``，要求模型按给定 JSON Schema 返回对象。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 180.0,
    ) -> None:
        """保存生成服务地址、鉴权信息、模型名和请求超时。"""

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete_json(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        """请求结构化 JSON；服务不支持 json_schema 时自动降级为 json_object。"""

        if not self.model:
            raise ProviderError("generation model is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "testcase_generation", "strict": True, "schema": schema},
            },
        }
        response = self._post(payload)
        if response.status_code in {400, 404, 422}:
            payload["response_format"] = {"type": "json_object"}
            response = self._post(payload)
        try:
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"generation request failed: {exc}") from exc

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """发送一次 Chat Completions 请求，并把网络异常转换成 ProviderError。"""

        try:
            return httpx.post(
                _endpoint(self.base_url, "chat/completions"),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"generation request failed: {exc}") from exc


def parse_json_object(raw: str) -> dict[str, Any]:
    """去除模型可能附加的 Markdown 代码围栏，并严格解析顶层 JSON 对象。"""

    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1] if "\n" in value else value
        value = value.rsplit("```", 1)[0].strip()
        if value.startswith("json"):
            value = value[4:].lstrip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("model response must be a JSON object")
    return parsed
