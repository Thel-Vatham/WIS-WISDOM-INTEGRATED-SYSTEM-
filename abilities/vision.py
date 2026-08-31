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

        # Florence-2 Model & Processor (lazy load)
        self._florence_model = None
        self._florence_processor = None
        self._florence_device = None

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
    # Florence-2 Vision Helpers
    # ------------------------------------------------------------------ #
    def _load_florence_model_sync(self, model_id: str = "microsoft/Florence-2-base") -> tuple:
        """Carga y cachea el modelo y procesador Florence-2 (lazy loading)."""
        if self._florence_model is not None and self._florence_processor is not None:
            return self._florence_model, self._florence_processor, self._florence_device

        try:
            import torch  # type: ignore
            from transformers import AutoProcessor, AutoModelForCausalLM  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Florence-2 requires 'torch' and 'transformers'. "
                f"Please install via 'pip install torch transformers'. Details: {exc}"
            )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        torch_dtype = torch.float16 if device == "cuda" else torch.float32

        # CPU Optimization: set max threads if running on CPU
        if device == "cpu":
            try:
                num_threads = min(8, os.cpu_count() or 4)
                torch.set_num_threads(num_threads)
            except Exception:
                pass

        model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch_dtype
        ).to(device)

        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

        self._florence_model = model
        self._florence_processor = processor
        self._florence_device = device
        return model, processor, device

    def _run_florence_sync(
        self,
        image_path_or_frame: Any,
        task_prompt: str = "<DENSE_REGION_CAPTION>",
        text_input: str = "",
        model_id: str = "microsoft/Florence-2-base",
    ) -> dict:
        """Ejecuta una tarea de Florence-2 sobre una imagen o fotograma."""
        from PIL import Image  # type: ignore

        if isinstance(image_path_or_frame, str) and os.path.exists(image_path_or_frame):
            pil_img = Image.open(image_path_or_frame).convert("RGB")
        elif hasattr(image_path_or_frame, "shape"):
            cv2 = self._import_cv2()
            if cv2 is not None:
                rgb = cv2.cvtColor(image_path_or_frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
            else:
                pil_img = Image.fromarray(image_path_or_frame)
        elif isinstance(image_path_or_frame, Image.Image):
            pil_img = image_path_or_frame
        else:
            raise ValueError(f"Formato de imagen inválido para Florence-2: {type(image_path_or_frame)}")

        model, processor, device = self._load_florence_model_sync(model_id)

        prompt = task_prompt + (f" {text_input}" if text_input else "")
        inputs = processor(text=prompt, images=pil_img, return_tensors="pt")

        import torch  # type: ignore
        if device == "cuda":
            inputs = {k: v.to(device, torch.float16) if v.dtype == torch.float32 else v.to(device) for k, v in inputs.items()}
        else:
            inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                do_sample=False,
            )

        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed_answer = processor.post_process_generation(
            generated_text,
            task=task_prompt,
            image_size=(pil_img.width, pil_img.height),
        )

        return {
            "task": task_prompt,
            "text_input": text_input,
            "parsed": parsed_answer,
            "raw_text": generated_text,
            "device": device,
        }

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
        if action in ("florence_analyze", "florence"):
            return await self._action_florence_analyze(params)
        if action in ("florence_grounding", "grounding", "find_element_visual"):
            return await self._action_florence_grounding(params)

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

    async def _action_florence_analyze(self, params: dict) -> dict:
        """Ejecuta tareas analíticas de Florence-2 (<DENSE_REGION_CAPTION>, <OCR_WITH_REGION>, etc.)."""
        task_prompt = str(params.get("task_prompt", "<DENSE_REGION_CAPTION>")).strip()
        text_input = str(params.get("text_input", "")).strip()
        image_path = params.get("image_path")

        image_target = image_path
        if not image_target or not os.path.exists(image_target):
            cv2_module, frame = await asyncio.to_thread(self._capture_sync)
            if frame is None:
                return {"success": False, "data": None, "message": "No image_path provided and camera capture failed."}
            image_target = frame

        try:
            result = await asyncio.to_thread(
                self._run_florence_sync,
                image_target,
                task_prompt,
                text_input,
            )
            return {
                "success": True,
                "data": result,
                "message": f"Florence-2 task '{task_prompt}' executed successfully on {result.get('device', 'cpu')}.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Florence-2 execution failed: {exc}"}

    async def _action_florence_grounding(self, params: dict) -> dict:
        """Encuentra coordenadas de bounding box [x1, y1, x2, y2] para un elemento visual o texto."""
        phrase = str(params.get("phrase") or params.get("target") or params.get("text_input") or "").strip()
        if not phrase:
            return {"success": False, "data": None, "message": "Parameter 'phrase' or 'target' is required for florence_grounding."}

        image_path = params.get("image_path")
        image_target = image_path
        if not image_target or not os.path.exists(image_target):
            cv2_module, frame = await asyncio.to_thread(self._capture_sync)
            if frame is None:
                return {"success": False, "data": None, "message": "No image_path provided and camera capture failed."}
            image_target = frame

        try:
            result = await asyncio.to_thread(
                self._run_florence_sync,
                image_target,
                "<CAPTION_TO_PHRASE_GROUNDING>",
                phrase,
            )
            parsed = result.get("parsed", {})
            grounding_data = parsed.get("<CAPTION_TO_PHRASE_GROUNDING>", {})
            bboxes = grounding_data.get("bboxes", [])
            labels = grounding_data.get("labels", [])

            return {
                "success": True,
                "data": {
                    "phrase": phrase,
                    "count": len(bboxes),
                    "bboxes": bboxes,
                    "labels": labels,
                    "device": result.get("device", "cpu"),
                },
                "message": f"Found {len(bboxes)} bounding box(es) for phrase '{phrase}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Florence-2 grounding failed: {exc}"}

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
            {
                "action": "florence_analyze",
                "description": "Executes Microsoft Florence-2 vision task (<DENSE_REGION_CAPTION>, <OCR_WITH_REGION>, <DETAILED_CAPTION>).",
                "params": {
                    "task_prompt": "string (optional, default '<DENSE_REGION_CAPTION>') - Florence-2 task tag.",
                    "text_input": "string (optional) - additional prompt context.",
                    "image_path": "string (optional) - path to static image file instead of live camera."
                },
            },
            {
                "action": "florence_grounding",
                "description": "Locates target UI elements or visual phrases on screen/image and returns precise [x1, y1, x2, y2] bounding boxes.",
                "params": {
                    "phrase": "string (required) - target element description (e.g. 'submit button', 'red icon', 'login input').",
                    "image_path": "string (optional) - path to static image file."
                },
            },
        ]
