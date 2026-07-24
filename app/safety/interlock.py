"""设备级安全联锁 — 防止意外写入物理设备

为 FIN MCP (8001) 提供设备级写入保护。
锁定后，对该设备的所有写入操作将被 MCP 拒绝。

用法:
    from app.safety.interlock import interlock
    
    interlock.lock("CH-01")
    interlock.is_locked("CH-01")  # → True
    interlock.unlock("CH-01")
"""

import logging
import re
from threading import Lock

logger = logging.getLogger(__name__)


class SafetyInterlock:
    """设备级安全联锁"""
    
    def __init__(self):
        self._locks: dict[str, bool] = {}
        self._lock = Lock()
    
    def is_locked(self, equip_ref: str) -> bool:
        return self._locks.get(equip_ref, False)
    
    def lock(self, equip_ref: str) -> str:
        with self._lock:
            self._locks[equip_ref] = True
        logger.warning(f"[SAFETY] 🔒 设备已锁定: {equip_ref}")
        return f"🔒 设备 {equip_ref} 已锁定。对该设备的所有写入操作将被拒绝。"
    
    def unlock(self, equip_ref: str) -> str:
        with self._lock:
            self._locks[equip_ref] = False
        logger.warning(f"[SAFETY] 🔓 设备已解锁: {equip_ref}")
        return f"🔓 设备 {equip_ref} 已解锁。允许写入操作。"
    
    def status(self, equip_ref: str = None) -> dict:
        if equip_ref:
            return {equip_ref: self.is_locked(equip_ref)}
        return dict(self._locks)
    
    def check_expression(self, expr: str) -> tuple[bool, list[str]]:
        """检查 Axon 表达式中涉及的所有设备是否被锁定
        
        Returns:
            (是否通过, 被锁定的设备列表)
        """
        refs = self._extract_refs(expr)
        locked = [r for r in refs if self.is_locked(r)]
        return (len(locked) == 0, locked)
    
    @staticmethod
    def _extract_refs(expr: str) -> list[str]:
        """从 Axon 表达式中抽取设备引用"""
        return re.findall(r'@([\w\-:]+)', expr)


# 全局单例
interlock = SafetyInterlock()
