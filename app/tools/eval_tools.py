"""evalAxon / evalAxonWrite — 通用 Axon 执行工具"""
import json
import logging
from typing import Optional
from app.skyspark.cleaner import clean_grid
from app.safety.validation import precheck_axon_syntax, format_axon_error
from app.safety.audit import log_write

logger = logging.getLogger(__name__)

# 写操作关键词黑名单（用于 evalAxon 只读检测）
WRITE_KEYWORDS = [
    "commit", "commitAdd", "commitUpdate", "commitRemove", "ioWriteTrio",
    "ioWriteZinc", "purge", "purgeAll", "install", "uninstall",
    "sys::Pod", "sys::Type", "delete", "remove",
]


def evalAxon(client, expr: str) -> str:
    """执行只读 Axon 查询
    
    自动检测并拒绝包含写操作关键字的表达式。
    返回值经过 Grid 清洗，去除 Haystack 类型元数据。
    
    Args:
        client: SkySpark 客户端实例
        expr: Axon 表达式
        
    Returns:
        JSON 字符串（清洗后的数据或错误信息）
    """
    # 安全检查：拒绝写操作
    for kw in WRITE_KEYWORDS:
        if kw in expr:
            return json.dumps({
                "error": True,
                "type": "write_detected",
                "message": f"表达式包含写操作关键词 '{kw}'。如需写操作，请使用 evalAxonWrite 工具并设置 confirm=true",
            }, ensure_ascii=False)
    
    try:
        result = client.eval(expr)
        cleaned = clean_grid(result)
        return json.dumps(cleaned, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": True,
            "type": "execution_error",
            "message": format_axon_error(e),
        }, ensure_ascii=False)


def evalAxonWrite(client, expr: str, confirm: bool = False, mcp_name: str = "skyforge-mcp") -> str:
    """执行可能包含写操作的 Axon 表达式
    
    需要 confirm=true 确认执行。
    执行前进行语法预检，注入超时保护，记录审计日志。
    
    Args:
        client: SkySpark 客户端实例
        expr: Axon 表达式
        confirm: 确认执行
        mcp_name: MCP 名称（审计用）
        
    Returns:
        JSON 字符串（执行结果或确认提示）
    """
    if not confirm:
        return json.dumps({
            "confirm_required": True,
            "message": "此操作可能修改数据。请确认后设置 confirm=true 重新调用。",
            "hint": "示例: evalAxonWrite(expr=\"...\", confirm=true)",
        }, ensure_ascii=False)
    
    # 语法预检
    syntax_err = precheck_axon_syntax(expr)
    if syntax_err:
        return json.dumps({
            "error": True,
            "type": "syntax_error",
            "message": syntax_err,
        }, ensure_ascii=False)
    
    # 注入超时保护（30 秒）
    safe_expr = f"timeout(30s, {{ {expr} }})"
    
    try:
        result = client.eval(safe_expr)
        # 审计日志
        log_write(mcp_name, "evalAxonWrite", expr, success=True)
        cleaned = clean_grid(result)
        return json.dumps(cleaned, ensure_ascii=False)
    except Exception as e:
        log_write(mcp_name, "evalAxonWrite", expr, success=False)
        return json.dumps({
            "error": True,
            "type": "execution_error",
            "message": format_axon_error(e),
            "hint": "请检查 Axon 语法后重试，或简化查询",
        }, ensure_ascii=False)
