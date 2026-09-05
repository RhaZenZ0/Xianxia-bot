package server

import (
	"net/http/httptest"
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
