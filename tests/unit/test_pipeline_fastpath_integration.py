"""
Unit tests for ActionPipeline integrated with FastPath and HardwareMemory.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.pipeline import ActionPipeline
from core.fastpath import EngineeringFastPath
from core.hardware_memory import HardwareMemory
from core.skill_memory import SkillMemory
from core.reasoning import ReasoningEngine


@pytest.mark.asyncio
async def test_pipeline_fastpath_priority(hardware_memory: HardwareMemory):
    # Registrar un dispositivo
    hardware_memory.register_device(
        device_id="stm32_board",
        name="STM32F4 Nucleo",
        interface="serial",
        port_or_address="COM8",
        baud_rate=115200,
    )

    fastpath = EngineeringFastPath(hardware_memory=hardware_memory)
    mock_reasoning = MagicMock(spec=ReasoningEngine)
    mock_reasoning.think = AsyncMock()
    mock_skill_mem = MagicMock(spec=SkillMemory)
    mock_skill_mem.lookup.return_value = None

    pipeline = ActionPipeline(
        reasoning=mock_reasoning,
        skill_memory=mock_skill_mem,
        fastpath=fastpath,
        hardware_memory=hardware_memory,
    )

    # Consulta que debe ser interceptada por FastPath
    result = await pipeline.process("listar dispositivos")
    assert result["path_used"] == "fastpath"
    assert result["success"] is True
    assert "STM32_BOARD" in result["response"]
    # ReasoningEngine no debió ser llamado (0 llamadas al LLM)
    mock_reasoning.think.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_fallback_to_reflexive(hardware_memory: HardwareMemory):
    fastpath = EngineeringFastPath(hardware_memory=hardware_memory)
    mock_reasoning = MagicMock(spec=ReasoningEngine)
    mock_reasoning.think = AsyncMock()
    mock_skill_mem = MagicMock(spec=SkillMemory)

    pipeline = ActionPipeline(
        reasoning=mock_reasoning,
        skill_memory=mock_skill_mem,
        fastpath=fastpath,
        hardware_memory=hardware_memory,
    )

    # Saludo estándar -> interceptado por Path 1 (Reflexive)
    result = await pipeline.process("hola")
    assert result["path_used"] == "reflexive"
    assert "Hello" in result["response"]
    mock_reasoning.think.assert_not_called()
