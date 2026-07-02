#!/usr/bin/env python3
"""Register Failover module functions (failDetect, failSwitch) into FIN test1 project.

Compatible with FIN 3.1.5: no :=, no ||/&&, no 0f.
"""
import os, sys

os.chdir("/mnt/hermes-work/skyforge-mcp-fin")
from phable import open_haxall_client, Marker, Number as PhNumber
from dotenv import load_dotenv
load_dotenv()

URI = os.getenv("SKYSPARK_URI")
USER = os.getenv("SKYSPARK_USERNAME")
PASS = os.getenv("SKYSPARK_PASSWORD")
print(f"Connecting to {URI} ...")

# ── Axon source (FIN 3.1.5 compatible) ──────────────────────────────────────

failDetect_src = (
    '(runningStatus, alarmFlag, feedbackTimeout) => do\n'
    '  if (runningStatus > 0.0)\n'
    '    if (alarmFlag > 0.0) return 1.0\n'
    '  if (runningStatus > 0.0)\n'
    '    if (feedbackTimeout > 0.0) return 1.0\n'
    '  return 0.0\n'
    'end'
)

failSwitch_src = (
    '(faultIndex, totalCount, standbyIndex) => do\n'
    '  if (faultIndex < 0.0) return -1.0\n'
    '  if (standbyIndex >= 0.0) return standbyIndex\n'
    '  if (faultIndex == 0.0)\n'
    '    if (totalCount > 1.0) return 1.0\n'
    '  if (totalCount > 1.0) return 0.0\n'
    '  return -1.0\n'
    'end'
)

# ── Params builder ───────────────────────────────────────────────────────────

def make_params(**params):
    d = {}
    for pname, pinfo in params.items():
        entry = {"name": pinfo["name"], "kind": pinfo["kind"], "help": pinfo["help"]}
        if "default" in pinfo:
            entry["default"] = PhNumber(val=pinfo["default"])
        d[pname] = entry
    return {"kind": "Dict", "params": d}

failDetect_params = make_params(
    runningStatus={"name":"runningStatus","kind":"Number","help":"设备运行状态 (1=运行, 0=停止)"},
    alarmFlag={"name":"alarmFlag","kind":"Number","help":"设备报警标志 (1=报警, 0=正常)"},
    feedbackTimeout={"name":"feedbackTimeout","kind":"Number","help":"反馈超时标志 (1=超时, 0=正常)"}
)

failSwitch_params = make_params(
    faultIndex={"name":"faultIndex","kind":"Number","help":"故障设备索引"},
    totalCount={"name":"totalCount","kind":"Number","help":"同类型设备总数"},
    standbyIndex={"name":"standbyIndex","kind":"Number","help":"备用设备索引（-1自动选择）","default":-1}
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
    for name, src, params in [
        ("failDetect", failDetect_src, failDetect_params),
        ("failSwitch", failSwitch_src, failSwitch_params),
    ]:
        rec = make_rec(name, src, params)
        result = client.commit_add(rec)
        rid = dict(result.rows[0]).get("id", "?")
        print(f"Registered {name} -> id={rid}")

    # ── Verify registration ────────────────────────────────────────────────
    print("\n═══ Verification ═══")
    result = client.eval('readAll(func and (name=="failDetect" or name=="failSwitch"))')
    for row in result.rows:
        d = dict(row)
        n = d.get("name","?")
        rid = d.get("id","?")
        has_mcp = "skyforgeMcp" in d
        has_func = "func" in d
        has_params = "params" in d
        print(f"  {n}: id={rid}, func={has_func}, skyforgeMcp={has_mcp}, params={has_params}")

    # ── Test failDetect ─────────────────────────────────────────────────────
    print("\n═══ Testing failDetect ═══")
    test_cases = [
        (1.0, 1.0, 0.0, 1.0, "运行中报警→故障"),
        (1.0, 0.0, 0.0, 0.0, "正常运行"),
        (0.0, 1.0, 0.0, 0.0, "停止状态报警忽略"),
    ]
    for run, alarm, timeout, expected, desc in test_cases:
        expr = f'failDetect({run}, {alarm}, {timeout})'
        result = client.eval(expr)
        raw = dict(result.rows[0]).get("val") if result.rows else None
        val_str = str(raw.val) if raw is not None else "?"
        ok = "✓" if raw is not None and float(raw.val) == expected else "✗"
        print(f"  {ok} failDetect({run},{alarm},{timeout}) = {val_str:>5s}  (expected {expected})  [{desc}]")

    # ── Test failSwitch ─────────────────────────────────────────────────────
    print("\n═══ Testing failSwitch ═══")
    test_cases = [
        (2.0, 4.0, -1.0, 0.0, "故障3号切1号"),
        (0.0, 3.0, -1.0, 1.0, "故障1号切2号"),
        (0.0, 1.0, -1.0, -1.0, "单台设备无备用"),
    ]
    for idx, total, standby, expected, desc in test_cases:
        expr = f'failSwitch({idx}, {total}, {standby})'
        result = client.eval(expr)
        raw = dict(result.rows[0]).get("val") if result.rows else None
        val_str = str(raw.val) if raw is not None else "?"
        ok = "✓" if raw is not None and float(raw.val) == expected else "✗"
        print(f"  {ok} failSwitch({idx},{total},{standby}) = {val_str:>5s}  (expected {expected})  [{desc}]")

    print("\n═══ All done! ═══")
