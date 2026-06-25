"""
Dream agent — éjjeli REM feldolgozás.
Fut: minden nap 03:00-kor.
Feladata:
  1. Napi memóriák összegyűjtése agent-enként
  2. LLM-alapú konszolidáció (duplikátok, minták)
  3. Strength decay
  4. Fontos memóriák boost-ja
  5. Long-term promóció — konszolidált új sor + embedding
"""
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from axel_shared import config
from axel_rem import db
from axel_rem import redis_mem

log = logging.getLogger(__name__)

AGENTS = ["AXEL", "FORGE", "ATLAS"]
DREAM_HOUR = 3  # 03:00 UTC
PROMOTE_MIN_IMPORTANCE = 6   # LLM importance >= ennyi → long-term promóció
PROMOTE_STRENGTH = 2.5       # promótált sor induló strength-je
PROMOTE_DEDUP_HOURS = 20     # ennyi órán belüli dream-sor ugyanarra az entity-re nem duplikálódik


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
        memories = db.memory_get_recent(agent=agent, hours=26, limit=80)
        raw_tasks = db.tasks_get_recent(agent=agent, hours=26, limit=60)

        # Task ID-k amihez már van memória sor — ne duplikáljuk
        seen_refs = {m.get("source_ref") for m in memories}

        # Konvertál task sorokat pseudo-memória dikt-té, ha még nincs feldolgozva
        appended = 0
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
                appended += 1

        if not memories:
            return 0

        log.info("[DREAM] %s: %d elem konszolidáció (%d memória + %d nyers task)",
                 agent, len(memories), len(memories) - appended, appended)

        by_entity: dict[str, list[dict]] = {}
        for m in memories:
            e = m.get("entity") or "általános"
            by_entity.setdefault(e, []).append(m)

        promoted = 0
        for entity, mems in by_entity.items():
            if len(mems) < 2:
                continue
            if self._process_cluster(agent, entity, mems):
                promoted += 1
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
                    "model": "groq-llama",
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
                        if m.get("id"):  # nyers task soroknak nincs id-juk
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
        """
        Long-term memória sor létrehozása.
        Visszaad False-t ha már van friss dream-sor erre az entity-re.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        source_ref = f"dream://{today}/{entity.lower().replace(' ', '_')}"

        # Dedup: ha már van friss dream-sor erre az entity+agent párra, kihagyjuk
        if db.dream_memory_exists(agent, entity, hours=PROMOTE_DEDUP_HOURS):
            log.debug("[DREAM] Dedup: %s/%s már van — kihagyva", agent, entity)
            return False

        # Chunk: az összes eredeti fact összefűzve kontextusnak
        chunk = " | ".join(m["fact"] for m in source_mems[:5] if m.get("fact"))

        # Embedding generálás
        embedding = None
        try:
            from axel_rem.embedder import embed
            embedding = embed(f"{entity} {fact}")
        except Exception as e:
            log.warning("[DREAM] Embedding hiba [%s/%s]: %s", agent, entity, e)

        # Tags az eredeti memóriákból összegyűjtve
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

        # Redis frissítés
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
