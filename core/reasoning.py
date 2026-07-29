"""WIS Reasoning Engine - Context compiler and LLM reasoning.

Compila contexto relevante ("minimal sufficient context") y razona con el LLM.
Cortex / Reasoning Engine toma el input del usuario, reune SOLO el contexto
necesario, construye el system prompt e interpreta la respuesta del LLM.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.memory import Memory, _cosine_similarity
from core.llm_client import LLMClient, ModelRouter
from core.identity import Identity
from core.event_bus import event_bus

logger = logging.getLogger("wis.core.reasoning")

TOP_MEMORIES_K = 3
MIN_RELEVANCE = 0.35
MAX_FACTS = 5


class ReasoningEngine:
    """Compila contexto y razona con el LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        identity: Identity,
        memory: Memory,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self.llm_client: LLMClient = llm_client
        self.identity: Identity = identity
        self.memory: Memory = memory
        self.router: ModelRouter = router or ModelRouter()
        # Mapa: nombre_funcion_openai → (skill, action) para revertir tool_calls.
        self._tool_name_map: Dict[str, tuple] = {}

    async def think(
        self,
        user_input: str,
        sensor_data: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        context: str = self._compile_context(user_input)
        system_prompt: str = self._build_system_prompt(context, tools=tools)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.memory.get_history())
        user_content = self._format_user_message(user_input, sensor_data)
        messages.append({"role": "user", "content": user_content})

        model = self.router.route(user_input, has_tools=bool(tools))

        # ── Cognitive Trace: emit what we're sending to the LLM ──────────────
        event_bus.emit("reasoning.prompt", {
            "system_prompt": system_prompt,
            "user_message": user_content,
            "model": model,
            "history_turns": len(self.memory.get_history()),
        })

        # Convertimos el esquema interno de habilidades a formato OpenAI
        # function-calling para que el LLM devuelva tool_calls estructurados
        # (en lugar de texto libre a parsear con regex). Los esquemas tambien
        # se inyectan en el system prompt como referencia para el LLM y como
        # mecanismo de fallback si el proveedor no soporta tools nativos.
        openai_tools: List[Dict[str, Any]] = self._tools_to_openai(tools) if tools else []

        raw_chunks: List[str] = []
        async for chunk in self.llm_client.stream(
            messages, tools=openai_tools or None, model=model
        ):
            raw_chunks.append(chunk)
            # Stream each token chunk to the trace
            event_bus.emit("reasoning.token_chunk", {"chunk": chunk})

        raw_response: str = "".join(raw_chunks)
        text, calls = self._split_text_and_calls(raw_response)

        # Si no se obtuvieron calls estructurados, intentamos parsers de texto
        # (fallback para proveedores sin tool-calling nativo o respuestas malformadas).
        if not calls:
            parsed_calls = self._parse_tool_calls(raw_response)
            if parsed_calls:
                calls = parsed_calls

        # Normaliza los tool_calls nativos: el nombre llega como "skill::action"
        # y lo separamos de vuelta en campos skill/action para el pipeline.
        calls = self._normalize_calls_namespace(calls)

        # ── Cognitive Trace: emit what the LLM returned ───────────────────────
        event_bus.emit("reasoning.response", {
            "raw_response": raw_response,
            "text": text.strip(),
            "calls": calls,
            "model": model,
        })

        return {
            "text": text.strip(),
            "calls": calls,
            "raw_response": raw_response,
            "model_used": model,
        }

    # ------------------------------------------------------------------ #
    # Tool calls nativos (formato OpenAI function-calling).
    # Los nombres de funciones se generan concatenando skill+action con '_'
    # y se mantiene un mapa (self._tool_name_map) para revertir al recibir
    # el tool_call del LLM. Esto evita usar caracteres especiales (como ':')
    # que la API de OpenAI/DeepSeek rechaza en el nombre de la funcion.
    # ------------------------------------------------------------------ #

    def _normalize_native_tool_name(self, name: str) -> tuple:
        """Descompone un nombre de tool nativo de vuelta a (skill, action).

        Usa el mapa construido por _tools_to_openai(). Si no encuentra el
        nombre en el mapa, intenta un split por '_' como fallback."""
        raw = str(name or "").strip()
        if not raw:
            return "", ""
        if raw in self._tool_name_map:
            return self._tool_name_map[raw]
        if "_" in raw:
            parts = raw.split("_", 1)
            return parts[0].strip(), parts[1].strip()
        return "", raw

    def _normalize_calls_namespace(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normaliza una lista de tool_calls: separa nombres 'skill_action'.

        El llm_client devuelve cada tool_call con el nombre compuesto en
        'name'. Aqui reconstruimos skill/action correctos usando el mapa
        construido por _tools_to_openai() y aseguramos que existan
        'params'/'arguments' consistentes.
        """
        if not calls:
            return calls
        normalized: List[Dict[str, Any]] = []
        for c in calls:
            if not isinstance(c, dict):
                continue
            orig_skill = str(c.get("skill") or "").strip()
            orig_action = str(c.get("action") or "").strip()
            name = str(c.get("name") or (f"{orig_skill}_{orig_action}" if orig_skill and orig_action else orig_skill or orig_action)).strip()
            skill, action = self._normalize_native_tool_name(name)
            params = c.get("params")
            if params is None:
                params = c.get("arguments") or {}
            entry = dict(c)

            if orig_action:
                entry["action"] = orig_action
                if orig_skill:
                    entry["skill"] = orig_skill
                elif skill:
                    entry["skill"] = skill
            else:
                if skill:
                    entry["skill"] = skill
                if action:
                    entry["action"] = action

            entry["name"] = name
            entry["params"] = params if isinstance(params, dict) else {}
            if "arguments" not in entry:
                entry["arguments"] = entry["params"]
            normalized.append(entry)
        return normalized

    def _tools_to_openai(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convierte el esquema interno de habilidades a formato OpenAI tools.

        Construye y actualiza self._tool_name_map para poder revertir los
        nombres de funciones cuando el LLM devuelva tool_calls."""
        if not tools:
            return []

        # Reiniciar el mapa para esta invocacion.
        self._tool_name_map.clear()

        openai_tools: List[Dict[str, Any]] = []
        seen_names: set = set()

        for t in tools:
            if not isinstance(t, dict):
                continue
            skill_name = str(t.get("skill") or t.get("name") or "").strip()
            skill_desc = str(t.get("description") or "").strip()
            actions = t.get("actions") or []

            if not actions:
                if skill_name and skill_name not in seen_names:
                    seen_names.add(skill_name)
                    self._tool_name_map[skill_name] = (skill_name, "")
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": skill_name,
                            "description": skill_desc or skill_name,
                            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
                        },
                    })
                continue

            for act in actions:
                if not isinstance(act, dict):
                    continue
                action_name = str(act.get("action") or "").strip()
                if not action_name:
                    continue
                fn_name = f"{skill_name}_{action_name}"
                base_name = fn_name
                suffix = 1
                while fn_name in seen_names:
                    suffix += 1
                    fn_name = f"{base_name}_{suffix}"
                seen_names.add(fn_name)
                self._tool_name_map[fn_name] = (skill_name, action_name)
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "description": str(act.get("description") or skill_desc or action_name),
                        "parameters": self._params_to_json_schema(act.get("params") or {}),
                    },
                })

        return openai_tools

    @staticmethod
    def _params_to_json_schema(params: Dict[str, Any]) -> Dict[str, Any]:
        """Convierte el dict de parametros (nombre -> descripcion/tipo) a JSON schema."""
        if not params or not isinstance(params, dict):
            return {"type": "object", "properties": {}, "additionalProperties": True}

        properties: Dict[str, Any] = {}
        for pname, pmeta in params.items():
            pname = str(pname).strip()
            if not pname:
                continue
            if isinstance(pmeta, dict):
                prop = dict(pmeta)
                prop.setdefault("type", "string")
                properties[pname] = prop
            else:
                meta = str(pmeta or "").lower()
                ptype = "string"
                if "int" in meta or "number" in meta or "float" in meta:
                    ptype = "number"
                elif "bool" in meta:
                    ptype = "boolean"
                properties[pname] = {"type": ptype, "description": str(pmeta or "")}
        return {
            "type": "object",
            "properties": properties,
            "additionalProperties": True,
        }

    def _compile_context(self, user_input: str) -> str:
        sections: List[str] = []

        memories = self.memory.recall(user_input, top_k=TOP_MEMORIES_K * 2)
        if memories:
            ranked = self._rank_memories(memories, user_input, top_k=TOP_MEMORIES_K)
            if ranked:
                lines = []
                for m in ranked:
                    ui = (m.get("user_input") or "")[:120]
                    resp = (m.get("response") or "")[:120]
                    lines.append(f"- User: {ui} | Assistant: {resp}")
                sections.append("Relevant memories:\n" + "\n".join(lines))

        facts_subject_hint = self._guess_subject_from_input(user_input)
        facts = self.memory.get_facts(subject=facts_subject_hint)
        if not facts:
            facts = self.memory.get_facts()[:MAX_FACTS]
        if facts:
            sections.append("Known facts:\n" + "\n".join(f"- {f}" for f in facts[:MAX_FACTS]))

        return "\n\n".join(sections)

    def _rank_memories(
        self,
        memories: List[Dict[str, Any]],
        query: str,
        top_k: int = TOP_MEMORIES_K,
    ) -> List[Dict[str, Any]]:
        filtered = [m for m in memories if m.get("score", 0.0) >= MIN_RELEVANCE]
        return filtered[:top_k]

    @staticmethod
    def _guess_subject_from_input(text: str) -> Optional[str]:
        words = [w.lower() for w in re.findall(r"\b\w+\b", text or "")]
        for w in ("user", "usuario", "robot", "wis", "system", "sistema"):
            if w in words:
                return w
        return None

    def _load_system_file(self, filename: str) -> str:
        """Carga un archivo de sistema desde la carpeta Core/system si existe."""
        from pathlib import Path
        try:
            base_path = Path(__file__).resolve().parent.parent.parent / "Core" / "system"
            file_path = base_path / filename
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    def _build_system_prompt(
        self,
        context: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        parts: List[str] = []

        # 1. Constitution (Identidad + Seguridad)
        const_text = self._load_system_file("CONSTITUTION.md")
        if const_text:
            from string import Template
            const_text = const_text.replace("{{", "\x00LB\x00").replace("}}", "\x00RB\x00")
            const_template = Template(const_text)
            
            # Buscar nombre de usuario o por defecto Nicolas
            user_name = "Nicolas"
            
            const_text = const_template.safe_substitute(
                name=self.identity.get_name(),
                version="2.0.0",
                personality=self.identity.get_description(),
                user=user_name,
                lang="EN",
            ).replace("\x00LB\x00", "{").replace("\x00RB\x00", "}")
            parts.append(const_text)
            # English summary to prime the LLM's output language from the start.
            parts.append(
                "OUTPUT LANGUAGE: English. All user-facing text MUST be in English. "
                "This is non-negotiable."
            )
        else:
            parts.append(self.identity.get_system_prompt_section())

        # 2. Temporal Context
        import datetime
        now = datetime.datetime.now()
        current_time_str = now.strftime("%A, %B %d, %Y, %H:%M:%S")
        parts.append(
            f"TEMPORAL CONTEXT\n"
            f"- Current Date/Time: {current_time_str}\n"
            f"- Current Year: {now.year}"
        )

        # 3. Execution (Tool rules)
        exec_text = self._load_system_file("EXECUTION.md")
        if exec_text:
            parts.append(exec_text)
        else:
            parts.append(
                "OPERATIONAL RULES\n"
                "- Be direct, helpful, and concise.\n"
                "- ALWAYS respond in native, professional English. Do NOT reply in the user's input language (Spanish, Chinese, Korean, etc.) under any circumstances.\n"
                "- If actions or tool calls are required, emit them as valid JSON array.\n"
                "- Enforce safety limits."
            )

        # 4. Guided Mode
        guided_text = self._load_system_file("GUIDED_MODE.md")
        if guided_text:
            parts.append(guided_text)

        # 5. Examples
        examples_text = self._load_system_file("EXAMPLES.md")
        if examples_text:
            parts.append(examples_text)

        # 6. Reference
        ref_text = self._load_system_file("REFERENCE.md")
        if ref_text:
            parts.append(ref_text)

        # 6.5 Available Tools and Schemas
        if tools:
            tools_section = ["## AVAILABLE TOOLS AND SCHEMAS\n"]
            tools_section.append("You have access to the following tools. You must use them by emitting JSON tool calls matching these schemas.\n")
            for t in tools:
                skill_name = t.get("skill", "")
                domain = t.get("domain", "")
                desc = t.get("description", "")
                actions = t.get("actions", [])
                
                tools_section.append(f"### Skill: `{skill_name}` (Domain: `{domain}`)")
                tools_section.append(f"Description: {desc}")
                tools_section.append("Actions:")
                for act in actions:
                    act_name = act.get("action", "")
                    act_desc = act.get("description", "")
                    params = act.get("params", {})
                    tools_section.append(f"- **`{act_name}`**: {act_desc}")
                    if params:
                        params_json = json.dumps(params, indent=2, ensure_ascii=False)
                        tools_section.append(f"  Parameters (JSON schema/format):\n  ```json\n  {params_json}\n  ```")
                    else:
                        tools_section.append("  Parameters: None")
                tools_section.append("")  # newline
            parts.append("\n".join(tools_section))

        # 7. Dynamic Context (Facts and memories)
        if context.strip():
            parts.append(f"CONTEXT\n{context}")

        # 8. Strict Language Rule (Always respond in native English)
        parts.append(
            "STRICT LANGUAGE RULE\n"
            "Output language: Strictly English. ALWAYS communicate and respond in clear, natural, native English.\n"
            "Even if the user asks to translate, answer, or speak in Spanish or any other language, "
            "you MUST output your text strictly in English (e.g. explain or translate into English)."
        )

        return "\n\n".join(parts)

    @staticmethod
    def _format_user_message(text: str, sensor_data: Optional[Dict[str, Any]]) -> str:
        if not sensor_data:
            return text
        try:
            sensors_str = json.dumps(sensor_data, ensure_ascii=False)
            return f"{text}\n\n[Sensory Data: {sensors_str}]"
        except Exception:
            return text

    def _parse_dsml_calls(self, content: str) -> List[Dict[str, Any]]:
        """Parsea llamadas de herramientas formateadas con la sintaxis DSML de Avrora."""
        calls = []
        # Expresión regular para detectar bloques de invocación soportando barras completas unicode (｜) o normales (|)
        invoke_pattern = re.compile(
            r'<[｜|]{2}DSML[｜|]{2}invoke\s+name="([^"]+)"\s*>(.*?)</[｜|]{2}DSML[｜|]{2}invoke\s*>',
            re.DOTALL
        )
        param_pattern = re.compile(
            r'<[｜|]{2}DSML[｜|]{2}parameter\s+name="([^"]+)"[^>]*>(.*?)</[｜|]{2}DSML[｜|]{2}parameter\s*>',
            re.DOTALL
        )
        
        for name, params_block in invoke_pattern.findall(content):
            params = {}
            for p_name, p_val in param_pattern.findall(params_block):
                val = p_val.strip()
                if val.lower() == "true":
                    params[p_name] = True
                elif val.lower() == "false":
                    params[p_name] = False
                else:
                    try:
                        if val.isdigit():
                            params[p_name] = int(val)
                        else:
                            params[p_name] = float(val)
                    except ValueError:
                        params[p_name] = val
            calls.append({
                "skill": name,
                "action": params.get("action", ""),
                "params": params
            })
        return calls

    def _parse_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        if not content:
            return []

        # 0. Check for DSML tags first (Avrora execution style)
        if "<" in content and "DSML" in content:
            dsml_calls = self._parse_dsml_calls(content)
            if dsml_calls:
                return dsml_calls

        # 1. Try raw JSON parse first (covers bare arrays/objects)
        try:
            calls = self._extract_calls_from_json(content.strip())
            if calls:
                return calls
        except Exception:
            pass

        # 2. Extract from markdown code fences: ```json [...] ``` or ```json {...} ```
        md_match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", content, re.DOTALL)
        if md_match:
            calls = self._extract_calls_from_json(md_match.group(1))
            if calls is not None:
                return calls

        # 3. Look for {"tool_calls": [...]} or {"calls": [...]} pattern
        json_match = re.search(r'\{\s*"(?:tool_calls|calls)"\s*:\s*\[.*', content, re.DOTALL)
        if json_match:
            calls = self._extract_calls_from_json(json_match.group(0))
            if calls is not None:
                return calls

        # 4. Look for bare array starting with [{"action" or [{"type" or [{"command"
        arr_match = re.search(r'\[\s*\{\s*"(?:action|name|skill|type|command)".*', content, re.DOTALL)
        if arr_match:
            calls = self._extract_calls_from_json(arr_match.group(0))
            if calls is not None:
                return calls

        return []

    @staticmethod
    def _extract_calls_from_json(json_str: str) -> Optional[List[Dict[str, Any]]]:
        # Strip markdown fences if present
        stripped = json_str.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```\s*$", "", stripped)

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            # Try to recover truncated JSON
            try:
                end_brace = stripped.rfind("}")
                end_bracket = stripped.rfind("]")
                end_idx = max(end_brace, end_bracket)
                if end_idx > 0:
                    data = json.loads(stripped[:end_idx + 1])
                else:
                    return None
            except Exception:
                return None

        if isinstance(data, dict):
            calls = data.get("tool_calls") or data.get("calls")
            if isinstance(calls, list):
                return [c for c in calls if isinstance(c, dict) and (c.get("name") or c.get("skill") or c.get("action") or c.get("command") or c.get("type") or c.get("tool"))]
            # Single tool call as dict
            if data.get("action") or data.get("name") or data.get("skill") or data.get("command") or data.get("type") or data.get("tool"):
                return [data]
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict) and (c.get("name") or c.get("skill") or c.get("action") or c.get("command") or c.get("type") or c.get("tool"))]
        return None

    def _split_text_and_calls(self, raw: str) -> tuple:
        if not raw:
            return "", []

        # 0. Check for DSML tags (Avrora execution style)
        first_dsml = re.search(r'<[｜|]{2}DSML[｜|]{2}', raw)
        if first_dsml:
            calls = self._parse_dsml_calls(raw)
            if calls:
                # Find the last closing tag
                last_dsml_end = raw.rfind('>')
                if last_dsml_end > first_dsml.start():
                    text = (raw[:first_dsml.start()] + raw[last_dsml_end + 1:]).strip()
                else:
                    text = raw[:first_dsml.start()].strip()
                return text, calls

        # 1. Try direct JSON parse (bare array or object)
        try:
            data = json.loads(raw.strip())
            if isinstance(data, list):
                calls = [c for c in data if isinstance(c, dict) and (c.get("action") or c.get("name") or c.get("skill") or c.get("command") or c.get("type") or c.get("tool"))]
                if calls:
                    return "", calls
            elif isinstance(data, dict):
                calls = self._extract_calls_from_json(raw)
                if calls:
                    text = data.get("text") or data.get("message") or ""
                    return text, calls
        except Exception:
            pass

        # 2. Match markdown code fences containing JSON arrays OR objects
        md_match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", raw, re.DOTALL)
        if md_match:
            calls = self._extract_calls_from_json(md_match.group(1))
            if calls:
                text = (raw[: md_match.start()] + raw[md_match.end():]).strip()
                return text, calls

        # 3. Look for {"tool_calls": [...]} or {"calls": [...]}
        json_match = re.search(r'\{\s*"(?:tool_calls|calls)"\s*:\s*\[.*', raw, re.DOTALL)
        if json_match:
            text = (raw[: json_match.start()]).strip()
            return text, self._parse_tool_calls(raw) or []

        # 4. Look for bare array starting with [{"action" or [{"type" or [{"command" or [{"tool"
        arr_match = re.search(r'\[\s*\{\s*"(?:action|name|skill|type|command|tool)".*', raw, re.DOTALL)
        if arr_match:
            calls = self._extract_calls_from_json(arr_match.group(0))
            if calls:
                text = (raw[: arr_match.start()]).strip()
                return text, calls

        return raw, []
