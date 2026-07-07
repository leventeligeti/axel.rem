"""REM agent — standalone memory inspector and maintenance worker."""
import logging
import time

from axel_rem.tools import TOOL_REGISTRY, TOOL_DEFINITIONS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are REM — the memory system custodian.

## Role
- Inspect and maintain the rem_memory table
- Run semantic searches across stored memories
- Report on memory system health
- Consolidate memories on request

## Tools
{tools}
"""


class RemAgent:
    NAME = "REM"

    def get_tools(self) -> list[dict]:
        return TOOL_DEFINITIONS

    def get_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def execute_tool(self, tool_name: str, args: dict) -> dict:
        if tool_name not in TOOL_REGISTRY:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            fn = TOOL_REGISTRY[tool_name]
            return fn(**args) if args else fn()
        except Exception as e:
            log.error("[REM] Tool error [%s]: %s", tool_name, e)
            return {"error": str(e)}

    def run_worker(self, poll_interval: float = 15.0) -> None:
        """Simple polling worker — override for custom scheduling."""
        log.info("[REM] Worker started, poll_interval=%.1fs", poll_interval)
        while True:
            time.sleep(poll_interval)
