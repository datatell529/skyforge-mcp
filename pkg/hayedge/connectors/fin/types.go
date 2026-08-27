// Package fin 实现 datatell HayEdge 北向连接器底座中的 FIN Framework MCP 连接器。
//
// types.go 为骨架文件，仅包含类型定义（无实现逻辑）：
//   - Config：FIN 连接器配置
//   - Kind / Val：Haystack 值类型（_kind 编码体系）
//   - Col / HGrid：FIN 标准响应网格
//   - 7 个 MCP 工具的参数类型
//
// 规范要求：
//   - 纯 Go 原生、零 CGO（CGO_ENABLED=0 可构建）
package fin

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// AuthType FIN 鉴权方式。
type AuthType string

const (
	// AuthTypeSCRAM 使用 Project Haystack 三阶段 SCRAM 握手（默认）。
	AuthTypeSCRAM AuthType = "scram"
	// AuthTypeBasic 使用 HTTP Basic 鉴权。
	AuthTypeBasic AuthType = "basic"
)

// DefaultTimeout 单次 FIN 请求超时默认值（任务书 §0.4：默认 5s，熔断隔离）。
const DefaultTimeout = 5 * time.Second

// Config FIN 连接器配置。URL 形如 http://host:8800/api/demo。
type Config struct {
	// URL FIN Haystack 端点 URL。
	URL string `json:"url"`
	// AuthType 鉴权方式，scram 或 basic，缺省 scram。
	AuthType AuthType `json:"auth_type,omitempty"`
	// Username 鉴权用户名。
	Username string `json:"username"`
	// Password 鉴权密码。
	Password string `json:"password"`
	// Timeout 单次请求超时；<=0 时使用 DefaultTimeout。
	Timeout time.Duration `json:"timeout,omitempty"`
}

// Kind Haystack 值类型（_kind 体系，任务书 §3.3）。
type Kind string

const (
	KindRef    Kind = "Ref"
	KindMarker Kind = "Marker"
	KindNumber Kind = "Number"
	KindStr    Kind = "Str"
	KindBool   Kind = "Bool"
	KindNA     Kind = "NA"
	// KindRemove 用于 commit update 时移除标签（Haystack Remove）。
	KindRemove Kind = "Remove"
	// KindDateTime 表示 Haystack DateTime（Zinc 如 2026-08-27T06:00:00Z）。
	// 真机 FIN 3.9 的 mod 乐观锁标签即为 DateTime 类型，而非 Ref。
	KindDateTime Kind = "DateTime"
	// KindDate 与 KindTime 表示 Haystack Date/Time（Zinc 如 2026-08-27 / 06:00:00）。
	KindDate Kind = "Date"
	KindTime Kind = "Time"
)

// Val 表示一个 Haystack 标量值，兼容 JSON _kind 编码。
//
// 例：
//
//	Ref:    {"_kind":"Ref","val":"p:demo:r:xxx","dis":"显示名"}
//	Marker: {"_kind":"Marker"}
//	Number: {"_kind":"Number","val":42.0,"unit":"kW"}
//	Str:    {"_kind":"Str","val":"..."}
//	Bool:   {"_kind":"Bool","val":true}
//	NA:     {"_kind":"NA"}
type Val struct {
	Kind Kind `json:"_kind"`
	// Val 为底层值：Number 对应 float64，Bool 对应 bool，Str/Ref 对应 string，
	// Marker/NA/Remove 为 nil。使用 any 以便 json.RawMessage 兼容解析。
	Val  any    `json:"val,omitempty"`
	Dis  string `json:"dis,omitempty"`
	Unit string `json:"unit,omitempty"`
}

// MarshalJSON 将 Val 编码为 Haystack JSON 值对象（任务书 §3.3）。
// 输出 _kind 一律使用规范大小写（Ref/Marker/Number/Str/Bool/NA/Remove）。
func (v Val) MarshalJSON() ([]byte, error) {
	m := map[string]any{"_kind": string(v.Kind)}
	if v.Val != nil {
		m["val"] = v.Val
	}
	if v.Dis != "" {
		m["dis"] = v.Dis
	}
	if v.Unit != "" {
		m["unit"] = v.Unit
	}
	return json.Marshal(m)
}

// UnmarshalJSON 解析 Haystack JSON 值对象；同时容忍：
//   - _kind 大小写变体（Ref/ref、Marker/marker 等）
//   - 裸标量（字符串 → Str、数字 → Number、布尔 → Bool、null → NA）
func (v *Val) UnmarshalJSON(data []byte) error {
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return errors.New("empty Haystack value JSON")
	}
	switch trimmed[0] {
	case '"': // 裸字符串 → Str
		var s string
		if err := json.Unmarshal(trimmed, &s); err != nil {
			return err
		}
		*v = Val{Kind: KindStr, Val: s}
		return nil
	case '{': // _kind 编码对象
		var probe struct {
			Kind string          `json:"_kind"`
			Val  json.RawMessage `json:"val"`
			Dis  string          `json:"dis"`
			Unit string          `json:"unit"`
		}
		if err := json.Unmarshal(trimmed, &probe); err != nil {
			return err
		}
		if probe.Kind == "" {
			return fmt.Errorf("Haystack value object missing _kind: %s", truncateJSON(trimmed))
		}
		*v = Val{
			Kind: Kind(normalizeKind(probe.Kind)),
			Dis:  probe.Dis,
			Unit: probe.Unit,
		}
		if len(probe.Val) == 0 {
			return nil
		}
		switch v.Kind {
		case KindRef, KindStr, KindDateTime, KindDate, KindTime:
			var s string
			if err := json.Unmarshal(probe.Val, &s); err != nil {
				return err
			}
			v.Val = s
		case KindNumber:
			var n float64
			if err := json.Unmarshal(probe.Val, &n); err != nil {
				return err
			}
			v.Val = n
		case KindBool:
			var b bool
			if err := json.Unmarshal(probe.Val, &b); err != nil {
				return err
			}
			v.Val = b
		}
		return nil
	case 't', 'f': // 裸布尔 → Bool
		var b bool
		if err := json.Unmarshal(trimmed, &b); err != nil {
			return err
		}
		*v = Val{Kind: KindBool, Val: b}
		return nil
	case 'n': // null / NaN → NA
		if string(trimmed) == "null" {
			*v = Val{Kind: KindNA}
			return nil
		}
		return fmt.Errorf("unsupported Haystack value JSON: %s", truncateJSON(trimmed))
	default: // 裸数字 → Number
		var n float64
		if err := json.Unmarshal(trimmed, &n); err != nil {
			return fmt.Errorf("unsupported Haystack value JSON: %s", truncateJSON(trimmed))
		}
		*v = Val{Kind: KindNumber, Val: n}
		return nil
	}
}

// NewRefVal 构造 Ref 值；id 形如 "@p:demo:r:site01" 或 "p:demo:r:site01"，自动去掉前导 @。
func NewRefVal(id string) Val {
	return Val{Kind: KindRef, Val: RefID(id)}
}

// NewMarkerVal 构造 Marker 值。
func NewMarkerVal() Val {
	return Val{Kind: KindMarker}
}

// NewStrVal 构造 Str 值。
func NewStrVal(s string) Val {
	return Val{Kind: KindStr, Val: s}
}

// NewNumberVal 构造 Number 值。
func NewNumberVal(n float64) Val {
	return Val{Kind: KindNumber, Val: n}
}

// NewBoolVal 构造 Bool 值。
func NewBoolVal(b bool) Val {
	return Val{Kind: KindBool, Val: b}
}

// NewNAVal 构造 NA（不可用/释放）值。
func NewNAVal() Val {
	return Val{Kind: KindNA}
}

// RefID 返回 Haystack Ref 的 id 部分（去掉前导 @ 和空白）。
// 例："@p:demo:r:site01" → "p:demo:r:site01"。
func RefID(s string) string {
	return strings.TrimPrefix(strings.TrimSpace(s), "@")
}

// normalizeKind 将 _kind 字符串规范为首字母大写形式。
// "ref"→"Ref"、"marker"→"Marker"、"number"→"Number"、"str"→"Str"、
// "bool"→"Bool"、"na"→"NA"、"remove"→"Remove"。
func normalizeKind(k string) string {
	switch strings.ToLower(k) {
	case "ref":
		return string(KindRef)
	case "marker":
		return string(KindMarker)
	case "number":
		return string(KindNumber)
	case "str":
		return string(KindStr)
	case "bool":
		return string(KindBool)
	case "na":
		return string(KindNA)
	case "remove":
		return string(KindRemove)
	case "datetime":
		return string(KindDateTime)
	case "date":
		return string(KindDate)
	case "time":
		return string(KindTime)
	default:
		return k
	}
}

func truncateJSON(data []byte) string {
	s := string(data)
	if len(s) > 200 {
		return s[:200] + "..."
	}
	return s
}

// Col HGrid 的列定义。
type Col struct {
	Name string         `json:"name"`
	Meta map[string]any `json:"meta,omitempty"`
}

// HGrid 是 FIN 标准响应网格（meta/cols/rows 完整保留，供 LLM 读取）。
type HGrid struct {
	Meta map[string]any   `json:"meta"`
	Cols []Col            `json:"cols"`
	Rows []map[string]Val `json:"rows"`
}

// CommitMode 表示 FIN commit op 的三种模式。
type CommitMode string

const (
	CommitAdd    CommitMode = "add"
	CommitUpdate CommitMode = "update"
	CommitRemove CommitMode = "remove"
)

// ----------------------------------------------------------------------------
// 7 个 MCP 工具的参数类型（任务书 §2）
// ----------------------------------------------------------------------------

// TestConnectionParams 是 fin_test_connection 工具的参数（§2.1）。
type TestConnectionParams struct {
	URL      string   `json:"url"`
	AuthType AuthType `json:"auth_type,omitempty"`
	Username string   `json:"username"`
	Password string   `json:"password"`
}

// SyncPoint 是 fin_sync_point_tree 中单个语义点位的定义（§2.2）。
type SyncPoint struct {
	Name string         `json:"name"`
	Spec string         `json:"spec,omitempty"` // Xeto Spec，如 ph.points::AirTempSensor
	Tags map[string]any `json:"tags,omitempty"` // Marker 字典：true→Marker；字符串→Str；@开头→Ref；数字→Number
	Unit string         `json:"unit,omitempty"`
	Kind Kind           `json:"kind"` // Number / Bool / Str
}

// SyncPointTreeParams 是 fin_sync_point_tree 工具的参数（§2.2）。
type SyncPointTreeParams struct {
	SiteRef  string      `json:"site_ref"`
	EquipRef string      `json:"equip_ref,omitempty"`
	Points   []SyncPoint `json:"points"`
}

// QueryEntitiesParams 是 fin_query_entities 工具的参数（§2.3）。
type QueryEntitiesParams struct {
	Filter string `json:"filter"`
	Limit  int    `json:"limit,omitempty"`
}

// WriteOverrideParams 是 fin_write_override 工具的参数（§2.4）。
type WriteOverrideParams struct {
	PointID     string          `json:"point_id"`
	Val         json.RawMessage `json:"val"` // Number/Bool/Str 控制值，原始 JSON 留待编码
	Priority    int             `json:"priority,omitempty"`
	DurationSec int             `json:"duration_sec,omitempty"`
}

// UpdateEntityParams 是 fin_update_entity 工具的参数（§2.5）。
type UpdateEntityParams struct {
	ID         string         `json:"id"`
	Tags       map[string]any `json:"tags"`
	RemoveTags []string       `json:"remove_tags,omitempty"`
}

// DeleteEntityParams 是 fin_delete_entity 工具的参数（§2.6）。
type DeleteEntityParams struct {
	ID    string `json:"id"`
	Force bool   `json:"force,omitempty"`
}

// EvalAxonParams 是 fin_eval_axon 工具的参数（§2.7）。
type EvalAxonParams struct {
	Expr string `json:"expr"`
}

// ----------------------------------------------------------------------------
// 附加 SDK 类型
// ----------------------------------------------------------------------------

// PointError 记录 fin_sync_point_tree 中单点建点失败信息（§2.2）。
type PointError struct {
	Name  string `json:"name"`
	Error string `json:"error"`
}

// AboutInfo 是 FIN about op 解析后的服务器信息（§2.1）。
type AboutInfo struct {
	FinVersion string
	ServerName string
	Version    string
	Raw        map[string]Val
}
