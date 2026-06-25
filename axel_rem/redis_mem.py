"""
Redis rövid távú memória — sorted sets recency alapján.
Key: axel:mem:{agent}:{entity}
Score: Unix timestamp (újabb = nagyobb = erősebb)
Value: JSON(id, entity, fact, strength)
"""
import json
import logging
import time

import redis

from axel_shared import config

log = logging.getLogger(__name__)

_TTL_SECONDS = 48 * 3600  # 48 óra
_MAX_PER_ENTITY = 10       # max emlékezet entity-nként


def _client() -> redis.Redis:
    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=config.REDIS_PASS or None,
        decode_responses=True,
    )


def _key(agent: str, entity: str) -> str:
    safe_entity = entity.lower().replace(" ", "_").replace("/", "_")[:60]
    return f"axel:mem:{agent.upper()}:{safe_entity}"


def push_memory(agent: str, mem_id: int, entity: str,
                fact: str, strength: float) -> None:
    """Memória írása Redis-be. Régi elemek automatikusan kiesnek."""
    if not entity:
        return
    try:
        r = _client()
        key = _key(agent, entity)
        value = json.dumps({
            "id": mem_id,
            "entity": entity,
            "fact": fact[:300],
            "strength": round(strength, 3),
        })
        score = time.time() + strength  # erősebb = magasabb score
        r.zadd(key, {value: score})
        r.zremrangebyrank(key, 0, -(_MAX_PER_ENTITY + 1))  # top N megtart
        r.expire(key, _TTL_SECONDS)
    except Exception as e:
        log.warning("[REDIS] push_memory hiba: %s", e)


def get_memories(agent: str, entity: str, k: int = 5) -> list[dict]:
    """Legfrissebb/legerősebb memóriák entity alapján."""
    try:
        r = _client()
        key = _key(agent, entity)
        items = r.zrevrange(key, 0, k - 1)
        return [json.loads(v) for v in items]
    except Exception as e:
        log.warning("[REDIS] get_memories hiba: %s", e)
        return []


def get_all_entities(agent: str) -> list[str]:
    """Összes entity amelyhez van Redis memória az agentnek."""
    try:
        r = _client()
        pattern = f"axel:mem:{agent.upper()}:*"
        prefix = f"axel:mem:{agent.upper()}:"
        keys = r.keys(pattern)
        return [k[len(prefix):] for k in keys]
    except Exception as e:
        log.warning("[REDIS] get_all_entities hiba: %s", e)
        return []


def build_memory_context(agent: str, entities: list[str], k_per: int = 3) -> str:
    """
    System prompt prefix — task előtt injektálva.
    Visszaad egy [MEMORY CONTEXT] blokkot vagy üres stringet.
    """
    lines = []
    for entity in entities[:8]:  # max 8 entity
        memories = get_memories(agent, entity, k=k_per)
        for m in memories:
            lines.append(f"- {m['entity']}: {m['fact']}")
    if not lines:
        return ""
    return "[MEMORY CONTEXT]\n" + "\n".join(lines) + "\n[/MEMORY CONTEXT]\n\n"
