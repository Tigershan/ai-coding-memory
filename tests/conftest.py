"""pytest 配置：把项目根加进 sys.path，让 tests 直接 from core import ..."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# mcp-server 子目录也加上，便于 import lib.*
_MCP_SERVER = _PROJECT_ROOT / "mcp-server"
if str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))
