package game

type FamilyRelative struct {
	Name       string `json:"name"`
	Relation   string `json:"relation"`
	Gender     string `json:"gender"`
	Age        int64  `json:"age"`
	RealmIndex int64  `json:"realm_index"`
	Phase      int64  `json:"phase"`
}

type BirthFamily struct {
	ID                string           `json:"id"`
	Archetype         string           `json:"archetype"`
	Category          string           `json:"category,omitempty"`
	FamilyName        string           `json:"family_name"`
	Name              string           `json:"name"`
	Surname           string           `json:"surname"`
	Tier              int64            `json:"tier"`
	Wealth            int64            `json:"wealth"`
	Influence         int64            `json:"influence"`
	Stability         int64            `json:"stability"`
	AlignmentBias     int64            `json:"alignment_bias"`
	Location          string           `json:"location"`
	NearbyCity        string           `json:"nearby_city,omitempty"`
	HomelandTheme     string           `json:"homeland_theme,omitempty"`
	Climate           string           `json:"climate,omitempty"`
	Boon              string           `json:"boon,omitempty"`
	Risk              string           `json:"risk,omitempty"`
	RebirthWorld      string           `json:"rebirth_world,omitempty"`
	LineageStatus     string           `json:"lineage_status,omitempty"`
	LineageSummary    string           `json:"lineage_summary,omitempty"`
	PreviousFamily    string           `json:"previous_family,omitempty"`
	HeadName          string           `json:"head_name"`
	HeadGender        string           `json:"head_gender"`
	HeadTitle         string           `json:"head_title"`
	HeadRealmIndex    int64            `json:"head_realm_index"`
	HeadPhase         int64            `json:"head_phase"`
	ClanStructure     string           `json:"clan_structure"`
	BloodlineName     string           `json:"bloodline_name"`
	BloodlineAffinity string           `json:"bloodline_affinity"`
	BloodlineTrait    string           `json:"bloodline_trait"`
	BloodlinePurity   int64            `json:"bloodline_purity"`
	BranchCount       int64            `json:"branch_count"`
	RetainerCount     int64            `json:"retainer_count"`
	ConfederacyName   string           `json:"confederacy_name"`
	BirthOrder        int64            `json:"birth_order"`
	Relatives         []FamilyRelative `json:"relatives"`
}

type SpiritualRootState struct {
	Grade              string   `json:"grade"`
	Purity             int      `json:"purity"`
	Elements           []string `json:"elements"`
	Mutation           string   `json:"mutation"`
	Stability          int      `json:"stability"`
	RefinementProgress int      `json:"refinement_progress"`
	Compatibility      int      `json:"compatibility"`
}

type BloodlineState struct {
	BloodlineID        string   `json:"bloodline_id"`
	Name               string   `json:"name"`
	Affinity           string   `json:"affinity"`
	Purity             int      `json:"purity"`
	State              string   `json:"state"`
	EvolutionStage     int      `json:"evolution_stage"`
	Progress           int      `json:"progress"`
	Rejection          int      `json:"rejection"`
	Mutation           string   `json:"mutation"`
	PrimaryLineage     int      `json:"primary_lineage"`
	UnlockedTechniques []string `json:"unlocked_techniques"`
}

type PhysiqueState struct {
	PhysiqueID     string `json:"physique_id"`
	Name           string `json:"name"`
	State          string `json:"state"`
	EvolutionStage int    `json:"evolution_stage"`
	Progress       int    `json:"progress"`
	Stability      int    `json:"stability"`
	Instability    int    `json:"instability"`
}

type AptitudeBundle struct {
	Root      SpiritualRootState `json:"root"`
	Bloodline *BloodlineState    `json:"bloodline"`
	Physique  PhysiqueState      `json:"physique"`
}

type CharacterState struct {
	UserID               int64  `json:"user_id"`
	Name                 string `json:"name"`
	Gender               string `json:"gender"`
	Path                 string `json:"path"`
	SpiritualRoot        string `json:"spiritual_root"`
	RealmIndex           int64  `json:"realm_index"`
	Phase                int64  `json:"phase"`
	BodyRealmIndex       int64  `json:"body_realm_index"`
	BodyPhase            int64  `json:"body_phase"`
	NaturalLifespanYears int64  `json:"natural_lifespan_years"`
	LifeExtensionYears   int64  `json:"life_extension_years"`
	CreatedGameMinute    int64  `json:"created_game_minute"`
	AgeAtCreationYears   int64  `json:"age_at_creation_years"`
}

type CultivationState struct {
	RealmIndex  int64 `json:"realm_index"`
	Phase       int64 `json:"phase"`
	Cultivation int64 `json:"cultivation"`
	Qi          int64 `json:"qi"`
	QiMax       int64 `json:"qi_max"`
}

type CombatState struct {
	BattleID    int64  `json:"battle_id"`
	UserID      int64  `json:"user_id"`
	PlayerHP    int64  `json:"player_hp"`
	PlayerHPMax int64  `json:"player_hp_max"`
	Status      string `json:"status"`
	Version     int64  `json:"version"`
}

type NPCState struct {
	Name       string `json:"name"`
	Status     string `json:"status"`
	Location   string `json:"location"`
	RealmIndex int64  `json:"realm_index"`
	Phase      int64  `json:"phase"`
	Faction    string `json:"faction"`
}

type SectState struct {
	SectID    string `json:"sect_id"`
	Name      string `json:"name"`
	Treasury  int64  `json:"treasury"`
	Influence int64  `json:"influence"`
	Stability int64  `json:"stability"`
}
