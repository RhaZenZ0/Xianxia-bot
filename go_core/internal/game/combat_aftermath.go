package game

import (
	"encoding/json"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
)

type combatAftermathResult struct {
	Impacts []string
}

func applyCombatAftermathTx(conn *storage.Conn, userID int64, b battleRow, outcome string, severity, gameMinute int64, now float64) (combatAftermathResult, error) {
	impacts := []string{}
	action := "npc_spared"
	if outcome == "kill" {
		action = "npc_killed"
	}
	target := b.NPCName
	loc := b.Location
	actorName := fmt.Sprintf("Cultivator %d", userID)
	if r, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{userID}); err == nil && len(r.Rows) > 0 {
		actorName = fmt.Sprint(r.Rows[0][0])
	}

	var attackerFamilyID int64
	if tableExistsTx(conn, "character_birth_family") {
		if r, err := conn.Execute(`SELECT family_id FROM character_birth_family WHERE user_id=?`, []any{userID}); err == nil && len(r.Rows) > 0 {
			attackerFamilyID = storage.ParseInt(r.Rows[0][0])
		}
	}

	var victimFamilyID int64
	var victimFamilyName string
	var bloodFeudName string
	var leadership map[string]any
	if action == "npc_killed" && tableExistsTx(conn, "birth_families") {
		isHead := false
		victimNPCID := int64(0)
		famRes, err := conn.Execute(`SELECT family_id,family_name,wealth,influence,stability,head_name,head_title,head_realm_index,head_phase,history_json FROM birth_families WHERE head_name=? ORDER BY family_id LIMIT 1`, []any{target})
		if err != nil {
			return combatAftermathResult{}, err
		}
		if len(famRes.Rows) > 0 {
			isHead = true
		} else if tableExistsTx(conn, "birth_family_npcs") {
			member, e := conn.Execute(`SELECT npc_id,family_id FROM birth_family_npcs WHERE name=? AND status='alive' ORDER BY npc_id LIMIT 1`, []any{target})
			if e != nil {
				return combatAftermathResult{}, e
			}
			if len(member.Rows) > 0 {
				victimNPCID = storage.ParseInt(member.Rows[0][0])
				victimFamilyID = storage.ParseInt(member.Rows[0][1])
				famRes, err = conn.Execute(`SELECT family_id,family_name,wealth,influence,stability,head_name,head_title,head_realm_index,head_phase,history_json FROM birth_families WHERE family_id=?`, []any{victimFamilyID})
				if err != nil {
					return combatAftermathResult{}, err
				}
			}
		}
		if len(famRes.Rows) > 0 {
			f := famRes.Rows[0]
			victimFamilyID = storage.ParseInt(f[0])
			victimFamilyName = fmt.Sprint(f[1])
			if tableExistsTx(conn, "birth_family_npcs") {
				if victimNPCID > 0 {
					if _, err = conn.Execute(`UPDATE birth_family_npcs SET status='dead' WHERE npc_id=?`, []any{victimNPCID}); err != nil {
						return combatAftermathResult{}, err
					}
				} else {
					if _, err = conn.Execute(`UPDATE birth_family_npcs SET status='dead' WHERE family_id=? AND name=? AND status='alive'`, []any{victimFamilyID, target}); err != nil {
						return combatAftermathResult{}, err
					}
				}
			}
			wealthLoss := severity
			influenceLoss := severity * 2
			stabilityLoss := severity * 3
			if isHead {
				wealthLoss, influenceLoss, stabilityLoss = severity*2, severity*4, severity*6
			}
			wealth := maxI64(0, storage.ParseInt(f[2])-wealthLoss)
			influence := maxI64(0, storage.ParseInt(f[3])-influenceLoss)
			stability := maxI64(0, storage.ParseInt(f[4])-stabilityLoss)
			score := wealth + influence + stability
			tier := int64(1)
			switch {
			case score >= 245:
				tier = 5
			case score >= 185:
				tier = 4
			case score >= 125:
				tier = 3
			case score >= 70:
				tier = 2
			}
			headName, headTitle := fmt.Sprint(f[5]), fmt.Sprint(f[6])
			headRealm, headPhase := storage.ParseInt(f[7]), storage.ParseInt(f[8])
			history := []string{}
			_ = json.Unmarshal([]byte(fmt.Sprint(f[9])), &history)
			if isHead {
				successorName := "Vacant Ancestral Seat"
				successorTitle := "Acting Family Council"
				successorRealm, successorPhase := int64(0), int64(1)
				if tableExistsTx(conn, "birth_family_npcs") {
					succ, e := conn.Execute(`SELECT name,realm_index,phase FROM birth_family_npcs WHERE family_id=? AND status='alive' ORDER BY realm_index DESC,phase DESC,npc_id ASC LIMIT 1`, []any{victimFamilyID})
					if e != nil {
						return combatAftermathResult{}, e
					}
					if len(succ.Rows) > 0 {
						successorName, successorTitle = fmt.Sprint(succ.Rows[0][0]), headTitle
						successorRealm, successorPhase = storage.ParseInt(succ.Rows[0][1]), storage.ParseInt(succ.Rows[0][2])
						history = append(history, fmt.Sprintf("%s, the family leader, was killed by an outside cultivator. %s inherited the seat amid severe instability.", target, successorName))
					} else {
						history = append(history, fmt.Sprintf("%s, the family leader, was killed. No clear successor remained, and an acting council took control.", target))
					}
				}
				headName, headTitle, headRealm, headPhase = successorName, successorTitle, successorRealm, successorPhase
				impacts = append(impacts, victimFamilyName+" lost its leader and suffered a succession crisis")
				leadership = map[string]any{"family_id": victimFamilyID, "family_name": victimFamilyName, "old_leader": target, "new_leader": headName, "new_title": headTitle}
			} else {
				history = append(history, fmt.Sprintf("%s, a member of the family, was killed by an outside cultivator, weakening the household.", target))
				impacts = append(impacts, victimFamilyName+" suffered a bloodline casualty")
			}
			if len(history) > 100 {
				history = history[len(history)-100:]
			}
			histJSON, _ := json.Marshal(history)
			if _, err = conn.Execute(`UPDATE birth_families SET wealth=?,influence=?,stability=?,tier=?,head_name=?,head_title=?,head_realm_index=?,head_phase=?,history_json=?,updated_at=? WHERE family_id=?`, []any{wealth, influence, stability, tier, headName, headTitle, headRealm, headPhase, string(histJSON), now, victimFamilyID}); err != nil {
				return combatAftermathResult{}, err
			}
			if tableExistsTx(conn, "martial_clan_branches") {
				loyaltyLoss, strengthLoss := severity, severity*2
				if isHead {
					loyaltyLoss, strengthLoss = severity*3, severity*2
				}
				if _, err = conn.Execute(`UPDATE martial_clan_branches SET loyalty=MAX(0,loyalty-?),martial_strength=MAX(0,martial_strength-?),updated_at=? WHERE family_id=? AND status='active'`, []any{loyaltyLoss, strengthLoss, now, victimFamilyID}); err != nil {
					return combatAftermathResult{}, err
				}
			}
			if tableExistsTx(conn, "martial_clan_retainers") {
				loss := severity * 2
				if isHead {
					loss = severity * 4
				}
				if _, err = conn.Execute(`UPDATE martial_clan_retainers SET loyalty=MAX(0,loyalty-?),updated_at=? WHERE family_id=? AND status='active'`, []any{loss, now, victimFamilyID}); err != nil {
					return combatAftermathResult{}, err
				}
				if _, err = conn.Execute(`UPDATE martial_clan_retainers SET status='deserted',updated_at=? WHERE family_id=? AND status='active' AND loyalty<10`, []any{now, victimFamilyID}); err != nil {
					return combatAftermathResult{}, err
				}
			}
			if isHead && tableExistsTx(conn, "martial_clan_relations") {
				if _, err = conn.Execute(`UPDATE martial_clan_relations SET relation_score=MAX(-100,relation_score-?),updated_at=? WHERE family_id=? AND active=1`, []any{severity * 3, now, victimFamilyID}); err != nil {
					return combatAftermathResult{}, err
				}
			}
			if attackerFamilyID > 0 && attackerFamilyID != victimFamilyID && tableExistsTx(conn, "martial_clan_relations") {
				attackerName := fmt.Sprintf("Family %d", attackerFamilyID)
				if ar, e := conn.Execute(`SELECT family_name FROM birth_families WHERE family_id=?`, []any{attackerFamilyID}); e == nil && len(ar.Rows) > 0 {
					attackerName = fmt.Sprint(ar.Rows[0][0])
				}
				rel, e := conn.Execute(`SELECT relation_id FROM martial_clan_relations WHERE family_id=? AND partner_family_id=? AND active=1 LIMIT 1`, []any{victimFamilyID, attackerFamilyID})
				if e != nil {
					return combatAftermathResult{}, e
				}
				feudScore := -minI64(100, 45+severity*5)
				if len(rel.Rows) > 0 {
					_, err = conn.Execute(`UPDATE martial_clan_relations SET relation_type='blood_feud',relation_score=?,updated_at=? WHERE relation_id=?`, []any{feudScore, now, storage.ParseInt(rel.Rows[0][0])})
				} else {
					_, err = conn.Execute(`INSERT INTO martial_clan_relations(family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at) VALUES(?,?,?,'blood_feud',?,1,?,?)`, []any{victimFamilyID, attackerFamilyID, attackerName, feudScore, gameMinute, now})
				}
				if err != nil {
					return combatAftermathResult{}, err
				}
				impacts = append(impacts, "a blood feud formed against "+attackerName)
				bloodFeudName = attackerName
			}
		}
	}

	npcFaction := ""
	if tableExistsTx(conn, "npc_civilization_state") {
		nr, err := conn.Execute(`SELECT current_location,profession,faction,influence FROM npc_civilization_state WHERE npc_name=?`, []any{target})
		if err != nil {
			return combatAftermathResult{}, err
		}
		if len(nr.Rows) > 0 {
			n := nr.Rows[0]
			currentLoc := fmt.Sprint(n[0])
			if strings.TrimSpace(currentLoc) == "" {
				currentLoc = loc
			}
			if action == "npc_killed" {
				if _, err = conn.Execute(`UPDATE npc_civilization_state SET status='dead',activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?`, []any{fmt.Sprintf("Killed by player %d", userID), gameMinute, now, target}); err != nil {
					return combatAftermathResult{}, err
				}
				regionSev := maxI64(severity, 1+storage.ParseInt(n[3])/25)
				if tableExistsTx(conn, "civilization_regions") {
					if _, err = conn.Execute(`UPDATE civilization_regions SET security=MAX(0,security-?),unrest=MIN(100,unrest+?),prosperity=MAX(0,prosperity-?),last_game_minute=?,updated_at=? WHERE location=?`, []any{regionSev * 2, regionSev * 3, maxI64(1, regionSev), gameMinute, now, currentLoc}); err != nil {
						return combatAftermathResult{}, err
					}
				}
				if tableExistsTx(conn, "civilization_events") {
					_, _ = conn.Execute(`INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{currentLoc, target + " was killed by a cultivator, destabilizing local power networks.", regionSev, gameMinute, now})
				}
				impacts = append(impacts, target+"'s death increased regional unrest")
				tradeDisruption := regionSev
				profession := strings.ToLower(fmt.Sprint(n[1]))
				if strings.Contains(profession, "merchant") || strings.Contains(profession, "caravan") || strings.Contains(profession, "trader") || strings.Contains(profession, "auction") {
					tradeDisruption *= 2
				}
				if tableExistsTx(conn, "economy_markets") {
					if _, err = conn.Execute(`UPDATE economy_markets SET supply=MAX(1,supply-?),demand=MIN(500,demand+?),updated_at=? WHERE location=?`, []any{tradeDisruption, maxI64(1, regionSev/2), now, currentLoc}); err != nil {
						return combatAftermathResult{}, err
					}
				}
				if tableExistsTx(conn, "economy_events") {
					_, _ = conn.Execute(`INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,NULL,?,?,?)`, []any{currentLoc, "The death of " + target + " disrupted local confidence and short-term supply routes.", gameMinute, now})
				}
				faction := fmt.Sprint(n[2])
				if faction != "" && faction != "Independent" {
					npcFaction = faction
					if tableExistsTx(conn, "sect_politics_state") {
						if _, err = conn.Execute(`UPDATE sect_politics_state SET influence=MAX(0,influence-?),cohesion=MAX(0,cohesion-?),resources=MAX(0,resources-?),updated_at=? WHERE sect_name=?`, []any{regionSev * 2, regionSev * 3, regionSev, now, faction}); err != nil {
							return combatAftermathResult{}, err
						}
					}
					if tableExistsTx(conn, "sect_politics_events") {
						_, _ = conn.Execute(`INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{faction, "The death of " + target + " damaged the sect's influence and triggered internal blame.", regionSev, gameMinute, now})
					}
					impacts = append(impacts, faction+" lost influence and cohesion")
				}
			} else {
				if _, err = conn.Execute(`UPDATE npc_civilization_state SET activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?`, []any{fmt.Sprintf("Defeated and spared by player %d", userID), gameMinute, now, target}); err != nil {
					return combatAftermathResult{}, err
				}
				impacts = append(impacts, target+" survived and remembers being spared")
			}
		}
	}

	details := map[string]any{"battle_id": b.BattleID, "source": b.Source, "impacts": impacts}
	if tableExistsTx(conn, "world_action_events") {
		enc, _ := json.Marshal(details)
		if _, err := conn.Execute(`INSERT INTO world_action_events(user_id,action_type,target_type,target_key,location,severity,game_minute,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, action, "npc", target, loc, severity, gameMinute, string(enc), now}); err != nil {
			return combatAftermathResult{}, err
		}
	}
	uid := userID
	if action == "npc_killed" {
		summary := fmt.Sprintf("%s was killed by %s at %s.", target, actorName, loc)
		if len(impacts) > 0 {
			summary += " Consequences: " + strings.Join(impacts, "; ")
		}
		tags := []string{"death", "battle", "npc"}
		if severity >= 5 {
			tags = append(tags, "major battle")
		}
		if err := recordWorldHistoryTx(conn, fmt.Sprintf("npc_death:%s:%d:%d", target, gameMinute, userID), "death", "Death of "+target, summary, minI64(100, 65+severity*4), "public", loc, npcFaction, "player", fmt.Sprint(userID), actorName, "npc", target, target, &uid, target, tags, gameMinute, map[string]any{"severity": severity, "impacts": impacts, "battle_id": b.BattleID, "source": b.Source}, now); err != nil {
			return combatAftermathResult{}, err
		}
		if severity >= 5 {
			if err := recordWorldHistoryTx(conn, fmt.Sprintf("major_battle:%d:kill", b.BattleID), "major_battle", "Major battle: "+actorName+" defeated "+target, fmt.Sprintf("A high-stakes battle at %s ended with %s's death at the hands of %s.", loc, target, actorName), minI64(100, 72+severity*3), "public", loc, npcFaction, "player", fmt.Sprint(userID), actorName, "npc", target, target, &uid, target, []string{"major battle", "battle", "victory", "death"}, gameMinute, map[string]any{"severity": severity, "battle_id": b.BattleID, "source": b.Source}, now); err != nil {
				return combatAftermathResult{}, err
			}
		}
	} else if severity >= 4 {
		if err := recordWorldHistoryTx(conn, fmt.Sprintf("major_battle:%d:spare", b.BattleID), "major_battle", actorName+" defeated and spared "+target, fmt.Sprintf("A major battle at %s ended when %s defeated %s but chose to spare their life.", loc, actorName, target), minI64(94, 68+severity*3), "public", loc, npcFaction, "player", fmt.Sprint(userID), actorName, "npc", target, target, &uid, target, []string{"major battle", "battle", "mercy", "spared"}, gameMinute, map[string]any{"severity": severity, "battle_id": b.BattleID, "source": b.Source}, now); err != nil {
			return combatAftermathResult{}, err
		}
	}
	if leadership != nil {
		if err := recordWorldHistoryTx(conn, fmt.Sprintf("leadership:%d:%d", victimFamilyID, gameMinute), "leadership_change", "Leadership changed in "+victimFamilyName, fmt.Sprintf("After %s was killed, %s assumed the leadership of %s as %s.", target, leadership["new_leader"], victimFamilyName, leadership["new_title"]), 88, "public", loc, victimFamilyName, "clan", fmt.Sprint(victimFamilyID), victimFamilyName, "leader", fmt.Sprint(leadership["new_leader"]), fmt.Sprint(leadership["new_leader"]), nil, "", []string{"leadership", "succession", "clan", "death"}, gameMinute, leadership, now); err != nil {
			return combatAftermathResult{}, err
		}
	}
	if bloodFeudName != "" && victimFamilyName != "" {
		if err := recordWorldHistoryTx(conn, fmt.Sprintf("blood_feud:%d:%s:%d", victimFamilyID, bloodFeudName, gameMinute), "blood_feud", "Blood feud: "+victimFamilyName+" and "+bloodFeudName, fmt.Sprintf("The death of %s hardened relations into a blood feud between %s and %s.", target, victimFamilyName, bloodFeudName), 82, "public", loc, victimFamilyName, "clan", fmt.Sprint(victimFamilyID), victimFamilyName, "clan", bloodFeudName, bloodFeudName, nil, "", []string{"blood feud", "grudge", "clan", "death"}, gameMinute, map[string]any{"victim": target, "severity": severity}, now); err != nil {
			return combatAftermathResult{}, err
		}
	}
	return combatAftermathResult{Impacts: impacts}, nil
}
