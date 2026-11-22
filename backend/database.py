"""
SQLite Persistence Layer with Multi-Tenant (`user_id`) Isolation & Hot/Cold Path Separation.
- Hot path pushes persistence events to an in-memory `asyncio.Queue` via `put_nowait`.
- Cold-path background worker batches and commits records via `asyncio.to_thread`.
- Strictly isolates per-user `exchanges`, `symbol_configs`, `runtime_settings`, `trade_logs`,
  `orders`, `positions`, and `risk_events` so guests and other users never see or overwrite
  another user's API keys, balances, strategies, or trade history.
- Automatically migrates legacy single-tenant databases by assigning existing live config
  to `ye520958` (if present) or the primary admin account.
"""
import asyncio
import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger("sniper.db")
from typing import Any, Dict, List
from backend.crypto_utils import vault
from backend.models import ExchangeConfigModel, SymbolStrategy


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sniper_engine.db")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 1. 交易所连接配置表 (按 user_id 严格隔离)
CREATE TABLE IF NOT EXISTS exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(32) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,
    api_key TEXT DEFAULT '',
    api_secret_enc TEXT DEFAULT '',
    passphrase_enc TEXT DEFAULT '',
    vip_level VARCHAR(32) DEFAULT 'VIP 0',
    taker_fee DECIMAL(8, 6) DEFAULT 0.0005,
    maker_fee DECIMAL(8, 6) DEFAULT 0.0002,
    is_active BOOLEAN DEFAULT 1,
    auth_status VARCHAR(32) DEFAULT 'UNCONFIGURED',
    auth_message TEXT DEFAULT '未配置 API 密钥',
    account_equity_usdt DECIMAL(18, 4) DEFAULT 0.0,
    last_verified_at TEXT DEFAULT '',
    UNIQUE(user_id, exchange_id)
);

-- 2. 监控币种与策略参数表 (按 user_id 严格隔离)
CREATE TABLE IF NOT EXISTS symbol_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(32) NOT NULL,
    exchange_id VARCHAR(32) DEFAULT 'okx',
    strategy_type VARCHAR(24) DEFAULT 'LEAD_LAG',
    direction VARCHAR(16) DEFAULT 'BOTH',
    order_usdt DECIMAL(12, 2) DEFAULT 300,
    leverage INTEGER DEFAULT 5,
    window_ms INTEGER DEFAULT 150,
    min_jump_pct DECIMAL(6, 4) DEFAULT 0.0060,
    adaptive_z_score DECIMAL(6, 2) DEFAULT 3.00,
    lag_confirm_max_pct DECIMAL(6, 4) DEFAULT 0.0010,
    take_profit_pct DECIMAL(6, 4) DEFAULT 0.0025,
    stop_loss_pct DECIMAL(6, 4) DEFAULT 0.0035,
    reversal_stop_ratio DECIMAL(6, 4) DEFAULT 0.5000,
    trailing_stop_enabled BOOLEAN DEFAULT 1,
    trailing_retracement_pct DECIMAL(6, 4) DEFAULT 0.0008,
    max_hold_ms INTEGER DEFAULT 1500,
    slippage_pct DECIMAL(6, 4) DEFAULT 0.0005,
    beta_coeff DECIMAL(6, 4) DEFAULT 0.8800,
    auto_beta_enabled BOOLEAN DEFAULT 1,
    require_volume_confirm BOOLEAN DEFAULT 1,
    use_post_only_tp BOOLEAN DEFAULT 1,
    sl_trigger_px_type VARCHAR(8) DEFAULT 'mark',
    max_signal_age_ms INTEGER DEFAULT 80,
    max_book_staleness_ms INTEGER DEFAULT 600,
    contract_size DECIMAL(12, 6) DEFAULT 1.0,
    min_qty DECIMAL(12, 6) DEFAULT 0.1,
    qty_precision INTEGER DEFAULT 2,
    price_precision INTEGER DEFAULT 4,
    grid_side_mode VARCHAR(16) DEFAULT 'LONG_SHORT',
    grid_levels INTEGER DEFAULT 3,
    grid_step_pct DECIMAL(8, 6) DEFAULT 0.0080,
    grid_take_profit_pct DECIMAL(8, 6) DEFAULT 0.0085,
    grid_range_pct DECIMAL(8, 6) DEFAULT 0.0280,
    grid_stop_loss_pct DECIMAL(8, 6) DEFAULT 0.0380,
    grid_recenter_enabled BOOLEAN DEFAULT 1,
    trend_dead_zone_mult DECIMAL(6, 2) DEFAULT 1.25,
    trend_trailing_mult DECIMAL(6, 2) DEFAULT 1.25,
    trend_pyramid_decay BOOLEAN DEFAULT 1,
    trend_use_bb_breakout BOOLEAN DEFAULT 1,
    bbw_timeframe VARCHAR(8) DEFAULT '15m',
    bbw_squeeze_percentile DECIMAL(6, 2) DEFAULT 20.00,
    is_running BOOLEAN DEFAULT 1,
    auto_disabled_reason TEXT DEFAULT NULL,
    UNIQUE(user_id, symbol)
);

-- 3. 狙击交易统计与复盘记录表 (按 user_id 隔离)
CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(32) NOT NULL,
    leader_ex VARCHAR(32) DEFAULT 'binanceusdm',
    follower_ex VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    trading_mode VARCHAR(16) DEFAULT 'SHADOW',
    order_id VARCHAR(64),
    exit_order_type VARCHAR(24) DEFAULT 'MAKER_POST_ONLY',
    leader_jump_pct DECIMAL(8, 6),
    follower_lag_pct DECIMAL(8, 6),
    expected_net_pct DECIMAL(8, 6),
    actual_slippage_pct DECIMAL(8, 6),
    beta_est DECIMAL(6, 4),
    leader_snapshot_price DECIMAL(18, 8),
    follower_snapshot_price DECIMAL(18, 8),
    reservation_price DECIMAL(18, 8),
    entry_price DECIMAL(18, 8),
    exit_price DECIMAL(18, 8),
    filled_qty DECIMAL(18, 8),
    gross_pnl_usdt DECIMAL(12, 4),
    fee_usdt DECIMAL(12, 4),
    net_pnl_usdt DECIMAL(12, 4),
    signal_ts_ms INTEGER,
    submit_ts_ms INTEGER,
    ack_ts_ms INTEGER,
    signal_to_fill_ms DECIMAL(8, 2),
    hold_duration_ms INTEGER,
    exit_reason VARCHAR(32),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 订单生命周期与对账表 (按 user_id 隔离)
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(32) NOT NULL,
    exchange_id VARCHAR(32) NOT NULL,
    purpose VARCHAR(16) NOT NULL,
    side VARCHAR(8) NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    price DECIMAL(18, 8),
    requested_qty DECIMAL(18, 8),
    filled_qty DECIMAL(18, 8),
    avg_price DECIMAL(18, 8),
    status VARCHAR(24) NOT NULL,
    latency_ms DECIMAL(8, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. 持仓状态与孤儿仓位对账表 (按 user_id + symbol 隔离)
CREATE TABLE IF NOT EXISTS positions (
    user_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(32) NOT NULL,
    exchange_id VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    qty DECIMAL(18, 8) NOT NULL,
    entry_price DECIMAL(18, 8) NOT NULL,
    tp_order_id VARCHAR(64),
    sl_order_id VARCHAR(64),
    state VARCHAR(24) NOT NULL,
    opened_at_ms INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, symbol)
);

-- 6. 风控与对账审计日志表 (按 user_id 隔离)
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    level VARCHAR(16) NOT NULL,
    category VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) DEFAULT '',
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. 运行时全局配置持久化表 (按 user_id 隔离)
CREATE TABLE IF NOT EXISTS runtime_settings (
    user_id INTEGER NOT NULL DEFAULT 0,
    key VARCHAR(64) NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT DEFAULT '',
    PRIMARY KEY (user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_uid ON trade_logs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_uid ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at);
CREATE INDEX IF NOT EXISTS idx_risk_events_uid ON risk_events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_created ON risk_events(created_at);
"""


DEFAULT_EXCHANGES = [
    ("binanceusdm", "Binance USDM (币安合约)", "leader", "VIP 1", 0.0004, 0.00018, 1),
    ("okx", "OKX Swap (欧易永续)", "follower", "VIP 1", 0.0005, 0.00020, 1),
    ("bybit", "Bybit Unified (Bybit永续)", "follower", "VIP 0", 0.00055, 0.00020, 0),
    ("gate", "Gate.io Futures (芝麻开门)", "follower", "VIP 2", 0.0005, 0.00015, 0),
    ("bitget", "Bitget Futures (Bitget合约)", "follower", "VIP 1", 0.0006, 0.00020, 0),
    ("krakenfutures", "Kraken Futures (Kraken合约)", "follower", "Tier 2", 0.0005, 0.00020, 0),
    ("futu", "富途牛牛 (Futu OpenD 证券接口)", "follower", "美股标准", 0.0003, 0.00010, 0),
    ("moomoo", "moomoo (Moomoo OpenD 国际证券)", "follower", "US Tier 1", 0.0003, 0.00010, 0),
    ("ibkr", "IBKR (盈透证券 TWS / Client Portal API)", "follower", "IBKR Pro", 0.0002, 0.00008, 0),
    ("schwab", "Charles Schwab (嘉信理财 Trader API)", "follower", "Zero-Comm", 0.0001, 0.00005, 0),
]

DEFAULT_SYMBOLS = [
    # symbol, direction, order_usdt, leverage, window_ms, min_jump_pct, tp_pct, sl_pct, max_hold_ms, slippage_pct, beta, qty_prec, price_prec
    ("BTC/USDT:USDT", "BOTH", 50.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.88, 3, 1),
    ("ETH/USDT:USDT", "BOTH", 50.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.88, 2, 2),
    ("SOL/USDT:USDT", "BOTH", 50.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.90, 2, 3),
    ("XRP/USDT:USDT", "BOTH", 50.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.88, 1, 4),
]


class AsyncDatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._write_queue: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue(maxsize=10000)
        self._worker_task: asyncio.Task | None = None
        self._running = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _migrate_legacy_single_tenant_tables(self, conn: sqlite3.Connection):
        """
        Detects legacy single-tenant schema (without `user_id` column on `exchanges`)
        and safely migrates existing configured live data to `ye520958` (or first admin).
        """
        cur = conn.cursor()
        tbl = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exchanges'"
        ).fetchone()
        if not tbl:
            return

        cols = {row[1] for row in cur.execute("PRAGMA table_info(exchanges)").fetchall()}
        if "user_id" in cols:
            return

        # Determine target owner user_id for legacy configured data
        owner_uid = 0
        has_users_tbl = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if has_users_tbl:
            row = cur.execute("SELECT id FROM users WHERE username = 'ye520958' LIMIT 1").fetchone()
            if row:
                owner_uid = int(row[0])
            else:
                row = cur.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1").fetchone()
                if row:
                    owner_uid = int(row[0])

        legacy_tables = [
            "exchanges",
            "symbol_configs",
            "trade_logs",
            "orders",
            "positions",
            "risk_events",
            "runtime_settings",
        ]
        for t in legacy_tables:
            exists = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            if exists:
                cur.execute(f"ALTER TABLE {t} RENAME TO {t}_legacy_v1")

        # Recreate all multi-tenant tables
        conn.executescript(SCHEMA_SQL)

        def legacy_exists(name: str) -> bool:
            return cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (f"{name}_legacy_v1",)
            ).fetchone() is not None

        if owner_uid > 0:
            if legacy_exists("exchanges"):
                cur.execute(
                    """INSERT OR IGNORE INTO exchanges
                       (user_id, exchange_id, display_name, role, api_key, api_secret_enc, passphrase_enc,
                        vip_level, taker_fee, maker_fee, is_active, auth_status, auth_message,
                        account_equity_usdt, last_verified_at)
                       SELECT ?, exchange_id, display_name, role, api_key, api_secret_enc, passphrase_enc,
                              vip_level, taker_fee, maker_fee, is_active, auth_status, auth_message,
                              account_equity_usdt, last_verified_at
                       FROM exchanges_legacy_v1""",
                    (owner_uid,),
                )
            if legacy_exists("symbol_configs"):
                cur.execute(
                    """INSERT OR IGNORE INTO symbol_configs
                       (user_id, symbol, direction, order_usdt, leverage, window_ms, min_jump_pct,
                        adaptive_z_score, lag_confirm_max_pct, take_profit_pct, stop_loss_pct,
                        reversal_stop_ratio, trailing_stop_enabled, trailing_retracement_pct,
                        max_hold_ms, slippage_pct, beta_coeff, auto_beta_enabled,
                        require_volume_confirm, use_post_only_tp, max_signal_age_ms,
                        max_book_staleness_ms, contract_size, min_qty, qty_precision,
                        price_precision, is_running, auto_disabled_reason)
                       SELECT ?, symbol, direction, order_usdt, leverage, window_ms, min_jump_pct,
                              adaptive_z_score, lag_confirm_max_pct, take_profit_pct, stop_loss_pct,
                              reversal_stop_ratio, trailing_stop_enabled, trailing_retracement_pct,
                              max_hold_ms, slippage_pct, beta_coeff, auto_beta_enabled,
                              require_volume_confirm, use_post_only_tp, max_signal_age_ms,
                              max_book_staleness_ms, contract_size, min_qty, qty_precision,
                              price_precision, is_running, auto_disabled_reason
                       FROM symbol_configs_legacy_v1""",
                    (owner_uid,),
                )
            if legacy_exists("runtime_settings"):
                cur.execute(
                    """INSERT OR IGNORE INTO runtime_settings (user_id, key, value_json, updated_at)
                       SELECT ?, key, value_json, updated_at FROM runtime_settings_legacy_v1""",
                    (owner_uid,),
                )
            if legacy_exists("risk_events"):
                cur.execute(
                    """INSERT INTO risk_events (user_id, level, category, symbol, message, created_at)
                       SELECT ?, level, category, symbol, message, created_at FROM risk_events_legacy_v1""",
                    (owner_uid,),
                )

        # Keep legacy SHADOW trade_logs & old dummy orders under user_id = 0,
        # while assigning any LIVE trade_logs to owner_uid.
        if legacy_exists("trade_logs"):
            cur.execute(
                """INSERT INTO trade_logs
                   (user_id, symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type,
                    leader_jump_pct, follower_lag_pct, expected_net_pct, actual_slippage_pct,
                    beta_est, leader_snapshot_price, follower_snapshot_price, reservation_price,
                    entry_price, exit_price, filled_qty, gross_pnl_usdt, fee_usdt, net_pnl_usdt,
                    signal_ts_ms, submit_ts_ms, ack_ts_ms, signal_to_fill_ms, hold_duration_ms, exit_reason, created_at)
                   SELECT CASE WHEN trading_mode = 'LIVE' THEN ? ELSE 0 END,
                          symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type,
                          leader_jump_pct, follower_lag_pct, expected_net_pct, actual_slippage_pct,
                          beta_est, leader_snapshot_price, follower_snapshot_price, reservation_price,
                          entry_price, exit_price, filled_qty, gross_pnl_usdt, fee_usdt, net_pnl_usdt,
                          signal_ts_ms, submit_ts_ms, ack_ts_ms, signal_to_fill_ms, hold_duration_ms, exit_reason, created_at
                   FROM trade_logs_legacy_v1""",
                (owner_uid,),
            )

        if legacy_exists("orders"):
            cur.execute(
                """INSERT OR IGNORE INTO orders
                   (order_id, user_id, symbol, exchange_id, purpose, side, order_type,
                    price, requested_qty, filled_qty, avg_price, status, latency_ms, created_at)
                   SELECT order_id, 0, symbol, exchange_id, purpose, side, order_type,
                          price, requested_qty, filled_qty, avg_price, status, latency_ms, created_at
                   FROM orders_legacy_v1"""
            )

        for t in legacy_tables:
            cur.execute(f"DROP TABLE IF EXISTS {t}_legacy_v1")
        conn.commit()

    def _ensure_symbol_columns_sync(self, conn: sqlite3.Connection):
        """Additive column migration for multi-strategy (`LEAD_LAG` / `LONG_SHORT_GRID` / `REVERSE_GRID` / `TREND_GRID` / `ADAPTIVE_GRID`) fields."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(symbol_configs)").fetchall()}
        additions = [
            ("exchange_id", "ALTER TABLE symbol_configs ADD COLUMN exchange_id VARCHAR(32) DEFAULT 'okx'"),
            ("strategy_type", "ALTER TABLE symbol_configs ADD COLUMN strategy_type VARCHAR(24) DEFAULT 'LEAD_LAG'"),
            ("grid_side_mode", "ALTER TABLE symbol_configs ADD COLUMN grid_side_mode VARCHAR(16) DEFAULT 'LONG_SHORT'"),
            ("grid_levels", "ALTER TABLE symbol_configs ADD COLUMN grid_levels INTEGER DEFAULT 3"),
            ("grid_step_pct", "ALTER TABLE symbol_configs ADD COLUMN grid_step_pct DECIMAL(8, 6) DEFAULT 0.0080"),
            ("grid_take_profit_pct", "ALTER TABLE symbol_configs ADD COLUMN grid_take_profit_pct DECIMAL(8, 6) DEFAULT 0.0085"),
            ("grid_range_pct", "ALTER TABLE symbol_configs ADD COLUMN grid_range_pct DECIMAL(8, 6) DEFAULT 0.0280"),
            ("grid_stop_loss_pct", "ALTER TABLE symbol_configs ADD COLUMN grid_stop_loss_pct DECIMAL(8, 6) DEFAULT 0.0380"),
            ("grid_recenter_enabled", "ALTER TABLE symbol_configs ADD COLUMN grid_recenter_enabled BOOLEAN DEFAULT 1"),
            ("trend_dead_zone_mult", "ALTER TABLE symbol_configs ADD COLUMN trend_dead_zone_mult DECIMAL(6, 2) DEFAULT 1.25"),
            ("trend_trailing_mult", "ALTER TABLE symbol_configs ADD COLUMN trend_trailing_mult DECIMAL(6, 2) DEFAULT 1.25"),
            ("trend_pyramid_decay", "ALTER TABLE symbol_configs ADD COLUMN trend_pyramid_decay BOOLEAN DEFAULT 1"),
            ("trend_use_bb_breakout", "ALTER TABLE symbol_configs ADD COLUMN trend_use_bb_breakout BOOLEAN DEFAULT 1"),
            ("bbw_timeframe", "ALTER TABLE symbol_configs ADD COLUMN bbw_timeframe VARCHAR(8) DEFAULT '15m'"),
            ("bbw_squeeze_percentile", "ALTER TABLE symbol_configs ADD COLUMN bbw_squeeze_percentile DECIMAL(6, 2) DEFAULT 20.00"),
            ("sl_trigger_px_type", "ALTER TABLE symbol_configs ADD COLUMN sl_trigger_px_type VARCHAR(8) DEFAULT 'mark'"),
        ]
        for col, ddl in additions:
            if col not in existing:
                conn.execute(ddl)

    def init_sync(self):
        """Initialize multi-tenant schema, migrate legacy data if present, and seed default guest workspace."""
        conn = self._connect()
        try:
            self._migrate_legacy_single_tenant_tables(conn)
            conn.executescript(SCHEMA_SQL)
            self._ensure_symbol_columns_sync(conn)
            conn.commit()
        finally:
            conn.close()
        # Ensure guest preview workspace (user_id=0) has clean default entries
        self.ensure_user_workspace_sync(0)

    def ensure_user_workspace_sync(self, user_id: int):
        """
        Idempotently initializes clean default exchanges & starter symbols for `user_id`
        if that user has no existing rows yet.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            existing_ex = {
                r[0]
                for r in cur.execute("SELECT exchange_id FROM exchanges WHERE user_id = ?", (user_id,)).fetchall()
            }
            is_brand_new = len(existing_ex) == 0
            for ex_id, name, role, vip, taker, maker, active in DEFAULT_EXCHANGES:
                if ex_id not in existing_ex:
                    eff_role = role if is_brand_new else "follower"
                    eff_active = active if is_brand_new else 0
                    cur.execute(
                        """INSERT OR IGNORE INTO exchanges
                           (user_id, exchange_id, display_name, role, vip_level, taker_fee, maker_fee, is_active)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (user_id, ex_id, name, eff_role, vip, taker, maker, eff_active),
                    )
            sym_cnt = cur.execute("SELECT COUNT(*) FROM symbol_configs WHERE user_id = ?", (user_id,)).fetchone()[0]
            if sym_cnt == 0:
                for row in DEFAULT_SYMBOLS:
                    cur.execute(
                        """INSERT OR IGNORE INTO symbol_configs
                           (user_id, symbol, direction, order_usdt, leverage, window_ms, min_jump_pct,
                            take_profit_pct, stop_loss_pct, max_hold_ms, slippage_pct,
                            beta_coeff, qty_precision, price_precision, is_running)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                        (user_id, *row),
                    )
            conn.commit()
        finally:
            conn.close()

    async def start_worker(self):
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._batch_writer_loop())

    async def stop_worker(self):
        self._running = False
        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            self._worker_task = None
        await self._flush_remaining()

    def enqueue_write(self, sql: str, params: tuple = ()):
        """Hot-path safe: non-blocking enqueue for SQLite persistence."""
        try:
            self._write_queue.put_nowait((sql, params))
        except asyncio.QueueFull:
            logger.warning("[DB] Async write queue full (10000); dropping write: %s", sql[:80])

    async def _batch_writer_loop(self):
        while self._running:
            try:
                first_item = await asyncio.wait_for(self._write_queue.get(), timeout=0.25)
                batch = [first_item]
                while len(batch) < 128 and not self._write_queue.empty():
                    batch.append(self._write_queue.get_nowait())
                await asyncio.to_thread(self._execute_batch_sync, batch)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[DB] Batch writer error: %s", exc)
                await asyncio.sleep(0.1)

    async def _flush_remaining(self):
        batch = []
        while not self._write_queue.empty():
            batch.append(self._write_queue.get_nowait())
        if batch:
            await asyncio.to_thread(self._execute_batch_sync, batch)

    def _execute_batch_sync(self, batch: List[tuple[str, tuple]]):
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                for sql, params in batch:
                    cur.execute(sql, params)
                conn.commit()
            except Exception as batch_exc:
                # Never let 1 malformed statement roll back 127 valid trade/position writes!
                conn.rollback()
                logger.warning("[DB] Batch commit failed (%s); retrying %d statements individually", batch_exc, len(batch))
                for sql, params in batch:
                    try:
                        cur.execute(sql, params)
                        conn.commit()
                    except Exception as item_exc:
                        conn.rollback()
                        logger.error("[DB] Statement failed (%s): %s", item_exc, sql[:100])
        finally:
            conn.close()

    # ==================== Per-User Synchronous / Thread-Pool Query Helpers ====================
    def load_exchanges_sync(self, user_id: int = 0) -> List[Dict[str, Any]]:
        self.ensure_user_workspace_sync(user_id)
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM exchanges WHERE user_id = ? ORDER BY id ASC", (user_id,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                ex_id = d.get("exchange_id", "")
                raw_key = (d.get("api_key") or "").strip()
                dec_secret = vault.decrypt(d.get("api_secret_enc", "")).strip()
                dec_pass = vault.decrypt(d.get("passphrase_enc", "")).strip()

                has_key = bool(raw_key)
                has_sec = bool(dec_secret)
                has_pass = bool(dec_pass)
                has_any = has_key or has_sec or has_pass
                has_all = has_key and has_sec

                d["api_key"] = raw_key
                d["api_key_masked"] = vault.mask(raw_key)
                d["has_api_key"] = has_key
                d["has_api_secret"] = has_sec
                d["has_passphrase"] = has_pass
                d["has_credentials"] = has_all
                d["_decrypted_secret"] = dec_secret
                d["_decrypted_passphrase"] = dec_pass
                d["account_equity_usdt"] = float(d.get("account_equity_usdt") or 0.0)
                d["last_verified_at"] = d.get("last_verified_at") or ""

                saved_status = d.get("auth_status") or "UNCONFIGURED"
                saved_msg = d.get("auth_message") or "未配置 API 密钥"
                if not has_any:
                    d["auth_status"] = "UNCONFIGURED"
                    d["auth_message"] = "未配置 API 密钥"
                elif not has_all:
                    missing = []
                    if not has_key:
                        missing.append("API Key")
                    if not has_sec:
                        missing.append("API Secret")
                    d["auth_status"] = "CONFIG_INCOMPLETE"
                    d["auth_message"] = f"配置不完整：缺少 {', '.join(missing)}，请补充完整后点击「验证并保存」"
                else:
                    if saved_status in ("UNCONFIGURED", "CONFIG_INCOMPLETE"):
                        d["auth_status"] = "CONFIGURED_UNVERIFIED"
                        d["auth_message"] = "已填写密钥，点击「验证并保存」连通交易所"
                    else:
                        d["auth_status"] = saved_status
                        d["auth_message"] = saved_msg
                result.append(d)
            return result
        finally:
            conn.close()

    def update_exchange_auth_status_sync(
        self,
        exchange_id: str,
        auth_status: str,
        auth_message: str,
        account_equity_usdt: float = 0.0,
        user_id: int = 0,
    ):
        conn = self._connect()
        try:
            ts_str = time.strftime("%H:%M:%S")
            conn.execute(
                """UPDATE exchanges
                   SET auth_status=?, auth_message=?, account_equity_usdt=?, last_verified_at=?
                   WHERE user_id=? AND exchange_id=?""",
                (auth_status, auth_message, round(account_equity_usdt, 4), ts_str, user_id, exchange_id),
            )
            conn.commit()
        finally:
            conn.close()

    def clear_exchange_credentials_sync(self, exchange_id: str, user_id: int = 0):
        """Permanently wipes saved API Key, encrypted Secret, Passphrase, and verification state for a user."""
        conn = self._connect()
        try:
            if exchange_id == "ALL":
                conn.execute(
                    """UPDATE exchanges
                       SET api_key='', api_secret_enc='', passphrase_enc='',
                           auth_status='UNCONFIGURED', auth_message='未配置 API 密钥',
                           account_equity_usdt=0.0, last_verified_at=''
                       WHERE user_id=?""",
                    (user_id,),
                )
            else:
                conn.execute(
                    """UPDATE exchanges
                       SET api_key='', api_secret_enc='', passphrase_enc='',
                           auth_status='UNCONFIGURED', auth_message='未配置 API 密钥',
                           account_equity_usdt=0.0, last_verified_at=''
                       WHERE user_id=? AND exchange_id=?""",
                    (user_id, exchange_id),
                )
            conn.commit()
        finally:
            conn.close()

    def save_exchange_sync(self, cfg: ExchangeConfigModel, user_id: int = 0):
        self.ensure_user_workspace_sync(user_id)
        conn = self._connect()
        try:
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT api_key, api_secret_enc, passphrase_enc FROM exchanges WHERE user_id = ? AND exchange_id = ?",
                (user_id, cfg.exchange_id),
            ).fetchone()

            in_key = (cfg.api_key or "").strip()
            in_sec = (cfg.api_secret or "").strip()
            in_pass = (cfg.passphrase or "").strip()

            if existing:
                api_key = in_key if (in_key and "****" not in in_key and "•" not in in_key) else existing[0]
                secret_enc = (
                    vault.encrypt(in_sec)
                    if (in_sec and "****" not in in_sec and "•" not in in_sec)
                    else existing[1]
                )
                pass_enc = (
                    vault.encrypt(in_pass)
                    if (in_pass and "****" not in in_pass and "•" not in in_pass)
                    else existing[2]
                )
                if cfg.role == "leader" and cfg.is_active:
                    cur.execute(
                        "UPDATE exchanges SET role = 'follower' WHERE user_id = ? AND role = 'leader' AND exchange_id != ?",
                        (user_id, cfg.exchange_id),
                    )
                cur.execute(
                    """UPDATE exchanges
                       SET display_name=?, role=?, api_key=?, api_secret_enc=?, passphrase_enc=?,
                           vip_level=?, taker_fee=?, maker_fee=?, is_active=?
                       WHERE user_id=? AND exchange_id=?""",
                    (
                        cfg.display_name,
                        cfg.role,
                        api_key,
                        secret_enc,
                        pass_enc,
                        cfg.vip_level,
                        cfg.taker_fee,
                        cfg.maker_fee,
                        1 if cfg.is_active else 0,
                        user_id,
                        cfg.exchange_id,
                    ),
                )
            else:
                secret_enc = vault.encrypt(in_sec)
                pass_enc = vault.encrypt(in_pass)
                cur.execute(
                    """INSERT INTO exchanges
                       (user_id, exchange_id, display_name, role, api_key, api_secret_enc, passphrase_enc,
                        vip_level, taker_fee, maker_fee, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        cfg.exchange_id,
                        cfg.display_name,
                        cfg.role,
                        in_key,
                        secret_enc,
                        pass_enc,
                        cfg.vip_level,
                        cfg.taker_fee,
                        cfg.maker_fee,
                        1 if cfg.is_active else 0,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def load_symbols_sync(self, user_id: int = 0) -> List[SymbolStrategy]:
        self.ensure_user_workspace_sync(user_id)
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM symbol_configs WHERE user_id = ? ORDER BY id ASC", (user_id,)
            ).fetchall()
            strategies = []
            for r in rows:
                d = dict(r)
                d.pop("user_id", None)
                d["trailing_stop_enabled"] = bool(d.get("trailing_stop_enabled", 1))
                d["auto_beta_enabled"] = bool(d.get("auto_beta_enabled", 1))
                d["require_volume_confirm"] = bool(d.get("require_volume_confirm", 1))
                d["use_post_only_tp"] = bool(d.get("use_post_only_tp", 1))
                d["grid_recenter_enabled"] = bool(d.get("grid_recenter_enabled", 1) if d.get("grid_recenter_enabled") is not None else 1)
                d["trend_pyramid_decay"] = bool(d.get("trend_pyramid_decay", 1) if d.get("trend_pyramid_decay") is not None else 1)
                d["trend_use_bb_breakout"] = bool(d.get("trend_use_bb_breakout", 1) if d.get("trend_use_bb_breakout") is not None else 1)
                if not d.get("exchange_id"):
                    d["exchange_id"] = "okx"
                if not d.get("strategy_type"):
                    d["strategy_type"] = "LEAD_LAG"
                if not d.get("grid_side_mode"):
                    d["grid_side_mode"] = "LONG_SHORT"
                if d.get("bbw_timeframe") not in ("15m", "1H"):
                    d["bbw_timeframe"] = "15m"
                if d.get("sl_trigger_px_type") not in ("mark", "last"):
                    d["sl_trigger_px_type"] = "mark"
                for k, dv in (
                    ("grid_levels", 3),
                    ("grid_step_pct", 0.0080),
                    ("grid_take_profit_pct", 0.0085),
                    ("grid_range_pct", 0.028),
                    ("grid_stop_loss_pct", 0.038),
                    ("trend_dead_zone_mult", 1.25),
                    ("trend_trailing_mult", 1.25),
                    ("bbw_squeeze_percentile", 20.0),
                ):
                    if d.get(k) is None:
                        d[k] = dv
                d["is_running"] = bool(d.get("is_running", 1))
                strategies.append(SymbolStrategy(**d))
            return strategies
        finally:
            conn.close()

    def upsert_symbol_sync(self, cfg: SymbolStrategy, user_id: int = 0):
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO symbol_configs (
                    user_id, symbol, exchange_id, strategy_type, direction, order_usdt, leverage, window_ms, min_jump_pct,
                    adaptive_z_score, lag_confirm_max_pct, take_profit_pct, stop_loss_pct,
                    reversal_stop_ratio, trailing_stop_enabled, trailing_retracement_pct,
                    max_hold_ms, slippage_pct, beta_coeff, auto_beta_enabled,
                    require_volume_confirm, use_post_only_tp, sl_trigger_px_type, max_signal_age_ms,
                    max_book_staleness_ms, contract_size, min_qty, qty_precision,
                    price_precision, grid_side_mode, grid_levels, grid_step_pct,
                    grid_take_profit_pct, grid_range_pct, grid_stop_loss_pct,
                    grid_recenter_enabled, trend_dead_zone_mult, trend_trailing_mult,
                    trend_pyramid_decay, trend_use_bb_breakout, bbw_timeframe,
                    bbw_squeeze_percentile, is_running, auto_disabled_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, symbol) DO UPDATE SET
                    exchange_id=excluded.exchange_id,
                    strategy_type=excluded.strategy_type,
                    direction=excluded.direction,
                    order_usdt=excluded.order_usdt,
                    leverage=excluded.leverage,
                    window_ms=excluded.window_ms,
                    min_jump_pct=excluded.min_jump_pct,
                    adaptive_z_score=excluded.adaptive_z_score,
                    lag_confirm_max_pct=excluded.lag_confirm_max_pct,
                    take_profit_pct=excluded.take_profit_pct,
                    stop_loss_pct=excluded.stop_loss_pct,
                    reversal_stop_ratio=excluded.reversal_stop_ratio,
                    trailing_stop_enabled=excluded.trailing_stop_enabled,
                    trailing_retracement_pct=excluded.trailing_retracement_pct,
                    max_hold_ms=excluded.max_hold_ms,
                    slippage_pct=excluded.slippage_pct,
                    beta_coeff=excluded.beta_coeff,
                    auto_beta_enabled=excluded.auto_beta_enabled,
                    require_volume_confirm=excluded.require_volume_confirm,
                    use_post_only_tp=excluded.use_post_only_tp,
                    sl_trigger_px_type=excluded.sl_trigger_px_type,
                    max_signal_age_ms=excluded.max_signal_age_ms,
                    max_book_staleness_ms=excluded.max_book_staleness_ms,
                    contract_size=excluded.contract_size,
                    min_qty=excluded.min_qty,
                    qty_precision=excluded.qty_precision,
                    price_precision=excluded.price_precision,
                    grid_side_mode=excluded.grid_side_mode,
                    grid_levels=excluded.grid_levels,
                    grid_step_pct=excluded.grid_step_pct,
                    grid_take_profit_pct=excluded.grid_take_profit_pct,
                    grid_range_pct=excluded.grid_range_pct,
                    grid_stop_loss_pct=excluded.grid_stop_loss_pct,
                    grid_recenter_enabled=excluded.grid_recenter_enabled,
                    trend_dead_zone_mult=excluded.trend_dead_zone_mult,
                    trend_trailing_mult=excluded.trend_trailing_mult,
                    trend_pyramid_decay=excluded.trend_pyramid_decay,
                    trend_use_bb_breakout=excluded.trend_use_bb_breakout,
                    bbw_timeframe=excluded.bbw_timeframe,
                    bbw_squeeze_percentile=excluded.bbw_squeeze_percentile,
                    is_running=excluded.is_running,
                    auto_disabled_reason=excluded.auto_disabled_reason
                """,
                (
                    user_id,
                    cfg.symbol,
                    str(getattr(cfg, "exchange_id", "okx") or "okx"),
                    cfg.strategy_type,
                    cfg.direction,
                    cfg.order_usdt,
                    cfg.leverage,
                    cfg.window_ms,
                    cfg.min_jump_pct,
                    cfg.adaptive_z_score,
                    cfg.lag_confirm_max_pct,
                    cfg.take_profit_pct,
                    cfg.stop_loss_pct,
                    cfg.reversal_stop_ratio,
                    1 if cfg.trailing_stop_enabled else 0,
                    cfg.trailing_retracement_pct,
                    cfg.max_hold_ms,
                    cfg.slippage_pct,
                    cfg.beta_coeff,
                    1 if cfg.auto_beta_enabled else 0,
                    1 if cfg.require_volume_confirm else 0,
                    1 if cfg.use_post_only_tp else 0,
                    cfg.sl_trigger_px_type,
                    cfg.max_signal_age_ms,
                    cfg.max_book_staleness_ms,
                    cfg.contract_size,
                    cfg.min_qty,
                    cfg.qty_precision,
                    cfg.price_precision,
                    cfg.grid_side_mode,
                    cfg.grid_levels,
                    cfg.grid_step_pct,
                    cfg.grid_take_profit_pct,
                    cfg.grid_range_pct,
                    cfg.grid_stop_loss_pct,
                    1 if cfg.grid_recenter_enabled else 0,
                    cfg.trend_dead_zone_mult,
                    cfg.trend_trailing_mult,
                    1 if cfg.trend_pyramid_decay else 0,
                    1 if cfg.trend_use_bb_breakout else 0,
                    cfg.bbw_timeframe,
                    cfg.bbw_squeeze_percentile,
                    1 if cfg.is_running else 0,
                    cfg.auto_disabled_reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_symbol_sync(self, symbol: str, user_id: int = 0):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM symbol_configs WHERE user_id = ? AND symbol = ?", (user_id, symbol))
            conn.commit()
        finally:
            conn.close()

    def load_recent_trades_sync(self, limit: int = 100, user_id: int = 0) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trade_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_open_positions_sync(self, user_id: int = 0) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM positions WHERE user_id = ? AND state IN ('HOLDING', 'EXITING')",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_risk_events_sync(self, limit: int = 50, user_id: int = 0) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM risk_events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def record_trade_async(self, trade: Dict[str, Any], user_id: int = 0):
        ord_id = str(trade.get("order_id") or "")
        sql = """INSERT INTO trade_logs (
            user_id, symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type,
            leader_jump_pct, follower_lag_pct, expected_net_pct, actual_slippage_pct,
            beta_est, leader_snapshot_price, follower_snapshot_price, reservation_price,
            entry_price, exit_price, filled_qty, gross_pnl_usdt, fee_usdt, net_pnl_usdt,
            signal_ts_ms, submit_ts_ms, ack_ts_ms, signal_to_fill_ms, hold_duration_ms, exit_reason
        ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM trade_logs WHERE user_id = ? AND order_id = ? AND order_id != ''
        )"""
        params = (
            user_id,
            trade["symbol"],
            trade.get("leader_ex", "binanceusdm"),
            trade.get("follower_ex", "okx"),
            trade["side"],
            trade.get("trading_mode", "SHADOW"),
            ord_id,
            trade.get("exit_order_type", "MAKER_POST_ONLY"),
            trade.get("leader_jump_pct", 0.0),
            trade.get("follower_lag_pct", 0.0),
            trade.get("expected_net_pct", 0.0),
            trade.get("actual_slippage_pct", 0.0),
            trade.get("beta_est", 0.88),
            trade.get("leader_snapshot_price", 0.0),
            trade.get("follower_snapshot_price", 0.0),
            trade.get("reservation_price", 0.0),
            trade.get("entry_price", 0.0),
            trade.get("exit_price", 0.0),
            trade.get("filled_qty", 0.0),
            trade.get("gross_pnl_usdt", 0.0),
            trade.get("fee_usdt", 0.0),
            trade.get("net_pnl_usdt", 0.0),
            trade.get("signal_ts_ms", int(time.time() * 1000)),
            trade.get("submit_ts_ms", int(time.time() * 1000)),
            trade.get("ack_ts_ms", int(time.time() * 1000)),
            trade.get("signal_to_fill_ms", 0.0),
            trade.get("hold_duration_ms", 0),
            trade.get("exit_reason", "TAKE_PROFIT"),
            user_id,
            ord_id,
        )
        self.enqueue_write(sql, params)

    def record_order_async(
        self,
        order_id: str,
        symbol: str,
        exchange_id: str,
        purpose: str,
        side: str,
        order_type: str,
        price: float,
        requested_qty: float,
        filled_qty: float,
        avg_price: float,
        status: str,
        latency_ms: float,
        user_id: int = 0,
    ):
        sql = """INSERT OR REPLACE INTO orders (
            order_id, user_id, symbol, exchange_id, purpose, side, order_type,
            price, requested_qty, filled_qty, avg_price, status, latency_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        self.enqueue_write(
            sql,
            (
                order_id,
                user_id,
                symbol,
                exchange_id,
                purpose,
                side,
                order_type,
                price,
                requested_qty,
                filled_qty,
                avg_price,
                status,
                latency_ms,
            ),
        )

    def upsert_position_async(
        self,
        symbol: str,
        exchange_id: str,
        side: str,
        qty: float,
        entry_price: float,
        tp_order_id: str,
        sl_order_id: str,
        state: str,
        opened_at_ms: int,
        user_id: int = 0,
    ):
        sql = """INSERT INTO positions (
            user_id, symbol, exchange_id, side, qty, entry_price, tp_order_id, sl_order_id, state, opened_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            side=excluded.side,
            qty=excluded.qty,
            entry_price=excluded.entry_price,
            tp_order_id=excluded.tp_order_id,
            sl_order_id=excluded.sl_order_id,
            state=excluded.state,
            opened_at_ms=excluded.opened_at_ms,
            updated_at=CURRENT_TIMESTAMP"""
        self.enqueue_write(
            sql,
            (
                user_id,
                symbol,
                exchange_id,
                side,
                qty,
                entry_price,
                tp_order_id,
                sl_order_id,
                state,
                opened_at_ms,
            ),
        )

    def record_risk_event_async(self, level: str, category: str, symbol: str, message: str, user_id: int = 0):
        sql = "INSERT INTO risk_events (user_id, level, category, symbol, message) VALUES (?, ?, ?, ?, ?)"
        self.enqueue_write(sql, (user_id, level, category, symbol, message))

    def save_runtime_setting_sync(self, key: str, value: Any, user_id: int = 0):
        conn = self._connect()
        try:
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """INSERT INTO runtime_settings (user_id, key, value_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (user_id, key, json.dumps(value, ensure_ascii=False), ts_str),
            )
            conn.commit()
        finally:
            conn.close()

    def save_runtime_setting_async(self, key: str, value: Any, user_id: int = 0):
        """Hot-path safe: persists runtime state via the background SQLite batch writer thread."""
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        sql = """INSERT INTO runtime_settings (user_id, key, value_json, updated_at)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(user_id, key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at"""
        self.enqueue_write(sql, (user_id, key, json.dumps(value, ensure_ascii=False), ts_str))

    def cleanup_old_records_sync(self, missed_keep_hours: int = 48, risk_keep_days: int = 14) -> Dict[str, Any]:
        """
        Prunes stale MISSED order attempts and aged risk events while preserving 100% of
        FILLED/PARTIAL orders, trade_logs, configs, and runtime settings.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM orders WHERE status = 'MISSED' AND created_at < datetime('now', ?)",
                (f"-{max(1, int(missed_keep_hours))} hours",),
            )
            missed_deleted = cur.rowcount or 0
            cur.execute(
                "DELETE FROM risk_events WHERE created_at < datetime('now', ?)",
                (f"-{max(1, int(risk_keep_days))} days",),
            )
            risk_deleted = cur.rowcount or 0
            conn.commit()
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
            return {"missed_deleted": missed_deleted, "risk_deleted": risk_deleted}
        finally:
            conn.close()

    def load_runtime_setting_sync(self, key: str, default: Any = None, user_id: int = 0) -> Any:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value_json FROM runtime_settings WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
            if not row or not row[0]:
                return default
            return json.loads(row[0])
        except Exception:
            return default
        finally:
            conn.close()

    def list_persistent_user_ids_sync(self) -> List[int]:
        """Returns all user_ids (> 0) that have active runtime configs or configured exchange credentials."""
        conn = self._connect()
        try:
            uids = set()
            for r in conn.execute("SELECT DISTINCT user_id FROM runtime_settings WHERE user_id > 0").fetchall():
                uids.add(int(r[0]))
            for r in conn.execute(
                "SELECT DISTINCT user_id FROM exchanges WHERE user_id > 0 AND api_key != ''"
            ).fetchall():
                uids.add(int(r[0]))
            return sorted(uids)
        finally:
            conn.close()

    def get_user_live_summary_map_sync(self) -> Dict[int, Dict[str, Any]]:
        """Non-sensitive summary per user_id for Admin dashboard observability (never includes secrets)."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            summary: Dict[int, Dict[str, Any]] = {}
            for r in conn.execute(
                """SELECT user_id, exchange_id, role, auth_status, account_equity_usdt
                   FROM exchanges WHERE user_id > 0 AND role = 'follower' AND is_active = 1"""
            ).fetchall():
                uid = int(r["user_id"])
                auth_st = r["auth_status"] or "UNCONFIGURED"
                eq_u = float(r["account_equity_usdt"] or 0.0)
                summary[uid] = {
                    "follower_exchange": r["exchange_id"],
                    "auth_status": auth_st,
                    "follower_auth_status": auth_st,
                    "account_equity_usdt": eq_u,
                    "follower_equity_usdt": eq_u,
                    "symbols_count": 0,
                    "live_active": False,
                    "trading_active_live": False,
                    "live_trades_count": 0,
                    "live_net_pnl_usdt": 0.0,
                }
            for r in conn.execute(
                "SELECT user_id, COUNT(*) AS cnt FROM symbol_configs WHERE user_id > 0 AND is_running = 1 GROUP BY user_id"
            ).fetchall():
                uid = int(r["user_id"])
                summary.setdefault(uid, {})["symbols_count"] = int(r["cnt"])
            for r in conn.execute(
                "SELECT user_id, COUNT(*) AS cnt, COALESCE(SUM(net_pnl_usdt), 0.0) AS pnl FROM trade_logs WHERE user_id > 0 AND trading_mode = 'LIVE' GROUP BY user_id"
            ).fetchall():
                uid = int(r["user_id"])
                entry = summary.setdefault(uid, {})
                entry["live_trades_count"] = int(r["cnt"] or 0)
                entry["live_net_pnl_usdt"] = round(float(r["pnl"] or 0.0), 4)
            for r in conn.execute(
                "SELECT user_id, value_json FROM runtime_settings WHERE user_id > 0 AND key = 'trading_active_by_mode'"
            ).fetchall():
                uid = int(r["user_id"])
                try:
                    val = json.loads(r["value_json"])
                    is_live = bool(val.get("LIVE", False))
                    entry = summary.setdefault(uid, {})
                    entry["live_active"] = is_live
                    entry["trading_active_live"] = is_live
                except Exception:
                    pass
            return summary
        finally:
            conn.close()
