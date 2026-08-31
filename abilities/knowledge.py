"""
WIS Knowledge Ability - Web search and information retrieval.
Habilidad de conocimiento: busqueda web y recuperacion de informacion.
========================================
Usa la DuckDuckGo Instant Answer API (sin clave) para responder preguntas
y devolver resumenes rapidos sobre temas generales.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .base import Ability


class KnowledgeAbility(Ability):
    """
    Habilidad de busqueda web ligera.

    Notas:
      - Usa la Instant Answer API de DuckDuckGo (https://duckduckgo.com/ia)
        que no requiere API key.
      - Requiere el paquete 'requests'.
      - Toda la red se hace en un hilo aparte para no bloquear el event loop.
    """

    IA_URL = "https://api.duckduckgo.com/"
    # User-Agent para evitar bloqueos simples del endpoint
    USER_AGENT = "WIS/1.0 (robot cognitive agent)"

    def __init__(self, timeout: int = 10):
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # Metadatos requeridos por Ability
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "knowledge"

    @property
    def description(self) -> str:
        return "Web search and quick fact lookup via DuckDuckGo Instant Answer API."

    @property
    def domain(self) -> str:
        return "knowledge"

    # ------------------------------------------------------------------ #
    # Helpers de red (sincronos, se envuelven en to_thread)
    # ------------------------------------------------------------------ #
    def _import_requests(self):
        """Importa requests de forma segura. Devuelve el modulo o None."""
        try:
            import requests  # type: ignore

            return requests
        except Exception:
            return None

    def _query_ddg(self, query: str) -> dict:
        """
        Llama a la Instant Answer API de DuckDuckGo.
        Devuelve un dict con {abstract, answer, related, source_url}.
        """
        requests = self._import_requests()
        if requests is None:
            return {"error": "requests no instalado"}

        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        headers = {"User-Agent": self.USER_AGENT}

        try:
            resp = requests.get(self.IA_URL, params=params, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return {"error": f"fallo de red: {exc}"}

        # Construir respuesta consolidada
        abstract = (data.get("AbstractText") or "").strip()
        abstract_source = (data.get("AbstractURL") or "").strip()
        answer = (data.get("Answer") or "").strip()
        definition = (data.get("Definition") or "").strip()

        # Temas relacionados (RelatedTopics) -> lista ligera
        related = []
        for item in (data.get("RelatedTopics") or [])[:5]:
            if isinstance(item, dict):
                text = (item.get("Text") or "").strip()
                first_url = (item.get("FirstURL") or "").strip()
                if text:
                    related.append({"text": text[:220], "url": first_url})

        return {
            "abstract": abstract,
            "abstract_source": abstract_source,
            "answer": answer,
            "definition": definition,
            "related": related,
        }

    # ------------------------------------------------------------------ #
    # Ejecucion de acciones
    # ------------------------------------------------------------------ #
    async def execute(self, action: str, params: dict) -> dict:
        """
        Acciones soportadas:
          - search: busqueda web, devuelve abstracto + temas relacionados.
          - lookup: hecho rapido (intenta Answer/Definition primero).
        """
        action = (action or "").lower().strip()

        if action in ("search", "web_search"):
            return await self._action_search(params)
        if action in ("lookup", "fact_lookup", "quick_lookup"):
            return await self._action_lookup(params)

        return {
            "success": False,
            "data": None,
            "message": f"Accion de conocimiento no reconocida: '{action}'.",
        }

    async def _action_search(self, params: dict) -> dict:
        """Realiza una busqueda web y devuelve un resumen estructurado."""
        query = str(params.get("query", "")).strip()
        if not query:
            return {"success": False, "data": None, "message": "Query vacia."}

        result = await asyncio.to_thread(self._query_ddg, query)

        if "error" in result:
            return {"success": False, "data": None, "message": result["error"]}

        # Mensaje legible para el agente
        summary = result.get("abstract") or result.get("answer") or ""
        if not summary:
            msg = f"Sin resultado claro para '{query}'. Temas relacionados disponibles."
        else:
            msg = summary[:300]

        return {
            "success": True,
            "data": result,
            "message": msg,
        }

    async def _action_lookup(self, params: dict) -> dict:
        """Devuelve el hecho/definicion mas conciso posible."""
        query = str(params.get("query", "")).strip()
        if not query:
            return {"success": False, "data": None, "message": "Query vacia."}

        result = await asyncio.to_thread(self._query_ddg, query)

        if "error" in result:
            return {"success": False, "data": None, "message": result["error"]}

        # Priorizar respuestas cortas
        fact = result.get("answer") or result.get("definition") or result.get("abstract")
        if not fact:
            return {
                "success": False,
                "data": result,
                "message": f"No se encontro un hecho rapido para '{query}'.",
            }
        return {
            "success": True,
            "data": {"fact": fact, "source": result.get("abstract_source", "")},
            "message": fact[:300],
        }

    # ------------------------------------------------------------------ #
    # Esquema para el LLM
    # ------------------------------------------------------------------ #
    def get_schema(self) -> list:
        return [
            {
                "action": "web_search",
                "description": "Search the web and return an abstract plus related topics.",
                "params": {"query": "string (required) - search query"},
            },
            {
                "action": "lookup",
                "description": "Quick fact lookup; returns the most concise answer available.",
                "params": {"query": "string (required) - fact to look up"},
            },
        ]
