"""FIN Skill 搜索工具 — finSkillSearch"""
import json
import logging

logger = logging.getLogger(__name__)


def finSkillSearch(skill_registry, query: str) -> str:
    """搜索 FIN 领域的 Skill
    
    Args:
        skill_registry: Skill 注册表
        query: 搜索关键词
        
    Returns:
        JSON 字符串（匹配的 Skill 列表）
    """
    if not query or not skill_registry:
        return json.dumps({"query": query, "count": 0, "results": []})
    
    results = skill_registry.search(query)
    return json.dumps({
        "query": query,
        "count": len(results),
        "results": [
            {
                "name": s.name,
                "doc": s.doc,
                "keywords": s.keywords,
                "suggested_group": s.suggested_group,
            }
            for s in results
        ],
        "hint": "使用 skillUse(name) 激活 Skill。部分 Skill 会自动推荐对应的工具组。",
    }, ensure_ascii=False)
