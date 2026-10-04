// SPDX-License-Identifier: MIT
// Native owner API contract tests; no credentials or live gateway access.
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
	"time"
)

type ownerAPI struct {
	mu                              sync.Mutex
	cfg                             Config
	users                           []User
	profile                         object
	keys, prices                    []object
	creates, coreCalls, deletes     int
	failPost, hideKeys, failPricing bool
	failList                        bool
}

func ownerPolicy(c Config) object {
	providers := []object{}
	for _, p := range c.Providers {
		providers = append(providers, object{"provider_name": p.Provider, "provider": p.Provider,
			"all_models_allowed": false, "allowed_models": p.Models, "key_ids": p.KeyIDs})
	}
	return object{
		"provider_configs": providers, "mcp_configs": []object{},
		"budgets":    []object{{"max_limit": c.BudgetUSD, "reset_duration": c.BudgetReset, "current_usage": 12.5}},
		"rate_limit": object{"request_max_limit": c.RequestsPerMinute, "request_reset_duration": "60s", "token_max_limit": c.TokensPerMinute, "token_reset_duration": "60s"},
	}
}

func (a *ownerAPI) key(user User) object {
	key := ownerPolicy(a.cfg)
	key["id"], key["value"] = "personal-"+user.ID, "test-secret-"+user.ID
	key["name"] = a.cfg.ManagedBy + ":" + user.ID
	key["description"] = (&Bridge{cfg: a.cfg}).description(user)
	key["is_active"] = true
	return key
}

func (a *ownerAPI) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		a.mu.Lock()
		defer a.mu.Unlock()
		respond := func(v any) { _ = json.NewEncoder(w).Encode(v) }
		owner := "/api/users/" + a.cfg.IssuerUserID
		switch {
		case r.Method == "DELETE":
			a.deletes++
			t.Error("native unlink must never be used as revoke")
			w.WriteHeader(405)
		case r.URL.Path == "/api/v1/users/":
			respond(object{"users": a.users, "total": len(a.users)})
		case strings.HasPrefix(r.URL.Path, "/api/v1/users/"):
			for _, user := range a.users {
				if r.URL.Path == "/api/v1/users/"+user.ID {
					respond(user)
					return
				}
			}
			w.WriteHeader(404)
		case r.URL.Path == owner+"/access-profiles" && r.Method == "GET":
			respond(object{"user_id": a.cfg.IssuerUserID, "access_profiles": []object{a.profile}})
		case r.URL.Path == owner+"/virtual-keys" && r.Method == "GET":
			if a.failList {
				w.WriteHeader(503)
				return
			}
			keys := a.keys
			if a.hideKeys {
				keys = []object{}
			}
			respond(object{"virtual_keys": keys}) // Deliberately no total_count.
		case r.URL.Path == fmt.Sprintf("%s/access-profiles/%d/virtual-keys", owner, a.cfg.IssuerProfileID) && r.Method == "POST":
			var body object
			if json.NewDecoder(r.Body).Decode(&body) != nil || len(body) != 2 {
				t.Error("native child body must contain only name and description")
				w.WriteHeader(400)
				return
			}
			a.creates++
			for _, key := range a.keys {
				if key["name"] == body["name"] {
					w.WriteHeader(409)
					return
				}
			}
			key := ownerPolicy(a.cfg)
			key["name"], key["description"] = body["name"], body["description"]
			key["id"], key["value"], key["is_active"] = fmt.Sprintf("created-%d", a.creates), "test-personal-secret", true
			a.keys = append(a.keys, key)
			if a.failPost {
				w.WriteHeader(500)
				return
			}
			w.WriteHeader(201)
			respond(object{"virtual_key": key})
		case r.URL.Path == "/api/governance/pricing-overrides":
			if a.failPricing {
				w.WriteHeader(503)
				return
			}
			if r.Method == "GET" {
				var prices []object
				for _, p := range a.prices {
					if p["virtual_key_id"] == r.URL.Query().Get("virtual_key_id") {
						prices = append(prices, p)
					}
				}
				respond(object{"pricing_overrides": prices, "total_count": len(prices)})
				return
			}
			var body object
			if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&body) != nil {
				t.Error("invalid pricing mutation")
				w.WriteHeader(400)
				return
			}
			patch, _ := json.Marshal(body["patch"])
			body["pricing_patch"] = string(patch)
			delete(body, "patch")
			a.prices = append(a.prices, body)
			w.WriteHeader(201)
			respond(object{"pricing_override": body})
		case strings.HasPrefix(r.URL.Path, "/api/governance/virtual-keys"):
			a.coreCalls++
			w.WriteHeader(404) // Reproduce stale DAC visibility, including PUT.
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(404)
		}
	}
}

func ownerFixture(t *testing.T) (*Bridge, *ownerAPI) {
	t.Helper()
	cfg := configured()
	cfg.IssuerUserID, cfg.IssuerProfileID, cfg.TeamID = "issuer-owner-id", 7, "expected-team-id"
	cfg.Pricing = []PriceRule{{"a", 10, 20}}
	a := &ownerAPI{cfg: cfg, users: []User{{ID: subjectID, Name: "Example User", Role: "user"}}, keys: []object{}}
	a.profile = ownerPolicy(cfg)
	a.profile["id"], a.profile["user_id"], a.profile["is_active"] = cfg.IssuerProfileID, cfg.IssuerUserID, true
	a.profile["team_id"] = cfg.TeamID
	for _, field := range []string{"mcp_tool_groups", "mcp_servers", "mcp_tool_overrides"} {
		a.profile[field] = []object{}
	}
	s := httptest.NewServer(a.handler(t))
	t.Cleanup(s.Close)
	cfg.WebUIURL, cfg.GatewayURL = s.URL, s.URL
	return &Bridge{cfg: cfg, client: s.Client(), keys: map[string]credential{}}, a
}

func TestOwnerCreateSucceedsWithoutCoreVisibility(t *testing.T) {
	b, api := ownerFixture(t)
	ctx := context.Background()
	key, err := b.ensureKey(ctx, api.users[0])
	if err != nil || key.ID == "" || api.creates != 1 || api.coreCalls != 0 || len(api.prices) != 1 {
		t.Fatalf("native issuance failed: %v", err)
	}
	for range 2 {
		b.keys, b.createAttempted, b.issued = map[string]credential{}, nil, nil
		recovered, err := b.ensureKey(ctx, api.users[0])
		if err != nil || recovered != key || api.creates != 1 || api.coreCalls != 0 || len(api.prices) != 1 {
			t.Fatal("restart changed key, price or used stale core API")
		}
	}
	if !b.confirmOwnerKey(ctx, subjectID, key) {
		t.Fatal("owner-list activation verification failed")
	}
}

func TestOwnerAmbiguousPOSTRecoversWithoutRetry(t *testing.T) {
	for _, hidden := range []bool{false, true} {
		t.Run(fmt.Sprint(hidden), func(t *testing.T) {
			b, api := ownerFixture(t)
			api.failPost, api.hideKeys = true, hidden
			_, err := b.ensureKey(context.Background(), api.users[0])
			if (err != nil) != hidden {
				t.Fatal("ambiguous POST did not follow owner-list evidence")
			}
			_, _ = b.ensureKey(context.Background(), api.users[0])
			if api.creates != 1 {
				t.Fatal("ambiguous POST repeated")
			}
			api.hideKeys = false
			if _, err := b.ensureKey(context.Background(), api.users[0]); err != nil || api.creates != 1 {
				t.Fatal("owner-list did not recover committed key")
			}
		})
	}
}

func TestOwnerPricingFailureRetainsUnusableIssuance(t *testing.T) {
	b, api := ownerFixture(t)
	api.failPricing = true
	if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil {
		t.Fatal("unpriced credential admitted")
	}
	if len(b.issued) != 1 || len(b.keys) != 0 || api.creates != 1 {
		t.Fatal("successful POST lost, or credential became usable")
	}
	api.failPricing = false
	if _, err := b.ensureKey(context.Background(), api.users[0]); err != nil || api.creates != 1 || len(b.issued) != 0 {
		t.Fatal("pricing recovery changed issuance")
	}
}

func TestOwnerSuccessfulPOSTSurvivesBoundedListLag(t *testing.T) {
	for _, unavailable := range []bool{false, true} {
		t.Run(fmt.Sprint(unavailable), func(t *testing.T) {
			b, api := ownerFixture(t)
			api.failPricing = true
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil {
				t.Fatal("unpriced key served")
			}
			issued := b.issued[subjectID].key
			api.failPricing, api.hideKeys, api.failList = false, true, unavailable
			key, err := b.ensureKey(context.Background(), api.users[0])
			if err != nil || key.ID != issued["id"] || api.creates != 1 || api.coreCalls != 0 {
				t.Fatalf("successful POST discarded during list lag: %v", err)
			}
		})
	}
}

func TestOwnerRetainedResponseDoesNotOutliveApprovalOrExpiry(t *testing.T) {
	for _, pendingUser := range []bool{false, true} {
		t.Run(fmt.Sprint(pendingUser), func(t *testing.T) {
			b, api := ownerFixture(t)
			api.failPricing = true
			_, _ = b.ensureKey(context.Background(), api.users[0])
			api.failPricing, api.hideKeys = false, true
			if pendingUser {
				api.users[0].Role = "pending"
			} else {
				issued := b.issued[subjectID]
				issued.until = time.Now().Add(-time.Second)
				b.issued[subjectID] = issued
			}
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || len(b.keys) != 0 || api.creates != 1 {
				t.Fatal("retained key bypassed approval or bounded lifetime")
			}
		})
	}
}

func TestOwnerLegacyFourteenKeysKeepIdentityAndSpend(t *testing.T) {
	b, api := ownerFixture(t)
	api.users = nil
	for i := range 14 {
		user := User{ID: fmt.Sprintf("existing-user-%02d", i), Name: "Renamed User", Role: "user"}
		api.users = append(api.users, user)
		key := api.key(user)
		key["name"] = "Legacy display name " + user.ID
		key["description"] = b.marker() + user.ID // Legacy description is preserved.
		key["team_id"] = b.cfg.TeamID
		api.keys = append(api.keys, key)
	}
	before, _ := json.Marshal(api.keys)
	if err := b.reconcile(context.Background()); err != nil {
		t.Fatal(err)
	}
	after, _ := json.Marshal(api.keys)
	if string(before) != string(after) || api.creates != 0 || api.coreCalls != 0 || len(b.keys) != 14 {
		t.Fatal("legacy keys, spend or ownership changed")
	}
}

func TestOwnerPendingAndFailedRevokeDoNotIssueOrUnlink(t *testing.T) {
	b, api := ownerFixture(t)
	api.users[0].Role = "pending"
	if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || api.creates != 0 {
		t.Fatal("pending account received a credential")
	}
	api.keys = []object{api.key(api.users[0])}
	api.users = append(api.users, User{ID: "approved-user-id", Role: "user"})
	if err := b.reconcile(context.Background()); err == nil {
		t.Fatal("failed revoke reported success")
	}
	if api.creates != 1 || api.deletes != 0 || len(b.keys) != 1 {
		t.Fatal("failed revoke blocked unrelated approval or unlinked key")
	}
	if _, ok := b.keys[subjectID]; ok {
		t.Fatal("pending user's cached credential survived")
	}
}

func TestOwnerProfileDriftPreventsIssuance(t *testing.T) {
	for _, field := range []string{"owner", "id", "inactive", "team", "models", "wildcard-keys", "mcp", "budget", "rate"} {
		t.Run(field, func(t *testing.T) {
			b, api := ownerFixture(t)
			switch field {
			case "owner":
				api.profile["user_id"] = "different-owner"
			case "id":
				api.profile["id"] = 8
			case "inactive":
				api.profile["is_active"] = false
			case "team":
				api.profile["team_id"] = "other-team"
			case "models":
				api.profile["provider_configs"].([]object)[0]["all_models_allowed"] = true
			case "wildcard-keys":
				api.profile["provider_configs"].([]object)[0]["key_ids"] = []string{"*"}
			case "mcp":
				api.profile["mcp_tool_groups"] = []object{{"tool_group_id": 1}}
			case "budget":
				api.profile["budgets"].([]object)[0]["max_limit"] = 900
			case "rate":
				api.profile["rate_limit"].(object)["request_max_limit"] = 900
			}
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || api.creates != 0 {
				t.Fatal("profile drift issued a key")
			}
		})
	}
}

func TestOwnerKeyPolicyAndMarkerDriftFailClosed(t *testing.T) {
	for _, field := range []string{"models", "keys", "mcp", "budget", "duplicate", "inactive"} {
		t.Run(field, func(t *testing.T) {
			b, api := ownerFixture(t)
			key := api.key(api.users[0])
			api.keys = []object{key}
			switch field {
			case "models":
				key["provider_configs"].([]object)[0]["allowed_models"] = []string{"private"}
			case "keys":
				key["provider_configs"].([]object)[0]["key_ids"] = []string{"other-key"}
			case "mcp":
				key["mcp_configs"] = []object{{"mcp_client_id": "admin"}}
			case "budget":
				key["budgets"].([]object)[0]["max_limit"] = 900
			case "duplicate":
				api.keys = append(api.keys, api.key(api.users[0]))
			case "inactive":
				key["is_active"] = false
			}
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || api.creates != 0 || len(b.keys) != 0 {
				t.Fatal("key drift granted access or rotated key")
			}
		})
	}
}

func TestOwnerMarkerMustBeExactAndForeignKeysUntouched(t *testing.T) {
	b, api := ownerFixture(t)
	foreign := api.key(api.users[0])
	foreign["name"] = "foreign-key"
	foreign["description"] = b.description(api.users[0]) + "; user-id=other-user"
	api.keys = []object{foreign}
	before, _ := json.Marshal(foreign)
	if _, err := b.ensureKey(context.Background(), api.users[0]); err != nil {
		t.Fatal(err)
	}
	after, _ := json.Marshal(foreign)
	if string(before) != string(after) || api.creates != 1 || api.coreCalls != 0 {
		t.Fatal("foreign marker adopted or mutated")
	}
}
