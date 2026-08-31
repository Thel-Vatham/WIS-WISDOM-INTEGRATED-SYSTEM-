"""
WIS Voice Ability — Kokoro-82M ONNX Engine (Voz Exclusiva: af_sky).
===================================================================
Utiliza únicamente el modelo neuronal Kokoro-82M ONNX con la voz 'af_sky'.
Todos los motores legacy robóticos (pyttsx3 / edge-tts) han sido eliminados.
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import soundfile as sf

from .base import Ability

logger = logging.getLogger("wis.abilities.voice")

DEFAULT_KOKORO_VOICE = "af_sky"


import unicodedata

def _clean_text_for_speech(text: str) -> str:
    """Limpia el texto antes de enviarlo a Kokoro para una síntesis natural sin lectura de símbolos ni rutas."""
    if not text:
        return ""
    # Convertir diacríticos y acentos a ASCII limpio (ej. Nicolás -> Nicolas) para fonetización perfecta en Kokoro
    clean = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('utf-8')
    # 1. Eliminar bloques de código
    clean = re.sub(r'```[\s\S]*?```', ' code block executed. ', clean)
    # 2. Formatear tablas markdown
    clean = re.sub(r'\|(?:\s*:?-+:?\s*\|)+', ' ', clean)
    clean = clean.replace('|', '. ')
    # 3. Limpiar rutas de archivos de Windows y Unix (ej. C:\Users\..\Report.docx -> Report.docx)
    clean = re.sub(r'[A-Za-z]:\\(?:[^\s\\]+\\)+([^\s\\]+)', r'\1', clean)
    clean = re.sub(r'(?:/[^\s/]+)+/([^\s/]+)', r'\1', clean)
    # 4. Reemplazar contrabarras (\) para que NUNCA pronuncie "backslash"
    clean = clean.replace('\\', ' ')
    clean = re.sub(r'https?://\S+', 'link', clean)
    # 5. Eliminar símbolos de formato markdown y puntuaciones raras (#, *, _, ~, `, ?, !)
    clean = re.sub(r'^#{1,6}\s+', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'[*#_>~`]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


class VoiceAbility(Ability):
    """
    Habilidad de Voz Neural de WIS — Kokoro-82M ONNX (Voz Exclusiva: af_sky).
    Sin motores robóticos de fallback ni pyttsx3/edge-tts.
    """

    def __init__(
        self,
        rate: int = 170,
        language: str = "en",
        enabled: bool = True,
        voice_override: Optional[str] = None,
    ) -> None:
        self._rate = rate
        self._enabled = enabled
        self._voice = DEFAULT_KOKORO_VOICE
        self._kokoro_engine = None

    @property
    def name(self) -> str:
        return "voice"

    @property
    def description(self) -> str:
        return "Natural neural text-to-speech engine using Kokoro-82M ONNX with 'af_sky' voice."

    @property
    def domain(self) -> str:
        return "voice"

    def _ensure_kokoro_engine(self):
        if self._kokoro_engine is not None:
            return self._kokoro_engine

        try:
            from kokoro_onnx import Kokoro
            models_dir = Path("Data/kokoro")
            models_dir.mkdir(parents=True, exist_ok=True)

            model_path = models_dir / "kokoro-v0_19.onnx"
            voices_path = models_dir / "voices.bin"

            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

            if not model_path.exists():
                logger.info("Downloading Kokoro-82M ONNX model to Data/kokoro...")
                req = urllib.request.Request("https://huggingface.co/thewh1teagle/Kokoro/resolve/main/kokoro-v0_19.onnx", headers=headers)
                with urllib.request.urlopen(req) as resp, open(model_path, "wb") as f:
                    f.write(resp.read())

            if not voices_path.exists():
                logger.info("Downloading Kokoro voices.bin to Data/kokoro...")
                req = urllib.request.Request("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin", headers=headers)
                with urllib.request.urlopen(req) as resp, open(voices_path, "wb") as f:
                    f.write(resp.read())

            self._kokoro_engine = Kokoro(str(model_path), str(voices_path))
            logger.info("Kokoro-82M ONNX Engine initialized successfully with voice 'af_sky'.")
            return self._kokoro_engine

        except Exception as exc:
            logger.error("Failed to initialize Kokoro ONNX engine: %s", exc)
            return None

    def get_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "action": "speak",
                "description": "Speak text out loud using natural Kokoro 'af_sky' neural voice.",
                "params": {"text": "Text to synthesize into spoken audio"},
            },
            {
                "action": "synthesize",
                "description": "Synthesize text to WAV bytes (returns base64 wav data, does NOT play audio — for streaming to browser).",
                "params": {"text": "Text to synthesize"},
            },
            {
                "action": "mute",
                "description": "Mute voice output.",
                "params": {},
            },
            {
                "action": "unmute",
                "description": "Unmute voice output.",
                "params": {},
            },
        ]

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").lower().strip()
        if action == "speak":
            return await self._speak(params)
        if action == "synthesize":
            return await self._synthesize_only(params)
        if action == "mute":
            self._enabled = False
            return {"success": True, "message": "Voice muted."}
        if action == "unmute":
            self._enabled = True
            return {"success": True, "message": "Voice unmuted."}
        return {"success": False, "message": f"Unknown action: '{action}'."}

    async def _speak(self, params: dict) -> dict:
        if not self._enabled:
            return {"success": False, "message": "Voice is muted."}
        raw_text = str(params.get("text", "")).strip()
        if not raw_text:
            return {"success": False, "message": "Empty text."}

        clean_text = _clean_text_for_speech(raw_text)
        if not clean_text:
            return {"success": False, "message": "No speakable text after cleaning."}

        try:
            engine = await asyncio.to_thread(self._ensure_kokoro_engine)
            if not engine:
                return {"success": False, "message": "Kokoro ONNX engine unavailable."}

            samples, sample_rate = await asyncio.to_thread(
                engine.create, clean_text, voice=self._voice, speed=1.0, lang="en-us"
            )

            tmp_dir = Path(tempfile.gettempdir()) / "wis_kokoro"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / f"sky_{hash(clean_text) & 0xFFFFFFFF}.wav"

            await asyncio.to_thread(sf.write, str(tmp_path), samples, sample_rate)
            await asyncio.to_thread(self._play_audio, tmp_path)

            return {
                "success": True,
                "data": {
                    "text": clean_text,
                    "voice": "af_sky",
                    "engine": "kokoro-82m-onnx",
                },
                "message": f"Spoken with Kokoro-82M ONNX (voice: af_sky): '{clean_text[:60]}...'",
            }
        except Exception as exc:
            logger.exception("Error in Kokoro synthesis: %s", exc)
            return {"success": False, "message": f"Kokoro TTS error: {exc}"}

    async def _synthesize_only(self, params: dict) -> dict:
        """Sintetiza texto a WAV y devuelve la ruta del archivo. NO reproduce audio.
        Usado por el endpoint /api/tts para stream al navegador."""
        raw_text = str(params.get("text", "")).strip()
        if not raw_text:
            return {"success": False, "message": "Empty text."}

        clean_text = _clean_text_for_speech(raw_text)
        if not clean_text:
            return {"success": False, "message": "No speakable text after cleaning."}

        try:
            engine = await asyncio.to_thread(self._ensure_kokoro_engine)
            if not engine:
                return {"success": False, "message": "Kokoro ONNX engine unavailable."}

            samples, sample_rate = await asyncio.to_thread(
                engine.create, clean_text, voice=self._voice, speed=1.0, lang="en-us"
            )

            tmp_dir = Path(tempfile.gettempdir()) / "wis_kokoro"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / f"sky_{hash(clean_text) & 0xFFFFFFFF}.wav"

            await asyncio.to_thread(sf.write, str(tmp_path), samples, sample_rate)
            # NO _play_audio — el navegador reproduce el WAV

            return {
                "success": True,
                "data": {"wav_path": str(tmp_path), "text": clean_text},
                "message": f"Synthesized (no playback): '{clean_text[:60]}...'",
            }
        except Exception as exc:
            logger.exception("Error in Kokoro synthesis: %s", exc)
            return {"success": False, "message": f"Kokoro TTS error: {exc}"}

    def _play_audio(self, audio_path: Path) -> None:
        """Reproduce el archivo WAV generado por Kokoro directamente a través del dispositivo de audio del sistema."""
        try:
            import sounddevice as sd
            import numpy as np
            data, samplerate = sf.read(str(audio_path), dtype="float32")
            sd.play(data, samplerate=samplerate, blocking=True)
        except Exception as exc:
            logger.error("sounddevice playback failed, falling back to subprocess: %s", exc)
            import subprocess, sys
            path_str = str(audio_path)
            if sys.platform == "win32":
                try:
                    subprocess.Popen(
                        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path_str],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).wait()
                except (FileNotFoundError, OSError):
                    import winsound
                    winsound.PlaySound(path_str, winsound.SND_FILENAME)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", path_str]).wait()
            else:
                subprocess.Popen(["aplay", path_str]).wait()
