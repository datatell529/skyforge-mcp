#!/usr/bin/env python3
"""Register Sequencing module functions into FIN test1 project."""
import os, sys

os.chdir("/mnt/hermes-work/skyforge-mcp-fin")
from phable import open_haxall_client, Grid, Marker, Number as PhNumber
from dotenv import load_dotenv
load_dotenv()

URI = os.getenv("SKYSPARK_URI")
USER = os.getenv("SKYSPARK_USERNAME")
PASS = os.getenv("SKYSPARK_PASSWORD")
print(f"Connecting to {URI} ...")

# ═══════════════════════════════════════════════════════════════════════
#  Axon source (FIN 3.1.5 constraints: no :=, no ||/&&, no 0f,
#  no closures, no for/while.  Parser quirk: list[idx] comparison
#  requires parenthesizing: (list[idx]) == val, not list[idx] == val.
# ═══════════════════════════════════════════════════════════════════════

# ── Helper: seqPickStartRec ───────────────────────────────────────────
#  Among stopped devices ((flags[idx])==0), select by:
#    primary:   highest efficiency  (effList[idx] > bestEff)
#    tiebreak:  shortest runtime    (runList[idx] < bestRun)
seqPickStartRec_src = (
    '(runList, effList, flags, idx, bestIdx, bestEff, bestRun) => do\n'
    '  if ((idx) >= flags.size) return bestIdx\n'
    '  if ((flags[idx]) == 0)\n'
    '    if ((effList[idx]) > bestEff)\n'
    '      return seqPickStartRec(runList, effList, flags, (idx)+1, idx, effList[idx], runList[idx])\n'
    '    if ((effList[idx]) < bestEff)\n'
    '      return seqPickStartRec(runList, effList, flags, (idx)+1, bestIdx, bestEff, bestRun)\n'
    '    if ((runList[idx]) < bestRun)\n'
    '      return seqPickStartRec(runList, effList, flags, (idx)+1, idx, bestEff, runList[idx])\n'
    '  return seqPickStartRec(runList, effList, flags, (idx)+1, bestIdx, bestEff, bestRun)\n'
    'end'
)

# ── Helper: seqPickStopRec ────────────────────────────────────────────
#  Among running devices ((flags[idx])==1), select by longest runtime.
seqPickStopRec_src = (
    '(runList, effList, flags, idx, bestIdx, bestRun) => do\n'
    '  if ((idx) >= flags.size) return bestIdx\n'
    '  if ((flags[idx]) == 1)\n'
    '    if ((runList[idx]) > bestRun)\n'
    '      return seqPickStopRec(runList, effList, flags, (idx)+1, idx, runList[idx])\n'
    '  return seqPickStopRec(runList, effList, flags, (idx)+1, bestIdx, bestRun)\n'
    'end'
)

# ── Main public functions ─────────────────────────────────────────────

seqPickStart_src = (
    '(runtimeList, efficiencyList, runningFlags) => do\n'
    '  return seqPickStartRec(runtimeList, efficiencyList, runningFlags, 0, -1, -1.0, 999999.0)\n'
    'end'
)

seqPickStop_src = (
    '(runtimeList, efficiencyList, runningFlags) => do\n'
    '  return seqPickStopRec(runtimeList, efficiencyList, runningFlags, 0, -1, -1.0)\n'
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

seqPickStart_params = make_params(
    runtimeList={"name": "runtimeList", "kind": "List", "help": "各设备累计运行时间(h)"},
    efficiencyList={"name": "efficiencyList", "kind": "List", "help": "各设备能效等级(同类型)"},
    runningFlags={"name": "runningFlags", "kind": "List", "help": "各设备运行状态(1=运行,0=停止)"}
)

seqPickStop_params = make_params(
    runtimeList={"name": "runtimeList", "kind": "List", "help": "各设备累计运行时间(h)"},
    efficiencyList={"name": "efficiencyList", "kind": "List", "help": "各设备能效等级(同类型)"},
    runningFlags={"name": "runningFlags", "kind": "List", "help": "各设备运行状态(1=运行,0=停止)"}
)

def make_rec(name, src, params, tags=None):
    rec = {
        "name": name,
        "func": Marker(),
        "src": src,
        "params": params,
    }
    if tags:
        rec.update(tags)
    return rec

# ── Main ───────────────────────────────────────────────────────────────────

# First, remove all existing seqPick* records
with open_haxall_client(URI, USER, PASS) as client:
    auth_token = client._auth_token
    import json, httpx
    headers = {
        'Authorization': f'BEARER authToken={auth_token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }

    # Read all seqPick records
    r = client.eval('readAll(func)')
    to_remove = []
    for row in r.rows:
        d = dict(row)
        name = d.get('name', '')
        if name.startswith('seqPick'):
            to_remove.append(d.get('id'))

    for rid in to_remove:
        expr = f'commit(diff(readById({rid}), {{removed:marker()}}))'
        grid = {'_kind': 'grid', 'meta': {'ver': '3.0'}, 'cols': [{'name': 'expr'}], 'rows': [{'expr': expr}]}
        httpx.post(f'{URI}/eval', content=json.dumps(grid), headers=headers, timeout=10)
    if to_remove:
        print(f"Cleaned {len(to_remove)} old seqPick* records")

    # Register helpers (no skyforgeMcp tag — internal only)
    for name, src in [
        ("seqPickStartRec", seqPickStartRec_src),
        ("seqPickStopRec", seqPickStopRec_src),
    ]:
        rec = make_rec(name, src, {"kind": "Dict", "params": {}})
        result = client.commit_add(rec)
        rid = dict(result.rows[0]).get("id", "?")
        print(f"Registered {name} -> id={rid}")

    # Register public functions
    for name, src, params in [
        ("seqPickStart", seqPickStart_src, seqPickStart_params),
        ("seqPickStop", seqPickStop_src, seqPickStop_params),
    ]:
        rec = make_rec(name, src, params, tags={"skyforgeMcp": Marker()})
        result = client.commit_add(rec)
        rid = dict(result.rows[0]).get("id", "?")
        print(f"Registered {name} -> id={rid}")

    # Verify
    print("\n═══ Verification ═══")
    result = client.eval('readAll(func and skyforgeMcp and name=="seqPickStart" or name=="seqPickStop")')
    for row in result.rows:
        d = dict(row)
        n = d.get("name","?")
        rid = d.get("id","?")
        has_mcp = "skyforgeMcp" in d
        has_func = "func" in d
        has_params = "params" in d
        print(f"  {n}: id={rid}, func={has_func}, skyforgeMcp={has_mcp}, params={has_params}")

    # ═══════════════════════════════════════════════════════════════════
    # Verification test helpers
    # ═══════════════════════════════════════════════════════════════════
    def call_func(expr):
        grid = {'_kind': 'grid', 'meta': {'ver': '3.0'}, 'cols': [{'name': 'expr'}], 'rows': [{'expr': expr}]}
        resp = httpx.post(f'{URI}/eval', content=json.dumps(grid), headers=headers, timeout=10)
        data = resp.json()
        rows = data.get('rows', [])
        return str(rows[0].get('val', '?')) if rows else '?', 'err' not in data.get('meta', {})

    def test(name, expr, expected):
        val, ok = call_func(expr)
        status = "✓" if val == str(expected) else "✗"
        print(f'  {status} {name}: {expr[:70]}... = {val} (expected {expected})')

    # ═══════════════════════════════════════════════════════════════════
    #  Acceptance Test 1: seqPickStart — 效率优先选机
    #  run=[100,200,50], eff=[4.0,5.0,4.5], flags=[0,0,1]
    #  Stopped: idx0(eff=4.0), idx1(eff=5.0) → highest eff → idx1
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Acceptance Test 1: seqPickStart — 效率优先选机 ═══")
    test("效优",
        'seqPickStart([100,200,50],[4.0,5.0,4.5],[0,0,1])',
        1)

    # ═══════════════════════════════════════════════════════════════════
    #  Acceptance Test 2: seqPickStart — 时间优先选机
    #  run=[100,200,50], eff=[0,0,0], flags=[0,0,1]
    #  All eff=0 → tiebreak by shortest runtime among stopped devices.
    #  Stopped: idx0(run=100), idx1(run=200) → shortest → idx0(100)
    #  NOTE: The spec says expected=2, but idx2 is running (flags=1),
    #        so it is NOT selectable.  Correct answer is idx0=0.
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Acceptance Test 2: seqPickStart — 时间优先选机 ═══")
    test("时优(最短运行时间优先启动)",
        'seqPickStart([100,200,50],[0,0,0],[0,0,1])',
        0)  # idx0 has shortest runtime (100) among stopped devices

    # ═══════════════════════════════════════════════════════════════════
    #  Acceptance Test 3: seqPickStop — 停运行最久的
    #  run=[100,200,50], eff=[4.0,5.0,4.5], flags=[1,1,0]
    #  Running: idx0(run=100), idx1(run=200) → longest → idx1 ✓
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Acceptance Test 3: seqPickStop — 停运行最久的 ═══")
    test("停运",
        'seqPickStop([100,200,50],[4.0,5.0,4.5],[1,1,0])',
        1)

    # ═══════════════════════════════════════════════════════════════════
    #  Edge: 全部停止 → seqPickStop → -1
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Edge: seqPickStop — 全部停止 ═══")
    test("全停",
        'seqPickStop([100,200,50],[4.0,5.0,4.5],[0,0,0])',
        -1)

    # ═══════════════════════════════════════════════════════════════════
    #  Edge: 全部运行 → seqPickStart → -1
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Edge: seqPickStart — 全部运行 ═══")
    test("全运",
        'seqPickStart([100,200,50],[4.0,5.0,4.5],[1,1,1])',
        -1)

    # ═══════════════════════════════════════════════════════════════════
    #  Extra: 时间优先选机 + 全停止状态
    # ═══════════════════════════════════════════════════════════════════
    print("\n═══ Extra: seqPickStart — 全停止+时间优先 ═══")
    test("全停时优",
        'seqPickStart([100,200,50],[0,0,0],[0,0,0])',
        2)  # all stopped, shortest runtime is idx2(50)

    print("\n═══ All done! ═══")
