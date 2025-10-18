"""SkySpark MCP Server - Model Context Protocol server for SkySpark/Haxall systems.

Provides dynamic MCP tools from SkySpark Axon functions and prompts.
Supports both stdio and HTTP/SSE transports for flexible integration
with MCP clients like Claude Desktop, Cline, and custom applications.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.fastmcp import FastMCP
import jsonschema

from app.skyspark.client import SkySpark

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LLM_NOTE: Initialize SkySpark client with error handling
try:
    skyspark = SkySpark()
    logger.info("✓ SkySpark client initialized successfully")
except Exception as e:  # noqa: BLE001 - surface clear initialization failure
    logger.error(f"✗ FAILED TO INITIALIZE SKYSPARK CLIENT: {e}")
    logger.error(
        "Check connection settings (host, port, credentials) and SkySpark server availability",
    )
    skyspark = None

# Tool and prompt lookups - updated dynamically on each list call
# LLM_NOTE: Axon tools only, no basic tools
AXON_TOOLS_BY_ID: Dict[str, types.Tool] = {}
AXON_PROMPTS_BY_NAME: Dict[str, types.Prompt] = {}

mcp = FastMCP(
    name="skyforge-mcp",
    sse_path="/mcp",
    message_path="/mcp/messages",
    stateless_http=True,
)


@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    """Fetch fresh axon tools from SkySpark.
    LLM_NOTE: Returns only axon tools, no basic tools."""
    global AXON_TOOLS_BY_ID

    if skyspark is None:
        logger.error("Cannot list tools: SkySpark client not initialized")
        return []

    # Fetch fresh axon tools from SkySpark
    axon_tools = skyspark.fetchMcpTools()

    # Update lookup dictionary
    AXON_TOOLS_BY_ID = {tool.name: tool for tool in axon_tools}

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


async def _get_prompt_request(req: types.GetPromptRequest) -> types.ServerResult:
    """Handle get_prompt request - returns prompt with populated message template."""
    prompt_name = req.params.name
    arguments = req.params.arguments or {}

    # Look up prompt
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

    # Build message template with argument placeholders
    message_parts = [f"Prompt: {prompt.description}"]

    if arguments:
        message_parts.append("\nArguments:")
        for arg_name, arg_value in arguments.items():
            message_parts.append(f"  {arg_name}: {arg_value}")

    message_text = "\n".join(message_parts)

    return types.ServerResult(
        types.GetPromptResult(
            description=prompt.description or "",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=message_text),
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
    LLM_NOTE: Tool dispatcher for axon tools only.
    """
    # Look up tool
    tool = AXON_TOOLS_BY_ID.get(req.params.name)
    if not tool:
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Unknown tool: {req.params.name}",
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


def main() -> None:
    """Entry point for the mcp script."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()


