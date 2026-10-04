// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
)

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
	if b.cfg.nativeIssuance() {
		return b.ownerKeys(ctx)
	}
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
	if b.cfg.ProvisioningMode == "create" {
		return b.createKey(ctx, u)
	}
	if b.cfg.ProvisioningMode != "reserve" {
		return nil, errors.New("invalid provisioning mode")
	}
	keys, err := b.listKeys(ctx)
	if err != nil {
		return nil, err
	}
	var selected object
	for _, k := range keys {
		if k["description"] == b.reserveMarker() && k["is_active"] == false {
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
	body := object{"name": b.cfg.KeyNamePrefix + ": " + u.Name + " (" + u.ID + ")", "description": b.description(u)}
	var out struct {
		Key object `json:"virtual_key"`
	}
	err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(stringValue(selected["id"])), body, &out)
	return out.Key, err
}

// The stable name is also unique in Bifrost's database. Never rename it on login.
// A failed POST can have committed. Read back first; no blind retry in this process.
func (b *Bridge) createKey(ctx context.Context, u User) (object, error) {
	if b.createAttempted[u.ID] {
		return nil, errors.New("key creation outcome unknown; restore API visibility before retry")
	}
	if b.createAttempted == nil {
		b.createAttempted = map[string]bool{}
	}
	providers := make([]object, 0, len(b.cfg.Providers))
	for _, p := range b.cfg.Providers {
		providers = append(providers, object{"provider": p.Provider, "allowed_models": p.Models, "key_ids": p.KeyIDs})
	}
	body := object{
		"name": b.cfg.ManagedBy + ":" + u.ID, "description": b.description(u), "is_active": false,
		"provider_configs": providers, "mcp_configs": []object{},
		"budgets":    []object{{"max_limit": b.cfg.BudgetUSD, "reset_duration": b.cfg.BudgetReset}},
		"rate_limit": object{"request_max_limit": b.cfg.RequestsPerMinute, "request_reset_duration": "60s", "token_max_limit": b.cfg.TokensPerMinute, "token_reset_duration": "60s"},
	}
	if b.cfg.TeamID != "" {
		body["team_id"] = b.cfg.TeamID
	}
	b.createAttempted[u.ID] = true
	var out struct {
		Key object `json:"virtual_key"`
	}
	err := b.gateway(ctx, "POST", "/api/governance/virtual-keys", body, &out)
	if err == nil && b.keyUserID(out.Key) == u.ID {
		return out.Key, nil
	}
	keys, lookupErr := b.listKeys(ctx)
	if lookupErr != nil {
		return nil, errors.New("key creation outcome unknown; key list unavailable")
	}
	var found []object
	for _, k := range keys {
		if b.keyUserID(k) == u.ID {
			found = append(found, k)
		}
	}
	if len(found) == 1 {
		return found[0], nil
	}
	return nil, errors.New("key creation not confirmed; no automatic POST retry")
}

// Pricing is attached to the personal VK, never a global model override.
func (b *Bridge) ensurePricing(ctx context.Context, id string) error {
	if len(b.cfg.Pricing) == 0 {
		return nil
	}
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
	for _, rule := range b.cfg.Pricing {
		model := rule.Model
		name := b.cfg.KeyNamePrefix + " " + id + " " + model
		var found []object
		for _, p := range out.Prices {
			if p["name"] == name {
				found = append(found, p)
			}
		}
		if len(found) > 1 {
			return errors.New("ambiguous price")
		}
		patch := object{"input_cost_per_token": rule.Input / 1e6, "output_cost_per_token": rule.Output / 1e6}
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
	if b.cfg.TeamID != "" && stringValue(key["team_id"]) != b.cfg.TeamID {
		return errors.New("key team policy drift")
	}
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
	if len(v.Providers) != len(b.cfg.Providers) || len(v.MCP) != 0 {
		return errors.New("unexpected key access")
	}
	seen := map[string]bool{}
	for _, actual := range v.Providers {
		if actual.AllKeys || seen[actual.Provider] {
			return errors.New("unexpected provider access")
		}
		seen[actual.Provider] = true
		var want *ProviderPolicy
		for i := range b.cfg.Providers {
			if b.cfg.Providers[i].Provider == actual.Provider {
				want = &b.cfg.Providers[i]
			}
		}
		var ids []string
		for _, k := range actual.Keys {
			ids = append(ids, k.KeyID)
		}
		if want == nil || !sameStrings(actual.AllowedModels, want.Models) || !sameStrings(ids, want.KeyIDs) {
			return errors.New("key model policy drift")
		}
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
	if b.cfg.nativeIssuance() {
		return b.ensureOwnerKey(ctx, u)
	}
	if key, ok := b.keys[u.ID]; ok {
		return key, nil
	}
	keys, err := b.listKeys(ctx)
	if err != nil {
		return credential{}, err
	}
	var found []object
	for _, k := range keys {
		if b.keyUserID(k) == u.ID {
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
	if b.keyUserID(key) != u.ID {
		return credential{}, errors.New("key ownership mismatch")
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
	update := object{}
	if key["description"] != b.description(u) {
		update["description"] = b.description(u)
	}
	if key["is_active"] != true {
		update["is_active"] = true
	}
	if len(update) > 0 {
		if err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(id), update, nil); err != nil {
			return credential{}, err
		}
	}
	result := credential{id, value}
	b.keys[u.ID] = result
	return result, nil
}
