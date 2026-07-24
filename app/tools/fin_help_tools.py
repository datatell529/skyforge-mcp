"""FIN 帮助工具 — finHelp / finToolSuggest"""
import json
import logging

logger = logging.getLogger(__name__)

# 工具使用提示库
TOOL_TIPS = {
    "finMcpQuery": {"use": "语义查询设备/点位", "example": 'finMcpQuery("equip and ahu", 20)'},
    "finMcpDescribeEntity": {"use": "查看设备详情", "example": "finMcpDescribeEntity(@p:xxx)"},
    "finMcpReadCurrent": {"use": "读取点位当前值", "example": "finMcpReadCurrent([@p:xxx, @p:yyy])"},
    "finMcpReadHistory": {"use": "读取历史趋势", "example": 'finMcpReadHistory(@p:xxx, "thisWeek")'},
    "finMcpListAlarms": {"use": "查看当前报警", "example": "finMcpListAlarms()"},
    "finMcpEnergyBreakdown": {"use": "能耗分解", "example": 'finMcpEnergyBreakdown("thisMonth")'},
    "finMcpProposeWrite": {"use": "提议写入控制点", "example": 'finMcpProposeWrite(@p:xxx, 45, "kW")'},
    "finMcpCreateWorkOrder": {"use": "创建工单", "example": 'finMcpCreateWorkOrder("AHU-01 故障", "high")'},
    "finEval": {"use": "执行 FIN 范围内的 Axon 查询", "example": 'finEval("readAll(equip)")'},
    "finSafetyLock": {"use": "锁定设备防止误写入", "example": "finSafetyLock('CH-01')"},
    "finSafetyUnlock": {"use": "解锁设备允许写入", "example": "finSafetyUnlock('CH-01')"},
}

TASK_TO_TOOLS = {
    "查询设备": ["finMcpQuery", "finMcpDescribeEntity"],
    "查看当前值": ["finMcpReadCurrent", "finMcpDescribeEntity"],
    "历史趋势": ["finMcpReadHistory", "finMcpQuery"],
    "报警审查": ["finMcpListAlarms", "finMcpCriticalAlarms"],
    "能耗分析": ["finMcpEnergyBreakdown", "finMcpEnergyBaseline", "finMcpCarbon"],
    "冷机诊断": ["finMcpDescribeEntity", "finMcpReadCurrent", "finMcpReadHistory", "finMcpChillerPerformance"],
    "控制写入": ["finMcpProposeWrite", "finSafetyLock", "finSafetyStatus"],
    "工单管理": ["finMcpCreateWorkOrder", "finMcpListWorkOrders", "finMcpCloseWorkOrder"],
    "能效报告": ["finMcpEnergyReport", "finMcpCarbon", "finMcpSavingsPotential"],
    "安全联锁": ["finSafetyStatus", "finSafetyLock", "finSafetyUnlock"],
}


def finHelp(tool_name: str) -> str:
    """查看 FIN 工具的详细使用说明"""
    if not tool_name:
        return json.dumps({
            "error": True, "message": "请指定工具名称",
            "available_tools": list(TOOL_TIPS.keys()),
        })
    
    tip = TOOL_TIPS.get(tool_name)
    if tip:
        return json.dumps({
            "tool": tool_name,
            "use": tip["use"],
            "example": tip["example"],
            "hint": "在 FIN MCP 中，控制类操作请先检查安全联锁状态。",
        }, ensure_ascii=False)
    
    return json.dumps({
        "info": f"'{tool_name}' 暂无预置提示，可直接调用。",
        "hint": "在 FIN MCP 中，所有工具按功能分组，可用 setToolGroup 切换。",
    }, ensure_ascii=False)


def finToolSuggest(task: str) -> str:
    """根据任务描述推荐工具组合"""
    if not task:
        return json.dumps({"error": True, "message": "请描述您要完成的任务"})
    
    # 匹配任务关键词
    suggestions = []
    for keyword, tools in TASK_TO_TOOLS.items():
        if keyword in task or any(kw in task for kw in keyword):
            suggestions.append({"task": keyword, "tools": tools})
    
    if not suggestions:
        # 兜底推荐
        suggestions = [
            {"task": "查询", "tools": ["finMcpQuery", "finMcpDescribeEntity"]},
            {"task": "控制", "tools": ["finMcpProposeWrite", "finSafetyStatus"]},
        ]
    
    return json.dumps({
        "task": task,
        "suggestions": suggestions,
        "general_flow": "查询 → 分析 → 确认 → 执行",
        "hint": "涉及写入操作前，建议先用 finSafetyStatus 检查设备联锁状态。",
    }, ensure_ascii=False)
