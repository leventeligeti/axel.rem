"""
Szemantikus keresés — pgvector + Redis fallback.
Az agentektől kapott query alapján releváns memóriákat ad vissza.
"""
import logging

from axel_rem.db import memory_search_vector, memory_boost
from axel_rem.embedder import embed
from axel_rem import redis_mem

log = logging.getLogger(__name__)


def search(query: str, agent: str | None = None, k: int = 10) -> list[dict]:
    """
    Szemantikus keresés a memóriában.
    Boost-olja a megtalált memóriákat (előhívás = erősödés).
    """
    try:
        vec = embed(query)
        results = memory_search_vector(vec, agent=agent, k=k)
        for r in results:
            memory_boost(r["id"], delta=0.05)
        return results
    except Exception as e:
        log.warning("[SEARCH] Hiba: %s", e)
        return []


def build_context_for_task(agent: str, task_description: str,
                           k: int = 8) -> str:
    """
    Task előtt hívódik — releváns memóriákat ad vissza prompt-hoz.
    Először Redis (gyors), majd pgvector (szemantikus) ha kevés van.
    """
    lines = []

    # 1. Redis: összes entity amit ismer ez az agent
    entities = redis_mem.get_all_entities(agent)
    redis_lines = []
    for entity in entities[:10]:
        mems = redis_mem.get_memories(agent, entity, k=2)
        for m in mems:
            redis_lines.append(f"- {m['entity']}: {m['fact']}")

    # 2. pgvector: szemantikus egyezés a task leírásra
    semantic = search(task_description, agent=agent, k=k)
    sem_lines = []
    for r in semantic:
        if r.get("entity") and r.get("fact"):
            entry = f"- {r['entity']}: {r['fact']}"
            if entry not in redis_lines:
                sem_lines.append(entry)

    all_lines = redis_lines[:6] + sem_lines[:4]
    if not all_lines:
        return ""

    return "[MEMORY CONTEXT]\n" + "\n".join(all_lines) + "\n[/MEMORY CONTEXT]\n\n"
