"""
Standalone Reverse Oscillating Grid Strategy (`REVERSE_GRID` — 多空震荡网格 · 保本移动止盈增强版).

Completely isolated trading logic — zero coupling to MEAN_REVERSION, TREND_BREAKOUT,
ADAPTIVE_GRID, BBW, or lagging EMA indicators:
- L1..Ln ABOVE center (涨破开多), S1..Sn BELOW center (跌破开空)
- Strict single-direction mutual exclusion (even in hedge mode): NEVER holds upper LONG + lower SHORT
  simultaneously (eliminating the "buy-high / sell-low" reverse-locking loss trap)
- Uncapped per-slot Breakeven + Trailing Take-Profit:
  * Below activation (`grid_take_profit_pct + rt_fee`): protected by tight per-slot hard SL (`grid_stop_loss_pct`)
  * Once favorable move reaches `grid_take_profit_pct + rt_fee`: activates per-slot trailing TP, locks guaranteed
    net profit floor on exchange-native Conditional SL, and ratchets trailing stop / OKX SL upward with momentum
  * Exits cleanly on retracement via `GRID_TRAIL_TP` (or profit-locked `GRID_BE_SL`), opening profit ceiling
- Dynamic per-slot SL sync on open pre-profit-lock positions when user changes `grid_stop_loss_pct`
  (never widens a profit-locked slot back into a loss)
- Per-slot crossing re-arm hysteresis (`rearm_buf`) & bypassed-inner-level disarming
- Symmetric anti-stacking spacing (0.55 * step) + concurrent ENTERING guard + 120s post-loss-SL cooldown
"""
from typing import Dict, List, Optional, Tuple

from backend.math_engine import mono_ms
from backend.models import SymbolStrategy
from backend.strategies.base import BaseGridStrategy


class ReverseOscillatingGridStrategy(BaseGridStrategy):
    strategy_type = "REVERSE_GRID"
    default_grid_mode = "REVERSE_OSCILLATING"
    default_regime_reason = "多空震荡（涨破开多/跌破开空 · 保本移动止盈 · 禁反向锁亏）"

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        # Trailing TP must NEVER place a fixed ceiling Maker limit order that caps winners;
        # freeing OKX closeable sz also ensures native Conditional SL algos install reliably.
        return False

    def uses_livermore_sl_amend(self) -> bool:
        return False

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        e_px = float(lv.get("entry_price") or 0.0)
        sl_px = float(lv.get("sl_price") or 0.0)
        if lv.get("trail_active"):
            return "GRID_BE_SL"
        if e_px > 0 and sl_px > 0:
            if lv.get("side") == "LONG" and sl_px > e_px:
                return "GRID_BE_SL"
            if lv.get("side") == "SHORT" and sl_px < e_px:
                return "GRID_BE_SL"
        return "GRID_SL"

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        return ("多空震荡开仓", "涨破开多" if side == "LONG" else "跌破开空")

    def get_exit_log_tag(self) -> str:
        return "多空震荡平仓"

    def get_mode_display_name(self) -> str:
        return "多空震荡"

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
            f"[多空震荡成交] {self.symbol} [{slot_id}] {side} 均价: {avg_price:.{cfg.price_precision}f} | "
            f"数量: {filled_qty} | 追踪激活: {lv.get('tp_price', 0):.{cfg.price_precision}f} | "
            f"硬止损: {lv.get('sl_price', 0):.{cfg.price_precision}f}{prot_suffix}"
        )

    def _compute_sig_params(self, cfg: SymbolStrategy) -> tuple:
        return (
            cfg.strategy_type,
            self.active_grid_mode,
            cfg.grid_side_mode,
            int(cfg.grid_levels),
            round(float(cfg.order_usdt), 4),
            round(float(cfg.grid_step_pct), 6),
            round(float(cfg.grid_take_profit_pct), 6),
            round(float(cfg.grid_range_pct), 6),
            round(float(cfg.grid_stop_loss_pct), 6),
            int(cfg.price_precision),
        )

    def _refresh_side_trail_summaries(self):
        long_peaks = [
            float(lv.get("peak_price") or 0.0)
            for lv in self.levels.values()
            if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING") and float(lv.get("peak_price") or 0.0) > 0
        ]
        long_trails = [
            float(lv.get("trail_stop_price") or 0.0)
            for lv in self.levels.values()
            if lv["side"] == "LONG"
            and lv["status"] in ("OPEN", "EXITING")
            and lv.get("trail_active")
            and float(lv.get("trail_stop_price") or 0.0) > 0
        ]
        short_troughs = [
            float(lv.get("trough_price") or 0.0)
            for lv in self.levels.values()
            if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING") and float(lv.get("trough_price") or 0.0) > 0
        ]
        short_trails = [
            float(lv.get("trail_stop_price") or 0.0)
            for lv in self.levels.values()
            if lv["side"] == "SHORT"
            and lv["status"] in ("OPEN", "EXITING")
            and lv.get("trail_active")
            and float(lv.get("trail_stop_price") or 0.0) > 0
        ]
        self.long_peak_price = max(long_peaks) if long_peaks else 0.0
        self.long_trail_stop_price = max(long_trails) if long_trails else 0.0
        self.short_trough_price = min(short_troughs) if short_troughs else 0.0
        self.short_trail_stop_price = min(short_trails) if short_trails else 0.0

    def _build_levels(self, cfg: SymbolStrategy, preserve_cooldowns: bool = False):
        if self.center_price <= 0:
            return
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

        if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
            long_indices = [i for i in range(1, count + 1) if i == 1 or i * step <= rng + 1e-9]
            w_pct = round(100.0 / max(1, len(long_indices)), 1)
            for i in long_indices:
                slot_id = f"L{i}"
                trig = round(self.center_price * (1.0 + i * step), prec)
                cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                new_levels[slot_id] = {
                    "slot_id": slot_id,
                    "side": "LONG",
                    "level_idx": i,
                    "grid_mode": "REVERSE_OSCILLATING",
                    "trigger_price": trig,
                    "tp_price": round(trig * (1.0 + tp_pct), prec),
                    "sl_price": round(trig * max(0.05, 1.0 - sl_pct), prec),
                    "order_usdt": round(float(cfg.order_usdt), 2),
                    "weight_pct": w_pct,
                    "status": "IDLE",
                    "armed": True,
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
                    "trail_active": False,
                    "peak_price": 0.0,
                    "trough_price": 0.0,
                    "trail_stop_price": 0.0,
                }

        if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
            short_indices = [i for i in range(1, count + 1) if i == 1 or i * step <= rng + 1e-9]
            w_pct = round(100.0 / max(1, len(short_indices)), 1)
            for i in short_indices:
                slot_id = f"S{i}"
                trig = round(self.center_price * max(0.05, 1.0 - i * step), prec)
                cd_u, last_c = old_cooldowns.get(slot_id, (0.0, 0.0))
                new_levels[slot_id] = {
                    "slot_id": slot_id,
                    "side": "SHORT",
                    "level_idx": i,
                    "grid_mode": "REVERSE_OSCILLATING",
                    "trigger_price": trig,
                    "tp_price": round(trig * max(0.05, 1.0 - tp_pct), prec),
                    "sl_price": round(trig * (1.0 + sl_pct), prec),
                    "order_usdt": round(float(cfg.order_usdt), 2),
                    "weight_pct": w_pct,
                    "status": "IDLE",
                    "armed": True,
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
                    "trail_active": False,
                    "peak_price": 0.0,
                    "trough_price": 0.0,
                    "trail_stop_price": 0.0,
                }

        self.levels = new_levels
        self._refresh_side_trail_summaries()

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        self.active_grid_mode = "REVERSE_OSCILLATING"
        self.last_regime_reason = self.default_regime_reason
        sig_params = self._compute_sig_params(cfg)

        if self.center_price <= 0 and current_mid > 0:
            self.center_price = current_mid
            self._build_levels(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            return

        if not self._has_active_slots() and sig_params != self._last_sig_params:
            if self.center_price > 0:
                self._build_levels(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            self.clear_cooldowns()

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        if new_center <= 0 or self._has_active_slots():
            return False
        self.active_grid_mode = "REVERSE_OSCILLATING"
        self.last_regime_reason = self.default_regime_reason
        self.center_price = new_center
        self._post_sl_cooldown_until_mono = 0.0
        self._build_levels(cfg)
        sig_params = self._compute_sig_params(cfg)
        self._last_user_sig_params = sig_params
        self._last_sig_params = sig_params
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

        if self.center_price <= 0 or not self.levels:
            self.active_grid_mode = "REVERSE_OSCILLATING"
            self.last_regime_reason = self.default_regime_reason
            self.center_price = mid
            self._build_levels(cfg)
            sig_params = self._compute_sig_params(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            return [], []

        now_m = mono_ms()
        if force_exit_all:
            return [], self._evaluate_kill_switch_exits(bid, ask, now_m)

        exits: List[dict] = []
        entries: List[dict] = []
        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        tp_act = max(0.0030, float(cfg.grid_take_profit_pct))
        rt_fee = max(0.0004, float(round_trip_fee))
        # Guaranteed net-profit lock buffer strictly below activation threshold
        be_lock_buf = min(max(0.0020, tp_act * 0.35), max(0.0010, tp_act * 0.60))
        # Trailing retracement: tight enough to lock net profit immediately, wide enough to ride waves
        trail_ret = min(max(0.0032, tp_act * 0.55), max(0.0025, tp_act * 0.70), 0.0080)

        # 1. Check exits, per-slot Breakeven + Trailing TP, and pre-BE dynamic SL parameter sync
        rev_sl_pct = max(0.002, float(cfg.grid_stop_loss_pct))
        rev_room_buf = max(0.0025, step * 0.30)
        for lv in self.levels.values():
            if lv["status"] != "OPEN":
                continue
            e_px = float(lv.get("entry_price") or 0.0)
            cur_sl_px = float(lv.get("sl_price") or 0.0)
            is_trail_active = bool(lv.get("trail_active", False))

            # Clear any legacy resting Maker TP order once so profits run uncapped & OKX SL installs cleanly
            if lv.get("maker_tp_live") and not lv.get("_legacy_tp_clear_sent"):
                lv["_legacy_tp_clear_sent"] = True
                lv["sl_needs_amend"] = True

            # Dynamic SL sync ONLY when slot is NOT yet in profit-lock (never widen a locked winner back into loss!)
            if e_px > 0 and not is_trail_active:
                if lv["side"] == "LONG":
                    in_profit_sl = cur_sl_px > e_px
                    target_rev_sl = round(e_px * max(0.05, 1.0 - rev_sl_pct), prec)
                    if not in_profit_sl and abs(cur_sl_px - target_rev_sl) > 1e-9 and bid >= target_rev_sl * (1.0 + rev_room_buf):
                        lv["sl_price"] = target_rev_sl
                        cur_sl_px = target_rev_sl
                        lv["sl_needs_amend"] = True
                else:
                    in_profit_sl = cur_sl_px > 0 and cur_sl_px < e_px
                    target_rev_sl = round(e_px * (1.0 + rev_sl_pct), prec)
                    if (
                        not in_profit_sl
                        and (cur_sl_px <= 0 or abs(cur_sl_px - target_rev_sl) > 1e-9)
                        and ask <= target_rev_sl * max(0.05, 1.0 - rev_room_buf)
                    ):
                        lv["sl_price"] = target_rev_sl
                        cur_sl_px = target_rev_sl
                        lv["sl_needs_amend"] = True

            if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                continue

            act_px = float(lv.get("tp_price") or 0.0)
            if act_px <= 0 and e_px > 0:
                act_px = round(
                    e_px * (1.0 + tp_act + rt_fee) if lv["side"] == "LONG" else e_px * max(0.05, 1.0 - tp_act - rt_fee),
                    prec,
                )
                lv["tp_price"] = act_px

            if lv["side"] == "LONG":
                prev_peak = float(lv.get("peak_price") or 0.0)
                peak = max(prev_peak, e_px, bid)
                lv["peak_price"] = peak

                if not is_trail_active and act_px > 0 and bid >= act_px:
                    is_trail_active = True
                    lv["trail_active"] = True

                if is_trail_active and e_px > 0:
                    be_floor = round(e_px * (1.0 + rt_fee + be_lock_buf), prec)
                    cand_trail = round(max(be_floor, peak * (1.0 - trail_ret)), prec)
                    prev_trail = float(lv.get("trail_stop_price") or 0.0)
                    trail_px = max(prev_trail, cand_trail)
                    lv["trail_stop_price"] = trail_px

                    # Ratchet exchange-native Conditional SL into profit (immediate on activation, then every +0.25%)
                    target_okx_sl = round(max(be_floor, trail_px * (1.0 - 0.0010)), prec)
                    if (cur_sl_px < be_floor - 1e-9 or target_okx_sl >= cur_sl_px * 1.0025) and bid >= target_okx_sl * 1.0008:
                        lv["sl_price"] = target_okx_sl
                        cur_sl_px = target_okx_sl
                        lv["sl_needs_amend"] = True

                    if trail_px > 0 and bid <= trail_px:
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = False
                        # Disarm bypassed IDLE LONG levels below current pullback so we never knife-catch on reversal
                        for other in self.levels.values():
                            if other["side"] == "LONG" and other["status"] == "IDLE" and float(other.get("trigger_price") or 0.0) <= mid:
                                other["armed"] = False
                        exits.append({**lv, "exit_reason": "GRID_TRAIL_TP", "target_price": bid})
                        continue

                sl_px = float(lv.get("sl_price") or 0.0)
                if sl_px > 0 and bid <= sl_px:
                    if self._should_fire_software_sl(lv, bid, sl_px, True, now_m):
                        is_profit_sl = bool(is_trail_active or (e_px > 0 and sl_px > e_px))
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = not is_profit_sl
                        exits.append(
                            {**lv, "exit_reason": "GRID_BE_SL" if is_profit_sl else "GRID_SL", "target_price": bid}
                        )
                else:
                    if float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and bid > sl_px * 1.0005:
                        lv["cond_sl_touched_mono"] = 0.0
            else:
                prev_trough = float(lv.get("trough_price") or 0.0)
                trough = min(prev_trough, e_px, ask) if prev_trough > 0 else min(e_px, ask)
                lv["trough_price"] = trough

                if not is_trail_active and act_px > 0 and ask <= act_px:
                    is_trail_active = True
                    lv["trail_active"] = True

                if is_trail_active and e_px > 0:
                    be_floor = round(e_px * max(0.05, 1.0 - rt_fee - be_lock_buf), prec)
                    cand_trail = round(min(be_floor, trough * (1.0 + trail_ret)), prec)
                    prev_trail = float(lv.get("trail_stop_price") or 0.0)
                    trail_px = min(prev_trail, cand_trail) if prev_trail > 0 else cand_trail
                    lv["trail_stop_price"] = trail_px

                    target_okx_sl = round(min(be_floor, trail_px * (1.0 + 0.0010)), prec)
                    if (
                        cur_sl_px <= 0
                        or cur_sl_px > be_floor + 1e-9
                        or target_okx_sl <= cur_sl_px * 0.9975
                    ) and ask <= target_okx_sl * 0.9992:
                        lv["sl_price"] = target_okx_sl
                        cur_sl_px = target_okx_sl
                        lv["sl_needs_amend"] = True

                    if trail_px > 0 and ask >= trail_px:
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = False
                        # Disarm bypassed IDLE SHORT levels above current bounce so we never knife-catch on reversal
                        for other in self.levels.values():
                            if other["side"] == "SHORT" and other["status"] == "IDLE" and float(other.get("trigger_price") or 0.0) >= mid:
                                other["armed"] = False
                        exits.append({**lv, "exit_reason": "GRID_TRAIL_TP", "target_price": ask})
                        continue

                sl_px = float(lv.get("sl_price") or 0.0)
                if sl_px > 0 and ask >= sl_px:
                    if self._should_fire_software_sl(lv, ask, sl_px, False, now_m):
                        is_profit_sl = bool(is_trail_active or (e_px > 0 and sl_px < e_px))
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = not is_profit_sl
                        exits.append(
                            {**lv, "exit_reason": "GRID_BE_SL" if is_profit_sl else "GRID_SL", "target_price": ask}
                        )
                else:
                    if float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and ask < sl_px * 0.9995:
                        lv["cond_sl_touched_mono"] = 0.0

        self._refresh_side_trail_summaries()

        # 2. Flat Auto-Recenter (never preempts L1/S1 trigger distance)
        if allow_recenter and not self._has_active_slots():
            eff_range = max(float(cfg.grid_range_pct), step * 1.15)
            drifted_out = (
                cfg.grid_recenter_enabled
                and now_m >= self._post_sl_cooldown_until_mono
                and self.center_price > 0
                and abs(mid - self.center_price) / self.center_price > eff_range
            )
            if drifted_out:
                self.center_price = mid
                self._build_levels(cfg)
                sig_params = self._compute_sig_params(cfg)
                self._last_user_sig_params = sig_params
                self._last_sig_params = sig_params
                return [], exits

        if (
            not allow_entries
            or now_m < self._post_sl_cooldown_until_mono
            or bool(exits)
            or any(lv.get("status") == "EXITING" for lv in self.levels.values())
        ):
            return [], exits

        # 3. Crossing re-arm hysteresis & strict single-side bidirectional Reverse Oscillating entries
        #    (涨破开多 / 跌破开空 — NEVER opens opposite side while existing side is active -> zero reverse-locking!)
        rearm_buf = min(step * 0.05, 0.0003)
        for lv in self.levels.values():
            if lv["status"] != "IDLE":
                continue
            if not lv.get("armed", True):
                t_px = float(lv.get("trigger_price") or 0.0)
                if t_px > 0:
                    if lv["side"] == "LONG" and mid <= t_px * max(0.05, 1.0 - rearm_buf):
                        lv["armed"] = True
                    elif lv["side"] == "SHORT" and mid >= t_px * (1.0 + rearm_buf):
                        lv["armed"] = True

        has_long_active = self._has_active_slots("LONG")
        has_short_active = self._has_active_slots("SHORT")
        eff_rng = max(float(cfg.grid_range_pct), step)
        range_upper = self.center_price * (1.0 + eff_rng + step * 0.5)
        range_lower = self.center_price * max(0.05, 1.0 - eff_rng - step * 0.5)
        has_entering_long = any(lv["side"] == "LONG" and lv["status"] == "ENTERING" for lv in self.levels.values())
        has_entering_short = any(lv["side"] == "SHORT" and lv["status"] == "ENTERING" for lv in self.levels.values())
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

        if (
            not has_entering_long
            and not has_short_active
            and cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY")
            and self.center_price < mid <= range_upper
        ):
            long_cands = []
            for lv in self.levels.values():
                if lv["side"] != "LONG" or lv["status"] != "IDLE" or not lv.get("armed", True):
                    continue
                if now_m < self._slot_cooldown_until(lv):
                    continue
                if ask >= float(lv["trigger_price"]):
                    long_cands.append(lv)
            if long_cands and all(abs(ask - ep) / ep >= step * 0.55 for ep in open_long_entries):
                chosen = max(long_cands, key=lambda x: (float(x["trigger_price"]), int(x["level_idx"])))
                chosen["status"] = "ENTERING"
                has_long_active = True
                res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": ask})

        if (
            not has_entering_short
            and not has_long_active
            and cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY")
            and range_lower <= mid < self.center_price
        ):
            short_cands = []
            for lv in self.levels.values():
                if lv["side"] != "SHORT" or lv["status"] != "IDLE" or not lv.get("armed", True):
                    continue
                if now_m < self._slot_cooldown_until(lv):
                    continue
                if bid <= float(lv["trigger_price"]):
                    short_cands.append(lv)
            if short_cands and all(abs(bid - ep) / ep >= step * 0.55 for ep in open_short_entries):
                chosen = min(short_cands, key=lambda x: (float(x["trigger_price"]), -int(x["level_idx"])))
                chosen["status"] = "ENTERING"
                has_short_active = True
                res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": bid})

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
        lv["armed"] = False
        lv["entry_price"] = entry_price
        lv["filled_qty"] = filled_qty
        lv["contracts"] = contracts
        lv["ct_val"] = ct_val
        lv["opened_mono_ms"] = mono_ms()
        lv["maker_tp_touched_mono"] = 0.0
        lv["cond_sl_touched_mono"] = 0.0
        lv["trail_active"] = False
        lv["trail_stop_price"] = 0.0
        lv["peak_price"] = entry_price if lv["side"] == "LONG" else 0.0
        lv["trough_price"] = entry_price if lv["side"] == "SHORT" else 0.0
        lv.pop("_legacy_tp_clear_sent", None)

        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        rearm_buf = min(step * 0.25, 0.0020)
        sl_pct = float(cfg.grid_stop_loss_pct)

        if lv["side"] == "LONG":
            # tp_price marks the Trailing TP activation price (NOT a fixed Maker TP ceiling!)
            lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
            lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
            for other in self.levels.values():
                if other["slot_id"] != slot_id and other["side"] == "LONG" and other["status"] == "IDLE":
                    if float(other.get("trigger_price") or 0.0) <= entry_price * max(0.05, 1.0 - rearm_buf):
                        other["armed"] = False
        else:
            lv["tp_price"] = round(
                entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
            )
            lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
            for other in self.levels.values():
                if other["slot_id"] != slot_id and other["side"] == "SHORT" and other["status"] == "IDLE":
                    if float(other.get("trigger_price") or 0.0) >= entry_price * (1.0 + rearm_buf):
                        other["armed"] = False
        self._refresh_side_trail_summaries()

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
            self.active_grid_mode = "REVERSE_OSCILLATING"
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
            grid_mode="REVERSE_OSCILLATING",
            has_fixed_tp=True,
        )
        lv = self.levels[target_slot]
        prec = self._calc_prec(cfg)
        sl_pct = float(cfg.grid_stop_loss_pct)
        lv["status"] = "OPEN"
        lv["armed"] = False
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
        lv["trail_active"] = False
        lv["trail_stop_price"] = 0.0
        lv["peak_price"] = entry_price if side == "LONG" else 0.0
        lv["trough_price"] = entry_price if side == "SHORT" else 0.0

        if side == "LONG":
            lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
            lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
        else:
            lv["tp_price"] = round(
                entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
            )
            lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
        self._refresh_side_trail_summaries()
        return target_slot

    def clear_external_exit(self, slot_id: str):
        lv = self.levels.get(slot_id)
        if not lv:
            return
        if lv.get("ephemeral_recovered"):
            self.levels.pop(slot_id, None)
            self._refresh_side_trail_summaries()
            return
        now_m = mono_ms()
        lv["status"] = "IDLE"
        lv["armed"] = False
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
        lv["trail_active"] = False
        lv["peak_price"] = 0.0
        lv["trough_price"] = 0.0
        lv["trail_stop_price"] = 0.0
        lv.pop("_legacy_tp_clear_sent", None)
        lv.pop("exiting_is_sl", None)
        lv.pop("recovered", None)
        self._refresh_side_trail_summaries()

    def mark_exited(self, slot_id: str, net_pnl: float, is_sl: bool = False):
        lv = self.levels.get(slot_id)
        now_m = mono_ms()
        if lv:
            if lv.get("ephemeral_recovered"):
                self.levels.pop(slot_id, None)
            else:
                lv["status"] = "IDLE"
                lv["armed"] = False
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
                lv["trail_active"] = False
                lv["peak_price"] = 0.0
                lv["trough_price"] = 0.0
                lv["trail_stop_price"] = 0.0
                lv.pop("_legacy_tp_clear_sent", None)
                lv.pop("sl_needs_amend", None)
                lv.pop("exiting_is_sl", None)
                lv.pop("recovered", None)
        self._refresh_side_trail_summaries()
        # Only engage 120s penalty cooldown on actual losing stop-losses (not profitable BE/Trailing exits)
        if is_sl and net_pnl < 0:
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + 120_000.0)
        else:
            self.completed_cycles += 1
        self.total_net_pnl = round(self.total_net_pnl + net_pnl, 4)

    def export_state(self) -> dict:
        disarmed_slots = [sid for sid, lv in self.levels.items() if lv.get("armed") is False]
        serialized_slots = []
        for s_item in self._serialize_open_slots():
            lv_ref = self.levels.get(s_item["slot_id"], {})
            s_item["trail_active"] = bool(lv_ref.get("trail_active", False))
            s_item["peak_price"] = float(lv_ref.get("peak_price") or 0.0)
            s_item["trough_price"] = float(lv_ref.get("trough_price") or 0.0)
            s_item["trail_stop_price"] = float(lv_ref.get("trail_stop_price") or 0.0)
            serialized_slots.append(s_item)
        return {
            "strategy_type": self.strategy_type,
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": "REVERSE_OSCILLATING",
            "long_peak_price": float(self.long_peak_price or 0.0),
            "short_trough_price": float(self.short_trough_price or 0.0),
            "completed_cycles": int(self.completed_cycles or 0),
            "total_net_pnl": float(self.total_net_pnl or 0.0),
            "last_bbw_info": None,
            "needs_post_trend_recenter": False,
            "open_slots": serialized_slots,
            "disarmed_slots": disarmed_slots,
        }

    def restore_state(self, data: dict, cfg: SymbolStrategy):
        if not isinstance(data, dict):
            return
        self.active_grid_mode = "REVERSE_OSCILLATING"
        self.last_regime_reason = self.default_regime_reason
        c_px = float(data.get("center_price") or 0.0)
        if c_px > 0:
            self.center_price = c_px
            self._build_levels(cfg)
            sig_params = self._compute_sig_params(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
        for sid in data.get("disarmed_slots") or []:
            if sid in self.levels:
                self.levels[sid]["armed"] = False
        self.completed_cycles = int(data.get("completed_cycles") or self.completed_cycles)
        self.total_net_pnl = float(data.get("total_net_pnl") or self.total_net_pnl)
        prec = self._calc_prec(cfg)

        for slot in data.get("open_slots") or []:
            if not str(slot.get("slot_id") or ""):
                continue
            lv = self._restore_common_slot_fields(slot, cfg, grid_mode="REVERSE_OSCILLATING")
            lv["armed"] = False
            e_px = float(lv.get("entry_price") or 0.0)
            lv["trail_active"] = bool(slot.get("trail_active", False))
            lv["peak_price"] = float(slot.get("peak_price") or (e_px if lv["side"] == "LONG" else 0.0))
            lv["trough_price"] = float(slot.get("trough_price") or (e_px if lv["side"] == "SHORT" else 0.0))
            lv["trail_stop_price"] = float(slot.get("trail_stop_price") or 0.0)
            if float(lv.get("tp_price") or 0.0) <= 0 and e_px > 0:
                lv["tp_price"] = round(
                    e_px * (1.0 + float(cfg.grid_take_profit_pct) + 0.001)
                    if lv["side"] == "LONG"
                    else e_px * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - 0.001),
                    prec,
                )
        self._refresh_side_trail_summaries()

    def get_snapshot(self) -> dict:
        open_long = sum(1 for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING"))
        open_short = sum(1 for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING"))
        ordered = sorted(
            self.levels.values(),
            key=lambda x: (0 if x["side"] == "LONG" else 1, -x["level_idx"] if x["side"] == "LONG" else x["level_idx"]),
        )
        return {
            "center_price": self.center_price,
            "active_grid_mode": "REVERSE_OSCILLATING",
            "regime_reason": self.last_regime_reason,
            "long_peak_price": float(self.long_peak_price or 0.0),
            "short_trough_price": float(self.short_trough_price or 0.0),
            "long_trail_stop_price": float(self.long_trail_stop_price or 0.0),
            "short_trail_stop_price": float(self.short_trail_stop_price or 0.0),
            "bbw_timeframe": "",
            "bbw_pct": None,
            "bbw_percentile": None,
            "bb_upper": None,
            "bb_lower": None,
            "ema_fast": None,
            "ema_slow": None,
            "ema_gap_pct": None,
            "trend_bias": "RANGE",
            "breakout_expansion": "NONE",
            "vcp_contraction": False,
            "open_long_count": open_long,
            "open_short_count": open_short,
            "completed_cycles": self.completed_cycles,
            "grid_net_pnl": self.total_net_pnl,
            "levels": [
                {
                    "slot_id": lv["slot_id"],
                    "side": lv["side"],
                    "grid_mode": lv.get("grid_mode", "REVERSE_OSCILLATING"),
                    "trigger_price": lv["trigger_price"],
                    "tp_price": lv["tp_price"],
                    "sl_price": lv["sl_price"],
                    "order_usdt": lv.get("order_usdt", 0.0),
                    "weight_pct": lv.get("weight_pct", 0.0),
                    "maker_tp_live": bool(lv.get("maker_tp_live", False)),
                    "cond_sl_live": bool(lv.get("cond_sl_live", False)),
                    "trail_active": bool(lv.get("trail_active", False)),
                    "trail_stop_price": float(lv.get("trail_stop_price") or 0.0),
                    "armed": bool(lv.get("armed", True)),
                    "status": lv["status"],
                    "entry_price": lv["entry_price"],
                }
                for lv in ordered
            ],
        }
