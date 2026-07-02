# Changelog

## [0.2.0] - 2026-07-02 — SkyBridge 项目 (P0-P2)

### 项目背景
架设"天空之桥"让 Hermes（通过 MCP）能调用 FIN 中 finCopilot 的全部能力。
finCopilot 的 @Axon 函数是 Fantom 编译方法，不在 FIN 数据库的 func 记录中，
因此 MCP 原本完全看不到它们。

### 新增功能

#### SB-03: finCopilotAsk 统一入口
- 在 FIN 中创建 `finCopilotAsk` func 记录
- 调用 finCopilot 的 `finMcpChatStart` + `finMcpChatPoll` 轮询
- 支持 capability 领域路由（9 个领域）和 effort 质量控制

#### SB-05/SB-06: 多 Pod 自动发现
- 通过 FIN 的 `defs()` API 自动发现 @Axon 函数
- 环境变量 `MCP_AUTO_DISCOVER_PODS` 配置要发现的 pod 列表
- 当前支持: finCopilot (102 函数), coolMatrix (413 函数), chillerOpt (4 函数)
- 无需手动注册，新增 pod 只需加到环境变量并重启

#### SB-07: 工具缓存
- 60 秒 TTL 缓存，避免每次 `tools/list` 都查 FIN
- `_invalidate_tool_cache()` 手动刷新

#### SB-08: 参数 Schema 推导
- `_FN_SIGNATURES` 字典维护函数签名
- 支持 doc 字符串解析作为回退方案

#### SB-10: 清理旧 HVAC 工具
- 移除 53 个 `hx*` + 6 个 `job*` 遗留工具（移除 skyforgeMcp 标签）
- 工具总数: 172 → 113

#### SB-11: MCP Prompts 支持
- 12 个中文领域 prompt 模板（9 个领域 + 3 个工作流）
- 纯 Python 实现，不依赖 FIN 侧的 `fetchMcpPrompts()`
- 每个 prompt 包含系统指令（角色定位 + 工作流程）+ 用户问题模板

#### SB-09: 工具分组加载
- 7 个功能领域分组: query, energy, diagnosis, control, coolmatrix_admin, admin, ai_chat
- 默认只返回 14 个基础工具（含组管理工具）
- AI 通过 `getToolGroups` / `setToolGroup` 切换工具组
- Token 消耗降低约 97%（从 533 工具降到 14-53 个）

### 技术变更

| 文件 | 变更 |
|:-----|:------|
| `app/skyspark/client.py` | SkySpark 客户端 — 自动发现、缓存、分组、参数推导 |
| `app/skyspark/converters.py` | 类型转换增强 |
| `app/skyspark/grid.py` | phable 0.1.27/0.1.28 兼容性 |
| `app/tools/axon_tools.py` | 硬编码工具定义（增加 CRUD 工具） |
| `skyforge_mcp/main.py` | MCP 服务 — 组管理工具、prompt 模板 |
| `.env` | MCP_AUTO_DISCOVER_PODS 配置 |

### 部署说明
- FIN 5.3 运行中，端口 8800
- SkyForge MCP 运行中，端口 8001
- PM2 管理: `pm2 restart skyforge-mcp-fin`
