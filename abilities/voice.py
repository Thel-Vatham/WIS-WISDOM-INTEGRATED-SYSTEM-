"""
WIS Voice Ability - Text-to-speech con voces neuronales naturales.
===================================================================
Motor primario: edge-tts (Microsoft Edge Neural TTS) — voces premium
naturales, gratis, sin API key, requiere internet.
Fallback: pyttsx3 (offline) — cuando no hay conexion.

Deteccion automatica de idioma: si el texto es espanol, usa voz femenina
espanola automaticamente (es-ES-ElviraNeural). Si es ingles, usa en-US-
AriaNeural (femenina natural).
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Ability

logger = logging.getLogger("wis.abilities.voice")

# ──────────────────────────────────────────────────────────────────────────
# Voces neuronales por idioma (edge-tts)
# ──────────────────────────────────────────────────────────────────────────

# Mapa: idioma → (voz_predeterminada, voces_alternativas)
_NEURAL_VOICES: Dict[str, Dict[str, Any]] = {
    "es": {
        "default": "es-ES-ElviraNeural",        # Femenina, Espana
        "alternatives": [
            "es-MX-DalianNeural",                # Femenina, Mexico
            "es-AR-ElenaNeural",                 # Femenina, Argentina
            "es-CO-SalomeNeural",                # Femenina, Colombia
            "es-US-PalomaNeural",                # Femenina, EE.UU.
        ],
    },
    "en": {
        "default": "en-US-AriaNeural",          # Femenina, natural
        "alternatives": [
            "en-US-JennyNeural",                 # Femenina, conversacional
            "en-GB-SoniaNeural",                 # Femenina, UK
            "en-US-AnaNeural",                   # Femenina, nina
            "en-AU-NatashaNeural",               # Femenina, Australia
        ],
    },
}

# Palabras comunes en espanol para deteccion de idioma.
_SPANISH_INDICATORS = frozenset((
    "que", "hola", "como", "esta", "buenos", "buenas", "gracias", "por",
    "favor", "pero", "porque", "cuando", "donde", "quien", "cual", "cuanto",
    "muy", "mucho", "poco", "todo", "nada", "algo", "alguien", "tambien",
    "ahora", "despues", "antes", "aqui", "alli", "entonces", "pues",
    "del", "al", "para", "nuestro", "esto", "eso",
    "recuerdame", "dime", "habla", "escucha", "abre", "cierra", "busca",
    "hora", "tiempo", "dia", "hoy", "manana", "ayer", "noche", "tarde",
    "planta", "plantas", "regar", "agua", "calendario", "recordatorio",
))

# Caracteres especificos del espanol.
_SPANISH_CHARS = frozenset("áéíóúñü¿¡")


def _detect_language(text: str) -> str:
    """Always returns 'en' to maintain native English voice output."""
    return "en"


# Single native English female neural voice for the entire system
DEFAULT_VOICE = "en-US-AriaNeural"


def _get_neural_voice(language: str = "en") -> str:
    """Returns the single native English neural voice to maintain consistency."""
    return DEFAULT_VOICE


class VoiceAbility(Ability):
    """
    Habilidad de voz con voces neuronales naturales.

    Motor primario: edge-tts (Microsoft Neural TTS online).
    Fallback: pyttsx3 (offline, calidad reducida).
    """

    def __init__(
        self,
        rate: int = 170,
        language: str = "en",
        enabled: bool = True,
        voice_override: Optional[str] = None,
    ) -> None:
        self._rate = rate
        self._language = "en"
        self._enabled = enabled
        self._voice_override = voice_override or DEFAULT_VOICE
        self._pyttsx_engine = None  # Motor fallback (lazy init)

    # ------------------------------------------------------------------ #
    # Metadatos
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "voice"

    @property
    def description(self) -> str:
        return "Natural text-to-speech output using native English neural voices (edge-tts / Zira fallback)."

    @property
    def domain(self) -> str:
        return "voice"

    # ------------------------------------------------------------------ #
    # Motor edge-tts (primario)
    # ------------------------------------------------------------------ #

    async def _speak_edge_tts(self, text: str, voice: str) -> bool:
        """Sintetiza y reproduce audio con edge-tts. Devuelve True si ok."""
        try:
            import edge_tts
        except ImportError:
            logger.debug("edge-tts no disponible, usando fallback.")
            return False

        tmp_path: Optional[Path] = None
        try:
            rate_pct = int((self._rate - 170) / 170 * 100)
            rate_str = f"{rate_pct:+d}%"

            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate_str,
            )

            tmp_dir = Path(tempfile.gettempdir()) / "wis_tts"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / f"tts_{hash(text) & 0xFFFFFFFF}.mp3"

            await communicate.save(str(tmp_path))

            await asyncio.to_thread(self._play_audio, tmp_path)
            return True

        except Exception as exc:
            logger.warning(f"edge-tts fallo: {exc}")
            return False
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _play_audio(self, audio_path: Path) -> None:
        """Reproduce un archivo de audio usando el reproductor del OS."""
        import subprocess
        import sys

        path_str = str(audio_path)
        if sys.platform == "win32":
            try:
                subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path_str],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).wait()
            except (FileNotFoundError, OSError):
                import os
                os.startfile(path_str)  # type: ignore[attr-defined]
                import time
                time.sleep(0.5)
        elif sys.platform == "darwin":
            subprocess.Popen(["afplay", path_str]).wait()
        else:
            try:
                subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path_str],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).wait()
            except (FileNotFoundError, OSError):
                subprocess.Popen(["aplay", path_str]).wait()

    # ------------------------------------------------------------------ #
    # Motor pyttsx3 (fallback offline)
    # ------------------------------------------------------------------ #

    def _init_fallback_engine(self) -> bool:
        """Inicializa pyttsx3 como fallback. Devuelve True si ok."""
        if not self._enabled:
            return False
        if self._pyttsx_engine is not None:
            return True
        try:
            import pyttsx3

            self._pyttsx_engine = pyttsx3.init()
            self._pyttsx_engine.setProperty("rate", self._rate)
            self._select_fallback_voice("en")
            return True
        except Exception:
            self._pyttsx_engine = None
            return False

    def _select_fallback_voice(self, language: str = "en") -> None:
        """Selecciona estrictamente una voz nativa en ingles para pyttsx3."""
        try:
            voices = self._pyttsx_engine.getProperty("voices")
            if not voices:
                return

            female_keywords_en = ("zira", "aria", "jenny", "eva", "hazel", "samantha", "victoria", "karen", "catherine", "linda", "female")

            target_voice = None
            # 1. English female voice (e.g. Zira, Jenny, Hazel)
            for v in voices:
                v_name = str(getattr(v, "name", "")).lower()
                v_id = str(getattr(v, "id", "")).lower()
                v_langs = str(getattr(v, "languages", "")).lower()

                is_english = "en-us" in v_id or "en_us" in v_id or "en-gb" in v_id or "english" in v_name or "en-" in v_name or "en-us" in v_langs or "zira" in v_name or "hazel" in v_name
                if is_english and any(k in v_name for k in female_keywords_en):
                    target_voice = v
                    break

            # 2. Any English voice (e.g. David, English US/UK)
            if not target_voice:
                for v in voices:
                    v_name = str(getattr(v, "name", "")).lower()
                    v_id = str(getattr(v, "id", "")).lower()
                    v_langs = str(getattr(v, "languages", "")).lower()

                    if "en-us" in v_id or "en_us" in v_id or "english" in v_name or "zira" in v_name or "david" in v_name or "en-" in v_name or "en-us" in v_langs:
                        target_voice = v
                        break

            if not target_voice:
                target_voice = voices[0]

            self._pyttsx_engine.setProperty("voice", target_voice.id)

            # Direct SAPI5 COM object driver update for Windows stability
            try:
                drv = getattr(getattr(self._pyttsx_engine, "proxy", None), "_driver", None)
                if drv and hasattr(drv, "_tts") and hasattr(drv, "_tokenFromId"):
                    token = drv._tokenFromId(target_voice.id)
                    if token:
                        drv._tts.Voice = token
            except Exception:
                pass

            logger.info(f"Fallback TTS voice selected: {target_voice.name}")
        except Exception as exc:
            logger.warning(f"Error selecting fallback voice: {exc}")

    # ------------------------------------------------------------------ #
    # Ejecucion de acciones
    # ------------------------------------------------------------------ #

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").lower().strip()

        if action == "speak":
            return await self._speak(params)
        if action == "set_voice":
            return self._set_voice(params)
        if action == "set_rate":
            return self._set_rate(params)
        if action == "list_voices":
            return self._list_voices()
        if action == "mute":
            self._enabled = False
            return {
                "success": True,
                "data": {"enabled": False},
                "message": "Voz silenciada. WIS ya no hablara en voz alta a menos que se use la accion 'unmute'.",
            }
        if action == "unmute":
            self._enabled = True
            return {
                "success": True,
                "data": {"enabled": True},
                "message": "Voz activada. WIS hablara en voz alta cuando sea necesario.",
            }

        return {
            "success": False,
            "data": None,
            "message": f"Accion de voz no reconocida: '{action}'.",
        }

    async def _speak(self, params: dict) -> dict:
        """Sintetiza voz: intenta edge-tts primero, fallback a pyttsx3."""
        text = str(params.get("text", "")).strip()
        if not text:
            return {"success": False, "data": None, "message": "Texto vacio."}
        if not self._enabled:
            return {
                "success": False,
                "data": None,
                "message": "Voz deshabilitada en la configuracion.",
            }

        # Usar la voz femenina unica del sistema (sin cambiar dinamicamente)
        voice = self._voice_override or DEFAULT_VOICE
        lang = "en"

        # Intentar edge-tts (motor primario).
        spoken = await self._speak_edge_tts(text, voice)
        if spoken:
            return {
                "success": True,
                "data": {
                    "text": text,
                    "engine": "edge-tts",
                    "voice": voice,
                    "language": lang,
                    "rate": self._rate,
                },
                "message": "Text spoken using neural voice.",
            }

        # Fallback: pyttsx3 offline.
        if self._init_fallback_engine():
            try:
                self._select_fallback_voice(lang)
                await asyncio.to_thread(self._pyttsx_engine.say, text)
                await asyncio.to_thread(self._pyttsx_engine.runAndWait)
                return {
                    "success": True,
                    "data": {
                        "text": text,
                        "engine": "pyttsx3",
                        "language": lang,
                        "rate": self._rate,
                    },
                    "message": "Text spoken (offline voice).",
                }
            except Exception as exc:
                return {"success": False, "data": None, "message": f"TTS Error: {exc}"}

        return {
            "success": False,
            "data": None,
            "message": "No TTS engine available (install edge-tts or pyttsx3).",
        }

    def _set_voice(self, params: dict) -> dict:
        """Cambia la voz. Acepta nombres edge-tts o ids pyttsx3."""
        voice_id = params.get("id") or params.get("name")
        if not voice_id:
            # Si no se especifica, mostrar las opciones conocidas.
            return {
                "success": False,
                "data": None,
                "message": (
                    "Especifica 'name'. Voces disponibles: "
                    + ", ".join(
                        str(v["default"]) for v in _NEURAL_VOICES.values()
                    )
                ),
            }

        voice_name = str(voice_id).strip()

        # Si parece una voz edge-tts (contiene 'Neural'), guardarla.
        if "neural" in voice_name.lower():
            self._voice_override = voice_name
            return {
                "success": True,
                "data": {"voice": voice_name, "engine": "edge-tts"},
                "message": f"Voz neuronal seleccionada: {voice_name}",
            }

        # Si no, intentar como voz pyttsx3.
        if self._init_fallback_engine():
            try:
                voices = self._pyttsx_engine.getProperty("voices")
                for v in voices:
                    if voice_name.lower() in str(getattr(v, "name", "")).lower() or voice_name == getattr(v, "id", ""):
                        self._pyttsx_engine.setProperty("voice", v.id)
                        return {
                            "success": True,
                            "data": {"voice": v.id, "engine": "pyttsx3"},
                            "message": "Voz actualizada.",
                        }
            except Exception:
                pass

        return {"success": False, "data": None, "message": "Voz no encontrada."}

    def _set_rate(self, params: dict) -> dict:
        try:
            rate = int(params.get("rate", self._rate))
        except (TypeError, ValueError):
            return {"success": False, "data": None, "message": "Rate debe ser un entero."}
        self._rate = max(50, min(400, rate))
        if self._pyttsx_engine:
            self._pyttsx_engine.setProperty("rate", self._rate)
        return {
            "success": True,
            "data": {"rate": self._rate},
            "message": f"Velocidad = {self._rate} wpm.",
        }

    def _list_voices(self) -> dict:
        """Lista voces neuronales disponibles + voces offline del sistema."""
        neural: List[Dict[str, str]] = []
        for lang_key, voices in _NEURAL_VOICES.items():
            neural.append({"voice": str(voices["default"]), "language": lang_key, "type": "neural"})
            for alt in voices.get("alternatives", []):
                neural.append({"voice": str(alt), "language": lang_key, "type": "neural"})

        offline: List[Dict[str, str]] = []
        if self._init_fallback_engine():
            try:
                for v in self._pyttsx_engine.getProperty("voices"):
                    offline.append({
                        "id": str(getattr(v, "id", "")),
                        "name": str(getattr(v, "name", "")),
                        "type": "offline",
                    })
            except Exception:
                pass

        return {
            "success": True,
            "data": neural + offline,
            "message": f"{len(neural)} voces neuronales + {len(offline)} offline.",
        }

    # ------------------------------------------------------------------ #
    # Esquema para el LLM
    # ------------------------------------------------------------------ #
    def get_schema(self) -> list:
        return [
            {
                "action": "speak",
                "description": "Speak the given text aloud using natural neural voices. Language is auto-detected.",
                "params": {"text": "string (required) - text to synthesize"},
            },
            {
                "action": "set_voice",
                "description": "Change the active TTS voice. Use neural voice names like 'es-ES-ElviraNeural' or 'en-US-AriaNeural'.",
                "params": {"name": "string (optional) - neural voice name"},
            },
            {
                "action": "set_rate",
                "description": "Set speech rate in words per minute (typical 100-250, default 170).",
                "params": {"rate": "int"},
            },
            {
                "action": "list_voices",
                "description": "List available neural and offline TTS voices.",
                "params": {},
            },
            {
                "action": "mute",
                "description": "Silence the voice output completely. WIS will execute actions but not speak aloud.",
                "params": {},
            },
            {
                "action": "unmute",
                "description": "Re-enable voice output. WIS will speak aloud normally.",
                "params": {},
            },
        ]
