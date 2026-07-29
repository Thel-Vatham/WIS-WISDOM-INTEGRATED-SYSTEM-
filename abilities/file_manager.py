"""
WIS File Manager Ability - Habilidad nativa de gestion de archivos y directorios.
=============================================================================
Proporciona operaciones directas y seguras de lectura, escritura, busqueda
y navegacion en el sistema de archivos con encoding UTF-8 forzado.
"""
from __future__ import annotations

import os
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .base import Ability

logger = logging.getLogger("wis.abilities.file_manager")


class FileManagerAbility(Ability):
    """
    Habilidad nativa para operaciones de archivos y directorios.
    Soporta UTF-8 forzado para prevenir errores de encoding en Windows.
    """

    def __init__(self, base_dir: Optional[Union[str, Path]] = None) -> None:
        self._base_dir = Path(base_dir or os.getcwd()).resolve()

    @property
    def name(self) -> str:
        return "file_manager"

    @property
    def description(self) -> str:
        return "Native file and directory management (read, write, list, search, delete) with UTF-8 encoding."

    @property
    def domain(self) -> str:
        return "system"

    def _resolve_path(self, raw_path: str) -> Path:
        p = Path(raw_path or ".").expanduser()
        if not p.is_absolute():
            p = (self._base_dir / p).resolve()
        return p

    async def execute(self, action: str, params: dict) -> dict:
        action = (action or "").lower().strip()
        params = params or {}

        if action in ("read", "read_file", "view_file"):
            return self._read_file(params)
        if action in ("write", "write_file", "save_file", "create_file"):
            return self._write_file(params)
        if action in ("list", "list_directory", "list_dir", "ls", "dir"):
            return self._list_directory(params)
        if action in ("info", "file_info", "exists", "stat"):
            return self._file_info(params)
        if action in ("delete", "delete_file", "remove", "rm"):
            return self._delete_file(params)
        if action in ("search", "search_in_files", "grep"):
            return self._search_in_files(params)

        return {
            "success": False,
            "data": None,
            "message": f"Accion de gestion de archivos no reconocida: '{action}'.",
        }

    def _read_file(self, params: dict) -> dict:
        raw_path = params.get("path") or params.get("filename") or params.get("file")
        if not raw_path:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'path'."}

        path = self._resolve_path(str(raw_path))
        if not path.exists():
            return {"success": False, "data": None, "message": f"El archivo no existe: '{path}'."}
        if not path.is_file():
            return {"success": False, "data": None, "message": f"La ruta especificada no es un archivo: '{path}'."}

        encoding = params.get("encoding", "utf-8")
        start_line = params.get("start_line")
        end_line = params.get("end_line")
        max_bytes = int(params.get("max_bytes", 200_000))

        try:
            size = path.stat().st_size
            if size > max_bytes and not (start_line or end_line):
                # Leer solo los primeros max_bytes
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    content = f.read(max_bytes)
                return {
                    "success": True,
                    "data": {
                        "path": str(path),
                        "content": content,
                        "truncated": True,
                        "total_bytes": size,
                    },
                    "message": f"Archivo leido (truncado a {max_bytes} bytes de {size} bytes totales).",
                }

            with open(path, "r", encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)
            if start_line or end_line:
                s_idx = max(0, int(start_line or 1) - 1)
                e_idx = int(end_line or total_lines)
                lines = lines[s_idx:e_idx]

            content = "".join(lines)
            return {
                "success": True,
                "data": {
                    "path": str(path),
                    "content": content,
                    "total_lines": total_lines,
                    "lines_returned": len(lines),
                },
                "message": f"Archivo '{path.name}' leido con exito ({len(lines)} lineas).",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al leer archivo '{path}': {exc}"}

    def _write_file(self, params: dict) -> dict:
        raw_path = params.get("path") or params.get("filename") or params.get("file")
        content = params.get("content")
        if not raw_path:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'path'."}
        if content is None:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'content'."}

        path = self._resolve_path(str(raw_path))
        append = bool(params.get("append", False))
        encoding = params.get("encoding", "utf-8")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(path, mode, encoding=encoding, errors="replace") as f:
                f.write(str(content))

            mode_str = "anexado" if append else "escrito"
            return {
                "success": True,
                "data": {
                    "path": str(path),
                    "bytes_written": len(str(content).encode(encoding, errors="replace")),
                    "mode": mode_str,
                },
                "message": f"Archivo '{path.name}' {mode_str} correctamente en UTF-8.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al escribir archivo '{path}': {exc}"}

    def _list_directory(self, params: dict) -> dict:
        raw_path = params.get("path") or "."
        path = self._resolve_path(str(raw_path))

        if not path.exists():
            return {"success": False, "data": None, "message": f"El directorio no existe: '{path}'."}
        if not path.is_dir():
            return {"success": False, "data": None, "message": f"La ruta no es un directorio: '{path}'."}

        pattern = params.get("pattern") or "*"
        recursive = bool(params.get("recursive", False))
        limit = int(params.get("limit", 200))

        try:
            items: List[Dict[str, Any]] = []
            iterator = path.rglob(pattern) if recursive else path.glob(pattern)

            count = 0
            for item in iterator:
                if count >= limit:
                    break
                try:
                    stat = item.stat()
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size_bytes": stat.st_size if item.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    })
                    count += 1
                except Exception:
                    continue

            return {
                "success": True,
                "data": {
                    "directory": str(path),
                    "items": items,
                    "total_count": len(items),
                },
                "message": f"{len(items)} elementos encontrados en '{path.name}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al listar directorio '{path}': {exc}"}

    def _file_info(self, params: dict) -> dict:
        raw_path = params.get("path") or params.get("filename")
        if not raw_path:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'path'."}

        path = self._resolve_path(str(raw_path))
        if not path.exists():
            return {
                "success": True,
                "data": {"exists": False, "path": str(path)},
                "message": f"La ruta '{path}' no existe.",
            }

        try:
            stat = path.stat()
            return {
                "success": True,
                "data": {
                    "exists": True,
                    "path": str(path),
                    "name": path.name,
                    "is_file": path.is_file(),
                    "is_dir": path.is_dir(),
                    "size_bytes": stat.st_size if path.is_file() else None,
                    "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                },
                "message": f"Informacion obtenida para '{path.name}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error obteniendo info de '{path}': {exc}"}

    def _delete_file(self, params: dict) -> dict:
        raw_path = params.get("path") or params.get("filename")
        if not raw_path:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'path'."}

        path = self._resolve_path(str(raw_path))
        if not path.exists():
            return {"success": False, "data": None, "message": f"La ruta '{path}' no existe."}

        try:
            if path.is_dir():
                shutil.rmtree(path)
                msg = f"Directorio '{path.name}' eliminado con exito."
            else:
                path.unlink()
                msg = f"Archivo '{path.name}' eliminado con exito."

            return {"success": True, "data": {"path": str(path)}, "message": msg}
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al eliminar '{path}': {exc}"}

    def _search_in_files(self, params: dict) -> dict:
        query = str(params.get("query") or params.get("search") or "").strip()
        if not query:
            return {"success": False, "data": None, "message": "Se requiere el parametro 'query'."}

        raw_path = params.get("path") or "."
        path = self._resolve_path(str(raw_path))
        if not path.exists():
            return {"success": False, "data": None, "message": f"La ruta '{path}' no existe."}

        extensions = params.get("extensions") or [".py", ".json", ".md", ".txt", ".html", ".css", ".js"]
        if isinstance(extensions, str):
            extensions = [e.strip() for e in extensions.split(",") if e.strip()]

        max_results = int(params.get("max_results", 50))
        regex = bool(params.get("regex", False))
        flags = re.IGNORECASE if bool(params.get("case_insensitive", True)) else 0

        try:
            pattern = re.compile(query, flags) if regex else None
            matches: List[Dict[str, Any]] = []

            files_to_search = []
            if path.is_file():
                files_to_search.append(path)
            else:
                for root, _, filenames in os.walk(path):
                    for fname in filenames:
                        fpath = Path(root) / fname
                        if any(fname.endswith(ext) for ext in extensions):
                            files_to_search.append(fpath)

            for fpath in files_to_search:
                if len(matches) >= max_results:
                    break
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            matched = False
                            if regex and pattern:
                                matched = bool(pattern.search(line))
                            else:
                                matched = query.lower() in line.lower()

                            if matched:
                                matches.append({
                                    "file": str(fpath),
                                    "line_number": line_num,
                                    "content": line.strip(),
                                })
                                if len(matches) >= max_results:
                                    break
                except Exception:
                    continue

            return {
                "success": True,
                "data": {
                    "query": query,
                    "matches": matches,
                    "total_matches": len(matches),
                },
                "message": f"{len(matches)} coincidencias encontradas para '{query}'.",
            }
        except Exception as exc:
            return {"success": False, "data": None, "message": f"Error al buscar en archivos: {exc}"}

    def get_schema(self) -> list:
        return [
            {
                "action": "read_file",
                "description": "Read text content of a file in UTF-8 safely.",
                "params": {
                    "path": "string (required) - file path",
                    "start_line": "int (optional) - starting line",
                    "end_line": "int (optional) - ending line",
                },
            },
            {
                "action": "write_file",
                "description": "Write or append text content to a file in UTF-8.",
                "params": {
                    "path": "string (required) - target file path",
                    "content": "string (required) - text content",
                    "append": "bool (optional, default false) - append instead of overwrite",
                },
            },
            {
                "action": "list_directory",
                "description": "List files and subdirectories in a folder with metadata.",
                "params": {
                    "path": "string (optional, default '.') - directory path",
                    "pattern": "string (optional, default '*') - glob pattern",
                    "recursive": "bool (optional, default false)",
                },
            },
            {
                "action": "file_info",
                "description": "Check if a file or directory exists and get metadata.",
                "params": {"path": "string (required) - path to check"},
            },
            {
                "action": "delete_file",
                "description": "Delete a file or directory safely.",
                "params": {"path": "string (required) - file or folder path"},
            },
            {
                "action": "search_in_files",
                "description": "Search text or regex pattern across multiple files (grep).",
                "params": {
                    "query": "string (required) - search text or regex",
                    "path": "string (optional, default '.') - root directory",
                },
            },
        ]
