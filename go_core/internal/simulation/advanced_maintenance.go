package simulation

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/storage"
)

var bountyHunterTitles = []string{
	"Iron Badge Constable", "Black-Cloak Pursuer", "Seven Provinces Tracker",
	"Spirit-Hound Warden", "Heavenly Warrant Enforcer", "Jade Tribunal Hunter",
}

type eraTemplate struct {
	Name, Description string
	DurationDays      int64
	Modifiers         map[string]float64
}

var eraCycle = []eraTemplate{
	{"Jade Meridian Awakening Era", "Spiritual veins awaken and new inheritances surface across the four worlds.", 180, map[string]float64{"cultivation_gain": 1.05, "secret_realm_frequency": 1.10}},
	{"Hundred Sects Strife Era", "Competition over spirit veins hardens into open territorial conflict.", 120, map[string]float64{"war_pressure": 1.25, "market_volatility": 1.10}},
	{"Beast Tide Era", "Ancient bloodlines stir and spirit beasts migrate in destructive tides.", 90, map[string]float64{"beast_encounter_rate": 1.35, "caravan_risk": 1.15}},
	{"Quiet Heaven Era", "After upheaval, the heavens settle and orthodox institutions rebuild order.", 150, map[string]float64{"recovery_rate": 1.10, "crime_pressure": 0.85}},
}

func stablePercent(parts ...any) int64 {
	vals := make([]string, 0, len(parts))
	for _, p := range parts {
		vals = append(vals, fmt.Sprint(p))
	}
	sum := sha256.Sum256([]byte(strings.Join(vals, "|")))
	return int64(binary.BigEndian.Uint32(sum[:4]) % 100)
}

func float64Value(v any, fallback float64) float64 {
	if v == nil {
		return fallback
	}
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	}
	f, err := strconv.ParseFloat(fmt.Sprint(v), 64)
	if err != nil {
		return fallback
	}
	return f
}

func (r *Runner) eraModifiers(conn *storage.Conn) (map[string]float64, error) {
	res, err := conn.Execute(`SELECT modifiers_json FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1`, nil)
	if err != nil {
		return nil, err
	}
	out := map[string]float64{}
	row := firstMap(res)
	if row == nil {
		return out, nil
	}
	var raw map[string]any
	if json.Unmarshal([]byte(fmt.Sprint(row["modifiers_json"])), &raw) == nil {
		for k, v := range raw {
			out[k] = float64Value(v, 1)
		}
	}
	return out, nil
}

func walletDeltaSim(conn *storage.Conn, userID int64, currency string, delta int64) error {
	res, err := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{userID, currency})
	if err != nil {
		return err
	}
	current := int64(0)
	if row := firstMap(res); row != nil {
		current = i64(row["balance"])
	}
	next := current + delta
	if next < 0 {
		return fmt.Errorf("insufficient %s", currency)
	}
	if _, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`, []any{userID, currency, next}); err != nil {
		return err
	}
	if currency == "low_spirit_stone" {
		_, err = conn.Execute(`UPDATE characters SET spirit_stones=?,updated_at=? WHERE user_id=?`, []any{next, nowFloat(), userID})
	}
	return err
}

func hasBoolKey(m map[string]bool, key string) bool { _, ok := m[key]; return ok }

func (r *Runner) advancedMaintenance(conn *storage.Conn, gm int64, automation map[string]bool) (Run, bool, error) {
	probe, err := conn.Execute(`SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='auctions' LIMIT 1`, nil)
	if err != nil {
		return Run{}, false, err
	}
	if firstMap(probe) == nil {
		return Run{}, false, nil
	}
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return Run{}, false, err
	}
	ok := false
	defer func() {
		if !ok {
			_ = conn.Rollback()
		}
	}()
	counts := map[string]int64{}
	eraChanged := false
	if !hasBoolKey(automation, "auction_settlement") || automation["auction_settlement"] {
		counts["auctions"], err = r.finalizeAuctions(conn, gm)
		if err != nil {
			return Run{}, false, err
		}
	}
	// Merchants (v0.34.1): the traders walk their loops on the same tick
	// that settles the floors, after settlement so a lot bought this tick
	// is in a pack before the pack moves.
	if !hasBoolKey(automation, "merchants") || automation["merchants"] {
		counts["merchants"], err = game.AdvanceMerchants(conn, r.World, gm)
		if err != nil {
			return Run{}, false, err
		}
		// ...and bid on the open lots (v0.37.0), after settlement so a lot
		// struck this tick is not bid on.
		counts["merchant_bids"], err = game.MerchantsBid(conn, r.World, gm)
		if err != nil {
			return Run{}, false, err
		}
	}
	// Secret realms (v0.39.0): the rotation opens the next one in turn, so
	// every world always has a realm coming whether or not anyone is
	// standing at its entrance.
	if !hasBoolKey(automation, "secret_realms") || automation["secret_realms"] {
		counts["secret_realms"], err = game.RotateSecretRealms(conn, r.World, gm)
		if err != nil {
			return Run{}, false, err
		}
	}
	counts["hunters_spawned"], err = r.spawnHunters(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	counts["hunters_updated"], err = r.advanceHunters(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	counts["wars"], err = r.advanceWars(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	counts["occupations"], err = r.advanceOccupations(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	counts["caravans"], err = r.advanceCaravans(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	eraChanged, err = r.advanceEra(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	if !hasBoolKey(automation, "background_seclusion") || automation["background_seclusion"] {
		counts["seclusions"], err = r.advanceSeclusions(conn, gm)
		if err != nil {
			return Run{}, false, err
		}
	}
	// Commissions (v0.22.0): a deadline that only fired when someone opened
	// Discord would not be a deadline. The engine tick is what makes `failed`
	// real, and it survives a restart because it reads the stored deadline
	// rather than a timer.
	counts["commissions_expired"], err = r.expireCommissions(conn, gm)
	if err != nil {
		return Run{}, false, err
	}
	// Moderation (v0.32.0): a mute or freeze given a duration ends when the
	// clock says so. checkPlayerModerationTx already treats a lapsed flag as
	// over; this is what clears the row so the dashboard and /admin player
	// inspect agree with it.
	counts["moderations_expired"], err = r.expireModerations(conn)
	if err != nil {
		return Run{}, false, err
	}
	if err = conn.Commit(); err != nil {
		return Run{}, false, err
	}
	ok = true
	changed := eraChanged
	for _, n := range counts {
		if n > 0 {
			changed = true
		}
	}
	if !changed {
		return Run{}, false, nil
	}
	summary := fmt.Sprintf("auctions=%d merchants=%d merchant_bids=%d secret_realms=%d hunters_spawned=%d hunters_updated=%d wars=%d occupations=%d caravans=%d seclusions=%d commissions_expired=%d moderations_expired=%d era_changed=%t", counts["auctions"], counts["merchants"], counts["merchant_bids"], counts["secret_realms"], counts["hunters_spawned"], counts["hunters_updated"], counts["wars"], counts["occupations"], counts["caravans"], counts["seclusions"], counts["commissions_expired"], counts["moderations_expired"], eraChanged)
	return Run{System: "advanced_world", DueSteps: 1, AppliedSteps: 1, Summary: summary}, true, nil
}

func (r *Runner) finalizeAuctions(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	res, err := conn.Execute(`SELECT * FROM auctions WHERE active=1 AND ends_at<=? ORDER BY ends_at`, []any{now})
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	for _, a := range rows {
		winner := i64(a["current_bidder_user_id"])
		if winner > 0 {
			if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{winner, fmt.Sprint(a["item_id"]), i64(a["quantity"])}); err != nil {
				return 0, err
			}
			if err = walletDeltaSim(conn, i64(a["seller_user_id"]), fmt.Sprint(a["currency_id"]), i64(a["current_bid"])); err != nil {
				return 0, err
			}
			if err = game.AuctionStruckProsperityTx(conn, r.World, a); err != nil {
				return 0, err
			}
			item := r.Catalog.Items[fmt.Sprint(a["item_id"])]
			level := strings.ToLower(strings.TrimSpace(item.AuctionInterest))
			if level == "special" || level == "legendary" {
				chance := item.DoorEventChance
				if chance <= 0 {
					if level == "legendary" {
						chance = 60
					} else {
						chance = 35
					}
				}
				chance = clamp(chance, 1, 100)
				if _, err = conn.Execute(`INSERT INTO auction_door_risks(user_id,auction_id,item_id,risk_level,chance_percent,created_at,consumed_at) VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(user_id,auction_id) DO UPDATE SET item_id=excluded.item_id,risk_level=excluded.risk_level,chance_percent=excluded.chance_percent,created_at=excluded.created_at,consumed_at=NULL`, []any{winner, i64(a["auction_id"]), fmt.Sprint(a["item_id"]), level, chance, now}); err != nil {
					return 0, err
				}
			}
		} else {
			// No player bidder: the merchant holding the high bid wins it
			// (v0.37.0); failing that, a merchant whose loop passes this
			// city may take the lot at its starting bid (v0.34.1); otherwise
			// it goes back to the seller.
			_, bought, err := game.MerchantWinsLot(conn, r.World, a, gm)
			if err != nil {
				return 0, err
			}
			if !bought {
				_, bought, err = game.MerchantBuysUnsoldLot(conn, r.World, a, gm)
				if err != nil {
					return 0, err
				}
			}
			if !bought {
				if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{i64(a["seller_user_id"]), fmt.Sprint(a["item_id"]), i64(a["quantity"])}); err != nil {
					return 0, err
				}
			}
		}
		if _, err = conn.Execute(`UPDATE auctions SET active=0 WHERE auction_id=?`, []any{i64(a["auction_id"])}); err != nil {
			return 0, err
		}
	}
	return int64(len(rows)), nil
}

func (r *Runner) spawnHunters(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	res, err := conn.Execute(`SELECT b.*,c.realm_index FROM bounties b JOIN characters c ON c.user_id=b.user_id WHERE b.status='active' AND NOT EXISTS(SELECT 1 FROM bounty_hunter_pursuits p WHERE p.bounty_id=b.bounty_id AND (p.status IN ('tracking','engaged') OR p.updated_game_minute>?))`, []any{gm - 7*minutesPerDay})
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	for _, b := range rows {
		power := max64(1, i64(b["realm_index"])*2+max64(1, i64(b["amount"])/100))
		title := bountyHunterTitles[int(stablePercent(b["bounty_id"], b["jurisdiction"])%int64(len(bountyHunterTitles)))]
		if _, err = conn.Execute(`INSERT INTO bounty_hunter_pursuits(bounty_id,user_id,hunter_name,hunter_power,status,pressure,escape_progress,capture_progress,next_action_game_minute,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(?,?,?,?,'tracking',10,0,0,?,?,?,?,?)`, []any{i64(b["bounty_id"]), i64(b["user_id"]), title, power, gm + minutesPerDay, gm, gm, now, now}); err != nil {
			return 0, err
		}
	}
	return int64(len(rows)), nil
}

func (r *Runner) advanceHunters(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	mods, err := r.eraModifiers(conn)
	if err != nil {
		return 0, err
	}
	m := mods["crime_pressure"]
	if m == 0 {
		m = 1
	}
	m = math.Max(.25, m)
	if _, err = conn.Execute(`UPDATE bounty_hunter_pursuits SET status='withdrawn',updated_game_minute=?,updated_at=? WHERE status IN ('tracking','engaged') AND bounty_id IN (SELECT bounty_id FROM bounties WHERE status!='active')`, []any{gm, now}); err != nil {
		return 0, err
	}
	res, err := conn.Execute(`SELECT * FROM bounty_hunter_pursuits WHERE status IN ('tracking','engaged') AND next_action_game_minute<=?`, []any{gm})
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	for _, p := range rows {
		elapsed := max64(1, (gm-i64(p["next_action_game_minute"]))/minutesPerDay+1)
		pressure := min64(100, i64(p["pressure"])+int64(math.Round(float64(elapsed*(8+i64(p["hunter_power"])))*m)))
		capture := i64(p["capture_progress"])
		status := fmt.Sprint(p["status"])
		if pressure >= 65 {
			status = "engaged"
			capture = min64(100, capture+int64(math.Round(float64(elapsed*max64(5, i64(p["hunter_power"])))*m)))
		}
		if capture >= 100 {
			status = "captured"
		}
		if _, err = conn.Execute(`UPDATE bounty_hunter_pursuits SET status=?,pressure=?,capture_progress=?,next_action_game_minute=?,updated_game_minute=?,updated_at=? WHERE pursuit_id=?`, []any{status, pressure, capture, gm + minutesPerDay, gm, now, i64(p["pursuit_id"])}); err != nil {
			return 0, err
		}
		if status == "captured" {
			if _, err = conn.Execute(`UPDATE bounties SET status='resolved',updated_at=? WHERE bounty_id=? AND status='active'`, []any{now, i64(p["bounty_id"])}); err != nil {
				return 0, err
			}
			if _, err = conn.Execute(`UPDATE crime_records SET status='captured',updated_at=? WHERE crime_id=(SELECT source_crime_id FROM bounties WHERE bounty_id=?) AND status='open'`, []any{now, i64(p["bounty_id"])}); err != nil {
				return 0, err
			}
		}
	}
	return int64(len(rows)), nil
}

func (r *Runner) advanceWars(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	mods, err := r.eraModifiers(conn)
	if err != nil {
		return 0, err
	}
	wm := mods["war_pressure"]
	if wm == 0 {
		wm = 1
	}
	wm = math.Max(.25, wm)
	res, err := conn.Execute(`SELECT w.*,o.siege_progress,o.attacker_morale,o.defender_morale,o.attacker_force,o.defender_force,o.last_tick_game_minute FROM territory_wars w LEFT JOIN territory_war_operations o ON o.war_id=w.war_id WHERE w.status='active' ORDER BY w.war_id`, nil)
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	changed := int64(0)
	for _, w := range rows {
		last := i64(w["last_tick_game_minute"])
		if last == 0 {
			last = i64(w["updated_game_minute"])
		}
		days := max64(0, (gm-last)/minutesPerDay)
		if days <= 0 {
			continue
		}
		if _, err = conn.Execute(`INSERT INTO territory_war_operations(war_id,siege_progress,attacker_morale,defender_morale,attacker_force,defender_force,last_tick_game_minute,winner_key,resolution,occupation_until_game_minute,updated_at) VALUES(?,0,100,100,0,0,?,'','',0,?) ON CONFLICT(war_id) DO NOTHING`, []any{i64(w["war_id"]), gm, now}); err != nil {
			return changed, err
		}
		opres, e := conn.Execute(`SELECT * FROM territory_war_operations WHERE war_id=?`, []any{i64(w["war_id"])})
		if e != nil {
			return changed, e
		}
		op := firstMap(opres)
		af := max64(1, i64(op["attacker_force"]))
		df := max64(1, i64(op["defender_force"]))
		side := "attacker"
		tactic := "siege"
		if af < df {
			side = "defender"
			tactic = "fortify"
		}
		power := max64(1, int64(math.Round(float64(min64(50, days+max64(af, df)/10))*wm)))
		siege := i64(op["siege_progress"])
		am := i64(op["attacker_morale"])
		dm := i64(op["defender_morale"])
		af = i64(op["attacker_force"])
		df = i64(op["defender_force"])
		roll := stablePercent(w["war_id"], 0, side, tactic, gm)
		impact := max64(2, power/4+roll/15)
		siegeDelta := int64(0)
		moraleDelta := -impact
		if side == "attacker" {
			af += power
			siegeDelta = impact + 5
			dm -= impact / 2
			siege = min64(100, siege+max64(0, siegeDelta))
		} else {
			df += power
			siegeDelta = -(impact + 4)
			dm += impact
			siege = max64(0, siege+siegeDelta)
		}
		am = clamp(am, 0, 120)
		dm = clamp(dm, 0, 120)
		winner := ""
		resolution := ""
		occ := int64(0)
		if siege >= 100 || dm <= 0 {
			winner = fmt.Sprint(w["attacker_key"])
			resolution = "attacker_occupation"
			occ = gm + 30*minutesPerDay
		} else if am <= 0 {
			winner = fmt.Sprint(w["defender_key"])
			resolution = "defender_holds"
		}
		if _, err = conn.Execute(`UPDATE territory_war_operations SET siege_progress=?,attacker_morale=?,defender_morale=?,attacker_force=?,defender_force=?,last_tick_game_minute=?,winner_key=?,resolution=?,occupation_until_game_minute=?,updated_at=? WHERE war_id=?`, []any{siege, am, dm, af, df, gm, winner, resolution, occ, now, i64(w["war_id"])}); err != nil {
			return changed, err
		}
		if _, err = conn.Execute(`INSERT INTO territory_war_actions(war_id,user_id,side,tactic,power,siege_delta,morale_delta,game_minute,created_at) VALUES(?,NULL,?,?,?,?,?,?,?)`, []any{i64(w["war_id"]), side, tactic, power, siegeDelta, moraleDelta, gm, now}); err != nil {
			return changed, err
		}
		if _, err = conn.Execute(`UPDATE territory_wars SET attacker_score=?,defender_score=?,updated_game_minute=?,updated_at=? WHERE war_id=?`, []any{af, df, gm, now, i64(w["war_id"])}); err != nil {
			return changed, err
		}
		if winner != "" {
			if _, err = conn.Execute(`UPDATE territory_wars SET status='resolved',updated_game_minute=?,updated_at=? WHERE war_id=?`, []any{gm, now, i64(w["war_id"])}); err != nil {
				return changed, err
			}
			if winner == fmt.Sprint(w["attacker_key"]) {
				_, err = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+35),updated_game_minute=?,updated_at=? WHERE territory_key=?`, []any{winner, gm, now, fmt.Sprint(w["territory_key"])})
			} else {
				_, err = conn.Execute(`UPDATE territory_state SET unrest=MAX(0,unrest-10),updated_game_minute=?,updated_at=? WHERE territory_key=?`, []any{gm, now, fmt.Sprint(w["territory_key"])})
			}
			if err != nil {
				return changed, err
			}
		}
		changed++
	}
	return changed, nil
}

func (r *Runner) advanceOccupations(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	res, err := conn.Execute(`SELECT w.war_id,w.territory_key,w.attacker_key FROM territory_wars w JOIN territory_war_operations o ON o.war_id=w.war_id WHERE w.status='resolved' AND o.resolution='attacker_occupation' AND o.occupation_until_game_minute>0 AND o.occupation_until_game_minute<=?`, []any{gm})
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	for _, w := range rows {
		if _, err = conn.Execute(`UPDATE territory_war_operations SET resolution='attacker_annexed',occupation_until_game_minute=0,last_tick_game_minute=?,updated_at=? WHERE war_id=?`, []any{gm, now, i64(w["war_id"])}); err != nil {
			return 0, err
		}
		if _, err = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MAX(0,unrest-20),updated_game_minute=?,updated_at=? WHERE territory_key=?`, []any{fmt.Sprint(w["attacker_key"]), gm, now, fmt.Sprint(w["territory_key"])}); err != nil {
			return 0, err
		}
	}
	return int64(len(rows)), nil
}

func boolIntSim(v bool) int {
	if v {
		return 1
	}
	return 0
}
func (r *Runner) advanceCaravans(conn *storage.Conn, gm int64) (int64, error) {
	now := nowFloat()
	mods, err := r.eraModifiers(conn)
	if err != nil {
		return 0, err
	}
	rm := mods["caravan_risk"]
	if rm == 0 {
		rm = 1
	}
	rm = math.Max(.25, rm)
	res, err := conn.Execute(`SELECT c.*,o.escort_strength,o.concealment,o.smuggling,o.tax_rate FROM caravans c LEFT JOIN caravan_operations o ON o.caravan_id=c.caravan_id WHERE c.status='traveling' AND c.arrive_game_minute<=? ORDER BY c.caravan_id`, []any{gm})
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	for _, c := range rows {
		cargo := map[string]any{}
		_ = json.Unmarshal([]byte(fmt.Sprint(c["cargo_json"])), &cargo)
		payout := max64(0, i64(cargo["_payout"]))
		currency := fmt.Sprint(cargo["_currency"])
		if currency == "" {
			currency = "low_spirit_stone"
		}
		escort := i64(c["escort_strength"])
		conceal := i64(c["concealment"])
		smuggling := i64(c["smuggling"]) != 0
		tax := max64(0, i64(c["tax_rate"]))
		legacy := tax == 0 && escort == 0 && conceal == 0 && !smuggling
		effective := int64(0)
		if !legacy {
			raw := i64(c["risk"])
			if smuggling {
				raw += 20
			}
			raw -= escort*2 + conceal
			effective = clamp(int64(math.Round(float64(raw)*rm)), 0, 95)
		}
		roll := stablePercent(c["caravan_id"], c["origin"], c["destination"], c["depart_game_minute"])
		intercepted := roll < effective
		seized := false
		loss := int64(0)
		if intercepted {
			if smuggling && roll < max64(5, effective/3) {
				seized = true
				loss = 100
			} else {
				loss = min64(75, 20+(effective-roll)/2)
			}
		}
		after := payout * (100 - loss) / 100
		toll := int64(0)
		if !smuggling && !seized {
			toll = after * tax / 100
		}
		final := max64(0, after-toll)
		outcome := "arrived"
		if intercepted {
			outcome = "intercepted"
		}
		if seized {
			outcome = "seized"
		}
		if fmt.Sprint(c["owner_type"]) == "player" && final > 0 {
			uid, e := strconv.ParseInt(fmt.Sprint(c["owner_key"]), 10, 64)
			if e == nil {
				if err = walletDeltaSim(conn, uid, currency, final); err != nil {
					return 0, err
				}
			}
		}
		if _, err = conn.Execute(`UPDATE caravans SET status=?,updated_at=? WHERE caravan_id=?`, []any{outcome, now, i64(c["caravan_id"])}); err != nil {
			return 0, err
		}
		lossJSON, _ := json.Marshal(map[string]any{"percent": loss})
		intercept := 0
		if intercepted {
			intercept = 1
		}
		seize := 0
		if seized {
			seize = 1
		}
		if _, err = conn.Execute(`INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,toll_paid,intercepted,seized,payout_final,losses_json,outcome,resolved_game_minute,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(caravan_id) DO UPDATE SET toll_paid=excluded.toll_paid,intercepted=excluded.intercepted,seized=excluded.seized,payout_final=excluded.payout_final,losses_json=excluded.losses_json,outcome=excluded.outcome,resolved_game_minute=excluded.resolved_game_minute,updated_at=excluded.updated_at`, []any{i64(c["caravan_id"]), escort, conceal, boolIntSim(smuggling), tax, toll, intercept, seize, final, string(lossJSON), outcome, gm, now}); err != nil {
			return 0, err
		}
		detail, _ := json.Marshal(map[string]any{"risk": effective, "roll": roll, "loss_percent": loss, "tax": toll, "payout": final})
		if _, err = conn.Execute(`INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{i64(c["caravan_id"]), outcome, string(detail), gm, now}); err != nil {
			return 0, err
		}
	}
	return int64(len(rows)), nil
}

func (r *Runner) advanceEra(conn *storage.Conn, gm int64) (bool, error) {
	now := nowFloat()
	res, err := conn.Execute(`SELECT * FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1`, nil)
	if err != nil {
		return false, err
	}
	cur := firstMap(res)
	if cur == nil {
		t := eraCycle[0]
		mods, _ := json.Marshal(t.Modifiers)
		_, err = conn.Execute(`INSERT INTO world_eras(name,description,started_game_minute,active,modifiers_json,created_at) VALUES(?,?,?,1,?,?)`, []any{t.Name, t.Description, gm, string(mods), now})
		return err == nil, err
	}
	idx := 0
	for i, t := range eraCycle {
		if t.Name == fmt.Sprint(cur["name"]) {
			idx = i
			break
		}
	}
	changed := false
	started := i64(cur["started_game_minute"])
	name := fmt.Sprint(cur["name"])
	for n := 0; n < 12; n++ {
		t := eraCycle[idx]
		duration := t.DurationDays * minutesPerDay
		if gm-started < duration {
			break
		}
		transition := started + duration
		next := (idx + 1) % len(eraCycle)
		nt := eraCycle[next]
		mods, _ := json.Marshal(nt.Modifiers)
		if _, err = conn.Execute(`UPDATE world_eras SET active=0,ended_game_minute=? WHERE active=1`, []any{transition}); err != nil {
			return changed, err
		}
		ins, e := conn.Execute(`INSERT INTO world_eras(name,description,started_game_minute,active,modifiers_json,created_at) VALUES(?,?,?,1,?,?)`, []any{nt.Name, nt.Description, transition, string(mods), now})
		if e != nil {
			return changed, e
		}
		detail, _ := json.Marshal(map[string]any{"previous": name, "automatic": true})
		if _, err = conn.Execute(`INSERT INTO world_era_events(era_id,event_type,title,detail_json,game_minute,created_at) VALUES(?,'transition',?,?,?,?)`, []any{ins.LastInsertID, nt.Name + " begins", string(detail), transition, now}); err != nil {
			return changed, err
		}
		idx = next
		started = transition
		name = nt.Name
		changed = true
	}
	return changed, nil
}

func soulMultSim(conn *storage.Conn, uid int64) (float64, error) {
	res, err := conn.Execute(`SELECT talent_echo,special_trait FROM soul_legacy WHERE user_id=?`, []any{uid})
	if err != nil {
		return 1, err
	}
	row := firstMap(res)
	if row == nil {
		return 1, nil
	}
	m := 1 + math.Min(.10, float64(clamp(i64(row["talent_echo"]), 0, 100))/1000)
	switch fmt.Sprint(row["special_trait"]) {
	case "Born Knowing":
		m += .03
	case "Old Soul":
		m += .02
	case "Heaven-Defying Fate":
		m += .05
	}
	return math.Max(1, math.Min(1.25, m)), nil
}
func (r *Runner) phaseCapSim(realm, phase int64, body bool) int64 {
	realms := r.Catalog.Realms
	if body {
		realms = r.Catalog.BodyRealms
	}
	if realm < 0 || int(realm) >= len(realms) || phase < 1 {
		return math.MaxInt64
	}
	costs := realms[realm].PhaseCosts
	if int(phase) > len(costs) {
		return math.MaxInt64
	}
	return costs[phase-1]
}
func (r *Runner) advanceSeclusions(conn *storage.Conn, gm int64) (int64, error) {
	res, err := conn.Execute(`SELECT s.*,c.life_status,c.realm_index,c.phase,c.body_realm_index,c.body_phase,c.cultivation,c.body_cultivation,c.attributes_json FROM seclusion_sessions s JOIN characters c ON c.user_id=s.user_id WHERE s.status='active'`, nil)
	if err != nil {
		return 0, err
	}
	rows := maps(res)
	changed := int64(0)
	now := nowFloat()
	for _, s := range rows {
		uid := i64(s["user_id"])
		if fmt.Sprint(s["life_status"]) != "alive" {
			if _, err = conn.Execute(`UPDATE seclusion_sessions SET status='completed',ended_reason='incarnation unavailable',updated_at=? WHERE user_id=?`, []any{now, uid}); err != nil {
				return changed, err
			}
			changed++
			continue
		}
		last := i64(s["last_settled_game_minute"])
		end := i64(s["ends_game_minute"])
		target := min64(gm, end)
		days := max64(0, (target-last)/minutesPerDay)
		settled := last + days*minutesPerDay
		mode := fmt.Sprint(s["mode"])
		attrs := map[string]any{}
		_ = json.Unmarshal([]byte(fmt.Sprint(s["attributes_json"])), &attrs)
		realm := i64(s["realm_index"])
		phase := i64(s["phase"])
		base := 8 + i64(attrs["will"]) + i64(attrs["insight"])/2 + realm/2
		field := "cultivation"
		current := i64(s["cultivation"])
		if mode == "body" {
			realm = i64(s["body_realm_index"])
			phase = i64(s["body_phase"])
			base = 7 + i64(attrs["body"]) + i64(attrs["will"])/3 + realm/2
			field = "body_cultivation"
			current = i64(s["body_cultivation"])
		}
		env := math.Max(.5, math.Min(1.75, float64Value(s["environment_mult"], 1)))
		mult, e := soulMultSim(conn, uid)
		if e != nil {
			return changed, e
		}
		daily := max64(1, int64(math.Round(float64(base)*.60*env*mult)))
		awarded := min64(daily*days, max64(0, r.phaseCapSim(realm, phase, mode == "body")-current))
		if awarded > 0 {
			q := fmt.Sprintf("UPDATE characters SET %s=%s+?,updated_at=? WHERE user_id=?", field, field)
			if _, err = conn.Execute(q, []any{awarded, now, uid}); err != nil {
				return changed, err
			}
		}
		// See the note in internal/game/family_dao_actions.go: whole-day
		// accounting can never reach an end that is not a whole number of days,
		// so completion keys off the clock and the books are closed at the end.
		completed := gm >= end
		if completed {
			settled = max64(settled, end)
		}
		status := "active"
		reason := ""
		if completed {
			status = "completed"
			reason = "planned seclusion completed"
		}
		if days > 0 || completed {
			if _, err = conn.Execute(`UPDATE seclusion_sessions SET last_settled_game_minute=?,accumulated_gain=accumulated_gain+?,status=?,ended_reason=?,updated_at=? WHERE user_id=?`, []any{settled, awarded, status, reason, now, uid}); err != nil {
				return changed, err
			}
			changed++
		}
	}
	return changed, nil
}

// expireCommissions fails every commission whose deadline has passed. The
// outcome table itself lives in the game package (commission_actions.go) so
// that a deadline expiring and a player abandoning cost exactly the same
// standing - there is only one implementation of that rule.
func (r *Runner) expireCommissions(conn *storage.Conn, gm int64) (int64, error) {
	probe, err := conn.Execute(`SELECT 1 AS ok FROM pragma_table_info('character_quests') WHERE name='commission' LIMIT 1`, nil)
	if err != nil {
		return 0, err
	}
	if firstMap(probe) == nil {
		return 0, nil
	}
	expired, err := game.ExpireDueCommissions(conn, gm)
	if err != nil {
		return 0, err
	}
	return int64(len(expired)), nil
}

// expireModerations clears lapsed mutes and freezes. The rule - what a
// duration means and what lifting it restores - lives in the game package
// beside admin.player.set_moderation, so the tick and the GM action can never
// disagree about it.
func (r *Runner) expireModerations(conn *storage.Conn) (int64, error) {
	cleared, err := game.ExpireDueModerations(conn, nowFloat())
	if err != nil {
		return 0, err
	}
	return int64(len(cleared)), nil
}
