package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type aptitudeActionPayload struct {
	Target          string `json:"target"`
	GameMinute      int64  `json:"game_minute"`
	CooldownSeconds int64  `json:"cooldown_seconds"`
}

type mechanicsCharacter struct {
	Name                         string
	Gender                       string
	Path                         string
	SpiritualRoot                string
	Location                     string
	Attributes                   map[string]int64
	RealmIndex, Phase            int64
	BodyRealmIndex, BodyPhase    int64
	Cultivation, BodyCultivation int64
	LifeStatus                   string
}

type resolvedModifiers struct {
	Add map[string]float64
	Mul map[string]float64
	Set map[string]float64
}

func newResolvedModifiers() resolvedModifiers {
	return resolvedModifiers{Add: map[string]float64{}, Mul: map[string]float64{}, Set: map[string]float64{}}
}
func (m resolvedModifiers) apply(mod worlddata.Modifier, stacks int) {
	if stacks < 1 {
		stacks = 1
	}
	switch strings.ToLower(mod.Operation) {
	case "add", "":
		m.Add[mod.Stat] += mod.Value * float64(stacks)
	case "mul":
		if m.Mul[mod.Stat] == 0 {
			m.Mul[mod.Stat] = 1
		}
		m.Mul[mod.Stat] *= math.Pow(mod.Value, float64(stacks))
	case "set":
		m.Set[mod.Stat] = mod.Value
	}
}
func (m resolvedModifiers) value(base int64, stat string) int64 {
	v := float64(base)
	if x, ok := m.Set[stat]; ok {
		v = x
	}
	v += m.Add[stat]
	if x := m.Mul[stat]; x != 0 {
		v *= x
	}
	return int64(math.Round(v))
}

func loadMechanicsCharacter(conn *storage.Conn, userID int64) (mechanicsCharacter, error) {
	res, err := conn.Execute(`SELECT name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return mechanicsCharacter{}, err
	}
	if len(res.Rows) == 0 {
		return mechanicsCharacter{}, errors.New("create a cultivation character first")
	}
	r := res.Rows[0]
	attrs := map[string]int64{}
	var raw map[string]any
	if err := json.Unmarshal([]byte(fmt.Sprint(r[5])), &raw); err == nil {
		for k, v := range raw {
			attrs[k] = storage.ParseInt(v)
		}
	}
	return mechanicsCharacter{Name: fmt.Sprint(r[0]), Gender: fmt.Sprint(r[1]), Path: fmt.Sprint(r[2]), SpiritualRoot: fmt.Sprint(r[3]), Location: fmt.Sprint(r[4]), Attributes: attrs, RealmIndex: storage.ParseInt(r[6]), Phase: storage.ParseInt(r[7]), BodyRealmIndex: storage.ParseInt(r[8]), BodyPhase: storage.ParseInt(r[9]), Cultivation: storage.ParseInt(r[10]), BodyCultivation: storage.ParseInt(r[11]), LifeStatus: fmt.Sprint(r[12])}, nil
}

func loadAptitudes(conn *storage.Conn, userID int64) (AptitudeBundle, error) {
	var bundle AptitudeBundle
	rr, err := conn.Execute(`SELECT grade,purity,elements_json,mutation,stability,refinement_progress,compatibility FROM character_spiritual_roots WHERE user_id=?`, []any{userID})
	if err != nil {
		return bundle, err
	}
	if len(rr.Rows) == 0 {
		return bundle, errors.New("no spiritual-root profile exists")
	}
	r := rr.Rows[0]
	elements := []string{}
	_ = json.Unmarshal([]byte(fmt.Sprint(r[2])), &elements)
	bundle.Root = SpiritualRootState{Grade: fmt.Sprint(r[0]), Purity: intFromDB(r[1]), Elements: elements, Mutation: fmt.Sprint(r[3]), Stability: intFromDB(r[4]), RefinementProgress: intFromDB(r[5]), Compatibility: intFromDB(r[6])}
	br, err := conn.Execute(`SELECT bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,primary_lineage,unlocked_techniques_json FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1`, []any{userID})
	if err != nil {
		return bundle, err
	}
	if len(br.Rows) > 0 {
		r = br.Rows[0]
		tech := []string{}
		_ = json.Unmarshal([]byte(fmt.Sprint(r[10])), &tech)
		bundle.Bloodline = &BloodlineState{BloodlineID: fmt.Sprint(r[0]), Name: fmt.Sprint(r[1]), Affinity: fmt.Sprint(r[2]), Purity: intFromDB(r[3]), State: fmt.Sprint(r[4]), EvolutionStage: intFromDB(r[5]), Progress: intFromDB(r[6]), Rejection: intFromDB(r[7]), Mutation: fmt.Sprint(r[8]), PrimaryLineage: intFromDB(r[9]), UnlockedTechniques: tech}
	}
	pr, err := conn.Execute(`SELECT physique_id,name,state,evolution_stage,progress,stability,instability FROM character_physiques WHERE user_id=?`, []any{userID})
	if err != nil {
		return bundle, err
	}
	if len(pr.Rows) > 0 {
		r = pr.Rows[0]
		bundle.Physique = PhysiqueState{PhysiqueID: fmt.Sprint(r[0]), Name: fmt.Sprint(r[1]), State: fmt.Sprint(r[2]), EvolutionStage: intFromDB(r[3]), Progress: intFromDB(r[4]), Stability: intFromDB(r[5]), Instability: intFromDB(r[6])}
	} else {
		bundle.Physique = ordinaryPhysique()
	}
	return bundle, nil
}

func loadEffectModifiers(conn *storage.Conn, userID, gameMinute int64, bundle AptitudeBundle, c mechanicsCharacter, catalog worlddata.Catalog) (resolvedModifiers, error) {
	mods := newResolvedModifiers()
	if err := settleDueToxicityTx(conn, userID, gameMinute); err != nil {
		return mods, err
	}
	res, err := conn.Execute(`SELECT effect_json,stacks FROM active_effects WHERE user_id=? AND starts_game_minute<=? AND (ends_game_minute IS NULL OR ends_game_minute>?)`, []any{userID, gameMinute, gameMinute})
	if err != nil {
		return mods, err
	}
	for _, row := range res.Rows {
		var payload struct {
			Modifiers []worlddata.Modifier `json:"modifiers"`
		}
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &payload) == nil {
			stacks := intFromDB(row[1])
			for _, m := range payload.Modifiers {
				mods.apply(m, stacks)
			}
		}
	}
	if m, ok := catalog.SpiritualRootSystem.Mutations[bundle.Root.Mutation]; ok {
		for _, x := range m.Modifiers {
			mods.apply(x, 1)
		}
	}
	if b := bundle.Bloodline; b != nil {
		if def, ok := catalog.Bloodlines[b.BloodlineID]; ok {
			if b.State == "awakened" || b.State == "evolved" || b.State == "mutated" {
				stage := b.EvolutionStage
				if stage < 1 {
					stage = 1
				}
				if len(def.Evolutions) > 0 {
					idx := stage - 1
					if idx >= len(def.Evolutions) {
						idx = len(def.Evolutions) - 1
					}
					for _, x := range def.Evolutions[idx].Modifiers {
						mods.apply(x, 1)
					}
				}
			}
			if b.State == "rejected" {
				mods.apply(worlddata.Modifier{Stat: "will", Operation: "add", Value: -2}, 1)
			} else if b.Rejection >= 60 {
				mods.apply(worlddata.Modifier{Stat: "will", Operation: "add", Value: -1}, 1)
			}
		}
	}
	p := bundle.Physique
	if p.PhysiqueID != "ordinary_mortal_body" && (p.State == "awakened" || p.State == "evolved") {
		if def, ok := catalog.Physiques[p.PhysiqueID]; ok {
			stage := p.EvolutionStage
			if stage < 1 {
				stage = 1
			}
			if len(def.Evolutions) > 0 {
				idx := stage - 1
				if idx >= len(def.Evolutions) {
					idx = len(def.Evolutions) - 1
				}
				for _, x := range def.Evolutions[idx].Modifiers {
					mods.apply(x, 1)
				}
			}
			for _, x := range def.DrawbackModifiers {
				mods.apply(x, 1)
			}
		}
	}
	if p.Instability >= 60 {
		mods.apply(worlddata.Modifier{Stat: "will", Operation: "add", Value: -1}, 1)
	}
	_ = c
	return mods, nil
}

func phaseCost(realms []worlddata.Realm, index, phase int64) (int64, error) {
	if index < 0 || index >= int64(len(realms)) {
		return 0, errors.New("realm index out of range")
	}
	costs := realms[index].PhaseCosts
	if len(costs) == 0 {
		return 0, errors.New("realm has no phase costs")
	}
	p := phase
	if p < 1 {
		p = 1
	}
	if p > int64(len(costs)) {
		p = int64(len(costs))
	}
	return costs[p-1], nil
}
func cooldownRemaining(conn *storage.Conn, userID int64, action string, now float64) (int64, error) {
	res, err := conn.Execute(`SELECT available_at FROM cooldowns WHERE user_id=? AND action=?`, []any{userID, action})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 {
		return 0, nil
	}
	var available float64
	switch v := res.Rows[0][0].(type) {
	case float64:
		available = v
	case int64:
		available = float64(v)
	default:
		fmt.Sscan(fmt.Sprint(v), &available)
	}
	remaining := int64(math.Ceil(available - now))
	if remaining < 0 {
		return 0, nil
	}
	return remaining, nil
}
func setCooldown(conn *storage.Conn, userID int64, action string, seconds int64, now float64) error {
	if seconds < 0 {
		seconds = 0
	}
	_, err := conn.Execute(`INSERT INTO cooldowns(user_id,action,available_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET available_at=excluded.available_at`, []any{userID, action, now + float64(seconds)})
	return err
}
func roll2d10(modifier, tn int64) (map[string]any, error) {
	a, err := gamerng.D10()
	if err != nil {
		return nil, err
	}
	b, err := gamerng.D10()
	if err != nil {
		return nil, err
	}
	total := a + b + modifier
	margin := total - tn
	degree := "Severe Failure"
	switch {
	case margin >= 10:
		degree = "Overwhelming Success"
	case margin >= 5:
		degree = "Strong Success"
	case margin >= 0:
		degree = "Success"
	case margin >= -3:
		degree = "Soft Failure"
	case margin >= -7:
		degree = "Hard Failure"
	}
	return map[string]any{"die1": a, "die2": b, "modifier": modifier, "tn": tn, "total": total, "margin": margin, "success": total >= tn, "degree": degree, "probability": rollOdds(modifier, tn)}, nil
}
func boolResult(r map[string]any) bool    { v, _ := r["success"].(bool); return v }
func resultMargin(r map[string]any) int64 { return storage.ParseInt(r["margin"]) }

func aptitudeTemper(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p aptitudeActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Target = strings.ToLower(p.Target)
	if p.Target != "root" && p.Target != "bloodline" && p.Target != "physique" {
		return authoritativeMutation{}, errors.New("target must be root, bloodline, or physique")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot temper innate aptitudes")
	}
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var progress int
	switch p.Target {
	case "root":
		progress = bundle.Root.RefinementProgress
	case "bloodline":
		if bundle.Bloodline == nil {
			return authoritativeMutation{}, errors.New("you do not carry a recognized bloodline")
		}
		progress = bundle.Bloodline.Progress
	case "physique":
		if bundle.Physique.PhysiqueID == "ordinary_mortal_body" {
			return authoritativeMutation{}, errors.New("you do not possess a special physique to temper")
		}
		progress = bundle.Physique.Progress
	}
	if progress >= 100 {
		return authoritativeMutation{}, errors.New("aptitude progress is already 100%")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "aptitude_temper:" + p.Target
	if remaining, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("aptitude cooldown remaining: %d", remaining)
	}
	var stageCost, relevant, realm int64
	if p.Target == "physique" {
		stageCost, err = phaseCost(catalog.BodyRealms, c.BodyRealmIndex, c.BodyPhase)
		relevant = c.Attributes["body"]
		realm = c.BodyRealmIndex
	} else {
		stageCost, err = phaseCost(catalog.Realms, c.RealmIndex, c.Phase)
		if p.Target == "root" {
			relevant = c.Attributes["insight"]
		} else {
			relevant = c.Attributes["will"]
		}
		realm = c.RealmIndex
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	cost := stageCost / 12
	if cost < 3 {
		cost = 3
	}
	pool := c.Cultivation
	if p.Target == "physique" {
		pool = c.BodyCultivation
	}
	if pool < cost {
		return authoritativeMutation{}, fmt.Errorf("you need %d %scultivation essence", cost, map[bool]string{true: "body ", false: ""}[p.Target == "physique"])
	}
	r, _ := gamerng.Intn(7)
	gain := int64(5+r) + maxI64(0, relevant/2) + realm/3
	if gain > 20 {
		gain = 20
	}
	awarded := minI64(gain, int64(100-progress))
	poolColumn := "cultivation"
	if p.Target == "physique" {
		poolColumn = "body_cultivation"
	}
	if _, err = conn.Execute(fmt.Sprintf(`UPDATE characters SET %s=%s-?,updated_at=? WHERE user_id=?`, poolColumn, poolColumn), []any{cost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	switch p.Target {
	case "root":
		_, err = conn.Execute(`UPDATE character_spiritual_roots SET refinement_progress=refinement_progress+?,updated_at=? WHERE user_id=?`, []any{awarded, now, userID})
	case "bloodline":
		_, err = conn.Execute(`UPDATE character_bloodlines SET progress=progress+?,updated_at=? WHERE id=(SELECT id FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1)`, []any{awarded, now, userID})
	case "physique":
		_, err = conn.Execute(`UPDATE character_physiques SET progress=progress+?,updated_at=? WHERE user_id=?`, []any{awarded, now, userID})
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	cool := p.CooldownSeconds
	if cool < 300 {
		cool = 300
	}
	if err = setCooldown(conn, userID, key, cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	updated, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	legacy, _ := json.Marshal(map[string]any{"target": p.Target, "cost": cost, "progress": awarded})
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "aptitude_temper", string(legacy), now})
	result := map[string]any{"target": p.Target, "cost": cost, "awarded": awarded, "aptitudes": updated}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "aptitude_tempered", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func aptitudeHarmonize(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p aptitudeActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Target = strings.ToLower(p.Target)
	if p.Target != "root" && p.Target != "bloodline" && p.Target != "physique" {
		return authoritativeMutation{}, errors.New("target must be root, bloodline, or physique")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if p.Target == "bloodline" && bundle.Bloodline == nil {
		return authoritativeMutation{}, errors.New("you do not carry a recognized bloodline")
	}
	if p.Target == "physique" && bundle.Physique.PhysiqueID == "ordinary_mortal_body" {
		return authoritativeMutation{}, errors.New("an ordinary body has no special-physique instability")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "aptitude_harmonize:" + p.Target
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("aptitude cooldown remaining: %d", rem)
	}
	var stageCost int64
	if p.Target == "physique" {
		stageCost, err = phaseCost(catalog.BodyRealms, c.BodyRealmIndex, c.BodyPhase)
	} else {
		stageCost, err = phaseCost(catalog.Realms, c.RealmIndex, c.Phase)
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	cost := stageCost / 15
	if cost < 2 {
		cost = 2
	}
	pool := c.Cultivation
	if p.Target == "physique" {
		pool = c.BodyCultivation
	}
	if pool < cost {
		return authoritativeMutation{}, fmt.Errorf("you need %d cultivation essence", cost)
	}
	r, _ := gamerng.Intn(8)
	amount := int64(8+r) + maxI64(0, c.Attributes["will"]/2)
	col := "cultivation"
	if p.Target == "physique" {
		col = "body_cultivation"
	}
	if _, err = conn.Execute(fmt.Sprintf(`UPDATE characters SET %s=%s-?,updated_at=? WHERE user_id=?`, col, col), []any{cost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	switch p.Target {
	case "root":
		_, err = conn.Execute(`UPDATE character_spiritual_roots SET stability=MIN(100,stability+?),updated_at=? WHERE user_id=?`, []any{amount, now, userID})
	case "bloodline":
		_, err = conn.Execute(`UPDATE character_bloodlines SET rejection=MAX(0,rejection-?),state=CASE WHEN state='rejected' AND rejection-?<100 THEN 'dormant' ELSE state END,updated_at=? WHERE id=(SELECT id FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1)`, []any{amount, amount, now, userID})
	case "physique":
		_, err = conn.Execute(`UPDATE character_physiques SET instability=MAX(0,instability-?),stability=MIN(100,stability+?),updated_at=? WHERE user_id=?`, []any{amount, maxI64(1, amount/2), now, userID})
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	cool := p.CooldownSeconds
	if cool < 300 {
		cool = 300
	}
	if err = setCooldown(conn, userID, key, cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	updated, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	legacy, _ := json.Marshal(map[string]any{"target": p.Target, "cost": cost, "amount": amount})
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "aptitude_harmonize", string(legacy), now})
	result := map[string]any{"target": p.Target, "cost": cost, "amount": amount, "aptitudes": updated}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "aptitude_harmonized", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func bloodlineDefinition(b *BloodlineState, catalog worlddata.Catalog) (worlddata.BloodlineDefinition, bool) {
	if b == nil {
		return worlddata.BloodlineDefinition{}, false
	}
	if d, ok := catalog.Bloodlines[b.BloodlineID]; ok {
		return d, true
	}
	for _, d := range catalog.Bloodlines {
		if strings.EqualFold(d.Name, b.Name) {
			return d, true
		}
	}
	return worlddata.BloodlineDefinition{}, false
}
func progressionProblems(target, action string, b AptitudeBundle, c mechanicsCharacter, catalog worlddata.Catalog) []string {
	out := []string{}
	switch target {
	case "root":
		if action != "evolve" {
			return []string{"spiritual roots are awakened at birth; refine or evolve the root instead"}
		}
		idx := gradeIndex(catalog.SpiritualRootSystem, b.Root.Grade)
		if idx >= len(catalog.SpiritualRootSystem.Grades)-1 {
			return []string{"spiritual root is already at the highest configured grade"}
		}
		next := catalog.SpiritualRootSystem.Grades[idx+1]
		if b.Root.RefinementProgress < 100 {
			out = append(out, "root refinement must reach 100%")
		}
		if c.RealmIndex < int64(next.MinRealmToEvolve) {
			out = append(out, "qi realm is too low for the next root grade")
		}
		if b.Root.Stability < 35 {
			out = append(out, "root stability must be at least 35%")
		}
	case "bloodline":
		if b.Bloodline == nil {
			return []string{"you do not carry a recognized ancestral bloodline"}
		}
		def, _ := bloodlineDefinition(b.Bloodline, catalog)
		state := b.Bloodline.State
		if action == "awaken" {
			if state != "dormant" && state != "rejected" {
				out = append(out, "bloodline is already awakened")
			}
			if b.Bloodline.Progress < 100 {
				out = append(out, "bloodline tempering must reach 100%")
			}
			if b.Bloodline.Purity < def.AwakeningMinPurity {
				out = append(out, "bloodline purity is too diluted for awakening")
			}
			if c.RealmIndex < int64(def.AwakeningMinRealm) {
				out = append(out, "qi realm is too low for this awakening")
			}
			if b.Bloodline.Rejection >= 100 {
				out = append(out, "bloodline rejection must be harmonized below 100%")
			}
		} else {
			if state != "awakened" && state != "evolved" && state != "mutated" {
				out = append(out, "awaken the bloodline before evolving it")
			}
			stage := b.Bloodline.EvolutionStage
			if stage >= len(def.Evolutions) {
				out = append(out, "bloodline has reached its final evolution")
			} else {
				next := def.Evolutions[stage]
				if b.Bloodline.Progress < 100 {
					out = append(out, "bloodline evolution progress must reach 100%")
				}
				if b.Bloodline.Purity < next.MinPurity {
					out = append(out, "bloodline purity is too low")
				}
				if c.RealmIndex < int64(next.MinRealm) {
					out = append(out, "qi realm is too low")
				}
			}
		}
	case "physique":
		if b.Physique.PhysiqueID == "ordinary_mortal_body" {
			return []string{"you do not possess a dormant special physique"}
		}
		def := catalog.Physiques[b.Physique.PhysiqueID]
		state := b.Physique.State
		if action == "awaken" {
			if state != "dormant" {
				out = append(out, "physique is already awakened")
			}
			if b.Physique.Progress < 100 {
				out = append(out, "physique tempering must reach 100%")
			}
			if c.BodyRealmIndex < int64(def.AwakeningMinBodyRealm) {
				out = append(out, "body realm is too low for this awakening")
			}
			if b.Physique.Stability < 35 {
				out = append(out, "physique stability must be at least 35%")
			}
		} else {
			if state != "awakened" && state != "evolved" {
				out = append(out, "awaken the physique before evolving it")
			}
			stage := b.Physique.EvolutionStage
			if stage >= len(def.Evolutions) {
				out = append(out, "physique has reached its final evolution")
			} else {
				next := def.Evolutions[stage]
				if b.Physique.Progress < 100 {
					out = append(out, "physique evolution progress must reach 100%")
				}
				if c.BodyRealmIndex < int64(next.MinBodyRealm) {
					out = append(out, "body realm is too low")
				}
				if b.Physique.Stability < next.MinStability {
					out = append(out, "physique stability is too low")
				}
			}
		}
	}
	return out
}
func unlockedTechniques(b *BloodlineState, def worlddata.BloodlineDefinition) []string {
	if b == nil || (b.State != "awakened" && b.State != "evolved" && b.State != "mutated") {
		return []string{}
	}
	out := []string{}
	for _, t := range def.AncestralTechniques {
		if b.EvolutionStage >= t.Stage && b.Purity >= t.MinPurity {
			out = append(out, t.Name)
		}
	}
	return out
}

func saveRoot(conn *storage.Conn, userID int64, r SpiritualRootState, now float64) error {
	elements, _ := json.Marshal(r.Elements)
	_, err := conn.Execute(`UPDATE character_spiritual_roots SET grade=?,purity=?,elements_json=?,mutation=?,stability=?,refinement_progress=?,compatibility=?,updated_at=? WHERE user_id=?`, []any{r.Grade, clampInt(r.Purity, 1, 100), string(elements), r.Mutation, clampInt(r.Stability, 0, 100), clampInt(r.RefinementProgress, 0, 100), clampInt(r.Compatibility, 0, 100), now, userID})
	return err
}
func saveBloodline(conn *storage.Conn, userID int64, b *BloodlineState, now float64) error {
	if b == nil {
		return errors.New("bloodline missing")
	}
	tech, _ := json.Marshal(b.UnlockedTechniques)
	_, err := conn.Execute(`UPDATE character_bloodlines SET name=?,affinity=?,purity=?,state=?,evolution_stage=?,progress=?,rejection=?,mutation=?,unlocked_techniques_json=?,updated_at=? WHERE user_id=? AND bloodline_id=?`, []any{b.Name, b.Affinity, clampInt(b.Purity, 0, 100), b.State, maxInt(0, b.EvolutionStage), clampInt(b.Progress, 0, 100), clampInt(b.Rejection, 0, 100), b.Mutation, string(tech), now, userID, b.BloodlineID})
	return err
}
func savePhysique(conn *storage.Conn, userID int64, p PhysiqueState, now float64) error {
	_, err := conn.Execute(`UPDATE character_physiques SET physique_id=?,name=?,state=?,evolution_stage=?,progress=?,stability=?,instability=?,updated_at=? WHERE user_id=?`, []any{p.PhysiqueID, p.Name, p.State, maxInt(0, p.EvolutionStage), clampInt(p.Progress, 0, 100), clampInt(p.Stability, 0, 100), clampInt(p.Instability, 0, 100), now, userID})
	return err
}

func aptitudeAwaken(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p aptitudeActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Target = strings.ToLower(p.Target)
	if p.Target != "bloodline" && p.Target != "physique" {
		return authoritativeMutation{}, errors.New("awakening target must be bloodline or physique")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if problems := progressionProblems(p.Target, "awaken", bundle, c, catalog); len(problems) > 0 {
		return authoritativeMutation{}, errors.New(strings.Join(problems, "; "))
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "aptitude_awaken:" + p.Target
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("aptitude cooldown remaining: %d", rem)
	}
	mods, err := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var roll map[string]any
	var state string
	if p.Target == "bloodline" {
		b := bundle.Bloodline
		modifier := mods.value(c.Attributes["will"], "will") + int64(b.Purity/20) - int64(b.Rejection/25) + c.RealmIndex/2
		roll, err = roll2d10(modifier, 13)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if boolResult(roll) {
			def, _ := bloodlineDefinition(b, catalog)
			b.State = "awakened"
			b.EvolutionStage = 1
			b.Progress = 0
			b.Rejection = maxInt(0, b.Rejection-10)
			b.UnlockedTechniques = unlockedTechniques(b, def)
		} else {
			margin := resultMargin(roll)
			b.Rejection = minInt(100, b.Rejection+9+maxInt(0, int(-margin)))
			if b.Rejection >= 100 {
				b.State = "rejected"
			}
			if margin <= -7 {
				b.Mutation = "Divergent " + firstNonempty(b.Affinity, "Ancestral") + " Strain"
			}
		}
		if err = saveBloodline(conn, userID, b, now); err != nil {
			return authoritativeMutation{}, err
		}
		state = b.State
	} else {
		ph := bundle.Physique
		modifier := mods.value(c.Attributes["body"], "body") + maxI64(1, mods.value(c.Attributes["will"], "will")/2) + int64(ph.Stability/25) + c.BodyRealmIndex/2
		roll, err = roll2d10(modifier, 14)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if boolResult(roll) {
			ph.State = "awakened"
			ph.EvolutionStage = 1
			ph.Progress = 0
			ph.Instability = maxInt(0, ph.Instability-10)
		} else {
			margin := resultMargin(roll)
			ph.Instability = minInt(100, ph.Instability+9+maxInt(0, int(-margin)))
			ph.Stability = maxInt(0, ph.Stability-5)
		}
		if err = savePhysique(conn, userID, ph, now); err != nil {
			return authoritativeMutation{}, err
		}
		state = ph.State
	}
	if err = setCooldown(conn, userID, key, 6*3600, now); err != nil {
		return authoritativeMutation{}, err
	}
	updated, _ := loadAptitudes(conn, userID)
	payload := map[string]any{"target": p.Target, "roll": roll, "state": state, "aptitudes": updated}
	legacy, _ := json.Marshal(payload)
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, p.Target + "_awakening", string(legacy), now})
	return authoritativeMutation{Result: payload, Event: eventledger.Event{Domain: "character", EventType: "aptitude_awakening_attempted", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: payload}}, nil
}

func aptitudeEvolve(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p aptitudeActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Target = strings.ToLower(p.Target)
	if p.Target != "root" && p.Target != "bloodline" && p.Target != "physique" {
		return authoritativeMutation{}, errors.New("target must be root, bloodline, or physique")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if problems := progressionProblems(p.Target, "evolve", bundle, c, catalog); len(problems) > 0 {
		return authoritativeMutation{}, errors.New(strings.Join(problems, "; "))
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "aptitude_evolve:" + p.Target
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("aptitude cooldown remaining: %d", rem)
	}
	mods, err := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var roll map[string]any
	outcome := map[string]any{}
	switch p.Target {
	case "root":
		r := bundle.Root
		idx := gradeIndex(catalog.SpiritualRootSystem, r.Grade) + 1
		modifier := mods.value(c.Attributes["insight"], "insight") + maxI64(1, mods.value(c.Attributes["will"], "will")/2) + int64(r.Stability/25)
		roll, err = roll2d10(modifier, int64(13+idx))
		if err != nil {
			return authoritativeMutation{}, err
		}
		if boolResult(roll) {
			r.Grade = catalog.SpiritualRootSystem.Grades[idx].Name
			r.Purity = minInt(100, r.Purity+3)
			r.RefinementProgress = 0
			r.Stability = minInt(100, r.Stability+2)
		} else {
			margin := resultMargin(roll)
			r.Stability = maxInt(0, r.Stability-7-maxInt(0, int(-margin/2)))
			if margin <= -7 {
				eligible := []string{}
				for k, d := range catalog.SpiritualRootSystem.Mutations {
					if len(d.RequiresAny) == 0 {
						eligible = append(eligible, k)
						continue
					}
					for _, e := range r.Elements {
						if contains(d.RequiresAny, e) {
							eligible = append(eligible, k)
							break
						}
					}
				}
				if len(eligible) > 0 {
					i, _ := gamerng.Intn(len(eligible))
					r.Mutation = eligible[i]
				}
			}
		}
		r.Compatibility = rootCompatibility(r.Elements, c.Path, catalog.SpiritualRootSystem, r.Mutation)
		if err = saveRoot(conn, userID, r, now); err != nil {
			return authoritativeMutation{}, err
		}
		outcome = map[string]any{"grade": r.Grade, "stability": r.Stability, "root": r}
	case "bloodline":
		b := bundle.Bloodline
		def, _ := bloodlineDefinition(b, catalog)
		stage := b.EvolutionStage
		modifier := mods.value(c.Attributes["will"], "will") + int64(b.Purity/20) - int64(b.Rejection/25) + c.RealmIndex/2
		roll, err = roll2d10(modifier, int64(13+stage*2))
		if err != nil {
			return authoritativeMutation{}, err
		}
		if boolResult(roll) {
			b.State = "evolved"
			b.EvolutionStage = stage + 1
			b.Progress = 0
			b.Purity = minInt(100, b.Purity+4)
			b.Rejection = maxInt(0, b.Rejection-5)
			b.UnlockedTechniques = unlockedTechniques(b, def)
		} else {
			margin := resultMargin(roll)
			b.Purity = maxInt(1, b.Purity-2)
			b.Rejection = minInt(100, b.Rejection+10+maxInt(0, int(-margin)))
			if margin <= -7 {
				b.State = "mutated"
				b.Mutation = "Divergent " + firstNonempty(b.Affinity, "Ancestral") + " Strain"
			}
		}
		if err = saveBloodline(conn, userID, b, now); err != nil {
			return authoritativeMutation{}, err
		}
		outcome = map[string]any{"stage": b.EvolutionStage, "purity": b.Purity, "rejection": b.Rejection, "bloodline": b}
	case "physique":
		ph := bundle.Physique
		stage := ph.EvolutionStage
		modifier := mods.value(c.Attributes["body"], "body") + maxI64(1, mods.value(c.Attributes["will"], "will")/2) + int64(ph.Stability/25) - int64(ph.Instability/25)
		roll, err = roll2d10(modifier, int64(14+stage*2))
		if err != nil {
			return authoritativeMutation{}, err
		}
		if boolResult(roll) {
			ph.State = "evolved"
			ph.EvolutionStage = stage + 1
			ph.Progress = 0
			ph.Stability = minInt(100, ph.Stability+3)
			ph.Instability = maxInt(0, ph.Instability-5)
		} else {
			margin := resultMargin(roll)
			_ = margin
			ph.Stability = maxInt(0, ph.Stability-8)
			ph.Instability = minInt(100, ph.Instability+10+maxInt(0, int(-resultMargin(roll))))
		}
		if err = savePhysique(conn, userID, ph, now); err != nil {
			return authoritativeMutation{}, err
		}
		outcome = map[string]any{"stage": ph.EvolutionStage, "stability": ph.Stability, "instability": ph.Instability, "physique": ph}
	}
	if err = setCooldown(conn, userID, key, 12*3600, now); err != nil {
		return authoritativeMutation{}, err
	}
	updated, _ := loadAptitudes(conn, userID)
	result := map[string]any{"target": p.Target, "roll": roll, "outcome": outcome, "aptitudes": updated}
	legacy, _ := json.Marshal(result)
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, p.Target + "_evolution", string(legacy), now})
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "aptitude_evolution_attempted", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func minI64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
