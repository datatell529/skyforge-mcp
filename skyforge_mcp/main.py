"""SkySpark MCP Server - Model Context Protocol server for SkySpark/Haxall systems.

FIN MCP (SkyBridge) with Phase 3 enhancements:
- Skill engine + Memory + Safety interlock
- finEval / finEvalWrite with prefix whitelist
- finHelp / finSkillSearch / finToolSuggest
- finSafetyLock / finSafetyUnlock / finSafetyStatus
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.fastmcp import FastMCP
import jsonschema

from app.skyspark.client import SkySpark
from app.skills.loader import SkillLoader
from app.skills.registry import SkillRegistry
from app.memory.store import MemoryStore
from app.prompts.builder import PromptBuilder

# ── Phase 3 FIN tools ─────────────────────────────────────────────
from app.tools.fin_eval_tools import finEval, finEvalWrite
from app.tools.fin_help_tools import finHelp, finToolSuggest
from app.tools.fin_skill_tools import finSkillSearch
from app.tools.fin_memory_tools import finMemory, finMemoryAppend
from app.tools.fin_safety_tools import finSafetyLock, finSafetyUnlock, finSafetyStatus

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LLM_NOTE: Initialize SkySpark client with error handling
try:
    skyspark = SkySpark()
    logger.info("✓ FIN client initialized (SCRAM persistent session)")
except Exception as e:  # noqa: BLE001 - surface clear initialization failure
    logger.error(f"✗ FAILED TO INITIALIZE SKYSPARK CLIENT: {e}")
    logger.error(
        "Check connection settings (host, port, credentials) and SkySpark server availability",
    )
    skyspark = None

# ── Initialize Skill engine + Memory + Prompt builder ────────────
skill_registry = None
memory_store = None
prompt_builder = None

if skyspark:
    try:
        loader = SkillLoader("app/skills/builtins")
        skill_registry = SkillRegistry(loader).load()
        logger.info(f"✓ Skill engine loaded ({len(skill_registry.all())} skills)")
    except Exception as e:
        logger.warning(f"Skill engine init failed: {e}")

    try:
        memory_store = MemoryStore("/var/skyforge-mcp-fin/memory.json")
        logger.info("✓ Memory store initialized")
    except Exception as e:
        logger.warning(f"Memory store init failed: {e}")

    prompt_builder = PromptBuilder(
        skill_registry=skill_registry,
        memory_store=memory_store,
    )

# Tool and prompt lookups
AXON_TOOLS_BY_ID: Dict[str, types.Tool] = {}
AXON_PROMPTS_BY_NAME: Dict[str, types.Prompt] = {}
FIN_CORE_TOOL_HANDLERS: Dict[str, callable] = {}

mcp = FastMCP(
    name="skyforge-mcp-fin",
    sse_path="/mcp",
    message_path="/mcp/messages",
    stateless_http=True,
)


# ── FIN Core tool definitions (Phase 3) ──────────────────────────

_FIN_CORE_TOOL_DEFS: list[types.Tool] = [
    types.Tool(
        name="finEval",
        description="在 FIN 范围内执行只读 Axon 查询。只允许 finMcp*/cm*/chillerOpt* 函数。返回值已清洗。",
        inputSchema={
            "type": "object",
            "properties": {
                "expr": {"type": "string", "description": "Axon 表达式，如 'readAll(equip)'"},
            },
            "required": ["expr"],
        },
    ),
    types.Tool(
        name="finEvalWrite",
        description="在 FIN 范围内执行写入操作。需要 confirm=true 确认。语法预检+超时保护+审计。",
        inputSchema={
            "type": "object",
            "properties": {
                "expr": {"type": "string", "description": "Axon 表达式"},
                "confirm": {"type": "boolean", "description": "确认执行写入", "default": False},
            },
            "required": ["expr"],
        },
    ),
    types.Tool(
        name="finHelp",
        description="查看 FIN 工具的使用说明和示例。",
        inputSchema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "工具名称"},
            },
            "required": ["tool_name"],
        },
    ),
    types.Tool(
        name="finToolSuggest",
        description="根据任务描述推荐工具组合。",
        inputSchema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务描述，如 '查看冷机运行状态'、'能耗分析'"},
            },
            "required": ["task"],
        },
    ),
    types.Tool(
        name="finSkillSearch",
        description="搜索 FIN 领域的 Skill。",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="finMemory",
        description="读取 FIN 项目记忆。记忆会自动注入 System Prompt。",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="finMemoryAppend",
        description="追加 FIN 项目记忆。后续所有对话自动包含此信息。",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要追加的记忆内容"},
            },
            "required": ["text"],
        },
    ),
    types.Tool(
        name="finSafetyLock",
        description="锁定一个设备，拒绝所有写入操作。物理设备操作前建议锁定。",
        inputSchema={
            "type": "object",
            "properties": {
                "equipRef": {"type": "string", "description": "设备引用 ID，如 'CH-01'"},
            },
            "required": ["equipRef"],
        },
    ),
    types.Tool(
        name="finSafetyUnlock",
        description="解锁一个设备，允许写入操作。",
        inputSchema={
            "type": "object",
            "properties": {
                "equipRef": {"type": "string", "description": "设备引用 ID"},
            },
            "required": ["equipRef"],
        },
    ),
    types.Tool(
        name="finSafetyStatus",
        description="查看设备安全联锁状态。不指定设备则返回全部。",
        inputSchema={
            "type": "object",
            "properties": {
                "equipRef": {"type": "string", "description": "可选，设备引用 ID"},
            },
        },
    ),
]

# Build handler map
FIN_CORE_TOOL_HANDLERS = {
    "finEval": lambda a: finEval(skyspark, a.get("expr", "")),
    "finEvalWrite": lambda a: finEvalWrite(skyspark, a.get("expr", ""), a.get("confirm", False)),
    "finHelp": lambda a: finHelp(a.get("tool_name", "")),
    "finToolSuggest": lambda a: finToolSuggest(a.get("task", "")),
    "finSkillSearch": lambda a: finSkillSearch(skill_registry, a.get("query", "")),
    "finMemory": lambda a: finMemory(memory_store),
    "finMemoryAppend": lambda a: finMemoryAppend(memory_store, a.get("text", "")),
    "finSafetyLock": lambda a: finSafetyLock(a.get("equipRef", "")),
    "finSafetyUnlock": lambda a: finSafetyUnlock(a.get("equipRef", "")),
    "finSafetyStatus": lambda a: finSafetyStatus(a.get("equipRef")),
}

# ── SB-09: Group-management tool definitions ─────────────────────────────

_GROUP_TOOL_DEFS: list[types.Tool] = [
    types.Tool(
        name="getToolGroups",
        description="获取可用工具分组列表。调用后返回所有可选的工具组名称和说明，AI 可根据任务选择适合的组。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="setToolGroup",
        description="切换当前工具组。设置后 tools/list 将只返回该组的工具（+基础工具）。用 'all' 可恢复显示全部工具。",
        inputSchema={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "工具组名称，可选: query/energy/diagnosis/control/coolmatrix_admin/admin/ai_chat/all",
                },
            },
            "required": ["group"],
        },
    ),
]


@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    """Fetch tools from SkySpark, filtered by active group (SB-09).

    Returns FIN core tools + group tools + axon tools.
    FIN core tools are always visible (base).
    """
    global AXON_TOOLS_BY_ID

    if skyspark is None:
        logger.error("Cannot list tools: SkySpark client not initialized")
        return _FIN_CORE_TOOL_DEFS + _GROUP_TOOL_DEFS

    # Fetch tools filtered by active group
    axon_tools = skyspark.fetchMcpTools()

    # Always include FIN core tools + group-management tools
    fin_tool_names = {t.name for t in axon_tools}
    for t in _FIN_CORE_TOOL_DEFS + _GROUP_TOOL_DEFS:
        if t.name not in fin_tool_names:
            axon_tools.append(t)
            fin_tool_names.add(t.name)

    # Update lookup dictionary
    AXON_TOOLS_BY_ID = {tool.name: tool for tool in axon_tools}

    logger.info(
        "tools/list: active_group=%s, returned %d tools (%d FIN core + %d axon/group)",
        skyspark._active_group if hasattr(skyspark, "_active_group") else "?",
        len(axon_tools),
        len(_FIN_CORE_TOOL_DEFS),
        len(axon_tools) - len(_FIN_CORE_TOOL_DEFS) - len(_GROUP_TOOL_DEFS),
    )
    return axon_tools


@mcp._mcp_server.list_prompts()
async def _list_prompts() -> List[types.Prompt]:
    """Fetch fresh axon prompts from SkySpark."""
    global AXON_PROMPTS_BY_NAME

    if skyspark is None:
        logger.error("Cannot list prompts: SkySpark client not initialized")
        return []

    # Fetch fresh prompts from SkySpark
    axon_prompts = skyspark.fetchMcpPrompts()

    # Update lookup dictionary
    AXON_PROMPTS_BY_NAME = {prompt.name: prompt for prompt in axon_prompts}

    return axon_prompts


# ── Prompt content templates (SB-11) ─────────────────────────────────────

_DOMAIN_SYSTEM_PROMPTS: dict[str, str] = {
    "fin_general_assistant": (
        "你是一名楼宇运维助手（finCopilot 通用模式）。"
        "用 finMcp* 工具回答用户关于楼宇设备运行、查询、控制的日常问题。"
        "回答应简洁准确，必要时引用数据来源。"
    ),
    "fin_hvac_diagnosis": (
        "你是一名暖通空调诊断专家。分析用户指定的 HVAC 设备（AHU、冷机、冷却塔、水泵等）运行状态。\n"
        "工作流程：\n"
        "1. 用 finMcpDescribeEntity 了解设备详情\n"
        "2. 用 finMcpReadCurrent 读取关键点位当前值\n"
        "3. 用 finMcpReadHistory/ChillerPerformance 分析趋势\n"
        "4. 有异常时用 finMcpListAlarms 查看关联报警\n"
        "5. 如需创建工单用 finMcpCreateWorkOrder\n"
        "诊断应给出：现象 → 根因分析 → 建议措施。"
    ),
    "fin_me_equipment": (
        "你是一名机电设备工程师。查询风机、水泵、照明等机电设备的状态与参数。\n"
        "使用 finMcpQuery/finMcpDescribeEntity/finMcpReadCurrent 获取设备信息。"
    ),
    "fin_space_comfort": (
        "你是一名室内环境质量(IEQ)专家。分析温度、湿度、CO₂、光照等环境参数，"
        "评估空间舒适度是否符合 ASHRAE 标准，给出改善建议。"
    ),
    "fin_bas_control": (
        "你是一名楼宇自控系统工程师。查看楼控系统的运行参数、控制序列、"
        "设备启停状态和模式切换。使用 finMcpQuery/finMcpReadCurrent 获取信息。"
    ),
    "fin_fdd_diagnosis": (
        "你是一名故障诊断专家。主动分析设备报警，诊断故障根因。\n"
        "工作流程：\n"
        "1. 用 finMcpListAlarms 查看当前报警\n"
        "2. 用 finMcpDescribeEntity 了解报警设备\n"
        "3. 用 finMcpReadCurrent/ReadHistory 分析相关点位\n"
        "4. 用 finMcpRecallCases 检索相似案例\n"
        "5. 给出诊断结论和修复建议，必要时创建工单"
    ),
    "fin_energy_analysis": (
        "你是一名能效分析师。分析建筑能耗数据，计算 KPI，识别节能机会。\n"
        "工作流程：\n"
        "1. 用 finMcpEnergyBreakdown 看能耗构成\n"
        "2. 用 finMcpComputeKpi 计算 EUI/COP 等指标\n"
        "3. 用 finMcpEnergyBaseline 对比基准\n"
        "4. 用 finMcpSavingsPotential 评估节能潜力\n"
        "5. 用 finMcpCarbon 核算碳排放\n"
        "输出应包含：能耗概况 → 关键指标 → 异常发现 → 节能建议。"
    ),
    "fin_wellness": (
        "你是一名健康建筑专家。分析室内空气质量(IAQ)、热舒适(PMV/PPD)、"
        "CO₂浓度、VOC等健康指标，参照 WELL/绿建标准给出评估和改进建议。"
    ),
    "fin_report_generation": (
        "你是一名报告撰写专家。根据用户需求自动生成运行报告/能效报告/诊断报告。\n"
        "先用 finMcpQuery 收集数据，再用 finMcpReport 或 finMcpBuildCustomReport 生成报告。"
    ),
    "fin_query_entities": (
        "你是一名楼宇数据检索专家。用自然语言理解用户的查询意图，"
        "转换成 finMcpQuery 的 Haystack 过滤器来查询设备、点位和空间信息。"
    ),
    "fin_work_order": (
        "你是一名工单管理员。管理工单的全生命周期：查询、创建、关闭。\n"
        "- list：用 finMcpListWorkOrders 列出工单\n"
        "- create：用 finMcpCreateWorkOrder 新建工单\n"
        "- close：用 finMcpCloseWorkOrder 关闭工单并记录处理结果"
    ),
    "fin_alarm_review": (
        "你是一名报警审查员。查看当前激活报警，分析严重程度和影响范围。\n"
        "用 finMcpListAlarms 获取报警列表，用 finMcpCriticalAlarms 查看严重报警。"
    ),
}


async def _get_prompt_request(req: types.GetPromptRequest) -> types.ServerResult:
    """Handle get_prompt request — returns domain-specific system prompt + user message."""
    prompt_name = req.params.name
    arguments = req.params.arguments or {}

    # Lazily populate prompt cache if needed (SB-11)
    global AXON_PROMPTS_BY_NAME
    if not AXON_PROMPTS_BY_NAME:
        try:
            from app.skyspark.client import SkySpark
            client = skyspark or SkySpark()
            axon_prompts = client.fetchMcpPrompts()
            AXON_PROMPTS_BY_NAME = {p.name: p for p in axon_prompts}
        except Exception:
            pass

    # Look up prompt in our cache (fallback to _DOMAIN_SYSTEM_PROMPTS keys)
    prompt = AXON_PROMPTS_BY_NAME.get(prompt_name)
    if prompt is None:
        return types.ServerResult(
            types.GetPromptResult(
                description="",
                messages=[
                    types.PromptMessage(
                        role="user",
                        content=types.TextContent(
                            type="text",
                            text=f"Unknown prompt: {prompt_name}",
                        ),
                    ),
                ],
                _meta={"error": f"Unknown prompt: {prompt_name}"},
            ),
        )

    # Build system message from domain template
    system_text = _DOMAIN_SYSTEM_PROMPTS.get(
        prompt_name,
        f"You are a building operations assistant. Use finCopilot tools to help the user.\nPrompt: {prompt.description}",
    )

    # Build user message from arguments
    user_parts: list[str] = []
    if prompt_name == "fin_general_assistant":
        user_parts.append(arguments.get("question", "请帮我看看当前的运行状态。"))
    elif prompt_name == "fin_energy_analysis":
        equip = arguments.get("equipRef", "整个建筑")
        rng = arguments.get("range", "thisMonth")
        user_parts.append(f"请对 {equip} 在 {rng} 的能耗进行分析。")
    elif prompt_name == "fin_hvac_diagnosis":
        equip = arguments.get("equipRef", "指定设备")
        rng = arguments.get("range", "thisWeek")
        user_parts.append(f"请诊断 {equip} 在 {rng} 的运行状态，分析是否存在异常。")
    elif prompt_name == "fin_report_generation":
        rtype = arguments.get("reportType", "energy")
        rng = arguments.get("range", "thisMonth")
        equip = arguments.get("equipRef", "")
        equip_part = f" 设备范围: {equip}" if equip else ""
        user_parts.append(f"请生成一份{rtype}报告，覆盖时间: {rng}。{equip_part}")
    elif prompt_name == "fin_fdd_diagnosis":
        equip = arguments.get("equipRef", "所有设备")
        user_parts.append(f"请检查 {equip} 是否存在故障，分析报警并给出诊断结论。")
    elif prompt_name == "fin_work_order":
        action = arguments.get("action", "list")
        if action == "list":
            user_parts.append("请列出当前工单。")
        elif action == "create":
            desc = arguments.get("description", "新建工单")
            user_parts.append(f"请创建工单: {desc}")
        elif action == "close":
            wid = arguments.get("workOrderId", "")
            user_parts.append(f"请关闭工单 {wid}，记录处理结果。")
        else:
            user_parts.append(f"执行工单操作: {action}")
    elif prompt_name == "fin_query_entities":
        q = arguments.get("query", "所有设备")
        limit = arguments.get("limit", 20)
        user_parts.append(f"查询: {q}（限制 {limit} 条）")
    elif prompt_name == "fin_alarm_review":
        sev = arguments.get("severity", "all")
        user_parts.append(f"请查看严重级别为 '{sev}' 的当前报警。")
    elif prompt_name == "fin_space_comfort":
        space = arguments.get("spaceRef", "指定区域")
        rng = arguments.get("range", "thisWeek")
        user_parts.append(f"请分析 {space} 在 {rng} 的环境舒适度。")
    elif prompt_name == "fin_wellness":
        space = arguments.get("spaceRef", "指定区域")
        rng = arguments.get("range", "thisWeek")
        user_parts.append(f"请评估 {space} 在 {rng} 的健康舒适指标。")
    elif prompt_name == "fin_me_equipment":
        equip = arguments.get("equipRef", "指定设备")
        user_parts.append(f"请查询设备 {equip} 的当前状态和参数。")
    elif prompt_name == "fin_bas_control":
        equip = arguments.get("equipRef", "")
        flt = arguments.get("filter", "")
        parts = []
        if equip:
            parts.append(f"设备: {equip}")
        if flt:
            parts.append(f"过滤器: {flt}")
        parts.append("请查看楼控系统运行状态")
        user_parts.append(" | ".join(parts))
    else:
        # Generic fallback
        for arg_name, arg_value in arguments.items():
            user_parts.append(f"{arg_name}: {arg_value}")
        if not user_parts:
            user_parts.append(prompt.description or f"请帮我处理 {prompt_name}")

    # MCP protocol only supports "user" and "assistant" roles for PromptMessage.
    # We combine the system instruction into the user message, prefixed with a
    # clear role indicator so the LLM understands it's a system-level directive.
    combined_text = f"[系统指令]\n{system_text}\n\n[用户问题]\n{'\n'.join(user_parts)}"

    return types.ServerResult(
        types.GetPromptResult(
            description=prompt.description or "",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=combined_text),
                ),
            ],
        ),
    )


def _validate_tool_arguments(tool: types.Tool, arguments: Dict[str, Any]) -> Optional[str]:
    """Validate tool arguments against JSON schema.

    Args:
        tool: Tool with inputSchema
        arguments: Arguments to validate

    Returns:
        Error message string if validation fails, None if valid
    """
    if not tool.inputSchema:
        return None

    try:
        jsonschema.validate(instance=arguments, schema=tool.inputSchema)
        return None
    except jsonschema.ValidationError as exc:  # noqa: TRY003 - return string
        return f"Input validation error: {exc.message}"


async def _call_tool_request(req: types.CallToolRequest) -> types.ServerResult:
    """
    Tool dispatcher: FIN core tools → group tools → axon tools.
    """
    tool_name = req.params.name
    arguments = req.params.arguments or {}

    # ── Phase 3: Handle FIN core tools ────────────────────────────────
    handler = FIN_CORE_TOOL_HANDLERS.get(tool_name)
    if handler:
        try:
            result_str = handler(arguments)
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=result_str)],
                ),
            )
        except Exception as e:
            logger.error(f"FIN core tool '{tool_name}' failed: {e}", exc_info=True)
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps({
                        "error": True, "message": f"工具执行失败: {str(e)[:200]}",
                    }))],
                    isError=True,
                ),
            )

    # ── SB-09: Handle group-management tools ──────────────────────────
    if tool_name == "getToolGroups":
        if skyspark is None:
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text="SkySpark client not available")],
                    isError=True,
                ),
            )
        groups = skyspark.getToolGroups()
        lines = ["可用工具组：\n"]
        for g in groups:
            lines.append(f"  · {g['name']}: {g['description']}")
        lines.append(f"\n使用 setToolGroup(group='组名') 切换工具组。")
        lines.append(f"当前组: {skyspark._active_group}")
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="\n".join(lines))],
            ),
        )

    if tool_name == "setToolGroup":
        if skyspark is None:
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text="SkySpark client not available")],
                    isError=True,
                ),
            )
        group_name = arguments.get("group", "")
        if not group_name:
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text="请指定 group 参数")],
                    isError=True,
                ),
            )
        try:
            msg = skyspark.setActiveGroup(group_name)
            # Invalidate the tool cache so next tools/list picks up the new group
            skyspark._invalidate_tool_cache()
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=msg)],
                ),
            )
        except ValueError as exc:
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(exc))],
                    isError=True,
                ),
            )

    # ── Regular axon tool lookup ──────────────────────────────────────
    tool = AXON_TOOLS_BY_ID.get(tool_name)
    if not tool:
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Unknown tool: {tool_name}",
                    ),
                ],
                isError=True,
            ),
        )

    return await _handle_axon_tool_call(tool, req)


async def _handle_axon_tool_call(
    axon_tool: types.Tool, req: types.CallToolRequest,
) -> types.ServerResult:
    arguments = req.params.arguments or {}
    logger.debug(f"Incoming axon tool args: {arguments}")

    # Validate arguments against tool schema
    validation_error = _validate_tool_arguments(axon_tool, arguments)
    if validation_error:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=validation_error)],
                isError=True,
            ),
        )

    # Additional axon-specific validation
    if not axon_tool.name or not isinstance(axon_tool.name, str):
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="Invalid axon tool name"),
                ],
                isError=True,
            ),
        )

    # Validate that axon tool has axon marker in meta
    if not axon_tool.meta or not axon_tool.meta.get("axon"):
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=f"Tool {axon_tool.name} is not a valid axon tool",
                    ),
                ],
                isError=True,
            ),
        )

    # Validate SkySpark client is available
    if not hasattr(skyspark, "handleToolCall") or skyspark is None:
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text="SkySpark client not available for axon tool execution",
                    ),
                ],
                isError=True,
            ),
        )

    logger.debug(f"Processing axon tool: {axon_tool.name}")

    # Extract params kind and order from tool meta (default to Dict for backwards compatibility)
    params_kind = axon_tool.meta.get("paramsKind", "Dict") if axon_tool.meta else "Dict"
    params_order = axon_tool.meta.get("paramsOrder", []) if axon_tool.meta else []

    # Execute actual SkySpark call
    try:
        hgrid_result = skyspark.handleToolCall(
            axon_tool.name, arguments, params_kind, params_order,
        )

        # Dual format output - JSON for structured data, Zinc for human-readable text or low token counts
        # - structuredContent: JSON format for data processing
        # - content: Zinc format for human-readable grid display or low token count
        structured_content = hgrid_result.toJson()
        zinc_content = hgrid_result.toZinc()

        # Generate response text - use Zinc format for content
        call_result = types.CallToolResult(
            content=[types.TextContent(type="text", text=zinc_content)],
            structuredContent=structured_content,
            _meta=axon_tool.meta or {},
        )

        return types.ServerResult(call_result)

    except Exception as e:  # noqa: BLE001 - surface tool execution errors
        logger.error(f"SkySpark call failed: {e}", exc_info=True)
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=f"Axon tool execution failed: {str(e)}"),
                ],
                isError=True,
            ),
        )


mcp._mcp_server.request_handlers[types.CallToolRequest] = _call_tool_request
mcp._mcp_server.request_handlers[types.GetPromptRequest] = _get_prompt_request


app = mcp.streamable_http_app()

try:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
except Exception as e:  # noqa: BLE001
    logger.warning(f"Failed to add CORS middleware: {e}")

# Log ALL routes (including nested mounts) to diagnose endpoint paths
def _log_routes() -> None:
    try:
        def visit(prefix: str, routes) -> None:
            for r in routes or []:
                path = getattr(r, "path", "/")
                methods = getattr(r, "methods", None)
                full_path = f"{prefix}{path}".replace("//", "/")
                logger.info(f"HTTP route mounted: path='{full_path}' methods={methods}")
                # Recurse into mounted sub-apps
                child_app = getattr(r, "app", None)
                child_routes = getattr(child_app, "routes", None)
                if child_routes:
                    visit(full_path, child_routes)

        visit("", getattr(app, "routes", []))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to enumerate routes: {e}")

_log_routes()

def main() -> None:
    """Entry point for the mcp script."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()


