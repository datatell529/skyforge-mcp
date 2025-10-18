"""Hardcoded MCP tools for SkySpark
Tools converted from axon_tools.py decorator format to HGrid row format
"""

# Tool rows match expected schema: name, dis, help, params
HARDCODED_TOOLS = [
    {
        "name": "about",
        "dis": "About Server",
        "help": "Returns a dict with server information",
        "params": {"kind": "Dict", "val": {}},
    },
    {
        "name": "defCompTest",
        "dis": "Test defComp function",
        "help": "Tests a defComp function type",
        "params": {
            "kind": "Dict",
            "vals": {
                "stringInput": {"required": True, "kind": "Str", "help": "Test string input", "default": "hey"},
                "numberInput": {"required": True, "kind": "Number", "help": "Test number input", "default": 12},
                "refInput": {"required": True, "kind": "Ref", "help": "Test ref input"},
            }
        }
    },
    {
        "name": "basicTest",
        "dis": "Basic Func Test",
        "help": "Test a basic function type",
        "params": {
            "kind": "List",
            "vals": [
                {"required": True, "name": "stringInput", "kind": "Str", "help": "Test string input", "default": "hey"},
                {"required": True, "name": "numberInput", "kind": "Number", "help": "Test number input", "default": 12},
                {"required": True, "name": "refInput", "kind": "Ref", "help": "Test ref input"},
            ]
        }
    },
]

