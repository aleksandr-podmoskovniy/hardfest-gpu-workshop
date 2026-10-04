// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"time"
)

type issuedKey struct {
	key   object
	until time.Time
}

func (b *Bridge) ownerPath() string {
	return "/api/users/" + url.PathEscape(b.cfg.IssuerUserID)
}

func (b *Bridge) ownerKeys(ctx context.Context) ([]object, error) {
	var out struct {
		Keys []object `json:"virtual_keys"`
	}
	if err := b.gateway(ctx, "GET", b.ownerPath()+"/virtual-keys", nil, &out); err != nil {
		return nil, err
	}
	if out.Keys == nil {
		return nil, errors.New("owner key list is incomplete")
	}
	return out.Keys, nil
}

// Native provider DTOs differ from the core management API. Convert a copy,
// never mutate the returned policy or erase a wildcard before validation.
func nativePolicy(raw object) (object, error) {
	var data struct {
		Providers []struct {
			Name      string   `json:"provider_name"`
			Alias     string   `json:"provider"`
			AllModels bool     `json:"all_models_allowed"`
			Models    []string `json:"allowed_models"`
			KeyID     string   `json:"key_id"`
			KeyIDs    []string `json:"key_ids"`
			Budgets   []object `json:"budgets"`
			RateLimit *object  `json:"rate_limit"`
		} `json:"provider_configs"`
	}
	encoded, err := json.Marshal(raw)
	if err != nil || json.Unmarshal(encoded, &data) != nil {
		return nil, errors.New("invalid native policy")
	}
	copy := object{}
	for name, value := range raw {
		copy[name] = value
	}
	providers := []object{}
	for _, p := range data.Providers {
		if p.Name == "" || (p.Alias != "" && p.Alias != p.Name) || p.AllModels || !explicitList(p.Models) || !explicitList(p.KeyIDs) || len(p.Budgets) != 0 || p.RateLimit != nil {
			return nil, errors.New("unexpected native provider access")
		}
		if p.KeyID != "" && !contains(p.KeyIDs, p.KeyID) {
			return nil, errors.New("ambiguous native provider key")
		}
		keys := []object{}
		for _, id := range p.KeyIDs {
			keys = append(keys, object{"key_id": id})
		}
		providers = append(providers, object{"provider": p.Name, "allowed_models": p.Models, "keys": keys, "allow_all_keys": false})
	}
	copy["provider_configs"] = providers
	return copy, nil
}

func (b *Bridge) verifyIssuerProfile(ctx context.Context) error {
	var out struct {
		UserID   string   `json:"user_id"`
		Profiles []object `json:"access_profiles"`
	}
	if err := b.gateway(ctx, "GET", b.ownerPath()+"/access-profiles", nil, &out); err != nil {
		return err
	}
	if out.UserID != b.cfg.IssuerUserID {
		return errors.New("issuer owner mismatch")
	}
	var matches []object
	for _, profile := range out.Profiles {
		if profile["id"] == float64(b.cfg.IssuerProfileID) {
			matches = append(matches, profile)
		}
	}
	if len(matches) != 1 || matches[0]["user_id"] != b.cfg.IssuerUserID || matches[0]["is_active"] != true {
		return errors.New("issuer profile unavailable or inactive")
	}
	profile := matches[0]
	for _, field := range []string{"mcp_tool_groups", "mcp_servers", "mcp_tool_overrides"} {
		if value := profile[field]; value != nil && !sameStrings(value, nil) {
			return errors.New("issuer profile grants MCP access")
		}
	}
	policy, err := nativePolicy(profile)
	if err != nil {
		return err
	}
	return b.validateKey(policy)
}

// verifyIssuerProfile checks the team's profile association separately. The
// native child DTO can omit key.team_id; this is not a direct VK/team binding.
func (b *Bridge) validateOwnerKey(key object) error {
	policy, err := nativePolicy(key)
	if err != nil {
		return err
	}
	if team := stringValue(policy["team_id"]); team != "" && team != b.cfg.TeamID {
		return errors.New("key team policy drift")
	}
	policy["team_id"] = b.cfg.TeamID
	return b.validateKey(policy)
}

func (b *Bridge) createOwnerKey(ctx context.Context, u User) (object, error) {
	if b.createAttempted[u.ID] {
		return nil, errors.New("key creation outcome unknown; recover through owner key list")
	}
	if b.createAttempted == nil {
		b.createAttempted = map[string]bool{}
	}
	b.createAttempted[u.ID] = true
	name := b.cfg.ManagedBy + ":" + u.ID
	var out struct {
		Key object `json:"virtual_key"`
	}
	body := object{"name": name, "description": b.description(u)}
	endpoint := fmt.Sprintf("%s/access-profiles/%d/virtual-keys", b.ownerPath(), b.cfg.IssuerProfileID)
	err := b.gateway(ctx, "POST", endpoint, body, &out)
	if err == nil && out.Key["name"] == name && b.keyUserID(out.Key) == u.ID && stringValue(out.Key["id"]) != "" && stringValue(out.Key["value"]) != "" {
		// Retain the complete successful response before pricing. It is not a
		// usable credential until all checks below pass, and is never logged.
		if b.issued == nil {
			b.issued = map[string]issuedKey{}
		}
		b.issued[u.ID] = issuedKey{out.Key, time.Now().Add(2 * time.Minute)}
		return out.Key, nil
	}
	keys, lookupErr := b.ownerKeys(ctx)
	if lookupErr == nil {
		var found []object
		for _, key := range keys {
			if key["name"] == name && b.keyUserID(key) == u.ID {
				found = append(found, key)
			}
		}
		if len(found) == 1 {
			return found[0], nil
		}
	}
	return nil, errors.New("key creation not confirmed; no automatic POST retry")
}

// Called under b.mu. Owner list, not a stale core DAC snapshot, is authoritative
// for recovery. Existing marked keys retain their ID, value, name and usage.
func (b *Bridge) ensureOwnerKey(ctx context.Context, u User) (credential, error) {
	if _, err := b.user(ctx, u.ID); err != nil {
		return credential{}, err
	}
	if key, ok := b.keys[u.ID]; ok {
		return key, nil
	}
	if err := b.verifyIssuerProfile(ctx); err != nil {
		return credential{}, err
	}
	keys, err := b.ownerKeys(ctx)
	pending, retained := b.issued[u.ID]
	retained = retained && time.Now().Before(pending.until)
	if err != nil && !retained {
		return credential{}, err
	}
	var found []object
	for _, key := range keys {
		if b.keyUserID(key) == u.ID {
			found = append(found, key)
		}
	}
	if len(found) > 1 {
		return credential{}, errors.New("ambiguous personal key")
	}
	var key object
	if len(found) == 1 {
		key = found[0]
	} else if retained {
		// A successful native POST is authoritative during brief list lag.
		// Recheck profile and pricing, but never mint a second key. This bounded
		// process-only recovery expires; after restart owner-list is required.
		key = pending.key
	} else {
		key, err = b.createOwnerKey(ctx, u)
		if err != nil {
			return credential{}, err
		}
	}
	result := credential{stringValue(key["id"]), stringValue(key["value"])}
	if result.ID == "" || result.Value == "" || b.keyUserID(key) != u.ID {
		return credential{}, errors.New("owner key response incomplete")
	}
	if issued, ok := b.issued[u.ID]; ok && (issued.key["id"] != result.ID || stringValue(issued.key["value"]) != result.Value) {
		return credential{}, errors.New("issued key identity changed during recovery")
	}
	if _, ok := key["is_active"].(bool); !ok {
		return credential{}, errors.New("owner key activation state missing")
	}
	if err := b.validateOwnerKey(key); err != nil {
		return credential{}, err
	}
	if err := b.ensurePricing(ctx, result.ID); err != nil {
		return credential{}, err
	}
	if key["is_active"] != true {
		// Preserve reapproval of legacy keys when the existing core endpoint
		// permits it. A 404 fails closed; do not unlink or create a replacement.
		if err := b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(result.ID), object{"is_active": true}, nil); err != nil {
			return credential{}, err
		}
	}
	b.keys[u.ID] = result
	delete(b.issued, u.ID)
	return result, nil
}

func (b *Bridge) confirmOwnerKey(ctx context.Context, userID string, credential credential) bool {
	if b.verifyIssuerProfile(ctx) != nil {
		return false
	}
	keys, err := b.ownerKeys(ctx)
	if err != nil {
		return false
	}
	var matched []object
	for _, key := range keys {
		if b.keyUserID(key) == userID {
			matched = append(matched, key)
		}
	}
	return len(matched) == 1 && matched[0]["is_active"] == true && stringValue(matched[0]["id"]) == credential.ID &&
		stringValue(matched[0]["value"]) == credential.Value && b.validateOwnerKey(matched[0]) == nil
}
