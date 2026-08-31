"""
Unit tests for SkillSynthesizer (AST validation, security guardrails, sandbox testing).
"""
import pytest
from core.skill_synthesizer import SkillSynthesizer
from abilities.registry import AbilityRegistry


def test_extract_code_block():
    markdown_text = """
Aquí está el código de la habilidad:
```python
class TestAbility:
    pass
```
Espero que te sirva.
    """
    clean = SkillSynthesizer.extract_code_block(markdown_text)
    assert clean.startswith("class TestAbility:")
    assert clean.endswith("pass")


def test_ast_validation_valid_skill(valid_skill_code: str):
    valid, msg, meta = SkillSynthesizer.validate_ast(valid_skill_code)
    assert valid is True
    assert meta["class_name"] == "StepperDriverAbility"
    assert "execute" in meta["methods"]
    assert "get_schema" in meta["methods"]


def test_ast_validation_blocks_insecure_imports(malicious_skill_code: str):
    valid, msg, meta = SkillSynthesizer.validate_ast(malicious_skill_code)
    assert valid is False
    assert "Blocked import detected" in msg


def test_ast_validation_missing_ability_subclass():
    plain_class = """
class SimpleClass:
    def execute(self): pass
    def get_schema(self): return []
"""
    valid, msg, meta = SkillSynthesizer.validate_ast(plain_class)
    assert valid is False
    assert "No class inheriting from 'Ability'" in msg


def test_sandbox_testing_valid_skill(valid_skill_code: str):
    success, msg, instance = SkillSynthesizer.test_sandbox(valid_skill_code)
    assert success is True
    assert instance is not None
    assert instance.name == "stepper_driver"
    assert instance.domain == "robotics"
    schema = instance.get_schema()
    assert len(schema) == 2
    assert any(s["action"] == "move_steps" for s in schema)


@pytest.mark.asyncio
async def test_sandbox_instance_execution(valid_skill_code: str):
    success, msg, instance = SkillSynthesizer.test_sandbox(valid_skill_code)
    assert success is True
    assert instance is not None

    # Probar ejecución de acción
    res = await instance.execute("move_steps", {"steps": 100, "direction": "CW"})
    assert res["success"] is True
    assert res["data"]["position"] == 100

    res2 = await instance.execute("get_position", {})
    assert res2["success"] is True
    assert res2["data"]["position"] == 100


def test_save_and_hot_load(valid_skill_code: str, temp_dir, mock_registry: AbilityRegistry):
    custom_dir = temp_dir / "custom_abilities"
    success, msg, name = SkillSynthesizer.save_and_hot_load(
        code=valid_skill_code,
        registry=mock_registry,
        custom_dir=custom_dir,
    )
    assert success is True
    assert name == "stepper_driver"
    assert (custom_dir / "stepper_driver.py").exists()
