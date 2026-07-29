"""WIS Event Bus - In-process pub/sub event dispatcher.

Desacopla emisores de eventos (pipeline, memoria, seguridad) de sus consumidores
(logs, telemetria, interfaz). Soporta handlers sincronos y asincronos.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("wis.core.event_bus")

# Handler callback signature: fn(data: Dict[str, Any]) -> None
EventHandler = Callable[[Dict[str, Any]], Any]


class EventBus:
    """Bus de eventos in-process tipo singleton/instancia.

    Permite suscribirse a eventos por nombre y emitirlos con un payload dict.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = {}

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Suscribe un handler a un evento por nombre."""
        if not callable(handler):
            raise ValueError("Handler must be callable")
        self._subscribers.setdefault(event_name, []).append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> bool:
        """Desuscribe un handler. Devuelve True si se elimino."""
        if event_name in self._subscribers:
            try:
                self._subscribers[event_name].remove(handler)
                return True
            except ValueError:
                pass
        return False

    def emit(self, event_name: str, data: Dict[str, Any] = None) -> None:
        """Emite un evento a todos los suscriptores.

        Los handlers sincronos se ejecutan de inmediato.
        Los asincronos se programan en el event loop actual (si hay uno corriendo).
        """
        payload = dict(data) if data is not None else {}
        payload["_event_name"] = event_name
        handlers = list(self._subscribers.get(event_name, []))
        # Handler comodin '*' (recibe todos los eventos).
        handlers.extend(self._subscribers.get("*", []))

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    # Si hay un loop corriendo, creamos una task; si no, log warning.
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(handler(payload))
                    except RuntimeError:
                        logger.warning(
                            "event_bus: no async loop running for async handler on '%s'",
                            event_name,
                        )
                else:
                    handler(payload)
            except Exception as exc:
                logger.exception("Error in event handler for '%s': %s", event_name, exc)

    def clear(self) -> None:
        """Limpia todos los suscriptores."""
        self._subscribers.clear()


# Singleton global por conveniencia.
event_bus = EventBus()
