// SPDX-License-Identifier: MIT
// One replica only: activation is serialized; credentials never reach clients.
package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"
)

type object = map[string]any

const marker = "managed-by=hardfest-webui-access; user-id="
const reserveMarker = "managed-by=hardfest-webui-access; reserve"

var userIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]{8,80}$`)

type Config struct {
	WebUIURL          string   `json:"webui_url"`
	GatewayURL        string   `json:"gateway_url"`
	ServiceUserID     string   `json:"service_user_id"`
	ChatModels        []string `json:"chat_models"`
	AllowedModels     []string `json:"allowed_models"`
	ProviderKeyIDs    []string `json:"provider_key_ids"`
	PriceModels       []string `json:"price_models"`
	BudgetUSD         float64  `json:"budget_usd"`
	BudgetReset       string   `json:"budget_reset"`
	InputPrice        float64  `json:"input_usd_per_million_tokens"`
	OutputPrice       float64  `json:"output_usd_per_million_tokens"`
	RequestsPerMinute int      `json:"requests_per_minute"`
	TokensPerMinute   int      `json:"tokens_per_minute"`
	MaxConcurrent     int      `json:"max_concurrent_total"`
}

type User struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Role string `json:"role"`
}

type credential struct{ ID, Value string }

// One process owns reconciliation. Do not scale without a distributed lock.
type Bridge struct {
	cfg                                               Config
	client                                            *http.Client
	streamClient                                      *http.Client
	webUIKey, managementKey, transportKey, signingKey string
	mu                                                sync.Mutex
	keys                                              map[string]credential
	activeMu                                          sync.Mutex
	activeUsers                                       map[string]bool
	activeTotal                                       int
	now                                               func() time.Time
}

func (b *Bridge) api(ctx context.Context, base, key, method, path string, body any, out any) error {
	var data []byte
	var err error
	if body != nil {
		data, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(data))
	if err != nil {
		return errors.New("invalid API request")
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := b.client.Do(req)
	if err != nil {
		return errors.New("API unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("API returned HTTP %d", resp.StatusCode)
	}
	if out != nil {
		return json.NewDecoder(io.LimitReader(resp.Body, 16<<20)).Decode(out)
	}
	return nil
}

func (b *Bridge) gateway(ctx context.Context, method, path string, body, out any) error {
	return b.api(ctx, b.cfg.GatewayURL, b.managementKey, method, path, body, out)
}

func (b *Bridge) user(ctx context.Context, id string) (User, error) {
	var u User
	if !userIDPattern.MatchString(id) {
		return u, errors.New("invalid subject")
	}
	err := b.api(ctx, b.cfg.WebUIURL, b.webUIKey, "GET", "/api/v1/users/"+url.PathEscape(id), nil, &u)
	if err == nil && (u.ID != id || id == b.cfg.ServiceUserID || (u.Role != "user" && u.Role != "admin")) {
		err = errors.New("user is not approved")
	}
	return u, err
}

func (b *Bridge) subject(token string) (string, error) {
	if len(token) > 8192 {
		return "", errors.New("invalid identity")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", errors.New("invalid identity")
	}
	var header struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
	}
	hd, e1 := base64.RawURLEncoding.DecodeString(parts[0])
	pl, e2 := base64.RawURLEncoding.DecodeString(parts[1])
	sig, e3 := base64.RawURLEncoding.DecodeString(parts[2])
	if e1 != nil || e2 != nil || e3 != nil || json.Unmarshal(hd, &header) != nil || header.Alg != "HS256" {
		return "", errors.New("invalid identity")
	}
	mac := hmac.New(sha256.New, []byte(b.signingKey))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return "", errors.New("invalid identity")
	}
	var claims struct {
		Sub string `json:"sub"`
		Iss string `json:"iss"`
		Exp int64  `json:"exp"`
		Iat int64  `json:"iat"`
	}
	if json.Unmarshal(pl, &claims) != nil {
		return "", errors.New("invalid identity")
	}
	now := b.now().Unix()
	if claims.Iss != "open-webui" || !userIDPattern.MatchString(claims.Sub) || claims.Exp <= now || claims.Iat > now+15 || claims.Iat <= 0 || claims.Exp-claims.Iat > 300 || claims.Exp <= claims.Iat {
		return "", errors.New("invalid identity")
	}
	return claims.Sub, nil
}

func stringValue(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if m, ok := v.(map[string]any); ok {
		s, _ := m["value"].(string)
		return s
	}
	return ""
}

func (b *Bridge) listKeys(ctx context.Context) ([]object, error) {
	var keys []object
	for page := 0; page < 100; page++ {
		var out struct {
			Keys  []object `json:"virtual_keys"`
			Total int      `json:"total_count"`
		}
		if err := b.gateway(ctx, "GET", fmt.Sprintf("/api/governance/virtual-keys?limit=100&offset=%d", len(keys)), nil, &out); err != nil {
			return nil, err
		}
		keys = append(keys, out.Keys...)
		if len(keys) == out.Total {
			return keys, nil
		}
		if len(keys) > out.Total || len(out.Keys) == 0 {
			return nil, errors.New("incomplete key list")
		}
	}
	return nil, errors.New("key list too large")
}

func (b *Bridge) newKey(ctx context.Context, u User) (object, error) {
	keys, err := b.listKeys(ctx)
	if err != nil {
		return nil, err
	}
	var selected object
	for _, k := range keys {
		if k["description"] == reserveMarker && k["is_active"] == false {
			if err := b.validateKey(k); err != nil {
				continue
			}
			selected = k
			break
		}
	}
	if selected == nil {
		return nil, errors.New("disabled personal key reserve exhausted")
	}
	// Do not reset budgets, rates or usage while assigning/reconciling a key.
	body := object{"name": "HardFest: " + u.Name + " (" + u.ID + ")", "description": marker + u.ID}
	var out struct {
		Key object `json:"virtual_key"`
	}
	err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(stringValue(selected["id"])), body, &out)
	return out.Key, err
}

// Pricing is attached to the personal VK, never a global model override.
func (b *Bridge) ensurePricing(ctx context.Context, id string) error {
	var out struct {
		Prices []object `json:"pricing_overrides"`
		Total  int      `json:"total_count"`
	}
	if err := b.gateway(ctx, "GET", "/api/governance/pricing-overrides?limit=100&virtual_key_id="+url.QueryEscape(id), nil, &out); err != nil {
		return err
	}
	if out.Total > len(out.Prices) {
		return errors.New("incomplete pricing list")
	}
	for _, model := range b.cfg.PriceModels {
		name := "HardFest " + id + " " + model
		var found []object
		for _, p := range out.Prices {
			if p["name"] == name {
				found = append(found, p)
			}
		}
		if len(found) > 1 {
			return errors.New("ambiguous price")
		}
		patch := object{"input_cost_per_token": b.cfg.InputPrice / 1e6, "output_cost_per_token": b.cfg.OutputPrice / 1e6}
		if len(found) == 1 {
			p := found[0]
			var cost object
			if err := json.Unmarshal([]byte(stringValue(p["pricing_patch"])), &cost); err != nil {
				return errors.New("invalid pricing patch")
			}
			// Installed Bifrost omits enabled; an explicit false still fails closed.
			if p["virtual_key_id"] != id || p["scope_kind"] != "virtual_key" || p["pattern"] != model || p["match_type"] != "exact" || cost["input_cost_per_token"] != patch["input_cost_per_token"] || cost["output_cost_per_token"] != patch["output_cost_per_token"] || p["enabled"] == false || !sameStrings(p["request_types"], []string{"chat_completion"}) {
				return errors.New("pricing drift")
			}
			continue
		}
		// Pricing API accepts the base type: chat_completion covers SSE too.
		body := object{"name": name, "scope_kind": "virtual_key", "virtual_key_id": id, "match_type": "exact", "pattern": model, "request_types": []string{"chat_completion"}, "patch": patch}
		if err := b.gateway(ctx, "POST", "/api/governance/pricing-overrides", body, nil); err != nil {
			return err
		}
	}
	return nil
}

func sameStrings(value any, expected []string) bool {
	encoded, err := json.Marshal(value)
	if err != nil {
		return false
	}
	var actual []string
	if json.Unmarshal(encoded, &actual) != nil {
		return false
	}
	want := slices.Clone(expected)
	slices.Sort(actual)
	slices.Sort(want)
	return slices.Equal(actual, want)
}

func (b *Bridge) validateKey(key object) error {
	var v struct {
		Providers []struct {
			Provider      string   `json:"provider"`
			AllowedModels []string `json:"allowed_models"`
			AllKeys       bool     `json:"allow_all_keys"`
			Keys          []struct {
				KeyID string `json:"key_id"`
			} `json:"keys"`
		} `json:"provider_configs"`
		MCP     []object `json:"mcp_configs"`
		Budgets []struct {
			Max   float64 `json:"max_limit"`
			Reset string  `json:"reset_duration"`
		} `json:"budgets"`
		Rate struct {
			Requests     int    `json:"request_max_limit"`
			Tokens       int    `json:"token_max_limit"`
			RequestReset string `json:"request_reset_duration"`
			TokenReset   string `json:"token_reset_duration"`
		} `json:"rate_limit"`
	}
	raw, err := json.Marshal(key)
	if err != nil || json.Unmarshal(raw, &v) != nil {
		return errors.New("invalid key policy")
	}
	if len(v.Providers) != 1 || v.Providers[0].Provider != "vllm" || v.Providers[0].AllKeys || len(v.MCP) != 0 {
		return errors.New("unexpected key access")
	}
	var ids []string
	for _, k := range v.Providers[0].Keys {
		ids = append(ids, k.KeyID)
	}
	if !sameStrings(v.Providers[0].AllowedModels, b.cfg.AllowedModels) || !sameStrings(ids, b.cfg.ProviderKeyIDs) {
		return errors.New("key model policy drift")
	}
	if len(v.Budgets) != 1 || v.Budgets[0].Max != b.cfg.BudgetUSD || v.Budgets[0].Reset != b.cfg.BudgetReset {
		return errors.New("budget policy drift")
	}
	if v.Rate.Requests != b.cfg.RequestsPerMinute || v.Rate.Tokens != b.cfg.TokensPerMinute || v.Rate.RequestReset != "60s" || v.Rate.TokenReset != "60s" {
		return errors.New("rate policy drift")
	}
	return nil
}

func (b *Bridge) ensureKey(ctx context.Context, u User) (credential, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if key, ok := b.keys[u.ID]; ok {
		return key, nil
	}
	keys, err := b.listKeys(ctx)
	if err != nil {
		return credential{}, err
	}
	var found []object
	for _, k := range keys {
		if k["description"] == marker+u.ID {
			found = append(found, k)
		}
	}
	if len(found) > 1 {
		return credential{}, errors.New("ambiguous personal key")
	}
	var key object
	if len(found) == 1 {
		key = found[0]
	} else {
		key, err = b.newKey(ctx, u)
		if err != nil {
			return credential{}, err
		}
	}
	id := stringValue(key["id"])
	value := stringValue(key["value"])
	if id == "" || value == "" {
		return credential{}, errors.New("key response is incomplete")
	}
	if mcp, ok := key["mcp_configs"].([]any); ok && len(mcp) != 0 {
		return credential{}, errors.New("unexpected MCP permission")
	}
	if err := b.validateKey(key); err != nil {
		return credential{}, err
	}
	if err = b.ensurePricing(ctx, id); err != nil {
		return credential{}, err
	}
	if key["is_active"] != true {
		if err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(id), object{"is_active": true}, nil); err != nil {
			return credential{}, err
		}
	}
	result := credential{id, value}
	b.keys[u.ID] = result
	return result, nil
}

func (b *Bridge) users(ctx context.Context) ([]User, error) {
	var users []User
	total := -1
	for page := 1; page <= 100; page++ {
		var out struct {
			Users []User `json:"users"`
			Total int    `json:"total"`
		}
		if err := b.api(ctx, b.cfg.WebUIURL, b.webUIKey, "GET", fmt.Sprintf("/api/v1/users/?page=%d&order_by=id&direction=asc", page), nil, &out); err != nil {
			return nil, err
		}
		if total != -1 && total != out.Total {
			return nil, errors.New("user list changed during read")
		}
		total = out.Total
		users = append(users, out.Users...)
		if len(users) == total {
			return users, nil
		}
		if len(users) > total || len(out.Users) == 0 {
			return nil, errors.New("incomplete user list")
		}
	}
	return nil, errors.New("user list too large")
}

func (b *Bridge) reconcile(ctx context.Context) error {
	users, err := b.users(ctx)
	if err != nil {
		return err
	}
	approved := map[string]User{}
	for _, u := range users {
		if userIDPattern.MatchString(u.ID) && u.ID != b.cfg.ServiceUserID && (u.Role == "user" || u.Role == "admin") {
			approved[u.ID] = u
		}
	}
	// Invalidate the process cache before reviewing native key state.
	b.mu.Lock()
	b.keys = map[string]credential{}
	b.mu.Unlock()
	keys, err := b.listKeys(ctx)
	if err != nil {
		return err
	}
	for _, k := range keys {
		desc := stringValue(k["description"])
		if !strings.HasPrefix(desc, marker) {
			continue
		}
		id := strings.TrimPrefix(desc, marker)
		if _, ok := approved[id]; !ok && k["is_active"] == true {
			if err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(stringValue(k["id"])), object{"is_active": false}, nil); err != nil {
				return err
			}
		}
	}
	for _, u := range approved {
		// Recheck immediately before issuance: approval may have changed during the scan.
		current, err := b.user(ctx, u.ID)
		if err != nil {
			continue
		}
		if _, err = b.ensureKey(ctx, current); err != nil {
			return err
		}
	}
	return nil
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

func (b *Bridge) acquire(id string) bool {
	b.activeMu.Lock()
	defer b.activeMu.Unlock()
	if b.activeUsers[id] || b.activeTotal >= b.cfg.MaxConcurrent {
		return false
	}
	b.activeUsers[id] = true
	b.activeTotal++
	return true
}
func (b *Bridge) release(id string) {
	b.activeMu.Lock()
	defer b.activeMu.Unlock()
	delete(b.activeUsers, id)
	b.activeTotal--
}

func fail(w http.ResponseWriter, code int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(object{"error": object{"message": message, "type": "access_policy"}})
}

func (b *Bridge) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" && r.URL.Path == "/healthz" {
		w.WriteHeader(200)
		return
	}
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+b.transportKey)) != 1 {
		fail(w, 401, "invalid service authentication")
		return
	}
	if r.Method == "GET" && r.URL.Path == "/v1/models" {
		models := []object{}
		for _, id := range b.cfg.ChatModels {
			models = append(models, object{"id": id, "object": "model", "owned_by": "hardfest"})
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(object{"object": "list", "data": models})
		return
	}
	// This ingress is not a generic gateway: no MCP, management API, URL override or fallbacks.
	if r.Method != "POST" || r.URL.Path != "/v1/chat/completions" || r.URL.RawQuery != "" {
		fail(w, 403, "endpoint is not enabled")
		return
	}
	if len(r.Header.Values("X-OpenWebUI-User-Jwt")) != 1 {
		fail(w, 401, "signed user identity required")
		return
	}
	id, err := b.subject(r.Header.Get("X-OpenWebUI-User-Jwt"))
	if err != nil {
		fail(w, 401, "invalid user identity")
		return
	}
	u, err := b.user(r.Context(), id)
	if err != nil {
		fail(w, 403, "user not approved or identity service unavailable")
		return
	}
	var body object
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4<<20))
	if err = decoder.Decode(&body); err != nil || body == nil {
		fail(w, 400, "invalid request")
		return
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		fail(w, 400, "invalid request")
		return
	}
	model, ok := body["model"].(string)
	if !ok || !contains(b.cfg.ChatModels, model) {
		fail(w, 403, "model not allowed")
		return
	}
	for _, field := range []string{"tools", "functions", "tool_choice", "function_call", "fallbacks", "provider", "key_id", "mcp_tools", "mcp_servers"} {
		if _, ok = body[field]; ok {
			fail(w, 403, "request capability not allowed")
			return
		}
	}
	if _, ok = body["messages"].([]any); !ok {
		fail(w, 400, "messages required")
		return
	}
	// An allowlist also excludes future gateway control fields supplied in the body.
	allowed := []string{"model", "messages", "max_tokens", "max_completion_tokens", "stream", "stream_options", "temperature", "top_p", "top_k", "seed", "stop", "presence_penalty", "frequency_penalty", "repetition_penalty", "chat_template_kwargs", "response_format"}
	clean := object{}
	for _, field := range allowed {
		if v, ok := body[field]; ok {
			clean[field] = v
		}
	}
	clean["user"] = id
	if !b.acquire(id) {
		w.Header().Set("Retry-After", "5")
		fail(w, 429, "concurrency limit reached")
		return
	}
	defer b.release(id)
	key, err := b.ensureKey(r.Context(), u)
	if err != nil {
		log.Printf("personal access unavailable user=%s: %v", u.ID, err)
		fail(w, 503, "personal access is not ready")
		return
	}
	data, _ := json.Marshal(clean)
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Minute)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "POST", b.cfg.GatewayURL+"/v1/chat/completions", bytes.NewReader(data))
	req.Header.Set("Authorization", "Bearer "+key.Value)
	req.Header.Set("x-bf-vk", key.Value)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-bf-mcp-include-tools", "")
	req.Header.Set("x-bf-cache-key", "hardfest-personal-"+id)
	req.Header.Set("x-bf-cache-type", "direct")
	req.Header.Set("x-bf-cache-no-store", "true")
	resp, err := b.streamClient.Do(req)
	if err != nil {
		fail(w, 502, "model gateway unavailable")
		return
	}
	defer resp.Body.Close()
	for _, h := range []string{"Content-Type", "Retry-After"} {
		if v := resp.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(resp.StatusCode)
	buf := make([]byte, 32<<10)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			if _, e := w.Write(buf[:n]); e != nil {
				return
			}
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
		}
		if err != nil {
			return
		}
	}
}

func main() {
	configData, err := os.ReadFile("/config/config.json")
	if err != nil {
		log.Fatal("configuration unavailable")
	}
	var cfg Config
	if json.Unmarshal(configData, &cfg) != nil || cfg.WebUIURL == "" || cfg.GatewayURL == "" || cfg.ServiceUserID == "" || len(cfg.ChatModels) != 2 || len(cfg.ProviderKeyIDs) == 0 || cfg.MaxConcurrent < 1 || cfg.BudgetUSD <= 0 || cfg.InputPrice <= 0 || cfg.OutputPrice <= 0 {
		log.Fatal("invalid configuration")
	}
	for _, base := range []string{cfg.WebUIURL, cfg.GatewayURL} {
		u, e := url.Parse(base)
		if e != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || (u.Scheme != "https" && u.Scheme != "http") {
			log.Fatal("invalid API origin")
		}
	}
	if !strings.HasPrefix(cfg.GatewayURL, "https://") {
		log.Fatal("gateway TLS required")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	noRedirect := func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	b := &Bridge{cfg: cfg, client: &http.Client{Timeout: 20 * time.Second, Transport: transport, CheckRedirect: noRedirect}, streamClient: &http.Client{Transport: transport, CheckRedirect: noRedirect}, keys: map[string]credential{}, activeUsers: map[string]bool{}, now: time.Now,
		webUIKey: os.Getenv("WEBUI_ADMIN_API_KEY"), managementKey: os.Getenv("BIFROST_MANAGEMENT_KEY"), transportKey: os.Getenv("WEBUI_TRANSPORT_KEY"), signingKey: os.Getenv("FORWARD_USER_INFO_HEADER_JWT_SECRET")}
	for _, s := range []string{b.webUIKey, b.managementKey, b.transportKey, b.signingKey} {
		if len(s) < 32 {
			log.Fatal("required credential unavailable")
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		for {
			run, cancel := context.WithTimeout(ctx, 2*time.Minute)
			err := b.reconcile(run)
			cancel()
			if err != nil {
				log.Printf("participant reconciliation failed: %v", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(15 * time.Second):
			}
		}
	}()
	server := &http.Server{Addr: ":8080", Handler: b, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	if err = server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatal("HTTP listener failed")
	}
}
