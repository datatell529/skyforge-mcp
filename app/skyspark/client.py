import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Union, cast
import mcp.types as types
from phable import Grid, GridCol, open_haxall_client, Remove, Ref
from phable.haystack_client import CallError, UnknownRecError
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

    def eval(self, expression: str, operation_type: str = "read") -> HGrid:
        """Evaluate an Axon expression on the SkySpark server and always
        return an ``HGrid`` wrapper.

        Args:
            expression: Axon expression to evaluate
            operation_type: "read" or "write" — controls error handling
                - "read": CallError propagates to caller (isError=True)
                - "write": CallError caught, logged, empty grid returned

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
        except CallError:
            if operation_type == "write":
                # FIN 3.1.5 quirk: write operations sometimes return CallError
                # but actually succeed. Return empty grid + log.
                logger.warning(
                    "eval: CallError (FIN 3.1.5 quirk) for %s — returning empty grid",
                    expression,
                )
                return HGrid(Grid(meta={}, cols=[], rows=[]))
            else:
                # Read operations: propagate CallError so callers know it failed
                raise
        except Exception:
            logger.error(f"Failed to eval Axon expression: {expression}", exc_info=True)
            raise

    def _handle_fin_315_call_error(self, error: Exception, operation: str) -> HGrid:
        logger.warning("FIN 3.1.5 quirk: CallError during '%s' — write likely succeeded", operation)
        return HGrid(Grid(meta={}, cols=[], rows=[]))

    def batch_commit_add(self, zinc_str: str) -> HGrid:
        axon_expr = f"ioReadZinc({to_axon(zinc_str)}).map(ioWriteTrio).commit()"
        try:
            with self._get_client() as client:
                result = client.eval(axon_expr)
                if isinstance(result, Grid):
                    return HGrid(result)
                return HGrid(Grid(meta={}, cols=[], rows=[]))
        except CallError as e:
            return self._handle_fin_315_call_error(e, "batch_commit_add")
        except Exception:
            logger.error("batch_commit_add failed", exc_info=True)
            raise

    def commit_add(self, records: Union[Dict[str, Any], List[Dict[str, Any]]]) -> HGrid:
        """Add one or more records using phable's native commit_add.

        This replaces the broken eval-based batchCommitAdd (ioReadZinc/ioWriteTrio
        don't exist in FIN 3.1.5).

        Args:
            records: Single dict or list of dicts with tag definitions

        Returns:
            HGrid with the newly created records (includes ids, mods)
        """
        if isinstance(records, dict):
            records = [records]
        try:
            with self._get_client() as client:
                result = client.commit_add(records)
                if isinstance(result, Grid):
                    return HGrid(result)
                return HGrid(Grid(meta={}, cols=[], rows=[]))
        except Exception:
            logger.error("commit_add failed", exc_info=True)
            raise

    def commit_update(self, ref_id: Union[str, Any], tags: Dict[str, Any]) -> HGrid:
        """Update tags on an existing record using phable's native commit_update.

        Steps:
            1. Read the current record to get 'id' and 'mod'
            2. Apply tag changes to the record dict
            3. Call commit_update() with the modified record

        This is more reliable than the eval-based approach because:
            - Uses standard Haystack commit API endpoint
            - Properly raises on error (no CallError swallowing)
            - Returns full updated record with new mod

        Args:
            ref_id: Reference ID (e.g., "p:test1:r:xxx") or RefExt object
            tags: Dict of tags to set/update

        Returns:
            HGrid with the updated record

        Raises:
            UnknownRecError: If ref_id doesn't exist
            CallError: If commit fails
        """
        rid = str(ref_id).split(" (")[0].lstrip("@")
        try:
            with self._get_client() as client:
                # Step 1: Read current record
                ref = Ref(rid)
                current = client.read_by_id(ref, checked=True)

                # Step 2: Apply changes (use a copy to avoid side effects)
                updated = dict(current)
                for k, v in tags.items():
                    if v is None:
                        # None → Remove marker (Haystack方式删除tag)
                        updated[k] = Remove()
                    else:
                        updated[k] = v

                # Step 3: Commit update
                result = client.commit_update(updated)
                if isinstance(result, Grid):
                    return HGrid(result)
                return HGrid(Grid(meta={}, cols=[], rows=[]))
        except Exception:
            logger.error(f"commit_update failed for ref_id={rid}", exc_info=True)
            raise

    def commit_remove(self, ref_id: Union[str, Any]) -> HGrid:
        """Remove (delete) a record by reference ID using phable's native commit_remove.

        Steps:
            1. Read the current record to get 'id' and 'mod'
            2. Call commit_remove() with id and mod

        Args:
            ref_id: Reference ID (e.g., "p:test1:r:xxx") or RefExt object

        Returns:
            HGrid with confirmation of removal

        Raises:
            UnknownRecError: If ref_id doesn't exist
            CallError: If remove fails
        """
        rid = str(ref_id).split(" (")[0].lstrip("@")
        try:
            with self._get_client() as client:
                # Step 1: Read current record
                ref = Ref(rid)
                current = client.read_by_id(ref, checked=True)
                # commit_remove requires id + mod
                remove_rec = {"id": current.get("id"), "mod": current.get("mod")}
                result = client.commit_remove(remove_rec)
                return HGrid(Grid(meta={"removed": rid}, cols=[GridCol(name="id")], rows=[]))
        except UnknownRecError:
            logger.warning(f"commit_remove: record not found: {rid}")
            raise
        except Exception:
            logger.error(f"commit_remove failed for ref_id={rid}", exc_info=True)
            raise

    def read_record(self, ref_id: Union[str, Any]) -> HGrid:
        """Read a single record by reference ID using phable's native read_by_id.

        This replaces the eval-based readById which swallows errors.
        Uses the standard Haystack read API endpoint.

        Args:
            ref_id: Reference ID (e.g., "p:test1:r:xxx" or "@p:test1:r:xxx") or RefExt object

        Returns:
            HGrid with the record data

        Raises:
            UnknownRecError: If ref_id doesn't exist
        """
        # Strip leading @ if present and convert to string
        rid = str(ref_id).split(" (")[0].lstrip("@")
        try:
            with self._get_client() as client:
                ref = Ref(rid)
                result = client.read_by_id(ref, checked=True)
                # Convert dict result to a single-row grid
                grid = Grid(meta={}, cols=[GridCol(name=k) for k in result.keys()], rows=[result])
                return HGrid(grid)
        except UnknownRecError:
            logger.warning(f"read_record: record not found: {rid}")
            raise
        except Exception:
            logger.error(f"read_record failed for ref_id={rid}", exc_info=True)
            raise

    def clean_grid_for_llm(self, grid: HGrid) -> HGrid:
        """Clean grid for LLM consumption by removing internal columns and normalizing types.

        Changes from original:
            - Ref values now include display name: \"@id (dis)\"
            - NA values preserved as \"NA\" (not converted to None/null)
            - Remove values preserved as \"REMOVE\" (not converted to None/null)
            - Number values show unit: \"45 kW\"
        """
        internal_cols = {"mod", "tz"}
        data = grid.toJson()

        col_dicts = [c for c in data.get("cols", []) if c.get("name") not in internal_cols]
        cols = []
        for c in col_dicts:
            kwargs = {"name": c["name"]}
            if "kind" in c:
                kwargs["kind"] = c["kind"]
            cols.append(GridCol(**kwargs))

        cleaned_rows = []
        for row in data.get("rows", []):
            clean = {}
            for k, v in row.items():
                if k in internal_cols:
                    continue
                if isinstance(v, dict):
                    kind = v.get("_kind")
                    if kind == "marker":
                        clean[k] = True
                    elif kind == "ref":
                        ref_val = "@" + str(v.get("val", ""))
                        dis = v.get("dis")
                        if dis:
                            ref_val += f" ({dis})"
                        clean[k] = ref_val
                    elif kind == "na":
                        clean[k] = "NA"
                    elif kind == "remove":
                        clean[k] = "REMOVE"
                    elif kind == "number":
                        val = v.get("val")
                        unit = v.get("unit")
                        if unit:
                            clean[k] = f"{val} {unit}"
                        else:
                            clean[k] = val
                    elif kind == "dateTime":
                        clean[k] = v.get("val", "")
                    elif kind == "date":
                        clean[k] = v.get("val", "")
                    elif kind == "time":
                        clean[k] = v.get("val", "")
                    elif kind == "coord":
                        clean[k] = f"coord({v.get('lat')}, {v.get('lng')})"
                    elif kind == "uri":
                        clean[k] = str(v.get("val", ""))
                    elif kind == "symbol":
                        clean[k] = "^" + str(v.get("val", ""))
                    else:
                        clean[k] = v
                else:
                    clean[k] = v
            cleaned_rows.append(clean)

        new_grid = Grid(meta={}, cols=cols, rows=cleaned_rows)
        return HGrid(new_grid)

    def readAll(self, axon_filter: str) -> HGrid:
        expression = f"readAll({axon_filter})"
        result = self.eval(expression)
        return self.clean_grid_for_llm(result)

    # ---- Tool cache (SB-07) ----
    _tool_cache: Optional[list[types.Tool]] = None
    _tool_cache_time: float = 0.0
    _tool_cache_ttl: float = 60.0  # seconds

    def _invalidate_tool_cache(self) -> None:
        """Force next fetchMcpTools call to re-fetch from FIN."""
        self._tool_cache = None
        self._tool_cache_time = 0.0

    def _get_auto_discover_pods(self) -> list[str]:
        """Read configured pod names for auto-discovery from env.

        SB-06: Supports MCP_AUTO_DISCOVER_PODS as JSON array or
        comma-separated list.  Defaults to ["finCopilot"].
        """
        raw = os.getenv("MCP_AUTO_DISCOVER_PODS", '["finCopilot"]')
        if not raw or raw.strip().lower() in ("", "none", "null"):
            return []
        try:
            pods = json.loads(raw)
            if isinstance(pods, list):
                return [p.strip() for p in pods if p.strip()]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: treat as comma-separated
        return [p.strip() for p in raw.split(",") if p.strip()]

    def _discover_pod_functions(self) -> list[dict]:
        """Discover @Axon functions from configured pods via defs() lookup.

        SB-05 / SB-06: Uses FIN's defs() API (which works on FIN 3.1.5)
        instead of Fantom reflection (sys::Pod / sys::Type) which is not
        supported.  Each def row from a target lib that has a 'func' marker
        is treated as a callable tool.

        Returns:
            List of tool dicts with name, dis, help, src, params keys.
        """
        discovered: list[dict] = []
        pods = self._get_auto_discover_pods()
        if not pods:
            return discovered

        try:
            # Fetch all defs once and filter in Python
            all_defs = self.eval("defs()")
        except Exception as exc:
            logger.debug("pod auto-discovery: defs() failed: %s", exc)
            return discovered

        seen_names: set[str] = set()
        for row in all_defs.rows:
            d = dict(row)
            lib_sym = d.get("lib")
            if not lib_sym:
                continue
            lib_str = str(lib_sym)
            # Strip leading '^lib:' prefix to get pod name
            lib_name = lib_str.removeprefix("^lib:") if lib_str.startswith("^lib:") else lib_str
            if lib_name not in pods:
                continue
            # Must have 'func' marker
            if not d.get("func"):
                continue
            def_sym = d.get("def", "")
            def_name = str(def_sym)
            # Strip leading '^func:' to get callable name
            func_name = def_name.removeprefix("^func:") if def_name.startswith("^func:") else def_name
            if not func_name:
                continue
            if func_name in seen_names:
                continue
            seen_names.add(func_name)

            # Extract tool help from doc string
            doc = str(d.get("doc", "") or "")

            # Build params schema from doc + curated signatures (SB-08)
            params_schema = self._parse_params_from_doc(doc, func_name)

            discovered.append({
                "name": func_name,
                "dis": func_name,
                "help": doc or f"{lib_name} auto: {func_name}",
                "src": "",
                "params": params_schema,
            })

        logger.info("pod auto-discovery: found %d functions from %s", len(discovered), pods)
        return discovered

    _FN_SIGNATURES: dict[str, list[tuple[str, str]]] = {
        # Known finCopilot function signatures (name -> [(param, type), ...])
        # These cannot be reflected in FIN 3.1.5, so we maintain a curated list.
        "finMcpChatStart": [("messagesJson", "Str"), ("capability", "Str"), ("contextJson", "Str"), ("modelOverride", "Str"), ("effort", "Str")],
        "finMcpChatPoll": [("sessionId", "Str"), ("after", "Number")],
        "finMcpChatCancel": [("sessionId", "Str")],
        "finMcpQuery": [("expr", "Str"), ("limit", "Number")],
        "finMcpDescribeEntity": [("ref", "Str")],
        "finMcpReadCurrent": [("refs", "List")],
        "finMcpReadHistory": [("ref", "Str"), ("range", "Str")],
        "finMcpListAlarms": [],
        "finMcpCriticalAlarms": [],
        "finMcpEquipTopology": [("ref", "Str")],
        "finMcpComputeKpi": [],
        "finMcpEnergyBreakdown": [],
        "finMcpEnergyFlow": [],
        "finMcpChillerPerformance": [],
        "finMcpProposeWrite": [("pointRef", "Str"), ("value", "Str"), ("unit", "Str")],
        "finMcpRollback": [("sessionId", "Str")],
        "finMcpSetLlm": [("provider", "Str"), ("model", "Str"), ("apiKey", "Str"), ("baseUrl", "Str")],
        "finMcpListJobs": [],
        "finMcpListMemory": [("scope", "Str")],
        "finMcpAggregateQuery": [("specJson", "Str")],
        "finMcpDetectAnomalies": [("pointRef", "Str"), ("method", "Str"), ("window", "Number")],
        "finMcpHealth": [],
        "finMcpIngestDoc": [("text", "Str"), ("docRef", "Str"), ("equipType", "Str"), ("equipRef", "Str")],
        "finMcpIngestUrl": [("url", "Str")],
        "finMcpRetrieveDocs": [("query", "Str"), ("maxResults", "Number")],
        "finMcpListDocs": [],
        "finMcpDeleteDoc": [("docRef", "Str")],
        "finMcpCreateWorkOrder": [("description", "Str"), ("priority", "Str")],
        "finMcpCloseWorkOrder": [("workOrderId", "Str"), ("resolution", "Str")],
        "finMcpListWorkOrders": [("status", "Str")],
        "finMcpSavingsPotential": [],
        "finMcpReport": [("range", "Str")],
        "finMcpEnergyReport": [("range", "Str"), ("priorRange", "Str")],
        "finMcpCarbon": [("range", "Str")],
        "finMcpSuggest": [("query", "Str"), ("maxResults", "Number")],
        "finMcpAutoTag": [("text", "Str")],
        "finMcpTagVocab": [],
        "finMcpUsage": [],
        "finMcpEval": [],
        "finMcpEvalGuardrails": [],
        "finMcpEvalTrajectories": [],
        "finMcpPerfCurve": [("equipRef", "Str"), ("range", "Str")],
        # ── coolMatrix functions (SB-06) ────────────────────────────────────
        "cmCleanHistory": [("pointIds", "List"), ("days", "Number")],
        "cmChillerPerfNow": [("chillerRef", "Str")],
        "cmCreateAndBindBatch": [("bindings", "List")],
        "cmTopologyPush": [("siteRef", "Str")],
        "cmAiBoxBuildPlantDataset": [("siteRef", "Str"), ("force", "Bool")],
        "cmAiInfer": [("modelRef", "Str"), ("input", "Dict")],
        "cmAiPredict": [("modelRef", "Str"), ("horizon", "Str")],
        "cmAiOptimize": [("siteRef", "Str"), ("objective", "Str")],
        "cmEnergyBreakdown": [("siteRef", "Str"), ("range", "Str")],
        "cmEnergyCompare": [("siteRef", "Str"), ("rangeA", "Str"), ("rangeB", "Str")],
        "cmPlantCopNow": [("siteRef", "Str")],
        "cmPumpPerformance": [("pumpRef", "Str")],
        "cmTowerPerformance": [("towerRef", "Str")],
        "cmTowerFreqControl": [("towerRef", "Str"), ("sp", "Number")],
        "cmActivateStrategy": [("strategyRef", "Str")],
        "cmStopStrategyDevices": [("strategyRef", "Str")],
        "cmGetPlantTree": [("siteRef", "Str")],
        "cmGetSites": [],
        "cmGetAllEquipment": [("siteRef", "Str")],
        "cmGetChillers": [("siteRef", "Str")],
        "cmWritePoint": [("pointRef", "Str"), ("value", "Number"), ("reason", "Str")],
        "cmSafetyStatus": [("equipRef", "Str")],
        "cmLicenseStatus": [],
        "cmHelpDocs": [],
        "cmFsmStatus": [("siteRef", "Str")],
        "cmDeleteGroupStrategy": [("strategyRef", "Str")],
        "cmTrimActionAudit": [("siteRef", "Str"), ("days", "Number")],
        "cmRotateAllGroupsNative": [("siteRef", "Str")],
        "cmWtfSnapshot": [("siteRef", "Str")],
        "cmAiBoxStatus": [("siteRef", "Str")],
        "cmAiModels": [("siteRef", "Str")],
        "cmEnergyMonthly": [("siteRef", "Str"), ("year", "Number")],
        "cmChillerOptRunCycle": [("siteRef", "Str")],
        "cmEngineAllowed": [("siteRef", "Str")],
        "cmStrategyMainLoop": [("siteRef", "Str")],
        # ── chillerOpt functions ─────────────────────────────────────────
        "chillerOptFeatures": [("siteRef", "Str"), ("range", "Str")],
        "chillerOptReloadModel": [("siteRef", "Str")],
        "chillerOptRunCycle": [("siteRef", "Str")],
        "chillerOptScore": [("siteRef", "Str"), ("range", "Str")],
    }

    def _parse_params_from_doc(self, doc: str, func_name: str = "") -> dict:
        """Parse parameter descriptions from a function's doc string.

        SB-08: Converts Axon-style parameter docs to Haystack-style params dict.
        Uses a priority chain:
          1. Curated `_FN_SIGNATURES` dict (most reliable)
          2. "paramName: description" pattern in doc
          3. Chinese "paramName 为..." pattern in doc
          4. Example call parsing in doc
          5. Returns empty Dict (no params needed)

        Returns:
            Haystack-style params dict: {"kind": "Dict", "params": {...}}
        """
        # Strategy 1: Curated signature (most reliable for compiled Fantom methods)
        if func_name in self._FN_SIGNATURES:
            sig = self._FN_SIGNATURES[func_name]
            if not sig:
                return {"kind": "Dict", "val": {}}
            params_dict: dict[str, dict] = {}
            for pname, pkind in sig:
                params_dict[pname] = {
                    "name": pname,
                    "kind": pkind,
                    "help": pname,
                }
            return {"kind": "Dict", "params": params_dict}

        params_dict = {}

        # Strategy 2: Standard "paramName: description" or "paramName - description"
        for line in doc.split("\n"):
            line = line.strip()
            m = re.match(r'^(\w+)\s*[:=-]\s*(.+)$', line)
            if m:
                pname = m.group(1)
                phelp = m.group(2).strip()
                if len(phelp) > 3 and not phelp.startswith("→") and not phelp.startswith("->"):
                    if not re.match(r'^[→➡▶▸]$', phelp[0]):
                        params_dict[pname] = {
                            "name": pname,
                            "kind": "Str",
                            "help": phelp,
                        }

        # Strategy 3: Chinese "paramName 为..." pattern
        if not params_dict:
            for line in doc.split("\n"):
                line = line.strip()
                m = re.match(r'^(\w[\w\d]*)\s+为\s+(.+)$', line)
                if m:
                    pname = m.group(1)
                    phelp = m.group(2).strip().rstrip("。，；")
                    params_dict[pname] = {
                        "name": pname,
                        "kind": "Str",
                        "help": phelp,
                    }

        # Strategy 4: Extract from example call: funcName("arg1", arg2)
        if not params_dict:
            for line in doc.split("\n"):
                line = line.strip()
                m = re.search(r'\w+\(([^)]+)\)', line)
                if m:
                    args_str = m.group(1)
                    args = []
                    current = ""
                    in_quote = False
                    for ch in args_str:
                        if ch == '"':
                            in_quote = not in_quote
                            current += ch
                        elif ch == ',' and not in_quote:
                            args.append(current.strip())
                            current = ""
                        else:
                            current += ch
                    if current.strip():
                        args.append(current.strip())
                    for i, arg in enumerate(args):
                        pname = f"arg{i + 1}" if i > 0 else "expr"
                        if arg in ("true", "false"):
                            pkind = "Bool"
                        elif arg.replace(".", "").replace("-", "").isdigit():
                            pkind = "Number"
                        elif arg.startswith('"'):
                            pkind = "Str"
                        else:
                            pkind = "Str"
                        if pname not in params_dict:
                            params_dict[pname] = {
                                "name": pname,
                                "kind": pkind,
                                "help": f"Argument {i + 1}",
                            }

        if params_dict:
            return {"kind": "Dict", "params": params_dict}
        return {"kind": "Dict", "val": {}}

    def _build_tool_from_func_record(self, row: dict, name: str) -> dict:
        """Convert a func record dict into a tool dict.

        Args:
            row: Row dict from readAll(func and skyforgeMcp)
            name: Function name

        Returns:
            Tool dict with name, dis, help, src, params keys.
        """
        func_params = row.get("params")
        if func_params and isinstance(func_params, dict):
            has_content = func_params.get("params") or func_params.get("vals")
            if has_content:
                tool_params = func_params
            else:
                tool_params = {"kind": "Dict", "val": {}}
        else:
            tool_params = {"kind": "Dict", "val": {}}

        return {
            "name": name,
            "dis": row.get("name", name),
            "help": row.get("name", name),
            "src": row.get("src", ""),
            "params": tool_params,
        }

    # ── SB-09: Tool group definitions ───────────────────────────────────
    # Each group has: description, tool_names (list of exact names or
    # prefix patterns ending with '*'), and optional exclude list.
    TOOL_GROUPS: dict[str, dict] = {
        "base": {
            "description": "基础工具 — 始终可用",
            "tool_names": [
                "about", "evalAxon", "readSites", "readById", "readRecord",
                "readEquips", "readPoints", "readAll",
                "batchCommitAdd", "commitUpdate", "commitRemove",
                "finCopilotAsk",
                "getToolGroups", "setToolGroup",
            ],
        },
        "query": {
            "description": "查询检索 — 语义查询、设备详情、点位当前值/历史趋势",
            "tool_names": [
                "finMcpQuery", "finMcpDescribeEntity", "finMcpReadCurrent",
                "finMcpReadHistory", "finMcpAggregateQuery",
                "finMcpEntityGraphics", "finMcpEquipTopology",
                "finMcpDraftQuery",
            ],
        },
        "energy": {
            "description": "能效分析 — 能耗分解、KPI计算、碳排放、基准对比、节能潜力",
            "tool_names": [
                "finMcpEnergyBreakdown", "finMcpEnergyBaseline",
                "finMcpComputeKpi", "finMcpEnergyFlow", "finMcpCarbon",
                "finMcpSavingsPotential", "finMcpChillerPerformance",
                "finMcpPerfCurve", "finMcpPsychrometric",
                "finMcpEnergyReport", "finMcpReport",
                "finMcpCarbonMv", "finMcpReportBenchmark",
                "finMcpReportExplain",
                # coolMatrix energy functions
                "cmEnergyBreakdown*", "cmEnergyCompare*",
                "cmEnergy*", "cmPlantCopNow*",
                "cmPumpPerformance*", "cmTowerPerformance*",
                "cmEfficiency*", "cmMeter*",
                "cmPlantPerformanceSnapshot*",
                # chillerOpt
                "chillerOptFeatures*", "chillerOptScore*",
            ],
        },
        "diagnosis": {
            "description": "故障诊断 — 报警审查、设备诊断、根因分析、案例检索",
            "tool_names": [
                "finMcpListAlarms", "finMcpCriticalAlarms",
                "finMcpDetectAnomalies", "finMcpEquipTopology",
                "finMcpDescribeEntity", "finMcpListCases",
                "finMcpRecallCases", "finMcpListNotifications",
                "finMcpMarkNotificationRead",
                # coolMatrix safety/diagnosis
                "cmSafety*", "cmFsmStatus*", "cmCopAnomaly*",
                "cmListActionAudit*", "cmGetFaultLog*",
                "cmLogFault*", "cmDiagnostics*",
            ],
        },
        "control": {
            "description": "控制写入 — 受控写入、审批回退、工单管理、策略执行",
            "tool_names": [
                "finMcpProposeWrite", "finMcpApprove",
                "finMcpApproveProposal", "finMcpApprovePackage",
                "finMcpRollback", "finMcpListProposals",
                "finMcpCreateWorkOrder", "finMcpCloseWorkOrder",
                "finMcpListWorkOrders", "finMcpRejectProposal",
                # coolMatrix control
                "cmWritePoint*", "cmActivateStrategy*",
                "cmStopStrategyDevices*", "cmSetControlMode*",
                "cmEmergencyStop*", "cmReleaseEmergencyStop*",
                "cmSetChiller*", "cmSetHeatPump*",
                "cmStageUp*", "cmStageDown*",
                "cmManualModeOverride*",
            ],
        },
        "coolmatrix_admin": {
            "description": "CoolMatrix 管理 — 站点/设备/策略配置、AI模型、许可证",
            "tool_names": [
                "cmGetSites*", "cmGetPlantTree*",
                "cmGetAllEquipment*", "cmGetChillers*",
                "cmGetCoolingTowers*", "cmGetChilledWaterPumps*",
                "cmGetCondenserWaterPumps*", "cmGetHeatPumps*",
                "cmReadEquip*", "cmAddChiller*",
                "cmAddCoolingTower*", "cmAddPump*",
                "cmDeleteEquip*", "cmUpdateEquipTags*",
                "cmLicenseStatus*", "cmLicenseReload*",
                "cmInstallLicense*", "cmHostId*",
                "cmAiBoxStatus*", "cmAiModels*",
                "cmAiInfer*", "cmAiPredict*",
                "cmAiOptimize*", "cmAiModelActivate*",
                "cmAiTrainJobList*", "cmAiBoxBuildPlantDataset*",
                "cmCleanHistory*", "cmTopologyPush*",
                "cmCreateAndBindBatch*", "cmCreateAndBindBatch*",
                "cmTrimActionAudit*", "cmDeleteGroupStrategy*",
                "cmRotateAllGroupsNative*", "cmWtfSnapshot*",
                "cmFsmTick*", "cmStrategyMainLoop*",
                "cmEngineAllowed*", "cmOptimizeTick*",
                "cmPsoOptimize*", "cmPsoStatus*",
                "cmChillerOptRunCycle*", "cmChillerOptProvision*",
                "cmChillerOptRunCycle*",
            ],
        },
        "admin": {
            "description": "系统管理 — LLM配置、用量统计、调度任务、文档管理",
            "tool_names": [
                "finMcpSetLlm", "finMcpLlmStatus", "finMcpLlmTest",
                "finMcpUsage", "finMcpHealth",
                "finMcpListJobs", "finMcpSetJobEnabled",
                "finMcpRunJob", "finMcpCreateCustomJob",
                "finMcpListCustomJobs", "finMcpDeleteCustomJob",
                "finMcpListDocs", "finMcpIngestDoc",
                "finMcpIngestUrl", "finMcpDeleteDoc",
                "finMcpRetrieveDocs", "finMcpListMemory",
                "finMcpSetAgentConfig", "finMcpSetEmbed",
                "finMcpSetAsr", "finMcpEval",
                "finMcpEvalGuardrails", "finMcpEvalTrajectories",
                "finMcpBatchSubmit", "finMcpBatchStatus",
                "finMcpBatchResults",
                # coolMatrix admin
                "cmChillerOptReloadModel*", "cmChillerOptRunCycle*",
            ],
        },
        "ai_chat": {
            "description": "AI对话 — finCopilot 统一入口、聊天会话管理（推荐首选）",
            "tool_names": [
                "finCopilotAsk",
                "finMcpChatStart", "finMcpChatPoll",
                "finMcpChatCancel", "finMcpSaveChat",
                "finMcpLoadChat", "finMcpListChats",
                "finMcpDeleteChat",
            ],
        },
    }

    # Default active group — "base" tools are always included regardless
    _active_group: str = "base"

    # Names of tools that are always available (included in every group)
    _BASE_TOOL_NAMES: set[str] = {
        "about", "evalAxon", "readSites", "readById", "readRecord",
        "readEquips", "readPoints", "readAll",
        "batchCommitAdd", "commitUpdate", "commitRemove",
        "getToolGroups", "setToolGroup",
    }

    @classmethod
    def getToolGroups(cls) -> list[dict]:
        """Return available tool groups with descriptions.

        Returns:
            List of {name, description, toolCount} dicts.
        """
        result = []
        for name, g in cls.TOOL_GROUPS.items():
            if name == "base":
                continue
            result.append({
                "name": name,
                "description": g["description"],
            })
        return result

    @classmethod
    def setActiveGroup(cls, group_name: str) -> str:
        """Set the active tool group for filtering.

        Args:
            group_name: One of the group names in TOOL_GROUPS, or "all" for no filter.

        Returns:
            Confirmation message.
        """
        if group_name == "all":
            cls._active_group = "all"
            return "已切换到全部工具模式 (显示全部 531 个工具)"
        if group_name in cls.TOOL_GROUPS:
            cls._active_group = group_name
            g = cls.TOOL_GROUPS[group_name]
            return f"已切换到工具组: {group_name} ({g['description']})"
        raise ValueError(f"未知工具组: {group_name}，可用组: {', '.join(cls.TOOL_GROUPS.keys())}")

    def _match_tool_group(self, tool_name: str, group_cfg: dict) -> bool:
        """Check if a tool name matches a group's tool_names patterns.

        Supports exact match and prefix wildcard (e.g. "cmSafety*" matches
        "cmSafetyStatus", "cmSafetyClear", etc.).
        """
        for pattern in group_cfg.get("tool_names", []):
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if tool_name.startswith(prefix):
                    return True
            elif tool_name == pattern:
                return True
        return False

    def _get_group_tool_names(self, group_name: str) -> set[str]:
        """Get the set of tool names (expanded patterns) for a group.

        Args:
            group_name: Group name or "all".

        Returns:
            Set of tool name strings.  If group_name is "all", returns
            an empty set (meaning no filter).
        """
        if group_name == "all":
            return set()  # empty = no filter

        names: set[str] = set()
        # Always include base tools
        names.update(self._BASE_TOOL_NAMES)

        # Include tools from the active group
        cfg = self.TOOL_GROUPS.get(group_name)
        if cfg:
            for pattern in cfg.get("tool_names", []):
                if pattern.endswith("*"):
                    # Can't expand prefix patterns here — handled at filter time
                    names.add(pattern)
                else:
                    names.add(pattern)
        return names

    def fetchMcpTools(self, group: Optional[str] = None) -> list[types.Tool]:
        """Fetch MCP tools from SkySpark, with group filtering (SB-09).

        If *group* is None, uses the currently active group
        (``self._active_group``).  If *group* is "all" or no group is
        active, all tools are returned.

        Sources (in priority order):
          1. DB func records tagged with skyforgeMcp
          2. Auto-discovered @Axon functions from configured pods (SB-05/SB-06)
          3. Hardcoded tools (evalAxon, readSites, etc.)

        Caching (SB-07): Results are cached for 60 seconds to avoid
        hammering the FIN server on every tools/list call.

        Returns:
            List of MCP Tool objects, filtered by active group.
        """
        # ---- SB-07: Check cache first ----
        now = time.time()
        if self._tool_cache is not None and (now - self._tool_cache_time) < self._tool_cache_ttl:
            return self._tool_cache

        try:
            # Step 1: Read DB func records with skyforgeMcp tag
            result = self.eval("readAll(func and skyforgeMcp)")
            tools_from_db: list[dict] = []
            seen_names: set[str] = set()

            for row in result.rows:
                d = dict(row)
                name = d.get("name", "")
                if not name or name in ("fetchMcpTools", "fetchMcpPrompts"):
                    continue
                if name in seen_names:
                    continue
                seen_names.add(name)
                tools_from_db.append(self._build_tool_from_func_record(d, name))

            # Step 2: Auto-discover functions from configured pods (SB-05/SB-06)
            auto_tools = self._discover_pod_functions()
            for t in auto_tools:
                if t["name"] not in seen_names:
                    seen_names.add(t["name"])
                    tools_from_db.append(t)

            # Step 3: Add hardcoded tools
            all_tools = tools_from_db + list(HARDCODED_TOOLS)

            # Build the final grid
            fallback_grid = Grid(meta={}, cols=[GridCol(name="name")], rows=[])
            hgrid = HGrid(fallback_grid)
            final_names: set[str] = set()
            for tool in all_tools:
                if tool["name"] not in final_names:
                    final_names.add(tool["name"])
                    hgrid.grid.rows.append(tool)

            result_tools = hgrid_to_tools(hgrid)

            # ---- SB-09: Apply group filter ----
            resolved_group = group if group is not None else self._active_group
            if resolved_group and resolved_group != "all":
                group_cfg = self.TOOL_GROUPS.get(resolved_group)
                if group_cfg:
                    filtered: list[types.Tool] = []
                    for tool in result_tools:
                        name = tool.name
                        # Always include base tools and the group-management tools
                        if name in self._BASE_TOOL_NAMES:
                            filtered.append(tool)
                            continue
                        # Check if tool matches any pattern in the group
                        if self._match_tool_group(name, group_cfg):
                            filtered.append(tool)
                    result_tools = filtered

            # ---- Update cache (SB-07) ----
            self._tool_cache = result_tools
            self._tool_cache_time = now

            return result_tools

        except Exception as e:
            logger.warning("Failed to read func records: %s, falling back to hardcoded tools", e)
            fallback_grid = Grid(meta={}, cols=[GridCol(name="name")], rows=[])
            hgrid = HGrid(fallback_grid)
            for tool in HARDCODED_TOOLS:
                hgrid.grid.rows.append(tool)
            return hgrid_to_tools(hgrid)

    def fetchMcpPrompts(self) -> list[types.Prompt]:
        """Build MCP prompts for finCopilot capability domains (SB-11)

        Returns prompts for each of the 9 finCopilot capability domains plus
        common workflow templates.  These are built entirely in Python so that
        they work without depending on the FIN-side fetchMcpPrompts() Axon
        function (which only returns a hardcoded example in setup.zinc).

        Returns:
            List of MCP Prompt objects
        """
        # Domain-specific prompts — each maps to finCopilot's capabilityId
        domain_prompts: list[dict] = [
            {
                "name": "fin_general_assistant",
                "description": "通用运维助手 — 回答楼宇设备运行、查询、控制等日常问题",
                "arguments": [
                    {"name": "question", "description": "用户问题", "required": True},
                ],
            },
            {
                "name": "fin_hvac_diagnosis",
                "description": "暖通空调诊断 — 分析AHU/冷机/冷却塔/水泵运行状态，诊断效率下降或故障原因",
                "arguments": [
                    {"name": "equipRef", "description": "设备引用ID（如AHU-101、Chiller-01）", "required": True},
                    {"name": "range", "description": "分析时间范围，如 'thisWeek', 'thisMonth'", "required": False},
                ],
            },
            {
                "name": "fin_me_equipment",
                "description": "机电设备 — 查询风机/水泵/照明等机电设备的状态与参数",
                "arguments": [
                    {"name": "equipRef", "description": "设备引用ID", "required": True},
                ],
            },
            {
                "name": "fin_space_comfort",
                "description": "空间舒适度 — 分析室内温湿度/CO₂/光照等环境参数，评估舒适度",
                "arguments": [
                    {"name": "spaceRef", "description": "空间/区域引用ID", "required": True},
                    {"name": "range", "description": "分析时间范围", "required": False},
                ],
            },
            {
                "name": "fin_bas_control",
                "description": "楼宇自控 — 查看楼控系统运行参数、控制序列、设备启停状态",
                "arguments": [
                    {"name": "equipRef", "description": "设备引用ID", "required": False},
                    {"name": "filter", "description": "Haystack过滤器，如 'ahu and equip'", "required": False},
                ],
            },
            {
                "name": "fin_fdd_diagnosis",
                "description": "故障诊断 — 自动分析报警、诊断设备故障原因、建议修复措施",
                "arguments": [
                    {"name": "equipRef", "description": "设备引用ID", "required": False},
                    {"name": "severity", "description": "报警严重级别过滤", "required": False},
                ],
            },
            {
                "name": "fin_energy_analysis",
                "description": "能效分析 — 分析能耗数据、计算KPI、找节能机会、碳排放核算",
                "arguments": [
                    {"name": "range", "description": "分析时间范围，如 'thisMonth', 'lastQuarter'", "required": True},
                    {"name": "equipRef", "description": "指定设备分析（可选）", "required": False},
                ],
            },
            {
                "name": "fin_wellness",
                "description": "健康舒适 — 分析室内环境质量(IEQ)、热舒适、空气质量等健康指标",
                "arguments": [
                    {"name": "spaceRef", "description": "空间/区域引用ID", "required": True},
                    {"name": "range", "description": "分析时间范围", "required": False},
                ],
            },
            {
                "name": "fin_report_generation",
                "description": "报告生成 — 自动生成运行报告/能效报告/诊断报告",
                "arguments": [
                    {"name": "reportType", "description": "报告类型: energy/operation/diagnosis/custom", "required": True},
                    {"name": "range", "description": "报告覆盖时间范围", "required": True},
                    {"name": "equipRef", "description": "指定设备范围（可选）", "required": False},
                ],
            },
            {
                "name": "fin_query_entities",
                "description": "语义查询 — 用自然语言查询楼宇设备、点位和空间信息",
                "arguments": [
                    {"name": "query", "description": "查询描述，如 '所有AHU设备'、'3楼的温度点位'", "required": True},
                    {"name": "limit", "description": "最大返回数量", "required": False},
                ],
            },
            {
                "name": "fin_work_order",
                "description": "工单管理 — 创建/查询/关闭工单，跟踪维修进度",
                "arguments": [
                    {"name": "action", "description": "操作: list/create/close", "required": True},
                    {"name": "workOrderId", "description": "工单ID（close时必填）", "required": False},
                    {"name": "description", "description": "工单描述（create时必填）", "required": False},
                ],
            },
            {
                "name": "fin_alarm_review",
                "description": "报警审查 — 查看当前激活报警，分析报警根因",
                "arguments": [
                    {"name": "severity", "description": "严重级别: critical/urgent/warning/info", "required": False},
                    {"name": "range", "description": "时间范围", "required": False},
                ],
            },
        ]

        # Build Prompt objects
        prompts: list[types.Prompt] = []
        for dp in domain_prompts:
            args_list: list[types.PromptArgument] = []
            for a in dp.get("arguments", []):
                args_list.append(
                    types.PromptArgument(
                        name=a["name"],
                        description=a.get("description", ""),
                        required=a.get("required", False),
                    )
                )
            prompts.append(
                types.Prompt(
                    name=dp["name"],
                    description=dp["description"],
                    arguments=args_list,
                )
            )

        logger.info(f"Built {len(prompts)} finCopilot prompts (SB-11)")
        return prompts

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
            return self.eval(expr, operation_type="read")
        elif name in ("readById", "readRecord"):
            rid = params.get("id", "") if isinstance(params, dict) else ""
            return self.read_record(rid)
        elif name == "batchCommitAdd":
            # New path: accept records as JSON string or dict
            records_raw = params.get("records") if isinstance(params, dict) else None
            if records_raw is not None:
                if isinstance(records_raw, str):
                    records = json.loads(records_raw)
                else:
                    records = records_raw
                if isinstance(records, dict):
                    records = [records]
                return self.commit_add(records)
            # Backward compat: zinc_payload param
            zinc_str = params.get("zinc_payload", "") if isinstance(params, dict) else ""
            if zinc_str:
                logger.warning(
                    "batchCommitAdd using deprecated zinc_payload param - "
                    "prefer 'records' JSON param"
                )
                return self.batch_commit_add(zinc_str)
            return HGrid(Grid(meta={"err": "Missing records or zinc_payload parameter"}, cols=[], rows=[]))
        elif name == "commitUpdate":
            target_id = params.get("target_id", "") if isinstance(params, dict) else ""
            update_tags_raw = params.get("update_tags", {}) if isinstance(params, dict) else {}
            if isinstance(update_tags_raw, str):
                update_tags = json.loads(update_tags_raw)
            else:
                update_tags = update_tags_raw
            return self.commit_update(target_id, update_tags)
        elif name == "commitRemove":
            target_id = params.get("target_id", "") if isinstance(params, dict) else ""
            return self.commit_remove(target_id)
        elif name == "readAll":
            axon_filter = params.get("axon_filter", "") if isinstance(params, dict) else ""
            return self.readAll(axon_filter)

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
            # For Dict kind (default): extract values as positional args
            # FIN 3.1.5 does not support call("funcName", [{dict}])
            # so we convert to positional: call("funcName", [v1, v2, ...])
            if isinstance(params, dict):
                if not params:
                    expression = f"call({to_axon(name)}, [])"
                else:
                    # Use params_order if available, otherwise sorted keys
                    if params_order:
                        keys = params_order
                    else:
                        keys = sorted(params.keys())
                    args_parts = [to_axon(params[k]) for k in keys if k in params]
                    args_str = ", ".join(args_parts)
                    expression = f"call({to_axon(name)}, [{args_str}])"
            else:
                # If list provided but Dict expected, treat as positional
                if isinstance(params, list):
                    args_parts = [to_axon(item) for item in params]
                else:
                    args_parts = [to_axon(params)]
                args_str = ", ".join(args_parts)
                expression = f"call({to_axon(name)}, [{args_str}])"
        
        # Execute via eval (which will log on error) - return HGrid for dual format support
        return self.eval(expression)
