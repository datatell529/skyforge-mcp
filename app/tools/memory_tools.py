"""记忆工具 — readMemory / appendMemory"""
import json
import logging

logger = logging.getLogger(__name__)


def readMemory(memory_store) -> str:
    """读取项目记忆
    
    Args:
        memory_store: MemoryStore 实例
        
    Returns:
        JSON 字符串（记忆内容）
    """
    if not memory_store:
        return json.dumps({"memory": "", "info": "记忆系统未启用"})
    
    content = memory_store.read()
    if not content:
        return json.dumps({
            "memory": "",
            "info": "暂无项目记忆。使用 appendMemory 添加。",
        })
    
    return json.dumps({
        "memory": content,
        "hint": "项目记忆会在每次对话中自动注入到 System Prompt",
    }, ensure_ascii=False)


def appendMemory(memory_store, text: str) -> str:
    """追加项目记忆
    
    新增的记忆会在后续所有对话中自动注入。
    建议每条记忆 1-2 句话，多段落内容建议创建 Skill。
    
    Args:
        memory_store: MemoryStore 实例
        text: 要追加的文本
        
    Returns:
        JSON 字符串（操作结果）
    """
    if not text:
        return json.dumps({"error": True, "message": "请提供要追加的记忆内容"})
    
    if not memory_store:
        return json.dumps({"error": True, "message": "记忆系统未启用"})
    
    result = memory_store.append(text)
    return json.dumps({"success": True, "message": result}, ensure_ascii=False)
