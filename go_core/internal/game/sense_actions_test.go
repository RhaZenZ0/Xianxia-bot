package game

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// cave_abodes and deployed_location_arrays come with the batch-4 database;
// the abode there predates the columns the ground pricing reads, and the sect
// residence is not built at all.
const senseGroundDDL = `ALTER TABLE cave_abodes ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE cave_abodes ADD COLUMN cultivation_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN formation_level INTEGER NOT NULL DEFAULT 0;
CREATE TABLE sect_abodes(
    location_key TEXT NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',
    cultivation_level INTEGER NOT NULL DEFAULT 0, formation_level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(location_key,user_id));`

func senseGroundConn(t *testing.T) (*storage.Conn, worlddata.Catalog) {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := conn.ExecScript(senseGroundDDL); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return conn, catalog
}

func senseRoll(success bool) map[string]any { return map[string]any{"success": success} }
func sensePrec(tier string) map[string]any  { return map[string]any{"tier": tier} }

// The detail a sweep earns is the precision tier: the word for the ground, then
// what is making it so, then the numbers. This is the qualitative-to-precise
// ladder the sense system is supposed to walk and could not, because the sweep
// never read the ground at all.
func TestSenseGroundReadingDetailFollowsPrecision(t *testing.T) {
	conn, catalog := senseGroundConn(t)
	sensor := senseCharacter{Location: "Greenriver Town"}
	for _, tc := range []struct {
		tier, detail string
		wantGround   bool
		wantNumbers  bool
	}{
		{"partial", "vague", false, false},
		{"success", "named", true, false},
		{"strong", "named", true, false},
		{"overwhelming", "exact", true, true},
	} {
		got, err := senseGroundReading(conn, catalog, 1, sensor, 600, senseRoll(true), sensePrec(tc.tier))
		if err != nil {
			t.Fatalf("%s: %v", tc.tier, err)
		}
		if got == nil {
			t.Fatalf("%s: no ground reading at all", tc.tier)
		}
		if got["detail"] != tc.detail {
			t.Errorf("%s: detail = %v, want %s", tc.tier, got["detail"], tc.detail)
		}
		if _, ok := got["quality"]; !ok {
			t.Errorf("%s: a readable sweep always names the quality", tc.tier)
		}
		if _, ok := got["ground"]; ok != tc.wantGround {
			t.Errorf("%s: ground named = %v, want %v", tc.tier, ok, tc.wantGround)
		}
		if _, ok := got["multiplier"]; ok != tc.wantNumbers {
			t.Errorf("%s: multiplier given = %v, want %v", tc.tier, ok, tc.wantNumbers)
		}
	}
}

// A sweep that failed, or whose detail roll failed outright, reads nothing -
// it must not quietly hand over the ground it could not feel.
func TestSenseGroundReadingIsSilentOnFailure(t *testing.T) {
	conn, catalog := senseGroundConn(t)
	sensor := senseCharacter{Location: "Greenriver Town"}
	for _, tc := range []struct {
		name    string
		roll    map[string]any
		tier    string
		wantNil bool
	}{
		{"sweep failed", senseRoll(false), "overwhelming", true},
		{"detail failed", senseRoll(true), "failure", true},
		{"detail critical", senseRoll(true), "critical_failure", true},
		{"both fine", senseRoll(true), "success", false},
	} {
		got, err := senseGroundReading(conn, catalog, 1, sensor, 600, tc.roll, sensePrec(tc.tier))
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		if (got == nil) != tc.wantNil {
			t.Errorf("%s: nil = %v, want %v", tc.name, got == nil, tc.wantNil)
		}
	}
}

// The reading is the cultivation engine's own number, not a second model of
// it: a gathering array raised at home has to move what the sweep reports, or
// a player is being told one thing and paid another.
func TestSenseGroundReadingTracksTheCultivationMultiplier(t *testing.T) {
	conn, catalog := senseGroundConn(t)
	sensor := senseCharacter{Location: "Greenriver Town"}
	plain, err := senseGroundReading(conn, catalog, 1, sensor, 600, senseRoll(true), sensePrec("overwhelming"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO cave_abodes(user_id,location_key,base_location,name,cultivation_level,formation_level)
		 VALUES(?,?,?,?,?,?)`,
		[]any{1, "Greenriver Town", "Greenriver Town", "Cloudrest Cave", 5, 9}); err != nil {
		t.Fatal(err)
	}
	rich, err := senseGroundReading(conn, catalog, 1, sensor, 600, senseRoll(true), sensePrec("overwhelming"))
	if err != nil {
		t.Fatal(err)
	}
	name, mult, err := placeCultivationMultiplier(conn, catalog, 1, sensor.Location, 600)
	if err != nil {
		t.Fatal(err)
	}
	if rich["multiplier"] != mult {
		t.Errorf("sweep reports x%v, the engine will actually pay x%v", rich["multiplier"], mult)
	}
	if rich["ground"] != name {
		t.Errorf("sweep names %q, the engine calls it %q", rich["ground"], name)
	}
	if plain["multiplier"] == rich["multiplier"] {
		t.Errorf("the cave and its finished array changed nothing: still x%v", plain["multiplier"])
	}
	if rich["quality"] != placeQuality(mult) {
		t.Errorf("quality %q is not the word the Here line uses (%q)", rich["quality"], placeQuality(mult))
	}
}

// Two of the five readings /sense can give were unreachable: resolving *what* a
// target is got 2 harder a realm while detecting them at all got 7, so by the
// time the detail roll was marginal the detection roll had already failed. The
// slope is what reopens them, so it is pinned here rather than left to drift.
func TestPrecisionSlopeMakesTheMiddleReadingsReachable(t *testing.T) {
	if precisionPerTargetRealm <= precisionPerAreaRealm {
		t.Fatalf("a cultivator must be harder to resolve than a place: %d vs %d",
			precisionPerTargetRealm, precisionPerAreaRealm)
	}
	got := precisionResult(5, 5, 20, 4, precisionPerTargetRealm, 0)
	if want := int64(10 + 4*precisionPerTargetRealm); got["tn"] != want {
		t.Errorf("tn = %v, want %d", got["tn"], want)
	}
	if got := precisionResult(5, 5, 20, 4, precisionPerAreaRealm, 2); got["tn"] != int64(10+4*precisionPerAreaRealm+2) {
		t.Errorf("the area sweep kept its own slope: tn = %v", got["tn"])
	}

	// Walk the ladder the way a player does and count which readings come up.
	// Both middle readings must actually occur against a plausible opponent.
	seen := map[string]bool{}
	for realm := int64(0); realm < 32; realm++ {
		for target := int64(0); target < 32; target++ {
			spirit, will, insight, phase := 3+realm, 2+realm, int64(3), int64(5)
			power := 4 + spirit*3 + will + realm*6 + phase - 1
			precision := 3 + insight*3 + spirit + realm*4 + (phase-1)/2
			// The real concealment rule, not a copy of it: the two slopes
			// are tuned against each other, so this must move when it does.
			detTN := 10 + concealmentPowerGo(senseCharacter{
				Realm: target, Phase: phase, Spirit: 3 + target, Will: 2 + target,
				ConcealmentActive: true,
			})
			for d1 := int64(1); d1 <= 10; d1++ {
				for d2 := int64(1); d2 <= 10; d2++ {
					det := senseTier(d1 + d2 + power - detTN)
					pt := precisionResult(d1, d2, precision, target, precisionPerTargetRealm, 0)["tier"].(string)
					switch {
					case det == "partial" || pt == "critical_failure" || pt == "failure":
						seen["world"] = true
					case det != "critical_failure" && det != "failure":
						switch pt {
						case "partial":
							seen["realm"] = true
						case "success", "strong":
							seen["approx"] = true
						default:
							seen["exact"] = true
						}
					}
				}
			}
		}
	}
	for _, reading := range []string{"exact", "approx", "realm", "world"} {
		if !seen[reading] {
			t.Errorf("the reading %q is unreachable - the ladder is not being walked", reading)
		}
	}
}

// Hiding has to cost something or it is not a decision: it was a free toggle
// with no reason to ever be off. A folded aura does not reach as far.
func TestConcealmentCostsTheSensorTheirOwnReach(t *testing.T) {
	conn, catalog := senseGroundConn(t)
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,
		 realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,
		 karma_score,qi,qi_max,vitality,vitality_max)
		 VALUES(?,?,'male','Qi Refiner','Single',?,?,?,?,0,0,1,0,'alive',0,10,10,10,10)`,
		[]any{4242, "Hidden", "Greenriver Town", `{"spirit":9,"insight":3,"will":8}`, 6, 5}); err != nil {
		t.Fatal(err)
	}
	_, openPower, openPrecision, openRange, err := senseStatsGo(conn, catalog, 4242, 600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE characters SET concealment_active=1 WHERE user_id=?`, []any{4242}); err != nil {
		t.Fatal(err)
	}
	_, hidPower, hidPrecision, hidRange, err := senseStatsGo(conn, catalog, 4242, 600)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		what            string
		open, concealed int64
	}{
		{"power", openPower, hidPower},
		{"precision", openPrecision, hidPrecision},
		{"range", openRange, hidRange},
	} {
		if tc.concealed >= tc.open {
			t.Errorf("%s: concealed %d is not below open %d - hiding is still free",
				tc.what, tc.concealed, tc.open)
		}
		if want := tc.open * concealedSenseNumerator / concealedSenseDenominator; tc.concealed != want {
			t.Errorf("%s: concealed %d, want %d", tc.what, tc.concealed, want)
		}
	}
}

// Concealment rose slower than the power that pierces it, so it stopped
// meaning anything between peers: from realm 4 a concealed cultivator was read
// exactly every single time. Hiding has to keep its worth up the whole ladder.
func TestConcealmentHoldsItsWorthUpTheLadder(t *testing.T) {
	for _, realm := range []int64{0, 4, 8, 16, 31} {
		target := senseCharacter{Realm: realm, Phase: 5, Spirit: 3 + realm, Will: 2 + realm, ConcealmentActive: true}
		sensor := senseCharacter{Realm: realm, Phase: 5, Spirit: 3 + realm, Will: 2 + realm}
		power := 4 + sensor.Spirit*3 + sensor.Will + realm*6 + sensor.Phase - 1
		tn := 10 + concealmentPowerGo(target)
		stopped := 0
		for d1 := int64(1); d1 <= 10; d1++ {
			for d2 := int64(1); d2 <= 10; d2++ {
				switch senseTier(d1 + d2 + power - tn) {
				case "failure", "critical_failure", "partial":
					stopped++
				}
			}
		}
		if stopped < 10 {
			t.Errorf("realm %d: concealment stopped or blurred only %d%% of probes - "+
				"hiding has stopped mattering at this realm", realm, stopped)
		}
	}
}

// A sense reached across the whole world: /sense on a player checked nothing,
// while sensing an NPC already required standing with them, and range_m was
// computed in detail and then only printed.
func TestSenseReachFollowsTheMapAndTheRange(t *testing.T) {
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	here := "Greenriver Town"
	loc, ok := catalog.Locations[here]
	if !ok || len(loc.Roads) == 0 {
		t.Skip("the fixture world has no road out of " + here)
	}
	neighbour := loc.Roads[0]

	if !senseReaches(catalog, here, here, 1) {
		t.Error("the place you are standing in is always within reach")
	}
	if senseReaches(catalog, here, neighbour, senseNeighbourRangeMeters-1) {
		t.Error("a short sense reached the next place along the road")
	}
	if !senseReaches(catalog, here, neighbour, senseNeighbourRangeMeters) {
		t.Error("a long sense could not reach the next place along the road")
	}
	far := ""
	for name := range catalog.Locations {
		if name == here || name == neighbour {
			continue
		}
		joined := false
		for _, road := range loc.Roads {
			if road == name {
				joined = true
			}
		}
		for _, behind := range loc.Gates {
			for _, n := range behind {
				if n == name {
					joined = true
				}
			}
		}
		if !joined {
			far = name
			break
		}
	}
	if far == "" {
		t.Skip("the fixture world is fully connected to " + here)
	}
	if senseReaches(catalog, here, far, 1<<40) {
		t.Errorf("an unbounded sense reached %q, which no road leaves here for", far)
	}
}

// The engine told the sensor "the target immediately feels your probing sense"
// and nothing anywhere backed it: a probe was silent, so there was no
// counter-play to it at all.
func TestSenseTargetNoticesAProbeItCanFeel(t *testing.T) {
	for _, tc := range []struct {
		name                     string
		sensorPower, targetPower int64
		reveal                   string
		want                     bool
	}{
		{"read by someone far sharper, only partly", 200, 20, "world", false},
		{"read to the dantian", 200, 20, "exact", true},
		{"the one being read is just as perceptive", 100, 100, "world", true},
		{"the one being read is sharper", 60, 100, "none", true},
	} {
		if got := senseTargetNotices(tc.sensorPower, tc.targetPower, tc.reveal); got != tc.want {
			t.Errorf("%s: noticed = %v, want %v", tc.name, got, tc.want)
		}
	}
}
