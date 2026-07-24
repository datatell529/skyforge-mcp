"""FIN 记忆工具 — finMemory / finMemoryAppend"""
import json
import logging

logger = logging.getLogger(__name__)


def finMemory(memory_store) -> str:
    """读取 FIN 项目记忆"""
    if not memory_store:
        return json.dumps({"memory": "", "info": "记忆系统未启用"})
    content = memory_store.read()
    if not content:
        return json.dumps({"memory": "", "info": "暂无 FIN 项目记忆。使用 finMemoryAppend 添加。"})
    return json.dumps({
        "memory": content,
        "hint": "FIN 项目记忆会在每次对话中自动注入 System Prompt。",
    }, ensure_ascii=False)


def finMemoryAppend(memory_store, text: str) -> str:
    """追加 FIN 项目记忆"""
    if not text:
        return json.dumps({"error": True, "message": "请提供要追加的记忆内容"})
    if not memory_store:
        return json.dumps({"error": True, "message": "记忆系统未启用"})
    result = memory_store.append(text)
    return json.dumps({"success": True, "message": result}, ensure_ascii=False)
