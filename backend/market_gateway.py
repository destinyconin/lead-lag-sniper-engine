"""
High-Frequency Market Data Gateway (Real Exchange WebSocket + Proxy Auto-Detection + Shadow Simulator).
- Auto-detects Windows/system HTTP/HTTPS proxy (`urllib.request.getproxies()`) for both `websockets` and `httpx`.
- Streams 100% real live market data from Binance USDM (`@bookTicker` + `@aggTrade`) and OKX V5 (`bbo-tbt` + `tickers`) in LIVE mode.
- Keeps real exchange BBO cache warm in the background so switching between `SHADOW` (模拟盘) and `LIVE` (实盘通道) displays real prices in 0ms.
"""
import asyncio
import hashlib
import math
import os
import random
import time
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple
import httpx
import orjson
import websockets
from backend.math_engine import mono_ms
from backend.models import PulseInjectionRequest


def get_system_proxy() -> Optional[str]:
    """Auto-detects system proxy (e.g. http://127.0.0.1:10808 on Windows) for exchange WS & REST."""
    try:
        proxies = urllib.request.getproxies()
        proxy = (
            proxies.get("https")
            or proxies.get("http")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
        )
        if proxy and not proxy.startswith(("http://", "https://", "socks")):
            proxy = f"http://{proxy}"
        return proxy
    except Exception:
        return None


BASE_PRICES: Dict[str, float] = {
    # Mainstream
    "BTC/USDT:USDT": 91450.0,
    "ETH/USDT:USDT": 3125.00,
    "SOL/USDT:USDT": 185.400,
    "XRP/USDT:USDT": 2.4520,
    "BNB/USDT:USDT": 642.50,
    "DOGE/USDT:USDT": 0.36420,
    "ADA/USDT:USDT": 0.9850,
    "TRX/USDT:USDT": 0.2480,
    "AVAX/USDT:USDT": 36.850,
    "LINK/USDT:USDT": 21.450,
    "DOT/USDT:USDT": 7.420,
    "LTC/USDT:USDT": 108.50,
    "BCH/USDT:USDT": 465.20,
    "UNI/USDT:USDT": 12.840,
    "NEAR/USDT:USDT": 5.620,
    # Layer 1 / Layer 2 (High Lead-Lag elasticity)
    "SUI/USDT:USDT": 3.2850,
    "APT/USDT:USDT": 11.420,
    "ARB/USDT:USDT": 0.9240,
    "OP/USDT:USDT": 1.8650,
    "SEI/USDT:USDT": 0.4820,
    "TIA/USDT:USDT": 5.450,
    "INJ/USDT:USDT": 23.600,
    "FTM/USDT:USDT": 0.8420,
    "ATOM/USDT:USDT": 6.950,
    "FIL/USDT:USDT": 5.280,
    "STX/USDT:USDT": 1.740,
    "HBAR/USDT:USDT": 0.2840,
    "ICP/USDT:USDT": 10.850,
    # AI / DeFi / Mid-Cap (Strongest Lead-Lag opportunities per Review Section 2.3)
    "FET/USDT:USDT": 1.3850,
    "RENDER/USDT:USDT": 7.640,
    "TAO/USDT:USDT": 485.00,
    "WLD/USDT:USDT": 2.340,
    "AAVE/USDT:USDT": 295.40,
    "PENDLE/USDT:USDT": 4.820,
    "ENA/USDT:USDT": 0.8950,
    "ONDO/USDT:USDT": 1.3250,
    "JUP/USDT:USDT": 0.9450,
    "PYTH/USDT:USDT": 0.3820,
    "CRV/USDT:USDT": 0.8640,
    "LDO/USDT:USDT": 1.840,
    "DYDX/USDT:USDT": 1.420,
    # High-Volatility Meme Coins (1X per-token USD price matching OKX & divisor-adjusted Binance)
    "PEPE/USDT:USDT": 0.00001852,
    "WIF/USDT:USDT": 2.1850,
    "BONK/USDT:USDT": 0.00003125,
    "FLOKI/USDT:USDT": 0.00019450,
    "SHIB/USDT:USDT": 0.00002415,
    "ORDI/USDT:USDT": 32.400,
    "PNUT/USDT:USDT": 0.6840,
    "ACT/USDT:USDT": 0.3420,
    "NEIRO/USDT:USDT": 0.001045,
    "BOME/USDT:USDT": 0.006820,
    "PEOPLE/USDT:USDT": 0.04850,
    "MEME/USDT:USDT": 0.01420,
    "TURBO/USDT:USDT": 0.009450,
    # S&P 500 Index, ETFs & Flagship Constituents (标普500指数与核心成分股)
    "SPX/USDT:USDT": 5985.00,
    "SPY/USDT:USDT": 597.50,
    "VOO/USDT:USDT": 548.20,
    "IVV/USDT:USDT": 599.10,
    "ES/USDT:USDT": 6012.50,
    "SP500/USDT:USDT": 5985.00,
    "QQQ/USDT:USDT": 512.40,
    "NVDA/USDT:USDT": 138.50,
    "AAPL/USDT:USDT": 228.40,
    "MSFT/USDT:USDT": 426.80,
    "AMZN/USDT:USDT": 210.50,
    "GOOGL/USDT:USDT": 179.20,
    "META/USDT:USDT": 588.60,
    "TSLA/USDT:USDT": 352.40,
    "BRKB/USDT:USDT": 462.00,
    "AVGO/USDT:USDT": 172.80,
    "LLY/USDT:USDT": 795.00,
    "JPM/USDT:USDT": 242.60,
    "V/USDT:USDT": 308.50,
    "UNH/USDT:USDT": 592.00,
    "XOM/USDT:USDT": 118.40,
    "MA/USDT:USDT": 522.00,
    "COST/USDT:USDT": 928.00,
    "NFLX/USDT:USDT": 885.00,
    "AMD/USDT:USDT": 139.80,
    "CRM/USDT:USDT": 332.50,
    "ORCL/USDT:USDT": 186.40,
    "SNDK/USDT:USDT": 82.50,
    "SPCX/USDT:USDT": 129.60,
    "WMT/USDT:USDT": 89.40,
    "HD/USDT:USDT": 412.00,
    "PG/USDT:USDT": 171.50,
    "BAC/USDT:USDT": 46.80,
    "KO/USDT:USDT": 63.20,
    "PEP/USDT:USDT": 162.40,
    "INTC/USDT:USDT": 24.80,
    "DIS/USDT:USDT": 114.50,
    "PLTR/USDT:USDT": 64.20,
    "COIN/USDT:USDT": 308.00,
    "MSTR/USDT:USDT": 395.00,
    "UBER/USDT:USDT": 72.60,
    "GS/USDT:USDT": 594.00,
}

SP500_SYMBOL_NAMES: Dict[str, str] = {
    "SPX": "标普500指数 (S&P 500 Index)",
    "SPY": "SPDR 标普500 ETF",
    "VOO": "Vanguard 标普500 ETF",
    "IVV": "iShares 核心标普500 ETF",
    "ES": "E-mini 标普500期货",
    "SP500": "标普500永续指数",
    "QQQ": "纳指100 / 标普科技 ETF",
    "NVDA": "英伟达 (NVIDIA)",
    "AAPL": "苹果 (Apple)",
    "MSFT": "微软 (Microsoft)",
    "AMZN": "亚马逊 (Amazon)",
    "GOOGL": "谷歌 (Alphabet)",
    "META": "Meta Platforms",
    "TSLA": "特斯拉 (Tesla)",
    "BRKB": "伯克希尔 (Berkshire B)",
    "AVGO": "博通 (Broadcom)",
    "LLY": "礼来 (Eli Lilly)",
    "JPM": "摩根大通 (JPMorgan)",
    "V": "Visa",
    "UNH": "联合健康 (UnitedHealth)",
    "XOM": "埃克森美孚 (ExxonMobil)",
    "MA": "万事达 (Mastercard)",
    "COST": "开市客 (Costco)",
    "NFLX": "奈飞 (Netflix)",
    "AMD": "超威半导体 (AMD)",
    "CRM": "Salesforce",
    "ORCL": "甲骨文 (Oracle)",
    "SNDK": "闪迪 (SanDisk)",
    "SPCX": "SPAC 标普精选",
    "WMT": "沃尔玛 (Walmart)",
    "HD": "家得宝 (Home Depot)",
    "PG": "宝洁 (Procter & Gamble)",
    "BAC": "美国银行 (Bank of America)",
    "KO": "可口可乐 (Coca-Cola)",
    "PEP": "百事 (PepsiCo)",
    "INTC": "英特尔 (Intel)",
    "DIS": "迪士尼 (Disney)",
    "PLTR": "Palantir (标普新贵)",
    "COIN": "Coinbase",
    "MSTR": "MicroStrategy",
    "UBER": "Uber Technologies",
    "GS": "高盛 (Goldman Sachs)",
}


def get_default_base_price(symbol: str) -> float:
    if symbol in BASE_PRICES:
        return BASE_PRICES[symbol]
    h = int(hashlib.md5(symbol.encode("utf-8")).hexdigest()[:6], 16)
    return round(0.5 + (h % 5000) / 100.0, 4)


BINANCE_1000X_BASES = {"PEPE", "SHIB", "BONK", "FLOKI", "SATS", "RATS", "LUNC", "XEC", "CAT", "CHEEMS"}


def to_binance_stream_sym(unified_sym: str) -> str:
    """Converts 'XRP/USDT:USDT' -> 'xrpusdt', and 'PEPE/USDT:USDT' -> '1000pepeusdt'."""
    base_quote = unified_sym.split(":")[0]
    base = base_quote.split("/")[0].upper()
    raw = base_quote.replace("/", "").lower()
    if base in BINANCE_1000X_BASES and not raw.startswith("1000"):
        return f"1000{raw}"
    return raw


def get_binance_price_divisor(unified_sym: str) -> float:
    """Returns 1000.0 for symbols quoted as 1000X on Binance USDM vs 1X on OKX."""
    base = unified_sym.split("/")[0].upper()
    return 1000.0 if base in BINANCE_1000X_BASES else 1.0


def to_okx_inst_id(unified_sym: str) -> str:
    """Converts 'XRP/USDT:USDT' -> 'XRP-USDT-SWAP'."""
    base_quote = unified_sym.split(":")[0]
    return base_quote.replace("/", "-") + "-SWAP"


def okx_inst_id_to_unified(inst_id: str) -> str:
    """Converts 'XRP-USDT-SWAP' -> 'XRP/USDT:USDT'."""
    if inst_id.endswith("-USDT-SWAP"):
        base = inst_id[:-len("-USDT-SWAP")]
        return f"{base}/USDT:USDT"
    return inst_id


def to_bybit_symbol(unified_sym: str) -> str:
    """Converts 'XRP/USDT:USDT' -> 'XRPUSDT', 'PEPE/USDT:USDT' -> '1000PEPEUSDT' per Bybit V5 Linear spec."""
    base = unified_sym.split("/")[0].upper()
    if base in ("PEPE", "SHIB", "BONK", "FLOKI", "LUNC", "XEC", "RATS", "SATS", "CAT", "TURBO"):
        return f"1000{base}USDT"
    return f"{base}USDT"


def to_bitget_symbol(unified_sym: str) -> str:
    """Converts 'XRP/USDT:USDT' -> 'XRPUSDT' per Bitget V2 USDT-FUTURES spec."""
    base = unified_sym.split("/")[0].upper()
    return f"{base}USDT"


def to_gate_contract(unified_sym: str) -> str:
    """Converts 'XRP/USDT:USDT' -> 'XRP_USDT' per Gate.io V4 Futures spec."""
    base = unified_sym.split("/")[0].upper()
    return f"{base}_USDT"


def to_kraken_futures_symbol(unified_sym: str) -> str:
    """Converts 'BTC/USDT:USDT' -> 'PF_XBTUSD', 'XRP/USDT:USDT' -> 'PF_XRPUSD' per Kraken Futures v3 spec."""
    base = unified_sym.split("/")[0].upper()
    if base == "BTC":
        base = "XBT"
    elif base == "DOGE":
        base = "XDG"
    return f"PF_{base}USD"


def to_futu_moomoo_code(unified_sym: str) -> str:
    """Converts 'SPY/USDT:USDT' -> 'US.SPY', 'BRKB/USDT:USDT' -> 'US.BRK.B' per Futu/Moomoo OpenD Security code spec."""
    base = unified_sym.split("/")[0].upper()
    if base == "BRKB":
        base = "BRK.B"
    elif base == "SPX":
        return "US.SPX"
    return f"US.{base}"


def to_us_equity_symbol(unified_sym: str) -> str:
    """Converts 'SPY/USDT:USDT' -> 'SPY', 'BRKB/USDT:USDT' -> 'BRK.B' per IBKR / Charles Schwab Trader API spec."""
    base = unified_sym.split("/")[0].upper()
    if base == "BRKB":
        return "BRK.B"
    return base


# Shared module-level instrument caches (refreshed by MarketDataGateway & preflight)
_OKX_INSTRUMENT_CACHE: Dict[str, dict] = {}
_BINANCE_INSTRUMENT_CACHE: Dict[str, dict] = {}
_INSTRUMENTS_LAST_FETCH_MONO: float = 0.0


def get_okx_instrument_spec(symbol_or_inst_id: str) -> Optional[dict]:
    """Returns cached OKX SWAP specification (ctVal, lotSz, minSz, tickSz, state, maxLever)."""
    if not symbol_or_inst_id:
        return None
    inst_id = (
        symbol_or_inst_id
        if symbol_or_inst_id.endswith("-SWAP")
        else to_okx_inst_id(symbol_or_inst_id)
    )
    return _OKX_INSTRUMENT_CACHE.get(inst_id)


def get_binance_instrument_spec(unified_sym: str) -> Optional[dict]:
    """Returns cached Binance USDM specification (status, tickSize, stepSize, minQty)."""
    if not unified_sym:
        return None
    raw_sym = to_binance_stream_sym(unified_sym).upper()
    return _BINANCE_INSTRUMENT_CACHE.get(raw_sym)


class MarketDataGateway:
    def __init__(
        self,
        on_leader_bbo: Optional[Callable[[str, float, float, float, float], None]] = None,
        on_leader_trade: Optional[Callable[[str, str, float, float], None]] = None,
        on_follower_bbo: Optional[Callable[[str, float, float, float, float], None]] = None,
        on_log: Optional[Callable[[str, str, str], None]] = None,
    ):
        self._subscribers: Dict[str, dict] = {}
        self._feed_mode: str = "SIMULATOR"
        if on_leader_bbo or on_leader_trade or on_follower_bbo or on_log:
            self._subscribers["__default__"] = {
                "mode": self._feed_mode,
                "symbols": None,
                "on_leader_bbo": on_leader_bbo,
                "on_leader_trade": on_leader_trade,
                "on_follower_bbo": on_follower_bbo,
                "on_log": on_log,
            }

        self.symbols: List[str] = []
        self.binance_connected: bool = False
        self.okx_connected: bool = False
        self.active_source: str = "HIGH_FREQ_SIM"
        self.last_ws_error: str = ""
        self.proxy_url: Optional[str] = get_system_proxy()

        # Real-time cache of genuine exchange BBOs: sym -> (bid, ask, bid_qty, ask_qty)
        self.real_leader_bbo: Dict[str, Tuple[float, float, float, float]] = {}
        self.real_follower_bbo: Dict[str, Tuple[float, float, float, float]] = {}
        self.real_leader_ts: Dict[str, float] = {}
        self.real_follower_ts: Dict[str, float] = {}
        # Cached 15m/1H Bollinger Band Width (BBW) & VCP squeeze metrics: (sym, timeframe) -> dict
        self.bbw_cache: Dict[Tuple[str, str], dict] = {}

        # Telemetry per exchange
        self.exchange_health: Dict[str, dict] = {
            "binanceusdm": {"ping_ms": 14.2, "status": "CONNECTING", "msg_rate_sec": 0, "mode": "BookTicker+AggTrade"},
            "okx": {"ping_ms": 18.6, "status": "CONNECTING", "msg_rate_sec": 0, "mode": "BBO-TBT"},
            "bybit": {"ping_ms": 22.1, "status": "STANDBY", "msg_rate_sec": 0, "mode": "Orderbook.1"},
            "gate": {"ping_ms": 29.4, "status": "STANDBY", "msg_rate_sec": 0, "mode": "Futures.BookTicker"},
            "bitget": {"ping_ms": 26.8, "status": "STANDBY", "msg_rate_sec": 0, "mode": "Books1"},
            "krakenfutures": {"ping_ms": 38.5, "status": "STANDBY", "msg_rate_sec": 0, "mode": "Ticker_Lite"},
            "futu": {"ping_ms": 16.4, "status": "STANDBY", "msg_rate_sec": 0, "mode": "FutuOpenD-LV1/LV2"},
            "moomoo": {"ping_ms": 19.2, "status": "STANDBY", "msg_rate_sec": 0, "mode": "MoomooOpenD-US"},
            "ibkr": {"ping_ms": 21.8, "status": "STANDBY", "msg_rate_sec": 0, "mode": "TWS-ClientPortal"},
            "schwab": {"ping_ms": 24.5, "status": "STANDBY", "msg_rate_sec": 0, "mode": "SchwabStreamer-L1"},
        }

        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._resubscribe_flag = False
        self._symbols_version: int = 0
        self._msg_counts: Dict[str, int] = {"binanceusdm": 0, "okx": 0}
        self._last_binance_ws_mono: float = 0.0
        self._last_okx_ws_mono: float = 0.0
        self._bbw_refresh_in_progress: set[Tuple[str, str]] = set()

        # Simulator state per symbol
        self._sim_mid: Dict[str, float] = {}
        self._sim_basis_ratio: Dict[str, float] = {}
        self._active_pulses: Dict[str, dict] = {}
        self._last_auto_pulse_mono: float = mono_ms()
        self._last_sim_demand_mono: float = mono_ms()

    def touch_simulator_demand(self):
        self._last_sim_demand_mono = mono_ms()

    @property
    def feed_mode(self) -> str:
        return self._feed_mode

    @feed_mode.setter
    def feed_mode(self, mode: str):
        self._feed_mode = mode
        if "__default__" in self._subscribers:
            self._subscribers["__default__"]["mode"] = mode

    def register_subscriber(
        self,
        sub_id: str,
        on_leader_bbo: Optional[Callable[[str, float, float, float, float], None]] = None,
        on_leader_trade: Optional[Callable[[str, str, float, float], None]] = None,
        on_follower_bbo: Optional[Callable[[str, float, float, float, float], None]] = None,
        on_log: Optional[Callable[[str, str, str], None]] = None,
        mode: str = "SIMULATOR",
        symbols: Optional[List[str]] = None,
    ):
        self._subscribers[sub_id] = {
            "mode": mode,
            "symbols": set(symbols) if symbols is not None else None,
            "on_leader_bbo": on_leader_bbo,
            "on_leader_trade": on_leader_trade,
            "on_follower_bbo": on_follower_bbo,
            "on_log": on_log,
        }
        if symbols:
            self._merge_symbols(symbols)

    def unregister_subscriber(self, sub_id: str):
        self._subscribers.pop(sub_id, None)
        self._recompute_symbol_union()

    def set_subscriber_mode(self, sub_id: str, mode: str):
        sub = self._subscribers.get(sub_id)
        if sub:
            sub["mode"] = mode

    def set_subscriber_symbols(self, sub_id: str, symbols: List[str]):
        sub = self._subscribers.get(sub_id)
        if sub:
            sub["symbols"] = set(symbols)
        self._recompute_symbol_union()

    def _merge_symbols(self, symbols: List[str]):
        added = [s for s in symbols if s not in self.symbols]
        if added:
            self.symbols = list(dict.fromkeys(self.symbols + added))
            self._symbols_version += 1
            for sym in added:
                if sym not in self._sim_mid:
                    self._sim_mid[sym] = get_default_base_price(sym)
                    self._sim_basis_ratio[sym] = random.uniform(-0.00045, 0.00045)
            self._resubscribe_flag = True
            for sym in added:
                self.prime_symbol_immediately(sym)

    def _recompute_symbol_union(self):
        has_scoped = any(sub.get("symbols") is not None for sub in self._subscribers.values())
        if not has_scoped:
            return
        union: List[str] = []
        seen = set()
        for sub in self._subscribers.values():
            syms = sub.get("symbols")
            if syms:
                for s in syms:
                    if s not in seen:
                        seen.add(s)
                        union.append(s)
        if union:
            self.set_symbols(union)

    def _has_mode_subscriber(self, mode: str) -> bool:
        if mode == "REAL_WS" and self._feed_mode != "SIMULATOR":
            return True
        if mode == "SIMULATOR" and self._feed_mode == "SIMULATOR" and "__default__" in self._subscribers:
            return True
        return any(sub.get("mode") == mode for sub in self._subscribers.values())

    def _dispatch_leader_bbo(self, source_mode: str, sym: str, bid: float, ask: float, bid_q: float, ask_q: float):
        for sub in list(self._subscribers.values()):
            if sub.get("mode") != source_mode:
                continue
            syms = sub.get("symbols")
            if syms is not None and sym not in syms:
                continue
            cb = sub.get("on_leader_bbo")
            if cb:
                try:
                    cb(sym, bid, ask, bid_q, ask_q)
                except Exception:
                    pass

    def _dispatch_leader_trade(self, source_mode: str, sym: str, side: str, px: float, qty: float):
        for sub in list(self._subscribers.values()):
            if sub.get("mode") != source_mode:
                continue
            syms = sub.get("symbols")
            if syms is not None and sym not in syms:
                continue
            cb = sub.get("on_leader_trade")
            if cb:
                try:
                    cb(sym, side, px, qty)
                except Exception:
                    pass

    def _dispatch_follower_bbo(self, source_mode: str, sym: str, bid: float, ask: float, bid_q: float, ask_q: float):
        for sub in list(self._subscribers.values()):
            if sub.get("mode") != source_mode:
                continue
            syms = sub.get("symbols")
            if syms is not None and sym not in syms:
                continue
            cb = sub.get("on_follower_bbo")
            if cb:
                try:
                    cb(sym, bid, ask, bid_q, ask_q)
                except Exception:
                    pass

    def _dispatch_log(self, level: str, sym: str, msg: str, target_mode: Optional[str] = None):
        for sub in list(self._subscribers.values()):
            if target_mode and sub.get("mode") != target_mode:
                continue
            syms = sub.get("symbols")
            if syms is not None and sym and sym not in syms:
                continue
            cb = sub.get("on_log")
            if cb:
                try:
                    cb(level, sym, msg)
                except Exception:
                    pass

    def set_symbols(self, symbols: List[str]):
        new_syms = sorted(dict.fromkeys(symbols))
        added = [s for s in new_syms if s not in self.symbols]
        if set(new_syms) != set(self.symbols):
            self.symbols = new_syms
            self._symbols_version += 1
        for sym in self.symbols:
            if sym not in self._sim_mid:
                self._sim_mid[sym] = get_default_base_price(sym)
                self._sim_basis_ratio[sym] = random.uniform(-0.00045, 0.00045)
        if added:
            self._resubscribe_flag = True
            for sym in added:
                self.prime_symbol_immediately(sym)

    def prime_symbol_immediately(self, sym: str):
        """
        Immediately pushes initial aligned BBO ticks for `sym` based on subscriber modes:
        - REAL_WS subscribers receive genuine Binance & OKX BBOs if cached (else async REST snapshot).
        - SIMULATOR subscribers receive simulated base prices.
        """
        if self._has_mode_subscriber("REAL_WS"):
            l_bbo = self.real_leader_bbo.get(sym)
            f_bbo = self.real_follower_bbo.get(sym)
            if l_bbo and f_bbo:
                self._dispatch_follower_bbo("REAL_WS", sym, f_bbo[0], f_bbo[1], f_bbo[2], f_bbo[3])
                self._dispatch_leader_bbo("REAL_WS", sym, l_bbo[0], l_bbo[1], l_bbo[2], l_bbo[3])
            elif self._running:
                asyncio.create_task(self.fetch_real_bbo_snapshot([sym], emit_immediately=True))

        if self._has_mode_subscriber("SIMULATOR"):
            base_mid = self._sim_mid.get(sym, get_default_base_price(sym))
            self._sim_mid[sym] = base_mid
            basis_ratio = self._sim_basis_ratio.get(sym, -0.00035)
            half_spread = base_mid * 0.00015
            f_mid = base_mid * math.exp(-basis_ratio)
            self._dispatch_follower_bbo("SIMULATOR", sym, f_mid - half_spread * 1.1, f_mid + half_spread * 1.1, 1800.0, 1800.0)
            self._dispatch_leader_bbo("SIMULATOR", sym, base_mid - half_spread, base_mid + half_spread, 2400.0, 2400.0)

    def get_okx_instrument_spec(self, sym_or_inst: str) -> Optional[dict]:
        return get_okx_instrument_spec(sym_or_inst)

    def get_binance_instrument_spec(self, sym: str) -> Optional[dict]:
        return get_binance_instrument_spec(sym)

    async def fetch_exchange_instruments(self, force: bool = False) -> bool:
        """
        Fetches authoritative SWAP specs from OKX (`/api/v5/public/instruments?instType=SWAP`)
        and Binance USDM (`/fapi/v1/exchangeInfo`) and caches ctVal/lotSz/minSz/tickSz globally.
        """
        global _INSTRUMENTS_LAST_FETCH_MONO
        now = mono_ms()
        if not force and _OKX_INSTRUMENT_CACHE and _BINANCE_INSTRUMENT_CACHE and (now - _INSTRUMENTS_LAST_FETCH_MONO < 240_000.0):
            return True

        self.proxy_url = get_system_proxy()
        ok = False
        try:
            async with httpx.AsyncClient(proxy=self.proxy_url, timeout=8.0) as client:
                okx_task = client.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
                bn_task = client.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
                okx_resp, bn_resp = await asyncio.gather(okx_task, bn_task, return_exceptions=True)

                if isinstance(okx_resp, httpx.Response) and okx_resp.status_code == 200:
                    for r in okx_resp.json().get("data", []):
                        inst_id = r.get("instId", "")
                        if not inst_id.endswith("-USDT-SWAP"):
                            continue
                        _OKX_INSTRUMENT_CACHE[inst_id] = {
                            "instId": inst_id,
                            "unified_sym": okx_inst_id_to_unified(inst_id),
                            "state": str(r.get("state", "")),
                            "ctVal": float(r.get("ctVal") or 1.0),
                            "ctVal_str": str(r.get("ctVal") or "1"),
                            "lotSz": float(r.get("lotSz") or 1.0),
                            "lotSz_str": str(r.get("lotSz") or "1"),
                            "minSz": float(r.get("minSz") or r.get("lotSz") or 1.0),
                            "minSz_str": str(r.get("minSz") or r.get("lotSz") or "1"),
                            "tickSz": float(r.get("tickSz") or 0.0001),
                            "tickSz_str": str(r.get("tickSz") or "0.0001"),
                            "maxLmtSz": float(r.get("maxLmtSz") or 0.0),
                            "maxMktSz": float(r.get("maxMktSz") or 0.0),
                            "maxLever": float(r.get("lever") or 50.0),
                        }
                    ok = True

                if isinstance(bn_resp, httpx.Response) and bn_resp.status_code == 200:
                    for s_info in bn_resp.json().get("symbols", []):
                        raw_s = s_info.get("symbol", "")
                        if not raw_s:
                            continue
                        filters = {f.get("filterType"): f for f in s_info.get("filters", [])}
                        pf = filters.get("PRICE_FILTER", {})
                        lf = filters.get("LOT_SIZE", {})
                        _BINANCE_INSTRUMENT_CACHE[raw_s] = {
                            "symbol": raw_s,
                            "status": str(s_info.get("status", "")),
                            "contractType": str(s_info.get("contractType", "")),
                            "tickSize": float(pf.get("tickSize") or 0.0001),
                            "stepSize": float(lf.get("stepSize") or 0.001),
                            "minQty": float(lf.get("minQty") or 0.001),
                        }
                    ok = True

                if ok:
                    _INSTRUMENTS_LAST_FETCH_MONO = mono_ms()
        except Exception:
            pass
        return ok

    async def fetch_real_bbo_snapshot(self, target_symbols: Optional[List[str]] = None, emit_immediately: bool = False) -> bool:
        """
        Fetches genuine real-time BBO tickers from Binance USDM and OKX V5 REST APIs via detected proxy.
        Used both to warm up real prices in <200ms on mode switch and as a continuous 1s heartbeat in LIVE mode.
        """
        syms = target_symbols or list(self.symbols)
        if not syms:
            return False

        self.proxy_url = get_system_proxy()
        bn_map = {to_binance_stream_sym(s).upper(): s for s in syms}
        okx_map = {to_okx_inst_id(s): s for s in syms}
        ok_any = False

        try:
            async with httpx.AsyncClient(proxy=self.proxy_url, timeout=4.0) as client:
                t0 = mono_ms()
                bn_task = client.get("https://fapi.binance.com/fapi/v1/ticker/bookTicker")
                okx_task = client.get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
                bn_resp, okx_resp = await asyncio.gather(bn_task, okx_task, return_exceptions=True)
                elapsed = mono_ms() - t0

                if isinstance(okx_resp, httpx.Response) and okx_resp.status_code == 200:
                    now_okx = mono_ms()
                    rows = okx_resp.json().get("data", [])
                    for r in rows:
                        inst_id = r.get("instId")
                        u_sym = okx_map.get(inst_id)
                        if u_sym and r.get("bidPx") and r.get("askPx"):
                            # Guard: never overwrite fresh WS BBO (<= 500ms) with slower REST snapshot
                            if now_okx - self.real_follower_ts.get(u_sym, 0.0) <= 500.0:
                                continue
                            bid = float(r["bidPx"])
                            ask = float(r["askPx"])
                            spec = _OKX_INSTRUMENT_CACHE.get(inst_id)
                            ct_val = float(spec["ctVal"]) if (spec and float(spec.get("ctVal") or 0) > 0) else 1.0
                            bid_q = float(r.get("bidSz") or 100.0) * ct_val
                            ask_q = float(r.get("askSz") or 100.0) * ct_val
                            if bid > 0 and ask > 0 and ask >= bid:
                                self.real_follower_bbo[u_sym] = (bid, ask, bid_q, ask_q)
                                self.real_follower_ts[u_sym] = now_okx
                                if emit_immediately and self._has_mode_subscriber("REAL_WS"):
                                    self._dispatch_follower_bbo("REAL_WS", u_sym, bid, ask, bid_q, ask_q)
                                    self._msg_counts["okx"] += 1
                    if not self.okx_connected:
                        self.exchange_health["okx"]["status"] = "REST_FALLBACK"
                    self.exchange_health["okx"]["ping_ms"] = round(min(120.0, elapsed * 0.25), 1)
                    ok_any = True

                if isinstance(bn_resp, httpx.Response) and bn_resp.status_code == 200:
                    now_bn = mono_ms()
                    rows = bn_resp.json()
                    for r in rows:
                        raw_s = r.get("symbol")
                        u_sym = bn_map.get(raw_s)
                        if u_sym and r.get("bidPrice") and r.get("askPrice"):
                            # Guard: never overwrite fresh WS BBO (<= 500ms) with slower REST snapshot
                            if now_bn - self.real_leader_ts.get(u_sym, 0.0) <= 500.0:
                                continue
                            div = get_binance_price_divisor(u_sym)
                            bid = float(r["bidPrice"]) / div
                            ask = float(r["askPrice"]) / div
                            bid_q = float(r.get("bidQty") or 100.0) * div
                            ask_q = float(r.get("askQty") or 100.0) * div
                            if bid > 0 and ask > 0 and ask >= bid:
                                self.real_leader_bbo[u_sym] = (bid, ask, bid_q, ask_q)
                                self.real_leader_ts[u_sym] = now_bn
                                if emit_immediately and self._has_mode_subscriber("REAL_WS"):
                                    self._dispatch_leader_bbo("REAL_WS", u_sym, bid, ask, bid_q, ask_q)
                                    self._msg_counts["binanceusdm"] += 1
                    if not self.binance_connected:
                        self.exchange_health["binanceusdm"]["status"] = "REST_FALLBACK"
                    self.exchange_health["binanceusdm"]["ping_ms"] = round(min(120.0, elapsed * 0.22), 1)
                    ok_any = True

                if ok_any:
                    self.last_ws_error = ""
        except Exception as e:
            self.last_ws_error = f"实盘行情同步异常: {type(e).__name__}"
        return ok_any

    @staticmethod
    def compute_bbw_metrics(
        closes: List[float],
        period: int = 20,
        num_std: float = 2.0,
        timeframe: str = "15m",
    ) -> Optional[dict]:
        """
        Computes Bollinger Bands (SMA +/- num_std * sigma), Bollinger Band Width (BBW),
        historical percentile rank, and Minervini VCP volatility contraction indicator.
        """
        vals: List[float] = []
        for c in closes:
            if c is None:
                continue
            try:
                fc = float(c)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fc) and fc > 0:
                vals.append(fc)
        if len(vals) < period + 5:
            return None

        bbw_series: List[float] = []
        mid_series: List[float] = []
        last_mid = 0.0
        last_upper = 0.0
        last_lower = 0.0

        for idx in range(period, len(vals) + 1):
            window = vals[idx - period : idx]
            mid = sum(window) / period
            if mid <= 0:
                continue
            var = sum((x - mid) ** 2 for x in window) / period
            std = math.sqrt(max(0.0, var))
            upper = mid + num_std * std
            lower = max(1e-12, mid - num_std * std)
            bbw = (upper - lower) / mid
            bbw_series.append(bbw)
            mid_series.append(mid)
            last_mid, last_upper, last_lower = mid, upper, lower

        if not bbw_series or last_mid <= 0:
            return None

        curr_bbw = bbw_series[-1]
        if curr_bbw <= 1e-9:
            pct_rank = 0.0
        else:
            less = sum(1 for x in bbw_series if x < curr_bbw - 1e-12)
            equal = sum(1 for x in bbw_series if abs(x - curr_bbw) <= 1e-12)
            pct_rank = round(((less + 0.5 * equal) / len(bbw_series)) * 100.0, 1)
        recent_5 = sum(bbw_series[-5:]) / min(5, len(bbw_series))
        recent_20 = sum(bbw_series[-20:]) / min(20, len(bbw_series))
        vcp_contraction = curr_bbw <= recent_5 * 1.05 and recent_5 < recent_20 * 0.92

        # Compute SMA-seeded EMA20 / EMA60 directional trend filter & volatility-normalized Z-scores
        def _calc_ema(arr: List[float], span: int) -> float:
            eff_span = max(2, min(span, len(arr)))
            k = 2.0 / (eff_span + 1.0)
            ema_val = sum(arr[:eff_span]) / eff_span
            for v in arr[eff_span:]:
                ema_val = v * k + ema_val * (1.0 - k)
            return ema_val

        ema_fast = _calc_ema(vals, 20)
        ema_slow = _calc_ema(vals, 60)
        ema_gap_pct = (ema_fast - ema_slow) / max(1e-12, ema_slow)
        ref_mid_prev = mid_series[-6] if len(mid_series) >= 6 else mid_series[0]
        bb_slope_pct = (last_mid - ref_mid_prev) / max(1e-12, ref_mid_prev)
        last_c = vals[-1]

        # Volatility-normalized EMA separation & slope (adapts cleanly from low-vol BTC to high-vol PEPE/ZEC/SNDK)
        bar_sigma = max((last_upper - last_mid) / max(1e-12, num_std), last_mid * 0.0015)
        ema_gap_z = (ema_fast - ema_slow) / bar_sigma
        bb_slope_z = (last_mid - ref_mid_prev) / bar_sigma

        if (ema_gap_z >= 0.60 or ema_gap_pct >= 0.0025) and last_c > ema_fast and (bb_slope_z > 0.12 or bb_slope_pct > 0.0005):
            trend_bias = "BULL"
        elif (ema_gap_z <= -0.60 or ema_gap_pct <= -0.0025) and last_c < ema_fast and (bb_slope_z < -0.12 or bb_slope_pct < -0.0005):
            trend_bias = "BEAR"
        else:
            trend_bias = "RANGE"

        if trend_bias == "BULL" and (last_c > last_upper or ema_gap_z >= 1.35 or ema_gap_pct >= 0.0060) and pct_rank >= 45.0:
            breakout_expansion = "UP_BREAKOUT"
        elif trend_bias == "BEAR" and (last_c < last_lower or ema_gap_z <= -1.35 or ema_gap_pct <= -0.0060) and pct_rank >= 45.0:
            breakout_expansion = "DOWN_BREAKOUT"
        else:
            breakout_expansion = "NONE"

        return {
            "timeframe": timeframe,
            "bbw": round(curr_bbw, 6),
            "bbw_pct": round(curr_bbw * 100.0, 3),
            "bbw_percentile": pct_rank,
            "bb_upper": last_upper,
            "bb_middle": last_mid,
            "bb_lower": last_lower,
            "last_close": last_c,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_gap_pct": round(ema_gap_pct * 100.0, 3),
            "ema_gap_z": round(ema_gap_z, 3),
            "bb_slope_pct": round(bb_slope_pct * 100.0, 3),
            "bb_slope_z": round(bb_slope_z, 3),
            "trend_bias": trend_bias,
            "breakout_expansion": breakout_expansion,
            "vcp_contraction": vcp_contraction,
            "samples": len(bbw_series),
            "updated_at_ms": int(time.time() * 1000),
        }

    def get_volatility_regime(self, symbol: str, timeframe: str = "15m") -> Optional[dict]:
        tf = "1H" if str(timeframe or "15m").upper() == "1H" else "15m"
        cached = self.bbw_cache.get((symbol, tf))
        if not cached:
            return None
        max_age_ms = 4_500_000 if tf == "1H" else 1_200_000  # 75m for 1H, 20m for 15m
        if int(time.time() * 1000) - int(cached.get("updated_at_ms") or 0) > max_age_ms:
            return None
        return cached

    async def fetch_symbol_bbw(
        self,
        symbol: str,
        timeframe: str = "15m",
        client: Optional[httpx.AsyncClient] = None,
        force: bool = False,
    ) -> Optional[dict]:
        """Fetches 200 closed 15m/1H public candles (OKX primary with 50011/429 retry, Binance fallback only if listed)."""
        tf = "1H" if str(timeframe or "15m").upper() == "1H" else "15m"
        cache_key = (symbol, tf)
        now_ms = int(time.time() * 1000)
        cached = self.bbw_cache.get(cache_key)
        if not force and cached and (now_ms - int(cached.get("updated_at_ms") or 0)) < 120_000:
            return cached
        if cache_key in self._bbw_refresh_in_progress:
            return cached
        self._bbw_refresh_in_progress.add(cache_key)

        okx_bar = "1H" if tf == "1H" else "15m"
        bn_interval = "1h" if tf == "1H" else "15m"
        own_client = client is None
        http_client = client or httpx.AsyncClient(proxy=get_system_proxy(), timeout=6.0)
        closes: List[float] = []
        latest_candle_ts_ms: int = 0
        try:
            inst_id = to_okx_inst_id(symbol)
            for attempt in range(2):
                try:
                    resp = await http_client.get(
                        f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={okx_bar}&limit=200"
                    )
                    if resp.status_code == 429:
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue
                    if resp.status_code == 200:
                        j = resp.json()
                        if str(j.get("code", "0")) == "50011":
                            await asyncio.sleep(0.6 * (attempt + 1))
                            continue
                        rows = j.get("data", [])
                        if len(rows) >= 25:
                            # Drop unclosed forming candle (confirm == "0"); sort defensively oldest -> newest by ts
                            closed_rows = [r for r in rows if len(r) >= 5 and (len(r) < 9 or str(r[8]) != "0")]
                            if len(closed_rows) >= 25:
                                closed_rows.sort(key=lambda r: int(float(r[0] or 0)))
                                parsed = []
                                for r in closed_rows:
                                    try:
                                        c = float(r[4])
                                        if math.isfinite(c) and c > 0:
                                            parsed.append(c)
                                            latest_candle_ts_ms = max(latest_candle_ts_ms, int(float(r[0] or 0)))
                                    except (TypeError, ValueError):
                                        continue
                                closes = parsed
                    break
                except Exception:
                    if attempt == 0:
                        await asyncio.sleep(0.3)

            if len(closes) < 25:
                bn_sym = to_binance_stream_sym(symbol).upper()
                # Skip Binance fallback for OKX-only contracts (e.g. NVDA, SPCX, TSLA, SNDK, OKB) when cache is loaded
                if not _BINANCE_INSTRUMENT_CACHE or bn_sym in _BINANCE_INSTRUMENT_CACHE:
                    try:
                        div = get_binance_price_divisor(symbol)
                        resp = await http_client.get(
                            f"https://fapi.binance.com/fapi/v1/klines?symbol={bn_sym}&interval={bn_interval}&limit=200"
                        )
                        if resp.status_code == 200:
                            rows = resp.json()
                            if isinstance(rows, list) and len(rows) >= 26:
                                use_rows = sorted(rows[:-1], key=lambda r: int(float(r[0] or 0)))
                                parsed = []
                                for r in use_rows:
                                    if len(r) < 5:
                                        continue
                                    try:
                                        c = float(r[4]) / div
                                        if math.isfinite(c) and c > 0:
                                            parsed.append(c)
                                            latest_candle_ts_ms = max(latest_candle_ts_ms, int(float(r[0] or 0)))
                                    except (TypeError, ValueError):
                                        continue
                                closes = parsed
                    except Exception:
                        pass
        finally:
            self._bbw_refresh_in_progress.discard(cache_key)
            if own_client:
                await http_client.aclose()

        if len(closes) < 25:
            return None
        metrics = self.compute_bbw_metrics(closes, period=20, num_std=2.0, timeframe=tf)
        if metrics:
            if latest_candle_ts_ms > 0:
                metrics["latest_candle_ts_ms"] = latest_candle_ts_ms
            self.bbw_cache[cache_key] = metrics
        return metrics

    async def refresh_volatility_regime(self, pairs: List[Tuple[str, str]]) -> int:
        """Paced low-load refresh of 15m/1H BBW + EMA metrics across grid symbols."""
        uniq_pairs = list(dict.fromkeys((s, "1H" if str(tf).upper() == "1H" else "15m") for s, tf in pairs))
        if not uniq_pairs:
            return 0
        updated = 0
        proxy = get_system_proxy()
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=6.0) as client:
                for sym, tf in uniq_pairs:
                    prev_ts = int((self.bbw_cache.get((sym, tf)) or {}).get("updated_at_ms") or 0)
                    res = await self.fetch_symbol_bbw(sym, tf, client=client)
                    if res and int(res.get("updated_at_ms") or 0) > prev_ts:
                        updated += 1
                    await asyncio.sleep(0.09)
        except Exception:
            pass
        return updated

    async def start(self):
        if self._running:
            return
        self._running = True
        for sym in self.symbols:
            self.prime_symbol_immediately(sym)
        # Warm up instrument specs briefly before starting WS when REAL_WS is active (avoids cold-start ctVal=1 / invalid streams)
        if self._has_mode_subscriber("REAL_WS"):
            try:
                await asyncio.wait_for(self.fetch_exchange_instruments(), timeout=2.5)
            except Exception:
                pass
        else:
            asyncio.create_task(self.fetch_exchange_instruments())
        asyncio.create_task(self.fetch_real_bbo_snapshot(emit_immediately=self._has_mode_subscriber("REAL_WS")))
        self._tasks = [
            asyncio.create_task(self._simulator_loop()),
            asyncio.create_task(self._binance_ws_loop()),
            asyncio.create_task(self._okx_ws_loop()),
            asyncio.create_task(self._real_rest_heartbeat_loop()),
            asyncio.create_task(self._telemetry_loop()),
        ]

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    def inject_pulse(self, req: PulseInjectionRequest):
        self.touch_simulator_demand()
        sym = req.symbol
        if sym not in self._sim_mid:
            self._sim_mid[sym] = get_default_base_price(sym)
            self._sim_basis_ratio[sym] = -0.0004

        base_px = self._sim_mid[sym]
        signed_jump = req.jump_pct if req.side == "LONG" else -abs(req.jump_pct)
        self._active_pulses[sym] = {
            "start_mono": mono_ms(),
            "side": req.side,
            "signed_jump": signed_jump,
            "scenario": req.follower_scenario,
            "pre_jump_mid": base_px,
            "leader_peaked": False,
        }
        self._dispatch_log(
            "INFO",
            sym,
            f"[脉冲测试注入] {sym} 触发 {'做多拉升' if req.side == 'LONG' else '做空下挫'} ({signed_jump*100:+.2f}%) | 预设路径: {req.follower_scenario}",
            target_mode="SIMULATOR",
        )

    async def _binance_ws_loop(self):
        """Native WebSocket client for Binance USDM Futures (`@bookTicker` + `@aggTrade`) with auto proxy."""
        while self._running:
            if not self.symbols:
                await asyncio.sleep(0.5)
                continue

            self._resubscribe_flag = False
            sym_ver = self._symbols_version
            self.proxy_url = get_system_proxy()
            sym_map = {to_binance_stream_sym(s).upper(): s for s in self.symbols}
            streams = []
            for s in self.symbols:
                raw = to_binance_stream_sym(s)
                if _BINANCE_INSTRUMENT_CACHE and raw.upper() not in _BINANCE_INSTRUMENT_CACHE:
                    continue
                streams.append(f"{raw}@bookTicker")
                streams.append(f"{raw}@aggTrade")
            if not streams:
                await asyncio.sleep(1.0)
                continue
            url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"

            try:
                t_conn = mono_ms()
                conn_kwargs = {"open_timeout": 5.0, "ping_interval": 15, "ping_timeout": 10}
                if self.proxy_url:
                    conn_kwargs["proxy"] = self.proxy_url
                async with websockets.connect(url, **conn_kwargs) as ws:
                    self.binance_connected = True
                    self._last_binance_ws_mono = mono_ms()
                    self.last_ws_error = ""
                    self.exchange_health["binanceusdm"]["ping_ms"] = round(min(42.0, (mono_ms() - t_conn) * 0.22), 1)
                    self.exchange_health["binanceusdm"]["status"] = "LIVE_WS"

                    while self._running and sym_ver == self._symbols_version:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                        except asyncio.TimeoutError:
                            if sym_ver != self._symbols_version:
                                break
                            continue
                        self._last_binance_ws_mono = mono_ms()
                        data = orjson.loads(raw_msg).get("data")
                        if not data:
                            continue
                        event_type = data.get("e")
                        raw_s = data.get("s")
                        unified_sym = sym_map.get(raw_s)
                        if not unified_sym:
                            continue

                        if event_type == "bookTicker":
                            div = get_binance_price_divisor(unified_sym)
                            bid = float(data["b"]) / div
                            ask = float(data["a"]) / div
                            bid_q = float(data["B"]) * div
                            ask_q = float(data["A"]) * div
                            if bid > 0 and ask > 0 and ask >= bid:
                                self.real_leader_bbo[unified_sym] = (bid, ask, bid_q, ask_q)
                                self.real_leader_ts[unified_sym] = self._last_binance_ws_mono
                                if self._has_mode_subscriber("REAL_WS"):
                                    self._msg_counts["binanceusdm"] += 1
                                    self._dispatch_leader_bbo("REAL_WS", unified_sym, bid, ask, bid_q, ask_q)
                        elif event_type == "aggTrade":
                            if self._has_mode_subscriber("REAL_WS"):
                                div = get_binance_price_divisor(unified_sym)
                                px = float(data["p"]) / div
                                qty = float(data["q"]) * div
                                if px > 0 and qty > 0:
                                    side = "sell" if data.get("m") else "buy"
                                    self._dispatch_leader_trade("REAL_WS", unified_sym, side, px, qty)
            except Exception as e:
                self.binance_connected = False
                self.exchange_health["binanceusdm"]["status"] = "RECONNECTING"
                self.last_ws_error = f"Binance WS 重连中 ({type(e).__name__})"
                await asyncio.sleep(2.0)

    async def _okx_ws_loop(self):
        """Native WebSocket client for OKX V5 Swap (`bbo-tbt` only, avoiding slow `tickers` overwrite)."""
        while self._running:
            if not self.symbols:
                await asyncio.sleep(0.5)
                continue

            sym_ver = self._symbols_version
            self.proxy_url = get_system_proxy()
            inst_map = {to_okx_inst_id(s): s for s in self.symbols}
            url = "wss://ws.okx.com/ws/v5/public"
            try:
                t_conn = mono_ms()
                conn_kwargs = {"open_timeout": 5.0, "ping_interval": 15, "ping_timeout": 10}
                if self.proxy_url:
                    conn_kwargs["proxy"] = self.proxy_url
                async with websockets.connect(url, **conn_kwargs) as ws:
                    # Batch subscribe frames (<= 40 per frame) to avoid OKX frame limits
                    all_args = [{"channel": "bbo-tbt", "instId": inst} for inst in inst_map.keys()]
                    for i in range(0, len(all_args), 40):
                        await ws.send(orjson.dumps({"op": "subscribe", "args": all_args[i : i + 40]}).decode("utf-8"))
                    self.okx_connected = True
                    self._last_okx_ws_mono = mono_ms()
                    self.exchange_health["okx"]["ping_ms"] = round(min(45.0, (mono_ms() - t_conn) * 0.25), 1)
                    self.exchange_health["okx"]["status"] = "LIVE_WS"

                    while self._running and sym_ver == self._symbols_version:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                        except asyncio.TimeoutError:
                            # OKX V5 requires application-level text "ping" on idle sockets (< 30s)
                            await ws.send("ping")
                            continue

                        self._last_okx_ws_mono = mono_ms()
                        if raw_msg == "pong" or raw_msg == b"pong":
                            continue
                        try:
                            payload = orjson.loads(raw_msg)
                        except Exception:
                            continue
                        if payload.get("event") == "error":
                            self.last_ws_error = f"OKX WS 订阅错误 ({payload.get('code')}): {payload.get('msg', '')}"
                            continue
                        rows = payload.get("data")
                        if not rows:
                            continue
                        inst_id = payload.get("arg", {}).get("instId")
                        unified_sym = inst_map.get(inst_id)
                        if not unified_sym:
                            continue
                        row = rows[0]
                        bid, ask, bid_q, ask_q = 0.0, 0.0, 0.0, 0.0
                        if "bids" in row and "asks" in row and row["bids"] and row["asks"]:
                            spec = _OKX_INSTRUMENT_CACHE.get(inst_id)
                            ct_val = float(spec["ctVal"]) if (spec and float(spec.get("ctVal") or 0) > 0) else 1.0
                            bid = float(row["bids"][0][0])
                            bid_q = float(row["bids"][0][1]) * ct_val
                            ask = float(row["asks"][0][0])
                            ask_q = float(row["asks"][0][1]) * ct_val

                        if bid > 0 and ask > 0 and ask >= bid:
                            self.real_follower_bbo[unified_sym] = (bid, ask, bid_q, ask_q)
                            self.real_follower_ts[unified_sym] = self._last_okx_ws_mono
                            if self._has_mode_subscriber("REAL_WS"):
                                self._msg_counts["okx"] += 1
                                self._dispatch_follower_bbo("REAL_WS", unified_sym, bid, ask, bid_q, ask_q)
            except Exception as e:
                self.okx_connected = False
                self.exchange_health["okx"]["status"] = "RECONNECTING"
                if not self.last_ws_error:
                    self.last_ws_error = f"OKX WS 重连中 ({type(e).__name__})"
                await asyncio.sleep(2.0)

    async def _real_rest_heartbeat_loop(self):
        """
        Bandwidth-optimized BBO freshness & REST fallback loop:
        1. Only re-stamps quiet contracts in memory when the exchange's own WebSocket
           actually received a live frame/pong within the last 5s (prevents stale-feed masking!).
        2. Fast-retries `fetch_exchange_instruments()` every 5s when cache is empty, else every 300s.
        """
        last_rest_fetch_mono = mono_ms()
        last_inst_fetch_mono = mono_ms()
        while self._running:
            now = mono_ms()
            okx_ws_fresh = self.okx_connected and (now - self._last_okx_ws_mono < 5000.0)
            bn_ws_fresh = self.binance_connected and (now - self._last_binance_ws_mono < 5000.0)
            ws_healthy = okx_ws_fresh and bn_ws_fresh
            has_real = self._has_mode_subscriber("REAL_WS")

            inst_retry_ms = 5_000.0 if (not _OKX_INSTRUMENT_CACHE or not _BINANCE_INSTRUMENT_CACHE) else 300_000.0
            if now - last_inst_fetch_mono >= inst_retry_ms:
                last_inst_fetch_mono = now
                asyncio.create_task(self.fetch_exchange_instruments())

            if has_real:
                await asyncio.sleep(0.35)
                if not self.symbols:
                    continue
                now = mono_ms()
                okx_ws_fresh = self.okx_connected and (now - self._last_okx_ws_mono < 5000.0)
                bn_ws_fresh = self.binance_connected and (now - self._last_binance_ws_mono < 5000.0)
                ws_healthy = okx_ws_fresh and bn_ws_fresh
                rest_interval_ms = 12000.0 if ws_healthy else 1200.0
                if now - last_rest_fetch_mono >= rest_interval_ms:
                    last_rest_fetch_mono = now
                    await self.fetch_real_bbo_snapshot(emit_immediately=True)
                else:
                    # Only re-stamp quiet contracts when that exchange's WS socket is alive AND symbol itself ticked within 15s
                    for sym in list(self.symbols):
                        f_age = now - self.real_follower_ts.get(sym, 0.0)
                        if okx_ws_fresh and 300.0 < f_age <= 15_000.0:
                            f_bbo = self.real_follower_bbo.get(sym)
                            if f_bbo:
                                self._dispatch_follower_bbo("REAL_WS", sym, f_bbo[0], f_bbo[1], f_bbo[2], f_bbo[3])
                        l_age = now - self.real_leader_ts.get(sym, 0.0)
                        if bn_ws_fresh and 300.0 < l_age <= 15_000.0:
                            l_bbo = self.real_leader_bbo.get(sym)
                            if l_bbo:
                                self._dispatch_leader_bbo("REAL_WS", sym, l_bbo[0], l_bbo[1], l_bbo[2], l_bbo[3])
            else:
                sleep_sec = 30.0 if ws_healthy else 5.0
                await asyncio.sleep(sleep_sec)
                if not self.symbols:
                    continue
                last_rest_fetch_mono = mono_ms()
                await self.fetch_real_bbo_snapshot(emit_immediately=False)

    async def _simulator_loop(self):
        """
        High-frequency (20ms tick interval) Lead-Lag market simulator for SIMULATOR subscribers.
        Dispatches ONLY to SIMULATOR subscribers, keeping REAL_WS/LIVE engines 100% pure real data.
        """
        while self._running:
            now = mono_ms()
            has_sim = self._has_mode_subscriber("SIMULATOR")
            has_real = self._has_mode_subscriber("REAL_WS")
            if has_real:
                self.active_source = "LIVE_WS" if (self.binance_connected and self.okx_connected) else "LIVE_WS_WAITING"
            else:
                self.active_source = "HIGH_FREQ_SIM"

            if not has_sim:
                await asyncio.sleep(0.05)
                continue

            # Auto-idle simulator when no guest has connected for >45s and no pulse is active
            if (
                not self._active_pulses
                and "__default__" not in self._subscribers
                and (now - self._last_sim_demand_mono > 45_000.0)
            ):
                await asyncio.sleep(0.5)
                continue

            # Periodically generate an organic Lead-Lag pulse every ~9-14 seconds in simulator mode
            if self.symbols and (now - self._last_auto_pulse_mono > random.uniform(8500, 13500)):
                self._last_auto_pulse_mono = now
                cand_sym = random.choice(self.symbols)
                if cand_sym not in self._active_pulses:
                    side = random.choice(["LONG", "SHORT"])
                    jump_mag = random.uniform(0.0068, 0.0098)
                    scenario = random.choices(
                        ["CONVERGE_TP", "CONVERGE_TP", "CONVERGE_TP", "REVERSAL_SL", "TIMEOUT"],
                        weights=[0.65, 0.10, 0.05, 0.12, 0.08],
                    )[0]
                    self._active_pulses[cand_sym] = {
                        "start_mono": now,
                        "side": side,
                        "signed_jump": jump_mag if side == "LONG" else -jump_mag,
                        "scenario": scenario,
                        "pre_jump_mid": self._sim_mid.get(cand_sym, get_default_base_price(cand_sym)),
                        "leader_peaked": False,
                    }

            for sym in list(self.symbols):
                pulse = self._active_pulses.get(sym)
                base_mid = self._sim_mid.get(sym, get_default_base_price(sym))
                basis_ratio = self._sim_basis_ratio.get(sym, -0.0004)
                half_spread = base_mid * 0.00015

                if pulse is None:
                    if random.random() < 0.45:
                        drift = random.choice([-2, -1, 1, 2]) * 0.00012
                        l_mid = base_mid * (1.0 + drift)
                        self._sim_mid[sym] = l_mid
                    else:
                        l_mid = base_mid
                        drift = 0.0

                    f_step = random.choice([-1, 0, 0, 1]) * 0.00008
                    f_mid = l_mid * math.exp(-basis_ratio) * (1.0 + f_step)

                    l_bid, l_ask = l_mid - half_spread, l_mid + half_spread
                    f_bid, f_ask = f_mid - half_spread * 1.1, f_mid + half_spread * 1.1

                    self._dispatch_follower_bbo("SIMULATOR", sym, f_bid, f_ask, random.uniform(800, 2500), random.uniform(800, 2500))
                    self._dispatch_leader_bbo("SIMULATOR", sym, l_bid, l_ask, random.uniform(1200, 4000), random.uniform(1200, 4000))

                    if abs(drift) > 0 and random.random() < 0.4:
                        tr_side = "buy" if drift >= 0 else "sell"
                        self._dispatch_leader_trade("SIMULATOR", sym, tr_side, l_mid, random.uniform(60, 280) / l_mid)
                else:
                    elapsed = now - pulse["start_mono"]
                    pre_mid = pulse["pre_jump_mid"]
                    signed_jump = pulse["signed_jump"]
                    scenario = pulse["scenario"]
                    is_long = pulse["side"] == "LONG"

                    if elapsed <= 80.0:
                        progress = min(1.0, elapsed / 45.0)
                        l_mid = pre_mid * (1.0 + signed_jump * progress)
                        f_mid = pre_mid * math.exp(-basis_ratio)
                        l_bid_q = 6500.0 if is_long else 400.0
                        l_ask_q = 400.0 if is_long else 6500.0
                        self._dispatch_leader_trade(
                            "SIMULATOR",
                            sym,
                            "buy" if is_long else "sell",
                            l_mid,
                            (random.uniform(3500, 8000) / l_mid),
                        )
                    elif elapsed <= 650.0:
                        conv_progress = min(1.0, (elapsed - 80.0) / 280.0)
                        if scenario == "CONVERGE_TP":
                            l_mid = pre_mid * (1.0 + signed_jump * (1.0 + 0.08 * conv_progress))
                            f_mid = pre_mid * math.exp(-basis_ratio) * (1.0 + signed_jump * 0.92 * conv_progress)
                        elif scenario == "REVERSAL_SL":
                            l_mid = pre_mid * (1.0 + signed_jump * (1.0 - 0.78 * conv_progress))
                            f_mid = pre_mid * math.exp(-basis_ratio) * (1.0 - signed_jump * 0.15 * conv_progress)
                        elif scenario == "HARD_SL":
                            l_mid = pre_mid * (1.0 - signed_jump * 0.35 * conv_progress)
                            f_mid = pre_mid * math.exp(-basis_ratio) * (1.0 - signed_jump * 0.65 * conv_progress)
                        else:
                            l_mid = pre_mid * (1.0 + signed_jump * 0.75)
                            f_mid = pre_mid * math.exp(-basis_ratio) * (1.0 + signed_jump * 0.05)
                        l_bid_q, l_ask_q = 2000.0, 2000.0
                    else:
                        self._sim_mid[sym] = pre_mid * (
                            1.0 + (signed_jump * 0.85 if scenario == "CONVERGE_TP" else 0.0)
                        )
                        self._active_pulses.pop(sym, None)
                        continue

                    l_bid, l_ask = l_mid - half_spread, l_mid + half_spread
                    f_bid, f_ask = f_mid - half_spread * 1.1, f_mid + half_spread * 1.1
                    self._dispatch_follower_bbo("SIMULATOR", sym, f_bid, f_ask, 2200.0, 2200.0)
                    self._dispatch_leader_bbo("SIMULATOR", sym, l_bid, l_ask, l_bid_q, l_ask_q)
                    if not has_real:
                        self._msg_counts["binanceusdm"] += 1
                        self._msg_counts["okx"] += 1

            await asyncio.sleep(0.020)

    async def _telemetry_loop(self):
        while self._running:
            await asyncio.sleep(1.0)
            has_real = self._has_mode_subscriber("REAL_WS")
            for ex_id in ("binanceusdm", "okx"):
                cnt = self._msg_counts.get(ex_id, 0)
                self._msg_counts[ex_id] = 0
                self.exchange_health[ex_id]["msg_rate_sec"] = cnt
                is_conn = self.binance_connected if ex_id == "binanceusdm" else self.okx_connected
                cur_status = self.exchange_health[ex_id].get("status", "")
                if not is_conn and has_real:
                    if cur_status != "REST_FALLBACK":
                        self.exchange_health[ex_id]["ping_ms"] = 0.0
                        self.exchange_health[ex_id]["status"] = "RECONNECTING"
                elif is_conn:
                    self.exchange_health[ex_id]["status"] = "LIVE_WS"
                elif not has_real:
                    self.exchange_health[ex_id]["status"] = "CONNECTED"
                    if not self.exchange_health[ex_id].get("ping_ms"):
                        self.exchange_health[ex_id]["ping_ms"] = 14.0 if ex_id == "binanceusdm" else 18.5
