package game

// The gate you tear open, and what your money is worth on the far side
// (v1.0.0-rc.44).
//
// Two things were missing at the world boundary, and they are the same
// omission seen from either side of it.
//
// **Nothing converted.** A cultivator who crossed into the Spiritual World
// arrived carrying Mortal stones. Every price over there is in
// `low_spirit_crystal` and every reward has paid in the local money since
// earlier in this release, so what they carried across was not merely worth
// less - it was worth nothing at all, and the fortune of a whole Mortal life
// sat in the purse unspendable. The content already said what to do: each
// currency carries a `base_ratio`, what one unit is worth in its world's
// tier-1 money, and the four worlds are the same ladder seen from further up.
// A crossing now converts at that rung - a hundred of the world below buys one
// of the world above - and the remainder stays in the money it was already in,
// because nothing should be destroyed by walking through a door.
//
// **The heavens opened over one named place and left nothing there.** Clearing
// a world-crossing tribulation wrote `tribulation_state.cleared`, granted a
// reputation point and a fate point, and stopped. The only anchored crossing
// out of a world is one authored array in one capital, so the storm that
// judged you at a waystation in the hills changed that waystation not at all.
// `ascension.gate` anchors the seam where the lightning fell: a permanent
// crossing standing at your own location, into the world the gate you survived
// opens onto, arriving at the terminus content already names for that pair.
//
// It is not a second travel mechanism. A raised crossing is resolved by
// `array.use` beside the authored eight, pays the same fare in the same money,
// converts through the same door - and, because it is ground the world can
// stand on rather than a private teleport, the world's own people walk through
// it too (`npcTravel`) - but only the ones whose cultivation is near the
// cultivator who tore it. A seam is cut to the measure of whoever survived the
// storm that made it, and somebody far below that has no business in it; the
// row carries `opened_realm_index` for exactly that comparison. The world
// boundary in `WhereAnNPCCanWalk` stays exactly where it was: content roads
// still never leave a world. Only a gate somebody tore open does.

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	// crossingKeyPrefix namespaces a raised gate's id against the authored
	// arrays, which are keyed by their content id. `array.use` looks in the
	// catalogue first, so a raised gate can never shadow an authored one.
	crossingKeyPrefix = "crossing:"
	// What one unit of a world's money is worth in the world below, when the
	// content does not say. Every world in the shipped file says 100.
	defaultLadderRung = 100
	// A gate costs the authored crossing's fare times this, when the content
	// does not say. Anchoring a seam is not a transit.
	defaultRaiseCostMultiplier = 10
	// How loudly one traveller is written. Deliberately far below
	// `QUEST_FORGE_MIN_SIGNIFICANCE` (80): the crossing is the significant
	// event and it is written once, when it is anchored.
	DefaultNPCHistorySignificance = 45
)

// worldLadderRung is what one unit of `world`'s money is worth one rung down.
//
// Read off the world's own tier-2 currency, because that is precisely what
// `base_ratio` states: a Mid-Grade Spirit Stone is a hundred Low-Grade ones,
// and a world is the same step seen from further up. A world whose content
// carries no tier-2 currency falls back to the hundred every shipped world
// declares rather than to 1, which would make a crossing free.
func worldLadderRung(catalog worlddata.Catalog, world string) int64 {
	ids := make([]string, 0, len(catalog.Currencies))
	for id := range catalog.Currencies {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if def := catalog.Currencies[id]; def.World == world && def.Tier == 2 && def.BaseRatio > 0 {
			return def.BaseRatio
		}
	}
	return defaultLadderRung
}

// crossWorldsPurseTx converts what a cultivator carries into the money of the
// world they have just arrived in.
//
// Direction is decided by `worldTierIndex`, the same floor the support gift
// and the depth term already use, so the four worlds cannot be ordered one way
// here and another way there. Going up divides and leaves the remainder where
// it was; going down multiplies and takes the whole balance, because a
// remainder cannot exist in that direction.
//
// The credit is written even when it is zero. `walletDeltaTx` sets the sheet's
// mirror from the balance of the world the character is standing in, and a
// cultivator who arrives too poor to convert anything must still have a sheet
// that reads in local money rather than one still showing the fortune they
// left behind.
func crossWorldsPurseTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, fromWorld, toWorld string, now float64) (map[string]any, error) {
	from := worldBaseCurrency(catalog, fromWorld)
	to := worldBaseCurrency(catalog, toWorld)
	if from == "" || to == "" || from == to {
		return nil, nil
	}
	balance, err := walletBalanceTx(conn, userID, from)
	if err != nil {
		return nil, err
	}
	steps := worldTierIndex(catalog, toWorld) - worldTierIndex(catalog, fromWorld)
	lower := toWorld
	if steps > 0 {
		lower = fromWorld
	}
	rate := ladderFactor(worldLadderRung(catalog, lower), steps)
	converted, spent := balance, balance
	switch {
	case steps > 0:
		converted = balance / rate
		spent = converted * rate
	case steps < 0:
		if rate > 0 && balance > math.MaxInt64/rate {
			converted = math.MaxInt64
		} else {
			converted = balance * rate
		}
	}
	if spent > 0 {
		if _, err = walletDeltaTx(conn, catalog, userID, from, -spent, now); err != nil {
			return nil, err
		}
	}
	arrived, err := walletDeltaTx(conn, catalog, userID, to, converted, now)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"from_currency": from, "to_currency": to, "from_world": fromWorld, "to_world": toWorld,
		"spent": spent, "converted": converted, "remainder": balance - spent, "rate": rate,
		"balance": arrived,
	}, nil
}

// ladderFactor is the rung raised to the number of worlds crossed, clamped
// well short of overflow. Every crossing in the content is one world, so the
// loop runs once; the clamp is for a world map somebody else writes.
func ladderFactor(rung, steps int64) int64 {
	if rung < 1 {
		rung = 1
	}
	if steps < 0 {
		steps = -steps
	}
	factor := int64(1)
	for i := int64(0); i < steps; i++ {
		if factor > math.MaxInt64/rung {
			return math.MaxInt64
		}
		factor *= rung
	}
	if factor < 1 {
		return 1
	}
	return factor
}

// moveCharacterTx is the one door out of a world.
//
// Every path that puts a character somewhere else goes through here, and the
// reason is the purse: a crossing that converts at one door and not another is
// worse than one that never converts at all, because only some of the money
// survives and which half depends on how you travelled. `TestAWorldIsLeftByOneDoor`
// holds that no other statement in the package writes `characters.location`.
//
// It answers the conversion report, or nil when the move stayed inside one
// world, which is nearly every move in the game.
func moveCharacterTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, destination string, now float64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	fromWorld := catalog.Locations[fmt.Sprint(row["location"])].World
	toWorld := catalog.Locations[destination].World
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{destination, now, userID}); err != nil {
		return nil, err
	}
	// A key the catalogue does not carry - a household interior, a personal
	// world, a scene key - has no world of its own, and a move into or out of
	// one is not a crossing. Converting there would empty a purse on the way
	// through a front door.
	if fromWorld == "" || toWorld == "" || fromWorld == toWorld {
		return nil, nil
	}
	exchange, err := crossWorldsPurseTx(conn, catalog, userID, fromWorld, toWorld, now)
	if err != nil {
		return nil, err
	}
	// A crossing always answers, even when there was nothing to convert. The
	// answer is what tells presentation that a world boundary was crossed -
	// the `world_cross` objective is reported off it - and a world map whose
	// two sides happened to share a base currency must not silently stop
	// counting as an ascension.
	if exchange == nil {
		exchange = map[string]any{"from_world": fromWorld, "to_world": toWorld, "converted": 0, "spent": 0, "remainder": 0, "rate": 1}
	}
	return exchange, nil
}

// ---------- the raised crossings ----------

// raisedCrossing is a gate somebody tore open, as the engine reads it back.
type raisedCrossing struct {
	Location      string
	Name          string
	FromWorld     string
	ToWorld       string
	Destination   string
	Currency      string
	Cost          int64
	MinRealmIndex int64
	OpenedBy      int64
}

func crossingKey(location string) string { return crossingKeyPrefix + location }

// Crossing is a raised gate as the world simulation sees it: somewhere to
// walk, and what it takes to walk there.
type Crossing struct {
	Location      string
	Destination   string
	ToWorld       string
	Name          string
	MinRealmIndex int64
	// OpenedRealmIndex is the cultivation of whoever tore the seam. An NPC
	// walks through only if their own is near it - see `NPCMayCross`.
	OpenedRealmIndex int64
}

// NPCMayCross is whether one of the world's own people can use a raised gate.
//
// Two conditions, and the second is the one that makes a gate personal. The
// authored crossing's realm floor is what the road itself demands of anybody,
// player or not. Beyond that, a seam is cut to the measure of the cultivator
// who survived the storm that made it: `reach` is how many realms either side
// of them still fits through. A village smith does not walk into the Spiritual
// World because an Ascension-realm cultivator once tore the sky open over their
// town.
func NPCMayCross(crossing Crossing, realmIndex, reach int64) bool {
	if crossing.Destination == "" || realmIndex < crossing.MinRealmIndex {
		return false
	}
	if reach < 0 {
		reach = 0
	}
	gap := realmIndex - crossing.OpenedRealmIndex
	if gap < 0 {
		gap = -gap
	}
	return gap <= reach
}

// OpenCrossings is every raised gate in the world, keyed by where it stands.
//
// Loaded once per tick rather than per NPC: there are single figures of these
// and five hundred and seventy-four people, and the whole point of the batched
// simulation is that the second number never becomes a query count.
func OpenCrossings(conn *storage.Conn) (map[string]Crossing, error) {
	if !tableExistsTx(conn, "world_crossings") {
		return nil, nil
	}
	res, err := conn.Execute(
		`SELECT location_key,destination_location,to_world,name,min_realm_index,opened_realm_index
		   FROM world_crossings ORDER BY location_key`, nil)
	if err != nil {
		return nil, err
	}
	out := map[string]Crossing{}
	for _, row := range res.Rows {
		if len(row) < 6 {
			continue
		}
		location := fmt.Sprint(row[0])
		out[location] = Crossing{
			Location: location, Destination: fmt.Sprint(row[1]), ToWorld: fmt.Sprint(row[2]),
			Name: fmt.Sprint(row[3]), MinRealmIndex: storage.ParseInt(row[4]),
			OpenedRealmIndex: storage.ParseInt(row[5]),
		}
	}
	return out, nil
}

// RecordNPCCrossingTx writes that one of the world's own people walked
// through a gate a cultivator raised, and counts it against the gate.
//
// Public, and deliberately well under `QUEST_FORGE_MIN_SIGNIFICANCE`: the
// crossing itself is the significant event and it is written once, when it is
// anchored. Every traveller after that is news, not a quest.
func RecordNPCCrossingTx(conn *storage.Conn, catalog worlddata.Catalog, name, gateName, location, destination, toWorld string, gm int64, now float64) error {
	if _, err := conn.Execute(
		`UPDATE world_crossings SET npc_uses=npc_uses+1 WHERE location_key=?`, []any{location}); err != nil {
		return err
	}
	significance := catalog.WorldCrossing.NPCHistorySignificance
	if significance <= 0 {
		significance = DefaultNPCHistorySignificance
	}
	return recordWorldHistoryTx(conn,
		fmt.Sprintf("crossing_walked:%s:%s:%d", location, name, gm),
		"world_crossing_used",
		fmt.Sprintf("%s stepped through the %s", name, gateName),
		fmt.Sprintf("%s walked out of %s through the %s and into %s, arriving at %s.", name, location, gateName, toWorld, destination),
		significance, "public", location, "", "npc", name, name, "world", toWorld, toWorld,
		nil, name, []string{"world_crossing", "travel", toWorld}, gm, map[string]any{
			"location": location, "destination": destination, "to_world": toWorld, "gate": gateName,
		}, now)
}

func scanCrossing(catalog worlddata.Catalog, row map[string]any) raisedCrossing {
	c := raisedCrossing{
		Location:      fmt.Sprint(row["location_key"]),
		Name:          fmt.Sprint(row["name"]),
		FromWorld:     fmt.Sprint(row["from_world"]),
		ToWorld:       fmt.Sprint(row["to_world"]),
		Destination:   fmt.Sprint(row["destination_location"]),
		Cost:          i64(row["cost"]),
		MinRealmIndex: i64(row["min_realm_index"]),
		OpenedBy:      i64(row["opened_by_user_id"]),
	}
	c.Currency = worldBaseCurrency(catalog, c.FromWorld)
	return c
}

func crossingAtTx(conn *storage.Conn, catalog worlddata.Catalog, location string) (raisedCrossing, bool, error) {
	if !tableExistsTx(conn, "world_crossings") {
		return raisedCrossing{}, false, nil
	}
	res, err := conn.Execute(`SELECT * FROM world_crossings WHERE location_key=?`, []any{location})
	if err != nil {
		return raisedCrossing{}, false, err
	}
	row := firstRowMap(res)
	if row == nil {
		return raisedCrossing{}, false, nil
	}
	return scanCrossing(catalog, row), true, nil
}

// authoredCrossing is the array content anchors between two worlds: where it
// arrives, what it charges, and the realm it will not carry. A raised gate
// borrows all three, so a cultivator cannot tear open a cheaper road than the
// one the world already has.
func authoredCrossing(catalog worlddata.Catalog, fromWorld, toWorld string) (worlddata.TeleportArray, bool) {
	ids := make([]string, 0, len(catalog.TeleportArrays))
	for id := range catalog.TeleportArrays {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		array := catalog.TeleportArrays[id]
		if catalog.Locations[array.From].World == fromWorld && catalog.Locations[array.To].World == toWorld {
			return array, true
		}
	}
	return worlddata.TeleportArray{}, false
}

// ascensionGateOutOf is the world-crossing tribulation that leads out of a
// world. Sorted, because the gates are a map and two gates out of one world
// would otherwise be read differently on different runs.
func ascensionGateOutOf(world string) (tribulationGate, bool) {
	realms := make([]int64, 0, len(tribulationGates))
	for realm := range tribulationGates {
		realms = append(realms, realm)
	}
	sort.Slice(realms, func(a, b int) bool { return realms[a] < realms[b] })
	for _, realm := range realms {
		if gate := tribulationGates[realm]; gate.From == world {
			return gate, true
		}
	}
	return tribulationGate{}, false
}

type ascensionGatePayload struct {
	GameMinute int64 `json:"game_minute"`
}

// ascensionGateAction anchors the seam a survived tribulation left.
func ascensionGateAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p ascensionGatePayload
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &p); err != nil {
			return authoritativeMutation{}, err
		}
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot anchor a crossing")
	}
	loc, known := catalog.Locations[c.Location]
	if !known || strings.TrimSpace(loc.World) == "" {
		return authoritativeMutation{}, errors.New("a crossing can only be anchored somewhere the world itself knows; stand in a real place first")
	}
	if loc.Private {
		return authoritativeMutation{}, errors.New("a crossing cannot be anchored inside a private place: it is public ground once it stands")
	}
	gate, hasGate := ascensionGateOutOf(loc.World)
	if !hasGate {
		return authoritativeMutation{}, errors.New("no world-crossing tribulation leads out of this world")
	}
	_, _, cleared, err := tribulationState(conn, userID, gate.Realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !cleared {
		return authoritativeMutation{}, fmt.Errorf("the seam is not yours to anchor: survive the %s first", gate.Name)
	}
	array, authored := authoredCrossing(catalog, loc.World, gate.To)
	if !authored {
		return authoritativeMutation{}, fmt.Errorf("no road between %s and %s is known, so there is nothing to anchor onto", loc.World, gate.To)
	}
	if _, standing, err := crossingAtTx(conn, catalog, c.Location); err != nil {
		return authoritativeMutation{}, err
	} else if standing {
		return authoritativeMutation{}, errors.New("a crossing already stands here")
	}
	// One seam per cultivator per world. The tribulation is survived once and
	// tears one sky; a second gate would be a second storm.
	mine, err := conn.Execute(
		`SELECT location_key FROM world_crossings WHERE opened_by_user_id=? AND from_world=?`, []any{userID, loc.World})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row := firstRowMap(mine); row != nil {
		return authoritativeMutation{}, fmt.Errorf("you have already anchored your crossing out of %s, at %s", loc.World, fmt.Sprint(row["location_key"]))
	}
	multiplier := catalog.WorldCrossing.RaiseCostMultiplier
	if multiplier <= 0 {
		multiplier = defaultRaiseCostMultiplier
	}
	currency := worldBaseCurrency(catalog, loc.World)
	cost := array.Cost * multiplier
	balance, err := walletDeltaTx(conn, catalog, userID, currency, -cost, nowSeconds())
	if err != nil {
		return authoritativeMutation{}, fmt.Errorf("anchoring the seam costs %d %s, which you do not have", cost, strings.ReplaceAll(currency, "_", " "))
	}
	name := strings.TrimSpace(catalog.WorldCrossing.NameTemplate)
	if name == "" {
		name = "{character}'s Ascension Gate"
	}
	name = strings.ReplaceAll(name, "{character}", c.Name)
	now := nowSeconds()
	// The cultivation the seam was cut at. Whoever tore it decides who else
	// fits through it, and on either ladder: a body cultivator who survived the
	// storm tore it at the realm that carried them (`accessRealmIndex`).
	openedAt := c.accessRealmIndex()
	if _, err = conn.Execute(`INSERT INTO world_crossings(location_key,name,from_world,to_world,destination_location,min_realm_index,opened_realm_index,cost,opened_by_user_id,opened_game_minute,player_uses,npc_uses,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?)`,
		[]any{c.Location, name, loc.World, gate.To, array.To, array.MinRealmIndex, openedAt, array.Cost, userID, p.GameMinute, now}); err != nil {
		return authoritativeMutation{}, err
	}
	significance := catalog.WorldCrossing.HistorySignificance
	if significance <= 0 {
		significance = 88
	}
	if err = recordWorldHistoryTx(conn,
		fmt.Sprintf("crossing_raised:%s", c.Location),
		"world_crossing_raised",
		fmt.Sprintf("A crossing into %s opened at %s", gate.To, c.Location),
		fmt.Sprintf("%s anchored the seam the %s tore over %s, and the %s now stands there: a public road out of %s into %s, arriving at %s.", c.Name, gate.Name, c.Location, name, loc.World, gate.To, array.To),
		significance, "public", c.Location, "", "player", fmt.Sprint(userID), c.Name, "world", gate.To, gate.To,
		&userID, "", []string{"world_crossing", "ascension", loc.World, gate.To}, p.GameMinute,
		map[string]any{"location": c.Location, "from_world": loc.World, "to_world": gate.To, "destination": array.To, "gate": name}, now); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"location": c.Location, "name": name, "from_world": loc.World, "to_world": gate.To,
		"destination": array.To, "min_realm_index": array.MinRealmIndex, "fare": array.Cost,
		"opened_realm_index": openedAt,
		"currency":           currency, "cost": cost, "balance": balance, "array_id": crossingKey(c.Location),
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "travel", EventType: "ascension.gate", EntityType: "character",
		EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
