"""Skill YAML 加载器 — 解析 YAML + include 链递归 + 循环检测"""
import os
import yaml
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_INCLUDE_DEPTH = 5

@dataclass
class SkillDef:
    name: str
    doc: str = ""
    keywords: list = field(default_factory=list)
    include: list = field(default_factory=list)
    suggested_group: Optional[str] = None
    prompt: str = ""
    source: str = "yaml"


class SkillLoader:
    """从 YAML 文件加载 Skill 定义"""
    
    def __init__(self, builtins_dir: str):
        self.builtins_dir = builtins_dir
        self._skills: dict[str, SkillDef] = {}
    
    def load_all(self) -> dict[str, SkillDef]:
        """加载 builtins 目录下所有 Skill"""
        index_path = os.path.join(self.builtins_dir, "_index.yaml")
        if not os.path.exists(index_path):
            logger.warning(f"Skill index not found: {index_path}")
            return self._skills
        
        with open(index_path, 'r') as f:
            index = yaml.safe_load(f)
        
        for name, meta in index.get("skills", {}).items():
            file_path = os.path.join(self.builtins_dir, meta["file"])
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    data = yaml.safe_load(f)
                self._skills[name] = SkillDef(
                    name=data.get("name", name),
                    doc=data.get("doc", ""),
                    keywords=data.get("keywords", []),
                    include=data.get("include", []),
                    suggested_group=data.get("suggested_group"),
                    prompt=data.get("prompt", ""),
                    source="yaml",
                )
                logger.debug(f"Loaded skill: {name}")
            else:
                logger.warning(f"Skill file not found: {file_path}")
        
        logger.info(f"Loaded {len(self._skills)} skills from {self.builtins_dir}")
        return self._skills
    
    def resolve(self, name: str, depth: int = 0, visited: set = None) -> str:
        """递归解析 Skill + include 链，返回完整 prompt
        
        Args:
            name: Skill 名称
            depth: 当前递归深度
            visited: 已访问的 Skill 名称（循环检测）
            
        Returns:
            组合后的 Markdown 字符串
        """
        if visited is None:
            visited = set()
        
        if depth > MAX_INCLUDE_DEPTH:
            logger.warning(f"Skill '{name}' include 深度超限 ({MAX_INCLUDE_DEPTH})")
            return f"<!-- Skill '{name}' 达到最大 include 深度 -->"
        
        if name in visited:
            logger.warning(f"Skill '{name}' 循环引用 detected: {visited}")
            return f"<!-- Skill '{name}' 循环引用已跳过 -->"
        
        skill = self._skills.get(name)
        if not skill:
            logger.warning(f"Skill '{name}' 未找到")
            return f"<!-- Skill '{name}' 未找到 -->"
        
        visited = visited | {name}
        parts = [skill.prompt]
        
        for inc_name in skill.include:
            child_prompt = self.resolve(inc_name, depth + 1, visited)
            if child_prompt and not child_prompt.startswith("<!--"):
                parts.append(f"<!-- from {inc_name} -->\n{child_prompt}")
        
        return "\n\n---\n\n".join(filter(None, parts))
