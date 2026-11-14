"""
User Authentication, Membership Subscription & Admin Management System.
Enforces strict tiered access rules:
1. Unauthenticated Guest: Cannot see the Live Trading system (Shadow Mode only).
2. Registered User (Non-VIP): Can see the Live Trading system & real-time market feed,
   but CANNOT use Live Trading execution or save/verify Live API keys until VIP is active.
3. Active VIP Member / Admin: Full access to view and use the Live Trading system.
4. Admin: Full access to User Management System & Membership Management System.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional
from backend.database import DB_PATH
from backend.models import (
    AdminCreateUserRequest,
    AdminGrantVipRequest,
    AdminMembershipPlanModel,
    AdminUpdateUserRequest,
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

-- 9. 会员购买订单与流水表
CREATE TABLE IF NOT EXISTS membership_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no VARCHAR(64) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    username VARCHAR(64) NOT NULL,
    plan_id INTEGER NOT NULL,
    plan_name VARCHAR(64) NOT NULL,
    duration_days INTEGER NOT NULL,
    amount_usdt DECIMAL(12, 2) NOT NULL,
    pay_method VARCHAR(32) NOT NULL,         -- 'INSTANT_PAY', 'USDT_TRC20', 'USDT_ERC20', 'ALIPAY_WECHAT', 'REDEEM_CODE', 'ADMIN_GRANT'
    tx_hash VARCHAR(128) DEFAULT '',
    status VARCHAR(24) NOT NULL,             -- 'PAID', 'PENDING', 'REJECTED'
    created_at TEXT NOT NULL,
    paid_at TEXT DEFAULT ''
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
"""

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


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, expected_hex = stored_hash.split("$", 1)
    actual = hash_password(password, salt)
    return hmac.compare_digest(actual, stored_hash)


def create_auth_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "role": role,
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

    def init_sync(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(USER_MEMBERSHIP_SCHEMA)
            cur = conn.cursor()
            now_s = int(time.time())
            now_str = _now_str()

            # 1. Seed Default Membership Plans
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

            # 2. Seed Default Users (admin, vip_user, reg_user)
            cur.execute("SELECT COUNT(*) FROM users")
            if cur.fetchone()[0] == 0:
                # Admin account (Full Admin + Permanent VIP)
                cur.execute(
                    """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                       VALUES (?, ?, ?, 'admin', 1, '终身至尊版会员', ?, ?, ?)""",
                    ("admin", "admin@sniper-engine.io", hash_password("admin123456"), now_s + 36500 * 86400, now_str, now_str),
                )
                # Active VIP Demo User (Can see AND use Live system)
                cur.execute(
                    """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                       VALUES (?, ?, ?, 'user', 1, '季度旗舰版会员', ?, ?, ?)""",
                    ("vip_user", "vip@sniper-engine.io", hash_password("123456"), now_s + 90 * 86400, now_str, ""),
                )
                # Registered Non-VIP Demo User (Can see Live system, must purchase VIP to use Live system)
                cur.execute(
                    """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                       VALUES (?, ?, ?, 'user', 1, 'NONE', 0, ?, ?)""",
                    ("reg_user", "user@sniper-engine.io", hash_password("123456"), now_str, ""),
                )

                # Seed sample order for vip_user
                cur.execute(
                    """INSERT INTO membership_orders
                       (order_no, user_id, username, plan_id, plan_name, duration_days, amount_usdt, pay_method, tx_hash, status, created_at, paid_at)
                       VALUES (?, 2, 'vip_user', 2, '季度旗舰版会员', 90, 249.0, 'USDT_TRC20', '0x89f4e2b90c71d...a31f', 'PAID', ?, ?)""",
                    (f"VIP{time.strftime('%Y%m%d')}8801", now_str, now_str),
                )

            # 3. Seed Sample Unused VIP Activation Codes
            cur.execute("SELECT COUNT(*) FROM vip_codes")
            if cur.fetchone()[0] == 0:
                sample_codes = [
                    ("VIP-30D-8888", "月度专业版会员", 30),
                    ("VIP-90D-9999", "季度旗舰版会员", 90),
                    ("VIP-365D-6666", "年度机构版会员", 365),
                ]
                for code_str, p_name, d_days in sample_codes:
                    cur.execute(
                        """INSERT INTO vip_codes (code, plan_name, duration_days, status, created_at)
                           VALUES (?, ?, ?, 'UNUSED', ?)""",
                        (code_str, p_name, d_days, now_str),
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
            vip_label = d.get("vip_tier") if d.get("vip_tier") != "NONE" else "终身至尊版 (管理员)"
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
        # Core Business Permission Flags:
        # 1. Registered & active user can SEE the live trading system
        d["can_see_live"] = is_active
        # 2. Only active VIP user (or Admin) can USE the live trading system
        d["can_use_live"] = is_active and is_vip
        return d

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
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
        user = self.get_user_by_id(int(payload["uid"]))
        if not user or not user.get("is_active"):
            return None
        return user

    # ==================== Auth Endpoints Logic ====================
    def register_user(self, username: str, password: str, email: str = "") -> Dict[str, Any]:
        uname = username.strip()
        if len(uname) < 3:
            return {"ok": False, "message": "用户名长度至少为 3 个字符"}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            existing = cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone()
            if existing:
                return {"ok": False, "message": f"用户名 '{uname}' 已被注册，请更换其他用户名"}
            now_str = _now_str()
            cur.execute(
                """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                   VALUES (?, ?, ?, 'user', 1, 'NONE', 0, ?, ?)""",
                (uname, email.strip(), hash_password(password), now_str, now_str),
            )
            uid = cur.lastrowid
            conn.commit()
            user = self.get_user_by_id(uid)
            token = create_auth_token(uid, uname, "user")
            return {
                "ok": True,
                "message": "注册成功！您已解锁「实盘通道」查看权限，开通会员后即可启动实盘自动交易",
                "token": token,
                "user": user,
            }
        finally:
            conn.close()

    def login_user(self, username: str, password: str) -> Dict[str, Any]:
        uname = username.strip()
        conn = sqlite3.connect(self.db_path)
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
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_str, row["id"]))
            conn.commit()

            user = self.get_user_by_id(row["id"])
            token = create_auth_token(row["id"], row["username"], row["role"])
            return {
                "ok": True,
                "message": f"欢迎回来，{row['username']}！",
                "token": token,
                "user": user,
            }
        finally:
            conn.close()

    def change_password(self, user_id: int, old_password: str, new_password: str) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}
            if not verify_password(old_password, row["password_hash"]):
                return {"ok": False, "message": "原密码验证失败"}
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user_id))
            conn.commit()
            return {"ok": True, "message": "密码修改成功"}
        finally:
            conn.close()

    # ==================== Membership Plans, Orders & Redemption ====================
    def list_plans(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
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

    def purchase_membership(
        self,
        user_id: int,
        plan_id: int,
        pay_method: str = "INSTANT_PAY",
        tx_hash: str = "",
        auto_confirm: bool = True,
    ) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            u_row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u_row:
                return {"ok": False, "message": "用户不存在"}
            p_row = cur.execute("SELECT * FROM membership_plans WHERE id = ?", (plan_id,)).fetchone()
            if not p_row or not bool(p_row["is_enabled"]):
                return {"ok": False, "message": "所选会员套餐不存在或已下架"}

            now_str = _now_str()
            order_no = f"VIP{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(9000)+1000}"
            duration_days = int(p_row["duration_days"])
            plan_name = p_row["name"]
            amount = float(p_row["price_usdt"])

            status = "PAID" if auto_confirm else "PENDING"
            paid_at = now_str if auto_confirm else ""

            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days, amount_usdt, pay_method, tx_hash, status, created_at, paid_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_no,
                    user_id,
                    u_row["username"],
                    plan_id,
                    plan_name,
                    duration_days,
                    amount,
                    pay_method,
                    tx_hash.strip() or f"AUTO-{order_no[-6:]}",
                    status,
                    now_str,
                    paid_at,
                ),
            )

            if auto_confirm:
                self._extend_user_vip_in_tx(cur, user_id, duration_days, plan_name)
                msg = f"会员开通成功！已为您激活【{plan_name}】（+{duration_days} 天），实盘自动交易功能已全部解锁！"
            else:
                msg = f"订单 {order_no} 已提交成功，等待后台管理员审核确认后自动为您开通【{plan_name}】"

            conn.commit()
            return {
                "ok": True,
                "status": status,
                "order_no": order_no,
                "message": msg,
                "user": self.get_user_by_id(user_id),
            }
        finally:
            conn.close()

    def redeem_vip_code(self, user_id: int, code: str) -> Dict[str, Any]:
        clean_code = code.strip().upper()
        if not clean_code:
            return {"ok": False, "message": "请输入有效的会员激活码"}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            u_row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u_row:
                return {"ok": False, "message": "用户不存在"}

            c_row = cur.execute("SELECT * FROM vip_codes WHERE UPPER(code) = ?", (clean_code,)).fetchone()
            if not c_row:
                return {"ok": False, "message": "激活码不存在，请检查输入是否正确"}
            if c_row["status"] == "USED":
                return {"ok": False, "message": f"该激活码已被使用（使用时间: {c_row['used_at']}）"}
            if c_row["status"] != "UNUSED":
                return {"ok": False, "message": "该激活码已失效或被禁用"}

            now_str = _now_str()
            duration_days = int(c_row["duration_days"])
            plan_name = c_row["plan_name"]

            cur.execute(
                "UPDATE vip_codes SET status = 'USED', used_by_username = ?, used_at = ? WHERE id = ?",
                (u_row["username"], now_str, c_row["id"]),
            )
            self._extend_user_vip_in_tx(cur, user_id, duration_days, plan_name)

            order_no = f"CODE{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900)+100}"
            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days, amount_usdt, pay_method, tx_hash, status, created_at, paid_at)
                   VALUES (?, ?, ?, 0, ?, ?, 0.0, 'REDEEM_CODE', ?, 'PAID', ?, ?)""",
                (order_no, user_id, u_row["username"], plan_name, duration_days, clean_code, now_str, now_str),
            )
            conn.commit()
            return {
                "ok": True,
                "message": f"激活码兑换成功！已为您开通【{plan_name}】（+{duration_days} 天），实盘交易系统已解锁！",
                "user": self.get_user_by_id(user_id),
            }
        finally:
            conn.close()

    def list_user_orders(self, user_id: int) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM membership_orders WHERE user_id = ? ORDER BY id DESC LIMIT 50", (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ==================== Admin: User Management System ====================
    def admin_list_users(self, search: str = "") -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            if cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone():
                return {"ok": False, "message": f"用户名 '{uname}' 已存在"}
            now_str = _now_str()
            now_s = int(time.time())
            exp = (now_s + req.vip_days * 86400) if req.vip_days > 0 else (now_s + 36500 * 86400 if req.role == "admin" else 0)
            tier = req.vip_tier if (req.vip_days > 0 and req.vip_tier != "NONE") else ("终身至尊版会员" if req.role == "admin" else ("专业版会员" if req.vip_days > 0 else "NONE"))
            cur.execute(
                """INSERT INTO users (username, email, password_hash, role, is_active, vip_tier, vip_expires_at, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '')""",
                (uname, req.email.strip(), hash_password(req.password), req.role, tier, exp, now_str),
            )
            conn.commit()
            return {"ok": True, "message": f"已成功创建用户 {uname}"}
        finally:
            conn.close()

    def admin_update_user(self, req: AdminUpdateUserRequest) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM users WHERE id = ?", (req.user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}

            email = req.email.strip() if req.email is not None else row["email"]
            role = req.role if req.role is not None else row["role"]
            is_active = (1 if req.is_active else 0) if req.is_active is not None else row["is_active"]
            pwd_hash = hash_password(req.new_password) if (req.new_password and len(req.new_password) >= 6) else row["password_hash"]

            cur.execute(
                "UPDATE users SET email = ?, role = ?, is_active = ?, password_hash = ? WHERE id = ?",
                (email, role, is_active, pwd_hash, req.user_id),
            )
            conn.commit()
            return {"ok": True, "message": f"用户 {row['username']} 信息已更新"}
        finally:
            conn.close()

    def admin_grant_vip(self, req: AdminGrantVipRequest) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM users WHERE id = ?", (req.user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}

            now_str = _now_str()
            if req.revoke or req.add_days <= 0:
                cur.execute("UPDATE users SET vip_tier = 'NONE', vip_expires_at = 0 WHERE id = ?", (req.user_id,))
                conn.commit()
                return {"ok": True, "message": f"已取消用户 {row['username']} 的会员资格"}

            self._extend_user_vip_in_tx(cur, req.user_id, req.add_days, req.vip_tier or "专业版会员")
            order_no = f"ADM{time.strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900)+100}"
            cur.execute(
                """INSERT INTO membership_orders
                   (order_no, user_id, username, plan_id, plan_name, duration_days, amount_usdt, pay_method, tx_hash, status, created_at, paid_at)
                   VALUES (?, ?, ?, 0, ?, ?, 0.0, 'ADMIN_GRANT', '管理员后台手动开通/延期', 'PAID', ?, ?)""",
                (order_no, req.user_id, row["username"], req.vip_tier, req.add_days, now_str, now_str),
            )
            conn.commit()
            return {
                "ok": True,
                "message": f"已为用户 {row['username']} 开通/延期【{req.vip_tier}】 +{req.add_days} 天",
            }
        finally:
            conn.close()

    def admin_delete_user(self, user_id: int) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT username, role FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return {"ok": False, "message": "用户不存在"}
            if row["username"] == "admin":
                return {"ok": False, "message": "系统默认超级管理员账号不可删除"}
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return {"ok": True, "message": f"已删除用户 {row['username']}"}
        finally:
            conn.close()

    # ==================== Admin: Membership Management System ====================
    def admin_upsert_plan(self, req: AdminMembershipPlanModel) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            feat_json = json.dumps(req.features or [], ensure_ascii=False)
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
            return {"ok": True, "message": f"会员套餐【{req.name}】已保存生效"}
        finally:
            conn.close()

    def admin_delete_plan(self, plan_id: int) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM membership_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return {"ok": True, "message": "会员套餐已删除"}
        finally:
            conn.close()

    def admin_list_orders(self, status_filter: str = "") -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if status_filter.strip():
                rows = conn.execute(
                    "SELECT * FROM membership_orders WHERE status = ? ORDER BY id DESC LIMIT 200",
                    (status_filter.strip(),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM membership_orders ORDER BY id DESC LIMIT 200").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def admin_audit_order(self, order_id: int, action: str) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            o_row = cur.execute("SELECT * FROM membership_orders WHERE id = ?", (order_id,)).fetchone()
            if not o_row:
                return {"ok": False, "message": "订单不存在"}
            if o_row["status"] == "PAID":
                return {"ok": False, "message": "该订单已处于已支付开通状态"}

            now_str = _now_str()
            if action == "APPROVE":
                cur.execute(
                    "UPDATE membership_orders SET status = 'PAID', paid_at = ? WHERE id = ?",
                    (now_str, order_id),
                )
                self._extend_user_vip_in_tx(cur, int(o_row["user_id"]), int(o_row["duration_days"]), o_row["plan_name"])
                conn.commit()
                return {
                    "ok": True,
                    "message": f"订单 {o_row['order_no']} 已审核通过，已为用户 {o_row['username']} 自动开通【{o_row['plan_name']}】",
                }
            else:
                cur.execute("UPDATE membership_orders SET status = 'REJECTED' WHERE id = ?", (order_id,))
                conn.commit()
                return {"ok": True, "message": f"已拒绝订单 {o_row['order_no']}"}
        finally:
            conn.close()

    def admin_list_vip_codes(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM vip_codes ORDER BY id DESC LIMIT 200").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def admin_generate_vip_codes(self, plan_name: str, duration_days: int, count: int = 5) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            now_str = _now_str()
            generated = []
            for _ in range(min(50, max(1, count))):
                rand_part = secrets.token_hex(3).upper()
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
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM vip_codes WHERE id = ?", (code_id,))
            conn.commit()
            return {"ok": True, "message": "激活码记录已删除"}
        finally:
            conn.close()

    def admin_get_dashboard_summary(self) -> Dict[str, Any]:
        users = self.admin_list_users()
        orders = self.admin_list_orders()
        codes = self.admin_list_vip_codes()
        plans = self.list_plans(include_disabled=True)

        total_users = len(users)
        active_vip_users = sum(1 for u in users if u["is_vip"])
        non_vip_users = total_users - active_vip_users
        disabled_users = sum(1 for u in users if not u["is_active"])

        paid_orders = [o for o in orders if o["status"] == "PAID"]
        pending_orders = [o for o in orders if o["status"] == "PENDING"]
        total_revenue_usdt = round(sum(float(o.get("amount_usdt") or 0.0) for o in paid_orders), 2)
        unused_codes = sum(1 for c in codes if c["status"] == "UNUSED")

        return {
            "stats": {
                "total_users": total_users,
                "active_vip_users": active_vip_users,
                "non_vip_users": non_vip_users,
                "disabled_users": disabled_users,
                "total_orders": len(orders),
                "paid_orders_count": len(paid_orders),
                "pending_orders_count": len(pending_orders),
                "total_revenue_usdt": total_revenue_usdt,
                "unused_codes_count": unused_codes,
                "total_codes_count": len(codes),
            },
            "users": users,
            "orders": orders,
            "vip_codes": codes,
            "plans": plans,
        }


user_manager = UserMembershipManager()
