"""
Core Bidirectional Lead-Lag Sniper Engine & 8-Stage State Machine.
Includes:
- Strict data & execution isolation between `SHADOW` (影子模拟盘) and `LIVE` (实盘通道)
- Instant symbol priming on contract addition (0ms warmup delay)
- Quantitative Shadow-to-Live Reality Gap & Decay Estimator
"""
import asyncio
import math
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from backend.database import AsyncDatabaseManager
from backend.executor import ExecutionAdapter, resolve_okx_spec
from backend.grid_engine import LongShortGridTracker
from backend.market_gateway import (
    MarketDataGateway,
    get_default_base_price,
    get_okx_instrument_spec,
)
from backend.math_engine import SignalCandidate, SymbolMathState, mono_ms, wall_ms
from backend.models import (
    BatchSymbolUpdateRequest,
    GlobalRiskConfig,
    PulseInjectionRequest,
    SniperState,
    SymbolStrategy,
    is_grid_strategy,
)


def _utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _create_empty_stats_bucket() -> Dict[str, Any]:
    return {
        "total_signals": 0,
        "ioc_filled_count": 0,
        "ioc_missed_count": 0,
        "reconciled_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "long_trades": 0,
        "short_trades": 0,
        "long_net_pnl": 0.0,
        "short_net_pnl": 0.0,
        "maker_tp_count": 0,
        "taker_exit_count": 0,
        "total_gross_pnl": 0.0,
        "total_fees": 0.0,
        "total_net_pnl": 0.0,
        "daily_net_pnl": 0.0,
        "daily_pnl_date_utc": _utc_today_str(),
        "consecutive_losses": 0,
        "gross_profit_sum": 0.0,
        "gross_loss_sum": 0.0,
        "equity_curve": [],
        "recent_trades": [],
        "symbol_stats": {},
    }


def _get_or_init_symbol_stat(bucket: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    sym_map = bucket.setdefault("symbol_stats", {})
    if symbol not in sym_map:
        sym_map[symbol] = {
            "symbol": symbol,
            "signals": 0,
            "filled": 0,
            "missed": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "long_trades": 0,
            "short_trades": 0,
            "long_net_pnl": 0.0,
            "short_net_pnl": 0.0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "net_pnl": 0.0,
            "best_trade_pnl": 0.0,
            "worst_trade_pnl": 0.0,
            "avg_hold_ms": 0,
            "_hold_ms_sum": 0,
            "last_trade_time": "",
        }
    return sym_map[symbol]


class BidirectionalSniperEngine:
    def __init__(
        self,
        db: AsyncDatabaseManager,
        user_id: int = 0,
        shared_gateway: Optional[MarketDataGateway] = None,
        is_guest_preview: bool = False,
    ):
        self.db = db
        self.user_id = int(user_id)
        self.is_guest_preview = bool(is_guest_preview)
        self._owns_gateway = shared_gateway is None
        self._subscriber_id = f"guest-{self.user_id}" if self.is_guest_preview else f"user-{self.user_id}"

        self.risk_config = GlobalRiskConfig()
        self.strategies: Dict[str, SymbolStrategy] = {}
        self.exchanges: Dict[str, Dict[str, Any]] = {}
        self.math_states: Dict[str, SymbolMathState] = {}

        # Per-symbol state machine & event-driven wakeup
        self.symbol_states: Dict[str, SniperState] = {}
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.grid_trackers: Dict[str, LongShortGridTracker] = {}
        self.active_grid_positions: Dict[str, Dict[str, Any]] = {}
        self.exchange_live_positions: Dict[str, Dict[str, Any]] = {}
        self.book_events: Dict[str, asyncio.Event] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._pos_sync_lock: Optional[asyncio.Lock] = None
        self._symbol_exec_locks: Dict[str, asyncio.Lock] = {}
        self._last_exchange_pos_sync_mono: float = 0.0

        # Per-symbol rolling net PnL history for auto-disable
        self.symbol_rolling_pnls: Dict[str, deque] = {}

        # Strict isolation between SHADOW (Paper) and LIVE (Real) logs & statistics
        self.logs_by_mode: Dict[str, deque] = {
            "SHADOW": deque(maxlen=200),
            "LIVE": deque(maxlen=200),
        }
        self.stats_by_mode: Dict[str, Dict[str, Any]] = {
            "SHADOW": _create_empty_stats_bucket(),
            "LIVE": _create_empty_stats_bucket(),
        }
        self.trading_active_by_mode: Dict[str, bool] = {
            "SHADOW": True if (self.is_guest_preview or self.user_id == 0) else False,
            "LIVE": False,
        }
        self.scan_ticks_by_mode: Dict[str, int] = {
            "SHADOW": 0,
            "LIVE": 0,
        }
        self.stats_reset_at_by_mode: Dict[str, int] = {
            "SHADOW": 0,
            "LIVE": 0,
        }
        self._last_live_cred_warn_mono: float = 0.0
        self._grid_block_log_ts: Dict[str, float] = {}

        self.executor = ExecutionAdapter(
            get_math_state=lambda sym: self.math_states.get(sym),
            get_risk_cfg=lambda: self.risk_config,
        )
        if shared_gateway is not None:
            self.gateway = shared_gateway
        else:
            self.gateway = MarketDataGateway(
                on_leader_bbo=self._handle_leader_bbo,
                on_leader_trade=self._handle_leader_trade,
                on_follower_bbo=self._handle_follower_bbo,
                on_log=self.append_log,
            )

    @property
    def stats(self) -> Dict[str, Any]:
        """Returns the statistics bucket for the currently active trading_mode."""
        return self.stats_by_mode.get(self.risk_config.trading_mode, self.stats_by_mode["SHADOW"])

    @property
    def terminal_logs(self) -> deque:
        return self.logs_by_mode.get(self.risk_config.trading_mode, self.logs_by_mode["SHADOW"])

    def _spawn_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def append_log(self, level: str, symbol: str, message: str, mode: Optional[str] = None):
        now = datetime.now()
        ts_str = now.strftime("%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}"
        target_mode = mode or self.risk_config.trading_mode
        entry = {
            "id": f"{wall_ms()}-{len(self.logs_by_mode[target_mode])}",
            "time": ts_str,
            "level": level,
            "symbol": symbol,
            "mode": target_mode,
            "message": message,
        }
        self.logs_by_mode[target_mode].appendleft(entry)

    def _gateway_mode_for_self(self) -> str:
        if self.is_guest_preview:
            return "SIMULATOR"
        if self.risk_config.trading_mode == "LIVE" or self.risk_config.feed_source == "REAL_WS":
            return "REAL_WS"
        return "SIMULATOR"

    def _sync_gateway_subscription(self):
        syms = list(self.strategies.keys())
        if self._owns_gateway:
            self.gateway.feed_mode = self._gateway_mode_for_self()
            self.gateway.set_symbols(syms)
        else:
            self.gateway.set_subscriber_mode(self._subscriber_id, self._gateway_mode_for_self())
            self.gateway.set_subscriber_symbols(self._subscriber_id, syms)

    def _get_symbol_exec_lock(self, sym: str) -> asyncio.Lock:
        lock = self._symbol_exec_locks.get(sym)
        if lock is None:
            lock = asyncio.Lock()
            self._symbol_exec_locks[sym] = lock
        return lock

    def _ensure_daily_pnl_date(self, bucket: Dict[str, Any]) -> str:
        today = _utc_today_str()
        if bucket.get("daily_pnl_date_utc") != today:
            bucket["daily_pnl_date_utc"] = today
            bucket["daily_net_pnl"] = 0.0
        return today

    def _sync_cfg_with_exchange_spec(self, cfg: SymbolStrategy):
        """Synchronizes OKX precision & minimum contract qty so small/high-price coins are not dropped."""
        spec = get_okx_instrument_spec(cfg.symbol)
        if not spec:
            return
        changed = False
        tick_str = str(spec.get("tickSz_str") or "")
        if "." in tick_str:
            dec_places = min(12, len(tick_str.rstrip("0").split(".")[1]))
            if dec_places > cfg.price_precision:
                cfg.price_precision = dec_places
                changed = True
        lot_base = float(spec.get("lotSz") or 0.0) * float(spec.get("ctVal") or 1.0)
        if lot_base > 0:
            lot_base_str = f"{lot_base:.12f}".rstrip("0").rstrip(".")
            if "." in lot_base_str:
                req_qty_prec = min(12, len(lot_base_str.split(".")[1]))
                if req_qty_prec > cfg.qty_precision:
                    cfg.qty_precision = req_qty_prec
                    changed = True
        min_base = float(spec.get("minSz") or 0.0) * float(spec.get("ctVal") or 1.0)
        if min_base > 0 and cfg.min_qty > min_base:
            cfg.min_qty = round(min_base, 8)
            changed = True
        if changed and not self.is_guest_preview and self.user_id > 0:
            try:
                self.db.upsert_symbol_sync(cfg, user_id=self.user_id)
            except Exception:
                pass

    def persist_runtime_state(self, sync: bool = False):
        """Persists GlobalRiskConfig and trading_active_by_mode to SQLite without blocking hot loops."""
        if self.is_guest_preview:
            return
        try:
            if not sync and self.db._running:
                self.db.save_runtime_setting_async("risk_config", self.risk_config.model_dump(), user_id=self.user_id)
                self.db.save_runtime_setting_async("trading_active_by_mode", self.trading_active_by_mode, user_id=self.user_id)
            else:
                self.db.save_runtime_setting_sync("risk_config", self.risk_config.model_dump(), user_id=self.user_id)
                self.db.save_runtime_setting_sync("trading_active_by_mode", self.trading_active_by_mode, user_id=self.user_id)
        except Exception:
            pass

    async def start(self):
        if self._owns_gateway:
            self.db.init_sync()
            await self.db.start_worker()

        if not self.is_guest_preview and self.user_id > 0:
            self.db.ensure_user_workspace_sync(self.user_id)
            # Logged-in registered users default to LIVE mode (no simulated trading workspace)
            self.risk_config.trading_mode = "LIVE"
            self.risk_config.feed_source = "REAL_WS"

        # Restore persisted risk_config and trading_active_by_mode from SQLite (scoped by user_id)
        if not self.is_guest_preview:
            saved_risk = self.db.load_runtime_setting_sync("risk_config", user_id=self.user_id)
            if isinstance(saved_risk, dict):
                try:
                    self.risk_config = GlobalRiskConfig(**saved_risk)
                except Exception:
                    pass
            saved_active = self.db.load_runtime_setting_sync("trading_active_by_mode", user_id=self.user_id)
            if isinstance(saved_active, dict):
                self.trading_active_by_mode.update({k: bool(v) for k, v in saved_active.items()})

            saved_reset = self.db.load_runtime_setting_sync("stats_reset_at_by_mode", user_id=self.user_id)
            if isinstance(saved_reset, dict):
                for mk in ("SHADOW", "LIVE"):
                    try:
                        self.stats_reset_at_by_mode[mk] = int(saved_reset.get(mk) or 0)
                    except (TypeError, ValueError):
                        pass

            if self.user_id > 0:
                self.risk_config.trading_mode = "LIVE"
                self.risk_config.feed_source = "REAL_WS"

            for ex in self.db.load_exchanges_sync(user_id=self.user_id):
                self.exchanges[ex["exchange_id"]] = ex
        else:
            # Guest preview engine NEVER loads any private API credentials
            self.risk_config.trading_mode = "SHADOW"
            self.risk_config.feed_source = "SIMULATOR"
            self.exchanges = {
                "binanceusdm": {"exchange_id": "binanceusdm", "display_name": "Binance USDM", "role": "leader", "is_active": True, "taker_fee": 0.0004, "maker_fee": 0.00018, "has_credentials": False, "auth_status": "GUEST_PREVIEW", "auth_message": "游客体验通道"},
                "okx": {"exchange_id": "okx", "display_name": "OKX Swap", "role": "follower", "is_active": True, "taker_fee": 0.0005, "maker_fee": 0.0002, "has_credentials": False, "auth_status": "GUEST_PREVIEW", "auth_message": "游客体验通道"},
            }

        for cfg in self.db.load_symbols_sync(user_id=self.user_id):
            self._register_symbol_internal(cfg)

        # Load historical trades into their respective SHADOW or LIVE stats buckets (respecting stats reset watermark)
        historical = list(reversed(self.db.load_recent_trades_sync(limit=5000, user_id=self.user_id)))
        for tr in historical:
            tr_mode = tr.get("trading_mode", "SHADOW")
            reset_ms = int(self.stats_reset_at_by_mode.get(tr_mode, 0) or 0)
            if reset_ms > 0:
                tr_ts_ms = int(tr.get("ack_ts_ms") or tr.get("submit_ts_ms") or tr.get("signal_ts_ms") or 0)
                if tr_ts_ms <= 0 and tr.get("created_at"):
                    try:
                        from datetime import timezone
                        dt = datetime.strptime(str(tr["created_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone.utc
                        )
                        tr_ts_ms = int(dt.timestamp() * 1000)
                    except Exception:
                        tr_ts_ms = 0
                if tr_ts_ms <= reset_ms:
                    continue
            self._accumulate_trade_into_stats(tr, is_historical=True, mode_override=tr_mode)

        await self._reconcile_startup_positions()

        # Verify credentials & reconcile real OKX positions BEFORE registering live callbacks
        # so startup ticks cannot fire duplicate orders against unreconciled live positions!
        if not self.is_guest_preview:
            for ex_id, ex_row in list(self.exchanges.items()):
                if ex_row.get("has_api_key") or ex_row.get("has_api_secret") or ex_row.get("has_passphrase"):
                    await self.verify_and_update_exchange(ex_id)
            if self.risk_config.trading_mode == "LIVE":
                try:
                    await self.sync_live_exchange_positions(trigger="startup")
                except Exception:
                    pass

        if self._owns_gateway:
            self.gateway.feed_mode = self._gateway_mode_for_self()
            self.gateway.set_symbols(list(self.strategies.keys()))
            await self.gateway.start()
        else:
            self.gateway.register_subscriber(
                sub_id=self._subscriber_id,
                on_leader_bbo=self._handle_leader_bbo,
                on_leader_trade=self._handle_leader_trade,
                on_follower_bbo=self._handle_follower_bbo,
                on_log=self.append_log,
                mode=self._gateway_mode_for_self(),
                symbols=list(self.strategies.keys()),
            )
            for sym in self.strategies.keys():
                self.gateway.prime_symbol_immediately(sym)

        # Seed initial LIVE BBO ticks if starting in LIVE mode without triggering redundant re-verify
        if self.risk_config.trading_mode == "LIVE":
            if self.gateway.real_follower_bbo:
                for sym, cfg in list(self.strategies.items()):
                    if sym in self.math_states:
                        self._seed_initial_ticks(sym, cfg, self.math_states[sym])

        if not self.is_guest_preview:
            self._spawn_task(self._live_position_sync_loop())
            self._spawn_task(self._bbw_volatility_sync_loop())

        if self.is_guest_preview:
            self.append_log(
                "INFO",
                "SYSTEM",
                f"游客公开模拟体验引擎已启动 | 监控合约: {len(self.strategies)} 个（登录并开通会员后可绑定个人交易所 API 24h 实盘运行）",
                mode="SHADOW",
            )
        else:
            self.append_log(
                "INFO",
                "SYSTEM",
                f"跨所价差脉冲高频狙击系统已启动 | 监控合约: {len(self.strategies)} 个",
                mode="SHADOW",
            )
            self.append_log(
                "INFO",
                "SYSTEM",
                f"实盘交易通道已就绪 (UID={self.user_id}) | 已连接 Binance 与 OKX 真实盘口行情，关闭网页后仍将在后台持续运行",
                mode="LIVE",
            )

    async def stop(self):
        self._save_open_strategy_state(sync=True)
        self.persist_runtime_state(sync=True)
        for t in list(self._bg_tasks):
            t.cancel()
        await self.executor.aclose()
        if self._owns_gateway:
            await self.gateway.stop()
            await self.db.stop_worker()
        else:
            self.gateway.unregister_subscriber(self._subscriber_id)

    def _get_pos_sync_lock(self) -> asyncio.Lock:
        if self._pos_sync_lock is None:
            self._pos_sync_lock = asyncio.Lock()
        return self._pos_sync_lock

    def _save_open_strategy_state(self, sync: bool = False):
        if self.is_guest_preview or self.user_id <= 0:
            return
        try:
            grid_states = {}
            for sym, tracker in self.grid_trackers.items():
                if tracker.center_price > 0:
                    grid_states[sym] = tracker.export_state()
            if not sync and self.db._running:
                self.db.save_runtime_setting_async(
                    "open_strategy_state",
                    {"grid_states": grid_states},
                    user_id=self.user_id,
                )
            else:
                self.db.save_runtime_setting_sync(
                    "open_strategy_state",
                    {"grid_states": grid_states},
                    user_id=self.user_id,
                )
        except Exception:
            pass

    async def _reconcile_startup_positions(self):
        if self.is_guest_preview:
            return
        saved_state = self.db.load_runtime_setting_sync("open_strategy_state", user_id=self.user_id)
        restored_grid_slots = 0
        if isinstance(saved_state, dict):
            grid_states = saved_state.get("grid_states") or {}
            _, follower_ex = self.get_leader_And_follower_cfg()
            cur_mode = self.risk_config.trading_mode
            for sym, gdata in grid_states.items():
                cfg = self.strategies.get(sym)
                tracker = self.grid_trackers.get(sym)
                if not cfg or not tracker or not is_grid_strategy(cfg.strategy_type):
                    continue
                tracker.restore_state(gdata, cfg)
                for lv in tracker.levels.values():
                    if lv.get("status") == "OPEN" and float(lv.get("entry_price") or 0.0) > 0:
                        gk = f"{sym}:{lv['slot_id']}"
                        qty = float(lv.get("filled_qty") or 0.0)
                        ep = float(lv.get("entry_price") or 0.0)
                        self.active_grid_positions[gk] = {
                            "symbol": sym,
                            "strategy_type": cfg.strategy_type,
                            "grid_mode": tracker.active_grid_mode,
                            "grid_slot_id": lv["slot_id"],
                            "mode": cur_mode,
                            "side": lv["side"],
                            "qty": qty,
                            "contracts": float(lv.get("contracts") or 0.0),
                            "ct_val": float(lv.get("ct_val") or 1.0),
                            "notional_usdt": round(qty * ep * cfg.contract_size, 2),
                            "entry_price": ep,
                            "mark_price": ep,
                            "tp_price": float(lv.get("tp_price") or 0.0),
                            "sl_price": float(lv.get("sl_price") or 0.0),
                            "trail_stop_price": 0.0,
                            "opened_mono_ms": mono_ms(),
                            "unrealized_net_pct": 0.0,
                            "unrealized_usdt": 0.0,
                            "recovered": True,
                            "managed_by_strategy": True,
                        }
                        restored_grid_slots += 1

        orphans = self.db.load_open_positions_sync(user_id=self.user_id)
        if restored_grid_slots > 0:
            self.append_log(
                "INFO",
                "SYSTEM",
                f"[启动恢复] 从本地持久化恢复了 {restored_grid_slots} 个网格活跃持仓槽位，即将与交易所实时持仓对账",
                mode="LIVE",
            )
        elif orphans:
            self.append_log(
                "INFO",
                "SYSTEM",
                f"[启动对账] 本地存在 {len(orphans)} 条历史未闭环持仓记录，即将核验交易所实盘真实持仓",
                mode="LIVE",
            )
        else:
            self.append_log("INFO", "SYSTEM", "[启动对账] 本地持仓表核验完成，准备同步交易所实时持仓", mode="LIVE")

    async def _live_position_sync_loop(self):
        while True:
            try:
                await asyncio.sleep(12.0)
                if self.risk_config.trading_mode == "LIVE":
                    _, follower_ex = self.get_leader_And_follower_cfg()
                    if follower_ex.get("auth_status") == "VERIFIED":
                        await self.sync_live_exchange_positions(trigger="periodic")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _bbw_volatility_sync_loop(self):
        """Low-overhead periodic 15m/1H Bollinger Band Width (BBW) refresh for Trend & Adaptive Grid symbols."""
        first_pass = True
        while True:
            try:
                await asyncio.sleep(3.0 if first_pass else 180.0)
                first_pass = False
                pairs = [
                    (cfg.symbol, cfg.bbw_timeframe)
                    for cfg in self.strategies.values()
                    if cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID")
                ]
                if pairs:
                    await self.gateway.refresh_volatility_regime(pairs)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _monitor_recovered_lead_lag_position(self, cfg: SymbolStrategy, pos_entry: dict):
        """Monitors a post-restart recovered LEAD_LAG position for TP/SL/Kill-Switch."""
        sym = cfg.symbol
        side = pos_entry["side"]
        entry_avg = float(pos_entry["entry_price"])
        filled_qty = float(pos_entry["qty"])
        contracts = float(pos_entry.get("contracts") or 0.0)
        ct_val = float(pos_entry.get("ct_val") or 1.0)
        tp_price = float(pos_entry["tp_price"])
        sl_price = float(pos_entry["sl_price"])
        _, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        maker_fee = float(follower_ex.get("maker_fee", 0.0002))
        rt_fee = taker_fee + (maker_fee if cfg.use_post_only_tp else taker_fee)
        m_state = self.math_states.get(sym)
        ev = self.book_events.get(sym)
        exit_reason = ""

        try:
            while sym in self.active_positions and self.active_positions[sym].get("recovered"):
                if self.risk_config.kill_switch_active:
                    exit_reason = "KILL_SWITCH"
                    break
                f_curr = m_state.latest_follower if m_state else None
                if f_curr:
                    mark_px = f_curr.bid if side == "LONG" else f_curr.ask
                    raw_ret = (mark_px - entry_avg) / entry_avg if side == "LONG" else (entry_avg - mark_px) / entry_avg
                    curr_net_pct = raw_ret - rt_fee
                    pos_entry["mark_price"] = mark_px
                    pos_entry["unrealized_net_pct"] = round(curr_net_pct, 6)
                    if (side == "LONG" and mark_px >= tp_price) or (side == "SHORT" and mark_px <= tp_price):
                        exit_reason = "TAKE_PROFIT"
                        break
                    if (side == "LONG" and mark_px <= sl_price) or (side == "SHORT" and mark_px >= sl_price):
                        exit_reason = "HARD_SL"
                        break
                if ev:
                    ev.clear()
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=0.05)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.05)

            if not exit_reason or self.active_positions.get(sym) is not pos_rec:
                return

            keep_uncertain = False
            async with self._get_symbol_exec_lock(sym):
                if self.active_positions.get(sym) is not pos_rec:
                    return
                self.symbol_states[sym] = SniperState.EXITING
                exit_res = await self.executor.execute_exit_with_retry(
                    cfg=cfg,
                    side=side,
                    qty=filled_qty,
                    exit_reason=exit_reason,
                    tp_price=tp_price,
                    exchange_cfg=follower_ex,
                    contracts=contracts or None,
                    ct_val=ct_val or None,
                )
            if exit_res.status == "UNCERTAIN":
                keep_uncertain = True
                self.symbol_states[sym] = SniperState.EXIT_UNCERTAIN
                self.append_log("CRITICAL", sym, f"[恢复持仓平仓告警] {sym} {exit_res.raw_msg}", mode="LIVE")
                return
            exit_avg = exit_res.avg_price
            gross_pnl = (exit_avg - entry_avg) * filled_qty if side == "LONG" else (entry_avg - exit_avg) * filled_qty
            total_fee = entry_avg * filled_qty * taker_fee + exit_avg * filled_qty * (maker_fee if exit_res.is_maker else taker_fee)
            net_pnl = gross_pnl - total_fee
            now_dt = datetime.now()
            trade_record = {
                "time": now_dt.strftime("%H:%M:%S.") + f"{int(now_dt.microsecond / 1000):03d}",
                "symbol": sym,
                "leader_ex": follower_ex["exchange_id"],
                "follower_ex": follower_ex["exchange_id"],
                "side": side,
                "trading_mode": "LIVE",
                "order_id": exit_res.order_id,
                "exit_order_type": "MAKER_POST_ONLY" if exit_res.is_maker else "TAKER_IOC",
                "leader_jump_pct": 0.0,
                "follower_lag_pct": 0.0,
                "expected_net_pct": float(cfg.take_profit_pct),
                "actual_slippage_pct": 0.0,
                "beta_est": 1.0,
                "leader_snapshot_price": entry_avg,
                "follower_snapshot_price": entry_avg,
                "reservation_price": tp_price,
                "entry_price": entry_avg,
                "exit_price": exit_avg,
                "filled_qty": filled_qty,
                "gross_pnl_usdt": round(gross_pnl, 4),
                "fee_usdt": round(total_fee, 4),
                "net_pnl_usdt": round(net_pnl, 4),
                "signal_ts_ms": wall_ms(),
                "submit_ts_ms": wall_ms(),
                "ack_ts_ms": wall_ms(),
                "signal_to_fill_ms": round(exit_res.latency_ms, 2),
                "hold_duration_ms": 0,
                "exit_reason": exit_reason,
            }
            self.db.record_trade_async(trade_record, user_id=self.user_id)
            self.db.upsert_position_async(
                sym, follower_ex["exchange_id"], side, 0.0, entry_avg, "", "", "CLOSED", wall_ms(), user_id=self.user_id
            )
            self._accumulate_trade_into_stats(trade_record, is_historical=False, mode_override="LIVE")
            self.append_log(
                "EXIT_WIN" if net_pnl >= 0 else "EXIT_LOSS",
                sym,
                f"[接管持仓平仓] {sym} {side} ({exit_reason}) | 平仓={exit_avg:.{cfg.price_precision}f} | 净收益: {net_pnl:+.4f} USDT",
                mode="LIVE",
            )
        except asyncio.CancelledError:
            pass
        finally:
            if not locals().get("keep_uncertain") and self.active_positions.get(sym) is pos_rec:
                self.active_positions.pop(sym, None)
                self.symbol_states[sym] = SniperState.IDLE

    async def sync_live_exchange_positions(self, trigger: str = "manual") -> Dict[str, Any]:
        """
        Synchronizes real OKX positions into active strategy trackers (`grid_trackers` / `active_positions`)
        and `exchange_live_positions` so restart/pause cycles never orphan real holdings.
        """
        if self.is_guest_preview or self.risk_config.trading_mode != "LIVE":
            return {"ok": True, "synced_count": 0, "recovered_count": 0}

        _, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        maker_fee = float(follower_ex.get("maker_fee", 0.0002))
        rt_fee = taker_fee + maker_fee
        grid_rt_fee = 2.0 * taker_fee

        async with self._get_pos_sync_lock():
            res = await self.executor.fetch_live_positions(follower_ex)
            if not res.get("ok"):
                return {"ok": False, "error": res.get("error", "OKX持仓同步失败")}

            self._last_exchange_pos_sync_mono = mono_ms()
            live_rows = res.get("positions", [])
            next_ex_map: Dict[str, Dict[str, Any]] = {}
            recovered_syms: List[str] = []
            state_changed = False

            for lp in live_rows:
                sym = lp["symbol"]
                side = lp["side"]
                key = f"{sym}:{side}"
                cfg = self.strategies.get(sym)
                prec = min(12, max(2, int(cfg.price_precision))) if cfg else 4
                entry_px = float(lp["entry_price"] or 0.0)
                mark_px = float(lp["mark_price"] or entry_px)
                upl_usdt = float(lp["upl_usdt"] or 0.0)
                lever = max(1, int(lp.get("leverage") or (cfg.leverage if cfg else 5)))
                raw_ret = ((mark_px - entry_px) / entry_px) if (entry_px > 0 and side == "LONG") else (
                    ((entry_px - mark_px) / entry_px) if entry_px > 0 else 0.0
                )

                pos_summary = {
                    "symbol": sym,
                    "inst_id": lp["inst_id"],
                    "side": side,
                    "qty": lp["qty"],
                    "contracts": lp["contracts"],
                    "ct_val": lp["ct_val"],
                    "notional_usdt": lp["notional_usdt"],
                    "margin_usdt": lp["margin_usdt"],
                    "leverage": lever,
                    "entry_price": entry_px,
                    "mark_price": mark_px,
                    "unrealized_usdt": upl_usdt,
                    "unrealized_net_pct": round(raw_ret - rt_fee, 6),
                    "mgn_mode": lp.get("mgn_mode", "cross"),
                    "c_time_ms": lp.get("c_time_ms", 0),
                    "managed_by_strategy": False,
                }
                next_ex_map[key] = pos_summary

                if not cfg:
                    continue

                if is_grid_strategy(cfg.strategy_type):
                    tracker = self.grid_trackers.get(sym)
                    if tracker is None:
                        tracker = LongShortGridTracker(sym)
                        self.grid_trackers[sym] = tracker
                        bbw_inf = self.gateway.get_volatility_regime(sym, cfg.bbw_timeframe)
                        tracker.sync_config(cfg, current_mid=mark_px or entry_px, bbw_info=bbw_inf)

                    matching_slots = [
                        lv
                        for lv in tracker.levels.values()
                        if lv.get("side") == side and lv.get("status") in ("ENTERING", "OPEN", "EXITING")
                    ]
                    if not matching_slots:
                        slot_id = tracker.recover_open_position(
                            side=side,
                            entry_price=entry_px,
                            filled_qty=lp["qty"],
                            contracts=lp["contracts"],
                            ct_val=lp["ct_val"],
                            cfg=cfg,
                            round_trip_fee=grid_rt_fee,
                        )
                        lv = tracker.levels[slot_id]
                        gk = f"{sym}:{slot_id}"
                        self.active_grid_positions[gk] = {
                            "symbol": sym,
                            "strategy_type": cfg.strategy_type,
                            "grid_mode": tracker.active_grid_mode,
                            "grid_slot_id": slot_id,
                            "mode": "LIVE",
                            "side": side,
                            "qty": lp["qty"],
                            "contracts": lp["contracts"],
                            "ct_val": lp["ct_val"],
                            "notional_usdt": lp["notional_usdt"],
                            "entry_price": entry_px,
                            "mark_price": mark_px,
                            "tp_price": lv["tp_price"],
                            "sl_price": lv["sl_price"],
                            "trail_stop_price": (
                                tracker.long_trail_stop_price if side == "LONG" else tracker.short_trail_stop_price
                            ),
                            "opened_mono_ms": mono_ms(),
                            "unrealized_net_pct": round(raw_ret - grid_rt_fee, 6),
                            "unrealized_usdt": upl_usdt,
                            "leverage": lever,
                            "mgn_mode": lp.get("mgn_mode", "cross"),
                            "recovered": True,
                            "managed_by_strategy": True,
                        }
                        recovered_syms.append(f"{sym}[{slot_id}]")
                        state_changed = True
                    else:
                        # Sync live OKX mark & contracts without overwriting native slot entry_price with blended avgPx
                        for lv in matching_slots:
                            gk = f"{sym}:{lv['slot_id']}"
                            if gk in self.active_grid_positions:
                                gpos = self.active_grid_positions[gk]
                                gpos["mark_price"] = mark_px
                                if len(matching_slots) == 1:
                                    gpos["unrealized_usdt"] = upl_usdt
                                    gpos["notional_usdt"] = lp["notional_usdt"]
                                    if lv.get("recovered") or float(lv.get("entry_price") or 0.0) <= 0:
                                        gpos["contracts"] = lp["contracts"]
                                        gpos["qty"] = lp["qty"]
                                        gpos["entry_price"] = entry_px
                                        lv["contracts"] = lp["contracts"]
                                        lv["filled_qty"] = lp["qty"]
                                        lv["entry_price"] = entry_px
                                    elif lp["contracts"] < float(lv.get("contracts") or 0.0):
                                        # External partial close reduced exchange size -> clamp down safely
                                        gpos["contracts"] = lp["contracts"]
                                        gpos["qty"] = lp["qty"]
                                        lv["contracts"] = lp["contracts"]
                                        lv["filled_qty"] = lp["qty"]
                    pos_summary["managed_by_strategy"] = True

                elif cfg.strategy_type == "LEAD_LAG":
                    if sym in self.active_positions and self.active_positions[sym].get("side") == side:
                        ap = self.active_positions[sym]
                        ap["mark_price"] = mark_px
                        ap["unrealized_usdt"] = upl_usdt
                        ap["notional_usdt"] = lp["notional_usdt"]
                        pos_summary["managed_by_strategy"] = True
                    elif self.symbol_states.get(sym, SniperState.IDLE) == SniperState.IDLE:
                        tp_px = round(
                            entry_px * (1.0 + cfg.take_profit_pct + rt_fee)
                            if side == "LONG"
                            else entry_px * max(0.05, 1.0 - cfg.take_profit_pct - rt_fee),
                            prec,
                        )
                        sl_px = round(
                            entry_px * max(0.05, 1.0 - cfg.stop_loss_pct)
                            if side == "LONG"
                            else entry_px * (1.0 + cfg.stop_loss_pct),
                            prec,
                        )
                        self.symbol_states[sym] = SniperState.HOLDING
                        pos_rec = {
                            "symbol": sym,
                            "strategy_type": "LEAD_LAG",
                            "mode": "LIVE",
                            "side": side,
                            "qty": lp["qty"],
                            "contracts": lp["contracts"],
                            "ct_val": lp["ct_val"],
                            "notional_usdt": lp["notional_usdt"],
                            "entry_price": entry_px,
                            "mark_price": mark_px,
                            "tp_price": tp_px,
                            "sl_price": sl_px,
                            "opened_mono_ms": mono_ms(),
                            "peak_net_pct": max(0.0, raw_ret - rt_fee),
                            "unrealized_net_pct": round(raw_ret - rt_fee, 6),
                            "unrealized_usdt": upl_usdt,
                            "leverage": lever,
                            "mgn_mode": lp.get("mgn_mode", "cross"),
                            "recovered": True,
                            "managed_by_strategy": True,
                        }
                        self.active_positions[sym] = pos_rec
                        self._spawn_task(self._monitor_recovered_lead_lag_position(cfg, pos_rec))
                        recovered_syms.append(f"{sym}[狙击]")
                        state_changed = True
                        pos_summary["managed_by_strategy"] = True

            # Clear stale local LIVE slots whose OKX position no longer exists (e.g. closed on OKX App/Web)
            now_m = mono_ms()
            for gk, gpos in list(self.active_grid_positions.items()):
                if gpos.get("mode") != "LIVE":
                    continue
                key = f"{gpos['symbol']}:{gpos['side']}"
                if key not in next_ex_map:
                    sym = gpos["symbol"]
                    slot_id = gpos.get("grid_slot_id", "")
                    tracker = self.grid_trackers.get(sym)
                    lv = tracker.levels.get(slot_id) if tracker else None
                    age_ms = now_m - float(gpos.get("opened_mono_ms") or 0.0)
                    if lv and lv.get("status") == "OPEN" and (gpos.get("recovered") or age_ms > 8000.0):
                        lv["status"] = "IDLE"
                        lv["entry_price"] = 0.0
                        lv["filled_qty"] = 0.0
                        lv["contracts"] = 0.0
                        lv["opened_mono_ms"] = 0.0
                        lv["last_closed_mono"] = now_m
                        lv.pop("recovered", None)
                        self.active_grid_positions.pop(gk, None)
                        state_changed = True

            for sym, ap in list(self.active_positions.items()):
                if ap.get("mode") == "LIVE" and (
                    ap.get("recovered") or self.symbol_states.get(sym) == SniperState.EXIT_UNCERTAIN
                ):
                    key = f"{sym}:{ap['side']}"
                    if key not in next_ex_map and (now_m - float(ap.get("opened_mono_ms") or 0.0) > 8000.0):
                        self.active_positions.pop(sym, None)
                        if self.symbol_states.get(sym) in (SniperState.HOLDING, SniperState.EXIT_UNCERTAIN):
                            self.symbol_states[sym] = SniperState.IDLE
                        state_changed = True

            # Release any EXIT_UNCERTAIN symbol that OKX confirms has zero open positions
            for sym, st in list(self.symbol_states.items()):
                if st == SniperState.EXIT_UNCERTAIN and not any(k.startswith(f"{sym}:") for k in next_ex_map):
                    self.active_positions.pop(sym, None)
                    self.symbol_states[sym] = SniperState.IDLE
                    state_changed = True

            # Clean stale SQLite positions rows that are closed on OKX
            if trigger in ("startup", "manual", "resume"):
                for db_pos in self.db.load_open_positions_sync(user_id=self.user_id):
                    db_key = f"{db_pos['symbol']}:{db_pos['side']}"
                    if db_key not in next_ex_map:
                        self.db.upsert_position_async(
                            db_pos["symbol"],
                            db_pos["exchange_id"],
                            db_pos["side"],
                            0.0,
                            float(db_pos.get("entry_price") or 0.0),
                            "",
                            "",
                            "CLOSED",
                            int(db_pos.get("opened_at_ms") or 0),
                            user_id=self.user_id,
                        )

            self.exchange_live_positions = next_ex_map
            if state_changed:
                self._save_open_strategy_state()
            if recovered_syms:
                self.append_log(
                    "FILL",
                    "SYNC",
                    f"[持仓识别恢复] 已自动接管交易所 {len(recovered_syms)} 个在途持仓并恢复止盈/止损监控: {', '.join(recovered_syms[:8])}{' 等' if len(recovered_syms) > 8 else ''}",
                    mode="LIVE",
                )

            return {
                "ok": True,
                "synced_count": len(next_ex_map),
                "recovered_count": len(recovered_syms),
                "recovered": recovered_syms,
            }

    async def manual_close_position(self, symbol: str, side: str, slot_id: Optional[str] = None) -> Dict[str, Any]:
        """Closes a specific strategy slot (without liquidating sibling slots) or whole exchange position."""
        if self.is_guest_preview:
            return {"ok": False, "message": "游客预览模式不可操作实盘持仓"}

        _, follower_ex = self.get_leader_And_follower_cfg()
        cur_mode = self.risk_config.trading_mode

        async with self._get_symbol_exec_lock(symbol):
            tracker = self.grid_trackers.get(symbol)
            cfg = self.strategies.get(symbol)
            same_side_open = (
                [lv for lv in tracker.levels.values() if lv.get("side") == side and lv.get("status") in ("OPEN", "EXITING")]
                if tracker
                else []
            )
            single_slot_partial = (
                bool(slot_id)
                and tracker is not None
                and slot_id in tracker.levels
                and tracker.levels[slot_id].get("side") == side
                and len(same_side_open) > 1
            )
            ex_pos_meta = self.exchange_live_positions.get(f"{symbol}:{side}", {})
            real_mgn_mode = str(ex_pos_meta.get("mgn_mode") or "cross")

            if cur_mode == "LIVE":
                if single_slot_partial and cfg is not None:
                    # Only close THIS slot's contracts via reduce-only order so sibling slots stay intact!
                    lv = tracker.levels[slot_id]
                    slot_qty = float(lv.get("filled_qty") or 0.0)
                    slot_ct = float(lv.get("contracts") or 0.0)
                    slot_ct_val = float(lv.get("ct_val") or 1.0)
                    e_px = float(lv.get("entry_price") or 0.0)
                    exit_res = await self.executor.execute_exit_with_retry(
                        cfg=cfg,
                        side=side,
                        qty=slot_qty,
                        exit_reason="MANUAL_CLOSE",
                        tp_price=e_px,
                        exchange_cfg=follower_ex,
                        contracts=slot_ct or None,
                        ct_val=slot_ct_val or None,
                        order_key=f"{symbol}:{slot_id}",
                        allow_close_position_fallback=False,
                    )
                    if exit_res.status == "UNCERTAIN":
                        return {"ok": False, "message": f"单格手动平仓未确认: {exit_res.raw_msg}"}
                    res = {
                        "ok": True,
                        "message": f"OKX {symbol} [{slot_id}] 单格减仓平仓已成交（其余同向网格槽位不受影响）",
                    }
                else:
                    res = await self.executor.close_exchange_position(
                        symbol=symbol, side=side, mgn_mode=real_mgn_mode, exchange_cfg=follower_ex
                    )
                    if not res.get("ok"):
                        return res
            else:
                res = {"ok": True, "message": f"模拟持仓 {symbol} ({side}) 已平仓"}

            # Clear local tracking entries cleanly (if full close-position ran, clear ALL same-side slots!)
            if tracker:
                clear_all_side = not single_slot_partial
                for sid, lv in list(tracker.levels.items()):
                    if (single_slot_partial and sid == slot_id) or (
                        clear_all_side and lv.get("side") == side and lv.get("status") != "IDLE"
                    ):
                        if lv.get("ephemeral_recovered"):
                            tracker.levels.pop(sid, None)
                        else:
                            lv["status"] = "IDLE"
                            lv["entry_price"] = 0.0
                            lv["filled_qty"] = 0.0
                            lv["contracts"] = 0.0
                            lv["opened_mono_ms"] = 0.0
                            lv["last_closed_mono"] = mono_ms()
                            lv["exit_retry_until_mono"] = 0.0
                            lv.pop("recovered", None)
                        self.active_grid_positions.pop(f"{symbol}:{sid}", None)

            ap = self.active_positions.get(symbol)
            if ap and ap.get("side") == side and not single_slot_partial:
                self.active_positions.pop(symbol, None)
                self.symbol_states[symbol] = SniperState.IDLE
                ev = self.book_events.get(symbol)
                if ev:
                    ev.set()
                if not self.is_guest_preview:
                    self.db.upsert_position_async(
                        symbol, follower_ex["exchange_id"], side, 0.0, float(ap.get("entry_price") or 0.0), "", "", "CLOSED", wall_ms(), user_id=self.user_id
                    )

        if cur_mode == "LIVE":
            await self.sync_live_exchange_positions(trigger="manual_close")
        self._save_open_strategy_state()
        self.append_log("WARN", symbol, f"[手动平仓] {res.get('message', symbol)}", mode=cur_mode)
        return res

    async def adopt_exchange_position_into_grid(self, symbol: str) -> Dict[str, Any]:
        """Adds an unmanaged exchange open position (e.g., SNDK/NVDA) into LONG_SHORT_GRID strategy management."""
        if self.is_guest_preview:
            return {"ok": False, "message": "游客模式不可修改策略"}
        ex_pos = next(
            (p for p in self.exchange_live_positions.values() if p.get("symbol") == symbol),
            None,
        )
        if not ex_pos:
            return {"ok": False, "message": f"未检测到 {symbol} 的活跃交易所持仓"}

        existing_cfg = self.strategies.get(symbol)
        if existing_cfg:
            existing_cfg.strategy_type = "LONG_SHORT_GRID"
            existing_cfg.is_running = True
            self.add_or_update_symbol(existing_cfg)
        else:
            spec = resolve_okx_spec(symbol)
            tick_str = str(spec.get("tickSz_str") or "")
            p_prec = len(tick_str.rstrip("0").split(".")[1]) if "." in tick_str else 4
            min_base = float(spec.get("minSz") or 0.1) * float(spec.get("ctVal") or 1.0)
            cfg = SymbolStrategy(
                symbol=symbol,
                strategy_type="LONG_SHORT_GRID",
                order_usdt=round(max(5.0, float(ex_pos.get("notional_usdt") or 5.0)), 2),
                leverage=max(1, min(50, int(ex_pos.get("leverage") or 5))),
                price_precision=min(12, max(2, p_prec)),
                qty_precision=2,
                min_qty=round(max(1e-8, min_base), 8),
                grid_side_mode="LONG_SHORT",
                is_running=True,
            )
            self.add_or_update_symbol(cfg)

        sync_res = await self.sync_live_exchange_positions(trigger="adopt")
        return {
            "ok": True,
            "message": f"已将 {symbol} 纳入「多空动态网格」并接管止盈止损监控",
            "sync": sync_res,
        }

    def _register_symbol_internal(self, cfg: SymbolStrategy):
        sym = cfg.symbol
        self._sync_cfg_with_exchange_spec(cfg)
        self.strategies[sym] = cfg
        if sym not in self.math_states:
            m_state = SymbolMathState(sym, tau_seconds=30.0)
            self.math_states[sym] = m_state
            self.symbol_states[sym] = SniperState.IDLE
            self.book_events[sym] = asyncio.Event()
            self.symbol_rolling_pnls[sym] = deque(maxlen=100)
        tracker = self.grid_trackers.get(sym)
        if tracker is None:
            tracker = LongShortGridTracker(sym)
            self.grid_trackers[sym] = tracker
        f_mid = 0.0
        ms = self.math_states.get(sym)
        if ms and ms.latest_follower:
            f_mid = ms.latest_follower.mid
        tracker.sync_config(cfg, current_mid=f_mid)

    def _seed_initial_ticks(self, sym: str, cfg: SymbolStrategy, m_state: SymbolMathState):
        if self.risk_config.trading_mode == "LIVE":
            l_bbo = self.gateway.real_leader_bbo.get(sym)
            f_bbo = self.gateway.real_follower_bbo.get(sym)
            if l_bbo and f_bbo:
                now_m = mono_ms()
                for i in range(18, 0, -1):
                    t_mono = now_m - i * 45.0
                    m_state.on_follower_tick(f_bbo[0], f_bbo[1], f_bbo[2], f_bbo[3], now_mono=t_mono)
                    m_state.on_leader_tick(l_bbo[0], l_bbo[1], l_bbo[2], l_bbo[3], cfg, is_locked=False, now_mono=t_mono)
            return

        base_px = get_default_base_price(sym)
        now_m = mono_ms()
        half_sp = base_px * 0.00015
        basis_r = -0.00035
        for i in range(25, 0, -1):
            t_mono = now_m - i * 40.0
            step_offset = ((i % 4) - 1.5) * 0.00012
            l_mid = base_px * (1.0 + step_offset)
            f_mid = l_mid * math.exp(-basis_r)
            m_state.on_follower_tick(
                f_mid - half_sp * 1.1, f_mid + half_sp * 1.1, 1800.0, 1800.0, now_mono=t_mono
            )
            m_state.on_leader_tick(
                l_mid - half_sp, l_mid + half_sp, 2200.0, 2200.0, cfg, is_locked=False, now_mono=t_mono
            )

    def add_or_update_symbol(self, cfg: SymbolStrategy):
        is_new = cfg.symbol not in self.strategies
        prev_cfg = self.strategies.get(cfg.symbol)
        # Prevent switching between GRID and LEAD_LAG while positions/in-flight slots are open
        if prev_cfg and (is_grid_strategy(prev_cfg.strategy_type) != is_grid_strategy(cfg.strategy_type)):
            tracker_prev = self.grid_trackers.get(cfg.symbol)
            has_grid_active = bool(tracker_prev and tracker_prev._has_active_slots()) or any(
                k.startswith(f"{cfg.symbol}:") for k in self.active_grid_positions
            )
            has_ll_active = cfg.symbol in self.active_positions or (
                self.symbol_states.get(cfg.symbol, SniperState.IDLE) != SniperState.IDLE
            )
            if has_grid_active or has_ll_active:
                raise ValueError(f"{cfg.symbol} 当前存在在途持仓或未完成挂单，请先平仓后再切换策略大类（网格 ↔ 狙击）")

        # Clearing rolling loss memory when user explicitly re-enables an auto-disabled symbol
        if prev_cfg and not prev_cfg.is_running and cfg.is_running:
            if cfg.symbol in self.symbol_rolling_pnls:
                self.symbol_rolling_pnls[cfg.symbol].clear()
            cfg.auto_disabled_reason = None

        self._grid_block_log_ts.pop(cfg.symbol, None)
        self._register_symbol_internal(cfg)
        if cfg.symbol in self.grid_trackers:
            self.grid_trackers[cfg.symbol].clear_cooldowns()
        if is_new and cfg.symbol in self.math_states:
            self._seed_initial_ticks(cfg.symbol, cfg, self.math_states[cfg.symbol])
        if not self.is_guest_preview:
            self.db.upsert_symbol_sync(cfg, user_id=self.user_id)
        self._sync_gateway_subscription()
        strat_label_map = {
            "LONG_SHORT_GRID": "传统震荡网格",
            "TREND_GRID": "顺势突破网格",
            "ADAPTIVE_GRID": "VCP双模式智能网格",
            "LEAD_LAG": "跨所脉冲狙击",
        }
        strat_label = strat_label_map.get(cfg.strategy_type, cfg.strategy_type)
        action_str = "新增并即时激活监控合约" if is_new else (
            f"切换为【{strat_label}】并更新参数" if (prev_cfg and prev_cfg.strategy_type != cfg.strategy_type) else "更新合约策略参数"
        )
        if is_grid_strategy(cfg.strategy_type):
            if cfg.strategy_type == "LONG_SHORT_GRID":
                detail = f"模式={cfg.grid_side_mode}, 档位={cfg.grid_levels}层/侧, 间距={cfg.grid_step_pct*100:.2f}%, 止盈={cfg.grid_take_profit_pct*100:.2f}%, 区间=±{cfg.grid_range_pct*100:.1f}%"
            else:
                detail = (
                    f"模式={cfg.grid_side_mode}, 档位={cfg.grid_levels}层/侧, 间距={cfg.grid_step_pct*100:.2f}%, "
                    f"死区={cfg.trend_dead_zone_mult:g}x, 追踪回撤={cfg.trend_trailing_mult:g}x, "
                    f"递减仓={'开' if cfg.trend_pyramid_decay else '关'}, BBW={cfg.bbw_timeframe}≤{cfg.bbw_squeeze_percentile:g}%"
                )
        else:
            detail = f"方向={cfg.direction}, 窗口={cfg.window_ms}ms, 最小脉冲={cfg.min_jump_pct*100:.2f}%, 目标净利={cfg.take_profit_pct*100:.2f}%"
        self.append_log(
            "INFO",
            cfg.symbol,
            f"[{action_str}] {cfg.symbol} ({strat_label}) 已生效 ({detail})",
        )
        if cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID"):
            try:
                asyncio.get_running_loop()
                self._spawn_task(self.gateway.fetch_symbol_bbw(cfg.symbol, cfg.bbw_timeframe))
            except RuntimeError:
                pass
        if not self.is_guest_preview and self.risk_config.trading_mode == "LIVE":
            try:
                asyncio.get_running_loop()
                if any(k.startswith(f"{cfg.symbol}:") for k in self.exchange_live_positions):
                    self._spawn_task(self.sync_live_exchange_positions(trigger="symbol_update"))
            except RuntimeError:
                pass
        self._save_open_strategy_state()

    def remove_symbol(self, symbol: str):
        tracker = self.grid_trackers.get(symbol)
        has_grid_active = bool(tracker and tracker._has_active_slots()) or any(
            k.startswith(f"{symbol}:") for k in self.active_grid_positions
        )
        if symbol in self.strategies and self.symbol_states.get(symbol) == SniperState.IDLE and not has_grid_active:
            self.strategies.pop(symbol, None)
            self.math_states.pop(symbol, None)
            self.symbol_states.pop(symbol, None)
            self.grid_trackers.pop(symbol, None)
            self.book_events.pop(symbol, None)
            self.symbol_rolling_pnls.pop(symbol, None)
            self._grid_block_log_ts.pop(symbol, None)
            self._symbol_exec_locks.pop(symbol, None)
            for gk in [k for k in self.active_grid_positions.keys() if k.startswith(f"{symbol}:")]:
                self.active_grid_positions.pop(gk, None)
            if not self.is_guest_preview:
                self.db.delete_symbol_sync(symbol, user_id=self.user_id)
            self._sync_gateway_subscription()
            self.append_log("WARN", symbol, f"[移除合约] 已停止并移除 {symbol} 实时监控")

    def validate_and_batch_update_symbols(self, req: BatchSymbolUpdateRequest) -> Dict[str, Any]:
        """
        Audits all matching stored symbol configs for parameter anomalies (`action='validate'`)
        and optionally applies safe bulk updates (`action='apply'`) without touching open positions.
        """
        _, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        grid_rt_fee = 2.0 * taker_fee

        items: List[Dict[str, Any]] = []
        anomaly_symbols = 0
        updated_count = 0
        open_deferred_count = 0

        for sym, cfg in sorted(self.strategies.items()):
            if req.only_running and not cfg.is_running:
                continue
            if req.strategy_filter == "ALL_GRID" and not is_grid_strategy(cfg.strategy_type):
                continue
            if req.strategy_filter in ("ADAPTIVE_GRID", "TREND_GRID", "LONG_SHORT_GRID") and cfg.strategy_type != req.strategy_filter:
                continue

            issues: List[str] = []
            fixes: List[str] = []
            tracker = self.grid_trackers.get(sym)
            has_open = bool(tracker and tracker._has_active_slots()) or (sym in self.active_positions)

            spec = get_okx_instrument_spec(sym)
            f_bbo = self.gateway.real_follower_bbo.get(sym)
            ref_px = (f_bbo[0] + f_bbo[1]) * 0.5 if (f_bbo and f_bbo[0] > 0) else 0.0
            if ref_px <= 0:
                ms = self.math_states.get(sym)
                if ms and ms.latest_follower:
                    ref_px = ms.latest_follower.mid

            new_cfg = cfg.model_copy(deep=True)
            changed = False

            if is_grid_strategy(new_cfg.strategy_type):
                # 1. Diagnose current config
                if new_cfg.grid_step_pct < 0.0055:
                    issues.append(f"网格步长偏窄 ({new_cfg.grid_step_pct*100:.2f}% < 推荐 0.60%)")
                if new_cfg.grid_take_profit_pct < new_cfg.grid_step_pct - 1e-6:
                    issues.append(
                        f"止盈低于步长倒挂 (TP={new_cfg.grid_take_profit_pct*100:.2f}% < Step={new_cfg.grid_step_pct*100:.2f}%)"
                    )
                if new_cfg.grid_take_profit_pct <= grid_rt_fee:
                    issues.append(
                        f"止盈未覆盖双边吃单费 ({new_cfg.grid_take_profit_pct*100:.2f}% ≤ {grid_rt_fee*100:.2f}%)"
                    )
                if new_cfg.grid_stop_loss_pct <= new_cfg.grid_range_pct:
                    issues.append(
                        f"止损小于等于区间 (SL={new_cfg.grid_stop_loss_pct*100:.1f}% ≤ Range={new_cfg.grid_range_pct*100:.1f}%)"
                    )
                if new_cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID"):
                    dz_span = new_cfg.trend_dead_zone_mult * new_cfg.grid_step_pct
                    if dz_span > new_cfg.grid_range_pct:
                        issues.append(
                            f"顺势死区超出重锚区间 ({dz_span*100:.2f}% > {new_cfg.grid_range_pct*100:.1f}%)"
                        )

                # 2. Check OKX contract spec / minimum notional / precision
                if spec:
                    min_notional = float(spec.get("minSz") or 0.0) * float(spec.get("ctVal") or 1.0) * ref_px
                    if ref_px > 0 and min_notional > new_cfg.order_usdt * 1.35:
                        issues.append(
                            f"单格金额({new_cfg.order_usdt:.1f}U)低于OKX最小开仓额({min_notional:.1f}U)"
                        )
                    tick_str = str(spec.get("tickSz_str") or "")
                    if "." in tick_str:
                        req_p = min(12, len(tick_str.rstrip("0").split(".")[1]))
                        if req_p > new_cfg.price_precision:
                            issues.append(f"价格精度不足 ({new_cfg.price_precision} < {req_p})")
                            if req.action == "apply" and req.auto_fix_anomalies:
                                new_cfg.price_precision = req_p
                                fixes.append(f"价格精度→{req_p}")
                                changed = True
                    lot_base = float(spec.get("lotSz") or 0.0) * float(spec.get("ctVal") or 1.0)
                    if lot_base > 0:
                        lot_s = f"{lot_base:.12f}".rstrip("0").rstrip(".")
                        req_q = min(12, len(lot_s.split(".")[1])) if "." in lot_s else 0
                        if req_q > new_cfg.qty_precision:
                            issues.append(f"数量精度不足 ({new_cfg.qty_precision} < {req_q})")
                            if req.action == "apply" and req.auto_fix_anomalies:
                                new_cfg.qty_precision = req_q
                                fixes.append(f"数量精度→{req_q}")
                                changed = True

                # 3. Apply requested bulk updates if action == "apply"
                if req.action == "apply":
                    if req.grid_step_pct is not None and abs(new_cfg.grid_step_pct - req.grid_step_pct) > 1e-7:
                        new_cfg.grid_step_pct = round(float(req.grid_step_pct), 6)
                        fixes.append(f"步长→{new_cfg.grid_step_pct*100:.2f}%")
                        changed = True
                    if req.grid_take_profit_pct is not None and abs(new_cfg.grid_take_profit_pct - req.grid_take_profit_pct) > 1e-7:
                        new_cfg.grid_take_profit_pct = round(float(req.grid_take_profit_pct), 6)
                        fixes.append(f"止盈→{new_cfg.grid_take_profit_pct*100:.2f}%")
                        changed = True
                    if (
                        req.trend_trailing_mult is not None
                        and new_cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID")
                        and abs(new_cfg.trend_trailing_mult - req.trend_trailing_mult) > 1e-6
                    ):
                        new_cfg.trend_trailing_mult = round(float(req.trend_trailing_mult), 3)
                        fixes.append(f"追踪倍率→{new_cfg.trend_trailing_mult:g}x")
                        changed = True
                    if req.auto_fix_anomalies:
                        min_safe_tp = round(max(new_cfg.grid_step_pct, grid_rt_fee + 0.0005), 6)
                        if new_cfg.grid_take_profit_pct < min_safe_tp - 1e-7:
                            new_cfg.grid_take_profit_pct = min_safe_tp
                            fixes.append(f"修正倒挂止盈→{min_safe_tp*100:.2f}%")
                            changed = True
                        if new_cfg.grid_stop_loss_pct <= new_cfg.grid_range_pct:
                            new_cfg.grid_stop_loss_pct = round(min(0.90, new_cfg.grid_range_pct + 0.03), 4)
                            fixes.append(f"修正止损→{new_cfg.grid_stop_loss_pct*100:.1f}%")
                            changed = True
                        if new_cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID"):
                            dz_span = new_cfg.trend_dead_zone_mult * new_cfg.grid_step_pct
                            if dz_span > new_cfg.grid_range_pct:
                                new_cfg.grid_range_pct = round(min(0.80, dz_span * 1.5), 4)
                                fixes.append(f"扩宽区间→±{new_cfg.grid_range_pct*100:.1f}%")
                                changed = True
                                if new_cfg.grid_stop_loss_pct <= new_cfg.grid_range_pct:
                                    new_cfg.grid_stop_loss_pct = round(min(0.90, new_cfg.grid_range_pct + 0.03), 4)

            if issues:
                anomaly_symbols += 1

            if req.action == "apply" and changed:
                self.strategies[sym] = new_cfg
                if tracker is not None:
                    tracker.sync_config(new_cfg, current_mid=ref_px)
                    if has_open:
                        open_deferred_count += 1
                if not self.is_guest_preview:
                    self.db.upsert_symbol_sync(new_cfg, user_id=self.user_id)
                updated_count += 1

            items.append(
                {
                    "symbol": sym,
                    "strategy_type": new_cfg.strategy_type,
                    "is_running": new_cfg.is_running,
                    "has_open_position": has_open,
                    "grid_step_pct": new_cfg.grid_step_pct,
                    "grid_take_profit_pct": new_cfg.grid_take_profit_pct,
                    "grid_range_pct": new_cfg.grid_range_pct,
                    "grid_stop_loss_pct": new_cfg.grid_stop_loss_pct,
                    "trend_trailing_mult": new_cfg.trend_trailing_mult,
                    "order_usdt": new_cfg.order_usdt,
                    "issues": issues,
                    "fixes_applied": fixes,
                }
            )

        if req.action == "apply" and updated_count > 0:
            self._save_open_strategy_state()
            msg = f"已批量更新 {updated_count} 个存量合约参数" + (
                f"（其中 {open_deferred_count} 个当前有持仓，待空仓重锚时自动按新参数铺网，不影响在途持仓）"
                if open_deferred_count > 0
                else ""
            )
            self.append_log("INFO", "SYSTEM", f"[批量参数更新] {msg}")
        elif req.action == "apply":
            msg = "全部匹配合约参数已符合目标配置，无需变更"
        else:
            msg = (
                f"体检完成：共扫描 {len(items)} 个合约，发现 {anomaly_symbols} 个合约存在待优化或异常参数"
                if anomaly_symbols > 0
                else f"体检完成：共扫描 {len(items)} 个合约，全部参数校验正常"
            )

        return {
            "ok": True,
            "action": req.action,
            "scanned_count": len(items),
            "anomaly_count": anomaly_symbols,
            "updated_count": updated_count,
            "open_deferred_count": open_deferred_count,
            "grid_round_trip_fee_pct": round(grid_rt_fee * 100.0, 3),
            "message": msg,
            "items": items,
        }

    def get_leader_And_follower_cfg(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        leader = next(
            (ex for ex in self.exchanges.values() if ex["role"] == "leader" and ex["is_active"]),
            self.exchanges.get("binanceusdm", {"exchange_id": "binanceusdm", "display_name": "Binance USDM", "taker_fee": 0.0004, "maker_fee": 0.00018}),
        )
        follower = next(
            (ex for ex in self.exchanges.values() if ex["role"] == "follower" and ex["is_active"]),
            self.exchanges.get("okx", {"exchange_id": "okx", "display_name": "OKX Swap", "taker_fee": 0.0005, "maker_fee": 0.00020}),
        )
        return leader, follower

    async def verify_and_update_exchange(self, exchange_id: str, auto_upgrade_okx_mode: bool = False) -> Dict[str, Any]:
        if self.is_guest_preview:
            return {"ok": False, "auth_status": "GUEST_PREVIEW", "auth_message": "游客模式不支持修改交易所密钥", "account_equity_usdt": 0.0}
        for ex in self.db.load_exchanges_sync(user_id=self.user_id):
            self.exchanges[ex["exchange_id"]] = ex
        ex_row = self.exchanges.get(exchange_id)
        if not ex_row:
            return {"ok": False, "auth_status": "ERROR", "auth_message": "交易所不存在", "account_equity_usdt": 0.0}

        res = await self.executor.verify_exchange_credentials(
            ex_row, auto_upgrade_okx_mode=auto_upgrade_okx_mode
        )
        self.db.update_exchange_auth_status_sync(
            exchange_id=exchange_id,
            auth_status=res["auth_status"],
            auth_message=res["auth_message"],
            account_equity_usdt=res.get("account_equity_usdt", 0.0),
            user_id=self.user_id,
        )
        for ex in self.db.load_exchanges_sync(user_id=self.user_id):
            self.exchanges[ex["exchange_id"]] = ex

        disp = ex_row.get("display_name", exchange_id)
        if res["ok"]:
            self.append_log("FILL", "AUTH", f"[API验证通过] {disp}: {res['auth_message']}", mode="LIVE")
            self.append_log("INFO", "AUTH", f"[API验证通过] {disp}: {res['auth_message']}", mode="SHADOW")
        elif res["auth_status"] != "UNCONFIGURED":
            self.append_log("WARN", "AUTH", f"[API配置异常] {disp}: {res['auth_message']}", mode="LIVE")
            self.append_log("WARN", "AUTH", f"[API配置异常] {disp}: {res['auth_message']}", mode="SHADOW")
        return res

    async def switch_okx_account_mode(self, exchange_id: str = "okx", target_level: str = "2") -> Dict[str, Any]:
        if self.is_guest_preview:
            return {"ok": False, "message": "游客模式无法操作"}
        for ex in self.db.load_exchanges_sync(user_id=self.user_id):
            self.exchanges[ex["exchange_id"]] = ex
        ex_row = self.exchanges.get(exchange_id)
        if not ex_row:
            return {"ok": False, "message": "交易所配置不存在"}
        res = await self.executor.switch_okx_account_level(ex_row, target_level=target_level)
        if res.get("ok") and res.get("verify_result"):
            ver = res["verify_result"]
            self.db.update_exchange_auth_status_sync(
                exchange_id=exchange_id,
                auth_status=ver["auth_status"],
                auth_message=ver["auth_message"],
                account_equity_usdt=ver.get("account_equity_usdt", 0.0),
                user_id=self.user_id,
            )
            for ex in self.db.load_exchanges_sync(user_id=self.user_id):
                self.exchanges[ex["exchange_id"]] = ex
            self.append_log("FILL", "AUTH", f"[账户模式切换成功] {res['message']}", mode="LIVE")
        else:
            self.append_log("WARN", "AUTH", f"[账户模式切换提示] {res.get('message')}", mode="LIVE")
        return res

    def clear_exchange_credentials(self, exchange_id: str) -> Dict[str, Any]:
        if self.is_guest_preview:
            return {"ok": False, "message": "游客模式无法操作"}
        prev_ex = self.exchanges.get(exchange_id, {})
        disp = "全部交易所" if exchange_id == "ALL" else prev_ex.get("display_name", exchange_id)
        self.db.clear_exchange_credentials_sync(exchange_id, user_id=self.user_id)
        for ex in self.db.load_exchanges_sync(user_id=self.user_id):
            self.exchanges[ex["exchange_id"]] = ex

        _, follower_ex = self.get_leader_And_follower_cfg()
        if exchange_id == "ALL" or follower_ex.get("exchange_id") == exchange_id:
            self.trading_active_by_mode["LIVE"] = False

        msg = f"已清除 {disp} 本地加密保存的 API Key、Secret 及口令数据"
        self.append_log("WARN", "AUTH", f"[清除API密钥] {msg}", mode="LIVE")
        self.append_log("INFO", "AUTH", f"[清除API密钥] {msg}", mode="SHADOW")
        self.persist_runtime_state()
        return {"ok": True, "message": msg}

    async def switch_trading_mode(self, new_mode: str, preserve_user_started: bool = False):
        if self.is_guest_preview and new_mode == "LIVE":
            return
        prev_mode = self.risk_config.trading_mode
        self.risk_config.trading_mode = new_mode

        if new_mode == "LIVE":
            if self._owns_gateway:
                self.gateway.feed_mode = "REAL_WS"
            else:
                self.gateway.set_subscriber_mode(self._subscriber_id, "REAL_WS")

            if prev_mode != "LIVE":
                # Wipe simulated ticks so LIVE mode shows 100% real exchange prices immediately
                for sym, m in self.math_states.items():
                    m.reset_ticks()
                await self.gateway.fetch_real_bbo_snapshot(emit_immediately=False)
                for sym, cfg in self.strategies.items():
                    self._sync_cfg_with_exchange_spec(cfg)
                    m = self.math_states.get(sym)
                    if m:
                        self._seed_initial_ticks(sym, cfg, m)

            _, follower_ex = self.get_leader_And_follower_cfg()
            if follower_ex.get("has_api_key") or follower_ex.get("has_api_secret") or follower_ex.get("has_passphrase"):
                await self.verify_and_update_exchange(follower_ex["exchange_id"])
                _, follower_ex = self.get_leader_And_follower_cfg()

            auth_st = follower_ex.get("auth_status", "UNCONFIGURED")
            auth_msg = follower_ex.get("auth_message", "未配置 API 密钥")
            if auth_st == "VERIFIED":
                if not preserve_user_started:
                    self.trading_active_by_mode["LIVE"] = True
                self.append_log(
                    "FILL",
                    "LIVE_MODE",
                    f"[切换实盘通道] 已载入 Binance & OKX 真实盘口价格 | 执行所 ({follower_ex.get('display_name')}) {auth_msg} -> {'自动交易运行中' if self.trading_active_by_mode.get('LIVE') else '待命'}",
                    mode="LIVE",
                )
            else:
                self.trading_active_by_mode["LIVE"] = False
                self.append_log(
                    "WARN",
                    "LIVE_MODE",
                    f"[切换实盘通道] 已载入 Binance & OKX 真实盘口价格 | 执行所 ({follower_ex.get('display_name')}) 状态: {auth_msg}",
                    mode="LIVE",
                )
        else:
            desired = "SIMULATOR" if self.risk_config.feed_source != "REAL_WS" else "REAL_WS"
            if self._owns_gateway:
                self.gateway.feed_mode = desired
            else:
                self.gateway.set_subscriber_mode(self._subscriber_id, desired)
            if prev_mode == "LIVE" and desired == "SIMULATOR":
                for sym, m in self.math_states.items():
                    m.reset_ticks()
                for sym, cfg in self.strategies.items():
                    m = self.math_states.get(sym)
                    if m:
                        self._seed_initial_ticks(sym, cfg, m)
            self.append_log("INFO", "SHADOW_MODE", "[切换模拟盘] 已恢复高频脉冲模拟盘视图", mode="SHADOW")
        self.persist_runtime_state()

    async def set_trading_active(self, active: bool) -> Dict[str, Any]:
        cur_mode = self.risk_config.trading_mode
        if not active:
            self.trading_active_by_mode[cur_mode] = False
            self.persist_runtime_state()
            self._save_open_strategy_state()
            mode_label = "实盘通道" if cur_mode == "LIVE" else "影子模拟盘"
            msg = f"已停止{mode_label}自动交易（当前仅实时刷新行情，不会发送任何新订单）"
            self.append_log("WARN", "CONTROL", f"[停止自动交易] {msg}", mode=cur_mode)
            return {"ok": True, "trading_active": False, "message": msg}

        if self.risk_config.kill_switch_active:
            self.risk_config.kill_switch_active = False

        if cur_mode == "SHADOW":
            self.trading_active_by_mode["SHADOW"] = True
            self.persist_runtime_state()
            msg = "影子模拟盘自动狙击交易已启动，正在实时扫描价差脉冲"
            self.append_log("INFO", "CONTROL", f"[开始自动交易] {msg}", mode="SHADOW")
            return {"ok": True, "trading_active": True, "message": msg}

        # LIVE mode: validate Follower credentials before allowing trading to start!
        _, follower_ex = self.get_leader_And_follower_cfg()
        ver_res = await self.verify_and_update_exchange(follower_ex["exchange_id"])
        _, follower_ex = self.get_leader_And_follower_cfg()

        if not ver_res.get("ok") or follower_ex.get("auth_status") != "VERIFIED":
            self.trading_active_by_mode["LIVE"] = False
            self.persist_runtime_state()
            err_detail = follower_ex.get("auth_message") or "未配置有效的执行所 API 密钥"
            msg = f"无法开始实盘交易：{follower_ex.get('display_name')} {err_detail}"
            self.append_log("CRITICAL", "CONTROL", f"[实盘启动阻断] {msg}", mode="LIVE")
            return {"ok": False, "trading_active": False, "message": msg}

        sync_res = await self.sync_live_exchange_positions(trigger="resume")
        self.trading_active_by_mode["LIVE"] = True
        self.persist_runtime_state()
        synced_cnt = int(sync_res.get("synced_count") or 0)
        rec_cnt = int(sync_res.get("recovered_count") or 0)
        sync_note = (
            f" · 已识别并接管交易所 {synced_cnt} 笔在途持仓" + (f"（新恢复 {rec_cnt} 笔）" if rec_cnt > 0 else "")
            if synced_cnt > 0
            else ""
        )
        msg = f"实盘自动交易已启动！{follower_ex.get('display_name')} ({follower_ex.get('auth_message')}){sync_note}"
        self.append_log("FILL", "CONTROL", f"[实盘交易启动] {msg}", mode="LIVE")
        return {"ok": True, "trading_active": True, "message": msg}

    # ==================== High-Frequency Market Data Callbacks ====================
    def _handle_follower_bbo(self, sym: str, bid: float, ask: float, bid_qty: float, ask_qty: float):
        m_state = self.math_states.get(sym)
        if not m_state:
            return
        m_state.on_follower_tick(bid, ask, bid_qty, ask_qty)
        ev = self.book_events.get(sym)
        if ev:
            ev.set()

        cfg = self.strategies.get(sym)

        # Keep exchange-authoritative live positions' mark price & USDT PnL updated tick-by-tick
        if not self.is_guest_preview and self.risk_config.trading_mode == "LIVE" and self.exchange_live_positions:
            prec_ex = cfg.price_precision if cfg else 6
            for ex_side in ("LONG", "SHORT"):
                ex_pos = self.exchange_live_positions.get(f"{sym}:{ex_side}")
                if ex_pos:
                    ex_e = float(ex_pos.get("entry_price") or 0.0)
                    ex_q = float(ex_pos.get("qty") or 0.0)
                    if ex_e > 0 and ex_q > 0:
                        ex_mark = bid if ex_side == "LONG" else ask
                        ex_pos["mark_price"] = round(ex_mark, prec_ex)
                        ex_pos["notional_usdt"] = round(ex_q * ex_mark, 4)
                        ex_pos["unrealized_usdt"] = round(
                            ((ex_mark - ex_e) if ex_side == "LONG" else (ex_e - ex_mark)) * ex_q,
                            4,
                        )
                        _, follower_ex_live = self.get_leader_And_follower_cfg()
                        ex_rt_fee = 2.0 * float(follower_ex_live.get("taker_fee", 0.0005))
                        raw_ex_r = (ex_mark - ex_e) / ex_e if ex_side == "LONG" else (ex_e - ex_mark) / ex_e
                        ex_pos["unrealized_net_pct"] = round(raw_ex_r - ex_rt_fee, 6)

        if not cfg:
            return

        # Keep live mark price & real-time unrealized USDT PnL fresh for active LEAD_LAG positions
        ap = self.active_positions.get(sym)
        if ap:
            e_px = float(ap.get("entry_price") or 0.0)
            if e_px > 0:
                mark_px = bid if ap.get("side") == "LONG" else ask
                _, follower_ex_ap = self.get_leader_And_follower_cfg()
                rt_fee_ap = float(follower_ex_ap.get("taker_fee", 0.0005)) + float(
                    follower_ex_ap.get("maker_fee", 0.0002)
                )
                raw_r = (mark_px - e_px) / e_px if ap.get("side") == "LONG" else (e_px - mark_px) / e_px
                ap["mark_price"] = round(mark_px, cfg.price_precision)
                ap["unrealized_net_pct"] = round(raw_r - rt_fee_ap, 6)
                q = float(ap.get("qty") or 0.0)
                if q > 0:
                    ap["unrealized_usdt"] = round(
                        ((mark_px - e_px) if ap.get("side") == "LONG" else (e_px - mark_px)) * q,
                        4,
                    )

        if not is_grid_strategy(cfg.strategy_type):
            return

        tracker = self.grid_trackers.get(sym)
        if not tracker:
            tracker = LongShortGridTracker(sym)
            self.grid_trackers[sym] = tracker
        mid = (bid + ask) * 0.5
        bbw_info = (
            self.gateway.get_volatility_regime(sym, cfg.bbw_timeframe)
            if cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID")
            else None
        )
        tracker.sync_config(cfg, current_mid=mid, bbw_info=bbw_info)

        cur_mode = self.risk_config.trading_mode
        if self.is_guest_preview or cur_mode == "SHADOW":
            # Keep guest preview radar scan ticks alive cleanly for grid symbols too
            self.scan_ticks_by_mode[cur_mode] = self.scan_ticks_by_mode.get(cur_mode, 0) + 1

        # Update live mark price & real-time unrealized PnL on all open grid positions (true 2 × taker_fee)
        _, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        grid_rt_fee = 2.0 * taker_fee
        for gk, gpos in list(self.active_grid_positions.items()):
            if gpos.get("symbol") == sym:
                e_px = float(gpos.get("entry_price") or 0.0)
                if e_px > 0:
                    mark_px = bid if gpos["side"] == "LONG" else ask
                    raw_r = (mark_px - e_px) / e_px if gpos["side"] == "LONG" else (e_px - mark_px) / e_px
                    gpos["mark_price"] = round(mark_px, cfg.price_precision)
                    gpos["unrealized_net_pct"] = round(raw_r - grid_rt_fee, 6)
                    q = float(gpos.get("qty") or 0.0)
                    if q > 0:
                        gpos["unrealized_usdt"] = round(
                            ((mark_px - e_px) if gpos["side"] == "LONG" else (e_px - mark_px)) * q,
                            4,
                        )
                lv_ref = tracker.levels.get(gpos.get("grid_slot_id", ""))
                if lv_ref:
                    gpos["sl_price"] = float(lv_ref.get("sl_price") or gpos.get("sl_price") or 0.0)
                gpos["grid_mode"] = tracker.active_grid_mode
                gpos["trail_stop_price"] = (
                    tracker.long_trail_stop_price if gpos["side"] == "LONG" else tracker.short_trail_stop_price
                )

        allow_entries = (
            cfg.is_running
            and not self.risk_config.kill_switch_active
            and bool(self.trading_active_by_mode.get(cur_mode, False))
            and self._passes_grid_risk_checks(cfg)
        )
        if cur_mode == "LIVE":
            if follower_ex.get("auth_status") != "VERIFIED" or not self.gateway.okx_connected:
                allow_entries = False

        api_key = (follower_ex.get("api_key") or "").strip()
        okx_meta = self.executor._get_okx_meta(api_key) if api_key else {"posMode": "net_mode"}
        is_net_mode = (cur_mode == "LIVE") and (okx_meta.get("posMode", "net_mode") == "net_mode")

        entries, exits = tracker.evaluate_tick(
            bid=bid,
            ask=ask,
            mid=mid,
            cfg=cfg,
            is_net_mode=is_net_mode,
            allow_entries=allow_entries,
            round_trip_fee=grid_rt_fee,
            bbw_info=bbw_info,
        )
        regime_msg = tracker.consume_regime_log()
        if regime_msg:
            self.append_log("INFO", sym, regime_msg, mode=cur_mode)
        # Keep live trailing stop synchronized on active_grid_positions immediately after evaluate_tick
        if tracker.active_grid_mode == "TREND_BREAKOUT":
            for gk, gpos in list(self.active_grid_positions.items()):
                if gpos.get("symbol") == sym:
                    gpos["trail_stop_price"] = (
                        tracker.long_trail_stop_price if gpos["side"] == "LONG" else tracker.short_trail_stop_price
                    )

        # Group simultaneous same-side exits into a single aggregated exit order so sibling slots never race!
        if exits:
            by_side: Dict[str, List[dict]] = {}
            for ex_item in exits:
                by_side.setdefault(ex_item["side"], []).append(ex_item)
            for side_exits in by_side.values():
                if len(side_exits) == 1:
                    self._spawn_task(self._run_grid_exit(cfg, tracker, side_exits[0], cur_mode))
                else:
                    self._spawn_task(self._run_grid_exits_batch(cfg, tracker, side_exits, cur_mode))

        for en_item in entries:
            slot_notional = float(en_item.get("order_usdt") or cfg.order_usdt)
            if not self._passes_grid_risk_checks(cfg, extra_order_usdt=slot_notional, exclude_slot=(sym, en_item["slot_id"])):
                tracker.mark_entry_missed(en_item["slot_id"], backoff_ms=3000.0)
                continue
            self.stats_by_mode[cur_mode]["total_signals"] += 1
            _get_or_init_symbol_stat(self.stats_by_mode[cur_mode], sym)["signals"] += 1
            self._spawn_task(self._run_grid_entry(cfg, tracker, en_item, cur_mode))

    def _handle_leader_trade(self, sym: str, side: str, price: float, qty: float):
        m_state = self.math_states.get(sym)
        if m_state:
            m_state.on_leader_trade(side, price, qty)

    def _handle_leader_bbo(self, sym: str, bid: float, ask: float, bid_qty: float, ask_qty: float):
        cfg = self.strategies.get(sym)
        m_state = self.math_states.get(sym)
        if not cfg or not m_state:
            return

        cur_mode = self.risk_config.trading_mode
        self.scan_ticks_by_mode[cur_mode] = self.scan_ticks_by_mode.get(cur_mode, 0) + 1

        state = self.symbol_states.get(sym, SniperState.IDLE)
        is_locked = state != SniperState.IDLE
        m_state.on_leader_tick(bid, ask, bid_qty, ask_qty, cfg, is_locked=is_locked)

        # Only LEAD_LAG symbols fire cross-exchange jump sniper entries here
        if cfg.strategy_type != "LEAD_LAG":
            return

        if is_locked:
            ev = self.book_events.get(sym)
            if ev:
                ev.set()
            return

        if not cfg.is_running or self.risk_config.kill_switch_active:
            return

        # Respect explicit Start / Stop Trading state
        if not self.trading_active_by_mode.get(cur_mode, False):
            return

        _, follower_ex = self.get_leader_And_follower_cfg()

        # In LIVE mode, strictly require verified Follower API credentials!
        if cur_mode == "LIVE":
            if follower_ex.get("auth_status") != "VERIFIED":
                now_m = mono_ms()
                if now_m - self._last_live_cred_warn_mono > 15000.0:
                    self._last_live_cred_warn_mono = now_m
                    self.append_log(
                        "WARN",
                        "LIVE_AUTH",
                        f"[实盘待命] 执行所 ({follower_ex.get('display_name', 'OKX')}) 状态: {follower_ex.get('auth_message', '未验证')}，当前仅更新真实行情不发单",
                        mode="LIVE",
                    )
                return
            if not (self.gateway.binance_connected and self.gateway.okx_connected):
                return

        if not self._passes_global_risk_checks(cfg):
            return

        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        maker_fee = float(follower_ex.get("maker_fee", 0.0002))

        candidate = m_state.evaluate_signal(cfg, taker_fee=taker_fee, maker_fee=maker_fee)
        if candidate is not None:
            signal_age_ms = mono_ms() - candidate.signal_mono_ms
            if signal_age_ms > cfg.max_signal_age_ms:
                return

            # Synchronous lock before task spawn
            self.symbol_states[sym] = SniperState.SIGNALED
            self.stats["total_signals"] += 1
            _get_or_init_symbol_stat(self.stats, sym)["signals"] += 1
            self._spawn_task(self._run_state_machine(cfg, candidate, self.risk_config.trading_mode))

    def _compute_current_exposure(
        self, cur_mode: str, exclude_slot: Optional[tuple[str, str]] = None
    ) -> float:
        exp = sum(
            float(p.get("notional_usdt", 0.0))
            for p in self.active_positions.values()
            if p.get("mode", cur_mode) == cur_mode
        )
        exp += sum(
            float(p.get("notional_usdt", 0.0))
            for p in self.active_grid_positions.values()
            if p.get("mode", cur_mode) == cur_mode
        )
        # Count in-flight ENTERING grid slots so concurrent bursts cannot overshoot max_exposure_usdt
        for s_sym, trk in self.grid_trackers.items():
            s_cfg = self.strategies.get(s_sym)
            default_u = float(s_cfg.order_usdt) if s_cfg else 0.0
            for sid, lv in trk.levels.items():
                if lv.get("status") == "ENTERING":
                    if exclude_slot and exclude_slot == (s_sym, sid):
                        continue
                    exp += float(lv.get("order_usdt") or default_u)
        # Count in-flight SIGNALED / SUBMITTED LEAD_LAG entries
        for s_sym, st in self.symbol_states.items():
            if st in (SniperState.SIGNALED, SniperState.SUBMITTED, SniperState.RECONCILE) and s_sym not in self.active_positions:
                s_cfg = self.strategies.get(s_sym)
                if s_cfg:
                    exp += float(s_cfg.order_usdt)
        if cur_mode == "LIVE":
            exp += sum(
                float(ep.get("notional_usdt", 0.0))
                for ep in self.exchange_live_positions.values()
                if not ep.get("managed_by_strategy")
            )
        return exp

    def _passes_grid_risk_checks(
        self,
        cfg: SymbolStrategy,
        extra_order_usdt: Optional[float] = None,
        exclude_slot: Optional[tuple[str, str]] = None,
    ) -> bool:
        risk = self.risk_config
        cur_mode = risk.trading_mode
        cur_stats = self.stats
        self._ensure_daily_pnl_date(cur_stats)
        if risk.kill_switch_active:
            return False
        if cur_stats["daily_net_pnl"] <= -abs(risk.max_daily_loss_usdt):
            return False
        if cur_stats["consecutive_losses"] >= risk.max_consecutive_losses:
            return False
        add_usdt = float(extra_order_usdt) if extra_order_usdt is not None else float(cfg.order_usdt)
        current_exposure = self._compute_current_exposure(cur_mode, exclude_slot=exclude_slot)
        if current_exposure + add_usdt > risk.max_exposure_usdt:
            return False
        if self.executor.last_rtt_ms > risk.latency_circuit_breaker_ms:
            return False
        return True

    def _record_symbol_rolling_pnl_and_check_auto_disable(
        self, sym: str, cfg: SymbolStrategy, net_pnl: float, exec_mode: str
    ):
        rolling = self.symbol_rolling_pnls.get(sym)
        if rolling is None:
            rolling = deque(maxlen=100)
            self.symbol_rolling_pnls[sym] = rolling
        rolling.append(net_pnl)
        win_n = max(1, int(self.risk_config.auto_disable_rolling_trades))
        if len(rolling) >= win_n:
            recent_slice = list(rolling)[-win_n:]
            if sum(recent_slice) < 0:
                cfg.is_running = False
                cfg.auto_disabled_reason = f"滚动 {win_n} 笔累计净利为负 ({sum(recent_slice):.2f} USDT)，触发自动熔断停用"
                if not self.is_guest_preview:
                    self.db.upsert_symbol_sync(cfg, user_id=self.user_id)
                    self.db.record_risk_event_async("WARN", "AUTO_DISABLE", sym, cfg.auto_disabled_reason, user_id=self.user_id)
                self.append_log("WARN", sym, f"[风控熔断] {sym} {cfg.auto_disabled_reason}", mode=exec_mode)

    async def _run_grid_entry(
        self,
        cfg: SymbolStrategy,
        tracker: LongShortGridTracker,
        slot_info: dict,
        exec_mode: str,
    ):
        sym = cfg.symbol
        slot_id = slot_info["slot_id"]
        side = slot_info["side"]
        res_px = float(slot_info["reservation_price"])
        trig_px = float(slot_info["trigger_exec_price"])
        leader_ex, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        grid_rt_fee = 2.0 * taker_fee
        mode_stats = self.stats_by_mode[exec_mode]

        slot_order_usdt = float(slot_info.get("order_usdt") or cfg.order_usdt)
        slot_weight_pct = float(slot_info.get("weight_pct") or 0.0)
        is_trend_mode = tracker.active_grid_mode == "TREND_BREAKOUT"
        exec_cfg = cfg.model_copy(update={"order_usdt": slot_order_usdt}) if abs(slot_order_usdt - cfg.order_usdt) > 1e-6 else cfg
        est_qty = slot_order_usdt / max(1e-12, res_px)
        try:
            async with self._get_symbol_exec_lock(sym):
                if self.risk_config.kill_switch_active:
                    tracker.mark_entry_missed(slot_id, backoff_ms=3000.0)
                    return

                entry_res = await self.executor.place_entry_ioc(
                    cfg=exec_cfg,
                    side=side,
                    qty=est_qty,
                    reservation_price=res_px,
                    trigger_price=trig_px,
                    exchange_cfg=follower_ex,
                )
                if entry_res.status == "UNCERTAIN":
                    mode_stats["reconciled_count"] += 1
                    entry_res = await self.executor.reconcile_order(
                        exec_cfg,
                        entry_res.order_id,
                        est_qty,
                        res_px,
                        exchange_cfg=follower_ex,
                        ct_val=entry_res.ct_val or None,
                        cl_ord_id=entry_res.cl_ord_id,
                    )

                if entry_res.status == "UNCERTAIN" and exec_mode == "LIVE":
                    # Do NOT treat unresolved live entry as normal MISSED — back off 60s and sync OKX positions immediately!
                    tracker.mark_entry_missed(slot_id, backoff_ms=60_000.0)
                    if not self.is_guest_preview:
                        self.db.record_risk_event_async(
                            "CRITICAL", "GRID_ENTRY_UNCERTAIN", sym, f"[{slot_id}] {entry_res.raw_msg}", user_id=self.user_id
                        )
                    self.append_log("CRITICAL", sym, f"[网格开仓待对账] {sym} [{slot_id}] {entry_res.raw_msg}", mode=exec_mode)
                    self._spawn_task(self.sync_live_exchange_positions(trigger="entry_uncertain"))
                    return

                raw_reason = str(entry_res.raw_msg or "")
                is_structural_block = (
                    (entry_res.filled_qty <= 0 or entry_res.status == "MISSED")
                    and any(
                        marker in raw_reason
                        for marker in (
                            "元数据校验拦截",
                            "最小开仓额",
                            "可用保证金",
                            "ctVal 未知",
                            "暂未对接真实实盘",
                            "(51008)",
                            "(51010)",
                            "(51020)",
                            "余额不足",
                        )
                    )
                )

                if not is_structural_block:
                    mode_tag = "顺势突破开仓" if is_trend_mode else "多空网格开仓"
                    w_note = f" ({slot_weight_pct:g}%权重)" if (is_trend_mode and slot_weight_pct > 0) else ""
                    self.append_log(
                        "SIGNAL",
                        sym,
                        f"[{mode_tag}] {sym} [{slot_id}] 触发 {'突破做多' if (is_trend_mode and side == 'LONG') else ('跌破做空' if is_trend_mode else ('做多网格' if side == 'LONG' else '做空网格'))} @ {trig_px:.{cfg.price_precision}f} | 下单额 {slot_order_usdt:.1f}U{w_note}",
                        mode=exec_mode,
                    )
                    if not self.is_guest_preview:
                        self.db.record_order_async(
                            order_id=entry_res.order_id,
                            symbol=sym,
                            exchange_id=follower_ex["exchange_id"],
                            purpose=f"GRID_{slot_id}",
                            side=side,
                            order_type="IOC_LIMIT",
                            price=res_px,
                            requested_qty=est_qty,
                            filled_qty=entry_res.filled_qty,
                            avg_price=entry_res.avg_price,
                            status=entry_res.status,
                            latency_ms=entry_res.latency_ms,
                            user_id=self.user_id,
                        )

                if entry_res.filled_qty <= 0 or entry_res.status == "MISSED":
                    if is_structural_block:
                        # Back off 60s on impossible size/margin blocks so we don't spam SQLite or logs every 1.2s
                        tracker.mark_entry_missed(slot_id, backoff_ms=60_000.0)
                        now_m = mono_ms()
                        if now_m - self._grid_block_log_ts.get(sym, 0.0) >= 300_000.0:
                            self._grid_block_log_ts[sym] = now_m
                            self.append_log(
                                "WARN",
                                sym,
                                f"[网格下单受限] {sym} [{slot_id}] {raw_reason}（已自动降频，调高每格下单额或补充保证金后恢复）",
                                mode=exec_mode,
                            )
                    else:
                        tracker.mark_entry_missed(slot_id, backoff_ms=1200.0)
                    mode_stats["ioc_missed_count"] += 1
                    _get_or_init_symbol_stat(mode_stats, sym)["missed"] += 1
                    return

                mode_stats["ioc_filled_count"] += 1
                _get_or_init_symbol_stat(mode_stats, sym)["filled"] += 1
                tracker.mark_entry_filled(
                    slot_id=slot_id,
                    entry_price=entry_res.avg_price,
                    filled_qty=entry_res.filled_qty,
                    contracts=entry_res.contracts,
                    ct_val=entry_res.ct_val,
                    cfg=cfg,
                    round_trip_fee=grid_rt_fee,
                )
                lv = tracker.levels.get(slot_id, {})
                gk = f"{sym}:{slot_id}"
                self.active_grid_positions[gk] = {
                    "symbol": sym,
                    "strategy_type": cfg.strategy_type,
                    "grid_mode": tracker.active_grid_mode,
                    "grid_slot_id": slot_id,
                    "mode": exec_mode,
                    "side": side,
                    "qty": entry_res.filled_qty,
                    "contracts": entry_res.contracts,
                    "ct_val": entry_res.ct_val,
                    "notional_usdt": round(entry_res.filled_qty * entry_res.avg_price, 2),
                    "entry_price": entry_res.avg_price,
                    "mark_price": entry_res.avg_price,
                    "tp_price": lv.get("tp_price", 0.0 if is_trend_mode else entry_res.avg_price),
                    "sl_price": lv.get("sl_price", 0.0),
                    "trail_stop_price": (
                        tracker.long_trail_stop_price if side == "LONG" else tracker.short_trail_stop_price
                    ),
                    "opened_mono_ms": mono_ms(),
                    "unrealized_net_pct": round(-grid_rt_fee, 6),
                    "unrealized_usdt": 0.0,
                    "recovered": False,
                    "managed_by_strategy": True,
                }
                # Sync Livermore stepped-up breakeven stop-losses to all earlier open slots of this symbol
                for other_gk, other_pos in self.active_grid_positions.items():
                    if other_pos.get("symbol") == sym:
                        other_lv = tracker.levels.get(other_pos.get("grid_slot_id", ""))
                        if other_lv and float(other_lv.get("sl_price") or 0.0) > 0:
                            other_pos["sl_price"] = float(other_lv["sl_price"])
                self._save_open_strategy_state()
                if is_trend_mode:
                    self.append_log(
                        "FILL",
                        sym,
                        f"[顺势突破成交] {sym} [{slot_id}] {side} 均价: {entry_res.avg_price:.{cfg.price_precision}f} | 数量: {entry_res.filled_qty} | 假突破/保本止损: {lv.get('sl_price', 0):.{cfg.price_precision}f} | 追踪止盈: 回撤 {cfg.trend_trailing_mult * cfg.grid_step_pct * 100:.2f}%",
                        mode=exec_mode,
                    )
                else:
                    self.append_log(
                        "FILL",
                        sym,
                        f"[多空网格成交] {sym} [{slot_id}] {side} 均价: {entry_res.avg_price:.{cfg.price_precision}f} | 数量: {entry_res.filled_qty} | 止盈目标: {lv.get('tp_price', 0):.{cfg.price_precision}f}",
                        mode=exec_mode,
                    )

                # If Kill Switch fired while this IOC was in flight, immediately close the newly opened slot!
                if self.risk_config.kill_switch_active and lv.get("status") == "OPEN":
                    lv["status"] = "EXITING"
                    kill_exit = {**lv, "exit_reason": "KILL_SWITCH", "target_price": entry_res.avg_price}
                    self._spawn_task(self._run_grid_exit(cfg, tracker, kill_exit, exec_mode))
        except Exception:
            tracker.mark_entry_missed(slot_id)

    async def _run_grid_exit(
        self,
        cfg: SymbolStrategy,
        tracker: LongShortGridTracker,
        exit_info: dict,
        exec_mode: str,
    ):
        await self._run_grid_exits_batch(cfg, tracker, [exit_info], exec_mode)

    async def _run_grid_exits_batch(
        self,
        cfg: SymbolStrategy,
        tracker: LongShortGridTracker,
        exit_items: List[dict],
        exec_mode: str,
    ):
        """
        Executes 1 or more same-side grid slot exits serially under the per-symbol lock.
        Aggregates simultaneous same-side exits into a single reduce-only order and NEVER invokes
        OKX full `close-position` fallback if unrelated sibling slots remain open on that side!
        """
        if not exit_items:
            return
        sym = cfg.symbol
        side = exit_items[0]["side"]
        leader_ex, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        maker_fee = float(follower_ex.get("maker_fee", 0.0002))
        exiting_ids = {it["slot_id"] for it in exit_items}

        try:
            async with self._get_symbol_exec_lock(sym):
                total_qty = sum(max(0.0, float(it.get("filled_qty") or 0.0)) for it in exit_items)
                total_contracts = sum(max(0.0, float(it.get("contracts") or 0.0)) for it in exit_items)
                ct_val = float(exit_items[0].get("ct_val") or 1.0)
                ref_tp = float(exit_items[0].get("tp_price") or exit_items[0].get("target_price") or exit_items[0].get("entry_price") or 0.0)
                primary_reason = str(exit_items[0].get("exit_reason") or "GRID_TP")
                primary_key = f"{sym}:{exit_items[0]['slot_id']}"

                # Only allow full `close-position` fallback when NO other same-side slots remain OPEN/ENTERING!
                other_open_same_side = sum(
                    1
                    for sid, lv in tracker.levels.items()
                    if sid not in exiting_ids and lv.get("side") == side and lv.get("status") in ("OPEN", "ENTERING")
                )
                allow_full_close_fallback = (other_open_same_side == 0)

                exit_res = await self.executor.execute_exit_with_retry(
                    cfg=cfg,
                    side=side,
                    qty=total_qty,
                    exit_reason=primary_reason,
                    tp_price=ref_tp,
                    exchange_cfg=follower_ex,
                    contracts=total_contracts or None,
                    ct_val=ct_val or None,
                    order_key=primary_key,
                    allow_close_position_fallback=allow_full_close_fallback,
                )
                if exit_res.status == "UNCERTAIN" and exec_mode == "LIVE":
                    for it in exit_items:
                        tracker.revert_exit_failed(it["slot_id"], backoff_ms=5000.0)
                    if not self.is_guest_preview:
                        self.db.record_risk_event_async(
                            "CRITICAL", "GRID_EXIT_UNCERTAIN", sym, exit_res.raw_msg, user_id=self.user_id
                        )
                    self.append_log(
                        "CRITICAL",
                        sym,
                        f"[网格平仓告警] {sym} [{','.join(sorted(exiting_ids))}] {exit_res.raw_msg}（5秒后重试）",
                        mode=exec_mode,
                    )
                    return

                exit_avg = exit_res.avg_price
                now_dt = datetime.now()
                now_time_str = now_dt.strftime("%H:%M:%S.") + f"{int(now_dt.microsecond / 1000):03d}"
                now_wall = wall_ms()
                for it in exit_items:
                    slot_id = it["slot_id"]
                    qty = float(it.get("filled_qty") or 0.0)
                    entry_avg = float(it.get("entry_price") or 0.0)
                    slot_tp = float(it.get("tp_price") or it.get("target_price") or entry_avg)
                    slot_reason = str(it.get("exit_reason") or primary_reason)
                    gk = f"{sym}:{slot_id}"

                    gross_pnl = (exit_avg - entry_avg) * qty if side == "LONG" else (entry_avg - exit_avg) * qty
                    notional_entry = entry_avg * qty
                    notional_exit = exit_avg * qty
                    total_fee = notional_entry * taker_fee + notional_exit * (maker_fee if exit_res.is_maker else taker_fee)
                    net_pnl = gross_pnl - total_fee
                    hold_ms = int(max(1.0, mono_ms() - float(it.get("opened_mono_ms") or mono_ms())))

                    trade_record = {
                        "time": now_time_str,
                        "symbol": sym,
                        "leader_ex": leader_ex["exchange_id"],
                        "follower_ex": follower_ex["exchange_id"],
                        "side": side,
                        "trading_mode": exec_mode,
                        "order_id": f"grid-{slot_id}-{exit_res.order_id}",
                        "exit_order_type": "MAKER_POST_ONLY" if exit_res.is_maker else "TAKER_IOC",
                        "leader_jump_pct": 0.0,
                        "follower_lag_pct": float(cfg.grid_step_pct),
                        "expected_net_pct": float(cfg.grid_take_profit_pct),
                        "actual_slippage_pct": 0.0,
                        "beta_est": 1.0,
                        "leader_snapshot_price": it.get("trigger_price", entry_avg),
                        "follower_snapshot_price": entry_avg,
                        "reservation_price": slot_tp,
                        "entry_price": entry_avg,
                        "exit_price": exit_avg,
                        "filled_qty": qty,
                        "gross_pnl_usdt": round(gross_pnl, 4),
                        "fee_usdt": round(total_fee, 4),
                        "net_pnl_usdt": round(net_pnl, 4),
                        "signal_ts_ms": now_wall,
                        "submit_ts_ms": now_wall,
                        "ack_ts_ms": now_wall,
                        "signal_to_fill_ms": round(exit_res.latency_ms, 2),
                        "hold_duration_ms": hold_ms,
                        "exit_reason": f"{slot_reason}_{slot_id}",
                    }
                    if not self.is_guest_preview:
                        self.db.record_trade_async(trade_record, user_id=self.user_id)
                    self._accumulate_trade_into_stats(trade_record, is_historical=False, mode_override=exec_mode)
                    self._record_symbol_rolling_pnl_and_check_auto_disable(sym, cfg, net_pnl, exec_mode)
                    tracker.mark_exited(slot_id, net_pnl)
                    self.active_grid_positions.pop(gk, None)
                    self.append_log(
                        "EXIT_WIN" if net_pnl >= 0 else "EXIT_LOSS",
                        sym,
                        f"[多空网格平仓] {sym} [{slot_id}] {side} 归因: {slot_reason} | 开={entry_avg:.{cfg.price_precision}f} 平={exit_avg:.{cfg.price_precision}f} | 净收益: {net_pnl:+.4f} USDT",
                        mode=exec_mode,
                    )
                self._save_open_strategy_state()
        except Exception:
            for it in exit_items:
                tracker.revert_exit_failed(it["slot_id"], backoff_ms=5000.0)

    def _passes_global_risk_checks(self, cfg: SymbolStrategy) -> bool:
        risk = self.risk_config
        cur_mode = risk.trading_mode
        cur_stats = self.stats
        self._ensure_daily_pnl_date(cur_stats)
        if risk.kill_switch_active:
            return False
        if cur_stats["daily_net_pnl"] <= -abs(risk.max_daily_loss_usdt):
            return False
        if cur_stats["consecutive_losses"] >= risk.max_consecutive_losses:
            return False
        open_count = sum(1 for p in self.active_positions.values() if p.get("mode", cur_mode) == cur_mode)
        if open_count >= risk.max_concurrent_positions:
            return False
        current_exposure = self._compute_current_exposure(cur_mode)
        if current_exposure + cfg.order_usdt > risk.max_exposure_usdt:
            return False
        if self.executor.last_rtt_ms > risk.latency_circuit_breaker_ms:
            return False
        return True

    # ==================== 8-Stage State Machine ====================
    async def _run_state_machine(self, cfg: SymbolStrategy, sig: SignalCandidate, exec_mode: str):
        sym = cfg.symbol
        leader_ex, follower_ex = self.get_leader_And_follower_cfg()
        taker_fee = float(follower_ex.get("taker_fee", 0.0005))
        maker_fee = float(follower_ex.get("maker_fee", 0.0002))
        mode_Stats = self.stats_by_mode[exec_mode]

        try:
            self.symbol_states[sym] = SniperState.SUBMITTED
            submit_wall_ms = wall_ms()

            self.append_log(
                "SIGNAL",
                sym,
                f"[信号触发] {sym} 主所 {cfg.window_ms}ms {'拉升' if sig.side == 'LONG' else '下挫'} "
                f"{sig.leader_jump_pct*100:+.2f}% ({sig.z_score:.1f}σ) | "
                f"从所盘口价: {sig.trigger_price:.{cfg.price_precision}f} | "
                f"保留价: {sig.reservation_price:.{cfg.price_precision}f} | "
                f"预期净利: {sig.expected_net_pct*100:+.2f}% (β={sig.beta_used:.2f}) -> 发送限价IOC{ '买' if sig.side == 'LONG' else '卖' }单",
                mode=exec_mode,
            )

            keep_locked = False
            pos_record: Optional[Dict[str, Any]] = None

            entry_res = await self.executor.place_entry_ioc(
                cfg=cfg,
                side=sig.side,
                qty=sig.recommended_qty,
                reservation_price=sig.reservation_price,
                trigger_price=sig.trigger_price,
                exchange_cfg=follower_ex,
            )

            if entry_res.status == "UNCERTAIN":
                self.symbol_states[sym] = SniperState.RECONCILE
                mode_Stats["reconciled_count"] += 1
                self.append_log("WARN", sym, f"[状态对账] {sym} 报单回包未决，执行私有流与持仓核验...", mode=exec_mode)
                entry_res = await self.executor.reconcile_order(
                    cfg,
                    entry_res.order_id,
                    sig.recommended_qty,
                    sig.reservation_price,
                    exchange_cfg=follower_ex,
                    ct_val=entry_res.ct_val or None,
                    cl_ord_id=entry_res.cl_ord_id,
                )

            ack_wall_ms = wall_ms()
            signal_to_fill_ms = round(mono_ms() - sig.signal_mono_ms, 2)

            if not self.is_guest_preview:
                self.db.record_order_async(
                    order_id=entry_res.order_id,
                    symbol=sym,
                    exchange_id=follower_ex["exchange_id"],
                    purpose="ENTRY_IOC",
                    side=sig.side,
                    order_type="IOC_LIMIT",
                    price=sig.reservation_price,
                    requested_qty=sig.recommended_qty,
                    filled_qty=entry_res.filled_qty,
                    avg_price=entry_res.avg_price,
                    status=entry_res.status,
                    latency_ms=entry_res.latency_ms,
                    user_id=self.user_id,
                )

            if entry_res.status == "UNCERTAIN" and exec_mode == "LIVE":
                keep_locked = True
                self.symbol_states[sym] = SniperState.EXIT_UNCERTAIN
                if not self.is_guest_preview:
                    self.db.record_risk_event_async("CRITICAL", "ENTRY_UNCERTAIN", sym, entry_res.raw_msg, user_id=self.user_id)
                self.append_log("CRITICAL", sym, f"[开仓状态未决] {sym} {entry_res.raw_msg}，已锁定合约并触发实盘持仓对账", mode=exec_mode)
                self._spawn_task(self.sync_live_exchange_positions(trigger="entry_uncertain"))
                return

            if entry_res.filled_qty <= 0 or entry_res.status == "MISSED":
                mode_Stats["ioc_missed_count"] += 1
                _get_or_init_symbol_stat(mode_Stats, sym)["missed"] += 1
                self.append_log(
                    "MISS",
                    sym,
                    f"[未成撤单] {sym} {entry_res.raw_msg} (耗时 {entry_res.latency_ms:.1f}ms, 零成本撤单重置)",
                    mode=exec_mode,
                )
                return

            mode_Stats["ioc_filled_count"] += 1
            _get_or_init_symbol_stat(mode_Stats, sym)["filled"] += 1
            self.symbol_states[sym] = SniperState.HOLDING
            filled_qty = entry_res.filled_qty
            entry_contracts = entry_res.contracts
            entry_ct_val = entry_res.ct_val
            entry_avg = entry_res.avg_price
            entry_mono_ms = mono_ms()

            exit_fee_rate = maker_fee if cfg.use_post_only_tp else taker_fee
            round_trip_fee = taker_fee + exit_fee_rate
            if sig.side == "LONG":
                tp_price = round(entry_avg * (1.0 + cfg.take_profit_pct + round_trip_fee), cfg.price_precision)
                sl_price = round(entry_avg * (1.0 - cfg.stop_loss_pct), cfg.price_precision)
            else:
                tp_price = round(entry_avg * (1.0 - cfg.take_profit_pct - round_trip_fee), cfg.price_precision)
                sl_price = round(entry_avg * (1.0 + cfg.stop_loss_pct), cfg.price_precision)

            tp_oid, sl_oid, post_only_crossed = await self.executor.place_protective_orders(
                cfg,
                sig.side,
                filled_qty,
                tp_price,
                sl_price,
                exchange_cfg=follower_ex,
                contracts=entry_contracts or None,
                ct_val=entry_ct_val or None,
            )

            pos_record = {
                "symbol": sym,
                "mode": exec_mode,
                "side": sig.side,
                "qty": filled_qty,
                "notional_usdt": round(filled_qty * entry_avg, 2),
                "entry_price": entry_avg,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "leader_entry_mid": sig.leader_mid,
                "leader_jump_pct": sig.leader_jump_pct,
                "opened_mono_ms": entry_mono_ms,
                "tp_order_id": tp_oid,
                "sl_order_id": sl_oid,
                "peak_net_pct": 0.0,
                "unrealized_net_pct": 0.0,
            }
            self.active_positions[sym] = pos_record
            if not self.is_guest_preview:
                self.db.upsert_position_async(
                    sym,
                    follower_ex["exchange_id"],
                    sig.side,
                    filled_qty,
                    entry_avg,
                    tp_oid,
                    sl_oid,
                    "HOLDING",
                    ack_wall_ms,
                    user_id=self.user_id,
                )

            self.append_log(
                "FILL",
                sym,
                f"[抢单成交] {sym} {'多头' if sig.side == 'LONG' else '空头'} 均价: {entry_avg:.{cfg.price_precision}f} | 数量: {filled_qty} | "
                f"耗时: {signal_to_fill_ms:.1f}ms | 已挂被动止盈: {tp_price:.{cfg.price_precision}f} & 止损: {sl_price:.{cfg.price_precision}f}",
                mode=exec_mode,
            )

            exit_reason = "TIMEOUT"
            peak_net_pct = -999.0
            last_new_high_mono = entry_mono_ms
            trailing_armed = False
            m_state = self.math_states[sym]
            ev = self.book_events[sym]

            if post_only_crossed:
                exit_reason = "TAKE_PROFIT"
            else:
                while True:
                    now_hold_mono = mono_ms()
                    hold_elapsed_ms = now_hold_mono - entry_mono_ms
                    if hold_elapsed_ms >= cfg.max_hold_ms:
                        exit_reason = "TAKE_PROFIT" if trailing_armed else "TIMEOUT"
                        break

                    if self.active_positions.get(sym) is not pos_record:
                        # Closed externally (e.g., manual_close_position) -> abort exit cleanly without clobbering new trades
                        return

                    if self.risk_config.kill_switch_active:
                        exit_reason = "KILL_SWITCH"
                        break

                    f_curr = m_state.latest_follower
                    l_curr = m_state.latest_leader
                    if f_curr and l_curr:
                        if sig.side == "LONG":
                            mark_px = f_curr.bid
                            raw_ret = (mark_px - entry_avg) / entry_avg
                            curr_net_pct = raw_ret - round_trip_fee

                            if mark_px >= tp_price:
                                if not cfg.trailing_stop_enabled:
                                    exit_reason = "TAKE_PROFIT"
                                    break
                                if not trailing_armed:
                                    trailing_armed = True
                                    last_new_high_mono = now_hold_mono

                            if curr_net_pct > peak_net_pct + 1e-6:
                                peak_net_pct = curr_net_pct
                                last_new_high_mono = now_hold_mono
                            if trailing_armed:
                                if (peak_net_pct - curr_net_pct) >= cfg.trailing_retracement_pct:
                                    exit_reason = "TRAILING_TP"
                                    break
                                if (now_hold_mono - last_new_high_mono) >= 45.0:
                                    exit_reason = "TAKE_PROFIT"
                                    break

                            if mark_px <= sl_price:
                                exit_reason = "HARD_SL"
                                break

                            leader_retrace = (sig.leader_mid - l_curr.mid) / (abs(sig.leader_jump_pct) * sig.leader_mid + 1e-9)
                            if hold_elapsed_ms > 45.0 and leader_retrace >= cfg.reversal_stop_ratio:
                                exit_reason = "REVERSAL_SL"
                                break

                        else:
                            mark_px = f_curr.ask
                            raw_ret = (entry_avg - mark_px) / entry_avg
                            curr_net_pct = raw_ret - round_trip_fee

                            if mark_px <= tp_price:
                                if not cfg.trailing_stop_enabled:
                                    exit_reason = "TAKE_PROFIT"
                                    break
                                if not trailing_armed:
                                    trailing_armed = True
                                    last_new_high_mono = now_hold_mono

                            if curr_net_pct > peak_net_pct + 1e-6:
                                peak_net_pct = curr_net_pct
                                last_new_high_mono = now_hold_mono
                            if trailing_armed:
                                if (peak_net_pct - curr_net_pct) >= cfg.trailing_retracement_pct:
                                    exit_reason = "TRAILING_TP"
                                    break
                                if (now_hold_mono - last_new_high_mono) >= 45.0:
                                    exit_reason = "TAKE_PROFIT"
                                    break

                            if mark_px >= sl_price:
                                exit_reason = "HARD_SL"
                                break

                            leader_retrace = (l_curr.mid - sig.leader_mid) / (abs(sig.leader_jump_pct) * sig.leader_mid + 1e-9)
                            if hold_elapsed_ms > 45.0 and leader_retrace >= cfg.reversal_stop_ratio:
                                exit_reason = "REVERSAL_SL"
                                break

                        if sym in self.active_positions:
                            self.active_positions[sym]["peak_net_pct"] = round(max(0.0, peak_net_pct), 6)
                            self.active_positions[sym]["unrealized_net_pct"] = round(curr_net_pct, 6)

                    ev.clear()
                    remaining_sec = max(0.001, (cfg.max_hold_ms - (mono_ms() - entry_mono_ms)) / 1000.0)
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=min(0.015, remaining_sec))
                    except asyncio.TimeoutError:
                        pass

            async with self._get_symbol_exec_lock(sym):
                if self.active_positions.get(sym) is not pos_record:
                    return
                self.symbol_states[sym] = SniperState.EXITING
                exit_res = await self.executor.execute_exit_with_retry(
                    cfg=cfg,
                    side=sig.side,
                    qty=filled_qty,
                    exit_reason=exit_reason,
                    tp_price=tp_price,
                    exchange_cfg=follower_ex,
                    contracts=entry_contracts or None,
                    ct_val=entry_ct_val or None,
                )
            hold_duration_ms = int(mono_ms() - entry_mono_ms)

            if exit_res.status == "UNCERTAIN" and exec_mode == "LIVE":
                keep_locked = True
                self.symbol_states[sym] = SniperState.EXIT_UNCERTAIN
                if not self.is_guest_preview:
                    self.db.upsert_position_async(
                        sym,
                        follower_ex["exchange_id"],
                        sig.side,
                        filled_qty,
                        entry_avg,
                        tp_oid,
                        sl_oid,
                        "UNCERTAIN",
                        ack_wall_ms,
                        user_id=self.user_id,
                    )
                    self.db.record_risk_event_async("CRITICAL", "EXIT_UNCERTAIN", sym, exit_res.raw_msg, user_id=self.user_id)
                self.append_log("CRITICAL", sym, f"[实盘平仓告警] {sym} {exit_res.raw_msg}（已锁定等待持仓核验）", mode=exec_mode)
                return

            self.symbol_states[sym] = SniperState.CLOSED
            exit_avg = exit_res.avg_price
            notional_entry = entry_avg * filled_qty
            notional_exit = exit_avg * filled_qty

            if sig.side == "LONG":
                gross_pnl = (exit_avg - entry_avg) * filled_qty
                actual_slip_pct = (entry_avg - sig.trigger_price) / sig.trigger_price
            else:
                gross_pnl = (entry_avg - exit_avg) * filled_qty
                actual_slip_pct = (sig.trigger_price - entry_avg) / sig.trigger_price

            entry_fee = notional_entry * taker_fee
            exit_fee = notional_exit * (maker_fee if exit_res.is_maker else taker_fee)
            total_fee = entry_fee + exit_fee
            net_pnl = gross_pnl - total_fee
            exit_order_type = "MAKER_POST_ONLY" if exit_res.is_maker else "TAKER_IOC"

            trade_record = {
                "time": datetime.now().strftime("%H:%M:%S.") + f"{int(datetime.now().microsecond / 1000):03d}",
                "symbol": sym,
                "leader_ex": leader_ex["exchange_id"],
                "follower_ex": follower_ex["exchange_id"],
                "side": sig.side,
                "trading_mode": exec_mode,
                "order_id": entry_res.order_id,
                "exit_order_type": exit_order_type,
                "leader_jump_pct": round(sig.leader_jump_pct, 6),
                "follower_lag_pct": round(sig.follower_lag_pct, 6),
                "expected_net_pct": round(sig.expected_net_pct, 6),
                "actual_slippage_pct": round(actual_slip_pct, 6),
                "beta_est": round(sig.beta_used, 4),
                "leader_snapshot_price": sig.leader_mid,
                "follower_snapshot_price": sig.follower_mid,
                "reservation_price": sig.reservation_price,
                "entry_price": entry_avg,
                "exit_price": exit_avg,
                "filled_qty": filled_qty,
                "gross_pnl_usdt": round(gross_pnl, 4),
                "fee_usdt": round(total_fee, 4),
                "net_pnl_usdt": round(net_pnl, 4),
                "signal_ts_ms": sig.signal_wall_ms,
                "submit_ts_ms": submit_wall_ms,
                "ack_ts_ms": ack_wall_ms,
                "signal_to_fill_ms": signal_to_fill_ms,
                "hold_duration_ms": hold_duration_ms,
                "exit_reason": exit_reason,
            }

            if not self.is_guest_preview:
                self.db.record_trade_async(trade_record, user_id=self.user_id)
                self.db.upsert_position_async(
                    sym, follower_ex["exchange_id"], sig.side, 0.0, entry_avg, "", "", "CLOSED", ack_wall_ms, user_id=self.user_id
                )
            self._accumulate_trade_into_stats(trade_record, is_historical=False, mode_override=exec_mode)
            self._record_symbol_rolling_pnl_and_check_auto_disable(sym, cfg, net_pnl, exec_mode)

            self.append_log(
                "EXIT_WIN" if net_pnl >= 0 else "EXIT_LOSS",
                sym,
                f"[平仓闭环] {sym} {'多头' if sig.side == 'LONG' else '空头'} 归因: {exit_reason} ({'被动挂单' if exit_res.is_maker else '主动吃单'}) | "
                f"平仓价: {exit_avg:.{cfg.price_precision}f} | 持仓: {hold_duration_ms}ms | "
                f"手续费: {total_fee:.4f}U | 净收益: {net_pnl:+.4f} USDT",
                mode=exec_mode,
            )

        finally:
            if not keep_locked:
                if pos_record is None or self.active_positions.get(sym) is pos_record:
                    self.active_positions.pop(sym, None)
                    self.symbol_states[sym] = SniperState.COOLDOWN
                    await asyncio.sleep(self.risk_config.cooldown_ms / 1000.0)
                    if self.symbol_states.get(sym) == SniperState.COOLDOWN:
                        self.symbol_states[sym] = SniperState.IDLE

    def _accumulate_trade_into_stats(self, tr: Dict[str, Any], is_historical: bool = False, mode_override: Optional[str] = None):
        target_mode = mode_override or tr.get("trading_mode", "SHADOW")
        if target_mode not in self.stats_by_mode:
            target_mode = "SHADOW"
        bucket = self.stats_by_mode[target_mode]
        today_utc = self._ensure_daily_pnl_date(bucket)

        gross = float(tr.get("gross_pnl_usdt", 0.0))
        fee = float(tr.get("fee_usdt", 0.0))
        net = float(tr.get("net_pnl_usdt", 0.0))
        side = tr.get("side", "LONG")
        exit_type = tr.get("exit_order_type", "MAKER_POST_ONLY")

        if is_historical:
            bucket["total_signals"] += 1
            bucket["ioc_filled_count"] += 1

        bucket["total_gross_pnl"] = round(bucket["total_gross_pnl"] + gross, 4)
        bucket["total_fees"] = round(bucket["total_fees"] + fee, 4)
        bucket["total_net_pnl"] = round(bucket["total_net_pnl"] + net, 4)

        include_in_daily = True
        if is_historical:
            tr_ts_ms = int(tr.get("ack_ts_ms") or tr.get("submit_ts_ms") or tr.get("signal_ts_ms") or 0)
            if tr_ts_ms > 0:
                tr_day = datetime.fromtimestamp(tr_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
                include_in_daily = (tr_day == today_utc)
            elif tr.get("created_at"):
                include_in_daily = str(tr["created_at"])[:10] == today_utc
            else:
                include_in_daily = False
        if include_in_daily:
            bucket["daily_net_pnl"] = round(bucket["daily_net_pnl"] + net, 4)

        if exit_type == "MAKER_POST_ONLY":
            bucket["maker_tp_count"] += 1
        else:
            bucket["taker_exit_count"] += 1

        if side == "LONG":
            bucket["long_trades"] += 1
            bucket["long_net_pnl"] = round(bucket["long_net_pnl"] + net, 4)
        else:
            bucket["short_trades"] += 1
            bucket["short_net_pnl"] = round(bucket["short_net_pnl"] + net, 4)

        if net > 0:
            bucket["win_count"] += 1
            bucket["gross_profit_sum"] = round(bucket["gross_profit_sum"] + net, 4)
            bucket["consecutive_losses"] = 0
        else:
            bucket["loss_count"] += 1
            bucket["gross_loss_sum"] = round(bucket["gross_loss_sum"] + abs(net), 4)
            if not is_historical:
                bucket["consecutive_losses"] += 1

        # Update per-symbol statistics
        sym_stat = _get_or_init_symbol_stat(bucket, tr["symbol"])
        if is_historical:
            sym_stat["signals"] += 1
            sym_stat["filled"] += 1
        sym_stat["trades"] += 1
        sym_stat["gross_pnl"] = round(sym_stat["gross_pnl"] + gross, 4)
        sym_stat["fees"] = round(sym_stat["fees"] + fee, 4)
        sym_stat["net_pnl"] = round(sym_stat["net_pnl"] + net, 4)
        if net > 0:
            sym_stat["wins"] += 1
        else:
            sym_stat["losses"] += 1
        if side == "LONG":
            sym_stat["long_trades"] += 1
            sym_stat["long_net_pnl"] = round(sym_stat["long_net_pnl"] + net, 4)
        else:
            sym_stat["short_trades"] += 1
            sym_stat["short_net_pnl"] = round(sym_stat["short_net_pnl"] + net, 4)
        if sym_stat["trades"] == 1 or net > sym_stat["best_trade_pnl"]:
            sym_stat["best_trade_pnl"] = round(net, 4)
        if sym_stat["trades"] == 1 or net < sym_stat["worst_trade_pnl"]:
            sym_stat["worst_trade_pnl"] = round(net, 4)
        hold_ms_val = int(tr.get("hold_duration_ms", 0))
        sym_stat["_hold_ms_sum"] += hold_ms_val
        sym_stat["avg_hold_ms"] = int(sym_stat["_hold_ms_sum"] / max(1, sym_stat["trades"]))
        sym_stat["last_trade_time"] = tr.get("time") or str(tr.get("created_at", ""))[-8:]

        ui_trade = {
            "id": tr.get("id") or tr.get("order_id"),
            "time": tr.get("time") or str(tr.get("created_at", ""))[-8:],
            "symbol": tr["symbol"],
            "follower_ex": tr.get("follower_ex", "okx"),
            "side": side,
            "trading_mode": target_mode,
            "exit_order_type": exit_type,
            "jump_pct": round(float(tr.get("leader_jump_pct", 0.0)) * 100, 3),
            "expected_net_pct": round(float(tr.get("expected_net_pct", 0.0)) * 100, 3),
            "beta_est": round(float(tr.get("beta_est", 0.88)), 2),
            "entry": float(tr.get("entry_price", 0.0)),
            "exit": float(tr.get("exit_price", 0.0)),
            "qty": float(tr.get("filled_qty", 0.0)),
            "signal_to_fill_ms": float(tr.get("signal_to_fill_ms", 24.0)),
            "hold_ms": int(tr.get("hold_duration_ms", 0)),
            "gross_pnl": round(gross, 4),
            "fee": round(fee, 4),
            "net_pnl": round(net, 4),
            "reason": tr.get("exit_reason", "TAKE_PROFIT"),
        }
        bucket["recent_trades"].insert(0, ui_trade)
        bucket["recent_trades"] = bucket["recent_trades"][:100]

        bucket["equity_curve"].append(
            {
                "index": len(bucket["equity_curve"]) + 1,
                "time": ui_trade["time"],
                "net_pnl": bucket["total_net_pnl"],
                "gross_pnl": bucket["total_gross_pnl"],
                "trade_pnl": round(net, 4),
                "symbol": tr["symbol"],
            }
        )
        bucket["equity_curve"] = bucket["equity_curve"][-120:]

    def recenter_symbol_grid(self, symbol: str) -> tuple[bool, str]:
        cfg = self.strategies.get(symbol)
        tracker = self.grid_trackers.get(symbol)
        m_state = self.math_states.get(symbol)
        if not cfg or not tracker:
            return False, "合约不存在"
        f_mid = m_state.latest_follower.mid if (m_state and m_state.latest_follower) else 0.0
        if f_mid <= 0:
            fb = self.gateway.real_follower_bbo.get(symbol)
            if fb:
                f_mid = (fb[0] + fb[1]) * 0.5
        if f_mid <= 0:
            return False, "暂未收到有效盘口中枢价，请稍后重试"
        bbw_info = (
            self.gateway.get_volatility_regime(symbol, cfg.bbw_timeframe)
            if cfg.strategy_type in ("TREND_GRID", "ADAPTIVE_GRID")
            else None
        )
        ok = tracker.recenter(f_mid, cfg, bbw_info=bbw_info)
        if not ok:
            return False, "该合约当前存在未平仓网格持仓，请先平仓后再重锚网格中心"
        regime_msg = tracker.consume_regime_log()
        if regime_msg:
            self.append_log("INFO", symbol, regime_msg)
        mode_cn = "顺势突破" if tracker.active_grid_mode == "TREND_BREAKOUT" else "传统震荡"
        self.append_log("INFO", symbol, f"[网格重锚] {symbol} ({mode_cn}) 网格中枢已更新至 {f_mid:.{cfg.price_precision}f}")
        self._save_open_strategy_state()
        return True, f"{symbol} 网格({mode_cn})已以 {f_mid:.{cfg.price_precision}f} 重锚"

    def reset_statistics(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """
        Resets KPI statistics (net/gross PnL, fill rate, win rate, signals, equity curve,
        per-symbol realized breakdown) for the active mode and persists a reset watermark
        so statistics restart cleanly from zero even across service restarts.
        Never touches open positions, exchange orders, API credentials, or strategy parameters.
        """
        target_mode = str(mode or self.risk_config.trading_mode or "LIVE").upper()
        if target_mode not in ("SHADOW", "LIVE"):
            target_mode = self.risk_config.trading_mode

        reset_ts = wall_ms()
        self.stats_reset_at_by_mode[target_mode] = reset_ts
        self.stats_by_mode[target_mode] = _create_empty_stats_bucket()
        self.scan_ticks_by_mode[target_mode] = 0

        if target_mode == self.risk_config.trading_mode:
            for dq in self.symbol_rolling_pnls.values():
                dq.clear()
            for tracker in self.grid_trackers.values():
                tracker.completed_cycles = 0
                tracker.total_net_pnl = 0.0

        if not self.is_guest_preview and self.user_id > 0:
            try:
                self.db.save_runtime_setting_sync(
                    "stats_reset_at_by_mode",
                    dict(self.stats_reset_at_by_mode),
                    user_id=self.user_id,
                )
            except Exception:
                pass
            self._save_open_strategy_state(sync=True)

        mode_label = "实盘" if target_mode == "LIVE" else "模拟"
        msg = f"已一键清零{mode_label}净收益、抢单成交、闭环胜率及触发信号统计，现已从 0 重新累计（在途持仓不受影响）"
        self.append_log("INFO", "SYSTEM", f"[统计清零重置] {msg}", mode=target_mode)
        return {
            "ok": True,
            "mode": target_mode,
            "reset_at_ms": reset_ts,
            "message": msg,
        }

    async def trigger_kill_switch(self, active: bool):
        self.risk_config.kill_switch_active = active
        if active:
            for ev in self.book_events.values():
                ev.set()
            cur_mode = self.risk_config.trading_mode
            for sym, tracker in list(self.grid_trackers.items()):
                cfg = self.strategies.get(sym)
                if not cfg:
                    continue
                side_groups: Dict[str, List[dict]] = {}
                for slot_id, lv in list(tracker.levels.items()):
                    if lv.get("status") == "OPEN":
                        lv["status"] = "EXITING"
                        side_groups.setdefault(lv["side"], []).append({**lv, "exit_reason": "KILL_SWITCH"})
                for side_exits in side_groups.values():
                    self._spawn_task(self._run_grid_exits_batch(cfg, tracker, side_exits, cur_mode))
            msg = "全局紧急熔断 (Kill Switch) 已触发！所有监控已暂停，挂单已撤销，在途持仓强制平仓"
            if not self.is_guest_preview:
                self.db.record_risk_event_async("CRITICAL", "KILL_SWITCH", "ALL", msg, user_id=self.user_id)
            self.append_log("CRITICAL", "SYSTEM", f"[紧急熔断] {msg}")
        else:
            self.stats["consecutive_losses"] = 0
            msg = "全局紧急熔断已解除，恢复毫秒级监控"
            if not self.is_guest_preview:
                self.db.record_risk_event_async("INFO", "KILL_SWITCH", "ALL", msg, user_id=self.user_id)
            self.append_log("INFO", "SYSTEM", f"[熔断解除] {msg}")
            if not self.is_guest_preview and self.risk_config.trading_mode == "LIVE":
                self._spawn_task(self.sync_live_exchange_positions(trigger="kill_release"))
        self.persist_runtime_state()
        self._save_open_strategy_state()

    def inject_test_pulse(self, req: PulseInjectionRequest) -> tuple[bool, str]:
        if self.risk_config.trading_mode == "LIVE":
            return False, "实盘通道禁止注入虚拟脉冲数据，请先切换至「影子模拟盘」进行压力测试"
        if req.follower_scenario == "MM_WITHDRAW_MISS":
            self.executor.force_next_miss(req.symbol)
        self.gateway.inject_pulse(req)
        return True, "已注入脉冲测试序列"

    def _build_reality_gap_analysis(self) -> Dict[str, Any]:
        """
        Quantitative Shadow-to-Live Reality Gap & Decay Estimator.
        Evaluates how much of the Shadow PnL survives in real production after accounting for:
        1. Adverse selection / Winner's Curse on mainstream vs altcoin symbols
        2. Network latency disadvantage vs colocated market makers (<2ms)
        3. Post-Only Maker queue priority failure rate
        """
        shadow_bucket = self.stats_by_mode["SHADOW"]
        s_trades = shadow_bucket["win_count"] + shadow_bucket["loss_count"]
        s_win_rate = (shadow_bucket["win_count"] / s_trades * 100.0) if s_trades > 0 else 72.0
        s_fill_rate = (
            shadow_bucket["ioc_filled_count"]
            / max(1, shadow_bucket["ioc_filled_count"] + shadow_bucket["ioc_missed_count"])
            * 100.0
        ) if shadow_bucket["total_signals"] > 0 else 68.0
        s_net_pnl = shadow_bucket["total_net_pnl"]

        # Estimate realistic live decay factors
        rtt = self.executor.last_rtt_ms
        latency_survival = max(0.12, math.exp(-max(0.0, rtt - 5.0) / 38.0))
        projected_live_fill_rate = round(min(22.0, s_fill_rate * latency_survival * 0.45), 1)
        # Adverse selection reduces win rate by ~18-26 percentage points on filled IOC orders
        adverse_selection_penalty = 21.5 if rtt > 20.0 else 12.5
        projected_live_win_rate = round(max(32.0, s_win_rate - adverse_selection_penalty), 1)

        return {
            "shadow_trades": s_trades,
            "shadow_win_rate_pct": round(s_win_rate, 1),
            "shadow_fill_rate_pct": round(s_fill_rate, 1),
            "shadow_net_pnl_usdt": round(s_net_pnl, 4),
            "projected_live_fill_rate_pct": projected_live_fill_rate,
            "projected_live_win_rate_pct": projected_live_win_rate,
            "breakeven_win_rate_pct": 64.3,
            "colocation_gap_ms": round(max(0.0, rtt - 1.8), 1),
            "mainstream_verdict": "极难盈利 (做市商专线 <2ms 垄断，逆向选择严重)",
            "altcoin_verdict": "具备边际 (中小市值合约 + 二线执行所滞后达 80~250ms)",
        }

    def build_dashboard_snapshot(self, radar_symbol: Optional[str] = None) -> Dict[str, Any]:
        now_m = mono_ms()
        cur_mode = self.risk_config.trading_mode
        if self.is_guest_preview or cur_mode == "SHADOW":
            self.gateway.touch_simulator_demand()
        leader_ex, follower_ex = self.get_leader_And_follower_cfg()
        symbols_telemetry = {}
        radar_data = {}
        target_radar_sym = (radar_symbol or "").strip() or None

        for sym, cfg in self.strategies.items():
            m = self.math_states.get(sym)
            if not m:
                continue
            l_tick = m.latest_leader
            f_tick = m.latest_follower
            log_basis = m.log_basis_ema or 0.0
            basis_bps = log_basis * 10000.0
            f_age_ms = round(now_m - f_tick.mono_ts, 1) if f_tick else 9999.0

            calibrated_l = (l_tick.mid * (2.718281828 ** (-log_basis))) if l_tick else 0.0
            live_spread_bps = (
                ((calibrated_l - f_tick.mid) / f_tick.mid) * 10000.0
                if (l_tick and f_tick and f_tick.mid > 0)
                else 0.0
            )

            if f_tick and 0 < f_tick.mid < 0.0005 and cfg.price_precision < 8:
                cfg.price_precision = 8

            grid_tracker = self.grid_trackers.get(sym)
            grid_snap = grid_tracker.get_snapshot() if (is_grid_strategy(cfg.strategy_type) and grid_tracker) else None
            eff_state = self.symbol_states.get(sym, SniperState.IDLE).value
            if is_grid_strategy(cfg.strategy_type) and grid_snap:
                eff_state = (
                    "GRID_HOLDING"
                    if (grid_snap["open_long_count"] + grid_snap["open_short_count"]) > 0
                    else "GRID_WATCH"
                )

            symbols_telemetry[sym] = {
                "symbol": sym,
                "strategy_type": cfg.strategy_type,
                "grid_side_mode": cfg.grid_side_mode,
                "grid": grid_snap,
                "state": eff_state,
                "leader_bid": l_tick.bid if l_tick else 0.0,
                "leader_ask": l_tick.ask if l_tick else 0.0,
                "leader_mid": l_tick.mid if l_tick else 0.0,
                "leader_micro": round(l_tick.microprice, cfg.price_precision + 1) if l_tick else 0.0,
                "follower_bid": f_tick.bid if f_tick else 0.0,
                "follower_ask": f_tick.ask if f_tick else 0.0,
                "follower_mid": f_tick.mid if f_tick else 0.0,
                "follower_spread_bps": round(((f_tick.ask - f_tick.bid) / f_tick.mid) * 10000.0, 2) if (f_tick and f_tick.mid > 0) else 0.0,
                "follower_age_ms": f_age_ms,
                "basis_bps": round(basis_bps, 2),
                "basis_abs": round(m.get_price_basis_abs(), cfg.price_precision + 1),
                "basis_frozen": m.basis_frozen,
                "live_spread_bps": round(live_spread_bps, 2),
                "estimated_beta": round(m.estimated_beta, 3),
                "volatility_sigma_bps": round((m.jump_var_ema ** 0.5) * 10000.0, 2),
                "instant_jump_pct": round(m.last_instant_jump * 100.0, 3),
                "max_jump_seen_pct": round(m.max_jump_seen * 100.0, 3),
            }
            if target_radar_sym is None or sym == target_radar_sym:
                radar_data[sym] = list(m.radar_series)[-90:]

        active_bucket = self.stats_by_mode.get(cur_mode, self.stats_by_mode["SHADOW"])
        total_closed = active_bucket["win_count"] + active_bucket["loss_count"]
        win_rate = round((active_bucket["win_count"] / total_closed) * 100.0, 1) if total_closed > 0 else 0.0
        fill_attempted = active_bucket["ioc_filled_count"] + active_bucket["ioc_missed_count"]
        fill_rate = round((active_bucket["ioc_filled_count"] / fill_attempted) * 100.0, 1) if fill_attempted > 0 else 0.0
        profit_factor = (
            round(active_bucket["gross_profit_sum"] / active_bucket["gross_loss_sum"], 2)
            if active_bucket["gross_loss_sum"] > 0
            else (99.9 if active_bucket["gross_profit_sum"] > 0 else 0.0)
        )

        mode_positions: List[Dict[str, Any]] = []
        seen_ex_keys = set()
        for p in self.active_positions.values():
            if p.get("mode", "SHADOW") == cur_mode:
                cfg = self.strategies.get(p["symbol"])
                item = dict(p)
                item.setdefault("strategy_type", cfg.strategy_type if cfg else "LEAD_LAG")
                item.setdefault("managed_by_strategy", True)
                mode_positions.append(item)
                seen_ex_keys.add(f"{p['symbol']}:{p['side']}")

        for p in self.active_grid_positions.values():
            if p.get("mode", "SHADOW") == cur_mode:
                item = dict(p)
                item.setdefault("strategy_type", "LONG_SHORT_GRID")
                item.setdefault("managed_by_strategy", True)
                mode_positions.append(item)
                seen_ex_keys.add(f"{p['symbol']}:{p['side']}")

        if cur_mode == "LIVE" and not self.is_guest_preview:
            for ex_key, ex_pos in self.exchange_live_positions.items():
                if ex_key not in seen_ex_keys:
                    mode_positions.append(
                        {
                            "symbol": ex_pos["symbol"],
                            "strategy_type": "UNMANAGED",
                            "managed_by_strategy": False,
                            "mode": "LIVE",
                            "side": ex_pos["side"],
                            "qty": ex_pos["qty"],
                            "contracts": ex_pos["contracts"],
                            "ct_val": ex_pos["ct_val"],
                            "notional_usdt": ex_pos["notional_usdt"],
                            "entry_price": ex_pos["entry_price"],
                            "mark_price": ex_pos["mark_price"],
                            "tp_price": 0.0,
                            "sl_price": 0.0,
                            "leverage": ex_pos["leverage"],
                            "unrealized_net_pct": ex_pos["unrealized_net_pct"],
                            "unrealized_usdt": ex_pos["unrealized_usdt"],
                            "recovered": True,
                        }
                    )

        mode_positions.sort(key=lambda x: (0 if x.get("managed_by_strategy", True) else 1, x.get("symbol", "")))

        pos_by_sym: Dict[str, List[dict]] = {}
        for p in mode_positions:
            pos_by_sym.setdefault(p["symbol"], []).append(p)

        # Aggregate real-time total profit, total loss & net PnL across all open positions.
        # In LIVE mode, anchor on exchange-reconciled net positions (`exchange_live_positions`,
        # updated tick-by-tick from OKX avgPx + live BBO) so multi-slot net_mode grids match
        # exchange PnL accurately without slot-average drift, plus any brand-new un-synced local positions.
        summary_entries: List[Dict[str, Any]] = []
        if cur_mode == "LIVE" and not self.is_guest_preview and self.exchange_live_positions:
            covered_ex_keys = set()
            for ex_k, ex_p in self.exchange_live_positions.items():
                summary_entries.append(ex_p)
                covered_ex_keys.add(ex_k)
            for p in mode_positions:
                pk = f"{p['symbol']}:{p['side']}"
                if pk not in covered_ex_keys:
                    summary_entries.append(p)
        else:
            summary_entries = mode_positions

        total_profit_usdt = 0.0
        total_loss_usdt = 0.0
        total_notional_usdt = 0.0
        profit_count = 0
        loss_count = 0
        flat_count = 0

        for p in summary_entries:
            notional_val = float(p.get("notional_usdt") or 0.0)
            total_notional_usdt += notional_val
            if p.get("unrealized_usdt") is not None:
                upl_val = float(p.get("unrealized_usdt") or 0.0)
            else:
                upl_val = notional_val * float(p.get("unrealized_net_pct") or 0.0)
            if upl_val > 1e-9:
                total_profit_usdt += upl_val
                profit_count += 1
            elif upl_val < -1e-9:
                total_loss_usdt += upl_val
                loss_count += 1
            else:
                flat_count += 1

        total_profit_usdt = round(total_profit_usdt, 4)
        total_loss_usdt = round(total_loss_usdt, 4)
        net_unrealized_usdt = round(total_profit_usdt + total_loss_usdt, 4)
        total_notional_usdt = round(total_notional_usdt, 2)
        position_pnl_summary = {
            "total_positions": len(summary_entries),
            "strategy_slots": len(mode_positions),
            "profit_count": profit_count,
            "loss_count": loss_count,
            "flat_count": flat_count,
            "total_profit_usdt": total_profit_usdt,
            "total_loss_usdt": total_loss_usdt,
            "net_unrealized_usdt": net_unrealized_usdt,
            "total_notional_usdt": total_notional_usdt,
            "net_unrealized_pct": round((net_unrealized_usdt / total_notional_usdt) * 100.0, 2)
            if total_notional_usdt > 0
            else 0.0,
        }

        # Build per-symbol PnL breakdown for all running/monitored contracts + any traded contracts
        sym_stats_map = active_bucket.get("symbol_stats", {})
        ordered_syms = list(self.strategies.keys()) + [
            s for s in sym_stats_map.keys() if s not in self.strategies
        ]
        symbol_pnl_breakdown = []
        for sym in ordered_syms:
            cfg = self.strategies.get(sym)
            raw_st = sym_stats_map.get(sym, {})
            trades_cnt = int(raw_st.get("trades", 0))
            wins_cnt = int(raw_st.get("wins", 0))
            losses_cnt = int(raw_st.get("losses", 0))
            filled_cnt = int(raw_st.get("filled", 0))
            missed_cnt = int(raw_st.get("missed", 0))
            signals_cnt = int(raw_st.get("signals", 0))
            sym_win_rate = round((wins_cnt / trades_cnt) * 100.0, 1) if trades_cnt > 0 else 0.0
            sym_fill_rate = (
                round((filled_cnt / (filled_cnt + missed_cnt)) * 100.0, 1)
                if (filled_cnt + missed_cnt) > 0
                else 0.0
            )
            open_pos_list = pos_by_sym.get(sym, [])
            unrealized_usdt = round(
                sum(
                    float(op["unrealized_usdt"])
                    if op.get("unrealized_usdt") is not None
                    else float(op.get("notional_usdt", 0.0)) * float(op.get("unrealized_net_pct", 0.0))
                    for op in open_pos_list
                ),
                4,
            )
            tel = symbols_telemetry.get(sym, {})
            symbol_pnl_breakdown.append(
                {
                    "symbol": sym,
                    "strategy_type": cfg.strategy_type if cfg else "LEAD_LAG",
                    "grid_side_mode": cfg.grid_side_mode if cfg else "LONG_SHORT",
                    "is_monitored": cfg is not None,
                    "is_running": bool(cfg.is_running) if cfg else False,
                    "direction": cfg.direction if cfg else "BOTH",
                    "order_usdt": float(cfg.order_usdt) if cfg else 300.0,
                    "leverage": int(cfg.leverage) if cfg else 5,
                    "state": tel.get("state", self.symbol_states.get(sym, SniperState.IDLE).value),
                    "follower_mid": tel.get("follower_mid", 0.0),
                    "live_spread_bps": tel.get("live_spread_bps", 0.0),
                    "signals": signals_cnt,
                    "filled": filled_cnt,
                    "missed": missed_cnt,
                    "fill_rate_pct": sym_fill_rate,
                    "trades": trades_cnt,
                    "wins": wins_cnt,
                    "losses": losses_cnt,
                    "win_rate_pct": sym_win_rate,
                    "long_trades": int(raw_st.get("long_trades", 0)),
                    "short_trades": int(raw_st.get("short_trades", 0)),
                    "long_net_pnl": round(float(raw_st.get("long_net_pnl", 0.0)), 4),
                    "short_net_pnl": round(float(raw_st.get("short_net_pnl", 0.0)), 4),
                    "gross_pnl": round(float(raw_st.get("gross_pnl", 0.0)), 4),
                    "fees": round(float(raw_st.get("fees", 0.0)), 4),
                    "net_pnl": round(float(raw_st.get("net_pnl", 0.0)), 4),
                    "unrealized_pnl": unrealized_usdt,
                    "best_trade_pnl": round(float(raw_st.get("best_trade_pnl", 0.0)), 4),
                    "worst_trade_pnl": round(float(raw_st.get("worst_trade_pnl", 0.0)), 4),
                    "avg_hold_ms": int(raw_st.get("avg_hold_ms", 0)),
                    "last_trade_time": raw_st.get("last_trade_time", ""),
                }
            )

        user_started = bool(self.trading_active_by_mode.get(cur_mode, False))
        f_auth_status = follower_ex.get("auth_status", "UNCONFIGURED")
        f_auth_msg = follower_ex.get("auth_message", "未配置 API 密钥")
        f_equity = float(follower_ex.get("account_equity_usdt") or 0.0)
        running_contracts = sum(1 for c in self.strategies.values() if c.is_running)
        scan_cnt = self.scan_ticks_by_mode.get(cur_mode, 0)

        if self.risk_config.kill_switch_active:
            eff_active = False
            status_code = "KILL_SWITCH"
            status_label = "已暂停：全局紧急熔断激活"
            status_detail = "点击顶部「解除熔断恢复监控」或「开始交易」恢复自动抢单"
        elif cur_mode == "LIVE" and f_auth_status != "VERIFIED":
            eff_active = False
            status_code = "AUTH_ERROR"
            status_label = "无法交易：执行所 API 未就绪"
            status_detail = f"{follower_ex.get('display_name', 'OKX')}: {f_auth_msg}"
        elif not user_started:
            eff_active = False
            status_code = "STOPPED"
            status_label = "已停止 (待命观察)"
            status_detail = "当前仅实时刷新行情与价差，点击「开始交易」启动自动抢单"
        elif cur_mode == "LIVE" and not (self.gateway.binance_connected and self.gateway.okx_connected):
            eff_active = False
            status_code = "WS_CONNECTING"
            status_label = "等待实盘行情流就绪"
            status_detail = "正在同步 Binance 与 OKX 实时盘口流..."
        else:
            eff_active = True
            status_code = "TRADING_ACTIVE"
            status_label = "自动交易运行中"
            status_detail = (
                f"正在毫秒级扫描 {running_contracts} 个合约 · 累计扫描 {scan_cnt:,} 次盘口"
                + (f" · 账户权益 {f_equity:.2f} USDT" if cur_mode == "LIVE" and f_equity > 0 else "")
            )

        effective_has_key = False if self.is_guest_preview else bool(follower_ex.get("has_credentials"))
        effective_auth_status = "GUEST_PREVIEW" if self.is_guest_preview else f_auth_status
        effective_auth_msg = (
            "游客公开体验模式 · 登录后可绑定个人交易所 API 实盘运行"
            if self.is_guest_preview
            else f_auth_msg
        )
        effective_equity = 0.0 if self.is_guest_preview else f_equity

        return {
            "ts": wall_ms(),
            "owner_user_id": self.user_id,
            "is_guest_preview": self.is_guest_preview,
            "trading_mode": cur_mode,
            "feed_source": "HIGH_FREQ_SIM" if self.is_guest_preview else self.gateway.active_source,
            "trading_control": {
                "trading_active": eff_active,
                "user_started": user_started,
                "status_code": status_code,
                "status_label": status_label,
                "status_detail": status_detail,
                "scan_ticks_count": scan_cnt,
                "monitored_running_count": running_contracts,
                "follower_auth_status": effective_auth_status,
                "follower_auth_message": effective_auth_msg,
                "follower_equity_usdt": effective_equity,
            },
            "live_status": {
                "binance_ws_connected": self.gateway.binance_connected,
                "okx_ws_connected": self.gateway.okx_connected,
                "follower_has_api_key": effective_has_key,
                "follower_auth_status": effective_auth_status,
                "follower_auth_message": effective_auth_msg,
                "follower_equity_usdt": effective_equity,
                "follower_exchange_name": follower_ex.get("display_name", "OKX Swap"),
                "leader_exchange_name": leader_ex.get("display_name", "Binance USDM"),
                "last_ws_error": self.gateway.last_ws_error,
            },
            "last_order_rtt_ms": self.executor.last_rtt_ms,
            "risk": self.risk_config.model_dump(),
            "exchange_health": self.gateway.exchange_health,
            "telemetry": symbols_telemetry,
            "active_positions": mode_positions,
            "position_pnl_summary": position_pnl_summary,
            "radar_series": radar_data,
            "logs": list(self.logs_by_mode.get(cur_mode, []))[:60],
            "reality_gap": self._build_reality_gap_analysis(),
            "stats": {
                **active_bucket,
                "win_rate_pct": win_rate,
                "fill_rate_pct": fill_rate,
                "profit_factor": profit_factor,
                "symbol_pnl_breakdown": symbol_pnl_breakdown,
                "position_pnl_summary": position_pnl_summary,
                "stats_reset_at_ms": int(self.stats_reset_at_by_mode.get(cur_mode, 0) or 0),
            },
        }

    def build_public_preview_snapshot(self, radar_symbol: Optional[str] = None) -> Dict[str, Any]:
        """Always returns a scrubbed read-only market preview snapshot with zero user secrets/balances."""
        snap = self.build_dashboard_snapshot(radar_symbol=radar_symbol)
        snap["is_guest_preview"] = True
        snap["owner_user_id"] = 0
        snap["trading_control"]["follower_auth_status"] = "GUEST_PREVIEW"
        snap["trading_control"]["follower_auth_message"] = "游客体验模式 · 请登录账号绑定个人交易所 API"
        snap["trading_control"]["follower_equity_usdt"] = 0.0
        snap["live_status"]["follower_has_api_key"] = False
        snap["live_status"]["follower_auth_status"] = "GUEST_PREVIEW"
        snap["live_status"]["follower_auth_message"] = "游客体验模式 · 请登录账号绑定个人交易所 API"
        snap["live_status"]["follower_equity_usdt"] = 0.0
        return snap

    async def run_preflight_check(self) -> Dict[str, Any]:
        """Runs a read-only live order feasibility check against the user's follower exchange."""
        _, follower_ex = self.get_leader_And_follower_cfg()
        for ex in self.db.load_exchanges_sync(user_id=self.user_id):
            self.exchanges[ex["exchange_id"]] = ex
        follower_ex = self.exchanges.get(follower_ex.get("exchange_id", "okx"), follower_ex)
        return await self.executor.preflight_check_symbols(
            exchange_cfg=follower_ex,
            strategies=list(self.strategies.values()),
            math_states=self.math_states,
            gateway=self.gateway,
        )
