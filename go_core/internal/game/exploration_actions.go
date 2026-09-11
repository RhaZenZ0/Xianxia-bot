package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type explorationPayload struct {
	GameMinute              int64  `json:"game_minute"`
	CooldownSeconds         int64  `json:"cooldown_seconds"`
	UnexpectedEventsEnabled *bool  `json:"unexpected_events_enabled,omitempty"`
	UnexpectedEventChance   int    `json:"unexpected_event_chance_percent"`
	EventKey                string `json:"event_key"`
}

type travelPayload struct {
	Destination string `json:"destination"`
	Mode        string `json:"mode"`
	GameMinute  int64  `json:"game_minute"`
}

type huntPayload struct {
	GameMinute      int64 `json:"game_minute"`
	CooldownSeconds int64 `json:"cooldown_seconds"`
}

type canonicalReward struct {
	Cultivation  int64
	SpiritStones int64
	InsightXP    int64
	Items        map[string]int64
}

func mapInt64(v any) int64 { return storage.ParseInt(v) }

func mapItems(v any) map[string]int64 {
	out := map[string]int64{}
	switch x := v.(type) {
	case map[string]any:
		for k, raw := range x {
			out[k] = storage.ParseInt(raw)
		}
	case map[string]int64:
		for k, raw := range x {
			out[k] = raw
		}
	}
	return out
}

func rewardFromMap(v map[string]any) canonicalReward {
	if v == nil {
		return canonicalReward{Items: map[string]int64{}}
	}
	return canonicalReward{
		Cultivation:  storage.ParseInt(v["cultivation"]),
		SpiritStones: storage.ParseInt(v["spirit_stones"]),
		InsightXP:    storage.ParseInt(v["insight_xp"]),
		Items:        mapItems(v["items"]),
	}
}

func applyCanonicalRewardTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, reward canonicalReward, eventType string, now float64) (int64, error) {
	awarded := reward.Cultivation
	if awarded < 0 {
		awarded = 0
	}
	cap, err := phaseCost(catalog.Realms, c.RealmIndex, c.Phase)
	if err != nil {
		return 0, err
	}
	currentCultivation := c.Cultivation
	currentRes, err := conn.Execute(`SELECT cultivation FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	currentRow := firstRowMap(currentRes)
	if currentRow == nil {
		return 0, fmt.Errorf("character not found")
	}
	currentCultivation = mapInt64(currentRow["cultivation"])
	room := cap - currentCultivation
	if room < 0 {
		room = 0
	}
	if awarded > room {
		awarded = room
	}
	if _, err = conn.Execute(`UPDATE characters SET cultivation=cultivation+?,spirit_stones=spirit_stones+?,insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{awarded, reward.SpiritStones, reward.InsightXP, now, userID}); err != nil {
		return 0, err
	}
	if reward.SpiritStones != 0 {
		if _, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance`, []any{userID, "low_spirit_stone", reward.SpiritStones}); err != nil {
			return 0, err
		}
	}
	for item, qty := range reward.Items {
		if qty <= 0 {
			continue
		}
		if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{userID, item, qty}); err != nil {
			return 0, err
		}
	}
	payload, _ := json.Marshal(map[string]any{"cultivation": awarded, "spirit_stones": reward.SpiritStones, "insight_xp": reward.InsightXP, "items": reward.Items})
	if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, eventType, string(payload), now}); err != nil {
		return 0, err
	}
	return awarded, nil
}

func stringInList(xs []string, needle string) bool {
	for _, x := range xs {
		if x == needle {
			return true
		}
	}
	return false
}

func currentWorld(c mechanicsCharacter, catalog worlddata.Catalog) string {
	if loc, ok := catalog.Locations[c.Location]; ok && strings.TrimSpace(loc.World) != "" {
		return loc.World
	}
	idx := c.RealmIndex
	if idx < 0 {
		idx = 0
	}
	if int(idx) >= len(catalog.Realms) {
		idx = int64(len(catalog.Realms) - 1)
	}
	if idx >= 0 && int(idx) < len(catalog.Realms) {
		return catalog.Realms[idx].World
	}
	return "Mortal World"
}

func worldMinRealm(catalog worlddata.Catalog, world string) int64 {
	var best int64 = 1 << 62
	for _, loc := range catalog.Locations {
		if loc.World == world && loc.RealmHub {
			if loc.MinRealmIndex < best {
				best = loc.MinRealmIndex
			}
		}
	}
	if best != 1<<62 {
		return best
	}
	for _, loc := range catalog.Locations {
		if loc.World == world && loc.MinRealmIndex < best {
			best = loc.MinRealmIndex
		}
	}
	if best == 1<<62 {
		return 0
	}
	return best
}

type roadTravelProfile struct {
	TravelMinutes   int64
	DangerScore     int64
	EncounterChance int64
}

var roadEncounterIntn = gamerng.Intn

func roadTerrainTravelMinutes(terrain string) int64 {
	terrain = strings.ToLower(strings.TrimSpace(terrain))
	switch {
	case strings.Contains(terrain, "imperial"), strings.Contains(terrain, "trade road"):
		return 45
	case strings.Contains(terrain, "river"), strings.Contains(terrain, "plain"):
		return 60
	case strings.Contains(terrain, "forest"), strings.Contains(terrain, "valley"):
		return 80
	case strings.Contains(terrain, "fortified"), strings.Contains(terrain, "dry"):
		return 75
	case strings.Contains(terrain, "mountain"), strings.Contains(terrain, "highland"):
		return 105
	case strings.Contains(terrain, "peak"), strings.Contains(terrain, "wind"):
		return 95
	case strings.Contains(terrain, "volcan"), strings.Contains(terrain, "forge"):
		return 110
	case strings.Contains(terrain, "wetland"), strings.Contains(terrain, "marsh"), strings.Contains(terrain, "mist"):
		return 115
	default:
		return 75
	}
}

func roadTerrainDanger(terrain string) int64 {
	terrain = strings.ToLower(strings.TrimSpace(terrain))
	switch {
	case strings.Contains(terrain, "imperial"):
		return 6
	case strings.Contains(terrain, "trade road"):
		return 9
	case strings.Contains(terrain, "river"), strings.Contains(terrain, "plain"):
		return 12
	case strings.Contains(terrain, "fortified"), strings.Contains(terrain, "dry"):
		return 15
	case strings.Contains(terrain, "forest"), strings.Contains(terrain, "valley"):
		return 18
	case strings.Contains(terrain, "peak"), strings.Contains(terrain, "wind"):
		return 22
	case strings.Contains(terrain, "mountain"), strings.Contains(terrain, "highland"):
		return 24
	case strings.Contains(terrain, "wetland"), strings.Contains(terrain, "marsh"), strings.Contains(terrain, "mist"):
		return 27
	case strings.Contains(terrain, "volcan"), strings.Contains(terrain, "forge"):
		return 30
	default:
		return 16
	}
}

func roadWorldTravelBonus(world string) int64 {
	switch world {
	case "Spiritual World":
		return 15
	case "Immortal World":
		return 30
	case "Celestial World":
		return 45
	default:
		return 0
	}
}

func roadWorldDangerBonus(world string) int64 {
	switch world {
	case "Spiritual World":
		return 3
	case "Immortal World":
		return 6
	case "Celestial World":
		return 9
	default:
		return 0
	}
}

func canonicalRoadTravelProfile(origin, destination worlddata.LocationDefinition, realmIndex int64) roadTravelProfile {
	travel := (roadTerrainTravelMinutes(origin.Terrain)+roadTerrainTravelMinutes(destination.Terrain))/2 +
		roadWorldTravelBonus(origin.World)
	speedReduction := realmIndex * 2
	if speedReduction > travel/3 {
		speedReduction = travel / 3
	}
	travel -= speedReduction
	if travel < 30 {
		travel = 30
	}

	danger := (roadTerrainDanger(origin.Terrain)+roadTerrainDanger(destination.Terrain))/2 +
		roadWorldDangerBonus(origin.World)
	danger -= realmIndex / 2
	if origin.SafeZone && destination.SafeZone {
		danger -= 3
	}
	if danger < 5 {
		danger = 5
	}
	if danger > 45 {
		danger = 45
	}
	return roadTravelProfile{
		TravelMinutes:   travel,
		DangerScore:     danger,
		EncounterChance: danger,
	}
}

type roadRouteLeg struct {
	From    string
	To      string
	Profile roadTravelProfile
	Cost    int64
}

type roadRoutePlan struct {
	Nodes         []string
	Legs          []roadRouteLeg
	TravelMinutes int64
	Cost          int64
	MaxDanger     int64
}

func roadLegCost(profile roadTravelProfile) int64 {
	cost := int64(1) + profile.TravelMinutes/120 + profile.DangerScore/15
	if cost < 1 {
		return 1
	}
	return cost
}

func canonicalRoadRoute(catalog worlddata.Catalog, origin, destination string, realmIndex int64) (roadRoutePlan, bool) {
	if origin == destination {
		return roadRoutePlan{}, false
	}
	start, ok := catalog.Locations[origin]
	if !ok {
		return roadRoutePlan{}, false
	}
	target, ok := catalog.Locations[destination]
	if !ok || start.World != target.World {
		return roadRoutePlan{}, false
	}

	const infinity int64 = 1<<62 - 1
	dist := map[string]int64{}
	previous := map[string]string{}
	unvisited := map[string]bool{}
	for name, location := range catalog.Locations {
		if location.Private || location.World != start.World || location.MinRealmIndex > realmIndex {
			continue
		}
		dist[name] = infinity
		unvisited[name] = true
	}
	if !unvisited[origin] || !unvisited[destination] {
		return roadRoutePlan{}, false
	}
	dist[origin] = 0

	for len(unvisited) > 0 {
		current := ""
		best := infinity
		for name := range unvisited {
			if dist[name] < best || (dist[name] == best && (current == "" || name < current)) {
				current = name
				best = dist[name]
			}
		}
		if current == "" || best == infinity {
			break
		}
		delete(unvisited, current)
		if current == destination {
			break
		}
		from := catalog.Locations[current]
		for _, neighbor := range canonicalRoadNeighbors(catalog, current, realmIndex) {
			if !unvisited[neighbor] {
				continue
			}
			profile := canonicalRoadTravelProfile(from, catalog.Locations[neighbor], realmIndex)
			alt := best + profile.TravelMinutes
			if alt < dist[neighbor] {
				dist[neighbor] = alt
				previous[neighbor] = current
			}
		}
	}
	if dist[destination] == infinity {
		return roadRoutePlan{}, false
	}

	nodes := []string{destination}
	for nodes[len(nodes)-1] != origin {
		prev, exists := previous[nodes[len(nodes)-1]]
		if !exists {
			return roadRoutePlan{}, false
		}
		nodes = append(nodes, prev)
	}
	for left, right := 0, len(nodes)-1; left < right; left, right = left+1, right-1 {
		nodes[left], nodes[right] = nodes[right], nodes[left]
	}

	plan := roadRoutePlan{Nodes: nodes}
	for i := 0; i+1 < len(nodes); i++ {
		from, to := catalog.Locations[nodes[i]], catalog.Locations[nodes[i+1]]
		profile := canonicalRoadTravelProfile(from, to, realmIndex)
		cost := roadLegCost(profile)
		plan.Legs = append(plan.Legs, roadRouteLeg{From: nodes[i], To: nodes[i+1], Profile: profile, Cost: cost})
		plan.TravelMinutes += profile.TravelMinutes
		plan.Cost += cost
		if profile.DangerScore > plan.MaxDanger {
			plan.MaxDanger = profile.DangerScore
		}
	}
	return plan, len(plan.Legs) > 0
}

func chargeRoadTravelTx(conn *storage.Conn, userID, cost int64, now float64) error {
	if cost <= 0 {
		return nil
	}
	res, err := conn.Execute(`SELECT spirit_stones FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	row := firstRowMap(res)
	if row == nil {
		return errors.New("character not found")
	}
	balance := storage.ParseInt(row["spirit_stones"])
	if balance < cost {
		return fmt.Errorf("road travel requires %d spirit stones; only %d available", cost, balance)
	}
	_, err = conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones-?,updated_at=? WHERE user_id=?`, []any{cost, now, userID})
	return err
}

type roadTransitState struct {
	Origin              string   `json:"origin"`
	Destination         string   `json:"destination"`
	Route               []string `json:"route,omitempty"`
	TravelCost          int64    `json:"travel_cost_spirit_stones,omitempty"`
	DepartureGameMinute int64    `json:"departure_game_minute"`
	ArrivalGameMinute   int64    `json:"arrival_game_minute"`
}

func roadTransitStateKey(userID int64) string {
	return fmt.Sprintf("road_transit:%d", userID)
}

// travelStatusQuery reports whether the character is currently mid-transit on
// a road journey, and if so, where to and when they arrive - both as an
// absolute game-clock minute and, when the world clock is actually advancing
// (scale > 0), as a real Unix timestamp the caller can hand straight to a
// Discord <t:...> timestamp for a live, auto-updating countdown. This never
// mutates anything (it is registered as a read-only authoritativeQuery, not a
// mutation) beyond opportunistically clearing an already-arrived transit
// record, exactly like ensureRoadTransitReadyTx does inline for every other
// action - so a stale "still traveling" status is never reported once the
// arrival minute has passed.
func travelStatusQuery(conn *storage.Conn, userID int64) (map[string]any, error) {
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return map[string]any{"traveling": false}, nil
	}
	var state roadTransitState
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &state); err != nil {
		return nil, fmt.Errorf("invalid road transit state: %w", err)
	}
	if gameMinute >= state.ArrivalGameMinute {
		if _, err := conn.Execute(`DELETE FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)}); err != nil {
			return nil, err
		}
		return map[string]any{"traveling": false}, nil
	}
	clock, err := readCanonicalWorldClock(conn)
	if err != nil {
		return nil, err
	}
	result := map[string]any{
		"traveling":                 true,
		"origin":                    state.Origin,
		"destination":               state.Destination,
		"route":                     state.Route,
		"travel_cost_spirit_stones": state.TravelCost,
		"departure_game_minute":     state.DepartureGameMinute,
		"arrival_game_minute":       state.ArrivalGameMinute,
		"current_game_minute":       gameMinute,
		"remaining_game_minutes":    state.ArrivalGameMinute - gameMinute,
	}
	if arrivalTS, ok := realTimestampForGameMinute(clock, state.ArrivalGameMinute); ok {
		result["arrival_unix_ts"] = arrivalTS
	}
	if departureTS, ok := realTimestampForGameMinute(clock, state.DepartureGameMinute); ok {
		result["departure_unix_ts"] = departureTS
	}
	return result, nil
}

func ensureRoadTransitReadyTx(conn *storage.Conn, userID, gameMinute int64) error {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)})
	if err != nil {
		return err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil
	}
	var state roadTransitState
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &state); err != nil {
		return fmt.Errorf("invalid road transit state: %w", err)
	}
	if gameMinute >= state.ArrivalGameMinute {
		_, err := conn.Execute(`DELETE FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)})
		return err
	}
	remaining := state.ArrivalGameMinute - gameMinute
	return fmt.Errorf(
		"road journey to %s is still in progress: arrives at game minute %d (%d game-minutes remaining)",
		state.Destination,
		state.ArrivalGameMinute,
		remaining,
	)
}

func setRoadTransitTx(
	conn *storage.Conn,
	userID int64,
	origin, destination string,
	route []string,
	travelCost, departureGameMinute, arrivalGameMinute int64,
	now float64,
) error {
	if arrivalGameMinute <= departureGameMinute {
		return nil
	}
	state := roadTransitState{
		Origin:              origin,
		Destination:         destination,
		Route:               append([]string(nil), route...),
		TravelCost:          travelCost,
		DepartureGameMinute: departureGameMinute,
		ArrivalGameMinute:   arrivalGameMinute,
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return err
	}
	_, err = conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`,
		[]any{roadTransitStateKey(userID), string(encoded), now},
	)
	return err
}

func applyRoadEncounterDamageTx(conn *storage.Conn, userID, requested int64, now float64) (int64, int64, error) {
	if requested <= 0 {
		return 0, 0, nil
	}
	res, err := conn.Execute(`SELECT vitality FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, 0, errors.New("character not found")
	}
	vitality := storage.ParseInt(row["vitality"])
	damage := requested
	if vitality <= 1 {
		damage = 0
	} else if damage >= vitality {
		damage = vitality - 1
	}
	remaining := vitality - damage
	if damage > 0 {
		if _, err := conn.Execute(`UPDATE characters SET vitality=?,updated_at=? WHERE user_id=?`, []any{remaining, now, userID}); err != nil {
			return 0, 0, err
		}
	}
	return damage, remaining, nil
}

func resolveRoadEncounterTx(conn *storage.Conn, userID int64, profile roadTravelProfile, gameMinute int64, now float64) (map[string]any, int64, error) {
	trigger, err := roadEncounterIntn(100)
	if err != nil {
		return nil, 0, err
	}
	if int64(trigger) >= profile.EncounterChance {
		return nil, 0, nil
	}
	kindRoll, err := roadEncounterIntn(4)
	if err != nil {
		return nil, 0, err
	}
	var kind, detail string
	var delay, requestedDamage int64
	switch kindRoll {
	case 0:
		kind = "blocked_road"
		detail = "A damaged bridge and fallen ward-stones force a careful detour."
		delay = 20 + profile.DangerScore/2
	case 1:
		kind = "qi_weather"
		detail = "A violent qi squall crosses the road and batters exposed travelers."
		delay = 15 + profile.DangerScore/3
		requestedDamage = 1 + profile.DangerScore/12
	case 2:
		kind = "spirit_beast"
		detail = "A territorial spirit beast rushes the roadside before retreating into the wilds."
		delay = 25 + profile.DangerScore/2
		requestedDamage = 1 + profile.DangerScore/10
	default:
		kind = "road_ambush"
		detail = "Road raiders probe the route's defenses before being driven off."
		delay = 20 + profile.DangerScore/2
		requestedDamage = 1 + profile.DangerScore/9
	}
	damage, vitality, err := applyRoadEncounterDamageTx(conn, userID, requestedDamage, now)
	if err != nil {
		return nil, 0, err
	}
	encounter := map[string]any{
		"kind": kind, "detail": detail, "game_minute": gameMinute, "delay_minutes": delay,
		"vitality_damage": damage, "vitality_after": vitality,
	}
	encoded, _ := json.Marshal(encounter)
	if _, err := conn.Execute(
		`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, "road_encounter", string(encoded), now},
	); err != nil {
		return nil, 0, err
	}
	return encounter, delay, nil
}

func canonicalRoadNeighbors(catalog worlddata.Catalog, current string, realmIndex int64) []string {
	location, ok := catalog.Locations[current]
	if !ok {
		return nil
	}
	neighbors := make([]string, 0, len(location.Roads))
	seen := map[string]bool{}
	for _, raw := range location.Roads {
		name := strings.TrimSpace(raw)
		if name == "" || seen[name] {
			continue
		}
		destination, exists := catalog.Locations[name]
		if !exists || destination.Private || destination.World != location.World || destination.MinRealmIndex > realmIndex {
			continue
		}
		seen[name] = true
		neighbors = append(neighbors, name)
	}
	sort.Strings(neighbors)
	return neighbors
}

func knownLocationsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter) (map[string]bool, error) {
	known := map[string]bool{}
	rows, err := conn.Execute(`SELECT location FROM character_location_discoveries WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	for _, row := range rows.Rows {
		if len(row) > 0 {
			known[fmt.Sprint(row[0])] = true
		}
	}
	if c.Location != "" && !strings.HasPrefix(c.Location, "abode:") && !strings.HasPrefix(c.Location, "personal_world:") {
		known[c.Location] = true
		for _, neighbor := range canonicalRoadNeighbors(catalog, c.Location, c.RealmIndex) {
			known[neighbor] = true
		}
		// Inside a shop or an auction hall (v0.35.0) the street outside the
		// door is known too, and the roads that leave it.
		if cur, ok := catalog.Locations[c.Location]; ok && (cur.Shop != "" || cur.AuctionHouse != "") && cur.OutsideLocation != "" {
			known[cur.OutsideLocation] = true
			for _, neighbor := range canonicalRoadNeighbors(catalog, cur.OutsideLocation, c.RealmIndex) {
				known[neighbor] = true
			}
		}
	}
	for name, loc := range catalog.Locations {
		if loc.RealmHub && c.RealmIndex >= worldMinRealm(catalog, loc.World) {
			known[name] = true
		}
	}
	return known, nil
}

var locationDiscoveryIntn = gamerng.Intn
var unexpectedEventIntn = gamerng.Intn

// roadFrontierTx returns every location one road-hop away from anything the
// character already knows (their current spot, prior discoveries, and any
// realm hub they qualify for) - the "edge of the charted map". A location's
// own road neighbors are already auto-known the moment the character stands
// there (see knownLocationsTx), so this frontier is naturally never the
// city's own immediate neighbors while occupying it; it is exactly the
// next ring out, reachable but not yet visited.
func roadFrontierTx(catalog worlddata.Catalog, known map[string]bool, realmIndex int64) map[string]bool {
	frontier := map[string]bool{}
	for name := range known {
		for _, neighbor := range canonicalRoadNeighbors(catalog, name, realmIndex) {
			frontier[neighbor] = true
		}
	}
	return frontier
}

func discoverNextLocationTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, gameMinute int64, now float64) (string, error) {
	known, err := knownLocationsTx(conn, catalog, userID, c)
	if err != nil {
		return "", err
	}
	world := currentWorld(c, catalog)
	// Candidates are restricted to the road frontier of what the character
	// already knows, not "any unknown location anywhere in the world" - a
	// mortal-realm character exploring around their home village should turn
	// up a road to a neighboring city, not a random capital on the far side
	// of the map. Every world's road graph is fully connected from that
	// world's realm-hub city (which every qualifying character always knows,
	// per knownLocationsTx), so this never stalls exploration - it just
	// makes discovery follow the road network outward ring by ring instead
	// of jumping anywhere at once.
	frontier := roadFrontierTx(catalog, known, c.RealmIndex)
	candidates := []string{}
	for name := range frontier {
		loc, ok := catalog.Locations[name]
		if !ok || known[name] || loc.World != world || loc.MinRealmIndex > c.RealmIndex || loc.Private || strings.HasPrefix(name, "abode:") || strings.HasPrefix(name, "personal_world:") {
			continue
		}
		candidates = append(candidates, name)
	}
	if len(candidates) == 0 {
		return "", nil
	}
	roll, err := locationDiscoveryIntn(100)
	if err != nil {
		return "", err
	}
	if roll >= 45 {
		return "", nil
	}
	sort.Strings(candidates)
	idx, err := locationDiscoveryIntn(len(candidates))
	if err != nil {
		return "", err
	}
	location := candidates[idx]
	r, err := conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`, []any{userID, location, "exploration", gameMinute, now})
	if err != nil {
		return "", err
	}
	if r.RowsAffected == 0 {
		return "", nil
	}
	return location, nil
}

func randomExploreReward() (canonicalReward, error) {
	a, err := gamerng.Intn(9)
	if err != nil {
		return canonicalReward{}, err
	}
	b, err := gamerng.Intn(5)
	if err != nil {
		return canonicalReward{}, err
	}
	table := []map[string]int64{{"spirit_herb": 1}, {"spirit_herb": 2}, {"spirit_iron": 1}, {"beast_core": 1}, {}}
	i, err := gamerng.Intn(len(table))
	if err != nil {
		return canonicalReward{}, err
	}
	return canonicalReward{Cultivation: int64(a + 5), SpiritStones: int64(b + 2), Items: table[i]}, nil
}

func eligibleUnexpectedEvents(catalog worlddata.Catalog, c mechanicsCharacter) []worlddata.UnexpectedEvent {
	world := currentWorld(c, catalog)
	out := []worlddata.UnexpectedEvent{}
	for _, e := range catalog.UnexpectedEvents {
		if len(e.Locations) > 0 && !stringInList(e.Locations, c.Location) {
			continue
		}
		if len(e.Worlds) > 0 && !stringInList(e.Worlds, world) {
			continue
		}
		if c.RealmIndex < e.MinRealmIndex {
			continue
		}
		if e.MaxRealmIndex != nil && c.RealmIndex > *e.MaxRealmIndex {
			continue
		}
		if e.Kind == "secret_realm" {
			realm, ok := catalog.SecretRealms[e.SecretRealmID]
			if !ok || realm.Location != c.Location || c.RealmIndex < realm.MinRealmIndex {
				continue
			}
		}
		out = append(out, e)
	}
	return out
}

func unexpectedEventsEnabledTx(conn *storage.Conn, override *bool) (bool, error) {
	if override != nil {
		return *override, nil
	}
	r, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='automation_settings'`, nil)
	if err != nil {
		return false, err
	}
	if len(r.Rows) == 0 {
		return true, nil
	}
	var settings map[string]any
	if json.Unmarshal([]byte(fmt.Sprint(r.Rows[0][0])), &settings) != nil {
		return true, nil
	}
	v, ok := settings["unexpected_events"]
	if !ok {
		return true, nil
	}
	if b, ok := v.(bool); ok {
		return b, nil
	}
	return true, nil
}

func rollUnexpectedEvent(catalog worlddata.Catalog, c mechanicsCharacter, enabled bool, chance int) (*worlddata.UnexpectedEvent, error) {
	if !enabled {
		return nil, nil
	}
	events := eligibleUnexpectedEvents(catalog, c)
	if len(events) == 0 {
		return nil, nil
	}
	if chance < 0 {
		chance = 0
	}
	if chance > 100 {
		chance = 100
	}
	n, err := unexpectedEventIntn(100)
	if err != nil {
		return nil, err
	}
	if n >= chance {
		return nil, nil
	}
	total := 0
	for _, e := range events {
		if e.Weight > 0 {
			total += e.Weight
		}
	}
	if total <= 0 {
		return nil, nil
	}
	pick, err := unexpectedEventIntn(total)
	if err != nil {
		return nil, err
	}
	for i := range events {
		w := events[i].Weight
		if w < 0 {
			w = 0
		}
		if pick < w {
			e := events[i]
			return &e, nil
		}
		pick -= w
	}
	return nil, nil
}

func adjustFateTx(conn *storage.Conn, userID, delta, gameMinute int64, reason string, now float64) (int64, int64, error) {
	r, err := conn.Execute(`SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, 0, err
	}
	cur, earned, spent := int64(0), int64(0), int64(0)
	if len(r.Rows) > 0 {
		cur = storage.ParseInt(r.Rows[0][0])
		earned = storage.ParseInt(r.Rows[0][1])
		spent = storage.ParseInt(r.Rows[0][2])
	}
	target := cur + delta
	if target < 0 {
		target = 0
	}
	if target > 9 {
		target = 9
	}
	applied := target - cur
	if applied > 0 {
		earned += applied
	} else if applied < 0 {
		spent += -applied
	}
	if _, err = conn.Execute(`INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET points=excluded.points,lifetime_earned=excluded.lifetime_earned,lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at`, []any{userID, target, earned, spent, now}); err != nil {
		return 0, 0, err
	}
	if applied != 0 {
		if _, err = conn.Execute(`INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)`, []any{userID, applied, target, reason, gameMinute, now}); err != nil {
			return 0, 0, err
		}
	}
	return target, applied, nil
}

func applyEventParticipationTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, eventID string, reward map[string]any, effect map[string]any, karmaDelta, fateDelta, gameMinute int64, now float64, eventType string) (map[string]any, error) {
	out := map[string]any{}
	rw := rewardFromMap(reward)
	if rw.Cultivation != 0 || rw.SpiritStones != 0 || rw.InsightXP != 0 || len(rw.Items) > 0 {
		awarded, err := applyCanonicalRewardTx(conn, catalog, userID, c, rw, eventType, now)
		if err != nil {
			return nil, err
		}
		out["cultivation_awarded"] = awarded
		out["spirit_stones"] = rw.SpiritStones
		out["insight_xp"] = rw.InsightXP
		out["items"] = rw.Items
		c.Cultivation += awarded
	}
	if len(effect) > 0 {
		key := strings.TrimSpace(fmt.Sprint(effect["effect_key"]))
		if key == "" {
			key = "event_" + eventID
		}
		name := strings.TrimSpace(fmt.Sprint(effect["name"]))
		if name == "" {
			name = strings.Title(strings.ReplaceAll(key, "_", " "))
		}
		duration := storage.ParseInt(effect["duration_game_minutes"])
		if duration <= 0 {
			duration = 120
		}
		normalized := map[string]any{}
		for k, v := range effect {
			normalized[k] = v
		}
		normalized["special"] = true
		encoded, _ := json.Marshal(normalized)
		if _, err := conn.Execute(`INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET name=excluded.name,effect_json=excluded.effect_json,stacks=excluded.stacks,starts_game_minute=excluded.starts_game_minute,ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`, []any{userID, key, name, "random_event", eventID, string(encoded), 1, gameMinute, gameMinute + duration, now}); err != nil {
			return nil, err
		}
		out["effect"] = map[string]any{"effect_key": key, "name": name, "duration_game_minutes": duration}
	}
	if karmaDelta != 0 {
		if _, err := conn.Execute(`UPDATE characters SET karma_score=MAX(-1000,MIN(1000,karma_score+?)),updated_at=? WHERE user_id=?`, []any{karmaDelta, now, userID}); err != nil {
			return nil, err
		}
		r, err := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
		if err != nil {
			return nil, err
		}
		if len(r.Rows) > 0 {
			out["karma_score"] = storage.ParseInt(r.Rows[0][0])
			out["karma_delta"] = karmaDelta
		}
	}
	if fateDelta != 0 {
		fate, applied, err := adjustFateTx(conn, userID, fateDelta, gameMinute, "event:"+eventID, now)
		if err != nil {
			return nil, err
		}
		out["fate"] = fate
		out["fate_delta"] = applied
	}
	return out, nil
}

func claimActiveEventParticipationTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, gameMinute int64, now float64) ([]map[string]any, error) {
	rows, err := conn.Execute(`SELECT event_key,title,payload_json FROM world_events WHERE active=1 AND ends_at>? AND location=? ORDER BY ends_at`, []any{now, c.Location})
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for _, row := range rows.Rows {
		if len(row) < 3 {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(fmt.Sprint(row[2])), &payload) != nil {
			continue
		}
		reward, _ := payload["player_reward"].(map[string]any)
		effect, _ := payload["player_effect"].(map[string]any)
		karma := storage.ParseInt(payload["karma_delta"])
		fate := storage.ParseInt(payload["fate_delta"])
		if len(reward) == 0 && len(effect) == 0 && karma == 0 && fate == 0 {
			continue
		}
		ins, err := conn.Execute(`INSERT OR IGNORE INTO event_claims(user_id,event_key,claimed_at) VALUES(?,?,?)`, []any{userID, fmt.Sprint(row[0]), now})
		if err != nil {
			return nil, err
		}
		if ins.RowsAffected == 0 {
			continue
		}
		id := fmt.Sprint(payload["definition_id"])
		if id == "" || id == "<nil>" {
			id = "participation"
		}
		details, err := applyEventParticipationTx(conn, catalog, userID, c, id, reward, effect, karma, fate, gameMinute, now, "world_event_"+id)
		if err != nil {
			return nil, err
		}
		out = append(out, map[string]any{"event_key": fmt.Sprint(row[0]), "title": fmt.Sprint(row[1]), "details": details})
	}
	return out, nil
}

func activateUnexpectedEventTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, event worlddata.UnexpectedEvent, eventKey string, gameMinute int64, now float64) (map[string]any, error) {
	out := map[string]any{"id": event.ID, "title": event.Title, "category": event.Category, "kind": event.Kind, "description": event.Description, "duration_hours": event.DurationHours, "severity": event.Severity, "consequence_text": event.ConsequenceText, "world_effect": event.WorldEffect, "player_reward": event.PlayerReward, "player_effect": event.PlayerEffect, "karma_delta": event.KarmaDelta, "fate_delta": event.FateDelta, "secret_realm_id": event.SecretRealmID}
	switch event.Kind {
	case "personal":
		persistent, err := createPersonalExplorationEventTx(conn, userID, c, event, eventKey, gameMinute, now)
		if err != nil {
			return nil, err
		}
		for key, value := range persistent {
			out[key] = value
		}
		out["activated"] = true
	case "world_event":
		if strings.TrimSpace(eventKey) == "" {
			return nil, errors.New("event_key is required when an unexpected world event is selected")
		}
		ends := now + float64(maxI64(1, event.DurationHours))*3600
		payload := map[string]any{"definition_id": event.ID, "category": event.Category, "severity": event.Severity, "consequence_text": event.ConsequenceText, "player_reward": event.PlayerReward, "player_effect": event.PlayerEffect, "karma_delta": event.KarmaDelta, "fate_delta": event.FateDelta, "world_effect": event.WorldEffect}
		enc, _ := json.Marshal(payload)
		_, err := conn.Execute(`UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?`, []any{now})
		if err != nil {
			return nil, err
		}
		dedupe := "random:" + event.ID
		existing, err := conn.Execute(`SELECT 1 FROM world_events WHERE dedupe_key=? AND location=? AND active=1 AND ends_at>? LIMIT 1`, []any{dedupe, c.Location, now})
		if err != nil {
			return nil, err
		}
		activated := len(existing.Rows) == 0
		if activated {
			_, err = conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(event_key) DO UPDATE SET active=1,dedupe_key=excluded.dedupe_key,payload_json=excluded.payload_json,ends_at=excluded.ends_at`, []any{eventKey, dedupe, "random_event", event.Title, c.Location, string(enc), now, ends})
			if err != nil {
				return nil, err
			}
			impacts, effectErr := applyWorldEventEffectTx(conn, event.ID, event.Title, c.Location, gameMinute, event.Severity, event.WorldEffect, now)
			if effectErr != nil {
				return nil, effectErr
			}
			out["impacts"] = impacts
			uid := userID
			summary := fmt.Sprintf("A server-wide %s manifested at %s. %s", event.Category, c.Location, event.Description)
			if len(impacts) > 0 {
				summary += " Persistent effects: " + strings.Join(impacts, "; ") + "."
			}
			if histErr := recordWorldHistoryTx(conn, "world_event:"+eventKey+":history", "world_event", event.Title, summary, minI64(95, 50+event.Severity*5), "public", c.Location, "", "world", event.ID, "World phenomenon", "location", c.Location, c.Location, &uid, "", []string{"world event", event.Category, event.ID}, gameMinute, map[string]any{"impacts": impacts, "severity": event.Severity}, now); histErr != nil {
				return nil, histErr
			}
		}
		out["activated"] = activated
		out["event_key"] = eventKey
		out["location"] = c.Location
		out["expires_at"] = ends
	case "secret_realm":
		realm, ok := catalog.SecretRealms[event.SecretRealmID]
		if !ok {
			return nil, errors.New("secret realm definition is missing")
		}
		if strings.TrimSpace(eventKey) == "" {
			return nil, errors.New("event_key is required when a secret realm is selected")
		}
		ends := now + float64(maxI64(1, realm.OpenHours))*3600
		payload := map[string]any{"definition_id": event.ID, "category": event.Category, "realm_id": event.SecretRealmID}
		enc, _ := json.Marshal(payload)
		_, err := conn.Execute(`UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?`, []any{now})
		if err != nil {
			return nil, err
		}
		dedupe := "secret_realm:" + event.SecretRealmID
		existing, err := conn.Execute(`SELECT 1 FROM world_events WHERE dedupe_key=? AND location=? AND active=1 AND ends_at>? LIMIT 1`, []any{dedupe, realm.Location, now})
		if err != nil {
			return nil, err
		}
		activated := len(existing.Rows) == 0
		if activated {
			_, err = conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(event_key) DO UPDATE SET active=1,dedupe_key=excluded.dedupe_key,payload_json=excluded.payload_json,ends_at=excluded.ends_at`, []any{eventKey, dedupe, "secret_realm", realm.Name, realm.Location, string(enc), now, ends})
			if err != nil {
				return nil, err
			}
		}
		out["activated"] = activated
		out["event_key"] = eventKey
		out["location"] = realm.Location
		out["expires_at"] = ends
		out["realm_name"] = realm.Name
		out["realm_description"] = realm.Description
		out["open_hours"] = realm.OpenHours
	}
	return out, nil
}

func explorationExploreAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p explorationPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living incarnation can explore")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	activeEvent, err := activeExplorationEventForUserTx(conn, userID, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if activeEvent != nil {
		eventOut, err := explorationEventPublicTx(conn, activeEvent, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result := map[string]any{"kind": "event_active", "event": eventOut, "surprise": eventOut}
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: "exploration_event_reopened", EntityType: "exploration_event", EntityID: activeEvent.EventID, SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
	}
	if strings.HasPrefix(c.Location, "abode:") || strings.HasPrefix(c.Location, "sect_abode:") || strings.HasPrefix(c.Location, "personal_world:") || strings.HasPrefix(c.Location, "birth_family:") {
		return authoritativeMutation{}, errors.New("world exploration is unavailable inside a private residence or personal world")
	}
	loc, ok := catalog.Locations[c.Location]
	if !ok {
		return authoritativeMutation{}, errors.New("current location is not in the world catalog")
	}
	remaining, err := cooldownRemaining(conn, userID, "explore", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	if len(loc.Encounters) == 0 {
		return authoritativeMutation{}, errors.New("current location has no exploration encounters")
	}
	idx, err := gamerng.Intn(len(loc.Encounters))
	if err != nil {
		return authoritativeMutation{}, err
	}
	encounter := loc.Encounters[idx]
	reward, err := randomExploreReward()
	if err != nil {
		return authoritativeMutation{}, err
	}
	awarded, err := applyCanonicalRewardTx(conn, catalog, userID, c, reward, "explore_discovery", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	c.Cultivation += awarded
	shared, err := claimActiveEventParticipationTx(conn, catalog, userID, c, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	discovered, err := discoverNextLocationTx(conn, catalog, userID, c, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// Walking a city finds its shops (v0.35.0), independently of the road
	// beyond the gate.
	foundShop, err := discoverCityShopTx(conn, catalog, userID, c, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var discoveredShop any
	if foundShop != "" {
		discoveredShop = shopDiscoveryView(catalog, foundShop)
	}
	enabled, err := unexpectedEventsEnabledTx(conn, p.UnexpectedEventsEnabled)
	if err != nil {
		return authoritativeMutation{}, err
	}
	surprise, err := rollUnexpectedEvent(catalog, c, enabled, p.UnexpectedEventChance)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var surpriseOut map[string]any
	if surprise != nil {
		surpriseOut, err = activateUnexpectedEventTx(conn, catalog, userID, c, *surprise, p.EventKey, p.GameMinute, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err = setCooldown(conn, userID, "explore", p.CooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	kind := "exploration"
	if surpriseOut != nil {
		kind = "event_started"
	}
	result := map[string]any{"kind": kind, "location": c.Location, "encounter": encounter, "cultivation_awarded": awarded, "spirit_stones": reward.SpiritStones, "items": reward.Items, "shared_claims": shared, "discovered_location": discovered, "discovered_shop": discoveredShop, "surprise": surpriseOut, "event": surpriseOut}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: "exploration_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func explorationTravelAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p travelPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Destination = strings.TrimSpace(p.Destination)
	if p.Destination == "" {
		return authoritativeMutation{}, errors.New("destination is required")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living incarnation can travel")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if active, err := activeExplorationEventForUserTx(conn, userID, now); err != nil {
		return authoritativeMutation{}, err
	} else if active != nil {
		return authoritativeMutation{}, fmt.Errorf("resolve or leave active exploration event %s before travelling", active.EventID)
	}
	if strings.HasPrefix(c.Location, "abode:") {
		return authoritativeMutation{}, errors.New("leave the player-owned property before normal travel")
	}
	if strings.HasPrefix(c.Location, "personal_world:") {
		return authoritativeMutation{}, errors.New("leave the personal world before normal travel")
	}
	if strings.HasPrefix(c.Location, "sect_abode:") {
		return authoritativeMutation{}, errors.New("leave the sect abode before normal travel")
	}
	if strings.HasPrefix(c.Location, "birth_family:") {
		return authoritativeMutation{}, errors.New("leave the birth family household before normal travel")
	}
	dest, ok := catalog.Locations[p.Destination]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown destination")
	}
	if c.Location == p.Destination {
		return authoritativeMutation{}, errors.New("already at destination")
	}
	if dest.AuctionHouse != "" {
		return authoritativeMutation{}, errors.New("auction houses must be entered through their warded doors")
	}
	if cur, ok := catalog.Locations[c.Location]; ok && cur.AuctionHouse != "" {
		outside := cur.OutsideLocation
		if outside == "" {
			outside = "Greenriver Town"
		}
		if p.Destination != outside {
			return authoritativeMutation{}, fmt.Errorf("warded auction exit leads first to %s", outside)
		}
	}
	// A shop (v0.35.0) is entered from its city's street or from another
	// shop of the same city, and its door opens back onto that street.
	if dest.Shop != "" {
		cur := catalog.Locations[c.Location]
		if c.Location != dest.OutsideLocation && !(cur.Shop != "" && cur.OutsideLocation == dest.OutsideLocation) {
			return authoritativeMutation{}, fmt.Errorf("%s is in %s; travel there first", p.Destination, dest.OutsideLocation)
		}
	} else if cur, ok := catalog.Locations[c.Location]; ok && cur.Shop != "" && p.Destination != cur.OutsideLocation {
		return authoritativeMutation{}, fmt.Errorf("the shop door opens onto %s", cur.OutsideLocation)
	}
	if c.RealmIndex < dest.MinRealmIndex {
		return authoritativeMutation{}, errors.New("destination lies beyond the character's current cultivation")
	}

	mode := strings.TrimSpace(strings.ToLower(p.Mode))
	if mode == "" {
		mode = "known"
	}
	if mode == "hub" {
		if !dest.RealmHub {
			return authoritativeMutation{}, errors.New("destination is not a realm capital")
		}
		if c.RealmIndex < worldMinRealm(catalog, dest.World) {
			return authoritativeMutation{}, errors.New("realm capital is not yet unlocked")
		}
	} else {
		known, err := knownLocationsTx(conn, catalog, userID, c)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if !known[p.Destination] {
			return authoritativeMutation{}, errors.New("destination route has not been discovered")
		}
	}

	route := []string{}
	roadLegs := []map[string]any{}
	roadEncounters := []map[string]any{}
	travelMinutes := int64(0)
	travelCost := int64(0)
	dangerScore := int64(0)
	encounterChance := int64(0)
	roadConnection := false

	if mode != "hub" {
		if plan, found := canonicalRoadRoute(catalog, c.Location, p.Destination, c.RealmIndex); found {
			roadConnection = true
			route = plan.Nodes
			travelCost = plan.Cost
			dangerScore = plan.MaxDanger
			if err := chargeRoadTravelTx(conn, userID, travelCost, now); err != nil {
				return authoritativeMutation{}, err
			}
			elapsed := int64(0)
			for _, leg := range plan.Legs {
				encounterChance = maxI64(encounterChance, leg.Profile.EncounterChance)
				elapsed += leg.Profile.TravelMinutes
				encounter, delay, err := resolveRoadEncounterTx(conn, userID, leg.Profile, p.GameMinute+elapsed, now)
				if err != nil {
					return authoritativeMutation{}, err
				}
				elapsed += delay
				travelMinutes = elapsed
				legOut := map[string]any{
					"from":                     leg.From,
					"to":                       leg.To,
					"base_travel_minutes":      leg.Profile.TravelMinutes,
					"danger":                   leg.Profile.DangerScore,
					"encounter_chance_percent": leg.Profile.EncounterChance,
					"cost_spirit_stones":       leg.Cost,
				}
				if encounter != nil {
					legOut["encounter"] = encounter
					roadEncounters = append(roadEncounters, encounter)
				}
				roadLegs = append(roadLegs, legOut)
			}
		}
	}

	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{p.Destination, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}

	discoveryKind := "travel"
	if roadConnection {
		discoveryKind = "road_travel"
		for _, location := range route[1:] {
			if _, err = conn.Execute(
				`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`,
				[]any{userID, location, discoveryKind, p.GameMinute, now},
			); err != nil {
				return authoritativeMutation{}, err
			}
		}
	} else if _, err = conn.Execute(
		`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`,
		[]any{userID, p.Destination, discoveryKind, p.GameMinute, now},
	); err != nil {
		return authoritativeMutation{}, err
	}

	arrivalGameMinute := p.GameMinute + travelMinutes
	if roadConnection && travelMinutes > 0 {
		if err := setRoadTransitTx(
			conn,
			userID,
			c.Location,
			p.Destination,
			route,
			travelCost,
			p.GameMinute,
			arrivalGameMinute,
			now,
		); err != nil {
			return authoritativeMutation{}, err
		}
	}

	var roadEncounterOut any
	if len(roadEncounters) > 0 {
		roadEncounterOut = roadEncounters[0]
	}
	result := map[string]any{
		"from":                          c.Location,
		"destination":                   p.Destination,
		"world":                         dest.World,
		"description":                   dest.Description,
		"safe_zone":                     dest.SafeZone,
		"realm_hub":                     dest.RealmHub,
		"road_connection":               roadConnection,
		"road_route":                    route,
		"road_legs":                     roadLegs,
		"road_hops":                     len(roadLegs),
		"departure_game_minute":         p.GameMinute,
		"arrival_game_minute":           arrivalGameMinute,
		"travel_minutes":                travelMinutes,
		"travel_cost_spirit_stones":     travelCost,
		"road_danger":                   dangerScore,
		"road_encounter_chance_percent": encounterChance,
		"road_encounter":                roadEncounterOut,
		"road_encounters":               roadEncounters,
		"traveling":                     roadConnection && travelMinutes > 0,
	}
	// Merchants on the way (v0.34.1): whoever walks a leg of this route or
	// waits in a city it passes is named, so the traveller knows to stop.
	if roadConnection {
		encounters, err := merchantEncountersOnRoute(conn, catalog, route, p.GameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result["merchant_encounters"] = encounters
	}
	// Best-effort: hand back real Unix timestamps for departure/arrival too,
	// so the reply can show a live Discord countdown instead of a bare
	// game-minute figure. Not fatal if the world clock can't be read - the
	// game-minute fields above are always present regardless.
	if clock, clockErr := readCanonicalWorldClock(conn); clockErr == nil {
		if ts, ok := realTimestampForGameMinute(clock, p.GameMinute); ok {
			result["departure_unix_ts"] = ts
		}
		if ts, ok := realTimestampForGameMinute(clock, arrivalGameMinute); ok {
			result["arrival_unix_ts"] = ts
		}
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "exploration",
			EventType:  "travel_completed",
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			GameMinute: p.GameMinute,
			Payload:    result,
		},
	}, nil
}

type huntBeast struct {
	Name         string
	TN           int64
	TamingTN     int64
	Rank         int64
	Element      string
	Temperament  string
	Bloodline    string
	Intelligence int64
	Loot         map[string]int64
	Stones       int64
	Cultivation  int64
}

func randomHuntBeast(realmIndex int64) (huntBeast, error) {
	base := []huntBeast{{Name: "Iron-Horn Boar", TN: 12, Loot: map[string]int64{"beast_core": 1, "spirit_iron": 1}, Element: "Earth", Temperament: "stubborn", Bloodline: "Ironhide", Intelligence: 11}, {Name: "Mistclaw Wolf", TN: 13, Loot: map[string]int64{"beast_core": 1}, Element: "Wind", Temperament: "cautious", Bloodline: "Mistclaw", Intelligence: 16}, {Name: "Riverfang Serpent", TN: 14, Loot: map[string]int64{"beast_core": 1, "spirit_herb": 1}, Element: "Water", Temperament: "calculating", Bloodline: "Riverfang", Intelligence: 18}, {Name: "Stoneback Ape", TN: 15, Loot: map[string]int64{"beast_core": 2}, Element: "Earth", Temperament: "proud", Bloodline: "Stoneback", Intelligence: 20}}
	i, err := gamerng.Intn(len(base))
	if err != nil {
		return huntBeast{}, err
	}
	b := base[i]
	scaled := realmIndex
	if scaled < 0 {
		scaled = 0
	}
	if scaled > 12 {
		scaled = 12
	}
	add := scaled
	if add > 4 {
		add = 4
	}
	b.TN += add
	tameAdd := scaled
	if tameAdd > 5 {
		tameAdd = 5
	}
	b.TamingTN = b.TN - add + 2 + tameAdd
	b.Rank = scaled / 4
	b.Intelligence += scaled
	if b.Intelligence > 60 {
		b.Intelligence = 60
	}
	s, err := gamerng.Intn(7)
	if err != nil {
		return huntBeast{}, err
	}
	g, err := gamerng.Intn(10)
	if err != nil {
		return huntBeast{}, err
	}
	b.Stones = int64(4 + s)
	b.Cultivation = int64(8 + g)
	return b, nil
}

func explorationHuntAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p huntPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living incarnation can hunt")
	}
	if strings.HasPrefix(c.Location, "abode:") || strings.HasPrefix(c.Location, "sect_abode:") || strings.HasPrefix(c.Location, "personal_world:") || strings.HasPrefix(c.Location, "birth_family:") {
		return authoritativeMutation{}, errors.New("hunting is unavailable inside a private residence or personal world")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if active, err := activeExplorationEventForUserTx(conn, userID, now); err != nil {
		return authoritativeMutation{}, err
	} else if active != nil {
		return authoritativeMutation{}, fmt.Errorf("resolve or leave active exploration event %s before hunting", active.EventID)
	}
	remaining, err := cooldownRemaining(conn, userID, "hunt", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	beast, err := randomHuntBeast(c.RealmIndex)
	if err != nil {
		return authoritativeMutation{}, err
	}
	agility, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "agility")
	if err != nil {
		return authoritativeMutation{}, err
	}
	body, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "body")
	if err != nil {
		return authoritativeMutation{}, err
	}
	dual := int64(0)
	if c.RealmIndex == c.BodyRealmIndex && c.Phase == c.BodyPhase {
		dual = 1
	}
	roll, err := rollCheck(agility+body+1+dual, beast.TN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, "hunt", p.CooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	awarded := int64(0)
	var bonded map[string]any
	var wild map[string]any
	if roll["success"].(bool) {
		awarded, err = applyCanonicalRewardTx(conn, catalog, userID, c, canonicalReward{Cultivation: beast.Cultivation, SpiritStones: beast.Stones, Items: beast.Loot}, "hunt_success", now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		margin := storage.ParseInt(roll["margin"])
		rows, err := conn.Execute(`SELECT beast_id,name,species,active FROM spirit_beasts WHERE user_id=? ORDER BY active DESC,loyalty DESC,beast_id`, []any{userID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		speciesExists := false
		for _, r := range rows.Rows {
			if len(r) > 2 && fmt.Sprint(r[2]) == beast.Name {
				speciesExists = true
			}
		}
		can := len(rows.Rows) < 5 && !speciesExists
		if can && c.Path == "Beast Binder" && margin >= 8 {
			if len(rows.Rows) > 0 {
				if _, err = conn.Execute(`UPDATE spirit_beasts SET active=0 WHERE user_id=?`, []any{userID}); err != nil {
					return authoritativeMutation{}, err
				}
			}
			r, err := conn.Execute(`INSERT INTO spirit_beasts(user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,loyalty,contract_type,active,techniques_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,25,'equality',?,'[]',?,?)`, []any{userID, beast.Name, beast.Name, beast.Rank, beast.Element, beast.Intelligence, "respectful", beast.Bloodline, len(rows.Rows) == 0, now, now})
			if err != nil {
				return authoritativeMutation{}, err
			}
			bonded = map[string]any{"beast_id": r.LastInsertID, "name": beast.Name, "species": beast.Name, "rank": beast.Rank, "element": beast.Element, "intelligence": beast.Intelligence, "temperament": "respectful", "bloodline": beast.Bloodline, "loyalty": 25, "contract_type": "equality", "active": len(rows.Rows) == 0}
		} else {
			threshold := int64(4)
			if c.Path == "Beast Binder" {
				threshold = 2
			}
			if can && margin >= threshold {
				taming := beast.TamingTN
				if c.Path == "Beast Binder" && taming > 7 {
					taming -= 2
					if taming < 7 {
						taming = 7
					}
				}
				expires := p.GameMinute + 180
				r, err := conn.Execute(`INSERT INTO wild_beast_encounters(user_id,species,rank,element,intelligence,temperament,bloodline,taming_tn,location,expires_game_minute,status,created_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'available',?,?,?)`, []any{userID, beast.Name, beast.Rank, beast.Element, beast.Intelligence, beast.Temperament, beast.Bloodline, taming, c.Location, expires, p.GameMinute, now, now})
				if err != nil {
					return authoritativeMutation{}, err
				}
				wild = map[string]any{"encounter_id": r.LastInsertID, "species": beast.Name, "rank": beast.Rank, "element": beast.Element, "intelligence": beast.Intelligence, "temperament": beast.Temperament, "bloodline": beast.Bloodline, "taming_tn": taming, "location": c.Location, "expires_game_minute": expires}
			}
		}
	}
	beastOut := map[string]any{"name": beast.Name, "tn": beast.TN, "taming_tn": beast.TamingTN, "rank": beast.Rank, "element": beast.Element, "temperament": beast.Temperament, "bloodline": beast.Bloodline, "intelligence": beast.Intelligence, "loot": beast.Loot, "stones": beast.Stones, "cultivation": beast.Cultivation}
	result := map[string]any{"beast": beastOut, "roll": roll, "success": roll["success"], "cultivation_awarded": awarded, "bonded_beast": bonded, "wild_encounter": wild}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: "hunt_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
