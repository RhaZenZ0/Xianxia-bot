package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type lawComprehendPayload struct {
	Law        string `json:"law"`
	GameMinute int64  `json:"game_minute"`
}

func containsString(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}
func lawStage(catalog worlddata.Catalog, comp int64) worlddata.LawStage {
	cur := worlddata.LawStage{Index: 0, Name: "Unawakened", Min: 0}
	for _, s := range catalog.LawSystem.Stages {
		if comp >= int64(s.Min) {
			cur = s
		}
	}
	return cur
}
func soulLawBonus(conn *storage.Conn, userID int64) (int64, bool, error) {
	r, err := conn.Execute(`SELECT law_echo,memory_seed,awakened_memory,special_trait FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, false, err
	}
	if len(r.Rows) == 0 {
		return 0, false, nil
	}
	echo := storage.ParseInt(r.Rows[0][0])
	if echo < 0 {
		echo = 0
	}
	if echo > 100 {
		echo = 100
	}
	bonus := echo / 25
	trait := fmt.Sprint(r.Rows[0][3])
	switch trait {
	case "Born Knowing", "Heaven-Defying Fate", "Demonic Rebirth":
		bonus++
	case "Dao Memory":
		bonus += 2
	}
	return bonus, storage.ParseInt(r.Rows[0][1]) > storage.ParseInt(r.Rows[0][2]), nil
}
func lawComprehendAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p lawComprehendPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Law = strings.TrimSpace(p.Law)
	def, ok := catalog.LawSystem.Laws[p.Law]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown law")
	}
	cr, err := conn.Execute(`SELECT realm_index,spiritual_root,path FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(cr.Rows) == 0 {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	realm := storage.ParseInt(cr.Rows[0][0])
	root, path := fmt.Sprint(cr.Rows[0][1]), fmt.Sprint(cr.Rows[0][2])
	minimum := catalog.LawSystem.NormalMinRealmIndex
	if def.Supreme {
		minimum = catalog.LawSystem.SupremeMinRealmIndex
	}
	if realm < minimum {
		return authoritativeMutation{}, fmt.Errorf("realm %d required", minimum)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "law:" + p.Law
	remaining, err := cooldownRemaining(conn, userID, key, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	insight, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "insight")
	if err != nil {
		return authoritativeMutation{}, err
	}
	spirit, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "spirit")
	if err != nil {
		return authoritativeMutation{}, err
	}
	affinity := int64(0)
	if containsString(def.AffinityRoots, root) {
		affinity += 3
	}
	if containsString(def.AffinityPaths, path) {
		affinity += 3
	}
	legacyBonus, canAwaken, err := soulLawBonus(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := int64(17)
	if def.Supreme {
		tn += 5
	}
	roll, err := rollCheck(insight+spirit+affinity+legacyBonus, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gain := int64(1)
	if roll["success"].(bool) {
		gain = 2 + maxI64(0, roll["margin"].(int64))/3 + affinity/2
		if gain < 1 {
			gain = 1
		}
	}
	_, err = conn.Execute(`INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,law_id) DO UPDATE SET comprehension=MIN(100,law_progress.comprehension+excluded.comprehension),insights=law_progress.insights+1,updated_at=excluded.updated_at`, []any{userID, p.Law, gain, 1, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	lr, err := conn.Execute(`SELECT comprehension,insights FROM law_progress WHERE user_id=? AND law_id=?`, []any{userID, p.Law})
	if err != nil {
		return authoritativeMutation{}, err
	}
	comp := storage.ParseInt(lr.Rows[0][0])
	insights := storage.ParseInt(lr.Rows[0][1])
	dao := def.Dao
	if dao == "" {
		dao = def.Name
	}
	daoGain := maxI64(1, gain/4)
	_, err = conn.Execute(`INSERT INTO dao_progress(user_id,dao_id,progress,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,dao_id) DO UPDATE SET progress=MIN(100,dao_progress.progress+excluded.progress),updated_at=excluded.updated_at`, []any{userID, dao, daoGain, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	awakened := false
	if roll["success"].(bool) && canAwaken {
		_, err = conn.Execute(`UPDATE soul_legacy SET awakened_memory=MIN(memory_seed,awakened_memory+2),updated_at=? WHERE user_id=?`, []any{now, userID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		awakened = true
	}
	cd := catalog.LawSystem.ComprehendCooldownMinutes * 60
	if err = setCooldown(conn, userID, key, cd, now); err != nil {
		return authoritativeMutation{}, err
	}
	stage := lawStage(catalog, comp)
	result := map[string]any{"law": p.Law, "name": def.Name, "dao": dao, "roll": roll, "gain": gain, "comprehension": comp, "insights": insights, "dao_gain": daoGain, "stage_index": stage.Index, "stage_name": stage.Name, "affinity_bonus": affinity, "legacy_bonus": legacyBonus, "memory_awakened": awakened}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "law", EventType: "law_comprehended", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
