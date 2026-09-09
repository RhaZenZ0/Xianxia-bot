package server

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHandlerRequiresEngineTokenForV1Routes(t *testing.T) {
	t.Setenv("ENGINE_AUTH_TOKEN", "test-engine-token-1234567890")
	engine, err := New(t.TempDir()+"/auth.sqlite3", "")
	if err != nil {
		t.Fatalf("could not create engine: %v", err)
	}
	defer engine.Close()

	unauthorized := httptest.NewRecorder()
	engine.Handler().ServeHTTP(unauthorized, httptest.NewRequest("GET", "/v1/db/status", nil))
	if unauthorized.Code != 401 {
		t.Fatalf("missing token status=%d, want 401", unauthorized.Code)
	}

	wrong := httptest.NewRequest("GET", "/v1/db/status", nil)
	wrong.Header.Set("X-Xianxia-Engine-Token", "wrong-token")
	wrongResponse := httptest.NewRecorder()
	engine.Handler().ServeHTTP(wrongResponse, wrong)
	if wrongResponse.Code != 401 {
		t.Fatalf("wrong token status=%d, want 401", wrongResponse.Code)
	}

	health := httptest.NewRecorder()
	engine.Handler().ServeHTTP(health, httptest.NewRequest("GET", "/livez", nil))
	if health.Code != 200 {
		t.Fatalf("health status=%d, want 200", health.Code)
	}
}

func TestNewRefusesAnEngineWithoutAUsableToken(t *testing.T) {
	// Before v0.29.0 an unset ENGINE_AUTH_TOKEN did not fail construction; it
	// made authorized() return true for every request. Both halves are pinned:
	// New must refuse, and a Server that somehow exists with no token must deny.
	for _, token := range []string{"", "   ", "nineteen-characters"} {
		t.Setenv("ENGINE_AUTH_TOKEN", token)
		engine, err := New(t.TempDir()+"/blank.sqlite3", "")
		if err == nil {
			engine.Close()
			t.Fatalf("New accepted ENGINE_AUTH_TOKEN=%q; want a refusal", token)
		}
		if !strings.Contains(err.Error(), "ENGINE_AUTH_TOKEN") {
			t.Fatalf("refusal must name the variable, got: %v", err)
		}
	}

	bare := &Server{}
	if bare.authorized(httptest.NewRequest("GET", "/v1/db/status", nil)) {
		t.Fatal("a Server with no token authorised a request; the door must fail closed")
	}
}
