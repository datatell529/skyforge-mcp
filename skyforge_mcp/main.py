"""SkySpark MCP Server - Model Context Protocol server for SkySpark/Haxall systems.

Phase 2: Integrated with core tools + Skill engine + Memory + Safety.

Provides ~15 carefully designed core tools plus auto-discovered Axon functions
as secondary. Supports both stdio and HTTP/SSE transports.
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

# ── Phase 1 & 2 tools ─────────────────────────────────────────────
from app.tools.eval_tools import evalAxon, evalAxonWrite
from app.tools.help_tools import helpFunc, helpSkill, helpDoc
from app.tools.search_tools import searchFuncs, searchSkills, searchDocs
from app.tools.xeto_tools import readXeto, writeXeto, editXeto
from app.tools.memory_tools import readMemory, appendMemory
from app.tools.skill_tools import skillList, skillUse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Initialize SkySpark client ────────────────────────────────────
try:
    skyspark = SkySpark()
    logger.info("✓ SkySpark client initialized (SCRAM persistent session)")
except Exception as e:
    logger.error(f"✗ FAILED TO INITIALIZE SKYSPARK CLIENT: {e}")
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
        memory_store = MemoryStore("/var/skyforge-mcp/memory.json")
        logger.info("✓ Memory store initialized")
    except Exception as e:
        logger.warning(f"Memory store init failed: {e}")

    prompt_builder = PromptBuilder(
        skill_registry=skill_registry,
        memory_store=memory_store,
    )

# Tool lookup dictionaries
CORE_TOOLS: Dict[str, types.Tool] = {}  # Phase 2 core tools
AXON_TOOLS_BY_ID: Dict[str, types.Tool] = {}  # Auto-discovered axon tools
AXON_PROMPTS_BY_NAME: Dict[str, types.Prompt] = {}

mcp = FastMCP(
    name="skyforge-mcp",
    sse_path="/mcp",
    message_path="/mcp/messages",
    stateless_http=True,
)


# ── Core tool definitions ─────────────────────────────────────────

def _build_core_tools() -> list[types.Tool]:
    """Build the ~15 core tool definitions."""
    return [
        # Eval
        types.Tool(
            name="evalAxon",
            description="执行只读 Axon 查询。自动拒绝写操作。返回值已清洗为扁平 JSON。",
            inputSchema={
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "Axon 表达式，如 'readAll(site)' 或 'readAll(equip and ahu)'"},
                },
                "required": ["expr"],
            },
        ),
        types.Tool(
            name="evalAxonWrite",
            description="执行可能包含写操作的 Axon 表达式。需要 confirm=true 确认。执行前自动语法预检+超时保护。",
            inputSchema={
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "Axon 表达式"},
                    "confirm": {"type": "boolean", "description": "确认执行写操作", "default": False},
                },
                "required": ["expr"],
            },
        ),
        # Help
        types.Tool(
            name="helpFunc",
            description="查看 Axon 函数的签名和参数说明。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "函数名称，如 'hisRead'、'readAll'"},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="helpSkill",
            description="查看 Skill 的详细内容和适用场景。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill 名称，如 'hvac-diagnosis'"},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="helpDoc",
            description="查看 SkySpark 文档页内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "文档 URI，如 'doc/hx.ai/index'"},
                },
                "required": ["uri"],
            },
        ),
        # Search
        types.Tool(
            name="searchFuncs",
            description="按关键词搜索可用的 Axon 函数。先搜函数名，再搜文档内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，如 'chiller'、'energy'"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="searchSkills",
            description="按关键词搜索可用的 Skill。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="searchDocs",
            description="搜索 SkySpark 文档页。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        ),
        # Xeto
        types.Tool(
            name="readXeto",
            description="读取 Xeto 规范定义。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Xeto 类型名称，如 'Ahu'、'Chiller'、'Site'"},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="writeXeto",
            description="写入/更新 Xeto 规范。需要 confirm=true 确认。执行前自动语法预检。",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Xeto 源码"},
                    "confirm": {"type": "boolean", "description": "确认执行写入", "default": False},
                },
                "required": ["code"],
            },
        ),
        types.Tool(
            name="editXeto",
            description="编辑 Xeto 源码（替换模式）。将 old 替换为 new。需要 confirm=true 确认。",
            inputSchema={
                "type": "object",
                "properties": {
                    "old": {"type": "string", "description": "待替换的原文"},
                    "new": {"type": "string", "description": "替换后的新文"},
                    "confirm": {"type": "boolean", "description": "确认执行编辑", "default": False},
                },
                "required": ["old", "new"],
            },
        ),
        # Skill management
        types.Tool(
            name="skillList",
            description="列出所有可用的 Skill 及当前激活的 Skill。",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="skillUse",
            description="激活一个 Skill。Skill 激活后其领域知识会注入 System Prompt。使用 skillUse('none') 清除所有。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill 名称，或 'none'（清除所有）"},
                },
                "required": ["name"],
            },
        ),
        # Memory
        types.Tool(
            name="readMemory",
            description="读取项目级记忆。记忆会在每次对话中自动注入 System Prompt。",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="appendMemory",
            description="追加项目级记忆。后续所有对话自动包含此信息。建议每条 1-2 句。",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要追加的记忆内容"},
                },
                "required": ["text"],
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────

CORE_TOOL_HANDLERS = {
    "evalAxon": lambda args, meta: evalAxon(skyspark, args.get("expr", "")),
    "evalAxonWrite": lambda args, meta: evalAxonWrite(skyspark, args.get("expr", ""), args.get("confirm", False)),
    "helpFunc": lambda args, meta: helpFunc(skyspark, args.get("name", "")),
    "helpSkill": lambda args, meta: helpSkill(skill_registry, args.get("name", "")),
    "helpDoc": lambda args, meta: helpDoc(skyspark, args.get("uri", "")),
    "searchFuncs": lambda args, meta: searchFuncs(skyspark, args.get("query", "")),
    "searchSkills": lambda args, meta: searchSkills(skill_registry, args.get("query", "")),
    "searchDocs": lambda args, meta: searchDocs(skyspark, args.get("query", "")),
    "readXeto": lambda args, meta: readXeto(skyspark, args.get("name", "")),
    "writeXeto": lambda args, meta: writeXeto(skyspark, args.get("code", ""), args.get("confirm", False)),
    "editXeto": lambda args, meta: editXeto(skyspark, args.get("old", ""), args.get("new", ""), args.get("confirm", False)),
    "skillList": lambda args, meta: skillList(skill_registry),
    "skillUse": lambda args, meta: skillUse(skill_registry, args.get("name", "")),
    "readMemory": lambda args, meta: readMemory(memory_store),
    "appendMemory": lambda args, meta: appendMemory(memory_store, args.get("text", "")),
}


@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    """Return core tools + auto-discovered axon tools."""
    global AXON_TOOLS_BY_ID, CORE_TOOLS

    # Build core tools
    core_tools = _build_core_tools()
    CORE_TOOLS = {t.name: t for t in core_tools}

    # Fetch auto-discovered axon tools (secondary)
    axon_tools = []
    if skyspark:
        try:
            axon_tools = skyspark.fetchMcpTools()
        except Exception as e:
            logger.warning(f"Failed to fetch axon tools: {e}")
    AXON_TOOLS_BY_ID = {tool.name: tool for tool in axon_tools}

    # Return core tools only — axon tools are accessible via searchFuncs + call_tool
    logger.info(f"tools/list: {len(core_tools)} core tools + {len(axon_tools)} axon tools (hidden, searchable)")
    return core_tools


@mcp._mcp_server.list_prompts()
async def _list_prompts() -> List[types.Prompt]:
    """Fetch fresh axon prompts from SkySpark."""
    global AXON_PROMPTS_BY_NAME

    if skyspark is None:
        return []

    try:
        axon_prompts = skyspark.fetchMcpPrompts()
        AXON_PROMPTS_BY_NAME = {prompt.name: prompt for prompt in axon_prompts}
        return axon_prompts
    except Exception:
        return []


async def _get_prompt_request(req: types.GetPromptRequest) -> types.ServerResult:
    """Handle get_prompt request — returns prompt assembled by PromptBuilder."""
    prompt_name = req.params.name
    arguments = req.params.arguments or {}

    # Check if this is one of our managed prompts
    if prompt_name in _DOMAIN_SYSTEM_PROMPTS:
        return await _build_domain_prompt(prompt_name, arguments)

    # Fallback to SkySpark prompts
    prompt = AXON_PROMPTS_BY_NAME.get(prompt_name)
    if prompt is None:
        return types.ServerResult(
            types.GetPromptResult(
                description="",
                messages=[types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=f"Unknown prompt: {prompt_name}"),
                )],
                _meta={"error": f"Unknown prompt: {prompt_name}"},
            ),
        )

    message_text = f"Prompt: {prompt.description}"
    if arguments:
        message_text += "\nArguments:\n" + "\n".join(f"  {k}: {v}" for k, v in arguments.items())

    return types.ServerResult(
        types.GetPromptResult(
            description=prompt.description or "",
            messages=[types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=message_text),
            )],
        ),
    )


# ── Domain system prompts (from Phase 1 PromptBuilder) ────────────

_DOMAIN_SYSTEM_PROMPTS = {
    "sky_general_assistant": "你是一名 SkySpark 通用运维助手。使用 SkySpark 工具回答用户关于楼宇设备、数据查询、分析的问题。",
    "sky_hvac_diagnosis": "你是一名暖通空调诊断专家。分析 HVAC 设备（AHU、冷机、冷却塔、水泵）的运行状态，诊断故障原因。",
    "sky_energy_analysis": "你是一名能效分析师。分析建筑能耗数据，计算 KPI，识别节能机会。",
    "sky_alarm_review": "你是一名报警审查员。查看当前激活报警，分析严重程度和影响范围。",
    "sky_query_entities": "你是一名数据查询专家。用 Axon 表达式查询 SkySpark 中的设备、点位和空间信息。",
    "sky_xeto_editor": "你是一名 Xeto 规范工程师。查看、编辑 SkySpark 的 Xeto 类型定义。",
}


async def _build_domain_prompt(prompt_name: str, arguments: dict) -> types.ServerResult:
    """Build a domain-specific prompt using PromptBuilder."""
    soul = _DOMAIN_SYSTEM_PROMPTS.get(prompt_name, "你是一名 SkySpark 运维助手。")
    user_input = arguments.get("question") or arguments.get("query") or ""

    # Use PromptBuilder to assemble 4-layer prompt
    system_prompt = ""
    if prompt_builder:
        system_prompt = prompt_builder.build(
            soul=soul,
            memory=True,
            auto_match_input=user_input,
        )
    else:
        system_prompt = soul

    # Build user message
    user_parts = []
    for k, v in arguments.items():
        user_parts.append(f"{k}: {v}")
    if not user_parts:
        user_parts.append(user_input or f"请帮我处理 {prompt_name}")

    combined = f"[系统指令]\n{system_prompt}\n\n[用户问题]\n{''.join(user_parts)}"

    return types.ServerResult(
        types.GetPromptResult(
            description=_DOMAIN_SYSTEM_PROMPTS.get(prompt_name, ""),
            messages=[types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=combined),
            )],
        ),
    )


def _validate_tool_arguments(tool: types.Tool, arguments: Dict[str, Any]) -> Optional[str]:
    """Validate tool arguments against JSON schema."""
    if not tool.inputSchema:
        return None
    try:
        jsonschema.validate(instance=arguments, schema=tool.inputSchema)
        return None
    except jsonschema.ValidationError as exc:
        return f"Input validation error: {exc.message}"


async def _call_tool_request(req: types.CallToolRequest) -> types.ServerResult:
    """
    Tool dispatcher: core tools first, then axon tools fallback.
    """
    tool_name = req.params.name
    arguments = req.params.arguments or {}

    # ── Dispatch to core tools ─────────────────────────────────────
    handler = CORE_TOOL_HANDLERS.get(tool_name)
    if handler:
        try:
            result_str = handler(arguments, {})
            # result_str is already JSON string
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=result_str)],
                ),
            )
        except Exception as e:
            logger.error(f"Core tool '{tool_name}' failed: {e}", exc_info=True)
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps({
                        "error": True, "message": f"工具执行失败: {str(e)[:200]}",
                    }))],
                    isError=True,
                ),
            )

    # ── Dispatch to auto-discovered axon tools ─────────────────────
    tool = AXON_TOOLS_BY_ID.get(tool_name)
    if not tool:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"未知工具: {tool_name}")],
                isError=True,
            ),
        )

    return await _handle_axon_tool_call(tool, req)


async def _handle_axon_tool_call(
    axon_tool: types.Tool, req: types.CallToolRequest,
) -> types.ServerResult:
    """Handle auto-discovered axon tool calls (existing logic)."""
    arguments = req.params.arguments or {}
    logger.debug(f"Incoming axon tool args: {arguments}")

    validation_error = _validate_tool_arguments(axon_tool, arguments)
    if validation_error:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=validation_error)],
                isError=True,
            ),
        )

    if not axon_tool.name or not isinstance(axon_tool.name, str):
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="Invalid axon tool name")],
                isError=True,
            ),
        )

    if not axon_tool.meta or not axon_tool.meta.get("axon"):
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Tool {axon_tool.name} is not a valid axon tool")],
                isError=True,
            ),
        )

    if not hasattr(skyspark, "handleToolCall") or skyspark is None:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="SkySpark client not available")],
                isError=True,
            ),
        )

    params_kind = axon_tool.meta.get("paramsKind", "Dict") if axon_tool.meta else "Dict"
    params_order = axon_tool.meta.get("paramsOrder", []) if axon_tool.meta else []

    try:
        hgrid_result = skyspark.handleToolCall(
            axon_tool.name, arguments, params_kind, params_order,
        )

        structured_content = hgrid_result.toJson()
        zinc_content = hgrid_result.toZinc()

        call_result = types.CallToolResult(
            content=[types.TextContent(type="text", text=zinc_content)],
            structuredContent=structured_content,
            _meta=axon_tool.meta or {},
        )

        return types.ServerResult(call_result)

    except Exception as e:
        logger.error(f"SkySpark call failed: {e}", exc_info=True)
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Axon tool execution failed: {str(e)}")],
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
except Exception as e:
    logger.warning(f"Failed to add CORS middleware: {e}")


def main() -> None:
    """Entry point for the mcp script."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()


