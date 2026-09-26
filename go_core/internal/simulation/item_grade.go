package simulation

import (
	"xianxia/core/internal/game"
	"xianxia/core/internal/worlddata"
)

// worldItem is an item's definition through the engine's one door
// (game.ItemDef), so a graded id on an auction row resolves like any other.
// An id the catalogue does not carry is the zero Item, which is what the bare
// map read these call sites used before answered.
func worldItem(catalog worlddata.Catalog, itemID string) worlddata.Item {
	item, _, _ := game.ItemDef(catalog, itemID)
	return item
}
