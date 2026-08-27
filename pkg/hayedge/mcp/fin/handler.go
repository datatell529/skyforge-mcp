package finmcp

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	finconn "github.com/datatell529/skyforge-mcp/pkg/hayedge/connectors/fin"
)

// Handler 桥接 MCP JSON-RPC 请求与 FIN Client SDK。
// client 为默认 FIN 客户端（供除 fin_test_connection 外的工具使用），可为 nil（此时这些工具返回未配置错误）。
type Handler struct {
	client *finconn.Client
}

// NewHandler 创建工具桥接处理器。
func NewHandler(client *finconn.Client) *Handler {
	return &Handler{client: client}
}

// Register 将 7 个工具注册到 MCP 服务器。
func (h *Handler) Register(s *server.MCPServer) {
	s.AddTools(ServerTools(h)...)
}

// ServerTools 返回 7 个 (Tool, Handler) 对，供 server.AddTools 使用。
func ServerTools(h *Handler) []server.ServerTool {
	handler := h.handleCall
	tools := AllTools()
	out := make([]server.ServerTool, 0, len(tools))
	for _, t := range tools {
		out = append(out, server.ServerTool{Tool: t, Handler: handler})
	}
	return out
}

// handleCall 统一分发 7 个工具的调用。
func (h *Handler) handleCall(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	switch req.Params.Name {
	case "fin_test_connection":
		return h.handleTestConnection(ctx, req)
	case "fin_sync_point_tree":
		return h.handleSyncPointTree(ctx, req)
	case "fin_query_entities":
		return h.handleQueryEntities(ctx, req)
	case "fin_write_override":
		return h.handleWriteOverride(ctx, req)
	case "fin_update_entity":
		return h.handleUpdateEntity(ctx, req)
	case "fin_delete_entity":
		return h.handleDeleteEntity(ctx, req)
	case "fin_eval_axon":
		return h.handleEvalAxon(ctx, req)
	default:
		return mcp.NewToolResultErrorf("unknown tool: %s", req.Params.Name), nil
	}
}

// defaultClient 返回默认客户端；未配置时返回错误结果。
func (h *Handler) defaultClient() (*finconn.Client, error) {
	if h.client == nil {
		return nil, fmt.Errorf("FIN client not configured")
	}
	return h.client, nil
}

// handleTestConnection 处理 fin_test_connection（§2.1）。
// 失败统一输出 {"success": false, "error": "..."}。
func (h *Handler) handleTestConnection(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.TestConnectionParams
	if err := bindParams(&p, req); err != nil {
		return jsonResult(map[string]any{"success": false, "error": fmt.Sprintf("invalid params: %v", err)})
	}
	if p.URL == "" || p.Username == "" || p.Password == "" {
		return jsonResult(map[string]any{"success": false, "error": "url, username, password are required"})
	}
	if p.AuthType == "" {
		p.AuthType = finconn.AuthTypeSCRAM
	}
	client, err := finconn.NewClient(finconn.Config{
		URL:      p.URL,
		AuthType: p.AuthType,
		Username: p.Username,
		Password: p.Password,
	})
	if err != nil {
		return jsonResult(map[string]any{"success": false, "error": fmt.Sprintf("failed to create FIN client: %v", err)})
	}
	result, err := client.TestConnection(ctx)
	if err != nil {
		// TestConnection 失败时已返回 success=false 的 map，这里直接输出
		return jsonResult(result)
	}
	return jsonResult(result)
}

// handleSyncPointTree 处理 fin_sync_point_tree（§2.2）。
func (h *Handler) handleSyncPointTree(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.SyncPointTreeParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.SiteRef == "" {
		return mcp.NewToolResultError("site_ref is required"), nil
	}
	if len(p.Points) == 0 {
		return mcp.NewToolResultError("points is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	synced, errs, err := client.SyncPoints(ctx, p.SiteRef, p.EquipRef, p.Points)
	if err != nil {
		return jsonResult(map[string]any{
			"synced_count": synced,
			"errors":       errs,
			"error":        err.Error(),
		})
	}
	return jsonResult(map[string]any{"synced_count": synced, "errors": errs})
}

// handleQueryEntities 处理 fin_query_entities（§2.3）。
func (h *Handler) handleQueryEntities(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.QueryEntitiesParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.Filter == "" {
		return mcp.NewToolResultError("filter is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	grid, err := client.Read(ctx, p.Filter, p.Limit)
	if err != nil {
		return mcp.NewToolResultErrorf("query failed: %v", err), nil
	}
	return jsonResult(grid)
}

// handleWriteOverride 处理 fin_write_override（§2.4）。
func (h *Handler) handleWriteOverride(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.WriteOverrideParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.PointID == "" {
		return mcp.NewToolResultError("point_id is required"), nil
	}
	if len(p.Val) == 0 || string(p.Val) == "null" {
		return mcp.NewToolResultError("val is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	priority, err := client.WriteOverride(ctx, p)
	if err != nil {
		return mcp.NewToolResultErrorf("write override failed: %v", err), nil
	}
	return jsonResult(map[string]any{
		"success":          true,
		"applied_priority": priority,
		"point_id":         p.PointID,
	})
}

// handleUpdateEntity 处理 fin_update_entity（§2.5）。
func (h *Handler) handleUpdateEntity(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.UpdateEntityParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.ID == "" {
		return mcp.NewToolResultError("id is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	if err := client.UpdateEntity(ctx, p.ID, p.Tags, p.RemoveTags); err != nil {
		return mcp.NewToolResultErrorf("update failed: %v", err), nil
	}
	return jsonResult(map[string]any{"success": true, "id": p.ID})
}

// handleDeleteEntity 处理 fin_delete_entity（§2.6）。
func (h *Handler) handleDeleteEntity(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.DeleteEntityParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.ID == "" {
		return mcp.NewToolResultError("id is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	if err := client.DeleteEntity(ctx, p.ID, p.Force); err != nil {
		return mcp.NewToolResultErrorf("delete failed: %v", err), nil
	}
	return jsonResult(map[string]any{"success": true, "deleted": p.ID})
}

// handleEvalAxon 处理 fin_eval_axon（§2.7）。
func (h *Handler) handleEvalAxon(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	var p finconn.EvalAxonParams
	if err := bindParams(&p, req); err != nil {
		return mcp.NewToolResultErrorf("invalid params: %v", err), nil
	}
	if p.Expr == "" {
		return mcp.NewToolResultError("expr is required"), nil
	}
	client, err := h.defaultClient()
	if err != nil {
		return mcp.NewToolResultError(err.Error()), nil
	}
	grid, err := client.Eval(ctx, p.Expr)
	if err != nil {
		return mcp.NewToolResultErrorf("eval failed: %v", err), nil
	}
	return jsonResult(grid)
}

// bindParams 将请求参数绑定到目标结构。
func bindParams(target any, req mcp.CallToolRequest) error {
	return req.BindArguments(target)
}

// jsonResult 输出 JSON 文本结果。
func jsonResult(data any) (*mcp.CallToolResult, error) {
	res, err := mcp.NewToolResultJSON(data)
	if err != nil {
		slog.Error("failed to marshal tool result", "error", err)
		return mcp.NewToolResultErrorf("failed to marshal result: %v", err), nil
	}
	return res, nil
}
