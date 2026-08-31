"""
WIS Toolchain Ability - Control y Flasheo de Microcontroladores.
================================================================
Gestiona toolchains de ingeniería embebida:
- PlatformIO (compilación, subida, librerías)
- Arduino-CLI (compilación, subida a AVR, ESP, STM32, SAMD)
- esptool (diagnóstico y flasheo de ESP32/ESP8266)
- Serial Monitor (captura de streaming y logs de depuración)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from abilities.base import Ability

logger = logging.getLogger("wis.abilities.toolchain")


class ToolchainAbility(Ability):
    """Habilidad para interactuar con herramientas de compilación y flasheo de microcontroladores."""

    def __init__(self, hardware_memory: Optional[Any] = None) -> None:
        self.hw_memory = hardware_memory

    @property
    def name(self) -> str:
        return "toolchain"

    @property
    def description(self) -> str:
        return "Embedded toolchain management: compile, flash, diagnose microcontrollers (PlatformIO, Arduino-CLI, esptool, Serial Monitor)."

    @property
    def domain(self) -> str:
        return "engineering"

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").strip().lower()
        if action == "platformio_cmd":
            return await self._platformio_cmd(params)
        elif action == "arduino_cli":
            return await self._arduino_cli(params)
        elif action == "esptool_cmd":
            return await self._esptool_cmd(params)
        elif action == "serial_monitor":
            return await self._serial_monitor(params)
        elif action == "check_tools":
            return await self._check_installed_tools(params)
        else:
            return {"success": False, "data": None, "message": f"Accion desconocida: '{action}'"}

    async def _platformio_cmd(self, params: dict) -> dict:
        """Ejecuta un comando de PlatformIO (pio)."""
        subcmd = params.get("command", "run")
        project_dir = params.get("project_dir", ".")
        extra_args = params.get("args", [])
        context_tag = params.get("context_tag", "platformio_project")

        pio_bin = shutil.which("pio") or shutil.which("platformio")
        if not pio_bin:
            return {
                "success": False,
                "data": None,
                "message": "PlatformIO CLI (pio) no está instalado o no se encuentra en el PATH.",
            }

        cmd = [pio_bin] + subcmd.split() + (extra_args if isinstance(extra_args, list) else str(extra_args).split())
        start_t = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=params.get("timeout_s", 120))
            duration_ms = (time.time() - start_t) * 1000.0
            out_str = stdout.decode("utf-8", errors="replace")
            err_str = stderr.decode("utf-8", errors="replace")
            success = proc.returncode == 0

            # Registrar en memoria procedimental si está disponible
            if self.hw_memory:
                self.hw_memory.record_procedural_command(
                    command=" ".join(cmd),
                    toolchain="platformio",
                    context_tag=context_tag,
                    success=success,
                    duration_ms=duration_ms,
                    error=err_str if not success else "",
                )

            return {
                "success": success,
                "data": {
                    "returncode": proc.returncode,
                    "stdout": out_str[-2000:],  # ultimos 2000 chars
                    "stderr": err_str[-1000:],
                    "duration_ms": round(duration_ms, 1),
                },
                "message": f"PlatformIO '{subcmd}' finalizado con código {proc.returncode}.",
            }
        except asyncio.TimeoutError:
            return {"success": False, "data": None, "message": "PlatformIO excedió el tiempo límite."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error ejecutando PlatformIO: {exc}"}

    async def _arduino_cli(self, params: dict) -> dict:
        """Ejecuta un comando de Arduino-CLI."""
        subcmd = params.get("command", "board list")
        extra_args = params.get("args", [])
        context_tag = params.get("context_tag", "arduino_project")

        arduino_bin = shutil.which("arduino-cli")
        if not arduino_bin:
            return {
                "success": False,
                "data": None,
                "message": "arduino-cli no está instalado o no se encuentra en el PATH.",
            }

        cmd = [arduino_bin] + subcmd.split() + (extra_args if isinstance(extra_args, list) else str(extra_args).split())
        start_t = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=params.get("timeout_s", 90))
            duration_ms = (time.time() - start_t) * 1000.0
            out_str = stdout.decode("utf-8", errors="replace")
            err_str = stderr.decode("utf-8", errors="replace")
            success = proc.returncode == 0

            if self.hw_memory:
                self.hw_memory.record_procedural_command(
                    command=" ".join(cmd),
                    toolchain="arduino_cli",
                    context_tag=context_tag,
                    success=success,
                    duration_ms=duration_ms,
                    error=err_str if not success else "",
                )

            return {
                "success": success,
                "data": {
                    "returncode": proc.returncode,
                    "stdout": out_str[-2000:],
                    "stderr": err_str[-1000:],
                    "duration_ms": round(duration_ms, 1),
                },
                "message": f"arduino-cli '{subcmd}' finalizado con código {proc.returncode}.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error ejecutando arduino-cli: {exc}"}

    async def _esptool_cmd(self, params: dict) -> dict:
        """Ejecuta operaciones de diagnóstico y flasheo con esptool.py."""
        subcmd = params.get("command", "chip_id")  # 'chip_id', 'flash_id', 'read_mac', 'erase_flash'
        port = params.get("port", "COM3")
        baud = params.get("baud", 115200)

        cmd = [sys.executable, "-m", "esptool", "--port", str(port), "--baud", str(baud)] + subcmd.split()
        start_t = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=params.get("timeout_s", 60))
            duration_ms = (time.time() - start_t) * 1000.0
            out_str = stdout.decode("utf-8", errors="replace")
            err_str = stderr.decode("utf-8", errors="replace")
            success = proc.returncode == 0

            if self.hw_memory:
                self.hw_memory.record_procedural_command(
                    command=" ".join(cmd),
                    toolchain="esptool",
                    context_tag=f"esp_{port}",
                    success=success,
                    duration_ms=duration_ms,
                    error=err_str if not success else "",
                )

            return {
                "success": success,
                "data": {
                    "returncode": proc.returncode,
                    "stdout": out_str,
                    "stderr": err_str,
                },
                "message": f"esptool '{subcmd}' en {port} finalizado con código {proc.returncode}.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error ejecutando esptool: {exc}"}

    async def _serial_monitor(self, params: dict) -> dict:
        """Abre un puerto serial y captura líneas durante un tiempo determinado o hasta coincidencia regex."""
        port = params.get("port", "COM3")
        baud = int(params.get("baud", 115200))
        duration_s = float(params.get("duration_s", 3.0))
        stop_pattern = params.get("stop_pattern")

        try:
            import serial
        except ImportError:
            return {"success": False, "data": None, "message": "pyserial no está instalado."}

        lines = []
        start_time = time.time()

        def _read_sync():
            captured = []
            try:
                with serial.Serial(port, baud, timeout=0.5) as ser:
                    while (time.time() - start_time) < duration_s:
                        line = ser.readline().decode("utf-8", errors="replace").strip()
                        if line:
                            captured.append(line)
                            if stop_pattern and re.search(stop_pattern, line):
                                break
            except Exception as e:
                logger.error("Serial monitor error on %s: %s", port, e)
                captured.append(f"[ERROR] {e}")
            return captured

        captured_lines = await asyncio.to_thread(_read_sync)
        return {
            "success": True,
            "data": {"lines": captured_lines, "count": len(captured_lines), "port": port},
            "message": f"Capturadas {len(captured_lines)} líneas del puerto {port}.",
        }

    async def _check_installed_tools(self, params: dict) -> dict:
        """Verifica el estado y disponibilidad de las herramientas de compilación."""
        tools = {
            "platformio": bool(shutil.which("pio") or shutil.which("platformio")),
            "arduino_cli": bool(shutil.which("arduino-cli")),
            "esptool": False,
            "pyserial": False,
            "git": bool(shutil.which("git")),
        }
        try:
            import esptool
            tools["esptool"] = True
        except ImportError:
            pass
        try:
            import serial
            tools["pyserial"] = True
        except ImportError:
            pass

        return {
            "success": True,
            "data": tools,
            "message": "Estado de toolchains de ingeniería verificado.",
        }

    def get_schema(self) -> list:
        return [
            {
                "action": "platformio_cmd",
                "description": "Execute PlatformIO commands (build, upload, test, device list).",
                "params": {
                    "command": "string (e.g. 'run', 'run -t upload', 'device list')",
                    "project_dir": "string (path to project root)",
                    "args": "list or string of extra arguments",
                },
            },
            {
                "action": "arduino_cli",
                "description": "Execute Arduino-CLI commands to compile or flash sketches.",
                "params": {
                    "command": "string (e.g. 'compile --fqbn esp32:esp32:esp32 sketch', 'board list')",
                    "args": "list of arguments",
                },
            },
            {
                "action": "esptool_cmd",
                "description": "Execute esptool operations (chip_id, flash_id, erase_flash, write_flash).",
                "params": {
                    "command": "string (default: 'chip_id')",
                    "port": "string (e.g. 'COM3')",
                    "baud": "int (default: 115200)",
                },
            },
            {
                "action": "serial_monitor",
                "description": "Capture serial stream logs from microcontrollers for debugging.",
                "params": {
                    "port": "string (e.g. 'COM4')",
                    "baud": "int (default: 115200)",
                    "duration_s": "float (seconds to monitor)",
                    "stop_pattern": "string (optional regex to stop capture)",
                },
            },
            {
                "action": "check_tools",
                "description": "Check which embedded toolchains (PlatformIO, Arduino-CLI, esptool) are installed.",
                "params": {},
            },
        ]
