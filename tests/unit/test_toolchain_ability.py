"""
Unit tests for ToolchainAbility (Microcontroller & Embedded Toolchains).
"""
import pytest
from abilities.toolchain import ToolchainAbility
from core.hardware_memory import HardwareMemory


@pytest.mark.asyncio
async def test_toolchain_schema():
    tc = ToolchainAbility()
    assert tc.name == "toolchain"
    assert tc.domain == "engineering"
    schema = tc.get_schema()
    assert len(schema) >= 4
    actions = [s["action"] for s in schema]
    assert "platformio_cmd" in actions
    assert "arduino_cli" in actions
    assert "esptool_cmd" in actions
    assert "serial_monitor" in actions


@pytest.mark.asyncio
async def test_toolchain_check_tools():
    tc = ToolchainAbility()
    res = await tc.execute("check_tools", {})
    assert res["success"] is True
    assert "data" in res
    assert "git" in res["data"]


@pytest.mark.asyncio
async def test_toolchain_unknown_action():
    tc = ToolchainAbility()
    res = await tc.execute("non_existent_action", {})
    assert res["success"] is False
    assert "Accion desconocida" in res["message"]
