"""Xeto 工具 — readXeto / writeXeto / editXeto"""
import json
import logging
from app.safety.validation import precheck_xeto_syntax, format_axon_error
from app.safety.audit import log_write

logger = logging.getLogger(__name__)


def readXeto(client, name: str) -> str:
    """读取 Xeto 规范定义
    
    Args:
        client: SkySpark 客户端
        name: Xeto 类型名称（如 Ahu, Chiller, Site）
        
    Returns:
        JSON 字符串（规范内容或错误）
    """
    if not name:
        return json.dumps({"error": True, "message": "请指定 Xeto 类型名称"})
    
    try:
        result = client.eval(f'xetoLib("{name}")')
        from app.skyspark.cleaner import clean_grid
        return json.dumps(clean_grid(result), ensure_ascii=False)
    except Exception:
        # 尝试其他查询方式
        try:
            result = client.eval(f'readAll(spec and name=="{name}")')
            from app.skyspark.cleaner import clean_grid
            rows = clean_grid(result)
            return json.dumps(rows if rows else {
                "info": f"未找到类型 '{name}'",
                "hint": "常见类型: Ahu, Chiller, Site, Equip, TempPoint",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": True,
                "message": str(e)[:200],
                "hint": "请使用 helpDoc('doc.xeto/index') 查看 Xeto 文档",
            }, ensure_ascii=False)


def writeXeto(client, code: str, confirm: bool = False, mcp_name: str = "skyforge-mcp") -> str:
    """写入/更新 Xeto 规范
    
    需要 confirm=true 确认执行。
    执行前进行 Xeto 语法预检。
    
    Args:
        client: SkySpark 客户端
        code: Xeto 源码
        confirm: 确认执行
        mcp_name: MCP 名称（审计用）
        
    Returns:
        JSON 字符串（执行结果）
    """
    if not code:
        return json.dumps({"error": True, "message": "请提供 Xeto 源码"})
    
    if not confirm:
        return json.dumps({
            "confirm_required": True,
            "message": "此操作将修改 Xeto 规范。请确认后设置 confirm=true。",
            "hint": "建议先用 readXeto 查看当前定义，再修改",
        }, ensure_ascii=False)
    
    # 语法预检
    err = precheck_xeto_syntax(code)
    if err:
        return json.dumps({"error": True, "message": err})
    
    try:
        result = client.eval(f'commitWriteTrio({json.dumps(code)})')
        log_write(mcp_name, "writeXeto", code[:200], success=True)
        return json.dumps({"success": True, "message": "Xeto 规范已更新"}, ensure_ascii=False)
    except Exception as e:
        log_write(mcp_name, "writeXeto", code[:200], success=False)
        return json.dumps({
            "error": True,
            "message": format_axon_error(e),
        }, ensure_ascii=False)


def editXeto(client, old: str, new: str, confirm: bool = False, mcp_name: str = "skyforge-mcp") -> str:
    """编辑 Xeto 源码（替换模式）
    
    在现有 Xeto 源码中查找 old 字符串并替换为 new。
    
    Args:
        client: SkySpark 客户端
        old: 待替换的原文
        new: 替换后的新文
        confirm: 确认执行
        mcp_name: MCP 名称（审计用）
        
    Returns:
        JSON 字符串（执行结果）
    """
    if not old or not new:
        return json.dumps({"error": True, "message": "请提供 old（原文）和 new（新文）参数"})
    
    if not confirm:
        return json.dumps({
            "confirm_required": True,
            "message": "此操作将修改 Xeto 规范。请确认后设置 confirm=true。",
        }, ensure_ascii=False)
    
    # 对新代码做语法预检
    err = precheck_xeto_syntax(new)
    if err:
        return json.dumps({"error": True, "message": err})
    
    try:
        result = client.eval(
            f'xetoEdit({json.dumps(old)}, {json.dumps(new)})'
        )
        log_write(mcp_name, "editXeto", f"old={old[:100]} new={new[:100]}", success=True)
        from app.skyspark.cleaner import clean_grid
        return json.dumps(clean_grid(result), ensure_ascii=False)
    except Exception as e:
        log_write(mcp_name, "editXeto", f"old={old[:100]}", success=False)
        return json.dumps({
            "error": True,
            "message": format_axon_error(e),
        }, ensure_ascii=False)
