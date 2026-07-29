"""
WIS Builder Ability - Meta-programacion: WIS crea sus propias habilidades.
==========================================================================
Permite a WIS escribir, validar e instalar nuevas habilidades en tiempo real.

Flujo completo:
  1. El usuario (o DiscoveryAbility) describe que necesita WIS para controlar
     un dispositivo o integrar un sistema.
  2. BuilderAbility usa el LLM para escribir una clase Ability completa.
  3. Valida sintaxis y reglas de seguridad (imports bloqueados, etc.).
  4. Instala dependencias pip si el codigo las requiere.
  5. Guarda el archivo en abilities/custom/<nombre>.py
  6. Instruye al AbilityRegistry para hot-load del nuevo modulo.
  7. WIS puede usar la nueva habilidad inmediatamente, sin reiniciarse.

Principio WIS: No se programa la solucion — WIS la programa sola.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import Ability

if TYPE_CHECKING:
    from .registry import AbilityRegistry

logger = logging.getLogger("wis.abilities.builder")

# --------------------------------------------------------------------------- #
# Seguridad: imports absolutamente prohibidos en habilidades generadas
# --------------------------------------------------------------------------- #
_BLOCKED_IMPORTS = {
    "ctypes", "winreg", "winsound", "_ctypes", "_winapi",
    "mmap", "cffi", "pty", "shlex",
    # No se bloquean os/sys/subprocess porque algunas habilidades roboticas
    # legitimas los necesitan, pero se registran en los logs
}
_WARN_IMPORTS = {"subprocess", "os", "sys"}

# --------------------------------------------------------------------------- #
# Prompt de arquitectura para el LLM
# --------------------------------------------------------------------------- #
_BUILDER_PROMPT = """\
Eres el Arquitecto de Habilidades de WIS (Wisdom Integrated System).
WIS es un agente cognitivo que controla robots y dispositivos IoT.
Tu tarea es crear una HABILIDAD nueva para WIS segun estas especificaciones:

=== ESPECIFICACIONES ===
Nombre de la habilidad (snake_case): {ability_name}
Descripcion del requerimiento:
{requirements}

=== CONTRATO OBLIGATORIO ===
1. El archivo debe ser Python puro. Sin markdown. Si usas markdown, pon TODO en un bloque ```python ... ```.
2. Debe importar Ability: `from abilities.base import Ability`
3. Debe declarar UNA clase que herede de Ability con estos @property abstractos implementados:
   - name -> str          (exactamente: "{ability_name}")
   - description -> str   (descripcion clara de que hace)
   - domain -> str        (ej: "robot", "iot", "voice", "vision", "system")
4. Debe implementar:
   - async def execute(self, action: str, params: dict) -> dict:
       Siempre retorna: {{"success": bool, "data": Any, "message": str}}
   - def get_schema(self) -> list:
       Lista de {{"action": str, "description": str, "params": dict}}
5. Si necesita librerias externas, agrega al inicio: `# REQUIRES: lib1, lib2`
6. Usa try/except en toda operacion de red o IO. Nunca dejes excepciones sin manejar.
7. Documenta cada metodo con docstrings en espanol.

=== REGLAS DE SEGURIDAD ===
- NO importes: ctypes, winreg, winsound, mmap, cffi, pty, shlex
- Valida siempre los parametros antes de usarlos
- Usa timeouts en toda conexion de red

=== ESTRUCTURA DE EJEMPLO ===
```python
# REQUIRES: requests
from __future__ import annotations
from abilities.base import Ability

class EjemploAbility(Ability):
    \"\"\"Habilidad de ejemplo para conectar un dispositivo HTTP.\"\"\"

    def __init__(self, ip: str = "", port: int = 80):
        self._ip = ip
        self._port = port

    @property
    def name(self) -> str:
        return "ejemplo"

    @property
    def description(self) -> str:
        return "Controla un dispositivo via HTTP."

    @property
    def domain(self) -> str:
        return "iot"

    def get_schema(self) -> list:
        return [
            {{"action": "encender", "description": "Enciende el dispositivo", "params": {{"ip": "string"}}}},
            {{"action": "apagar",   "description": "Apaga el dispositivo",    "params": {{"ip": "string"}}}},
        ]

    async def execute(self, action: str, params: dict) -> dict:
        import asyncio, requests
        ip = params.get("ip", self._ip) or self._ip
        if not ip:
            return {{"success": False, "data": None, "message": "Falta la IP del dispositivo."}}
        try:
            if action == "encender":
                r = await asyncio.to_thread(requests.get, f"http://{{ip}}/on", timeout=5)
                return {{"success": r.status_code == 200, "data": r.text, "message": "Dispositivo encendido."}}
            elif action == "apagar":
                r = await asyncio.to_thread(requests.get, f"http://{{ip}}/off", timeout=5)
                return {{"success": r.status_code == 200, "data": r.text, "message": "Dispositivo apagado."}}
            else:
                return {{"success": False, "data": None, "message": f"Accion desconocida: {{action}}"}}
        except Exception as exc:
            return {{"success": False, "data": None, "message": f"Error: {{exc}}"}}
```

Escribe AHORA el codigo Python perfecto para la habilidad "{ability_name}":
"""

# --------------------------------------------------------------------------- #
# Nombre valido para una habilidad
# --------------------------------------------------------------------------- #
_ABILITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
_RESERVED_NAMES = {
    "base", "registry", "builder", "discovery", "core", "system",
    "voice", "vision", "listen", "knowledge", "desktop", "browser",
    "file_manager", "web_search",
}


class BuilderAbility(Ability):
    """
    Meta-programacion para WIS.
    Crea, valida e instala nuevas habilidades en tiempo real usando el LLM.
    """

    def __init__(self, registry: "AbilityRegistry") -> None:
        self._registry = registry
        # Directorio donde se guardan las habilidades generadas
        self._custom_dir = Path(__file__).resolve().parent / "custom"
        self._custom_dir.mkdir(exist_ok=True)
        # Asegurar que custom/ sea un paquete Python
        init = self._custom_dir / "__init__.py"
        if not init.exists():
            init.write_text("# WIS custom abilities — auto-generated\n", encoding="utf-8")
        # Historial de generacion de habilidades para auto-construccion
        self._history_path = self._custom_dir / "builder_history.json"
        self._history = self._load_history()

    # ------------------------------------------------------------------ #
    # Contrato Ability
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "builder"

    @property
    def description(self) -> str:
        return (
            "Meta-programador de WIS. Crea e instala nuevas habilidades en tiempo real. "
            "Usa el LLM para escribir codigo Python, lo valida, instala dependencias y "
            "lo registra sin necesidad de reiniciar el sistema."
        )

    @property
    def domain(self) -> str:
        return "meta"

    def get_schema(self) -> list:
        return [
            {
                "action": "create_ability",
                "description": (
                    "Crea una nueva habilidad personalizada para WIS. "
                    "WIS escribira el codigo, lo validara y lo instalara automaticamente. "
                    "Usar cuando se quiera conectar un robot, un dispositivo IoT, o cualquier "
                    "sistema nuevo que WIS no sepa controlar todavia."
                ),
                "params": {
                    "ability_name": "string — nombre en snake_case (ej: 'nao_robot', 'zigbee_light')",
                    "requirements": "string — descripcion detallada de que debe hacer la habilidad y como conectarse al dispositivo",
                    "overwrite": "bool (opcional) — sobreescribir si ya existe. Default: false",
                },
            },
            {
                "action": "list_custom",
                "description": "Lista todas las habilidades custom instaladas dinamicamente.",
                "params": {},
            },
            {
                "action": "delete_ability",
                "description": "Elimina una habilidad custom del sistema.",
                "params": {
                    "ability_name": "string — nombre de la habilidad a eliminar",
                },
            },
            {
                "action": "get_history",
                "description": "Muestra el historial de creacion de habilidades generadas por WIS.",
                "params": {},
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        """Ejecuta acciones del meta-programador."""
        action = (action or "").strip().lower()

        if action == "create_ability":
            return await self._create_ability(params)
        elif action == "list_custom":
            return self._list_custom()
        elif action == "delete_ability":
            return self._delete_ability(params)
        elif action == "get_history":
            return {"success": True, "data": self._history, "message": f"Historial de {len(self._history)} creaciones de habilidades."}
        else:
            return {
                "success": False,
                "data": None,
                "message": f"Accion desconocida: '{action}'. Disponibles: create_ability, list_custom, delete_ability, get_history",
            }

    # ------------------------------------------------------------------ #
    # Logica interna
    # ------------------------------------------------------------------ #
    async def _create_ability(self, params: dict) -> dict:
        """Pipeline completo: LLM → validar → instalar deps → hot-load."""
        import asyncio

        ability_name = self._normalize_name(params.get("ability_name", ""))
        requirements = str(params.get("requirements", "")).strip()
        overwrite = bool(params.get("overwrite", False))

        # ── Validaciones previas ────────────────────────────────────────
        if not ability_name:
            return {"success": False, "data": None, "message": "Falta 'ability_name'. Usa snake_case, ej: 'nao_robot'."}
        if not _ABILITY_NAME_RE.match(ability_name):
            return {"success": False, "data": None, "message": f"Nombre invalido: '{ability_name}'. Usa snake_case, 3-49 chars, empieza por letra."}
        if ability_name in _RESERVED_NAMES:
            return {"success": False, "data": None, "message": f"Nombre reservado: '{ability_name}'. Elige otro nombre descriptivo."}
        if not requirements:
            return {"success": False, "data": None, "message": "Falta 'requirements'. Describe que debe hacer la habilidad y como conectarse."}

        target_file = self._custom_dir / f"{ability_name}.py"
        history = {
            "ability_name": ability_name,
            "requirements": requirements[:1200],
            "file": str(target_file),
            "requested_overwrite": overwrite,
            "timestamp": self._current_timestamp(),
            "build_success": False,
            "status": "started",
            "message": "",
            "dependencies": [],
        }

        if target_file.exists() and not overwrite:
            history.update({
                "status": "blocked",
                "message": f"La habilidad '{ability_name}' ya existe en {target_file}."
            })
            self._record_history(history)
            return {
                "success": False,
                "data": str(target_file),
                "message": f"La habilidad '{ability_name}' ya existe en {target_file}. Pide sobreescribirla con overwrite=true si quieres actualizarla.",
            }

        if self._registry.has(ability_name) and not overwrite:
            history.update({
                "status": "blocked",
                "message": f"La habilidad '{ability_name}' ya se encuentra registrada en el sistema."
            })
            self._record_history(history)
            return {
                "success": False,
                "data": None,
                "message": f"La habilidad '{ability_name}' ya se encuentra registrada en el sistema. Usa overwrite=true para reemplazarla.",
            }

        logger.info("BuilderAbility: iniciando forja de '%s'...", ability_name)

        # ── 1. Generar codigo con el LLM ────────────────────────────────
        code_raw = await asyncio.to_thread(self._call_llm, ability_name, requirements)
        if not code_raw:
            history.update({"status": "failed", "message": "El LLM no genero codigo. Verifica la configuracion de la API (Keys.env)."})
            self._record_history(history)
            return {"success": False, "data": None, "message": "El LLM no genero codigo. Verifica la configuracion de la API (Keys.env)."}

        # ── 2. Extraer y limpiar codigo ─────────────────────────────────
        code = self._extract_code(code_raw)
        if not code:
            history.update({"status": "failed", "message": "El LLM genero una respuesta sin codigo Python valido."})
            self._record_history(history)
            return {"success": False, "data": None, "message": "El LLM genero una respuesta sin codigo Python valido."}

        # ── 3. Validar sintaxis ─────────────────────────────────────────
        try:
            compile(code, f"<ability:{ability_name}>", "exec")
        except SyntaxError as exc:
            history.update({
                "status": "failed",
                "message": f"Error de sintaxis en el codigo generado: {exc}",
            })
            self._record_history(history)
            return {"success": False, "data": code, "message": f"Error de sintaxis en el codigo generado: {exc}"}

        # ── 4. Validar seguridad ────────────────────────────────────────
        sec_error = self._validate_security(code)
        if sec_error:
            history.update({"status": "failed", "message": sec_error})
            self._record_history(history)
            return {"success": False, "data": code, "message": sec_error}

        # ── 5. Validar contrato Ability ─────────────────────────────────
        contract_error = self._validate_contract(code, ability_name)
        if contract_error:
            history.update({"status": "failed", "message": contract_error})
            self._record_history(history)
            return {"success": False, "data": code, "message": contract_error}

        # ── 6. Instalar dependencias pip si es necesario ────────────────
        deps = self._extract_dependencies(code)
        history["dependencies"] = deps
        if deps:
            dep_result = await asyncio.to_thread(self._install_deps, deps)
            if not dep_result["success"]:
                history.update({"status": "failed", "message": dep_result["message"]})
                self._record_history(history)
                return dep_result

        # ── 7. Guardar en disco ─────────────────────────────────────────
        try:
            target_file.write_text(code, encoding="utf-8", newline="\n")
            logger.info("BuilderAbility: '%s' guardado en %s", ability_name, target_file)
        except Exception as exc:
            history.update({"status": "failed", "message": f"Error al guardar el archivo: {exc}"})
            self._record_history(history)
            return {"success": False, "data": None, "message": f"Error al guardar el archivo: {exc}"}

        # ── 8. Hot-load en el registry ──────────────────────────────────
        loaded = self._registry.hot_load_from_path(str(target_file))
        if not loaded:
            history.update({
                "status": "partial",
                "build_success": True,
                "message": "Habilidad generada y guardada, pero el hot-load fallo.",
                "loaded_names": [],
            })
            self._record_history(history)
            return {
                "success": True,
                "data": str(target_file),
                "message": (
                    f"Habilidad '{ability_name}' generada y guardada en {target_file}, "
                    "pero el hot-load fallo. WIS podra usarla despues de reiniciarse."
                ),
            }

        logger.info("BuilderAbility: '%s' lista y activa.", ability_name)
        deps_msg = f" Dependencias instaladas: {deps}." if deps else ""
        history.update({
            "status": "completed",
            "build_success": True,
            "message": "Habilidad creada e instalada exitosamente.",
            "loaded_names": loaded,
        })
        self._record_history(history)

        available_actions = []
        try:
            if self._registry.has(ability_name):
                available_actions = [s.get("action") for s in self._registry.get(ability_name).get_schema() if isinstance(s, dict)]
        except Exception:
            available_actions = []

        return {
            "success": True,
            "data": {
                "ability_name": ability_name,
                "file": str(target_file),
                "loaded_names": loaded,
                "dependencies": deps,
            },
            "message": (
                f"¡Habilidad '{ability_name}' creada e instalada exitosamente!{deps_msg} "
                f"WIS ya puede usarla ahora mismo. Prueba con las acciones: {available_actions}"
                if self._registry.has(ability_name) else
                f"¡Habilidad '{ability_name}' creada exitosamente!{deps_msg} Archivo: {target_file}"
            ),
        }

    def _list_custom(self) -> dict:
        """Lista todas las habilidades en el directorio custom/."""
        abilities = []
        for py_file in sorted(self._custom_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = py_file.stem
            is_loaded = self._registry.has(name)
            entry = {"name": name, "file": str(py_file), "active": is_loaded}
            if is_loaded:
                try:
                    ability = self._registry.get(name)
                    entry["description"] = ability.description
                    entry["domain"] = ability.domain
                    entry["schema"] = ability.get_schema()
                except Exception:
                    entry["description"] = "(no disponible)"
                    entry["domain"] = "unknown"
                    entry["schema"] = []
            abilities.append(entry)

        return {
            "success": True,
            "data": abilities,
            "message": f"{len(abilities)} habilidad(es) custom encontrada(s)." if abilities else "No hay habilidades custom instaladas aun.",
        }

    def _delete_ability(self, params: dict) -> dict:
        """Elimina una habilidad custom del disco y del registry."""
        ability_name = self._normalize_name(params.get("ability_name", ""))
        if not ability_name:
            return {"success": False, "data": None, "message": "Falta 'ability_name'."}

        target_file = self._custom_dir / f"{ability_name}.py"
        if not target_file.exists():
            return {"success": False, "data": None, "message": f"La habilidad '{ability_name}' no existe en custom/."}

        try:
            target_file.unlink()
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al eliminar el archivo: {exc}"}

        # Desregistrar del registry
        self._registry.unregister(ability_name)

        # Limpiar del sys.modules para que un futuro reload no use cache vieja
        module_key = f"wis.abilities.custom.{ability_name}"
        sys.modules.pop(module_key, None)

        return {
            "success": True,
            "data": None,
            "message": f"Habilidad '{ability_name}' eliminada del sistema.",
        }

    # ------------------------------------------------------------------ #
    # Utilidades de generacion y validacion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_name(raw: str) -> str:
        name = str(raw or "").strip().lower()
        name = re.sub(r"[^a-z0-9_]+", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name

    def _call_llm(self, ability_name: str, requirements: str) -> str:
        """Llama al LLM sincrono para generar el codigo de la habilidad."""
        try:
            import httpx
            httpx_available = True
        except ImportError:
            httpx = None
            httpx_available = False

        try:
            import requests as req
        except ImportError:
            req = None

        if not httpx_available and req is None:
            logger.error("BuilderAbility: neither httpx nor requests is installed.")
            return ""

        api_key = (
            os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or ""
        )

        if os.getenv("DEEPSEEK_API_KEY"):
            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        elif os.getenv("OPENAI_API_KEY"):
            base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        elif os.getenv("OPENROUTER_API_KEY"):
            base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
        else:
            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        if not api_key:
            logger.error("BuilderAbility: No hay API key configurada en Keys.env.")
            return ""

        prompt = _BUILDER_PROMPT.format(
            ability_name=ability_name,
            requirements=requirements,
        )

        request_body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }

        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            if httpx_available:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, json=request_body)
                    if resp.status_code == 200:
                        data = resp.json()
                        choice = data.get("choices", [{}])[0]
                        return choice.get("message", {}).get("content", "") or choice.get("content", "") or ""
                    logger.error("BuilderAbility LLM error %d: %s", resp.status_code, resp.text[:200])
            elif req is not None:
                resp = req.post(url, headers=headers, json=request_body, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data.get("choices", [{}])[0]
                    return choice.get("message", {}).get("content", "") or choice.get("content", "") or ""
                logger.error("BuilderAbility LLM error %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("BuilderAbility: fallo al llamar al LLM: %s", exc)
        return ""

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extrae el bloque de codigo Python de una respuesta del LLM."""
        if not text:
            return ""

        # Preferir bloques de codigo triple-backtick que contengan la definicion de la habilidad.
        matches = re.findall(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
        for block in reversed(matches):
            if "class" in block and "Ability" in block:
                return block.strip()
        for block in matches:
            if "from abilities.base import Ability" in block or "class " in block:
                return block.strip()

        # Si no hay bloques, intentar extraer el fragmento a partir de la definicion de la clase.
        idx = text.find("from abilities.base import Ability")
        if idx >= 0:
            return text[idx:].strip()
        idx = text.find("class ")
        if idx >= 0 and "Ability" in text[idx:idx + 200]:
            return text[idx:].strip()

        return ""

    @staticmethod
    def _validate_security(code: str) -> str:
        """Revisa que el codigo no use imports prohibidos. Retorna '' si esta OK."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ""  # ya fue verificado antes

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue

            blocked = sorted(names & _BLOCKED_IMPORTS)
            if blocked:
                return f"[SEGURIDAD] Import prohibido detectado: {', '.join(blocked)}. El codigo no puede usar estos modulos."

            warn = sorted(names & _WARN_IMPORTS)
            if warn:
                logger.warning("BuilderAbility: codigo usa imports sensibles: %s", warn)

        return ""

    @staticmethod
    def _validate_contract(code: str, expected_name: str) -> str:
        """Verifica que el codigo implemente correctamente el contrato de Ability."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return f"Error de sintaxis: {exc}"

        ability_classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {
                    getattr(b, "id", getattr(b, "attr", ""))
                    for b in node.bases
                }
                if "Ability" in bases:
                    ability_classes.append(node)

        if not ability_classes:
            return "[CONTRATO] El codigo no declara ninguna clase que herede de Ability."
        if len(ability_classes) > 1:
            return "[CONTRATO] El codigo debe declarar una sola clase que herede de Ability."

        cls = ability_classes[0]
        props_found = set()
        methods_found = set()
        declared_name = None

        for item in cls.body:
            if isinstance(item, ast.FunctionDef):
                for dec in item.decorator_list:
                    if (isinstance(dec, ast.Name) and dec.id == "property") or \
                       (isinstance(dec, ast.Attribute) and dec.attr == "property"):
                        props_found.add(item.name)
                        if item.name == "name":
                            for stmt in item.body:
                                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                                    declared_name = stmt.value.value
                methods_found.add(item.name)
            elif isinstance(item, ast.AsyncFunctionDef):
                methods_found.add(item.name)

        missing_props = {"name", "description", "domain"} - props_found
        missing_methods = {"execute", "get_schema"} - methods_found

        if declared_name and declared_name != expected_name:
            return f"[CONTRATO] El nombre de habilidad declarado ('{declared_name}') no coincide con '{expected_name}'."
        if missing_props or missing_methods:
            return (
                f"[CONTRATO] Falta implementar: "
                f"@property: {missing_props or 'OK'}, "
                f"metodos: {missing_methods or 'OK'}."
            )

        if not any(isinstance(item, ast.AsyncFunctionDef) and item.name == "execute" for item in cls.body):
            return "[CONTRATO] El metodo 'execute' debe ser asincrono (async def)."

        return ""

    @staticmethod
    def _extract_dependencies(code: str) -> list:
        """Extrae dependencias de la linea # REQUIRES: ..."""
        match = re.search(r"#\s*REQUIRES:\s*(.*)", code)
        if not match:
            return []

        stdlib = {
            "os", "sys", "json", "re", "time", "datetime", "pathlib", "typing",
            "math", "random", "collections", "itertools", "functools", "sqlite3",
            "csv", "uuid", "hashlib", "base64", "logging", "asyncio", "inspect",
            "importlib", "abc", "enum", "threading", "socket",
        }

        deps = []
        for raw in match.group(1).split(","):
            dep = raw.strip()
            if not dep:
                continue
            package = re.split(r"[<>=!~\[]", dep, maxsplit=1)[0].strip().lower()
            if package in stdlib:
                continue
            if not re.match(r"^[A-Za-z0-9_.\-]+", dep):
                logger.warning("BuilderAbility: dependencia con formato invalido, ignorada: %s", dep)
                continue
            deps.append(dep)
        return deps

    @staticmethod
    def _install_deps(deps: list) -> dict:
        """Instala dependencias via pip. Retorna dict de resultado."""
        logger.info("BuilderAbility: instalando dependencias: %s", deps)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + deps,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("BuilderAbility: dependencias instaladas correctamente.")
                return {"success": True, "data": deps, "message": f"Dependencias instaladas: {deps}"}
            else:
                err = result.stderr[-500:] if result.stderr else "error desconocido"
                return {"success": False, "data": None, "message": f"Error al instalar dependencias {deps}: {err}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "data": None, "message": f"Timeout al instalar dependencias: {deps}"}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Excepcion al instalar dependencias: {exc}"}

    def _load_history(self) -> list[dict]:
        """Carga el historial de builder desde disco."""
        if not self._history_path.exists():
            return []
        try:
            content = self._history_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.warning("BuilderAbility: no se pudo cargar builder_history: %s", exc)
        return []

    def _record_history(self, record: dict) -> None:
        """Guarda un registro de la generacion de habilidad."""
        if not isinstance(record, dict):
            return
        self._history.append(record)
        try:
            self._history_path.write_text(json.dumps(self._history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("BuilderAbility: fallo al guardar builder_history: %s", exc)

    @staticmethod
    def _current_timestamp() -> str:
        import datetime
        return datetime.datetime.utcnow().isoformat() + "Z"
