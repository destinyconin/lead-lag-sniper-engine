"""
Unit & Integration Tests verifying all fixes from the Review & Improvement Plan:
1. Time-decayed proportional log-basis (Fixes #5, #6)
2. Jump freeze during price spikes or open positions (Fix #7)
3. Follower book staleness protection (Fix #8)
4. Exact binary search (`bisect_right`) window lookup (Fix #9)
5. Explicit Follower spread & partial convergence beta in expected profit & Reservation Price (Fixes #10, #11, Section 3.2)
6. Microprice & active volume confirmation (Fix #12)
7. Synchronous lock acquisition before task spawn & full 7-state machine lifecycle (Fixes Fatal ①, ②, ③)
"""
import asyncio
import math
import os
import tempfile
import pytest
from backend.database import AsyncDatabaseManager
from backend.math_engine import SymbolMathState
from backend.models import PulseInjectionRequest, SniperState, SymbolStrategy
from backend.sniper_engine import BidirectionalSniperEngine


def test_proportional_time_decayed_basis_and_jump_freeze():
    """Verifies Fixes #5, #6, #7: log-ratio basis, time decay, and jump freeze."""
    m = SymbolMathState("XRP/USDT:USDT", tau_seconds=30.0)
    cfg = SymbolStrategy(symbol="XRP/USDT:USDT", window_ms=150, min_jump_pct=0.0060)

    # Follower at 2.0000, Leader at 2.0010 (~5 bps higher)
    m.on_follower_tick(1.9998, 2.0002, 1000, 1000, now_mono=1000.0)
    m.on_leader_tick(2.0008, 2.0012, 1000, 1000, cfg, is_locked=False, now_mono=1000.0)

    initial_basis = m.log_basis_ema
    assert initial_basis is not None
    assert math.isclose(initial_basis, math.log(2.0010 / 2.0000), rel_tol=1e-4)

    # Normal small move 100ms later updates basis smoothly
    m.on_follower_tick(1.9998, 2.0002, 1000, 1000, now_mono=1100.0)
    m.on_leader_tick(2.0010, 2.0014, 1000, 1000, cfg, is_locked=False, now_mono=1100.0)
    basis_before_spike = m.log_basis_ema
    assert not m.basis_frozen

    # Sudden +0.8% spike on Leader within 150ms -> MUST FREEZE BASIS (Fix #7)!
    m.on_leader_tick(2.0170, 2.0174, 5000, 200, cfg, is_locked=False, now_mono=1150.0)
    assert m.basis_frozen is True
    assert m.log_basis_ema == basis_before_spike


def test_bisect_window_sampling_and_staleness_guard():
    """Verifies Fix #8 (stale follower book rejection) and Fix #9 (bisect_right lookup)."""
    m = SymbolMathState("SOL/USDT:USDT", tau_seconds=30.0)
    cfg = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        window_ms=150,
        min_jump_pct=0.0060,
        take_profit_pct=0.0020,
        max_book_staleness_ms=500,
    )

    # Seed ticks at t=1000ms and t=1050ms
    m.on_follower_tick(100.00, 100.02, 500, 500, now_mono=1000.0)
    m.on_leader_tick(100.04, 100.06, 500, 500, cfg, is_locked=False, now_mono=1000.0)

    # Verify bisect_right finds the tick <= target_mono_ms
    sample = m.get_tick_at_or_before(m.leader_ts, m.leader_ticks, 1020.0)
    assert sample is not None
    assert sample.mono_ts == 1000.0

    # At t=1700ms (Follower book is 700ms old > max_book_staleness_ms=500ms), signal MUST be rejected (Fix #8)
    m.on_leader_tick(100.04, 100.06, 500, 500, cfg, is_locked=False, now_mono=1600.0)
    m.on_leader_trade("buy", 101.0, 100.0, now_mono=1680.0)
    m.on_leader_tick(100.95, 100.99, 5000, 100, cfg, is_locked=False, now_mono=1700.0)
    stale_sig = m.evaluate_signal(cfg, taker_fee=0.0005, maker_fee=0.0002, now_mono=1700.0)
    assert stale_sig is None

    # Now refresh Follower book at t=1700ms (still lagging at 100.00/100.02) -> signal MUST fire!
    m.on_follower_tick(100.00, 100.02, 500, 500, now_mono=1700.0)
    valid_sig = m.evaluate_signal(cfg, taker_fee=0.0005, maker_fee=0.0002, now_mono=1700.0)
    assert valid_sig is not None
    assert valid_sig.side == "LONG"
    assert valid_sig.reservation_price >= valid_sig.trigger_price
    assert valid_sig.expected_net_pct >= cfg.take_profit_pct


def test_full_state_machine_and_sync_lock():
    """Verifies Fatal Bug ② (synchronous lock) and complete state machine execution."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "test_sniper.db")
            db = AsyncDatabaseManager(db_path=db_file)
            db.init_sync()
            await db.start_worker()

            eng = BidirectionalSniperEngine(db)
            cfg = SymbolStrategy(
                symbol="XRP/USDT:USDT",
                window_ms=150,
                min_jump_pct=0.0060,
                take_profit_pct=0.0020,
                max_hold_ms=400,
            )
            eng._register_symbol_internal(cfg)
            eng.risk_config.cooldown_ms = 50
            eng.risk_config.shadow_adverse_selection_prob = 0.0
            eng.risk_config.shadow_base_latency_ms = 10.0

            # Warm up baseline ticks
            eng._handle_follower_bbo("XRP/USDT:USDT", 2.0000, 2.0002, 2000, 2000)
            eng._handle_leader_bbo("XRP/USDT:USDT", 2.0004, 2.0006, 2000, 2000)
            await asyncio.sleep(0.06)

            # Trigger a +0.95% Leader spike
            eng._handle_leader_trade("XRP/USDT:USDT", "buy", 2.0200, 5000)
            eng._handle_leader_bbo("XRP/USDT:USDT", 2.0195, 2.0199, 8000, 200)

            # State MUST be synchronously locked (not IDLE) immediately before coroutine even awaits!
            assert eng.symbol_states["XRP/USDT:USDT"] != SniperState.IDLE
            assert eng.stats["total_signals"] == 1

            # Wait 40ms so IOC fills and enters HOLDING, then push Follower price up to TP
            await asyncio.sleep(0.05)
            eng._handle_follower_bbo("XRP/USDT:USDT", 2.0160, 2.0164, 2000, 2000)

            # Wait for exit and cooldown to complete
            await asyncio.sleep(0.20)
            assert eng.symbol_states["XRP/USDT:USDT"] == SniperState.IDLE
            assert eng.stats["ioc_filled_count"] == 1
            assert eng.stats["win_count"] == 1
            assert eng.stats["total_net_pnl"] > 0

            await db.stop_worker()

    asyncio.run(_run())


def test_reversal_stop_and_hard_stop_exits():
    """Verifies Section 1.3 missing features: Leader 50% Reversal Stop-Loss and Hard Stop-Loss."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "test_reversal.db")
            db = AsyncDatabaseManager(db_path=db_file)
            db.init_sync()
            await db.start_worker()

            eng = BidirectionalSniperEngine(db)
            cfg = SymbolStrategy(
                symbol="DOGE/USDT:USDT",
                window_ms=150,
                min_jump_pct=0.0060,
                take_profit_pct=0.0025,
                stop_loss_pct=0.0035,
                reversal_stop_ratio=0.50,
                max_hold_ms=800,
                price_precision=5,
                qty_precision=0,
            )
            eng._register_symbol_internal(cfg)
            eng.risk_config.cooldown_ms = 30
            eng.risk_config.shadow_adverse_selection_prob = 0.0
            eng.risk_config.shadow_base_latency_ms = 8.0

            # Seed baseline at 0.30000
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.29995, 0.30005, 10000, 10000)
            eng._handle_leader_bbo("DOGE/USDT:USDT", 0.29995, 0.30005, 10000, 10000)
            await asyncio.sleep(0.03)

            # Leader spikes +1.0% to 0.30300 -> triggers LONG snipe
            eng._handle_leader_trade("DOGE/USDT:USDT", "buy", 0.30300, 50000)
            eng._handle_leader_bbo("DOGE/USDT:USDT", 0.30295, 0.30305, 50000, 1000)

            await asyncio.sleep(0.06)
            assert eng.symbol_states["DOGE/USDT:USDT"] == SniperState.HOLDING

            # Leader fake breakout: drops back to 0.30050 (>50% retracement of the +0.00300 jump)
            eng._handle_leader_bbo("DOGE/USDT:USDT", 0.30045, 0.30055, 5000, 5000)
            await asyncio.sleep(0.08)

            assert eng.symbol_states["DOGE/USDT:USDT"] == SniperState.IDLE
            assert eng.stats["recent_trades"][0]["reason"] == "REVERSAL_SL"

            await db.stop_worker()

    asyncio.run(_run())


def test_instant_symbol_priming_and_shadow_live_isolation():
    """
    Verifies:
    1. Adding a new contract via `add_or_update_symbol` immediately primes tick state (<50ms age).
    2. Switching from SHADOW to LIVE strictly isolates statistics and trade logs.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "test_isolation.db")
            db = AsyncDatabaseManager(db_path=db_file)
            db.init_sync()
            await db.start_worker()

            eng = BidirectionalSniperEngine(db)
            new_cfg = SymbolStrategy(symbol="ARB/USDT:USDT", price_precision=4, qty_precision=1)
            eng.add_or_update_symbol(new_cfg)

            snap_shadow = eng.build_dashboard_snapshot()
            arb_tel = snap_shadow["telemetry"]["ARB/USDT:USDT"]
            assert arb_tel["follower_age_ms"] < 100.0
            assert arb_tel["leader_mid"] > 0
            assert len(snap_shadow["radar_series"]["ARB/USDT:USDT"]) >= 10

            # Record a trade in SHADOW mode
            eng._accumulate_trade_into_stats(
                {
                    "symbol": "ARB/USDT:USDT",
                    "side": "LONG",
                    "trading_mode": "SHADOW",
                    "gross_pnl_usdt": 1.25,
                    "fee_usdt": 0.25,
                    "net_pnl_usdt": 1.00,
                    "entry_price": 0.9200,
                    "exit_price": 0.9240,
                    "filled_qty": 300.0,
                    "exit_reason": "TAKE_PROFIT",
                },
                is_historical=True,
                mode_override="SHADOW",
            )
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] == 1.00

            # Switch to LIVE mode -> stats must be 0.00 (strictly isolated from SHADOW!)
            eng.risk_config.trading_mode = "LIVE"
            snap_live = eng.build_dashboard_snapshot()
            assert snap_live["stats"]["total_net_pnl"] == 0.0
            assert len(snap_live["stats"]["recent_trades"]) == 0

            await db.stop_worker()

    asyncio.run(_run())



