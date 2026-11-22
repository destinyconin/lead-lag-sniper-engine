"""
FastAPI Backend Server for the Lead-Lag Bidirectional Sniper System.
Exposes REST configuration/control endpoints and a disconnect-safe WebSocket stream (`/ws/dashboard`).
"""
import asyncio
import ipaddress
import math
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
import orjson
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.crypto_utils import vault
from backend.database import AsyncDatabaseManager
from backend.engine_manager import EngineManager
from backend.market_gateway import (
    BASE_PRICES,
    BINANCE_1000X_BASES,
    SP500_SYMBOL_NAMES,
    _BINANCE_INSTRUMENT_CACHE,
    _OKX_INSTRUMENT_CACHE,
    get_binance_instrument_spec,
    get_default_base_price,
    get_okx_instrument_spec,
    to_okx_inst_id,
)
from backend.models import (
    ALL_EXCHANGE_IDS,
    TRADITIONAL_BROKER_IDS,
    AdminAuditOrderRequest,
    AdminCreateUserRequest,
    AdminGenerateVipCodesRequest,
    AdminGrantVipRequest,
    AdminMembershipPlanModel,
    AdminUpdateUserRequest,
    BatchSymbolUpdateRequest,
    ChangePasswordRequest,
    CheckPaymentOrderRequest,
    ExchangeConfigModel,
    GlobalRiskConfig,
    LoginRequest,
    PurchaseMembershipRequest,
    PulseInjectionRequest,
    RedeemVipCodeRequest,
    RegisterRequest,
    SymbolStrategy,
    is_grid_strategy,
    is_traditional_broker,
)
from backend.payment_monitor import OnChainPaymentMonitor
from backend.sniper_engine import BidirectionalSniperEngine
from backend.user_membership import rate_limiter, user_manager


db_manager = AsyncDatabaseManager()
engine_manager = EngineManager(db_manager, get_user_fn=user_manager.get_user_by_id)
payment_monitor = OnChainPaymentMonitor(user_manager)

TRUSTED_PROXY_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
SYMBOL_FORMAT_RE = re.compile(r"^[A-Z0-9]{1,20}/USDT:USDT$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    user_manager.init_sync()
    payment_monitor.start()
    await engine_manager.start()
    yield
    await payment_monitor.stop()
    await engine_manager.stop()


def _extract_client_ip(request: Request) -> str:
    """Extracts verified client IP, trusting proxy headers ONLY from local trusted reverse proxies."""
    peer_host = (request.client.host if request.client else "").strip()
    if peer_host.startswith("::ffff:"):
        peer_host = peer_host[7:]
    candidate = peer_host
    if peer_host in TRUSTED_PROXY_HOSTS:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            candidate = xff.split(",")[-1].strip()
        elif request.headers.get("x-real-ip", "").strip():
            candidate = request.headers.get("x-real-ip", "").strip()
    if candidate.startswith("::ffff:"):
        candidate = candidate[7:]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer_host or "unknown"


def _extract_user(authorization: str = "") -> Optional[Dict[str, Any]]:
    if not authorization:
        return None
    parts = authorization.strip().split()
    token = parts[-1] if parts else ""
    return user_manager.get_user_from_token(token)


def _require_user(authorization: str = "") -> Dict[str, Any]:
    u = _extract_user(authorization)
    if not u:
        raise HTTPException(status_code=401, detail="请先注册并登录账号后再查看或操作个人实盘系统")
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


async def _resolve_user_engine(authorization: str = "") -> tuple[Optional[Dict[str, Any]], BidirectionalSniperEngine]:
    u = _extract_user(authorization)
    if u:
        eng = await engine_manager.get_user_engine(int(u["id"]))
        return u, eng
    return None, engine_manager.get_guest_engine()


async def _require_user_engine(authorization: str = "") -> tuple[Dict[str, Any], BidirectionalSniperEngine]:
    u = _require_user(authorization)
    eng = await engine_manager.get_user_engine(int(u["id"]))
    return u, eng


async def _require_vip_engine(authorization: str = "") -> tuple[Dict[str, Any], BidirectionalSniperEngine]:
    u = _require_vip(authorization)
    eng = await engine_manager.get_user_engine(int(u["id"]))
    return u, eng


app = FastAPI(
    title="Lead-Lag Bidirectional Sniper Engine (v2.0 Multi-Tenant Hardened)",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


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
    **{k: "标普500" for k in SP500_SYMBOL_NAMES.keys()},
}

SP500_ALIASES: Dict[str, str] = {
    "S&P500": "SPX",
    "S&P": "SPX",
    "SPX500": "SPX",
    "GSPC": "SPX",
    "US500": "SPX",
    "BRK.B": "BRKB",
    "BRK-B": "BRKB",
    "BRK/B": "BRKB",
    "GOOG": "GOOGL",
}


def build_perp_catalog():
    catalog = []
    for sym, px in BASE_PRICES.items():
        base = sym.split("/")[0]
        p_prec, q_prec, min_q = _infer_precision(px)
        sector = SECTOR_MAP.get(base, "高弹性Meme与中小市值")
        is_sp500 = sector == "标普500"
        if is_sp500:
            ex_list = ["futu", "moomoo", "ibkr", "schwab", "okx"]
            if base in ("NVDA", "TSLA", "SNDK", "SPCX", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "COIN", "MSTR"):
                ex_list = ["okx", "futu", "moomoo", "ibkr", "schwab", "bybit", "gate", "bitget"]
        elif base in ("BTC", "ETH", "SOL"):
            ex_list = ["binanceusdm", "okx", "bybit", "gate", "bitget", "krakenfutures", "futu", "moomoo", "ibkr", "schwab"]
        else:
            ex_list = ["binanceusdm", "okx", "bybit", "gate", "bitget", "krakenfutures"]
        catalog.append(
            {
                "symbol": sym,
                "base": base,
                "name_cn": SP500_SYMBOL_NAMES.get(base, ""),
                "sector": sector,
                "ref_price": px,
                "price_prec": p_prec,
                "qty_prec": q_prec,
                "min_qty": min_q,
                "exchanges": ex_list,
            }
        )
    return catalog


SUPPORTED_PERP_CATALOG = build_perp_catalog()

VIP_FEE_TIERS: Dict[str, Dict[str, tuple[float, float]]] = {
    "binanceusdm": {
        "VIP 0": (0.00050, 0.00020),
        "VIP 1": (0.00040, 0.00018),
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
    "bybit": {
        "VIP 0": (0.00055, 0.00020),
        "VIP 1": (0.00040, 0.00018),
        "VIP 2": (0.00035, 0.00014),
        "VIP 3": (0.00030, 0.00012),
    },
    "gate": {
        "VIP 0": (0.00050, 0.00020),
        "VIP 1": (0.00050, 0.00018),
        "VIP 2": (0.00050, 0.00015),
        "VIP 3": (0.00045, 0.00012),
    },
    "bitget": {
        "VIP 0": (0.00060, 0.00020),
        "VIP 1": (0.00050, 0.00018),
        "VIP 2": (0.00045, 0.00014),
        "VIP 3": (0.00040, 0.00012),
    },
    "krakenfutures": {
        "Tier 1": (0.00050, 0.00020),
        "Tier 2": (0.00040, 0.00015),
        "VIP 1": (0.00040, 0.00015),
    },
    "futu": {
        "美股标准": (0.00030, 0.00010),
        "VIP 1": (0.00025, 0.00008),
    },
    "moomoo": {
        "US Tier 1": (0.00030, 0.00010),
        "VIP 1": (0.00020, 0.00005),
    },
    "ibkr": {
        "IBKR Pro": (0.00020, 0.00008),
        "IBKR Lite": (0.00015, 0.00005),
        "VIP 1": (0.00018, 0.00006),
    },
    "schwab": {
        "Zero-Comm": (0.00010, 0.00005),
        "VIP 1": (0.00010, 0.00005),
    },
}


# ==================== 1. Exchange Connector APIs (Strictly Per-User Isolated) ====================
def _build_exchanges_payload(user_id: int, eng: BidirectionalSniperEngine) -> Dict[str, Any]:
    rows = db_manager.load_exchanges_sync(user_id=user_id)
    sanitized = []
    for r in rows:
        ex_id = r["exchange_id"]
        health = eng.gateway.exchange_health.get(
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


@app.get("/api/exchanges")
async def get_exchanges(authorization: str = Header(default="")):
    user, eng = await _require_user_engine(authorization)
    return _build_exchanges_payload(int(user["id"]), eng)


@app.post("/api/exchanges/update")
async def update_exchange(cfg: ExchangeConfigModel, authorization: str = Header(default="")):
    user, eng = await _require_vip_engine(authorization)
    uid = int(user["id"])
    db_manager.save_exchange_sync(cfg, user_id=uid)
    for ex in db_manager.load_exchanges_sync(user_id=uid):
        eng.exchanges[ex["exchange_id"]] = ex

    ver_res = await eng.verify_and_update_exchange(cfg.exchange_id, auto_upgrade_okx_mode=True)
    ex_resp = _build_exchanges_payload(uid, eng)
    ex_resp["verify_result"] = ver_res
    return ex_resp


@app.post("/api/exchanges/verify")
async def verify_exchange_api(exchange_id: str = Query(...), authorization: str = Header(default="")):
    user, eng = await _require_vip_engine(authorization)
    ver_res = await eng.verify_and_update_exchange(exchange_id, auto_upgrade_okx_mode=True)
    ex_resp = _build_exchanges_payload(int(user["id"]), eng)
    ex_resp["verify_result"] = ver_res
    return ex_resp


@app.post("/api/exchanges/switch-account-mode")
async def switch_exchange_account_mode(
    exchange_id: str = Query(default="okx"),
    acct_lv: str = Query(default="2"),
    authorization: str = Header(default=""),
):
    user, eng = await _require_vip_engine(authorization)
    res = await eng.switch_okx_account_mode(exchange_id=exchange_id, target_level=acct_lv)
    ex_resp = _build_exchanges_payload(int(user["id"]), eng)
    ex_resp["switch_result"] = res
    ex_resp["snapshot"] = eng.build_dashboard_snapshot()
    return ex_resp


@app.post("/api/exchanges/preflight")
async def preflight_exchange_orders(authorization: str = Header(default="")):
    _, eng = await _require_user_engine(authorization)
    return await eng.run_preflight_check()


@app.post("/api/exchanges/clear-credentials")
async def clear_exchange_credentials(exchange_id: str = Query(...), authorization: str = Header(default="")):
    user, eng = await _require_user_engine(authorization)
    res = eng.clear_exchange_credentials(exchange_id)
    ex_resp = _build_exchanges_payload(int(user["id"]), eng)
    ex_resp["clear_result"] = res
    ex_resp["snapshot"] = eng.build_dashboard_snapshot()
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
async def get_symbols(authorization: str = Header(default="")):
    _, eng = await _resolve_user_engine(authorization)
    return {
        "strategies": [cfg.model_dump() for cfg in eng.strategies.values()],
        "catalog": SUPPORTED_PERP_CATALOG,
    }


@app.get("/api/symbol/verify")
async def verify_symbol(
    symbol: str = Query(...),
    exchange_id: str = Query(default=""),
    authorization: str = Header(default=""),
):
    _, eng = await _resolve_user_engine(authorization)
    leader, follower = eng.get_leader_And_follower_cfg()
    req_ex_id = (exchange_id or "").strip().lower()
    if req_ex_id in ("all", ""):
        req_ex_id = ""
    raw = (symbol or "").strip().upper()
    for prefix in ("US.", "HK.", "$", "^"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    if raw in SP500_ALIASES:
        raw = SP500_ALIASES[raw]
    raw = raw.replace("-SWAP", "").replace("-PERP", "").replace("_", "/")
    if raw in SP500_ALIASES:
        raw = SP500_ALIASES[raw]
    if "-" in raw and "/" not in raw:
        raw = raw.replace("-", "/")
    if ":" in raw:
        raw = raw.split(":")[0]
    if "/" in raw:
        base_clean = raw.split("/")[0].strip()
    else:
        base_clean = raw[:-4].strip() if raw.endswith("USDT") and len(raw) > 4 else raw.strip()
    base_clean = SP500_ALIASES.get(base_clean, base_clean).replace(".", "")

    if base_clean.startswith("1000") and base_clean[4:] in BINANCE_1000X_BASES:
        base_clean = base_clean[4:]

    if not base_clean or base_clean == "USDT" or not re.match(r"^[A-Z0-9]{1,20}$", base_clean):
        return {
            "symbol": symbol.strip().upper() or "--",
            "verified": False,
            "in_catalog": False,
            "error": "标的代号格式无效，请输入有效的合约或标普500股票代号（如 SPY、SPX、AAPL、NVDA、SUI、ARB）",
        }

    norm_sym = f"{base_clean}/USDT:USDT"
    already_monitored = norm_sym in eng.strategies
    match = next((item for item in SUPPORTED_PERP_CATALOG if item["symbol"] == norm_sym), None)
    okx_spec = get_okx_instrument_spec(norm_sym)
    bn_spec = get_binance_instrument_spec(norm_sym)
    is_sp500_sym = base_clean in SP500_SYMBOL_NAMES or (match and match.get("sector") == "标普500")
    is_trad_req = is_traditional_broker(req_ex_id)

    if _OKX_INSTRUMENT_CACHE and not okx_spec and match is None and not is_trad_req:
        return {
            "symbol": norm_sym,
            "verified": False,
            "in_catalog": False,
            "already_monitored": already_monitored,
            "leader_supported": bool(bn_spec and bn_spec.get("status") == "TRADING"),
            "follower_supported": False,
            "error": f"添加失败：执行端 OKX 未上线 {to_okx_inst_id(norm_sym)} 永续合约（若添加美股/标普500标的，请先在上方切换至富途/moomoo/IBKR/Schwab券商通道）",
        }

    if not is_trad_req and okx_spec and okx_spec.get("state") not in (None, "live"):
        return {
            "symbol": norm_sym,
            "verified": False,
            "in_catalog": False,
            "already_monitored": already_monitored,
            "leader_supported": bool(bn_spec and bn_spec.get("status") == "TRADING"),
            "follower_supported": False,
            "error": f"添加失败：OKX 合约 {to_okx_inst_id(norm_sym)} 当前状态为 {okx_spec.get('state')}，暂不可交易",
        }

    target_exchange_id = req_ex_id if req_ex_id in ALL_EXCHANGE_IDS else (
        "futu" if (is_sp500_sym and not okx_spec) else follower["exchange_id"]
    )

    ref_px = match["ref_price"] if match else get_default_base_price(norm_sym)
    f_bbo = eng.gateway.real_follower_bbo.get(norm_sym)
    if f_bbo and f_bbo[0] > 0 and f_bbo[1] > 0:
        ref_px = (f_bbo[0] + f_bbo[1]) * 0.5
    elif norm_sym in eng.math_states and eng.math_states[norm_sym].latest_follower:
        f_mid = eng.math_states[norm_sym].latest_follower.mid
        if f_mid > 0:
            ref_px = f_mid

    p_prec, q_prec, min_q = _infer_precision(ref_px)
    min_notional_usdt = 0.0
    if okx_spec and not is_traditional_broker(target_exchange_id):
        tick_str = str(okx_spec.get("tickSz_str") or "")
        if "." in tick_str:
            p_prec = min(12, max(p_prec, len(tick_str.rstrip("0").split(".")[1])))
        min_base = float(okx_spec.get("minSz") or 0.0) * float(okx_spec.get("ctVal") or 1.0)
        if min_base > 0:
            min_q = round(min_base, 8)
            min_notional_usdt = round(min_base * ref_px, 4)
    elif is_traditional_broker(target_exchange_id) or is_sp500_sym:
        p_prec = 2
        q_prec = 2
        min_q = 0.01

    leader_supported = (
        bool(bn_spec and bn_spec.get("status") == "TRADING")
        if (_BINANCE_INSTRUMENT_CACHE and not is_traditional_broker(target_exchange_id))
        else True
    )
    follower_supported = is_traditional_broker(target_exchange_id) or is_sp500_sym or okx_spec is None or okx_spec.get("state") == "live"

    # Infer sensible strategy & order_usdt defaults from the user's current portfolio
    existing = list(eng.strategies.values())
    grid_configs = [s for s in existing if is_grid_strategy(s.strategy_type)]
    if grid_configs and len(grid_configs) >= len(existing) / 2:
        rec_strategy = grid_configs[0].strategy_type
        base_order_usdt = float(grid_configs[0].order_usdt or 20.0)
    elif is_traditional_broker(target_exchange_id) or is_sp500_sym or not leader_supported:
        rec_strategy = "TREND_GRID" if (is_sp500_sym or is_traditional_broker(target_exchange_id)) else "LONG_SHORT_GRID"
        base_order_usdt = 20.0 if (is_sp500_sym or is_traditional_broker(target_exchange_id)) else 10.0
    else:
        rec_strategy = "LEAD_LAG"
        base_order_usdt = float(existing[0].order_usdt if existing else 300.0)

    if min_notional_usdt > base_order_usdt * 1.35:
        rec_order_usdt = float(math.ceil(min_notional_usdt))
    else:
        rec_order_usdt = base_order_usdt

    warnings = []
    if is_sp500_sym or is_traditional_broker(target_exchange_id):
        sp_title = SP500_SYMBOL_NAMES.get(base_clean, base_clean)
        warnings.append(f"标普500/美股标的 {sp_title} 已绑定执行通道 [{target_exchange_id.upper()}]")
    elif _BINANCE_INSTRUMENT_CACHE and not leader_supported:
        warnings.append(f"{norm_sym} 在主所 Binance 未上线，已自动设为 OKX「多空动态网格」策略")
    if min_notional_usdt > base_order_usdt * 1.35:
        warnings.append(
            f"{norm_sym} 在 OKX 最小下单量 ({okx_spec.get('minSz')} 张) 约需 {min_notional_usdt:.2f} USDT，已自动调整建议下单额为 {rec_order_usdt:g} USDT"
        )

    return {
        "symbol": norm_sym,
        "base": base_clean,
        "name_cn": SP500_SYMBOL_NAMES.get(base_clean, ""),
        "sector": "标普500" if is_sp500_sym else (match["sector"] if match else "自定义标的"),
        "verified": True,
        "already_monitored": already_monitored,
        "in_catalog": match is not None or okx_spec is not None or is_trad_req,
        "ref_price": ref_px,
        "min_notional_usdt": min_notional_usdt,
        "leader_supported": leader_supported,
        "follower_supported": follower_supported,
        "leader_exchange": leader["exchange_id"],
        "follower_exchange": target_exchange_id,
        "recommended_exchange_id": target_exchange_id,
        "okx_spec": okx_spec,
        "recommended_strategy_type": rec_strategy,
        "recommended_order_usdt": rec_order_usdt,
        "warning": "；".join(warnings) if warnings else "",
        "recommended_precision": {
            "price_precision": min(12, match["price_prec"] if (match and not okx_spec) else p_prec),
            "qty_precision": min(12, match["qty_prec"] if match else q_prec),
            "min_qty": min_q,
        },
    }


@app.post("/api/symbol/update")
async def update_symbol(cfg: SymbolStrategy, authorization: str = Header(default="")):
    _, eng = await _require_vip_engine(authorization)
    cfg.symbol = cfg.symbol.strip().upper()
    if not SYMBOL_FORMAT_RE.match(cfg.symbol):
        raise HTTPException(status_code=400, detail="合约代号格式无效，应为例如 BTC/USDT:USDT")
    cfg.price_precision = min(12, max(0, int(cfg.price_precision)))
    cfg.qty_precision = min(12, max(0, int(cfg.qty_precision)))
    try:
        eng.add_or_update_symbol(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    warnings = []
    okx_spec = get_okx_instrument_spec(cfg.symbol)
    if okx_spec:
        f_bbo = eng.gateway.real_follower_bbo.get(cfg.symbol)
        live_px = (f_bbo[0] + f_bbo[1]) * 0.5 if (f_bbo and f_bbo[0] > 0 and f_bbo[1] > 0) else 0.0
        if live_px <= 0 and cfg.symbol in eng.math_states and eng.math_states[cfg.symbol].latest_follower:
            live_px = eng.math_states[cfg.symbol].latest_follower.mid
        if live_px > 0:
            min_base = float(okx_spec.get("minSz") or 0.0) * float(okx_spec.get("ctVal") or 1.0)
            min_notional = min_base * live_px
            if min_notional > cfg.order_usdt * 1.35:
                warnings.append(
                    f"OKX 该合约最小委托 ({okx_spec.get('minSz')} 张) 约需 {min_notional:.2f} USDT，当前 {cfg.order_usdt:g} USDT 实盘下单可能被拒，建议调至 ≥ {math.ceil(min_notional)} USDT"
                )
    if cfg.strategy_type == "LEAD_LAG" and _BINANCE_INSTRUMENT_CACHE and not get_binance_instrument_spec(cfg.symbol):
        warnings.append("主信号源 Binance 未上线该合约，脉冲狙击无主所信号，建议切换为「多空动态网格 (GRID)」策略")

    return {
        "status": "ok",
        "warning": "；".join(warnings) if warnings else "",
        "strategies": [s.model_dump() for s in eng.strategies.values()],
    }


@app.post("/api/symbols/batch-update")
async def batch_update_symbols(req: BatchSymbolUpdateRequest, authorization: str = Header(default="")):
    if req.action == "apply":
        _, eng = await _require_vip_engine(authorization)
    else:
        _, eng = await _require_user_engine(authorization)
    res = eng.validate_and_batch_update_symbols(req)
    return {
        **res,
        "strategies": [s.model_dump() for s in eng.strategies.values()],
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/symbol/grid-recenter")
async def recenter_symbol_grid(symbol: str = Query(...), authorization: str = Header(default="")):
    _, eng = await _require_vip_engine(authorization)
    ok, msg = eng.recenter_symbol_grid(symbol)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "snapshot": eng.build_dashboard_snapshot()}


@app.post("/api/positions/sync")
async def sync_positions(authorization: str = Header(default="")):
    _, eng = await _require_user_engine(authorization)
    sync_res = await eng.sync_live_exchange_positions(trigger="manual")
    return {
        **sync_res,
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/positions/close")
async def close_position(payload: Dict[str, Any], authorization: str = Header(default="")):
    # Allow any authenticated user (including expired VIPs!) to close their open exchange positions safely
    _, eng = await _require_user_engine(authorization)
    symbol = str(payload.get("symbol") or "").strip()
    side = str(payload.get("side") or "LONG").strip().upper()
    slot_id = payload.get("slot_id")
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少合约代号")
    if side not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="无效持仓方向，应为 LONG 或 SHORT")
    res = await eng.manual_close_position(symbol=symbol, side=side, slot_id=slot_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message", "平仓失败"))
    return {
        **res,
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/positions/adopt-grid")
async def adopt_position_grid(payload: Dict[str, Any], authorization: str = Header(default="")):
    _, eng = await _require_vip_engine(authorization)
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少合约代号")
    res = await eng.adopt_exchange_position_into_grid(symbol)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message", "纳入策略失败"))
    return {
        **res,
        "strategies": [s.model_dump() for s in eng.strategies.values()],
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.delete("/api/symbol/{symbol:path}")
async def delete_symbol(symbol: str, authorization: str = Header(default="")):
    _, eng = await _require_vip_engine(authorization)
    tracker = eng.grid_trackers.get(symbol)
    has_grid_pos = any(k.startswith(f"{symbol}:") for k in eng.active_grid_positions.keys()) or bool(
        tracker and tracker._has_active_slots()
    )
    if has_grid_pos or eng.symbol_states.get(symbol) not in (None, "IDLE"):
        raise HTTPException(status_code=409, detail="该合约当前存在在途持仓或活跃状态，无法移除")
    eng.remove_symbol(symbol)
    return {"status": "ok", "strategies": [s.model_dump() for s in eng.strategies.values()]}


# ==================== 3. Risk Control & Pulse Testing APIs ====================
@app.get("/api/risk")
async def get_risk(authorization: str = Header(default="")):
    user, eng = await _resolve_user_engine(authorization)
    events = db_manager.load_risk_events_sync(limit=40, user_id=int(user["id"])) if user else []
    return {
        "risk": eng.risk_config.model_dump(),
        "events": events,
    }


@app.post("/api/mode/enter-live")
async def enter_live_mode(authorization: str = Header(default="")):
    """
    Automatically called when any registered user logs in or opens the console.
    Switches THAT user's isolated engine into LIVE mode (real exchange BBOs) without
    affecting any other tenant or stopping an already-running LIVE session.
    """
    user, eng = await _require_user_engine(authorization)
    if eng.risk_config.trading_mode != "LIVE":
        await eng.switch_trading_mode("LIVE", preserve_user_started=True)
    if user and user.get("can_use_live") and not eng.exchange_live_positions:
        try:
            await eng.sync_live_exchange_positions(trigger="enter_live")
        except Exception:
            pass
    eng.persist_runtime_state()
    return {
        "status": "ok",
        "risk": eng.risk_config.model_dump(),
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/risk/update")
async def update_risk(cfg: GlobalRiskConfig, authorization: str = Header(default="")):
    user, eng = await _require_user_engine(authorization)

    # Registered users are strictly locked to LIVE mode
    cfg.trading_mode = "LIVE"

    # Only VIP / Admin users can modify risk parameters on the server
    if not user.get("can_use_live"):
        if eng.risk_config.trading_mode != "LIVE":
            await eng.switch_trading_mode("LIVE", preserve_user_started=True)
        return {
            "status": "ok",
            "risk": eng.risk_config.model_dump(),
            "snapshot": eng.build_dashboard_snapshot(),
        }

    prev_kill = eng.risk_config.kill_switch_active
    prev_mode = eng.risk_config.trading_mode
    # Never allow stale risk form saves to silently disengage an active emergency Kill Switch!
    cfg.kill_switch_active = prev_kill
    eng.risk_config = cfg

    if prev_mode != "LIVE":
        eng.risk_config.trading_mode = prev_mode
        await eng.switch_trading_mode("LIVE", preserve_user_started=True)
    else:
        eng._sync_gateway_subscription()

    eng.persist_runtime_state()
    return {
        "status": "ok",
        "risk": eng.risk_config.model_dump(),
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/trading/start")
async def start_trading(authorization: str = Header(default="")):
    user = _extract_user(authorization)
    if not user:
        guest_eng = engine_manager.get_guest_engine()
        return {
            "ok": False,
            "require_login": True,
            "trading_active": False,
            "message": "请先注册并登录账号后再使用个人实盘系统",
            "snapshot": guest_eng.build_public_preview_snapshot(),
        }
    eng = await engine_manager.get_user_engine(int(user["id"]))
    if not user.get("can_use_live"):
        eng.trading_active_by_mode["LIVE"] = False
        eng.persist_runtime_state()
        return {
            "ok": False,
            "require_vip": True,
            "trading_active": False,
            "message": "注册用户需开通 VIP 会员后才可使用实盘自动交易，请先前往会员中心开通会员",
            "snapshot": eng.build_dashboard_snapshot(),
        }
    res = await eng.set_trading_active(True)
    return {
        **res,
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/trading/stop")
async def stop_trading(authorization: str = Header(default="")):
    # Allow any authenticated user (including expired VIPs!) to stop automated trading safely
    _, eng = await _require_user_engine(authorization)
    res = await eng.set_trading_active(False)
    return {
        **res,
        "snapshot": eng.build_dashboard_snapshot(),
    }


@app.post("/api/risk/kill-switch")
async def toggle_kill_switch(active: bool = Query(...), authorization: str = Header(default="")):
    # Activating emergency Kill Switch must always work even if VIP expired; releasing requires VIP
    if active:
        _, eng = await _require_user_engine(authorization)
    else:
        _, eng = await _require_vip_engine(authorization)
    await eng.trigger_kill_switch(active)
    return {
        "status": "ok",
        "kill_switch_active": eng.risk_config.kill_switch_active,
        "risk": eng.risk_config.model_dump(),
    }


@app.post("/api/pulse/inject")
async def inject_pulse(req: PulseInjectionRequest, request: Request, authorization: str = Header(default="")):
    client_ip = _extract_client_ip(request)
    allowed, retry = rate_limiter.check_and_record(f"pulse:{client_ip}", max_hits=10, window_sec=60.0)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"操作过于频繁，请在 {retry} 秒后重试")
    user, eng = await _resolve_user_engine(authorization)
    if user is not None:
        raise HTTPException(status_code=400, detail="已登录实盘通道禁止注入模拟脉冲数据")
    if req.symbol not in eng.strategies and req.symbol not in BASE_PRICES:
        raise HTTPException(status_code=400, detail="无效的模拟合约代号")
    ok, msg = eng.inject_test_pulse(req)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg, "injected": req.model_dump()}


# ==================== 4. User Auth & Membership System APIs ====================
@app.post("/api/auth/register")
async def auth_register(req: RegisterRequest, request: Request):
    client_ip = _extract_client_ip(request)
    allowed, retry = rate_limiter.check_and_record(f"reg:{client_ip}", max_hits=10, window_sec=300.0)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"注册请求过于频繁，请在 {retry} 秒后重试")
    res = await asyncio.to_thread(user_manager.register_user, req.username, req.password, req.email, client_ip)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    client_ip = _extract_client_ip(request)
    allowed, retry = rate_limiter.check_and_record(f"login:{client_ip}", max_hits=20, window_sec=300.0)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"登录尝试过于频繁，请在 {retry} 秒后重试")
    uname_key = req.username.strip().lower()
    u_ok, u_retry = rate_limiter.check_and_record(f"login_u:{uname_key}", max_hits=10, window_sec=300.0)
    if not u_ok:
        raise HTTPException(status_code=429, detail=f"该账号登录尝试过于频繁，请在 {u_retry} 秒后重试")
    res = await asyncio.to_thread(user_manager.login_user, req.username, req.password, client_ip)
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
    allowed, retry = rate_limiter.check_and_record(f"chpwd:{user['id']}", max_hits=8, window_sec=300.0)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"改密操作过于频繁，请在 {retry} 秒后重试")
    res = await asyncio.to_thread(user_manager.change_password, user["id"], req.old_password, req.new_password)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.get("/api/membership/plans")
async def get_membership_plans(authorization: str = Header(default="")):
    user = _extract_user(authorization)
    plans = user_manager.list_plans(include_disabled=False)
    orders = user_manager.list_user_orders(user["id"]) if user else []
    active_order = next((o for o in orders if o.get("status") == "PENDING" and o.get("remaining_seconds", 0) > 0), None)
    return {
        "plans": plans,
        "orders": orders,
        "active_order": active_order,
        "payment_info": user_manager.get_payment_metadata(),
        "user": user,
    }


@app.get("/api/membership/payment-info")
async def get_payment_info(authorization: str = Header(default="")):
    user = _extract_user(authorization)
    orders = user_manager.list_user_orders(user["id"]) if user else []
    active_order = next((o for o in orders if o.get("status") == "PENDING" and o.get("remaining_seconds", 0) > 0), None)
    return {
        "payment_info": user_manager.get_payment_metadata(),
        "active_order": active_order,
    }


@app.post("/api/membership/purchase")
async def purchase_membership(req: PurchaseMembershipRequest, authorization: str = Header(default="")):
    user = _require_user(authorization)
    if req.force_new:
        allowed, retry = rate_limiter.check_and_record(f"purch_new:{user['id']}", max_hits=8, window_sec=3600.0)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"频繁重置订单金额受限，请在 {retry} 秒后重试")
    res = await asyncio.to_thread(
        user_manager.create_web3_payment_order,
        user["id"],
        req.plan_id,
        req.chain_network,
        req.force_new,
    )
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    payment_monitor.trigger_wake()
    res["orders"] = await asyncio.to_thread(user_manager.list_user_orders, user["id"])
    return res


@app.get("/api/membership/orders/{order_no}")
async def get_membership_order_status(order_no: str, authorization: str = Header(default="")):
    user = _require_user(authorization)
    order = user_manager.get_order_by_no(user["id"], order_no)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    refreshed_user = user_manager.get_user_by_id(user["id"])
    return {
        "ok": True,
        "order": order,
        "paid": order["status"] == "PAID",
        "expired": order["status"] in ("EXPIRED", "CANCELLED", "REJECTED"),
        "user": refreshed_user,
        "orders": user_manager.list_user_orders(user["id"]),
    }


@app.post("/api/membership/orders/{order_no}/check")
async def check_membership_order_onchain(
    order_no: str,
    req: CheckPaymentOrderRequest,
    authorization: str = Header(default=""),
):
    user = _require_user(authorization)
    order = user_manager.get_order_by_no(user["id"], order_no)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    if order["status"] == "PENDING":
        allowed, retry = rate_limiter.check_and_record(f"chk:{user['id']}", max_hits=12, window_sec=60.0)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"链上查账过于频繁，请等待 {retry} 秒后重试")
        await payment_monitor.scan_once(optional_tx_hash=req.tx_hash)
        order = user_manager.get_order_by_no(user["id"], order_no)

    refreshed_user = user_manager.get_user_by_id(user["id"])
    paid = order["status"] == "PAID"
    msg = (
        f"链上支付已确认到账（{order.get('chain_network')}）！已为您自动激活【{order['plan_name']}】"
        if paid
        else (
            "订单已超时自动关闭，请重新发起支付订单"
            if order["status"] == "EXPIRED"
            else f"已扫描全部 10 条公链最新区块，暂未检测到精确金额 {order['pay_amount_str']} 到账，请确认转账金额包含完整3位小数并等待区块确认"
        )
    )
    return {
        "ok": True,
        "paid": paid,
        "order": order,
        "message": msg,
        "user": refreshed_user,
        "orders": user_manager.list_user_orders(user["id"]),
    }


@app.post("/api/membership/orders/{order_no}/cancel")
async def cancel_membership_order(order_no: str, authorization: str = Header(default="")):
    user = _require_user(authorization)
    res = user_manager.cancel_user_order(user["id"], order_no)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    res["orders"] = user_manager.list_user_orders(user["id"])
    return res


@app.post("/api/membership/redeem")
async def redeem_vip_code(req: RedeemVipCodeRequest, authorization: str = Header(default="")):
    user = _require_user(authorization)
    allowed, retry = rate_limiter.check_and_record(f"redeem:{user['id']}", max_hits=10, window_sec=300.0)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"激活码兑换尝试过于频繁，请在 {retry} 秒后重试")
    res = user_manager.redeem_vip_code(user_id=user["id"], code=req.code)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    res["orders"] = user_manager.list_user_orders(user["id"])
    return res


# ==================== 5. Admin User & Membership Management APIs ====================
def _admin_summary() -> Dict[str, Any]:
    return user_manager.admin_get_dashboard_summary(runtime_status_map=engine_manager.get_runtime_status_map())


async def _admin_summary_async() -> Dict[str, Any]:
    return await asyncio.to_thread(_admin_summary)


@app.get("/api/admin/dashboard")
async def admin_dashboard(authorization: str = Header(default="")):
    _require_admin(authorization)
    return await _admin_summary_async()


@app.post("/api/admin/users/create")
async def admin_create_user(req: AdminCreateUserRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_create_user, req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": await _admin_summary_async()}


@app.post("/api/admin/users/update")
async def admin_update_user(req: AdminUpdateUserRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_update_user, req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    if req.is_active is False:
        await engine_manager.stop_user_engine(req.user_id)
    else:
        updated_u = await asyncio.to_thread(user_manager.get_user_by_id, req.user_id)
        if updated_u and not updated_u.get("can_use_live"):
            eng = engine_manager.user_engines.get(int(req.user_id))
            if eng and eng.trading_active_by_mode.get("LIVE"):
                await eng.set_trading_active(False)
    return {**res, "dashboard": await _admin_summary_async()}


@app.post("/api/admin/users/grant-vip")
async def admin_grant_vip(req: AdminGrantVipRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_grant_vip, req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    updated_u = await asyncio.to_thread(user_manager.get_user_by_id, req.user_id)
    if updated_u and not updated_u.get("can_use_live"):
        eng = engine_manager.user_engines.get(int(req.user_id))
        if eng and eng.trading_active_by_mode.get("LIVE"):
            await eng.set_trading_active(False)
            eng.append_log("WARN", "VIP_REVOKED", "[会员权限收回] 管理员已收回会员权限，实盘自动交易已立即暂停", mode="LIVE")
    return {**res, "dashboard": await _admin_summary_async()}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_delete_user, user_id)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    await engine_manager.stop_user_engine(user_id)
    return {**res, "dashboard": await _admin_summary_async()}


@app.post("/api/admin/plans/upsert")
async def admin_upsert_plan(req: AdminMembershipPlanModel, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_upsert_plan, req)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": await _admin_summary_async()}


@app.delete("/api/admin/plans/{plan_id}")
async def admin_delete_plan(plan_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_delete_plan, plan_id)
    return {**res, "dashboard": await _admin_summary_async()}


@app.post("/api/admin/orders/audit")
async def admin_audit_order(req: AdminAuditOrderRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = await asyncio.to_thread(user_manager.admin_audit_order, req.order_id, req.action, req.tx_hash)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return {**res, "dashboard": await _admin_summary_async()}


@app.post("/api/admin/vip-codes/generate")
async def admin_generate_vip_codes(req: AdminGenerateVipCodesRequest, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_generate_vip_codes(req.plan_name, req.duration_days, req.count)
    return {**res, "dashboard": _admin_summary()}


@app.delete("/api/admin/vip-codes/{code_id}")
async def admin_delete_vip_code(code_id: int, authorization: str = Header(default="")):
    _require_admin(authorization)
    res = user_manager.admin_delete_vip_code(code_id)
    return {**res, "dashboard": _admin_summary()}


# ==================== 6. Stats & Real-Time WebSocket Stream (Tenant-Isolated) ====================
@app.get("/api/stats")
async def get_stats(
    radar_symbol: Optional[str] = Query(default=None),
    authorization: str = Header(default=""),
):
    user, eng = await _resolve_user_engine(authorization)
    if user is None:
        return eng.build_public_preview_snapshot(radar_symbol=radar_symbol)
    return eng.build_dashboard_snapshot(radar_symbol=radar_symbol)


@app.post("/api/stats/reset")
async def reset_stats(
    radar_symbol: Optional[str] = Query(default=None),
    authorization: str = Header(default=""),
):
    _, eng = await _require_user_engine(authorization)
    res = eng.reset_statistics()
    return {
        **res,
        "snapshot": eng.build_dashboard_snapshot(radar_symbol=radar_symbol),
    }


@app.websocket("/ws/dashboard")
async def dashboard_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
    radar_symbol: str = Query(default=""),
):
    await websocket.accept()
    raw_token = (token or "").strip()
    if not raw_token:
        auth_hdr = websocket.headers.get("authorization", "")
        if auth_hdr:
            raw_token = auth_hdr.strip().split()[-1]

    user = user_manager.get_user_from_token(raw_token) if raw_token else None
    bound_uid = int(user["id"]) if user else 0
    ws_state = {"radar_symbol": (radar_symbol or "").strip()}
    tick_counter = 0
    reader_task: Optional[asyncio.Task] = None

    async def _recv_client_msgs():
        try:
            while True:
                raw_txt = await websocket.receive_text()
                try:
                    msg = orjson.loads(raw_txt)
                    if isinstance(msg, dict) and msg.get("type") == "select_symbol":
                        sym_val = str(msg.get("symbol") or "").strip()
                        if sym_val:
                            ws_state["radar_symbol"] = sym_val
                except Exception:
                    pass
        except Exception:
            pass

    try:
        if bound_uid > 0:
            eng = await engine_manager.get_user_engine(bound_uid)
        else:
            eng = engine_manager.get_guest_engine()

        reader_task = asyncio.create_task(_recv_client_msgs())

        while True:
            tick_counter += 1
            if bound_uid > 0 and tick_counter % 20 == 0:
                refreshed = await asyncio.to_thread(user_manager.get_user_from_token, raw_token)
                if not refreshed or not refreshed.get("is_active"):
                    bound_uid = 0
                    eng = engine_manager.get_guest_engine()
                elif bound_uid in engine_manager.user_engines:
                    eng = engine_manager.user_engines[bound_uid]

            target_radar = ws_state["radar_symbol"] or None
            if bound_uid > 0:
                snapshot = eng.build_dashboard_snapshot(radar_symbol=target_radar)
            else:
                snapshot = eng.build_public_preview_snapshot(radar_symbol=target_radar)

            await asyncio.wait_for(websocket.send_text(orjson.dumps(snapshot).decode("utf-8")), timeout=5.0)
            await asyncio.sleep(0.35)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if reader_task is not None and not reader_task.done():
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass


FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def serve_spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "API endpoint not found"}, status_code=404)
        index_file = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        return JSONResponse({"status": "Frontend not built yet"})
