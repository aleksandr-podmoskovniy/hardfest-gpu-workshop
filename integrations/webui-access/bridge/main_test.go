// SPDX-License-Identifier: MIT
// Local-runtime tests, not live Bifrost acceptance evidence.
package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const subjectID = "test-user-12345678"

func jwtFor(b *Bridge, claims object) string {
	h := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	p, _ := json.Marshal(claims)
	data := h + "." + base64.RawURLEncoding.EncodeToString(p)
	m := hmac.New(sha256.New, []byte(b.signingKey))
	m.Write([]byte(data))
	return data + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}
func validClaims(b *Bridge) object {
	return object{"sub": subjectID, "iss": "open-webui", "iat": b.now().Unix(), "exp": b.now().Unix() + 60, "role": "admin"}
}

func TestForgedIdentityDenied(t *testing.T) {
	b := &Bridge{signingKey: strings.Repeat("s", 32), now: time.Now}
	for _, tc := range []struct {
		name   string
		change func(object)
	}{
		{"issuer", func(c object) { c["iss"] = "attacker" }},
		{"expired", func(c object) { c["exp"] = b.now().Unix() }},
		{"future", func(c object) { c["iat"] = b.now().Unix() + 30 }},
		{"too-long", func(c object) { c["exp"] = b.now().Unix() + 301 }},
		{"subject-path", func(c object) { c["sub"] = "../../admin" }},
		{"empty-subject", func(c object) { delete(c, "sub") }},
		{"missing-iat", func(c object) { delete(c, "iat") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := validClaims(b)
			tc.change(c)
			if _, err := b.subject(jwtFor(b, c)); err == nil {
				t.Fatal("accepted invalid identity")
			}
		})
	}
	token := jwtFor(b, validClaims(b))
	if id, err := b.subject(token); err != nil || id != subjectID {
		t.Fatal("rejected valid identity")
	}
	if _, err := b.subject(token + "x"); err == nil {
		t.Fatal("accepted forged signature")
	}
}

func fixture(t *testing.T, role string) (*Bridge, *int) {
	t.Helper()
	calls := new(int)
	ui := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer admin-test" {
			t.Error("wrong user lookup credential")
		}
		_ = json.NewEncoder(w).Encode(User{ID: subjectID, Name: "Test", Role: role})
	}))
	t.Cleanup(ui.Close)
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*calls++
		if r.URL.Path != "/v1/chat/completions" {
			t.Error("unexpected gateway endpoint")
		}
		if r.Header.Get("Authorization") != "Bearer personal-key" || r.Header.Get("x-bf-vk") != "personal-key" {
			t.Error("wrong personal key")
		}
		if r.Header.Get("X-Bf-Key-Id") != "" || r.Header.Get("X-OpenWebUI-User-Jwt") != "" {
			t.Error("caller control or identity leaked")
		}
		if r.Header.Get("X-Bf-Cache-No-Store") != "true" {
			t.Error("cache store not disabled")
		}
		var req object
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req["user"] != subjectID {
			t.Error("wrong user attribution")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: {\"choices\":[]}\n\ndata: [DONE]\n\n")
	}))
	t.Cleanup(gw.Close)
	b := &Bridge{cfg: Config{WebUIURL: ui.URL, GatewayURL: gw.URL, ChatModels: []string{"vllm/a", "vllm/b"}, MaxConcurrent: 8}, client: ui.Client(), streamClient: gw.Client(), webUIKey: "admin-test", transportKey: "transport-test", signingKey: strings.Repeat("s", 32), now: time.Now, keys: map[string]credential{subjectID: {"key-id", "personal-key"}}, activeUsers: map[string]bool{}}
	return b, calls
}
func request(b *Bridge, path, body string) *http.Request {
	r := httptest.NewRequest("POST", path, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer transport-test")
	r.Header.Set("X-OpenWebUI-User-Jwt", jwtFor(b, validClaims(b)))
	return r
}
func TestPendingUserDenied(t *testing.T) {
	b, calls := fixture(t, "pending")
	w := httptest.NewRecorder()
	b.ServeHTTP(w, request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[]}`))
	if w.Code != 403 || *calls != 0 {
		t.Fatal("pending user reached model")
	}
}
func TestStreamAndIdentityPreserved(t *testing.T) {
	b, calls := fixture(t, "user")
	w := httptest.NewRecorder()
	r := request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[],"stream":true,"user":"forged"}`)
	r.Header.Set("X-Bf-Key-Id", "arbitrary")
	b.ServeHTTP(w, r)
	if w.Code != 200 || *calls != 1 || !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatal("stream failed")
	}
}
func TestParticipantMCPDenied(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"mcp", "/mcp", `{}`}, {"management", "/api/governance/virtual-keys", `{}`},
		{"model", "/v1/chat/completions", `{"model":"vllm/other","messages":[]}`},
		{"tools", "/v1/chat/completions", `{"model":"vllm/a","messages":[],"tools":[]}`},
		{"fallback", "/v1/chat/completions", `{"model":"vllm/a","messages":[],"fallbacks":["other"]}`},
		{"query", "/v1/chat/completions?key_id=other", `{"model":"vllm/a","messages":[]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b, calls := fixture(t, "user")
			w := httptest.NewRecorder()
			b.ServeHTTP(w, request(b, tc.path, tc.body))
			if w.Code != 403 || *calls != 0 {
				t.Fatal("capability escaped")
			}
		})
	}
}
func TestServiceAndUserAuthenticationRequired(t *testing.T) {
	for _, header := range []string{"Authorization", "X-OpenWebUI-User-Jwt"} {
		t.Run(header, func(t *testing.T) {
			b, calls := fixture(t, "user")
			r := request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[]}`)
			r.Header.Del(header)
			w := httptest.NewRecorder()
			b.ServeHTTP(w, r)
			if w.Code != 401 || *calls != 0 {
				t.Fatal("unauthenticated request accepted")
			}
		})
	}
}
func TestConcurrencyLimit(t *testing.T) {
	b, calls := fixture(t, "user")
	if !b.acquire(subjectID) {
		t.Fatal("first acquisition failed")
	}
	defer b.release(subjectID)
	w := httptest.NewRecorder()
	b.ServeHTTP(w, request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[]}`))
	if w.Code != 429 || *calls != 0 {
		t.Fatal("concurrency limit bypassed")
	}
}
func TestNativePricingAndBudget(t *testing.T) {
	var captured object
	key := object{"id": "reserved", "value": "private", "is_active": false, "description": reserveMarker,
		"provider_configs": []any{object{"provider": "vllm", "allowed_models": []string{"a"}, "allow_all_keys": false, "keys": []any{object{"key_id": "route-a"}}}},
		"mcp_configs":      []any{}, "budgets": []any{object{"max_limit": 500, "reset_duration": "24h", "current_usage": 0}},
		"rate_limit": object{"request_max_limit": 10, "request_reset_duration": "60s", "token_max_limit": 200000, "token_reset_duration": "60s"}}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			_ = json.NewEncoder(w).Encode(object{"virtual_keys": []any{key}, "total_count": 1})
			return
		}
		if r.Method != "PUT" || r.URL.Path != "/api/governance/virtual-keys/reserved" {
			t.Error("unexpected native mutation")
		}
		_ = json.NewDecoder(r.Body).Decode(&captured)
		_ = json.NewEncoder(w).Encode(object{"virtual_key": object{"id": "id", "value": "private"}})
	}))
	defer s.Close()
	b := &Bridge{cfg: Config{GatewayURL: s.URL, BudgetUSD: 500, BudgetReset: "24h", RequestsPerMinute: 10, TokensPerMinute: 200000, AllowedModels: []string{"a"}, ProviderKeyIDs: []string{"route-a"}}, client: s.Client()}
	if _, err := b.newKey(context.Background(), User{ID: subjectID, Name: "Test"}); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"is_active", "budgets", "rate_limit", "provider_configs", "mcp_configs", "value"} {
		if _, ok := captured[name]; ok {
			t.Fatalf("assignment changes protected field %s", name)
		}
	}
	if captured["description"] != marker+subjectID {
		t.Fatal("wrong recipient")
	}
}

func TestReserveExhaustionFailsClosed(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			t.Error("unexpected key creation")
		}
		_ = json.NewEncoder(w).Encode(object{"virtual_keys": []any{}, "total_count": 0})
	}))
	defer s.Close()
	b := &Bridge{cfg: Config{GatewayURL: s.URL}, client: s.Client()}
	if _, err := b.newKey(context.Background(), User{ID: subjectID}); err == nil {
		t.Fatal("exhausted reserve accepted")
	}
}

func TestCachedIdentityDoesNotRotateKey(t *testing.T) {
	b, _ := fixture(t, "user")
	for range 3 {
		k, err := b.ensureKey(context.Background(), User{ID: subjectID})
		if err != nil || k.Value != "personal-key" || k.ID != "key-id" {
			t.Fatal("personal key changed")
		}
	}
}

func TestPricesIncludeStreaming(t *testing.T) {
	var captured object
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			_ = json.NewEncoder(w).Encode(object{"pricing_overrides": []any{}, "total_count": 0})
			return
		}
		_ = json.NewDecoder(r.Body).Decode(&captured)
		w.WriteHeader(201)
	}))
	defer s.Close()
	b := &Bridge{cfg: Config{GatewayURL: s.URL, PriceModels: []string{"a"}, InputPrice: 10, OutputPrice: 20}, client: s.Client()}
	if err := b.ensurePricing(context.Background(), "personal"); err != nil {
		t.Fatal(err)
	}
	if !sameStrings(captured["request_types"], []string{"chat_completion"}) {
		t.Fatal("pricing API requires the base type, which also covers streaming")
	}
	if captured["virtual_key_id"] != "personal" || captured["scope_kind"] != "virtual_key" {
		t.Fatal("global price mutation")
	}
}

func TestExistingNativePriceDoesNotRequireEnabledField(t *testing.T) {
	for _, disabled := range []bool{false, true} {
		p := object{"name": "HardFest personal a", "virtual_key_id": "personal", "scope_kind": "virtual_key", "pattern": "a", "match_type": "exact", "request_types": []string{"chat_completion"}, "pricing_patch": `{"input_cost_per_token":0.00001,"output_cost_per_token":0.00002}`}
		if disabled {
			p["enabled"] = false
		}
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "GET" {
				t.Error("existing pricing must not be recreated")
			}
			_ = json.NewEncoder(w).Encode(object{"pricing_overrides": []object{p}, "total_count": 1})
		}))
		b := &Bridge{cfg: Config{GatewayURL: s.URL, PriceModels: []string{"a"}, InputPrice: 10, OutputPrice: 20}, client: s.Client()}
		err := b.ensurePricing(context.Background(), "personal")
		s.Close()
		if (err != nil) != disabled {
			t.Fatalf("disabled=%v error=%v", disabled, err)
		}
	}
}
