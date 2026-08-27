// Package finmcp 提供 datatell HayEdge FIN Framework MCP 连接器的 7 个工具声明与桥接处理。
//
// tools.go 定义 7 个 MCP Tools（name/description/inputSchema），与任务书 §2 完全对齐：
//   fin_test_connection / fin_sync_point_tree / fin_query_entities / fin_write_override
//   fin_update_entity / fin_delete_entity / fin_eval_axon
package finmcp

import (
	"github.com/mark3labs/mcp-go/mcp"
)

// AllTools 返回 7 个 MCP 工具声明。
func AllTools() []mcp.Tool {
	return []mcp.Tool{
		ToolTestConnection(),
		ToolSyncPointTree(),
		ToolQueryEntities(),
		ToolWriteOverride(),
		ToolUpdateEntity(),
		ToolDeleteEntity(),
		ToolEvalAxon(),
	}
}

// ToolTestConnection 测试与 FIN 站点的网络连通性及 Haystack API 鉴权有效性（§2.1）。
func ToolTestConnection() mcp.Tool {
	return mcp.NewTool("fin_test_connection",
		mcp.WithDescription("测试与 FIN 站点的网络连通性及 Haystack API 鉴权有效性（动态连接参数，不依赖 config）"),
		mcp.WithString("url",
			mcp.Required(),
			mcp.Description("FIN Haystack 端点 URL，如 http://192.168.1.100:8800/api/demo"),
		),
		mcp.WithString("auth_type",
			mcp.Description("鉴权方式"),
			mcp.Enum("scram", "basic"),
			mcp.DefaultString("scram"),
		),
		mcp.WithString("username", mcp.Required(), mcp.Description("用户名")),
		mcp.WithString("password", mcp.Required(), mcp.Description("密码")),
	)
}

// ToolSyncPointTree 将语义点位与 Equip/Site 关联，批量推送到 FIN DB 建点（§2.2）。
func ToolSyncPointTree() mcp.Tool {
	return mcp.NewTool("fin_sync_point_tree",
		mcp.WithDescription("将调用方传入的语义点位（已 verified）与 Equip/Site 关联，批量推送到 FIN DB 建点"),
		mcp.WithString("site_ref", mcp.Required(), mcp.Description("目标 FIN Site Ref，如 @p:demo:r:site01")),
		mcp.WithString("equip_ref", mcp.Description("目标 FIN Equip Ref，如 @p:demo:r:ahu01（可选，缺省则建到 site 下）")),
		mcp.WithArray("points",
			mcp.Required(),
			mcp.Description("要推送的语义点位数组"),
			mcp.Items(map[string]any{
				"type": "object",
				"properties": map[string]any{
					"name": map[string]any{"type": "string", "description": "点位名称"},
					"spec": map[string]any{"type": "string", "description": "Xeto Spec，如 ph.points::AirTempSensor"},
					"tags": map[string]any{"type": "object", "description": "Haystack Marker 字典，true 表示 Marker"},
					"unit": map[string]any{"type": "string", "description": "单位"},
					"kind": map[string]any{"type": "string", "enum": []string{"Number", "Bool", "Str"}},
				},
				"required": []string{"name", "kind"},
			}),
		),
	)
}

// ToolQueryEntities 使用 Haystack Filter 语法查询 FIN 实体（§2.3）。
func ToolQueryEntities() mcp.Tool {
	return mcp.NewTool("fin_query_entities",
		mcp.WithDescription("Haystack Filter 语法查询 FIN 现有设备/点位/逻辑配置"),
		mcp.WithString("filter",
			mcp.Required(),
			mcp.Description("Haystack Filter，如 'point and temp and siteRef==@p:demo:r:site01'"),
		),
		mcp.WithInteger("limit", mcp.Description("返回数量上限"), mcp.DefaultNumber(100)),
	)
}

// ToolWriteOverride 向 FIN 下发调试覆盖/设定值，16 级优先级 + 超时自动释放（§2.4）。
func ToolWriteOverride() mcp.Tool {
	return mcp.NewTool("fin_write_override",
		mcp.WithDescription("向 FIN 下发调试覆盖/设定值，16 级优先级 + 超时自动释放"),
		mcp.WithString("point_id", mcp.Required(), mcp.Description("FIN DB 中的 Point Ref，如 @p:demo:r:p123")),
		mcp.WithAny("val", mcp.Required(), mcp.Description("下发的控制值 (Number/Bool/Str)")),
		mcp.WithInteger("priority",
			mcp.Description("优先级 1-16"),
			mcp.DefaultNumber(8),
			mcp.Min(1),
			mcp.Max(16),
		),
		mcp.WithInteger("duration_sec", mcp.Description("覆盖维持秒数，0 为永久覆盖")),
	)
}

// ToolUpdateEntity 按 Ref 更新 FIN 记录标签（§2.5）。
func ToolUpdateEntity() mcp.Tool {
	return mcp.NewTool("fin_update_entity",
		mcp.WithDescription("按 Ref 更新 FIN 记录标签（增/改标签，可移除标签）"),
		mcp.WithString("id", mcp.Required(), mcp.Description("记录 Ref，如 @p:demo:r:p123")),
		mcp.WithObject("tags", mcp.Required(), mcp.Description("要写入/覆盖的标签字典")),
		mcp.WithArray("remove_tags",
			mcp.Description("要移除的标签名列表"),
			mcp.WithStringItems(),
		),
	)
}

// ToolDeleteEntity 按 Ref 删除 FIN 记录（§2.6）。
func ToolDeleteEntity() mcp.Tool {
	return mcp.NewTool("fin_delete_entity",
		mcp.WithDescription("按 Ref 删除 FIN 记录"),
		mcp.WithString("id", mcp.Required(), mcp.Description("记录 Ref，如 @p:demo:r:p123")),
		mcp.WithBoolean("force",
			mcp.Description("true 时连子记录一并删除（如删 Equip 连带其 points）；false 且有子记录引用时报错"),
			mcp.DefaultBool(false),
		),
	)
}

// ToolEvalAxon 执行只读 Axon 表达式（§2.7）。
func ToolEvalAxon() mcp.Tool {
	return mcp.NewTool("fin_eval_axon",
		mcp.WithDescription("执行只读 Axon 表达式（调试/复杂查询兜底）"),
		mcp.WithString("expr", mcp.Required(), mcp.Description("只读 Axon 表达式，如 readAll(point)")),
	)
}
