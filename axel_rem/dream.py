"""
Dream agent — éjjeli REM feldolgozás.
Fut: minden nap 03:00-kor.
Feladata:
  1. axel_task (rem_processed=FALSE) + axel_message (rem_processed=FALSE) összegyűjtése
  2. axel_rem_memory legfrissebb soraival kombinálva
  3. LLM-alapú konszolidáció (duplikátok, minták)
  4. Strength decay
  5. Fontos memóriák boost-ja
  6. Long-term promóció — konszolidált új sor + embedding
  7. Cognitive Reflex Decay — ha egy entitás N cikluson át sem stabilizálódik,
     canonical ténnyé alakul: exempt a decay alól, további konszolidáció kizárva.
     Inspired by InsomnAI 3.0 — Frész Ferenc
     https://github.com/ferencfresz/insomnai_3.0
"""
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from axel_rem import config
from axel_rem import db
from axel_rem import redis_mem

log = logging.getLogger(__name__)

AGENTS = config.AGENTS
DREAM_HOUR = config.DREAM_HOUR
PROMOTE_MIN_IMPORTANCE = 6
PROMOTE_STRENGTH = 2.5
PROMOTE_DEDUP_HOURS = 20

# Cognitive Reflex Decay — Frész Ferenc (InsomnAI 3.0) ötlete alapján
# https://github.com/ferencfresz/insomnai_3.0
# Ha egy entitás ennyi dream cikluson át ismétlődik → canonical ténnyé válik.
REFLEX_DECAY_THRESHOLD = 3
CANONICAL_STRENGTH = 5.0


class DreamScheduler:

    def run(self):
        log.info("[DREAM] Scheduler indul — fut: minden nap %02d:00 UTC", DREAM_HOUR)
        while True:
            now = datetime.now(timezone.utc)
            if now.hour == DREAM_HOUR and now.minute < 5:
                self._run_dream_cycle()
                time.sleep(300)
            else:
                time.sleep(60)

    def _run_dream_cycle(self):
        log.info("[DREAM] Éjjeli ciklus indul — %s", datetime.now(timezone.utc).isoformat())

        decayed = db.memory_decay_all(factor=0.98)
        log.info("[DREAM] Decay: %d memória halványítva", decayed)

        promoted_total = 0
        for agent in AGENTS:
            try:
                promoted_total += self._consolidate_agent(agent)
            except Exception as e:
                log.warning("[DREAM] %s konszolidáció hiba: %s", agent, e)

        log.info("[DREAM] Ciklus kész — %d long-term promóció", promoted_total)

    def _consolidate_agent(self, agent: str) -> int:
        # 1. Már feldolgozott memóriák (axel_rem_memory)
        memories = db.memory_get_recent(agent=agent, hours=26, limit=300)

        # 2. Feldolgozatlan task-ok (axel_task rem_processed=FALSE)
        raw_tasks = db.tasks_get_unprocessed(agent=agent, limit=80)
        task_ids_processed: list[int] = []

        seen_refs = {m.get("source_ref") for m in memories}
        task_appended = 0
        for t in raw_tasks:
            ref = f"task://{t['id']}"
            if ref not in seen_refs:
                memories.append({
                    "id": None,
                    "entity": None,
                    "fact": t["description"][:300],
                    "chunk": (t.get("result_summary") or "")[:600],
                    "source_type": "task",
                    "source_ref": ref,
                    "strength": 1.0,
                    "tags": [],
                    "created_at": t["finished_at"],
                })
                task_appended += 1
            task_ids_processed.append(t["id"])

        # 3. Feldolgozatlan üzenetek (axel_message rem_processed=FALSE) — csak AXEL-nél
        msg_ids_processed: list[int] = []
        msg_appended = 0
        if agent == "AXEL":
            raw_msgs = db.messages_get_unprocessed(limit=100)
            for msg in raw_msgs:
                ref = f"msg://{msg['id']}"
                if ref not in seen_refs:
                    title = msg.get("title") or ""
                    body = msg.get("body") or ""
                    fact = f"{title} {body}".strip()[:300]
                    memories.append({
                        "id": None,
                        "entity": None,
                        "fact": fact,
                        "chunk": body[:600],
                        "source_type": "message",
                        "source_ref": ref,
                        "strength": 1.0,
                        "tags": [],
                        "created_at": msg["created_at"],
                    })
                    msg_appended += 1
                msg_ids_processed.append(msg["id"])

        if not memories:
            return 0

        log.info("[DREAM] %s: %d elem (%d memória, %d task, %d üzenet)",
                 agent, len(memories),
                 len(memories) - task_appended - msg_appended,
                 task_appended, msg_appended)

        by_entity: dict[str, list[dict]] = {}
        for m in memories:
            e = m.get("entity") or "általános"
            by_entity.setdefault(e, []).append(m)

        promoted = 0
        canonical_skipped = 0
        for entity, mems in by_entity.items():
            if len(mems) < 2:
                continue
            # Cognitive Reflex Decay: canonical entitások kizárva a konszolidációból
            if db.memory_has_canonical(agent, entity):
                canonical_skipped += 1
                log.debug("[DREAM] Canonical skip: %s/%s", agent, entity)
                continue
            if self._process_cluster(agent, entity, mems):
                promoted += 1

        if canonical_skipped:
            log.info("[DREAM] %s: %d canonical entitás kihagyva (Cognitive Reflex Decay)",
                     agent, canonical_skipped)

        # Feldolgozottnak jelöljük a forrás sorokat
        db.tasks_mark_processed_bulk(task_ids_processed)
        db.messages_mark_processed_bulk(msg_ids_processed)

        return promoted

    def _process_cluster(self, agent: str, entity: str, mems: list[dict]) -> bool:
        """Visszaad True-t ha long-term promóció történt."""
        facts_text = "\n".join(
            f"- [{m['created_at']}] {m['fact']}" for m in mems[:10]
        )
        prompt = f"""Az alábbi AI agent memóriák mind a "{entity}" témáról szólnak.
Összefoglalás: mit tudunk biztosan erről az entitásról?
Emelj ki 1-3 legfontosabb tényt, ami hosszú távon hasznos lehet.
Adj vissza JSON listát: [{{"fact": "...", "importance": 1-10}}]

Memóriák:
{facts_text}

CSAK a JSON listát add vissza."""

        try:
            r = httpx.post(
                f"{config.LLM_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
                timeout=20,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            if "```" in content:
                content = content.split("```")[1].lstrip("json").strip()
            consolidated = json.loads(content)

            promoted = False
            for item in consolidated:
                importance = int(item.get("importance", 5))
                fact = str(item.get("fact", "")).strip()

                if importance >= 7:
                    for m in mems[:3]:
                        if m.get("id"):
                            db.memory_boost(m["id"], delta=float(importance) * 0.05)

                if importance >= PROMOTE_MIN_IMPORTANCE and fact:
                    if self._promote_to_longterm(agent, entity, fact, importance, mems):
                        promoted = True

            log.debug("[DREAM] %s/%s: %d konszolidált tény", agent, entity, len(consolidated))
            return promoted

        except Exception as e:
            log.warning("[DREAM] Cluster hiba [%s/%s]: %s", agent, entity, e)
            return False

    def _promote_to_longterm(self, agent: str, entity: str, fact: str,
                             importance: int, source_mems: list[dict]) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        source_ref = f"dream://{today}/{entity.lower().replace(' ', '_')}"

        if db.dream_memory_exists(agent, entity, hours=PROMOTE_DEDUP_HOURS):
            # Cognitive Reflex Decay — Frész Ferenc (InsomnAI 3.0) ötlete alapján
            # https://github.com/ferencfresz/insomnai_3.0
            # Az entitás ismét visszatért — növeljük a számlálót.
            # Ha eléri a küszöböt, canonical ténnyé válik: nem kapja a napi decay-t,
            # és kizárjuk a jövőbeli konszolidációból.
            new_count = db.memory_increment_dream_count(agent, entity)
            if new_count >= REFLEX_DECAY_THRESHOLD:
                affected = db.memory_mark_canonical(agent, entity, max_strength=CANONICAL_STRENGTH)
                log.info(
                    "[DREAM] Cognitive Reflex Decay: %s/%s → canonical "
                    "(dream_count=%d, %d sor jelölve)",
                    agent, entity, new_count, affected,
                )
            else:
                log.debug("[DREAM] Dedup: %s/%s már van (dream_count=%d) — kihagyva",
                          agent, entity, new_count)
            return False

        chunk = " | ".join(m["fact"] for m in source_mems[:5] if m.get("fact"))

        embedding = None
        try:
            from axel_rem.embedder import embed
            embedding = embed(f"{entity} {fact}")
        except Exception as e:
            log.warning("[DREAM] Embedding hiba [%s/%s]: %s", agent, entity, e)

        tags: list[str] = []
        for m in source_mems:
            for t in (m.get("tags") or []):
                if t not in tags:
                    tags.append(t)
        tags = tags[:6]

        mem_id = db.longterm_insert(
            entity=entity,
            fact=fact,
            chunk=chunk,
            source_ref=source_ref,
            agent=agent,
            strength=PROMOTE_STRENGTH,
            embedding=embedding,
            tags=tags,
        )

        if entity:
            redis_mem.push_memory(
                agent=agent,
                mem_id=mem_id,
                entity=entity,
                fact=fact,
                strength=PROMOTE_STRENGTH,
            )

        log.info("[DREAM] Long-term promóció: %s/%s (importance=%d) → id=%d",
                 agent, entity, importance, mem_id)
        return True
