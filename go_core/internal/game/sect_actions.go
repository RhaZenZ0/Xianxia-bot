package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type sectRecommendationPayload struct {
	NPCName    string         `json:"npc_name"`
	SectName   string         `json:"sect_name"`
	GameMinute int64          `json:"game_minute"`
	Location   string         `json:"location"`
	Details    map[string]any `json:"details"`
}
type sectTrialPayload struct {
	SectName         string `json:"sect_name"`
	Examiner         string `json:"examiner"`
	Location         string `json:"location"`
	TrialName        string `json:"trial_name"`
	GameMinute       int64  `json:"game_minute"`
	PrimaryDetails   any    `json:"primary_details"`
	SecondaryDetails any    `json:"secondary_details"`
}
type sectItemPayload struct {
	ItemID   string `json:"item_id"`
	Quantity int64  `json:"quantity"`
}
type discipleRequestPayload struct {
	MasterUserID int64 `json:"master_user_id"`
}
type discipleResolvePayload struct {
	RequestID int64 `json:"request_id"`
	Accept    bool  `json:"accept"`
}
type sectManorPayload struct {
	Name       string `json:"name"`
	Facility   string `json:"facility"`
	GameMinute int64  `json:"game_minute"`
}

type checkRollGo struct {
	Die1, Die2, Modifier, TN, Total, Margin int64
	Success                                 bool
}

func roll2d10Go(mod, tn int64) (checkRollGo, error) {
	d1, e := gamerng.D10()
	if e != nil {
		return checkRollGo{}, e
	}
	d2, e := gamerng.D10()
	if e != nil {
		return checkRollGo{}, e
	}
	total := d1 + d2 + mod
	return checkRollGo{d1, d2, mod, tn, total, total - tn, total >= tn}, nil
}
func rollMapGo(r checkRollGo) map[string]any {
	return map[string]any{"die1": r.Die1, "die2": r.Die2, "modifier": r.Modifier, "tn": r.TN, "total": r.Total, "margin": r.Margin, "success": r.Success}
}
func recordSectAttemptGo(conn *storage.Conn, userID int64, sect, kind, npc, loc, result string, score, target, bonus, gm int64, details any, now float64) error {
	b, _ := json.Marshal(details)
	_, e := conn.Execute(`INSERT INTO sect_recruitment_attempts(user_id,sect_name,attempt_type,npc_name,location,result,score,target,recommendation_bonus,details_json,game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, sect, kind, npc, loc, result, score, target, bonus, string(b), gm, now})
	return e
}
func sectReputationScore(conn *storage.Conn, userID int64, sect string) (int64, error) {
	r, e := conn.Execute(`SELECT score FROM faction_reputation WHERE user_id=? AND faction_key=?`, []any{userID, sect})
	if e != nil {
		return 0, e
	}
	row := firstRowMap(r)
	if row == nil {
		return 0, nil
	}
	return i64(row["score"]), nil
}
func sectRecommendationActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectRecommendationPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	p.SectName = strings.TrimSpace(p.SectName)
	if p.NPCName == "" {
		return authoritativeMutation{}, errors.New("npc_name is required")
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot seek a sect recommendation")
	}
	// Whom the sponsor speaks for, and the gate their word reveals, are the
	// engine's (v1.1.0). Both used to be the caller's: the sect was taken on
	// trust and `location` was written straight onto the travel list, where a
	// road-less place is an instant jump. A caller naming a sect is still
	// heard - an older bot sends one - but only to refuse a mismatch.
	sponsored, e := resolveRecommenderTx(conn, catalog, p.NPCName, c, p.GameMinute, nowSeconds())
	if e != nil {
		return authoritativeMutation{}, e
	}
	if p.SectName != "" && p.SectName != sponsored {
		return authoritativeMutation{}, fmt.Errorf("%s speaks for the %s, not the %s", p.NPCName, sponsored, p.SectName)
	}
	p.SectName = sponsored
	gate := sectGate(catalog, p.SectName)
	if m, _ := sectMembershipRow(conn, userID); m != nil {
		return authoritativeMutation{}, errors.New("already belongs to a public sect")
	}
	r, e := conn.Execute(`SELECT 1 FROM sect_recommendations WHERE user_id=? AND sect_name=? AND status='active'`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) != nil {
		return authoritativeMutation{}, errors.New("an active recommendation already exists")
	}
	r, e = conn.Execute(`SELECT result,game_minute FROM sect_recruitment_attempts WHERE user_id=? AND sect_name=? AND attempt_type='recommendation' ORDER BY attempt_id DESC LIMIT 1`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if x := firstRowMap(r); x != nil && fmt.Sprint(x["result"]) == "fail" && p.GameMinute-i64(x["game_minute"]) < 1440 {
		return authoritativeMutation{}, errors.New("recommendation retry cooldown is still active")
	}
	repScore, e := sectReputationScore(conn, userID, p.SectName)
	if e != nil {
		return authoritativeMutation{}, e
	}
	// Canonical, not caller-supplied: how favorably an NPC is inclined to
	// vouch for this character derives from the character's own presence,
	// cultivation depth, and standing already earned with this sect - never
	// from a client-chosen modifier/TN/bonus.
	modifier := c.Attributes["presence"] + c.RealmIndex*2 + repScore/20
	tn := int64(14)
	roll, e := roll2d10Go(modifier, tn)
	if e != nil {
		return authoritativeMutation{}, e
	}
	bonus := int64(0)
	if roll.Success {
		bonus = clampI64(2+roll.Margin/3, 1, 6)
	}
	now := nowSeconds()
	route, newSect := false, false
	recID := int64(0)
	if roll.Success {
		_, _ = conn.Execute(`UPDATE sect_recommendations SET status='superseded',updated_at=? WHERE user_id=? AND sect_name=? AND status='active'`, []any{now, userID, p.SectName})
		ins, e := conn.Execute(`INSERT INTO sect_recommendations(user_id,npc_name,sect_name,bonus,status,issued_game_minute,created_at,updated_at) VALUES(?,?,?,?,'active',?,?,?)`, []any{userID, p.NPCName, p.SectName, bonus, p.GameMinute, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		recID = ins.LastInsertID
		shown, e := revealSectRouteTx(conn, catalog, userID, p.SectName, "npc_recommendation", p.NPCName, p.GameMinute, now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if shown != nil {
			route = shown["new_route"] == true
			newSect = shown["new_sect"] == true
		}
	}
	details := map[string]any{"roll": rollMapGo(roll), "route_revealed": route}
	for k, v := range p.Details {
		details[k] = v
	}
	if e = recordSectAttemptGo(conn, userID, p.SectName, "recommendation", p.NPCName, gate, map[bool]string{true: "pass", false: "fail"}[roll.Success], roll.Total, roll.TN, bonus, p.GameMinute, details, now); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"success": roll.Success, "roll": rollMapGo(roll), "recommendation_id": recID, "recommendation_bonus": bonus, "route_revealed": route, "new_sect": newSect, "gate": gate, "sect_name": p.SectName, "npc_name": p.NPCName}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.recruitment.recommendation", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// sectEntryManual picks the one manual a sect bestows on a new Outer
// Disciple (v0.21.3). Deterministic, from the catalog, on canon only:
//
//   - the sect's own entry inheritance first (v0.21.4): every public sect
//     authors one in content/world.json, tier 0, marked with its "sect".
//     Only when the character already has it does the general rule apply:
//   - alignment follows the sect: an Orthodox sect gives an Orthodox manual,
//     a Neutral sect a Neutral one (Orthodox if it has none), a Demonic sect a
//     Demonic one. A righteous sect never hands out a forbidden art.
//   - the character's own path first; any path of the right alignment only
//     if their path has nothing, so a Beast Binder joining a sword sect still
//     leaves with something.
//   - the lowest tier the character can already study, or - when nothing of
//     the right alignment and path is within reach yet, which is every
//     fresh Mortal-realm disciple - the lowest tier there is, to grow into
//     (manual.study still enforces min_realm_index at study time). The sect's
//     entry manual, not its treasure. Ties break on the manual id so the
//     choice never depends on map order.
//   - never one the character has already learned or already carries.
//
// Returns "" when the catalog has nothing that fits; the trial still passes.
func sectEntryManual(catalog worlddata.Catalog, sectName string, c mechanicsCharacter, owned map[string]bool) string {
	sectAlignment := strings.ToLower(strings.TrimSpace(catalog.Sects[sectName].Alignment))
	var allowed []string
	switch sectAlignment {
	case "demonic":
		allowed = []string{"demonic"}
	case "neutral":
		allowed = []string{"neutral", "orthodox"}
	default:
		allowed = []string{"orthodox"}
	}
	path := strings.TrimSpace(c.Path)
	own := ""
	for id, m := range catalog.TechniqueSystem.Manuals {
		if !strings.EqualFold(strings.TrimSpace(m.Sect), strings.TrimSpace(sectName)) || owned[id] || owned[m.ItemID] {
			continue
		}
		if own == "" || m.MinRealmIndex < catalog.TechniqueSystem.Manuals[own].MinRealmIndex || (m.MinRealmIndex == catalog.TechniqueSystem.Manuals[own].MinRealmIndex && id < own) {
			own = id
		}
	}
	if own != "" {
		return own
	}
	// Rank: within reach beats out of reach, then the lowest tier, then the id.
	better := func(id string, m worlddata.ManualDefinition, chosen string, chosenM worlddata.ManualDefinition) bool {
		if chosen == "" {
			return true
		}
		reach, chosenReach := m.MinRealmIndex <= c.RealmIndex, chosenM.MinRealmIndex <= c.RealmIndex
		if reach != chosenReach {
			return reach
		}
		if m.MinRealmIndex != chosenM.MinRealmIndex {
			return m.MinRealmIndex < chosenM.MinRealmIndex
		}
		return id < chosen
	}
	best := func(requirePath bool) string {
		for _, alignment := range allowed {
			chosen := ""
			var chosenM worlddata.ManualDefinition
			for id, m := range catalog.TechniqueSystem.Manuals {
				if strings.ToLower(strings.TrimSpace(m.Alignment)) != alignment {
					continue
				}
				if requirePath && !strings.EqualFold(strings.TrimSpace(m.Path), path) {
					continue
				}
				if owned[id] || owned[m.ItemID] {
					continue
				}
				if better(id, m, chosen, chosenM) {
					chosen, chosenM = id, m
				}
			}
			if chosen != "" {
				return chosen // the first alignment in preference order that has one wins
			}
		}
		return ""
	}
	if id := best(true); id != "" {
		return id
	}
	return best(false)
}

// ownedManualKeys: every manual id the character has learned and every
// manual item id they carry, so a gift is never a duplicate.
func ownedManualKeys(conn *storage.Conn, userID int64) (map[string]bool, error) {
	owned := map[string]bool{}
	r, e := conn.Execute(`SELECT manual_id FROM character_manuals WHERE user_id=?`, []any{userID})
	if e != nil {
		return nil, e
	}
	for _, row := range r.Rows {
		if len(row) > 0 {
			owned[fmt.Sprint(row[0])] = true
		}
	}
	r, e = conn.Execute(`SELECT item_id FROM inventory WHERE user_id=? AND quantity>0 AND item_id LIKE '%_manual'`, []any{userID})
	if e != nil {
		return nil, e
	}
	for _, row := range r.Rows {
		if len(row) > 0 {
			owned[fmt.Sprint(row[0])] = true
		}
	}
	return owned, nil
}

// sectTrialTuning is what a sect's own recruitment block adds to its trial.
type sectTrialTuning struct {
	BaseTN          int64
	PathBonus       int64
	RootAffinity    int64
	FamilyBonus     int64
	KarmaAdjustment int64
	Bonus           int64
	Rejection       string
}

func (t sectTrialTuning) Map() map[string]any {
	return map[string]any{"base_tn": t.BaseTN, "path_bonus": t.PathBonus, "root_affinity": t.RootAffinity, "family_bonus": t.FamilyBonus, "karma_adjustment": t.KarmaAdjustment, "bonus": t.Bonus}
}

// sectTrialDefaultTN is the trial's TN for a sect that authors none; every
// shipped sect authors one, and the Python profile reads the same default.
const sectTrialDefaultTN = int64(14)

// sectTrialTuningTx reads the sect's recruitment tuning against this
// character: the base TN, a bonus for the path the sect favours, one point for
// a root it has an affinity with, the tradition it keeps with a household,
// and the karma preference - which refuses an applicant on the wrong side of
// it outright unless a sponsor vouches, and otherwise moves the roll two
// against or one for. It is the engine's copy of `trial_modifier` in
// app/rules/sect_recruitment.py, which was the only copy until v1.2.4 and
// decided nothing (rc.48: a bound that lives in the client is not a bound).
func sectTrialTuningTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, sectName string, hasRecommendation bool) (sectTrialTuning, error) {
	rec := catalog.Sects[sectName].Recruitment
	t := sectTrialTuning{BaseTN: rec.BaseTN}
	if t.BaseTN <= 0 {
		t.BaseTN = sectTrialDefaultTN
	}
	t.BaseTN = maxI64(8, t.BaseTN)
	t.PathBonus = rec.PathBonuses[strings.TrimSpace(c.Path)]
	root := strings.ToLower(strings.TrimSpace(c.SpiritualRoot))
	for _, affinity := range rec.RootAffinities {
		if a := strings.ToLower(strings.TrimSpace(affinity)); a != "" && strings.Contains(root, a) {
			t.RootAffinity = 1
			break
		}
	}
	if len(rec.FamilyArchetypeBonus) > 0 {
		archetype, err := householdArchetypeTx(conn, userID)
		if err != nil {
			return t, err
		}
		t.FamilyBonus = rec.FamilyArchetypeBonus[archetype]
	}
	res, err := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return t, err
	}
	karma := int64(0)
	if row := firstRowMap(res); row != nil {
		karma = i64(row["karma_score"])
	}
	switch strings.ToLower(strings.TrimSpace(rec.KarmaPreference)) {
	case "righteous":
		switch {
		case karma <= -200 && !hasRecommendation:
			t.Rejection = "your karmic record is too notorious for this orthodox sect to admit you without a trusted sponsor"
		case karma <= -50:
			t.KarmaAdjustment = -2
		case karma >= 50:
			t.KarmaAdjustment = 1
		}
	case "demonic":
		switch {
		case karma >= 200 && !hasRecommendation:
			t.Rejection = "your strongly righteous reputation makes this demonic sect unwilling to expose its inner gate without a trusted sponsor"
		case karma >= 50:
			t.KarmaAdjustment = -2
		case karma <= -50:
			t.KarmaAdjustment = 1
		}
	}
	t.Bonus = t.PathBonus + t.RootAffinity + t.FamilyBonus + t.KarmaAdjustment
	return t, nil
}

func sectTrialActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectTrialPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot sit a sect entrance trial")
	}
	if m, _ := sectMembershipRow(conn, userID); m != nil {
		return authoritativeMutation{}, errors.New("already belongs to a public sect")
	}
	r, e := conn.Execute(`SELECT 1 FROM character_sect_discoveries WHERE user_id=? AND sect_name=?`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) == nil {
		return authoritativeMutation{}, errors.New("sect has not been discovered")
	}
	// The gate is the catalogue's, never the caller's (v1.1.0): "standing at
	// the gate" used to be whatever location the payload named. It is still
	// accepted on the wire, so an older bot mid-upgrade is not refused, and
	// ignored.
	gate := sectGate(catalog, p.SectName)
	if gate == "" {
		return authoritativeMutation{}, errors.New("that sect holds no public entrance trial")
	}
	if c.Location != gate {
		return authoritativeMutation{}, fmt.Errorf("the entrance trial is sat at %s; you are at %s", gate, c.Location)
	}
	p.Location = gate
	if examiner := strings.TrimSpace(catalog.Sects[p.SectName].Recruitment.Examiner); examiner != "" {
		p.Examiner = examiner
	}
	r, _ = conn.Execute(`SELECT result,game_minute FROM sect_recruitment_attempts WHERE user_id=? AND sect_name=? AND attempt_type='trial' ORDER BY attempt_id DESC LIMIT 1`, []any{userID, p.SectName})
	if x := firstRowMap(r); x != nil && fmt.Sprint(x["result"]) == "fail" && p.GameMinute-i64(x["game_minute"]) < 1440 {
		return authoritativeMutation{}, errors.New("entrance trial retry cooldown is still active")
	}
	r, e = conn.Execute(`SELECT * FROM sect_recommendations WHERE user_id=? AND sect_name=? AND status='active' ORDER BY recommendation_id DESC LIMIT 1`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	rec := firstRowMap(r)
	recBonus := int64(0)
	if rec != nil {
		recBonus = i64(rec["bonus"])
	}
	repScore, e := sectReputationScore(conn, userID, p.SectName)
	if e != nil {
		return authoritativeMutation{}, e
	}
	// Canonical, not caller-supplied: the primary (martial) and secondary
	// (spiritual) trial components derive from the character's own
	// attributes and cultivation depth, with existing sect standing making
	// the baseline slightly easier.
	//
	// A recommendation is worth its bonus on both rolls (v1.1.0). Four screens
	// have said "+N to the trial checks" since recommendations existed, while
	// this function added nothing and used the recommendation only to open the
	// conditional pass below - the one thing a sponsor did was the thing no
	// player was told. It now does both: the bonus rides the rolls, and a near
	// miss with a sponsor behind you is still a conditional pass.
	// What the sect authored for its own trial (v1.2.4): its base TN, the
	// paths it favours, the roots it has an affinity for, the households it
	// has a tradition with, and which side of the karma ledger it wants. The
	// bot has printed all of it in the trial notes since the block was written
	// and the engine read none of it, so every sect's trial was TN 15/14 and
	// a Sword Cultivator at the Azure Cloud gate got the same odds as anybody.
	tuning, e := sectTrialTuningTx(conn, catalog, userID, c, p.SectName, rec != nil)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if tuning.Rejection != "" {
		return authoritativeMutation{}, errors.New(tuning.Rejection)
	}
	primaryMod := c.Attributes["body"] + c.RealmIndex*2 + c.Phase/3 + recBonus + tuning.Bonus
	secondaryMod := c.Attributes["insight"] + c.Attributes["spirit"]/2 + c.RealmIndex + recBonus + tuning.Bonus
	baseTN := maxI64(10, tuning.BaseTN-repScore/25)
	primary, e := roll2d10Go(primaryMod, baseTN)
	if e != nil {
		return authoritativeMutation{}, e
	}
	secondary, e := roll2d10Go(secondaryMod, max64(8, baseTN-1))
	if e != nil {
		return authoritativeMutation{}, e
	}
	successes := 0
	if primary.Margin >= 0 {
		successes++
	}
	if secondary.Margin >= 0 {
		successes++
	}
	combined := primary.Margin + secondary.Margin
	outcome := "fail"
	if successes == 2 || combined >= 2 {
		outcome = "pass"
	} else if rec != nil && successes >= 1 && combined >= -2 {
		outcome = "conditional_pass"
	}
	now := nowSeconds()
	if rec != nil {
		_, e = conn.Execute(`UPDATE sect_recommendations SET status='used',used_game_minute=?,updated_at=? WHERE recommendation_id=? AND status='active'`, []any{p.GameMinute, now, i64(rec["recommendation_id"])})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	var granted map[string]any
	if outcome == "pass" || outcome == "conditional_pass" {
		_, e = conn.Execute(`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(?,?,'Outer Disciple',10,?) ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,rank_name=excluded.rank_name,rank_level=excluded.rank_level,joined_at=excluded.joined_at`, []any{userID, p.SectName, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if _, e = adjustReputationTx(conn, userID, p.SectName, 5, "Passed sect entrance trial", now); e != nil {
			return authoritativeMutation{}, e
		}
		// One manual on joining (v0.21.3): the sect's entry inheritance, in
		// the same transaction as the membership, so a new Outer Disciple
		// never exists without it and a rolled-back trial never grants one.
		owned, e := ownedManualKeys(conn, userID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if manualID := sectEntryManual(catalog, p.SectName, c, owned); manualID != "" {
			m := catalog.TechniqueSystem.Manuals[manualID]
			if e = addInventoryTx(conn, userID, map[string]int64{m.ItemID: 1}); e != nil {
				return authoritativeMutation{}, e
			}
			legal := "clean"
			if manualForbidden(m) {
				legal = "forbidden"
			}
			if _, e = conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(?,?,1,'sect_entry',?,?,?,100,0,?,?,?)`, []any{userID, m.ItemID, p.SectName, p.SectName + " entry inheritance", legal, p.GameMinute, now, now}); e != nil {
				return authoritativeMutation{}, e
			}
			granted = map[string]any{"manual_id": manualID, "name": m.Name, "item_id": m.ItemID, "alignment": m.Alignment, "path": m.Path, "min_realm_index": m.MinRealmIndex}
		}
	}
	details := map[string]any{"trial_name": p.TrialName, "primary_roll": rollMapGo(primary), "secondary_roll": rollMapGo(secondary), "primary_factors": p.PrimaryDetails, "secondary_factors": p.SecondaryDetails}
	if rec != nil {
		details["recommendation_source"] = fmt.Sprint(rec["npc_name"])
	}
	if e = recordSectAttemptGo(conn, userID, p.SectName, "trial", p.Examiner, p.Location, outcome, primary.Total+secondary.Total, primary.TN+secondary.TN, recBonus, p.GameMinute, details, now); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"outcome": outcome, "primary": rollMapGo(primary), "secondary": rollMapGo(secondary), "recommendation_bonus": recBonus, "sect_name": p.SectName, "gate": gate, "tuning": tuning.Map()}
	if granted != nil {
		out["granted_manual"] = granted
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.recruitment.trial", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func sectEconomyActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	var p sectItemPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.Quantity <= 0 {
		p.Quantity = 1
	}
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("you are not a member of a sect")
	}
	sect := fmt.Sprint(mem["sect_name"])
	item, _, ok := itemDef(catalog, p.ItemID)
	if !ok {
		return authoritativeMutation{}, errors.New("unknown item")
	}
	unit := max64(1, item.SectValue)
	now := nowSeconds()
	out := map[string]any{"sect_name": sect, "item_id": p.ItemID, "quantity": p.Quantity}
	if op == "sect.contribute" {
		q, e := inventoryQuantityTx(conn, userID, p.ItemID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if q < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough of that item")
		}
		if q == p.Quantity {
			_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		} else {
			_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, _ = conn.Execute(`INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING`, []any{sect, now})
		_, e = conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES(?,?,?) ON CONFLICT(sect_name,item_id) DO UPDATE SET quantity=sect_treasury.quantity+excluded.quantity`, []any{sect, p.ItemID, p.Quantity})
		if e != nil {
			return authoritativeMutation{}, e
		}
		points := p.Quantity * unit
		_, _ = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?`, []any{points, max64(1, points/10), userID})
		_, _ = conn.Execute(`UPDATE sect_lineage SET attention=attention+? WHERE disciple_user_id=?`, []any{max64(1, points/20), userID})
		out["points"] = points
	} else {
		r, e := conn.Execute(`SELECT resources FROM sect_politics_state WHERE sect_name=?`, []any{sect})
		if e != nil {
			return authoritativeMutation{}, e
		}
		resources := int64(50)
		if x := firstRowMap(r); x != nil {
			resources = i64(x["resources"])
		}
		mult := 1.0
		if resources < 25 {
			mult = 1.60
		} else if resources < 50 {
			mult = 1.35
		} else if resources < 80 {
			mult = 1.20
		}
		unit = max64(1, int64(math.Round(float64(unit)*mult)))
		cost := unit * p.Quantity
		points := i64(mem["contribution_points"])
		if points < cost {
			return authoritativeMutation{}, errors.New("not enough sect contribution points")
		}
		r, e = conn.Execute(`SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, []any{sect, p.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if x := firstRowMap(r); x == nil || i64(x["quantity"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("the sect treasury does not have enough of that item")
		}
		_, _ = conn.Execute(`UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?`, []any{p.Quantity, sect, p.ItemID})
		_, _ = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?`, []any{cost, userID})
		_, e = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{userID, p.ItemID, p.Quantity})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["unit_cost"] = unit
		out["cost"] = cost
		out["remaining_points"] = points - cost
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "sect", EntityID: sect, Payload: out}}, nil
}
func discipleshipActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	now := nowSeconds()
	var out map[string]any
	if op == "discipleship.request" {
		var p discipleRequestPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if p.MasterUserID == userID {
			return authoritativeMutation{}, errors.New("a cultivator cannot request themselves as master")
		}
		r, e := conn.Execute(`SELECT user_id,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)`, []any{userID, p.MasterUserID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		chars := rowsToMaps(r)
		if len(chars) != 2 {
			return authoritativeMutation{}, errors.New("both cultivators must have characters")
		}
		power := map[int64]int64{}
		for _, c := range chars {
			if fmt.Sprint(c["life_status"]) != "alive" {
				return authoritativeMutation{}, errors.New("master-disciple contracts require living incarnations")
			}
			power[i64(c["user_id"])] = i64(c["realm_index"])*10 + i64(c["phase"])
		}
		if power[p.MasterUserID] <= power[userID] {
			return authoritativeMutation{}, errors.New("requested master must have a higher cultivation stage")
		}
		r, _ = conn.Execute(`SELECT 1 FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("you already have a recorded master")
		}
		r, _ = conn.Execute(`SELECT user_id,sect_name FROM sect_membership WHERE user_id IN (?,?)`, []any{userID, p.MasterUserID})
		ms := rowsToMaps(r)
		if len(ms) == 2 && fmt.Sprint(ms[0]["sect_name"]) != fmt.Sprint(ms[1]["sect_name"]) {
			return authoritativeMutation{}, errors.New("master and disciple must belong to the same sect")
		}
		_, _ = conn.Execute(`UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending'`, []any{now, userID})
		c, e := conn.Execute(`INSERT INTO disciple_requests(disciple_user_id,master_user_id,status,created_at) VALUES(?,?,'pending',?)`, []any{userID, p.MasterUserID, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out = map[string]any{"request_id": c.LastInsertID, "disciple_user_id": userID, "master_user_id": p.MasterUserID, "status": "pending"}
	} else if op == "discipleship.resolve" {
		var p discipleResolvePayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		r, e := conn.Execute(`SELECT * FROM disciple_requests WHERE request_id=? AND status='pending'`, []any{p.RequestID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		req := firstRowMap(r)
		if req == nil {
			return authoritativeMutation{}, errors.New("pending disciple request does not exist")
		}
		if i64(req["master_user_id"]) != userID {
			return authoritativeMutation{}, errors.New("only the requested master can resolve this request")
		}
		disciple := i64(req["disciple_user_id"])
		if !p.Accept {
			_, e = conn.Execute(`UPDATE disciple_requests SET status='rejected',resolved_at=? WHERE request_id=?`, []any{now, p.RequestID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			out = map[string]any{"request_id": p.RequestID, "disciple_user_id": disciple, "master_user_id": userID, "status": "rejected"}
		} else {
			r, _ = conn.Execute(`SELECT 1 FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple})
			if firstRowMap(r) != nil {
				return authoritativeMutation{}, errors.New("that cultivator already has a recorded master")
			}
			r, e = conn.Execute(`SELECT user_id,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)`, []any{disciple, userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			power := map[int64]int64{}
			for _, c := range rowsToMaps(r) {
				if fmt.Sprint(c["life_status"]) != "alive" {
					return authoritativeMutation{}, errors.New("both master and disciple must still be living")
				}
				power[i64(c["user_id"])] = i64(c["realm_index"])*10 + i64(c["phase"])
			}
			if power[userID] <= power[disciple] {
				return authoritativeMutation{}, errors.New("requested master must still have a higher cultivation stage")
			}
			cursor := userID
			seen := map[int64]bool{disciple: true}
			for i := 0; i < 64; i++ {
				if seen[cursor] {
					return authoritativeMutation{}, errors.New("that contract would create a lineage cycle")
				}
				seen[cursor] = true
				rr, _ := conn.Execute(`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{cursor})
				x := firstRowMap(rr)
				if x == nil {
					break
				}
				cursor = i64(x["master_user_id"])
			}
			_, e = conn.Execute(`INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at,attention) VALUES(?,?,?,0)`, []any{disciple, userID, now})
			if e != nil {
				return authoritativeMutation{}, e
			}
			_, _ = conn.Execute(`UPDATE disciple_requests SET status='accepted',resolved_at=? WHERE request_id=?`, []any{now, p.RequestID})
			_, _ = conn.Execute(`UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending' AND request_id<>?`, []any{now, disciple, p.RequestID})
			out = map[string]any{"request_id": p.RequestID, "disciple_user_id": disciple, "master_user_id": userID, "status": "accepted"}
		}
	} else {
		r, e := conn.Execute(`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		x := firstRowMap(r)
		if x == nil {
			return authoritativeMutation{}, errors.New("no recorded master")
		}
		master := i64(x["master_user_id"])
		_, e = conn.Execute(`DELETE FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out = map[string]any{"disciple_user_id": userID, "master_user_id": master, "severed": true}
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "character", EntityID: fmt.Sprint(userID), Payload: out}}, nil
}

var sectManorFoundationCostGo = map[string]int64{"spirit_iron": 30, "spirit_herb": 20, "beast_core": 10}

type manorFacilityGo struct {
	Column string
	Cost   map[string]int64
}

var manorFacilitiesGo = map[string]manorFacilityGo{"qi_array": {"qi_array_level", map[string]int64{"spirit_herb": 8, "beast_core": 3, "spirit_iron": 2}}, "alchemy_hall": {"alchemy_hall_level", map[string]int64{"spirit_herb": 10, "beast_core": 2, "spirit_iron": 3}}, "forge_pavilion": {"forge_pavilion_level", map[string]int64{"spirit_iron": 10, "beast_core": 2, "spirit_herb": 2}}, "defense_array": {"defense_array_level", map[string]int64{"spirit_iron": 8, "beast_core": 4, "spirit_herb": 2}}}

func consumeSectTreasuryCostGo(conn *storage.Conn, sect string, cost map[string]int64) error {
	for item, qty := range cost {
		r, e := conn.Execute(`SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, []any{sect, item})
		if e != nil {
			return e
		}
		if x := firstRowMap(r); x == nil || i64(x["quantity"]) < qty {
			return fmt.Errorf("sect treasury is missing %s x%d", item, qty)
		}
	}
	for item, qty := range cost {
		if _, e := conn.Execute(`UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?`, []any{qty, sect, item}); e != nil {
			return e
		}
	}
	return nil
}
func sectManorActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	var p sectManorPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("you are not a sect member")
	}
	sect := fmt.Sprint(mem["sect_name"])
	now := nowSeconds()
	out := map[string]any{"sect_name": sect}
	if op == "sect.manor.establish" {
		if i64(mem["rank_level"]) < 70 {
			return authoritativeMutation{}, errors.New("only a Sect Master or Ancestor can establish the sect manor")
		}
		r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		c := firstRowMap(r)
		loc := fmt.Sprint(c["location"])
		if strings.HasPrefix(loc, "abode:") || strings.HasPrefix(loc, "personal_world:") {
			return authoritativeMutation{}, errors.New("choose a normal world location")
		}
		r, _ = conn.Execute(`SELECT 1 FROM sect_manors WHERE sect_name=?`, []any{sect})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("your sect already has a persistent manor")
		}
		name := strings.TrimSpace(p.Name)
		if len([]rune(name)) < 3 {
			return authoritativeMutation{}, errors.New("the sect manor needs a name of at least three characters")
		}
		if len([]rune(name)) > 80 {
			name = string([]rune(name)[:80])
		}
		if e = consumeSectTreasuryCostGo(conn, sect, sectManorFoundationCostGo); e != nil {
			return authoritativeMutation{}, e
		}
		_, e = conn.Execute(`INSERT INTO sect_manors(sect_name,name,base_location,founded_by_user_id,created_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?)`, []any{sect, name, loc, userID, p.GameMinute, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		costj, _ := json.Marshal(sectManorFoundationCostGo)
		_, _ = conn.Execute(`INSERT INTO sect_manor_projects(sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at) VALUES(?,?,'establish','foundation',0,0,?,?,?)`, []any{sect, userID, string(costj), p.GameMinute, now})
		out["name"] = name
		out["base_location"] = loc
	} else {
		f, ok := manorFacilitiesGo[p.Facility]
		if !ok {
			return authoritativeMutation{}, errors.New("unknown sect-manor facility")
		}
		if i64(mem["rank_level"]) < 50 {
			return authoritativeMutation{}, errors.New("only an Elder or higher-ranked sect member can direct manor construction")
		}
		r, e := conn.Execute(`SELECT * FROM sect_manors WHERE sect_name=?`, []any{sect})
		if e != nil {
			return authoritativeMutation{}, e
		}
		m := firstRowMap(r)
		if m == nil {
			return authoritativeMutation{}, errors.New("your sect has not established a manor")
		}
		r, _ = conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
		if c := firstRowMap(r); c == nil || fmt.Sprint(c["location"]) != fmt.Sprint(m["base_location"]) {
			return authoritativeMutation{}, errors.New("construction must be directed at the sect manor")
		}
		cur := i64(m[f.Column])
		if cur >= 5 {
			return authoritativeMutation{}, errors.New("facility is already at maximum level")
		}
		target := cur + 1
		cost := map[string]int64{}
		for item, base := range f.Cost {
			cost[item] = base * target
		}
		if e = consumeSectTreasuryCostGo(conn, sect, cost); e != nil {
			return authoritativeMutation{}, e
		}
		allowed := map[string]bool{"qi_array_level": true, "alchemy_hall_level": true, "forge_pavilion_level": true, "defense_array_level": true}
		if !allowed[f.Column] {
			return authoritativeMutation{}, errors.New("invalid facility column")
		}
		q := fmt.Sprintf("UPDATE sect_manors SET %s=?,updated_at=? WHERE sect_name=?", f.Column)
		if _, e = conn.Execute(q, []any{target, now, sect}); e != nil {
			return authoritativeMutation{}, e
		}
		costj, _ := json.Marshal(cost)
		_, _ = conn.Execute(`INSERT INTO sect_manor_projects(sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at) VALUES(?,?,'upgrade',?,?,?,?,?,?)`, []any{sect, userID, p.Facility, cur, target, string(costj), p.GameMinute, now})
		out["facility"] = p.Facility
		out["from_level"] = cur
		out["to_level"] = target
		out["cost"] = cost
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "sect", EntityID: sect, GameMinute: p.GameMinute, Payload: out}}, nil
}
