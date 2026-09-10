package game

import (
	"sort"

	"xianxia/core/internal/worlddata"
)

// Player property types (v0.30.1). The catalogue's abode_system defines
// every type a cave_abodes row may carry; only those not marked
// `"buildable": false` may be founded. The cave abode is the one retired so
// far: a public sect assigns its disciples an abode (sect_abodes), so the
// property a cultivator founds by hand is one of the specialised estates.
// Rows founded before the flag keep their type and its label.

func propertyTypeDefinition(c worlddata.Catalog, propertyType string) (map[string]any, bool) {
	types, _ := c.AbodeSystem["property_types"].(map[string]any)
	definition, ok := types[propertyType].(map[string]any)
	return definition, ok
}

func propertyTypeBuildable(c worlddata.Catalog, propertyType string) bool {
	definition, ok := propertyTypeDefinition(c, propertyType)
	if !ok {
		return false
	}
	if buildable, flagged := definition["buildable"].(bool); flagged && !buildable {
		return false
	}
	return true
}

func buildablePropertyTypes(c worlddata.Catalog) []string {
	types, _ := c.AbodeSystem["property_types"].(map[string]any)
	out := make([]string, 0, len(types))
	for key := range types {
		if propertyTypeBuildable(c, key) {
			out = append(out, key)
		}
	}
	sort.Strings(out)
	return out
}
