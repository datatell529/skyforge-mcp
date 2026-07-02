#!/usr/bin/env python3
"""Register Staging module functions into FIN test1 project."""
import os, sys

os.chdir("/mnt/hermes-work/skyforge-mcp-fin")
from phable import open_haxall_client, Marker, Number as PhNumber
from dotenv import load_dotenv
load_dotenv()

URI = os.getenv("SKYSPARK_URI")
USER = os.getenv("SKYSPARK_USERNAME")
PASS = os.getenv("SKYSPARK_PASSWORD")
print(f"Connecting to {URI} ...")

# ── Axon source (FIN 3.1.5: no :=, no ||/&&, no 0f) ──────────────────────

stagDecide_src = (
    '(currentLoad, totalCapacity, runningCount, totalCount, loadBufPct) => do\n'
    '  if (runningCount >= totalCount) return "HOLD"\n'
    '  if (totalCapacity <= 0.0) return "HOLD"\n'
    '  if ((currentLoad / totalCapacity) > (1.0 + loadBufPct)) return "ADD"\n'
    '  if (runningCount > 1.0)\n'
    '    if (currentLoad < ((runningCount - 1.0) * (totalCapacity / runningCount) * (1.0 - loadBufPct))) return "SUB"\n'
    '  return "HOLD"\n'
    'end'
)

stagAdd_src = (
    '(loadRatio, runningCount, chillerCount, pumpCount, towerCount) => do\n'
    '  if (chillerCount > 0.0) return "CHILLER"\n'
    '  if (pumpCount > 0.0) return "PUMP"\n'
    '  if (towerCount > 0.0) return "TOWER"\n'
    '  return "NONE"\n'
    'end'
)

stagSub_src = (
    '(loadRatio, runningCount, chillerCount, pumpCount, towerCount) => do\n'
    '  if (towerCount > 0.0) return "TOWER"\n'
    '  if (pumpCount > 0.0) return "PUMP"\n'
    '  if (chillerCount > 0.0) return "CHILLER"\n'
    '  return "NONE"\n'
    'end'
)

# ── Params builder ─────────────────────────────────────────────────────────

def make_params(**params):
    d = {}
    for pname, pinfo in params.items():
        entry = {"name": pinfo["name"], "kind": pinfo["kind"], "help": pinfo["help"]}
        if "default" in pinfo:
            entry["default"] = PhNumber(val=pinfo["default"])
        d[pname] = entry
    return {"kind": "Dict", "params": d}

stagDecide_params = make_params(
    currentLoad={"name":"currentLoad","kind":"Number","help":"当前总冷负荷(kW)"},
    totalCapacity={"name":"totalCapacity","kind":"Number","help":"当前运行设备总容量(kW)"},
    runningCount={"name":"runningCount","kind":"Number","help":"当前运行台数"},
    totalCount={"name":"totalCount","kind":"Number","help":"设备总台数"},
    loadBufPct={"name":"loadBufPct","kind":"Number","help":"加载缓冲比例","default":0.15}
)

stagAdd_params = make_params(
    loadRatio={"name":"loadRatio","kind":"Number","help":"当前负荷率"},
    runningCount={"name":"runningCount","kind":"Number","help":"当前运行台数"},
    chillerCount={"name":"chillerCount","kind":"Number","help":"冷机可加台数"},
    pumpCount={"name":"pumpCount","kind":"Number","help":"水泵可加台数"},
    towerCount={"name":"towerCount","kind":"Number","help":"冷却塔可加台数"}
)

stagSub_params = make_params(
    loadRatio={"name":"loadRatio","kind":"Number","help":"当前负荷率"},
    runningCount={"name":"runningCount","kind":"Number","help":"当前运行台数"},
    chillerCount={"name":"chillerCount","kind":"Number","help":"冷机可减台数"},
    pumpCount={"name":"pumpCount","kind":"Number","help":"水泵可减台数"},
    towerCount={"name":"towerCount","kind":"Number","help":"冷却塔可减台数"}
)

def make_rec(name, src, params):
    return {
        "name": name,
        "func": Marker(),
        "skyforgeMcp": Marker(),
        "src": src,
        "params": params,
    }

# ── Main ───────────────────────────────────────────────────────────────────

with open_haxall_client(URI, USER, PASS) as client:
    # Note: commit_remove is skipped due to FIN 3.1.5 quirk.
    # Unique function names ensure no conflicts.

    for name, src, params in [
        ("stagDecide", stagDecide_src, stagDecide_params),
        ("stagAdd", stagAdd_src, stagAdd_params),
        ("stagSub", stagSub_src, stagSub_params),
    ]:
        rec = make_rec(name, src, params)
        result = client.commit_add(rec)
        rid = dict(result.rows[0]).get("id", "?")
        print(f"Registered {name} -> id={rid}")

    # Verify
    print("\n═══ Verification ═══")
    result = client.eval('readAll(func and (name=="stagDecide" or name=="stagAdd" or name=="stagSub"))')
    for row in result.rows:
        d = dict(row)
        n = d.get("name","?")
        rid = d.get("id","?")
        has_mcp = "skyforgeMcp" in d
        has_func = "func" in d
        has_params = "params" in d
        print(f"  {n}: id={rid}, func={has_func}, skyforgeMcp={has_mcp}, params={has_params}")

    # Test stagDecide
    print("\n═══ Testing stagDecide ═══")
    test_cases = [
        (600, 500, 1, 2, 0.15, "ADD"),
        (300, 1000, 2, 3, 0.15, "SUB"),
        (300, 500, 1, 2, 0.15, "HOLD"),
        (580, 500, 1, 2, 0.15, "ADD"),
        (600, 600, 2, 2, 0.15, "HOLD"),
        (100, 0, 0, 2, 0.15, "HOLD"),
    ]
    for load, cap, run, total, buf, expected in test_cases:
        expr = f'stagDecide({load}, {cap}, {run}, {total}, {buf})'
        result = client.eval(expr)
        val = str(dict(result.rows[0]).get("val", "?")) if result.rows else "?"
        ok = "✓" if expected in val else "✗"
        print(f"  {ok} stagDecide({load},{cap},{run},{total},{buf}) = {val} (expected {expected})")

    # Test stagAdd
    print("\n═══ Testing stagAdd ═══")
    for lr, rc, cc, pc, tc, expected in [
        (0.8, 2, 1, 0, 0, "CHILLER"),
        (0.8, 2, 0, 1, 0, "PUMP"),
        (0.8, 2, 0, 0, 1, "TOWER"),
        (0.8, 2, 0, 0, 0, "NONE"),
    ]:
        expr = f'stagAdd({lr}, {rc}, {cc}, {pc}, {tc})'
        result = client.eval(expr)
        val = str(dict(result.rows[0]).get("val", "?")) if result.rows else "?"
        ok = "✓" if expected in val else "✗"
        print(f"  {ok} stagAdd({lr},{rc},{cc},{pc},{tc}) = {val}")

    # Test stagSub
    print("\n═══ Testing stagSub ═══")
    for lr, rc, cc, pc, tc, expected in [
        (0.5, 2, 0, 0, 1, "TOWER"),
        (0.5, 2, 0, 1, 0, "PUMP"),
        (0.5, 2, 1, 0, 0, "CHILLER"),
        (0.5, 2, 0, 0, 0, "NONE"),
    ]:
        expr = f'stagSub({lr}, {rc}, {cc}, {pc}, {tc})'
        result = client.eval(expr)
        val = str(dict(result.rows[0]).get("val", "?")) if result.rows else "?"
        ok = "✓" if expected in val else "✗"
        print(f"  {ok} stagSub({lr},{rc},{cc},{pc},{tc}) = {val}")

    print("\n═══ All done! ═══")

