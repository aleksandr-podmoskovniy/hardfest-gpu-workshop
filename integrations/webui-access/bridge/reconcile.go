// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
)

func (b *Bridge) users(ctx context.Context) ([]User, error) {
	var users []User
	total := -1
	for page := 1; page <= 100; page++ {
		var out struct {
			Users []User `json:"users"`
			Total int    `json:"total"`
		}
		if err := b.api(ctx, b.cfg.WebUIURL, b.webUIKey, "GET", fmt.Sprintf("/api/v1/users/?page=%d&order_by=id&direction=asc", page), nil, &out); err != nil {
			return nil, err
		}
		if total != -1 && total != out.Total {
			return nil, errors.New("user list changed during read")
		}
		total = out.Total
		users = append(users, out.Users...)
		if len(users) == total {
			return users, nil
		}
		if len(users) > total || len(out.Users) == 0 {
			return nil, errors.New("incomplete user list")
		}
	}
	return nil, errors.New("user list too large")
}

func (b *Bridge) reconcile(ctx context.Context) error {
	users, err := b.users(ctx)
	if err != nil {
		return err
	}
	approved := map[string]User{}
	for _, u := range users {
		if userIDPattern.MatchString(u.ID) && u.ID != b.cfg.ServiceUserID && (u.Role == "user" || u.Role == "admin") {
			approved[u.ID] = u
		}
	}
	// Invalidate the process cache before reviewing native key state.
	b.mu.Lock()
	b.keys = map[string]credential{}
	b.mu.Unlock()
	keys, err := b.listKeys(ctx)
	if err != nil {
		return err
	}
	var failures []error
	for _, k := range keys {
		id := b.keyUserID(k)
		if id == "" {
			continue
		}
		if _, ok := approved[id]; !ok && k["is_active"] == true {
			// The account may have been approved after the paginated snapshot.
			// Do not deactivate a key just issued by the request path.
			if current, lookupErr := b.user(ctx, id); lookupErr == nil {
				approved[id] = current
				continue
			}
			if err = b.gateway(ctx, "PUT", "/api/governance/virtual-keys/"+url.PathEscape(stringValue(k["id"])), object{"is_active": false}, nil); err != nil {
				// Native child DELETE only unlinks ownership; it is not revocation.
				// Report the failed core revoke, but do not starve unrelated approvals.
				failures = append(failures, fmt.Errorf("user %s: key revocation not confirmed: %w", id, err))
			}
		}
	}
	for _, u := range approved {
		// Recheck immediately before issuance: approval may have changed during the scan.
		current, err := b.user(ctx, u.ID)
		if err != nil {
			continue
		}
		if _, err = b.ensureKey(ctx, current); err != nil {
			failures = append(failures, fmt.Errorf("user %s: %w", u.ID, err))
		}
	}
	return errors.Join(failures...)
}
