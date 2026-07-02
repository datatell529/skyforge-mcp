# 🌉 SkyBridge 项目 — 完整交接文档

> 给 Hermes Agent 和未来 Claude Code 会话阅读。
> 记录 SkyBridge 项目所有阶段（P0-P2）的完成状态和设计决策。

---

## 一、项目概况

**目的**：架设 FIN ↔ MCP 的桥梁，让 Hermes（通过 MCP）能调用 FIN 中 finCopilot、
coolMatrix、chillerOpt 等 pod 的全部 @Axon 函数。

**状态**：P0-P2 全部完成 ✅，P3 待开始 📋

**版本**：`skyforge-mcp-fin v0.2.0`

---

## 二、环境信息

| 组件 | 详情 |
|:-----|:------|
| **FIN 5.3** | `http://localhost:8800` (PID 见 `ps aux | grep finStackHost`) |
| **SkyForge MCP** | `http://localhost:8001` (PM2: `skyforge-mcp-fin`) |
| **代码路径** | `/mnt/hermes-work/skyforge-mcp-fin/` |
| **Python 环境** | `/mnt/hermes-work/skyforge-mcp-fin/.venv/bin/python3` |
| **PM2 管理** | `pm2 restart skyforge-mcp-fin` |
| **连接配置** | `.env` 文件: `su/susu` @ test1 项目 |

### 已部署的 Pod

| Pod | 位置 | 状态 |
|:----|:-----|:------|
| finCopilot | `/mnt/fin-5.3/fin/lib/fan/finCopilot.pod` | ✅ |
| coolMatrix | `/mnt/fin-5.3/fin/lib/fan/coolMatrix.pod` | ✅ |
| chillerOpt | `/mnt/fin-5.3/fin/lib/fan/chillerOpt.pod` | ✅ |

---

## 三、工具分组体系 (SB-09)

### 默认组 (base)
每次 `tools/list` 默认返回 14 个基础工具：
```
about, evalAxon, readSites, readById, readRecord, readEquips,
readPoints, readAll, batchCommitAdd, commitUpdate, commitRemove,
finCopilotAsk, getToolGroups, setToolGroup
```

### 7 个功能领域组

| 组名 | 说明 | 典型工具数 |
|:-----|:------|:----------:|
| `query` | 语义查询、设备详情、点位当前值/历史趋势 | ~20 |
| `energy` | 能耗分解、KPI、碳排放、基准对比、节能潜力 | ~50 |
| `diagnosis` | 报警审查、设备诊断、根因分析、案例检索 | ~30 |
| `control` | 受控写入、审批回退、工单管理、策略执行 | ~30 |
| `coolmatrix_admin` | 站点/设备/策略配置、AI 模型、许可证 | ~53 |
| `admin` | LLM 配置、用量统计、调度任务、文档管理 | ~35 |
| `ai_chat` | finCopilot 统一入口、聊天会话管理 | ~15 |

### 切换流程
```
1. tools/list → 14 个基础工具（含 getToolGroups / setToolGroup）
2. getToolGroups → 查看可用组
3. setToolGroup("energy") → 切换到能效组
4. tools/list → 只返回能效相关工具
5. 调用 finMcpEnergyBreakdown("thisMonth") → 正常执行
```

### 代码位置
- 分组定义: `app/skyspark/client.py` → `TOOL_GROUPS` 类属性
- 组管理工具: `skyforge_mcp/main.py` → `_call_tool_request()` 中的 getToolGroups/setToolGroup 处理
- 活跃组状态: `client.py` → `SkySpark._active_group` 类变量

---

## 四、自动发现机制 (SB-05/SB-06)

### 工作原理
```
FIN pod (@Axon 函数)
    → defs() API 枚举
    → Python 端按 lib 名过滤 (MCP_AUTO_DISCOVER_PODS)
    → 去重 + 合并硬编码工具
    → 构建 MCP Tool 对象
    → 按活跃组过滤 (SB-09)
```

### 配置
`.env` 文件中的环境变量：
```
MCP_AUTO_DISCOVER_PODS=["finCopilot","coolMatrix","chillerOpt"]
```

### 参数 Schema
- 主方案: `_FN_SIGNATURES` 字典 (`client.py` 第 ~500 行)
- 回退方案: doc 字符串解析
- 如果函数不在 `_FN_SIGNATURES` 中，工具仍有空 schema（可调用但无参数提示）

### 添加新 Pod 的步骤
```bash
# 1. 复制 pod 到 FIN
cp /path/to/newPod.pod /mnt/fin-5.3/fin/lib/fan/

# 2. 重启 FIN
kill -9 $(pgrep -f finStackHost)
cd /mnt/fin-5.3 && nohup ./fin/bin/fin > /tmp/fin.log 2>&1 &

# 3. 添加环境变量
echo 'MCP_AUTO_DISCOVER_PODS=["finCopilot","coolMatrix","chillerOpt","newPod"]' >> .env

# 4. 添加函数签名到 _FN_SIGNATURES (可选但推荐)

# 5. 重启 MCP
pm2 restart skyforge-mcp-fin
```

---

## 五、MCP Prompts (SB-11)

12 个中文 prompt 模板，纯 Python 实现：
- 9 个领域 prompt (general/hvac/me/space/bas/fdd/energy/wellness/report)
- 3 个工作流 prompt (query/work_order/alarm_review)

代码位置:
- 定义: `app/skyspark/client.py` → `fetchMcpPrompts()`
- 渲染: `skyforge_mcp/main.py` → `_DOMAIN_SYSTEM_PROMPTS` + `_get_prompt_request()`

---

## 六、关键文件索引

| 文件 | 说明 |
|:-----|:------|
| `app/skyspark/client.py` | **核心** — SkySpark 客户端（828 行）：自动发现、缓存、分组、参数推导 |
| `app/skyspark/converters.py` | HGrid → MCP 类型转换 |
| `app/tools/axon_tools.py` | 硬编码工具定义 |
| `skyforge_mcp/main.py` | **MCP 服务** — FastMCP: 组管理、prompt、工具分发 |
| `.env` | 连接配置 + MCP_AUTO_DISCOVER_PODS |
| `setup.zinc` | FIN 侧的示例 func 记录 |
| `CHANGELOG.md` | 变更日志 v0.2.0 |
| `SKYBRIDGE_HANDOFF.md` | **本文档** |

---

## 七、P3 待办 (待启动)

| ID | 任务 | 说明 |
|:---|:------|:------|
| SB-12 | **Streaming (SSE)** | AgentLoop onEvent → MCP stream |
| SB-13 | **多租户** | 同时连接多个 FIN 项目 |
| SB-14 | **审计日志** | 记录每次 tools/call |
| SB-15 | **Docker 化** | 容器化部署 |

---

## 八、常见问题

### Q: 认证失败 (User disabled: su)
FIN 重启后 su 用户可能被禁用。重新启用方法：
在 FIN 浏览器 Axon 编辑器中执行:
```
commit(readById("p:test1:r:su"), {disabled:Remove()})
```

### Q: coolMatrix 不加载
检查依赖: `grep pod.depends /tmp/pod_analysis/coolMatrix/meta.props`
需要先部署 chillerOpt.pod 和 finEntityModelToolsExt.pod。

### Q: phable eval 返回 "Haystack Grid"
phable 的 `client.eval()` 无法处理某些 Grid 类型返回值。
改用 `client.call("eval", ...)` 或直接通过 MCP 调用 FIN 函数。

### Q: 忘记当前在哪个组
调用 `getToolGroups` 会显示当前活跃组。
