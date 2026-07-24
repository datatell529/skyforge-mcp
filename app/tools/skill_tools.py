"""Skill 管理工具 — skillList / skillUse"""
import json
import logging

logger = logging.getLogger(__name__)

# 全局当前激活的 Skill 列表（由 main.py 管理）
_active_skills: list[str] = []


def set_active_skills(skills: list[str]):
    """设置当前激活的 Skill 列表（由 main.py 调用）"""
    global _active_skills
    _active_skills = skills


def get_active_skills() -> list[str]:
    """获取当前激活的 Skill 列表"""
    global _active_skills
    return _active_skills


def skillList(skill_registry) -> str:
    """列出所有可用的 Skill
    
    Args:
        skill_registry: Skill 注册表
        
    Returns:
        JSON 字符串（Skill 列表 + 当前激活的）
    """
    if not skill_registry:
        return json.dumps({"error": True, "message": "Skill 系统未启用"})
    
    skills = [
        {
            "name": s.name,
            "doc": s.doc,
            "keywords": s.keywords,
            "suggested_group": s.suggested_group,
        }
        for s in skill_registry.all()
    ]
    
    return json.dumps({
        "skills": skills,
        "active": _active_skills,
        "hint": "使用 skillUse(name) 激活 Skill。Skill 激活后其知识会注入到 System Prompt 中。",
    }, ensure_ascii=False)


def skillUse(skill_registry, name: str) -> str:
    """激活一个 Skill
    
    Skill 激活后，其 Markdown 知识会注入到后续对话的 System Prompt 中。
    使用 skillUse("none") 清除所有激活的 Skill。
    
    Args:
        skill_registry: Skill 注册表
        name: Skill 名称，或 "none"（清除所有）
        
    Returns:
        JSON 字符串（操作结果）
    """
    global _active_skills
    
    if name == "none" or name == "clear":
        _active_skills = []
        return json.dumps({"success": True, "message": "已清除所有 Skill"}, ensure_ascii=False)
    
    if not skill_registry:
        return json.dumps({"error": True, "message": "Skill 系统未启用"})
    
    skill = skill_registry.get(name)
    if not skill:
        available = skill_registry.names()
        return json.dumps({
            "error": True,
            "message": f"未找到 Skill '{name}'",
            "available": available,
        })
    
    if name not in _active_skills:
        _active_skills.append(name)
    
    return json.dumps({
        "success": True,
        "message": f"✅ Skill '{name}' 已激活",
        "active": _active_skills,
        "hint": "使用 skillList 查看当前激活的 Skill，使用 skillUse('none') 清除",
    }, ensure_ascii=False)
