"""
Szemantikus keresés — pgvector + Redis, időrend figyelembevételével.
"""
import logging
from datetime import datetime, timezone

from axel_rem.db import memory_search_vector, memory_boost
from axel_rem.embedder import embed
from axel_rem import redis_mem

log = logging.getLogger(__name__)


def search(query: str, agent: str | None = None, k: int = 10) -> list[dict]:
    """Szemantikus keresés + boost az előhívott memóriákon."""
    try:
        vec = embed(query)
        results = memory_search_vector(vec, agent=agent, k=k)
        for r in results:
            memory_boost(r["id"], delta=0.05)
        return results
    except Exception as e:
        log.warning("[SEARCH] Hiba: %s", e)
        return []


def _format_date(ts) -> str:
    """created_at → 'YYYY-MM-DD' string."""
    if ts is None:
        return "?"
    if isinstance(ts, str):
        return ts[:10]
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d")
    return str(ts)[:10]


def build_context_for_task(agent: str, task_description: str, k: int = 8) -> str:
    """
    Task előtt injektált memória kontextus.
    - Dátum minden sornál → agent tudja melyik újabb
    - Entity dedup → ugyanarról az entity-ről csak a legfrissebb + egy régebbi zárójelben
    - Sorend: újabb előre (az idősúlyozott keresés és Redis erősség alapján)
    """
    # 1. Redis: entity-alapú legfrissebb memóriák
    redis_entries: list[dict] = []
    entities = redis_mem.get_all_entities(agent)
    for entity in entities[:12]:
        mems = redis_mem.get_memories(agent, entity, k=3)
        for m in mems:
            redis_entries.append({
                "entity": m.get("entity", ""),
                "fact":   m.get("fact", ""),
                "date":   m.get("created_at", ""),
                "strength": float(m.get("strength", 1.0)),
            })

    # 2. pgvector: időrend-súlyozott szemantikus keresés
    semantic_entries: list[dict] = []
    semantic = search(task_description, agent=agent, k=k)
    for r in semantic:
        if r.get("entity") and r.get("fact"):
            semantic_entries.append({
                "entity":   r["entity"],
                "fact":     r["fact"],
                "date":     _format_date(r.get("created_at")),
                "strength": float(r.get("strength", 1.0)),
            })

    # Összevonás + dedup: entity-nként max 2 sor (legújabb + egy régebbi)
    by_entity: dict[str, list[dict]] = {}
    for e in redis_entries + semantic_entries:
        key = (e["entity"] or "").lower().strip()
        by_entity.setdefault(key, []).append(e)

    lines = []
    for entity_key, entries in by_entity.items():
        # strength szerinti csökkentő sorrend → legerősebb (legújabb) elöl
        entries.sort(key=lambda x: x["strength"], reverse=True)
        best = entries[0]
        date_str = best["date"][:10] if best["date"] else "?"
        lines.append((best["strength"], f"- [{date_str}] {best['entity']}: {best['fact']}"))

        # Ha van régebbi ugyanerről az entity-ről, zárójelben jelezzük
        if len(entries) > 1:
            older = entries[1]
            older_date = older["date"][:10] if older["date"] else "?"
            lines.append((older["strength"] * 0.5,
                          f"  (korábban [{older_date}]: {older['fact']})"))

    if not lines:
        return ""

    # Strength szerinti csökkentő sorrend, max 12 sor
    lines.sort(key=lambda x: x[0], reverse=True)
    text_lines = [l[1] for l in lines[:12]]

    return "[MEMORY CONTEXT]\n" + "\n".join(text_lines) + "\n[/MEMORY CONTEXT]\n\n"
