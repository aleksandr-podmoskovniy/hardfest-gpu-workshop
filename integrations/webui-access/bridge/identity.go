// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/url"
	"regexp"
	"strings"
)

var userIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]{8,80}$`)

func (b *Bridge) user(ctx context.Context, id string) (User, error) {
	var u User
	if !userIDPattern.MatchString(id) {
		return u, errors.New("invalid subject")
	}
	err := b.api(ctx, b.cfg.WebUIURL, b.webUIKey, "GET", "/api/v1/users/"+url.PathEscape(id), nil, &u)
	if err == nil && (u.ID != id || id == b.cfg.ServiceUserID || (u.Role != "user" && u.Role != "admin")) {
		err = errors.New("user is not approved")
	}
	return u, err
}

func (b *Bridge) subject(token string) (string, error) {
	if len(token) > 8192 {
		return "", errors.New("invalid identity")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", errors.New("invalid identity")
	}
	var header struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
	}
	hd, e1 := base64.RawURLEncoding.DecodeString(parts[0])
	pl, e2 := base64.RawURLEncoding.DecodeString(parts[1])
	sig, e3 := base64.RawURLEncoding.DecodeString(parts[2])
	if e1 != nil || e2 != nil || e3 != nil || json.Unmarshal(hd, &header) != nil || header.Alg != "HS256" {
		return "", errors.New("invalid identity")
	}
	mac := hmac.New(sha256.New, []byte(b.signingKey))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return "", errors.New("invalid identity")
	}
	var claims struct {
		Sub string `json:"sub"`
		Iss string `json:"iss"`
		Exp int64  `json:"exp"`
		Iat int64  `json:"iat"`
	}
	if json.Unmarshal(pl, &claims) != nil {
		return "", errors.New("invalid identity")
	}
	now := b.now().Unix()
	if claims.Iss != "open-webui" || !userIDPattern.MatchString(claims.Sub) || claims.Exp <= now || claims.Iat > now+15 || claims.Iat <= 0 || claims.Exp-claims.Iat > 300 || claims.Exp <= claims.Iat {
		return "", errors.New("invalid identity")
	}
	return claims.Sub, nil
}
