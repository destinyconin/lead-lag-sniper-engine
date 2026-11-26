"""
Multi-Chain Web3 On-Chain Payment Monitor for VIP Membership Orders.
Monitors incoming ERC-20 (USDT / USDC / stablecoin) and native transfers to:
    0x8D550B8c462310126BA26d85A4a1f779Cd56A9e0
across 10 supported EVM networks:
    Ethereum, Optimism, Base, Arbitrum, Polygon,
    Ink, Linea, Avalanche, BNB Smart Chain, Robinhood Chain.

Matches transfers by the exact 3-decimal randomized order amount (e.g. 98.123)
within the 30-minute order validity window, and automatically activates VIP.
Automatically closes expired orders and stops on-chain polling when no orders are pending.
"""
import asyncio
import base64
import io
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
import qrcode
import qrcode.image.svg


def _get_proxy():
    from backend.market_gateway import get_system_proxy

    return get_system_proxy()

logger = logging.getLogger("sniper.payment_monitor")

PAYMENT_WALLET_ADDRESS = "0x8D550B8c462310126BA26d85A4a1f779Cd56A9e0"
PAYMENT_WALLET_LOWER = PAYMENT_WALLET_ADDRESS.lower()
ORDER_TIMEOUT_SECONDS = 30 * 60  # 30 minutes

SUPPORTED_CHAINS: List[Dict[str, Any]] = [
    {
        "id": "ethereum",
        "name": "Ethereum",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "lookback_blocks": 50,
        "tokens": {
            "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
            "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
        },
        "require_address_filter": True,
    },
    {
        "id": "optimism",
        "name": "Optimism",
        "rpc": "https://mainnet.optimism.io",
        "lookback_blocks": 400,
        "tokens": {
            "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": ("USDT", 6),
            "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USDC", 6),
            "0x7f5c764cbc14f9669b88837ca1490cca17c31607": ("USDC.e", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "base",
        "name": "Base",
        "rpc": "https://base-rpc.publicnode.com",
        "lookback_blocks": 400,
        "tokens": {
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
            "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": ("USDT", 6),
            "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": ("USDbC", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "arbitrum",
        "name": "Arbitrum",
        "rpc": "https://arb1.arbitrum.io/rpc",
        "lookback_blocks": 1500,
        "tokens": {
            "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
            "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
            "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("USDC.e", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "polygon",
        "name": "Polygon",
        "rpc": "https://polygon-bor-rpc.publicnode.com",
        "lookback_blocks": 300,
        "tokens": {
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USDT", 6),
            "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": ("USDC.e", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "ink",
        "name": "Ink",
        "rpc": "https://rpc-gel.inkonchain.com",
        "lookback_blocks": 400,
        "tokens": {
            "0x0200c29006150606b650577bbe7b6248f58470c1": ("USDT0", 6),
            "0xf1815bd50389c46847f0bda824ec8da914045d14": ("USDC.e", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "linea",
        "name": "Linea",
        "rpc": "https://rpc.linea.build",
        "lookback_blocks": 300,
        "tokens": {
            "0xa219439258ca9da29e9cc4ce5596924745e12b93": ("USDT", 6),
            "0x176211869ca2b568f2a7d4ee941e073a821ee1ff": ("USDC", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "avalanche",
        "name": "Avalanche",
        "rpc": "https://avalanche-c-chain-rpc.publicnode.com",
        "lookback_blocks": 80,
        "tokens": {
            "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": ("USDT", 6),
            "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": ("USDC", 6),
            "0xc7198437980c041c805a1edcba50c1ce5db95118": ("USDT.e", 6),
            "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664": ("USDC.e", 6),
        },
        "require_address_filter": True,
    },
    {
        "id": "bsc",
        "name": "BNB Smart Chain",
        "rpc": "https://bsc-rpc.publicnode.com",
        "lookback_blocks": 300,
        "tokens": {
            "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18),
            "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409": ("FDUSD", 18),
        },
        "require_address_filter": True,
    },
    {
        "id": "robinhood",
        "name": "Robinhood Chain",
        "rpc": "https://rpc.mainnet.chain.robinhood.com",
        "lookback_blocks": 500,
        "tokens": {
            "0x5fc5360d0400a0fd4f2af552add042d716f1d168": ("USDG", 6),
        },
        "require_address_filter": True,
    },
]

SUPPORTED_CHAIN_NAMES = [c["name"] for c in SUPPORTED_CHAINS]

PAYMENT_WARNING_TEXT = (
    "此地址支持 Ethereum, Optimism, Base, Arbitrum, Polygon, "
    "Ink, Linea, Avalanche, BNB Smart Chain, Robinhood Chain 上的资产。"
    "发送不兼容的资产可能导致永久丢失。"
)

TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
RECIPIENT_PADDED_TOPIC = "0x000000000000000000000000" + PAYMENT_WALLET_LOWER[2:]

_QR_SVG_DATA_URI_CACHE: Dict[str, str] = {}


def generate_wallet_qr_data_uri(address: str = PAYMENT_WALLET_ADDRESS) -> str:
    if address in _QR_SVG_DATA_URI_CACHE:
        return _QR_SVG_DATA_URI_CACHE[address]
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(address)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    uri = f"data:image/svg+xml;base64,{b64}"
    _QR_SVG_DATA_URI_CACHE[address] = uri
    return uri


def _matches_exact_3dec(raw_value: int, target_amount: float, decimals_list: List[int]) -> bool:
    """
    Checks whether raw integer token units match `target_amount` (e.g. 98.123)
    at 3 decimal places precision.
    """
    target_mills = int(round(target_amount * 1000))
    if target_mills <= 0 or raw_value <= 0:
        return False
    for dec in decimals_list:
        if dec < 3:
            continue
        scale = 10 ** (dec - 3)
        # Allow exact match on the 3 decimal places (or tiny 1-wei rounding on 18-dec tokens)
        expected_raw = target_mills * scale
        tolerance = max(1, scale // 100)  # strictly within 0.00001 token
        if abs(raw_value - expected_raw) <= tolerance:
            return True
    return False


CHAIN_MIN_CONFIRMATIONS: Dict[str, int] = {
    "ethereum": 2,
    "bsc": 12,
    "polygon": 25,
    "arbitrum": 5,
    "optimism": 5,
    "base": 5,
    "avalanche": 5,
    "linea": 5,
    "ink": 5,
}


class OnChainPaymentMonitor:
    def __init__(self, user_manager):
        self.user_manager = user_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        self._last_scanned_block: Dict[str, int] = {}
        self._last_head_block: Dict[str, int] = {}
        self._token_decimals_cache: Dict[str, int] = {}
        self._block_ts_cache: Dict[str, int] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._http_client_loop_id: int = 0
        self.last_scan_ts: int = 0
        self.scan_cycles_count: int = 0

    def _get_http_client(self) -> httpx.AsyncClient:
        from backend.market_gateway import get_system_proxy
        try:
            lid = id(asyncio.get_running_loop())
        except RuntimeError:
            lid = 0
        if self._http_client is None or self._http_client.is_closed or self._http_client_loop_id != lid:
            self._http_client = httpx.AsyncClient(
                proxy=get_system_proxy(),
                timeout=httpx.Timeout(8.0, connect=5.0),
                limits=httpx.Limits(max_connections=30, max_keepalive_connections=15, keepalive_expiry=45.0),
            )
            self._http_client_loop_id = lid
        return self._http_client

    def _load_persisted_cursors(self):
        try:
            from backend.database import AsyncDatabaseManager
            db = AsyncDatabaseManager(self.user_manager.db_path)
            saved = db.load_runtime_setting_sync("payment_monitor_cursors", default={}, user_id=0)
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if isinstance(v, int) and v > 0:
                        self._last_scanned_block[str(k)] = v
        except Exception:
            pass

    def _save_persisted_cursors(self):
        if not self._last_scanned_block:
            return
        try:
            from backend.database import AsyncDatabaseManager
            db = AsyncDatabaseManager(self.user_manager.db_path)
            db.save_runtime_setting_sync("payment_monitor_cursors", dict(self._last_scanned_block), user_id=0)
        except Exception:
            pass

    def start(self):
        if self._running:
            return
        self._load_persisted_cursors()
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        self._running = False
        self._wake_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http_client is not None and not self._http_client.is_closed:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

    def trigger_wake(self):
        """Wake up the monitor loop immediately when a new order is created."""
        self._wake_event.set()

    async def _monitor_loop(self):
        while self._running:
            try:
                # 1. Always expire overdue orders (>30 minutes) off the event loop
                await asyncio.to_thread(self.user_manager.expire_overdue_orders)
                pending = await asyncio.to_thread(self.user_manager.get_active_pending_orders)
                if pending:
                    await self.scan_once()
                    # Poll every 6 seconds while there are active pending orders
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=6.0)
                    except asyncio.TimeoutError:
                        pass
                    self._wake_event.clear()
                else:
                    # No active pending orders: sleep until woken up or check every 30s
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=30.0)
                    except asyncio.TimeoutError:
                        pass
                    self._wake_event.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Payment monitor loop error: {e}")
                await asyncio.sleep(5.0)

    async def _rpc_call(self, client: httpx.AsyncClient, rpc_url: str, method: str, params: list) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        resp = await client.post(
            rpc_url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "LeadLagSniper-PaymentMonitor/2.0"},
            timeout=7.5,
        )
        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(f"RPC HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data and data["error"]:
            raise RuntimeError(str(data["error"]))
        return data.get("result")

    async def _get_token_decimals(self, client: httpx.AsyncClient, chain_cfg: Dict[str, Any], token_addr: str) -> List[int]:
        addr_low = token_addr.lower()
        known = chain_cfg.get("tokens", {}).get(addr_low)
        if known:
            return [known[1]]
        cache_key = f"{chain_cfg['id']}:{addr_low}"
        if cache_key in self._token_decimals_cache:
            return [self._token_decimals_cache[cache_key]]
        try:
            res = await self._rpc_call(
                client,
                chain_cfg["rpc"],
                "eth_call",
                [{"to": token_addr, "data": "0x313ce567"}, "latest"],
            )
            if res and res not in ("0x", "0x0"):
                dec = int(res, 16)
                if 3 <= dec <= 24:
                    self._token_decimals_cache[cache_key] = dec
                    return [dec]
        except Exception:
            pass
        return [6, 18, 8]

    async def verify_specific_tx_hash(self, tx_hash: str, pending_orders: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        If a user provides a specific transaction hash, check its receipt & transaction data
        across all 10 supported chains immediately.
        """
        clean_hash = tx_hash.strip()
        if not clean_hash.startswith("0x") or len(clean_hash) != 66:
            return None
        if await asyncio.to_thread(self.user_manager.is_tx_hash_already_used, clean_hash):
            return None

        async with httpx.AsyncClient(proxy=_get_proxy(), timeout=8.0) as client:
            tasks = [
                self._check_tx_on_chain(client, chain_cfg, clean_hash, pending_orders)
                for chain_cfg in SUPPORTED_CHAINS
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict) and r.get("matched"):
                    return r
        return None

    async def _get_block_timestamp(
        self,
        client: httpx.AsyncClient,
        rpc_url: str,
        chain_id: str,
        block_hex: Optional[str],
    ) -> int:
        if not block_hex:
            return 0
        cache_key = f"{chain_id}:{block_hex.lower()}"
        if cache_key in self._block_ts_cache:
            return self._block_ts_cache[cache_key]
        try:
            blk = await self._rpc_call(client, rpc_url, "eth_getBlockByNumber", [block_hex, False])
            if isinstance(blk, dict) and blk.get("timestamp"):
                ts = int(blk["timestamp"], 16)
                if len(self._block_ts_cache) > 2000:
                    self._block_ts_cache.clear()
                self._block_ts_cache[cache_key] = ts
                return ts
        except Exception:
            pass
        return 0

    @staticmethod
    def _order_matches_block_window(order: Dict[str, Any], block_ts: int) -> bool:
        if order.get("status") != "PENDING":
            return False
        if block_ts <= 0:
            return False
        created_ts = int(order.get("created_at_ts") or 0)
        expires_ts = int(order.get("expires_at_ts") or 0)
        # Never accept transfers confirmed BEFORE the order was created (blocks transfer-hijacking attacks!)
        if created_ts > 0 and block_ts < created_ts:
            return False
        if expires_ts > 0 and block_ts > expires_ts + 180:
            return False
        return True

    async def _check_tx_on_chain(
        self,
        client: httpx.AsyncClient,
        chain_cfg: Dict[str, Any],
        tx_hash: str,
        pending_orders: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        tokens_map = chain_cfg.get("tokens") or {}
        if not tokens_map:
            return None
        try:
            receipt = await self._rpc_call(client, chain_cfg["rpc"], "eth_getTransactionReceipt", [tx_hash])
            if not receipt or receipt.get("status") != "0x1":
                return None

            chain_id = chain_cfg["id"]
            min_conf = int(CHAIN_MIN_CONFIRMATIONS.get(chain_id, 3))
            tx_blk_hex = receipt.get("blockNumber")
            if tx_blk_hex and chain_id in self._last_head_block:
                tx_blk = int(tx_blk_hex, 16)
                if self._last_head_block[chain_id] - tx_blk < min_conf:
                    head_hex = await self._rpc_call(client, chain_cfg["rpc"], "eth_blockNumber", [])
                    if isinstance(head_hex, str) and head_hex.startswith("0x"):
                        self._last_head_block[chain_id] = max(self._last_head_block[chain_id], int(head_hex, 16))
                    if self._last_head_block[chain_id] - tx_blk < min_conf:
                        return None

            blk_ts = await self._get_block_timestamp(
                client, chain_cfg["rpc"], chain_id, tx_blk_hex
            )
            if blk_ts <= 0:
                return None

            # Strictly accept ONLY non-removed whitelisted stablecoin ERC-20 Transfer logs
            for log in receipt.get("logs", []):
                if log.get("removed") is True:
                    continue
                topics = [t.lower() for t in log.get("topics", [])]
                if len(topics) >= 3 and topics[0] == TRANSFER_EVENT_TOPIC and topics[2] == RECIPIENT_PADDED_TOPIC:
                    token_addr = (log.get("address") or "").lower()
                    token_meta = tokens_map.get(token_addr)
                    if not token_meta:
                        continue
                    raw_hex = str(log.get("data") or "0x0")
                    if raw_hex in ("", "0x", "0X"):
                        continue
                    raw_val = int(raw_hex, 16)
                    if raw_val <= 0:
                        continue
                    decs = [int(token_meta[1])]
                    for order in pending_orders:
                        if not self._order_matches_block_window(order, blk_ts):
                            continue
                        target_amt = float(order.get("pay_amount_usdt") or order.get("amount_usdt") or 0.0)
                        if _matches_exact_3dec(raw_val, target_amt, decs):
                            res = self.user_manager.confirm_order_paid_onchain(
                                order_id=order["id"],
                                chain_name=f"{chain_cfg['name']} ({token_meta[0]})",
                                tx_hash=tx_hash,
                                paid_amount=target_amt,
                            )
                            if res.get("ok"):
                                return {"matched": True, "order_no": order["order_no"], "chain": chain_cfg["name"], "tx_hash": tx_hash}
        except Exception:
            pass
        return None

    async def _scan_single_chain(
        self,
        client: httpx.AsyncClient,
        chain_cfg: Dict[str, Any],
        pending_orders: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        matched_events = []
        tokens_map = chain_cfg.get("tokens") or {}
        if not tokens_map:
            return matched_events
        chain_id = chain_cfg["id"]
        rpc_url = chain_cfg["rpc"]
        try:
            blk_hex = await self._rpc_call(client, rpc_url, "eth_blockNumber", [])
            if not blk_hex:
                return matched_events
            head_block = int(blk_hex, 16)
            self._last_head_block[chain_id] = head_block
            min_conf = int(CHAIN_MIN_CONFIRMATIONS.get(chain_id, 3))
            safe_to_block = max(0, head_block - min_conf)
            lookback = max(int(chain_cfg.get("lookback_blocks", 80)), 250)

            last_blk = self._last_scanned_block.get(chain_id)
            if last_blk is None or safe_to_block - last_blk > lookback * 3:
                from_block = max(0, safe_to_block - lookback)
            else:
                from_block = max(0, last_blk - 2)

            if from_block > safe_to_block:
                return matched_events

            # Chunk eth_getLogs into <= 400-block pages to prevent public RPC range rejections
            all_logs: List[dict] = []
            chunk_size = 400
            cur_from = from_block
            while cur_from <= safe_to_block:
                cur_to = min(safe_to_block, cur_from + chunk_size - 1)
                filter_params: Dict[str, Any] = {
                    "fromBlock": hex(cur_from),
                    "toBlock": hex(cur_to),
                    "address": list(tokens_map.keys()),
                    "topics": [TRANSFER_EVENT_TOPIC, None, RECIPIENT_PADDED_TOPIC],
                }
                chunk_logs = await self._rpc_call(client, rpc_url, "eth_getLogs", [filter_params])
                if not isinstance(chunk_logs, list):
                    return matched_events
                all_logs.extend(chunk_logs)
                cur_from = cur_to + 1

            had_transient_ts_failure = False
            for log in all_logs:
                if log.get("removed") is True:
                    continue
                tx_hash = log.get("transactionHash") or ""
                if not tx_hash or self.user_manager.is_tx_hash_already_used(tx_hash):
                    continue
                token_addr = (log.get("address") or "").lower()
                token_meta = tokens_map.get(token_addr)
                if not token_meta:
                    continue
                raw_hex = str(log.get("data") or "0x0")
                if raw_hex in ("", "0x", "0X"):
                    continue
                raw_val = int(raw_hex, 16)
                if raw_val <= 0:
                    continue

                decs = [int(token_meta[1])]
                candidate_orders = [
                    o
                    for o in pending_orders
                    if o.get("status") == "PENDING"
                    and _matches_exact_3dec(
                        raw_val, float(o.get("pay_amount_usdt") or o.get("amount_usdt") or 0.0), decs
                    )
                ]
                if not candidate_orders:
                    continue

                blk_ts = await self._get_block_timestamp(client, rpc_url, chain_id, log.get("blockNumber"))
                if blk_ts <= 0:
                    had_transient_ts_failure = True
                    continue
                for order in candidate_orders:
                    if not self._order_matches_block_window(order, blk_ts):
                        continue
                    target_amt = float(order.get("pay_amount_usdt") or order.get("amount_usdt") or 0.0)
                    res = self.user_manager.confirm_order_paid_onchain(
                        order_id=order["id"],
                        chain_name=f"{chain_cfg['name']} ({token_meta[0]})",
                        tx_hash=tx_hash,
                        paid_amount=target_amt,
                    )
                    if res.get("ok"):
                        order["status"] = "PAID"
                        matched_events.append(
                            {
                                "order_no": order["order_no"],
                                "chain": chain_cfg["name"],
                                "tx_hash": tx_hash,
                                "amount": target_amt,
                            }
                        )
                        break

            # Advance cursor ONLY AFTER all logs processed cleanly without transient timestamp failures!
            if not had_transient_ts_failure:
                self._last_scanned_block[chain_id] = safe_to_block
        except Exception as e:
            logger.warning(f"Chain {chain_cfg['name']} scan error: {e}")
        return matched_events

    async def scan_once(self, optional_tx_hash: str = "") -> List[Dict[str, Any]]:
        """
        Executes one full scan pass across all 10 chains for all currently active PENDING orders.
        """
        async with self._scan_lock:
            await asyncio.to_thread(self.user_manager.expire_overdue_orders)
            pending_orders = await asyncio.to_thread(self.user_manager.get_active_pending_orders)
            if not pending_orders:
                return []

            matched: List[Dict[str, Any]] = []
            if optional_tx_hash.strip():
                tx_match = await self.verify_specific_tx_hash(optional_tx_hash.strip(), pending_orders)
                if tx_match:
                    matched.append(tx_match)
                    pending_orders = await asyncio.to_thread(self.user_manager.get_active_pending_orders)
                    if not pending_orders:
                        return matched

            client = self._get_http_client()
            tasks = [
                self._scan_single_chain(client, chain_cfg, pending_orders)
                for chain_cfg in SUPPORTED_CHAINS
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    matched.extend(r)

            await asyncio.to_thread(self._save_persisted_cursors)
            self.last_scan_ts = int(time.time())
            self.scan_cycles_count += 1
            return matched
