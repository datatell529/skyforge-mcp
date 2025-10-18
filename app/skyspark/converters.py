"""Converters for HGrid to MCP types (tools, prompts, resources)"""

from typing import Any, Dict, List, Optional
import mcp.types as types
from .grid import HGrid
from .types import (
    DictExt, ListExt, MarkerExt, NAExt, RemoveExt,
    NumberExt, UriExt, RefExt, SymbolExt, CoordExt,
    DateExt, TimeExt, DateTimeExt, DateRangeExt, DateTimeRangeExt, XStrExt
)


def _to_axon(value: Any) -> str:
    """Convert Python value to Axon expression string

    Args:
        value: Python value to convert

    Returns:
        Axon expression string
    """
    # Extended types with toAxon() method
    if hasattr(value, 'toAxon'):
        return value.toAxon()

    # None -> null
    if value is None:
        return "null"

    # bool -> true/false
    if isinstance(value, bool):
        return "true" if value else "false"

    # str -> quoted string, escape quotes
    if isinstance(value, str):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    # int/float -> number literal
    if isinstance(value, (int, float)):
        return str(value)

    # dict -> {key:val, ...}
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            parts.append(f"{k}:{_to_axon(v)}")
        return "{" + ", ".join(parts) + "}"

    # list -> [item1, item2, ...]
    if isinstance(value, list):
        parts = [_to_axon(item) for item in value]
        return "[" + ", ".join(parts) + "]"

    # Fallback: convert to string
    return str(value)


def extract_plain_value(value: Any) -> Any:
    """Recursively convert extended Haystack types to plain Python types

    Args:
        value: Value with potential extended types

    Returns:
        Plain Python value (dict, list, str, int, float, bool, None)
    """
    # Primitives - pass through first
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Special handling for Haystack sentinel types (check BEFORE dict/list)
    if isinstance(value, RemoveExt):
        return None  # Remove marker becomes null in JSON
        
    if isinstance(value, NAExt):
        return None  # NA becomes null in JSON

    if isinstance(value, MarkerExt):
        return True  # Marker becomes boolean true in JSON
    
    # NumberExt - extract just the numeric value for JSON schema validation
    if isinstance(value, NumberExt):
        return value.val  # Return numeric value, ignore unit

    # DictExt/dict - recurse into dict (check BEFORE toStr)
    if isinstance(value, (dict, DictExt)):
        return {k: extract_plain_value(v) for k, v in value.items()}

    # ListExt/list - recurse into list (check BEFORE toStr)
    if isinstance(value, (list, ListExt)):
        return [extract_plain_value(item) for item in value]

    # Extended types with toStr() - convert to string
    if hasattr(value, 'toStr'):
        return value.toStr()

    # Fallback - convert to string
    return str(value)


def convert_haystack_to_json_schema(haystack_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Haystack type schema to JSON Schema

    Args:
        haystack_schema: Schema with Haystack kinds (Ref, Str, Number, etc)

    Returns:
        JSON Schema compatible with MCP
    """
    schema_kind = haystack_schema.get("kind", "Dict")
    
    # Handle empty params (val) vs params with values (vals/params)
    if "vals" in haystack_schema:
        properties_raw = haystack_schema.get("vals", {})
    elif "params" in haystack_schema:
        properties_raw = haystack_schema.get("params", {})
    else:
        # Empty params case: {"kind":"Dict", "val":{}}
        properties_raw = {}
    
    # Convert list format to dict format for processing
    if schema_kind == "List" and isinstance(properties_raw, list):
        properties = {item["name"]: item for item in properties_raw if "name" in item}
    elif isinstance(properties_raw, dict):
        properties = properties_raw
    else:
        properties = {}

    # Map Haystack types to JSON Schema types
    type_map = {
        "Str": "string",
        "Number": "number",
        "Bool": "boolean",
        "List": "array",
        "Dict": "object",
        "Ref": "string",  # Refs as strings in JSON Schema
        "Uri": "string",
        "Date": "string",
        "Time": "string",
        "DateTime": "string",
        "Marker": "boolean",  # Marker as boolean
        "NA": "null",
        "Remove": "null",
        "Symbol": "string",
        "Coord": "string",
        "XStr": "string",
    }

    # Format hints for LLM understanding
    format_hints = {
        "Ref": "Format: @id (e.g., @p:demo:r:2f916364-3c4439be)",
        "Uri": "Format: `uri` (e.g., `https://example.com`)",
        "Date": "Format: YYYY-MM-DD (e.g., 2025-10-09)",
        "Time": "Format: HH:MM:SS (e.g., 10:00:00)",
        "DateTime": "Format: ISO 8601 with timezone (e.g., 2025-10-10T17:50:06.427Z)",
        "Marker": "Format: marker() or true",
        "Number": "Format: numeric value with optional unit (e.g., 45kW or 23.5)",
        "Symbol": "Format: ^symbolName (e.g., ^elec-meter)",
        "Coord": "Format: coord(lat,lng) (e.g., coord(37.5458,-77.4492))",
        "NA": "Format: na() for not available",
        "Remove": "Format: removeMarker() for tag removal",
        "List": "Format: JSON array (e.g., [\"ahu\", 10, true])",
        "Dict": "Format: JSON object (e.g., {\"key\": \"value\"})",
        "Str": "Format: string value",
        "Bool": "Format: true or false",
        "XStr": "Format: xstr(\"type\", \"value\")",
    }

    json_properties = {}
    required_props = []

    for prop_name, prop_def in properties.items():
        if isinstance(prop_def, dict):
            prop_kind = prop_def.get("kind", "Str")
            json_type = type_map.get(prop_kind, "string")

            # Build description with format hint
            base_description = prop_def.get("help", f"Parameter {prop_name}")
            format_hint = format_hints.get(prop_kind, "")

            if format_hint:
                description = f"{base_description}. {format_hint}"
            else:
                description = base_description

            json_prop = {
                "type": json_type,
                "description": description
            }

            # Add enum if present
            if "enum" in prop_def:
                json_prop["enum"] = prop_def["enum"]

            # Add default if present (convert extended types)
            if "default" in prop_def:
                json_prop["default"] = extract_plain_value(prop_def["default"])

            # Add default if present (convert extended types)
            if "defVal" in prop_def:
                json_prop["default"] = extract_plain_value(prop_def["defVal"])

            # Check if property is required (property-level flag or marker object)
            required_val = prop_def.get("required")
            if required_val is True or (isinstance(required_val, dict) and required_val.get("_kind") == "marker"):
                required_props.append(prop_name)

            json_properties[prop_name] = json_prop

    # Also check for schema-level required array (fallback)
    schema_level_required = haystack_schema.get("required", [])
    if schema_level_required:
        required_props.extend(schema_level_required)

    # Remove duplicates while preserving order
    required_props = list(dict.fromkeys(required_props))

    return {
        "type": "object",
        "properties": json_properties,
        "required": required_props
    }


def hgrid_to_tools(hgrid: HGrid) -> List[types.Tool]:
    """Convert HGrid from fetchMcpTools() to list of MCP Tool objects

    Args:
        hgrid: HGrid with tool definitions

    Returns:
        List of mcp.types.Tool objects
    """
    tools = []

    for row in hgrid.rows:
        # Extract plain dict from DictExt
        plain_row = extract_plain_value(row)

        if not isinstance(plain_row, dict):
            continue

        # Extract required fields
        name = plain_row.get("name")
        if not name:
            continue

        title = plain_row.get("dis", name)
        description = plain_row.get("help", "")

        # Extract and convert params, capturing the params kind and order
        input_schema_raw = plain_row.get("params")
        params_kind = "Dict"  # Default to Dict
        params_order = []  # Track parameter order for List kind
        if input_schema_raw and isinstance(input_schema_raw, dict):
            params_kind = input_schema_raw.get("kind", "Dict")
            
            # Capture parameter order for List kind
            if params_kind == "List" and "vals" in input_schema_raw:
                vals = input_schema_raw["vals"]
                if isinstance(vals, list):
                    params_order = [item["name"] for item in vals if "name" in item]
            
            input_schema = convert_haystack_to_json_schema(input_schema_raw)
        else:
            # Default empty schema
            input_schema = {
                "type": "object",
                "properties": {},
                "required": []
            }

        # LLM_NOTE: Base meta for Axon tools
        # Store paramsKind to determine how to convert arguments during tool call execution
        tool_meta = {
            "axon": True,
            "paramsKind": params_kind,  # Track whether params are List or Dict
        }
        
        # Add params order for List kind (needed to convert dict args to positional list)
        if params_order:
            tool_meta["paramsOrder"] = params_order

        # Create Tool object
        tool = types.Tool(
            name=name,
            title=title,
            description=description,
            inputSchema=input_schema,
            _meta=tool_meta
        )

        tools.append(tool)

    return tools


def hgrid_to_prompts(hgrid: HGrid) -> List[types.Prompt]:
    """Convert HGrid from fetchMcpPrompts() to list of MCP Prompt objects
    
    LLM_NOTE: Grid schema - name, description, arguments
    Each row has: name (Str), description (Str), arguments (List of Dicts)
    
    Args:
        hgrid: HGrid with prompt definitions
        
    Returns:
        List of mcp.types.Prompt objects
    """
    prompts = []
    
    for row in hgrid.rows:
        # Extract plain dict from extended types
        plain_row = extract_plain_value(row)
        
        if not isinstance(plain_row, dict):
            continue
            
        # Extract required fields
        name = plain_row.get("name")
        if not name:
            continue
            
        description = plain_row.get("description", "")
        
        # Extract arguments list
        arguments_raw = plain_row.get("arguments", [])
        arguments = []
        
        if isinstance(arguments_raw, list):
            for arg in arguments_raw:
                if isinstance(arg, dict):
                    arg_name = arg.get("name")
                    if arg_name:
                        prompt_arg = types.PromptArgument(
                            name=arg_name,
                            description=arg.get("description", ""),
                            required=arg.get("required", False)
                        )
                        arguments.append(prompt_arg)
        
        # Create Prompt object
        prompt = types.Prompt(
            name=name,
            description=description,
            arguments=arguments
        )
        
        prompts.append(prompt)
    
    return prompts

