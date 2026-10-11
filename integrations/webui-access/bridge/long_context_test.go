// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestLongOutputBudgetAndConfiguredDeadlineReachGateway(t *testing.T) {
	for _, seconds := range []int{0, 7200} {
		b, _ := fixture(t, "user")
		b.cfg.RequestTimeoutSeconds = seconds
		wantSeconds := seconds
		if wantSeconds == 0 {
			wantSeconds = 900
		}
		b.streamClient = &http.Client{Transport: transportFunc(func(r *http.Request) (*http.Response, error) {
			deadline, ok := r.Context().Deadline()
			remaining := time.Until(deadline)
			want := time.Duration(wantSeconds) * time.Second
			if !ok || remaining > want || remaining < want-time.Second {
				t.Fatalf("deadline %v, wanted %v", remaining, want)
			}
			var body object
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body["max_tokens"] != float64(32768) {
				t.Fatalf("32K output budget changed: %v", err)
			}
			return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: io.NopCloser(strings.NewReader("data: [DONE]\n\n"))}, nil
		})}
		w := httptest.NewRecorder()
		b.ServeHTTP(w, request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[],"max_tokens":32768,"stream":true}`))
		if w.Code != 200 || !strings.Contains(w.Body.String(), "[DONE]") {
			t.Fatal("long-context stream failed")
		}
	}
}

func TestClientCancellationStillStopsLongRequest(t *testing.T) {
	b, _ := fixture(t, "user")
	b.cfg.RequestTimeoutSeconds = 7200
	r := request(b, "/v1/chat/completions", `{"model":"vllm/a","messages":[],"max_tokens":32768}`)
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	b.streamClient = &http.Client{Transport: transportFunc(func(upstream *http.Request) (*http.Response, error) {
		cancel()
		select {
		case <-upstream.Context().Done():
			return nil, upstream.Context().Err()
		case <-time.After(time.Second):
			t.Fatal("client cancellation did not reach gateway request")
			return nil, context.DeadlineExceeded
		}
	})}
	w := httptest.NewRecorder()
	b.ServeHTTP(w, r.WithContext(ctx))
	if w.Code != 502 || b.activeTotal != 0 || len(b.activeUsers) != 0 {
		t.Fatal("cancelled request retained capacity")
	}
}
