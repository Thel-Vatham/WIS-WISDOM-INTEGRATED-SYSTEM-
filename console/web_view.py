"""
WIS Console - pywebview window launcher.
Lanzador de ventana pywebview para la consola.

Arranca el servidor FastAPI en un hilo en segundo plano y abre una
ventana nativa con pywebview apuntando a la UI servida por /console/.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .server import WISCoreContainer, start_server_thread

logger = logging.getLogger("wis.console.web_view")


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    """Espera a que el servidor responda antes de abrir la ventana."""
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)
    return False


def launch_console(
    url: str = "http://127.0.0.1:8770/console/",
    title: str = "WIS Console",
    width: int = 900,
    height: int = 700,
    core: Optional[WISCoreContainer] = None,
    auth_token: Optional[str] = None,
    cors_origins: Optional[list] = None,
    **_webview_kwargs: Any,
) -> None:
    """Abre la consola de WIS en una ventana nativa de pywebview.

    Args:
        url:    URL de la UI servida por el backend.
        title:  Titulo de la ventana.
        width:  Ancho inicial en pixeles.
        height: Alto inicial en pixeles.
        core:   Contenedor con el nucleo de WIS (cortex/praxis/...).
        auth_token:  Token requerido por la API (Bearer / ?token=).
        cors_origins: Origenes permitidos por CORS.
    """
    # Arranca el servidor en segundo plano (hilo demonio).
    host = "127.0.0.1"
    port = 8770
    if url:
        # Extrae host/puerto de la url si se personalizo.
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            if parsed.hostname:
                host = parsed.hostname
            if parsed.port:
                port = parsed.port
        except Exception:
            pass

    start_server_thread(
        host=host, port=port, core=core,
        auth_token=auth_token, cors_origins=cors_origins,
    )

    # Espera a que el servidor este listo antes de abrir la ventana.
    base = f"http://{host}:{port}/"
    if not _wait_for_server(base):
        logger.warning("El servidor no respondio a tiempo; abriendo de todos modos.")

    # Import diferido: pywebview puede no estar instalado en algunos entornos.
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - dependencia opcional
        raise RuntimeError(
            "pywebview no esta instalado. Instala con: pip install pywebview"
        ) from exc

    # Crea la ventana y arranca el loop de pywebview (bloqueante).
    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        min_size=(480, 400),
        frameless=False,
        easy_drag=False,
        text_select=True,
    )
    webview.start(debug=False)


if __name__ == "__main__":  # pragma: no cover
    # Ejecucion directa: arranca con un nucleo vacio (demo).
    logging.basicConfig(level=logging.INFO)
    launch_console()
