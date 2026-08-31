"""
End-to-End Integration Test: Skill Synthesis -> Sandbox Test -> Hot-Reload -> Hardware Memory -> Fast-Path Execution.
"""
import pytest
from pathlib import Path

from abilities.registry import AbilityRegistry
from core.hardware_memory import HardwareMemory
from core.skill_synthesizer import SkillSynthesizer
from core.fastpath import EngineeringFastPath


@pytest.mark.asyncio
async def test_full_hardware_synthesis_and_execution_lifecycle(temp_dir: Path, temp_db_path: Path):
    # 1. Instanciar componentes del Core
    hw_memory = HardwareMemory(db_path=temp_db_path)
    registry = AbilityRegistry()
    custom_dir = temp_dir / "custom_abilities"

    # 2. Código de habilidad sintetizada para un Gripper Robótico
    gripper_skill_code = '''
from __future__ import annotations
from abilities.base import Ability

class RoboticGripperAbility(Ability):
    """Driver para pinza robótica con servo PWM y sensor de presión."""

    def __init__(self, port: str = "COM7", pin: int = 12):
        self._port = port
        self._pin = pin
        self._grip_state = 0 # 0% open, 100% closed

    @property
    def name(self) -> str:
        return "robotic_gripper"

    @property
    def description(self) -> str:
        return "Control de apertura y presión de garra robótica."

    @property
    def domain(self) -> str:
        return "robotics"

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").strip().lower()
        if action == "grip":
            percentage = max(0, min(100, int(params.get("percentage", 100))))
            self._grip_state = percentage
            return {
                "success": True,
                "data": {"grip_percentage": self._grip_state, "port": self._port, "pin": self._pin},
                "message": f"Garra ajustada al {percentage}% de presión."
            }
        elif action == "release":
            self._grip_state = 0
            return {
                "success": True,
                "data": {"grip_percentage": 0},
                "message": "Garra abierta completamente."
            }
        return {"success": False, "data": None, "message": f"Acción inválida: {action}"}

    def get_schema(self) -> list:
        return [
            {
                "action": "grip",
                "description": "Cierra la garra robótica a un porcentaje de fuerza.",
                "params": {"percentage": "int (0 a 100)"}
            },
            {
                "action": "release",
                "description": "Abre la garra liberando cualquier objeto.",
                "params": {}
            }
        ]
'''

    # 3. Síntesis, Sandbox y Hot-Reload
    success, msg, skill_name = SkillSynthesizer.save_and_hot_load(
        code=gripper_skill_code,
        registry=registry,
        custom_dir=custom_dir,
    )
    assert success is True
    assert skill_name == "robotic_gripper"
    assert "robotic_gripper" in registry.all()

    # 4. Registrar dispositivo y pines en HardwareMemory
    hw_memory.register_device(
        device_id="gripper_robot_1",
        name="6-DOF Arm Gripper Module",
        interface="serial",
        port_or_address="COM7",
        baud_rate=115200,
    )
    hw_memory.set_pin_mapping(
        device_id="gripper_robot_1",
        pin_or_gpio="GPIO12",
        function="PWM",
        label="gripper_servo_pwm",
        signal_type="pwm",
        notes="Robotic gripper angle control",
    )

    # 5. Ejecutar acción en la nueva habilidad cargada en caliente
    ability_instance = registry.get("robotic_gripper")
    assert ability_instance is not None
    exec_result = await ability_instance.execute("grip", {"percentage": 85})
    assert exec_result["success"] is True
    assert exec_result["data"]["grip_percentage"] == 85

    # 6. Registrar comando procedimental en memoria técnica
    hw_memory.record_procedural_command(
        command="gripper_driver.py --port COM7 --pin 12 --grip 85",
        toolchain="robotics_hal",
        context_tag="gripper_close",
        success=True,
        duration_ms=45.0,
    )
    best_cmd = hw_memory.get_best_procedural_command("robotics_hal", "gripper_close")
    assert best_cmd is not None
    assert best_cmd["success_count"] == 1

    # 7. Validar que el Fast-Path responda en 0ms
    fastpath = EngineeringFastPath(hardware_memory=hw_memory, abilities=registry.all())
    fp_res = await fastpath.try_handle("dónde está conectado el gripper")
    assert fp_res is not None
    assert fp_res["handled"] is True
    assert "GPIO12" in fp_res["response"]
    assert "gripper_servo_pwm" in fp_res["response"]

    hw_memory.close()
