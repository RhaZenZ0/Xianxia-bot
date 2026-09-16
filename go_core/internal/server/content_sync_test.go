package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// The sync endpoint is behind the engine token like every other /v1 route,
// and on a database the migration has not reached it answers "skipped"
// rather than failing - that is the state every first boot passes through,
// and db-init calls this the moment the tables exist.
func TestContentSyncIsTokenGuardedAndSkipsBeforeTheMigration(t *testing.T) {
	t.Setenv("ENGINE_AUTH_TOKEN", "test-engine-token-1234567890")
	world := filepath.Join("..", "..", "..", "content", "world.json")
	engine, err := New(t.TempDir()+"/content.sqlite3", world)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()
	handler := engine.Handler()

	unauthenticated := httptest.NewRecorder()
	handler.ServeHTTP(unauthenticated, httptest.NewRequest(http.MethodPost, "/v1/content/sync", strings.NewReader("{}")))
	if unauthenticated.Code != http.StatusUnauthorized {
		t.Fatalf("without the token: %d, want 401", unauthenticated.Code)
	}

	request := httptest.NewRequest(http.MethodPost, "/v1/content/sync", strings.NewReader("{}"))
	request.Header.Set("X-Xianxia-Engine-Token", "test-engine-token-1234567890")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("with the token: %d %s", response.Code, response.Body.String())
	}
	var result struct {
		Skipped bool `json:"skipped"`
		Applied bool `json:"applied"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Skipped || result.Applied {
		t.Fatalf("a bare database must be skipped, not written: %s", response.Body.String())
	}

	get := httptest.NewRequest(http.MethodGet, "/v1/content/sync", nil)
	get.Header.Set("X-Xianxia-Engine-Token", "test-engine-token-1234567890")
	wrongMethod := httptest.NewRecorder()
	handler.ServeHTTP(wrongMethod, get)
	if wrongMethod.Code != http.StatusMethodNotAllowed {
		t.Fatalf("GET: %d, want 405", wrongMethod.Code)
	}
}
