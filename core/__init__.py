"""WIS Core Cognitive Engine.

Standard programming architecture:
  - identity: Identity and persona loader
  - memory: Unified 3-tier memory engine (short-term, facts, episodic)
  - skill_memory: Skill cache with confidence scoring and degradation
  - llm_client: OpenAI-compatible LLM client and model router
  - reasoning: Context compiler and LLM reasoning engine
  - safety: Aegis safety policy engine
  - pipeline: 3-path action execution pipeline
  - event_bus: Pub/sub event bus
"""
__version__ = "1.0.0"
