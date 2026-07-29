"""
WIS Console Server - FastAPI backend for the web interface.
Servidor backend FastAPI para la interfaz web.

Este modulo expone:
  - REST: POST /api/chat, GET /api/state, GET /api/abilities,
          GET /api/memory/facts
  - WebSocket: /ws (streaming bidireccional en tiempo real)
  - Estaticos: montaje de /console -> console/web/

El servidor recibe una referencia al nucleo de WIS (cortex, praxis,
vault, etc.) mediante inyeccion de dependencias a traves de la clase
WISCoreContainer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.event_bus import event_bus

logger = logging.getLogger("wis.console.server")

# Set of active WebSockets
_active_websockets: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# Autenticacion por token (Bearer header para REST, ?token= para WebSocket).
# ---------------------------------------------------------------------------

import hmac  # noqa: E402

# Token de autenticacion de la consola (None = auth desactivada).
_auth_token: Optional[str] = None
# Origenes permitidos por CORS. Por defecto solo same-origin / localhost.
_allowed_cors_origins: List[str] = []


def set_auth_token(token: Optional[str]) -> None:
    """Establece el token requerido para acceder a la API. None desactiva el auth."""
    global _auth_token
    _auth_token = (str(token).strip() if token else None)


def get_auth_token() -> Optional[str]:
    return _auth_token


def set_cors_origins(origins: Optional[List[str]]) -> None:
    """Define la lista de origenes permitidos para CORS."""
    global _allowed_cors_origins
    if not origins:
        _allowed_cors_origins = []
        return
    _allowed_cors_origins = [str(o).strip() for o in origins if str(o).strip()]


def _token_is_valid(provided: Optional[str]) -> bool:
    """True si no hay token configurado (auth off) o si el token coincide (ct-compare)."""
    if not _auth_token:
        return True
    if not provided:
        return False
    return hmac.compare_digest(str(_auth_token), str(provided))


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Dependencia FastAPI: valida el header Authorization: Bearer <token>."""
    if not _auth_token:
        return  # auth desactivada
    token = _extract_bearer(authorization)
    if not _token_is_valid(token):
        raise HTTPException(
            status_code=401,
            detail="Token de autenticacion invalido o ausente.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_ws_token(token: Optional[str]) -> bool:
    """Valida el token de un WebSocket recibido por query param (?token=)."""
    return _token_is_valid(token)


def broadcast_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Retransmite eventos del event_bus a todos los clientes WebSocket."""
    if not _active_websockets:
        return
    msg = json.dumps({"type": "event_bus", "event": event_name, "data": payload}, default=str)
    try:
        loop = asyncio.get_running_loop()
        for ws in list(_active_websockets):
            # Verificar que el WebSocket siga conectado antes de enviar.
            # Esto previene el error 'send after close' cuando un cliente
            # se desconecta pero el bus sigue emitiendo eventos.
            if _ws_is_open(ws):
                loop.create_task(_safe_ws_send(ws, msg))
    except RuntimeError:
        pass


def _ws_is_open(ws: WebSocket) -> bool:
    """Devuelve True si el WebSocket sigue conectado y acepta mensajes."""
    try:
        # Starlette marca el estado en ws.application_state.
        # CONNECTED = 1, DISCONNECTED = 2 (starlette.websockets.protocol.State).
        from starlette.websockets import WebSocketState
        return ws.client_state == WebSocketState.CONNECTED
    except Exception:
        return False


async def _safe_ws_send(ws: WebSocket, msg: str) -> None:
    """Envia un mensaje al WebSocket de forma segura (ignora errores de cierre)."""
    try:
        await ws.send_text(msg)
    except Exception:
        # Conexion cerrada o rota: remover del set activo silenciosamente.
        _active_websockets.discard(ws)


def _on_bus_event(data: Dict[str, Any]) -> None:
    evt = data.get("_event_name", "bus_event")
    broadcast_event(evt, data)

event_bus.subscribe("*", _on_bus_event)

# ---------------------------------------------------------------------------
# Contenedor de dependencias del nucleo de WIS.
# Permite inyectar cortex/praxis/vault sin acoplar el servidor a detalles.
# ---------------------------------------------------------------------------


class WISCoreContainer:
    """Recibe referencias a los componentes del nucleo de WIS.

    Attributes:
        praxis: Objeto con metodo process(message) -> respuesta (str o async).
        cortex: Nucleo cognitivo (memoria / estado).
        vault:  Almacen seguro (opcional).
        abilities: Registro de habilidades disponibles.
        proactivity: Motor de proactividad (opcional).
        aegis: Politica de seguridad (opcional).
    """

    def __init__(
        self,
        praxis: Any = None,
        cortex: Any = None,
        vault: Any = None,
        abilities: Any = None,
        proactivity: Any = None,
        aegis: Any = None,
    ) -> None:
        self.praxis = praxis
        self.cortex = cortex
        self.vault = vault
        self.abilities = abilities
        self.proactivity = proactivity
        self.aegis = aegis


# Contenedor global por defecto; se reemplaza al arrancar la app.
_core: WISCoreContainer = WISCoreContainer()


def set_core(core: WISCoreContainer) -> None:
    """Inyecta el nucleo de WIS en el servidor."""
    global _core
    _core = core


def get_core() -> WISCoreContainer:
    """Devuelve el contenedor del nucleo actual."""
    return _core


# ---------------------------------------------------------------------------
# Modelos de datos (Pydantic) para validar entrada/salida de la API.
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Peticion de chat enviada por el usuario."""

    message: str


class ChatResponse(BaseModel):
    """Respuesta de chat devuelta por WIS."""

    response: str
    calls: List[Any] = []
    results: List[Any] = []
    success: bool = True
    path: Optional[str] = None


# ---------------------------------------------------------------------------
# Utilidades internas.
# ---------------------------------------------------------------------------


async def _maybe_await(value: Any) -> Any:
    """Espera el valor si es una corrutina, sino lo devuelve directo."""
    if asyncio.iscoroutine(value):
        return await value
    return value


def _safe_call(target: Any, attr: str, *args: Any, **kwargs: Any) -> Any:
    """Llama a un atributo del objetivo si existe; devuelve None si no."""
    if target is None:
        return None
    fn = getattr(target, attr, None)
    if fn is None:
        return None
    return fn(*args, **kwargs)


def gather_system_context() -> dict:
    """Recolecta información de presencia ambiental del sistema operativo (apps abiertas, volumen, etc.)"""
    import os
    import platform
    
    context = {}
    context["current_directory"] = os.getcwd()
    
    # 1. Volumen del sistema (usando pycaw / comtypes de manera segura)
    try:
        from ctypes import cast, POINTER
        from comtypes import CoInitialize, CoUninitialize, CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        
        CoInitialize()
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        context["system_volume"] = int(round(volume.GetMasterVolumeLevelScalar() * 100))
        context["system_muted"] = bool(volume.GetMute())
        CoUninitialize()
    except Exception:
        context["system_volume"] = "unknown"
        context["system_muted"] = "unknown"

    # 2. Aplicaciones/Procesos activos (con ventana visible)
    if platform.system() == "Windows":
        try:
            import ctypes
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
            
            visible_windows = []
            
            def foreach_window(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        title = buff.value
                        
                        # Filtrar nombres internos de Windows
                        if title and not any(x in title for x in (
                            "Default IME", "MSCTFIME", "Windows Input Experience", 
                            "Program Manager", "Settings", "Consola de WIS", 
                            "WIS Console", "Task Manager"
                        )):
                            pid = ctypes.c_ulong()
                            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            visible_windows.append(f"{title} (PID: {pid.value})")
                return True
                
            EnumWindows(EnumWindowsProc(foreach_window), 0)
            context["active_windows"] = visible_windows[:15]
        except Exception:
            context["active_windows"] = []
    else:
        context["active_windows"] = []
        
    return context


def _normalize_response(raw: Any) -> Dict[str, Any]:
    """Normaliza la salida de praxis.process() a un diccionario plano.

    Praxis devuelve {response, calls, results, path_used, success}; mapeamos
    path_used -> path para la UI.
    """
    if isinstance(raw, dict):
        return {
            "response": str(raw.get("response", "")),
            "calls": list(raw.get("calls", []) or []),
            "results": list(raw.get("results", []) or []),
            "success": bool(raw.get("success", True)),
            # Praxis usa 'path_used'; aceptamos ambas claves por compatibilidad.
            "path": raw.get("path") or raw.get("path_used"),
        }
    return {"response": str(raw or ""), "calls": [], "results": [], "success": True, "path": None}


def _get_facts(cortex: Any) -> List[str]:
    """Extrae hechos desde cortex.mnemonic.get_facts() (API real de WIS)."""
    if cortex is None:
        return []
    mnemonic = getattr(cortex, "mnemonic", None)
    if mnemonic is None:
        return []
    try:
        fn = getattr(mnemonic, "get_facts", None)
        if fn is None:
            return []
        return [str(f) for f in (fn() or [])]
    except Exception:
        return []


def _abilities_as_list(abilities: Any) -> List[Dict[str, Any]]:
    """Normaliza 'abilities' que puede ser dict, AbilityRegistry o lista."""
    if abilities is None:
        return []
    # Caso AbilityRegistry: expone get_schemas() con metadata rica.
    schemas_fn = getattr(abilities, "get_schemas", None)
    if callable(schemas_fn):
        try:
            return list(schemas_fn() or [])
        except Exception:
            pass
    # Caso AbilityRegistry: expone names() + all().
    names_fn = getattr(abilities, "names", None)
    all_fn = getattr(abilities, "all", None)
    if callable(names_fn) and callable(all_fn):
        try:
            names = list(names_fn() or [])
            mapping = all_fn() or {}
            out = []
            for n in names:
                entry = mapping.get(n) if isinstance(mapping, dict) else None
                out.append(
                    {
                        "name": str(n),
                        "domain": getattr(entry, "domain", ""),
                        "description": getattr(entry, "description", ""),
                    }
                )
            return out
        except Exception:
            pass
    # Caso dict simple (praxis.abilities = {name: fn}).
    if isinstance(abilities, dict):
        return [{"name": str(k)} for k in abilities.keys()]
    # Fallback: iterable generico.
    try:
        return [{"name": str(a)} for a in abilities]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Fabrica de la aplicacion FastAPI.
# ---------------------------------------------------------------------------


def create_app(
    core: Optional[WISCoreContainer] = None,
    auth_token: Optional[str] = None,
    cors_origins: Optional[List[str]] = None,
) -> FastAPI:
    """Crea y configura la aplicacion FastAPI para la consola de WIS.

    Args:
        core: Contenedor del nucleo de WIS.
        auth_token: Si se establece, todos los endpoints /api/* y /ws requieren
                    este token (Bearer header o ?token=). None desactiva el auth.
        cors_origins: Lista de origenes permitidos para CORS. Por defecto ninguno
                      (same-origin); nunca se combina '*' con credenciales.
    """
    if core is not None:
        set_core(core)
    set_auth_token(auth_token)
    set_cors_origins(cors_origins)

    app = FastAPI(
        title="WIS Console",
        description="Servidor web de la consola de WIS.",
        version="1.0.0",
    )

    # CORS restringido: same-origin por defecto. Solo se permiten origenes
    # explicitos; nunca se combina '*' con credenciales (config insegura).
    if _allowed_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_allowed_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )


    # ---------------------------------------------------------------------
    # Endpoints REST.
    # ---------------------------------------------------------------------

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, _: None = Depends(require_auth)) -> ChatResponse:
        """Endpoint principal de chat.

        Recibe {message} y devuelve {response, calls, path}.
        Si praxis soporta streaming, se usa StreamingResponse.
        """
        core = get_core()
        if core.praxis is None:
            raise HTTPException(status_code=503, detail="WIS core no disponible.")

        try:
            # Ruta de streaming: si praxis expone process_stream().
            stream_fn = getattr(core.praxis, "process_stream", None)
            if stream_fn is not None:

                async def _gen() -> Any:
                    # Generador asincrono que propaga los chunks.
                    try:
                        async for chunk in _maybe_await(stream_fn(req.message)):
                            yield f"data: {json.dumps({'chunk': str(chunk)})}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                    except Exception as exc:  # pragma: no cover - defensivo
                        logger.exception("Error en streaming de chat: %s", exc)
                        yield f"data: {json.dumps({'error': str(exc)})}\n\n"

                return StreamingResponse(_gen(), media_type="text/event-stream")

            # Ruta sincrona / asincrona clasica.
            sensor_data = gather_system_context()
            raw = await _maybe_await(core.praxis.process(req.message, sensor_data=sensor_data))
            data = _normalize_response(raw)
            return ChatResponse(**data)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error en /api/chat: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/state")
    async def state(_: None = Depends(require_auth)) -> Dict[str, Any]:
        """Devuelve el estado actual: memoria, vault y habilidades activas."""
        core = get_core()
        memory_stats: Dict[str, Any] = {}
        vault_stats: Dict[str, Any] = {}
        active: List[str] = []

        # Memoria: numero de hechos disponibles en cortex.mnemonic.
        try:
            facts = _get_facts(core.cortex)
            memory_stats = {"facts": len(facts)}
        except Exception:
            memory_stats = {}

        # Vault: estadisticas de skills cacheadas.
        try:
            vault_stats = _safe_call(core.vault, "stats") or {}
        except Exception:
            vault_stats = {}

        # Habilidades activas: nombres desde el registro/dict.
        try:
            active = [str(a.get("skill") or a.get("name")) for a in _abilities_as_list(core.abilities)]
        except Exception:
            active = []

        agent_name = "WIS"
        if core and core.cortex and hasattr(core.cortex, "persona") and core.cortex.persona:
            try:
                agent_name = core.cortex.persona.get_name()
            except Exception:
                pass

        return {
            "agent_name": agent_name,
            "memory": memory_stats,
            "vault": vault_stats,
            "active_abilities": active,
        }

    @app.get("/api/abilities")
    async def abilities(_: None = Depends(require_auth)) -> Dict[str, Any]:
        """Lista las habilidades disponibles y sus esquemas."""
        core = get_core()
        try:
            return {"abilities": _abilities_as_list(core.abilities)}
        except Exception as exc:
            logger.exception("Error en /api/abilities: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/memory/facts")
    async def memory_facts(_: None = Depends(require_auth)) -> Dict[str, Any]:
        """Devuelve la lista de hechos almacenados en memoria."""
        core = get_core()
        try:
            return {"facts": _get_facts(core.cortex)}
        except Exception as exc:
            logger.exception("Error en /api/memory/facts: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/health")
    async def health() -> Dict[str, str]:
        """Comprobacion simple de vida del servidor."""
        return {"status": "ok"}

    # --- Security Mode endpoints ---

    @app.get("/api/security/mode")
    async def get_security_mode(_: None = Depends(require_auth)) -> Dict[str, str]:
        core = get_core()
        mode = "secure"
        if core.aegis:
            mode = core.aegis.mode
        elif core.praxis and hasattr(core.praxis, 'safety'):
            mode = core.praxis.safety.mode
        return {"mode": mode}

    @app.post("/api/security/mode")
    async def set_security_mode(req: Request, _: None = Depends(require_auth)) -> Dict[str, str]:
        body = await req.json()
        new_mode = str(body.get("mode", "secure")).strip().lower()
        core = get_core()
        if core.aegis:
            core.aegis.set_mode(new_mode)
        if core.praxis and hasattr(core.praxis, 'safety'):
            core.praxis.safety.set_mode(new_mode)
        actual = core.aegis.mode if core.aegis else new_mode
        broadcast_event("security.mode_changed", {"mode": actual})
        return {"mode": actual}

    # --- Approval endpoints ---

    @app.post("/api/approve")
    async def approve_action(_: None = Depends(require_auth)) -> Dict[str, bool]:
        core = get_core()
        if core.praxis and hasattr(core.praxis, 'approve_action'):
            core.praxis.approve_action()
        return {"approved": True}

    @app.post("/api/deny")
    async def deny_action(_: None = Depends(require_auth)) -> Dict[str, bool]:
        core = get_core()
        if core.praxis and hasattr(core.praxis, 'deny_action'):
            core.praxis.deny_action()
        return {"denied": True}

    # --- Proactivity endpoints ---

    @app.get("/api/proactivity/inbox")
    async def proactivity_inbox(
        limit: int = 50,
        _: None = Depends(require_auth),
    ) -> Dict[str, Any]:
        core = get_core()
        if not core.proactivity:
            return {"inbox": []}
        items = core.proactivity.inbox(limit=limit)
        return {"inbox": items}

    @app.get("/api/proactivity/rules")
    async def proactivity_rules(_: None = Depends(require_auth)) -> Dict[str, Any]:
        core = get_core()
        if not core.proactivity:
            return {"rules": []}
        return {"rules": core.proactivity.list_rules()}

    @app.get("/api/proactivity/routines")
    async def proactivity_routines(_: None = Depends(require_auth)) -> Dict[str, Any]:
        core = get_core()
        if not core.proactivity:
            return {"routines": []}
        return {"routines": core.proactivity.list_routines()}

    @app.get("/api/auth/bootstrap")
    async def auth_bootstrap(request: Request) -> Dict[str, Any]:
        """Entrega el token de la consola SOLO a clientes loopback (localhost).

        Esto permite que la consola web local obtenga su token automaticamente
        sin exponerlo a origenes externos. Sitios web remotos no seran loopback
        y recibiran 403, por lo que no podran atacar la API.
        """
        client_host = (request.client.host if request.client else "") or ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
        if not is_loopback:
            raise HTTPException(status_code=403, detail="Bootstrap solo permitido desde localhost.")
        return {"token": _auth_token or "", "auth_required": bool(_auth_token)}

    @app.post("/api/listen")
    async def listen_endpoint(_: None = Depends(require_auth)) -> Dict[str, Any]:
        """Escucha 5s en el microfono del hardware local y envia lo transcrito al LLM."""
        core = get_core()
        if not core or core.praxis is None:
            raise HTTPException(status_code=500, detail="WIS core no disponible.")

        listen_ability = None
        if core.abilities and hasattr(core.abilities, "get"):
            listen_ability = core.abilities.get("listen")

        if not listen_ability:
            raise HTTPException(status_code=400, detail="Habilidad 'listen' no registrada en el robot.")

        res = await _maybe_await(listen_ability.execute("listen_once", {"timeout": 5.0}))
        if not res.get("success"):
            return {"success": False, "message": res.get("message", "No se pudo transcribir audio.")}

        text = res.get("data", {}).get("text", "")
        if not text:
            return {"success": False, "message": "No se detecto voz en el microfono."}

        raw = await _maybe_await(core.praxis.process(text))
        data = _normalize_response(raw)
        data["transcribed_text"] = text
        return data

    # ---------------------------------------------------------------------
    # WebSocket: canal bidireccional en tiempo real.
    # ---------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        """Canal bidireccional con el frontend. Requiere ?token=<console_token>."""
        # Validar token antes de aceptar la conexion (403 si es invalido).
        if not verify_ws_token(ws.query_params.get("token")):
            await ws.close(code=1008)  # policy violation
            return
        await ws.accept()
        _active_websockets.add(ws)
        core = get_core()
        try:
            while True:
                payload = await ws.receive_json()
                msg_type = payload.get("type", "message")
                text = payload.get("text") or payload.get("message") or ""

                if msg_type != "message" or not text:
                    continue

                if core.praxis is None:
                    await ws.send_json(
                        {"type": "error", "error": "WIS core no disponible."}
                    )
                    continue

                # Notificar: pensando.
                await ws.send_json({"type": "status", "state": "thinking"})

                try:
                    stream_fn = getattr(core.praxis, "process_stream", None)
                    if stream_fn is not None:
                        full_parts: List[str] = []
                        async for chunk in _maybe_await(stream_fn(text)):
                            full_parts.append(str(chunk))
                            await ws.send_json(
                                {"type": "chunk", "text": str(chunk)}
                            )
                        await ws.send_json(
                            {
                                "type": "response",
                                "response": "".join(full_parts),
                                "calls": [],
                                "path": None,
                            }
                        )
                    else:
                        sensor_data = gather_system_context()
                        raw = await _maybe_await(core.praxis.process(text, sensor_data=sensor_data))
                        data = _normalize_response(raw)
                        await ws.send_json({"type": "response", **data})
                except Exception as exc:
                    logger.exception("Error en WS: %s", exc)
                    await ws.send_json(
                        {"type": "status", "state": "error", "error": str(exc)}
                    )
                    continue

                # Notificar: terminado.
                await ws.send_json({"type": "status", "state": "done"})

        except WebSocketDisconnect:
            logger.info("Cliente WebSocket desconectado.")
        except Exception as exc:  # pragma: no cover - defensivo
            logger.exception("Error inesperado en WebSocket: %s", exc)
        finally:
            _active_websockets.discard(ws)

    @app.get("/api/logs")
    async def get_logs(lines: int = 100, _: None = Depends(require_auth)) -> JSONResponse:
        """Devuelve las ultimas lineas del archivo de log logs/wis.log."""
        log_file = Path(__file__).resolve().parent.parent / "logs" / "wis.log"
        if not log_file.exists():
            return JSONResponse({"logs": ["Log file not created yet."]})
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                return JSONResponse({"logs": [line.strip() for line in all_lines[-lines:]]})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ---------------------------------------------------------------------
    # Estaticos: la UI se sirve desde /console -> console/web/.
    # ---------------------------------------------------------------------

    web_dir = Path(__file__).resolve().parent / "web"
    if web_dir.is_dir():
        app.mount(
            "/console",
            StaticFiles(directory=str(web_dir), html=True),
            name="console-web",
        )

        @app.get("/")
        async def _root() -> JSONResponse:
            # Redirige a la consola montada.
            return JSONResponse(
                {"console": "/console/", "endpoints": ["/api/chat", "/ws"]}
            )

    return app


# ---------------------------------------------------------------------------
# Helper para arrancar uvicorn en un hilo (usado por web_view.py).
# ---------------------------------------------------------------------------


def _is_port_available(host: str, port: int) -> bool:
    """Verifica si un puerto esta libre en el host indicado."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def run_server(
    host: str = "127.0.0.1",
    port: int = 8770,
    core: Optional[WISCoreContainer] = None,
    auth_token: Optional[str] = None,
    cors_origins: Optional[List[str]] = None,
) -> None:
    """Arranca uvicorn de forma bloqueante buscando un puerto libre."""
    import uvicorn

    app = create_app(core, auth_token=auth_token, cors_origins=cors_origins)
    target_port = port
    while not _is_port_available(host, target_port):
        logger.warning(f"Port {target_port} is busy, checking port {target_port + 1}...")
        target_port += 1

    logger.info(f"WIS server active on http://{host}:{target_port}/console/")
    print(f"\n  WIS Server active at: http://{host}:{target_port}/console/\n")
    uvicorn.run(app, host=host, port=target_port, log_level="warning")


def start_server_thread(
    host: str = "127.0.0.1",
    port: int = 8770,
    core: Optional[WISCoreContainer] = None,
    auth_token: Optional[str] = None,
    cors_origins: Optional[List[str]] = None,
) -> Callable[[], None]:
    """Lanza el servidor en un hilo demonio y devuelve un stop() callable."""
    import threading
    import uvicorn

    app = create_app(core, auth_token=auth_token, cors_origins=cors_origins)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def _stop() -> None:
        server.should_exit = True

    return _stop
