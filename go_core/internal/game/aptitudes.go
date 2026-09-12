package game

import (
	"strings"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/worlddata"
)

var familyRootAffinities = map[string][]string{
	"martial_household": {"Earth", "Metal", "Yang"}, "escort_martial_family": {"Wind", "Water", "Metal"},
	"weaponsmith_martial_family": {"Metal", "Fire", "Earth"}, "body_tempering_family": {"Earth", "Yang", "Metal"},
	"sword_hall_family": {"Metal", "Wind", "Lightning"}, "spear_guard_family": {"Metal", "Earth", "Yang"},
	"hidden_weapon_family": {"Yin", "Water", "Ice"}, "border_garrison_family": {"Earth", "Wind", "Metal"},
	"fallen_martial_clan": {"Metal", "Yin", "Earth"}, "noble_martial_clan": {"Metal", "Yang", "Lightning"},
	"alchemy_family": {"Wood", "Fire", "Water"},
	// v1.0.0-rc.8: the two ghost-born households.
	"nether_market_house": {"Yin", "Void", "Water"}, "tomb_watch_clan": {"Yin", "Earth", "Ice"},
}
var locationRootAffinities = map[string][]string{
	"Greenriver Town":               {"Water", "Wood"},
	"Cloudspine Foothills":          {"Earth", "Wind"},
	"Moonfen Marsh":                 {"Water", "Yin", "Wood"},
	"Spirit Jade Rebirth Enclave":   {"Wood", "Water", "Yang"},
	"Nine-Heavens Rebirth Terrace":  {"Wind", "Yang", "Lightning"},
	"Celestial Cradle Province":     {"Yang", "Void", "Chaos"},
	"Riverguard City":               {"Water", "Earth", "Yang"},
	"Jadeflow Spirit City":          {"Water", "Earth", "Yang"},
	"Immortal River City":           {"Water", "Earth", "Yang"},
	"Celestial River City":          {"Water", "Earth", "Yang"},
	"Four-Roads Caravan City":       {"Wind", "Water", "Metal"},
	"Galevein Spirit City":          {"Wind", "Water", "Metal"},
	"Skyroad Immortal City":         {"Wind", "Water", "Metal"},
	"Starroad Celestial City":       {"Wind", "Water", "Metal"},
	"Emberforge City":               {"Fire", "Metal", "Earth"},
	"Vermilion Furnace City":        {"Fire", "Metal", "Earth"},
	"Solar Furnace Immortal City":   {"Fire", "Metal", "Earth"},
	"Solar Crucible Celestial City": {"Fire", "Metal", "Earth"},
	"Stoneback Mountain City":       {"Earth", "Yang", "Metal"},
	"Stoneheart Spirit City":        {"Earth", "Yang", "Metal"},
	"Adamant Body Immortal City":    {"Earth", "Yang", "Metal"},
	"Worldstone Celestial City":     {"Earth", "Yang", "Metal"},
	"Cloudblade City":               {"Wind", "Metal", "Lightning"},
	"Cloudedge Spirit City":         {"Wind", "Metal", "Lightning"},
	"Heavenblade Immortal City":     {"Wind", "Metal", "Lightning"},
	"Firmament Blade City":          {"Wind", "Metal", "Lightning"},
	"Ironbanner City":               {"Metal", "Earth", "Yang"},
	"Spearwall Spirit City":         {"Metal", "Earth", "Yang"},
	"Golden Spear Immortal City":    {"Metal", "Earth", "Yang"},
	"Mandate Spear City":            {"Metal", "Earth", "Yang"},
	"Moonfen City":                  {"Yin", "Water", "Ice"},
	"Moonfrost Spirit City":         {"Yin", "Water", "Ice"},
	"Lunar Veil Immortal City":      {"Yin", "Water", "Ice"},
	"Lunar Shadow Celestial City":   {"Yin", "Water", "Ice"},
	"Frostwatch City":               {"Ice", "Wind", "Earth"},
	"Northwind Spirit City":         {"Ice", "Wind", "Earth"},
	"Polar Gate Immortal City":      {"Ice", "Wind", "Earth"},
	"Froststar Border City":         {"Ice", "Wind", "Earth"},
	"Ashenwall City":                {"Earth", "Metal", "Yin"},
	"Broken Halo Spirit City":       {"Earth", "Metal", "Yin"},
	"Fallen Star Immortal City":     {"Earth", "Metal", "Yin"},
	"Ruined Constellation City":     {"Earth", "Metal", "Yin"},
	"Azure Crown Imperial City":     {"Yang", "Metal", "Lightning"},
	"Jade Crown Spirit City":        {"Yang", "Metal", "Lightning"},
	"Ninefold Noble Immortal City":  {"Yang", "Metal", "Lightning"},
	"Mandate Crown Celestial City":  {"Yang", "Metal", "Lightning"},
	"Jadewood Medicine City":        {"Wood", "Water", "Fire"},
	"Hundred Herb Spirit City":      {"Wood", "Water", "Fire"},
	"Jade Cauldron Immortal City":   {"Wood", "Water", "Fire"},
	"Divine Herb Celestial City":    {"Wood", "Water", "Fire"},
}
var rootBaseWeights = map[string]int{"Fire": 10, "Water": 10, "Wood": 10, "Earth": 10, "Metal": 10, "Lightning": 5, "Wind": 7, "Ice": 5, "Yin": 5, "Yang": 5, "Mortal Root": 18, "Void": 2, "Chaos": 1}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func familyArchetype(f BirthFamily) string {
	if strings.TrimSpace(f.ID) != "" {
		return f.ID
	}
	return f.Archetype
}

func rollFamilyRoot(f BirthFamily, roots []string) (string, error) {
	weights := map[string]int{}
	for _, root := range roots {
		w := rootBaseWeights[root]
		if w < 1 {
			w = 6
		}
		weights[root] = w
	}
	if favored := familyRootAffinities[familyArchetype(f)]; len(favored) > 0 {
		bonuses := []int{28, 18, 12}
		for i, root := range favored {
			if _, ok := weights[root]; ok {
				b := bonuses[minInt(i, 2)]
				weights[root] += b
			}
		}
	}
	if favored := locationRootAffinities[f.Location]; len(favored) > 0 {
		bonuses := []int{10, 7, 5}
		for i, root := range favored {
			if _, ok := weights[root]; ok {
				weights[root] += bonuses[minInt(i, 2)]
			}
		}
	}
	if _, ok := weights[f.BloodlineAffinity]; ok && f.BloodlineAffinity != "" {
		weights[f.BloodlineAffinity] += 10
	}
	if w, ok := weights["Mortal Root"]; ok {
		tier := clampInt(int(f.Tier), 1, 5)
		weights["Mortal Root"] = maxInt(5, w-(tier-1)*2)
	}
	total := 0
	for _, root := range roots {
		total += maxInt(1, weights[root])
	}
	if total <= 0 {
		return "Mortal Root", nil
	}
	roll, err := gamerng.Intn(total)
	if err != nil {
		return "", err
	}
	running := 0
	for _, root := range roots {
		running += maxInt(1, weights[root])
		if roll < running {
			return root, nil
		}
	}
	return roots[len(roots)-1], nil
}

func gradeIndex(system worlddata.RootSystem, grade string) int {
	for i, g := range system.Grades {
		if strings.EqualFold(g.Name, grade) {
			return i
		}
	}
	return 0
}
func gradeDef(system worlddata.RootSystem, grade string) worlddata.RootGrade {
	if len(system.Grades) == 0 {
		return worlddata.RootGrade{Name: "Common", CultivationMult: 1}
	}
	i := gradeIndex(system, grade)
	if i >= len(system.Grades) {
		i = len(system.Grades) - 1
	}
	return system.Grades[i]
}
func rollRootGrade(system worlddata.RootSystem, familyTier, talentEcho int) (string, error) {
	if len(system.Grades) == 0 {
		return "Common", nil
	}
	n, err := gamerng.Intn(1000)
	if err != nil {
		return "", err
	}
	luck := maxInt(0, familyTier-1)*24 + maxInt(0, talentEcho)*2
	roll := minInt(999, n+luck)
	selected := system.Grades[0].Name
	for _, g := range system.Grades {
		if roll >= g.MinRoll {
			selected = g.Name
		}
	}
	return selected, nil
}
func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}
func rootCompatibility(elements []string, path string, system worlddata.RootSystem, mutation string) int {
	affinities := system.PathAffinities[path]
	score := 30
	if len(elements) > 0 && !(len(elements) == 1 && elements[0] == "Mortal Root") {
		matches := 0
		for _, e := range elements {
			if contains(affinities, e) {
				matches++
			}
		}
		score = 45 + matches*20 - maxInt(0, len(elements)-1)*4
		if matches == len(elements) {
			score += 5
		}
	}
	if m, ok := system.Mutations[mutation]; ok && contains(m.FavoredPaths, path) {
		score += 10
	}
	return clampInt(score, 10, 100)
}

func generateRootProfile(baseRoot, path string, system worlddata.RootSystem, familyTier, talentEcho int) (SpiritualRootState, error) {
	grade, err := rollRootGrade(system, familyTier, talentEcho)
	if err != nil {
		return SpiritualRootState{}, err
	}
	gd := gradeDef(system, grade)
	n, err := gamerng.Intn(24)
	if err != nil {
		return SpiritualRootState{}, err
	}
	purity := clampInt(38+gradeIndex(system, grade)*10+n+talentEcho/8, 20, 100)
	elements := []string{baseRoot}
	candidates := []string{}
	for _, v := range system.Elements {
		if v != baseRoot && v != "Mortal Root" {
			candidates = append(candidates, v)
		}
	}
	if baseRoot != "Mortal Root" && len(candidates) > 0 {
		r, _ := gamerng.Intn(100)
		if r < gd.SecondaryChance {
			i, _ := gamerng.Intn(len(candidates))
			elements = append(elements, candidates[i])
		}
	}
	remaining := []string{}
	for _, v := range candidates {
		if !contains(elements, v) {
			remaining = append(remaining, v)
		}
	}
	if len(remaining) > 0 && len(elements) > 1 {
		r, _ := gamerng.Intn(100)
		if r < gd.TertiaryChance {
			i, _ := gamerng.Intn(len(remaining))
			elements = append(elements, remaining[i])
		}
	}
	mutation := ""
	eligible := []string{}
	for k, d := range system.Mutations {
		if len(d.RequiresAny) == 0 {
			eligible = append(eligible, k)
			continue
		}
		for _, e := range elements {
			if contains(d.RequiresAny, e) {
				eligible = append(eligible, k)
				break
			}
		}
	}
	if len(eligible) > 0 {
		r, _ := gamerng.Intn(100)
		if r < gd.MutationChance+maxInt(0, talentEcho/20) {
			i, _ := gamerng.Intn(len(eligible))
			mutation = eligible[i]
		}
	}
	sroll, _ := gamerng.Intn(13)
	stability := clampInt(88+sroll-maxInt(0, len(elements)-1)*6, 35, 100)
	return SpiritualRootState{Grade: grade, Purity: purity, Elements: elements, Mutation: mutation, Stability: stability, RefinementProgress: 0, Compatibility: rootCompatibility(elements, path, system, mutation)}, nil
}

func generateBloodline(f BirthFamily, defs map[string]worlddata.BloodlineDefinition) (*BloodlineState, error) {
	name := f.BloodlineName
	if name == "" || name == "None" || f.BloodlinePurity <= 0 {
		return nil, nil
	}
	id := "legacy_family_bloodline"
	def := worlddata.BloodlineDefinition{}
	for k, d := range defs {
		if strings.EqualFold(d.Name, name) {
			id = k
			def = d
			break
		}
	}
	n, err := gamerng.Intn(25)
	if err != nil {
		return nil, err
	}
	purity := clampInt(clampInt(int(f.BloodlinePurity), 1, 100)-12+n, 5, 100)
	aff := def.Affinity
	if aff == "" {
		aff = f.BloodlineAffinity
	}
	display := def.Name
	if display == "" {
		display = name
	}
	return &BloodlineState{BloodlineID: id, Name: display, Affinity: aff, Purity: purity, State: "dormant", PrimaryLineage: 1, UnlockedTechniques: []string{}}, nil
}
func ordinaryPhysique() PhysiqueState {
	return PhysiqueState{PhysiqueID: "ordinary_mortal_body", Name: "Ordinary Mortal Body", State: "ordinary", Stability: 100}
}
func generatePhysique(path string, roots []string, f BirthFamily, defs map[string]worlddata.PhysiqueDefinition, talentEcho int) (PhysiqueState, error) {
	chance := clampInt(18+int(f.Tier)*4+talentEcho/5, 18, 60)
	r, err := gamerng.Intn(100)
	if err != nil {
		return PhysiqueState{}, err
	}
	if r >= chance {
		return ordinaryPhysique(), nil
	}
	type pick struct {
		id string
		d  worlddata.PhysiqueDefinition
	}
	weighted := []pick{}
	for k, d := range defs {
		if k == "ordinary_mortal_body" {
			continue
		}
		w := 1
		if contains(d.FavoredPaths, path) {
			w += 4
		}
		match := false
		for _, root := range roots {
			if contains(d.FavoredRoots, root) {
				match = true
				break
			}
		}
		if match {
			w += 4
		}
		for i := 0; i < w; i++ {
			weighted = append(weighted, pick{k, d})
		}
	}
	if len(weighted) == 0 {
		return ordinaryPhysique(), nil
	}
	i, err := gamerng.Intn(len(weighted))
	if err != nil {
		return PhysiqueState{}, err
	}
	chosen := weighted[i]
	sr, _ := gamerng.Intn(19)
	name := chosen.d.Name
	if name == "" {
		name = chosen.id
	}
	return PhysiqueState{PhysiqueID: chosen.id, Name: name, State: "dormant", Stability: 82 + sr}, nil
}
func generateAptitudes(baseRoot, path string, f BirthFamily, c worlddata.Catalog, talentEcho int) (AptitudeBundle, error) {
	root, err := generateRootProfile(baseRoot, path, c.SpiritualRootSystem, int(f.Tier), talentEcho)
	if err != nil {
		return AptitudeBundle{}, err
	}
	blood, err := generateBloodline(f, c.Bloodlines)
	if err != nil {
		return AptitudeBundle{}, err
	}
	phys, err := generatePhysique(path, root.Elements, f, c.Physiques, talentEcho)
	if err != nil {
		return AptitudeBundle{}, err
	}
	return AptitudeBundle{Root: root, Bloodline: blood, Physique: phys}, nil
}
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
