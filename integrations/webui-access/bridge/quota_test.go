// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Mirrors the separate self-service quota wire response, not the owner DTO.
// Provider keys are deliberately absent; owner-list supplies their identity.
func quotaFixture(c Config, key object) object {
	providers := []object{}
	for _, p := range c.Providers {
		providers = append(providers, object{"provider": p.Provider, "allow_all_keys": false,
			"keys": nil, "allowed_models": p.Models})
	}
	return object{
		"virtual_key_name": key["name"], "is_active": key["is_active"],
		"budgets": []object{{"max_limit": c.BudgetUSD, "reset_duration": c.BudgetReset, "current_usage": 12.5}},
		"rate_limit": object{"request_max_limit": c.RequestsPerMinute, "request_reset_duration": "60s",
			"token_max_limit": c.TokensPerMinute, "token_reset_duration": "60s"},
		"provider_configs": providers, "model_configs": []object{},
	}
}

func partialOwnerKey(api *ownerAPI) object {
	key := api.key(api.users[0])
	key["budgets"], key["rate_limit"] = []object{}, nil
	api.keys = []object{key}
	return key
}

func TestPartialOwnerUsesRealQuotaAcrossRestart(t *testing.T) {
	b, api := ownerFixture(t)
	key := partialOwnerKey(api)
	before, _ := json.Marshal(key)
	var original credential
	for run := range 3 {
		b.keys, b.createAttempted, b.issued = map[string]credential{}, nil, nil
		got, err := b.ensureKey(context.Background(), api.users[0])
		if err != nil || got.ID != key["id"] || api.creates != 0 || api.coreCalls != 0 {
			t.Fatalf("partial owner quota recovery failed: %v", err)
		}
		if run == 0 {
			original = got
		} else if got != original {
			t.Fatal("restart changed credential")
		}
	}
	if !b.confirmOwnerKey(context.Background(), subjectID, original) {
		t.Fatal("activation confirmation ignored valid self-service quota")
	}
	after, _ := json.Marshal(key)
	if string(before) != string(after) {
		t.Fatal("validation mutated owner DTO or fabricated stored limits")
	}
}

func TestQuotaUnavailableFailsClosed(t *testing.T) {
	for _, status := range []int{401, 403, 404, 500, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			b, api := ownerFixture(t)
			key := partialOwnerKey(api)
			api.quotaStatus = status
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || len(b.keys) != 0 || len(api.prices) != 0 {
				t.Fatal("unverified quota served or priced a credential")
			}
			if b.confirmOwnerKey(context.Background(), subjectID, credential{stringValue(key["id"]), stringValue(key["value"])}) {
				t.Fatal("failed quota accepted as active proof")
			}
		})
	}
}

func TestQuotaDriftAndIncompleteResponsesFailClosed(t *testing.T) {
	mutations := map[string]func(object){
		"wrong-name":     func(q object) { q["virtual_key_name"] = "another key" },
		"missing-name":   func(q object) { delete(q, "virtual_key_name") },
		"wrong-active":   func(q object) { q["is_active"] = false },
		"missing-active": func(q object) { delete(q, "is_active") },
		"no-budget":      func(q object) { q["budgets"] = []object{} },
		"wrong-budget":   func(q object) { q["budgets"].([]object)[0]["max_limit"] = 900 },
		"wrong-window":   func(q object) { q["budgets"].([]object)[0]["reset_duration"] = "48h" },
		"extra-budget": func(q object) {
			q["budgets"] = append(q["budgets"].([]object), object{"max_limit": 500, "reset_duration": "1h"})
		},
		"no-rate":      func(q object) { q["rate_limit"] = nil },
		"wrong-rate":   func(q object) { q["rate_limit"].(object)["request_max_limit"] = 900 },
		"no-providers": func(q object) { q["provider_configs"] = nil },
		"extra-provider": func(q object) {
			q["provider_configs"] = append(q["provider_configs"].([]object), object{"provider": "foreign"})
		},
		"duplicate-provider":    func(q object) { p := q["provider_configs"].([]object); q["provider_configs"] = append(p, p[0]) },
		"all-keys":              func(q object) { q["provider_configs"].([]object)[0]["allow_all_keys"] = true },
		"missing-all-keys":      func(q object) { delete(q["provider_configs"].([]object)[0], "allow_all_keys") },
		"blacklisted-models":    func(q object) { q["provider_configs"].([]object)[0]["blacklisted_models"] = []string{"a"} },
		"conflicting-keys":      func(q object) { q["provider_configs"].([]object)[0]["keys"] = []object{{"key_id": "foreign"}} },
		"wrong-models":          func(q object) { q["provider_configs"].([]object)[0]["allowed_models"] = []string{"*"} },
		"provider-budget":       func(q object) { q["provider_configs"].([]object)[0]["budgets"] = []object{{"max_limit": 500}} },
		"provider-rate":         func(q object) { q["provider_configs"].([]object)[0]["rate_limit"] = object{} },
		"model-override":        func(q object) { q["model_configs"] = []object{{"model_name": "a"}} },
		"missing-model-configs": func(q object) { delete(q, "model_configs") },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			b, api := ownerFixture(t)
			partialOwnerKey(api)
			api.quotaPatch = mutate
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || len(b.keys) != 0 || api.creates != 0 {
				t.Fatal("quota drift served or replaced a credential")
			}
		})
	}
}

func TestPresentOwnerLimitsAreNotOverwrittenByQuota(t *testing.T) {
	for _, field := range []string{"budgets", "rate_limit"} {
		t.Run(field, func(t *testing.T) {
			b, api := ownerFixture(t)
			key := partialOwnerKey(api)
			if field == "budgets" {
				key[field] = []object{{"max_limit": 900, "reset_duration": b.cfg.BudgetReset}}
			} else {
				key[field] = object{"request_max_limit": 900}
			}
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil {
				t.Fatal("owner's explicit policy drift hidden by quota")
			}
		})
	}
}

// main constructs its clients locally. Mirror that client policy here to prove
// quota requests use it rather than an independent, redirect-following client.
func isolatedQuotaClient(t *testing.T) *http.Client {
	t.Helper()
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	t.Cleanup(transport.CloseIdleConnections)
	return &http.Client{
		Transport: transport,
		Timeout:   20 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func TestQuotaDoesNotRedirectPersonalCredential(t *testing.T) {
	for _, status := range []int{http.StatusFound, http.StatusTemporaryRedirect} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			var targetCalls, originCalls atomic.Int32
			target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				targetCalls.Add(1)
				w.WriteHeader(http.StatusUnauthorized)
			}))
			t.Cleanup(target.Close)
			const personal = "synthetic-personal-redirect-credential"
			origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				originCalls.Add(1)
				if r.URL.Path != "/api/governance/virtual-keys/quota" || r.URL.RawQuery != "" || r.Header.Get("x-bf-vk") != personal || r.Header.Get("Authorization") != "" {
					t.Error("quota request used an unexpected endpoint or credential")
				}
				http.Redirect(w, r, target.URL+"/credential-sentinel", status)
			}))
			t.Cleanup(origin.Close)
			b := &Bridge{cfg: Config{GatewayURL: origin.URL}, client: isolatedQuotaClient(t), managementKey: "synthetic-management-credential"}
			_, err := b.ownerKeyQuota(context.Background(), object{"value": personal, "name": "expected", "is_active": true})
			if err == nil || originCalls.Load() != 1 || targetCalls.Load() != 0 {
				t.Fatal("quota redirect was accepted or sent a request to its target")
			}
			if strings.Contains(err.Error(), personal) || strings.Contains(err.Error(), b.managementKey) {
				t.Fatal("quota redirect error disclosed a credential")
			}
		})
	}
}

type quotaRoundTrip func(*http.Request) (*http.Response, error)

func (f quotaRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestQuotaFailuresDoNotEchoCredentials(t *testing.T) {
	const personal = "synthetic-personal-error-credential"
	const management = "synthetic-management-error-credential"
	for _, failure := range []string{"malformed-json", "http-error", "transport-error"} {
		t.Run(failure, func(t *testing.T) {
			origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("x-bf-vk") != personal || r.Header.Get("Authorization") != "" {
					t.Error("quota failure probe used the wrong credential")
				}
				if failure == "http-error" {
					w.WriteHeader(http.StatusServiceUnavailable)
				}
				_, _ = fmt.Fprintf(w, `{"echo":%q,"management":%q,`, personal, management)
			}))
			t.Cleanup(origin.Close)
			client := isolatedQuotaClient(t)
			if failure == "transport-error" {
				client.Transport = quotaRoundTrip(func(r *http.Request) (*http.Response, error) {
					if r.Header.Get("x-bf-vk") != personal || r.Header.Get("Authorization") != "" {
						t.Error("transport failure probe used the wrong credential")
					}
					return nil, errors.New("transport details " + personal + " " + management)
				})
			}
			b := &Bridge{cfg: Config{GatewayURL: origin.URL}, client: client, managementKey: management}
			_, err := b.ownerKeyQuota(context.Background(), object{"value": personal, "name": "expected", "is_active": true})
			if err == nil {
				t.Fatal("invalid quota response was accepted")
			}
			if strings.Contains(err.Error(), personal) || strings.Contains(err.Error(), management) || strings.Contains(err.Error(), "transport details") || strings.Contains(err.Error(), "echo") {
				t.Fatal("quota error included upstream body, transport detail or credential")
			}
		})
	}
}
