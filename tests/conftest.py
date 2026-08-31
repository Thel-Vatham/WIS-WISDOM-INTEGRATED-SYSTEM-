"""
WIS Production Test Suite - Global Fixtures & Configurations.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from abilities.base import Ability
from abilities.registry import AbilityRegistry
from core.hardware_memory import HardwareMemory
from core.fastpath import EngineeringFastPath


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Crea un directorio temporal limpio para pruebas de archivos y SQLite."""
    tmp = Path(tempfile.mkdtemp(prefix="wis_test_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


@pytest.fixture
def temp_db_path(temp_dir: Path) -> Path:
    """Ruta a una base de datos SQLite temporal."""
    return temp_dir / "test_wis_memory.db"


@pytest.fixture
def hardware_memory(temp_db_path: Path) -> Generator[HardwareMemory, None, None]:
    """Instancia limpia de HardwareMemory conectada a base de datos temporal."""
    mem = HardwareMemory(db_path=temp_db_path)
    try:
        yield mem
    finally:
        mem.close()


@pytest.fixture
def mock_registry(temp_dir: Path) -> AbilityRegistry:
    """Instancia limpia de AbilityRegistry."""
    return AbilityRegistry()


class MockSerialAbility(Ability):
    """Habilidad Serial simulada para pruebas unitarias de envío directo."""

    def __init__(self) -> None:
        self.sent_packets: list = []

    @property
    def name(self) -> str:
        return "serial_comm"

    @property
    def description(self) -> str:
        return "Mock Serial Comm"

    @property
    def domain(self) -> str:
        return "hardware"

    async def execute(self, action: str, params: dict) -> dict:
        if action == "write":
            port = params.get("port")
            data = params.get("data")
            self.sent_packets.append({"port": port, "data": data})
            return {"success": True, "message": f"Trama '{data}' enviada exitosamente a {port}."}
        return {"success": False, "message": "Acción no soportada."}

    def get_schema(self) -> list:
        return [{"action": "write", "description": "Write data to serial port", "params": {"port": "str", "data": "str"}}]


@pytest.fixture
def mock_serial_ability() -> MockSerialAbility:
    return MockSerialAbility()


@pytest.fixture
def valid_skill_code() -> str:
    """Código Python válido que cumple 100% el contrato Ability para pruebas del sintetizador."""
    return '''# REQUIRES: 
from __future__ import annotations
from abilities.base import Ability

class StepperDriverAbility(Ability):
    """Habilidad generada para control de motores paso a paso."""

    def __init__(self, step_pin: int = 18, dir_pin: int = 19):
        self._step_pin = step_pin
        self._dir_pin = dir_pin
        self._current_pos = 0

    @property
    def name(self) -> str:
        return "stepper_driver"

    @property
    def description(self) -> str:
        return "Control de motor a pasos vía GPIO PWM y dirección."

    @property
    def domain(self) -> str:
        return "robotics"

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").strip().lower()
        if action == "move_steps":
            steps = int(params.get("steps", 0))
            direction = str(params.get("direction", "CW")).upper()
            self._current_pos += steps if direction == "CW" else -steps
            return {
                "success": True,
                "data": {"position": self._current_pos, "steps": steps, "direction": direction},
                "message": f"Motor movido {steps} pasos en dirección {direction}."
            }
        elif action == "get_position":
            return {
                "success": True,
                "data": {"position": self._current_pos},
                "message": f"Posición actual: {self._current_pos}"
            }
        return {"success": False, "data": None, "message": f"Acción desconocida: {action}"}

    def get_schema(self) -> list:
        return [
            {
                "action": "move_steps",
                "description": "Mueve el motor N pasos en una dirección (CW o CCW).",
                "params": {"steps": "int", "direction": "string ('CW' or 'CCW')"}
            },
            {
                "action": "get_position",
                "description": "Consulta la posición actual del encoder del motor.",
                "params": {}
            }
        ]
'''


@pytest.fixture
def malicious_skill_code() -> str:
    """Código con imports inseguros bloqueados por el validador AST."""
    return '''
import ctypes
from abilities.base import Ability

class MaliciousAbility(Ability):
    @property
    def name(self) -> str:
        return "malicious"
    @property
    def description(self) -> str:
        return "Insegura"
    @property
    def domain(self) -> str:
        return "hack"
    async def execute(self, action: str, params: dict) -> dict:
        return {"success": False}
    def get_schema(self) -> list:
        return []
'''
