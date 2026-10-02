// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Actual governance denial is a policy outcome, not an internal Bifrost error.
const inactiveReply = `{"type":"virtual_key_blocked","status_code":403,"is_bifrost_error":false,"error":{"message":"Virtual key is inactive"}}`

func TestActivationRetryOnlyForConfirmedActivePersonalKey(t *testing.T) {
	for _, tc := range []struct {
		name, role, response string
		active               bool
		status, calls        int
	}{
		{"replica-lag", "user", inactiveReply, true, 403, 2},
		{"revoked-key", "user", inactiveReply, false, 403, 1},
		{"revoked-user", "pending", inactiveReply, true, 403, 1},
		{"budget-denial", "user", `{"error":{"message":"budget exceeded"}}`, true, 429, 1},
		{"upstream-failure", "user", `{"error":{"message":"upstream error"}}`, true, 503, 1},
		{"unverified-denial", "user", `{"error":{"message":"Virtual key is inactive"}}`, true, 403, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b, native := testNativeAPI(t)
			key, err := b.ensureKey(context.Background(), native.users[0])
			if err != nil {
				t.Fatal(err)
			}
			native.keys[0]["is_active"] = tc.active
			native.users[0].Role = tc.role
			calls := 0
			gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasPrefix(r.URL.Path, "/api/governance/virtual-keys/") {
					if r.Method != "GET" {
						t.Error("retry mutated policy")
					}
					json.NewEncoder(w).Encode(object{"virtual_key": native.keys[0]})
					return
				}
				calls++
				body, _ := io.ReadAll(r.Body)
				if string(body) != `{"messages":[]}` {
					t.Error("request body changed")
				}
				if r.Header.Get("x-bf-vk") != key.Value {
					t.Error("personal key changed")
				}
				if calls == 1 {
					w.WriteHeader(tc.status)
					io.WriteString(w, tc.response)
				} else {
					io.WriteString(w, `{"choices":[{}]}`)
				}
			}))
			defer gateway.Close()
			b.cfg.GatewayURL = gateway.URL
			b.streamClient = gateway.Client()
			response, err := b.inference(context.Background(), subjectID, key, []byte(`{"messages":[]}`))
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			body, _ := io.ReadAll(response.Body)
			if calls != tc.calls {
				t.Fatalf("calls=%d, want=%d", calls, tc.calls)
			}
			if tc.calls == 1 && (response.StatusCode != tc.status || string(body) != tc.response) {
				t.Fatal("terminal denial changed")
			}
			if tc.calls == 2 && response.StatusCode != 200 {
				t.Fatal("activation did not converge")
			}
		})
	}
}

func TestActivationRetryHonorsCancellation(t *testing.T) {
	b, native := testNativeAPI(t)
	key, err := b.ensureKey(context.Background(), native.users[0])
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") {
			json.NewEncoder(w).Encode(object{"virtual_key": native.keys[0]})
			cancel()
			return
		}
		calls++
		w.WriteHeader(403)
		io.WriteString(w, inactiveReply)
	}))
	defer gateway.Close()
	b.cfg.GatewayURL = gateway.URL
	b.streamClient = gateway.Client()
	start := time.Now()
	response, err := b.inference(ctx, subjectID, key, []byte(`{}`))
	if response != nil {
		response.Body.Close()
	}
	if calls != 1 || time.Since(start) > time.Second {
		t.Fatal("retry ignored cancellation")
	}
	if response == nil && err == nil {
		t.Fatal("no result")
	}
}

func TestCreateKeyUsesConfiguredTeam(t *testing.T) {
	b, api := testNativeAPI(t)
	b.cfg.TeamID = "isolated-team-id"
	if _, err := b.ensureKey(context.Background(), api.users[0]); err != nil {
		t.Fatal(err)
	}
	if api.keys[0]["team_id"] != b.cfg.TeamID {
		t.Fatal("created key outside configured team")
	}
	api.keys[0]["team_id"] = "other-team-id"
	if b.validateKey(api.keys[0]) == nil {
		t.Fatal("accepted foreign team")
	}
}
