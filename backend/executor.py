"""
Execution Layer: Token-Bucket Rate Limiter, Shadow (Paper) Execution Engine with
Realistic Latency Distribution & Adverse Selection, and Real OKX V5 / Binance USDM
Credential Verification & Live Order Execution Adapter.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import hmac
import math
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import httpx
import orjson
import websockets
from backend.market_gateway import (
    get_binance_instrument_spec,
    get_okx_instrument_spec,
    get_system_proxy,
    okx_inst_id_to_unified,
    to_okx_inst_id,
)
from backend.math_engine import SymbolMathState, mono_ms
from backend.models import GlobalRiskConfig, SymbolStrategy


OKX_ERROR_MAP = {
    "50001": "OKX 服务暂不可用，状态未决待对账",
    "50004": "OKX 接口请求超时，报单结果未决待对账",
    "50011": "OKX 请求频率超限 (Rate Limit / 429)",
    "50026": "OKX 系统繁忙，报单状态未决待对账",
    "50111": "API Key 无效或不存在 (Invalid OK-ACCESS-KEY)",
    "50113": "API Secret 密钥错误，签名校验失败 (Invalid Sign)",
    "50104": "缺少 API 密码 (Passphrase)：欧易官方接口强制要求填写创建该 API 时您自己设置的密码（不能为空）",
    "50105": "API 密码 (Passphrase) 错误：请填写您在欧易创建该 API 时自己设置的密码（若忘记需在欧易重新创建 API）",
    "50110": "当前服务器 IP 不在该 API Key 的白名单内",
    "50101": "API Key 环境不匹配 (实盘与欧易模拟盘密钥不能混用)",
    "50103": "欧易 V5 接口要求填写 Passphrase（即您在欧易 App 创建该 API 时自己设置的密码）",
    "51000": "参数或持仓模式不匹配 (请检查单向/双向持仓或逐/全仓设置)",
    "51008": "账户可用保证金余额不足 (Insufficient balance)",
    "51006": "委托价格超出当前合约限价范围或未按 tickSz 对齐",
    "51010": "当前账户模式禁止该操作（简单交易模式无法交易永续合约，请在 OKX 切换为单币种或跨币种保证金模式）",
    "51020": "下单数量小于该合约最小委托单位",
    "51023": "当前持仓不存在或保证金模式 (全仓/逐仓) 不匹配",
    "51054": "该账户暂不满足切换账户模式条件（请先在 OKX Web/App 完成合约开通与首次账户模式切换）",
    "51116": "委托价或触发价超出合约允许价格区间",
    "51121": "委托数量必须为 lotSz 步长的整数倍",
    "51169": "可平持仓数量不足（可能已被其他平仓挂单冻结）",
    "51170": "平仓挂单数量超过当前可平持仓",
    "51277": "止盈触发价需高于当前价格（多单）",
    "51278": "止损触发价需高于当前价格（空单）",
    "51279": "止盈触发价需低于当前价格（空单）",
    "51280": "止损触发价需低于当前价格（多单）",
    "51603": "订单不存在或已归档至历史订单",
    "59000": "存在未平仓合约或未撤挂单/条件单，OKX 禁止此时切换账户或持仓模式，请先全部平仓撤单",
    "59100": "需先在 OKX 网页或 App 端完成首次账户模式激活，此后可通过 API 自由切换",
}

_AMBIGUOUS_OKX_CODES = {"50001", "50004", "50026", "500", "502", "503", "504"}


# Static fallback only used if public OKX instruments API has not been cached yet
_FALLBACK_OKX_SPECS: Dict[str, dict] = {
    "BTC-USDT-SWAP": {"ctVal": 0.01, "ctVal_str": "0.01", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.1, "tickSz_str": "0.1"},
    "ETH-USDT-SWAP": {"ctVal": 0.1, "ctVal_str": "0.1", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.01, "tickSz_str": "0.01"},
    "SOL-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.01, "tickSz_str": "0.01"},
    "XRP-USDT-SWAP": {"ctVal": 100.0, "ctVal_str": "100", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "DOGE-USDT-SWAP": {"ctVal": 1000.0, "ctVal_str": "1000", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.00001, "tickSz_str": "0.00001"},
    "PEPE-USDT-SWAP": {"ctVal": 10000000.0, "ctVal_str": "10000000", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 1e-10, "tickSz_str": "0.0000000001"},
    "SHIB-USDT-SWAP": {"ctVal": 1000000.0, "ctVal_str": "1000000", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 1e-9, "tickSz_str": "0.000000001"},
    "BONK-USDT-SWAP": {"ctVal": 100000.0, "ctVal_str": "100000", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 1e-10, "tickSz_str": "0.0000000001"},
    "FLOKI-USDT-SWAP": {"ctVal": 100000.0, "ctVal_str": "100000", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 1e-8, "tickSz_str": "0.00000001"},
    "ADA-USDT-SWAP": {"ctVal": 100.0, "ctVal_str": "100", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "BNB-USDT-SWAP": {"ctVal": 0.01, "ctVal_str": "0.01", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.1, "tickSz_str": "0.1"},
    "LINK-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 0.001, "tickSz_str": "0.001"},
    "SEI-USDT-SWAP": {"ctVal": 10.0, "ctVal_str": "10", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "HYPE-USDT-SWAP": {"ctVal": 0.1, "ctVal_str": "0.1", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.001, "tickSz_str": "0.001"},
    "ZEC-USDT-SWAP": {"ctVal": 0.01, "ctVal_str": "0.01", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.01, "tickSz_str": "0.01"},
    "UNI-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.001, "tickSz_str": "0.001"},
    "SUI-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "ARB-USDT-SWAP": {"ctVal": 10.0, "ctVal_str": "10", "lotSz": 0.1, "lotSz_str": "0.1", "minSz": 0.1, "minSz_str": "0.1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "OP-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "WIF-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 1.0, "lotSz_str": "1", "minSz": 1.0, "minSz_str": "1", "tickSz": 0.0001, "tickSz_str": "0.0001"},
    "TSLA-USDT-SWAP": {"ctVal": 0.01, "ctVal_str": "0.01", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.01, "tickSz_str": "0.01"},
    "SPCX-USDT-SWAP": {"ctVal": 1.0, "ctVal_str": "1", "lotSz": 0.01, "lotSz_str": "0.01", "minSz": 0.01, "minSz_str": "0.01", "tickSz": 0.01, "tickSz_str": "0.01"},
}


def quantize_decimal_step(val: float, step_str: str, rounding: str = "down") -> Tuple[str, float]:
    """Quantizes a price or contract lot size to an exact Decimal multiple of tickSz/lotSz (immune to IEEE-754 underflow)."""
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return "0", 0.0
    try:
        d_step = Decimal(str(step_str or "0.0001"))
    except InvalidOperation:
        d_step = Decimal("0.0001")
    if d_step <= 0:
        d_step = Decimal("0.0001")
    try:
        d_val = Decimal(str(val))
    except InvalidOperation:
        return "0", 0.0
    if d_val <= 0:
        return "0", 0.0
    ratio = d_val / d_step
    if rounding == "down":
        # Snap IEEE-754 representation dust (e.g. 0.3+0.3+0.3=0.8999999999999999 -> 9.0 steps instead of 8!)
        nearest_int = ratio.to_integral_value(rounding=ROUND_HALF_UP)
        if abs(nearest_int - ratio) <= Decimal("1e-9"):
            steps = nearest_int
        else:
            steps = ratio.to_integral_value(rounding=ROUND_FLOOR)
    elif rounding == "up":
        nearest_int = ratio.to_integral_value(rounding=ROUND_HALF_UP)
        if abs(ratio - nearest_int) <= Decimal("1e-9"):
            steps = nearest_int
        else:
            steps = ratio.to_integral_value(rounding=ROUND_CEILING)
    else:
        steps = ratio.to_integral_value(rounding=ROUND_HALF_UP)
    q = (steps * d_step).quantize(d_step)
    text = format(q, "f")
    return text, float(q)


def resolve_okx_spec(symbol: str) -> dict:
    live = get_okx_instrument_spec(symbol)
    if live:
        return live
    inst_id = to_okx_inst_id(symbol)
    if inst_id in _FALLBACK_OKX_SPECS:
        fb = dict(_FALLBACK_OKX_SPECS[inst_id])
        fb["instId"] = inst_id
        fb["state"] = "live"
        fb["maxLever"] = 50.0
        return fb
    return {
        "instId": inst_id,
        "state": "unknown",
        "ctVal": 1.0,
        "ctVal_str": "1",
        "lotSz": 1.0,
        "lotSz_str": "1",
        "minSz": 1.0,
        "minSz_str": "1",
        "tickSz": 0.0001,
        "tickSz_str": "0.0001",
        "maxLever": 50.0,
    }


class TokenBucketRateLimiter:
    """Per-exchange lock-protected token bucket rate limiter to prevent IP/API bans."""

    def __init__(self, rate_per_sec: float = 15.0, capacity: float = 20.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = mono_ms()
        self._penalty_until_mono: float = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop_id: int = 0

    def penalize(self, cooldown_sec: float = 0.8):
        self._penalty_until_mono = max(self._penalty_until_mono, mono_ms() + max(0.1, float(cooldown_sec)) * 1000.0)
        self.tokens = min(self.tokens, 0.0)

    def _get_lock(self) -> asyncio.Lock:
        try:
            lid = id(asyncio.get_running_loop())
        except RuntimeError:
            lid = 0
        if self._lock is None or self._lock_loop_id != lid:
            self._lock = asyncio.Lock()
            self._lock_loop_id = lid
        return self._lock

    async def acquire(self, cost: float = 1.0) -> bool:
        wait_sec = 0.0
        async with self._get_lock():
            now = mono_ms()
            elapsed_sec = max(0.0, (now - self.last_refill) / 1000.0)
            self.tokens = min(self.capacity, self.tokens + elapsed_sec * self.rate)
            self.last_refill = now
            penalty_wait = max(0.0, (self._penalty_until_mono - now) / 1000.0)
            if self.tokens >= cost and penalty_wait <= 0.0:
                self.tokens -= cost
                return True
            token_wait = max(0.0, (cost - self.tokens) / self.rate)
            wait_sec = max(token_wait, penalty_wait)
            self.tokens = max(-self.capacity, self.tokens - cost)
        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec, 4.0))
        return True


@dataclass
class OrderResult:
    order_id: str
    status: str             # "FILLED", "PARTIAL", "MISSED", "REJECTED_POST_ONLY", "UNCERTAIN"
    filled_qty: float
    avg_price: float
    latency_ms: float
    is_maker: bool = False
    raw_msg: str = ""
    contracts: float = 0.0
    ct_val: float = 1.0
    cl_ord_id: str = ""
    fee_usdt: float = 0.0
    fee_ccy: str = ""
    exec_type: str = ""


class OkxPrivateWsClient:
    """
    Authenticated OKX V5 Private WebSocket monitor (`orders`, `orders-algo`, `positions` for SWAP).
    Delivers millisecond-latency Maker TP fills, Conditional SL triggers (`effective`/`order_failed`),
    and position updates directly to the owning tenant's engine, eliminating 4s REST polling blind spots.
    """

    def __init__(
        self,
        get_creds_fn: Callable[[], Optional[Tuple[str, str, str, bool]]],
        on_order_event: Callable[[dict], Awaitable[None]],
        on_algo_event: Callable[[dict], Awaitable[None]],
        on_position_event: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self._get_creds = get_creds_fn
        self._on_order_event = on_order_event
        self._on_algo_event = on_algo_event
        self._on_position_event = on_position_event
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self.connected: bool = False
        self.last_msg_mono: float = 0.0
        self.last_error: str = ""

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        self.connected = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self):
        backoff = 2.0
        while self._running:
            creds = self._get_creds()
            if not creds:
                self.connected = False
                await asyncio.sleep(3.0)
                continue
            api_key, api_secret, passphrase, is_demo = creds
            url = (
                "wss://wspap.okx.com/ws/v5/private?brokerId=9999"
                if is_demo
                else "wss://ws.okx.com/ws/v5/private"
            )
            proxy = get_system_proxy()
            conn_kwargs: Dict[str, Any] = {"open_timeout": 8.0, "ping_interval": None}
            if proxy:
                conn_kwargs["proxy"] = proxy
            try:
                async with websockets.connect(url, **conn_kwargs) as ws:
                    ts_sec = str(int(time.time()))
                    sign_raw = f"{ts_sec}GET/users/self/verify"
                    sign = base64.b64encode(
                        hmac.new(api_secret.encode("utf-8"), sign_raw.encode("utf-8"), hashlib.sha256).digest()
                    ).decode("utf-8")
                    login_msg = {
                        "op": "login",
                        "args": [
                            {
                                "apiKey": api_key,
                                "passphrase": passphrase,
                                "timestamp": ts_sec,
                                "sign": sign,
                            }
                        ],
                    }
                    await ws.send(orjson.dumps(login_msg).decode("utf-8"))
                    auth_resp: Dict[str, Any] = {}
                    for _ in range(4):
                        auth_raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
                        parsed = orjson.loads(auth_raw)
                        if isinstance(parsed, dict) and parsed.get("event") == "notice":
                            continue
                        auth_resp = parsed if isinstance(parsed, dict) else {}
                        break
                    if str(auth_resp.get("code", "")) != "0":
                        self.connected = False
                        self.last_error = f"OKX Private WS 登录失败 ({auth_resp.get('code')}): {auth_resp.get('msg', '')}"
                        await asyncio.sleep(min(30.0, backoff))
                        backoff = min(30.0, backoff * 1.5)
                        continue

                    sub_msg = {
                        "op": "subscribe",
                        "args": [
                            {"channel": "orders", "instType": "SWAP"},
                            {"channel": "orders-algo", "instType": "SWAP"},
                            {"channel": "positions", "instType": "SWAP"},
                        ],
                    }
                    await ws.send(orjson.dumps(sub_msg).decode("utf-8"))
                    self.connected = True
                    self.last_error = ""
                    self.last_msg_mono = mono_ms()
                    backoff = 2.0

                    while self._running:
                        # Reconnect cleanly if credentials rotated
                        cur_creds = self._get_creds()
                        if not cur_creds or cur_creds[0] != api_key or cur_creds[3] != is_demo:
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                        except asyncio.TimeoutError:
                            await ws.send("ping")
                            try:
                                pong_raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
                                self.last_msg_mono = mono_ms()
                                if pong_raw in ("pong", b"pong"):
                                    continue
                                raw = pong_raw
                            except asyncio.TimeoutError:
                                break
                        self.last_msg_mono = mono_ms()
                        if raw in ("pong", b"pong"):
                            continue
                        try:
                            msg = orjson.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(msg, dict) or not msg.get("data"):
                            continue
                        chan = str((msg.get("arg") or {}).get("channel") or "")
                        for row in msg.get("data", []):
                            if not isinstance(row, dict):
                                continue
                            try:
                                if chan == "orders":
                                    await self._on_order_event(row)
                                elif chan == "orders-algo":
                                    await self._on_algo_event(row)
                                elif chan == "positions" and self._on_position_event is not None:
                                    await self._on_position_event(row)
                            except Exception:
                                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self.last_error = f"OKX Private WS 重连中 ({type(e).__name__})"
                await asyncio.sleep(min(20.0, backoff))
                backoff = min(20.0, backoff * 1.5)
        self.connected = False


class ExecutionAdapter:
    """
    Unified Execution Engine supporting:
    - Real Exchange API verification (`verify_exchange_credentials`) for OKX V5 & Binance USDM.
    - SHADOW mode: High-fidelity simulated matching against Follower book with latency & adverse selection.
    - LIVE mode: Real signed OKX V5 REST order execution, protective TP/SL orders, and fill reconciliation.
    """

    def __init__(
        self,
        get_math_state: Callable[[str], Optional[SymbolMathState]],
        get_risk_cfg: Callable[[], GlobalRiskConfig],
    ):
        self._get_math_state = get_math_state
        self._get_risk_cfg = get_risk_cfg
        self.rate_limiter = TokenBucketRateLimiter(rate_per_sec=15.0, capacity=20.0)
        self._algo_limiter = TokenBucketRateLimiter(rate_per_sec=8.0, capacity=10.0)
        self._acct_limiter = TokenBucketRateLimiter(rate_per_sec=4.0, capacity=5.0)
        self.last_rtt_ms: float = 24.5
        self._last_rtt_mono: float = mono_ms()
        self._active_maker_tp_orders: Dict[str, dict] = {}
        self._active_cond_sl_orders: Dict[str, dict] = {}
        self._forced_miss_next: Dict[str, bool] = {}
        self._okx_demo_flag: Dict[str, bool] = {}
        self._okx_account_meta: Dict[str, dict] = {}
        self._meta_locks: Dict[str, asyncio.Lock] = {}
        self._usdt_avail_by_key: Dict[str, float] = {}
        self._leverage_configured: Dict[Tuple[str, str, int, str, str], float] = {}
        self._http_clients: Dict[Tuple[int, Optional[str]], httpx.AsyncClient] = {}
        self._retired_http_clients: List[Tuple[int, httpx.AsyncClient]] = []

    def _record_live_rtt(self, rtt_ms: float):
        if rtt_ms <= 0:
            return
        clamped = min(15000.0, max(1.0, float(rtt_ms)))
        if self.last_rtt_ms <= 0:
            self.last_rtt_ms = round(clamped, 1)
        else:
            self.last_rtt_ms = round(0.7 * self.last_rtt_ms + 0.3 * clamped, 1)
        self._last_rtt_mono = mono_ms()

    def effective_rtt_ms(self) -> float:
        """Returns smoothed RTT with gentle idle time-decay toward 25ms so latency circuit breaker never deadlocks."""
        elapsed_sec = max(0.0, (mono_ms() - self._last_rtt_mono) / 1000.0)
        if elapsed_sec <= 5.0 or self.last_rtt_ms <= 25.0:
            return self.last_rtt_ms
        decay = math.exp(-(elapsed_sec - 5.0) / 15.0)
        decayed = 25.0 + (self.last_rtt_ms - 25.0) * decay
        return round(decayed, 1)

    def get_usdt_avail(self, api_key: str) -> float:
        return float(self._usdt_avail_by_key.get(api_key) or 0.0)

    def invalidate_okx_meta(self, api_key: str):
        cached = self._okx_account_meta.get(api_key)
        if cached:
            cached["_fetched_mono"] = 0.0

    def _get_http_client(self, proxy: Optional[str], timeout: float = 4.0) -> httpx.AsyncClient:
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        key = (loop_id, proxy)
        for old_k in [k for k in list(self._http_clients.keys()) if k[0] != loop_id]:
            self._http_clients.pop(old_k, None)
        self._retired_http_clients = [(lid, c) for lid, c in self._retired_http_clients if lid == loop_id and not c.is_closed]
        client = self._http_clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                proxy=proxy,
                timeout=httpx.Timeout(max(6.0, float(timeout)), connect=4.0),
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20, keepalive_expiry=45.0),
            )
            self._http_clients[key] = client
        return client

    @asynccontextmanager
    async def _borrow_http_client(self, proxy: Optional[str], timeout: float = 4.0):
        client = self._get_http_client(proxy, timeout=timeout)
        try:
            yield client
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.CloseError):
            # Only rotate pool on actual socket/protocol failures (never on a single slow ReadTimeout!)
            try:
                loop_id = id(asyncio.get_running_loop())
            except RuntimeError:
                loop_id = 0
            key = (loop_id, proxy)
            if self._http_clients.get(key) is client:
                stale = self._http_clients.pop(key, None)
                if stale is not None and not stale.is_closed:
                    self._retired_http_clients.append((loop_id, stale))
                    if len(self._retired_http_clients) > 8:
                        evicted = self._retired_http_clients[:-8]
                        self._retired_http_clients = self._retired_http_clients[-8:]
                        for _, ev_c in evicted:
                            if not ev_c.is_closed:
                                try:
                                    asyncio.create_task(ev_c.aclose())
                                except Exception:
                                    pass
            raise

    async def aclose(self):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        for k in list(self._http_clients.keys()):
            client = self._http_clients.pop(k, None)
            if client is not None and k[0] == loop_id and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
        for lid, client in list(self._retired_http_clients):
            if lid == loop_id and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
        self._retired_http_clients.clear()

    def force_next_miss(self, symbol: str):
        self._forced_miss_next[symbol] = True

    @staticmethod
    def _build_okx_headers(
        api_key: str,
        api_secret: str,
        passphrase: str,
        method: str,
        request_path: str,
        body_str: str = "",
        simulated_trading: bool = False,
    ) -> Dict[str, str]:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        prehash = f"{ts}{method.upper()}{request_path}{body_str}"
        mac = hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        sign = base64.b64encode(mac).decode("utf-8")
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }
        if simulated_trading:
            headers["x-simulated-trading"] = "1"
        return headers

    async def verify_exchange_credentials(
        self,
        ex_row: Dict[str, Any],
        auto_upgrade_okx_mode: bool = False,
        allow_cached_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Verifies exchange API credentials against the real exchange API
        (OKX V5, Binance USDM, Bybit V5, Bitget V2, Gate V4).
        When `auto_upgrade_okx_mode=True` and OKX is in Simple mode (`acctLv='1'`) with trade permission,
        automatically calls OKX `POST /api/v5/account/set-account-level` (`acctLv='2'`).
        """
        ex_id = ex_row.get("exchange_id", "")
        api_key = (ex_row.get("api_key") or "").strip()
        api_secret = (ex_row.get("_decrypted_secret") or "").strip()
        passphrase = (ex_row.get("_decrypted_passphrase") or "").strip()
        needs_pass = ex_id in ("okx", "bitget")

        if not api_key and not api_secret and not passphrase:
            return {
                "ok": False,
                "auth_status": "UNCONFIGURED",
                "auth_message": "未配置 API 密钥",
                "account_equity_usdt": 0.0,
            }

        missing = []
        if not api_key:
            missing.append("API Key")
        if not api_secret:
            missing.append("API Secret")
        if needs_pass and not passphrase:
            missing.append("API Passphrase")

        if missing:
            return {
                "ok": False,
                "auth_status": "CONFIG_INCOMPLETE",
                "auth_message": f"配置不完整：缺少 {', '.join(missing)}，请填写完整后再保存",
                "account_equity_usdt": 0.0,
            }

        proxy = get_system_proxy()

        # 1. Real OKX V5 Verification (+ /api/v5/account/config check)
        if ex_id == "okx":
            req_path = "/api/v5/account/balance"
            url = f"https://www.okx.com{req_path}"
            cfg_path = "/api/v5/account/config"
            cfg_url = f"https://www.okx.com{cfg_path}"
            last_exc = None
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(proxy=proxy, timeout=8.0) as client:
                        t0 = mono_ms()
                        headers = self._build_okx_headers(api_key, api_secret, passphrase, "GET", req_path, "", False)
                        resp = await client.get(url, headers=headers)
                        rtt = mono_ms() - t0
                        self._record_live_rtt(rtt)
                        body = resp.json()
                        code = str(body.get("code", ""))

                        is_demo = False
                        if code == "50101":
                            headers_demo = self._build_okx_headers(
                                api_key, api_secret, passphrase, "GET", req_path, "", True
                            )
                            resp_demo = await client.get(url, headers=headers_demo)
                            body_demo = resp_demo.json()
                            if str(body_demo.get("code", "")) == "0":
                                body = body_demo
                                code = "0"
                                is_demo = True

                        if code == "0":
                            self._okx_demo_flag[api_key] = is_demo
                            data_list = body.get("data", [])
                            total_eq = 0.0
                            usdt_avail = 0.0
                            if data_list:
                                raw_eq = data_list[0].get("totalEq")
                                if raw_eq:
                                    total_eq = float(raw_eq)
                                for det in data_list[0].get("details", []):
                                    if det.get("ccy") == "USDT":
                                        usdt_avail = float(det.get("availEq") or det.get("cashBal") or 0.0)
                                        if total_eq <= 0:
                                            total_eq = usdt_avail
                            eq_val = round(total_eq if total_eq > 0 else usdt_avail, 2)
                            self._usdt_avail_by_key[api_key] = round(usdt_avail, 2)

                            acct_lv = "2"
                            pos_mode = "net_mode"
                            perm = "read_only,trade"
                            cfg_loaded = False
                            try:
                                cfg_headers = self._build_okx_headers(
                                    api_key, api_secret, passphrase, "GET", cfg_path, "", is_demo
                                )
                                cfg_resp = await client.get(cfg_url, headers=cfg_headers)
                                cfg_json = cfg_resp.json()
                                if str(cfg_json.get("code", "")) == "0" and cfg_json.get("data"):
                                    c_row = cfg_json["data"][0]
                                    acct_lv = str(c_row.get("acctLv") or acct_lv)
                                    pos_mode = str(c_row.get("posMode") or pos_mode)
                                    perm = str(c_row.get("perm") or perm)
                                    cfg_loaded = True
                            except Exception:
                                pass

                            if cfg_loaded:
                                self._okx_account_meta[api_key] = {
                                    "is_demo": is_demo,
                                    "acctLv": acct_lv,
                                    "posMode": pos_mode,
                                    "perm": perm,
                                    "usdt_avail_usdt": round(usdt_avail, 2),
                                    "_fetched_mono": mono_ms(),
                                    "_verified": True,
                                }
                            elif not allow_cached_fallback:
                                return {
                                    "ok": False,
                                    "auth_status": "ERROR",
                                    "auth_message": f"无法读取 OKX 账户配置(/account/config)，为防止持仓模式或权限误判已拦截（当前权益: {eq_val:.2f} USDT）",
                                    "account_equity_usdt": eq_val,
                                    "usdt_avail_usdt": round(usdt_avail, 2),
                                }

                            perms = [p.strip() for p in perm.split(",") if p.strip()]
                            if "trade" not in perms:
                                return {
                                    "ok": False,
                                    "auth_status": "READ_ONLY_KEY",
                                    "auth_message": (
                                        f"OKX API Key 仅含只读权限({perm})，未开通「交易」权限，无法实盘下单"
                                        f"（当前权益: {eq_val:.2f} USDT）"
                                    ),
                                    "account_equity_usdt": eq_val,
                                    "acct_lv": acct_lv,
                                    "pos_mode": pos_mode,
                                    "perm": perm,
                                    "is_demo": is_demo,
                                }

                            if acct_lv == "1" and auto_upgrade_okx_mode:
                                try:
                                    sw_path = "/api/v5/account/set-account-level"
                                    sw_body = orjson.dumps({"acctLv": "2"}).decode("utf-8")
                                    sw_headers = self._build_okx_headers(
                                        api_key, api_secret, passphrase, "POST", sw_path, sw_body, is_demo
                                    )
                                    sw_resp = await client.post(f"https://www.okx.com{sw_path}", content=sw_body, headers=sw_headers)
                                    sw_json = sw_resp.json()
                                    if str(sw_json.get("code", "")) == "0":
                                        acct_lv = "2"
                                        self._okx_account_meta[api_key]["acctLv"] = "2"
                                except Exception:
                                    pass

                            if acct_lv == "1":
                                return {
                                    "ok": False,
                                    "auth_status": "ACCOUNT_MODE_UNSUPPORTED",
                                    "auth_message": (
                                        f"OKX 账户当前为「简单交易模式(acctLv=1)」，OKX 官方禁止在此模式下单永续合约(51010)。"
                                        f"请点击「一键切换单币种保证金模式」或在 OKX 交易设置 → 账户模式切换为「单币种保证金模式」"
                                        f"（当前权益: {eq_val:.2f} USDT）"
                                    ),
                                    "account_equity_usdt": eq_val,
                                    "acct_lv": acct_lv,
                                    "pos_mode": pos_mode,
                                    "perm": perm,
                                    "is_demo": is_demo,
                                }

                            env_tag = "欧易模拟盘API" if is_demo else "欧易实盘账户"
                            mode_label = "双向持仓" if pos_mode == "long_short_mode" else "单向买卖持仓"
                            return {
                                "ok": True,
                                "auth_status": "VERIFIED",
                                "auth_message": f"已配置且验证通过 ({env_tag} · {mode_label} · 权益: {eq_val:.2f} USDT)",
                                "account_equity_usdt": eq_val,
                                "usdt_avail_usdt": round(usdt_avail, 2),
                                "acct_lv": acct_lv,
                                "pos_mode": pos_mode,
                                "perm": perm,
                                "is_demo": is_demo,
                            }
                        else:
                            raw_msg = body.get("msg", "认证失败")
                            cn_reason = OKX_ERROR_MAP.get(code, raw_msg)
                            return {
                                "ok": False,
                                "auth_status": "ERROR",
                                "auth_message": f"OKX 验证失败 ({code}): {cn_reason}",
                                "account_equity_usdt": 0.0,
                            }
                except Exception as e:
                    last_exc = e
                    await asyncio.sleep(0.3)

            if allow_cached_fallback and ex_row.get("auth_status") == "VERIFIED":
                prev_eq = float(ex_row.get("account_equity_usdt") or 0.0)
                return {
                    "ok": True,
                    "auth_status": "VERIFIED",
                    "auth_message": ex_row.get("auth_message") or f"已配置且验证通过 (欧易实盘账户 · 权益: {prev_eq:.2f} USDT)",
                    "account_equity_usdt": prev_eq,
                }
            return {
                "ok": False,
                "auth_status": "ERROR",
                "auth_message": f"连接 OKX 验证接口失败 ({type(last_exc).__name__})，请检查本地网络或代理",
                "account_equity_usdt": 0.0,
            }

        # 2. Real Binance USDM Verification (+ dualSidePosition check)
        if ex_id == "binanceusdm":
            try:
                ts_ms = int(time.time() * 1000)
                query_str = f"timestamp={ts_ms}&recvWindow=5000"
                sig = hmac.new(api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
                url = f"https://fapi.binance.com/fapi/v2/balance?{query_str}&signature={sig}"
                async with httpx.AsyncClient(proxy=proxy, timeout=6.0) as client:
                    resp = await client.get(url, headers={"X-MBX-APIKEY": api_key})
                    body = resp.json()
                    if resp.status_code == 200 and isinstance(body, list):
                        usdt_bal = 0.0
                        for asset in body:
                            if asset.get("asset") == "USDT":
                                usdt_bal = float(asset.get("balance") or 0.0)
                        pos_mode_label = "单向持仓"
                        try:
                            q2 = f"timestamp={int(time.time()*1000)}&recvWindow=5000"
                            s2 = hmac.new(api_secret.encode("utf-8"), q2.encode("utf-8"), hashlib.sha256).hexdigest()
                            r2 = await client.get(
                                f"https://fapi.binance.com/fapi/v1/positionSide/dual?{q2}&signature={s2}",
                                headers={"X-MBX-APIKEY": api_key},
                            )
                            if r2.status_code == 200 and isinstance(r2.json(), dict):
                                pos_mode_label = "双向持仓" if r2.json().get("dualSidePosition") else "单向持仓"
                        except Exception:
                            pass
                        return {
                            "ok": True,
                            "auth_status": "VERIFIED",
                            "auth_message": f"已配置且验证通过 (币安实盘合约 · {pos_mode_label} · 余额: {usdt_bal:.2f} USDT)",
                            "account_equity_usdt": round(usdt_bal, 2),
                        }
                    else:
                        err_code = body.get("code", resp.status_code) if isinstance(body, dict) else resp.status_code
                        err_msg = body.get("msg", "密钥无效") if isinstance(body, dict) else "密钥校验未通过"
                        return {
                            "ok": False,
                            "auth_status": "ERROR",
                            "auth_message": f"币安 API 验证失败 ({err_code}): {err_msg}",
                            "account_equity_usdt": 0.0,
                        }
            except Exception as e:
                return {
                    "ok": False,
                    "auth_status": "ERROR",
                    "auth_message": f"连接币安验证接口失败 ({type(e).__name__})",
                    "account_equity_usdt": 0.0,
                }

        # 3. Real Bybit V5 Verification (/v5/account/wallet-balance)
        if ex_id == "bybit":
            try:
                async with httpx.AsyncClient(proxy=proxy, timeout=6.0) as client:
                    ts = str(int(time.time() * 1000))
                    recv_w = "5000"
                    q = "accountType=UNIFIED"
                    pre = f"{ts}{api_key}{recv_w}{q}"
                    sign = hmac.new(api_secret.encode("utf-8"), pre.encode("utf-8"), hashlib.sha256).hexdigest()
                    hdrs = {
                        "X-BAPI-API-KEY": api_key,
                        "X-BAPI-TIMESTAMP": ts,
                        "X-BAPI-RECV-WINDOW": recv_w,
                        "X-BAPI-SIGN": sign,
                    }
                    r = await client.get(f"https://api.bybit.com/v5/account/wallet-balance?{q}", headers=hdrs)
                    b = r.json()
                    if b.get("retCode") == 0:
                        eq = 0.0
                        lst = b.get("result", {}).get("list", [])
                        if lst:
                            eq = float(lst[0].get("totalEquity") or 0.0)
                        return {
                            "ok": True,
                            "auth_status": "VERIFIED",
                            "auth_message": f"已配置且验证通过 (Bybit V5 统一账户 · 权益: {eq:.2f} USDT)",
                            "account_equity_usdt": round(eq, 2),
                        }
                    return {
                        "ok": False,
                        "auth_status": "ERROR",
                        "auth_message": f"Bybit API 验证失败 ({b.get('retCode')}): {b.get('retMsg', '认证未通过')}",
                        "account_equity_usdt": 0.0,
                    }
            except Exception as e:
                return {
                    "ok": False,
                    "auth_status": "ERROR",
                    "auth_message": f"连接 Bybit 验证接口失败 ({type(e).__name__})",
                    "account_equity_usdt": 0.0,
                }

        # 4. Real Bitget V2 Futures Verification (/api/v2/mix/account/accounts)
        if ex_id == "bitget":
            try:
                req_path = "/api/v2/mix/account/accounts?productType=USDT-FUTURES"
                ts = str(int(time.time() * 1000))
                pre = f"{ts}GET{req_path}"
                sign = base64.b64encode(hmac.new(api_secret.encode("utf-8"), pre.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
                hdrs = {
                    "ACCESS-KEY": api_key,
                    "ACCESS-SIGN": sign,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": passphrase,
                    "Content-Type": "application/json",
                }
                async with httpx.AsyncClient(proxy=proxy, timeout=6.0) as client:
                    r = await client.get(f"https://api.bitget.com{req_path}", headers=hdrs)
                    b = r.json()
                    if str(b.get("code", "")) == "00000":
                        usdt_eq = 0.0
                        for row in b.get("data", []):
                            if row.get("marginCoin") == "USDT":
                                usdt_eq = float(row.get("usdtEquity") or row.get("available") or 0.0)
                        return {
                            "ok": True,
                            "auth_status": "VERIFIED",
                            "auth_message": f"已配置且验证通过 (Bitget V2 合约 · 权益: {usdt_eq:.2f} USDT)",
                            "account_equity_usdt": round(usdt_eq, 2),
                        }
                    return {
                        "ok": False,
                        "auth_status": "ERROR",
                        "auth_message": f"Bitget API 验证失败 ({b.get('code')}): {b.get('msg', '认证未通过')}",
                        "account_equity_usdt": 0.0,
                    }
            except Exception as e:
                return {
                    "ok": False,
                    "auth_status": "ERROR",
                    "auth_message": f"连接 Bitget 验证接口失败 ({type(e).__name__})",
                    "account_equity_usdt": 0.0,
                }

        # 5. Real Gate.io V4 Futures Verification (/api/v4/futures/usdt/accounts)
        if ex_id == "gate":
            try:
                ts = str(int(time.time()))
                req_path = "/api/v4/futures/usdt/accounts"
                empty_sha512 = hashlib.sha512(b"").hexdigest()
                pre = f"GET\n{req_path}\n\n{empty_sha512}\n{ts}"
                sign = hmac.new(api_secret.encode("utf-8"), pre.encode("utf-8"), hashlib.sha512).hexdigest()
                hdrs = {"KEY": api_key, "Timestamp": ts, "SIGN": sign, "Accept": "application/json"}
                async with httpx.AsyncClient(proxy=proxy, timeout=6.0) as client:
                    r = await client.get(f"https://api.gateio.ws{req_path}", headers=hdrs)
                    b = r.json()
                    if r.status_code == 200 and isinstance(b, dict) and "total" in b:
                        eq = float(b.get("total") or 0.0)
                        return {
                            "ok": True,
                            "auth_status": "VERIFIED",
                            "auth_message": f"已配置且验证通过 (Gate V4 永续 · 权益: {eq:.2f} USDT)",
                            "account_equity_usdt": round(eq, 2),
                        }
                    err_label = b.get("label", r.status_code) if isinstance(b, dict) else r.status_code
                    err_msg = b.get("message", "认证未通过") if isinstance(b, dict) else "认证未通过"
                    return {
                        "ok": False,
                        "auth_status": "ERROR",
                        "auth_message": f"Gate API 验证失败 ({err_label}): {err_msg}",
                        "account_equity_usdt": 0.0,
                    }
            except Exception as e:
                return {
                    "ok": False,
                    "auth_status": "ERROR",
                    "auth_message": f"连接 Gate 验证接口失败 ({type(e).__name__})",
                    "account_equity_usdt": 0.0,
                }

        # 6. Fallback format validation for other standby connectors
        if len(api_key) < 8 or len(api_secret) < 8:
            return {
                "ok": False,
                "auth_status": "ERROR",
                "auth_message": "API Key 或 Secret 长度无效，请检查密钥格式",
                "account_equity_usdt": 0.0,
            }
        return {
            "ok": False,
            "auth_status": "ERROR",
            "auth_message": f"{ex_id} 当前仅作为备选行情源展示，实盘下单主通道请使用 OKX Swap",
            "account_equity_usdt": 0.0,
        }

    async def switch_okx_account_level(self, ex_row: Dict[str, Any], target_level: str = "2") -> Dict[str, Any]:
        """
        Calls OKX V5 `POST /api/v5/account/set-account-level` (`acctLv`: '2' Single-currency or '3' Multi-currency)
        per OKX official docs to resolve 51010 Simple Mode restrictions programmatically.
        """
        creds = self._extract_okx_creds(ex_row)
        if not creds:
            return {"ok": False, "message": "未配置完整的 OKX API Key / Secret / Passphrase"}
        if str(target_level) not in ("2", "3"):
            return {"ok": False, "message": "仅支持切换至单币种保证金模式(2)或跨币种保证金模式(3)"}

        proxy = get_system_proxy()
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=8.0) as client:
                meta = await self.ensure_okx_account_meta(client, creds)
                res = await self._okx_request(
                    client,
                    creds,
                    "POST",
                    "/api/v5/account/set-account-level",
                    {"acctLv": str(target_level)},
                )
                code = str(res.get("code", ""))
                if code == "0":
                    self._okx_account_meta.pop(creds[0], None)
                    ver = await self.verify_exchange_credentials(ex_row, auto_upgrade_okx_mode=False)
                    mode_cn = "单币种保证金模式" if str(target_level) == "2" else "跨币种保证金模式"
                    return {
                        "ok": True,
                        "acct_lv": str(target_level),
                        "verify_result": ver,
                        "message": f"OKX 账户模式已通过官方 API 成功切换为「{mode_cn} (acctLv={target_level})」！",
                    }
                raw_msg = str(res.get("msg") or "切换失败")
                cn_err = OKX_ERROR_MAP.get(code, raw_msg)
                return {
                    "ok": False,
                    "code": code,
                    "acct_lv": meta.get("acctLv", "1"),
                    "message": f"OKX 切换账户模式被拒 ({code}): {cn_err}",
                }
        except Exception as e:
            return {"ok": False, "message": f"连接 OKX 账户模式接口失败 ({type(e).__name__})"}

    def sample_network_latency_ms(self) -> float:
        risk = self._get_risk_cfg()
        base = max(5.0, risk.shadow_base_latency_ms)
        jitter = max(1.0, risk.shadow_latency_jitter_ms)
        sigma = 0.32
        sample = base + (random.lognormvariate(0.0, sigma) - 1.0) * jitter
        rtt = max(6.0, min(380.0, sample))
        self.last_rtt_ms = round(0.8 * self.last_rtt_ms + 0.2 * rtt, 2)
        return rtt

    async def place_entry_ioc(
        self,
        cfg: SymbolStrategy,
        side: str,
        qty: float,
        reservation_price: float,
        trigger_price: float,
        exchange_cfg: dict,
    ) -> OrderResult:
        await self.rate_limiter.acquire(1.0)
        t0 = mono_ms()
        risk = self._get_risk_cfg()
        order_id = f"ioc-{uuid.uuid4().hex[:10]}"

        if risk.trading_mode == "SHADOW":
            rtt_ms = self.sample_network_latency_ms()
            one_way_ms = rtt_ms * 0.55
            await asyncio.sleep(one_way_ms / 1000.0)

            if self._forced_miss_next.pop(cfg.symbol, False):
                return OrderResult(
                    order_id=order_id,
                    status="MISSED",
                    filled_qty=0.0,
                    avg_price=0.0,
                    latency_ms=mono_ms() - t0,
                    raw_msg="Market Maker withdrew quote within latency window (IOC canceled)",
                )

            m_state = self._get_math_state(cfg.symbol)
            f_book = m_state.latest_follower if m_state else None
            if not f_book:
                return OrderResult(order_id, "MISSED", 0.0, 0.0, mono_ms() - t0, False, "No follower book")

            if random.random() < risk.shadow_adverse_selection_prob:
                return OrderResult(
                    order_id=order_id,
                    status="MISSED",
                    filled_qty=0.0,
                    avg_price=0.0,
                    latency_ms=mono_ms() - t0,
                    raw_msg="MM quote pulled before IOC arrival (0 cost cancel)",
                )

            if side == "LONG":
                curr_ask = f_book.ask
                if curr_ask > reservation_price:
                    return OrderResult(
                        order_id=order_id,
                        status="MISSED",
                        filled_qty=0.0,
                        avg_price=0.0,
                        latency_ms=mono_ms() - t0,
                        raw_msg=f"Ask {curr_ask} moved above reservation price {reservation_price}",
                    )
                slip_factor = random.uniform(0.0, cfg.slippage_pct * 0.45)
                fill_price = min(reservation_price, curr_ask * (1.0 + slip_factor))
                avail_qty = max(qty, f_book.ask_qty)
                fill_qty = min(qty, avail_qty)
            else:
                curr_bid = f_book.bid
                if curr_bid < reservation_price:
                    return OrderResult(
                        order_id=order_id,
                        status="MISSED",
                        filled_qty=0.0,
                        avg_price=0.0,
                        latency_ms=mono_ms() - t0,
                        raw_msg=f"Bid {curr_bid} moved below reservation price {reservation_price}",
                    )
                slip_factor = random.uniform(0.0, cfg.slippage_pct * 0.45)
                fill_price = max(reservation_price, curr_bid * (1.0 - slip_factor))
                avail_qty = max(qty, f_book.bid_qty)
                fill_qty = min(qty, avail_qty)

            await asyncio.sleep((rtt_ms * 0.45) / 1000.0)
            total_lat = mono_ms() - t0
            return OrderResult(
                order_id=order_id,
                status="FILLED" if fill_qty >= qty * 0.99 else "PARTIAL",
                filled_qty=round(fill_qty, cfg.qty_precision) if cfg.qty_precision > 0 else float(int(fill_qty)),
                avg_price=round(fill_price, cfg.price_precision),
                latency_ms=round(total_lat, 2),
                is_maker=False,
                raw_msg="Shadow IOC filled within reservation price",
            )

        # LIVE mode execution with mandatory reconciliation fallback
        return await self._live_place_ioc_with_reconcile(
            cfg, side, qty, reservation_price, exchange_cfg, order_id, t0
        )

    @staticmethod
    def _extract_okx_creds(exchange_cfg: Optional[dict]) -> Optional[Tuple[str, str, str]]:
        if not exchange_cfg or exchange_cfg.get("exchange_id", "okx") != "okx":
            return None
        api_key = (exchange_cfg.get("api_key") or "").strip()
        api_secret = (exchange_cfg.get("_decrypted_secret") or "").strip()
        passphrase = (exchange_cfg.get("_decrypted_passphrase") or "").strip()
        if not api_key or not api_secret or not passphrase:
            return None
        return api_key, api_secret, passphrase

    def _get_okx_meta(self, api_key: str) -> dict:
        meta = self._okx_account_meta.get(api_key)
        if meta:
            return meta
        return {
            "is_demo": self._okx_demo_flag.get(api_key, False),
            "acctLv": "2",
            "posMode": "net_mode",
            "perm": "read_only,trade",
            "_verified": False,
        }

    def _get_meta_lock(self, api_key: str) -> asyncio.Lock:
        lock = self._meta_locks.get(api_key)
        if lock is None:
            lock = asyncio.Lock()
            self._meta_locks[api_key] = lock
        return lock

    @staticmethod
    def _okx_close_mode_fields(side: str, pos_mode: str) -> dict:
        if pos_mode == "long_short_mode":
            return {"posSide": "long" if side == "LONG" else "short"}
        return {"reduceOnly": True}

    @staticmethod
    def _okx_entry_mode_fields(side: str, pos_mode: str) -> dict:
        if pos_mode == "long_short_mode":
            return {"posSide": "long" if side == "LONG" else "short"}
        return {}

    @staticmethod
    def _parse_okx_fee_usdt(row: dict) -> Tuple[float, str]:
        try:
            fee_val = float(row.get("fee") or row.get("fillFee") or 0.0)
            rebate_val = float(row.get("rebate") or 0.0)
            fee_ccy = str(row.get("feeCcy") or "")
            # OKX reports fees as negative numbers (e.g. -0.0135) and rebates as positive
            net_cost = max(0.0, -fee_val - max(0.0, rebate_val))
            return round(net_cost, 6), fee_ccy
        except (TypeError, ValueError):
            return 0.0, ""

    async def _okx_request(
        self,
        client: httpx.AsyncClient,
        creds: Tuple[str, str, str],
        method: str,
        path: str,
        payload: Optional[Any] = None,
        timeout: Optional[float] = None,
        simulated_override: Optional[bool] = None,
    ) -> dict:
        # Per-endpoint-group rate limiting inside _okx_request prevents 50011/429 bursts
        if "algo" in path:
            await self._algo_limiter.acquire(1.0)
        elif "/account/" in path:
            await self._acct_limiter.acquire(1.0)
        else:
            await self.rate_limiter.acquire(1.0)

        api_key, api_secret, passphrase = creds
        meta = self._get_okx_meta(api_key)
        is_demo = bool(meta.get("is_demo", False)) if simulated_override is None else bool(simulated_override)
        body_str = orjson.dumps(payload).decode("utf-8") if payload is not None else ""
        headers = self._build_okx_headers(api_key, api_secret, passphrase, method, path, body_str, is_demo)
        url = f"https://www.okx.com{path}"
        req_kwargs: Dict[str, Any] = {"headers": headers}
        if timeout is not None:
            req_kwargs["timeout"] = timeout
        t0 = mono_ms()
        if method.upper() == "GET":
            resp = await client.get(url, **req_kwargs)
        else:
            resp = await client.post(url, content=body_str, **req_kwargs)
        self._record_live_rtt(mono_ms() - t0)
        status_code = getattr(resp, "status_code", 200)
        if status_code == 429:
            self.rate_limiter.penalize(0.8)
            self._algo_limiter.penalize(1.0)
            self._acct_limiter.penalize(1.0)
            return {"code": "50011", "_http_status": 429, "msg": "OKX HTTP 429 Too Many Requests", "data": []}
        try:
            body = resp.json()
            if isinstance(body, dict):
                if str(body.get("code", "")) == "50011":
                    self.rate_limiter.penalize(0.8)
                    self._algo_limiter.penalize(1.0)
                if str(body.get("code", "")) == "51000" or (
                    body.get("data")
                    and isinstance(body["data"], list)
                    and isinstance(body["data"][0], dict)
                    and str(body["data"][0].get("sCode", "")) == "51000"
                ):
                    self.invalidate_okx_meta(api_key)
                return body
            return {"code": str(status_code), "_http_status": status_code, "msg": "OKX 非法响应体", "data": []}
        except Exception:
            return {
                "code": str(status_code),
                "_http_status": status_code,
                "msg": f"OKX 非 JSON 响应 (HTTP {status_code})",
                "data": [],
            }

    async def ensure_okx_account_meta(
        self, client: httpx.AsyncClient, creds: Tuple[str, str, str], force_refresh: bool = False
    ) -> dict:
        api_key = creds[0]
        cached = self._okx_account_meta.get(api_key)
        now_m = mono_ms()
        if (
            not force_refresh
            and cached
            and cached.get("_verified", True)
            and (now_m - float(cached.get("_fetched_mono") or 0.0) < 300_000.0)
        ):
            return cached

        async with self._get_meta_lock(api_key):
            cached = self._okx_account_meta.get(api_key)
            now_m = mono_ms()
            if (
                not force_refresh
                and cached
                and cached.get("_verified", True)
                and (now_m - float(cached.get("_fetched_mono") or 0.0) < 300_000.0)
            ):
                return cached
            is_demo = bool((cached or {}).get("is_demo", self._okx_demo_flag.get(api_key, False)))
            meta = (
                dict(cached)
                if cached
                else {
                    "is_demo": is_demo,
                    "acctLv": "2",
                    "posMode": "net_mode",
                    "perm": "read_only,trade",
                    "_verified": False,
                }
            )
            try:
                self._okx_account_meta.setdefault(api_key, dict(meta))["is_demo"] = is_demo
                res = await self._okx_request(client, creds, "GET", "/api/v5/account/config")
                if str(res.get("code", "")) == "50101":
                    flipped_demo = not is_demo
                    self._okx_demo_flag[api_key] = flipped_demo
                    meta["is_demo"] = flipped_demo
                    self._okx_account_meta.setdefault(api_key, dict(meta))["is_demo"] = flipped_demo
                    res = await self._okx_request(client, creds, "GET", "/api/v5/account/config")
                if str(res.get("code", "")) == "0" and res.get("data"):
                    row = res["data"][0]
                    meta["acctLv"] = str(row.get("acctLv") or "2")
                    meta["posMode"] = str(row.get("posMode") or "net_mode")
                    meta["perm"] = str(row.get("perm") or "read_only,trade")
                    meta["_fetched_mono"] = mono_ms()
                    meta["_verified"] = True
                    self._okx_account_meta[api_key] = meta
            except Exception:
                pass
            return meta

    async def ensure_leverage(
        self,
        client: httpx.AsyncClient,
        creds: Tuple[str, str, str],
        inst_id: str,
        leverage: float,
        pos_mode: str,
        mgn_mode: str = "cross",
        side: str = "",
    ) -> Tuple[bool, str]:
        sym = okx_inst_id_to_unified(inst_id)
        spec = resolve_okx_spec(sym)
        max_lev = max(1, int(float(spec.get("maxLever") or 50.0)))
        lever_int = max(1, min(max_lev, int(round(leverage))))
        eff_mgn = "isolated" if str(mgn_mode or "cross").lower() == "isolated" else "cross"
        ps_key = ("long" if side == "LONG" else "short") if (eff_mgn == "isolated" and pos_mode == "long_short_mode" and side) else ""
        cache_key = (creds[0], inst_id, lever_int, pos_mode, f"{eff_mgn}:{ps_key}")
        now_m = mono_ms()
        last_ok = float(self._leverage_configured.get(cache_key) or 0.0)
        if last_ok > 0.0 and (now_m - last_ok < 600_000.0):
            return True, ""
        try:
            payload: Dict[str, Any] = {"instId": inst_id, "lever": str(lever_int), "mgnMode": eff_mgn}
            # OKX V5: `posSide` is REQUIRED for isolated + long_short_mode, and FORBIDDEN for cross (51000)
            if ps_key:
                payload["posSide"] = ps_key
            res = await self._okx_request(client, creds, "POST", "/api/v5/account/set-leverage", payload)
            code = str(res.get("code", ""))
            if code == "0":
                self._leverage_configured[cache_key] = now_m
                return True, ""
            return False, f"OKX 设置杠杆失败 ({code}): {OKX_ERROR_MAP.get(code, res.get('msg', ''))}"
        except Exception as e:
            return False, f"OKX 设置杠杆网络异常 ({type(e).__name__})"

    @staticmethod
    def _calc_okx_entry_sz(
        symbol: str,
        price: float,
        order_usdt: float,
        equity_usdt: float,
        leverage: float,
    ) -> Tuple[str, float, float, float, str]:
        """Returns (sz_str, contracts, base_qty, ct_val, err_msg)."""
        spec = resolve_okx_spec(symbol)
        if spec.get("state") == "unknown":
            return "", 0.0, 0.0, 1.0, f"合约 {symbol} 规范未同步 (ctVal 未知)，已拒绝盲目实盘下单"
        ct_val = float(spec["ctVal"])
        lot_sz_str = str(spec["lotSz_str"])
        min_sz = float(spec["minSz"])
        max_lmt_sz = float(spec.get("maxLmtSz") or 0.0)
        max_lever = max(1.0, float(spec.get("maxLever") or 50.0))
        eff_leverage = max(1.0, min(float(leverage), max_lever))

        if price <= 0 or ct_val <= 0:
            return "", 0.0, 0.0, ct_val, "价格或合约面值无效"

        target_notional = float(order_usdt)
        max_safe_notional = target_notional
        if equity_usdt > 0:
            max_safe_notional = max(0.0, equity_usdt * eff_leverage * 0.85)
            target_notional = min(target_notional, max_safe_notional)

        min_notional = min_sz * ct_val * price
        if equity_usdt > 0 and max_safe_notional < min_notional * 0.98:
            return (
                "",
                0.0,
                0.0,
                ct_val,
                f"账户可用保证金({equity_usdt:.2f}U)×杠杆({eff_leverage:.0f}x)={max_safe_notional:.2f}U，低于最小名义下单额 {min_notional:.2f}U",
            )

        raw_contracts = target_notional / max(1e-12, price * ct_val)
        if raw_contracts < min_sz:
            # Allow snapping up to minSz only if it doesn't blow past configured order_usdt by >35% or safe margin
            if min_notional <= order_usdt * 1.35 and (equity_usdt <= 0 or min_notional <= max_safe_notional):
                raw_contracts = min_sz
            else:
                return (
                    "",
                    0.0,
                    0.0,
                    ct_val,
                    f"单笔金额 {order_usdt:.2f}U 小于 OKX 最小开仓额 {min_notional:.2f}U (minSz={min_sz}, ctVal={ct_val})",
                )

        if max_lmt_sz > 0 and raw_contracts > max_lmt_sz:
            raw_contracts = max_lmt_sz

        sz_str, contracts = quantize_decimal_step(raw_contracts, lot_sz_str, rounding="down")
        if contracts < min_sz:
            sz_str, contracts = quantize_decimal_step(min_sz, lot_sz_str, rounding="up")
            snapped_notional = contracts * ct_val * price
            if snapped_notional > order_usdt * 1.35 or (equity_usdt > 0 and snapped_notional > max_safe_notional * 1.02):
                return (
                    "",
                    0.0,
                    0.0,
                    ct_val,
                    f"取整后最小下单额 {snapped_notional:.2f}U 超出单笔/保证金安全上限",
                )
        if contracts <= 0:
            return "", 0.0, 0.0, ct_val, "取整后委托张数为 0"
        base_qty = contracts * ct_val
        return sz_str, contracts, base_qty, ct_val, ""

    async def reconcile_order(
        self,
        cfg: SymbolStrategy,
        order_id: str,
        expected_qty: float,
        fallback_price: float,
        exchange_cfg: Optional[dict] = None,
        ct_val: Optional[float] = None,
        cl_ord_id: str = "",
    ) -> OrderResult:
        t0 = mono_ms()
        risk = self._get_risk_cfg()
        if risk.trading_mode == "LIVE":
            creds = self._extract_okx_creds(exchange_cfg)
            if not creds:
                return OrderResult(
                    order_id=str(order_id),
                    status="UNCERTAIN",
                    filled_qty=0.0,
                    avg_price=fallback_price,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg="实盘对账缺少有效 OKX API 凭证，保持 UNCERTAIN 待人工/同步确认",
                    cl_ord_id=cl_ord_id,
                )
            inst_id = to_okx_inst_id(cfg.symbol)
            spec = resolve_okx_spec(cfg.symbol)
            eff_ct_val = ct_val if (ct_val and ct_val > 0) else float(spec["ctVal"])
            proxy = get_system_proxy()
            use_cl = bool(cl_ord_id) and (not order_id or str(order_id).startswith(("ioc-", "ord-")))
            if not use_cl and (not order_id or str(order_id).startswith(("ioc-", "ord-"))):
                return OrderResult(
                    order_id=str(order_id),
                    status="UNCERTAIN",
                    filled_qty=0.0,
                    avg_price=fallback_price,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg="无有效交易所订单号/clOrdId，保持 UNCERTAIN 等待持仓同步",
                    cl_ord_id=cl_ord_id,
                )
            try:
                async with self._borrow_http_client(proxy, timeout=4.0) as client:
                    await self.ensure_okx_account_meta(client, creds)
                    q_path = (
                        f"/api/v5/trade/order?instId={inst_id}&clOrdId={cl_ord_id}"
                        if use_cl
                        else f"/api/v5/trade/order?instId={inst_id}&ordId={order_id}"
                    )
                    row = None
                    q_code = ""
                    q_msg = ""
                    for poll_i in range(3):
                        q_json = await self._okx_request(client, creds, "GET", q_path)
                        q_code = str(q_json.get("code", ""))
                        q_msg = str(q_json.get("msg", ""))
                        q_data = q_json.get("data", []) if q_code == "0" else []
                        if q_data:
                            row = q_data[0]
                            st_chk = str(row.get("state") or "")
                            if st_chk not in ("live", "partially_filled"):
                                break
                            await asyncio.sleep(0.08 * (poll_i + 1))
                        elif q_code == "51603":
                            # Order evicted from active cache -> fall back to OKX orders-history!
                            hist_json = await self._okx_request(
                                client,
                                creds,
                                "GET",
                                f"/api/v5/trade/orders-history?instType=SWAP&instId={inst_id}&limit=20",
                            )
                            if str(hist_json.get("code", "")) == "0":
                                for hr in hist_json.get("data", []):
                                    if (use_cl and str(hr.get("clOrdId") or "") == cl_ord_id) or (
                                        not use_cl and str(hr.get("ordId") or "") == str(order_id)
                                    ):
                                        row = hr
                                        break
                            break
                        else:
                            break

                    if row:
                        acc_contracts = float(row.get("accFillSz") or 0.0)
                        filled_base = acc_contracts * eff_ct_val
                        avg_px = float(row.get("avgPx") or fallback_price) if acc_contracts > 0 else 0.0
                        state = str(row.get("state") or "")
                        real_ord_id = str(row.get("ordId") or order_id)
                        fee_u, fee_c = self._parse_okx_fee_usdt(row)
                        if state in ("live", "partially_filled") and acc_contracts <= 0:
                            return OrderResult(
                                order_id=real_ord_id,
                                status="UNCERTAIN",
                                filled_qty=0.0,
                                avg_price=fallback_price,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg=f"OKX 订单仍在撮合中 (state={state})，保持 UNCERTAIN 等待持仓同步",
                                contracts=0.0,
                                ct_val=eff_ct_val,
                                cl_ord_id=cl_ord_id,
                            )
                        if acc_contracts > 0:
                            return OrderResult(
                                order_id=real_ord_id,
                                status="FILLED" if (state == "filled" or expected_qty <= 0 or filled_base >= expected_qty * 0.99) else "PARTIAL",
                                filled_qty=filled_base,
                                avg_price=avg_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg=f"OKX 订单对账确认 (state={state}, fill_sz={acc_contracts})",
                                contracts=acc_contracts,
                                ct_val=eff_ct_val,
                                cl_ord_id=cl_ord_id,
                                fee_usdt=fee_u,
                                fee_ccy=fee_c,
                            )
                        return OrderResult(
                            order_id=real_ord_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"OKX 订单未成交 (state={state})",
                            contracts=0.0,
                            ct_val=eff_ct_val,
                            cl_ord_id=cl_ord_id,
                        )
                    return OrderResult(
                        order_id=str(order_id),
                        status="UNCERTAIN",
                        filled_qty=0.0,
                        avg_price=fallback_price,
                        latency_ms=round(mono_ms() - t0, 2),
                        is_maker=False,
                        raw_msg=f"OKX 订单对账未返回记录 (code={q_code}, msg={q_msg})",
                        cl_ord_id=cl_ord_id,
                    )
            except Exception as e:
                return OrderResult(
                    order_id=str(order_id),
                    status="UNCERTAIN",
                    filled_qty=0.0,
                    avg_price=fallback_price,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg=f"OKX 订单对账异常 ({type(e).__name__})",
                    cl_ord_id=cl_ord_id,
                )

        # SHADOW mode reconciliation simulation
        await asyncio.sleep(0.015)
        return OrderResult(
            order_id=order_id,
            status="FILLED",
            filled_qty=expected_qty,
            avg_price=fallback_price,
            latency_ms=round(mono_ms() - t0, 2),
            is_maker=False,
            raw_msg="Reconciled via shadow check",
        )

    def get_slot_protective_status(self, slot_key: str) -> Dict[str, Any]:
        tp = self._active_maker_tp_orders.get(slot_key) or {}
        sl = self._active_cond_sl_orders.get(slot_key) or {}
        return {
            "tp_order_id": str(tp.get("order_id") or "") if tp.get("is_live") else "",
            "tp_is_live": bool(tp.get("is_live", False)),
            "tp_price": float(tp.get("tp_price") or 0.0),
            "sl_order_id": str(sl.get("order_id") or "") if sl.get("is_live") else "",
            "sl_is_live": bool(sl.get("is_live", False)),
            "sl_price": float(sl.get("sl_price") or 0.0),
            "sl_breached": bool(sl.get("sl_breached", False)),
        }

    def restore_slot_protective_tracking(
        self,
        symbol: str,
        slot_key: str,
        side: str,
        qty: float,
        contracts: float,
        ct_val: float,
        tp_price: float,
        sl_price: float,
        maker_tp_order_id: str = "",
        cond_sl_algo_id: str = "",
        exchange_cfg: Optional[dict] = None,
        mgn_mode: str = "cross",
    ):
        inst_id = to_okx_inst_id(symbol)
        close_side = "sell" if side == "LONG" else "buy"
        spec = resolve_okx_spec(symbol)
        eff_ct_val = ct_val if (ct_val and ct_val > 0) else float(spec["ctVal"])
        eff_contracts = contracts if (contracts and contracts > 0) else (qty / max(1e-12, eff_ct_val))
        sz_str, q_contracts = quantize_decimal_step(eff_contracts, str(spec["lotSz_str"]), rounding="down")
        eff_mgn = "isolated" if str(mgn_mode or "cross").lower() == "isolated" else "cross"
        if maker_tp_order_id and not str(maker_tp_order_id).startswith("tp-po-"):
            self._active_maker_tp_orders[slot_key] = {
                "order_id": str(maker_tp_order_id),
                "is_live": True,
                "symbol": symbol,
                "inst_id": inst_id,
                "side": close_side,
                "mgn_mode": eff_mgn,
                "tp_price": float(tp_price or 0.0),
                "qty": float(qty or 0.0),
                "contracts": q_contracts or eff_contracts,
                "ct_val": eff_ct_val,
                "sz_str": sz_str,
                "exchange_cfg": exchange_cfg,
            }
        if cond_sl_algo_id and not str(cond_sl_algo_id).startswith("sl-cond-"):
            self._active_cond_sl_orders[slot_key] = {
                "order_id": str(cond_sl_algo_id),
                "is_live": True,
                "symbol": symbol,
                "inst_id": inst_id,
                "side": close_side,
                "mgn_mode": eff_mgn,
                "sl_price": float(sl_price or 0.0),
                "qty": float(qty or 0.0),
                "contracts": q_contracts or eff_contracts,
                "ct_val": eff_ct_val,
                "sz_str": sz_str,
                "exchange_cfg": exchange_cfg,
            }

    async def place_protective_orders(
        self,
        cfg: SymbolStrategy,
        side: str,
        qty: float,
        tp_price: float,
        sl_price: float,
        exchange_cfg: Optional[dict] = None,
        contracts: Optional[float] = None,
        ct_val: Optional[float] = None,
        order_key: Optional[str] = None,
        mgn_mode: str = "cross",
    ) -> tuple[str, str, bool]:
        slot_key = order_key or cfg.symbol
        tp_order_id = f"tp-po-{uuid.uuid4().hex[:8]}"
        sl_order_id = f"sl-cond-{uuid.uuid4().hex[:8]}"
        eff_mgn = "isolated" if str(mgn_mode or "cross").lower() == "isolated" else "cross"
        sl_trig_type = "last" if str(getattr(cfg, "sl_trigger_px_type", "mark") or "mark") == "last" else "mark"

        m_state = self._get_math_state(cfg.symbol)
        f_book = m_state.latest_follower if m_state else None
        spec = resolve_okx_spec(cfg.symbol)
        eff_ct_val = ct_val if (ct_val and ct_val > 0) else float(spec["ctVal"])
        eff_contracts = contracts if (contracts is not None and contracts > 0) else (qty / max(1e-12, eff_ct_val))
        sz_str, q_contracts = quantize_decimal_step(eff_contracts, str(spec["lotSz_str"]), rounding="down")
        # Never snap UP above actual held contracts (would fail or steal sibling slots!)
        if q_contracts < float(spec["minSz"]):
            q_contracts = 0.0

        tp_px_str, q_tp_px = (
            quantize_decimal_step(tp_price, str(spec["tickSz_str"]), rounding=("up" if side == "LONG" else "down"))
            if tp_price > 0
            else ("", 0.0)
        )
        sl_px_str, q_sl_px = (
            quantize_decimal_step(sl_price, str(spec["tickSz_str"]), rounding="nearest")
            if sl_price > 0
            else ("", 0.0)
        )

        # Check crossing on QUANTIZED price (not raw tp_price!)
        post_only_crossed = False
        if f_book and cfg.use_post_only_tp and q_tp_px > 0:
            if side == "LONG" and f_book.bid >= q_tp_px:
                post_only_crossed = True
            elif side == "SHORT" and f_book.ask <= q_tp_px:
                post_only_crossed = True

        sl_breached = False
        sl_dir_safe = q_sl_px > 0 and (
            not f_book
            or (side == "LONG" and q_sl_px < f_book.bid)
            or (side == "SHORT" and q_sl_px > f_book.ask)
        )
        if q_sl_px > 0 and not sl_dir_safe:
            sl_breached = True

        risk = self._get_risk_cfg()
        creds = self._extract_okx_creds(exchange_cfg) if risk.trading_mode == "LIVE" else None
        sl_is_live = False
        tp_is_live = False
        inst_id = to_okx_inst_id(cfg.symbol)
        close_side = "sell" if side == "LONG" else "buy"

        # Peek existing protective orders; only remove after confirmed cancel
        prev_tp = self._active_maker_tp_orders.get(slot_key)
        prev_sl = self._active_cond_sl_orders.get(slot_key)

        if creds and q_contracts > 0:
            proxy = get_system_proxy()
            try:
                async with self._borrow_http_client(proxy, timeout=4.0) as client:
                    if prev_tp and prev_tp.get("is_live") and prev_tp.get("order_id"):
                        try:
                            c_tp = await self._okx_request(
                                client,
                                creds,
                                "POST",
                                "/api/v5/trade/cancel-order",
                                {"instId": inst_id, "ordId": str(prev_tp["order_id"])},
                            )
                            if str(c_tp.get("code", "")) == "0":
                                self._active_maker_tp_orders.pop(slot_key, None)
                        except Exception:
                            pass
                    else:
                        self._active_maker_tp_orders.pop(slot_key, None)

                    if prev_sl and prev_sl.get("is_live") and prev_sl.get("order_id"):
                        try:
                            c_sl = await self._okx_request(
                                client,
                                creds,
                                "POST",
                                "/api/v5/trade/cancel-algos",
                                [{"algoId": str(prev_sl["order_id"]), "instId": inst_id}],
                            )
                            if str(c_sl.get("code", "")) == "0":
                                self._active_cond_sl_orders.pop(slot_key, None)
                        except Exception:
                            pass
                    else:
                        self._active_cond_sl_orders.pop(slot_key, None)

                    meta = await self.ensure_okx_account_meta(client, creds)
                    pos_mode = meta.get("posMode", "net_mode")
                    mode_fields = self._okx_close_mode_fields(side, pos_mode)

                    async def _place_tp():
                        nonlocal tp_order_id, tp_is_live, post_only_crossed
                        if not (cfg.use_post_only_tp and q_tp_px > 0 and not post_only_crossed):
                            return
                        tp_cl_id = f"tp{uuid.uuid4().hex[:24]}"
                        tp_payload = {
                            "instId": inst_id,
                            "tdMode": eff_mgn,
                            "side": close_side,
                            "ordType": "post_only",
                            "px": tp_px_str,
                            "sz": sz_str,
                            "clOrdId": tp_cl_id,
                            **mode_fields,
                        }
                        tp_res = await self._okx_request(client, creds, "POST", "/api/v5/trade/order", tp_payload)
                        tp_data = tp_res.get("data", [])
                        if str(tp_res.get("code", "")) == "0" and tp_data and str(tp_data[0].get("sCode", "0")) == "0":
                            cand_ord_id = str(tp_data[0].get("ordId", tp_order_id))
                            # Verify OKX did not immediately auto-cancel post_only for crossing book
                            still_live = True
                            try:
                                vq = await self._okx_request(
                                    client, creds, "GET", f"/api/v5/trade/order?instId={inst_id}&ordId={cand_ord_id}"
                                )
                                vrows = vq.get("data", []) if str(vq.get("code", "")) == "0" else []
                                if vrows:
                                    vst = str(vrows[0].get("state") or "")
                                    if vst in ("canceled", "mmp_canceled") and float(vrows[0].get("accFillSz") or 0.0) <= 0:
                                        still_live = False
                                        post_only_crossed = True
                            except Exception:
                                pass
                            if still_live:
                                tp_order_id = cand_ord_id
                                tp_is_live = True
                        else:
                            s_msg = str((tp_data[0].get("sMsg") if tp_data else tp_res.get("msg")) or "").lower()
                            if "post only" in s_msg or "immediately" in s_msg:
                                post_only_crossed = True

                    async def _place_sl():
                        nonlocal sl_order_id, sl_is_live, sl_breached
                        if not sl_dir_safe:
                            return
                        sl_cl_id = f"sl{uuid.uuid4().hex[:24]}"
                        sl_payload = {
                            "instId": inst_id,
                            "tdMode": eff_mgn,
                            "side": close_side,
                            "ordType": "conditional",
                            "sz": sz_str,
                            "slTriggerPx": sl_px_str,
                            "slTriggerPxType": sl_trig_type,
                            "slOrdPx": "-1",
                            "algoClOrdId": sl_cl_id,
                            **mode_fields,
                        }
                        sl_res = await self._okx_request(client, creds, "POST", "/api/v5/trade/order-algo", sl_payload)
                        sl_data = sl_res.get("data", [])
                        if (
                            str(sl_res.get("code", "")) == "0"
                            and sl_data
                            and str(sl_data[0].get("sCode", "0")) == "0"
                            and sl_data[0].get("algoId")
                        ):
                            sl_order_id = str(sl_data[0]["algoId"])
                            sl_is_live = True
                        else:
                            sc = str(sl_data[0].get("sCode", "")) if sl_data else str(sl_res.get("code", ""))
                            if sc in ("51278", "51280"):
                                sl_breached = True

                    # Option A in OKX `long_short_mode`: resting Post-Only TP freezes `availPos`, so do NOT
                    # place a conflicting unlinked conditional SL when Maker Post-Only TP is live!
                    # (Software SL cancels Maker TP first then market-closes cleanly; in TREND_BREAKOUT where
                    # tp_price==0 or when Maker TP is off/failed, native OKX conditional SL stays 100% live!)
                    if pos_mode == "long_short_mode" and cfg.use_post_only_tp and q_tp_px > 0 and not post_only_crossed:
                        await _place_tp()
                        if not tp_is_live:
                            await _place_sl()
                    else:
                        await _place_sl()
                        await _place_tp()
            except Exception:
                pass

        if creds:
            # In LIVE mode, ONLY store confirmed live OKX order IDs (never fake UUIDs!)
            if sl_is_live:
                self._active_cond_sl_orders[slot_key] = {
                    "order_id": sl_order_id,
                    "is_live": True,
                    "symbol": cfg.symbol,
                    "inst_id": inst_id,
                    "side": close_side,
                    "mgn_mode": eff_mgn,
                    "sl_price": sl_price,
                    "sl_breached": False,
                    "qty": qty,
                    "contracts": q_contracts,
                    "ct_val": eff_ct_val,
                    "sz_str": sz_str,
                    "exchange_cfg": exchange_cfg,
                }
            elif sl_breached:
                self._active_cond_sl_orders[slot_key] = {
                    "order_id": "",
                    "is_live": False,
                    "sl_breached": True,
                    "symbol": cfg.symbol,
                    "inst_id": inst_id,
                    "side": close_side,
                    "mgn_mode": eff_mgn,
                    "sl_price": sl_price,
                    "qty": qty,
                    "contracts": q_contracts,
                    "ct_val": eff_ct_val,
                    "sz_str": sz_str,
                    "exchange_cfg": exchange_cfg,
                }
            if tp_is_live:
                self._active_maker_tp_orders[slot_key] = {
                    "order_id": tp_order_id,
                    "is_live": True,
                    "symbol": cfg.symbol,
                    "inst_id": inst_id,
                    "side": close_side,
                    "mgn_mode": eff_mgn,
                    "tp_price": tp_price,
                    "qty": qty,
                    "contracts": q_contracts,
                    "ct_val": eff_ct_val,
                    "sz_str": sz_str,
                    "exchange_cfg": exchange_cfg,
                }
        else:
            # SHADOW simulation tracking
            if sl_price > 0:
                self._active_cond_sl_orders[slot_key] = {
                    "order_id": sl_order_id,
                    "is_live": False,
                    "symbol": cfg.symbol,
                    "inst_id": inst_id,
                    "side": close_side,
                    "mgn_mode": eff_mgn,
                    "sl_price": sl_price,
                    "qty": qty,
                    "contracts": q_contracts,
                    "ct_val": eff_ct_val,
                    "sz_str": sz_str,
                    "exchange_cfg": exchange_cfg,
                }
            if cfg.use_post_only_tp and tp_price > 0 and not post_only_crossed:
                self._active_maker_tp_orders[slot_key] = {
                    "order_id": tp_order_id,
                    "is_live": False,
                    "symbol": cfg.symbol,
                    "inst_id": inst_id,
                    "side": close_side,
                    "mgn_mode": eff_mgn,
                    "tp_price": tp_price,
                    "qty": qty,
                    "contracts": q_contracts,
                    "ct_val": eff_ct_val,
                    "sz_str": sz_str,
                    "exchange_cfg": exchange_cfg,
                }

        return tp_order_id, sl_order_id, post_only_crossed

    async def update_slot_sl_order(
        self,
        cfg: SymbolStrategy,
        slot_key: str,
        side: str,
        qty: float,
        new_sl_price: float,
        exchange_cfg: Optional[dict] = None,
        contracts: Optional[float] = None,
        ct_val: Optional[float] = None,
        mgn_mode: str = "cross",
    ) -> Dict[str, Any]:
        """
        Atomically updates an existing slot's OKX conditional SL algo (e.g. Livermore step-up protection)
        using OKX `POST /api/v5/trade/amend-algos` first (zero naked window!), with safe verified
        cancel+replace fallback if amend is unavailable. Never drops an old live SL into an orphan!
        """
        prev_sl = self._active_cond_sl_orders.get(slot_key)
        prev_aid = str(prev_sl.get("order_id") or "") if (prev_sl and prev_sl.get("is_live")) else ""
        kept_resp = {"status": "kept_old", "algo_id": prev_aid, "sl_is_live": bool(prev_aid)}

        risk = self._get_risk_cfg()
        creds = self._extract_okx_creds(exchange_cfg) if risk.trading_mode == "LIVE" else None
        if not creds or new_sl_price <= 0:
            return kept_resp

        spec = resolve_okx_spec(cfg.symbol)
        eff_ct_val = ct_val if (ct_val and ct_val > 0) else float(spec["ctVal"])
        eff_contracts = contracts if (contracts is not None and contracts > 0) else (qty / max(1e-12, eff_ct_val))
        sz_str, q_contracts = quantize_decimal_step(eff_contracts, str(spec["lotSz_str"]), rounding="down")
        if q_contracts < float(spec["minSz"]):
            return kept_resp

        sl_px_str, q_sl_px = quantize_decimal_step(new_sl_price, str(spec["tickSz_str"]), rounding="nearest")
        m_state = self._get_math_state(cfg.symbol)
        f_book = m_state.latest_follower if m_state else None
        if f_book:
            if (side == "LONG" and q_sl_px >= f_book.bid) or (side == "SHORT" and q_sl_px <= f_book.ask):
                # Do NOT wipe existing live SL into an orphan! Keep old SL tracked.
                return kept_resp

        inst_id = to_okx_inst_id(cfg.symbol)
        close_side = "sell" if side == "LONG" else "buy"
        eff_mgn = "isolated" if str(mgn_mode or (prev_sl or {}).get("mgn_mode") or "cross").lower() == "isolated" else "cross"
        sl_trig_type = "last" if str(getattr(cfg, "sl_trigger_px_type", "mark") or "mark") == "last" else "mark"
        proxy = get_system_proxy()

        try:
            async with self._borrow_http_client(proxy, timeout=4.0) as client:
                meta = await self.ensure_okx_account_meta(client, creds)
                pos_mode = meta.get("posMode", "net_mode")
                # Under Option A, never place an unlinked conditional SL if a live Maker TP is holding availPos in long_short_mode
                cur_tp = self._active_maker_tp_orders.get(slot_key)
                if pos_mode == "long_short_mode" and cur_tp and cur_tp.get("is_live"):
                    return kept_resp

                # 1. Preferred path: atomic OKX `amend-algos` (zero naked window!)
                if prev_aid:
                    try:
                        amend_payload = {
                            "instId": inst_id,
                            "algoId": prev_aid,
                            "newSlTriggerPx": sl_px_str,
                            "newSlOrdPx": "-1",
                            "newSlTriggerPxType": sl_trig_type,
                        }
                        am_res = await self._okx_request(
                            client, creds, "POST", "/api/v5/trade/amend-algos", amend_payload
                        )
                        am_data = am_res.get("data", [])
                        if (
                            str(am_res.get("code", "")) == "0"
                            and am_data
                            and str(am_data[0].get("sCode", "0")) == "0"
                            and am_data[0].get("algoId")
                        ):
                            am_id = str(am_data[0]["algoId"])
                            if prev_sl is not None:
                                prev_sl["order_id"] = am_id
                                prev_sl["sl_price"] = new_sl_price
                                prev_sl["is_live"] = True
                            return {"status": "updated", "algo_id": am_id, "sl_is_live": True}
                    except Exception:
                        pass

                    # 2. Fallback: safely cancel old algo and verify it hasn't already triggered!
                    cancel_ok = False
                    try:
                        c_res = await self._okx_request(
                            client,
                            creds,
                            "POST",
                            "/api/v5/trade/cancel-algos",
                            [{"algoId": prev_aid, "instId": inst_id}],
                        )
                        c_data = c_res.get("data", [])
                        if str(c_res.get("code", "")) == "0" and (not c_data or str(c_data[0].get("sCode", "0")) == "0"):
                            cancel_ok = True
                    except Exception:
                        cancel_ok = False

                    if not cancel_ok:
                        hist = await self.query_algo_final_status(cfg.symbol, prev_aid, exchange_cfg)
                        if hist.get("ok") and hist.get("state") in ("effective", "partially_effective") and float(hist.get("acc_fill_ct") or 0.0) > 0:
                            self._active_cond_sl_orders.pop(slot_key, None)
                            return {
                                "status": "old_triggered",
                                "algo_id": "",
                                "sl_is_live": False,
                                "fill_info": hist,
                            }
                        # Transient cancel failure: keep old SL tracked and DO NOT place a duplicate 2x SL!
                        return kept_resp
                    self._active_cond_sl_orders.pop(slot_key, None)

                mode_fields = self._okx_close_mode_fields(side, pos_mode)
                sl_payload = {
                    "instId": inst_id,
                    "tdMode": eff_mgn,
                    "side": close_side,
                    "ordType": "conditional",
                    "sz": sz_str,
                    "slTriggerPx": sl_px_str,
                    "slTriggerPxType": sl_trig_type,
                    "slOrdPx": "-1",
                    "algoClOrdId": f"sl{uuid.uuid4().hex[:24]}",
                    **mode_fields,
                }
                sl_res = await self._okx_request(client, creds, "POST", "/api/v5/trade/order-algo", sl_payload)
                sl_data = sl_res.get("data", [])
                if (
                    str(sl_res.get("code", "")) == "0"
                    and sl_data
                    and str(sl_data[0].get("sCode", "0")) == "0"
                    and sl_data[0].get("algoId")
                ):
                    new_algo_id = str(sl_data[0]["algoId"])
                    self._active_cond_sl_orders[slot_key] = {
                        "order_id": new_algo_id,
                        "is_live": True,
                        "symbol": cfg.symbol,
                        "inst_id": inst_id,
                        "side": close_side,
                        "mgn_mode": eff_mgn,
                        "sl_price": new_sl_price,
                        "qty": qty,
                        "contracts": q_contracts,
                        "ct_val": eff_ct_val,
                        "sz_str": sz_str,
                        "exchange_cfg": exchange_cfg,
                    }
                    return {"status": "updated", "algo_id": new_algo_id, "sl_is_live": True}
                return {"status": "place_failed", "algo_id": "", "sl_is_live": False}
        except Exception:
            if slot_key in self._active_cond_sl_orders and self._active_cond_sl_orders[slot_key].get("is_live"):
                return kept_resp
            return {"status": "place_failed", "algo_id": "", "sl_is_live": False}

    async def query_order_final_status(
        self, symbol: str, ord_id: str, exchange_cfg: Optional[dict] = None
    ) -> Dict[str, Any]:
        creds = self._extract_okx_creds(exchange_cfg)
        if not creds or not ord_id:
            return {"ok": False, "state": "unknown", "acc_fill_ct": 0.0, "avg_px": 0.0, "fee_usdt": 0.0}
        inst_id = to_okx_inst_id(symbol)
        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=4.0) as client:
                await self.ensure_okx_account_meta(client, creds)
                q = await self._okx_request(
                    client, creds, "GET", f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}"
                )
                code = str(q.get("code", ""))
                rows = q.get("data", []) if code == "0" else []
                if not rows and code == "51603":
                    hq = await self._okx_request(
                        client,
                        creds,
                        "GET",
                        f"/api/v5/trade/orders-history?instType=SWAP&instId={inst_id}&limit=30",
                    )
                    if str(hq.get("code", "")) == "0":
                        rows = [r for r in hq.get("data", []) if str(r.get("ordId") or "") == str(ord_id)]
                if rows:
                    r0 = rows[0]
                    fee_u, fee_c = self._parse_okx_fee_usdt(r0)
                    return {
                        "ok": True,
                        "state": str(r0.get("state") or ""),
                        "acc_fill_ct": float(r0.get("accFillSz") or 0.0),
                        "avg_px": float(r0.get("avgPx") or 0.0),
                        "ord_id": str(r0.get("ordId") or ord_id),
                        "fee_usdt": fee_u,
                        "fee_ccy": fee_c,
                    }
        except Exception:
            pass
        return {"ok": False, "state": "unknown", "acc_fill_ct": 0.0, "avg_px": 0.0, "fee_usdt": 0.0}

    async def query_algo_final_status(
        self, symbol: str, algo_id: str, exchange_cfg: Optional[dict] = None
    ) -> Dict[str, Any]:
        creds = self._extract_okx_creds(exchange_cfg)
        if not creds or not algo_id:
            return {"ok": False, "state": "unknown", "acc_fill_ct": 0.0, "avg_px": 0.0, "ord_id": "", "fee_usdt": 0.0}
        inst_id = to_okx_inst_id(symbol)
        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=4.0) as client:
                await self.ensure_okx_account_meta(client, creds)
                rows = []
                # Prefer direct single-algo lookup (covers both live & historical states), fall back to orders-algo-history
                q_direct = await self._okx_request(
                    client, creds, "GET", f"/api/v5/trade/order-algo?algoId={algo_id}"
                )
                if str(q_direct.get("code", "")) == "0" and q_direct.get("data"):
                    rows = q_direct["data"]
                if not rows:
                    q = await self._okx_request(
                        client,
                        creds,
                        "GET",
                        f"/api/v5/trade/orders-algo-history?ordType=conditional&instType=SWAP&instId={inst_id}&algoId={algo_id}",
                    )
                    rows = q.get("data", []) if str(q.get("code", "")) == "0" else []
                if rows:
                    r0 = rows[0]
                    st = str(r0.get("state") or "")
                    # OKX V5 returns triggered child order IDs in `ordIdList` (top-level `ordId` may be empty!)
                    raw_list = r0.get("ordIdList") or []
                    child_ids = [str(x) for x in raw_list if str(x or "").strip()]
                    if not child_ids and str(r0.get("ordId") or "").strip():
                        child_ids = [str(r0["ordId"])]
                    primary_child_id = child_ids[0] if child_ids else ""
                    actual_px = float(r0.get("actualPx") or r0.get("slTriggerPx") or 0.0)
                    actual_sz = float(r0.get("actualSz") or 0.0)
                    total_fee_u = 0.0
                    child_state = ""

                    if st in ("effective", "partially_effective") and child_ids:
                        sum_sz = 0.0
                        sum_notional = 0.0
                        queried_any_child = False
                        for cid in child_ids:
                            try:
                                oq = await self._okx_request(
                                    client, creds, "GET", f"/api/v5/trade/order?instId={inst_id}&ordId={cid}"
                                )
                                orows = oq.get("data", []) if str(oq.get("code", "")) == "0" else []
                                if orows:
                                    queried_any_child = True
                                    cr = orows[0]
                                    child_state = str(cr.get("state") or child_state)
                                    c_sz = float(cr.get("accFillSz") or 0.0)
                                    c_px = float(cr.get("avgPx") or 0.0)
                                    c_fee, _ = self._parse_okx_fee_usdt(cr)
                                    total_fee_u += c_fee
                                    if c_sz > 0:
                                        sum_sz += c_sz
                                        if c_px > 0:
                                            sum_notional += c_sz * c_px
                            except Exception:
                                pass
                        if queried_any_child:
                            # Trust child order accFillSz EVEN WHEN 0 (never fabricate fill from requested sz!)
                            actual_sz = sum_sz
                            if sum_sz > 0 and sum_notional > 0:
                                actual_px = sum_notional / sum_sz
                    elif st in ("effective", "partially_effective") and actual_sz <= 0:
                        # No child order ID returned yet -> use actualSz only (if present, else fallback sz only for legacy mocks)
                        actual_sz = float(r0.get("actualSz") or r0.get("sz") or 0.0)

                    return {
                        "ok": True,
                        "state": st,
                        "child_state": child_state,
                        "acc_fill_ct": actual_sz if st in ("effective", "partially_effective") else 0.0,
                        "avg_px": actual_px,
                        "ord_id": primary_child_id or algo_id,
                        "fee_usdt": round(total_fee_u, 6),
                    }
        except Exception:
            pass
        return {"ok": False, "state": "unknown", "acc_fill_ct": 0.0, "avg_px": 0.0, "ord_id": "", "fee_usdt": 0.0}

    async def fetch_live_pending_protective_ids(self, exchange_cfg: Optional[dict]) -> Dict[str, Any]:
        """Bulk-fetches ALL pending SWAP limit orders & conditional SL algos with cursor pagination (>100 safe!)."""
        creds = self._extract_okx_creds(exchange_cfg)
        if not creds:
            return {"ok": False, "pending_ord_ids": set(), "pending_algo_ids": set()}
        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=5.0) as client:
                await self.ensure_okx_account_meta(client, creds)
                ord_ids: set[str] = set()
                after_ord = ""
                for _ in range(5):
                    qs = "/api/v5/trade/orders-pending?instType=SWAP&limit=100" + (f"&after={after_ord}" if after_ord else "")
                    ord_res = await self._okx_request(client, creds, "GET", qs)
                    if str(ord_res.get("code", "")) != "0":
                        return {"ok": False, "pending_ord_ids": set(), "pending_algo_ids": set()}
                    rows = ord_res.get("data", [])
                    for r in rows:
                        if r.get("ordId"):
                            ord_ids.add(str(r["ordId"]))
                    if len(rows) < 100:
                        break
                    after_ord = str(rows[-1].get("ordId") or "")
                    if not after_ord:
                        break

                algo_ids: set[str] = set()
                after_algo = ""
                for _ in range(5):
                    qs = "/api/v5/trade/orders-algo-pending?ordType=conditional&instType=SWAP&limit=100" + (
                        f"&after={after_algo}" if after_algo else ""
                    )
                    algo_res = await self._okx_request(client, creds, "GET", qs)
                    if str(algo_res.get("code", "")) != "0":
                        return {"ok": False, "pending_ord_ids": set(), "pending_algo_ids": set()}
                    rows = algo_res.get("data", [])
                    for r in rows:
                        if r.get("algoId"):
                            algo_ids.add(str(r["algoId"]))
                    if len(rows) < 100:
                        break
                    after_algo = str(rows[-1].get("algoId") or "")
                    if not after_algo:
                        break

                return {"ok": True, "pending_ord_ids": ord_ids, "pending_algo_ids": algo_ids}
        except Exception:
            return {"ok": False, "pending_ord_ids": set(), "pending_algo_ids": set()}

    async def cancel_slot_protective_orders(
        self,
        symbol: str,
        slot_key: str,
        exchange_cfg: Optional[dict] = None,
        cancel_tp: bool = True,
        cancel_sl: bool = True,
        expected_contracts: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Fail-closed cancellation & reconciliation of live Maker TP and/or Conditional SL orders for `slot_key`.
        - Never pops tracked order IDs until OKX confirms cancellation or terminal fill!
        - Sets `reconcile_uncertain=True` if cancel/status verification cannot reach a terminal state so
          callers NEVER fire a blind duplicate market close that could wipe sibling grid slots!
        """
        res: Dict[str, Any] = {
            "tp_fully_filled": False,
            "tp_filled_ct": 0.0,
            "tp_avg_px": 0.0,
            "tp_order_id": "",
            "tp_fee_usdt": 0.0,
            "sl_already_filled": False,
            "sl_filled_ct": 0.0,
            "sl_avg_px": 0.0,
            "sl_order_id": "",
            "sl_fee_usdt": 0.0,
            "reconcile_uncertain": False,
        }
        tp_info = self._active_maker_tp_orders.get(slot_key) if cancel_tp else None
        sl_info = self._active_cond_sl_orders.get(slot_key) if cancel_sl else None
        if cancel_tp and tp_info and not tp_info.get("is_live"):
            self._active_maker_tp_orders.pop(slot_key, None)
            tp_info = None
        if cancel_sl and sl_info and not sl_info.get("is_live"):
            self._active_cond_sl_orders.pop(slot_key, None)
            sl_info = None

        eff_ex = exchange_cfg or (tp_info or {}).get("exchange_cfg") or (sl_info or {}).get("exchange_cfg")
        creds = self._extract_okx_creds(eff_ex)
        if not creds:
            return res

        inst_id = to_okx_inst_id(symbol)
        spec = resolve_okx_spec(symbol)
        lot_step_dec = Decimal(str(spec.get("lotSz_str") or "0.0001"))
        target_ct = expected_contracts or float((tp_info or sl_info or {}).get("contracts") or 0.0)
        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=5.0) as client:
                await self.ensure_okx_account_meta(client, creds)

                # 1. Check & cancel live Maker Post-Only TP order until confirmed terminal
                if tp_info and tp_info.get("is_live") and tp_info.get("order_id"):
                    tp_ord_id = str(tp_info["order_id"])
                    res["tp_order_id"] = tp_ord_id
                    tp_terminal = False
                    pre_filled = False
                    try:
                        tp_q = await self._okx_request(
                            client, creds, "GET", f"/api/v5/trade/order?instId={inst_id}&ordId={tp_ord_id}"
                        )
                        tp_rows = tp_q.get("data", []) if str(tp_q.get("code", "")) == "0" else []
                        if tp_rows:
                            r0 = tp_rows[0]
                            acc_ct = float(r0.get("accFillSz") or 0.0)
                            avg_px = float(r0.get("avgPx") or tp_info.get("tp_price") or 0.0)
                            st = str(r0.get("state") or "")
                            f_u, _ = self._parse_okx_fee_usdt(r0)
                            res["tp_filled_ct"] = acc_ct
                            res["tp_avg_px"] = avg_px
                            res["tp_fee_usdt"] = f_u
                            rem_d = Decimal(str(max(0.0, target_ct))) - Decimal(str(max(0.0, acc_ct)))
                            if st == "filled" or (target_ct > 0 and rem_d < lot_step_dec):
                                res["tp_fully_filled"] = True
                                pre_filled = True
                                tp_terminal = True
                            elif st in ("canceled", "mmp_canceled"):
                                tp_terminal = True
                    except Exception:
                        pass

                    if not pre_filled and not tp_terminal:
                        try:
                            await self._okx_request(
                                client,
                                creds,
                                "POST",
                                "/api/v5/trade/cancel-order",
                                {"instId": inst_id, "ordId": tp_ord_id},
                            )
                        except Exception:
                            pass
                        # Poll until order confirms canceled or filled (never market-close while TP is still live!)
                        for poll_i in range(3):
                            try:
                                tp_q2 = await self._okx_request(
                                    client,
                                    creds,
                                    "GET",
                                    f"/api/v5/trade/order?instId={inst_id}&ordId={tp_ord_id}",
                                )
                                tp_rows2 = tp_q2.get("data", []) if str(tp_q2.get("code", "")) == "0" else []
                                if tp_rows2:
                                    r2 = tp_rows2[0]
                                    acc_ct2 = float(r2.get("accFillSz") or 0.0)
                                    avg_px2 = float(r2.get("avgPx") or res["tp_avg_px"] or tp_info.get("tp_price") or 0.0)
                                    st2 = str(r2.get("state") or "")
                                    f_u2, _ = self._parse_okx_fee_usdt(r2)
                                    if acc_ct2 >= res["tp_filled_ct"]:
                                        res["tp_filled_ct"] = acc_ct2
                                        res["tp_avg_px"] = avg_px2
                                        res["tp_fee_usdt"] = f_u2
                                    rem_d2 = Decimal(str(max(0.0, target_ct))) - Decimal(str(max(0.0, acc_ct2)))
                                    if st2 == "filled" or (target_ct > 0 and rem_d2 < lot_step_dec):
                                        res["tp_fully_filled"] = True
                                        tp_terminal = True
                                        break
                                    if st2 in ("canceled", "mmp_canceled"):
                                        tp_terminal = True
                                        break
                                elif str(tp_q2.get("code", "")) == "51603":
                                    # Check history before assuming canceled
                                    st_h = await self.query_order_final_status(symbol, tp_ord_id, eff_ex)
                                    if st_h.get("ok"):
                                        res["tp_filled_ct"] = max(res["tp_filled_ct"], float(st_h["acc_fill_ct"]))
                                        if st_h["avg_px"] > 0:
                                            res["tp_avg_px"] = float(st_h["avg_px"])
                                        res["tp_fee_usdt"] = float(st_h.get("fee_usdt") or res["tp_fee_usdt"])
                                        if st_h["state"] == "filled":
                                            res["tp_fully_filled"] = True
                                        tp_terminal = True
                                        break
                            except Exception:
                                pass
                            await asyncio.sleep(0.12 * (poll_i + 1))

                    if tp_terminal:
                        self._active_maker_tp_orders.pop(slot_key, None)
                    else:
                        res["reconcile_uncertain"] = True

                # 2. Cancel live Conditional SL algo (and verify with backoff if it already triggered!)
                if sl_info and sl_info.get("is_live") and sl_info.get("order_id"):
                    sl_algo_id = str(sl_info["order_id"])
                    res["sl_order_id"] = sl_algo_id
                    sl_canceled_ok = False
                    try:
                        c_res = await self._okx_request(
                            client,
                            creds,
                            "POST",
                            "/api/v5/trade/cancel-algos",
                            [{"algoId": sl_algo_id, "instId": inst_id}],
                        )
                        c_data = c_res.get("data", [])
                        if str(c_res.get("code", "")) == "0" and (not c_data or str(c_data[0].get("sCode", "0")) == "0"):
                            sl_canceled_ok = True
                    except Exception:
                        sl_canceled_ok = False

                    if sl_canceled_ok:
                        self._active_cond_sl_orders.pop(slot_key, None)
                    elif not res["tp_fully_filled"]:
                        # Retry history check briefly to absorb OKX orders-algo-history eventual consistency lag!
                        sl_resolved = False
                        for h_try in range(2):
                            hist = await self.query_algo_final_status(symbol, sl_algo_id, eff_ex)
                            if hist.get("ok"):
                                h_st = str(hist.get("state") or "")
                                if h_st in ("effective", "partially_effective") and float(hist.get("acc_fill_ct") or 0.0) > 0:
                                    res["sl_already_filled"] = True
                                    res["sl_filled_ct"] = float(hist["acc_fill_ct"])
                                    res["sl_avg_px"] = float(hist.get("avg_px") or sl_info.get("sl_price") or 0.0)
                                    res["sl_order_id"] = str(hist.get("ord_id") or sl_algo_id)
                                    res["sl_fee_usdt"] = float(hist.get("fee_usdt") or 0.0)
                                    self._active_cond_sl_orders.pop(slot_key, None)
                                    sl_resolved = True
                                    break
                                if h_st in ("canceled", "order_failed", "partially_failed"):
                                    self._active_cond_sl_orders.pop(slot_key, None)
                                    sl_resolved = True
                                    break
                            if h_try == 0:
                                await asyncio.sleep(0.20)
                        if not sl_resolved:
                            res["reconcile_uncertain"] = True
        except Exception:
            if (cancel_tp and slot_key in self._active_maker_tp_orders) or (
                cancel_sl and slot_key in self._active_cond_sl_orders
            ):
                res["reconcile_uncertain"] = True
        return res

    async def execute_exit_with_retry(
        self,
        cfg: SymbolStrategy,
        side: str,
        qty: float,
        exit_reason: str,
        tp_price: float,
        max_retries: int = 5,
        exchange_cfg: Optional[dict] = None,
        contracts: Optional[float] = None,
        ct_val: Optional[float] = None,
        order_key: Optional[str] = None,
        allow_close_position_fallback: bool = True,
        mgn_mode: str = "cross",
    ) -> OrderResult:
        t0 = mono_ms()
        slot_key = order_key or cfg.symbol
        tp_info = self._active_maker_tp_orders.get(slot_key)
        sl_info = self._active_cond_sl_orders.get(slot_key)
        has_resting_maker_tp = tp_info is not None

        eff_ex_cfg = exchange_cfg or (tp_info or {}).get("exchange_cfg") or (sl_info or {}).get("exchange_cfg")
        spec = resolve_okx_spec(cfg.symbol)
        lot_step_dec = Decimal(str(spec.get("lotSz_str") or "0.0001"))
        eff_ct_val = ct_val or (tp_info or {}).get("ct_val") or (sl_info or {}).get("ct_val") or float(spec["ctVal"])
        eff_mgn = "isolated" if str(mgn_mode or (tp_info or sl_info or {}).get("mgn_mode") or "cross").lower() == "isolated" else "cross"
        eff_contracts = (
            float(contracts)
            if contracts is not None
            else float((tp_info or {}).get("contracts") or (sl_info or {}).get("contracts") or (qty / max(1e-12, eff_ct_val)))
        )

        m_state = self._get_math_state(cfg.symbol)
        f_book = m_state.latest_follower if m_state else None
        risk = self._get_risk_cfg()
        creds = self._extract_okx_creds(eff_ex_cfg) if risk.trading_mode == "LIVE" else None

        # Never fabricate fake LIVE fills for non-OKX exchanges
        if risk.trading_mode == "LIVE" and not creds:
            fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
            ex_id = (eff_ex_cfg or {}).get("exchange_id", "unknown")
            return OrderResult(
                order_id=f"exit-unsupported-{uuid.uuid4().hex[:8]}",
                status="UNCERTAIN",
                filled_qty=0.0,
                avg_price=fallback_px,
                latency_ms=round(mono_ms() - t0, 2),
                is_maker=False,
                raw_msg=f"实盘平仓拦截：交易所 {ex_id} 未配置有效 OKX 实盘执行通道",
            )

        # LIVE OKX real exit orchestration
        if creds:
            inst_id = to_okx_inst_id(cfg.symbol)
            close_side = "sell" if side == "LONG" else "buy"
            proxy = get_system_proxy()
            total_filled_ct = 0.0
            weighted_px_sum = 0.0
            total_fee_usdt = 0.0
            try:
                async with self._borrow_http_client(proxy, timeout=5.0) as client:
                    meta = await self.ensure_okx_account_meta(client, creds)
                    if not meta.get("_verified", True):
                        fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
                        return OrderResult(
                            order_id=f"exit-meta-unverified-{uuid.uuid4().hex[:8]}",
                            status="UNCERTAIN",
                            filled_qty=0.0,
                            avg_price=fallback_px,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg="OKX 账户配置未同步(/account/config 失败)，拒绝盲目按默认模式平仓",
                        )
                    pos_mode = meta.get("posMode", "net_mode")
                    mode_fields = self._okx_close_mode_fields(side, pos_mode)

                    # 1. Fail-closed protective cancel & reconciliation (catches already-filled Maker TP OR already-triggered SL!)
                    rem_contracts_dec = Decimal(str(max(0.0, eff_contracts)))
                    if (tp_info and tp_info.get("is_live")) or (sl_info and sl_info.get("is_live")):
                        c_info = await self.cancel_slot_protective_orders(
                            symbol=cfg.symbol,
                            slot_key=slot_key,
                            exchange_cfg=eff_ex_cfg,
                            cancel_tp=True,
                            cancel_sl=True,
                            expected_contracts=eff_contracts,
                        )
                        if c_info["tp_fully_filled"]:
                            tp_ct = float(c_info["tp_filled_ct"] or eff_contracts)
                            tp_px = float(c_info["tp_avg_px"] or tp_price)
                            return OrderResult(
                                order_id=c_info["tp_order_id"] or "maker-tp",
                                status="FILLED",
                                filled_qty=tp_ct * eff_ct_val,
                                avg_price=tp_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=True,
                                raw_msg="OKX 实盘 Post-Only Maker 止盈已成交",
                                contracts=tp_ct,
                                ct_val=eff_ct_val,
                                fee_usdt=float(c_info.get("tp_fee_usdt") or 0.0),
                            )
                        if c_info["sl_already_filled"]:
                            sl_ct = float(c_info["sl_filled_ct"] or eff_contracts)
                            sl_px = float(c_info["sl_avg_px"] or tp_price)
                            return OrderResult(
                                order_id=c_info["sl_order_id"] or "cond-sl",
                                status="FILLED",
                                filled_qty=sl_ct * eff_ct_val,
                                avg_price=sl_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg="OKX 实盘条件止损已触发成交（拦截重复平仓）",
                                contracts=sl_ct,
                                ct_val=eff_ct_val,
                                fee_usdt=float(c_info.get("sl_fee_usdt") or 0.0),
                            )
                        if c_info.get("reconcile_uncertain"):
                            fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
                            return OrderResult(
                                order_id=f"exit-prot-uncertain-{uuid.uuid4().hex[:8]}",
                                status="UNCERTAIN",
                                filled_qty=0.0,
                                avg_price=fallback_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg="平仓前挂单/条件止损撤单状态未决，暂缓市价平仓防双重平仓",
                            )
                        partial_tp_ct = float(c_info.get("tp_filled_ct") or 0.0)
                        if partial_tp_ct > 0:
                            # Cumulative VWAP from TP partial fill
                            total_filled_ct = partial_tp_ct
                            weighted_px_sum = partial_tp_ct * float(c_info.get("tp_avg_px") or tp_price)
                            total_fee_usdt += float(c_info.get("tp_fee_usdt") or 0.0)
                            rem_contracts_dec = max(Decimal("0"), rem_contracts_dec - Decimal(str(partial_tp_ct)))
                    else:
                        self._active_maker_tp_orders.pop(slot_key, None)
                        self._active_cond_sl_orders.pop(slot_key, None)

                    # 2. Retry aggressive reduce-only close (IOC -> Market -> optional close-position fallback)
                    rem_contracts = float(rem_contracts_dec)
                    sz_str, q_rem_contracts = quantize_decimal_step(rem_contracts, str(spec["lotSz_str"]), rounding="down")
                    if 0.0 < rem_contracts and q_rem_contracts < float(spec["minSz"]):
                        # Only close sub-minSz dust if allow_close_position_fallback is True (avoid stealing sibling slots!)
                        if allow_close_position_fallback:
                            q_rem_contracts = 0.0
                        else:
                            sz_str, q_rem_contracts = quantize_decimal_step(rem_contracts, str(spec["lotSz_str"]), rounding="up")

                    is_urgent_sl = any(tok in exit_reason for tok in ("SL", "EMERGENCY", "KILL_SWITCH"))
                    ambiguous_in_flight = False
                    for attempt in range(1, max_retries + 1):
                        if q_rem_contracts <= 0:
                            break
                        f_book = m_state.latest_follower if m_state else f_book
                        use_market = is_urgent_sl or attempt >= 2 or not f_book
                        exit_cl_id = f"ex{uuid.uuid4().hex[:24]}"

                        if use_market:
                            close_payload = {
                                "instId": inst_id,
                                "tdMode": eff_mgn,
                                "side": close_side,
                                "ordType": "market",
                                "sz": sz_str,
                                "clOrdId": exit_cl_id,
                                **mode_fields,
                            }
                        else:
                            slip = cfg.slippage_pct * (0.4 + 0.35 * attempt)
                            raw_px = f_book.bid * (1.0 - slip) if side == "LONG" else f_book.ask * (1.0 + slip)
                            px_str, _ = quantize_decimal_step(
                                raw_px, str(spec["tickSz_str"]), rounding=("down" if side == "LONG" else "up")
                            )
                            close_payload = {
                                "instId": inst_id,
                                "tdMode": eff_mgn,
                                "side": close_side,
                                "ordType": "ioc",
                                "px": px_str,
                                "sz": sz_str,
                                "clOrdId": exit_cl_id,
                                **mode_fields,
                            }

                        c_res = await self._okx_request(client, creds, "POST", "/api/v5/trade/order", close_payload)
                        c_code = str(c_res.get("code", ""))
                        c_data = c_res.get("data", [])
                        if c_code in _AMBIGUOUS_OKX_CODES:
                            # Never blind-retry an ambiguous POST (50004/502/504)! Reconcile by clOrdId first.
                            rec_ex = await self.reconcile_order(
                                cfg, "", q_rem_contracts * eff_ct_val, tp_price, eff_ex_cfg, ct_val=eff_ct_val, cl_ord_id=exit_cl_id
                            )
                            if rec_ex.status in ("FILLED", "PARTIAL") and rec_ex.contracts > 0:
                                total_filled_ct += rec_ex.contracts
                                weighted_px_sum += rec_ex.contracts * rec_ex.avg_price
                                total_fee_usdt += rec_ex.fee_usdt
                                rem_dec = max(Decimal("0"), Decimal(str(q_rem_contracts)) - Decimal(str(rec_ex.contracts)))
                                if rem_dec < lot_step_dec:
                                    final_avg_px = (weighted_px_sum / total_filled_ct) if total_filled_ct > 0 else rec_ex.avg_price
                                    return OrderResult(
                                        order_id=rec_ex.order_id,
                                        status="FILLED",
                                        filled_qty=total_filled_ct * eff_ct_val,
                                        avg_price=final_avg_px,
                                        latency_ms=round(mono_ms() - t0, 2),
                                        is_maker=False,
                                        raw_msg=f"OKX 实盘平仓对账确认 #{attempt} ({exit_reason})",
                                        contracts=total_filled_ct,
                                        ct_val=eff_ct_val,
                                        fee_usdt=round(total_fee_usdt, 6),
                                    )
                                rem_contracts = float(rem_dec)
                                sz_str, q_rem_contracts = quantize_decimal_step(rem_contracts, str(spec["lotSz_str"]), rounding="down")
                                continue
                            ambiguous_in_flight = True
                            break

                        if c_code == "0" and c_data and str(c_data[0].get("sCode", "0")) == "0":
                            okx_close_id = str(c_data[0].get("ordId", f"close-{attempt}"))
                            q_row = None
                            for p_i in range(3):
                                await asyncio.sleep(0.06 * (p_i + 1))
                                q_res = await self._okx_request(
                                    client, creds, "GET", f"/api/v5/trade/order?instId={inst_id}&ordId={okx_close_id}"
                                )
                                q_rows = q_res.get("data", []) if str(q_res.get("code", "")) == "0" else []
                                if q_rows:
                                    q_row = q_rows[0]
                                    if str(q_row.get("state") or "") not in ("live", "partially_filled"):
                                        break
                            if q_row:
                                st_q = str(q_row.get("state") or "")
                                if st_q in ("live", "partially_filled") and float(q_row.get("accFillSz") or 0.0) <= 0:
                                    # Still matching on OKX -> do NOT fire duplicate market close!
                                    ambiguous_in_flight = True
                                    break
                                acc_fill_ct = float(q_row.get("accFillSz") or 0.0)
                                avg_px = float(q_row.get("avgPx") or tp_price)
                                f_u, _ = self._parse_okx_fee_usdt(q_row)
                                total_fee_usdt += f_u
                                if acc_fill_ct > 0:
                                    total_filled_ct += acc_fill_ct
                                    weighted_px_sum += acc_fill_ct * avg_px
                                rem_dec = max(Decimal("0"), Decimal(str(q_rem_contracts)) - Decimal(str(acc_fill_ct)))
                                rem_contracts = float(rem_dec)
                                # Exact completion check (no 1% abandonment via `* 0.99`!)
                                if st_q == "filled" or rem_dec < lot_step_dec:
                                    final_avg_px = (weighted_px_sum / total_filled_ct) if total_filled_ct > 0 else avg_px
                                    return OrderResult(
                                        order_id=okx_close_id,
                                        status="FILLED",
                                        filled_qty=total_filled_ct * eff_ct_val,
                                        avg_price=final_avg_px,
                                        latency_ms=round(mono_ms() - t0, 2),
                                        is_maker=False,
                                        raw_msg=f"OKX 实盘平仓确认 #{attempt} ({exit_reason})",
                                        contracts=total_filled_ct,
                                        ct_val=eff_ct_val,
                                        fee_usdt=round(total_fee_usdt, 6),
                                    )
                                sz_str, q_rem_contracts = quantize_decimal_step(
                                    rem_contracts, str(spec["lotSz_str"]), rounding="down"
                                )
                                if 0.0 < rem_contracts and q_rem_contracts < float(spec["minSz"]):
                                    break
                            else:
                                # Query lag after successful close POST: verify position BEFORE close-position!
                                ambiguous_in_flight = True
                                break

                        await asyncio.sleep(0.05 * attempt)

                    # 3. Verify live OKX position BEFORE considering `close-position` fallback (inspect `row_ps` directly!)
                    async def _query_open_sz() -> Tuple[bool, float]:
                        pr = await self._okx_request(
                            client, creds, "GET", f"/api/v5/account/positions?instType=SWAP&instId={inst_id}"
                        )
                        if str(pr.get("code", "")) != "0":
                            return False, 0.0
                        o_sz = 0.0
                        t_ps = "long" if side == "LONG" else "short"
                        for prow in pr.get("data", []):
                            raw_p = float(prow.get("pos") or 0.0)
                            if abs(raw_p) <= 0.0:
                                continue
                            row_ps = str(prow.get("posSide") or "net").lower()
                            # Inspect row's own posSide first (prevents SHORT positive-pos misclassification if pos_mode drifted!)
                            if row_ps in ("long", "short"):
                                if row_ps != t_ps:
                                    continue
                            else:
                                if (side == "LONG" and raw_p < 0) or (side == "SHORT" and raw_p > 0):
                                    continue
                            o_sz += abs(raw_p)
                        return True, o_sz

                    fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
                    pos_ok, open_sz = await _query_open_sz()
                    if pos_ok and open_sz == 0.0:
                        final_ct = total_filled_ct if total_filled_ct > 0 else eff_contracts
                        final_px = (weighted_px_sum / total_filled_ct) if total_filled_ct > 0 else round(fallback_px, cfg.price_precision)
                        return OrderResult(
                            order_id=f"exit-verified-flat-{uuid.uuid4().hex[:8]}",
                            status="FILLED",
                            filled_qty=final_ct * eff_ct_val,
                            avg_price=final_px,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"OKX 实盘对账已空仓确认 ({exit_reason})",
                            contracts=final_ct,
                            ct_val=eff_ct_val,
                            fee_usdt=round(total_fee_usdt, 6),
                        )

                    # 4. Final fallback: ONLY invoke full-contract `close-position` when explicitly allowed & still open!
                    cp_code = ""
                    cp_msg = ""
                    if allow_close_position_fallback and not ambiguous_in_flight:
                        cp_payload: Dict[str, Any] = {"instId": inst_id, "mgnMode": eff_mgn, "autoCxl": True}
                        if pos_mode == "long_short_mode":
                            cp_payload["posSide"] = "long" if side == "LONG" else "short"
                        cp_res = await self._okx_request(
                            client, creds, "POST", "/api/v5/trade/close-position", cp_payload
                        )
                        cp_code = str(cp_res.get("code", ""))
                        cp_msg = str(cp_res.get("msg", ""))
                        if cp_code == "0":
                            # Fetch actual fill price & fee from OKX /trade/fills instead of fabricating BBO fallback!
                            real_cp_px = round(fallback_px, cfg.price_precision)
                            cp_fee = total_fee_usdt
                            try:
                                await asyncio.sleep(0.08)
                                fq = await self._okx_request(
                                    client,
                                    creds,
                                    "GET",
                                    f"/api/v5/trade/fills?instType=SWAP&instId={inst_id}&limit=5",
                                )
                                frows = fq.get("data", []) if str(fq.get("code", "")) == "0" else []
                                matched_fills = [
                                    fr for fr in frows if str(fr.get("side") or "").lower() == close_side
                                ]
                                if matched_fills:
                                    f0 = matched_fills[0]
                                    if float(f0.get("fillPx") or 0.0) > 0:
                                        real_cp_px = float(f0["fillPx"])
                                    fu, _ = self._parse_okx_fee_usdt(f0)
                                    cp_fee += fu
                            except Exception:
                                pass
                            return OrderResult(
                                order_id=f"exit-closepos-{uuid.uuid4().hex[:8]}",
                                status="FILLED",
                                filled_qty=qty,
                                avg_price=real_cp_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg=f"OKX 实盘市价全平确认 ({exit_reason})",
                                contracts=eff_contracts,
                                ct_val=eff_ct_val,
                                fee_usdt=round(cp_fee, 6),
                            )

                    return OrderResult(
                        order_id=f"exit-uncertain-{uuid.uuid4().hex[:8]}",
                        status="UNCERTAIN",
                        filled_qty=total_filled_ct * eff_ct_val,
                        avg_price=(weighted_px_sum / total_filled_ct) if total_filled_ct > 0 else fallback_px,
                        latency_ms=round(mono_ms() - t0, 2),
                        is_maker=False,
                        raw_msg=(
                            f"OKX 平仓重试未确认 (pos_ok={pos_ok}, open_sz={open_sz}, cp={cp_code}:{cp_msg})"
                        ),
                        contracts=total_filled_ct,
                        ct_val=eff_ct_val,
                        fee_usdt=round(total_fee_usdt, 6),
                    )
            except Exception as e:
                fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
                return OrderResult(
                    order_id=f"exit-err-{uuid.uuid4().hex[:8]}",
                    status="UNCERTAIN",
                    filled_qty=total_filled_ct * eff_ct_val,
                    avg_price=(weighted_px_sum / total_filled_ct) if total_filled_ct > 0 else fallback_px,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg=f"OKX 实盘平仓网络异常 ({type(e).__name__})",
                    contracts=total_filled_ct,
                    ct_val=eff_ct_val,
                    fee_usdt=round(total_fee_usdt, 6),
                )

        # SHADOW simulation / non-OKX fallback
        self._active_maker_tp_orders.pop(slot_key, None)
        self._active_cond_sl_orders.pop(slot_key, None)
        if exit_reason in ("TAKE_PROFIT", "GRID_TP") and cfg.use_post_only_tp and has_resting_maker_tp:
            await asyncio.sleep(0.004)
            return OrderResult(
                order_id=f"exit-maker-{uuid.uuid4().hex[:8]}",
                status="FILLED",
                filled_qty=qty,
                avg_price=round(tp_price, cfg.price_precision),
                latency_ms=round(mono_ms() - t0, 2),
                is_maker=True,
                raw_msg="Post-Only Maker TP filled with 0 slippage",
            )

        for attempt in range(1, max_retries + 1):
            try:
                await self.rate_limiter.acquire(1.0)
                rtt_ms = self.sample_network_latency_ms()
                await asyncio.sleep((rtt_ms * 0.5) / 1000.0)

                f_book = m_state.latest_follower if m_state else f_book
                if f_book:
                    if side == "LONG":
                        raw_exit = f_book.bid * (1.0 - cfg.slippage_pct * 0.25)
                    else:
                        raw_exit = f_book.ask * (1.0 + cfg.slippage_pct * 0.25)
                else:
                    raw_exit = tp_price

                return OrderResult(
                    order_id=f"exit-ioc-{uuid.uuid4().hex[:8]}",
                    status="FILLED",
                    filled_qty=qty,
                    avg_price=round(raw_exit, cfg.price_precision),
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg=f"Reduce-only Taker exit on attempt #{attempt} ({exit_reason})",
                )
            except Exception:
                await asyncio.sleep(0.02 * attempt)

        fallback_px = (f_book.bid if side == "LONG" else f_book.ask) if f_book else tp_price
        return OrderResult(
            order_id=f"exit-emerg-{uuid.uuid4().hex[:8]}",
            status="FILLED",
            filled_qty=qty,
            avg_price=round(fallback_px, cfg.price_precision),
            latency_ms=round(mono_ms() - t0, 2),
            is_maker=False,
            raw_msg="Emergency forced close completed",
        )

    async def fetch_live_positions(self, exchange_cfg: Optional[dict]) -> Dict[str, Any]:
        """
        Fetches real open SWAP positions from OKX (`GET /api/v5/account/positions`)
        so restarts, pauses, or externally opened positions remain synchronized in strategies and UI.
        """
        creds = self._extract_okx_creds(exchange_cfg)
        if not creds:
            return {"ok": False, "pos_mode": "net_mode", "positions": [], "error": "OKX API 凭证未完整配置"}

        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=8.0) as client:
                meta = await self.ensure_okx_account_meta(client, creds)
                res = await self._okx_request(client, creds, "GET", "/api/v5/account/positions?instType=SWAP")
                if str(res.get("code", "")) != "0":
                    return {
                        "ok": False,
                        "pos_mode": meta.get("posMode", "net_mode"),
                        "positions": [],
                        "error": str(res.get("msg") or f"code={res.get('code')}"),
                    }

                parsed: List[dict] = []
                for row in res.get("data", []):
                    inst_id = str(row.get("instId") or "")
                    if not inst_id.endswith("-USDT-SWAP"):
                        continue
                    try:
                        raw_pos = float(row.get("pos") or 0.0)
                    except (TypeError, ValueError):
                        raw_pos = 0.0
                    if abs(raw_pos) <= 0.0:
                        continue

                    pos_side = str(row.get("posSide") or "net").lower()
                    if pos_side == "long":
                        side = "LONG"
                    elif pos_side == "short":
                        side = "SHORT"
                    else:
                        side = "LONG" if raw_pos > 0 else "SHORT"

                    sym = okx_inst_id_to_unified(inst_id)
                    spec = resolve_okx_spec(sym)
                    ct_val = float(spec.get("ctVal") or 1.0)
                    contracts = abs(raw_pos)
                    qty = contracts * ct_val
                    entry_px = float(row.get("avgPx") or 0.0)
                    mark_px = float(row.get("markPx") or row.get("last") or entry_px)
                    notional = float(row.get("notionalUsd") or (qty * mark_px if mark_px > 0 else qty * entry_px))
                    lever = max(1, int(round(float(row.get("lever") or 5.0))))
                    upl_usdt = float(row.get("upl") or 0.0)
                    upl_ratio = float(row.get("uplRatio") or 0.0)
                    imr = float(row.get("imr") or row.get("margin") or 0.0)
                    avail_pos = float(row.get("availPos") or contracts)
                    mgn_mode = str(row.get("mgnMode") or "cross")
                    c_time = int(float(row.get("cTime") or 0))
                    parsed.append(
                        {
                            "inst_id": inst_id,
                            "symbol": sym,
                            "side": side,
                            "pos_side": pos_side,
                            "contracts": contracts,
                            "avail_contracts": abs(avail_pos),
                            "ct_val": ct_val,
                            "qty": qty,
                            "entry_price": entry_px,
                            "mark_price": mark_px,
                            "notional_usdt": round(abs(notional), 4),
                            "margin_usdt": round(imr, 4) if imr > 0 else round(abs(notional) / lever, 4),
                            "leverage": lever,
                            "upl_usdt": round(upl_usdt, 4),
                            "upl_ratio": round(upl_ratio, 6),
                            "mgn_mode": mgn_mode,
                            "c_time_ms": c_time,
                            "pos_id": str(row.get("posId") or ""),
                        }
                    )

                return {
                    "ok": True,
                    "pos_mode": meta.get("posMode", "net_mode"),
                    "positions": parsed,
                }
        except Exception as e:
            return {"ok": False, "pos_mode": "net_mode", "positions": [], "error": type(e).__name__}

    async def close_exchange_position(
        self,
        symbol: str,
        side: str,
        mgn_mode: str = "cross",
        exchange_cfg: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Executes OKX market close-position for an active contract (strictly side-isolated in long_short_mode!)."""
        creds = self._extract_okx_creds(exchange_cfg)
        if not creds:
            return {"ok": False, "message": "未配置有效的 OKX 实盘 API 凭证"}
        inst_id = to_okx_inst_id(symbol)
        target_close_side = "sell" if side == "LONG" else "buy"
        proxy = get_system_proxy()
        try:
            async with self._borrow_http_client(proxy, timeout=8.0) as client:
                meta = await self.ensure_okx_account_meta(client, creds)
                if not meta.get("_verified", True):
                    return {"ok": False, "message": "无法确认 OKX 账户持仓模式，暂缓一键市价平仓"}
                pos_mode = meta.get("posMode", "net_mode")
                # In net_mode, verify current position direction matches `side` before blind close-position!
                if pos_mode != "long_short_mode":
                    pr = await self._okx_request(
                        client, creds, "GET", f"/api/v5/account/positions?instType=SWAP&instId={inst_id}"
                    )
                    if str(pr.get("code", "")) == "0":
                        has_matching = False
                        for prow in pr.get("data", []):
                            rp = float(prow.get("pos") or 0.0)
                            if (side == "LONG" and rp > 0) or (side == "SHORT" and rp < 0):
                                has_matching = True
                                break
                        if not has_matching:
                            return {"ok": False, "message": f"OKX 单向持仓模式下未检测到 {symbol} ({side}) 持仓，已拦截防误平反方向"}

                payload: Dict[str, Any] = {
                    "instId": inst_id,
                    "mgnMode": mgn_mode or "cross",
                    "autoCxl": True,
                }
                if pos_mode == "long_short_mode":
                    payload["posSide"] = "long" if side == "LONG" else "short"
                res = await self._okx_request(client, creds, "POST", "/api/v5/trade/close-position", payload)
                code = str(res.get("code", ""))
                if code == "0":
                    # Strictly filter by `side` in long_short_mode so closing LONG NEVER cancels live SHORT stops!
                    algos_to_cancel = []
                    for k in [key for key in list(self._active_maker_tp_orders.keys()) if key == symbol or key.startswith(f"{symbol}:")]:
                        rec = self._active_maker_tp_orders.get(k)
                        if pos_mode != "long_short_mode" or not rec or rec.get("side") == target_close_side:
                            self._active_maker_tp_orders.pop(k, None)
                    for k in [key for key in list(self._active_cond_sl_orders.keys()) if key == symbol or key.startswith(f"{symbol}:")]:
                        sl_rec = self._active_cond_sl_orders.get(k)
                        if pos_mode != "long_short_mode" or not sl_rec or sl_rec.get("side") == target_close_side:
                            self._active_cond_sl_orders.pop(k, None)
                            if sl_rec and sl_rec.get("is_live") and sl_rec.get("order_id"):
                                algos_to_cancel.append({"algoId": str(sl_rec["order_id"]), "instId": inst_id})
                    # Chunk `/api/v5/trade/cancel-algos` in batches of <= 10 per OKX V5 hard limit!
                    for idx_c in range(0, len(algos_to_cancel), 10):
                        try:
                            await self._okx_request(
                                client,
                                creds,
                                "POST",
                                "/api/v5/trade/cancel-algos",
                                algos_to_cancel[idx_c : idx_c + 10],
                            )
                        except Exception:
                            pass
                    return {"ok": True, "message": f"OKX {symbol} ({'多' if side == 'LONG' else '空'}) 已触发市价全平"}
                raw_msg = str(res.get("msg") or "平仓被拒")
                cn_err = OKX_ERROR_MAP.get(code, raw_msg)
                return {"ok": False, "message": f"OKX 市价平仓失败 ({code}): {cn_err}"}
        except Exception as e:
            return {"ok": False, "message": f"连接 OKX 平仓接口异常 ({type(e).__name__})"}

    async def _live_place_ioc_with_reconcile(
        self,
        cfg: SymbolStrategy,
        side: str,
        qty: float,
        reservation_price: float,
        exchange_cfg: dict,
        order_id: str,
        t0: float,
    ) -> OrderResult:
        """
        Real OKX V5 signed REST IOC order placement + dynamic ctVal/tickSz quantization + fill reconciliation.
        """
        ex_id = exchange_cfg.get("exchange_id", "okx")
        creds = self._extract_okx_creds(exchange_cfg) if ex_id == "okx" else None

        if ex_id == "okx":
            if not creds:
                return OrderResult(
                    order_id=order_id,
                    status="MISSED",
                    filled_qty=0.0,
                    avg_price=0.0,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg="OKX API 密钥/Passphrase 未配置完整，无法实盘开仓",
                )

            api_key = creds[0]
            inst_id = to_okx_inst_id(cfg.symbol)
            spec = resolve_okx_spec(cfg.symbol)
            # Prefer real USDT availEq when known (acctLv=2 single-currency margin), else account_equity_usdt
            usdt_avail = self.get_usdt_avail(api_key)
            acct_eq = usdt_avail if usdt_avail > 0 else float(exchange_cfg.get("account_equity_usdt") or 0.0)

            # Honor depth-aware `qty` cap for LEAD_LAG while keeping Grid order_usdt exact
            eff_order_usdt = float(cfg.order_usdt)
            if cfg.strategy_type == "LEAD_LAG" and qty > 0 and reservation_price > 0:
                eff_order_usdt = min(eff_order_usdt, qty * reservation_price)

            sz_str, contracts, _, ct_val, size_err = self._calc_okx_entry_sz(
                cfg.symbol, reservation_price, eff_order_usdt, acct_eq, cfg.leverage
            )
            if size_err:
                return OrderResult(
                    order_id=order_id,
                    status="MISSED",
                    filled_qty=0.0,
                    avg_price=0.0,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg=f"OKX 实盘下单元数据校验拦截: {size_err}",
                )

            # Round entry reservation price INWARD for LEAD_LAG (never pay above max profit bound), outward for grid IOC
            px_round = (
                ("down" if side == "LONG" else "up")
                if cfg.strategy_type == "LEAD_LAG"
                else ("up" if side == "LONG" else "down")
            )
            px_str, q_px = quantize_decimal_step(reservation_price, str(spec["tickSz_str"]), rounding=px_round)
            proxy = get_system_proxy()

            cl_ord_id = f"sn{uuid.uuid4().hex[:24]}"
            order_submitted = False
            okx_ord_id = ""
            try:
                async with self._borrow_http_client(proxy, timeout=4.0) as client:
                    meta = await self.ensure_okx_account_meta(client, creds)
                    if not meta.get("_verified", True):
                        return OrderResult(
                            order_id=order_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg="OKX 账户持仓模式未确认(/account/config 失败)，已拦截本次实盘开仓防模式冲突",
                        )
                    if meta.get("acctLv") == "1":
                        return OrderResult(
                            order_id=order_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg="OKX 当前为「简单交易模式(acctLv=1)」(51010)，禁止交易永续合约，请在 OKX 切换为单币种/跨币种保证金模式",
                        )

                    pos_mode = meta.get("posMode", "net_mode")
                    lev_ok, lev_err = await self.ensure_leverage(client, creds, inst_id, cfg.leverage, pos_mode, mgn_mode="cross", side=side)
                    if not lev_ok:
                        return OrderResult(
                            order_id=order_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"实盘开仓拦截：{lev_err}",
                        )

                    payload = {
                        "instId": inst_id,
                        "tdMode": "cross",
                        "side": "buy" if side == "LONG" else "sell",
                        "ordType": "ioc",
                        "px": px_str,
                        "sz": sz_str,
                        "clOrdId": cl_ord_id,
                        **self._okx_entry_mode_fields(side, pos_mode),
                    }
                    order_submitted = True
                    res_json = await self._okx_request(client, creds, "POST", "/api/v5/trade/order", payload)
                    code = str(res_json.get("code", ""))
                    data_arr = res_json.get("data", [])
                    if code in _AMBIGUOUS_OKX_CODES:
                        return OrderResult(
                            order_id=order_id,
                            status="UNCERTAIN",
                            filled_qty=0.0,
                            avg_price=q_px,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"OKX 报单返回未决错误 ({code}): {OKX_ERROR_MAP.get(code, res_json.get('msg', ''))}，需立即对账",
                            contracts=contracts,
                            ct_val=ct_val,
                            cl_ord_id=cl_ord_id,
                        )
                    if code != "0" or not data_arr or str(data_arr[0].get("sCode", "0")) != "0":
                        s_code = str(data_arr[0].get("sCode", code)) if data_arr else code
                        s_msg = (
                            data_arr[0].get("sMsg", res_json.get("msg", "拒单"))
                            if data_arr
                            else res_json.get("msg", "拒单")
                        )
                        cn_err = OKX_ERROR_MAP.get(s_code, s_msg)
                        return OrderResult(
                            order_id=order_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"OKX 实盘报单未成 ({s_code}): {cn_err}",
                            cl_ord_id=cl_ord_id,
                        )

                    okx_ord_id = str(data_arr[0].get("ordId", order_id))
                    q_path = f"/api/v5/trade/order?instId={inst_id}&ordId={okx_ord_id}"
                    row = None
                    for poll_i in range(3):
                        q_json = await self._okx_request(client, creds, "GET", q_path)
                        q_data = q_json.get("data", []) if str(q_json.get("code", "")) == "0" else []
                        if q_data:
                            row = q_data[0]
                            if str(row.get("state") or "") not in ("live", "partially_filled"):
                                break
                            await asyncio.sleep(0.06 * (poll_i + 1))
                        else:
                            break
                    if row:
                        acc_contracts = float(row.get("accFillSz") or 0.0)
                        filled_base_qty = acc_contracts * ct_val
                        avg_px = float(row.get("avgPx") or q_px) if acc_contracts > 0 else 0.0
                        state = str(row.get("state") or "")
                        fee_u, fee_c = self._parse_okx_fee_usdt(row)
                        if state in ("live", "partially_filled") and acc_contracts <= 0:
                            return OrderResult(
                                order_id=okx_ord_id,
                                status="UNCERTAIN",
                                filled_qty=0.0,
                                avg_price=q_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg=f"OKX IOC 报单仍在撮合 (state={state})，待对账确认",
                                contracts=contracts,
                                ct_val=ct_val,
                                cl_ord_id=cl_ord_id,
                            )
                        if acc_contracts > 0:
                            return OrderResult(
                                order_id=okx_ord_id,
                                status="FILLED" if (state == "filled" or acc_contracts >= contracts * 0.99) else "PARTIAL",
                                filled_qty=filled_base_qty,
                                avg_price=avg_px,
                                latency_ms=round(mono_ms() - t0, 2),
                                is_maker=False,
                                raw_msg=f"OKX 实盘 IOC 成交确认 ({acc_contracts} 张 ≈ {filled_base_qty:.6f} 币, ordId={okx_ord_id})",
                                contracts=acc_contracts,
                                ct_val=ct_val,
                                cl_ord_id=cl_ord_id,
                                fee_usdt=fee_u,
                                fee_ccy=fee_c,
                            )
                        return OrderResult(
                            order_id=okx_ord_id,
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=round(mono_ms() - t0, 2),
                            is_maker=False,
                            raw_msg=f"OKX 实盘 IOC 未抢到对手盘自动撤单 (state={state})",
                            cl_ord_id=cl_ord_id,
                        )
                    return OrderResult(
                        order_id=okx_ord_id,
                        status="UNCERTAIN",
                        filled_qty=0.0,
                        avg_price=q_px,
                        latency_ms=round(mono_ms() - t0, 2),
                        is_maker=False,
                        raw_msg=f"OKX 报单已提交(ordId={okx_ord_id})但未返回订单详情，待强制对账确认",
                        contracts=contracts,
                        ct_val=ct_val,
                        cl_ord_id=cl_ord_id,
                    )
            except Exception as e:
                if order_submitted:
                    # Order may already have executed on OKX -> MUST return UNCERTAIN so caller reconciles by ordId/clOrdId!
                    return OrderResult(
                        order_id=okx_ord_id or order_id,
                        status="UNCERTAIN",
                        filled_qty=0.0,
                        avg_price=q_px,
                        latency_ms=round(mono_ms() - t0, 2),
                        is_maker=False,
                        raw_msg=f"OKX 报单已发出但网络异常 ({type(e).__name__})，需立即对账确认",
                        contracts=contracts,
                        ct_val=ct_val,
                        cl_ord_id=cl_ord_id,
                    )
                return OrderResult(
                    order_id=order_id,
                    status="MISSED",
                    filled_qty=0.0,
                    avg_price=0.0,
                    latency_ms=round(mono_ms() - t0, 2),
                    is_maker=False,
                    raw_msg=f"OKX 报单前网络异常 ({type(e).__name__})",
                )

        # Never fabricate fake LIVE fills for non-OKX exchanges
        return OrderResult(
            order_id=order_id,
            status="MISSED",
            filled_qty=0.0,
            avg_price=0.0,
            latency_ms=round(mono_ms() - t0, 2),
            is_maker=False,
            raw_msg=f"实盘下单拦截：执行交易所 {ex_id} 暂未对接真实实盘下单接口（当前仅支持 OKX 实盘）",
        )

    async def preflight_check_symbols(
        self,
        exchange_cfg: Optional[dict],
        strategies: List[SymbolStrategy],
        math_states: Dict[str, SymbolMathState],
        gateway: Optional[Any] = None,
    ) -> dict:
        """
        Read-only pre-flight feasibility check for OKX LIVE trading.
        NEVER submits any order to `/api/v5/trade/order`.
        """
        if gateway is not None and hasattr(gateway, "fetch_exchange_instruments"):
            await gateway.fetch_exchange_instruments()

        blockers: List[str] = []
        warnings: List[str] = []

        if not exchange_cfg or not (exchange_cfg.get("api_key") or "").strip():
            return {
                "ok": False,
                "account": {"auth_status": "UNCONFIGURED", "auth_message": "尚未配置 OKX API 密钥"},
                "blockers": ["未配置 OKX API Key / Secret / Passphrase"],
                "warnings": [],
                "symbols": [],
            }

        account_res = await self.verify_exchange_credentials(exchange_cfg, allow_cached_fallback=False)
        equity = float(account_res.get("usdt_avail_usdt") or account_res.get("account_equity_usdt") or 0.0)
        creds = self._extract_okx_creds(exchange_cfg)

        if not account_res.get("ok"):
            blockers.append(str(account_res.get("auth_message") or "OKX 账户验证未通过"))

        running_strats = [s for s in strategies if s.is_running]
        if not running_strats:
            blockers.append("当前没有任何处于启用状态的监控合约")

        # Check live max-size readiness on first RUNNING symbol (read-only)
        max_size_probe: Optional[dict] = None
        if creds and running_strats:
            first_inst = to_okx_inst_id(running_strats[0].symbol)
            try:
                async with httpx.AsyncClient(proxy=get_system_proxy(), timeout=6.0) as client:
                    await self.ensure_okx_account_meta(client, creds)
                    ms_json = await self._okx_request(
                        client, creds, "GET", f"/api/v5/account/max-size?instId={first_inst}&tdMode=cross"
                    )
                    max_size_probe = {
                        "instId": first_inst,
                        "code": str(ms_json.get("code", "")),
                        "msg": str(ms_json.get("msg", "")),
                        "data": ms_json.get("data", []),
                    }
                    if max_size_probe["code"] != "0":
                        err_txt = OKX_ERROR_MAP.get(max_size_probe["code"], max_size_probe["msg"])
                        msg_full = f"OKX 模拟开仓额度探测被拒 ({max_size_probe['code']}): {err_txt}"
                        if msg_full not in blockers:
                            blockers.append(msg_full)
                    elif max_size_probe["data"]:
                        d0 = max_size_probe["data"][0]
                        if float(d0.get("maxBuy") or 0.0) <= 0 and float(d0.get("maxSell") or 0.0) <= 0:
                            blockers.append(f"OKX {first_inst} 可开仓张数为 0 (maxBuy=0, maxSell=0)，请检查 USDT 可用保证金")
            except Exception as e:
                warnings.append(f"max-size 只读探测网络异常: {type(e).__name__}")

        symbol_reports: List[dict] = []
        for cfg in strategies:
            if not cfg.is_running:
                continue
            inst_id = to_okx_inst_id(cfg.symbol)
            okx_spec = get_okx_instrument_spec(cfg.symbol) or resolve_okx_spec(cfg.symbol)
            bn_spec = get_binance_instrument_spec(cfg.symbol)
            m_state = math_states.get(cfg.symbol)

            f_px = 0.0
            if m_state and m_state.latest_follower:
                f_px = m_state.latest_follower.mid
            elif gateway and getattr(gateway, "real_follower_bbo", None):
                fb = gateway.real_follower_bbo.get(cfg.symbol)
                if fb:
                    f_px = (fb[0] + fb[1]) * 0.5

            l_ready = bool(m_state and m_state.latest_leader)
            if not l_ready and gateway and getattr(gateway, "real_leader_bbo", None):
                l_ready = cfg.symbol in gateway.real_leader_bbo

            ct_val = float(okx_spec.get("ctVal") or 1.0)
            lot_sz = float(okx_spec.get("lotSz") or 1.0)
            min_sz = float(okx_spec.get("minSz") or 1.0)
            tick_sz = float(okx_spec.get("tickSz") or 0.0001)
            okx_state = str(okx_spec.get("state") or "unknown")
            bn_status = str(bn_spec.get("status") or "unknown") if bn_spec else "not_found"

            sym_issues: List[str] = []
            sym_warns: List[str] = []

            if okx_state != "live":
                sym_issues.append(f"OKX 合约状态异常 ({okx_state})")
            if bn_spec and bn_status != "TRADING":
                sym_issues.append(f"Binance 合约状态异常 ({bn_status})")
            elif not bn_spec:
                sym_warns.append("Binance 无对应 USDM 永续，可能无法接收领跑端推送")

            if f_px <= 0:
                sym_warns.append("暂未收到 OKX BBO 实时报价")
                sz_str, contracts, base_qty, min_notional = "", 0.0, 0.0, 0.0
            else:
                min_notional = min_sz * ct_val * f_px
                sz_str, contracts, base_qty, _, size_err = self._calc_okx_entry_sz(
                    cfg.symbol, f_px, cfg.order_usdt, equity, cfg.leverage
                )
                if size_err:
                    sym_issues.append(size_err)
                est_base_qty = cfg.order_usdt / max(1e-12, f_px)
                if cfg.min_qty > est_base_qty:
                    sym_warns.append(
                        f"策略最小数量阈值 min_qty={cfg.min_qty} 大于按 {cfg.order_usdt}U 估算数量 {est_base_qty:.6f}（实时风控阶段可能拦截）"
                    )

            symbol_reports.append({
                "symbol": cfg.symbol,
                "instId": inst_id,
                "is_running": bool(cfg.is_running),
                "okx_state": okx_state,
                "binance_status": bn_status,
                "has_leader_bbo": l_ready,
                "follower_mid": round(f_px, 8) if f_px > 0 else 0.0,
                "ctVal": ct_val,
                "lotSz": lot_sz,
                "minSz": min_sz,
                "tickSz": tick_sz,
                "min_notional_usdt": round(min_notional, 2),
                "planned_sz": sz_str,
                "planned_base_qty": round(base_qty, 8),
                "ready": len(sym_issues) == 0 and bool(account_res.get("ok")),
                "issues": sym_issues,
                "warnings": sym_warns,
            })

        return {
            "ok": len(blockers) == 0 and all(len(s["issues"]) == 0 for s in symbol_reports),
            "account": account_res,
            "max_size_probe": max_size_probe,
            "blockers": blockers,
            "warnings": warnings,
            "symbols": symbol_reports,
        }
