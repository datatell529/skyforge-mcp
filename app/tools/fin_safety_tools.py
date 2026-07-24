"""FIN 安全联锁工具 — finSafetyLock / finSafetyUnlock / finSafetyStatus"""
import json
import logging
from app.safety.interlock import interlock

logger = logging.getLogger(__name__)


def finSafetyLock(equipRef: str) -> str:
    """锁定一个设备，拒绝所有写入操作
    
    Args:
        equipRef: 设备引用 ID（如 "CH-01"、"AHU-01"）
        
    Returns:
        JSON 字符串（操作结果）
    """
    if not equipRef:
        return json.dumps({"error": True, "message": "请指定设备引用"})
    result = interlock.lock(equipRef)
    return json.dumps({"success": True, "message": result}, ensure_ascii=False)


def finSafetyUnlock(equipRef: str) -> str:
    """解锁一个设备，允许写入操作
    
    Args:
        equipRef: 设备引用 ID
        
    Returns:
        JSON 字符串（操作结果）
    """
    if not equipRef:
        return json.dumps({"error": True, "message": "请指定设备引用"})
    result = interlock.unlock(equipRef)
    return json.dumps({"success": True, "message": result}, ensure_ascii=False)


def finSafetyStatus(equipRef: str = None) -> str:
    """查看设备安全联锁状态
    
    Args:
        equipRef: 可选，指定设备。不指定则返回所有设备状态。
        
    Returns:
        JSON 字符串（联锁状态表）
    """
    status = interlock.status(equipRef)
    locked = {k: v for k, v in status.items() if v}
    unlocked = {k: v for k, v in status.items() if not v}
    
    return json.dumps({
        "status": status,
        "locked_count": len(locked),
        "unlocked_count": len(unlocked),
        "locked": list(locked.keys()),
        "hint": "锁定设备后，所有写入操作会被 MCP 自动拒绝。使用 finSafetyUnlock 解锁。",
    }, ensure_ascii=False)
