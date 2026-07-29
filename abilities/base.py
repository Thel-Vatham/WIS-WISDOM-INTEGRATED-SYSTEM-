"""
WIS Ability Base - Interfaz base para todas las habilidades.
========================================
Define el contrato comun que toda habilidad de WIS debe implementar.
Una "habilidad" es una capacidad modular del robot (voz, vision, etc.)
que el agente puede invocar de forma estandarizada.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Ability(ABC):
    """
    Clase base abstracta para todas las habilidades de WIS.

    Cada habilidad expone:
      - Metadatos (name, description, domain) para identificacion.
      - execute(): metodo asincrono que ejecuta una accion concreta.
      - get_schema(): descripcion de acciones/parametros para inyectar al LLM.

    Toda ejecucion devuelve un dict estandarizado:
        {"success": bool, "data": Any, "message": str}
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre unico y corto de la habilidad (ej. 'voice', 'vision')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripcion legible para humanos del proposito de la habilidad."""
        raise NotImplementedError

    @property
    @abstractmethod
    def domain(self) -> str:
        """
        Categoria funcional de la habilidad.
        Ejemplos: 'voice', 'vision', 'movement', 'system', 'knowledge'.
        """
        raise NotImplementedError

    @abstractmethod
    async def execute(self, action: str, params: dict) -> dict:
        """
        Ejecuta una accion de la habilidad.

        Args:
            action: Nombre de la accion a ejecutar (ej. 'speak', 'capture').
            params: Diccionario de parametros para la accion.

        Returns:
            dict con la forma:
                {
                    "success": bool,   # True si la accion tuvo exito
                    "data": Any,       # Resultado especifico (path, conteo, etc.)
                    "message": str,    # Mensaje legible para el agente/usuario
                }
        """
        raise NotImplementedError

    @abstractmethod
    def get_schema(self) -> list:
        """
        Devuelve el esquema de acciones que soporta esta habilidad.
        Pensado para inyectarse en el prompt del LLM y que sepa como llamarla.

        Returns:
            Lista de dicts, cada uno con la forma:
                {
                    "action": str,         # Nombre de la accion
                    "description": str,    # Que hace la accion
                    "params": dict,        # Parametros esperados (nombre -> tipo/descripcion)
                }
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        # Representacion util para logs y depuracion
        return f"<Ability name={self.name} domain={self.domain}>"


# --------------------------------------------------------------------------- #
# Hardware Abstraction Layer (HAL)
# --------------------------------------------------------------------------- #

class HardwareAbility(Ability):
    """Clase base para habilidades orientadas a la comunicacion e integracion de hardware."""

    @property
    def domain(self) -> str:
        return "hardware"

    @property
    @abstractmethod
    def connection_status(self) -> dict:
        """Devuelve el estado actual de la conexion con el dispositivo/puerto."""
        raise NotImplementedError


class SensorAbility(HardwareAbility):
    """Clase base para sensores físicos o virtuales."""

    @abstractmethod
    async def read_sensor(self, sensor_id: str = "") -> dict:
        """Lee los valores actuales de un sensor."""
        raise NotImplementedError


class ActuatorAbility(HardwareAbility):
    """Clase base para actuadores (motores, relés, servomotores, luces)."""

    @abstractmethod
    async def write_actuator(self, actuator_id: str, value: Any) -> dict:
        """Envía un comando o valor a un actuador."""
        raise NotImplementedError

