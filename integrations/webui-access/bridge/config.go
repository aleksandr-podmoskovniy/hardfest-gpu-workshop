// SPDX-License-Identifier: MIT
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/url"
	"regexp"
	"strings"
)

type ProviderPolicy struct {
	Provider string   `json:"provider"`
	Models   []string `json:"allowed_models"`
	KeyIDs   []string `json:"key_ids"`
}

type PriceRule struct {
	Model  string  `json:"model"`
	Input  float64 `json:"input_usd_per_million_tokens"`
	Output float64 `json:"output_usd_per_million_tokens"`
}

type Config struct {
	WebUIURL             string           `json:"webui_url"`
	GatewayURL           string           `json:"gateway_url"`
	ServiceUserID        string           `json:"service_user_id"`
	ManagedBy            string           `json:"managed_by"`
	KeyNamePrefix        string           `json:"key_name_prefix"`
	ProvisioningMode     string           `json:"provisioning_mode"`
	ChatModels           []string         `json:"chat_models"`
	Providers            []ProviderPolicy `json:"providers"`
	Pricing              []PriceRule      `json:"pricing"`
	BudgetUSD            float64          `json:"budget_usd"`
	BudgetReset          string           `json:"budget_reset"`
	RequestsPerMinute    int              `json:"requests_per_minute"`
	TokensPerMinute      int              `json:"tokens_per_minute"`
	MaxConcurrent        int              `json:"max_concurrent_total"`
	MaxConcurrentPerUser int              `json:"max_concurrent_per_user"`
	ReconcileSeconds     int              `json:"reconcile_interval_seconds"`
}

func readConfig(r io.Reader) (Config, error) {
	var c Config
	d := json.NewDecoder(io.LimitReader(r, 1<<20))
	d.DisallowUnknownFields()
	if err := d.Decode(&c); err != nil {
		return c, fmt.Errorf("invalid config: %w", err)
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return c, fmt.Errorf("unexpected trailing config")
	}
	if c.ProvisioningMode == "" {
		c.ProvisioningMode = "create"
	}
	if c.KeyNamePrefix == "" {
		c.KeyNamePrefix = "Open WebUI"
	}
	if c.MaxConcurrentPerUser == 0 {
		c.MaxConcurrentPerUser = 1
	}
	if c.ReconcileSeconds == 0 {
		c.ReconcileSeconds = 15
	}
	return c, c.validate()
}

func (c Config) validate() error {
	for _, base := range []string{c.WebUIURL, c.GatewayURL} {
		u, err := url.Parse(base)
		if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" || (u.Scheme != "http" && u.Scheme != "https") {
			return fmt.Errorf("API URLs must be origins without paths, credentials or query strings")
		}
	}
	if !strings.HasPrefix(c.GatewayURL, "https://") {
		return fmt.Errorf("gateway TLS required")
	}
	if !userIDPattern.MatchString(c.ServiceUserID) {
		return fmt.Errorf("service_user_id is required")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`).MatchString(c.ManagedBy) {
		return fmt.Errorf("managed_by must identify this installation")
	}
	if len(c.KeyNamePrefix) > 64 || strings.TrimSpace(c.KeyNamePrefix) == "" {
		return fmt.Errorf("invalid key_name_prefix")
	}
	if c.ProvisioningMode != "create" && c.ProvisioningMode != "reserve" {
		return fmt.Errorf("provisioning_mode must be create or reserve")
	}
	if len(c.Providers) == 0 || !explicitList(c.ChatModels) {
		return fmt.Errorf("providers and chat_models are required")
	}
	models := map[string]bool{}
	providers := map[string]bool{}
	for _, p := range c.Providers {
		if !regexp.MustCompile(`^[a-zA-Z0-9_-]+$`).MatchString(p.Provider) || providers[p.Provider] || !explicitList(p.Models) || !explicitList(p.KeyIDs) {
			return fmt.Errorf("providers require unique names and explicit models/key IDs")
		}
		providers[p.Provider] = true
		for _, m := range p.Models {
			models[p.Provider+"/"+m] = true
		}
	}
	for _, m := range c.ChatModels {
		if !models[m] {
			return fmt.Errorf("chat model %q is absent from provider policy", m)
		}
	}
	prices := map[string]bool{}
	for _, p := range c.Pricing {
		if p.Model == "" || strings.Contains(p.Model, "*") || prices[p.Model] || !finiteNonnegative(p.Input) || !finiteNonnegative(p.Output) {
			return fmt.Errorf("pricing requires unique exact model names and nonnegative finite prices")
		}
		prices[p.Model] = true
	}
	if !finiteNonnegative(c.BudgetUSD) || c.BudgetUSD == 0 || !regexp.MustCompile(`^[1-9][0-9]*(s|m|h|d)$`).MatchString(c.BudgetReset) || c.RequestsPerMinute < 1 || c.TokensPerMinute < 1 {
		return fmt.Errorf("positive budget and rate limits are required")
	}
	if c.MaxConcurrent < 1 || c.MaxConcurrentPerUser < 1 || c.MaxConcurrentPerUser > c.MaxConcurrent || c.ReconcileSeconds < 5 || c.ReconcileSeconds > 3600 {
		return fmt.Errorf("invalid concurrency or reconciliation interval")
	}
	return nil
}

func finiteNonnegative(v float64) bool { return v >= 0 && !math.IsInf(v, 0) && !math.IsNaN(v) }

func explicitList(xs []string) bool {
	seen := map[string]bool{}
	if len(xs) == 0 {
		return false
	}
	for _, x := range xs {
		if strings.TrimSpace(x) != x || x == "" || strings.ContainsAny(x, "*\r\n") || seen[x] {
			return false
		}
		seen[x] = true
	}
	return true
}

func (b *Bridge) marker() string        { return "managed-by=" + b.cfg.ManagedBy + "; user-id=" }
func (b *Bridge) reserveMarker() string { return "managed-by=" + b.cfg.ManagedBy + "; reserve" }
func (b *Bridge) description(u User) string {
	return b.marker() + u.ID + "; name=" + url.QueryEscape(u.Name)
}
func (b *Bridge) keyUserID(key object) string {
	desc := stringValue(key["description"])
	if !strings.HasPrefix(desc, b.marker()) {
		return ""
	}
	id, _, _ := strings.Cut(strings.TrimPrefix(desc, b.marker()), ";")
	if !userIDPattern.MatchString(id) {
		return ""
	}
	return id
}
