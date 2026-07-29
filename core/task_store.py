"""
WIS Task Store - Persistencia de objetivos, planes y subtareas.
================================================================
Almacena goals (objetivos), sus planes descompuestos (subtareas)
y el estado de ejecución. Es la base de datos del GoalManager.

Tablas:
  - goals:     objetivo con prioridad, deadline y estado.
  - subtasks:  pasos atómicos del plan (ordenados, con resultado).

Estados de goal:    planning → planned → executing → done | failed | cancelled
Estados de subtask: pending → executing → done | failed | skipped
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger("wis.core.task_store")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 5,
    status      TEXT NOT NULL DEFAULT 'planning',
    deadline    TEXT NOT NULL DEFAULT '',
    plan_summary TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subtasks (
    id          TEXT PRIMARY KEY,
    goal_id     TEXT NOT NULL,
    order_idx   INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    executed_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);

CREATE INDEX IF NOT EXISTS idx_subtasks_goal ON subtasks(goal_id, order_idx);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status, priority);
"""


class TaskStore:
    """Persistencia SQLite para goals y subtasks del GoalManager."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path and str(db_path) != ":memory:":
            self._path = Path(db_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connect_target = str(self._path)
        else:
            self._path = None
            connect_target = ":memory:"
        self._conn: sqlite3.Connection = sqlite3.connect(
            connect_target, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_db()

    # ---- Conexion ----------------------------------------------------------

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---- Goals -------------------------------------------------------------

    def create_goal(
        self,
        *,
        text: str,
        priority: int = 5,
        deadline: str = "",
    ) -> dict:
        goal_id = "goal_" + uuid.uuid4().hex[:12]
        now = _utc_now()
        goal = {
            "id": goal_id,
            "text": str(text or "")[:2000],
            "priority": max(1, min(10, int(priority))),
            "status": "planning",
            "deadline": str(deadline or "")[:60],
            "plan_summary": "",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO goals (id, text, priority, status, deadline,
                   plan_summary, created_at, updated_at)
                   VALUES (:id, :text, :priority, :status, :deadline,
                   :plan_summary, :created_at, :updated_at)""",
                goal,
            )
            self._conn.commit()
        return goal

    def update_goal_status(self, goal_id: str, status: str) -> bool:
        if status not in {"planning", "planned", "executing", "done", "failed", "cancelled"}:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE goals SET status=?, updated_at=? WHERE id=?",
                (status, _utc_now(), goal_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def update_goal_summary(self, goal_id: str, summary: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE goals SET plan_summary=?, updated_at=? WHERE id=?",
                (str(summary or "")[:4000], _utc_now(), goal_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_goal(self, goal_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def list_goals(
        self,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[dict]:
        sql = "SELECT * FROM goals"
        params: list = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def delete_goal(self, goal_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM subtasks WHERE goal_id=?", (goal_id,))
            cur = self._conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---- Subtasks ----------------------------------------------------------

    def add_subtask(
        self,
        *,
        goal_id: str,
        text: str,
        order_idx: int = 0,
    ) -> dict:
        sub_id = "task_" + uuid.uuid4().hex[:12]
        now = _utc_now()
        subtask = {
            "id": sub_id,
            "goal_id": goal_id,
            "order_idx": int(order_idx),
            "text": str(text or "")[:2000],
            "status": "pending",
            "result": "",
            "error": "",
            "created_at": now,
            "executed_at": "",
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO subtasks (id, goal_id, order_idx, text, status,
                   result, error, created_at, executed_at)
                   VALUES (:id, :goal_id, :order_idx, :text, :status,
                   :result, :error, :created_at, :executed_at)""",
                subtask,
            )
            self._conn.commit()
        return subtask

    def add_subtasks_batch(
        self, *, goal_id: str, texts: List[str]
    ) -> List[dict]:
        """Añade múltiples subtareas de una vez (para un plan completo)."""
        created: List[dict] = []
        now = _utc_now()
        with self._lock:
            # Limpiar plan anterior si existe.
            self._conn.execute("DELETE FROM subtasks WHERE goal_id=?", (goal_id,))
            for idx, text in enumerate(texts):
                sub_id = "task_" + uuid.uuid4().hex[:12]
                row = {
                    "id": sub_id,
                    "goal_id": goal_id,
                    "order_idx": idx,
                    "text": str(text or "")[:2000],
                    "status": "pending",
                    "result": "",
                    "error": "",
                    "created_at": now,
                    "executed_at": "",
                }
                self._conn.execute(
                    """INSERT INTO subtasks (id, goal_id, order_idx, text, status,
                       result, error, created_at, executed_at)
                       VALUES (:id, :goal_id, :order_idx, :text, :status,
                       :result, :error, :created_at, :executed_at)""",
                    row,
                )
                created.append(row)
            self._conn.commit()
        return created

    def get_subtasks(self, goal_id: str) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM subtasks WHERE goal_id=? ORDER BY order_idx",
                (goal_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_next_pending_subtask(self, goal_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM subtasks
                   WHERE goal_id=? AND status='pending'
                   ORDER BY order_idx LIMIT 1""",
                (goal_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_subtask(
        self,
        subtask_id: str,
        *,
        status: Optional[str] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        sets: List[str] = []
        params: List[Any] = []
        if status is not None:
            if status not in {"pending", "executing", "done", "failed", "skipped"}:
                return False
            sets.append("status=?")
            params.append(status)
            if status in {"done", "failed", "skipped"}:
                sets.append("executed_at=?")
                params.append(_utc_now())
        if result is not None:
            sets.append("result=?")
            params.append(str(result)[:4000])
        if error is not None:
            sets.append("error=?")
            params.append(str(error)[:2000])
        if not sets:
            return False
        params.append(subtask_id)
        sql = f"UPDATE subtasks SET {', '.join(sets)} WHERE id=?"
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount > 0

    # ---- Estadisticas ------------------------------------------------------

    def goal_progress(self, goal_id: str) -> dict:
        subs = self.get_subtasks(goal_id)
        total = len(subs)
        done = sum(1 for s in subs if s["status"] == "done")
        failed = sum(1 for s in subs if s["status"] == "failed")
        pending = sum(1 for s in subs if s["status"] == "pending")
        pct = round((done / total) * 100, 1) if total else 0.0
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "pending": pending,
            "percent": pct,
        }
