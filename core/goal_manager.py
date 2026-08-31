"""
WIS Goal Manager - Orquestador de objetivos con planificación y ejecución.
==========================================================================
El GoalManager es el cerebro autónomo de WIS. Convierte un objetivo
de alto nivel (ej. "Recuérdame regar las plantas") en:

  1. PLAN      — descomposición en subtareas atómicas ordenadas (vía LLM).
  2. WALKTHROUGH — presentación legible del plan para el usuario.
  3. EXECUTE   — ejecución de cada subtarea a través del ActionPipeline.
  4. VERIFY    — verificación de completitud y reporte de resultados.

Ciclo de vida de un goal:
  planning → planned → executing → done | failed | cancelled

El manager corre un loop asíncrono de fondo que evalúa goals activos
cada `eval_interval` segundos, descompone los pendientes de planificación,
y ejecuta el siguiente paso de los que ya tienen plan.

Dependencias inyectables (DI):
  - pipeline:   ActionPipeline  (ejecución de cada subtarea).
  - llm_client: LLMClient       (descomposición de objetivos en plan).
  - task_store: TaskStore       (persistencia de goals/subtasks).

Eventos EventBus:
  goal.created, goal.planned, goal.walkthrough_ready,
  goal.subtask_started, goal.subtask_done, goal.subtask_failed,
  goal.completed, goal.failed, goal.cancelled
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.event_bus import event_bus
from core.task_store import TaskStore

if TYPE_CHECKING:
    from core.pipeline import ActionPipeline
    from core.llm_client import LLMClient

logger = logging.getLogger("wis.core.goal_manager")

# ──────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────

MAX_PLAN_STEPS = 8          # Máximo de subtareas por goal (evita planes infinitos)
MIN_PLAN_STEPS = 1          # Mínimo de subtareas (un objetivo trivial = 1 paso)
MAX_GOALS_CONCURRENT = 3    # Goals simultáneos en ejecución (evita saturación)
SUBTASK_TIMEOUT_S = 30      # Timeout por subtarea individual
PLAN_TIMEOUT_S = 25         # Timeout para la llamada LLM de planificación
MAX_RETRIES_PER_SUBTASK = 1 # Reintentos ante fallo transitorio

GOAL_STATUSES_ACTIVE = {"planning", "planned", "executing"}

# ──────────────────────────────────────────────────────────────────────────
# Prompt de planificación
# ──────────────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
You are a task decomposition expert for WIS, a cognitive assistant.
Given a goal, break it down into a sequence of concrete, atomic action steps.

Rules:
- Each step must be a single action that can be expressed as a natural-language \
instruction to the assistant (e.g. "Check the current time" or "Search for X").
- Minimum 1 step, maximum 8 steps.
- Steps must be ordered sequentially (step 1 first).
- Be specific and actionable. No vague steps like "think about it".
- Respond ONLY with a JSON object, no markdown fences, no commentary.

JSON format:
{"steps": ["Step 1 text", "Step 2 text", "Step 3 text"]}
"""

_REEVALUATOR_SYSTEM = """\
You are an adaptive plan evaluator for WIS, a cognitive assistant.
Given an overall goal, a step that was just executed with its actual outcome/output, and the remaining planned steps:
Determine if the remaining steps are still valid and sufficient, or if they must be adjusted, replaced, or added to.

Rules:
- Respond ONLY with a JSON object, no markdown fences, no commentary.
- If the remaining steps are still accurate and sufficient, respond with:
  {"valid": true, "reason": "Steps remain appropriate", "new_steps": []}
- If the plan must change due to a failure, new data, or changed context, respond with:
  {"valid": false, "reason": "Short explanation why plan changed", "new_steps": ["Updated step 1", "Updated step 2"]}

JSON format:
{"valid": true, "reason": "...", "new_steps": ["Step text"]}
"""


class GoalManager:
    """Orquestador de objetivos autónomos con planificación y ejecución."""

    def __init__(
        self,
        *,
        pipeline: "ActionPipeline",
        llm_client: "LLMClient",
        task_store: Optional[TaskStore] = None,
        db_path: Optional[Any] = None,
        eval_interval: float = 30.0,
    ) -> None:
        self.pipeline = pipeline
        self.llm_client = llm_client
        self.task_store: TaskStore = task_store or TaskStore(db_path)
        self.eval_interval = max(5.0, float(eval_interval))

        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._running = False
        self._lock = asyncio.Lock()

    # ────────────────────────────────────────────────────────────────────
    # API pública — registro de objetivos
    # ────────────────────────────────────────────────────────────────────

    async def add_goal(
        self,
        text: str,
        *,
        priority: int = 5,
        deadline: str = "",
        auto_plan: bool = True,
    ) -> dict:
        """Registra un objetivo y opcionalmente genera su plan inmediatamente."""
        goal = self.task_store.create_goal(
            text=text, priority=priority, deadline=deadline
        )
        event_bus.emit("goal.created", {
            "goal_id": goal["id"],
            "text": goal["text"],
            "priority": goal["priority"],
        })
        logger.info("Goal creado: %s → %s", goal["id"], goal["text"][:80])

        if auto_plan:
            try:
                await self.plan_goal(goal["id"])
            except Exception as exc:
                logger.warning("Planificación inicial falló para %s: %s", goal["id"], exc)

        return self.task_store.get_goal(goal["id"]) or goal

    # ────────────────────────────────────────────────────────────────────
    # PLAN — descomposición LLM en subtareas
    # ────────────────────────────────────────────────────────────────────

    async def plan_goal(self, goal_id: str) -> List[dict]:
        """Descompone un goal en subtareas usando el LLM y las persiste."""
        goal = self.task_store.get_goal(goal_id)
        if not goal:
            raise ValueError(f"Goal no encontrado: {goal_id}")

        steps = await self._decompose_with_llm(goal["text"])
        if not steps:
            steps = [goal["text"]]  # Fallback: el objetivo mismo como único paso

        # Truncar a límites seguros.
        steps = steps[:MAX_PLAN_STEPS]

        subtasks = self.task_store.add_subtasks_batch(
            goal_id=goal_id, texts=steps
        )

        # Generar resumen del plan.
        summary = self._build_plan_summary(goal, subtasks)
        self.task_store.update_goal_summary(goal_id, summary)
        self.task_store.update_goal_status(goal_id, "planned")

        event_bus.emit("goal.planned", {
            "goal_id": goal_id,
            "steps": len(subtasks),
            "summary": summary,
        })
        logger.info(
            "Goal %s planificado: %d subtareas", goal_id, len(subtasks)
        )
        return subtasks

    async def _decompose_with_llm(self, goal_text: str) -> List[str]:
        """Llama al LLM para descomponer el objetivo en pasos atómicos."""
        messages = [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": f"Goal: {goal_text.strip()}"},
        ]

        raw = await asyncio.wait_for(
            self._collect_llm_response(messages, temperature=0.3),
            timeout=PLAN_TIMEOUT_S,
        )

        steps = self._parse_plan_json(raw)
        if not steps:
            # Intentar extracción de líneas numeradas como fallback.
            steps = self._parse_plan_lines(raw)

        # Sanitizar: strip, descartar vacíos, deduplicar manteniendo orden.
        seen: set = set()
        clean: List[str] = []
        for s in steps:
            text = s.strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                clean.append(text[:300])
        return clean[:MAX_PLAN_STEPS]

    async def _collect_llm_response(
        self, messages: List[Dict[str, Any]], temperature: float = 0.3
    ) -> str:
        """Recolecta todos los chunks del stream LLM en un string."""
        chunks: List[str] = []
        async for chunk in self.llm_client.stream(
            messages, temperature=temperature
        ):
            chunks.append(chunk)
        return "".join(chunks)

    @staticmethod
    def _parse_plan_json(raw: str) -> List[str]:
        """Extrae la lista de pasos de un JSON del LLM."""
        # Buscar el primer { ... } en la respuesta.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        steps = data.get("steps") or data.get("tasks") or data.get("actions")
        if not isinstance(steps, list):
            return []
        return [str(s) for s in steps if s]

    @staticmethod
    def _parse_plan_lines(raw: str) -> List[str]:
        """Fallback: extrae pasos SOLO de líneas numeradas o con guion/bullet.
        Una sola línea sin prefijo de lista se considera texto libre, no un plan."""
        steps: List[str] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Solo aceptar líneas con prefijo de lista: "1.", "1)", "-", "*"
            if not re.match(r"^(?:\d+[.)]\s*|[-*]\s+)", line):
                continue
            cleaned = re.sub(r"^(?:\d+[.)]\s*|[-*]\s+)", "", line).strip()
            if cleaned and len(cleaned) > 2:
                steps.append(cleaned)
        return steps

    # ────────────────────────────────────────────────────────────────────
    # WALKTHROUGH — presentación legible del plan
    # ────────────────────────────────────────────────────────────────────

    def get_walkthrough(self, goal_id: str) -> str:
        """Genera un walkthrough markdown del estado actual del goal."""
        goal = self.task_store.get_goal(goal_id)
        if not goal:
            return f"⚠️ Goal no encontrado: {goal_id}"

        subtasks = self.task_store.get_subtasks(goal_id)
        progress = self.task_store.goal_progress(goal_id)

        lines: List[str] = []
        lines.append(f"# 🎯 Objetivo: {goal['text']}")
        lines.append("")
        lines.append(
            f"**Estado:** {_status_emoji(goal['status'])} `{goal['status']}`  "
            f"| **Prioridad:** {goal['priority']}/10  "
            f"| **Progreso:** {progress['done']}/{progress['total']} "
            f"({progress['percent']}%)"
        )
        if goal.get("deadline"):
            lines.append(f"| **Deadline:** {goal['deadline']}")
        lines.append("")

        # Barra de progreso visual
        if progress["total"] > 0:
            bar_len = 20
            filled = int((progress["percent"] / 100) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(f"```\n{bar} {progress['percent']}%\n```")
            lines.append("")

        if not subtasks:
            lines.append("_Sin plan generado aún._")
        else:
            lines.append("## 📋 Plan de ejecución")
            lines.append("")
            for sub in subtasks:
                icon = _subtask_icon(sub["status"])
                lines.append(f"{icon} **Paso {sub['order_idx'] + 1}.** {sub['text']}")
                if sub["status"] == "done" and sub.get("result"):
                    result_preview = sub["result"][:200]
                    lines.append(f"   ↳ ✅ _{result_preview}_")
                elif sub["status"] == "failed" and sub.get("error"):
                    lines.append(f"   ↳ ❌ _{sub['error'][:200]}_")
            lines.append("")

        return "\n".join(lines)

    def _build_plan_summary(self, goal: dict, subtasks: List[dict]) -> str:
        """Resume el plan en una sola línea para almacenamiento."""
        if not subtasks:
            return goal["text"]
        steps_text = " → ".join(
            f"{s['text'][:60]}" for s in subtasks[:4]
        )
        suffix = f" (+{len(subtasks) - 4} más)" if len(subtasks) > 4 else ""
        return f"{steps_text}{suffix}"

    # ────────────────────────────────────────────────────────────────────
    # EXECUTE — ejecución de subtareas vía pipeline
    # ────────────────────────────────────────────────────────────────────

    async def execute_next_subtask(self, goal_id: str) -> Optional[dict]:
        """Ejecuta el siguiente paso pendiente de un goal."""
        async with self._lock:
            goal = self.task_store.get_goal(goal_id)
            if not goal:
                return None
            if goal["status"] not in {"planned", "executing"}:
                return None

            subtask = self.task_store.get_next_pending_subtask(goal_id)
            if not subtask:
                # No hay más subtareas → verificar completitud.
                await self._check_completion(goal_id)
                return None

            # Marcar como en ejecución.
            self.task_store.update_subtask(subtask["id"], status="executing")
            self.task_store.update_goal_status(goal_id, "executing")

            event_bus.emit("goal.subtask_started", {
                "goal_id": goal_id,
                "subtask_id": subtask["id"],
                "step": subtask["order_idx"] + 1,
                "text": subtask["text"],
            })
            logger.info(
                "Ejecutando subtarea %d/%d de goal %s: %s",
                subtask["order_idx"] + 1,
                len(self.task_store.get_subtasks(goal_id)),
                goal_id,
                subtask["text"][:60],
            )

            result = await self._run_subtask_with_retry(subtask)

            # Protocolo Stop, Check & Think: Re-evaluar si hay pasos pendientes
            remaining = [s for s in self.task_store.get_subtasks(goal_id) if s["status"] == "pending"]
            if remaining:
                await self._stop_check_and_think(goal, subtask, result, remaining)

            # Tras ejecutar y re-evaluar, verificar si el goal quedó completo.
            remaining_after = self.task_store.get_next_pending_subtask(goal_id)
            if not remaining_after:
                await self._check_completion(goal_id)

            return result

    async def _run_subtask_with_retry(self, subtask: dict) -> dict:
        """Ejecuta una subtarea con reintentos ante fallo transitorio."""
        last_error = ""
        for attempt in range(1, MAX_RETRIES_PER_SUBTASK + 2):
            try:
                result = await asyncio.wait_for(
                    self.pipeline.process(subtask["text"]),
                    timeout=SUBTASK_TIMEOUT_S,
                )

                if result.get("success", False):
                    # Éxito: extraer respuesta para almacenar.
                    response_text = result.get("response", "")
                    if not response_text and result.get("results"):
                        # Si no hay texto, usar el output del primer call.
                        first_result = result["results"][0]
                        response_text = str(
                            first_result.get("output", "")
                        )[:500]

                    self.task_store.update_subtask(
                        subtask["id"],
                        status="done",
                        result=response_text or "Completado",
                    )
                    event_bus.emit("goal.subtask_done", {
                        "goal_id": subtask["goal_id"],
                        "subtask_id": subtask["id"],
                        "step": subtask["order_idx"] + 1,
                        "result_preview": response_text[:200],
                    })
                    logger.info(
                        "Subtarea %s completada (intento %d)",
                        subtask["id"], attempt,
                    )
                    return {
                        "subtask_id": subtask["id"],
                        "status": "done",
                        "result": response_text,
                    }
                else:
                    last_error = "Pipeline devolvió success=False"
                    if attempt <= MAX_RETRIES_PER_SUBTASK:
                        logger.warning(
                            "Subtarea %s falló (intento %d/%d), reintentando...",
                            subtask["id"], attempt, MAX_RETRIES_PER_SUBTASK + 1,
                        )

            except asyncio.TimeoutError:
                last_error = f"Timeout tras {SUBTASK_TIMEOUT_S}s"
                logger.warning(
                    "Subtarea %s timeout (intento %d)", subtask["id"], attempt
                )
            except Exception as exc:
                last_error = str(exc)[:500]
                logger.warning(
                    "Subtarea %s error (intento %d): %s",
                    subtask["id"], attempt, exc,
                )

        # Todos los intentos fallaron.
        self.task_store.update_subtask(
            subtask["id"], status="failed", error=last_error
        )
        event_bus.emit("goal.subtask_failed", {
            "goal_id": subtask["goal_id"],
            "subtask_id": subtask["id"],
            "step": subtask["order_idx"] + 1,
            "error": last_error[:200],
        })
        return {
            "subtask_id": subtask["id"],
            "status": "failed",
            "error": last_error,
        }

    # ────────────────────────────────────────────────────────────────────
    # STOP, CHECK & THINK — Re-evaluación dinámica del plan por paso
    # ────────────────────────────────────────────────────────────────────

    async def _stop_check_and_think(
        self,
        goal: dict,
        completed_subtask: dict,
        subtask_result: dict,
        remaining_subtasks: List[dict],
    ) -> None:
        """Protocolo 'Stop, Check & Think': evalúa si el resultado del paso actual
        invalida o modifica los pasos restantes del plan."""
        goal_text = goal.get("text", "")
        step_text = completed_subtask.get("text", "")
        status = subtask_result.get("status", "")
        outcome = subtask_result.get("result") or subtask_result.get("error") or ""
        remaining_texts = [s["text"] for s in remaining_subtasks]

        user_content = (
            f"Overall Goal: {goal_text}\n"
            f"Step Just Executed: {step_text}\n"
            f"Execution Status: {status}\n"
            f"Outcome/Output: {outcome[:500]}\n"
            f"Remaining Planned Steps: {json.dumps(remaining_texts, ensure_ascii=False)}"
        )

        messages = [
            {"role": "system", "content": _REEVALUATOR_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await asyncio.wait_for(
                self._collect_llm_response(messages, temperature=0.2),
                timeout=PLAN_TIMEOUT_S,
            )
            data = self._parse_json_object(raw)
            if data and not data.get("valid", True):
                new_steps = data.get("new_steps") or []
                reason = data.get("reason", "Plan adjusted after step evaluation.")
                logger.info(
                    "Stop, Check & Think: Re-evaluación cambió el plan del goal %s (Razón: %s)",
                    goal["id"], reason
                )

                # Cancelar/omitir subtareas pendientes obsoletas
                for sub in remaining_subtasks:
                    self.task_store.update_subtask(sub["id"], status="skipped")

                # Agregar las nuevas subtareas si las hay
                if new_steps:
                    clean_new_steps = [str(s)[:300] for s in new_steps[:MAX_PLAN_STEPS]]
                    added = self.task_store.add_subtasks_batch(goal["id"], clean_new_steps)
                    logger.info("Nuevos pasos agregados al goal %s: %d", goal["id"], len(added))

                # Emitir evento de plan ajustado
                event_bus.emit("goal.plan_adjusted", {
                    "goal_id": goal["id"],
                    "reason": reason,
                    "new_steps": new_steps,
                })

                # Actualizar resumen del goal
                updated_subtasks = self.task_store.get_subtasks(goal["id"])
                summary = self._build_plan_summary(goal, updated_subtasks)
                self.task_store.update_goal_summary(goal["id"], summary)

        except Exception as exc:
            logger.warning("Stop, Check & Think re-evaluation warning for goal %s: %s", goal["id"], exc)

    @staticmethod
    def _parse_json_object(raw: str) -> Optional[dict]:
        """Extrae un diccionario JSON de la respuesta LLM."""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        return None

    # ────────────────────────────────────────────────────────────────────
    # VERIFY — comprobación de completitud
    # ────────────────────────────────────────────────────────────────────

    async def _check_completion(self, goal_id: str) -> None:
        """Verifica si un goal está completo y actualiza su estado."""
        progress = self.task_store.goal_progress(goal_id)
        goal = self.task_store.get_goal(goal_id)
        if not goal:
            return

        if progress["total"] == 0:
            return

        if progress["pending"] > 0:
            return  # Aún hay pasos por ejecutar.

        # No hay pendientes: ¿todas done o algunas failed?
        if progress["failed"] > 0 and progress["done"] == 0:
            self.task_store.update_goal_status(goal_id, "failed")
            event_bus.emit("goal.failed", {
                "goal_id": goal_id,
                "failed": progress["failed"],
            })
            logger.warning(
                "Goal %s FALLÓ: %d subtareas fallidas",
                goal_id, progress["failed"],
            )
        else:
            self.task_store.update_goal_status(goal_id, "done")
            event_bus.emit("goal.completed", {
                "goal_id": goal_id,
                "done": progress["done"],
                "failed": progress["failed"],
                "percent": progress["percent"],
            })
            logger.info(
                "Goal %s COMPLETADO: %d/%d (%.0f%%)",
                goal_id, progress["done"], progress["total"],
                progress["percent"],
            )

    # ────────────────────────────────────────────────────────────────────
    # LOOP — evaluación periódica de goals activos
    # ────────────────────────────────────────────────────────────────────

    async def evaluate(self) -> dict:
        """Evalúa todos los goals activos en una pasada.
        Retorna un resumen de acciones tomadas."""
        stats = {"planned": 0, "executed": 0, "completed": 0, "failed": 0}

        # 1. Planificar goals en estado 'planning'.
        planning_goals = self.task_store.list_goals(statuses=["planning"])
        for goal in planning_goals[:MAX_GOALS_CONCURRENT]:
            try:
                await self.plan_goal(goal["id"])
                stats["planned"] += 1
            except Exception as exc:
                logger.warning("Error planificando %s: %s", goal["id"], exc)

        # 2. Ejecutar siguiente paso de goals planificados/ejecutando.
        active_goals = self.task_store.list_goals(
            statuses=["planned", "executing"]
        )
        for goal in active_goals[:MAX_GOALS_CONCURRENT]:
            result = await self.execute_next_subtask(goal["id"])
            if result:
                if result["status"] == "done":
                    stats["executed"] += 1
                else:
                    stats["failed"] += 1
            # Verificar si este goal quedó completo tras la ejecución.
            updated = self.task_store.get_goal(goal["id"])
            if updated and updated["status"] == "done":
                stats["completed"] += 1
            elif updated and updated["status"] == "failed":
                stats["failed"] += 1

        return stats

    async def _evaluation_loop(self) -> None:
        """Loop de fondo: evalúa goals cada eval_interval segundos."""
        logger.info(
            "GoalManager loop iniciado (interval=%.0fs)", self.eval_interval
        )
        while not self._stop_event.is_set():
            try:
                await self.evaluate()
            except Exception as exc:
                logger.warning("Error en evaluation loop: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.eval_interval
                )
            except asyncio.TimeoutError:
                pass  # Timeout normal = siguiente iteración.

        logger.info("GoalManager loop detenido.")

    def start(self) -> None:
        """Inicia el loop de evaluación asíncrono de fondo."""
        if self._running:
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(
            self._evaluation_loop(), name="WIS-GoalManager-Loop"
        )
        self._running = True
        logger.info("GoalManager arrancado.")

    async def stop(self) -> None:
        """Detiene el loop de evaluación limpiamente."""
        self._stop_event.set()
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._running = False
        logger.info("GoalManager detenido.")

    # ────────────────────────────────────────────────────────────────────
    # Cancelación y limpieza
    # ────────────────────────────────────────────────────────────────────

    def cancel_goal(self, goal_id: str) -> bool:
        """Cancela un goal y marca sus subtareas pendientes como skipped."""
        ok = self.task_store.update_goal_status(goal_id, "cancelled")
        if ok:
            for sub in self.task_store.get_subtasks(goal_id):
                if sub["status"] in {"pending", "executing"}:
                    self.task_store.update_subtask(sub["id"], status="skipped")
            event_bus.emit("goal.cancelled", {"goal_id": goal_id})
            logger.info("Goal %s cancelado.", goal_id)
        return ok


# ──────────────────────────────────────────────────────────────────────────
# Helpers visuales
# ──────────────────────────────────────────────────────────────────────────

_GOAL_EMOJI = {
    "planning": "🧠",
    "planned": "📋",
    "executing": "⚙️",
    "done": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}

_SUBTASK_ICON = {
    "pending": "⬜",
    "executing": "🔄",
    "done": "✅",
    "failed": "❌",
    "skipped": "⏭️",
}


def _status_emoji(status: str) -> str:
    return _GOAL_EMOJI.get(status, "❓")


def _subtask_icon(status: str) -> str:
    return _SUBTASK_ICON.get(status, "⬜")
