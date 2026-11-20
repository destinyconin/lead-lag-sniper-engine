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
    EXIT_UNCERTAIN = "EXIT_UNCERTAIN"  # Exit failed/uncertain; locked until exchange reconciliation
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
    taker_fee: float = Field(default=0.0005, ge=0.0, le=0.05, allow_inf_nan=False, description="Taker fee rate (e.g., 0.0005 = 0.05%)")
    maker_fee: float = Field(default=0.0002, ge=-0.01, le=0.05, allow_inf_nan=False, description="Maker fee rate (e.g., 0.0002 = 0.02%)")
    is_active: bool = True


GRID_STRATEGY_TYPES = ("LONG_SHORT_GRID", "TREND_GRID", "ADAPTIVE_GRID")


def is_grid_strategy(strategy_type: Optional[str]) -> bool:
    return str(strategy_type or "") in GRID_STRATEGY_TYPES


class SymbolStrategy(BaseModel):
    symbol: str = Field(default="XRP/USDT:USDT", description="Unified perpetual swap symbol")
    strategy_type: Literal["LEAD_LAG", "LONG_SHORT_GRID", "TREND_GRID", "ADAPTIVE_GRID"] = Field(
        default="LEAD_LAG",
        description="Per-symbol running strategy: LEAD_LAG, LONG_SHORT_GRID, TREND_GRID, or ADAPTIVE_GRID",
    )
    direction: Literal["BOTH", "LONG_ONLY", "SHORT_ONLY"] = "BOTH"
    order_usdt: float = Field(default=300.0, gt=0, le=1_000_000.0, allow_inf_nan=False, description="Notional order size in USDT")
    leverage: int = Field(default=5, ge=1, le=50, description="Leverage multiplier")
    window_ms: int = Field(default=150, ge=20, le=5000, description="Leader pulse detection window (ms)")
    min_jump_pct: float = Field(default=0.0060, gt=0, le=0.50, allow_inf_nan=False, description="Minimum Leader jump ratio (0.0060 = 0.60%)")
    adaptive_z_score: float = Field(default=3.0, ge=0.0, le=50.0, allow_inf_nan=False, description="Minimum realized volatility Z-score")
    lag_confirm_max_pct: float = Field(default=0.0010, ge=0, le=0.50, allow_inf_nan=False, description="Max Follower move in window to confirm lag (0.10%)")
    take_profit_pct: float = Field(default=0.0025, gt=0, le=0.50, allow_inf_nan=False, description="Target net profit ratio after fees (0.25%)")
    stop_loss_pct: float = Field(default=0.0035, gt=0, le=0.90, allow_inf_nan=False, description="Hard stop-loss ratio (0.35%)")
    reversal_stop_ratio: float = Field(default=0.50, gt=0, le=1.0, allow_inf_nan=False, description="Leader retracement ratio to trigger reversal stop (50%)")
    trailing_stop_enabled: bool = Field(default=True, description="Enable trailing profit lock once TP threshold is reached")
    trailing_retracement_pct: float = Field(default=0.0008, gt=0, le=0.50, allow_inf_nan=False, description="Retracement from peak net profit to trigger trailing exit (0.08%)")
    max_hold_ms: int = Field(default=1500, ge=100, le=30000, description="Maximum holding duration before forced timeout exit (ms)")
    slippage_pct: float = Field(default=0.0005, ge=0, le=0.10, allow_inf_nan=False, description="IOC max tolerated slippage cap (0.05%)")
    beta_coeff: float = Field(default=0.88, gt=0.1, le=1.5, allow_inf_nan=False, description="Expected Follower-to-Leader convergence coefficient beta")
    auto_beta_enabled: bool = Field(default=True, description="Dynamically update beta from rolling returns regression")
    require_volume_confirm: bool = Field(default=True, description="Require microprice & active trade flow confirmation")
    use_post_only_tp: bool = Field(default=True, description="Place Maker Post-Only limit order for Take Profit to save fees")
    sl_trigger_px_type: Literal["mark", "last"] = Field(
        default="mark", description="OKX conditional SL trigger price type: mark (anti-wick, default) or last"
    )
    max_signal_age_ms: int = Field(default=80, ge=5, le=1000, description="Maximum allowed signal age before aborting order")
    max_book_staleness_ms: int = Field(default=600, ge=50, le=5000, description="Maximum Follower book age to prevent stale book false triggers")
    contract_size: float = Field(default=1.0, gt=0, le=1e12, allow_inf_nan=False, description="Contract multiplier / lot value")
    min_qty: float = Field(default=0.1, gt=0, le=1e12, allow_inf_nan=False, description="Minimum order quantity")
    qty_precision: int = Field(default=2, ge=0, le=12, description="Quantity decimal places")
    price_precision: int = Field(default=4, ge=0, le=12, description="Price decimal places")
    # Long-Short Grid strategy parameters
    grid_side_mode: Literal["LONG_SHORT", "LONG_ONLY", "SHORT_ONLY"] = Field(
        default="LONG_SHORT", description="Grid directional mode: symmetrical LONG_SHORT, LONG_ONLY, or SHORT_ONLY"
    )
    grid_levels: int = Field(default=5, ge=1, le=30, description="Number of grid levels per active side")
    grid_step_pct: float = Field(default=0.0060, ge=0.0001, le=0.50, allow_inf_nan=False, description="Price step between adjacent grid levels (0.006 = 0.60%)")
    grid_take_profit_pct: float = Field(default=0.0065, ge=0.0001, le=0.50, allow_inf_nan=False, description="Per-grid take-profit target ratio")
    grid_range_pct: float = Field(default=0.05, ge=0.001, le=0.80, allow_inf_nan=False, description="Outer boundary +/- ratio around center price")
    grid_stop_loss_pct: float = Field(default=0.08, ge=0.002, le=0.90, allow_inf_nan=False, description="Breakout hard stop-loss ratio from grid center")
    grid_recenter_enabled: bool = Field(default=True, description="Auto-recenter grid when flat and price leaves range")
    # Reverse / Trend Breakout Grid & Adaptive VCP/BBW Dual-Mode parameters
    trend_dead_zone_mult: float = Field(
        default=2.0, ge=0.5, le=10.0, allow_inf_nan=False, description="First-level breakout dead-zone multiplier of grid_step_pct"
    )
    trend_trailing_mult: float = Field(
        default=1.5, ge=0.3, le=10.0, allow_inf_nan=False, description="Trailing-stop pullback multiplier of grid_step_pct from peak/trough"
    )
    trend_pyramid_decay: bool = Field(
        default=True, description="Use front-loaded decaying pyramid sizing (e.g. 50%/30%/20%) in trend grid mode"
    )
    trend_use_bb_breakout: bool = Field(
        default=True, description="Align first breakout level outside live 15m/1H Bollinger upper/lower band when available"
    )
    bbw_timeframe: Literal["15m", "1H"] = Field(
        default="15m", description="Candle timeframe for VCP / Bollinger Band Width squeeze detection"
    )
    bbw_squeeze_percentile: float = Field(
        default=20.0, ge=1.0, le=50.0, allow_inf_nan=False, description="Historical BBW percentile threshold to trigger adaptive trend grid mode"
    )
    is_running: bool = True
    auto_disabled_reason: Optional[str] = None


class GlobalRiskConfig(BaseModel):
    kill_switch_active: bool = False
    trading_mode: Literal["SHADOW", "LIVE"] = "SHADOW"
    feed_source: Literal["AUTO", "REAL_WS", "SIMULATOR"] = "AUTO"
    dynamic_leader_enabled: bool = False
    max_daily_loss_usdt: float = Field(default=150.0, ge=1.0, le=1_000_000.0, allow_inf_nan=False)
    max_consecutive_losses: int = Field(default=5, ge=1, le=500)
    max_concurrent_positions: int = Field(default=2, ge=1, le=200)
    max_exposure_usdt: float = Field(default=1500.0, ge=10.0, le=10_000_000.0, allow_inf_nan=False)
    latency_circuit_breaker_ms: float = Field(default=250.0, ge=20.0, le=30_000.0, allow_inf_nan=False)
    auto_disable_rolling_trades: int = Field(default=8, ge=1, le=100)
    cooldown_ms: int = Field(default=500, ge=0, le=60_000)
    shadow_base_latency_ms: float = Field(default=26.0, ge=0.0, le=5_000.0, allow_inf_nan=False)
    shadow_latency_jitter_ms: float = Field(default=12.0, ge=0.0, le=5_000.0, allow_inf_nan=False)
    shadow_adverse_selection_prob: float = Field(default=0.25, ge=0.0, le=1.0, allow_inf_nan=False)


class BatchSymbolUpdateRequest(BaseModel):
    action: Literal["validate", "apply"] = "validate"
    strategy_filter: Literal["ALL_GRID", "ADAPTIVE_GRID", "TREND_GRID", "LONG_SHORT_GRID", "ALL"] = "ALL_GRID"
    only_running: bool = False
    order_usdt: Optional[float] = Field(default=None, ge=5.0, le=500_000.0, allow_inf_nan=False)
    grid_levels: Optional[int] = Field(default=None, ge=1, le=30)
    grid_step_pct: Optional[float] = Field(default=None, ge=0.0005, le=0.20, allow_inf_nan=False)
    grid_take_profit_pct: Optional[float] = Field(default=None, ge=0.0005, le=0.25, allow_inf_nan=False)
    grid_range_pct: Optional[float] = Field(default=None, ge=0.001, le=0.80, allow_inf_nan=False)
    grid_stop_loss_pct: Optional[float] = Field(default=None, ge=0.002, le=0.90, allow_inf_nan=False)
    trend_trailing_mult: Optional[float] = Field(default=None, ge=0.3, le=10.0, allow_inf_nan=False)
    use_post_only_tp: Optional[bool] = None
    sl_trigger_px_type: Optional[Literal["mark", "last"]] = None
    auto_fix_anomalies: bool = True


class PulseInjectionRequest(BaseModel):
    symbol: str = "XRP/USDT:USDT"
    side: Literal["LONG", "SHORT"] = "LONG"
    jump_pct: float = Field(default=0.0085, gt=0, le=0.25, allow_inf_nan=False, description="Simulated Leader jump magnitude, e.g., 0.0085 = +0.85%")
    follower_scenario: Literal["CONVERGE_TP", "REVERSAL_SL", "HARD_SL", "TIMEOUT", "MM_WITHDRAW_MISS"] = "CONVERGE_TP"


# ==================== User, Membership & Admin Models ====================

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    email: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=64)


class PurchaseMembershipRequest(BaseModel):
    plan_id: int
    chain_network: str = "MULTI_EVM"
    force_new: bool = False


class CheckPaymentOrderRequest(BaseModel):
    tx_hash: str = ""


class RedeemVipCodeRequest(BaseModel):
    code: str


class AdminCreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    email: str = ""
    role: Literal["user", "admin"] = "user"
    vip_days: int = 0
    vip_tier: str = "NONE"


class AdminUpdateUserRequest(BaseModel):
    user_id: int
    email: Optional[str] = None
    role: Optional[Literal["user", "admin"]] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = Field(default=None, max_length=128)


class AdminGrantVipRequest(BaseModel):
    user_id: int
    add_days: int = Field(..., ge=-3650, le=3650, description="Days to add (positive to extend, 0 or negative to revoke)")
    vip_tier: str = "专业版会员"
    revoke: bool = False


class AdminMembershipPlanModel(BaseModel):
    id: Optional[int] = None
    plan_code: str
    name: str
    duration_days: int = Field(..., gt=0, le=3650)
    price_usdt: float = Field(..., ge=0, le=1_000_000.0, allow_inf_nan=False)
    original_price_usdt: float = Field(..., ge=0, le=1_000_000.0, allow_inf_nan=False)
    badge_text: str = ""
    features: list[str] = []
    is_enabled: bool = True
    sort_order: int = 1


class AdminGenerateVipCodesRequest(BaseModel):
    plan_name: str = "季度旗舰版会员"
    duration_days: int = Field(default=90, gt=0, le=3650)
    count: int = Field(default=5, ge=1, le=50)


class AdminAuditOrderRequest(BaseModel):
    order_id: int
    action: Literal["APPROVE", "REJECT", "EXPIRE"] = "APPROVE"
    tx_hash: Optional[str] = None



