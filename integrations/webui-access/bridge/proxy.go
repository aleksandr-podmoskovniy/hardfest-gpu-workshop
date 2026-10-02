// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"time"
)

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
	if b.activeUsers[id] >= b.cfg.MaxConcurrentPerUser || b.activeTotal >= b.cfg.MaxConcurrent {
		return false
	}
	b.activeUsers[id]++
	b.activeTotal++
	return true
}
func (b *Bridge) release(id string) {
	b.activeMu.Lock()
	defer b.activeMu.Unlock()
	b.activeUsers[id]--
	if b.activeUsers[id] == 0 {
		delete(b.activeUsers, id)
	}
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
			models = append(models, object{"id": id, "object": "model", "owned_by": b.cfg.ManagedBy})
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
	resp, err := b.inference(ctx, id, key, data)
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

// HA nodes can briefly retain the disabled state of a newly activated VK.
// Only retry the exact pre-inference denial, never timeouts, 5xx, quotas or SSE.
// Recheck approval and persisted policy before every retry; never reactivate here.
func (b *Bridge) inference(ctx context.Context, id string, key credential, data []byte) (*http.Response, error) {
	for attempt := 0; ; attempt++ {
		req, err := http.NewRequestWithContext(ctx, "POST", b.cfg.GatewayURL+"/v1/chat/completions", bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+key.Value)
		req.Header.Set("x-bf-vk", key.Value)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("x-bf-mcp-include-tools", "")
		req.Header.Set("x-bf-cache-key", b.cfg.ManagedBy+"-personal-"+id)
		req.Header.Set("x-bf-cache-type", "direct")
		req.Header.Set("x-bf-cache-no-store", "true")
		resp, err := b.streamClient.Do(req)
		if err != nil {
			return nil, err
		}
		if resp.StatusCode != http.StatusForbidden || attempt >= 4 {
			return resp, nil
		}
		raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 4097))
		originalBody := resp.Body
		resp.Body = &replayBody{Reader: io.MultiReader(bytes.NewReader(raw), originalBody), Closer: originalBody}
		var denial struct {
			Bifrost bool `json:"is_bifrost_error"`
			Error   struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		if readErr != nil || len(raw) > 4096 || json.Unmarshal(raw, &denial) != nil || !denial.Bifrost || denial.Error.Message != "Virtual key is inactive" {
			return resp, nil
		}
		if _, err := b.user(ctx, id); err != nil {
			return resp, nil
		}
		var out struct {
			Key object `json:"virtual_key"`
		}
		if err := b.gateway(ctx, "GET", "/api/governance/virtual-keys/"+url.PathEscape(key.ID), nil, &out); err != nil ||
			out.Key["is_active"] != true || b.keyUserID(out.Key) != id || stringValue(out.Key["id"]) != key.ID ||
			stringValue(out.Key["value"]) != key.Value || b.validateKey(out.Key) != nil {
			return resp, nil
		}
		resp.Body.Close()
		timer := time.NewTimer(time.Duration(1<<attempt) * 250 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

type replayBody struct {
	io.Reader
	io.Closer
}
