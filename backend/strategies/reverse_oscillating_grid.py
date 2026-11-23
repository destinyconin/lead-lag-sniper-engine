"""
Standalone Reverse Oscillating Grid Strategy (`REVERSE_GRID` — 多空震荡网格).

Completely isolated trading logic — zero coupling to MEAN_REVERSION, TREND_BREAKOUT,
ADAPTIVE_GRID, BBW, or lagging EMA indicators:
- L1..Ln ABOVE center (涨破开多), S1..Sn BELOW center (跌破开空)
- Equal per-slot sizing + fixed per-slot take-profit (supports Maker Post-Only TP) + per-slot hard SL
- Dynamic per-slot SL tightening/widening sync on open positions when user changes `grid_stop_loss_pct`
- Per-slot crossing re-arm hysteresis (`rearm_buf`) & bypassed-inner-level disarming so post-TP slots
  never immediately re-stack while cleanly re-arming on shallow pullbacks
- Symmetric anti-stacking spacing (0.55 * step) + concurrent ENTERING guard + 120s post-SL cooldown
"""
from typing import Dict, List, Optional, Tuple

from backend.math_engine import mono_ms
from backend.models import SymbolStrategy
from backend.strategies.base import BaseGridStrategy


class ReverseOscillatingGridStrategy(BaseGridStrategy):
    strategy_type = "REVERSE_GRID"
    default_grid_mode = "REVERSE_OSCILLATING"
    default_regime_reason = "手动指定多空震荡网格（跌破开空 / 涨破开多）"

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return bool(cfg.use_post_only_tp)

    def uses_livermore_sl_amend(self) -> bool:
        return False

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
            f"数量: {filled_qty} | 止盈目标: {lv.get('tp_price', 0):.{cfg.price_precision}f} | "
            f"止损: {lv.get('sl_price', 0):.{cfg.price_precision}f}{prot_suffix}"
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
                }

        self.levels = new_levels

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

        # 1. Check exits & dynamic SL parameter sync on OPEN slots
        rev_sl_pct = max(0.002, float(cfg.grid_stop_loss_pct))
        rev_room_buf = max(0.0025, step * 0.30)
        for lv in self.levels.values():
            if lv["status"] != "OPEN":
                continue
            e_px = float(lv.get("entry_price") or 0.0)
            cur_sl_px = float(lv.get("sl_price") or 0.0)
            if e_px > 0:
                if lv["side"] == "LONG":
                    target_rev_sl = round(e_px * max(0.05, 1.0 - rev_sl_pct), prec)
                    if abs(cur_sl_px - target_rev_sl) > 1e-9 and bid >= target_rev_sl * (1.0 + rev_room_buf):
                        lv["sl_price"] = target_rev_sl
                        lv["sl_needs_amend"] = True
                else:
                    target_rev_sl = round(e_px * (1.0 + rev_sl_pct), prec)
                    if (cur_sl_px <= 0 or abs(cur_sl_px - target_rev_sl) > 1e-9) and ask <= target_rev_sl * max(
                        0.05, 1.0 - rev_room_buf
                    ):
                        lv["sl_price"] = target_rev_sl
                        lv["sl_needs_amend"] = True

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
                        if (now_m - touched >= 4500.0) or (bid >= tp_px * 1.0025):
                            lv["status"] = "EXITING"
                            exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                    else:
                        lv["status"] = "EXITING"
                        exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                elif sl_px > 0 and bid <= sl_px:
                    if self._should_fire_software_sl(lv, bid, sl_px, True, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
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
                    if self._should_fire_software_sl(lv, ask, sl_px, False, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
                        exits.append({**lv, "exit_reason": "GRID_SL", "target_price": ask})
                else:
                    if maker_live and float(lv.get("maker_tp_touched_mono") or 0.0) > 0.0 and ask > tp_px * 1.0005:
                        lv["maker_tp_touched_mono"] = 0.0
                    if float(lv.get("cond_sl_touched_mono") or 0.0) > 0.0 and sl_px > 0 and ask < sl_px * 0.9995:
                        lv["cond_sl_touched_mono"] = 0.0

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
            or any("SL" in str(e.get("exit_reason") or "") for e in exits)
            or any(lv.get("status") == "EXITING" and lv.get("exiting_is_sl") for lv in self.levels.values())
        ):
            return [], exits

        # 3. Crossing re-arm hysteresis & bidirectional Reverse Oscillating entries (涨破开多 / 跌破开空)
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
            and cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY")
            and not (is_net_mode and has_short_active)
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
            and cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY")
            and not (is_net_mode and has_long_active)
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

        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        rearm_buf = min(step * 0.25, 0.0020)
        sl_pct = float(cfg.grid_stop_loss_pct)

        if lv["side"] == "LONG":
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

        if side == "LONG":
            lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
            lv["sl_price"] = round(entry_price * max(0.05, 1.0 - sl_pct), prec)
        else:
            lv["tp_price"] = round(
                entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
            )
            lv["sl_price"] = round(entry_price * (1.0 + sl_pct), prec)
        return target_slot

    def clear_external_exit(self, slot_id: str):
        lv = self.levels.get(slot_id)
        if not lv:
            return
        if lv.get("ephemeral_recovered"):
            self.levels.pop(slot_id, None)
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
        lv.pop("exiting_is_sl", None)
        lv.pop("recovered", None)

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
                lv.pop("sl_needs_amend", None)
                lv.pop("exiting_is_sl", None)
                lv.pop("recovered", None)
        if is_sl:
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + 120_000.0)
        else:
            self.completed_cycles += 1
        self.total_net_pnl = round(self.total_net_pnl + net_pnl, 4)

    def export_state(self) -> dict:
        disarmed_slots = [sid for sid, lv in self.levels.items() if lv.get("armed") is False]
        return {
            "strategy_type": self.strategy_type,
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": "REVERSE_OSCILLATING",
            "long_peak_price": 0.0,
            "short_trough_price": 0.0,
            "completed_cycles": int(self.completed_cycles or 0),
            "total_net_pnl": float(self.total_net_pnl or 0.0),
            "last_bbw_info": None,
            "needs_post_trend_recenter": False,
            "open_slots": self._serialize_open_slots(),
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
            if float(lv.get("tp_price") or 0.0) <= 0 and e_px > 0:
                lv["tp_price"] = round(
                    e_px * (1.0 + float(cfg.grid_take_profit_pct) + 0.001)
                    if lv["side"] == "LONG"
                    else e_px * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - 0.001),
                    prec,
                )

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
            "long_peak_price": 0.0,
            "short_trough_price": 0.0,
            "long_trail_stop_price": 0.0,
            "short_trail_stop_price": 0.0,
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
                    "armed": bool(lv.get("armed", True)),
                    "status": lv["status"],
                    "entry_price": lv["entry_price"],
                }
                for lv in ordered
            ],
        }
