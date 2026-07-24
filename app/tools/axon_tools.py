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
        "name": "evalAxon",
        "dis": "Evaluate Axon Expression",
        "help": "Execute any Axon expression on the SkySpark server and return results",
        "params": {
            "kind": "Dict",
            "params": {
                "expr": {
                    "name": "expr",
                    "kind": "Str",
                    "help": "Axon expression to evaluate (e.g. 'read(site)' or 'readAll(equip)')",
                    "required": True,
                }
            },
        },
    },
    {
        "name": "readSites",
        "dis": "List Sites",
        "help": "List all site records (buildings/facilities)",
        "params": {"kind": "Dict", "val": {}},
    },
    {
        "name": "readEquips",
        "dis": "List Equipment",
        "help": "List all equipment records",
        "params": {"kind": "Dict", "val": {}},
    },
    {
        "name": "readPoints",
        "dis": "List Points",
        "help": "List all point records",
        "params": {"kind": "Dict", "val": {}},
    },
    {
        "name": "readById",
        "dis": "Read Record By ID",
        "help": "Read a record by its reference ID",
        "params": {
            "kind": "Dict",
            "params": {
                "id": {
                    "name": "id",
                    "kind": "Str",
                    "help": "Record reference ID (e.g. 'p:demo:r:xxx')",
                    "required": True,
                }
            },
        },
    },
]

