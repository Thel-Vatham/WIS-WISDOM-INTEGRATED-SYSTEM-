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
from pathlib import Path
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


class LocalLLMClient:
    """Cliente para inferencia local en CPU con modelo TinyLlama-1.1B GGUF.
    Descarga automáticamente el modelo en la carpeta models/ si no existe.
    """

    def __init__(
        self,
        model_filename: str = "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        context_length: int = 2048,
    ) -> None:
        self.models_dir = Path("models")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.models_dir / model_filename
        self.download_url = "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
        self._model = None
        # Contexto del modelo local (TinyLlama soporta hasta 2048).
        self._context_length: int = max(512, int(context_length))

    def ensure_model(self) -> bool:
        """Descarga el modelo GGUF automáticamente en la carpeta models/ si no existe o está incompleto."""
        if self.model_path.exists():
            if self.model_path.stat().st_size < 600 * 1024 * 1024:
                logger.warning("Local GGUF model is incomplete or corrupted (size < 600MB). Deleting...")
                try:
                    self.model_path.unlink()
                except Exception as e:
                    logger.error("Failed to delete corrupted model: %s", e)
                    return False
            else:
                return True
        import urllib.request
        try:
            logger.info("Downloading local CPU model TinyLlama-1.1B (~630MB) to %s...", self.model_path)
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            req = urllib.request.Request(self.download_url, headers=headers)
            with urllib.request.urlopen(req) as resp, open(self.model_path, "wb") as f:
                chunk_size = 1024 * 1024
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
            logger.info("Local GGUF model downloaded successfully.")
            return True
        except Exception as exc:
            logger.error("Failed to download local GGUF model: %s", exc)
            if self.model_path.exists():
                try:
                    self.model_path.unlink()
                except Exception:
                    pass
            return False

    def load_model(self):
        if self._model is not None:
            return self._model
        if not self.ensure_model():
            return None
        try:
            from ctransformers import AutoModelForCausalLM
            self._model = AutoModelForCausalLM.from_pretrained(
                str(self.model_path),
                model_type="llama",
                context_length=self._context_length,
            )
            logger.info("Local CPU LLM TinyLlama-1.1B loaded successfully (context=%d).", self._context_length)
            return self._model
        except Exception as exc:
            logger.error("Error loading local CPU model: %s", exc)
            return None

    @staticmethod
    def _truncate_prompt(prompt: str, max_input_tokens: int) -> str:
        """Trunca el prompt para que quepa en el contexto del modelo.

        Usa una estimacion CONSERVADORA de ~2 caracteres por token. El texto
        real (espanol/ingles con markdown y emojis) tokeniza a ~2.5-3
        chars/token, asi que este factor garantiza que el prompt truncado
        NUNCA exceda el contexto del modelo. Excederlo provoca un abort nativo
        de ctransformers ('exceeded maximum context length') que mata el
        proceso entero de WIS sin traceback.
        Se conserva el FINAL del prompt (instrucciones + pregunta del usuario),
        que es lo esencial para generar una respuesta util.
        """
        prefix = "...[truncated context]...\n"
        max_chars = max(128, int(max_input_tokens) * 2)
        if len(prompt) <= max_chars:
            return prompt
        # Descuenta el prefijo del presupuesto para que el total final
        # (prefijo + cuerpo) nunca supere max_chars.
        body = prompt[-(max_chars - len(prefix)):]
        return prefix + body

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimacion conservadora: a lo sumo 2 caracteres por token.

        Es un LIMITE SUPERIOR (los tokens reales de texto mixto ocupan
        ~2.5-3 chars), por lo que si len/2 cabe, los tokens reales tambien.
        """
        return len(text) // 2

    async def generate(self, prompt: str, max_tokens: int = 150) -> str:
        model = await asyncio.to_thread(self.load_model)
        if not model:
            return ""
        # Contexto real del modelo (ctransformers lo expone tras cargarlo).
        ctx = getattr(model, "context_length", None) or self._context_length
        # Margen de 32 tokens para el prompt + generacion, nunca a tope.
        max_input = max(64, int(ctx) - int(max_tokens) - 32)
        safe_prompt = self._truncate_prompt(prompt, max_input)
        # Defensa en profundidad: si la estimacion realista aun excede, corta
        # de nuevo. Un abort nativo de ctransformers NO se puede capturar con
        # try/except, por lo que la prevencion es la unica proteccion.
        while self._estimate_tokens(safe_prompt) > max_input:
            safe_prompt = self._truncate_prompt(safe_prompt, max_input // 2)
        try:
            res = await asyncio.to_thread(model, safe_prompt, max_new_tokens=max_tokens)
        except Exception as exc:
            logger.error("Local LLM generation failed: %s", exc)
            return ""
        return str(res or "").strip()


class ModelRouter:
    """Enruta entre un modelo rapido (local o cloud) y uno fuerte."""

    def __init__(
        self,
        fast_model: str = "deepseek-chat",
        strong_model: str = "deepseek-chat",
        local_client: Optional[LocalLLMClient] = None,
    ) -> None:
        self.fast_model: str = fast_model
        self.strong_model: str = strong_model
        self.local_client: Optional[LocalLLMClient] = local_client or LocalLLMClient()

    def route(self, user_input: str, has_tools: bool = False) -> str:
        text = (user_input or "").strip()
        if len(text) > 300 or has_tools:
            return self.strong_model
        return self.fast_model

