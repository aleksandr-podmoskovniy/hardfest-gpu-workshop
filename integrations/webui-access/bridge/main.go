// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"
)

type object = map[string]any

type User struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Role string `json:"role"`
}

type credential struct{ ID, Value string }

// One process owns reconciliation. Do not scale without a distributed lock.
type Bridge struct {
	cfg                                               Config
	client                                            *http.Client
	streamClient                                      *http.Client
	webUIKey, managementKey, transportKey, signingKey string
	mu                                                sync.Mutex
	keys                                              map[string]credential
	createAttempted                                   map[string]bool
	issued                                            map[string]issuedKey
	journal                                           *issuanceJournal
	activeMu                                          sync.Mutex
	activeUsers                                       map[string]int
	activeTotal                                       int
	now                                               func() time.Time
}

func main() {
	if len(os.Args) == 4 && os.Args[1] == "--initialize-state" {
		if err := initializeJournal(os.Args[2], os.Args[3], os.Stdin); err != nil {
			log.Fatal(err)
		}
		log.Print("issuance journal initialized")
		return
	}
	path := os.Getenv("CONFIG_FILE")
	if path == "" {
		path = "/config/config.json"
	}
	file, err := os.Open(path)
	if err != nil {
		log.Fatal("configuration unavailable")
	}
	cfg, err := readConfig(file)
	_ = file.Close()
	if err != nil {
		log.Fatal(err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	noRedirect := func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	b := &Bridge{cfg: cfg, client: &http.Client{Timeout: 20 * time.Second, Transport: transport, CheckRedirect: noRedirect}, streamClient: &http.Client{Transport: transport, CheckRedirect: noRedirect}, keys: map[string]credential{}, activeUsers: map[string]int{}, now: time.Now,
		webUIKey: os.Getenv("WEBUI_ADMIN_API_KEY"), managementKey: os.Getenv("BIFROST_MANAGEMENT_KEY"), transportKey: os.Getenv("WEBUI_TRANSPORT_KEY"), signingKey: os.Getenv("FORWARD_USER_INFO_HEADER_JWT_SECRET")}
	for _, s := range []string{b.webUIKey, b.managementKey, b.transportKey, b.signingKey} {
		if len(s) < 32 {
			log.Fatal("required credential unavailable")
		}
	}
	if b.transportKey == b.signingKey {
		log.Fatal("transport and signing secrets must differ")
	}
	if cfg.StateDir != "" {
		b.journal, err = openJournal(cfg.StateDir, cfg.ManagedBy, false)
		if err != nil {
			log.Fatal(err)
		}
		defer b.journal.lock.Close()
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		for {
			run, cancel := context.WithTimeout(ctx, 2*time.Minute)
			err := b.reconcile(run)
			cancel()
			if err != nil {
				log.Printf("user reconciliation failed: %v", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Duration(cfg.ReconcileSeconds) * time.Second):
			}
		}
	}()
	server := &http.Server{Addr: ":8080", Handler: b, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	if err = server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatal("HTTP listener failed")
	}
}
