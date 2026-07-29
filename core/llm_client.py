"""WIS LLM Client - LLM HTTP connection and model routing.

Conecta con proveedores de LLM compatibles con la API de OpenAI (DeepSeek, OpenRouter, ZhipuAI).
LLMClient: Cliente HTTP async (httpx) con streaming SSE y reintentos.
ModelRouter: Enruta entre modelo rapido y fuerte segun complejidad.
"""
from __future__ import annotations

import os
import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

try:
    import httpx
    _HTTPX_IMPORT_ERROR = None
except ImportError as _exc:
    httpx = None
    _HTTPX_IMPORT_ERROR = _exc

logger = logging.getLogger("wis.core.llm_client")

DEFAULT_TIMEOUT = (15.0, 60.0)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFFS = (0.5, 1.5, 4.0)
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


class LLMError(RuntimeError):
    """Error base para fallos de conexion LLM."""


class LLMClient:
    """Cliente async para proveedores LLM compatibles con OpenAI."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "deepseek-chat",
        *,
        timeout: Optional[tuple] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoffs: Optional[tuple] = None,
    ) -> None:
        if httpx is None:
            raise LLMError("httpx no esta disponible. Instala con: pip install httpx") from _HTTPX_IMPORT_ERROR

        self.base_url: str = base_url.rstrip("/")
        self.api_key: str = api_key or os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        self.model: str = model
        self.timeout: tuple = timeout or DEFAULT_TIMEOUT
        self.max_retries: int = max(1, int(max_retries))
        self.backoffs: tuple = tuple(backoffs) if backoffs else DEFAULT_BACKOFFS

        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> "httpx.AsyncClient":
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_keepalive_connections=10,
                        max_connections=20,
                        keepalive_expiry=30.0,
                    ),
                )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        client = await self._get_client()

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key and self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"

        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"

        for attempt in range(1, self.max_retries + 1):
            try:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        err_msg = body.decode("utf-8", errors="ignore")
                        logger.warning("HTTP %d on attempt %d: %s", response.status_code, attempt, err_msg)
                        if attempt == self.max_retries:
                            # Tentar fallback automatico con OpenRouter si existe llave
                            openrouter_key = os.environ.get("OPENROUTER_API_KEY")
                            if openrouter_key and "openrouter" not in self.base_url:
                                logger.info("LLMClient: Failing over to OpenRouter API...")
                                self.base_url = "https://openrouter.ai/api/v1"
                                self.api_key = openrouter_key
                                self.model = "deepseek/deepseek-chat"
                                url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
                                headers["Authorization"] = f"Bearer {self.api_key}"
                                payload["model"] = self.model
                                continue
                            raise LLMError(f"HTTP {response.status_code}: {err_msg}")
                        await asyncio.sleep(self.backoffs[min(attempt - 1, len(self.backoffs) - 1)])
                        continue

                    tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                delta = chunk_data["choices"][0].get("delta", {})

                                if "content" in delta and delta["content"]:
                                    yield delta["content"]

                                if "tool_calls" in delta and delta["tool_calls"]:
                                    for tc in delta["tool_calls"]:
                                        idx = tc.get("index", 0)
                                        if idx not in tool_calls_accumulator:
                                            tool_calls_accumulator[idx] = {
                                                "name": "",
                                                "arguments": "",
                                            }
                                        fn_delta = tc.get("function", {})
                                        if "name" in fn_delta and fn_delta["name"]:
                                            tool_calls_accumulator[idx]["name"] += fn_delta["name"]
                                        if "arguments" in fn_delta and fn_delta["arguments"]:
                                            tool_calls_accumulator[idx]["arguments"] += fn_delta["arguments"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue

                    if tool_calls_accumulator:
                        formatted_calls = []
                        for idx, call_data in tool_calls_accumulator.items():
                            args_str = call_data["arguments"]
                            try:
                                args_dict = json.loads(args_str) if args_str else {}
                            except json.JSONDecodeError:
                                args_dict = {"raw": args_str}

                            formatted_calls.append({
                                "name": call_data["name"],
                                "skill": call_data["name"],
                                "arguments": args_dict,
                                "params": args_dict,
                            })

                        yield json.dumps({"calls": formatted_calls})
                    return

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning("Network error on attempt %d: %s", attempt, exc)
                if attempt == self.max_retries:
                    raise LLMError(f"Connection failed: {exc}") from exc
                await asyncio.sleep(self.backoffs[min(attempt - 1, len(self.backoffs) - 1)])


class ModelRouter:
    """Enruta entre un modelo rapido y uno fuerte."""

    def __init__(
        self,
        fast_model: str = "deepseek-chat",
        strong_model: str = "deepseek-chat",
    ) -> None:
        self.fast_model: str = fast_model
        self.strong_model: str = strong_model

    def route(self, user_input: str, has_tools: bool = False) -> str:
        text = (user_input or "").strip()
        if len(text) > 300 or has_tools:
            return self.strong_model
        return self.fast_model
