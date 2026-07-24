import logging
import os
from typing import Any, Dict, List, Union, cast
import mcp.types as types
from phable import Grid, GridCol, open_haxall_client
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
        """Fetch MCP tools from SkySpark by reading func records

        Returns:
            List of MCP tools (with extended types)
        """
        try:
            # Read all functions with skyforgeMcp tag from SkySpark
            result = self.eval("readAll(func and skyforgeMcp)")
            tools_from_db = []
            for row in result.rows:
                d = dict(row)
                name = d.get("name", "")
                if name and name not in ("fetchMcpTools", "fetchMcpPrompts"):
                    tools_from_db.append({
                        "name": name,
                        "dis": d.get("name", name),
                        "help": d.get("name", name),
                        "params": {"kind": "Dict", "val": {}},
                    })
            # Add DB tools + hardcoded tools
            all_tools = tools_from_db + list(HARDCODED_TOOLS)
            fallback_grid = Grid(meta={}, cols=[GridCol(name="name")], rows=[])
            hgrid = HGrid(fallback_grid)
            for tool in all_tools:
                hgrid.grid.rows.append(tool)
            return hgrid_to_tools(hgrid)
        except Exception as e:
            logger.warning(f"Failed to read func records: {e}, falling back to hardcoded tools")
            # Return only hardcoded tools
            fallback_grid = Grid(meta={}, cols=[GridCol(name="name")], rows=[])
            hgrid = HGrid(fallback_grid)
            for tool in HARDCODED_TOOLS:
                hgrid.grid.rows.append(tool)
            return hgrid_to_tools(hgrid)

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

    def handleToolCall(self, name: str, params: Union[Dict[str, Any], List[Any]], params_kind: str = "Dict", params_order: List[str] = None) -> "HGrid":
        """Execute tool call on SkySpark

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

        # Map built-in tool names to Axon expressions
        BUILTIN_TOOLS = {
            "about": "about()",
            "readSites": "readAll(site)",
            "readEquips": "readAll(equip)",
            "readPoints": "readAll(point)",
        }

        if name in BUILTIN_TOOLS:
            expression = BUILTIN_TOOLS[name]
            return self.eval(expression)
        elif name == "evalAxon":
            expr = params.get("expr", "") if isinstance(params, dict) else ""
            return self.eval(expr)
        elif name == "readById":
            rid = params.get("id", "") if isinstance(params, dict) else ""
            return self.eval(f'readById({rid})')

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
                if not params:
                    # No params - pass empty list
                    expression = f"call({to_axon(name)}, [])"
                else:
                    params_axon = to_axon(params)
                    expression = f"call({to_axon(name)}, [{params_axon}])"
            else:
                # If list provided but Dict expected, wrap in dict
                params_axon = to_axon({"items": params})
                expression = f"call({to_axon(name)}, [{params_axon}])"
        
        # Execute via eval (which will log on error) - return HGrid for dual format support
        return self.eval(expression)
