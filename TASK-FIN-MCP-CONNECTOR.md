# Go 版 FIN Framework MCP 连接器 — 开发任务书 v1.0

> 交付对象：datatell HayEdge v1.2.0 北向连接器底座（纯 Go、零 CGO）
> 编写：Hermes（2026-08-27）｜执行：Claude Code ｜验收：Hermes
> 工作区：/mnt/hermes-work/hayedge-go（已 clone datatell529/skyforge-mcp）

---

## 0. 硬性约束（必须全部满足）

1. **纯 Go 原生，零 CGO**：禁止 cgo、禁止调用 Python/Node 外部进程。`CGO_ENABLED=0 go build` 必须通过。
2. **Go 版本**：go.mod 声明 `go 1.22`（本机 go1.26.4 编译）。
3. **命名合规**：代码、注释、日志、字符串中**严禁出现 "edgeCore" 或 "anviod"**。品牌统一为 datatell HayEdge。
4. **熔断隔离**：FIN 请求超时（默认 5s，config 可配）即抛错，任何 goroutine 不得阻塞底座主采集/联锁线程。
5. **不写任何无关文件**：只新增下述目录/文件，不动仓库现有 Python 部分（app/、skyforge_mcp/、main.py 等）。

## 1. 交付结构（按此路径创建）

```
pkg/hayedge/connectors/fin/
├── client.go          # FIN HTTP / SCRAM 鉴权 + Haystack REST API SDK
├── types.go           # 数据结构体：Config、HGrid、Haystack 值类型、工具参数
└── client_test.go     # SDK 单元测试（httptest Mock Server 验证）

pkg/hayedge/mcp/fin/
├── tools.go           # 7 个 MCP Tools 声明（name/description/inputSchema）
└── handler.go         # MCP Tools JSON-RPC 请求与 Client SDK 的桥接处理

cmd/fin-mcp/main.go    # 可选：最小独立 MCP Server（stdio + HTTP），供 Hermes 联调
```

根目录新增 `go.mod`：`module github.com/datatell529/skyforge-mcp`（与仓库名一致，不含违禁词）。
依赖仅允许：`github.com/mark3labs/mcp-go`（MCP SDK，纯 Go）+ Go 标准库。**禁止 xdg-go/scram**——SCRAM 按 §3.2 的 Project Haystack 流程手写（几十行，避免额外依赖）。

## 2. 7 个 MCP 工具规格

### 2.1 fin_test_connection
- 功能：测试与 FIN 站点的网络连通性及 Haystack API 鉴权有效性（动态连接参数，不依赖 config）
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "url": {"type": "string", "description": "FIN Haystack 端点 URL，如 http://192.168.1.100:8800/api/demo"},
    "auth_type": {"type": "string", "enum": ["scram", "basic"], "default": "scram"},
    "username": {"type": "string"},
    "password": {"type": "string"}
  },
  "required": ["url", "username", "password"]
}
```
- 输出：`{"success": true, "fin_version": "5.3", "server_name": "..."}`；失败返回 `{"success": false, "error": "..."}`
- 实现：SCRAM 握手（或 basic）→ 调 about → 取 `finVersion`/`serverName` 字段（注意 FIN about 返回字段名：`finVersion`、`serverName`、`version`）

### 2.2 fin_sync_point_tree
- 功能：将调用方传入的语义点位（已 verified）与 Equip/Site 关联，批量推送到 FIN DB 建点
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "site_ref": {"type": "string", "description": "目标 FIN Site Ref，如 @p:demo:r:site01"},
    "equip_ref": {"type": "string", "description": "目标 FIN Equip Ref，如 @p:demo:r:ahu01（可选，缺省则建到 site 下）"},
    "points": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "spec": {"type": "string", "description": "Xeto Spec，如 ph.points::AirTempSensor"},
          "tags": {"type": "object", "description": "Haystack Marker 字典，true 表示 Marker"},
          "unit": {"type": "string"},
          "kind": {"type": "string", "enum": ["Number", "Bool", "Str"]}
        },
        "required": ["name", "kind"]
      }
    }
  },
  "required": ["site_ref", "points"]
}
```
- 输出：`{"synced_count": N, "errors": []}`（errors 数组元素 `{"name": "...", "error": "..."}`）
- 实现要点：
  - 每个 point 生成 FIN 记录：`dis`=name、`point`、`kind`（Number/Bool/Str → 对应 kind 标签）、`siteRef`=Ref、`equipRef`（若有）、`unit`（若有）、`spec` 存为 `spec` 标签（FIN 会解析 Xeto spec，若 FIN 无该 spec 定义则记录仍创建，spec 以标签形式保留，不因 spec 校验失败而整体回滚）
  - `tags` 字典：值为 `true` 的 → Marker；值为字符串 → 字符串；以 `@` 开头的字符串 → Ref（去掉 @，按 FIN Ref id 解析）；数字 → Number
  - 批量写入用 FIN commit API（add 模式），一次 commit 提交所有点；单点失败记录到 errors 不中断
  - Ref 必须是 Ref 类型（不是字符串），Marker 必须是 Marker 类型（不是布尔）——这是验收重点，见 §4

### 2.3 fin_query_entities
- 功能：Haystack Filter 语法查询 FIN 现有设备/点位/逻辑配置
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "filter": {"type": "string", "description": "Haystack Filter，如 'point and temp and siteRef==@p:demo:r:site01'"},
    "limit": {"type": "integer", "default": 100}
  },
  "required": ["filter"]
}
```
- 输出：标准 HGrid JSON 数据树（meta/cols/rows 完整保留，供 LLM 读取）
- 实现：FIN `read` op（GET/POST /api/{project}/read，filter+limit），返回 HGrid JSON 原样清洗输出（Ref/Marker 类型信息保留在 JSON 中）

### 2.4 fin_write_override
- 功能：向 FIN 下发调试覆盖/设定值，16 级优先级 + 超时自动释放
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "point_id": {"type": "string", "description": "FIN DB 中的 Point Ref，如 @p:demo:r:p123"},
    "val": {"description": "下发的控制值 (Number/Bool/Str)"},
    "priority": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8},
    "duration_sec": {"type": "integer", "description": "覆盖维持秒数，0 为永久覆盖"}
  },
  "required": ["point_id", "val"]
}
```
- 输出：`{"success": true, "applied_priority": 8, "point_id": "@p:demo:r:p123"}`
- 实现要点：
  - FIN `pointWrite` op：POST /api/{project}/pointWrite，body `{"id": "<ref id 去掉@>", "priority": N, "val": {"_kind": "Number", "val": 42.0}}`
  - val 编码：Number → `{"_kind":"Number","val":<num>}`；Bool → `{"_kind":"Bool","val":true}`；Str → `{"_kind":"Str","val":"..."}`（JSON 数字/布尔/字符串按实际类型）
  - **超时释放**：`duration_sec > 0` 时启动 goroutine + `time.After(duration_sec)`，到点自动向同一 point_id 同一 priority 回写 null 释放（`{"_kind":"NA"}` 或 FIN 接受的释放格式，联调确认；实现时先按 `{"_kind":"NA"}` 写，Mock 测试通过即可）
  - `duration_sec = 0`：永久覆盖，直到人工释放或新写入
  - **不持久化**：网关重启后未到期任务即释放（安全优先，内存态即可）
  - 记录已生效覆盖（map[point_id+priority]timer），同点同优先级再次写入先取消旧定时器

### 2.5 fin_update_entity（新增，补"改"）
- 功能：按 Ref 更新 FIN 记录标签（增/改标签，可移除标签）
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "id": {"type": "string", "description": "记录 Ref，如 @p:demo:r:p123"},
    "tags": {"type": "object", "description": "要写入/覆盖的标签字典"},
    "remove_tags": {"type": "array", "items": {"type": "string"}, "description": "要移除的标签名列表"}
  },
  "required": ["id", "tags"]
}
```
- 输出：`{"success": true, "id": "@p:demo:r:p123"}`
- 实现：FIN commit op（update 模式），diff 记录携带 id + 新标签 + remove 标签。类型转换规则同 §2.2

### 2.6 fin_delete_entity（新增，补"删"）
- 功能：按 Ref 删除 FIN 记录
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "id": {"type": "string", "description": "记录 Ref，如 @p:demo:r:p123"},
    "force": {"type": "boolean", "default": false, "description": "true 时连子记录一并删除（如删 Equip 连带其 points）；false 且有子记录引用时报错"}
  },
  "required": ["id"]
}
```
- 输出：`{"success": true, "deleted": "@p:demo:r:p123"}`
- 实现：FIN commit op（remove 模式）。force=true 时先查子记录（`equipRef==@<id>`）再一并 remove。**注意 FIN/SkySpark 的 remove 可能要求 mod 标签（乐观锁），联调时确认是否需要先 read 拿 mod 再删；Mock 测试按 commit remove 标准格式**

### 2.7 fin_eval_axon（新增，只读兜底）
- 功能：执行只读 Axon 表达式（调试/复杂查询兜底）
- 输入 Schema：
```json
{
  "type": "object",
  "properties": {
    "expr": {"type": "string", "description": "只读 Axon 表达式，如 readAll(point)"}
  },
  "required": ["expr"]
}
```
- 输出：清洗后的 JSON（同 fin_query_entities 格式）
- 实现：FIN `eval` op（POST /api/{project}/eval，body `{"expr": "..."}`）
- **安全检查**：表达式含写操作关键词即拒绝返回错误。关键词列表：`commit`, `commitAdd`, `commitUpdate`, `commitRemove`, `ioWriteTrio`, `ioWriteZinc`, `purge`, `purgeAll`, `install`, `uninstall`, `delete`, `remove`（不区分大小写；"remove" 需谨慎——只匹配 `remove` 作为独立函数调用时拒绝，简单做法：只要出现这些子串即拒绝，宁可误杀）

## 3. FIN Haystack API 技术细节

### 3.1 端点（base = config.url，如 http://host:8800/api/demo）
| op | 方法/路径 | 说明 |
|---|---|---|
| about | GET /api/{project}/about | 服务器信息（SCRAM 握手也走它） |
| read | POST /api/{project}/read | body: `{"filter": "...", "limit": 100}` |
| pointWrite | POST /api/{project}/pointWrite | body: `{"id": "<refid>", "priority": 8, "val": {...}}` |
| commit | POST /api/{project}/commit | 批量增/改/删记录，HGrid JSON body |
| eval | POST /api/{project}/eval | body: `{"expr": "..."}` |

认证后所有请求带 `Authorization: Bearer <authToken>`。Content-Type: `application/json`。
（注：read 也可 GET 带 query 参数，但统一用 POST JSON 更稳）

### 3.2 SCRAM 鉴权（Project Haystack 三阶段握手，参考 phable 实现）
路径：`/mnt/hermes-work/skyforge-mcp/.venv/lib/python3.14/site-packages/phable/auth/scram.py`（完整参考）

**阶段 1 HELLO**：GET base+/about，Header `Authorization: HELLO username=<base64url(username)>`
→ 从响应头 `WWW-Authenticate` 解析 `handshakeToken=xxx` 和 `hash=SHA-256`（用正则 `handshakeToken=[a-zA-Z0-9+=/]+`、`hash=(SHA-256)`）

**阶段 2 client-first**：
- 生成客户端 nonce：12 字节随机数的 hex（24 字符）
- `c1_bare = "n=<username>,r=<c_nonce>"`，`gs2 = "n,,"`，`c1_msg = gs2 + c1_bare`，base64url 编码（**去掉 = 填充**）
- GET base+/about，Header `Authorization: SCRAM data=<b64(c1_msg)>, handshakeToken=<handshakeToken>`
→ 从 `WWW-Authenticate` 的 `data=<b64(...)>` 解析（base64url 解码，补 = 填充）：`r=<s_nonce>,s=<salt>,i=<iterations>`，逗号分隔（解码后 replace(" ","") 再 split(",")）

**阶段 3 client-final**：
- `s1_msg = "r=<s_nonce>,s=<salt>,i=<iter>"`（原样）
- `client_final_no_proof = "c=<b64url("n,,")>,r=<s_nonce>"`
- `auth_message = "n=<user>,r=<c_nonce>,<s1_msg>,<client_final_no_proof>"`
- 计算（RFC5802，SHA-256）：
  - `salted_password = PBKDF2-HMAC-SHA256(password, urlsafe_b64decode(salt), iterations, 32)`（**salt 用 urlsafe_b64decode 解码**）
  - `client_key = HMAC(salted_password, "Client Key")`
  - `stored_key = SHA256(client_key)`
  - `client_signature = HMAC(stored_key, auth_message)`
  - `client_proof = client_key XOR client_signature`，base64url
  - `client_final = client_final_no_proof + ",p=" + b64url(client_proof)`，再整体 base64url
- GET base+/about，Header `Authorization: SCRAM data=<b64(client_final)>, handshakeToken=<handshakeToken>`
→ 从 `WWW-Authenticate` 解析 `authToken=xxx`（到逗号为止）和 `data=<b64(...)>`（解码后 `v=<server_signature>`）
- 校验 server_signature：`server_key = HMAC(salted_password, "Server Key")`；`expected = b64(HMAC(server_key, auth_message))`；不匹配报鉴权失败

**阶段 4**：后续所有请求 `Authorization: Bearer <authToken>`

HTTP 401/403 处理：401 → 重新握手；403 → 凭证错误（AuthError）。
注意：握手过程服务器可能返回 401 状态码但带 WWW-Authenticate 头——**要读响应头而不是只看状态码**（phable 的 `_ph_scram_get` 在 HTTPError 时取 `e.headers`）。

**basic 模式**（auth_type=basic）：请求带 `Authorization: Basic base64(user:pass)` 即可（FIN 也支持 basic）。

### 3.3 HGrid JSON 格式（响应与 commit body 共用）
- 响应：`{"meta": {...}, "cols": [{"name": "...", "meta": {"_kind": "Ref"}}, ...], "rows": [{...}]}`
- 值编码（_kind 体系）：
  - Ref：`{"_kind": "Ref", "val": "p:demo:r:xxx", "dis": "显示名"}`
  - Marker：`{"_kind": "Marker"}`
  - Number：`{"_kind": "Number", "val": 42.0, "unit": "kW"}`
  - Str：`{"_kind": "Str", "val": "..."}`
  - Bool：`{"_kind": "Bool", "val": true}`
  - NA：`{"_kind": "NA"}`
- Go 类型设计（types.go）：
  - `type Kind string`：Ref/Marker/Number/Str/Bool/NA
  - `type Val struct { Kind Kind; Val any; Dis string; Unit string }`（json.RawMessage 兼容解析）
  - `type HGrid struct { Meta map[string]any; Cols []Col; Rows []map[string]Val }`
- **commit body 格式**（add/update/remove 三种模式，参考 phable `io/json_encoder.py` 和 `haystack_client.py` 的 commit 实现）：
  - commit meta 携带模式标志；rows 为记录。具体编码参照 phable 实现（路径见下），Mock 测试按同一格式往返验证
  - Ref id 在 commit 中如何编码（id 字段用 `{"_kind":"Ref","val":"p:demo:r:xxx"}`）以 phable 实现为准

### 3.4 commit 参考（phable 已验证可用的写路径）
参考文件：
- `/mnt/hermes-work/skyforge-mcp/.venv/lib/python3.14/site-packages/phable/haystack_client.py`（`commit_add`/`commit_update`/`commit_remove` 方法）
- `/mnt/hermes-work/skyforge-mcp/.venv/lib/python3.14/site-packages/phable/io/json_encoder.py`（HGrid JSON 编码）
- `/mnt/hermes-work/skyforge-mcp/app/skyspark/client.py`（skyforge-mcp 的 commit_add 封装 + Ref/Marker 类型转换 `_ref_pat` 正则）
- `/mnt/hermes-work/skyforge-mcp/app/skyspark/converters.py`（`_to_axon` Ref 处理参考）

已知坑（必须规避）：
1. **FIN 的写响应可能是 CallError 包装**——成功也包错。Go 版解析时：commit/pointWrite 请求 2xx 即视为成功（不因 body 形状怪而报错）；必要时读回验证。
2. **Ref 必须是 Ref 类型，Marker 必须是 Marker 类型**——字符串/布尔是错误的（验收重点）。

## 4. 单元测试要求（client_test.go）

用 `net/http/httptest` 起 Mock Server 模拟 FIN，覆盖：
1. **SCRAM 全流程**：Mock 三阶段握手（返回 handshakeToken/SHA-256 → nonce/salt/iter → authToken+server_signature），验证 client 能完成握手并拿到 token；server 签名不匹配时抛错
2. **about**：返回 finVersion/serverName，解析正确
3. **read**：filter+limit 正确传递，HGrid JSON 解析（含 Ref/Marker/Number/Str/Bool/NA 各类型）
4. **pointWrite**：priority/val 编码正确；超时释放——Mock 里验证 duration_sec 到点后自动发第二次 pointWrite（id+priority 相同，val 为释放值）；同点同优先级重写取消旧定时器
5. **commit add**：批量建点，Ref/Marker 类型正确（Mock 端校验收到的 JSON 中 Ref 是 _kind Ref、Marker 是 _kind Marker）
6. **commit update/remove**：diff 正确（含 remove 标签/记录）
7. **eval 安全检查**：含写关键词的表达式被拒
8. **超时熔断**：Mock 延迟超过 timeout_sec，client 快速返回错误（context deadline）

## 5. 验收标准（Hermes 执行）

```bash
cd /mnt/hermes-work/hayedge-go
CGO_ENABLED=0 go build ./pkg/hayedge/...      # 零报错、零 CGO
go test ./pkg/hayedge/...                      # 全绿
grep -rniE "edgecore|anviod" pkg/ cmd/ go.mod  # 无输出（命名合规）
go vet ./pkg/hayedge/...                       # 无告警
```

## 6. 交付后（Hermes 负责，Claude Code 不需做）
- 启动本地 FIN 5.3（/mnt/fin-5.3）真机联调 7 工具（demo/test1 项目）
- push 到 origin（datatell529/skyforge-mcp）
- 命名合规全仓复查（含 README 若需更新，先不动 Python 部分）

## 7. 完成定义（Definition of Done）
- [ ] go.mod + pkg/hayedge/connectors/fin/{client,types,client_test}.go
- [ ] pkg/hayedge/mcp/fin/{tools,handler}.go（7 工具完整 Schema + 桥接）
- [ ] cmd/fin-mcp/main.go（stdio + HTTP 双模式 MCP Server，7 工具可被 tools/list 列出）
- [ ] 单测全绿（§4 八项覆盖）
- [ ] CGO_ENABLED=0 构建通过
- [ ] 全仓无 edgeCore/anviod 字样
