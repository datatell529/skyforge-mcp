// fin-mcp 是 datatell HayEdge FIN Framework MCP 连接器的最小独立 MCP Server。
//
// 支持两种模式：
//   - stdio（默认）：MCP stdio 传输，供 Claude Desktop / 编程工具内嵌使用
//   - http：Streamable HTTP 传输，默认监听 127.0.0.1:8801/mcp
//
// 7 个工具均可通过 tools/list 列出（任务书 §1 交付结构 cmd/fin-mcp/main.go）。
//
// 用法：
//
//	fin-mcp -mode stdio \
//	  -url http://192.168.1.100:8800/api/demo -username su -password secret
package main

import (
	"flag"
	"log"
	"net/http"

	"github.com/mark3labs/mcp-go/server"

	finconn "github.com/datatell529/skyforge-mcp/pkg/hayedge/connectors/fin"
	finmcp "github.com/datatell529/skyforge-mcp/pkg/hayedge/mcp/fin"
)

func main() {
	var (
		mode     = flag.String("mode", "stdio", "server mode: stdio or http")
		addr     = flag.String("addr", "127.0.0.1:8801", "HTTP listen address (http mode)")
		url      = flag.String("url", "", "FIN Haystack 端点 URL，如 http://192.168.1.100:8800/api/demo")
		username = flag.String("username", "", "FIN 用户名")
		password = flag.String("password", "", "FIN 密码")
		authType = flag.String("auth_type", "scram", "FIN 鉴权方式: scram 或 basic")
	)
	flag.Parse()

	var client *finconn.Client
	if *url != "" && *username != "" && *password != "" {
		c, err := finconn.NewClient(finconn.Config{
			URL:      *url,
			AuthType: finconn.AuthType(*authType),
			Username: *username,
			Password: *password,
		})
		if err != nil {
			log.Fatalf("create FIN client: %v", err)
		}
		client = c
	} else {
		log.Println("warning: 未提供 -url/-username/-password，仅 fin_test_connection 可用（其他工具返回未配置错误）")
	}

	s := server.NewMCPServer("fin-mcp", "1.0.0", server.WithToolCapabilities(true))
	handler := finmcp.NewHandler(client)
	handler.Register(s)

	switch *mode {
	case "stdio":
		log.Println("fin-mcp: stdio mode")
		if err := server.ServeStdio(s); err != nil {
			log.Fatalf("stdio serve: %v", err)
		}
	case "http":
		httpServer := server.NewStreamableHTTPServer(s)
		mux := http.NewServeMux()
		mux.Handle("/mcp", httpServer)
		log.Printf("fin-mcp: http mode listening on http://%s/mcp", *addr)
		if err := http.ListenAndServe(*addr, mux); err != nil {
			log.Fatalf("http serve: %v", err)
		}
	default:
		log.Fatalf("unknown mode %q (want stdio or http)", *mode)
	}
}
