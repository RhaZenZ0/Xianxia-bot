package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type trueDeathPayload struct {
	GameMinute       int64  `json:"game_minute"`
	Reason           string `json:"reason"`
	MinutesPerYear   int64  `json:"minutes_per_year"`
	BaseSamsaraYears int64  `json:"base_samsara_years"`
	MaxWaitSeconds   int64  `json:"max_wait_seconds"`
}

type reincarnatePayload struct {
	Name       string `json:"name"`
	Gender     string `json:"gender"`
	Path       string `json:"path"`
	GameMinute int64  `json:"game_minute"`
}

type lifeSnapshot struct {
	Name, Path, SpiritualRoot, Location, LifeStatus string
	RealmIndex, Phase                               int64
	BodyRealmIndex, BodyPhase                       int64
	Karma, InsightXP                                int64
}

func loadLifeSnapshot(conn *storage.Conn, userID int64) (lifeSnapshot, error) {
	r, err := conn.Execute(`SELECT name,path,spiritual_root,location,life_status,realm_index,phase,body_realm_index,body_phase,karma_score,insight_xp FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return lifeSnapshot{}, err
	}
	if len(r.Rows) == 0 {
		return lifeSnapshot{}, errors.New("character not found")
	}
	x := r.Rows[0]
	return lifeSnapshot{Name: fmt.Sprint(x[0]), Path: fmt.Sprint(x[1]), SpiritualRoot: fmt.Sprint(x[2]), Location: fmt.Sprint(x[3]), LifeStatus: fmt.Sprint(x[4]), RealmIndex: i64(x[5]), Phase: i64(x[6]), BodyRealmIndex: i64(x[7]), BodyPhase: i64(x[8]), Karma: i64(x[9]), InsightXP: i64(x[10])}, nil
}

func samsaraScale(c lifeSnapshot, base, maxWait, maxLaw int64) (years, wait int64) {
	realm, phase := c.RealmIndex, c.Phase
	if c.BodyRealmIndex > realm || (c.BodyRealmIndex == realm && c.BodyPhase > phase) {
		realm, phase = c.BodyRealmIndex, c.BodyPhase
	}
	if realm < 0 {
		realm = 0
	}
	if phase < 1 {
		phase = 1
	}
	if base < 1 {
		base = 320
	}
	tier, local := realm/8, realm%8
	if tier > 3 {
		tier = 3
	}
	switch tier {
	case 0:
		years = base + local*maxI64(40, base/4) + (phase-1)*maxI64(5, base/32)
	case 1:
		years = base*4 + local*maxI64(160, base*3/4) + (phase-1)*maxI64(20, base/10)
	case 2:
		years = base*12 + local*maxI64(480, base*2) + (phase-1)*maxI64(60, base/4)
	default:
		years = base*30 + local*maxI64(1200, base*5) + (phase-1)*maxI64(120, base/2)
	}
	law := clampI64(maxLaw, 0, 200)
	years += (years * law) / 1000
	cap := maxI64(30, maxWait)
	wait = 60 + realm*7 + (phase-1)*2 + tier*15
	if wait < 45 {
		wait = 45
	}
	if wait > cap {
		wait = cap
	}
	if years < 1 {
		years = 1
	}
	return
}

func chooseSamsaraWorld(realm, karma int64) (string, error) {
	realm = clampI64(realm, 0, 31)
	karma = clampI64(karma, -1000, 1000)
	tier, local := realm/8, realm%8
	if tier > 3 {
		tier = 3
	}
	weights := []int64{0, 0, 0, 0}
	switch tier {
	case 0:
		weights = []int64{94 - local*5, 6 + local*5, 0, 0}
		if local >= 6 {
			weights[2] = local - 5
			weights[0] -= weights[2]
		}
	case 1:
		weights = []int64{22 - minI64(12, local*2), 68 - local*2, 10 + local*4, 0}
		if local >= 6 {
			weights[3] = local - 5
			weights[1] -= weights[3]
		}
	case 2:
		weights = []int64{5, 22 - minI64(14, local*2), 63 - local, 10 + local*3}
	default:
		weights = []int64{2, 5, maxI64(8, 28-local*3), minI64(85, 65+local*3)}
	}
	shift := minI64(8, absI64(karma)/125)
	if shift > 0 && karma > 0 {
		for i := 2; i >= 0; i-- {
			moved := minI64(shift, maxI64(0, weights[i]-1))
			weights[i] -= moved
			weights[i+1] += moved
		}
	}
	if shift > 0 && karma < 0 {
		for i := 1; i < 4; i++ {
			moved := minI64(shift, maxI64(0, weights[i]-1))
			weights[i] -= moved
			weights[i-1] += moved
		}
	}
	total := int64(0)
	for i := range weights {
		if weights[i] < 0 {
			weights[i] = 0
		}
		total += weights[i]
	}
	if total < 1 {
		total = 1
	}
	roll, err := gamerng.Intn(int(total))
	if err != nil {
		return "", err
	}
	running := int64(0)
	worlds := []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"}
	for i, w := range weights {
		running += w
		if int64(roll) < running {
			return worlds[i], nil
		}
	}
	return worlds[0], nil
}

func samsaraEchoes(years, karma, realm int64) (int64, []string, error) {
	if years < 1 {
		years = 1
	}
	total := years*2 + maxI64(0, realm)*20
	if total < 3 {
		total = 3
	}
	if total > 999999 {
		total = 999999
	}
	forms := []string{"a mortal farmer", "a wandering merchant", "a village physician", "a hunting beast", "a mountain spirit", "an ancient tree", "a medicinal herb", "a river fish", "a minor cultivator", "a battlefield orphan", "a temple keeper", "a spirit bird"}
	if karma >= 50 {
		forms = append(forms, "a healer who protected a village", "a guardian spirit beast", "a hermit who taught children")
	}
	if karma <= -50 {
		forms = append(forms, "a predatory spirit beast", "a poisonous marsh plant", "a bandit cultivator", "a resentful wandering ghost")
	}
	count := int64(4 + total/50)
	if count < 4 {
		count = 4
	}
	if count > 10 {
		count = 10
	}
	notable := make([]string, 0, count)
	maxSpan := years / maxI64(1, count)
	if maxSpan < 2 {
		maxSpan = 2
	}
	if maxSpan > 90 {
		maxSpan = 90
	}
	for i := int64(0); i < count; i++ {
		fi, e := gamerng.Intn(len(forms))
		if e != nil {
			return 0, nil, e
		}
		sp, e := gamerng.Intn(int(maxSpan))
		if e != nil {
			return 0, nil, e
		}
		notable = append(notable, fmt.Sprintf("You once lived as %s; that life lasted roughly %d years before the wheel turned again.", forms[fi], 1+sp))
	}
	return total, notable, nil
}

func soulLegacy(c lifeSnapshot, maxLaw, perfect int64) (map[string]any, error) {
	realm, phase := c.RealmIndex, c.Phase
	if c.BodyRealmIndex > realm || (c.BodyRealmIndex == realm && c.BodyPhase > phase) {
		realm, phase = c.BodyRealmIndex, c.BodyPhase
	}
	tier := realm / 8
	if tier > 3 {
		tier = 3
	}
	karmic := minI64(12, absI64(c.Karma)/80)
	memory := minI64(96, 5+realm*2+tier*8+phase/2+karmic)
	talent := minI64(100, 18+realm*2+tier*10+phase+karmic)
	law := minI64(92, realm+tier*10+maxI64(0, maxLaw)/3)
	insight := minI64(80, 5+realm*2+tier*8)
	points := maxI64(1, realm*4+phase+maxLaw/5+maxI64(0, perfect)*4)
	fortune := clampI64(c.Karma/10, -100, 100)
	rr, err := gamerng.Intn(1000)
	if err != nil {
		return nil, err
	}
	roll := int64(rr)
	depth := minI64(300, points+absI64(fortune))
	trait := ""
	if roll < minI64(18, depth/8) {
		trait = "Heaven-Defying Fate"
	} else if roll < minI64(45, 12+depth/5) {
		if maxLaw >= 50 {
			trait = "Dao Memory"
		} else {
			trait = "Born Knowing"
		}
	} else if roll < minI64(100, 35+depth/3) {
		trait = "Old Soul"
	} else if c.Karma <= -400 && roll < 150 {
		trait = "Demonic Rebirth"
	} else if c.Karma >= 400 && roll < 150 {
		trait = "Karmic Eyes"
	}
	return map[string]any{"memory_seed": memory, "talent_echo": talent, "law_echo": law, "insight_echo": insight, "legacy_points": points, "karmic_fortune": fortune, "special_trait": trait}, nil
}

func recordTrueDeathAuthoritative(conn *storage.Conn, userID int64, p trueDeathPayload) (map[string]any, error) {
	c, err := loadLifeSnapshot(conn, userID)
	if err != nil {
		return nil, err
	}
	if c.LifeStatus != "alive" {
		return nil, errors.New("this incarnation is already deceased")
	}
	fam, err := conn.Execute(`SELECT cbf.family_id,cbf.generation FROM character_birth_family cbf WHERE cbf.user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(fam.Rows) == 0 {
		return nil, errors.New("true death requires a birth family")
	}
	familyID := i64(fam.Rows[0][0])
	generation := i64(fam.Rows[0][1])
	if generation < 1 {
		generation = 1
	}
	laws, err := conn.Execute(`SELECT law_id,comprehension,insights FROM law_progress WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	maxLaw := int64(0)
	snap := map[string]any{}
	for _, r := range laws.Rows {
		comp := i64(r[1])
		if comp > maxLaw {
			maxLaw = comp
		}
		snap[fmt.Sprint(r[0])] = map[string]any{"comprehension": comp, "insights": i64(r[2])}
	}
	perfect := int64(0)
	for _, table := range []string{"realm_perfection", "body_realm_perfection"} {
		r, e := conn.Execute(fmt.Sprintf(`SELECT 1 FROM %s WHERE user_id=? AND completed=1 LIMIT 1`, table), []any{userID})
		if e != nil {
			return nil, e
		}
		if len(r.Rows) > 0 {
			perfect++
		}
	}
	years, wait := samsaraScale(c, maxI64(1, p.BaseSamsaraYears), p.MaxWaitSeconds, maxLaw)
	minutes := p.MinutesPerYear
	if minutes < 1 {
		minutes = 518400
	}
	target := years * minutes
	now := float64(time.Now().UnixNano()) / 1e9
	readyAt := now + float64(wait)
	readyMinute := p.GameMinute + target
	prevRealm := maxI64(c.RealmIndex, c.BodyRealmIndex)
	world, err := chooseSamsaraWorld(prevRealm, c.Karma)
	if err != nil {
		return nil, err
	}
	lives, echoes, err := samsaraEchoes(years, c.Karma, prevRealm)
	if err != nil {
		return nil, err
	}
	legacy, err := soulLegacy(c, maxLaw, perfect)
	if err != nil {
		return nil, err
	}
	partnerEcho := int64(0)
	partnerName := ""
	pr, e := conn.Execute(`SELECT p.user_a,p.user_b,p.resonance,ca.name,cb.name FROM dao_partnerships p JOIN characters ca ON ca.user_id=p.user_a JOIN characters cb ON cb.user_id=p.user_b WHERE p.status='active' AND (p.user_a=? OR p.user_b=?) ORDER BY p.partnership_id DESC LIMIT 1`, []any{userID, userID})
	if e == nil && len(pr.Rows) > 0 {
		partnerEcho = minI64(25, i64(pr.Rows[0][2])/4)
		if i64(pr.Rows[0][0]) == userID {
			partnerName = fmt.Sprint(pr.Rows[0][4])
		} else {
			partnerName = fmt.Sprint(pr.Rows[0][3])
		}
	}
	snapJSON, _ := json.Marshal(snap)
	echoJSON, _ := json.Marshal(echoes)
	if _, err = conn.Execute(`UPDATE characters SET life_status='deceased',death_game_minute=?,reincarnation_ready_game_minute=?,true_death_count=true_death_count+1,updated_at=? WHERE user_id=?`, []any{p.GameMinute, readyMinute, now, userID}); err != nil {
		return nil, err
	}
	// Escrow keyed by the persistent user id - auctions, bids, caravans, a
	// seclusion in progress - is resolved here, in the same transaction as the
	// death. See death_escrow.go for why it happens now and not at settlement.
	escrow, err := resolveIncarnationEscrowTx(conn, userID, p.GameMinute, now)
	if err != nil {
		return nil, err
	}
	_, err = conn.Execute(`INSERT INTO reincarnation_state(user_id,family_id,death_game_minute,ready_game_minute,death_reason,previous_name,previous_generation,karma_at_death,family_target_minutes,family_simulated_minutes,afterlife_started_at,reincarnation_ready_at,rebirth_mode,target_world,samsara_lives_count,samsara_history_json,memory_retention,talent_retention,comprehension_retention,insight_retention,legacy_points,special_trait,karmic_fortune,previous_realm_index,previous_phase,previous_body_realm_index,previous_body_phase,previous_spiritual_root,previous_path,previous_insight_xp,law_snapshot_json,active,created_at,partner_echo,partner_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(user_id) DO UPDATE SET family_id=excluded.family_id,death_game_minute=excluded.death_game_minute,ready_game_minute=excluded.ready_game_minute,death_reason=excluded.death_reason,previous_name=excluded.previous_name,previous_generation=excluded.previous_generation,karma_at_death=excluded.karma_at_death,family_target_minutes=excluded.family_target_minutes,family_simulated_minutes=0,afterlife_started_at=excluded.afterlife_started_at,reincarnation_ready_at=excluded.reincarnation_ready_at,rebirth_mode='samsara',target_world=excluded.target_world,samsara_lives_count=excluded.samsara_lives_count,samsara_history_json=excluded.samsara_history_json,memory_retention=excluded.memory_retention,talent_retention=excluded.talent_retention,comprehension_retention=excluded.comprehension_retention,insight_retention=excluded.insight_retention,legacy_points=excluded.legacy_points,special_trait=excluded.special_trait,karmic_fortune=excluded.karmic_fortune,previous_realm_index=excluded.previous_realm_index,previous_phase=excluded.previous_phase,previous_body_realm_index=excluded.previous_body_realm_index,previous_body_phase=excluded.previous_body_phase,previous_spiritual_root=excluded.previous_spiritual_root,previous_path=excluded.previous_path,previous_insight_xp=excluded.previous_insight_xp,law_snapshot_json=excluded.law_snapshot_json,active=1,created_at=excluded.created_at,partner_echo=excluded.partner_echo,partner_name=excluded.partner_name`, []any{userID, familyID, p.GameMinute, readyMinute, strings.TrimSpace(p.Reason), c.Name, generation, c.Karma, target, 0, now, readyAt, "samsara", world, lives, string(echoJSON), legacy["memory_seed"], legacy["talent_echo"], legacy["law_echo"], legacy["insight_echo"], legacy["legacy_points"], legacy["special_trait"], legacy["karmic_fortune"], c.RealmIndex, c.Phase, c.BodyRealmIndex, c.BodyPhase, c.SpiritualRoot, c.Path, c.InsightXP, string(snapJSON), now, partnerEcho, partnerName})
	if err != nil {
		return nil, err
	}
	out := map[string]any{"escrow": escrow, "reason": p.Reason, "private_years": years, "real_wait_seconds": wait, "reincarnation_ready_at": readyAt, "ready_game_minute": readyMinute, "target_world": world, "samsara_lives_count": lives, "samsara_history": echoes, "memory_retention": legacy["memory_seed"], "talent_retention": legacy["talent_echo"], "comprehension_retention": legacy["law_echo"], "insight_retention": legacy["insight_echo"], "legacy_points": legacy["legacy_points"], "special_trait": legacy["special_trait"], "karmic_fortune": legacy["karmic_fortune"], "partner_echo": partnerEcho, "partner_name": partnerName, "previous_realm_index": prevRealm, "location": c.Location, "name": c.Name}
	b, _ := json.Marshal(out)
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "true_death", string(b), now})
	return out, nil
}

func trueDeathAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p trueDeathPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	out, err := recordTrueDeathAuthoritative(conn, userID, p)
	if err != nil {
		return authoritativeMutation{}, err
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "lifecycle", EventType: "true_death", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func samsaraStatus(conn *storage.Conn, userID int64, minutesPerYear int64) (map[string]any, error) {
	r, err := conn.Execute(`SELECT family_target_minutes,family_simulated_minutes,afterlife_started_at,reincarnation_ready_at,target_world,samsara_lives_count,samsara_history_json,memory_retention,talent_retention,comprehension_retention,insight_retention,legacy_points,special_trait,karmic_fortune,partner_echo,partner_name,karma_at_death,previous_spiritual_root,active FROM reincarnation_state WHERE user_id=? AND active=1`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(r.Rows) == 0 {
		return nil, errors.New("no active Samsara cycle")
	}
	x := r.Rows[0]
	target := maxI64(1, i64(x[0]))
	started := toFloat(x[2])
	ready := toFloat(x[3])
	now := float64(time.Now().UnixNano()) / 1e9
	duration := ready - started
	if duration < 1 {
		duration = 1
	}
	fraction := (now - started) / duration
	if fraction < 0 {
		fraction = 0
	}
	if fraction > 1 {
		fraction = 1
	}
	desired := int64(float64(target) * fraction)
	if now >= ready {
		desired = target
	}
	mpy := minutesPerYear
	if mpy < 1 {
		mpy = 518400
	}
	hist := []string{}
	_ = json.Unmarshal([]byte(fmt.Sprint(x[6])), &hist)
	return map[string]any{"family_target_minutes": target, "family_simulated_minutes": desired, "reincarnation_ready_at": ready, "target_world": fmt.Sprint(x[4]), "samsara_lives_count": i64(x[5]), "samsara_history": hist, "memory_retention": i64(x[7]), "talent_retention": i64(x[8]), "comprehension_retention": i64(x[9]), "insight_retention": i64(x[10]), "legacy_points": i64(x[11]), "special_trait": fmt.Sprint(x[12]), "karmic_fortune": i64(x[13]), "partner_echo": i64(x[14]), "partner_name": fmt.Sprint(x[15]), "karma_at_death": i64(x[16]), "previous_spiritual_root": fmt.Sprint(x[17]), "seconds_remaining": maxI64(0, int64(ready-now)), "ready": now >= ready, "samsara_years_elapsed": float64(desired) / float64(mpy), "samsara_years_target": float64(target) / float64(mpy)}, nil
}

func toFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	default:
		var f float64
		fmt.Sscan(fmt.Sprint(v), &f)
		return f
	}
}
func absI64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}

var samsaraMale = []string{"Wei", "Jun", "Hao", "Tian", "Rui", "Feng", "Ming", "Bo", "Jian", "Kai"}
var samsaraFemale = []string{"Mei", "Lan", "Yue", "Xue", "Ling", "Hua", "Ning", "Qiao", "Yan", "Rin"}

type familyTemplate struct {
	ID, Name                                      string
	Wealth, Influence, Stability, Tier, Alignment int64
	Location                                      string
}

type upperFamilyTemplate struct {
	ID, Archetype, Label, Location, ClanStructure string
	Wealth, Influence, Stability, Tier, Alignment int64
}

var samsaraFamilies = []familyTemplate{
	{"martial_household", "Martial Household", 42, 48, 62, 2, 0, "Riverguard City"},
	{"escort_martial_family", "Escort Agency Martial Family", 58, 46, 54, 2, 2, "Four-Roads Caravan City"},
	{"weaponsmith_martial_family", "Weapon-Smith Martial Family", 52, 38, 68, 2, 0, "Emberforge City"},
	{"body_tempering_family", "Body-Tempering Martial Family", 36, 43, 70, 2, 1, "Stoneback Mountain City"},
	{"sword_hall_family", "Sword Hall Martial Family", 48, 55, 58, 3, 3, "Cloudblade City"},
	{"spear_guard_family", "Spear Guard Martial Family", 44, 59, 64, 3, 2, "Ironbanner City"},
	{"hidden_weapon_family", "Hidden-Weapon Martial Family", 50, 41, 50, 3, -3, "Moonfen City"},
	{"border_garrison_family", "Border Garrison Martial Family", 40, 62, 52, 3, 0, "Frostwatch City"},
	{"fallen_martial_clan", "Fallen Martial Clan", 26, 36, 36, 2, -4, "Ashenwall City"},
	{"noble_martial_clan", "Noble Martial Clan", 82, 76, 46, 4, 0, "Azure Crown Imperial City"},
	{"alchemy_family", "Alchemy Family", 61, 45, 67, 3, 2, "Jadewood Medicine City"},
}

var upperSamsaraFamilies = map[string][]upperFamilyTemplate{
	"Spiritual World": {
		{"spirit_river_ward_house", "river_ward_house", "River-Ward House", "Jadeflow Spirit City", "martial_household", 46, 43, 68, 2, 2},
		{"gale_merchant_house", "spirit_caravan_house", "Gale Merchant House", "Galevein Spirit City", "extended_household", 64, 48, 58, 2, 1},
		{"vermilion_forge_house", "spirit_forge_house", "Vermilion Forge House", "Vermilion Furnace City", "extended_household", 58, 45, 70, 3, 1},
		{"stone_marrow_house", "spirit_body_house", "Stone-Marrow House", "Stoneheart Spirit City", "bloodline_clan", 41, 51, 72, 3, 0},
		{"cloudedge_sword_courtyard", "spirit_sword_house", "Cloudedge Sword Courtyard", "Cloudedge Spirit City", "martial_household", 54, 62, 57, 3, 3},
		{"iron_spear_watch_house", "spirit_guard_house", "Iron Spear Watch House", "Spearwall Spirit City", "extended_household", 49, 66, 64, 3, 2},
		{"moonveil_covert_house", "spirit_hidden_house", "Moonveil Covert House", "Moonfrost Spirit City", "bloodline_clan", 55, 39, 48, 3, -3},
		{"northwind_frontier_house", "spirit_frontier_house", "Northwind Frontier House", "Northwind Spirit City", "extended_household", 43, 61, 52, 3, 0},
		{"broken_halo_remnant_house", "spirit_fallen_house", "Broken Halo Remnant House", "Broken Halo Spirit City", "bloodline_clan", 29, 31, 34, 2, -4},
		{"jade_crown_cadet_house", "spirit_cadet_house", "Jade Crown Cadet House", "Jade Crown Spirit City", "extended_household", 60, 58, 53, 3, 0},
		{"hundred_herb_apothecary_house", "spirit_medicine_house", "Hundred-Herb Apothecary House", "Hundred Herb Spirit City", "extended_household", 63, 47, 73, 3, 2},
	},
	"Immortal World": {
		{"immortal_tide_house", "immortal_river_house", "Tide-Listening House", "Immortal River City", "extended_household", 51, 45, 70, 2, 2},
		{"skyroad_wayfarer_clan", "immortal_wayfarer_clan", "Skyroad Wayfarer Clan", "Skyroad Immortal City", "bloodline_clan", 66, 54, 59, 3, 1},
		{"sunforge_lineage", "immortal_forge_lineage", "Sunforge Lineage", "Solar Furnace Immortal City", "bloodline_clan", 71, 58, 68, 3, 1},
		{"adamant_bone_lineage", "immortal_body_lineage", "Adamant-Bone Lineage", "Adamant Body Immortal City", "bloodline_clan", 48, 63, 75, 3, 0},
		{"heaven_edge_sword_house", "immortal_sword_house", "Heaven-Edge Sword House", "Heavenblade Immortal City", "martial_household", 61, 69, 60, 4, 3},
		{"golden_lance_house", "immortal_guard_house", "Golden Lance House", "Golden Spear Immortal City", "extended_household", 57, 71, 66, 4, 2},
		{"lunar_veil_house", "immortal_hidden_house", "Lunar Veil House", "Lunar Veil Immortal City", "bloodline_clan", 62, 50, 49, 4, -3},
		{"polar_gate_house", "immortal_frontier_house", "Polar Gate House", "Polar Gate Immortal City", "extended_household", 52, 67, 55, 3, 0},
		{"fallen_star_successor_house", "immortal_successor_house", "Fallen-Star Successor House", "Fallen Star Immortal City", "extended_household", 38, 44, 41, 3, -2},
		{"ninefold_cadet_house", "immortal_cadet_house", "Ninefold Cadet House", "Ninefold Noble Immortal City", "extended_household", 65, 62, 55, 3, 0},
		{"jade_cauldron_house", "immortal_medicine_house", "Jade Cauldron House", "Jade Cauldron Immortal City", "extended_household", 72, 57, 74, 4, 2},
	},
	"Celestial World": {
		{"star_river_house", "celestial_river_house", "Star-River House", "Celestial River City", "extended_household", 55, 51, 73, 2, 2},
		{"constellation_wayfarer_house", "celestial_wayfarer_house", "Constellation Wayfarer House", "Starroad Celestial City", "extended_household", 68, 60, 61, 3, 1},
		{"solar_crucible_house", "celestial_forge_house", "Solar Crucible House", "Solar Crucible Celestial City", "bloodline_clan", 76, 66, 71, 4, 1},
		{"worldstone_body_house", "celestial_body_house", "Worldstone Body House", "Worldstone Celestial City", "bloodline_clan", 53, 69, 78, 4, 0},
		{"firmament_sword_house", "celestial_sword_house", "Firmament Sword House", "Firmament Blade City", "martial_household", 67, 76, 62, 4, 3},
		{"mandate_spear_house", "celestial_guard_house", "Mandate Spear House", "Mandate Spear City", "extended_household", 61, 79, 68, 4, 2},
		{"lunar_shadow_house", "celestial_hidden_house", "Lunar Shadow House", "Lunar Shadow Celestial City", "bloodline_clan", 69, 56, 50, 4, -3},
		{"froststar_watch_house", "celestial_frontier_house", "Froststar Watch House", "Froststar Border City", "extended_household", 58, 72, 57, 4, 0},
		{"ruined_constellation_successor", "celestial_successor_house", "Ruined-Constellation Successor House", "Ruined Constellation City", "extended_household", 42, 48, 43, 3, -2},
		{"mandate_crown_minor_house", "celestial_cadet_house", "Mandate-Crown Minor House", "Mandate Crown Celestial City", "extended_household", 70, 65, 58, 3, 0},
		{"divine_herb_house", "celestial_medicine_house", "Divine Herb House", "Divine Herb Celestial City", "extended_household", 78, 62, 76, 4, 2},
	},
}

var upperSamsaraSurnames = map[string][]string{
	"Spiritual World": {"Yu", "Pei", "Huo", "Shi", "Ji", "Duan", "Nie", "Xue", "Mo", "Rong", "Yao"},
	"Immortal World":  {"Cang", "Lu", "Zhu", "He", "Sikong", "Zhen", "Wen", "Leng", "Qu", "Nangong", "Zuo"},
	"Celestial World": {"Xuanyuan", "Tantai", "Baili", "Helian", "Gongye", "Shangguan", "Murong", "Dugu", "Yuwen", "Nalan", "Ouyang"},
}

var bloodTraits = [][3]string{
	{"Azure Wolf Bloodline", "Body", "Tracking instinct, endurance and coordinated hunting"},
	{"Vermilion Bird Emberline", "Fire", "Fire affinity and resilience to heat"},
	{"Black Tortoise Marrowline", "Earth", "Defense, vitality and patient cultivation"},
	{"White Tiger Warline", "Metal", "Battle instinct, killing intent and weapon affinity"},
	{"Moon Serpent Yin Line", "Water", "Yin sensitivity, concealment and poison resistance"},
	{"Thunder Roc Lineage", "Lightning", "Speed, lightning affinity and aerial techniques"},
	{"Jade River Spirit Line", "Water", "Water affinity, healing intuition and river-sense"},
	{"Stone Bear Ancestry", "Earth", "Powerful physique, endurance and mountain survival"},
}

func randomName(surname, gender string) (string, error) {
	pool := samsaraFemale
	if gender == "male" {
		pool = samsaraMale
	}
	i, e := gamerng.Intn(len(pool))
	if e != nil {
		return "", e
	}
	return surname + " " + pool[i], nil
}

func pickMortalSamsaraTemplate(karma int64) (familyTemplate, error) {
	weights := make([]int, len(samsaraFamilies))
	total := 0
	for i, t := range samsaraFamilies {
		w := 20
		if karma >= 50 {
			if contains([]string{"martial_household", "weaponsmith_martial_family", "body_tempering_family", "spear_guard_family", "noble_martial_clan", "alchemy_family"}, t.ID) {
				w += int(minI64(24, karma/45))
			}
			if contains([]string{"fallen_martial_clan", "hidden_weapon_family"}, t.ID) {
				w = maxIntLife(6, w-int(minI64(10, karma/90)))
			}
		} else if karma <= -50 {
			if contains([]string{"fallen_martial_clan", "hidden_weapon_family", "escort_martial_family", "border_garrison_family"}, t.ID) {
				w += int(minI64(28, -karma/40))
			}
			if t.ID == "noble_martial_clan" {
				w = maxIntLife(8, w-int(minI64(8, -karma/120)))
			}
		}
		weights[i] = w
		total += w
	}
	pick, err := gamerng.Intn(total)
	if err != nil {
		return familyTemplate{}, err
	}
	run := 0
	for i, w := range weights {
		run += w
		if pick < run {
			return samsaraFamilies[i], nil
		}
	}
	return samsaraFamilies[len(samsaraFamilies)-1], nil
}

func pickUpperSamsaraTemplate(world string, karma int64) (upperFamilyTemplate, error) {
	options := upperSamsaraFamilies[world]
	if len(options) == 0 {
		return upperFamilyTemplate{}, fmt.Errorf("no upper-world Samsara families for %q", world)
	}
	weights := make([]int, len(options))
	total := 0
	for i, option := range options {
		weight := 20
		if karma >= 50 {
			if option.Stability >= 65 || option.Alignment > 0 {
				weight += int(minI64(18, karma/60))
			}
			if option.Stability < 45 {
				weight = maxIntLife(7, weight-int(minI64(8, karma/120)))
			}
		} else if karma <= -50 {
			if option.Alignment < 0 || option.Stability < 50 {
				weight += int(minI64(22, -karma/55))
			}
			if option.Stability >= 70 {
				weight = maxIntLife(8, weight-int(minI64(7, -karma/140)))
			}
		}
		weights[i] = weight
		total += weight
	}
	pick, err := gamerng.Intn(total)
	if err != nil {
		return upperFamilyTemplate{}, err
	}
	run := 0
	for i, weight := range weights {
		run += weight
		if pick < run {
			return options[i], nil
		}
	}
	return options[len(options)-1], nil
}

func previousFamilyWasNoble(name, archetype string) bool {
	value := strings.ToLower(name + " " + archetype)
	for _, marker := range []string{"noble", "royal", "imperial", "crown", "mandate"} {
		if strings.Contains(value, marker) {
			return true
		}
	}
	return false
}

func samsaraLineageFromRoll(previousFamilyName, previousArchetype, world string, family BirthFamily, karma int64, roll int) (string, string) {
	previousFamilyName = strings.TrimSpace(previousFamilyName)
	if previousFamilyName == "" {
		return "unrelated_rebirth", fmt.Sprintf("%s is a realm-local %s household with no assigned ancestry from a previous playable life.", family.FamilyName, world)
	}
	survivingCutoff := 18
	fallenCutoff := 35
	replacedCutoff := 58
	noble := previousFamilyWasNoble(previousFamilyName, previousArchetype)
	if noble {
		// Lower-world rank does not propagate upward. Noble lines are especially
		// likely to become cadet, ruined, or replaced branches in stronger realms.
		survivingCutoff = 10
		fallenCutoff = 38
		replacedCutoff = 68
	}
	if karma >= 400 {
		survivingCutoff += 4
	} else if karma <= -400 {
		fallenCutoff += 4
		replacedCutoff += 4
	}
	switch {
	case roll < survivingCutoff:
		if noble {
			return "distant_surviving_branch", fmt.Sprintf("A distant blood trace from %s survived into the %s as ancestors of %s, but the branch never inherited its lower-world rank. It is a local household with its own resources, talent and obligations, not a royal continuation.", previousFamilyName, world, family.FamilyName)
		}
		return "distant_surviving_branch", fmt.Sprintf("A distant branch connected to %s survived into the %s and eventually became %s. The connection is ancestral only; status, resources and political standing were rebuilt locally.", previousFamilyName, world, family.FamilyName)
	case roll < fallenCutoff:
		return "fallen_severed_branch", fmt.Sprintf("A branch once connected to %s reached the %s, then lost enough talent, resources and standing that its old identity disappeared. %s descends from that severed remnant but inherits no automatic prestige.", previousFamilyName, world, family.FamilyName)
	case roll < replacedCutoff:
		return "extinct_branch_replaced", fmt.Sprintf("An older branch associated with %s died out in the %s. %s later replaced it in the local estate, trade, military or political niche and has no blood continuity with the extinct house.", previousFamilyName, world, family.FamilyName)
	default:
		return "no_known_connection", fmt.Sprintf("No reliable blood, oath or inheritance connects %s to %s. Samsara placed the soul in an unrelated %s family rather than extending the former lineage upward.", previousFamilyName, family.FamilyName, world)
	}
}

func rollSamsaraLineage(previousFamilyName, previousArchetype, world string, family BirthFamily, karma int64) (string, string, error) {
	if strings.TrimSpace(previousFamilyName) == "" {
		status, summary := samsaraLineageFromRoll(previousFamilyName, previousArchetype, world, family, karma, 99)
		return status, summary, nil
	}
	roll, err := gamerng.Intn(100)
	if err != nil {
		return "", "", err
	}
	status, summary := samsaraLineageFromRoll(previousFamilyName, previousArchetype, world, family, karma, roll)
	return status, summary, nil
}

func generateSamsaraFamilyWithLineage(world string, karma int64, previousFamilyName, previousArchetype string) (BirthFamily, error) {
	floor := map[string]int64{"Mortal World": 0, "Spiritual World": 8, "Immortal World": 16, "Celestial World": 24}
	if _, ok := floor[world]; !ok {
		world = "Mortal World"
	}
	karma = clampI64(karma, -1000, 1000)
	if world == "Mortal World" {
		template, err := pickMortalSamsaraTemplate(karma)
		if err != nil {
			return BirthFamily{}, err
		}
		options, err := generateBirthFamilyOptions(world)
		if err != nil {
			return BirthFamily{}, err
		}
		for _, option := range options {
			if option.ID == template.ID {
				option.RebirthWorld = world
				option.PreviousFamily = strings.TrimSpace(previousFamilyName)
				if option.PreviousFamily != "" {
					option.LineageStatus = "new_mortal_incarnation"
					option.LineageSummary = fmt.Sprintf("Samsara placed the soul into the established %s. This is a new Mortal incarnation, not an automatic continuation of %s.", option.FamilyName, option.PreviousFamily)
				}
				return option, nil
			}
		}
		return BirthFamily{}, fmt.Errorf("no starting family found for Samsara archetype %q", template.ID)
	}

	template, err := pickUpperSamsaraTemplate(world, karma)
	if err != nil {
		return BirthFamily{}, err
	}
	surnames := upperSamsaraSurnames[world]
	surnameIndex, err := gamerng.Intn(len(surnames))
	if err != nil {
		return BirthFamily{}, err
	}
	surname := surnames[surnameIndex]
	f := BirthFamily{
		ID: template.ID, Archetype: template.Archetype, Name: template.Label,
		Surname: surname, FamilyName: surname + " " + template.Label,
		Tier: template.Tier, Wealth: template.Wealth, Influence: template.Influence,
		Stability: template.Stability, AlignmentBias: template.Alignment,
		Location: template.Location, NearbyCity: template.Location, RebirthWorld: world,
		ClanStructure: template.ClanStructure, BranchCount: 1, ConfederacyName: "None",
		PreviousFamily: strings.TrimSpace(previousFamilyName),
	}
	status, summary, err := rollSamsaraLineage(previousFamilyName, previousArchetype, world, f, karma)
	if err != nil {
		return BirthFamily{}, err
	}
	f.LineageStatus = status
	f.LineageSummary = summary

	headRealm := floor[world]
	realmRoll, _ := gamerng.Intn(5)
	headRealm = minI64(31, headRealm+int64(realmRoll))
	genderRoll, _ := gamerng.Intn(2)
	gender := "female"
	title := "Matriarch"
	if genderRoll == 0 {
		gender = "male"
		title = "Patriarch"
	}
	headName, err := randomName(surname, gender)
	if err != nil {
		return BirthFamily{}, err
	}
	headPhaseRoll, _ := gamerng.Intn(9)
	birthOrderRoll, _ := gamerng.Intn(4)
	f.HeadName = headName
	f.HeadGender = gender
	f.HeadTitle = title
	f.HeadRealmIndex = headRealm
	f.HeadPhase = 1 + int64(headPhaseRoll)
	f.BirthOrder = 1 + int64(birthOrderRoll)

	established := f.ClanStructure == "bloodline_clan"
	if established {
		branchRoll, _ := gamerng.Intn(int(3 + maxI64(1, f.Tier)))
		f.BranchCount = 2 + int64(branchRoll)
	}
	bloodlineChance := int64(45 + f.Tier*6)
	if established {
		bloodlineChance += 12
	}
	bloodRoll, _ := gamerng.Intn(100)
	if int64(bloodRoll) < minI64(88, bloodlineChance) {
		traitIndex, _ := gamerng.Intn(len(bloodTraits))
		trait := bloodTraits[traitIndex]
		f.BloodlineName = trait[0]
		f.BloodlineAffinity = trait[1]
		f.BloodlineTrait = trait[2]
		purityRoll, _ := gamerng.Intn(31)
		f.BloodlinePurity = clampI64(35+f.Tier*7+int64(purityRoll)-15, 5, 100)
	} else {
		f.BloodlineName = "None"
		f.BloodlineAffinity = "None"
		f.BloodlineTrait = "No awakened ancestral bloodline"
	}

	retainerRoll, _ := gamerng.Intn(int(8 + maxI64(1, f.Wealth)/2))
	f.RetainerCount = 3 + int64(retainerRoll)
	relativeFloor := floor[world]
	fatherAge, _ := gamerng.Intn(18)
	motherAge, _ := gamerng.Intn(18)
	siblingAge, _ := gamerng.Intn(16)
	fatherPhase, _ := gamerng.Intn(9)
	motherPhase, _ := gamerng.Intn(9)
	siblingGenderRoll, _ := gamerng.Intn(2)
	siblingGender := "female"
	if siblingGenderRoll == 0 {
		siblingGender = "male"
	}
	fatherName, _ := randomName(surname, "male")
	motherName, _ := randomName(surname, "female")
	siblingName, _ := randomName(surname, siblingGender)
	f.Relatives = []FamilyRelative{
		{fatherName, "Father", "male", 34 + int64(fatherAge), maxI64(relativeFloor, minI64(headRealm, relativeFloor+2)), 1 + int64(fatherPhase)},
		{motherName, "Mother", "female", 32 + int64(motherAge), maxI64(relativeFloor, minI64(headRealm, relativeFloor+1)), 1 + int64(motherPhase)},
		{siblingName, "Older/Younger Sibling", siblingGender, 12 + int64(siblingAge), relativeFloor, 1},
	}
	return f, nil
}

func maxIntLife(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func inheritedSamsaraRoot(old string, roots []string, f BirthFamily, karma, talent int64) (string, error) {
	echo := talent * 2 / 3
	if echo < 5 {
		echo = 5
	}
	if echo > 70 {
		echo = 70
	}
	r, e := gamerng.Intn(100)
	if e != nil {
		return "", e
	}
	if int64(r) < echo {
		return old, nil
	}
	chance := minI64(88, 40+f.Tier*6+minI64(10, absI64(karma)/50)+maxI64(0, f.BloodlinePurity)/10)
	r, e = gamerng.Intn(100)
	if e != nil {
		return "", e
	}
	if int64(r) < chance {
		return old, nil
	}
	if len(roots) == 0 {
		return "Mortal Root", nil
	}
	for i := int64(0); i < maxI64(1, f.Tier); i++ {
		pi, e := gamerng.Intn(len(roots))
		if e != nil {
			return "", e
		}
		pick := roots[pi]
		if pick != "Mortal Root" {
			return pick, nil
		}
		rr, e := gamerng.Intn(100)
		if e != nil {
			return "", e
		}
		if rr < 45 {
			return pick, nil
		}
	}
	return "Mortal Root", nil
}

func samsaraFamilySourceWorld(catalog worlddata.Catalog, familyLocation string, realmIndex int64) string {
	if location, ok := catalog.Locations[strings.TrimSpace(familyLocation)]; ok && strings.TrimSpace(location.World) != "" {
		return location.World
	}
	return realmWorld(catalog.Realms, realmIndex)
}

func reincarnateAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p reincarnatePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" {
		return authoritativeMutation{}, errors.New("character name is required")
	}
	path, ok := catalog.NormalizePath(p.Path)
	if !ok {
		return authoritativeMutation{}, errors.New("unknown cultivation path")
	}
	gender := strings.ToLower(strings.TrimSpace(p.Gender))
	if gender != "male" && gender != "female" && gender != "neutral" {
		gender = "neutral"
	}
	st, err := conn.Execute(`SELECT family_id,death_reason,previous_name,karma_at_death,reincarnation_ready_at,target_world,memory_retention,talent_retention,comprehension_retention,insight_retention,legacy_points,special_trait,karmic_fortune,previous_realm_index,previous_phase,previous_body_realm_index,previous_body_phase,previous_spiritual_root,previous_path,partner_echo,partner_name FROM reincarnation_state WHERE user_id=? AND active=1`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(st.Rows) == 0 {
		return authoritativeMutation{}, errors.New("no active Samsara cycle is waiting for this soul")
	}
	s := st.Rows[0]
	if float64(time.Now().UnixNano())/1e9 < toFloat(s[4]) {
		return authoritativeMutation{}, errors.New("your soul has not yet completed its Samsara cycle")
	}
	karma := i64(s[3])
	world := fmt.Sprint(s[5])
	oldFamName := "Unknown Family"
	oldFamArchetype := ""
	oldFamLocation := ""
	of, familyLookupErr := conn.Execute(`SELECT family_name,archetype,location FROM birth_families WHERE family_id=?`, []any{i64(s[0])})
	if familyLookupErr != nil {
		return authoritativeMutation{}, familyLookupErr
	}
	if len(of.Rows) > 0 {
		oldFamName = fmt.Sprint(of.Rows[0][0])
		oldFamArchetype = fmt.Sprint(of.Rows[0][1])
		oldFamLocation = fmt.Sprint(of.Rows[0][2])
	}
	family, err := generateSamsaraFamilyWithLineage(world, karma, oldFamName, oldFamArchetype)
	if err != nil {
		return authoritativeMutation{}, err
	}
	familyID := int64(0)
	if world == "Mortal World" {
		familyID, family, err = ensureStarterBirthFamily(conn, world, family, p.GameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	talent := clampI64(i64(s[7])+clampI64(i64(s[19]), 0, 25), 0, 100)
	oldRoot := fmt.Sprint(s[17])
	if oldRoot == "" {
		oldRoot = "Mortal Root"
	}
	root, err := inheritedSamsaraRoot(oldRoot, catalog.Roots, family, karma, talent)
	if err != nil {
		return authoritativeMutation{}, err
	}
	apt, err := generateAptitudes(root, path, family, catalog, int(talent))
	if err != nil {
		return authoritativeMutation{}, err
	}
	nr, err := gamerng.Intn(11)
	if err != nil {
		return authoritativeMutation{}, err
	}
	natural := int64(70 + nr)
	pd := catalog.Paths[path]
	attrs := map[string]int{"body": pd.Body, "agility": pd.Agility, "spirit": pd.Spirit, "insight": pd.Insight, "will": pd.Will, "presence": pd.Presence}
	attrsJSON, _ := json.Marshal(attrs)
	qiMax := int64(8 + pd.Spirit*2)
	vitMax := int64(10 + pd.Body*2)
	now := float64(time.Now().UnixNano()) / 1e9
	oldLegacy := map[string]any{"incarnation_count": int64(1), "legacy_points": int64(0), "memory_seed": int64(0), "talent_echo": int64(0), "law_echo": int64(0), "insight_echo": int64(0), "special_trait": "", "past_lives_json": "[]"}
	lr, e := conn.Execute(`SELECT incarnation_count,legacy_points,memory_seed,talent_echo,law_echo,insight_echo,special_trait,past_lives_json FROM soul_legacy WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if len(lr.Rows) > 0 {
		keys := []string{"incarnation_count", "legacy_points", "memory_seed", "talent_echo", "law_echo", "insight_echo", "special_trait", "past_lives_json"}
		for i, k := range keys {
			oldLegacy[k] = lr.Rows[0][i]
		}
	}
	past := []map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(oldLegacy["past_lives_json"])), &past)
	past = append(past, map[string]any{"name": fmt.Sprint(s[2]), "family": oldFamName, "realm_index": i64(s[13]), "phase": i64(s[14]), "body_realm_index": i64(s[15]), "body_phase": i64(s[16]), "path": fmt.Sprint(s[18]), "spiritual_root": oldRoot, "karma": karma, "death_reason": fmt.Sprint(s[1])})
	if len(past) > 50 {
		past = past[len(past)-50:]
	}
	pastJSON, _ := json.Marshal(past)
	incarnation := maxI64(1, i64(oldLegacy["incarnation_count"])) + 1
	totalLegacy := maxI64(0, i64(oldLegacy["legacy_points"])) + maxI64(0, i64(s[10]))
	memory := minI64(100, maxI64(i64(s[6]), i64(oldLegacy["memory_seed"]))+minI64(10, incarnation/3))
	mergedTalent := minI64(100, maxI64(i64(s[7]), i64(oldLegacy["talent_echo"])))
	mergedLaw := minI64(100, maxI64(i64(s[8]), i64(oldLegacy["law_echo"])))
	mergedInsight := minI64(100, maxI64(i64(s[9]), i64(oldLegacy["insight_echo"])))
	trait := fmt.Sprint(s[11])
	if trait == "" {
		trait = fmt.Sprint(oldLegacy["special_trait"])
	}
	dynastyHistoryID := int64(0)
	if world != "Mortal World" {
		history := []string{fmt.Sprintf("Samsara turned, and %s was born into %s in the %s.", p.Name, family.FamilyName, world)}
		if strings.TrimSpace(family.LineageSummary) != "" {
			history = append(history, family.LineageSummary)
		}
		hist, _ := json.Marshal(history)
		res, insertErr := conn.Execute(`INSERT INTO birth_families(family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,created_game_minute,last_simulated_game_minute,history_json,line_status,clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{family.FamilyName, family.Surname, firstNonempty(family.Archetype, family.ID), family.Tier, family.Wealth, family.Influence, family.Stability, family.AlignmentBias, family.Location, family.HeadName, family.HeadGender, family.HeadTitle, family.HeadRealmIndex, family.HeadPhase, maxI64(0, family.Wealth*4), 1, p.GameMinute, p.GameMinute, string(hist), "active", family.ClanStructure, firstNonempty(family.BloodlineName, "None"), firstNonempty(family.BloodlineAffinity, "None"), firstNonempty(family.BloodlineTrait, "No awakened ancestral bloodline"), family.BloodlinePurity, maxI64(1, family.BranchCount), maxI64(0, family.RetainerCount), firstNonempty(family.ConfederacyName, "None"), now, now})
		if insertErr != nil {
			return authoritativeMutation{}, insertErr
		}
		familyID = res.LastInsertID
		for _, rel := range family.Relatives {
			rr, _ := gamerng.Intn(21)
			life := realmLifespanCeiling(rel.RealmIndex, maxI64(1, rel.Phase), int64(70+rr))
			lv := int64(2000000000)
			if life != nil {
				lv = *life
			}
			_, err = conn.Execute(`INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{familyID, rel.Name, rel.Relation, rel.Gender, maxI64(1, rel.Age), p.GameMinute - maxI64(1, rel.Age)*518400, lv, "alive", "Mortal Root", rel.RealmIndex, maxI64(1, rel.Phase), "Family member of a Samsara rebirth martial household", now})
			if err != nil {
				return authoritativeMutation{}, err
			}
		}
	}
	sourceWorld := samsaraFamilySourceWorld(catalog, oldFamLocation, i64(s[13]))
	dynastyHistoryID, err = recordSamsaraDynastyHistory(
		conn,
		userID,
		incarnation,
		i64(s[0]),
		familyID,
		p.GameMinute,
		oldFamName,
		oldFamArchetype,
		sourceWorld,
		family.FamilyName,
		firstNonempty(family.Archetype, family.ID),
		world,
		family.LineageStatus,
		family.LineageSummary,
		now,
	)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// Clear incarnation-scoped state before installing the new body.
	tables := []string{"inventory", "active_effects", "character_conditions", "tribulation_state", "tribulation_attempts", "profession_progress", "faction_reputation", "bounty_hunter_pursuits", "boss_reward_claims", "formation_positions", "equipment_instances", "bounties", "grudges", "crime_records", "character_manuals", "spirit_beasts", "artifact_bonds", "item_provenance", "character_social_state", "hidden_sect_membership", "character_bloodlines", "character_physiques", "character_spiritual_roots", "cooldowns", "realm_perfection", "body_realm_perfection", "law_progress", "dao_progress", "inheritances", "currency_wallets", "storage_inventory", "storage_containers", "sect_membership", "secret_realm_runs", "auction_door_risks", "personal_worlds"}
	for _, t := range tables {
		if _, err = conn.Execute(fmt.Sprintf(`DELETE FROM %s WHERE user_id=?`, t), []any{userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	_, _ = conn.Execute(`DELETE FROM boss_participants WHERE user_id=?`, []any{userID})
	_, _ = conn.Execute(`DELETE FROM party_members WHERE user_id=?`, []any{userID})
	_, _ = conn.Execute(`UPDATE parties SET status='disbanded',updated_at=? WHERE leader_user_id=? AND status='active'`, []any{now, userID})
	_, _ = conn.Execute(`DELETE FROM pvp_challenges WHERE challenger_user_id=? OR target_user_id=?`, []any{userID, userID})
	_, _ = conn.Execute(`DELETE FROM sect_lineage WHERE disciple_user_id=? OR master_user_id=?`, []any{userID, userID})
	_, _ = conn.Execute(`DELETE FROM cave_abode_access WHERE owner_user_id=? OR guest_user_id=?`, []any{userID, userID})
	_, _ = conn.Execute(`DELETE FROM cave_abodes WHERE user_id=?`, []any{userID})
	_, _ = conn.Execute(`DELETE FROM player_family_invites WHERE inviter_user_id=? OR invitee_user_id=?`, []any{userID, userID})
	_, _ = conn.Execute(`DELETE FROM family_children WHERE parent_user_id=?`, []any{userID})
	_, _ = conn.Execute(`DELETE FROM player_family_members WHERE user_id=?`, []any{userID})
	_, err = conn.Execute(`INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET family_id=excluded.family_id,birth_order=excluded.birth_order,generation=excluded.generation,last_support_game_minute=excluded.last_support_game_minute`, []any{userID, familyID, maxI64(1, family.BirthOrder), 1, -999999999})
	if err != nil {
		return authoritativeMutation{}, err
	}
	currency := map[string]string{"Mortal World": "low_spirit_stone", "Spiritual World": "low_spirit_crystal", "Immortal World": "low_immortal_stone", "Celestial World": "low_celestial_crystal"}[world]
	if currency == "" {
		currency = "low_spirit_stone"
	}
	spiritStones := int64(0)
	if currency == "low_spirit_stone" {
		spiritStones = 25
	}
	origin := fmt.Sprintf("%s, %s", family.FamilyName, family.Location)
	householdLocation := birthFamilyHouseholdLocation(familyID)
	_, err = conn.Execute(`UPDATE characters SET name=?,origin=?,path=?,spiritual_root=?,gender=?,age_at_creation_years=12,created_game_minute=?,natural_lifespan_years=?,life_extension_years=0,life_status='alive',death_game_minute=NULL,reincarnation_ready_game_minute=NULL,realm_index=0,phase=1,cultivation=0,body_realm_index=0,body_phase=1,body_cultivation=0,sense_power_bonus=0,sense_precision_bonus=0,sense_range_bonus=0,concealment_bonus=0,concealment_active=0,qi=?,qi_max=?,vitality=?,vitality_max=?,spirit_stones=?,insight_xp=0,location=?,attributes_json=?,updated_at=? WHERE user_id=?`, []any{p.Name, origin, path, root, gender, p.GameMinute, natural, qiMax, qiMax, vitMax, vitMax, spiritStones, householdLocation, string(attrsJSON), now, userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	sceneMetadata, _ := json.Marshal(map[string]any{"family_id": familyID, "family_name": family.FamilyName, "base_location": family.Location, "samsara_rebirth": true})
	_, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type=excluded.scene_type,scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=NULL,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`, []any{userID, family.Location, "birth_family_household", householdLocation, family.FamilyName + " Household", nil, string(sceneMetadata), now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	_, err = conn.Execute(`INSERT INTO soul_legacy(user_id,incarnation_count,legacy_points,memory_seed,talent_echo,law_echo,insight_echo,karmic_fortune,special_trait,awakened_memory,past_lives_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,?,?) ON CONFLICT(user_id) DO UPDATE SET incarnation_count=excluded.incarnation_count,legacy_points=excluded.legacy_points,memory_seed=excluded.memory_seed,talent_echo=excluded.talent_echo,law_echo=excluded.law_echo,insight_echo=excluded.insight_echo,karmic_fortune=excluded.karmic_fortune,special_trait=excluded.special_trait,awakened_memory=0,past_lives_json=excluded.past_lives_json,updated_at=excluded.updated_at`, []any{userID, incarnation, totalLegacy, memory, mergedTalent, mergedLaw, mergedInsight, i64(s[12]), trait, string(pastJSON), now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	elems, _ := json.Marshal(apt.Root.Elements)
	_, err = conn.Execute(`INSERT INTO character_spiritual_roots(user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, apt.Root.Grade, apt.Root.Purity, string(elems), apt.Root.Mutation, apt.Root.Stability, apt.Root.RefinementProgress, apt.Root.Compatibility, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if apt.Bloodline != nil {
		b := apt.Bloodline
		tech, _ := json.Marshal(b.UnlockedTechniques)
		_, err = conn.Execute(`INSERT INTO character_bloodlines(user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,primary_lineage,source_family_id,unlocked_techniques_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, b.BloodlineID, b.Name, b.Affinity, b.Purity, b.State, b.EvolutionStage, b.Progress, b.Rejection, b.Mutation, 1, familyID, string(tech), now})
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	ph := apt.Physique
	_, err = conn.Execute(`INSERT INTO character_physiques(user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, ph.PhysiqueID, ph.Name, ph.State, ph.EvolutionStage, ph.Progress, ph.Stability, ph.Instability, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	for item, qty := range map[string]int64{"spirit_herb": 2, "spirit_iron": 1} {
		_, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)`, []any{userID, item, qty})
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	_, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,25)`, []any{userID, currency})
	if err != nil {
		return authoritativeMutation{}, err
	}
	_, err = conn.Execute(`INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at) VALUES(?,?,?,?,?,?,?)`, []any{userID, "common_spatial_pouch", "Common Spatial Pouch", "Mortal", 24, 0, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	_, err = conn.Execute(`UPDATE reincarnation_state SET active=0 WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"family_name": family.FamilyName, "family_archetype": family.Archetype, "generation": 1,
		"spiritual_root": root, "mode": "samsara", "target_world": world,
		"location": householdLocation, "physical_location": family.Location,
		"lineage_status": family.LineageStatus, "lineage_summary": family.LineageSummary, "previous_family": family.PreviousFamily, "dynasty_history_id": dynastyHistoryID,
		"memory_retention": memory, "talent_retention": mergedTalent, "partner_echo": i64(s[19]), "partner_name": fmt.Sprint(s[20]),
		"comprehension_retention": mergedLaw, "insight_retention": mergedInsight, "legacy_points": totalLegacy,
		"special_trait": trait, "incarnation_count": incarnation, "natural_lifespan_years": natural, "aptitudes": apt,
	}
	b, _ := json.Marshal(out)
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "reincarnation", string(b), now})
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "lifecycle", EventType: "reincarnation", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
