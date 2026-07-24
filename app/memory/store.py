"""记忆系统 — JSON 文件持久化存储

提供跨会话的项目级记忆读写，支持序列化写入防止并发冲突。

用法:
    store = MemoryStore("/var/skyforge-mcp/memory.json")
    store.append("- 本项目冷站编号为 CH-01, CH-02, CH-03")
    print(store.read())
"""

import json
import logging
import os
from datetime import datetime
from threading import Lock

logger = logging.getLogger(__name__)

class MemoryStore:
    """文件级持久化记忆存储"""
    
    def __init__(self, path: str):
        self.path = path
        self._lock = Lock()
        self._ensure_file()
    
    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, 'w') as f:
                json.dump({"memory": "", "created": datetime.now().isoformat()}, f)
    
    def read(self) -> str:
        with self._lock:
            with open(self.path, 'r') as f:
                data = json.load(f)
            return data.get("memory", "")
    
    def append(self, text: str) -> str:
        with self._lock:
            with open(self.path, 'r') as f:
                data = json.load(f)
            data["memory"] += f"\n{text}"
            data["updated"] = datetime.now().isoformat()
            with open(self.path, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        char_count = len(text)
        logger.info(f"MemoryStore: appended {char_count} chars to {self.path}")
        return f"已追加记忆 ({char_count} 字符)"
