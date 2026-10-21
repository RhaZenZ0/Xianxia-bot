package game

// Every field a caller may state, in one list (v1.36.0).
//
// rc.56 found fourteen actions reading their own wait off the payload; v1.35.0
// found the samsara wait and a duel's window the same way, one action at a
// time, by somebody noticing. `callerOwnedNothing` is the refusal list - the
// fields a caller may never send - and this is its mirror: the fields a caller
// may. Every struct the engine unmarshals a request payload into is walked by
// AST, every json field it carries is held to `callerStates` (a thing the
// caller legitimately says: what they want, which thing, how much of their
// own) or to `callerIgnored` (a field kept on the wire for an older bot and
// read by nothing, which the gate proves), and a field in neither fails here
// naming its struct. The next outcome or bound somebody adds to a payload is
// found before a release, not after.
//
// The list is the decision, and it is kept honest both ways: a field in the
// list that no struct carries is stale and fails too.

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// What a caller may say, by json field, with the reason it is theirs to say.
// A reason is one of a few kinds, named so the list reads as a policy rather
// than a hundred and forty sentences.
const (
	statesWhich  = "names a thing the engine looks up, and refuses when it is not there or not theirs"
	statesChoice = "a choice the engine offers and bounds; the engine decides what the choice is worth"
	statesOffer  = "what the caller offers of their own - the engine checks they hold it and prices it itself"
	statesText   = "text the caller authors, stored as written and never read as a number"
	statesWho    = "another cultivator the caller names; the engine checks they exist and are reachable"
	statesWire   = "a protocol field: the version the caller saw, so a stale press is refused rather than replayed"
	statesScene  = "presentation state the bot draws (the thread, the label, the scene type); the engine stores it for the panel and reads no mechanic off it"
	// What a GM authors through the dashboard (admin.quest.save): content, not
	// a player's state. The Forge's validator (app/rules/quests.py,
	// validate_quest_definition) is the gate on it - the budget, the targets,
	// the objective types - and the engine writes the row it is given.
	statesAuthored = "quest content a GM authors through the dashboard (admin.quest.save), held by the Forge's validator before it reaches the engine"
	// The /check die (check.resolve, sense.py): a roleplay beat the player
	// calls, naming the attribute and the difficulty they are rolling against
	// and what they are attempting. The engine rolls with the attribute it
	// computes and writes nothing but the result; a scene's own checks
	// (scene.action) take their TN from sceneActionProfiles and not from here.
	statesDie = "the /check die a player calls for a roleplay beat: attribute and difficulty are the player's own call, the engine rolls and records only the result"
)

var callerStates = map[string]string{
	// Which thing.
	"item_id": statesWhich, "npc_name": statesWhich, "quest_key": statesWhich, "quest_id": statesWhich,
	"battle_id": statesWhich, "beast_id": statesWhich, "auction_id": statesWhich, "house_id": statesWhich,
	"challenge_id": statesWhich, "match_id": statesWhich, "encounter_id": statesWhich, "formation_id": statesWhich,
	"party_id": statesWhich, "listing_id": statesWhich, "offer_id": statesWhich, "pursuit_id": statesWhich,
	"crime_id": statesWhich, "request_id": statesWhich, "partnership_id": statesWhich, "flame_id": statesWhich,
	"array_id": statesWhich, "claim_id": statesWhich, "history_id": statesWhich, "event_id": statesWhich,
	"event_key": statesWhich, "node_key": statesWhich, "realm_id": statesWhich, "manual_id": statesWhich,
	"technique_id": statesWhich, "template_key": statesWhich, "territory_key": statesWhich, "war_id": statesWhich,
	"family_id": statesWhich, "family_choice_id": statesWhich, "sect_name": statesWhich, "location": statesWhich,
	"destination": statesWhich, "target": statesWhich, "recipe": statesWhich, "law": statesWhich,
	"profession": statesWhich, "facility": statesWhich, "property_type": statesWhich, "trial_name": statesWhich,
	"examiner": statesWhich, "scene_key": statesWhich, "currency_id": statesWhich, "spirit_name": statesWhich,
	"technique": statesWhich, "manual_technique": statesWhich, "objective_type": statesWhich, "action_key": statesWhich,
	"action": statesWhich, "rule": statesWhich, "merchant": statesWhich, "food": statesWhich,
	"condition": statesWhich, "id": statesWhich,
	// An older bot spelled the event and its node into the battle's source
	// (`event:<key>|node:<n>`); the engine reads those two keys off it and
	// nothing else (combatStartPayload).
	"source": statesWhich,
	// A choice.
	"mode": statesChoice, "style": statesChoice, "tactic": statesChoice, "stance": statesChoice, "kind": statesChoice,
	"path": statesChoice, "gender": statesChoice, "accept": statesChoice, "confirm": statesChoice, "reroll": statesChoice,
	"buy": statesChoice, "smuggle": statesChoice, "escort": statesChoice, "all": statesChoice, "active": statesChoice,
	"anonymous": statesChoice, "force_end": statesChoice, "paid": statesChoice, "spend_insight": statesChoice,
	"claim_type": statesChoice, "variant_index": statesChoice, "seniority_order": statesChoice,
	"hold_policy": statesChoice, "definition": statesChoice, "admin_retire": statesChoice, "position": statesChoice,
	// Spare or kill, offered only once the engine has read npc_hp<=0 off the
	// battle; a commission's outcome is the giver's word on the player's
	// report, which the engine then prices.
	"outcome": statesChoice,
	// What they offer of their own.
	"quantity": statesOffer, "amount": statesOffer, "starting_bid": statesOffer, "unit_price": statesOffer,
	"give_items": statesOffer, "give_stones": statesOffer, "want_items": statesOffer, "want_stones": statesOffer,
	"stakes": statesOffer, "ends_at": statesOffer, "duration_real_minutes": statesOffer, "duration_game_minutes": statesOffer,
	// Text.
	"attribute": statesDie, "tn": statesDie, "label": statesText,
	"name": statesText, "concept": statesText, "discord_name": statesText, "child_name": statesText,
	"summary": statesText, "detail": statesText, "details": statesText, "primary_details": statesText,
	"secondary_details": statesText, "end_reason": statesText, "reason": statesText,
	// Who.
	"target_user_id": statesWho, "to_user_id": statesWho, "partner_user_id": statesWho, "master_user_id": statesWho,
	"guest_user_id": statesWho, "invitee_user_id": statesWho, "owner_user_id": statesWho, "user_id": statesWho,
	// The wire.
	"version": statesWire, "match_version": statesWire,
	// The scene.
	"physical_location": statesScene, "scene_type": statesScene, "scene_label": statesScene,
	"channel_id": statesScene, "metadata": statesScene,
	// Authored quest content.
	"title": statesAuthored, "description": statesAuthored, "objectives": statesAuthored, "rewards": statesAuthored,
	"variants": statesAuthored, "giver_npc": statesAuthored, "realm_band": statesAuthored, "tier": statesAuthored,
	"deadline_game_minutes": statesAuthored, "requires_sect": statesAuthored, "reward_visibility": statesAuthored,
	"boast": statesAuthored,
	// The Python-era levers (relationship.update, cultivation.reward,
	// combat.apply_damage) stated trust, cultivation, stones and damage here
	// until v1.37.0, when they became GM levers reading a decoded map like
	// every other admin.player.* lever, and /talk's trust became the engine's
	// rule (npc.talk, whose fields are a name and a text).
}

// Fields kept on the wire for an older bot and read by nothing, keyed
// "Struct.field"; the gate proves the "read by nothing" half.
var callerIgnored = map[string]string{
	"combatStartPayload.npc_realm_index": "an older bot's opponent realm; the engine reads the opponent off the world (resolveChallengeTargetTx, v1.33.0)",
	"combatStartPayload.npc_stage":       "an older bot's opponent stage; read off the world with the realm",
	"combatStartPayload.severity":        "an older bot's event severity; the engine reads the event's own (v1.35.0)",
	"combatStartPayload.target_key":      "an older bot's battle lock; the engine spells the lock itself from what it resolved",
}

// The one field of callerOwnedNothing a struct may carry: the canonicalizer
// refuses the caller's and writes the engine's own into the payload before
// the handler reads it (stage5CanonicalizeMutationPayload).
var engineInjected = map[string]bool{"game_minute": true}

// A handler that reads its payload as a map rather than a struct, keyed by
// function name, with why a struct would not do.
var rawMapReaders = map[string]string{
	"rejectCallerGameMinute":            "the refusal itself: it reads the raw keys to find one of callerOwnedNothing and refuse the request",
	"stage5CanonicalizeMutationPayload": "the canonicalizer: it reads the raw keys to write the engine's game_minute over whatever came",
	"craftResolveAction":                "reads the raw keys beside its struct to refuse a caller-owned field by presence (v1.0.x: absent, never falsy); the struct is what this gate reads",
	"forageResolveAction":               "reads the raw keys to refuse a caller-owned field by presence; the forage has no other payload",
	"familyAddChildActionGo":            "reads the raw keys beside its struct to refuse a caller-owned field by presence; the struct is what this gate reads",
	"validateCompanionPayload":          "reads the raw keys to refuse a caller-owned field by presence, for every beast and artifact action",
	"createCharacterAuthoritative":      "reads the raw keys beside its struct to refuse a client-owned field by presence; the struct is what this gate reads",
	"seclusionStartActionGo":            "probes the raw keys beside its struct to tell an old client's game-minute length from a new one's real-minute length; the struct is what this gate reads",
	"adminTradeVoid":                    "a GM lever, audited: it reads the offer to void and the reason, and GM levers state what they state",
}

type payloadField struct {
	Struct, Field, Tag string
}

// payloadStructs are the struct types the engine unmarshals a request payload
// into: in every function with a json.RawMessage parameter, the target of
// json.Unmarshal(<that parameter>, &x), resolved through `var x T`.
func payloadStructs(t *testing.T, files map[string]*ast.File) (map[string]bool, map[string]*ast.StructType, map[string]string) {
	t.Helper()
	structs := map[string]bool{}
	inline := map[string]*ast.StructType{}
	maps := map[string]string{}
	for _, file := range files {
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil || fn.Type.Params == nil {
				continue
			}
			rawParams := map[string]bool{}
			for _, param := range fn.Type.Params.List {
				if sel, ok := param.Type.(*ast.SelectorExpr); ok && sel.Sel.Name == "RawMessage" {
					for _, name := range param.Names {
						rawParams[name.Name] = true
					}
				}
			}
			if len(rawParams) == 0 {
				continue
			}
			// Every `var x T` in the body, by position: a handler that serves
			// two operations declares `var p` twice in two branches, and the
			// unmarshal in each branch resolves to the declaration nearest
			// above it, not to the last one in the file.
			type varDecl struct {
				name string
				pos  token.Pos
				typ  ast.Expr
			}
			var decls []varDecl
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if gen, ok := n.(*ast.GenDecl); ok && gen.Tok == token.VAR {
					for _, spec := range gen.Specs {
						if vs, ok := spec.(*ast.ValueSpec); ok && vs.Type != nil {
							for _, name := range vs.Names {
								decls = append(decls, varDecl{name.Name, name.Pos(), vs.Type})
							}
						}
					}
				}
				return true
			})
			declaredBefore := func(name string, at token.Pos) (ast.Expr, bool) {
				var best *varDecl
				for i := range decls {
					d := &decls[i]
					if d.name == name && d.pos < at && (best == nil || d.pos > best.pos) {
						best = d
					}
				}
				if best == nil {
					return nil, false
				}
				return best.typ, true
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok || len(call.Args) != 2 {
					return true
				}
				sel, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "Unmarshal" {
					return true
				}
				if pkg, ok := sel.X.(*ast.Ident); !ok || pkg.Name != "json" {
					return true
				}
				src, ok := call.Args[0].(*ast.Ident)
				if !ok || !rawParams[src.Name] {
					return true
				}
				addr, ok := call.Args[1].(*ast.UnaryExpr)
				if !ok || addr.Op != token.AND {
					return true
				}
				target, ok := addr.X.(*ast.Ident)
				if !ok {
					return true
				}
				typ, ok := declaredBefore(target.Name, target.Pos())
				if !ok {
					maps[fn.Name.Name] = "an unmarshal into something not declared with var"
					return true
				}
				switch typed := typ.(type) {
				case *ast.Ident:
					structs[typed.Name] = true
				case *ast.StructType:
					// `var p struct{...}` inside the handler: its fields are
					// read under the handler's own name.
					inline[fn.Name.Name+"."+target.Name] = typed
				default:
					maps[fn.Name.Name] = fmt.Sprintf("%T", typ)
				}
				return true
			})
		}
	}
	return structs, inline, maps
}

func structFields(t *testing.T, name string, st *ast.StructType) []payloadField {
	t.Helper()
	var out []payloadField
	for _, field := range st.Fields.List {
		if field.Tag == nil || len(field.Names) == 0 {
			continue
		}
		raw, err := strconv.Unquote(field.Tag.Value)
		if err != nil {
			t.Fatalf("%s.%s: tag %s: %v", name, field.Names[0].Name, field.Tag.Value, err)
		}
		tag := strings.Split(reflect.StructTag(raw).Get("json"), ",")[0]
		if tag == "" || tag == "-" {
			continue
		}
		out = append(out, payloadField{name, field.Names[0].Name, tag})
	}
	return out
}

func payloadFieldsOf(t *testing.T, files map[string]*ast.File, structs map[string]bool, inline map[string]*ast.StructType) []payloadField {
	t.Helper()
	var out []payloadField
	for name, st := range inline {
		out = append(out, structFields(t, name, st)...)
	}
	for _, file := range files {
		for _, decl := range file.Decls {
			gen, ok := decl.(*ast.GenDecl)
			if !ok || gen.Tok != token.TYPE {
				continue
			}
			for _, spec := range gen.Specs {
				ts, ok := spec.(*ast.TypeSpec)
				if !ok || !structs[ts.Name.Name] {
					continue
				}
				st, ok := ts.Type.(*ast.StructType)
				if !ok {
					t.Fatalf("%s is unmarshalled into and is not a struct", ts.Name.Name)
				}
				out = append(out, structFields(t, ts.Name.Name, st)...)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Struct != out[j].Struct {
			return out[i].Struct < out[j].Struct
		}
		return out[i].Tag < out[j].Tag
	})
	return out
}

// fieldIsRead reports whether any function reads `<x>.<field>` on a value it
// holds of the struct's type - a parameter, or a `var x T` - in any non-test
// file of the package.
func fieldIsRead(files map[string]*ast.File, structName, field string) bool {
	for _, file := range files {
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			holders := map[string]bool{}
			if fn.Type.Params != nil {
				for _, param := range fn.Type.Params.List {
					if ident, ok := param.Type.(*ast.Ident); ok && ident.Name == structName {
						for _, name := range param.Names {
							holders[name.Name] = true
						}
					}
				}
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if gen, ok := n.(*ast.GenDecl); ok && gen.Tok == token.VAR {
					for _, spec := range gen.Specs {
						if vs, ok := spec.(*ast.ValueSpec); ok {
							if ident, ok := vs.Type.(*ast.Ident); ok && ident.Name == structName {
								for _, name := range vs.Names {
									holders[name.Name] = true
								}
							}
						}
					}
				}
				return true
			})
			if len(holders) == 0 {
				continue
			}
			read := false
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if sel, ok := n.(*ast.SelectorExpr); ok && sel.Sel.Name == field {
					if x, ok := sel.X.(*ast.Ident); ok && holders[x.Name] {
						read = true
					}
				}
				return !read
			})
			if read {
				return true
			}
		}
	}
	return false
}

func packageFiles(t *testing.T) map[string]*ast.File {
	t.Helper()
	paths, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	fset := token.NewFileSet()
	files := map[string]*ast.File{}
	for _, path := range paths {
		if strings.HasSuffix(path, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		files[path] = file
	}
	if len(files) < 50 {
		t.Fatalf("read %d production files; the reader is broken, not the tree", len(files))
	}
	return files
}

func TestEveryPayloadFieldIsTheCallersToState(t *testing.T) {
	files := packageFiles(t)
	structs, inline, maps := payloadStructs(t, files)
	if len(structs) < 100 {
		t.Fatalf("found %d payload structs; the reader is broken, not the tree", len(structs))
	}
	fields := payloadFieldsOf(t, files, structs, inline)
	if len(fields) < 200 {
		t.Fatalf("found %d payload fields; the reader is broken, not the tree", len(fields))
	}
	forbidden := map[string]bool{}
	for _, field := range callerOwnedNothing {
		forbidden[field] = true
	}
	var faults []string
	used := map[string]bool{}
	for _, f := range fields {
		key := f.Struct + "." + f.Tag
		if forbidden[f.Tag] && !engineInjected[f.Tag] {
			faults = append(faults, fmt.Sprintf("%s carries %q, a field callerOwnedNothing refuses: the engine derives it, so the struct must not read it", f.Struct, f.Tag))
			continue
		}
		if engineInjected[f.Tag] {
			continue
		}
		if reason, ok := callerIgnored[key]; ok {
			used[key] = true
			if len(strings.TrimSpace(reason)) < 20 {
				faults = append(faults, fmt.Sprintf("%s is ignored without a reason", key))
			}
			if fieldIsRead(files, f.Struct, f.Field) {
				faults = append(faults, fmt.Sprintf("%s is listed as ignored and a handler reads %s.%s", key, f.Struct, f.Field))
			}
			continue
		}
		if reason, ok := callerStates[f.Tag]; ok {
			used[f.Tag] = true
			if len(strings.TrimSpace(reason)) < 20 {
				faults = append(faults, fmt.Sprintf("%q is stated without a reason", f.Tag))
			}
			continue
		}
		faults = append(faults, fmt.Sprintf("%s carries %q, which no list says a caller may state: a thing the caller names, chooses or offers goes in callerStates; a wire leftover nothing reads goes in callerIgnored; an outcome or a bound goes in callerOwnedNothing and the engine derives it", f.Struct, f.Tag))
	}
	for tag := range callerStates {
		if !used[tag] {
			faults = append(faults, fmt.Sprintf("callerStates names %q and no payload struct carries it: stale", tag))
		}
	}
	for key := range callerIgnored {
		if !used[key] {
			faults = append(faults, fmt.Sprintf("callerIgnored names %q and no payload struct carries it: stale", key))
		}
	}
	for fn, what := range maps {
		if reason, ok := rawMapReaders[fn]; !ok || len(strings.TrimSpace(reason)) < 20 {
			faults = append(faults, fmt.Sprintf("%s unmarshals its payload into %s rather than a struct; name it in rawMapReaders with why, or give it a struct this gate can read", fn, what))
		}
	}
	for fn := range rawMapReaders {
		if _, ok := maps[fn]; !ok {
			faults = append(faults, fmt.Sprintf("rawMapReaders names %s, which reads no map: stale", fn))
		}
	}
	sort.Strings(faults)
	if len(faults) > 0 {
		t.Fatalf("%d payload field(s) are not accounted for:\n  %s", len(faults), strings.Join(faults, "\n  "))
	}
}
