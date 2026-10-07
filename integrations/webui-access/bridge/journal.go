// SPDX-License-Identifier: MIT
package main

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode"
)

// Only issuance identities are persisted, never credentials or quota counters.
// One process holds the volume lock; b.mu serializes requests and reconciliation.
type issuanceRecord struct {
	UserID string `json:"user_id"`
	KeyID  string `json:"key_id"`
	Name   string `json:"name"`
}

type issuanceInventory struct {
	Version   int              `json:"version"`
	ManagedBy string           `json:"managed_by"`
	Keys      []issuanceRecord `json:"keys"`
}

type issuanceJournal struct {
	dir, managedBy string
	lock           *os.File
	persist        func(string, any) error
}

func openJournal(dir, managedBy string, initialize bool) (*issuanceJournal, error) {
	if !filepath.IsAbs(dir) || filepath.Clean(dir) == string(filepath.Separator) {
		return nil, errors.New("issuance journal requires a dedicated absolute directory")
	}
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		return nil, errors.New("issuance journal volume unavailable")
	}
	lock, err := os.OpenFile(filepath.Join(dir, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, errors.New("issuance journal lock unavailable")
	}
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		_ = lock.Close()
		return nil, errors.New("issuance journal already in use")
	}
	j := &issuanceJournal{dir: dir, managedBy: managedBy, lock: lock}
	var marker issuanceInventory
	err = j.read("initialized.json", &marker)
	if initialize {
		if !errors.Is(err, os.ErrNotExist) {
			_ = lock.Close()
			return nil, errors.New("journal initialization requires a new uninitialized volume")
		}
	} else if err != nil || marker.Version != 1 || marker.ManagedBy != managedBy || marker.Keys == nil {
		_ = lock.Close()
		return nil, errors.New("issuance journal not initialized for this installation")
	}
	return j, nil
}

func (j *issuanceJournal) read(name string, out any) error {
	f, err := os.Open(filepath.Join(j.dir, name))
	if err != nil {
		return err
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 1<<20))
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil {
		return errors.New("issuance journal corrupt")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("issuance journal trailing data")
	}
	return nil
}

func (j *issuanceJournal) write(name string, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil || len(encoded) >= 1<<20 {
		return errors.New("issuance journal exceeds supported size")
	}
	f, err := os.CreateTemp(j.dir, ".pending-")
	if err != nil {
		return errors.New("issuance journal unavailable")
	}
	defer os.Remove(f.Name())
	_, err = f.Write(encoded)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(f.Name(), filepath.Join(j.dir, name))
	}
	if err == nil {
		var parent *os.File
		parent, err = os.Open(j.dir)
		if err == nil {
			err = parent.Sync()
			_ = parent.Close()
		}
	}
	if err != nil {
		return errors.New("issuance journal durable write failed")
	}
	return nil
}

func (j *issuanceJournal) inventory() (issuanceInventory, error) {
	var inventory issuanceInventory
	if j.read("initialized.json", &inventory) != nil || inventory.Version != 1 || inventory.ManagedBy != j.managedBy || inventory.Keys == nil {
		return inventory, errors.New("issuance history unavailable; refusing new issuance")
	}
	users, keys := map[string]bool{}, map[string]bool{}
	for _, r := range inventory.Keys {
		if !userIDPattern.MatchString(r.UserID) || r.Name == "" || users[r.UserID] || (r.KeyID != "" && keys[r.KeyID]) {
			return inventory, errors.New("issuance history corrupt")
		}
		users[r.UserID], keys[r.KeyID] = true, true
	}
	return inventory, nil
}

func (j *issuanceJournal) record(id string) (issuanceRecord, error) {
	inventory, err := j.inventory()
	if err != nil {
		return issuanceRecord{}, err
	}
	for _, r := range inventory.Keys {
		if r.UserID == id {
			return r, nil
		}
	}
	return issuanceRecord{}, os.ErrNotExist
}

func (j *issuanceJournal) save(r issuanceRecord) error {
	inventory, err := j.inventory()
	if err != nil {
		return err
	}
	found := false
	for i, old := range inventory.Keys {
		if old.UserID == r.UserID {
			inventory.Keys[i], found = r, true
			break
		}
	}
	if !found {
		inventory.Keys = append(inventory.Keys, r)
	}
	if j.persist != nil {
		return j.persist("initialized.json", inventory)
	}
	return j.write("initialized.json", inventory)
}

func initializeJournal(dir, managedBy string, input io.Reader) error {
	j, err := openJournal(dir, managedBy, true)
	if err != nil {
		return err
	}
	defer j.lock.Close()
	var inventory issuanceInventory
	d := json.NewDecoder(io.LimitReader(input, 1<<20))
	d.DisallowUnknownFields()
	if d.Decode(&inventory) != nil || d.Decode(new(any)) != io.EOF || inventory.Version != 1 || inventory.ManagedBy != managedBy || inventory.Keys == nil {
		return errors.New("invalid issuance inventory")
	}
	users, keys := map[string]bool{}, map[string]bool{}
	for _, r := range inventory.Keys {
		if !userIDPattern.MatchString(r.UserID) || r.KeyID == "" || r.Name == "" || users[r.UserID] || keys[r.KeyID] {
			return errors.New("ambiguous issuance inventory")
		}
		users[r.UserID], keys[r.KeyID] = true, true
	}
	// One atomic snapshot is both inventory and initialization marker. Missing
	// history cannot be mistaken for an empty list of previously issued users.
	return j.write("initialized.json", inventory)
}

func (b *Bridge) displayKeyName(u User) string {
	name := strings.Join(strings.FieldsFunc(u.Name, func(r rune) bool {
		return unicode.IsSpace(r) || unicode.IsControl(r) || unicode.Is(unicode.Cf, r)
	}), " ")
	var limited strings.Builder
	for _, r := range name {
		if limited.Len()+len(string(r)) > 80 {
			break
		}
		limited.WriteRune(r)
	}
	if limited.Len() == 0 {
		limited.WriteString("User")
	}
	return b.cfg.KeyNamePrefix + ": " + limited.String() + " (" + u.ID + ")"
}

func (b *Bridge) beginIssuance(u User) (string, error) {
	if b.journal == nil {
		return b.cfg.ManagedBy + ":" + u.ID, nil // Legacy installations retain stable names.
	}
	if _, err := b.journal.record(u.ID); !errors.Is(err, os.ErrNotExist) {
		return "", errors.New("issuance already recorded or journal unavailable; recover the existing owner key")
	}
	name := b.displayKeyName(u)
	// An empty key_id means a POST may have committed. Never retry blindly,
	// even after restart, user rename, lost response or an incomplete owner list.
	if err := b.journal.save(issuanceRecord{u.ID, "", name}); err != nil {
		return "", err
	}
	return name, nil
}

func (b *Bridge) rememberIssuance(u User, key object) error {
	if b.journal == nil {
		return nil
	}
	id, name := stringValue(key["id"]), stringValue(key["name"])
	if b.keyUserID(key) != u.ID || id == "" || name == "" {
		return errors.New("cannot journal unverified key identity")
	}
	r, err := b.journal.record(u.ID)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err == nil && r.KeyID != "" {
		if r.KeyID != id {
			return errors.New("owner key differs from recorded identity")
		}
		return nil
	}
	return b.journal.save(issuanceRecord{u.ID, id, name})
}
