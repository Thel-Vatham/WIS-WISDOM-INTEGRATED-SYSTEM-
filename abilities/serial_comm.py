"""
WIS Serial Communication Ability - Comunicación por Puerto Serie/UART.
================================================-------------------
Permite a WIS comunicarse con microcontroladores (Arduino, ESP32, STM32, etc.),
sensores serie y dispositivos USB-TTL en tiempo real.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .base import HardwareAbility

logger = logging.getLogger("wis.abilities.serial_comm")

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None
    PYSERIAL_AVAILABLE = False


class SerialCommAbility(HardwareAbility):
    """
    Habilidad para comunicacion por puerto serie (UART/USB) en WIS.
    Permite enviar/recibir datos a microcontroladores como Arduino o ESP32.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "serial_comm"

    @property
    def description(self) -> str:
        return (
            "Comunicacion por puerto serie/UART (USB). "
            "Permite listar puertos COM/tty, conectar a Arduinos, ESP32 y sensores serie, "
            "enviar/recibir lineas de texto, JSON o bytes."
        )

    @property
    def connection_status(self) -> dict:
        active = []
        for port_name, conn in self._connections.items():
            is_open = getattr(conn, "is_open", False)
            active.append({"port": port_name, "is_open": is_open})
        return {
            "pyserial_installed": PYSERIAL_AVAILABLE,
            "active_connections": active,
        }

    def get_schema(self) -> list:
        return [
            {
                "action": "list_ports",
                "description": "Lista todos los puertos serie/USB disponibles en la maquina.",
                "params": {},
            },
            {
                "action": "connect",
                "description": "Abre la conexion serie en el puerto y baudrate especificados.",
                "params": {
                    "port": "string — nombre del puerto (ej: 'COM3' en Windows o '/dev/ttyUSB0' en Linux)",
                    "baudrate": "int (opcional) — velocidad en baudios. Default: 9600",
                    "timeout": "float (opcional) — timeout de lectura en segundos. Default: 1.0",
                },
            },
            {
                "action": "disconnect",
                "description": "Cierra la conexion serie en el puerto indicado.",
                "params": {
                    "port": "string — nombre del puerto a cerrar",
                },
            },
            {
                "action": "send_line",
                "description": "Envia una linea de texto (con salto de linea) por el puerto serie.",
                "params": {
                    "port": "string — nombre del puerto",
                    "text": "string — comando o texto a enviar",
                },
            },
            {
                "action": "read_line",
                "description": "Lee una linea de texto desde el puerto serie.",
                "params": {
                    "port": "string — nombre del puerto",
                    "timeout": "float (opcional) — timeout maximo de espera en segundos",
                },
            },
            {
                "action": "get_status",
                "description": "Obtiene el estado de las conexiones serie activas.",
                "params": {},
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").strip().lower()

        if action == "list_ports":
            return await self._list_ports()
        elif action == "connect":
            return await self._connect(params)
        elif action == "disconnect":
            return self._disconnect(params)
        elif action == "send_line":
            return await self._send_line(params)
        elif action == "read_line":
            return await self._read_line(params)
        elif action == "get_status":
            return {"success": True, "data": self.connection_status, "message": "Estado de conexion serie."}
        else:
            return {
                "success": False,
                "data": None,
                "message": f"Accion desconocida: '{action}'. Disponibles: list_ports, connect, disconnect, send_line, read_line, get_status",
            }

    # ------------------------------------------------------------------ #
    # Metodos internos
    # ------------------------------------------------------------------ #

    async def _list_ports(self) -> dict:
        if not PYSERIAL_AVAILABLE:
            return {
                "success": False,
                "data": [],
                "message": "pyserial no esta instalado. Instala con 'pip install pyserial' para usar puertos serie reales.",
            }

        def _do_list() -> List[dict]:
            ports = serial.tools.list_ports.comports()
            result = []
            for p in ports:
                result.append({
                    "port": p.device,
                    "description": p.description,
                    "hwid": p.hwid,
                    "vid": getattr(p, "vid", None),
                    "pid": getattr(p, "pid", None),
                })
            return result

        try:
            ports_list = await asyncio.to_thread(_do_list)
            return {
                "success": True,
                "data": ports_list,
                "message": f"Se encontraron {len(ports_list)} puerto(s) serie." if ports_list else "No se encontraron puertos serie activos.",
            }
        except Exception as exc:
            return {"success": False, "data": [], "message": f"Error al listar puertos: {exc}"}

    async def _connect(self, params: dict) -> dict:
        port = str(params.get("port", "")).strip()
        baudrate = int(params.get("baudrate", 9600))
        timeout = float(params.get("timeout", 1.0))

        if not port:
            return {"success": False, "data": None, "message": "Falta parametro 'port' (ej: 'COM3')."}

        if not PYSERIAL_AVAILABLE:
            return {
                "success": False,
                "data": None,
                "message": "pyserial no esta instalado. Ejecuta 'pip install pyserial'.",
            }

        if port in self._connections and getattr(self._connections[port], "is_open", False):
            return {"success": True, "data": {"port": port}, "message": f"El puerto '{port}' ya esta conectado y abierto."}

        def _do_connect():
            ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
            return ser

        try:
            ser = await asyncio.to_thread(_do_connect)
            self._connections[port] = ser
            return {
                "success": True,
                "data": {"port": port, "baudrate": baudrate},
                "message": f"Conexion establecida exitosamente en {port} @ {baudrate} baudios.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al conectar con {port}: {exc}"}

    def _disconnect(self, params: dict) -> dict:
        port = str(params.get("port", "")).strip()
        if not port:
            return {"success": False, "data": None, "message": "Falta parametro 'port'."}

        conn = self._connections.pop(port, None)
        if conn and getattr(conn, "is_open", False):
            try:
                conn.close()
                return {"success": True, "data": {"port": port}, "message": f"Puerto '{port}' desconectado."}
            except Exception as exc:
                return {"success": False, "data": None, "message": f"Error al cerrar puerto '{port}': {exc}"}

        return {"success": True, "data": {"port": port}, "message": f"El puerto '{port}' no estaba abierto."}

    async def _send_line(self, params: dict) -> dict:
        port = str(params.get("port", "")).strip()
        text = str(params.get("text", "")).strip()

        if not port or not text:
            return {"success": False, "data": None, "message": "Faltan parametros 'port' y/o 'text'."}

        conn = self._connections.get(port)
        if not conn or not getattr(conn, "is_open", False):
            return {"success": False, "data": None, "message": f"El puerto '{port}' no esta conectado. Conecta primero."}

        def _do_send():
            payload = (text + "\n").encode("utf-8")
            conn.write(payload)
            conn.flush()

        try:
            await asyncio.to_thread(_do_send)
            return {"success": True, "data": {"port": port, "sent": text}, "message": f"Enviado a {port}: {text}"}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al enviar datos a {port}: {exc}"}

    async def _read_line(self, params: dict) -> dict:
        port = str(params.get("port", "")).strip()

        if not port:
            return {"success": False, "data": None, "message": "Falta parametro 'port'."}

        conn = self._connections.get(port)
        if not conn or not getattr(conn, "is_open", False):
            return {"success": False, "data": None, "message": f"El puerto '{port}' no esta conectado."}

        def _do_read():
            line_bytes = conn.readline()
            return line_bytes.decode("utf-8", errors="replace").strip()

        try:
            line = await asyncio.to_thread(_do_read)
            return {
                "success": True,
                "data": {"port": port, "line": line},
                "message": f"Leido de {port}: '{line}'" if line else f"No se recibieron datos de {port} (timeout).",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al leer de {port}: {exc}"}
