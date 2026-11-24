"""
Standalone Mode-Locked Hybrid Reverse Oscillating Grid Strategy (`REVERSE_GRID` — 多空震荡网格).

Completely isolated from LONG_SHORT_GRID, TREND_GRID, ADAPTIVE_GRID, BBW, and EMA indicators.
Implements the full mode-locked base + oscillation scalp + decaying pyramid + group trailing TP specification:

1. Anchor & Mode Lock (`locked_mode`: "NONE" -> "LONG" or "SHORT"):
   - On startup or post-restart, anchors `center_price = C` to live mid price and builds symmetrical
     L1..LN (`C * (1 + i * step)`) and S1..SN (`C * (1 - i * step)`) levels (default N=5, step=0.6%).
   - Whichever first triggers (`L1` upward -> `Q` LONG, or `S1` downward -> `QS` SHORT) locks
     `locked_mode` to `"LONG"` or `"SHORT"` until all slots close, reconcile flat, cool down, and re-anchor.

2. LONG Mode (`locked_mode == "LONG"`, all positions are LONG):
   - `Q` (100% at L1): base position, no fixed TP; managed by L-group trailing TP / L_N full TP / Global SL.
   - Oscillation dip-buy slots (100% each, Maker Post-Only TP supported):
       * `C`:  buy at C,  sell at L1 (when `Q` is held, rising to L1 ONLY sells `C`, never re-buys `Q`;
               if `C` is held WITHOUT `Q`, reaching L1 converts `C` in-place to `Q` with zero extra order!)
       * `S1`: buy at S1, sell at C
       * `S2`: buy at S2, sell at S1 ... up to `S_N`: buy at S_N, sell at S_{N-1}
   - Decaying Pyramid trend slots (join L-group with `Q`, no fixed TP):
       * `L2` (50% at L2): activates L-group trailing TP
       * `L3` (30% at L3): joins L-group, recalculates group VWAP & monotonic floor
       * `L4` (10% at L4): joins L-group, recalculates group VWAP & monotonic floor
       * `L_N` (L5 at C + 5*step): closes ENTIRE L-group (`Q, L2..L_{N-1}`) for full take-profit (`GRID_GROUP_TP`)
   - L-Group Trailing Take-Profit:
       * `floor = max(prev_floor, group_vwap * (1 + rt_fee))` (monotonically upward only)
       * `trail_stop = max(prev_trail, floor, peak * (1 - retracement))` (`retracement` in 0.8%~1.0%, upward only)
       * When `bid <= trail_stop`: closes entire L-group via `GRID_TRAIL_TP`
   - Global Stop-Loss: `S_N * (1 - global_sl_pct)` (default 5% below S5 -> ~92.15 when C=100, step=0.6%).
     Closes ALL open slots via `GRID_GLOBAL_SL` and triggers multi-hour cooldown.

3. SHORT Mode (`locked_mode == "SHORT"`, exact mirror — all positions are SHORT):
   - `QS` (Q', 100% at S1), `CS` (C', short at C, cover at S1; converts to `QS` if `QS` not held),
     `L1..L_N` (100% oscillation shorts covering 1 step lower), `S2 (50%), S3 (30%), S4 (10%)`
     pyramid shorts activating S-group trailing TP, `S_N` (S5) full group close, Global SL above `L_N` by 5%.

4. Shared Restart, Reconciliation, Cooldowns & Range Drift:
   - Per-slot re-arm hysteresis: after a slot closes, price must return >= 0.03% to its outer side + 1.5s cooldown.
   - After all positions close in a locked cycle:
       * Requires exchange zero-position & zero-order verification (cleanup any residual before re-anchor)
       * Normal TP cycle cooldown: 4 minutes (within 3~5 minutes)
       * Global SL cooldown: 3 hours (multi-hour protection)
       * Once verified flat & cooldown expires: pulls latest mid as new `center_price`, resets `locked_mode = "NONE"`,
         rebuilds all levels with every slot freshly armed.
   - Idle range drift (`locked_mode == "NONE"`, flat, not in cooldown): if `|mid - C| / C > grid_range_pct` (±3.5%),
     auto-recenters cleanly to latest mid.
"""
from typing import Dict, List, Optional, Tuple

from backend.math_engine import mono_ms
from backend.models import SymbolStrategy
from backend.strategies.base import BaseGridStrategy

# Cooldown constants (milliseconds)
SLOT_CLOSE_COOLDOWN_MS = 1500.0        # 1~2s per-slot post-close cooldown
TP_CYCLE_COOLDOWN_MS = 240_000.0       # 4 min (3~5 min) post-TP restart cooldown
GLOBAL_SL_COOLDOWN_MS = 10_800_000.0   # 3 hours post-global-SL cooldown
REARM_OUTER_BUF_PCT = 0.0003           # 0.03% outer-side re-arm hysteresis

# Pyramid level ratios for L2/S2, L3/S3, L4/S4, ...
PYRAMID_LEVEL_RATIOS = {
    2: 0.50,
    3: 0.30,
    4: 0.10,
}


class ReverseOscillatingGridStrategy(BaseGridStrategy):
    strategy_type = "REVERSE_GRID"
    default_grid_mode = "REVERSE_OSCILLATING"
    default_regime_reason = "多空震荡（待锁定方向 · 涨破L1做多 / 跌破S1做空）"

    def __init__(self, symbol: str):
        super().__init__(symbol)
        self.locked_mode: str = "NONE"  # "NONE" | "LONG" | "SHORT"
        self.group_trail_active: bool = False
        self.group_vwap: float = 0.0
        self.group_floor_price: float = 0.0
        self.full_tp_price: float = 0.0
        self.global_sl_price: float = 0.0
        self.cycle_phase: str = "ACTIVE"  # "ACTIVE" | "PENDING_RECONCILE" | "COOLDOWN"
        self.needs_flat_reconcile: bool = False
        self.reconcile_verified: bool = True
        self.restart_cooldown_until_mono: float = 0.0
        self.restart_reason: str = ""  # "" | "TP_COOLDOWN" | "GLOBAL_SL_COOLDOWN"
        self._last_cycle_ended_by_sl: bool = False
        self._pending_conversions: List[dict] = []

    # ---------------- Strategy capability & formatting hooks ----------------

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return bool(cfg.use_post_only_tp)

    def uses_livermore_sl_amend(self) -> bool:
        return False

    def get_rt_fee(self, cfg: SymbolStrategy, taker_fee: float, maker_fee: float) -> float:
        # Oscillation slots can exit Maker, while group trailing / L5 / SL exits are Taker;
        # use conservative Taker+Taker RT fee for group breakeven floor safety.
        return 2.0 * taker_fee

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        role = str(lv.get("role") or "")
        if role in ("BASE", "PYRAMID") and self.group_trail_active:
            return "GRID_TRAIL_TP"
        return "GRID_GLOBAL_SL"

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        if self.locked_mode == "LONG":
            return ("多空震荡·多头模式", "做多建仓")
        if self.locked_mode == "SHORT":
            return ("多空震荡·空头模式", "做空建仓")
        return ("多空震荡开仓", "涨破L1锁多" if side == "LONG" else "跌破S1锁空")

    def get_exit_log_tag(self) -> str:
        return "多空震荡平仓"

    def get_mode_display_name(self) -> str:
        if self.locked_mode == "LONG":
            return "多空震荡(多)"
        if self.locked_mode == "SHORT":
            return "多空震荡(空)"
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
        role = str(lv.get("role") or "")
        prec = cfg.price_precision
        if role == "OSC":
            return (
                f"[多空震荡成交] {self.symbol} [{slot_id}] {side} (震荡格) 均价: {avg_price:.{prec}f} | "
                f"数量: {filled_qty} | 止盈目标: {lv.get('tp_price', 0):.{prec}f} | "
                f"全局止损: {lv.get('sl_price', 0):.{prec}f}{prot_suffix}"
            )
        trail_note = (
            f"组均价: {self.group_vwap:.{prec}f} | 保底: {self.group_floor_price:.{prec}f}"
            if self.group_trail_active
            else f"顶格全平: {self.full_tp_price:.{prec}f}"
        )
        return (
            f"[多空震荡成交] {self.symbol} [{slot_id}] {side} ({'底仓' if role == 'BASE' else '金字塔'}) "
            f"均价: {avg_price:.{prec}f} | 数量: {filled_qty} | {trail_note} | "
            f"止损: {lv.get('sl_price', 0):.{prec}f}{prot_suffix}"
        )

    def consume_slot_conversions(self) -> List[dict]:
        """Returns and clears any zero-order in-place C->Q / CS->QS conversions to sync in engine."""
        out = list(self._pending_conversions)
        self._pending_conversions.clear()
        return out

    def needs_post_cycle_flat_reconcile(self) -> bool:
        return bool(self.needs_flat_reconcile)

    def mark_reconcile_verified_clean(self):
        """Called by engine after exchange positions == 0 and pending orders == 0 are confirmed."""
        self.needs_flat_reconcile = False
        self.reconcile_verified = True
        if self.cycle_phase == "PENDING_RECONCILE":
            self.cycle_phase = "COOLDOWN"
            self._update_regime_reason()

    def clear_cooldowns(self):
        super().clear_cooldowns()
        self.restart_cooldown_until_mono = 0.0
        self.restart_reason = ""
        if self.cycle_phase == "COOLDOWN" and self.reconcile_verified:
            self.cycle_phase = "ACTIVE"

    def reset_runtime_state(self):
        super().reset_runtime_state()
        self.locked_mode = "NONE"
        self.group_trail_active = False
        self.group_vwap = 0.0
        self.group_floor_price = 0.0
        self.full_tp_price = 0.0
        self.global_sl_price = 0.0
        self.cycle_phase = "ACTIVE"
        self.needs_flat_reconcile = False
        self.reconcile_verified = True
        self.restart_cooldown_until_mono = 0.0
        self.restart_reason = ""
        self._last_cycle_ended_by_sl = False
        self._pending_conversions.clear()

    # ---------------- Internal helpers ----------------

    @staticmethod
    def _pyramid_ratio(level_idx: int) -> float:
        if level_idx in PYRAMID_LEVEL_RATIOS:
            return PYRAMID_LEVEL_RATIOS[level_idx]
        # Graceful decay for N > 5
        return max(0.05, round(0.10 * (0.5 ** max(0, level_idx - 4)), 4))

    @staticmethod
    def _trail_retracement(cfg: SymbolStrategy) -> float:
        # User spec: 0.8% ~ 1.0% retracement (exact 0.80% at step=0.60%, scaling up to 1.00% at step>=0.80%)
        step = max(0.0001, float(cfg.grid_step_pct))
        if step <= 0.0065:
            return 0.0080
        return min(0.0100, max(0.0080, round(step * 1.25, 4)))

    def _compute_sig_params(self, cfg: SymbolStrategy) -> tuple:
        return (
            cfg.strategy_type,
            self.active_grid_mode,
            cfg.grid_side_mode,
            int(cfg.grid_levels),
            round(float(cfg.order_usdt), 4),
            round(float(cfg.grid_step_pct), 6),
            round(float(cfg.grid_range_pct), 6),
            round(float(cfg.grid_stop_loss_pct), 6),
            int(cfg.price_precision),
        )

    def _update_regime_reason(self):
        if self.cycle_phase == "PENDING_RECONCILE":
            self.last_regime_reason = "多空震荡 · 周期平仓完成，正在对账确认交易所零持仓/零挂单"
        elif self.cycle_phase == "COOLDOWN":
            tag = "全局止损长冷却" if self.restart_reason == "GLOBAL_SL_COOLDOWN" else "止盈重启冷却(4m)"
            self.last_regime_reason = f"多空震荡 · {tag}，冷却结束后拉最新价重锚"
        elif self.locked_mode == "LONG":
            if self.group_trail_active:
                self.last_regime_reason = (
                    f"多头模式(锁定) · L组移动止盈已激活 (均价 {self.group_vwap:g} / 保底 {self.group_floor_price:g})"
                )
            else:
                self.last_regime_reason = "多头模式(锁定) · 底仓Q持仓 · C/S档震荡套利 · 上破L2激活追踪"
        elif self.locked_mode == "SHORT":
            if self.group_trail_active:
                self.last_regime_reason = (
                    f"空头模式(锁定) · S组移动止盈已激活 (均价 {self.group_vwap:g} / 保底 {self.group_floor_price:g})"
                )
            else:
                self.last_regime_reason = "空头模式(锁定) · 底仓Q'持仓 · C'/L档震荡套利 · 下破S2激活追踪"
        else:
            self.last_regime_reason = "多空震荡（待锁定方向 · 涨破L1做多 / 跌破S1做空）"

    def _new_slot(
        self,
        slot_id: str,
        side: str,
        level_idx: int,
        role: str,
        trigger_price: float,
        tp_price: float,
        sl_price: float,
        order_usdt: float,
        weight_pct: float,
        sort_price: float,
        armed: bool = True,
        cd_u: float = 0.0,
        last_c: float = 0.0,
    ) -> dict:
        return {
            "slot_id": slot_id,
            "side": side,
            "level_idx": level_idx,
            "role": role,  # "BASE" | "OSC" | "PYRAMID"
            "grid_mode": "REVERSE_OSCILLATING",
            "trigger_price": trigger_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "sort_price": sort_price,
            "order_usdt": round(float(order_usdt), 2),
            "weight_pct": round(float(weight_pct), 1),
            "status": "IDLE",
            "armed": armed,
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

    def _build_levels(self, cfg: SymbolStrategy, preserve_cooldowns: bool = False):
        """Builds levels for current `locked_mode` (NONE, LONG, or SHORT)."""
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
        prec = self._calc_prec(cfg)
        step = max(0.0001, float(cfg.grid_step_pct))
        sl_pct = max(0.002, float(cfg.grid_stop_loss_pct))
        n = max(2, int(cfg.grid_levels))
        base_usdt = float(cfg.order_usdt)
        c_px = round(self.center_price, prec)

        l_px = {i: round(self.center_price * (1.0 + i * step), prec) for i in range(1, n + 1)}
        s_px = {i: round(self.center_price * max(0.05, 1.0 - i * step), prec) for i in range(1, n + 1)}

        long_global_sl = round(s_px[n] * max(0.05, 1.0 - sl_pct), prec)
        short_global_sl = round(l_px[n] * (1.0 + sl_pct), prec)

        new_levels: Dict[str, dict] = {}

        if self.locked_mode == "NONE":
            self.full_tp_price = 0.0
            self.global_sl_price = 0.0
            self.group_trail_active = False
            self.group_vwap = 0.0
            self.group_floor_price = 0.0
            self.long_peak_price = 0.0
            self.short_trough_price = 0.0
            self.long_trail_stop_price = 0.0
            self.short_trail_stop_price = 0.0

            if cfg.grid_side_mode in ("LONG_SHORT", "LONG_ONLY"):
                cd_u, last_c = old_cooldowns.get("Q", (0.0, 0.0))
                new_levels["Q"] = self._new_slot(
                    slot_id="Q",
                    side="LONG",
                    level_idx=1,
                    role="BASE",
                    trigger_price=l_px[1],
                    tp_price=0.0,
                    sl_price=long_global_sl,
                    order_usdt=base_usdt,
                    weight_pct=100.0,
                    sort_price=l_px[1],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )
            if cfg.grid_side_mode in ("LONG_SHORT", "SHORT_ONLY"):
                cd_u, last_c = old_cooldowns.get("QS", (0.0, 0.0))
                new_levels["QS"] = self._new_slot(
                    slot_id="QS",
                    side="SHORT",
                    level_idx=1,
                    role="BASE",
                    trigger_price=s_px[1],
                    tp_price=0.0,
                    sl_price=short_global_sl,
                    order_usdt=base_usdt,
                    weight_pct=100.0,
                    sort_price=s_px[1],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )

        elif self.locked_mode == "LONG":
            self.full_tp_price = l_px[n]
            self.global_sl_price = long_global_sl
            # Base slot Q (at L1, 100%, no fixed TP)
            cd_u, last_c = old_cooldowns.get("Q", (0.0, 0.0))
            new_levels["Q"] = self._new_slot(
                slot_id="Q",
                side="LONG",
                level_idx=1,
                role="BASE",
                trigger_price=l_px[1],
                tp_price=0.0,
                sl_price=long_global_sl,
                order_usdt=base_usdt,
                weight_pct=100.0,
                sort_price=l_px[1],
                armed=False,
                cd_u=cd_u,
                last_c=last_c,
            )
            # Pyramid slots L2..L_{n-1} (50%, 30%, 10%..., no fixed TP; L_n is full L-group close!)
            for i in range(2, n):
                sid = f"L{i}"
                ratio = self._pyramid_ratio(i)
                cd_u, last_c = old_cooldowns.get(sid, (0.0, 0.0))
                new_levels[sid] = self._new_slot(
                    slot_id=sid,
                    side="LONG",
                    level_idx=i,
                    role="PYRAMID",
                    trigger_price=l_px[i],
                    tp_price=0.0,
                    sl_price=long_global_sl,
                    order_usdt=base_usdt * ratio,
                    weight_pct=ratio * 100.0,
                    sort_price=l_px[i],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )
            # Oscillation dip-buy slot C (buy at C, sell at L1, 100%)
            cd_u, last_c = old_cooldowns.get("C", (0.0, 0.0))
            new_levels["C"] = self._new_slot(
                slot_id="C",
                side="LONG",
                level_idx=0,
                role="OSC",
                trigger_price=c_px,
                tp_price=l_px[1],
                sl_price=long_global_sl,
                order_usdt=base_usdt,
                weight_pct=100.0,
                sort_price=c_px,
                armed=True,
                cd_u=cd_u,
                last_c=last_c,
            )
            # Oscillation dip-buy slots S1..S_n (buy at S_i, sell at C / S_{i-1}, 100%)
            for i in range(1, n + 1):
                sid = f"S{i}"
                sell_tp = c_px if i == 1 else s_px[i - 1]
                cd_u, last_c = old_cooldowns.get(sid, (0.0, 0.0))
                new_levels[sid] = self._new_slot(
                    slot_id=sid,
                    side="LONG",
                    level_idx=i,
                    role="OSC",
                    trigger_price=s_px[i],
                    tp_price=sell_tp,
                    sl_price=long_global_sl,
                    order_usdt=base_usdt,
                    weight_pct=100.0,
                    sort_price=s_px[i],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )

        elif self.locked_mode == "SHORT":
            self.full_tp_price = s_px[n]
            self.global_sl_price = short_global_sl
            # Base slot QS (Q' at S1, 100%, no fixed TP)
            cd_u, last_c = old_cooldowns.get("QS", (0.0, 0.0))
            new_levels["QS"] = self._new_slot(
                slot_id="QS",
                side="SHORT",
                level_idx=1,
                role="BASE",
                trigger_price=s_px[1],
                tp_price=0.0,
                sl_price=short_global_sl,
                order_usdt=base_usdt,
                weight_pct=100.0,
                sort_price=s_px[1],
                armed=False,
                cd_u=cd_u,
                last_c=last_c,
            )
            # Pyramid slots S2..S_{n-1} (50%, 30%, 10%..., no fixed TP; S_n is full S-group close!)
            for i in range(2, n):
                sid = f"S{i}"
                ratio = self._pyramid_ratio(i)
                cd_u, last_c = old_cooldowns.get(sid, (0.0, 0.0))
                new_levels[sid] = self._new_slot(
                    slot_id=sid,
                    side="SHORT",
                    level_idx=i,
                    role="PYRAMID",
                    trigger_price=s_px[i],
                    tp_price=0.0,
                    sl_price=short_global_sl,
                    order_usdt=base_usdt * ratio,
                    weight_pct=ratio * 100.0,
                    sort_price=s_px[i],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )
            # Oscillation bounce-short slot CS (C': short at C, cover at S1, 100%)
            cd_u, last_c = old_cooldowns.get("CS", (0.0, 0.0))
            new_levels["CS"] = self._new_slot(
                slot_id="CS",
                side="SHORT",
                level_idx=0,
                role="OSC",
                trigger_price=c_px,
                tp_price=s_px[1],
                sl_price=short_global_sl,
                order_usdt=base_usdt,
                weight_pct=100.0,
                sort_price=c_px,
                armed=True,
                cd_u=cd_u,
                last_c=last_c,
            )
            # Oscillation bounce-short slots L1..L_n (short at L_i, cover at C / L_{i-1}, 100%)
            for i in range(1, n + 1):
                sid = f"L{i}"
                cover_tp = c_px if i == 1 else l_px[i - 1]
                cd_u, last_c = old_cooldowns.get(sid, (0.0, 0.0))
                new_levels[sid] = self._new_slot(
                    slot_id=sid,
                    side="SHORT",
                    level_idx=i,
                    role="OSC",
                    trigger_price=l_px[i],
                    tp_price=cover_tp,
                    sl_price=short_global_sl,
                    order_usdt=base_usdt,
                    weight_pct=100.0,
                    sort_price=l_px[i],
                    armed=True,
                    cd_u=cd_u,
                    last_c=last_c,
                )

        self.levels = new_levels
        self._update_regime_reason()

    def _lock_into_mode(self, mode: str, base_slot_id: str, base_snapshot: dict, cfg: SymbolStrategy):
        """Transitions from locked_mode == 'NONE' into 'LONG' or 'SHORT' preserving the filled base slot."""
        self.locked_mode = mode
        self._build_levels(cfg)
        if base_slot_id in self.levels:
            lv = self.levels[base_slot_id]
            for k in (
                "status",
                "armed",
                "entry_price",
                "filled_qty",
                "contracts",
                "ct_val",
                "mgn_mode",
                "opened_mono_ms",
                "maker_tp_order_id",
                "maker_tp_live",
                "cond_sl_algo_id",
                "cond_sl_live",
                "entry_fee_usdt",
                "recovered",
                "restored_from_state",
            ):
                if k in base_snapshot:
                    lv[k] = base_snapshot[k]
            lv["sl_price"] = self.global_sl_price
            lv["tp_price"] = 0.0
        self._update_regime_reason()

    def _group_open_slots(self) -> List[dict]:
        """Returns all OPEN slots belonging to the trend group (Q + L2..L_{N-1} in LONG, QS + S2..S_{N-1} in SHORT)."""
        if self.locked_mode not in ("LONG", "SHORT"):
            return []
        return [
            lv
            for lv in self.levels.values()
            if lv["status"] == "OPEN"
            and lv["side"] == self.locked_mode
            and lv.get("role") in ("BASE", "PYRAMID")
            and float(lv.get("entry_price") or 0.0) > 0
        ]

    def _recompute_group_trailing(self, cfg: SymbolStrategy, round_trip_fee: float, ref_price: float = 0.0):
        """
        Recomputes group weighted-average cost (VWAP), monotonic breakeven floor, and monotonic trailing stop.
        - LONG mode:  floor = max(prev_floor, vwap * (1 + rt_fee))
                      trail = max(prev_trail, floor, peak * (1 - retracement))
        - SHORT mode: floor = min(prev_floor, vwap * (1 - rt_fee))
                      trail = min(prev_trail, floor, trough * (1 + retracement))
        """
        group_slots = self._group_open_slots()
        if not group_slots:
            return
        prec = self._calc_prec(cfg)
        rt_fee = max(0.0004, float(round_trip_fee))
        retr = self._trail_retracement(cfg)

        total_qty = 0.0
        weighted_sum = 0.0
        for lv in group_slots:
            q = float(lv.get("filled_qty") or 0.0)
            ep = float(lv.get("entry_price") or 0.0)
            if q <= 0:
                q = max(1e-9, float(lv.get("order_usdt") or cfg.order_usdt) / max(1e-9, ep))
            total_qty += q
            weighted_sum += ep * q
        if total_qty <= 0:
            return

        vwap = weighted_sum / total_qty
        self.group_vwap = round(vwap, prec)

        if not self.group_trail_active:
            return

        if self.locked_mode == "LONG":
            max_entry = max(float(lv.get("entry_price") or 0.0) for lv in group_slots)
            # Add calibrated slippage + net-profit cushion (+0.08%~+0.10%) above RT fee so live Taker exit
            # slippage at the group floor still settles strictly net-profitable (while never crossing L2 entry!)
            headroom_pct = max(0.0, (max_entry - vwap) / max(1e-12, vwap))
            slip_buf = min(0.0010, max(0.0005, headroom_pct * 0.35)) if headroom_pct > rt_fee + 0.0006 else 0.0004
            raw_floor = round(vwap * (1.0 + rt_fee + slip_buf), prec)
            self.group_floor_price = max(self.group_floor_price, raw_floor) if self.group_floor_price > 0 else raw_floor
            self.long_peak_price = max(self.long_peak_price, max_entry, ref_price)
            cand_trail = round(max(self.group_floor_price, self.long_peak_price * (1.0 - retr)), prec)
            self.long_trail_stop_price = (
                max(self.long_trail_stop_price, cand_trail) if self.long_trail_stop_price > 0 else cand_trail
            )
            # Sync group slots' SL upward toward trail stop (throttled on >=0.20% improvement for live OKX amend)
            target_sl = self.long_trail_stop_price
            for lv in group_slots:
                cur_sl = float(lv.get("sl_price") or 0.0)
                if cur_sl < self.group_floor_price - 1e-9 or (
                    cur_sl > 0 and target_sl >= cur_sl * 1.0020
                ):
                    if ref_price <= 0 or ref_price >= target_sl * 1.0008:
                        lv["sl_price"] = target_sl
                        lv["sl_needs_amend"] = True
        elif self.locked_mode == "SHORT":
            min_entry = min(float(lv.get("entry_price") or 0.0) for lv in group_slots)
            headroom_pct = max(0.0, (vwap - min_entry) / max(1e-12, vwap))
            slip_buf = min(0.0010, max(0.0005, headroom_pct * 0.35)) if headroom_pct > rt_fee + 0.0006 else 0.0004
            raw_floor = round(vwap * max(0.05, 1.0 - rt_fee - slip_buf), prec)
            self.group_floor_price = min(self.group_floor_price, raw_floor) if self.group_floor_price > 0 else raw_floor
            trough_cands = [p for p in (self.short_trough_price, min_entry, ref_price) if p > 0]
            self.short_trough_price = min(trough_cands) if trough_cands else 0.0
            cand_trail = round(min(self.group_floor_price, self.short_trough_price * (1.0 + retr)), prec)
            self.short_trail_stop_price = (
                min(self.short_trail_stop_price, cand_trail) if self.short_trail_stop_price > 0 else cand_trail
            )
            target_sl = self.short_trail_stop_price
            for lv in group_slots:
                cur_sl = float(lv.get("sl_price") or 0.0)
                if cur_sl <= 0 or cur_sl > self.group_floor_price + 1e-9 or target_sl <= cur_sl * 0.9980:
                    if ref_price <= 0 or ref_price <= target_sl * 0.9992:
                        lv["sl_price"] = target_sl
                        lv["sl_needs_amend"] = True

        self._update_regime_reason()

    def _begin_post_cycle_restart(self, now_m: float, is_global_sl: bool):
        """Transitions a completed locked cycle into reconcile + cooldown before re-anchoring to NONE."""
        if self.locked_mode == "NONE":
            return
        self._last_cycle_ended_by_sl = bool(is_global_sl)
        cd_ms = GLOBAL_SL_COOLDOWN_MS if is_global_sl else TP_CYCLE_COOLDOWN_MS
        self.restart_cooldown_until_mono = max(self.restart_cooldown_until_mono, now_m + cd_ms)
        self.restart_reason = "GLOBAL_SL_COOLDOWN" if is_global_sl else "TP_COOLDOWN"
        if is_global_sl:
            self._post_sl_cooldown_until_mono = max(self._post_sl_cooldown_until_mono, now_m + cd_ms)
        self.cycle_phase = "PENDING_RECONCILE"
        self.needs_flat_reconcile = True
        self.reconcile_verified = False
        self.group_trail_active = False
        self.group_vwap = 0.0
        self.group_floor_price = 0.0
        self.long_peak_price = 0.0
        self.short_trough_price = 0.0
        self.long_trail_stop_price = 0.0
        self.short_trail_stop_price = 0.0
        self._update_regime_reason()

    # ---------------- Strategy lifecycle contract ----------------

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        self.active_grid_mode = "REVERSE_OSCILLATING"
        sig_params = self._compute_sig_params(cfg)

        if self.center_price <= 0 and current_mid > 0:
            self.center_price = current_mid
            self.locked_mode = "NONE"
            self.cycle_phase = "ACTIVE"
            self._build_levels(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            return

        if not self._has_active_slots() and self.locked_mode == "NONE" and sig_params != self._last_sig_params:
            if self.center_price > 0:
                self._build_levels(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            self.clear_cooldowns()
        elif self._has_active_slots() and self.locked_mode in ("LONG", "SHORT"):
            # Dynamically sync global_sl_price if user updates grid_stop_loss_pct while cycle is active
            prec = self._calc_prec(cfg)
            step = max(0.0001, float(cfg.grid_step_pct))
            sl_pct = max(0.002, float(cfg.grid_stop_loss_pct))
            n = max(2, int(cfg.grid_levels))
            if self.locked_mode == "LONG":
                sn = round(self.center_price * max(0.05, 1.0 - n * step), prec)
                self.global_sl_price = round(sn * max(0.05, 1.0 - sl_pct), prec)
            else:
                ln = round(self.center_price * (1.0 + n * step), prec)
                self.global_sl_price = round(ln * (1.0 + sl_pct), prec)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        if new_center <= 0 or self._has_active_slots():
            return False
        self.active_grid_mode = "REVERSE_OSCILLATING"
        self.center_price = new_center
        self.locked_mode = "NONE"
        self.cycle_phase = "ACTIVE"
        self.needs_flat_reconcile = False
        self.reconcile_verified = True
        self.restart_cooldown_until_mono = 0.0
        self.restart_reason = ""
        self._post_sl_cooldown_until_mono = 0.0
        self._build_levels(cfg)
        sig_params = self._compute_sig_params(cfg)
        self._last_user_sig_params = sig_params
        self._last_sig_params = sig_params
        self._update_regime_reason()
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
            self.center_price = mid
            self.locked_mode = "NONE"
            self.cycle_phase = "ACTIVE"
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
        n = max(2, int(cfg.grid_levels))
        sl_pct = max(0.002, float(cfg.grid_stop_loss_pct))

        # Keep global_sl_price responsive to live user parameter edits
        if self.locked_mode == "LONG":
            s_n = round(self.center_price * max(0.05, 1.0 - n * step), prec)
            new_gsl = round(s_n * max(0.05, 1.0 - sl_pct), prec)
            if abs(new_gsl - self.global_sl_price) > 1e-9:
                self.global_sl_price = new_gsl
                for lv in self.levels.values():
                    if lv["side"] == "LONG" and (lv.get("role") == "OSC" or not self.group_trail_active):
                        lv["sl_price"] = new_gsl
                        if lv["status"] == "OPEN" and bid >= new_gsl * 1.0025:
                            lv["sl_needs_amend"] = True
        elif self.locked_mode == "SHORT":
            l_n = round(self.center_price * (1.0 + n * step), prec)
            new_gsl = round(l_n * (1.0 + sl_pct), prec)
            if abs(new_gsl - self.global_sl_price) > 1e-9:
                self.global_sl_price = new_gsl
                for lv in self.levels.values():
                    if lv["side"] == "SHORT" and (lv.get("role") == "OSC" or not self.group_trail_active):
                        lv["sl_price"] = new_gsl
                        if lv["status"] == "OPEN" and ask <= new_gsl * 0.9975:
                            lv["sl_needs_amend"] = True

        # 0. Post-cycle reconcile & cooldown -> auto-reanchor to NONE once verified flat and cooldown elapsed
        if self.locked_mode in ("LONG", "SHORT") and not self._has_active_slots():
            if self.cycle_phase == "ACTIVE":
                self._begin_post_cycle_restart(now_m, is_global_sl=self._last_cycle_ended_by_sl)
            if not self.reconcile_verified or self.needs_flat_reconcile:
                return [], []
            if now_m < self.restart_cooldown_until_mono:
                return [], []
            # Cooldown expired & exchange confirmed 0 position + 0 orders -> re-anchor at live mid!
            self.center_price = mid
            self.locked_mode = "NONE"
            self.cycle_phase = "ACTIVE"
            self.restart_cooldown_until_mono = 0.0
            self.restart_reason = ""
            self._post_sl_cooldown_until_mono = 0.0
            self._last_cycle_ended_by_sl = False
            self._build_levels(cfg)
            sig_params = self._compute_sig_params(cfg)
            self._last_user_sig_params = sig_params
            self._last_sig_params = sig_params
            self._pending_regime_log = (
                f"[多空震荡重锚重启] {self.symbol} 对账零仓位且冷却结束，新中枢={mid:.{prec}f}，模式重置为待锁定(NONE)"
            )
            return [], []

        # 1. Evaluate Exits for OPEN slots (Global SL, L5/S5 Full TP, Group Trailing TP, Osc Scalp TP, C->Q Conversion)
        if self.locked_mode == "LONG":
            # 1a. Global Hard Stop-Loss (below S_N by global_sl_pct, e.g. ~92.15) -> close ALL LONG slots!
            if self.global_sl_price > 0 and bid <= self.global_sl_price:
                for lv in self.levels.values():
                    if lv["status"] != "OPEN" or lv["side"] != "LONG":
                        continue
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    if self._should_fire_software_sl(lv, bid, self.global_sl_price, True, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
                        exits.append({**lv, "exit_reason": "GRID_GLOBAL_SL", "target_price": bid})
                if exits:
                    return [], exits

            # 1b. Update L-group trailing stop if active
            if self.group_trail_active:
                self._recompute_group_trailing(cfg, round_trip_fee, ref_price=bid)

            # 1c. L_N (L5 = 103.00) Full Take-Profit -> close ENTIRE L-group (and any open LONG slots)
            if self.full_tp_price > 0 and bid >= self.full_tp_price:
                for lv in self.levels.values():
                    if lv["status"] != "OPEN" or lv["side"] != "LONG":
                        continue
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_GROUP_TP", "target_price": bid})
                if exits:
                    return [], exits

            # 1d. L-Group Trailing Stop Hit -> close ENTIRE L-group (Q, L2..L_{N-1})
            if self.group_trail_active and self.long_trail_stop_price > 0 and bid <= self.long_trail_stop_price:
                for lv in self._group_open_slots():
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_TRAIL_TP", "target_price": bid})
                if exits:
                    return [], exits

            # 1e. Individual Oscillation Scalp Exits (C, S1..S5) & Special Rule: C -> Q conversion when Q is absent!
            q_lv = self.levels.get("Q")
            q_held = bool(q_lv and q_lv["status"] in ("OPEN", "ENTERING", "EXITING"))
            for lv in self.levels.values():
                if lv["status"] != "OPEN" or lv["side"] != "LONG" or lv.get("role") != "OSC":
                    continue
                if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                tp_px = float(lv.get("tp_price") or 0.0)
                if tp_px <= 0 or bid < tp_px:
                    if lv.get("maker_tp_live") and float(lv.get("maker_tp_touched_mono") or 0.0) > 0.0 and bid < tp_px * 0.9995:
                        lv["maker_tp_touched_mono"] = 0.0
                    continue

                # Special rule from spec: if C is held & Q is NOT held when price reaches L1 (100.60),
                # convert C directly into base Q (zero extra exchange order, net position unchanged!)
                if lv["slot_id"] == "C" and not q_held and q_lv and q_lv["status"] == "IDLE":
                    self._convert_c_to_base(lv, q_lv, now_m)
                    q_held = True
                    continue

                if lv.get("maker_tp_live"):
                    touched = float(lv.get("maker_tp_touched_mono") or 0.0)
                    if touched <= 0.0:
                        lv["maker_tp_touched_mono"] = now_m
                        touched = now_m
                    if (now_m - touched >= 4500.0) or (bid >= tp_px * 1.0025):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = False
                        exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                else:
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})

        elif self.locked_mode == "SHORT":
            # 1a. Global Hard Stop-Loss (above L_N by global_sl_pct, e.g. ~108.15) -> close ALL SHORT slots!
            if self.global_sl_price > 0 and ask >= self.global_sl_price:
                for lv in self.levels.values():
                    if lv["status"] != "OPEN" or lv["side"] != "SHORT":
                        continue
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    if self._should_fire_software_sl(lv, ask, self.global_sl_price, False, now_m):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = True
                        exits.append({**lv, "exit_reason": "GRID_GLOBAL_SL", "target_price": ask})
                if exits:
                    return [], exits

            # 1b. Update S-group trailing stop if active
            if self.group_trail_active:
                self._recompute_group_trailing(cfg, round_trip_fee, ref_price=ask)

            # 1c. S_N (S5 = 97.00) Full Take-Profit -> close ENTIRE S-group (and any open SHORT slots)
            if self.full_tp_price > 0 and ask <= self.full_tp_price:
                for lv in self.levels.values():
                    if lv["status"] != "OPEN" or lv["side"] != "SHORT":
                        continue
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_GROUP_TP", "target_price": ask})
                if exits:
                    return [], exits

            # 1d. S-Group Trailing Stop Hit -> close ENTIRE S-group (QS, S2..S_{N-1})
            if self.group_trail_active and self.short_trail_stop_price > 0 and ask >= self.short_trail_stop_price:
                for lv in self._group_open_slots():
                    if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                        continue
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_TRAIL_TP", "target_price": ask})
                if exits:
                    return [], exits

            # 1e. Individual Oscillation Scalp Exits (CS, L1..L5) & Special Rule: CS -> QS conversion when QS is absent!
            qs_lv = self.levels.get("QS")
            qs_held = bool(qs_lv and qs_lv["status"] in ("OPEN", "ENTERING", "EXITING"))
            for lv in self.levels.values():
                if lv["status"] != "OPEN" or lv["side"] != "SHORT" or lv.get("role") != "OSC":
                    continue
                if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                    continue
                tp_px = float(lv.get("tp_price") or 0.0)
                if tp_px <= 0 or ask > tp_px:
                    if lv.get("maker_tp_live") and float(lv.get("maker_tp_touched_mono") or 0.0) > 0.0 and ask > tp_px * 1.0005:
                        lv["maker_tp_touched_mono"] = 0.0
                    continue

                if lv["slot_id"] == "CS" and not qs_held and qs_lv and qs_lv["status"] == "IDLE":
                    self._convert_c_to_base(lv, qs_lv, now_m)
                    qs_held = True
                    continue

                if lv.get("maker_tp_live"):
                    touched = float(lv.get("maker_tp_touched_mono") or 0.0)
                    if touched <= 0.0:
                        lv["maker_tp_touched_mono"] = now_m
                        touched = now_m
                    if (now_m - touched >= 4500.0) or (ask <= tp_px * 0.9975):
                        lv["status"] = "EXITING"
                        lv["exiting_is_sl"] = False
                        exits.append({**lv, "exit_reason": "GRID_TP", "target_price": ask})
                else:
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = False
                    exits.append({**lv, "exit_reason": "GRID_TP", "target_price": ask})

        # Also handle any legacy opposite-side restored slots safely until flat
        for lv in self.levels.values():
            if lv["status"] != "OPEN" or not lv.get("legacy_closing_only"):
                continue
            if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                continue
            tp_px = float(lv.get("tp_price") or 0.0)
            sl_px = float(lv.get("sl_price") or 0.0)
            if lv["side"] == "LONG":
                if tp_px > 0 and bid >= tp_px:
                    lv["status"] = "EXITING"
                    exits.append({**lv, "exit_reason": "GRID_TP", "target_price": bid})
                elif sl_px > 0 and bid <= sl_px:
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = True
                    exits.append({**lv, "exit_reason": "GRID_SL", "target_price": bid})
            else:
                if tp_px > 0 and ask <= tp_px:
                    lv["status"] = "EXITING"
                    exits.append({**lv, "exit_reason": "GRID_TP", "target_price": ask})
                elif sl_px > 0 and ask >= sl_px:
                    lv["status"] = "EXITING"
                    lv["exiting_is_sl"] = True
                    exits.append({**lv, "exit_reason": "GRID_SL", "target_price": ask})

        # 2. Idle Range Drift Auto-Recenter (ONLY when locked_mode == NONE, flat, and not in cooldown)
        if allow_recenter and self.locked_mode == "NONE" and not self._has_active_slots():
            eff_range = max(float(cfg.grid_range_pct), step * 1.15)
            drifted_out = (
                cfg.grid_recenter_enabled
                and now_m >= self._post_sl_cooldown_until_mono
                and now_m >= self.restart_cooldown_until_mono
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
            or self.cycle_phase != "ACTIVE"
            or now_m < self._post_sl_cooldown_until_mono
            or now_m < self.restart_cooldown_until_mono
            or bool(exits)
            or any(lv.get("status") == "EXITING" for lv in self.levels.values())
        ):
            return [], exits

        # 3. Re-arm disarmed IDLE slots when price returns >= 0.03% to their outer side
        for lv in self.levels.values():
            if lv["status"] != "IDLE" or lv.get("armed", True):
                continue
            t_px = float(lv.get("trigger_price") or 0.0)
            if t_px <= 0:
                continue
            role = str(lv.get("role") or "")
            if self.locked_mode == "LONG":
                if role == "OSC":
                    # Dip-buy slots (C, S1..S5) approach from ABOVE -> outer side is ABOVE trigger by +0.03%
                    if mid >= t_px * (1.0 + REARM_OUTER_BUF_PCT):
                        lv["armed"] = True
                elif role == "PYRAMID":
                    # Breakout-buy slots (L2..L4) approach from BELOW -> outer side is BELOW trigger by -0.03%
                    if mid <= t_px * max(0.05, 1.0 - REARM_OUTER_BUF_PCT):
                        lv["armed"] = True
            elif self.locked_mode == "SHORT":
                if role == "OSC":
                    # Bounce-short slots (CS, L1..L5) approach from BELOW -> outer side is BELOW trigger by -0.03%
                    if mid <= t_px * max(0.05, 1.0 - REARM_OUTER_BUF_PCT):
                        lv["armed"] = True
                elif role == "PYRAMID":
                    # Breakdown-short slots (S2..S4) approach from ABOVE -> outer side is ABOVE trigger by +0.03%
                    if mid >= t_px * (1.0 + REARM_OUTER_BUF_PCT):
                        lv["armed"] = True

        # Prevent concurrent in-flight entries
        if any(lv["status"] == "ENTERING" for lv in self.levels.values()):
            return [], exits

        # 4. Evaluate Entries
        if self.locked_mode == "NONE":
            # First break of L1 (upward -> LONG mode) or S1 (downward -> SHORT mode) locks direction!
            q_lv = self.levels.get("Q")
            qs_lv = self.levels.get("QS")
            if (
                q_lv
                and q_lv["status"] == "IDLE"
                and q_lv.get("armed", True)
                and now_m >= self._slot_cooldown_until(q_lv)
                and ask >= float(q_lv["trigger_price"])
            ):
                q_lv["status"] = "ENTERING"
                res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                entries.append({**q_lv, "reservation_price": res_px, "trigger_exec_price": ask})
            elif (
                qs_lv
                and qs_lv["status"] == "IDLE"
                and qs_lv.get("armed", True)
                and now_m >= self._slot_cooldown_until(qs_lv)
                and bid <= float(qs_lv["trigger_price"])
            ):
                qs_lv["status"] = "ENTERING"
                res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                entries.append({**qs_lv, "reservation_price": res_px, "trigger_exec_price": bid})
            return entries, exits

        if self.locked_mode == "LONG":
            # 4a. Pyramid upward breakouts (L2..L_{N-1}) — only when Q is held and oscillation slots below are cleared
            q_open = bool(self.levels.get("Q") and self.levels["Q"]["status"] == "OPEN")
            osc_open = any(
                lv.get("role") == "OSC" and lv["status"] in ("OPEN", "ENTERING", "EXITING")
                for lv in self.levels.values()
            )
            if q_open and not osc_open and (self.full_tp_price <= 0 or bid < self.full_tp_price):
                pyr_cands = [
                    lv
                    for lv in self.levels.values()
                    if lv.get("role") == "PYRAMID"
                    and lv["status"] == "IDLE"
                    and lv.get("armed", True)
                    and now_m >= self._slot_cooldown_until(lv)
                    and ask >= float(lv["trigger_price"])
                ]
                if pyr_cands:
                    # Fill sequential pyramid levels from lowest crossed upward (L2 -> L3 -> L4)
                    chosen = min(pyr_cands, key=lambda x: int(x["level_idx"]))
                    chosen["status"] = "ENTERING"
                    res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": ask})
                    return entries, exits

            # 4b. Oscillation dip-buys (C, S1..S_N) — buy when ask drops to/below trigger, above global SL
            if self.global_sl_price <= 0 or bid > self.global_sl_price:
                osc_cands = [
                    lv
                    for lv in self.levels.values()
                    if lv.get("role") == "OSC"
                    and lv["status"] == "IDLE"
                    and lv.get("armed", True)
                    and now_m >= self._slot_cooldown_until(lv)
                    and ask <= float(lv["trigger_price"])
                ]
                if osc_cands:
                    # Pick closest crossed level (lowest trigger_price <= ask) so gap drops fill at current grid line
                    chosen = min(osc_cands, key=lambda x: float(x["trigger_price"]))
                    chosen["status"] = "ENTERING"
                    res_px = round(ask * (1.0 + cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": ask})
                    return entries, exits

        elif self.locked_mode == "SHORT":
            # 4a. Pyramid downward breakdowns (S2..S_{N-1}) — only when QS is held and oscillation slots above are cleared
            qs_open = bool(self.levels.get("QS") and self.levels["QS"]["status"] == "OPEN")
            osc_open = any(
                lv.get("role") == "OSC" and lv["status"] in ("OPEN", "ENTERING", "EXITING")
                for lv in self.levels.values()
            )
            if qs_open and not osc_open and (self.full_tp_price <= 0 or ask > self.full_tp_price):
                pyr_cands = [
                    lv
                    for lv in self.levels.values()
                    if lv.get("role") == "PYRAMID"
                    and lv["status"] == "IDLE"
                    and lv.get("armed", True)
                    and now_m >= self._slot_cooldown_until(lv)
                    and bid <= float(lv["trigger_price"])
                ]
                if pyr_cands:
                    chosen = min(pyr_cands, key=lambda x: int(x["level_idx"]))
                    chosen["status"] = "ENTERING"
                    res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": bid})
                    return entries, exits

            # 4b. Oscillation bounce-shorts (CS, L1..L_N) — short when bid rises to/above trigger, below global SL
            if self.global_sl_price <= 0 or ask < self.global_sl_price:
                osc_cands = [
                    lv
                    for lv in self.levels.values()
                    if lv.get("role") == "OSC"
                    and lv["status"] == "IDLE"
                    and lv.get("armed", True)
                    and now_m >= self._slot_cooldown_until(lv)
                    and bid >= float(lv["trigger_price"])
                ]
                if osc_cands:
                    # Pick closest crossed level (highest trigger_price <= bid)
                    chosen = max(osc_cands, key=lambda x: float(x["trigger_price"]))
                    chosen["status"] = "ENTERING"
                    res_px = round(bid * max(0.05, 1.0 - cfg.slippage_pct), prec)
                    entries.append({**chosen, "reservation_price": res_px, "trigger_exec_price": bid})
                    return entries, exits

        return entries, exits

    def _convert_c_to_base(self, c_lv: dict, q_lv: dict, now_m: float):
        """
        Zero-order in-place conversion when price reaches L1 (or S1) while holding C (or CS) without Q (or QS):
        net exchange position is unchanged, eliminating double-fee sell+buy at the exact same price.
        """
        from_sid = c_lv["slot_id"]
        to_sid = q_lv["slot_id"]
        q_lv["status"] = "OPEN"
        q_lv["armed"] = False
        q_lv["entry_price"] = float(c_lv.get("entry_price") or 0.0)
        q_lv["filled_qty"] = float(c_lv.get("filled_qty") or 0.0)
        q_lv["contracts"] = float(c_lv.get("contracts") or 0.0)
        q_lv["ct_val"] = float(c_lv.get("ct_val") or 1.0)
        q_lv["mgn_mode"] = str(c_lv.get("mgn_mode") or "cross")
        q_lv["opened_mono_ms"] = float(c_lv.get("opened_mono_ms") or now_m)
        q_lv["tp_price"] = 0.0
        q_lv["sl_price"] = self.global_sl_price
        q_lv["maker_tp_order_id"] = ""
        q_lv["maker_tp_live"] = False
        q_lv["cond_sl_algo_id"] = str(c_lv.get("cond_sl_algo_id") or "")
        q_lv["cond_sl_live"] = bool(c_lv.get("cond_sl_live", False))
        if "entry_fee_usdt" in c_lv:
            q_lv["entry_fee_usdt"] = float(c_lv.get("entry_fee_usdt") or 0.0)

        old_maker_tp_id = str(c_lv.get("maker_tp_order_id") or "") if c_lv.get("maker_tp_live") else ""
        c_lv["status"] = "IDLE"
        c_lv["armed"] = False
        c_lv["entry_price"] = 0.0
        c_lv["filled_qty"] = 0.0
        c_lv["contracts"] = 0.0
        c_lv["opened_mono_ms"] = 0.0
        c_lv["last_closed_mono"] = now_m
        c_lv["cooldown_until_mono"] = now_m + SLOT_CLOSE_COOLDOWN_MS
        c_lv["maker_tp_order_id"] = ""
        c_lv["maker_tp_live"] = False
        c_lv["maker_tp_touched_mono"] = 0.0
        c_lv["cond_sl_algo_id"] = ""
        c_lv["cond_sl_live"] = False
        c_lv["cond_sl_touched_mono"] = 0.0

        self._pending_conversions.append(
            {
                "symbol": self.symbol,
                "from_slot": from_sid,
                "to_slot": to_sid,
                "side": q_lv["side"],
                "cancel_maker_tp_order_id": old_maker_tp_id,
            }
        )
        self._pending_regime_log = (
            f"[多空震荡无损转换] {self.symbol} 无底仓时到达基准位，[{from_sid}] 原地转为底仓 [{to_sid}]（净仓位不变，零滑点省双边费）"
        )

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
        now_m = mono_ms()
        lv["status"] = "OPEN"
        lv["armed"] = False
        lv["entry_price"] = entry_price
        lv["filled_qty"] = filled_qty
        lv["contracts"] = contracts
        lv["ct_val"] = ct_val
        lv["opened_mono_ms"] = now_m
        lv["maker_tp_touched_mono"] = 0.0
        lv["cond_sl_touched_mono"] = 0.0

        # Lock direction on initial Q (LONG) or QS (SHORT) fill!
        if self.locked_mode == "NONE" and slot_id in ("Q", "QS"):
            target_mode = "LONG" if slot_id == "Q" else "SHORT"
            self._lock_into_mode(target_mode, slot_id, dict(lv), cfg)
            self._pending_regime_log = (
                f"[多空震荡锁定] {self.symbol} 首次触及{'L1' if target_mode == 'LONG' else 'S1'}成交底仓[{slot_id}] @ {entry_price:.{cfg.price_precision}f}，"
                f"锁定{'做多' if target_mode == 'LONG' else '做空'}模式（全局止损 {self.global_sl_price:.{cfg.price_precision}f}）"
            )
            return

        lv = self.levels.get(slot_id)
        if not lv:
            return
        role = str(lv.get("role") or "")
        if role == "PYRAMID":
            # L2 (or S2) activates group trailing TP; L3/L4 (or S3/S4) join group & recalculate monotonic VWAP floor!
            if not self.group_trail_active:
                self.group_trail_active = True
                if self.locked_mode == "LONG":
                    self.long_peak_price = max(self.long_peak_price, entry_price)
                else:
                    self.short_trough_price = (
                        min(self.short_trough_price, entry_price) if self.short_trough_price > 0 else entry_price
                    )
            self._recompute_group_trailing(cfg, round_trip_fee, ref_price=entry_price)
            # Ensure newly filled pyramid slot itself receives the group's trailing stop SL
            lv["sl_price"] = (
                self.long_trail_stop_price if self.locked_mode == "LONG" else self.short_trail_stop_price
            ) or self.global_sl_price
        elif role == "BASE":
            lv["tp_price"] = 0.0
            lv["sl_price"] = self.global_sl_price
            if self.group_trail_active:
                self._recompute_group_trailing(cfg, round_trip_fee, ref_price=entry_price)
        elif role == "OSC":
            # Oscillation scalp slot keeps its exact adjacent grid-line TP and global SL
            lv["sl_price"] = self.global_sl_price
            # If Q (or QS) is NOT held when C (or CS) opens, keep tp_price for software C->Q conversion
            # (sniper_engine checks should_place_maker_tp_for_slot so OKX doesn't auto-sell C instead of converting!)

    def should_place_maker_tp_for_slot(self, slot_id: str, cfg: SymbolStrategy) -> bool:
        """
        Determines whether OKX should place a resting Maker Post-Only TP limit order for `slot_id`:
        - Only oscillation scalp slots (C, S1..S5 / CS, L1..L5) with fixed grid TP use Maker Post-Only TP.
        - Base & Pyramid slots (Q, L2..L4 / QS, S2..S4) NEVER place fixed Maker TP (managed by trailing / L5).
        - For C (or CS) when Q (or QS) is NOT held, skip resting Maker TP so software can do zero-fee C->Q conversion!
        """
        if not cfg.use_post_only_tp:
            return False
        lv = self.levels.get(slot_id)
        if not lv or lv.get("role") != "OSC" or float(lv.get("tp_price") or 0.0) <= 0:
            return False
        if slot_id == "C":
            q_lv = self.levels.get("Q")
            return bool(q_lv and q_lv["status"] in ("OPEN", "ENTERING"))
        if slot_id == "CS":
            qs_lv = self.levels.get("QS")
            return bool(qs_lv and qs_lv["status"] in ("OPEN", "ENTERING"))
        return True

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
            self.center_price = entry_price
            self.locked_mode = side
            self.cycle_phase = "ACTIVE"
            self._build_levels(cfg)
        elif self.locked_mode == "NONE":
            self.locked_mode = side
            self.cycle_phase = "ACTIVE"
            self._build_levels(cfg)

        if self.locked_mode == side:
            base_sid = "Q" if side == "LONG" else "QS"
            pref = preferred_slot or base_sid
            target_slot = self._select_or_create_recovery_slot(
                side=side,
                entry_price=entry_price,
                filled_qty=filled_qty,
                ct_val=ct_val,
                cfg=cfg,
                preferred_slot=pref,
                mgn_mode=mgn_mode,
                grid_mode="REVERSE_OSCILLATING",
                has_fixed_tp=False,
            )
            lv = self.levels[target_slot]
            if not lv.get("role"):
                lv["role"] = "BASE" if target_slot in ("Q", "QS") else ("OSC" if target_slot in ("C", "CS") else "PYRAMID")
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
            lv["sl_price"] = self.global_sl_price or round(
                entry_price * (max(0.05, 1.0 - float(cfg.grid_stop_loss_pct)) if side == "LONG" else (1.0 + float(cfg.grid_stop_loss_pct))),
                self._calc_prec(cfg),
            )
            if lv.get("role") in ("BASE", "PYRAMID"):
                lv["tp_price"] = 0.0
            self._update_regime_reason()
            return target_slot

        # Legacy opposite-side exchange position during upgrade migration -> track as closing-only so never orphaned!
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
        lv["status"] = "OPEN"
        lv["armed"] = False
        lv["recovered"] = True
        lv["legacy_closing_only"] = True
        lv["role"] = "LEGACY"
        lv["entry_price"] = entry_price
        lv["filled_qty"] = filled_qty
        lv["contracts"] = contracts
        lv["ct_val"] = ct_val
        lv["mgn_mode"] = mgn_mode or "cross"
        lv["opened_mono_ms"] = mono_ms()
        if side == "LONG":
            lv["tp_price"] = round(entry_price * (1.0 + float(cfg.grid_take_profit_pct) + round_trip_fee), prec)
            lv["sl_price"] = round(entry_price * max(0.05, 1.0 - float(cfg.grid_stop_loss_pct)), prec)
        else:
            lv["tp_price"] = round(entry_price * max(0.05, 1.0 - float(cfg.grid_take_profit_pct) - round_trip_fee), prec)
            lv["sl_price"] = round(entry_price * (1.0 + float(cfg.grid_stop_loss_pct)), prec)
        return target_slot

    def clear_external_exit(self, slot_id: str):
        lv = self.levels.get(slot_id)
        if not lv:
            return
        if lv.get("ephemeral_recovered") or lv.get("legacy_closing_only"):
            self.levels.pop(slot_id, None)
            if self.locked_mode in ("LONG", "SHORT") and not self._has_active_slots():
                self._begin_post_cycle_restart(mono_ms(), is_global_sl=False)
            return
        now_m = mono_ms()
        lv["status"] = "IDLE"
        lv["armed"] = False
        lv["entry_price"] = 0.0
        lv["filled_qty"] = 0.0
        lv["contracts"] = 0.0
        lv["opened_mono_ms"] = 0.0
        lv["last_closed_mono"] = now_m
        lv["cooldown_until_mono"] = now_m + SLOT_CLOSE_COOLDOWN_MS
        lv["exit_retry_until_mono"] = 0.0
        lv["maker_tp_order_id"] = ""
        lv["maker_tp_live"] = False
        lv["maker_tp_touched_mono"] = 0.0
        lv["cond_sl_algo_id"] = ""
        lv["cond_sl_live"] = False
        lv["cond_sl_touched_mono"] = 0.0
        lv.pop("exiting_is_sl", None)
        lv.pop("recovered", None)
        if self.locked_mode in ("LONG", "SHORT") and not self._has_active_slots():
            self._begin_post_cycle_restart(now_m, is_global_sl=False)

    def mark_exited(self, slot_id: str, net_pnl: float, is_sl: bool = False):
        lv = self.levels.get(slot_id)
        now_m = mono_ms()
        was_global_sl = bool(is_sl)
        if lv:
            if lv.get("ephemeral_recovered") or lv.get("legacy_closing_only"):
                self.levels.pop(slot_id, None)
            else:
                lv["status"] = "IDLE"
                lv["armed"] = False
                lv["entry_price"] = 0.0
                lv["filled_qty"] = 0.0
                lv["contracts"] = 0.0
                lv["opened_mono_ms"] = 0.0
                lv["last_closed_mono"] = now_m
                lv["cooldown_until_mono"] = now_m + SLOT_CLOSE_COOLDOWN_MS
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
        if was_global_sl:
            self._last_cycle_ended_by_sl = True
        else:
            self.completed_cycles += 1
        self.total_net_pnl = round(self.total_net_pnl + net_pnl, 4)

        # If ALL slots in a locked cycle have finished exiting -> begin reconciliation + cooldown restart!
        if self.locked_mode in ("LONG", "SHORT") and not self._has_active_slots():
            self._begin_post_cycle_restart(now_m, is_global_sl=self._last_cycle_ended_by_sl)

    def export_state(self) -> dict:
        disarmed_slots = [sid for sid, lv in self.levels.items() if lv.get("armed") is False]
        serialized_slots = []
        for s_item in self._serialize_open_slots():
            lv_ref = self.levels.get(s_item["slot_id"], {})
            s_item["role"] = str(lv_ref.get("role") or "")
            s_item["legacy_closing_only"] = bool(lv_ref.get("legacy_closing_only", False))
            serialized_slots.append(s_item)
        now_m = mono_ms()
        cd_rem_ms = max(0.0, self.restart_cooldown_until_mono - now_m)
        return {
            "strategy_type": self.strategy_type,
            "center_price": float(self.center_price or 0.0),
            "active_grid_mode": "REVERSE_OSCILLATING",
            "locked_mode": self.locked_mode,
            "group_trail_active": bool(self.group_trail_active),
            "group_vwap": float(self.group_vwap or 0.0),
            "group_floor_price": float(self.group_floor_price or 0.0),
            "full_tp_price": float(self.full_tp_price or 0.0),
            "global_sl_price": float(self.global_sl_price or 0.0),
            "long_peak_price": float(self.long_peak_price or 0.0),
            "short_trough_price": float(self.short_trough_price or 0.0),
            "long_trail_stop_price": float(self.long_trail_stop_price or 0.0),
            "short_trail_stop_price": float(self.short_trail_stop_price or 0.0),
            "cycle_phase": self.cycle_phase,
            "needs_flat_reconcile": bool(self.needs_flat_reconcile),
            "reconcile_verified": bool(self.reconcile_verified),
            "restart_cooldown_rem_ms": round(cd_rem_ms, 1),
            "restart_reason": self.restart_reason,
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
        c_px = float(data.get("center_price") or 0.0)
        saved_mode = str(data.get("locked_mode") or "").upper()
        open_slots_raw = list(data.get("open_slots") or [])

        # Infer locked_mode cleanly when migrating legacy persisted state
        if saved_mode in ("NONE", "LONG", "SHORT"):
            self.locked_mode = saved_mode
        elif open_slots_raw:
            sides = [str(s.get("side") or "LONG").upper() for s in open_slots_raw]
            self.locked_mode = "LONG" if sides.count("LONG") >= sides.count("SHORT") else "SHORT"
        else:
            self.locked_mode = "NONE"

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
        self.group_trail_active = bool(data.get("group_trail_active", False))
        self.group_vwap = float(data.get("group_vwap") or 0.0)
        self.group_floor_price = float(data.get("group_floor_price") or 0.0)
        self.long_peak_price = float(data.get("long_peak_price") or 0.0)
        self.short_trough_price = float(data.get("short_trough_price") or 0.0)
        self.long_trail_stop_price = float(data.get("long_trail_stop_price") or 0.0)
        self.short_trail_stop_price = float(data.get("short_trail_stop_price") or 0.0)
        self.cycle_phase = str(data.get("cycle_phase") or "ACTIVE")
        self.needs_flat_reconcile = bool(data.get("needs_flat_reconcile", False))
        self.reconcile_verified = bool(data.get("reconcile_verified", True))
        self.restart_reason = str(data.get("restart_reason") or "")
        cd_rem = float(data.get("restart_cooldown_rem_ms") or 0.0)
        if cd_rem > 0:
            self.restart_cooldown_until_mono = mono_ms() + cd_rem

        for slot in open_slots_raw:
            sid = str(slot.get("slot_id") or "")
            if not sid:
                continue
            slot_side = str(slot.get("side") or "LONG").upper()
            is_legacy_opp = bool(slot.get("legacy_closing_only", False)) or (
                self.locked_mode in ("LONG", "SHORT") and slot_side != self.locked_mode
            )
            lv = self._restore_common_slot_fields(slot, cfg, grid_mode="REVERSE_OSCILLATING")
            lv["armed"] = False
            if is_legacy_opp:
                lv["legacy_closing_only"] = True
                lv["role"] = "LEGACY"
            elif not lv.get("role"):
                lv["role"] = str(slot.get("role") or ("BASE" if sid in ("Q", "QS") else ("OSC" if sid in ("C", "CS") else "PYRAMID")))
        self._update_regime_reason()

    def get_snapshot(self) -> dict:
        open_long = sum(1 for lv in self.levels.values() if lv["side"] == "LONG" and lv["status"] in ("OPEN", "EXITING"))
        open_short = sum(1 for lv in self.levels.values() if lv["side"] == "SHORT" and lv["status"] in ("OPEN", "EXITING"))
        ordered = sorted(
            self.levels.values(),
            key=lambda x: -float(x.get("sort_price") or x.get("trigger_price") or 0.0),
        )
        now_m = mono_ms()
        cd_rem_sec = round(max(0.0, self.restart_cooldown_until_mono - now_m) / 1000.0, 1)
        return {
            "center_price": self.center_price,
            "active_grid_mode": "REVERSE_OSCILLATING",
            "locked_mode": self.locked_mode,
            "group_trail_active": bool(self.group_trail_active),
            "group_vwap": float(self.group_vwap or 0.0),
            "group_floor_price": float(self.group_floor_price or 0.0),
            "full_tp_price": float(self.full_tp_price or 0.0),
            "global_sl_price": float(self.global_sl_price or 0.0),
            "cycle_phase": self.cycle_phase,
            "restart_cooldown_rem_sec": cd_rem_sec,
            "restart_reason": self.restart_reason,
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
                    "role": lv.get("role", ""),
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
