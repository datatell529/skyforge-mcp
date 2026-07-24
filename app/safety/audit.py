"""审计日志 — 记录所有写入操作"""
import logging
import json
from datetime import datetime

audit_logger = logging.getLogger("mcp.audit")
_handler = logging.FileHandler("/var/log/skyforge-mcp/audit.log")
_handler.setFormatter(logging.Formatter("%(asctime)s [AUDIT] %(message)s"))
audit_logger.addHandler(_handler)
audit_logger.setLevel(logging.INFO)

def log_write(mcp_name: str, tool: str, expr: str, user: str = "ai_agent", success: bool = True):
    """记录写入操作审计日志"""
    audit_logger.info(json.dumps({
        "mcp": mcp_name, "tool": tool,
        "expr": expr[:500], "user": user,
        "timestamp": datetime.now().isoformat(),
        "result": "success" if success else "failed",
    }, ensure_ascii=False))
