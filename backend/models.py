"""
Unified Data Models for the Lead-Lag Bidirectional Sniper Engine.
Resolves all Section 1.3 naming and default parameter inconsistencies:
- Unified `take_profit_pct` = 0.0025 (0.25%)
- Unified `slippage_pct` = 0.0005 (0.05%)
- Explicit fields for Follower lag confirmation, Leader reversal stop-loss,
  Trailing stop-loss, Beta convergence factor, and Signal/Book staleness thresholds.
"""
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field


class SniperState(str, Enum):
    IDLE = "IDLE"
    SIGNALED = "SIGNALED"       # Synchronous lock acquired before task spawn
    SUBMITTED = "SUBMITTED"     # IOC order sent, waiting for fill confirmation
    RECONCILE = "RECONCILE"     # Uncertain fill status, reconciling with exchange
    HOLDING = "HOLDING"         # Position open, Post-Only TP & Hard SL active
    EXITING = "EXITING"         # Canceling TP / executing market or IOC exit with retry
    CLOSED = "CLOSED"           # Accounting & async persistence
    COOLDOWN = "COOLDOWN"       # Post-trade debounce


class ExchangeConfigModel(BaseModel):
    id: Optional[int] = None
    exchange_id: Literal["binanceusdm", "okx", "krakenfutures", "bybit", "gate", "bitget"] = "binanceusdm"
    display_name: str = "Binance USDM Futures"
    role: Literal["leader", "follower"] = "leader"
    api_key: str = ""
    api_secret: str = ""        # Plaintext only on input; encrypted in DB, masked on GET
    passphrase: str = ""        # Plaintext only on input; encrypted in DB, masked on GET
    vip_level: str = "VIP 0"
    taker_fee: float = Field(default=0.0005, description="Taker fee rate (e.g., 0.0005 = 0.05%)")
    maker_fee: float = Field(default=0.0002, description="Maker fee rate (e.g., 0.0002 = 0.02%)")
    is_active: bool = True


class SymbolStrategy(BaseModel):
    symbol: str = Field(default="XRP/USDT:USDT", description="Unified perpetual swap symbol")
    direction: Literal["BOTH", "LONG_ONLY", "SHORT_ONLY"] = "BOTH"
    order_usdt: float = Field(default=300.0, gt=0, description="Notional order size in USDT")
    leverage: int = Field(default=5, ge=1, le=50, description="Leverage multiplier")
    window_ms: int = Field(default=150, ge=20, le=5000, description="Leader pulse detection window (ms)")
    min_jump_pct: float = Field(default=0.0060, gt=0, description="Minimum Leader jump ratio (0.0060 = 0.60%)")
    adaptive_z_score: float = Field(default=3.0, ge=0.0, description="Minimum realized volatility Z-score")
    lag_confirm_max_pct: float = Field(default=0.0010, ge=0, description="Max Follower move in window to confirm lag (0.10%)")
    take_profit_pct: float = Field(default=0.0025, gt=0, description="Target net profit ratio after fees (0.25%)")
    stop_loss_pct: float = Field(default=0.0035, gt=0, description="Hard stop-loss ratio (0.35%)")
    reversal_stop_ratio: float = Field(default=0.50, gt=0, le=1.0, description="Leader retracement ratio to trigger reversal stop (50%)")
    trailing_stop_enabled: bool = Field(default=True, description="Enable trailing profit lock once TP threshold is reached")
    trailing_retracement_pct: float = Field(default=0.0008, gt=0, description="Retracement from peak net profit to trigger trailing exit (0.08%)")
    max_hold_ms: int = Field(default=1500, ge=100, le=30000, description="Maximum holding duration before forced timeout exit (ms)")
    slippage_pct: float = Field(default=0.0005, ge=0, description="IOC max tolerated slippage cap (0.05%)")
    beta_coeff: float = Field(default=0.88, gt=0.1, le=1.5, description="Expected Follower-to-Leader convergence coefficient beta")
    auto_beta_enabled: bool = Field(default=True, description="Dynamically update beta from rolling returns regression")
    require_volume_confirm: bool = Field(default=True, description="Require microprice & active trade flow confirmation")
    use_post_only_tp: bool = Field(default=True, description="Place Maker Post-Only limit order for Take Profit to save fees")
    max_signal_age_ms: int = Field(default=80, ge=5, le=1000, description="Maximum allowed signal age before aborting order")
    max_book_staleness_ms: int = Field(default=600, ge=50, le=5000, description="Maximum Follower book age to prevent stale book false triggers")
    contract_size: float = Field(default=1.0, gt=0, description="Contract multiplier / lot value")
    min_qty: float = Field(default=0.1, gt=0, description="Minimum order quantity")
    qty_precision: int = Field(default=2, ge=0, le=8, description="Quantity decimal places")
    price_precision: int = Field(default=4, ge=0, le=8, description="Price decimal places")
    is_running: bool = True
    auto_disabled_reason: Optional[str] = None


class GlobalRiskConfig(BaseModel):
    kill_switch_active: bool = False
    trading_mode: Literal["SHADOW", "LIVE"] = "SHADOW"
    feed_source: Literal["AUTO", "REAL_WS", "SIMULATOR"] = "AUTO"
    dynamic_leader_enabled: bool = False
    max_daily_loss_usdt: float = 150.0
    max_consecutive_losses: int = 5
    max_concurrent_positions: int = 2
    max_exposure_usdt: float = 1500.0
    latency_circuit_breaker_ms: float = 250.0
    auto_disable_rolling_trades: int = 8
    cooldown_ms: int = 500
    shadow_base_latency_ms: float = 26.0
    shadow_latency_jitter_ms: float = 12.0
    shadow_adverse_selection_prob: float = 0.25


class PulseInjectionRequest(BaseModel):
    symbol: str = "XRP/USDT:USDT"
    side: Literal["LONG", "SHORT"] = "LONG"
    jump_pct: float = Field(default=0.0085, description="Simulated Leader jump magnitude, e.g., 0.0085 = +0.85%")
    follower_scenario: Literal["CONVERGE_TP", "REVERSAL_SL", "HARD_SL", "TIMEOUT", "MM_WITHDRAW_MISS"] = "CONVERGE_TP"

