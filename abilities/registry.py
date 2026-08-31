"""
WIS Ability Registry - Registro central de habilidades.
========================================
Mantiene el catalogo de habilidades disponibles para el agente WIS.
Permite registrar, consultar y ejecutar habilidades de forma uniforme,
y genera los esquemas que se inyectan en el prompt del LLM.

Soporta hot-load de habilidades desde archivos .py externos,
lo que permite a WIS auto-instalarse nuevas capacidades en tiempo real.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .base import Ability

logger = logging.getLogger("wis.abilities.registry")


class AbilityRegistry:
    """
    Registro central de habilidades.

    Uso tipico:
        registry = AbilityRegistry()
        registry.register(VoiceAbility())
        result = await registry.execute("voice", "speak", {"text": "Hello"})
    """

    def __init__(self):
        # Mapa nombre -> instancia de Ability
        self._abilities: Dict[str, Ability] = {}

    # ------------------------------------------------------------------ #
    # Registro / consulta
    # ------------------------------------------------------------------ #
    def register(self, ability: Ability) -> bool:
        """
        Registra una habilidad. Devuelve True si se registro correctamente.
        Si ya existe una habilidad con el mismo nombre, se ignora (False).
        """
        if not isinstance(ability, Ability):
            return False
        name = ability.name
        if name in self._abilities:
            # Evitar sobreescribir una habilidad ya registrada
            return False
        self._abilities[name] = ability
        return True

    def unregister(self, name: str) -> bool:
        """Elimina una habilidad del registro por nombre."""
        if name in self._abilities:
            del self._abilities[name]
            return True
        return False

    def hot_load_from_path(self, file_path: str) -> List[str]:
        """
        Carga dinamicamente una o mas habilidades desde un archivo .py externo.
        El archivo debe contener al menos una clase que herede de Ability.

        Args:
            file_path: Ruta absoluta al archivo .py con la(s) habilidad(es).

        Returns:
            Lista con los nombres de las habilidades registradas exitosamente.
            Lista vacia si no se encontro ninguna habilidad valida.
        """
        path = Path(file_path)
        if not path.exists() or path.suffix != ".py":
            logger.warning("hot_load_from_path: archivo invalido o no encontrado: %s", file_path)
            return []

        # Crear un nombre de modulo unico basado en la ruta para evitar colisiones
        module_name = f"_wis_custom_{path.stem}_{abs(hash(str(path)))}"

        try:
            # Si el modulo ya fue cargado antes, lo recargamos para reflejar cambios
            if module_name in sys.modules:
                mod = importlib.reload(sys.modules[module_name])
            else:
                spec = importlib.util.spec_from_file_location(module_name, str(path))
                if spec is None or spec.loader is None:
                    logger.error("hot_load_from_path: no se pudo crear spec para '%s'", file_path)
                    return []
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
        except Exception as exc:
            logger.error("hot_load_from_path: fallo al importar '%s': %s", file_path, exc)
            return []

        registered = []
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, Ability)
                and obj is not Ability
            ):
                try:
                    instance = obj()
                    name = instance.name
                    # Si ya existe, lo sobreescribimos (actualizacion de habilidad)
                    if name in self._abilities:
                        logger.info("hot_load: actualizando habilidad existente '%s'", name)
                        self._abilities[name] = instance
                    else:
                        self._abilities[name] = instance
                        logger.info("hot_load: habilidad '%s' registrada exitosamente", name)
                    registered.append(name)
                except Exception as exc:
                    logger.warning("hot_load_from_path: fallo al instanciar '%s': %s", attr_name, exc)

        return registered

    def load_custom_directory(self, custom_dir: Optional[str] = None) -> List[str]:
        """
        Escanea y carga todas las habilidades encontradas en un directorio.
        Por defecto usa 'abilities/custom/' relativo al paquete.

        Returns:
            Lista con los nombres de todas las habilidades cargadas.
        """
        if custom_dir is None:
            # Ruta por defecto: abilities/custom/ junto a este archivo
            base = Path(__file__).resolve().parent
            custom_dir = base / "custom"

        custom_path = Path(custom_dir)
        if not custom_path.exists():
            return []

        all_loaded = []
        for py_file in sorted(custom_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            loaded = self.hot_load_from_path(str(py_file))
            all_loaded.extend(loaded)

        if all_loaded:
            logger.info("load_custom_directory: %d habilidad(es) cargada(s): %s", len(all_loaded), all_loaded)
        return all_loaded

    def get(self, name: str) -> Ability:
        """
        Devuelve la habilidad con el nombre indicado.
        Lanza KeyError si no existe.
        """
        if name not in self._abilities:
            raise KeyError(f"Habilidad no registrada: '{name}'.")
        return self._abilities[name]

    def has(self, name: str) -> bool:
        """True si la habilidad esta registrada."""
        return name in self._abilities

    def all(self) -> Dict[str, Ability]:
        """Devuelve un dict con todas las habilidades registradas."""
        return dict(self._abilities)

    def names(self) -> List[str]:
        """Lista los nombres de las habilidades registradas."""
        return list(self._abilities.keys())

    # ------------------------------------------------------------------ #
    # Esquemas para el LLM
    # ------------------------------------------------------------------ #
    def get_schemas(self) -> list:
        """
        Recopila los esquemas de todas las habilidades registradas.
        Pensado para inyectarse en el prompt del LLM.
        Estructura de cada entrada:
            {
                "skill": str,          # nombre de la habilidad
                "domain": str,         # dominio (voice, vision, ...)
                "description": str,    # descripcion general
                "actions": list,       # esquema de acciones (de ability.get_schema())
            }
        """
        schemas = []
        for name, ability in self._abilities.items():
            schemas.append({
                "skill": name,
                "domain": ability.domain,
                "description": ability.description,
                "actions": ability.get_schema(),
            })
        return schemas

    # ------------------------------------------------------------------ #
    # Ejecucion delegada
    # ------------------------------------------------------------------ #
    async def execute(self, skill: str, action: str, params: dict) -> dict:
        """
        Ejecuta una accion de una habilidad registrada.
        Con auto-inferencia tipo Avrora si el LLM omite 'action' o confunde 'skill'.
        """
        target_ability: Optional[Ability] = None
        params_dict = params or {}

        # Auto-inferir 'action' si falta segun las claves presentes (Inferencia Avrora)
        action_norm = (action or "").strip().lower()
        if not action_norm:
            if "app_name" in params_dict or "application" in params_dict:
                action_norm = "open_application"
            elif "command" in params_dict:
                action_norm = "execute_shell"
            elif "script" in params_dict:
                action_norm = "execute_powershell"
            elif "code" in params_dict:
                action_norm = "run_python_code"
            elif "query" in params_dict:
                action_norm = "web_search"
            elif "level" in params_dict or "volume" in params_dict:
                action_norm = "set_volume"

        action = action_norm or action

        if skill and self.has(skill):
            target_ability = self._abilities[skill]
        else:
            # Buscar que habilidad soporta esta accion
            for ab in self._abilities.values():
                supported_actions = [a.get("action", "").lower() for a in ab.get_schema() if isinstance(a, dict)]
                if action_norm in supported_actions or action_norm == ab.name.lower() or action_norm == ab.domain.lower():
                    target_ability = ab
                    break

        if target_ability is None:
            return {
                "success": False,
                "data": None,
                "message": f"Habilidad/accion '{skill or action}' no registrada. Disponibles: {self.names()}.",
            }

        try:
            return await target_ability.execute(action, params_dict)
        except Exception as exc:
            return {
                "success": False,
                "data": None,
                "message": f"Excepcion al ejecutar '{action}': {exc}",
            }

    # ------------------------------------------------------------------ #
    # Helpers de instanciacion por defecto
    # ------------------------------------------------------------------ #
    @classmethod
    def with_defaults(cls) -> "AbilityRegistry":
        """
        Crea un registro con el set de habilidades por defecto de WIS.
        Las dependencias opcionales se cargan de forma perezosa dentro
        de cada habilidad, por lo que esto no falla aunque falten librerias.
        """
        registry = cls()
        from .voice import VoiceAbility
        from .listen import ListenAbility
        from .vision import VisionAbility
        from .system import SystemAbility
        from .knowledge import KnowledgeAbility

        registry.register(VoiceAbility())
        registry.register(ListenAbility())
        registry.register(VisionAbility())
        registry.register(SystemAbility())
        registry.register(KnowledgeAbility())

        # Habilidades avanzadas
        try:
            from .file_manager import FileManagerAbility
            registry.register(FileManagerAbility())
        except Exception:
            pass
        try:
            from .browser import BrowserAbility
            registry.register(BrowserAbility())
        except Exception:
            pass
        try:
            from .desktop import DesktopAbility
            registry.register(DesktopAbility())
        except Exception:
            pass
        try:
            from .web_search import WebSearchAbility
            registry.register(WebSearchAbility())
        except Exception:
            pass

        # Meta-programacion: BuilderAbility permite a WIS crear nuevas habilidades
        try:
            from .builder import BuilderAbility
            registry.register(BuilderAbility(registry))
        except Exception as e:
            logger.warning("No se pudo cargar BuilderAbility: %s", e)

        # Autodescubrimiento: DiscoveryAbility coordina nuevas conexiones
        try:
            from .discovery import DiscoveryAbility
            registry.register(DiscoveryAbility(registry))
        except Exception as e:
            logger.warning("No se pudo cargar DiscoveryAbility: %s", e)

        # Protocolos Hardware Nativos (Serial/UART + MQTT)
        try:
            from .serial_comm import SerialCommAbility
            registry.register(SerialCommAbility())
        except Exception as e:
            logger.warning("No se pudo cargar SerialCommAbility: %s", e)

        try:
            from .mqtt_comm import MQTTCommAbility
            registry.register(MQTTCommAbility())
        except Exception as e:
            logger.warning("No se pudo cargar MQTTCommAbility: %s", e)

        # Cargar habilidades custom generadas dinamicamente (robots, IoT, etc.)
        registry.load_custom_directory()

        return registry

    def __len__(self) -> int:
        return len(self._abilities)

    def __repr__(self) -> str:
        return f"<AbilityRegistry count={len(self)} skills={self.names()}>"
