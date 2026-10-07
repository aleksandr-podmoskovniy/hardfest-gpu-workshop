// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"unicode/utf8"
)

func installTestJournal(t *testing.T, b *Bridge, keys []issuanceRecord) {
	t.Helper()
	b.cfg.StateDir = t.TempDir()
	raw, _ := json.Marshal(issuanceInventory{1, b.cfg.ManagedBy, keys})
	if err := initializeJournal(b.cfg.StateDir, b.cfg.ManagedBy, strings.NewReader(string(raw))); err != nil {
		t.Fatal(err)
	}
	var err error
	b.journal, err = openJournal(b.cfg.StateDir, b.cfg.ManagedBy, false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = b.journal.lock.Close() })
}

func restartTestJournal(t *testing.T, b *Bridge) {
	t.Helper()
	_ = b.journal.lock.Close()
	var err error
	b.journal, err = openJournal(b.cfg.StateDir, b.cfg.ManagedBy, false)
	if err != nil {
		t.Fatal(err)
	}
	b.keys, b.createAttempted, b.issued = map[string]credential{}, nil, nil
}

func TestReadableKeySurvivesRenameRestartAndPartialList(t *testing.T) {
	b, api := ownerFixture(t)
	api.users[0].Name = "Иван"
	installTestJournal(t, b, []issuanceRecord{})
	ctx := context.Background()
	key, err := b.ensureKey(ctx, api.users[0])
	if err != nil || !strings.Contains(stringValue(api.keys[0]["name"]), "Иван") {
		t.Fatalf("readable issuance: %v", err)
	}
	before, _ := json.Marshal(api.keys)
	api.users[0].Name = "Changed Name"
	restartTestJournal(t, b)
	api.hideKeys = true
	if _, err := b.ensureKey(ctx, api.users[0]); err == nil || api.creates != 1 {
		t.Fatal("partial owner list after restart caused repeated issuance")
	}
	api.hideKeys = false
	got, err := b.ensureKey(ctx, api.users[0])
	after, _ := json.Marshal(api.keys)
	if err != nil || got != key || string(before) != string(after) || api.coreCalls != 0 {
		t.Fatal("rename or restart changed the key, policy, spend, or called metadata PUT")
	}
}

func TestJournalLostPOSTResponseAndCrashBeforePOST(t *testing.T) {
	for _, posted := range []bool{false, true} {
		t.Run(map[bool]string{false: "before-post", true: "lost-response"}[posted], func(t *testing.T) {
			b, api := ownerFixture(t)
			installTestJournal(t, b, []issuanceRecord{})
			ctx := context.Background()
			if posted {
				api.failPost, api.hideKeys = true, true
				_, _ = b.ensureKey(ctx, api.users[0])
			} else if _, err := b.beginIssuance(api.users[0]); err != nil {
				t.Fatal(err)
			}
			restartTestJournal(t, b)
			api.users[0].Name = "Another name"
			creates := api.creates
			if _, err := b.ensureKey(ctx, api.users[0]); err == nil || api.creates != creates {
				t.Fatal("ambiguous durable attempt repeated")
			}
			if posted {
				api.hideKeys = false
				if _, err := b.ensureKey(ctx, api.users[0]); err != nil || api.creates != creates {
					t.Fatal("committed key was not recovered by marker")
				}
			}
		})
	}
}

func TestJournalMigratedKeyIDMustMatch(t *testing.T) {
	b, api := ownerFixture(t)
	key := api.key(api.users[0])
	api.keys = []object{key}
	installTestJournal(t, b, []issuanceRecord{{api.users[0].ID, stringValue(key["id"]), stringValue(key["name"])}})
	api.hideKeys = true
	if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || api.creates != 0 {
		t.Fatal("migrated identity lost to incomplete owner list")
	}
	api.hideKeys = false
	key["id"] = "replacement-id"
	if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || len(b.keys) != 0 {
		t.Fatal("different key adopted under the same user marker")
	}
}

func TestJournalMissingCorruptOrUnwritableFailsBeforePOST(t *testing.T) {
	for _, mode := range []string{"missing", "corrupt", "no-space"} {
		t.Run(mode, func(t *testing.T) {
			b, api := ownerFixture(t)
			installTestJournal(t, b, []issuanceRecord{})
			path := filepath.Join(b.cfg.StateDir, "initialized.json")
			switch mode {
			case "missing":
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
			case "corrupt":
				if err := os.WriteFile(path, []byte("{"), 0600); err != nil {
					t.Fatal(err)
				}
			case "no-space":
				b.journal.persist = func(string, any) error { return syscall.ENOSPC }
			}
			if _, err := b.ensureKey(context.Background(), api.users[0]); err == nil || api.creates != 0 {
				t.Fatal("unavailable history permitted POST")
			}
		})
	}
}

func TestJournalInitializationAndExclusiveOwnership(t *testing.T) {
	dir := t.TempDir()
	if _, err := openJournal(dir, "test", false); err == nil {
		t.Fatal("empty volume treated as new issuance history")
	}
	if err := initializeJournal(dir, "test", strings.NewReader(`{"version":1,"managed_by":"test","keys":[]}`)); err != nil {
		t.Fatal(err)
	}
	if err := initializeJournal(dir, "test", strings.NewReader(`{"version":1,"managed_by":"test","keys":[]}`)); err == nil {
		t.Fatal("existing history overwritten")
	}
	if _, err := openJournal(dir, "other", false); err == nil {
		t.Fatal("another installation adopted history")
	}
	j, err := openJournal(dir, "test", false)
	if err != nil {
		t.Fatal(err)
	}
	defer j.lock.Close()
	if _, err := openJournal(dir, "test", false); err == nil {
		t.Fatal("two processes acquired same journal")
	}
}

func TestReadableNameIsBoundedUnicodeAndFullIdentity(t *testing.T) {
	b, _ := ownerFixture(t)
	for _, name := range []string{"Иван", "\n\r\u202eИван\x00", strings.Repeat("Ж", 200), ""} {
		u := User{ID: subjectID, Name: name}
		got := b.displayKeyName(u)
		if !utf8.ValidString(got) || len(got) > 255 || !strings.HasSuffix(got, "("+u.ID+")") || strings.ContainsAny(got, "\n\r\x00\u202e") {
			t.Fatal("unsafe or ambiguous readable key name")
		}
	}
}

func TestJournalReserveModeRejected(t *testing.T) {
	c := configured()
	c.StateDir, c.ProvisioningMode = "/state", "reserve"
	if c.validate() == nil {
		t.Fatal("reserve assignment bypasses durable issuance guard")
	}
}
