"""
WIS Vision Ability - Camera and image analysis.
Habilidad de vision: analisis de camara e imagenes.
========================================
Usa OpenCV (cv2) para capturar imagenes desde la camara y realizar
analisis basicos: deteccion de rostros y descripcion estadistica de la escena.
Esta pensada para funcionar en robots con webcam integrada o USB.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Any

from .base import Ability


class VisionAbility(Ability):
    """
    Habilidad de vision por computador.

    Notas:
      - Requiere el paquete 'opencv-python' (cv2) y numpy.
      - El clasificador Haar para rostros se carga desde los datos que
        distribuye el propio cv2; si no existe, se desactiva la deteccion.
      - Si no hay camara disponible, las acciones fallan de forma graceful.
    """

    def __init__(self, camera_index: int = 0):
        # Indice del dispositivo de camara (0 = camara por defecto)
        self._camera_index = camera_index
        self._face_cascade = None  # Clasificador Haar (lazy load)
        self._cascade_ready = False

    # ------------------------------------------------------------------ #
    # Metadatos requeridos por Ability
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "vision"

    @property
    def description(self) -> str:
        return "Camera capture and basic image analysis (faces, scene stats)."

    @property
    def domain(self) -> str:
        return "vision"

    # ------------------------------------------------------------------ #
    # Helpers internos
    # ------------------------------------------------------------------ #
    def _import_cv2(self):
        """Importa cv2 de forma segura. Devuelve el modulo o None."""
        try:
            import cv2  # type: ignore

            return cv2
        except Exception:
            return None

    def _load_face_cascade(self, cv2_module) -> bool:
        """Carga el clasificador Haar para deteccion de rostros."""
        if self._cascade_ready:
            return self._face_cascade is not None
        try:
            # cv2.data.haarcascades contiene los XML preentrenados
            cascade_path = os.path.join(
                cv2_module.data.haarcascades,
                "haarcascade_frontalface_default.xml",
            )
            if not os.path.exists(cascade_path):
                self._face_cascade = None
                self._cascade_ready = True
                return False
            self._face_cascade = cv2_module.CascadeClassifier(cascade_path)
            self._cascade_ready = True
            return True
        except Exception:
            self._face_cascade = None
            self._cascade_ready = True
            return False

    def _capture_sync(self) -> tuple:
        """
        Captura un frame de la camara (sincrono) reusando el dispositivo.
        Devuelve (cv2, frame) o (None, None) si falla.
        """
        cv2 = self._import_cv2()
        if cv2 is None:
            return None, None
        try:
            if not hasattr(self, "_cap") or self._cap is None or not self._cap.isOpened():
                self._cap = cv2.VideoCapture(self._camera_index)
                for _ in range(3):
                    self._cap.read()
            ok, frame = self._cap.read()
            if not ok or frame is None:
                return cv2, None
            return cv2, frame
        except Exception:
            return cv2, None

    def close(self) -> None:
        """Libera el dispositivo de camara."""
        if hasattr(self, "_cap") and self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _save_temp_image(self, cv2_module, frame) -> str:
        """Guarda el frame en un archivo temporal PNG y devuelve la ruta."""
        filename = f"wis_capture_{int(time.time())}.png"
        tmp_dir = tempfile.gettempdir()
        path = os.path.join(tmp_dir, filename)
        cv2_module.imwrite(path, frame)
        return path

    def _describe_scene(self, cv2_module, frame) -> dict:
        """Genera una descripcion estadistica basica de la escena."""
        try:
            import numpy as np  # type: ignore
        except Exception:
            return {"note": "numpy no disponible, descripcion limitada."}

        gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
        mean_brightness = float(np.mean(gray))
        # Clasificacion tosca de iluminacion
        if mean_brightness < 60:
            level = "dark"
        elif mean_brightness > 200:
            level = "very bright"
        elif mean_brightness < 110:
            level = "dim"
        else:
            level = "well lit"

        # Color promedio BGR -> RGB para reporte legible
        avg_color = np.mean(frame.reshape(-1, 3), axis=0)  # BGR
        avg_rgb = [int(avg_color[2]), int(avg_color[1]), int(avg_color[0])]

        return {
            "brightness": round(mean_brightness, 2),
            "lighting": level,
            "average_color_rgb": avg_rgb,
            "resolution": {"width": int(frame.shape[1]), "height": int(frame.shape[0])},
        }

    def _detect_faces_sync(self, cv2_module, frame) -> list:
        """Ejecuta el clasificador Haar y devuelve la lista de rostros (x,y,w,h)."""
        if not self._load_face_cascade(cv2_module):
            return []
        gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
        )
        # Convertir de numpy arrays a listas planas para serializar facil
        result = []
        for (x, y, w, h) in faces:
            result.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h)})
        return result

    # ------------------------------------------------------------------ #
    # Ejecucion de acciones
    # ------------------------------------------------------------------ #
    async def execute(self, action: str, params: dict) -> dict:
        """
        Acciones soportadas:
          - capture:       toma una foto y devuelve la ruta del archivo.
          - detect_faces:  captura y detecta rostros (haar cascade).
          - describe:      captura y devuelve estadisticas de brillo/color.
        """
        action = (action or "").lower().strip()

        if action == "capture":
            return await self._action_capture(params)
        if action == "detect_faces":
            return await self._action_detect_faces(params)
        if action == "describe":
            return await self._action_describe(params)
        if action in ("analyze_scene_vlm", "vlm", "analyze_vlm"):
            return await self._action_analyze_scene_vlm(params)

        return {
            "success": False,
            "data": None,
            "message": f"Accion de vision no reconocida: '{action}'.",
        }

    async def _action_capture(self, params: dict) -> dict:
        """Captura una imagen de la camara y la guarda en disco."""
        cv2_module, frame = await asyncio.to_thread(self._capture_sync)
        if cv2_module is None:
            return {
                "success": False,
                "data": None,
                "message": "OpenCV (cv2) no esta disponible.",
            }
        if frame is None:
            return {
                "success": False,
                "data": None,
                "message": "No se pudo capturar imagen (sin camara o dispositivo ocupado).",
            }
        path = await asyncio.to_thread(self._save_temp_image, cv2_module, frame)
        return {
            "success": True,
            "data": {"path": path},
            "message": f"Imagen guardada en {path}.",
        }

    async def _action_detect_faces(self, params: dict) -> dict:
        """Captura una imagen y detecta rostros en ella."""
        cv2_module, frame = await asyncio.to_thread(self._capture_sync)
        if cv2_module is None:
            return {"success": False, "data": None, "message": "OpenCV no disponible."}
        if frame is None:
            return {
                "success": False,
                "data": None,
                "message": "No se pudo capturar imagen.",
            }
        faces = await asyncio.to_thread(self._detect_faces_sync, cv2_module, frame)
        # Si se solicita, tambien guardamos la imagen con los rostros marcados
        annotated_path = None
        if params.get("save", False):
            annotated = frame.copy()
            for f in faces:
                cv2_module.rectangle(
                    annotated,
                    (f["x"], f["y"]),
                    (f["x"] + f["w"], f["y"] + f["h"]),
                    (0, 255, 0),
                    2,
                )
            annotated_path = await asyncio.to_thread(
                self._save_temp_image, cv2_module, annotated
            )
        return {
            "success": True,
            "data": {"count": len(faces), "faces": faces, "annotated_path": annotated_path},
            "message": f"Se detectaron {len(faces)} rostro(s).",
        }

    async def _action_describe(self, params: dict) -> dict:
        """Captura una imagen y devuelve estadisticas de la escena."""
        cv2_module, frame = await asyncio.to_thread(self._capture_sync)
        if cv2_module is None:
            return {"success": False, "data": None, "message": "OpenCV no disponible."}
        if frame is None:
            return {
                "success": False,
                "data": None,
                "message": "No se pudo capturar imagen.",
            }
        scene = await asyncio.to_thread(self._describe_scene, cv2_module, frame)
        return {
            "success": True,
            "data": scene,
            "message": f"Escena {scene.get('lighting', 'unknown')}, brillo ~{scene.get('brightness', 0)}.",
        }

    async def _action_analyze_scene_vlm(self, params: dict) -> dict:
        """Captura un fotograma de la camara (o lee imagen) y lo codifica en Base64 para razonamiento VLM."""
        import base64

        prompt = str(params.get("prompt", "Describe en detalle la escena visual, objetos y posicionamiento.")).strip()
        image_path = params.get("image_path")

        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "success": True,
                    "data": {
                        "prompt": prompt,
                        "b64_image": b64_data[:100] + "...[TRUNCATED]",
                        "image_path": image_path,
                    },
                    "message": f"Imagen codificada en Base64 lista para VLM: '{prompt}'.",
                }
            except Exception as exc:
                return {"success": False, "data": None, "message": f"Error leyendo imagen '{image_path}': {exc}"}

        cv2_module, frame = await asyncio.to_thread(self._capture_sync)
        if cv2_module is None or frame is None:
            return {"success": False, "data": None, "message": "No se pudo capturar imagen de la camara para VLM."}

        def _encode_b64():
            _, buffer = cv2_module.imencode(".jpg", frame)
            return base64.b64encode(buffer).decode("utf-8")

        try:
            b64_str = await asyncio.to_thread(_encode_b64)
            return {
                "success": True,
                "data": {
                    "prompt": prompt,
                    "b64_image": b64_str[:100] + "...[TRUNCATED]",
                    "b64_full_length": len(b64_str),
                },
                "message": f"Fotograma en vivo capturado y preparado para VLM con prompt: '{prompt}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error procesando VLM: {exc}"}

    # ------------------------------------------------------------------ #
    # Esquema para el LLM
    # ------------------------------------------------------------------ #
    def get_schema(self) -> list:
        return [
            {
                "action": "capture",
                "description": "Capture a single photo from the camera and return the file path.",
                "params": {},
            },
            {
                "action": "detect_faces",
                "description": "Capture a photo and detect human faces using a Haar cascade.",
                "params": {"save": "bool (optional) - save annotated image with boxes"},
            },
            {
                "action": "describe",
                "description": "Capture a photo and return basic scene stats (brightness, color).",
                "params": {},
            },
            {
                "action": "analyze_scene_vlm",
                "description": "Captura un fotograma y genera un payload Base64 para razonamiento espacial VLM (Visión Multimodal).",
                "params": {
                    "prompt": "string (opcional) — pregunta o indicación de análisis visual.",
                    "image_path": "string (opcional) — ruta a imagen estática en lugar de cámara en vivo."
                },
            },
        ]
