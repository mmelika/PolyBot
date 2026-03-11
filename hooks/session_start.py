import database
import polymarket_client


def auto_redeem_settled_trades(db_path: str, mode: str) -> int:
    redeemed = 0
    for trade in database.get_settled_unredeemed_trades(db_path, mode=mode):
        if polymarket_client.auto_redeem(trade):
            database.mark_trade_redeemed(db_path, trade["id"])
            redeemed += 1
    return redeemed


def run(db_path: str, mode: str, settings: dict) -> dict:
    import trader

    return {
        "redeemed": auto_redeem_settled_trades(db_path, mode),
        "stopped_out": trader.check_stop_losses(db_path, mode, settings),
    }
