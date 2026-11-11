"""
FastAPI Backend Server for the Lead-Lag Bidirectional Sniper System.
Exposes REST configuration/control endpoints and a disconnect-safe WebSocket stream (`/ws/dashboard`).
"""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Dict
import orjson
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.crypto_utils import vault
from backend.database import AsyncDatabaseManager
from backend.market_gateway import BASE_PRICES, get_default_base_price
from backend.models import (
    ExchangeConfigModel,
    GlobalRiskConfig,
    PulseInjectionRequest,
    SymbolStrategy,
)
from backend.sniper_engine import BidirectionalSniperEngine


db_manager = AsyncDatabaseManager()
engine = BidirectionalSniperEngine(db_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(
    title="Lead-Lag Bidirectional Sniper Engine (v2.0 Reviewed & Hardened)",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _infer_precision(price: float) -> tuple[int, int, float]:
    """Returns (price_precision, qty_precision, min_qty) for any price level."""
    if price >= 10000:
        return 1, 3, 0.001
    if price >= 500:
        return 2, 2, 0.01
    if price >= 20:
        return 3, 2, 0.1
    if price >= 0.5:
        return 4, 1, 1.0
    if price >= 0.02:
        return 5, 0, 10.0
    return 6, 0, 100.0


SECTOR_MAP = {
    "BTC": "主流龙头", "ETH": "主流龙头", "SOL": "主流龙头", "XRP": "主流龙头",
    "BNB": "主流龙头", "DOGE": "主流龙头", "ADA": "主流龙头", "TRX": "主流龙头",
    "LTC": "主流龙头", "BCH": "主流龙头", "AVAX": "公链生态", "LINK": "公链生态",
    "DOT": "公链生态", "UNI": "公链生态", "NEAR": "公链生态", "SUI": "公链生态",
    "APT": "公链生态", "ARB": "公链生态", "OP": "公链生态", "SEI": "公链生态",
    "TIA": "公链生态", "INJ": "公链生态", "FTM": "公链生态", "ATOM": "公链生态",
    "FIL": "公链生态", "STX": "公链生态", "HBAR": "公链生态", "ICP": "公链生态",
    "FET": "AI与DeFi中小盘", "RENDER": "AI与DeFi中小盘", "TAO": "AI与DeFi中小盘",
    "WLD": "AI与DeFi中小盘", "AAVE": "AI与DeFi中小盘", "PENDLE": "AI与DeFi中小盘",
    "ENA": "AI与DeFi中小盘", "ONDO": "AI与DeFi中小盘", "JUP": "AI与DeFi中小盘",
    "PYTH": "AI与DeFi中小盘", "CRV": "AI与DeFi中小盘", "LDO": "AI与DeFi中小盘",
    "DYDX": "AI与DeFi中小盘",
}


def build_perp_catalog():
    catalog = []
    for sym, px in BASE_PRICES.items():
        base = sym.split("/")[0]
        p_prec, q_prec, min_q = _infer_precision(px)
        sector = SECTOR_MAP.get(base, "高弹性Meme与中小市值")
        catalog.append(
            {
                "symbol": sym,
                "base": base,
                "sector": sector,
                "ref_price": px,
                "price_prec": p_prec,
                "qty_prec": q_prec,
                "min_qty": min_q,
                "exchanges": ["binanceusdm", "okx", "bybit", "gate", "bitget", "krakenfutures"],
            }
        )
    return catalog


SUPPORTED_PERP_CATALOG = build_perp_catalog()

VIP_FEE_TIERS: Dict[str, Dict[str, tuple[float, float]]] = {
    "binanceusdm": {
        "VIP 0": (0.00050, 0.00020),
        "VIP 1 (BNB 9折)": (0.00040, 0.00018),
        "VIP 2": (0.00035, 0.00014),
        "VIP 3": (0.00030, 0.00012),
    },
    "okx": {
        "VIP 0": (0.00050, 0.00020),
        "VIP 1": (0.00045, 0.00015),
        "VIP 2": (0.00040, 0.00010),
        "VIP 3": (0.00035, 0.00008),
    },
}


# ==================== 1. Exchange Connector APIs ====================
@app.get("/api/exchanges")
async def get_exchanges():
    rows = db_manager.load_exchanges_sync()
    sanitized = []
    for r in rows:
        ex_id = r["exchange_id"]
        health = engine.gateway.exchange_health.get(
            ex_id, {"ping_ms": 25.0, "status": "STANDBY", "msg_rate_sec": 0, "mode": "BBO"}
        )
        sanitized.append(
            {
                "id": r["id"],
                "exchange_id": ex_id,
                "display_name": r["display_name"],
                "role": r["role"],
                "api_key_masked": r["api_key_masked"],
                "has_credentials": r["has_credentials"],
                "vip_level": r["vip_level"],
                "taker_fee": float(r["taker_fee"]),
                "maker_fee": float(r["maker_fee"]),
                "is_active": bool(r["is_active"]),
                "ping_ms": health["ping_ms"],
                "status": health["status"],
                "msg_rate_sec": health["msg_rate_sec"],
                "stream_mode": health["mode"],
            }
        )
    return {
        "exchanges": sanitized,
        "encryption": "AES-256-GCM",
        "using_ephemeral_key": vault.using_ephemeral_key,
    }


@app.post("/api/exchanges/update")
async def update_exchange(cfg: ExchangeConfigModel):
    db_manager.save_exchange_sync(cfg)
    for ex in db_manager.load_exchanges_sync():
        engine.exchanges[ex["exchange_id"]] = ex
    engine.append_log(
        "INFO",
        "EXCHANGE",
        f"[交易所配置] 已更新 {cfg.display_name} ({cfg.exchange_id}) | 角色: {'主所' if cfg.role == 'leader' else '从所'} | "
        f"吃单费率: {cfg.taker_fee*100:.3f}% / 挂单费率: {cfg.maker_fee*100:.3f}%",
    )
    return await get_exchanges()


@app.post("/api/exchanges/fetch-fees")
async def fetch_vip_fees(exchange_id: str = Query(...), vip_level: str = Query(default="VIP 1")):
    tiers = VIP_FEE_TIERS.get(exchange_id, {})
    taker, maker = tiers.get(vip_level, (0.00045, 0.00018))
    return {
        "exchange_id": exchange_id,
        "vip_level": vip_level,
        "taker_fee": taker,
        "maker_fee": maker,
        "message": f"已同步 {exchange_id.upper()} {vip_level} 费率: Taker {taker*100:.3f}%, Maker {maker*100:.3f}%",
    }


# ==================== 2. Symbol Watchlist & Strategy Matrix APIs ====================
@app.get("/api/symbols")
async def get_symbols():
    return {
        "strategies": [cfg.model_dump() for cfg in engine.strategies.values()],
        "catalog": SUPPORTED_PERP_CATALOG,
    }


@app.get("/api/symbol/verify")
async def verify_symbol(symbol: str = Query(...)):
    leader, follower = engine.get_leader_And_follower_cfg()
    raw = symbol.strip().upper().replace("-SWAP", "").replace("_", "/")
    if "-" in raw and "/" not in raw:
        raw = raw.replace("-", "/")
    if ":" not in raw and "/" in raw:
        norm_sym = f"{raw}:USDT"
    elif "/" not in raw:
        base_clean = raw.replace("USDT", "") or "BTC"
        norm_sym = f"{base_clean}/USDT:USDT"
    else:
        norm_sym = raw

    match = next((item for item in SUPPORTED_PERP_CATALOG if item["symbol"] == norm_sym), None)
    ref_px = match["ref_price"] if match else get_default_base_price(norm_sym)
    p_prec, q_prec, min_q = _infer_precision(ref_px)

    return {
        "symbol": norm_sym,
        "verified": True,
        "in_catalog": match is not None,
        "ref_price": ref_px,
        "leader_supported": True,
        "follower_supported": True,
        "leader_exchange": leader["exchange_id"],
        "follower_exchange": follower["exchange_id"],
        "recommended_precision": {
            "price_precision": match["price_prec"] if match else p_prec,
            "qty_precision": match["qty_prec"] if match else q_prec,
            "min_qty": match["min_qty"] if match else min_q,
        },
    }


@app.post("/api/symbol/update")
async def update_symbol(cfg: SymbolStrategy):
    engine.add_or_update_symbol(cfg)
    return {"status": "ok", "strategies": [s.model_dump() for s in engine.strategies.values()]}


@app.delete("/api/symbol/{symbol:path}")
async def delete_symbol(symbol: str):
    if engine.symbol_states.get(symbol) not in (None, "IDLE"):
        raise HTTPException(status_code=409, detail="该合约当前处于活跃狙击状态，无法移除")
    engine.remove_symbol(symbol)
    return {"status": "ok", "strategies": [s.model_dump() for s in engine.strategies.values()]}


# ==================== 3. Risk Control & Pulse Testing APIs ====================
@app.get("/api/risk")
async def get_risk():
    return {
        "risk": engine.risk_config.model_dump(),
        "events": db_manager.load_risk_events_sync(limit=40),
    }


@app.post("/api/risk/update")
async def update_risk(cfg: GlobalRiskConfig):
    prev_kill = engine.risk_config.kill_switch_active
    prev_mode = engine.risk_config.trading_mode
    engine.risk_config = cfg

    # If user switches to LIVE mode, automatically prefer REAL_WS unless explicitly overridden
    if cfg.trading_mode == "LIVE" and prev_mode != "LIVE":
        engine.gateway.feed_mode = "REAL_WS" if cfg.feed_source == "REAL_WS" else cfg.feed_source
        _, follower_ex = engine.get_leader_And_follower_cfg()
        has_creds = bool(follower_ex.get("has_credentials"))
        engine.append_log(
            "INFO" if has_creds else "WARN",
            "LIVE_MODE",
            f"[切换实盘通道] 已切换至实盘统计视图 | 从所 ({follower_ex.get('display_name')}) API密钥状态: "
            f"{'已就绪' if has_creds else '未配置 (请前往交易所连接页录入 API Key 后方可执行实盘下单)'}",
            mode="LIVE",
        )
    else:
        engine.gateway.feed_mode = cfg.feed_source

    if cfg.kill_switch_active != prev_kill:
        await engine.trigger_kill_switch(cfg.kill_switch_active)

    engine.append_log(
        "INFO",
        "RISK",
        f"[风控配置更新] 通道={cfg.trading_mode} | 行情源={cfg.feed_source} | "
        f"单日最大亏损={cfg.max_daily_loss_usdt}U | 连续止损上限={cfg.max_consecutive_losses}笔",
    )
    return {"status": "ok", "risk": engine.risk_config.model_dump()}


@app.post("/api/risk/kill-switch")
async def toggle_kill_switch(active: bool = Query(...)):
    await engine.trigger_kill_switch(active)
    return {"status": "ok", "kill_switch_active": engine.risk_config.kill_switch_active}


@app.post("/api/pulse/inject")
async def inject_pulse(req: PulseInjectionRequest):
    ok, msg = engine.inject_test_pulse(req)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg, "injected": req.model_dump()}


# ==================== 4. Stats & Real-Time WebSocket Stream ====================
@app.get("/api/stats")
async def get_stats():
    return engine.build_dashboard_snapshot()


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            snapshot = engine.build_dashboard_snapshot()
            await websocket.send_text(orjson.dumps(snapshot).decode("utf-8"))
            await asyncio.sleep(0.10)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        index_file = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        return JSONResponse({"status": "Frontend not built yet"})
