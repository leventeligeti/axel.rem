"""
Dream agent — éjjeli REM feldolgozás.
Fut: minden nap 03:00-kor.
Feladata:
  1. Napi memóriák összegyűjtése agent-enként
  2. LLM-alapú konszolidáció (duplikátok, minták)
  3. Strength decay
  4. Fontos memóriák kiemelése
"""
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from axel_shared import config
from axel_rem import db

log = logging.getLogger(__name__)

AGENTS = ["AXEL", "FORGE", "ATLAS"]
DREAM_HOUR = 3  # 03:00 UTC


class DreamScheduler:

    def run(self):
        log.info("[DREAM] Scheduler indul — fut: minden nap %02d:00 UTC", DREAM_HOUR)
        while True:
            now = datetime.now(timezone.utc)
            if now.hour == DREAM_HOUR and now.minute < 5:
                self._run_dream_cycle()
                time.sleep(300)  # 5 perc szünet hogy ne fusson kétszer
            else:
                time.sleep(60)

    def _run_dream_cycle(self):
        log.info("[DREAM] Éjjeli ciklus indul — %s", datetime.now(timezone.utc).isoformat())

        # 1. Decay
        decayed = db.memory_decay_all(factor=0.98)
        log.info("[DREAM] Decay: %d memória halványítva", decayed)

        # 2. Agent-enkénti konszolidáció
        for agent in AGENTS:
            try:
                self._consolidate_agent(agent)
            except Exception as e:
                log.warning("[DREAM] %s konszolidáció hiba: %s", agent, e)

        log.info("[DREAM] Ciklus kész")

    def _consolidate_agent(self, agent: str):
        memories = db.memory_get_recent(agent=agent, hours=26, limit=80)
        if not memories:
            return

        log.info("[DREAM] %s: %d memória konszolidáció", agent, len(memories))

        # Entitások csoportosítása
        by_entity: dict[str, list[dict]] = {}
        for m in memories:
            e = m.get("entity") or "általános"
            by_entity.setdefault(e, []).append(m)

        # Minden entity cluster LLM-feldolgozása
        for entity, mems in by_entity.items():
            if len(mems) < 2:
                continue
            self._process_cluster(agent, entity, mems)

    def _process_cluster(self, agent: str, entity: str, mems: list[dict]):
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

            for item in consolidated:
                importance = int(item.get("importance", 5))
                # Boost a legfontosabb eredeti memóriáknak
                if importance >= 7:
                    for m in mems[:3]:
                        db.memory_boost(m["id"], delta=float(importance) * 0.05)

            log.debug("[DREAM] %s/%s: %d konszolidált tény", agent, entity, len(consolidated))

        except Exception as e:
            log.warning("[DREAM] Cluster hiba [%s/%s]: %s", agent, entity, e)
