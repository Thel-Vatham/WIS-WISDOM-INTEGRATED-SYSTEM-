"""
WIS Proactivity Engine - Motor de reglas, rutinas y sugerencias proactivas.
==========================================================================
Permite configurar:
  - Reglas reactivas: disparan sugerencias cuando ocurre un evento.
  - Rutinas programadas: tareas periodicas (daily/weekly/manual).
  - Inbox de sugerencias con estados (pending/accepted/dismissed/done).
  - Daemon thread que revisa rutinas cada N segundos.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("wis.core.proactivity")

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|credential|authorization|bearer)\s*[:=]\s*[^,\s]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]{12,}"), r"\1[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), "[TOKEN]"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProactivityEngine:
    """Motor de proactividad con reglas, rutinas e inbox."""

    def __init__(self, path: Optional[Path] = None, check_interval: int = 60):
        self._path = Path(path) if path else Path("Data/proactivity.json")
        self._lock = threading.Lock()
        self._check_interval = max(10, int(check_interval))
        self._daemon_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_suggestion_callbacks: List[Any] = []

    # ---- Persistence -------------------------------------------------------

    def _empty(self) -> dict:
        return {"version": 2, "rules": {}, "routines": {}, "inbox": []}

    def _load(self) -> dict:
        if not self._path.exists():
            return self._empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(data, dict):
                return self._empty()
            data.setdefault("version", 2)
            data.setdefault("rules", {})
            data.setdefault("routines", {})
            data.setdefault("inbox", [])
            return data
        except Exception:
            return self._empty()

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ---- Rules CRUD --------------------------------------------------------

    def add_rule(self, *, name: str, event_type: str = "*",
                 pattern: str = "*", suggestion_prompt: str = "",
                 boundaries: Optional[List[str]] = None,
                 enabled: bool = True) -> dict:
        rule_id = "pro_" + uuid.uuid4().hex[:12]
        rule = {
            "id": rule_id,
            "name": self._safe_text(str(name or rule_id), 160),
            "event_type": str(event_type or "*")[:120],
            "pattern": self._safe_text(str(pattern or "*"), 500),
            "suggestion_prompt": self._safe_text(str(suggestion_prompt or ""), 2000),
            "boundaries": list(boundaries or [
                "never_send_without_approval",
                "never_delete_files",
                "summarize_before_acting",
            ]),
            "enabled": bool(enabled),
            "created_at": _utc_now(),
        }
        with self._lock:
            data = self._load()
            data["rules"][rule_id] = rule
            self._save(data)
        return rule

    def list_rules(self, include_disabled: bool = True) -> List[dict]:
        rules = list(self._load().get("rules", {}).values())
        if not include_disabled:
            rules = [r for r in rules if r.get("enabled")]
        return sorted(rules, key=lambda r: r.get("created_at", ""), reverse=True)

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            data = self._load()
            removed = data.get("rules", {}).pop(rule_id, None) is not None
            self._save(data)
            return removed

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> bool:
        with self._lock:
            data = self._load()
            rule = data.get("rules", {}).get(rule_id)
            if not rule:
                return False
            rule["enabled"] = bool(enabled)
            self._save(data)
            return True

    # ---- Routines CRUD -----------------------------------------------------

    def add_routine(self, *, name: str, cadence: str = "daily",
                    suggestion_prompt: str = "", time_of_day: str = "",
                    boundaries: Optional[List[str]] = None,
                    enabled: bool = True) -> dict:
        cadence = self._normalize_cadence(cadence)
        now = datetime.now(timezone.utc)
        routine_id = "routine_" + uuid.uuid4().hex[:12]
        routine = {
            "id": routine_id,
            "name": self._safe_text(str(name or routine_id), 160),
            "cadence": cadence,
            "time_of_day": self._normalize_time(time_of_day),
            "suggestion_prompt": self._safe_text(str(suggestion_prompt or ""), 2000),
            "boundaries": list(boundaries or [
                "never_send_without_approval",
                "summarize_before_acting",
            ]),
            "enabled": bool(enabled),
            "last_run_at": "",
            "next_run_at": self._compute_next_run({"cadence": cadence, "time_of_day": self._normalize_time(time_of_day)}, now).isoformat(),
            "created_at": _utc_now(),
        }
        with self._lock:
            data = self._load()
            data.setdefault("routines", {})[routine_id] = routine
            self._save(data)
        return routine

    def list_routines(self, include_disabled: bool = True) -> List[dict]:
        routines = list(self._load().get("routines", {}).values())
        if not include_disabled:
            routines = [r for r in routines if r.get("enabled")]
        return sorted(routines, key=lambda r: r.get("next_run_at") or r.get("created_at", ""))

    def delete_routine(self, routine_id: str) -> bool:
        with self._lock:
            data = self._load()
            removed = data.get("routines", {}).pop(routine_id, None) is not None
            self._save(data)
            return removed

    # ---- Inbox & Events ----------------------------------------------------

    def record_event(self, *, event_type: str, text: str = "",
                     payload: Optional[dict] = None) -> List[dict]:
        safe_text = self._safe_text(str(text or ""), 1000)
        suggestions = []
        with self._lock:
            data = self._load()
            for rule in data.get("rules", {}).values():
                if not rule.get("enabled", True):
                    continue
                if not self._matches(rule, event_type, text):
                    continue
                sug = {
                    "id": "sug_" + uuid.uuid4().hex[:12],
                    "ts": _utc_now(),
                    "status": "pending",
                    "rule_id": rule["id"],
                    "rule_name": rule.get("name", ""),
                    "event_type": event_type,
                    "prompt": rule.get("suggestion_prompt", ""),
                    "boundaries": list(rule.get("boundaries") or []),
                    "event": {"text": safe_text, "payload": self._safe_payload(payload or {})},
                }
                suggestions.append(sug)
                data["inbox"].append(sug)
            data["inbox"] = list(data.get("inbox") or [])[-300:]
            self._save(data)
        for sug in suggestions:
            self._notify_suggestion(sug)
        return suggestions

    def run_due_routines(self, now: Optional[datetime] = None) -> List[dict]:
        current = now or datetime.now(timezone.utc)
        suggestions = []
        with self._lock:
            data = self._load()
            for routine in data.get("routines", {}).values():
                if not routine.get("enabled", True):
                    continue
                next_run = self._parse_dt(routine.get("next_run_at"))
                if next_run > current:
                    continue
                sug = {
                    "id": "sug_" + uuid.uuid4().hex[:12],
                    "ts": current.isoformat(),
                    "status": "pending",
                    "rule_id": routine.get("id", ""),
                    "rule_name": routine.get("name", ""),
                    "event_type": "routine.due",
                    "prompt": routine.get("suggestion_prompt", "")[:2000],
                    "boundaries": list(routine.get("boundaries") or []),
                    "event": {"text": f"Routine due: {routine.get('name', '')[:160]}"},
                }
                suggestions.append(sug)
                data.setdefault("inbox", []).append(sug)
                routine["last_run_at"] = sug["ts"]
                routine["next_run_at"] = self._compute_next_run(routine, current).isoformat()
            if suggestions:
                data["inbox"] = list(data.get("inbox") or [])[-300:]
                self._save(data)
        for sug in suggestions:
            self._notify_suggestion(sug)
        return suggestions

    def inbox(self, limit: int = 50, statuses: Optional[List[str]] = None) -> List[dict]:
        items = list(self._load().get("inbox") or [])
        if statuses:
            allowed = set(statuses)
            items = [i for i in items if i.get("status") in allowed]
        items.sort(key=lambda i: i.get("ts", ""), reverse=True)
        return items[:max(1, min(int(limit), 200))]

    def set_suggestion_status(self, suggestion_id: str, status: str) -> bool:
        if status not in {"pending", "accepted", "dismissed", "done"}:
            return False
        with self._lock:
            data = self._load()
            for item in data.get("inbox") or []:
                if item.get("id") == suggestion_id:
                    item["status"] = status
                    self._save(data)
                    return True
            return False

    # ---- Daemon thread -----------------------------------------------------

    def on_suggestion(self, callback) -> None:
        """Registra un callback que se invoca con cada sugerencia generada."""
        self._on_suggestion_callbacks.append(callback)

    def _notify_suggestion(self, sug: dict) -> None:
        for cb in self._on_suggestion_callbacks:
            try:
                cb(sug)
            except Exception as exc:
                logger.warning(f"Proactivity callback error: {exc}")

    def start_daemon(self) -> None:
        if self._daemon_thread and self._daemon_thread.is_alive():
            return
        self._stop_event.clear()
        self._daemon_thread = threading.Thread(
            target=self._daemon_loop, name="WIS-Proactivity-Daemon", daemon=True)
        self._daemon_thread.start()
        logger.info(f"Proactivity daemon started (interval={self._check_interval}s).")

    def stop_daemon(self) -> None:
        self._stop_event.set()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)

    def _daemon_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_due_routines()
            except Exception as exc:
                logger.warning(f"Proactivity daemon error: {exc}")
            self._stop_event.wait(timeout=self._check_interval)

    # ---- Helpers -----------------------------------------------------------

    def _matches(self, rule: dict, event_type: str, text: str) -> bool:
        rule_event = str(rule.get("event_type") or "*")
        if rule_event not in {"*", event_type}:
            return False
        pattern = str(rule.get("pattern") or "*")
        if pattern == "*":
            return True
        try:
            return re.search(pattern, text or "", re.IGNORECASE) is not None
        except re.error:
            return pattern.lower() in (text or "").lower()

    def _normalize_cadence(self, cadence: str) -> str:
        v = str(cadence or "daily").strip().lower()
        return v if v in {"daily", "weekly", "manual"} else "daily"

    def _normalize_time(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", text)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""

    def _compute_next_run(self, routine: dict, after: datetime) -> datetime:
        cadence = self._normalize_cadence(str(routine.get("cadence") or "daily"))
        if cadence == "manual":
            return after + timedelta(days=3650)
        days = 7 if cadence == "weekly" else 1
        tod = self._normalize_time(str(routine.get("time_of_day") or ""))
        if not tod:
            return after + timedelta(days=days)
        hour, minute = [int(p) for p in tod.split(":", 1)]
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        while candidate <= after:
            candidate += timedelta(days=days)
        return candidate

    def _parse_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value or "").strip()
            if not text:
                return datetime.now(timezone.utc)
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except Exception:
                return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _safe_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                lower = str(k).lower()
                if any(m in lower for m in ("token", "secret", "password", "api_key")):
                    out[str(k)] = "[REDACTED]"
                else:
                    out[str(k)] = self._safe_payload(v)
            return out
        if isinstance(value, list):
            return [self._safe_payload(i) for i in value[-30:]]
        if isinstance(value, str):
            return self._safe_text(value, 2000)
        return value

    def _safe_text(self, value: str, limit: int = 2000) -> str:
        clean = str(value or "")
        for pattern, replacement in _SECRET_PATTERNS:
            clean = pattern.sub(replacement, clean)
        return clean[:max(1, int(limit))]
