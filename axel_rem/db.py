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
    """Idosullyozott szemantikus kereses: ujabb es erosebb memoriak elore kerulnek.
    
    Score = cosine_distance + 0.02 * days_old
    Azonos tema eseten az ujabb teny rangsora jobb lesz a reginel.
    """
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if agent:
                cur.execute(
                    """SELECT id, entity, fact, chunk, source_type, source_ref,
                              agent, strength, tags, created_at,
                              1 - (embedding <=> %s::vector) AS similarity
                       FROM axel_rem_memory
                       WHERE extracted = TRUE AND embedding IS NOT NULL AND agent = %s
                       ORDER BY
                         (embedding <=> %s::vector)
                         + (EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 * 0.02)
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
                       ORDER BY
                         (embedding <=> %s::vector)
                         + (EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 * 0.02)
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


def dream_memory_exists(agent: str, entity: str, hours: int = 20) -> bool:
    """Ellenőrzi van-e már friss dream-promótált sor erre az entity+agent párra."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM axel_rem_memory
                   WHERE agent = %s AND entity ILIKE %s
                     AND source_type = 'dream'
                     AND created_at >= NOW() - make_interval(hours => %s)
                   LIMIT 1""",
                (agent.upper(), entity, hours),
            )
            return cur.fetchone() is not None


def longterm_insert(entity: str, fact: str, chunk: str, source_ref: str,
                    agent: str, strength: float, embedding: list[float] | None,
                    tags: list[str]) -> int:
    """Long-term konszolidált memória sor létrehozása, visszaadja az új id-t."""
    with db() as conn:
        with conn.cursor() as cur:
            if embedding:
                cur.execute(
                    """INSERT INTO axel_rem_memory
                       (entity, fact, chunk, source_type, source_ref, agent,
                        strength, embedding, tags, extracted)
                       VALUES (%s, %s, %s, 'dream', %s, %s, %s, %s::vector, %s, TRUE)
                       RETURNING id""",
                    (entity, fact, chunk, source_ref, agent.upper(),
                     strength, embedding, tags),
                )
            else:
                cur.execute(
                    """INSERT INTO axel_rem_memory
                       (entity, fact, chunk, source_type, source_ref, agent,
                        strength, tags, extracted)
                       VALUES (%s, %s, %s, 'dream', %s, %s, %s, %s, TRUE)
                       RETURNING id""",
                    (entity, fact, chunk, source_ref, agent.upper(),
                     strength, tags),
                )
            return cur.fetchone()[0]



def tasks_get_recent(agent: str, hours: int = 26, limit: int = 60) -> list[dict]:
    """Kozvetlenul az axel_task tablabol olvassa az elmult N ora befejezett taskjait."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, description, result_summary, finished_at
                   FROM axel_task
                   WHERE assigned_agent = %s AND status = 'DONE'
                     AND finished_at >= NOW() - make_interval(hours => %s)
                   ORDER BY finished_at DESC
                   LIMIT %s""",
                (agent.upper(), hours, limit),
            )
            return [dict(r) for r in cur.fetchall()]


def messages_get_unprocessed(limit: int = 20) -> list[dict]:
    """axel_message sorok ahol rem_processed=FALSE."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, sender, title, body, task_id, created_at
                   FROM axel_message
                   WHERE rem_processed = FALSE
                   ORDER BY created_at ASC
                   LIMIT %s""",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def message_mark_processed(msg_id: int) -> None:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE axel_message SET rem_processed = TRUE WHERE id = %s",
                (msg_id,),
            )


def messages_mark_processed_bulk(ids: list[int]) -> None:
    if not ids:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE axel_message SET rem_processed = TRUE WHERE id = ANY(%s)",
                (ids,),
            )


def tasks_get_unprocessed(agent: str, limit: int = 80) -> list[dict]:
    """axel_task sorok ahol rem_processed=FALSE es status=DONE."""
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, description, result_summary, finished_at
                   FROM axel_task
                   WHERE assigned_agent = %s AND status = 'DONE'
                     AND rem_processed = FALSE
                   ORDER BY finished_at DESC
                   LIMIT %s""",
                (agent.upper(), limit),
            )
            return [dict(r) for r in cur.fetchall()]


def tasks_mark_processed_bulk(ids: list[int]) -> None:
    if not ids:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE axel_task SET rem_processed = TRUE WHERE id = ANY(%s)",
                (ids,),
            )


def memory_insert_extracted(entity: str, fact: str, chunk: str, source_ref: str,
                            agent: str, embedding: list[float] | None,
                            tags: list[str], strength: float = 1.0) -> int:
    """Kozvetlen (mar feldolgozott) memoria sor irasa."""
    with db() as conn:
        with conn.cursor() as cur:
            if embedding:
                cur.execute(
                    """INSERT INTO axel_rem_memory
                       (entity, fact, chunk, source_type, source_ref, agent,
                        strength, embedding, tags, extracted)
                       VALUES (%s, %s, %s, 'message', %s, %s, %s, %s::vector, %s, TRUE)
                       RETURNING id""",
                    (entity, fact, chunk, source_ref, agent.upper(),
                     strength, embedding, tags),
                )
            else:
                cur.execute(
                    """INSERT INTO axel_rem_memory
                       (entity, fact, chunk, source_type, source_ref, agent,
                        strength, tags, extracted)
                       VALUES (%s, %s, %s, 'message', %s, %s, %s, %s, TRUE)
                       RETURNING id""",
                    (entity, fact, chunk, source_ref, agent.upper(),
                     strength, tags),
                )
            return cur.fetchone()[0]
