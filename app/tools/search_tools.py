"""搜索工具 — searchFuncs / searchSkills / searchDocs"""
import json
import logging

logger = logging.getLogger(__name__)


def searchFuncs(client, query: str) -> str:
    """按关键词搜索可用的 Axon 函数
    
    从 SkySpark 中搜索匹配的 func 记录。
    
    Args:
        client: SkySpark 客户端
        query: 搜索关键词
        
    Returns:
        JSON 字符串（匹配的函数列表）
    """
    if not query:
        return json.dumps({"error": True, "message": "请指定搜索关键词"})
    
    try:
        result = client.eval(f'readAll(func and name->"{query}")')
        from app.skyspark.cleaner import clean_grid
        rows = clean_grid(result)
        
        if not rows:
            # 放宽搜索：匹配 doc 中包含关键词的
            result = client.eval(f'readAll(func)')
            all_funcs = clean_grid(result)
            q = query.lower()
            rows = [f for f in all_funcs 
                    if q in str(f.get("name", "")).lower() 
                    or q in str(f.get("doc", "")).lower()]
        
        # 只返回关键信息
        summary = []
        for r in rows[:30]:  # 最多返回 30 条
            summary.append({
                "name": r.get("name", ""),
                "doc": str(r.get("doc", ""))[:100],
            })
        
        return json.dumps({
            "query": query,
            "count": len(summary),
            "results": summary,
            "hint": "使用 helpFunc(name) 查看函数详情" if summary else "未找到匹配函数",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": f"搜索失败: {str(e)[:200]}",
            "fallback": "可尝试直接使用 evalAxon 执行查询",
        }, ensure_ascii=False)


def searchSkills(skill_registry, query: str) -> str:
    """按关键词搜索 Skill
    
    Args:
        skill_registry: Skill 注册表
        query: 搜索关键词
        
    Returns:
        JSON 字符串（匹配的 Skill 列表）
    """
    if not query or not skill_registry:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": [],
        })
    
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
        "hint": "使用 skillUse(name) 激活 Skill，使用 helpSkill(name) 查看详情",
    }, ensure_ascii=False)


def searchDocs(client, query: str) -> str:
    """搜索 SkySpark 文档页
    
    Args:
        client: SkySpark 客户端
        query: 搜索关键词
        
    Returns:
        JSON 字符串（匹配的文档列表）
    """
    if not query:
        return json.dumps({"error": True, "message": "请指定搜索关键词"})
    
    try:
        result = client.eval(f'searchDocs("{query}")')
        from app.skyspark.cleaner import clean_grid
        rows = clean_grid(result)
        return json.dumps({
            "query": query,
            "count": len(rows),
            "results": rows[:20],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": f"文档搜索失败: {str(e)[:200]}",
            "hint": "可直接使用 helpDoc(uri) 查看已知文档",
        }, ensure_ascii=False)
