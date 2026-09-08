package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// The narration chain is presentation, not canonical mechanics, so the engine
// stores and audits it without ever reading it back: which slugs are real, and
// which are free, is Python's question. What Go owns here is that the choice
// is durable and that nobody can change it without an audit row.

func TestNarrationChainIsStoredAndAudited(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.narration.set_chain", map[string]any{
		"slots":  map[string]any{"routine_model": "vendor/one:free"},
		"reason": "GM picked a new routine primary",
	})

	stored := scalar(t, path, "SELECT value_json FROM world_state WHERE key='narration_chain'")
	text, _ := stored.(string)
	slots := map[string]any{}
	if err := json.Unmarshal([]byte(text), &slots); err != nil {
		t.Fatalf("stored chain is not JSON: %v (%q)", err, text)
	}
	if slots["routine_model"] != "vendor/one:free" {
		t.Fatalf("routine_model=%v", slots["routine_model"])
	}

	action := scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")
	if action != "admin.narration.set_chain" {
		t.Fatalf("audit action=%v", action)
	}
	reason := scalar(t, path, "SELECT reason FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")
	if !strings.Contains(strings.ToLower(fmt.Sprint(reason)), "routine primary") {
		t.Fatalf("audit reason=%v", reason)
	}
}

func TestNarrationChainMergesRatherThanReplaces(t *testing.T) {
	// A GM changing only the epic primary must not silently blank the routine
	// slots the previous save set.
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.narration.set_chain", map[string]any{
		"slots": map[string]any{"routine_model": "vendor/routine:free"},
	})
	applyAdmin(t, path, "admin.narration.set_chain", map[string]any{
		"slots": map[string]any{"epic_model": "vendor/epic:free"},
	})

	slots := map[string]any{}
	text, _ := scalar(t, path, "SELECT value_json FROM world_state WHERE key='narration_chain'").(string)
	_ = json.Unmarshal([]byte(text), &slots)
	if slots["routine_model"] != "vendor/routine:free" {
		t.Fatalf("routine_model was lost: %v", slots["routine_model"])
	}
	if slots["epic_model"] != "vendor/epic:free" {
		t.Fatalf("epic_model=%v", slots["epic_model"])
	}
}

func TestNarrationChainStoresAClearedFallbackAsEmpty(t *testing.T) {
	// "This tier has no named second hop" is a real answer. Dropping the key
	// instead of storing "" would read back as "never set" and fall through to
	// whatever .env says.
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.narration.set_chain", map[string]any{
		"slots": map[string]any{"routine_fallback_model": "vendor/second:free"},
	})
	applyAdmin(t, path, "admin.narration.set_chain", map[string]any{
		"slots": map[string]any{"routine_fallback_model": ""},
	})

	slots := map[string]any{}
	text, _ := scalar(t, path, "SELECT value_json FROM world_state WHERE key='narration_chain'").(string)
	_ = json.Unmarshal([]byte(text), &slots)
	value, present := slots["routine_fallback_model"]
	if !present {
		t.Fatal("a cleared fallback must be stored, not dropped")
	}
	if value != "" {
		t.Fatalf("routine_fallback_model=%v", value)
	}
}

func TestNarrationChainRejectsAnUnknownSlot(t *testing.T) {
	path := setupAdminDB(t)
	err := applyAdminErr(t, path, "admin.narration.set_chain", map[string]any{
		"slots": map[string]any{"reasoning": "off"},
	})
	if err == nil || !strings.Contains(err.Error(), "unknown narration slot") {
		t.Fatalf("err=%v", err)
	}
	if got := scalar(t, path, "SELECT COUNT(*) FROM world_state WHERE key='narration_chain'"); fmt.Sprint(got) != "0" {
		t.Fatalf("a rejected write must store nothing, got %v", got)
	}
}
