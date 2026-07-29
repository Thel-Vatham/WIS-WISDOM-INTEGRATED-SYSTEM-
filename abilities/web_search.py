"""
WIS Web Search Ability - Busqueda web avanzada multi-proveedor.
===============================================================
Prioridad: Tavily API (si hay key) > DuckDuckGo (libreria) > DuckDuckGo HTTP
           > Wikipedia es.

Acciones: search, search_images
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from .base import Ability

logger = logging.getLogger("wis.abilities.web_search")


class WebSearchAbility(Ability):
    """Busqueda web avanzada con multiples proveedores y fallbacks."""

    def __init__(self, timeout: int = 10):
        self._timeout = timeout
        self._tavily_key = os.getenv("TAVILY_API_KEY", "")

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        provider = "Tavily API" if self._tavily_key else "DuckDuckGo"
        return f"Busqueda web avanzada ({provider}) con fallback multi-proveedor."

    @property
    def domain(self) -> str:
        return "knowledge"

    def get_schema(self) -> list:
        return [
            {"action": "search", "description": "Buscar en la web",
             "params": {"query": "string"}},
            {"action": "search_images", "description": "Buscar imagenes",
             "params": {"query": "string"}},
        ]

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "search").lower().strip()
        query = str(params.get("query", "") or params.get("text", "")).strip()
        if not query:
            return {"success": False, "data": None, "message": "Falta la consulta 'query'."}

        try:
            if action == "search_images":
                result = await asyncio.to_thread(self._search_images, query)
            else:
                result = await asyncio.to_thread(self._search, query)
            ok = bool(result and not result.startswith("No encontre"))
            return {"success": ok, "data": result, "message": result}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error busqueda: {exc}"}

    def _search(self, query: str) -> str:
        if self._tavily_key:
            result = self._search_tavily(query)
            if result:
                return result
        result = self._search_duckduckgo(query)
        if result:
            return result
        result = self._search_duckduckgo_http(query)
        if result:
            return result
        result = self._search_wikipedia(query)
        if result:
            return result
        return f"No encontre resultados confiables para: '{query}'"

    def _search_tavily(self, query: str) -> str | None:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._tavily_key)
            response = client.search(
                query=query, search_depth="basic",
                max_results=3, include_answer=True,
            )
            lines = [f"**Busqueda:** {query}\n"]
            if response.get("answer"):
                lines.append(f"**Resumen:** {response['answer']}\n")
            results = response.get("results", [])[:3]
            if results:
                lines.append("**Fuentes:**")
                for item in results:
                    title = item.get("title", "Sin titulo")
                    snippet = item.get("content", "")[:220].strip()
                    url = item.get("url", "")
                    lines.append(f"- **{title}**")
                    if snippet:
                        lines.append(f"  {snippet}...")
                    if url:
                        lines.append(f"  {url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"Tavily error: {exc}")
            return None

    def _search_duckduckgo(self, query: str) -> str | None:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            ddgs = DDGS()
            results = []
            seen = set()
            for item in ddgs.text(query, max_results=10):
                url = item.get("href", "")
                if url in seen:
                    continue
                if url:
                    seen.add(url)
                results.append(item)
                if len(results) >= 3:
                    break
            if not results:
                return None
            lines = [f"**Busqueda:** {query}\n", "**Resultados:**"]
            for item in results:
                title = item.get("title", "Sin titulo")
                body = item.get("body", "")[:220].strip()
                url = item.get("href", "")
                lines.append(f"- **{title}**")
                if body:
                    lines.append(f"  {body}...")
                if url:
                    lines.append(f"  {url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"DuckDuckGo lib error: {exc}")
            return None

    def _search_duckduckgo_http(self, query: str) -> str | None:
        try:
            import urllib.request
            import urllib.parse
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
                "Accept-Language": "es,en;q=0.9",
            })
            html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            urls_raw = re.findall(r'<a[^>]+class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)
            if not urls_raw:
                urls_raw = re.findall(r'uddg=([^&"]+)', html)
            if not titles:
                return None
            lines = [f"**Busqueda:** {query}\n", "**Resultados (DuckDuckGo HTML):**"]
            for i in range(min(len(titles), 3)):
                title = re.sub("<[^<]+?>", "", titles[i]).strip()
                snippet = ""
                if i < len(snippets):
                    snippet = re.sub("<[^<]+?>", "", snippets[i]).strip()[:220]
                link = ""
                if i < len(urls_raw):
                    link = re.sub("<[^<]+?>", "", urls_raw[i]).strip()
                    if link.startswith("http%3A") or "%2F" in link:
                        link = urllib.parse.unquote(link)
                    elif not link.startswith("http"):
                        link = f"https://{link}"
                lines.append(f"- **{title}**")
                if snippet:
                    lines.append(f"  {snippet}...")
                if link:
                    lines.append(f"  {link}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"DuckDuckGo HTTP error: {exc}")
            return None

    def _search_images(self, query: str) -> str:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            ddgs = DDGS()
            results = []
            for item in ddgs.images(query, max_results=6):
                img_url = item.get("image") or item.get("thumbnail")
                if not img_url:
                    continue
                results.append({
                    "title": item.get("title", "Imagen"),
                    "image": img_url,
                    "source": item.get("url", ""),
                })
                if len(results) >= 3:
                    break
            if not results:
                return f"No encontre imagenes para: '{query}'"
            lines = [f"**Imagenes:** {query}\n"]
            for item in results:
                lines.append(f"- **{item['title']}**")
                lines.append(f"  ![]({item['image']})")
                if item["source"]:
                    lines.append(f"  Fuente: {item['source']}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"DuckDuckGo images error: {exc}")
            return f"No encontre imagenes para: '{query}'"

    def _search_wikipedia(self, query: str) -> str | None:
        try:
            import requests
            response = requests.get(
                "https://es.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search",
                        "srsearch": query, "utf8": "", "format": "json"},
                timeout=10,
            )
            response.raise_for_status()
            results = response.json().get("query", {}).get("search", [])
            if not results:
                return None
            lines = [f"**Busqueda (Wikipedia):** {query}\n"]
            for item in results[:3]:
                title = item.get("title", "")
                snippet = re.sub("<[^<]+>", "", item.get("snippet", ""))[:220].strip()
                url = f"https://es.wikipedia.org/wiki/{title.replace(' ', '_')}"
                lines.append(f"- **{title}**")
                if snippet:
                    lines.append(f"  {snippet}...")
                lines.append(f"  {url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"Wikipedia error: {exc}")
            return None
