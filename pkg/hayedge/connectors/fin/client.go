// Package fin 实现 datatell HayEdge 北向连接器底座中的 FIN Framework MCP 连接器 SDK。
//
// client.go 提供：
//   - Project Haystack 三阶段 SCRAM 鉴权（纯 Go、零 CGO、零外部依赖）
//   - HTTP Basic 鉴权
//   - Haystack REST API SDK：about / read / pointWrite / commit / eval
//   - 7 个 MCP 工具对应的业务方法
//
// 硬性约束（任务书 §0）：
//   - 纯 Go 原生，零 CGO（CGO_ENABLED=0 可构建）
//   - 请求超时默认 5s，可配置；任何 goroutine 不得阻塞底座主线程（熔断隔离）
//   - Ref 必须是 Ref 类型、Marker 必须是 Marker 类型（非字符串/布尔）
package fin

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ErrAuth 表示鉴权失败（凭证错误或 SCRAM 校验失败）。
type ErrAuth struct {
	Msg string
}

func (e *ErrAuth) Error() string { return "FIN auth error: " + e.Msg }

// ErrTimeout 表示请求超时（熔断隔离，任务书 §0.4）。
type ErrTimeout struct{}

func (e *ErrTimeout) Error() string { return "FIN request timeout" }

// Client 是 FIN Haystack REST API 客户端。
type Client struct {
	cfg     Config
	httpCli *http.Client

	tokenMu    sync.RWMutex
	authTok    string // SCRAM Bearer token；basic 模式为空
	cookieName string // FIN 5.3 Set-Cookie 解析出的 cookie 名（skyarc-auth-<port>）；非空时用 Cookie 鉴权
	cookieTok  string // Set-Cookie 中的 token（通常与 authTok 相同，缺省回退 authTok）
	authInit   bool   // SCRAM 是否已完成握手
	authMu     sync.Mutex // 串行化 SCRAM 握手，避免并发重复握手

	overMu    sync.Mutex
	overrides map[string]*time.Timer // pointID|priority → 释放定时器
}

// NewClient 创建 FIN 客户端。timeout <= 0 时使用默认 5s。
func NewClient(cfg Config) (*Client, error) {
	if cfg.URL == "" {
		return nil, errors.New("fin: url is required")
	}
	if cfg.Username == "" || cfg.Password == "" {
		return nil, errors.New("fin: username and password are required")
	}
	if cfg.AuthType == "" {
		cfg.AuthType = AuthTypeSCRAM
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = DefaultTimeout
	}
	cfg.URL = strings.TrimRight(cfg.URL, "/")
	return &Client{
		cfg:       cfg,
		httpCli:   &http.Client{Timeout: cfg.Timeout},
		overrides: make(map[string]*time.Timer),
	}, nil
}

// Config 返回客户端配置副本。
func (c *Client) Config() Config { return c.cfg }

// ----------------------------------------------------------------------------
// 鉴权
// ----------------------------------------------------------------------------

// ensureAuth 确保客户端已持有可用鉴权。SCRAM 模式首次调用时执行三阶段握手；
// basic 模式无需握手。
func (c *Client) ensureAuth(ctx context.Context) error {
	if c.cfg.AuthType == AuthTypeBasic {
		return nil
	}
	c.tokenMu.RLock()
	init := c.authInit
	c.tokenMu.RUnlock()
	if init {
		return nil
	}
	c.authMu.Lock()
	defer c.authMu.Unlock()
	// 双检：等待锁期间可能已有其他 goroutine 完成握手
	c.tokenMu.RLock()
	init = c.authInit
	c.tokenMu.RUnlock()
	if init {
		return nil
	}
	return c.authenticateSCRAM(ctx)
}

// authenticateSCRAM 执行 Project Haystack 三阶段 SCRAM 握手（任务书 §3.2）。
func (c *Client) authenticateSCRAM(ctx context.Context) error {
	token, cookieName, cookieTok, err := c.scramHandshake(ctx)
	if err != nil {
		return err
	}
	c.tokenMu.Lock()
	c.authTok = token
	c.cookieName = cookieName
	c.cookieTok = cookieTok
	c.authInit = true
	c.tokenMu.Unlock()
	return nil
}

// authHeader 返回请求的鉴权头名与头值。
//   - basic 模式：Authorization: Basic base64(user:pass)
//   - SCRAM 模式：Authorization: BEARER authToken=<token>（phable 风格）
//
// 真机 FIN 3.9 实测结论：SCRAM 认证后所有请求（GET/POST）统一用
// 'Authorization: BEARER authToken=<token>' 头。Cookie（skyarc-auth-<port>）仅对
// GET 有效、POST 会被 Jetty 拒 400，因此不再依赖 Cookie 头鉴权（握手仍解析
// Set-Cookie 留档于 cookieName/cookieTok，仅作诊断信息）。
func (c *Client) authHeader() (name, value string) {
	if c.cfg.AuthType == AuthTypeBasic {
		cred := base64.StdEncoding.EncodeToString([]byte(c.cfg.Username + ":" + c.cfg.Password))
		return "Authorization", "Basic " + cred
	}
	c.tokenMu.RLock()
	defer c.tokenMu.RUnlock()
	return "Authorization", "BEARER authToken=" + c.authTok
}

// ----------------------------------------------------------------------------
// 底层 HTTP
// ----------------------------------------------------------------------------

// rawBody 携带已按目标格式（如 Zinc 文本）预编码的请求体，do 直接发送。
// 区别于普通 map/struct body（do 会 json.Marshal 并设 Content-Type: application/json）。
type rawBody struct {
	data        []byte
	contentType string
}

// zincBody 将 Zinc 网格文本包装为 rawBody。
func zincBody(grid string) rawBody {
	return rawBody{data: []byte(grid), contentType: "text/zinc"}
}

// do 发送一次带鉴权头的 HTTP 请求并返回响应（不自动关闭 body）。
func (c *Client) do(ctx context.Context, method, path string, body any) (*http.Response, error) {
	var bodyReader io.Reader
	contentType := ""
	if body != nil {
		if rb, ok := body.(rawBody); ok {
			bodyReader = bytes.NewReader(rb.data)
			contentType = rb.contentType
		} else {
			data, err := json.Marshal(body)
			if err != nil {
				return nil, err
			}
			bodyReader = bytes.NewReader(data)
			contentType = "application/json"
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, c.cfg.URL+path, bodyReader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json, text/zinc")
	if body != nil {
		req.Header.Set("Content-Type", contentType)
	}
	hname, hval := c.authHeader()
	req.Header.Set(hname, hval)
	resp, err := c.httpCli.Do(req)
	if err != nil {
		if isTimeoutErr(err) {
			return nil, &ErrTimeout{}
		}
		return nil, err
	}
	return resp, nil
}

// request 发送请求并在 401 时重新握手重试一次；返回状态码、Content-Type 与响应体 bytes。
// 注意：FIN 写响应可能是 CallError 包装（任务书 §3.4 坑 1），调用方按 2xx 视为成功。
func (c *Client) request(ctx context.Context, method, path string, body any) (int, string, []byte, error) {
	// 每个已鉴权请求先确保已完成握手（basic 为 no-op，SCRAM 首次请求时握手）
	if err := c.ensureAuth(ctx); err != nil {
		return 0, "", nil, err
	}
	resp, err := c.do(ctx, method, path, body)
	if err != nil {
		return 0, "", nil, err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return resp.StatusCode, "", nil, err
	}
	ct := resp.Header.Get("Content-Type")

	// 401：重新握手后重试一次（仅 SCRAM）
	if resp.StatusCode == http.StatusUnauthorized && c.cfg.AuthType == AuthTypeSCRAM {
		c.tokenMu.Lock()
		c.authInit = false
		c.authTok = ""
		c.cookieName = ""
		c.cookieTok = ""
		c.tokenMu.Unlock()
		if err := c.authenticateSCRAM(ctx); err != nil {
			return resp.StatusCode, ct, data, err
		}
		resp2, err2 := c.do(ctx, method, path, body)
		if err2 != nil {
			return 0, "", nil, err2
		}
		defer resp2.Body.Close()
		data, err = io.ReadAll(resp2.Body)
		if err != nil {
			return resp2.StatusCode, "", nil, err
		}
		resp = resp2
		ct = resp.Header.Get("Content-Type")
	}
	return resp.StatusCode, ct, data, nil
}

// scramGet 发送 SCRAM 握手用的 GET /about 请求，返回响应头。
// 握手过程中服务器可能返回 401 挑战（带 WWW-Authenticate 头）或 200 成功
// （带 Authentication-Info 头）——必须读头而非只看状态码（§3.2）。
// 仅对 403（凭证被拒）直接报错，其余状态码一律把 header 交给解析函数处理。
func (c *Client) scramGet(ctx context.Context, authHeader string) (http.Header, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.cfg.URL+"/about", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", authHeader)
	resp, err := c.httpCli.Do(req)
	if err != nil {
		if isTimeoutErr(err) {
			return nil, &ErrTimeout{}
		}
		return nil, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if resp.StatusCode == http.StatusForbidden {
		return nil, &ErrAuth{Msg: "credentials rejected (HTTP 403)"}
	}
	return resp.Header, nil
}

// ----------------------------------------------------------------------------
// SCRAM 计算（RFC5802 + Project Haystack）
// ----------------------------------------------------------------------------

var (
	// 字符类同时兼容标准 base64 与 base64url（-_），避免 data/handshakeToken 值被截断。
	reHandshakeToken = regexp.MustCompile(`handshakeToken=[a-zA-Z0-9+/_=-]+`)
	reHash           = regexp.MustCompile(`hash=(SHA-256)`)
	reData           = regexp.MustCompile(`data=[a-zA-Z0-9+/_=-]+`)
	reAuthToken      = regexp.MustCompile(`authToken=[^,\s]+`)

	// b64URLToStd 将 URL-safe base64 的 -_ 翻译回标准 base64 的 +/（Python urlsafe_b64decode 语义）。
	b64URLToStd = strings.NewReplacer("-", "+", "_", "/")
)

func b64urlEncode(data []byte) string {
	return base64.RawURLEncoding.EncodeToString(data)
}

func b64urlDecode(s string) ([]byte, error) {
	// FIN 返回的 salt/data 常为无填充 URL-safe base64（含 -/_），个别实现是标准 base64（+/）。
	// 与 phable 的 urlsafe_b64decode 对齐：先 RawURLEncoding（无填充 URL-safe），
	// 再 URLEncoding（带填充 URL-safe），最后按 -_ → +/ 翻译 + 标准 base64 解码兜底。
	if b, err := base64.RawURLEncoding.DecodeString(s); err == nil {
		return b, nil
	}
	if b, err := base64.URLEncoding.DecodeString(s); err == nil {
		return b, nil
	}
	trimmed := strings.TrimRight(s, "=")
	if l := len(trimmed) % 4; l != 0 {
		trimmed += strings.Repeat("=", 4-l)
	}
	translated := b64URLToStd.Replace(trimmed)
	return base64.StdEncoding.DecodeString(translated)
}

func hmacSHA256(key, msg []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(msg)
	return mac.Sum(nil)
}

func xorBytes(a, b []byte) []byte {
	out := make([]byte, len(a))
	for i := range a {
		out[i] = a[i] ^ b[i]
	}
	return out
}

// pbkdf2SHA256 实现 PBKDF2-HMAC-SHA256（Go 标准库无 PBKDF2，手写避免额外依赖）。
func pbkdf2SHA256(password, salt []byte, iter, keyLen int) []byte {
	h := hmac.New(sha256.New, password)
	hLen := h.Size()
	numBlocks := (keyLen + hLen - 1) / hLen
	var dk []byte
	for block := 1; block <= numBlocks; block++ {
		h.Reset()
		h.Write(salt)
		h.Write([]byte{byte(block >> 24), byte(block >> 16), byte(block >> 8), byte(block)})
		u := h.Sum(nil)
		t := make([]byte, hLen)
		copy(t, u)
		for i := 1; i < iter; i++ {
			h.Reset()
			h.Write(u)
			u = h.Sum(nil)
			for j := 0; j < hLen; j++ {
				t[j] ^= u[j]
			}
		}
		dk = append(dk, t...)
	}
	return dk[:keyLen]
}

// scramHandshake 执行三阶段握手并返回 authToken、Set-Cookie 解析出的 cookie 名与 token。
// FIN 5.3 真机在认证成功的 client-final 响应里带 Set-Cookie（cookie 名动态为
// skyarc-auth-<port>），后续请求必须用 Cookie 头；旧版/SkySpark 无 Set-Cookie 时
// cookieName 为空，调用方继续用 Bearer。
func (c *Client) scramHandshake(ctx context.Context) (authToken, cookieName, cookieTok string, err error) {
	// 阶段 1：HELLO
	helloAuth := "HELLO username=" + b64urlEncode([]byte(c.cfg.Username))
	h1, err := c.scramGet(ctx, helloAuth)
	if err != nil {
		return "", "", "", err
	}
	handshakeToken, hash, err := parseHelloCall(h1)
	if err != nil {
		return "", "", "", fmt.Errorf("scram hello: %w", err)
	}
	if hash != "SHA-256" {
		return "", "", "", fmt.Errorf("scram: unsupported hash %q", hash)
	}

	// 阶段 2：client-first
	cNonceBytes := make([]byte, 12)
	if _, err := rand.Read(cNonceBytes); err != nil {
		return "", "", "", fmt.Errorf("scram: generate nonce: %w", err)
	}
	cNonce := hex.EncodeToString(cNonceBytes)
	c1Bare := "n=" + c.cfg.Username + ",r=" + cNonce
	gs2 := "n,,"
	c1Msg := gs2 + c1Bare

	firstAuth := fmt.Sprintf("SCRAM data=%s, handshakeToken=%s", b64urlEncode([]byte(c1Msg)), handshakeToken)
	h2, err := c.scramGet(ctx, firstAuth)
	if err != nil {
		return "", "", "", err
	}
	sNonce, salt, iter, err := parseFirstCall(h2)
	if err != nil {
		return "", "", "", fmt.Errorf("scram client-first: %w", err)
	}

	// 阶段 3：client-final
	s1Msg := "r=" + sNonce + ",s=" + salt + ",i=" + strconv.Itoa(iter)
	clientFinalNoProof := "c=" + b64urlEncode([]byte("n,,")) + ",r=" + sNonce
	authMessage := c1Bare + "," + s1Msg + "," + clientFinalNoProof

	saltBytes, err := b64urlDecode(salt)
	if err != nil {
		return "", "", "", fmt.Errorf("scram: invalid salt: %w", err)
	}
	saltedPassword := pbkdf2SHA256([]byte(c.cfg.Password), saltBytes, iter, 32)

	clientKey := hmacSHA256(saltedPassword, []byte("Client Key"))
	sh := sha256.Sum256(clientKey)
	storedKey := sh[:]
	clientSignature := hmacSHA256(storedKey, []byte(authMessage))
	clientProof := xorBytes(clientKey, clientSignature)
	clientFinal := clientFinalNoProof + ",p=" + b64urlEncode(clientProof)

	finalAuth := fmt.Sprintf("SCRAM data=%s, handshakeToken=%s", b64urlEncode([]byte(clientFinal)), handshakeToken)
	h3, err := c.scramGet(ctx, finalAuth)
	if err != nil {
		return "", "", "", err
	}
	authToken, serverSig, err := parseFinalCall(h3)
	if err != nil {
		return "", "", "", fmt.Errorf("scram client-final: %w", err)
	}
	// FIN 5.3 真机：认证成功后从 Set-Cookie 解析动态 cookie 名与 token。
	cookieName, cookieTok = parseSetCookie(h3)

	// 校验服务器签名：v= 值可能是标准 base64（+/）或 URL-safe base64（-_），
	// 统一解码为 bytes 后常数时间比较，避免字母表/填充差异导致误判。
	serverKey := hmacSHA256(saltedPassword, []byte("Server Key"))
	expectedSigBytes := hmacSHA256(serverKey, []byte(authMessage))
	serverSigBytes, err := b64urlDecode(serverSig)
	if err != nil {
		return "", "", "", fmt.Errorf("scram: invalid server signature: %w", err)
	}
	if !hmac.Equal(expectedSigBytes, serverSigBytes) {
		return "", "", "", &ErrAuth{Msg: "SCRAM server signature mismatch"}
	}
	return authToken, cookieName, cookieTok, nil
}

// authHeaderValues 返回认证参数所在 header 的拼接值。
// FIN 5.3 真机在认证成功（HTTP 200）时把 SCRAM 参数放在 Authentication-Info 头，
// 401 挑战时放在 WWW-Authenticate 头；两者兼容，优先取 Authentication-Info。
func authHeaderValues(h http.Header) string {
	if auth := strings.Join(h.Values("Authentication-Info"), ", "); auth != "" {
		return auth
	}
	return strings.Join(h.Values("WWW-Authenticate"), ", ")
}

// parseHelloCall 解析 HELLO 响应头：handshakeToken=xxx 与 hash=SHA-256。
func parseHelloCall(h http.Header) (token, hash string, err error) {
	auth := authHeaderValues(h)
	m := reHandshakeToken.FindString(auth)
	if m == "" {
		return "", "", errors.New("handshakeToken not found in Authentication-Info/WWW-Authenticate")
	}
	hh := reHash.FindString(auth)
	if hh == "" {
		return "", "", errors.New("hash not found in Authentication-Info/WWW-Authenticate")
	}
	return strings.TrimPrefix(m, "handshakeToken="), strings.TrimPrefix(hh, "hash="), nil
}

// parseFirstCall 解析 server-first：data=<b64url("r=...,s=...,i=...")>。
func parseFirstCall(h http.Header) (sNonce, salt string, iter int, err error) {
	auth := authHeaderValues(h)
	m := reData.FindString(auth)
	if m == "" {
		return "", "", 0, errors.New("data not found in Authentication-Info/WWW-Authenticate")
	}
	decoded, err := b64urlDecode(strings.TrimPrefix(m, "data="))
	if err != nil {
		return "", "", 0, err
	}
	parts := strings.Split(strings.ReplaceAll(string(decoded), " ", ""), ",")
	if len(parts) < 3 {
		return "", "", 0, errors.New("malformed server-first message")
	}
	sNonce = strings.TrimPrefix(parts[0], "r=")
	salt = strings.TrimPrefix(parts[1], "s=")
	iter, err = strconv.Atoi(strings.TrimPrefix(parts[2], "i="))
	if err != nil {
		return "", "", 0, err
	}
	return sNonce, salt, iter, nil
}

// parseFinalCall 解析 server-final：authToken=xxx 与 data=<b64url("v=<sig>")>。
// 真机 FIN 5.3 在认证成功（HTTP 200）时返回 Authentication-Info 头：
//
//	Authentication-Info: authToken=web-..., data=dj02...
//
// 旧版/兼容实现则在 401 挑战的 WWW-Authenticate 头返回同样内容。两者都解析，
// 优先 Authentication-Info。
func parseFinalCall(h http.Header) (authToken, serverSig string, err error) {
	auth := authHeaderValues(h)
	m := reAuthToken.FindString(auth)
	if m == "" {
		return "", "", errors.New("authToken not found in Authentication-Info/WWW-Authenticate")
	}
	d := reData.FindString(auth)
	if d == "" {
		return "", "", errors.New("data not found in Authentication-Info/WWW-Authenticate")
	}
	decoded, err := b64urlDecode(strings.TrimPrefix(d, "data="))
	if err != nil {
		return "", "", err
	}
	return strings.TrimPrefix(m, "authToken="), strings.TrimPrefix(string(decoded), "v="), nil
}

// parseSetCookie 解析握手响应中的 Set-Cookie 头，返回 cookie 名与值。
// 真机 FIN 5.3 格式：skyarc-auth-8800=web-XXX;Path=/;HttpOnly;SameSite=strict
// （cookie 名动态 = skyarc-auth-<port>）。无有效 Set-Cookie 时返回 ("", "")。
func parseSetCookie(h http.Header) (name, value string) {
	for _, sc := range h.Values("Set-Cookie") {
		// 取分号前的 name=value 部分（忽略 Path/HttpOnly 等属性）
		if i := strings.Index(sc, ";"); i >= 0 {
			sc = sc[:i]
		}
		n, v, ok := strings.Cut(sc, "=")
		if !ok {
			continue
		}
		n = strings.TrimSpace(n)
		v = strings.TrimSpace(v)
		if n != "" && v != "" {
			return n, v
		}
	}
	return "", ""
}

// ----------------------------------------------------------------------------
// Haystack REST API
// ----------------------------------------------------------------------------

// About 查询服务器信息（GET /about），返回首行记录。
func (c *Client) About(ctx context.Context) (*AboutInfo, error) {
	if err := c.ensureAuth(ctx); err != nil {
		return nil, err
	}
	status, ct, data, err := c.request(ctx, http.MethodGet, "/about", nil)
	if err != nil {
		return nil, err
	}
	if status >= 400 {
		return nil, fmt.Errorf("fin about failed: HTTP %d: %s", status, truncateJSON(data))
	}
	grid, err := parseGrid(data, ct)
	if err != nil {
		return nil, fmt.Errorf("fin about: parse grid: %w", err)
	}
	if err := gridErr(grid); err != nil {
		return nil, fmt.Errorf("fin about: %w", err)
	}
	if len(grid.Rows) == 0 {
		return nil, errors.New("fin about: empty rows")
	}
	row := grid.Rows[0]
	info := &AboutInfo{Raw: row}
	// 真机 Zinc 返回的列名可能为 haystackVersion/projName，做兼容回退。
	if v, ok := row["finVersion"]; ok {
		info.FinVersion = valString(v)
	} else if v, ok := row["haystackVersion"]; ok {
		info.FinVersion = valString(v)
	}
	if v, ok := row["serverName"]; ok {
		info.ServerName = valString(v)
	} else if v, ok := row["projName"]; ok {
		info.ServerName = valString(v)
	}
	if v, ok := row["version"]; ok {
		info.Version = valString(v)
	}
	return info, nil
}

// Read 按 Haystack Filter 查询实体（GET /read?filter=<escaped>&limit=N）。
// 真机 FIN 3.9.12 实测：POST /read {json} 返回 400，必须改 GET 查询参数（200 返回 JSON grid）。
func (c *Client) Read(ctx context.Context, filter string, limit int) (*HGrid, error) {
	if limit <= 0 {
		limit = 100
	}
	path := "/read?filter=" + url.QueryEscape(filter) + "&limit=" + strconv.Itoa(limit)
	return c.callGrid(ctx, http.MethodGet, path, nil, true)
}

// PointWrite 向 FIN 写入点位覆盖值（POST /pointWrite，body 为 Zinc grid）。
// 真机 FIN 3.9.12 实测：POST {json} 返回 400，必须发 Zinc grid（列 id,priority,val）。
// 注意：2xx 即视为成功（任务书 §3.4 坑 1），但 FIN 业务错误（如对只读点写入）也以
// HTTP 200 返回，响应 grid meta 带 err marker，需检测并返回可读错误。
func (c *Client) PointWrite(ctx context.Context, pointID string, priority int, val Val, durationSec int) error {
	if priority < 1 || priority > 16 {
		priority = 8
	}
	grid := zincGrid{
		meta: map[string]any{"ver": "3.0"},
		cols: []string{"id", "priority", "val"},
		rows: []map[string]Val{
			{
				"id":       NewRefVal(pointID),
				"priority": NewNumberVal(float64(priority)),
				"val":      val,
			},
		},
	}
	body := zincBody(encodeZincGrid(grid))
	status, ct, data, err := c.request(ctx, http.MethodPost, "/pointWrite", body)
	if err != nil {
		return err
	}
	if status >= 400 {
		return fmt.Errorf("fin pointWrite failed: HTTP %d: %s", status, truncateJSON(data))
	}
	// 2xx 即视为成功；但需检查响应是否携带业务错误（meta.err marker）。
	if len(bytes.TrimSpace(data)) == 0 {
		return nil
	}
	hg, perr := parseGrid(data, ct)
	if perr != nil {
		return nil // 2xx 响应体解析失败不视为错误
	}
	if err := gridErr(hg); err != nil {
		return fmt.Errorf("fin pointWrite: %w", err)
	}
	return nil
}

// Commit 执行 FIN commit op（add/update/remove），body 为 Zinc grid，2xx 视为成功。
// 但 FIN 业务错误也以 HTTP 200 返回（响应 grid meta 带 err marker），需检测。
func (c *Client) Commit(ctx context.Context, mode CommitMode, rows []map[string]Val) (*HGrid, error) {
	grid := buildCommitGrid(mode, rows)
	body := zincBody(encodeZincGrid(grid))
	status, ct, data, err := c.request(ctx, http.MethodPost, "/commit", body)
	if err != nil {
		return nil, err
	}
	if status >= 400 {
		return nil, fmt.Errorf("fin commit(%s) failed: HTTP %d: %s", mode, status, truncateJSON(data))
	}
	// 2xx 即成功；尽力解析响应，解析失败不视为错误
	hg, perr := parseGrid(data, ct)
	if perr != nil {
		return &HGrid{Meta: map[string]any{}, Cols: []Col{}, Rows: []map[string]Val{}}, nil
	}
	// FIN 业务错误以 HTTP 200 + meta.err marker 返回，需检测
	if err := gridErr(hg); err != nil {
		return nil, fmt.Errorf("fin commit(%s): %w", mode, err)
	}
	return hg, nil
}

// Eval 执行只读 Axon 表达式（POST /eval，body 为 Zinc grid，列 expr）。
// 真机 FIN 3.9.12 实测：POST {json} 返回 400，必须发 Zinc grid（同 phable 的
// self.call("eval", Grid.to_grid({"expr": expr}))）。
func (c *Client) Eval(ctx context.Context, expr string) (*HGrid, error) {
	if err := CheckEvalSafety(expr); err != nil {
		return nil, err
	}
	grid := zincGrid{
		meta: map[string]any{"ver": "3.0"},
		cols: []string{"expr"},
		rows: []map[string]Val{{"expr": NewStrVal(expr)}},
	}
	body := zincBody(encodeZincGrid(grid))
	return c.callGrid(ctx, http.MethodPost, "/eval", body, true)
}

// callGrid 发送请求并把响应解析为 HGrid。
// FIN 业务错误以 HTTP 200 + grid meta 带 err marker 返回（如 read 的 badFilter、
// eval 的表达式错误），解析后需检测并返回可读错误。
func (c *Client) callGrid(ctx context.Context, method, path string, body any, checkStatus bool) (*HGrid, error) {
	if err := c.ensureAuth(ctx); err != nil {
		return nil, err
	}
	status, ct, data, err := c.request(ctx, method, path, body)
	if err != nil {
		return nil, err
	}
	if checkStatus && status >= 400 {
		return nil, fmt.Errorf("fin %s failed: HTTP %d: %s", path, status, truncateJSON(data))
	}
	grid, err := parseGrid(data, ct)
	if err != nil {
		return nil, fmt.Errorf("fin %s: parse grid: %w", path, err)
	}
	if err := gridErr(grid); err != nil {
		return nil, fmt.Errorf("fin %s: %w", path, err)
	}
	return grid, nil
}

// TestConnection 测试与 FIN 站点的连通性与鉴权（供 fin_test_connection 工具）。
func (c *Client) TestConnection(ctx context.Context) (map[string]any, error) {
	info, err := c.About(ctx)
	if err != nil {
		return map[string]any{"success": false, "error": err.Error()}, err
	}
	result := map[string]any{"success": true}
	if info.FinVersion != "" {
		result["fin_version"] = info.FinVersion
	}
	if info.ServerName != "" {
		result["server_name"] = info.ServerName
	}
	if info.Version != "" {
		result["version"] = info.Version
	}
	return result, nil
}

// ----------------------------------------------------------------------------
// 7 个工具的业务方法
// ----------------------------------------------------------------------------

// SyncPoints 批量推送语义点位（fin_sync_point_tree，§2.2）。
// 返回成功建点数与单点失败列表；单点失败不中断。
func (c *Client) SyncPoints(ctx context.Context, siteRef, equipRef string, points []SyncPoint) (int, []PointError, error) {
	var rows []map[string]Val
	var errs []PointError
	for _, p := range points {
		rec, err := BuildPointRecord(siteRef, equipRef, p)
		if err != nil {
			errs = append(errs, PointError{Name: p.Name, Error: err.Error()})
			continue
		}
		rows = append(rows, rec)
	}
	if len(rows) > 0 {
		if _, err := c.Commit(ctx, CommitAdd, rows); err != nil {
			return 0, errs, err
		}
	}
	return len(rows), errs, nil
}

// UpdateEntity 更新 FIN 记录标签（fin_update_entity，§2.5）。
//
// FIN/SkySpark 的 commit update 是乐观锁：diff 行除 id 外还必须携带 mod 标签
// （记录版本戳，Ref 类型如 @p:xxx）。因此先 Read 该记录取 mod，再把 mod 一并
// 写入 commit update 的 diff 行；remove_tags 的 R 行同样带 mod。
func (c *Client) UpdateEntity(ctx context.Context, id string, tags map[string]any, removeTags []string) error {
	// 先读取记录拿到 mod（乐观锁版本戳），避免真机报 haystack::UnknownNameErr: mod
	row, err := c.readByID(ctx, id)
	if err != nil {
		return fmt.Errorf("update: read record: %w", err)
	}
	mod, err := getMod(row)
	if err != nil {
		return fmt.Errorf("update: %s: %w", id, err)
	}
	rec := map[string]Val{
		"id":  NewRefVal(id),
		"mod": mod,
	}
	for k, v := range tags {
		val, err := ConvertTagValue(v)
		if err != nil {
			return fmt.Errorf("tag %q: %w", k, err)
		}
		rec[k] = val
	}
	for _, name := range removeTags {
		rec[name] = Val{Kind: KindRemove}
	}
	if len(rec) == 2 { // 仅 id+mod，无实际标签变更
		return errors.New("no tags to update and no tags to remove")
	}
	_, err = c.Commit(ctx, CommitUpdate, []map[string]Val{rec})
	return err
}

// DeleteEntity 删除 FIN 记录（fin_delete_entity，§2.6）。
//
// FIN/SkySpark 的 commit remove 同样是乐观锁：diff 行必须携带 mod 标签。
// 因此先 Read 该记录取 mod 再提交 remove。force=true 时先查子记录
// （equipRef==@<id>），各自 Read 后一并 remove（每行都带各自 mod）。
func (c *Client) DeleteEntity(ctx context.Context, id string, force bool) error {
	// 先读取记录拿到 mod（乐观锁版本戳）
	row, err := c.readByID(ctx, id)
	if err != nil {
		return fmt.Errorf("delete: read record: %w", err)
	}
	mod, err := getMod(row)
	if err != nil {
		return fmt.Errorf("delete: %s: %w", id, err)
	}
	rows := []map[string]Val{
		{"id": NewRefVal(id), "mod": mod},
	}
	if force {
		children, err := c.Read(ctx, "equipRef==@"+RefID(id), -1)
		if err != nil {
			return fmt.Errorf("delete force: query children: %w", err)
		}
		for _, childRow := range children.Rows {
			cid, ok := childRow["id"]
			if !ok || cid.Kind != KindRef {
				continue
			}
			childMod, err := getMod(childRow)
			if err != nil {
				return fmt.Errorf("delete force: child %v: %w", cid.Val, err)
			}
			rows = append(rows, map[string]Val{"id": cid, "mod": childMod})
		}
	}
	_, err = c.Commit(ctx, CommitRemove, rows)
	return err
}

// readByID 按 Ref id 读取单条记录（GET /read?filter=id==@<id>&limit=1）。
// 返回该记录行；未找到时返回错误。
func (c *Client) readByID(ctx context.Context, id string) (map[string]Val, error) {
	grid, err := c.Read(ctx, "id==@"+RefID(id), 1)
	if err != nil {
		return nil, err
	}
	if len(grid.Rows) == 0 {
		return nil, fmt.Errorf("record not found: %s", id)
	}
	return grid.Rows[0], nil
}

// getMod 从 FIN 记录行中提取 mod 乐观锁版本戳。
//
// 真机 FIN 3.9 实测：mod 是 DateTime 类型（Zinc 如 2026-08-27T06:00:00Z），
// 旧版/SkySpark 也有 Ref 类型（@p:xxx）；为兼容两者（Str/Number 兜底），
// 原样返回该 Val，保证 commit diff 能按原格式编码回 Zinc。
func getMod(row map[string]Val) (Val, error) {
	mod, ok := row["mod"]
	if !ok {
		return Val{}, errors.New("record has no mod tag")
	}
	switch mod.Kind {
	case KindRef, KindDateTime, KindStr, KindNumber:
		return mod, nil
	default:
		return Val{}, fmt.Errorf("record mod tag is %v, want Ref/DateTime/Str/Number", mod.Kind)
	}
}

// WriteOverride 下发覆盖值并调度超时释放（fin_write_override，§2.4）。
// 返回实际生效的 priority。duration_sec>0 时启动 goroutine 定时释放（内存态，不持久化）。
func (c *Client) WriteOverride(ctx context.Context, params WriteOverrideParams) (int, error) {
	priority := params.Priority
	if priority < 1 || priority > 16 {
		priority = 8
	}
	val, err := EncodeWriteValue(params.Val)
	if err != nil {
		return 0, err
	}
	if err := c.PointWrite(ctx, params.PointID, priority, val, params.DurationSec); err != nil {
		return 0, err
	}
	// 同点同优先级再次写入先取消旧定时器（§2.4），再按需调度新释放
	c.cancelOverride(params.PointID, priority)
	if params.DurationSec > 0 {
		c.scheduleRelease(params.PointID, priority, time.Duration(params.DurationSec)*time.Second)
	}
	return priority, nil
}

// cancelOverride 取消并移除 pointID|priority 的旧覆盖定时器。
func (c *Client) cancelOverride(pointID string, priority int) {
	key := overrideKey(pointID, priority)
	c.overMu.Lock()
	if t, ok := c.overrides[key]; ok {
		t.Stop()
		delete(c.overrides, key)
	}
	c.overMu.Unlock()
}

// overrideKey 生成 pointID|priority 覆盖记录键。
func overrideKey(pointID string, priority int) string {
	return fmt.Sprintf("%s|%d", RefID(pointID), priority)
}

// scheduleRelease 调度超时自动释放（写回 NA 释放值）。同点同优先级重写先取消旧定时器。
func (c *Client) scheduleRelease(pointID string, priority int, d time.Duration) {
	key := overrideKey(pointID, priority)
	c.overMu.Lock()
	if t, ok := c.overrides[key]; ok {
		t.Stop()
	}
	t := time.AfterFunc(d, func() {
		ctx, cancel := context.WithTimeout(context.Background(), c.cfg.Timeout)
		defer cancel()
		_ = c.PointWrite(ctx, pointID, priority, NewNAVal(), 0)
		c.overMu.Lock()
		delete(c.overrides, key)
		c.overMu.Unlock()
	})
	c.overrides[key] = t
	c.overMu.Unlock()
}

// StopAllOverrides 取消全部未到期覆盖定时器（网关关闭时调用，安全优先）。
func (c *Client) StopAllOverrides() {
	c.overMu.Lock()
	defer c.overMu.Unlock()
	for k, t := range c.overrides {
		t.Stop()
		delete(c.overrides, k)
	}
}

// ----------------------------------------------------------------------------
// 纯函数辅助
// ----------------------------------------------------------------------------

// BuildPointRecord 将 SyncPoint 转换为 FIN 记录（§2.2）。
func BuildPointRecord(siteRef, equipRef string, p SyncPoint) (map[string]Val, error) {
	if p.Name == "" {
		return nil, errors.New("point name is required")
	}
	switch p.Kind {
	case KindNumber, KindBool, KindStr:
	default:
		return nil, fmt.Errorf("invalid kind %q", p.Kind)
	}
	rec := map[string]Val{
		"dis":     NewStrVal(p.Name),
		"point":   NewMarkerVal(),
		"kind":    NewStrVal(string(p.Kind)),
		"siteRef": NewRefVal(siteRef),
	}
	if equipRef != "" {
		rec["equipRef"] = NewRefVal(equipRef)
	}
	if p.Unit != "" {
		rec["unit"] = NewStrVal(p.Unit)
	}
	if p.Spec != "" {
		rec["spec"] = NewStrVal(p.Spec)
	}
	for k, v := range p.Tags {
		val, err := ConvertTagValue(v)
		if err != nil {
			return nil, fmt.Errorf("tag %q: %w", k, err)
		}
		rec[k] = val
	}
	return rec, nil
}

// ConvertTagValue 将 tags 字典中的 Go 值转换为 Haystack 值（§2.2 类型规则）。
// true→Marker；字符串→Str；@开头→Ref；数字→Number；false→Bool。
func ConvertTagValue(v any) (Val, error) {
	switch t := v.(type) {
	case bool:
		if t {
			return NewMarkerVal(), nil
		}
		return NewBoolVal(false), nil
	case string:
		if t == "marker" {
			return NewMarkerVal(), nil
		}
		if strings.HasPrefix(t, "@") {
			return NewRefVal(t), nil
		}
		return NewStrVal(t), nil
	case float64:
		return NewNumberVal(t), nil
	case json.Number:
		n, err := t.Float64()
		if err != nil {
			return Val{}, err
		}
		return NewNumberVal(n), nil
	case int:
		return NewNumberVal(float64(t)), nil
	case int64:
		return NewNumberVal(float64(t)), nil
	default:
		return Val{}, fmt.Errorf("unsupported tag value type %T", v)
	}
}

// EncodeWriteValue 将 fin_write_override 的原始 JSON val 编码为 Haystack 值。
func EncodeWriteValue(raw json.RawMessage) (Val, error) {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return Val{}, err
	}
	switch t := v.(type) {
	case float64:
		return NewNumberVal(t), nil
	case bool:
		return NewBoolVal(t), nil
	case string:
		return NewStrVal(t), nil
	default:
		return Val{}, fmt.Errorf("unsupported write value type %T", v)
	}
}

// CheckEvalSafety 检查 Axon 表达式是否含写操作关键词（§2.7），含则拒绝。
var evalForbiddenWords = []string{
	"commit", "commitadd", "commitupdate", "commitremove",
	"iowritetrio", "iowritezinc", "purge", "purgeall",
	"install", "uninstall", "delete", "remove",
}

// CheckEvalSafety 检查 Axon 表达式是否含写操作关键词（§2.7），含则拒绝。
func CheckEvalSafety(expr string) error {
	lower := strings.ToLower(expr)
	for _, w := range evalForbiddenWords {
		if strings.Contains(lower, w) {
			return fmt.Errorf("axon expression contains forbidden write keyword %q", w)
		}
	}
	return nil
}

// buildCommitGrid 构造 FIN commit 请求体（Zinc grid，meta 携带 commit 模式）。
// 参考 phable 的 _get_commit_grid：meta = {ver:"3.0", commit:<op>}，行即记录。
// 列名为所有行的 key 的并集（排序保证确定性；真机按列名解析，顺序无关）。
func buildCommitGrid(mode CommitMode, rows []map[string]Val) zincGrid {
	var colNames []string
	seen := make(map[string]bool)
	for _, row := range rows {
		for k := range row {
			if !seen[k] {
				seen[k] = true
				colNames = append(colNames, k)
			}
		}
	}
	sort.Strings(colNames)
	return zincGrid{
		meta: map[string]any{"ver": "3.0", "commit": string(mode)},
		cols: colNames,
		rows: rows,
	}
}

// ----------------------------------------------------------------------------
// Zinc 文本编码（请求体：真机 FIN 3.9.12 的 eval/pointWrite/commit 只收 Zinc grid）
// 参考 phable zinc_encoder.py：meta 行 → 列名行 → 数据行 → 结尾空行。
// ----------------------------------------------------------------------------

// zincGrid 描述一个待编码为 Zinc 文本的网格（meta/cols/rows）。
type zincGrid struct {
	meta map[string]any
	cols []string
	rows []map[string]Val
}

// encodeZincGrid 将网格编码为 Zinc 文本。
//
//	ver:"3.0"
//	id,priority,val
//	@p:xxx,8.0,42.0
//	<空行>
func encodeZincGrid(g zincGrid) string {
	var b strings.Builder
	writeZincMeta(&b, g.meta)
	b.WriteByte('\n')
	if len(g.cols) == 0 {
		b.WriteString("noCols\n")
	} else {
		for i, col := range g.cols {
			if i > 0 {
				b.WriteByte(',')
			}
			b.WriteString(col)
		}
		b.WriteByte('\n')
	}
	for _, row := range g.rows {
		for i, col := range g.cols {
			if i > 0 {
				b.WriteByte(',')
			}
			if v, ok := row[col]; ok {
				b.WriteString(encodeZincVal(v))
			}
			// 缺列：留空单元格（等价 phable 的 None + 多列时 skip）
		}
		b.WriteByte('\n')
	}
	b.WriteByte('\n')
	return b.String()
}

// writeZincMeta 写 Zinc 网格 meta 行。Marker 直接写列名（无 :value），
// 字符串写 "..."，数字/布尔写字面量。meta 键排序保证确定性（Go map 迭代序随机）；
// "ver" 恒排首位（对齐 phable Grid.to_grid 始终以 ver 开头）。
func writeZincMeta(b *strings.Builder, meta map[string]any) {
	keys := make([]string, 0, len(meta))
	for k := range meta {
		if k != "ver" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	if _, ok := meta["ver"]; ok {
		keys = append([]string{"ver"}, keys...)
	}
	first := true
	for _, k := range keys {
		if !first {
			b.WriteByte(' ')
		}
		first = false
		b.WriteString(k)
		v := meta[k]
		if mv, ok := v.(Val); ok && mv.Kind == KindMarker {
			continue // Marker 直接列名
		}
		if v == nil {
			continue
		}
		b.WriteByte(':')
		b.WriteString(encodeZincMetaVal(v))
	}
}

// encodeZincMetaVal 编码 meta 值（非 Marker）。
func encodeZincMetaVal(v any) string {
	switch t := v.(type) {
	case Val:
		return encodeZincVal(t)
	case string:
		return escapeZincStr(t)
	case bool:
		if t {
			return "T"
		}
		return "F"
	case float64:
		return formatZincNumber(t)
	case int:
		return strconv.Itoa(t)
	case json.Number:
		return t.String()
	default:
		return escapeZincStr(fmt.Sprintf("%v", t))
	}
}

// encodeZincVal 将 Haystack 值编码为 Zinc 单元格文本。
//   - Ref：@p:xxx（可选 "dis" display）
//   - Marker：M；NA：NA；Remove：R；Bool：T/F
//   - Number：42.0 / 42.5 / 21.5kW / NaN / INF / -INF（整数值带 .0，对齐 Python f"{42.0}"）
//   - Str："..."（转义）
func encodeZincVal(v Val) string {
	switch v.Kind {
	case KindRef:
		s := "@" + RefID(fmt.Sprintf("%v", v.Val))
		if v.Dis != "" {
			s += ` "` + escapeZincStr(v.Dis) + `"`
		}
		return s
	case KindDateTime:
		// Zinc DateTime 字面量（如 2026-08-27T06:00:00Z）原样输出，不加引号。
		if s, ok := v.Val.(string); ok {
			return s
		}
		return fmt.Sprintf("%v", v.Val)
	case KindDate:
		if s, ok := v.Val.(string); ok {
			return s
		}
		return fmt.Sprintf("%v", v.Val)
	case KindTime:
		if s, ok := v.Val.(string); ok {
			return s
		}
		return fmt.Sprintf("%v", v.Val)
	case KindMarker:
		return "M"
	case KindNumber:
		n, _ := v.Val.(float64)
		if math.IsNaN(n) {
			return "NaN"
		}
		if math.IsInf(n, 1) {
			return "INF"
		}
		if math.IsInf(n, -1) {
			return "-INF"
		}
		s := formatZincNumber(n)
		if v.Unit != "" {
			s += v.Unit
		}
		return s
	case KindStr:
		return escapeZincStr(fmt.Sprintf("%v", v.Val))
	case KindBool:
		if b, ok := v.Val.(bool); ok && b {
			return "T"
		}
		return "F"
	case KindNA:
		return "NA"
	case KindRemove:
		return "R"
	default:
		return "NA"
	}
}

// formatZincNumber 将 float64 编码为 Zinc Number 字面量。
// 整数值附加 ".0"（Python f"{42.0}" → "42.0"，而 Go FormatFloat 42 → "42"）。
func formatZincNumber(n float64) string {
	s := strconv.FormatFloat(n, 'f', -1, 64)
	if !strings.ContainsAny(s, ".eE") {
		s += ".0"
	}
	return s
}

// escapeZincStr 将字符串编码为 Zinc 双引号字符串字面量。
// 转义集与 phable zinc_encoder._parse_grid_str_to_zinc_str 一致：
// \n \r \f \t \\ \" \` \' ；非 ASCII 输出 \u<hex>。
func escapeZincStr(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\f':
			b.WriteString(`\f`)
		case '\t':
			b.WriteString(`\t`)
		case '\\':
			b.WriteString(`\\`)
		case '"':
			b.WriteString(`\"`)
		case '`':
			b.WriteString("\\`")
		case '\'':
			b.WriteString(`\'`)
		default:
			if r > 127 {
				b.WriteString(`\u` + strconv.FormatInt(int64(r), 16))
			} else {
				b.WriteByte(byte(r))
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// parseGrid 解析 FIN HGrid 响应，兼容 JSON 与 Zinc 文本两种格式。
//   - JSON：{"meta":..., "cols":..., "rows":...}（兼容带/不带顶层 _kind）
//   - Zinc：首行 meta（如 ver:"3.0"），第二行列名，后续行为数据（真机 FIN 5.3
//     about/read 等返回 Zinc 文本而非 JSON）
//
// 判定：Content-Type 含 "zinc"，或 body 去掉首尾空白后首字符不是 '{'（JSON 对象）。
func parseGrid(data []byte, contentType string) (*HGrid, error) {
	trimmed := bytes.TrimSpace(data)
	trimmed = bytes.TrimPrefix(trimmed, []byte("\xef\xbb\xbf")) // 去掉 UTF-8 BOM
	if len(trimmed) == 0 {
		return &HGrid{Meta: map[string]any{}, Cols: []Col{}, Rows: []map[string]Val{}}, nil
	}
	if looksLikeZinc(trimmed, contentType) {
		return parseGridZinc(trimmed)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &raw); err != nil {
		return nil, err
	}
	hg := &HGrid{
		Meta: map[string]any{},
		Cols: []Col{},
		Rows: []map[string]Val{},
	}
	if m, ok := raw["meta"]; ok {
		if err := json.Unmarshal(m, &hg.Meta); err != nil {
			return nil, err
		}
	}
	if c, ok := raw["cols"]; ok {
		if err := json.Unmarshal(c, &hg.Cols); err != nil {
			return nil, err
		}
	}
	if r, ok := raw["rows"]; ok {
		if err := json.Unmarshal(r, &hg.Rows); err != nil {
			return nil, err
		}
	}
	return hg, nil
}

// looksLikeZinc 判断响应是否为 Zinc 文本而非 JSON。
func looksLikeZinc(data []byte, contentType string) bool {
	if strings.Contains(strings.ToLower(contentType), "zinc") {
		return true
	}
	return len(data) > 0 && data[0] != '{'
}

// ----------------------------------------------------------------------------
// Zinc 文本解析（真机 FIN 5.3 about/read 等返回 Zinc 而非 JSON）
// ----------------------------------------------------------------------------

// parseGridZinc 解析 Haystack Zinc 文本网格：
//
//	ver:"3.0"
//	haystackVersion,projName,...
//	"5.3","demo",...
//
// 首行为 meta（ver 等），第二行为列名，后续行为记录。
func parseGridZinc(data []byte) (*HGrid, error) {
	text := strings.TrimSpace(string(data))
	if text == "" {
		return &HGrid{Meta: map[string]any{}, Cols: []Col{}, Rows: []map[string]Val{}}, nil
	}
	lines := strings.Split(text, "\n")

	// 第一行：grid meta（如 ver:"3.0"）
	meta := parseZincMeta(strings.TrimSpace(lines[0]))

	// 仅 meta 行（无列名/数据行）：返回空网格（FIN 错误网格常见形态，
	// 如 ver:"3.0" err errType:"writeAccess" dis:"..."）
	if len(lines) < 2 {
		return &HGrid{Meta: meta, Cols: []Col{}, Rows: []map[string]Val{}}, nil
	}

	// 第二行：列名
	colTokens := splitZincRow(strings.TrimSpace(lines[1]))
	cols := make([]Col, 0, len(colTokens))
	for _, tok := range colTokens {
		name := strings.TrimSpace(tok)
		if i := strings.IndexAny(name, " \t"); i >= 0 {
			name = name[:i] // 列名后可能的 meta/描述，取第一段
		}
		if name != "" {
			cols = append(cols, Col{Name: name})
		}
	}

	// 后续行：记录
	rows := make([]map[string]Val, 0)
	for _, line := range lines[2:] {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		vals := splitZincRow(line)
		row := make(map[string]Val, len(cols))
		for i, col := range cols {
			if i < len(vals) {
				row[col.Name] = parseZincValue(strings.TrimSpace(vals[i]))
			}
		}
		rows = append(rows, row)
	}

	return &HGrid{Meta: meta, Cols: cols, Rows: rows}, nil
}

// parseZincMeta 解析 Zinc 首行 meta，如 `ver:"3.0"`。
// 按空白切分（引号内空白不切），每个 token 形如 name:value。
// 无冒号的裸 token（如错误网格的 `err` marker）记为 Marker（"M"），
// 供 gridErr 检测 FIN 业务错误（meta.err）。
func parseZincMeta(line string) map[string]any {
	meta := make(map[string]any)
	for _, tok := range splitZincTokens(line) {
		if tok == "" {
			continue
		}
		key, raw, ok := strings.Cut(tok, ":")
		if !ok || strings.TrimSpace(key) == "" {
			// 无冒号：Marker（如 err）
			name := strings.TrimSpace(tok)
			if name != "" {
				meta[name] = "M"
			}
			continue
		}
		val := parseZincValue(strings.TrimSpace(raw))
		meta[strings.TrimSpace(key)] = zincValToAny(val)
	}
	return meta
}

// zincValToAny 将 Zinc 解析出的 Val 转为普通 Go 值（用于 meta 字典）。
func zincValToAny(v Val) any {
	switch v.Kind {
	case KindMarker:
		return "M"
	case KindNA:
		return nil
	default:
		return v.Val
	}
}

// parseZincValue 解析 Zinc 单元格值：Number / Str / Ref / Marker / NA / Bool / Remove。
func parseZincValue(s string) Val {
	if s == "" {
		return NewNAVal()
	}
	switch s[0] {
	case 'M':
		if s == "M" {
			return NewMarkerVal()
		}
	case 'N':
		if s == "NA" {
			return NewNAVal()
		}
	case 'R':
		if s == "R" {
			return Val{Kind: KindRemove}
		}
	case 'T':
		if s == "T" {
			return NewBoolVal(true)
		}
	case 'F':
		if s == "F" {
			return NewBoolVal(false)
		}
	case '"':
		return parseZincStr(s)
	case '@':
		return parseZincRef(s)
	}
	// DateTime/Date/Time 必须以数字开头，须在 Number 解析之前判定
	// （否则 2026-08-27T06:00:00Z 会被 Number 前缀 2026 误吞）。
	if isZincDateTime(s) {
		return Val{Kind: KindDateTime, Val: s}
	}
	if isZincDate(s) {
		return Val{Kind: KindDate, Val: s}
	}
	if isZincTime(s) {
		return Val{Kind: KindTime, Val: s}
	}
	if isZincNumber(s) {
		return parseZincNumber(s)
	}
	// 非数字字面量（如 meta 里的 commit:add）按 Str 处理
	return NewStrVal(s)
}

// isZincNumber 判断字符串是否为 Zinc Number 字面量（含 NaN/INF/科学计数法）。
func isZincNumber(s string) bool {
	switch strings.ToUpper(s) {
	case "NAN", "INF", "-INF":
		return true
	}
	return reZincNumber.FindString(s) != ""
}

// parseZincStr 解析 Zinc 字符串字面量（双引号包裹，支持 \n \t \" \\ \$ 转义）。
func parseZincStr(s string) Val {
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		inner := s[1 : len(s)-1]
		var b strings.Builder
		for i := 0; i < len(inner); i++ {
			ch := inner[i]
			if ch == '\\' && i+1 < len(inner) {
				i++
				switch inner[i] {
				case 'n':
					b.WriteByte('\n')
				case 't':
					b.WriteByte('\t')
				case 'r':
					b.WriteByte('\r')
				case '"':
					b.WriteByte('"')
				case '\\':
					b.WriteByte('\\')
				case '$':
					b.WriteByte('$')
				default:
					b.WriteByte(inner[i])
				}
				continue
			}
			b.WriteByte(ch)
		}
		return NewStrVal(b.String())
	}
	return NewStrVal(s)
}

// parseZincRef 解析 Zinc Ref 字面量：@id 或 @id "display"。
func parseZincRef(s string) Val {
	rest := s[1:]
	// 可选 display：@id "display"
	if i := strings.IndexAny(rest, " \t"); i >= 0 {
		id := rest[:i]
		disPart := strings.TrimSpace(rest[i+1:])
		dis := ""
		if v := parseZincStr(disPart); v.Kind == KindStr {
			if str, ok := v.Val.(string); ok {
				dis = str
			}
		}
		return Val{Kind: KindRef, Val: id, Dis: dis}
	}
	return Val{Kind: KindRef, Val: rest}
}

// reZincNumber 匹配 Zinc Number 的数值前缀（含可选符号、小数与科学计数法）。
var reZincNumber = regexp.MustCompile(`^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?`)

// reZincDateTime 匹配 Haystack Zinc DateTime 字面量：
//
//	2026-08-27T06:00:00Z
//	2026-08-27T06:00:00+08:00
//	2026-08-27T06:00:00.123-05:00
//	2026-08-27T06:00:00 New_York
var reZincDateTime = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}|\s+[A-Za-z_]+(\s[A-Za-z_]+)*)?$`)

// reZincDate 匹配 Haystack Zinc Date 字面量（2026-08-27）。
var reZincDate = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// reZincTime 匹配 Haystack Zinc Time 字面量（06:00:00 或 06:00:00.123）。
var reZincTime = regexp.MustCompile(`^\d{2}:\d{2}(:\d{2}(\.\d+)?)?$`)

// isZincDateTime 判断字符串是否为 Zinc DateTime 字面量。
func isZincDateTime(s string) bool { return reZincDateTime.MatchString(s) }

// isZincDate 判断字符串是否为 Zinc Date 字面量。
func isZincDate(s string) bool { return reZincDate.MatchString(s) }

// isZincTime 判断字符串是否为 Zinc Time 字面量。
func isZincTime(s string) bool { return reZincTime.MatchString(s) }

// parseZincNumber 解析 Zinc Number：数值前缀 + 可选单位后缀（如 21.5kW、-5.5°F）。
func parseZincNumber(s string) Val {
	upper := strings.ToUpper(s)
	switch upper {
	case "NAN":
		return Val{Kind: KindNumber, Val: math.NaN()}
	case "INF":
		return Val{Kind: KindNumber, Val: math.Inf(1)}
	case "-INF":
		return Val{Kind: KindNumber, Val: math.Inf(-1)}
	}
	numPart := reZincNumber.FindString(s)
	if numPart == "" {
		return Val{Kind: KindNumber, Val: 0}
	}
	n, err := strconv.ParseFloat(numPart, 64)
	if err != nil {
		return Val{Kind: KindNumber, Val: 0}
	}
	unit := strings.TrimSpace(s[len(numPart):])
	return Val{Kind: KindNumber, Val: n, Unit: unit}
}

// splitZincRow 按逗号切分 Zinc 行，引号内的逗号不切分。
func splitZincRow(line string) []string {
	var parts []string
	start := 0
	inStr, escaped := false, false
	for i := 0; i < len(line); i++ {
		ch := line[i]
		if inStr {
			if escaped {
				escaped = false
			} else if ch == '\\' {
				escaped = true
			} else if ch == '"' {
				inStr = false
			}
			continue
		}
		switch ch {
		case '"':
			inStr = true
		case ',':
			parts = append(parts, line[start:i])
			start = i + 1
		}
	}
	parts = append(parts, line[start:])
	return parts
}

// splitZincTokens 按空白切分 Zinc 行（用于 meta），引号内的空白不切分。
func splitZincTokens(line string) []string {
	var parts []string
	start := -1
	inStr, escaped := false, false
	for i := 0; i < len(line); i++ {
		ch := line[i]
		if inStr {
			if escaped {
				escaped = false
			} else if ch == '\\' {
				escaped = true
			} else if ch == '"' {
				inStr = false
			}
			continue
		}
		switch {
		case ch == '"':
			inStr = true
			if start < 0 {
				start = i
			}
		case ch == ' ' || ch == '\t':
			if start >= 0 {
				parts = append(parts, line[start:i])
				start = -1
			}
		default:
			if start < 0 {
				start = i
			}
		}
	}
	if start >= 0 {
		parts = append(parts, line[start:])
	}
	return parts
}

func valString(v Val) string {
	switch t := v.Val.(type) {
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	default:
		return fmt.Sprintf("%v", v.Val)
	}
}

// metaString 从网格 meta 值中提取可读字符串。
// 兼容裸字符串/数字/布尔，以及 Haystack JSON 编码 {_kind:"Str", val:"..."}。
func metaString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	case map[string]any:
		if s, ok := t["val"].(string); ok {
			return s
		}
		if d, ok := t["dis"].(string); ok {
			return d
		}
		if k, ok := t["_kind"].(string); ok {
			return k
		}
		return ""
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", v)
	}
}

// gridErr 检查 FIN 响应网格是否携带业务错误（meta.err marker）。
//
// 真机 FIN 3.9 业务错误返回 HTTP 200 + grid meta 带 err marker，形如：
//
//	Zinc: ver:"3.0" err errType:"writeAccess" errTrace:"..." dis:"..."
//	JSON: {"meta":{"err":{},"errType":"...","errTrace":"...","dis":"..."},...}
//
// 检测到 meta.err 时返回含 dis/errType/errTrace 的可读错误；否则返回 nil。
func gridErr(grid *HGrid) error {
	if grid == nil {
		return nil
	}
	v, ok := grid.Meta["err"]
	if !ok {
		return nil
	}
	// err 值可能是 Marker("M")、JSON 对象、true 等；空串/零值视为无错误。
	switch t := v.(type) {
	case nil:
		return nil
	case string:
		if t == "" {
			return nil
		}
	case bool:
		if !t {
			return nil
		}
	}
	msg := metaString(grid.Meta["dis"])
	if msg == "" {
		msg = "op returned error grid"
	}
	errType := metaString(grid.Meta["errType"])
	errTrace := metaString(grid.Meta["errTrace"])
	var parts []string
	parts = append(parts, msg)
	if errType != "" {
		parts = append(parts, "errType="+errType)
	}
	if errTrace != "" {
		parts = append(parts, "errTrace="+errTrace)
	}
	return errors.New(strings.Join(parts, "; "))
}

func isTimeoutErr(err error) bool {
	var ne interface{ Timeout() bool }
	if errors.As(err, &ne) {
		return ne.Timeout()
	}
	return errors.Is(err, context.DeadlineExceeded)
}
