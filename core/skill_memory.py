"""WIS Skill Memory - Persistent skill cache with confidence tracking.

SkillMemory (ActionCache v2) guarda las "skills aprendidas": combinaciones
input -> calls -> response que el agente descubrio (via LLM) y que pueden
reusarse sin volver a razonar.
"""
from __future__ import annotations

import re
import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core.memory import _Embedder, _cosine_similarity

logger = logging.getLogger("wis.core.skill_memory")

HEALTHY = 0.70          # Confianza minima para servir una skill cacheada.
DEGRADED = 0.30         # Por debajo de esto, la skill necesita re-descubrimiento.
FAILURE_PENALTY = 0.25  # Cuanto resta un fallo.
SUCCESS_BOOST = 0.10    # Cuanto suma un exito (hasta saturar en 1.0).
MIN_PROMOTIONS = 2      # Exitos minimos antes de considerar la skill confiable.
MAX_CONFIDENCE = 1.0
MIN_CONFIDENCE = 0.0


def _hash_input(user_input: str) -> str:
    normalized = (user_input or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_NON_CACHEABLE_ACTIONS = {"get_current_time", "capture", "detect_faces", "describe", "web_search", "listen_once"}

# Palabras estrictamente temporales cuya respuesta cambia con el tiempo y, por
# tanto, invalidan el cacheo de la skill. Se mantiene el filtro (es correcto:
# no se cachea "que hora es" ni "clima de hoy"), pero se quitaron terminos
# ambiguos ("date" = cita/fruta/fecha; "manana" no invalida un recordatorio
# fijo) que descartaban skills perfectamente cacheables.
_TEMPORAL_WORDS = {
    "hoy", "ayer", "mañana", "manana",
    "today", "yesterday", "tomorrow",
    "now", "ahora",  # el estado actual cambia de un momento a otro
    "fecha", "date", # pide la fecha del momento
    "time", "hora", "minuto", "segundo", # pide la hora
}


def _is_cacheable(user_input: str, calls: List[Dict[str, Any]]) -> bool:
    text_words = set(re.findall(r"\b\w+\b", (user_input or "").lower()))
    if text_words.intersection(_TEMPORAL_WORDS):
        return False
    for c in calls:
        if isinstance(c, dict):
            act = str(c.get("action") or c.get("name") or c.get("skill") or "").lower()
            if act in _NON_CACHEABLE_ACTIONS:
                return False
    return True


class SkillMemory:
    """Memoria persistente de habilidades aprendidas con confianza."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        embedder: Optional[_Embedder] = None,
    ) -> None:
        self._db_path: str = str(db_path) if db_path else ":memory:"
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, check_same_thread=False
        )
        self._lock = threading.RLock()
        self._embedder: _Embedder = embedder or _Embedder()
        self._vectors: List[Tuple[int, List[float], str]] = []

        self._ensure_schema()
        self._load_vectors()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    input_hash TEXT UNIQUE NOT NULL,
                    user_input TEXT NOT NULL,
                    calls_json TEXT NOT NULL,
                    response TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    pend_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_hit_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_skills_confidence ON skills(confidence);
                CREATE INDEX IF NOT EXISTS idx_skills_input_hash ON skills(input_hash);
                """
            )
            self._conn.commit()

    def _load_vectors(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, embedding, user_input FROM skills WHERE confidence >= ?",
                (HEALTHY,),
            ).fetchall()
        self._vectors = [
            (row[0], Memory._deserialize_embedding(row[1]), row[2]) for row in rows
        ] if hasattr(Memory, "_deserialize_embedding") else []

    def lookup(self, user_input: str) -> Optional[Dict[str, Any]]:
        input_hash = _hash_input(user_input)

        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, user_input, calls_json, response, confidence
                FROM skills WHERE input_hash=? AND confidence >= ?
                """,
                (input_hash, HEALTHY),
            ).fetchone()
        if row:
            return self._row_to_skill_dict(row)

        if not self._vectors:
            return None
        query_vec = self._embedder.embed(user_input)
        best_id: Optional[int] = None
        best_score: float = 0.0
        for (skill_id, vec, _text) in self._vectors:
            score = _cosine_similarity(query_vec, vec)
            if score > best_score:
                best_score = score
                best_id = skill_id

        if best_id is None or best_score < 0.85:
            return None

        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, user_input, calls_json, response, confidence
                FROM skills WHERE id=? AND confidence >= ?
                """,
                (best_id, HEALTHY),
            ).fetchone()
        if row:
            result = self._row_to_skill_dict(row)
            result["semantic_score"] = best_score
            return result

        return None

    @staticmethod
    def _row_to_skill_dict(row: Tuple) -> Dict[str, Any]:
        skill_id, user_input, calls_json, response, confidence = row
        try:
            calls = json.loads(calls_json) if calls_json else []
        except json.JSONDecodeError:
            calls = []
        return {
            "id": skill_id,
            "user_input": user_input,
            "calls": calls,
            "response": response,
            "confidence": confidence,
        }

    def record_success(
        self,
        user_input: str,
        calls: List[Dict[str, Any]],
        response: str,
    ) -> None:
        if not _is_cacheable(user_input, calls):
            return

        input_hash = _hash_input(user_input)
        calls_json = json.dumps(calls or [], ensure_ascii=False)
        embedding = self._embedder.embed(user_input)
        blob = Memory._serialize_embedding(embedding) if hasattr(Memory, "_serialize_embedding") else b""
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            existing = self._conn.execute(
                "SELECT id, confidence, pend_count FROM skills WHERE input_hash=?",
                (input_hash,),
            ).fetchone()
            if existing:
                skill_id, conf, pend = existing
                new_pend = int(pend) + 1
                new_conf = min(MAX_CONFIDENCE, float(conf) + SUCCESS_BOOST)
                if new_pend >= MIN_PROMOTIONS:
                    new_conf = max(new_conf, 0.85)
                self._conn.execute(
                    """
                    UPDATE skills
                    SET pend_count=?, hit_count=hit_count+1, confidence=?,
                        last_hit_at=?, response=?, calls_json=?
                    WHERE id=?
                    """,
                    (new_pend, new_conf, now, response, calls_json, skill_id),
                )
                self._conn.commit()
                if new_conf >= HEALTHY and new_pend >= MIN_PROMOTIONS:
                    self._upsert_ram_vector(skill_id, embedding, user_input)
            else:
                cur = self._conn.execute(
                    """
                    INSERT INTO skills
                        (input_hash, user_input, calls_json, response, embedding,
                         hit_count, pend_count, failure_count, confidence, last_hit_at)
                    VALUES (?, ?, ?, ?, ?, 1, 1, 0, 0.50, ?)
                    """,
                    (input_hash, user_input, calls_json, response, blob, now),
                )
                self._conn.commit()

    def record_failure(
        self,
        user_input: str,
        calls: List[Dict[str, Any]],
    ) -> None:
        input_hash = _hash_input(user_input)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, confidence FROM skills WHERE input_hash=?",
                (input_hash,),
            ).fetchone()
            if not row:
                return
            skill_id, conf = row
            new_conf = max(MIN_CONFIDENCE, float(conf) - FAILURE_PENALTY)
            self._conn.execute(
                """
                UPDATE skills
                SET failure_count=failure_count+1, confidence=?
                WHERE id=?
                """,
                (new_conf, skill_id),
            )
            self._conn.commit()

            if new_conf < HEALTHY:
                self._remove_ram_vector(skill_id)

    def _upsert_ram_vector(self, skill_id: int, vec: List[float], text: str) -> None:
        self._vectors = [v for v in self._vectors if v[0] != skill_id]
        self._vectors.append((skill_id, vec, text))

    def _remove_ram_vector(self, skill_id: int) -> None:
        self._vectors = [v for v in self._vectors if v[0] != skill_id]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            healthy = self._conn.execute(
                "SELECT COUNT(*) FROM skills WHERE confidence >= ?", (HEALTHY,)
            ).fetchone()[0]
            degraded = self._conn.execute(
                "SELECT COUNT(*) FROM skills WHERE confidence < ? AND confidence >= ?",
                (HEALTHY, DEGRADED),
            ).fetchone()[0]
            dead = self._conn.execute(
                "SELECT COUNT(*) FROM skills WHERE confidence < ?", (DEGRADED,)
            ).fetchone()[0]
        return {
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "dead": dead,
        }

    def close(self) -> None:
        """Cierra la conexion SQLite de SkillMemory."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# Import local Memory helper
from core.memory import Memory
