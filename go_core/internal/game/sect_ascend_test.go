package game

import (
	"fmt"
	"strings"
	"testing"
)

// The way up into an allied sect (v1.18.0). These drive the shipped
// catalogue: Azure Cloud names the Jade Meridian Sect above it, whose gate is
// the Jade Meridian Stone Gate in the Spiritual World at that world's floor.

func ascentDB(t *testing.T, sect string, location string, realm int64) string {
	t.Helper()
	path := setupSectTrialDB(t)
	batch4Exec(t, path, fmt.Sprintf(`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'%s','Core Disciple',30,0)`, sect))
	batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET location='%s',realm_index=%d WHERE user_id=42`, location, realm))
	return path
}

func TestAMemberAtTheAlliedGateIsTakenInAsAnOuterDisciple(t *testing.T) {
	catalog := crossingCatalog(t)
	gate := sectGate(catalog, "Jade Meridian Sect")
	if gate == "" || sectAscendsTo(catalog, "Azure Cloud Sect") != "Jade Meridian Sect" {
		t.Fatalf("the shipped content no longer names Jade Meridian above Azure Cloud at a gate; the fixture is wrong, not the rule")
	}
	path := ascentDB(t, "Azure Cloud Sect", gate, catalog.Locations[gate].MinRealmIndex)
	out := batch4Result(t, batch4Apply(t, path, batch4WorldPath(t), "sect.ascend", 1, map[string]any{}))
	if out["to"] != "Jade Meridian Sect" || out["from"] != "Azure Cloud Sect" {
		t.Fatalf("the ascent went %v -> %v", out["from"], out["to"])
	}
	row := actionScalar(t, path, `SELECT sect_name||'|'||rank_name||'|'||rank_level FROM sect_membership WHERE user_id=42`)
	if fmt.Sprint(row) != "Jade Meridian Sect|Outer Disciple|10" {
		t.Fatalf("membership after the ascent: %v; standing begins again in the sect above", row)
	}
	if n := actionScalar(t, path, `SELECT COUNT(*) FROM character_sect_discoveries WHERE user_id=42 AND sect_name='Jade Meridian Sect'`); fmt.Sprint(n) != "1" {
		t.Fatalf("the sect above is not on the map after the ascent: %v", n)
	}
	if n := actionScalar(t, path, `SELECT COUNT(*) FROM sect_recruitment_attempts WHERE user_id=42 AND sect_name='Jade Meridian Sect' AND attempt_type='ascent' AND result='pass'`); fmt.Sprint(n) != "1" {
		t.Fatalf("the ascent left no record: %v", n)
	}
}

func TestTheWayUpIsRefusedAwayFromTheGateBelowTheFloorAndToNobody(t *testing.T) {
	catalog := crossingCatalog(t)
	gate := sectGate(catalog, "Jade Meridian Sect")
	floor := catalog.Locations[gate].MinRealmIndex
	world := batch4WorldPath(t)
	cases := []struct {
		name, sect, location string
		realm                int64
		member               bool
		refusal              string
	}{
		{"from the street", "Azure Cloud Sect", "Greenriver Town", floor, true, "is taken at " + gate},
		{"below the world's floor", "Azure Cloud Sect", gate, floor - 1, true, "asks for"},
		{"in no sect", "", gate, floor, false, "belong to no sect"},
		{"from a sect with nothing above it", "Celestial Mandate Academy", gate, floor, true, "names no sect above it"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var path string
			if tc.member {
				path = ascentDB(t, tc.sect, tc.location, tc.realm)
			} else {
				path = setupSectTrialDB(t)
				batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET location='%s',realm_index=%d WHERE user_id=42`, tc.location, tc.realm))
			}
			_, err := batch4ApplyErr(path, world, "sect.ascend", 42, 1, map[string]any{})
			if err == nil || !strings.Contains(err.Error(), tc.refusal) {
				t.Fatalf("want a refusal naming %q, got %v", tc.refusal, err)
			}
			if tc.member {
				if row := actionScalar(t, path, `SELECT sect_name FROM sect_membership WHERE user_id=42`); fmt.Sprint(row) != tc.sect {
					t.Fatalf("a refused ascent moved the membership to %v", row)
				}
			}
		})
	}
}

// A place floor is measured on the ladder that carried the cultivator there.
// Travel admits a body cultivator to the gate at the body ladder's stage, so a
// member who walked in at body 8 with the qi ladder still at 3 must be let
// through the door they were walked to (the qi-only comparison refused them
// with "asks for Spirit Body Transformation; you stand at Core Formation").
func TestABodyCultivatorAtTheAlliedGateIsTakenIn(t *testing.T) {
	catalog := crossingCatalog(t)
	gate := sectGate(catalog, "Jade Meridian Sect")
	floor := catalog.Locations[gate].MinRealmIndex
	if floor < 5 {
		t.Fatalf("the gate's floor is %d; the fixture needs a qi stage well below it", floor)
	}
	path := ascentDB(t, "Azure Cloud Sect", gate, 3)
	batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET body_realm_index=%d WHERE user_id=42`, floor))
	out := batch4Result(t, batch4Apply(t, path, batch4WorldPath(t), "sect.ascend", 1, map[string]any{}))
	if out["to"] != "Jade Meridian Sect" {
		t.Fatalf("a body cultivator at body %d / qi 3 was not taken in: %v", floor, out)
	}
	if row := actionScalar(t, path, `SELECT sect_name FROM sect_membership WHERE user_id=42`); fmt.Sprint(row) != "Jade Meridian Sect" {
		t.Fatalf("membership after the ascent: %v", row)
	}
}

// Below the floor on both ladders, the refusal names the stage the way was
// measured at - the higher ladder's - as the stall's does, rather than the qi
// stage the cultivator is not being measured by.
func TestTheRefusalNamesTheStageTheWayWasMeasuredAt(t *testing.T) {
	catalog := crossingCatalog(t)
	gate := sectGate(catalog, "Jade Meridian Sect")
	floor := catalog.Locations[gate].MinRealmIndex
	path := ascentDB(t, "Azure Cloud Sect", gate, 3)
	batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET body_realm_index=%d WHERE user_id=42`, floor-1))
	_, err := batch4ApplyErr(path, batch4WorldPath(t), "sect.ascend", 42, 1, map[string]any{})
	want := "you stand at " + realmNameGo(catalog, floor-1)
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("want a refusal containing %q, got %v", want, err)
	}
	if strings.Contains(err.Error(), realmNameGo(catalog, 3)) {
		t.Fatalf("the refusal names the qi stage the cultivator was not measured at: %v", err)
	}
	if row := actionScalar(t, path, `SELECT sect_name FROM sect_membership WHERE user_id=42`); fmt.Sprint(row) != "Azure Cloud Sect" {
		t.Fatalf("a refused ascent moved the membership to %v", row)
	}
}

// Every sect below the top world names a sect whose gate stands in the world
// above its own, so the way up is a road and never a sidestep; the top
// world's sects and the hidden one name nothing.
func TestTheWayUpAlwaysClimbsOneWorld(t *testing.T) {
	catalog := crossingCatalog(t)
	worldOrder := map[string]int{}
	for _, realm := range catalog.Realms {
		if _, seen := worldOrder[realm.World]; !seen {
			worldOrder[realm.World] = len(worldOrder)
		}
	}
	top := ""
	for world, order := range worldOrder {
		if order == len(worldOrder)-1 {
			top = world
		}
	}
	named := 0
	for name, def := range catalog.Sects {
		gate := sectGate(catalog, name)
		if def.Hidden || gate == "" {
			if def.AscendsTo != "" {
				t.Errorf("%s has no public gate and still names %s above it", name, def.AscendsTo)
			}
			continue
		}
		world := catalog.Locations[gate].World
		if world == top {
			if def.AscendsTo != "" {
				t.Errorf("%s stands in the top world and names %s above it", name, def.AscendsTo)
			}
			continue
		}
		if def.AscendsTo == "" {
			t.Errorf("%s stands in the %s and names no sect above it: a member there can never climb", name, world)
			continue
		}
		aboveGate := sectGate(catalog, def.AscendsTo)
		if aboveGate == "" {
			t.Errorf("%s names %s, which keeps no gate", name, def.AscendsTo)
			continue
		}
		if worldOrder[catalog.Locations[aboveGate].World] != worldOrder[world]+1 {
			t.Errorf("%s (%s) names %s (%s), which is not the world above", name, world, def.AscendsTo, catalog.Locations[aboveGate].World)
		}
		named++
	}
	if named < 10 {
		t.Fatalf("only %d sects name a sect above them; the content read is broken, not the tree", named)
	}
}
