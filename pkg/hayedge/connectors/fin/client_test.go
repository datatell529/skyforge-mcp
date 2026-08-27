package fin

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// ----------------------------------------------------------------------------
// Mock FIN Server（SCRAM 三阶段握手 + 业务端点）
// ----------------------------------------------------------------------------

const (
	testHandshakeToken = "test-handshake"
	testAuthToken      = "test-auth-token"
	// testAuthTokenFIN53 模拟真机 FIN 5.3 在 HTTP 200 + Authentication-Info 头
	// 中返回的 authToken（base64url 风格，含连字符）。
	testAuthTokenFIN53 = "web-XMbHipf8CjOx-tFB-sA9q3QsVcBT3cB-ptez3BLhTxw-d"
	testUsername       = "su"
	testPassword       = "secret"
	// testModRef 模拟旧版/SkySpark 记录的 mod 乐观锁版本戳（Ref 类型，commit update/remove 必须携带）。
	testModRef = "p:2026-08-27T12:00:00.000Z"
	// testModDateTime 模拟真机 FIN 3.9 的 mod 乐观锁版本戳（DateTime 类型，
	// Zinc 如 2026-08-27T06:00:00Z，commit update/remove 必须原样带回去）。
	testModDateTime = "2026-08-27T06:00:00Z"
)

// mustB64URLDecode 测试辅助：base64url 解码。
func mustB64URLDecode(t *testing.T, s string) []byte {
	t.Helper()
	b, err := b64urlDecode(s)
	if err != nil {
		t.Fatalf("b64urlDecode(%q): %v", s, err)
	}
	return b
}

// parseSCRAMHeader 解析 "SCRAM data=<...>, handshakeToken=<...>"。
func parseSCRAMHeader(auth string) (dataB64, token string, ok bool) {
	if !strings.HasPrefix(auth, "SCRAM ") {
		return "", "", false
	}
	for _, p := range strings.Split(strings.TrimPrefix(auth, "SCRAM "), ",") {
		p = strings.TrimSpace(p)
		switch {
		case strings.HasPrefix(p, "data="):
			dataB64 = strings.TrimPrefix(p, "data=")
		case strings.HasPrefix(p, "handshakeToken="):
			token = strings.TrimPrefix(p, "handshakeToken=")
		}
	}
	return dataB64, token, dataB64 != ""
}

// scramServerState 记录 Mock SCRAM 握手所需的跨阶段状态。
type scramServerState struct {
	mu           sync.Mutex
	storedUser   string
	storedCNonce string
	sNonce       string
	salt         string
	iter         int
}

// newSCRAMServer 构造支持三阶段 SCRAM 握手的 Mock FIN 服务器。
// wrongSig=true 返回错误服务器签名；authed 可选，处理 Bearer 鉴权后的 about。
func newSCRAMServer(t *testing.T, wrongSig bool, authed http.HandlerFunc) *httptest.Server {
	return newSCRAMServerWithSalt(t, wrongSig, authed, "")
}

// computeServerFinalData 计算 SCRAM server-final 的 data=<b64url("v=<sig>")> 值。
// 与 newSCRAMServerWithSalt 内联逻辑一致，供 Cookie 鉴权 Mock 复用。
func computeServerFinalData(t *testing.T, st *scramServerState, clientFinalMsg string, wrongSig bool) string {
	t.Helper()
	cPart, rPart := "", ""
	for _, pp := range strings.Split(clientFinalMsg, ",") {
		switch {
		case strings.HasPrefix(pp, "c="):
			cPart = pp
		case strings.HasPrefix(pp, "r="):
			rPart = pp
		}
	}
	clientFinalNoProof := cPart + "," + rPart
	s1Msg := fmt.Sprintf("r=%s,s=%s,i=%d", st.sNonce, st.salt, st.iter)
	authMessage := fmt.Sprintf("n=%s,r=%s,%s,%s", st.storedUser, st.storedCNonce, s1Msg, clientFinalNoProof)
	saltedPW := pbkdf2SHA256([]byte(testPassword), mustB64URLDecode(t, st.salt), st.iter, 32)
	serverKey := hmacSHA256(saltedPW, []byte("Server Key"))
	sig := base64.StdEncoding.EncodeToString(hmacSHA256(serverKey, []byte(authMessage)))
	if wrongSig {
		sig = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
	}
	return b64urlEncode([]byte("v=" + sig))
}

// newSCRAMCookieServer 模拟 FIN 5.3 握手 Set-Cookie + FIN 3.9 请求头风格：
//   - client-final 阶段返回 Set-Cookie: skyarc-auth-8800=<token>;Path=/;HttpOnly;SameSite=strict
//   - 后续请求统一用 'Authorization: BEARER authToken=<token>'（phable 风格，GET/POST 兼容；
//     真机实测 Cookie 只对 GET 有效、POST 会被 Jetty 拒 400）
func newSCRAMCookieServer(t *testing.T, authed http.HandlerFunc) *httptest.Server {
	t.Helper()
	st := &scramServerState{}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		switch {
		case strings.HasPrefix(auth, "HELLO "):
			w.Header().Set("WWW-Authenticate", "haystack handshakeToken="+testHandshakeToken+", hash=SHA-256")
			w.WriteHeader(http.StatusUnauthorized)

		case strings.HasPrefix(auth, "SCRAM "):
			dataB64, token, ok := parseSCRAMHeader(auth)
			if !ok || token != testHandshakeToken {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			decoded, err := b64urlDecode(dataB64)
			if err != nil {
				http.Error(w, "bad data", http.StatusBadRequest)
				return
			}
			msg := string(decoded)
			if strings.Contains(msg, ",p=") {
				// 阶段 3：client-final → authToken + Set-Cookie
				st.mu.Lock()
				defer st.mu.Unlock()
				dataVal := computeServerFinalData(t, st, msg, false)
				w.Header().Set("Authentication-Info", "authToken="+testAuthTokenFIN53+", data="+dataVal)
				// 真机 FIN 5.3：Set-Cookie 提供动态 cookie 名（skyarc-auth-<port>）
				w.Header().Set("Set-Cookie", "skyarc-auth-8800="+testAuthTokenFIN53+";Path=/;HttpOnly;SameSite=strict")
				w.WriteHeader(http.StatusOK)
				_, _ = io.WriteString(w, `{"meta":{"ver":"3.0"},"cols":[],"rows":[]}`)
				return
			}
			// 阶段 2：client-first → server-first
			st.mu.Lock()
			defer st.mu.Unlock()
			gs2rest := strings.TrimPrefix(msg, "n,,")
			userPart, rPart, _ := strings.Cut(gs2rest, ",")
			st.storedUser = strings.TrimPrefix(userPart, "n=")
			st.storedCNonce = strings.TrimPrefix(rPart, "r=")
			st.sNonce = st.storedCNonce + "server123456"
			st.salt = b64urlEncode([]byte("0123456789abcdef0123456789abcdef"))
			st.iter = 4096
			dataVal := b64urlEncode([]byte(fmt.Sprintf("r=%s,s=%s,i=%d", st.sNonce, st.salt, st.iter)))
			w.Header().Set("WWW-Authenticate", "haystack data="+dataVal+", handshakeToken="+testHandshakeToken)
			w.WriteHeader(http.StatusUnauthorized)

		case strings.HasPrefix(auth, "BEARER authToken="):
			// 真机 FIN 3.9：认证后统一用 BEARER authToken=<token>（GET/POST 兼容）
			if auth != "BEARER authToken="+testAuthTokenFIN53 {
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
			if authed != nil {
				authed(w, r)
				return
			}
			_, _ = io.WriteString(w, `{"meta":{"ver":"3.0"},"cols":[],"rows":[]}`)

		default:
			w.WriteHeader(http.StatusUnauthorized)
		}
	})
	return httptest.NewServer(mux)
}

// newSCRAMServerWithSalt 与 newSCRAMServer 相同，但可指定 server-first 的 salt。
// saltB64 非空时原样发送（可用于模拟标准 base64 含 +/ 的 salt），为空时用默认值。
// fin53 为可选布尔：置真时 client-final 阶段按真机 FIN 5.3 格式返回
// HTTP 200 + Authentication-Info（authToken 含连字符），否则走旧兼容格式
// HTTP 200 + WWW-Authenticate。
func newSCRAMServerWithSalt(t *testing.T, wrongSig bool, authed http.HandlerFunc, saltB64 string, fin53 ...bool) *httptest.Server {
	t.Helper()
	useFIN53 := len(fin53) > 0 && fin53[0]
	st := &scramServerState{salt: saltB64}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		switch {
		case strings.HasPrefix(auth, "HELLO "):
			// 阶段 1：HELLO → handshakeToken + hash
			w.Header().Set("WWW-Authenticate", "haystack handshakeToken="+testHandshakeToken+", hash=SHA-256")
			w.WriteHeader(http.StatusUnauthorized)

		case strings.HasPrefix(auth, "SCRAM "):
			dataB64, token, ok := parseSCRAMHeader(auth)
			if !ok || token != testHandshakeToken {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			decoded, err := b64urlDecode(dataB64)
			if err != nil {
				http.Error(w, "bad data", http.StatusBadRequest)
				return
			}
			msg := string(decoded)
			if strings.Contains(msg, ",p=") {
				// 阶段 3：client-final → authToken + server signature
				st.mu.Lock()
				defer st.mu.Unlock()
				cPart, rPart := "", ""
				for _, pp := range strings.Split(msg, ",") {
					switch {
					case strings.HasPrefix(pp, "c="):
						cPart = pp
					case strings.HasPrefix(pp, "r="):
						rPart = pp
					}
				}
				clientFinalNoProof := cPart + "," + rPart
				s1Msg := fmt.Sprintf("r=%s,s=%s,i=%d", st.sNonce, st.salt, st.iter)
				authMessage := fmt.Sprintf("n=%s,r=%s,%s,%s", st.storedUser, st.storedCNonce, s1Msg, clientFinalNoProof)
				saltedPW := pbkdf2SHA256([]byte(testPassword), mustB64URLDecode(t, st.salt), st.iter, 32)
				serverKey := hmacSHA256(saltedPW, []byte("Server Key"))
				sig := base64.StdEncoding.EncodeToString(hmacSHA256(serverKey, []byte(authMessage)))
				if wrongSig {
					sig = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
				}
				dataVal := b64urlEncode([]byte("v=" + sig))
				authToken := testAuthToken
				if useFIN53 {
					// 真机 FIN 5.3：认证成功返回 HTTP 200，authToken/data 在
					// Authentication-Info 头（authToken 为含连字符的 web- 风格）。
					authToken = testAuthTokenFIN53
					w.Header().Set("Authentication-Info", "authToken="+authToken+", data="+dataVal)
				} else {
					// 旧兼容：401 挑战格式，但本项目 Mock 直接 200 + WWW-Authenticate。
					w.Header().Set("WWW-Authenticate", "haystack authToken="+authToken+", data="+dataVal)
				}
				w.WriteHeader(http.StatusOK)
				_, _ = io.WriteString(w, `{"meta":{"ver":"3.0"},"cols":[],"rows":[]}`)
				return
			}
			// 阶段 2：client-first → server-first (nonce/salt/iter)
			st.mu.Lock()
			defer st.mu.Unlock()
			gs2rest := strings.TrimPrefix(msg, "n,,")
			userPart, rPart, _ := strings.Cut(gs2rest, ",")
			st.storedUser = strings.TrimPrefix(userPart, "n=")
			st.storedCNonce = strings.TrimPrefix(rPart, "r=")
			st.sNonce = st.storedCNonce + "server123456"
			if st.salt == "" {
				st.salt = b64urlEncode([]byte("0123456789abcdef0123456789abcdef"))
			}
			st.iter = 4096
			dataVal := b64urlEncode([]byte(fmt.Sprintf("r=%s,s=%s,i=%d", st.sNonce, st.salt, st.iter)))
			w.Header().Set("WWW-Authenticate", "haystack data="+dataVal+", handshakeToken="+testHandshakeToken)
			w.WriteHeader(http.StatusUnauthorized)

		case strings.HasPrefix(auth, "BEARER authToken="):
			// 真机 FIN 3.9：SCRAM 认证后请求头为 'BEARER authToken=<token>'（phable 风格），
			// 兼容 GET/POST（Cookie 只对 GET 有效、POST 会被 Jetty 拒 400）。
			if auth != "BEARER authToken="+testAuthToken && auth != "BEARER authToken="+testAuthTokenFIN53 {
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
			if authed != nil {
				authed(w, r)
				return
			}
			_, _ = io.WriteString(w, `{"meta":{"ver":"3.0"},"cols":[],"rows":[]}`)

		default:
			w.WriteHeader(http.StatusUnauthorized)
		}
	})
	return httptest.NewServer(mux)
}

// requireBearer 校验请求携带 'Authorization: BEARER authToken=<token>' 头
// （真机 FIN 3.9 实测：SCRAM 认证后所有请求统一用 phable 风格 BEARER authToken，兼容 GET/POST）。
func requireBearer(t *testing.T, r *http.Request) bool {
	t.Helper()
	want := "BEARER authToken=" + testAuthToken
	if got := r.Header.Get("Authorization"); got != want {
		t.Errorf("expected Authorization %q, got %q", want, got)
		return false
	}
	return true
}

// mockState 记录业务端点收到的请求。
// 兼容两种 body 格式：text/zinc（真机 3.9.12 必需）与 application/json（旧兼容分支）。
type mockState struct {
	mu sync.Mutex

	readFilters  []string
	readLimits   []string
	pwBodies     []string
	pwGrids      []*HGrid
	commitBodies []string
	commitGrids  []*HGrid
	commitRows   []map[string]Val
	commitModes  []string
	evalBodies   []string
	evalGrids    []*HGrid
}

// readGridRequest 读取请求体并解析为 HGrid（兼容 text/zinc 与 application/json）。
// wantCT 非空时校验 Content-Type 精确匹配。
func readGridRequest(t *testing.T, r *http.Request, wantCT string) (string, *HGrid) {
	t.Helper()
	data, err := io.ReadAll(r.Body)
	if err != nil {
		t.Fatalf("read request body: %v", err)
	}
	ct := r.Header.Get("Content-Type")
	if wantCT != "" && ct != wantCT {
		t.Errorf("Content-Type = %q, want %q (body=%s)", ct, wantCT, string(data))
	}
	grid, err := parseGrid(data, ct)
	if err != nil {
		t.Fatalf("parse request grid: %v (ct=%q body=%q)", err, ct, string(data))
	}
	return string(data), grid
}

// newFINServer 构造完整业务端点 Mock（SCRAM 握手 + about/read/commit/pointWrite/eval）。
func newFINServer(t *testing.T) (*httptest.Server, *mockState) {
	t.Helper()
	st := &mockState{
		readFilters:  make([]string, 0),
		readLimits:   make([]string, 0),
		pwBodies:     make([]string, 0),
		pwGrids:      make([]*HGrid, 0),
		commitBodies: make([]string, 0),
		commitGrids:  make([]*HGrid, 0),
		commitRows:   make([]map[string]Val, 0),
		commitModes:  make([]string, 0),
		evalBodies:   make([]string, 0),
		evalGrids:    make([]*HGrid, 0),
	}

	aboutHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}, {"name": "serverName"}, {"name": "version"}],
			"rows": [{
				"finVersion": {"_kind": "Str", "val": "5.3"},
				"serverName": {"_kind": "Str", "val": "demo-fin"},
				"version": {"_kind": "Str", "val": "5.3.1"}
			}]
		}`)
	})
	srv := newSCRAMServer(t, false, aboutHandler)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)

	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		if r.Method != http.MethodGet {
			t.Errorf("read method = %s, want GET", r.Method)
		}
		filter := r.URL.Query().Get("filter")
		limit := r.URL.Query().Get("limit")
		st.mu.Lock()
		st.readFilters = append(st.readFilters, filter)
		st.readLimits = append(st.readLimits, limit)
		st.mu.Unlock()
		grid := `{
			"meta": {"ver": "3.0"},
			"cols": [
				{"name": "id", "meta": {"_kind": "Ref"}},
				{"name": "mod", "meta": {"_kind": "DateTime"}},
				{"name": "dis"},
				{"name": "temp"},
				{"name": "point"},
				{"name": "onOff"},
				{"name": "na"},
				{"name": "str"}
			],
			"rows": [{
				"id": {"_kind": "Ref", "val": "p:demo:r:p123", "dis": "Point 1"},
				"mod": {"_kind": "DateTime", "val": "` + testModDateTime + `"},
				"dis": "Point 1",
				"temp": {"_kind": "Number", "val": 21.5, "unit": "°F"},
				"point": {"_kind": "Marker"},
				"onOff": {"_kind": "Bool", "val": true},
				"na": {"_kind": "NA"},
				"str": {"_kind": "Str", "val": "hello"}
			}]
		}`
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, grid)
	})

	mux.HandleFunc("/api/demo/pointWrite", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		raw, grid := readGridRequest(t, r, "text/zinc")
		st.mu.Lock()
		st.pwBodies = append(st.pwBodies, raw)
		st.pwGrids = append(st.pwGrids, grid)
		st.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"meta":{},"cols":[],"rows":[]}`)
	})

	mux.HandleFunc("/api/demo/commit", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		raw, grid := readGridRequest(t, r, "text/zinc")
		st.mu.Lock()
		mode, _ := grid.Meta["commit"].(string)
		st.commitModes = append(st.commitModes, mode)
		st.commitBodies = append(st.commitBodies, raw)
		st.commitGrids = append(st.commitGrids, grid)
		st.commitRows = append(st.commitRows, grid.Rows...)
		st.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"meta":{"commit":"`+mode+`"},"cols":[],"rows":[]}`)
	})

	mux.HandleFunc("/api/demo/eval", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		raw, grid := readGridRequest(t, r, "text/zinc")
		st.mu.Lock()
		st.evalBodies = append(st.evalBodies, raw)
		st.evalGrids = append(st.evalGrids, grid)
		st.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"meta":{"ver":"3.0"},"cols":[{"name":"name"}],"rows":[{"name":{"_kind":"Str","val":"ahu1"}}]}`)
	})

	srv.Config.Handler = mux
	return srv, st
}

// newTestClient 创建指向 Mock 的 FIN 客户端。
func newTestClient(t *testing.T, srv *httptest.Server) *Client {
	t.Helper()
	c, err := NewClient(Config{
		URL:      srv.URL + "/api/demo",
		AuthType: AuthTypeSCRAM,
		Username: testUsername,
		Password: testPassword,
		Timeout:  3 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

// ----------------------------------------------------------------------------
// 1. SCRAM 全流程
// ----------------------------------------------------------------------------

func TestSCRAMFullFlow(t *testing.T) {
	srv := newSCRAMServer(t, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}],
			"rows": [{"finVersion": {"_kind": "Str", "val": "5.3"}}]
		}`)
	}))
	defer srv.Close()
	c := newTestClient(t, srv)

	_, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About after SCRAM: %v", err)
	}
	c.tokenMu.RLock()
	tok := c.authTok
	init := c.authInit
	c.tokenMu.RUnlock()
	if !init {
		t.Error("authInit should be true after handshake")
	}
	if tok != testAuthToken {
		t.Errorf("authToken = %q, want %q", tok, testAuthToken)
	}
}

func TestSCRAMFullFlowFIN53(t *testing.T) {
	// 真机 FIN 5.3 格式：client-final 阶段返回 HTTP 200，authToken/data 在
	// Authentication-Info 头，authToken 为含连字符的 web- 风格。
	srv := newSCRAMServerWithSalt(t, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}],
			"rows": [{"finVersion": {"_kind": "Str", "val": "5.3"}}]
		}`)
	}), "", true)
	defer srv.Close()
	c := newTestClient(t, srv)

	_, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About after SCRAM (FIN 5.3 Authentication-Info): %v", err)
	}
	c.tokenMu.RLock()
	tok := c.authTok
	init := c.authInit
	c.tokenMu.RUnlock()
	if !init {
		t.Error("authInit should be true after handshake")
	}
	if tok != testAuthTokenFIN53 {
		t.Errorf("authToken = %q, want %q", tok, testAuthTokenFIN53)
	}
}

func TestSCRAMServerSignatureMismatch(t *testing.T) {
	srv := newSCRAMServer(t, true, nil)
	defer srv.Close()
	c := newTestClient(t, srv)

	_, err := c.About(context.Background())
	if err == nil {
		t.Fatal("expected error for wrong server signature, got nil")
	}
	if !strings.Contains(err.Error(), "server signature mismatch") {
		t.Errorf("unexpected error: %v", err)
	}
	var ae *ErrAuth
	if !errors.As(err, &ae) {
		t.Errorf("expected *ErrAuth, got %T", err)
	}
}

func TestSCRAMStandardBase64Salt(t *testing.T) {
	// 真机 FIN 返回的 salt 可能是标准 base64（含 +/，无填充），如
	// "+////////////////////w"。旧代码用 base64.URLEncoding 解码会在 byte 0 报
	// "illegal base64 data"，本测试覆盖该回归路径（b64urlDecode 的 StdEncoding 兜底）。
	stdSalt := "+////////////////////w"
	srv := newSCRAMServerWithSalt(t, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}],
			"rows": [{"finVersion": {"_kind": "Str", "val": "5.3"}}]
		}`)
	}), stdSalt)
	defer srv.Close()
	c := newTestClient(t, srv)

	if _, err := c.About(context.Background()); err != nil {
		t.Fatalf("About with standard-base64 salt: %v", err)
	}
	c.tokenMu.RLock()
	tok := c.authTok
	c.tokenMu.RUnlock()
	if tok != testAuthToken {
		t.Errorf("authToken = %q, want %q", tok, testAuthToken)
	}
}

// ----------------------------------------------------------------------------
// 1b. FIN 5.3 Cookie 鉴权（Set-Cookie + Cookie header）
// ----------------------------------------------------------------------------

func TestCookieAuthFIN53(t *testing.T) {
	// 真机 FIN 5.3 握手响应带 Set-Cookie（skyarc-auth-<port>），但 FIN 3.9 实测请求鉴权头
	// 统一用 'Authorization: BEARER authToken=<token>'（phable 风格，兼容 GET/POST；
	// Cookie 只对 GET 有效、POST 会被 Jetty 拒 400）。客户端仍从 Set-Cookie 解析 cookie
	// 留档（诊断用），但实际请求头用 BEARER authToken。
	var mu sync.Mutex
	var gotCookie, gotAuth string
	srv := newSCRAMCookieServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		gotCookie = r.Header.Get("Cookie")
		gotAuth = r.Header.Get("Authorization")
		mu.Unlock()
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}, {"name": "serverName"}],
			"rows": [{
				"finVersion": {"_kind": "Str", "val": "5.3"},
				"serverName": {"_kind": "Str", "val": "cookie-demo"}
			}]
		}`)
	}))
	defer srv.Close()
	c := newTestClient(t, srv)

	info, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About with cookie auth: %v", err)
	}
	if info.ServerName != "cookie-demo" {
		t.Errorf("ServerName = %q, want cookie-demo", info.ServerName)
	}

	mu.Lock()
	cookie, auth := gotCookie, gotAuth
	mu.Unlock()
	if cookie != "" {
		t.Errorf("Cookie = %q, want empty (请求头用 BEARER authToken，不再依赖 Cookie)", cookie)
	}
	wantAuth := "BEARER authToken=" + testAuthTokenFIN53
	if auth != wantAuth {
		t.Errorf("Authorization = %q, want %q", auth, wantAuth)
	}

	// 客户端内部状态：cookieName/cookieTok 仍从 Set-Cookie 解析留档（诊断用）
	c.tokenMu.RLock()
	cn, ck := c.cookieName, c.cookieTok
	c.tokenMu.RUnlock()
	if cn != "skyarc-auth-8800" {
		t.Errorf("cookieName = %q, want skyarc-auth-8800", cn)
	}
	if ck != testAuthTokenFIN53 {
		t.Errorf("cookieTok = %q, want %q", ck, testAuthTokenFIN53)
	}
}

func TestParseSetCookie(t *testing.T) {
	h := http.Header{}
	h.Set("Set-Cookie", "skyarc-auth-8800=web-abc;Path=/;HttpOnly;SameSite=strict")
	name, val := parseSetCookie(h)
	if name != "skyarc-auth-8800" || val != "web-abc" {
		t.Errorf("parseSetCookie = (%q, %q), want (skyarc-auth-8800, web-abc)", name, val)
	}
}

func TestParseSetCookieEmpty(t *testing.T) {
	// 旧版/SkySpark 无 Set-Cookie → 返回空，保持 Bearer 鉴权。
	h := http.Header{}
	if name, val := parseSetCookie(h); name != "" || val != "" {
		t.Errorf("parseSetCookie(empty) = (%q, %q), want empty", name, val)
	}
}

// ----------------------------------------------------------------------------
// 1c. Zinc 文本响应解析（真机 FIN 5.3 about/read 返回 Zinc 而非 JSON）
// ----------------------------------------------------------------------------

func TestZincAboutResponse(t *testing.T) {
	srv := newSCRAMServer(t, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/zinc; charset=utf-8")
		_, _ = io.WriteString(w, "ver:\"3.0\"\n"+
			"finVersion,serverName,version\n"+
			"\"5.3\",\"demo-fin\",\"5.3.1\"\n")
	}))
	defer srv.Close()
	c := newTestClient(t, srv)

	info, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About Zinc: %v", err)
	}
	if info.FinVersion != "5.3" {
		t.Errorf("FinVersion = %q, want 5.3", info.FinVersion)
	}
	if info.ServerName != "demo-fin" {
		t.Errorf("ServerName = %q, want demo-fin", info.ServerName)
	}
	if info.Version != "5.3.1" {
		t.Errorf("Version = %q, want 5.3.1", info.Version)
	}
}

func TestZincAboutRealColumns(t *testing.T) {
	// 真机 FIN 5.3 Zinc about 列名：haystackVersion/projName（兼容回退 finVersion/serverName）。
	srv := newSCRAMServer(t, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\"\n"+
			"haystackVersion,projName,finVersion\n"+
			"\"3.0\",\"demo\",\"5.3\"\n")
	}))
	defer srv.Close()
	c := newTestClient(t, srv)

	info, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About Zinc real columns: %v", err)
	}
	if info.FinVersion != "5.3" {
		t.Errorf("FinVersion = %q, want 5.3 (from finVersion col)", info.FinVersion)
	}
	if info.ServerName != "demo" {
		t.Errorf("ServerName = %q, want demo (fallback to projName)", info.ServerName)
	}
}

func TestZincReadResponse(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	// 覆盖 /read 端点，返回 Zinc 网格（含各值类型）。
	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\"\n"+
			"id,mod,dis,temp,point,onOff,na\n"+
			"@p:demo:r:p123 \"Point 1\",@p:2026-08-27T12:00:00.000Z,\"P\\\"1\",21.5°F,M,T,NA\n")
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	grid, err := c.Read(context.Background(), "point", 10)
	if err != nil {
		t.Fatalf("Read Zinc: %v", err)
	}
	if len(grid.Rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(grid.Rows))
	}
	row := grid.Rows[0]

	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p123" {
		t.Errorf("id = %+v, want Ref p:demo:r:p123", id)
	} else if id.Dis != "Point 1" {
		t.Errorf("id.dis = %q, want Point 1", id.Dis)
	}
	// mod 是 Ref 类型（如 @p:xxx），commit update/remove 必须携带
	if m, ok := row["mod"]; !ok || m.Kind != KindRef || m.Val != testModRef {
		t.Errorf("mod = %+v, want Ref %s", m, testModRef)
	}
	if d, ok := row["dis"]; !ok || d.Kind != KindStr || d.Val != `P"1` {
		t.Errorf("dis = %+v, want Str P\"1 (escaped quote)", d)
	}
	if tmp, ok := row["temp"]; !ok || tmp.Kind != KindNumber || tmp.Val != 21.5 || tmp.Unit != "°F" {
		t.Errorf("temp = %+v, want Number 21.5°F", tmp)
	}
	if pt, ok := row["point"]; !ok || pt.Kind != KindMarker {
		t.Errorf("point = %+v, want Marker", pt)
	}
	if b, ok := row["onOff"]; !ok || b.Kind != KindBool || b.Val != true {
		t.Errorf("onOff = %+v, want Bool true", b)
	}
	if na, ok := row["na"]; !ok || na.Kind != KindNA {
		t.Errorf("na = %+v, want NA", na)
	}
}

func TestParseGridZincValues(t *testing.T) {
	// 直接验证 Zinc 各值类型解析（含 Ref display、Str 转义、Number 单位、Marker/NA/Remove）。
	data := []byte("ver:\"3.0\" dis:\"Zinc Grid\"\n" +
		"id,dis,temp,point,onOff,na,remove\n" +
		"@p:demo:r:p123 \"Point 1\",\"P\\\"1\\n2\",21.5kW,M,F,NA,R\n")
	hg, err := parseGrid(data, "text/zinc")
	if err != nil {
		t.Fatalf("parseGrid Zinc: %v", err)
	}
	if hg.Meta["ver"] != "3.0" {
		t.Errorf("meta.ver = %v, want 3.0", hg.Meta["ver"])
	}
	if hg.Meta["dis"] != "Zinc Grid" {
		t.Errorf("meta.dis = %v, want Zinc Grid", hg.Meta["dis"])
	}
	if len(hg.Cols) != 7 {
		t.Errorf("cols = %d, want 7", len(hg.Cols))
	}
	if len(hg.Rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(hg.Rows))
	}
	row := hg.Rows[0]

	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p123" || id.Dis != "Point 1" {
		t.Errorf("id = %+v, want Ref p:demo:r:p123 dis=Point 1", id)
	}
	if d, ok := row["dis"]; !ok || d.Kind != KindStr || d.Val != "P\"1\n2" {
		t.Errorf("dis = %+v, want Str P\"1\\n2", d)
	}
	if tmp, ok := row["temp"]; !ok || tmp.Kind != KindNumber || tmp.Val != 21.5 || tmp.Unit != "kW" {
		t.Errorf("temp = %+v, want Number 21.5kW", tmp)
	}
	if pt, ok := row["point"]; !ok || pt.Kind != KindMarker {
		t.Errorf("point = %+v, want Marker", pt)
	}
	if b, ok := row["onOff"]; !ok || b.Kind != KindBool || b.Val != false {
		t.Errorf("onOff = %+v, want Bool false", b)
	}
	if na, ok := row["na"]; !ok || na.Kind != KindNA {
		t.Errorf("na = %+v, want NA", na)
	}
	if rm, ok := row["remove"]; !ok || rm.Kind != KindRemove {
		t.Errorf("remove = %+v, want Remove", rm)
	}
}

// ----------------------------------------------------------------------------
// 2. about：finVersion/serverName 解析
// ----------------------------------------------------------------------------

func TestAboutParsesFields(t *testing.T) {
	srv, _ := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	info, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About: %v", err)
	}
	if info.FinVersion != "5.3" {
		t.Errorf("FinVersion = %q, want 5.3", info.FinVersion)
	}
	if info.ServerName != "demo-fin" {
		t.Errorf("ServerName = %q, want demo-fin", info.ServerName)
	}
	if info.Version != "5.3.1" {
		t.Errorf("Version = %q, want 5.3.1", info.Version)
	}
}

// ----------------------------------------------------------------------------
// 3. read：filter+limit 传递 + HGrid 类型解析
// ----------------------------------------------------------------------------

func TestReadFilterLimitAndTypes(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	grid, err := c.Read(context.Background(), "point and siteRef==@p:demo:r:site01", 42)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if grid == nil {
		t.Fatal("Read returned nil grid")
	}

	st.mu.Lock()
	if len(st.readFilters) != 1 {
		st.mu.Unlock()
		t.Fatalf("read filters = %d, want 1", len(st.readFilters))
	}
	filter := st.readFilters[0]
	limit := st.readLimits[0]
	st.mu.Unlock()
	if filter != "point and siteRef==@p:demo:r:site01" {
		t.Errorf("filter = %q, want point and siteRef==@p:demo:r:site01", filter)
	}
	if limit != "42" {
		t.Errorf("limit = %q, want 42", limit)
	}

	if len(grid.Rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(grid.Rows))
	}
	row := grid.Rows[0]

	if id, ok := row["id"]; !ok || id.Kind != KindRef {
		t.Errorf("id kind = %v, want Ref", id.Kind)
	} else if id.Val != "p:demo:r:p123" {
		t.Errorf("id val = %v, want p:demo:r:p123", id.Val)
	}

	if temp, ok := row["temp"]; !ok || temp.Kind != KindNumber {
		t.Errorf("temp kind = %v, want Number", temp.Kind)
	} else if temp.Val != 21.5 {
		t.Errorf("temp val = %v, want 21.5", temp.Val)
	} else if temp.Unit != "°F" {
		t.Errorf("temp unit = %q, want °F", temp.Unit)
	}

	if pt, ok := row["point"]; !ok || pt.Kind != KindMarker {
		t.Errorf("point kind = %v, want Marker", pt.Kind)
	}

	if b, ok := row["onOff"]; !ok || b.Kind != KindBool || b.Val != true {
		t.Errorf("onOff = %+v, want Bool true", b)
	}

	if na, ok := row["na"]; !ok || na.Kind != KindNA {
		t.Errorf("na kind = %v, want NA", na.Kind)
	}

	if s, ok := row["str"]; !ok || s.Kind != KindStr || s.Val != "hello" {
		t.Errorf("str = %+v, want Str hello", s)
	}
}

// ----------------------------------------------------------------------------
// 4. pointWrite：编码 + 超时释放 + 重写取消旧定时器
// ----------------------------------------------------------------------------

func TestPointWriteEncoding(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	err := c.PointWrite(context.Background(), "@p:demo:r:p123", 8, NewNumberVal(42), 0)
	if err != nil {
		t.Fatalf("PointWrite: %v", err)
	}

	st.mu.Lock()
	if len(st.pwGrids) != 1 {
		st.mu.Unlock()
		t.Fatalf("pw grids = %d, want 1", len(st.pwGrids))
	}
	grid := st.pwGrids[0]
	raw := st.pwBodies[0]
	st.mu.Unlock()

	// Content-Type 与 Zinc body 精确校验
	wantRaw := "ver:\"3.0\"\nid,priority,val\n@p:demo:r:p123,8.0,42.0\n\n"
	if raw != wantRaw {
		t.Errorf("pointWrite body = %q, want %q", raw, wantRaw)
	}
	if len(grid.Cols) != 3 || grid.Cols[0].Name != "id" || grid.Cols[1].Name != "priority" || grid.Cols[2].Name != "val" {
		t.Errorf("cols = %+v, want [id priority val]", grid.Cols)
	}
	if len(grid.Rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(grid.Rows))
	}
	row := grid.Rows[0]
	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p123" {
		t.Errorf("id = %+v, want Ref p:demo:r:p123 (stripped of @)", id)
	}
	if pri, ok := row["priority"]; !ok || pri.Kind != KindNumber || pri.Val != 8.0 {
		t.Errorf("priority = %+v, want Number 8", pri)
	}
	if val, ok := row["val"]; !ok || val.Kind != KindNumber || val.Val != 42.0 {
		t.Errorf("val = %+v, want Number 42", val)
	}
}

func TestPointWriteTimeoutRelease(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	_, err := c.WriteOverride(context.Background(), WriteOverrideParams{
		PointID:     "@p:demo:r:p123",
		Val:         json.RawMessage(`42.0`),
		Priority:    8,
		DurationSec: 1,
	})
	if err != nil {
		t.Fatalf("WriteOverride: %v", err)
	}

	// 等待释放写（最多 3s）
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		st.mu.Lock()
		n := len(st.pwGrids)
		st.mu.Unlock()
		if n >= 2 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}

	st.mu.Lock()
	n := len(st.pwGrids)
	var second *HGrid
	if n >= 2 {
		second = st.pwGrids[1]
	}
	st.mu.Unlock()

	if n < 2 {
		t.Fatalf("expected release write, got %d writes", n)
	}
	row := second.Rows[0]
	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p123" {
		t.Errorf("release id = %+v, want Ref p:demo:r:p123", id)
	}
	if pri, ok := row["priority"]; !ok || pri.Kind != KindNumber || pri.Val != 8.0 {
		t.Errorf("release priority = %+v, want Number 8", pri)
	}
	if val, ok := row["val"]; !ok || val.Kind != KindNA {
		t.Errorf("release val = %+v, want NA", val)
	}
}

func TestPointWriteRewriteCancelsOldTimer(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	// 第一次写：duration=1s 会调度释放
	if _, err := c.WriteOverride(context.Background(), WriteOverrideParams{
		PointID: "@p:demo:r:p123", Val: json.RawMessage(`10`), Priority: 8, DurationSec: 1,
	}); err != nil {
		t.Fatalf("first write: %v", err)
	}
	// 第二次写：同点同优先级，duration=0（永久）应取消旧定时器
	if _, err := c.WriteOverride(context.Background(), WriteOverrideParams{
		PointID: "@p:demo:r:p123", Val: json.RawMessage(`20`), Priority: 8, DurationSec: 0,
	}); err != nil {
		t.Fatalf("second write: %v", err)
	}

	// 等待超过第一次的 1s 定时器
	time.Sleep(1300 * time.Millisecond)

	st.mu.Lock()
	n := len(st.pwGrids)
	st.mu.Unlock()
	if n != 2 {
		t.Errorf("writes = %d, want 2 (no release after rewrite)", n)
	}
}

// ----------------------------------------------------------------------------
// 5. commit add：批量建点，Ref/Marker 类型正确
// ----------------------------------------------------------------------------

func TestCommitAddRefMarkerTypes(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	synced, errs, err := c.SyncPoints(context.Background(), "@p:demo:r:site01", "@p:demo:r:ahu01", []SyncPoint{
		{
			Name: "AHU-01 Supply Air Temp",
			Spec: "ph.points::AirTempSensor",
			Kind: KindNumber,
			Unit: "°F",
			Tags: map[string]any{
				"temp":   true,
				"desc":   "supply air temperature",
				"parent": "@p:demo:r:ahu01",
				"max":    100.0,
			},
		},
		{
			Name: "AHU-01 Run",
			Kind: KindBool,
		},
	})
	if err != nil {
		t.Fatalf("SyncPoints: %v", err)
	}
	if len(errs) != 0 {
		t.Errorf("errs = %v, want none", errs)
	}
	if synced != 2 {
		t.Errorf("synced = %d, want 2", synced)
	}

	st.mu.Lock()
	defer st.mu.Unlock()
	if len(st.commitModes) != 1 || st.commitModes[0] != "add" {
		t.Fatalf("commit modes = %v, want [add]", st.commitModes)
	}
	if len(st.commitRows) != 2 {
		t.Fatalf("commit rows = %d, want 2", len(st.commitRows))
	}
	if len(st.commitBodies) != 1 {
		t.Fatalf("commit bodies = %d, want 1", len(st.commitBodies))
	}
	if raw := st.commitBodies[0]; !strings.HasPrefix(raw, "ver:\"3.0\" commit:\"add\"\n") {
		t.Errorf("commit body = %q, want Zinc prefix ver:\"3.0\" commit:\"add\"", raw)
	}

	row := st.commitRows[0]
	// Ref 必须是 Ref 类型
	if sr, ok := row["siteRef"]; !ok || sr.Kind != KindRef || sr.Val != "p:demo:r:site01" {
		t.Errorf("siteRef = %+v, want Ref p:demo:r:site01", sr)
	}
	if er, ok := row["equipRef"]; !ok || er.Kind != KindRef || er.Val != "p:demo:r:ahu01" {
		t.Errorf("equipRef = %+v, want Ref p:demo:r:ahu01", er)
	}
	// Marker 必须是 Marker 类型（不是布尔）
	if pt, ok := row["point"]; !ok || pt.Kind != KindMarker {
		t.Errorf("point = %+v, want Marker", pt)
	}
	if tg, ok := row["temp"]; !ok || tg.Kind != KindMarker {
		t.Errorf("temp tag = %+v, want Marker", tg)
	}
	// 字符串 → Str
	if d, ok := row["desc"]; !ok || d.Kind != KindStr || d.Val != "supply air temperature" {
		t.Errorf("desc = %+v, want Str", d)
	}
	// @ 开头 → Ref
	if p, ok := row["parent"]; !ok || p.Kind != KindRef || p.Val != "p:demo:r:ahu01" {
		t.Errorf("parent = %+v, want Ref p:demo:r:ahu01", p)
	}
	// 数字 → Number
	if m, ok := row["max"]; !ok || m.Kind != KindNumber || m.Val != 100.0 {
		t.Errorf("max = %+v, want Number 100", m)
	}
	// kind 标签
	if k, ok := row["kind"]; !ok || k.Kind != KindStr || k.Val != "Number" {
		t.Errorf("kind = %+v, want Str Number", k)
	}
	// spec 标签
	if sp, ok := row["spec"]; !ok || sp.Kind != KindStr || sp.Val != "ph.points::AirTempSensor" {
		t.Errorf("spec = %+v, want Str ph.points::AirTempSensor", sp)
	}
}

func TestSyncPointInvalidKindRecordsError(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	synced, errs, err := c.SyncPoints(context.Background(), "@p:demo:r:site01", "", []SyncPoint{
		{Name: "Valid Point", Kind: KindNumber},
		{Name: "Bad Point", Kind: "Date"},
	})
	if err != nil {
		t.Fatalf("SyncPoints: %v", err)
	}
	if synced != 1 {
		t.Errorf("synced = %d, want 1", synced)
	}
	if len(errs) != 1 || errs[0].Name != "Bad Point" {
		t.Errorf("errs = %+v, want 1 error for Bad Point", errs)
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	if len(st.commitRows) != 1 {
		t.Errorf("commit rows = %d, want 1 (invalid skipped)", len(st.commitRows))
	}
}

// ----------------------------------------------------------------------------
// 6. commit update/remove
// ----------------------------------------------------------------------------

func TestCommitUpdate(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	err := c.UpdateEntity(context.Background(), "@p:demo:r:p123", map[string]any{
		"newTag": true,
		"dis":    "Updated Name",
	}, []string{"oldTag"})
	if err != nil {
		t.Fatalf("UpdateEntity: %v", err)
	}

	st.mu.Lock()
	defer st.mu.Unlock()

	// 客户端应先 Read 该记录取 mod（乐观锁），再提交 update
	if len(st.readFilters) != 1 {
		t.Fatalf("read filters = %d, want 1 (auto-read for mod)", len(st.readFilters))
	}
	if f := st.readFilters[0]; f != "id==@p:demo:r:p123" {
		t.Errorf("read filter = %q, want id==@p:demo:r:p123", f)
	}

	if len(st.commitModes) != 1 || st.commitModes[0] != "update" {
		t.Fatalf("commit modes = %v, want [update]", st.commitModes)
	}
	if len(st.commitRows) != 1 {
		t.Fatalf("commit rows = %d, want 1", len(st.commitRows))
	}
	row := st.commitRows[0]
	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p123" {
		t.Errorf("id = %+v, want Ref p:demo:r:p123", id)
	}
	// 关键：update diff 行必须带 mod（真机 FIN 3.9 为 DateTime 类型）
	if m, ok := row["mod"]; !ok || m.Kind != KindDateTime {
		t.Errorf("mod = %+v, want DateTime (commit update 必须带 mod 乐观锁)", m)
	} else if m.Val != testModDateTime {
		t.Errorf("mod val = %v, want %v", m.Val, testModDateTime)
	}
	if nt, ok := row["newTag"]; !ok || nt.Kind != KindMarker {
		t.Errorf("newTag = %+v, want Marker", nt)
	}
	if d, ok := row["dis"]; !ok || d.Kind != KindStr || d.Val != "Updated Name" {
		t.Errorf("dis = %+v, want Str Updated Name", d)
	}
	if ot, ok := row["oldTag"]; !ok || ot.Kind != KindRemove {
		t.Errorf("oldTag = %+v, want Remove", ot)
	}

	// Zinc 文本格式：diff 行应包含 mod 列（DateTime 编码 2026-08-27T06:00:00Z）
	if len(st.commitBodies) != 1 {
		t.Fatalf("commit bodies = %d, want 1", len(st.commitBodies))
	}
	body := st.commitBodies[0]
	if !strings.Contains(body, "mod") {
		t.Errorf("commit body = %q, want to contain mod column", body)
	}
	if !strings.Contains(body, testModDateTime) {
		t.Errorf("commit body = %q, want to contain mod datetime %s", body, testModDateTime)
	}
}

func TestCommitRemove(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	err := c.DeleteEntity(context.Background(), "@p:demo:r:p999", false)
	if err != nil {
		t.Fatalf("DeleteEntity: %v", err)
	}

	st.mu.Lock()
	defer st.mu.Unlock()

	// 客户端应先 Read 该记录取 mod，再提交 remove
	if len(st.readFilters) != 1 {
		t.Fatalf("read filters = %d, want 1 (auto-read for mod)", len(st.readFilters))
	}
	if f := st.readFilters[0]; f != "id==@p:demo:r:p999" {
		t.Errorf("read filter = %q, want id==@p:demo:r:p999", f)
	}

	if len(st.commitModes) != 1 || st.commitModes[0] != "remove" {
		t.Fatalf("commit modes = %v, want [remove]", st.commitModes)
	}
	if len(st.commitRows) != 1 {
		t.Fatalf("commit rows = %d, want 1", len(st.commitRows))
	}
	row := st.commitRows[0]
	if id, ok := row["id"]; !ok || id.Kind != KindRef || id.Val != "p:demo:r:p999" {
		t.Errorf("id = %+v, want Ref p:demo:r:p999", id)
	}
	// 关键：remove diff 行必须带 mod（真机 FIN 3.9 为 DateTime 类型）
	if m, ok := row["mod"]; !ok || m.Kind != KindDateTime {
		t.Errorf("mod = %+v, want DateTime (commit remove 必须带 mod 乐观锁)", m)
	} else if m.Val != testModDateTime {
		t.Errorf("mod val = %v, want %v", m.Val, testModDateTime)
	}
}

func TestDeleteEntityForceQueriesChildren(t *testing.T) {
	var mu sync.Mutex
	var readFilters []string
	srv, st := newFINServer(t)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		filter := r.URL.Query().Get("filter")
		mu.Lock()
		readFilters = append(readFilters, filter)
		mu.Unlock()
		if strings.HasPrefix(filter, "id==") {
			// 父记录 read：返回父记录带 mod（真机 FIN 3.9 为 DateTime）
			_, _ = io.WriteString(w, `{
				"meta": {},
				"cols": [{"name": "id"}, {"name": "mod"}],
				"rows": [{"id": {"_kind": "Ref", "val": "p:demo:r:ahu01"}, "mod": {"_kind": "DateTime", "val": "`+testModDateTime+`"}}]
			}`)
			return
		}
		// 子记录 read：返回子记录带各自 mod（DateTime）
		_, _ = io.WriteString(w, `{
			"meta": {},
			"cols": [{"name": "id"}, {"name": "mod"}],
			"rows": [{"id": {"_kind": "Ref", "val": "p:demo:r:p_child1"}, "mod": {"_kind": "DateTime", "val": "2026-08-27T06:00:01Z"}}]
		}`)
	})
	mux.HandleFunc("/api/demo/commit", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		raw, grid := readGridRequest(t, r, "text/zinc")
		mu.Lock()
		mode, _ := grid.Meta["commit"].(string)
		st.commitModes = append(st.commitModes, mode)
		st.commitBodies = append(st.commitBodies, raw)
		st.commitRows = append(st.commitRows, grid.Rows...)
		mu.Unlock()
		_, _ = io.WriteString(w, `{"meta":{},"cols":[],"rows":[]}`)
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	err := c.DeleteEntity(context.Background(), "@p:demo:r:ahu01", true)
	if err != nil {
		t.Fatalf("DeleteEntity force: %v", err)
	}

	mu.Lock()
	filters := readFilters
	modes := st.commitModes
	rows := st.commitRows
	mu.Unlock()

	// 客户端应先读父记录拿 mod，再查子记录
	if len(filters) != 2 {
		t.Fatalf("read filters = %v, want [parent-read child-query]", filters)
	}
	if filters[0] != "id==@p:demo:r:ahu01" {
		t.Errorf("parent read filter = %q, want id==@p:demo:r:ahu01", filters[0])
	}
	if filters[1] != "equipRef==@p:demo:r:ahu01" {
		t.Errorf("child filter = %q, want equipRef==@p:demo:r:ahu01", filters[1])
	}
	if len(modes) != 1 || modes[0] != "remove" {
		t.Errorf("commit modes = %v, want [remove]", modes)
	}
	if len(rows) != 2 {
		t.Fatalf("remove rows = %d, want 2 (parent + child)", len(rows))
	}
	// 父、子记录 remove diff 行都必须带 mod（真机 FIN 3.9 为 DateTime）
	for i, row := range rows {
		if id, ok := row["id"]; !ok || id.Kind != KindRef {
			t.Errorf("row %d id = %+v, want Ref", i, id)
		}
		if m, ok := row["mod"]; !ok || m.Kind != KindDateTime {
			t.Errorf("row %d mod = %+v, want DateTime (commit remove 必须带 mod)", i, m)
		}
	}
}

// TestUpdateEntityAutoReadsMod 覆盖"无 mod 输入时自动先读再提交"：
// UpdateEntity 入参不含 mod，客户端应自动先 Read 记录取 mod，再带 mod 提交 update。
// 这是 FIN/SkySpark commit update 乐观锁的硬性要求。
func TestUpdateEntityAutoReadsMod(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	if err := c.UpdateEntity(context.Background(), "@p:demo:r:p123", map[string]any{
		"foo": true,
	}, nil); err != nil {
		t.Fatalf("UpdateEntity: %v", err)
	}

	st.mu.Lock()
	defer st.mu.Unlock()

	// 1) 先 Read 拿 mod
	if len(st.readFilters) != 1 {
		t.Fatalf("read filters = %d, want 1 (auto-read for mod)", len(st.readFilters))
	}
	if f := st.readFilters[0]; f != "id==@p:demo:r:p123" {
		t.Errorf("read filter = %q, want id==@p:demo:r:p123", f)
	}
	// 2) 再 Commit update，diff 行带 mod
	if len(st.commitRows) != 1 {
		t.Fatalf("commit rows = %d, want 1", len(st.commitRows))
	}
	row := st.commitRows[0]
	m, ok := row["mod"]
	if !ok || m.Kind != KindDateTime || m.Val != testModDateTime {
		t.Errorf("mod = %+v, want DateTime %s (auto-read from record)", m, testModDateTime)
	}
	// 3) Zinc body 的 diff 行带 mod 列（DateTime 编码 2026-08-27T06:00:00Z）
	if len(st.commitBodies) != 1 || !strings.Contains(st.commitBodies[0], testModDateTime) {
		t.Errorf("commit body = %q, want to contain mod datetime %s", st.commitBodies[0], testModDateTime)
	}
}

// TestUpdateEntityRemoveTagsCarryMod 覆盖 remove_tags 的 diff 行同样带 mod：
// 同一行同时包含 update 标签与 remove 标签（R marker），两者都在带 mod 的行内。
func TestUpdateEntityRemoveTagsCarryMod(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	err := c.UpdateEntity(context.Background(), "@p:demo:r:p123", map[string]any{
		"keep": "yes",
	}, []string{"obsolete", "deprecated"})
	if err != nil {
		t.Fatalf("UpdateEntity: %v", err)
	}

	st.mu.Lock()
	defer st.mu.Unlock()
	if len(st.commitRows) != 1 {
		t.Fatalf("commit rows = %d, want 1", len(st.commitRows))
	}
	row := st.commitRows[0]
	// remove_tags 的 R 行与 update 行同属一个 diff 行，必须带 mod
	if m, ok := row["mod"]; !ok || m.Kind != KindDateTime || m.Val != testModDateTime {
		t.Errorf("mod = %+v, want DateTime %s", m, testModDateTime)
	}
	for _, name := range []string{"obsolete", "deprecated"} {
		if v, ok := row[name]; !ok || v.Kind != KindRemove {
			t.Errorf("%s = %+v, want Remove (remove_tags 行带 mod)", name, v)
		}
	}
}

// TestDeleteEntityMissingModError 覆盖读返回的记录没有 mod 时的错误路径：
// 客户端应返回可读错误，而不是继续发缺少 mod 的 remove commit。
func TestDeleteEntityMissingModError(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		// 记录没有 mod 列（旧库或异常数据）
		_, _ = io.WriteString(w, `{
			"meta": {},
			"cols": [{"name": "id"}],
			"rows": [{"id": {"_kind": "Ref", "val": "p:demo:r:p999"}}]
		}`)
	})
	mux.HandleFunc("/api/demo/commit", func(w http.ResponseWriter, r *http.Request) {
		t.Error("commit should not be called when record has no mod")
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	err := c.DeleteEntity(context.Background(), "@p:demo:r:p999", false)
	if err == nil {
		t.Fatal("expected error when record has no mod, got nil")
	}
	if !strings.Contains(err.Error(), "mod") {
		t.Errorf("error = %q, want to mention mod", err)
	}
}

// ----------------------------------------------------------------------------
// 6b. DateTime mod（真机 FIN 3.9 乐观锁）
// ----------------------------------------------------------------------------

// TestGetModAcceptsDateTime 验证 getMod 接受 DateTime 类型的 mod（真机 FIN 3.9），
// 并把原始值原样返回；同时兼容 Ref/Str/Number 兜底。
func TestGetModAcceptsDateTime(t *testing.T) {
	cases := []struct {
		name string
		mod  Val
	}{
		{"DateTime", Val{Kind: KindDateTime, Val: "2026-08-27T06:00:00Z"}},
		{"Ref", Val{Kind: KindRef, Val: "p:2026-08-27T12:00:00.000Z"}},
		{"Str", Val{Kind: KindStr, Val: "2026-08-27T06:00:00Z"}},
		{"Number", Val{Kind: KindNumber, Val: float64(12345)}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := getMod(map[string]Val{"mod": tc.mod})
			if err != nil {
				t.Fatalf("getMod(%v) error: %v", tc.mod.Kind, err)
			}
			if got.Kind != tc.mod.Kind || got.Val != tc.mod.Val {
				t.Errorf("getMod = %+v, want %+v (原样保留)", got, tc.mod)
			}
		})
	}

	// 不支持的类型仍报错
	if _, err := getMod(map[string]Val{"mod": Val{Kind: KindBool, Val: true}}); err == nil {
		t.Error("getMod(Bool) should error")
	} else if !strings.Contains(err.Error(), "want Ref/DateTime/Str/Number") {
		t.Errorf("getMod(Bool) error = %q, want to mention accepted kinds", err)
	}
}

// TestEncodeZincValDateTime 验证 encodeZincVal 把 DateTime 值编码为
// 未加引号的 Zinc DateTime 字面量（如 2026-08-27T06:00:00Z）。
func TestEncodeZincValDateTime(t *testing.T) {
	got := encodeZincVal(Val{Kind: KindDateTime, Val: "2026-08-27T06:00:00Z"})
	if got != "2026-08-27T06:00:00Z" {
		t.Errorf("encodeZincVal(DateTime) = %q, want raw DateTime literal", got)
	}
	got = encodeZincVal(Val{Kind: KindDateTime, Val: "2026-08-27T06:00:00.123+08:00"})
	if got != "2026-08-27T06:00:00.123+08:00" {
		t.Errorf("encodeZincVal(DateTime offset) = %q, want raw literal", got)
	}
}

// TestParseZincValueDateTime 验证 parseZincValue 能把 Zinc DateTime 字面量解析为
// KindDateTime（而不是被 Number 前缀误吞）。
func TestParseZincValueDateTime(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"2026-08-27T06:00:00Z", "2026-08-27T06:00:00Z"},
		{"2026-08-27T06:00:00.123+08:00", "2026-08-27T06:00:00.123+08:00"},
		{"2026-08-27T06:00:00 New_York", "2026-08-27T06:00:00 New_York"},
	}
	for _, tc := range cases {
		v := parseZincValue(tc.in)
		if v.Kind != KindDateTime {
			t.Errorf("parseZincValue(%q) kind = %v, want DateTime", tc.in, v.Kind)
		}
		if v.Val != tc.want {
			t.Errorf("parseZincValue(%q) val = %v, want %q", tc.in, v.Val, tc.want)
		}
	}
}

// TestUpdateEntityDateTimeModRoundTrip 覆盖真机 FIN 3.9 场景：read 返回 Zinc 文本且
// mod 列为 DateTime（如 2026-08-27T06:00:00Z），UpdateEntity 应先读记录，再把
// mod 原样编码回 commit update 的 Zinc diff 行（DateTime 不加引号）。
func TestUpdateEntityDateTimeModRoundTrip(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	var mu sync.Mutex
	var commitBodies []string
	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\"\n"+
			"id,mod,dis\n"+
			"@p:demo:r:p456,2026-08-27T06:00:00Z,\"P456\"\n")
	})
	mux.HandleFunc("/api/demo/commit", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		raw, grid := readGridRequest(t, r, "text/zinc")
		mu.Lock()
		commitBodies = append(commitBodies, raw)
		mu.Unlock()
		mode, _ := grid.Meta["commit"].(string)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"meta":{"commit":"`+mode+`"},"cols":[],"rows":[]}`)
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	if err := c.UpdateEntity(context.Background(), "@p:demo:r:p456", map[string]any{
		"foo": true,
	}, nil); err != nil {
		t.Fatalf("UpdateEntity with DateTime mod: %v", err)
	}

	mu.Lock()
	bodies := append([]string(nil), commitBodies...)
	mu.Unlock()

	if len(bodies) != 1 {
		t.Fatalf("commit bodies = %d, want 1", len(bodies))
	}
	body := bodies[0]
	// Zinc body 里 mod 值必须是不带引号的 DateTime 字面量（不能被 .0 或引号破坏）
	if !strings.Contains(body, "2026-08-27T06:00:00Z") {
		t.Errorf("commit body = %q, want to contain raw DateTime mod %s", body, "2026-08-27T06:00:00Z")
	}
	if strings.Contains(body, `"2026-08-27T06:00:00Z"`) {
		t.Errorf("commit body = %q, DateTime mod must not be quoted", body)
	}
	if strings.Contains(body, "2026.0-08-27") {
		t.Errorf("commit body = %q, DateTime mod must not be mis-encoded as Number with unit", body)
	}
}

// ----------------------------------------------------------------------------
// 7. eval 安全检查
// ----------------------------------------------------------------------------

func TestEvalSafetyRejectsWriteKeywords(t *testing.T) {
	srv, st := newFINServer(t)
	defer srv.Close()
	c := newTestClient(t, srv)

	bad := []string{
		"commitAdd(...)",
		"readAll(point).commit",
		"purgeAll()",
		"ioWriteZinc(...)",
		"remove(...)",
		"install(...)",
		"uninstall(...)",
		"delete(...)",
	}
	for _, expr := range bad {
		if _, err := c.Eval(context.Background(), expr); err == nil {
			t.Errorf("Eval(%q) should be rejected", expr)
		} else if !strings.Contains(err.Error(), "forbidden write keyword") {
			t.Errorf("Eval(%q) error = %v, want forbidden write keyword", expr, err)
		}
	}

	// 安全表达式应通过；校验 Zinc body（Content-Type text/zinc + 内容）
	grid, err := c.Eval(context.Background(), "readAll(point)")
	if err != nil {
		t.Fatalf("Eval(readAll(point)): %v", err)
	}
	if grid == nil || len(grid.Rows) != 1 {
		t.Errorf("eval grid = %+v, want 1 row", grid)
	}

	st.mu.Lock()
	defer st.mu.Unlock()
	if len(st.evalBodies) != 1 {
		t.Fatalf("eval bodies = %d, want 1", len(st.evalBodies))
	}
	wantRaw := "ver:\"3.0\"\nexpr\n\"readAll(point)\"\n\n"
	if raw := st.evalBodies[0]; raw != wantRaw {
		t.Errorf("eval body = %q, want %q", raw, wantRaw)
	}
	if len(st.evalGrids) != 1 || len(st.evalGrids[0].Cols) != 1 || st.evalGrids[0].Cols[0].Name != "expr" {
		t.Errorf("eval grid cols = %+v, want [expr]", st.evalGrids[0].Cols)
	}
	if len(st.evalGrids[0].Rows) != 1 || st.evalGrids[0].Rows[0]["expr"].Val != "readAll(point)" {
		t.Errorf("eval grid rows = %+v, want expr=readAll(point)", st.evalGrids[0].Rows)
	}
}

// ----------------------------------------------------------------------------
// 8. 超时熔断
// ----------------------------------------------------------------------------

func TestTimeoutCircuitBreaker(t *testing.T) {
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 无论哪个端点都延迟，超过客户端超时
		time.Sleep(500 * time.Millisecond)
		_, _ = io.WriteString(w, `{"meta":{},"cols":[],"rows":[]}`)
	}))
	defer slow.Close()

	c, err := NewClient(Config{
		URL:      slow.URL + "/api/demo",
		AuthType: AuthTypeBasic, // 跳过 SCRAM，直接触发 read 超时
		Username: testUsername,
		Password: testPassword,
		Timeout:  100 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	start := time.Now()
	_, err = c.Read(context.Background(), "point", 10)
	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Errorf("timeout too slow: %v", elapsed)
	}
	var te *ErrTimeout
	if !errors.As(err, &te) {
		t.Errorf("expected *ErrTimeout, got %T: %v", err, err)
	}
}

// ----------------------------------------------------------------------------
// basic 鉴权
// ----------------------------------------------------------------------------

func TestBasicAuth(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0"},
			"cols": [{"name": "finVersion"}, {"name": "serverName"}],
			"rows": [{
				"finVersion": {"_kind": "Str", "val": "5.3"},
				"serverName": {"_kind": "Str", "val": "basic-demo"}
			}]
		}`)
	}))
	defer srv.Close()

	c, err := NewClient(Config{
		URL:      srv.URL + "/api/demo",
		AuthType: AuthTypeBasic,
		Username: "su",
		Password: "secret",
		Timeout:  2 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	info, err := c.About(context.Background())
	if err != nil {
		t.Fatalf("About basic: %v", err)
	}
	if info.ServerName != "basic-demo" {
		t.Errorf("ServerName = %q, want basic-demo", info.ServerName)
	}
	want := "Basic " + base64.StdEncoding.EncodeToString([]byte("su:secret"))
	if gotAuth != want {
		t.Errorf("Authorization = %q, want %q", gotAuth, want)
	}
}

// ----------------------------------------------------------------------------
// 9. FIN 业务错误网格（HTTP 200 + grid meta.err marker）
// ----------------------------------------------------------------------------

// TestPointWriteReadOnlyErrorGrid 覆盖真机 FIN 3.9 场景：对只读点 pointWrite，
// FIN 返回 HTTP 200 + Zinc error grid（meta 带 err marker），客户端必须检测并返回
// 含 dis/errType/errTrace 的可读错误。
func TestPointWriteReadOnlyErrorGrid(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/pointWrite", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\" err errType:\"writeAccess\" errTrace:\"java.lang.SecurityException: read-only\" dis:\"point is read-only\"\n")
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	err := c.PointWrite(context.Background(), "@p:demo:r:p123", 8, NewNumberVal(42), 0)
	if err == nil {
		t.Fatal("expected error for read-only pointWrite, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "point is read-only") {
		t.Errorf("error = %q, want to contain dis 'point is read-only'", msg)
	}
	if !strings.Contains(msg, "errType=writeAccess") {
		t.Errorf("error = %q, want to contain errType=writeAccess", msg)
	}
	if !strings.Contains(msg, "errTrace=") {
		t.Errorf("error = %q, want to contain errTrace", msg)
	}
}

// TestEvalErrorGridJSON 覆盖 FIN 业务错误以 JSON error grid（HTTP 200）返回的场景。
func TestEvalErrorGridJSON(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/eval", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{
			"meta": {"ver": "3.0", "err": {}, "errType": "evalErr", "errTrace": "axon stack trace", "dis": "unknown id 'foo'"},
			"cols": [],
			"rows": []
		}`)
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	_, err := c.Eval(context.Background(), "readById(@foo)")
	if err == nil {
		t.Fatal("expected error for eval error grid, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "unknown id 'foo'") {
		t.Errorf("error = %q, want to contain dis 'unknown id foo'", msg)
	}
	if !strings.Contains(msg, "errType=evalErr") {
		t.Errorf("error = %q, want to contain errType=evalErr", msg)
	}
	if !strings.Contains(msg, "errTrace=axon stack trace") {
		t.Errorf("error = %q, want to contain errTrace", msg)
	}
}

// TestCommitErrorGrid 覆盖 commit 业务错误（HTTP 200 + Zinc error grid）。
func TestCommitErrorGrid(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/commit", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\" err errType:\"notFound\" errTrace:\"...\" dis:\"record not found\"\n")
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	_, err := c.Commit(context.Background(), CommitUpdate, []map[string]Val{
		{"id": NewRefVal("@p:demo:r:missing")},
	})
	if err == nil {
		t.Fatal("expected error for commit error grid, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "record not found") {
		t.Errorf("error = %q, want to contain dis 'record not found'", msg)
	}
	if !strings.Contains(msg, "errType=notFound") {
		t.Errorf("error = %q, want to contain errType=notFound", msg)
	}
}

// TestReadErrorGrid 覆盖 read 通过 callGrid 检测业务错误（HTTP 200 + Zinc error grid）。
func TestReadErrorGrid(t *testing.T) {
	srv := newSCRAMServer(t, false, nil)
	defer srv.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/demo/about", srv.Config.Handler.ServeHTTP)
	mux.HandleFunc("/api/demo/read", func(w http.ResponseWriter, r *http.Request) {
		if !requireBearer(t, r) {
			return
		}
		w.Header().Set("Content-Type", "text/zinc")
		_, _ = io.WriteString(w, "ver:\"3.0\" err errType:\"badFilter\" errTrace:\"filter parse error\" dis:\"invalid filter syntax\"\n")
	})
	srv.Config.Handler = mux
	c := newTestClient(t, srv)

	_, err := c.Read(context.Background(), "siteRef==@[bad", 10)
	if err == nil {
		t.Fatal("expected error for read error grid, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "invalid filter syntax") {
		t.Errorf("error = %q, want to contain dis 'invalid filter syntax'", msg)
	}
	if !strings.Contains(msg, "errType=badFilter") {
		t.Errorf("error = %q, want to contain errType=badFilter", msg)
	}
}

// TestGridErrZincMetaMarker 直接验证 parseZincMeta 对错误网格 meta 行（含裸 err marker）的解析。
func TestGridErrZincMetaMarker(t *testing.T) {
	hg, err := parseGrid([]byte("ver:\"3.0\" err errType:\"writeAccess\" dis:\"read-only\"\n"), "text/zinc")
	if err != nil {
		t.Fatalf("parseGrid error grid: %v", err)
	}
	if v, ok := hg.Meta["err"]; !ok || v != "M" {
		t.Errorf("meta.err = %v, want Marker \"M\"", v)
	}
	if v, ok := hg.Meta["errType"]; !ok || v != "writeAccess" {
		t.Errorf("meta.errType = %v, want writeAccess", v)
	}
	if err := gridErr(hg); err == nil {
		t.Error("gridErr should detect err marker")
	} else if !strings.Contains(err.Error(), "read-only") {
		t.Errorf("gridErr = %q, want to contain dis 'read-only'", err.Error())
	}
}

// ----------------------------------------------------------------------------
// b64urlDecode 兼容性
// ----------------------------------------------------------------------------

func TestB64URLDecodeCompat(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"urlsafe unpadded with -_", "aGVsbG8td29ybGRfIQ", "hello-world_!"},
		{"urlsafe padded", "aGVsbG8td29ybGQ=", "hello-world"},
		{"standard unpadded with +/", "+////////////////////w", "\xfb\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"},
		{"standard padded", "rL3h5K/Tw6n/2A==", "\xac\xbd\xe1\xe4\xaf\xd3\xc3\xa9\xff\xd8"},
		{"mixed -_ and standard", "aGVsbG8td29ybGQ", "hello-world"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := b64urlDecode(tc.in)
			if err != nil {
				t.Fatalf("b64urlDecode(%q): %v", tc.in, err)
			}
			if string(got) != tc.want {
				t.Errorf("b64urlDecode(%q) = %q, want %q", tc.in, string(got), tc.want)
			}
		})
	}
}
