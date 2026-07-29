"""WIS Safety - Safety policy engine and guard rails.

SafetyPolicy (Aegis) evalua si una llamada a una habilidad (tool call) puede
ejecutarse.  Dos modos de operacion:
  - secure:     acciones riesgosas requieren aprobacion explicita del usuario.
  - privileged: 100 % libre, sin restricciones.
"""
from __future__ import annotations

import re
import logging
from typing import Tuple

logger = logging.getLogger("wis.core.safety")

MODE_SECURE = "secure"
MODE_PRIVILEGED = "privileged"
_VALID_MODES = (MODE_SECURE, MODE_PRIVILEGED)

# Acciones criticas que SIEMPRE se bloquean sin importar el modo.
CRITICAL_DANGER_NAMES = (
    "force_movement",
    "disable_safety",
    "emergency_stop_override",
    "overcurrent",
    "calibration_override",
    "discharge_battery",
    "ignore_collision",
)

# Habilidades / acciones que requieren aprobacion en modo secure.
RISKY_ABILITY_NAMES = {
    "browser", "desktop", "execute_shell", "execute_powershell",
    "run_python_code", "open_application", "delete", "reboot",
    "shutdown", "formatear", "format", "rm", "eliminar",
}

# Hints para detectar movimiento fisico (robotica).
_MOVEMENT_NAME_HINTS = (
    "move", "movimiento", "walk", "caminar", "turn", "girar",
    "arm", "brazo", "head", "cabeza", "leg", "pierna",
    "joint", "articulacion", "servo", "motor", "gesture", "gesto",
)

_DANGEROUS_VELOCITY_RE = re.compile(
    r"(velocity|velocidad|speed)\s*[:=]\s*[-]?\s*([0-9]*\.?[0-9]+)",
    re.IGNORECASE,
)
_DANGEROUS_ANGLE_RE = re.compile(
    r"(angle|angulo|position|posicion)\s*[:=]\s*[-]?([0-9]*\.?[0-9]+)",
    re.IGNORECASE,
)

_MAX_SAFE_VELOCITY = 1.5
_MAX_ABS_ANGLE = 170.0


class SafetyPolicy:
    """Politica de seguridad para llamadas a habilidades."""

    def __init__(self, mode: str = MODE_SECURE) -> None:
        self._mode: str = MODE_SECURE
        self._e_stop_active: bool = False
        self.set_mode(mode)

    def trigger_e_stop(self) -> None:
        """Dispara la parada de emergencia global del sistema."""
        self._e_stop_active = True
        logger.critical("SafetyPolicy: ¡PARADA DE EMERGENCIA (E-STOP) ACTIVADA! Todas las acciones de hardware/SO quedan congeladas.")

    def reset_e_stop(self) -> None:
        """Reinicia el estado de parada de emergencia."""
        self._e_stop_active = False
        logger.info("SafetyPolicy: Parada de emergencia restablecida.")

    @property
    def is_e_stopped(self) -> bool:
        return self._e_stop_active

    def set_mode(self, mode: str) -> None:
        normalized = str(mode or MODE_SECURE).strip().lower()
        # Accept legacy names
        if normalized in ("safe",):
            normalized = MODE_SECURE
        elif normalized in ("autonomous",):
            normalized = MODE_PRIVILEGED
        if normalized not in _VALID_MODES:
            normalized = MODE_SECURE
        self._mode = normalized

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, call: dict) -> Tuple[bool, str]:
        """Evalua si una llamada puede ejecutarse."""
        if self._e_stop_active:
            return False, "emergency_stop_active"

        if not isinstance(call, dict):
            return False, "invalid_call_format"

        name = str(call.get("name") or call.get("skill") or "").lower().strip()
        action = str(call.get("action") or "").lower().strip()

        if name in CRITICAL_DANGER_NAMES or action in CRITICAL_DANGER_NAMES:
            return False, f"critical_danger:{action or name}"

        if self._is_dangerous_movement(call):
            return False, "dangerous_movement"

        # --- Automatic Rollback Protection on file writes/deletes ---
        if action in ("delete_file", "write_file", "delete", "remove") or name == "file_manager":
            params = call.get("params") if isinstance(call.get("params"), dict) else {}
            target_path = params.get("path") or params.get("filepath") or params.get("target")
            if target_path:
                try:
                    from core.rollback import rollback_engine
                    rollback_engine.create_backup(str(target_path))
                except Exception as exc:
                    logger.warning("SafetyPolicy: error al crear copia de seguridad rollback: %s", exc)

        return True, "ok"

    def needs_approval(self, call: dict) -> bool:
        """Devuelve True si la accion requiere aprobacion del usuario en modo secure.

        En modo privileged siempre devuelve False (todo se ejecuta libre).
        """
        if self._mode == MODE_PRIVILEGED:
            return False

        if not isinstance(call, dict):
            return False

        name = str(call.get("name") or call.get("skill") or "").lower().strip()
        action = str(call.get("action") or "").lower().strip()
        domain = str(call.get("domain") or "").lower().strip()

        combined = {name, action, domain}

        # Chequear si alguno de los identificadores esta en la lista riesgosa
        for token in combined:
            if token and token in RISKY_ABILITY_NAMES:
                return True

        # Acciones de shell / codigo
        if action in ("execute_shell", "execute_powershell", "run_python_code",
                       "open_application", "run"):
            return True

        # Acciones destructivas
        risky_action_hints = ("delete", "eliminar", "rm", "format", "formatear",
                               "shutdown", "reboot")
        for hint in risky_action_hints:
            if hint in name or hint in action:
                return True

        return False

    def _is_dangerous_movement(self, call: dict) -> bool:
        domain = str(call.get("domain") or "").lower().strip()
        name = str(call.get("name") or call.get("skill") or "").lower().strip()
        action = str(call.get("action") or "").lower().strip()
        combined_name = f"{domain} {name} {action}"

        is_movement = any(hint in combined_name for hint in _MOVEMENT_NAME_HINTS)
        if not is_movement:
            return False

        args = call.get("params") if isinstance(call.get("params"), dict) else (call.get("arguments") or {})
        args_text = self._stringify_arguments(args)

        for match in _DANGEROUS_VELOCITY_RE.finditer(args_text):
            try:
                value = abs(float(match.group(2)))
                if value > _MAX_SAFE_VELOCITY:
                    return True
            except (ValueError, IndexError):
                continue

        for match in _DANGEROUS_ANGLE_RE.finditer(args_text):
            try:
                value = abs(float(match.group(2)))
                if value > _MAX_ABS_ANGLE:
                    return True
            except (ValueError, IndexError):
                continue

        if self._mode == MODE_SECURE and not args_text.strip():
            return True

        return False

    @staticmethod
    def _stringify_arguments(args) -> str:
        if not args:
            return ""
        if isinstance(args, str):
            return args
        if isinstance(args, dict):
            parts = []
            for k, v in args.items():
                parts.append(f"{k}:{v}")
            return " ".join(parts)
        return str(args)


class FailureClassifier:
    """Clasifica errores de ejecucion en tipos estructurados."""

    @staticmethod
    def classify(error_text: str) -> str:
        text = str(error_text or "").lower()
        if any(h in text for h in ("blocked", "critical_danger", "dangerous", "safety")):
            return "SAFETY_BLOCK"
        if any(h in text for h in ("timeout", "timed out", "timedout")):
            return "TIMEOUT"
        if any(h in text for h in ("json", "parse", "format", "invalid_call")):
            return "LLM_FORMAT_ERROR"
        if any(h in text for h in ("connection", "network", "http 5", "http 4")):
            return "NETWORK_ERROR"
        return "HARDWARE_ERROR"
