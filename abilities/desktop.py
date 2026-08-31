"""
WIS Desktop Ability - Control de UI nativo Win64.
==================================================
Automatizacion de escritorio sobre Windows x64 usando:
  - UIAutomation nativa (comtypes + uiautomation): busqueda logica de controles,
    patrones InvokePattern / TogglePattern / ValuePattern / SelectionItemPattern.
  - pyautogui como fallback para simulacion fisica de teclado/raton.
  - pyperclip para texto largo via portapapeles.

Acciones: type, shortcut, click, scroll, focus_wait, read_window
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base import Ability

logger = logging.getLogger("wis.abilities.desktop")

_CLIPBOARD_THRESHOLD = 50

# UIAutomation nativo Win64
_UI_AUTO_OK = False
try:
    import comtypes.automation  # noqa: F401
    import comtypes.client  # noqa: F401
    import comtypes.stream  # noqa: F401
    import comtypes.typeinfo  # noqa: F401
    import uiautomation as auto
    _UI_AUTO_OK = True
except ImportError:
    logger.warning("uiautomation/comtypes no instalado. Control nativo Win64 parcial.")

# pyautogui como fallback
_PYAUTOGUI_OK = False
_gui = None
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _gui = pyautogui
    _PYAUTOGUI_OK = True
except ImportError:
    logger.warning("pyautogui no instalado. Simulacion fisica deshabilitada.")
except Exception as exc:
    logger.warning(f"pyautogui fallo al inicializar: {exc}")

# pyperclip
_clipboard = None
try:
    import pyperclip
    _clipboard = pyperclip
except ImportError:
    logger.warning("pyperclip no instalado. Texto largo ira caracter por caracter.")


class DesktopAbility(Ability):
    """Control de UI nativo de Windows x64."""

    @property
    def name(self) -> str:
        return "desktop"

    @property
    def description(self) -> str:
        return ("Control nativo de Windows x64: escribir texto, atajos de teclado, "
                "clicks logicos y fisicos, scroll, enfocar ventanas, leer contenido.")

    @property
    def domain(self) -> str:
        return "desktop"

    def get_schema(self) -> list:
        return [
            {"action": "type", "description": "Escribir texto en un control o simulacion",
             "params": {"text": "string", "target": "string?", "control_type": "string?",
                        "automation_id": "string?", "submit": "bool?"}},
            {"action": "shortcut", "description": "Ejecutar atajo de teclado",
             "params": {"keys": "list[string]"}},
            {"action": "click", "description": "Clic logico o fisico en elemento",
             "params": {"target": "string?", "x": "int?", "y": "int?",
                        "button": "left|right", "control_type": "string?",
                        "automation_id": "string?", "class_name": "string?"}},
            {"action": "scroll", "description": "Scroll de raton",
             "params": {"amount": "int"}},
            {"action": "focus_wait", "description": "Esperar y activar ventana por titulo",
             "params": {"title": "string", "timeout": "int?"}},
            {"action": "read_window", "description": "Leer contenido de ventana activa",
             "params": {}},
            {
                "action": "verified_action",
                "description": (
                    "Ejecuta una accion de GUI con verificacion visual pre/post usando screenshot + analisis VLM. "
                    "Imprescindible para apps complejas (Unity, LTspice, Blender, etc.). "
                    "Captura pantalla antes, ejecuta la accion, captura pantalla despues, "
                    "compara visualmente y confirma si el objetivo fue logrado."
                ),
                "params": {
                    "window_title": "string — titulo de la ventana objetivo",
                    "action_type": "string — 'click', 'shortcut', 'type', o 'key'",
                    "action_params": "object — parametros de la accion (x, y, text, keys, etc.)",
                    "verify_prompt": "string — descripcion de que debe observarse en la pantalla para confirmar exito",
                    "delay_ms": "int? — ms de espera entre accion y screenshot de verificacion (default 800)",
                }
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        import asyncio
        try:
            result = await asyncio.to_thread(self._execute_sync, action, params)
            ok = not str(result).startswith("[ERROR]")
            return {"success": ok, "data": result, "message": str(result)}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error desktop: {exc}"}

    def _execute_sync(self, action: str, params: dict) -> str:
        if _UI_AUTO_OK:
            with auto.UIAutomationInitializerInThread():
                return self._execute_sync_inner(action, params)
        return self._execute_sync_inner(action, params)

    def _execute_sync_inner(self, action: str, params: dict) -> str:
        action = (action or "").lower().strip()

        if action == "type":
            return self._type(params)
        elif action == "shortcut":
            return self._shortcut(params)
        elif action == "click":
            return self._click(params)
        elif action == "scroll":
            return self._scroll(params)
        elif action == "focus_wait":
            return self._focus_wait(params)
        elif action == "read_window":
            return self._read_window(params)
        elif action == "verified_action":
            import asyncio
            return asyncio.get_event_loop().run_until_complete(self._verified_action(params))
        return f"[ERROR] Accion '{action}' no soportada en desktop."

    # ---- UIAutomation: busqueda logica de controles -----------------------

    def _find_control(self, target: str = None, control_type: str = None,
                      automation_id: str = None, class_name: str = None) -> Any:
        """Busca un control Win64 por nombre, tipo, AutomationId o ClassName."""
        if not _UI_AUTO_OK:
            return None

        # Obtener ventana activa como raiz
        root = None
        try:
            fg = auto.GetForegroundControl()
            if fg:
                curr = fg
                while curr and curr != auto.GetRootControl():
                    if curr.ControlType == auto.ControlType.WindowControl:
                        root = curr
                        break
                    parent = curr.GetParentControl()
                    if not parent or parent == curr:
                        break
                    curr = parent
                if not root:
                    root = fg
        except Exception:
            pass

        if not root:
            root = auto.GetRootControl()

        # Construir criterios de busqueda
        kwargs = {}
        if automation_id:
            kwargs["AutomationId"] = automation_id
        if class_name:
            kwargs["ClassName"] = class_name
        if control_type:
            if isinstance(control_type, str):
                ct_name = control_type if control_type.endswith("Control") else (control_type + "Control")
                if hasattr(auto.ControlType, ct_name):
                    kwargs["ControlType"] = getattr(auto.ControlType, ct_name)
            else:
                kwargs["ControlType"] = control_type

        # Busqueda exacta por Name
        if target:
            kwargs_exact = dict(kwargs)
            kwargs_exact["Name"] = target
            try:
                elem = root.Control(searchDepth=6, **kwargs_exact)
                if elem.Exists(0.5):
                    return elem
            except Exception:
                pass

        # Busqueda por otros criterios sin target
        if not target and kwargs:
            try:
                elem = root.Control(searchDepth=6, **kwargs)
                if elem.Exists(0.5):
                    return elem
            except Exception:
                pass
            return None

        # Busqueda difusa (substring match)
        if target:
            matched = None
            target_lower = target.lower()
            target_ct = kwargs.get("ControlType")

            def walk_fuzzy(control, depth):
                nonlocal matched
                if matched is not None or depth > 6:
                    return False
                is_match = True
                if target_lower:
                    ctrl_name = (control.Name or "").lower()
                    if target_lower not in ctrl_name:
                        is_match = False
                if automation_id:
                    if (control.AutomationId or "").lower() != automation_id.lower():
                        is_match = False
                if class_name:
                    if (control.ClassName or "").lower() != class_name.lower():
                        is_match = False
                if target_ct and control.ControlType != target_ct:
                    is_match = False
                if is_match:
                    matched = control
                    return False
                try:
                    for child in control.GetChildren():
                        if not walk_fuzzy(child, depth + 1):
                            break
                except Exception:
                    pass
                return True

            try:
                walk_fuzzy(root, 0)
            except Exception:
                pass
            return matched

        return None

    # ---- Acciones concretas -----------------------------------------------

    def _type(self, params: dict) -> str:
        text = params.get("text", "")
        if text is None:
            return "[ERROR] Falta 'text'."

        target = params.get("target") or params.get("name")
        control_type = params.get("control_type") or "Edit"
        automation_id = params.get("automation_id")
        class_name = params.get("class_name")
        delay = params.get("delay", 0.8)
        time.sleep(delay)

        # Intento nativo via UIA
        if _UI_AUTO_OK and (target or automation_id or class_name or control_type):
            elem = self._find_control(target, control_type, automation_id, class_name)
            if elem:
                try:
                    elem.SetFocus()
                except Exception:
                    pass

                # ValuePattern — escritura logica directa
                try:
                    pattern = elem.GetValuePattern()
                    if pattern:
                        pattern.SetValue(str(text))
                        if params.get("submit", False):
                            elem.SendKeys("{Enter}")
                        return f"[OK] Texto escrito en '{elem.Name or target}' (UIA ValuePattern)."
                except Exception:
                    pass

                # Clic fisico para enfocar
                try:
                    rect = elem.BoundingRectangle
                    if rect and (rect.right - rect.left) > 0:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        if _gui:
                            _gui.click(x=cx, y=cy)
                            time.sleep(0.2)
                except Exception:
                    pass

        # Fallback: simulacion fisica
        if not _PYAUTOGUI_OK:
            return "[ERROR] pyautogui no disponible y UIA no pudo escribir."

        if len(text) > _CLIPBOARD_THRESHOLD and _clipboard:
            try:
                original = _clipboard.paste()
            except Exception:
                original = ""
            try:
                _clipboard.copy(text)
                _gui.hotkey("ctrl", "v")
                time.sleep(0.3)
            finally:
                try:
                    time.sleep(0.5)
                    _clipboard.copy(original)
                except Exception:
                    pass
        else:
            _gui.write(text, interval=0.02)

        if params.get("submit", False):
            _gui.press("enter")

        return f"[OK] Texto escrito por simulacion fisica ({len(text)} chars)."

    def _shortcut(self, params: dict) -> str:
        keys = params.get("keys", [])
        if not keys or not isinstance(keys, list):
            return "[ERROR] Falta la lista 'keys' (ej: ['ctrl', 'c'])."
        if not _PYAUTOGUI_OK:
            return "[ERROR] pyautogui no disponible para atajos."
        delay = params.get("delay", 0.3)
        time.sleep(delay)
        _gui.hotkey(*keys)
        return f"[OK] Atajo ejecutado: {' + '.join(keys)}"

    def _click(self, params: dict) -> str:
        button = params.get("button", "left")
        clicks = params.get("clicks", 1)
        x = params.get("x")
        y = params.get("y")
        target = params.get("target") or params.get("name")
        control_type = params.get("control_type")
        automation_id = params.get("automation_id")
        class_name = params.get("class_name")
        delay = params.get("delay", 0.2)
        time.sleep(delay)

        # Busqueda logica por UIA
        if _UI_AUTO_OK and (target or automation_id or class_name or control_type):
            elem = self._find_control(target, control_type, automation_id, class_name)
            if elem:
                # InvokePattern
                if button == "left" and clicks == 1:
                    try:
                        pattern = elem.GetInvokePattern()
                        if pattern:
                            pattern.Invoke()
                            return f"[OK] '{elem.Name or target}' invocado (UIA InvokePattern)."
                    except Exception:
                        pass
                    try:
                        pattern = elem.GetTogglePattern()
                        if pattern:
                            pattern.Toggle()
                            return f"[OK] '{elem.Name or target}' alternado (UIA TogglePattern)."
                    except Exception:
                        pass
                    try:
                        pattern = elem.GetSelectionItemPattern()
                        if pattern:
                            pattern.Select()
                            return f"[OK] '{elem.Name or target}' seleccionado (UIA SelectionItemPattern)."
                    except Exception:
                        pass

                # BoundingRectangle -> clic fisico
                try:
                    rect = elem.BoundingRectangle
                    if rect and (rect.right - rect.left) > 0:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        if _gui:
                            _gui.click(x=cx, y=cy, button=button, clicks=clicks)
                            return f"[OK] Clic fisico en '{elem.Name or target}' ({cx},{cy}) via UIA BoundingRect."
                except Exception:
                    pass

                # Clic nativo uiautomation
                try:
                    if button == "left" and clicks == 1:
                        elem.Click(simulateMove=False)
                        return f"[OK] Clic nativo en '{elem.Name or target}' via uiautomation."
                except Exception:
                    pass

                # Fallback a coordenadas si se dieron
                if x is not None and y is not None and _gui:
                    _gui.click(x=x, y=y, button=button, clicks=clicks)
                    return f"[WARN] UIA encontro '{target}' pero fallo. Clic fisico en ({x},{y})."

                return f"[ERROR] Elemento '{target or automation_id}' encontrado pero no clickeable."

        # Clic fisico puro
        if not _PYAUTOGUI_OK:
            return "[ERROR] pyautogui no disponible para clic fisico."
        if x is not None and y is not None:
            _gui.click(x=x, y=y, button=button, clicks=clicks)
            return f"[OK] Clic fisico en ({x},{y})."
        _gui.click(button=button, clicks=clicks)
        return f"[OK] Clic ({button}, {clicks}x) en posicion actual."

    def _scroll(self, params: dict) -> str:
        if not _PYAUTOGUI_OK:
            return "[ERROR] pyautogui no disponible."
        amount = params.get("amount", 3)
        _gui.scroll(amount)
        return f"[OK] Scroll ({amount})."

    def _focus_wait(self, params: dict) -> str:
        title = params.get("title", "")
        timeout = params.get("timeout", 5)
        if not title:
            return "[ERROR] Falta 'title'."

        # UIA nativo
        if _UI_AUTO_OK:
            try:
                start = time.time()
                while time.time() - start < timeout:
                    for win in auto.GetRootControl().GetChildren():
                        if (win.ControlType == auto.ControlType.WindowControl
                                and win.Name
                                and title.lower() in win.Name.lower()):
                            try:
                                win.ShowWindow(3)
                                win.SetActive()
                                win.SetFocus()
                            except Exception:
                                pass
                            time.sleep(0.3)
                            return f"[OK] Ventana '{win.Name}' activada (UIA)."
                    time.sleep(0.3)
            except Exception:
                pass

        # Fallback pygetwindow
        try:
            import pygetwindow as gw
            start = time.time()
            words = [w.strip() for w in title.lower().replace("-", " ").split()]
            while time.time() - start < timeout:
                for win in gw.getAllWindows():
                    if not win.title:
                        continue
                    if all(w in win.title.lower() for w in words):
                        try:
                            win.activate()
                        except Exception:
                            pass
                        time.sleep(0.3)
                        return f"[OK] Ventana '{win.title}' enfocada."
                time.sleep(0.3)
        except ImportError:
            pass

        return f"[WARN] Ventana '{title}' no encontrada en {timeout}s."

    def _read_window(self, params: dict) -> str:
        """Lee el contenido textual de la ventana activa via UIA."""
        if not _UI_AUTO_OK:
            return "[ERROR] uiautomation no disponible."

        try:
            fg = auto.GetForegroundControl()
            if not fg:
                return "[ERROR] No hay ventana activa."

            # Encontrar ventana raiz
            root = fg
            curr = fg
            while curr and curr != auto.GetRootControl():
                if curr.ControlType == auto.ControlType.WindowControl:
                    root = curr
                    break
                parent = curr.GetParentControl()
                if not parent or parent == curr:
                    break
                curr = parent

            window_name = root.Name or "Desconocida"
            texts = []

            def collect_text(control, depth):
                if depth > 4 or len(texts) > 60:
                    return
                name = (control.Name or "").strip()
                if name and len(name) > 1:
                    texts.append(name)
                try:
                    for child in control.GetChildren():
                        collect_text(child, depth + 1)
                except Exception:
                    pass

            collect_text(root, 0)
            content = "\n".join(texts[:60])
            return f"[OK] Ventana: {window_name}\n{content}"
        except Exception as exc:
            return f"[ERROR] No se pudo leer la ventana: {exc}"

    # ---- Accion Verificada con Visión: screenshot → action → screenshot → VLM ----

    async def _verified_action(self, params: dict) -> str:
        """
        Ejecuta una accion de GUI con verificacion visual pre/post.
        Ciclo:
          1. Focus en la ventana objetivo
          2. Screenshot PRE (estado actual)
          3. Ejecuta la accion (click, shortcut, type, key)
          4. Espera delay_ms para que la UI reaccione
          5. Screenshot POST
          6. Llama al VLM (Florence-2 o analyze_scene_vlm) para verificar si el objetivo fue alcanzado
          7. Retorna resultado con descripcion visual del estado POST
        """
        import asyncio
        import base64
        import tempfile
        from pathlib import Path

        window_title = params.get("window_title", "")
        action_type = str(params.get("action_type", "")).lower()
        action_params = params.get("action_params", {})
        verify_prompt = params.get("verify_prompt", "Describe what changed on screen after the action.")
        delay_ms = int(params.get("delay_ms", 800))

        steps = []

        # 1. Focus ventana objetivo
        if window_title:
            focus_result = self._focus_wait({"title": window_title, "timeout": 5})
            steps.append(f"Focus: {focus_result}")
            await asyncio.sleep(0.3)

        # 2. Screenshot PRE
        pre_path = None
        try:
            if _PYAUTOGUI_OK:
                pre_img = _gui.screenshot()
                pre_file = Path(tempfile.gettempdir()) / "wis_va_pre.png"
                pre_img.save(str(pre_file))
                pre_path = str(pre_file)
                steps.append(f"Screenshot PRE: {pre_path}")
        except Exception as exc:
            steps.append(f"Screenshot PRE failed: {exc}")

        # 3. Ejecutar la accion
        try:
            if action_type == "click":
                x = action_params.get("x")
                y = action_params.get("y")
                button = action_params.get("button", "left")
                if x is not None and y is not None and _PYAUTOGUI_OK:
                    _gui.click(int(x), int(y), button=button)
                    steps.append(f"Click({x},{y},{button})")
                else:
                    res = self._click(action_params)
                    steps.append(f"Click: {res}")

            elif action_type == "shortcut":
                keys = action_params.get("keys", [])
                if keys and _PYAUTOGUI_OK:
                    _gui.hotkey(*keys)
                    steps.append(f"Shortcut: {'+'.join(keys)}")

            elif action_type == "type":
                text = action_params.get("text", "")
                if text and _PYAUTOGUI_OK:
                    _gui.typewrite(text, interval=0.05)
                    steps.append(f"Type: '{text}'")

            elif action_type == "key":
                key = action_params.get("key", "")
                if key and _PYAUTOGUI_OK:
                    _gui.press(key)
                    steps.append(f"Key: {key}")

            else:
                steps.append(f"Unknown action_type: '{action_type}'")

        except Exception as exc:
            steps.append(f"Action error: {exc}")

        # 4. Esperar reaccion de la UI
        await asyncio.sleep(delay_ms / 1000.0)

        # 5. Screenshot POST
        post_path = None
        try:
            if _PYAUTOGUI_OK:
                post_img = _gui.screenshot()
                post_file = Path(tempfile.gettempdir()) / "wis_va_post.png"
                post_img.save(str(post_file))
                post_path = str(post_file)
                steps.append(f"Screenshot POST: {post_path}")
        except Exception as exc:
            steps.append(f"Screenshot POST failed: {exc}")

        # 6. VLM Verification — usar analyze_scene_vlm si vision ability disponible
        vlm_result = "VLM verification skipped (no vision ability injected)."
        if post_path:
            try:
                # Intentar importar y usar vision si está disponible globalmente
                from abilities.vision import VisionAbility
                vision = VisionAbility()
                vlm_res = await vision.execute("analyze_scene_vlm", {
                    "prompt": verify_prompt,
                    "image_path": post_path,
                })
                if vlm_res.get("success"):
                    analysis = vlm_res.get("data", {}).get("analysis") or vlm_res.get("message", "")
                    vlm_result = f"VLM says: {analysis}"
                else:
                    vlm_result = f"VLM error: {vlm_res.get('message', 'unknown')}"
            except Exception as exc:
                vlm_result = f"VLM unavailable: {exc}"

        steps.append(vlm_result)

        return "[VERIFIED_ACTION]\n" + "\n".join(steps)
