"""
User Authentication, Membership Subscription, Web3 On-Chain Payment & Admin Management System.
Enforces strict tiered access rules:
1. Unauthenticated Guest: Cannot see the Live Trading system (Shadow Mode only).
2. Registered User (Non-VIP): Can see the Live Trading system & real-time market feed,
   but CANNOT use Live Trading execution or save/verify Live API keys until VIP is active.
3. Active VIP Member / Admin: Full access to view and use the Live Trading system.
4. Admin: Full access to User Management System & Membership Management System.
"""
import base64
from collections import deque
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
from backend.database import AsyncDatabaseManager, DB_PATH
from backend.models import (
    AdminCreateUserRequest,
    AdminGrantVipRequest,
    AdminMembershipPlanModel,
    AdminUpdateUserRequest,
)
from backend.payment_monitor import (
    ORDER_TIMEOUT_SECONDS,
    PAYMENT_WALLET_ADDRESS,
    PAYMENT_WARNING_TEXT,
    SUPPORTED_CHAIN_NAMES,
    generate_wallet_qr_data_uri,
)


TOKEN_SECRET = os.environ.get("SNIPER_AUTH_SECRET", "sniper-lead-lag-hmac-secret-key-2026-v2").encode("utf-8")

USER_MEMBERSHIP_SCHEMA = """
-- 7. 用户账户与会员状态表
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    email VARCHAR(128) DEFAULT '',
    password_hash TEXT NOT NULL,
    role VARCHAR(16) DEFAULT 'user',         -- 'user' 或 'admin'
    is_active BOOLEAN DEFAULT 1,             -- 1=正常, 0=封禁
    vip_tier VARCHAR(64) DEFAULT 'NONE',
    vip_expires_at INTEGER DEFAULT 0,        -- Unix秒级时间戳, > now 即为有效会员
    register_ip VARCHAR(64) DEFAULT '',      -- 注册绑定 IP (一IP一号限制)
    last_login_ip VARCHAR(64) DEFAULT '',    -- 最近登录 IP
    created_at TEXT DEFAULT '',
    last_login_at TEXT DEFAULT ''
);

-- 8. 会员套餐配置表
CREATE TABLE IF NOT EXISTS membership_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(64) NOT NULL,
    duration_days INTEGER NOT NULL,
    price_usdt DECIMAL(12, 2) NOT NULL,
    original_price_usdt DECIMAL(12, 2) NOT NULL,
    badge_text VARCHAR(32) DEFAULT '',
    features_json TEXT NOT NULL,
    is_enabled BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 1
);

-- 9. 会员购买订单与链上监控流水表
CREATE TABLE IF NOT EXISTS membership_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no VARCHAR(64) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    username VARCHAR(64) NOT NULL,
    plan_id INTEGER NOT NULL,
    plan_name VARCHAR(64) NOT NULL,
    duration_days INTEGER NOT NULL,
    amount_usdt DECIMAL(12, 3) NOT NULL,
    base_amount_usdt DECIMAL(12, 2) DEFAULT 0.0,
    pay_amount_usdt DECIMAL(12, 3) DEFAULT 0.0,
    pay_method VARCHAR(32) NOT NULL,         -- 'WEB3_ONCHAIN', 'REDEEM_CODE', 'ADMIN_GRANT'
    chain_network VARCHAR(64) DEFAULT 'MULTI_EVM',
    wallet_address VARCHAR(64) DEFAULT '',
    tx_hash VARCHAR(128) DEFAULT '',
    status VARCHAR(24) NOT NULL,             -- 'PAID', 'PENDING', 'EXPIRED', 'CANCELLED', 'REJECTED'
    created_at TEXT NOT NULL,
    paid_at TEXT DEFAULT '',
    created_at_ts INTEGER DEFAULT 0,
    expires_at_ts INTEGER DEFAULT 0
);

-- 10. 会员激活码/卡密表
CREATE TABLE IF NOT EXISTS vip_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(64) NOT NULL UNIQUE,
    plan_name VARCHAR(64) NOT NULL,
    duration_days INTEGER NOT NULL,
    status VARCHAR(16) DEFAULT 'UNUSED',     -- 'UNUSED', 'USED', 'DISABLED'
    used_by_username VARCHAR(64) DEFAULT '',
    used_at TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- 11. 用户问题留言与管理员回复表
CREATE TABLE IF NOT EXISTS user_feedback_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username VARCHAR(64) NOT NULL,
    category VARCHAR(32) DEFAULT '问题反馈',
    title VARCHAR(128) DEFAULT '',
    content TEXT NOT NULL,
    status VARCHAR(16) DEFAULT 'OPEN',       -- 'OPEN' (待回复), 'REPLIED' (已回复)
    admin_reply TEXT DEFAULT '',
    replied_by VARCHAR(64) DEFAULT '',
    replied_at TEXT DEFAULT '',
    replied_at_ts INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    created_at_ts INTEGER NOT NULL,
    user_deleted BOOLEAN DEFAULT 0,
    user_deleted_at INTEGER DEFAULT 0,
    admin_deleted BOOLEAN DEFAULT 0,
    admin_deleted_at INTEGER DEFAULT 0
);

-- 12. 已核销链上交易哈希防重放归档表（删除已支付订单时保留哈希记录）
CREATE TABLE IF NOT EXISTS consumed_tx_hashes (
    tx_hash VARCHAR(128) PRIMARY KEY,
    order_no VARCHAR(64) DEFAULT '',
    archived_at TEXT DEFAULT ''
);
"""

FEEDBACK_SUBMIT_COOLDOWN_SECONDS = 86400
FEEDBACK_USER_DELETE_MIN_AGE_SECONDS = 86400

DEFAULT_PLANS = [
    (
        "PLAN_MONTH",
        "月度专业版会员",
        30,
        99.0,
        149.0,
        "热门入门",
        json.dumps(
            [
                "解锁实盘毫秒级狙击通道使用权限",
                "支持 OKX / Binance 实盘 API 密钥绑定与验证",
                "支持最多 8 个永续合约并发监控与自定义参数",
                "内置 IOC 保留价报单与毫秒级持仓对账状态机",
            ],
            ensure_ascii=False,
        ),
        1,
        1,
    ),
    (
        "PLAN_QUARTER",
        "季度旗舰版会员",
        90,
        249.0,
        447.0,
        "超值推荐",
        json.dumps(
            [
                "包含月度专业版全部实盘狙击权限（立省 44%）",
                "解锁移动追踪止盈（Trailing TP）与主所反转逃生算子",
                "支持 54+ 全板块永续合约自由搜索添加与零延迟预热",
                "支持欧易永续合约自动张数换算与保证金安全保护",
            ],
            ensure_ascii=False,
        ),
        1,
        2,
    ),
    (
        "PLAN_YEAR",
        "年度机构版会员",
        365,
        799.0,
        1788.0,
        "机构专享",
        json.dumps(
            [
                "全年 365 天无限制使用实盘毫秒级高频狙击系统",
                "支持多交易所（OKX / Bybit / Gate / Bitget / Kraken）执行通道",
                "解锁自适应波动率 Z-Score 与滚动 OLS Beta 动态收敛校准",
                "专属高优风控熔断保护与全量实盘交易复盘导出",
            ],
            ensure_ascii=False,
        ),
        1,
        3,
    ),
    (
        "PLAN_LIFETIME",
        "终身至尊版会员",
        36500,
        1999.0,
        3999.0,
        "永久买断",
        json.dumps(
            [
                "一次开通，永久解锁实盘系统全部核心算子与自动交易权限",
                "无限制绑定与切换主从交易所实盘 API 通道",
                "永久享受后续算法升级、微观价格过滤与多所并发扩展",
                "最高优先级风控参数矩阵与独立实盘审计账本",
            ],
            ensure_ascii=False,
        ),
        1,
        4,
    ),
]


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_ts(ts: int) -> str:
    if not ts or ts <= 0:
        return "未开通"
    if ts > int(time.time()) + 3650 * 86400:
        return "永久有效"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _format_duration_cn(seconds: int) -> str:
    s = max(1, int(seconds))
    hours, rem = divmod(s, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}小时{mins}分" if mins > 0 else f"{hours}小时"
    if mins > 0:
        return f"{mins}分{secs}秒" if secs > 0 else f"{mins}分钟"
    return f"{secs}秒"


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    actual = hash_password(password, salt)
    return hmac.compare_digest(actual, stored_hash)


def _pwd_fingerprint(password_hash: str) -> str:
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:12]


def create_auth_token(user_id: int, username: str, role: str, password_hash: str = "") -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "role": role,
        "pfp": _pwd_fingerprint(password_hash),
        "exp": int(time.time()) + 14 * 86400,  # 14 days validity
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    sig = hmac.new(TOKEN_SECRET, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def decode_auth_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        b64_payload, sig = token.split(".", 1)
        expected_sig = hmac.new(TOKEN_SECRET, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        pad = "=" * (-len(b64_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64_payload + pad).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


class UserMembershipManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._default_admin_pwd_cached: Optional[bool] = None

    def _connect(self, isolation_level: Optional[str] = "") -> sqlite3.Connection:
        if isolation_level is None:
            conn = sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def init_sync(self):
        conn = self._connect()
        try:
            conn.executescript(USER_MEMBERSHIP_SCHEMA)
            cur = conn.cursor()
            try:
                cur.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS idx_paid_onchain_tx_hash
                       ON membership_orders(LOWER(tx_hash))
                       WHERE status = 'PAID' AND pay_method = 'WEB3_ONCHAIN' AND tx_hash != ''"""
                )
            except Exception:
                pass
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username))")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user_created ON user_feedback_messages(user_id, created_at_ts DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_admin_status ON user_feedback_messages(admin_deleted, status, created_at_ts DESC)")
            except Exception:
                pass

            # Safe schema migration for membership_orders new columns
            existing_cols = {
                row[1] for row in cur.execute("PRAGMA table_info(membership_orders)").fetchall()
            }
            migrations = [
                ("base_amount_usdt", "ALTER TABLE membership_orders ADD COLUMN base_amount_usdt DECIMAL(12, 2) DEFAULT 0.0"),
                ("pay_amount_usdt", "ALTER TABLE membership_orders ADD COLUMN pay_amount_usdt DECIMAL(12, 3) DEFAULT 0.0"),
                ("chain_network", "ALTER TABLE membership_orders ADD COLUMN chain_network VARCHAR(64) DEFAULT 'MULTI_EVM'"),
                ("wallet_address", "ALTER TABLE membership_orders ADD COLUMN wallet_address VARCHAR(64) DEFAULT ''"),
                ("created_at_ts", "ALTER TABLE membership_orders ADD COLUMN created_at_ts INTEGER DEFAULT 0"),
                ("expires_at_ts", "ALTER TABLE membership_orders ADD COLUMN expires_at_ts INTEGER DEFAULT 0"),
            ]
            for col_name, ddl in migrations:
                if col_name not in existing_cols:
                    cur.execute(ddl)

            # Safe schema migration for users IP tracking columns
            user_cols = {
                row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()
            }
            if "register_ip" not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN register_ip VARCHAR(64) DEFAULT ''")
            if "last_login_ip" not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN last_login_ip VARCHAR(64) DEFAULT ''")

            now_s = int(time.time())
            now_str = _now_str()

            # 1. Seed Default Membership Plans if empty
            cur.execute("SELECT COUNT(*) FROM membership_plans")
            if cur.fetchone()[0] == 0:
                for row in DEFAULT_PLANS:
                    cur.execute(
                        """INSERT INTO membership_plans
                           (plan_code, name, duration_days, price_usdt, original_price_usdt,
                            badge_text, features_json, is_enabled, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        row,
                    )

            # 2. Clean up insecure demo accounts, fake orders, and public sample VIP codes
            cur.execute("DELETE FROM users WHERE username IN ('vip_user', 'reg_user')")
            cur.execute("DELETE FROM membership_orders WHERE username IN ('vip_user', 'reg_user') OR order_no LIKE 'VIP%8801'")
            cur.execute("DELETE FROM vip_codes WHERE code IN ('VIP-30D-8888', 'VIP-90D-9999', 'VIP-365D-6666') AND status = 'UNUSED'")

            # 3. Ensure Default Admin User exists
            cur.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                       VALUES (?, ?, ?, 'admin', 1, '终身至尊版会员', ?, ?, ?)""",
                    ("admin", "admin@sniper-engine.io", hash_password("admin123456"), now_s + 36500 * 86400, now_str, now_str),
                )

            conn.commit()
        finally:
            conn.close()

    def _format_user_row(self, row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        d = dict(row)
        d.pop("password_hash", None)
        now_s = int(time.time())
        exp = int(d.get("vip_expires_at") or 0)
        role = d.get("role", "user")
        is_active = bool(d.get("is_active", 1))
        is_vip = (role == "admin") or (exp > now_s)
        remaining_days = max(0, int((exp - now_s) / 86400)) if exp > now_s else (36500 if role == "admin" else 0)

        if role == "admin":
            vip_status = "ACTIVE"
            vip_label = d.get("vip_tier") if d.get("vip_tier") and d.get("vip_tier") != "NONE" else "终身至尊版 (管理员)"
        elif exp > now_s:
            vip_status = "ACTIVE"
            vip_label = d.get("vip_tier") or "专业版会员"
        elif exp > 0 and exp <= now_s:
            vip_status = "EXPIRED"
            vip_label = f"{d.get('vip_tier') or '会员'} (已过期)"
        else:
            vip_status = "NONE"
            vip_label = "未开通会员"

        d["is_active"] = is_active
        d["is_admin"] = role == "admin"
        d["is_vip"] = is_vip
        d["vip_status"] = vip_status
        d["vip_label"] = vip_label
        d["vip_remaining_days"] = remaining_days
        d["vip_expires_str"] = "永久有效" if role == "admin" else _fmt_ts(exp)
        d["register_ip"] = d.get("register_ip") or ""
        d["last_login_ip"] = d.get("last_login_ip") or ""
        d["can_see_live"] = is_active
        d["can_use_live"] = is_active and is_vip
        return d

    def _format_order_row(self, row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        d = dict(row)
        now_s = int(time.time())
        amt = float(d.get("amount_usdt") or 0.0)
        base_amt = float(d.get("base_amount_usdt") or amt)
        pay_amt = float(d.get("pay_amount_usdt") or amt)
        exp_ts = int(d.get("expires_at_ts") or 0)
        created_ts = int(d.get("created_at_ts") or 0)

        status = d.get("status", "PENDING")
        if status == "PENDING" and exp_ts > 0 and exp_ts <= now_s:
            status = "EXPIRED"
            d["status"] = "EXPIRED"

        remaining_s = max(0, exp_ts - now_s) if status == "PENDING" and exp_ts > 0 else 0
        d["amount_usdt"] = round(pay_amt if pay_amt > 0 else amt, 3)
        d["base_amount_usdt"] = round(base_amt, 2)
        d["pay_amount_usdt"] = round(pay_amt if pay_amt > 0 else amt, 3)
        d["pay_amount_str"] = f"{d['pay_amount_usdt']:.3f}"
        d["base_amount_str"] = f"{d['base_amount_usdt']:.2f}"
        d["remaining_seconds"] = remaining_s
        d["timeout_seconds"] = ORDER_TIMEOUT_SECONDS
        d["expires_at_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp_ts)) if exp_ts > 0 else ""
        d["wallet_address"] = d.get("wallet_address") or PAYMENT_WALLET_ADDRESS
        d["chain_network"] = d.get("chain_network") or "MULTI_EVM"
        d["created_at_ts"] = created_ts
        d["expires_at_ts"] = exp_ts
        return d

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._format_user_row(row) if row else None
        finally:
            conn.close()

    def get_user_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        payload = decode_auth_token(token)
        if not payload:
            return None
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(payload["uid"]),)).fetchone()
            if not row or not bool(row["is_active"]):
                return None
            # Strictly enforce password fingerprint on every token (invalidates old tokens on password change)
            token_pfp = payload.get("pfp")
            expected_pfp = _pwd_fingerprint(row["password_hash"])
            if not token_pfp or not hmac.compare_digest(str(token_pfp), expected_pfp):
                return None
            return self._format_user_row(row)
        finally:
            conn.close()

    # ==================== Auth Endpoints Logic ====================
    def register_user(self, username: str, password: str, email: str = "", client_ip: str = "") -> Dict[str, Any]:
        uname = username.strip()
        if len(uname) < 3 or len(uname) > 32:
            return {"ok": False, "message": "用户名长度需在 3 ~ 32 个字符之间"}
        if not re.match(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$", uname):
            return {"ok": False, "message": "用户名仅支持字母、数字、中文、下划线或连字符"}
        if len(password) < 6:
            return {"ok": False, "message": "登录密码长度至少为 6 位"}
        clean_email = email.strip()
        if clean_email and ("@" not in clean_email or len(clean_email) > 120):
            return {"ok": False, "message": "请输入格式有效的邮箱地址"}
        clean_ip = (client_ip or "").strip()

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            # Enforce 1 account per IP address restriction
            if clean_ip:
                ip_row = cur.execute(
                    "SELECT username FROM users WHERE register_ip = ? LIMIT 1",
                    (clean_ip,),
                ).fetchone()
                if ip_row:
                    return {
                        "ok": False,
                        "message": f"当前 IP ({clean_ip}) 已注册过账号，系统限制每个 IP 仅允许注册一个账户，请直接登录已有账号",
                    }

            existing = cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone()
            if existing:
                return {"ok": False, "message": f"用户名 '{uname}' 已被注册，请更换其他用户名"}
            now_str = _now_str()
            pwd_hash = hash_password(password)
            cur.execute(
                """INSERT INTO users
                   (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, register_ip, last_login_ip, created_at, last_login_at)
                   VALUES (?, ?, ?, 'user', 1, 'NONE', 0, ?, ?, ?, ?)""",
                (uname, clean_email, pwd_hash, clean_ip, clean_ip, now_str, now_str),
            )
            uid = cur.lastrowid
            conn.commit()
            try:
                AsyncDatabaseManager(self.db_path).ensure_user_workspace_sync(int(uid))
            except Exception:
                pass
            user = self.get_user_by_id(uid)
            token = create_auth_token(uid, uname, "user", pwd_hash)
            return {
                "ok": True,
                "message": "注册成功！您已解锁「实盘通道」与独立账户配置空间，开通会员后即可启动 24h 实盘自动交易",
                "token": token,
                "user": user,
            }
        finally:
            conn.close()

    def login_user(self, username: str, password: str, client_ip: str = "") -> Dict[str, Any]:
        uname = username.strip()
        clean_ip = (client_ip or "").strip()
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone()
            if not row:
                return {"ok": False, "message": "账号不存在，请检查用户名或先注册账号"}
            if not verify_password(password, row["password_hash"]):
                return {"ok": False, "message": "登录密码错误，请重新输入"}
            if not bool(row["is_active"]):
                return {"ok": False, "message": "该账号已被管理员禁用，请联系后台管理员"}

            now_str = _now_str()
            if clean_ip:
                conn.execute(
                    """UPDATE users
                       SET last_login_at = ?,
                           last_login_ip = ?,
                           register_ip = CASE
                               WHEN (register_ip IS NULL OR register_ip = '') AND role != 'admin' THEN ?
                               ELSE register_ip
                           END
                       WHERE id = ?""",
                    (now_str, clean_ip, clean_ip, row["id"]),
                )
            else:
                conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_str, row["id"]))
            conn.commit()

            user = self.get_user_by_id(row["id"])
            token = create_auth_token(row["id"], row["username"], row["role"], row["password_hash"])
            return {
                "ok": True,
                "message": f"欢迎回来，{row['username']}！",
                "token": token,
                "user": user,
            }
        finally:
            conn.close()

    def change_password(self, user_id: int, old_password: str, new_password: str) -> Dict[str, Any]:
        if len(new_password) < 6:
            return {"ok": False, "message": "新密码长度至少为 6 位"}
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}
            if not verify_password(old_password, row["password_hash"]):
                return {"ok": False, "message": "当前原密码验证失败"}
            new_hash = hash_password(new_password)
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
            conn.commit()
            if row["username"] == "admin":
                self._default_admin_pwd_cached = None
            new_token = create_auth_token(row["id"], row["username"], row["role"], new_hash)
            return {
                "ok": True,
                "message": "登录密码修改成功，旧会话已自动失效",
                "token": new_token,
            }
        finally:
            conn.close()

    # ==================== Membership Plans, Web3 Orders & Redemption ====================
    def list_plans(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM membership_plans ORDER BY sort_order ASC, id ASC"
            if not include_disabled:
                sql = "SELECT * FROM membership_plans WHERE is_enabled = 1 ORDER BY sort_order ASC, id ASC"
            rows = conn.execute(sql).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["price_usdt"] = float(d.get("price_usdt") or 0.0)
                d["original_price_usdt"] = float(d.get("original_price_usdt") or 0.0)
                d["is_enabled"] = bool(d.get("is_enabled", 1))
                try:
                    d["features"] = json.loads(d.get("features_json") or "[]")
                except Exception:
                    d["features"] = []
                result.append(d)
            return result
        finally:
            conn.close()

    def _extend_user_vip_in_tx(self, cur: sqlite3.Cursor, user_id: int, add_days: int, tier_name: str) -> int:
        row = cur.execute("SELECT vip_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
        now_s = int(time.time())
        current_exp = int(row[0] or 0) if row else 0
        base_ts = max(now_s, current_exp)
        new_exp = base_ts + int(add_days) * 86400
        cur.execute(
            "UPDATE users SET vip_tier = ?, vip_expires_at = ? WHERE id = ?",
            (tier_name, new_exp, user_id),
        )
        return new_exp

    def _expire_overdue_orders_in_tx(self, cur: sqlite3.Cursor) -> int:
        now_s = int(time.time())
        cur.execute(
            """UPDATE membership_orders
               SET status = 'EXPIRED',
                   tx_hash = CASE
                       WHEN tx_hash = '' OR tx_hash LIKE '等待链上%' THEN '30分钟支付超时自动关闭'
                       ELSE tx_hash
                   END
               WHERE status = 'PENDING' AND expires_at_ts > 0 AND expires_at_ts <= ?""",
            (now_s,),
        )
        return cur.rowcount

    def expire_overdue_orders(self) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            count = self._expire_overdue_orders_in_tx(cur)
            if count > 0:
                conn.commit()
            return count
        finally:
            conn.close()

    def get_active_pending_orders(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            self._expire_overdue_orders_in_tx(cur)
            conn.commit()
            now_s = int(time.time())
            rows = cur.execute(
                """SELECT * FROM membership_orders
                   WHERE status = 'PENDING' AND (expires_at_ts = 0 OR expires_at_ts > ?)
                   ORDER BY id ASC""",
                (now_s,),
            ).fetchall()
            return [self._format_order_row(r) for r in rows]
        finally:
            conn.close()

    def is_tx_hash_already_used(self, tx_hash: str) -> bool:
        if not tx_hash:
            return False
        clean = tx_hash.strip()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM membership_orders WHERE LOWER(tx_hash) = LOWER(?) AND status = 'PAID' LIMIT 1",
                (clean,),
            ).fetchone()
            if row is not None:
                return True
            archived = conn.execute(
                "SELECT tx_hash FROM consumed_tx_hashes WHERE tx_hash = LOWER(?) LIMIT 1",
                (clean,),
            ).fetchone()
            return archived is not None
        finally:
            conn.close()

    def _generate_unique_3dec_amount(self, cur: sqlite3.Cursor, base_price: float) -> float:
        """
        Generates a unique 3-decimal amount for an order slightly <= base_price.
        For example: 99 -> 98.101..98.999 (excluding multiples of 0.010); 19.5 -> 19.101..19.499.
        """
        now_s = int(time.time())
        active_rows = cur.execute(
            "SELECT pay_amount_usdt, amount_usdt FROM membership_orders WHERE status = 'PENDING' AND expires_at_ts > ?",
            (now_s,),
        ).fetchall()
        used_mills = set()
        for r in active_rows:
            val = float(r[0] or r[1] or 0.0)
            used_mills.add(int(round(val * 1000)))

        total_base_mills = max(200, int(round(float(base_price) * 1000)))
        frac_mills = total_base_mills % 1000
        if frac_mills == 0:
            int_part = max(0, (total_base_mills // 1000) - 1)
            max_m = 999
        elif frac_mills >= 200:
            int_part = total_base_mills // 1000
            max_m = frac_mills - 1
        else:
            int_part = max(0, (total_base_mills // 1000) - 1)
            max_m = 999

        min_m = 101 if max_m >= 200 else 11
        candidates = [
            m for m in range(min_m, max_m + 1)
            if (m % 10 != 0) and (int_part * 1000 + m not in used_mills)
        ]
        if not candidates:
            candidates = [
                m for m in range(1, max_m + 1)
                if (int_part * 1000 + m not in used_mills)
            ]
        if not candidates:
            raise RuntimeError("当前套餐并发待支付订单已满，请 1 分钟后重试")
        chosen_millis = secrets.choice(candidates)
        return round(int_part + chosen_millis / 1000.0, 3)

    def create_web3_payment_order(
        self,
        user_id: int,
        plan_id: int,
        chain_network: str = "MULTI_EVM",
        force_new: bool = False,
    ) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            self._expire_overdue_orders_in_tx(cur)

            u_row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u_row or not bool(u_row["is_active"]):
                conn.rollback()
                return {"ok": False, "message": "用户不存在或已被禁用"}

            p_row = cur.execute("SELECT * FROM membership_plans WHERE id = ?", (plan_id,)).fetchone()
            if not p_row or not bool(p_row["is_enabled"]):
                conn.rollback()
                return {"ok": False, "message": "所选会员套餐不存在或已下架"}

            now_s = int(time.time())
            # Check if user already has an active PENDING order
            existing_pending = cur.execute(
                """SELECT * FROM membership_orders
                   WHERE user_id = ? AND status = 'PENDING' AND expires_at_ts > ?
                   ORDER BY id DESC LIMIT 1""",
                (user_id, now_s + 30),
            ).fetchone()

            if existing_pending and not force_new and int(existing_pending["plan_id"]) == int(plan_id):
                if chain_network and chain_network != existing_pending["chain_network"]:
                    cur.execute(
                        "UPDATE membership_orders SET chain_network = ? WHERE id = ? AND status = 'PENDING'",
                        (chain_network, existing_pending["id"]),
                    )
                    existing_pending = cur.execute(
                        "SELECT * FROM membership_orders WHERE id = ?", (existing_pending["id"],)
                    ).fetchone()
                conn.commit()
                formatted_order = self._format_order_row(existing_pending)
                return {
                    "ok": True,
                    "status": "PENDING",
                    "order_no": formatted_order["order_no"],
                    "order": formatted_order,
                    "payment_info": self.get_payment_metadata(),
                    "message": (
                        f"已为您载入进行中的支付订单 {formatted_order['order_no']}，"
                        f"请在剩余时间内精确转账 {formatted_order['pay_amount_str']} USDT/USDC"
                    ),
                    "user": self.get_user_by_id(user_id),
                }

            # Cancel any previous pending orders of this user so their 3-decimal slot is released
            cur.execute(
                """UPDATE membership_orders
                   SET status = 'CANCELLED', tx_hash = '用户重新发起新订单自动关闭'
                   WHERE user_id = ? AND status = 'PENDING'""",
                (user_id,),
            )

            base_price = float(p_row["price_usdt"])
            try:
                pay_amount = self._generate_unique_3dec_amount(cur, base_price)
            except RuntimeError as pool_err:
                conn.rollback()
                return {"ok": False, "message": str(pool_err)}
            now_str = _now_str()
            expires_ts = now_s + ORDER_TIMEOUT_SECONDS
            order_no = f"VIP{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(9000)+1000}"
            duration_days = int(p_row["duration_days"])
            plan_name = p_row["name"]

            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days,
                    amount_usdt, base_amount_usdt, pay_amount_usdt, pay_method,
                    chain_network, wallet_address, tx_hash, status, created_at, paid_at,
                    created_at_ts, expires_at_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'WEB3_ONCHAIN', ?, ?, ?, 'PENDING', ?, '', ?, ?)""",
                (
                    order_no,
                    user_id,
                    u_row["username"],
                    plan_id,
                    plan_name,
                    duration_days,
                    pay_amount,
                    base_price,
                    pay_amount,
                    chain_network or "MULTI_EVM",
                    PAYMENT_WALLET_ADDRESS,
                    f"等待链上精确到账 {pay_amount:.3f}",
                    now_str,
                    now_s,
                    expires_ts,
                ),
            )
            order_id = cur.lastrowid
            conn.commit()

            new_row = cur.execute("SELECT * FROM membership_orders WHERE id = ?", (order_id,)).fetchone()
            formatted_order = self._format_order_row(new_row)
            return {
                "ok": True,
                "status": "PENDING",
                "order_no": order_no,
                "order": formatted_order,
                "payment_info": self.get_payment_metadata(),
                "message": (
                    f"订单已创建！请在 30 分钟内向收款地址精确转账 {pay_amount:.3f} USDT/USDC "
                    f"（原价 ${base_price:.0f}），链上监控确认到账后将秒级自动开通【{plan_name}】"
                ),
                "user": self.get_user_by_id(user_id),
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def confirm_order_paid_onchain(
        self,
        order_id: int,
        chain_name: str,
        tx_hash: str,
        paid_amount: float,
    ) -> Dict[str, Any]:
        clean_tx = (tx_hash or "").strip()
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            o_row = cur.execute("SELECT * FROM membership_orders WHERE id = ?", (order_id,)).fetchone()
            if not o_row:
                conn.rollback()
                return {"ok": False, "message": "订单不存在"}
            if o_row["status"] == "PAID":
                conn.rollback()
                return {"ok": False, "message": "订单已完成支付"}
            if o_row["status"] != "PENDING":
                conn.rollback()
                return {"ok": False, "message": f"订单当前状态为 {o_row['status']}，不可确认支付"}

            # Prevent duplicate tx_hash reuse atomically inside BEGIN IMMEDIATE
            if clean_tx:
                dup = cur.execute(
                    "SELECT id FROM membership_orders WHERE LOWER(tx_hash) = LOWER(?) AND status = 'PAID' LIMIT 1",
                    (clean_tx,),
                ).fetchone()
                if not dup:
                    dup = cur.execute(
                        "SELECT tx_hash FROM consumed_tx_hashes WHERE tx_hash = LOWER(?) LIMIT 1",
                        (clean_tx,),
                    ).fetchone()
                if dup:
                    conn.rollback()
                    return {"ok": False, "message": "该交易哈希已被其他订单使用"}

            now_str = _now_str()
            try:
                cur.execute(
                    """UPDATE membership_orders
                       SET status = 'PAID',
                           paid_at = ?,
                           chain_network = ?,
                           tx_hash = ?
                       WHERE id = ? AND status = 'PENDING'""",
                    (now_str, chain_name, clean_tx, order_id),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                return {"ok": False, "message": "该交易哈希已被其他订单使用"}

            if cur.rowcount != 1:
                conn.rollback()
                return {"ok": False, "message": "订单状态已变更，请刷新确认"}

            self._extend_user_vip_in_tx(
                cur,
                int(o_row["user_id"]),
                int(o_row["duration_days"]),
                o_row["plan_name"],
            )
            conn.commit()
            return {
                "ok": True,
                "order_no": o_row["order_no"],
                "message": f"链上到账确认成功（{chain_name}: {paid_amount:.3f}）！已自动开通【{o_row['plan_name']}】",
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_order_by_no(self, user_id: int, order_no: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            self._expire_overdue_orders_in_tx(cur)
            conn.commit()
            row = cur.execute(
                "SELECT * FROM membership_orders WHERE order_no = ? AND user_id = ?",
                (order_no.strip(), user_id),
            ).fetchone()
            return self._format_order_row(row) if row else None
        finally:
            conn.close()

    def cancel_user_order(self, user_id: int, order_no: str) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT * FROM membership_orders WHERE order_no = ? AND user_id = ?",
                (order_no.strip(), user_id),
            ).fetchone()
            if not row:
                conn.rollback()
                return {"ok": False, "message": "订单不存在"}
            if row["status"] != "PENDING":
                conn.rollback()
                return {"ok": False, "message": "仅待支付状态的订单可以取消"}
            cur.execute(
                "UPDATE membership_orders SET status = 'CANCELLED', tx_hash = '用户主动取消订单' WHERE id = ? AND status = 'PENDING'",
                (row["id"],),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return {"ok": False, "message": "订单状态已更新，无法取消"}
            conn.commit()
            return {"ok": True, "message": f"订单 {order_no} 已取消，链上监控已停止"}
        finally:
            conn.close()

    def get_payment_metadata(self) -> Dict[str, Any]:
        return {
            "wallet_address": PAYMENT_WALLET_ADDRESS,
            "qr_svg_uri": generate_wallet_qr_data_uri(PAYMENT_WALLET_ADDRESS),
            "supported_chains": SUPPORTED_CHAIN_NAMES,
            "warning_text": PAYMENT_WARNING_TEXT,
            "timeout_minutes": 30,
            "timeout_seconds": ORDER_TIMEOUT_SECONDS,
        }

    def redeem_vip_code(self, user_id: int, code: str) -> Dict[str, Any]:
        clean_code = code.strip().upper()
        if not clean_code:
            return {"ok": False, "message": "请输入有效的会员激活码"}
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            u_row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u_row or not bool(u_row["is_active"]):
                conn.rollback()
                return {"ok": False, "message": "用户不存在或已被封禁"}

            c_row = cur.execute("SELECT * FROM vip_codes WHERE UPPER(code) = ?", (clean_code,)).fetchone()
            if not c_row:
                conn.rollback()
                return {"ok": False, "message": "激活码不存在，请检查输入是否正确"}
            if c_row["status"] == "USED":
                conn.rollback()
                return {"ok": False, "message": f"该激活码已被使用（使用时间: {c_row['used_at']}）"}
            if c_row["status"] != "UNUSED":
                conn.rollback()
                return {"ok": False, "message": "该激活码已失效或被禁用"}

            now_str = _now_str()
            now_s = int(time.time())
            duration_days = int(c_row["duration_days"])
            plan_name = c_row["plan_name"]

            cur.execute(
                "UPDATE vip_codes SET status = 'USED', used_by_username = ?, used_at = ? WHERE id = ? AND status = 'UNUSED'",
                (u_row["username"], now_str, c_row["id"]),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return {"ok": False, "message": "该激活码已被使用"}
            self._extend_user_vip_in_tx(cur, user_id, duration_days, plan_name)

            order_no = f"CODE{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900)+100}"
            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days,
                    amount_usdt, base_amount_usdt, pay_amount_usdt, pay_method,
                    chain_network, wallet_address, tx_hash, status, created_at, paid_at,
                    created_at_ts, expires_at_ts)
                   VALUES (?, ?, ?, 0, ?, ?, 0.0, 0.0, 0.0, 'REDEEM_CODE', 'VIP_CODE', '', ?, 'PAID', ?, ?, ?, ?)""",
                (order_no, user_id, u_row["username"], plan_name, duration_days, clean_code, now_str, now_str, now_s, now_s),
            )
            conn.commit()
            return {
                "ok": True,
                "message": f"激活码兑换成功！已为您开通【{plan_name}】（+{duration_days} 天），实盘交易系统已解锁！",
                "user": self.get_user_by_id(user_id),
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def list_user_orders(self, user_id: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            self._expire_overdue_orders_in_tx(cur)
            conn.commit()
            rows = cur.execute(
                "SELECT * FROM membership_orders WHERE user_id = ? ORDER BY id DESC LIMIT 50", (user_id,)
            ).fetchall()
            return [self._format_order_row(r) for r in rows]
        finally:
            conn.close()

    # ==================== Admin: User Management System ====================
    def admin_list_users(self, search: str = "") -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            if search.strip():
                q = f"%{search.strip()}%"
                rows = conn.execute(
                    "SELECT * FROM users WHERE username LIKE ? OR email LIKE ? OR vip_tier LIKE ? ORDER BY id ASC",
                    (q, q, q),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
            return [self._format_user_row(r) for r in rows]
        finally:
            conn.close()

    def admin_create_user(self, req: AdminCreateUserRequest) -> Dict[str, Any]:
        uname = req.username.strip()
        if len(uname) < 3 or not re.match(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$", uname):
            return {"ok": False, "message": "用户名格式不合法（需3位以上字母、数字、中文或下划线）"}
        if len(req.password) < 6:
            return {"ok": False, "message": "初始密码长度至少为 6 位"}
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            if cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone():
                return {"ok": False, "message": f"用户名 '{uname}' 已存在"}
            now_str = _now_str()
            now_s = int(time.time())
            exp = (now_s + req.vip_days * 86400) if req.vip_days > 0 else (now_s + 36500 * 86400 if req.role == "admin" else 0)
            tier = (
                req.vip_tier
                if (req.vip_days > 0 and req.vip_tier != "NONE")
                else ("终身至尊版会员" if req.role == "admin" else ("专业版会员" if req.vip_days > 0 else "NONE"))
            )
            cur.execute(
                """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '')""",
                (uname, req.email.strip(), hash_password(req.password), req.role, tier, exp, now_str),
            )
            uid = cur.lastrowid
            if req.vip_days > 0:
                order_no = f"ADM{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900)+100}"
                cur.execute(
                    """INSERT INTO membership_orders
                       (order_no, user_id, username, plan_id, plan_name, duration_days,
                        amount_usdt, base_amount_usdt, pay_amount_usdt, pay_method,
                        chain_network, wallet_address, tx_hash, status, created_at, paid_at,
                        created_at_ts, expires_at_ts)
                       VALUES (?, ?, ?, 0, ?, ?, 0.0, 0.0, 0.0, 'ADMIN_GRANT', 'ADMIN', '', '管理员新建用户赠送会员', 'PAID', ?, ?, ?, ?)""",
                    (order_no, uid, uname, tier, int(req.vip_days), now_str, now_str, now_s, now_s),
                )
            conn.commit()
            try:
                AsyncDatabaseManager(self.db_path).ensure_user_workspace_sync(int(uid))
            except Exception:
                pass
            return {"ok": True, "message": f"已成功创建用户 {uname}"}
        finally:
            conn.close()

    def admin_update_user(self, req: AdminUpdateUserRequest) -> Dict[str, Any]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM users WHERE id = ?", (req.user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}

            if row["username"] == "admin":
                if req.role is not None and req.role != "admin":
                    return {"ok": False, "message": "系统默认超级管理员账号不可降级"}
                if req.is_active is not None and not req.is_active:
                    return {"ok": False, "message": "系统默认超级管理员账号不可封禁"}

            email = req.email.strip() if req.email is not None else row["email"]
            role = req.role if req.role is not None else row["role"]
            is_active = (1 if req.is_active else 0) if req.is_active is not None else row["is_active"]
            if req.new_password is not None and req.new_password.strip() != "":
                if len(req.new_password.strip()) < 6:
                    return {"ok": False, "message": "重置的新密码长度至少为 6 位"}
                pwd_hash = hash_password(req.new_password.strip())
            else:
                pwd_hash = row["password_hash"]

            cur.execute(
                "UPDATE users SET email = ?, role = ?, is_active = ?, password_hash = ? WHERE id = ?",
                (email, role, is_active, pwd_hash, req.user_id),
            )
            conn.commit()
            if row["username"] == "admin" and req.new_password:
                self._default_admin_pwd_cached = None
            return {"ok": True, "message": f"用户 {row['username']} 信息已更新"}
        finally:
            conn.close()

    def admin_grant_vip(self, req: AdminGrantVipRequest) -> Dict[str, Any]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM users WHERE id = ?", (req.user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}

            now_str = _now_str()
            now_s = int(time.time())
            if req.revoke or req.add_days <= 0:
                if row["username"] == "admin":
                    return {"ok": False, "message": "系统默认超级管理员不可收回实盘权限"}
                cur.execute("UPDATE users SET vip_tier = 'NONE', vip_expires_at = 0 WHERE id = ?", (req.user_id,))
                conn.commit()
                return {"ok": True, "message": f"已取消用户 {row['username']} 的会员资格"}

            self._extend_user_vip_in_tx(cur, req.user_id, req.add_days, req.vip_tier or "专业版会员")
            order_no = f"ADM{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900)+100}"
            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days,
                    amount_usdt, base_amount_usdt, pay_amount_usdt, pay_method,
                    chain_network, wallet_address, tx_hash, status, created_at, paid_at,
                    created_at_ts, expires_at_ts)
                   VALUES (?, ?, ?, 0, ?, ?, 0.0, 0.0, 0.0, 'ADMIN_GRANT', 'ADMIN', '', '管理员后台手动开通/延期', 'PAID', ?, ?, ?, ?)""",
                (order_no, req.user_id, row["username"], req.vip_tier, req.add_days, now_str, now_str, now_s, now_s),
            )
            conn.commit()
            return {
                "ok": True,
                "message": f"已为用户 {row['username']} 开通/延期【{req.vip_tier}】 +{req.add_days} 天",
            }
        finally:
            conn.close()

    def admin_delete_user(self, user_id: int) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute("SELECT username, role FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"ok": False, "message": "用户不存在"}
            if row["username"] == "admin":
                conn.rollback()
                return {"ok": False, "message": "系统默认超级管理员账号不可删除"}
            cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
            # Purge all tenant credentials, strategies, and runtime state so ghost engines can never resurrect!
            existing_tables = {
                r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for tbl in ("exchanges", "symbol_configs", "runtime_settings", "positions", "orders", "trade_logs", "risk_events"):
                if tbl in existing_tables:
                    cur.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
            conn.commit()
            return {"ok": True, "message": f"已删除用户 {row['username']} 并清除其交易所密钥与策略配置"}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ==================== Admin: Membership Management System ====================
    def admin_upsert_plan(self, req: AdminMembershipPlanModel) -> Dict[str, Any]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            feat_json = json.dumps(req.features or [], ensure_ascii=False)
            try:
                if req.id:
                    cur.execute(
                        """UPDATE membership_plans
                           SET plan_code=?, name=?, duration_days=?, price_usdt=?, original_price_usdt=?,
                               badge_text=?, features_json=?, is_enabled=?, sort_order=?
                           WHERE id=?""",
                        (
                            req.plan_code.strip(),
                            req.name.strip(),
                            req.duration_days,
                            req.price_usdt,
                            req.original_price_usdt,
                            req.badge_text.strip(),
                            feat_json,
                            1 if req.is_enabled else 0,
                            req.sort_order,
                            req.id,
                        ),
                    )
                else:
                    cur.execute(
                        """INSERT INTO membership_plans
                           (plan_code, name, duration_days, price_usdt, original_price_usdt,
                            badge_text, features_json, is_enabled, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            req.plan_code.strip(),
                            req.name.strip(),
                            req.duration_days,
                            req.price_usdt,
                            req.original_price_usdt,
                            req.badge_text.strip(),
                            feat_json,
                            1 if req.is_enabled else 0,
                            req.sort_order,
                        ),
                    )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return {"ok": False, "message": f"套餐编码「{req.plan_code}」已存在，请更换"}
            return {"ok": True, "message": f"会员套餐【{req.name}】已保存生效"}
        finally:
            conn.close()

    def admin_delete_plan(self, plan_id: int) -> Dict[str, Any]:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM membership_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return {"ok": True, "message": "会员套餐已删除"}
        finally:
            conn.close()

    def admin_list_orders(self, status_filter: str = "") -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            self._expire_overdue_orders_in_tx(cur)
            conn.commit()
            if status_filter.strip():
                rows = cur.execute(
                    "SELECT * FROM membership_orders WHERE status = ? ORDER BY id DESC LIMIT 200",
                    (status_filter.strip(),),
                ).fetchall()
            else:
                rows = cur.execute("SELECT * FROM membership_orders ORDER BY id DESC LIMIT 200").fetchall()
            return [self._format_order_row(r) for r in rows]
        finally:
            conn.close()

    def admin_audit_order(self, order_id: int, action: str, tx_hash: Optional[str] = None) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            o_row = cur.execute("SELECT * FROM membership_orders WHERE id = ?", (order_id,)).fetchone()
            if not o_row:
                conn.rollback()
                return {"ok": False, "message": "订单不存在"}
            if o_row["status"] == "PAID":
                conn.rollback()
                return {"ok": False, "message": "该订单已处于已支付开通状态"}

            now_str = _now_str()
            if action == "APPROVE":
                note_tx = (tx_hash or "").strip() or o_row["tx_hash"] or "管理员后台手动确认收款"
                if note_tx.startswith("等待链上") or note_tx.startswith("30分钟"):
                    note_tx = "管理员后台手动核验通过"
                cur.execute(
                    "UPDATE membership_orders SET status = 'PAID', paid_at = ?, tx_hash = ? WHERE id = ? AND status != 'PAID'",
                    (now_str, note_tx, order_id),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return {"ok": False, "message": "该订单已处于已支付开通状态"}
                self._extend_user_vip_in_tx(cur, int(o_row["user_id"]), int(o_row["duration_days"]), o_row["plan_name"])
                conn.commit()
                return {
                    "ok": True,
                    "message": f"订单 {o_row['order_no']} 已审核通过，已为用户 {o_row['username']} 自动开通【{o_row['plan_name']}】",
                }
            elif action == "EXPIRE":
                cur.execute(
                    "UPDATE membership_orders SET status = 'EXPIRED', tx_hash = '管理员手动关闭订单' WHERE id = ? AND status != 'PAID'",
                    (order_id,),
                )
                conn.commit()
                return {"ok": True, "message": f"已关闭订单 {o_row['order_no']}"}
            else:
                cur.execute("UPDATE membership_orders SET status = 'REJECTED' WHERE id = ? AND status != 'PAID'", (order_id,))
                conn.commit()
                return {"ok": True, "message": f"已拒绝订单 {o_row['order_no']}"}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def admin_delete_order(self, order_id: int) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            o_row = cur.execute("SELECT * FROM membership_orders WHERE id = ?", (order_id,)).fetchone()
            if not o_row:
                conn.rollback()
                return {"ok": False, "message": "该条订单记录不存在或已被删除"}

            # Preserve on-chain 0x tx_hash in replay-guard table before removing a PAID Web3 order
            tx_clean = (o_row["tx_hash"] or "").strip()
            if (
                o_row["status"] == "PAID"
                and tx_clean.lower().startswith("0x")
                and len(tx_clean) == 66
            ):
                cur.execute(
                    "INSERT OR IGNORE INTO consumed_tx_hashes (tx_hash, order_no, archived_at) VALUES (?, ?, ?)",
                    (tx_clean.lower(), o_row["order_no"], _now_str()),
                )

            cur.execute("DELETE FROM membership_orders WHERE id = ?", (order_id,))
            conn.commit()
            extra = "（用户已生效的会员时长不受影响）" if o_row["status"] == "PAID" else ""
            return {
                "ok": True,
                "message": f"已删除订单记录 {o_row['order_no']}（用户 {o_row['username']}）{extra}",
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def admin_clear_closed_orders(self) -> Dict[str, Any]:
        conn = self._connect(isolation_level=None)
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            self._expire_overdue_orders_in_tx(cur)
            cur.execute("DELETE FROM membership_orders WHERE status IN ('EXPIRED', 'CANCELLED', 'REJECTED')")
            deleted_count = int(cur.rowcount or 0)
            conn.commit()
            if deleted_count == 0:
                return {"ok": True, "deleted_count": 0, "message": "当前没有可清理的已超时/取消/拒绝订单"}
            return {
                "ok": True,
                "deleted_count": deleted_count,
                "message": f"已批量清理 {deleted_count} 条已超时/已取消/已拒绝订单流水",
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def admin_list_vip_codes(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM vip_codes ORDER BY id DESC LIMIT 200").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def admin_generate_vip_codes(self, plan_name: str, duration_days: int, count: int = 5) -> Dict[str, Any]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            now_str = _now_str()
            generated = []
            for _ in range(min(50, max(1, count))):
                rand_part = secrets.token_hex(4).upper()
                code_str = f"VIP-{duration_days}D-{rand_part}"
                cur.execute(
                    """INSERT INTO vip_codes (code, plan_name, duration_days, status, created_at)
                       VALUES (?, ?, ?, 'UNUSED', ?)""",
                    (code_str, plan_name, duration_days, now_str),
                )
                generated.append(code_str)
            conn.commit()
            return {
                "ok": True,
                "codes": generated,
                "message": f"成功生成 {len(generated)} 张【{plan_name} ({duration_days}天)】会员激活码",
            }
        finally:
            conn.close()

    def admin_delete_vip_code(self, code_id: int) -> Dict[str, Any]:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM vip_codes WHERE id = ?", (code_id,))
            conn.commit()
            return {"ok": True, "message": "激活码记录已删除"}
        finally:
            conn.close()

    # ==================== User Feedback / Question Message Board ====================
    def _format_feedback_row(
        self,
        row: sqlite3.Row | Dict[str, Any],
        now_ts: Optional[int] = None,
        for_admin: bool = False,
    ) -> Dict[str, Any]:
        d = dict(row)
        now_s = int(now_ts if now_ts is not None else time.time())
        created_ts = int(d.get("created_at_ts") or 0)
        age_sec = max(0, now_s - created_ts) if created_ts > 0 else FEEDBACK_USER_DELETE_MIN_AGE_SECONDS
        unlock_rem = max(0, FEEDBACK_USER_DELETE_MIN_AGE_SECONDS - age_sec)
        formatted = {
            "id": int(d["id"]),
            "user_id": int(d["user_id"]),
            "username": d.get("username") or "",
            "category": d.get("category") or "问题反馈",
            "title": d.get("title") or "",
            "content": d.get("content") or "",
            "status": d.get("status") or ("REPLIED" if (d.get("admin_reply") or "").strip() else "OPEN"),
            "admin_reply": d.get("admin_reply") or "",
            "replied_by": d.get("replied_by") or "",
            "replied_at": d.get("replied_at") or "",
            "replied_at_ts": int(d.get("replied_at_ts") or 0),
            "created_at": d.get("created_at") or "",
            "created_at_ts": created_ts,
            "age_seconds": age_sec,
            "can_user_delete": unlock_rem == 0,
            "delete_unlock_remaining_sec": unlock_rem,
            "delete_unlock_at_ts": (created_ts + FEEDBACK_USER_DELETE_MIN_AGE_SECONDS) if created_ts > 0 else 0,
        }
        if for_admin:
            formatted["user_deleted"] = bool(d.get("user_deleted", 0))
            formatted["user_deleted_at"] = int(d.get("user_deleted_at") or 0)
            formatted["admin_deleted"] = bool(d.get("admin_deleted", 0))
            formatted["admin_deleted_at"] = int(d.get("admin_deleted_at") or 0)
        return formatted

    def list_user_feedback(self, user_id: int, now_ts: Optional[int] = None) -> Dict[str, Any]:
        now_s = int(now_ts if now_ts is not None else time.time())
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # 24h submit limit checks ALL submissions of this user (including soft-deleted rows)
            last_row = conn.execute(
                "SELECT MAX(created_at_ts) AS last_ts FROM user_feedback_messages WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            last_ts = int(last_row["last_ts"] or 0) if last_row else 0
            elapsed = (now_s - last_ts) if last_ts > 0 else FEEDBACK_SUBMIT_COOLDOWN_SECONDS
            cooldown_rem = max(0, FEEDBACK_SUBMIT_COOLDOWN_SECONDS - elapsed) if last_ts > 0 else 0
            next_submit_at_ts = (last_ts + FEEDBACK_SUBMIT_COOLDOWN_SECONDS) if cooldown_rem > 0 else 0

            rows = conn.execute(
                """SELECT * FROM user_feedback_messages
                   WHERE user_id = ? AND user_deleted = 0
                   ORDER BY created_at_ts DESC, id DESC
                   LIMIT 100""",
                (user_id,),
            ).fetchall()
            messages = [self._format_feedback_row(r, now_ts=now_s, for_admin=False) for r in rows]
            replied_count = sum(1 for m in messages if m["status"] == "REPLIED")
            return {
                "messages": messages,
                "can_submit": cooldown_rem == 0,
                "cooldown_remaining_sec": cooldown_rem,
                "next_submit_at_ts": next_submit_at_ts,
                "cooldown_seconds": FEEDBACK_SUBMIT_COOLDOWN_SECONDS,
                "delete_min_age_seconds": FEEDBACK_USER_DELETE_MIN_AGE_SECONDS,
                "total_count": len(messages),
                "replied_count": replied_count,
            }
        finally:
            conn.close()

    def submit_user_feedback(
        self,
        user_id: int,
        title: str,
        content: str,
        category: str = "问题反馈",
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        clean_content = (content or "").strip()
        clean_title = (title or "").strip()[:120]
        clean_category = (category or "问题反馈").strip()[:32] or "问题反馈"
        if len(clean_content) < 2:
            return {"ok": False, "message": "请详细描述您的问题（至少 2 个字符）"}
        if len(clean_content) > 2000:
            return {"ok": False, "message": "留言内容请控制在 2000 字以内"}

        now_s = int(now_ts if now_ts is not None else time.time())
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_s))

        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            u_row = cur.execute("SELECT id, username, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u_row or not bool(u_row["is_active"]):
                conn.rollback()
                return {"ok": False, "message": "用户不存在或已被禁用"}

            # Atomic 24-hour rate-limit check across ALL submissions (cannot bypass by deleting)
            last_row = cur.execute(
                "SELECT MAX(created_at_ts) AS last_ts FROM user_feedback_messages WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            last_ts = int(last_row["last_ts"] or 0) if last_row else 0
            if last_ts > 0 and (now_s - last_ts) < FEEDBACK_SUBMIT_COOLDOWN_SECONDS:
                remaining = FEEDBACK_SUBMIT_COOLDOWN_SECONDS - (now_s - last_ts)
                conn.rollback()
                return {
                    "ok": False,
                    "message": f"每位用户 24 小时内仅可提交一次问题留言，距离下次提交还剩 {_format_duration_cn(remaining)}",
                    "cooldown_remaining_sec": remaining,
                }

            cur.execute(
                """INSERT INTO user_feedback_messages
                   (user_id, username, category, title, content, status,
                    admin_reply, replied_by, replied_at, replied_at_ts,
                    created_at, created_at_ts, user_deleted, user_deleted_at, admin_deleted, admin_deleted_at)
                   VALUES (?, ?, ?, ?, ?, 'OPEN', '', '', '', 0, ?, ?, 0, 0, 0, 0)""",
                (user_id, u_row["username"], clean_category, clean_title, clean_content, now_str, now_s),
            )
            msg_id = cur.lastrowid
            new_row = cur.execute("SELECT * FROM user_feedback_messages WHERE id = ?", (msg_id,)).fetchone()
            conn.commit()
            return {
                "ok": True,
                "message": "问题留言已提交成功！管理员将在后台查看并为您回复（24小时内仅可提交一次）",
                "item": self._format_feedback_row(new_row, now_ts=now_s, for_admin=False),
                "feedback": self.list_user_feedback(user_id, now_ts=now_s),
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def delete_user_feedback(
        self,
        user_id: int,
        message_id: int,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now_s = int(now_ts if now_ts is not None else time.time())
        conn = self._connect(isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT * FROM user_feedback_messages WHERE id = ? AND user_id = ? AND user_deleted = 0",
                (message_id, user_id),
            ).fetchone()
            if not row:
                conn.rollback()
                return {"ok": False, "message": "该条留言记录不存在或已被删除"}

            created_ts = int(row["created_at_ts"] or 0)
            age_sec = max(0, now_s - created_ts)
            if age_sec < FEEDBACK_USER_DELETE_MIN_AGE_SECONDS:
                remaining = FEEDBACK_USER_DELETE_MIN_AGE_SECONDS - age_sec
                conn.rollback()
                return {
                    "ok": False,
                    "message": f"留言提交满 24 小时后才可删除记录，还需等待 {_format_duration_cn(remaining)}",
                    "delete_unlock_remaining_sec": remaining,
                }

            # Soft-delete ONLY on user side; admin copy remains intact until admin explicitly deletes it
            cur.execute(
                "UPDATE user_feedback_messages SET user_deleted = 1, user_deleted_at = ? WHERE id = ? AND user_id = ?",
                (now_s, message_id, user_id),
            )
            conn.commit()
            return {
                "ok": True,
                "message": "已删除您侧的该条留言记录（管理员后台留存记录需由管理员自行处理）",
                "feedback": self.list_user_feedback(user_id, now_ts=now_s),
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def admin_list_feedback(
        self,
        status_filter: str = "ALL",
        search: str = "",
        limit: int = 200,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now_s = int(now_ts if now_ts is not None else time.time())
        st = (status_filter or "ALL").strip().upper()
        q = (search or "").strip()
        lim = max(1, min(500, int(limit)))

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            agg = conn.execute(
                """SELECT
                       COUNT(*) AS total_cnt,
                       SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_cnt,
                       SUM(CASE WHEN status = 'REPLIED' THEN 1 ELSE 0 END) AS replied_cnt
                   FROM user_feedback_messages
                   WHERE admin_deleted = 0"""
            ).fetchone()

            clauses = ["admin_deleted = 0"]
            params: List[Any] = []
            if st in ("OPEN", "REPLIED"):
                clauses.append("status = ?")
                params.append(st)
            if q:
                like = f"%{q}%"
                clauses.append("(username LIKE ? OR title LIKE ? OR content LIKE ? OR category LIKE ? OR admin_reply LIKE ?)")
                params.extend([like, like, like, like, like])
            params.append(lim)

            sql = f"""SELECT * FROM user_feedback_messages
                      WHERE {' AND '.join(clauses)}
                      ORDER BY created_at_ts DESC, id DESC
                      LIMIT ?"""
            rows = conn.execute(sql, tuple(params)).fetchall()
            return {
                "messages": [self._format_feedback_row(r, now_ts=now_s, for_admin=True) for r in rows],
                "total_count": int(agg["total_cnt"] or 0) if agg else 0,
                "open_count": int(agg["open_cnt"] or 0) if agg else 0,
                "replied_count": int(agg["replied_cnt"] or 0) if agg else 0,
            }
        finally:
            conn.close()

    def admin_reply_feedback(
        self,
        admin_username: str,
        message_id: int,
        reply: str,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        clean_reply = (reply or "").strip()
        if not clean_reply:
            return {"ok": False, "message": "回复内容不能为空"}
        if len(clean_reply) > 2000:
            return {"ok": False, "message": "回复内容请控制在 2000 字以内"}

        now_s = int(now_ts if now_ts is not None else time.time())
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_s))

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM user_feedback_messages WHERE id = ? AND admin_deleted = 0",
                (message_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "message": "该条用户留言不存在或已被管理员删除"}

            conn.execute(
                """UPDATE user_feedback_messages
                   SET status = 'REPLIED', admin_reply = ?, replied_by = ?, replied_at = ?, replied_at_ts = ?
                   WHERE id = ? AND admin_deleted = 0""",
                (clean_reply, admin_username or "admin", now_str, now_s, message_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM user_feedback_messages WHERE id = ?", (message_id,)).fetchone()
            return {
                "ok": True,
                "message": f"已回复用户【{row['username']}】的问题留言",
                "item": self._format_feedback_row(updated, now_ts=now_s, for_admin=True),
            }
        finally:
            conn.close()

    def admin_delete_feedback(
        self,
        message_id: int,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now_s = int(now_ts if now_ts is not None else time.time())
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, username FROM user_feedback_messages WHERE id = ? AND admin_deleted = 0",
                (message_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "message": "该条留言记录不存在或已被删除"}
            conn.execute(
                "UPDATE user_feedback_messages SET admin_deleted = 1, admin_deleted_at = ? WHERE id = ?",
                (now_s, message_id),
            )
            conn.commit()
            return {
                "ok": True,
                "message": f"已从后台删除用户【{row['username']}】的留言记录 #{message_id}",
            }
        finally:
            conn.close()

    def _is_default_admin_password_in_use(self) -> bool:
        if self._default_admin_pwd_cached is not None:
            return self._default_admin_pwd_cached
        conn = self._connect()
        try:
            row = conn.execute("SELECT password_hash FROM users WHERE username = 'admin' LIMIT 1").fetchone()
            res = bool(row and verify_password("admin123456", row[0]))
            self._default_admin_pwd_cached = res
            return res
        finally:
            conn.close()

    def admin_get_dashboard_summary(self, runtime_status_map: Optional[Dict[int, dict]] = None) -> Dict[str, Any]:
        users = self.admin_list_users()
        orders = self.admin_list_orders()
        codes = self.admin_list_vip_codes()
        plans = self.list_plans(include_disabled=True)

        db_summary_map: Dict[int, Dict[str, Any]] = {}
        try:
            db_summary_map = AsyncDatabaseManager(self.db_path).get_user_live_summary_map_sync()
        except Exception:
            pass
        rt_map = runtime_status_map or {}

        for u in users:
            uid = int(u["id"])
            db_info = db_summary_map.get(uid, {})
            rt_info = rt_map.get(uid, {})
            u["live_workspace"] = {
                "symbols_count": rt_info.get("symbols_count", db_info.get("symbols_count", 0)),
                "running_symbols_count": rt_info.get("running_symbols_count", 0),
                "follower_auth_status": rt_info.get("follower_auth_status", db_info.get("follower_auth_status", "UNCONFIGURED")),
                "follower_equity_usdt": rt_info.get("follower_equity_usdt", db_info.get("follower_equity_usdt", 0.0)),
                "trading_active": rt_info.get("trading_active", db_info.get("trading_active_live", False)),
                "engine_loaded": bool(rt_info.get("engine_loaded", False)),
                "live_trades_count": db_info.get("live_trades_count", 0),
                "live_net_pnl_usdt": db_info.get("live_net_pnl_usdt", 0.0),
            }

        total_users = len(users)
        active_vip_users = sum(1 for u in users if u["is_vip"])
        non_vip_users = total_users - active_vip_users
        disabled_users = sum(1 for u in users if not u["is_active"])

        # Compute full platform order/revenue/code counts via SQL (not truncated at LIMIT 200)
        conn = self._connect()
        try:
            ord_agg = conn.execute(
                """SELECT
                       COUNT(*) AS total_orders,
                       SUM(CASE WHEN status = 'PAID' THEN 1 ELSE 0 END) AS paid_cnt,
                       SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending_cnt,
                       SUM(CASE WHEN status IN ('EXPIRED', 'CANCELLED') THEN 1 ELSE 0 END) AS expired_cnt,
                       COALESCE(SUM(CASE WHEN status = 'PAID' THEN amount_usdt ELSE 0.0 END), 0.0) AS total_rev
                   FROM membership_orders"""
            ).fetchone()
            code_agg = conn.execute(
                """SELECT
                       COUNT(*) AS total_codes,
                       SUM(CASE WHEN status = 'UNUSED' THEN 1 ELSE 0 END) AS unused_codes
                   FROM vip_codes"""
            ).fetchone()
            fb_agg = conn.execute(
                """SELECT
                       COUNT(*) AS total_fb,
                       SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_fb,
                       SUM(CASE WHEN status = 'REPLIED' THEN 1 ELSE 0 END) AS replied_fb
                   FROM user_feedback_messages
                   WHERE admin_deleted = 0"""
            ).fetchone()
        finally:
            conn.close()

        feedback_data = self.admin_list_feedback(limit=200)

        return {
            "stats": {
                "total_users": total_users,
                "active_vip_users": active_vip_users,
                "non_vip_users": non_vip_users,
                "disabled_users": disabled_users,
                "total_orders": int(ord_agg[0] or 0),
                "paid_orders_count": int(ord_agg[1] or 0),
                "pending_orders_count": int(ord_agg[2] or 0),
                "expired_orders_count": int(ord_agg[3] or 0),
                "total_revenue_usdt": round(float(ord_agg[4] or 0.0), 3),
                "unused_codes_count": int(code_agg[1] or 0),
                "total_codes_count": int(code_agg[0] or 0),
                "active_live_engines_count": sum(1 for v in rt_map.values() if v.get("trading_active")),
                "total_feedback_count": int(fb_agg[0] or 0) if fb_agg else 0,
                "open_feedback_count": int(fb_agg[1] or 0) if fb_agg else 0,
                "replied_feedback_count": int(fb_agg[2] or 0) if fb_agg else 0,
            },
            "security_warnings": {
                "default_admin_password_in_use": self._is_default_admin_password_in_use(),
            },
            "users": users,
            "orders": orders,
            "vip_codes": codes,
            "plans": plans,
            "feedback_messages": feedback_data["messages"],
            "payment_info": self.get_payment_metadata(),
        }


class SlidingWindowRateLimiter:
    """In-memory per-IP/endpoint sliding-window rate limiter with automatic key pruning."""

    def __init__(self):
        self._buckets: Dict[str, deque] = {}
        self._last_prune_ts: float = time.time()

    def _prune_stale(self, now: float):
        if len(self._buckets) < 512 and (now - self._last_prune_ts) < 300.0:
            return
        self._last_prune_ts = now
        stale_cutoff = now - 3600.0
        for k in list(self._buckets.keys()):
            q = self._buckets.get(k)
            if not q or q[-1] < stale_cutoff:
                self._buckets.pop(k, None)

    def check_and_record(self, key: str, max_hits: int, window_sec: float) -> Tuple[bool, int]:
        now = time.time()
        self._prune_stale(now)
        q = self._buckets.get(key)
        if q is None:
            q = deque()
            self._buckets[key] = q
        cutoff = now - window_sec
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= max_hits:
            retry_after = max(1, int(q[0] + window_sec - now))
            return False, retry_after
        q.append(now)
        return True, 0


rate_limiter = SlidingWindowRateLimiter()
user_manager = UserMembershipManager()
