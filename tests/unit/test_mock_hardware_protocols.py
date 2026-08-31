"""
Unit tests for Hardware Protocols (Serial & MQTT communication abilities).
"""
import pytest
from abilities.serial_comm import SerialCommAbility, PYSERIAL_AVAILABLE
from abilities.mqtt_comm import MQTTCommAbility


@pytest.mark.asyncio
async def test_serial_comm_schema():
    ser = SerialCommAbility()
    assert ser.name == "serial_comm"
    assert ser.domain == "hardware"
    schema = ser.get_schema()
    actions = [s["action"] for s in schema]
    assert "list_ports" in actions
    assert "connect" in actions
    assert "disconnect" in actions
    assert "send_line" in actions
    assert "read_line" in actions
    assert "get_status" in actions


@pytest.mark.asyncio
async def test_serial_comm_list_ports():
    ser = SerialCommAbility()
    res = await ser.execute("list_ports", {})
    assert "data" in res
    assert isinstance(res["data"], list)


@pytest.mark.asyncio
async def test_mqtt_comm_schema():
    mqtt = MQTTCommAbility()
    assert mqtt.name == "mqtt_comm"
    assert mqtt.domain == "hardware"
    schema = mqtt.get_schema()
    actions = [s["action"] for s in schema]
    assert "connect_broker" in actions
    assert "publish" in actions
    assert "subscribe" in actions
    assert "get_status" in actions


@pytest.mark.asyncio
async def test_mqtt_comm_status_disconnected():
    mqtt = MQTTCommAbility()
    res = await mqtt.execute("get_status", {})
    assert res["success"] is True
    assert res["data"]["connected"] is False
