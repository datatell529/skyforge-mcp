"""语法预检与错误格式化 — 写操作防爆机制"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def precheck_axon_syntax(expr: str) -> Optional[str]:
    """Axon 表达式语法预检：括号匹配 + 引号匹配"""
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(expr):
        if ch in pairs:
            stack.append((ch, i))
        elif ch in pairs.values():
            if not stack:
                return f"第 {i+1} 字符: 多余的 '{ch}'"
            if pairs[stack[-1][0]] != ch:
                expected = pairs[stack[-1][0]]
                return f"第 {i+1} 字符: 期望 '{expected}' 但收到 '{ch}'"
            stack.pop()
    if stack:
        missing = pairs[stack[-1][0]]
        return f"括号不匹配: 缺少 '{missing}'"
    if not expr.strip():
        return "表达式不能为空"
    return None

def precheck_xeto_syntax(code: str) -> Optional[str]:
    """Xeto 源码语法预检"""
    if code.count("{") != code.count("}"):
        return "Xeto 花括号不匹配"
    if code.count("(") != code.count(")"):
        return "Xeto 括号不匹配"
    if not re.search(r'^\s*\w+\s*:', code, re.MULTILINE):
        return "Xeto 缺少类型定义 (格式: TypeName : SuperType { ... })"
    forbidden = ["sys::", "fan::"]
    for kw in forbidden:
        if kw in code:
            return f"Xeto 包含不允许的引用: {kw}"
    return None

def format_axon_error(error: Exception) -> str:
    """格式化 Axon 错误为 AI 可读信息"""
    error_str = str(error)
    if "TypeMismatch" in error_str:
        return f"类型不匹配: {_truncate(error_str)}"
    if "UnknownName" in error_str:
        return f"未知名称: {_truncate(error_str)}"
    if "UnknownRec" in error_str:
        return f"未知记录: {_truncate(error_str)}"
    if "SyntaxErr" in error_str or "syntax" in error_str.lower():
        return f"语法错误: {_truncate(error_str)}"
    if "timeout" in error_str.lower():
        return "执行超时 (30s)。请简化查询或分批执行。"
    if "AuthError" in error_str or "auth" in error_str.lower():
        return f"认证失败: {_truncate(error_str)}"
    if "permission" in error_str.lower() or "denied" in error_str.lower():
        return f"权限不足: {_truncate(error_str)}"
    return f"Axon 执行错误: {_truncate(error_str)}"

def _truncate(text: str, max_len: int = 200) -> str:
    text = text.strip()
    return text[:max_len] + "..." if len(text) > max_len else text
