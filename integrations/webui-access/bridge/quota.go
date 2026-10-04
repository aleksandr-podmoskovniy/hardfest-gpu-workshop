// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
)

type ownerQuota struct {
	Name      string   `json:"virtual_key_name"`
	Active    *bool    `json:"is_active"`
	Budgets   []object `json:"budgets"`
	RateLimit object   `json:"rate_limit"`
	Providers []struct {
		Provider    string   `json:"provider"`
		Models      []string `json:"allowed_models"`
		Blacklisted []string `json:"blacklisted_models"`
		AllKeys     *bool    `json:"allow_all_keys"`
		Budgets     []object `json:"budgets"`
		Rate        *object  `json:"rate_limit"`
		Keys        []struct {
			ID string `json:"key_id"`
		} `json:"keys"`
	} `json:"provider_configs"`
	Models []object `json:"model_configs"`
}

// The owner DTO does not hydrate migrated VK-scoped model-config limits. Read
// actual governance through the installed self-service API with this exact VK,
// not with a management credential or the issuer profile's expected limits.
func (b *Bridge) ownerKeyQuota(ctx context.Context, key object) (ownerQuota, error) {
	var quota ownerQuota
	value := stringValue(key["value"])
	if value == "" {
		return quota, errors.New("quota credential unavailable")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, b.cfg.GatewayURL+"/api/governance/virtual-keys/quota", nil)
	if err != nil {
		return quota, errors.New("invalid quota request")
	}
	req.Header.Set("x-bf-vk", value)
	resp, err := b.client.Do(req)
	if err != nil {
		return quota, errors.New("quota API unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return quota, errors.New("quota API did not confirm policy")
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 16<<20)).Decode(&quota) != nil {
		return quota, errors.New("invalid quota response")
	}
	active, ok := key["is_active"].(bool)
	if !ok || quota.Active == nil || *quota.Active != active || quota.Name == "" || quota.Name != key["name"] {
		return quota, errors.New("quota identity mismatch")
	}
	return quota, nil
}

func (b *Bridge) validateOwnerQuota(ctx context.Context, key object) error {
	quota, err := b.ownerKeyQuota(ctx, key)
	if err != nil {
		return err
	}
	if len(quota.Providers) != len(b.cfg.Providers) || quota.Models == nil || len(quota.Models) != 0 {
		return errors.New("unexpected quota scope")
	}
	seen := map[string]bool{}
	for _, provider := range quota.Providers {
		if provider.AllKeys == nil || *provider.AllKeys || seen[provider.Provider] || len(provider.Blacklisted) != 0 || len(provider.Budgets) != 0 || provider.Rate != nil {
			return errors.New("unexpected quota provider access")
		}
		seen[provider.Provider] = true
		matched := false
		var reportedKeys []string
		for _, key := range provider.Keys {
			reportedKeys = append(reportedKeys, key.ID)
		}
		for _, expected := range b.cfg.Providers {
			if provider.Provider == expected.Provider && sameStrings(provider.Models, expected.Models) && (len(provider.Keys) == 0 || sameStrings(reportedKeys, expected.KeyIDs)) {
				matched = true
			}
		}
		if !matched {
			return errors.New("quota model policy drift")
		}
	}
	// Combine independent evidence in a temporary copy only: owner supplies
	// identity/access; quota supplies actual limits. Neither response is changed.
	policy := object{}
	for field, value := range key {
		policy[field] = value
	}
	policy["budgets"], policy["rate_limit"] = quota.Budgets, quota.RateLimit
	if err := b.validateOwnerKey(policy); err != nil {
		return err
	}
	// A complete native key may still carry direct limits. Do not hide a
	// disagreement merely because the separate quota endpoint returned a match.
	var declared struct {
		Budgets []object `json:"budgets"`
		Rate    *object  `json:"rate_limit"`
	}
	raw, err := json.Marshal(key)
	if err != nil || json.Unmarshal(raw, &declared) != nil {
		return errors.New("invalid owner limits")
	}
	if len(declared.Budgets) != 0 {
		policy["budgets"] = declared.Budgets
	}
	if declared.Rate != nil {
		policy["rate_limit"] = *declared.Rate
	}
	return b.validateOwnerKey(policy)
}
