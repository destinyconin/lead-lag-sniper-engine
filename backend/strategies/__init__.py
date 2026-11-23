"""
Isolated Grid Strategy Package.

Each trading strategy is implemented in its own standalone module with zero cross-strategy
trading-rule coupling:
- `LONG_SHORT_GRID` -> `MeanReversionGridStrategy` (`mean_reversion_grid.py`)
- `REVERSE_GRID`    -> `ReverseOscillatingGridStrategy` (`reverse_oscillating_grid.py`)
- `TREND_GRID`      -> `TrendBreakoutGridStrategy` (`trend_breakout_grid.py`)
- `ADAPTIVE_GRID`   -> `AdaptiveVcpGridStrategy` (`adaptive_vcp_grid.py`)
"""
from typing import Dict, Type

from backend.strategies.adaptive_vcp_grid import AdaptiveVcpGridStrategy
from backend.strategies.base import BaseGridStrategy, calc_pyramid_weights
from backend.strategies.mean_reversion_grid import MeanReversionGridStrategy
from backend.strategies.reverse_oscillating_grid import ReverseOscillatingGridStrategy
from backend.strategies.trend_breakout_grid import TrendBreakoutGridStrategy

GRID_STRATEGY_REGISTRY: Dict[str, Type[BaseGridStrategy]] = {
    "LONG_SHORT_GRID": MeanReversionGridStrategy,
    "REVERSE_GRID": ReverseOscillatingGridStrategy,
    "TREND_GRID": TrendBreakoutGridStrategy,
    "ADAPTIVE_GRID": AdaptiveVcpGridStrategy,
}


def create_grid_strategy(strategy_type: str, symbol: str) -> BaseGridStrategy:
    """Factory returning a fresh, completely isolated strategy instance for `strategy_type`."""
    cls = GRID_STRATEGY_REGISTRY.get(str(strategy_type or "").upper(), MeanReversionGridStrategy)
    return cls(symbol)


__all__ = [
    "BaseGridStrategy",
    "MeanReversionGridStrategy",
    "ReverseOscillatingGridStrategy",
    "TrendBreakoutGridStrategy",
    "AdaptiveVcpGridStrategy",
    "GRID_STRATEGY_REGISTRY",
    "calc_pyramid_weights",
    "create_grid_strategy",
]
