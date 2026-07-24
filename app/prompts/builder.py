"""System Prompt 组装器 — 4 层分层结构

组合方式:
    Layer 1: Soul（角色定义）
    Layer 2: Memory（项目记忆）
    Layer 3: Skills（按需领域知识）
    Layer 4: Tools（可用工具描述 — 由 MCP 框架自动添加）

用法:
    from app.prompts.builder import PromptBuilder
    
    builder = PromptBuilder(skill_registry, memory_store)
    prompt = builder.build(
        soul="你是一名暖通空调诊断专家",
        memory=True,           # 是否注入项目记忆
        skills=["hvac-diagnosis"],  # 激活的 Skill
    )
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PromptBuilder:
    """4 层 System Prompt 组装器"""
    
    def __init__(self, skill_registry=None, memory_store=None):
        self.skill_registry = skill_registry
        self.memory_store = memory_store
    
    def build(
        self,
        soul: str = "",
        memory: bool = True,
        skills: list[str] = None,
        auto_match_input: str = None,
    ) -> str:
        """组装完整 System Prompt
        
        Args:
            soul: Layer 1 角色定义
            memory: 是否注入 Layer 2 项目记忆
            skills: Layer 3 手动激活的 Skill 名称列表
            auto_match_input: 自动匹配用的用户输入文本
            
        Returns:
            组装好的 System Prompt 字符串
        """
        layers = []
        
        # Layer 1: Soul
        if soul:
            layers.append(f"## 角色定义\n{soul}")
        
        # Layer 2: Memory
        if memory and self.memory_store:
            mem_text = self.memory_store.read()
            if mem_text:
                layers.append(f"## 项目记忆\n{mem_text}")
        
        # Layer 3: Skills
        skill_texts = []
        activated = set(skills or [])
        
        # 自动匹配
        if auto_match_input and self.skill_registry:
            auto = self.skill_registry.match_user_input(auto_match_input)
            activated.update(auto)
        
        # 解析 Skill
        if activated and self.skill_registry:
            for name in activated:
                try:
                    text = self.skill_registry.resolve(name)
                    if text and not text.startswith("<!--"):
                        skill_texts.append(f"<!-- skill: {name} -->\n{text}")
                except Exception as e:
                    logger.warning(f"Failed to resolve skill '{name}': {e}")
            
            if skill_texts:
                layers.append("## 领域知识\n" + "\n\n---\n\n".join(skill_texts))
        
        # 组装
        return "\n\n---\n\n".join(filter(None, layers))
    
    def list_skills(self, registry=None) -> list[dict]:
        """列出所有可用 Skill（供 skillList 工具使用）"""
        reg = registry or self.skill_registry
        if not reg:
            return []
        return [
            {"name": s.name, "doc": s.doc, "keywords": s.keywords,
             "suggested_group": s.suggested_group}
            for s in reg.all()
        ]
