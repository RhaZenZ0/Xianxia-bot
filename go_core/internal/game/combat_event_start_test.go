package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// An event battle is the event's, not the caller's (v1.35.0). combat.start
// kind=event took `severity`, `source` and the node off the payload and checked
// nothing about the event - not that it existed, not that it was open, not that
// the caller stood at it - while world_event.engage checked all three. The
// engine reads the event through the same reader now, picks the beast off the
// roster and scales the fight itself; what the payload says about any of it is
// ignored, and a forged event or node is refused.

var eventStartSeq int

// setupEventBattleDB is an open event at the starting town (setupEventSiteDB)
// plus the battles table a fight is written into (installChallengeWorld).
func setupEventBattleDB(t *testing.T, category string, severity int64) (string, string) {
	t.Helper()
	path, world := setupEventSiteDB(t, category, severity)
	installChallengeWorld(t, path)
	return path, world
}

func eventStart(path, world string, actor int64, payload map[string]any) (map[string]any, error) {
	eventStartSeq++
	out, err := batch4ApplyErr(path, world, "combat.start", actor, eventStartSeq, payload)
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func TestAnEventBattleIsScaledByTheEventNotByTheCaller(t *testing.T) {
	// Severity 2 on the event; the payload claims 10, a realm and a stage, and
	// a forged source naming a node that does not exist. The manifestation is
	// scaled off the caller's own realm and phase by the event's severity, so
	// both scalings are computed off the row and the result must be the
	// event's and not the caller's.
	path, world := setupEventBattleDB(t, "Beast Tide", 2)
	realm := storage.ParseInt(actionScalar(t, path, `SELECT realm_index FROM characters WHERE user_id=42`))
	phase := storage.ParseInt(actionScalar(t, path, `SELECT phase FROM characters WHERE user_id=42`))
	scaled := func(sev int64) (int64, int64, int64) {
		r := maxI64(0, realm+maxI64(0, sev-5)/3)
		s := maxI64(1, minI64(9, phase+maxI64(0, sev-4)/2))
		return r, s, maxI64(12, 14+r*5+s*2+sev*2)
	}
	wantRealm, wantStage, wantHP := scaled(2)
	_, _, forgedHP := scaled(10)
	if wantHP == forgedHP {
		t.Fatalf("severity 2 and 10 scale to the same fight (%d) off realm %d phase %d; the check cannot tell them apart", wantHP, realm, phase)
	}
	out, err := eventStart(path, world, 42, map[string]any{
		"kind": "event", "event_key": "site-event", "npc_name": "Forged Wraith",
		"severity": 10, "npc_realm_index": 9, "npc_stage": 9, "source": "event:forged|node:x", "target_key": "junk",
	})
	if err != nil {
		t.Fatalf("an event battle against an open event was refused: %v", err)
	}
	if storage.ParseInt(out["npc_realm_index"]) != wantRealm || storage.ParseInt(out["npc_stage"]) != wantStage || storage.ParseInt(out["npc_hp"]) != wantHP {
		t.Fatalf("the opponent is realm %v stage %v with %v vitality; the event's severity 2 gives realm %d, stage %d, %d (the caller's 10 would give %d)", out["npc_realm_index"], out["npc_stage"], out["npc_hp"], wantRealm, wantStage, wantHP, forgedHP)
	}
	if got := fmt.Sprint(out["npc_name"]); got != "Test Event — hostile manifestation" {
		t.Fatalf("the opponent is named %q by the caller; the engine names the manifestation", got)
	}
	if got := fmt.Sprint(out["source"]); got != "event:site-event" {
		t.Fatalf("source=%q, the caller's forged source was kept", got)
	}
	if got := fmt.Sprint(out["target_key"]); got != "event:site-event:42" {
		t.Fatalf("target_key=%q; the lock is the engine's, one fight per cultivator against this source", got)
	}
	if out["manifestation"] != true {
		t.Fatalf("an event with no roster is a manifestation; got %v", out["manifestation"])
	}
}

func TestAnEventBattleFightsTheNextBeastOnTheRoster(t *testing.T) {
	path, world := setupEventBattleDB(t, "Beast Tide", 6)
	spawnTestSite(t, path, world, "Beast Tide", 6)
	want := fmt.Sprint(actionScalar(t, path, `SELECT node_key FROM world_event_nodes WHERE event_key='site-event' AND node_type='beast' AND remaining>0 ORDER BY node_id LIMIT 1`))
	name := fmt.Sprint(actionScalar(t, path, `SELECT name FROM world_event_nodes WHERE event_key='site-event' AND node_key=?`, want))
	if want == "" || want == "<nil>" {
		t.Fatal("the Beast Tide site spawned no beast; the fixture is broken, not the tree")
	}
	out, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event"})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(out["node_key"]) != want || fmt.Sprint(out["npc_name"]) != name {
		t.Fatalf("the engine chose %v (%v); the next living beast on the roster is %s (%s)", out["node_key"], out["npc_name"], want, name)
	}
	if got := fmt.Sprint(out["source"]); got != "event:site-event|node:"+want {
		t.Fatalf("source=%q; the node rides the source so the kill takes that beast off the site", got)
	}
	if out["manifestation"] != false {
		t.Fatalf("a roster beast is not a manifestation; got %v", out["manifestation"])
	}
	// A node that is not a beast cannot be fought, however the caller names it.
	batch4Exec(t, path, `INSERT INTO world_event_nodes(event_key,node_key,node_type,name,remaining,created_at,updated_at) VALUES('site-event','shrine','relic','Old Shrine',1,0,0)`)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event", "node_key": "shrine"}); err == nil || !strings.Contains(err.Error(), "not something to fight") {
		t.Fatalf("a relic named as the node was fought: %v", err)
	}
	// Nor a beast the roster has already lost.
	batch4Exec(t, path, `UPDATE world_event_nodes SET remaining=0 WHERE event_key='site-event' AND node_key=?`, want)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event", "node_key": want}); err == nil || !strings.Contains(err.Error(), "already cleared out") {
		t.Fatalf("a cleared beast was fought again: %v", err)
	}
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event", "node_key": "no-such-node"}); err == nil {
		t.Fatal("a node the event does not have was fought")
	}
}

func TestAnEventBattleNeedsAnOpenEventWhereTheCallerStands(t *testing.T) {
	path, world := setupEventBattleDB(t, "Beast Tide", 4)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "no-such-event"}); err == nil || !strings.Contains(err.Error(), "world event not found") {
		t.Fatalf("an event that does not exist was fought: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Cloudblade City' WHERE user_id=42`)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event"}); err == nil || !strings.Contains(err.Error(), "travel to Greenriver Town") {
		t.Fatalf("an event the caller is not at was fought: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE world_events SET active=0 WHERE event_key='site-event'`)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event"}); err == nil || !strings.Contains(err.Error(), "world event is closed") {
		t.Fatalf("a closed event was fought: %v", err)
	}
}

func TestAnEventWithNothingToFightRefusesTheBattle(t *testing.T) {
	// A festival has no roster and is not of a hostile kind, so nothing in it
	// takes the field. The list of hostile kinds used to live in the Battle
	// button; the refusal is the engine's now.
	path, world := setupEventBattleDB(t, "Festival", 3)
	if _, err := eventStart(path, world, 42, map[string]any{"kind": "event", "event_key": "site-event"}); err == nil || !strings.Contains(err.Error(), "nothing in this event is forcing a battle") {
		t.Fatalf("a festival forced a battle: %v", err)
	}
}

func TestAnOlderBotsEventSourceStillNamesTheEvent(t *testing.T) {
	// A bot before v1.35.0 spelled the event and the node into the source and
	// sent no event_key. The two keys are taken from there - a refusal would
	// turn a rolling deploy into an outage (rc.48) - and nothing else it says
	// is read.
	path, world := setupEventBattleDB(t, "Beast Tide", 6)
	spawnTestSite(t, path, world, "Beast Tide", 6)
	want := fmt.Sprint(actionScalar(t, path, `SELECT node_key FROM world_event_nodes WHERE event_key='site-event' AND node_type='beast' AND remaining>0 ORDER BY node_id DESC LIMIT 1`))
	out, err := eventStart(path, world, 42, map[string]any{
		"kind": "event", "npc_name": "Forged", "severity": 10, "source": "event:site-event|node:" + want, "target_key": "event:site-event|node:" + want + ":42",
	})
	if err != nil {
		t.Fatalf("an older bot's event battle was refused: %v", err)
	}
	if fmt.Sprint(out["node_key"]) != want {
		t.Fatalf("the older bot named %s and the engine fought %v", want, out["node_key"])
	}
	rank := storage.ParseInt(actionScalar(t, path, `SELECT rank FROM world_event_nodes WHERE event_key='site-event' AND node_key=?`, want))
	sev := clampI64(rank, 1, 10)
	if got, want := storage.ParseInt(out["npc_hp"]), maxI64(12, 14+storage.ParseInt(out["npc_realm_index"])*5+storage.ParseInt(out["npc_stage"])*2+sev*2); got != want {
		t.Fatalf("npc_hp=%d, want %d from the beast's rank %d; the caller's severity 10 was read", got, want, rank)
	}
}
