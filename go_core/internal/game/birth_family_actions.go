package game

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

type birthFamilyArchetype struct {
	ID            string
	Category      string
	Name          string
	Wealth        int64
	Influence     int64
	Stability     int64
	Tier          int64
	AlignmentBias int64
	Location      string
	Boon          string
	Risk          string
}

type bloodlineTrait struct {
	Name     string
	Affinity string
	Trait    string
}

type familyOffer struct {
	ChoiceID string `json:"choice_id"`
	FamilyID int64  `json:"family_id"`
	BirthFamily
}

type birthFamilyChoice struct {
	FamilyID int64
	BirthFamily
}

var birthFamilyMaleNames = []string{"Wei", "Jun", "Hao", "Tian", "Rui", "Feng", "Ming", "Bo", "Jian", "Kai"}
var birthFamilyFemaleNames = []string{"Mei", "Lan", "Yue", "Xue", "Ling", "Hua", "Ning", "Qiao", "Yan", "Rin"}
var birthFamilyBloodlines = []bloodlineTrait{
	{"Azure Wolf Bloodline", "Body", "Tracking instinct, endurance and coordinated hunting"},
	{"Vermilion Bird Emberline", "Fire", "Fire affinity and resilience to heat"},
	{"Black Tortoise Marrowline", "Earth", "Defense, vitality and patient cultivation"},
	{"White Tiger Warline", "Metal", "Battle instinct, killing intent and weapon affinity"},
	{"Moon Serpent Yin Line", "Water", "Yin sensitivity, concealment and poison resistance"},
	{"Thunder Roc Lineage", "Lightning", "Speed, lightning affinity and aerial techniques"},
	{"Jade River Spirit Line", "Water", "Water affinity, healing intuition and river-sense"},
	{"Stone Bear Ancestry", "Earth", "Powerful physique, endurance and mountain survival"},
}
var birthFamilyConfederacies = []string{
	"Nine Banners Martial Alliance", "Northern River Clan League", "Hundred Peaks Covenant",
	"Red Blade Council", "Jade Plains Alliance", "Three Valleys Blood-Oath League",
}

var starterBirthFamilySurnames = map[string]string{
	"martial_household":          "Han",
	"escort_martial_family":      "Chen",
	"weaponsmith_martial_family": "Wei",
	"body_tempering_family":      "Zhao",
	"sword_hall_family":          "Shen",
	"spear_guard_family":         "Lin",
	"hidden_weapon_family":       "Su",
	"border_garrison_family":     "Gu",
	"fallen_martial_clan":        "Luo",
	"noble_martial_clan":         "Qin",
	"alchemy_family":             "Bai",
	// v1.0.0-rc.8: the two households a ghost cultivator can be born into.
	"nether_market_house": "Ye",
	"tomb_watch_clan":     "Xie",
}
var birthFamilyArchetypes = []birthFamilyArchetype{
	{"martial_household", "martial", "Martial Household", 42, 48, 62, 2, 0, "Riverguard City", "Weapons, body-training knowledge, guards and combat-minded relatives.", "Feuds, injuries and expectations to defend the family name."},
	{"escort_martial_family", "martial", "Escort Agency Martial Family", 58, 46, 54, 2, 2, "Four-Roads Caravan City", "Travel contacts, caravan intelligence, practical combat training and escort equipment.", "Bandits, dangerous contracts, merchant enemies and relatives frequently traveling into danger."},
	{"weaponsmith_martial_family", "martial", "Weapon-Smith Martial Family", 52, 38, 68, 2, 0, "Emberforge City", "Forge access, weapon maintenance, ore contacts and relatives skilled in practical weapon arts.", "Expensive materials, workshop rivalries and pressure to protect valuable forging knowledge."},
	{"body_tempering_family", "martial", "Body-Tempering Martial Family", 36, 43, 70, 2, 1, "Stoneback Mountain City", "Strong physique traditions, medicinal baths, endurance training and experienced sparring partners.", "Harsh training, frequent injuries and a culture that respects strength above comfort."},
	{"sword_hall_family", "martial", "Sword Hall Martial Family", 48, 55, 58, 3, 3, "Cloudblade City", "Sword instructors, dueling contacts, old forms and a respected local martial reputation.", "Rival schools, formal challenges and pressure to uphold the family's sword reputation."},
	{"spear_guard_family", "martial", "Spear Guard Martial Family", 44, 59, 64, 3, 2, "Ironbanner City", "Formation fighting, guard service, disciplined training and strong ties to local officials.", "Military obligations, dangerous guard duty and political pressure from powerful patrons."},
	{"hidden_weapon_family", "martial", "Hidden-Weapon Martial Family", 50, 41, 50, 3, -3, "Moonfen City", "Concealed weapons, poison-resistance training, stealth methods and discreet underworld contacts.", "Suspicion from orthodox factions, secret feuds and dangerous family techniques."},
	{"border_garrison_family", "martial", "Border Garrison Martial Family", 40, 62, 52, 3, 0, "Frostwatch City", "Battlefield experience, armor and weapon access, scouts and strong defensive discipline.", "Beast attacks, border wars, casualties and long periods away from home."},
	{"fallen_martial_clan", "martial", "Fallen Martial Clan", 26, 36, 36, 2, -4, "Ashenwall City", "Old manuals, ruined training grounds, forgotten enemies and a chance to recover a lost martial inheritance.", "Debt, broken alliances, old grudges and internal pressure to restore the clan's former glory."},
	{"noble_martial_clan", "martial", "Noble Martial Clan", 82, 76, 46, 4, 0, "Azure Crown Imperial City", "Strong resources, martial tutors, political protection, retainers and access to better cultivation contacts.", "Succession disputes, family politics, powerful enemies and heavy expectations placed on talented descendants."},
	{"alchemy_family", "martial", "Alchemy Family", 61, 45, 67, 3, 2, "Jadewood Medicine City", "Medicinal herb gardens, furnace access, pill lore and relatives experienced in identifying and refining spirit medicines.", "Rare-herb debts, furnace accidents, pill poisoning and rival alchemists seeking the family's recipes."}, // v1.0.0-rc.8: born to death qi. Only these two households raise a
	// child who can walk the ghost path; nobody else may take it.
	{"nether_market_house", "ghost", "Nether-Market Household", 55, 30, 44, 2, -5, "Moonfen City", "Funeral rites, spirit money, corpse-brokerage, incense that carries and buyers no ledger names.", "Watched by the magistrate, hated by orthodox sects and owed favours by things that do not stay buried."},
	{"tomb_watch_clan", "ghost", "Tomb-Watch Clan", 31, 39, 58, 2, -2, "Ashenwall City", "A necropolis to keep, grave-lore passed down, wards against what wakes and death qi thick enough to breathe.", "Grave-robbers, restless occupants, a duty that cannot be set down and neighbours who cross the road."},
}

type birthFamilyHomelandProfile struct {
	Theme     string
	Climate   string
	Locations map[string]string
}

var birthFamilyHomelands = map[string]birthFamilyHomelandProfile{
	"martial_household": {Theme: "temperate river martial district", Climate: "temperate", Locations: map[string]string{
		"Mortal World":    "Riverguard City",
		"Spiritual World": "Jadeflow Spirit City",
		"Immortal World":  "Immortal River City",
		"Celestial World": "Celestial River City",
	}},
	"escort_martial_family": {Theme: "open-road trade crossroads", Climate: "warm-breezy", Locations: map[string]string{
		"Mortal World":    "Four-Roads Caravan City",
		"Spiritual World": "Galevein Spirit City",
		"Immortal World":  "Skyroad Immortal City",
		"Celestial World": "Starroad Celestial City",
	}},
	"weaponsmith_martial_family": {Theme: "forge and volcanic firelands", Climate: "hot", Locations: map[string]string{
		"Mortal World":    "Emberforge City",
		"Spiritual World": "Vermilion Furnace City",
		"Immortal World":  "Solar Furnace Immortal City",
		"Celestial World": "Solar Crucible Celestial City",
	}},
	"body_tempering_family": {Theme: "rugged mountain highlands", Climate: "cool-highland", Locations: map[string]string{
		"Mortal World":    "Stoneback Mountain City",
		"Spiritual World": "Stoneheart Spirit City",
		"Immortal World":  "Adamant Body Immortal City",
		"Celestial World": "Worldstone Celestial City",
	}},
	"sword_hall_family": {Theme: "high windy sword peaks", Climate: "cool-windy", Locations: map[string]string{
		"Mortal World":    "Cloudblade City",
		"Spiritual World": "Cloudedge Spirit City",
		"Immortal World":  "Heavenblade Immortal City",
		"Celestial World": "Firmament Blade City",
	}},
	"spear_guard_family": {Theme: "fortified dry plains", Climate: "dry-temperate", Locations: map[string]string{
		"Mortal World":    "Ironbanner City",
		"Spiritual World": "Spearwall Spirit City",
		"Immortal World":  "Golden Spear Immortal City",
		"Celestial World": "Mandate Spear City",
	}},
	"hidden_weapon_family": {Theme: "misty yin wetlands", Climate: "cold-damp", Locations: map[string]string{
		"Mortal World":    "Moonfen City",
		"Spiritual World": "Moonfrost Spirit City",
		"Immortal World":  "Lunar Veil Immortal City",
		"Celestial World": "Lunar Shadow Celestial City",
	}},
	"border_garrison_family": {Theme: "northern frozen frontier", Climate: "cold", Locations: map[string]string{
		"Mortal World":    "Frostwatch City",
		"Spiritual World": "Northwind Spirit City",
		"Immortal World":  "Polar Gate Immortal City",
		"Celestial World": "Froststar Border City",
	}},
	"fallen_martial_clan": {Theme: "weathered ruins and old battlefields", Climate: "cool-dry", Locations: map[string]string{
		"Mortal World":    "Ashenwall City",
		"Spiritual World": "Broken Halo Spirit City",
		"Immortal World":  "Fallen Star Immortal City",
		"Celestial World": "Ruined Constellation City",
	}},
	"noble_martial_clan": {Theme: "prosperous imperial heartland", Climate: "temperate", Locations: map[string]string{
		"Mortal World":    "Azure Crown Imperial City",
		"Spiritual World": "Jade Crown Spirit City",
		"Immortal World":  "Ninefold Noble Immortal City",
		"Celestial World": "Mandate Crown Celestial City",
	}},
	"alchemy_family": {Theme: "warm herb forests and medicine gardens", Climate: "warm-humid", Locations: map[string]string{
		"Mortal World":    "Jadewood Medicine City",
		"Spiritual World": "Hundred Herb Spirit City",
		"Immortal World":  "Jade Cauldron Immortal City",
		"Celestial World": "Divine Herb Celestial City",
	}},
	"nether_market_house": {Theme: "fog-bound night markets of the yin wetlands", Climate: "cold-damp", Locations: map[string]string{
		"Mortal World":    "Moonfen City",
		"Spiritual World": "Moonfrost Spirit City",
		"Immortal World":  "Lunar Veil Immortal City",
		"Celestial World": "Lunar Shadow Celestial City",
	}},
	"tomb_watch_clan": {Theme: "necropolis terraces above an old battlefield", Climate: "cool-dry", Locations: map[string]string{
		"Mortal World":    "Ashenwall City",
		"Spiritual World": "Broken Halo Spirit City",
		"Immortal World":  "Fallen Star Immortal City",
		"Celestial World": "Ruined Constellation City",
	}},
}

func birthFamilyHomeland(worldName, archetype string) (birthFamilyHomelandProfile, string, bool) {
	profile, ok := birthFamilyHomelands[strings.TrimSpace(archetype)]
	if !ok {
		return birthFamilyHomelandProfile{}, "", false
	}
	location, ok := profile.Locations[worldName]
	if !ok {
		return birthFamilyHomelandProfile{}, "", false
	}
	return profile, location, true
}

func applyBirthFamilyHomelandMetadata(f *BirthFamily) error {
	if f == nil {
		return errors.New("birth family is required")
	}
	profile, location, ok := birthFamilyHomeland(f.RebirthWorld, firstNonempty(f.ID, f.Archetype))
	if !ok {
		return fmt.Errorf("no canonical homeland for family archetype %q in %q", firstNonempty(f.ID, f.Archetype), f.RebirthWorld)
	}
	f.HomelandTheme = profile.Theme
	f.Climate = profile.Climate
	f.NearbyCity = location
	return nil
}

func secureChoiceID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}

func birthFamilyGivenName(gender string) (string, error) {
	pool := birthFamilyFemaleNames
	if gender == "male" {
		pool = birthFamilyMaleNames
	}
	i, err := gamerng.Intn(len(pool))
	if err != nil {
		return "", err
	}
	return pool[i], nil
}

func birthFamilyRelative(surname, relation, gender string, age, realm, phase int64) (FamilyRelative, error) {
	given, err := birthFamilyGivenName(gender)
	if err != nil {
		return FamilyRelative{}, err
	}
	return FamilyRelative{Name: surname + " " + given, Relation: relation, Gender: gender, Age: age, RealmIndex: realm, Phase: phase}, nil
}

func generateBirthFamilyOptions(worldName string) ([]BirthFamily, error) {
	if worldName != "Mortal World" && worldName != "Spiritual World" && worldName != "Immortal World" && worldName != "Celestial World" {
		worldName = "Mortal World"
	}
	worldFloors := map[string]int64{"Mortal World": 0, "Spiritual World": 8, "Immortal World": 16, "Celestial World": 24}
	worldBonuses := map[string]int64{"Spiritual World": 12, "Immortal World": 24, "Celestial World": 36}
	tierBonuses := map[string]int64{"Spiritual World": 1, "Immortal World": 2, "Celestial World": 3}

	out := make([]BirthFamily, 0, len(birthFamilyArchetypes))
	floor := worldFloors[worldName]
	for _, a := range birthFamilyArchetypes {
		surname := starterBirthFamilySurnames[a.ID]
		if surname == "" {
			surname = "Han"
		}
		profile, location, ok := birthFamilyHomeland(worldName, a.ID)
		if !ok {
			return nil, fmt.Errorf("no canonical homeland for family archetype %q in %q", a.ID, worldName)
		}
		f := BirthFamily{
			ID: a.ID, Archetype: a.ID, Category: a.Category, Name: a.Name, Surname: surname,
			Tier: a.Tier, Wealth: a.Wealth, Influence: a.Influence, Stability: a.Stability,
			AlignmentBias: a.AlignmentBias, Location: location, NearbyCity: location,
			HomelandTheme: profile.Theme, Climate: profile.Climate, Boon: a.Boon, Risk: a.Risk,
			RebirthWorld: worldName,
		}
		familyWord := "Family"
		if strings.Contains(a.ID, "clan") {
			familyWord = "Clan"
		}
		f.FamilyName = surname + " " + familyWord
		if worldName != "Mortal World" {
			f.Wealth = minI64(100, f.Wealth+worldBonuses[worldName])
			f.Influence = minI64(100, f.Influence+worldBonuses[worldName])
			f.Tier = minI64(5, f.Tier+tierBonuses[worldName])
		}

		var headRealm int64
		var err error
		if worldName == "Mortal World" {
			var n int
			switch a.ID {
			case "fallen_martial_clan":
				n, err = gamerng.Intn(2)
				headRealm = 1 + int64(n)
			case "noble_martial_clan":
				n, err = gamerng.Intn(3)
				headRealm = 2 + int64(n)
			case "sword_hall_family", "spear_guard_family", "border_garrison_family":
				n, err = gamerng.Intn(3)
				headRealm = 1 + int64(n)
			case "martial_household":
				n, err = gamerng.Intn(2)
				headRealm = int64(n)
			default:
				n, err = gamerng.Intn(3)
				headRealm = int64(n)
			}
			if err != nil {
				return nil, err
			}
		} else {
			n, e := gamerng.Intn(5)
			if e != nil {
				return nil, e
			}
			headRealm = minI64(31, floor+int64(n))
		}
		g, err := gamerng.Intn(2)
		if err != nil {
			return nil, err
		}
		f.HeadGender = "female"
		f.HeadTitle = "Matriarch"
		if g == 0 {
			f.HeadGender = "male"
			f.HeadTitle = "Patriarch"
		}
		given, err := birthFamilyGivenName(f.HeadGender)
		if err != nil {
			return nil, err
		}
		f.HeadName = surname + " " + given
		f.HeadRealmIndex = headRealm
		n, err := gamerng.Intn(9)
		if err != nil {
			return nil, err
		}
		f.HeadPhase = 1 + int64(n)
		n, err = gamerng.Intn(4)
		if err != nil {
			return nil, err
		}
		f.BirthOrder = 1 + int64(n)

		established := a.ID == "fallen_martial_clan" || a.ID == "noble_martial_clan"
		f.ClanStructure = "martial_household"
		if established {
			f.ClanStructure = "bloodline_clan"
		}
		bloodlineChance := int64(50 + minI64(20, f.Tier*4))
		if established {
			bloodlineChance = 82
		}
		n, err = gamerng.Intn(100)
		if err != nil {
			return nil, err
		}
		if int64(n) < bloodlineChance {
			bi, e := gamerng.Intn(len(birthFamilyBloodlines))
			if e != nil {
				return nil, e
			}
			b := birthFamilyBloodlines[bi]
			basePurity := int64(45)
			if a.ID == "fallen_martial_clan" {
				basePurity = 38
			} else if a.ID == "noble_martial_clan" {
				basePurity = 72
			} else if a.ID == "body_tempering_family" || a.ID == "sword_hall_family" || a.ID == "hidden_weapon_family" {
				basePurity = 52
			}
			pr, e := gamerng.Intn(21)
			if e != nil {
				return nil, e
			}
			f.BloodlineName, f.BloodlineAffinity, f.BloodlineTrait = b.Name, b.Affinity, b.Trait
			f.BloodlinePurity = clampI64(basePurity-10+int64(pr), 5, 100)
		} else {
			f.BloodlineName, f.BloodlineAffinity, f.BloodlineTrait, f.BloodlinePurity = "None", "None", "No awakened ancestral bloodline", 0
		}
		if established {
			bc, e := gamerng.Intn(3 + int(f.Tier))
			if e != nil {
				return nil, e
			}
			f.BranchCount = 2 + int64(bc)
		} else {
			f.BranchCount = 1
		}
		rc, e := gamerng.Intn(8 + int(f.Wealth)/2)
		if e != nil {
			return nil, e
		}
		f.RetainerCount = 3 + int64(rc)
		f.ConfederacyName = "None"
		if established {
			cr, e := gamerng.Intn(100)
			if e != nil {
				return nil, e
			}
			if cr < 30 {
				ci, e := gamerng.Intn(len(birthFamilyConfederacies))
				if e != nil {
					return nil, e
				}
				f.ConfederacyName = birthFamilyConfederacies[ci]
			}
		}
		relativeFloor := int64(0)
		if worldName != "Mortal World" {
			relativeFloor = floor
		}
		age1, e := gamerng.Intn(18)
		if e != nil {
			return nil, e
		}
		ph1, e := gamerng.Intn(9)
		if e != nil {
			return nil, e
		}
		father, e := birthFamilyRelative(surname, "Father", "male", 34+int64(age1), maxI64(relativeFloor, minI64(headRealm, relativeFloor+2)), 1+int64(ph1))
		if e != nil {
			return nil, e
		}
		age2, e := gamerng.Intn(18)
		if e != nil {
			return nil, e
		}
		ph2, e := gamerng.Intn(9)
		if e != nil {
			return nil, e
		}
		mother, e := birthFamilyRelative(surname, "Mother", "female", 32+int64(age2), maxI64(relativeFloor, minI64(headRealm, relativeFloor+1)), 1+int64(ph2))
		if e != nil {
			return nil, e
		}
		sg, e := gamerng.Intn(2)
		if e != nil {
			return nil, e
		}
		siblingGender := "female"
		if sg == 0 {
			siblingGender = "male"
		}
		age3, e := gamerng.Intn(16)
		if e != nil {
			return nil, e
		}
		sibling, e := birthFamilyRelative(surname, "Older/Younger Sibling", siblingGender, 12+int64(age3), relativeFloor, 1)
		if e != nil {
			return nil, e
		}
		f.Relatives = []FamilyRelative{father, mother, sibling}
		out = append(out, f)
	}
	return out, nil
}

func birthFamilyArchetypeDefinition(id string) (birthFamilyArchetype, bool) {
	for _, a := range birthFamilyArchetypes {
		if a.ID == id {
			return a, true
		}
	}
	return birthFamilyArchetype{}, false
}

func starterBirthFamilyKey(worldName, archetype string) string {
	world := strings.ToLower(strings.TrimSpace(worldName))
	world = strings.ReplaceAll(world, " ", "_")
	if world == "" {
		world = "mortal_world"
	}
	return "starter:" + world + ":" + strings.TrimSpace(archetype)
}

func loadBirthFamilyByID(conn *storage.Conn, familyID int64) (BirthFamily, error) {
	res, err := conn.Execute(`SELECT family_id,family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,head_name,head_gender,head_title,head_realm_index,head_phase,clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,starter_key FROM birth_families WHERE family_id=?`, []any{familyID})
	if err != nil {
		return BirthFamily{}, err
	}
	if len(res.Rows) == 0 {
		return BirthFamily{}, errors.New("birth family no longer exists")
	}
	r := res.Rows[0]
	archetype := fmt.Sprint(r[3])
	f := BirthFamily{
		ID:                archetype,
		Archetype:         archetype,
		FamilyName:        fmt.Sprint(r[1]),
		Surname:           fmt.Sprint(r[2]),
		Tier:              storage.ParseInt(r[4]),
		Wealth:            storage.ParseInt(r[5]),
		Influence:         storage.ParseInt(r[6]),
		Stability:         storage.ParseInt(r[7]),
		AlignmentBias:     storage.ParseInt(r[8]),
		Location:          fmt.Sprint(r[9]),
		HeadName:          fmt.Sprint(r[10]),
		HeadGender:        fmt.Sprint(r[11]),
		HeadTitle:         fmt.Sprint(r[12]),
		HeadRealmIndex:    storage.ParseInt(r[13]),
		HeadPhase:         storage.ParseInt(r[14]),
		ClanStructure:     fmt.Sprint(r[15]),
		BloodlineName:     fmt.Sprint(r[16]),
		BloodlineAffinity: fmt.Sprint(r[17]),
		BloodlineTrait:    fmt.Sprint(r[18]),
		BloodlinePurity:   storage.ParseInt(r[19]),
		BranchCount:       storage.ParseInt(r[20]),
		RetainerCount:     storage.ParseInt(r[21]),
		ConfederacyName:   fmt.Sprint(r[22]),
	}
	if a, ok := birthFamilyArchetypeDefinition(archetype); ok {
		f.Category, f.Name, f.Boon, f.Risk = a.Category, a.Name, a.Boon, a.Risk
	}
	starterKey := fmt.Sprint(r[23])
	parts := strings.Split(starterKey, ":")
	if len(parts) >= 3 {
		f.RebirthWorld = strings.ReplaceAll(parts[1], "_", " ")
		f.RebirthWorld = strings.Title(f.RebirthWorld)
	}
	if f.RebirthWorld == "" {
		f.RebirthWorld = "Mortal World"
	}
	if err := applyBirthFamilyHomelandMetadata(&f); err != nil {
		return BirthFamily{}, err
	}
	rels, err := conn.Execute(`SELECT name,relation,gender,age_at_creation,realm_index,phase FROM birth_family_npcs WHERE family_id=? AND status='alive' ORDER BY npc_id LIMIT 12`, []any{familyID})
	if err != nil {
		return BirthFamily{}, err
	}
	f.Relatives = make([]FamilyRelative, 0, len(rels.Rows))
	for _, rr := range rels.Rows {
		f.Relatives = append(f.Relatives, FamilyRelative{Name: fmt.Sprint(rr[0]), Relation: fmt.Sprint(rr[1]), Gender: fmt.Sprint(rr[2]), Age: storage.ParseInt(rr[3]), RealmIndex: storage.ParseInt(rr[4]), Phase: storage.ParseInt(rr[5])})
	}
	return f, nil
}

func ensureStarterBirthFamily(conn *storage.Conn, worldName string, f BirthFamily, gameMinute int64) (int64, BirthFamily, error) {
	starterKey := starterBirthFamilyKey(worldName, firstNonempty(f.ID, f.Archetype))
	res, err := conn.Execute(`SELECT family_id FROM birth_families WHERE starter_key=?`, []any{starterKey})
	if err != nil {
		return 0, BirthFamily{}, err
	}
	if len(res.Rows) > 0 {
		familyID := storage.ParseInt(res.Rows[0][0])
		canonical, e := loadBirthFamilyByID(conn, familyID)
		if e != nil {
			return 0, BirthFamily{}, e
		}
		_, expectedLocation, ok := birthFamilyHomeland(worldName, firstNonempty(f.ID, f.Archetype))
		if !ok {
			return 0, BirthFamily{}, fmt.Errorf("no canonical homeland for family archetype %q in %q", firstNonempty(f.ID, f.Archetype), worldName)
		}
		if canonical.Location != expectedLocation {
			now := float64(time.Now().UnixNano()) / 1e9
			if _, e = conn.Execute(`UPDATE birth_families SET location=?,updated_at=? WHERE family_id=?`, []any{expectedLocation, now, familyID}); e != nil {
				return 0, BirthFamily{}, e
			}
			canonical.Location = expectedLocation
		}
		if e = applyBirthFamilyHomelandMetadata(&canonical); e != nil {
			return 0, BirthFamily{}, e
		}
		return familyID, canonical, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	history, _ := json.Marshal([]string{fmt.Sprintf("%s is an established starter household of %s.", f.FamilyName, worldName)})
	inserted, err := conn.Execute(`INSERT INTO birth_families(family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,created_game_minute,last_simulated_game_minute,history_json,clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,created_at,updated_at,starter_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{f.FamilyName, f.Surname, firstNonempty(f.ID, f.Archetype), defaultI64(f.Tier, 1), defaultI64(f.Wealth, 20), defaultI64(f.Influence, 10), defaultI64(f.Stability, 60), f.AlignmentBias, f.Location, firstNonempty(f.HeadName, "Family Head"), firstNonempty(f.HeadGender, "neutral"), firstNonempty(f.HeadTitle, "Family Head"), f.HeadRealmIndex, defaultI64(f.HeadPhase, 1), maxI64(0, defaultI64(f.Wealth, 20)*4), 1, maxI64(0, gameMinute), maxI64(0, gameMinute), string(history), firstNonempty(f.ClanStructure, "extended_household"), firstNonempty(f.BloodlineName, "None"), firstNonempty(f.BloodlineAffinity, "None"), firstNonempty(f.BloodlineTrait, "No awakened ancestral bloodline"), clampI64(f.BloodlinePurity, 0, 100), maxI64(1, f.BranchCount), maxI64(0, f.RetainerCount), firstNonempty(f.ConfederacyName, "None"), now, now, starterKey})
	if err != nil {
		return 0, BirthFamily{}, err
	}
	familyID := inserted.LastInsertID
	for _, rel := range f.Relatives {
		relAge := maxI64(1, rel.Age)
		birth := maxI64(0, gameMinute-relAge*minutesPerYear)
		rr, e := gamerng.Intn(11)
		if e != nil {
			return 0, BirthFamily{}, e
		}
		relNatural := int64(70 + rr)
		life := realmLifespanCeiling(rel.RealmIndex, defaultI64(rel.Phase, 1), relNatural)
		lifeVal := int64(2000000000)
		if life != nil {
			lifeVal = *life
		}
		if _, e = conn.Execute(`INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{familyID, firstNonempty(rel.Name, "Relative"), firstNonempty(rel.Relation, "Relative"), firstNonempty(rel.Gender, "neutral"), relAge, birth, lifeVal, "alive", "Mortal Root", rel.RealmIndex, defaultI64(rel.Phase, 1), "Member of a shared starter household", now}); e != nil {
			return 0, BirthFamily{}, e
		}
	}
	canonical, err := loadBirthFamilyByID(conn, familyID)
	return familyID, canonical, err
}

func birthFamilyOptionsAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p struct {
		WorldName  string `json:"world_name"`
		GameMinute int64  `json:"game_minute"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if strings.TrimSpace(p.WorldName) == "" {
		p.WorldName = "Mortal World"
	}
	exists, err := conn.Execute(`SELECT 1 FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(exists.Rows) > 0 {
		return authoritativeMutation{}, errors.New("character already exists")
	}
	candidates, err := generateBirthFamilyOptions(p.WorldName)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	expiresAt := now + 20*60
	if _, err = conn.Execute(`DELETE FROM character_creation_family_options WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	offers := make([]familyOffer, 0, len(candidates))
	for i, candidate := range candidates {
		familyID, canonical, e := ensureStarterBirthFamily(conn, p.WorldName, candidate, p.GameMinute)
		if e != nil {
			return authoritativeMutation{}, e
		}
		canonical.BirthOrder = candidate.BirthOrder
		choiceID, e := secureChoiceID()
		if e != nil {
			return authoritativeMutation{}, e
		}
		body, _ := json.Marshal(canonical)
		if _, e = conn.Execute(`INSERT INTO character_creation_family_options(option_id,user_id,ordinal,family_json,created_game_minute,expires_at,created_at,family_id) VALUES(?,?,?,?,?,?,?,?)`, []any{choiceID, userID, i, string(body), maxI64(0, p.GameMinute), expiresAt, now, familyID}); e != nil {
			return authoritativeMutation{}, e
		}
		offers = append(offers, familyOffer{ChoiceID: choiceID, FamilyID: familyID, BirthFamily: canonical})
	}
	result := map[string]any{"families": offers, "world_name": p.WorldName, "expires_at": expiresAt}
	eventPayload := map[string]any{"count": len(offers), "world_name": p.WorldName, "expires_at": expiresAt}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "birth_family_options_generated", EntityType: "character_creation", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: eventPayload}}, nil
}

func loadBirthFamilyChoice(conn *storage.Conn, userID int64, choiceID string) (birthFamilyChoice, error) {
	choiceID = strings.TrimSpace(choiceID)
	if choiceID == "" {
		return birthFamilyChoice{}, errors.New("family_choice_id is required")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`SELECT family_id,family_json FROM character_creation_family_options WHERE option_id=? AND user_id=? AND consumed_at IS NULL AND expires_at>?`, []any{choiceID, userID, now})
	if err != nil {
		return birthFamilyChoice{}, err
	}
	if len(res.Rows) == 0 {
		return birthFamilyChoice{}, errors.New("birth-family choice is invalid or expired; use /begin again")
	}
	familyID := storage.ParseInt(res.Rows[0][0])
	if familyID <= 0 {
		return birthFamilyChoice{}, errors.New("birth-family choice predates shared households; use /begin again")
	}
	var offered BirthFamily
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][1])), &offered); err != nil {
		return birthFamilyChoice{}, err
	}
	canonical, err := loadBirthFamilyByID(conn, familyID)
	if err != nil {
		return birthFamilyChoice{}, err
	}
	canonical.BirthOrder = maxI64(1, offered.BirthOrder)
	if strings.TrimSpace(canonical.ID) == "" || strings.TrimSpace(canonical.FamilyName) == "" {
		return birthFamilyChoice{}, errors.New("stored birth-family choice is invalid")
	}
	return birthFamilyChoice{FamilyID: familyID, BirthFamily: canonical}, nil
}
