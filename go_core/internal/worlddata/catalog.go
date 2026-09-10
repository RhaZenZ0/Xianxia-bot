package worlddata

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
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
	ArrayDeploy      string         `json:"array_deploy"`
	SpatialKey       map[string]any `json:"spatial_key"`
	MarketExcluded   bool           `json:"market_excluded"`
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
	DoorRule          bool   `json:"door_rule"`
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

type CurrencyDefinition struct {
	Name string `json:"name"`
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
}
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
	Climate         string   `json:"climate"`
	Terrain         string   `json:"terrain"`
	SettlementType  string   `json:"settlement_type"`
	Roads           []string `json:"roads"`
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
}

type ManualDefinition struct {
	Name          string   `json:"name"`
	ItemID        string   `json:"item_id"`
	Alignment     string   `json:"alignment"`
	Path          string   `json:"path"`
	Grade         string   `json:"grade"`
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

type SectDefinition struct {
	Alignment string `json:"alignment"`
	Hidden    bool   `json:"hidden"` // the Heaven-Devouring Demon Sect: no public trial, no entry manual
	// Karma gates and cell names for a hidden sect (v0.23.0). Only the
	// Heaven-Devouring Demon Sect carries these today; a public sect leaves
	// them zero and Branches empty.
	Branches         map[string]string `json:"branches"`
	KarmaObservation int64             `json:"karma_observation"`
	KarmaInitiation  int64             `json:"karma_initiation"`
	RighteousEnemy   int64             `json:"righteous_enemy"`
}

type Catalog struct {
	StartingLocation    string                         `json:"starting_location"`
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
	AbodeSystem         map[string]any                 `json:"abode_system"`
	SectAbodeSystem     map[string]any                 `json:"sect_abode_system"`
	SectSystem          map[string]any                 `json:"sect_system"`
	SpecialEffects      map[string]map[string]any      `json:"special_effects"`
	Recipes             map[string]Recipe              `json:"recipes"`
	LawSystem           LawSystem                      `json:"law_system"`
	Locations           map[string]LocationDefinition  `json:"locations"`
	UnexpectedEvents    []UnexpectedEvent              `json:"unexpected_events"`
	SecretRealms        map[string]SecretRealm         `json:"secret_realms"`
	Inheritances        map[string]Inheritance         `json:"inheritances"`
	NPCs                map[string]NPCDefinition       `json:"npcs"`
	TechniqueSystem     TechniqueSystemDefinition      `json:"technique_system"`
	WorldRules          map[string]any                 `json:"world_rules"`
	Sects               map[string]SectDefinition      `json:"sects"`
}

func Load(path string) (Catalog, error) {
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
	return c, nil
}

func (c Catalog) NormalizePath(raw string) (string, bool) {
	needle := strings.TrimSpace(strings.ToLower(raw))
	for name := range c.Paths {
		if strings.ToLower(name) == needle {
			return name, true
		}
	}
	aliases := map[string]string{"sword": "Sword Cultivator", "qi": "Qi Refiner", "body": "Body Refiner", "soul": "Soul Cultivator", "beast": "Beast Binder", "formation": "Formation Adept"}
	v, ok := aliases[needle]
	return v, ok
}
