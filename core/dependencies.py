"""
WIS Dependencies Autoinstaller - Verificacion e instalacion automatica de dependencias.
=====================================================================================
Lee 'requirements.txt' al iniciar y valida si todas las librerias estan instaladas.
Si falta alguna, la instala usando pip.

Ademas, si Playwright esta declarado en requirements, verifica que el binario de
Chromium este descargado y lo instala automaticamente si falta (idempotente),
aunque el paquete Python ya este instalado.
"""
from __future__ import annotations

import importlib.metadata
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wis.core.dependencies")

# Mapa de normalizacion opcional
_NORMALIZATION_MAP = {
    "duckduckgo_search": "duckduckgo-search",
}


def _playwright_browsers_dir() -> Path:
    """Directorio donde Playwright almacena los navegadores descargados."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _find_chromium_executable(folder: Path) -> Optional[Path]:
    """Busca el ejecutable de Chromium/headless-shell dentro de una carpeta de Playwright."""
    exe_names = {"chrome.exe", "headless_shell.exe", "chrome", "headless_shell"}
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower() in exe_names:
                return Path(root) / f
    return None


def _playwright_chromium_ready() -> bool:
    """True si el binario de Chromium de Playwright ya esta instalado en disco.

    Busca directamente en el directorio de browsers de Playwright (ms-playwright)
    sin lanzar el driver, para evitar efectos secundarios y ruido en el arranque.
    """
    base = _playwright_browsers_dir()
    if not base.exists():
        return False
    for folder in base.glob("chromium*"):
        if folder.is_dir() and _find_chromium_executable(folder):
            return True
    return False


def _ensure_playwright_browsers() -> None:
    """Instala el binario de Chromium de Playwright si falta (idempotente)."""
    if _playwright_chromium_ready():
        print("  [OK] Chromium de Playwright ya esta instalado.")
        return

    print("  Instalando binario de Chromium para Playwright (~170 MB)...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        if _playwright_chromium_ready():
            print("  [OK] Navegador Chromium configurado.")
        else:
            print("  [AVISO] La instalacion de Chromium finalizo pero la verificacion fallo.")
            print("          Puede requerir reiniciar WIS o ejecutar manualmente:")
            print("          python -m playwright install chromium")
    except Exception as exc:
        print(f"  [ERROR] Fallo al instalar Chromium: {exc}")


def check_and_install_dependencies(requirements_path: Path) -> None:
    """Verifica e instala dependencias que falten en el requirements.txt."""
    if not requirements_path.exists():
        return

    # Leer requirements.txt
    try:
        content = requirements_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[WIS] Error al leer {requirements_path}: {exc}")
        return

    missing_packages = []
    has_playwright = False

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Extraer el nombre del paquete (quitar >=, ==, <, etc.)
        match = re.match(r"^([a-zA-Z0-9_\-]+)", line)
        if not match:
            continue

        pkg_name = match.group(1)
        if pkg_name.lower() == "playwright":
            has_playwright = True

        normalized_name = _NORMALIZATION_MAP.get(pkg_name, pkg_name).replace("_", "-").lower()

        # Verificar si esta instalado
        try:
            importlib.metadata.version(normalized_name)
        except importlib.metadata.PackageNotFoundError:
            # Reintentar con el nombre original sin normalizar por si acaso
            try:
                importlib.metadata.version(pkg_name)
            except importlib.metadata.PackageNotFoundError:
                missing_packages.append(line)

    installed_any = False

    if missing_packages:
        print()
        print("  ===========================================")
        print("       Auto-Instalador de Dependencias       ")
        print("  ===========================================")
        print(f"  Faltan las siguientes librerias: {', '.join(missing_packages)}")
        print("  Instalando dependencias... Por favor espera.")
        print()

        # Lanzar instalacion via pip
        cmd = [sys.executable, "-m", "pip", "install"] + missing_packages
        try:
            # Ejecutar de manera síncrona
            subprocess.check_call(cmd, stdout=sys.stdout, stderr=sys.stderr)
            print("\n  [OK] Dependencias instaladas con exito.")
            installed_any = True
        except Exception as exc:
            print(f"\n  [ERROR] Fallo al instalar dependencias: {exc}")
            return

    # --- Playwright browsers: verificar SIEMPRE, aunque el paquete ya exista ---
    if has_playwright:
        _ensure_playwright_browsers()

    if installed_any:
        print("  Dependencias listas. Continuando inicio...\n")
