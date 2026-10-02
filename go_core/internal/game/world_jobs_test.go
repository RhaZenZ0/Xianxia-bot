package game

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Each world above the Mortal has a job (v1.17.0). The world-flow study
// called the three upper worlds "the Mortal World again with less in it":
// the same eleven cities and shops, and nothing a rule read that the Mortal
// World did not also have. Each carries four things of its own now - a
// district kind read by one rule and standing in no other world, a raid boss
// in the wilds of one of its cities, a secret realm at that wild place, and a
// key to it on the shelves of the world's array workshops - and these tests
// hold the content to that shape and drive each rule where it lives.

const mortalWorld = "Mortal World"

func upperWorlds(catalog worlddata.Catalog) []string {
	seen := map[string]bool{}
	for _, loc := range catalog.Locations {
		if loc.World != "" && loc.World != mortalWorld {
			seen[loc.World] = true
		}
	}
	out := make([]string, 0, len(seen))
	for w := range seen {
		out = append(out, w)
	}
	sort.Strings(out)
	return out
}

// jobDistrictOf answers the one district kind that stands in this world and
// nowhere else, read off the content rather than a list.
func jobDistrictOf(catalog worlddata.Catalog, world string) (kind, name string) {
	worldsByKind := map[string]map[string]bool{}
	namesByKind := map[string]string{}
	for place, loc := range catalog.Locations {
		if loc.District == "" || loc.District == "gate" || loc.District == "inn" {
			continue
		}
		if worldsByKind[loc.District] == nil {
			worldsByKind[loc.District] = map[string]bool{}
		}
		worldsByKind[loc.District][loc.World] = true
		if loc.World == world {
			namesByKind[loc.District] = place
		}
	}
	for k, worlds := range worldsByKind {
		if len(worlds) == 1 && worlds[world] {
			return k, namesByKind[k]
		}
	}
	return "", ""
}

func TestEachUpperWorldHasAJob(t *testing.T) {
	catalog := crossingCatalog(t)
	worlds := upperWorlds(catalog)
	if len(worlds) != 3 {
		t.Fatalf("the catalogue has %d worlds above the Mortal, not three; the check is broken, not the tree", len(worlds))
	}
	// The kinds the engine reads, so a district kind unique to a world that
	// no rule reads is a decoration rather than a job.
	readers := map[string]func(worlddata.Catalog, string) (string, int64){
		districtArchive: lawPlaceBonus, districtCourt: craftPlaceBonus, districtAltar: breakthroughPlaceBonus,
	}
	for _, world := range worlds {
		kind, district := jobDistrictOf(catalog, world)
		if kind == "" {
			t.Errorf("%s has no district kind of its own", world)
			continue
		}
		reader, ok := readers[kind]
		if !ok {
			t.Errorf("%s's own district kind %q is read by no rule", world, kind)
			continue
		}
		if name, bonus := reader(catalog, district); name != district || bonus <= 0 {
			t.Errorf("%s: the reader of %q answers (%q, %d) at %s", world, kind, name, bonus, district)
		}
		for other, otherReader := range readers {
			if other != kind {
				if name, bonus := otherReader(catalog, district); name != "" || bonus != 0 {
					t.Errorf("%s answers another world's rule (%q) with (%q, %d)", district, other, name, bonus)
				}
			}
		}
		// A raid of its own, in the wilds of one of its cities.
		var bosses []string
		for key, template := range bossTemplatesGo {
			lair, _ := bossLair(catalog, template)
			if loc, ok := catalog.Locations[lair]; ok && loc.World == world {
				bosses = append(bosses, key)
				if loc.WildsOf == "" {
					t.Errorf("%s's raid %s stands at %s, which is not in the wilds of a city", world, key, lair)
				}
				if template.RealmIndex < worldMinRealm(catalog, world) {
					t.Errorf("%s's raid %s asks realm %d, below the world's floor", world, key, template.RealmIndex)
				}
			}
		}
		if len(bosses) != 1 {
			t.Errorf("%s has %d raids (%v), want one", world, len(bosses), bosses)
		}
		// A keyed realm of its own, the key on a shelf in that world.
		var keyed []string
		for id, item := range catalog.Items {
			rid, _ := item.SpatialKey["secret_realm_id"].(string)
			realm, ok := catalog.SecretRealms[rid]
			if !ok {
				continue
			}
			entrance, ok := catalog.Locations[realm.Location]
			if !ok || entrance.World != world {
				continue
			}
			keyed = append(keyed, id)
			if entrance.WildsOf == "" {
				t.Errorf("%s's keyed realm %s opens at %s, which is not in the wilds of a city", world, rid, realm.Location)
			}
			sold := false
			for _, shop := range catalog.Shops {
				for _, line := range shop.Sells {
					if line.ItemID == id && shop.World == world {
						sold = true
					}
				}
			}
			if !sold {
				t.Errorf("%s's key %s is on no shelf in that world", world, id)
			}
		}
		if len(keyed) != 1 {
			t.Errorf("%s has %d keys (%v), want one", world, len(keyed), keyed)
		}
	}
}

// A Law is comprehended more clearly under the sutra archive's roof: the
// result names the place and its bonus, and the roll's modifier carries it.
func TestALawIsReadMoreClearlyInTheArchive(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	_, archive := jobDistrictOf(catalog, "Spiritual World")
	if archive == "" || catalog.Locations[archive].District != districtArchive {
		t.Fatalf("the Spiritual World's own district is %q, not an archive", archive)
	}
	comprehend := func(step int, at string) map[string]any {
		batch4Exec(t, path, `UPDATE characters SET realm_index=8,location=? WHERE user_id=42`, at)
		batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
		return batch4Result(t, batch4Apply(t, path, world, "law.comprehend", step, map[string]any{"law": "fire", "game_minute": 200}))
	}
	street := comprehend(1, catalog.Locations[archive].OutsideLocation)
	inside := comprehend(2, archive)
	if storage.ParseInt(street["place_bonus"]) != 0 || fmt.Sprint(street["place"]) != "" {
		t.Fatalf("on the street the archive's bonus applied: %v %v", street["place"], street["place_bonus"])
	}
	if storage.ParseInt(inside["place_bonus"]) != lawArchiveBonus || fmt.Sprint(inside["place"]) != archive {
		t.Fatalf("under the archive's roof the result says place=%v bonus=%v; want %s +%d", inside["place"], inside["place_bonus"], archive, lawArchiveBonus)
	}
	streetRoll, _ := street["roll"].(map[string]any)
	insideRoll, _ := inside["roll"].(map[string]any)
	if storage.ParseInt(insideRoll["modifier"])-storage.ParseInt(streetRoll["modifier"]) != lawArchiveBonus {
		t.Fatalf("the roll's modifier is %v inside and %v outside; the archive is worth +%d and the roll did not carry it", insideRoll["modifier"], streetRoll["modifier"], lawArchiveBonus)
	}
}

// A thing made in the grandmasters' court is made better: the craft's
// context bonus carries the court's term, and the result names it.
func TestACraftIsMadeBetterInTheCourt(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	_, court := jobDistrictOf(catalog, "Immortal World")
	if court == "" || catalog.Locations[court].District != districtCourt {
		t.Fatalf("the Immortal World's own district is %q, not a court", court)
	}
	batch4TeachRecipe(t, path, 42, "Spirit-Iron Sword")
	craft := func(id, at string) map[string]any {
		batch4Exec(t, path, `UPDATE characters SET location=?, realm_index=16 WHERE user_id=42`, at)
		batch4Exec(t, path, `DELETE FROM inventory WHERE user_id=42`)
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',3),(42,'beast_core',1)`)
		raw, _ := json.Marshal(map[string]any{"recipe": "Spirit-Iron Sword"})
		out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: id, Operation: "craft.resolve", ActorID: 42, Payload: raw})
		if err != nil {
			t.Fatal(err)
		}
		return out.Result.(map[string]any)
	}
	street := craft("job-craft-street", catalog.Locations[court].OutsideLocation)
	inside := craft("job-craft-court", court)
	if storage.ParseInt(street["place_bonus"]) != 0 {
		t.Fatalf("on the street the court's bonus applied: %v", street["place_bonus"])
	}
	if storage.ParseInt(inside["place_bonus"]) != craftCourtBonus || fmt.Sprint(inside["place"]) != court {
		t.Fatalf("in the court the result says place=%v bonus=%v; want %s +%d", inside["place"], inside["place_bonus"], court, craftCourtBonus)
	}
	if storage.ParseInt(inside["context_bonus"])-storage.ParseInt(street["context_bonus"]) != craftCourtBonus {
		t.Fatalf("context_bonus is %v in the court and %v on the street; the court is worth +%d and the roll did not carry it", inside["context_bonus"], street["context_bonus"], craftCourtBonus)
	}
}

// A breakthrough on the heaven-reading altar is heard sooner: the odds the
// sheet shows and the roll itself both carry the altar's term, and the sheet
// names it among the movers.
func TestABreakthroughIsHeardSoonerOnTheAltar(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	_, altar := jobDistrictOf(catalog, "Celestial World")
	if altar == "" || catalog.Locations[altar].District != districtAltar {
		t.Fatalf("the Celestial World's own district is %q, not an altar", altar)
	}
	odds := func(at string) map[string]any {
		batch4Exec(t, path, `UPDATE characters SET realm_index=24,phase=3,location=? WHERE user_id=42`, at)
		status := cultivationQuery(t, path, world, "cultivation.status", 42)
		o, _ := status["odds"].(map[string]any)
		if o == nil {
			t.Fatalf("the sheet carries no odds at %s: %v", at, status)
		}
		return o
	}
	street := odds(catalog.Locations[altar].OutsideLocation)
	inside := odds(altar)
	if storage.ParseInt(inside["modifier"])-storage.ParseInt(street["modifier"]) != breakthroughAltarBonus {
		t.Fatalf("the odds' modifier is %v on the altar and %v on the street; the altar is worth +%d", inside["modifier"], street["modifier"], breakthroughAltarBonus)
	}
	// The result is read in-process, so the movers are the Go slice rather
	// than the JSON a client would see.
	named := false
	for _, mover := range inside["movers"].([]map[string]any) {
		if fmt.Sprint(mover["label"]) == altar && storage.ParseInt(mover["value"]) == breakthroughAltarBonus {
			named = true
		}
	}
	if !named {
		t.Fatalf("the sheet's movers do not name the altar: %v", inside["movers"])
	}
	// And the roll itself, with will 100 so it cannot fail and only the term is
	// in question: a stage within the realm, so no gate is asked.
	batch4Exec(t, path, `UPDATE characters SET cultivation=1000000000 WHERE user_id=42`)
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 1, map[string]any{"confirm": true}))
	if crossed["success"] != true {
		t.Fatalf("a will-100 breakthrough failed: %v", crossed)
	}
	if storage.ParseInt(crossed["place_bonus"]) != breakthroughAltarBonus || fmt.Sprint(crossed["place"]) != altar {
		t.Fatalf("the breakthrough's result says place=%v bonus=%v; want %s +%d", crossed["place"], crossed["place_bonus"], altar, breakthroughAltarBonus)
	}
	if storage.ParseInt(crossed["modifier"]) != storage.ParseInt(inside["modifier"]) {
		t.Fatalf("the roll's modifier (%v) is not the one the sheet showed (%v)", crossed["modifier"], inside["modifier"])
	}
}

// A raid's reward is paid in the money of the world the raider stands in.
// The claim paid the Mortal stone in every world, which was right only while
// every raid was the Mortal World's.
func TestARaidIsPaidInTheMoneyOfItsWorld(t *testing.T) {
	path := setupSoloRaidDB(t)
	catalog := crossingCatalog(t)
	syncPurse(t, path)
	// A claim staged in the Spiritual World, where the stag is fought.
	lair, _ := bossLair(catalog, bossTemplatesGo["hundred_horn_ancestor_stag"])
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, lair)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(7,42,'x','finished',0,0)`)
	batch4Exec(t, path, `INSERT INTO boss_encounters(encounter_id,party_id,template_key,location,boss_name,boss_hp,boss_hp_max,status,created_at,updated_at) VALUES(9,7,'hundred_horn_ancestor_stag',?,?,0,640,'victory',0,0)`, lair, bossTemplatesGo["hundred_horn_ancestor_stag"].Name)
	batch4Exec(t, path, `INSERT INTO boss_reward_claims(encounter_id,user_id,currency_amount,item_id,item_quantity,created_at) VALUES(9,42,300,'spirit_crystal_ore',4,0)`)
	raw, _ := json.Marshal(map[string]any{"id": 9})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossClaimActionGo(conn, catalog, 42, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	if err != nil {
		t.Fatalf("the claim was refused: %v", err)
	}
	want := WorldBaseCurrency(catalog, "Spiritual World")
	if want == "low_spirit_stone" || !strings.Contains(want, "spirit_crystal") {
		t.Fatalf("the Spiritual World's base currency reads as %q; the check is broken, not the tree", want)
	}
	if fmt.Sprint(out["currency"]) != want {
		t.Fatalf("the claim says it paid %v; a raid in the Spiritual World pays %s", out["currency"], want)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(balance),0) FROM currency_wallets WHERE user_id=42 AND currency_id=?`, want)); got != 300 {
		t.Fatalf("the purse holds %d %s after the claim, want 300", got, want)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(balance),0) FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got != 0 {
		t.Fatalf("the claim paid %d Mortal stones to a raider in the Spiritual World", got)
	}
}
