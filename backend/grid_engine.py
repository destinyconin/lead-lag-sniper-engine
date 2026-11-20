"""
Dual-Mode Bidirectional Grid Strategy Tracker (`LONG_SHORT_GRID`, `TREND_GRID`, `ADAPTIVE_GRID`).
Supports per-symbol:
1. Traditional Mean-Reversion Grid (`MEAN_REVERSION`):
   - Buy dips (`L1..Ln` below center) / Sell rallies (`S1..Sn` above center)
   - Fixed per-slot take-profit covering round-trip fees + breakout hard stop-loss
2. Reverse / Trend Breakout Grid (`TREND_BREAKOUT`):
   - Buy breakouts (`L1..Ln` above center + Dead Zone / Bollinger Upper Band)
   - Sell breakdowns (`S1..Sn` below center - Dead Zone / Bollinger Lower Band)
   - Front-loaded decaying pyramid sizing (e.g., 50% / 30% / 20% across 3 levels)
   - False-breakout hard stop-loss + Livermore step-up breakeven stop-loss on pyramid fills
   - No fixed TP ceiling: global per-side Trailing Stop (e.g., 1.5 * step pullback from peak/trough)
     that batch-closes all profitable slots on that side at once
3. Volatility-Adaptive Dual-Mode Grid (`ADAPTIVE_GRID`):
   - Defaults to `MEAN_REVERSION` during normal range-bound markets (70% of time)
   - When completely flat (initial build / Auto-Recenter / flat regime check) and 15m/1H
     Bollinger Band Width (BBW) compresses into extreme VCP squeeze percentile, automatically
     switches to `TREND_BREAKOUT` to capture directional expansion, with hysteresis on exit.
"""
import math
import time
from typing import Dict, List, Optional, Tuple
from backend.math_engine import mono_ms
from backend.models import SymbolStrategy


def calc_pyramid_weights(count: int, decay_enabled: bool = True) -> List[float]:
    """
    Returns normalized sizing weights summing to 1.0 across `count` grid levels.
    When `decay_enabled=True`: front-loaded pyramid (3 levels => exact 50% / 30% / 20%).
    """
    n = max(1, int(count))
    if not decay_enabled or n == 1:
        return [1.0 / n] * n
    presets = {
        2: [0.60, 0.40],
        3: [0.50, 0.30, 0.20],
        4: [0.38, 0.27, 0.20, 0.15],
        5: [0.30, 0.24, 0.19, 0.15, 0.12],
    }
    if n in presets:
        return list(presets[n])
    raw = [0.65 ** i for i in range(n)]
    total = sum(raw)
    return [w / total for w in raw]


class LongShortGridTracker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.center_price: float = 0.0
        self.levels: Dict[str, dict] = {}
        self.completed_cycles: int = 0
        self.total_net_pnl: float = 0.0
        self._last_sig_params: Optional[tuple] = None

        # Dual-mode state: "MEAN_REVERSION" | "TREND_BREAKOUT"
        self.active_grid_mode: str = "MEAN_REVERSION"
        self.long_peak_price: float = 0.0
        self.short_trough_price: float = 0.0
        self.long_trail_stop_price: float = 0.0
        self.short_trail_stop_price: float = 0.0
        self.last_bbw_info: Optional[dict] = None
        self.last_regime_reason: str = "震荡均值回归（默认）"
        self._pending_regime_log: Optional[str] = None
        self._needs_post_trend_recenter: bool = False
        self._post_sl_cooldown_until_mono: float = 0.0
        self._effective_trend_bias: str = "RANGE"
        self._last_user_sig_params: Optional[tuple] = None

    def consume_regime_log(self) -> Optional[str]:
        msg = self._pending_regime_log
        self._pending_regime_log = None
        return msg

    def _has_active_slots(self, side_filter: Optional[str] = None) -> bool:
        for lv in self.levels.values():
            if side_filter and lv["side"] != side_filter:
                continue
            if lv["status"] in ("ENTERING", "OPEN", "EXITING"):
                return True
        return False

    def _calc_prec(self, cfg: SymbolStrategy) -> int:
        base_prec = min(12, max(2, int(cfg.price_precision)))
        if 0.0 < self.center_price < 1.0:
            dyn_floor = max(base_prec, -int(math.floor(math.log10(self.center_price))) + 4)
            return min(12, dyn_floor)
        return base_prec

    def _get_valid_bbw(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> Optional[dict]:
        """Returns fresh, timeframe-aligned BBW/EMA info and automatically expires stale snapshots."""
        tf_expected = "1H" if str(cfg.bbw_timeframe or "15m").upper() == "1H" else "15m"
        max_age_ms = 4_500_000 if tf_expected == "1H" else 1_200_000
        now_wall = int(time.time() * 1000)

        def _is_valid(cand: Optional[dict]) -> bool:
            if not isinstance(cand, dict) or not cand:
                return False
            c_tf = str(cand.get("timeframe") or "")
            if c_tf and c_tf.upper() != tf_expected.upper():
                return False
            upd = int(cand.get("updated_at_ms") or 0)
            if upd > 0 and (now_wall - upd) > max_age_ms:
                return False
            return True

        if bbw_info is not None:
            if _is_valid(bbw_info):
                self.last_bbw_info = dict(bbw_info)
                return self.last_bbw_info
        if _is_valid(self.last_bbw_info):
            return self.last_bbw_info
        self.last_bbw_info = None
        self._effective_trend_bias = "RANGE"
        return None

    def _resolve_effective_trend_bias(self, info: Optional[dict]) -> str:
        """Applies hysteresis to EMA trend bias so minor 1-tick oscillations cannot thrash regime/filters."""
        if not info:
            self._effective_trend_bias = "RANGE"
            return "RANGE"
        raw_bias = str(info.get("trend_bias") or "RANGE")
        ema_z = info.get("ema_gap_z")
        if ema_z is None:
            self._effective_trend_bias = raw_bias
            return raw_bias
        z_val = float(ema_z)
        cur = self._effective_trend_bias
        if raw_bias == "BULL":
            self._effective_trend_bias = "BULL"
        elif raw_bias == "BEAR":
            self._effective_trend_bias = "BEAR"
        elif cur == "BULL" and z_val > 0.30:
            self._effective_trend_bias = "BULL"
        elif cur == "BEAR" and z_val < -0.30:
            self._effective_trend_bias = "BEAR"
        else:
            self._effective_trend_bias = "RANGE"
        return self._effective_trend_bias

    def _resolve_target_mode(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> Tuple[str, str]:
        if cfg.strategy_type == "TREND_GRID":
            return "TREND_BREAKOUT", "手动指定顺势突破网格"
        if cfg.strategy_type == "LONG_SHORT_GRID":
            return "MEAN_REVERSION", "手动指定传统震荡网格"

        # ADAPTIVE_GRID: Volatility-driven dual-mode switching with hysteresis
        info = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        if not info:
            return self.active_grid_mode, "等待 15m/1H BBW 波动率样本（暂保持当前模式）"

        pct_rank = float(info.get("bbw_percentile", 50.0))
        bbw_pct = float(info.get("bbw_pct", 0.0))
        tf = str(info.get("timeframe", cfg.bbw_timeframe))
        trend_bias = self._resolve_effective_trend_bias(info)
        breakout_exp = str(info.get("breakout_expansion") or "NONE")
        thresh = float(cfg.bbw_squeeze_percentile)
        release_thresh = min(85.0, thresh + 15.0)

        if self.active_grid_mode != "TREND_BREAKOUT":
            if pct_rank <= thresh:
                return (
                    "TREND_BREAKOUT",
                    f"检测到 {tf} BBW={bbw_pct:.2f}% 压缩至历史 {pct_rank:.1f}% 极低位 (≤{thresh:.0f}%)，触发 VCP 变盘预警 → 顺势突破网格",
                )
            if breakout_exp in ("UP_BREAKOUT", "DOWN_BREAKOUT"):
                dir_cn = "放量向上突破" if breakout_exp == "UP_BREAKOUT" else "放量向下破位"
                return (
                    "TREND_BREAKOUT",
                    f"检测到 {tf} {dir_cn}（EMA趋势={trend_bias}, BBW分位={pct_rank:.1f}%）→ 切入顺势突破网格防逆势接飞刀",
                )
            bias_cn = "多头(停逆势空)" if trend_bias == "BULL" else ("空头(停逆势多)" if trend_bias == "BEAR" else "震荡双向")
            return (
                "MEAN_REVERSION",
                f"{tf} BBW={bbw_pct:.2f}%（分位 {pct_rank:.1f}% · {bias_cn}）→ 传统均值回归网格",
            )
        else:
            if pct_rank >= release_thresh and breakout_exp == "NONE" and trend_bias == "RANGE":
                return (
                    "MEAN_REVERSION",
                    f"{tf} BBW={bbw_pct:.2f}% 扩张恢复至 {pct_rank:.1f}% 且均线回归震荡 → 切回传统震荡网格",
                )
            if breakout_exp in ("UP_BREAKOUT", "DOWN_BREAKOUT"):
                return (
                    "TREND_BREAKOUT",
                    f"{tf} 单边趋势延续（{breakout_exp} · EMA={trend_bias}）→ 保持顺势突破网格",
                )
            return (
                "TREND_BREAKOUT",
                f"{tf} BBW={bbw_pct:.2f}% 仍处于爆发/趋势窗口（分位 {pct_rank:.1f}% · EMA={trend_bias}）→ 保持顺势突破网格",
            )

    def _build_levels(
        self,
        cfg: SymbolStrategy,
        bbw_info: Optional[dict] = None,
        preserve_cooldowns: bool = False,
    ):
        if self.center_price <= 0:
            return
        info = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        old_cooldowns = (
            {
                sid: (
                    float(lv.get("cooldown_until_mono") or 0.0),
                    float(lv.get("last_closed_mono") or 0.0),
                )
                for sid, lv in self.levels.items()
            }
            if preserve_cooldowns
            else {}
        )

        new_levels: Dict[str, dict] = {}
        prec = self._calc_prec(cfg)
        step = float(cfg.grid_step_pct)
        tp_pct = float(cfg.grid_take_profit_pct)
        sl_pct = float(cfg.grid_stop_loss_pct)
        rng = float(cfg.grid_range_pct)
        count = max(1, int(cfg.grid_levels))

        # Reset side trailing extremes whenever rebuilding flat grid
        self.long_peak_price = 0.0
        self.short_trough_price = 0.0
        self.long_trail_stop_price = 0.0
        self.short_trail_stop_price = 0.0

        if self.active_grid_mode == "TREND_BREAKOUT":
            dz_mult = float(cfg.trend_dead_zone_mult)
            dz_ratio = dz_mult * step
            bb_upper = float(info.get("bb_upper") or 0.0) if (cfg.trend_use_bb_breakout and info) else 0.0
            bb_lower = float(info.get("bb_lower") or 0.0) if (cfg.trend_use_bb_breakout and info) else 0.0
            # Cap ONLY L1/S1 distance from center; ALWAYS place all `count` pyramid legs beyond L1/S1 (no clipping L2..Ln!)
            max_first_span = max(rng, dz_ratio * 1.5, step * 2.0)

            if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
                base_l1 = self.center_price * (1.0 + dz_ratio)
                if bb_upper > self.center_price:
                    base_l1 = min(self.center_price * (1.0 + max_first_span), max(base_l1, bb_upper))
                long_triggers: List[float] = [
                    round(base_l1 + (i - 1) * self.center_price * step, prec) for i in range(1, count + 1)
                ]

                weights = calc_pyramid_weights(len(long_triggers), cfg.trend_pyramid_decay)
                side_budget = float(cfg.order_usdt) * len(long_triggers)
                for idx_0, trig in enumerate(long_triggers):
                    i = idx_0 + 1
                    slot_id = f"L{i}"
                    w = weights[idx_0]
                    slot_usdt = round(side_budget * w, 2) if cfg.trend_pyramid_decay else round(float(cfg.order_usdt), 2)
                    init_sl = (
                        round(max(self.center_price, trig * max(0.05, 1.0 - sl_pct)), prec)
                        if i == 1
                        else long_triggers[idx_0 - 1]
                    )
                    cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                    new_levels[slot_id] = {
                        "slot_id": slot_id,
                        "side": "LONG",
                        "level_idx": i,
                        "grid_mode": "TREND_BREAKOUT",
                        "trigger_price": trig,
                        "tp_price": 0.0,  # Trend grid uses dynamic trailing stop instead of fixed TP
                        "sl_price": init_sl,
                        "order_usdt": slot_usdt,
                        "weight_pct": round(w * 100.0, 1),
                        "status": "IDLE",
                        "entry_price": 0.0,
                        "filled_qty": 0.0,
                        "contracts": 0.0,
                        "ct_val": 1.0,
                        "mgn_mode": "cross",
                        "opened_mono_ms": 0.0,
                        "last_closed_mono": last_c,
                        "cooldown_until_mono": cd_u,
                        "maker_tp_touched_mono": 0.0,
                        "cond_sl_touched_mono": 0.0,
                    }

            if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
                base_s1 = self.center_price * max(0.05, 1.0 - dz_ratio)
                if 0.0 < bb_lower < self.center_price:
                    base_s1 = max(self.center_price * max(0.05, 1.0 - max_first_span), min(base_s1, bb_lower))
                short_triggers: List[float] = [
                    round(max(self.center_price * 0.05, base_s1 - (i - 1) * self.center_price * step), prec)
                    for i in range(1, count + 1)
                ]

                weights = calc_pyramid_weights(len(short_triggers), cfg.trend_pyramid_decay)
                side_budget = float(cfg.order_usdt) * len(short_triggers)
                for idx_0, trig in enumerate(short_triggers):
                    i = idx_0 + 1
                    slot_id = f"S{i}"
                    w = weights[idx_0]
                    slot_usdt = round(side_budget * w, 2) if cfg.trend_pyramid_decay else round(float(cfg.order_usdt), 2)
                    init_sl = (
                        round(min(self.center_price, trig * (1.0 + sl_pct)), prec)
                        if i == 1
                        else short_triggers[idx_0 - 1]
                    )
                    cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                    new_levels[slot_id] = {
                        "slot_id": slot_id,
                        "side": "SHORT",
                        "level_idx": i,
                        "grid_mode": "TREND_BREAKOUT",
                        "trigger_price": trig,
                        "tp_price": 0.0,
                        "sl_price": init_sl,
                        "order_usdt": slot_usdt,
                        "weight_pct": round(w * 100.0, 1),
                        "status": "IDLE",
                        "entry_price": 0.0,
                        "filled_qty": 0.0,
                        "contracts": 0.0,
                        "ct_val": 1.0,
                        "mgn_mode": "cross",
                        "opened_mono_ms": 0.0,
                        "last_closed_mono": last_c,
                        "cooldown_until_mono": cd_u,
                        "maker_tp_touched_mono": 0.0,
                        "cond_sl_touched_mono": 0.0,
                    }
        else:
            # Traditional Mean-Reversion Grid: L1..Ln below center, S1..Sn above center
            if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
                long_indices = [i for i in range(1, count + 1) if i == 1 or i * step <= rng + 1e-9]
                w_pct = round(100.0 / max(1, len(long_indices)), 1)
                for i in long_indices:
                    slot_id = f"L{i}"
                    trig = round(self.center_price * max(0.05, 1.0 - i * step), prec)
                    cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                    new_levels[slot_id] = {
                        "slot_id": slot_id,
                        "side": "LONG",
                        "level_idx": i,
                        "grid_mode": "MEAN_REVERSION",
                        "trigger_price": trig,
                        "tp_price": round(trig * (1.0 + tp_pct), prec),
                        "sl_price": round(self.center_price * max(0.05, 1.0 - sl_pct), prec),
                        "order_usdt": round(float(cfg.order_usdt), 2),
                        "weight_pct": w_pct,
                        "status": "IDLE",
                        "entry_price": 0.0,
                        "filled_qty": 0.0,
                        "contracts": 0.0,
                        "ct_val": 1.0,
                        "mgn_mode": "cross",
                        "opened_mono_ms": 0.0,
                        "last_closed_mono": last_c,
                        "cooldown_until_mono": cd_u,
                        "maker_tp_touched_mono": 0.0,
                        "cond_sl_touched_mono": 0.0,
                    }

            if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
                short_indices = [i for i in range(1, count + 1) if i == 1 or i * step <= rng + 1e-9]
                w_pct = round(100.0 / max(1, len(short_indices)), 1)
                for i in short_indices:
                    slot_id = f"S{i}"
                    trig = round(self.center_price * (1.0 + i * step), prec)
                    cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                    new_levels[slot_id] = {
                        "slot_id": slot_id,
                        "side": "SHORT",
                        "level_idx": i,
                        "grid_mode": "MEAN_REVERSION",
                        "trigger_price": trig,
                        "tp_price": round(trig * max(0.05, 1.0 - tp_pct), prec),
                        "sl_price": round(self.center_price * (1.0 + sl_pct), prec),
                        "order_usdt": round(float(cfg.order_usdt), 2),
                        "weight_pct": w_pct,
                        "status": "IDLE",
                        "entry_price": 0.0,
                        "filled_qty": 0.0,
                        "contracts": 0.0,
                        "ct_val": 1.0,
                        "mgn_mode": "cross",
                        "opened_mono_ms": 0.0,
                        "last_closed_mono": last_c,
                        "cooldown_until_mono": cd_u,
                        "maker_tp_touched_mono": 0.0,
                        "cond_sl_touched_mono": 0.0,
                    }

        self.levels = new_levels

    def clear_cooldowns(self):
        self._post_sl_cooldown_until_mono = 0.0
        for lv in self.levels.values():
            lv["cooldown_until_mono"] = 0.0
            lv["last_closed_mono"] = 0.0

    def _compute_user_sig_params(self, cfg: SymbolStrategy, target_mode: str) -> tuple:
        return (
            cfg.strategy_type,
            target_mode,
            cfg.grid_side_mode,
            int(cfg.grid_levels),
            round(float(cfg.order_usdt), 4),
            round(float(cfg.grid_step_pct), 6),
            round(float(cfg.grid_take_profit_pct), 6),
            round(float(cfg.grid_range_pct), 6),
            round(float(cfg.grid_stop_loss_pct), 6),
            round(float(cfg.trend_dead_zone_mult), 3),
            round(float(cfg.trend_trailing_mult), 3),
            bool(cfg.trend_pyramid_decay),
            bool(cfg.trend_use_bb_breakout),
            int(cfg.price_precision),
            str(cfg.bbw_timeframe),
            round(float(cfg.bbw_squeeze_percentile), 2),
        )

    def _compute_sig_params(
        self, cfg: SymbolStrategy, target_mode: str, bbw_info: Optional[dict] = None
    ) -> tuple:
        info = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        prec = self._calc_prec(cfg)
        bb_u = 0.0
        bb_l = 0.0
        if target_mode == "TREND_BREAKOUT" and cfg.trend_use_bb_breakout and info:
            bb_u = round(float(info.get("bb_upper") or 0.0), prec)
            bb_l = round(float(info.get("bb_lower") or 0.0), prec)
        return self._compute_user_sig_params(cfg, target_mode) + (bb_u, bb_l)

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
        user_sig = self._compute_user_sig_params(cfg, target_mode)
        sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)

        if self.center_price <= 0 and current_mid > 0:
            self.active_grid_mode = target_mode
            self.last_regime_reason = reason
            self.center_price = current_mid
            self._build_levels(cfg, bbw_info=valid_bbw)
            self._last_user_sig_params = user_sig
            self._last_sig_params = sig_params
            return

        if not self._has_active_slots():
            self.last_regime_reason = reason
            mode_changed = target_mode != self.active_grid_mode
            user_changed = (self._last_user_sig_params is None) or (user_sig != self._last_user_sig_params)
            if mode_changed or sig_params != self._last_sig_params:
                prev_mode = self.active_grid_mode
                self.active_grid_mode = target_mode
                bb_only_update = (not mode_changed) and (not user_changed)
                if current_mid > 0 and (self.center_price <= 0 or mode_changed or bb_only_update):
                    self.center_price = current_mid
                if self.center_price > 0:
                    self._build_levels(cfg, bbw_info=valid_bbw, preserve_cooldowns=bb_only_update)
                self._needs_post_trend_recenter = False
                self._last_user_sig_params = user_sig
                self._last_sig_params = sig_params
                if not bb_only_update:
                    self.clear_cooldowns()
                if mode_changed and cfg.strategy_type == "ADAPTIVE_GRID":
                    self._pending_regime_log = (
                        f"[VCP双模式切换] {self.symbol} 空仓切换 {prev_mode} → {target_mode} | {reason}"
                    )

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        if new_center <= 0 or self._has_active_slots():
            return False
        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        prev_mode = self.active_grid_mode
        target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
        self.active_grid_mode = target_mode
        self.last_regime_reason = reason
        self.center_price = new_center
        self._needs_post_trend_recenter = False
        self._post_sl_cooldown_until_mono = 0.0
        self._build_levels(cfg, bbw_info=valid_bbw)
        self._last_user_sig_params = self._compute_user_sig_params(cfg, target_mode)
        self._last_sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)
        if prev_mode != target_mode and cfg.strategy_type == "ADAPTIVE_GRID":
            self._pending_regime_log = (
                f"[VCP双模式重锚] {self.symbol} 重锚至 {new_center} 并切换 {prev_mode} → {target_mode} | {reason}"
            )
        return True

    def evaluate_tick(
        self,
        bid: float,
        ask: float,
        mid: float,
        cfg: SymbolStrategy,
        is_net_mode: bool,
        allow_entries: bool = True,
        round_trip_fee: float = 0.0010,
        bbw_info: Optional[dict] = None,
        force_exit_all: bool = False,
        allow_recenter: bool = True,
    ) -> Tuple[List[dict], List[dict]]:
        """
        Evaluates a single Follower BBO update against all grid levels.
        Synchronously locks selected slots into `ENTERING` or `EXITING` before returning.
        """
        if mid <= 0 or bid <= 0 or ask <= 0:
            return [], []

        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)

        if self.center_price <= 0 or not self.levels:
            target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
            self.active_grid_mode = target_mode
            self.last_regime_reason = reason
            self.center_price = mid
            self._build_levels(cfg, bbw_info=valid_bbw)
            self._last_user_sig_params = self._compute_user_sig_params(cfg, target_mode)
            self._last_sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)
            return [], []

        now_m = mono_ms()
        exits: List[dict] = []
        entries: List[dict] = []
        prec = self._calc_prec(cfg)

        # 0. Emergency Kill Switch retry: keep retrying any OPEN slot whose backoff expired until flat!
        if force_exit_all:
            for lv in self.levels.values():
                if lv["status"] != "OPEN":
                    continue
                if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                lv["status"] = "EXITING"
                exits.append(
                    {**lv, "exit_reason": "KILL_SWITCH", "target_price": bid if lv["side"] == "LONG" else ask}
                )
            return [], exits

        def _should_fire_software_sl(lv_ref: dict, px_now: float, sl_px_val: float, is_long_side: bool) -> bool:
            if not bool(lv_ref.get("cond_sl_live", False)):
                return True
            # Grant OKX native Conditional SL 1.5s grace (or immediate if breached >0.25% past SL) to prevent double-close!
            touched = float(lv_ref.get("cond_sl_touched_mono") or 0.0)
            if touched <= 0.0:
                lv_ref["cond_sl_touched_mono"] = now_m
                touched = now_m
            deep_breach = (
                (px_now <= sl_px_val * 0.9975) if is_long_side else (px_now >= sl_px_val * 1.0025)
            )
            return (now_m - touched >= 1500.0) or deep_breach

        def _real_slot_notional(lv_ref: dict) -> float:
            e_px_v = float(lv_ref.get("entry_price") or 0.0)
            q_v = float(lv_ref.get("filled_qty") or 0.0)
            if e_px_v > 0 and q_v > 0:
                return e_px_v * q_v
            return max(1.0, float(lv_ref.get("order_usdt") or cfg.order_usdt))

        # 1. Check exits on currently OPEN grid slots first
        if self.active_grid_mode == "TREND_BREAKOUT":
            trail_ratio = max(0.0005, float(cfg.trend_trailing_mult) * float(cfg.grid_step_pct))

            open_longs = [lv for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] == "OPEN"]
            if open_longs:
                max_entry_l = max(float(lv.get("entry_price") or 0.0) for lv in open_longs)
                self.long_peak_price = max(self.long_peak_price, bid, max_entry_l)
                long_trail_px = round(self.long_peak_price * max(0.05, 1.0 - trail_ratio), prec)
                self.long_trail_stop_price = long_trail_px

                # 1a. Per-slot false-breakout / Livermore breakeven stop-loss (with cond_sl_live grace)
                for lv in open_longs:
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    sl_px = float(lv.get("sl_price") or 0.0)
                    if sl_px > 0 and bid <= sl_px:
                        if _should_fire_software_sl(lv, bid, sl_px, True):
                            lv["status"] = "EXITING"
                            reason = "TREND_BE_SL" if sl_px >= float(lv.get("entry_price") or 0.0) else "TREND_SL"
                            exits.append({**lv, "exit_reason": reason, "target_price": bid})
                    elif float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and bid > sl_px * 1.0005:
                        lv["cond_sl_touched_mono"] = 0.0

                # 1b. All-or-Nothing Batch Trailing Stop (weighted by real filled_qty * entry_price; eliminates partial-sweep churn!)
                if self.long_peak_price > 0 and bid <= long_trail_px:
                    remaining_longs = [
                        lv for lv in open_longs
                        if lv["status"] == "OPEN" and now_m >= float(lv.get("exit_retry_until_mono") or 0.0)
                    ]
                    if remaining_longs:
                        side_net_pnl = sum(
                            ((bid - float(lv.get("entry_price") or 0.0)) / max(1e-12, float(lv.get("entry_price") or 0.0)) - round_trip_fee)
                            * _real_slot_notional(lv)
                            for lv in remaining_longs
                            if float(lv.get("entry_price") or 0.0) > 0
                        )
                        side_notional = sum(
                            _real_slot_notional(lv)
                            for lv in remaining_longs
                            if float(lv.get("entry_price") or 0.0) > 0
                        )
                        if side_notional > 0 and side_net_pnl > max(0.0, side_notional * 0.0005):
                            for lv in remaining_longs:
                                lv["status"] = "EXITING"
                                exits.append({**lv, "exit_reason": "TREND_TRAIL_TP", "target_price": bid})
            elif not self._has_active_slots("LONG"):
                self.long_peak_price = 0.0
                self.long_trail_stop_price = 0.0

            open_shorts = [lv for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] == "OPEN"]
            if open_shorts:
                min_entry_s = min(float(lv.get("entry_price") or ask) for lv in open_shorts)
                if self.short_trough_price <= 0:
                    self.short_trough_price = min(ask, min_entry_s)
                else:
                    self.short_trough_price = min(self.short_trough_price, ask, min_entry_s)
                short_trail_px = round(self.short_trough_price * (1.0 + trail_ratio), prec)
                self.short_trail_stop_price = short_trail_px

                for lv in open_shorts:
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    sl_px = float(lv.get("sl_price") or 0.0)
                    if sl_px > 0 and ask >= sl_px:
                        if _should_fire_software_sl(lv, ask, sl_px, False):
                            lv["status"] = "EXITING"
                            reason = "TREND_BE_SL" if sl_px <= float(lv.get("entry_price") or 0.0) else "TREND_SL"
                            exits.append({**lv, "exit_reason": reason, "target_price": ask})
                    elif float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and ask < sl_px * 0.9995:
                        lv["cond_sl_touched_mono"] = 0.0

                if self.short_trough_price > 0 and ask >= short_trail_px:
                    remaining_shorts = [
                        lv for lv in open_shorts
                        if lv["status"] == "OPEN" and now_m >= float(lv.get("exit_retry_until_mono") or 0.0)
                    ]
                    if remaining_shorts:
                        side_net_pnl = sum(
                            ((float(lv.get("entry_price") or 0.0) - ask) / max(1e-12, float(lv.get("entry_price") or 0.0)) - round_trip_fee)
                            * _real_slot_notional(lv)
                            for lv in remaining_shorts
                            if float(lv.get("entry_price") or 0.0) > 0
                        )
                        side_notional = sum(
                            _real_slot_notional(lv)
                            for lv in remaining_shorts
                            if float(lv.get("entry_price") or 0.0) > 0
                        )
                        if side_notional > 0 and side_net_pnl > max(0.0, side_notional * 0.0005):
                            for lv in remaining_shorts:
                                lv["status"] = "EXITING"
                                exits.append({**lv, "exit_reason": "TREND_TRAIL_TP", "target_price": ask})
            elif not self._has_active_slots("SHORT"):
                self.short_trough_price = 0.0
                self.short_trail_stop_price = 0.0
        else:
            for slot_id, lv in self.levels.items():
                if lv["status"] != "OPEN":
                    continue
                if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                tp_px = float(lv.get("tp_price") or 0.0)
                sl_px = float(lv.get("sl_price") or 0.0)
                maker_live = bool(lv.get("maker_tp_live", False))
                if lv["side"] == "LONG":
                    if tp_px > 0 and bid >= tp_px:
                        if maker_live:
                            touched = float(lv.get("maker_tp_touched_mono") or 0.0)
                            if touched <= 0.0:
                                lv["maker_tp_touched_mono"] = now_m
                                touched = now_m
                            # Give OKX resting Maker Post-Only TP 4.5s grace (WS/poll settles naturally) or >0.25% breakthrough
                            if (now_m - touched >= 4500.0) or (bid >= tp_px * 1.0025):
                                lv["status"] = "EXITING"
                                exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                        else:
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                    elif sl_px > 0 and bid <= sl_px:
                        if _should_fire_software_sl(lv, bid, sl_px, True):
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "GRID_SL", "target_price": bid})
                    else:
                        if maker_live and float(lv.get("maker_tp_touched_mono") or 0.0) > 0.0 and bid < tp_px * 0.9995:
                            lv["maker_tp_touched_mono"] = 0.0
                        if float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and bid > sl_px * 1.0005:
                            lv["cond_sl_touched_mono"] = 0.0
                else:
                    if tp_px > 0 and ask <= tp_px:
                        if maker_live:
                            touched = float(lv.get("maker_tp_touched_mono") or 0.0)
                            if touched <= 0.0:
                                lv["maker_tp_touched_mono"] = now_m
                                touched = now_m
                            if (now_m - touched >= 4500.0) or (ask <= tp_px * 0.9975):
                                lv["status"] = "EXITING"
                                exits.append({**lv, "exit_reason": "GRID_TP", "target_price": ask})
                        else:
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "GRID_TP", "target_price": ask})
                    elif sl_px > 0 and ask >= sl_px:
                        if _should_fire_software_sl(lv, ask, sl_px, False):
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "GRID_SL", "target_price": ask})
                    else:
                        if maker_live and float(lv.get("maker_tp_touched_mono") or 0.0) > 0.0 and ask > tp_px * 1.0005:
                            lv["maker_tp_touched_mono"] = 0.0
                        if float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and ask < sl_px * 0.9995:
                            lv["cond_sl_touched_mono"] = 0.0

        # 2. Flat Auto-Recenter & Dual-Mode Volatility Regime Switching (gated by allow_recenter & post-SL cooldown!)
        if allow_recenter and not self._has_active_slots():
            target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
            mode_switched = target_mode != self.active_grid_mode
            eff_range = float(cfg.grid_range_pct)
            if self.active_grid_mode == "TREND_BREAKOUT" and self.center_price > 0:
                # Never let auto-recenter preempt L1/S1 trigger distance!
                first_dists = [
                    abs(float(lv["trigger_price"]) - self.center_price) / self.center_price
                    for lv in self.levels.values()
                    if int(lv.get("level_idx") or 0) == 1 and float(lv.get("trigger_price") or 0.0) > 0
                ]
                max_first_dist = max(first_dists) if first_dists else (float(cfg.trend_dead_zone_mult) * float(cfg.grid_step_pct))
                eff_range = max(eff_range, max_first_dist * 1.15)
            drifted_out = (
                cfg.grid_recenter_enabled
                and now_m >= self._post_sl_cooldown_until_mono
                and self.center_price > 0
                and abs(mid - self.center_price) / self.center_price > eff_range
            )
            post_trend_recenter = self._needs_post_trend_recenter
            if mode_switched or drifted_out or post_trend_recenter:
                prev_mode = self.active_grid_mode
                self.active_grid_mode = target_mode
                self.last_regime_reason = reason
                self.center_price = mid
                self._needs_post_trend_recenter = False
                self._build_levels(cfg, bbw_info=valid_bbw)
                self._last_user_sig_params = self._compute_user_sig_params(cfg, target_mode)
                self._last_sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)
                if mode_switched and cfg.strategy_type == "ADAPTIVE_GRID":
                    self._pending_regime_log = (
                        f"[VCP双模式切换] {self.symbol} 空仓重锚切换 {prev_mode} → {target_mode} | {reason}"
                    )
                return [], exits

        if not allow_entries:
            return [], exits

        # 3. Check IDLE slots for new grid entries (with 15m/1H EMA trend direction filter!)
        has_long_active = self._has_active_slots("LONG")
        has_short_active = self._has_active_slots("SHORT")
        step = max(0.0001, float(cfg.grid_step_pct))
        trend_bias = self._resolve_effective_trend_bias(valid_bbw)

        if self.active_grid_mode == "TREND_BREAKOUT":
            # Strictly monotonic breakout pyramid: only enter `max_active_idx + 1` (never re-open L1 under an open L2!)
            allow_trend_long = (cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY")) and (trend_bias != "BEAR")
            allow_trend_short = (cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY")) and (trend_bias != "BULL")
            if allow_trend_long:
                max_active_l = max(
                    (int(lv.get("level_idx") or 0) for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("ENTERING", "OPEN", "EXITING")),
                    default=0,
                )
                ordered_longs = sorted(
                    (lv for lv in self.levels.values() if lv["side"] == "LONG"),
                    key=lambda x: x["level_idx"],
                )
                for lv in ordered_longs:
                    if lv["level_idx"] <= max_active_l:
                        continue
                    if lv["status"] != "IDLE":
                        break
                    if is_net_mode and has_short_active:
                        break
                    cooldown_until = max(
                        float(lv.get("cooldown_until_mono") or 0.0),
                        (float(lv.get("last_closed_mono") or 0.0) + 1200.0) if float(lv.get("last_closed_mono") or 0.0) > 0 else 0.0,
                    )
                    if now_m < cooldown_until:
                        break
                    min_req_px = float(lv["trigger_price"])
                    if lv["level_idx"] > 1:
                        prev_lv = self.levels.get(f"L{lv['level_idx'] - 1}")
                        if not prev_lv or prev_lv.get("status") != "OPEN":
                            break
                        prev_entry = float(prev_lv.get("entry_price") or 0.0)
                        if prev_entry > 0:
                            min_req_px = max(min_req_px, round(prev_entry * (1.0 + step * 0.90), prec))
                    if ask >= min_req_px:
                        lv["status"] = "ENTERING"
                        has_long_active = True
                        res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": ask})
                        break

            if allow_trend_short:
                max_active_s = max(
                    (int(lv.get("level_idx") or 0) for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("ENTERING", "OPEN", "EXITING")),
                    default=0,
                )
                ordered_shorts = sorted(
                    (lv for lv in self.levels.values() if lv["side"] == "SHORT"),
                    key=lambda x: x["level_idx"],
                )
                for lv in ordered_shorts:
                    if lv["level_idx"] <= max_active_s:
                        continue
                    if lv["status"] != "IDLE":
                        break
                    if is_net_mode and has_long_active:
                        break
                    cooldown_until = max(
                        float(lv.get("cooldown_until_mono") or 0.0),
                        (float(lv.get("last_closed_mono") or 0.0) + 1200.0) if float(lv.get("last_closed_mono") or 0.0) > 0 else 0.0,
                    )
                    if now_m < cooldown_until:
                        break
                    max_req_px = float(lv["trigger_price"])
                    if lv["level_idx"] > 1:
                        prev_lv = self.levels.get(f"S{lv['level_idx'] - 1}")
                        if not prev_lv or prev_lv.get("status") != "OPEN":
                            break
                        prev_entry = float(prev_lv.get("entry_price") or 0.0)
                        if prev_entry > 0:
                            max_req_px = min(max_req_px, round(prev_entry * max(0.05, 1.0 - step * 0.90), prec))
                    if bid <= max_req_px:
                        lv["status"] = "ENTERING"
                        has_short_active = True
                        res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": bid})
                        break
        else:
            # Traditional Mean-Reversion entries: max 1 entry per side per tick, enforce spacing vs open siblings, respect post-SL cooldown
            if now_m < self._post_sl_cooldown_until_mono:
                return [], exits
            sl_guard_long = self.center_price * max(0.05, 1.0 - float(cfg.grid_stop_loss_pct) * 0.90)
            sl_guard_short = self.center_price * (1.0 + float(cfg.grid_stop_loss_pct) * 0.90)
            open_long_entries = [
                float(lv.get("entry_price") or 0.0)
                for lv in self.levels.values()
                if lv["side"] == "LONG" and lv["status"] == "OPEN" and float(lv.get("entry_price") or 0.0) > 0
            ]
            open_short_entries = [
                float(lv.get("entry_price") or 0.0)
                for lv in self.levels.values()
                if lv["side"] == "SHORT" and lv["status"] == "OPEN" and float(lv.get("entry_price") or 0.0) > 0
            ]
            entered_sides: set[str] = set()

            for slot_id, lv in self.levels.items():
                if len(entries) >= 2:
                    break
                if lv["status"] != "IDLE" or lv["side"] in entered_sides:
                    continue
                if lv["side"] == "LONG" and (cfg.grid_side_mode == "SHORT_ONLY" or trend_bias == "BEAR"):
                    continue
                if lv["side"] == "SHORT" and (cfg.grid_side_mode == "LONG_ONLY" or trend_bias == "BULL"):
                    continue
                cooldown_until = max(
                    float(lv.get("cooldown_until_mono") or 0.0),
                    (float(lv.get("last_closed_mono") or 0.0) + 1200.0) if float(lv.get("last_closed_mono") or 0.0) > 0 else 0.0,
                )
                if now_m < cooldown_until:
                    continue

                if lv["side"] == "LONG":
                    if is_net_mode and has_short_active:
                        continue
                    spacing_cap = min(open_long_entries) * (1.0 - step * 0.85) if open_long_entries else float("inf")
                    if ask <= lv["trigger_price"] and ask <= spacing_cap and mid >= sl_guard_long:
                        lv["status"] = "ENTERING"
                        has_long_active = True
                        entered_sides.add("LONG")
                        res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": ask})
                else:
                    if is_net_mode and has_long_active:
                        continue
                    spacing_floor = max(open_short_entries) * (1.0 + step * 0.85) if open_short_entries else 0.0
                    if bid >= lv["trigger_price"] and bid >= spacing_floor and mid <= sl_guard_short:
                        lv["status"] = "ENTERING"
                        has_short_active = True
                        entered_sides.add("SHORT")
                        res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": bid})

        return entries, exits

    def mark_entry_filled(
        self,
        slot_id: str,
        entry_price: float,
        filled_qty: float,
        contracts: float,
        ct_val: float,
        cfg: SymbolStrategy,
        round_trip_fee: float,
    ):
        lv = self.levels.get(slot_id)
        if not lv:
            return
        lv["status"] = "OPEN"
        lv["entry_price"] = entry_price
        lv["filled_qty"] = filled_qty
        lv["contracts"] = contracts
        lv["ct_val"] = ct_val
        lv["opened_mono_ms"] = mono_ms()
        lv["maker_tp_touched_mono"] = 0.0
        lv["cond_sl_touched_mono"] = 0.0
        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        sl_buf = max(0.0015, step * 0.25)
        tick_unit = 10.0 ** (-prec)

        if self.active_grid_mode == "TREND_BREAKOUT":
            # No fixed TP cap in Trend Breakout mode: trailing stop harvests winners
            lv["tp_price"] = 0.0
            sl_pct = float(cfg.grid_stop_loss_pct)
            lvl_idx = int(lv.get("level_idx") or 1)

            if lv["side"] == "LONG":
                self.long_peak_price = max(self.long_peak_price, entry_price)
                safe_cap_l = min(round(entry_price * max(0.05, 1.0 - sl_buf), prec), round(entry_price - tick_unit, prec))
                if lvl_idx <= 1:
                    raw_sl = round(max(self.center_price, entry_price * max(0.05, 1.0 - sl_pct)), prec)
                    lv["sl_price"] = min(raw_sl, safe_cap_l) if raw_sl >= entry_price else raw_sl
                else:
                    prev_lv = self.levels.get(f"L{lvl_idx - 1}")
                    prev_anchor = (
                        float(prev_lv["entry_price"])
                        if (prev_lv and float(prev_lv.get("entry_price") or 0.0) > 0)
                        else float(prev_lv.get("trigger_price", self.center_price) if prev_lv else self.center_price)
                    )
                    # Livermore floating-profit protection FIRST, then enforce monotonic SL ordering (L2.sl >= L1.sl)
                    # so base leg L1 can NEVER stop out before top leg L2!
                    max_earlier_sl = 0.0
                    for earlier in self.levels.values():
                        if earlier["side"] == "LONG" and earlier["status"] == "OPEN" and earlier["level_idx"] < lvl_idx:
                            e_entry = float(earlier.get("entry_price") or 0.0)
                            if e_entry <= 0:
                                continue
                            be_px = round(e_entry * (1.0 + round_trip_fee), prec)
                            step_px = round(prev_anchor, prec) if earlier["level_idx"] < lvl_idx - 1 else be_px
                            target_sl = max(float(earlier.get("sl_price") or 0.0), be_px, step_px)
                            earlier["sl_price"] = min(target_sl, safe_cap_l)
                            max_earlier_sl = max(max_earlier_sl, float(earlier["sl_price"]))
                    lv["sl_price"] = min(max(round(prev_anchor, prec), max_earlier_sl), safe_cap_l)
            else:
                self.short_trough_price = (
                    entry_price if self.short_trough_price <= 0 else min(self.short_trough_price, entry_price)
                )
                safe_floor_s = max(round(entry_price * (1.0 + sl_buf), prec), round(entry_price + tick_unit, prec))
                if lvl_idx <= 1:
                    raw_sl = round(min(self.center_price, entry_price * (1.0 + sl_pct)), prec)
                    lv["sl_price"] = max(raw_sl, safe_floor_s) if raw_sl <= entry_price else raw_sl
                else:
                    prev_lv = self.levels.get(f"S{lvl_idx - 1}")
                    prev_anchor = (
                        float(prev_lv["entry_price"])
                        if (prev_lv and float(prev_lv.get("entry_price") or 0.0) > 0)
                        else float(prev_lv.get("trigger_price", self.center_price) if prev_lv else self.center_price)
                    )
                    min_earlier_sl = float("inf")
                    for earlier in self.levels.values():
                        if earlier["side"] == "SHORT" and earlier["status"] == "OPEN" and earlier["level_idx"] < lvl_idx:
                            e_entry = float(earlier.get("entry_price") or 0.0)
                            if e_entry <= 0:
                                continue
                            be_px = round(e_entry * max(0.05, 1.0 - round_trip_fee), prec)
                            step_px = round(prev_anchor, prec) if earlier["level_idx"] < lvl_idx - 1 else be_px
                            cur_sl = float(earlier.get("sl_price") or 0.0)
                            target_sl = min(cur_sl, be_px, step_px) if cur_sl > 0 else min(be_px, step_px)
                            earlier["sl_price"] = max(target_sl, safe_floor_s)
                            min_earlier_sl = min(min_earlier_sl, float(earlier["sl_price"]))
                    base_sl = min(round(prev_anchor, prec), min_earlier_sl) if min_earlier_sl < float("inf") else round(prev_anchor, prec)
                    lv["sl_price"] = max(base_sl, safe_floor_s)
        else:
            sl_pct = float(cfg.grid_stop_loss_pct)
            if lv["side"] == "LONG":
                lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
                lv["sl_price"] = round(self.center_price * max(0.05, 1.0 - sl_pct), prec)
            else:
                lv["tp_price"] = round(
                    entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
                )
                lv["sl_price"] = round(self.center_price * (1.0 + sl_pct), prec)

    def recover_open_position(
        self,
        side: str,
        entry_price: float,
        filled_qty: float,
        contracts: float,
        ct_val: float,
        cfg: SymbolStrategy,
        round_trip_fee: float,
        preferred_slot: Optional[str] = None,
        mgn_mode: str = "cross",
    ) -> str:
        """
        Binds a persisted or live-exchange open position back into an active grid slot
        so restarts or pauses do not orphan positions out of strategy TP/SL management.
        Never overwrites an already-active slot!
        """
        if self.center_price <= 0 or not self.levels:
            target_mode, reason = self._resolve_target_mode(cfg)
            self.active_grid_mode = target_mode
            self.last_regime_reason = reason
            self.center_price = entry_price
            self._build_levels(cfg)

        target_slot = None
        if preferred_slot and preferred_slot in self.levels:
            lv = self.levels[preferred_slot]
            if lv["side"] == side and lv["status"] == "IDLE":
                target_slot = preferred_slot

        if not target_slot:
            for sid, lv in self.levels.items():
                if lv["side"] == side and lv["status"] == "IDLE":
                    target_slot = sid
                    break

        if not target_slot:
            prefix = "L" if side == "LONG" else "S"
            rec_idx = 1
            while f"{prefix}_REC_{rec_idx}" in self.levels:
                rec_idx += 1
            target_slot = f"{prefix}_REC_{rec_idx}"
            prec = self._calc_prec(cfg)
            self.levels[target_slot] = {
                "slot_id": target_slot,
                "side": side,
                "level_idx": max(1, int(cfg.grid_levels) + rec_idx),
                "grid_mode": self.active_grid_mode,
                "trigger_price": round(entry_price, prec),
                "tp_price": 0.0 if self.active_grid_mode == "TREND_BREAKOUT" else round(entry_price, prec),
                "sl_price": round(
                    entry_price
                    * (
                        max(0.05, 1.0 - float(cfg.grid_stop_loss_pct))
                        if side == "LONG"
                        else (1.0 + float(cfg.grid_stop_loss_pct))
                    ),
                    prec,
                ),
                "order_usdt": round(max(1.0, entry_price * filled_qty), 2),
                "weight_pct": 100.0,
                "status": "IDLE",
                "entry_price": 0.0,
                "filled_qty": 0.0,
                "contracts": 0.0,
                "ct_val": ct_val,
                "mgn_mode": mgn_mode or "cross",
                "opened_mono_ms": 0.0,
                "last_closed_mono": 0.0,
                "cooldown_until_mono": 0.0,
                "maker_tp_touched_mono": 0.0,
                "cond_sl_touched_mono": 0.0,
                "ephemeral_recovered": True,
            }

        lv = self.levels[target_slot]
        prec = self._calc_prec(cfg)
        sl_pct = float(cfg.grid_stop_loss_pct)
        lv["status"] = "OPEN"
        lv["recovered"] = True
        lv["entry_price"] = entry_price
        lv["filled_qty"] = filled_qty
        lv["contracts"] = contracts
        lv["ct_val"] = ct_val
        lv["mgn_mode"] = mgn_mode or lv.get("mgn_mode") or "cross"
        if entry_price > 0 and filled_qty > 0:
            lv["order_usdt"] = round(entry_price * filled_qty, 2)
        lv["opened_mono_ms"] = mono_ms()
        lv["exit_retry_until_mono"] = 0.0
        lv["maker_tp_touched_mono"] = 0.0
        lv["cond_sl_touched_mono"] = 0.0

        if self.active_grid_mode == "TREND_BREAKOUT":
            lv["tp_price"] = 0.0
            if side == "LONG":
                self.long_peak_price = max(self.long_peak_price, entry_price)
                lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
            else:
                self.short_trough_price = (
                    entry_price if self.short_trough_price <= 0 else min(self.short_trough_price, entry_price)
                )
                lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
        else:
            if side == "LONG":
                lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
                lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
            else:
                lv["tp_price"] = round(
                    entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
                )
                lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
        return target_slot

    def export_state(self) -> dict:
        open_slots = [
            {
                "slot_id": lv["slot_id"],
                "side": lv["side"],
                "entry_price": float(lv.get("entry_price") or 0.0),
                "filled_qty": float(lv.get("filled_qty") or 0.0),
                "contracts": float(lv.get("contracts") or 0.0),
                "ct_val": float(lv.get("ct_val") or 1.0),
                "mgn_mode": str(lv.get("mgn_mode") or "cross"),
                "tp_price": float(lv.get("tp_price") or 0.0),
                "sl_price": float(lv.get("sl_price") or 0.0),
                "order_usdt": float(lv.get("order_usdt") or 0.0),
                "weight_pct": float(lv.get("weight_pct") or 0.0),
                "maker_tp_order_id": str(lv.get("maker_tp_order_id") or "") if lv.get("maker_tp_live") else "",
                "maker_tp_live": bool(lv.get("maker_tp_live", False) and lv.get("maker_tp_order_id")),
                "cond_sl_algo_id": str(lv.get("cond_sl_algo_id") or "") if lv.get("cond_sl_live") else "",
                "cond_sl_live": bool(lv.get("cond_sl_live", False) and lv.get("cond_sl_algo_id")),
                "status": "OPEN",  # Restore EXITING slots cleanly as OPEN so protective poll/re-exit handles them
            }
            for lv in self.levels.values()
            if lv.get("status") in ("OPEN", "EXITING") and float(lv.get("entry_price") or 0.0) > 0
        ]
        return {
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": self.active_grid_mode,
            "long_peak_price": float(self.long_peak_price or 0.0),
            "short_trough_price": float(self.short_trough_price or 0.0),
            "completed_cycles": int(self.completed_cycles or 0),
            "total_net_pnl": float(self.total_net_pnl or 0.0),
            "last_bbw_info": dict(self.last_bbw_info) if isinstance(self.last_bbw_info, dict) else None,
            "open_slots": open_slots,
        }

    def restore_state(self, data: dict, cfg: SymbolStrategy):
        if not isinstance(data, dict):
            return
        if isinstance(data.get("last_bbw_info"), dict):
            self._get_valid_bbw(cfg, bbw_info=data.get("last_bbw_info"))
        saved_mode = str(data.get("active_grid_mode") or "")
        if cfg.strategy_type == "TREND_GRID":
            self.active_grid_mode = "TREND_BREAKOUT"
        elif cfg.strategy_type == "LONG_SHORT_GRID":
            self.active_grid_mode = "MEAN_REVERSION"
        elif saved_mode in ("MEAN_REVERSION", "TREND_BREAKOUT"):
            self.active_grid_mode = saved_mode

        c_px = float(data.get("center_price") or 0.0)
        if c_px > 0:
            self.center_price = c_px
            self._build_levels(cfg, bbw_info=self.last_bbw_info)
            self._last_user_sig_params = self._compute_user_sig_params(cfg, self.active_grid_mode)
            self._last_sig_params = self._compute_sig_params(cfg, self.active_grid_mode, bbw_info=self.last_bbw_info)
        self.completed_cycles = int(data.get("completed_cycles") or self.completed_cycles)
        self.total_net_pnl = float(data.get("total_net_pnl") or self.total_net_pnl)
        self.long_peak_price = float(data.get("long_peak_price") or 0.0)
        self.short_trough_price = float(data.get("short_trough_price") or 0.0)
        prec = self._calc_prec(cfg)

        for slot in data.get("open_slots") or []:
            sid = str(slot.get("slot_id") or "")
            if not sid:
                continue
            if sid not in self.levels:
                self.levels[sid] = {
                    "slot_id": sid,
                    "side": str(slot.get("side") or "LONG"),
                    "level_idx": int(sid[1:]) if len(sid) > 1 and sid[1:].isdigit() else 1,
                    "grid_mode": self.active_grid_mode,
                    "trigger_price": float(slot.get("entry_price") or 0.0),
                    "tp_price": float(slot.get("tp_price") or 0.0),
                    "sl_price": float(slot.get("sl_price") or 0.0),
                    "order_usdt": float(slot.get("order_usdt") or cfg.order_usdt),
                    "weight_pct": float(slot.get("weight_pct") or 0.0),
                    "status": "IDLE",
                    "entry_price": 0.0,
                    "filled_qty": 0.0,
                    "contracts": 0.0,
                    "ct_val": 1.0,
                    "mgn_mode": str(slot.get("mgn_mode") or "cross"),
                    "opened_mono_ms": 0.0,
                    "last_closed_mono": 0.0,
                    "cooldown_until_mono": 0.0,
                    "maker_tp_touched_mono": 0.0,
                    "cond_sl_touched_mono": 0.0,
                    "ephemeral_recovered": True,
                }
            lv = self.levels[sid]
            lv["status"] = "OPEN"
            lv["recovered"] = True
            e_px = float(slot.get("entry_price") or 0.0)
            lv["entry_price"] = e_px
            lv["filled_qty"] = float(slot.get("filled_qty") or 0.0)
            lv["contracts"] = float(slot.get("contracts") or 0.0)
            lv["ct_val"] = float(slot.get("ct_val") or 1.0)
            lv["mgn_mode"] = str(slot.get("mgn_mode") or "cross")
            tp_restored = float(slot.get("tp_price") or 0.0)
            # If strategy switched from TREND_BREAKOUT (tp=0) -> MEAN_REVERSION, synthesize valid TP so slot isn't stuck!
            if self.active_grid_mode == "MEAN_REVERSION" and tp_restored <= 0 and e_px > 0:
                tp_restored = round(
                    e_px * (1.0 + float(cfg.grid_take_profit_pct) + 0.001)
                    if lv["side"] == "LONG"
                    else e_px * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - 0.001),
                    prec,
                )
            lv["tp_price"] = tp_restored
            if float(slot.get("sl_price") or 0.0) > 0:
                lv["sl_price"] = float(slot["sl_price"])
            if float(slot.get("order_usdt") or 0.0) > 0:
                lv["order_usdt"] = float(slot["order_usdt"])
            tp_id = str(slot.get("maker_tp_order_id") or "")
            sl_id = str(slot.get("cond_sl_algo_id") or "")
            lv["maker_tp_live"] = bool(slot.get("maker_tp_live", False) and tp_id and not tp_id.startswith("tp-po-"))
            lv["maker_tp_order_id"] = tp_id if lv["maker_tp_live"] else ""
            lv["maker_tp_touched_mono"] = 0.0
            lv["cond_sl_live"] = bool(slot.get("cond_sl_live", False) and sl_id and not sl_id.startswith("sl-cond-"))
            lv["cond_sl_algo_id"] = sl_id if lv["cond_sl_live"] else ""
            lv["cond_sl_touched_mono"] = 0.0
            lv["opened_mono_ms"] = mono_ms()

    def mark_entry_missed(self, slot_id: str, backoff_ms: float = 1200.0):
        lv = self.levels.get(slot_id)
        if lv and lv["status"] == "ENTERING":
            now_m = mono_ms()
            lv["status"] = "IDLE"
            lv["last_closed_mono"] = now_m
            lv["cooldown_until_mono"] = now_m + max(1200.0, float(backoff_ms))

    def clear_external_exit(self, slot_id: str):
        """Cleanly resets a slot closed externally or manually, including ephemeral slots & trailing peaks."""
        lv = self.levels.get(slot_id)
        if lv:
            if lv.get("ephemeral_recovered"):
                self.levels.pop(slot_id, None)
            else:
                now_m = mono_ms()
                lv["status"] = "IDLE"
                lv["entry_price"] = 0.0
                lv["filled_qty"] = 0.0
                lv["contracts"] = 0.0
                lv["opened_mono_ms"] = 0.0
                lv["last_closed_mono"] = now_m
                lv["cooldown_until_mono"] = now_m + 1200.0
                lv["exit_retry_until_mono"] = 0.0
                lv["maker_tp_order_id"] = ""
                lv["maker_tp_live"] = False
                lv["maker_tp_touched_mono"] = 0.0
                lv["cond_sl_algo_id"] = ""
                lv["cond_sl_live"] = False
                lv["cond_sl_touched_mono"] = 0.0
                lv.pop("recovered", None)
        if not self._has_active_slots("LONG"):
            self.long_peak_price = 0.0
            self.long_trail_stop_price = 0.0
        if not self._has_active_slots("SHORT"):
            self.short_trough_price = 0.0
            self.short_trail_stop_price = 0.0

    def mark_exited(self, slot_id: str, net_pnl: float, is_sl: bool = False):
        lv = self.levels.get(slot_id)
        now_m = mono_ms()
        if lv:
            if lv.get("ephemeral_recovered"):
                self.levels.pop(slot_id, None)
            else:
                lv["status"] = "IDLE"
                lv["entry_price"] = 0.0
                lv["filled_qty"] = 0.0
                lv["contracts"] = 0.0
                lv["opened_mono_ms"] = 0.0
                lv["last_closed_mono"] = now_m
                lv["cooldown_until_mono"] = now_m + 1200.0
                lv["exit_retry_until_mono"] = 0.0
                lv["maker_tp_order_id"] = ""
                lv["maker_tp_live"] = False
                lv["maker_tp_touched_mono"] = 0.0
                lv["cond_sl_algo_id"] = ""
                lv["cond_sl_live"] = False
                lv["cond_sl_touched_mono"] = 0.0
                lv.pop("recovered", None)
        if is_sl and self.active_grid_mode == "MEAN_REVERSION":
            # Prevent instant crash-low recenter & second falling-knife SL (120s cooloff)
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + 120_000.0)
        if not is_sl:
            self.completed_cycles += 1
        self.total_net_pnl = round(self.total_net_pnl + net_pnl, 4)
        if not self._has_active_slots("LONG"):
            self.long_peak_price = 0.0
            self.long_trail_stop_price = 0.0
        if not self._has_active_slots("SHORT"):
            self.short_trough_price = 0.0
            self.short_trail_stop_price = 0.0
        if self.active_grid_mode == "TREND_BREAKOUT" and not self._has_active_slots():
            self._needs_post_trend_recenter = True

    def revert_exit_failed(self, slot_id: str, backoff_ms: float = 5000.0):
        lv = self.levels.get(slot_id)
        if lv and lv["status"] == "EXITING":
            lv["status"] = "OPEN"
            lv["exit_retry_until_mono"] = mono_ms() + max(1000.0, float(backoff_ms))

    def get_snapshot(self) -> dict:
        open_long = sum(1 for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING"))
        open_short = sum(1 for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING"))
        # Display order from highest price at top to lowest price at bottom
        if self.active_grid_mode == "TREND_BREAKOUT":
            ordered = sorted(
                self.levels.values(),
                key=lambda x: (0 if x["side"] == "LONG" else 1, -x["level_idx"] if x["side"] == "LONG" else x["level_idx"]),
            )
        else:
            ordered = sorted(
                self.levels.values(),
                key=lambda x: (0 if x["side"] == "SHORT" else 1, -x["level_idx"] if x["side"] == "SHORT" else x["level_idx"]),
            )
        bbw = self.last_bbw_info or {}
        return {
            "center_price": self.center_price,
            "active_grid_mode": self.active_grid_mode,
            "regime_reason": self.last_regime_reason,
            "long_peak_price": self.long_peak_price,
            "short_trough_price": self.short_trough_price,
            "long_trail_stop_price": self.long_trail_stop_price,
            "short_trail_stop_price": self.short_trail_stop_price,
            "bbw_timeframe": bbw.get("timeframe", ""),
            "bbw_pct": bbw.get("bbw_pct"),
            "bbw_percentile": bbw.get("bbw_percentile"),
            "bb_upper": bbw.get("bb_upper"),
            "bb_lower": bbw.get("bb_lower"),
            "ema_fast": bbw.get("ema_fast"),
            "ema_slow": bbw.get("ema_slow"),
            "ema_gap_pct": bbw.get("ema_gap_pct"),
            "trend_bias": bbw.get("trend_bias", "RANGE"),
            "breakout_expansion": bbw.get("breakout_expansion", "NONE"),
            "vcp_contraction": bool(bbw.get("vcp_contraction", False)),
            "open_long_count": open_long,
            "open_short_count": open_short,
            "completed_cycles": self.completed_cycles,
            "grid_net_pnl": self.total_net_pnl,
            "levels": [
                {
                    "slot_id": lv["slot_id"],
                    "side": lv["side"],
                    "grid_mode": lv.get("grid_mode", self.active_grid_mode),
                    "trigger_price": lv["trigger_price"],
                    "tp_price": lv["tp_price"],
                    "sl_price": lv["sl_price"],
                    "order_usdt": lv.get("order_usdt", 0.0),
                    "weight_pct": lv.get("weight_pct", 0.0),
                    "maker_tp_live": bool(lv.get("maker_tp_live", False)),
                    "cond_sl_live": bool(lv.get("cond_sl_live", False)),
                    "status": lv["status"],
                    "entry_price": lv["entry_price"],
                }
                for lv in ordered
            ],
        }
