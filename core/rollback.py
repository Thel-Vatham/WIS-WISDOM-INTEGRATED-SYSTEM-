"""
WIS Action Rollback - Mecanismo de Seguridad y Respaldo para Operaciones del SO.
================================================----------------=============
Crea copias de seguridad automáticas antes de realizar cambios destructivos o
modificaciones en archivos del sistema operativo, permitiendo revertir acciones
en caso de error.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("wis.core.rollback")


class ActionRollback:
    """Manejador de copias de seguridad y reversión de acciones del SO."""

    def __init__(self, backup_dir: Optional[Path] = None) -> None:
        self.backup_dir = Path(backup_dir) if backup_dir else Path("Data/backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._history: List[Dict[str, str]] = []

    def create_backup(self, target_path: str) -> Optional[str]:
        """Crea una copia de seguridad de un archivo antes de ser modificado o eliminado."""
        path = Path(target_path)
        if not path.exists() or not path.is_file():
            return None

        timestamp = int(time.time() * 1000)
        backup_filename = f"{path.stem}_{timestamp}{path.suffix}.bak"
        backup_file = self.backup_dir / backup_filename

        try:
            shutil.copy2(path, backup_file)
            record = {
                "original": str(path.resolve()),
                "backup": str(backup_file.resolve()),
                "timestamp": str(timestamp),
            }
            self._history.append(record)
            logger.info("ActionRollback: respaldo creado para '%s' -> '%s'", path, backup_file)
            return str(backup_file)
        except Exception as exc:
            logger.error("ActionRollback: fallo al crear respaldo de '%s': %s", path, exc)
            return None

    def rollback_last(self) -> bool:
        """Revierte la última acción de modificación/borrado registrada."""
        if not self._history:
            return False

        last = self._history.pop()
        original = Path(last["original"])
        backup = Path(last["backup"])

        if not backup.exists():
            logger.warning("ActionRollback: no se encontró el archivo de respaldo '%s'", backup)
            return False

        try:
            shutil.copy2(backup, original)
            logger.info("ActionRollback: restauración exitosa de '%s' desde '%s'", original, backup)
            return True
        except Exception as exc:
            logger.error("ActionRollback: error al restaurar '%s': %s", original, exc)
            return False

    def list_backups(self) -> List[Dict[str, str]]:
        return list(self._history)


# Singleton de rollback
rollback_engine = ActionRollback()
