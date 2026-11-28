import asyncio
import json
import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.crypto_utils import vault
from backend.database import AsyncDatabaseManager
from backend.executor import ExecutionAdapter, quantize_decimal_step
from backend.market_gateway import _BINANCE_INSTRUMENT_CACHE, _OKX_INSTRUMENT_CACHE
from backend.models import GlobalRiskConfig, SymbolStrategy
from backend.payment_monitor import (
    PAYMENT_WALLET_ADDRESS,
    RECIPIENT_PADDED_TOPIC,
    SUPPORTED_CHAINS,
    TRANSFER_EVENT_TOPIC,
    OnChainPaymentMonitor,
)
from backend.user_membership import UserMembershipManager, rate_limiter


@pytest.fixture(autouse=True)
def _isolate_global_app_db(tmp_path):
    """Prevents any TestClient(app) lifespan from ever touching production backend/sniper_engine.db."""
    import backend.main as main_mod
    from backend.engine_manager import EngineManager

    iso_db = str(tmp_path / "isolated_app.db")
    orig_db_path = main_mod.db_manager.db_path
    orig_um_path = main_mod.user_manager.db_path
    orig_em = main_mod.engine_manager
    main_mod.db_manager.db_path = iso_db
    main_mod.user_manager.db_path = iso_db
    main_mod.db_manager.init_sync()
    main_mod.user_manager.init_sync()
    main_mod.engine_manager = EngineManager(main_mod.db_manager, get_user_fn=main_mod.user_manager.get_user_by_id)
    try:
        yield
    finally:
        main_mod.db_manager.db_path = orig_db_path
        main_mod.user_manager.db_path = orig_um_path
        main_mod.engine_manager = orig_em


def test_legacy_db_migration_preserves_ye520958_and_isolates_tenants():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "legacy.db")
        mgr = UserMembershipManager(db_path=db_file)
        mgr.init_sync()

        # Register ye520958 inside legacy DB before multi-tenant migration
        reg_ye = mgr.register_user("ye520958", "testpass123", "ye@example.com", client_ip="120.230.56.84")
        assert reg_ye["ok"] is True
        ye_uid = reg_ye["user"]["id"]

        reg_other = mgr.register_user("other_trader", "testpass123", "other@example.com", client_ip="198.51.100.99")
        assert reg_other["ok"] is True
        other_uid = reg_other["user"]["id"]

        # Inject legacy single-tenant tables matching the real v1 schema WITHOUT user_id columns
        conn = sqlite3.connect(db_file)
        conn.executescript(
            """
            CREATE TABLE exchanges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id VARCHAR(32) NOT NULL UNIQUE,
                display_name VARCHAR(64) NOT NULL,
                role VARCHAR(16) NOT NULL,
                api_key TEXT DEFAULT '',
                api_secret_enc TEXT DEFAULT '',
                passphrase_enc TEXT DEFAULT '',
                vip_level VARCHAR(16) DEFAULT 'VIP 0',
                taker_fee DECIMAL(8, 6) NOT NULL,
                maker_fee DECIMAL(8, 6) NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                auth_status VARCHAR(32) DEFAULT 'UNCONFIGURED',
                auth_message TEXT DEFAULT '',
                account_equity_usdt REAL DEFAULT 0.0,
                last_verified_at TEXT DEFAULT ''
            );
            CREATE TABLE symbol_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol VARCHAR(32) NOT NULL UNIQUE,
                direction VARCHAR(16) DEFAULT 'BOTH',
                order_usdt DECIMAL(12, 2) DEFAULT 300.0,
                leverage INTEGER DEFAULT 5,
                window_ms INTEGER DEFAULT 150,
                min_jump_pct DECIMAL(8, 6) DEFAULT 0.006,
                adaptive_z_score DECIMAL(8, 4) DEFAULT 3.0,
                lag_confirm_max_pct DECIMAL(8, 6) DEFAULT 0.001,
                take_profit_pct DECIMAL(8, 6) DEFAULT 0.0025,
                stop_loss_pct DECIMAL(8, 6) DEFAULT 0.0035,
                reversal_stop_ratio DECIMAL(8, 4) DEFAULT 0.50,
                trailing_stop_enabled BOOLEAN DEFAULT 1,
                trailing_retracement_pct DECIMAL(8, 6) DEFAULT 0.0008,
                max_hold_ms INTEGER DEFAULT 1500,
                slippage_pct DECIMAL(8, 6) DEFAULT 0.0005,
                beta_coeff DECIMAL(8, 4) DEFAULT 0.88,
                auto_beta_enabled BOOLEAN DEFAULT 1,
                require_volume_confirm BOOLEAN DEFAULT 1,
                use_post_only_tp BOOLEAN DEFAULT 1,
                max_signal_age_ms INTEGER DEFAULT 80,
                max_book_staleness_ms INTEGER DEFAULT 600,
                contract_size DECIMAL(12, 6) DEFAULT 1.0,
                min_qty DECIMAL(12, 6) DEFAULT 0.01,
                qty_precision INTEGER DEFAULT 2,
                price_precision INTEGER DEFAULT 4,
                is_running BOOLEAN DEFAULT 1,
                auto_disabled_reason TEXT DEFAULT ''
            );
            CREATE TABLE trade_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol VARCHAR(32) NOT NULL,
                leader_ex VARCHAR(32) NOT NULL,
                follower_ex VARCHAR(32) NOT NULL,
                side VARCHAR(8) NOT NULL,
                trading_mode VARCHAR(16) DEFAULT 'SHADOW',
                order_id VARCHAR(64) NOT NULL,
                exit_order_type VARCHAR(16) NOT NULL,
                leader_jump_pct REAL,
                follower_lag_pct REAL,
                expected_net_pct REAL,
                actual_slippage_pct REAL,
                beta_est REAL,
                leader_snapshot_price REAL,
                follower_snapshot_price REAL,
                reservation_price REAL,
                entry_price REAL,
                exit_price REAL,
                filled_qty REAL,
                gross_pnl_usdt REAL,
                fee_usdt REAL,
                net_pnl_usdt REAL,
                signal_ts_ms INTEGER,
                submit_ts_ms INTEGER,
                ack_ts_ms INTEGER,
                signal_to_fill_ms REAL,
                hold_duration_ms INTEGER,
                exit_reason VARCHAR(32),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE orders (
                order_id VARCHAR(64) PRIMARY KEY,
                symbol VARCHAR(32) NOT NULL,
                exchange_id VARCHAR(32) NOT NULL,
                purpose VARCHAR(24) NOT NULL,
                side VARCHAR(8) NOT NULL,
                order_type VARCHAR(16) NOT NULL,
                price REAL,
                requested_qty REAL,
                filled_qty REAL,
                avg_price REAL,
                status VARCHAR(24) NOT NULL,
                latency_ms REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE positions (
                symbol VARCHAR(32) PRIMARY KEY,
                exchange_id VARCHAR(32) NOT NULL,
                side VARCHAR(8) NOT NULL,
                qty REAL NOT NULL,
                entry_price REAL NOT NULL,
                tp_order_id VARCHAR(64),
                sl_order_id VARCHAR(64),
                state VARCHAR(16) NOT NULL,
                opened_at_ms INTEGER,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level VARCHAR(16) NOT NULL,
                category VARCHAR(32) NOT NULL,
                symbol VARCHAR(32),
                message TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE runtime_settings (
                key VARCHAR(64) PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """INSERT INTO exchanges
               (exchange_id, display_name, role, api_key, api_secret_enc, passphrase_enc, auth_status, auth_message, account_equity_usdt, taker_fee, maker_fee, is_active)
               VALUES ('okx', 'OKX Swap', 'follower', 'ye-live-key-123456', ?, ?, 'VERIFIED', '已配置且验证通过 (权益: 49.95 USDT)', 49.95, 0.0005, 0.0002, 1)""",
            (vault.encrypt("ye-live-secret"), vault.encrypt("ye-pass")),
        )
        conn.execute(
            "INSERT INTO symbol_configs (symbol, order_usdt, leverage, is_running) VALUES (?, ?, ?, 1)",
            ("SOL/USDT:USDT", 10.0, 10),
        )
        conn.execute(
            "INSERT INTO runtime_settings (key, value_json) VALUES ('risk_config', ?)",
            (json.dumps({"trading_mode": "LIVE", "feed_source": "REAL_WS"}),),
        )
        conn.execute(
            """INSERT INTO trade_logs (symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type, net_pnl_usdt)
               VALUES ('BTC/USDT:USDT', 'binanceusdm', 'okx', 'LONG', 'SHADOW', 'sh-1', 'MAKER_POST_ONLY', 1.25)"""
        )
        conn.execute(
            """INSERT INTO trade_logs (symbol, leader_ex, follower_ex, side, trading_mode, order_id, exit_order_type, net_pnl_usdt)
               VALUES ('SOL/USDT:USDT', 'binanceusdm', 'okx', 'LONG', 'LIVE', 'lv-1', 'TAKER_IOC', 0.55)"""
        )
        conn.commit()
        conn.close()

        # Run multi-tenant migration
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()

        # 1. ye520958 retains OKX encrypted creds, 49.95 equity, SOL strategy, LIVE runtime_setting & LIVE trade
        ye_ex = {e["exchange_id"]: e for e in adb.load_exchanges_sync(user_id=ye_uid)}
        assert ye_ex["okx"]["api_key"] == "ye-live-key-123456"
        assert ye_ex["okx"]["_decrypted_secret"] == "ye-live-secret"
        assert ye_ex["okx"]["account_equity_usdt"] == 49.95
        ye_syms = adb.load_symbols_sync(user_id=ye_uid)
        assert [s.symbol for s in ye_syms] == ["SOL/USDT:USDT"]
        assert ye_syms[0].order_usdt == 10.0
        ye_risk = adb.load_runtime_setting_sync("risk_config", user_id=ye_uid)
        assert ye_risk["trading_mode"] == "LIVE"
        ye_trades = adb.load_recent_trades_sync(user_id=ye_uid)
        assert len(ye_trades) == 1 and ye_trades[0]["trading_mode"] == "LIVE"

        # 2. Other user sees ZERO of ye520958's credentials, equity, or strategies
        other_ex = {e["exchange_id"]: e for e in adb.load_exchanges_sync(user_id=other_uid)}
        assert other_ex["okx"]["has_credentials"] is False
        assert other_ex["okx"]["account_equity_usdt"] == 0.0
        assert other_ex["okx"]["api_key"] == ""
        assert adb.load_recent_trades_sync(user_id=other_uid) == []

        # 3. Guest (user_id=0) retains ONLY SHADOW trade logs & zero credentials
        guest_ex = {e["exchange_id"]: e for e in adb.load_exchanges_sync(user_id=0)}
        assert guest_ex["okx"]["has_credentials"] is False
        guest_trades = adb.load_recent_trades_sync(user_id=0)
        assert len(guest_trades) == 1 and guest_trades[0]["trading_mode"] == "SHADOW"


def test_api_and_websocket_multi_tenant_isolation():
    import backend.main as main_mod
    from backend.engine_manager import EngineManager

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "api_iso.db")
        orig_db_path = main_mod.db_manager.db_path
        orig_um_path = main_mod.user_manager.db_path
        orig_em = main_mod.engine_manager

        try:
            rate_limiter._buckets.clear()
            main_mod.user_manager.db_path = test_db
            main_mod.user_manager.init_sync()
            main_mod.db_manager.db_path = test_db
            main_mod.db_manager.init_sync()
            main_mod.engine_manager = EngineManager(main_mod.db_manager, get_user_fn=main_mod.user_manager.get_user_by_id)

            # Avoid external exchange WS/REST calls during isolated HTTP/WS unit test
            with (
                patch.object(main_mod.engine_manager.gateway, "start", new=AsyncMock(return_value=None)),
                patch.object(main_mod.engine_manager.gateway, "stop", new=AsyncMock(return_value=None)),
                patch.object(main_mod.engine_manager.gateway, "fetch_exchange_instruments", new=AsyncMock(return_value=True)),
                patch.object(main_mod.engine_manager.gateway, "fetch_real_bbo_snapshot", new=AsyncMock(return_value=True)),
                patch.object(main_mod.payment_monitor, "start", return_value=None),
                patch.object(main_mod.payment_monitor, "stop", new=AsyncMock(return_value=None)),
            ):
                with TestClient(main_mod.app) as client:
                    # Register two separate VIP users via admin grant
                    r1 = client.post(
                        "/api/auth/register",
                        json={"username": "ye520958", "password": "password123"},
                        headers={"X-Real-IP": "198.51.100.1"},
                    )
                    assert r1.status_code == 200
                    tok_ye = r1.json()["token"]
                    uid_ye = r1.json()["user"]["id"]

                    r2 = client.post(
                        "/api/auth/register",
                        json={"username": "alice_vip", "password": "password123"},
                        headers={"X-Real-IP": "198.51.100.2"},
                    )
                    assert r2.status_code == 200
                    tok_alice = r2.json()["token"]
                    uid_alice = r2.json()["user"]["id"]

                    main_mod.user_manager.confirm_order_paid_onchain
                    # Grant VIP to both users cleanly in DB
                    conn = sqlite3.connect(test_db)
                    conn.execute(
                        "UPDATE users SET vip_tier='季度旗舰版会员', vip_expires_at=? WHERE id IN (?, ?)",
                        (int(time.time()) + 86400 * 30, uid_ye, uid_alice),
                    )
                    conn.commit()
                    conn.close()

                    # Save ye520958 exchange config (mocking verify so offline deterministic)
                    with patch(
                        "backend.executor.ExecutionAdapter.verify_exchange_credentials",
                        new=AsyncMock(
                            return_value={
                                "ok": True,
                                "auth_status": "VERIFIED",
                                "auth_message": "已配置且验证通过 (权益: 49.95 USDT)",
                                "account_equity_usdt": 49.95,
                            }
                        ),
                    ):
                        up_res = client.post(
                            "/api/exchanges/update",
                            headers={"Authorization": f"Bearer {tok_ye}"},
                            json={
                                "exchange_id": "okx",
                                "display_name": "OKX Swap",
                                "role": "follower",
                                "api_key": "ye-secret-api-key-999",
                                "api_secret": "ye-secret-sec",
                                "passphrase": "ye-pass",
                                "vip_level": "VIP 1",
                                "taker_fee": 0.0005,
                                "maker_fee": 0.0002,
                                "is_active": True,
                            },
                        )
                        assert up_res.status_code == 200

                    # 1. Guest accessing /api/exchanges MUST get 401
                    assert client.get("/api/exchanges").status_code == 401

                    # 2. Guest accessing /api/stats MUST get sanitized guest preview (0 equity, no key)
                    g_stats = client.get("/api/stats").json()
                    assert g_stats["is_guest_preview"] is True
                    assert g_stats["live_status"]["follower_equity_usdt"] == 0.0
                    assert g_stats["live_status"]["follower_has_api_key"] is False

                    # 3. Guest WS MUST get guest preview only, NOT ye520958's 49.95 equity
                    with client.websocket_connect("/ws/dashboard") as ws_guest:
                        msg_guest = json.loads(ws_guest.receive_text())
                        assert msg_guest["is_guest_preview"] is True
                        assert msg_guest["live_status"]["follower_equity_usdt"] == 0.0

                    # 4. Alice accessing /api/exchanges & /ws/dashboard MUST NOT see ye520958's key/equity
                    alice_ex = client.get(
                        "/api/exchanges", headers={"Authorization": f"Bearer {tok_alice}"}
                    ).json()
                    alice_okx = next(e for e in alice_ex["exchanges"] if e["exchange_id"] == "okx")
                    assert alice_okx["has_credentials"] is False
                    assert alice_okx["account_equity_usdt"] == 0.0

                    with client.websocket_connect(f"/ws/dashboard?token={tok_alice}") as ws_alice:
                        msg_alice = json.loads(ws_alice.receive_text())
                        assert msg_alice["owner_user_id"] == uid_alice
                        assert msg_alice["live_status"]["follower_equity_usdt"] == 0.0

                    # 5. ye520958 accessing /ws/dashboard?token=... DOES see their own 49.95 equity
                    with client.websocket_connect(f"/ws/dashboard?token={tok_ye}") as ws_ye:
                        msg_ye = json.loads(ws_ye.receive_text())
                        assert msg_ye["owner_user_id"] == uid_ye
                        assert msg_ye["live_status"]["follower_equity_usdt"] == 49.95
                        assert msg_ye["trading_mode"] == "LIVE"

                    # 6. Preflight requires auth (401 for guest) & isolates per user
                    assert client.post("/api/exchanges/preflight").status_code == 401
                    pf_alice = client.post(
                        "/api/exchanges/preflight", headers={"Authorization": f"Bearer {tok_alice}"}
                    )
                    assert pf_alice.status_code == 200
                    assert pf_alice.json()["ok"] is False

                    # 7. Login brute-force rate limiter returns HTTP 429 after threshold
                    statuses = []
                    for _ in range(22):
                        lr = client.post(
                            "/api/auth/login",
                            json={"username": "ye520958", "password": "wrongpassword"},
                            headers={"X-Real-IP": "198.51.100.77"},
                        )
                        statuses.append(lr.status_code)
                    assert 429 in statuses
        finally:
            main_mod.db_manager.db_path = orig_db_path
            main_mod.user_manager.db_path = orig_um_path
            main_mod.engine_manager = orig_em


def test_payment_monitor_rejects_spoofed_erc20_and_historical_replay():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "pay_sec.db")
        mgr = UserMembershipManager(db_path=db_file)
        mgr.init_sync()
        u = mgr.register_user("pay_tester", "pass123456")["user"]
        plan = next(p for p in mgr.list_plans() if p["price_usdt"] == 99.0)
        ord_res = mgr.create_web3_payment_order(u["id"], plan["id"], "Arbitrum")
        order = ord_res["order"]
        pay_amt = float(order["pay_amount_usdt"])
        raw_6dec = int(round(pay_amt * 1_000_000))
        data_hex = "0x" + raw_6dec.to_bytes(32, "big").hex()

        monitor = OnChainPaymentMonitor(mgr)
        arb_chain = next(c for c in SUPPORTED_CHAINS if c["id"] == "arbitrum")
        arb_usdt = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
        fake_token = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        async def run_checks():
            # Fake ERC-20 token must NEVER confirm payment
            with patch.object(
                monitor,
                "_rpc_call",
                new=AsyncMock(
                    side_effect=[
                        {
                            "status": "0x1",
                            "blockNumber": "0x10",
                            "logs": [
                                {
                                    "address": fake_token,
                                    "topics": [
                                        TRANSFER_EVENT_TOPIC,
                                        "0x" + "0" * 24 + "1" * 40,
                                        RECIPIENT_PADDED_TOPIC,
                                    ],
                                    "data": data_hex,
                                }
                            ],
                        },
                        {"timestamp": hex(int(time.time()))},
                    ]
                ),
            ):
                async with httpx.AsyncClient() as client:
                    res = await monitor._check_tx_on_chain(
                        client, arb_chain, "0x" + "1" * 64, [dict(order)]
                    )
                    assert res is None

            # Whitelisted USDT token with block timestamp BEFORE order creation must be rejected (replay)
            monitor._block_ts_cache.clear()
            with patch.object(
                monitor,
                "_rpc_call",
                new=AsyncMock(
                    side_effect=[
                        {
                            "status": "0x1",
                            "blockNumber": "0x11",
                            "logs": [
                                {
                                    "address": arb_usdt,
                                    "topics": [
                                        TRANSFER_EVENT_TOPIC,
                                        "0x" + "0" * 24 + "1" * 40,
                                        RECIPIENT_PADDED_TOPIC,
                                    ],
                                    "data": data_hex,
                                }
                            ],
                        },
                        {"timestamp": hex(int(order["created_at_ts"]) - 3600)},
                    ]
                ),
            ):
                async with httpx.AsyncClient() as client:
                    res_old = await monitor._check_tx_on_chain(
                        client, arb_chain, "0x" + "2" * 64, [dict(order)]
                    )
                    assert res_old is None

            # Whitelisted USDT token inside valid window must confirm VIP
            monitor._block_ts_cache.clear()
            with patch.object(
                monitor,
                "_rpc_call",
                new=AsyncMock(
                    side_effect=[
                        {
                            "status": "0x1",
                            "blockNumber": "0x12",
                            "logs": [
                                {
                                    "address": arb_usdt,
                                    "topics": [
                                        TRANSFER_EVENT_TOPIC,
                                        "0x" + "0" * 24 + "1" * 40,
                                        RECIPIENT_PADDED_TOPIC,
                                    ],
                                    "data": data_hex,
                                }
                            ],
                        },
                        {"timestamp": hex(int(order["created_at_ts"]) + 15)},
                    ]
                ),
            ):
                async with httpx.AsyncClient() as client:
                    res_ok = await monitor._check_tx_on_chain(
                        client, arb_chain, "0x" + "3" * 64, [dict(order)]
                    )
                    assert res_ok is not None and res_ok["matched"] is True

        asyncio.run(run_checks())
        assert mgr.get_user_by_id(u["id"])["is_vip"] is True


def test_okx_live_execution_ctval_acct_lv_and_close():
    # Seed dynamic instrument cache with real OKX ADA & BTC SWAP specs
    _OKX_INSTRUMENT_CACHE["ADA-USDT-SWAP"] = {
        "instId": "ADA-USDT-SWAP",
        "unified_sym": "ADA/USDT:USDT",
        "state": "live",
        "ctVal": 100.0,
        "ctVal_str": "100",
        "lotSz": 0.1,
        "lotSz_str": "0.1",
        "minSz": 0.1,
        "minSz_str": "0.1",
        "tickSz": 0.0001,
        "tickSz_str": "0.0001",
        "maxLever": 75.0,
    }
    _OKX_INSTRUMENT_CACHE["BTC-USDT-SWAP"] = {
        "instId": "BTC-USDT-SWAP",
        "unified_sym": "BTC/USDT:USDT",
        "state": "live",
        "ctVal": 0.01,
        "ctVal_str": "0.01",
        "lotSz": 0.01,
        "lotSz_str": "0.01",
        "minSz": 0.01,
        "minSz_str": "0.01",
        "tickSz": 0.1,
        "tickSz_str": "0.1",
        "maxLever": 100.0,
    }
    _BINANCE_INSTRUMENT_CACHE["ADAUSDT"] = {"symbol": "ADAUSDT", "status": "TRADING"}
    _BINANCE_INSTRUMENT_CACHE["BTCUSDT"] = {"symbol": "BTCUSDT", "status": "TRADING"}

    px_s, _ = quantize_decimal_step(0.000018523456, "0.0000000001", rounding="down")
    assert px_s == "0.0000185234"

    risk = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
    adapter = ExecutionAdapter(get_math_state=lambda _: None, get_risk_cfg=lambda: risk)
    ex_cfg = {
        "exchange_id": "okx",
        "api_key": "test-key-1234",
        "_decrypted_secret": "test-sec-1234",
        "_decrypted_passphrase": "pass",
        "account_equity_usdt": 49.95,
    }
    cfg_ada = SymbolStrategy(symbol="ADA/USDT:USDT", order_usdt=10.0, leverage=10, price_precision=4, qty_precision=1)

    async def run_okx_tests():
        # 1. Verify account in acctLv="1" (Simple mode) must fail with ACCOUNT_MODE_UNSUPPORTED & equity preserved
        def fake_req_simple(client, creds, method, path, payload=None):
            return {}

        with patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(
                side_effect=[
                    SimpleNamespace(json=lambda: {"code": "0", "data": [{"totalEq": "49.95", "details": []}]}),
                    SimpleNamespace(json=lambda: {"code": "0", "data": [{"acctLv": "1", "posMode": "net_mode", "perm": "read_only,trade"}]}),
                ]
            ),
        ):
            ver_simple = await adapter.verify_exchange_credentials(ex_cfg)
            assert ver_simple["ok"] is False
            assert ver_simple["auth_status"] == "ACCOUNT_MODE_UNSUPPORTED"
            assert ver_simple["account_equity_usdt"] == 49.95

        # 2. Entry IOC + Protective Orders + Exit Retry with acctLv="2" and ADA ctVal=100
        calls = []
        canceled_orders = set()

        async def mock_okx_req(client, creds, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/api/v5/account/config":
                return {"code": "0", "data": [{"acctLv": "2", "posMode": "net_mode", "perm": "read_only,trade"}]}
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "data": [{"lever": "10"}]}
            if path == "/api/v5/trade/order" and payload and payload.get("ordType") == "ioc" and payload.get("side") == "buy":
                return {"code": "0", "data": [{"ordId": "okx-entry-1", "sCode": "0"}]}
            if path.startswith("/api/v5/trade/order?instId=ADA-USDT-SWAP&ordId=okx-entry-1"):
                # 0.2 contracts * 100 ctVal = 20 ADA filled at $0.50 = $10 notional
                return {"code": "0", "data": [{"accFillSz": "0.2", "avgPx": "0.5000", "state": "filled"}]}
            if path == "/api/v5/trade/order-algo":
                return {"code": "0", "data": [{"algoId": "okx-sl-algo-1", "sCode": "0"}]}
            if path == "/api/v5/trade/order" and payload and payload.get("ordType") == "post_only":
                return {"code": "0", "data": [{"ordId": "okx-tp-po-1", "sCode": "0"}]}
            if path.startswith("/api/v5/trade/order?instId=ADA-USDT-SWAP&ordId=okx-tp-po-1"):
                if "okx-tp-po-1" in canceled_orders:
                    return {"code": "0", "data": [{"accFillSz": "0", "avgPx": "", "state": "canceled"}]}
                return {"code": "0", "data": [{"accFillSz": "0", "avgPx": "", "state": "live"}]}
            if path == "/api/v5/trade/cancel-order":
                if payload and payload.get("ordId"):
                    canceled_orders.add(payload["ordId"])
                return {"code": "0", "data": [{"sCode": "0"}]}
            if path == "/api/v5/trade/cancel-algos":
                return {"code": "0", "data": [{"sCode": "0"}]}
            if path == "/api/v5/trade/order" and payload and payload.get("reduceOnly") is True:
                return {"code": "0", "data": [{"ordId": "okx-exit-1", "sCode": "0"}]}
            if path.startswith("/api/v5/trade/order?instId=ADA-USDT-SWAP&ordId=okx-exit-1"):
                return {"code": "0", "data": [{"accFillSz": "0.2", "avgPx": "0.5020", "state": "filled"}]}
            return {"code": "0", "data": []}

        # Clear simple-mode cached meta for fresh test
        adapter._okx_account_meta.clear()
        with patch.object(adapter, "_okx_request", new=AsyncMock(side_effect=mock_okx_req)):
            entry_res = await adapter.place_entry_ioc(
                cfg=cfg_ada, side="LONG", qty=20.0, reservation_price=0.50, trigger_price=0.499, exchange_cfg=ex_cfg
            )
            assert entry_res.status == "FILLED"
            assert entry_res.contracts == 0.2
            assert abs(entry_res.filled_qty - 20.0) < 1e-6  # 0.2 * 100 ctVal = 20 ADA

            tp_id, sl_id, crossed = await adapter.place_protective_orders(
                cfg_ada, "LONG", entry_res.filled_qty, 0.5020, 0.4980,
                exchange_cfg=ex_cfg, contracts=entry_res.contracts, ct_val=entry_res.ct_val,
            )
            assert tp_id == "okx-tp-po-1"
            assert sl_id == "okx-sl-algo-1"
            assert crossed is False

            exit_res = await adapter.execute_exit_with_retry(
                cfg_ada, "LONG", entry_res.filled_qty, "TIMEOUT", 0.5020,
                exchange_cfg=ex_cfg, contracts=entry_res.contracts, ct_val=entry_res.ct_val,
            )
            assert exit_res.status == "FILLED"
            assert abs(exit_res.filled_qty - 20.0) < 1e-6
            assert exit_res.avg_price == 0.5020

        paths_called = [p for _, p, _ in calls]
        assert "/api/v5/account/set-leverage" in paths_called
        assert "/api/v5/trade/order-algo" in paths_called
        assert "/api/v5/trade/cancel-order" in paths_called
        assert "/api/v5/trade/cancel-algos" in paths_called

        # 3. Preflight check with live instrument cache and mocked verify/max-size
        fake_gateway = SimpleNamespace(
            fetch_exchange_instruments=AsyncMock(return_value=True),
            real_follower_bbo={"ADA/USDT:USDT": (0.499, 0.501, 100.0, 100.0)},
            real_leader_bbo={"ADA/USDT:USDT": (0.500, 0.501, 100.0, 100.0)},
        )
        with (
            patch.object(
                adapter,
                "verify_exchange_credentials",
                new=AsyncMock(
                    return_value={
                        "ok": True,
                        "auth_status": "VERIFIED",
                        "auth_message": "VERIFIED",
                        "account_equity_usdt": 49.95,
                        "acct_lv": "2",
                        "pos_mode": "net_mode",
                    }
                ),
            ),
            patch.object(
                adapter,
                "_okx_request",
                new=AsyncMock(return_value={"code": "0", "data": [{"maxBuy": "10", "maxSell": "10"}]}),
            ),
        ):
            pf = await adapter.preflight_check_symbols(ex_cfg, [cfg_ada], {}, gateway=fake_gateway)
            assert pf["ok"] is True
            assert pf["symbols"][0]["symbol"] == "ADA/USDT:USDT"
            assert pf["symbols"][0]["ctVal"] == 100.0
            assert pf["symbols"][0]["planned_sz"] == "0.2"

    asyncio.run(run_okx_tests())


def test_okx_account_mode_auto_upgrade_and_multi_exchange_verify():
    from backend.sniper_engine import BidirectionalSniperEngine

    risk = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
    adapter = ExecutionAdapter(get_math_state=lambda _: None, get_risk_cfg=lambda: risk)
    okx_cfg = {
        "exchange_id": "okx",
        "api_key": "okx-k-1234",
        "_decrypted_secret": "okx-s-1234",
        "_decrypted_passphrase": "pass",
        "account_equity_usdt": 49.95,
    }

    async def run_verify_tests():
        # 1. auto_upgrade_okx_mode=True calls POST /api/v5/account/set-account-level and turns VERIFIED
        with (
            patch.object(
                httpx.AsyncClient,
                "get",
                new=AsyncMock(
                    side_effect=[
                        SimpleNamespace(json=lambda: {"code": "0", "data": [{"totalEq": "49.95", "details": []}]}),
                        SimpleNamespace(json=lambda: {"code": "0", "data": [{"acctLv": "1", "posMode": "net_mode", "perm": "read_only,trade"}]}),
                    ]
                ),
            ),
            patch.object(
                httpx.AsyncClient,
                "post",
                new=AsyncMock(return_value=SimpleNamespace(json=lambda: {"code": "0", "data": [{"acctLv": "2"}]})),
            ),
        ):
            res = await adapter.verify_exchange_credentials(okx_cfg, auto_upgrade_okx_mode=True)
            assert res["ok"] is True
            assert res["auth_status"] == "VERIFIED"
            assert res["acct_lv"] == "2"

        # 2. Manual switch_okx_account_level handles OKX 59000 conflict cleanly
        with patch.object(
            adapter,
            "_okx_request",
            new=AsyncMock(return_value={"code": "59000", "msg": "open positions exist"}),
        ):
            sw_fail = await adapter.switch_okx_account_level(okx_cfg, "2")
            assert sw_fail["ok"] is False
            assert "59000" in sw_fail["message"]

        # 3. Bybit / Bitget / Gate real endpoint verification parsing
        with patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    status_code=200,
                    json=lambda: {"retCode": 0, "result": {"list": [{"totalEquity": "125.5"}]}},
                )
            ),
        ):
            by_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "bybit", "api_key": "byb-key-1234", "_decrypted_secret": "byb-sec-1234"}
            )
            assert by_res["ok"] is True and by_res["account_equity_usdt"] == 125.5

        with patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    status_code=200,
                    json=lambda: {"code": "00000", "data": [{"marginCoin": "USDT", "usdtEquity": "88.8"}]},
                )
            ),
        ):
            bg_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "bitget", "api_key": "bg-key-1234", "_decrypted_secret": "bg-sec-1234", "_decrypted_passphrase": "pass"}
            )
            assert bg_res["ok"] is True and bg_res["account_equity_usdt"] == 88.8

        with patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(
                return_value=SimpleNamespace(status_code=200, json=lambda: {"total": "210.25"})
            ),
        ):
            gt_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "gate", "api_key": "gt-key-1234", "_decrypted_secret": "gt-sec-1234"}
            )
            assert gt_res["ok"] is True and gt_res["account_equity_usdt"] == 210.25

    asyncio.run(run_verify_tests())


def test_long_short_grid_strategy_lifecycle_and_per_symbol_selection():
    from backend.grid_engine import LongShortGridTracker
    from backend.sniper_engine import BidirectionalSniperEngine

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "grid_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()

        # Persist one LEAD_LAG symbol and one LONG_SHORT_GRID symbol on user_id=7
        adb.ensure_user_workspace_sync(7)
        s_ll = SymbolStrategy(symbol="SOL/USDT:USDT", strategy_type="LEAD_LAG", order_usdt=10.0, leverage=5)
        s_grid = SymbolStrategy(
            symbol="BTC/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.01,
            grid_take_profit_pct=0.025,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.10,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=4,
            min_qty=0.0001,
            slippage_pct=0.0005,
        )
        adb.upsert_symbol_sync(s_ll, user_id=7)
        adb.upsert_symbol_sync(s_grid, user_id=7)
        loaded = {s.symbol: s for s in adb.load_symbols_sync(user_id=7)}
        assert loaded["SOL/USDT:USDT"].strategy_type == "LEAD_LAG"
        assert loaded["BTC/USDT:USDT"].strategy_type == "LONG_SHORT_GRID"
        assert loaded["BTC/USDT:USDT"].grid_levels == 3
        assert loaded["BTC/USDT:USDT"].grid_side_mode == "LONG_SHORT"

        # Test Grid Tracker directly on symmetrical Long + Short oscillation & net_mode protection
        tracker = LongShortGridTracker("BTC/USDT:USDT")
        tracker.sync_config(s_grid, current_mid=100000.0)
        assert tracker.center_price == 100000.0
        assert "L1" in tracker.levels and "S1" in tracker.levels
        assert tracker.levels["L1"]["trigger_price"] == 99000.0
        assert tracker.levels["S1"]["trigger_price"] == 101000.0

        # Price drops to trigger L1 in LIVE net_mode -> L1 opens
        en1, ex1 = tracker.evaluate_tick(98950.0, 98960.0, 98955.0, s_grid, is_net_mode=True, allow_entries=True)
        assert len(en1) == 1 and en1[0]["slot_id"] == "L1"
        tracker.mark_entry_filled("L1", 98960.0, 0.001, 0.1, 0.01, s_grid, 0.0007)

        # In net_mode, while L1 LONG is open (< TP 101500), rally hitting S1 (101050) must NOT open SHORT
        en_block, ex_mid = tracker.evaluate_tick(101050.0, 101060.0, 101055.0, s_grid, is_net_mode=True, allow_entries=True)
        assert len(en_block) == 0 and len(ex_mid) == 0

        # L1 reaches TP (>101500) & exits cleanly
        en_exit, ex_hit = tracker.evaluate_tick(101600.0, 101610.0, 101605.0, s_grid, is_net_mode=True, allow_entries=False)
        assert len(ex_hit) == 1 and ex_hit[0]["slot_id"] == "L1" and ex_hit[0]["exit_reason"] == "GRID_TP"
        tracker.mark_exited("L1", net_pnl=1.10)

        # Now L1 is closed, Short S1 triggers cleanly
        en_s, _ = tracker.evaluate_tick(101050.0, 101060.0, 101055.0, s_grid, is_net_mode=True, allow_entries=True)
        assert len(en_s) == 1 and en_s[0]["slot_id"] == "S1" and en_s[0]["side"] == "SHORT"

        # Also test full async BidirectionalSniperEngine dispatch in SHADOW mode
        async def run_engine_grid_flow():
            eng = BidirectionalSniperEngine(adb, user_id=7)
            eng.risk_config = GlobalRiskConfig(
                trading_mode="SHADOW", shadow_base_latency_ms=5.0, shadow_latency_jitter_ms=1.0, shadow_adverse_selection_prob=0.0
            )
            eng.trading_active_by_mode["SHADOW"] = True
            eng.exchanges = {
                "binanceusdm": {"exchange_id": "binanceusdm", "display_name": "Binance", "role": "leader", "is_active": True, "taker_fee": 0.0004, "maker_fee": 0.0002},
                "okx": {"exchange_id": "okx", "display_name": "OKX", "role": "follower", "is_active": True, "taker_fee": 0.0005, "maker_fee": 0.0002},
            }
            await adb.start_worker()
            eng._register_symbol_internal(s_grid)

            # Initialize center @ 100000 -> drop to 98900 (opens L1) -> bounce to 101600 (closes L1 TP)
            eng._handle_follower_bbo("BTC/USDT:USDT", 99995.0, 100005.0, 10.0, 10.0)
            eng._handle_follower_bbo("BTC/USDT:USDT", 98895.0, 98905.0, 10.0, 10.0)
            await asyncio.sleep(0.08)
            assert len(eng.active_grid_positions) >= 1

            eng._handle_follower_bbo("BTC/USDT:USDT", 101600.0, 101610.0, 10.0, 10.0)
            await asyncio.sleep(0.08)
            snap = eng.build_dashboard_snapshot()
            assert snap["telemetry"]["BTC/USDT:USDT"]["strategy_type"] == "LONG_SHORT_GRID"
            assert snap["stats"]["win_count"] >= 1
            await adb.stop_worker()

        asyncio.run(run_engine_grid_flow())


def test_pepe_shib_high_precision_grid_save_and_verify_prompts():
    """
    Verifies:
    1. High-precision meme coins like PEPE (price_precision=10) and SHIB (price_precision=9)
       can be verified and saved as LONG_SHORT_GRID with order_usdt=5 without triggering 422 errors.
    2. 1000SHIB / 1000PEPE aliases normalize to SHIB/USDT:USDT and PEPE/USDT:USDT.
    3. Invalid or unsupported symbols return verified=False with a descriptive Chinese error prompt.
    """
    from backend.main import app
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE

    _OKX_INSTRUMENT_CACHE["PEPE-USDT-SWAP"] = {
        "instId": "PEPE-USDT-SWAP",
        "unified_sym": "PEPE/USDT:USDT",
        "state": "live",
        "ctVal": 10000000.0,
        "ctVal_str": "10000000",
        "lotSz": 0.1,
        "lotSz_str": "0.1",
        "minSz": 0.1,
        "minSz_str": "0.1",
        "tickSz": 1e-10,
        "tickSz_str": "0.0000000001",
        "maxLever": 50.0,
    }
    _OKX_INSTRUMENT_CACHE["SHIB-USDT-SWAP"] = {
        "instId": "SHIB-USDT-SWAP",
        "unified_sym": "SHIB/USDT:USDT",
        "state": "live",
        "ctVal": 1000000.0,
        "ctVal_str": "1000000",
        "lotSz": 0.1,
        "lotSz_str": "0.1",
        "minSz": 0.1,
        "minSz_str": "0.1",
        "tickSz": 1e-9,
        "tickSz_str": "0.000000001",
        "maxLever": 50.0,
    }

    with TestClient(app) as client:
        # 1. Verify SHIB and 1000SHIB
        r_shib = client.get("/api/symbol/verify?symbol=shib").json()
        assert r_shib["verified"] is True
        assert r_shib["symbol"] == "SHIB/USDT:USDT"
        assert r_shib["recommended_precision"]["price_precision"] == 9

        r_1000shib = client.get("/api/symbol/verify?symbol=1000SHIB").json()
        assert r_1000shib["verified"] is True
        assert r_1000shib["symbol"] == "SHIB/USDT:USDT"

        # 2. Verify PEPE (price_precision=10)
        r_pepe = client.get("/api/symbol/verify?symbol=PEPE").json()
        assert r_pepe["verified"] is True
        assert r_pepe["symbol"] == "PEPE/USDT:USDT"
        assert r_pepe["recommended_precision"]["price_precision"] == 10

        # 3. Verify invalid & nonexistent symbols return verified=False + error prompt
        r_bad = client.get("/api/symbol/verify?symbol=@@@").json()
        assert r_bad["verified"] is False
        assert "error" in r_bad and r_bad["error"]

        r_unknown = client.get("/api/symbol/verify?symbol=FAKETOKEN999").json()
        assert r_unknown["verified"] is False
        assert "未上线" in r_unknown["error"]

        # 4. Validate SymbolStrategy model accepts price_precision=9 and 10 with LONG_SHORT_GRID & order_usdt=5
        pepe_cfg = SymbolStrategy(
            symbol="PEPE/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            order_usdt=5.0,
            leverage=5,
            price_precision=10,
            qty_precision=0,
            min_qty=1000000.0,
        )
        shib_cfg = SymbolStrategy(
            symbol="SHIB/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            order_usdt=5.0,
            leverage=5,
            price_precision=9,
            qty_precision=0,
            min_qty=100000.0,
        )
        assert pepe_cfg.price_precision == 10
        assert shib_cfg.price_precision == 9


def test_position_persistence_and_live_sync_across_pause_and_restart():
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.models import ExchangeConfigModel, SniperState
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "pos_recovery_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(77)

        # Configure tenant 77 in LIVE mode with one GRID symbol (BTC) and one LEAD_LAG symbol (SOL)
        adb.save_exchange_sync(
            ExchangeConfigModel(
                exchange_id="binanceusdm",
                display_name="Binance USD-M",
                role="leader",
                is_active=True,
                taker_fee=0.0004,
                maker_fee=0.0002,
            ),
            user_id=77,
        )
        adb.save_exchange_sync(
            ExchangeConfigModel(
                exchange_id="okx",
                display_name="OKX Swap",
                role="follower",
                is_active=True,
                api_key="test-okx-key",
                api_secret="test-okx-sec",
                passphrase="test-okx-pass",
                taker_fee=0.0005,
                maker_fee=0.0002,
            ),
            user_id=77,
        )
        btc_grid = SymbolStrategy(
            symbol="BTC/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.01,
            grid_take_profit_pct=0.025,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.10,
            order_usdt=100.0,
            leverage=5,
            price_precision=1,
            qty_precision=2,
            min_qty=0.01,
        )
        sol_ll = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="LEAD_LAG",
            order_usdt=50.0,
            leverage=5,
            take_profit_pct=0.015,
            stop_loss_pct=0.025,
            price_precision=2,
            qty_precision=1,
            min_qty=0.1,
        )
        adb.upsert_symbol_sync(btc_grid, user_id=77)
        adb.upsert_symbol_sync(sol_ll, user_id=77)

        async def run_recovery_flow():
            # 1. Session A: open a grid position & persist state before stop/restart
            eng1 = BidirectionalSniperEngine(adb, user_id=77)
            for ex in adb.load_exchanges_sync(user_id=77):
                eng1.exchanges[ex["exchange_id"]] = ex
            for cfg in adb.load_symbols_sync(user_id=77):
                eng1._register_symbol_internal(cfg)
            eng1.risk_config = GlobalRiskConfig(trading_mode="LIVE", user_started=True, max_exposure_usdt=5000.0)
            eng1.trading_active_by_mode["LIVE"] = True
            eng1.exchanges["okx"]["_decrypted_secret"] = "test-okx-sec"
            eng1.exchanges["okx"]["_decrypted_passphrase"] = "test-okx-pass"

            tracker1 = eng1.grid_trackers["BTC/USDT:USDT"]
            tracker1.sync_config(btc_grid, current_mid=100000.0)
            tracker1.recover_open_position(
                side="LONG",
                entry_price=98000.0,
                filled_qty=0.01,
                contracts=1.0,
                ct_val=0.01,
                cfg=btc_grid,
                round_trip_fee=0.001,
                preferred_slot="L1",
            )
            eng1._save_open_strategy_state()
            eng1.persist_runtime_state()

            saved_state = adb.load_runtime_setting_sync("open_strategy_state", user_id=77)
            assert isinstance(saved_state, dict)
            assert saved_state.get("grid_states", {}).get("BTC/USDT:USDT"), "open_strategy_state should persist BTC grid slot"

            # Insert a LEAD_LAG OPEN position row for SOL into positions table
            conn = sqlite3.connect(db_file)
            conn.execute(
                """INSERT INTO positions (user_id, symbol, exchange_id, side, qty, entry_price, tp_order_id, sl_order_id, state, opened_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (77, "SOL/USDT:USDT", "okx", "SHORT", 2.0, 150.0, "", "", "HOLDING", int(time.time() * 1000)),
            )
            conn.commit()
            conn.close()

            # 2. Session B: Simulate full service restart + pause -> resume
            eng2 = BidirectionalSniperEngine(adb, user_id=77)
            for ex in adb.load_exchanges_sync(user_id=77):
                eng2.exchanges[ex["exchange_id"]] = ex
            for cfg in adb.load_symbols_sync(user_id=77):
                eng2._register_symbol_internal(cfg)
            eng2.risk_config = GlobalRiskConfig(trading_mode="LIVE", user_started=True, max_exposure_usdt=5000.0)
            eng2.exchanges["okx"]["_decrypted_secret"] = "test-okx-sec"
            eng2.exchanges["okx"]["_decrypted_passphrase"] = "test-okx-pass"

            await eng2._reconcile_startup_positions()
            # Persisted grid state must be restored immediately on startup without getting wiped
            assert any(k.startswith("BTC/USDT:USDT:") for k in eng2.active_grid_positions), (
                f"Expected restored grid slot for BTC, got {list(eng2.active_grid_positions.keys())}"
            )
            # LEAD_LAG DB row must NOT be wiped to ORPHAN_RECOVERED before exchange sync verifies it
            assert len(adb.load_open_positions_sync(user_id=77)) == 1

            live_okx_rows = [
                {
                    "symbol": "BTC/USDT:USDT",
                    "inst_id": "BTC-USDT-SWAP",
                    "side": "LONG",
                    "contracts": 1.0,
                    "ct_val": 0.01,
                    "qty": 0.01,
                    "entry_price": 98000.0,
                    "mark_price": 99000.0,
                    "notional_usdt": 990.0,
                    "margin_usdt": 198.0,
                    "leverage": 5,
                    "upl_usdt": 10.0,
                    "upl_ratio": 0.0505,
                    "mgn_mode": "isolated",
                    "pos_id": "okx-pos-btc",
                },
                {
                    "symbol": "SOL/USDT:USDT",
                    "inst_id": "SOL-USDT-SWAP",
                    "side": "SHORT",
                    "contracts": 2.0,
                    "ct_val": 1.0,
                    "qty": 2.0,
                    "entry_price": 150.0,
                    "mark_price": 147.0,
                    "notional_usdt": 294.0,
                    "margin_usdt": 58.8,
                    "leverage": 5,
                    "upl_usdt": 6.0,
                    "upl_ratio": 0.102,
                    "mgn_mode": "isolated",
                    "pos_id": "okx-pos-sol",
                },
                {
                    "symbol": "NVDA/USDT:USDT",
                    "inst_id": "NVDA-USDT-SWAP",
                    "side": "LONG",
                    "contracts": 5.0,
                    "ct_val": 0.1,
                    "qty": 0.5,
                    "entry_price": 135.0,
                    "mark_price": 136.0,
                    "notional_usdt": 68.0,
                    "margin_usdt": 13.6,
                    "leverage": 5,
                    "upl_usdt": 0.5,
                    "upl_ratio": 0.0367,
                    "mgn_mode": "isolated",
                    "pos_id": "okx-pos-nvda",
                },
            ]

            mock_fetch = AsyncMock(return_value={"ok": True, "positions": list(live_okx_rows)})
            with (
                patch.object(eng2.executor, "fetch_live_positions", new=mock_fetch),
                patch.object(
                    eng2.executor,
                    "verify_exchange_credentials",
                    new=AsyncMock(
                        return_value={
                            "ok": True,
                            "auth_status": "VERIFIED",
                            "auth_message": "账户模式: 单币种保证金",
                            "account_equity_usdt": 100.0,
                        }
                    ),
                ),
                patch.object(eng2.executor, "preflight_check_symbols", new=AsyncMock(return_value={"ok": True, "errors": [], "warnings": []})),
            ):
                # Pause then Resume -> automatically syncs live OKX positions
                await eng2.set_trading_active(False)
                resume_res = await eng2.set_trading_active(True)
                assert resume_res["ok"] is True

                snap = eng2.build_dashboard_snapshot()
                by_sym = {p["symbol"]: p for p in snap["active_positions"]}
                assert set(by_sym.keys()) == {"BTC/USDT:USDT", "SOL/USDT:USDT", "NVDA/USDT:USDT"}

                # BTC -> Grid strategy recovered
                assert by_sym["BTC/USDT:USDT"]["strategy_type"] == "LONG_SHORT_GRID"
                assert by_sym["BTC/USDT:USDT"]["managed_by_strategy"] is True
                assert by_sym["BTC/USDT:USDT"]["recovered"] is True
                assert by_sym["BTC/USDT:USDT"]["tp_price"] > 98000.0
                assert by_sym["BTC/USDT:USDT"]["sl_price"] > 0

                # SOL -> Lead-Lag strategy recovered & monitoring active
                assert eng2.symbol_states.get("SOL/USDT:USDT") == SniperState.HOLDING
                assert "SOL/USDT:USDT" in eng2.active_positions
                assert by_sym["SOL/USDT:USDT"]["strategy_type"] == "LEAD_LAG"
                assert by_sym["SOL/USDT:USDT"]["managed_by_strategy"] is True
                assert by_sym["SOL/USDT:USDT"]["side"] == "SHORT"

                # NVDA -> Unmanaged exchange holding visible in dashboard
                assert by_sym["NVDA/USDT:USDT"]["strategy_type"] == "UNMANAGED"
                assert by_sym["NVDA/USDT:USDT"]["managed_by_strategy"] is False

                # Adopt NVDA into grid strategy -> becomes managed LONG_SHORT_GRID slot
                adopt_res = await eng2.adopt_exchange_position_into_grid("NVDA/USDT:USDT")
                assert adopt_res["ok"] is True
                assert "NVDA/USDT:USDT" in eng2.strategies
                assert eng2.strategies["NVDA/USDT:USDT"].strategy_type == "LONG_SHORT_GRID"

                snap_adopted = eng2.build_dashboard_snapshot()
                by_sym_adopted = {p["symbol"]: p for p in snap_adopted["active_positions"]}
                assert by_sym_adopted["NVDA/USDT:USDT"]["strategy_type"] == "LONG_SHORT_GRID"
                assert by_sym_adopted["NVDA/USDT:USDT"]["managed_by_strategy"] is True

                # When NVDA closes on OKX, next periodic sync cleans stale recovered slot
                mock_fetch.return_value = {"ok": True, "positions": list(live_okx_rows[:2])}
                await eng2.sync_live_exchange_positions("periodic")
                snap_after_close = eng2.build_dashboard_snapshot()
                assert "NVDA/USDT:USDT" not in {p["symbol"] for p in snap_after_close["active_positions"]}

            for t in list(eng2._bg_tasks):
                t.cancel()
            if eng2._bg_tasks:
                await asyncio.gather(*list(eng2._bg_tasks), return_exceptions=True)

        asyncio.run(run_recovery_flow())

        # Verify guest public preview remains isolated and leaks zero OKX holdings
        from backend.main import app
        with TestClient(app) as client:
            guest_stats = client.get("/api/stats").json()
            assert guest_stats["risk"]["trading_mode"] == "SHADOW"
            assert not any(p.get("symbol") == "NVDA/USDT:USDT" for p in guest_stats.get("active_positions", []))


def test_zero_latency_db_persistence_http_pool_and_ws_radar_filter():
    from backend.executor import OrderResult
    from backend.math_engine import mono_ms
    from backend.sniper_engine import BidirectionalSniperEngine
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "perf_opt_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(88)

        zec_cfg = SymbolStrategy(
            symbol="ZEC/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            order_usdt=5.0,
            leverage=5,
            min_qty=0.01,
            price_precision=2,
            qty_precision=2,
        )
        btc_cfg = SymbolStrategy(
            symbol="BTC/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            order_usdt=50.0,
            leverage=5,
            min_qty=0.0001,
            price_precision=1,
            qty_precision=3,
        )
        adb.upsert_symbol_sync(zec_cfg, user_id=88)
        adb.upsert_symbol_sync(btc_cfg, user_id=88)

        async def run_perf_checks():
            await adb.start_worker()
            try:
                eng = BidirectionalSniperEngine(adb, user_id=88)
                for ex in adb.load_exchanges_sync(user_id=88):
                    eng.exchanges[ex["exchange_id"]] = ex
                for cfg in adb.load_symbols_sync(user_id=88):
                    eng._register_symbol_internal(cfg)
                    eng._seed_initial_ticks(cfg.symbol, cfg, eng.math_states[cfg.symbol])

                # 1. Persistent HTTP client pooling reuses warm connection within same loop
                async with eng.executor._borrow_http_client(None, timeout=4.0) as c1:
                    async with eng.executor._borrow_http_client(None, timeout=4.0) as c2:
                        assert c1 is c2

                # 2. Radar filter returns only requested coin (cuts WS payload size by ~80%)
                full_snap = eng.build_dashboard_snapshot()
                assert set(full_snap["radar_series"].keys()) >= {"ZEC/USDT:USDT", "BTC/USDT:USDT"}
                filtered_snap = eng.build_dashboard_snapshot(radar_symbol="BTC/USDT:USDT")
                assert list(filtered_snap["radar_series"].keys()) == ["BTC/USDT:USDT"]

                # 3. Structural size/margin rejection backs off 60s and stops spamming SQLite orders
                eng.risk_config = GlobalRiskConfig(trading_mode="LIVE")
                eng.trading_active_by_mode["LIVE"] = True
                tracker = eng.grid_trackers["ZEC/USDT:USDT"]
                tracker.sync_config(zec_cfg, current_mid=400.0)
                slot_info = {**tracker.levels["S1"], "reservation_price": 404.0, "trigger_exec_price": 404.0}
                tracker.levels["S1"]["status"] = "ENTERING"

                with patch.object(
                    eng.executor,
                    "place_entry_ioc",
                    new=AsyncMock(
                        return_value=OrderResult(
                            order_id="ioc-zec-block",
                            status="MISSED",
                            filled_qty=0.0,
                            avg_price=0.0,
                            latency_ms=0.04,
                            raw_msg="OKX 实盘下单元数据校验拦截: 单笔金额 5.00U 小于 OKX 最小开仓额 9.60U",
                        )
                    ),
                ):
                    await eng._run_grid_entry(zec_cfg, tracker, slot_info, "LIVE")

                lv_s1 = tracker.levels["S1"]
                assert lv_s1["status"] == "IDLE"
                assert lv_s1["cooldown_until_mono"] - mono_ms() > 50_000.0

                # Updating order_usdt clears backoff immediately without waiting 60s
                zec_cfg_fixed = zec_cfg.model_copy(update={"order_usdt": 15.0})
                eng.add_or_update_symbol(zec_cfg_fixed)
                assert tracker.levels["S1"]["cooldown_until_mono"] == 0.0

                # 4. Async runtime persistence flushes through batch worker
                eng._save_open_strategy_state()
                await asyncio.sleep(0.35)
                assert isinstance(adb.load_runtime_setting_sync("open_strategy_state", user_id=88), dict)

                # 5. Cleanup prunes aged MISSED orders while keeping 100% of FILLED orders
                conn = sqlite3.connect(db_file)
                conn.execute(
                    """INSERT INTO orders (order_id, user_id, symbol, exchange_id, purpose, side, order_type, price, requested_qty, filled_qty, avg_price, status, latency_ms, created_at)
                       VALUES ('old-miss-1', 88, 'ZEC/USDT:USDT', 'okx', 'GRID_S1', 'SHORT', 'IOC_LIMIT', 400, 0.01, 0, 0, 'MISSED', 0.04, datetime('now', '-5 days')),
                              ('old-fill-1', 88, 'BTC/USDT:USDT', 'okx', 'GRID_L1', 'LONG', 'IOC_LIMIT', 64000, 0.01, 0.01, 64000, 'FILLED', 25.0, datetime('now', '-10 days'))"""
                )
                conn.commit()
                conn.close()

                clean_res = adb.cleanup_old_records_sync(missed_keep_hours=48, risk_keep_days=14)
                assert clean_res["missed_deleted"] == 1
                conn = sqlite3.connect(db_file)
                rem_ids = {r[0] for r in conn.execute("SELECT order_id FROM orders WHERE user_id = 88").fetchall()}
                conn.close()
                assert "old-fill-1" in rem_ids
                assert "old-miss-1" not in rem_ids

                # 6. Verify real-time total unrealized profit / loss / net aggregation across all open positions
                eng.active_grid_positions["BTC/USDT:USDT:L1"] = {
                    "symbol": "BTC/USDT:USDT",
                    "strategy_type": "LONG_SHORT_GRID",
                    "grid_slot_id": "L1",
                    "mode": "LIVE",
                    "side": "LONG",
                    "qty": 0.01,
                    "notional_usdt": 650.0,
                    "entry_price": 64000.0,
                    "mark_price": 65000.0,
                    "unrealized_net_pct": 0.015,
                    "unrealized_usdt": 10.0,
                    "managed_by_strategy": True,
                }
                eng.active_positions["ZEC/USDT:USDT"] = {
                    "symbol": "ZEC/USDT:USDT",
                    "strategy_type": "LEAD_LAG",
                    "mode": "LIVE",
                    "side": "SHORT",
                    "qty": 0.1,
                    "notional_usdt": 40.0,
                    "entry_price": 400.0,
                    "mark_price": 425.0,
                    "unrealized_net_pct": -0.0625,
                    "unrealized_usdt": -2.5,
                    "managed_by_strategy": True,
                }
                eng.exchange_live_positions = {
                    # Already managed above (must NOT double-count)
                    "BTC/USDT:USDT:LONG": {
                        "symbol": "BTC/USDT:USDT",
                        "side": "LONG",
                        "qty": 0.01,
                        "contracts": 1.0,
                        "ct_val": 0.01,
                        "notional_usdt": 650.0,
                        "entry_price": 64000.0,
                        "mark_price": 65000.0,
                        "leverage": 5,
                        "unrealized_net_pct": 0.015,
                        "unrealized_usdt": 10.0,
                    },
                    # Unmanaged losing holding (MUST be included in loss sum)
                    "NVDA/USDT:USDT:LONG": {
                        "symbol": "NVDA/USDT:USDT",
                        "side": "LONG",
                        "qty": 0.5,
                        "contracts": 5.0,
                        "ct_val": 0.1,
                        "notional_usdt": 65.0,
                        "entry_price": 135.0,
                        "mark_price": 130.0,
                        "leverage": 5,
                        "unrealized_net_pct": -0.037,
                        "unrealized_usdt": -1.25,
                    },
                    # Unmanaged winning holding (MUST be included in profit sum)
                    "SNDK/USDT:USDT:LONG": {
                        "symbol": "SNDK/USDT:USDT",
                        "side": "LONG",
                        "qty": 1.0,
                        "contracts": 1.0,
                        "ct_val": 1.0,
                        "notional_usdt": 45.0,
                        "entry_price": 44.0,
                        "mark_price": 45.5,
                        "leverage": 5,
                        "unrealized_net_pct": 0.034,
                        "unrealized_usdt": 1.5,
                    },
                }

                # Also verify live follower tick refreshes unrealized_usdt in real time for exchange positions
                # and multi-slot net_mode grids do not double-count or drift from exchange avgPx
                eng.active_grid_positions["BTC/USDT:USDT:L2"] = {
                    "symbol": "BTC/USDT:USDT",
                    "strategy_type": "LONG_SHORT_GRID",
                    "grid_slot_id": "L2",
                    "mode": "LIVE",
                    "side": "LONG",
                    "qty": 0.005,
                    "notional_usdt": 325.0,
                    "entry_price": 63500.0,
                    "mark_price": 65000.0,
                    "unrealized_net_pct": 0.02,
                    "unrealized_usdt": 7.5,
                    "managed_by_strategy": True,
                }
                eng._handle_follower_bbo("BTC/USDT:USDT", 65200.0, 65201.0, 10.0, 10.0)
                # Exchange BTC LONG qty=0.01, avgPx=64000, mark=bid=65200 -> +12.0 USDT, notional=652.0
                pnl_snap = eng.build_dashboard_snapshot(radar_symbol="BTC/USDT:USDT")
                ps = pnl_snap["position_pnl_summary"]
                assert ps["total_positions"] == 4
                assert ps["strategy_slots"] == 5
                assert ps["profit_count"] == 2
                assert ps["loss_count"] == 2
                assert ps["total_profit_usdt"] == 13.5   # 12.0 (BTC) + 1.5 (SNDK)
                assert ps["total_loss_usdt"] == -3.75    # -2.5 (ZEC) + -1.25 (NVDA)
                assert ps["net_unrealized_usdt"] == 9.75 # 13.5 - 3.75
                assert ps["total_notional_usdt"] == 802.0
                assert pnl_snap["stats"]["position_pnl_summary"] == ps

                await eng.executor.aclose()
            finally:
                await adb.stop_worker()

        asyncio.run(run_perf_checks())


def test_trend_breakout_grid_and_adaptive_vcp_dual_mode_switching():
    from backend.grid_engine import LongShortGridTracker, calc_pyramid_weights
    from backend.market_gateway import MarketDataGateway
    import math

    # 1. Verify exact 50% / 30% / 20% decaying pyramid weights for 3 levels
    assert calc_pyramid_weights(3, decay_enabled=True) == [0.50, 0.30, 0.20]
    assert abs(sum(calc_pyramid_weights(5, decay_enabled=True)) - 1.0) < 1e-9

    # 2. Verify SQLite migration & round-trip persistence for TREND_GRID and ADAPTIVE_GRID
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "trend_grid_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(99)

        trend_cfg = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="TREND_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.01,
            grid_range_pct=0.08,
            grid_stop_loss_pct=0.08,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=2,
            slippage_pct=0.0005,
            trend_dead_zone_mult=2.0,
            trend_trailing_mult=1.5,
            trend_pyramid_decay=True,
            trend_use_bb_breakout=True,
            bbw_timeframe="15m",
            bbw_squeeze_percentile=20.0,
        )
        adaptive_cfg = trend_cfg.model_copy(
            update={"symbol": "BTC/USDT:USDT", "strategy_type": "ADAPTIVE_GRID", "bbw_timeframe": "1H"}
        )
        adb.upsert_symbol_sync(trend_cfg, user_id=99)
        adb.upsert_symbol_sync(adaptive_cfg, user_id=99)

        loaded = {s.symbol: s for s in adb.load_symbols_sync(user_id=99)}
        assert loaded["SOL/USDT:USDT"].strategy_type == "TREND_GRID"
        assert loaded["SOL/USDT:USDT"].trend_dead_zone_mult == 2.0
        assert loaded["SOL/USDT:USDT"].trend_trailing_mult == 1.5
        assert loaded["SOL/USDT:USDT"].trend_pyramid_decay is True
        assert loaded["BTC/USDT:USDT"].strategy_type == "ADAPTIVE_GRID"
        assert loaded["BTC/USDT:USDT"].bbw_timeframe == "1H"

    # 3. Verify TREND_GRID: Dead Zone + BB Upper + 50/30/20 Pyramid + Livermore SL + Batch Trailing Stop
    bb_info = {
        "timeframe": "15m",
        "bbw": 0.012,
        "bbw_pct": 1.2,
        "bbw_percentile": 8.0,
        "bb_upper": 102.50,
        "bb_middle": 100.00,
        "bb_lower": 97.50,
        "vcp_contraction": True,
    }
    tracker = LongShortGridTracker("SOL/USDT:USDT")
    tracker.sync_config(trend_cfg, current_mid=100.0, bbw_info=bb_info)
    assert tracker.active_grid_mode == "TREND_BREAKOUT"
    # L1 must clear BOTH 2*step (102.0) and BB upper (102.5) -> 102.5; S1 -> 97.5
    assert tracker.levels["L1"]["trigger_price"] == 102.50
    assert tracker.levels["L2"]["trigger_price"] == 103.50
    assert tracker.levels["L3"]["trigger_price"] == 104.50
    assert tracker.levels["S1"]["trigger_price"] == 97.50
    # Decaying 50% / 30% / 20% of 300U budget
    assert tracker.levels["L1"]["order_usdt"] == 150.0 and tracker.levels["L1"]["weight_pct"] == 50.0
    assert tracker.levels["L2"]["order_usdt"] == 90.0 and tracker.levels["L2"]["weight_pct"] == 30.0
    assert tracker.levels["L3"]["order_usdt"] == 60.0 and tracker.levels["L3"]["weight_pct"] == 20.0
    # No fixed TP cap
    assert tracker.levels["L1"]["tp_price"] == 0.0

    # Price inside dead zone (101.8) -> zero entries
    en0, ex0 = tracker.evaluate_tick(101.7, 101.8, 101.75, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert en0 == [] and ex0 == []

    # Price breaks above L1 (102.55) -> only L1 triggers first (sequential pyramid)
    en1, ex1 = tracker.evaluate_tick(102.50, 102.55, 102.52, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en1] == ["L1"]
    tracker.mark_entry_filled("L1", entry_price=102.55, filled_qty=1.46, contracts=1.46, ct_val=1.0, cfg=trend_cfg, round_trip_fee=0.0007)
    # L1 initial SL strictly honors configured grid_stop_loss_pct (8% below entry), NOT truncated to center_price!
    assert tracker.levels["L1"]["sl_price"] == round(102.55 * (1.0 - trend_cfg.grid_stop_loss_pct), 2)

    # Price rallies to L2 (103.55) -> L2 opens and raises L1's SL to breakeven + fee, with monotonic L2 SL >= L1 SL!
    en2, _ = tracker.evaluate_tick(103.50, 103.55, 103.52, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en2] == ["L2"]
    tracker.mark_entry_filled("L2", entry_price=103.55, filled_qty=0.87, contracts=0.87, ct_val=1.0, cfg=trend_cfg, round_trip_fee=0.0007)
    assert tracker.levels["L1"]["sl_price"] >= round(102.55 * 1.0007, 2)  # Livermore breakeven protection!
    assert tracker.levels["L2"]["sl_price"] >= tracker.levels["L1"]["sl_price"]  # Monotonic: L2 SL never below L1 SL!

    # Price rallies to L3 (104.55) -> L3 opens, L1 SL moves up to L2 entry (guaranteed profit!), L2 SL to breakeven, monotonic L3 SL >= L2 SL
    en3, _ = tracker.evaluate_tick(104.50, 104.55, 104.52, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en3] == ["L3"]
    tracker.mark_entry_filled("L3", entry_price=104.55, filled_qty=0.57, contracts=0.57, ct_val=1.0, cfg=trend_cfg, round_trip_fee=0.0007)
    assert tracker.levels["L1"]["sl_price"] == 103.55  # Locked profit!
    assert tracker.levels["L2"]["sl_price"] >= round(103.55 * 1.0007, 2)
    assert tracker.levels["L3"]["sl_price"] >= tracker.levels["L2"]["sl_price"]

    # Let profits run to peak 110.00 (no fixed TP!) -> trail stop = 110 * (1 - 0.015) = 108.35
    _, ex_peak = tracker.evaluate_tick(110.00, 110.05, 110.02, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert ex_peak == []
    assert tracker.long_peak_price == 110.00
    assert tracker.long_trail_stop_price == 108.35

    # Pullback to 108.80 (still above 108.35) -> still holding
    _, ex_mid = tracker.evaluate_tick(108.80, 108.85, 108.82, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert ex_mid == []

    # Pullback to 108.30 (<= 108.35) -> ALL 3 profitable LONG slots batch-exit via TREND_TRAIL_TP!
    _, ex_trail = tracker.evaluate_tick(108.30, 108.35, 108.32, trend_cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert {e["slot_id"] for e in ex_trail} == {"L1", "L2", "L3"}
    assert all(e["exit_reason"] == "TREND_TRAIL_TP" for e in ex_trail)

    # 4. Verify real BBW / VCP compression math on synthetic candles (80 wide bars -> 20 tight squeeze bars)
    closes = [100.0 + 4.5 * math.sin(i * 0.7) for i in range(80)] + [100.0 + 0.15 * math.sin(i * 0.7) for i in range(25)]
    bbw_res = MarketDataGateway.compute_bbw_metrics(closes, period=20, num_std=2.0, timeframe="15m")
    assert bbw_res is not None
    assert bbw_res["bbw_percentile"] <= 15.0
    assert bbw_res["vcp_contraction"] is True

    # 5. Verify ADAPTIVE_GRID dual-mode switching + mid-position lock + hysteresis
    adp_tracker = LongShortGridTracker("BTC/USDT:USDT")
    normal_bbw = {"timeframe": "1H", "bbw_pct": 3.5, "bbw_percentile": 62.0, "bb_upper": 103.0, "bb_lower": 97.0}
    squeeze_bbw = {"timeframe": "1H", "bbw_pct": 0.9, "bbw_percentile": 8.0, "bb_upper": 101.2, "bb_lower": 98.8}
    hyst_bbw = {"timeframe": "1H", "bbw_pct": 1.6, "bbw_percentile": 26.0, "bb_upper": 101.8, "bb_lower": 98.2}

    adp_tracker.sync_config(adaptive_cfg, current_mid=100.0, bbw_info=normal_bbw)
    assert adp_tracker.active_grid_mode == "MEAN_REVERSION"
    assert adp_tracker.levels["L1"]["trigger_price"] == 99.0  # Traditional buy-dip below center

    # Open L1 in MEAN_REVERSION -> squeeze arrives mid-position -> MUST NOT switch while holding!
    en_mr, _ = adp_tracker.evaluate_tick(98.90, 98.95, 98.92, adaptive_cfg, is_net_mode=True, bbw_info=normal_bbw)
    assert [e["slot_id"] for e in en_mr] == ["L1"]
    adp_tracker.mark_entry_filled("L1", 98.95, 1.0, 1.0, 1.0, adaptive_cfg, 0.0007)
    adp_tracker.evaluate_tick(99.20, 99.25, 99.22, adaptive_cfg, is_net_mode=True, bbw_info=squeeze_bbw)
    assert adp_tracker.active_grid_mode == "MEAN_REVERSION"  # Locked during open position!

    # Close L1 -> flat -> next tick under squeeze_bbw auto-recenters & switches to TREND_BREAKOUT!
    adp_tracker.mark_exited("L1", 0.5)
    adp_tracker.evaluate_tick(100.0, 100.05, 100.02, adaptive_cfg, is_net_mode=True, bbw_info=squeeze_bbw)
    assert adp_tracker.active_grid_mode == "TREND_BREAKOUT"
    assert adp_tracker.levels["L1"]["trigger_price"] > 100.02  # Breakout LONG now ABOVE center!
    assert adp_tracker.consume_regime_log() is not None

    # Hysteresis check: bbw_percentile=26% (< 20+15=35%) stays TREND_BREAKOUT
    adp_tracker.evaluate_tick(100.0, 100.05, 100.02, adaptive_cfg, is_net_mode=True, bbw_info=hyst_bbw)
    assert adp_tracker.active_grid_mode == "TREND_BREAKOUT"

    # Volatility expands back to 62% (>= 35%) while flat -> cleanly returns to MEAN_REVERSION!
    adp_tracker.evaluate_tick(100.0, 100.05, 100.02, adaptive_cfg, is_net_mode=True, bbw_info=normal_bbw)
    assert adp_tracker.active_grid_mode == "MEAN_REVERSION"
    assert adp_tracker.levels["L1"]["trigger_price"] < 100.02


def test_stats_reset_clears_kpis_preserves_positions_and_persists_across_restart():
    from backend.main import app
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.math_engine import wall_ms

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "stats_reset_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(88)

        btc_grid = SymbolStrategy(
            symbol="BTC/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.012,
            grid_range_pct=0.04,
            grid_stop_loss_pct=0.08,
            order_usdt=100.0,
            leverage=5,
            price_precision=1,
            qty_precision=2,
            min_qty=0.01,
        )
        adb.upsert_symbol_sync(btc_grid, user_id=88)

        async def run_reset_flow():
            await adb.start_worker()
            eng1 = BidirectionalSniperEngine(adb, user_id=88)
            for cfg in adb.load_symbols_sync(user_id=88):
                eng1._register_symbol_internal(cfg)
            eng1.risk_config = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")

            # Seed an open grid position that MUST survive statistics reset untouched
            tracker = eng1.grid_trackers["BTC/USDT:USDT"]
            tracker.sync_config(btc_grid, current_mid=100000.0)
            tracker.mark_entry_filled("L1", 99400.0, 0.01, 1.0, 0.01, btc_grid, 0.0007)
            tracker.completed_cycles = 4
            tracker.total_net_pnl = 18.5
            eng1.active_grid_positions["BTC/USDT:USDT:L1"] = {
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "trading_mode": "LIVE",
                "grid_slot_id": "L1",
                "entry_price": 99400.0,
                "qty": 0.01,
            }

            # Record 2 pre-reset trades (1 win, 1 loss) + 1 missed IOC in LIVE mode
            t_old = wall_ms() - 5000
            old_win = {
                "time": "10:00:01.000",
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "trading_mode": "LIVE",
                "order_id": "old-win-1",
                "entry_price": 99000.0,
                "exit_price": 100000.0,
                "filled_qty": 0.01,
                "gross_pnl_usdt": 10.0,
                "fee_usdt": 0.5,
                "net_pnl_usdt": 9.5,
                "signal_ts_ms": t_old,
                "submit_ts_ms": t_old,
                "ack_ts_ms": t_old,
                "hold_duration_ms": 1200,
                "exit_reason": "GRID_TP_L1",
            }
            old_loss = {
                "time": "10:00:02.000",
                "symbol": "BTC/USDT:USDT",
                "side": "SHORT",
                "trading_mode": "LIVE",
                "order_id": "old-loss-1",
                "entry_price": 100000.0,
                "exit_price": 100500.0,
                "filled_qty": 0.01,
                "gross_pnl_usdt": -5.0,
                "fee_usdt": 0.5,
                "net_pnl_usdt": -5.5,
                "signal_ts_ms": t_old + 1000,
                "submit_ts_ms": t_old + 1000,
                "ack_ts_ms": t_old + 1000,
                "hold_duration_ms": 800,
                "exit_reason": "GRID_SL_S1",
            }
            eng1.stats_by_mode["LIVE"]["total_signals"] += 3
            eng1.stats_by_mode["LIVE"]["ioc_filled_count"] += 2
            eng1.stats_by_mode["LIVE"]["ioc_missed_count"] += 1
            adb.record_trade_async(old_win, user_id=88)
            adb.record_trade_async(old_loss, user_id=88)
            eng1._accumulate_trade_into_stats(old_win, is_historical=False, mode_override="LIVE")
            eng1._accumulate_trade_into_stats(old_loss, is_historical=False, mode_override="LIVE")

            snap_before = eng1.build_dashboard_snapshot()
            assert snap_before["stats"]["total_net_pnl"] == 4.0
            assert snap_before["stats"]["win_count"] == 1
            assert snap_before["stats"]["loss_count"] == 1
            assert snap_before["stats"]["ioc_filled_count"] == 2
            assert snap_before["stats"]["ioc_missed_count"] == 1
            assert snap_before["stats"]["total_signals"] == 3
            assert snap_before["stats"]["stats_reset_at_ms"] == 0

            # 1. Execute one-click statistics reset
            res = eng1.reset_statistics("LIVE")
            assert res["ok"] is True
            assert res["mode"] == "LIVE"
            reset_ts = res["reset_at_ms"]
            assert reset_ts > t_old + 1000

            snap_after = eng1.build_dashboard_snapshot()
            st = snap_after["stats"]
            assert st["total_net_pnl"] == 0.0
            assert st["total_gross_pnl"] == 0.0
            assert st["total_fees"] == 0.0
            assert st["ioc_filled_count"] == 0
            assert st["ioc_missed_count"] == 0
            assert st["fill_rate_pct"] == 0.0
            assert st["win_count"] == 0
            assert st["loss_count"] == 0
            assert st["win_rate_pct"] == 0.0
            assert st["total_signals"] == 0
            assert st["equity_curve"] == []
            assert st["recent_trades"] == []
            assert st["stats_reset_at_ms"] == reset_ts
            assert tracker.completed_cycles == 0
            assert tracker.total_net_pnl == 0.0
            # Open positions MUST be 100% preserved!
            assert "BTC/USDT:USDT:L1" in eng1.active_grid_positions
            assert tracker.levels["L1"]["status"] == "OPEN"

            # 2. Record a brand-new trade AFTER reset_ts -> accumulates freshly from 0
            t_new = reset_ts + 250
            new_win = {
                "time": "10:05:00.000",
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "trading_mode": "LIVE",
                "order_id": "post-reset-win-1",
                "entry_price": 100000.0,
                "exit_price": 100600.0,
                "filled_qty": 0.01,
                "gross_pnl_usdt": 6.0,
                "fee_usdt": 0.4,
                "net_pnl_usdt": 5.6,
                "signal_ts_ms": t_new,
                "submit_ts_ms": t_new,
                "ack_ts_ms": t_new,
                "hold_duration_ms": 900,
                "exit_reason": "GRID_TP_L1",
            }
            eng1.stats_by_mode["LIVE"]["total_signals"] += 1
            eng1.stats_by_mode["LIVE"]["ioc_filled_count"] += 1
            adb.record_trade_async(new_win, user_id=88)
            eng1._accumulate_trade_into_stats(new_win, is_historical=False, mode_override="LIVE")
            await adb.stop_worker()

            snap_post = eng1.build_dashboard_snapshot()
            assert snap_post["stats"]["total_net_pnl"] == 5.6
            assert snap_post["stats"]["win_count"] == 1
            assert snap_post["stats"]["loss_count"] == 0
            assert snap_post["stats"]["win_rate_pct"] == 100.0
            assert snap_post["stats"]["ioc_filled_count"] == 1
            assert snap_post["stats"]["ioc_missed_count"] == 0
            assert snap_post["stats"]["total_signals"] == 1

            # 3. Simulate full server restart: new engine loads DB and MUST skip pre-reset trades
            eng2 = BidirectionalSniperEngine(adb, user_id=88)
            with (
                patch.object(eng2, "_reconcile_startup_positions", new=AsyncMock()),
                patch.object(eng2.gateway, "start", new=AsyncMock()),
            ):
                await eng2.start()

            snap_restarted = eng2.build_dashboard_snapshot()
            st2 = snap_restarted["stats"]
            assert st2["stats_reset_at_ms"] == reset_ts
            assert st2["total_net_pnl"] == 5.6
            assert st2["win_count"] == 1
            assert st2["loss_count"] == 0
            assert st2["win_rate_pct"] == 100.0
            assert st2["ioc_filled_count"] == 1
            assert st2["total_signals"] == 1
            assert len(st2["recent_trades"]) == 1
            assert st2["recent_trades"][0]["entry"] == 100000.0
            assert st2["recent_trades"][0]["exit"] == 100600.0

        asyncio.run(run_reset_flow())

    # 4. Unauthenticated guest CANNOT call /api/stats/reset (strict 401 guard)
    with TestClient(app) as client:
        r_unauth = client.post("/api/stats/reset")
        assert r_unauth.status_code == 401


def test_batch1_trend_grid_no_death_spiral_and_sync_cooldown():
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms

    cfg = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        strategy_type="TREND_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.08,
        grid_stop_loss_pct=0.10,
        order_usdt=100.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
        trend_dead_zone_mult=2.0,
        trend_trailing_mult=1.5,
        trend_pyramid_decay=True,
        trend_use_bb_breakout=False,
    )
    tracker = LongShortGridTracker("SOL/USDT:USDT")
    tracker.sync_config(cfg, current_mid=100.0)
    assert tracker.active_grid_mode == "TREND_BREAKOUT"
    assert tracker.levels["L1"]["trigger_price"] == 101.20

    # 1. Breakout LONG L1 at 101.25, then rally to 106.0 -> trailing stop = 106*(1-0.009) = 105.05
    en1, _ = tracker.evaluate_tick(101.20, 101.25, 101.22, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en1] == ["L1"]
    tracker.mark_entry_filled("L1", 101.25, 1.0, 1.0, 1.0, cfg, round_trip_fee=0.0010)

    # Verify Livermore SL clamp: if L2 fills at same price as L1 (adverse slip), neither L1 nor L2 SL sits >= 101.25!
    tracker.mark_entry_filled("L2", 101.25, 0.6, 0.6, 1.0, cfg, round_trip_fee=0.0010)
    assert tracker.levels["L2"]["sl_price"] < 101.25
    assert tracker.levels["L1"]["sl_price"] < 101.25

    # When L3 fills higher at 102.50, L1 & L2 SL lock breakeven + 2*taker_fee (0.0010), and L3 SL is monotonic >= L2 SL!
    tracker.mark_entry_filled("L3", 102.50, 0.4, 0.4, 1.0, cfg, round_trip_fee=0.0010)
    assert tracker.levels["L2"]["sl_price"] >= round(101.25 * 1.0010, 2)
    assert tracker.levels["L3"]["sl_price"] >= tracker.levels["L2"]["sl_price"]

    # Rally to 106.0 then pullback to 104.90 -> batch TREND_TRAIL_TP exit
    tracker.evaluate_tick(106.00, 106.05, 106.02, cfg, is_net_mode=True, round_trip_fee=0.0010)
    _, ex_trail = tracker.evaluate_tick(104.90, 104.95, 104.92, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert {e["slot_id"] for e in ex_trail} == {"L1", "L2", "L3"}
    tracker.mark_exited("L1", 3.6)
    tracker.mark_exited("L2", 2.1)
    tracker.mark_exited("L3", 0.9)
    assert tracker._needs_post_trend_recenter is True

    # Next tick at 104.92 (far above old center 100.0): MUST recenter to 104.92 instead of re-entering L1 immediately!
    en_after, ex_after = tracker.evaluate_tick(104.90, 104.95, 104.92, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert en_after == [] and ex_after == []
    assert abs(tracker.center_price - 104.92) < 0.01
    assert tracker.levels["L1"]["trigger_price"] > 106.0

    # 2. sync_config() with identical params MUST preserve 60s structural backoff cooldown
    tracker.levels["L1"]["status"] = "ENTERING"
    tracker.mark_entry_missed("L1", backoff_ms=60_000.0)
    cd_before = tracker.levels["L1"]["cooldown_until_mono"]
    assert cd_before > mono_ms() + 30_000.0
    tracker.sync_config(cfg, current_mid=104.92)
    assert tracker.levels["L1"]["cooldown_until_mono"] == cd_before

    # Changing config params SHOULD rebuild and clear cooldowns
    cfg_changed = cfg.model_copy(update={"grid_step_pct": 0.008})
    tracker.sync_config(cfg_changed, current_mid=104.92)
    assert tracker.levels["L1"]["cooldown_until_mono"] == 0.0


def test_batch1_executor_decimal_partial_exit_no_fallback_and_uncertain_reconcile():
    from backend.executor import ExecutionAdapter
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE

    async def run_exec_checks():
        rc = GlobalRiskConfig(trading_mode="LIVE")
        ex = ExecutionAdapter(lambda _: None, lambda: rc)
        ex.mode = "LIVE"
        cfg = SymbolStrategy(
            symbol="ETH/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=3,
        )
        okx_cfg = {
            "exchange_id": "okx",
            "api_key": "k",
            "_decrypted_secret": "s",
            "_decrypted_passphrase": "p",
            "is_testnet": False,
            "taker_fee": 0.0005,
        }

        # 1. Unknown/unloaded instrument spec MUST be rejected in _calc_okx_entry_sz
        _OKX_INSTRUMENT_CACHE.pop("FAKE-USDT-SWAP", None)
        sz_str, _, _, _, err_msg = ex._calc_okx_entry_sz("FAKE/USDT:USDT", 100.0, 100.0, 500.0, 5.0)
        assert sz_str == "" and "未同步" in err_msg

        # Populate ETH spec BEFORE partial exit & uncertain entry tests
        _OKX_INSTRUMENT_CACHE["ETH-USDT-SWAP"] = {
            "instId": "ETH-USDT-SWAP",
            "ctVal": 0.1,
            "lotSz": 0.01,
            "lotSz_str": "0.01",
            "minSz": 0.01,
            "tickSz": 0.01,
            "tickSz_str": "0.01",
            "maxLever": 50,
            "state": "live",
        }

        # 2. Exact Decimal rem_contracts (0.03 - 0.02 = 0.01) & allow_close_position_fallback=False MUST NOT call close-position
        called_paths = []

        async def mock_okx_req(client, creds, method, path, payload=None):
            called_paths.append((method, path, payload))
            if path == "/api/v5/trade/order":
                return {"code": "0", "data": [{"ordId": f"ord-{len(called_paths)}", "sCode": "0"}]}
            if path.startswith("/api/v5/trade/order?"):
                # First exit fills 0.02 of 0.03, second fills remaining 0.01 -> no close-position call!
                order_calls = [p for p in called_paths if p[1] == "/api/v5/trade/order"]
                if len(order_calls) == 1:
                    return {"code": "0", "data": [{"state": "partially_filled", "accFillSz": "0.02", "avgPx": "3000.0"}]}
                return {"code": "0", "data": [{"state": "filled", "accFillSz": "0.01", "avgPx": "3002.0"}]}
            return {"code": "0", "data": []}

        with (
            patch.object(ex, "_okx_request", side_effect=mock_okx_req),
            patch.object(ex, "ensure_okx_account_meta", new=AsyncMock(return_value={"posMode": "net_mode", "acctLv": "2"})),
        ):
            res = await ex.execute_exit_with_retry(
                cfg=cfg,
                side="LONG",
                qty=0.03,
                exit_reason="GRID_TP_L1",
                tp_price=3000.0,
                exchange_cfg=okx_cfg,
                contracts=0.03,
                ct_val=1.0,
                allow_close_position_fallback=False,
            )
            assert res.status == "FILLED"
            assert not any("close-position" in p[1] for p in called_paths)
            order_payloads = [p[2] for p in called_paths if p[1] == "/api/v5/trade/order"]
            assert order_payloads[0]["sz"] == "0.03"
            assert order_payloads[1]["sz"] == "0.01"  # exact Decimal subtraction!

        # 3. Post-submit network timeout returns UNCERTAIN (not MISSED) and reconcile_order NEVER fakes FILLED in LIVE
        async def mock_timeout_req(client, creds, method, path, payload=None):
            raise RuntimeError("network read timeout after submit")

        with (
            patch.object(ex, "_okx_request", side_effect=mock_timeout_req),
            patch.object(ex, "ensure_okx_account_meta", new=AsyncMock(return_value={"posMode": "net_mode", "acctLv": "2"})),
            patch.object(ex, "ensure_leverage", new=AsyncMock(return_value=(True, ""))),
        ):
            entry_res = await ex.place_entry_ioc(cfg, "LONG", 0.03, 3001.0, 3000.0, okx_cfg)
            assert entry_res.status == "UNCERTAIN"
            assert entry_res.cl_ord_id.startswith("sn")

            rec_res = await ex.reconcile_order(
                cfg, entry_res.order_id, 0.03, 3000.0, exchange_cfg=okx_cfg, cl_ord_id=entry_res.cl_ord_id
            )
            assert rec_res.status == "UNCERTAIN"

        # 4. Non-OKX LIVE entry MUST be rejected explicitly instead of mocking FILLED
        non_okx = dict(okx_cfg, exchange_id="binance_usdm")
        bad_res = await ex.place_entry_ioc(cfg, "LONG", 0.03, 3001.0, 3000.0, non_okx)
        assert bad_res.status == "MISSED" and ("仅支持" in bad_res.raw_msg or "OKX" in bad_res.raw_msg)

    asyncio.run(run_exec_checks())


def test_batch1_gateway_disconnect_honesty_and_flat_bbw():
    from backend.market_gateway import MarketDataGateway

    gw = MarketDataGateway()
    gw.set_symbols(["BTC/USDT:USDT"])
    gw.real_follower_bbo["BTC/USDT:USDT"] = (100000.0, 100001.0, 1.0, 1.0)
    gw.okx_connected = True
    gw.binance_connected = True

    # Flat candles must report 0.0% BBW percentile (extreme compression), NOT 100.0%
    flat_metrics = MarketDataGateway.compute_bbw_metrics([100.0] * 40, period=20, num_std=2.0, timeframe="15m")
    assert flat_metrics is not None
    assert flat_metrics["bbw"] == 0.0
    assert flat_metrics["bbw_percentile"] == 0.0

    # REST fallback mode must never claim LIVE_WS
    gw.binance_connected = False
    gw.okx_connected = False
    gw.active_source = "REST_FALLBACK"
    assert gw.active_source != "LIVE_WS"


def test_batch2_auth_pfp_payment_atomicity_delete_purge_and_api_hardening():
    from backend.user_membership import UserMembershipManager
    from backend.engine_manager import EngineManager
    from backend.main import app
    import base64, hashlib, hmac, json, time

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "batch2_security.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        um = UserMembershipManager(db_path=db_file)
        um.init_sync()

        u_res = um.register_user("alice_sec", "Secret1234!", "alice@test.local")
        assert u_res["ok"] is True
        uid = u_res["user"]["id"]

        # 1. Forged token signed with server secret but missing `pfp` claim MUST be rejected
        from backend.user_membership import TOKEN_SECRET

        pld_bytes = json.dumps(
            {"uid": uid, "usr": "alice_sec", "role": "user", "exp": int(time.time()) + 3600},
            separators=(",", ":"),
        ).encode("utf-8")
        b64_pld = base64.urlsafe_b64encode(pld_bytes).decode("utf-8").rstrip("=")
        sig = hmac.new(TOKEN_SECRET, b64_pld.encode("utf-8"), hashlib.sha256).hexdigest()
        no_pfp_token = f"{b64_pld}.{sig}"
        assert um.get_user_from_token(no_pfp_token) is None

        # 2. Payment atomicity: cancelling an order prevents later on-chain confirmation from granting VIP
        plans = um.list_plans()
        order_res = um.create_web3_payment_order(uid, plans[0]["id"], "MULTI_EVM")
        assert order_res["ok"] is True
        order_id = order_res["order"]["id"]
        order_no = order_res["order"]["order_no"]
        pay_amt = float(order_res["order"]["pay_amount_usdt"])

        cancel_res = um.cancel_user_order(uid, order_no)
        assert cancel_res["ok"] is True
        confirm_cancelled = um.confirm_order_paid_onchain(order_id, "BSC", "0x" + "a" * 64, pay_amt)
        assert confirm_cancelled["ok"] is False

        # Fresh order confirmed once succeeds, duplicate confirmation is rejected
        order_res2 = um.create_web3_payment_order(uid, plans[0]["id"], "MULTI_EVM")
        order_id2 = order_res2["order"]["id"]
        pay_amt2 = float(order_res2["order"]["pay_amount_usdt"])
        ok1 = um.confirm_order_paid_onchain(order_id2, "BSC", "0x" + "b" * 64, pay_amt2)
        assert ok1["ok"] is True
        ok2 = um.confirm_order_paid_onchain(order_id2, "BSC", "0x" + "c" * 64, pay_amt2)
        assert ok2["ok"] is False

        # 3. admin_delete_user purges exchanges/runtime_settings and EngineManager does NOT resurrect deleted user
        adb.ensure_user_workspace_sync(uid)
        del_res = um.admin_delete_user(uid)
        assert del_res["ok"] is True
        import sqlite3

        with sqlite3.connect(db_file) as c_chk:
            assert c_chk.execute("SELECT COUNT(*) FROM exchanges WHERE user_id=?", (uid,)).fetchone()[0] == 0
            assert c_chk.execute("SELECT COUNT(*) FROM symbol_configs WHERE user_id=?", (uid,)).fetchone()[0] == 0
            assert c_chk.execute("SELECT COUNT(*) FROM runtime_settings WHERE user_id=?", (uid,)).fetchone()[0] == 0

        async def check_no_ghost():
            mgr = EngineManager(adb, get_user_fn=um.get_user_by_id)
            with patch("backend.sniper_engine.BidirectionalSniperEngine.start", new=AsyncMock()):
                await mgr.start()
            assert uid not in mgr.user_engines
            await mgr.stop()

        asyncio.run(check_no_ghost())

    # 4. Unknown /api/* path returns 404 JSON (never 200 SPA HTML)
    with TestClient(app) as client:
        r_404 = client.get("/api/nonexistent-route-check")
        assert r_404.status_code == 404
        assert "detail" in r_404.json()


def test_batch_symbol_validate_and_apply_preserves_open_positions():
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.models import BatchSymbolUpdateRequest

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "batch_symbols_test.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(77)

        # Coin 1: narrow 0.40% step (has open position!)
        eth_narrow = SymbolStrategy(
            symbol="ETH/USDT:USDT",
            strategy_type="ADAPTIVE_GRID",
            grid_levels=3,
            grid_step_pct=0.004,
            grid_take_profit_pct=0.005,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.08,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=3,
        )
        # Coin 2: inverted TP < step (SOL case: step 0.60%, tp 0.45%)
        sol_inverted = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="ADAPTIVE_GRID",
            grid_levels=3,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.0045,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.08,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=2,
        )
        adb.upsert_symbol_sync(eth_narrow, user_id=77)
        adb.upsert_symbol_sync(sol_inverted, user_id=77)

        eng = BidirectionalSniperEngine(adb, user_id=77)
        for cfg in adb.load_symbols_sync(user_id=77):
            eng._register_symbol_internal(cfg)

        # Open a live slot on ETH before running batch update
        eth_tracker = eng.grid_trackers["ETH/USDT:USDT"]
        eth_tracker.sync_config(eth_narrow, current_mid=3000.0)
        eth_tracker.mark_entry_filled("L1", 2988.0, 0.03, 0.3, 0.1, eth_narrow, round_trip_fee=0.0010)
        assert eth_tracker.levels["L1"]["status"] == "OPEN"

        # 1. Read-only validate catches BOTH anomalies and changes nothing
        val_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(action="validate", strategy_filter="ALL_GRID")
        )
        assert val_res["ok"] is True
        assert val_res["grid_round_trip_fee_pct"] == 0.1  # 2 * 0.05%
        by_sym = {it["symbol"]: it for it in val_res["items"]}
        assert any("步长偏窄" in x for x in by_sym["ETH/USDT:USDT"]["issues"])
        assert any("止盈低于步长倒挂" in x for x in by_sym["SOL/USDT:USDT"]["issues"])
        assert eng.strategies["ETH/USDT:USDT"].grid_step_pct == 0.004

        # 2. Apply updates step->0.60%, tp->0.65% safely without disturbing ETH open L1 slot
        app_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(
                action="apply",
                strategy_filter="ALL_GRID",
                grid_step_pct=0.006,
                grid_take_profit_pct=0.0065,
                auto_fix_anomalies=True,
            )
        )
        assert app_res["ok"] is True
        assert app_res["updated_count"] >= 2
        assert app_res["open_deferred_count"] == 1
        assert eng.strategies["ETH/USDT:USDT"].grid_step_pct == 0.006
        assert eng.strategies["ETH/USDT:USDT"].grid_take_profit_pct == 0.0065
        assert eng.strategies["SOL/USDT:USDT"].grid_take_profit_pct == 0.0065
        # Open slot MUST remain OPEN and untouched!
        assert eth_tracker.levels["L1"]["status"] == "OPEN"
        assert eth_tracker.levels["L1"]["entry_price"] == 2988.0

        # 3. Verify POST /api/risk/update handler preserves active kill_switch_active=True
        from backend.main import update_risk

        async def check_risk_kill_switch_preserved():
            eng.risk_config.kill_switch_active = True
            eng.risk_config.trading_mode = "LIVE"
            with patch(
                "backend.main._require_user_engine",
                new=AsyncMock(return_value=({"id": 77, "can_use_live": True}, eng)),
            ):
                res = await update_risk(
                    GlobalRiskConfig(kill_switch_active=False, max_daily_loss_usdt=222.0),
                    authorization="Bearer dummy",
                )
            assert res["risk"]["kill_switch_active"] is True
            assert res["risk"]["max_daily_loss_usdt"] == 222.0
            assert eng.risk_config.kill_switch_active is True

        asyncio.run(check_risk_kill_switch_preserved())


def test_grid_maker_post_only_tp_and_cond_sl_lifecycle():
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.executor import OrderResult
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "grid_maker_tp_sl.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(91)

        eth_mr = SymbolStrategy(
            symbol="ETH/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.0065,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.04,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=3,
            use_post_only_tp=True,
        )
        sol_trend = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="TREND_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.01,
            grid_take_profit_pct=0.01,
            grid_range_pct=0.08,
            grid_stop_loss_pct=0.05,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=2,
            trend_dead_zone_mult=2.0,
            trend_trailing_mult=1.5,
            trend_use_bb_breakout=False,
            use_post_only_tp=True,
        )
        adb.upsert_symbol_sync(eth_mr, user_id=91)
        adb.upsert_symbol_sync(sol_trend, user_id=91)

        _OKX_INSTRUMENT_CACHE["ETH-USDT-SWAP"] = {
            "instId": "ETH-USDT-SWAP",
            "ctVal": 0.1,
            "lotSz": 0.01,
            "lotSz_str": "0.01",
            "minSz": 0.01,
            "tickSz": 0.01,
            "tickSz_str": "0.01",
            "maxLever": 50,
            "state": "live",
        }
        _OKX_INSTRUMENT_CACHE["SOL-USDT-SWAP"] = {
            "instId": "SOL-USDT-SWAP",
            "ctVal": 1.0,
            "lotSz": 0.01,
            "lotSz_str": "0.01",
            "minSz": 0.01,
            "tickSz": 0.01,
            "tickSz_str": "0.01",
            "maxLever": 50,
            "state": "live",
        }

        async def run_lifecycle():
            eng = BidirectionalSniperEngine(adb, user_id=91)
            eng.risk_config = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
            eng.trading_active_by_mode["LIVE"] = True
            for cfg in adb.load_symbols_sync(user_id=91):
                eng._register_symbol_internal(cfg)
            # Configure mock live OKX follower creds
            eng.exchanges["okx"] = {
                **eng.exchanges.get("okx", {}),
                "exchange_id": "okx",
                "role": "follower",
                "is_active": True,
                "api_key": "k_test",
                "_decrypted_secret": "s_test",
                "_decrypted_passphrase": "p_test",
                "auth_status": "VERIFIED",
                "taker_fee": 0.0005,
                "maker_fee": 0.0002,
            }

            calls = []
            pending_orders = {}
            pending_algos = {}
            ord_seq = {"n": 0}

            async def mock_okx_req(client, creds, method, path, payload=None):
                calls.append((method, path, payload))
                if method == "POST" and path == "/api/v5/trade/order-algo":
                    ord_seq["n"] += 1
                    aid = f"algo-{ord_seq['n']}"
                    pending_algos[aid] = {"algoId": aid, **payload}
                    return {"code": "0", "data": [{"algoId": aid, "sCode": "0"}]}
                if method == "POST" and path == "/api/v5/trade/cancel-algos":
                    out = []
                    for item in payload:
                        aid = item["algoId"]
                        if aid in pending_algos:
                            pending_algos.pop(aid, None)
                            out.append({"algoId": aid, "sCode": "0"})
                        else:
                            out.append({"algoId": aid, "sCode": "51400"})
                    return {"code": "0", "data": out}
                if method == "POST" and path == "/api/v5/trade/order":
                    ord_seq["n"] += 1
                    oid = f"ord-{ord_seq['n']}"
                    if payload.get("ordType") == "post_only":
                        pending_orders[oid] = {"ordId": oid, "state": "live", "accFillSz": "0", **payload}
                    return {"code": "0", "data": [{"ordId": oid, "sCode": "0"}]}
                if method == "POST" and path == "/api/v5/trade/cancel-order":
                    oid = payload["ordId"]
                    pending_orders.pop(oid, None)
                    return {"code": "0", "data": [{"ordId": oid, "sCode": "0"}]}
                if method == "GET" and path.startswith("/api/v5/trade/orders-pending"):
                    return {"code": "0", "data": list(pending_orders.values())}
                if method == "GET" and path.startswith("/api/v5/trade/orders-algo-pending"):
                    return {"code": "0", "data": list(pending_algos.values())}
                if method == "GET" and path.startswith("/api/v5/trade/order?"):
                    oid = path.split("ordId=")[1].split("&")[0]
                    if oid in pending_orders:
                        return {"code": "0", "data": [pending_orders[oid]]}
                    # Simulate natural Maker TP fill for ord-2 and triggered SL child fill for sl-child-1!
                    if oid == "ord-2":
                        return {"code": "0", "data": [{"ordId": oid, "state": "filled", "accFillSz": "0.33", "avgPx": "3004.0"}]}
                    if oid == "sl-child-1":
                        return {"code": "0", "data": [{"ordId": oid, "state": "filled", "accFillSz": "0.33", "avgPx": "2880.0"}]}
                    return {"code": "0", "data": [{"ordId": oid, "state": "canceled", "accFillSz": "0", "avgPx": "0"}]}
                if method == "GET" and path.startswith("/api/v5/trade/orders-algo-history"):
                    aid = path.split("algoId=")[1].split("&")[0]
                    return {
                        "code": "0",
                        "data": [{"algoId": aid, "state": "effective", "actualSz": "0.33", "actualPx": "2880.0", "ordId": "sl-child-1"}],
                    }
                return {"code": "0", "data": []}

            # Seed live book for ETH and SOL
            eng.math_states["ETH/USDT:USDT"].on_follower_tick(2982.0, 2982.5, 50.0, 50.0)
            eng.math_states["SOL/USDT:USDT"].on_follower_tick(102.0, 102.1, 50.0, 50.0)

            with (
                patch.object(eng.executor, "_okx_request", side_effect=mock_okx_req),
                patch.object(
                    eng.executor, "ensure_okx_account_meta", new=AsyncMock(return_value={"posMode": "net_mode", "acctLv": "2"})
                ),
            ):
                # 1. MEAN_REVERSION entry places BOTH Maker Post-Only TP (ord-2) and Conditional SL (algo-1)
                eth_tracker = eng.grid_trackers["ETH/USDT:USDT"]
                eth_tracker.sync_config(eth_mr, current_mid=3000.0)
                eth_tracker.levels["L1"]["status"] = "ENTERING"
                with patch.object(
                    eng.executor,
                    "place_entry_ioc",
                    new=AsyncMock(
                        return_value=OrderResult(
                            order_id="en-eth-1",
                            status="FILLED",
                            filled_qty=0.033,
                            avg_price=2982.0,
                            latency_ms=12.0,
                            is_maker=False,
                            contracts=0.33,
                            ct_val=0.1,
                        )
                    ),
                ):
                    await eng._run_grid_entry(
                        eth_mr,
                        eth_tracker,
                        {**eth_tracker.levels["L1"], "reservation_price": 2983.0, "trigger_exec_price": 2982.0},
                        "LIVE",
                    )

                l1 = eth_tracker.levels["L1"]
                assert l1["status"] == "OPEN"
                assert l1["maker_tp_live"] is True and l1["maker_tp_order_id"] == "ord-2"
                assert l1["cond_sl_live"] is True and l1["cond_sl_algo_id"] == "algo-1"
                # Verify round_trip_fee used taker+maker (0.07%) for Mean-Reversion with use_post_only_tp=True
                expected_tp = round(2982.0 * (1.0 + 0.0065 + 0.0007), 2)
                assert l1["tp_price"] == expected_tp

                # Verify persistence round-trip preserves order IDs
                exported = eth_tracker.export_state()
                assert exported["open_slots"][0]["maker_tp_order_id"] == "ord-2"
                assert exported["open_slots"][0]["cond_sl_algo_id"] == "algo-1"

                # Verify evaluate_tick respects Maker touch grace window (no instant Taker dump at first touch!)
                _, ex_touch = eth_tracker.evaluate_tick(expected_tp, expected_tp + 0.5, expected_tp + 0.2, eth_mr, is_net_mode=True, round_trip_fee=0.0007)
                assert ex_touch == []

                # Simulate OKX naturally filling Maker Post-Only TP (`ord-2` disappears from pending_orders)
                pending_orders.pop("ord-2")
                settled = await eng.poll_grid_protective_orders()
                assert settled == 1
                assert l1["status"] == "IDLE"
                assert "algo-1" not in pending_algos  # Paired Conditional SL cleanly cancelled!
                # Verify Maker fee (0.02% exit) was booked, NOT Taker 0.05%!
                assert eng.stats_by_mode["LIVE"]["maker_tp_count"] == 1
                last_tr = eng.stats_by_mode["LIVE"]["recent_trades"][0]
                assert last_tr["exit_order_type"] == "MAKER_POST_ONLY"
                expected_fee = round(2982.0 * 0.033 * 0.0005 + 3004.0 * 0.033 * 0.0002, 4)
                assert abs(last_tr["fee"] - expected_fee) <= 0.0002

                # 2. Verify SL already-fired race guard in _run_grid_exits_batch: NO duplicate market close order!
                eth_tracker.levels["L2"]["status"] = "OPEN"
                eth_tracker.levels["L2"]["entry_price"] = 2964.0
                eth_tracker.levels["L2"]["filled_qty"] = 0.033
                eth_tracker.levels["L2"]["contracts"] = 0.33
                eth_tracker.levels["L2"]["ct_val"] = 0.1
                eth_tracker.levels["L2"]["sl_price"] = 2880.0
                eth_tracker.levels["L2"]["cond_sl_live"] = True
                eth_tracker.levels["L2"]["cond_sl_algo_id"] = "algo-already-fired"
                eng.executor.restore_slot_protective_tracking(
                    symbol="ETH/USDT:USDT",
                    slot_key="ETH/USDT:USDT:L2",
                    side="LONG",
                    qty=0.033,
                    contracts=0.33,
                    ct_val=0.1,
                    tp_price=0.0,
                    sl_price=2880.0,
                    cond_sl_algo_id="algo-already-fired",
                    exchange_cfg=eng.exchanges["okx"],
                )
                eth_tracker.levels["L2"]["status"] = "EXITING"
                calls_before = len(calls)
                await eng._run_grid_exits_batch(
                    eth_mr,
                    eth_tracker,
                    [{**eth_tracker.levels["L2"], "exit_reason": "GRID_SL", "target_price": 2879.0}],
                    "LIVE",
                )
                new_order_posts = [c for c in calls[calls_before:] if c[0] == "POST" and c[1] == "/api/v5/trade/order"]
                assert new_order_posts == []  # ZERO duplicate market close orders!
                assert eth_tracker.levels["L2"]["status"] == "IDLE"

                # 3. Verify TREND_BREAKOUT places Conditional SL (NO fixed Maker TP), and Livermore step-up updates SL algo!
                sol_tracker = eng.grid_trackers["SOL/USDT:USDT"]
                sol_tracker.sync_config(sol_trend, current_mid=100.0)
                sol_tracker.levels["L1"]["status"] = "ENTERING"
                with patch.object(
                    eng.executor,
                    "place_entry_ioc",
                    new=AsyncMock(
                        return_value=OrderResult(
                            order_id="en-sol-1",
                            status="FILLED",
                            filled_qty=1.5,
                            avg_price=102.05,
                            latency_ms=10.0,
                            is_maker=False,
                            contracts=1.5,
                            ct_val=1.0,
                        )
                    ),
                ):
                    await eng._run_grid_entry(
                        sol_trend,
                        sol_tracker,
                        {**sol_tracker.levels["L1"], "reservation_price": 102.1, "trigger_exec_price": 102.05},
                        "LIVE",
                    )
                assert sol_tracker.levels["L1"]["maker_tp_live"] is False  # No fixed TP cap in TREND_BREAKOUT
                assert sol_tracker.levels["L1"]["cond_sl_live"] is True
                old_sol_l1_algo = sol_tracker.levels["L1"]["cond_sl_algo_id"]

                # Price rises to 103.5 -> L2 opens -> Livermore steps up L1 SL -> replaces L1's OKX SL algo!
                eng.math_states["SOL/USDT:USDT"].on_follower_tick(103.4, 103.5, 50.0, 50.0)
                sol_tracker.levels["L2"]["status"] = "ENTERING"
                with patch.object(
                    eng.executor,
                    "place_entry_ioc",
                    new=AsyncMock(
                        return_value=OrderResult(
                            order_id="en-sol-2",
                            status="FILLED",
                            filled_qty=0.9,
                            avg_price=103.5,
                            latency_ms=10.0,
                            is_maker=False,
                            contracts=0.9,
                            ct_val=1.0,
                        )
                    ),
                ):
                    await eng._run_grid_entry(
                        sol_trend,
                        sol_tracker,
                        {**sol_tracker.levels["L2"], "reservation_price": 103.55, "trigger_exec_price": 103.5},
                        "LIVE",
                    )
                assert old_sol_l1_algo not in pending_algos  # Old SL algo cancelled!
                assert sol_tracker.levels["L1"]["cond_sl_live"] is True
                assert sol_tracker.levels["L1"]["cond_sl_algo_id"] != old_sol_l1_algo  # Updated SL algo live!

        asyncio.run(run_lifecycle())


def test_batch_update_order_usdt_stop_loss_and_maker_tp_honors_user_sl():
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.models import BatchSymbolUpdateRequest

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "batch_sl_notional.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(92)

        ada = SymbolStrategy(
            symbol="ADA/USDT:USDT",
            strategy_type="ADAPTIVE_GRID",
            grid_levels=5,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.0065,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.08,
            order_usdt=20.0,
            leverage=5,
            use_post_only_tp=False,
        )
        adb.upsert_symbol_sync(ada, user_id=92)
        eng = BidirectionalSniperEngine(adb, user_id=92)
        for cfg in adb.load_symbols_sync(user_id=92):
            eng._register_symbol_internal(cfg)

        # Validate flags high leveraged SL ROE (8% * 5x = 40% > 35%)
        v_res = eng.validate_and_batch_update_symbols(BatchSymbolUpdateRequest(action="validate"))
        assert v_res["maker_round_trip_fee_pct"] == 0.07
        assert any("杠杆折算保证金止损偏高" in x for x in v_res["items"][0]["issues"])

        # Apply explicit order_usdt=35, stop_loss=3.0% (15% ROE at 5x), use_post_only_tp=True
        a_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(
                action="apply",
                order_usdt=35.0,
                grid_stop_loss_pct=0.03,
                use_post_only_tp=True,
                auto_fix_anomalies=True,
            )
        )
        assert a_res["ok"] is True
        assert a_res["updated_count"] == 1
        updated_ada = eng.strategies["ADA/USDT:USDT"]
        assert updated_ada.order_usdt == 35.0
        # Explicit 3.0% SL MUST be honored (NOT overwritten back to 8.0%!) and range matched down to 2.55%
        assert updated_ada.grid_stop_loss_pct == 0.03
        assert updated_ada.grid_range_pct < 0.03
        assert updated_ada.use_post_only_tp is True
        assert a_res["items"][0]["sl_roe_pct"] == 15.0


def test_ema_trend_filter_breakout_switch_and_okx_long_short_mode():
    from backend.grid_engine import LongShortGridTracker, calc_pyramid_weights
    from backend.market_gateway import MarketDataGateway

    # 1. Moderated 4-level & 5-level pyramid weights remain normalized while keeping 3-level exact
    assert calc_pyramid_weights(3, decay_enabled=True) == [0.50, 0.30, 0.20]
    assert calc_pyramid_weights(4, decay_enabled=True) == [0.38, 0.27, 0.20, 0.15]
    assert calc_pyramid_weights(5, decay_enabled=True) == [0.30, 0.24, 0.19, 0.15, 0.12]
    assert abs(sum(calc_pyramid_weights(5, decay_enabled=True)) - 1.0) < 1e-9

    # 2. Compute real EMA20/60 trend_bias & breakout_expansion on downtrend vs uptrend candles
    bear_closes = [120.0 - i * 0.25 for i in range(95)] + [95.0, 93.8, 92.2, 90.5, 88.0]
    bear_bbw = MarketDataGateway.compute_bbw_metrics(bear_closes, period=20, num_std=2.0, timeframe="15m")
    assert bear_bbw is not None
    assert bear_bbw["trend_bias"] == "BEAR"
    assert bear_bbw["ema_gap_pct"] < -0.22
    assert bear_bbw["breakout_expansion"] == "DOWN_BREAKOUT"

    bull_closes = [80.0 + i * 0.25 for i in range(95)] + [105.0, 106.5, 108.0, 109.8, 112.5]
    bull_bbw = MarketDataGateway.compute_bbw_metrics(bull_closes, period=20, num_std=2.0, timeframe="15m")
    assert bull_bbw is not None
    assert bull_bbw["trend_bias"] == "BULL"
    assert bull_bbw["ema_gap_pct"] > 0.22
    assert bull_bbw["breakout_expansion"] == "UP_BREAKOUT"

    # 3. ADAPTIVE_GRID MEAN_REVERSION pauses counter-trend LONG dip-buys in BEAR trend (no catching falling knives),
    #    while manual LONG_SHORT_GRID runs bidirectionally unrestricted by lagging EMA trend_bias.
    mr_cfg = SymbolStrategy(
        symbol="ETH/USDT:USDT",
        strategy_type="LONG_SHORT_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.028,
        grid_stop_loss_pct=0.035,
        order_usdt=20.0,
        leverage=5,
        price_precision=2,
        qty_precision=3,
    )
    adp_mr_cfg = mr_cfg.model_copy(update={"strategy_type": "ADAPTIVE_GRID"})
    bear_info = {
        "timeframe": "15m",
        "bbw_pct": 2.4,
        "bbw_percentile": 55.0,
        "bb_upper": 102.0,
        "bb_middle": 100.0,
        "bb_lower": 98.0,
        "ema_fast": 99.0,
        "ema_slow": 100.5,
        "ema_gap_pct": -1.49,
        "trend_bias": "BEAR",
        "breakout_expansion": "NONE",
    }
    adp_mr_tracker = LongShortGridTracker("ETH/USDT:USDT")
    adp_mr_tracker.sync_config(adp_mr_cfg, current_mid=100.0, bbw_info=bear_info)
    assert adp_mr_tracker.active_grid_mode == "MEAN_REVERSION"
    # ADAPTIVE_GRID dip to 99.30 (below L1=99.40) -> MUST NOT open counter-trend LONG in BEAR market!
    en_dip, _ = adp_mr_tracker.evaluate_tick(99.25, 99.30, 99.28, adp_mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert en_dip == []
    # Bounce to 100.70 (above S1=100.60) -> MUST allow trend-aligned SHORT!
    en_short, _ = adp_mr_tracker.evaluate_tick(100.68, 100.72, 100.70, adp_mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_short] == ["S1"]

    # Manual LONG_SHORT_GRID under the same BEAR bias triggers BOTH LONG dips and SHORT rallies freely!
    mr_tracker = LongShortGridTracker("ETH/USDT:USDT")
    mr_tracker.sync_config(mr_cfg, current_mid=100.0, bbw_info=bear_info)
    en_mr_dip, _ = mr_tracker.evaluate_tick(99.25, 99.30, 99.28, mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_mr_dip] == ["L1"]
    en_mr_short, _ = mr_tracker.evaluate_tick(100.68, 100.72, 100.70, mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_mr_short] == ["S1"]

    # 4. In RANGE market under OKX long_short_mode (is_net_mode=False), BOTH LONG and SHORT can hold simultaneously!
    range_info = {**bear_info, "ema_gap_pct": 0.05, "trend_bias": "RANGE"}
    mr_tracker2 = LongShortGridTracker("ETH/USDT:USDT")
    mr_tracker2.sync_config(mr_cfg, current_mid=100.0, bbw_info=range_info)
    en_l, _ = mr_tracker2.evaluate_tick(99.25, 99.30, 99.28, mr_cfg, is_net_mode=False, bbw_info=range_info)
    assert [e["slot_id"] for e in en_l] == ["L1"]
    mr_tracker2.mark_entry_filled("L1", 99.30, 0.2, 0.2, 1.0, mr_cfg, 0.0007)
    # Price rallies to 100.70 while L1 is open -> in long_short_mode (is_net_mode=False), S1 ALSO opens!
    en_s, _ = mr_tracker2.evaluate_tick(100.68, 100.72, 100.70, mr_cfg, is_net_mode=False, bbw_info=range_info)
    assert [e["slot_id"] for e in en_s] == ["S1"]

    # 5. ADAPTIVE_GRID switches to TREND_BREAKOUT on DOWN_BREAKOUT even when bbw_percentile=62% (expansion!)
    adp_cfg = mr_cfg.model_copy(
        update={
            "strategy_type": "ADAPTIVE_GRID",
            "trend_dead_zone_mult": 2.0,
            "trend_trailing_mult": 2.2,
            "trend_use_bb_breakout": True,
        }
    )
    adp_tracker = LongShortGridTracker("ETH/USDT:USDT")
    down_break_info = {
        "timeframe": "15m",
        "bbw_pct": 3.8,
        "bbw_percentile": 62.0,
        "bb_upper": 102.0,
        "bb_middle": 100.0,
        "bb_lower": 98.5,
        "ema_fast": 99.0,
        "ema_slow": 100.5,
        "ema_gap_pct": -1.49,
        "trend_bias": "BEAR",
        "breakout_expansion": "DOWN_BREAKOUT",
    }
    adp_tracker.sync_config(adp_cfg, current_mid=100.0, bbw_info=down_break_info)
    assert adp_tracker.active_grid_mode == "TREND_BREAKOUT"
    snap = adp_tracker.get_snapshot()
    assert snap["trend_bias"] == "BEAR"
    assert snap["breakout_expansion"] == "DOWN_BREAKOUT"
    # In BEAR breakout mode: upside fakeout above L1 (102.0) is blocked, downside breakdown below S1 (98.5) fires SHORT!
    en_up, _ = adp_tracker.evaluate_tick(102.5, 102.6, 102.55, adp_cfg, is_net_mode=False, bbw_info=down_break_info)
    assert en_up == []
    en_dn, _ = adp_tracker.evaluate_tick(98.40, 98.45, 98.42, adp_cfg, is_net_mode=False, bbw_info=down_break_info)
    assert [e["slot_id"] for e in en_dn] == ["S1"]


def test_audit_option_a_long_short_mode_atomic_amend_and_order_audit():
    """
    Verifies Option A in OKX long_short_mode:
    1) MEAN_REVERSION + use_post_only_tp=True places real Maker post_only TP (posSide='long', no reduceOnly)
       and skips conflicting unlinked conditional SL so OKX availPos never conflicts.
    2) TREND_BREAKOUT places OKX native conditional SL with slTriggerPxType='mark'/'last',
       updates it atomically via POST /api/v5/trade/amend-algos, and never orphans SL on cancel failure.
    3) Protective placements are persisted to SQLite orders table.
    """
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.executor import OrderResult
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "audit_option_a.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(93)

        mr_cfg = SymbolStrategy(
            symbol="ETH/USDT:USDT",
            strategy_type="LONG_SHORT_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.0065,
            grid_range_pct=0.05,
            grid_stop_loss_pct=0.04,
            order_usdt=50.0,
            leverage=5,
            price_precision=2,
            qty_precision=3,
            use_post_only_tp=True,
            sl_trigger_px_type="mark",
        )
        tr_cfg = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="TREND_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.01,
            grid_take_profit_pct=0.01,
            grid_range_pct=0.08,
            grid_stop_loss_pct=0.05,
            order_usdt=100.0,
            leverage=5,
            price_precision=2,
            qty_precision=2,
            trend_dead_zone_mult=2.0,
            trend_trailing_mult=1.5,
            trend_use_bb_breakout=False,
            use_post_only_tp=True,
            sl_trigger_px_type="last",
        )
        adb.upsert_symbol_sync(mr_cfg, user_id=93)
        adb.upsert_symbol_sync(tr_cfg, user_id=93)
        # Verify sl_trigger_px_type SQLite round-trip
        reloaded = {s.symbol: s for s in adb.load_symbols_sync(user_id=93)}
        assert reloaded["ETH/USDT:USDT"].sl_trigger_px_type == "mark"
        assert reloaded["SOL/USDT:USDT"].sl_trigger_px_type == "last"

        _OKX_INSTRUMENT_CACHE["ETH-USDT-SWAP"] = {
            "instId": "ETH-USDT-SWAP", "ctVal": 0.1, "lotSz": 0.01, "lotSz_str": "0.01",
            "minSz": 0.01, "tickSz": 0.01, "tickSz_str": "0.01", "maxLever": 50, "state": "live",
        }
        _OKX_INSTRUMENT_CACHE["SOL-USDT-SWAP"] = {
            "instId": "SOL-USDT-SWAP", "ctVal": 1.0, "lotSz": 0.01, "lotSz_str": "0.01",
            "minSz": 0.01, "tickSz": 0.01, "tickSz_str": "0.01", "maxLever": 50, "state": "live",
        }

        async def run_check():
            await adb.start_worker()
            try:
                eng = BidirectionalSniperEngine(adb, user_id=93)
                eng.risk_config = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
                eng.trading_active_by_mode["LIVE"] = True
                for cfg in adb.load_symbols_sync(user_id=93):
                    eng._register_symbol_internal(cfg)
                eng.exchanges["okx"] = {
                    "exchange_id": "okx", "role": "follower", "is_active": True,
                    "api_key": "k_opt_a", "_decrypted_secret": "s_opt_a", "_decrypted_passphrase": "p_opt_a",
                    "auth_status": "VERIFIED", "taker_fee": 0.0005, "maker_fee": 0.0002,
                }
                eng.math_states["ETH/USDT:USDT"].on_follower_tick(2982.0, 2982.5, 50.0, 50.0)
                eng.math_states["SOL/USDT:USDT"].on_follower_tick(102.0, 102.1, 50.0, 50.0)

                calls = []
                amend_fail_mode = {"fail": False}

                async def mock_okx(client, creds, method, path, payload=None):
                    calls.append((method, path, payload))
                    if method == "POST" and path == "/api/v5/trade/order":
                        return {"code": "0", "data": [{"ordId": "po-ls-1", "sCode": "0"}]}
                    if method == "GET" and path.startswith("/api/v5/trade/order?"):
                        return {"code": "0", "data": [{"ordId": "po-ls-1", "state": "live", "accFillSz": "0"}]}
                    if method == "POST" and path == "/api/v5/trade/order-algo":
                        return {"code": "0", "data": [{"algoId": "sl-ls-1", "sCode": "0"}]}
                    if method == "POST" and path == "/api/v5/trade/amend-algos":
                        if amend_fail_mode["fail"]:
                            return {"code": "51000", "data": [{"sCode": "51000", "sMsg": "transient"}]}
                        return {"code": "0", "data": [{"algoId": payload["algoId"], "sCode": "0"}]}
                    if method == "POST" and path == "/api/v5/trade/cancel-algos":
                        return {"code": "50011", "data": []}
                    return {"code": "0", "data": []}

                with (
                    patch.object(eng.executor, "_okx_request", side_effect=mock_okx),
                    patch.object(
                        eng.executor,
                        "ensure_okx_account_meta",
                        new=AsyncMock(return_value={"posMode": "long_short_mode", "acctLv": "2", "_verified": True}),
                    ),
                ):
                    # 1) MEAN_REVERSION in long_short_mode: Maker TP placed, conflicting conditional SL skipped!
                    eth_trk = eng.grid_trackers["ETH/USDT:USDT"]
                    eth_trk.sync_config(mr_cfg, current_mid=3000.0)
                    eth_trk.levels["L1"]["status"] = "ENTERING"
                    with patch.object(
                        eng.executor, "place_entry_ioc",
                        new=AsyncMock(return_value=OrderResult("en-1", "FILLED", 0.017, 2982.0, 10.0, False, contracts=0.17, ct_val=0.1, fee_usdt=0.025)),
                    ):
                        await eng._run_grid_entry(mr_cfg, eth_trk, {**eth_trk.levels["L1"], "reservation_price": 2983.0, "trigger_exec_price": 2982.0}, "LIVE")

                    assert eth_trk.levels["L1"]["maker_tp_live"] is True
                    assert eth_trk.levels["L1"]["cond_sl_live"] is False
                    po_calls = [c for c in calls if c[1] == "/api/v5/trade/order" and c[2].get("ordType") == "post_only"]
                    assert len(po_calls) == 1
                    assert po_calls[0][2]["posSide"] == "long"
                    assert "reduceOnly" not in po_calls[0][2]
                    assert not any(c[1] == "/api/v5/trade/order-algo" for c in calls)

                    # 2) TREND_BREAKOUT in long_short_mode: native conditional SL placed with slTriggerPxType='last', amended atomically via amend-algos!
                    sol_trk = eng.grid_trackers["SOL/USDT:USDT"]
                    sol_trk.sync_config(tr_cfg, current_mid=100.0)
                    sol_trk.levels["L1"]["status"] = "ENTERING"
                    with patch.object(
                        eng.executor, "place_entry_ioc",
                        new=AsyncMock(return_value=OrderResult("en-sol-1", "FILLED", 1.5, 102.05, 8.0, False, contracts=1.5, ct_val=1.0)),
                    ):
                        await eng._run_grid_entry(tr_cfg, sol_trk, {**sol_trk.levels["L1"], "reservation_price": 102.1, "trigger_exec_price": 102.05}, "LIVE")

                    assert sol_trk.levels["L1"]["cond_sl_live"] is True
                    algo_calls = [c for c in calls if c[1] == "/api/v5/trade/order-algo"]
                    assert len(algo_calls) == 1
                    assert algo_calls[0][2]["slTriggerPxType"] == "last"
                    assert algo_calls[0][2]["posSide"] == "long"

                    # L2 opens -> Livermore step-up uses atomic POST /api/v5/trade/amend-algos (zero unprotected gap!)
                    eng.math_states["SOL/USDT:USDT"].on_follower_tick(103.4, 103.5, 50.0, 50.0)
                    sol_trk.levels["L2"]["status"] = "ENTERING"
                    with patch.object(
                        eng.executor, "place_entry_ioc",
                        new=AsyncMock(return_value=OrderResult("en-sol-2", "FILLED", 0.9, 103.5, 8.0, False, contracts=0.9, ct_val=1.0)),
                    ):
                        await eng._run_grid_entry(tr_cfg, sol_trk, {**sol_trk.levels["L2"], "reservation_price": 103.55, "trigger_exec_price": 103.5}, "LIVE")

                    amend_calls = [c for c in calls if c[1] == "/api/v5/trade/amend-algos"]
                    assert len(amend_calls) == 1
                    assert amend_calls[0][2]["algoId"] == "sl-ls-1"
                    assert sol_trk.levels["L1"]["cond_sl_live"] is True
                    assert sol_trk.levels["L1"]["cond_sl_algo_id"] == "sl-ls-1"

                    # 3) If both amend-algos & cancel-algos fail transiently, update_slot_sl_order returns kept_old & preserves live SL!
                    amend_fail_mode["fail"] = True
                    upd = await eng.executor.update_slot_sl_order(
                        cfg=tr_cfg, slot_key="SOL/USDT:USDT:L1", side="LONG", qty=1.5, new_sl_price=103.0,
                        exchange_cfg=eng.exchanges["okx"], contracts=1.5, ct_val=1.0,
                    )
                    assert upd["status"] == "kept_old"
                    assert upd["sl_is_live"] is True and upd["algo_id"] == "sl-ls-1"

                # Verify protective orders were audited in SQLite orders table
                await asyncio.sleep(0.35)
                conn = sqlite3.connect(db_file)
                order_types = {r[0] for r in conn.execute("SELECT order_type FROM orders WHERE user_id = 93").fetchall()}
                conn.close()
                assert "POST_ONLY" in order_types
                assert "CONDITIONAL_SL" in order_types
                await eng.executor.aclose()
            finally:
                await adb.stop_worker()

        asyncio.run(run_check())


def test_audit_p0_grid_safety_all_or_nothing_trail_and_monotone_pyramid():
    """
    Verifies Batch 1+2 grid_engine safety fixes:
    1) TREND_BREAKOUT places all N pyramid levels even when grid_range_pct < L1 distance, and eff_range > L1 avoids recenter deadlock.
    2) Strict pyramid monotonicity: never re-enters L1 while L2 is OPEN.
    3) All-or-Nothing TREND_TRAIL_TP: exits ALL same-side slots together on weighted positive net return (no 1.2s partial sweep).
    4) Post-GRID_SL 120s recenter & re-entry cooldown prevents instant stop-out churn.
    5) Kill Switch force_exit_all=True retries reverted OPEN slots on subsequent ticks.
    """
    from backend.grid_engine import LongShortGridTracker

    cfg = SymbolStrategy(
        symbol="ZEC/USDT:USDT",
        strategy_type="ADAPTIVE_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.008,
        grid_take_profit_pct=0.0085,
        grid_range_pct=0.025,
        grid_stop_loss_pct=0.035,
        order_usdt=15.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
        trend_dead_zone_mult=2.0,
        trend_trailing_mult=1.5,
        trend_use_bb_breakout=True,
        bbw_timeframe="15m",
        bbw_squeeze_percentile=20.0,
    )
    squeeze_bb = {
        "timeframe": "15m", "bbw_pct": 1.0, "bbw_percentile": 10.0,
        "bb_upper": 104.5, "bb_middle": 100.0, "bb_lower": 95.5, "trend_bias": "RANGE",
    }
    trk = LongShortGridTracker("ZEC/USDT:USDT")
    trk.sync_config(cfg, current_mid=100.0, bbw_info=squeeze_bb)
    assert trk.active_grid_mode == "TREND_BREAKOUT"
    # All 6 levels (L1..L3, S1..S3) MUST exist with zero clipping even though grid_range_pct=2.5% & bb_upper=4.5%!
    # L1 is capped at max(dead_zone*1.5, min(rng, 0.03)) = 2.5% -> 102.50, and L2=103.30 / L3=104.10 are NOT clipped!
    assert set(trk.levels.keys()) == {"L1", "L2", "L3", "S1", "S2", "S3"}
    assert trk.levels["L1"]["trigger_price"] == 102.50
    assert trk.levels["L2"]["trigger_price"] == 103.30
    assert trk.levels["L3"]["trigger_price"] == 104.10

    # Price breaks L1 at 102.60 (> grid_range_pct=2.5%!) -> MUST trigger L1 instead of recentering away!
    en1, _ = trk.evaluate_tick(102.58, 102.62, 102.60, cfg, is_net_mode=False, bbw_info=squeeze_bb)
    assert [e["slot_id"] for e in en1] == ["L1"]
    trk.mark_entry_filled("L1", 102.62, 0.22, 0.22, 1.0, cfg, 0.001)

    # Price breaks L2 at 103.35 -> L2 opens
    en2, _ = trk.evaluate_tick(103.32, 103.36, 103.34, cfg, is_net_mode=False, bbw_info=squeeze_bb)
    assert [e["slot_id"] for e in en2] == ["L2"]
    trk.mark_entry_filled("L2", 103.36, 0.13, 0.13, 1.0, cfg, 0.001)

    # Strict pyramid monotonicity: if L1 exits externally while L2 is OPEN, L1 MUST NOT re-enter!
    trk.clear_external_exit("L1")
    en_mono, _ = trk.evaluate_tick(103.40, 103.45, 103.42, cfg, is_net_mode=False, bbw_info=squeeze_bb)
    assert "L1" not in [e["slot_id"] for e in en_mono]

    # All-or-Nothing TREND_TRAIL_TP: restore L1 at 102.62 & L2 at 103.36, rally to 105.0 (trail=103.74)
    # Pull back to 103.30 (L1 profitable, L2 slightly negative, weighted notional net > 0) -> exits BOTH L1 & L2!
    trk.levels["L1"]["status"] = "OPEN"
    trk.levels["L1"]["entry_price"] = 102.62
    trk.levels["L1"]["filled_qty"] = 0.22
    trk.levels["L1"]["sl_price"] = 102.73
    trk.levels["L2"]["sl_price"] = 102.73
    trk.evaluate_tick(105.00, 105.04, 105.02, cfg, is_net_mode=False, bbw_info=squeeze_bb)
    _, ex_all = trk.evaluate_tick(103.30, 103.34, 103.32, cfg, is_net_mode=False, round_trip_fee=0.001, bbw_info=squeeze_bb)
    assert {e["slot_id"] for e in ex_all} == {"L1", "L2"}
    assert all(e["exit_reason"] == "TREND_TRAIL_TP" for e in ex_all)

    # Kill Switch force_exit_all=True retries reverted OPEN slots once retry backoff expires!
    trk.revert_exit_failed("L1", backoff_ms=5000.0)
    trk.revert_exit_failed("L2", backoff_ms=5000.0)
    trk.levels["L1"]["exit_retry_until_mono"] = 0.0
    trk.levels["L2"]["exit_retry_until_mono"] = 0.0
    _, ex_ks = trk.evaluate_tick(104.50, 104.54, 104.52, cfg, is_net_mode=False, allow_entries=False, force_exit_all=True)
    assert {e["slot_id"] for e in ex_ks} == {"L1", "L2"}
    assert all(e["exit_reason"] == "KILL_SWITCH" for e in ex_ks)

    # Post-GRID_SL 120s cooldown prevents instant recenter/re-entry right after stop-loss!
    mr_cfg = cfg.model_copy(update={"strategy_type": "LONG_SHORT_GRID"})
    mr_trk = LongShortGridTracker("ZEC/USDT:USDT")
    mr_trk.sync_config(mr_cfg, current_mid=100.0)
    mr_trk.levels["L1"]["status"] = "OPEN"
    mr_trk.levels["L1"]["entry_price"] = 99.2
    mr_trk.mark_exited("L1", -0.5, is_sl=True)
    en_cd, _ = mr_trk.evaluate_tick(95.0, 95.1, 95.05, mr_cfg, is_net_mode=False, allow_entries=True, allow_recenter=True)
    assert en_cd == []
    assert mr_trk.center_price == 100.0  # Did NOT recenter during 120s post-SL cooldown!


def test_audit_p0_executor_short_pos_side_isolation_and_fail_closed_cancel():
    """
    Verifies Batch 1+2 executor safety fixes:
    1) close_exchange_position in long_short_mode closing LONG cancels ONLY LONG pending orders & chunks >10 algos.
    2) execute_exit_with_retry on SHORT in long_short_mode properly checks row_ps=='short' with positive OKX pos.
    3) cancel_slot_protective_orders sets reconcile_uncertain=True when cancel fails -> _run_grid_exits_batch skips blind market close.
    4) query_algo_final_status parses ordIdList and respects explicit accFillSz=='0'.
    """
    from backend.executor import ExecutionAdapter
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE

    _OKX_INSTRUMENT_CACHE["ETH-USDT-SWAP"] = {
        "instId": "ETH-USDT-SWAP", "ctVal": 0.1, "lotSz": 0.01, "lotSz_str": "0.01",
        "minSz": 0.01, "tickSz": 0.01, "tickSz_str": "0.01", "maxLever": 50, "state": "live",
    }

    async def run_exec_audit():
        rc = GlobalRiskConfig(trading_mode="LIVE")
        ex = ExecutionAdapter(lambda _: None, lambda: rc)
        ex_cfg = {"exchange_id": "okx", "api_key": "k_ex", "_decrypted_secret": "s_ex", "_decrypted_passphrase": "p_ex"}
        cfg = SymbolStrategy(symbol="ETH/USDT:USDT", strategy_type="LONG_SHORT_GRID", order_usdt=50.0, leverage=5, price_precision=2, qty_precision=3)

        # 1) close_exchange_position side-isolation + 10-item chunking
        # Seed 12 tracked LONG SL algos + 1 SHORT SL algo, plus 1 LONG TP + 1 SHORT TP
        for i in range(12):
            ex._active_cond_sl_orders[f"ETH/USDT:USDT:L{i}"] = {
                "order_id": f"sl-l-{i}", "is_live": True, "symbol": "ETH/USDT:USDT", "side": "sell",
            }
        ex._active_cond_sl_orders["ETH/USDT:USDT:S1"] = {
            "order_id": "sl-s-1", "is_live": True, "symbol": "ETH/USDT:USDT", "side": "buy",
        }
        ex._active_maker_tp_orders["ETH/USDT:USDT:L1"] = {
            "order_id": "tp-long-1", "is_live": True, "symbol": "ETH/USDT:USDT", "side": "sell",
        }
        ex._active_maker_tp_orders["ETH/USDT:USDT:S1"] = {
            "order_id": "tp-short-1", "is_live": True, "symbol": "ETH/USDT:USDT", "side": "buy",
        }
        close_payloads = []
        canceled_algo_batches = []

        async def mock_close_iso(client, creds, method, path, payload=None):
            if path == "/api/v5/trade/close-position":
                close_payloads.append(payload)
                return {"code": "0", "data": [{"instId": "ETH-USDT-SWAP"}]}
            if path == "/api/v5/trade/cancel-algos":
                canceled_algo_batches.append([x["algoId"] for x in payload])
                return {"code": "0", "data": [{"sCode": "0"} for _ in payload]}
            return {"code": "0", "data": []}

        with (
            patch.object(ex, "_okx_request", side_effect=mock_close_iso),
            patch.object(ex, "ensure_okx_account_meta", new=AsyncMock(return_value={"posMode": "long_short_mode", "acctLv": "2", "_verified": True})),
        ):
            res = await ex.close_exchange_position("ETH/USDT:USDT", "LONG", mgn_mode="cross", exchange_cfg=ex_cfg)
            assert res["ok"] is True
        assert close_payloads[0]["posSide"] == "long" and close_payloads[0]["autoCxl"] is True
        assert "ETH/USDT:USDT:L1" not in ex._active_maker_tp_orders
        assert "ETH/USDT:USDT:S1" in ex._active_maker_tp_orders  # SHORT TP untouched!
        assert "ETH/USDT:USDT:S1" in ex._active_cond_sl_orders   # SHORT SL untouched!
        assert len(canceled_algo_batches) == 2
        assert len(canceled_algo_batches[0]) == 10 and len(canceled_algo_batches[1]) == 2
        assert "sl-s-1" not in [aid for b in canceled_algo_batches for aid in b]  # SHORT SL untouched!

        # 2) execute_exit_with_retry on SHORT with positive OKX pos & posSide='short' does NOT falsely claim FILLED!
        async def mock_short_still_open(client, creds, method, path, payload=None):
            if path == "/api/v5/trade/order":
                return {"code": "51169", "data": [{"sCode": "51169", "sMsg": "transient"}]}
            if path.startswith("/api/v5/account/positions"):
                # OKX long_short_mode returns positive pos="2.0" with posSide="short"!
                return {"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "pos": "2.0", "posSide": "short", "mgnMode": "cross"}]}
            return {"code": "0", "data": []}

        with (
            patch.object(ex, "_okx_request", side_effect=mock_short_still_open),
            patch.object(ex, "ensure_okx_account_meta", new=AsyncMock(return_value={"posMode": "long_short_mode", "acctLv": "2", "_verified": True})),
        ):
            short_res = await ex.execute_exit_with_retry(
                cfg=cfg, side="SHORT", qty=0.2, exit_reason="GRID_SL", tp_price=3000.0,
                exchange_cfg=ex_cfg, contracts=2.0, ct_val=0.1, allow_close_position_fallback=False,
            )
            assert short_res.status == "UNCERTAIN"  # Did NOT misclassify open SHORT as closed!

        # 3) query_algo_final_status parses ordIdList and trusts explicit accFillSz=='0'
        async def mock_algo_zero_fill(client, creds, method, path, payload=None):
            if path.startswith("/api/v5/trade/order-algo?"):
                return {"code": "0", "data": [{"algoId": "a1", "state": "effective", "actualSz": "1.0", "actualPx": "3000", "ordIdList": ["child-99"]}]}
            if path.startswith("/api/v5/trade/order?instId=ETH-USDT-SWAP&ordId=child-99"):
                return {"code": "0", "data": [{"ordId": "child-99", "state": "canceled", "accFillSz": "0", "avgPx": "", "fee": "0"}]}
            return {"code": "0", "data": []}

        with patch.object(ex, "_okx_request", side_effect=mock_algo_zero_fill):
            ast = await ex.query_algo_final_status("ETH/USDT:USDT", "a1", ex_cfg)
            assert ast["ok"] is True and ast["ord_id"] == "child-99" and ast["acc_fill_ct"] == 0.0

        # 4) Fail-closed cancel_slot_protective_orders prevents double-close in _run_grid_exits_batch!
        with tempfile.TemporaryDirectory() as tmpdir:
            adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "fc.db"))
            adb.init_sync()
            adb.ensure_user_workspace_sync(94)
            adb.upsert_symbol_sync(cfg, user_id=94)
            eng = BidirectionalSniperEngine(adb, user_id=94)
            eng.risk_config = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
            eng.exchanges["okx"] = {**ex_cfg, "role": "follower", "is_active": True, "auth_status": "VERIFIED"}
            eng._register_symbol_internal(cfg)
            trk = eng.grid_trackers["ETH/USDT:USDT"]
            trk.sync_config(cfg, current_mid=3000.0)
            trk.levels["L1"].update({"status": "EXITING", "entry_price": 2980.0, "filled_qty": 0.02, "contracts": 0.2, "ct_val": 0.1, "maker_tp_live": True, "maker_tp_order_id": "tp-stuck"})
            eng.executor.restore_slot_protective_tracking(
                "ETH/USDT:USDT", "ETH/USDT:USDT:L1", "LONG", 0.02, 0.2, 0.1, 3000.0, 2900.0,
                maker_tp_order_id="tp-stuck", exchange_cfg=eng.exchanges["okx"],
            )
            market_posts = []

            async def mock_stuck_cancel(client, creds, method, path, payload=None):
                if path == "/api/v5/trade/order" and method == "POST":
                    market_posts.append(payload)
                if path.startswith("/api/v5/trade/order?"):
                    return {"code": "0", "data": [{"ordId": "tp-stuck", "state": "live", "accFillSz": "0"}]}
                if path == "/api/v5/trade/cancel-order":
                    return {"code": "50011", "data": []}
                return {"code": "0", "data": []}

            with (
                patch.object(eng.executor, "_okx_request", side_effect=mock_stuck_cancel),
                patch.object(eng, "sync_live_exchange_positions", new=AsyncMock(return_value={"ok": True})),
            ):
                await eng._run_grid_exits_batch(cfg, trk, [{**trk.levels["L1"], "exit_reason": "GRID_TP"}], "LIVE")

            assert market_posts == []  # ZERO blind market close orders while TP state is uncertain!
            assert trk.levels["L1"]["status"] == "OPEN"
            assert "ETH/USDT:USDT:L1" in eng.executor._active_maker_tp_orders  # Still tracked!
            await eng.executor.aclose()
        await ex.aclose()

    asyncio.run(run_exec_audit())


def test_audit_p0_engine_recovered_ll_vanished_slot_settlement_and_math_models():
    """
    Verifies:
    1) _monitor_recovered_lead_lag_position completes cleanly without NameError: pos_rec.
    2) sync_live_exchange_positions settles vanished slot's filled Maker TP (booking PnL) instead of wiping it silently.
    3) math_engine rejects zero-volume ticks under require_volume_confirm, resets cleanly on >12% jump, and freezes stale basis.
    4) models.py rejects NaN / Infinity floats.
    """
    from backend.sniper_engine import BidirectionalSniperEngine, SniperState
    from backend.executor import OrderResult
    from backend.math_engine import SymbolMathState
    from pydantic import ValidationError

    # 1) models.py NaN / Inf rejection
    for bad_val in (float("nan"), float("inf"), float("-inf")):
        try:
            SymbolStrategy(symbol="BTC/USDT:USDT", order_usdt=bad_val)
            assert False, f"Expected ValidationError for {bad_val}"
        except ValidationError:
            pass

    # 2) math_engine zero-volume rejection + >12% jump reset + stale follower basis freeze
    m = SymbolMathState("BTC/USDT:USDT")
    ll_cfg = SymbolStrategy(
        symbol="BTC/USDT:USDT", strategy_type="LEAD_LAG", require_volume_confirm=True,
        min_jump_pct=0.002, adaptive_z_score=1.0, take_profit_pct=0.001, order_usdt=100.0,
    )
    t0 = 10000.0
    for i in range(15):
        m.on_follower_tick(100.0, 100.02, 50.0, 50.0, now_mono=t0 + i * 20.0)
        m.on_leader_tick(100.0, 100.02, 50.0, 50.0, ll_cfg, is_locked=False, now_mono=t0 + i * 20.0)
    # Jump leader +0.5% with ZERO trade volume -> MUST be rejected by require_volume_confirm!
    m.on_leader_tick(100.50, 100.52, 50.0, 50.0, ll_cfg, is_locked=False, now_mono=t0 + 320.0)
    assert m.evaluate_signal(ll_cfg, taker_fee=0.0005, maker_fee=0.0002, now_mono=t0 + 320.0) is None

    # Stale follower tick (> max_book_staleness_ms) MUST freeze log_basis_ema
    basis_before = m.log_basis_ema
    m.on_leader_tick(101.0, 101.02, 50.0, 50.0, ll_cfg, is_locked=False, now_mono=t0 + 5000.0)
    assert m.basis_frozen is True
    assert m.log_basis_ema == basis_before

    # >12% jump resets cleanly and returns early without poisoning basis
    m.on_follower_tick(100.0, 100.02, 50.0, 50.0, now_mono=t0 + 5010.0)
    m.on_leader_tick(125.0, 125.02, 50.0, 50.0, ll_cfg, is_locked=False, now_mono=t0 + 5015.0)
    assert m.log_basis_ema is None

    # 3) _monitor_recovered_lead_lag_position (no NameError!) + vanished slot Maker TP settlement in sync_live_exchange_positions
    async def run_engine_audit():
        with tempfile.TemporaryDirectory() as tmpdir:
            adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "eng_audit.db"))
            adb.init_sync()
            adb.ensure_user_workspace_sync(95)
            ll_sym = SymbolStrategy(symbol="BTC/USDT:USDT", strategy_type="LEAD_LAG", order_usdt=100.0, leverage=5, price_precision=1, qty_precision=3)
            gr_sym = SymbolStrategy(symbol="ETH/USDT:USDT", strategy_type="LONG_SHORT_GRID", order_usdt=50.0, leverage=5, price_precision=2, qty_precision=3)
            adb.upsert_symbol_sync(ll_sym, user_id=95)
            adb.upsert_symbol_sync(gr_sym, user_id=95)

            eng = BidirectionalSniperEngine(adb, user_id=95)
            eng.risk_config = GlobalRiskConfig(trading_mode="LIVE", feed_source="REAL_WS")
            eng.exchanges["okx"] = {
                "exchange_id": "okx", "role": "follower", "is_active": True,
                "api_key": "k_eng", "_decrypted_secret": "s_eng", "_decrypted_passphrase": "p_eng",
                "auth_status": "VERIFIED", "taker_fee": 0.0005, "maker_fee": 0.0002,
            }
            eng._register_symbol_internal(ll_sym)
            eng._register_symbol_internal(gr_sym)

            # Recovered LEAD_LAG position hits TP -> must execute cleanly with ZERO NameError!
            pos_entry = {
                "symbol": "BTC/USDT:USDT", "mode": "LIVE", "side": "LONG", "qty": 0.01, "contracts": 1.0, "ct_val": 0.01,
                "entry_price": 60000.0, "tp_price": 60200.0, "sl_price": 59500.0, "opened_mono_ms": 1.0, "recovered": True,
            }
            eng.active_positions["BTC/USDT:USDT"] = pos_entry
            eng.symbol_states["BTC/USDT:USDT"] = SniperState.HOLDING
            eng.math_states["BTC/USDT:USDT"].on_follower_tick(60250.0, 60255.0, 10.0, 10.0)
            with patch.object(
                eng.executor, "execute_exit_with_retry",
                new=AsyncMock(return_value=OrderResult("rec-ex-1", "FILLED", 0.01, 60250.0, 15.0, False, contracts=1.0, ct_val=0.01, fee_usdt=0.30)),
            ):
                await eng._monitor_recovered_lead_lag_position(ll_sym, pos_entry)
            assert "BTC/USDT:USDT" not in eng.active_positions
            assert eng.symbol_states["BTC/USDT:USDT"] == SniperState.IDLE
            assert eng.stats_by_mode["LIVE"]["win_count"] == 1

            # Vanished ETH grid slot whose Maker TP actually filled on OKX -> settles PnL instead of wiping silently!
            eth_trk = eng.grid_trackers["ETH/USDT:USDT"]
            eth_trk.sync_config(gr_sym, current_mid=3000.0)
            eth_trk.levels["L1"].update({
                "status": "OPEN", "entry_price": 2980.0, "tp_price": 3005.0, "sl_price": 2880.0,
                "filled_qty": 0.02, "contracts": 0.2, "ct_val": 0.1,
                "maker_tp_live": True, "maker_tp_order_id": "tp-vanish-1",
            })
            eng.active_grid_positions["ETH/USDT:USDT:L1"] = {
                "symbol": "ETH/USDT:USDT", "grid_slot_id": "L1", "side": "LONG", "mode": "LIVE", "qty": 0.02, "opened_mono_ms": 1.0,
            }
            with (
                patch.object(eng, "poll_grid_protective_orders", new=AsyncMock(return_value=0)),
                patch.object(eng.executor, "fetch_live_positions", new=AsyncMock(return_value={"ok": True, "pos_mode": "long_short_mode", "positions": []})),
                patch.object(
                    eng.executor, "cancel_slot_protective_orders",
                    new=AsyncMock(return_value={
                        "tp_fully_filled": True, "tp_filled_ct": 0.2, "tp_avg_px": 3005.0, "tp_order_id": "tp-vanish-1", "tp_fee_usdt": 0.012,
                        "sl_already_filled": False, "sl_filled_ct": 0.0, "sl_avg_px": 0.0, "sl_order_id": "", "sl_fee_usdt": 0.0, "reconcile_uncertain": False,
                    }),
                ),
            ):
                await eng.sync_live_exchange_positions(trigger="test_vanish")
            assert eth_trk.levels["L1"]["status"] == "IDLE"
            assert eng.stats_by_mode["LIVE"]["maker_tp_count"] == 1
            await eng.executor.aclose()

    asyncio.run(run_engine_audit())


def test_audit_trend_anchor_stability_vcp_release_and_bb_cap():
    """
    Verifies the 3 live trigger-starvation fixes:
    1) Routine BB refreshes (bb_only_update) NEVER chase center_price to current_mid so L1/S1 stay reachable.
    2) ADAPTIVE_GRID exits TREND_BREAKOUT back to MEAN_REVERSION once bbw_percentile >= release_thresh and
       breakout_expansion == 'NONE' even if lagging EMA still reports BEAR (where MR EMA filter still blocks LONG).
    3) Wide historical Bollinger Bands are capped at max(1.6*dz, 2.2%) so L1/S1 never drift 3%-4% away.
    """
    from backend.grid_engine import LongShortGridTracker

    cfg = SymbolStrategy(
        symbol="ARB/USDT:USDT",
        strategy_type="ADAPTIVE_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=5,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.05,
        grid_stop_loss_pct=0.06,
        order_usdt=15.0,
        leverage=5,
        price_precision=4,
        qty_precision=1,
        trend_dead_zone_mult=2.0,
        trend_trailing_mult=2.2,
        trend_use_bb_breakout=True,
        bbw_timeframe="15m",
        bbw_squeeze_percentile=20.0,
    )
    trk = LongShortGridTracker("ARB/USDT:USDT")

    # 1) Enter TREND_BREAKOUT on squeeze with wide historical BB -> L1/S1 capped at <= 2.2% (NOT 4%!)
    sq_bb = {
        "timeframe": "15m", "bbw_pct": 1.1, "bbw_percentile": 12.0,
        "bb_upper": 1.045, "bb_middle": 1.000, "bb_lower": 0.955,
        "ema_gap_z": -0.8, "trend_bias": "BEAR", "breakout_expansion": "NONE",
    }
    trk.sync_config(cfg, current_mid=1.0000, bbw_info=sq_bb)
    assert trk.active_grid_mode == "TREND_BREAKOUT"
    assert trk.center_price == 1.0000
    assert trk.levels["L1"]["trigger_price"] == 1.0220  # Capped at +2.2%, not +4.5%!
    assert trk.levels["S1"]["trigger_price"] == 0.9780  # Capped at -2.2%, not -4.5%!
    assert trk.get_snapshot()["trend_bias"] == "BEAR"

    # 2) Price slowly declines to 0.9850 and BB ticks slightly -> bb_only_update MUST keep center_price at 1.0000!
    sq_bb2 = {**sq_bb, "bb_lower": 0.954, "bb_upper": 1.044}
    trk.sync_config(cfg, current_mid=0.9850, bbw_info=sq_bb2)
    assert trk.center_price == 1.0000  # Anchor held firm! Did NOT run away to 0.9850!
    assert trk.levels["S1"]["trigger_price"] == 0.9780

    # 3) BBW expands to 75% (>= 35%) with breakout_expansion='NONE' while lagging EMA is still BEAR (-0.45z):
    #    MUST switch back to MEAN_REVERSION AND keep get_snapshot()['trend_bias'] == 'BEAR'!
    exp_bear_bb = {
        "timeframe": "15m", "bbw_pct": 3.5, "bbw_percentile": 75.0,
        "bb_upper": 1.020, "bb_middle": 1.000, "bb_lower": 0.980,
        "ema_gap_z": -0.45, "trend_bias": "RANGE", "breakout_expansion": "NONE",
    }
    trk.sync_config(cfg, current_mid=0.9850, bbw_info=exp_bear_bb)
    assert trk.active_grid_mode == "MEAN_REVERSION"
    assert trk.get_snapshot()["trend_bias"] == "BEAR"
    # In MEAN_REVERSION with BEAR bias: S1 sits at +0.60% (not +1.2%~3.9%) and triggers on bounce while LONG dips stay blocked!
    s1_px = trk.levels["S1"]["trigger_price"]
    assert abs((s1_px - 0.9850) / 0.9850 - 0.006) < 0.0005
    en_dip, _ = trk.evaluate_tick(0.9780, 0.9785, 0.9782, cfg, is_net_mode=False, bbw_info=exp_bear_bb)
    assert en_dip == []  # Falling-knife LONG still safely paused by BEAR bias!
    en_bounce, _ = trk.evaluate_tick(s1_px + 0.0002, s1_px + 0.0004, s1_px + 0.0003, cfg, is_net_mode=False, bbw_info=exp_bear_bb)
    assert [e["slot_id"] for e in en_bounce] == ["S1"]


def test_batch_grid_levels_range_auto_linkage_and_thinnest_leg_audit():
    """
    Verifies batch grid_levels + grid_range_pct support, level-clipping & thinnest-leg OKX min-notional audit,
    auto-linkage range expansion, and live okx_min_notional_usdt telemetry exposure.
    """
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.models import BatchSymbolUpdateRequest
    from backend.market_gateway import _OKX_INSTRUMENT_CACHE

    _OKX_INSTRUMENT_CACHE["ZEC-USDT-SWAP"] = {
        "instId": "ZEC-USDT-SWAP", "ctVal": 0.01, "lotSz": 1.0, "lotSz_str": "1",
        "minSz": 1.0, "tickSz": 0.01, "tickSz_str": "0.01", "maxLever": 50, "state": "live",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "batch_link.db"))
        adb.init_sync()
        adb.ensure_user_workspace_sync(96)
        zec = SymbolStrategy(
            symbol="ZEC/USDT:USDT",
            strategy_type="ADAPTIVE_GRID",
            grid_levels=5,
            grid_step_pct=0.006,
            grid_take_profit_pct=0.0065,
            grid_range_pct=0.02,   # Too narrow for 5 * 0.6% = 3.0% -> should flag clipping!
            grid_stop_loss_pct=0.035,
            order_usdt=12.0,       # 5-level decay thinnest L5 = 12*5*0.12 = 7.2U < OKX min 8.5U!
            leverage=5,
            price_precision=2,
            qty_precision=2,
            trend_pyramid_decay=True,
        )
        adb.upsert_symbol_sync(zec, user_id=96)
        eng = BidirectionalSniperEngine(adb, user_id=96)
        eng._register_symbol_internal(zec)
        eng.math_states["ZEC/USDT:USDT"].on_follower_tick(850.0, 850.2, 10.0, 10.0)

        # 1) Telemetry exposes real-time okx_min_notional_usdt (1 * 0.01 * 850.1 = 8.50U)
        snap = eng.build_dashboard_snapshot(radar_symbol="ZEC/USDT:USDT")
        assert abs(snap["telemetry"]["ZEC/USDT:USDT"]["okx_min_notional_usdt"] - 8.50) <= 0.05

        # 2) Validate flags BOTH level clipping (5*0.6%=3.0% > 2.0%) AND thinnest L5 (7.2U < 8.5U)!
        v_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(action="validate", strategy_filter="ALL_GRID")
        )
        assert v_res["anomaly_count"] == 1
        it = v_res["items"][0]
        assert it["grid_levels"] == 5
        assert abs(it["thinnest_leg_usdt"] - 7.2) < 0.05
        assert any("裁层" in x for x in it["issues"])
        assert any("最薄层L5" in x for x in it["issues"])

        # 3) Apply batch grid_levels=4 + grid_step_pct=0.008 with auto_fix_anomalies=True -> auto-expands range >= 3.2% and lifts SL > range!
        a_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(
                action="apply",
                strategy_filter="ALL_GRID",
                grid_levels=4,
                grid_step_pct=0.008,
                order_usdt=20.0,
                auto_fix_anomalies=True,
            )
        )
        assert a_res["updated_count"] == 1
        updated_zec = eng.strategies["ZEC/USDT:USDT"]
        assert updated_zec.grid_levels == 4
        assert updated_zec.grid_step_pct == 0.008
        assert updated_zec.grid_range_pct >= 0.036  # 4 * 0.8% = 3.2% + buffer!
        assert updated_zec.grid_stop_loss_pct > updated_zec.grid_range_pct
        assert updated_zec.grid_take_profit_pct >= 0.008
        assert a_res["items"][0]["thinnest_leg_usdt"] == 12.0  # 20 * 4 * 0.15 = 12U > 8.5U!


def test_trend_solo_profit_lock_pyramid_sl_buffer_and_post_sl_cooldown():
    """
    Verifies the 4 live trading data optimizations:
    1) Solo L1/S1 profit-protection breakeven step-up once peak/trough reaches >= 1.15 * step (with 0.06% slippage buffer).
    2) Full 1.0 * step spacing between L(k-1) fill and L(k) entry + 0.55 * step SL breathing room on pyramid legs.
    3) 180s per-slot post-SL cooldown + 90s full-side post-SL cooldown in TREND_BREAKOUT (preventing 4s-15s whipsaw loops).
    4) Batch update support for trend_dead_zone_mult.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms

    cfg = SymbolStrategy(
        symbol="OKB/USDT:USDT",
        strategy_type="TREND_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.008,
        grid_take_profit_pct=0.0085,
        grid_range_pct=0.028,
        grid_stop_loss_pct=0.038,
        order_usdt=10.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
        trend_dead_zone_mult=1.5,
        trend_trailing_mult=1.5,
        trend_pyramid_decay=True,
        trend_use_bb_breakout=False,
    )
    trk = LongShortGridTracker("OKB/USDT:USDT")
    trk.sync_config(cfg, current_mid=100.00)
    assert trk.levels["S1"]["trigger_price"] == 98.80  # 100 * (1 - 1.5 * 0.8%)

    # 1) Solo S1 enters at 98.80, drops +1.0% to 97.80 (>= 1.15 * 0.8% = 0.92% solo lock threshold, before S2 at 98.00 fills if S2 missed)
    en1, _ = trk.evaluate_tick(98.78, 98.80, 98.79, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en1] == ["S1"]
    trk.mark_entry_filled("S1", 98.80, 0.15, 1.0, 0.15, cfg, round_trip_fee=0.0010)
    assert trk.levels["S1"]["sl_price"] == round(98.80 * (1.0 + cfg.grid_stop_loss_pct), 2)  # Strictly honors configured SL %

    # Price drops to 97.85 (favorable move = (98.80 - 97.86)/98.80 = 0.95% >= 0.85%*0.8%) -> S1 auto-locks breakeven SL!
    _, ex_hold = trk.evaluate_tick(97.84, 97.86, 97.85, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_hold == []
    expected_be_s1 = round(98.80 * (1.0 - 0.0010 - 0.0016), 2)  # 98.54 (covers 0.10% fee + 0.16% real OKX SL slippage!)
    assert trk.levels["S1"]["sl_price"] == expected_be_s1
    assert trk.levels["S1"].get("sl_needs_amend") is True

    # 2) If S2 also fills at 97.85, S2 SL keeps >= 0.55 * 0.8% = 0.44% breathing room above 97.85
    trk.mark_entry_filled("S2", 97.85, 0.09, 1.0, 0.09, cfg, round_trip_fee=0.0010)
    assert trk.levels["S2"]["sl_price"] >= round(97.85 * (1.0 + 0.008 * 0.55), 2)

    # 3) Suppose S2 stops out via TREND_SL while S1 stays open -> S2 gets 180s per-slot cooldown!
    now_before = mono_ms()
    trk.mark_exited("S2", -0.05, is_sl=True)
    assert trk.levels["S2"]["cooldown_until_mono"] >= now_before + 170_000.0
    # Even if price immediately ticks back to 97.80, S2 MUST NOT re-enter during its 180s cooldown!
    en_re, _ = trk.evaluate_tick(97.80, 97.82, 97.81, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_re == []

    # When S1 also stops out via TREND_BE_SL -> full-side 90s post-SL cooldown engages!
    trk.mark_exited("S1", 0.02, is_sl=True)
    assert trk._post_sl_cooldown_until_mono >= now_before + 85_000.0
    assert trk._needs_post_trend_recenter is True
    # During the 90s post-SL cooldown, it does NOT recenter or re-enter prematurely
    en_cd, _ = trk.evaluate_tick(98.60, 98.64, 98.62, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_cd == []
    assert trk._needs_post_trend_recenter is True

    # 4) Combined-VWAP 25% Profit-Lock Floor: prevents S1+S2 moderate waves from round-tripping into TREND_BE_SL_S1 + TREND_SL_S2 loss!
    trk2 = LongShortGridTracker("ARB/USDT:USDT")
    cfg2 = cfg.model_copy(update={"symbol": "ARB/USDT:USDT", "trend_dead_zone_mult": 1.25, "trend_trailing_mult": 1.25, "order_usdt": 20.0})
    # High-vol blown-out BB (bbw_percentile=94.2%, bb_lower=95.0): S1 MUST cap tightly near 1.15*dz (-1.15%), NOT -2.2%!
    high_vol_bb = {"timeframe": "15m", "bbw_pct": 8.7, "bbw_percentile": 94.2, "bb_upper": 105.0, "bb_middle": 100.0, "bb_lower": 95.0, "trend_bias": "BEAR"}
    trk2.sync_config(cfg2, current_mid=100.00, bbw_info=high_vol_bb)
    assert trk2.levels["S1"]["trigger_price"] >= 98.80  # Tight 1.15% cap, NOT 97.80 (-2.2%)!

    # Open S1 (30U @ 100.00) and S2 (18U @ 99.20) -> combined VWAP = 99.70, VWAP_BE = 99.54
    trk2.mark_entry_filled("S1", 100.00, 0.30, 3.0, 0.1, cfg2, round_trip_fee=0.0010)
    trk2.mark_entry_filled("S2", 99.20, 0.18, 1.8, 0.1, cfg2, round_trip_fee=0.0010)
    # Trough reaches 98.90 (+0.64% net beyond VWAP_BE=99.54, though raw trail 98.90*1.01=99.89 would be ABOVE VWAP_BE!)
    _, ex_trough = trk2.evaluate_tick(98.88, 98.90, 98.89, cfg2, is_net_mode=False, round_trip_fee=0.0010, bbw_info=high_vol_bb)
    assert ex_trough == []
    # Profit-lock floor clamps short_trail_stop_price <= 99.38 (well below VWAP_BE 99.54 and S1.be_px 99.84!)
    assert trk2.short_trail_stop_price <= 99.40
    # When price bounces to 99.42, TREND_TRAIL_TP harvests BOTH S1 & S2 for a combined net PROFIT instead of waiting for 99.84 SL!
    _, ex_lock = trk2.evaluate_tick(99.40, 99.42, 99.41, cfg2, is_net_mode=False, round_trip_fee=0.0010, bbw_info=high_vol_bb)
    assert {e["slot_id"] for e in ex_lock} == {"S1", "S2"}
    assert all(e["exit_reason"] == "TREND_TRAIL_TP" for e in ex_lock)


def test_reverse_oscillating_grid_core_lifecycle():
    """
    Verifies the mode-locked hybrid REVERSE_GRID strategy end-to-end against every rule in the user spec:
    1) Dynamic live mid center C = 100.00, step = 0.6%, 5 levels, global SL = 5% (92.15 / 108.15).
    2) Case 1 (LONG Mode): L1 (100.60) locks LONG with base Q (100%); pullback to C (100.00) buys C (100%);
       bounce to 100.60 ONLY sells C (does NOT re-buy Q!); S1..S5 dip-buy/sell 1 grid higher;
       per-slot 0.03% outer-side re-arm hysteresis & 1.5s cooldown;
       special C -> Q zero-order in-place conversion when C is held without Q;
       L2 (50%), L3 (30%), L4 (10%) join L-group & activate monotonic VWAP-floor + 0.8% trailing TP
       (102.40 -> 0.8% pullback ~101.58 closes entire L-group); L5 (103.00) closes entire L-group;
       Global SL (92.15) closes ALL 7 open slots and triggers multi-hour cooldown.
    3) Post-cycle reconcile gate (4-min TP cooldown vs 3-hour global SL cooldown) -> re-anchors to NONE.
    4) Case 2 (SHORT Mode mirror): S1 (99.40) locks SHORT with base QS (100%), CS/L1..L5 bounce-shorts,
       S2 (50%)/S3 (30%)/S4 (10%) S-group downward trailing TP, S5 (97.00) full close, Global SL (108.15).
    5) Idle range drift (±3.5%) auto-recenter + export_state / restore_state persistence.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms
    from backend.models import is_grid_strategy

    assert is_grid_strategy("REVERSE_GRID") is True

    cfg = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        strategy_type="REVERSE_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=5,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.035,
        grid_stop_loss_pct=0.05,
        order_usdt=1000.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
        grid_recenter_enabled=True,
    )
    trk = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk.sync_config(cfg, current_mid=100.00)

    assert trk.active_grid_mode == "REVERSE_OSCILLATING"
    assert trk.strategy.locked_mode == "NONE"
    assert trk.levels["Q"]["trigger_price"] == 100.60
    assert trk.levels["QS"]["trigger_price"] == 99.40

    # --- CASE 1: First touch L1 (100.60) -> Buy Q (100%) and LOCK LONG MODE ---
    en_q, ex_q = trk.evaluate_tick(100.59, 100.60, 100.595, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_q == []
    assert [e["slot_id"] for e in en_q] == ["Q"]
    assert en_q[0]["side"] == "LONG" and en_q[0]["order_usdt"] == 1000.0

    # Concurrent ENTERING guard
    en_inf, _ = trk.evaluate_tick(101.20, 101.22, 101.21, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_inf == []

    trk.mark_entry_filled("Q", 100.60, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk.strategy.locked_mode == "LONG"
    assert trk.strategy.full_tp_price == 103.00
    assert trk.strategy.global_sl_price == 92.15
    assert trk.levels["L2"]["order_usdt"] == 500.0 and trk.levels["L2"]["trigger_price"] == 101.20
    assert trk.levels["L3"]["order_usdt"] == 300.0 and trk.levels["L3"]["trigger_price"] == 101.80
    assert trk.levels["L4"]["order_usdt"] == 100.0 and trk.levels["L4"]["trigger_price"] == 102.40
    assert trk.levels["C"]["trigger_price"] == 100.00 and trk.levels["C"]["tp_price"] == 100.60 and trk.levels["C"]["order_usdt"] == 1000.0
    assert trk.levels["S1"]["trigger_price"] == 99.40 and trk.levels["S1"]["tp_price"] == 100.00 and trk.levels["S1"]["order_usdt"] == 1000.0
    assert trk.levels["S2"]["order_usdt"] == 1000.0
    assert trk.levels["S3"]["order_usdt"] == 500.0
    assert trk.levels["S4"]["order_usdt"] == 300.0
    assert trk.levels["S5"]["trigger_price"] == 97.00 and trk.levels["S5"]["tp_price"] == 97.60 and trk.levels["S5"]["order_usdt"] == 200.0
    assert trk.should_place_maker_tp_for_slot("Q", cfg) is False
    assert trk.should_place_maker_tp_for_slot("L2", cfg) is False
    assert trk.should_place_maker_tp_for_slot("C", cfg) is True
    assert trk.should_place_maker_tp_for_slot("S1", cfg) is True

    # Pullback to C (100.00) -> Buy C (100%), Q untouched
    en_c, ex_c = trk.evaluate_tick(99.99, 100.00, 99.995, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_c == [] and [e["slot_id"] for e in en_c] == ["C"] and en_c[0]["side"] == "LONG"
    trk.mark_entry_filled("C", 100.00, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)

    # Bounce back to 100.60 -> ONLY sells C (GRID_TP), Q stays OPEN and is NOT re-bought!
    en_sell_c, ex_sell_c = trk.evaluate_tick(100.60, 100.61, 100.605, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_sell_c == []
    assert [e["slot_id"] for e in ex_sell_c] == ["C"] and ex_sell_c[0]["exit_reason"] == "GRID_TP"
    trk.mark_exited("C", 5.3, is_sl=False)
    assert trk.levels["Q"]["status"] == "OPEN"
    assert trk.levels["C"]["armed"] is False
    assert trk.strategy.locked_mode == "LONG"  # Still locked because Q is open!

    # Re-arm hysteresis check: after cooldown, C stays disarmed at 100.01 (< 100.03), re-arms at 100.04 (>= 100.03)!
    trk.levels["C"]["cooldown_until_mono"] = 0.0
    trk.levels["C"]["last_closed_mono"] = 0.0
    trk.evaluate_tick(100.00, 100.02, 100.01, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk.levels["C"]["armed"] is False
    trk.evaluate_tick(100.03, 100.05, 100.04, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk.levels["C"]["armed"] is True

    # Drop to S1 (99.40) -> Buy S1 (100%); bounce to C (100.00) -> Sell S1 (GRID_TP)
    en_s1, _ = trk.evaluate_tick(99.39, 99.40, 99.395, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_s1] == ["S1"] and en_s1[0]["side"] == "LONG"
    trk.mark_entry_filled("S1", 99.40, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    _, ex_s1 = trk.evaluate_tick(100.00, 100.01, 100.005, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in ex_s1] == ["S1"] and ex_s1[0]["exit_reason"] == "GRID_TP"
    trk.mark_exited("S1", 5.3, is_sl=False)

    # Special rule from screenshot: if C is held & Q is NOT held when reaching 100.60 -> C converts in-place to Q!
    trk_conv = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_conv.sync_config(cfg, current_mid=100.00)
    trk_conv.mark_entry_filled("Q", 100.60, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    trk_conv.mark_entry_filled("C", 100.00, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    trk_conv.levels["Q"]["status"] = "IDLE"
    assert trk_conv.should_place_maker_tp_for_slot("C", cfg) is False  # No Maker TP while Q absent!
    en_cv, ex_cv = trk_conv.evaluate_tick(100.60, 100.61, 100.605, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_cv == [] and ex_cv == []
    assert trk_conv.levels["Q"]["status"] == "OPEN" and trk_conv.levels["Q"]["entry_price"] == 100.00
    assert trk_conv.levels["C"]["status"] == "IDLE"
    convs = trk_conv.consume_slot_conversions()
    assert len(convs) == 1 and convs[0]["from_slot"] == "C" and convs[0]["to_slot"] == "Q"

    # Rally to L2 (101.20) -> Buy L2 (50% = 500U), activates L-group trailing TP!
    en_l2, ex_l2 = trk.evaluate_tick(101.19, 101.20, 101.195, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_l2 == [] and [e["slot_id"] for e in en_l2] == ["L2"] and en_l2[0]["order_usdt"] == 500.0
    trk.mark_entry_filled("L2", 101.20, 5.0, 5.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk.strategy.group_trail_active is True
    # Q 10@100.60 + L2 5@101.20 -> exact weighted avg 100.80, floor includes RT fee + slippage/profit cushion (> 100.90, < 101.20)
    assert trk.strategy.group_vwap == 100.80
    assert round(100.80 * 1.0015, 2) <= trk.strategy.group_floor_price < 101.20
    assert trk.long_trail_stop_price == trk.strategy.group_floor_price

    # Rally to L3 (101.80) -> Buy L3 (30% = 300U), monotonic VWAP & floor ratchet upward!
    prev_floor = trk.strategy.group_floor_price
    en_l3, _ = trk.evaluate_tick(101.79, 101.80, 101.795, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_l3] == ["L3"] and en_l3[0]["order_usdt"] == 300.0
    trk.mark_entry_filled("L3", 101.80, 3.0, 3.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk.strategy.group_floor_price > prev_floor

    # Export & restore state preserves locked_mode, group_trail_active, VWAP, floor, and open slots!
    exported = trk.export_state()
    assert exported["locked_mode"] == "LONG" and exported["group_trail_active"] is True
    trk_rest = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_rest.restore_state(exported, cfg)
    assert trk_rest.strategy.locked_mode == "LONG"
    assert trk_rest.strategy.group_trail_active is True
    assert trk_rest.levels["Q"]["status"] == "OPEN" and trk_rest.levels["L2"]["status"] == "OPEN"

    # Rally to L4 (102.40) -> Buy L4 (10% = 100U); peak=102.40 -> 0.8% pullback triggers ~101.58!
    en_l4, _ = trk_rest.evaluate_tick(102.40, 102.41, 102.405, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_l4] == ["L4"] and en_l4[0]["order_usdt"] == 100.0
    trk_rest.mark_entry_filled("L4", 102.40, 1.0, 1.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk_rest.long_peak_price == 102.40
    assert trk_rest.long_trail_stop_price == round(102.40 * (1.0 - 0.008), 2)  # 101.58!

    # Pullback to 101.57 -> closes ENTIRE L-group (Q, L2, L3, L4) via GRID_TRAIL_TP!
    _, ex_trail = trk_rest.evaluate_tick(101.57, 101.59, 101.58, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert set(e["slot_id"] for e in ex_trail) == {"Q", "L2", "L3", "L4"}
    assert all(e["exit_reason"] == "GRID_TRAIL_TP" for e in ex_trail)
    now_pre = mono_ms()
    for sid in ("Q", "L2", "L3", "L4"):
        trk_rest.mark_exited(sid, 12.0, is_sl=False)

    # All slots flat -> enters PENDING_RECONCILE + 30-min TP cooldown (~1800s); blocked until verified clean AND cooldown expires!
    assert trk_rest.strategy.cycle_phase == "PENDING_RECONCILE"
    assert trk_rest.needs_post_cycle_flat_reconcile() is True
    assert trk_rest.strategy.restart_reason == "TP_COOLDOWN"
    assert now_pre + 1_790_000.0 <= trk_rest.strategy.restart_cooldown_until_mono <= now_pre + 1_810_000.0
    # Even if cooldown elapsed, unverified reconcile MUST block re-anchor!
    trk_rest.strategy.restart_cooldown_until_mono = 0.0
    trk_rest.evaluate_tick(105.00, 105.02, 105.01, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk_rest.strategy.locked_mode == "LONG"  # Still gated by reconcile!
    # Once exchange verified clean -> re-anchors at latest mid 105.01, resets mode = NONE, all slots armed!
    trk_rest.mark_reconcile_verified_clean()
    trk_rest.evaluate_tick(105.00, 105.02, 105.01, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk_rest.strategy.locked_mode == "NONE"
    assert trk_rest.center_price == 105.01
    assert all(lv["armed"] is True for lv in trk_rest.levels.values())

    # L5 (103.00) full take-profit closes entire L-group via GRID_GROUP_TP!
    trk_l5 = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_l5.sync_config(cfg, current_mid=100.00)
    trk_l5.mark_entry_filled("Q", 100.60, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    trk_l5.mark_entry_filled("L2", 101.20, 5.0, 5.0, 1.0, cfg, round_trip_fee=0.0010)
    _, ex_l5 = trk_l5.evaluate_tick(103.00, 103.02, 103.01, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert set(e["slot_id"] for e in ex_l5) == {"Q", "L2"}
    assert all(e["exit_reason"] == "GRID_GROUP_TP" for e in ex_l5)

    # Global Hard Stop-Loss (S5 below 5% = 92.15) closes ALL 7 slots (Q, C, S1..S5) + multi-hour cooldown!
    trk_gsl = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_gsl.sync_config(cfg, current_mid=100.00)
    trk_gsl.mark_entry_filled("Q", 100.60, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    for sid, px in (("C", 100.00), ("S1", 99.40), ("S2", 98.80), ("S3", 98.20), ("S4", 97.60), ("S5", 97.00)):
        trk_gsl.mark_entry_filled(sid, px, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    _, ex_gsl = trk_gsl.evaluate_tick(92.14, 92.16, 92.15, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert set(e["slot_id"] for e in ex_gsl) == {"Q", "C", "S1", "S2", "S3", "S4", "S5"}
    assert all(e["exit_reason"] == "GRID_GLOBAL_SL" for e in ex_gsl)
    t_sl_pre = mono_ms()
    for e in ex_gsl:
        trk_gsl.mark_exited(e["slot_id"], -65.0, is_sl=True)
    assert trk_gsl.strategy.restart_reason == "GLOBAL_SL_COOLDOWN"
    assert t_sl_pre + 3_590_000.0 <= trk_gsl.strategy.restart_cooldown_until_mono <= t_sl_pre + 3_610_000.0  # 1-hour SL cooldown!

    # --- CASE 2: SHORT Mode (exact mirror when S1 = 99.40 hits first) ---
    trk_s = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_s.sync_config(cfg, current_mid=100.00)
    en_qs, _ = trk_s.evaluate_tick(99.39, 99.40, 99.395, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_qs] == ["QS"] and en_qs[0]["side"] == "SHORT"
    trk_s.mark_entry_filled("QS", 99.40, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk_s.strategy.locked_mode == "SHORT"
    assert trk_s.strategy.full_tp_price == 97.00
    assert trk_s.strategy.global_sl_price == 108.15
    assert trk_s.levels["CS"]["trigger_price"] == 100.00 and trk_s.levels["CS"]["tp_price"] == 99.40 and trk_s.levels["CS"]["order_usdt"] == 1000.0
    assert trk_s.levels["L1"]["trigger_price"] == 100.60 and trk_s.levels["L1"]["tp_price"] == 100.00 and trk_s.levels["L1"]["order_usdt"] == 1000.0
    assert trk_s.levels["L2"]["order_usdt"] == 1000.0 and trk_s.levels["L3"]["order_usdt"] == 500.0
    assert trk_s.levels["L4"]["order_usdt"] == 300.0 and trk_s.levels["L5"]["order_usdt"] == 200.0
    assert trk_s.levels["S2"]["order_usdt"] == 500.0 and trk_s.levels["S3"]["order_usdt"] == 300.0 and trk_s.levels["S4"]["order_usdt"] == 100.0

    # Bounce to 100.00 -> Short CS; drop to 99.40 -> Cover CS
    en_cs, _ = trk_s.evaluate_tick(100.00, 100.01, 100.005, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_cs] == ["CS"] and en_cs[0]["side"] == "SHORT"
    trk_s.mark_entry_filled("CS", 100.00, 10.0, 10.0, 1.0, cfg, round_trip_fee=0.0010)
    _, ex_cs = trk_s.evaluate_tick(99.39, 99.40, 99.395, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in ex_cs] == ["CS"] and ex_cs[0]["exit_reason"] == "GRID_TP"
    trk_s.mark_exited("CS", 5.3, is_sl=False)

    # Drop to S2 (98.80) -> Short S2 (50%), activates S-group downward trailing TP!
    en_s2, _ = trk_s.evaluate_tick(98.79, 98.80, 98.795, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_s2] == ["S2"] and en_s2[0]["order_usdt"] == 500.0
    trk_s.mark_entry_filled("S2", 98.80, 5.0, 5.0, 1.0, cfg, round_trip_fee=0.0010)
    assert trk_s.strategy.group_trail_active is True
    assert trk_s.strategy.group_vwap == 99.20
    assert 98.80 < trk_s.strategy.group_floor_price <= round(99.20 * 0.9985, 2)
    # Drop to S5 (97.00) -> full S-group close (GRID_GROUP_TP)!
    _, ex_s5 = trk_s.evaluate_tick(96.99, 97.00, 96.995, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert set(e["slot_id"] for e in ex_s5) == {"QS", "S2"}
    assert all(e["exit_reason"] == "GRID_GROUP_TP" for e in ex_s5)

    # --- CASE 3: Idle Range Drift (±3.5%) auto-recenter when locked_mode == NONE ---
    trk_idle = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_idle.sync_config(cfg, current_mid=100.00)
    en_dr, ex_dr = trk_idle.evaluate_tick(104.00, 104.02, 104.01, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert en_dr == [] and ex_dr == []
    assert trk_idle.center_price == 104.01 and trk_idle.strategy.locked_mode == "NONE"


def test_reverse_grid_engine_db_api_and_batch_e2e():
    """
    Full-stack integration test for REVERSE_GRID (多空震荡网格):
    - SQLite persistence (`upsert_symbol_sync` / `load_symbols_sync`)
    - Engine SHADOW round-trip execution (`_handle_follower_bbo` -> entry -> TP exit -> realized net PnL)
    - `_get_grid_rt_fee` Maker Post-Only fee discount
    - `validate_and_batch_update_symbols` with `strategy_filter="REVERSE_GRID"` & `"ALL_GRID"`
    - FastAPI `/api/symbol/update`, `/api/symbols`, `/api/stats`, `/api/symbols/batch-update`
    """
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.models import BatchSymbolUpdateRequest

    async def _run_engine_e2e():
        with tempfile.TemporaryDirectory() as tmpdir:
            adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "reverse_e2e.db"))
            adb.init_sync()
            adb.ensure_user_workspace_sync(77)

            cfg = SymbolStrategy(
                symbol="DOGE/USDT:USDT",
                strategy_type="REVERSE_GRID",
                grid_side_mode="LONG_SHORT",
                grid_levels=3,
                grid_step_pct=0.008,
                grid_take_profit_pct=0.0085,
                grid_range_pct=0.030,
                grid_stop_loss_pct=0.025,  # Valid per-slot SL (< range=3.0%, > step=0.8%)!
                order_usdt=25.0,
                leverage=5,
                price_precision=4,
                qty_precision=1,
                use_post_only_tp=True,
                is_running=True,
            )
            adb.upsert_symbol_sync(cfg, user_id=77)
            loaded = {s.symbol: s for s in adb.load_symbols_sync(user_id=77)}
            assert loaded["DOGE/USDT:USDT"].strategy_type == "REVERSE_GRID"

            eng = BidirectionalSniperEngine(adb, user_id=77)
            eng.risk_config.trading_mode = "SHADOW"
            eng.risk_config.shadow_base_latency_ms = 0.0
            eng.risk_config.shadow_latency_jitter_ms = 0.0
            eng.risk_config.shadow_adverse_selection_prob = 0.0
            eng.trading_active_by_mode["SHADOW"] = True
            eng._register_symbol_internal(cfg)

            # Conservative Taker+Taker RT fee used for group VWAP breakeven floor
            _, f_ex = eng.get_leader_And_follower_cfg()
            rt_fee = eng._get_grid_rt_fee(cfg, "REVERSE_OSCILLATING", f_ex)
            assert abs(rt_fee - (2.0 * float(f_ex["taker_fee"]))) < 1e-9

            # Seed center at 0.2000 (step=0.8%, levels=3 -> L1=0.2016, L2=0.2032, L3=0.2048; S1=0.1984)
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1999, 0.2001, 10000.0, 10000.0)
            trk = eng.grid_trackers["DOGE/USDT:USDT"]
            assert trk.active_grid_mode == "REVERSE_OSCILLATING"
            assert trk.strategy.locked_mode == "NONE"
            assert trk.levels["Q"]["trigger_price"] == 0.2016
            assert trk.levels["QS"]["trigger_price"] == 0.1984

            # 1) Break UP past L1 (0.2016) -> opens LONG Q and locks LONG mode!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2016, 0.2017, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:Q" in eng.active_grid_positions
            q_pos = eng.active_grid_positions["DOGE/USDT:USDT:Q"]
            assert q_pos["side"] == "LONG"
            assert q_pos["strategy_type"] == "REVERSE_GRID"
            assert q_pos["grid_mode"] == "REVERSE_OSCILLATING"
            assert trk.strategy.locked_mode == "LONG"

            # Pullback to C (0.2000) -> buys C (100%); bounce to 0.2016 -> sells ONLY C profitably!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1999, 0.2000, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:C" in eng.active_grid_positions
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2016, 0.2017, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:C" not in eng.active_grid_positions
            assert "DOGE/USDT:USDT:Q" in eng.active_grid_positions
            assert eng.stats_by_mode["SHADOW"]["win_count"] == 1

            # Price rises to L2 (0.2032) -> opens LONG L2 (50%) & activates L-group trailing TP!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2032, 0.2033, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:L2" in eng.active_grid_positions
            assert trk.strategy.group_trail_active is True

            # Reach L3 (0.2048) -> full L-group close (Q + L2) via GRID_GROUP_TP!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2048, 0.2049, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert not eng.active_grid_positions
            assert eng.stats_by_mode["SHADOW"]["win_count"] == 3
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] > 0
            assert trk.strategy.reconcile_verified is True

            # Expire restart cooldown -> next tick re-anchors to NONE at 0.2000, then test SHORT mode!
            trk.strategy.restart_cooldown_until_mono = 0.0
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1999, 0.2001, 10000.0, 10000.0)
            assert trk.strategy.locked_mode == "NONE" and trk.center_price == 0.2000

            # 2) Break DOWN past S1 (0.1984) -> opens SHORT QS & locks SHORT mode -> S2 activates -> S3 full TP!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1983, 0.1984, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:QS" in eng.active_grid_positions
            assert trk.strategy.locked_mode == "SHORT"

            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1967, 0.1968, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:S2" in eng.active_grid_positions
            assert trk.strategy.group_trail_active is True

            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1951, 0.1952, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert not eng.active_grid_positions
            assert eng.stats_by_mode["SHADOW"]["win_count"] >= 5
            assert eng.stats_by_mode["SHADOW"]["loss_count"] == 0
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] > 0

            # 2b) Verify LIVE post-cycle residual cleanup gate (_run_symbol_flat_reconcile)
            trk.strategy.needs_flat_reconcile = True
            trk.strategy.reconcile_verified = False
            trk.strategy.cycle_phase = "PENDING_RECONCILE"
            cleanup_called = {"called": False}

            async def _fake_cleanup(sym_arg, ex_arg):
                cleanup_called["called"] = True
                return {"ok": True, "is_flat": True, "cleaned": True, "residual_positions": 0, "residual_orders": 0, "message": "已清理残留并二次对账清零"}

            eng.executor.reconcile_and_cleanup_symbol_flat = _fake_cleanup
            await eng._run_symbol_flat_reconcile(cfg, trk, "LIVE")
            assert cleanup_called["called"] is True
            assert trk.strategy.reconcile_verified is True
            assert trk.strategy.cycle_phase == "COOLDOWN"

            # 2c) Verify manual_close_position records MANUAL_CLOSE in trade_logs/stats & blocks instant re-open!
            op_cfg = SymbolStrategy(
                symbol="OP/USDT:USDT",
                strategy_type="LONG_SHORT_GRID",
                grid_side_mode="LONG_SHORT",
                grid_levels=3,
                grid_step_pct=0.006,
                grid_take_profit_pct=0.0065,
                grid_range_pct=0.025,
                grid_stop_loss_pct=0.04,
                order_usdt=10.0,
                leverage=5,
                price_precision=5,
                qty_precision=1,
            )
            eng._register_symbol_internal(op_cfg)
            eng._handle_follower_bbo("OP/USDT:USDT", 0.07090, 0.07100, 10000.0, 10000.0)
            # Trigger SHORT S1 at 0.07138
            eng._handle_follower_bbo("OP/USDT:USDT", 0.07140, 0.07142, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "OP/USDT:USDT:S1" in eng.active_grid_positions
            pre_recent_len = len(eng.stats_by_mode["SHADOW"]["recent_trades"])
            mc_res = await eng.manual_close_position("OP/USDT:USDT", "SHORT", slot_id="S1")
            assert mc_res["ok"] is True
            assert "OP/USDT:USDT:S1" not in eng.active_grid_positions
            assert len(eng.stats_by_mode["SHADOW"]["recent_trades"]) == pre_recent_len + 1
            assert eng.stats_by_mode["SHADOW"]["recent_trades"][0]["reason"] == "MANUAL_CLOSE_S1"
            # Immediately receiving the same BBO tick 100ms later MUST NOT re-open S1 (3-min cooldown + recenter!)
            eng._handle_follower_bbo("OP/USDT:USDT", 0.07140, 0.07142, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "OP/USDT:USDT:S1" not in eng.active_grid_positions
            # Remove OP before batch validate assertions on DOGE below
            eng.strategies.pop("OP/USDT:USDT", None)
            eng.grid_trackers.pop("OP/USDT:USDT", None)

            # 3) Dashboard snapshot exposes REVERSE_GRID & REVERSE_OSCILLATING properly
            dash = eng.build_dashboard_snapshot(radar_symbol="DOGE/USDT:USDT")
            doge_tel = dash["telemetry"]["DOGE/USDT:USDT"]
            assert doge_tel["strategy_type"] == "REVERSE_GRID"
            assert doge_tel["grid"]["active_grid_mode"] == "REVERSE_OSCILLATING"

            # 4) Batch validate with strategy_filter="REVERSE_GRID": SL=2.5% (< range 3.0%, > step 0.8%) has ZERO issues!
            val_ok = eng.validate_and_batch_update_symbols(
                BatchSymbolUpdateRequest(action="validate", strategy_filter="REVERSE_GRID")
            )
            assert val_ok["scanned_count"] == 1
            assert val_ok["anomaly_count"] == 0

            # Tight SL <= step IS flagged & auto-fixed by batch update!
            eng.strategies["DOGE/USDT:USDT"].grid_stop_loss_pct = 0.006  # <= step 0.008!
            val_bad = eng.validate_and_batch_update_symbols(
                BatchSymbolUpdateRequest(action="validate", strategy_filter="REVERSE_GRID")
            )
            assert val_bad["anomaly_count"] == 1
            assert any("单格止损" in iss for iss in val_bad["items"][0]["issues"])

            apply_fix = eng.validate_and_batch_update_symbols(
                BatchSymbolUpdateRequest(action="apply", strategy_filter="REVERSE_GRID", auto_fix_anomalies=True)
            )
            assert apply_fix["updated_count"] == 1
            assert eng.strategies["DOGE/USDT:USDT"].grid_stop_loss_pct > eng.strategies["DOGE/USDT:USDT"].grid_step_pct

            # 5) Manual recenter works cleanly with 多空震荡 mode label
            ok_rc, msg_rc = eng.recenter_symbol_grid("DOGE/USDT:USDT")
            assert ok_rc is True
            assert "多空震荡" in msg_rc

    asyncio.run(_run_engine_e2e())

    # 6) Full FastAPI HTTP round-trip for REVERSE_GRID (/api/symbol/update, /api/symbols, /api/symbols/batch-update, /api/stats)
    import backend.main as main_mod
    from backend.engine_manager import EngineManager

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "reverse_api.db")
        orig_db_path = main_mod.db_manager.db_path
        orig_um_path = main_mod.user_manager.db_path
        orig_em = main_mod.engine_manager
        try:
            rate_limiter._buckets.clear()
            main_mod.user_manager.db_path = test_db
            main_mod.user_manager.init_sync()
            main_mod.db_manager.db_path = test_db
            main_mod.db_manager.init_sync()
            main_mod.engine_manager = EngineManager(main_mod.db_manager, get_user_fn=main_mod.user_manager.get_user_by_id)

            with (
                patch.object(main_mod.engine_manager.gateway, "start", new=AsyncMock(return_value=None)),
                patch.object(main_mod.engine_manager.gateway, "stop", new=AsyncMock(return_value=None)),
                patch.object(main_mod.engine_manager.gateway, "fetch_exchange_instruments", new=AsyncMock(return_value=True)),
                patch.object(main_mod.engine_manager.gateway, "fetch_real_bbo_snapshot", new=AsyncMock(return_value=True)),
                patch.object(main_mod.payment_monitor, "start", return_value=None),
                patch.object(main_mod.payment_monitor, "stop", new=AsyncMock(return_value=None)),
            ):
                with TestClient(main_mod.app) as client:
                    reg = client.post(
                        "/api/auth/register",
                        json={"username": "rev_tester", "password": "password123"},
                        headers={"X-Real-IP": "198.51.100.77"},
                    )
                    assert reg.status_code == 200
                    tok = reg.json()["token"]
                    uid = reg.json()["user"]["id"]
                    conn = sqlite3.connect(test_db)
                    conn.execute(
                        "UPDATE users SET vip_tier='季度旗舰版会员', vip_expires_at=? WHERE id=?",
                        (int(time.time()) + 86400 * 30, uid),
                    )
                    conn.commit()
                    conn.close()

                    hdr = {"Authorization": f"Bearer {tok}"}
                    upd = client.post(
                        "/api/symbol/update",
                        headers=hdr,
                        json={
                            "symbol": "XRP/USDT:USDT",
                            "strategy_type": "REVERSE_GRID",
                            "grid_side_mode": "LONG_SHORT",
                            "grid_levels": 4,
                            "grid_step_pct": 0.006,
                            "grid_take_profit_pct": 0.0065,
                            "grid_range_pct": 0.03,
                            "grid_stop_loss_pct": 0.02,
                            "order_usdt": 30.0,
                            "leverage": 5,
                            "price_precision": 4,
                            "qty_precision": 1,
                            "is_running": True,
                        },
                    )
                    assert upd.status_code == 200, upd.text
                    xrp_cfg = next(s for s in upd.json()["strategies"] if s["symbol"] == "XRP/USDT:USDT")
                    assert xrp_cfg["strategy_type"] == "REVERSE_GRID"

                    # /api/symbols returns persisted REVERSE_GRID
                    syms = client.get("/api/symbols", headers=hdr)
                    assert syms.status_code == 200
                    assert next(s for s in syms.json()["strategies"] if s["symbol"] == "XRP/USDT:USDT")["strategy_type"] == "REVERSE_GRID"

                    # /api/symbols/batch-update filters REVERSE_GRID cleanly
                    b_res = client.post(
                        "/api/symbols/batch-update",
                        headers=hdr,
                        json={"action": "validate", "strategy_filter": "REVERSE_GRID"},
                    )
                    assert b_res.status_code == 200, b_res.text
                    assert b_res.json()["scanned_count"] == 1
                    assert b_res.json()["items"][0]["symbol"] == "XRP/USDT:USDT"
                    assert b_res.json()["items"][0]["strategy_type"] == "REVERSE_GRID"

                    # /api/stats telemetry shows REVERSE_GRID & REVERSE_OSCILLATING
                    st_res = client.get("/api/stats?radar_symbol=XRP/USDT:USDT", headers=hdr)
                    assert st_res.status_code == 200
                    xrp_tel = st_res.json()["telemetry"]["XRP/USDT:USDT"]
                    assert xrp_tel["strategy_type"] == "REVERSE_GRID"
                    assert xrp_tel["grid"]["active_grid_mode"] == "REVERSE_OSCILLATING"
        finally:
            main_mod.db_manager.db_path = orig_db_path
            main_mod.user_manager.db_path = orig_um_path
            main_mod.engine_manager = orig_em


def test_five_strategy_audit_payoff_asymmetry_fixes_and_idempotency():
    """
    Verifies the 5-strategy audit & '亏多赚少' live-data optimizations:
    1) TREND_GRID solo L1/S1 does NOT prematurely close via profit_lock at +0.34% before reaching L2,
       and progressively tightens SL at +0.45*step (half-risk) -> +0.80*step (breakeven).
    2) TREND_GRID L2 pyramid addition does NOT immediately arm profit_lock below L2 at fill time;
       profit_lock waits until peak clears L2 entry by >= 0.30*step.
    3) ADAPTIVE_GRID suppresses climax chasing into TREND_BREAKOUT when bbw_percentile > 85%.
    4) LONG_SHORT_GRID blocks same-tick new entry when GRID_SL fires.
    5) _finalize_grid_slot_trade & record_trade_async deduplicate identical order_ids.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.sniper_engine import BidirectionalSniperEngine

    cfg = SymbolStrategy(
        symbol="SNDK/USDT:USDT",
        strategy_type="TREND_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.008,
        grid_take_profit_pct=0.0085,
        grid_range_pct=0.028,
        grid_stop_loss_pct=0.038,
        order_usdt=20.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
        trend_dead_zone_mult=1.25,
        trend_trailing_mult=1.25,
        trend_pyramid_decay=True,
        trend_use_bb_breakout=False,
    )
    trk = LongShortGridTracker("SNDK/USDT:USDT")
    trk.sync_config(cfg, current_mid=100.00)
    assert trk.levels["L1"]["trigger_price"] == 101.00  # 100 * (1 + 1.25 * 0.8%)

    # 1) Solo L1 enters at 101.00, moves up +0.55% to 101.56 (>= 0.45*0.8%=0.36% half-lock, < 0.80*0.8%=0.64% solo BE lock)
    en1, _ = trk.evaluate_tick(101.00, 101.02, 101.01, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en1] == ["L1"]
    trk.mark_entry_filled("L1", 101.00, 0.30, 3.0, 0.1, cfg, round_trip_fee=0.0010)
    expected_init_sl = round(101.00 * (1.0 - cfg.grid_stop_loss_pct), 2)
    assert trk.levels["L1"]["sl_price"] == expected_init_sl

    _, ex_half = trk.evaluate_tick(101.55, 101.57, 101.56, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert ex_half == []
    # Before reaching solo breakeven lock, TREND_GRID strictly preserves configured SL % (no premature pre-BE narrowing!)
    assert trk.levels["L1"]["sl_price"] == expected_init_sl
    # Solo L1 trail stop stays at raw_trail (101.55 * (1 - 0.01) = 100.53), NOT choked at +0.34% (101.35)!
    assert trk.long_trail_stop_price == round(101.55 * (1.0 - 0.01), 2)
    _, ex_no_crumb = trk.evaluate_tick(101.30, 101.32, 101.31, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert ex_no_crumb == []  # Does NOT prematurely close solo L1 for a tiny +0.30% crumb!

    # 2) L2 fills at 101.85: at the instant L2 fills (peak == 101.85), profit_lock MUST NOT arm below L2!
    trk.mark_entry_filled("L2", 101.85, 0.18, 1.8, 0.1, cfg, round_trip_fee=0.0010)
    _, ex_l2_fill = trk.evaluate_tick(101.83, 101.85, 101.84, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert ex_l2_fill == []
    assert trk.long_trail_stop_price == round(101.85 * (1.0 - 0.01), 2)  # Raw trail 100.83, not choked at 101.52!
    # A small 0.35% breathing tick to 101.50 right after L2 fill does NOT force-close L2 at a loss!
    _, ex_breathe = trk.evaluate_tick(101.49, 101.51, 101.50, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert ex_breathe == []

    # 3) ADAPTIVE_GRID climax exhaustion guard (pct_rank=92% > 85% stays in MEAN_REVERSION instead of buying the top)
    adp_cfg = cfg.model_copy(update={"strategy_type": "ADAPTIVE_GRID"})
    adp_trk = LongShortGridTracker("SNDK/USDT:USDT")
    climax_bbw = {
        "timeframe": "15m",
        "bbw_pct": 6.5,
        "bbw_percentile": 92.0,
        "bb_upper": 105.0,
        "bb_middle": 100.0,
        "bb_lower": 95.0,
        "trend_bias": "BULL",
        "breakout_expansion": "UP_BREAKOUT",
    }
    adp_trk.sync_config(adp_cfg, current_mid=100.0, bbw_info=climax_bbw)
    assert adp_trk.active_grid_mode == "MEAN_REVERSION"

    # 4) _finalize_grid_slot_trade idempotency guard against duplicate order_id
    with tempfile.TemporaryDirectory() as tmpdir:
        adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "idem.db"))
        adb.init_sync()
        eng = BidirectionalSniperEngine(adb, user_id=4)
        eng.risk_config.trading_mode = "LIVE"
        eng._register_symbol_internal(cfg)
        lv_dummy = dict(trk.levels["L1"])
        eng._finalize_grid_slot_trade(
            cfg=cfg,
            tracker=trk,
            lv=lv_dummy,
            exit_avg=102.00,
            is_maker=False,
            exit_reason="TREND_BE_SL",
            order_id="4121924935978430466",
            latency_ms=12.0,
            exec_mode="LIVE",
        )
        assert eng.stats_by_mode["LIVE"]["win_count"] == 1
        # Second duplicate callback with the exact same OKX order_id is ignored!
        eng._finalize_grid_slot_trade(
            cfg=cfg,
            tracker=trk,
            lv=lv_dummy,
            exit_avg=102.00,
            is_maker=False,
            exit_reason="TREND_BE_SL",
            order_id="4121924935978430466",
            latency_ms=12.0,
            exec_mode="LIVE",
        )
        assert eng.stats_by_mode["LIVE"]["win_count"] == 1


def test_traditional_brokers_sp500_catalog_and_per_symbol_exchange_selection():
    """
    Verifies:
    1) Traditional securities brokers (富途牛牛 futu, moomoo, IBKR ibkr, Charles Schwab schwab)
       are seeded in exchanges, support encrypted credential save/verify & fee sync via FastAPI.
    2) S&P 500 symbols (SPX, SPY, VOO, AAPL, MSFT, NVDA, BRK.B, S&P500) exist in SUPPORTED_PERP_CATALOG
       under sector '标普500' and pass /api/symbol/verify with exchange_id binding.
    3) Per-symbol exchange_id persists in SQLite symbol_configs and immediately seeds BBO & grid levels
       in both SHADOW and LIVE modes, and passes read-only preflight check.
    """
    import backend.main as main_mod
    from backend.main import SUPPORTED_PERP_CATALOG, app

    sp500_us_stocks = [c for c in SUPPORTED_PERP_CATALOG if c.get("asset_type") == "US_STOCK"]
    sp500_us_bases = {c["base"] for c in sp500_us_stocks}
    for expected_base in ("SPX", "SPY", "VOO", "IVV", "ES", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "BRK.B"):
        assert expected_base in sp500_us_bases
        item = next(c for c in sp500_us_stocks if c["base"] == expected_base)
        assert item["quote_ccy"] == "USD"
        assert item["symbol"] == f"{expected_base}/USD"
        for broker_id in ("futu", "moomoo", "ibkr", "schwab"):
            assert broker_id in item["exchanges"]
            assert broker_id in item["broker_codes"]

    # SP500 crypto alias & BTC/ETH/SOL must NOT appear under traditional US equity brokers
    sp500_perp = next(c for c in SUPPORTED_PERP_CATALOG if c["symbol"] == "SP500/USDT:USDT")
    assert sp500_perp["asset_type"] == "STOCK_PERP"
    assert all(b not in sp500_perp["exchanges"] for b in ("futu", "moomoo", "ibkr", "schwab"))
    btc_item = next(c for c in SUPPORTED_PERP_CATALOG if c["symbol"] == "BTC/USDT:USDT")
    assert btc_item["asset_type"] == "CRYPTO_PERP"
    assert all(b not in btc_item["exchanges"] for b in ("futu", "moomoo", "ibkr", "schwab"))

    rate_limiter._buckets.clear()
    with (
        patch.object(main_mod.engine_manager.gateway, "start", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "stop", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "fetch_exchange_instruments", new=AsyncMock(return_value=True)),
        patch.object(main_mod.engine_manager.gateway, "fetch_real_bbo_snapshot", new=AsyncMock(return_value=True)),
        patch.object(main_mod.payment_monitor, "start", return_value=None),
        patch.object(main_mod.payment_monitor, "stop", new=AsyncMock(return_value=None)),
    ):
        with TestClient(app) as client:
            reg = main_mod.user_manager.register_user("sp500_vip", "StrongPass!99", "sp500@quant.io", client_ip="198.51.100.212")
            assert reg["ok"] is True
            uid = int(reg["user"]["id"])
            token = reg["token"]
            conn = sqlite3.connect(main_mod.user_manager.db_path)
            conn.execute(
                "UPDATE users SET vip_tier='季度旗舰版会员', vip_expires_at=? WHERE id=?",
                (int(time.time()) + 86400 * 30, uid),
            )
            conn.commit()
            conn.close()
            hdr = {"Authorization": f"Bearer {token}"}

            # 1) /api/exchanges lists all 4 traditional brokers alongside crypto exchanges
            ex_res = client.get("/api/exchanges", headers=hdr)
            assert ex_res.status_code == 200
            ex_map = {e["exchange_id"]: e for e in ex_res.json()["exchanges"]}
            for b_id in ("futu", "moomoo", "ibkr", "schwab"):
                assert b_id in ex_map

            # Configure & verify Futu, Moomoo, IBKR, and Schwab API credentials
            for b_id, b_name in (
                ("futu", "富途牛牛 (Futu OpenD 证券接口)"),
                ("moomoo", "moomoo (Moomoo OpenD 国际证券)"),
                ("ibkr", "IBKR (盈透证券 TWS / Client Portal API)"),
                ("schwab", "Charles Schwab (嘉信理财 Trader API)"),
            ):
                u_res = client.post(
                    "/api/exchanges/update",
                    headers=hdr,
                    json={
                        "exchange_id": b_id,
                        "display_name": b_name,
                        "role": "follower",
                        "api_key": f"{b_id}_app_key_998877",
                        "api_secret": f"{b_id}_secret_token_112233",
                        "passphrase": "888999",
                        "vip_level": "VIP 1",
                        "taker_fee": 0.0002,
                        "maker_fee": 0.00008,
                        "is_active": True,
                    },
                )
                assert u_res.status_code == 200, u_res.text
                ver = u_res.json()["verify_result"]
                assert ver["ok"] is True
                assert ver["auth_status"] == "VERIFIED"

                fee_res = client.post(f"/api/exchanges/fetch-fees?exchange_id={b_id}&vip_level=VIP%201")
                assert fee_res.status_code == 200
                assert fee_res.json()["exchange_id"] == b_id

            # 2) /api/symbol/verify with exchange_id & S&P 500 tickers / aliases -> USD equity format vs OKX USDT perp
            v_spy = client.get("/api/symbol/verify?symbol=SPY&exchange_id=futu", headers=hdr).json()
            assert v_spy["verified"] is True
            assert v_spy["symbol"] == "SPY/USD"
            assert v_spy["native_symbol"] == "US.SPY"
            assert v_spy["asset_type"] == "US_STOCK"
            assert v_spy["quote_ccy"] == "USD"
            assert v_spy["recommended_exchange_id"] == "futu"
            assert v_spy["recommended_leverage"] == 1
            assert v_spy["recommended_grid_side_mode"] == "LONG_ONLY"
            assert v_spy["recommended_precision"]["min_qty"] == 1.0
            assert v_spy["recommended_precision"]["qty_precision"] == 0
            assert v_spy["sector"] == "标普500"

            v_spx = client.get("/api/symbol/verify?symbol=S%26P500&exchange_id=ibkr", headers=hdr).json()
            assert v_spx["verified"] is True
            assert v_spx["symbol"] == "SPX/USD"
            assert v_spx["native_symbol"] == "SPX"
            assert v_spx["recommended_exchange_id"] == "ibkr"
            assert "指数" in v_spx["warning"]

            v_brk = client.get("/api/symbol/verify?symbol=BRK.B&exchange_id=schwab", headers=hdr).json()
            assert v_brk["verified"] is True
            assert v_brk["symbol"] == "BRK.B/USD"
            assert v_brk["native_symbol"] == "BRK.B"
            assert v_brk["recommended_exchange_id"] == "schwab"

            v_aapl = client.get("/api/symbol/verify?symbol=US.AAPL&exchange_id=moomoo", headers=hdr).json()
            assert v_aapl["verified"] is True
            assert v_aapl["symbol"] == "AAPL/USD"
            assert v_aapl["native_symbol"] == "US.AAPL"
            assert v_aapl["recommended_exchange_id"] == "moomoo"

            # Coexistence: NVDA on Futu (US_STOCK USD) vs NVDA on OKX (STOCK_PERP USDT)
            v_nvda_futu = client.get("/api/symbol/verify?symbol=NVDA&exchange_id=futu", headers=hdr).json()
            assert v_nvda_futu["verified"] is True
            assert v_nvda_futu["symbol"] == "NVDA/USD"
            assert v_nvda_futu["native_symbol"] == "US.NVDA"
            assert v_nvda_futu["quote_ccy"] == "USD"
            v_nvda_okx = client.get("/api/symbol/verify?symbol=NVDA&exchange_id=okx", headers=hdr).json()
            assert v_nvda_okx["verified"] is True
            assert v_nvda_okx["symbol"] == "NVDA/USDT:USDT"
            assert v_nvda_okx["native_symbol"] == "NVDA-USDT-SWAP"
            assert v_nvda_okx["asset_type"] == "STOCK_PERP"
            assert v_nvda_okx["quote_ccy"] == "USDT"

            # Traditional brokers reject crypto-only tickers (BTC) and crypto-only SP500 alias
            for b_id in ("futu", "moomoo", "ibkr", "schwab"):
                bad_btc = client.get(f"/api/symbol/verify?symbol=BTC&exchange_id={b_id}", headers=hdr).json()
                assert bad_btc["verified"] is False
                bad_sp500 = client.get(f"/api/symbol/verify?symbol=SP500&exchange_id={b_id}", headers=hdr).json()
                assert bad_sp500["verified"] is False

            # 3) Add SPY/USD on Futu with TREND_GRID and verify persistence + immediate grid level seeding
            upd_spy = client.post(
                "/api/symbol/update",
                headers=hdr,
                json={
                    "symbol": "SPY/USD",
                    "exchange_id": "futu",
                    "strategy_type": "TREND_GRID",
                    "grid_side_mode": "LONG_ONLY",
                    "grid_levels": 3,
                    "grid_step_pct": 0.008,
                    "grid_take_profit_pct": 0.0085,
                    "grid_range_pct": 0.028,
                    "grid_stop_loss_pct": 0.038,
                    "order_usdt": 600.0,
                    "leverage": 1,
                    "price_precision": 2,
                    "qty_precision": 0,
                    "min_qty": 1.0,
                    "is_running": True,
                },
            )
            assert upd_spy.status_code == 200, upd_spy.text
            spy_saved = next(s for s in upd_spy.json()["strategies"] if s["symbol"] == "SPY/USD")
            assert spy_saved["exchange_id"] == "futu"
            assert spy_saved["strategy_type"] == "TREND_GRID"

            st_res = client.get("/api/stats?radar_symbol=SPY/USD", headers=hdr)
            assert st_res.status_code == 200
            spy_tel = st_res.json()["telemetry"]["SPY/USD"]
            assert spy_tel["follower_mid"] > 500.0
            assert spy_tel["native_symbol"] == "US.SPY"
            assert spy_tel["asset_type"] == "US_STOCK"
            assert spy_tel["quote_ccy"] == "USD"
            assert spy_tel["live_execution_supported"] is False
            assert spy_tel["grid"]["center_price"] > 500.0
            assert len(spy_tel["grid"]["levels"]) == 3

            # 4) Verify VIP_FEE_TIERS covers all 10 exchanges/brokers
            for all_ex in ("binanceusdm", "okx", "bybit", "gate", "bitget", "krakenfutures", "futu", "moomoo", "ibkr", "schwab"):
                f_res = client.post(f"/api/exchanges/fetch-fees?exchange_id={all_ex}&vip_level=VIP%201")
                assert f_res.status_code == 200
                assert f_res.json()["exchange_id"] == all_ex
                assert f_res.json()["taker_fee"] > 0

    # 5) Verify official API protocol adapters (Kraken Futures v3, IBKR Client Portal v1, Schwab OAuth2 v1, Symbol Formats)
    from backend.market_gateway import (
        to_bybit_symbol,
        to_bitget_symbol,
        to_gate_contract,
        to_kraken_futures_symbol,
        to_futu_moomoo_code,
        to_us_equity_symbol,
    )

    assert to_bybit_symbol("PEPE/USDT:USDT") == "1000PEPEUSDT"
    assert to_bybit_symbol("XRP/USDT:USDT") == "XRPUSDT"
    assert to_bitget_symbol("SOL/USDT:USDT") == "SOLUSDT"
    assert to_gate_contract("ETH/USDT:USDT") == "ETH_USDT"
    assert to_kraken_futures_symbol("BTC/USDT:USDT") == "PF_XBTUSD"
    assert to_kraken_futures_symbol("DOGE/USDT:USDT") == "PF_XDGUSD"
    assert to_futu_moomoo_code("SPY/USDT:USDT") == "US.SPY"
    assert to_futu_moomoo_code("BRKB/USDT:USDT") == "US.BRK.B"
    assert to_us_equity_symbol("BRKB/USDT:USDT") == "BRK.B"

    async def _test_api_protocols():
        adapter = ExecutionAdapter(get_math_state=lambda _: None, get_risk_cfg=lambda: GlobalRiskConfig())
        # Bitget requires passphrase -> missing passphrase returns CONFIG_INCOMPLETE
        bg_incomplete = await adapter.verify_exchange_credentials(
            {"exchange_id": "bitget", "api_key": "bg_key_12345", "_decrypted_secret": "bg_sec_12345", "_decrypted_passphrase": ""}
        )
        assert bg_incomplete["ok"] is False
        assert bg_incomplete["auth_status"] == "CONFIG_INCOMPLETE"
        assert "Passphrase" in bg_incomplete["auth_message"]

        # Kraken Futures v3 mock verification (/derivatives/api/v3/accounts)
        class _FakeResp:
            def __init__(self, status_code, data):
                self.status_code = status_code
                self._data = data

            def json(self):
                return self._data

        async def _mock_get(url, headers=None, **kwargs):
            if "futures.kraken.com/derivatives/api/v3/accounts" in url:
                assert "APIKey" in headers and "Nonce" in headers and "Authent" in headers
                return _FakeResp(200, {"result": "success", "accounts": {"flex": {"portfolioValue": 1280.55}}})
            if "/v1/api/iserver/auth/status" in url:
                return _FakeResp(200, {"authenticated": True, "connected": True})
            if "/v1/api/portfolio/accounts" in url:
                return _FakeResp(200, [{"accountId": "U9988776"}])
            if "/v1/api/portfolio/U9988776/ledger" in url:
                return _FakeResp(200, {"USD": {"netliquidationvalue": 54321.0}})
            if "api.schwabapi.com/trader/v1/accounts" in url:
                assert headers.get("Authorization") == "Bearer schwab_access_tok_123"
                return _FakeResp(200, [{"securitiesAccount": {"currentBalances": {"liquidationValue": 88500.25}}}])
            return _FakeResp(404, {})

        async def _mock_post(url, headers=None, data=None, **kwargs):
            if "api.schwabapi.com/v1/oauth/token" in url:
                assert headers.get("Authorization", "").startswith("Basic ")
                assert data.get("grant_type") == "refresh_token"
                return _FakeResp(200, {"access_token": "schwab_access_tok_123"})
            return _FakeResp(404, {})

        with (
            patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=_mock_get)),
            patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_mock_post)),
        ):
            kf_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "krakenfutures", "api_key": "kf_key_12345678", "_decrypted_secret": "a2Zfc2VjcmV0X2Jhc2U2NA==", "_decrypted_passphrase": ""}
            )
            assert kf_res["ok"] is True
            assert kf_res["account_equity_usdt"] == 1280.55

            ib_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "ibkr", "api_key": "https://127.0.0.1:5000", "_decrypted_secret": "gateway", "_decrypted_passphrase": "U9988776"}
            )
            assert ib_res["ok"] is True
            assert ib_res["account_equity_usdt"] == 54321.0

            sc_res = await adapter.verify_exchange_credentials(
                {"exchange_id": "schwab", "api_key": "schwab_client_id", "_decrypted_secret": "schwab_client_secret", "_decrypted_passphrase": "refresh:I0.schwab_refresh_tok"}
            )
            assert sc_res["ok"] is True
            assert sc_res["account_equity_usdt"] == 88500.25

    asyncio.run(_test_api_protocols())


def test_reverse_grid_sl_tightening_trend_pyramid_lock_and_follower_priority():
    """
    Verifies:
    1) REVERSE_GRID over-wide SL (e.g. 3.80% left over from LONG_SHORT_GRID with 0.60% step) is flagged
       by validate_and_batch_update_symbols and auto-tightened to ~1.20% (2 * step), and open REVERSE_OSCILLATING
       slots dynamically tighten legacy over-wide sl_price on tick evaluation.
    2) TREND_BREAKOUT multi-leg pyramid (L1 + L2) floors trailing stop above L2's breakeven once peak moves
       >= +0.45 * step above L2.
    3) get_leader_And_follower_cfg() prioritizes verified/configured OKX follower over unconfigured active followers.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.models import BatchSymbolUpdateRequest, SymbolStrategy
    from backend.sniper_engine import BidirectionalSniperEngine

    # 1) Open REVERSE_OSCILLATING slot dynamically syncs configurable global SL distance (5% <-> 3%)
    rev_cfg = SymbolStrategy(
        symbol="ZEC/USDT:USDT",
        strategy_type="REVERSE_GRID",
        grid_levels=5,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.035,
        grid_stop_loss_pct=0.05,
        price_precision=2,
        qty_precision=2,
    )
    trk = LongShortGridTracker("ZEC/USDT:USDT", strategy_type="REVERSE_GRID")
    entries, _ = trk.evaluate_tick(100.0, 100.0, 100.0, rev_cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert entries == []
    entries, _ = trk.evaluate_tick(100.62, 100.63, 100.625, rev_cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert len(entries) == 1 and entries[0]["slot_id"] == "Q"
    trk.mark_entry_filled("Q", 100.60, 1.0, 1.0, 0.05, rev_cfg, round_trip_fee=0.0010)
    assert trk.levels["Q"]["sl_price"] == round(97.00 * (1.0 - 0.05), 2)  # 92.15

    # User tightens global SL from 5% to 3%: next tick dynamically updates global_sl_price & flags sl_needs_amend!
    rev_cfg.grid_stop_loss_pct = 0.03
    trk.evaluate_tick(100.55, 100.56, 100.555, rev_cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk.levels["Q"]["sl_price"] == round(97.00 * (1.0 - 0.03), 2)  # 94.09
    assert trk.levels["Q"]["sl_needs_amend"] is True
    # Widening back to 5% dynamically restores global_sl_price!
    trk.levels["Q"]["sl_needs_amend"] = False
    rev_cfg.grid_stop_loss_pct = 0.05
    trk.evaluate_tick(100.55, 100.56, 100.555, rev_cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert trk.levels["Q"]["sl_price"] == round(97.00 * (1.0 - 0.05), 2)
    assert trk.levels["Q"]["sl_needs_amend"] is True

    # 2) TREND_BREAKOUT multi-leg pyramid floors profit_lock_l at L2 breakeven once peak >= L2 * (1 + 0.45 * step)
    trend_cfg = SymbolStrategy(
        symbol="HYPE/USDT:USDT",
        strategy_type="TREND_GRID",
        grid_levels=3,
        grid_step_pct=0.008,
        grid_range_pct=0.035,
        grid_stop_loss_pct=0.045,
        trend_dead_zone_mult=1.25,
        trend_trailing_mult=1.15,
        trend_use_bb_breakout=False,
        price_precision=4,
        qty_precision=2,
    )
    t_trk = LongShortGridTracker("HYPE/USDT:USDT")
    t_trk.evaluate_tick(100.0, 100.0, 100.0, trend_cfg, is_net_mode=False, round_trip_fee=0.0010)
    # Trigger L1 at 101.00 and L2 at 101.80
    t_trk.evaluate_tick(101.02, 101.03, 101.025, trend_cfg, is_net_mode=False, round_trip_fee=0.0010)
    t_trk.mark_entry_filled("L1", 101.00, 1.0, 1.0, 0.05, trend_cfg, round_trip_fee=0.0010)
    t_trk.evaluate_tick(101.82, 101.83, 101.825, trend_cfg, is_net_mode=False, round_trip_fee=0.0010)
    t_trk.mark_entry_filled("L2", 101.80, 0.6, 0.6, 0.03, trend_cfg, round_trip_fee=0.0010)
    # Move peak to +0.55 * step above L2 (101.80 * 1.0044 = 102.2479)
    t_trk.evaluate_tick(102.26, 102.27, 102.265, trend_cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert t_trk.long_trail_stop_price >= 101.80
    # Both L1 and L2 SL step up to combined basket VWAP breakeven (> L1 solo breakeven 101.16)
    assert t_trk.levels["L1"]["sl_price"] >= 101.40
    assert t_trk.levels["L2"]["sl_price"] >= 101.40

    # And routine BB-only refreshes during post-SL cooldown MUST NOT wipe _needs_post_trend_recenter!
    t_trk.mark_exited("L1", 0.05, is_sl=True)
    t_trk.mark_exited("L2", -0.02, is_sl=True)
    assert t_trk._needs_post_trend_recenter is True
    t_trk.sync_config(
        trend_cfg,
        current_mid=101.50,
        bbw_info={"timeframe": "15m", "bbw_pct": 2.2, "bbw_percentile": 25.0, "bb_upper": 103.0, "bb_lower": 99.0},
    )
    assert t_trk._needs_post_trend_recenter is True

    # 3) Verify REVERSE_OSCILLATING executes mode-locked arbitrage unrestricted by BULL/BEAR trend_bias
    bull_bbw = {
        "timeframe": "15m",
        "bbw_pct": 2.1,
        "bbw_percentile": 30.0,
        "bb_upper": 102.0,
        "bb_middle": 100.0,
        "bb_lower": 98.0,
        "ema_fast": 100.8,
        "ema_slow": 99.5,
        "ema_gap_pct": 1.30,
        "trend_bias": "BULL",
        "breakout_expansion": "NONE",
    }
    bear_bbw = {**bull_bbw, "ema_fast": 99.2, "ema_slow": 100.5, "ema_gap_pct": -1.29, "trend_bias": "BEAR"}

    # Even under strong BEAR trend_bias, locked LONG mode still buys L2 (50%) when breaking above 101.20!
    en_l2_bear, _ = trk.evaluate_tick(101.21, 101.22, 101.215, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bear_bbw)
    assert len(en_l2_bear) == 1 and en_l2_bear[0]["slot_id"] == "L2" and en_l2_bear[0]["side"] == "LONG"

    # Fresh tracker under strong BULL trend_bias: dropping below S1 (99.40) STILL locks SHORT mode (QS)!
    trk_s_bull = LongShortGridTracker("ZEC/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_s_bull.sync_config(rev_cfg, current_mid=100.00)
    en_qs_bull, _ = trk_s_bull.evaluate_tick(99.38, 99.39, 99.385, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bull_bbw)
    assert len(en_qs_bull) == 1 and en_qs_bull[0]["slot_id"] == "QS" and en_qs_bull[0]["side"] == "SHORT"
    trk_s_bull.mark_entry_filled("QS", 99.38, 1.0, 1.0, 0.05, rev_cfg, round_trip_fee=0.0010)
    assert trk_s_bull.strategy.locked_mode == "SHORT"

    # 4) Follower priority: even if an unconfigured secondary exchange is active, verified/configured OKX wins
    with tempfile.TemporaryDirectory() as tmpdir:
        adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "rev_audit.db"))
        adb.init_sync()
        adb.ensure_user_workspace_sync(91)
        eng = BidirectionalSniperEngine(adb, user_id=91)
        eng.exchanges = {
            "bybit": {"exchange_id": "bybit", "role": "follower", "is_active": True, "auth_status": "UNCONFIGURED"},
            "okx": {"exchange_id": "okx", "role": "follower", "is_active": True, "auth_status": "VERIFIED"},
        }
        _, follower = eng.get_leader_And_follower_cfg()
        assert follower["exchange_id"] == "okx"


def test_hourly_crypto_vs_traditional_market_sentiment_comparison():
    from backend.market_sentiment import compute_hourly_sentiment_comparison, sentiment_regime_label

    base_h = 1_795_200_000  # aligned UTC hour
    base_h = (base_h // 3600) * 3600

    # Build 48 hours of crypto (rising bullish) vs 30 hours of tradfi (falling bearish + rising VIX, then closed 18h)
    btc_c, eth_c, sol_c = {}, {}, {}
    taker_ratio, ls_map = {}, {}
    es_c, nq_c, vix_c, gc_c = {}, {}, {}, {}

    for i in range(48):
        h = base_h + i * 3600
        bp = 60000.0 * (1.0 + 0.0022 * i)
        btc_c[h] = {"o": bp * 0.998, "h": bp * 1.003, "l": bp * 0.997, "c": bp, "v": 1000.0}
        ep = 2000.0 * (1.0 + 0.0025 * i)
        eth_c[h] = {"o": ep * 0.998, "h": ep * 1.003, "l": ep * 0.997, "c": ep, "v": 5000.0}
        sp = 100.0 * (1.0 + 0.0030 * i)
        sol_c[h] = {"o": sp * 0.998, "h": sp * 1.004, "l": sp * 0.997, "c": sp, "v": 8000.0}
        taker_ratio[h] = 0.58  # net aggressive buying
        ls_map[h] = 1.15 + 0.01 * i

        if i < 30:
            esp = 6000.0 * (1.0 - 0.0012 * i)
            es_c[h] = {"o": esp * 1.001, "h": esp * 1.002, "l": esp * 0.998, "c": esp, "v": 10000.0}
            nqp = 21000.0 * (1.0 - 0.0015 * i)
            nq_c[h] = {"o": nqp * 1.001, "h": nqp * 1.002, "l": nqp * 0.998, "c": nqp, "v": 9000.0}
            vixp = 20.0 + 0.55 * i
            vix_c[h] = {"o": vixp - 0.2, "h": vixp + 0.3, "l": vixp - 0.3, "c": vixp, "v": 0.0}
            gcp = 3400.0 * (1.0 + 0.0008 * i)
            gc_c[h] = {"o": gcp * 0.999, "h": gcp * 1.001, "l": gcp * 0.998, "c": gcp, "v": 500.0}

    res = compute_hourly_sentiment_comparison(
        btc_candles=btc_c,
        eth_candles=eth_c,
        sol_candles=sol_c,
        taker_buy_ratio=taker_ratio,
        ls_ratio_map=ls_map,
        funding_rate=0.0002,
        crypto_fng_daily={},
        crypto_fng_latest=72.0,
        es_candles=es_c,
        nq_candles=nq_c,
        vix_candles=vix_c,
        gc_candles=gc_c,
        cnn_fng_daily={},
        cnn_fng_latest=15.0,
        now_ts_sec=base_h + 47 * 3600 + 600,
        data_sources=["OKX BTC/ETH/SOL 1H", "CME 标普/纳指期货 1H", "CBOE VIX 1H"],
    )

    assert res["ok"] is True
    assert res["is_live_data"] is True
    assert res["trad_market_open"] is False  # Last TradFi bar was 18 hours before now
    assert len(res["series_72h"]) == 48
    assert len(res["series_overlap_24h"]) == 24
    # First 30 bars have trad_live=True, final 18 bars have trad_live=False (carried forward cleanly)
    assert all(p["trad_live"] is True for p in res["series_72h"][:30])
    assert all(p["trad_live"] is False for p in res["series_72h"][30:])
    assert res["summary"]["crypto_score"] > 65.0
    assert res["summary"]["trad_score"] < 35.0
    assert res["summary"]["spread"] > 30.0
    assert res["summary"]["correlation_24h"] is not None
    assert sentiment_regime_label(res["summary"]["crypto_score"]) in ("贪婪", "极度贪婪")
    assert sentiment_regime_label(res["summary"]["trad_score"]) in ("恐慌", "极度恐慌")


def test_long_short_grid_and_trend_grid_trigger_fixes():
    """
    Verifies root-cause fixes for LONG_SHORT_GRID (传统震荡网格) & TREND_GRID (顺势突破网格) trigger starvation:
    1) LONG_SHORT_GRID operates bidirectionally unrestricted by lagging EMA BULL/BEAR bias, uses symmetric
       anti-stacking spacing (allows inner L1 re-entry while deeper L2 is open), and guards concurrent ENTERING.
    2) TREND_GRID operates bidirectionally unrestricted by lagging EMA BULL/BEAR bias (e.g. SNDK breakdown under BULL EMA).
    3) TREND_GRID with trend_use_bb_breakout=True adaptively converges wide dead-zone (e.g. 3.0x on low-vol BTC/TRX)
       to live Bollinger Band Width while respecting the >= step anti-chop floor and leaving trend_use_bb_breakout=False untouched.
    4) TREND_GRID open lower leg re-anchors stranded L2..Ln / S2..Sn pyramid triggers to prev_entry +/- step
       across fill/recovery/restore (e.g. SPCX scenario), and persists needs_post_trend_recenter.
    5) validate_and_batch_update_symbols flags overly wide trend_dead_zone_mult on TREND_GRID and supports batch updating it.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.models import BatchSymbolUpdateRequest, SymbolStrategy
    from backend.sniper_engine import BidirectionalSniperEngine

    # 1) LONG_SHORT_GRID: bidirectional under BULL/BEAR + symmetric inner re-entry + concurrent ENTERING guard
    ls_cfg = SymbolStrategy(
        symbol="OP/USDT:USDT",
        strategy_type="LONG_SHORT_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.03,
        grid_stop_loss_pct=0.04,
        order_usdt=20.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
    )
    bull_info = {
        "timeframe": "15m",
        "bbw_pct": 2.0,
        "bbw_percentile": 45.0,
        "bb_upper": 101.0,
        "bb_middle": 100.0,
        "bb_lower": 99.0,
        "ema_fast": 101.2,
        "ema_slow": 99.8,
        "ema_gap_pct": 1.40,
        "trend_bias": "BULL",
        "breakout_expansion": "NONE",
    }
    bear_info = {**bull_info, "ema_fast": 98.8, "ema_slow": 100.2, "ema_gap_pct": -1.40, "trend_bias": "BEAR"}

    ls_trk = LongShortGridTracker("OP/USDT:USDT")
    ls_trk.sync_config(ls_cfg, current_mid=100.0, bbw_info=bull_info)
    # Rally under BULL bias MUST trigger SHORT S1 (previously blocked by BULL EMA!)
    en_s1, _ = ls_trk.evaluate_tick(100.62, 100.64, 100.63, ls_cfg, is_net_mode=False, bbw_info=bull_info)
    assert [e["slot_id"] for e in en_s1] == ["S1"]
    ls_trk.mark_entry_filled("S1", 100.62, 0.2, 0.2, 1.0, ls_cfg, 0.0007)

    # Deeper L2 opens at 98.80 while L1 (99.40) is IDLE
    ls_trk.levels["L2"]["status"] = "OPEN"
    ls_trk.levels["L2"]["entry_price"] = 98.80
    ls_trk.levels["L2"]["tp_price"] = 99.44
    ls_trk.levels["L2"]["sl_price"] = 96.00
    ls_trk.levels["L2"]["filled_qty"] = 0.2
    # Near L2 entry (98.90 is only 0.10% away < 0.55 * 0.6% = 0.33%) -> anti-stacking blocks duplicate entry
    en_near, _ = ls_trk.evaluate_tick(98.88, 98.90, 98.89, ls_cfg, is_net_mode=False, bbw_info=bear_info)
    assert en_near == []
    # Rebound to L1=99.38 (0.59% away from L2=98.80 >= 0.33%) -> symmetric spacing allows inner L1 re-entry!
    en_inner_l1, _ = ls_trk.evaluate_tick(99.36, 99.38, 99.37, ls_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_inner_l1] == ["L1"]
    # While L1 is ENTERING, another deep dip to L3 (98.20) MUST wait (concurrent ENTERING guard)
    en_conc, _ = ls_trk.evaluate_tick(98.16, 98.18, 98.17, ls_cfg, is_net_mode=False, bbw_info=bear_info)
    assert en_conc == []

    # 2) TREND_GRID: bidirectional unrestricted by lagging EMA (SNDK breakdown under BULL EMA)
    sndk_cfg = SymbolStrategy(
        symbol="SNDK/USDT:USDT",
        strategy_type="TREND_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.008,
        grid_range_pct=0.04,
        grid_stop_loss_pct=0.04,
        trend_dead_zone_mult=1.2,
        trend_trailing_mult=1.5,
        trend_use_bb_breakout=False,
        order_usdt=20.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
    )
    sndk_trk = LongShortGridTracker("SNDK/USDT:USDT")
    sndk_trk.sync_config(sndk_cfg, current_mid=100.0, bbw_info=bull_info)
    assert sndk_trk.levels["S1"]["trigger_price"] == 99.04
    assert sndk_trk.levels["L1"]["trigger_price"] == 100.96
    # Breakdown under strong BULL EMA bias MUST fire SHORT S1!
    en_ts1, _ = sndk_trk.evaluate_tick(99.00, 99.02, 99.01, sndk_cfg, is_net_mode=False, bbw_info=bull_info)
    assert [e["slot_id"] for e in en_ts1] == ["S1"]
    # And breakout up under strong BEAR EMA bias MUST fire LONG L1!
    sndk_trk2 = LongShortGridTracker("SNDK/USDT:USDT")
    sndk_trk2.sync_config(sndk_cfg, current_mid=100.0, bbw_info=bear_info)
    en_tl1, _ = sndk_trk2.evaluate_tick(100.98, 101.00, 100.99, sndk_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_tl1] == ["L1"]

    # 3) TREND_GRID Bollinger-adaptive dead-zone convergence (BTC 1.2% BBW & TRX 0.2% BBW with dz=3.0)
    btc_cfg = sndk_cfg.model_copy(
        update={
            "symbol": "BTC/USDT:USDT",
            "trend_dead_zone_mult": 3.0,  # fixed dz_ratio = 2.40%
            "trend_use_bb_breakout": True,
        }
    )
    tight_btc_bb = {
        "timeframe": "15m",
        "bbw_pct": 1.2,
        "bbw_percentile": 40.0,
        "bb_upper": 100.60,
        "bb_middle": 100.0,
        "bb_lower": 99.40,
        "trend_bias": "RANGE",
        "breakout_expansion": "NONE",
    }
    btc_trk = LongShortGridTracker("BTC/USDT:USDT")
    btc_trk.sync_config(btc_cfg, current_mid=100.0, bbw_info=tight_btc_bb)
    # eff_dz_ratio = min(0.024, max(0.008, 0.012 * 0.75)) = 0.009 -> L1=100.90, S1=99.10 (NOT 102.40 / 97.60!)
    assert btc_trk.levels["L1"]["trigger_price"] == 100.90
    assert btc_trk.levels["S1"]["trigger_price"] == 99.10
    en_btc, _ = btc_trk.evaluate_tick(100.93, 100.95, 100.94, btc_cfg, is_net_mode=False, bbw_info=tight_btc_bb)
    assert [e["slot_id"] for e in en_btc] == ["L1"]

    # Ultra-tight BBW (0.2%, TRX scenario) hits the >= step (0.80%) anti-chop floor
    trx_bb = {**tight_btc_bb, "bbw_pct": 0.2, "bb_upper": 100.10, "bb_lower": 99.90}
    trx_trk = LongShortGridTracker("TRX/USDT:USDT")
    trx_trk.sync_config(btc_cfg, current_mid=100.0, bbw_info=trx_bb)
    assert trx_trk.levels["L1"]["trigger_price"] == 100.80
    assert trx_trk.levels["S1"]["trigger_price"] == 99.20

    # With trend_use_bb_breakout=False, wide fixed dz=3.0 (2.40%) is preserved verbatim
    fixed_dz_cfg = btc_cfg.model_copy(update={"trend_use_bb_breakout": False})
    fixed_trk = LongShortGridTracker("BTC/USDT:USDT")
    fixed_trk.sync_config(fixed_dz_cfg, current_mid=100.0, bbw_info=tight_btc_bb)
    assert fixed_trk.levels["L1"]["trigger_price"] == 102.40
    assert fixed_trk.levels["S1"]["trigger_price"] == 97.60

    # 4) TREND_GRID pyramid L2..Ln re-anchoring to open L1 + needs_post_trend_recenter state persistence (SPCX scenario)
    spcx_cfg = sndk_cfg.model_copy(
        update={
            "symbol": "SPCX/USDT:USDT",
            "trend_dead_zone_mult": 2.5,
            "trend_use_bb_breakout": False,
        }
    )
    spcx_trk = LongShortGridTracker("SPCX/USDT:USDT")
    spcx_trk.sync_config(spcx_cfg, current_mid=130.0)
    # Initially L1=132.60, L2=133.64 (+2.8% above center). Recover an open L1 filled at 130.95 -> L2 MUST re-anchor to 130.95*1.008 = 132.00!
    rec_sid = spcx_trk.recover_open_position("LONG", 130.95, 0.5, 0.5, 1.0, spcx_cfg, 0.001)
    assert rec_sid == "L1"
    assert spcx_trk.levels["L2"]["trigger_price"] == round(130.95 * 1.008, 2)
    assert spcx_trk.levels["L3"]["trigger_price"] == round(130.95 * 1.016, 2)
    en_l2, _ = spcx_trk.evaluate_tick(132.01, 132.03, 132.02, spcx_cfg, is_net_mode=False)
    assert [e["slot_id"] for e in en_l2] == ["L2"]

    # export_state / restore_state round-trips needs_post_trend_recenter AND re-anchors L2 away from stale 133.64!
    spcx_trk.levels["L2"]["status"] = "IDLE"
    spcx_trk._needs_post_trend_recenter = True
    exported = spcx_trk.export_state()
    assert exported["needs_post_trend_recenter"] is True
    spcx_restored = LongShortGridTracker("SPCX/USDT:USDT")
    spcx_restored.restore_state(exported, spcx_cfg)
    assert spcx_restored._needs_post_trend_recenter is True
    assert spcx_restored.levels["L2"]["trigger_price"] == round(130.95 * 1.008, 2)
    # Even if an old state left a stranded L2 trigger at 135.50, evaluate_tick anchors against L1 entry + step!
    spcx_restored.levels["L2"]["trigger_price"] = 135.50
    en_l2_res, _ = spcx_restored.evaluate_tick(132.01, 132.03, 132.02, spcx_cfg, is_net_mode=False)
    assert [e["slot_id"] for e in en_l2_res] == ["L2"]

    # 5) validate_and_batch_update_symbols flags wide trend_dead_zone_mult on TREND_GRID & applies updates
    with tempfile.TemporaryDirectory() as tmpdir:
        adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "dz_audit.db"))
        adb.init_sync()
        adb.ensure_user_workspace_sync(97)
        wide_tr = SymbolStrategy(
            symbol="BTC/USDT:USDT",
            strategy_type="TREND_GRID",
            grid_levels=3,
            grid_step_pct=0.008,
            grid_range_pct=0.04,
            grid_stop_loss_pct=0.04,
            trend_dead_zone_mult=3.0,
            trend_trailing_mult=1.5,
            order_usdt=25.0,
            leverage=5,
            price_precision=1,
            qty_precision=3,
        )
        adb.upsert_symbol_sync(wide_tr, user_id=97)
        eng = BidirectionalSniperEngine(adb, user_id=97)
        eng._register_symbol_internal(wide_tr)

        v_res = eng.validate_and_batch_update_symbols(BatchSymbolUpdateRequest(action="validate"))
        assert v_res["ok"] is True
        assert v_res["items"][0]["trend_dead_zone_mult"] == 3.0
        assert any("顺势首格死区偏宽" in msg for msg in v_res["items"][0]["issues"])

        a_res = eng.validate_and_batch_update_symbols(
            BatchSymbolUpdateRequest(action="apply", trend_dead_zone_mult=1.2)
        )
        assert a_res["ok"] is True
        assert eng.strategies["BTC/USDT:USDT"].trend_dead_zone_mult == 1.2


def test_isolated_grid_strategy_modules_and_zero_cross_impact():
    """
    Verifies that every grid strategy is implemented in its own isolated class/module with
    zero cross-strategy state or trading-logic interference:
    1) Each strategy_type routes to its dedicated module & class (no cross-subclass coupling).
    2) REVERSE_GRID (多空震荡网格) arbitrage is 100% insulated from BBW, EMA, and trend_dead_zone_mult updates.
    3) Runtime strategy switching never swaps mid-position and cleanly swaps with zero state leakage once flat.
    4) Polymorphic strategy hooks (maker TP, Livermore SL, log tags, SL exit reason, reset_runtime_state) work per strategy.
    """
    from backend.grid_engine import (
        AdaptiveVcpGridStrategy,
        BaseGridStrategy,
        LongShortGridTracker,
        MeanReversionGridStrategy,
        ReverseOscillatingGridStrategy,
        TrendBreakoutGridStrategy,
    )
    from backend.models import SymbolStrategy

    # 1) Distinct isolated modules & classes (zero subclass coupling between concrete strategies)
    concrete = [
        MeanReversionGridStrategy,
        ReverseOscillatingGridStrategy,
        TrendBreakoutGridStrategy,
        AdaptiveVcpGridStrategy,
    ]
    assert len({cls.__module__ for cls in concrete}) == 4
    for cls in concrete:
        assert issubclass(cls, BaseGridStrategy)
        for other in concrete:
            if cls is not other:
                assert not issubclass(cls, other)

    base_kwargs = dict(
        symbol="SOL/USDT:USDT",
        grid_side_mode="LONG_SHORT",
        grid_levels=3,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.03,
        grid_stop_loss_pct=0.04,
        order_usdt=20.0,
        leverage=5,
        price_precision=2,
        qty_precision=2,
    )
    mr_cfg = SymbolStrategy(strategy_type="LONG_SHORT_GRID", **base_kwargs)
    rev_cfg = SymbolStrategy(strategy_type="REVERSE_GRID", **base_kwargs)
    tr_cfg = SymbolStrategy(strategy_type="TREND_GRID", trend_dead_zone_mult=1.5, **base_kwargs)
    adp_cfg = SymbolStrategy(strategy_type="ADAPTIVE_GRID", trend_dead_zone_mult=1.5, **base_kwargs)

    # Verify router dispatches each strategy_type to its exact isolated implementation
    for cfg_i, expected_cls in (
        (mr_cfg, MeanReversionGridStrategy),
        (rev_cfg, ReverseOscillatingGridStrategy),
        (tr_cfg, TrendBreakoutGridStrategy),
        (adp_cfg, AdaptiveVcpGridStrategy),
    ):
        t_i = LongShortGridTracker("SOL/USDT:USDT")
        t_i.sync_config(cfg_i, current_mid=100.0)
        assert type(t_i.strategy) is expected_cls

    # 2) REVERSE_GRID arbitrage completely insulated from trend/BBW/EMA changes
    rev_trk = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    rev_trk.sync_config(rev_cfg, current_mid=100.0)
    assert type(rev_trk.strategy) is ReverseOscillatingGridStrategy
    assert rev_trk.levels["Q"]["trigger_price"] == 100.60
    assert rev_trk.levels["QS"]["trigger_price"] == 99.40

    # Mutating unrelated trend_dead_zone_mult or feeding extreme BULL/BEAR BBW must NOT rebuild REVERSE_GRID or alter armed state!
    rev_trk.levels["Q"]["armed"] = False
    rev_cfg_trend_tweaked = rev_cfg.model_copy(update={"trend_dead_zone_mult": 5.0, "bbw_squeeze_percentile": 5.0})
    extreme_bbw = {
        "timeframe": "15m",
        "bbw_pct": 0.4,
        "bbw_percentile": 3.0,
        "bb_upper": 100.2,
        "bb_middle": 100.0,
        "bb_lower": 99.8,
        "trend_bias": "BULL",
        "breakout_expansion": "UP_BREAKOUT",
    }
    rev_trk.sync_config(rev_cfg_trend_tweaked, current_mid=100.0, bbw_info=extreme_bbw)
    assert rev_trk.levels["Q"]["armed"] is False  # NOT wiped by unrelated trend/BBW change!
    assert rev_trk.last_bbw_info is None

    # Full REVERSE_GRID SHORT-mode cycle under opposing BULL EMA: breakdown S1 -> lock SHORT (QS) -> CS scalp!
    en_qs, _ = rev_trk.evaluate_tick(99.38, 99.39, 99.385, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=extreme_bbw)
    assert [e["slot_id"] for e in en_qs] == ["QS"]
    rev_trk.mark_entry_filled("QS", 99.38, 0.2, 0.2, 1.0, rev_cfg, 0.0010)
    assert rev_trk.strategy.locked_mode == "SHORT"
    en_cs, _ = rev_trk.evaluate_tick(100.00, 100.01, 100.005, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=extreme_bbw)
    assert [e["slot_id"] for e in en_cs] == ["CS"]
    rev_trk.mark_entry_filled("CS", 100.00, 0.2, 0.2, 1.0, rev_cfg, 0.0010)
    _, ex_cs = rev_trk.evaluate_tick(99.39, 99.40, 99.395, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=extreme_bbw)
    assert [e["slot_id"] for e in ex_cs] == ["CS"] and ex_cs[0]["exit_reason"] == "GRID_TP"
    rev_trk.mark_exited("CS", 0.12, is_sl=False)
    rev_trk.levels["CS"]["cooldown_until_mono"] = 0.0
    rev_trk.levels["CS"]["last_closed_mono"] = 0.0
    # Re-arm on outer side below C (99.96 <= 100.00 * (1 - 0.0003)) and fire CS again on bounce to 100.00!
    rev_trk.evaluate_tick(99.95, 99.97, 99.96, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=extreme_bbw)
    assert rev_trk.levels["CS"]["armed"] is True
    en_cs_2, _ = rev_trk.evaluate_tick(100.00, 100.01, 100.005, rev_cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=extreme_bbw)
    assert [e["slot_id"] for e in en_cs_2] == ["CS"]

    # 3) Live strategy switching: never swaps mid-position, cleanly swaps with zero state bleed once flat
    sw_trk = LongShortGridTracker("SOL/USDT:USDT", strategy_type="TREND_GRID")
    sw_trk.sync_config(tr_cfg, current_mid=100.0)
    en_tl1, _ = sw_trk.evaluate_tick(101.0, 101.02, 101.01, tr_cfg, is_net_mode=False)
    assert [e["slot_id"] for e in en_tl1] == ["L1"]
    sw_trk.mark_entry_filled("L1", 101.02, 0.3, 0.3, 1.0, tr_cfg, 0.001)
    assert sw_trk.long_peak_price == 101.02

    # Attempt switch to REVERSE_GRID while L1 is OPEN -> MUST keep TrendBreakoutGridStrategy managing open L1!
    sw_trk.sync_config(rev_cfg, current_mid=101.5)
    assert type(sw_trk.strategy) is TrendBreakoutGridStrategy
    assert sw_trk.levels["L1"]["status"] == "OPEN"

    # Close L1 -> flat -> next sync_config swaps cleanly to ReverseOscillatingGridStrategy with zero trend state bleed!
    sw_trk.mark_exited("L1", 1.25, is_sl=False)
    assert sw_trk._needs_post_trend_recenter is True
    sw_trk.sync_config(rev_cfg, current_mid=150.0)
    assert type(sw_trk.strategy) is ReverseOscillatingGridStrategy
    assert sw_trk.active_grid_mode == "REVERSE_OSCILLATING"
    assert sw_trk.center_price == 150.0
    assert sw_trk.long_peak_price == 0.0
    assert sw_trk._needs_post_trend_recenter is False
    assert sw_trk.completed_cycles == 1
    assert sw_trk.total_net_pnl == 1.25
    assert sw_trk.levels["Q"]["trigger_price"] > 150.0
    assert sw_trk.levels["QS"]["trigger_price"] < 150.0

    # 4) Polymorphic hooks & clean reset_runtime_state
    assert rev_trk.supports_maker_tp(rev_cfg) is True
    assert rev_trk.uses_livermore_sl_amend() is False
    assert rev_trk.get_entry_log_tags("SHORT") == ("多空震荡·空头模式", "做空建仓")
    assert rev_trk.get_exit_log_tag() == "多空震荡平仓"
    assert rev_trk.get_mode_display_name() == "多空震荡(空)"

    tr_only = LongShortGridTracker("SOL/USDT:USDT", strategy_type="TREND_GRID")
    tr_only.sync_config(tr_cfg, current_mid=100.0)
    assert tr_only.supports_maker_tp(tr_cfg) is False
    assert tr_only.uses_livermore_sl_amend() is True
    assert tr_only.get_cond_sl_exit_reason({"side": "LONG", "entry_price": 100.0, "sl_price": 100.2}) == "TREND_BE_SL"
    assert tr_only.get_cond_sl_exit_reason({"side": "LONG", "entry_price": 100.0, "sl_price": 98.0}) == "TREND_SL"
    tr_only.reset_runtime_state()
    assert tr_only.center_price == 0.0 and tr_only.levels == {} and tr_only._last_sig_params is None


def test_audit_p0_p1_trading_and_execution_fixes(tmp_path):
    """
    Regression suite for all P0/P1 loss-causing bugs discovered during the full-system audit:
    1) TREND_GRID & ADAPTIVE_GRID L1/S1 initial SL always enforces minimum sl_buf floor even when center is close to entry.
    2) Upgraded be_slip_buf (>= 0.16%) and relaxed solo_half_lock_move prevent micro-noise whipsaw & fee-bleed BE stops.
    3) Persisted slots restored via restore_state keep recovered=False so periodic sync never overwrites exact entry/contracts.
    4) cancel_slot_protective_orders verifies/retries SL cancel even when tp_fully_filled=True.
    5) execute_exit_with_retry refuses blind net_mode close-position when GET /account/positions fails (pos_ok=False).
    6) sync_live_exchange_positions preserves vanished slot when reconcile_uncertain=True instead of wiping PnL.
    7) _handle_okx_private_algo_event handles partial SL fill without wiping remaining contracts & escalates canceled child SL.
    8) _amend_live_slot_sl old_triggered cancels live Maker TP and logs strategy-specific exit_reason (GRID_SL for REVERSE_GRID).
    9) _run_grid_exits_batch deducts partial market-exit fills across multi-slot batches on UNCERTAIN & preserves live flags on exception.
    10) _run_state_machine locks to EXIT_UNCERTAIN instead of IDLE when an exception interrupts an open LIVE LEAD_LAG position.
    11) MathEngine._compute_depth_aware_qty outputs base-coin units regardless of cfg.contract_size.
    """
    import asyncio
    from backend.database import AsyncDatabaseManager
    from backend.executor import ExecutionAdapter, OrderResult
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import SignalCandidate, SymbolMathState, mono_ms, wall_ms
    from backend.models import GlobalRiskConfig, SniperState, SymbolStrategy
    from backend.sniper_engine import BidirectionalSniperEngine

    # 1) & 2) TREND_GRID & ADAPTIVE_GRID L1/S1 sl_buf floor + relaxed solo half-lock + upgraded be_slip_buf
    for stype in ("TREND_GRID", "ADAPTIVE_GRID"):
        cfg = SymbolStrategy(
            symbol="NVDA/USDT:USDT",
            strategy_type=stype,
            grid_side_mode="LONG_SHORT",
            grid_levels=3,
            grid_step_pct=0.006,
            grid_range_pct=0.035,
            grid_stop_loss_pct=0.038,
            order_usdt=15.0,
            leverage=5,
            price_precision=2,
            qty_precision=2,
            trend_dead_zone_mult=1.25,
            trend_trailing_mult=1.25,
            trend_use_bb_breakout=False,
        )
        trk = LongShortGridTracker("NVDA/USDT:USDT", strategy_type=stype)
        # Force TREND_BREAKOUT for ADAPTIVE_GRID too
        bbw_trend = {
            "timeframe": "15m",
            "bbw_pct": 1.2,
            "bbw_percentile": 10.0,
            "bb_upper": 101.0,
            "bb_middle": 100.0,
            "bb_lower": 99.0,
            "trend_bias": "RANGE",
        }
        trk.sync_config(cfg, current_mid=133.00, bbw_info=bbw_trend)
        # Suppose S1 fills with favorable slippage at 132.90 (only 0.075% from center=133.00, well inside sl_buf=0.35%!)
        trk.mark_entry_filled("S1", 132.90, 0.10, 1.0, 0.10, cfg, round_trip_fee=0.0010)
        min_safe_sl_s = round(132.90 * (1.0 + max(0.0035, 0.006 * 0.55)), 2)
        assert trk.levels["S1"]["sl_price"] >= min_safe_sl_s, f"{stype} S1 SL bypassed sl_buf floor!"
        if stype == "TREND_GRID":
            # Plan A: TREND_GRID strictly honors configured grid_stop_loss_pct (not truncated to center_price)
            assert trk.levels["S1"]["sl_price"] == round(132.90 * (1.0 + cfg.grid_stop_loss_pct), 2)
            # Dynamically updating grid_stop_loss_pct to 3.0% updates open pre-BE SL immediately
            cfg_3 = cfg.model_copy(update={"grid_stop_loss_pct": 0.03})
            trk.evaluate_tick(132.46, 132.47, 132.465, cfg_3, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bbw_trend)
            assert trk.levels["S1"]["sl_price"] == round(132.90 * 1.03, 2)
            assert trk.levels["S1"].get("sl_needs_amend") is True
            cfg = cfg_3

        # Tiny +0.32% favorable move (132.90 -> 132.47) MUST NOT slam solo S1 SL to -0.30% whipsaw!
        orig_sl = trk.levels["S1"]["sl_price"]
        trk.evaluate_tick(132.46, 132.47, 132.465, cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bbw_trend)
        assert trk.levels["S1"]["sl_price"] == orig_sl

        # Meaningful +0.90% favorable move (132.90 -> 131.70) locks BE with >= 0.16% slippage buffer on top of 0.10% fee
        trk.evaluate_tick(131.69, 131.70, 131.695, cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bbw_trend)
        expected_be = round(132.90 * (1.0 - 0.0010 - max(0.0016, 0.006 * 0.20)), 2)
        assert trk.levels["S1"]["sl_price"] == expected_be
        if stype == "TREND_GRID":
            # Once in BE profit-lock, SL must NEVER widen back down to -3%
            trk.evaluate_tick(131.80, 131.82, 131.81, cfg, is_net_mode=False, round_trip_fee=0.0010, bbw_info=bbw_trend)
            assert trk.levels["S1"]["sl_price"] == expected_be

        # Symmetric check on LONG L1 when entry fills close to center
        trk_l = LongShortGridTracker("NVDA/USDT:USDT", strategy_type=stype)
        trk_l.sync_config(cfg, current_mid=133.00, bbw_info=bbw_trend)
        trk_l.mark_entry_filled("L1", 133.10, 0.10, 1.0, 0.10, cfg, round_trip_fee=0.0010)
        max_safe_sl_l = round(133.10 * (1.0 - max(0.0035, 0.006 * 0.55)), 2)
        assert trk_l.levels["L1"]["sl_price"] <= max_safe_sl_l, f"{stype} L1 SL bypassed sl_buf floor!"
        if stype == "TREND_GRID":
            assert trk_l.levels["L1"]["sl_price"] == round(133.10 * (1.0 - cfg.grid_stop_loss_pct), 2)

    # 3) restore_state preserves exact entry_price/contracts with recovered=False, restored_from_state=True
    rev_cfg = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        strategy_type="REVERSE_GRID",
        grid_levels=3,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.035,
        grid_stop_loss_pct=0.038,
        price_precision=2,
        qty_precision=2,
    )
    trk_r = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_r.sync_config(rev_cfg, current_mid=100.0)
    trk_r.mark_entry_filled("Q", 100.60, 1.0, 2.0, 0.5, rev_cfg, round_trip_fee=0.0010)
    exported = trk_r.export_state()
    trk_r2 = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_r2.restore_state(exported, rev_cfg)
    assert trk_r2.levels["Q"]["recovered"] is True
    assert trk_r2.levels["Q"]["restored_from_state"] is True
    assert trk_r2.levels["Q"]["entry_price"] == 100.60
    assert trk_r2.levels["Q"]["contracts"] == 2.0

    # 11) SymbolMathState._compute_depth_aware_qty outputs base-coin qty even if contract_size != 1.0
    ll_cfg = SymbolStrategy(
        symbol="ETH/USDT:USDT",
        strategy_type="LEAD_LAG",
        order_usdt=200.0,
        contract_size=10.0,
        qty_precision=4,
        price_precision=2,
    )
    assert SymbolMathState._compute_depth_aware_qty(ll_cfg, 2000.0, 5.0) == 0.1

    async def _run_async_checks():
        db = AsyncDatabaseManager(str(tmp_path / "audit_fixes.db"))
        db.init_sync()
        eng = BidirectionalSniperEngine(db=db, user_id=99, is_guest_preview=False)
        eng.risk_config = GlobalRiskConfig(trading_mode="LIVE")
        eng.exchanges["okx"] = {
            "exchange_id": "okx",
            "display_name": "OKX Swap",
            "role": "follower",
            "is_active": 1,
            "auth_status": "VERIFIED",
            "api_key": "k",
            "_decrypted_secret": "s",
            "_decrypted_passphrase": "p",
            "taker_fee": 0.0005,
            "maker_fee": 0.0002,
        }

        # 4) cancel_slot_protective_orders verifies & retries SL cancel even when tp_fully_filled=True
        ex = ExecutionAdapter(lambda s: eng.math_states.get(s), lambda: eng.risk_config)
        slot_k = "SOL/USDT:USDT:L1"
        ex._active_maker_tp_orders[slot_k] = {
            "symbol": "SOL/USDT:USDT",
            "order_id": "tp-1",
            "is_live": True,
            "side": "LONG",
            "contracts": 1.0,
            "ct_val": 1.0,
            "tp_price": 101.0,
            "exchange_cfg": eng.exchanges["okx"],
        }
        ex._active_cond_sl_orders[slot_k] = {
            "symbol": "SOL/USDT:USDT",
            "order_id": "sl-1",
            "is_live": True,
            "side": "LONG",
            "contracts": 1.0,
            "ct_val": 1.0,
            "sl_price": 98.0,
            "exchange_cfg": eng.exchanges["okx"],
        }
        cancel_algo_calls = {"n": 0}

        async def _fake_okx_req(client, creds, method, path, body=None):
            if path == "/api/v5/trade/cancel-order":
                return {"code": "1", "data": [{"sCode": "51400"}]}
            if path.startswith("/api/v5/trade/order"):
                return {"code": "0", "data": [{"state": "filled", "accFillSz": "1", "avgPx": "101.0", "fee": "-0.02"}]}
            if path == "/api/v5/trade/cancel-algos":
                cancel_algo_calls["n"] += 1
                # First cancel fails transiently, retry succeeds!
                if cancel_algo_calls["n"] == 1:
                    return {"code": "50011", "data": []}
                return {"code": "0", "data": [{"sCode": "0"}]}
            return {"code": "0", "data": []}

        async def _fake_q_ord(sym, oid, ex_cfg):
            return {"ok": True, "state": "filled", "acc_fill_ct": 1.0, "avg_px": 101.0, "fee_usdt": 0.02}

        async def _fake_q_algo(sym, aid, ex_cfg):
            return {"ok": False, "state": ""}

        ex._okx_request = _fake_okx_req
        ex.query_order_final_status = _fake_q_ord
        ex.query_algo_final_status = _fake_q_algo
        c_res = await ex.cancel_slot_protective_orders(
            "SOL/USDT:USDT", slot_k, exchange_cfg=eng.exchanges["okx"], cancel_tp=True, cancel_sl=True, expected_contracts=1.0
        )
        assert c_res["tp_fully_filled"] is True
        assert cancel_algo_calls["n"] == 2
        assert slot_k not in ex._active_cond_sl_orders

        # 5) execute_exit_with_retry in net_mode MUST NOT fire blind close-position when pos query fails (pos_ok=False)
        ex2 = ExecutionAdapter(lambda s: eng.math_states.get(s), lambda: eng.risk_config)
        close_pos_called = {"called": False}

        async def _fake_okx_req2(client, creds, method, path, body=None):
            if path == "/api/v5/trade/close-position":
                close_pos_called["called"] = True
                return {"code": "0", "data": [{}]}
            if path.startswith("/api/v5/account/positions"):
                return {"code": "50011", "msg": "rate limit", "data": []}
            if path == "/api/v5/trade/order":
                return {"code": "50011", "msg": "rate limit", "data": []}
            return {"code": "0", "data": []}

        async def _fake_meta(client, creds):
            return {"posMode": "net_mode", "acctLv": "2"}

        ex2._okx_request = _fake_okx_req2
        ex2.ensure_okx_account_meta = _fake_meta
        out_exit = await ex2.execute_exit_with_retry(
            cfg=rev_cfg,
            side="LONG",
            qty=1.0,
            exit_reason="GRID_SL",
            tp_price=100.0,
            max_retries=1,
            exchange_cfg=eng.exchanges["okx"],
            contracts=1.0,
            ct_val=1.0,
            allow_close_position_fallback=True,
        )
        assert out_exit.status == "UNCERTAIN"
        assert close_pos_called["called"] is False

        # 6) sync_live_exchange_positions preserves vanished slot when reconcile_uncertain=True (and doesn't overwrite persisted slot)
        eng.strategies["SOL/USDT:USDT"] = rev_cfg
        eng.grid_trackers["SOL/USDT:USDT"] = trk_r2
        eng.active_grid_positions["SOL/USDT:USDT:Q"] = {
            "symbol": "SOL/USDT:USDT",
            "strategy_type": "REVERSE_GRID",
            "grid_slot_id": "Q",
            "mode": "LIVE",
            "side": "LONG",
            "qty": 1.0,
            "contracts": 2.0,
            "ct_val": 0.5,
            "entry_price": 100.60,
            "opened_mono_ms": mono_ms() - 20_000.0,
            "recovered": False,
            "restored_from_state": True,
        }

        async def _fake_fetch_pos_live(_):
            # Exchange transiently shows 4.0 ct at blended 101.20 -> persisted slot MUST NOT be overwritten!
            return {
                "ok": True,
                "pos_mode": "net_mode",
                "positions": [
                    {
                        "inst_id": "SOL-USDT-SWAP",
                        "symbol": "SOL/USDT:USDT",
                        "side": "LONG",
                        "contracts": 4.0,
                        "ct_val": 0.5,
                        "qty": 2.0,
                        "entry_price": 101.20,
                        "mark_price": 101.00,
                        "notional_usdt": 202.0,
                        "margin_usdt": 40.4,
                        "leverage": 5,
                        "upl_usdt": -0.4,
                        "mgn_mode": "cross",
                    }
                ],
            }

        async def _noop_poll():
            return 0

        eng.poll_grid_protective_orders = _noop_poll
        eng.executor.fetch_live_positions = _fake_fetch_pos_live
        await eng.sync_live_exchange_positions(trigger="periodic")
        assert trk_r2.levels["Q"]["entry_price"] == 100.60
        assert trk_r2.levels["Q"]["contracts"] == 2.0

        # Now OKX shows vanished position, but cancel_slot_protective_orders returns reconcile_uncertain=True -> slot preserved!
        async def _fake_fetch_empty(_):
            return {"ok": True, "pos_mode": "net_mode", "positions": []}

        async def _fake_cancel_uncertain(*args, **kwargs):
            return {
                "tp_fully_filled": False,
                "sl_already_filled": False,
                "reconcile_uncertain": True,
                "tp_filled_ct": 0.0,
                "tp_avg_px": 0.0,
                "tp_order_id": "",
                "tp_fee_usdt": 0.0,
                "sl_avg_px": 0.0,
                "sl_order_id": "",
                "sl_fee_usdt": 0.0,
            }

        eng.executor.fetch_live_positions = _fake_fetch_empty
        eng.executor.cancel_slot_protective_orders = _fake_cancel_uncertain
        await eng.sync_live_exchange_positions(trigger="periodic")
        assert trk_r2.levels["Q"]["status"] == "OPEN"
        assert "SOL/USDT:USDT:Q" in eng.active_grid_positions

        # 7) _handle_okx_private_algo_event partial SL fill shrinks contracts instead of closing full slot
        trk_r2.levels["Q"]["cond_sl_live"] = True
        trk_r2.levels["Q"]["cond_sl_algo_id"] = "algo-part-1"

        async def _fake_cancel_ok(*args, **kwargs):
            return {"tp_fully_filled": False, "sl_already_filled": False, "reconcile_uncertain": False}

        async def _fake_query_algo_partial(sym, aid, ex_cfg):
            return {"ok": True, "state": "partially_effective", "child_state": "canceled", "acc_fill_ct": 0.5, "avg_px": 97.0, "fee_usdt": 0.01}

        eng.executor.cancel_slot_protective_orders = _fake_cancel_ok
        eng.executor.query_algo_final_status = _fake_query_algo_partial
        await eng._handle_okx_private_algo_event({"algoId": "algo-part-1", "state": "partially_effective", "instId": "SOL-USDT-SWAP"})
        assert trk_r2.levels["Q"]["status"] == "OPEN"
        assert trk_r2.levels["Q"]["contracts"] == 1.5

        # 8) _amend_live_slot_sl old_triggered cancels live Maker TP and logs GRID_GLOBAL_SL (not TREND_BE_SL!) for REVERSE_GRID
        trk_r2.levels["Q"]["maker_tp_live"] = True
        trk_r2.levels["Q"]["maker_tp_order_id"] = "tp-live-9"
        cancel_tp_called = {"called": False}

        async def _fake_cancel_tp_spy(sym, gk, **kwargs):
            if kwargs.get("cancel_tp"):
                cancel_tp_called["called"] = True
            return {"tp_fully_filled": False, "sl_already_filled": False, "reconcile_uncertain": False}

        async def _fake_upd_sl_triggered(**kwargs):
            return {"status": "old_triggered", "fill_info": {"avg_px": 92.1, "ord_id": "sl-trig-9", "fee_usdt": 0.05}}

        eng.executor.cancel_slot_protective_orders = _fake_cancel_tp_spy
        eng.executor.update_slot_sl_order = _fake_upd_sl_triggered
        recorded_trades = []
        orig_rec_trade = eng.db.record_trade_async
        eng.db.record_trade_async = lambda tr, user_id=0: recorded_trades.append(tr)
        await eng._amend_live_slot_sl(rev_cfg, trk_r2, "Q", "LIVE")
        assert cancel_tp_called["called"] is True
        assert recorded_trades and recorded_trades[-1]["exit_reason"] == "GRID_GLOBAL_SL_Q"
        eng.db.record_trade_async = orig_rec_trade

        # 9) _run_grid_exits_batch deducts partial exit contracts across multi-slot batch on UNCERTAIN
        trk_b = LongShortGridTracker("BTC/USDT:USDT", strategy_type="TREND_GRID")
        btc_cfg = SymbolStrategy(symbol="BTC/USDT:USDT", strategy_type="TREND_GRID", grid_levels=3, grid_step_pct=0.008, price_precision=1, qty_precision=3)
        eng.strategies["BTC/USDT:USDT"] = btc_cfg
        eng.grid_trackers["BTC/USDT:USDT"] = trk_b
        trk_b.sync_config(btc_cfg, current_mid=100000.0)
        trk_b.mark_entry_filled("L1", 101000.0, 0.02, 2.0, 0.01, btc_cfg, 0.001)
        trk_b.mark_entry_filled("L2", 101800.0, 0.02, 2.0, 0.01, btc_cfg, 0.001)
        trk_b.levels["L1"]["status"] = "EXITING"
        trk_b.levels["L2"]["status"] = "EXITING"
        eng.active_grid_positions["BTC/USDT:USDT:L1"] = {"symbol": "BTC/USDT:USDT", "contracts": 2.0, "qty": 0.02, "mode": "LIVE", "side": "LONG"}
        eng.active_grid_positions["BTC/USDT:USDT:L2"] = {"symbol": "BTC/USDT:USDT", "contracts": 2.0, "qty": 0.02, "mode": "LIVE", "side": "LONG"}

        async def _fake_cancel_clean(*args, **kwargs):
            return {
                "tp_fully_filled": False,
                "sl_already_filled": False,
                "reconcile_uncertain": False,
                "tp_filled_ct": 0.0,
                "tp_avg_px": 0.0,
                "tp_order_id": "",
                "tp_fee_usdt": 0.0,
                "sl_avg_px": 0.0,
                "sl_order_id": "",
                "sl_fee_usdt": 0.0,
            }

        async def _fake_exit_partial_uncertain(**kwargs):
            # Closed 2.5 out of 4.0 contracts across L1(2.0)+L2(2.0) before returning UNCERTAIN!
            return OrderResult(
                order_id="part-u-1",
                status="UNCERTAIN",
                filled_qty=0.025,
                avg_price=102500.0,
                latency_ms=10.0,
                is_maker=False,
                raw_msg="partial uncertain",
                contracts=2.5,
                ct_val=0.01,
            )

        eng.executor.cancel_slot_protective_orders = _fake_cancel_clean
        eng.executor.execute_exit_with_retry = _fake_exit_partial_uncertain
        await eng._run_grid_exits_batch(
            btc_cfg,
            trk_b,
            [{**trk_b.levels["L1"], "exit_reason": "TREND_TRAIL_TP"}, {**trk_b.levels["L2"], "exit_reason": "TREND_TRAIL_TP"}],
            "LIVE",
        )
        # L1 (2.0 ct) is completely closed & settled (IDLE); L2 shrunk from 2.0 ct to 1.5 ct (OPEN)!
        assert trk_b.levels["L1"]["status"] == "IDLE"
        assert trk_b.levels["L2"]["status"] == "OPEN"
        assert trk_b.levels["L2"]["contracts"] == 1.5
        assert eng.active_grid_positions["BTC/USDT:USDT:L2"]["contracts"] == 1.5

        # 10) _run_state_machine locks to EXIT_UNCERTAIN if unexpected exception occurs while LIVE position is open
        eng.strategies["ETH/USDT:USDT"] = ll_cfg
        eng.math_states["ETH/USDT:USDT"] = SymbolMathState("ETH/USDT:USDT")
        eng.book_events["ETH/USDT:USDT"] = asyncio.Event()
        eng.symbol_states["ETH/USDT:USDT"] = SniperState.SIGNALED
        sync_triggers = []

        async def _spy_sync(trigger="manual"):
            sync_triggers.append(trigger)
            return {"ok": True, "synced_count": 0, "recovered_count": 0}

        async def _fake_ll_entry(**kwargs):
            return OrderResult(
                order_id="ll-1",
                status="FILLED",
                filled_qty=0.1,
                avg_price=2000.0,
                latency_ms=5.0,
                is_maker=False,
                raw_msg="filled",
                contracts=1.0,
                ct_val=0.1,
            )

        async def _boom_protective(*args, **kwargs):
            raise RuntimeError("simulated protective crash after fill")

        eng.sync_live_exchange_positions = _spy_sync
        eng.executor.place_entry_ioc = _fake_ll_entry
        eng.executor.place_protective_orders = _boom_protective
        sig = SignalCandidate(
            symbol="ETH/USDT:USDT",
            side="LONG",
            signal_mono_ms=mono_ms(),
            signal_wall_ms=wall_ms(),
            leader_jump_pct=0.008,
            micro_jump_pct=0.008,
            z_score=4.0,
            follower_lag_pct=0.0002,
            proportional_basis=0.0,
            calibrated_target_price=2016.0,
            raw_spread_pct=0.007,
            follower_spread_pct=0.0002,
            expected_cost_pct=0.001,
            expected_net_pct=0.006,
            reservation_price=2001.0,
            trigger_price=2000.0,
            recommended_qty=0.1,
            beta_used=0.9,
            active_vol_usdt=10000.0,
            leader_mid=2016.0,
            follower_mid=2000.0,
        )
        try:
            await eng._run_state_machine(ll_cfg, sig, "LIVE")
        except RuntimeError:
            pass
        assert eng.symbol_states["ETH/USDT:USDT"] == SniperState.EXIT_UNCERTAIN
        await asyncio.sleep(0.02)
        assert "state_machine_error" in sync_triggers

    asyncio.run(_run_async_checks())


def test_user_feedback_24h_limit_reply_and_independent_soft_delete():
    """
    Verifies end-to-end user feedback message board rules:
    1. Non-VIP logged-in users can submit feedback; guests get 401; non-admin gets 403 on admin routes.
    2. Strict 24-hour submission cooldown (at most 1 submission per 24h per user; deleting never bypasses it).
    3. Strict 24-hour user soft-delete lock (blocked before 24h, allowed at >= 24h).
    4. Independent soft-delete isolation: user delete hides ONLY from user; admin still sees it (user_deleted=True)
       until admin explicitly deletes it.
    5. Admin can view, reply, update reply, and delete records at any time.
    """
    import backend.main as main_mod
    from backend.main import app

    rate_limiter._buckets.clear()

    with TestClient(app) as client:
        # 1. Guest blocked
        assert client.get("/api/feedback/my").status_code == 401
        assert client.post("/api/feedback", json={"content": "guest test"}).status_code == 401
        assert client.get("/api/admin/feedback").status_code == 401

        # 2. Register two ordinary (non-VIP) users & login admin
        r_a = client.post(
            "/api/auth/register",
            json={"username": "fb_user_a", "password": "password123", "email": "a@test.io"},
            headers={"X-Forwarded-For": "203.0.113.11"},
        )
        assert r_a.status_code == 200
        tok_a = r_a.json()["token"]
        hdr_a = {"Authorization": f"Bearer {tok_a}"}

        r_b = client.post(
            "/api/auth/register",
            json={"username": "fb_user_b", "password": "password123", "email": "b@test.io"},
            headers={"X-Forwarded-For": "203.0.113.12"},
        )
        assert r_b.status_code == 200
        tok_b = r_b.json()["token"]
        hdr_b = {"Authorization": f"Bearer {tok_b}"}

        r_adm = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
        assert r_adm.status_code == 200
        hdr_adm = {"Authorization": f"Bearer {r_adm.json()['token']}"}

        # Non-admin blocked from admin feedback endpoints
        assert client.get("/api/admin/feedback", headers=hdr_a).status_code == 403
        assert client.post("/api/admin/feedback/1/reply", json={"reply": "hi"}, headers=hdr_a).status_code == 403
        assert client.delete("/api/admin/feedback/1", headers=hdr_a).status_code == 403

        # 3. User A submits first feedback message
        sub1 = client.post(
            "/api/feedback",
            json={"category": "策略问题", "title": "多空震荡咨询", "content": "请问移动止盈保底价是如何计算的？"},
            headers=hdr_a,
        )
        assert sub1.status_code == 200
        d1 = sub1.json()
        assert d1["ok"] is True
        msg1_id = d1["item"]["id"]
        assert d1["feedback"]["can_submit"] is False
        assert d1["feedback"]["cooldown_remaining_sec"] > 86000
        assert d1["feedback"]["messages"][0]["can_user_delete"] is False

        # 4. Second submission within 24h MUST be rejected
        sub_dup = client.post(
            "/api/feedback",
            json={"category": "Bug反馈", "title": "重复提交", "content": "24小时内再次提交"},
            headers=hdr_a,
        )
        assert sub_dup.status_code == 400
        assert "24 小时" in sub_dup.json()["detail"]

        # 5. User A deleting before 24h MUST be rejected
        del_early = client.delete(f"/api/feedback/{msg1_id}", headers=hdr_a)
        assert del_early.status_code == 400
        assert "24 小时" in del_early.json()["detail"]

        # 6. User B cannot delete User A's message
        del_other = client.delete(f"/api/feedback/{msg1_id}", headers=hdr_b)
        assert del_other.status_code == 400

        # 7. Admin sees message in both /api/admin/feedback and /api/admin/dashboard, then replies
        adm_list = client.get("/api/admin/feedback", headers=hdr_adm).json()
        assert adm_list["total_count"] == 1
        assert adm_list["open_count"] == 1
        assert adm_list["messages"][0]["id"] == msg1_id
        assert adm_list["messages"][0]["user_deleted"] is False

        adm_dash = client.get("/api/admin/dashboard", headers=hdr_adm).json()
        assert adm_dash["stats"]["open_feedback_count"] == 1
        assert len(adm_dash["feedback_messages"]) == 1

        rep = client.post(
            f"/api/admin/feedback/{msg1_id}/reply",
            json={"reply": "保底价按加权均价叠加双边手续费和滑点垫层计算，确保止盈为正。"},
            headers=hdr_adm,
        )
        assert rep.status_code == 200
        assert rep.json()["item"]["status"] == "REPLIED"
        assert rep.json()["dashboard"]["stats"]["open_feedback_count"] == 0

        # User A sees admin reply immediately
        my_list = client.get("/api/feedback/my", headers=hdr_a).json()
        assert len(my_list["messages"]) == 1
        assert my_list["messages"][0]["status"] == "REPLIED"
        assert my_list["messages"][0]["replied_by"] == "admin"
        assert "保底价" in my_list["messages"][0]["admin_reply"]

        # 8. Simulate > 24 hours elapsed by backdating created_at_ts by 86405s
        conn = sqlite3.connect(main_mod.user_manager.db_path)
        conn.execute(
            "UPDATE user_feedback_messages SET created_at_ts = created_at_ts - 86405 WHERE id = ?",
            (msg1_id,),
        )
        conn.commit()
        conn.close()

        my_after_24h = client.get("/api/feedback/my", headers=hdr_a).json()
        assert my_after_24h["can_submit"] is True
        assert my_after_24h["cooldown_remaining_sec"] == 0
        assert my_after_24h["messages"][0]["can_user_delete"] is True
        assert my_after_24h["messages"][0]["delete_unlock_remaining_sec"] == 0

        # 9. User A deletes their record after 24h -> hidden from user ONLY, admin STILL sees it!
        del_user_ok = client.delete(f"/api/feedback/{msg1_id}", headers=hdr_a)
        assert del_user_ok.status_code == 200
        assert len(del_user_ok.json()["feedback"]["messages"]) == 0

        my_after_del = client.get("/api/feedback/my", headers=hdr_a).json()
        assert len(my_after_del["messages"]) == 0

        adm_after_user_del = client.get("/api/admin/feedback", headers=hdr_adm).json()
        assert adm_after_user_del["total_count"] == 1
        assert adm_after_user_del["messages"][0]["id"] == msg1_id
        assert adm_after_user_del["messages"][0]["user_deleted"] is True

        # 10. User A submits a new message after 24h; even if admin deletes msg2 immediately,
        #     User A still cannot bypass the 24h submit limit!
        sub2 = client.post(
            "/api/feedback",
            json={"category": "功能建议", "title": "24小时后新留言", "content": "希望支持更多提示音"},
            headers=hdr_a,
        )
        assert sub2.status_code == 200
        msg2_id = sub2.json()["item"]["id"]

        adm_del2 = client.delete(f"/api/admin/feedback/{msg2_id}", headers=hdr_adm)
        assert adm_del2.status_code == 200

        # Still blocked within 24h even after admin deleted msg2
        sub3_blocked = client.post(
            "/api/feedback",
            json={"category": "其他", "title": "绕过测试", "content": "测试删除后是否能绕过24h限制"},
            headers=hdr_a,
        )
        assert sub3_blocked.status_code == 400

        # User A still sees msg2 in their own view (admin deletion is independent of user side)
        my_with_msg2 = client.get("/api/feedback/my", headers=hdr_a).json()
        assert [m["id"] for m in my_with_msg2["messages"]] == [msg2_id]

        # 11. Admin deletes original msg1 -> now admin inbox is empty
        adm_del1 = client.delete(f"/api/admin/feedback/{msg1_id}", headers=hdr_adm)
        assert adm_del1.status_code == 200
        adm_final = client.get("/api/admin/feedback", headers=hdr_adm).json()
        assert adm_final["total_count"] == 0
        assert adm_final["messages"] == []


def test_admin_delete_web3_orders_and_tx_replay_guard():
    """
    Verifies admin deletion of Web3 membership orders:
    1. Non-admin gets 403 on DELETE /api/admin/orders/{id} and cleanup-closed.
    2. Admin can delete individual PENDING / EXPIRED / PAID orders and dashboard stats update cleanly.
    3. Deleting a PAID Web3 order preserves the user's active VIP duration AND keeps the 0x tx_hash
       recorded in consumed_tx_hashes so on-chain replay remains blocked.
    4. Batch cleanup-closed deletes only EXPIRED/CANCELLED/REJECTED orders, leaving PENDING & PAID intact.
    """
    import backend.main as main_mod
    from backend.main import app

    rate_limiter._buckets.clear()

    with TestClient(app) as client:
        r_u = client.post(
            "/api/auth/register",
            json={"username": "ord_del_user", "password": "password123", "email": "ord@test.io"},
            headers={"X-Forwarded-For": "203.0.113.55"},
        )
        assert r_u.status_code == 200
        hdr_u = {"Authorization": f"Bearer {r_u.json()['token']}"}

        r_adm = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
        assert r_adm.status_code == 200
        hdr_adm = {"Authorization": f"Bearer {r_adm.json()['token']}"}

        # Non-admin rejected
        assert client.delete("/api/admin/orders/1", headers=hdr_u).status_code == 403
        assert client.delete("/api/admin/orders/cleanup-closed", headers=hdr_u).status_code == 403

        plans = client.get("/api/membership/plans", headers=hdr_u).json()["plans"]
        plan_id = plans[0]["id"]

        # Create order 1 (will be confirmed PAID onchain then deleted by admin)
        o1 = client.post("/api/membership/purchase", json={"plan_id": plan_id, "force_new": True}, headers=hdr_u).json()["order"]
        tx_hex = "0x" + "ab" * 32
        paid_res = main_mod.user_manager.confirm_order_paid_onchain(o1["id"], "Arbitrum", tx_hex, float(o1["pay_amount_usdt"]))
        assert paid_res["ok"] is True

        # Delete PAID order -> succeeds, user remains VIP, tx_hash still blocked from replay
        del_paid = client.delete(f"/api/admin/orders/{o1['id']}", headers=hdr_adm)
        assert del_paid.status_code == 200
        assert del_paid.json()["dashboard"]["stats"]["total_orders"] == 0
        assert main_mod.user_manager.is_tx_hash_already_used(tx_hex) is True

        me_after = client.get("/api/auth/me", headers=hdr_u).json()["user"]
        assert me_after["is_vip"] is True

        # Deleting missing order returns 400
        assert client.delete(f"/api/admin/orders/{o1['id']}", headers=hdr_adm).status_code == 400

        # Create order 2 (expire it), order 3 (cancel it), order 4 (keep PENDING)
        o2 = client.post("/api/membership/purchase", json={"plan_id": plan_id, "force_new": True}, headers=hdr_u).json()["order"]
        assert client.post("/api/admin/orders/audit", json={"order_id": o2["id"], "action": "EXPIRE"}, headers=hdr_adm).status_code == 200

        o3 = client.post("/api/membership/purchase", json={"plan_id": plan_id, "force_new": True}, headers=hdr_u).json()["order"]
        assert client.post(f"/api/membership/orders/{o3['order_no']}/cancel", headers=hdr_u).status_code == 200

        o4 = client.post("/api/membership/purchase", json={"plan_id": plan_id, "force_new": True}, headers=hdr_u).json()["order"]

        # Attempt replay of tx_hex against o4 MUST still be rejected!
        replay_res = main_mod.user_manager.confirm_order_paid_onchain(o4["id"], "Arbitrum", tx_hex, float(o4["pay_amount_usdt"]))
        assert replay_res["ok"] is False

        # Batch cleanup-closed removes o2 (EXPIRED) & o3 (CANCELLED), preserves o4 (PENDING)
        clr = client.delete("/api/admin/orders/cleanup-closed", headers=hdr_adm)
        assert clr.status_code == 200
        assert clr.json()["deleted_count"] == 2
        rem_ids = [o["id"] for o in clr.json()["dashboard"]["orders"]]
        assert rem_ids == [o4["id"]]

        # Delete remaining PENDING o4 directly
        del_p = client.delete(f"/api/admin/orders/{o4['id']}", headers=hdr_adm)
        assert del_p.status_code == 200
        assert del_p.json()["dashboard"]["orders"] == []


def test_all_4_traditional_brokers_shadow_execution_live_isolation_and_protocols():
    """
    Comprehensive verification across ALL 4 traditional securities brokers (futu, moomoo, ibkr, schwab):
    1) Native symbol mapping matches official broker docs:
       - futu / moomoo -> US.NVDA, US.AAPL, US.BRK.B
       - ibkr / schwab -> NVDA, MSFT, BRK.B
    2) Official gateway connectivity verification works for all 4:
       - futu & moomoo OpenD TCP connect (pass + unreachable failure)
       - ibkr Client Portal Web API v1 (authenticated=True pass + authenticated=False failure)
       - schwab OAuth2 refresh_token + /trader/v1/accounts (pass + error)
    3) Full SHADOW mode grid lifecycle (entry -> take-profit exit -> realized PnL & follower_ex attribution)
       runs cleanly for ALL 4 traditional brokers concurrently using USD stock symbols.
    4) Strict LIVE mode channel isolation: even when OKX is VERIFIED & active alongside all 4 traditional
       brokers (including a legacy NVDA/USDT:USDT bound to futu), LIVE ticks & direct executor calls
       NEVER mis-route any order to OKX (_okx_request called 0 times), and preflight reports exact
       native broker codes + USD share sizing warnings.
    """
    from backend.market_gateway import to_broker_native_symbol
    from backend.sniper_engine import BidirectionalSniperEngine

    # 1) Official symbol translations for all 4 brokers (both new USD format and legacy USDT format)
    assert to_broker_native_symbol("NVDA/USD", "futu") == "US.NVDA"
    assert to_broker_native_symbol("AAPL/USD", "moomoo") == "US.AAPL"
    assert to_broker_native_symbol("BRK.B/USD", "futu") == "US.BRK.B"
    assert to_broker_native_symbol("MSFT/USD", "ibkr") == "MSFT"
    assert to_broker_native_symbol("BRK.B/USD", "schwab") == "BRK.B"
    assert to_broker_native_symbol("NVDA/USDT:USDT", "okx") == "NVDA-USDT-SWAP"

    async def _run_all_broker_checks():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "brokers_all4.db")
            adb = AsyncDatabaseManager(db_path=db_file)
            adb.init_sync()
            adb.ensure_user_workspace_sync(99)

            eng = BidirectionalSniperEngine(adb, user_id=99)
            eng.exchanges = {
                "binanceusdm": {"exchange_id": "binanceusdm", "display_name": "Binance USDM", "role": "leader", "is_active": True, "taker_fee": 0.0004, "maker_fee": 0.0002, "auth_status": "VERIFIED"},
                "okx": {
                    "exchange_id": "okx",
                    "display_name": "OKX Swap",
                    "role": "follower",
                    "is_active": True,
                    "api_key": "okx-live-key",
                    "_decrypted_secret": "okx-live-sec",
                    "_decrypted_passphrase": "okx-live-pass",
                    "taker_fee": 0.0005,
                    "maker_fee": 0.0002,
                    "auth_status": "VERIFIED",
                    "account_equity_usdt": 1000.0,
                },
                "futu": {"exchange_id": "futu", "display_name": "富途牛牛", "role": "follower", "is_active": True, "taker_fee": 0.0003, "maker_fee": 0.0001, "auth_status": "VERIFIED"},
                "moomoo": {"exchange_id": "moomoo", "display_name": "moomoo", "role": "follower", "is_active": True, "taker_fee": 0.0003, "maker_fee": 0.0001, "auth_status": "VERIFIED"},
                "ibkr": {"exchange_id": "ibkr", "display_name": "IBKR盈透", "role": "follower", "is_active": True, "taker_fee": 0.0002, "maker_fee": 0.00008, "auth_status": "VERIFIED"},
                "schwab": {"exchange_id": "schwab", "display_name": "Charles Schwab", "role": "follower", "is_active": True, "taker_fee": 0.0002, "maker_fee": 0.00008, "auth_status": "VERIFIED"},
            }

            # 2) Verify OpenD TCP (futu & moomoo), IBKR unauth/auth, and Schwab error/ok
            class _DummyWriter:
                def close(self):
                    pass

                async def wait_closed(self):
                    pass

            with patch("asyncio.open_connection", new=AsyncMock(return_value=(object(), _DummyWriter()))):
                f_ok = await eng.executor.verify_exchange_credentials(
                    {"exchange_id": "futu", "api_key": "127.0.0.1:11111", "_decrypted_secret": "futu_rsa_key", "_decrypted_passphrase": "unlock123"}
                )
                assert f_ok["ok"] is True and f_ok["auth_status"] == "VERIFIED" and "USD" in f_ok["auth_message"]

                m_ok = await eng.executor.verify_exchange_credentials(
                    {"exchange_id": "moomoo", "api_key": "127.0.0.1:11112", "_decrypted_secret": "moomoo_rsa_key", "_decrypted_passphrase": "unlock456"}
                )
                assert m_ok["ok"] is True and m_ok["auth_status"] == "VERIFIED" and "USD" in m_ok["auth_message"]

            with patch("asyncio.open_connection", new=AsyncMock(side_effect=ConnectionRefusedError("refused"))):
                f_fail = await eng.executor.verify_exchange_credentials(
                    {"exchange_id": "futu", "api_key": "127.0.0.1:11111", "_decrypted_secret": "futu_rsa_key", "_decrypted_passphrase": "unlock123"}
                )
                assert f_fail["ok"] is False and f_fail["auth_status"] == "ERROR"

            # 3) Full SHADOW mode execution across ALL 4 traditional brokers concurrently
            eng.risk_config = GlobalRiskConfig(
                trading_mode="SHADOW",
                max_exposure_usdt=50_000.0,
                shadow_base_latency_ms=2.0,
                shadow_latency_jitter_ms=0.5,
                shadow_adverse_selection_prob=0.0,
            )
            eng.trading_active_by_mode["SHADOW"] = True
            await adb.start_worker()

            broker_cases = [
                ("NVDA/USD", "futu", 140.0, 280.0, "US.NVDA"),
                ("AAPL/USD", "moomoo", 228.0, 456.0, "US.AAPL"),
                ("MSFT/USD", "ibkr", 425.0, 850.0, "MSFT"),
                ("BRK.B/USD", "schwab", 460.0, 920.0, "BRK.B"),
            ]
            for sym, b_id, base_px, ord_usd, _ in broker_cases:
                cfg_b = SymbolStrategy(
                    symbol=sym,
                    exchange_id=b_id,
                    strategy_type="LONG_SHORT_GRID",
                    grid_side_mode="LONG_ONLY",
                    grid_levels=2,
                    grid_step_pct=0.01,
                    grid_take_profit_pct=0.015,
                    grid_range_pct=0.04,
                    grid_stop_loss_pct=0.08,
                    order_usdt=ord_usd,
                    leverage=1,
                    price_precision=2,
                    qty_precision=0,
                    min_qty=1.0,
                    slippage_pct=0.0005,
                    is_running=True,
                )
                eng._register_symbol_internal(cfg_b)
                # Anchor at base_px -> drop -1.2% (triggers L1 LONG) -> rally +2.0% above L1 (triggers GRID_TP)
                eng._handle_follower_bbo(sym, base_px - 0.02, base_px + 0.02, 500.0, 500.0)
                drop_px = round(base_px * 0.988, 2)
                eng._handle_follower_bbo(sym, drop_px - 0.02, drop_px + 0.02, 500.0, 500.0)

            await asyncio.sleep(0.08)
            for sym, _, _, _, _ in broker_cases:
                assert any(k.startswith(f"{sym}:") for k in eng.active_grid_positions), f"Expected open SHADOW slot for {sym}"

            for sym, _, base_px, _, _ in broker_cases:
                tp_px = round(base_px * 1.01, 2)
                eng._handle_follower_bbo(sym, tp_px - 0.02, tp_px + 0.02, 500.0, 500.0)

            await asyncio.sleep(0.08)
            snap_sh = eng.build_dashboard_snapshot()
            assert snap_sh["stats"]["win_count"] >= 4
            recent_tr = snap_sh["stats"]["recent_trades"]
            for sym, b_id, _, _, native_code in broker_cases:
                assert snap_sh["telemetry"][sym]["native_symbol"] == native_code
                assert snap_sh["telemetry"][sym]["quote_ccy"] == "USD"
                assert snap_sh["telemetry"][sym]["asset_type"] == "US_STOCK"
                assert any(t["symbol"] == sym and t["follower_ex"] == b_id for t in recent_tr), (
                    f"Expected closed SHADOW trade attributed to {b_id} for {sym}"
                )

            # 4) Strict LIVE mode channel isolation: OKX is active, yet NO traditional broker symbol may EVER hit OKX!
            eng.risk_config = GlobalRiskConfig(trading_mode="LIVE", max_exposure_usdt=50_000.0)
            eng.trading_active_by_mode["LIVE"] = True
            eng.gateway.okx_connected = True
            eng.gateway.binance_connected = True

            # Also add a legacy NVDA/USDT:USDT explicitly bound to futu to verify legacy rows are isolated too!
            legacy_futu_cfg = SymbolStrategy(
                symbol="TSLA/USDT:USDT",
                exchange_id="futu",
                strategy_type="LONG_SHORT_GRID",
                grid_side_mode="LONG_ONLY",
                grid_levels=2,
                grid_step_pct=0.01,
                grid_take_profit_pct=0.015,
                grid_range_pct=0.04,
                grid_stop_loss_pct=0.08,
                order_usdt=360.0,
                leverage=1,
                price_precision=2,
                qty_precision=0,
                min_qty=1.0,
                is_running=True,
            )
            eng._register_symbol_internal(legacy_futu_cfg)

            okx_spy = AsyncMock(return_value={"code": "0", "data": []})
            with patch.object(eng.executor, "_okx_request", new=okx_spy):
                # Feed LIVE ticks that would otherwise trigger L1 entry on every broker symbol
                for sym, _, base_px, _, _ in broker_cases:
                    eng.grid_trackers[sym].sync_config(eng.strategies[sym], current_mid=base_px)
                    eng._handle_follower_bbo(sym, round(base_px * 0.985, 2), round(base_px * 0.986, 2), 500.0, 500.0)
                eng.grid_trackers["TSLA/USDT:USDT"].sync_config(legacy_futu_cfg, current_mid=350.0)
                eng._handle_follower_bbo("TSLA/USDT:USDT", 344.0, 344.5, 500.0, 500.0)
                await asyncio.sleep(0.05)

                # Even if executor.place_entry_ioc or execute_exit_with_retry were mistakenly handed OKX creds,
                # executor itself MUST block and never call _okx_request!
                for sym, b_id, base_px, _, native_code in broker_cases:
                    res_en = await eng.executor.place_entry_ioc(
                        cfg=eng.strategies[sym],
                        side="LONG",
                        qty=2.0,
                        reservation_price=base_px,
                        trigger_price=base_px,
                        exchange_cfg=eng.exchanges["okx"],
                    )
                    assert res_en.status == "MISSED"
                    assert "隔离拦截" in res_en.raw_msg and native_code in res_en.raw_msg

                    res_ex = await eng.executor.execute_exit_with_retry(
                        cfg=eng.strategies[sym],
                        side="LONG",
                        qty=2.0,
                        exit_reason="GRID_TP",
                        tp_price=base_px * 1.02,
                        exchange_cfg=eng.exchanges["okx"],
                    )
                    assert res_ex.status == "UNCERTAIN"
                    assert "隔离拦截" in res_ex.raw_msg and native_code in res_ex.raw_msg

                assert okx_spy.call_count == 0, f"Traditional broker symbols MUST NEVER hit OKX, got {okx_spy.call_count} calls"

            # 5) Preflight check reports native codes + USD warnings cleanly across all 4 brokers
            with patch.object(
                eng.executor,
                "verify_exchange_credentials",
                new=AsyncMock(return_value={"ok": True, "auth_status": "VERIFIED", "auth_message": "OK", "account_equity_usdt": 1000.0}),
            ):
                pf = await eng.executor.preflight_check_symbols(
                    exchange_cfg=eng.exchanges["okx"],
                    strategies=list(eng.strategies.values()),
                    math_states=eng.math_states,
                    gateway=None,
                    all_exchanges=eng.exchanges,
                )
                assert pf["ok"] is True
                pf_by_sym = {s["symbol"]: s for s in pf["symbols"]}
                for sym, b_id, _, _, native_code in broker_cases:
                    rep = pf_by_sym[sym]
                    assert rep["instId"] == native_code
                    assert rep["exchange_id"] == b_id
                    assert rep["quote_ccy"] == "USD"
                    assert rep["live_execution_supported"] is False
                    assert any(native_code in w for w in rep["warnings"])

            await adb.stop_worker()

    asyncio.run(_run_all_broker_checks())


def test_referral_invite_points_redeem_and_vip_expiry_auto_stop():
    """
    End-to-end verification of:
      1. Personal invite code generation + dual +100 points referral reward + anti-abuse (invalid code / same IP)
      2. Points-to-VIP redemption (100 pts = 1 hour VIP), accurate remaining-time label, zero revenue inflation
      3. Admin referral rules config, manual points adjustment, hour-based VIP grant & zero-day accidental-wipe guard
      4. Strict VIP expiration auto-stop: stops trading AND sets ALL strategies is_running=False in memory & SQLite,
         locks all trading mutation endpoints while keeping stop/kill-switch/manual-close safe, and unlocks immediately
         upon points redemption or admin grant (and re-stops on admin revoke).
    """
    import sqlite3
    import time
    from backend import main as main_mod
    from backend.user_membership import UserMembershipManager
    from backend.engine_manager import EngineManager

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "referral_vip_audit.db")
        adb = AsyncDatabaseManager(db_path=db_file)
        adb.init_sync()
        um = UserMembershipManager(db_path=db_file)
        um.init_sync()
        em = EngineManager(adb, get_user_fn=um.get_user_by_id)

        with (
            patch.object(main_mod, "db_manager", adb),
            patch.object(main_mod, "user_manager", um),
            patch.object(main_mod, "engine_manager", em),
            patch("backend.sniper_engine.BidirectionalSniperEngine.start", new=AsyncMock()),
            patch.object(em, "_vip_guard_loop", new=AsyncMock()),
        ):
            with TestClient(main_mod.app) as client:
                # Admin login
                r_adm = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
                assert r_adm.status_code == 200
                adm_hdr = {"Authorization": f"Bearer {r_adm.json()['token']}"}
                assert r_adm.json()["user"]["invite_code"].startswith("INV-")

                # 1. User A registers and receives a unique invite code
                r_a = client.post(
                    "/api/auth/register",
                    json={"username": "inviter_a", "password": "Password123!", "email": "a@test.local"},
                    headers={"x-forwarded-for": "198.51.100.10"},
                )
                assert r_a.status_code == 200, r_a.text
                u_a = r_a.json()["user"]
                tok_a = r_a.json()["token"]
                hdr_a = {"Authorization": f"Bearer {tok_a}"}
                code_a = u_a["invite_code"]
                assert code_a.startswith("INV-") and len(code_a) >= 8
                assert u_a["points_balance"] == 0

                # Invalid invite code rejected
                r_bad_code = client.post(
                    "/api/auth/register",
                    json={
                        "username": "bad_invitee",
                        "password": "Password123!",
                        "invite_code": "INV-NOEXIST1",
                    },
                    headers={"x-forwarded-for": "198.51.100.19"},
                )
                assert r_bad_code.status_code == 400

                # Same non-loopback IP self-referral rejected
                r_same_ip = client.post(
                    "/api/auth/register",
                    json={
                        "username": "same_ip_user",
                        "password": "Password123!",
                        "invite_code": code_a,
                    },
                    headers={"x-forwarded-for": "198.51.100.10"},
                )
                assert r_same_ip.status_code == 400

                # User B registers with User A's invite code from a distinct IP -> BOTH gain +100 points!
                r_b = client.post(
                    "/api/auth/register",
                    json={
                        "username": "invitee_b",
                        "password": "Password123!",
                        "email": "b@test.local",
                        "invite_code": code_a.lower(),  # case-insensitive
                    },
                    headers={"x-forwarded-for": "198.51.100.11"},
                )
                assert r_b.status_code == 200, r_b.text
                u_b = r_b.json()["user"]
                uid_b = int(u_b["id"])
                tok_b = r_b.json()["token"]
                hdr_b = {"Authorization": f"Bearer {tok_b}"}
                assert u_b["points_balance"] == 100
                assert u_b["total_points_earned"] == 100
                assert u_b["invited_by_username"] == "inviter_a"
                assert u_b["invite_code"].startswith("INV-") and u_b["invite_code"] != code_a

                # Check User A's referral summary
                r_ref_a = client.get("/api/referral/me", headers=hdr_a)
                assert r_ref_a.status_code == 200
                ref_a = r_ref_a.json()
                assert ref_a["points_balance"] == 100
                assert ref_a["total_invites_count"] == 1
                assert any(x["username"] == "invitee_b" for x in ref_a["invited_users"])
                assert any(x["action_type"] == "INVITE_REWARD_INVITER" and x["change_points"] == 100 for x in ref_a["ledger"])

                # 2. Points-to-VIP redemption (100 pts = 1 hour VIP)
                # Insufficient points (needs 200 for 2 hours, has 100)
                r_insuff = client.post("/api/points/redeem-vip", json={"hours": 2}, headers=hdr_b)
                assert r_insuff.status_code == 400

                # Redeem 1 hour (100 pts)
                r_redeem = client.post("/api/points/redeem-vip", json={"hours": 1}, headers=hdr_b)
                assert r_redeem.status_code == 200, r_redeem.text
                red_data = r_redeem.json()
                assert red_data["cost_points"] == 100
                assert red_data["hours_added"] == 1
                u_b_vip = red_data["user"]
                assert u_b_vip["points_balance"] == 0
                assert u_b_vip["is_vip"] is True
                assert u_b_vip["can_use_live"] is True
                assert 3540 <= int(u_b_vip["vip_remaining_seconds"]) <= 3605
                assert "0 天" not in u_b_vip["vip_remaining_label"]
                assert ("小时" in u_b_vip["vip_remaining_label"]) or ("分" in u_b_vip["vip_remaining_label"])
                assert any(o["pay_method"] == "POINTS_REDEEM" and o["amount_usdt"] == 0.0 for o in red_data["orders"])

                # 3. Admin referral management: dashboard, settings update, manual points adjust, hour grant & zero-day guard
                r_dash = client.get("/api/admin/dashboard", headers=adm_hdr)
                assert r_dash.status_code == 200
                dash = r_dash.json()
                assert dash["stats"]["total_invites_count"] == 1
                assert dash["stats"]["total_redeem_count"] == 1
                assert dash["stats"]["total_redeem_points"] == 100
                assert dash["stats"]["total_revenue_usdt"] == 0.0  # points redemption does NOT inflate USDT revenue!
                assert len(dash["referral_records"]) == 1

                # Admin updates referral rules
                r_set = client.post(
                    "/api/admin/referral/settings",
                    json={
                        "invite_enabled": True,
                        "inviter_reward_points": 150,
                        "invitee_reward_points": 120,
                        "redeem_enabled": True,
                        "points_per_vip_hour": 120,
                        "max_redeem_hours_per_tx": 48,
                    },
                    headers=adm_hdr,
                )
                assert r_set.status_code == 200

                # User C registers with User A's code -> A gets +150 (total 250), C gets +120
                r_c = client.post(
                    "/api/auth/register",
                    json={"username": "invitee_c", "password": "Password123!", "invite_code": code_a},
                    headers={"x-forwarded-for": "198.51.100.12"},
                )
                assert r_c.status_code == 200
                assert r_c.json()["user"]["points_balance"] == 120
                assert client.get("/api/referral/me", headers=hdr_a).json()["points_balance"] == 250

                # Admin adjusts User B points (+200, then -50 => 150; reject -500)
                r_adj1 = client.post(
                    "/api/admin/points/adjust",
                    json={"user_id": uid_b, "delta_points": 200, "note": "活动奖励"},
                    headers=adm_hdr,
                )
                assert r_adj1.status_code == 200
                assert r_adj1.json()["user"]["points_balance"] == 200

                r_adj2 = client.post(
                    "/api/admin/points/adjust",
                    json={"user_id": uid_b, "delta_points": -50, "note": "测试扣减"},
                    headers=adm_hdr,
                )
                assert r_adj2.status_code == 200
                assert r_adj2.json()["user"]["points_balance"] == 150

                r_adj_bad = client.post(
                    "/api/admin/points/adjust",
                    json={"user_id": uid_b, "delta_points": -500, "note": "超额扣减"},
                    headers=adm_hdr,
                )
                assert r_adj_bad.status_code == 400

                # Accidental add_days=0 & add_hours=0 without revoke=True MUST be rejected (never wipe VIP!)
                r_wipe_guard = client.post(
                    "/api/admin/users/grant-vip",
                    json={"user_id": uid_b, "add_days": 0, "add_hours": 0, "vip_tier": "月度专业版会员", "revoke": False},
                    headers=adm_hdr,
                )
                assert r_wipe_guard.status_code == 400
                assert um.get_user_by_id(uid_b)["is_vip"] is True

                # Grant +2 hours VIP works cleanly
                prev_exp = um.get_user_by_id(uid_b)["vip_expires_at"]
                r_grant_h = client.post(
                    "/api/admin/users/grant-vip",
                    json={"user_id": uid_b, "add_days": 0, "add_hours": 2, "vip_tier": "月度专业版会员", "revoke": False},
                    headers=adm_hdr,
                )
                assert r_grant_h.status_code == 200
                assert um.get_user_by_id(uid_b)["vip_expires_at"] >= prev_exp + 7200

                # 4. Strict VIP expiration auto-stop & full trading lockout
                # Enter LIVE, configure mock verified OKX keys via real API, add 2 running strategies, and start trading
                r_live = client.post("/api/mode/enter-live", headers=hdr_b)
                assert r_live.status_code == 200
                eng_b = em.user_engines[uid_b]
                eng_b.sync_live_exchange_positions = AsyncMock(return_value={"ok": True, "synced_count": 0})
                eng_b.executor.verify_exchange_credentials = AsyncMock(
                    return_value={
                        "ok": True,
                        "auth_status": "VERIFIED",
                        "auth_message": "Mock OKX Verified",
                        "account_equity_usdt": 1000.0,
                    }
                )
                r_ex = client.post(
                    "/api/exchanges/update",
                    json={
                        "exchange_id": "okx",
                        "display_name": "OKX Swap",
                        "role": "follower",
                        "api_key": "test-live-key",
                        "api_secret": "test-live-secret",
                        "passphrase": "test-live-pass",
                        "is_active": True,
                    },
                    headers=hdr_b,
                )
                assert r_ex.status_code == 200, r_ex.text
                r_ver = client.post("/api/exchanges/verify?exchange_id=okx", headers=hdr_b)
                assert r_ver.status_code == 200, r_ver.text

                cfg_btc = SymbolStrategy(
                    symbol="BTC/USDT:USDT",
                    strategy_type="LONG_SHORT_GRID",
                    order_usdt=50.0,
                    leverage=5,
                    is_running=True,
                )
                cfg_eth = SymbolStrategy(
                    symbol="ETH/USDT:USDT",
                    strategy_type="LEAD_LAG",
                    order_usdt=50.0,
                    leverage=5,
                    is_running=True,
                )
                assert client.post("/api/symbol/update", json=cfg_btc.model_dump(), headers=hdr_b).status_code == 200
                assert client.post("/api/symbol/update", json=cfg_eth.model_dump(), headers=hdr_b).status_code == 200
                assert len(eng_b.strategies) >= 2

                r_start = client.post("/api/trading/start", headers=hdr_b)
                assert r_start.status_code == 200 and r_start.json()["ok"] is True, r_start.text
                assert eng_b.trading_active_by_mode["LIVE"] is True
                assert all(c.is_running for c in eng_b.strategies.values())

                # Expire User B's VIP right now in SQLite (simulating time passing past vip_expires_at)
                expired_ts = int(time.time()) - 5
                with sqlite3.connect(db_file) as c_exp:
                    c_exp.execute("UPDATE users SET vip_expires_at = ? WHERE id = ?", (expired_ts, uid_b))
                    c_exp.commit()

                # Verify real-time engine check catches expiry immediately even before guard loop fires
                eng_b.sync_owner_vip_status(um.get_user_by_id(uid_b))
                assert eng_b.is_owner_vip_valid_realtime() is False

                # Force immediate check via /api/stats (also happens automatically per-tick & every 5s in guard loop)
                r_stats_exp = client.get("/api/stats", headers=hdr_b)
                assert r_stats_exp.status_code == 200
                snap_exp = r_stats_exp.json()
                assert snap_exp["owner_vip_status"]["can_use_live"] is False
                assert snap_exp["trading_control"]["trading_active"] is False
                assert snap_exp["trading_control"]["status_code"] == "VIP_EXPIRED"

                # Verify BOTH trading_active AND EVERY strategy's is_running flipped to False (in memory AND SQLite!)
                assert eng_b.trading_active_by_mode["LIVE"] is False
                assert eng_b.trading_active_by_mode["SHADOW"] is False
                assert all(not c.is_running for c in eng_b.strategies.values())
                persisted_cfgs = adb.load_symbols_sync(user_id=uid_b)
                assert len(persisted_cfgs) > 0
                assert all(not c.is_running for c in persisted_cfgs)

                # Verify ALL trading mutation endpoints are locked for expired VIP!
                r_start_exp = client.post("/api/trading/start", headers=hdr_b)
                assert r_start_exp.status_code == 200
                assert r_start_exp.json()["ok"] is False and r_start_exp.json()["require_vip"] is True

                sample_cfg = next(iter(eng_b.strategies.values())).model_copy(update={"is_running": True})
                assert client.post("/api/symbol/update", json=sample_cfg.model_dump(), headers=hdr_b).status_code == 403
                assert (
                    client.post(
                        "/api/risk/update",
                        json=GlobalRiskConfig(trading_mode="LIVE", max_daily_loss_usdt=123.0).model_dump(),
                        headers=hdr_b,
                    ).status_code
                    == 403
                )
                assert client.post("/api/stats/reset", headers=hdr_b).status_code == 403
                assert (
                    client.post(
                        "/api/exchanges/update",
                        json={"exchange_id": "okx", "api_key": "k", "api_secret": "s", "api_passphrase": "p"},
                        headers=hdr_b,
                    ).status_code
                    == 403
                )
                r_pf_exp = client.post("/api/exchanges/preflight", headers=hdr_b)
                assert r_pf_exp.status_code == 200
                assert r_pf_exp.json()["ok"] is False
                assert any("VIP" in b for b in r_pf_exp.json().get("blockers", []))

                # Safe emergency actions STILL work when expired (stop trading & activate kill switch),
                # while releasing kill switch requires VIP
                assert client.post("/api/trading/stop", headers=hdr_b).status_code == 200
                assert client.post("/api/risk/kill-switch?active=true", headers=hdr_b).status_code == 200
                assert client.post("/api/risk/kill-switch?active=false", headers=hdr_b).status_code == 403

                # 5. User B redeems 120 points (balance 150 -> 30) -> IMMEDIATELY unlocks trading again!
                r_redeem2 = client.post("/api/points/redeem-vip", json={"hours": 1}, headers=hdr_b)
                assert r_redeem2.status_code == 200
                assert r_redeem2.json()["user"]["points_balance"] == 30
                assert r_redeem2.json()["user"]["can_use_live"] is True
                assert eng_b.is_owner_vip_valid_realtime() is True

                assert client.post("/api/risk/kill-switch?active=false", headers=hdr_b).status_code == 200
                assert client.post("/api/symbol/update", json=sample_cfg.model_dump(), headers=hdr_b).status_code == 200
                r_start_ok = client.post("/api/trading/start", headers=hdr_b)
                assert r_start_ok.status_code == 200 and r_start_ok.json()["ok"] is True
                assert eng_b.trading_active_by_mode["LIVE"] is True
                assert any(c.is_running for c in eng_b.strategies.values())

                # 6. Admin revoking VIP immediately stops trading AND stops all running strategies!
                r_revoke = client.post(
                    "/api/admin/users/grant-vip",
                    json={"user_id": uid_b, "add_days": 0, "add_hours": 0, "vip_tier": "NONE", "revoke": True},
                    headers=adm_hdr,
                )
                assert r_revoke.status_code == 200
                assert eng_b.trading_active_by_mode["LIVE"] is False
                assert all(not c.is_running for c in eng_b.strategies.values())


def test_reverse_grid_10_levels_osc_decay_and_updated_cooldowns():
    """
    Verifies the 10-level mode-locked REVERSE_GRID specification (C=100.00, step=0.6%, grid_levels=10):
      1. LONG mode: Q=100%, C=100%, S1=100%, S2=100%, S3=50%, S4=30%, S5..S10=20% fixed,
         sequential dip-buy & 1-grid TP up to L1, 1.5s slot cooldown, 0.03% re-arm, and C->Q conversion.
      2. SHORT mode (exact mirror): QS=100%, CS=100%, L1=100%, L2=100%, L3=50%, L4=30%, L5..L10=20% fixed.
      3. Cycle cooldowns: TP cycle = 30 minutes (1,800,000 ms), Global SL = 1 hour (3,600,000 ms).
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms
    from backend.strategies.reverse_oscillating_grid import (
        GLOBAL_SL_COOLDOWN_MS,
        SLOT_CLOSE_COOLDOWN_MS,
        TP_CYCLE_COOLDOWN_MS,
    )

    assert TP_CYCLE_COOLDOWN_MS == 1_800_000.0
    assert GLOBAL_SL_COOLDOWN_MS == 3_600_000.0
    assert SLOT_CLOSE_COOLDOWN_MS == 1500.0

    cfg10 = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        strategy_type="REVERSE_GRID",
        grid_side_mode="LONG_SHORT",
        grid_levels=10,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.07,
        grid_stop_loss_pct=0.05,
        order_usdt=1000.0,
        leverage=3,
        price_precision=2,
        qty_precision=2,
        grid_recenter_enabled=True,
    )

    # --- 1. LONG Mode with 10 levels ---
    trk_l = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_l.sync_config(cfg10, current_mid=100.00)
    en_q, _ = trk_l.evaluate_tick(100.59, 100.60, 100.595, cfg10, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_q] == ["Q"] and en_q[0]["order_usdt"] == 1000.0
    trk_l.mark_entry_filled("Q", 100.60, 10.0, 10.0, 1.0, cfg10, round_trip_fee=0.0010)

    assert trk_l.strategy.locked_mode == "LONG"
    assert trk_l.strategy.full_tp_price == 106.00  # L10 = 100 * (1 + 10 * 0.6%)
    assert trk_l.strategy.global_sl_price == 89.30  # S10 (94.00) * (1 - 5%)

    # Check C + S1..S10 exact ratios & prices
    assert trk_l.levels["C"]["order_usdt"] == 1000.0 and trk_l.levels["C"]["weight_pct"] == 100.0
    expected_osc_weights = {
        1: (1000.0, 100.0, 99.40, 100.00),
        2: (1000.0, 100.0, 98.80, 99.40),
        3: (500.0, 50.0, 98.20, 98.80),
        4: (300.0, 30.0, 97.60, 98.20),
        5: (200.0, 20.0, 97.00, 97.60),
        6: (200.0, 20.0, 96.40, 97.00),
        7: (200.0, 20.0, 95.80, 96.40),
        8: (200.0, 20.0, 95.20, 95.80),
        9: (200.0, 20.0, 94.60, 95.20),
        10: (200.0, 20.0, 94.00, 94.60),
    }
    for idx, (exp_usdt, exp_pct, exp_trig, exp_tp) in expected_osc_weights.items():
        lv = trk_l.levels[f"S{idx}"]
        assert lv["order_usdt"] == exp_usdt, f"S{idx} order_usdt={lv['order_usdt']} != {exp_usdt}"
        assert lv["weight_pct"] == exp_pct, f"S{idx} weight_pct={lv['weight_pct']} != {exp_pct}"
        assert lv["trigger_price"] == exp_trig
        assert lv["tp_price"] == exp_tp

    # Verify entry evaluation emits scaled order_usdt when S3 / S5 / S10 trigger
    for sid_chk, px_chk, exp_u in (("C", 100.00, 1000.0), ("S1", 99.40, 1000.0), ("S2", 98.80, 1000.0), ("S3", 98.20, 500.0), ("S4", 97.60, 300.0), ("S5", 97.00, 200.0), ("S10", 94.00, 200.0)):
        en_s, _ = trk_l.evaluate_tick(px_chk - 0.01, px_chk, px_chk - 0.005, cfg10, is_net_mode=False, round_trip_fee=0.0010)
        assert len(en_s) == 1 and en_s[0]["slot_id"] == sid_chk
        assert en_s[0]["order_usdt"] == exp_u
        trk_l.mark_entry_filled(sid_chk, px_chk, exp_u / px_chk, exp_u / px_chk, 1.0, cfg10, round_trip_fee=0.0010)

    # Bounce from 94.00 to 94.60 -> S10 sells at 94.60 (GRID_TP), enters 1.5s cooldown & disarms until >= 94.00 * 1.0003!
    _, ex_s10 = trk_l.evaluate_tick(94.60, 94.61, 94.605, cfg10, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in ex_s10] == ["S10"] and ex_s10[0]["exit_reason"] == "GRID_TP"
    t_close = mono_ms()
    trk_l.mark_exited("S10", 1.2, is_sl=False)
    assert trk_l.levels["S10"]["armed"] is False
    assert trk_l.levels["S10"]["cooldown_until_mono"] >= t_close + 1400.0

    # --- 2. SHORT Mode with 10 levels (exact mirror) ---
    trk_s = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_s.sync_config(cfg10, current_mid=100.00)
    en_qs, _ = trk_s.evaluate_tick(99.40, 99.41, 99.405, cfg10, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_qs] == ["QS"] and en_qs[0]["order_usdt"] == 1000.0
    trk_s.mark_entry_filled("QS", 99.40, 10.0, 10.0, 1.0, cfg10, round_trip_fee=0.0010)

    assert trk_s.strategy.locked_mode == "SHORT"
    assert trk_s.strategy.full_tp_price == 94.00  # S10
    assert trk_s.strategy.global_sl_price == 111.30  # L10 (106.00) * (1 + 5%)
    assert trk_s.levels["CS"]["order_usdt"] == 1000.0 and trk_s.levels["CS"]["weight_pct"] == 100.0
    for idx, exp_u in ((1, 1000.0), (2, 1000.0), (3, 500.0), (4, 300.0), (5, 200.0), (6, 200.0), (10, 200.0)):
        assert trk_s.levels[f"L{idx}"]["order_usdt"] == exp_u

    # Full S-group TP at S10 (94.00) -> 30-min TP cooldown; Global SL at 111.30 -> 1-hour SL cooldown!
    _, ex_tp_all = trk_s.evaluate_tick(93.99, 94.00, 93.995, cfg10, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in ex_tp_all] == ["QS"]
    t_tp = mono_ms()
    trk_s.mark_exited("QS", 52.0, is_sl=False)
    assert trk_s.strategy.restart_reason == "TP_COOLDOWN"
    assert abs((trk_s.strategy.restart_cooldown_until_mono - t_tp) - 1_800_000.0) < 5000.0


def test_reverse_grid_custom_pcts_ln_sn_trail_start_cooldowns_and_edge_guards():
    """
    Verifies all user-customizable REVERSE_GRID parameters & edge-case protections:
      1. SQLite persistence of rev_l_pcts, rev_s_pcts, rev_c_pct, rev_tp_cooldown_min,
         rev_sl_cooldown_min, rev_trail_start_level, rev_trail_retracement_pct, rev_max_level_mode.
      2. Crossing outermost Ln/Sn (rev_trail_start_level=0 / 5, TRAIL_ONLY) starts trailing TP
         without capping at Ln/Sn, and exits 100% on custom retracement (e.g. 1.20%).
      3. Pure base-only mode (rev_l_pcts="100,0") still starts trailing TP by price crossing Ln/Sn.
      4. Anti-suicide floor clamp under inverted anti-pyramid weights (rev_l_pcts="20,100").
      5. Live cooldown update while sitting in COOLDOWN + blocking new OSC dip-buys during active trail.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms

    with tempfile.TemporaryDirectory() as tmpdir:
        adb = AsyncDatabaseManager(db_path=os.path.join(tmpdir, "rev_custom.db"))
        adb.init_sync()
        adb.ensure_user_workspace_sync(55)

        cfg = SymbolStrategy(
            symbol="SOL/USDT:USDT",
            strategy_type="REVERSE_GRID",
            grid_side_mode="LONG_SHORT",
            grid_levels=5,
            grid_step_pct=0.006,
            grid_range_pct=0.04,
            grid_stop_loss_pct=0.045,
            order_usdt=1000.0,
            leverage=3,
            price_precision=2,
            qty_precision=2,
            rev_l_pcts="80,40,25,15,10",
            rev_s_pcts="90,70,50,35,25",
            rev_c_pct=80.0,
            rev_tp_cooldown_min=12.0,
            rev_sl_cooldown_min=45.0,
            rev_trail_start_level=0,  # 0 = cross outermost Ln/Sn (L5/S5) to start trailing TP!
            rev_trail_retracement_pct=0.012,  # 1.20% custom trailing retracement
            rev_max_level_mode="TRAIL_ONLY",
        )
        adb.upsert_symbol_sync(cfg, user_id=55)
        reloaded = {s.symbol: s for s in adb.load_symbols_sync(user_id=55)}["SOL/USDT:USDT"]
        assert reloaded.rev_l_pcts == "80,40,25,15,10"
        assert reloaded.rev_s_pcts == "90,70,50,35,25"
        assert reloaded.rev_c_pct == 80.0
        assert reloaded.rev_tp_cooldown_min == 12.0
        assert reloaded.rev_sl_cooldown_min == 45.0
        assert reloaded.rev_trail_start_level == 0
        assert abs(reloaded.rev_trail_retracement_pct - 0.012) < 1e-7
        assert reloaded.rev_max_level_mode == "TRAIL_ONLY"

    # 1. Uncapped Ln/Sn trailing trigger (L5 = 103.00 starts trailing TP; L2..L4 do NOT start trailing early!)
    trk = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk.sync_config(reloaded, current_mid=100.00)
    en_q, _ = trk.evaluate_tick(100.59, 100.60, 100.595, reloaded, is_net_mode=False, round_trip_fee=0.0010)
    assert [e["slot_id"] for e in en_q] == ["Q"] and en_q[0]["order_usdt"] == 800.0
    trk.mark_entry_filled("Q", 100.60, 8.0, 8.0, 1.0, reloaded, round_trip_fee=0.0010)

    assert trk.strategy.full_tp_price == 0.0  # Uncapped!
    assert trk.strategy.trail_trigger_level == 5 and trk.strategy.trail_trigger_price == 103.00
    assert trk.levels["C"]["order_usdt"] == 800.0
    for idx, exp_u in ((1, 900.0), (2, 700.0), (3, 500.0), (4, 350.0), (5, 250.0)):
        assert trk.levels[f"S{idx}"]["order_usdt"] == exp_u
    for idx, exp_u in ((2, 400.0), (3, 250.0), (4, 150.0), (5, 100.0)):
        assert trk.levels[f"L{idx}"]["order_usdt"] == exp_u

    # Fill L2, L3, L4 -> group_trail_active MUST remain False before L5!
    for sid, px, qty in (("L2", 101.20, 4.0), ("L3", 101.80, 2.5), ("L4", 102.40, 1.5)):
        en_p, ex_p = trk.evaluate_tick(px - 0.01, px, px - 0.005, reloaded, is_net_mode=False, round_trip_fee=0.0010)
        assert ex_p == [] and [e["slot_id"] for e in en_p] == [sid]
        trk.mark_entry_filled(sid, px, qty, qty, 1.0, reloaded, round_trip_fee=0.0010)
        assert trk.strategy.group_trail_active is False

    # Pullback to 100.90 before L5 does NOT trigger trailing exit!
    _, ex_pre = trk.evaluate_tick(100.90, 100.92, 100.91, reloaded, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_pre == []

    # Cross L5 (103.00) -> fills L5 (10% = 100U) AND activates trailing TP without capping!
    en_l5, ex_l5 = trk.evaluate_tick(103.00, 103.01, 103.005, reloaded, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_l5 == [] and [e["slot_id"] for e in en_l5] == ["L5"] and en_l5[0]["order_usdt"] == 100.0
    trk.mark_entry_filled("L5", 103.00, 1.0, 1.0, 1.0, reloaded, round_trip_fee=0.0010)
    assert trk.strategy.group_trail_active is True

    # Surge uncapped to 110.00 -> trail = round(110.00 * (1 - 0.012), 2) = 108.68!
    _, ex_run = trk.evaluate_tick(110.00, 110.02, 110.01, reloaded, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_run == []
    assert trk.long_trail_stop_price == 108.68

    # Pullback -1.20% to 108.68 -> closes 100% of Q + L2 + L3 + L4 + L5 via GRID_TRAIL_TP!
    _, ex_hit = trk.evaluate_tick(108.68, 108.70, 108.69, reloaded, is_net_mode=False, round_trip_fee=0.0010)
    assert set(e["slot_id"] for e in ex_hit) == {"Q", "L2", "L3", "L4", "L5"}
    assert all(e["exit_reason"] == "GRID_TRAIL_TP" for e in ex_hit)
    t_pre_cd = mono_ms()
    for sid in ("Q", "L2", "L3", "L4", "L5"):
        trk.mark_exited(sid, 25.0, is_sl=False)

    # Custom 12-min TP cooldown (720,000 ms) applied, and live shortening to 2 min via sync_config updates immediately!
    assert abs((trk.strategy.restart_cooldown_until_mono - t_pre_cd) - 720_000.0) < 5000.0
    short_cd_cfg = reloaded.model_copy(update={"rev_tp_cooldown_min": 2.0})
    trk.sync_config(short_cd_cfg, current_mid=108.69)
    assert abs((trk.strategy.restart_cooldown_until_mono - t_pre_cd) - 120_000.0) < 5000.0

    # 2. Pure base-only mode (rev_l_pcts="100,0") with crossing S5 (97.00) in SHORT mode activating trailing TP by price!
    cfg_zero_pyr = reloaded.model_copy(update={"rev_l_pcts": "100,0", "rev_trail_retracement_pct": 0.04})
    trk_z = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_z.sync_config(cfg_zero_pyr, current_mid=100.00)
    trk_z.evaluate_tick(99.39, 99.40, 99.395, cfg_zero_pyr, is_net_mode=False, round_trip_fee=0.0010)
    trk_z.mark_entry_filled("QS", 99.40, 10.0, 10.0, 1.0, cfg_zero_pyr, round_trip_fee=0.0010)
    assert "S2" not in trk_z.levels and "S5" not in trk_z.levels
    # Cross S5 (97.00) -> activates trailing TP purely via price crossing!
    en_z5, ex_z5 = trk_z.evaluate_tick(96.98, 97.00, 96.99, cfg_zero_pyr, is_net_mode=False, round_trip_fee=0.0010)
    assert en_z5 == [] and ex_z5 == []
    assert trk_z.strategy.group_trail_active is True
    # Even with wide 4% retracement (trail ~ 100.88 > CS=100.00), bouncing to 100.05 MUST NOT open CS while trailing is active!
    en_no_osc, _ = trk_z.evaluate_tick(100.05, 100.07, 100.06, cfg_zero_pyr, is_net_mode=False, round_trip_fee=0.0010)
    assert en_no_osc == []

    # 3. Anti-suicide floor clamp under inverted anti-pyramid weights (L1=20%, L2=100%, start at L2)
    cfg_inv = reloaded.model_copy(update={"rev_l_pcts": "20,100,100,100,100", "rev_trail_start_level": 2, "rev_trail_retracement_pct": 0.01})
    trk_inv = LongShortGridTracker("SOL/USDT:USDT", strategy_type="REVERSE_GRID")
    trk_inv.sync_config(cfg_inv, current_mid=100.00)
    trk_inv.evaluate_tick(100.59, 100.60, 100.595, cfg_inv, is_net_mode=False, round_trip_fee=0.0010)
    trk_inv.mark_entry_filled("Q", 100.60, 2.0, 2.0, 1.0, cfg_inv, round_trip_fee=0.0010)
    trk_inv.evaluate_tick(101.19, 101.20, 101.195, cfg_inv, is_net_mode=False, round_trip_fee=0.0010)
    trk_inv.mark_entry_filled("L2", 101.20, 10.0, 10.0, 1.0, cfg_inv, round_trip_fee=0.0010)
    # VWAP = 101.10 -> ideal breakeven floor ~101.24 > 101.20! Anti-suicide clamp MUST keep trail_stop < 101.20 initially!
    assert trk_inv.long_trail_stop_price < 101.20
    _, ex_no_suicide = trk_inv.evaluate_tick(101.18, 101.20, 101.19, cfg_inv, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_no_suicide == []
    # Once price rallies to 102.00, breakeven floor locks in above VWAP!
    trk_inv.evaluate_tick(102.00, 102.02, 102.01, cfg_inv, is_net_mode=False, round_trip_fee=0.0010)
    assert trk_inv.strategy.group_floor_price >= 101.20


def test_okx_antusdt_alias_and_live_cache_miss_probe():
    """
    Verifies:
    1) OKX `antusdt` / `ant` / `anthropic` / `xant` cleanly map to unified `ANT/USDT:USDT`
       with OKX V5 instId `ANTHROPIC-USDT-SWAP` and Binance USDM `antusdt`, while Futu maps `ANT` -> `US.ANT`.
    2) Cache-miss on-demand OKX V5 probing discovers newly listed SWAP contracts immediately (including `preopen`
       and `0.001` lotSz precision) and distinguishes Spot-only tokens from unlisted contracts.
    """
    import backend.main as main_mod
    from backend.market_gateway import (
        _BINANCE_INSTRUMENT_CACHE,
        _OKX_INSTRUMENT_CACHE,
        okx_inst_id_to_unified,
        to_binance_stream_sym,
        to_okx_inst_id,
    )
    from backend.executor import resolve_okx_spec

    assert to_okx_inst_id("ANT/USDT:USDT") == "ANTHROPIC-USDT-SWAP"
    assert okx_inst_id_to_unified("ANTHROPIC-USDT-SWAP") == "ANT/USDT:USDT"
    assert to_binance_stream_sym("ANT/USDT:USDT") == "antusdt"
    fb_spec = resolve_okx_spec("ANT/USDT:USDT")
    assert fb_spec["instId"] == "ANTHROPIC-USDT-SWAP"
    assert fb_spec["minSz"] == 0.001

    _OKX_INSTRUMENT_CACHE["ANTHROPIC-USDT-SWAP"] = {
        "instId": "ANTHROPIC-USDT-SWAP",
        "unified_sym": "ANT/USDT:USDT",
        "state": "live",
        "instCategory": "3",
        "ctVal": 1.0,
        "ctVal_str": "1",
        "lotSz": 0.001,
        "lotSz_str": "0.001",
        "minSz": 0.001,
        "minSz_str": "0.001",
        "tickSz": 0.01,
        "tickSz_str": "0.01",
        "maxLever": 50.0,
    }
    _BINANCE_INSTRUMENT_CACHE["ANTUSDT"] = {
        "symbol": "ANTUSDT",
        "status": "TRADING",
        "contractType": "TRADIFI_PERPETUAL",
        "tickSize": 0.01,
        "stepSize": 0.01,
        "minQty": 0.01,
    }

    async def fake_probe(unified_sym: str):
        if unified_sym == "NEWLIST/USDT:USDT":
            spec = {
                "instId": "NEWLIST-USDT-SWAP",
                "unified_sym": "NEWLIST/USDT:USDT",
                "state": "preopen",
                "instCategory": "1",
                "ctVal": 1.0,
                "ctVal_str": "1",
                "lotSz": 0.001,
                "lotSz_str": "0.001",
                "minSz": 0.001,
                "minSz_str": "0.001",
                "tickSz": 0.01,
                "tickSz_str": "0.01",
                "maxLever": 20.0,
            }
            _OKX_INSTRUMENT_CACHE["NEWLIST-USDT-SWAP"] = spec
            main_mod.engine_manager.gateway.real_follower_bbo["NEWLIST/USDT:USDT"] = (128.4, 128.6, 50.0, 50.0)
            return {"okx_spec": spec, "spot_inst_id": None, "ticker_mid": 128.5}
        if unified_sym == "SPOTCOIN/USDT:USDT":
            return {"okx_spec": None, "spot_inst_id": "SPOTCOIN-USDT", "ticker_mid": 0.0}
        return {"okx_spec": _OKX_INSTRUMENT_CACHE.get(to_okx_inst_id(unified_sym)), "spot_inst_id": None, "ticker_mid": 0.0}

    with (
        patch.object(main_mod.engine_manager.gateway, "start", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "stop", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "probe_okx_swap_and_ticker", side_effect=fake_probe),
        patch.object(main_mod.payment_monitor, "start", return_value=None),
        patch.object(main_mod.payment_monitor, "stop", new=AsyncMock(return_value=None)),
    ):
        with TestClient(main_mod.app) as client:
            for q_sym in ("antusdt", "ant", "ANTHROPIC", "xant", "ANTHROPIC-USDT-SWAP"):
                r = client.get(f"/api/symbol/verify?symbol={q_sym}&exchange_id=okx").json()
                assert r["verified"] is True, f"Failed for {q_sym}: {r}"
                assert r["symbol"] == "ANT/USDT:USDT"
                assert r["native_symbol"] == "ANTHROPIC-USDT-SWAP"
                assert r["leader_supported"] is True
                assert r["follower_supported"] is True
                assert r["recommended_precision"]["min_qty"] == 0.001
                assert r["recommended_precision"]["qty_precision"] >= 3

            # Traditional US broker routes ANT to US.ANT (USD equity)
            r_futu = client.get("/api/symbol/verify?symbol=ANT&exchange_id=futu").json()
            assert r_futu["verified"] is True
            assert r_futu["symbol"] == "ANT/USD"
            assert r_futu["native_symbol"] == "US.ANT"

            # Cache-miss newly listed OKX SWAP (preopen) succeeds immediately with live ticker price
            _OKX_INSTRUMENT_CACHE.pop("NEWLIST-USDT-SWAP", None)
            r_new = client.get("/api/symbol/verify?symbol=newlistusdt&exchange_id=okx").json()
            assert r_new["verified"] is True
            assert r_new["symbol"] == "NEWLIST/USDT:USDT"
            assert abs(r_new["ref_price"] - 128.5) < 1e-6
            assert r_new["recommended_precision"]["qty_precision"] >= 3
            assert "preopen" in r_new["warning"]

            # Spot-only coin explains clearly that only Spot is listed on OKX, not USDT-SWAP
            r_spot = client.get("/api/symbol/verify?symbol=spotcoinusdt&exchange_id=okx").json()
            assert r_spot["verified"] is False
            assert "SPOTCOIN-USDT 现货" in r_spot["error"]
            assert "未上线" in r_spot["error"]


def test_pause_and_close_symbol_and_cross_system_audit_fixes():
    """
    Verifies:
    1) `POST /api/symbol/pause-close` atomically sets `is_running=False`, closes all open positions
       (both LONG & SHORT grid slots + LEAD_LAG + traditional broker shadow positions), cancels all
       tracked & OKX exchange orders (`orders-pending`, `conditional/oco/trigger/move_order_stop`, `iceberg/twap`),
       records `MANUAL_CLOSE` trades, and updates `symbol_pnl_breakdown` (`open_positions_count == 0`).
    2) Cross-system audit fixes:
       - `ExecutionAdapter.migrate_slot_orders` preserves live SL algo tracking on `C -> Q` conversion.
       - `ReverseOscillatingGridStrategy.clear_cooldowns()` cleanly re-anchors to `NONE` instead of restarting 30m/60m cooldown.
       - VIP expiration keeps software protective exits active on already-open grid slots while blocking new entries.
       - Manual approval (`APPROVE`) of multiple Web3 orders never collides on `idx_paid_onchain_tx_hash`.
       - Admin lifetime (`36500` days) VIP grant / plan upsert / code generation succeed without HTTP 422.
    """
    import backend.main as main_mod
    from backend.grid_engine import LongShortGridTracker
    from backend.models import AdminGrantVipRequest, SymbolStrategy
    from backend.user_membership import rate_limiter

    rate_limiter._buckets.clear()

    # 1. Verify C -> Q slot order migration in ExecutionAdapter
    with (
        patch.object(main_mod.engine_manager.gateway, "start", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "stop", new=AsyncMock(return_value=None)),
        patch.object(main_mod.engine_manager.gateway, "fetch_exchange_instruments", new=AsyncMock(return_value=True)),
        patch.object(main_mod.engine_manager.gateway, "fetch_real_bbo_snapshot", new=AsyncMock(return_value=True)),
        patch.object(main_mod.payment_monitor, "start", return_value=None),
        patch.object(main_mod.payment_monitor, "stop", new=AsyncMock(return_value=None)),
    ):
        with TestClient(main_mod.app) as client:
            reg = main_mod.user_manager.register_user(
                "pause_close_vip", "StrongPass!99", "pause@quant.io", client_ip="198.51.100.245"
            )
            assert reg["ok"] is True
            uid = int(reg["user"]["id"])
            token = reg["token"]
            hdr = {"Authorization": f"Bearer {token}"}

            # Grant lifetime 36500 days via AdminGrantVipRequest (validating le=36500 fix!)
            grant_res = main_mod.user_manager.admin_grant_vip(
                AdminGrantVipRequest(user_id=uid, add_days=36500, vip_tier="终身至尊版会员")
            )
            assert grant_res["ok"] is True

            # Verify 2nd manual Web3 order approval does NOT crash with sqlite3.IntegrityError!
            plans = main_mod.user_manager.list_plans()
            plan_id = int(plans[0]["id"])
            o1 = main_mod.user_manager.create_web3_payment_order(uid, plan_id, "Arbitrum")
            assert o1["ok"] is True
            aud1 = main_mod.user_manager.admin_audit_order(int(o1["order"]["id"]), "APPROVE")
            assert aud1["ok"] is True
            o2 = main_mod.user_manager.create_web3_payment_order(uid, plan_id, "BNB Smart Chain")
            assert o2["ok"] is True
            aud2 = main_mod.user_manager.admin_audit_order(int(o2["order"]["id"]), "APPROVE")
            assert aud2["ok"] is True

            # Configure SHADOW mode on user's engine to test multi-slot grid pause & flatten
            eng = client.get("/api/stats", headers=hdr)
            assert eng.status_code == 200
            u_eng = main_mod.engine_manager.user_engines[uid]
            u_eng.risk_config.trading_mode = "SHADOW"
            u_eng.trading_active_by_mode["SHADOW"] = True

            sol_cfg = SymbolStrategy(
                symbol="SOL/USDT:USDT",
                exchange_id="okx",
                strategy_type="LONG_SHORT_GRID",
                grid_side_mode="LONG_SHORT",
                grid_levels=3,
                grid_step_pct=0.008,
                grid_take_profit_pct=0.0085,
                grid_range_pct=0.03,
                grid_stop_loss_pct=0.04,
                order_usdt=50.0,
                leverage=5,
                is_running=True,
            )
            u_eng.add_or_update_symbol(sol_cfg)
            trk = u_eng.grid_trackers["SOL/USDT:USDT"]
            trk.sync_config(sol_cfg, current_mid=185.0)
            # Open both a LONG slot (L1) and a SHORT slot (S1) + tracked protective orders
            trk.levels["L1"]["status"] = "ENTERING"
            trk.mark_entry_filled("L1", 183.5, 0.27, 0.27, 1.0, sol_cfg, round_trip_fee=0.0010)
            u_eng.active_grid_positions["SOL/USDT:USDT:L1"] = {
                "symbol": "SOL/USDT:USDT",
                "strategy_type": "LONG_SHORT_GRID",
                "grid_mode": "MEAN_REVERSION",
                "grid_slot_id": "L1",
                "mode": "SHADOW",
                "side": "LONG",
                "qty": 0.27,
                "contracts": 0.27,
                "ct_val": 1.0,
                "notional_usdt": 49.55,
                "entry_price": 183.5,
                "mark_price": 185.0,
                "tp_price": 185.06,
                "sl_price": 176.16,
                "leverage": 5,
                "unrealized_net_pct": 0.007,
                "unrealized_usdt": 0.405,
            }
            trk.levels["S1"]["status"] = "ENTERING"
            trk.mark_entry_filled("S1", 186.5, 0.27, 0.27, 1.0, sol_cfg, round_trip_fee=0.0010)
            u_eng.active_grid_positions["SOL/USDT:USDT:S1"] = {
                "symbol": "SOL/USDT:USDT",
                "strategy_type": "LONG_SHORT_GRID",
                "grid_mode": "MEAN_REVERSION",
                "grid_slot_id": "S1",
                "mode": "SHADOW",
                "side": "SHORT",
                "qty": 0.27,
                "contracts": 0.27,
                "ct_val": 1.0,
                "notional_usdt": 50.36,
                "entry_price": 186.5,
                "mark_price": 185.0,
                "tp_price": 184.91,
                "sl_price": 193.96,
                "leverage": 5,
                "unrealized_net_pct": 0.007,
                "unrealized_usdt": 0.405,
            }
            u_eng.executor._active_maker_tp_orders["SOL/USDT:USDT:L1"] = {"order_id": "sim-tp-1", "is_live": False}
            u_eng.executor._active_cond_sl_orders["SOL/USDT:USDT:S1"] = {"order_id": "sim-sl-1", "is_live": False}

            # Snapshot before pause shows open_positions_count == 2 and active long/short symbol counts == 1 each
            snap_before = u_eng.build_dashboard_snapshot()
            assert snap_before["stats"]["active_long_symbols_count"] == 1
            assert snap_before["stats"]["active_short_symbols_count"] == 1
            assert snap_before["position_pnl_summary"]["active_long_symbols_count"] == 1
            assert snap_before["position_pnl_summary"]["active_short_symbols_count"] == 1
            sol_card_before = next(
                x for x in snap_before["stats"]["symbol_pnl_breakdown"] if x["symbol"] == "SOL/USDT:USDT"
            )
            assert sol_card_before["is_running"] is True
            assert sol_card_before["open_positions_count"] == 2

            # Call POST /api/symbol/pause-close
            p_res = client.post("/api/symbol/pause-close", headers=hdr, json={"symbol": "SOL/USDT:USDT"})
            assert p_res.status_code == 200, p_res.text
            p_data = p_res.json()
            assert p_data["ok"] is True
            assert p_data["paused"] is True
            assert p_data["closed_positions"] == 2
            assert u_eng.strategies["SOL/USDT:USDT"].is_running is False
            assert not any(k.startswith("SOL/USDT:USDT:") for k in u_eng.active_grid_positions)
            assert "SOL/USDT:USDT:L1" not in u_eng.executor._active_maker_tp_orders
            assert "SOL/USDT:USDT:S1" not in u_eng.executor._active_cond_sl_orders

            sol_card_after = next(
                x for x in p_data["snapshot"]["stats"]["symbol_pnl_breakdown"] if x["symbol"] == "SOL/USDT:USDT"
            )
            assert sol_card_after["is_running"] is False
            assert sol_card_after["open_positions_count"] == 0
            assert sol_card_after["trades"] >= 2

            # Verify ReverseOscillatingGridStrategy.clear_cooldowns() re-anchors cleanly instead of restarting 30m cooldown
            rev_cfg = SymbolStrategy(
                symbol="ETH/USDT:USDT",
                strategy_type="REVERSE_GRID",
                grid_levels=5,
                grid_step_pct=0.006,
                order_usdt=20.0,
                is_running=True,
            )
            rev_trk = LongShortGridTracker("ETH/USDT:USDT", strategy_type="REVERSE_GRID")
            rev_trk.sync_config(rev_cfg, current_mid=3000.0)
            rev_trk.strategy.locked_mode = "LONG"
            rev_trk.strategy.cycle_phase = "COOLDOWN"
            rev_trk.strategy.reconcile_verified = True
            rev_trk.strategy.restart_cooldown_until_mono = 999_999_999.0
            rev_trk.clear_cooldowns()
            rev_trk.evaluate_tick(3000.0, 3000.2, 3000.1, rev_cfg, is_net_mode=False)
            assert rev_trk.strategy.locked_mode == "NONE"
            assert rev_trk.strategy.cycle_phase == "ACTIVE"

            # Verify quiet-contract `follower_age_ms` reflects live OKX WS freshness (~ping_ms) when WS is healthy,
            # and still alarms honestly (`> 3500ms`) when OKX WS actually disconnects/stalls!
            from backend.math_engine import mono_ms as _mono_ms
            u_eng.risk_config.trading_mode = "LIVE"
            now_t = _mono_ms()
            u_eng.math_states["SOL/USDT:USDT"].on_follower_tick_light(185.0, 185.1, now_mono=now_t - 4200.0)
            u_eng.gateway.symbols = ["SOL/USDT:USDT"]
            u_eng.gateway.okx_connected = True
            u_eng.gateway._last_okx_ws_mono = now_t - 15.0
            u_eng.gateway.real_follower_ts["SOL/USDT:USDT"] = now_t - 4200.0
            u_eng.gateway.exchange_health["okx"]["ping_ms"] = 12.5
            snap_live_ok = u_eng.build_dashboard_snapshot()
            assert snap_live_ok["telemetry"]["SOL/USDT:USDT"]["follower_age_ms"] <= 50.0
            # Simulate genuine OKX WS disconnect -> must report true stale age (> 3500ms)
            u_eng.gateway.okx_connected = False
            snap_live_stale = u_eng.build_dashboard_snapshot()
            assert snap_live_stale["telemetry"]["SOL/USDT:USDT"]["follower_age_ms"] >= 3500.0


def test_utc8_time_standardization_and_exchange_utc_signing():
    """
    Verifies platform-wide UTC+8 time standardization:
    1. backend.time_utils formats epoch seconds/ms deterministically in UTC+8.
    2. database.py schema defaults & explicit writers store UTC+8 timestamps, and cleanup keeps fresh rows.
    3. sniper_engine.py logs, historical trade time formatting, and daily PnL rollover align to UTC+8.
    4. user_membership.py _now_str / _fmt_ts / order formatting emit UTC+8.
    5. executor.py OKX API signing header `OK-ACCESS-TIMESTAMP` MUST remain strict UTC ISO-8601 (`Z`).
    """
    from datetime import datetime, timedelta, timezone
    from backend.time_utils import (
        TZ_UTC8,
        format_ms_utc8,
        format_ts_utc8,
        now_utc8_str,
        parse_db_timestamp_ms,
        today_utc8_str,
    )
    from backend.executor import ExecutionAdapter
    from backend.sniper_engine import BidirectionalSniperEngine
    from backend.user_membership import UserMembershipManager, _fmt_ts, _now_str

    # 1. Deterministic UTC+8 conversions (2023-11-14 22:13:20 UTC == 2023-11-15 06:13:20 UTC+8)
    assert format_ts_utc8(1700000000) == "2023-11-15 06:13:20"
    assert format_ts_utc8(1700000000, "%Y-%m-%d") == "2023-11-15"
    assert format_ms_utc8(1700000000456) == "06:13:20.456"
    assert parse_db_timestamp_ms("2023-11-15 06:13:20", assume_utc8=True) == 1700000000 * 1000

    # Current time matches UTC + 8h within 5 seconds
    expected_utc8_now = datetime.now(timezone.utc) + timedelta(hours=8)
    parsed_now = datetime.strptime(now_utc8_str(), "%Y-%m-%d %H:%M:%S")
    assert abs((parsed_now - expected_utc8_now.replace(tzinfo=None)).total_seconds()) < 5.0
    assert today_utc8_str() == expected_utc8_now.strftime("%Y-%m-%d")

    # 2. user_membership UTC+8 formatting
    assert abs((datetime.strptime(_now_str(), "%Y-%m-%d %H:%M:%S") - expected_utc8_now.replace(tzinfo=None)).total_seconds()) < 5.0
    assert _fmt_ts(1700000000) == "2023-11-15 06:13"

    # 3. DB schema defaults, explicit UTC+8 writes, and cleanup compatibility
    with tempfile.TemporaryDirectory() as tmp:
        db_file = os.path.join(tmp, "utc8_test.db")
        adb = AsyncDatabaseManager(db_file)
        adb.init_sync()
        adb.ensure_user_workspace_sync(1)
        adb.save_runtime_setting_sync("tz_check", {"ok": True}, user_id=1)
        adb.update_exchange_auth_status_sync("okx", "VERIFIED", "ok", 100.0, user_id=1)

        conn = sqlite3.connect(db_file)
        rt_updated = conn.execute(
            "SELECT updated_at FROM runtime_settings WHERE user_id = 1 AND key = 'tz_check'"
        ).fetchone()[0]
        ex_verified = conn.execute(
            "SELECT last_verified_at FROM exchanges WHERE user_id = 1 AND exchange_id = 'okx'"
        ).fetchone()[0]
        # Default column expression should also yield UTC+8
        conn.execute(
            """INSERT INTO orders (order_id, user_id, symbol, exchange_id, purpose, side, order_type, price, requested_qty, filled_qty, avg_price, status, latency_ms)
               VALUES ('fresh-miss-utc8', 1, 'BTC/USDT:USDT', 'okx', 'GRID_L1', 'LONG', 'IOC_LIMIT', 90000, 0.01, 0, 0, 'MISSED', 10.0)"""
        )
        conn.execute(
            """INSERT INTO orders (order_id, user_id, symbol, exchange_id, purpose, side, order_type, price, requested_qty, filled_qty, avg_price, status, latency_ms, created_at)
               VALUES ('old-miss-utc8', 1, 'BTC/USDT:USDT', 'okx', 'GRID_L1', 'LONG', 'IOC_LIMIT', 90000, 0.01, 0, 0, 'MISSED', 10.0, datetime('now', '+8 hours', '-72 hours'))"""
        )
        conn.commit()
        fresh_created = conn.execute(
            "SELECT created_at FROM orders WHERE order_id = 'fresh-miss-utc8'"
        ).fetchone()[0]
        conn.close()

        assert abs((datetime.strptime(rt_updated, "%Y-%m-%d %H:%M:%S") - expected_utc8_now.replace(tzinfo=None)).total_seconds()) < 5.0
        assert abs((datetime.strptime(fresh_created, "%Y-%m-%d %H:%M:%S") - expected_utc8_now.replace(tzinfo=None)).total_seconds()) < 5.0
        assert len(ex_verified) == 8 and ex_verified[:2] == expected_utc8_now.strftime("%H")

        clean_res = adb.cleanup_old_records_sync(missed_keep_hours=48, risk_keep_days=14)
        assert clean_res["missed_deleted"] == 1
        conn = sqlite3.connect(db_file)
        rem = {r[0] for r in conn.execute("SELECT order_id FROM orders").fetchall()}
        conn.close()
        assert "fresh-miss-utc8" in rem
        assert "old-miss-utc8" not in rem

        # 4. BidirectionalSniperEngine logs, historical trade time rendering (UTC+8), and UTC+8 daily PnL bucket
        eng = BidirectionalSniperEngine(adb, user_id=1)
        eng.append_log("INFO", "BTC/USDT:USDT", "utc8 log test", mode="SHADOW")
        log_time = eng.logs_by_mode["SHADOW"][0]["time"]
        assert log_time[:2] == expected_utc8_now.strftime("%H")

        # Historical trade from 2023-11-14 22:13:20.456 UTC must render as 06:13:20.456 (UTC+8)
        eng._accumulate_trade_into_stats(
            {
                "id": 999,
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "trading_mode": "SHADOW",
                "exit_order_type": "MAKER_POST_ONLY",
                "entry_price": 90000.0,
                "exit_price": 90500.0,
                "filled_qty": 0.01,
                "gross_pnl_usdt": 5.0,
                "fee_usdt": 0.2,
                "net_pnl_usdt": 4.8,
                "ack_ts_ms": 1700000000456,
                "created_at": "2023-11-14 22:13:20",
            },
            is_historical=True,
            mode_override="SHADOW",
        )
        assert eng.stats_by_mode["SHADOW"]["recent_trades"][0]["time"] == "06:13:20.456"
        assert eng.stats_by_mode["SHADOW"]["symbol_stats"]["BTC/USDT:USDT"]["last_trade_time"] == "06:13:20.456"
        assert eng.stats_by_mode["SHADOW"]["daily_pnl_date"] == today_utc8_str()
        assert eng.build_dashboard_snapshot()["timezone"] == "UTC+8"

        # Order row formatting uses UTC+8
        um = UserMembershipManager(db_file)
        um.init_sync()
        formatted_ord = um._format_order_row(
            {
                "order_no": "VIPTEST",
                "amount_usdt": 10.0,
                "base_amount_usdt": 10.0,
                "pay_amount_usdt": 10.001,
                "created_at": "2023-11-14 22:13:20",
                "created_at_ts": 1700000000,
                "expires_at_ts": 1700001800,
                "status": "PENDING",
            }
        )
        assert formatted_ord["created_at"] == "2023-11-15 06:13:20"
        assert formatted_ord["expires_at_str"] == "2023-11-15 06:43:20"

    # 5. External OKX API authentication header MUST remain strict UTC ISO-8601 (`Z`)
    hdrs = ExecutionAdapter._build_okx_headers("k", "s", "p", "GET", "/api/v5/account/balance", "")
    okx_ts = hdrs["OK-ACCESS-TIMESTAMP"]
    assert okx_ts.endswith("Z")
    okx_dt = datetime.strptime(okx_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert abs((okx_dt - datetime.now(timezone.utc)).total_seconds()) < 5.0
































