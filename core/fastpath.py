"""
WIS Engineering Fast-Path Engine.
=================================
Módulo de resolución determinista de 0 ms para comandos de ingeniería y hardware:
- Consulta instantánea de topología, puertos y estado de dispositivos.
- Búsqueda determinista de pines (Pinout Lookups) y mapeos de señales.
- Comandos directos de envío serial / MQTT sin pasar por el LLM.
- Consulta de atajos procedimentales de compilación memorizados.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from core.hardware_memory import HardwareMemory

logger = logging.getLogger("wis.core.fastpath")


import unicodedata

def _normalize_text(text: str) -> str:
    try:
        # Decomponer acentos y remover diacríticos (ej: cómo -> como, qué -> que)
        nfkd = unicodedata.normalize("NFKD", text)
        ascii_text = nfkd.encode("ASCII", "ignore").decode("utf-8")
        return ascii_text.lower()
    except Exception:
        return text.lower()


class EngineeringFastPath:
    """Interceptor de baja latencia para resolver intenciones de ingeniería y hardware."""

    def __init__(self, hardware_memory: HardwareMemory, abilities: Optional[Any] = None) -> None:
        self.hw_memory = hardware_memory
        self.abilities = abilities

    async def try_handle(self, user_input: str) -> Optional[Dict[str, Any]]:
        """
        Evalúa si la entrada del usuario puede resolverse de forma determinista en 0ms.
        Retorna un dict con la respuesta si se resolvió, o None si debe pasar al LLM.
        """
        raw_text = (user_input or "").strip()
        if not raw_text:
            return None

        text = _normalize_text(raw_text)

        # ------------------------------------------------------------------ #
        # 1. Consulta de Dispositivos / Puertos / Topología
        # ------------------------------------------------------------------ #
        if re.search(r"\b(listar?\s+(dispositivos|puertos|hardware|devices|ports)|estado\s+del?\s+hardware|scan\s+(ports|devices))\b", text):
            devices = self.hw_memory.list_devices()
            if not devices:
                return {
                    "handled": True,
                    "response": "No hay dispositivos de hardware registrados en la memoria técnica.",
                    "calls": [],
                    "path_used": "fastpath",
                }
            lines = [f"**Topología de Hardware Registrada ({len(devices)} dispositivos):**"]
            for d in devices:
                lines.append(f"- **[{d['device_id'].upper()}]** {d['name']} | Interfaz: `{d['interface']}` | Puerto/Dir: `{d['port_or_address']}` | Baud: `{d['baud_rate']}` | Estado: `{d['status']}`")
            return {
                "handled": True,
                "response": "\n".join(lines),
                "calls": [],
                "path_used": "fastpath",
            }

        # ------------------------------------------------------------------ #
        # 2. Consulta de Pinouts / Señales / GPIOs
        # ------------------------------------------------------------------ #
        pin_search_match = re.search(r"(?:que\s+pin\s+(?:usa|tiene|esta\s+en)|pinout\s+de(?:l)?|donde\s+esta\s+conectado\s+(?:el|la)?)\s+([a-zA-Z0-9_\-]+)", text)
        if pin_search_match:
            target = pin_search_match.group(1).strip()
            # Buscar por dispositivo primero
            pins = self.hw_memory.get_pin_map(target)
            if pins:
                lines = [f"**Mapeo de Pines para dispositivo `{target.upper()}`:**"]
                for p in pins:
                    lines.append(f"- **{p['pin_or_gpio']}**: {p['label']} (Función: `{p['function']}`, Señal: `{p['signal_type']}`)")
                return {
                    "handled": True,
                    "response": "\n".join(lines),
                    "calls": [],
                    "path_used": "fastpath",
                }

            # Buscar por etiqueta funcional (ej: 'servo', 'motor', 'relay', 'sda')
            matches = self.hw_memory.find_pin_by_label(target)
            if matches:
                lines = [f"**Pines encontrados para `{target}`:**"]
                for m in matches:
                    lines.append(f"- **{m['device_name']} ({m['device_id'].upper()})** -> **{m['pin_or_gpio']}**: {m['label']} ({m['function']}) en `{m['port_or_address']}`")
                return {
                    "handled": True,
                    "response": "\n".join(lines),
                    "calls": [],
                    "path_used": "fastpath",
                }

        # ------------------------------------------------------------------ #
        # 3. Consulta de Comandos Procedimentales Memorizados
        # ------------------------------------------------------------------ #
        proc_match = re.search(r"(?:como\s+(?:se\s+)?(?:compil\w+|flash\w+|sub\w+|ejecut\w+|build|upload)|comando\s+(?:de\s+)?(?:build|flash|compile|ejecucion)(?:\s+(?:para|de))?)\s+(?:el\s+)?(?:firmware\s+(?:de\s+)?|codigo\s+(?:de\s+)?)?([a-zA-Z0-9_\-]+)", text)
        if proc_match:
            context = proc_match.group(1).strip()
            # Buscar en toolchains comunes
            for tc in ("platformio", "arduino_cli", "idf", "esptool", "shell", "robotics_hal"):
                best = self.hw_memory.get_best_procedural_command(tc, context)
                if best:
                    rate = round((best["success_count"] / max(1, best["success_count"] + best["failure_count"])) * 100, 1)
                    return {
                        "handled": True,
                        "response": f"**Comando procedimental memorizado para `{context}` ({tc}):**\n```bash\n{best['command']}\n```\n*(Éxito: {rate}% en {best['success_count']} ejecuciones)*",
                        "calls": [],
                        "path_used": "fastpath",
                    }

        # ------------------------------------------------------------------ #
        # 4. Envío directo a puerto Serial / MQTT (Direct Action Dispatch)
        # ------------------------------------------------------------------ #
        # Ejemplo: envia "PING" a COM4 o enviar trama "A55A" por COM3
        direct_serial_match = re.search(r"(?:envi?ar?|mandar?|escribir?)\s+[\"']([^\"']+)[\"']\s+(?:a|por|en)\s+([a-zA-Z0-9_]+)", user_input, re.IGNORECASE)
        if direct_serial_match and self.abilities:
            payload = direct_serial_match.group(1)
            port_or_dev = direct_serial_match.group(2).strip().upper()

            # Verificar si existe habilidad serial_comm
            serial_ability = getattr(self.abilities, "get", lambda k: None)("serial_comm") if hasattr(self.abilities, "get") else None
            if serial_ability and hasattr(serial_ability, "execute"):
                try:
                    res = await serial_ability.execute("write", {"port": port_or_dev, "data": payload})
                    msg = res.get("message") or ("Trama enviada correctamente." if res.get("success") else "Fallo al enviar trama.")
                    return {
                        "handled": True,
                        "response": f"**Direct Fast-Path [SERIAL]**: {msg}",
                        "calls": [{"action": "serial_comm.write", "params": {"port": port_or_dev, "data": payload}}],
                        "path_used": "fastpath",
                    }
                except Exception as exc:
                    logger.error("FastPath serial write error: %s", exc)

        return None
