"""REM agent tool registry — memory inspekció és keresés."""
import logging

import psycopg2.extras

from axel_shared.db import db
from axel_rem import redis_mem

log = logging.getLogger(__name__)


def memory_stats() -> dict:
    """Memória rendszer összesített statisztika."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM axel_rem_memory")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM axel_rem_memory WHERE extracted = FALSE")
            unprocessed = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM axel_rem_memory WHERE embedding IS NOT NULL")
            with_embed = cur.fetchone()[0]
            cur.execute("""
                SELECT agent, COUNT(*) as cnt
                FROM axel_rem_memory GROUP BY agent ORDER BY cnt DESC
            """)
            by_agent = {r[0]: r[1] for r in cur.fetchall()}
    redis_entities = {}
    for agent in ["AXEL", "FORGE", "ATLAS", "REM"]:
        redis_entities[agent] = len(redis_mem.get_all_entities(agent))
    return {
        "total": total,
        "unprocessed_seeds": unprocessed,
        "with_embedding": with_embed,
        "by_agent": by_agent,
        "redis_entities": redis_entities,
    }


def memory_search(query: str, agent: str = None, k: int = 5) -> dict:
    """Szemantikus keresés a memóriában."""
    from axel_rem.search import search
    results = search(query, agent=agent, k=k)
    return {
        "query": query,
        "agent_filter": agent,
        "count": len(results),
        "results": [
            {
                "entity": r.get("entity"),
                "fact": r.get("fact", "")[:200],
                "agent": r.get("agent"),
                "similarity": round(float(r.get("similarity", 0)), 3),
                "source_ref": r.get("source_ref"),
            }
            for r in results
        ],
    }


def memory_recent(agent: str = None, limit: int = 10) -> dict:
    """Legfrissebb memóriák listája."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if agent:
                cur.execute(
                    """SELECT id, entity, fact, agent, source_ref, strength, created_at
                       FROM axel_rem_memory WHERE agent = %s AND extracted = TRUE
                       ORDER BY created_at DESC LIMIT %s""",
                    (agent.upper(), limit),
                )
            else:
                cur.execute(
                    """SELECT id, entity, fact, agent, source_ref, strength, created_at
                       FROM axel_rem_memory WHERE extracted = TRUE
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
            rows = cur.fetchall()
    return {
        "count": len(rows),
        "memories": [
            {
                "id": r["id"],
                "entity": r["entity"],
                "fact": r["fact"][:150] if r["fact"] else "",
                "agent": r["agent"],
                "source": r["source_ref"],
                "strength": r["strength"],
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ],
    }


TOOL_REGISTRY = {
    "memory_stats":   memory_stats,
    "memory_search":  memory_search,
    "memory_recent":  memory_recent,
}

TOOL_DEFINITIONS = [
    {
        "name": "memory_stats",
        "description": "Memória rendszer összesített statisztika — total, feldolgozatlan, embedding, agent szerinti bontás",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "memory_search",
        "description": "Szemantikus keresés a memóriában query szöveg alapján",
        "parameters": {
            "type": "object",
            "properties": {
                "query":  {"type": "string"},
                "agent":  {"type": "string", "description": "Szűrés agent-re (FORGE/ATLAS/AXEL), elhagyható"},
                "k":      {"type": "integer", "description": "Max találatok száma, default 5"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_recent",
        "description": "Legfrissebb extracted memóriák listája",
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Szűrés agent-re, elhagyható"},
                "limit": {"type": "integer", "description": "Max sorok száma, default 10"},
            },
            "required": [],
        },
    },
]
