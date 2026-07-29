"""WIS Abilities - Robot skill registry."""

# Re-exportar clases principales para importacion conveniente
# (import directo evita dependencias pesadas como cv2/pyttsx3 al cargar el paquete)
from .base import Ability

# Nota: BuilderAbility y DiscoveryAbility requieren una instancia de AbilityRegistry
# en su constructor — se cargan de forma lazy dentro de AbilityRegistry.with_defaults()

__all__ = ["Ability"]
