package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const authoritativeAPIVersion = "v1"

type authoritativeMutation struct {
	Result any
	Event  eventledger.Event
}

var authoritativeMutations = map[string]bool{
	"check.resolve":                   true,
	"scene.action":                    true,
	"perfection.start":                true,
	"perfection.quest":                true,
	"perfection.trial":                true,
	"perfection.abandon":              true,
	"perfection.body_start":           true,
	"perfection.body_quest":           true,
	"perfection.body_trial":           true,
	"perfection.body_abandon":         true,
	"character.family_options":        true,
	"character.create":                true,
	"family.household.enter":          true,
	"family.household.leave":          true,
	"family.lineage.investigate":      true,
	"family.lineage.quest":            true,
	"family.dynasty.claim":            true,
	"family.dynasty.conflict":         true,
	"aptitude.temper":                 true,
	"aptitude.awaken":                 true,
	"aptitude.evolve":                 true,
	"aptitude.harmonize":              true,
	"cultivation.train":               true,
	"cultivation.body_train":          true,
	"cultivation.breakthrough":        true,
	"cultivation.body_breakthrough":   true,
	"lifecycle.true_death":            true,
	"lifecycle.reincarnate":           true,
	"combat.start":                    true,
	"combat.turn":                     true,
	"combat.finalize":                 true,
	"combat.technique":                true,
	"combat.recovery_item":            true,
	"law.comprehend":                  true,
	"condition.treat":                 true,
	"alchemy.purge":                   true,
	"character.set_gender":            true,
	"sect.abode.enter":                true,
	"sect.abode.leave":                true,
	"law.technique":                   true,
	"sect.shadow":                     true,
	"sense.inspect":                   true,
	"sense.conceal":                   true,
	"tribulation.prepare":             true,
	"tribulation.attempt":             true,
	"exploration.explore":             true,
	"exploration.event.act":           true,
	"exploration.event.leave":         true,
	"exploration.travel":              true,
	"exploration.hunt":                true,
	"secret_realm.enter":              true,
	"secret_realm.explore":            true,
	"secret_realm.leave":              true,
	"craft.resolve":                   true,
	"forage.resolve":                  true,
	"beast.tame":                      true,
	"beast.feed":                      true,
	"beast.train":                     true,
	"beast.evolve":                    true,
	"beast.active":                    true,
	"artifact.bond":                   true,
	"artifact.awaken":                 true,
	"pvp.challenge":                   true,
	"pvp.respond":                     true,
	"pvp.act":                         true,
	"manual.study":                    true,
	"manual.technique":                true,
	"crime.atone":                     true,
	"world_event.act":                 true,
	"auction.enter":                   true,
	"auction.leave":                   true,
	"auction.sell":                    true,
	"auction.bid":                     true,
	"black_market.trade":              true,
	"market.trade":                    true,
	"bounty_hunter.act":               true,
	"equipment.bind":                  true,
	"equipment.equip":                 true,
	"equipment.unequip":               true,
	"equipment.repair":                true,
	"party.create":                    true,
	"party.join":                      true,
	"party.leave":                     true,
	"formation.create":                true,
	"formation.assign":                true,
	"formation.activate":              true,
	"formation.stance":                true,
	"boss.start":                      true,
	"boss.act":                        true,
	"boss.claim":                      true,
	"territory.claim":                 true,
	"war.act":                         true,
	"caravan.dispatch":                true,
	"caravan.settle":                  true,
	"sect.recruitment.recommendation": true,
	"sect.recruitment.trial":          true,
	"sect.contribute":                 true,
	"sect.redeem":                     true,
	"discipleship.request":            true,
	"discipleship.resolve":            true,
	"discipleship.leave":              true,
	"sect.manor.establish":            true,
	"sect.manor.upgrade":              true,
	"family.simulate":                 true,
	"family.support":                  true,
	"family.add_child":                true,
	"seclusion.start":                 true,
	"seclusion.settle":                true,
	"fate.adjust":                     true,
	"dao.propose":                     true,
	"dao.respond":                     true,
	"dao.sever":                       true,
	"dao.dual_cultivate":              true,
	"storage.deposit":                 true,
	"storage.withdraw":                true,
	"storage.upgrade":                 true,
	"abode.establish":                 true,
	"abode.enter":                     true,
	"abode.visit":                     true,
	"abode.leave":                     true,
	"abode.invite":                    true,
	"abode.revoke":                    true,
	"abode.upgrade":                   true,
	"abode.focus":                     true,
	"array.use":                       true,
	"array.deploy":                    true,
	"item.use":                        true,
	"spatial_key.use":                 true,
	"personal_world.create":           true,
	"personal_world.set_rule":         true,
	"personal_world.enter":            true,
	"personal_world.leave":            true,
	"commission.accept":               true,
	"commission.resolve":              true,
}
var authoritativeQueries = map[string]bool{
	"character.lifespan":        true,
	"npc.lifespan":              true,
	"effects.current":           true,
	"lifecycle.samsara_status":  true,
	"sense.status":              true,
	"secret_realm.status":       true,
	"exploration.event.status":  true,
	"exploration.travel_status": true,
	// v0.30.0: the world-status reads that app/simulation/world.py ran as raw
	// SQL, plus the market prices and the equipment power Python still
	// computed. Listed in world_status_queries.go.
	"market.rows":          true,
	"market.quote":         true,
	"market.catalog":       true,
	"combat.targets":       true,
	"simulation.state":     true,
	"simulation.status":    true,
	"world.recent_actions": true,
	"civilization.status":  true,
	"npc.status":           true,
	"sect.status":          true,
	"clan.status":          true,
	"equipment.power":      true,
}

var stage5CanonicalTimeNativeOperations = map[string]bool{
	"craft.resolve":   true,
	"forage.resolve":  true,
	"beast.tame":      true,
	"beast.feed":      true,
	"beast.train":     true,
	"beast.evolve":    true,
	"beast.active":    true,
	"artifact.bond":   true,
	"artifact.awaken": true,
}

func rejectCallerGameMinute(raw json.RawMessage) error {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(raw, &payload); err != nil {
		return err
	}
	if _, ok := payload["game_minute"]; ok {
		return errors.New("client-supplied game_minute is forbidden")
	}
	return nil
}

func stage5CanonicalizeMutationPayload(conn *storage.Conn, operation string, raw json.RawMessage) (json.RawMessage, int64, error) {
	if err := rejectCallerGameMinute(raw); err != nil {
		return nil, 0, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, 0, err
	}
	if stage5CanonicalTimeNativeOperations[operation] {
		return raw, gameMinute, nil
	}

	payload := map[string]any{}
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return nil, 0, err
		}
	}
	payload["game_minute"] = gameMinute
	encoded, err := json.Marshal(payload)
	if err != nil {
		return nil, 0, err
	}
	return encoded, gameMinute, nil
}

// replayResponse returns the stored response for an action_id that has already
// been applied. `hit` is false when this action_id has never been seen; an
// action_id belonging to a different actor or operation is an error, not a
// replay, because reusing one is a client bug that would otherwise return
// someone else's result.
func replayResponse(conn *storage.Conn, actionID string, req ActionRequest) (ActionResponse, bool, error) {
	replay, err := eventledger.Replay(conn, actionID)
	if err != nil {
		return ActionResponse{}, false, err
	}
	if replay == nil {
		return ActionResponse{}, false, nil
	}
	if replay.ActorID != req.ActorID || replay.Operation != req.Operation {
		return ActionResponse{}, false, errors.New("action_id already belongs to a different action")
	}
	return ActionResponse{
		APIVersion: authoritativeAPIVersion, ActionID: actionID, Operation: req.Operation,
		StateVersion: replay.StateVersion, Replayed: true, Result: replay.Result,
	}, true, nil
}

func isAuthoritativeOperation(op string) bool {
	return authoritativeMutations[op] || authoritativeQueries[op]
}

func applyAuthoritative(databasePath, worldPath string, req ActionRequest) (ActionResponse, error) {
	if authoritativeQueries[req.Operation] {
		return applyAuthoritativeQuery(databasePath, worldPath, req)
	}
	if req.ActorID <= 0 {
		return ActionResponse{}, errors.New("actor_id must be positive")
	}
	version := strings.TrimSpace(req.APIVersion)
	if version == "" {
		version = authoritativeAPIVersion
	}
	if version != authoritativeAPIVersion {
		return ActionResponse{}, fmt.Errorf("unsupported api_version: %s", version)
	}
	actionID := strings.TrimSpace(req.ActionID)
	if actionID == "" || len([]rune(actionID)) > 160 {
		return ActionResponse{}, errors.New("action_id must contain 1..160 characters")
	}
	if err := rejectCallerGameMinute(req.Payload); err != nil {
		return ActionResponse{}, err
	}
	conn, err := storage.Open(databasePath)
	if err != nil {
		return ActionResponse{}, err
	}
	defer conn.Close()
	// The cheap check: most duplicates are a client retrying long after the
	// original committed, and they never need the write lock at all.
	if replayed, hit, err := replayResponse(conn, actionID, req); err != nil {
		return ActionResponse{}, err
	} else if hit {
		return replayed, nil
	}
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return ActionResponse{}, err
	}
	defer func() {
		if conn.InTransaction() {
			_ = conn.Rollback()
		}
	}()
	// And the one that matters: two callers can both miss the check above and
	// then queue for the write lock. Whoever gets in second must see the
	// winner's receipt, or it re-runs the mutation and dies on a unique
	// constraint - the state stays correct, but the caller gets an error where
	// the contract promises the original result. Under 24 concurrent requests
	// with one action_id that was 10-23 spurious failures; this makes them all
	// replays. The check has to be *inside* the transaction, because outside it
	// is exactly the check that just failed.
	if replayed, hit, err := replayResponse(conn, actionID, req); err != nil {
		return ActionResponse{}, err
	} else if hit {
		if err := conn.Rollback(); err != nil {
			return ActionResponse{}, err
		}
		return replayed, nil
	}
	current, err := eventledger.CurrentActorVersion(conn, req.ActorID)
	if err != nil {
		return ActionResponse{}, err
	}
	if req.ExpectedVersion != nil && *req.ExpectedVersion != current {
		return ActionResponse{}, fmt.Errorf("stale expected_version: expected %d current %d", *req.ExpectedVersion, current)
	}
	canonicalPayload, actionGameMinute, err := stage5CanonicalizeMutationPayload(conn, req.Operation, req.Payload)
	if err != nil {
		return ActionResponse{}, err
	}
	req.Payload = canonicalPayload
	if req.Operation != "character.create" {
		if err := refreshPlayerLifespanActivityTx(conn, req.ActorID, actionGameMinute, time.Now()); err != nil {
			return ActionResponse{}, err
		}
	}
	if err := ensureRoadTransitReadyTx(conn, req.ActorID, actionGameMinute); err != nil {
		return ActionResponse{}, err
	}
	oldAgeDeath, err := checkPlayerOldAgeDeathTx(conn, req.ActorID, actionGameMinute, time.Now())
	if err != nil {
		return ActionResponse{}, err
	}
	if oldAgeDeath == nil {
		if err := checkPlayerModerationTx(conn, req.ActorID, req.Operation); err != nil {
			return ActionResponse{}, err
		}
	}
	var mutation authoritativeMutation
	if oldAgeDeath != nil {
		mutation = *oldAgeDeath
	} else {
		switch req.Operation {
		case "check.resolve", "scene.action":
			if strings.TrimSpace(worldPath) == "" {
				return ActionResponse{}, errors.New("world catalog path is required")
			}
			catalog, loadErr := worlddata.Load(worldPath)
			if loadErr != nil {
				return ActionResponse{}, loadErr
			}
			if req.Operation == "check.resolve" {
				mutation, err = resolveCanonicalCheckV2(conn, catalog, req.ActorID, req.Payload)
			} else {
				mutation, err = resolveSceneAction(conn, catalog, req.ActorID, req.Payload)
			}
		case "alchemy.purge":
			mutation, err = alchemyPurgeAction(conn, req.ActorID, req.Payload)
		case "character.set_gender":
			mutation, err = setGenderAction(conn, req.ActorID, req.Payload)
		case "sect.abode.enter":
			mutation, err = sectAbodeMoveAction(conn, req.ActorID, req.Payload, "enter")
		case "sect.abode.leave":
			mutation, err = sectAbodeMoveAction(conn, req.ActorID, req.Payload, "leave")
		case "commission.accept":
			mutation, err = commissionAcceptAction(conn, req.ActorID, req.Payload)
		case "commission.resolve":
			mutation, err = commissionResolveAction(conn, req.ActorID, req.Payload)
		case "lifecycle.true_death":
			mutation, err = trueDeathAction(conn, req.ActorID, req.Payload)
		case "combat.start":
			mutation, err = combatStartAction(conn, req.ActorID, req.Payload)
		case "combat.finalize":
			mutation, err = combatFinalizeAction(conn, req.ActorID, req.Payload)
		case "character.family_options":
			mutation, err = birthFamilyOptionsAction(conn, req.ActorID, req.Payload)
		case "character.create":
			mutation, err = createCharacterAuthoritative(conn, worldPath, req.ActorID, req.Payload)
		case "family.household.enter":
			mutation, err = familyHouseholdEnterAction(conn, req.ActorID, req.Payload)
		case "family.household.leave":
			mutation, err = familyHouseholdLeaveAction(conn, req.ActorID, req.Payload)
		case "family.lineage.investigate":
			mutation, err = lineageInvestigateAction(conn, req.ActorID, req.Payload)
		case "family.lineage.quest":
			mutation, err = dynastyQuestAction(conn, req.ActorID, req.Payload)
		case "family.dynasty.claim":
			mutation, err = dynastyClaimAction(conn, req.ActorID, req.Payload)
		case "family.dynasty.conflict":
			mutation, err = dynastyConflictAction(conn, req.ActorID, req.Payload)
		case "auction.enter", "auction.leave", "auction.sell", "auction.bid", "black_market.trade", "market.trade", "bounty_hunter.act", "equipment.bind", "equipment.equip", "equipment.unequip", "equipment.repair", "party.create", "party.join", "party.leave", "formation.create", "formation.assign", "formation.activate", "formation.stance", "boss.start", "boss.act", "boss.claim", "territory.claim", "war.act", "caravan.dispatch", "caravan.settle", "sect.recruitment.recommendation", "sect.recruitment.trial", "sect.contribute", "sect.redeem", "discipleship.request", "discipleship.resolve", "discipleship.leave", "sect.manor.establish", "sect.manor.upgrade", "family.simulate", "family.support", "family.add_child", "seclusion.start", "seclusion.settle", "fate.adjust", "dao.propose", "dao.respond", "dao.sever", "dao.dual_cultivate", "storage.deposit", "storage.withdraw", "storage.upgrade", "abode.establish", "abode.enter", "abode.visit", "abode.leave", "abode.invite", "abode.revoke", "abode.upgrade", "abode.focus", "array.use", "array.deploy", "spatial_key.use", "personal_world.create", "personal_world.set_rule", "personal_world.enter", "personal_world.leave", "item.use":
			if strings.TrimSpace(worldPath) == "" {
				return ActionResponse{}, errors.New("world catalog path is required")
			}
			catalog, loadErr := worlddata.Load(worldPath)
			if loadErr != nil {
				return ActionResponse{}, loadErr
			}
			mutation, err = applyLateMigrationAction(conn, catalog, req.ActorID, req.Operation, req.Payload)
		case "aptitude.temper", "aptitude.awaken", "aptitude.evolve", "aptitude.harmonize",
			"cultivation.train", "cultivation.body_train", "cultivation.breakthrough", "cultivation.body_breakthrough",
			"lifecycle.reincarnate", "combat.turn", "combat.technique", "combat.recovery_item",
			"perfection.start", "perfection.quest", "perfection.trial", "perfection.abandon",
			"perfection.body_start", "perfection.body_quest", "perfection.body_trial", "perfection.body_abandon", "law.comprehend",
			"law.technique", "sect.shadow", "condition.treat", "sense.inspect", "sense.conceal", "tribulation.prepare", "tribulation.attempt",
			"exploration.explore", "exploration.event.act", "exploration.event.leave", "exploration.travel", "exploration.hunt",
			"secret_realm.enter", "secret_realm.explore", "secret_realm.leave", "craft.resolve", "forage.resolve",
			"beast.tame", "beast.feed", "beast.train", "beast.evolve", "beast.active", "artifact.bond", "artifact.awaken",
			"pvp.challenge", "pvp.respond", "pvp.act", "manual.study", "manual.technique", "crime.atone", "world_event.act":
			if strings.TrimSpace(worldPath) == "" {
				return ActionResponse{}, errors.New("world catalog path is required")
			}
			catalog, loadErr := worlddata.Load(worldPath)
			if loadErr != nil {
				return ActionResponse{}, loadErr
			}
			switch req.Operation {
			case "aptitude.temper":
				mutation, err = aptitudeTemper(conn, catalog, req.ActorID, req.Payload)
			case "aptitude.awaken":
				mutation, err = aptitudeAwaken(conn, catalog, req.ActorID, req.Payload)
			case "aptitude.evolve":
				mutation, err = aptitudeEvolve(conn, catalog, req.ActorID, req.Payload)
			case "aptitude.harmonize":
				mutation, err = aptitudeHarmonize(conn, catalog, req.ActorID, req.Payload)
			case "cultivation.train":
				mutation, err = cultivationTrain(conn, catalog, req.ActorID, req.Payload, false)
			case "cultivation.body_train":
				mutation, err = cultivationTrain(conn, catalog, req.ActorID, req.Payload, true)
			case "cultivation.breakthrough":
				mutation, err = cultivationBreakthrough(conn, catalog, req.ActorID, req.Payload, false)
			case "cultivation.body_breakthrough":
				mutation, err = cultivationBreakthrough(conn, catalog, req.ActorID, req.Payload, true)
			case "lifecycle.reincarnate":
				mutation, err = reincarnateAction(conn, catalog, req.ActorID, req.Payload)
			case "combat.turn":
				mutation, err = combatTurnAction(conn, catalog, req.ActorID, req.Payload)
			case "combat.technique":
				mutation, err = combatTechniqueAction(conn, catalog, req.ActorID, req.Payload)
			case "combat.recovery_item":
				mutation, err = combatRecoveryItemAction(conn, catalog, req.ActorID, req.Payload)
			case "perfection.start":
				mutation, err = perfectionStartAction(conn, catalog, req.ActorID, req.Payload, false)
			case "perfection.quest":
				mutation, err = perfectionQuestAction(conn, catalog, req.ActorID, req.Payload, false)
			case "perfection.trial":
				mutation, err = perfectionTrialAction(conn, catalog, req.ActorID, req.Payload, false)
			case "perfection.abandon":
				mutation, err = perfectionAbandonAction(conn, catalog, req.ActorID, req.Payload, false)
			case "perfection.body_start":
				mutation, err = perfectionStartAction(conn, catalog, req.ActorID, req.Payload, true)
			case "perfection.body_quest":
				mutation, err = perfectionQuestAction(conn, catalog, req.ActorID, req.Payload, true)
			case "perfection.body_trial":
				mutation, err = perfectionTrialAction(conn, catalog, req.ActorID, req.Payload, true)
			case "perfection.body_abandon":
				mutation, err = perfectionAbandonAction(conn, catalog, req.ActorID, req.Payload, true)
			case "law.comprehend":
				mutation, err = lawComprehendAction(conn, catalog, req.ActorID, req.Payload)
			case "law.technique":
				mutation, err = lawTechniqueAction(conn, catalog, req.ActorID, req.Payload)
			case "sect.shadow":
				mutation, err = shadowAction(conn, catalog, req.ActorID, req.Payload)
			case "condition.treat":
				mutation, err = conditionTreatAction(conn, catalog, req.ActorID, req.Payload)
			case "sense.inspect":
				mutation, err = senseInspectAction(conn, catalog, req.ActorID, req.Payload)
			case "sense.conceal":
				mutation, err = senseConcealAction(conn, catalog, req.ActorID, req.Payload)
			case "tribulation.prepare":
				mutation, err = tribulationPrepareAction(conn, catalog, req.ActorID, req.Payload)
			case "tribulation.attempt":
				mutation, err = tribulationAttemptAction(conn, catalog, req.ActorID, req.Payload)
			case "exploration.explore":
				mutation, err = explorationExploreAction(conn, catalog, req.ActorID, req.Payload)
			case "exploration.event.act":
				mutation, err = explorationEventActAction(conn, catalog, req.ActorID, req.Payload, false)
			case "exploration.event.leave":
				mutation, err = explorationEventActAction(conn, catalog, req.ActorID, req.Payload, true)
			case "exploration.travel":
				mutation, err = explorationTravelAction(conn, catalog, req.ActorID, req.Payload)
			case "exploration.hunt":
				mutation, err = explorationHuntAction(conn, catalog, req.ActorID, req.Payload)
			case "secret_realm.enter":
				mutation, err = secretRealmEnterAction(conn, catalog, req.ActorID, req.Payload)
			case "secret_realm.explore":
				mutation, err = secretRealmExploreAction(conn, catalog, req.ActorID, req.Payload)
			case "secret_realm.leave":
				mutation, err = secretRealmLeaveAction(conn, req.ActorID, req.Payload)
			case "craft.resolve":
				mutation, err = craftResolveAction(conn, catalog, req.ActorID, req.Payload)
			case "forage.resolve":
				mutation, err = forageResolveAction(conn, catalog, req.ActorID, req.Payload)
			case "beast.tame":
				mutation, err = beastTameAction(conn, catalog, req.ActorID, req.Payload)
			case "beast.feed":
				mutation, err = beastFeedAction(conn, catalog, req.ActorID, req.Payload)
			case "beast.train":
				mutation, err = beastTrainAction(conn, catalog, req.ActorID, req.Payload)
			case "beast.evolve":
				mutation, err = beastEvolveAction(conn, catalog, req.ActorID, req.Payload)
			case "beast.active":
				mutation, err = beastActiveAction(conn, catalog, req.ActorID, req.Payload)
			case "artifact.bond":
				mutation, err = artifactBondAction(conn, catalog, req.ActorID, req.Payload)
			case "artifact.awaken":
				mutation, err = artifactAwakenAction(conn, catalog, req.ActorID, req.Payload)
			case "pvp.challenge":
				mutation, err = pvpChallengeAction(conn, catalog, req.ActorID, req.Payload)
			case "pvp.respond":
				mutation, err = pvpRespondAction(conn, catalog, req.ActorID, req.Payload)
			case "pvp.act":
				mutation, err = pvpActAction(conn, catalog, req.ActorID, req.Payload)
			case "manual.study":
				mutation, err = manualStudyAction(conn, catalog, req.ActorID, req.Payload)
			case "manual.technique":
				mutation, err = manualTechniqueAction(conn, catalog, req.ActorID, req.Payload)
			case "crime.atone":
				mutation, err = crimeAtoneAction(conn, catalog, req.ActorID, req.Payload)
			case "world_event.act":
				mutation, err = worldEventActAction(conn, catalog, req.ActorID, req.Payload)
			default:
				err = fmt.Errorf("unsupported authoritative operation: %s", req.Operation)
			}
		default:
			err = fmt.Errorf("unsupported authoritative operation: %s", req.Operation)
		}
	}
	if err != nil {
		return ActionResponse{}, err
	}
	if actionGameMinute < 0 {
		actionGameMinute, err = canonicalWorldGameMinute(conn)
		if err != nil {
			return ActionResponse{}, err
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	next, err := eventledger.AdvanceActorVersion(conn, req.ActorID, current, now)
	if err != nil {
		return ActionResponse{}, err
	}
	mutation.Event.EventUID = actionID + ":event"
	mutation.Event.GameMinute = actionGameMinute
	mutation.Event.StateVersion = next
	if mutation.Event.ActorID == nil {
		actor := req.ActorID
		mutation.Event.ActorID = &actor
	}
	if err := eventledger.Append(conn, mutation.Event); err != nil {
		return ActionResponse{}, err
	}
	if mutation.Event.EntityType != "" && mutation.Event.EntityID != "" {
		if err := eventledger.TouchEntityVersion(conn, mutation.Event.Domain, mutation.Event.EntityType, mutation.Event.EntityID, next, now); err != nil {
			return ActionResponse{}, err
		}
	}
	receipt := eventledger.Receipt{ActionID: actionID, ActorID: req.ActorID, Operation: req.Operation, StateVersion: next, Result: mutation.Result}
	if err := eventledger.RecordReceipt(conn, receipt, now); err != nil {
		return ActionResponse{}, err
	}
	if err := conn.Commit(); err != nil {
		return ActionResponse{}, err
	}
	return ActionResponse{APIVersion: authoritativeAPIVersion, ActionID: actionID, Operation: req.Operation, StateVersion: next, Result: mutation.Result}, nil
}

func applyAuthoritativeQuery(databasePath, worldPath string, req ActionRequest) (ActionResponse, error) {
	conn, err := storage.Open(databasePath)
	if err != nil {
		return ActionResponse{}, err
	}
	defer conn.Close()
	if err := rejectCallerGameMinute(req.Payload); err != nil {
		return ActionResponse{}, err
	}
	switch req.Operation {
	case "effects.current":
		result, qerr := currentEffectsAuthorityQuery(conn, req.ActorID)
		if qerr != nil {
			return ActionResponse{}, qerr
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "exploration.event.status":
		result, qerr := explorationEventStatusQuery(conn, req.ActorID, req.Payload)
		if qerr != nil {
			return ActionResponse{}, qerr
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "exploration.travel_status":
		result, qerr := travelStatusQuery(conn, req.ActorID)
		if qerr != nil {
			return ActionResponse{}, qerr
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "secret_realm.status":
		if strings.TrimSpace(worldPath) == "" {
			return ActionResponse{}, errors.New("world catalog path is required")
		}
		catalog, loadErr := worlddata.Load(worldPath)
		if loadErr != nil {
			return ActionResponse{}, loadErr
		}
		result, qerr := secretRealmStatusQuery(conn, catalog, req.ActorID)
		if qerr != nil {
			return ActionResponse{}, qerr
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "sense.status":
		if strings.TrimSpace(worldPath) == "" {
			return ActionResponse{}, errors.New("world catalog path is required")
		}
		catalog, loadErr := worlddata.Load(worldPath)
		if loadErr != nil {
			return ActionResponse{}, loadErr
		}
		gameMinute, err := canonicalWorldGameMinute(conn)
		if err != nil {
			return ActionResponse{}, err
		}
		c, power, precision, rng, err := senseStatsGo(conn, catalog, req.ActorID, gameMinute)
		if err != nil {
			return ActionResponse{}, err
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		result := map[string]any{"power": power, "precision": precision, "range_m": rng, "concealment_active": c.ConcealmentActive, "concealment_strength": concealmentPowerGo(c)}
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "lifecycle.samsara_status":
		var p struct {
			MinutesPerYear int64 `json:"minutes_per_year"`
		}
		if err := json.Unmarshal(req.Payload, &p); err != nil {
			return ActionResponse{}, err
		}
		result, err := samsaraStatus(conn, req.ActorID, p.MinutesPerYear)
		if err != nil {
			return ActionResponse{}, err
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: result}, nil
	case "npc.lifespan":
		var p struct {
			NPCName string `json:"npc_name"`
		}
		if err := json.Unmarshal(req.Payload, &p); err != nil {
			return ActionResponse{}, err
		}
		p.NPCName = strings.TrimSpace(p.NPCName)
		if p.NPCName == "" {
			return ActionResponse{}, errors.New("npc_name is required")
		}
		gameMinute, err := canonicalWorldGameMinute(conn)
		if err != nil {
			return ActionResponse{}, err
		}
		res, err := conn.Execute(
			`SELECT l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,c.realm_index,c.phase
			 FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name
			 WHERE l.npc_name=?`,
			[]any{p.NPCName},
		)
		if err != nil {
			return ActionResponse{}, err
		}
		if len(res.Rows) == 0 {
			return ActionResponse{}, errors.New("npc not found")
		}
		row := res.Rows[0]
		status := lifespanmodel.Evaluate(lifespanmodel.Subject{
			BirthGameMinute:    storage.ParseInt(row[0]),
			AgeAtCreationYears: storage.ParseInt(row[1]),
			NaturalYears:       storage.ParseInt(row[2]),
			RealmIndex:         storage.ParseInt(row[3]),
			Phase:              storage.ParseInt(row[4]),
		}, gameMinute)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, Result: status}, nil
	case "character.lifespan":
		gameMinute, err := canonicalWorldGameMinute(conn)
		if err != nil {
			return ActionResponse{}, err
		}
		res, err := conn.Execute(`SELECT user_id,name,gender,path,spiritual_root,realm_index,phase,body_realm_index,body_phase,natural_lifespan_years,life_extension_years,created_game_minute,age_at_creation_years FROM characters WHERE user_id=?`, []any{req.ActorID})
		if err != nil {
			return ActionResponse{}, err
		}
		if len(res.Rows) == 0 {
			return ActionResponse{}, errors.New("character not found")
		}
		r := res.Rows[0]
		c := CharacterState{UserID: storage.ParseInt(r[0]), Name: fmt.Sprint(r[1]), Gender: fmt.Sprint(r[2]), Path: fmt.Sprint(r[3]), SpiritualRoot: fmt.Sprint(r[4]), RealmIndex: storage.ParseInt(r[5]), Phase: storage.ParseInt(r[6]), BodyRealmIndex: storage.ParseInt(r[7]), BodyPhase: storage.ParseInt(r[8]), NaturalLifespanYears: storage.ParseInt(r[9]), LifeExtensionYears: storage.ParseInt(r[10]), CreatedGameMinute: storage.ParseInt(r[11]), AgeAtCreationYears: storage.ParseInt(r[12])}
		status, err := playerLifespanStatus(conn, c, gameMinute, time.Now())
		if err != nil {
			return ActionResponse{}, err
		}
		v, _ := eventledger.CurrentActorVersion(conn, req.ActorID)
		return ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, StateVersion: v, Result: status}, nil
	}
	if worldStatusQueries[req.Operation] {
		return applyWorldStatusQuery(conn, worldPath, req)
	}
	return ActionResponse{}, fmt.Errorf("unsupported authoritative query: %s", req.Operation)
}

type canonicalCheckPayload struct {
	Modifier   int64  `json:"modifier"`
	TN         int64  `json:"tn"`
	GameMinute int64  `json:"game_minute"`
	Label      string `json:"label"`
}

func resolveCanonicalCheck(actorID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p canonicalCheckPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	d1, err := gamerng.D10()
	if err != nil {
		return authoritativeMutation{}, err
	}
	d2, err := gamerng.D10()
	if err != nil {
		return authoritativeMutation{}, err
	}
	total := d1 + d2 + p.Modifier
	margin := total - p.TN
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
	result := map[string]any{"die1": d1, "die2": d2, "modifier": p.Modifier, "tn": p.TN, "total": total, "margin": margin, "success": total >= p.TN, "degree": degree, "label": p.Label}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "check_resolved", EntityType: "character", EntityID: fmt.Sprint(actorID), GameMinute: p.GameMinute, Payload: result}}, nil
}

type characterCreatePayload struct {
	DiscordName    string `json:"discord_name"`
	Name           string `json:"name"`
	Concept        string `json:"concept"`
	Gender         string `json:"gender"`
	Path           string `json:"path"`
	FamilyChoiceID string `json:"family_choice_id"`
	GameMinute     int64  `json:"game_minute"`
	AgeAtCreation  int64  `json:"age_at_creation_years"`
	TalentEcho     int    `json:"talent_echo"`
}

func createCharacterAuthoritative(conn *storage.Conn, worldPath string, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if strings.TrimSpace(worldPath) == "" {
		return authoritativeMutation{}, errors.New("world catalog path is required for character.create")
	}
	catalog, err := worlddata.Load(worldPath)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var clientFields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &clientFields); err != nil {
		return authoritativeMutation{}, err
	}
	if _, supplied := clientFields["family"]; supplied {
		return authoritativeMutation{}, errors.New("client-supplied family payload is forbidden; use family_choice_id from character.family_options")
	}
	var p characterCreatePayload
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
	selectedChoice, err := loadBirthFamilyChoice(conn, userID, p.FamilyChoiceID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	pFamily := selectedChoice.BirthFamily
	familyID := selectedChoice.FamilyID
	if familyID <= 0 {
		return authoritativeMutation{}, errors.New("canonical starter household is missing")
	}
	location := strings.TrimSpace(pFamily.Location)
	if location == "" {
		location = catalog.StartingLocation
	}
	pFamily.Location = location
	exists, err := conn.Execute(`SELECT 1 FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(exists.Rows) > 0 {
		result := map[string]any{"created": false, "reason": "character_exists"}
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "character_create_rejected", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
	}
	root, err := rollFamilyRoot(pFamily, catalog.Roots)
	if err != nil {
		return authoritativeMutation{}, err
	}
	aptitude, err := generateAptitudes(root, path, pFamily, catalog, p.TalentEcho)
	if err != nil {
		return authoritativeMutation{}, err
	}
	naturalRoll, err := gamerng.Intn(11)
	if err != nil {
		return authoritativeMutation{}, err
	}
	natural := int64(70 + naturalRoll)
	age := p.AgeAtCreation
	if age <= 0 {
		age = 18
	}
	pd := catalog.Paths[path]
	attrs := map[string]int{"body": pd.Body, "agility": pd.Agility, "spirit": pd.Spirit, "insight": pd.Insight, "will": pd.Will, "presence": pd.Presence}
	qiMax := int64(8 + pd.Spirit*2)
	vitMax := int64(10 + pd.Body*2)
	origin := fmt.Sprintf("%s — %s, %s", pFamily.FamilyName, pFamily.Name, location)
	now := float64(time.Now().UnixNano()) / 1e9
	attrsJSON, _ := json.Marshal(attrs)
	_, err = conn.Execute(`INSERT INTO characters(user_id,discord_name,name,origin,path,spiritual_root,concept,gender,age_at_creation_years,created_game_minute,natural_lifespan_years,life_extension_years,life_status,realm_index,phase,cultivation,qi,qi_max,vitality,vitality_max,spirit_stones,insight_xp,location,attributes_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,'alive',0,1,0,?,?,?,?,25,0,?,?,?,?)`, []any{userID, p.DiscordName, p.Name, origin, path, root, p.Concept, gender, age, maxI64(0, p.GameMinute), natural, qiMax, qiMax, vitMax, vitMax, birthFamilyHouseholdLocation(familyID), string(attrsJSON), now, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	householdLocation := birthFamilyHouseholdLocation(familyID)
	sceneMetadata, _ := json.Marshal(map[string]any{"family_id": familyID, "family_name": pFamily.FamilyName, "base_location": location})
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type=excluded.scene_type,scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=NULL,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`, []any{userID, location, "birth_family_household", householdLocation, pFamily.FamilyName + " Household", nil, string(sceneMetadata), now}); err != nil {
		return authoritativeMutation{}, err
	}
	for item, qty := range map[string]int64{"spirit_herb": 2, "spirit_iron": 1} {
		if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)`, []any{userID, item, qty}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if _, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)`, []any{userID, "low_spirit_stone", 25}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at) VALUES(?,?,?,?,?,?,?)`, []any{userID, "common_spatial_pouch", "Common Spatial Pouch", "Mortal", 24, 0, now}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`, []any{userID, location, "birthplace", maxI64(0, p.GameMinute), now}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(?,?,?,?,?)`, []any{userID, familyID, maxI64(1, pFamily.BirthOrder), 1, -999999999}); err != nil {
		return authoritativeMutation{}, err
	}
	historyRes, err := conn.Execute(`SELECT history_json FROM birth_families WHERE family_id=?`, []any{familyID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	history := []string{}
	if len(historyRes.Rows) > 0 {
		_ = json.Unmarshal([]byte(fmt.Sprint(historyRes.Rows[0][0])), &history)
	}
	history = append(history, fmt.Sprintf("%s welcomed %s into the household.", pFamily.FamilyName, p.Name))
	if len(history) > 80 {
		history = history[len(history)-80:]
	}
	historyJSON, _ := json.Marshal(history)
	if _, err = conn.Execute(`UPDATE birth_families SET history_json=?,updated_at=? WHERE family_id=?`, []any{string(historyJSON), now, familyID}); err != nil {
		return authoritativeMutation{}, err
	}
	rootElems, _ := json.Marshal(aptitude.Root.Elements)
	if _, err = conn.Execute(`INSERT INTO character_spiritual_roots(user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, aptitude.Root.Grade, aptitude.Root.Purity, string(rootElems), aptitude.Root.Mutation, aptitude.Root.Stability, aptitude.Root.RefinementProgress, aptitude.Root.Compatibility, now}); err != nil {
		return authoritativeMutation{}, err
	}
	if aptitude.Bloodline != nil {
		b := aptitude.Bloodline
		tech, _ := json.Marshal(b.UnlockedTechniques)
		var source any
		if familyID > 0 {
			source = familyID
		}
		if _, err = conn.Execute(`INSERT INTO character_bloodlines(user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,primary_lineage,source_family_id,unlocked_techniques_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, b.BloodlineID, b.Name, b.Affinity, b.Purity, b.State, b.EvolutionStage, b.Progress, b.Rejection, b.Mutation, b.PrimaryLineage, source, string(tech), now}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	ph := aptitude.Physique
	if _, err = conn.Execute(`INSERT INTO character_physiques(user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, ph.PhysiqueID, ph.Name, ph.State, ph.EvolutionStage, ph.Progress, ph.Stability, ph.Instability, now}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`DELETE FROM character_creation_family_options WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	legacyEvent, _ := json.Marshal(map[string]any{"name": p.Name, "path": path, "spiritual_root": root, "gender": gender, "origin": origin, "location": householdLocation, "physical_location": location, "family_archetype": firstNonempty(pFamily.ID, pFamily.Archetype)})
	if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "character_created", string(legacyEvent), now}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"created": true, "user_id": userID, "name": p.Name, "origin": origin, "path": path, "gender": gender, "location": householdLocation, "physical_location": location, "spiritual_root": root, "natural_lifespan_years": natural, "attributes": attrs, "qi_max": qiMax, "vitality_max": vitMax, "aptitudes": aptitude, "family_id": familyID, "family": pFamily}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "character_created", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func firstNonempty(v, fallback string) string {
	if strings.TrimSpace(v) != "" {
		return v
	}
	return fallback
}
func maxI64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
func defaultI64(v, d int64) int64 {
	if v == 0 {
		return d
	}
	return v
}
func clampI64(v, lo, hi int64) int64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
