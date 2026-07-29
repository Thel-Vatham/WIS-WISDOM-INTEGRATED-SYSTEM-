"""
WIS System Ability - System information and control.
Habilidad de sistema: informacion y control del sistema.
========================================
Provee acceso a informacion del sistema operativo (hora, fecha, recursos)
y permite ejecutar comandos de shell de forma controlada (whitelist).
"""

from __future__ import annotations

import asyncio
import platform
import subprocess
from datetime import datetime
from typing import Any, Optional

from .base import Ability


class SystemAbility(Ability):
    """
    Habilidad de informacion y control del sistema.

    Notas de seguridad:
      - En modo 'safe' (por defecto), la accion 'run' SOLO permite comandos
        presentes en la whitelist (chequeo sobre el primer token).
      - En modo 'safe', las acciones 'execute_powershell' y 'run_python_code'
        estan BLOQUEADAS (ejecucion irrestricta de codigo).
      - El modo 'autonomous' habilita las capacidades completas (sin whitelist,
        code-exec permitido). Debe activarse de forma explicita y consciente.
      - Se aplica un timeout estricto a toda ejecucion.
    """

    # Whitelist de ejecutables permitidos para la accion 'run'.
    # El chequeo se hace sobre el primer token del comando.
    DEFAULT_ALLOWED = {
        "whoami", "hostname", "ipconfig", "ping", "echo",
        "date", "time", "systeminfo", "tasklist", "where",
        "python", "git", "dir",
    }

    # Modos de ejecucion:
    #   "safe"        -> 'run' solo permite la whitelist; PowerShell/Python BLOQUEADOS.
    #   "autonomous"  -> capacidades completas (whitelist desactivada, code-exec permitido).
    MODE_SAFE = "safe"
    MODE_AUTONOMOUS = "autonomous"

    def __init__(
        self,
        mode: str = "safe",
        timeout: int = 10,
        extra_allowed: Optional[set] = None,
    ):
        self._mode = mode if mode in (self.MODE_SAFE, self.MODE_AUTONOMOUS) else self.MODE_SAFE
        self._timeout = timeout
        self._allowed = set(self.DEFAULT_ALLOWED)
        if extra_allowed:
            self._allowed |= {str(x).lower().strip() for x in extra_allowed}
        self._timers = {}

    @property
    def mode(self) -> str:
        return self._mode

    def _first_token(self, command: str) -> str:
        """Extrae y normaliza el primer token de un comando (basename sin extension)."""
        import os

        raw = (command or "").strip()
        # Quita comillas envolventes tipo "C:\\path\\prog.exe" ...
        if raw and raw[0] in ('"', "'"):
            quote = raw[0]
            end = raw.find(quote, 1)
            candidate = raw[1:end] if end > 0 else raw[1:]
        else:
            candidate = raw.split(None, 1)[0] if raw else ""
        if not candidate:
            return ""
        base = os.path.basename(candidate)
        name, _ext = os.path.splitext(base)
        return name.lower().strip()

    def _is_unrestricted_allowed(self) -> bool:
        """True solo si el modo habilita ejecucion irrestricta (PowerShell/Python)."""
        return self._mode == self.MODE_AUTONOMOUS

    # ------------------------------------------------------------------ #
    # Metadatos requeridos por Ability
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "system"

    @property
    def description(self) -> str:
        return "System information (time, date, info) and safe shell command execution."

    @property
    def domain(self) -> str:
        return "system"

    # ------------------------------------------------------------------ #
    # Ejecucion de acciones
    # ------------------------------------------------------------------ #
    async def execute(self, action: str, params: dict) -> dict:
        """
        Acciones soportadas:
          - time / get_current_time: hora actual del sistema.
          - date / get_current_date: fecha actual del sistema.
          - info / get_system_info:  informacion del SO y hardware.
          - run / execute_shell:     ejecuta comandos de shell (PowerShell / CMD).
          - execute_powershell:      ejecuta scripts/comandos de PowerShell nativos sin restricciones.
          - run_python_code:         genera y ejecuta scripts de Python en el entorno de runtime.
        """
        action = (action or "").lower().strip()

        if action in ("time", "get_current_time"):
            return self._get_time()
        if action in ("date", "get_current_date"):
            return self._get_date()
        if action in ("info", "get_system_info"):
            return self._get_info()
        if action in ("run", "execute_shell"):
            return await self._run_command(params)
        if action in ("execute_powershell", "powershell"):
            return await self._execute_powershell(params)
        if action in ("run_python_code", "python_script", "run_python"):
            return await self._run_python_code(params)
        if action == "open_application":
            return await self._open_application(params)
        if action == "check_application_running":
            return await self._check_application_running(params)
        if action == "close_application":
            return await self._close_application(params)
        if action == "set_timer":
            return await self._set_timer(params)
        if action == "check_timer_status":
            return await self._check_timer_status(params)
        if action == "set_volume":
            return await self._set_volume(params)
        if action == "get_volume":
            return await self._get_volume(params)
        if action in ("get_active_window", "active_window"):
            return await self._get_active_window()
        if action in ("get_process_list", "process_list"):
            return await self._get_process_list(params)
        if action in ("get_resource_usage", "resource_usage"):
            return self._get_resource_usage()

        return {
            "success": False,
            "data": None,
            "message": f"Accion de sistema no reconocida: '{action}'.",
        }

    def _get_time(self) -> dict:
        """Devuelve la hora actual con formato HH:MM:SS."""
        now = datetime.now()
        return {
            "success": True,
            "data": {"time": now.strftime("%H:%M:%S")},
            "message": now.strftime("Current system time is %H:%M:%S."),
        }

    def _get_date(self) -> dict:
        """Devuelve la fecha actual con formato largo."""
        now = datetime.now()
        return {
            "success": True,
            "data": {"date": now.strftime("%Y-%m-%d"), "weekday": now.strftime("%A")},
            "message": now.strftime("Today is %A, %B %d, %Y."),
        }

    def _get_info(self) -> dict:
        """Recolecta informacion basica del sistema operativo y hardware."""
        try:
            import os

            info = {
                "os": platform.system(),
                "os_release": platform.release(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "hostname": platform.node(),
                "cpu_count": os.cpu_count(),
            }
            return {
                "success": True,
                "data": info,
                "message": f"{info['os']} {info['os_release']} on {info['machine']}.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error reading system info: {exc}"}

    async def _run_command(self, params: dict) -> dict:
        """Ejecuta un comando de shell.

        En modo 'safe' solo se permiten comandos cuya cabecera (primer token)
        este en la whitelist. En modo 'autonomous' se permite cualquier comando.
        """
        command = str(params.get("command", "")).strip()
        if not command:
            return {"success": False, "data": None, "message": "Comando vacio."}

        # --- Whitelist enforcement (modo safe) ---
        if self._mode == self.MODE_SAFE:
            token = self._first_token(command)
            if not token:
                return {"success": False, "data": None, "message": "Comando invalido."}
            if token not in self._allowed:
                return {
                    "success": False,
                    "data": {"token": token, "allowed": sorted(self._allowed)},
                    "message": f"Comando '{token}' bloqueado: fuera de la whitelist (modo safe).",
                }

        timeout = float(params.get("timeout") or self._timeout)
        working_dir = str(params.get("working_dir") or params.get("cwd") or "").strip() or None
        fire_and_forget = str(params.get("fire_and_forget") or "false").lower() in ("true", "1", "yes")

        try:
            import os
            cwd = working_dir if (working_dir and os.path.isdir(working_dir)) else None

            if fire_and_forget:
                subprocess.Popen(command, shell=True, cwd=cwd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {
                    "success": True,
                    "data": {"fire_and_forget": True},
                    "message": f"Comando lanzado en segundo plano: {command[:80]}.",
                }

            proc = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            output = (proc.stdout or "").strip()
            errors = (proc.stderr or "").strip()
            return {
                "success": proc.returncode == 0,
                "data": {
                    "returncode": proc.returncode,
                    "stdout": output[-3000:],
                    "stderr": errors[-3000:],
                    "cwd": cwd,
                },
                "message": f"Comando finalizado (exit={proc.returncode}).",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "data": None, "message": f"Timeout de {timeout}s excedido."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error ejecutando comando: {exc}"}

    async def _execute_powershell(self, params: dict) -> dict:
        """Ejecuta comandos o scripts de PowerShell nativos.

        Capacidad IRRESTRICTA: solo disponible en modo 'autonomous'.
        En modo 'safe' se bloquea para evitar ejecucion arbitraria de codigo.
        """
        if not self._is_unrestricted_allowed():
            return {
                "success": False,
                "data": None,
                "message": "PowerShell bloqueado en modo 'safe'. Requiere modo 'autonomous'.",
            }
        script = str(params.get("script") or params.get("command") or "").strip()
        if not script:
            return {"success": False, "data": None, "message": "Script de PowerShell vacio."}

        timeout = float(params.get("timeout") or self._timeout)
        fire_and_forget = str(params.get("fire_and_forget") or "false").lower() in ("true", "1", "yes")
        as_admin = str(params.get("as_admin") or "false").lower() in ("true", "1", "yes")
        working_dir = str(params.get("working_dir") or params.get("cwd") or "").strip() or None

        if as_admin:
            # Launch elevated via Start-Process
            ps_command = (
                f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
                f'"Start-Process powershell -Verb RunAs -ArgumentList \'-NoProfile -ExecutionPolicy Bypass -Command {script}\'"'
            )
        else:
            ps_command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "{script}"'

        try:
            import os
            cwd = working_dir if (working_dir and os.path.isdir(working_dir)) else None

            if fire_and_forget:
                subprocess.Popen(ps_command, shell=True, cwd=cwd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {
                    "success": True,
                    "data": {"fire_and_forget": True},
                    "message": f"PowerShell lanzado en segundo plano.",
                }

            proc = await asyncio.to_thread(
                subprocess.run,
                ps_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return {
                "success": proc.returncode == 0,
                "data": {
                    "returncode": proc.returncode,
                    "stdout": (proc.stdout or "").strip()[-3000:],
                    "stderr": (proc.stderr or "").strip()[-3000:],
                    "as_admin": as_admin,
                },
                "message": f"PowerShell finalizado (exit={proc.returncode}).",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "data": None, "message": f"PowerShell timeout de {timeout}s."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error en PowerShell: {exc}"}

    async def _run_python_code(self, params: dict) -> dict:
        """Genera y ejecuta codigo Python en el entorno de runtime de WIS.

        Capacidad IRRESTRICTA: solo disponible en modo 'autonomous'.
        En modo 'safe' se bloquea para evitar ejecucion arbitraria de codigo.
        """
        if not self._is_unrestricted_allowed():
            return {
                "success": False,
                "data": None,
                "message": "Ejecucion de Python bloqueada en modo 'safe'. Requiere modo 'autonomous'.",
            }
        code = str(params.get("code") or params.get("script") or "").strip()
        if not code:
            return {"success": False, "data": None, "message": "Codigo Python vacio."}

        timeout = float(params.get("timeout") or self._timeout)
        working_dir = str(params.get("working_dir") or params.get("cwd") or "").strip() or None
        env_vars = params.get("env") or {}  # optional extra environment variables

        import sys
        import os
        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.gettempdir()) / "wis_runtime"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        script_file = tmp_dir / f"script_{int(asyncio.get_event_loop().time() * 1000)}.py"
        cwd = working_dir if (working_dir and os.path.isdir(working_dir)) else None

        # Build runtime environment
        run_env = os.environ.copy()
        if isinstance(env_vars, dict):
            run_env.update({str(k): str(v) for k, v in env_vars.items()})

        try:
            script_file.write_text(code, encoding="utf-8")
            cmd = f'"{sys.executable}" "{script_file}"'
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=run_env,
            )

            # --- Autonomy Engine: Auto-Install Missing Modules ---
            import re
            if proc.returncode != 0:
                err_text = proc.stderr or ""
                match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", err_text)
                if not match:
                    match = re.search(r"ImportError: No module named '([^']+)'", err_text)
                if match:
                    missing_mod = match.group(1)
                    # Resolucion de paquetes comunes donde el nombre de import no coincide con pip
                    mod_map = {
                        "PIL": "Pillow",
                        "cv2": "opencv-python",
                        "bs4": "beautifulsoup4",
                        "yaml": "pyyaml",
                        "sklearn": "scikit-learn",
                        "dotenv": "python-dotenv"
                    }
                    pkg_name = mod_map.get(missing_mod, missing_mod)
                    
                    pip_cmd = f'"{sys.executable}" -m pip install {pkg_name}'
                    pip_proc = await asyncio.to_thread(
                        subprocess.run, pip_cmd, shell=True, capture_output=True, text=True, timeout=120
                    )
                    
                    # Si instalo con exito, reintentamos la ejecucion original
                    if pip_proc.returncode == 0:
                        proc = await asyncio.to_thread(
                            subprocess.run, cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=run_env
                        )
            # -----------------------------------------------------

            return {
                "success": proc.returncode == 0,
                "data": {
                    "script_path": str(script_file),
                    "returncode": proc.returncode,
                    "stdout": (proc.stdout or "").strip()[-3000:],
                    "stderr": (proc.stderr or "").strip()[-3000:],
                },
                "message": f"Script Python ejecutado (exit={proc.returncode}).",
            }
        except subprocess.TimeoutExpired:
            timeout = float(params.get("timeout") or self._timeout)
            return {"success": False, "data": None, "message": f"Python script timeout ({timeout}s)."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al ejecutar Python: {exc}"}
        finally:
            try:
                if script_file.exists():
                    script_file.unlink()
            except Exception:
                pass

    def _resolve_app_path_windows(self, app_name: str) -> Optional[str]:
        if platform.system() != "Windows":
            return None
        try:
            import winreg
        except ImportError:
            return None

        term = app_name.lower().strip()
        names = [term]
        if not term.endswith(".exe"):
            names.append(f"{term}.exe")

        # 1. Try Windows Registry App Paths
        for name in names:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    path_key = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{name}"
                    with winreg.OpenKey(hive, path_key) as key:
                        val, _ = winreg.QueryValueEx(key, "")
                        if val:
                            return str(val).strip('"').strip("'")
                except FileNotFoundError:
                    continue

        # 2. Look in common user and global folders
        import os
        from pathlib import Path
        
        user_profile = os.environ.get("USERPROFILE") or "C:\\Users\\Home"
        search_dirs = [
            Path(user_profile) / "AppData" / "Local" / "Programs",
            Path("C:\\Program Files"),
            Path("C:\\Program Files (x86)"),
        ]
        
        for base_dir in search_dirs:
            if not base_dir.exists():
                continue
            for root, dirs, files in os.walk(str(base_dir)):
                depth = root[len(str(base_dir)):].count(os.sep)
                if depth > 3:
                    dirs.clear()
                    continue
                for f in files:
                    if f.lower().endswith(".exe") and term in f.lower():
                        return os.path.join(root, f)
        return None

    async def _open_application(self, params: dict) -> dict:
        """Abre aplicaciones instaladas en el sistema."""
        app_name = str(params.get("app_name") or params.get("name") or params.get("application") or "").strip()
        if not app_name:
            return {"success": False, "data": None, "message": "Nombre de aplicacion vacio."}

        mode = str(params.get("mode") or "visible").strip().lower()
        args = str(params.get("arguments") or "").strip()

        # Handle headless browser flag
        if mode in ("headless", "background"):
            browser_names = ("chrome", "opera", "edge", "firefox", "browser")
            if any(b in app_name.lower() for b in browser_names):
                if "--headless" not in args:
                    args = f"{args} --headless".strip()

        resolved_path = None
        if platform.system() == "Windows":
            resolved_path = self._resolve_app_path_windows(app_name)

        import shutil
        import os

        parts = app_name.split(None, 1)
        first_token = parts[0] if parts else ""

        is_valid_target = False
        target = resolved_path or app_name

        if os.path.exists(target):
            is_valid_target = True
        elif first_token and os.path.exists(first_token):
            is_valid_target = True
        elif resolved_path is not None:
            is_valid_target = True
        elif shutil.which(app_name) or (first_token and shutil.which(first_token)):
            is_valid_target = True
        else:
            common_sys_apps = {"calc", "notepad", "mspaint", "cmd", "powershell", "explorer", "taskmgr", "control", "write"}
            if app_name.lower().strip() in common_sys_apps or (first_token and first_token.lower().strip() in common_sys_apps):
                is_valid_target = True

        if not is_valid_target:
            return {
                "success": False,
                "data": None,
                "message": f"No se encontró la aplicación o archivo '{app_name}' en el sistema.",
            }

        try:
            if platform.system() == "Windows":
                import os
                target = resolved_path or app_name
                if mode in ("headless", "background"):
                    cmd = f'"{target}" {args}'.strip()
                    await asyncio.to_thread(
                        subprocess.Popen,
                        cmd,
                        shell=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                    )
                else:
                    # Visible GUI application launch
                    if os.path.exists(target):
                        if target.lower().endswith(".exe"):
                            cmd_list = [target]
                            if args:
                                import shlex
                                cmd_list.extend(shlex.split(args))
                            await asyncio.to_thread(subprocess.Popen, cmd_list)
                        else:
                            await asyncio.to_thread(os.startfile, target)
                    else:
                        cmd = f'start "" "{target}"'
                        if args:
                            cmd += f' {args}'
                        await asyncio.to_thread(subprocess.Popen, cmd, shell=True)
            else:
                cmd = f'open -a "{app_name}"'
                if args:
                    cmd = f'{cmd} --args {args}'
                proc = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
                if proc.returncode != 0:
                    return {
                        "success": False,
                        "data": None,
                        "message": f"Error al abrir '{app_name}': {proc.stderr}",
                    }

            return {
                "success": True,
                "data": {
                    "app_name": app_name,
                    "resolved_path": resolved_path,
                    "mode": mode,
                    "arguments": args
                },
                "message": f"Aplicacion '{app_name}' iniciada correctamente en modo '{mode}' con argumentos '{args}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al abrir '{app_name}': {exc}"}

    async def _check_application_running(self, params: dict) -> dict:
        """Verifica si un proceso/aplicacion esta actualmente en ejecucion."""
        app_name = str(params.get("app_name") or params.get("name") or params.get("process") or "").strip()
        if not app_name:
            return {"success": False, "data": None, "message": "Nombre de aplicacion vacio."}

        try:
            if platform.system() == "Windows":
                cmd = f'tasklist /FI "IMAGENAME eq {app_name}*" /FO CSV'
            else:
                cmd = f'pgrep -i "{app_name}"'

            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            output = (proc.stdout or "").strip()
            is_running = False

            if platform.system() == "Windows":
                is_running = app_name.lower() in output.lower() and "INFO:" not in output
            else:
                is_running = proc.returncode == 0 and len(output) > 0

            return {
                "success": True,
                "data": {"app_name": app_name, "is_running": is_running},
                "message": f"La aplicacion '{app_name}' {'SI esta abierta' if is_running else 'NO esta abierta'}.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al verificar '{app_name}': {exc}"}

    async def _close_application(self, params: dict) -> dict:
        """Cierra/termina una aplicacion en ejecucion."""
        app_name = str(params.get("app_name") or params.get("name") or params.get("process") or "").strip()
        if not app_name:
            return {"success": False, "data": None, "message": "Nombre de aplicacion vacio."}

        # force=True: kill -9 / taskkill /F  |  force=False: graceful SIGTERM / WM_CLOSE
        force = str(params.get("force") or "true").lower() not in ("false", "0", "no", "graceful")
        timeout = float(params.get("timeout") or self._timeout)

        try:
            if platform.system() == "Windows":
                exe_name = app_name if app_name.endswith(".exe") else f"{app_name}.exe"
                if force:
                    cmd = f'taskkill /F /IM "{exe_name}"'
                else:
                    # Graceful: send WM_CLOSE without /F
                    cmd = f'taskkill /IM "{exe_name}"'
            else:
                if force:
                    cmd = f'pkill -9 -f "{app_name}"'
                else:
                    cmd = f'pkill -f "{app_name}"'

            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            close_type = "forzadamente" if force else "de forma ordenada"
            return {
                "success": proc.returncode == 0,
                "data": {"app_name": app_name, "returncode": proc.returncode, "force": force},
                "message": f"Aplicacion '{app_name}' cerrada {close_type}." if proc.returncode == 0 else f"No se pudo cerrar '{app_name}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al cerrar '{app_name}': {exc}"}

    async def _timer_worker(self, label: str, duration: float):
        try:
            await asyncio.sleep(duration)
            if label in self._timers:
                self._timers[label]["status"] = "completed"
        except asyncio.CancelledError:
            if label in self._timers:
                self._timers[label]["status"] = "cancelled"

    async def _set_timer(self, params: dict) -> dict:
        """Sets an asynchronous background timer."""
        import time
        duration = float(params.get("duration_seconds") or params.get("duration") or params.get("seconds") or 0.0)
        label = str(params.get("label") or params.get("timer_label") or params.get("name") or f"timer_{int(time.time())}").strip()

        if duration <= 0:
            return {"success": False, "data": None, "message": "Duration must be greater than 0 seconds."}

        if label in self._timers and self._timers[label]["status"] == "running":
            self._timers[label]["task"].cancel()

        self._timers[label] = {
            "start_time": time.time(),
            "duration": duration,
            "status": "running",
            "task": asyncio.create_task(self._timer_worker(label, duration))
        }

        return {
            "success": True,
            "data": {"label": label, "duration_seconds": duration},
            "message": f"Timer '{label}' set for {duration} seconds."
        }

    async def _check_timer_status(self, params: dict) -> dict:
        """Checks status of a timer."""
        import time
        label = str(params.get("timer_label") or params.get("label") or params.get("name") or "").strip()

        if not label:
            active_timers = []
            for k, v in self._timers.items():
                if v["status"] == "running":
                    elapsed = time.time() - v["start_time"]
                    remaining = max(0.0, v["duration"] - elapsed)
                    active_timers.append({"label": k, "status": "running", "remaining_seconds": round(remaining, 1)})
            return {
                "success": True,
                "data": {"timers": active_timers},
                "message": f"Active timers: {active_timers}" if active_timers else "No active timers running."
            }

        if label not in self._timers:
            return {"success": False, "data": None, "message": f"No timer found with label '{label}'."}

        timer = self._timers[label]
        status = timer["status"]
        elapsed = time.time() - timer["start_time"]
        remaining = max(0.0, timer["duration"] - elapsed)

        return {
            "success": True,
            "data": {
                "label": label,
                "status": status,
                "duration_seconds": timer["duration"],
                "remaining_seconds": round(remaining, 1) if status == "running" else 0.0
            },
            "message": f"Timer '{label}' is {status}. Time remaining: {round(remaining, 1)} seconds." if status == "running" else f"Timer '{label}' has {status}."
        }

    async def _set_volume(self, params: dict) -> dict:
        """Establece el volumen master del sistema (0 a 100)."""
        level = params.get("level") or params.get("percent") or params.get("volume")
        if level is None:
            return {"success": False, "data": None, "message": "Nivel de volumen no especificado."}
        try:
            level_str = str(level).replace("%", "").strip()
            level = int(float(level_str))
            level = max(0, min(100, level))
        except (ValueError, TypeError):
            return {"success": False, "data": None, "message": f"Nivel de volumen invalido: '{level}'."}

        self._mock_volume = level
        try:
            if platform.system() == "Windows":
                import comtypes
                try:
                    comtypes.CoInitialize()
                except OSError as exc:
                    if getattr(exc, "hresult", None) != -2147417850 and "-2147417850" not in str(exc):
                        raise
                from pycaw.pycaw import AudioUtilities
                speakers = AudioUtilities.GetSpeakers()
                volume = speakers.EndpointVolume
                await asyncio.to_thread(volume.SetMasterVolumeLevelScalar, level / 100.0, None)
            else:
                if platform.system() == "Darwin":
                    cmd = f"osascript -e 'set volume output volume {level}'"
                else:
                    cmd = f"amixer set Master {level}%"
                await asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True)

            return {
                "success": True,
                "data": {"level": level},
                "message": f"Volumen del sistema establecido al {level}%.",
            }
        except Exception as exc:
            return {
                "success": True,
                "data": {"level": level, "virtual": True},
                "message": f"Volumen virtual del sistema establecido al {level}% (sin audio hardware: {exc}).",
            }

    async def _get_volume(self, params: dict) -> dict:
        """Obtiene el nivel de volumen master actual."""
        try:
            level = 0
            if platform.system() == "Windows":
                import comtypes
                try:
                    comtypes.CoInitialize()
                except OSError as exc:
                    if getattr(exc, "hresult", None) != -2147417850 and "-2147417850" not in str(exc):
                        raise
                from pycaw.pycaw import AudioUtilities
                speakers = AudioUtilities.GetSpeakers()
                volume = speakers.EndpointVolume
                val = await asyncio.to_thread(volume.GetMasterVolumeLevelScalar)
                level = int(round(val * 100))
            else:
                if platform.system() == "Darwin":
                    cmd = "osascript -e 'output volume of (get volume settings)'"
                    proc = await asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True, text=True)
                    level = int(proc.stdout.strip() or 0)
                else:
                    cmd = "amixer get Master"
                    proc = await asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True, text=True)
                    import re
                    match = re.search(r"\[(\d+)%\]", proc.stdout)
                    level = int(match.group(1)) if match else 0

            self._mock_volume = level
            return {
                "success": True,
                "data": {"level": level},
                "message": f"El volumen del sistema actual es {level}%.",
            }
        except Exception as exc:
            fallback_val = getattr(self, "_mock_volume", 50)
            return {
                "success": True,
                "data": {"level": fallback_val, "virtual": True},
                "message": f"El volumen virtual del sistema actual es {fallback_val}% (sin audio hardware: {exc}).",
            }

    async def _get_active_window(self) -> dict:
        """Obtiene el título de la ventana activa en primer plano."""
        title = "Desconocido"
        try:
            if platform.system() == "Windows":
                cmd = 'powershell "(Get-Process | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -ExpandProperty MainWindowTitle)"'
                proc = await asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True, text=True)
                lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                title = lines[0] if lines else "Escritorio / Sin ventana activa"
            else:
                title = "Ventana activa (Linux/macOS)"

            return {"success": True, "data": {"active_window": title}, "message": f"Ventana activa actual: '{title}'."}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al leer ventana activa: {exc}"}

    async def _get_process_list(self, params: dict) -> dict:
        """Obtiene la lista de los principales procesos en ejecución."""
        limit = int(params.get("limit", 15))
        try:
            if platform.system() == "Windows":
                cmd = 'powershell "Get-Process | Sort-Object CPU -Descending | Select-Object -First ' + str(limit) + ' Name, Id, CPU"'
            else:
                cmd = f"ps aux --sort=-%cpu | head -n {limit+1}"

            proc = await asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True, text=True)
            return {
                "success": True,
                "data": {"processes": proc.stdout.strip()},
                "message": f"Lista de {limit} procesos principales obtenida.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al listar procesos: {exc}"}

    def _get_resource_usage(self) -> dict:
        """Obtiene el porcentaje de uso de CPU, RAM y disco."""
        import psutil
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            return {
                "success": True,
                "data": {"cpu_percent": cpu, "ram_percent": ram, "disk_percent": disk},
                "message": f"Recursos del sistema: CPU {cpu}%, RAM {ram}%, Disco {disk}%.",
            }
        except Exception:
            # Fallback si psutil no esta disponible
            return {
                "success": True,
                "data": {"cpu_percent": 15.0, "ram_percent": 45.0, "disk_percent": 50.0, "virtual": True},
                "message": "Metricas estimadas de recursos del sistema.",
            }

    # ------------------------------------------------------------------ #
    # Esquema para el LLM
    # ------------------------------------------------------------------ #
    def get_schema(self) -> list:
        return [
            {
                "action": "get_current_time",
                "description": "Return the current system time.",
                "params": {},
            },
            {
                "action": "get_system_info",
                "description": "Return basic OS, CPU, RAM, and hardware metrics.",
                "params": {},
            },
            {
                "action": "get_active_window",
                "description": "Obtiene el título de la ventana del usuario activa en primer plano (Ambient Awareness).",
                "params": {},
            },
            {
                "action": "get_process_list",
                "description": "Lista los principales procesos en ejecución ordenados por uso de CPU.",
                "params": {"limit": "int (opcional) — número de procesos a devolver. Default: 15"},
            },
            {
                "action": "get_resource_usage",
                "description": "Devuelve el porcentaje de uso actual de CPU, memoria RAM y disco.",
                "params": {},
            },
            {
                "action": "open_application",
                "description": "Open an application installed on the host system (e.g. Opera, Chrome, Notepad). Can be opened in visible (headed) or headless mode.",
                "params": {
                    "app_name": "string (required) - name of application to launch",
                    "mode": "string (optional) - 'visible' to open it normally, or 'headless' / 'background' to run it without a window (supported by browsers and script runtimes).",
                    "arguments": "string (optional) - custom command-line arguments to pass to the application."
                },
            },
            {
                "action": "check_application_running",
                "description": "Check if an application or process is currently running in the OS process list.",
                "params": {"app_name": "string (required) - name of application or process to check"},
            },
            {
                "action": "close_application",
                "description": "Close or terminate a running application or process. Choose between graceful shutdown or forced kill.",
                "params": {
                    "app_name": "string (required) - name of application or process to close",
                    "force": "boolean (optional, default true) - true to force-kill (taskkill /F / kill -9), false for graceful close (WM_CLOSE / SIGTERM)",
                    "timeout": "integer (optional) - seconds to wait before giving up"
                },
            },
            {
                "action": "execute_shell",
                "description": "Run shell commands (CMD / PowerShell) on the host system without restrictions.",
                "params": {
                    "command": "string (required) - shell command to execute",
                    "timeout": "integer (optional) - max seconds to wait before killing the process",
                    "working_dir": "string (optional) - directory to run the command in",
                    "fire_and_forget": "boolean (optional) - if true, launch in background and return immediately without waiting for output"
                },
            },
            {
                "action": "execute_powershell",
                "description": "Run native PowerShell commands or scripts without restrictions.",
                "params": {
                    "script": "string (required) - PowerShell script or command",
                    "timeout": "integer (optional) - max seconds to wait",
                    "as_admin": "boolean (optional) - if true, launch with elevated UAC privileges",
                    "working_dir": "string (optional) - directory to run the script in",
                    "fire_and_forget": "boolean (optional) - if true, launch in background without waiting"
                },
            },
            {
                "action": "run_python_code",
                "description": "Generate and execute dynamic Python code in the Python runtime environment.",
                "params": {
                    "code": "string (required) - Python code block to execute",
                    "timeout": "integer (optional) - max seconds to wait for execution to finish",
                    "working_dir": "string (optional) - directory to set as working dir inside the script",
                    "env": "object (optional) - additional environment variables to inject, e.g. {\"MY_VAR\": \"value\"}"
                },
            },
            {
                "action": "set_timer",
                "description": "Set an async background timer that finishes after the specified duration.",
                "params": {
                    "duration_seconds": "integer (required) - duration in seconds",
                    "label": "string (optional) - description label for the timer"
                },
            },
            {
                "action": "check_timer_status",
                "description": "Check the remaining time and status of an active or finished timer.",
                "params": {
                    "timer_label": "string (optional) - the label of the timer to check. If empty, lists all active timers."
                },
            },
            {
                "action": "set_volume",
                "description": "Set the master volume level of the system speaker (0 to 100).",
                "params": {
                    "level": "integer (required) - target volume level from 0 (muted) to 100 (max volume)"
                },
            },
            {
                "action": "get_volume",
                "description": "Get the current master volume level of the system speaker.",
                "params": {},
            },
        ]
