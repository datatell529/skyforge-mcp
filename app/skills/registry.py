"""Skill 注册表 — 注册、搜索、组合"""
import logging
from typing import Optional
from .loader import SkillDef, SkillLoader

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Skill 注册表，管理所有可用 Skill 的查询和组合"""
    
    def __init__(self, loader: SkillLoader):
        self._loader = loader
        self._skills: dict[str, SkillDef] = {}
    
    def load(self):
        """加载所有 Skill"""
        self._skills = self._loader.load_all()
        return self
    
    def get(self, name: str) -> Optional[SkillDef]:
        return self._skills.get(name)
    
    def resolve(self, name: str) -> str:
        """解析 Skill + include 链，返回完整 prompt"""
        return self._loader.resolve(name)
    
    def search(self, query: str) -> list[SkillDef]:
        """按 keywords/doc/name 搜索 Skill"""
        q = query.lower()
        results = []
        for skill in self._skills.values():
            if any(kw in q or q in kw for kw in skill.keywords):
                results.append(skill)
            elif q in skill.name.lower() or q in skill.doc.lower():
                results.append(skill)
        return results
    
    def match_user_input(self, text: str) -> list[str]:
        """根据用户输入的关键词自动匹配 Skill 名称列表"""
        from .matcher import SkillMatcher
        return SkillMatcher.match(text, list(self._skills.keys()))
    
    def all(self) -> list[SkillDef]:
        return list(self._skills.values())
    
    def names(self) -> list[str]:
        return list(self._skills.keys())
