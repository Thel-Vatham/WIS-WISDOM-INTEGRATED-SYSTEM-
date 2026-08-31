# WIS - Wisdom Integrated System (v2.0)

<p align="center">
  <b>Living Autonomous Companion & Cognitive Operating System Core</b><br>
  <i>Designed for intelligent task automation, hardware management, computer vision, and real-time interactive voice.</i>
</p>

---

## 🌟 Overview

**WIS (Wisdom Integrated System)** is an advanced, autonomous AI assistant and cognitive engine designed to manage hardware, desktop workflows, browser interactions, and ambient computing tasks. Built with inspiration from systems like JARVIS and KAREN, WIS provides natural communication, fast local & cloud intelligence, and an extensible ability ecosystem.

---

## 🚀 Key Features

- 🧠 **Hybrid Cognitive Core & Multi-tier LLM Routing:**
  - Fast response reflex layer and episodic memory database (`wis_memory.db`).
  - Native support for Cloud LLMs (DeepSeek, OpenRouter, ZhipuAI) and Local CPU LLM inference (`ctransformers` / GGUF).
  - Autonomous goal decomposition and multi-step execution loop (`core/pipeline.py`, `core/goal_manager.py`).

- 🎙️ **High-Performance Voice & Audio Subsystem:**
  - Ultra-fast, natural speech synthesis powered by **Kokoro ONNX** (`abilities/voice.py`).
  - Voice recognition and transcription via **SpeechRecognition** & **Faster-Whisper** (`abilities/listen.py`).

- 👁️ **Vision & Desktop Automation:**
  - Screen capture, visual analysis, OCR, and GUI element recognition (`abilities/vision.py`).
  - Native Windows desktop automation using `pyautogui`, `uiautomation`, and audio control via `pycaw`.
  - Full headless/headed browser control using `Playwright` (`abilities/browser.py`).

- ⚡ **Hardware & IoT Protocol Suite:**
  - Real-time **Serial / UART** communications (`abilities/serial_comm.py`).
  - **MQTT** client for telemetry ingestion and IoT trigger evaluation (`abilities/mqtt_comm.py`, `core/telemetry.py`).

- 🛠️ **Self-Extension & Dynamic Abilities:**
  - Meta-programming ability builder (`abilities/builder.py`) and integration discovery (`abilities/discovery.py`).
  - Dynamic loading of custom user abilities (`abilities/custom/`).

- 💻 **Modern Web Console & Native Window:**
  - Interactive web interface served via FastAPI (`http://localhost:7777`).
  - Native desktop window support via `pywebview` (`console/web_view.py`).

---

## 📂 Project Architecture

```
WIS/
├── abilities/              # Modular ability plugins
│   ├── browser.py          # Playwright browser automation
│   ├── builder.py          # Meta-programming ability generator
│   ├── desktop.py          # Desktop control & OS management
│   ├── discovery.py        # Autonomous discovery engine
│   ├── file_manager.py     # Native filesystem operations
│   ├── knowledge.py        # Knowledge base & document query
│   ├── listen.py           # Speech-to-Text (STT) engine
│   ├── mqtt_comm.py        # MQTT IoT client
│   ├── registry.py         # Abilities registry & dispatcher
│   ├── serial_comm.py      # Serial/UART communication
│   ├── system.py           # System diagnostics & execution
│   ├── vision.py           # Computer vision & screen analysis
│   ├── voice.py            # Neural TTS engine (Kokoro ONNX)
│   └── web_search.py       # DuckDuckGo search integration
├── config/                 # Configurations & persona
│   ├── identity.md         # Persona directives & voice profile
│   └── settings.json       # System configurations
├── console/                # Web console server & UI
│   ├── web/                # Frontend (HTML, CSS, JS)
│   ├── server.py           # FastAPI backend server
│   └── web_view.py         # PyWebView desktop launcher
├── core/                   # Core cognitive OS architecture
│   ├── dependencies.py     # Auto-installer for dependencies
│   ├── event_bus.py        # Asynchronous event bus
│   ├── goal_manager.py     # Goal planner & subtask tracker
│   ├── identity.py         # Identity prompt manager
│   ├── llm_client.py       # Multi-model LLM router & Local CPU engine
│   ├── memory.py           # Episodic & semantic memory store
│   ├── pipeline.py         # Main cognition & execution pipeline
│   ├── proactivity.py      # Proactive trigger engine
│   ├── reasoning.py        # Planning & chain-of-thought engine
│   ├── safety.py           # Aegis safety & permission guardrails
│   ├── skill_memory.py     # Reflex skill & action cache
│   └── telemetry.py        # Real-time telemetry ingestion engine
├── Data/                   # Local databases and runtime state (git ignored)
├── Keys.env.template       # Environment keys template
├── main.py                 # Entry point (CLI & GUI server)
├── mock_server.py          # Testing & mock hardware server
├── requirements.txt        # Python dependency manifest
└── setup.bat               # Automated one-click Windows setup
```

---

## ⚡ Quick Start (Windows)

### 1. Installation & Environment Setup

Run the automated setup script to create a virtual environment and install all dependencies:
```bat
setup.bat
```

Or manually:
```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright Chromium browser
python -m playwright install chromium
```

### 2. Configure API Keys

Copy `Keys.env.template` to `Keys.env` and set your API key:
```bash
copy Keys.env.template Keys.env
```

Edit `Keys.env`:
```env
DEEPSEEK_API_KEY=sk-your-api-key-here
```

### 3. Launch WIS

#### Web Console Mode (Default)
```bash
python main.py --server
```
Open your browser at: **`http://localhost:7777`**

#### Interactive CLI Mode
```bash
python main.py --cli
```

---

## 🛡️ Safety & Security (Aegis)

WIS features built-in security profiles configured in `config/settings.json`:
- **Privileged Mode:** Full autonomous operation with security boundary checking.
- **Strict Mode:** Requires manual confirmation for sensitive actions (filesystem write/delete, shell execution).

---

## 📄 License

This project is developed for cognitive robotics, hardware integration, and autonomous assistance. All rights reserved.
