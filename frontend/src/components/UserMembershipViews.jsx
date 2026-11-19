import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  AlertCircle,
  BarChart2,
  Check,
  Clock,
  Copy,
  CreditCard,
  Edit3,
  Key,
  Lock,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  Share2,
  Shield,
  ShoppingCart,
  Sliders,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
  Wallet,
  X,
} from 'lucide-react';

const DEFAULT_WALLET_ADDRESS = '0x8D550B8c462310126BA26d85A4a1f779Cd56A9e0';
const DEFAULT_SUPPORTED_CHAINS = [
  'Ethereum',
  'Optimism',
  'Base',
  'Arbitrum',
  'Polygon',
  'Ink',
  'Linea',
  'Avalanche',
  'BNB Smart Chain',
  'Robinhood Chain',
];
const DEFAULT_WARNING_TEXT =
  '此地址支持 Ethereum, Optimism, Base, Arbitrum, Polygon, Ink, Linea, Avalanche, BNB Smart Chain, Robinhood Chain 上的资产。发送不兼容的资产可能导致永久丢失。';

function formatCountdown(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

// ==================== 1. AUTH LOGIN / REGISTER MODAL ====================
export function AuthModal({ isOpen, onClose, initialMode = 'login', onAuthSuccess, showToast }) {
  const [mode, setMode] = useState(initialMode);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setMode(initialMode);
  }, [initialMode]);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      showToast('请填写用户名和密码', 'error');
      return;
    }
    setLoading(true);
    try {
      const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/register';
      const payload =
        mode === 'login'
          ? { username: username.trim(), password }
          : { username: username.trim(), password, email: email.trim() };
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || data.message || '认证失败', 'error');
      } else {
        setUsername('');
        setPassword('');
        setEmail('');
        onAuthSuccess(data.token, data.user, data.message);
        onClose();
      }
    } catch (err) {
      showToast('网络请求异常，请稍后重试', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 backdrop-blur-sm p-3 sm:p-4">
      <div className="surface-card w-full max-w-md max-h-[92vh] overflow-y-auto p-5 sm:p-7 relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 sm:top-6 sm:right-6 w-8 h-8 rounded-full surface-subtle flex items-center justify-center text-dim hover:text-main transition"
        >
          <X className="w-4 h-4" strokeWidth={1.75} />
        </button>

        {/* Header */}
        <div className="flex items-center gap-3 mb-5 sm:mb-6 pr-8">
          <div className="icon-box w-10 h-10 sm:w-11 sm:h-11">
            <Shield className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <h3 className="text-base font-bold text-main">
              {mode === 'login' ? '登录量化狙击系统' : '注册新用户账号'}
            </h3>
            <p className="text-xs text-dim mt-0.5 leading-snug">
              注册登录可查看实盘系统 · 开通会员可使用实盘系统
            </p>
          </div>
        </div>

        {/* Mode Switcher Pill */}
        <div className="grid grid-cols-2 p-1 rounded-2xl surface-subtle mb-5 text-xs font-semibold">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={`py-2 rounded-xl transition ${mode === 'login' ? 'bg-white dark:bg-slate-800 text-main shadow-sm' : 'text-dim hover:text-main'
              }`}
          >
            账号登录
          </button>
          <button
            type="button"
            onClick={() => setMode('register')}
            className={`py-2 rounded-xl transition ${mode === 'register' ? 'bg-white dark:bg-slate-800 text-main shadow-sm' : 'text-dim hover:text-main'
              }`}
          >
            免费注册账号
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4 text-xs">
          <div>
            <label className="block text-main font-semibold mb-1.5">用户名</label>
            <input
              type="text"
              required
              minLength={3}
              maxLength={32}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="输入登录用户名 (至少3位)"
              className="w-full input-field px-4 py-3 text-base sm:text-xs font-mono"
            />
          </div>

          {mode === 'register' && (
            <div>
              <label className="block text-main font-semibold mb-1.5">注册邮箱 (选填)</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="输入联系邮箱，如 trader@proton.me"
                className="w-full input-field px-4 py-3 text-base sm:text-xs font-mono"
              />
            </div>
          )}

          <div>
            <label className="block text-main font-semibold mb-1.5">登录密码 (至少6位)</label>
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入登录密码"
              className="w-full input-field px-4 py-3 text-base sm:text-xs font-mono"
            />
          </div>

          <div className="pt-1">
            <button type="submit" disabled={loading} className="btn-primary-pill w-full py-3 text-xs">
              {loading ? '正在验证...' : mode === 'login' ? '立即登录系统' : '完成注册并登录'}
            </button>
          </div>
          {mode === 'register' && (
            <div className="text-[11px] text-dim text-center pt-1">
              系统已启用 IP 防刷限制：每个 IP 地址仅允许注册一个账户
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

// ==================== 2. MEMBERSHIP PURCHASE, WEB3 PAYMENT & ACCOUNT SECURITY VIEW ====================
export function MembershipCenterView({
  currentUser,
  authToken,
  onUserUpdated,
  onTokenUpdated,
  onOpenAuthModal,
  onSwitchToLive,
  showToast,
}) {
  const [plans, setPlans] = useState([]);
  const [orders, setOrders] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [selectedChain, setSelectedChain] = useState('MULTI_EVM');
  const [activeOrder, setActiveOrder] = useState(null);
  const [paymentInfo, setPaymentInfo] = useState({
    wallet_address: DEFAULT_WALLET_ADDRESS,
    qr_svg_uri: '',
    supported_chains: DEFAULT_SUPPORTED_CHAINS,
    warning_text: DEFAULT_WARNING_TEXT,
    timeout_minutes: 30,
    timeout_seconds: 1800,
  });
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const [txHashInput, setTxHashInput] = useState('');
  const [checkingChain, setCheckingChain] = useState(false);
  const [redeemCode, setRedeemCode] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Password Change State
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [pwdSaving, setPwdSaving] = useState(false);

  const onUserUpdatedRef = useRef(onUserUpdated);
  onUserUpdatedRef.current = onUserUpdated;
  const showToastRef = useRef(showToast);
  showToastRef.current = showToast;

  // Clear stale pending order immediately on account switch or logout
  useEffect(() => {
    setActiveOrder(null);
    setRemainingSeconds(0);
    setOrders([]);
  }, [authToken]);

  const fetchMembershipData = useCallback(async () => {
    try {
      const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
      const res = await fetch('/api/membership/plans', { headers });
      if (res.ok) {
        const data = await res.json();
        setPlans(data.plans || []);
        setOrders(data.orders || []);
        if (data.payment_info) {
          setPaymentInfo(data.payment_info);
        }
        if (data.active_order && data.active_order.status === 'PENDING') {
          setActiveOrder(data.active_order);
          setRemainingSeconds(Number(data.active_order.remaining_seconds || 0));
        } else {
          setActiveOrder(null);
          setRemainingSeconds(0);
        }
        if (data.plans?.length > 0) {
          setSelectedPlan((prev) => prev || data.plans[0]);
        }
      }
    } catch (e) {
      // ignore
    }
  }, [authToken]);

  useEffect(() => {
    fetchMembershipData();
  }, [fetchMembershipData]);

  // 1-second local countdown timer anchored to server remaining_seconds (immune to client clock skew)
  const activeOrderNo = activeOrder?.order_no || '';
  const activeOrderStatus = activeOrder?.status || '';
  const activeOrderInitRem = Number(activeOrder?.remaining_seconds ?? 0);
  useEffect(() => {
    if (!activeOrderNo || activeOrderStatus !== 'PENDING') return undefined;
    const deadlineMs = Date.now() + Math.max(0, activeOrderInitRem) * 1000;
    const calcRemaining = () => Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000));
    setRemainingSeconds(calcRemaining());

    const timer = setInterval(() => {
      const rem = calcRemaining();
      setRemainingSeconds(rem);
      if (rem <= 0) {
        setActiveOrder((prev) => (prev ? { ...prev, status: 'EXPIRED', remaining_seconds: 0 } : null));
        showToastRef.current?.('支付时间已满 30 分钟，该笔订单与链上监控已自动关闭', 'error');
        fetchMembershipData();
      }
    }, 1000);

    return () => clearInterval(timer);
  }, [activeOrderNo, activeOrderStatus, activeOrderInitRem, fetchMembershipData]);

  // Poll backend every 4 seconds while PENDING — decoupled from parent 350ms WS re-renders!
  useEffect(() => {
    if (!activeOrderNo || activeOrderStatus !== 'PENDING' || !authToken) return undefined;
    const poll = setInterval(async () => {
      try {
        const res = await fetch(`/api/membership/orders/${encodeURIComponent(activeOrderNo)}`, {
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.orders) setOrders(data.orders);
        if (data.order) {
          if (data.order.status === 'PAID') {
            setActiveOrder(data.order);
            if (data.user) onUserUpdatedRef.current?.(data.user);
            showToastRef.current?.(
              `链上到账自动确认成功！已为您激活【${data.order.plan_name}】并解锁实盘交易权限`,
              'success'
            );
          } else if (data.order.status !== 'PENDING') {
            setActiveOrder(data.order);
          }
        }
      } catch (e) {
        // ignore
      }
    }, 4000);

    return () => clearInterval(poll);
  }, [activeOrderNo, activeOrderStatus, authToken]);

  const handleCreatePaymentOrder = async (planOverride, forceNew = false) => {
    if (!currentUser) {
      showToast('请先注册或登录账号后再开通会员', 'error');
      onOpenAuthModal('register');
      return;
    }
    const targetPlan = planOverride || selectedPlan;
    if (!targetPlan) return;
    setSelectedPlan(targetPlan);
    setSubmitting(true);
    try {
      const res = await fetch('/api/membership/purchase', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          plan_id: targetPlan.id,
          chain_network: selectedChain,
          force_new: forceNew,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || data.message || '创建支付订单失败', 'error');
      } else {
        showToast(data.message, 'info');
        if (data.payment_info) setPaymentInfo(data.payment_info);
        if (data.order) {
          setActiveOrder(data.order);
          setRemainingSeconds(data.order.remaining_seconds || 1800);
        }
        if (data.orders) setOrders(data.orders);
        if (data.user) onUserUpdated(data.user);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleManualCheckOnChain = async () => {
    if (!activeOrder || !authToken) return;
    setCheckingChain(true);
    try {
      const res = await fetch(`/api/membership/orders/${encodeURIComponent(activeOrder.order_no)}/check`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ tx_hash: txHashInput.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        if (data.order) setActiveOrder(data.order);
        if (data.orders) setOrders(data.orders);
        if (data.user) onUserUpdated(data.user);
        showToast(data.message, data.paid ? 'success' : 'info');
        if (data.paid) setTxHashInput('');
      } else {
        showToast(data.detail || '查验失败', 'error');
      }
    } finally {
      setCheckingChain(false);
    }
  };

  const handleCancelOrder = async (orderNo) => {
    if (!authToken) return;
    const res = await fetch(`/api/membership/orders/${encodeURIComponent(orderNo)}/cancel`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'info');
      setActiveOrder(null);
      if (data.orders) setOrders(data.orders);
    } else {
      showToast(data.detail || '取消订单失败', 'error');
    }
  };

  const handleRedeem = async (e) => {
    e.preventDefault();
    if (!currentUser) {
      showToast('请先登录账号后再使用激活码兑换会员', 'error');
      onOpenAuthModal('login');
      return;
    }
    if (!redeemCode.trim()) {
      showToast('请输入会员激活码', 'error');
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch('/api/membership/redeem', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ code: redeemCode.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || data.message || '兑换失败', 'error');
      } else {
        showToast(data.message, 'success');
        setRedeemCode('');
        if (data.user) onUserUpdated(data.user);
        if (data.orders) setOrders(data.orders);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    if (!currentUser) {
      showToast('请先登录账号', 'error');
      onOpenAuthModal('login');
      return;
    }
    if (!oldPassword || newPassword.length < 6) {
      showToast('请输入当前密码及至少6位的新密码', 'error');
      return;
    }
    setPwdSaving(true);
    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || '密码修改失败', 'error');
      } else {
        showToast(data.message || '密码修改成功', 'success');
        if (data.token && onTokenUpdated) {
          onTokenUpdated(data.token);
        }
        setOldPassword('');
        setNewPassword('');
      }
    } finally {
      setPwdSaving(false);
    }
  };

  const copyText = async (txt, label) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(txt);
        showToast(`已复制${label}: ${txt}`, 'success');
        return;
      }
      const ta = document.createElement('textarea');
      ta.value = txt;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) {
        showToast(`已复制${label}: ${txt}`, 'success');
      } else {
        showToast(`复制${label}失败，请手动选中复制`, 'error');
      }
    } catch (e) {
      showToast(`复制${label}失败，请手动选中复制`, 'error');
    }
  };

  const walletAddress = paymentInfo.wallet_address || DEFAULT_WALLET_ADDRESS;
  const supportedChains = paymentInfo.supported_chains || DEFAULT_SUPPORTED_CHAINS;
  const warningText = paymentInfo.warning_text || DEFAULT_WARNING_TEXT;
  const isOrderPending = activeOrder && activeOrder.status === 'PENDING' && remainingSeconds > 0;
  const progressPct = isOrderPending
    ? Math.min(100, Math.max(0, (remainingSeconds / (paymentInfo.timeout_seconds || 1800)) * 100))
    : 0;

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* 1. Top Page Header Banner Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-main">会员中心与 Web3 多链自动收银台</h2>
          <p className="text-xs text-dim mt-1">
            注册用户可查看实盘系统实时行情，通过多链转账精确尾数金额或兑换激活码开通 VIP 后即可启动毫秒级实盘狙击
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-2.5 w-full sm:w-auto">
          {!currentUser ? (
            <>
              <button onClick={() => onOpenAuthModal('login')} className="btn-soft-pill px-4 sm:px-5 py-2 sm:py-2.5 text-xs">
                账号登录
              </button>
              <button onClick={() => onOpenAuthModal('register')} className="btn-primary-pill px-4 sm:px-5 py-2 sm:py-2.5 text-xs">
                免费注册账号
              </button>
            </>
          ) : currentUser.can_use_live ? (
            <button onClick={onSwitchToLive} className="btn-primary-pill w-full sm:w-auto px-5 py-2.5 text-xs">
              进入实盘交易通道
            </button>
          ) : (
            <button onClick={() => onOpenAuthModal('login')} className="btn-soft-pill w-full sm:w-auto px-5 py-2.5 text-xs">
              切换登录账号
            </button>
          )}
        </div>
      </div>

      {/* 2. Account & Security Overview Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5">
              <Shield className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
              <h3 className="text-sm sm:text-base font-bold text-main">账户与实盘权限状态</h3>
            </div>
            <p className="text-xs text-dim mt-1">
              管理登录账户、VIP 会员等级、实盘访问权限与安全密钥
            </p>
          </div>
          {currentUser && (
            <button
              onClick={() => onOpenAuthModal('login')}
              className="btn-primary-pill w-full sm:w-auto px-5 py-2 text-xs"
            >
              切换其他账号 ({currentUser.username})
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mt-4 sm:mt-5">
          <div className="surface-subtle p-4 sm:p-5">
            <div className="text-xs text-dim font-medium">当前登录账号 / 邮箱</div>
            <div className="text-sm sm:text-base font-bold text-main mt-1.5 truncate font-mono">
              {currentUser ? currentUser.email || currentUser.username : '未登录 (访客模式)'}
            </div>
          </div>

          <div className="surface-subtle p-4 sm:p-5">
            <div className="text-xs text-dim font-medium">当前会员等级</div>
            <div className="text-sm sm:text-base font-bold text-main mt-1.5">
              {currentUser ? currentUser.vip_label : '未开通'}
            </div>
          </div>

          <div className="surface-subtle p-4 sm:p-5">
            <div className="text-xs text-dim font-medium">实盘系统访问权限</div>
            <div
              className={`text-sm sm:text-base font-bold font-mono mt-1.5 ${currentUser?.can_use_live
                ? 'text-lime-600 dark:text-emerald-400'
                : currentUser
                  ? 'text-amber-600 dark:text-amber-400'
                  : 'text-dim'
                }`}
            >
              {currentUser?.can_use_live
                ? '可查看 + 可使用实盘'
                : currentUser
                  ? '仅可查看实盘 (需开VIP)'
                  : '实盘已隐藏 (需注册)'}
            </div>
          </div>

          <div className="surface-subtle p-4 sm:p-5">
            <div className="text-xs text-dim font-medium">会员到期时间</div>
            <div className="text-sm sm:text-base font-bold text-main mt-1.5">
              {currentUser ? currentUser.vip_expires_str : '未开通'}
            </div>
          </div>
        </div>
      </div>

      {/* 3. 4-Step Permission Rules Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7">
        <h3 className="text-sm sm:text-base font-bold text-main mb-4 sm:mb-5">Web3 链上自动开通会员流程</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-5">
          <div className="flex items-center gap-3 sm:gap-3.5">
            <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
              <UserPlus className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">1. 注册并选择套餐</div>
              <div className="text-xs text-dim mt-0.5">注册登录即可查看实盘行情</div>
            </div>
          </div>

          <div className="flex items-center gap-3 sm:gap-3.5">
            <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
              <QrCode className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">2. 生成随机3位小数金额</div>
              <div className="text-xs text-dim mt-0.5">如 99U 订单生成 98.123 识别金额</div>
            </div>
          </div>

          <div className="flex items-center gap-3 sm:gap-3.5">
            <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
              <Wallet className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">3. 30分钟内多链转账</div>
              <div className="text-xs text-dim mt-0.5">支持 10 条 EVM 公链扫码转账</div>
            </div>
          </div>

          <div className="flex items-center gap-3 sm:gap-3.5">
            <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
              <CreditCard className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">4. 链上监控秒级开通</div>
              <div className="text-xs text-dim mt-0.5">精确金额到账后自动激活 VIP</div>
            </div>
          </div>
        </div>
      </div>

      {/* 4. Membership Plans Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3.5 sm:gap-4">
        {plans.map((plan) => {
          const isSelected = selectedPlan?.id === plan.id;
          return (
            <div
              key={plan.id}
              onClick={() => setSelectedPlan(plan)}
              className={`surface-card p-5 sm:p-6 cursor-pointer transition flex flex-col justify-between relative ${isSelected ? 'ring-2 ring-slate-900 dark:ring-sky-400' : 'hover:opacity-95'
                }`}
            >
              {plan.badge_text && (
                <span className="absolute top-5 right-5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold surface-subtle text-main">
                  {plan.badge_text}
                </span>
              )}

              <div>
                <div className="text-base font-bold text-main">{plan.name}</div>
                <div className="text-xs text-dim mt-0.5">
                  有效期：{plan.duration_days >= 3650 ? '永久有效 (终身)' : `${plan.duration_days} 天`}
                </div>

                <div className="mt-5 flex items-baseline gap-2">
                  <span className="text-3xl font-bold font-mono text-main">
                    ${plan.price_usdt}
                  </span>
                  <span className="text-xs font-semibold text-dim">USDT</span>
                  {plan.original_price_usdt > plan.price_usdt && (
                    <span className="text-xs text-dim line-through font-mono">
                      ${plan.original_price_usdt}
                    </span>
                  )}
                </div>

                <div className="mt-5 pt-4 border-t border-line space-y-2.5 text-xs text-sub">
                  {(plan.features || []).map((feat, idx) => (
                    <div key={idx} className="flex items-start gap-2">
                      <Check className="w-4 h-4 text-main shrink-0 mt-0.5" strokeWidth={2} />
                      <span>{feat}</span>
                    </div>
                  ))}
                </div>
              </div>

              <button
                type="button"
                disabled={submitting}
                onClick={(e) => {
                  e.stopPropagation();
                  handleCreatePaymentOrder(plan, false);
                }}
                className={`mt-6 w-full py-2.5 text-xs ${isSelected ? 'btn-primary-pill' : 'btn-soft-pill'
                  }`}
              >
                {isOrderPending && activeOrder?.plan_id === plan.id
                  ? `查看支付订单 (${activeOrder.pay_amount_str} U)`
                  : '选择并生成链上支付单'}
              </button>
            </div>
          );
        })}
      </div>

      {/* 5. Web3 On-Chain Payment & QR Code Monitor Section + Activation Code Redemption */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-5">
        {/* Left 8 Cols: Web3 Multi-Chain Payment System */}
        <div className="lg:col-span-8 surface-card p-4 sm:p-6 lg:p-7 space-y-5 sm:space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-3 sm:gap-4">
            <div>
              <div className="flex items-center gap-2.5">
                <Wallet className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                <h3 className="text-sm sm:text-base font-bold text-main">Web3 多链自动监控支付系统</h3>
              </div>
              <p className="text-xs text-dim mt-1">
                系统为每笔订单生成唯一下浮 3 位小数金额（如 99 USDT 显示 98.xxx），链上监控检测到该精确金额到达收款地址即自动完成支付
              </p>
            </div>

            {isOrderPending && (
              <div className="flex items-center gap-2 px-3.5 sm:px-4 py-1.5 sm:py-2 rounded-full bg-amber-500/12 text-amber-600 dark:text-amber-400 text-xs font-mono font-bold">
                <Clock className="w-4 h-4 animate-pulse shrink-0" strokeWidth={2} />
                <span>剩余支付时间 {formatCountdown(remainingSeconds)}</span>
              </div>
            )}
          </div>

          {/* Supported Networks & Asset Warning Box */}
          <div className="surface-subtle p-3.5 sm:p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-bold text-main">支持监控的 10 条公链网络（点击可标记偏好网络，全链同步监听）：</span>
              <span className="text-[11px] font-mono text-emerald-600 dark:text-emerald-400 font-semibold flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                10链 RPC 实时在线
              </span>
            </div>

            <div className="flex flex-wrap gap-1.5 sm:gap-2">
              <button
                type="button"
                onClick={() => setSelectedChain('MULTI_EVM')}
                className={`px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-semibold transition ${selectedChain === 'MULTI_EVM' ? 'btn-primary-pill' : 'bg-white dark:bg-slate-800 border border-line text-sub hover:text-main'
                  }`}
              >
                全网络自动识别 (10链通用)
              </button>
              {supportedChains.map((net) => (
                <button
                  key={net}
                  type="button"
                  onClick={() => setSelectedChain(net)}
                  className={`px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition ${selectedChain === net ? 'btn-primary-pill' : 'bg-white dark:bg-slate-800 border border-line text-sub hover:text-main'
                    }`}
                >
                  {net}
                </button>
              ))}
            </div>

            <div className="pt-2 border-t border-line flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400 font-medium leading-relaxed">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" strokeWidth={2} />
              <span>{warningText}</span>
            </div>
          </div>

          {/* Address + QR Code + Order Exact Amount Box */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-4 sm:gap-5 items-center">
            {/* QR Code Box */}
            <div className="md:col-span-4 surface-subtle p-4 sm:p-5 flex flex-col items-center justify-center text-center">
              <div className="text-xs font-bold text-main mb-2.5">Web3 收款地址二维码</div>
              <div className="w-36 h-36 sm:w-44 sm:h-44 bg-white p-2.5 rounded-2xl border border-line flex items-center justify-center shadow-sm">
                {paymentInfo.qr_svg_uri ? (
                  <img
                    src={paymentInfo.qr_svg_uri}
                    alt="Web3 Payment Address QR"
                    className="w-full h-full object-contain"
                  />
                ) : (
                  <QrCode className="w-20 h-20 text-slate-800" strokeWidth={1.5} />
                )}
              </div>
              <div className="text-[11px] text-dim mt-2.5">
                使用钱包或交易所扫码转账
              </div>
            </div>

            {/* Order Payment Details */}
            <div className="md:col-span-8 space-y-3.5 sm:space-y-4 text-xs">
              {/* Receiving Wallet Address */}
              <div className="surface-subtle p-3.5 sm:p-4">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5">
                  <span className="text-dim font-medium">官方唯一 Web3 多链收款地址</span>
                  <button
                    type="button"
                    onClick={() => copyText(walletAddress, '收款地址')}
                    className="btn-soft-pill px-3 py-1 text-[11px]"
                  >
                    <Copy className="w-3 h-3" strokeWidth={1.75} />
                    <span>复制地址</span>
                  </button>
                </div>
                <div className="font-mono font-bold text-xs sm:text-sm text-main break-all select-all">
                  {walletAddress}
                </div>
              </div>

              {isOrderPending ? (
                <div className="surface-subtle p-4 sm:p-5 space-y-4 ring-2 ring-emerald-500/40">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-dim">
                        当前监控订单：<span className="font-mono font-bold text-main">{activeOrder.order_no}</span>（{activeOrder.plan_name}）
                      </div>
                      <div className="text-[11px] text-emerald-600 dark:text-emerald-400 font-semibold mt-0.5 flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                        链上监控运行中：等待精确到小数点后3位的金额到账
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => copyText(activeOrder.pay_amount_str, '精确转账金额')}
                      className="btn-primary-pill w-full sm:w-auto px-4 py-1.5 text-xs"
                    >
                      <Copy className="w-3.5 h-3.5" strokeWidth={1.75} />
                      <span>复制精确金额 {activeOrder.pay_amount_str}</span>
                    </button>
                  </div>

                  <div className="flex flex-wrap items-baseline justify-between gap-3 sm:gap-4 pt-2 border-t border-line">
                    <div>
                      <div className="text-dim">请务必精确转账以下金额（含后3位随机识别码）：</div>
                      <div className="mt-1 flex flex-wrap items-baseline gap-2 font-mono">
                        <span className="text-2xl sm:text-3xl font-extrabold text-emerald-600 dark:text-emerald-400">
                          {activeOrder.pay_amount_str}
                        </span>
                        <span className="text-xs sm:text-sm font-bold text-main">USDT / USDC</span>
                        <span className="text-xs text-dim line-through">
                          原价 ${activeOrder.base_amount_str}
                        </span>
                      </div>
                    </div>

                    <div className="sm:text-right font-mono">
                      <div className="text-dim text-[11px]">30分钟自动关闭倒计时</div>
                      <div className="text-lg sm:text-xl font-bold text-amber-600 dark:text-amber-400 mt-0.5">
                        {formatCountdown(remainingSeconds)}
                      </div>
                    </div>
                  </div>

                  {/* Countdown Progress Bar */}
                  <div className="w-full h-2 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 transition-all duration-1000"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>

                  {/* Optional TX Hash immediate check + Order Controls */}
                  <div className="pt-2 space-y-2.5">
                    <div className="flex flex-col sm:flex-row gap-2">
                      <input
                        type="text"
                        value={txHashInput}
                        onChange={(e) => setTxHashInput(e.target.value)}
                        placeholder="选填：粘贴转账交易哈希 (0x...) 可加速秒级核验"
                        className="flex-1 input-field px-3.5 py-2.5 text-base sm:text-xs font-mono"
                      />
                      <button
                        type="button"
                        disabled={checkingChain}
                        onClick={handleManualCheckOnChain}
                        className="btn-primary-pill px-5 py-2.5 text-xs whitespace-nowrap"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${checkingChain ? 'animate-spin' : ''}`} strokeWidth={1.75} />
                        <span>{checkingChain ? '正在扫描链上区块...' : '我已转账 · 立即核验'}</span>
                      </button>
                    </div>

                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 text-[11px] text-dim pt-1">
                      <span>提示：若交易所提币含手续费，请确保实际到账金额为 {activeOrder.pay_amount_str}</span>
                      <div className="flex items-center gap-3 shrink-0">
                        <button
                          type="button"
                          onClick={() => handleCreatePaymentOrder(selectedPlan, true)}
                          className="text-sub hover:text-main underline"
                        >
                          重新生成随机金额
                        </button>
                        <button
                          type="button"
                          onClick={() => handleCancelOrder(activeOrder.order_no)}
                          className="text-rose-500 hover:text-rose-600 underline"
                        >
                          取消此笔订单
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="surface-subtle p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
                  <div>
                    <div className="text-dim">当前选中会员套餐</div>
                    <div className="text-sm sm:text-base font-bold text-main mt-0.5">
                      {selectedPlan ? `${selectedPlan.name}（原价 $${Number(selectedPlan.price_usdt).toFixed(2)} USDT）` : '请先选择上方会员套餐'}
                    </div>
                    <div className="text-[11px] text-dim mt-1">
                      点击右侧按钮后将自动生成 <span className="font-mono font-semibold text-main">{selectedPlan ? `${Math.max(1, Math.round(selectedPlan.price_usdt) - 1)}.xxx` : '98.xxx'}</span> 随机3位小数专属应付金额并开启 30 分钟链上监控
                    </div>
                  </div>

                  <button
                    type="button"
                    disabled={submitting || !selectedPlan}
                    onClick={() => handleCreatePaymentOrder(selectedPlan, true)}
                    className="btn-primary-pill w-full sm:w-auto px-6 py-3 text-xs"
                  >
                    <CreditCard className="w-4 h-4" strokeWidth={1.75} />
                    <span>{submitting ? '正在生成订单...' : '生成精确金额并开启链上监控'}</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right 4 Cols: Activation Code Redemption */}
        <div className="lg:col-span-4 surface-card p-4 sm:p-6 lg:p-7 flex flex-col justify-between space-y-5">
          <div>
            <div className="flex items-center gap-2.5">
              <Key className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
              <h3 className="text-sm sm:text-base font-bold text-main">卡密 / 激活码兑换会员</h3>
            </div>
            <p className="text-xs text-dim mt-1">
              如果您持有管理员发放的 VIP 激活码，可在此直接兑换并立即解锁实盘交易权限
            </p>

            <form onSubmit={handleRedeem} className="mt-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-main mb-1.5">激活码 (VIP Code)</label>
                <input
                  type="text"
                  value={redeemCode}
                  onChange={(e) => setRedeemCode(e.target.value.toUpperCase())}
                  placeholder="输入专属 VIP 激活码"
                  className="w-full input-field px-4 py-3 text-base sm:text-xs font-mono uppercase"
                />
              </div>
              <button type="submit" disabled={submitting} className="btn-primary-pill w-full py-3 text-xs">
                <Key className="w-3.5 h-3.5" strokeWidth={1.75} />
                <span>立即兑换激活会员</span>
              </button>
            </form>
          </div>

          <div className="surface-subtle p-4 text-xs space-y-2 text-dim">
            <div className="font-semibold text-main">链上自动到账说明：</div>
            <p>• 每笔订单有效期为 30 分钟，超时未到账将自动关闭订单与链上监控。</p>
            <p>• 请务必按照订单显示的后 3 位小数精确金额转账，系统通过匹配该 3 位小数自动完成入账。</p>
          </div>
        </div>
      </div>

      {/* 6. Change Password Card */}
      <form onSubmit={handleChangePassword} className="surface-card p-4 sm:p-6 lg:p-7 space-y-4 sm:space-y-5">
        <div className="flex items-center gap-2.5">
          <Lock className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
          <h3 className="text-sm sm:text-base font-bold text-main">修改登录密码</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5 text-xs">
          <div>
            <label className="block text-main font-semibold mb-2">旧密码</label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              placeholder="输入当前登录密码"
              className="w-full input-field px-4 py-3 text-base sm:text-xs"
            />
          </div>
          <div>
            <label className="block text-main font-semibold mb-2">新密码 (至少6位)</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="输入新密码"
              className="w-full input-field px-4 py-3 text-base sm:text-xs"
            />
          </div>
        </div>

        <div>
          <button type="submit" disabled={pwdSaving} className="btn-primary-pill w-full sm:w-auto px-6 py-2.5 text-xs">
            {pwdSaving ? '保存中...' : '保存新密码'}
          </button>
        </div>
      </form>

      {/* 7. User Order History */}
      <div className="surface-card p-4 sm:p-6 lg:p-7">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h3 className="text-sm sm:text-base font-bold text-main">我的会员订单与链上监控记录</h3>
          <button onClick={fetchMembershipData} className="btn-soft-pill px-4 py-1.5 text-xs">
            <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>刷新记录</span>
          </button>
        </div>

        {/* Mobile Card View (< md) */}
        <div className="md:hidden space-y-2.5">
          {orders.length === 0 ? (
            <div className="py-8 text-center text-xs text-dim">暂无会员订单记录</div>
          ) : (
            orders.map((o) => (
              <div key={`mob-ord-${o.id}`} className="surface-subtle p-3.5 space-y-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-bold text-main truncate">{o.order_no}</span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] font-semibold shrink-0 ${o.status === 'PAID'
                      ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                      : o.status === 'PENDING'
                        ? 'bg-amber-500/12 text-amber-600 dark:text-amber-400'
                        : 'surface-card text-dim'
                      }`}
                  >
                    {o.status === 'PAID'
                      ? '已自动开通'
                      : o.status === 'PENDING'
                        ? `监控中 (${formatCountdown(o.remaining_seconds)})`
                        : o.status === 'EXPIRED'
                          ? '超时已关闭'
                          : o.status === 'CANCELLED'
                            ? '已取消'
                            : '已拒绝'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sub">
                  <span>{o.plan_name} (+{o.duration_days}天)</span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                    ${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-dim truncate" title={o.tx_hash}>
                  {o.chain_network === 'MULTI_EVM' ? '10链自动监控' : o.chain_network || o.pay_method} · {o.tx_hash}
                </div>
                <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-line">
                  <span className="text-[11px] font-mono text-dim">{o.created_at}</span>
                  {o.status === 'PENDING' && (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setActiveOrder(o);
                          setRemainingSeconds(o.remaining_seconds || 0);
                          window.scrollTo({ top: 380, behavior: 'smooth' });
                        }}
                        className="btn-primary-pill px-3 py-1 text-[11px]"
                      >
                        继续支付
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCancelOrder(o.order_no)}
                        className="btn-soft-pill px-2.5 py-1 text-[11px] text-rose-500"
                      >
                        取消
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Tablet / Desktop Table (>= md) */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full min-w-[780px] text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] text-dim">
                <th className="py-3 px-3">订单编号</th>
                <th className="py-3 px-3">会员套餐名称</th>
                <th className="py-3 px-3">授权天数</th>
                <th className="py-3 px-3">精确应付金额 (USDT)</th>
                <th className="py-3 px-3">支付网络 / 方式</th>
                <th className="py-3 px-3">链上哈希 / 监控状态</th>
                <th className="py-3 px-3">状态</th>
                <th className="py-3 px-3">创建时间</th>
                <th className="py-3 px-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y border-line font-mono">
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-10 text-center text-dim font-sans">
                    暂无会员订单记录
                  </td>
                </tr>
              ) : (
                orders.map((o) => (
                  <tr key={o.id}>
                    <td className="py-3 px-3 text-main font-semibold">{o.order_no}</td>
                    <td className="py-3 px-3 font-sans text-main font-medium">{o.plan_name}</td>
                    <td className="py-3 px-3 text-sub">+{o.duration_days} 天</td>
                    <td className="py-3 px-3 text-emerald-600 dark:text-emerald-400 font-bold">
                      ${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}
                    </td>
                    <td className="py-3 px-3 font-sans text-sub">
                      {o.chain_network === 'MULTI_EVM' ? '10链自动监控' : o.chain_network || o.pay_method}
                    </td>
                    <td className="py-3 px-3 text-dim max-w-xs truncate" title={o.tx_hash}>
                      {o.tx_hash}
                    </td>
                    <td className="py-3 px-3 font-sans">
                      <span
                        className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${o.status === 'PAID'
                          ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                          : o.status === 'PENDING'
                            ? 'bg-amber-500/12 text-amber-600 dark:text-amber-400'
                            : 'surface-subtle text-dim'
                          }`}
                      >
                        {o.status === 'PAID'
                          ? '已自动开通'
                          : o.status === 'PENDING'
                            ? `链上监控中 (${formatCountdown(o.remaining_seconds)})`
                            : o.status === 'EXPIRED'
                              ? '30分钟超时关闭'
                              : o.status === 'CANCELLED'
                                ? '已取消'
                                : '已拒绝'}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-dim">{o.created_at}</td>
                    <td className="py-3 px-3 text-right font-sans">
                      {o.status === 'PENDING' ? (
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setActiveOrder(o);
                              setRemainingSeconds(o.remaining_seconds || 0);
                              window.scrollTo({ top: 380, behavior: 'smooth' });
                            }}
                            className="btn-primary-pill px-3 py-1 text-[11px]"
                          >
                            继续支付
                          </button>
                          <button
                            type="button"
                            onClick={() => handleCancelOrder(o.order_no)}
                            className="btn-soft-pill px-2.5 py-1 text-[11px] text-rose-500"
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <span className="text-dim">--</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ==================== 3. ADMIN USER & MEMBERSHIP MANAGEMENT VIEW ====================
export function AdminSystemView({ currentUser, authToken, onCurrentUserRefresh, onOpenAuthModal, showToast }) {
  const [subTab, setSubTab] = useState('users'); // 'users' | 'orders' | 'plans' | 'codes'
  const [dashboard, setDashboard] = useState({
    stats: {
      total_users: 0,
      active_vip_users: 0,
      non_vip_users: 0,
      disabled_users: 0,
      total_orders: 0,
      paid_orders_count: 0,
      pending_orders_count: 0,
      expired_orders_count: 0,
      total_revenue_usdt: 0,
      unused_codes_count: 0,
      total_codes_count: 0,
    },
    users: [],
    orders: [],
    vip_codes: [],
    plans: [],
  });
  const [userSearch, setUserSearch] = useState('');
  const [orderFilter, setOrderFilter] = useState('ALL');

  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    email: '',
    role: 'user',
    vip_days: 0,
    vip_tier: '季度旗舰版会员',
  });

  const [grantTarget, setGrantTarget] = useState(null);
  const [grantDays, setGrantDays] = useState(30);
  const [grantTier, setGrantTier] = useState('月度专业版会员');

  const [editUserTarget, setEditUserTarget] = useState(null);
  const [editUserForm, setEditUserForm] = useState({
    email: '',
    role: 'user',
    new_password: '',
  });

  const [editingPlan, setEditingPlan] = useState(null);
  const [adminBusy, setAdminBusy] = useState(false);
  const adminBusyRef = useRef(false);
  const [codeGenForm, setCodeGenForm] = useState({
    plan_name: '季度旗舰版会员',
    duration_days: 90,
    count: 5,
  });

  const loadAdminDashboard = useCallback(async () => {
    if (!authToken || !currentUser?.is_admin) return;
    const res = await fetch('/api/admin/dashboard', {
      headers: { Authorization: `Bearer ${authToken}` },
    });
    if (res.ok) {
      const data = await res.json();
      setDashboard(data);
    }
  }, [authToken, currentUser]);

  useEffect(() => {
    loadAdminDashboard();
  }, [loadAdminDashboard]);

  if (!currentUser || !currentUser.is_admin) {
    return (
      <div className="surface-card p-12 text-center space-y-4">
        <div className="icon-box w-14 h-14 mx-auto">
          <Lock className="w-6 h-6" strokeWidth={1.75} />
        </div>
        <h3 className="text-lg font-bold text-main">需要后台管理员权限</h3>
        <p className="text-xs text-dim max-w-md mx-auto">
          后台管理系统（含后台用户管理系统与后台会员管理系统）仅对授权管理员账号开放。请先使用管理员账号登录。
        </p>
        <button onClick={() => onOpenAuthModal('login')} className="btn-primary-pill px-6 py-3 text-xs">
          登录管理员账号
        </button>
      </div>
    );
  }

  const handleCreateUser = async (e) => {
    e.preventDefault();
    const res = await fetch('/api/admin/users/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify(newUser),
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || '创建用户失败', 'error');
    } else {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
      setNewUser({ username: '', password: '', email: '', role: 'user', vip_days: 0, vip_tier: '季度旗舰版会员' });
    }
  };

  const handleUpdateUser = async (payload) => {
    const res = await fetch('/api/admin/users/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
      setEditUserTarget(null);
      onCurrentUserRefresh();
    } else {
      showToast(data.detail || '更新失败', 'error');
    }
  };

  const handleGrantVip = async (userId, addDays, vipTier, revoke = false) => {
    if (adminBusyRef.current) return;
    adminBusyRef.current = true;
    setAdminBusy(true);
    try {
      const res = await fetch('/api/admin/users/grant-vip', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          user_id: userId,
          add_days: Number(addDays),
          vip_tier: vipTier,
          revoke,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message, 'success');
        setDashboard(data.dashboard);
        setGrantTarget(null);
        onCurrentUserRefresh();
      } else {
        showToast(data.detail || '操作失败', 'error');
      }
    } finally {
      adminBusyRef.current = false;
      setAdminBusy(false);
    }
  };

  const handleDeleteUser = async (userId) => {
    if (adminBusyRef.current) return;
    if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
      if (!window.confirm('确认永久删除该用户及其所有交易所配置与交易数据？此操作不可恢复。')) {
        return;
      }
    }
    adminBusyRef.current = true;
    setAdminBusy(true);
    try {
      const res = await fetch(`/api/admin/users/${userId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message, 'info');
        setDashboard(data.dashboard);
      } else {
        showToast(data.detail || '删除失败', 'error');
      }
    } finally {
      adminBusyRef.current = false;
      setAdminBusy(false);
    }
  };

  const handleAuditOrder = async (orderId, action) => {
    if (adminBusyRef.current) return;
    adminBusyRef.current = true;
    setAdminBusy(true);
    try {
      const res = await fetch('/api/admin/orders/audit', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ order_id: orderId, action }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message, 'success');
        setDashboard(data.dashboard);
        onCurrentUserRefresh();
      } else {
        showToast(data.detail || '审核操作失败', 'error');
      }
    } finally {
      adminBusyRef.current = false;
      setAdminBusy(false);
    }
  };

  const handleSavePlan = async (e) => {
    e.preventDefault();
    if (!editingPlan) return;
    const payload = {
      ...editingPlan,
      duration_days: Number(editingPlan.duration_days),
      price_usdt: Number(editingPlan.price_usdt),
      original_price_usdt: Number(editingPlan.original_price_usdt),
      sort_order: Number(editingPlan.sort_order || 1),
      features:
        typeof editingPlan.features_text === 'string'
          ? editingPlan.features_text
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean)
          : editingPlan.features || [],
    };
    const res = await fetch('/api/admin/plans/upsert', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
      setEditingPlan(null);
    }
  };

  const handleDeletePlan = async (planId) => {
    const res = await fetch(`/api/admin/plans/${planId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'info');
      setDashboard(data.dashboard);
    }
  };

  const handleGenerateCodes = async (e) => {
    e.preventDefault();
    const res = await fetch('/api/admin/vip-codes/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify({
        plan_name: codeGenForm.plan_name,
        duration_days: Number(codeGenForm.duration_days),
        count: Number(codeGenForm.count),
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
    }
  };

  const handleDeleteCode = async (codeId) => {
    const res = await fetch(`/api/admin/vip-codes/${codeId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'info');
      setDashboard(data.dashboard);
    }
  };

  const st = dashboard.stats || {};
  const filteredUsers = (dashboard.users || []).filter((u) => {
    const q = userSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      u.username.toLowerCase().includes(q) ||
      (u.email || '').toLowerCase().includes(q) ||
      (u.vip_label || '').toLowerCase().includes(q) ||
      (u.register_ip || '').toLowerCase().includes(q) ||
      (u.last_login_ip || '').toLowerCase().includes(q)
    );
  });

  const filteredOrders = (dashboard.orders || []).filter((o) => {
    if (orderFilter === 'ALL') return true;
    if (orderFilter === 'EXPIRED') return o.status === 'EXPIRED' || o.status === 'CANCELLED';
    return o.status === orderFilter;
  });

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* 1. Top Header Banner Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-main">后台用户与会员管理系统</h2>
          <p className="text-xs text-dim mt-1">
            管理注册用户账号、重置用户密码、手动开通/收回 VIP 会员资格、监控链上充值订单与批量生成会员激活码
          </p>
        </div>
        <button onClick={loadAdminDashboard} className="btn-primary-pill w-full sm:w-auto px-5 py-2.5 text-xs">
          <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.75} />
          <span>刷新后台数据</span>
        </button>
      </div>

      {/* Security Warning Banner if Admin still has default password */}
      {dashboard?.security_warnings?.default_admin_password_in_use && (
        <div className="surface-card p-4 sm:p-5 border border-rose-500/30 bg-rose-500/5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start sm:items-center gap-3 text-xs text-rose-700 dark:text-rose-300">
            <AlertCircle className="w-5 h-5 shrink-0 text-rose-500 mt-0.5 sm:mt-0" strokeWidth={2} />
            <div>
              <div className="font-bold text-sm">高危安全提醒：管理员默认密码尚未修改</div>
              <div className="mt-0.5 opacity-90">
                检测到系统内置管理员账号 <span className="font-mono font-bold">admin</span> 仍使用默认初始密码，强烈建议立即在「会员中心 → 修改登录密码」中修改为高强度密码！
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 2. 4 Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4">
          <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
            <Users className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <div className="text-lg sm:text-2xl font-bold font-mono text-main">{st.total_users}</div>
            <div className="text-[11px] sm:text-xs text-dim mt-0.5 truncate">已注册用户总数</div>
          </div>
        </div>

        <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4">
          <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
            <UserCheck className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <div className="text-lg sm:text-2xl font-bold font-mono text-main">{st.active_vip_users}</div>
            <div className="text-[11px] sm:text-xs text-dim mt-0.5 truncate">有效 VIP 会员人数</div>
          </div>
        </div>

        <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4">
          <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
            <Wallet className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <div className="text-lg sm:text-2xl font-bold font-mono text-main truncate">
              ${Number(st.total_revenue_usdt || 0).toFixed(2)}
            </div>
            <div className="text-[11px] sm:text-xs text-dim mt-0.5 truncate">累计链上实收会员营收</div>
          </div>
        </div>

        <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4">
          <div className="icon-box w-10 h-10 sm:w-12 sm:h-12">
            <BarChart2 className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <div className="text-lg sm:text-2xl font-bold font-mono text-main">
              {st.unused_codes_count} / {st.total_codes_count}
            </div>
            <div className="text-[11px] sm:text-xs text-dim mt-0.5 truncate">可用未兑换激活码</div>
          </div>
        </div>
      </div>

      {/* 3. Sub-Navigation Pill Bar */}
      <div className="surface-card p-2.5 sm:p-3 flex items-center gap-1.5 sm:gap-2 overflow-x-auto no-scrollbar md:flex-wrap">
        {[
          { id: 'users', label: `后台用户管理 (${(dashboard.users || []).length})`, icon: Users },
          { id: 'orders', label: `链上订单与审核 (${(dashboard.orders || []).length})`, icon: CreditCard },
          { id: 'plans', label: `会员套餐配置 (${(dashboard.plans || []).length})`, icon: Sliders },
          { id: 'codes', label: `会员卡密生成 (${(dashboard.vip_codes || []).length})`, icon: Key },
        ].map((t) => {
          const Icon = t.icon;
          const active = subTab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setSubTab(t.id)}
              className={`px-4 sm:px-5 py-2 sm:py-2.5 text-xs whitespace-nowrap shrink-0 ${active ? 'btn-primary-pill' : 'btn-soft-pill border-transparent'
                }`}
            >
              <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* ==================== SUB-TAB 1: USER MANAGEMENT SYSTEM ==================== */}
      {subTab === 'users' && (
        <div className="space-y-4 sm:space-y-5">
          {/* Create User Card */}
          <form onSubmit={handleCreateUser} className="surface-card p-4 sm:p-6 lg:p-7 text-xs space-y-4">
            <div className="flex items-center gap-2.5">
              <UserPlus className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
              <h3 className="text-sm sm:text-base font-bold text-main">新增注册用户 / 直接授予会员</h3>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 sm:gap-3.5">
              <input
                type="text"
                required
                minLength={3}
                placeholder="输入用户名 (必填)"
                value={newUser.username}
                onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                className="input-field px-4 py-3 font-mono"
              />
              <input
                type="text"
                required
                minLength={6}
                placeholder="初始登录密码 (至少6位)"
                value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                className="input-field px-4 py-3 font-mono"
              />
              <input
                type="email"
                placeholder="注册邮箱 (选填)"
                value={newUser.email}
                onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                className="input-field px-4 py-3 font-mono"
              />
              <select
                value={newUser.role}
                onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                className="input-field px-4 py-3"
              >
                <option value="user">角色：普通注册用户</option>
                <option value="admin">角色：后台管理员</option>
              </select>
              <select
                value={newUser.vip_days}
                onChange={(e) => setNewUser({ ...newUser, vip_days: Number(e.target.value) })}
                className="input-field px-4 py-3"
              >
                <option value={0}>初始会员：未开通 (0天)</option>
                <option value={30}>赠送月度会员 (+30天)</option>
                <option value={90}>赠送季度会员 (+90天)</option>
                <option value={365}>赠送年度会员 (+365天)</option>
                <option value={36500}>赠送终身会员 (永久)</option>
              </select>
            </div>
            <div>
              <button type="submit" className="btn-primary-pill w-full sm:w-auto px-6 py-2.5 text-xs">
                + 新建用户账号
              </button>
            </div>
          </form>

          {/* Grant / Modify VIP Inline Card */}
          {grantTarget && (
            <div className="surface-card p-4 sm:p-6 ring-2 ring-slate-900 dark:ring-sky-400 flex flex-wrap items-center justify-between gap-3 sm:gap-4 text-xs">
              <div className="flex flex-wrap items-center gap-2.5 sm:gap-3">
                <span className="font-bold text-sm text-main">
                  调整用户【{grantTarget.username}】会员资格：
                </span>
                <select
                  value={grantTier}
                  onChange={(e) => setGrantTier(e.target.value)}
                  className="input-field w-full sm:w-auto px-3.5 py-2"
                >
                  <option value="月度专业版会员">月度专业版会员</option>
                  <option value="季度旗舰版会员">季度旗舰版会员</option>
                  <option value="年度机构版会员">年度机构版会员</option>
                  <option value="终身至尊版会员">终身至尊版会员</option>
                </select>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span>增加天数:</span>
                  <input
                    type="number"
                    min={1}
                    max={36500}
                    value={grantDays}
                    onChange={(e) => setGrantDays(Number(e.target.value))}
                    className="input-field px-3 py-2 w-24 font-mono"
                  />
                  {[7, 30, 90, 365, 36500].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setGrantDays(d)}
                      className="px-2.5 py-1.5 rounded-full surface-subtle text-[11px] font-mono hover:opacity-85"
                    >
                      +{d === 36500 ? '永久' : `${d}天`}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
                <button
                  onClick={() => handleGrantVip(grantTarget.id, grantDays, grantTier, false)}
                  className="btn-primary-pill px-5 py-2 text-xs"
                >
                  确认开通 / 延期会员
                </button>
                {grantTarget.username !== 'admin' && (
                  <button
                    onClick={() => handleGrantVip(grantTarget.id, 0, 'NONE', true)}
                    className="btn-soft-pill px-4 py-2 text-xs text-rose-600 dark:text-rose-400"
                  >
                    收回会员资格
                  </button>
                )}
                <button onClick={() => setGrantTarget(null)} className="btn-soft-pill px-4 py-2 text-xs">
                  取消
                </button>
              </div>
            </div>
          )}

          {/* Edit User / Reset Password Inline Card */}
          {editUserTarget && (
            <div className="surface-card p-4 sm:p-6 ring-2 ring-slate-900 dark:ring-sky-400 space-y-4 text-xs">
              <div className="font-bold text-sm text-main">
                编辑用户【{editUserTarget.username}】资料与重置密码
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5 sm:gap-4">
                <div>
                  <label className="block text-dim mb-1">联系邮箱</label>
                  <input
                    type="email"
                    value={editUserForm.email}
                    onChange={(e) => setEditUserForm({ ...editUserForm, email: e.target.value })}
                    placeholder="设置用户邮箱"
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">重置登录密码 (留空则不修改)</label>
                  <input
                    type="text"
                    value={editUserForm.new_password}
                    onChange={(e) => setEditUserForm({ ...editUserForm, new_password: e.target.value })}
                    placeholder="输入至少6位新密码以重置"
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">系统角色</label>
                  <select
                    disabled={editUserTarget.username === 'admin'}
                    value={editUserForm.role}
                    onChange={(e) => setEditUserForm({ ...editUserForm, role: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5"
                  >
                    <option value="user">普通注册用户</option>
                    <option value="admin">后台管理员</option>
                  </select>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2.5">
                <button onClick={() => setEditUserTarget(null)} className="btn-soft-pill px-4 py-2 text-xs">
                  取消
                </button>
                <button
                  onClick={() =>
                    handleUpdateUser({
                      user_id: editUserTarget.id,
                      email: editUserForm.email,
                      role: editUserForm.role,
                      new_password: editUserForm.new_password || undefined,
                    })
                  }
                  className="btn-primary-pill px-5 py-2 text-xs"
                >
                  保存修改
                </button>
              </div>
            </div>
          )}

          {/* Users Table Card */}
          <div className="surface-card p-4 sm:p-6 lg:p-7">
            <div className="flex flex-wrap items-center justify-between gap-3 sm:gap-4 mb-4 sm:mb-5">
              <div>
                <h3 className="text-sm sm:text-base font-bold text-main">全站注册用户与实盘会员列表</h3>
                <p className="text-xs text-dim mt-0.5">
                  未开通会员的注册用户仅可查看实盘行情；开通会员后自动解锁实盘 API 配置与自动交易
                </p>
              </div>
              <div className="relative w-full sm:w-72">
                <Search className="w-4 h-4 text-dim absolute left-3.5 top-3" strokeWidth={1.75} />
                <input
                  type="text"
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  placeholder="搜索用户名 / 邮箱 / 会员等级..."
                  className="w-full input-field rounded-full pl-10 pr-4 py-2.5 text-base sm:text-xs"
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-3 px-3">ID</th>
                    <th className="py-3 px-3">用户名 / 邮箱</th>
                    <th className="py-3 px-3">注册 / 最近登录 IP</th>
                    <th className="py-3 px-3">角色</th>
                    <th className="py-3 px-3">会员等级</th>
                    <th className="py-3 px-3">到期时间</th>
                    <th className="py-3 px-3">实盘工作区状态</th>
                    <th className="py-3 px-3">实盘系统权限</th>
                    <th className="py-3 px-3">状态</th>
                    <th className="py-3 px-3 text-right">管理操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line">
                  {filteredUsers.map((u) => {
                    const lw = u.live_workspace || {};
                    return (
                      <tr key={u.id}>
                        <td className="py-3.5 px-3 font-mono text-dim">#{u.id}</td>
                        <td className="py-3.5 px-3">
                          <div className="font-bold text-main font-mono">{u.username}</div>
                          <div className="text-[11px] text-dim">{u.email || '未设置邮箱'}</div>
                        </td>
                        <td className="py-3.5 px-3 font-mono text-[11px]">
                          <div className="text-main font-semibold" title="注册绑定 IP">
                            {u.register_ip || '系统/后台创建'}
                          </div>
                          <div className="text-dim" title="最近登录 IP">
                            {u.last_login_ip ? `最近: ${u.last_login_ip}` : '--'}
                          </div>
                        </td>
                        <td className="py-3.5 px-3">
                          <span className="px-2.5 py-1 rounded-full text-[11px] font-semibold surface-subtle text-main">
                            {u.role === 'admin' ? '管理员' : '注册用户'}
                          </span>
                        </td>
                        <td className="py-3.5 px-3 font-semibold text-main">{u.vip_label}</td>
                        <td className="py-3.5 px-3 font-mono text-sub">{u.vip_expires_str}</td>
                        <td className="py-3.5 px-3 font-mono text-[11px]">
                          <div className="text-main">
                            {lw.symbols_count || 0} 币种 ·{' '}
                            <span className={lw.trading_active ? 'text-emerald-600 font-bold' : 'text-dim'}>
                              {lw.trading_active ? '实盘运行中' : '待命'}
                            </span>
                          </div>
                          <div className="text-dim">
                            {lw.follower_auth_status || 'UNCONFIGURED'}
                            {Number(lw.follower_equity_usdt || 0) > 0
                              ? ` · $${Number(lw.follower_equity_usdt).toFixed(2)}`
                              : ''}
                          </div>
                        </td>
                        <td className="py-3.5 px-3">
                          {u.can_use_live ? (
                            <span className="text-lime-600 dark:text-emerald-400 font-bold">
                              可查看 + 可使用实盘
                            </span>
                          ) : (
                            <span className="text-amber-600 dark:text-amber-400 font-medium">
                              仅可查看实盘 (需开VIP)
                            </span>
                          )}
                        </td>
                        <td className="py-3.5 px-3">
                          <span
                            className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${u.is_active
                              ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                              : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                              }`}
                          >
                            {u.is_active ? '正常' : '已封禁'}
                          </span>
                        </td>
                        <td className="py-3.5 px-3 text-right space-x-2 whitespace-nowrap">
                          <button
                            onClick={() => {
                              setGrantTarget(u);
                              setEditUserTarget(null);
                              setGrantTier(u.vip_tier !== 'NONE' ? u.vip_tier : '季度旗舰版会员');
                            }}
                            className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
                          >
                            会员授权/延期
                          </button>
                          <button
                            onClick={() => {
                              setEditUserTarget(u);
                              setGrantTarget(null);
                              setEditUserForm({
                                email: u.email || '',
                                role: u.role || 'user',
                                new_password: '',
                              });
                            }}
                            className="btn-soft-pill px-3 py-1.5 text-[11px]"
                          >
                            <Edit3 className="w-3 h-3" strokeWidth={1.75} />
                            <span>编辑/改密</span>
                          </button>
                          {u.username !== 'admin' && (
                            <>
                              <button
                                onClick={() =>
                                  handleUpdateUser({
                                    user_id: u.id,
                                    is_active: !u.is_active,
                                  })
                                }
                                className="btn-soft-pill px-3 py-1.5 text-[11px]"
                              >
                                {u.is_active ? '封禁' : '解封'}
                              </button>
                              <button
                                onClick={() => handleDeleteUser(u.id)}
                                className="btn-soft-pill px-3 py-1.5 text-[11px] text-rose-600 dark:text-rose-400"
                              >
                                删除
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 2: MEMBERSHIP ORDERS & AUDIT ==================== */}
      {subTab === 'orders' && (
        <div className="surface-card p-4 sm:p-6 lg:p-7">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4 sm:mb-5">
            <div>
              <h3 className="text-sm sm:text-base font-bold text-main">Web3 链上订单监控与人工补单审核流水</h3>
              <p className="text-xs text-dim mt-0.5">
                正常精确尾数转账将由链上监控自动完成开通；若用户转账金额四舍五入错误，管理员可在此手动确认补单
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 text-xs">
              {[
                { k: 'ALL', l: '全部订单' },
                { k: 'PENDING', l: `链上监控中 (${st.pending_orders_count || 0})` },
                { k: 'PAID', l: `已开通 (${st.paid_orders_count || 0})` },
                { k: 'EXPIRED', l: `已超时/取消 (${st.expired_orders_count || 0})` },
                { k: 'REJECTED', l: '已拒绝' },
              ].map((f) => (
                <button
                  key={f.k}
                  onClick={() => setOrderFilter(f.k)}
                  className={`px-3 sm:px-4 py-1.5 sm:py-2 text-xs ${orderFilter === f.k ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                >
                  {f.l}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-line text-[11px] text-dim">
                  <th className="py-3 px-3">订单编号</th>
                  <th className="py-3 px-3">用户名</th>
                  <th className="py-3 px-3">会员套餐</th>
                  <th className="py-3 px-3">时长</th>
                  <th className="py-3 px-3">精确监控金额</th>
                  <th className="py-3 px-3">支付网络</th>
                  <th className="py-3 px-3">链上哈希 / 备注</th>
                  <th className="py-3 px-3">状态</th>
                  <th className="py-3 px-3">创建时间</th>
                  <th className="py-3 px-3 text-right">人工干预操作</th>
                </tr>
              </thead>
              <tbody className="divide-y border-line font-mono">
                {filteredOrders.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="py-12 text-center text-dim font-sans">
                      暂无符合条件的会员订单
                    </td>
                  </tr>
                ) : (
                  filteredOrders.map((o) => (
                    <tr key={o.id}>
                      <td className="py-3.5 px-3 font-semibold text-main">{o.order_no}</td>
                      <td className="py-3.5 px-3 font-bold text-main">{o.username}</td>
                      <td className="py-3.5 px-3 font-sans text-main">{o.plan_name}</td>
                      <td className="py-3.5 px-3 text-sub">+{o.duration_days} 天</td>
                      <td className="py-3.5 px-3 font-bold text-emerald-600 dark:text-emerald-400">
                        ${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}
                      </td>
                      <td className="py-3.5 px-3 font-sans text-sub">
                        {o.chain_network === 'MULTI_EVM' ? '10链监控' : o.chain_network || o.pay_method}
                      </td>
                      <td className="py-3.5 px-3 text-dim max-w-xs truncate" title={o.tx_hash}>
                        {o.tx_hash}
                      </td>
                      <td className="py-3.5 px-3 font-sans">
                        <span
                          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${o.status === 'PAID'
                            ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                            : o.status === 'PENDING'
                              ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                              : 'surface-subtle text-dim'
                            }`}
                        >
                          {o.status === 'PAID'
                            ? '已支付开通'
                            : o.status === 'PENDING'
                              ? `监控中 (${formatCountdown(o.remaining_seconds)})`
                              : o.status === 'EXPIRED'
                                ? '超时已关闭'
                                : o.status === 'CANCELLED'
                                  ? '用户已取消'
                                  : '已拒绝'}
                        </span>
                      </td>
                      <td className="py-3.5 px-3 text-dim">{o.created_at}</td>
                      <td className="py-3.5 px-3 text-right font-sans space-x-2 whitespace-nowrap">
                        {o.status !== 'PAID' ? (
                          <>
                            <button
                              onClick={() => handleAuditOrder(o.id, 'APPROVE')}
                              className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
                            >
                              手动确认并开通
                            </button>
                            {o.status === 'PENDING' && (
                              <button
                                onClick={() => handleAuditOrder(o.id, 'EXPIRE')}
                                className="btn-soft-pill px-3 py-1.5 text-[11px] text-rose-600"
                              >
                                关闭监控
                              </button>
                            )}
                          </>
                        ) : (
                          <span className="text-dim">已归档</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 3: MEMBERSHIP PLANS CONFIG ==================== */}
      {subTab === 'plans' && (
        <div className="surface-card p-4 sm:p-6 lg:p-7 space-y-4 sm:space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm sm:text-base font-bold text-main">会员套餐定价与特权管理</h3>
              <p className="text-xs text-dim mt-0.5">
                修改套餐价格、有效天数或特权描述后，前台会员购买页将实时同步更新（链上支付自动在该价格基础上生成随机3位小数）
              </p>
            </div>
            <button
              onClick={() =>
                setEditingPlan({
                  id: null,
                  plan_code: `PLAN_${Date.now().toString().slice(-4)}`,
                  name: '新自定义会员套餐',
                  duration_days: 60,
                  price_usdt: 169,
                  original_price_usdt: 299,
                  badge_text: '限时特惠',
                  features_text:
                    '解锁实盘毫秒级狙击通道使用权限\n支持 OKX / Binance 实盘 API 密钥绑定\n支持全币种自定义脉冲监控参数',
                  is_enabled: true,
                  sort_order: 5,
                })
              }
              className="btn-primary-pill w-full sm:w-auto px-5 py-2.5 text-xs"
            >
              <Plus className="w-3.5 h-3.5" strokeWidth={2} />
              <span>新增会员套餐</span>
            </button>
          </div>

          {editingPlan && (
            <form onSubmit={handleSavePlan} className="surface-subtle p-4 sm:p-6 space-y-4 text-xs">
              <div className="font-bold text-sm text-main">
                {editingPlan.id ? `编辑套餐：${editingPlan.name}` : '新建会员套餐'}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-3.5">
                <div>
                  <label className="block text-dim mb-1">套餐代号</label>
                  <input
                    type="text"
                    required
                    value={editingPlan.plan_code}
                    onChange={(e) => setEditingPlan({ ...editingPlan, plan_code: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">套餐名称</label>
                  <input
                    type="text"
                    required
                    value={editingPlan.name}
                    onChange={(e) => setEditingPlan({ ...editingPlan, name: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">有效天数</label>
                  <input
                    type="number"
                    required
                    min={1}
                    value={editingPlan.duration_days}
                    onChange={(e) => setEditingPlan({ ...editingPlan, duration_days: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">现价 (USDT)</label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={editingPlan.price_usdt}
                    onChange={(e) => setEditingPlan({ ...editingPlan, price_usdt: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">原价 (USDT)</label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={editingPlan.original_price_usdt}
                    onChange={(e) => setEditingPlan({ ...editingPlan, original_price_usdt: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-dim mb-1">角标文字</label>
                  <input
                    type="text"
                    value={editingPlan.badge_text}
                    onChange={(e) => setEditingPlan({ ...editingPlan, badge_text: e.target.value })}
                    className="w-full input-field px-3.5 py-2.5"
                  />
                </div>
              </div>
              <div>
                <label className="block text-dim mb-1">套餐权益说明（每行一条）</label>
                <textarea
                  rows={3}
                  value={editingPlan.features_text}
                  onChange={(e) => setEditingPlan({ ...editingPlan, features_text: e.target.value })}
                  className="w-full input-field p-3.5 text-base sm:text-xs"
                />
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setEditingPlan(null)}
                  className="btn-soft-pill px-5 py-2 text-xs"
                >
                  取消
                </button>
                <button type="submit" className="btn-primary-pill px-6 py-2 text-xs">
                  保存套餐配置
                </button>
              </div>
            </form>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3.5 sm:gap-4">
            {(dashboard.plans || []).map((p) => (
              <div key={p.id} className="surface-subtle p-4 sm:p-5 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-sm text-main">{p.name}</span>
                    <span className="text-xs font-mono text-dim">{p.duration_days} 天</span>
                  </div>
                  <div className="mt-3 flex items-baseline gap-2 font-mono">
                    <span className="text-2xl font-bold text-main">${p.price_usdt}</span>
                    <span className="text-xs text-dim line-through">${p.original_price_usdt}</span>
                  </div>
                  <ul className="mt-3 space-y-1.5 text-xs text-sub">
                    {(p.features || []).map((f, idx) => (
                      <li key={idx}>• {f}</li>
                    ))}
                  </ul>
                </div>

                <div className="mt-5 pt-3 border-t border-line flex items-center justify-end gap-2 text-xs">
                  <button
                    onClick={() =>
                      setEditingPlan({
                        ...p,
                        features_text: (p.features || []).join('\n'),
                      })
                    }
                    className="btn-primary-pill px-4 py-1.5 text-[11px]"
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => handleDeletePlan(p.id)}
                    className="btn-soft-pill px-3.5 py-1.5 text-[11px] text-rose-600"
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 4: VIP ACTIVATION CODES GENERATOR ==================== */}
      {subTab === 'codes' && (
        <div className="space-y-4 sm:space-y-5">
          <form onSubmit={handleGenerateCodes} className="surface-card p-4 sm:p-6 lg:p-7 text-xs space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <Key className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                <h3 className="text-sm sm:text-base font-bold text-main">批量制卡 / 生成会员激活码</h3>
              </div>
              <button
                type="button"
                onClick={() => {
                  const unused = (dashboard.vip_codes || [])
                    .filter((c) => c.status === 'UNUSED')
                    .map((c) => c.code);
                  if (!unused.length) {
                    showToast('当前没有未使用的激活码', 'error');
                    return;
                  }
                  navigator.clipboard?.writeText(unused.join('\n'));
                  showToast(`已复制全部 ${unused.length} 张未使用激活码`, 'success');
                }}
                className="btn-soft-pill w-full sm:w-auto px-4 py-2 text-xs"
              >
                <Copy className="w-3.5 h-3.5" strokeWidth={1.75} />
                <span>一键复制全部可用激活码</span>
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 sm:gap-4">
              <div>
                <label className="block text-dim mb-1.5">对应会员套餐</label>
                <select
                  value={codeGenForm.plan_name}
                  onChange={(e) => {
                    const name = e.target.value;
                    const daysMap = {
                      月度专业版会员: 30,
                      季度旗舰版会员: 90,
                      年度机构版会员: 365,
                      终身至尊版会员: 36500,
                    };
                    setCodeGenForm({
                      ...codeGenForm,
                      plan_name: name,
                      duration_days: daysMap[name] || codeGenForm.duration_days,
                    });
                  }}
                  className="w-full input-field px-4 py-3"
                >
                  <option value="月度专业版会员">月度专业版会员 (30天)</option>
                  <option value="季度旗舰版会员">季度旗舰版会员 (90天)</option>
                  <option value="年度机构版会员">年度机构版会员 (365天)</option>
                  <option value="终身至尊版会员">终身至尊版会员 (永久)</option>
                </select>
              </div>
              <div>
                <label className="block text-dim mb-1.5">授权有效天数</label>
                <input
                  type="number"
                  min={1}
                  value={codeGenForm.duration_days}
                  onChange={(e) => setCodeGenForm({ ...codeGenForm, duration_days: e.target.value })}
                  className="w-full input-field px-4 py-3 font-mono"
                />
              </div>
              <div>
                <label className="block text-dim mb-1.5">生成张数 (1~50)</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={codeGenForm.count}
                  onChange={(e) => setCodeGenForm({ ...codeGenForm, count: e.target.value })}
                  className="w-full input-field px-4 py-3 font-mono"
                />
              </div>
              <div className="flex items-end">
                <button type="submit" className="btn-primary-pill w-full py-3 text-xs">
                  + 批量生成激活码
                </button>
              </div>
            </div>
          </form>

          <div className="surface-card p-4 sm:p-6 lg:p-7">
            <h3 className="text-sm sm:text-base font-bold text-main mb-4">激活码库存与兑换记录</h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-3 px-3">ID</th>
                    <th className="py-3 px-3">激活码 (Code)</th>
                    <th className="py-3 px-3">对应会员套餐</th>
                    <th className="py-3 px-3">授权天数</th>
                    <th className="py-3 px-3">状态</th>
                    <th className="py-3 px-3">兑换用户</th>
                    <th className="py-3 px-3">兑换时间</th>
                    <th className="py-3 px-3">创建时间</th>
                    <th className="py-3 px-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line font-mono">
                  {(dashboard.vip_codes || []).length === 0 ? (
                    <tr>
                      <td colSpan={9} className="py-10 text-center text-dim font-sans">
                        暂无会员激活码，可在上方批量生成
                      </td>
                    </tr>
                  ) : (
                    (dashboard.vip_codes || []).map((c) => (
                      <tr key={c.id}>
                        <td className="py-3.5 px-3 text-dim">#{c.id}</td>
                        <td className="py-3.5 px-3 font-bold text-main">{c.code}</td>
                        <td className="py-3.5 px-3 font-sans text-main">{c.plan_name}</td>
                        <td className="py-3.5 px-3 text-sub">+{c.duration_days} 天</td>
                        <td className="py-3.5 px-3 font-sans">
                          <span
                            className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${c.status === 'UNUSED'
                              ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                              : 'surface-subtle text-dim'
                              }`}
                          >
                            {c.status === 'UNUSED' ? '未使用' : '已核销'}
                          </span>
                        </td>
                        <td className="py-3.5 px-3 text-main">{c.used_by_username || '--'}</td>
                        <td className="py-3.5 px-3 text-dim">{c.used_at || '--'}</td>
                        <td className="py-3.5 px-3 text-dim">{c.created_at}</td>
                        <td className="py-3.5 px-3 text-right font-sans space-x-2">
                          <button
                            onClick={() => {
                              navigator.clipboard?.writeText(c.code);
                              showToast(`已复制激活码: ${c.code}`, 'info');
                            }}
                            className="btn-soft-pill px-3 py-1 text-[11px]"
                          >
                            <Copy className="w-3 h-3" strokeWidth={1.75} />
                            <span>复制</span>
                          </button>
                          <button
                            onClick={() => handleDeleteCode(c.id)}
                            className="btn-soft-pill px-2.5 py-1 text-[11px] text-rose-600"
                          >
                            <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
