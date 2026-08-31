"""
Unit tests for Hot-Reload and Dynamic Ability Lifecycle in AbilityRegistry.
"""
import pytest
from pathlib import Path
from abilities.registry import AbilityRegistry
from abilities.base import Ability


def test_registry_register_and_unregister(mock_registry: AbilityRegistry):
    class DummyAbility(Ability):
        @property
        def name(self) -> str: return "dummy"
        @property
        def description(self) -> str: return "Dummy"
        @property
        def domain(self) -> str: return "test"
        async def execute(self, action: str, params: dict) -> dict: return {"success": True}
        def get_schema(self) -> list: return []

    dummy = DummyAbility()
    assert mock_registry.register(dummy) is True
    assert "dummy" in mock_registry.all()
    assert mock_registry.get("dummy") == dummy

    # No permite sobreescritura accidental sin unregister
    assert mock_registry.register(dummy) is False

    # Unregister
    assert mock_registry.unregister("dummy") is True
    assert "dummy" not in mock_registry.all()


def test_hot_load_from_path(mock_registry: AbilityRegistry, temp_dir: Path, valid_skill_code: str):
    skill_file = temp_dir / "stepper_driver.py"
    skill_file.write_text(valid_skill_code, encoding="utf-8")

    loaded = mock_registry.hot_load_from_path(str(skill_file))
    assert "stepper_driver" in loaded
    assert "stepper_driver" in mock_registry.all()

    inst = mock_registry.get("stepper_driver")
    assert inst is not None
    assert inst.name == "stepper_driver"
    assert inst.domain == "robotics"


def test_hot_load_invalid_file(mock_registry: AbilityRegistry, temp_dir: Path):
    non_py = temp_dir / "test.txt"
    non_py.write_text("not a python file", encoding="utf-8")
    assert mock_registry.hot_load_from_path(str(non_py)) == []
