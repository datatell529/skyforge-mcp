"""帮助工具 — helpFunc / helpSkill / helpDoc"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def helpFunc(client, name: str) -> str:
    """查看 Axon 函数的签名和参数说明
    
    Args:
        client: SkySpark 客户端
        name: 函数名称
        
    Returns:
        JSON 字符串（函数信息或错误）
    """
    if not name:
        return json.dumps({"error": True, "message": "请指定函数名称"})
    
    try:
        # 从 SkySpark 查询函数定义
        result = client.eval(f"readAll(func and name==\"{name}\")")
        rows = result.rows if hasattr(result, 'rows') else []
        
        if not rows:
            return json.dumps({
                "error": True,
                "message": f"未找到函数 '{name}'。可用函数: about, readAll, readById, hisRead, commit 等",
            })
        
        func_info = dict(rows[0])
        return json.dumps({
            "name": name,
            "doc": str(func_info.get("doc", "")),
            "params": str(func_info.get("params", "{}")),
            "src": str(func_info.get("src", "")),
        }, ensure_ascii=False)
    except Exception as e:
        # 兜底：返回基础信息
        return json.dumps({
            "name": name,
            "hint": f"函数 '{name}' 的信息暂不可用。可直接用 evalAxon 尝试调用。",
        }, ensure_ascii=False)


def helpSkill(skill_registry, name: str) -> str:
    """查看 Skill 的详细内容
    
    Args:
        skill_registry: Skill 注册表
        name: Skill 名称
        
    Returns:
        JSON 字符串（Skill 详情或错误）
    """
    if not name:
        return json.dumps({"error": True, "message": "请指定 Skill 名称"})
    
    skill = skill_registry.get(name) if skill_registry else None
    if not skill:
        available = skill_registry.names() if skill_registry else []
        return json.dumps({
            "error": True,
            "message": f"未找到 Skill '{name}'",
            "available": available,
        })
    
    return json.dumps({
        "name": skill.name,
        "doc": skill.doc,
        "keywords": skill.keywords,
        "include": skill.include,
        "suggested_group": skill.suggested_group,
        "prompt": skill.prompt[:500] + ("..." if len(skill.prompt) > 500 else ""),
    }, ensure_ascii=False)


def helpDoc(client, uri: str) -> str:
    """查看 SkySpark 文档页内容
    
    Args:
        client: SkySpark 客户端
        uri: 文档 URI（如 "doc/hx.ai/index"）
        
    Returns:
        JSON 字符串（文档内容或错误）
    """
    if not uri:
        return json.dumps({
            "error": True,
            "message": "请指定文档 URI",
            "hint": "例如: doc/hx.ai/index, doc/axon/Funcs",
        })
    
    try:
        result = client.eval(f"helpDoc({uri})")
        from app.skyspark.cleaner import clean_grid
        return json.dumps(clean_grid(result), ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": f"文档 '{uri}' 不可用: {str(e)[:200]}",
        }, ensure_ascii=False)
