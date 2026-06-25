"""REM agent — task worker: memory rendszer inspekció és karbantartás."""
import logging

from axel_shared.agent_base import AgentBase
from axel_rem.tools import TOOL_REGISTRY, TOOL_DEFINITIONS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Te REM vagy — az Axel emlékezeti rendszerének gondnoka.

## Szereped
- Felügyeled és karbantartod az axel_rem_memory táblát
- Szemantikus kereséseket futtatsz a memóriában
- Riportot készítesz a memória állapotáról
- Konzolid memóriákat kérés esetén

## Eszközeid
{tools}
"""


class RemAgent(AgentBase):
    NAME = "REM"

    def get_tools(self) -> list[dict]:
        return TOOL_DEFINITIONS

    def get_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def execute_tool(self, tool_name: str, args: dict) -> dict:
        if tool_name not in TOOL_REGISTRY:
            return {"error": f"Ismeretlen tool: {tool_name}"}
        try:
            fn = TOOL_REGISTRY[tool_name]
            return fn(**args) if args else fn()
        except Exception as e:
            log.error("[REM] Tool hiba [%s]: %s", tool_name, e)
            return {"error": str(e)}
