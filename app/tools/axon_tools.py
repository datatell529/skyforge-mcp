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
        "dis": "Read Record By ID (Legacy)",
        "help": "Read a record by its reference ID. Consider using readRecord instead for better error handling.",
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
    {
        "name": "readRecord",
        "dis": "Read Record By ID",
        "help": "Read a single record by its reference ID. Returns the full record with all tags. Raises error if record not found.",
        "params": {
            "kind": "Dict",
            "params": {
                "id": {
                    "name": "id",
                    "kind": "Str",
                    "help": "Record reference ID (e.g. 'p:test1:r:xxx' or '@p:test1:r:xxx')",
                    "required": True,
                }
            },
        },
    },
    {
        "name": "commitRemove",
        "dis": "Commit Remove (Delete)",
        "help": "Remove/delete a record by its reference ID. Permanently deletes the record from the database.",
        "params": {
            "kind": "Dict",
            "params": {
                "target_id": {
                    "name": "target_id",
                    "kind": "Str",
                    "help": "Reference ID of the record to remove (e.g. 'p:test1:r:xxx')",
                    "required": True,
                }
            },
        },
    },
    {
        "name": "batchCommitAdd",
        "dis": "Batch Commit Add Records",
        "help": "Add one or more new records to the database. Accepts a JSON array of record objects with tag definitions.",
        "params": {
            "kind": "Dict",
            "params": {
                "records": {
                    "name": "records",
                    "kind": "Str",
                    "help": "JSON string of records to add. Either a single object or array of objects. Each object is a dict of tag name to value. Example: '[{\"dis\": \"MySite\", \"site\": \"M\", \"area\": \"1000 ft²\"}]'",
                    "required": True,
                }
            },
        },
    },
    {
        "name": "commitUpdate",
        "dis": "Commit Update",
        "help": "Update tags on an existing record by reference ID. Provide update_tags as a JSON string.",
        "params": {
            "kind": "Dict",
            "params": {
                "target_id": {
                    "name": "target_id",
                    "kind": "Str",
                    "help": "Reference ID of the record to update (e.g. 'p:demo:r:xxx')",
                    "required": True,
                },
                "update_tags": {
                    "name": "update_tags",
                    "kind": "Str",
                    "help": "JSON string of tags to set on the record. Example: '{\"dis\": \"New Name\", \"area\": \"2000 ft²\"}'",
                    "required": True,
                },
            },
        },
    },
    {
        "name": "readAll",
        "dis": "Read All Records",
        "help": "Read all records matching a filter expression, with cleaned output for LLM consumption",
        "params": {
            "kind": "Dict",
            "params": {
                "axon_filter": {
                    "name": "axon_filter",
                    "kind": "Str",
                    "help": "Axon filter expression (e.g. 'equip' or 'site')",
                    "required": True,
                }
            },
        },
    },
]

