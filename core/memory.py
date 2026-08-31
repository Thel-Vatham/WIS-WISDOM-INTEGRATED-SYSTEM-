"""WIS Memory - Unified 3-tier memory engine.

Gestiona tres sistemas de memoria:
  1. Short-term (RAM queue): historial corto de conversacion de trabajo.
  2. Facts (SQLite): hechos atómicos (subject, relation, object) con upsert.
  3. Episodic (SQLite + Embeddings): vector recall semantico de interacciones pasadas.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import deque
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("wis.core.memory")

SHORT_TERM_LIMIT = 20
EMBEDDING_DIM = 384

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


class _Embedder:
    """Wrapper perezoso sobre fastembed con fallback determinista."""

    _instance: Optional["_Embedder"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "_Embedder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._model: Optional[Any] = None
        self._mode: str = "uninitialized"
        self._initialized = True
        self._load()

    def _load(self) -> None:
        try:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
            self._mode = "fastembed"
            logger.info("Embedder: fastembed 'sentence-transformers/all-MiniLM-L6-v2' loaded.")
        except Exception as exc:
            self._model = None
            self._mode = "fallback_hash"
            logger.warning("fastembed unavailable (%s), using token-hash fallback.", exc)

    def embed(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            return [0.0] * EMBEDDING_DIM

        if self._mode == "fastembed" and self._model is not None:
            try:
                gen = self._model.embed([text])
                vec = list(next(gen))
                return [float(x) for x in vec]
            except Exception as exc:
                logger.warning("fastembed error (%s), fallback to token-hash.", exc)

        return self._hash_embedding(text)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Tokeniza a palabras en minusculas, quitando acentos para robustez lexica."""
        import unicodedata

        normalized = unicodedata.normalize("NFKD", text or "")
        ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
        return re.findall(r"[a-z0-9]+", ascii_text.lower())

    @staticmethod
    def _hash_embedding(text: str) -> List[float]:
        """Embedding de fallback basado en feature-hashing de tokens (hashing trick).

        A diferencia del MD5 puro anterior (que producia vectores ortogonales para
        textos casi identicos), este metodo mapea cada token a una dimension estable
        mediante hash. Dos textos que comparten palabras solaparan dimensiones y
        tendran similitud coseno real. No captura semantica profunda, pero SI
        similitud lexica, lo que hace el recall funcional cuando fastembed no esta.
        """
        vec = [0.0] * EMBEDDING_DIM
        tokens = _Embedder._tokenize(text)
        # Stopwords minimas para no inflar dimensiones ruidosas.
        stop = {
            "the", "a", "an", "of", "to", "in", "on", "is", "are", "and", "or",
            "el", "la", "los", "las", "de", "que", "y", "o", "en", "un", "una",
            "es", "por", "para", "con", "se", "a",
        }
        for tok in tokens:
            if not tok or tok in stop or len(tok) < 2:
                continue
            # Doble hashing: uno para la dimension, otro para el signo (+1/-1).
            dim_h = hashlib.md5(tok.encode("utf-8")).digest()
            dim = int.from_bytes(dim_h[:4], "little") % EMBEDDING_DIM
            sign_h = hashlib.md5(b"sign|" + tok.encode("utf-8")).digest()
            sign = 1.0 if (sign_h[0] & 1) else -1.0
            vec[dim] += sign
        # Normalizacion L2 para que la similitud coseno este acotada en [-1, 1].
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-10:
            vec = [v / norm for v in vec]
        return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    if _NUMPY_AVAILABLE:
        arr_a = np.asarray(a, dtype=np.float32)
        arr_b = np.asarray(b, dtype=np.float32)
        na = float(np.linalg.norm(arr_a))
        nb = float(np.linalg.norm(arr_b))
        if na < 1e-10 or nb < 1e-10:
            return 0.0
        return float(np.dot(arr_a, arr_b) / (na * nb))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


class Memory:
    """Motor de memoria unificado de WIS."""

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
        self._short_term: Deque[Dict[str, Any]] = deque(maxlen=SHORT_TERM_LIMIT)
        self._embedder: _Embedder = embedder or _Embedder()

        self._ensure_schema()
        self._load_recent_history()

    def _load_recent_history(self) -> None:
        """Carga los últimos SHORT_TERM_LIMIT turnos conversacionales desde la tabla episodic
        de la base de datos al deque de RAM, evitando la amnesia al reiniciar WIS."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT user_input, response, created_at FROM episodic ORDER BY id DESC LIMIT ?",
                    (SHORT_TERM_LIMIT,)
                )
                rows = cur.fetchall()
                for user_input, response, created_at in reversed(rows):
                    self._short_term.append({
                        "user": user_input,
                        "response": response,
                        "timestamp": created_at
                    })
            if rows:
                logger.info("Short-term memory restored: %d turns loaded from disk.", len(rows))
        except Exception as exc:
            logger.warning("Could not load recent history into short-term memory: %s", exc)

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(subject, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);

                CREATE TABLE IF NOT EXISTS episodic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_input TEXT NOT NULL,
                    response TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS procedural (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    trigger TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    success_count INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._conn.commit()

    # --- Short-term ---
    def get_history(self) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for turn in list(self._short_term):
            messages.append({"role": "user", "content": turn.get("user", "")})
            messages.append({"role": "assistant", "content": turn.get("response", "")})
        return messages

    async def compress_history(self, local_client: Any) -> None:
        """Comprime el historial corto usando el LLM local para ahorrar contexto."""
        with self._lock:
            if len(self._short_term) < SHORT_TERM_LIMIT:
                return
            
            # Extrae los turnos mas antiguos (max 6). Limitar la cantidad y la
            # longitud de cada turno evita que el prompt de compresion exceda
            # el contexto del modelo local, lo que causaba un abort nativo de
            # ctransformers que cerraba WIS de golpe.
            to_compress = []
            for _ in range(6):
                if self._short_term:
                    to_compress.append(self._short_term.popleft())
                    
        if not to_compress:
            return
            
        history_text = ""
        for turn in to_compress:
            u = (turn.get("user") or "")[:150]
            r = (turn.get("response") or "")[:250]
            history_text += f"User: {u}\nAssistant: {r}\n"
            
        prompt = (
            "System: Summarize the following conversation in one short paragraph, focusing on key facts and decisions.\n"
            f"Conversation:\n{history_text}\nSummary: "
        )
        
        try:
            summary = await local_client.generate(prompt, max_tokens=80)
            if summary:
                from datetime import datetime
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with self._lock:
                    self._short_term.appendleft({
                        "user": "[System History Compression]",
                        "response": summary.strip(),
                        "timestamp": now_str
                    })
        except Exception as e:
            logger.warning(f"memory: compression failed: {e}")

    # --- Facts ---
    def add_fact(
        self,
        subject: str,
        relation: str,
        object: str,
        confidence: float = 1.0,
    ) -> None:
        s = str(subject or "").strip().lower()
        r = str(relation or "").strip().lower()
        o = str(object or "").strip()
        if not s or not r or not o:
            return
        conf = max(0.0, min(float(confidence), 1.0))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO facts (subject, relation, object, confidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subject, relation) DO UPDATE
                  SET object = excluded.object,
                      confidence = MAX(excluded.confidence, facts.confidence)
                """,
                (s, r, o, conf),
            )
            self._conn.commit()

    def get_facts(self, subject: Optional[str] = None, as_dict: bool = False) -> Any:
        with self._lock:
            if subject:
                cur = self._conn.execute(
                    "SELECT subject, relation, object, confidence FROM facts "
                    "WHERE subject=? ORDER BY confidence DESC",
                    (str(subject).strip().lower(),),
                )
            else:
                cur = self._conn.execute(
                    "SELECT subject, relation, object, confidence FROM facts "
                    "ORDER BY confidence DESC"
                )
            rows = cur.fetchall()
        if as_dict:
            return [{"subject": s, "relation": r, "object": o, "confidence": c} for (s, r, o, c) in rows]
        return [f"{s} {r} {o}" for (s, r, o, _c) in rows]

    # --- Episodic ---
    def remember(self, user_input: str, response: str) -> None:
        user_input = str(user_input or "")
        response = str(response or "")

        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._short_term.append({"user": user_input, "response": response, "timestamp": now_str})

        if not user_input.strip():
            return

        vec = self._embedder.embed(user_input)
        blob = self._serialize_embedding(vec)
        with self._lock:
            self._conn.execute(
                "INSERT INTO episodic (user_input, response, embedding, created_at) VALUES (?, ?, ?, ?)",
                (user_input, response, blob, now_str),
            )
            self._conn.commit()

    def recall(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []

        import re
        q_words = set(re.findall(r"\w+", query.lower()))

        query_vec = self._embedder.embed(query)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, user_input, response, embedding, created_at FROM episodic ORDER BY id DESC LIMIT 100"
            ).fetchall()

        total_rows = len(rows)
        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for idx, (row_id, ui, resp, blob, created_at) in enumerate(rows):
            vec = self._deserialize_embedding(blob)
            cosine_sim = _cosine_similarity(query_vec, vec)
            
            # Word overlap similarity (Jaccard token matching)
            doc_words = set(re.findall(r"\w+", f"{ui} {resp}".lower()))
            overlap_sim = len(q_words & doc_words) / max(1, len(q_words)) if q_words else 0.0

            if getattr(self._embedder, "_mode", "") == "fallback_hash":
                similarity = max(cosine_sim, overlap_sim)
            else:
                similarity = (0.7 * cosine_sim) + (0.3 * overlap_sim)

            recency_factor = 1.0 - (idx / max(1, total_rows))
            weighted_score = (0.80 * similarity) + (0.20 * recency_factor)
            candidates.append((weighted_score, {
                "id": row_id,
                "user_input": ui,
                "response": resp,
                "created_at": created_at,
                "score": weighted_score,
                "similarity": similarity,
            }))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in candidates[:top_k]]

    # --- Procedural Memory (4th Tier - Avrora Integration) ---
    def add_procedure(self, name: str, trigger: str, steps: List[Union[str, dict]]) -> None:
        """Guarda o actualiza una receta procedimental paso a paso."""
        import json
        n = str(name or "").strip().lower()
        t = str(trigger or "").strip()
        if not n or not steps:
            return
        steps_json = json.dumps(steps, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO procedural (name, trigger, steps)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE
                  SET steps = excluded.steps,
                      success_count = procedural.success_count + 1
                """,
                (n, t, steps_json),
            )
            self._conn.commit()

    def get_procedure(self, name_or_trigger: str) -> Optional[Dict[str, Any]]:
        """Busca una receta procedimental por nombre o coincidencia de trigger."""
        import json
        q = str(name_or_trigger or "").strip().lower()
        if not q:
            return None
        with self._lock:
            cur = self._conn.execute(
                "SELECT name, trigger, steps, success_count FROM procedural WHERE LOWER(name)=? OR LOWER(trigger) LIKE ?",
                (q, f"%{q}%"),
            )
            row = cur.fetchone()
        if row:
            name, trig, steps_str, count = row
            try:
                steps = json.loads(steps_str)
            except Exception:
                steps = []
            return {"name": name, "trigger": trig, "steps": steps, "success_count": count}
        return None

    def close(self) -> None:
        """Cierra la conexion SQLite limpiamente."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    @staticmethod
    def _serialize_embedding(vec: List[float]) -> bytes:
        if _NUMPY_AVAILABLE:
            return np.array(vec, dtype=np.float32).tobytes()
        import struct
        return struct.pack(f"{len(vec)}f", *vec)

    @staticmethod
    def _deserialize_embedding(blob: bytes) -> List[float]:
        if not blob:
            return [0.0] * EMBEDDING_DIM
        if _NUMPY_AVAILABLE:
            arr = np.frombuffer(blob, dtype=np.float32)
            return [float(x) for x in arr]
        import struct
        count = len(blob) // 4
        return list(struct.unpack(f"{count}f", blob))
