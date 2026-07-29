"""
WIS Dependencies Autoinstaller - Verificacion e instalacion automatica de dependencias.
=====================================================================================
Lee 'requirements.txt' al iniciar y valida si todas las librerias estan instaladas.
Si falta alguna, la instala usando pip y ejecuta la instalacion de navegadores de Playwright.
"""
from __future__ import annotations

import importlib.metadata
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("wis.core.dependencies")

# Mapa de normalizacion opcional
_NORMALIZATION_MAP = {
    "duckduckgo_search": "duckduckgo-search",
}


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
    playwright_needs_install = False

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Extraer el nombre del paquete (quitar >=, ==, <, etc.)
        match = re.match(r"^([a-zA-Z0-9_\-]+)", line)
        if not match:
            continue

        pkg_name = match.group(1)
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
                if pkg_name.lower() == "playwright":
                    playwright_needs_install = True

    if not missing_packages:
        return

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
    except Exception as exc:
        print(f"\n  [ERROR] Fallo al instalar dependencias: {exc}")
        return

    # Si instalamos playwright, correr playwright install chromium
    if playwright_needs_install:
        print("  Instalando binario de Chromium para Playwright...")
        try:
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"],
                                   stdout=sys.stdout, stderr=sys.stderr)
            print("  [OK] Navegador Chromium configurado.")
        except Exception as exc:
            print(f"  [ERROR] Fallo al instalar Chromium: {exc}")

    print("  Dependencias listas. Continuando inicio...\n")
