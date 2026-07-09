"""
Prompt-Chill daemon — mindig fut, nappal dolgozik.
Feladata:
  1. axel_rem_memory extracted=FALSE seed-ek feldolgozása
  2. axel_message rem_processed=FALSE üzenetek feldolgozása
  3. axel_task rem_processed=FALSE, status=DONE taskek feldolgozása
  4. agent_thinking rem_processed=FALSE bejegyzések feldolgozása

Soros feldolgozás: egy elem → LLM → embed → DB → következő elem.
Nincs mesterséges delay, az LLM válaszidő (~1-2s) adja a természetes throttlinget.
Ha nincs mit feldolgozni, 30s-t vár majd újraellenőriz.
"""
import logging
import threading

from axel_rem import db, redis_mem
from axel_rem.extract import extract_entity_fact
from axel_rem.embedder import embed, embed_batch

log = logging.getLogger(__name__)

IDLE_WAIT = 30   # másodperc — ha nincs mit feldolgozni


class PromptChill:

    def run(self):
        log.info("[PROMPT-CHILL] Indul — idle wait: %ds", IDLE_WAIT)
        try:
            from axel_rem.api import get_signal_event
            _event = get_signal_event()
        except Exception:
            _event = threading.Event()

        while True:
            did_work = False
            try:
                did_work |= self._process_one_seed()
                did_work |= self._process_one_message()
                did_work |= self._process_one_task()
                did_work |= self._process_one_thinking()
            except Exception as e:
                log.exception("[PROMPT-CHILL] Hiba: %s", e)

            if not did_work:
                _event.wait(timeout=IDLE_WAIT)
                _event.clear()

    # ── Egy seed feldolgozása (axel_rem_memory extracted=FALSE) ─────────────

    def _process_one_seed(self) -> bool:
        seeds = db.seed_get_unprocessed(limit=1)
        if not seeds:
            return False

        seed = seeds[0]
        try:
            text = (seed["fact"] + " " + (seed.get("chunk") or "")).strip()
            try:
                embedding = embed(text)
            except Exception:
                embedding = None

            extracted = extract_entity_fact(
                description=seed["fact"],
                summary=seed.get("chunk", ""),
            )
            db.seed_update(
                mem_id=seed["id"],
                entity=extracted["entity"],
                fact=extracted["fact"],
                tags=extracted["tags"],
                embedding=embedding,
            )
            if extracted["entity"]:
                redis_mem.push_memory(
                    agent=seed["agent"],
                    mem_id=seed["id"],
                    entity=extracted["entity"],
                    fact=extracted["fact"],
                    strength=1.0,
                )
            log.debug("[PROMPT-CHILL] seed #%d → %s",
                      seed["id"], extracted["entity"] or "(üres)")
        except Exception as e:
            log.warning("[PROMPT-CHILL] Seed #%d hiba: %s", seed["id"], e)

        return True

    # ── Egy üzenet feldolgozása (axel_message rem_processed=FALSE) ──────────

    def _process_one_message(self) -> bool:
        messages = db.messages_get_unprocessed(limit=1)
        if not messages:
            return False

        msg = messages[0]
        try:
            title = msg.get("title") or ""
            body  = msg.get("body")  or ""
            text  = f"{title} {body}".strip()[:600]

            extracted = extract_entity_fact(description=text, summary="")

            if not extracted.get("llm_ok"):
                log.debug("[PROMPT-CHILL] msg #%d LLM hiba — retry", msg["id"])
                return True  # volt munka (próbáltuk), de ne jelöljük feldolgozottnak

            entity = extracted["entity"]
            fact   = extracted["fact"]

            if entity and fact:
                try:
                    embedding = embed(f"{entity} {fact}")
                except Exception:
                    embedding = None

                mem_id = db.memory_insert_extracted(
                    entity=entity,
                    fact=fact,
                    chunk=text[:800],
                    source_ref=f"msg://{msg['id']}",
                    agent="AXEL",
                    embedding=embedding,
                    tags=extracted["tags"],
                )
                redis_mem.push_memory(
                    agent="AXEL",
                    mem_id=mem_id,
                    entity=entity,
                    fact=fact,
                    strength=1.0,
                )

            db.message_mark_processed(msg["id"])
            log.debug("[PROMPT-CHILL] msg #%d → %s", msg["id"], entity or "(nincs entity)")

        except Exception as e:
            log.warning("[PROMPT-CHILL] Message #%d hiba: %s", msg["id"], e)

        return True

    # ── Egy task feldolgozása (axel_task status=DONE, rem_processed=FALSE) ────

    def _process_one_task(self) -> bool:
        task = db.task_get_one_unprocessed()
        if not task:
            return False

        try:
            desc    = task.get("description") or ""
            summary = task.get("result_summary") or ""
            text    = f"{desc} {summary}".strip()[:600]
            agent   = task.get("assigned_agent") or "AXEL"

            extracted = extract_entity_fact(description=desc, summary=summary[:400])

            if not extracted.get("llm_ok"):
                log.debug("[PROMPT-CHILL] task #%d LLM hiba — retry", task["id"])
                return True  # ne jelöljük feldolgozottnak

            entity = extracted["entity"]
            fact   = extracted["fact"]

            if entity and fact:
                try:
                    embedding = embed(f"{entity} {fact}")
                except Exception:
                    embedding = None

                mem_id = db.memory_insert_extracted(
                    entity=entity,
                    fact=fact,
                    chunk=text[:800],
                    source_ref=f"task://{task['id']}",
                    agent=agent,
                    embedding=embedding,
                    tags=extracted["tags"],
                    source_type="task",
                )
                redis_mem.push_memory(
                    agent=agent,
                    mem_id=mem_id,
                    entity=entity,
                    fact=fact,
                    strength=1.0,
                )

            db.task_mark_processed(task["id"])
            log.debug("[PROMPT-CHILL] task #%d (%s) → %s", task["id"], agent,
                      entity or "(nincs entity)")

        except Exception as e:
            log.warning("[PROMPT-CHILL] Task #%d hiba: %s", task["id"], e)

        return True

    # ── Egy agent_thinking feldolgozása (rem_processed=FALSE) ───────────────

    def _process_one_thinking(self) -> bool:
        rows = db.thinking_get_unprocessed(limit=1)
        if not rows:
            return False

        row = rows[0]
        try:
            agent   = row.get("agent") or "AXEL"
            topic   = row["topic"]
            content = row["content"]
            text    = f"{topic}: {content[:500]}"

            extracted = extract_entity_fact(description=topic, summary=content[:400])

            if not extracted.get("llm_ok"):
                log.debug("[PROMPT-CHILL] thinking #%d LLM hiba — retry", row["id"])
                return True

            entity = extracted["entity"]
            fact   = extracted["fact"]

            try:
                embedding = embed(f"{topic} {content[:400]}")
            except Exception:
                embedding = None

            if entity and fact:
                try:
                    embed_ef = embed(f"{entity} {fact}")
                except Exception:
                    embed_ef = None

                mem_id = db.memory_insert_extracted(
                    entity=entity,
                    fact=fact,
                    chunk=text[:800],
                    source_ref=f"thinking://{row['id']}",
                    agent=agent,
                    embedding=embed_ef,
                    tags=extracted.get("tags", []),
                    source_type="thinking",
                )
                redis_mem.push_memory(
                    agent=agent,
                    mem_id=mem_id,
                    entity=entity,
                    fact=fact,
                    strength=1.0,
                )

            db.thinking_mark_processed(row["id"], embedding=embedding)
            log.debug("[PROMPT-CHILL] thinking #%d (%s / %s) → %s",
                      row["id"], agent, topic, entity or "(nincs entity)")

        except Exception as e:
            log.warning("[PROMPT-CHILL] Thinking #%d hiba: %s", row["id"], e)

        return True
