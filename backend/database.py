"""
SQLite Persistence Layer with Hot-Path/Cold-Path Separation (Section 五 & Section 3.4).
- Hot path pushes persistence events to an in-memory `asyncio.Queue` via `put_nowait`.
- A dedicated cold-path background worker batches and commits records via `asyncio.to_thread`.
- Includes full schema for `exchanges`, `symbol_configs`, `trade_logs` (with latency, beta,
  slippage, and snapshot extensions), `orders`, `positions`, and `risk_events`.
"""
import asyncio
import os
import sqlite3
import time
from typing import Any, Dict, List
from backend.crypto_utils import vault
from backend.models import ExchangeConfigModel, SymbolStrategy


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sniper_engine.db")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 1. 交易所连接配置表 (Section 五.1)
CREATE TABLE IF NOT EXISTS exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id VARCHAR(32) NOT NULL UNIQUE, -- 'binanceusdm', 'okx', 'krakenfutures', 'bybit', 'gate', 'bitget'
    display_name VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,               -- 'leader' 或 'follower'
    api_key TEXT DEFAULT '',
    api_secret_enc TEXT DEFAULT '',          -- AES-256-GCM 加密存储
    passphrase_enc TEXT DEFAULT '',          -- AES-256-GCM 加密存储
    vip_level VARCHAR(32) DEFAULT 'VIP 0',
    taker_fee DECIMAL(8, 6) DEFAULT 0.0005,  -- 默认万5
    maker_fee DECIMAL(8, 6) DEFAULT 0.0002,  -- 默认万2
    is_active BOOLEAN DEFAULT 1,
    auth_status VARCHAR(32) DEFAULT 'UNCONFIGURED',
    auth_message TEXT DEFAULT '未配置 API 密钥',
    account_equity_usdt DECIMAL(18, 4) DEFAULT 0.0,
    last_verified_at TEXT DEFAULT ''
);

-- 2. 监控币种与策略参数表 (Section 五.2 + Section 1.3/3.1 扩展字段)
CREATE TABLE IF NOT EXISTS symbol_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(32) NOT NULL UNIQUE,      -- 如 'XRP/USDT:USDT'
    direction VARCHAR(16) DEFAULT 'BOTH',    -- 'LONG_ONLY', 'SHORT_ONLY', 'BOTH'
    order_usdt DECIMAL(12, 2) DEFAULT 300,   -- 单笔下单额(U)
    leverage INTEGER DEFAULT 5,
    window_ms INTEGER DEFAULT 150,           -- 脉冲窗口(毫秒)
    min_jump_pct DECIMAL(6, 4) DEFAULT 0.0060,       -- 最小脉冲 0.60%
    adaptive_z_score DECIMAL(6, 2) DEFAULT 3.00,     -- 自适应波动率 Z-Score
    lag_confirm_max_pct DECIMAL(6, 4) DEFAULT 0.0010,-- Follower 滞后确认上限 0.10%
    take_profit_pct DECIMAL(6, 4) DEFAULT 0.0025,    -- 扣费后净止盈率 0.25%
    stop_loss_pct DECIMAL(6, 4) DEFAULT 0.0035,      -- 硬止损率 0.35%
    reversal_stop_ratio DECIMAL(6, 4) DEFAULT 0.5000,-- 信号反转回撤止损比例 50%
    trailing_stop_enabled BOOLEAN DEFAULT 1,
    trailing_retracement_pct DECIMAL(6, 4) DEFAULT 0.0008, -- 移动追踪止盈回撤 0.08%
    max_hold_ms INTEGER DEFAULT 1500,        -- 最大持仓毫秒数
    slippage_pct DECIMAL(6, 4) DEFAULT 0.0005,       -- IOC抢单容忍滑点 0.05%
    beta_coeff DECIMAL(6, 4) DEFAULT 0.8800,         -- 收敛系数 beta
    auto_beta_enabled BOOLEAN DEFAULT 1,
    require_volume_confirm BOOLEAN DEFAULT 1,
    use_post_only_tp BOOLEAN DEFAULT 1,
    max_signal_age_ms INTEGER DEFAULT 80,
    max_book_staleness_ms INTEGER DEFAULT 600,
    contract_size DECIMAL(12, 6) DEFAULT 1.0,
    min_qty DECIMAL(12, 6) DEFAULT 0.1,
    qty_precision INTEGER DEFAULT 2,
    price_precision INTEGER DEFAULT 4,
    is_running BOOLEAN DEFAULT 1,
    auto_disabled_reason TEXT DEFAULT NULL
);

-- 3. 狙击交易统计与复盘记录表 (Section 五.3 + Section 3.4 审查增补字段)
CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(32) NOT NULL,
    leader_ex VARCHAR(32) DEFAULT 'binanceusdm',
    follower_ex VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,                -- 'LONG' 或 'SHORT'
    trading_mode VARCHAR(16) DEFAULT 'SHADOW',
    order_id VARCHAR(64),
    exit_order_type VARCHAR(24) DEFAULT 'MAKER_POST_ONLY',
    leader_jump_pct DECIMAL(8, 6),           -- 触发时 Leader 涨跌幅
    follower_lag_pct DECIMAL(8, 6),          -- 触发时 Follower 窗口内变化率
    expected_net_pct DECIMAL(8, 6),          -- 触发时预期净利率
    actual_slippage_pct DECIMAL(8, 6),       -- 实际开仓滑点率
    beta_est DECIMAL(6, 4),                  -- 当时估计的收敛系数 beta
    leader_snapshot_price DECIMAL(18, 8),
    follower_snapshot_price DECIMAL(18, 8),
    reservation_price DECIMAL(18, 8),
    entry_price DECIMAL(18, 8),
    exit_price DECIMAL(18, 8),
    filled_qty DECIMAL(18, 8),
    gross_pnl_usdt DECIMAL(12, 4),           -- 毛利(U)
    fee_usdt DECIMAL(12, 4),                 -- 双边总手续费(U)
    net_pnl_usdt DECIMAL(12, 4),             -- 净利润(U)
    signal_ts_ms INTEGER,
    submit_ts_ms INTEGER,
    ack_ts_ms INTEGER,
    signal_to_fill_ms DECIMAL(8, 2),         -- 信号到成交总延迟(ms)
    hold_duration_ms INTEGER,                -- 持仓毫秒数
    exit_reason VARCHAR(32),                 -- 'TAKE_PROFIT', 'TRAILING_TP', 'TIMEOUT', 'REVERSAL_SL', 'HARD_SL', 'KILL_SWITCH'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 订单生命周期与对账表 (Section 3.4 新增)
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    exchange_id VARCHAR(32) NOT NULL,
    purpose VARCHAR(16) NOT NULL,            -- 'ENTRY_IOC', 'EXIT_TP_MAKER', 'EXIT_SL_COND', 'EXIT_IOC'
    side VARCHAR(8) NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    price DECIMAL(18, 8),
    requested_qty DECIMAL(18, 8),
    filled_qty DECIMAL(18, 8),
    avg_price DECIMAL(18, 8),
    status VARCHAR(24) NOT NULL,             -- 'SUBMITTED', 'FILLED', 'PARTIAL', 'CANCELED', 'REJECTED', 'RECONCILED'
    latency_ms DECIMAL(8, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. 持仓状态与孤儿仓位对账表 (Section 3.3 & 3.4 新增)
CREATE TABLE IF NOT EXISTS positions (
    symbol VARCHAR(32) PRIMARY KEY,
    exchange_id VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    qty DECIMAL(18, 8) NOT NULL,
    entry_price DECIMAL(18, 8) NOT NULL,
    tp_order_id VARCHAR(64),
    sl_order_id VARCHAR(64),
    state VARCHAR(24) NOT NULL,              -- 'HOLDING', 'EXITING', 'CLOSED', 'ORPHAN_RECOVERED'
    opened_at_ms INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. 风控与对账审计日志表 (Section 3.3 新增)
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level VARCHAR(16) NOT NULL,              -- 'INFO', 'WARN', 'CRITICAL'
    category VARCHAR(32) NOT NULL,           -- 'KILL_SWITCH', 'RECONCILE', 'AUTO_DISABLE', 'LATENCY_BREAKER', 'DAILY_LOSS'
    symbol VARCHAR(32) DEFAULT '',
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


DEFAULT_EXCHANGES = [
    ("binanceusdm", "Binance USDM (币安合约)", "leader", "VIP 1", 0.0004, 0.00018, 1),
    ("okx", "OKX Swap (欧易永续)", "follower", "VIP 1", 0.0005, 0.00020, 1),
    ("bybit", "Bybit Unified (Bybit永续)", "follower", "VIP 0", 0.00055, 0.00020, 0),
    ("gate", "Gate.io Futures (芝麻开门)", "follower", "VIP 2", 0.0005, 0.00015, 0),
    ("bitget", "Bitget Futures (Bitget合约)", "follower", "VIP 1", 0.0006, 0.00020, 0),
    ("krakenfutures", "Kraken Futures (Kraken合约)", "follower", "Tier 2", 0.0005, 0.00020, 0),
]

DEFAULT_SYMBOLS = [
    # symbol, direction, order_usdt, leverage, window_ms, min_jump_pct, tp_pct, sl_pct, max_hold_ms, slippage_pct, beta, qty_prec, price_prec
    ("XRP/USDT:USDT", "BOTH", 500.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.88, 1, 4),
    ("SOL/USDT:USDT", "BOTH", 500.0, 5, 150, 0.0060, 0.0025, 0.0035, 1500, 0.0005, 0.90, 2, 3),
    ("DOGE/USDT:USDT", "BOTH", 300.0, 5, 150, 0.0065, 0.0025, 0.0035, 1500, 0.0005, 0.86, 0, 5),
    ("PEPE/USDT:USDT", "BOTH", 300.0, 3, 180, 0.0075, 0.0030, 0.0040, 1800, 0.0006, 0.84, 0, 6),
]


class AsyncDatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._write_queue: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue(maxsize=10000)
        self._worker_task: asyncio.Task | None = None
        self._running = False

    def init_sync(self):
        """Initialize schema and default seed records synchronously on startup."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            cur = conn.cursor()
            # Ensure new auth verification columns exist in older DB files
            existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(exchanges)").fetchall()}
            if "auth_status" not in existing_cols:
                cur.execute("ALTER TABLE exchanges ADD COLUMN auth_status VARCHAR(32) DEFAULT 'UNCONFIGURED'")
            if "auth_message" not in existing_cols:
                cur.execute("ALTER TABLE exchanges ADD COLUMN auth_message TEXT DEFAULT '未配置 API 密钥'")
            if "account_equity_usdt" not in existing_cols:
                cur.execute("ALTER TABLE exchanges ADD COLUMN account_equity_usdt DECIMAL(18, 4) DEFAULT 0.0")
            if "last_verified_at" not in existing_cols:
                cur.execute("ALTER TABLE exchanges ADD COLUMN last_verified_at TEXT DEFAULT ''")

            cur.execute("SELECT COUNT(*) FROM exchanges")
            if cur.fetchone()[0] == 0:
                for ex_id, name, role, vip, taker, maker, active in DEFAULT_EXCHANGES:
                    cur.execute(
                        """INSERT INTO exchanges
                           (exchange_id, display_name, role, vip_level, taker_fee, maker_fee, is_active)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (ex_id, name, role, vip, taker, maker, active),
                    )
            cur.execute("SELECT COUNT(*) FROM symbol_configs")
            if cur.fetchone()[0] == 0:
                for row in DEFAULT_SYMBOLS:
                    cur.execute(
                        """INSERT INTO symbol_configs
                           (symbol, direction, order_usdt, leverage, window_ms, min_jump_pct,
                            take_profit_pct, stop_loss_pct, max_hold_ms, slippage_pct,
                            beta_coeff, qty_precision, price_precision, is_running)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                        row,
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
            await self._flush_remaining()
            self._worker_task.cancel()

    def enqueue_write(self, sql: str, params: tuple = ()):
        """Hot-path safe: non-blocking enqueue for SQLite persistence."""
        try:
            self._write_queue.put_nowait((sql, params))
        except asyncio.QueueFull:
            pass

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
            except Exception:
                await asyncio.sleep(0.1)

    async def _flush_remaining(self):
        batch = []
        while not self._write_queue.empty():
            batch.append(self._write_queue.get_nowait())
        if batch:
            await asyncio.to_thread(self._execute_batch_sync, batch)

    def _execute_batch_sync(self, batch: List[tuple[str, tuple]]):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            for sql, params in batch:
                cur.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    # ==================== Synchronous / Thread-Pool Query Helpers ====================
    def load_exchanges_sync(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM exchanges ORDER BY id ASC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                ex_id = d.get("exchange_id", "")
                raw_key = (d.get("api_key") or "").strip()
                dec_secret = vault.decrypt(d.get("api_secret_enc", "")).strip()
                dec_pass = vault.decrypt(d.get("passphrase_enc", "")).strip()
                needs_passphrase = ex_id in ("okx", "bitget")

                has_key = bool(raw_key)
                has_sec = bool(dec_secret)
                has_pass = bool(dec_pass)
                has_any = has_key or has_sec or has_pass
                has_all = has_key and has_sec and (has_pass if needs_passphrase else True)

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

                # Derive clear diagnostic status if incomplete or unconfigured
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
                    if needs_passphrase and not has_pass:
                        missing.append("API 口令 (Passphrase)")
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
        self, exchange_id: str, auth_status: str, auth_message: str, account_equity_usdt: float = 0.0
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            ts_str = time.strftime("%H:%M:%S")
            conn.execute(
                """UPDATE exchanges
                   SET auth_status=?, auth_message=?, account_equity_usdt=?, last_verified_at=?
                   WHERE exchange_id=?""",
                (auth_status, auth_message, round(account_equity_usdt, 4), ts_str, exchange_id),
            )
            conn.commit()
        finally:
            conn.close()

    def clear_exchange_credentials_sync(self, exchange_id: str):
        """Permanently wipes saved API Key, encrypted Secret, Passphrase, and verification state."""
        conn = sqlite3.connect(self.db_path)
        try:
            if exchange_id == "ALL":
                conn.execute(
                    """UPDATE exchanges
                       SET api_key='', api_secret_enc='', passphrase_enc='',
                           auth_status='UNCONFIGURED', auth_message='未配置 API 密钥',
                           account_equity_usdt=0.0, last_verified_at=''"""
                )
            else:
                conn.execute(
                    """UPDATE exchanges
                       SET api_key='', api_secret_enc='', passphrase_enc='',
                           auth_status='UNCONFIGURED', auth_message='未配置 API 密钥',
                           account_equity_usdt=0.0, last_verified_at=''
                       WHERE exchange_id=?""",
                    (exchange_id,),
                )
            conn.commit()
        finally:
            conn.close()

    def save_exchange_sync(self, cfg: ExchangeConfigModel):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT api_key, api_secret_enc, passphrase_enc FROM exchanges WHERE exchange_id = ?",
                (cfg.exchange_id,),
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
                        "UPDATE exchanges SET role = 'follower' WHERE role = 'leader' AND exchange_id != ?",
                        (cfg.exchange_id,),
                    )
                cur.execute(
                    """UPDATE exchanges
                       SET display_name=?, role=?, api_key=?, api_secret_enc=?, passphrase_enc=?,
                           vip_level=?, taker_fee=?, maker_fee=?, is_active=?
                       WHERE exchange_id=?""",
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
                        cfg.exchange_id,
                    ),
                )
            else:
                secret_enc = vault.encrypt(in_sec)
                pass_enc = vault.encrypt(in_pass)
                cur.execute(
                    """INSERT INTO exchanges
                       (exchange_id, display_name, role, api_key, api_secret_enc, passphrase_enc,
                        vip_level, taker_fee, maker_fee, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
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

    def load_symbols_sync(self) -> List[SymbolStrategy]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM symbol_configs ORDER BY id ASC").fetchall()
            strategies = []
            for r in rows:
                d = dict(r)
                d["trailing_stop_enabled"] = bool(d.get("trailing_stop_enabled", 1))
                d["auto_beta_enabled"] = bool(d.get("auto_beta_enabled", 1))
                d["require_volume_confirm"] = bool(d.get("require_volume_confirm", 1))
                d["use_post_only_tp"] = bool(d.get("use_post_only_tp", 1))
                d["is_running"] = bool(d.get("is_running", 1))
                strategies.append(SymbolStrategy(**d))
            return strategies
        finally:
            conn.close()

    def upsert_symbol_sync(self, cfg: SymbolStrategy):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO symbol_configs (
                    symbol, direction, order_usdt, leverage, window_ms, min_jump_pct,
                    adaptive_z_score, lag_confirm_max_pct, take_profit_pct, stop_loss_pct,
                    reversal_stop_ratio, trailing_stop_enabled, trailing_retracement_pct,
                    max_hold_ms, slippage_pct, beta_coeff, auto_beta_enabled,
                    require_volume_confirm, use_post_only_tp, max_signal_age_ms,
                    max_book_staleness_ms, contract_size, min_qty, qty_precision,
                    price_precision, is_running, auto_disabled_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
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
                    max_signal_age_ms=excluded.max_signal_age_ms,
                    max_book_staleness_ms=excluded.max_book_staleness_ms,
                    contract_size=excluded.contract_size,
                    min_qty=excluded.min_qty,
                    qty_precision=excluded.qty_precision,
                    price_precision=excluded.price_precision,
                    is_running=excluded.is_running,
                    auto_disabled_reason=excluded.auto_disabled_reason
                """,
                (
                    cfg.symbol,
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
                    cfg.max_signal_age_ms,
                    cfg.max_book_staleness_ms,
                    cfg.contract_size,
                    cfg.min_qty,
                    cfg.qty_precision,
                    cfg.price_precision,
                    1 if cfg.is_running else 0,
                    cfg.auto_disabled_reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_symbol_sync(self, symbol: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM symbol_configs WHERE symbol = ?", (symbol,))
            conn.commit()
        finally:
            conn.close()

    def load_recent_trades_sync(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trade_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_open_positions_sync(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM positions WHERE state IN ('HOLDING', 'EXITING')"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_risk_events_sync(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM risk_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def record_trade_async(self, trade: Dict[str, Any]):
        sql = """INSERT INTO trade_logs (
            symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type,
            leader_jump_pct, follower_lag_pct, expected_net_pct, actual_slippage_pct,
            beta_est, leader_snapshot_price, follower_snapshot_price, reservation_price,
            entry_price, exit_price, filled_qty, gross_pnl_usdt, fee_usdt, net_pnl_usdt,
            signal_ts_ms, submit_ts_ms, ack_ts_ms, signal_to_fill_ms, hold_duration_ms, exit_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            trade["symbol"],
            trade.get("leader_ex", "binanceusdm"),
            trade.get("follower_ex", "okx"),
            trade["side"],
            trade.get("trading_mode", "SHADOW"),
            trade.get("order_id", ""),
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
    ):
        sql = """INSERT OR REPLACE INTO orders (
            order_id, symbol, exchange_id, purpose, side, order_type,
            price, requested_qty, filled_qty, avg_price, status, latency_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        self.enqueue_write(
            sql,
            (
                order_id,
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
    ):
        sql = """INSERT INTO positions (
            symbol, exchange_id, side, qty, entry_price, tp_order_id, sl_order_id, state, opened_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
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

    def record_risk_event_async(self, level: str, category: str, symbol: str, message: str):
        sql = "INSERT INTO risk_events (level, category, symbol, message) VALUES (?, ?, ?, ?)"
        self.enqueue_write(sql, (level, category, symbol, message))

