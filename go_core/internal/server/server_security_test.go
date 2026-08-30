package server

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDecodeJSONRejectsTrailingValue(t *testing.T) {
	request := httptest.NewRequest("POST", "/", strings.NewReader(`{"ok":true} {"extra":true}`))
	var payload map[string]any
	if err := decodeJSON(request, &payload); err == nil {
		t.Fatal("expected trailing JSON value to be rejected")
	}
}

func TestSimulationForceRejectsOversizedBody(t *testing.T) {
	engine := &Server{}
	body := `{"padding":"` + strings.Repeat("x", (1<<20)+1) + `"}`
	request := httptest.NewRequest("POST", "/v1/simulation/force", strings.NewReader(body))
	response := httptest.NewRecorder()

	engine.simulationForce(response, request)

	if response.Code != 400 {
		t.Fatalf("expected 400 for oversized body, got %d: %s", response.Code, response.Body.String())
	}
}

func TestDBMaintenanceRejectsOversizedBody(t *testing.T) {
	engine := &Server{}
	body := `{"action":"` + strings.Repeat("x", (64<<10)+1) + `"}`
	request := httptest.NewRequest("POST", "/v1/db/maintenance", strings.NewReader(body))
	response := httptest.NewRecorder()

	engine.dbMaintenance(response, request)

	if response.Code != 400 {
		t.Fatalf("expected 400 for oversized body, got %d: %s", response.Code, response.Body.String())
	}
}
