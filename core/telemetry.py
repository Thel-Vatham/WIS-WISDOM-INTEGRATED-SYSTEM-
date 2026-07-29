"""
WIS Telemetry Engine - Motor de Ingesta de Telemetría y Alertas en Tiempo Real.
================================================----------------=============
Recopila lecturas continuas de sensores (vía Serial, MQTT, HTTP, etc.),
evalúa reglas de umbral (Threshold Rules) y dispara eventos al event_bus
para reacciones autónomas proactivas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from core.event_bus import event_bus

logger = logging.getLogger("wis.core.telemetry")


class TelemetryRule:
    """Regla de umbral para evaluar datos de telemetría."""

    def __init__(
        self,
        rule_id: str,
        metric: str,
        operator: str,
        threshold: float,
        action_prompt: str,
        cooldown_s: float = 10.0,
    ) -> None:
        self.rule_id = rule_id
        self.metric = metric
        self.operator = operator.strip().lower()  # '>', '<', '>=', '<=', '==', '!='
        self.threshold = float(threshold)
        self.action_prompt = action_prompt
        self.cooldown_s = max(1.0, float(cooldown_s))
        self.last_triggered: float = 0.0

    def evaluate(self, value: float) -> bool:
        now = time.time()
        if (now - self.last_triggered) < self.cooldown_s:
            return False

        trig = False
        if self.operator == ">":
            trig = value > self.threshold
        elif self.operator == "<":
            trig = value < self.threshold
        elif self.operator == ">=":
            trig = value >= self.threshold
        elif self.operator == "<=":
            trig = value <= self.threshold
        elif self.operator in ("==", "="):
            trig = abs(value - self.threshold) < 1e-6
        elif self.operator == "!=":
            trig = abs(value - self.threshold) >= 1e-6

        if trig:
            self.last_triggered = now

        return trig


class TelemetryEngine:
    """Motor de ingesta de telemetria en tiempo real."""

    def __init__(self) -> None:
        self._latest_telemetry: Dict[str, Any] = {}
        self._rules: Dict[str, TelemetryRule] = {}
        self._active: bool = False
        self._worker_task: Optional[asyncio.Task] = None
        self._trigger_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def set_trigger_callback(self, cb: Callable[[str, Dict[str, Any]], None]) -> None:
        """Configura un callback para invocar acciones autónomas cuando salte una regla."""
        self._trigger_callback = cb

    def add_rule(
        self,
        rule_id: str,
        metric: str,
        operator: str,
        threshold: float,
        action_prompt: str,
        cooldown_s: float = 10.0,
    ) -> None:
        rule = TelemetryRule(rule_id, metric, operator, threshold, action_prompt, cooldown_s)
        self._rules[rule_id] = rule
        logger.info("TelemetryEngine: regla añadida '%s' (%s %s %s)", rule_id, metric, operator, threshold)

    def remove_rule(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def ingest(self, source: str, data: Dict[str, Any]) -> List[str]:
        """
        Ingiere una lectura de telemetría de cualquier fuente (Serial, MQTT, HTTP).
        Retorna lista de rule_ids disparadas.
        """
        if not isinstance(data, dict):
            return []

        timestamp = time.time()
        self._latest_telemetry[source] = {"data": data, "timestamp": timestamp}
        event_bus.emit("telemetry.received", {"source": source, "data": data})

        triggered_rules = []
        for rule_id, rule in self._rules.items():
            if rule.metric in data:
                try:
                    val = float(data[rule.metric])
                    if rule.evaluate(val):
                        triggered_rules.append(rule_id)
                        payload = {
                            "rule_id": rule_id,
                            "metric": rule.metric,
                            "value": val,
                            "threshold": rule.threshold,
                            "operator": rule.operator,
                            "action_prompt": rule.action_prompt,
                            "source": source,
                        }
                        logger.warning("TelemetryEngine: REGLA DISPARADA '%s': %s %s %s (actual=%s)",
                                       rule_id, rule.metric, rule.operator, rule.threshold, val)
                        event_bus.emit("telemetry.alert_triggered", payload)

                        if self._trigger_callback:
                            try:
                                self._trigger_callback(rule.action_prompt, payload)
                            except Exception as exc:
                                logger.error("TelemetryEngine: error en callback de alerta: %s", exc)
                except (ValueError, TypeError):
                    continue

        return triggered_rules

    def get_latest(self) -> Dict[str, Any]:
        """Retorna el estado de telemetría más reciente."""
        return dict(self._latest_telemetry)

    def get_rules(self) -> List[Dict[str, Any]]:
        """Retorna las reglas activas."""
        return [
            {
                "rule_id": r.rule_id,
                "metric": r.metric,
                "operator": r.operator,
                "threshold": r.threshold,
                "action_prompt": r.action_prompt,
                "cooldown_s": r.cooldown_s,
            }
            for r in self._rules.values()
        ]


# Singleton global de telemetría
telemetry_engine = TelemetryEngine()
