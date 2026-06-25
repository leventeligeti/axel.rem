"""
Prompt-Chill daemon — mindig fut, nappal dolgozik.
Feladata:
  1. axel_rem_memory extracted=FALSE seed-ek feldolgozása
  2. axel_message rem_processed=FALSE üzenetek feldolgozása
  3. LLM extrakció (entity, fact, tags) + embedding + Redis frissítés

Rate limiting: LLM hívások között 1s szünet, batch max 5, hogy ne terhelje túl a proxyt.
LLM hiba esetén a sor NEM kerül rem_processed=TRUE-ra — következő körben újrapróbálja.
"""
import logging
import time
import threading

from axel_rem import db, redis_mem
from axel_rem.extract import extract_entity_fact
from axel_rem.embedder import embed, embed_batch

log = logging.getLogger(__name__)

POLL_INTERVAL = 60       # másodperc — signal esetén azonnal felébred
SEED_BATCH    = 5        # seed-ek per kör
MSG_BATCH     = 2        # üzenetek per kör — Groq 30 RPM limit miatt
LLM_DELAY_SEC = 4.0      # LLM hívások közötti szünet (~2 RPM üzenetből)


class PromptChill:

    def run(self):
        log.info("[PROMPT-CHILL] Indul — poll: %ds, msg_batch: %d, llm_delay: %.1fs",
                 POLL_INTERVAL, MSG_BATCH, LLM_DELAY_SEC)
        try:
            from axel_rem.api import get_signal_event
            _event = get_signal_event()
        except Exception:
            _event = threading.Event()

        while True:
            try:
                self._process_seeds()
                self._process_messages()
            except Exception as e:
                log.exception("[PROMPT-CHILL] Hiba: %s", e)
            _event.wait(timeout=POLL_INTERVAL)
            _event.clear()

    # ── Seed feldolgozás (axel_rem_memory extracted=FALSE) ──────────────────

    def _process_seeds(self):
        seeds = db.seed_get_unprocessed(limit=SEED_BATCH)
        if not seeds:
            return

        log.info("[PROMPT-CHILL] %d seed feldolgozás", len(seeds))

        facts    = [s["fact"] for s in seeds]
        chunks   = [s.get("chunk", "") or "" for s in seeds]
        combined = [f + " " + c for f, c in zip(facts, chunks)]

        try:
            embeddings = embed_batch(combined)
        except Exception as e:
            log.warning("[PROMPT-CHILL] Embedding batch hiba: %s", e)
            embeddings = [None] * len(seeds)

        for seed, embedding in zip(seeds, embeddings):
            try:
                extracted = extract_entity_fact(
                    description=seed["fact"],
                    summary=seed.get("chunk", ""),
                )
                # LLM hiba esetén is feldolgozottnak jelöljük — seed-nél ez elfogadható
                # (a seed már a DB-ben van, csak entity hiányzik)
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
                log.debug("[PROMPT-CHILL] seed #%d → %s", seed["id"],
                          extracted["entity"] or "(üres)")
            except Exception as e:
                log.warning("[PROMPT-CHILL] Seed #%d hiba: %s", seed["id"], e)

    # ── Üzenet feldolgozás (axel_message rem_processed=FALSE) ───────────────

    def _process_messages(self):
        messages = db.messages_get_unprocessed(limit=MSG_BATCH)
        if not messages:
            return

        log.info("[PROMPT-CHILL] %d üzenet feldolgozás", len(messages))

        for msg in messages:
            try:
                title = msg.get("title") or ""
                body  = msg.get("body")  or ""
                text  = f"{title} {body}".strip()[:600]

                extracted = extract_entity_fact(description=text, summary="")

                # Ha LLM 500-at adott → NEM jelöljük feldolgozottnak, következő körben retry
                if not extracted.get("llm_ok"):
                    log.debug("[PROMPT-CHILL] msg #%d LLM hiba — retry következő körben",
                              msg["id"])
                    time.sleep(LLM_DELAY_SEC)
                    continue

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

                # Sikeres LLM hívás → feldolgozottnak jelöljük
                # (akkor is, ha az üzenetből nem jött ki értelmes entity — pl. rövid ping)
                db.message_mark_processed(msg["id"])
                log.debug("[PROMPT-CHILL] msg #%d → %s", msg["id"], entity or "(nincs entity)")

                time.sleep(LLM_DELAY_SEC)

            except Exception as e:
                log.warning("[PROMPT-CHILL] Message #%d hiba: %s", msg["id"], e)
