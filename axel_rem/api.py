"""REM HTTP API — /api/signal és /api/recall végpontok."""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from axel_rem import db, redis_mem

log = logging.getLogger(__name__)

REM_API_PORT = 4101

# Prompt-chill figyeli ezt — signal esetén azonnal felébred
_signal_event = threading.Event()


def get_signal_event() -> threading.Event:
    return _signal_event


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # httpd noise elnyomása

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return json.loads(body) if body else {}

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        if self.path == "/api/signal":
            agent = body.get("agent", "AXEL").upper()
            log.info("[REM-API] Signal érkezett: agent=%s", agent)
            _signal_event.set()
            self._send_json(202, {"status": "queued", "agent": agent})

        elif self.path == "/api/recall":
            agent = body.get("agent", "AXEL").upper()
            topic = body.get("topic", "")
            log.info("[REM-API] Recall: agent=%s topic=%s", agent, topic[:60])
            memories = _handle_recall(agent, topic)
            self._send_json(200, {"loaded": len(memories), "memories": memories})

        else:
            self._send_json(404, {"error": "not found"})


def _handle_recall(agent: str, topic: str) -> list[dict]:
    """Szemantikus keresés + Redis frissítés adott topic-hoz."""
    results = []
    try:
        from axel_rem.embedder import embed
        embedding = embed(topic)
        results = db.memory_search_vector(embedding, agent=agent, k=8)
    except Exception as e:
        log.warning("[REM-API] Embedding hiba, ILIKE fallback: %s", e)
        results = db.memory_get_by_entity(topic, agent=agent, limit=8)

    memories = []
    for r in results:
        entity = r.get("entity") or ""
        fact = r.get("fact") or ""
        if entity and fact:
            redis_mem.push_memory(
                agent=agent,
                mem_id=r["id"],
                entity=entity,
                fact=fact,
                strength=r.get("strength", 1.0),
            )
            memories.append({"entity": entity, "fact": fact})

    log.info("[REM-API] Recall kész: %d memória Redis-be [%s / %s]",
             len(memories), agent, topic[:40])
    return memories


def start_api_server() -> HTTPServer:
    server = HTTPServer(("0.0.0.0", REM_API_PORT), _Handler)
    log.info("[REM-API] Indul — port %d", REM_API_PORT)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="rem-api")
    t.start()
    return server
