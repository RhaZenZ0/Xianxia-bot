package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The qi body (v1.0.0-rc.7): the three dantian, the hundred and eight
// meridians, the purity of what is held, and every content qi number read as
// a share of the pool rather than as a flat count.

func qiBodyCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func TestTheDantianWidensWithTheRealmTheChannelsAndTheMethod(t *testing.T) {
	catalog := qiBodyCatalog(t)
	ordinary := qiBody{Purity: purityStart, MeridiansOpen: meridianStartOpen, DantianState: "intact"}

	// The pool climbs the same ladder the cultivation does.
	previous := int64(0)
	for realm := int64(0); realm < int64(len(catalog.Realms)); realm++ {
		capacity := qiCapacityFor(catalog.Realms, realm, 1, 10, ordinary, "")
		if capacity <= previous {
			t.Fatalf("realm %d holds %d, no more than %d", realm, capacity, previous)
		}
		previous = capacity
	}
	// A Qi Condensation cultivator holds hundreds; a Nascent Soul one holds
	// tens of thousands. The genre's numbers, not the old pool of thirty.
	if first := qiCapacityFor(catalog.Realms, 1, 1, 10, ordinary, ""); first < 300 {
		t.Fatalf("Qi Condensation holds %d", first)
	}
	if nascent := qiCapacityFor(catalog.Realms, 4, 9, 10, ordinary, ""); nascent < 10000 {
		t.Fatalf("Nascent Soul holds %d", nascent)
	}
	// A later stage of the same realm holds more than the first.
	if early, late := qiCapacityFor(catalog.Realms, 2, 1, 10, ordinary, ""), qiCapacityFor(catalog.Realms, 2, 9, 10, ordinary, ""); late <= early {
		t.Fatalf("the stage must widen the dantian: %d -> %d", early, late)
	}

	base := qiCapacityFor(catalog.Realms, 2, 5, 10, ordinary, "")
	wider := ordinary
	wider.MeridiansOpen = 40
	if open := qiCapacityFor(catalog.Realms, 2, 5, 10, wider, ""); open <= base {
		t.Fatalf("open channels must widen the dantian: %d -> %d", base, open)
	}
	if dao := qiCapacityFor(catalog.Realms, 2, 5, 10, ordinary, "Dao"); dao <= qiCapacityFor(catalog.Realms, 2, 5, 10, ordinary, "Mortal") {
		t.Fatalf("a Dao method must hold more than a mortal one: %d", dao)
	}
	cracked := ordinary
	cracked.DantianState = "cracked"
	if hurt := qiCapacityFor(catalog.Realms, 2, 5, 10, cracked, ""); hurt >= base {
		t.Fatalf("a cracked vessel must hold less: %d vs %d", hurt, base)
	}
	// And the channels quicken the refill as well as widen the pool.
	if fast, slow := qiRegenPerGameMinute(base, wider, ""), qiRegenPerGameMinute(base, ordinary, ""); fast <= slow {
		t.Fatalf("open channels must quicken the refill: %v vs %v", fast, slow)
	}
	torn := ordinary
	torn.MeridiansDamaged = 1
	if hurt := qiRegenPerGameMinute(base, torn, ""); hurt >= qiRegenPerGameMinute(base, ordinary, "")*0.75 {
		t.Fatalf("a rupture must halve the recovery: %v", hurt)
	}
}

func TestAContentQiNumberIsTheSameShareOfEveryPool(t *testing.T) {
	catalog := qiBodyCatalog(t)
	clean := qiBody{Purity: purityCap, MeridiansOpen: meridianStartOpen, DantianState: "intact"}
	reference := referenceQiPool(10)
	// The pool a content number was written for: 8 + 2 x spirit, never less
	// than the fourteen the old flat session was worth.
	if reference != 28 || referenceQiPool(0) != legacyQiBaseline {
		t.Fatalf("reference pool: %v / %v", reference, referenceQiPool(0))
	}

	// A cost of 8 against a pool of 28 is two sevenths of the dantian, and it
	// stays two sevenths at the first realm and at the fifth. This is the
	// whole point of the rescale: the old prices keep biting.
	for _, realm := range []int64{0, 2, 5} {
		capacity := qiCapacityFor(catalog.Realms, realm, 5, 10, clean, "")
		cost := scaledQiCost(8, capacity, reference, clean)
		share := float64(cost) / float64(capacity)
		if share < 0.28 || share > 0.29 {
			t.Fatalf("realm %d: %d of %d is %.3f of the pool", realm, cost, capacity, share)
		}
	}
	// No content number may ever ask for more than half the dantian.
	capacity := qiCapacityFor(catalog.Realms, 1, 1, 10, clean, "")
	if huge := scaledQiCost(9999, capacity, reference, clean); huge != maxI64(1, int64(float64(capacity)*qiCostShareCeiling)) && float64(huge)/float64(capacity) > qiCostShareCeiling+0.001 {
		t.Fatalf("the ceiling must hold: %d of %d", huge, capacity)
	}
	// Impure qi costs more, and a torn channel doubles it again.
	dirty := clean
	dirty.Purity = purityStart
	if clean.skillCostMultiplier() != 1.0 || dirty.skillCostMultiplier() != 1.5 {
		t.Fatalf("purity multipliers: %v / %v", clean.skillCostMultiplier(), dirty.skillCostMultiplier())
	}
	torn := dirty
	torn.MeridiansDamaged = 1
	if torn.skillCostMultiplier() != 3.0 {
		t.Fatalf("a rupture must double the cost: %v", torn.skillCostMultiplier())
	}
	if scaledQiCost(8, capacity, reference, dirty) <= scaledQiCost(8, capacity, reference, clean) {
		t.Fatalf("impure qi must cost more")
	}
	// A pill restores by share too, but purity does not enter it.
	//
	// Asserted through qiState, because that is what carries the body.
	// scaledQiRestore takes no qiBody, so the line that used to stand here
	// compared it with itself and could not fail - it read as a test of the
	// sentence above and tested nothing. What is worth pinning is that the
	// same pill gives a torn cultivator exactly what it gives a clean one,
	// while the same skill costs them more.
	cleanState := qiState{Body: clean, Capacity: capacity, Reference: reference}
	tornState := qiState{Body: torn, Capacity: capacity, Reference: reference}
	if cleanState.Cost(8) >= tornState.Cost(8) {
		t.Fatalf("a torn channel must cost more: %d vs %d", cleanState.Cost(8), tornState.Cost(8))
	}
	if cleanState.Restore(8) != tornState.Restore(8) {
		t.Fatalf("purity entered a restore: %d clean vs %d torn", cleanState.Restore(8), tornState.Restore(8))
	}
	// And it is a share of the pool rather than a flat number: a dantian twice
	// the size takes twice as much from the same pill, give or take the one
	// unit that rounding a doubled share can move.
	wide := qiState{Body: clean, Capacity: capacity * 2, Reference: reference}
	if drift := wide.Restore(8) - cleanState.Restore(8)*2; drift > 1 || drift < -1 {
		t.Fatalf("a restore is not a share of the pool: %d of %d, but %d of %d",
			cleanState.Restore(8), capacity, wide.Restore(8), capacity*2)
	}
	if cleanState.Restore(0) != 0 {
		t.Fatal("a pill worth nothing must restore nothing")
	}
}

func TestTheDantianRefillsAsTheWorldTurns(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=2,phase=4,qi=0,qi_max=0 WHERE user_id=42`)

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	// The first settle writes the pool and the mark it settled at.
	first, err := settleQi(conn, catalog, 42, 1000, 1)
	if err != nil {
		t.Fatal(err)
	}
	if first.Capacity < qiCapacityFloor || first.Regen <= 0 {
		t.Fatalf("first settle: %d qi of %d, +%v", first.Qi, first.Capacity, first.Regen)
	}
	// Four game hours of an empty dantian fill it.
	later, err := settleQi(conn, catalog, 42, 1000+qiRefillGameMinutes, 2)
	if err != nil {
		t.Fatal(err)
	}
	if later.Qi <= first.Qi || later.Qi < later.Capacity/2 {
		t.Fatalf("four game hours should fill it: %d -> %d of %d", first.Qi, later.Qi, later.Capacity)
	}
	if later.Qi > later.Capacity {
		t.Fatalf("the pool must never overflow: %d of %d", later.Qi, later.Capacity)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT qi_max FROM characters WHERE user_id=42`)); got != later.Capacity {
		t.Fatalf("the settle writes qi_max: %d want %d", got, later.Capacity)
	}
}

func TestForcingAMeridianOpenSpendsInsightAndQi(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=2,phase=4,insight_xp=20,qi=0,qi_max=0 WHERE user_id=42`)

	// An empty dantian opens nothing.
	if _, err := batch4ApplyErr(path, world, "meridian.open", 42, 1, map[string]any{}); err == nil || !strings.Contains(err.Error(), "needs") {
		t.Fatalf("an empty dantian must refuse: %v", err)
	}
	status := cultivationQuery(t, path, world, "qi.status", 42)
	if storage.ParseInt(status["meridians_open"]) != meridianStartOpen {
		t.Fatalf("a new cultivator starts with twelve channels: %v", status["meridians_open"])
	}
	// The old pool a character predating the qi body carries: the settle
	// rescales what they were holding into the dantian they now have.
	batch4Exec(t, path, `UPDATE characters SET qi=28,qi_max=28 WHERE user_id=42`)

	before := storage.ParseInt(status["meridian_open_cost"])
	if before != meridianOpenCost(meridianStartOpen) {
		t.Fatalf("the next channel costs %d", before)
	}
	out := batch4Result(t, batch4Apply(t, path, world, "meridian.open", 2, map[string]any{}))
	if storage.ParseInt(out["insight_spent"]) != before || storage.ParseInt(out["qi_spent"]) <= 0 {
		t.Fatalf("forcing a channel spends both: %v", out)
	}
	if storage.ParseInt(out["meridian_ceiling"]) != meridianCeiling {
		t.Fatalf("a cultivator holds a hundred and eight channels: %v", out["meridian_ceiling"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT insight_xp FROM characters WHERE user_id=42`)); got != 20-before {
		t.Fatalf("insight_xp=%d", got)
	}
	opened := storage.ParseInt(out["meridians_open"])
	if out["success"] == true && opened != meridianStartOpen+1 {
		t.Fatalf("a success opens one: %d", opened)
	}
	if out["success"] == false && opened != meridianStartOpen {
		t.Fatalf("a failure opens none: %d", opened)
	}

	// A crossed stage opens one without any of that.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	grown, err := openMeridianOnStage(conn, 42, 3)
	if err != nil {
		t.Fatal(err)
	}
	if grown != opened+1 {
		t.Fatalf("a crossed stage opens a channel: %d -> %d", opened, grown)
	}
	// A rupture is what a severe deviation leaves, and it blocks the next.
	if _, err = damageMeridian(conn, 42, 4); err != nil {
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if _, err := batch4ApplyErr(path, world, "meridian.open", 42, 5, map[string]any{}); err == nil || !strings.Contains(err.Error(), "healed") {
		t.Fatalf("a torn channel must be mended first: %v", err)
	}
	// Mending costs spirit stones and clears it.
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=500 WHERE user_id=42`)
	healed := batch4Result(t, batch4Apply(t, path, world, "meridian.heal", 6, map[string]any{}))
	if healed["mended"] != "meridian" || storage.ParseInt(healed["meridians_damaged"]) != 0 {
		t.Fatalf("heal: %v", healed)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)); got != 460 {
		t.Fatalf("spirit_stones=%d", got)
	}
}

func TestRefiningCleansTheQiAndCheapensEveryTechnique(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := qiBodyCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=2,phase=4,qi=0,qi_max=0 WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE characters SET qi=28,qi_max=28 WHERE user_id=42`)

	out := batch4Result(t, batch4Apply(t, path, world, "qi.refine", 1, map[string]any{"cooldown_seconds": 1}))
	gain := storage.ParseInt(out["purity_gain"])
	if gain < 2 || gain > 4 {
		t.Fatalf("a refining session is worth two to four points: %d", gain)
	}
	if storage.ParseInt(out["purity"]) != purityStart+gain || storage.ParseInt(out["qi_spent"]) <= 0 {
		t.Fatalf("refine: %v", out)
	}
	// The ceiling is the realm and the method, and refining stops at it.
	ceiling := storage.ParseInt(out["purity_ceiling"])
	if ceiling != purityCeilingFor(catalog, 2, "", qiBody{QiType: spiritQiType}) {
		t.Fatalf("ceiling %d", ceiling)
	}
	if purityCeilingFor(catalog, 2, "Dao", qiBody{QiType: spiritQiType}) <= purityCeilingFor(catalog, 2, "", qiBody{QiType: spiritQiType}) {
		t.Fatalf("a better method must raise the ceiling")
	}
	batch4Exec(t, path, `UPDATE character_qi_body SET purity=? WHERE user_id=42`, ceiling)
	batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "qi.refine", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "as clean as") {
		t.Fatalf("a full ceiling must refuse: %v", err)
	}
	// And forcing the gathering is what dirties it again.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err = losePurity(conn, 42, 3, 5); err != nil {
		t.Fatal(err)
	}
	body, err := loadQiBody(conn, 42)
	if err != nil {
		t.Fatal(err)
	}
	if body.Purity != ceiling-3 {
		t.Fatalf("forcing costs purity: %d", body.Purity)
	}
	// Never below the floor, however hard it is forced.
	if err = losePurity(conn, 42, 500, 6); err != nil {
		t.Fatal(err)
	}
	if body, _ = loadQiBody(conn, 42); body.Purity != purityFloor {
		t.Fatalf("the floor holds: %d", body.Purity)
	}
}

func TestTheSheetCarriesTheWholeQiBodyInOneQuery(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=4,phase=2,qi=0,qi_max=0 WHERE user_id=42`)

	status := cultivationQuery(t, path, world, "qi.status", 42)
	for _, key := range []string{"qi", "qi_max", "regen_per_game_minute", "purity", "purity_ceiling",
		"skill_cost_mult", "meridians_open", "meridians_damaged", "meridian_ceiling",
		"meridian_open_cost", "dantian_state", "upper_open", "sense_reach", "breakthrough_qi_cost"} {
		if _, ok := status[key]; !ok {
			t.Fatalf("the sheet needs %s", key)
		}
	}
	// The upper dantian opens at Nascent Soul and the sense reaches further
	// the cleaner the qi is.
	if status["upper_open"] != true || storage.ParseInt(status["sense_reach"]) <= 0 {
		t.Fatalf("the upper dantian opens at Nascent Soul: %v", status)
	}
	if upperDantianOpen(3) || !upperDantianOpen(4) {
		t.Fatal("the upper dantian opens at realm four, not before")
	}
	if spiritualSenseReach(3, 100) != 0 || spiritualSenseReach(5, 100) <= spiritualSenseReach(5, 50) {
		t.Fatal("a cleaner sense reaches further")
	}
	// The breakthrough asks for a quarter of the pool.
	if storage.ParseInt(status["breakthrough_qi_cost"]) != maxI64(1, storage.ParseInt(status["qi_max"])/breakthroughQiShare) {
		t.Fatalf("a breakthrough costs a quarter: %v of %v", status["breakthrough_qi_cost"], status["qi_max"])
	}
	// The cultivation sheet carries it too, so the panel needs one call.
	sheet := cultivationQuery(t, path, world, "cultivation.status", 42)
	for _, key := range []string{"qi", "qi_max", "qi_regen", "purity", "purity_ceiling", "skill_cost_mult",
		"meridians_open", "meridians_damaged", "meridian_ceiling", "dantian_state", "breakthrough_qi_cost"} {
		if _, ok := sheet[key]; !ok {
			t.Fatalf("the cultivation sheet needs %s", key)
		}
	}
}
