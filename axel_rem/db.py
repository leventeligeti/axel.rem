"""axel_rem_memory DB műveletek."""
import logging
from datetime import datetime, timedelta, timezone

import psycopg2.extras

from axel_shared.db import db

log = logging.getLogger(__name__)


def seed_get_unprocessed(limit: int = 20) -> list[dict]:
    """Visszaadja az extracted=FALSE memóriákat feldolgozásra."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, fact, chunk, source_type, source_ref, agent
                   FROM axel_rem_memory
                   WHERE extracted = FALSE
                   ORDER BY created_at ASC
                   LIMIT %s""",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def seed_update(mem_id: int, entity: str, fact: str, tags: list[str],
                embedding: list[float] | None) -> None:
    """Feltölti az entity/fact/tags/embedding mezőket, extracted=TRUE."""
    with db() as conn:
        with conn.cursor() as cur:
            if embedding:
                cur.execute(
                    """UPDATE axel_rem_memory
                       SET entity=%s, fact=%s, tags=%s,
                           embedding=%s::vector, extracted=TRUE, updated_at=NOW()
                       WHERE id=%s""",
                    (entity, fact, tags, embedding, mem_id),
                )
            else:
                cur.execute(
                    """UPDATE axel_rem_memory
                       SET entity=%s, fact=%s, tags=%s,
                           extracted=TRUE, updated_at=NOW()
                       WHERE id=%s""",
                    (entity, fact, tags, mem_id),
                )


def memory_search_vector(embedding: list[float], agent: str | None,
                         k: int = 10) -> list[dict]:
    """Szemantikus keresés pgvector cosine similarity alapján."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if agent:
                cur.execute(
                    """SELECT id, entity, fact, chunk, source_type, source_ref,
                              agent, strength, tags, created_at,
                              1 - (embedding <=> %s::vector) AS similarity
                       FROM axel_rem_memory
                       WHERE extracted = TRUE AND embedding IS NOT NULL AND agent = %s
                       ORDER BY embedding <=> %s::vector
                       LIMIT %s""",
                    (embedding, agent.upper(), embedding, k),
                )
            else:
                cur.execute(
                    """SELECT id, entity, fact, chunk, source_type, source_ref,
                              agent, strength, tags, created_at,
                              1 - (embedding <=> %s::vector) AS similarity
                       FROM axel_rem_memory
                       WHERE extracted = TRUE AND embedding IS NOT NULL
                       ORDER BY embedding <=> %s::vector
                       LIMIT %s""",
                    (embedding, embedding, k),
                )
            return [dict(r) for r in cur.fetchall()]


def memory_get_recent(agent: str, hours: int = 48, limit: int = 100) -> list[dict]:
    """Legfrissebb memóriák adott agentnek — REM feldolgozáshoz."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, entity, fact, chunk, source_type, source_ref,
                          strength, tags, created_at
                   FROM axel_rem_memory
                   WHERE agent = %s AND created_at >= %s AND extracted = TRUE
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (agent.upper(), cutoff, limit),
            )
            return [dict(r) for r in cur.fetchall()]


def memory_boost(mem_id: int, delta: float = 0.1) -> None:
    """Erősség növelése — előhívás esetén."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE axel_rem_memory SET strength = LEAST(strength + %s, 10.0), "
                "updated_at = NOW() WHERE id = %s",
                (delta, mem_id),
            )


def memory_decay_all(factor: float = 0.98) -> int:
    """Napi decay — régi memóriák halványulnak."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE axel_rem_memory SET strength = strength * %s "
                "WHERE strength > 0.1",
                (factor,),
            )
            return cur.rowcount


def memory_get_by_entity(entity: str, agent: str | None = None,
                         limit: int = 5) -> list[dict]:
    """Entity alapú keresés — Redis miss esetén fallback."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if agent:
                cur.execute(
                    """SELECT id, entity, fact, strength, created_at
                       FROM axel_rem_memory
                       WHERE entity ILIKE %s AND agent = %s AND extracted = TRUE
                       ORDER BY strength DESC, created_at DESC LIMIT %s""",
                    (f"%{entity}%", agent.upper(), limit),
                )
            else:
                cur.execute(
                    """SELECT id, entity, fact, strength, created_at
                       FROM axel_rem_memory
                       WHERE entity ILIKE %s AND extracted = TRUE
                       ORDER BY strength DESC, created_at DESC LIMIT %s""",
                    (f"%{entity}%", limit),
                )
            return [dict(r) for r in cur.fetchall()]
