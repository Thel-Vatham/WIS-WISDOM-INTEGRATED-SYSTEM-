"""
WIS - Cognitive agent for humanoid robots.
Main entry point - Punto de entrada principal.

Arranca todos los componentes de WIS y abre la consola web.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path

# Asegura que el directorio raiz este en sys.path para los imports.
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Verificar e instalar dependencias antes de importar otros modulos
from core.dependencies import check_and_install_dependencies
check_and_install_dependencies(ROOT_DIR / "requirements.txt")

from core.safety import SafetyPolicy
from core.reasoning import ReasoningEngine
from core.memory import Memory
from core.llm_client import LLMClient, ModelRouter
from core.identity import Identity
from core.pipeline import ActionPipeline
from core.skill_memory import SkillMemory
from abilities.registry import AbilityRegistry

logger = logging.getLogger("wis")


import os

def load_env_files() -> None:
    """Busca y carga archivos de claves (.env, Keys.env) en el entorno."""
    candidates = [
        ROOT_DIR / "Keys.env",
        ROOT_DIR / ".env",
        ROOT_DIR.parent / "Keys.env",
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception as e:
                logger.debug("Error leyendo env candidate %s: %s", path, e)


def load_settings() -> dict:
    """Carga la configuracion desde config/settings.json y resuelve claves de entorno."""
    load_env_files()
    settings_path = ROOT_DIR / "config" / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception as e:
            logger.warning(f"Error al leer settings.json: {e}")

    llm_cfg = settings.setdefault("llm", {})
    api_key = llm_cfg.get("api_key", "").strip()

    if not api_key:
        if os.getenv("DEEPSEEK_API_KEY"):
            llm_cfg["api_key"] = os.getenv("DEEPSEEK_API_KEY")
            llm_cfg["base_url"] = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            llm_cfg["model"] = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            llm_cfg["fallback_model"] = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            logger.info("Auto-configured DeepSeek API key from environment.")
        elif os.getenv("OPENROUTER_API_KEY"):
            llm_cfg["api_key"] = os.getenv("OPENROUTER_API_KEY")
            llm_cfg["base_url"] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            llm_cfg["model"] = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
            llm_cfg["fallback_model"] = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
            logger.info("Auto-configured OpenRouter API key from environment.")
        elif os.getenv("ZHIPUAI_API_KEY"):
            llm_cfg["api_key"] = os.getenv("ZHIPUAI_API_KEY")
            logger.info("Auto-configured ZhipuAI API key from environment.")

    return settings


def resolve_console_token(settings: dict) -> str:
    """Resuelve el token de autenticacion de la consola web.

    Orden de prioridad:
      1. settings['server']['console_token']
      2. Variable de entorno WIS_CONSOLE_TOKEN
      3. Token persistente en Data/.wis_console_token
      4. Genera uno nuevo aleatorio y lo persiste.

    El token nunca se loggea en claro.
    """
    import secrets

    server_cfg = settings.setdefault("server", {})
    token = str(server_cfg.get("console_token") or "").strip()
    if token:
        return token

    token = str(os.getenv("WIS_CONSOLE_TOKEN") or "").strip()
    if token:
        server_cfg["console_token"] = token
        return token

    token_file = ROOT_DIR / "Data" / ".wis_console_token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        if token_file.exists():
            stored = token_file.read_text(encoding="utf-8").strip()
            if stored:
                server_cfg["console_token"] = stored
                return stored
        new_token = secrets.token_urlsafe(32)
        token_file.write_text(new_token, encoding="utf-8")
        try:
            # Restringe permisos del archivo de token (best-effort).
            os.chmod(token_file, 0o600)
        except OSError:
            pass
        logger.info("Generated new console auth token (stored at %s).", token_file)
        server_cfg["console_token"] = new_token
        return new_token
    except OSError as exc:
        logger.warning("Could not persist console token: %s", exc)
        return secrets.token_urlsafe(32)


def setup_logging(level: str = "INFO") -> None:
    """Configura el sistema de logging para consola y archivo logs/wis.log."""
    logs_dir = ROOT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "wis.log"

    log_level = getattr(logging, level.upper(), logging.INFO)

    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        "%(asctime)s [WIS] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Stream handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(console_formatter)
    root_logger.addHandler(ch)

    # File handler
    try:
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setFormatter(file_formatter)
        root_logger.addHandler(fh)
        logger.info(f"File logging active at {log_file}")
    except OSError as err:
        logger.warning(f"Could not open log file {log_file}: {err}")


def build_core(settings: dict) -> dict:
    """Construye e interconecta todos los componentes de WIS."""
    llm_cfg = settings.get("llm", {})
    mem_cfg = settings.get("memory", {})
    security_cfg = settings.get("security", settings.get("safety", {}))
    agentic_cfg = settings.get("agentic", {})
    proactivity_cfg = settings.get("proactivity", {})

    db_path = ROOT_DIR / mem_cfg.get("db_path", "Data/wis_memory.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    identity_path = ROOT_DIR / "config" / "identity.md"

    # --- Identity ---
    persona = Identity(identity_path=identity_path)
    logger.info(f"Identity loaded: {persona.get_name()}")

    # --- Memory ---
    mnemonic = Memory(db_path=db_path)
    logger.info(f"Memory ready (history: {len(mnemonic.get_history())} turns)")

    # --- Hardware & Procedural Memory ---
    from core.hardware_memory import HardwareMemory
    hw_vault = HardwareMemory(db_path=db_path)
    logger.info(f"HardwareMemory ready ({len(hw_vault.list_devices())} registered devices)")

    # --- Skill Memory ---
    vault = SkillMemory(db_path=db_path)
    logger.info(f"SkillMemory ready (healthy: {vault.stats().get('healthy', 0)} skills)")

    # --- Local CPU LLM Engine ---
    from core.llm_client import LocalLLMClient
    local_nexus = LocalLLMClient()
    threading.Thread(target=local_nexus.ensure_model, daemon=True).start()
    logger.info("Local CPU LLM Engine initialized (models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf)")

    # --- LLM Client ---
    nexus = LLMClient(
        base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", "deepseek-chat"),
    )
    router = ModelRouter(
        fast_model=llm_cfg.get("model", "deepseek-chat"),
        strong_model=llm_cfg.get("fallback_model", "deepseek-chat"),
        local_client=local_nexus,
    )
    logger.info(f"LLMClient ready (model: {llm_cfg.get('model', 'deepseek-chat')})")

    # --- Reasoning ---
    cortex = ReasoningEngine(
        llm_client=nexus,
        identity=persona,
        memory=mnemonic,
    )

    # --- Safety (secure / privileged) ---
    aegis = SafetyPolicy(mode=security_cfg.get("mode", "secure"))
    logger.info(f"SafetyPolicy mode: {aegis.mode}")

    # --- Abilities ---
    registry = AbilityRegistry()
    _register_default_abilities(registry, settings, hw_vault)
    logger.info(f"Abilities: {list(registry.all().keys())}")

    # --- Engineering Fast-Path ---
    from core.fastpath import EngineeringFastPath
    fast_router = EngineeringFastPath(hardware_memory=hw_vault, abilities=registry)

    # --- Action Pipeline (multi-step) ---
    max_steps = agentic_cfg.get("max_steps", 10)
    praxis = ActionPipeline(
        reasoning=cortex,
        skill_memory=vault,
        abilities=registry,
        safety=aegis,
        max_steps=max_steps,
        fastpath=fast_router,
        hardware_memory=hw_vault,
    )
    logger.info(f"ActionPipeline ready (Fast-Path + 3 cognitive paths, max_steps={max_steps})")

    # --- Proactivity Engine ---
    proactivity = None
    if proactivity_cfg.get("enabled", True):
        from core.proactivity import ProactivityEngine
        check_interval = proactivity_cfg.get("check_interval_s", 60)
        proactivity = ProactivityEngine(
            path=ROOT_DIR / "Data" / "proactivity.json",
            check_interval=check_interval,
        )
        proactivity.start_daemon()
        logger.info(f"ProactivityEngine started (interval={check_interval}s)")

    # --- Goal Manager (Orchestrator) ---
    goal_manager = None
    from core.goal_manager import GoalManager
    from core.task_store import TaskStore
    gm_cfg = settings.get("goal_manager", {})
    task_store = TaskStore(db_path=ROOT_DIR / "Data" / "wis_tasks.db")
    goal_manager = GoalManager(
        pipeline=praxis,
        llm_client=nexus,
        task_store=task_store,
        eval_interval=gm_cfg.get("eval_interval_s", 30.0),
    )
    logger.info("GoalManager ready (orchestrator con planificación y walkthrough)")

    return {
        "persona": persona,
        "mnemonic": mnemonic,
        "vault": vault,
        "nexus": nexus,
        "router": router,
        "cortex": cortex,
        "aegis": aegis,
        "registry": registry,
        "praxis": praxis,
        "proactivity": proactivity,
        "goal_manager": goal_manager,
        "task_store": task_store,
        "hardware_memory": hw_vault,
        "fastpath": fast_router,
    }


def _register_default_abilities(registry: AbilityRegistry, settings: dict, hw_vault: Optional[Any] = None) -> None:
    """Registra las habilidades base de WIS."""
    # Voice (TTS)
    try:
        from abilities.voice import VoiceAbility

        voice_cfg = settings.get("voice", {})
        registry.register(VoiceAbility(
            rate=voice_cfg.get("rate", 170),
            language=voice_cfg.get("language", "en"),
            enabled=voice_cfg.get("enabled", True),
        ))
    except Exception as e:
        logger.warning(f"VoiceAbility not loaded: {e}")

    # Listen (STT)
    try:
        from abilities.listen import ListenAbility

        registry.register(ListenAbility())
    except Exception as e:
        logger.warning(f"ListenAbility not loaded: {e}")

    # System (time, date, info)
    try:
        from abilities.system import SystemAbility

        sys_cfg = settings.get("system_ability", {}) or {}
        sys_mode = str(sys_cfg.get("mode", "safe")).strip().lower()
        extra_allowed = sys_cfg.get("extra_allowed") or []
        registry.register(SystemAbility(
            mode=sys_mode,
            extra_allowed=set(extra_allowed) if extra_allowed else None,
        ))
    except Exception as e:
        logger.warning(f"SystemAbility not loaded: {e}")

    # Vision (camera)
    try:
        from abilities.vision import VisionAbility

        registry.register(VisionAbility())
    except Exception as e:
        logger.warning(f"VisionAbility not loaded: {e}")

    # Knowledge (web search)
    try:
        from abilities.knowledge import KnowledgeAbility

        registry.register(KnowledgeAbility())
    except Exception as e:
        logger.warning(f"KnowledgeAbility not loaded: {e}")

    # Browser (Playwright)
    try:
        from abilities.browser import BrowserAbility
        registry.register(BrowserAbility())
    except Exception as e:
        logger.warning(f"BrowserAbility not loaded: {e}")

    # Desktop (Win64 UIA)
    try:
        from abilities.desktop import DesktopAbility
        registry.register(DesktopAbility())
    except Exception as e:
        logger.warning(f"DesktopAbility not loaded: {e}")

    # Web Search (multi-provider)
    try:
        from abilities.web_search import WebSearchAbility
        registry.register(WebSearchAbility())
    except Exception as e:
        logger.warning(f"WebSearchAbility not loaded: {e}")

    # File Manager (native file/dir operations)
    try:
        from abilities.file_manager import FileManagerAbility
        registry.register(FileManagerAbility())
    except Exception as e:
        logger.warning(f"FileManagerAbility not loaded: {e}")

    # Meta-programming: BuilderAbility lets WIS create new abilities
    try:
        from abilities.builder import BuilderAbility
        registry.register(BuilderAbility(registry))
    except Exception as e:
        logger.warning(f"BuilderAbility not loaded: {e}")

    # Auto-discovery: DiscoveryAbility coordinates new integrations
    try:
        from abilities.discovery import DiscoveryAbility
        registry.register(DiscoveryAbility(registry))
    except Exception as e:
        logger.warning(f"DiscoveryAbility not loaded: {e}")

    # Hardware protocols (Serial/UART + MQTT)
    try:
        from abilities.serial_comm import SerialCommAbility
        registry.register(SerialCommAbility())
    except Exception as e:
        logger.warning(f"SerialCommAbility not loaded: {e}")

    try:
        from abilities.mqtt_comm import MQTTCommAbility
        registry.register(MQTTCommAbility())
    except Exception as e:
        logger.warning(f"MQTTCommAbility not loaded: {e}")

    # Microcontroller & Embedded Toolchains (PlatformIO, Arduino-CLI, esptool)
    try:
        from abilities.toolchain import ToolchainAbility
        registry.register(ToolchainAbility(hardware_memory=hw_vault))
    except Exception as e:
        logger.warning(f"ToolchainAbility not loaded: {e}")

    # Custom abilities generated dynamically (robots, IoT, etc.)
    try:
        registry.load_custom_directory()
    except Exception as e:
        logger.warning(f"Custom abilities not loaded: {e}")


async def _cli_session(praxis: ActionPipeline, goal_manager) -> None:
    """Bucle interactivo CLI ejecutado dentro de un event loop."""
    if goal_manager is not None:
        goal_manager.start()
    try:
        while True:
            user_input = (await asyncio.to_thread(input, "You > ")).strip()
            if not user_input or user_input.lower() in ("exit", "quit"):
                break
            result = await praxis.process(user_input)
            print(f"\nWIS [{result.get('path_used', 'path')}]: {result.get('response')}")
            if result.get("calls"):
                print(f"Executed calls: {result['calls']}")
            print()
    finally:
        if goal_manager is not None:
            await goal_manager.stop()


def run_cli_session(praxis: ActionPipeline, goal_manager=None) -> None:
    """Modo consola interactivo en terminal."""
    print("=== WIS CLI Mode ===")
    print("Type your message, or 'exit' / 'quit' to stop.\n")
    try:
        asyncio.run(_cli_session(praxis, goal_manager))
    except (KeyboardInterrupt, EOFError):
        pass
    print("WIS CLI session ended.")


import atexit


def close_core(core: dict) -> None:
    """Cierra los recursos y conexiones de forma segura al apagar."""
    if not core:
        return
    logger.info("Closing WIS core resources...")
    for key in ("mnemonic", "vault"):
        comp = core.get(key)
        if comp and hasattr(comp, "close"):
            try:
                comp.close()
            except Exception as e:
                logger.warning(f"Error closing {key}: {e}")

    registry = core.get("registry")
    if registry and hasattr(registry, "all"):
        for ab_name, ab in registry.all().items():
            if hasattr(ab, "close"):
                try:
                    ab.close()
                except Exception as e:
                    logger.warning(f"Error closing ability {ab_name}: {e}")


def setup_terminal_trace() -> None:
    """Suscribe un listener al event bus para imprimir trazas legibles en la consola/terminal."""
    from core.event_bus import event_bus

    trace_logger = logging.getLogger("wis.trace")

    def on_event(data: dict) -> None:
        evt = data.get("_event_name", "event")

        # Color/text formatting tags
        cyan = "\033[96m"
        green = "\033[92m"
        yellow = "\033[93m"
        red = "\033[91m"
        reset = "\033[0m"

        if evt == "pipeline.input_received":
            print(f"\n{cyan}[INPUT]{reset} User input: {data.get('text')}")
        elif evt == "pipeline.path_start":
            print(f"{cyan}[PATH]{reset} Pathway selected: '{data.get('path')}'")
        elif evt == "pipeline.loop_step":
            print(f"{cyan}[STEP]{reset} Iteration Step {data.get('step')}/{data.get('max_steps')} - Executing reasoning...")
        elif evt == "pipeline.call_start":
            print(f"{yellow}[EXECUTE]{reset} Action: {data.get('skill')}.{data.get('action')} - Params: {data.get('params')}")
        elif evt == "pipeline.call_result":
            status_color = green if data.get("success") else red
            status_text = "SUCCESS" if data.get("success") else "FAILED"
            print(f"{status_color}[OUTPUT] ({status_text}){reset} {data.get('skill')}.{data.get('action')} -> Output: {data.get('output')}")
        elif evt == "pipeline.call_blocked":
            print(f"{red}[SECURITY BLOCK]{reset} Action {data.get('name')} rejected by Aegis: {data.get('reason')}")
        elif evt == "pipeline.approval_required":
            print(f"{yellow}[AWAITING APPROVAL]{reset} operator confirmation required for: {data.get('name')}.{data.get('action')}")
        elif evt == "pipeline.call_approved":
            print(f"{green}[APPROVED]{reset} Action execution authorized by operator.")
        elif evt == "pipeline.call_denied":
            print(f"{red}[DENIED]{reset} Action execution rejected by operator.")
        elif evt == "pipeline.failure_classified":
            print(f"{yellow}[AUTO-REPAIR]{reset} Classified failure as {data.get('category')}: {data.get('error')}")
        elif evt == "pipeline.response_ready":
            success_status = green + "SUCCESS" if data.get("success") else red + "FAILED"
            print(f"{green}[RESPONSE]{reset} ({success_status}{reset}) Final response synthesis:")
            print(f"\"\"\"\n{data.get('response')}\n\"\"\"")
            print("-" * 50)

    event_bus.subscribe("*", on_event)

    # ── File-based execution trace (writes FULL trace to wis.log) ──────────
    def on_trace(data: dict) -> None:
        """Log every event_bus event to wis.log with structured detail."""
        evt = data.get("_event_name", "event")

        # Skip noisy token chunks from log (stream them only to terminal/WS)
        if evt == "reasoning.token_chunk":
            return

        if evt == "pipeline.input_received":
            trace_logger.info("INPUT | %s", data.get("text"))

        elif evt == "pipeline.path_start":
            trace_logger.info("PATH | %s | text=%s", data.get("path"), data.get("text"))

        elif evt == "pipeline.loop_step":
            trace_logger.info("STEP | %d/%d | text=%s", data.get("step", 0), data.get("max_steps", 0), data.get("text"))

        elif evt == "reasoning.prompt":
            sys_prompt = data.get("system_prompt", "")
            user_msg = data.get("user_message", "")
            model = data.get("model", "")
            history = data.get("history_turns", 0)
            trace_logger.info(
                "LLM_REQUEST | model=%s | history_turns=%d\n"
                "--- SYSTEM PROMPT START ---\n%s\n--- SYSTEM PROMPT END ---\n"
                "--- USER MESSAGE START ---\n%s\n--- USER MESSAGE END ---",
                model, history, sys_prompt, user_msg,
            )

        elif evt == "reasoning.response":
            raw = data.get("raw_response", "")
            text = data.get("text", "")
            calls = data.get("calls", [])
            model = data.get("model", "")
            trace_logger.info(
                "LLM_RESPONSE | model=%s\n"
                "--- RAW RESPONSE START ---\n%s\n--- RAW RESPONSE END ---\n"
                "PARSED_TEXT: %s\n"
                "PARSED_CALLS: %s",
                model, raw, text, json.dumps(calls, ensure_ascii=False, default=str),
            )

        elif evt == "pipeline.call_start":
            trace_logger.info("TOOL_CALL | %s.%s | params=%s",
                              data.get("skill"), data.get("action"),
                              json.dumps(data.get("params", {}), ensure_ascii=False, default=str))

        elif evt == "pipeline.call_result":
            status = "SUCCESS" if data.get("success") else "FAILED"
            trace_logger.info("TOOL_RESULT | %s | %s.%s | output=%s",
                              status, data.get("skill"), data.get("action"),
                              json.dumps(data.get("output", {}), ensure_ascii=False, default=str))

        elif evt == "pipeline.call_blocked":
            trace_logger.warning("BLOCKED | %s | reason=%s", data.get("name"), data.get("reason"))

        elif evt == "pipeline.approval_required":
            trace_logger.info("APPROVAL_REQUIRED | %s.%s", data.get("name"), data.get("action"))

        elif evt == "pipeline.call_approved":
            trace_logger.info("APPROVED | %s", data.get("name"))

        elif evt == "pipeline.call_denied":
            trace_logger.warning("DENIED | %s", data.get("name"))

        elif evt == "pipeline.failure_classified":
            trace_logger.warning("AUTO_REPAIR | category=%s | error=%s | text=%s",
                                 data.get("category"), data.get("error"), data.get("text"))

        elif evt == "pipeline.loop_done":
            trace_logger.info("LOOP_DONE | steps=%d/%d | success=%s | text=%s",
                              data.get("steps", 0), data.get("max_steps", 0),
                              data.get("success"), data.get("text"))

        elif evt == "pipeline.response_ready":
            trace_logger.info("RESPONSE | success=%s | path=%s | steps=%s\n--- FINAL RESPONSE ---\n%s\n--- END RESPONSE ---",
                              data.get("success"), data.get("path_used"), data.get("steps_used"),
                              data.get("response", ""))

        elif evt in ("pipeline.reflexive_hit", "pipeline.known_hit",
                      "pipeline.new_path", "pipeline.known_failed_fallback",
                      "pipeline.loop_cancelled"):
            trace_logger.info("EVENT | %s | text=%s", evt, data.get("text"))

    event_bus.subscribe("*", on_trace)


def main() -> None:
    """Entry point - Arranca WIS con consola web o CLI."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    setup_logging()
    print()
    print("  ===========================================")
    print("              W I S  v2.0.0                  ")
    print("     High-Performance Cognitive Agent        ")
    print("  ===========================================")
    print()
    logger.info("Starting WIS...")

    settings = load_settings()
    core = build_core(settings)
    setup_terminal_trace()
    atexit.register(close_core, core)

    if "--cli" in sys.argv:
        run_cli_session(core["praxis"], core["goal_manager"])
        return

    # --- Arrancar consola ---
    server_cfg = settings.get("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    port = server_cfg.get("port", 7777)

    # Resuelve el token de autenticacion y los origenes CORS permitidos.
    console_token = resolve_console_token(settings)
    cors_origins = server_cfg.get("cors_origins") or []

    from console.server import WISCoreContainer, run_server, start_server_thread

    container = WISCoreContainer(
        praxis=core["praxis"],
        cortex=core["cortex"],
        vault=core["vault"],
        abilities=core["registry"],
        proactivity=core.get("proactivity"),
        aegis=core["aegis"],
        goal_manager=core["goal_manager"],
    )

    if "--server" in sys.argv:
        logger.info(f"Running standalone WIS web server on http://{host}:{port}/console/")
        print(f"\n  WIS Server active at: http://{host}:{port}/console/")
        print(f"  Console auth token: {console_token}\n")
        run_server(
            host=host, port=port, core=container,
            auth_token=console_token, cors_origins=cors_origins,
        )
        return

    from console.web_view import launch_console

    # Intenta abrir con pywebview; si falla, abre el navegador.
    url = f"http://{host}:{port}/console/"
    try:
        launch_console(
            url=url,
            title="WIS Console",
            width=960,
            height=720,
            core=container,
            auth_token=console_token,
            cors_origins=cors_origins,
        )
    except Exception as e:
        logger.warning(f"pywebview failed ({e}), falling back to browser.")
        start_server_thread(
            host=host, port=port, core=container,
            auth_token=console_token, cors_origins=cors_origins,
        )
        import webbrowser
        webbrowser.open(url)
        # Mantiene el proceso vivo.
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("WIS stopped.")


if __name__ == "__main__":
    main()
