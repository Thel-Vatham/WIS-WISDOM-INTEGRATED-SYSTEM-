"""
WIS Browser Ability - Automatizacion de navegador via Playwright.
=================================================================
Mantiene un navegador y un contexto Playwright reutilizables en un hilo
dedicado.  Soporta: goto, click, click_text, click_box, type, type_text,
search, observe, play_media, scroll, wait_for, press, get_html, smart, close.
"""
from __future__ import annotations

import atexit
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote_plus

from .base import Ability

logger = logging.getLogger("wis.abilities.browser")

DOMAIN_RE = re.compile(
    r"\b((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s\"']*)?)",
    re.IGNORECASE,
)

_PLAYWRIGHT_OK = False
PlaywrightTimeoutError = Exception
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except ImportError:
    logger.warning("playwright no instalado. BrowserAbility deshabilitada.")

if _PLAYWRIGHT_OK:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        try:
            from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError
        except ImportError:
            PlaywrightTimeoutError = Exception


# ---------------------------------------------------------------------------
# BrowserManager — Singleton con worker thread dedicado
# ---------------------------------------------------------------------------

class _BrowserManager:
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.pw = None
        self.context = None
        self.page = None
        self.task_queue: queue.Queue = queue.Queue()
        self._shutdown_requested = False
        self._lifecycle = {
            "created_at": time.time(), "last_event": "created",
            "last_error": "", "drop_refs": 0, "close_count": 0,
            "restart_count": 0, "transport_errors_suppressed": 0,
        }
        self.worker_thread = threading.Thread(
            target=self._worker_loop, name="WIS-Browser-Worker", daemon=True)
        self.worker_thread.start()
        atexit.register(self.shutdown)

    @classmethod
    def get_instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ---- Worker loop -------------------------------------------------------
    def _worker_loop(self):
        while True:
            try:
                task = self.task_queue.get()
                if task is None:
                    self.task_queue.task_done()
                    break
                func, args, kwargs, res_queue = task
                try:
                    res = func(*args, **kwargs)
                    res_queue.put((True, res))
                except Exception as e:
                    res_queue.put((False, e))
                finally:
                    self.task_queue.task_done()
            except Exception as e:
                logger.error(f"Error en worker browser: {e}")

    def run_task(self, func, *args, timeout=None, **kwargs):
        if self._shutdown_requested:
            raise RuntimeError("BrowserManager is shutting down")
        res_queue: queue.Queue = queue.Queue()
        self.task_queue.put((func, args, kwargs, res_queue))
        success, val = res_queue.get(timeout=timeout)
        if success:
            return val
        raise val

    # ---- Lifecycle ---------------------------------------------------------
    def _is_transport_error(self, exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(t in text for t in (
            "epipe", "broken pipe", "connection reset", "pipe closed",
            "target closed", "context closed", "browser has been closed",
        ))

    def _safe_call(self, label: str, func):
        try:
            return func()
        except Exception as exc:
            if self._is_transport_error(exc):
                self._lifecycle["transport_errors_suppressed"] += 1
                return None
            logger.warning(f"BrowserManager {label}: {exc}")
            return None

    def initialize(self, headless=False):
        if self.page and self.context:
            try:
                active_pages = []
                if self.context:
                    for p in list(getattr(self.context, "pages", []) or []):
                        try:
                            if not p.is_closed():
                                active_pages.append(p)
                        except Exception:
                            continue
                    if active_pages:
                        self.page = active_pages[-1]
                        return self.page
                if self.page:
                    try:
                        if not self.page.is_closed():
                            return self.page
                    except Exception:
                        pass
            except Exception:
                pass
            self._drop_refs("dead_context")

        logger.info(f"Initializing Playwright (headless={headless})...")
        self.pw = sync_playwright().start()
        profile = Path("Data/BrowserProfile")
        profile.mkdir(parents=True, exist_ok=True)
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
        self.context = self.pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=headless,
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True, user_agent=ua,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        def _handle_dialog(dialog):
            try:
                dialog.accept()
            except Exception:
                pass

        self.context.on("page", lambda p: p.on("dialog", _handle_dialog))
        self.page = (self.context.pages[0] if self.context.pages
                     else self.context.new_page())
        self.page.on("dialog", _handle_dialog)
        return self.page

    def _drop_refs(self, reason="reset"):
        self._lifecycle["drop_refs"] += 1
        self._lifecycle["restart_count"] += 1
        if self.context:
            self._safe_call("ctx.close", self.context.close)
        if self.pw:
            self._safe_call("pw.stop", self.pw.stop)
        self.page = self.context = self.pw = None

    def _close_internal(self):
        self._lifecycle["close_count"] += 1
        ctx, pw = self.context, self.pw
        if ctx:
            pages = []
            try:
                pages = list(ctx.pages)
            except Exception:
                pass
            for p in pages:
                try:
                    if not p.is_closed():
                        self._safe_call("page.close", lambda p=p: p.close(run_before_unload=False))
                except Exception:
                    pass
            self._safe_call("ctx.close", ctx.close)
        if pw:
            self._safe_call("pw.stop", pw.stop)
        self.page = self.context = self.pw = None

    def close(self, timeout=5):
        if threading.current_thread() is self.worker_thread:
            return self._close_internal()
        try:
            return self.run_task(self._close_internal, timeout=timeout)
        except Exception:
            self.page = self.context = self.pw = None

    def shutdown(self, timeout=8):
        if self._shutdown_requested:
            return
        try:
            self.close(timeout=timeout)
        finally:
            self._shutdown_requested = True
            self.page = self.context = self.pw = None
            try:
                self.task_queue.put_nowait(None)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# BrowserAbility — Adaptador WIS
# ---------------------------------------------------------------------------

class BrowserAbility(Ability):
    """Automatizacion del navegador via Playwright."""

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return ("Navegacion web completa: abrir URLs, clicks, escribir en campos, "
                "buscar, scraping, reproducir media, observar elementos interactivos.")

    @property
    def domain(self) -> str:
        return "browser"

    def get_schema(self) -> list:
        return [
            {"action": "goto", "description": "Navegar a una URL", "params": {"url": "string"}},
            {"action": "click", "description": "Clic en selector CSS", "params": {"selector": "string"}},
            {"action": "click_text", "description": "Clic en texto visible", "params": {"target": "string"}},
            {"action": "click_box", "description": "Clic en ID de observe", "params": {"box": "int"}},
            {"action": "type", "description": "Escribir en selector", "params": {"selector": "string", "text": "string"}},
            {"action": "type_text", "description": "Escribir en campo por label/placeholder", "params": {"target": "string", "text": "string", "submit": "bool"}},
            {"action": "search", "description": "Buscar en la web", "params": {"query": "string", "site": "string?"}},
            {"action": "observe", "description": "Listar elementos interactivos visibles"},
            {"action": "play_media", "description": "Reproducir video/audio", "params": {"target": "string?"}},
            {"action": "scroll", "description": "Desplazar pagina", "params": {"direction": "up|down", "amount": "int"}},
            {"action": "wait_for", "description": "Esperar selector o texto", "params": {"selector": "string?", "text": "string?", "timeout": "int"}},
            {"action": "press", "description": "Enviar tecla", "params": {"key": "string"}},
            {"action": "get_html", "description": "Obtener HTML de la pagina"},
            {"action": "smart", "description": "Objetivo complejo multi-paso", "params": {"objective": "string"}},
            {"action": "close", "description": "Cerrar el navegador"},
        ]

    async def execute(self, action: str, params: dict) -> dict:
        if not _PLAYWRIGHT_OK:
            return {"success": False, "data": None,
                    "message": "playwright no instalado. Instala con: pip install playwright && playwright install chromium"}

        import asyncio
        bm = _BrowserManager.get_instance()
        try:
            result = await asyncio.to_thread(
                bm.run_task, self._execute_internal, action, params)
            return {"success": True, "data": result, "message": str(result)}
        except Exception as e:
            return {"success": False, "data": None, "message": f"Error browser: {e}"}

    def _execute_internal(self, action: str, params: dict) -> str:
        action = (action or "").lower().strip()
        if not action:
            return "[ERROR] Falta el parametro 'action'."

        bm = _BrowserManager.get_instance()

        if action == "close":
            bm.close()
            return "[OK] Navegador cerrado."

        try:
            headless = not bool(params.get("visible", False))
            page = bm.initialize(headless=headless)

            if action == "goto":
                url = params.get("url")
                if not url:
                    return "[ERROR] Falta 'url'."
                url = self._normalize_url(url)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                title = page.title()
                content = page.evaluate("() => document.body.innerText.substring(0, 500)")
                return f"[OK] Navegado a {url}. Titulo: '{title}'. Contenido: {content}..."

            elif action == "click":
                sel = self._normalize_selector(params.get("selector", ""))
                if not sel:
                    return "[ERROR] Falta 'selector'."
                page.locator(sel).first.click(timeout=10000)
                return f"[OK] Clic en '{sel}'."

            elif action == "click_text":
                return self._click_by_text(page, str(params.get("target") or params.get("text") or ""))

            elif action == "click_box":
                box_id = str(params.get("box") or params.get("id") or params.get("box_id") or "")
                loc = page.locator(f"[data-wis-id='{box_id}']")
                return self._click_locator(loc, f"Box {box_id}", page) or f"[ERROR] Box '{box_id}' no encontrado."

            elif action == "type":
                sel = self._normalize_selector(params.get("selector", ""))
                text = params.get("text", "")
                if not sel or text is None:
                    return "[ERROR] Faltan 'selector' o 'text'."
                page.locator(sel).first.fill(str(text), timeout=10000)
                return f"[OK] Texto ingresado en '{sel}'."

            elif action == "type_text":
                text = params.get("text")
                if text is None:
                    return "[ERROR] Falta 'text'."
                target = params.get("target") or params.get("selector")
                return self._type_by_target(page, target, str(text), bool(params.get("submit", False)))

            elif action == "search":
                query = params.get("query") or params.get("text")
                if not query:
                    return "[ERROR] Falta 'query'."
                return self._search(page, str(query), params.get("site"))

            elif action == "observe":
                return self._observe(page)

            elif action == "play_media":
                return self._play_media(page, params.get("target"))

            elif action == "scroll":
                direction = str(params.get("direction", "down")).lower()
                amount = int(params.get("amount", 500))
                if direction == "up":
                    amount = -abs(amount)
                page.evaluate(f"window.scrollBy(0, {amount})")
                return f"[OK] Desplazado {abs(amount)}px {'arriba' if amount < 0 else 'abajo'}."

            elif action == "wait_for":
                sel = self._normalize_selector(params.get("selector", ""))
                text = params.get("text")
                timeout = int(params.get("timeout", 10000))
                if sel:
                    page.wait_for_selector(sel, timeout=timeout)
                    return f"[OK] Elemento '{sel}' visible."
                elif text:
                    page.wait_for_function(
                        f"() => document.body.innerText.includes({repr(text)})", timeout=timeout)
                    return f"[OK] Texto '{text}' encontrado."
                return "[ERROR] Falta 'selector' o 'text'."

            elif action == "press":
                key = params.get("key")
                if not key:
                    return "[ERROR] Falta 'key'."
                page.keyboard.press(key)
                if key == "Enter":
                    self._settle(page)
                    content = page.evaluate("() => document.body.innerText.substring(0, 800)")
                    return f"[OK] Enter enviado. Resultados: {content}..."
                return f"[OK] Tecla '{key}' enviada."

            elif action == "get_html":
                return page.content()

            elif action == "smart":
                objective = params.get("objective") or params.get("query") or params.get("text")
                if not objective:
                    return "[ERROR] Falta 'objective'."
                return self._smart_goal(page, str(objective))

            return f"[ERROR] Accion '{action}' no soportada."

        except PlaywrightTimeoutError:
            return "[ERROR] Timeout — el elemento no aparecio o la pagina tardo mucho."
        except Exception as e:
            err = str(e)
            if any(kw in err.lower() for kw in ('closed', 'target', 'context')):
                logger.warning(f"Contexto muerto, reiniciando ({action})...")
                bm._drop_refs(f"self_heal:{action}")
                try:
                    page2 = bm.initialize(headless=not bool(params.get("visible", False)))
                    if action == "goto":
                        url = self._normalize_url(str(params.get("url", "")))
                        page2.goto(url, timeout=30000, wait_until="domcontentloaded")
                        return f"[OK] Navegado a {url} (tras reinicio). Titulo: '{page2.title()}'."
                    return f"[INFO] Sesion reiniciada. Reintenta '{action}'."
                except Exception as e2:
                    return f"[ERROR] Fallo persistente: {e2}"
            return f"[ERROR] Browser: {err}"

    # ---- Helpers -----------------------------------------------------------
    def _normalize_url(self, url: str) -> str:
        url = str(url).strip().strip('"')
        if url.startswith(("http://", "https://", "file://")):
            return url
        candidate = Path(url)
        if candidate.exists():
            return candidate.resolve().as_uri()
        return "https://" + url

    def _normalize_selector(self, sel: str) -> str:
        if not sel:
            return sel
        sel = str(sel).strip()
        if sel.startswith("#") and ":" in sel and not sel.startswith("#\\"):
            return f'[id="{sel[1:]}"]'
        return sel

    def _settle(self, page, timeout=5000):
        for state in ("domcontentloaded", "load"):
            try:
                page.wait_for_load_state(state, timeout=timeout)
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass

    def _page_brief(self, page) -> str:
        try:
            title = page.title()
        except Exception:
            title = ""
        try:
            text = page.evaluate("() => document.body ? document.body.innerText.substring(0, 600) : ''")
        except Exception:
            text = ""
        return f"URL: {page.url}\nTitulo: {title}\nTexto: {text}..."

    def _click_locator(self, locator, label: str, page) -> str | None:
        try:
            if locator.count() < 1:
                return None
            locator.first.scroll_into_view_if_needed(timeout=3000)
            locator.first.click(timeout=7000)
            self._settle(page)
            return f"[OK] Clic en {label}.\n{self._page_brief(page)}"
        except Exception:
            return None

    def _click_by_text(self, page, target: str) -> str:
        if not target.strip():
            return "[ERROR] Falta texto objetivo."
        pattern = re.compile(re.escape(target), re.IGNORECASE)
        for locator, label in [
            (page.get_by_role("link", name=pattern), f"link '{target}'"),
            (page.get_by_role("button", name=pattern), f"boton '{target}'"),
            (page.get_by_role("menuitem", name=pattern), f"menu '{target}'"),
            (page.get_by_role("tab", name=pattern), f"tab '{target}'"),
            (page.get_by_text(pattern), f"texto '{target}'"),
        ]:
            result = self._click_locator(locator, label, page)
            if result:
                return result
        return f"[ERROR] No encontre '{target}'.\n{self._observe(page)}"

    def _type_by_target(self, page, target, text: str, submit: bool = False) -> str:
        locators = []
        target = str(target or "").strip()
        if target and target.startswith(("#", ".", "[", "input", "textarea")):
            locators.append((page.locator(target), target))
        if target:
            pat = re.compile(re.escape(target), re.IGNORECASE)
            locators.extend([
                (page.get_by_label(pat), f"label '{target}'"),
                (page.get_by_placeholder(pat), f"placeholder '{target}'"),
                (page.get_by_role("searchbox", name=pat), f"searchbox '{target}'"),
                (page.get_by_role("textbox", name=pat), f"textbox '{target}'"),
            ])
        locators.extend([
            (page.locator("input[type='search']"), "search input"),
            (page.locator("textarea, input:not([type='hidden']):not([disabled]), [contenteditable=true]"), "primer campo editable"),
        ])
        for loc, label in locators:
            try:
                if loc.count() < 1:
                    continue
                field = loc.first
                field.scroll_into_view_if_needed(timeout=3000)
                field.fill(text, timeout=7000)
                if submit:
                    field.press("Enter", timeout=3000)
                    self._settle(page, timeout=8000)
                return f"[OK] Texto en {label}: '{text}'.\n{self._page_brief(page)}"
            except Exception:
                continue
        return f"[ERROR] No encontre campo editable.\n{self._observe(page)}"

    def _search(self, page, query: str, site=None) -> str:
        if DOMAIN_RE.fullmatch(query) and " " not in query:
            url = self._normalize_url(query)
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            return f"[OK] Sitio abierto: {url}\n{self._page_brief(page)}"
        if site:
            query = f"site:{site} {query}"
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        
        # Google cookie consent bypass
        try:
            btn = page.locator("button:has-text('Aceptar todo'), button:has-text('Accept all'), button:has-text('I agree'), button:has-text('Acepto')")
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                self._settle(page)
        except Exception:
            pass
            
        return f"[OK] Busqueda: {query}\n{self._page_brief(page)}"

    def _observe(self, page) -> str:
        data = page.evaluate("""() => {
            document.querySelectorAll('.wis-bbox').forEach(el => el.remove());
            const nodes = Array.from(document.querySelectorAll(
                'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=tab],[contenteditable=true],video,audio'
            ));
            let id = 0;
            return nodes.filter(el => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none';
            }).slice(0, 45).map(el => {
                const i = id++;
                const label = [el.innerText, el.value, el.getAttribute('aria-label'),
                    el.getAttribute('placeholder'), el.getAttribute('title'), el.alt, el.href
                ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().slice(0, 140);
                const r = el.getBoundingClientRect();
                const box = document.createElement('div');
                box.className = 'wis-bbox';
                box.style.cssText = `position:absolute;left:${window.scrollX+r.left}px;top:${window.scrollY+r.top}px;width:${r.width}px;height:${r.height}px;border:2px solid #00f0ff;background:rgba(0,240,255,0.08);z-index:9999999;pointer-events:none`;
                const lbl = document.createElement('div');
                lbl.innerText = i.toString();
                lbl.style.cssText = 'position:absolute;background:#00f0ff;color:#000;font-size:11px;font-weight:bold;padding:1px 4px;top:-14px;left:0;border-radius:2px';
                box.appendChild(lbl);
                document.body.appendChild(box);
                el.setAttribute('data-wis-id', i);
                return {i, tag: el.tagName.toLowerCase(), role: el.getAttribute('role')||'', type: el.getAttribute('type')||'', label, x: Math.round(r.x), y: Math.round(r.y)};
            });
        }""")
        lines = [self._page_brief(page), "Elementos interactivos (usa click_box con ID):"]
        for item in data:
            meta = " ".join(x for x in [item.get("tag"), item.get("role"), item.get("type")] if x)
            lines.append(f"  {item.get('i')}: [{meta}] {item.get('label')}")
        return "\n".join(lines)

    def _play_media(self, page, target=None) -> str:
        if target:
            self._click_by_text(page, str(target))
        try:
            r = page.evaluate("""async () => {
                const m = Array.from(document.querySelectorAll('video,audio'));
                let started = 0;
                for (const el of m) { try { el.muted=false; await el.play(); started++; } catch(e){} }
                return {count: m.length, started};
            }""")
            if r.get("started", 0) > 0:
                return f"[OK] Reproduccion iniciada.\n{self._page_brief(page)}"
        except Exception:
            pass
        play_pat = re.compile(r"(play|reproducir|iniciar|ver ahora|watch)", re.IGNORECASE)
        for loc, label in [
            (page.get_by_role("button", name=play_pat), "boton play"),
            (page.get_by_text(play_pat), "texto play"),
        ]:
            result = self._click_locator(loc, label, page)
            if result:
                return result
        return f"[ERROR] No pude iniciar reproduccion.\n{self._observe(page)}"

    def _smart_goal(self, page, objective: str) -> str:
        obj_lower = objective.lower()
        steps = [f"[SMART] Objetivo: {objective}"]
        url = self._extract_url(objective)
        if url:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            self._settle(page)
            steps.append(f"[OK] Abierto: {url}")
        query = self._extract_query(objective)
        if query and any(w in obj_lower for w in ["busca", "search", "encuentra"]):
            typed = self._type_by_target(page, "search", query, submit=True)
            if typed.startswith("[OK]"):
                steps.append(typed)
            elif not url:
                steps.append(self._search(page, query))
        if any(w in obj_lower for w in ["reproduce", "play", "pon", "ver"]):
            steps.append(self._play_media(page))
        if len(steps) == 1:
            steps.append(self._observe(page))
        return "\n".join(steps)

    def _extract_url(self, text: str):
        m = DOMAIN_RE.search(text or "")
        return self._normalize_url(m.group(1).rstrip(".,;:)")) if m else None

    def _extract_query(self, text: str):
        quoted = re.findall(r'["\'\u201c\u201d]([^"\'\u201c\u201d]{2,90})["\'\u201c\u201d]', text or "")
        if quoted:
            return quoted[0].strip()
        m = re.search(r"(?:busca|buscar|search|encuentra)\s+([^,.;]+)", text or "", re.IGNORECASE)
        return m.group(1).strip() if m else None
