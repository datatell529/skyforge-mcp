"""关键词自动匹配引擎 — 根据用户输入自动推荐 Skill"""
import logging

logger = logging.getLogger(__name__)


class SkillMatcher:
    """轻量级关键词 → Skill 匹配引擎
    
    使用简单的关键词映射表，不依赖 AI 判断。
    匹配规则：
    - 精确匹配：用户输入包含关键词 → 激活对应 Skill
    - 支持中文和英文关键词
    """
    
    # 默认关键词映射表（可由子类或配置覆盖）
    KEYWORD_MAP = {
        # 冷站
        "冷机": "chiller-analysis",
        "chiller": "chiller-analysis",
        "冷水机组": "chiller-analysis",
        # HVAC
        "空调": "hvac-diagnosis",
        "hvac": "hvac-diagnosis",
        "暖通": "hvac-diagnosis",
        "ahu": "hvac-diagnosis",
        "vav": "hvac-diagnosis",
        # 能耗
        "能耗": "energy-analysis",
        "energy": "energy-analysis",
        "能效": "energy-analysis",
        "kpi": "energy-analysis",
        "eui": "energy-analysis",
        "碳排放": "energy-analysis",
        "carbon": "energy-analysis",
        # 报警
        "报警": "alarm-review",
        "alarm": "alarm-review",
        "故障": "alarm-review",
        # 工单
        "工单": "work-order",
        "work order": "work-order",
        # 舒适度
        "舒适度": "comfort-analysis",
        "comfort": "comfort-analysis",
        "温湿度": "comfort-analysis",
        "空气质量": "comfort-analysis",
        # 查询
        "查询": "query-basics",
        "query": "query-basics",
        "搜索": "query-basics",
        "search": "query-basics",
        # Axon
        "axon": "axon-basics",
        "脚本": "axon-basics",
        # Xeto
        "xeto": "xeto-basics",
        "规范": "xeto-basics",
        "type": "xeto-basics",
    }
    
    @classmethod
    def match(cls, user_input: str, available_skills: list[str] = None) -> list[str]:
        """从用户输入中匹配应激活的 Skill 名称列表
        
        Args:
            user_input: 用户输入文本
            available_skills: 可用的 Skill 名称列表（None = 不限）
            
        Returns:
            匹配到的 Skill 名称列表（去重，按匹配顺序）
        """
        if not user_input:
            return []
        
        text = user_input.lower()
        activated = []
        seen = set()
        
        for keyword, skill_name in cls.KEYWORD_MAP.items():
            if keyword in text and skill_name not in seen:
                # 如果在可用列表中（或不限）
                if available_skills is None or skill_name in available_skills:
                    activated.append(skill_name)
                    seen.add(skill_name)
        
        return activated
