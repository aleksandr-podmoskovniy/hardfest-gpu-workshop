// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

type nativeAPI struct {
	mu                 sync.Mutex
	users              []User
	keys               []object
	creates            int
	failPost, hideKeys bool
}

func (n *nativeAPI) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		n.mu.Lock()
		defer n.mu.Unlock()
		respond := func(v any) { _ = json.NewEncoder(w).Encode(v) }
		path := r.URL.Path
		if path == "/api/v1/users/" {
			respond(object{"users": n.users, "total": len(n.users)})
			return
		}
		if strings.HasPrefix(path, "/api/v1/users/") {
			for _, u := range n.users {
				if strings.HasSuffix(path, "/"+u.ID) {
					respond(u)
					return
				}
			}
			w.WriteHeader(404)
			return
		}
		if path == "/api/governance/virtual-keys" && r.Method == "GET" {
			if n.hideKeys {
				respond(object{"virtual_keys": []object{}, "total_count": 0})
				return
			}
			respond(object{"virtual_keys": n.keys, "total_count": len(n.keys)})
			return
		}
		var body object
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		if path == "/api/governance/virtual-keys" && r.Method == "POST" {
			n.creates++
			for _, k := range n.keys {
				if k["name"] == body["name"] {
					w.WriteHeader(409)
					return
				}
			}
			if body["is_active"] != false {
				t.Error("created active key before validation")
			}
			for _, item := range body["provider_configs"].([]any) {
				p := item.(map[string]any)
				ids := p["key_ids"].([]any)
				p["keys"] = []object{}
				for _, id := range ids {
					p["keys"] = append(p["keys"].([]object), object{"key_id": id})
				}
				p["allow_all_keys"] = false
			}
			body["id"] = fmt.Sprintf("key-%d", len(n.keys)+1)
			body["value"] = "personal-test-credential"
			body["budgets"].([]any)[0].(map[string]any)["current_usage"] = 12.5
			n.keys = append(n.keys, body)
			if n.failPost {
				w.WriteHeader(500)
				return
			}
			w.WriteHeader(201)
			respond(object{"virtual_key": body})
			return
		}
		if strings.HasPrefix(path, "/api/governance/virtual-keys/") && r.Method == "PUT" {
			for _, key := range n.keys {
				if strings.HasSuffix(path, "/"+stringValue(key["id"])) {
					for field, value := range body {
						if field != "is_active" && field != "description" {
							t.Errorf("reconcile touched %s", field)
						}
						key[field] = value
					}
					respond(object{"virtual_key": key})
					return
				}
			}
		}
		t.Errorf("unexpected API request: %s %s", r.Method, path)
		w.WriteHeader(400)
	}
}

func testNativeAPI(t *testing.T) (*Bridge, *nativeAPI) {
	t.Helper()
	api := &nativeAPI{users: []User{{ID: subjectID, Name: "Example User", Role: "user"}, {ID: "pending-user-id", Role: "pending"}}}
	server := httptest.NewServer(api.handler(t))
	t.Cleanup(server.Close)
	c := configured()
	c.WebUIURL = server.URL
	c.GatewayURL = server.URL
	return &Bridge{cfg: c, client: server.Client(), keys: map[string]credential{}}, api
}

func TestApprovalCreatesPersonalKeyAndPreservesSpend(t *testing.T) {
	b, api := testNativeAPI(t)
	ctx := context.Background()
	for range 2 {
		if err := b.reconcile(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if api.creates != 1 || len(api.keys) != 1 || api.keys[0]["is_active"] != true {
		t.Fatal("issuance not idempotent")
	}
	id := api.keys[0]["id"]
	// Restart: no in-memory credentials. Recover the same native key.
	b.keys = map[string]credential{}
	if err := b.reconcile(ctx); err != nil {
		t.Fatal(err)
	}
	api.mu.Lock()
	api.users[0].Role = "pending"
	api.mu.Unlock()
	if err := b.reconcile(ctx); err != nil {
		t.Fatal(err)
	}
	if api.keys[0]["is_active"] != false {
		t.Fatal("approval revocation ignored")
	}
	api.mu.Lock()
	api.users[0].Role = "user"
	api.users[0].Name = "Renamed User"
	api.mu.Unlock()
	if err := b.reconcile(ctx); err != nil {
		t.Fatal(err)
	}
	if api.creates != 1 || api.keys[0]["id"] != id || api.keys[0]["is_active"] != true {
		t.Fatal("reapproval rotated key")
	}
	if api.keys[0]["budgets"].([]any)[0].(map[string]any)["current_usage"] != 12.5 {
		t.Fatal("spend reset")
	}
	if api.keys[0]["description"] != b.description(api.users[0]) {
		t.Fatal("display name not refreshed")
	}
}

func TestCreateFailureReadsBackInsteadOfRepeatingPOST(t *testing.T) {
	for _, hidden := range []bool{false, true} {
		t.Run(fmt.Sprint(hidden), func(t *testing.T) {
			b, api := testNativeAPI(t)
			api.failPost = true
			api.hideKeys = hidden
			u := api.users[0]
			_, err := b.ensureKey(context.Background(), u)
			if (err != nil) != hidden {
				t.Fatalf("hidden=%v: %v", hidden, err)
			}
			_, _ = b.ensureKey(context.Background(), u)
			if api.creates != 1 {
				t.Fatal("retried ambiguous POST")
			}
			api.mu.Lock()
			api.hideKeys = false
			api.mu.Unlock()
			if _, err = b.ensureKey(context.Background(), u); err != nil {
				t.Fatal(err)
			}
			if api.creates != 1 || len(api.keys) != 1 {
				t.Fatal("read-back created a duplicate")
			}
		})
	}
}

func TestForeignKeysAreNotAdoptedOrRevoked(t *testing.T) {
	b, api := testNativeAPI(t)
	api.keys = []object{{"id": "foreign", "description": "managed-by=another-webui; user-id=" + subjectID, "is_active": true}}
	if err := b.reconcile(context.Background()); err != nil {
		t.Fatal(err)
	}
	if api.creates != 1 || api.keys[0]["is_active"] != true || len(api.keys) != 2 {
		t.Fatal("foreign key changed")
	}
}

func TestSeveralUsersGetDistinctKeys(t *testing.T) {
	b, api := testNativeAPI(t)
	api.users[1].Role = "user"
	if err := b.reconcile(context.Background()); err != nil {
		t.Fatal(err)
	}
	if api.creates != 2 || api.keys[0]["id"] == api.keys[1]["id"] {
		t.Fatal("shared key issued")
	}
}

func TestPolicyDriftDoesNotReactivateOrResetKey(t *testing.T) {
	b, api := testNativeAPI(t)
	ctx := context.Background()
	if _, err := b.ensureKey(ctx, api.users[0]); err != nil {
		t.Fatal(err)
	}
	b.keys = map[string]credential{}
	api.mu.Lock()
	api.keys[0]["is_active"] = false
	budget := api.keys[0]["budgets"].([]any)[0].(map[string]any)
	budget["max_limit"] = 999
	api.mu.Unlock()
	if _, err := b.ensureKey(ctx, api.users[0]); err == nil {
		t.Fatal("accepted policy drift")
	}
	if api.keys[0]["is_active"] != false || budget["max_limit"] != 999 || api.creates != 1 {
		t.Fatal("drift repaired by resetting or replacing key")
	}
}

func TestRestartAfterAmbiguousCreateCannotDuplicateName(t *testing.T) {
	b, api := testNativeAPI(t)
	api.failPost, api.hideKeys = true, true
	ctx := context.Background()
	u := api.users[0]
	if _, err := b.ensureKey(ctx, u); err == nil {
		t.Fatal("accepted invisible key")
	}
	// Bifrost's unique name constraint survives an adapter restart.
	b.createAttempted = nil
	if _, err := b.ensureKey(ctx, u); err == nil {
		t.Fatal("accepted conflicting invisible key")
	}
	if len(api.keys) != 1 || api.keys[0]["is_active"] != false {
		t.Fatal("duplicate key or premature activation")
	}
	api.mu.Lock()
	api.hideKeys = false
	api.mu.Unlock()
	if _, err := b.ensureKey(ctx, u); err != nil {
		t.Fatal(err)
	}
}
