package worlddata

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"
)

type Path struct {
	Body     int    `json:"body"`
	Agility  int    `json:"agility"`
	Spirit   int    `json:"spirit"`
	Insight  int    `json:"insight"`
	Will     int    `json:"will"`
	Presence int    `json:"presence"`
	Skill    string `json:"skill"`
}

type RootGrade struct {
	Name              string  `json:"name"`
	MinRoll           int     `json:"min_roll"`
	CultivationMult   float64 `json:"cultivation_mult"`
	BreakthroughBonus int     `json:"breakthrough_bonus"`
	SecondaryChance   int     `json:"secondary_chance"`
	TertiaryChance    int     `json:"tertiary_chance"`
	MutationChance    int     `json:"mutation_chance"`
	MinRealmToEvolve  int     `json:"min_realm_to_evolve"`
}

type Modifier struct {
	Stat      string  `json:"stat"`
	Operation string  `json:"operation"`
	Value     float64 `json:"value"`
}

type RootMutation struct {
	Name         string     `json:"name"`
	RequiresAny  []string   `json:"requires_any"`
	FavoredPaths []string   `json:"favored_paths"`
	Modifiers    []Modifier `json:"modifiers"`
}

type RootSystem struct {
	Elements       []string                `json:"elements"`
	Grades         []RootGrade             `json:"grades"`
	Mutations      map[string]RootMutation `json:"mutations"`
	PathAffinities map[string][]string     `json:"path_affinities"`
	// PurityBonusAtFull (v1.0.0-rc.55) is what a perfectly pure root adds to
	// the grade's own multiplier. It lived under `elemental_qi_system` until
	// rc.55, which is a system about the five phases and what a method's qi
	// is to a root - purity is neither. It is the root's, so it is here.
	PurityBonusAtFull float64 `json:"purity_bonus_at_full"`
}

type Evolution struct {
	Name         string     `json:"name"`
	MinPurity    int        `json:"min_purity"`
	MinRealm     int        `json:"min_realm"`
	MinBodyRealm int        `json:"min_body_realm"`
	MinStability int        `json:"min_stability"`
	Modifiers    []Modifier `json:"modifiers"`
}

type AncestralTechnique struct {
	Name      string `json:"name"`
	Stage     int    `json:"stage"`
	MinPurity int    `json:"min_purity"`
}

type BloodlineDefinition struct {
	Name                string               `json:"name"`
	Affinity            string               `json:"affinity"`
	Trait               string               `json:"trait"`
	AwakeningMinPurity  int                  `json:"awakening_min_purity"`
	AwakeningMinRealm   int                  `json:"awakening_min_realm"`
	Evolutions          []Evolution          `json:"evolutions"`
	AncestralTechniques []AncestralTechnique `json:"ancestral_techniques"`
}

type PhysiqueDefinition struct {
	Name                  string      `json:"name"`
	Advantage             string      `json:"advantage"`
	Drawback              string      `json:"drawback"`
	DrawbackModifiers     []Modifier  `json:"drawback_modifiers"`
	AwakeningMinBodyRealm int         `json:"awakening_min_body_realm"`
	FavoredPaths          []string    `json:"favored_paths"`
	FavoredRoots          []string    `json:"favored_roots"`
	Evolutions            []Evolution `json:"evolutions"`
}

type Realm struct {
	World      string  `json:"world"`
	Name       string  `json:"name"`
	PhaseCosts []int64 `json:"phase_costs"`
	BaseTN     int64   `json:"base_tn"`
}

type PerfectionQuest struct {
	Title               string `json:"title"`
	Description         string `json:"description"`
	PreparationRequired int64  `json:"preparation_required"`
	Attribute           string `json:"attribute"`
	TN                  int64  `json:"tn"`
	Clue                string `json:"clue"`
}
type PerfectionTrial struct {
	Name      string `json:"name"`
	Attribute string `json:"attribute"`
	TN        int64  `json:"tn"`
}
type PerfectionSystem struct {
	TrainingCap   int               `json:"training_cap"`
	QuestProgress []int64           `json:"quest_progress"`
	Quests        []PerfectionQuest `json:"quests"`
	FinalTrials   []PerfectionTrial `json:"final_trials"`
}

type ItemInstantUse struct {
	QiRestore       int64 `json:"qi_restore"`
	VitalityRestore int64 `json:"vitality_restore"`
}
type ItemUse struct {
	Instant ItemInstantUse `json:"instant"`
	// v0.21.0 (item.use): the non-battle use fields Python used to read for
	// itself. Effect is the raw effect definition (modifiers, tags, ...);
	// EffectKey/Name label the active_effects row; DurationGameMinutes 0
	// means the effect does not expire; LifespanYears is a permanent gain.
	Effect              map[string]any `json:"effect"`
	EffectKey           string         `json:"effect_key"`
	Name                string         `json:"name"`
	DurationGameMinutes int64          `json:"duration_game_minutes"`
	LifespanYears       int64          `json:"lifespan_years"`
	// Homeward (v1.0.0-rc.32) is the Hearth-Return Talisman: using it puts
	// the character inside their birth household from anywhere and marks
	// where it found them. Waymark is the other half, the Waymark Talisman:
	// read inside the household, it returns them to that mark.
	Homeward bool `json:"homeward"`
	Waymark  bool `json:"waymark"`
}
type Item struct {
	Name string  `json:"name"`
	Use  ItemUse `json:"use"`
	// PillToxicity overrides the derived medicinal-residue value when the
	// content sets it (nil = derive from tags/use, as app/rules/alchemy.py).
	PillToxicity     *int64         `json:"pill_toxicity"`
	SectValue        int64          `json:"sect_value"`
	BasePrice        int64          `json:"base_price"`
	LegalStatus      string         `json:"legal_status"`
	AuctionInterest  string         `json:"auction_interest"`
	DoorEventChance  int64          `json:"door_event_chance"`
	HunterRealmBonus int64          `json:"hunter_realm_bonus"`
	StorageUpgrade   map[string]any `json:"storage_upgrade"`
	// Flight (v1.0.0-rc.15) is the realm a rider effectively travels at while
	// carrying this, which is how a cultivator who cannot yet fly gets off the
	// road: the artifact does the flying. FlightName is what the reply calls
	// it - a flying sword, a paper crane, a folded step.
	Flight         int64          `json:"flight"`
	FlightName     string         `json:"flight_name"`
	ArrayDeploy    string         `json:"array_deploy"`
	SpatialKey     map[string]any `json:"spatial_key"`
	MarketExcluded bool           `json:"market_excluded"`
	// TeachesRecipe (v1.0.0-rc.20) is the method a jade slip carries. Reading
	// one is how every recipe a household did not teach is learned. Declared
	// last on purpose: a comment inside the struct starts a new gofmt
	// alignment block and silently re-indents every field below it, which
	// `test_flight_travel.py` pins by exact text.
	TeachesRecipe string `json:"teaches_recipe"`
}

// MarketTradeable is the one rule for whether an item belongs in ordinary
// regional markets (v0.30.0). Secret-realm keys and auction-interest
// special/legendary items are progression rewards, auction lots or event
// objects rather than infinite shop stock, and an explicit market_excluded
// content flag removes anything else. The simulation bootstrap and the
// market.catalog query both ask here; Python no longer holds a copy.
func MarketTradeable(marketExcluded, hasSpatialKey bool, auctionInterest string) bool {
	if marketExcluded || hasSpatialKey {
		return false
	}
	interest := strings.ToLower(strings.TrimSpace(auctionInterest))
	return interest != "special" && interest != "legendary"
}

// MarketTradeable applies the catalog rule to this item.
func (i Item) MarketTradeable() bool {
	return MarketTradeable(i.MarketExcluded, i.SpatialKey != nil, i.AuctionInterest)
}

type AuctionHouse struct {
	Name              string `json:"name"`
	Location          string `json:"location"`
	EntranceLocation  string `json:"entrance_location"`
	ProtectedInterior bool   `json:"protected_interior"`
	DefaultCurrency   string `json:"default_currency"`
	Description       string `json:"description"`
	// Size (v0.33.1): "grand" for a capital's house, "local" for an ordinary
	// city's. A local floor holds fewer lots at once and none for as long;
	// zero means no cap, which is what content without the fields gets.
	Size          string `json:"size"`
	MaxActiveLots int64  `json:"max_active_lots"`
	MaxLotMinutes int64  `json:"max_lot_minutes"`
	ChannelName   string `json:"channel_name"`
}

// CurrencyDefinition is one of the sixteen currencies: four tiers in each of
// the four worlds. Only `name` was parsed until v1.0.0-rc.43, so the file's own
// statement of which world a currency belongs to - and what a tier is worth -
// was dropped on the floor by the parser and read by nothing.
//
// `BaseRatio` is what one unit is worth in that world's tier-1 currency (100,
// 10,000, 1,000,000; tier 1 itself carries none). Nothing spends it yet: an
// exchange between tiers is a mechanic, not a parse, and it is deliberately not
// built here. What the ratio does do is get held to its shape by
// `TestTheStoneLadderIsWholeInEveryWorld`, so the day somebody builds the
// counter the content underneath it is already true.
type CurrencyDefinition struct {
	Name      string `json:"name"`
	World     string `json:"world"`
	Tier      int64  `json:"tier"`
	BaseRatio int64  `json:"base_ratio"`
}

// ProfessionExam (v1.0.0-rc.45) is one rank's examination in one trade. The
// hall that sells a trade's method slips is the hall that certifies it, so
// `HallKind` names a shop kind (`weaponsmith`, `apothecary`, `talisman`,
// `array`) and the examiner is that shop's own `keeper` - a catalogue NPC who
// already stands there, rather than somebody invented for the occasion.
//
// Nothing here gates a level: `advanceProfessionTx` goes on raising a rank on
// XP alone. What the examination is worth is the trade's recipes at that rank,
// which are otherwise only bought a slip at a time.
type ProfessionExam struct {
	Rank        int64          `json:"rank"`
	RankName    string         `json:"rank_name"`
	QuestKey    string         `json:"quest_key"`
	Title       string         `json:"title"`
	Description string         `json:"description"`
	Opening     string         `json:"opening"`
	HallKind    string         `json:"hall_kind"`
	Hall        string         `json:"hall"`
	TN          int64          `json:"tn"`
	Fee         int64          `json:"fee"`
	Objectives  []any          `json:"objectives"`
	Rewards     map[string]any `json:"rewards"`
}

// WorldCrossingSystem (v1.0.0-rc.44) is what a survived world-crossing
// tribulation leaves behind: the seam it tore, which a cultivator may anchor
// into a permanent crossing standing where the lightning fell rather than in
// the one capital the authored arrays depart from.
//
// Everything about it is content. `Quests` is keyed by the world the gate
// leads *out of*, so the quest a cleared tribulation hands over is the one
// authored for that crossing; the engine reads only the key, because the
// definition itself is seeded from this file the way the beginner path is.
type WorldCrossingSystem struct {
	Description            string                   `json:"description"`
	NameTemplate           string                   `json:"name_template"`
	RaiseCostMultiplier    int64                    `json:"raise_cost_multiplier"`
	NPCCrossingChance      int64                    `json:"npc_crossing_chance_percent"`
	NPCCrossingRealmReach  int64                    `json:"npc_crossing_realm_reach"`
	HistorySignificance    int64                    `json:"history_significance"`
	NPCHistorySignificance int64                    `json:"npc_history_significance"`
	Quests                 map[string]CrossingQuest `json:"quests"`
}

// CrossingQuest is one authored ascension quest. Only the key is read here.
type CrossingQuest struct {
	QuestKey string `json:"quest_key"`
	Title    string `json:"title"`
}

type TeleportArray struct {
	Name          string `json:"name"`
	From          string `json:"from"`
	To            string `json:"to"`
	Currency      string `json:"currency"`
	Cost          int64  `json:"cost"`
	MinRealmIndex int64  `json:"min_realm_index"`
}

type Recipe struct {
	Profession string           `json:"profession"`
	TN         int64            `json:"tn"`
	Cost       map[string]int64 `json:"cost"`
	Output     map[string]int64 `json:"output"`
	// MinLevel is the profession level the method asks for (v1.0.0-rc.20).
	// Zero means common knowledge: every profession keeps at least one entry
	// recipe anyone can make, so a new cultivator is never locked out of their
	// own craft. Anything above zero must be *learned* as well - the two halves
	// are deliberately separate, because knowing a method and being good enough
	// to use it are different things.
	MinLevel int64 `json:"min_level"`
}

// Learns is the recipe a method slip carries, or "" for an ordinary item.
func (i Item) Learns() string { return strings.TrimSpace(i.TeachesRecipe) }

type LawStage struct {
	Index int    `json:"index"`
	Name  string `json:"name"`
	Min   int    `json:"min"`
}
type LawTechnique struct {
	Name          string `json:"name"`
	Law           string `json:"law"`
	RequiresStage int    `json:"requires_stage"`
	MinRealmIndex int    `json:"min_realm_index"`
	Effect        string `json:"effect"`
}
type LawDefinition struct {
	Name          string   `json:"name"`
	Dao           string   `json:"dao"`
	Category      string   `json:"category"`
	Supreme       bool     `json:"supreme"`
	AffinityRoots []string `json:"affinity_roots"`
	AffinityPaths []string `json:"affinity_paths"`
}
type LawSystem struct {
	Stages                    []LawStage               `json:"stages"`
	Laws                      map[string]LawDefinition `json:"laws"`
	Techniques                map[string]LawTechnique  `json:"techniques"`
	ComprehendCooldownMinutes int64                    `json:"comprehend_cooldown_minutes"`
	NormalMinRealmIndex       int64                    `json:"normal_min_realm_index"`
	SupremeMinRealmIndex      int64                    `json:"supreme_min_realm_index"`
}

// EraTemplate is one age of one world: how long it lasts and what it does
// while it does. `Modifiers` is deliberately a bare map rather than named
// fields - the vocabulary is held by `era_modifier_vocabulary_test.go`, which
// requires every key to be *fetched* by a rule, and a struct would instead
// silently drop a key no field matched (the "silent when wrong" widening
// schema 51 refused for the content tables).
type EraTemplate struct {
	Name         string             `json:"name"`
	Description  string             `json:"description"`
	DurationDays int64              `json:"duration_days"`
	Modifiers    map[string]float64 `json:"modifiers"`
}

type LocationDefinition struct {
	Description     string   `json:"description"`
	Encounters      []string `json:"encounters"`
	SenseHints      []string `json:"sense_hints"`
	World           string   `json:"world"`
	MinRealmIndex   int64    `json:"min_realm_index"`
	SafeZone        bool     `json:"safe_zone"`
	Private         bool     `json:"private"`
	RealmHub        bool     `json:"realm_hub"`
	AuctionHouse    string   `json:"auction_house"`
	OutsideLocation string   `json:"outside_location"`
	// Shop (v0.35.0) names the city shop this location is the inside of;
	// OutsideLocation is the city its door opens onto.
	Shop string `json:"shop"`
	// District (v0.36.0) marks a part of a city - a gate, a quarter, the
	// forge terraces - with OutsideLocation naming the city. Gate is the
	// compass direction of a gate district. Gates, on the city itself, maps
	// each compass direction to the road neighbours that side faces.
	District       string              `json:"district"`
	Gate           string              `json:"gate"`
	Gates          map[string][]string `json:"gates"`
	Climate        string              `json:"climate"`
	Terrain        string              `json:"terrain"`
	SettlementType string              `json:"settlement_type"`
	Roads          []string            `json:"roads"`
	// RoadSite (v0.39.0) marks a place by the side of a road - a
	// "waystation", a "hunting_ground", a "ruin" or a "shrine" - and RoadLeg
	// names the two cities whose road it lies on. A site is reached from
	// either end of its leg and the road leads on from it to either.
	RoadSite string   `json:"road_site"`
	RoadLeg  []string `json:"road_leg"`
}

type UnexpectedEvent struct {
	ID              string         `json:"id"`
	Title           string         `json:"title"`
	Category        string         `json:"category"`
	Kind            string         `json:"kind"`
	Weight          int            `json:"weight"`
	DurationHours   int64          `json:"duration_hours"`
	Severity        int64          `json:"severity"`
	Description     string         `json:"description"`
	ConsequenceText string         `json:"consequence_text"`
	Locations       []string       `json:"locations"`
	Worlds          []string       `json:"worlds"`
	MinRealmIndex   int64          `json:"min_realm_index"`
	MaxRealmIndex   *int64         `json:"max_realm_index"`
	SecretRealmID   string         `json:"secret_realm_id"`
	PlayerReward    map[string]any `json:"player_reward"`
	PlayerEffect    map[string]any `json:"player_effect"`
	KarmaDelta      int64          `json:"karma_delta"`
	FateDelta       int64          `json:"fate_delta"`
	WorldEffect     map[string]any `json:"world_effect"`
}

// EventSiteNode is one concrete thing inside a world event: a beast to fight,
// an herb or ore node to harvest, a relic to recover, or a task to carry out.
// Count is a [min,max] pair scaled by event severity when the site is spawned,
// and Item may be the symbolic "@herb"/"@ore"/"@core", resolved against the
// world tier the event landed in so one template stays correct in every world.
type EventSiteNode struct {
	Key          string  `json:"key"`
	Type         string  `json:"type"`
	Name         string  `json:"name"`
	Descriptor   string  `json:"descriptor"`
	Count        []int64 `json:"count"`
	RankBonus    int64   `json:"rank_bonus"`
	TN           int64   `json:"tn"`
	Attribute    string  `json:"attribute"`
	Item         string  `json:"item"`
	ItemQty      int64   `json:"item_qty"`
	Cultivation  int64   `json:"cultivation"`
	SpiritStones int64   `json:"spirit_stones"`
	Contribution int64   `json:"contribution"`
}

// EventSiteNPC is one of the people an event needs: someone to report to,
// someone to ask, someone whose problem this is. Title is the role name and
// the given name is drawn from the shared pool at spawn, so two concurrent
// beast tides do not both field the same captain.
type EventSiteNPC struct {
	Key         string `json:"key"`
	Title       string `json:"title"`
	Role        string `json:"role"`
	Personality string `json:"personality"`
	Speech      string `json:"speech"`
	Want        string `json:"want"`
	Fear        string `json:"fear"`
	Descriptor  string `json:"descriptor"`
}

// EventSiteTemplate is the roster one event category spawns.
type EventSiteTemplate struct {
	Objective string          `json:"objective"`
	Nodes     []EventSiteNode `json:"nodes"`
	NPCs      []EventSiteNPC  `json:"npcs"`
}

// PatronGift decides which tier material a cultivator's out-of-world gift
// arrives as (support.vote_claim). The mapping is content rather than a Go
// switch so a new path or profession is a content edit, and the material ref
// it returns ("@herb"/"@ore"/"@core") is resolved against the world tier by
// EventSites.Material, which is where every other tier material comes from.
type PatronGift struct {
	ByProfession map[string]string `json:"by_profession"`
	ByPath       map[string]string `json:"by_path"`
	Default      string            `json:"default"`
	// Refs the tier table does not actually tier. "@herb" and "@ore" name a
	// different, richer item in each world; "@core" is beast_core in all four
	// deliberately - it is the one material every world's recipes and shops
	// still trade in, so it cannot be split per tier without rewriting them.
	// A gift of an untiered material is made worth the same by arriving in
	// greater number instead. See Untiered.
	UntieredRefs []string `json:"untiered"`
}

// Untiered reports whether a material ref keeps the same item in every world,
// and so has to be scaled by quantity rather than by identity.
func (p PatronGift) Untiered(ref string) bool {
	for _, candidate := range p.UntieredRefs {
		if candidate == ref {
			return true
		}
	}
	return false
}

// Material returns the material ref for a cultivator. A profession they have
// actually practised wins - a smith is given ore whatever they cultivate -
// and the path is what answers for everyone else. Professions are offered
// most-practised first by the caller; the first one named here wins.
func (p PatronGift) Material(professions []string, path string) string {
	for _, profession := range professions {
		if ref, ok := p.ByProfession[profession]; ok && ref != "" {
			return ref
		}
	}
	if ref, ok := p.ByPath[path]; ok && ref != "" {
		return ref
	}
	return p.Default
}

// EventSites turns an event category into the concrete roster players can act
// on. Without it a world event is an announcement with nothing inside it.
type EventSites struct {
	TierMaterials map[string]map[string]string `json:"tier_materials"`
	TierRank      map[string]int64             `json:"tier_rank"`
	NamePool      []string                     `json:"name_pool"`
	Default       EventSiteTemplate            `json:"default"`
	Categories    map[string]EventSiteTemplate `json:"categories"`
}

// Template returns the roster for a category, falling back to the default one
// so an event category nobody wrote a site for is still not empty.
func (e EventSites) Template(category string) EventSiteTemplate {
	if tpl, ok := e.Categories[category]; ok && (len(tpl.Nodes) > 0 || len(tpl.NPCs) > 0) {
		return tpl
	}
	return e.Default
}

// Material resolves "@herb"/"@ore"/"@core" against a world tier. Anything else
// is already a literal item id and is returned unchanged.
func (e EventSites) Material(world, ref string) string {
	if len(ref) == 0 || ref[0] != '@' {
		return ref
	}
	if tier, ok := e.TierMaterials[world]; ok {
		if id, ok := tier[ref[1:]]; ok {
			return id
		}
	}
	return ""
}

type SecretRealmRoom struct {
	Name           string           `json:"name"`
	Description    string           `json:"description"`
	Attribute      string           `json:"attribute"`
	TN             int64            `json:"tn"`
	PreferredPaths []string         `json:"preferred_paths"`
	PreferredRoots []string         `json:"preferred_roots"`
	Cultivation    int64            `json:"cultivation"`
	SpiritStones   int64            `json:"spirit_stones"`
	InsightXP      int64            `json:"insight_xp"`
	Items          map[string]int64 `json:"items"`
	// RareItems is what the room *might* hold, as opposed to what it holds
	// (v1.0.0-rc.50). A realm's rooms are walked again on every run -
	// `secret_realm_runs` keeps one row per user and resets `room_index` to 0
	// on entry - so anything in `Items` is a guaranteed repeat payout, which
	// is right for two spirit herbs and wrong for a thing the world should
	// have few of. The shape is `ForageMaterial`'s, minus the richness floor
	// a realm has no equivalent of, so the tree has one idea of what a find
	// chance looks like.
	RareItems map[string]RareFind `json:"rare_items"`
}

// RareFind is a chance in a hundred to find something, and the most one find
// can be. It is the roster shape `ForageMaterial` already uses; a zero or
// negative Chance or Max means the entry is simply never found, which is how
// content disables one without deleting it.
type RareFind struct {
	Chance int64 `json:"chance"`
	Max    int64 `json:"max"`
}

type SecretRealm struct {
	Name          string            `json:"name"`
	Location      string            `json:"location"`
	MinRealmIndex int64             `json:"min_realm_index"`
	OpenHours     int64             `json:"open_hours"`
	InheritanceID string            `json:"inheritance_id"`
	Description   string            `json:"description"`
	Rooms         []SecretRealmRoom `json:"rooms"`
}

// BeginnerStage is one leg of the path a new cultivator is put on the moment
// they are made (v1.0.0-rc.26). The send-off above is what the household puts
// in their hands; this is what it expects them to do with it.
//
// The engine reads one field of it - which quest to hand over first - because
// that is the only part that is a mechanic. The prose, the objectives and the
// rewards are seeded into `quest_definitions` from here by the same path the
// authored commission pool takes, and from then on a stage is an ordinary
// quest like any other: the same terms pinning, the same progress, the same
// payment inside the transaction that completes it. Nothing about a beginner
// quest is a special case once it has been handed over, which is the point -
// a second mechanism would be a second thing to keep correct.
type BeginnerStage struct {
	QuestKey string `json:"quest_key"`
	Title    string `json:"title"`
	// FollowOn is carried here for the content to read as one document. The
	// engine does not use it: chaining reads `seed_json` off the definition
	// row, so a GM who edits the chain in the dashboard is obeyed and the
	// shipped file is only ever the starting shape.
	FollowOn string `json:"follow_on"`
}

// HouseholdErrand is one thing a birth household asks of its own child
// (v1.0.0-rc.32), keyed by the trade the house teaches. Like a beginner
// stage it is an ordinary quest definition seeded from the content; the
// engine reads only its key, its title and its opening.
type HouseholdErrand struct {
	QuestKey string `json:"quest_key"`
	Title    string `json:"title"`
	Opening  string `json:"opening"`
}

// BirthFamilyLesson is the head of the house's last lesson and test
// (v1.0.0-rc.34), keyed by archetype: the family's own manual (realm 0, never
// forbidden - a child's first lesson costs no karma), the keepsake left in the
// child's hands, and the five things the head says - the lesson, what the test
// demands, the pass, the fail, and the story of the house.
type BirthFamilyLesson struct {
	Manual   string `json:"manual"`
	Keepsake string `json:"keepsake"`
	Lesson   string `json:"lesson"`
	Test     string `json:"test"`
	Pass     string `json:"pass"`
	Fail     string `json:"fail"`
	Story    string `json:"story"`
}

// BirthFamilySendoff is what a household puts in a child's hands on the day
// they leave it (v1.0.0-rc.15). Every archetype has one and no two share an
// item: which flying artifact a family owns *is* the family - a tomb-watch
// clan folds a burnt offering that will carry the living too, a weapon-smith's
// child leaves on the blade they proved on the anvil, and a fallen clan has
// only the cracked ancestral sword nobody would buy.
type BirthFamilySendoff struct {
	Item string `json:"item"`
	Line string `json:"line"`
	// Trade is the craft the household passes on (v1.0.0-rc.20): the one whose
	// methods a child is taught before they leave. Read off what the archetype
	// already is - a weapon-smith's family forges, a tomb-watch clan inscribes -
	// and kept in content so it is tunable without an engine change.
	Trade string `json:"trade"`
}

type Inheritance struct {
	Name           string           `json:"name"`
	Description    string           `json:"description"`
	PreferredPaths []string         `json:"preferred_paths"`
	Bonuses        map[string]int64 `json:"bonuses"`
	Item           string           `json:"item"`
}
type HiddenMasterDefinition struct {
	Kind                string `json:"kind"`
	TrueRealmIndex      int64  `json:"true_realm_index"`
	TrueStage           int64  `json:"true_stage"`
	Concealment         int64  `json:"concealment"`
	Deception           int64  `json:"deception"`
	ProjectedRealmIndex *int64 `json:"projected_realm_index"`
	ProjectedStage      int64  `json:"projected_stage"`
	SenseFalseReading   string `json:"sense_false_reading"`
	FraudItem           string `json:"fraud_item"`
}
type NPCDefinition struct {
	Realm        string                  `json:"realm"`
	Location     string                  `json:"location"`
	HiddenMaster *HiddenMasterDefinition `json:"hidden_master"`
	// Merchant (v0.34.1) names the catalog merchant this NPC is the face of,
	// so the trader's relocation moves the NPC too.
	Merchant string `json:"merchant"`
	// Circuit (v1.0.0-rc.15) is the road a wandering hidden master walks, a
	// stop at a time. Python resolves which stop from the canonical clock, so
	// the engine reads this only to leave those NPCs alone: an NPC whose
	// whereabouts are already decided by content must not also be walked by
	// the civilization tick, or the two answers fight.
	Circuit []string `json:"circuit"`
}

// GeneratedTraits is the prose a person this world made for itself is given
// (v1.0.0-rc.27). A child born to two NPCs, or a relative of a starter
// household, has a name and a location and nothing a narrator can speak with;
// `birth_family_npcs` says so outright by carrying the literal "Member of a
// shared starter household" in its personality column for every relative in
// the game.
//
// Content rather than code, for the same reason the send-off and the narration
// pool are: the prose is the point of it. Picked by `hash64` of the name, so
// the same world makes the same person twice - and deliberately small pools,
// because a hundred bland variations read worse than a dozen written ones.
type GeneratedTraits struct {
	Role        []string `json:"role"`
	Personality []string `json:"personality"`
	Speech      []string `json:"speech"`
	Want        []string `json:"want"`
	Fear        []string `json:"fear"`
}

type ManualDefinition struct {
	Name      string `json:"name"`
	ItemID    string `json:"item_id"`
	Alignment string `json:"alignment"`
	Path      string `json:"path"`
	Grade     string `json:"grade"`
	// Element (v1.0.0-rc.9) is the kind of qi this method draws, which decides
	// how well a given spiritual root can absorb what it gathers.
	Element       string   `json:"element"`
	Sect          string   `json:"sect"` // the sect whose entry inheritance this is (v0.21.4); "" for the rest
	MinRealmIndex int64    `json:"min_realm_index"`
	Description   string   `json:"description"`
	Techniques    []string `json:"techniques"`
	Tags          []string `json:"tags"`
}

type ManualTechniqueDefinition struct {
	Name          string   `json:"name"`
	Manual        string   `json:"manual"`
	MinMastery    int64    `json:"min_mastery"`
	QiCost        int64    `json:"qi_cost"`
	VitalityCost  int64    `json:"vitality_cost"`
	Damage        int64    `json:"damage"`
	Heal          int64    `json:"heal"`
	SuppressTurns int64    `json:"suppress_turns"`
	KarmaCost     int64    `json:"karma_cost"`
	Exposure      int64    `json:"exposure"`
	Tags          []string `json:"tags"`
	Description   string   `json:"description"`
}

type TechniqueSystemDefinition struct {
	Manuals    map[string]ManualDefinition          `json:"manuals"`
	Techniques map[string]ManualTechniqueDefinition `json:"techniques"`
}

type SectRecruitment struct {
	Location string `json:"location"`
	Examiner string `json:"examiner"`
}

// SectTribute is what a sect's own disciples hand in (v1.0.0-rc.18).
//
// `sect_treasury` is the shop a disciple spends contribution points in, and
// until now its only writer was `sect.contribute` - a player handing something
// over. A sect with no player members therefore stocked nothing, ever, which
// made the content's own `resource_policy` ("contribution points can be
// exchanged for stocked sect resources") false for twelve of the thirteen
// sects at any given time, and made joining a quiet sect strictly worse than
// joining a busy one for a reason no player could see.
//
// The roster is content rather than code for the reason the event sites are:
// `@herb`/`@ore`/`@core` resolve against the tier of the world the sect's gate
// stands in, through the same `EventSites.Material`, so one list stays correct
// from the Mortal World to the Celestial.
type SectTribute struct {
	// Materials are refs (`@herb`) or literal item ids, handed in together.
	Materials []string `json:"materials"`
	// DisciplesPerLot is how many living disciples it takes to bring in one
	// of each material per tick. A sect is its people: a valley with three
	// beast-tamers stocks more than a pavilion with two.
	DisciplesPerLot int64 `json:"disciples_per_lot"`
	// Cap is the most of any one material a treasury holds from tribute. A
	// storehouse that grows without limit is not a storehouse.
	Cap int64 `json:"cap"`
}

type SectDefinition struct {
	Alignment string `json:"alignment"`
	Hidden    bool   `json:"hidden"` // the Heaven-Devouring Demon Sect: no public trial, no entry manual
	// Recruitment (v1.0.0-rc.4) names the sect gate - where the trial is
	// held - so the engine knows a gate when a cultivator meditates at one.
	Recruitment SectRecruitment `json:"recruitment"`
	// Karma gates and cell names for a hidden sect (v0.23.0). Only the
	// Heaven-Devouring Demon Sect carries these today; a public sect leaves
	// them zero and Branches empty.
	Branches         map[string]string `json:"branches"`
	KarmaObservation int64             `json:"karma_observation"`
	KarmaInitiation  int64             `json:"karma_initiation"`
	RighteousEnemy   int64             `json:"righteous_enemy"`
}

// DeathQiSystem (v1.0.0-rc.8) is the ghost road: the one cultivation path a
// character must be born to, the ground and hours it reads the other way
// round, what the residue costs, and what it makes of the body.
type DeathQiGround struct {
	RoadSites   map[string]float64 `json:"road_sites"`
	Districts   map[string]float64 `json:"districts"`
	Default     float64            `json:"default"`
	CityPenalty float64            `json:"city_penalty"`
}

type DeathQiCorruption struct {
	PerSession                 int `json:"per_session"`
	PerHarvest                 int `json:"per_harvest"`
	AppeaseRelief              int `json:"appease_relief"`
	AppeaseStoneCost           int `json:"appease_stone_cost"`
	RuptureThreshold           int `json:"rupture_threshold"`
	RuptureChancePercent       int `json:"rupture_chance_percent"`
	PurityCeilingPenaltyPerTen int `json:"purity_ceiling_penalty_per_ten"`
}

type GhostForm struct {
	Name            string  `json:"name"`
	Corruption      int64   `json:"corruption"`
	MinRealmIndex   int64   `json:"min_realm_index"`
	CapacityMult    float64 `json:"capacity_mult"`
	DaylightPenalty float64 `json:"daylight_penalty"`
	Note            string  `json:"note"`
}

// VitalityRecovery is how fast a body mends when it is left alone (v1.0.4).
//
// The share is of the cultivator's own maximum, so the same wound costs the
// same number of world days at every realm and what changes with cultivation
// is what that share is worth. `MinutesPerGameDay` is content rather than a
// constant because it is the unit the share is quoted in, and the two must
// move together or the rate silently means something else.
type VitalityRecovery struct {
	Description       string `json:"description"`
	PercentPerGameDay int64  `json:"percent_per_game_day"`
	MinutesPerGameDay int64  `json:"minutes_per_game_day"`
}

type DeathQiSystem struct {
	Path        string             `json:"path"`
	Families    []string           `json:"families"`
	Description string             `json:"description"`
	Ground      DeathQiGround      `json:"ground"`
	Hours       map[string]float64 `json:"hours"`
	Corruption  DeathQiCorruption  `json:"corruption"`
	GhostForms  []GhostForm        `json:"ghost_forms"`
}

// ElementalQiSystem (v1.0.0-rc.9) is the five-phase cycle and what it is worth
// to a cultivator's absorption: which element each method draws, which phase
// every root element stands with, and what each relation between the two does
// to a gathering session.
type ElementRelation struct {
	Mult                      float64 `json:"mult"`
	Label                     string  `json:"label"`
	Note                      string  `json:"note"`
	DeviationSurchargePercent int     `json:"deviation_surcharge_percent"`
}

type ElementalQiSystem struct {
	Description string                     `json:"description"`
	Phases      []string                   `json:"phases"`
	Generates   map[string]string          `json:"generates"`
	Overcomes   map[string]string          `json:"overcomes"`
	PhaseOf     map[string]string          `json:"phase_of"`
	Relations   map[string]ElementRelation `json:"relations"`
}

type Catalog struct {
	StartingLocation string `json:"starting_location"`
	// WorldQiDensity (v1.0.0-rc.5) is how thick the qi is in each world, by
	// world name: what a cultivation session is multiplied by there. An
	// unlisted world is 1.0.
	WorldQiDensity      map[string]float64             `json:"world_qi_density"`
	Realms              []Realm                        `json:"realms"`
	BodyRealms          []Realm                        `json:"body_realms"`
	Paths               map[string]Path                `json:"paths"`
	Roots               []string                       `json:"roots"`
	SpiritualRootSystem RootSystem                     `json:"spiritual_root_system"`
	Bloodlines          map[string]BloodlineDefinition `json:"bloodlines"`
	Physiques           map[string]PhysiqueDefinition  `json:"physiques"`
	Perfection          PerfectionSystem               `json:"perfection"`
	BodyPerfection      PerfectionSystem               `json:"body_perfection"`
	Items               map[string]Item                `json:"items"`
	Currencies          map[string]CurrencyDefinition  `json:"currencies"`
	AuctionHouses       map[string]AuctionHouse        `json:"auction_houses"`
	TeleportArrays      map[string]TeleportArray       `json:"teleport_arrays"`
	WorldCrossing       WorldCrossingSystem            `json:"world_crossing_system"`
	ProfessionExams     map[string][]ProfessionExam    `json:"profession_exams"`
	AbodeSystem         map[string]any                 `json:"abode_system"`
	SectAbodeSystem     map[string]any                 `json:"sect_abode_system"`
	SectSystem          map[string]any                 `json:"sect_system"`
	SpecialEffects      map[string]map[string]any      `json:"special_effects"`
	Recipes             map[string]Recipe              `json:"recipes"`
	LawSystem           LawSystem                      `json:"law_system"`
	Locations           map[string]LocationDefinition  `json:"locations"`
	UnexpectedEvents    []UnexpectedEvent              `json:"unexpected_events"`
	EventSites          EventSites                     `json:"event_sites"`
	PatronGift          PatronGift                     `json:"patron_gift"`
	SecretRealms        map[string]SecretRealm         `json:"secret_realms"`
	Inheritances        map[string]Inheritance         `json:"inheritances"`
	BirthFamilySendoff  map[string]BirthFamilySendoff  `json:"birth_family_sendoff"`
	BeginnerPath        []BeginnerStage                `json:"beginner_path"`
	// WorldEraCycles (v1.0.7): one ordered cycle of eras per world, each
	// summing to exactly one world year. It lived as a single four-entry Go
	// literal (`eraCycle`) covering all four worlds at once until now - the
	// roster is content, like `event_sites` and `forage_materials`, so a GM
	// can rewrite an age without a rebuild.
	WorldEraCycles     map[string][]EraTemplate     `json:"world_era_cycles"`
	HouseholdErrands   map[string][]HouseholdErrand `json:"household_errands"`
	BirthFamilyLessons map[string]BirthFamilyLesson `json:"birth_family_lesson"`
	GeneratedTraits    GeneratedTraits              `json:"npc_generated_traits"`
	NPCs               map[string]NPCDefinition     `json:"npcs"`
	TechniqueSystem    TechniqueSystemDefinition    `json:"technique_system"`
	WorldRules         map[string]any               `json:"world_rules"`
	Sects              map[string]SectDefinition    `json:"sects"`
	// Merchants (v0.34.1): travelling traders who buy what an auction floor
	// could not sell, carry it along a fixed route of cities and resell it
	// at a markup. They are met in a city while they dwell there, or on the
	// road while a player is in transit over the same leg.
	Merchants map[string]Merchant `json:"merchants"`
	// Shops (v0.35.0): the fixed shops of every city - a smithy, an
	// apothecary, a talisman hall - each an interior location found by
	// exploring the city and entered by travelling to it.
	Shops map[string]Shop `json:"shops"`
	// DeathQi (v1.0.0-rc.8): the ghost road and everything it reads.
	DeathQi DeathQiSystem `json:"death_qi_system"`

	// VitalityRecovery (v1.0.4) is the rate a body mends at on its own. Before
	// it, nothing in this tree restored vitality with time at all - four pills
	// and one technique were the whole of it - so a cultivator who lost a fight
	// sat on the number the fight left them with until they bought their way
	// off it.
	VitalityRecovery VitalityRecovery `json:"vitality_recovery"`
	// ElementalQi (v1.0.0-rc.9): the five phases and what they are worth to
	// absorption.
	ElementalQi ElementalQiSystem `json:"elemental_qi_system"`
	// ForageMaterials (v1.0.0-rc.21): the worked makings a forager can bring
	// back beside the herbs - talisman paper, spirit ink, array blanks. Until
	// this existed, shops were their only source, so Alchemy and Forging could
	// be gathered into and Inscription and Formation could only be bought
	// into. Unlike `@herb`/`@ore` these are tier-flat: one talisman paper
	// serves a Mortal scribe and a Celestial one, so there is no per-world
	// resolution and the roster is a plain map of item id to how it is found.
	ForageMaterials map[string]ForageMaterial `json:"forage_materials"`
	// MineMaterials (v1.1.0): the seam's roster, the forage roster's twin. The
	// world-tier ore itself is `@ore` through EventSites, exactly as the
	// forage's herb is `@herb`; these are the tier-flat makings a dig turns up
	// beside it, and they share ForageMaterial's shape so the tree has one
	// idea of what a find chance looks like.
	MineMaterials map[string]ForageMaterial `json:"mine_materials"`
}

// ForageMaterial is one entry of that roster. Chance is the base percentage
// per forage, MinResources the region richness below which the makings are
// simply not there, and Max the most a single trip can bring back.
type ForageMaterial struct {
	Chance       int64 `json:"chance"`
	MinResources int64 `json:"min_resources"`
	Max          int64 `json:"max"`
}

// Shop is one city shop. Sells is what it stocks (MadeHere lines are the
// keeper's own craft), Buys is what it pays for and how much, both in the
// shop's currency; the stock refills to the content quantities every
// RestockMinutes.
type Shop struct {
	Name           string           `json:"name"`
	Kind           string           `json:"kind"`
	City           string           `json:"city"`
	World          string           `json:"world"`
	Tier           int64            `json:"tier"`
	Location       string           `json:"location"`
	Keeper         string           `json:"keeper"`
	Currency       string           `json:"currency"`
	RestockMinutes int64            `json:"restock_minutes"`
	Description    string           `json:"description"`
	Sells          []ShopLine       `json:"sells"`
	Buys           map[string]int64 `json:"buys"`
}

type ShopLine struct {
	ItemID   string `json:"item_id"`
	Quantity int64  `json:"quantity"`
	Price    int64  `json:"price"`
	MadeHere bool   `json:"made_here"`
}

// Merchant is one travelling trader. Home is the city the trader starts in
// and Route is the loop of cities it walks; the first stop after Home is the
// route entry after Home's index, so Home should be in the route.
type Merchant struct {
	Name          string   `json:"name"`
	World         string   `json:"world"`
	Home          string   `json:"home"`
	Route         []string `json:"route"`
	Budget        int64    `json:"budget"`
	Currency      string   `json:"currency"`
	MarkupPercent int64    `json:"markup_percent"`
	DwellMinutes  int64    `json:"dwell_minutes"`
	// Wares (v0.34.2) is the merchant's own shop: the goods it always
	// carries, at the prices content sets, restocked every time it comes
	// home. Auction leftovers sit beside them in the same pack.
	Wares []MerchantWare `json:"wares"`
}

type MerchantWare struct {
	ItemID   string `json:"item_id"`
	Quantity int64  `json:"quantity"`
	Price    int64  `json:"price"`
}

// loaded is the parsed catalogue, keyed by file and stamped with what the file
// looked like when it was read.
type loaded struct {
	catalog Catalog
	modTime time.Time
	size    int64
}

var (
	catalogMu    sync.RWMutex
	catalogCache = map[string]loaded{}
)

// Load reads and parses `content/world.json`, once per version of the file.
//
// It was a bare ReadFile + Unmarshal with no cache, and fifteen of its
// seventeen call sites are in authoritative.go - inside the request path. So
// every single player action re-read and re-parsed two and a half megabytes of
// JSON, on hardware this project exists to run on: a CPU-only NAS. It is the
// largest avoidable per-action cost in the engine.
//
// Keyed on (path, modification time, size) rather than on the path alone,
// which is what keeps an operator's edit from needing a restart: touch the
// file and the next call re-reads it. Stat is one syscall against a parse of
// millions of bytes. Size is carried beside the timestamp because some
// filesystems keep mtime at one-second resolution, and two edits inside the
// same second that change the length would otherwise serve the older parse.
//
// The Catalog is returned by value and every field in it is read-only after
// parsing, so handing the same one to concurrent callers is safe - nothing in
// this package writes to a loaded catalogue.
func Load(path string) (Catalog, error) {
	info, statErr := os.Stat(path)
	if statErr == nil {
		catalogMu.RLock()
		hit, ok := catalogCache[path]
		catalogMu.RUnlock()
		if ok && hit.size == info.Size() && hit.modTime.Equal(info.ModTime()) {
			return hit.catalog, nil
		}
	}
	// A failed Stat is not a failure: fall through and let ReadFile produce
	// the real error, or succeed if the file is readable but unstattable.
	data, err := os.ReadFile(path)
	if err != nil {
		return Catalog{}, err
	}
	var c Catalog
	if err := json.Unmarshal(data, &c); err != nil {
		return Catalog{}, err
	}
	if len(c.Paths) == 0 || len(c.Roots) == 0 {
		return Catalog{}, fmt.Errorf("world catalog missing paths or roots")
	}
	if statErr == nil {
		catalogMu.Lock()
		catalogCache[path] = loaded{catalog: c, modTime: info.ModTime(), size: info.Size()}
		catalogMu.Unlock()
	}
	return c, nil
}

// SectTribute reads the tribute block out of `sect_system`.
//
// `SectSystem` is an untyped map because most of what is in it (the rank
// ladder, the policy prose) is read by Python off the same file; this is the
// one part the engine acts on, so it is decoded into a shape rather than
// indexed by string at the call site. Called once a tick, not once per sect.
func (c Catalog) SectTribute() SectTribute {
	raw, ok := c.SectSystem["tribute"]
	if !ok {
		return SectTribute{}
	}
	encoded, err := json.Marshal(raw)
	if err != nil {
		return SectTribute{}
	}
	var out SectTribute
	if err := json.Unmarshal(encoded, &out); err != nil {
		return SectTribute{}
	}
	return out
}

func (c Catalog) NormalizePath(raw string) (string, bool) {
	needle := strings.TrimSpace(strings.ToLower(raw))
	for name := range c.Paths {
		if strings.ToLower(name) == needle {
			return name, true
		}
	}
	aliases := map[string]string{"sword": "Sword Cultivator", "qi": "Qi Refiner", "body": "Body Refiner", "soul": "Soul Cultivator", "beast": "Beast Binder", "formation": "Formation Adept", "ghost": "Ghost Cultivator"}
	v, ok := aliases[needle]
	return v, ok
}
