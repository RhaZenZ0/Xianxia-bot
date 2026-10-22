package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// admin.territory.set_controller (v1.38.0): the GM's hand on a territory's
// banner, audited and reversible, and refused while a war contests the ground.

func setupTerritoryLeverDB(t *testing.T) string {
	t.Helper()
	path := setupAdminDB(t)
	for _, table := range []string{"territory_state", "territory_wars", "world_history_events"} {
		createProductionTable(t, path, table)
	}
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,controller_type,controller_key,updated_at) VALUES('Cloudspine Foothills','Cloudspine Foothills','Cloudspine Foothills','sect','Crimson Furnace Sect',0)`)
	return path
}

func territoryHolder(t *testing.T, path string) string {
	t.Helper()
	return fmt.Sprint(scalar(t, path, `SELECT controller_type||':'||controller_key FROM territory_state WHERE territory_key='Cloudspine Foothills'`))
}

func TestAGMHandsATerritoryToASectAndTheUndoHandsItBack(t *testing.T) {
	path := setupTerritoryLeverDB(t)
	out, _ := applyAdminAs(t, path, "admin.territory.set_controller", 99, map[string]any{
		"territory_key": "Cloudspine Foothills", "sect_name": "Azure Cloud Sect", "reason": "the rival took it by migration 74",
	}).(map[string]any)
	if got := territoryHolder(t, path); got != "sect:Azure Cloud Sect" {
		t.Fatalf("holder=%s after the hand, want sect:Azure Cloud Sect", got)
	}
	if fmt.Sprint(out["previous_controller_key"]) != "Crimson Furnace Sect" {
		t.Fatalf("the reply does not say who held it: %v", out)
	}
	if n := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.territory.set_controller' AND admin_user_id=99`)); n != 1 {
		t.Fatalf("%d audit row(s); a GM lever that does not write admin_audit_log breaks repo rule 6", n)
	}
	if n := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_claimed' AND faction='Azure Cloud Sect' AND visibility='public'`)); n != 1 {
		t.Fatalf("%d public history row(s); the world is not told the banner changed", n)
	}
	// Neutral is the empty sect.
	applyAdmin(t, path, "admin.territory.set_controller", map[string]any{"territory_key": "Cloudspine Foothills", "sect_name": "", "reason": "let it lie"})
	if got := territoryHolder(t, path); got != "neutral:" {
		t.Fatalf("holder=%s after the neutral hand, want neutral:", got)
	}
	// The undo restores the two columns the last hand wrote.
	undoLast(t, path)
	if got := territoryHolder(t, path); got != "sect:Azure Cloud Sect" {
		t.Fatalf("holder=%s after the undo, want sect:Azure Cloud Sect: the lever is audited and its reversal must restore what it wrote", got)
	}
}

func TestTheTerritoryLeverRefusesWhatItCannotMean(t *testing.T) {
	path := setupTerritoryLeverDB(t)
	if _, err := applyAdminRaw(t, path, "admin.territory.set_controller", 99, map[string]any{"territory_key": "Cloudspine Foothills", "sect_name": "No Such Sect"}); err == nil {
		t.Fatal("a sect the catalogue does not know was handed a territory")
	}
	if _, err := applyAdminRaw(t, path, "admin.territory.set_controller", 99, map[string]any{"territory_key": "Nowhere Hills", "sect_name": "Azure Cloud Sect"}); err == nil {
		t.Fatal("a territory with no row was handed over")
	}
	batch4Exec(t, path, `INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_at,updated_at) VALUES('Azure Cloud Sect','Crimson Furnace Sect','Cloudspine Foothills','active',0,0)`)
	_, err := applyAdminRaw(t, path, "admin.territory.set_controller", 99, map[string]any{"territory_key": "Cloudspine Foothills", "sect_name": "Azure Cloud Sect"})
	if err == nil {
		t.Fatal("a territory under an active war was handed over by a GM: the war is the two sects' to end")
	}
	if got := territoryHolder(t, path); got != "sect:Crimson Furnace Sect" {
		t.Fatalf("holder=%s after the refusal; the refusal wrote", got)
	}
}

// A gate, a district or a shop is its city (v1.0.9): the lever lands on the
// city's row whatever part of it the GM names.
func TestTheTerritoryLeverLandsOnTheCity(t *testing.T) {
	path := setupTerritoryLeverDB(t)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,controller_type,controller_key,updated_at) VALUES('Azure Crown Imperial City','Azure Crown Imperial City','Azure Crown Imperial City','neutral','',0)`)
	out, _ := applyAdminAs(t, path, "admin.territory.set_controller", 99, map[string]any{
		"territory_key": "Azure Crown Imperial City North Gate", "sect_name": "Azure Cloud Sect", "reason": "test",
	}).(map[string]any)
	if fmt.Sprint(out["territory_key"]) != "Azure Crown Imperial City" {
		t.Fatalf("a hand naming the gate landed on %v, want the city", out["territory_key"])
	}
	if got := fmt.Sprint(scalar(t, path, `SELECT controller_key FROM territory_state WHERE territory_key='Azure Crown Imperial City'`)); got != "Azure Cloud Sect" {
		t.Fatalf("the city's row reads %q", got)
	}
}
