// SPDX-License-Identifier: MIT
package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func configured() Config {
	return Config{
		WebUIURL: "http://open-webui:8080", GatewayURL: "https://gateway.example.com", ServiceUserID: "service-user-id",
		ManagedBy: "team-webui", KeyNamePrefix: "WebUI", ProvisioningMode: "create",
		ChatModels: []string{"vllm/a", "vllm/b", "openai/c"},
		Providers:  []ProviderPolicy{{"vllm", []string{"a", "b"}, []string{"local-route"}}, {"openai", []string{"c"}, []string{"cloud-route"}}},
		BudgetUSD:  50, BudgetReset: "24h", RequestsPerMinute: 20, TokensPerMinute: 100000,
		MaxConcurrent: 8, MaxConcurrentPerUser: 2, ReconcileSeconds: 15,
	}
}

func TestConfigurationIsNotTiedToModelOrProviderCount(t *testing.T) {
	c := configured()
	for n := 1; n <= 3; n++ {
		c.ChatModels = configured().ChatModels[:n]
		raw, _ := json.Marshal(c)
		got, err := readConfig(strings.NewReader(string(raw)))
		if err != nil || len(got.ChatModels) != n {
			t.Fatalf("models=%d: %v", n, err)
		}
	}
}

func TestInvalidConfigurationFailsClosed(t *testing.T) {
	cases := map[string]func(*Config){
		"model outside policy": func(c *Config) { c.ChatModels = []string{"vllm/private"} },
		"wildcard keys":        func(c *Config) { c.Providers[0].KeyIDs = []string{"*"} },
		"wildcard models":      func(c *Config) { c.Providers[0].Models = []string{"*"} },
		"duplicate provider":   func(c *Config) { c.Providers[1].Provider = "vllm" },
		"http gateway":         func(c *Config) { c.GatewayURL = "http://gateway.example.com" },
		"query":                func(c *Config) { c.GatewayURL += "?token=invalid" },
		"path":                 func(c *Config) { c.GatewayURL += "/v1" },
		"empty namespace":      func(c *Config) { c.ManagedBy = "" },
		"mode":                 func(c *Config) { c.ProvisioningMode = "automatic-fallback" },
		"no budget":            func(c *Config) { c.BudgetUSD = 0 },
		"zero rpm":             func(c *Config) { c.RequestsPerMinute = 0 },
		"concurrency":          func(c *Config) { c.MaxConcurrentPerUser = 9 },
		"duplicate pricing":    func(c *Config) { c.Pricing = []PriceRule{{"a", 1, 2}, {"a", 2, 3}} },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			c := configured()
			change(&c)
			if c.validate() == nil {
				t.Fatal("accepted invalid configuration")
			}
		})
	}
	c := configured()
	raw, _ := json.Marshal(c)
	for _, body := range []string{strings.TrimSuffix(string(raw), "}") + `,"provider_key_ids":[]}`, string(raw) + " {}"} {
		if _, err := readConfig(strings.NewReader(body)); err == nil {
			t.Fatal("accepted legacy or trailing config")
		}
	}
}

func TestConfigurablePerUserConcurrency(t *testing.T) {
	b := &Bridge{cfg: configured(), activeUsers: map[string]int{}}
	if !b.acquire(subjectID) || !b.acquire(subjectID) || b.acquire(subjectID) {
		t.Fatal("per-user limit not applied")
	}
	b.release(subjectID)
	if !b.acquire(subjectID) {
		t.Fatal("capacity not released")
	}
}
