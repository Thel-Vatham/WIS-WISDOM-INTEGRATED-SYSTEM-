"""
WIS Discovery Ability - Autodescubrimiento de robots y dispositivos.
====================================================================
Cuando WIS detecta un dispositivo nuevo (robot, luz IoT, sensor, etc.),
DiscoveryAbility coordina todo el proceso de integracion autonoma:

  1. Investiga el dispositivo: consulta la web sobre su SDK/API.
  2. Sintetiza un plan de integracion claro y tecnico.
  3. Llama a BuilderAbility para generar e instalar la habilidad.
  4. Confirma a WIS (y al usuario) que el dispositivo ya puede controlarse.

Principio: WIS no necesita que le programen el robot.
           WIS descubre como programarlo y lo hace sola.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import Ability

if TYPE_CHECKING:
    from .registry import AbilityRegistry

logger = logging.getLogger("wis.abilities.discovery")


class DiscoveryAbility(Ability):
    """
    Autodescubrimiento de robots y dispositivos IoT para WIS.
    
    Coordina investigacion + generacion de habilidades de forma autonoma.
    WIS puede integrarse con cualquier robot o dispositivo del que pueda
    obtener informacion tecnica (SDK, API, protocolo HTTP, etc.).
    """

    def __init__(self, registry: "AbilityRegistry", data_path: Path | None = None) -> None:
        self._registry = registry
        self._data_path = Path(data_path) if data_path else Path(__file__).resolve().parent.parent / "Data" / "discovery.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._discovered = self._load_store()

    # ------------------------------------------------------------------ #
    # Contrato Ability
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "discovery"

    @property
    def description(self) -> str:
        return (
            "Autodescubrimiento de robots y dispositivos IoT. "
            "Dado el nombre/IP de un dispositivo, WIS investiga autonomamente "
            "como conectarse, genera el codigo de integracion e instala la "
            "habilidad sin intervencion humana. "
            "Soporta robots NAO, Pepper, RENATICo, luces Zigbee/HTTP, y cualquier "
            "dispositivo con SDK documentado o interfaz de red accesible."
        )

    @property
    def domain(self) -> str:
        return "meta"

    def get_schema(self) -> list:
        return [
            {
                "action": "integrate_device",
                "description": (
                    "Integra autonomamente un nuevo robot o dispositivo IoT. "
                    "WIS buscara documentacion tecnica, planificara la integracion "
                    "y generara la habilidad de control automaticamente. "
                    "Usar cuando el usuario menciona un robot nuevo o dispositivo que WIS no conoce."
                ),
                "params": {
                    "device_type": "string — tipo de dispositivo (ej: 'robot NAO', 'luz Zigbee', 'camara IP', 'Pepper robot')",
                    "connection_info": "string — como conectarse: IP, puerto, protocolo, SDK (ej: '192.168.1.50 NAOqi 2.8', 'HTTP en 192.168.1.100')",
                    "ability_name": "string (opcional) — nombre para la nueva habilidad. Si no se da, WIS lo infiere.",
                    "extra_context": "string (opcional) — informacion adicional sobre el dispositivo o sus capacidades.",
                },
            },
            {
                "action": "scan_network",
                "description": (
                    "Escanea la red local en busca de robots o dispositivos conocidos. "
                    "Reporta IPs activas y puertos tipicos de robots (9559 NAO, 5000 Pepper, etc.)."
                ),
                "params": {
                    "subnet": "string (opcional) — subred a escanear. Default: 192.168.1.0/24",
                    "timeout": "float (opcional) — timeout por host en segundos. Default: 0.5",
                },
            },
            {
                "action": "list_discovered",
                "description": "Lista todos los robots y dispositivos que WIS ha integrado.",
                "params": {},
            },
            {
                "action": "get_device_profile",
                "description": "Devuelve el perfil completo de los dispositivos descubiertos, incluyendo investigacion y schema de la habilidad.",
                "params": {},
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        """Ejecuta acciones de autodescubrimiento."""
        action = (action or "").strip().lower()

        if action == "integrate_device":
            return await self._integrate_device(params)
        elif action == "scan_network":
            return await self._scan_network(params)
        elif action == "list_discovered":
            return self._list_discovered()
        elif action == "get_device_profile":
            return self._get_device_profile()
        else:
            return {
                "success": False,
                "data": None,
                "message": f"Accion desconocida: '{action}'. Disponibles: integrate_device, scan_network, list_discovered, get_device_profile",
            }

    # ------------------------------------------------------------------ #
    # Integracion autonoma de dispositivos
    # ------------------------------------------------------------------ #
    async def _integrate_device(self, params: dict) -> dict:
        """
        Pipeline de autodescubrimiento:
          1. Investigar SDK/API del dispositivo
          2. Sintetizar requerimientos tecnicos
          3. Generar habilidad con BuilderAbility
        """
        device_type = str(params.get("device_type", "")).strip()
        connection_info = str(params.get("connection_info", "")).strip()
        extra_context = str(params.get("extra_context", "")).strip()
        ability_name = str(params.get("ability_name", "")).strip()

        if not device_type:
            return {
                "success": False,
                "data": None,
                "message": "Falta 'device_type'. Dime que tipo de dispositivo es (ej: 'robot NAO', 'luz Zigbee').",
            }

        # Inferir nombre de habilidad si no se dio
        if not ability_name:
            ability_name = self._infer_ability_name(device_type)

        logger.info(
            "DiscoveryAbility: iniciando integracion de '%s' como '%s'...",
            device_type, ability_name
        )

        # ── Paso 1: Investigar el dispositivo ───────────────────────────
        research = await self._research_device(device_type, connection_info, extra_context)
        logger.info("DiscoveryAbility: investigacion completada para '%s'.", device_type)

        # ── Paso 2: Construir los requerimientos para el Builder ────────
        requirements = self._build_requirements(
            device_type=device_type,
            connection_info=connection_info,
            extra_context=extra_context,
            research=research,
            ability_name=ability_name,
        )

        # ── Paso 3: Delegar a BuilderAbility ────────────────────────────
        if not self._registry.has("builder"):
            return {
                "success": False,
                "data": None,
                "message": "BuilderAbility no esta disponible. El sistema de meta-programacion no esta inicializado.",
            }

        build_result = await self._registry.execute(
            "builder",
            "create_ability",
            {
                "ability_name": ability_name,
                "requirements": requirements,
                "overwrite": False,
            },
        )

        record = {
            "device_type": device_type,
            "connection_info": connection_info,
            "extra_context": extra_context,
            "ability_name": ability_name,
            "status": "integrated" if build_result.get("success") else "failed",
            "build_success": bool(build_result.get("success")),
            "build_message": build_result.get("message"),
            "timestamp": self._current_timestamp(),
        }
        self._record_discovery(record)

        if build_result.get("success"):
            return {
                "success": True,
                "data": {
                    "device": device_type,
                    "ability_name": ability_name,
                    "connection": connection_info,
                    "build_data": build_result.get("data"),
                },
                "message": (
                    f"¡Integracion completada! WIS ahora puede controlar '{device_type}' "
                    f"a traves de la habilidad '{ability_name}'. "
                    f"{build_result.get('message', '')}"
                ),
            }
        return {
            "success": False,
            "data": {
                "device": device_type,
                "research": research[:500] if research else "",
                "requirements": requirements,
                "build_error": build_result.get("message"),
            },
            "message": (
                f"No se pudo completar la integracion de '{device_type}'. "
                f"Error del constructor: {build_result.get('message', 'desconocido')}"
            ),
        }

    async def _research_device(
        self,
        device_type: str,
        connection_info: str,
        extra_context: str,
    ) -> str:
        """
        Investiga el dispositivo buscando informacion tecnica sobre su SDK/API.
        Usa web_search si esta disponible, o genera un resumen del contexto dado.
        """
        # Intentar usar la habilidad de busqueda web si existe
        query = f"{device_type} Python SDK API control tutorial {connection_info}"
        search_text = await self._search_knowledge(query)
        if search_text:
            return search_text

        # Fallback: usar el contexto proporcionado directamente
        return (
            f"Dispositivo: {device_type}. "
            f"Informacion de conexion: {connection_info}. "
            f"Contexto adicional: {extra_context or 'No proporcionado'}."
        )

    async def _search_knowledge(self, query: str) -> str:
        """Usa las habilidades de conocimiento registradas para investigar el dispositivo."""
        search_methods = [
            ("web_search", "search"),
            ("knowledge", "web_search"),
            ("knowledge", "search"),
        ]

        for skill, action in search_methods:
            if not self._registry.has(skill):
                continue
            try:
                result = await asyncio.wait_for(
                    self._registry.execute(skill, action, {"query": query}),
                    timeout=15.0,
                )
                if result.get("success") and result.get("data"):
                    data = result["data"]
                    if isinstance(data, dict):
                        if isinstance(data.get("answer"), str) and data.get("answer"):
                            return data.get("answer")
                        if isinstance(data.get("abstract"), str) and data.get("abstract"):
                            return data.get("abstract")
                        if isinstance(data.get("definition"), str) and data.get("definition"):
                            return data.get("definition")
                        if isinstance(data.get("related"), list):
                            snippets = [
                                str(item.get("text") or item.get("body") or item.get("description", ""))[:240]
                                for item in data.get("related", []) if isinstance(item, dict)
                            ]
                            if snippets:
                                return "\n\n".join(snippets)
                    elif isinstance(data, str):
                        return data[:1500]
            except Exception as exc:
                logger.warning("DiscoveryAbility: fallo en la busqueda de conocimiento (%s.%s): %s", skill, action, exc)
        return ""

    @staticmethod
    def _build_requirements(
        device_type: str,
        connection_info: str,
        extra_context: str,
        research: str,
        ability_name: str,
    ) -> str:
        """
        Sintetiza los requerimientos tecnicos completos para BuilderAbility.
        Combina lo que el usuario dijo con lo que WIS encontro en internet.
        """
        sections = [
            f"## Dispositivo objetivo\n{device_type}",
            f"## Informacion de conexion\n{connection_info or 'No especificada — inferir de la investigacion'}",
        ]

        if extra_context:
            sections.append(f"## Contexto adicional del usuario\n{extra_context}")

        if research:
            sections.append(f"## Investigacion tecnica (WIS encontro esto en internet)\n{research[:1200]}")

        # Instrucciones especificas segun el tipo de dispositivo
        device_lower = device_type.lower()
        if any(k in device_lower for k in ["nao", "pepper", "aldebaran", "softbank"]):
            sections.append(
                "## Notas especificas para robot NAO/Pepper\n"
                "- SDK: NAOqi (Python 2.7 legacy) o qi framework (Python 3)\n"
                "- Puerto por defecto: 9559\n"
                "- Librerias: `naoqi` (legacy) o `qi` via conexion TCP\n"
                "- Capacidades tipicas: ALMotion (movimiento), ALTextToSpeech (voz), "
                "  ALVideoDevice (camara), ALSpeechRecognition (escucha), ALBehaviorManager\n"
                "- La habilidad debe manejar la conexion al broker NAOqi.\n"
                "- Acciones minimas: hablar (speak), mover_articulacion (move_joint), "
                "  capturar_camara (get_camera_frame), postura (set_posture), estado (get_status)"
            )
        elif any(k in device_lower for k in ["renatico", "renata", "ros"]):
            sections.append(
                "## Notas especificas para RENATICo / robots ROS\n"
                "- Protocolo: ROS o rosbridge (websocket en puerto 9090)\n"
                "- Librerias: `roslibpy` para conexion via rosbridge\n"
                "- Capacidades tipicas: publicar topics, suscribir topics, llamar servicios\n"
                "- La habilidad debe manejar conexion rosbridge y publicacion de mensajes\n"
                "- Acciones minimas: hablar, mover, capturar_imagen, obtener_estado"
            )
        elif any(k in device_lower for k in ["zigbee", "luz", "light", "bulb", "philips", "hue", "tuya"]):
            sections.append(
                "## Notas especificas para dispositivos IoT de iluminacion\n"
                "- Protocolo tipico: HTTP REST o MQTT\n"
                "- Si es Philips Hue: API REST en puerto 80 del hub\n"
                "- Si es Tuya/Zigbee generico: puede usar local API o cloud API\n"
                "- Acciones minimas: encender, apagar, cambiar_brillo, cambiar_color, estado"
            )
        elif any(k in device_lower for k in ["camara", "camera", "ip cam", "rtsp"]):
            sections.append(
                "## Notas especificas para camaras IP\n"
                "- Protocolo tipico: RTSP (video) o HTTP MJPEG\n"
                "- Librerias: `opencv-python` para captura de frames\n"
                "- Acciones minimas: capturar_frame, iniciar_stream, detener_stream"
            )

        sections.append(
            f"## Requisito final\n"
            f"La habilidad debe llamarse exactamente '{ability_name}' "
            f"y permitir a WIS controlar '{device_type}' de forma completa. "
            f"Incluir manejo de errores robusto y soporte para reconexion automatica si falla la conexion."
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------ #
    # Escaneo de red
    # ------------------------------------------------------------------ #
    async def _scan_network(self, params: dict) -> dict:
        """
        Escanea la red local buscando dispositivos roboticos conocidos.
        Detecta puertos tipicos: 9559 (NAO/Pepper), 9090 (ROS Bridge), 
        80/8080 (HTTP IoT), 554 (RTSP cameras).
        """
        subnet_base = str(params.get("subnet", "192.168.1")).strip()
        # Normalizar: si dieron "192.168.1.0/24", extraer la base
        subnet_base = re.sub(r"\.\d+/\d+$", "", subnet_base)
        subnet_base = re.sub(r"/\d+$", "", subnet_base)

        timeout_per_host = float(params.get("timeout", 0.3))

        # Puertos roboticos conocidos
        ROBOT_PORTS = {
            9559: "NAO/Pepper (NAOqi)",
            9090: "ROS Bridge (rosbridge_suite)",
            5000: "Pepper Web Interface",
            8080: "HTTP IoT / Web API",
            80: "HTTP IoT",
            554: "RTSP Camera",
            1883: "MQTT Broker",
        }

        found_devices = []

        async def probe_host(ip: str) -> dict | None:
            """Prueba si un host tiene algun puerto robotico abierto."""
            open_ports = []
            for port, service in ROBOT_PORTS.items():
                try:
                    conn = asyncio.open_connection(ip, port)
                    reader, writer = await asyncio.wait_for(conn, timeout=timeout_per_host)
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                    open_ports.append({"port": port, "service": service})
                except Exception:
                    pass
            if open_ports:
                return {"ip": ip, "open_ports": open_ports}
            return None

        # Escanear las primeras 254 IPs de la subred
        tasks = [probe_host(f"{subnet_base}.{i}") for i in range(1, 255)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, dict) and res:
                found_devices.append(res)

        if found_devices:
            for dev in found_devices:
                self._record_discovery({
                    "status": "network_detected",
                    "ip": dev["ip"],
                    "open_ports": dev["open_ports"],
                    "timestamp": self._current_timestamp(),
                })

        if not found_devices:
            return {
                "success": True,
                "data": [],
                "message": f"No se encontraron dispositivos roboticos en {subnet_base}.0/24. Verifica que estes en la misma red.",
            }

        # Formatear respuesta
        summary_lines = []
        for dev in found_devices:
            ports_str = ", ".join(f"{p['port']} ({p['service']})" for p in dev["open_ports"])
            summary_lines.append(f"  IP {dev['ip']}: {ports_str}")

        return {
            "success": True,
            "data": found_devices,
            "message": (
                f"Encontre {len(found_devices)} dispositivo(s) en {subnet_base}.0/24:\n"
                + "\n".join(summary_lines)
                + "\n\nPuedo integrarlos automaticamente. Dime cual quieres controlar."
            ),
        }

    # ------------------------------------------------------------------ #
    # Listado de dispositivos integrados
    # ------------------------------------------------------------------ #
    def _list_discovered(self) -> dict:
        """Lista todos los robots/dispositivos que WIS ha integrado."""
        if not self._registry.has("builder"):
            return {
                "success": False,
                "data": None,
                "message": "BuilderAbility no disponible.",
            }

        discovered = []
        for item in self._discovered:
            record = dict(item)
            ability_name = record.get("ability_name")
            if ability_name and self._registry.has(ability_name):
                ability = self._registry.get(ability_name)
                record["domain"] = ability.domain
                record["description"] = ability.description
                record["schema"] = ability.get_schema()
            discovered.append(record)

        return {
            "success": True,
            "data": discovered,
            "message": (
                f"{len(discovered)} elementos descubiertos e integrados." if discovered else "No hay dispositivos descubiertos aun."
            ),
        }

    def _get_device_profile(self) -> dict:
        """Devuelve un perfil detallado de los dispositivos descubiertos."""
        profile = {
            "total_discovered": len(self._discovered),
            "devices": [],
        }
        for item in self._discovered:
            record = dict(item)
            ability_name = record.get("ability_name")
            if ability_name and self._registry.has(ability_name):
                ability = self._registry.get(ability_name)
                record["domain"] = ability.domain
                record["description"] = ability.description
                record["schema"] = ability.get_schema()
            profile["devices"].append(record)

        return {
            "success": True,
            "data": profile,
            "message": f"Perfil de {len(profile['devices'])} dispositivo(s) descubierto(s).",
        }

    # ------------------------------------------------------------------ #
    # Utilidades
    # ------------------------------------------------------------------ #
    @staticmethod
    def _infer_ability_name(device_type: str) -> str:
        """Infiere un nombre de habilidad snake_case a partir del tipo de dispositivo."""
        name = device_type.lower()
        # Mapeo de nombres conocidos
        replacements = {
            "robot nao": "nao_robot",
            "nao": "nao_robot",
            "pepper": "pepper_robot",
            "renatico": "renatico_robot",
            "renata": "renatico_robot",
            "luz zigbee": "zigbee_light",
            "luz": "iot_light",
            "camara ip": "ip_camera",
            "camara": "ip_camera",
            "camera": "ip_camera",
        }

        for key, val in replacements.items():
            if key in name:
                return val

        # Normalizar: quitar caracteres especiales
        name = re.sub(r"[^a-z0-9\s]", "", name)
        name = re.sub(r"\s+", "_", name.strip())
        name = re.sub(r"_+", "_", name).strip("_")

        # Asegurar longitud minima
        if len(name) < 3:
            name = f"{name}_device"

        return name[:49]

    def _load_store(self) -> list[dict]:
        """Carga el historial de descubrimientos desde el almacenamiento local."""
        if not self._data_path.exists():
            return []
        try:
            content = self._data_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.warning("DiscoveryAbility: no se pudo cargar el store de descubrimiento: %s", exc)
        return []

    def _record_discovery(self, record: dict) -> None:
        """Registra un descubrimiento en memoria y persiste el historial."""
        if not isinstance(record, dict):
            return
        self._discovered.append(record)
        try:
            self._data_path.write_text(
                json.dumps(self._discovered, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("DiscoveryAbility: no se pudo guardar el descubrimiento: %s", exc)

    @staticmethod
    def _current_timestamp() -> str:
        import datetime
        return datetime.datetime.utcnow().isoformat() + "Z"
