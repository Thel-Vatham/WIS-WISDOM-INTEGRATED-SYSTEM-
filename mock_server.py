"""
Mock Robot Controller Server - Port 8999
Lightweight HTTP REST API with Live Web Dashboard for testing WIS autonomous communication.
Includes dynamic telemetry simulation and a premium interactive override control dashboard.
Supports outbound sensor event dispatch (voice, touch, vision) directly to the WIS console endpoint.
"""

import json
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Global State representing the Robot State
# ---------------------------------------------------------------------------
state_lock = threading.Lock()
ROBOT_STATE = {
    "battery_level_percent": 94.0,
    "temperature_celsius": 36.2,
    "motors_status": "ready",  # ready, active, error
    "sensors_active": ["lidar", "camera", "imu", "ultrasonic"],
    "operation_mode": "idle",  # idle, moving, charging, e-stop
    "location": "Lab Electronica - Area 1",
    "last_command": "N/A",
    "command_response": "Esperando ordenes del sistema de control...",
    "destination": "",
    "movement_timer": 0.0,
    "wis_endpoint": "http://127.0.0.1:7777/api/chat",
}

# ---------------------------------------------------------------------------
# Dynamic Telemetry Simulation Background Thread
# ---------------------------------------------------------------------------
def telemetry_simulation():
    global ROBOT_STATE
    while True:
        time.sleep(1.0)
        with state_lock:
            # 1. Simulación cuando está en modo de movimiento (moving)
            if ROBOT_STATE["operation_mode"] == "moving":
                # Consumo de batería: 0.8% por segundo
                ROBOT_STATE["battery_level_percent"] = max(0.0, ROBOT_STATE["battery_level_percent"] - 0.8)
                # Calentamiento de motores: +0.25 °C por segundo
                ROBOT_STATE["temperature_celsius"] = min(65.0, ROBOT_STATE["temperature_celsius"] + 0.25)
                ROBOT_STATE["motors_status"] = "active"

                # Temporizador de movimiento
                if ROBOT_STATE["movement_timer"] > 0:
                    ROBOT_STATE["movement_timer"] -= 1.0
                    if ROBOT_STATE["movement_timer"] <= 0:
                        # Llegada al destino
                        ROBOT_STATE["location"] = ROBOT_STATE["destination"] or ROBOT_STATE["location"]
                        ROBOT_STATE["operation_mode"] = "idle"
                        ROBOT_STATE["motors_status"] = "ready"
                        ROBOT_STATE["destination"] = ""
                        ROBOT_STATE["command_response"] = f"Desplazamiento completado. Robot posicionado en: {ROBOT_STATE['location']}."
                else:
                    ROBOT_STATE["operation_mode"] = "idle"
                    ROBOT_STATE["motors_status"] = "ready"

            # 2. Simulación cuando está cargando (charging)
            elif ROBOT_STATE["operation_mode"] == "charging":
                # Carga de batería: 1.5% por segundo
                ROBOT_STATE["battery_level_percent"] = min(100.0, ROBOT_STATE["battery_level_percent"] + 1.5)
                # Disipación de calor: -0.2 °C por segundo hasta la temperatura ambiente de 25 °C
                if ROBOT_STATE["temperature_celsius"] > 25.0:
                    ROBOT_STATE["temperature_celsius"] = max(25.0, ROBOT_STATE["temperature_celsius"] - 0.2)
                ROBOT_STATE["motors_status"] = "ready"

                if ROBOT_STATE["battery_level_percent"] >= 100.0:
                    ROBOT_STATE["operation_mode"] = "idle"
                    ROBOT_STATE["command_response"] = "Carga de bateria al 100% completada."

            # 3. Simulación en reposo (idle)
            elif ROBOT_STATE["operation_mode"] == "idle":
                # Descarga lenta en reposo: 0.02% por segundo
                if ROBOT_STATE["battery_level_percent"] > 0:
                    ROBOT_STATE["battery_level_percent"] = max(0.0, ROBOT_STATE["battery_level_percent"] - 0.02)
                # Normalización de temperatura a 35.0 °C
                if ROBOT_STATE["temperature_celsius"] > 35.0:
                    ROBOT_STATE["temperature_celsius"] = max(35.0, ROBOT_STATE["temperature_celsius"] - 0.05)
                elif ROBOT_STATE["temperature_celsius"] < 35.0:
                    ROBOT_STATE["temperature_celsius"] = min(35.0, ROBOT_STATE["temperature_celsius"] + 0.05)

            # 4. Parada de emergencia (e-stop)
            elif ROBOT_STATE["operation_mode"] in ("e-stop", "emergency"):
                ROBOT_STATE["motors_status"] = "error"
                # Enfriamiento rápido de motores desactivados
                if ROBOT_STATE["temperature_celsius"] > 27.0:
                    ROBOT_STATE["temperature_celsius"] = max(27.0, ROBOT_STATE["temperature_celsius"] - 0.15)


# ---------------------------------------------------------------------------
# Outbound Event Dispatcher to WIS
# ---------------------------------------------------------------------------
def dispatch_event_to_wis(message: str, endpoint_url: str):
    def run():
        payload = {"message": message}
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url,
            data=req_data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                result = resp.read().decode("utf-8")
                try:
                    data = json.loads(result)
                    reply = data.get("response", result[:150])
                except Exception:
                    reply = result[:150]
                
                with state_lock:
                    ROBOT_STATE["command_response"] = f"Respuesta de WIS: {reply}"
        except Exception as e:
            with state_lock:
                ROBOT_STATE["command_response"] = f"Error notificando a WIS: {e}. ¿Consola activa en {endpoint_url}?"
                
    t = threading.Thread(target=run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Natural Language Processor for Chat Commands
# ---------------------------------------------------------------------------
def parse_chat_command(prompt: str) -> dict | None:
    text = prompt.lower().strip()

    # E-Stop / Parada de Emergencia
    if any(w in text for w in ["emergencia", "e-stop", "estop", "parada de emergencia", "abortar"]):
        return {"action": "e_stop"}

    # Reestablecer
    elif any(w in text for w in ["resetear", "reestablecer", "limpiar error"]):
        return {"action": "reset_stop"}

    # Detenerse
    elif any(w in text for w in ["detente", "detener", "para", "parar", "stop"]):
        return {"action": "stop"}

    # Cargar
    elif any(w in text for w in ["cargar", "cargador", "cárgate", "carga", "charge"]):
        return {"action": "charge"}

    # Moverse
    elif any(w in text for w in ["muevete", "muévete", "ir a", "ve a", "navegar a", "desplázate a", "desplazate a", "move to", "go to"]):
        # Intentar extraer destino con regex
        patterns = [
            r"(?:muevete a|muévete a|ir a|ve a|navegar a|desplázate a|desplazate a|move to|go to)\s+([\w\s\-]{2,30})",
            r"(?:hacia)\s+([\w\s\-]{2,30})"
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                destination = match.group(1).title().strip()
                return {"action": "move", "destination": destination}
        
        # Fallback a palabras clave
        words = text.split()
        if len(words) > 2:
            return {"action": "move", "destination": " ".join(words[2:]).title().strip()}
        
    return None


# ---------------------------------------------------------------------------
# Premium Dashboard HTML
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Renata Robot Controller - Panel de Control Premium</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-dark: #07090e;
      --bg-card: rgba(15, 22, 38, 0.7);
      --bg-card-hover: rgba(22, 32, 54, 0.85);
      --border-glow: rgba(56, 189, 248, 0.15);
      --primary: #38bdf8;
      --primary-glow: rgba(56, 189, 248, 0.3);
      --success: #10b981;
      --success-glow: rgba(16, 185, 129, 0.25);
      --warning: #f59e0b;
      --danger: #ef4444;
      --danger-glow: rgba(239, 68, 68, 0.4);
      --text-main: #f1f5f9;
      --text-muted: #64748b;
    }

    * {
      box-sizing: border-box;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }

    body {
      margin: 0;
      padding: 30px;
      background: var(--bg-dark);
      background-image: 
        radial-gradient(at 0% 0%, rgba(24, 48, 89, 0.15) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(56, 189, 248, 0.08) 0px, transparent 50%);
      color: var(--text-main);
      font-family: 'Outfit', sans-serif;
      min-height: 100vh;
    }

    .container {
      max-width: 1200px;
      margin: 0 auto;
    }

    /* HEADER */
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      margin-bottom: 30px;
    }
    
    h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 700;
      background: linear-gradient(135deg, #fff 0%, var(--primary) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.5px;
    }

    .status-badge {
      font-size: 12px;
      font-weight: 700;
      background: rgba(16, 185, 129, 0.1);
      color: var(--success);
      border: 1px solid var(--success-glow);
      padding: 6px 14px;
      border-radius: 99px;
      letter-spacing: 1px;
      text-transform: uppercase;
      box-shadow: 0 0 15px var(--success-glow);
      animation: pulse-glow 2s infinite;
    }

    @keyframes pulse-glow {
      0%, 100% { box-shadow: 0 0 10px rgba(16, 185, 129, 0.2); }
      50% { box-shadow: 0 0 20px rgba(16, 185, 129, 0.4); }
    }

    /* GRID LAYOUT */
    .layout-grid {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 24px;
    }

    @media (max-width: 900px) {
      .layout-grid {
        grid-template-columns: 1fr;
      }
    }

    /* CARDS */
    .card {
      background: var(--bg-card);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border-glow);
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
      margin-bottom: 24px;
    }

    .card:hover {
      border-color: var(--primary-glow);
      background: var(--bg-card-hover);
    }

    .card-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--primary);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 0;
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* TELEMETRY READOUTS */
    .telemetry-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .stat-box {
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid rgba(255, 255, 255, 0.03);
      padding: 16px;
      border-radius: 12px;
      text-align: center;
    }

    .stat-box:hover {
      background: rgba(0, 0, 0, 0.3);
    }

    .stat-label {
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      margin-bottom: 8px;
      font-weight: 600;
    }

    .stat-value {
      font-size: 22px;
      font-weight: 700;
      color: #fff;
    }

    /* BATTERY BAR STYLE */
    .battery-container {
      width: 100%;
      height: 8px;
      background: rgba(255, 255, 255, 0.05);
      border-radius: 4px;
      margin-top: 10px;
      overflow: hidden;
    }
    
    .battery-bar {
      height: 100%;
      width: 0%;
      background: var(--success);
      border-radius: 4px;
      box-shadow: 0 0 10px rgba(16, 185, 129, 0.4);
    }

    /* FORMS & INPUTS */
    .form-group {
      margin-bottom: 16px;
    }

    .form-group label {
      display: block;
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 6px;
      font-weight: 600;
      text-transform: uppercase;
    }

    .input-field, select {
      width: 100%;
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #fff;
      padding: 10px 14px;
      border-radius: 8px;
      font-family: inherit;
      font-size: 14px;
    }

    .input-field:focus, select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 8px var(--primary-glow);
    }

    /* BUTTONS */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 20px;
      font-size: 14px;
      font-weight: 600;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      font-family: inherit;
      gap: 8px;
      width: 100%;
    }

    .btn-primary {
      background: var(--primary);
      color: #040814;
    }

    .btn-primary:hover {
      box-shadow: 0 0 15px var(--primary-glow);
      opacity: 0.95;
    }

    .btn-danger {
      background: var(--danger);
      color: #fff;
      box-shadow: 0 0 10px var(--danger-glow);
    }

    .btn-danger:hover {
      box-shadow: 0 0 20px var(--danger);
      opacity: 0.95;
    }

    .btn-secondary {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text-main);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }

    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
    }

    .btn-group {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    /* QUICK ACTIONS */
    .quick-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    @media (max-width: 500px) {
      .quick-grid {
        grid-template-columns: 1fr;
      }
    }

    /* Monospace Console */
    .console-box {
      background: #03050a;
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 12px;
      padding: 18px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      min-height: 120px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .console-prompt {
      color: var(--text-muted);
    }

    .console-resp {
      color: var(--primary);
      white-space: pre-wrap;
    }

    .pulse-blue {
      color: var(--primary);
      animation: pulse-txt-blue 1.5s infinite;
    }

    @keyframes pulse-txt-blue {
      0%, 100% { opacity: 0.8; }
      50% { opacity: 1; text-shadow: 0 0 8px var(--primary); }
    }

    .slider-container {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .slider-val {
      font-weight: 600;
      color: var(--primary);
      min-width: 40px;
    }

    /* Tabbed/Flex controls for events */
    .event-simulator-box {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .event-row {
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid rgba(255, 255, 255, 0.03);
      padding: 12px;
      border-radius: 10px;
    }

    .event-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-main);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .flex-group {
      display: flex;
      gap: 8px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <h1>Renata Robot Controller Dashboard</h1>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">Dispositivo: robot_alpha_01 | Port 8999</div>
      </div>
      <div class="status-badge" id="server-status">CONECTADO</div>
    </div>

    <!-- MAIN GRID -->
    <div class="layout-grid">
      
      <!-- LEFT COLUMN -->
      <div>
        <!-- Live Telemetry Readouts -->
        <div class="card">
          <div class="card-title">Telemetría en Tiempo Real</div>
          <div class="telemetry-grid">
            <div class="stat-box">
              <div class="stat-label">Batería</div>
              <div class="stat-value" id="val-battery">--%</div>
              <div class="battery-container">
                <div class="battery-bar" id="battery-bar"></div>
              </div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Temperatura</div>
              <div class="stat-value" id="val-temp">-- °C</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Estado de Motores</div>
              <div class="stat-value" id="val-motors">--</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Ubicación</div>
              <div class="stat-value" style="font-size: 16px;" id="val-location">--</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Modo Operativo</div>
              <div class="stat-value" style="font-size: 18px;" id="val-mode">--</div>
            </div>
          </div>
        </div>

        <!-- Manual Override Control Panel -->
        <div class="card">
          <div class="card-title">Edición y Override Manual (Telemetría)</div>
          <form id="override-form" onsubmit="event.preventDefault(); updateState();">
            <div class="form-group">
              <label>Batería del Robot</label>
              <div class="slider-container">
                <input type="range" id="input-battery" min="0" max="100" class="input-field" style="padding:0; margin:0;" oninput="document.getElementById('lbl-battery').textContent = this.value + '%'">
                <span class="slider-val" id="lbl-battery">94%</span>
              </div>
            </div>
            
            <div class="form-group">
              <label>Temperatura (°C)</label>
              <input type="number" step="0.1" id="input-temp" class="input-field" placeholder="36.2">
            </div>

            <div class="form-group">
              <label>Ubicación Actual</label>
              <input type="text" id="input-location" class="input-field" placeholder="Lab Electronica - Area 1">
            </div>

            <div class="form-group">
              <label>Motores</label>
              <select id="select-motors">
                <option value="ready">Ready (Listo)</option>
                <option value="active">Active (En Movimiento)</option>
                <option value="error">Error / Fallo</option>
              </select>
            </div>

            <div class="form-group">
              <label>Modo de Operación</label>
              <select id="select-mode">
                <option value="idle">Idle (Reposo)</option>
                <option value="moving">Moving (Desplazándose)</option>
                <option value="charging">Charging (Cargando)</option>
                <option value="e-stop">E-Stop (Emergencia)</option>
              </select>
            </div>

            <hr style="border:0; border-top: 1px solid rgba(255,255,255,0.05); margin: 20px 0;">
            
            <div class="form-group">
              <label>Callback URL de WIS (Consola API)</label>
              <input type="text" id="input-wis-endpoint" class="input-field" placeholder="http://127.0.0.1:7777/api/chat">
              <small style="color: var(--text-muted); font-size: 11px; margin-top: 4px; display: block;">WIS recibirá las alertas en esta dirección cuando simules eventos.</small>
            </div>

            <button type="submit" class="btn btn-primary">Guardar Configuración / Forzar Override</button>
          </form>
        </div>
      </div>

      <!-- RIGHT COLUMN -->
      <div>
        <!-- Quick Actions Panel -->
        <div class="card">
          <div class="card-title">Simulación de Comandos Rápidos</div>
          <div class="quick-grid">
            <button class="btn btn-secondary" onclick="triggerCommand('move', {destination: 'Cocina Principal'})">Navegar a Cocina</button>
            <button class="btn btn-secondary" onclick="triggerCommand('move', {destination: 'Laboratorio Central'})">Navegar a Lab</button>
            <button class="btn btn-secondary" onclick="triggerCommand('charge')">Conectar Cargador</button>
            <button class="btn btn-secondary" onclick="triggerCommand('stop')">Parar Actividad (Stop)</button>
          </div>
          <div style="margin-top: 10px; display: flex; gap: 10px;">
            <button class="btn btn-danger" onclick="triggerCommand('e_stop')" style="flex: 2;">🚨 EMERGENCY E-STOP</button>
            <button class="btn btn-secondary" onclick="triggerCommand('reset_stop')" style="flex: 1; font-size: 11px;">Reestablecer</button>
          </div>
        </div>

        <!-- Sensor Event Simulator Card (Robot to WIS) -->
        <div class="card">
          <div class="card-title">Simulador de Eventos de Sensores (Robot a WIS)</div>
          <div class="event-simulator-box">
            
            <!-- Voz / Speech Event -->
            <div class="event-row">
              <div class="event-title">Simular Escucha de Voz (Speech)</div>
              <div class="flex-group">
                <input type="text" id="event-voice-text" class="input-field" style="font-size: 13px;" placeholder="¿Qué le dicen al robot?" value="Por favor muévete al laboratorio central y carga tu batería">
                <button class="btn btn-secondary" style="width: auto; padding: 0 15px;" onclick="triggerSensorEvent('voice', document.getElementById('event-voice-text').value)">Enviar</button>
              </div>
            </div>

            <!-- Tacto / Touch Event -->
            <div class="event-row">
              <div class="event-title">Simular Sensor de Contacto (Touch / Choque)</div>
              <div class="flex-group">
                <select id="event-touch-sensor" style="font-size: 13px;">
                  <option value="Bumper Delantero (Colisión frontal)">Bumper Delantero (Choque)</option>
                  <option value="Bumper Trasero (Choque reversa)">Bumper Trasero (Choque)</option>
                  <option value="Sensor Capacitivo de Cabeza (Caricia/Contacto)">Sensor de Cabeza (Tacto)</option>
                </select>
                <button class="btn btn-secondary" style="width: auto; padding: 0 15px;" onclick="triggerSensorEvent('touch', document.getElementById('event-touch-sensor').value)">Enviar</button>
              </div>
            </div>

            <!-- Visión / Camera Event -->
            <div class="event-row">
              <div class="event-title">Simular Visión / Cámara</div>
              <div class="flex-group">
                <input type="text" id="event-vision-desc" class="input-field" style="font-size: 13px;" placeholder="¿Qué detecta la cámara?" value="Un usuario levantando la mano frente al robot">
                <button class="btn btn-secondary" style="width: auto; padding: 0 15px;" onclick="triggerSensorEvent('vision', document.getElementById('event-vision-desc').value)">Enviar</button>
              </div>
            </div>

          </div>
        </div>

        <!-- System Console Logs -->
        <div class="card">
          <div class="card-title">Consola de Control del Robot</div>
          <div class="console-box">
            <div>
              <span class="console-prompt">Último Suceso:</span> 
              <span id="console-command" style="color: #fff; font-weight: 600;">--</span>
            </div>
            <div>
              <span class="console-prompt">Estado / Respuesta:</span>
              <div id="console-response" class="console-resp">--</div>
            </div>
          </div>
          
          <div class="form-group" style="margin-top: 16px;">
            <input type="text" id="cmd-input" placeholder="Enviar comando de lenguaje natural (WIS a Robot)..." value="Por favor muévete a la cocina principal" style="width: 100%;" onkeydown="if(event.key === 'Enter') sendPrompt();">
          </div>
          <button class="btn btn-primary" onclick="sendPrompt()" style="width: 100%;">Enviar Comando Chat</button>
        </div>
      </div>

    </div>
  </div>

  <script>
    async function loadTelemetry() {
      try {
        const res = await fetch('/api/robot/status');
        const data = await res.json();
        
        // Actualizar UI
        document.getElementById('val-battery').textContent = Math.round(data.battery_level_percent) + '%';
        document.getElementById('val-temp').textContent = parseFloat(data.temperature_celsius).toFixed(1) + ' °C';
        document.getElementById('val-motors').textContent = data.motors_status.toUpperCase();
        document.getElementById('val-location').textContent = data.location;
        document.getElementById('val-mode').textContent = data.operation_mode.toUpperCase();
        
        // Barra de batería
        const bBar = document.getElementById('battery-bar');
        bBar.style.width = data.battery_level_percent + '%';
        if (data.battery_level_percent > 50) {
          bBar.style.background = 'var(--success)';
          bBar.style.boxShadow = '0 0 10px var(--success-glow)';
        } else if (data.battery_level_percent > 20) {
          bBar.style.background = 'var(--warning)';
          bBar.style.boxShadow = '0 0 10px rgba(245, 158, 11, 0.3)';
        } else {
          bBar.style.background = 'var(--danger)';
          bBar.style.boxShadow = '0 0 10px var(--danger-glow)';
        }

        // Colores de Motor
        const mVal = document.getElementById('val-motors');
        if (data.motors_status === 'active') {
          mVal.style.color = 'var(--primary)';
          mVal.className = 'stat-value pulse-blue';
        } else if (data.motors_status === 'ready') {
          mVal.style.color = 'var(--success)';
          mVal.className = 'stat-value';
        } else {
          mVal.style.color = 'var(--danger)';
          mVal.className = 'stat-value';
        }

        // Colores de Modo
        const mdVal = document.getElementById('val-mode');
        if (data.operation_mode === 'moving') {
          mdVal.style.color = 'var(--warning)';
        } else if (data.operation_mode === 'charging') {
          mdVal.style.color = 'var(--primary)';
        } else if (data.operation_mode === 'idle') {
          mdVal.style.color = 'var(--text-muted)';
        } else {
          mdVal.style.color = 'var(--danger)';
        }

        // Actualizar inputs si el usuario no los está editando activamente
        updateInputIfNotActive('input-battery', Math.round(data.battery_level_percent));
        updateInputIfNotActive('input-temp', parseFloat(data.temperature_celsius).toFixed(1));
        updateInputIfNotActive('input-location', data.location);
        updateInputIfNotActive('select-motors', data.motors_status);
        updateInputIfNotActive('select-mode', data.operation_mode);
        updateInputIfNotActive('input-wis-endpoint', data.wis_endpoint);

        if (document.activeElement !== document.getElementById('input-battery')) {
          document.getElementById('lbl-battery').textContent = Math.round(data.battery_level_percent) + '%';
        }

        // Actualizar consola
        document.getElementById('console-command').textContent = data.last_command || 'Ninguna';
        document.getElementById('console-response').textContent = data.command_response || '';

      } catch (e) {
        console.error("Error al cargar telemetría:", e);
      }
    }

    function updateInputIfNotActive(id, val) {
      const el = document.getElementById(id);
      if (el && document.activeElement !== el) {
        el.value = val;
      }
    }

    // Enviar cambios de override manual y configuración
    async function updateState() {
      const payload = {
        battery_level_percent: parseFloat(document.getElementById('input-battery').value),
        temperature_celsius: parseFloat(document.getElementById('input-temp').value),
        location: document.getElementById('input-location').value,
        motors_status: document.getElementById('select-motors').value,
        operation_mode: document.getElementById('select-mode').value,
        wis_endpoint: document.getElementById('input-wis-endpoint').value
      };

      try {
        const res = await fetch('/api/robot/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        loadTelemetry();
      } catch (e) {
        console.error("Error al actualizar estado:", e);
      }
    }

    // Trigger de comandos estructurados (de WIS o dashboard al robot)
    async function triggerCommand(action, params = {}) {
      const payload = { action, ...params };
      try {
        const res = await fetch('/api/robot/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        loadTelemetry();
      } catch (e) {
        console.error("Error al enviar comando:", e);
      }
    }

    // Simular un evento de sensor del robot hacia WIS
    async function triggerSensorEvent(type, description) {
      if (!description) return;
      try {
        const res = await fetch('/api/robot/trigger_event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type, description })
        });
        const data = await res.json();
        loadTelemetry();
      } catch (e) {
        console.error("Error al enviar evento de sensor:", e);
      }
    }

    // Enviar comando libre en chat (Natural Language)
    async function sendPrompt() {
      const input = document.getElementById('cmd-input');
      const text = input.value.trim();
      if (!text) return;
      
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text })
        });
        const data = await res.json();
        loadTelemetry();
      } catch (e) {
        console.error("Error al enviar chat:", e);
      }
    }

    // Carga inicial y bucle
    loadTelemetry();
    setInterval(loadTelemetry, 1000);
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------
class RobotMockHandler(BaseHTTPRequestHandler):

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code: int, html_str: str):
        body = html_str.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_html(200, DASHBOARD_HTML)
        elif path == "/health":
            self._send_json(200, {
                "status": "online",
                "system": "Renata Robot Controller v1.2",
                "robot_id": "robot_alpha_01",
                "timestamp": datetime.now().isoformat()
            })
        elif path == "/api/robot/status":
            with state_lock:
                self._send_json(200, {
                    "battery_level_percent": float(ROBOT_STATE["battery_level_percent"]),
                    "temperature_celsius": float(ROBOT_STATE["temperature_celsius"]),
                    "motors_status": str(ROBOT_STATE["motors_status"]),
                    "sensors_active": list(ROBOT_STATE["sensors_active"]),
                    "operation_mode": str(ROBOT_STATE["operation_mode"]),
                    "location": str(ROBOT_STATE["location"]),
                    "last_command": str(ROBOT_STATE["last_command"]),
                    "command_response": str(ROBOT_STATE["command_response"]),
                    "wis_endpoint": str(ROBOT_STATE["wis_endpoint"]),
                })
        else:
            self._send_json(404, {"error": "Endpoint not found", "path": path})

    def do_POST(self):
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            payload = {}

        # ── Endpoint: Actualización manual de Telemetría (Override) y Config ──
        if path == "/api/robot/status":
            with state_lock:
                if "battery_level_percent" in payload:
                    ROBOT_STATE["battery_level_percent"] = max(0.0, min(100.0, float(payload["battery_level_percent"])))
                if "temperature_celsius" in payload:
                    ROBOT_STATE["temperature_celsius"] = float(payload["temperature_celsius"])
                if "motors_status" in payload:
                    ROBOT_STATE["motors_status"] = str(payload["motors_status"])
                if "operation_mode" in payload:
                    ROBOT_STATE["operation_mode"] = str(payload["operation_mode"])
                if "location" in payload:
                    ROBOT_STATE["location"] = str(payload["location"])
                if "wis_endpoint" in payload:
                    ROBOT_STATE["wis_endpoint"] = str(payload["wis_endpoint"])
                
                ROBOT_STATE["last_command"] = "Manual Override"
                ROBOT_STATE["command_response"] = "Configuracion y telemetria actualizadas manualmente."

            self._send_json(200, {"success": True, "message": "Robot state and config updated successfully."})

        # ── Endpoint: Simulación de Eventos de Sensor (Robot a WIS) ──
        elif path == "/api/robot/trigger_event":
            event_type = payload.get("type")
            description = payload.get("description")
            if not event_type or not description:
                self._send_json(400, {"error": "Missing 'type' or 'description' parameters"})
                return

            with state_lock:
                endpoint = ROBOT_STATE["wis_endpoint"]
                ROBOT_STATE["last_command"] = f"Simular Evento ({event_type})"
                ROBOT_STATE["command_response"] = f"Despachando evento a WIS..."
                
                # Construir el mensaje formateado que recibirá WIS
                if event_type == "voice":
                    msg = f"[EVENTO ROBOT] El robot ha escuchado por reconocimiento de voz: '{description}'. Responde adecuadamente."
                elif event_type == "touch":
                    msg = f"[EVENTO ROBOT] Sensor tactil activado: '{description}' en la ubicacion '{ROBOT_STATE['location']}'. Por favor reacciona ante este evento."
                elif event_type == "vision":
                    msg = f"[EVENTO ROBOT] La camara ha detectado: '{description}' en la ubicacion '{ROBOT_STATE['location']}'. Por favor reacciona ante este evento."
                else:
                    msg = f"[EVENTO ROBOT] Evento '{event_type}' detectado: '{description}'."

            # Despachar asíncronamente
            dispatch_event_to_wis(msg, endpoint)

            self._send_json(200, {
                "success": True, 
                "message": f"Event '{event_type}' triggered and queued for transmission to WIS."
            })

        # ── Endpoint: Comandos estructurados directos (WIS/Consola a Robot) ──
        elif path == "/api/robot/command":
            action = payload.get("action")
            if not action:
                self._send_json(400, {"error": "Missing 'action' parameter"})
                return

            with state_lock:
                success = self._execute_robot_action(action, payload)
            
            if success:
                self._send_json(200, {
                    "success": True, 
                    "operation_mode": ROBOT_STATE["operation_mode"], 
                    "response": ROBOT_STATE["command_response"]
                })
            else:
                self._send_json(400, {
                    "success": False, 
                    "message": f"Action '{action}' failed or not recognized. Motors may be in error state."
                })

        # ── Endpoint: Chat en Lenguaje Natural (WIS/Consola a Robot) ──
        elif path == "/api/chat":
            prompt = payload.get("prompt") or payload.get("message") or ""
            with state_lock:
                ROBOT_STATE["last_command"] = f"Chat: '{prompt}'"
                
                # Intentar parsear acción del mensaje en lenguaje natural
                detected_cmd = parse_chat_command(prompt)
                
                if detected_cmd:
                    action = detected_cmd["action"]
                    # Ejecutar la acción deducida
                    action_success = self._execute_robot_action(action, detected_cmd)
                    if action_success:
                        response_text = f"Comando detectado: '{action}'. Respuesta: {ROBOT_STATE['command_response']}"
                    else:
                        response_text = f"Se detecto comando '{action}', pero fallo su ejecucion."
                else:
                    # Respuesta conversacional si no se mapea a comando directo
                    response_text = f"Mensaje recibido: '{prompt}'. No se detecto accion directa. Telemetria actual - Bateria: {int(ROBOT_STATE['battery_level_percent'])}%, Ubicacion: {ROBOT_STATE['location']}."
                    ROBOT_STATE["command_response"] = "Mensaje sin accion directa analizado."

            self._send_json(200, {
                "status": "success",
                "robot_id": "robot_alpha_01",
                "response": response_text,
                "timestamp": datetime.now().isoformat()
            })
        else:
            self._send_json(404, {"error": "Endpoint not found", "path": path})

    def _execute_robot_action(self, action: str, params: dict) -> bool:
        """
        Ejecuta acciones lógicas sobre la máquina de estados del robot.
        Debe ejecutarse bajo el contexto de 'state_lock'.
        """
        # Si está en estado de error, solo se permite la acción de restablecimiento
        if ROBOT_STATE["motors_status"] == "error" and action != "reset_stop":
            ROBOT_STATE["command_response"] = "ERROR: Motores bloqueados por seguridad o parada de emergencia activa."
            return False

        if action == "move":
            destination = params.get("destination", "Zona Desconocida")
            ROBOT_STATE["operation_mode"] = "moving"
            ROBOT_STATE["destination"] = destination
            ROBOT_STATE["movement_timer"] = 6.0  # Duración simulada del viaje: 6 segundos
            ROBOT_STATE["motors_status"] = "active"
            ROBOT_STATE["command_response"] = f"Iniciando trayectoria hacia: '{destination}'..."
            return True

        elif action == "charge":
            ROBOT_STATE["operation_mode"] = "charging"
            ROBOT_STATE["command_response"] = "Cargador conectado. Cargando bateria..."
            return True

        elif action == "stop":
            ROBOT_STATE["operation_mode"] = "idle"
            ROBOT_STATE["motors_status"] = "ready"
            ROBOT_STATE["destination"] = ""
            ROBOT_STATE["movement_timer"] = 0.0
            ROBOT_STATE["command_response"] = "Desplazamiento abortado. Estado: Reposo (Idle)."
            return True

        elif action == "e_stop":
            ROBOT_STATE["operation_mode"] = "e-stop"
            ROBOT_STATE["motors_status"] = "error"
            ROBOT_STATE["destination"] = ""
            ROBOT_STATE["movement_timer"] = 0.0
            ROBOT_STATE["command_response"] = "¡EMERGENCY E-STOP ACTIVADO! Motores bloqueados."
            return True

        elif action == "reset_stop":
            ROBOT_STATE["operation_mode"] = "idle"
            ROBOT_STATE["motors_status"] = "ready"
            ROBOT_STATE["command_response"] = "Parada de emergencia reestablecida. Motores desbloqueados y listos."
            return True

        return False

    def log_message(self, format, *args):
        sys.stdout.write(f"[MOCK SERVER] {format % args}\n")
        sys.stdout.flush()


def run_server(port: int = 8999):
    # Iniciar simulación de telemetría en hilo de fondo
    sim_thread = threading.Thread(target=telemetry_simulation, daemon=True)
    sim_thread.start()
    print("[MOCK SERVER] Hilo de simulacion de telemetria iniciado.")

    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, RobotMockHandler)
    print(f"[MOCK SERVER] Servidor corriendo en http://127.0.0.1:{port} ... Ctrl+C para detener.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[MOCK SERVER] Apagando servidor.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
