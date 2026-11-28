"""
Bidirectional Grid Strategy Router (`LongShortGridTracker`).

Delegates all strategy-specific trading logic to isolated, standalone strategy modules under
`backend/strategies/`:
- `LONG_SHORT_GRID` -> `MeanReversionGridStrategy` (`backend/strategies/mean_reversion_grid.py`)
- `REVERSE_GRID`    -> `ReverseOscillatingGridStrategy` (`backend/strategies/reverse_oscillating_grid.py`)
- `TREND_GRID`      -> `TrendBreakoutGridStrategy` (`backend/strategies/trend_breakout_grid.py`)
- `ADAPTIVE_GRID`   -> `AdaptiveVcpGridStrategy` (`backend/strategies/adaptive_vcp_grid.py`)

This router preserves full backward compatibility for engine callers and tests while guaranteeing
zero cross-strategy code or state interference.
"""
from typing import Dict, List, Optional, Tuple

from backend.models import SymbolStrategy
from backend.strategies import (
    GRID_STRATEGY_REGISTRY,
    AdaptiveVcpGridStrategy,
    BaseGridStrategy,
    MeanReversionGridStrategy,
    ReverseOscillatingGridStrategy,
    TrendBreakoutGridStrategy,
    calc_pyramid_weights,
    create_grid_strategy,
)


class LongShortGridTracker:
    """
    Lightweight per-symbol router wrapping an isolated `BaseGridStrategy` instance.
    Automatically binds (and swaps when flat) to the exact strategy class matching `cfg.strategy_type`.
    """

    def __init__(self, symbol: str, strategy_type: Optional[str] = None):
        self.symbol = symbol
        self._bound_strategy_type: Optional[str] = str(strategy_type).upper() if strategy_type else None
        self._strategy: BaseGridStrategy = create_grid_strategy(
            self._bound_strategy_type or "LONG_SHORT_GRID", symbol
        )

    @property
    def strategy(self) -> BaseGridStrategy:
        return self._strategy

    def _ensure_strategy(self, cfg: SymbolStrategy, current_mid: float = 0.0, force: bool = False):
        target_type = str(cfg.strategy_type or "LONG_SHORT_GRID").upper()
        if target_type not in GRID_STRATEGY_REGISTRY:
            return
        if self._bound_strategy_type == target_type:
            return
        if self._bound_strategy_type is not None and not force and self._strategy._has_active_slots():
            # Never swap strategy implementation mid-position; wait until all slots are flat.
            return
        prev = self._strategy
        new_strat = create_grid_strategy(target_type, self.symbol)
        new_strat.completed_cycles = prev.completed_cycles
        new_strat.total_net_pnl = prev.total_net_pnl
        if not force and current_mid <= 0 and prev.center_price > 0 and self._bound_strategy_type is not None:
            new_strat.center_price = prev.center_price
        self._strategy = new_strat
        self._bound_strategy_type = target_type

    # ---------------- Delegated state properties (100% backward compatible) ----------------

    @property
    def center_price(self) -> float:
        return self._strategy.center_price

    @center_price.setter
    def center_price(self, value: float):
        self._strategy.center_price = value

    @property
    def levels(self) -> Dict[str, dict]:
        return self._strategy.levels

    @levels.setter
    def levels(self, value: Dict[str, dict]):
        self._strategy.levels = value

    @property
    def active_grid_mode(self) -> str:
        return self._strategy.active_grid_mode

    @active_grid_mode.setter
    def active_grid_mode(self, value: str):
        self._strategy.active_grid_mode = value

    @property
    def locked_mode(self) -> str:
        return str(getattr(self._strategy, "locked_mode", "NONE") or "NONE")

    @property
    def deep_dip_breakeven_active(self) -> bool:
        return bool(getattr(self._strategy, "deep_dip_breakeven_active", False))

    @property
    def completed_cycles(self) -> int:
        return self._strategy.completed_cycles

    @completed_cycles.setter
    def completed_cycles(self, value: int):
        self._strategy.completed_cycles = value

    @property
    def total_net_pnl(self) -> float:
        return self._strategy.total_net_pnl

    @total_net_pnl.setter
    def total_net_pnl(self, value: float):
        self._strategy.total_net_pnl = value

    @property
    def long_peak_price(self) -> float:
        return self._strategy.long_peak_price

    @long_peak_price.setter
    def long_peak_price(self, value: float):
        self._strategy.long_peak_price = value

    @property
    def short_trough_price(self) -> float:
        return self._strategy.short_trough_price

    @short_trough_price.setter
    def short_trough_price(self, value: float):
        self._strategy.short_trough_price = value

    @property
    def long_trail_stop_price(self) -> float:
        return self._strategy.long_trail_stop_price

    @long_trail_stop_price.setter
    def long_trail_stop_price(self, value: float):
        self._strategy.long_trail_stop_price = value

    @property
    def short_trail_stop_price(self) -> float:
        return self._strategy.short_trail_stop_price

    @short_trail_stop_price.setter
    def short_trail_stop_price(self, value: float):
        self._strategy.short_trail_stop_price = value

    @property
    def last_bbw_info(self) -> Optional[dict]:
        return self._strategy.last_bbw_info

    @last_bbw_info.setter
    def last_bbw_info(self, value: Optional[dict]):
        self._strategy.last_bbw_info = value

    @property
    def last_regime_reason(self) -> str:
        return self._strategy.last_regime_reason

    @last_regime_reason.setter
    def last_regime_reason(self, value: str):
        self._strategy.last_regime_reason = value

    @property
    def _pending_regime_log(self) -> Optional[str]:
        return self._strategy._pending_regime_log

    @_pending_regime_log.setter
    def _pending_regime_log(self, value: Optional[str]):
        self._strategy._pending_regime_log = value

    @property
    def _needs_post_trend_recenter(self) -> bool:
        return self._strategy._needs_post_trend_recenter

    @_needs_post_trend_recenter.setter
    def _needs_post_trend_recenter(self, value: bool):
        self._strategy._needs_post_trend_recenter = value

    @property
    def _post_sl_cooldown_until_mono(self) -> float:
        return self._strategy._post_sl_cooldown_until_mono

    @_post_sl_cooldown_until_mono.setter
    def _post_sl_cooldown_until_mono(self, value: float):
        self._strategy._post_sl_cooldown_until_mono = value

    @property
    def _effective_trend_bias(self) -> str:
        return self._strategy._effective_trend_bias

    @_effective_trend_bias.setter
    def _effective_trend_bias(self, value: str):
        self._strategy._effective_trend_bias = value

    @property
    def _last_sig_params(self) -> Optional[tuple]:
        return self._strategy._last_sig_params

    @_last_sig_params.setter
    def _last_sig_params(self, value: Optional[tuple]):
        self._strategy._last_sig_params = value

    @property
    def _last_user_sig_params(self) -> Optional[tuple]:
        return self._strategy._last_user_sig_params

    @_last_user_sig_params.setter
    def _last_user_sig_params(self, value: Optional[tuple]):
        self._strategy._last_user_sig_params = value

    # ---------------- Strategy capability & formatting hooks ----------------

    def supports_maker_tp(self, cfg: SymbolStrategy) -> bool:
        return self._strategy.supports_maker_tp(cfg)

    def should_place_maker_tp_for_slot(self, slot_id: str, cfg: SymbolStrategy) -> bool:
        return self._strategy.should_place_maker_tp_for_slot(slot_id, cfg)

    def uses_livermore_sl_amend(self) -> bool:
        return self._strategy.uses_livermore_sl_amend()

    def consume_slot_conversions(self) -> List[dict]:
        return self._strategy.consume_slot_conversions()

    def needs_post_cycle_flat_reconcile(self) -> bool:
        return self._strategy.needs_post_cycle_flat_reconcile()

    def mark_reconcile_verified_clean(self):
        self._strategy.mark_reconcile_verified_clean()

    def get_rt_fee(self, cfg: SymbolStrategy, taker_fee: float, maker_fee: float) -> float:
        return self._strategy.get_rt_fee(cfg, taker_fee, maker_fee)

    def get_cond_sl_exit_reason(self, lv: dict) -> str:
        return self._strategy.get_cond_sl_exit_reason(lv)

    def get_entry_log_tags(self, side: str) -> Tuple[str, str]:
        return self._strategy.get_entry_log_tags(side)

    def get_exit_log_tag(self) -> str:
        return self._strategy.get_exit_log_tag()

    def get_mode_display_name(self) -> str:
        return self._strategy.get_mode_display_name()

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
        return self._strategy.format_fill_log(cfg, slot_id, side, avg_price, filled_qty, lv, prot_suffix)

    # ---------------- Delegated lifecycle methods ----------------

    def consume_regime_log(self) -> Optional[str]:
        return self._strategy.consume_regime_log()

    def _has_active_slots(self, side_filter: Optional[str] = None) -> bool:
        return self._strategy._has_active_slots(side_filter=side_filter)

    def clear_cooldowns(self):
        self._strategy.clear_cooldowns()

    def reset_runtime_state(self):
        self._strategy.reset_runtime_state()

    def sync_config(self, cfg: SymbolStrategy, current_mid: float = 0.0, bbw_info: Optional[dict] = None):
        self._ensure_strategy(cfg, current_mid=current_mid)
        self._strategy.sync_config(cfg, current_mid=current_mid, bbw_info=bbw_info)

    def recenter(self, new_center: float, cfg: SymbolStrategy, bbw_info: Optional[dict] = None) -> bool:
        self._ensure_strategy(cfg, current_mid=new_center)
        return self._strategy.recenter(new_center, cfg, bbw_info=bbw_info)

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
        self._ensure_strategy(cfg, current_mid=mid)
        return self._strategy.evaluate_tick(
            bid=bid,
            ask=ask,
            mid=mid,
            cfg=cfg,
            is_net_mode=is_net_mode,
            allow_entries=allow_entries,
            round_trip_fee=round_trip_fee,
            bbw_info=bbw_info,
            force_exit_all=force_exit_all,
            allow_recenter=allow_recenter,
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
        self._strategy.mark_entry_filled(
            slot_id=slot_id,
            entry_price=entry_price,
            filled_qty=filled_qty,
            contracts=contracts,
            ct_val=ct_val,
            cfg=cfg,
            round_trip_fee=round_trip_fee,
        )

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
        self._ensure_strategy(cfg, current_mid=entry_price)
        return self._strategy.recover_open_position(
            side=side,
            entry_price=entry_price,
            filled_qty=filled_qty,
            contracts=contracts,
            ct_val=ct_val,
            cfg=cfg,
            round_trip_fee=round_trip_fee,
            preferred_slot=preferred_slot,
            mgn_mode=mgn_mode,
        )

    def mark_entry_missed(self, slot_id: str, backoff_ms: float = 1200.0):
        self._strategy.mark_entry_missed(slot_id, backoff_ms=backoff_ms)

    def clear_external_exit(self, slot_id: str):
        self._strategy.clear_external_exit(slot_id)

    def mark_exited(self, slot_id: str, net_pnl: float, is_sl: bool = False):
        self._strategy.mark_exited(slot_id, net_pnl, is_sl=is_sl)

    def revert_exit_failed(self, slot_id: str, backoff_ms: float = 5000.0):
        self._strategy.revert_exit_failed(slot_id, backoff_ms=backoff_ms)

    def export_state(self) -> dict:
        return self._strategy.export_state()

    def restore_state(self, data: dict, cfg: SymbolStrategy):
        self._ensure_strategy(cfg, force=True)
        self._strategy.restore_state(data, cfg)

    def get_snapshot(self) -> dict:
        return self._strategy.get_snapshot()


__all__ = [
    "LongShortGridTracker",
    "BaseGridStrategy",
    "MeanReversionGridStrategy",
    "ReverseOscillatingGridStrategy",
    "TrendBreakoutGridStrategy",
    "AdaptiveVcpGridStrategy",
    "GRID_STRATEGY_REGISTRY",
    "calc_pyramid_weights",
    "create_grid_strategy",
]
