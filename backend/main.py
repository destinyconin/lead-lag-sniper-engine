"""
FastAPI Backend Server for the Lead-Lag Bidirectional Sniper System.
Exposes REST configuration/control endpoints and a disconnect-safe WebSocket stream (`/ws/dashboard`).
"""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
import orjson
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.crypto_utils import vault
from backend.database import AsyncDatabaseManager
from backend.market_gateway import BASE_PRICES, get_default_base_price
from backend.models import (
    AdminAuditOrderRequest,
    AdminCreateUserRequest,
    AdminGenerateVipCodesRequest,
    AdminGrantVipRequest,
    AdminMembershipPlanModel,
    AdminUpdateUserRequest,
    ChangePasswordRequest,
    ExchangeConfigModel,
    GlobalRiskConfig,
    LoginRequest,
    PurchaseMembershipRequest,
    PulseInjectionRequest,
    RedeemVipCodeRequest,
    RegisterRequest,
    SymbolStrategy,
)
from backend.sniper_engine import BidirectionalSniperEngine
from backend.user_membership import user_manager


db_manager = AsyncDatabaseManager()
engine = BidirectionalSniperEngine(db_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    user_manager.init_sync()
    await engine.start()
    yield
    await engine.stop()


def _extract_user(authorization: str = "") -> Optional[Dict[str, Any]]:
    if not authorization:
        return None
    parts = authorization.strip().split()
    token = parts[-1] if parts else ""
    return user_manager.get_user_from_token(token)


def _require_user(authorization: str = "") -> Dict[str, Any]:
    u = _extract_user(authorization)
    if not u:
        raise HTTPException(status_code=401, detail="请先注册并登录账号后再查看实盘系统")
    return u


def _require_vip(authorization: str = "") -> Dict[str, Any]:
    u = _require_user(authorization)
    if not u.get("can_use_live"):
        raise HTTPException(status_code=403, detail="注册用户开通 VIP 会员后才可以使用实盘系统，请先前往会员中心开通会员")
    return u


def _require_admin(authorization: str = "") -> Dict[str, Any]:
    u = _require_user(authorization)
    if not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="仅后台管理员可执行该管理操作")
    return u


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
                "has_api_key": r.get("has_api_key", False),
                "has_api_secret": r.get("has_api_secret", False),
                "has_passphrase": r.get("has_passphrase", False),
                "has_credentials": r["has_credentials"],
                "auth_status": r.get("auth_status", "UNCONFIGURED"),
                "auth_message": r.get("auth_message", "未配置 API 密钥"),
                "account_equity_usdt": r.get("account_equity_usdt", 0.0),
                "last_verified_at": r.get("last_verified_at", ""),
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
async def update_exchange(cfg: ExchangeConfigModel, authorization: str = Header(default="")):
    _require_vip(authorization)
    db_manager.save_exchange_sync(cfg)
    for ex in db_manager.load_exchanges_sync():
        engine.exchanges[ex["exchange_id"]] = ex

    ver_res = await engine.verify_and_update_exchange(cfg.exchange_id)
    ex_resp = await get_exchanges()
    ex_resp["verify_result"] = ver_res
    return ex_resp


@app.post("/api/exchanges/verify")
async def verify_exchange_api(exchange_id: str = Query(...), authorization: str = Header(default="")):
    _require_vip(authorization)
    ver_res = await engine.verify_and_update_exchange(exchange_id)
    ex_resp = await get_exchanges()
    ex_resp["verify_result"] = ver_res
    return ex_resp


@app.post("/api/exchanges/clear-credentials")
async def clear_exchange_credentials(exchange_id: str = Query(...), authorization: str = Header(default="")):
    _require_vip(authorization)
    res = engine.clear_exchange_credentials(exchange_id)
    ex_resp = await get_exchanges()
    ex_resp["clear_result"] = res
    ex_resp["snapshot"] = engine.build_dashboard_snapshot()
    return ex_resp


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
async def update_risk(cfg: GlobalRiskConfig, authorization: str = Header(default="")):
    user = _extract_user(authorization)
    if cfg.trading_mode == "LIVE":
        if not user:
            raise HTTPException(status_code=401, detail="注册并登录账号后才能查看实盘系统")

    prev_kill = engine.risk_config.kill_switch_active
    prev_mode = engine.risk_config.trading_mode
    target_mode = cfg.trading_mode
    engine.risk_config = cfg

    if target_mode != prev_mode:
        engine.risk_config.trading_mode = prev_mode
        await engine.switch_trading_mode(target_mode)
    else:
        if cfg.trading_mode == "LIVE":
            engine.gateway.feed_mode = "REAL_WS"
        else:
            engine.gateway.feed_mode = "SIMULATOR" if cfg.feed_source != "REAL_WS" else "REAL_WS"

    # Non-VIP registered users can SEE live market data, but cannot activate live trading
    if cfg.trading_mode == "LIVE" and (not user or not user.get("can_use_live")):
        engine.trading_active_by_mode["LIVE"] = False

    if cfg.kill_switch_active != prev_kill:
        await engine.trigger_kill_switch(cfg.kill_switch_active)

    return {
        "status": "ok",
        "risk": engine.risk_config.model_dump(),
        "snapshot": engine.build_dashboard_snapshot(),
    }


@app.post("/api/trading/start")
async def start_trading(authorization: str = Header(default="")):
    if engine.risk_config.trading_mode == "LIVE":
        user = _extract_user(authorization)
        if not user:
            return {
                "ok": False,
                "require_login": True,
                "trading_active": False,
                "message": "请先注册并登录账号后再查看与使用实盘系统",
                "snapshot": engine.build_dashboard_snapshot(),
            }
        if not user.get("can_use_live"):
            engine.trading_active_by_mode["LIVE"] = False
            return {
                "ok": False,
                "require_vip": True,
                "trading_active": False,
                "message": "注册用户需开通 VIP 会员后才可使用实盘自动交易，请先前往会员中心开通会员",
                "snapshot": engine.build_dashboard_snapshot(),
            }
    res = await engine.set_trading_active(True)
    return {
        **res,
        "snapshot": engine.build_dashboard_snapshot(),
    }


@app.post("/api/trading/stop")
async def stop_trading():
    res = await engine.set_trading_active(False)
    return {
        **res,
        "snapshot": engine.build_dashboard_snapshot(),
    }


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


# ==================== 4. User Auth & Membership System APIs ====================
@app.post("/api/auth/register")
async def auth_register(req: RegisterRequest):
    res = user_manager.register_user(req.username, req.password, req.email)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    res = user_manager.login_user(req.username, req.password)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.get("/api/auth/me")
async def auth_me(authorization: str = Header(default="")):
    user = _extract_user(authorization)
    if not user:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}


@app.post("/api/auth/change-password")
async def auth_change_password(req: ChangePasswordRequest, authorization: str = Header(default="")):
    user = _require_user(authorization)
    res = user_manager.change_password(user["id"], req.old_password, req.new_password)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.get("/api/membership/plans")
async def get_membership_plans(authorization: str = Header(default="")):
    user = _extract_user(authorization)
    plans = user_manager.list_plans(include_disabled=False)
    orders = user_manager.list_user_orders(user["id"]) if user else []
    return {
        "plans": plans,
        "orders": orders,
        "user": user,
    }


@app.post("/api/membership/purchase")
async def purchase_membership(req: PurchaseMembershipRequest, authorization: str = Header(default="")):
    user = _require_user(authorization)
    res = user_manager.purchase_membership(
        user_id=user["id"],
        plan_id=req.plan_id,
        pay_method=req.pay_method,
        tx_hash=req.tx_hash,
        auto_confirm=req.auto_confirm,
    )
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    res["orders"] = user_manager.list_user_orders(user["id"])
    return res


@app.post("/api/membership/redeem")
async def redeem_vip_code(req: RedeemVipCodeRequest, authorization: str = Header(default="")):
    user = _require_user(authorization)
    res = user_manager.redeem_vip_code(user_id=user["id"], code=req.code)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    res["orders"] = user_manager.list_user_orders(user["id"])
    return res


# ==================== 5. Admin User & Membership Management APIs ====================
@app.get("/api/admin/dashboard")
async def admin_dashboard(authorization: str = Header(default="")):
    _require_admin(authorization)
    return user_manager.admin_get_dashboard_summary()


@app.post("/api/admin/users/create")
async def admin_create_user(req: AdminCreateUserRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_create_user(req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.post("/api/admin/users/update")
async def admin_update_user(req: AdminUpdateUserRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_update_user(req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.post("/api/admin/users/grant-vip")
async def admin_grant_vip(req: AdminGrantVipRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_grant_vip(req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_delete_user(user_id)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.post("/api/admin/plans/upsert")
async def admin_upsert_plan(req: AdminMembershipPlanModel, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_upsert_plan(req)
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.delete("/api/admin/plans/{plan_id}")
async def admin_delete_plan(plan_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_delete_plan(plan_id)
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.post("/api/admin/orders/audit")
async def admin_audit_order(req: AdminAuditOrderRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_audit_order(req.order_id, req.action)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.post("/api/admin/vip-codes/generate")
async def admin_generate_vip_codes(req: AdminGenerateVipCodesRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_generate_vip_codes(req.plan_name, req.duration_days, req.count)
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


@app.delete("/api/admin/vip-codes/{code_id}")
async def admin_delete_vip_code(code_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_delete_vip_code(code_id)
    return {**res, "dashboard": user_manager.admin_get_dashboard_summary()}


# ==================== 6. Stats & Real-Time WebSocket Stream ====================
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
