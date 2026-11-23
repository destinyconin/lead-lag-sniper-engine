"""
Shared base infrastructure for isolated grid strategy implementations.

This module MUST contain ONLY generic slot/precision/risk/serialization utilities and the
strategy interface contract — NEVER strategy-specific entry, exit, or recenter rules.
Each concrete strategy (`LONG_SHORT_GRID`, `REVERSE_GRID`, `TREND_GRID`, `ADAPTIVE_GRID`)
implements its own complete trading logic in its dedicated sibling module so changes to
one strategy cannot affect another.
"""
import math
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


class BaseGridStrategy:
    """Base contract & pure slot utilities shared across isolated grid strategies."""

    strategy_type: str = "LONG_SHORT_GRID"
    default_grid_mode: str = "MEAN_REVERSION"
    default_regime_reason: str = "震荡均值回归（默认）"

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.center_price: float = 0.0
        self.levels: Dict[str, dict] = {}
        self.completed_cycles: int = 0
        self.total_net_pnl: float = 0.0
        self._last_sig_params: Optional[tuple] = None
        self._last_user_sig_params: Optional[tuple] = None

        self.active_grid_mode: str = self.default_grid_mode
        self.long_peak_price: float = 0.0
        self.short_trough_price: float = 0.0
        self.long_trail_stop_price: float = 0.0
        self.short_trail_stop_price: float = 0.0
        self.last_bbw_info: Optional[dict] = None
        self.last_regime_reason: str = self.default_regime_reason
        self._pending_regime_log: Optional[str] = None
        self._needs_post_trend_recenter: bool = False
        self._post_sl_cooldown_until_mono: float = 0.0
        self._effective_trend_bias: str = "RANGE"

    # ---------------- Strategy capability & formatting hooks ----------------

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return bool(cfg.use_post_only_tp) and self.active_grid_mode != "TREND_BREAKOUT"

    def uses_livermore_sl_amend(self) -> bool:
        return self.active_grid_mode == "TREND_BREAKOUT"

    def get_rt_fee(self, cfg: SymbolStrategy, taker_fee: float, maker_fee: float) -> float:
        if self.supports_maker_tp(cfg):
            return taker_fee + maker_fee
        return 2.0 * taker_fee

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        return "GRID_SL"

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        return ("多空网格开仓", "做多网格" if side == "LONG" else "做空网格")

    def get_exit_log_tag(self) -> str:
        return "多空网格平仓"

    def get_mode_display_name(self) -> str:
        return "传统震荡"

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
            f"[多空网格成交] {self.symbol} [{slot_id}] {side} 均价: {avg_price:.{cfg.price_precision}f} | "
            f"数量: {filled_qty} | 止盈目标: {lv.get('tp_price', 0):.{cfg.price_precision}f}{prot_suffix}"
        )

    # ---------------- Generic pure helpers ----------------

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

    @staticmethod
    def _slot_cooldown_until(lv: dict) -> float:
        last_c = float(lv.get("last_closed_mono") or 0.0)
        return max(
            float(lv.get("cooldown_until_mono") or 0.0),
            (last_c + 1200.0) if last_c > 0 else 0.0,
        )

    @staticmethod
    def _should_fire_software_sl(
        lv_ref: dict, px_now: float, sl_px_val: float, is_long_side: bool, now_m: float
    ) -> bool:
        if not bool(lv_ref.get("cond_sl_live", False)):
            return True
        # Grant OKX native Conditional SL 1.5s grace (or immediate if breached >0.25% past SL) to prevent double-close!
        touched = float(lv_ref.get("cond_sl_touched_mono") or 0.0)
        if touched <= 0.0:
            lv_ref["cond_sl_touched_mono"] = now_m
            touched = now_m
        deep_breach = (px_now <= sl_px_val * 0.9975) if is_long_side else (px_now >= sl_px_val * 1.0025)
        return (now_m - touched >= 1500.0) or deep_breach

    @staticmethod
    def _real_slot_notional(lv_ref: dict, cfg: SymbolStrategy) -> float:
        e_px_v = float(lv_ref.get("entry_price") or 0.0)
        q_v = float(lv_ref.get("filled_qty") or 0.0)
        if e_px_v > 0 and q_v > 0:
            return e_px_v * q_v
        return max(1.0, float(lv_ref.get("order_usdt") or cfg.order_usdt))

    def _evaluate_kill_switch_exits(self, bid: float, ask: float, now_m: float) -> List[dict]:
        exits: List[dict] = []
        for lv in self.levels.values():
            if lv["status"] != "OPEN":
                continue
            if now_m < float(lv.get("exit_retry_until_mono") or 0.0):
                continue
            lv["status"] = "EXITING"
            exits.append(
                {**lv, "exit_reason": "KILL_SWITCH", "target_price": bid if lv["side"] == "LONG" else ask}
            )
        return exits

    def clear_cooldowns(self):
        self._post_sl_cooldown_until_mono = 0.0
        for lv in self.levels.values():
            lv["cooldown_until_mono"] = 0.0
            lv["last_closed_mono"] = 0.0

    def mark_entry_missed(self, slot_id: str, backoff_ms: float = 1200.0):
        lv = self.levels.get(slot_id)
        if lv and lv["status"] == "ENTERING":
            now_m = mono_ms()
            lv["status"] = "IDLE"
            lv["last_closed_mono"] = now_m
            lv["cooldown_until_mono"] = now_m + max(1200.0, float(backoff_ms))

    def revert_exit_failed(self, slot_id: str, backoff_ms: float = 5000.0):
        lv = self.levels.get(slot_id)
        if lv and lv["status"] == "EXITING":
            lv["status"] = "OPEN"
            lv.pop("exiting_is_sl", None)
            lv["exit_retry_until_mono"] = mono_ms() + max(1000.0, float(backoff_ms))

    def reset_runtime_state(self):
        """Cleanly wipes in-memory slot/center/signature state (e.g. SHADOW <-> LIVE mode switch)."""
        for sid in list(self.levels.keys()):
            self.clear_external_exit(sid)
        self.levels.clear()
        self.center_price = 0.0
        self.long_peak_price = 0.0
        self.short_trough_price = 0.0
        self.long_trail_stop_price = 0.0
        self.short_trail_stop_price = 0.0
        self._needs_post_trend_recenter = False
        self._post_sl_cooldown_until_mono = 0.0
        self._last_sig_params = None
        self._last_user_sig_params = None

    def _serialize_open_slots(self) -> List[dict]:
        return [
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
                "status": "OPEN",
            }
            for lv in self.levels.values()
            if lv.get("status") in ("OPEN", "EXITING") and float(lv.get("entry_price") or 0.0) > 0
        ]

    def _select_or_create_recovery_slot(
        self,
        side: str,
        entry_price: float,
        filled_qty: float,
        ct_val: float,
        cfg: SymbolStrategy,
        preferred_slot: Optional[str],
        mgn_mode: str,
        grid_mode: str,
        has_fixed_tp: bool,
    ) -> str:
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
                "grid_mode": grid_mode,
                "trigger_price": round(entry_price, prec),
                "tp_price": round(entry_price, prec) if has_fixed_tp else 0.0,
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
        return target_slot

    def _restore_common_slot_fields(self, slot: dict, cfg: SymbolStrategy, grid_mode: str) -> dict:
        sid = str(slot.get("slot_id") or "")
        if sid not in self.levels:
            self.levels[sid] = {
                "slot_id": sid,
                "side": str(slot.get("side") or "LONG"),
                "level_idx": int(sid[1:]) if len(sid) > 1 and sid[1:].isdigit() else 1,
                "grid_mode": grid_mode,
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
        restored_entry = float(slot.get("entry_price") or 0.0)
        restored_contracts = float(slot.get("contracts") or 0.0)
        lv["entry_price"] = restored_entry
        lv["filled_qty"] = float(slot.get("filled_qty") or 0.0)
        lv["contracts"] = restored_contracts
        lv["recovered"] = True
        # Mark exact persisted slots so periodic OKX sync preserves per-slot entry_price/contracts
        # instead of overwriting them with OKX's blended position avgPx!
        lv["restored_from_state"] = bool(restored_entry > 0.0 and restored_contracts > 0.0)
        lv["ct_val"] = float(slot.get("ct_val") or 1.0)
        lv["mgn_mode"] = str(slot.get("mgn_mode") or "cross")
        lv["tp_price"] = float(slot.get("tp_price") or 0.0)
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
        return lv

    # ---------------- Strategy lifecycle contract (implemented independently per strategy) ----------------

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        raise NotImplementedError

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

    def clear_external_exit(self, slot_id: str):
        raise NotImplementedError

    def mark_exited(self, slot_id: str, net_pnl: float, is_sl: bool = False):
        raise NotImplementedError

    def export_state(self) -> dict:
        raise NotImplementedError

    def restore_state(self, data: dict, cfg: SymbolStrategy):
        raise NotImplementedError

    def get_snapshot(self) -> dict:
        raise NotImplementedError
