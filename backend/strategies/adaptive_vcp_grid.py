"""
Standalone Volatility-Adaptive VCP Dual-Mode Grid Strategy (`ADAPTIVE_GRID` — VCP双模式智能网格).

Completely isolated trading logic — owns its own VCP/BBW volatility regime detector, EMA trend_bias
hysteresis filter, and internal MEAN_REVERSION <-> TREND_BREAKOUT trading rules so editing any
manual strategy (`LONG_SHORT_GRID`, `REVERSE_GRID`, `TREND_GRID`) NEVER silently alters `ADAPTIVE_GRID`
(and vice versa):
- Defaults to `MEAN_REVERSION` during normal range-bound markets (with EMA anti-knife directional guard)
- Switches automatically to `TREND_BREAKOUT` when completely flat and 15m/1H BBW compresses into VCP
  squeeze percentile OR detects directional `UP_BREAKOUT`/`DOWN_BREAKOUT` expansion
- Exits `TREND_BREAKOUT` back to `MEAN_REVERSION` with release hysteresis once expansion settles
"""
import time
from typing import Dict, List, Optional, Tuple

from backend.math_engine import mono_ms
from backend.models import SymbolStrategy
from backend.strategies.base import BaseGridStrategy, calc_pyramid_weights


class AdaptiveVcpGridStrategy(BaseGridStrategy):
    strategy_type = "ADAPTIVE_GRID"
    default_grid_mode = "MEAN_REVERSION"
    default_regime_reason = "等待 15m/1H BBW 波动率样本（暂保持当前模式）"

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return bool(cfg.use_post_only_tp) and self.active_grid_mode == "MEAN_REVERSION"

    def uses_livermore_sl_amend(self) -> bool:
        return self.active_grid_mode == "TREND_BREAKOUT"

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        if self.active_grid_mode == "TREND_BREAKOUT":
            e_px = float(lv.get("entry_price") or 0.0)
            sl_px = float(lv.get("sl_price") or 0.0)
            side = str(lv.get("side") or "LONG")
            is_be = (side == "LONG" and sl_px >= e_px) or (side == "SHORT" and sl_px <= e_px)
            return "TREND_BE_SL" if is_be else "TREND_SL"
        return "GRID_SL"

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        if self.active_grid_mode == "TREND_BREAKOUT":
            return ("顺势突破开仓", "突破做多" if side == "LONG" else "跌破做空")
        return ("多空网格开仓", "做多网格" if side == "LONG" else "做空网格")

    def get_exit_log_tag(self) -> str:
        return "多空网格平仓"

    def get_mode_display_name(self) -> str:
        return "顺势突破" if self.active_grid_mode == "TREND_BREAKOUT" else "传统震荡"

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
        if self.active_grid_mode == "TREND_BREAKOUT":
            return (
                f"[顺势突破成交] {self.symbol} [{slot_id}] {side} 均价: {avg_price:.{cfg.price_precision}f} | "
                f"数量: {filled_qty} | 假突破/保本止损: {lv.get('sl_price', 0):.{cfg.price_precision}f} | "
                f"追踪止盈: 回撤 {cfg.trend_trailing_mult * cfg.grid_step_pct * 100:.2f}%{prot_suffix}"
            )
        return (
            f"[多空网格成交] {self.symbol} [{slot_id}] {side} 均价: {avg_price:.{cfg.price_precision}f} | "
            f"数量: {filled_qty} | 止盈目标: {lv.get('tp_price', 0):.{cfg.price_precision}f}{prot_suffix}"
        )

    # ---------------- Own VCP / BBW + EMA Trend Regime Detector ----------------

    def _get_valid_bbw(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> Optional[dict]:
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
        self._effective_trend_bias = "RANGE"
        return None

    def _resolve_effective_trend_bias(self, info: Optional[dict]) -> str:
        if not info:
            self._effective_trend_bias = "RANGE"
            return "RANGE"
        raw_bias = str(info.get("trend_bias") or "RANGE")
        ema_z = info.get("ema_gap_z")
        if ema_z is None:
            self._effective_trend_bias = raw_bias
            return raw_bias
        z_val = float(ema_z)
        slope_z = float(info.get("bb_slope_z") or 0.0)
        last_c = float(info.get("last_close") or 0.0)
        ema_s = float(info.get("ema_slow") or 0.0)
        cur = self._effective_trend_bias
        if raw_bias == "BULL":
            self._effective_trend_bias = "BULL"
        elif raw_bias == "BEAR":
            self._effective_trend_bias = "BEAR"
        elif z_val >= 0.75 and slope_z >= 0.20 and (last_c <= 0 or ema_s <= 0 or last_c >= ema_s):
            self._effective_trend_bias = "BULL"
        elif z_val <= -0.75 and slope_z <= -0.20 and (last_c <= 0 or ema_s <= 0 or last_c <= ema_s):
            self._effective_trend_bias = "BEAR"
        elif cur == "BULL" and z_val > 0.30 and (last_c <= 0 or ema_s <= 0 or last_c >= ema_s * 0.998):
            self._effective_trend_bias = "BULL"
        elif cur == "BEAR" and z_val < -0.30 and (last_c <= 0 or ema_s <= 0 or last_c <= ema_s * 1.002):
            self._effective_trend_bias = "BEAR"
        else:
            self._effective_trend_bias = "RANGE"
        return self._effective_trend_bias

    def _resolve_target_mode(self, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> Tuple[str, str]:
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
            if breakout_exp in ("UP_BREAKOUT", "DOWN_BREAKOUT") and pct_rank <= 85.0:
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
            if pct_rank >= release_thresh and breakout_exp == "NONE":
                bias_cn = (
                    "多头(停逆势空)"
                    if trend_bias == "BULL"
                    else ("空头(停逆势多)" if trend_bias == "BEAR" else "震荡双向")
                )
                return (
                    "MEAN_REVERSION",
                    f"{tf} BBW={bbw_pct:.2f}% 扩张恢复至 {pct_rank:.1f}% 且突破结束 → 切回传统震荡网格（EMA={bias_cn}）",
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

    # ---------------- Level Construction ----------------

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

        self.long_peak_price = 0.0
        self.short_trough_price = 0.0
        self.long_trail_stop_price = 0.0
        self.short_trail_stop_price = 0.0

        if self.active_grid_mode == "TREND_BREAKOUT":
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
        else:
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

    # ---------------- Lifecycle & Tick Evaluation ----------------

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
                if current_mid > 0 and (self.center_price <= 0 or mode_changed):
                    self.center_price = current_mid
                    self._needs_post_trend_recenter = False
                elif not bb_only_update:
                    self._needs_post_trend_recenter = False
                if self.center_price > 0:
                    self._build_levels(cfg, bbw_info=valid_bbw, preserve_cooldowns=bb_only_update)
                self._last_user_sig_params = user_sig
                self._last_sig_params = sig_params
                if not bb_only_update:
                    self.clear_cooldowns()
                if mode_changed:
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
        if prev_mode != target_mode:
            self._pending_regime_log = (
                f"[VCP双模式重锚] {self.symbol} 重锚至 {new_center} 并切换 {prev_mode} → {target_mode} | {reason}"
            )
        return True

    def _eval_trend_exits(
        self, bid: float, ask: float, cfg: SymbolStrategy, round_trip_fee: float, now_m: float, prec: int
    ) -> List[dict]:
        exits: List[dict] = []
        step_now = max(0.0001, float(cfg.grid_step_pct))
        trail_ratio = max(0.0005, float(cfg.trend_trailing_mult) * step_now)
        be_slip_buf = max(0.0016, step_now * 0.20)
        sl_room_buf = max(0.0025, step_now * 0.30)
        pyramid_profit_lock_move = max(0.45 * step_now, round_trip_fee + 0.0020)
        solo_half_lock_move = max(0.65 * step_now, round_trip_fee + 0.0030)
        solo_half_sl_dist = max(0.0045, 0.70 * step_now)
        solo_lock_move = max(0.85 * step_now, round_trip_fee + be_slip_buf + sl_room_buf + 0.0015)
        profit_floor_trigger = max(0.55 * step_now, 0.0040)
        pyramid_clear_move = max(0.30 * step_now, round_trip_fee + 0.0010)
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
                if self.long_peak_price >= max_entry_l * (1.0 + pyramid_profit_lock_move):
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
                    elif self.long_peak_price >= e_entry * (1.0 + solo_half_lock_move):
                        target_sl_l = max(target_sl_l, round(e_entry * max(0.05, 1.0 - solo_half_sl_dist), prec))
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
                if self.short_trough_price <= min_entry_s * max(0.05, 1.0 - pyramid_profit_lock_move):
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
                    elif self.short_trough_price <= e_entry * max(0.05, 1.0 - solo_half_lock_move):
                        target_sl_s = min(target_sl_s, round(e_entry * (1.0 + solo_half_sl_dist), prec))
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

        return exits

    def _eval_mr_exits(self, bid: float, ask: float, now_m: float) -> List[dict]:
        exits: List[dict] = []
        for lv in self.levels.values():
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
        return exits

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
            target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
            self.active_grid_mode = target_mode
            self.last_regime_reason = reason
            self.center_price = mid
            self._build_levels(cfg, bbw_info=valid_bbw)
            self._last_user_sig_params = self._compute_user_sig_params(cfg, target_mode)
            self._last_sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)
            return [], []

        now_m = mono_ms()
        if force_exit_all:
            return [], self._evaluate_kill_switch_exits(bid, ask, now_m)

        prec = self._calc_prec(cfg)
        entries: List[dict] = []

        # 1. Exits according to current adaptive sub-mode
        if self.active_grid_mode == "TREND_BREAKOUT":
            exits = self._eval_trend_exits(bid, ask, cfg, round_trip_fee, now_m, prec)
        else:
            exits = self._eval_mr_exits(bid, ask, now_m)

        # 2. Flat Auto-Recenter & VCP Dual-Mode Regime Switching
        if allow_recenter and not self._has_active_slots():
            target_mode, reason = self._resolve_target_mode(cfg, bbw_info=valid_bbw)
            mode_switched = target_mode != self.active_grid_mode
            eff_range = float(cfg.grid_range_pct)
            if self.active_grid_mode == "TREND_BREAKOUT" and self.center_price > 0:
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
            post_trend_recenter = self._needs_post_trend_recenter and (now_m >= self._post_sl_cooldown_until_mono)
            if mode_switched or drifted_out or post_trend_recenter:
                prev_mode = self.active_grid_mode
                self.active_grid_mode = target_mode
                self.last_regime_reason = reason
                self.center_price = mid
                self._needs_post_trend_recenter = False
                self._build_levels(cfg, bbw_info=valid_bbw)
                self._last_user_sig_params = self._compute_user_sig_params(cfg, target_mode)
                self._last_sig_params = self._compute_sig_params(cfg, target_mode, bbw_info=valid_bbw)
                if mode_switched:
                    self._pending_regime_log = (
                        f"[VCP双模式切换] {self.symbol} 空仓重锚切换 {prev_mode} → {target_mode} | {reason}"
                    )
                return [], exits

        if not allow_entries or now_m < self._post_sl_cooldown_until_mono:
            return [], exits

        # 3. Entries WITH ADAPTIVE_GRID's EMA trend_bias anti-knife guard
        has_long_active = self._has_active_slots("LONG")
        has_short_active = self._has_active_slots("SHORT")
        step = max(0.0001, float(cfg.grid_step_pct))
        trend_bias = self._resolve_effective_trend_bias(valid_bbw)

        if self.active_grid_mode == "TREND_BREAKOUT":
            if exits or any(lv.get("status") == "EXITING" and lv.get("exiting_is_sl") for lv in self.levels.values()):
                return [], exits
            allow_trend_long = (cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY")) and (trend_bias != "BEAR")
            allow_trend_short = (cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY")) and (trend_bias != "BULL")

            if allow_trend_long:
                max_active_l = max(
                    (
                        int(lv.get("level_idx") or 0)
                        for lv in self.levels.values()
                        if lv["side"] == "LONG" and lv["status"] in ("ENTERING", "OPEN", "EXITING")
                    ),
                    default=0,
                )
                for lv in sorted((x for x in self.levels.values() if x["side"] == "LONG"), key=lambda x: x["level_idx"]):
                    if lv["level_idx"] <= max_active_l:
                        continue
                    if lv["status"] != "IDLE" or (is_net_mode and has_short_active) or now_m < self._slot_cooldown_until(lv):
                        break
                    min_req_px = float(lv["trigger_price"])
                    if lv["level_idx"] > 1:
                        prev_lv = self.levels.get(f"L{lv['level_idx'] - 1}")
                        if not prev_lv or prev_lv.get("status") != "OPEN":
                            break
                        prev_entry = float(prev_lv.get("entry_price") or 0.0)
                        if prev_entry > 0:
                            step_anchor_l = round(prev_entry * (1.0 + step), prec)
                            min_req_px = max(min(min_req_px, step_anchor_l), round(prev_entry * (1.0 + step * 0.90), prec))
                    if ask >= min_req_px:
                        lv["status"] = "ENTERING"
                        has_long_active = True
                        res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": ask})
                        break

            if allow_trend_short:
                max_active_s = max(
                    (
                        int(lv.get("level_idx") or 0)
                        for lv in self.levels.values()
                        if lv["side"] == "SHORT" and lv["status"] in ("ENTERING", "OPEN", "EXITING")
                    ),
                    default=0,
                )
                for lv in sorted((x for x in self.levels.values() if x["side"] == "SHORT"), key=lambda x: x["level_idx"]):
                    if lv["level_idx"] <= max_active_s:
                        continue
                    if lv["status"] != "IDLE" or (is_net_mode and has_long_active) or now_m < self._slot_cooldown_until(lv):
                        break
                    max_req_px = float(lv["trigger_price"])
                    if lv["level_idx"] > 1:
                        prev_lv = self.levels.get(f"S{lv['level_idx'] - 1}")
                        if not prev_lv or prev_lv.get("status") != "OPEN":
                            break
                        prev_entry = float(prev_lv.get("entry_price") or 0.0)
                        if prev_entry > 0:
                            step_anchor_s = round(prev_entry * max(0.05, 1.0 - step), prec)
                            max_req_px = min(
                                max(max_req_px, step_anchor_s),
                                round(prev_entry * max(0.05, 1.0 - step * 0.90), prec),
                            )
                    if bid <= max_req_px:
                        lv["status"] = "ENTERING"
                        has_short_active = True
                        res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                        entries.append({**lv, "reservation_price": res_px, "trigger_exec_price": bid})
                        break
        else:
            if any("SL" in str(e.get("exit_reason") or "") for e in exits) or any(
                lv.get("status") == "EXITING" and lv.get("exiting_is_sl") for lv in self.levels.values()
            ):
                return [], exits
            sl_guard_long = self.center_price * max(0.05, 1.0 - float(cfg.grid_stop_loss_pct) * 0.95)
            sl_guard_short = self.center_price * (1.0 + float(cfg.grid_stop_loss_pct) * 0.95)
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
            allow_mr_long = (cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY")) and (trend_bias != "BEAR")
            allow_mr_short = (cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY")) and (trend_bias != "BULL")

            if (
                not has_entering_long
                and allow_mr_long
                and not (is_net_mode and has_short_active)
                and mid >= sl_guard_long
            ):
                long_cands = [
                    lv for lv in self.levels.values()
                    if lv["side"] == "LONG"
                    and lv["status"] == "IDLE"
                    and now_m >= self._slot_cooldown_until(lv)
                    and ask <= float(lv["trigger_price"])
                ]
                if long_cands and all(abs(ask - ep) / ep >= step * 0.55 for ep in open_long_entries):
                    chosen = min(long_cands, key=lambda x: (float(x["trigger_price"]), -int(x["level_idx"])))
                    chosen["status"] = "ENTERING"
                    has_long_active = True
                    res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": ask})

            if (
                not has_entering_short
                and allow_mr_short
                and not (is_net_mode and has_long_active)
                and mid <= sl_guard_short
            ):
                short_cands = [
                    lv for lv in self.levels.values()
                    if lv["side"] == "SHORT"
                    and lv["status"] == "IDLE"
                    and now_m >= self._slot_cooldown_until(lv)
                    and bid >= float(lv["trigger_price"])
                ]
                if short_cands and all(abs(bid - ep) / ep >= step * 0.55 for ep in open_short_entries):
                    chosen = max(short_cands, key=lambda x: (float(x["trigger_price"]), int(x["level_idx"])))
                    chosen["status"] = "ENTERING"
                    has_short_active = True
                    res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": bid})

        return entries, exits

    def _sync_trend_pyramid_triggers(self, cfg: SymbolStrategy):
        if self.active_grid_mode != "TREND_BREAKOUT":
            return
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
        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        sl_buf = max(0.0035, step * 0.55)
        be_slip_buf = max(0.0016, step * 0.20)
        tick_unit = 10.0 ** (-prec)
        sl_pct = float(cfg.grid_stop_loss_pct)

        if self.active_grid_mode == "TREND_BREAKOUT":
            lv["tp_price"] = 0.0
            lvl_idx = int(lv.get("level_idx") or 1)
            if lv["side"] == "LONG":
                self.long_peak_price = max(self.long_peak_price, entry_price)
                safe_cap_l = min(round(entry_price * max(0.05, 1.0 - sl_buf), prec), round(entry_price - tick_unit, prec))
                if lvl_idx <= 1:
                    raw_sl = round(max(self.center_price, entry_price * max(0.05, 1.0 - sl_pct)), prec)
                    lv["sl_price"] = min(raw_sl, safe_cap_l)
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
                    lv["sl_price"] = max(raw_sl, safe_floor_s)
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
        else:
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
        if self.center_price <= 0 or not self.levels:
            target_mode, reason = self._resolve_target_mode(cfg)
            self.active_grid_mode = target_mode
            self.last_regime_reason = reason
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
            grid_mode=self.active_grid_mode,
            has_fixed_tp=(self.active_grid_mode != "TREND_BREAKOUT"),
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
            self._sync_trend_pyramid_triggers(cfg)
        else:
            if side == "LONG":
                lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
                base_sl = (
                    self.center_price * max(0.05, 1.0 - sl_pct)
                    if self.center_price > 0
                    else entry_price * max(0.05, 1.0 - sl_pct)
                )
                lv["sl_price"] = round(min(base_sl, entry_price * max(0.05, 1.0 - max(float(cfg.grid_step_pct), 0.005))), prec)
            else:
                lv["tp_price"] = round(
                    entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec
                )
                base_sl = self.center_price * (1.0 + sl_pct) if self.center_price > 0 else entry_price * (1.0 + sl_pct)
                lv["sl_price"] = round(max(base_sl, entry_price * (1.0 + max(float(cfg.grid_step_pct), 0.005))), prec)
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
                slot_cd_ms = 180_000.0 if (is_sl and self.active_grid_mode == "TREND_BREAKOUT") else 1200.0
                lv["cooldown_until_mono"] = now_m + slot_cd_ms
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
        if is_sl and self.active_grid_mode == "MEAN_REVERSION":
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + 120_000.0)
        elif is_sl and self.active_grid_mode == "TREND_BREAKOUT" and not self._has_active_slots():
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
        if self.active_grid_mode == "TREND_BREAKOUT" and not self._has_active_slots():
            self._needs_post_trend_recenter = True

    def export_state(self) -> dict:
        return {
            "strategy_type": self.strategy_type,
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": self.active_grid_mode,
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
        saved_mode = str(data.get("active_grid_mode") or "")
        if saved_mode in ("MEAN_REVERSION", "TREND_BREAKOUT"):
            self.active_grid_mode = saved_mode
        else:
            self.active_grid_mode = "MEAN_REVERSION"

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
        self._needs_post_trend_recenter = bool(data.get("needs_post_trend_recenter", False))
        prec = self._calc_prec(cfg)

        for slot in data.get("open_slots") or []:
            if not str(slot.get("slot_id") or ""):
                continue
            lv = self._restore_common_slot_fields(slot, cfg, grid_mode=self.active_grid_mode)
            e_px = float(lv.get("entry_price") or 0.0)
            if self.active_grid_mode == "MEAN_REVERSION" and float(lv.get("tp_price") or 0.0) <= 0 and e_px > 0:
                lv["tp_price"] = round(
                    e_px * (1.0 + float(cfg.grid_take_profit_pct) + 0.001)
                    if lv["side"] == "LONG"
                    else e_px * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - 0.001),
                    prec,
                )
        if self.active_grid_mode == "TREND_BREAKOUT":
            self._sync_trend_pyramid_triggers(cfg)

    def get_snapshot(self) -> dict:
        open_long = sum(1 for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING"))
        open_short = sum(1 for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING"))
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
            "trend_bias": self._effective_trend_bias if self.last_bbw_info else bbw.get("trend_bias", "RANGE"),
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
                    "armed": True,
                    "status": lv["status"],
                    "entry_price": lv["entry_price"],
                }
                for lv in ordered
            ],
        }
