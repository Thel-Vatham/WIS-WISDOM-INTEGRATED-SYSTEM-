"""
WIS Hardware & Procedural Memory Engine.
=========================================
Base de datos técnica y procedimental para ingeniería y robótica:
- Device Graph (registro de dispositivos, puertos, baudrates, interfaces, topologías).
- Pinout Maps (mapeo de GPIOs, señales PWM/ADC/I2C/SPI, voltajes y sensores/actuadores).
- Procedural Memory (comandos de compilación, flasheo y depuración con ranking de éxito).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("wis.core.hardware_memory")


class HardwareMemory:
    """Memoria técnica y procedimental para control de hardware y microcontroladores."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hardware_devices (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    interface TEXT NOT NULL,          -- 'serial', 'i2c', 'spi', 'can', 'mqtt', 'modbus', 'scpi', 'http'
                    port_or_address TEXT NOT NULL,     -- 'COM4', '0x68', '192.168.1.50', 'robot/telemetry'
                    baud_rate INTEGER DEFAULT 115200,
                    protocol TEXT DEFAULT 'raw',       -- 'json', 'cobs', 'modbus_rtu', 'scpi', 'nmea'
                    description TEXT DEFAULT '',
                    config_json TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'configured',  -- 'configured', 'active', 'offline', 'error'
                    updated_at REAL DEFAULT (strftime('%s', 'now'))
                );

                CREATE TABLE IF NOT EXISTS hardware_pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    pin_or_gpio TEXT NOT NULL,        -- 'GPIO18', 'A0', 'PIN3'
                    function TEXT NOT NULL,           -- 'PWM', 'ADC', 'DIGITAL_OUT', 'I2C_SDA', 'SPI_MOSI'
                    signal_type TEXT DEFAULT 'digital',-- 'digital', 'analog', 'pwm', 'bus'
                    label TEXT NOT NULL,              -- 'motor_pwm_left', 'bme280_sda', 'relay_1'
                    notes TEXT DEFAULT '',
                    FOREIGN KEY (device_id) REFERENCES hardware_devices(device_id) ON DELETE CASCADE,
                    UNIQUE(device_id, pin_or_gpio)
                );

                CREATE TABLE IF NOT EXISTS procedural_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    toolchain TEXT NOT NULL,          -- 'platformio', 'arduino_cli', 'esptool', 'idf', 'shell'
                    context_tag TEXT NOT NULL,        -- 'esp32_wroom', 'stm32f4', 'uno_firmware'
                    success_count INTEGER DEFAULT 1,
                    failure_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0.0,
                    last_executed_at REAL DEFAULT (strftime('%s', 'now')),
                    last_error TEXT DEFAULT '',
                    UNIQUE(command, toolchain, context_tag)
                );

                CREATE INDEX IF NOT EXISTS idx_dev_interface ON hardware_devices(interface);
                CREATE INDEX IF NOT EXISTS idx_pins_label ON hardware_pins(label);
                CREATE INDEX IF NOT EXISTS idx_proc_tag ON procedural_commands(toolchain, context_tag);
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 1. Device Graph & Registry
    # ------------------------------------------------------------------ #
    def register_device(
        self,
        device_id: str,
        name: str,
        interface: str,
        port_or_address: str,
        baud_rate: int = 115200,
        protocol: str = "raw",
        description: str = "",
        config: Optional[Dict[str, Any]] = None,
        status: str = "configured",
    ) -> bool:
        """Registra o actualiza un dispositivo en la memoria técnica."""
        device_id = device_id.strip().lower()
        if not device_id or not name or not interface:
            return False

        config_str = json.dumps(config or {})
        now = time.time()
        sql = """
        INSERT INTO hardware_devices (device_id, name, interface, port_or_address, baud_rate, protocol, description, config_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            name=excluded.name,
            interface=excluded.interface,
            port_or_address=excluded.port_or_address,
            baud_rate=excluded.baud_rate,
            protocol=excluded.protocol,
            description=excluded.description,
            config_json=excluded.config_json,
            status=excluded.status,
            updated_at=excluded.updated_at;
        """
        with self._lock:
            self._conn.execute(sql, (device_id, name, interface.lower(), port_or_address, baud_rate, protocol.lower(), description, config_str, status, now))
            self._conn.commit()
            return True

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Recupera la información técnica de un dispositivo."""
        device_id = (device_id or "").strip().lower()
        sql = "SELECT * FROM hardware_devices WHERE device_id = ? LIMIT 1;"
        with self._lock:
            cur = self._conn.execute(sql, (device_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["config"] = json.loads(res.get("config_json") or "{}")
            except Exception:
                res["config"] = {}
            return res

    def list_devices(self, interface: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista todos los dispositivos registrados, opcionalmente filtrados por interfaz."""
        with self._lock:
            if interface:
                cur = self._conn.execute(
                    "SELECT * FROM hardware_devices WHERE interface = ? ORDER BY device_id;",
                    (interface.lower(),),
                )
            else:
                cur = self._conn.execute("SELECT * FROM hardware_devices ORDER BY device_id;")
            rows = cur.fetchall()
            devices = []
            for r in rows:
                item = dict(r)
                try:
                    item["config"] = json.loads(item.get("config_json") or "{}")
                except Exception:
                    item["config"] = {}
                devices.append(item)
            return devices

    def delete_device(self, device_id: str) -> bool:
        """Elimina un dispositivo y sus pines asociados."""
        device_id = (device_id or "").strip().lower()
        with self._lock:
            cur = self._conn.execute("DELETE FROM hardware_devices WHERE device_id = ?;", (device_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # 2. Pinout & Signal Mapping
    # ------------------------------------------------------------------ #
    def set_pin_mapping(
        self,
        device_id: str,
        pin_or_gpio: str,
        function: str,
        label: str,
        signal_type: str = "digital",
        notes: str = "",
    ) -> bool:
        """Mapea un pin o GPIO a una función o actuador/sensor."""
        device_id = (device_id or "").strip().lower()
        pin_or_gpio = (pin_or_gpio or "").strip().upper()
        label = (label or "").strip().lower()
        if not device_id or not pin_or_gpio or not label:
            return False

        sql = """
        INSERT INTO hardware_pins (device_id, pin_or_gpio, function, signal_type, label, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, pin_or_gpio) DO UPDATE SET
            function=excluded.function,
            signal_type=excluded.signal_type,
            label=excluded.label,
            notes=excluded.notes;
        """
        with self._lock:
            self._conn.execute(sql, (device_id, pin_or_gpio, function.upper(), signal_type.lower(), label, notes))
            self._conn.commit()
            return True

    def get_pin_map(self, device_id: str) -> List[Dict[str, Any]]:
        """Obtiene todos los pines mapeados para un dispositivo."""
        device_id = (device_id or "").strip().lower()
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM hardware_pins WHERE device_id = ? ORDER BY pin_or_gpio;",
                (device_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def find_pin_by_label(self, label_query: str) -> List[Dict[str, Any]]:
        """Busca un pin o señal por su etiqueta funcional (ej: 'servo', 'motor', 'relay')."""
        query = f"%{(label_query or '').strip().lower()}%"
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT p.*, d.name as device_name, d.port_or_address, d.interface
                FROM hardware_pins p
                JOIN hardware_devices d ON p.device_id = d.device_id
                WHERE p.label LIKE ? OR p.notes LIKE ?;
                """,
                (query, query),
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # 3. Procedural Command Memory (Toolchains / CLI)
    # ------------------------------------------------------------------ #
    def record_procedural_command(
        self,
        command: str,
        toolchain: str,
        context_tag: str,
        success: bool,
        duration_ms: float = 0.0,
        error: str = "",
    ) -> None:
        """Registra la ejecución de un comando de ingeniería y actualiza su tasa de éxito."""
        cmd = (command or "").strip()
        toolchain = (toolchain or "shell").strip().lower()
        context_tag = (context_tag or "general").strip().lower()
        if not cmd:
            return

        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, success_count, failure_count, avg_duration_ms FROM procedural_commands WHERE command=? AND toolchain=? AND context_tag=?;",
                (cmd, toolchain, context_tag),
            )
            row = cur.fetchone()
            if row:
                succ = row["success_count"] + (1 if success else 0)
                fail = row["failure_count"] + (0 if success else 1)
                total = succ + fail
                old_avg = row["avg_duration_ms"]
                new_avg = ((old_avg * (total - 1)) + duration_ms) / max(1, total) if duration_ms > 0 else old_avg

                self._conn.execute(
                    """
                    UPDATE procedural_commands
                    SET success_count=?, failure_count=?, avg_duration_ms=?, last_executed_at=?, last_error=?
                    WHERE id=?;
                    """,
                    (succ, fail, new_avg, now, error[:500] if not success else "", row["id"]),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO procedural_commands (command, toolchain, context_tag, success_count, failure_count, avg_duration_ms, last_executed_at, last_error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (cmd, toolchain, context_tag, 1 if success else 0, 0 if success else 1, duration_ms, now, error[:500] if not success else ""),
                )
            self._conn.commit()

    def get_best_procedural_command(self, toolchain: str, context_tag: str) -> Optional[Dict[str, Any]]:
        """Recupera el comando con mayor tasa de éxito y ejecuciones para un contexto."""
        toolchain = (toolchain or "").strip().lower()
        context_tag = (context_tag or "").strip().lower()
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT *, (CAST(success_count AS REAL) / (success_count + failure_count)) as success_rate
                FROM procedural_commands
                WHERE toolchain = ? AND context_tag = ? AND success_count > 0
                ORDER BY success_rate DESC, success_count DESC, last_executed_at DESC
                LIMIT 1;
                """,
                (toolchain, context_tag),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def query_topology_summary(self) -> Dict[str, Any]:
        """Genera un resumen técnico completo de la topología para inyectar al LLM."""
        devices = self.list_devices()
        summary = {"total_devices": len(devices), "devices": []}
        for d in devices:
            pins = self.get_pin_map(d["device_id"])
            summary["devices"].append({
                "id": d["device_id"],
                "name": d["name"],
                "interface": d["interface"],
                "port_or_address": d["port_or_address"],
                "baud_rate": d["baud_rate"],
                "protocol": d["protocol"],
                "status": d["status"],
                "pins": [{p["pin_or_gpio"]: f"{p['label']} ({p['function']})"} for p in pins],
            })
        return summary

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
