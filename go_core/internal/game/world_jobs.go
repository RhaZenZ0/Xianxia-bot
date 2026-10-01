package game

import "xianxia/core/internal/worlddata"

// Each world above the Mortal has a job (v1.17.0), and a district that does
// it there and nowhere else. The three worlds above the Mortal were the
// Mortal World again with less in it: the same eleven cities, the same
// shops, the same halls, and nothing a rule read that the Mortal World did
// not also have. Each now carries one district kind of its own, read by one
// rule:
//
//   - the Spiritual World, where the Laws open (law_system.normal_min_realm_index
//     is its first realm), has a sutra archive - `law.comprehend` rolls
//     lawArchiveBonus better under its roof;
//   - the Immortal World, whose flame is the first that opens the top grade,
//     has a grandmasters' court - every craft roll made in the square gains
//     craftCourtBonus;
//   - the Celestial World, where the mandate of heaven is read, has a
//     heaven-reading altar - a breakthrough attempted on its stone gains
//     breakthroughAltarBonus, in the odds shown and in the roll alike.
//
// The kind is read off the district the cultivator stands in, so the content
// decides where each stands and the engine decides what it is worth; the
// content gate holds each kind to one world. A district is `safe_zone`
// ground like any other and the bonus is the whole of what it does.
const (
	districtArchive = "archive"
	districtCourt   = "court"
	districtAltar   = "altar"

	lawArchiveBonus        = int64(2)
	craftCourtBonus        = int64(2)
	breakthroughAltarBonus = int64(2)
)

// jobDistrictAt answers the district's name when the location is a district
// of the given kind, else the empty string.
func jobDistrictAt(catalog worlddata.Catalog, location, kind string) string {
	if def, ok := catalog.Locations[location]; ok && def.District == kind {
		return location
	}
	return ""
}

// lawPlaceBonus is what the ground adds to a Law comprehension roll: the
// sutra archive's bonus under its roof, nothing anywhere else.
func lawPlaceBonus(catalog worlddata.Catalog, location string) (string, int64) {
	if name := jobDistrictAt(catalog, location, districtArchive); name != "" {
		return name, lawArchiveBonus
	}
	return "", 0
}

// craftPlaceBonus is what the ground adds to a craft roll: the grandmasters'
// court's bonus in its square, nothing anywhere else.
func craftPlaceBonus(catalog worlddata.Catalog, location string) (string, int64) {
	if name := jobDistrictAt(catalog, location, districtCourt); name != "" {
		return name, craftCourtBonus
	}
	return "", 0
}

// breakthroughPlaceBonus is what the ground adds to a breakthrough roll: the
// heaven-reading altar's bonus on its stone, nothing anywhere else.
func breakthroughPlaceBonus(catalog worlddata.Catalog, location string) (string, int64) {
	if name := jobDistrictAt(catalog, location, districtAltar); name != "" {
		return name, breakthroughAltarBonus
	}
	return "", 0
}
