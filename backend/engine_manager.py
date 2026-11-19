"""
Multi-Tenant Engine Manager:
- Maintains ONE shared high-frequency MarketDataGateway across all isolated tenants
- Provides a sanitized `guest_engine` (`user_id=0`, `is_guest_preview=True`) for unauthenticated visitors
- Runs an isolated `BidirectionalSniperEngine` per authenticated user (`user_id > 0`), with lazy spin-up,
  auto-resume on server restart for 24/7 background trading, and periodic VIP expiration enforcement.
"""
import asyncio
from typing import Callable, Dict, Optional
from backend.database import AsyncDatabaseManager
from backend.market_gateway import MarketDataGateway
from backend.sniper_engine import BidirectionalSniperEngine


class EngineManager:
    def __init__(
        self,
        db: AsyncDatabaseManager,
        get_user_fn: Optional[Callable[[int], Optional[dict]]] = None,
    ):
        self.db = db
        self._get_user_fn = get_user_fn
        self.gateway = MarketDataGateway()
        self.guest_engine = BidirectionalSniperEngine(
            db=self.db,
            user_id=0,
            shared_gateway=self.gateway,
            is_guest_preview=True,
        )
        self.user_engines: Dict[int, BidirectionalSniperEngine] = {}
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._vip_guard_task: Optional[asyncio.Task] = None
        self._running = False

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id] = lock
        return lock

    async def start(self):
        if self._running:
            return
        self._running = True
        self.db.init_sync()
        await self.db.start_worker()

        await self.guest_engine.start()
        try:
            await asyncio.wait_for(self.gateway.fetch_exchange_instruments(), timeout=6.0)
        except Exception:
            pass

        # Auto-resume persistent user engines (24h background running across restarts)
        for uid in self.db.list_persistent_user_ids_sync():
            if uid <= 0:
                continue
            try:
                user_info = self._get_user_fn(uid) if self._get_user_fn else None
                # Strictly skip deleted or disabled users (never resurrect ghost engines!)
                if self._get_user_fn and (not user_info or not user_info.get("is_active", True)):
                    continue
                eng = await self.get_user_engine(uid)
                if user_info and not user_info.get("can_use_live", False):
                    if eng.trading_active_by_mode.get("LIVE"):
                        await eng.set_trading_active(False)
                        eng.append_log(
                            "WARN",
                            "VIP_EXPIRED",
                            "[会员到期暂停] 会员有效期已结束，实盘自动下单已暂停；续费后自动恢复",
                            mode="LIVE",
                        )
            except Exception as e:
                print(f"[EngineManager] auto-resume uid={uid} warning: {e}")

        await self.gateway.start()
        self._vip_guard_task = asyncio.create_task(self._vip_guard_loop())

    async def stop(self):
        self._running = False
        if self._vip_guard_task:
            self._vip_guard_task.cancel()
            self._vip_guard_task = None

        for eng in list(self.user_engines.values()):
            try:
                await eng.stop()
            except Exception:
                pass
        self.user_engines.clear()

        try:
            await self.guest_engine.stop()
        except Exception:
            pass

        await self.gateway.stop()
        await self.db.stop_worker()

    def get_guest_engine(self) -> BidirectionalSniperEngine:
        return self.guest_engine

    async def get_user_engine(self, user_id: int) -> BidirectionalSniperEngine:
        uid = int(user_id)
        if uid <= 0:
            return self.guest_engine
        existing = self.user_engines.get(uid)
        if existing is not None:
            return existing

        async with self._lock_for(uid):
            existing = self.user_engines.get(uid)
            if existing is not None:
                return existing
            eng = BidirectionalSniperEngine(
                db=self.db,
                user_id=uid,
                shared_gateway=self.gateway,
                is_guest_preview=False,
            )
            await eng.start()
            # Lazy-reload VIP guard: never allow expired/disabled user to auto-resume LIVE orders
            if self._get_user_fn:
                user_info = self._get_user_fn(uid)
                if not user_info or not user_info.get("is_active", True) or not user_info.get("can_use_live", False):
                    if eng.trading_active_by_mode.get("LIVE"):
                        await eng.set_trading_active(False)
            self.user_engines[uid] = eng
            return eng

    async def stop_user_engine(self, user_id: int):
        uid = int(user_id)
        async with self._lock_for(uid):
            eng = self.user_engines.pop(uid, None)
            if eng is not None:
                await eng.stop()

    def get_runtime_status_map(self) -> Dict[int, dict]:
        out: Dict[int, dict] = {}
        for uid, eng in self.user_engines.items():
            cur_mode = eng.risk_config.trading_mode
            _, follower_ex = eng.get_leader_And_follower_cfg()
            out[uid] = {
                "engine_loaded": True,
                "trading_mode": cur_mode,
                "trading_active": bool(eng.trading_active_by_mode.get(cur_mode, False)),
                "symbols_count": len(eng.strategies),
                "running_symbols_count": sum(1 for c in eng.strategies.values() if c.is_running),
                "follower_auth_status": follower_ex.get("auth_status", "UNCONFIGURED"),
                "follower_equity_usdt": float(follower_ex.get("account_equity_usdt") or 0.0),
                "active_positions_count": max(
                    len(eng.exchange_live_positions),
                    len(eng.active_positions) + len(eng.active_grid_positions),
                ),
            }
        return out

    async def _vip_guard_loop(self):
        """Periodically enforces membership expiration & prunes aged SQLite MISSED order/event logs."""
        tick = 0
        while self._running:
            await asyncio.sleep(60.0)
            tick += 1
            # Run DB housekeeping 1 min after boot and every 6 hours thereafter (non-blocking)
            if tick == 1 or tick % 360 == 0:
                try:
                    await asyncio.to_thread(self.db.cleanup_old_records_sync, 48, 14)
                except Exception:
                    pass
            if not self._get_user_fn:
                continue
            for uid, eng in list(self.user_engines.items()):
                try:
                    u = self._get_user_fn(uid)
                    if not u or not u.get("is_active", True) or not u.get("can_use_live", False):
                        if eng.trading_active_by_mode.get("LIVE"):
                            await eng.set_trading_active(False)
                            eng.append_log(
                                "WARN",
                                "VIP_EXPIRED",
                                "[会员权限变更] 账户已过期或被禁用，后台实盘自动交易已自动停止",
                                mode="LIVE",
                            )
                except Exception:
                    pass
