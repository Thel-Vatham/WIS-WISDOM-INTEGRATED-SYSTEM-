"""WIS Action Pipeline - Multi-step agentic loop with 3 cognitive paths.

Pipeline (Praxis) orquesta la respuesta ante un input de usuario:
  1. REFLEJO  (_try_reflexive): respuestas instantaneas (saludos, identity).
  2. CONOCIDO (_try_known):     skill cacheada en SkillMemory.
  3. NUEVO    (_try_new):       loop agentico multi-paso con LLM Reasoning.
     - Ejecuta tool calls iterativamente (hasta max_steps).
     - Verificacion empirica al final de cada paso.
     - Auto-reparacion ante fallos.
     - Pausa de aprobacion en modo secure para acciones riesgosas.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Union

from core.safety import SafetyPolicy, FailureClassifier
from core.reasoning import ReasoningEngine
from core.event_bus import event_bus
from core.skill_memory import SkillMemory
from core.goal_manager import GoalManager
from core.event_bus import event_bus

logger = logging.getLogger("wis.core.pipeline")

PATH_REFLEXIVE = "reflexive"
PATH_KNOWN = "known"
PATH_NEW = "new"

DEFAULT_MAX_STEPS = 10
STEP_TIMEOUT_S = 60

AbilityFn = Callable[[Dict[str, Any]], Any]


class ActionPipeline:
    """Pipeline de accion con 3 caminos cognitivos y loop agentico multi-paso."""

    def __init__(
        self,
        reasoning: ReasoningEngine,
        skill_memory: SkillMemory,
        abilities: Optional[Union[Dict[str, AbilityFn], Any]] = None,
        safety: Optional[SafetyPolicy] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.reasoning: ReasoningEngine = reasoning
        self.skill_memory: SkillMemory = skill_memory
        if isinstance(abilities, dict):
            self.abilities: Any = dict(abilities)
        elif abilities is not None:
            self.abilities = abilities
        else:
            self.abilities = {}
        self.safety: SafetyPolicy = safety or SafetyPolicy()
        self.max_steps: int = max(1, int(max_steps or DEFAULT_MAX_STEPS))

        # Goal Manager integration (set externally)
        self.goal_manager: Optional[GoalManager] = None

        # Approval mechanism for secure mode
        self._approval_event: Optional[asyncio.Event] = None
        self._approval_result: bool = False
        self._cancel_event: Optional[asyncio.Event] = None

        self._reflex_table: Dict[str, str] = {
            "hi": "Hello.",
            "hello": "Hello.",
            "hey": "Hello.",
            "hola": "Hello.",
            "buenos dias": "Good morning.",
            "buenas tardes": "Good afternoon.",
            "buenas noches": "Good evening.",
            "thanks": "You're welcome.",
            "gracias": "You're welcome.",
            "bye": "Goodbye.",
            "adios": "Goodbye.",
            "chao": "Goodbye.",
        }

    def register_ability(self, name: str, fn: AbilityFn) -> None:
        if isinstance(self.abilities, dict):
            self.abilities[name] = fn

    def register_reflex(self, trigger: str, response: str) -> None:
        key = (trigger or "").strip().lower()
        if key:
            self._reflex_table[key] = str(response or "")

    # ---- Approval API (called from server.py) -----------------------------

    def approve_action(self) -> None:
        """Aprueba la accion riesgosa en espera."""
        self._approval_result = True
        if self._approval_event:
            self._approval_event.set()

    def deny_action(self) -> None:
        """Deniega la accion riesgosa."""
        self._approval_result = False
        if self._approval_event:
            self._approval_event.set()

    def cancel_processing(self) -> None:
        """Cancela el loop agentico actual."""
        if self._cancel_event:
            self._cancel_event.set()

    # ---- Main Process -----------------------------------------------------

    async def process(
        self,
        text: str,
        sensor_data: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {
                "response": "",
                "calls": [],
                "results": [],
                "path_used": PATH_REFLEXIVE,
                "success": True,
            }

        if tools is None and self.abilities:
            if hasattr(self.abilities, "get_schemas"):
                tools = self.abilities.get_schemas()

        event_bus.emit("pipeline.input_received", {"text": text})
        self._cancel_event = asyncio.Event()

        # --- Path 1: Reflexive ---
        result = self._try_reflexive(text)
        if result is not None:
            event_bus.emit("pipeline.reflexive_hit", {"text": text[:80]})
            event_bus.emit("pipeline.path_start", {"path": PATH_REFLEXIVE, "text": text[:80]})
            calls = result.get("calls", [])
            if calls:
                results, ok = await self._execute_calls(calls)
                result["results"] = results
                result["success"] = ok
            return self._finalize(result, PATH_REFLEXIVE, success=result.get("success", True))

        # --- Path 2: Known (SkillMemory cache) ---
        result = self._try_known(text)
        if result is not None:
            event_bus.emit("pipeline.known_hit", {"text": text[:80]})
            event_bus.emit("pipeline.path_start", {"path": PATH_KNOWN, "text": text[:80]})
            calls = result.get("calls", [])
            results, ok = await self._execute_calls(calls)
            response = result.get("response", "")
            if ok:
                self.skill_memory.record_success(text, calls, response)
            else:
                self.skill_memory.record_failure(text, calls)
                event_bus.emit("pipeline.known_failed_fallback", {"text": text[:80]})
                new_result = await self._try_new(text, sensor_data, tools)
                return self._finalize(new_result, PATH_NEW, success=new_result["success"])
            final = {
                "response": response,
                "calls": calls,
                "results": results,
                "success": ok,
            }
            return self._finalize(final, PATH_KNOWN, success=ok)

        # --- Path 3: New (Multi-Step Agentic Loop) ---
        event_bus.emit("pipeline.new_path", {"text": text[:80]})
        event_bus.emit("pipeline.path_start", {"path": PATH_NEW, "text": text[:80]})
        result = await self._try_new(text, sensor_data, tools)
        return self._finalize(result, PATH_NEW, success=result["success"])

    # ---- Reflexive & Known -------------------------------------------------

    def _try_reflexive(self, text: str) -> Optional[Dict[str, Any]]:
        normalized = text.strip().lower()
        if not normalized:
            return None

        normalized = re.sub(r"[?!.,]+$", "", normalized).strip()

        if normalized in self._reflex_table:
            return {
                "response": self._reflex_table[normalized],
                "calls": [],
                "results": [],
            }

        if normalized in ("who are you", "quien eres", "quien sos", "tu nombre"):
            name = self.reasoning.identity.get_name()
            desc = self.reasoning.identity.get_description()
            return {
                "response": f"I am {name}. {desc}",
                "calls": [],
                "results": [],
            }

        # Fast Intent Classifier
        m_open = re.match(r"^(?:abre|inicia|ejecuta|open|launch|start)\s+(?:el|la|program|app)?\s*(.+)$", normalized)
        if m_open:
            app_name = m_open.group(1).strip()
            # Only open reflexively if it is a known system application name or a real file path
            import os
            known_apps = {
                "chrome", "brave", "opera", "edge", "notepad", "calculator", "calc",
                "word", "excel", "powerpoint", "winword", "powerpnt", "code", "vs code",
                "spotify", "cmd", "powershell", "terminal", "paint", "mspaint"
            }
            # Also check if it's a real file path
            is_known = app_name.lower() in known_apps or os.path.exists(app_name)
            if is_known:
                return {
                    "response": f"Opening {app_name}...",
                    "calls": [{"action": "open_application", "app_name": app_name}],
                    "results": [],
                }

        if any(h in normalized for h in ("que hora es", "dime la hora", "dime hora actual")):
            return {
                "response": "Checking current time...",
                "calls": [{"action": "get_current_time"}],
                "results": [],
            }

        if any(h in normalized for h in ("estado del sistema", "system info", "informacion del sistema", "como esta el sistema")):
            return {
                "response": "Checking system metrics...",
                "calls": [{"action": "get_system_info"}],
                "results": [],
            }

        return None

    def _try_known(self, text: str) -> Optional[Dict[str, Any]]:
        skill = self.skill_memory.lookup(text)
        if not skill:
            return None
        return {
            "response": skill.get("response", ""),
            "calls": skill.get("calls", []),
            "results": [],
        }

    # ---- Multi-Step Agentic Loop -------------------------------------------

    async def _try_new(
        self,
        text: str,
        sensor_data: Optional[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Loop agentico multi-paso: ejecuta, verifica, itera hasta completar."""
        all_calls = []
        all_results = []
        text_response = ""
        final_ok = True
        accumulated_context = ""
        step_count = 0

        for step in range(1, self.max_steps + 1):
            step_count = step

            # Check cancellation
            if self._cancel_event and self._cancel_event.is_set():
                event_bus.emit("pipeline.loop_cancelled", {"step": step, "text": text[:80]})
                break

            event_bus.emit("pipeline.loop_step", {
                "step": step, "max_steps": self.max_steps,
                "text": text[:80],
            })

            # Build prompt with accumulated context
            if step == 1:
                prompt = text
            else:
                prompt = (
                    f"{text}\n\n"
                    f"[MULTI-STEP CONTEXT — Step {step}/{self.max_steps}]\n"
                    f"Previous actions and results:\n{accumulated_context}\n\n"
                    f"Continue working towards the user's goal. "
                    f"If the task is COMPLETE, respond with ONLY your final answer (no tool calls). "
                    f"If more actions are needed, emit tool calls."
                )

            thought = await self.reasoning.think(prompt, sensor_data=sensor_data, tools=tools)
            calls = thought.get("calls", []) or []
            text_response = thought.get("text", "") or ""

            # If no tool calls, the LLM considers the task done
            if not calls:
                final_ok = True
                break

            # Execute calls (with approval check in secure mode)
            results, ok = await self._execute_calls(calls)
            all_calls.extend(calls)
            all_results.extend(results)

            # Accumulate context for next step
            step_ctx = (
                f"Step {step}:\n"
                f"  Tool Calls: {json.dumps(calls, ensure_ascii=False)}\n"
                f"  Results: {json.dumps(results, ensure_ascii=False, default=str)}\n"
            )
            accumulated_context += step_ctx

            if not ok:
                # Auto-repair attempt
                failed_msgs = [
                    r.get("reason") or (r.get("output", {}) or {}).get("message")
                    or (r.get("output", {}) or {}).get("error")
                    for r in results if not r.get("ok", False)
                ]
                error_desc = " | ".join(str(m) for m in failed_msgs if m)
                category = FailureClassifier.classify(error_desc)
                event_bus.emit("pipeline.failure_classified", {
                    "category": category, "error": error_desc, "text": text[:80]
                })

                repair_prompt = (
                    f"{text}\n\n"
                    f"[AUTO-REPAIR — Step {step}]: Previous action failed ({category}): {error_desc}.\n"
                    f"Please consult the ## AVAILABLE TOOLS AND SCHEMAS section in the system prompt to find the correct, canonical tool and action name, along with their exact parameter schemas.\n"
                    f"Generate alternative tool calls or parameters."
                )
                try:
                    repair_thought = await self.reasoning.think(repair_prompt, sensor_data=sensor_data, tools=tools)
                    repair_calls = repair_thought.get("calls", []) or []
                    if repair_calls:
                        repair_results, repair_ok = await self._execute_calls(repair_calls)
                        if repair_ok:
                            text_response = repair_thought.get("text", "") or text_response
                            all_calls.extend(repair_calls)
                            all_results.extend(repair_results)
                            ok = True
                            accumulated_context += (
                                f"  Auto-Repair: {json.dumps(repair_calls, ensure_ascii=False)}\n"
                                f"  Repair Results: {json.dumps(repair_results, ensure_ascii=False, default=str)}\n"
                            )
                except Exception as exc:
                    logger.warning(f"pipeline: auto-repair failed: {exc}")

            final_ok = ok

            # If this is NOT the last step and we had tool calls, do post-exec synthesis
            if step == self.max_steps or not calls:
                # Final synthesis
                try:
                    synth_prompt = (
                        f"{text}\n\n"
                        f"[POST-EXECUTION SYNTHESIS]\n"
                        f"All executed actions:\n{accumulated_context}\n\n"
                        f"Provide a clear, conclusive synthesis based on all results."
                    )
                    synth = await self.reasoning.think(synth_prompt, sensor_data=sensor_data)
                    if synth.get("text"):
                        text_response = synth["text"].strip()
                except Exception as exc:
                    logger.warning(f"pipeline: synthesis skipped: {exc}")

        event_bus.emit("pipeline.loop_done", {
            "steps": step_count, "max_steps": self.max_steps,
            "success": final_ok, "text": text[:80],
        })

        try:
            self.reasoning.memory.remember(text, text_response)
        except Exception:
            logger.warning("pipeline: could not remember interaction.")

        if final_ok:
            self.skill_memory.record_success(text, all_calls, text_response)
        else:
            self.skill_memory.record_failure(text, all_calls)

        return {
            "response": text_response,
            "calls": all_calls,
            "results": all_results,
            "success": final_ok,
            "steps_used": step_count,
            "raw_response": "",
            "model_used": "",
        }

    # ---- Execute Calls (with approval) ------------------------------------

    async def _execute_calls(self, calls: List[Dict[str, Any]]) -> tuple:
        if not calls:
            return [], True

        results: List[Dict[str, Any]] = []
        all_ok = True

        for call in calls:
            if not isinstance(call, dict):
                results.append({"error": "invalid_call", "call": call})
                all_ok = False
                continue

            # Safety check — hard blocks
            allowed, reason = self.safety.check(call)
            if not allowed:
                call_name = call.get("skill") or call.get("name") or "unknown"
                event_bus.emit("pipeline.call_blocked", {"name": call_name, "reason": reason})
                results.append({"blocked": True, "reason": reason, "name": call_name})
                all_ok = False
                continue

            # Approval check — soft pause in secure mode
            if self.safety.needs_approval(call):
                call_name = call.get("skill") or call.get("name") or call.get("action") or call.get("tool") or "unknown"
                event_bus.emit("pipeline.approval_required", {
                    "call": call,
                    "name": call_name,
                    "action": call.get("action") or call.get("tool") or "",
                    "params": call.get("params") or call.get("arguments") or {},
                })

                # Wait for approval from UI
                self._approval_event = asyncio.Event()
                self._approval_result = False
                try:
                    await asyncio.wait_for(self._approval_event.wait(), timeout=120)
                except asyncio.TimeoutError:
                    self._approval_result = False

                if not self._approval_result:
                    event_bus.emit("pipeline.call_denied", {"name": call_name})
                    results.append({
                        "ok": False, "name": call_name,
                        "action": call.get("action") or call.get("tool") or "",
                        "output": {"error": "denied_by_user"},
                    })
                    all_ok = False
                    continue

                event_bus.emit("pipeline.call_approved", {"name": call_name})

            # Resolve action
            skill_name = str(call.get("skill") or call.get("name") or call.get("tool") or "").strip()
            raw_action = str(call.get("action") or "").strip()
            raw_type = str(call.get("type") or "").strip()

            if isinstance(call.get("params"), dict):
                params = dict(call["params"])
            elif isinstance(call.get("arguments"), dict):
                params = dict(call["arguments"])
            else:
                params = {k: v for k, v in call.items()
                          if k not in ("skill", "name", "action", "type", "domain", "tool", "params", "arguments")}

            if "command" in call and "command" not in params:
                params["command"] = call["command"]
            if "app_name" in call and "app_name" not in params:
                params["app_name"] = call["app_name"]

            action = raw_action or skill_name
            if not action or action in ("exec", "shell", "cmd", "powershell", "bash", "run"):
                if raw_type in ("exec", "shell", "cmd", "powershell", "bash", "run") or "command" in params:
                    action = "execute_shell"
                elif "app_name" in params or "application" in params:
                    action = "open_application"
                elif "query" in params:
                    action = "web_search"
                elif "level" in params or "volume" in params:
                    action = "set_volume"

            if not skill_name and self.abilities:
                if hasattr(self.abilities, "all"):
                    for ab_name, ab in self.abilities.all().items():
                        supported_actions = [a.get("action", "").lower() for a in ab.get_schema() if isinstance(a, dict)]
                        if action.lower() in supported_actions or action.lower() == ab_name.lower() or action.lower() == ab.domain.lower():
                            skill_name = ab_name
                            break

            event_bus.emit("pipeline.call_start", {
                "skill": skill_name,
                "action": action,
                "params": params,
            })

            output = None
            success = False

            if hasattr(self.abilities, "execute") and callable(getattr(self.abilities, "execute")):
                try:
                    res = await self.abilities.execute(skill_name, action, params)
                    output = res
                    success = res.get("success", False) if isinstance(res, dict) else True
                except Exception as exc:
                    logger.warning(f"pipeline: ability execute failed: {exc}")
                    output = {"error": str(exc)}
                    success = False
            elif isinstance(self.abilities, dict):
                fn = self.abilities.get(skill_name) or self.abilities.get(action)
                if fn is None:
                    output = {"error": "unknown_ability", "name": skill_name or action}
                    success = False
                else:
                    try:
                        if inspect.iscoroutinefunction(fn):
                            output = await fn(call)
                        else:
                            output = fn(call)
                        success = True
                    except Exception as exc:
                        output = {"error": str(exc)}
                        success = False
            else:
                output = {"error": "no_abilities_registered"}
                success = False

            event_bus.emit("pipeline.call_result", {
                "skill": skill_name,
                "action": action,
                "success": success,
                "output": output,
            })

            results.append({
                "ok": success,
                "name": skill_name or action,
                "action": action,
                "output": output,
            })
            if not success:
                all_ok = False

        return results, all_ok

    @staticmethod
    def _finalize(result: Dict[str, Any], path: str, success: bool) -> Dict[str, Any]:
        final_dict = {
            "response": result.get("response", ""),
            "calls": result.get("calls", []),
            "results": result.get("results", []),
            "path_used": path,
            "success": bool(result.get("success", success)),
            "steps_used": result.get("steps_used", 1),
        }
        event_bus.emit("pipeline.response_ready", final_dict)
        return final_dict
