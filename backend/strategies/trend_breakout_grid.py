"""
Standalone Trend Breakout Grid Strategy (`TREND_GRID` — 顺势突破网格).

Completely isolated trading logic — zero coupling to MEAN_REVERSION, REVERSE_OSCILLATING,
ADAPTIVE_GRID, or lagging EMA trend_bias blocking:
- L1..Ln above center + BB-adaptive Dead Zone / Bollinger Upper Band (涨破追多)
- S1..Sn below center - BB-adaptive Dead Zone / Bollinger Lower Band (跌破追空)
- Front-loaded decaying pyramid sizing (e.g. 50% / 30% / 20%)
- Strictly monotonic pyramid progression (max_active_idx + 1) with L2..Ln / S2..Sn anchored to prev_entry +/- step
- Livermore floating-profit breakeven SL step-up, solo L1 progressive risk tightening,
  multi-leg VWAP profit-lock floor, and All-or-Nothing trailing take-profit
- Post-trend flat recenter & 90s/180s post-SL whipsaw cooldowns
"""
import time
from typing import Dict, List, Optional, Tuple

from backend.math_engine import mono_ms
from backend.models import SymbolStrategy
from backend.strategies.base import BaseGridStrategy, calc_pyramid_weights


class TrendBreakoutGridStrategy(BaseGridStrategy):
    strategy_type = "TREND_GRID"
    default_grid_mode = "TREND_BREAKOUT"
    default_regime_reason = "手动指定顺势突破网格（涨破追多 / 跌破追空 · BB波动率自适应死区）"

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return False

    def uses_livermore_sl_amend(self) -> bool:
        return True

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        e_px = float(lv.get("entry_price") or 0.0)
        sl_px = float(lv.get("sl_price") or 0.0)
        side = str(lv.get("side") or "LONG")
        is_be = (side == "LONG" and sl_px >= e_px) or (side == "SHORT" and sl_px <= e_px)
        return "TREND_BE_SL" if is_be else "TREND_SL"

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        return ("顺势突破开仓", "突破做多" if side == "LONG" else "跌破做空")

    def get_exit_log_tag(self) -> str:
        return "多空网格平仓"

    def get_mode_display_name(self) -> str:
        return "顺势突破"

    def format_fill_log(
        self,
        cfg: SymbolStrategy,
        slot_id: str,
        side: str,
        avg_price: float,
        filled_qty: float,
        lv: dict,
        prot_suffix: str,
    ) -> str:
        return (
            f"[顺势突破成交] {self.symbol} [{slot_id}] {side} 均价: {avg_price:.{cfg.price_precision}f} | "
            f"数量: {filled_qty} | 假突破/保本止损: {lv.get('sl_price', 0):.{cfg.price_precision}f} | "
            f"追踪止盈: 回撤 {cfg.trend_trailing_mult * cfg.grid_step_pct * 100:.2f}%{prot_suffix}"
        )

    def _get_valid_bbw(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> Optional[dict]:
        """Returns fresh, timeframe-aligned Bollinger Band info for breakout band sizing."""
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

        if bbw_info is not None and _is_valid(bbw_info):
            self.last_bbw_info = dict(bbw_info)
            return self.last_bbw_info
        if _is_valid(self.last_bbw_info):
            return self.last_bbw_info
        self.last_bbw_info = None
        return None

    def _compute_user_sig_params(self, cfg: SymbolStrategy) -> tuple:
        return (
            cfg.strategy_type,
            "TREND_BREAKOUT",
            cfg.grid_side_mode,
            int(cfg.grid_levels),
            round(float(cfg.order_usdt), 4),
            round(float(cfg.grid_step_pct), 6),
            round(float(cfg.grid_range_pct), 6),
            round(float(cfg.grid_stop_loss_pct), 6),
            round(float(cfg.trend_dead_zone_mult), 3),
            round(float(cfg.trend_trailing_mult), 3),
            bool(cfg.trend_pyramid_decay),
            bool(cfg.trend_use_bb_breakout),
            int(cfg.price_precision),
            str(cfg.bbw_timeframe),
        )

    def _compute_sig_params(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> tuple:
        info = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        prec = self._calc_prec(cfg)
        bb_u = 0.0
        bb_l = 0.0
        if cfg.trend_use_bb_breakout and info:
            bb_u = round(float(info.get("bb_upper") or 0.0), prec)
            bb_l = round(float(info.get("bb_lower") or 0.0), prec)
        return self._compute_user_sig_params(cfg) + (bb_u, bb_l)

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
        sl_pct = float(cfg.grid_stop_loss_pct)
        rng = float(cfg.grid_range_pct)
        count = max(1, int(cfg.grid_levels))

        self.long_peak_price = 0.0
        self.short_trough_price = 0.0
        self.long_trail_stop_price = 0.0
        self.short_trail_stop_price = 0.0

        dz_mult = float(cfg.trend_dead_zone_mult)
        dz_ratio = dz_mult * step
        bb_upper = float(info.get("bb_upper") or 0.0) if (cfg.trend_use_bb_breakout and info) else 0.0
        bb_lower = float(info.get("bb_lower") or 0.0) if (cfg.trend_use_bb_breakout and info) else 0.0
        bb_pct_rank = float(info.get("bbw_percentile", 0.0)) if info else 0.0

        eff_dz_ratio = dz_ratio
        if cfg.trend_use_bb_breakout and bb_upper > bb_lower > 0:
            bb_mid_ref = float(info.get("bb_middle") or ((bb_upper + bb_lower) * 0.5) or self.center_price)
            live_bbw = (bb_upper - bb_lower) / max(1e-12, bb_mid_ref)
            if live_bbw > 0:
                eff_dz_ratio = min(dz_ratio, max(min(dz_ratio, step), live_bbw * 0.75))

        if bb_pct_rank > 55.0:
            bb_cap = eff_dz_ratio * 1.15
            max_first_span = max(eff_dz_ratio * 1.15, step * 1.25)
        elif bb_pct_rank > 25.0:
            bb_cap = eff_dz_ratio * 1.25
            max_first_span = max(eff_dz_ratio * 1.25, step * 1.35)
        else:
            bb_cap = max(eff_dz_ratio * 1.6, 0.022)
            max_first_span = max(eff_dz_ratio * 1.5, min(rng, bb_cap), step * 2.0)

        if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
            base_l1 = self.center_price * (1.0 + eff_dz_ratio)
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

        if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
            base_s1 = self.center_price * max(0.05, 1.0 - eff_dz_ratio)
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

        self.levels = new_levels

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        self.active_grid_mode = "TREND_BREAKOUT"
        self.last_regime_reason = self.default_regime_reason
        user_sig = self._compute_user_sig_params(cfg)
        sig_params = self._compute_sig_params(cfg, bbw_info=valid_bbw)

        if self.center_price <= 0 and current_mid > 0:
            self.center_price = current_mid
            self._build_levels(cfg, bbw_info=valid_bbw)
            self._last_user_sig_params = user_sig
            self._last_sig_params = sig_params
            return

        if not self._has_active_slots():
            user_changed = (self._last_user_sig_params is None) or (user_sig != self._last_user_sig_params)
            if sig_params != self._last_sig_params:
                bb_only_update = not user_changed
                if not bb_only_update:
                    self._needs_post_trend_recenter = False
                if self.center_price > 0:
                    self._build_levels(cfg, bbw_info=valid_bbw, preserve_cooldowns=bb_only_update)
                self._last_user_sig_params = user_sig
                self._last_sig_params = sig_params
                if not bb_only_update:
                    self.clear_cooldowns()

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        if new_center <= 0 or self._has_active_slots():
            return False
        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        self.active_grid_mode = "TREND_BREAKOUT"
        self.last_regime_reason = self.default_regime_reason
        self.center_price = new_center
        self._needs_post_trend_recenter = False
        self._post_sl_cooldown_until_mono = 0.0
        self._build_levels(cfg, bbw_info=valid_bbw)
        self._last_user_sig_params = self._compute_user_sig_params(cfg)
        self._last_sig_params = self._compute_sig_params(cfg, bbw_info=valid_bbw)
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
        if mid <= 0 or bid <= 0 or ask <= 0:
            return [], []

        valid_bbw = self._get_valid_bbw(cfg, bbw_info=bbw_info)
        if self.center_price <= 0 or not self.levels:
            self.active_grid_mode = "TREND_BREAKOUT"
            self.last_regime_reason = self.default_regime_reason
            self.center_price = mid
            self._build_levels(cfg, bbw_info=valid_bbw)
            self._last_user_sig_params = self._compute_user_sig_params(cfg)
            self._last_sig_params = self._compute_sig_params(cfg, bbw_info=valid_bbw)
            return [], []

        now_m = mono_ms()
        if force_exit_all:
            return [], self._evaluate_kill_switch_exits(bid, ask, now_m)

        exits: List[dict] = []
        entries: List[dict] = []
        prec = self._calc_prec(cfg)
        step_now = max(0.0001, float(cfg.grid_step_pct))

        # 1. Trailing Stop, Solo/Pyramid SL Step-Up & False-Breakout Stop-Loss on OPEN slots
        trail_ratio = max(0.0005, float(cfg.trend_trailing_mult) * step_now)
        half_lock_move = max(0.45 * step_now, round_trip_fee + 0.0020)
        solo_lock_move = max(0.80 * step_now, round_trip_fee + 0.0035)
        profit_floor_trigger = max(0.55 * step_now, 0.0040)
        pyramid_clear_move = max(0.30 * step_now, round_trip_fee + 0.0010)
        be_slip_buf = 0.0006
        sl_room_buf = max(0.0025, step_now * 0.30)
        max_l1_sl_dist = min(
            float(cfg.grid_stop_loss_pct),
            max(float(cfg.trend_dead_zone_mult) * step_now * 1.30, step_now * 2.55, 0.012),
        )

        open_longs = [lv for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] == "OPEN"]
        if open_longs:
            max_entry_l = max(float(lv.get("entry_price") or 0.0) for lv in open_longs)
            self.long_peak_price = max(self.long_peak_price, bid, max_entry_l)
            raw_trail_l = round(self.long_peak_price * max(0.05, 1.0 - trail_ratio), prec)
            tot_notional_l = sum(
                self._real_slot_notional(lv, cfg) for lv in open_longs if float(lv.get("entry_price") or 0.0) > 0
            )
            vwap_l = (
                sum(
                    float(lv.get("entry_price") or 0.0) * self._real_slot_notional(lv, cfg)
                    for lv in open_longs
                    if float(lv.get("entry_price") or 0.0) > 0
                )
                / max(1e-12, tot_notional_l)
            ) if tot_notional_l > 0 else max_entry_l
            vwap_be_l = round(vwap_l * (1.0 + round_trip_fee + be_slip_buf), prec) if vwap_l > 0 else 0.0
            if (
                len(open_longs) >= 2
                and vwap_be_l > 0
                and self.long_peak_price >= vwap_be_l * (1.0 + profit_floor_trigger)
                and self.long_peak_price >= max_entry_l * (1.0 + pyramid_clear_move)
            ):
                profit_lock_l = round(vwap_be_l + 0.25 * (self.long_peak_price - vwap_be_l), prec)
                if self.long_peak_price >= max_entry_l * (1.0 + half_lock_move):
                    profit_lock_l = max(profit_lock_l, round(max_entry_l * (1.0 + round_trip_fee * 0.5), prec))
                long_trail_px = max(raw_trail_l, profit_lock_l)
            else:
                long_trail_px = raw_trail_l
            self.long_trail_stop_price = long_trail_px

            if len(open_longs) == 1:
                lv = open_longs[0]
                e_entry = float(lv.get("entry_price") or 0.0)
                if e_entry > 0:
                    cur_sl = float(lv.get("sl_price") or 0.0)
                    target_sl_l = round(e_entry * max(0.05, 1.0 - max_l1_sl_dist), prec)
                    if self.long_peak_price >= e_entry * (1.0 + solo_lock_move):
                        target_sl_l = max(target_sl_l, round(e_entry * (1.0 + round_trip_fee + be_slip_buf), prec))
                    elif self.long_peak_price >= e_entry * (1.0 + half_lock_move):
                        target_sl_l = max(target_sl_l, round(e_entry * max(0.05, 1.0 - 0.50 * step_now), prec))
                    if cur_sl < target_sl_l - 1e-12 and bid >= target_sl_l * (1.0 + sl_room_buf):
                        lv["sl_price"] = target_sl_l
                        lv["sl_needs_amend"] = True
            elif (
                len(open_longs) >= 2
                and vwap_be_l > 0
                and self.long_peak_price >= max_entry_l * (1.0 + pyramid_clear_move)
                and bid >= vwap_be_l * (1.0 + sl_room_buf)
            ):
                for lv in open_longs:
                    cur_sl = float(lv.get("sl_price") or 0.0)
                    if cur_sl < vwap_be_l - 1e-12:
                        lv["sl_price"] = vwap_be_l
                        lv["sl_needs_amend"] = True

            if self.long_peak_price > 0 and bid <= long_trail_px:
                remaining_longs = [
                    lv for lv in open_longs
                    if lv["status"] == "OPEN" and now_m >= float(lv.get("exit_retry_until_mono") or 0.0)
                ]
                if remaining_longs:
                    side_net_pnl = sum(
                        ((bid - float(lv.get("entry_price") or 0.0)) / max(1e-12, float(lv.get("entry_price") or 0.0)) - round_trip_fee)
                        * self._real_slot_notional(lv, cfg)
                        for lv in remaining_longs
                        if float(lv.get("entry_price") or 0.0) > 0
                    )
                    side_notional = sum(
                        self._real_slot_notional(lv, cfg)
                        for lv in remaining_longs
                        if float(lv.get("entry_price") or 0.0) > 0
                    )
                    min_basket_net = 0.0 if len(remaining_longs) >= 2 else (side_notional * max(0.0012, 0.15 * step_now))
                    if side_notional > 0 and side_net_pnl > max(0.0, min_basket_net):
                        for lv in remaining_longs:
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "TREND_TRAIL_TP", "target_price": bid})

            for lv in open_longs:
                if lv["status"] != "OPEN" or now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                sl_px = float(lv.get("sl_price") or 0.0)
                if sl_px > 0 and bid <= sl_px:
                    if self._should_fire_software_sl(lv, bid, sl_px, True, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
                        reason = "TREND_BE_SL" if sl_px >= float(lv.get("entry_price") or 0.0) else "TREND_SL"
                        exits.append({**lv, "exit_reason": reason, "target_price": bid})
                elif float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and bid > sl_px * 1.0005:
                    lv["cond_sl_touched_mono"] = 0.0
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
            raw_trail_s = round(self.short_trough_price * (1.0 + trail_ratio), prec)
            tot_notional_s = sum(
                self._real_slot_notional(lv, cfg) for lv in open_shorts if float(lv.get("entry_price") or 0.0) > 0
            )
            vwap_s = (
                sum(
                    float(lv.get("entry_price") or 0.0) * self._real_slot_notional(lv, cfg)
                    for lv in open_shorts
                    if float(lv.get("entry_price") or 0.0) > 0
                )
                / max(1e-12, tot_notional_s)
            ) if tot_notional_s > 0 else min_entry_s
            vwap_be_s = round(vwap_s * max(0.05, 1.0 - round_trip_fee - be_slip_buf), prec) if vwap_s > 0 else 0.0
            if (
                len(open_shorts) >= 2
                and vwap_be_s > 0
                and self.short_trough_price > 0
                and self.short_trough_price <= vwap_be_s * max(0.05, 1.0 - profit_floor_trigger)
                and self.short_trough_price <= min_entry_s * max(0.05, 1.0 - pyramid_clear_move)
            ):
                profit_lock_s = round(vwap_be_s - 0.25 * (vwap_be_s - self.short_trough_price), prec)
                if self.short_trough_price <= min_entry_s * max(0.05, 1.0 - half_lock_move):
                    profit_lock_s = min(
                        profit_lock_s,
                        round(min_entry_s * max(0.05, 1.0 - round_trip_fee * 0.5), prec),
                    )
                short_trail_px = min(raw_trail_s, profit_lock_s)
            else:
                short_trail_px = raw_trail_s
            self.short_trail_stop_price = short_trail_px

            if len(open_shorts) == 1:
                lv = open_shorts[0]
                e_entry = float(lv.get("entry_price") or 0.0)
                if e_entry > 0 and self.short_trough_price > 0:
                    cur_sl = float(lv.get("sl_price") or 0.0)
                    target_sl_s = round(e_entry * (1.0 + max_l1_sl_dist), prec)
                    if self.short_trough_price <= e_entry * max(0.05, 1.0 - solo_lock_move):
                        target_sl_s = min(
                            target_sl_s,
                            round(e_entry * max(0.05, 1.0 - round_trip_fee - be_slip_buf), prec),
                        )
                    elif self.short_trough_price <= e_entry * max(0.05, 1.0 - half_lock_move):
                        target_sl_s = min(target_sl_s, round(e_entry * (1.0 + 0.50 * step_now), prec))
                    if (cur_sl <= 0 or cur_sl > target_sl_s + 1e-12) and ask <= target_sl_s * max(0.05, 1.0 - sl_room_buf):
                        lv["sl_price"] = target_sl_s
                        lv["sl_needs_amend"] = True
            elif (
                len(open_shorts) >= 2
                and vwap_be_s > 0
                and self.short_trough_price > 0
                and self.short_trough_price <= min_entry_s * max(0.05, 1.0 - pyramid_clear_move)
                and ask <= vwap_be_s * max(0.05, 1.0 - sl_room_buf)
            ):
                for lv in open_shorts:
                    cur_sl = float(lv.get("sl_price") or 0.0)
                    if cur_sl <= 0 or cur_sl > vwap_be_s + 1e-12:
                        lv["sl_price"] = vwap_be_s
                        lv["sl_needs_amend"] = True

            if self.short_trough_price > 0 and ask >= short_trail_px:
                remaining_shorts = [
                    lv for lv in open_shorts
                    if lv["status"] == "OPEN" and now_m >= float(lv.get("exit_retry_until_mono") or 0.0)
                ]
                if remaining_shorts:
                    side_net_pnl = sum(
                        ((float(lv.get("entry_price") or 0.0) - ask) / max(1e-12, float(lv.get("entry_price") or 0.0)) - round_trip_fee)
                        * self._real_slot_notional(lv, cfg)
                        for lv in remaining_shorts
                        if float(lv.get("entry_price") or 0.0) > 0
                    )
                    side_notional = sum(
                        self._real_slot_notional(lv, cfg)
                        for lv in remaining_shorts
                        if float(lv.get("entry_price") or 0.0) > 0
                    )
                    min_basket_net = 0.0 if len(remaining_shorts) >= 2 else (side_notional * max(0.0012, 0.15 * step_now))
                    if side_notional > 0 and side_net_pnl > max(0.0, min_basket_net):
                        for lv in remaining_shorts:
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "TREND_TRAIL_TP", "target_price": ask})

            for lv in open_shorts:
                if lv["status"] != "OPEN" or now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                sl_px = float(lv.get("sl_price") or 0.0)
                if sl_px > 0 and ask >= sl_px:
                    if self._should_fire_software_sl(lv, ask, sl_px, False, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
                        reason = "TREND_BE_SL" if sl_px <= float(lv.get("entry_price") or 0.0) else "TREND_SL"
                        exits.append({**lv, "exit_reason": reason, "target_price": ask})
                elif float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and ask < sl_px * 0.9995:
                    lv["cond_sl_touched_mono"] = 0.0
        elif not self._has_active_slots("SHORT"):
            self.short_trough_price = 0.0
            self.short_trail_stop_price = 0.0

        # 2. Flat Auto-Recenter & Post-Trend Recenter
        if allow_recenter and not self._has_active_slots():
            first_dists = [
                abs(float(lv["trigger_price"]) - self.center_price) / self.center_price
                for lv in self.levels.values()
                if int(lv.get("level_idx") or 0) == 1 and float(lv.get("trigger_price") or 0.0) > 0
            ]
            max_first_dist = max(first_dists) if first_dists else (float(cfg.trend_dead_zone_mult) * step_now)
            eff_range = max(float(cfg.grid_range_pct), max_first_dist * 1.15)
            drifted_out = (
                cfg.grid_recenter_enabled
                and now_m >= self._post_sl_cooldown_until_mono
                and self.center_price > 0
                and abs(mid - self.center_price) / self.center_price > eff_range
            )
            post_trend_recenter = self._needs_post_trend_recenter and (now_m >= self._post_sl_cooldown_until_mono)
            if drifted_out or post_trend_recenter:
                self.center_price = mid
                self._needs_post_trend_recenter = False
                self._build_levels(cfg, bbw_info=valid_bbw)
                self._last_user_sig_params = self._compute_user_sig_params(cfg)
                self._last_sig_params = self._compute_sig_params(cfg, bbw_info=valid_bbw)
                return [], exits

        if (
            not allow_entries
            or now_m < self._post_sl_cooldown_until_mono
            or exits
            or any(lv.get("status") == "EXITING" and lv.get("exiting_is_sl") for lv in self.levels.values())
        ):
            return [], exits

        # 3. Bidirectional Strictly Monotonic Breakout Pyramid Entries (unrestricted by lagging EMA)
        has_long_active = self._has_active_slots("LONG")
        has_short_active = self._has_active_slots("SHORT")

        if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
            max_active_l = max(
                (
                    int(lv.get("level_idx") or 0)
                    for lv in self.levels.values()
                    if lv["side"] == "LONG" and lv["status"] in ("ENTERING", "OPEN", "EXITING")
                ),
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
                if now_m < self._slot_cooldown_until(lv):
                    break
                min_req_px = float(lv["trigger_price"])
                if lv["level_idx"] > 1:
                    prev_lv = self.levels.get(f"L{lv['level_idx'] - 1}")
                    if not prev_lv or prev_lv.get("status") != "OPEN":
                        break
                    prev_entry = float(prev_lv.get("entry_price") or 0.0)
                    if prev_entry > 0:
                        step_anchor_l = round(prev_entry * (1.0 + step_now), prec)
                        min_req_px = max(min(min_req_px, step_anchor_l), round(prev_entry * (1.0 + step_now * 0.90), prec))
                if ask >= min_req_px:
                    lv["status"] = "ENTERING"
                    has_long_active = True
                    res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                    entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": ask})
                    break

        if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
            max_active_s = max(
                (
                    int(lv.get("level_idx") or 0)
                    for lv in self.levels.values()
                    if lv["side"] == "SHORT" and lv["status"] in ("ENTERING", "OPEN", "EXITING")
                ),
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
                if now_m < self._slot_cooldown_until(lv):
                    break
                max_req_px = float(lv["trigger_price"])
                if lv["level_idx"] > 1:
                    prev_lv = self.levels.get(f"S{lv['level_idx'] - 1}")
                    if not prev_lv or prev_lv.get("status") != "OPEN":
                        break
                    prev_entry = float(prev_lv.get("entry_price") or 0.0)
                    if prev_entry > 0:
                        step_anchor_s = round(prev_entry * max(0.05, 1.0 - step_now), prec)
                        max_req_px = min(
                            max(max_req_px, step_anchor_s),
                            round(prev_entry * max(0.05, 1.0 - step_now * 0.90), prec),
                        )
                if bid <= max_req_px:
                    lv["status"] = "ENTERING"
                    has_short_active = True
                    res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                    entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": bid})
                    break

        return entries, exits

    def _sync_trend_pyramid_triggers(self, cfg: SymbolStrategy):
        """Re-anchors IDLE higher pyramid triggers relative to the highest OPEN leg's actual entry_price."""
        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))

        open_longs = [
            lv for lv in self.levels.values()
            if lv["side"] == "LONG" and lv["status"] == "OPEN" and float(lv.get("entry_price") or 0.0) > 0
        ]
        if open_longs:
            top_l = max(open_longs, key=lambda x: int(x.get("level_idx") or 0))
            top_idx = int(top_l.get("level_idx") or 1)
            anchor = float(top_l["entry_price"])
            prev_px = anchor
            for lv in sorted(
                (x for x in self.levels.values() if x["side"] == "LONG" and int(x.get("level_idx") or 0) > top_idx),
                key=lambda x: int(x.get("level_idx") or 0),
            ):
                if lv["status"] != "IDLE":
                    prev_px = float(lv.get("entry_price") or lv.get("trigger_price") or prev_px)
                    continue
                delta = int(lv.get("level_idx") or top_idx + 1) - top_idx
                exp_trig = round(anchor * (1.0 + delta * step), prec)
                if float(lv.get("trigger_price") or 0.0) > exp_trig:
                    lv["trigger_price"] = exp_trig
                    lv["sl_price"] = round(prev_px, prec)
                prev_px = float(lv["trigger_price"])

        open_shorts = [
            lv for lv in self.levels.values()
            if lv["side"] == "SHORT" and lv["status"] == "OPEN" and float(lv.get("entry_price") or 0.0) > 0
        ]
        if open_shorts:
            top_s = max(open_shorts, key=lambda x: int(x.get("level_idx") or 0))
            top_idx = int(top_s.get("level_idx") or 1)
            anchor = float(top_s["entry_price"])
            prev_px = anchor
            for lv in sorted(
                (x for x in self.levels.values() if x["side"] == "SHORT" and int(x.get("level_idx") or 0) > top_idx),
                key=lambda x: int(x.get("level_idx") or 0),
            ):
                if lv["status"] != "IDLE":
                    prev_px = float(lv.get("entry_price") or lv.get("trigger_price") or prev_px)
                    continue
                delta = int(lv.get("level_idx") or top_idx + 1) - top_idx
                exp_trig = round(anchor * max(0.05, 1.0 - delta * step), prec)
                if float(lv.get("trigger_price") or 0.0) < exp_trig:
                    lv["trigger_price"] = exp_trig
                    lv["sl_price"] = round(prev_px, prec)
                prev_px = float(lv["trigger_price"])

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
        lv["tp_price"] = 0.0

        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        sl_buf = max(0.0035, step * 0.55)
        be_slip_buf = 0.0006
        tick_unit = 10.0 ** (-prec)
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
                max_earlier_sl = 0.0
                for earlier in self.levels.values():
                    if earlier["side"] == "LONG" and earlier["status"] == "OPEN" and earlier["level_idx"] < lvl_idx:
                        e_entry = float(earlier.get("entry_price") or 0.0)
                        if e_entry <= 0:
                            continue
                        be_px = round(e_entry * (1.0 + round_trip_fee + be_slip_buf), prec)
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
                        be_px = round(e_entry * max(0.05, 1.0 - round_trip_fee - be_slip_buf), prec)
                        step_px = round(prev_anchor, prec) if earlier["level_idx"] < lvl_idx - 1 else be_px
                        cur_sl = float(earlier.get("sl_price") or 0.0)
                        target_sl = min(cur_sl, be_px, step_px) if cur_sl > 0 else min(be_px, step_px)
                        earlier["sl_price"] = max(target_sl, safe_floor_s)
                        min_earlier_sl = min(min_earlier_sl, float(earlier["sl_price"]))
                base_sl = (
                    min(round(prev_anchor, prec), min_earlier_sl)
                    if min_earlier_sl < float("inf")
                    else round(prev_anchor, prec)
                )
                lv["sl_price"] = max(base_sl, safe_floor_s)

        self._sync_trend_pyramid_triggers(cfg)

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
        if self.center_price <= 0 or not self.levels:
            self.active_grid_mode = "TREND_BREAKOUT"
            self.last_regime_reason = self.default_regime_reason
            self.center_price = entry_price
            self._build_levels(cfg)

        target_slot = self._select_or_create_recovery_slot(
            side=side,
            entry_price=entry_price,
            filled_qty=filled_qty,
            ct_val=ct_val,
            cfg=cfg,
            preferred_slot=preferred_slot,
            mgn_mode=mgn_mode,
            grid_mode="TREND_BREAKOUT",
            has_fixed_tp=False,
        )
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
        lv["tp_price"] = 0.0

        if side == "LONG":
            self.long_peak_price = max(self.long_peak_price, entry_price)
            lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
        else:
            self.short_trough_price = (
                entry_price if self.short_trough_price <= 0 else min(self.short_trough_price, entry_price)
            )
            lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
        self._sync_trend_pyramid_triggers(cfg)
        return target_slot

    def clear_external_exit(self, slot_id: str):
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
                lv.pop("exiting_is_sl", None)
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
                lv["cooldown_until_mono"] = now_m + (180_000.0 if is_sl else 1200.0)
                lv["exit_retry_until_mono"] = 0.0
                lv["maker_tp_order_id"] = ""
                lv["maker_tp_live"] = False
                lv["maker_tp_touched_mono"] = 0.0
                lv["cond_sl_algo_id"] = ""
                lv["cond_sl_live"] = False
                lv["cond_sl_touched_mono"] = 0.0
                lv.pop("sl_needs_amend", None)
                lv.pop("exiting_is_sl", None)
                lv.pop("recovered", None)
        if is_sl and not self._has_active_slots():
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + 90_000.0)
        if not is_sl:
            self.completed_cycles += 1
        self.total_net_pnl = round(self.total_net_pnl + net_pnl, 4)
        if not self._has_active_slots("LONG"):
            self.long_peak_price = 0.0
            self.long_trail_stop_price = 0.0
        if not self._has_active_slots("SHORT"):
            self.short_trough_price = 0.0
            self.short_trail_stop_price = 0.0
        if not self._has_active_slots():
            self._needs_post_trend_recenter = True

    def export_state(self) -> dict:
        return {
            "strategy_type": self.strategy_type,
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": "TREND_BREAKOUT",
            "long_peak_price": float(self.long_peak_price or 0.0),
            "short_trough_price": float(self.short_trough_price or 0.0),
            "completed_cycles": int(self.completed_cycles or 0),
            "total_net_pnl": float(self.total_net_pnl or 0.0),
            "last_bbw_info": dict(self.last_bbw_info) if isinstance(self.last_bbw_info, dict) else None,
            "needs_post_trend_recenter": bool(self._needs_post_trend_recenter),
            "open_slots": self._serialize_open_slots(),
            "disarmed_slots": [],
        }

    def restore_state(self, data: dict, cfg: SymbolStrategy):
        if not isinstance(data, dict):
            return
        if isinstance(data.get("last_bbw_info"), dict):
            self._get_valid_bbw(cfg, bbw_info=data.get("last_bbw_info"))
        self.active_grid_mode = "TREND_BREAKOUT"
        self.last_regime_reason = self.default_regime_reason
        c_px = float(data.get("center_price") or 0.0)
        if c_px > 0:
            self.center_price = c_px
            self._build_levels(cfg, bbw_info=self.last_bbw_info)
            self._last_user_sig_params = self._compute_user_sig_params(cfg)
            self._last_sig_params = self._compute_sig_params(cfg, bbw_info=self.last_bbw_info)
        self.completed_cycles = int(data.get("completed_cycles") or self.completed_cycles)
        self.total_net_pnl = float(data.get("total_net_pnl") or self.total_net_pnl)
        self.long_peak_price = float(data.get("long_peak_price") or 0.0)
        self.short_trough_price = float(data.get("short_trough_price") or 0.0)
        self._needs_post_trend_recenter = bool(data.get("needs_post_trend_recenter", False))

        for slot in data.get("open_slots") or []:
            if not str(slot.get("slot_id") or ""):
                continue
            self._restore_common_slot_fields(slot, cfg, grid_mode="TREND_BREAKOUT")
        self._sync_trend_pyramid_triggers(cfg)

    def get_snapshot(self) -> dict:
        open_long = sum(1 for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING"))
        open_short = sum(1 for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING"))
        ordered = sorted(
            self.levels.values(),
            key=lambda x: (0 if x["side"] == "LONG" else 1, -x["level_idx"] if x["side"] == "LONG" else x["level_idx"]),
        )
        bbw = self.last_bbw_info or {}
        return {
            "center_price": self.center_price,
            "active_grid_mode": "TREND_BREAKOUT",
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
            "trend_bias": "RANGE",
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
                    "grid_mode": lv.get("grid_mode", "TREND_BREAKOUT"),
                    "trigger_price": lv["trigger_price"],
                    "tp_price": lv["tp_price"],
                    "sl_price": lv["sl_price"],
                    "order_usdt": lv.get("order_usdt", 0.0),
                    "weight_pct": lv.get("weight_pct", 0.0),
                    "maker_tp_live": bool(lv.get("maker_tp_live", False)),
                    "cond_sl_live": bool(lv.get("cond_sl_live", False)),
                    "armed": True,
                    "status": lv["status"],
                    "entry_price": lv["entry_price"],
                }
                for lv in ordered
            ],
        }
