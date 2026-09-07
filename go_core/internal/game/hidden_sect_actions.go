package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// `/sect shadow` (v0.23.0) - the last four rows of the v0.21 Authority I
// backlog, and with them the last gameplay writes on the Discord side.
//
// The command reads a player's karma and decides four things from it: whether
// the hidden sect has turned on them, whether it is watching, whether it will
// initiate them, and what it hands over when it does. All four lived in the
// command body, which meant the karma gates were enforced by the same code
// that rendered the reply - and the initiation itself was three unrelated
// writes (membership, item, provenance) that could half-happen.
//
// The status transition is the subtle one. Crossing into righteous karma turns
// an existing membership hostile, and that used to happen only when the player
// happened to open the shadow screen. It is part of the action now, so any
// call sees a membership that reflects the karma the character actually has.

const hiddenSectName = "Heaven-Devouring Demon Sect"

type shadowPayload struct {
	Mode       string `json:"mode"`
	GameMinute int64  `json:"game_minute"`
}

type hiddenSectView struct {
	Membership map[string]any
	Branch     string
	Karma      int64
	Definition worlddata.SectDefinition
}

// readHiddenSectTx loads the character's karma, the cell that would recruit
// them, and their membership - applying the righteous-enemy transition first
// so nothing downstream reads a membership that karma has already invalidated.
func readHiddenSectTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (hiddenSectView, error) {
	definition, ok := catalog.Sects[hiddenSectName]
	if !ok {
		return hiddenSectView{}, errors.New("the hidden sect is not defined in this world")
	}
	res, err := conn.Execute(
		`SELECT karma_score,realm_index FROM characters WHERE user_id=? AND life_status='alive'`,
		[]any{userID})
	if err != nil {
		return hiddenSectView{}, err
	}
	character := firstRowMap(res)
	if character == nil {
		return hiddenSectView{}, errors.New("living character not found")
	}
	karma := storage.ParseInt(character["karma_score"])
	branch := definition.Branches[realmWorldGo(catalog, storage.ParseInt(character["realm_index"]))]
	if strings.TrimSpace(branch) == "" {
		branch = "Unknown Shadow Cell"
	}

	membership, err := hiddenSectMembershipTx(conn, userID)
	if err != nil {
		return hiddenSectView{}, err
	}
	if membership != nil && karma >= definition.RighteousEnemy &&
		fmt.Sprint(membership["status"]) != "enemy" {
		if _, err = conn.Execute(
			`UPDATE hidden_sect_membership SET status='enemy',standing=standing-100,updated_at=? WHERE user_id=?`,
			[]any{nowSeconds(), userID},
		); err != nil {
			return hiddenSectView{}, err
		}
		if membership, err = hiddenSectMembershipTx(conn, userID); err != nil {
			return hiddenSectView{}, err
		}
	}
	return hiddenSectView{Membership: membership, Branch: branch, Karma: karma, Definition: definition}, nil
}

func hiddenSectMembershipTx(conn *storage.Conn, userID int64) (map[string]any, error) {
	res, err := conn.Execute(
		`SELECT user_id,sect_name,rank_name,branch_name,standing,status,joined_game_minute
		 FROM hidden_sect_membership WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	return firstRowMap(res), nil
}

// membershipOrNil keeps "no membership" out of the result as a JSON null
// rather than an empty object. A typed nil map inside an `any` is not nil to
// a caller checking for it, and the Discord side branches on exactly that.
func membershipOrNil(membership map[string]any) any {
	if len(membership) == 0 {
		return nil
	}
	return membership
}

// shadowInitiationManual picks what the cell hands a new initiate: the highest
// demonic manual of the character's own path that they can already carry.
// Transcribed from the command - it is a different rule from sectEntryManual,
// which picks by sect rather than by alignment and path.
func shadowInitiationManual(catalog worlddata.Catalog, path string, realmIndex int64) (string, worlddata.ManualDefinition, bool) {
	bestID := ""
	var best worlddata.ManualDefinition
	for id, manual := range catalog.TechniqueSystem.Manuals {
		if !strings.EqualFold(strings.TrimSpace(manual.Alignment), "demonic") {
			continue
		}
		if !strings.EqualFold(strings.TrimSpace(manual.Path), strings.TrimSpace(path)) {
			continue
		}
		if int64(manual.MinRealmIndex) > realmIndex {
			continue
		}
		// max by (min_realm_index, id), matching Python's max() on the tuple.
		if bestID == "" || manual.MinRealmIndex > best.MinRealmIndex ||
			(manual.MinRealmIndex == best.MinRealmIndex && id > bestID) {
			bestID, best = id, manual
		}
	}
	return bestID, best, bestID != ""
}

func shadowAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p shadowPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	mode := strings.TrimSpace(p.Mode)
	if mode == "" {
		mode = "status"
	}
	if mode != "status" && mode != "initiate" {
		return authoritativeMutation{}, fmt.Errorf("unknown shadow mode: %q", mode)
	}

	view, err := readHiddenSectTx(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"mode":              mode,
		"branch":            view.Branch,
		"karma":             view.Karma,
		"karma_observation": view.Definition.KarmaObservation,
		"karma_initiation":  view.Definition.KarmaInitiation,
		"righteous_enemy":   view.Definition.RighteousEnemy,
		"membership":        membershipOrNil(view.Membership),
	}

	if mode == "status" {
		return authoritativeMutation{
			Result: result,
			Event: eventledger.Event{
				Domain: "sect", EventType: "hidden_sect_observed", EntityType: "character",
				EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result,
			},
		}, nil
	}

	// Initiation, in the order the command refused things.
	if view.Membership != nil && fmt.Sprint(view.Membership["status"]) == "active" {
		return authoritativeMutation{}, errors.New("you are already an active hidden-sect initiate")
	}
	if view.Karma > view.Definition.KarmaInitiation {
		return authoritativeMutation{}, fmt.Errorf(
			"the initiation seal remains cold: karma %d or lower is required, yours is %d",
			view.Definition.KarmaInitiation, view.Karma)
	}
	if view.Karma >= view.Definition.RighteousEnemy {
		return authoritativeMutation{}, errors.New("the hidden sect recognizes you as a righteous enemy, not a recruit")
	}

	if _, err = conn.Execute(
		`INSERT INTO hidden_sect_membership(
			user_id,sect_name,rank_name,branch_name,standing,status,joined_game_minute,updated_at
		 ) VALUES(?,?,'Shadow Initiate',?,0,'active',?,?)
		 ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,
			branch_name=excluded.branch_name,status='active',updated_at=excluded.updated_at`,
		[]any{userID, hiddenSectName, view.Branch, p.GameMinute, nowSeconds()},
	); err != nil {
		return authoritativeMutation{}, err
	}

	res, err := conn.Execute(
		`SELECT path,realm_index FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	character := firstRowMap(res)
	manualID, manual, granted := shadowInitiationManual(
		catalog, fmt.Sprint(character["path"]), storage.ParseInt(character["realm_index"]))
	if granted && strings.TrimSpace(manual.ItemID) != "" {
		now := nowSeconds()
		if _, err = conn.Execute(
			`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,1)
			 ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+1`,
			[]any{userID, manual.ItemID},
		); err != nil {
			return authoritativeMutation{}, err
		}
		if _, err = conn.Execute(
			`INSERT INTO item_provenance(
				user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,
				authenticity,tracking_strength,acquired_game_minute,created_at,updated_at
			 ) VALUES(?,?,1,'hidden_sect_initiation',?,'Heaven-Devouring Seal','forbidden',100,70,?,?,?)`,
			[]any{userID, manual.ItemID, view.Branch, p.GameMinute, now, now},
		); err != nil {
			return authoritativeMutation{}, err
		}
		result["manual_id"] = manualID
		result["manual_name"] = manual.Name
		result["manual_item_id"] = manual.ItemID
	}

	membership, err := hiddenSectMembershipTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result["membership"] = membershipOrNil(membership)
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "sect", EventType: "hidden_sect_initiated", EntityType: "character",
			EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result,
		},
	}, nil
}
