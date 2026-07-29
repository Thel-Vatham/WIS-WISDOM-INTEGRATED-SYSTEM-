"""WIS Identity - Agent identity, tone, and language loader.

Carga la identidad del agente desde config/identity.md.
Define QUIEN es el agente (nombre, rol, descripcion, idioma de respuesta),
separado de lo que PUEDE hacer (capacidades).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("wis.core.identity")

DEFAULT_LANGUAGE = "en"

_DEFAULT_IDENTITY_MD = """# WIS Identity

**Name:** WIS
**Role:** Cognitive agent for humanoid robots.

## Description
WIS is a cognitive core designed for humanoid robots. It reasons,
remembers and acts safely across multiple modalities.

## Reply Language
en
"""


class Identity:
    """Carga y expone la identidad del agente desde config/identity.md."""

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        identity_path: Optional[Path] = None,
    ) -> None:
        if identity_path:
            self._identity_path = Path(identity_path)
            self._config_dir = self._identity_path.parent
        elif config_dir:
            self._config_dir = Path(config_dir)
            self._identity_path = self._config_dir / "identity.md"
        else:
            self._config_dir = self._default_config_dir()
            self._identity_path = self._config_dir / "identity.md"

        self._raw: str = ""
        self._reply_language: str = DEFAULT_LANGUAGE
        self._name: str = "WIS"
        self._description: str = ""
        self._cached_prompt_section: Optional[str] = None

        self._load()

    def reload(self) -> None:
        self._cached_prompt_section = None
        self._load()

    @staticmethod
    def _default_config_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "config"

    def _load(self) -> None:
        try:
            if self._identity_path.exists():
                self._raw = self._identity_path.read_text(encoding="utf-8")
            else:
                self._raw = _DEFAULT_IDENTITY_MD
                self._ensure_dir()
                try:
                    self._identity_path.write_text(self._raw, encoding="utf-8")
                except OSError:
                    pass
        except OSError:
            self._raw = _DEFAULT_IDENTITY_MD

        self._parse()

    def _ensure_dir(self) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _parse(self) -> None:
        text = self._raw or _DEFAULT_IDENTITY_MD
        lines = text.splitlines()

        self._name = self._extract_field(lines, "Name:", "WIS")
        self._reply_language = self._extract_section(text, "Reply Language", DEFAULT_LANGUAGE)
        self._reply_language = (self._reply_language or DEFAULT_LANGUAGE).strip().lower()[:5]

        self._description = self._extract_section(text, "Description", "").strip()
        if not self._description:
            self._description = "Cognitive agent for humanoid robots."
        self._cached_prompt_section = None

    def _extract_field(self, lines, key: str, default: str) -> str:
        for line in lines:
            if key in line:
                after = line.split(":", 1)[-1]
                return after.replace("*", "").strip() or default
        return default

    def _extract_section(self, text: str, header: str, default: str) -> str:
        marker = f"## {header}"
        idx = text.find(marker)
        if idx < 0:
            return default
        rest = text[idx + len(marker):]
        next_h = rest.find("\n## ")
        if next_h >= 0:
            rest = rest[:next_h]
        return rest.strip() or default

    def get_name(self) -> str:
        return self._name

    def get_description(self) -> str:
        return self._description

    def get_reply_language(self) -> str:
        return self._reply_language

    def get_context(self) -> Dict[str, str]:
        return {
            "name": self._name,
            "description": self._description,
            "reply_language": self._reply_language,
        }

    def get_system_prompt_section(self) -> str:
        if self._cached_prompt_section is None:
            self._cached_prompt_section = (
                f"IDENTITY\n"
                f"Name: {self._name}\n"
                f"Description: {self._description}\n"
                f"Reply language: {self._reply_language}\n"
            )
        return self._cached_prompt_section

    @property
    def reply_language(self) -> str:
        return self._reply_language

    @reply_language.setter
    def reply_language(self, value: str) -> None:
        self._reply_language = (str(value or DEFAULT_LANGUAGE)).strip().lower()[:5]
        self._cached_prompt_section = None
