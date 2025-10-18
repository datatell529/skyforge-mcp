import logging
import os
from typing import Any, Dict, List, Union
import mcp.types as types
from phable import Grid, open_haxall_client
from dotenv import load_dotenv
from .grid import HGrid
from .types import (
    MarkerExt, NAExt, RemoveExt, NumberExt, UriExt, RefExt, SymbolExt,
    CoordExt, DateExt, TimeExt, DateTimeExt, DateRangeExt, DateTimeRangeExt,
    XStrExt, ListExt, DictExt
)
from app.tools.axon_tools import HARDCODED_TOOLS
from .converters import hgrid_to_tools, _to_axon

# Load .env file at module import
load_dotenv()

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
        # Load env vars
        self.uri = os.getenv("SKYSPARK_URI")
        self.username = os.getenv("SKYSPARK_USERNAME")
        self.password = os.getenv("SKYSPARK_PASSWORD")

        if not all([self.uri, self.username, self.password]):
            raise ValueError(
                "Missing required environment variables:\n"
                "- SKYSPARK_URI\n"
                "- SKYSPARK_USERNAME\n"
                "- SKYSPARK_PASSWORD"
            )

        # Test connection
        self._test_connection()

    def _test_connection(self) -> None:
        """Test connection via about() call"""
        with self._get_client() as client:
            client.about()

    def _get_client(self):
        """Get client context manager for internal use"""
        return open_haxall_client(
            self.uri,
            self.username,
            self.password,
        )

    def eval(self, expression: str) -> HGrid: 
        """Evaluate Axon expression on SkySpark server

        Args:
            expression: Axon expression to evaluate

        Returns:
            HGrid if result is Grid (with extended types), otherwise raw result
        """
        try:
            with self._get_client() as client:
                result = client.eval(expression)
                # Convert phable Grid to HGrid for extended type support
                if isinstance(result, Grid):
                    return HGrid(result)
                return result
        except Exception as e:
            logger.error(f"Failed to eval Axon expression: {expression}", exc_info=True)
            raise

    def fetchMcpTools(self) -> List[types.Tool]:
        """Fetch MCP tools from SkySpark via eval

        Returns:
            HGrid with MCP tools (with extended types)
        """
        result = self.eval("fetchMcpTools()")

        # Append hardcoded axon tools to grid rows
        for tool in HARDCODED_TOOLS:
            result.grid.rows.append(tool)

        converted_result = hgrid_to_tools(result)

        return converted_result

    def fetchMcpPrompts(self) -> List[types.Prompt]:
        """Fetch MCP prompts from SkySpark via eval

        Returns:
            List of MCP prompts
        """
        result = self.eval("fetchMcpPrompts()")
        
        from .converters import hgrid_to_prompts
        converted_result = hgrid_to_prompts(result)
        
        return converted_result

    def handleToolCall(self, name: str, params: Union[Dict[str, Any], List[Any]], params_kind: str = "Dict", params_order: List[str] = None) -> "HGrid":
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
            
        
        # Build call expression based on params_kind
        if params_kind == "List":
            # For List kind: call("name", [param1, param2, ...])
            # Each list item becomes a positional argument
            if isinstance(params, list):
                # Already a list, convert each item separately
                args_parts = [_to_axon(item) for item in params]
                args_str = ", ".join(args_parts)
            elif isinstance(params, dict):
                # LLM sends dict but we need list - extract values using params_order
                if params_order:
                    args_parts = [_to_axon(params.get(k)) for k in params_order]
                else:
                    # Fallback to sorted keys if no order provided
                    sorted_keys = sorted(params.keys())
                    args_parts = [_to_axon(params[k]) for k in sorted_keys]
                args_str = ", ".join(args_parts)
            else:
                # Fallback: single argument
                args_str = _to_axon(params)
            expression = f"call({_to_axon(name)}, [{args_str}])"
        else:
            # For Dict kind (default): call("name", [dict])
            # Params dict is wrapped in array as single argument
            if isinstance(params, dict):
                params_axon = _to_axon(params)
            else:
                # If list provided but Dict expected, wrap in dict
                params_axon = _to_axon({"items": params})
            expression = f"call({_to_axon(name)}, [{params_axon}])"
        
        # Execute via eval (which will log on error) - return HGrid for dual format support
        return self.eval(expression)
