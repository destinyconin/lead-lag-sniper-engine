"""
Mathematical & Signal Operator Engine for Lead-Lag Bidirectional Sniper.
Fixes all defects identified in Section 1.2 (#5 - #12) and implements Section 3.1 & 3.2:
- Fix #5: Time-decayed EMA basis: alpha = 1 - exp(-dt / tau), tau = 30s
- Fix #6: Proportional log-basis ln(L_mid / F_mid) instead of absolute price difference
- Fix #7: Basis freeze when |jump| >= 0.5 * min_jump_pct or position is locked
- Fix #8: Follower book staleness check (now_mono - f_mono <= max_book_staleness_ms)
- Fix #9: Binary search (bisect_right) for the last tick at or before (now - window_ms)
- Fix #10: Explicit Follower bid-ask spread deduction in expected profit model
- Fix #11: Partial convergence factor beta (rolling OLS estimation + configurable override)
- Fix #12: Microprice & active trade flow confirmation to filter thin-book quote flicker
- Section 3.2: Reservation Price (保留价) and Depth-Aware Order Sizing
"""
import bisect
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Tuple
from backend.models import SymbolStrategy


def mono_ms() -> float:
    """High-resolution monotonic clock in milliseconds (Fixes Section 1.3 time.time() defect)."""
    return time.perf_counter_ns() / 1_000_000.0


def wall_ms() -> int:
    """Wall-clock timestamp in milliseconds for logs and UI alignment."""
    return int(time.time() * 1000)


@dataclass
class TickSample:
    mono_ts: float
    wall_ts: int
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    mid: float
    microprice: float


@dataclass
class TradeFlowSample:
    mono_ts: float
    side: str       # "buy" or "sell" (aggressor side)
    price: float
    qty: float
    notional: float


@dataclass
class SignalCandidate:
    symbol: str
    side: str                       # "LONG" or "SHORT"
    signal_mono_ms: float
    signal_wall_ms: int
    leader_jump_pct: float          # R_Leader
    micro_jump_pct: float           # Microprice jump ratio
    z_score: float                  # Jump relative to rolling realized volatility
    follower_lag_pct: float         # R_Follower in window_ms
    proportional_basis: float       # ln(L_mid / F_mid) EMA_30s
    calibrated_target_price: float  # Expected Follower price after beta convergence
    raw_spread_pct: float           # Spread before fees (including beta & Follower spread)
    follower_spread_pct: float      # Current Follower (ask - bid) / mid
    expected_cost_pct: float        # Fees + slippage buffer
    expected_net_pct: float         # Expected net profit ratio
    reservation_price: float        # Worst price that still guarantees target net profit
    trigger_price: float            # Current Follower Ask (for LONG) or Bid (for SHORT)
    recommended_qty: float          # Depth-aware and precision-rounded quantity
    beta_used: float
    active_vol_usdt: float
    leader_mid: float
    follower_mid: float


class SymbolMathState:
    """Per-symbol rolling mathematical state."""

    def __init__(self, symbol: str, tau_seconds: float = 30.0):
        self.symbol = symbol
        self.tau_ms = tau_seconds * 1000.0

        # Parallel monotonic timestamp list + tick buffer for O(log N) bisect lookup (Fix #9)
        self.leader_ts: list[float] = []
        self.leader_ticks: list[TickSample] = []

        self.follower_ts: list[float] = []
        self.follower_ticks: list[TickSample] = []

        # Active trade flow ring buffer (for Fix #12 volume confirmation)
        self.leader_trades: deque[TradeFlowSample] = deque(maxlen=600)

        # Latest BBO snapshot
        self.latest_leader: Optional[TickSample] = None
        self.latest_follower: Optional[TickSample] = None

        # Time-decayed proportional log-basis ln(L_mid / F_mid) (Fixes #5, #6, #7)
        self.log_basis_ema: Optional[float] = None
        self.last_basis_update_mono: float = 0.0
        self.basis_frozen: bool = False

        # Rolling realized volatility of window jumps (for Section 3.1 adaptive Z-score)
        self.jump_var_ema: float = (0.0012) ** 2  # Initial sigma ~ 0.12%

        # Rolling OLS Beta & Lead-Lag Information Share estimator (Fix #11 & Section 3.1)
        self.cov_lf_ema: float = 0.0000088
        self.var_l_ema: float = 0.0000100
        self.estimated_beta: float = 0.88
        self.leader_info_share: float = 0.78  # > 0.5 means Leader leads Follower
        self._pending_leader_jump: Optional[Tuple[float, float, float]] = None  # (mono_ts, l_jump, f_mid_at_signal)

        # Chart history for dual-exchange millisecond alignment radar (last 180 points)
        self.radar_series: deque[dict] = deque(maxlen=180)
        self._last_radar_sample_ms: float = 0.0

    def reset_ticks(self):
        """Clears all buffered ticks and chart series when switching between Simulator (SHADOW) and Real Exchange (LIVE)."""
        self.leader_ts.clear()
        self.leader_ticks.clear()
        self.follower_ts.clear()
        self.follower_ticks.clear()
        self.leader_trades.clear()
        self.latest_leader = None
        self.latest_follower = None
        self.log_basis_ema = None
        self.last_basis_update_mono = 0.0
        self.basis_frozen = False
        self._pending_leader_jump = None
        self.radar_series.clear()
        self._last_radar_sample_ms = 0.0

    @staticmethod
    def compute_microprice(bid: float, ask: float, bid_qty: float, ask_qty: float) -> float:
        total_qty = bid_qty + ask_qty
        if total_qty <= 0:
            return (bid + ask) * 0.5
        return (bid_qty * ask + ask_qty * bid) / total_qty

    def _trim_history(self, ts_list: list[float], tick_list: list[TickSample], max_len: int = 2000):
        if len(ts_list) > max_len:
            del ts_list[: len(ts_list) - max_len]
            del tick_list[: len(tick_list) - max_len]

    def on_follower_tick(self, bid: float, ask: float, bid_qty: float = 10.0, ask_qty: float = 10.0, now_mono: Optional[float] = None) -> TickSample:
        if now_mono is None:
            now_mono = mono_ms()
        now_wall = wall_ms()
        mid = (bid + ask) * 0.5
        micro = self.compute_microprice(bid, ask, bid_qty, ask_qty)
        sample = TickSample(now_mono, now_wall, bid, ask, bid_qty, ask_qty, mid, micro)
        self.latest_follower = sample

        # Ensure strictly non-decreasing timestamps for bisect
        if self.follower_ts and now_mono < self.follower_ts[-1]:
            now_mono = self.follower_ts[-1]
            sample.mono_ts = now_mono

        self.follower_ts.append(now_mono)
        self.follower_ticks.append(sample)
        self._trim_history(self.follower_ts, self.follower_ticks)

        # Check pending Leader jump to update rolling Beta convergence estimator (Fix #11)
        if self._pending_leader_jump is not None:
            sig_ts, l_ret, f_mid_0 = self._pending_leader_jump
            if now_mono - sig_ts >= 350.0:  # Measure Follower convergence 350ms after Leader jump
                if f_mid_0 > 0 and abs(l_ret) > 1e-5:
                    f_ret = (mid - f_mid_0) / f_mid_0
                    self.cov_lf_ema = 0.96 * self.cov_lf_ema + 0.04 * (l_ret * f_ret)
                    self.var_l_ema = 0.96 * self.var_l_ema + 0.04 * (l_ret * l_ret)
                    if self.var_l_ema > 1e-10:
                        raw_beta = self.cov_lf_ema / self.var_l_ema
                        self.estimated_beta = max(0.45, min(1.15, raw_beta))
                self._pending_leader_jump = None

        return sample

    def on_leader_trade(self, side: str, price: float, qty: float, now_mono: Optional[float] = None):
        if now_mono is None:
            now_mono = mono_ms()
        self.leader_trades.append(TradeFlowSample(now_mono, side, price, qty, price * qty))

    def on_leader_tick(
        self,
        bid: float,
        ask: float,
        bid_qty: float,
        ask_qty: float,
        cfg: SymbolStrategy,
        is_locked: bool,
        now_mono: Optional[float] = None,
    ) -> Tuple[TickSample, float]:
        if now_mono is None:
            now_mono = mono_ms()
        now_wall = wall_ms()
        mid = (bid + ask) * 0.5
        micro = self.compute_microprice(bid, ask, bid_qty, ask_qty)
        sample = TickSample(now_mono, now_wall, bid, ask, bid_qty, ask_qty, mid, micro)
        self.latest_leader = sample

        if self.leader_ts and now_mono < self.leader_ts[-1]:
            now_mono = self.leader_ts[-1]
            sample.mono_ts = now_mono

        self.leader_ts.append(now_mono)
        self.leader_ticks.append(sample)
        self._trim_history(self.leader_ts, self.leader_ticks)

        # Compute instantaneous jump over cfg.window_ms using bisect_right (Fix #9)
        past_leader = self.get_tick_at_or_before(self.leader_ts, self.leader_ticks, now_mono - cfg.window_ms)
        instant_jump = 0.0
        if past_leader and past_leader.mid > 0:
            instant_jump = (mid - past_leader.mid) / past_leader.mid
            # Update rolling jump variance (clamped so extreme outliers don't blow up sigma)
            clamped_jump = max(-0.015, min(0.015, instant_jump))
            self.jump_var_ema = 0.995 * self.jump_var_ema + 0.005 * (clamped_jump ** 2)

        # Update time-decayed proportional log-basis (Fixes #5, #6, #7)
        f_tick = self.latest_follower
        if f_tick and f_tick.mid > 0 and mid > 0:
            # Fix #7: Freeze basis if position is locked OR |jump| >= 0.5 * min_jump_pct
            should_freeze = is_locked or (abs(instant_jump) >= 0.5 * cfg.min_jump_pct)
            self.basis_frozen = should_freeze
            current_log_basis = math.log(mid / f_tick.mid)

            if self.log_basis_ema is None:
                self.log_basis_ema = current_log_basis
                self.last_basis_update_mono = now_mono
            elif not should_freeze:
                dt_ms = max(0.0, now_mono - self.last_basis_update_mono)
                alpha = 1.0 - math.exp(-dt_ms / self.tau_ms)
                self.log_basis_ema = (1.0 - alpha) * self.log_basis_ema + alpha * current_log_basis
                self.last_basis_update_mono = now_mono
            else:
                self.last_basis_update_mono = now_mono

            # Record radar alignment sample every ~40ms
            if now_mono - self._last_radar_sample_ms >= 40.0:
                self._last_radar_sample_ms = now_mono
                calibrated_leader = mid * math.exp(-(self.log_basis_ema or 0.0))
                spread_bps = ((calibrated_leader - f_tick.mid) / f_tick.mid) * 10000.0
                self.radar_series.append(
                    {
                        "ts": now_wall,
                        "leader_mid": round(mid, cfg.price_precision + 1),
                        "leader_calibrated": round(calibrated_leader, cfg.price_precision + 1),
                        "follower_mid": round(f_tick.mid, cfg.price_precision + 1),
                        "follower_bid": round(f_tick.bid, cfg.price_precision + 1),
                        "follower_ask": round(f_tick.ask, cfg.price_precision + 1),
                        "spread_bps": round(spread_bps, 2),
                        "jump_bps": round(instant_jump * 10000.0, 2),
                    }
                )

            # Register notable jump for beta convergence tracking
            if abs(instant_jump) >= cfg.min_jump_pct * 0.6 and self._pending_leader_jump is None:
                self._pending_leader_jump = (now_mono, instant_jump, f_tick.mid)

        return sample, instant_jump

    @staticmethod
    def get_tick_at_or_before(
        ts_list: list[float], tick_list: list[TickSample], target_mono_ms: float
    ) -> Optional[TickSample]:
        """
        Fix #9: Use binary search (`bisect_right`) to find the last tick with timestamp <= target_mono_ms.
        During initial warmup (< window_ms after startup), falls back to the earliest recorded tick if >= 2 ticks exist.
        """
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, target_mono_ms) - 1
        if idx >= 0:
            return tick_list[idx]
        if len(tick_list) >= 2:
            return tick_list[0]
        return None

    def get_window_trade_flow(self, now_mono: float, window_ms: float) -> Tuple[float, float]:
        """Returns (buy_notional_usdt, sell_notional_usdt) within [now_mono - window_ms, now_mono]."""
        cutoff = now_mono - window_ms
        buy_usdt = 0.0
        sell_usdt = 0.0
        for tr in reversed(self.leader_trades):
            if tr.mono_ts < cutoff:
                break
            if tr.side == "buy":
                buy_usdt += tr.notional
            else:
                sell_usdt += tr.notional
        return buy_usdt, sell_usdt

    def get_price_basis_abs(self) -> float:
        """Returns approximate price-unit basis (L_mid - F_mid) for display."""
        if self.log_basis_ema is None or self.latest_leader is None:
            return 0.0
        return self.latest_leader.mid * (1.0 - math.exp(-self.log_basis_ema))

    def evaluate_signal(
        self,
        cfg: SymbolStrategy,
        taker_fee: float,
        maker_fee: float,
        now_mono: Optional[float] = None,
    ) -> Optional[SignalCandidate]:
        """
        Evaluates bidirectional Lead-Lag signal with all mathematical fixes from Section 1.2 & 3.1/3.2.
        """
        if now_mono is None:
            now_mono = mono_ms()

        l_curr = self.latest_leader
        f_curr = self.latest_follower
        if not l_curr or not f_curr or self.log_basis_ema is None:
            return None

        # Fix #8: Follower book staleness check
        follower_age_ms = now_mono - f_curr.mono_ts
        if follower_age_ms > cfg.max_book_staleness_ms:
            return None

        if f_curr.bid <= 0 or f_curr.ask <= 0 or f_curr.ask < f_curr.bid:
            return None

        # Fix #9: Exact bisect window sampling for Leader and Follower
        l_past = self.get_tick_at_or_before(self.leader_ts, self.leader_ticks, now_mono - cfg.window_ms)
        if not l_past or l_past.mid <= 0:
            return None

        f_past = self.get_tick_at_or_before(self.follower_ts, self.follower_ticks, now_mono - cfg.window_ms)
        f_past_mid = f_past.mid if f_past and f_past.mid > 0 else f_curr.mid

        # Directional Leader jumps as defined in Section 二.2:
        # Long jump: (P_Leader_Bid(t) - P_Leader_Mid(t - dt)) / P_Leader_Mid(t - dt)
        # Short jump: (P_Leader_Mid(t - dt) - P_Leader_Ask(t)) / P_Leader_Mid(t - dt)
        long_jump_pct = (l_curr.bid - l_past.mid) / l_past.mid
        short_jump_pct = (l_past.mid - l_curr.ask) / l_past.mid
        micro_jump_pct = (l_curr.microprice - l_past.microprice) / l_past.microprice

        # Follower change in window_ms (Section 二.2 滞后确认)
        follower_change_pct = (f_curr.mid - f_past_mid) / f_past_mid

        # Realized volatility Z-Score (Section 3.1 自适应阈值)
        sigma = max(0.0005, math.sqrt(self.jump_var_ema))

        # Active volume confirmation (Fix #12 & Section 3.1)
        buy_vol_usdt, sell_vol_usdt = self.get_window_trade_flow(now_mono, cfg.window_ms)

        # Effective convergence coefficient beta (Fix #11)
        beta = self.estimated_beta if cfg.auto_beta_enabled else cfg.beta_coeff

        # Follower spread ratio (Fix #10: explicit bid-ask spread deduction)
        follower_spread_pct = (f_curr.ask - f_curr.bid) / f_curr.mid

        # Total fee + slippage cost model (Section 二.3 & Section 3.2)
        exit_fee = maker_fee if cfg.use_post_only_tp else taker_fee
        fee_cost_pct = taker_fee + exit_fee
        total_cost_pct = fee_cost_pct + cfg.slippage_pct

        basis_factor = math.exp(-self.log_basis_ema)

        # ==================== 1. LONG SNIPE EVALUATION ====================
        if cfg.direction in ("BOTH", "LONG_ONLY") and long_jump_pct >= cfg.min_jump_pct:
            z_score = long_jump_pct / sigma
            if z_score < cfg.adaptive_z_score:
                return None

            # Follower lag confirmation: Follower has not already jumped >= lag_confirm_max_pct
            if follower_change_pct >= cfg.lag_confirm_max_pct:
                return None

            # Fix #12: Microprice and active trade flow confirmation
            if cfg.require_volume_confirm:
                if micro_jump_pct < cfg.min_jump_pct * 0.65:
                    return None
                if (buy_vol_usdt + sell_vol_usdt) > 0 and buy_vol_usdt < sell_vol_usdt:
                    return None

            # Calibrated Leader bid mapped to Follower price space, scaled by partial convergence beta
            full_calibrated_price = l_curr.bid * basis_factor
            calibrated_target = f_curr.mid + beta * (full_calibrated_price - f_curr.mid)

            # Fix #10 & #11: Expected gross spread buys at f_curr.ask and accounts for Follower spread
            raw_spread_pct = (calibrated_target - f_curr.ask) / f_curr.ask - 0.5 * follower_spread_pct
            expected_net_pct = raw_spread_pct - total_cost_pct

            if expected_net_pct >= cfg.take_profit_pct:
                # Section 3.2 Reservation Price (保留价): worst buy price that still secures take_profit_pct
                max_buy_for_profit = calibrated_target * (1.0 - fee_cost_pct - cfg.take_profit_pct)
                slip_cap_price = f_curr.ask * (1.0 + cfg.slippage_pct)
                reservation_price = round(min(slip_cap_price, max_buy_for_profit), cfg.price_precision)

                if reservation_price >= f_curr.ask:
                    qty = self._compute_depth_aware_qty(cfg, f_curr.ask, f_curr.ask_qty)
                    if qty >= cfg.min_qty:
                        return SignalCandidate(
                            symbol=cfg.symbol,
                            side="LONG",
                            signal_mono_ms=now_mono,
                            signal_wall_ms=l_curr.wall_ts,
                            leader_jump_pct=long_jump_pct,
                            micro_jump_pct=micro_jump_pct,
                            z_score=z_score,
                            follower_lag_pct=follower_change_pct,
                            proportional_basis=self.log_basis_ema,
                            calibrated_target_price=calibrated_target,
                            raw_spread_pct=raw_spread_pct,
                            follower_spread_pct=follower_spread_pct,
                            expected_cost_pct=total_cost_pct,
                            expected_net_pct=expected_net_pct,
                            reservation_price=reservation_price,
                            trigger_price=f_curr.ask,
                            recommended_qty=qty,
                            beta_used=beta,
                            active_vol_usdt=buy_vol_usdt,
                            leader_mid=l_curr.mid,
                            follower_mid=f_curr.mid,
                        )

        # ==================== 2. SHORT SNIPE EVALUATION ====================
        if cfg.direction in ("BOTH", "SHORT_ONLY") and short_jump_pct >= cfg.min_jump_pct:
            z_score = short_jump_pct / sigma
            if z_score < cfg.adaptive_z_score:
                return None

            # Follower lag confirmation: Follower has not already dropped >= lag_confirm_max_pct
            if (-follower_change_pct) >= cfg.lag_confirm_max_pct:
                return None

            # Fix #12: Microprice and active trade flow confirmation
            if cfg.require_volume_confirm:
                if (-micro_jump_pct) < cfg.min_jump_pct * 0.65:
                    return None
                if (buy_vol_usdt + sell_vol_usdt) > 0 and sell_vol_usdt < buy_vol_usdt:
                    return None

            full_calibrated_price = l_curr.ask * basis_factor
            calibrated_target = f_curr.mid - beta * (f_curr.mid - full_calibrated_price)

            raw_spread_pct = (f_curr.bid - calibrated_target) / f_curr.bid - 0.5 * follower_spread_pct
            expected_net_pct = raw_spread_pct - total_cost_pct

            if expected_net_pct >= cfg.take_profit_pct:
                # Section 3.2 Reservation Price (保留价): lowest sell price that still secures take_profit_pct
                min_sell_for_profit = calibrated_target * (1.0 + fee_cost_pct + cfg.take_profit_pct)
                slip_cap_price = f_curr.bid * (1.0 - cfg.slippage_pct)
                reservation_price = round(max(slip_cap_price, min_sell_for_profit), cfg.price_precision)

                if reservation_price <= f_curr.bid:
                    qty = self._compute_depth_aware_qty(cfg, f_curr.bid, f_curr.bid_qty)
                    if qty >= cfg.min_qty:
                        return SignalCandidate(
                            symbol=cfg.symbol,
                            side="SHORT",
                            signal_mono_ms=now_mono,
                            signal_wall_ms=l_curr.wall_ts,
                            leader_jump_pct=-short_jump_pct,
                            micro_jump_pct=micro_jump_pct,
                            z_score=z_score,
                            follower_lag_pct=follower_change_pct,
                            proportional_basis=self.log_basis_ema,
                            calibrated_target_price=calibrated_target,
                            raw_spread_pct=raw_spread_pct,
                            follower_spread_pct=follower_spread_pct,
                            expected_cost_pct=total_cost_pct,
                            expected_net_pct=expected_net_pct,
                            reservation_price=reservation_price,
                            trigger_price=f_curr.bid,
                            recommended_qty=qty,
                            beta_used=beta,
                            active_vol_usdt=sell_vol_usdt,
                            leader_mid=l_curr.mid,
                            follower_mid=f_curr.mid,
                        )

        return None

    @staticmethod
    def _compute_depth_aware_qty(cfg: SymbolStrategy, price: float, top_book_qty: float) -> float:
        """
        Section 3.2 & 1.3: Depth-aware sizing with contract precision & minimum lot handling.
        qty = min(configured_usdt / price, top_book_qty * safety_factor)
        """
        if price <= 0:
            return 0.0
        target_raw_qty = cfg.order_usdt / (price * cfg.contract_size)
        depth_cap_qty = top_book_qty * 0.85 if top_book_qty > 0 else target_raw_qty
        raw_qty = min(target_raw_qty, max(cfg.min_qty, depth_cap_qty))
        if cfg.qty_precision == 0:
            return float(int(raw_qty))
        return round(raw_qty, cfg.qty_precision)
