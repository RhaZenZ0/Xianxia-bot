package game

import (
	"encoding/json"
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func applyLateMigrationAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, op string, raw json.RawMessage) (authoritativeMutation, error) {
	switch op {
	case "auction.enter":
		return auctionEnterAction(conn, catalog, userID, raw)
	case "auction.leave":
		return auctionLeaveAction(conn, catalog, userID, raw)
	case "auction.sell":
		return auctionSellAction(conn, catalog, userID, raw)
	case "auction.bid":
		return auctionBidAction(conn, catalog, userID, raw)
	case "merchant.buy":
		return merchantBuyAction(conn, catalog, userID, raw)
	case "shop.buy":
		return shopBuyAction(conn, catalog, userID, raw)
	case "shop.sell":
		return shopSellAction(conn, catalog, userID, raw)
	case "trade.offer":
		return tradeOfferAction(conn, catalog, userID, raw)
	case "trade.accept":
		return tradeAcceptAction(conn, catalog, userID, raw)
	case "trade.decline":
		return tradeDeclineAction(conn, catalog, userID, raw)
	case "black_market.trade":
		return blackMarketTradeAction(conn, catalog, userID, raw)
	case "market.trade":
		return marketTradeAction(conn, catalog, userID, raw)
	case "bounty_hunter.act":
		return bountyHunterActionGo(conn, catalog, userID, raw)
	case "equipment.bind", "equipment.equip", "equipment.unequip", "equipment.repair":
		return equipmentAction(conn, catalog, userID, raw, op)
	case "party.create", "party.join", "party.leave":
		return partyAction(conn, catalog, userID, raw, op)
	case "formation.create", "formation.assign", "formation.activate", "formation.stance":
		return formationAction(conn, catalog, userID, raw, op)
	case "boss.start":
		return bossStartActionGo(conn, catalog, userID, raw)
	case "boss.act":
		return bossActActionGo(conn, catalog, userID, raw)
	case "boss.claim":
		return bossClaimActionGo(conn, catalog, userID, raw)
	case "territory.claim":
		return territoryClaimActionGo(conn, catalog, userID, raw)
	case "war.act":
		return territoryWarActActionGo(conn, catalog, userID, raw)
	case "caravan.dispatch":
		return caravanDispatchActionGo(conn, catalog, userID, raw)
	case "caravan.settle":
		return caravanSettleActionGo(conn, catalog, userID, raw)
	case "sect.recruitment.recommendation":
		return sectRecommendationActionGo(conn, catalog, userID, raw)
	case "sect.recruitment.trial":
		return sectTrialActionGo(conn, catalog, userID, raw)
	case "sect.contribute", "sect.redeem":
		return sectEconomyActionGo(conn, catalog, userID, raw, op)
	case "discipleship.request", "discipleship.resolve", "discipleship.leave":
		return discipleshipActionGo(conn, catalog, userID, raw, op)
	case "sect.manor.establish", "sect.manor.upgrade":
		return sectManorActionGo(conn, catalog, userID, raw, op)
	case "family.simulate":
		return simulateBirthFamilyGo(conn, userID, raw)
	case "family.support":
		return familySupportActionGo(conn, catalog, userID, raw)
	case "family.add_child":
		return familyAddChildActionGo(conn, catalog, userID, raw)
	case "seclusion.start":
		return seclusionStartActionGo(conn, catalog, userID, raw)
	case "seclusion.settle":
		return seclusionSettleActionGo(conn, catalog, userID, raw)
	case "dao.propose", "dao.respond", "dao.sever", "dao.dual_cultivate":
		return daoPartnershipActionGo(conn, catalog, userID, raw, op)
	case "storage.deposit":
		return storageMoveActionGo(conn, userID, raw, false)
	case "storage.withdraw":
		return storageMoveActionGo(conn, userID, raw, true)
	case "storage.upgrade":
		return storageUpgradeActionGo(conn, catalog, userID, raw)
	case "abode.establish":
		return abodeEstablishActionGo(conn, catalog, userID, raw)
	case "abode.enter":
		return abodeMoveActionGo(conn, userID, raw, "enter")
	case "abode.visit":
		return abodeMoveActionGo(conn, userID, raw, "visit")
	case "abode.leave":
		return abodeMoveActionGo(conn, userID, raw, "leave")
	case "abode.invite":
		return abodeGuestActionGo(conn, userID, raw, false)
	case "abode.revoke":
		return abodeGuestActionGo(conn, userID, raw, true)
	case "abode.upgrade":
		return abodeUpgradeActionGo(conn, catalog, userID, raw)
	case "abode.focus":
		return abodeFocusActionGo(conn, catalog, userID, raw)
	case "array.use":
		return teleportArrayActionGo(conn, catalog, userID, raw)
	case "array.deploy":
		return deployArrayActionGo(conn, catalog, userID, raw)
	case "spatial_key.use":
		return spatialKeyActionGo(conn, catalog, userID, raw)
	case "personal_world.create":
		return personalWorldCreateActionGo(conn, userID, raw)
	case "personal_world.set_rule":
		return personalWorldRuleActionGo(conn, userID, raw)
	case "personal_world.enter":
		return personalWorldMoveActionGo(conn, userID, raw, false)
	case "personal_world.leave":
		return personalWorldMoveActionGo(conn, userID, raw, true)
	case "item.use":
		return itemUseActionGo(conn, catalog, userID, raw)
	case "sect.abode.upgrade":
		return sectAbodeUpgradeAction(conn, catalog, userID, raw)
	default:
		return authoritativeMutation{}, fmt.Errorf("unsupported late migration operation: %s", op)
	}
}
