"""
Entity és tény kinyerése nyers task seed-ekből.
Olcsó LLM hívás (groq-llama) — prompt-chill futtatja aszinkron.
"""
import json
import logging

import httpx

from axel_shared import config

log = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
Elemezd a következő AI agent task leírást és összefoglalót.
Adj vissza egy JSON objektumot ezekkel a mezőkkel:
- entity: a fő téma/entitás neve (max 3 szó, pl: "zetor", "BTC", "nginx service", "paper trade")
- fact: a legalapvetőbb tény amit megtudtunk (max 150 karakter)
- tags: max 4 releváns tag lista (pl: ["infrastruktúra", "monitoring", "kereskedés"])

CSAK a JSON objektumot add vissza, semmi mást.

Task leírás: {description}

Összefoglaló: {summary}
"""


def extract_entity_fact(description: str, summary: str) -> dict:
    """
    LLM-alapú entity/fact extrakció.
    Visszaad: {entity, fact, tags} vagy fallback értékeket hiba esetén.
    """
    prompt = _EXTRACT_PROMPT.format(
        description=description[:600],
        summary=summary[:600],
    )
    try:
        r = httpx.post(
            f"{config.LLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": "groq-llama",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 200,
            },
            timeout=15,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        # JSON parse — néha backtick block között jön
        if "```" in content:
            content = content.split("```")[1].lstrip("json").strip()
        data = json.loads(content)
        return {
            "entity": str(data.get("entity", ""))[:200],
            "fact":   str(data.get("fact",   description[:150]))[:500],
            "tags":   [str(t) for t in data.get("tags", [])][:4],
        }
    except Exception as e:
        log.warning("[EXTRACT] LLM hiba: %s — fallback", e)
        return {
            "entity": "",
            "fact":   description[:150],
            "tags":   [],
        }
