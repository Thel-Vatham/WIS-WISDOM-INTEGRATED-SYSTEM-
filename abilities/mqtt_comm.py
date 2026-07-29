"""
WIS MQTT Communication Ability - Protocolo MQTT para Sensores e IoT.
================================================--------------------
Permite a WIS conectarse a brokers MQTT (Mosquitto, Home Assistant, AWS IoT, etc.),
suscribirse a temas de sensores en tiempo real y publicar comandos de control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any, Dict, List, Optional

from .base import HardwareAbility

logger = logging.getLogger("wis.abilities.mqtt_comm")

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    mqtt = None
    PAHO_AVAILABLE = False


class MQTTCommAbility(HardwareAbility):
    """
    Habilidad MQTT para interaccion con sensores y dispositivos IoT en WIS.
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._broker_info: Dict[str, Any] = {}
        self._is_connected: bool = False
        self._message_queue: Dict[str, List[str]] = {}

    @property
    def name(self) -> str:
        return "mqtt_comm"

    @property
    def description(self) -> str:
        return (
            "Comunicacion por protocolo MQTT. Permite conectar a brokers (Mosquitto, Home Assistant), "
            "suscribirse a temas de sensores en tiempo real y publicar comandos de control IoT."
        )

    @property
    def connection_status(self) -> dict:
        return {
            "paho_installed": PAHO_AVAILABLE,
            "connected": self._is_connected,
            "broker": self._broker_info.get("host", ""),
            "port": self._broker_info.get("port", 1883),
            "subscribed_topics": list(self._message_queue.keys()),
        }

    def get_schema(self) -> list:
        return [
            {
                "action": "connect_broker",
                "description": "Conecta a un broker MQTT remoto o local.",
                "params": {
                    "host": "string — IP o hostname del broker MQTT (ej: '192.168.1.100' o 'localhost')",
                    "port": "int (opcional) — puerto MQTT. Default: 1883",
                    "client_id": "string (opcional) — ID del cliente MQTT",
                },
            },
            {
                "action": "publish",
                "description": "Publica un mensaje en un tema (topic) MQTT.",
                "params": {
                    "topic": "string — tema MQTT (ej: 'sensores/temperatura' o 'robot/comandos')",
                    "payload": "string o dict — contenido del mensaje a enviar",
                    "qos": "int (opcional) — nivel QoS (0, 1, 2). Default: 0",
                },
            },
            {
                "action": "subscribe",
                "description": "Se suscribe a un tema MQTT para recibir telemetria.",
                "params": {
                    "topic": "string — tema MQTT al cual suscribirse (ej: 'sensores/#')",
                },
            },
            {
                "action": "get_messages",
                "description": "Obtiene los mensajes recibidos recientemente en un tema suscrito.",
                "params": {
                    "topic": "string — tema MQTT a consultar",
                },
            },
            {
                "action": "get_status",
                "description": "Devuelve el estado de la conexion MQTT.",
                "params": {},
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").strip().lower()

        if action == "connect_broker":
            return await self._connect_broker(params)
        elif action == "publish":
            return await self._publish(params)
        elif action == "subscribe":
            return await self._subscribe(params)
        elif action == "get_messages":
            return self._get_messages(params)
        elif action == "get_status":
            return {"success": True, "data": self.connection_status, "message": "Estado MQTT."}
        else:
            return {
                "success": False,
                "data": None,
                "message": f"Accion desconocida: '{action}'. Disponibles: connect_broker, publish, subscribe, get_messages, get_status",
            }

    # ------------------------------------------------------------------ #
    # Metodos internos
    # ------------------------------------------------------------------ #

    async def _connect_broker(self, params: dict) -> dict:
        host = str(params.get("host", "localhost")).strip()
        port = int(params.get("port", 1883))
        client_id = str(params.get("client_id", "WIS_Agent")).strip()

        if not PAHO_AVAILABLE:
            return {
                "success": False,
                "data": None,
                "message": "paho-mqtt no esta instalado. Ejecuta 'pip install paho-mqtt'.",
            }

        def _on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._is_connected = True
                logger.info("MQTTCommAbility: conectado exitosamente al broker %s:%d", host, port)
            else:
                self._is_connected = False
                logger.warning("MQTTCommAbility: fallo al conectar al broker, rc=%d", rc)

        def _on_message(client, userdata, msg):
            top = msg.topic
            payload_str = msg.payload.decode("utf-8", errors="replace")
            if top not in self._message_queue:
                self._message_queue[top] = []
            self._message_queue[top].append(payload_str)
            # Mantener ultimos 50 mensajes por topic
            if len(self._message_queue[top]) > 50:
                self._message_queue[top].pop(0)

        def _do_connect():
            client = mqtt.Client(client_id=client_id)
            client.on_connect = _on_connect
            client.on_message = _on_message
            client.connect(host, port, keepalive=60)
            client.loop_start()
            return client

        try:
            self._client = await asyncio.to_thread(_do_connect)
            self._broker_info = {"host": host, "port": port, "client_id": client_id}
            self._is_connected = True
            return {
                "success": True,
                "data": self._broker_info,
                "message": f"Conectando al broker MQTT en {host}:{port}...",
            }
        except Exception as exc:
            self._is_connected = False
            return {"success": False, "data": None, "message": f"Error al conectar con el broker MQTT {host}:{port}: {exc}"}

    async def _publish(self, params: dict) -> dict:
        topic = str(params.get("topic", "")).strip()
        raw_payload = params.get("payload", "")
        qos = int(params.get("qos", 0))

        if not topic:
            return {"success": False, "data": None, "message": "Falta parametro 'topic'."}

        if not self._client or not self._is_connected:
            return {"success": False, "data": None, "message": "No hay conexion activa con un broker MQTT. Llama a connect_broker primero."}

        if isinstance(raw_payload, (dict, list)):
            payload_str = json.dumps(raw_payload, ensure_ascii=False)
        else:
            payload_str = str(raw_payload)

        def _do_pub():
            info = self._client.publish(topic, payload_str, qos=qos)
            info.wait_for_publish()

        try:
            await asyncio.to_thread(_do_pub)
            return {
                "success": True,
                "data": {"topic": topic, "payload": payload_str},
                "message": f"Mensaje publicado exitosamente en topic '{topic}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al publicar en MQTT '{topic}': {exc}"}

    async def _subscribe(self, params: dict) -> dict:
        topic = str(params.get("topic", "")).strip()
        if not topic:
            return {"success": False, "data": None, "message": "Falta parametro 'topic'."}

        if not self._client or not self._is_connected:
            return {"success": False, "data": None, "message": "No hay conexion activa con un broker MQTT."}

        def _do_sub():
            self._client.subscribe(topic)
            if topic not in self._message_queue:
                self._message_queue[topic] = []

        try:
            await asyncio.to_thread(_do_sub)
            return {"success": True, "data": {"topic": topic}, "message": f"Suscrito exitosamente al topic '{topic}'."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al suscribirse al topic '{topic}': {exc}"}

    def _get_messages(self, params: dict) -> dict:
        topic = str(params.get("topic", "")).strip()
        if not topic:
            return {"success": False, "data": None, "message": "Falta parametro 'topic'."}

        msgs = self._message_queue.get(topic, [])
        return {
            "success": True,
            "data": {"topic": topic, "messages": msgs},
            "message": f"Se obtuvieron {len(msgs)} mensaje(s) para '{topic}'.",
        }
