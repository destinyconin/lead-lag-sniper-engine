import os
import tempfile
import time
from backend.models import AdminUpdateUserRequest
from backend.payment_monitor import (
    PAYMENT_WALLET_ADDRESS,
    SUPPORTED_CHAIN_NAMES,
    _matches_exact_3dec,
    generate_wallet_qr_data_uri,
)
from backend.user_membership import UserMembershipManager


def test_security_cleanup_and_token_invalidation():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_users.db")
        mgr = UserMembershipManager(db_path=db_file)
        mgr.init_sync()

        users = mgr.admin_list_users()
        usernames = {u["username"] for u in users}
        # Demo users must not exist
        assert "vip_user" not in usernames
        assert "reg_user" not in usernames
        assert "admin" in usernames

        # Sample public VIP codes must not exist
        codes = mgr.admin_list_vip_codes()
        assert len(codes) == 0

        # Register a new user and verify token invalidation upon password change
        reg = mgr.register_user("alice_quant", "secret123", "alice@quant.io", client_ip="203.0.113.88")
        assert reg["ok"] is True
        assert reg["user"]["register_ip"] == "203.0.113.88"
        old_token = reg["token"]
        uid = reg["user"]["id"]

        # Second registration from the SAME IP must be rejected
        dup_ip_reg = mgr.register_user("bob_sybil", "secret123", "bob@quant.io", client_ip="203.0.113.88")
        assert dup_ip_reg["ok"] is False
        assert "每个 IP 仅允许注册一个账户" in dup_ip_reg["message"]

        # Registration from a DIFFERENT IP must succeed
        other_ip_reg = mgr.register_user("charlie_quant", "secret123", "c@quant.io", client_ip="198.51.100.42")
        assert other_ip_reg["ok"] is True

        assert mgr.get_user_from_token(old_token) is not None

        # Change password -> old token must be immediately invalidated
        chg = mgr.change_password(uid, "secret123", "newsecret456")
        assert chg["ok"] is True
        new_token = chg["token"]

        assert mgr.get_user_from_token(old_token) is None
        assert mgr.get_user_from_token(new_token) is not None

        # Admin protection: cannot delete, ban, or demote 'admin'
        admin_user = next(u for u in users if u["username"] == "admin")
        assert mgr.admin_delete_user(admin_user["id"])["ok"] is False
        assert (
            mgr.admin_update_user(
                AdminUpdateUserRequest(user_id=admin_user["id"], role="user")
            )["ok"]
            is False
        )
        assert (
            mgr.admin_update_user(
                AdminUpdateUserRequest(user_id=admin_user["id"], is_active=False)
            )["ok"]
            is False
        )


def test_web3_payment_3dec_amount_and_30min_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_pay.db")
        mgr = UserMembershipManager(db_path=db_file)
        mgr.init_sync()

        u1 = mgr.register_user("trader_one", "pass123456")["user"]
        u2 = mgr.register_user("trader_two", "pass123456")["user"]

        plans = mgr.list_plans()
        month_plan = next(p for p in plans if p["price_usdt"] == 99.0)

        # Create Web3 order for 99 USDT plan -> must be 98.xxx with non-zero 3rd decimal
        o1_res = mgr.create_web3_payment_order(u1["id"], month_plan["id"], "Arbitrum")
        assert o1_res["ok"] is True
        o1 = o1_res["order"]
        assert o1["status"] == "PENDING"
        assert o1["wallet_address"] == PAYMENT_WALLET_ADDRESS
        assert 98.101 <= o1["pay_amount_usdt"] <= 98.999
        assert int(round(o1["pay_amount_usdt"] * 1000)) % 10 != 0
        assert 1795 <= o1["remaining_seconds"] <= 1800

        # Second concurrent order for same 99 USDT plan must get a distinct 3-decimal amount
        o2_res = mgr.create_web3_payment_order(u2["id"], month_plan["id"], "Base")
        o2 = o2_res["order"]
        assert o2["pay_amount_usdt"] != o1["pay_amount_usdt"]

        # Exact 3-decimal matching check (6 decimals USDT/USDC and 18 decimals BSC USDT)
        target_amt = o1["pay_amount_usdt"]
        raw_6dec = int(round(target_amt * 1_000_000))
        raw_18dec = int(round(target_amt * 1_000) * (10**15))
        assert _matches_exact_3dec(raw_6dec, target_amt, [6]) is True
        assert _matches_exact_3dec(raw_18dec, target_amt, [18]) is True
        # Rounding to 99.000 or off by 0.001 must NOT match
        assert _matches_exact_3dec(99_000_000, target_amt, [6]) is False
        assert _matches_exact_3dec(raw_6dec + 1000, target_amt, [6]) is False

        # On-chain confirmation automatically activates VIP and prevents tx_hash reuse
        tx_hash = "0x" + "a1b2c3d4" * 8
        conf = mgr.confirm_order_paid_onchain(o1["id"], "Arbitrum", tx_hash, target_amt)
        assert conf["ok"] is True
        updated_u1 = mgr.get_user_by_id(u1["id"])
        assert updated_u1["is_vip"] is True
        assert updated_u1["can_use_live"] is True

        # Reusing the same tx_hash for another order must be rejected
        dup = mgr.confirm_order_paid_onchain(o2["id"], "Base", tx_hash, o2["pay_amount_usdt"])
        assert dup["ok"] is False

        # Verify QR code & 10 chains metadata
        meta = mgr.get_payment_metadata()
        assert meta["wallet_address"] == "0x8D550B8c462310126BA26d85A4a1f779Cd56A9e0"
        assert len(SUPPORTED_CHAIN_NAMES) == 10
        assert generate_wallet_qr_data_uri().startswith("data:image/svg+xml;base64,")
