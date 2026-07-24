"""Grid 出向清洗 — 将 Haystack Grid 降维为极简 JSON

将包含大量 Haystack 类型元数据（_kind: marker, m:, r: 等）的原始 Grid
清洗为扁平 JSON 数组，减少 Token 消耗 50-70%。

用法:
    from app.skyspark.cleaner import clean_grid, clean_value
    
    raw_grid = client.eval("readAll(site)")
    cleaned = clean_grid(HGrid(raw_grid))
    # 返回: [{"dis": "Building A", "area": "5000 m²"}, ...]
"""

from typing import Any
from .types import (
    MarkerExt, NAExt, RemoveExt, NumberExt,
    RefExt, UriExt, SymbolExt, CoordExt,
    DateExt, TimeExt, DateTimeExt,
    DateRangeExt, DateTimeRangeExt, XStrExt,
    DictExt, ListExt,
)


def clean_grid(hgrid) -> list[dict]:
    """将 Haystack Grid 清洗为极简 JSON 数组
    
    Args:
        hgrid: HGrid 包装对象
        
    Returns:
        清洗后的扁平 JSON 数组，每行一个 dict
    """
    rows = []
    for row in hgrid.rows:
        cleaned = {}
        for k, v in row.items():
            if k in ("mod", "tz", "na"):  # 移除内部列
                continue
            cleaned[k] = clean_value(v)
        rows.append(cleaned)
    return rows


def clean_value(v: Any) -> Any:
    """递归清洗单个 Haystack 值为纯 Python 类型
    
    转换规则:
        Marker  → True
        NA      → None
        Remove  → None
        Number  → "45 kW" (带单位) 或 45 (不带单位)
        Ref     → "@id (dis)"
        Uri     → str
        Symbol  → "^symbol"
        Coord   → {"lat": x, "lng": y}
        其他     → 递归处理 dict/list 或保留原值
    """
    if isinstance(v, MarkerExt):
        return True
    if isinstance(v, NAExt):
        return None
    if isinstance(v, RemoveExt):
        return None
    if isinstance(v, NumberExt):
        val = float(v.val) if v.val is not None else None
        return f"{val} {v.unit}" if (v.unit and val is not None) else val
    if isinstance(v, RefExt):
        result = f"@{v.val}"
        if v.dis:
            result += f" ({v.dis})"
        return result
    if isinstance(v, UriExt):
        return v.val
    if isinstance(v, SymbolExt):
        return f"^{v.val}"
    if isinstance(v, CoordExt):
        return {"lat": float(v.lat) if v.lat else None,
                "lng": float(v.lng) if v.lng else None}
    if isinstance(v, DateTimeExt):
        return str(v.val) if v.val else None
    if isinstance(v, (DateExt, TimeExt)):
        return str(v.val) if v.val else None
    if isinstance(v, DateRangeExt):
        return {"start": str(v.start), "end": str(v.end)}
    if isinstance(v, DateTimeRangeExt):
        return {"start": str(v.start), "end": str(v.end)}
    if isinstance(v, XStrExt):
        return str(v.val)
    if isinstance(v, (dict, DictExt)):
        return {k: clean_value(v) for k, v in v.items()}
    if isinstance(v, (list, ListExt, tuple)):
        return [clean_value(x) for x in v]
    # 基本类型直接返回
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    # 兜底：尝试 toStr
    if hasattr(v, 'toStr'):
        return v.toStr()
    return str(v)
