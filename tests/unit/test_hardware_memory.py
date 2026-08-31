"""
Unit tests for HardwareMemory (Device Graph, Pinout Maps, Procedural Commands).
"""
import pytest
from core.hardware_memory import HardwareMemory


def test_device_registration_and_retrieval(hardware_memory: HardwareMemory):
    # 1. Registrar dispositivo
    ok = hardware_memory.register_device(
        device_id="esp32_robot",
        name="ESP32-S3 Robot Controller",
        interface="serial",
        port_or_address="COM4",
        baud_rate=115200,
        protocol="cobs",
        description="Main motion controller",
        config={"voltage": 3.3, "firmware": "v1.2.0"},
    )
    assert ok is True

    # 2. Obtener dispositivo
    dev = hardware_memory.get_device("esp32_robot")
    assert dev is not None
    assert dev["device_id"] == "esp32_robot"
    assert dev["port_or_address"] == "COM4"
    assert dev["baud_rate"] == 115200
    assert dev["protocol"] == "cobs"
    assert dev["config"]["firmware"] == "v1.2.0"

    # 3. Listar por interfaz
    serial_devs = hardware_memory.list_devices(interface="serial")
    assert len(serial_devs) == 1
    assert serial_devs[0]["device_id"] == "esp32_robot"

    mqtt_devs = hardware_memory.list_devices(interface="mqtt")
    assert len(mqtt_devs) == 0


def test_device_upsert_and_deletion(hardware_memory: HardwareMemory):
    hardware_memory.register_device(
        device_id="sensor_bme",
        name="BME280 Environment Sensor",
        interface="i2c",
        port_or_address="0x76",
        baud_rate=400000,
    )
    # Actualizar estado a activo
    hardware_memory.register_device(
        device_id="sensor_bme",
        name="BME280 Environment Sensor",
        interface="i2c",
        port_or_address="0x76",
        status="active",
    )
    dev = hardware_memory.get_device("sensor_bme")
    assert dev["status"] == "active"

    # Eliminar
    deleted = hardware_memory.delete_device("sensor_bme")
    assert deleted is True
    assert hardware_memory.get_device("sensor_bme") is None


def test_pinout_mapping_and_search(hardware_memory: HardwareMemory):
    hardware_memory.register_device(
        device_id="esp32_node",
        name="ESP32 Board",
        interface="serial",
        port_or_address="COM5",
    )

    # Mapear pines
    hardware_memory.set_pin_mapping(
        device_id="esp32_node",
        pin_or_gpio="GPIO18",
        function="PWM",
        label="servo_gripper",
        signal_type="pwm",
        notes="50Hz PWM signal for servo",
    )
    hardware_memory.set_pin_mapping(
        device_id="esp32_node",
        pin_or_gpio="GPIO21",
        function="I2C_SDA",
        label="i2c_bus_sda",
        signal_type="bus",
    )

    # Recuperar pines por dispositivo
    pins = hardware_memory.get_pin_map("esp32_node")
    assert len(pins) == 2
    assert any(p["pin_or_gpio"] == "GPIO18" for p in pins)

    # Búsqueda por etiqueta funcional (ej: 'gripper' o 'servo')
    search_results = hardware_memory.find_pin_by_label("servo")
    assert len(search_results) == 1
    assert search_results[0]["label"] == "servo_gripper"
    assert search_results[0]["device_id"] == "esp32_node"


def test_procedural_command_memory(hardware_memory: HardwareMemory):
    # Registrar comandos con diferentes éxitos y fallos
    cmd = "pio run -t upload -e esp32dev"
    hardware_memory.record_procedural_command(
        command=cmd,
        toolchain="platformio",
        context_tag="esp32_robot",
        success=True,
        duration_ms=4500.0,
    )
    hardware_memory.record_procedural_command(
        command=cmd,
        toolchain="platformio",
        context_tag="esp32_robot",
        success=True,
        duration_ms=4200.0,
    )

    best = hardware_memory.get_best_procedural_command(
        toolchain="platformio",
        context_tag="esp32_robot",
    )
    assert best is not None
    assert best["command"] == cmd
    assert best["success_count"] == 2
    assert best["failure_count"] == 0
    assert best["avg_duration_ms"] > 4000.0


def test_topology_summary(hardware_memory: HardwareMemory):
    hardware_memory.register_device(
        device_id="rpi_gateway",
        name="Raspberry Pi Gateway",
        interface="mqtt",
        port_or_address="192.168.1.100",
    )
    summary = hardware_memory.query_topology_summary()
    assert summary["total_devices"] == 1
    assert summary["devices"][0]["id"] == "rpi_gateway"
