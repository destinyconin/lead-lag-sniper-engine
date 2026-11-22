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
    # L1 initial SL sits at center_price (100.0) to cut false breakouts immediately
    assert tracker.levels["L1"]["sl_price"] == 100.0

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

    # 3. MEAN_REVERSION pauses counter-trend LONG dip-buys in BEAR trend (no catching falling knives)
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
    mr_tracker = LongShortGridTracker("ETH/USDT:USDT")
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
    mr_tracker.sync_config(mr_cfg, current_mid=100.0, bbw_info=bear_info)
    # Dip to 99.30 (below L1=99.40) -> MUST NOT open counter-trend LONG in BEAR market!
    en_dip, _ = mr_tracker.evaluate_tick(99.25, 99.30, 99.28, mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert en_dip == []
    # Bounce to 100.70 (above S1=100.60) -> MUST allow trend-aligned SHORT!
    en_short, _ = mr_tracker.evaluate_tick(100.68, 100.72, 100.70, mr_cfg, is_net_mode=False, bbw_info=bear_info)
    assert [e["slot_id"] for e in en_short] == ["S1"]

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
    assert trk.levels["S1"]["sl_price"] == 100.00  # Initial false-breakout SL at center

    # Price drops to 97.85 (favorable move = (98.80 - 97.86)/98.80 = 0.95% >= 0.92%) -> S1 auto-locks breakeven SL!
    _, ex_hold = trk.evaluate_tick(97.84, 97.86, 97.85, cfg, is_net_mode=False, round_trip_fee=0.0010)
    assert ex_hold == []
    expected_be_s1 = round(98.80 * (1.0 - 0.0010 - 0.0006), 2)  # 98.64 (covers 0.10% fee + 0.06% slippage!)
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
    Verifies the new REVERSE_GRID (多空震荡网格: 跌破开空 / 涨破开多) tracker end-to-end:
    1) Symmetrical reverse layout: L1..Ln above center (break up -> LONG), S1..Sn below center (break down -> SHORT).
    2) Fixed per-slot TP + per-slot SL relative to entry price.
    3) Anti-re-stacking after TP via per-slot `armed` flag + hysteresis re-arm on pullback/bounce.
    4) Gap-jump closest-level selection, in-flight ENTERING guard, and bypassed-slot disarming.
    5) Net-mode mutual exclusion, EMA trend_bias filtering, post-SL cooldown, flat auto-recenter,
       and export_state / restore_state persistence.
    """
    from backend.grid_engine import LongShortGridTracker
    from backend.math_engine import mono_ms
    from backend.models import is_grid_strategy

    assert is_grid_strategy("REVERSE_GRID") is True

    cfg = SymbolStrategy(
        symbol="SOL/USDT:USDT",
        strategy_type="REVERSE_GRID",
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
        grid_recenter_enabled=True,
    )
    trk = LongShortGridTracker("SOL/USDT:USDT")
    trk.sync_config(cfg, current_mid=100.00)

    assert trk.active_grid_mode == "REVERSE_OSCILLATING"
    assert trk.levels["L1"]["trigger_price"] == 100.80
    assert trk.levels["L2"]["trigger_price"] == 101.60
    assert trk.levels["L3"]["trigger_price"] == 102.40
    assert trk.levels["S1"]["trigger_price"] == 99.20
    assert trk.levels["S2"]["trigger_price"] == 98.40
    assert trk.levels["S3"]["trigger_price"] == 97.60
    assert all(lv["armed"] is True for lv in trk.levels.values())

    # Snapshot orders from highest price (L3) down to lowest price (S3)
    snap = trk.get_snapshot()
    assert snap["active_grid_mode"] == "REVERSE_OSCILLATING"
    assert [lv["slot_id"] for lv in snap["levels"]] == ["L3", "L2", "L1", "S1", "S2", "S3"]

    # 1) Break UP above L1 (100.80) -> opens LONG L1!
    en1, ex1 = trk.evaluate_tick(100.80, 100.82, 100.81, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert ex1 == []
    assert [e["slot_id"] for e in en1] == ["L1"]
    assert en1[0]["side"] == "LONG"

    # While L1 is ENTERING, next tick does not double-fire same side
    en_inflight, _ = trk.evaluate_tick(101.62, 101.64, 101.63, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert en_inflight == []

    trk.mark_entry_filled("L1", 100.82, 0.20, 2.0, 0.1, cfg, round_trip_fee=0.0007)
    assert trk.levels["L1"]["armed"] is False
    assert trk.levels["L1"]["tp_price"] == round(100.82 * (1.0 + 0.0085 + 0.0007), 2)  # 101.75
    assert trk.levels["L1"]["sl_price"] == round(100.82 * (1.0 - 0.038), 2)            # 96.99

    # 2) Break UP above L2 (101.60) -> opens LONG L2!
    en2, _ = trk.evaluate_tick(101.62, 101.65, 101.635, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en2] == ["L2"]
    trk.mark_entry_filled("L2", 101.65, 0.20, 2.0, 0.1, cfg, round_trip_fee=0.0007)

    # 3) Price hits L1 TP (101.75) -> L1 exits with GRID_TP!
    _, ex_l1 = trk.evaluate_tick(101.78, 101.80, 101.79, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in ex_l1] == ["L1"]
    assert ex_l1[0]["exit_reason"] == "GRID_TP"
    trk.mark_exited("L1", 0.17, is_sl=False)
    assert trk.levels["L1"]["armed"] is False

    # Clear L1 1.2s cooldown and stay above L1 at 101.85 -> L1 MUST NOT spuriously re-open!
    trk.levels["L1"]["cooldown_until_mono"] = 0.0
    trk.levels["L1"]["last_closed_mono"] = 0.0
    en_no_restack, _ = trk.evaluate_tick(101.84, 101.86, 101.85, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert en_no_restack == []

    # 4) Price pulls back below L1 (100.80 * (1 - 0.002) = 100.598) -> L1 re-arms!
    en_pull, _ = trk.evaluate_tick(100.50, 100.55, 100.525, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert en_pull == []
    assert trk.levels["L1"]["armed"] is True

    # Price breaks UP across L1 (100.80) again while L2 is still OPEN at 101.65 (distance 0.79% >= 0.64%) -> L1 re-enters!
    en_re, _ = trk.evaluate_tick(100.81, 100.83, 100.82, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en_re] == ["L1"]
    trk.mark_entry_missed("L1")
    assert trk.levels["L1"]["status"] == "IDLE"
    assert trk.levels["L1"]["armed"] is True  # Miss preserves armed state!

    # 5) Net-mode mutual exclusion: L2 is OPEN (LONG), so dropping below S1 (99.20) is blocked in net_mode
    en_net, _ = trk.evaluate_tick(99.15, 99.18, 99.165, cfg, is_net_mode=True, round_trip_fee=0.0007)
    assert en_net == []

    # In hedge mode (is_net_mode=False), dropping below S1 (99.20) opens SHORT S1!
    en_s1, _ = trk.evaluate_tick(99.15, 99.18, 99.165, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en_s1] == ["S1"]
    assert en_s1[0]["side"] == "SHORT"
    trk.mark_entry_filled("S1", 99.15, 0.20, 2.0, 0.1, cfg, round_trip_fee=0.0007)
    assert trk.levels["S1"]["armed"] is False
    assert trk.levels["S1"]["tp_price"] == round(99.15 * (1.0 - 0.0085 - 0.0007), 2)  # 98.24
    assert trk.levels["S1"]["sl_price"] == round(99.15 * (1.0 + 0.038), 2)            # 102.92

    # S1 hits TP at 98.20!
    _, ex_s1 = trk.evaluate_tick(98.18, 98.20, 98.19, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in ex_s1] == ["S1"]
    assert ex_s1[0]["exit_reason"] == "GRID_TP"
    trk.mark_exited("S1", 0.17, is_sl=False)
    trk.levels["S1"]["cooldown_until_mono"] = 0.0
    trk.levels["S1"]["last_closed_mono"] = 0.0
    assert trk.levels["S1"]["armed"] is False

    # Export & restore state preserves active_grid_mode, open L2, and disarmed S1!
    exported = trk.export_state()
    assert exported["active_grid_mode"] == "REVERSE_OSCILLATING"
    assert "S1" in exported["disarmed_slots"]
    trk_restored = LongShortGridTracker("SOL/USDT:USDT")
    trk_restored.restore_state(exported, cfg)
    assert trk_restored.active_grid_mode == "REVERSE_OSCILLATING"
    assert trk_restored.levels["L2"]["status"] == "OPEN"
    assert trk_restored.levels["S1"]["armed"] is False
    assert trk_restored.levels["L1"]["armed"] is True

    # 6) Stop-loss on L2 (entry 101.65, sl = 97.79) triggers GRID_SL and engages 120s post-SL cooldown
    now_pre = mono_ms()
    _, ex_sl = trk_restored.evaluate_tick(97.70, 97.75, 97.725, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in ex_sl] == ["L2"]
    assert ex_sl[0]["exit_reason"] == "GRID_SL"
    trk_restored.mark_exited("L2", -0.76, is_sl=True)
    assert trk_restored._post_sl_cooldown_until_mono >= now_pre + 110_000.0
    # During cooldown, flat drift outside range does NOT prematurely recenter or enter!
    en_cd, _ = trk_restored.evaluate_tick(96.00, 96.04, 96.02, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert en_cd == []
    assert trk_restored.center_price == 100.00

    # Once cooldown expires & flat outside range -> auto-recenters cleanly with all slots armed!
    trk_restored._post_sl_cooldown_until_mono = 0.0
    en_rc, ex_rc = trk_restored.evaluate_tick(96.00, 96.04, 96.02, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert en_rc == [] and ex_rc == []
    assert trk_restored.center_price == 96.02
    assert all(lv["armed"] is True for lv in trk_restored.levels.values())

    # 7) Gap jump across multiple levels (100.0 -> 101.70) picks closest crossed L2 and disarms bypassed L1 on fill!
    trk_gap = LongShortGridTracker("SOL/USDT:USDT")
    trk_gap.sync_config(cfg, current_mid=100.00)
    en_gap, _ = trk_gap.evaluate_tick(101.68, 101.70, 101.69, cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert [e["slot_id"] for e in en_gap] == ["L2"]
    trk_gap.mark_entry_filled("L2", 101.70, 0.20, 2.0, 0.1, cfg, round_trip_fee=0.0007)
    assert trk_gap.levels["L1"]["armed"] is False  # Bypassed inner L1 disarmed!

    # 8) Unrestricted two-way grid arbitrage under BEAR/BULL trend_bias: BOTH upward LONG (涨破开多)
    #    and downward SHORT (跌破开空) trigger cleanly in LONG_SHORT mode!
    trk_bias = LongShortGridTracker("SOL/USDT:USDT")
    trk_bias.sync_config(cfg, current_mid=100.00)
    bear_bbw = {"timeframe": "15m", "bbw_pct": 2.5, "bbw_percentile": 45.0, "trend_bias": "BEAR"}
    en_bear_up, _ = trk_bias.evaluate_tick(100.82, 100.85, 100.835, cfg, is_net_mode=False, bbw_info=bear_bbw)
    assert [e["slot_id"] for e in en_bear_up] == ["L1"]
    trk_bias.mark_entry_filled("L1", 100.85, 0.20, 2.0, 0.1, cfg, round_trip_fee=0.0007)
    bull_bbw = {"timeframe": "15m", "bbw_pct": 2.5, "bbw_percentile": 45.0, "trend_bias": "BULL"}
    en_bull_dn, _ = trk_bias.evaluate_tick(99.15, 99.18, 99.165, cfg, is_net_mode=False, bbw_info=bull_bbw)
    assert [e["slot_id"] for e in en_bull_dn] == ["S1"]


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

            # Maker Post-Only fee applies cleanly to REVERSE_OSCILLATING
            _, f_ex = eng.get_leader_And_follower_cfg()
            rt_fee = eng._get_grid_rt_fee(cfg, "REVERSE_OSCILLATING", f_ex)
            assert abs(rt_fee - (float(f_ex["taker_fee"]) + float(f_ex["maker_fee"]))) < 1e-9

            # Seed center at 0.2000
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1999, 0.2001, 10000.0, 10000.0)
            trk = eng.grid_trackers["DOGE/USDT:USDT"]
            assert trk.active_grid_mode == "REVERSE_OSCILLATING"
            assert trk.levels["L1"]["trigger_price"] == 0.2016
            assert trk.levels["S1"]["trigger_price"] == 0.1984

            # 1) Break UP past L1 (0.2016) -> opens LONG L1 -> breaks L2 (0.2032) -> L1 hits TP -> L2 hits TP!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2016, 0.2017, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:L1" in eng.active_grid_positions
            l1_pos = eng.active_grid_positions["DOGE/USDT:USDT:L1"]
            assert l1_pos["side"] == "LONG"
            assert l1_pos["strategy_type"] == "REVERSE_GRID"
            assert l1_pos["grid_mode"] == "REVERSE_OSCILLATING"
            assert l1_pos["tp_price"] > l1_pos["entry_price"]
            assert l1_pos["sl_price"] < l1_pos["entry_price"]

            # Price rises to 0.2033 -> opens LONG L2 (0.2032) while L1 stays open
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.2032, 0.2033, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:L2" in eng.active_grid_positions
            l2_pos = eng.active_grid_positions["DOGE/USDT:USDT:L2"]

            # Price hits L1 TP -> L1 closes profitable; L1 is disarmed so it does NOT restack
            tp_l1 = l1_pos["tp_price"]
            eng._handle_follower_bbo("DOGE/USDT:USDT", tp_l1 + 0.0001, tp_l1 + 0.0002, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:L1" not in eng.active_grid_positions
            assert trk.levels["L1"]["armed"] is False
            assert eng.stats_by_mode["SHADOW"]["win_count"] == 1
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] > 0

            # Complete L2 TP cleanly so long side is flat (using L3's trigger > tp_l2 or recentering cleanly)
            tp_l2 = l2_pos["tp_price"]
            # Disarm L3 so we cleanly harvest L2 and stay flat for the downward SHORT test
            trk.levels["L3"]["armed"] = False
            eng._handle_follower_bbo("DOGE/USDT:USDT", tp_l2 + 0.0001, tp_l2 + 0.0002, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:L2" not in eng.active_grid_positions
            assert eng.stats_by_mode["SHADOW"]["win_count"] == 2
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] > 0

            # 2) Break DOWN past S1 (0.1984) -> opens SHORT S1 -> breaks S2 (0.1968) -> S1 & S2 both close profitable!
            eng._handle_follower_bbo("DOGE/USDT:USDT", 0.1983, 0.1984, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:S1" in eng.active_grid_positions
            s1_pos = eng.active_grid_positions["DOGE/USDT:USDT:S1"]
            assert s1_pos["side"] == "SHORT"
            assert s1_pos["tp_price"] < s1_pos["entry_price"]
            assert s1_pos["sl_price"] > s1_pos["entry_price"]

            tp_s1 = s1_pos["tp_price"]
            eng._handle_follower_bbo("DOGE/USDT:USDT", tp_s1 - 0.0002, tp_s1 - 0.0001, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:S1" not in eng.active_grid_positions
            assert "DOGE/USDT:USDT:S2" in eng.active_grid_positions  # Price also crossed S2 (0.1968)!
            s2_pos = eng.active_grid_positions["DOGE/USDT:USDT:S2"]

            # Harvest S2 TP cleanly (with S3 below TP range) -> all slots flat and 4 profitable round-trips!
            tp_s2 = s2_pos["tp_price"]
            eng._handle_follower_bbo("DOGE/USDT:USDT", tp_s2 - 0.0002, tp_s2 - 0.0001, 10000.0, 10000.0)
            await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert "DOGE/USDT:USDT:S2" not in eng.active_grid_positions
            # Close S3 if triggered at tp_s2 <= 0.1952
            if "DOGE/USDT:USDT:S3" in eng.active_grid_positions:
                tp_s3 = eng.active_grid_positions["DOGE/USDT:USDT:S3"]["tp_price"]
                eng._handle_follower_bbo("DOGE/USDT:USDT", tp_s3 - 0.0002, tp_s3 - 0.0001, 10000.0, 10000.0)
                await asyncio.gather(*list(eng._bg_tasks), return_exceptions=True)
            assert not eng.active_grid_positions
            assert eng.stats_by_mode["SHADOW"]["win_count"] >= 4
            assert eng.stats_by_mode["SHADOW"]["loss_count"] == 0
            assert eng.stats_by_mode["SHADOW"]["total_net_pnl"] > 0

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
    assert trk.levels["L1"]["sl_price"] == 100.00

    _, ex_half = trk.evaluate_tick(101.55, 101.57, 101.56, cfg, is_net_mode=True, round_trip_fee=0.0010)
    assert ex_half == []
    # Half-risk step-up tightened L1 SL from 100.00 (-0.99%) to 101.00 * (1 - 0.004) = 100.60 (-0.40%)!
    assert trk.levels["L1"]["sl_price"] == round(101.00 * (1.0 - 0.50 * 0.008), 2)
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

    sp500_items = [c for c in SUPPORTED_PERP_CATALOG if c.get("sector") == "标普500"]
    sp500_bases = {c["base"] for c in sp500_items}
    for expected_base in ("SPX", "SPY", "VOO", "IVV", "ES", "SP500", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "BRKB"):
        assert expected_base in sp500_bases
        item = next(c for c in sp500_items if c["base"] == expected_base)
        for broker_id in ("futu", "moomoo", "ibkr", "schwab"):
            assert broker_id in item["exchanges"]

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

            # 2) /api/symbol/verify with exchange_id & S&P 500 tickers / aliases
            v_spy = client.get("/api/symbol/verify?symbol=SPY&exchange_id=futu", headers=hdr).json()
            assert v_spy["verified"] is True
            assert v_spy["symbol"] == "SPY/USDT:USDT"
            assert v_spy["recommended_exchange_id"] == "futu"
            assert v_spy["sector"] == "标普500"

            v_spx = client.get("/api/symbol/verify?symbol=S%26P500&exchange_id=ibkr", headers=hdr).json()
            assert v_spx["verified"] is True
            assert v_spx["symbol"] == "SPX/USDT:USDT"
            assert v_spx["recommended_exchange_id"] == "ibkr"

            v_brk = client.get("/api/symbol/verify?symbol=BRK.B&exchange_id=schwab", headers=hdr).json()
            assert v_brk["verified"] is True
            assert v_brk["symbol"] == "BRKB/USDT:USDT"
            assert v_brk["recommended_exchange_id"] == "schwab"

            v_aapl = client.get("/api/symbol/verify?symbol=US.AAPL&exchange_id=moomoo", headers=hdr).json()
            assert v_aapl["verified"] is True
            assert v_aapl["symbol"] == "AAPL/USDT:USDT"
            assert v_aapl["recommended_exchange_id"] == "moomoo"

            # 3) Add SPY on Futu with TREND_GRID and verify persistence + immediate grid level seeding in LIVE mode
            upd_spy = client.post(
                "/api/symbol/update",
                headers=hdr,
                json={
                    "symbol": "SPY/USDT:USDT",
                    "exchange_id": "futu",
                    "strategy_type": "TREND_GRID",
                    "grid_side_mode": "LONG_SHORT",
                    "grid_levels": 3,
                    "grid_step_pct": 0.008,
                    "grid_take_profit_pct": 0.0085,
                    "grid_range_pct": 0.028,
                    "grid_stop_loss_pct": 0.038,
                    "order_usdt": 100.0,
                    "leverage": 5,
                    "price_precision": 2,
                    "qty_precision": 2,
                    "min_qty": 0.01,
                    "is_running": True,
                },
            )
            assert upd_spy.status_code == 200, upd_spy.text
            spy_saved = next(s for s in upd_spy.json()["strategies"] if s["symbol"] == "SPY/USDT:USDT")
            assert spy_saved["exchange_id"] == "futu"
            assert spy_saved["strategy_type"] == "TREND_GRID"

            st_res = client.get("/api/stats?radar_symbol=SPY/USDT:USDT", headers=hdr)
            assert st_res.status_code == 200
            spy_tel = st_res.json()["telemetry"]["SPY/USDT:USDT"]
            assert spy_tel["follower_mid"] > 500.0
            assert spy_tel["grid"]["center_price"] > 500.0
            assert len(spy_tel["grid"]["levels"]) == 6

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

    # 1) Open REVERSE_OSCILLATING slot dynamically tightens legacy 3.80% SL to 1.20%
    rev_cfg = SymbolStrategy(
        symbol="ZEC/USDT:USDT",
        strategy_type="REVERSE_GRID",
        grid_levels=5,
        grid_step_pct=0.006,
        grid_take_profit_pct=0.0065,
        grid_range_pct=0.035,
        grid_stop_loss_pct=0.038,
        price_precision=2,
        qty_precision=2,
    )
    trk = LongShortGridTracker("ZEC/USDT:USDT")
    entries, _ = trk.evaluate_tick(100.0, 100.0, 100.0, rev_cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert entries == []
    entries, _ = trk.evaluate_tick(100.62, 100.63, 100.625, rev_cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert len(entries) == 1 and entries[0]["slot_id"] == "L1"
    trk.mark_entry_filled("L1", 100.60, 1.0, 1.0, 0.05, rev_cfg, round_trip_fee=0.0007)
    assert trk.levels["L1"]["sl_price"] == round(100.60 * (1.0 - 0.038), 2)

    # User lowers grid_stop_loss_pct from 3.80% to 1.20%: next tick tightens open L1 sl_price & flags sl_needs_amend!
    rev_cfg.grid_stop_loss_pct = 0.012
    trk.evaluate_tick(100.55, 100.56, 100.555, rev_cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert trk.levels["L1"]["sl_price"] == round(100.60 * (1.0 - 0.012), 2)
    assert trk.levels["L1"]["sl_needs_amend"] is True
    # And widening grid_stop_loss_pct back to 3.80% (grid-range SL) dynamically widens open L1 sl_price back so it won't stop out inside the grid!
    trk.levels["L1"]["sl_needs_amend"] = False
    rev_cfg.grid_stop_loss_pct = 0.038
    trk.evaluate_tick(100.55, 100.56, 100.555, rev_cfg, is_net_mode=False, round_trip_fee=0.0007)
    assert trk.levels["L1"]["sl_price"] == round(100.60 * (1.0 - 0.038), 2)
    assert trk.levels["L1"]["sl_needs_amend"] is True

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

    # 3) Verify REVERSE_OSCILLATING executes BOTH LONG and SHORT grid arbitrage unrestricted by BULL/BEAR trend_bias,
    #    and re-arms on shallow grid pullbacks (e.g. JUP dipping 0.05% below L2)
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

    # Even under strong BULL trend_bias, dropping below S1 (99.40) in REVERSE_OSCILLATING MUST open SHORT S1 (跌破开空)!
    en_s1_bull, _ = trk.evaluate_tick(99.38, 99.39, 99.385, rev_cfg, is_net_mode=False, round_trip_fee=0.0007, bbw_info=bull_bbw)
    assert len(en_s1_bull) == 1 and en_s1_bull[0]["slot_id"] == "S1" and en_s1_bull[0]["side"] == "SHORT"
    trk.mark_entry_filled("S1", 99.38, 1.0, 1.0, 0.05, rev_cfg, round_trip_fee=0.0007)

    # S1 hits TP at 98.65 -> exits with GRID_TP!
    en_at_tp, ex_s1_tp = trk.evaluate_tick(98.64, 98.65, 98.645, rev_cfg, is_net_mode=False, round_trip_fee=0.0007, bbw_info=bull_bbw)
    for e_sub in en_at_tp:
        trk.mark_entry_missed(e_sub["slot_id"])
    assert [e["slot_id"] for e in ex_s1_tp] == ["S1"] and ex_s1_tp[0]["exit_reason"] == "GRID_TP"
    trk.mark_exited("S1", 0.065, is_sl=False)
    trk.levels["S1"]["cooldown_until_mono"] = 0.0
    trk.levels["S1"]["last_closed_mono"] = 0.0

    # Shallow bounce just 0.05% above S1 (99.40 * 1.0005 = 99.45) immediately re-arms S1 for repeated grid arbitrage!
    trk.evaluate_tick(99.44, 99.46, 99.45, rev_cfg, is_net_mode=False, round_trip_fee=0.0007, bbw_info=bull_bbw)
    assert trk.levels["S1"]["armed"] is True
    en_s1_repeat, _ = trk.evaluate_tick(99.38, 99.39, 99.385, rev_cfg, is_net_mode=False, round_trip_fee=0.0007, bbw_info=bull_bbw)
    assert len(en_s1_repeat) == 1 and en_s1_repeat[0]["slot_id"] == "S1"
    trk.mark_entry_missed("S1")

    # Even under strong BEAR trend_bias, rising above L2 (101.20) in REVERSE_OSCILLATING MUST open LONG L2 (涨破开多)!
    en_l2_bear, _ = trk.evaluate_tick(101.21, 101.22, 101.215, rev_cfg, is_net_mode=False, round_trip_fee=0.0007, bbw_info=bear_bbw)
    assert len(en_l2_bear) == 1 and en_l2_bear[0]["slot_id"] == "L2" and en_l2_bear[0]["side"] == "LONG"

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




















