"""
WIS Skill Synthesizer & Sandbox Engine.
========================================
Motor de meta-programación y auto-síntesis de habilidades:
1. Validación estática (AST) y guardrails de seguridad.
2. Sandbox de prueba en memoria (dry-run & schema validation).
3. Hot-Reload en caliente hacia el AbilityRegistry.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from abilities.base import Ability

logger = logging.getLogger("wis.core.skill_synthesizer")

_BLOCKED_IMPORTS = {
    "ctypes", "winreg", "winsound", "_ctypes", "_winapi",
    "mmap", "cffi", "pty", "shlex",
}


class SynthesisValidationError(Exception):
    """Error emitido cuando el código generado no cumple el contrato de seguridad o arquitectura."""
    pass


class SkillSynthesizer:
    """Validador, tester en sandbox y cargador en caliente de habilidades para WIS."""

    @staticmethod
    def extract_code_block(text: str) -> str:
        """Extrae el bloque de código Python si el LLM incluyó markdown."""
        text = text.strip()
        if "```python" in text:
            start = text.find("```python") + len("```python")
            end = text.find("```", start)
            return text[start:end].strip() if end != -1 else text[start:].strip()
        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            return text[start:end].strip() if end != -1 else text[start:].strip()
        return text

    @classmethod
    def validate_ast(cls, code: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Valida la sintaxis del código con AST y verifica las reglas de seguridad y arquitectura.
        Retorna (is_valid, error_msg, metadata).
        """
        clean_code = cls.extract_code_block(code)
        try:
            tree = ast.parse(clean_code)
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}", {}

        # 1. Verificar imports bloqueados
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in _BLOCKED_IMPORTS:
                        return False, f"Blocked import detected: '{alias.name}'", {}
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in _BLOCKED_IMPORTS:
                        return False, f"Blocked import detected: 'from {node.module}'", {}

        # 2. Buscar clases que hereden de Ability
        ability_classes = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = getattr(base, "id", None) or getattr(base, "attr", None)
                    if base_name in ("Ability", "BaseAbility"):
                        ability_classes.append(node)

        if not ability_classes:
            return False, "No class inheriting from 'Ability' found in code", {}

        target_class = ability_classes[0]
        methods = {n.name: n for n in target_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

        # Verificar metodos requeridos
        if "execute" not in methods:
            return False, f"Class '{target_class.name}' must implement 'execute(self, action: str, params: dict)'", {}
        if "get_schema" not in methods:
            return False, f"Class '{target_class.name}' must implement 'get_schema(self)'", {}

        meta = {
            "class_name": target_class.name,
            "methods": list(methods.keys()),
            "clean_code": clean_code,
        }
        return True, "AST validation passed", meta

    @classmethod
    def test_sandbox(
        cls,
        code: str,
        init_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Ability]]:
        """
        Instancia la habilidad en un módulo virtual aislado en memoria y valida su esquema.
        Retorna (success, message, instance).
        """
        valid, msg, meta = cls.validate_ast(code)
        if not valid:
            return False, f"AST Validation failed: {msg}", None

        clean_code = meta["clean_code"]
        class_name = meta["class_name"]

        # Crear namespace aislado
        mod_name = f"_sandbox_ability_{hash(clean_code) & 0xFFFFFFFF}"
        virtual_mod = types.ModuleType(mod_name)
        virtual_mod.__file__ = f"<sandbox:{mod_name}>"

        # Proveer base Ability
        virtual_mod.Ability = Ability  # type: ignore

        try:
            # Ejecutar codigo en el modulo virtual
            exec(clean_code, virtual_mod.__dict__)
            ability_cls = getattr(virtual_mod, class_name, None)
            if not ability_cls or not issubclass(ability_cls, Ability):
                return False, f"Exported symbol '{class_name}' is not an Ability subclass", None

            # Instanciar
            kwargs = init_kwargs or {}
            try:
                instance: Ability = ability_cls(**kwargs)
            except TypeError:
                instance = ability_cls()

            # Validar propiedades requeridas
            if not instance.name or not isinstance(instance.name, str):
                return False, "Instance 'name' property must be a non-empty string", None
            if not instance.description:
                return False, "Instance 'description' property must not be empty", None

            # Validar get_schema()
            schema = instance.get_schema()
            if not isinstance(schema, list):
                return False, "'get_schema()' must return a list of action dictionaries", None
            for item in schema:
                if not isinstance(item, dict) or "action" not in item or "description" not in item:
                    return False, f"Invalid schema item format: {item}", None

            return True, f"Sandbox test passed for '{instance.name}' ({len(schema)} actions)", instance

        except Exception as exc:
            logger.exception("Sandbox test exception: %s", exc)
            return False, f"Sandbox runtime error: {exc}", None

    @classmethod
    def save_and_hot_load(
        cls,
        code: str,
        registry: Any,
        custom_dir: Optional[Path] = None,
        filename_override: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Valida, testea en sandbox, guarda en abilities/custom/ y recarga en el AbilityRegistry.
        Retorna (success, message, ability_name).
        """
        valid, msg, instance = cls.test_sandbox(code)
        if not valid or instance is None:
            return False, f"Sandbox rejected skill: {msg}", None

        ability_name = instance.name
        clean_code = cls.extract_code_block(code)

        # Ruta de destino
        target_dir = custom_dir or (Path(__file__).resolve().parent.parent / "abilities" / "custom")
        target_dir.mkdir(parents=True, exist_ok=True)

        file_name = filename_override or f"{ability_name}.py"
        file_path = target_dir / file_name

        try:
            file_path.write_text(clean_code, encoding="utf-8")
            logger.info("Saved custom skill to %s", file_path)

            # Hot-load en el registry
            if hasattr(registry, "hot_load_from_path"):
                registered_names = registry.hot_load_from_path(str(file_path))
                if ability_name in registered_names or len(registered_names) > 0:
                    return True, f"Skill '{ability_name}' synthesized and hot-loaded successfully into runtime.", ability_name
                else:
                    return False, f"File saved to {file_path}, but registry failed to load it.", ability_name
            else:
                # Registro directo si no tiene hot_load_from_path
                registry.register(instance)
                return True, f"Skill '{ability_name}' registered directly.", ability_name

        except Exception as exc:
            logger.exception("Error saving and hot-loading skill: %s", exc)
            return False, f"File write / hot-load error: {exc}", ability_name
