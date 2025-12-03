import logging
import os
from typing import cast
import mcp.types as types
from phable.kinds import Grid
from phable.haxall_client import open_haxall_client
from dotenv import load_dotenv
from .grid import HGrid
from ..tools.axon_tools import HARDCODED_TOOLS
from . import converters as _converters
from typing import TypedDict

# Public re-exports for clarity
hgrid_to_tools = _converters.hgrid_to_tools
convert_haystack_to_json_schema = _converters.convert_haystack_to_json_schema

def to_axon(value: object) -> str:
    """Public wrapper around private converters._to_axon to satisfy linters."""
    return _converters._to_axon(value)


class RawTool(TypedDict, total=False):
    name: str
    dis: str
    help: str
    params: dict[str, object]

# Load .env file at module import (assign to '_' to avoid lint warning)
_ = load_dotenv()

logger = logging.getLogger(__name__)



class SkySpark:
    """SkySpark client with connection management and eval methods

    Attributes:
        uri: SkySpark server URI
        username: Authentication username
        password: Authentication password
        content_type: Data format (zinc or json)
    """

    def __init__(self):
        """Initialize SkySpark client, load env, test connection

        Args:
            content_type: Format for HTTP data exchange ("json" or "zinc")

        Raises:
            ValueError: If required env vars missing
            Exception: If connection test fails
        """
        # Load env vars (locals first to keep types precise)
        uri = os.getenv("SKYSPARK_URI")
        username = os.getenv("SKYSPARK_USERNAME")
        password = os.getenv("SKYSPARK_PASSWORD")

        if not uri or not username or not password:
            raise ValueError(
                "\n".join(
                    [
                        "Missing required environment variables:",
                        "- SKYSPARK_URI",
                        "- SKYSPARK_USERNAME",
                        "- SKYSPARK_PASSWORD",
                    ],
                ),
            )

        # Promote to validated, non-optional attributes
        self.uri: str = uri
        self.username: str = username
        self.password: str = password

        # Test connection
        self._test_connection()

    def _test_connection(self) -> None:
        """Test connection via about() call"""
        with self._get_client() as client:
            _ = client.about()

    def _get_client(self):
        """Get client context manager for internal use"""
        return open_haxall_client(
            self.uri,
            self.username,
            self.password,
        )

    def eval(self, expression: str) -> HGrid: 
        """Evaluate an Axon expression on the SkySpark server and always
        return an ``HGrid`` wrapper.

        Args:
            expression: Axon expression to evaluate

        Returns:
            HGrid with extended types. If the server returns a non-grid value,
            an empty grid wrapper is returned to keep call-sites safe.
        """
        try:
            with self._get_client() as client:
                result = client.eval(expression)
                if isinstance(result, Grid):
                    return HGrid(result)
                logger.warning("Eval returned non-grid result; returning empty grid fallback")
                return HGrid(Grid(meta={}, cols=[], rows=[]))
        except Exception:
            logger.error(f"Failed to eval Axon expression: {expression}", exc_info=True)
            raise

    def fetchMcpTools(self) -> list[types.Tool]:
        """Fetch MCP tools from SkySpark via eval

        Returns:
            HGrid with MCP tools (with extended types)
        """
        try:
            result = self.eval("fetchMcpTools()")
            # Append hardcoded axon tools to grid rows
            for tool in HARDCODED_TOOLS:
                result.grid.rows.append(tool)
            return hgrid_to_tools(result)
        except Exception:
            # Fallback: if SkySpark project doesn't define fetchMcpTools(),
            # expose the built-in tools only (e.g., 'about') so the client can still work.
            fallback_tools: list[types.Tool] = []
            for row in HARDCODED_TOOLS:
                name_val = row.get("name")
                name = str(name_val) if isinstance(name_val, (str, int, float)) else None
                if not name:
                    continue
                title_val = row.get("dis", name)
                title = str(title_val) if isinstance(title_val, (str, int, float)) else name
                desc_val = row.get("help", "")
                description = str(desc_val) if isinstance(desc_val, (str, int, float)) else ""
                params_schema_raw = row.get("params", {"kind": "Dict", "val": {}})
                params_schema = params_schema_raw if isinstance(params_schema_raw, dict) else {"kind": "Dict", "val": {}}
                params_kind = str(params_schema.get("kind", "Dict"))
                input_schema = convert_haystack_to_json_schema(params_schema)
                tool = types.Tool(
                    name=name,
                    title=title,
                    description=description,
                    inputSchema=input_schema,
                    _meta={"axon": True, "paramsKind": params_kind},
                )
                fallback_tools.append(tool)
            return fallback_tools

    def fetchMcpPrompts(self) -> list[types.Prompt]:
        """Fetch MCP prompts from SkySpark via eval

        Returns:
            List of MCP prompts
        """
        try:
            result = self.eval("fetchMcpPrompts()")
            from .converters import hgrid_to_prompts
            return hgrid_to_prompts(result)
        except Exception:
            # No prompts defined in SkySpark – return empty list gracefully
            return []
        
    def handleToolCall(self, name: str, params: dict[str, object] | list[object], params_kind: str = "Dict", params_order: list[str] | None = None) -> "HGrid":
        """Execute tool call on SkySpark via call() function

        Args:
            name: Tool name to call
            params: Parameters (dict or list) with Python values
            params_kind: "Dict" or "List" indicating expected params structure
            params_order: For List kind, ordered list of parameter names

        Returns:
            HGrid with grid result (supports both .toJson() and .toZinc())
        """
        if params_order is None:
            params_order = []
            
        
        # Local fallback for the built-in 'about' tool (doesn't require axon call())
        if name == "about":
            with self._get_client() as client:
                result = client.about()
                if isinstance(result, Grid):
                    return HGrid(result)
                # Construct an explicit empty grid (phable.Grid requires meta, cols, rows)
                return HGrid(Grid(meta={}, cols=[], rows=[]))  # empty grid fallback
        
        # Build call expression based on params_kind
        if params_kind == "List":
            # For List kind: call("name", [param1, param2, ...])
            # Each list item becomes a positional argument
            if isinstance(params, list):
                # Already a list, convert each item separately
                args_parts = [to_axon(item) for item in params]
                args_str = ", ".join(args_parts)
            elif isinstance(params, dict):
                # LLM sends dict but we need list - extract values using params_order
                if params_order:
                    args_parts = [to_axon(params.get(k)) for k in params_order]
                else:
                    # Fallback to sorted keys if no order provided
                    sorted_keys = sorted(params.keys())
                    args_parts = [to_axon(params[k]) for k in sorted_keys]
                args_str = ", ".join(args_parts)
            else:
                # Fallback: single argument
                args_str = to_axon(params)
            expression = f"call({to_axon(name)}, [{args_str}])"
        else:
            # For Dict kind (default): call("name", [dict])
            # Params dict is wrapped in array as single argument
            if isinstance(params, dict):
                params_axon = to_axon(params)
            else:
                # If list provided but Dict expected, wrap in dict
                params_axon = to_axon({"items": params})
            expression = f"call({to_axon(name)}, [{params_axon}])"
        
        # Execute via eval (which will log on error) - return HGrid for dual format support
        return self.eval(expression)
