#!/bin/bash
cd /mnt/hermes-work/skyforge-mcp-fin
export PYTHONPATH=/mnt/hermes-work/skyforge-mcp-fin:$PYTHONPATH
exec /mnt/hermes-work/skyforge-mcp-fin/.venv/bin/python skyforge_mcp/main.py
