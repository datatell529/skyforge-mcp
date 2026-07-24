"""FIN Eval 工具 — finEval / finEvalWrite（前缀白名单限制）

FIN MCP 的 eval 只允许调用特定命名空间的函数：
- finMcp* (finCopilot 函数)
- cm* (coolMatrix 函数)
- chillerOpt* (冷机优化函数)
"""

import json
import logging
from app.skyspark.cleaner import clean_grid
from app.safety.validation import precheck_axon_syntax, format_axon_error
from app.safety.audit import log_write

logger = logging.getLogger(__name__)

# 写操作关键词黑名单（用于 finEval 只读检测）
WRITE_KEYWORDS = ["commit", "commitAdd", "commitUpdate", "commitRemove",
                  "ioWriteTrio", "ioWriteZinc", "purge", "install", "uninstall"]

# FIN 允许调用的函数前缀白名单
ALLOWED_PREFIXES = ("finMcp", "cm", "chillerOpt", "read", "readAll", "readById",
                    "hisRead", "about", "defs")


def _check_allowed(expr: str) -> tuple[bool, str]:
    """检查表达式是否只调用了允许的函数"""
    import re
    # 提取所有函数调用
    funcs = re.findall(r'\b([a-zA-Z_]\w*)\s*\(', expr)
    for func in funcs:
        if func in ("call", "timeout"):  # 允许包装函数
            continue
        if not any(func.startswith(p) for p in ALLOWED_PREFIXES):
            return False, f"函数 '{func}' 不在 FIN 允许范围内"
    return True, ""


def finEval(client, expr: str) -> str:
    """在 FIN 项目范围内执行只读 Axon 查询
    
    只允许调用 finMcp* / cm* / chillerOpt* 等白名单函数。
    拒绝写操作。返回值经 Grid 清洗。
    """
    # 安全检查：写操作
    for kw in WRITE_KEYWORDS:
        if kw in expr:
            return json.dumps({
                "error": True,
                "message": f"表达式包含写操作关键词 '{kw}'。如需写入请用 finEvalWrite 工具。"
            }, ensure_ascii=False)
    
    # 白名单检查
    allowed, msg = _check_allowed(expr)
    if not allowed:
        return json.dumps({
            "error": True,
            "message": msg,
            "allowed_prefixes": list(ALLOWED_PREFIXES),
            "hint": "FIN eval 只允许调用 finMcp*/cm*/chillerOpt* 函数。如需执行其他函数请用 evalAxon。"
        }, ensure_ascii=False)
    
    try:
        result = client.eval(expr)
        return json.dumps(clean_grid(result), ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": format_axon_error(e),
        }, ensure_ascii=False)


def finEvalWrite(client, expr: str, confirm: bool = False) -> str:
    """在 FIN 项目范围内执行写入操作
    
    需要 confirm=true 确认。语法预检 + 超时保护 + 审计日志。
    """
    if not confirm:
        return json.dumps({
            "confirm_required": True,
            "message": "此操作将修改 FIN 项目数据。请确认后设置 confirm=true。",
        }, ensure_ascii=False)
    
    # 语法预检
    err = precheck_axon_syntax(expr)
    if err:
        return json.dumps({"error": True, "message": err}, ensure_ascii=False)
    
    # 注入超时
    safe_expr = f"timeout(30s, {{ {expr} }})"
    
    try:
        result = client.eval(safe_expr)
        log_write("mcp-fin", "finEvalWrite", expr[:200], success=True)
        return json.dumps(clean_grid(result), ensure_ascii=False)
    except Exception as e:
        log_write("mcp-fin", "finEvalWrite", expr[:200], success=False)
        return json.dumps({
            "error": True,
            "message": format_axon_error(e),
        }, ensure_ascii=False)
