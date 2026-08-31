"""
Unit tests for EngineeringFastPath (0ms deterministic routing for hardware and devices).
"""
import pytest
from core.fastpath import EngineeringFastPath
from core.hardware_memory import HardwareMemory


@pytest.mark.asyncio
async def test_fastpath_list_devices_empty(hardware_memory: HardwareMemory):
    fp = EngineeringFastPath(hardware_memory=hardware_memory)
    res = await fp.try_handle("listar dispositivos")
    assert res is not None
    assert res["handled"] is True
    assert "No hay dispositivos" in res["response"]


@pytest.mark.asyncio
async def test_fastpath_list_devices_populated(hardware_memory: HardwareMemory):
    hardware_memory.register_device(
        device_id="esp32_main",
        name="ESP32 Motion Controller",
        interface="serial",
        port_or_address="COM3",
        baud_rate=115200,
    )
    fp = EngineeringFastPath(hardware_memory=hardware_memory)
    res = await fp.try_handle("listar puertos y hardware")
    assert res is not None
    assert res["handled"] is True
    assert "ESP32_MAIN" in res["response"]
    assert "COM3" in res["response"]


@pytest.mark.asyncio
async def test_fastpath_pin_query(hardware_memory: HardwareMemory):
    hardware_memory.register_device(
        device_id="esp32_arm",
        name="Arm Controller",
        interface="serial",
        port_or_address="COM4",
    )
    hardware_memory.set_pin_mapping(
        device_id="esp32_arm",
        pin_or_gpio="GPIO25",
        function="PWM",
        label="gripper_servo",
    )

    fp = EngineeringFastPath(hardware_memory=hardware_memory)
    
    # Query por dispositivo
    res = await fp.try_handle("qué pin usa esp32_arm")
    assert res is not None
    assert res["handled"] is True
    assert "GPIO25" in res["response"]

    # Query por etiqueta funcional
    res2 = await fp.try_handle("dónde está conectado el gripper")
    assert res2 is not None
    assert res2["handled"] is True
    assert "gripper_servo" in res2["response"]


@pytest.mark.asyncio
async def test_fastpath_procedural_query(hardware_memory: HardwareMemory):
    hardware_memory.record_procedural_command(
        command="pio run -t upload -e esp32dev",
        toolchain="platformio",
        context_tag="firmware_v1",
        success=True,
    )

    fp = EngineeringFastPath(hardware_memory=hardware_memory)
    res = await fp.try_handle("como compilo firmware_v1")
    assert res is not None
    assert res["handled"] is True
    assert "pio run -t upload" in res["response"]


@pytest.mark.asyncio
async def test_fastpath_direct_serial_dispatch(hardware_memory: HardwareMemory, mock_serial_ability):
    abilities_dict = {"serial_comm": mock_serial_ability}
    fp = EngineeringFastPath(hardware_memory=hardware_memory, abilities=abilities_dict)

    res = await fp.try_handle('enviar "PING" a COM4')
    assert res is not None
    assert res["handled"] is True
    assert len(mock_serial_ability.sent_packets) == 1
    assert mock_serial_ability.sent_packets[0]["port"] == "COM4"
    assert mock_serial_ability.sent_packets[0]["data"] == "PING"


@pytest.mark.asyncio
async def test_fastpath_bypasses_unrelated_queries(hardware_memory: HardwareMemory):
    fp = EngineeringFastPath(hardware_memory=hardware_memory)
    res = await fp.try_handle("escribe una función en python para ordenar un array")
    assert res is None  # Deja pasar al LLM
