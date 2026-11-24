import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  AlertCircle,
  Award,
  BarChart2,
  Check,
  CheckCircle2,
  Clock,
  Coins,
  Copy,
  CreditCard,
  Edit3,
  Gift,
  Key,
  Lock,
  MessageSquare,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  Send,
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

const FEEDBACK_CATEGORIES = ['策略问题', '账户与会员', 'Bug反馈', '功能建议', '其他'];

function formatDurationCn(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s <= 0) return '0秒';
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  if (hrs > 0) return `${hrs}小时${String(mins).padStart(2, '0')}分`;
  if (mins > 0) return `${mins}分${String(secs).padStart(2, '0')}秒`;
  return `${secs}秒`;
}

function formatPointsActionLabel(actionType) {
  switch (actionType) {
    case 'INVITE_REWARD_INVITER':
    case 'INVITE_INVITER':
      return '邀请好友奖励';
    case 'INVITE_REWARD_INVITEE':
    case 'INVITE_INVITEE':
      return '受邀注册奖励';
    case 'REDEEM_VIP':
      return '积分兑换会员';
    case 'ADMIN_ADJUST':
      return '管理员调整';
    default:
      return actionType || '积分变动';
  }
}

// ==================== 1. AUTH LOGIN / REGISTER MODAL ====================
export function AuthModal({
  isOpen,
  onClose,
  initialMode = 'login',
  defaultInviteCode = '',
  onAuthSuccess,
  showToast,
}) {
  const [mode, setMode] = useState(initialMode);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [inviteCode, setInviteCode] = useState(defaultInviteCode || '');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setMode(initialMode);
  }, [initialMode]);

  useEffect(() => {
    if (defaultInviteCode) {
      setInviteCode(String(defaultInviteCode).trim().toUpperCase());
    }
  }, [defaultInviteCode]);

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
          : {
              username: username.trim(),
              password,
              email: email.trim(),
              invite_code: inviteCode.trim().toUpperCase(),
            };
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
        if (mode === 'register') setInviteCode('');
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

          {mode === 'register' && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-main font-semibold">好友邀请码 (选填)</label>
                <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
                  填码注册双方各得 100 积分
                </span>
              </div>
              <input
                type="text"
                maxLength={32}
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
                placeholder="输入专属邀请码，如 INV-A1B2C3D4"
                className="w-full input-field px-4 py-3 text-base sm:text-xs font-mono uppercase"
              />
            </div>
          )}

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

  // User Feedback / Question Message Board State
  const [feedbackData, setFeedbackData] = useState({
    messages: [],
    can_submit: true,
    cooldown_remaining_sec: 0,
    next_submit_at_ts: 0,
    total_count: 0,
    replied_count: 0,
  });
  const [feedbackFetchedAtMs, setFeedbackFetchedAtMs] = useState(() => Date.now());
  const [feedbackNowMs, setFeedbackNowMs] = useState(() => Date.now());
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackDeletingId, setFeedbackDeletingId] = useState(null);
  const [confirmDeleteFeedbackId, setConfirmDeleteFeedbackId] = useState(null);
  const [feedbackCategory, setFeedbackCategory] = useState('策略问题');
  const [feedbackTitle, setFeedbackTitle] = useState('');
  const [feedbackContent, setFeedbackContent] = useState('');

  // Referral Invite & Points Redemption State
  const [referralInfo, setReferralInfo] = useState(null);
  const [referralLoading, setReferralLoading] = useState(false);
  const [redeemHours, setRedeemHours] = useState(1);
  const [redeemPointsBusy, setRedeemPointsBusy] = useState(false);

  const onUserUpdatedRef = useRef(onUserUpdated);
  onUserUpdatedRef.current = onUserUpdated;
  const showToastRef = useRef(showToast);
  showToastRef.current = showToast;

  // Clear stale pending order, feedback & referral immediately on account switch or logout
  useEffect(() => {
    setActiveOrder(null);
    setRemainingSeconds(0);
    setOrders([]);
    setConfirmDeleteFeedbackId(null);
    setReferralInfo(null);
    setRedeemHours(1);
    setFeedbackData({
      messages: [],
      can_submit: true,
      cooldown_remaining_sec: 0,
      next_submit_at_ts: 0,
      total_count: 0,
      replied_count: 0,
    });
  }, [authToken]);

  const fetchMyReferral = useCallback(async () => {
    if (!authToken) return;
    setReferralLoading(true);
    try {
      const res = await fetch('/api/referral/me', {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setReferralInfo(data);
        if (data.user) {
          onUserUpdatedRef.current?.(data.user);
        }
      }
    } catch (e) {
      // ignore
    } finally {
      setReferralLoading(false);
    }
  }, [authToken]);

  useEffect(() => {
    fetchMyReferral();
  }, [fetchMyReferral]);

  const handleRedeemPointsForVip = async (e) => {
    e.preventDefault();
    if (!currentUser || !authToken) {
      showToast('请先登录账号后再使用积分兑换 VIP 会员', 'error');
      onOpenAuthModal('login');
      return;
    }
    const hrs = Math.max(1, parseInt(redeemHours, 10) || 1);
    setRedeemPointsBusy(true);
    try {
      const res = await fetch('/api/points/redeem-vip', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ hours: hrs }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '积分兑换失败', 'error');
      } else {
        showToast(data.message || `已成功兑换 +${hrs} 小时 VIP 会员`, 'success');
        if (data.user) onUserUpdated(data.user);
        if (data.referral) setReferralInfo(data.referral);
        if (data.orders) setOrders(data.orders);
      }
    } catch (err) {
      showToast('网络请求异常，请稍后重试', 'error');
    } finally {
      setRedeemPointsBusy(false);
    }
  };

  const fetchMyFeedback = useCallback(async () => {
    if (!authToken) return;
    setFeedbackLoading(true);
    try {
      const res = await fetch('/api/feedback/my', {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        const nowTs = Date.now();
        setFeedbackData({
          messages: data.messages || [],
          can_submit: Boolean(data.can_submit),
          cooldown_remaining_sec: Number(data.cooldown_remaining_sec || 0),
          next_submit_at_ts: Number(data.next_submit_at_ts || 0),
          total_count: Number(data.total_count || 0),
          replied_count: Number(data.replied_count || 0),
        });
        setFeedbackFetchedAtMs(nowTs);
        setFeedbackNowMs(nowTs);
      }
    } catch (e) {
      // ignore
    } finally {
      setFeedbackLoading(false);
    }
  }, [authToken]);

  useEffect(() => {
    fetchMyFeedback();
  }, [fetchMyFeedback]);

  const hasActiveFeedbackTimers =
    Number(feedbackData.cooldown_remaining_sec || 0) > 0 ||
    (feedbackData.messages || []).some((m) => Number(m.delete_unlock_remaining_sec || 0) > 0);

  useEffect(() => {
    if (!authToken || !hasActiveFeedbackTimers) return undefined;
    const timer = setInterval(() => {
      setFeedbackNowMs(Date.now());
    }, 1000);
    return () => clearInterval(timer);
  }, [authToken, hasActiveFeedbackTimers]);

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

  const handleSubmitFeedback = async (e) => {
    e.preventDefault();
    if (!currentUser || !authToken) {
      showToast('请先登录账号后再提交问题留言', 'error');
      onOpenAuthModal('login');
      return;
    }
    const trimmedContent = feedbackContent.trim();
    if (trimmedContent.length < 2) {
      showToast('请详细描述您的问题（至少 2 个字符）', 'error');
      return;
    }
    setFeedbackSubmitting(true);
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          category: feedbackCategory,
          title: feedbackTitle.trim(),
          content: trimmedContent,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '提交留言失败', 'error');
        fetchMyFeedback();
      } else {
        showToast(data.message || '问题留言已提交', 'success');
        setFeedbackTitle('');
        setFeedbackContent('');
        if (data.feedback) {
          const nowTs = Date.now();
          setFeedbackData(data.feedback);
          setFeedbackFetchedAtMs(nowTs);
          setFeedbackNowMs(nowTs);
        } else {
          fetchMyFeedback();
        }
      }
    } catch (err) {
      showToast('网络请求异常，请稍后重试', 'error');
    } finally {
      setFeedbackSubmitting(false);
    }
  };

  const handleDeleteMyFeedback = async (messageId) => {
    if (!authToken) return;
    setFeedbackDeletingId(messageId);
    try {
      const res = await fetch(`/api/feedback/${messageId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '删除失败', 'error');
        fetchMyFeedback();
      } else {
        showToast(data.message || '已删除您侧的留言记录', 'info');
        setConfirmDeleteFeedbackId(null);
        if (data.feedback) {
          const nowTs = Date.now();
          setFeedbackData(data.feedback);
          setFeedbackFetchedAtMs(nowTs);
          setFeedbackNowMs(nowTs);
        } else {
          fetchMyFeedback();
        }
      }
    } catch (err) {
      showToast('网络请求异常，请稍后重试', 'error');
    } finally {
      setFeedbackDeletingId(null);
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
  const feedbackElapsedSec = Math.max(0, Math.floor((feedbackNowMs - feedbackFetchedAtMs) / 1000));
  const submitCooldownRemaining = Math.max(
    0,
    Number(feedbackData.cooldown_remaining_sec || 0) - feedbackElapsedSec
  );
  const canSubmitFeedbackNow = Boolean(currentUser) && submitCooldownRemaining <= 0;
  const refSettings = referralInfo?.settings || {
    invite_enabled: true,
    inviter_reward_points: 100,
    invitee_reward_points: 100,
    redeem_enabled: true,
    points_per_vip_hour: 100,
    max_redeem_hours_per_tx: 720,
  };
  const myInviteCode = referralInfo?.invite_code || currentUser?.invite_code || '';
  const myInviteLink =
    typeof window !== 'undefined' && myInviteCode
      ? `${window.location.origin}/?invite=${encodeURIComponent(myInviteCode)}`
      : '';
  const myPointsBalance = Number(
    referralInfo?.points_balance ?? currentUser?.points_balance ?? 0
  );
  const myTotalEarnedPoints = Number(
    referralInfo?.total_points_earned ?? currentUser?.total_points_earned ?? 0
  );
  const myTotalInvites = Number(
    referralInfo?.total_invites_count ?? currentUser?.total_invites_count ?? 0
  );
  const pointsPerHour = Math.max(1, Number(refSettings.points_per_vip_hour || 100));
  const safeRedeemHours = Math.max(1, parseInt(redeemHours, 10) || 1);
  const redeemCostPoints = safeRedeemHours * pointsPerHour;

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* 1. Top Page Header Banner Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-main">会员中心与 Web3 多链自动收银台</h2>
          <p className="text-xs text-dim mt-1">
            注册用户可查看实盘行情、邀请好友赚取积分兑换 VIP 会员、提交问题留言；开通 VIP 后即可启动毫秒级实盘狙击
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-2.5 w-full sm:w-auto">
          <button
            type="button"
            onClick={() => {
              const el = document.getElementById('user-referral-points');
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}
            className="btn-soft-pill px-4 py-2 sm:py-2.5 text-xs"
          >
            <Gift className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>邀请赚积分</span>
            <span className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/15 text-amber-700 dark:text-amber-300">
              {currentUser ? `${myPointsBalance} 分` : `+${refSettings.inviter_reward_points}分`}
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              const el = document.getElementById('user-feedback-board');
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}
            className="btn-soft-pill px-4 py-2 sm:py-2.5 text-xs"
          >
            <MessageSquare className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>问题留言</span>
            {currentUser && feedbackData.replied_count > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
                {feedbackData.replied_count} 已复
              </span>
            )}
          </button>
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
            <div className="text-sm sm:text-base font-bold text-main mt-1.5 font-mono">
              {currentUser ? currentUser.vip_expires_str : '未开通'}
            </div>
            {currentUser && currentUser.is_vip && currentUser.vip_remaining_label && (
              <div className="text-[11px] font-mono text-emerald-600 dark:text-emerald-400 mt-1">
                剩余时长：{currentUser.vip_remaining_label}
              </div>
            )}
            {currentUser && !currentUser.is_vip && Number(currentUser.vip_expires_at || 0) > 0 && (
              <div className="text-[11px] text-rose-600 dark:text-rose-400 mt-1 font-medium">
                已到期 · 运行策略已全部自动停止
              </div>
            )}
          </div>
        </div>

        {currentUser && (
          <div className="mt-4 pt-4 border-t border-line flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-3 sm:gap-4">
              <div className="flex items-center gap-1.5">
                <Coins className="w-4 h-4 text-amber-500 shrink-0" strokeWidth={1.75} />
                <span className="text-dim">可用积分：</span>
                <span className="font-mono font-bold text-main">{myPointsBalance} 积分</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Gift className="w-4 h-4 text-emerald-500 shrink-0" strokeWidth={1.75} />
                <span className="text-dim">专属邀请码：</span>
                <span className="font-mono font-bold text-main px-2 py-0.5 rounded-lg surface-subtle select-all">
                  {myInviteCode || '--'}
                </span>
              </div>
              <div className="text-dim">
                已邀请 <span className="font-mono font-bold text-main">{myTotalInvites}</span> 人
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {myInviteCode && (
                <button
                  type="button"
                  onClick={() => copyText(myInviteCode, '专属邀请码')}
                  className="btn-soft-pill px-3 py-1.5 text-[11px]"
                >
                  <Copy className="w-3 h-3" strokeWidth={1.75} />
                  <span>复制邀请码</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  const el = document.getElementById('user-referral-points');
                  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }}
                className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
              >
                <Award className="w-3 h-3" strokeWidth={1.75} />
                <span>积分兑换 VIP</span>
              </button>
            </div>
          </div>
        )}
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

      {/* 5.2 Referral Invite & Points-to-VIP Redemption Section */}
      <div id="user-referral-points" className="surface-card p-4 sm:p-6 lg:p-7 space-y-5 scroll-mt-20">
        <div className="flex flex-wrap items-start sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5 flex-wrap">
              <Gift className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
              <h3 className="text-sm sm:text-base font-bold text-main">邀请好友赚积分 & 积分兑换 VIP 会员</h3>
              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/12 text-emerald-700 dark:text-emerald-400">
                邀请 1 人双方各得 +{refSettings.inviter_reward_points} / +{refSettings.invitee_reward_points} 积分
              </span>
              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/15 text-amber-700 dark:text-amber-300">
                {pointsPerHour} 积分 = 1 小时实盘 VIP
              </span>
            </div>
            <p className="text-xs text-dim mt-1">
              分享您的专属邀请码或专属链接，好友注册成功后双方自动获得积分奖励；积分可随时直接兑换实盘 VIP 会员时长（实时生效、到期自动停机保护）
            </p>
          </div>
          {currentUser && (
            <button
              type="button"
              onClick={fetchMyReferral}
              disabled={referralLoading}
              className="btn-soft-pill px-4 py-1.5 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${referralLoading ? 'animate-spin' : ''}`} strokeWidth={1.75} />
              <span>刷新积分数据</span>
            </button>
          )}
        </div>

        {!currentUser ? (
          <div className="surface-subtle p-6 text-center space-y-3">
            <p className="text-xs text-sub">
              注册登录后即可获取您的专属邀请码：每成功邀请 1 位好友注册，双方均可获赠{' '}
              <span className="font-mono font-bold text-main">+{refSettings.inviter_reward_points} 积分</span>，
              <span className="font-mono font-bold text-main">{pointsPerHour} 积分</span> 即可直接兑换{' '}
              <span className="font-mono font-bold text-main">1 小时</span> 实盘 VIP 会员！
            </p>
            <div className="flex flex-wrap items-center justify-center gap-2.5">
              <button
                type="button"
                onClick={() => onOpenAuthModal('register')}
                className="btn-primary-pill px-5 py-2 text-xs"
              >
                免费注册领取专属邀请码
              </button>
              <button
                type="button"
                onClick={() => onOpenAuthModal('login')}
                className="btn-soft-pill px-5 py-2 text-xs"
              >
                已有账号登录
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
            {/* Left 6 Cols: Personal Invite Code, Share Link & Invited Friends */}
            <div className="lg:col-span-6 space-y-4">
              <div className="surface-subtle p-4 sm:p-5 space-y-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-main">我的专属邀请码与推广链接</span>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                      refSettings.invite_enabled
                        ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                        : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                    }`}
                  >
                    {refSettings.invite_enabled ? '邀请奖励已开启' : '邀请奖励暂停中'}
                  </span>
                </div>

                {/* Invite Code Display */}
                <div className="surface-card p-3.5 sm:p-4 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] text-dim">专属邀请码（好友注册时填写）</div>
                    <div className="text-xl sm:text-2xl font-extrabold font-mono tracking-wider text-main mt-0.5 select-all">
                      {myInviteCode || '生成中...'}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={!myInviteCode}
                      onClick={() => copyText(myInviteCode, '专属邀请码')}
                      className="btn-primary-pill px-3.5 py-2 text-xs"
                    >
                      <Copy className="w-3.5 h-3.5" strokeWidth={1.75} />
                      <span>复制邀请码</span>
                    </button>
                    {myInviteLink && (
                      <button
                        type="button"
                        onClick={() => copyText(myInviteLink, '专属邀请链接')}
                        className="btn-soft-pill px-3.5 py-2 text-xs"
                      >
                        <Share2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                        <span>复制邀请链接</span>
                      </button>
                    )}
                  </div>
                </div>

                {/* 3 KPI tiles */}
                <div className="grid grid-cols-3 gap-2.5 text-center">
                  <div className="surface-card p-3">
                    <div className="text-[11px] text-dim">当前可用积分</div>
                    <div className="text-lg sm:text-xl font-extrabold font-mono text-amber-600 dark:text-amber-400 mt-1">
                      {myPointsBalance}
                    </div>
                  </div>
                  <div className="surface-card p-3">
                    <div className="text-[11px] text-dim">累计获得积分</div>
                    <div className="text-lg sm:text-xl font-extrabold font-mono text-main mt-1">
                      {myTotalEarnedPoints}
                    </div>
                  </div>
                  <div className="surface-card p-3">
                    <div className="text-[11px] text-dim">已邀请好友</div>
                    <div className="text-lg sm:text-xl font-extrabold font-mono text-emerald-600 dark:text-emerald-400 mt-1">
                      {myTotalInvites}
                    </div>
                  </div>
                </div>

                <div className="text-[11px] text-dim leading-relaxed space-y-1">
                  <div>
                    • 每成功邀请 1 位新用户注册：您得{' '}
                    <span className="font-mono font-semibold text-main">+{refSettings.inviter_reward_points} 积分</span>
                    ，受邀好友得{' '}
                    <span className="font-mono font-semibold text-main">+{refSettings.invitee_reward_points} 积分</span>
                    （同一 IP 防刷限制与防自邀校验生效）。
                  </div>
                  {(referralInfo?.invited_by_username || currentUser?.invited_by_username) && (
                    <div>
                      • 我的邀请人：
                      <span className="font-mono font-semibold text-main">
                        {referralInfo?.invited_by_username || currentUser?.invited_by_username}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* Invited Friends List */}
              <div className="surface-subtle p-4 space-y-2.5 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-main">我邀请的好友列表</span>
                  <span className="text-[11px] font-mono text-dim">
                    共 {(referralInfo?.invited_users || []).length} 位
                  </span>
                </div>
                {(referralInfo?.invited_users || []).length === 0 ? (
                  <div className="py-5 text-center text-dim">
                    暂未邀请好友，复制上方邀请码/链接分享给好友即可双方各得 {refSettings.inviter_reward_points} 积分
                  </div>
                ) : (
                  <div className="space-y-2 max-h-52 overflow-y-auto pr-0.5">
                    {(referralInfo?.invited_users || []).map((iu) => (
                      <div
                        key={iu.id}
                        className="surface-card px-3.5 py-2.5 flex items-center justify-between gap-2"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-mono font-bold text-main truncate">{iu.username}</span>
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              iu.is_vip
                                ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                                : 'surface-subtle text-dim'
                            }`}
                          >
                            {iu.is_vip ? iu.vip_label : '未开VIP'}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-dim shrink-0">{iu.created_at}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Right 6 Cols: Points-to-VIP Redemption + Personal Points Ledger */}
            <div className="lg:col-span-6 space-y-4">
              <form onSubmit={handleRedeemPointsForVip} className="surface-subtle p-4 sm:p-5 space-y-4 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Award className="w-4 h-4 text-amber-500 shrink-0" strokeWidth={1.75} />
                    <span className="font-bold text-main">积分兑换实盘 VIP 会员</span>
                  </div>
                  <span className="font-mono text-[11px] font-semibold text-amber-700 dark:text-amber-300">
                    {pointsPerHour} 积分 / 小时
                  </span>
                </div>

                {!refSettings.redeem_enabled && (
                  <div className="p-3 rounded-2xl bg-rose-500/10 border border-rose-500/25 text-rose-700 dark:text-rose-300">
                    管理员已暂时关闭积分兑换会员功能，请稍后再试。
                  </div>
                )}

                <div>
                  <label className="block font-semibold text-main mb-2">选择或输入兑换小时数</label>
                  <div className="flex flex-wrap items-center gap-1.5 mb-2.5">
                    {[1, 2, 6, 12, 24].map((h) => (
                      <button
                        key={h}
                        type="button"
                        onClick={() => setRedeemHours(h)}
                        className={`px-3 py-1.5 text-[11px] font-mono ${
                          safeRedeemHours === h ? 'btn-primary-pill' : 'btn-soft-pill'
                        }`}
                      >
                        +{h}小时 ({h * pointsPerHour}分)
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2.5">
                    <div className="flex items-center gap-2 flex-1">
                      <input
                        type="number"
                        min={1}
                        max={refSettings.max_redeem_hours_per_tx || 720}
                        value={redeemHours}
                        onChange={(e) => setRedeemHours(e.target.value)}
                        className="w-full input-field px-3.5 py-2.5 font-mono text-base sm:text-xs"
                      />
                      <span className="text-sub whitespace-nowrap">小时 VIP</span>
                    </div>
                    <button
                      type="submit"
                      disabled={
                        redeemPointsBusy ||
                        !refSettings.redeem_enabled ||
                        myPointsBalance < redeemCostPoints
                      }
                      className="btn-primary-pill px-5 py-2.5 text-xs whitespace-nowrap disabled:opacity-55 disabled:cursor-not-allowed"
                    >
                      <Coins className="w-3.5 h-3.5" strokeWidth={1.75} />
                      <span>
                        {redeemPointsBusy
                          ? '正在兑换...'
                          : `立即兑换 (+${safeRedeemHours}小时)`}
                      </span>
                    </button>
                  </div>
                </div>

                <div className="surface-card p-3.5 flex flex-wrap items-center justify-between gap-2 text-[11px]">
                  <span className="text-sub">
                    本次消耗：
                    <span className="font-mono font-bold text-main ml-1">{redeemCostPoints} 积分</span>
                  </span>
                  <span className="text-sub">
                    兑换后剩余：
                    <span
                      className={`font-mono font-bold ml-1 ${
                        myPointsBalance >= redeemCostPoints
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-rose-600 dark:text-rose-400'
                      }`}
                    >
                      {myPointsBalance >= redeemCostPoints
                        ? `${myPointsBalance - redeemCostPoints} 积分`
                        : `积分不足（差 ${redeemCostPoints - myPointsBalance} 分）`}
                    </span>
                  </span>
                </div>
              </form>

              {/* Personal Points Ledger */}
              <div className="surface-subtle p-4 space-y-2.5 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-main">我的积分明细流水</span>
                  <span className="text-[11px] font-mono text-dim">
                    最近 {(referralInfo?.ledger || []).length} 条
                  </span>
                </div>
                {(referralInfo?.ledger || []).length === 0 ? (
                  <div className="py-5 text-center text-dim">暂无积分变动记录</div>
                ) : (
                  <div className="space-y-2 max-h-56 overflow-y-auto pr-0.5">
                    {(referralInfo?.ledger || []).map((row) => {
                      const delta = Number(row.change_points || 0);
                      return (
                        <div
                          key={row.id}
                          className="surface-card px-3.5 py-2.5 flex items-start justify-between gap-3"
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold surface-subtle text-main">
                                {formatPointsActionLabel(row.action_type)}
                              </span>
                              <span className="text-[11px] font-mono text-dim">{row.created_at}</span>
                            </div>
                            <div className="text-sub mt-1 break-words">{row.note}</div>
                          </div>
                          <div className="text-right font-mono shrink-0">
                            <div
                              className={`font-bold ${
                                delta >= 0
                                  ? 'text-emerald-600 dark:text-emerald-400'
                                  : 'text-rose-600 dark:text-rose-400'
                              }`}
                            >
                              {delta >= 0 ? `+${delta}` : delta}
                            </div>
                            <div className="text-[10px] text-dim">余 {row.balance_after}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 5.5 User Issue / Feedback Message Board */}
      <div id="user-feedback-board" className="surface-card p-4 sm:p-6 lg:p-7 space-y-5 scroll-mt-20">
        <div className="flex flex-wrap items-start sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5">
              <MessageSquare className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
              <h3 className="text-sm sm:text-base font-bold text-main">用户问题留言板</h3>
              {currentUser && (
                <span className="px-2.5 py-0.5 rounded-full text-[11px] font-mono font-semibold surface-subtle text-sub">
                  共 {(feedbackData.messages || []).length} 条
                </span>
              )}
            </div>
            <p className="text-xs text-dim mt-1">
              提交使用问题或建议，管理员将在后台查看并回复 · 每 24 小时仅可提交一次 · 留言满 24 小时后可删除个人侧记录（管理员侧留存不受影响）
            </p>
          </div>
          {currentUser && (
            <button
              type="button"
              onClick={fetchMyFeedback}
              disabled={feedbackLoading}
              className="btn-soft-pill px-4 py-1.5 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${feedbackLoading ? 'animate-spin' : ''}`} strokeWidth={1.75} />
              <span>刷新留言</span>
            </button>
          )}
        </div>

        {!currentUser ? (
          <div className="surface-subtle p-6 text-center space-y-3">
            <p className="text-xs text-sub">登录后即可向管理员提交问题留言并查看专属回复记录</p>
            <button
              type="button"
              onClick={() => onOpenAuthModal('login')}
              className="btn-primary-pill px-5 py-2 text-xs"
            >
              登录账号提交留言
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
            {/* Left 5 Cols: Submit Form */}
            <form onSubmit={handleSubmitFeedback} className="lg:col-span-5 surface-subtle p-4 sm:p-5 space-y-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-bold text-main">提交新问题留言</span>
                <span
                  className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                    canSubmitFeedbackNow
                      ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                      : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
                  }`}
                >
                  {canSubmitFeedbackNow ? '当前可提交' : '24h 冷却中'}
                </span>
              </div>

              {!canSubmitFeedbackNow && (
                <div className="p-3 rounded-2xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-800 dark:text-amber-200 flex items-start gap-2">
                  <Clock className="w-4 h-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" strokeWidth={1.75} />
                  <div className="leading-relaxed">
                    <div className="font-semibold">24 小时内仅允许提交一次留言</div>
                    <div className="text-[11px] opacity-90 mt-0.5 font-mono">
                      距离下次可提交还剩：{formatDurationCn(submitCooldownRemaining)}
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className="block text-xs font-semibold text-main mb-2">问题分类</label>
                <div className="flex flex-wrap gap-1.5">
                  {FEEDBACK_CATEGORIES.map((cat) => (
                    <button
                      key={cat}
                      type="button"
                      disabled={!canSubmitFeedbackNow}
                      onClick={() => setFeedbackCategory(cat)}
                      className={`px-3 py-1.5 text-[11px] transition ${
                        feedbackCategory === cat ? 'btn-primary-pill' : 'btn-soft-pill'
                      } ${!canSubmitFeedbackNow ? 'opacity-60 cursor-not-allowed' : ''}`}
                    >
                      {cat}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-main mb-1.5">问题标题（选填）</label>
                <input
                  type="text"
                  maxLength={120}
                  disabled={!canSubmitFeedbackNow || feedbackSubmitting}
                  value={feedbackTitle}
                  onChange={(e) => setFeedbackTitle(e.target.value)}
                  placeholder="简要概括您的问题，如：多空震荡止盈疑问"
                  className="w-full input-field px-3.5 py-2.5 text-base sm:text-xs"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-semibold text-main">问题描述（必填）</label>
                  <span className="text-[11px] font-mono text-dim">{feedbackContent.length} / 2000</span>
                </div>
                <textarea
                  rows={5}
                  maxLength={2000}
                  required
                  disabled={!canSubmitFeedbackNow || feedbackSubmitting}
                  value={feedbackContent}
                  onChange={(e) => setFeedbackContent(e.target.value)}
                  placeholder="请详细描述您遇到的问题、涉及的币种/时间点或改进建议，以便管理员快速定位与回复..."
                  className="w-full input-field p-3.5 text-base sm:text-xs leading-relaxed resize-y"
                />
              </div>

              <button
                type="submit"
                disabled={!canSubmitFeedbackNow || feedbackSubmitting}
                className="btn-primary-pill w-full py-2.5 text-xs disabled:opacity-55 disabled:cursor-not-allowed"
              >
                <Send className="w-3.5 h-3.5" strokeWidth={1.75} />
                <span>
                  {feedbackSubmitting
                    ? '正在提交...'
                    : canSubmitFeedbackNow
                      ? '提交问题留言'
                      : `冷却中 · 还剩 ${formatDurationCn(submitCooldownRemaining)}`}
                </span>
              </button>
            </form>

            {/* Right 7 Cols: My Feedback History & Admin Replies */}
            <div className="lg:col-span-7 space-y-3">
              <div className="flex items-center justify-between text-xs px-1">
                <span className="font-bold text-main">我的留言与管理员回复记录</span>
                <span className="text-[11px] text-dim">
                  已回复 {feedbackData.replied_count || 0} / {(feedbackData.messages || []).length}
                </span>
              </div>

              {(feedbackData.messages || []).length === 0 ? (
                <div className="surface-subtle p-8 text-center text-xs text-dim">
                  暂无问题留言记录，如遇疑问可在左侧提交留言给管理员
                </div>
              ) : (
                <div className="space-y-3 max-h-[540px] overflow-y-auto pr-0.5">
                  {(feedbackData.messages || []).map((m) => {
                    const delRem = Math.max(
                      0,
                      Number(m.delete_unlock_remaining_sec || 0) - feedbackElapsedSec
                    );
                    const canDelete = delRem <= 0;
                    const isConfirming = confirmDeleteFeedbackId === m.id;
                    return (
                      <div key={m.id} className="surface-subtle p-4 space-y-3 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                            <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold surface-card text-main">
                              {m.category || '问题反馈'}
                            </span>
                            {m.title && (
                              <span className="font-bold text-main break-all">{m.title}</span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span
                              className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                                m.status === 'REPLIED'
                                  ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                                  : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
                              }`}
                            >
                              {m.status === 'REPLIED' ? '管理员已回复' : '待管理员回复'}
                            </span>
                            <span className="text-[11px] font-mono text-dim">{m.created_at}</span>
                          </div>
                        </div>

                        <div className="text-main leading-relaxed whitespace-pre-wrap break-words">
                          {m.content}
                        </div>

                        {m.admin_reply ? (
                          <div className="surface-card p-3.5 border border-emerald-500/20 space-y-1.5">
                            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                              <span className="inline-flex items-center gap-1.5 font-bold text-emerald-700 dark:text-emerald-400">
                                <CheckCircle2 className="w-3.5 h-3.5" strokeWidth={2} />
                                <span>管理员回复{m.replied_by ? ` (${m.replied_by})` : ''}</span>
                              </span>
                              <span className="font-mono text-dim">{m.replied_at}</span>
                            </div>
                            <div className="text-sub leading-relaxed whitespace-pre-wrap break-words">
                              {m.admin_reply}
                            </div>
                          </div>
                        ) : (
                          <div className="text-[11px] text-dim italic">
                            您的留言已送达管理员后台，请耐心等待回复...
                          </div>
                        )}

                        <div className="pt-2 border-t border-line flex flex-wrap items-center justify-between gap-2 text-[11px]">
                          <span className="text-dim font-mono">工单 #{m.id}</span>
                          {!canDelete ? (
                            <span
                              className="inline-flex items-center gap-1 text-dim font-mono"
                              title="留言提交满 24 小时后才允许用户删除本地记录"
                            >
                              <Clock className="w-3 h-3" strokeWidth={1.75} />
                              <span>满 24 小时后可删（还剩 {formatDurationCn(delRem)}）</span>
                            </span>
                          ) : isConfirming ? (
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="text-rose-600 dark:text-rose-400">
                                仅删除您侧记录（管理员仍留存），确认？
                              </span>
                              <button
                                type="button"
                                disabled={feedbackDeletingId === m.id}
                                onClick={() => handleDeleteMyFeedback(m.id)}
                                className="btn-primary-pill px-3 py-1 text-[11px]"
                              >
                                {feedbackDeletingId === m.id ? '删除中...' : '确认删除'}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteFeedbackId(null)}
                                className="btn-soft-pill px-2.5 py-1 text-[11px]"
                              >
                                取消
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteFeedbackId(m.id)}
                              className="btn-soft-pill px-3 py-1 text-[11px] text-rose-600 dark:text-rose-400"
                            >
                              <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                              <span>删除我的记录</span>
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
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
                  <span>
                    {o.plan_name} (
                    {o.duration_days > 0
                      ? `+${o.duration_days}天`
                      : o.pay_method === 'POINTS_REDEEM'
                        ? '积分按小时'
                        : '按小时授权'}
                    )
                  </span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                    {o.pay_method === 'POINTS_REDEEM'
                      ? '积分兑换'
                      : `$${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}`}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-dim truncate" title={o.tx_hash}>
                  {o.pay_method === 'POINTS_REDEEM'
                    ? '积分兑换'
                    : o.chain_network === 'MULTI_EVM'
                      ? '10链自动监控'
                      : o.chain_network || o.pay_method}{' '}
                  · {o.tx_hash}
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
                    <td className="py-3 px-3 text-sub">
                      {o.duration_days > 0
                        ? `+${o.duration_days} 天`
                        : o.pay_method === 'POINTS_REDEEM'
                          ? '按小时兑换'
                          : '按小时授权'}
                    </td>
                    <td className="py-3 px-3 text-emerald-600 dark:text-emerald-400 font-bold">
                      {o.pay_method === 'POINTS_REDEEM'
                        ? '积分兑换'
                        : `$${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}`}
                    </td>
                    <td className="py-3 px-3 font-sans text-sub">
                      {o.pay_method === 'POINTS_REDEEM'
                        ? '积分兑换'
                        : o.chain_network === 'MULTI_EVM'
                          ? '10链自动监控'
                          : o.chain_network || o.pay_method}
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
  const [subTab, setSubTab] = useState('users'); // 'users' | 'orders' | 'plans' | 'codes' | 'feedback' | 'referral'
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
      total_feedback_count: 0,
      open_feedback_count: 0,
      replied_feedback_count: 0,
      total_points_balance: 0,
      total_invites_count: 0,
      total_redeem_count: 0,
      total_redeem_points: 0,
    },
    users: [],
    orders: [],
    vip_codes: [],
    plans: [],
    feedback_messages: [],
    referral_settings: {
      invite_enabled: true,
      inviter_reward_points: 100,
      invitee_reward_points: 100,
      redeem_enabled: true,
      points_per_vip_hour: 100,
      max_redeem_hours_per_tx: 720,
    },
    referral_records: [],
    points_ledger: [],
  });
  const [userSearch, setUserSearch] = useState('');
  const [orderFilter, setOrderFilter] = useState('ALL');
  const [confirmDeleteOrderId, setConfirmDeleteOrderId] = useState(null);
  const [confirmClearClosedOrders, setConfirmClearClosedOrders] = useState(false);
  const [feedbackFilter, setFeedbackFilter] = useState('ALL');
  const [feedbackSearch, setFeedbackSearch] = useState('');
  const [replyDrafts, setReplyDrafts] = useState({});
  const [editingReplyId, setEditingReplyId] = useState(null);
  const [feedbackBusyId, setFeedbackBusyId] = useState(null);
  const [confirmAdminDeleteFeedbackId, setConfirmAdminDeleteFeedbackId] = useState(null);

  const [refSettingsForm, setRefSettingsForm] = useState({
    invite_enabled: true,
    inviter_reward_points: 100,
    invitee_reward_points: 100,
    redeem_enabled: true,
    points_per_vip_hour: 100,
    max_redeem_hours_per_tx: 720,
  });
  const [pointsAdjustForm, setPointsAdjustForm] = useState({
    user_id: '',
    delta_points: 100,
    note: '',
  });
  const [ledgerFilter, setLedgerFilter] = useState('ALL');
  const [ledgerSearch, setLedgerSearch] = useState('');
  const [referralBusy, setReferralBusy] = useState(false);

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
  const [grantHours, setGrantHours] = useState(0);
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
      if (data.referral_settings) {
        setRefSettingsForm({
          invite_enabled: Boolean(data.referral_settings.invite_enabled),
          inviter_reward_points: Number(data.referral_settings.inviter_reward_points ?? 100),
          invitee_reward_points: Number(data.referral_settings.invitee_reward_points ?? 100),
          redeem_enabled: Boolean(data.referral_settings.redeem_enabled),
          points_per_vip_hour: Number(data.referral_settings.points_per_vip_hour ?? 100),
          max_redeem_hours_per_tx: Number(data.referral_settings.max_redeem_hours_per_tx ?? 720),
        });
      }
      if (data.users?.length > 0) {
        setPointsAdjustForm((prev) => (prev.user_id ? prev : { ...prev, user_id: String(data.users[0].id) }));
      }
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

  const handleGrantVip = async (userId, addDays, vipTier, revoke = false, addHours = 0) => {
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
          add_days: Number(addDays || 0),
          add_hours: Number(addHours || 0),
          vip_tier: vipTier,
          revoke,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message, 'success');
        setDashboard(data.dashboard);
        setGrantTarget(null);
        setGrantHours(0);
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

  const handleDeleteOrder = async (orderId) => {
    if (adminBusyRef.current) return;
    adminBusyRef.current = true;
    setAdminBusy(true);
    try {
      const res = await fetch(`/api/admin/orders/${orderId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message || '订单记录已删除', 'info');
        setDashboard(data.dashboard);
        setConfirmDeleteOrderId(null);
      } else {
        showToast(data.detail || '删除订单失败', 'error');
      }
    } finally {
      adminBusyRef.current = false;
      setAdminBusy(false);
    }
  };

  const handleClearClosedOrders = async () => {
    if (adminBusyRef.current) return;
    adminBusyRef.current = true;
    setAdminBusy(true);
    try {
      const res = await fetch('/api/admin/orders/cleanup-closed', {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message || '已清理已关闭订单', 'info');
        setDashboard(data.dashboard);
        setConfirmClearClosedOrders(false);
        setConfirmDeleteOrderId(null);
      } else {
        showToast(data.detail || '批量清理失败', 'error');
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

  const handleAdminReplyFeedback = async (messageId, fallbackReply = '') => {
    const replyText = (replyDrafts[messageId] !== undefined ? replyDrafts[messageId] : fallbackReply).trim();
    if (!replyText) {
      showToast('请输入回复内容后再发送', 'error');
      return;
    }
    setFeedbackBusyId(messageId);
    try {
      const res = await fetch(`/api/admin/feedback/${messageId}/reply`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ reply: replyText }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '回复失败', 'error');
      } else {
        showToast(data.message || '管理员回复已保存', 'success');
        if (data.dashboard) setDashboard(data.dashboard);
        setEditingReplyId(null);
        setReplyDrafts((prev) => {
          const next = { ...prev };
          delete next[messageId];
          return next;
        });
      }
    } finally {
      setFeedbackBusyId(null);
    }
  };

  const handleAdminDeleteFeedback = async (messageId) => {
    setFeedbackBusyId(messageId);
    try {
      const res = await fetch(`/api/admin/feedback/${messageId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '删除失败', 'error');
      } else {
        showToast(data.message || '已从后台删除该条留言记录', 'info');
        if (data.dashboard) setDashboard(data.dashboard);
        setConfirmAdminDeleteFeedbackId(null);
      }
    } finally {
      setFeedbackBusyId(null);
    }
  };

  const handleSaveReferralSettings = async (e) => {
    e.preventDefault();
    setReferralBusy(true);
    try {
      const res = await fetch('/api/admin/referral/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          invite_enabled: Boolean(refSettingsForm.invite_enabled),
          inviter_reward_points: Math.max(0, parseInt(refSettingsForm.inviter_reward_points, 10) || 0),
          invitee_reward_points: Math.max(0, parseInt(refSettingsForm.invitee_reward_points, 10) || 0),
          redeem_enabled: Boolean(refSettingsForm.redeem_enabled),
          points_per_vip_hour: Math.max(1, parseInt(refSettingsForm.points_per_vip_hour, 10) || 100),
          max_redeem_hours_per_tx: Math.max(1, parseInt(refSettingsForm.max_redeem_hours_per_tx, 10) || 720),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '保存邀请积分规则失败', 'error');
      } else {
        showToast(data.message || '邀请与积分兑换规则已更新', 'success');
        if (data.dashboard) setDashboard(data.dashboard);
      }
    } finally {
      setReferralBusy(false);
    }
  };

  const handleAdjustUserPoints = async (e) => {
    e.preventDefault();
    const targetUid = parseInt(pointsAdjustForm.user_id, 10);
    const delta = parseInt(pointsAdjustForm.delta_points, 10);
    if (!targetUid) {
      showToast('请选择要调整积分的用户', 'error');
      return;
    }
    if (!delta || delta === 0) {
      showToast('请输入非零的积分调整数量（正数增加，负数扣减）', 'error');
      return;
    }
    setReferralBusy(true);
    try {
      const res = await fetch('/api/admin/points/adjust', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          user_id: targetUid,
          delta_points: delta,
          note: (pointsAdjustForm.note || '').trim(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.message || '调整积分失败', 'error');
      } else {
        showToast(data.message || '用户积分调整成功', 'success');
        if (data.dashboard) setDashboard(data.dashboard);
        setPointsAdjustForm((prev) => ({ ...prev, note: '' }));
        onCurrentUserRefresh();
      }
    } finally {
      setReferralBusy(false);
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
      (u.last_login_ip || '').toLowerCase().includes(q) ||
      (u.invite_code || '').toLowerCase().includes(q) ||
      (u.invited_by_username || '').toLowerCase().includes(q)
    );
  });

  const filteredPointsLedger = (dashboard.points_ledger || []).filter((row) => {
    if (
      ledgerFilter === 'INVITE' &&
      !['INVITE_REWARD_INVITER', 'INVITE_REWARD_INVITEE', 'INVITE_INVITER', 'INVITE_INVITEE'].includes(row.action_type)
    ) {
      return false;
    }
    if (ledgerFilter === 'REDEEM_VIP' && row.action_type !== 'REDEEM_VIP') return false;
    if (ledgerFilter === 'ADMIN_ADJUST' && row.action_type !== 'ADMIN_ADJUST') return false;
    const q = ledgerSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      (row.username || '').toLowerCase().includes(q) ||
      (row.related_username || '').toLowerCase().includes(q) ||
      (row.note || '').toLowerCase().includes(q)
    );
  });

  const filteredOrders = (dashboard.orders || []).filter((o) => {
    if (orderFilter === 'ALL') return true;
    if (orderFilter === 'EXPIRED') return o.status === 'EXPIRED' || o.status === 'CANCELLED';
    return o.status === orderFilter;
  });

  const filteredFeedback = (dashboard.feedback_messages || []).filter((m) => {
    if (feedbackFilter === 'OPEN' && m.status !== 'OPEN') return false;
    if (feedbackFilter === 'REPLIED' && m.status !== 'REPLIED') return false;
    const q = feedbackSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      (m.username || '').toLowerCase().includes(q) ||
      (m.category || '').toLowerCase().includes(q) ||
      (m.title || '').toLowerCase().includes(q) ||
      (m.content || '').toLowerCase().includes(q) ||
      (m.admin_reply || '').toLowerCase().includes(q)
    );
  });

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* 1. Top Header Banner Card */}
      <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-main">后台用户与会员管理系统</h2>
          <p className="text-xs text-dim mt-1">
            管理注册用户账号、手动开通/收回 VIP、监控链上订单、批量制卡以及查看回复用户问题留言
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
          { id: 'users', label: `后台用户管理 (${(dashboard.users || []).length})`, icon: Users, badge: 0 },
          { id: 'orders', label: `链上订单与审核 (${(dashboard.orders || []).length})`, icon: CreditCard, badge: 0 },
          { id: 'plans', label: `会员套餐配置 (${(dashboard.plans || []).length})`, icon: Sliders, badge: 0 },
          { id: 'codes', label: `会员卡密生成 (${(dashboard.vip_codes || []).length})`, icon: Key, badge: 0 },
          {
            id: 'referral',
            label: `邀请与积分管理 (${st.total_invites_count || 0})`,
            icon: Gift,
            badge: 0,
          },
          {
            id: 'feedback',
            label: `用户问题留言 (${st.total_feedback_count ?? (dashboard.feedback_messages || []).length})`,
            icon: MessageSquare,
            badge: Number(st.open_feedback_count || 0),
          },
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
              {t.badge > 0 && (
                <span
                  className={`ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                    active
                      ? 'bg-amber-400 text-slate-950'
                      : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                  }`}
                >
                  {t.badge} 待复
                </span>
              )}
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
                    min={0}
                    max={36500}
                    value={grantDays}
                    onChange={(e) => setGrantDays(Number(e.target.value))}
                    className="input-field px-3 py-2 w-20 font-mono"
                  />
                  <span>增加小时:</span>
                  <input
                    type="number"
                    min={0}
                    max={87600}
                    value={grantHours}
                    onChange={(e) => setGrantHours(Number(e.target.value))}
                    className="input-field px-3 py-2 w-20 font-mono"
                  />
                  {[
                    { label: '+1小时', d: 0, h: 1 },
                    { label: '+12小时', d: 0, h: 12 },
                    { label: '+24小时', d: 0, h: 24 },
                    { label: '+7天', d: 7, h: 0 },
                    { label: '+30天', d: 30, h: 0 },
                    { label: '+90天', d: 90, h: 0 },
                    { label: '+365天', d: 365, h: 0 },
                    { label: '+永久', d: 36500, h: 0 },
                  ].map((opt) => (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => {
                        setGrantDays(opt.d);
                        setGrantHours(opt.h);
                      }}
                      className="px-2.5 py-1.5 rounded-full surface-subtle text-[11px] font-mono hover:opacity-85"
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
                <button
                  onClick={() => handleGrantVip(grantTarget.id, grantDays, grantTier, false, grantHours)}
                  className="btn-primary-pill px-5 py-2 text-xs"
                >
                  确认开通 / 延期会员
                </button>
                {grantTarget.username !== 'admin' && (
                  <button
                    onClick={() => handleGrantVip(grantTarget.id, 0, 'NONE', true, 0)}
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
                  placeholder="搜索用户名 / 邮箱 / 邀请码 / 会员等级..."
                  className="w-full input-field rounded-full pl-10 pr-4 py-2.5 text-base sm:text-xs"
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[1040px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-3 px-3">ID</th>
                    <th className="py-3 px-3">用户名 / 邮箱</th>
                    <th className="py-3 px-3">专属邀请码 / 积分</th>
                    <th className="py-3 px-3">注册 / 最近登录 IP</th>
                    <th className="py-3 px-3">角色</th>
                    <th className="py-3 px-3">会员等级</th>
                    <th className="py-3 px-3">到期 / 剩余时长</th>
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
                          <div className="font-bold text-main">{u.invite_code || '--'}</div>
                          <div className="text-amber-600 dark:text-amber-400 font-semibold">
                            {Number(u.points_balance || 0)} 积分 · 邀 {Number(u.total_invites_count || 0)} 人
                          </div>
                          {u.invited_by_username && (
                            <div className="text-[10px] text-dim">受邀自: {u.invited_by_username}</div>
                          )}
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
                        <td className="py-3.5 px-3 font-mono text-sub">
                          <div>{u.vip_expires_str}</div>
                          {u.is_vip && u.vip_remaining_label && (
                            <div className="text-[10px] text-emerald-600 dark:text-emerald-400">
                              剩 {u.vip_remaining_label}
                            </div>
                          )}
                          {!u.is_vip && Number(u.vip_expires_at || 0) > 0 && (
                            <div className="text-[10px] text-rose-500">已到期</div>
                          )}
                        </td>
                        <td className="py-3.5 px-3 font-mono text-[11px]">
                          <div className="text-main">
                            {lw.symbols_count || 0} 币种
                            {lw.running_symbols_count > 0 ? ` (${lw.running_symbols_count}运行)` : ''} ·{' '}
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
                        <td className="py-3.5 px-3 text-right space-x-1.5 whitespace-nowrap">
                          <button
                            onClick={() => {
                              setGrantTarget(u);
                              setEditUserTarget(null);
                              setGrantDays(30);
                              setGrantHours(0);
                              setGrantTier(u.vip_tier !== 'NONE' ? u.vip_tier : '季度旗舰版会员');
                            }}
                            className="btn-primary-pill px-3 py-1.5 text-[11px]"
                          >
                            会员授权
                          </button>
                          <button
                            onClick={() => {
                              setPointsAdjustForm({
                                user_id: String(u.id),
                                delta_points: 100,
                                note: '',
                              });
                              setSubTab('referral');
                            }}
                            className="btn-soft-pill px-2.5 py-1.5 text-[11px]"
                          >
                            <Coins className="w-3 h-3" strokeWidth={1.75} />
                            <span>调积分</span>
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
              {(dashboard.orders || []).some((o) => ['EXPIRED', 'CANCELLED', 'REJECTED'].includes(o.status)) && (
                confirmClearClosedOrders ? (
                  <div className="flex items-center gap-1.5 pl-1">
                    <span className="text-[11px] text-rose-600 dark:text-rose-400">清理全部超时/取消/拒绝订单？</span>
                    <button
                      type="button"
                      disabled={adminBusy}
                      onClick={handleClearClosedOrders}
                      className="btn-primary-pill px-3 py-1.5 text-[11px]"
                    >
                      确认清理
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmClearClosedOrders(false)}
                      className="btn-soft-pill px-2.5 py-1.5 text-[11px]"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    disabled={adminBusy}
                    onClick={() => setConfirmClearClosedOrders(true)}
                    className="btn-soft-pill px-3 py-1.5 sm:py-2 text-xs text-rose-600 dark:text-rose-400"
                  >
                    <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                    <span>清理已关闭订单</span>
                  </button>
                )
              )}
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
                      <td className="py-3.5 px-3 text-sub">
                        {o.duration_days > 0
                          ? `+${o.duration_days} 天`
                          : o.pay_method === 'POINTS_REDEEM'
                            ? '按小时兑换'
                            : '按小时授权'}
                      </td>
                      <td className="py-3.5 px-3 font-bold text-emerald-600 dark:text-emerald-400">
                        {o.pay_method === 'POINTS_REDEEM'
                          ? '积分兑换'
                          : `$${o.pay_amount_str || Number(o.amount_usdt).toFixed(3)}`}
                      </td>
                      <td className="py-3.5 px-3 font-sans text-sub">
                        {o.pay_method === 'POINTS_REDEEM'
                          ? '积分兑换'
                          : o.chain_network === 'MULTI_EVM'
                            ? '10链监控'
                            : o.chain_network || o.pay_method}
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
                        {confirmDeleteOrderId === o.id ? (
                          <div className="inline-flex items-center justify-end gap-1.5">
                            <span className="text-[11px] text-rose-600 dark:text-rose-400">
                              {o.status === 'PAID' ? '仅删流水不收回VIP，确认？' : '确认删除此订单？'}
                            </span>
                            <button
                              type="button"
                              disabled={adminBusy}
                              onClick={() => handleDeleteOrder(o.id)}
                              className="btn-primary-pill px-3 py-1 text-[11px]"
                            >
                              确认删除
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteOrderId(null)}
                              className="btn-soft-pill px-2.5 py-1 text-[11px]"
                            >
                              取消
                            </button>
                          </div>
                        ) : (
                          <>
                            {o.status !== 'PAID' ? (
                              <>
                                <button
                                  type="button"
                                  disabled={adminBusy}
                                  onClick={() => handleAuditOrder(o.id, 'APPROVE')}
                                  className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
                                >
                                  手动确认并开通
                                </button>
                                {o.status === 'PENDING' && (
                                  <button
                                    type="button"
                                    disabled={adminBusy}
                                    onClick={() => handleAuditOrder(o.id, 'EXPIRE')}
                                    className="btn-soft-pill px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400"
                                  >
                                    关闭监控
                                  </button>
                                )}
                              </>
                            ) : (
                              <span className="text-dim mr-1">已归档</span>
                            )}
                            <button
                              type="button"
                              disabled={adminBusy}
                              onClick={() => setConfirmDeleteOrderId(o.id)}
                              title="删除此条订单流水记录"
                              className="btn-soft-pill px-2.5 py-1.5 text-[11px] text-rose-600 dark:text-rose-400"
                            >
                              <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                              <span>删除</span>
                            </button>
                          </>
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

      {/* ==================== SUB-TAB 5: USER FEEDBACK & ADMIN REPLIES ==================== */}
      {subTab === 'feedback' && (
        <div className="surface-card p-4 sm:p-6 lg:p-7 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2.5">
                <MessageSquare className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                <h3 className="text-sm sm:text-base font-bold text-main">用户问题留言箱与后台回复</h3>
              </div>
              <p className="text-xs text-dim mt-1">
                用户提交的留言记录均会保留于此（即使用户满 24 小时后删除了个人侧记录，管理员后台仍完整保留直至管理员手动删除）
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2 w-full lg:w-auto">
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                {[
                  { k: 'ALL', l: `全部 (${st.total_feedback_count ?? (dashboard.feedback_messages || []).length})` },
                  { k: 'OPEN', l: `待回复 (${st.open_feedback_count || 0})` },
                  { k: 'REPLIED', l: `已回复 (${st.replied_feedback_count || 0})` },
                ].map((f) => (
                  <button
                    key={f.k}
                    type="button"
                    onClick={() => setFeedbackFilter(f.k)}
                    className={`px-3 sm:px-4 py-1.5 sm:py-2 text-xs ${
                      feedbackFilter === f.k ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    {f.l}
                  </button>
                ))}
              </div>
              <div className="relative w-full sm:w-64">
                <Search className="w-4 h-4 text-dim absolute left-3.5 top-2.5" strokeWidth={1.75} />
                <input
                  type="text"
                  value={feedbackSearch}
                  onChange={(e) => setFeedbackSearch(e.target.value)}
                  placeholder="搜索用户名 / 标题 / 留言内容..."
                  className="w-full input-field rounded-full pl-10 pr-4 py-2 text-base sm:text-xs"
                />
              </div>
            </div>
          </div>

          {filteredFeedback.length === 0 ? (
            <div className="surface-subtle p-10 text-center text-xs text-dim">
              暂无符合条件的用户问题留言记录
            </div>
          ) : (
            <div className="space-y-4">
              {filteredFeedback.map((m) => {
                const isEditingReply = m.status !== 'REPLIED' || editingReplyId === m.id;
                const draftVal =
                  replyDrafts[m.id] !== undefined ? replyDrafts[m.id] : m.admin_reply || '';
                const isConfirmDelete = confirmAdminDeleteFeedbackId === m.id;
                return (
                  <div key={m.id} className="surface-subtle p-4 sm:p-5 space-y-3.5 text-xs">
                    {/* Meta Header */}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-dim">#{m.id}</span>
                        <span className="px-2.5 py-0.5 rounded-full surface-card font-mono font-bold text-main">
                          {m.username}
                        </span>
                        <span className="px-2.5 py-0.5 rounded-full surface-card text-sub font-medium">
                          {m.category || '问题反馈'}
                        </span>
                        <span
                          className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                            m.status === 'REPLIED'
                              ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                              : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
                          }`}
                        >
                          {m.status === 'REPLIED' ? '已回复' : '待回复'}
                        </span>
                        {m.user_deleted && (
                          <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-slate-500/15 text-sub">
                            用户侧已删除
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] font-mono text-dim">{m.created_at}</span>
                    </div>

                    {/* Title & Content */}
                    <div className="space-y-1.5">
                      {m.title && (
                        <div className="text-sm font-bold text-main break-words">{m.title}</div>
                      )}
                      <div className="surface-card p-3.5 text-main leading-relaxed whitespace-pre-wrap break-words">
                        {m.content}
                      </div>
                    </div>

                    {/* Existing Reply Display OR Inline Reply Form */}
                    {!isEditingReply ? (
                      <div className="surface-card p-3.5 border border-emerald-500/25 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                          <span className="inline-flex items-center gap-1.5 font-bold text-emerald-700 dark:text-emerald-400">
                            <CheckCircle2 className="w-3.5 h-3.5" strokeWidth={2} />
                            <span>已回复 · {m.replied_by || 'admin'}</span>
                          </span>
                          <div className="flex items-center gap-2.5">
                            <span className="font-mono text-dim">{m.replied_at}</span>
                            <button
                              type="button"
                              onClick={() => {
                                setEditingReplyId(m.id);
                                setReplyDrafts((prev) => ({ ...prev, [m.id]: m.admin_reply || '' }));
                              }}
                              className="btn-soft-pill px-2.5 py-1 text-[11px]"
                            >
                              <Edit3 className="w-3 h-3" strokeWidth={1.75} />
                              <span>修改回复</span>
                            </button>
                          </div>
                        </div>
                        <div className="text-sub leading-relaxed whitespace-pre-wrap break-words">
                          {m.admin_reply}
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2.5">
                        <textarea
                          rows={3}
                          maxLength={2000}
                          value={draftVal}
                          onChange={(e) =>
                            setReplyDrafts((prev) => ({ ...prev, [m.id]: e.target.value }))
                          }
                          placeholder={`回复用户 ${m.username} 的问题留言，提交后用户端将实时可见...`}
                          className="w-full input-field p-3.5 text-base sm:text-xs leading-relaxed resize-y"
                        />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-[11px] font-mono text-dim">
                            {draftVal.length} / 2000
                          </span>
                          <div className="flex flex-wrap items-center gap-2">
                            {m.status === 'REPLIED' && (
                              <button
                                type="button"
                                onClick={() => setEditingReplyId(null)}
                                className="btn-soft-pill px-3.5 py-1.5 text-[11px]"
                              >
                                取消编辑
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={feedbackBusyId === m.id}
                              onClick={() => handleAdminReplyFeedback(m.id, m.admin_reply || '')}
                              className="btn-primary-pill px-4 py-1.5 text-[11px]"
                            >
                              <Send className="w-3 h-3" strokeWidth={1.75} />
                              <span>
                                {feedbackBusyId === m.id
                                  ? '提交中...'
                                  : m.status === 'REPLIED'
                                    ? '保存更新回复'
                                    : '发送回复'}
                              </span>
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Admin Delete Footer */}
                    <div className="pt-2 border-t border-line flex flex-wrap items-center justify-end gap-2 text-[11px]">
                      {isConfirmDelete ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-rose-600 dark:text-rose-400">
                            确认从管理员后台删除此条留言记录？
                          </span>
                          <button
                            type="button"
                            disabled={feedbackBusyId === m.id}
                            onClick={() => handleAdminDeleteFeedback(m.id)}
                            className="btn-primary-pill px-3 py-1 text-[11px]"
                          >
                            {feedbackBusyId === m.id ? '删除中...' : '确认删除'}
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmAdminDeleteFeedbackId(null)}
                            className="btn-soft-pill px-2.5 py-1 text-[11px]"
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmAdminDeleteFeedbackId(m.id)}
                          className="btn-soft-pill px-3 py-1 text-[11px] text-rose-600 dark:text-rose-400"
                        >
                          <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                          <span>管理员删除记录</span>
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ==================== SUB-TAB 6: REFERRAL INVITES & POINTS MANAGEMENT ==================== */}
      {subTab === 'referral' && (
        <div className="space-y-4 sm:space-y-5">
          {/* 4 Referral / Points KPIs */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <div className="surface-card p-4 sm:p-5 flex items-center gap-3">
              <div className="icon-box w-10 h-10">
                <Coins className="w-4 h-4" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-xl font-bold font-mono text-main">
                  {Number(st.total_points_balance || 0)}
                </div>
                <div className="text-[11px] text-dim truncate">全站用户当前积分余额</div>
              </div>
            </div>
            <div className="surface-card p-4 sm:p-5 flex items-center gap-3">
              <div className="icon-box w-10 h-10">
                <Gift className="w-4 h-4" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-xl font-bold font-mono text-main">
                  {Number(st.total_invites_count || 0)}
                </div>
                <div className="text-[11px] text-dim truncate">累计成功邀请人数</div>
              </div>
            </div>
            <div className="surface-card p-4 sm:p-5 flex items-center gap-3">
              <div className="icon-box w-10 h-10">
                <Award className="w-4 h-4" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-xl font-bold font-mono text-main">
                  {Number(st.total_redeem_count || 0)}
                </div>
                <div className="text-[11px] text-dim truncate">积分兑换会员次数</div>
              </div>
            </div>
            <div className="surface-card p-4 sm:p-5 flex items-center gap-3">
              <div className="icon-box w-10 h-10">
                <BarChart2 className="w-4 h-4" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-xl font-bold font-mono text-main">
                  {Number(st.total_redeem_points || 0)}
                </div>
                <div className="text-[11px] text-dim truncate">累计兑换消耗积分</div>
              </div>
            </div>
          </div>

          {/* Global Rules + Manual Points Adjustment Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-5">
            {/* Left 7 Cols: Global Referral & Redeem Settings */}
            <form onSubmit={handleSaveReferralSettings} className="lg:col-span-7 surface-card p-4 sm:p-6 space-y-4 text-xs">
              <div className="flex items-center gap-2.5">
                <Sliders className="w-4 h-4 text-main shrink-0" strokeWidth={1.75} />
                <div>
                  <h3 className="text-sm sm:text-base font-bold text-main">邀请奖励与积分兑换会员规则配置</h3>
                  <p className="text-[11px] text-dim mt-0.5">
                    实时控制全站邀请注册双向奖励积分以及积分兑换 VIP 会员的汇率与单次上限
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                <label className="surface-subtle p-3.5 flex items-center justify-between gap-2 cursor-pointer">
                  <div>
                    <div className="font-bold text-main">开启邀请注册积分奖励</div>
                    <div className="text-[11px] text-dim">关闭后填邀请码注册不再发放积分</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={Boolean(refSettingsForm.invite_enabled)}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, invite_enabled: e.target.checked })
                    }
                    className="w-4 h-4 accent-emerald-600"
                  />
                </label>

                <label className="surface-subtle p-3.5 flex items-center justify-between gap-2 cursor-pointer">
                  <div>
                    <div className="font-bold text-main">开启积分兑换 VIP 会员</div>
                    <div className="text-[11px] text-dim">关闭后前台将暂停积分自助兑换</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={Boolean(refSettingsForm.redeem_enabled)}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, redeem_enabled: e.target.checked })
                    }
                    className="w-4 h-4 accent-emerald-600"
                  />
                </label>

                <div>
                  <label className="block text-dim mb-1 font-semibold">邀请人奖励积分 (每位好友)</label>
                  <input
                    type="number"
                    min={0}
                    max={1000000}
                    value={refSettingsForm.inviter_reward_points}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, inviter_reward_points: e.target.value })
                    }
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-dim mb-1 font-semibold">受邀新用户奖励积分</label>
                  <input
                    type="number"
                    min={0}
                    max={1000000}
                    value={refSettingsForm.invitee_reward_points}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, invitee_reward_points: e.target.value })
                    }
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-dim mb-1 font-semibold">兑换 1 小时 VIP 所需积分</label>
                  <input
                    type="number"
                    min={1}
                    max={1000000}
                    value={refSettingsForm.points_per_vip_hour}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, points_per_vip_hour: e.target.value })
                    }
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-dim mb-1 font-semibold">单次兑换最大小时数上限</label>
                  <input
                    type="number"
                    min={1}
                    max={8760}
                    value={refSettingsForm.max_redeem_hours_per_tx}
                    onChange={(e) =>
                      setRefSettingsForm({ ...refSettingsForm, max_redeem_hours_per_tx: e.target.value })
                    }
                    className="w-full input-field px-3.5 py-2.5 font-mono"
                  />
                </div>
              </div>

              <div>
                <button
                  type="submit"
                  disabled={referralBusy}
                  className="btn-primary-pill px-6 py-2.5 text-xs"
                >
                  {referralBusy ? '正在保存...' : '保存全局邀请与兑换规则'}
                </button>
              </div>
            </form>

            {/* Right 5 Cols: Manual Points Adjust */}
            <form onSubmit={handleAdjustUserPoints} className="lg:col-span-5 surface-card p-4 sm:p-6 space-y-4 text-xs">
              <div className="flex items-center gap-2.5">
                <Coins className="w-4 h-4 text-main shrink-0" strokeWidth={1.75} />
                <div>
                  <h3 className="text-sm sm:text-base font-bold text-main">手动发放 / 扣减用户积分</h3>
                  <p className="text-[11px] text-dim mt-0.5">
                    输入正数发放活动积分，输入负数扣减异常积分，所有操作均记入积分审计流水
                  </p>
                </div>
              </div>

              <div>
                <label className="block text-dim mb-1 font-semibold">目标用户</label>
                <select
                  value={pointsAdjustForm.user_id}
                  onChange={(e) => setPointsAdjustForm({ ...pointsAdjustForm, user_id: e.target.value })}
                  className="w-full input-field px-3.5 py-2.5 font-mono"
                >
                  <option value="">-- 请选择用户 --</option>
                  {(dashboard.users || []).map((u) => (
                    <option key={u.id} value={String(u.id)}>
                      #{u.id} {u.username}（当前 {Number(u.points_balance || 0)} 积分 · 码 {u.invite_code || '--'}）
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-dim mb-1 font-semibold">变动积分（正数增加 / 负数扣减）</label>
                <input
                  type="number"
                  value={pointsAdjustForm.delta_points}
                  onChange={(e) =>
                    setPointsAdjustForm({ ...pointsAdjustForm, delta_points: e.target.value })
                  }
                  className="w-full input-field px-3.5 py-2.5 font-mono"
                />
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {[100, 300, 500, 1000, -100, -500].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setPointsAdjustForm({ ...pointsAdjustForm, delta_points: v })}
                      className="px-2.5 py-1 rounded-full surface-subtle text-[11px] font-mono hover:opacity-85"
                    >
                      {v > 0 ? `+${v}` : v}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-dim mb-1 font-semibold">备注原因（选填）</label>
                <input
                  type="text"
                  maxLength={200}
                  value={pointsAdjustForm.note}
                  onChange={(e) => setPointsAdjustForm({ ...pointsAdjustForm, note: e.target.value })}
                  placeholder="如：活动奖励 / 客服补偿 / 违规扣回"
                  className="w-full input-field px-3.5 py-2.5"
                />
              </div>

              <button
                type="submit"
                disabled={referralBusy}
                className="btn-primary-pill w-full py-2.5 text-xs"
              >
                {referralBusy ? '正在处理...' : '确认调整用户积分'}
              </button>
            </form>
          </div>

          {/* Referral Relationships Table */}
          <div className="surface-card p-4 sm:p-6">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-sm sm:text-base font-bold text-main">全站邀请关系明细表</h3>
                <p className="text-xs text-dim mt-0.5">
                  记录每一位通过专属邀请码注册的新用户与其邀请人绑定关系
                </p>
              </div>
              <span className="text-xs font-mono text-dim">
                共 {(dashboard.referral_records || []).length} 条记录
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-2.5 px-3">邀请人</th>
                    <th className="py-2.5 px-3">使用邀请码</th>
                    <th className="py-2.5 px-3">受邀新用户</th>
                    <th className="py-2.5 px-3">受邀人当前会员</th>
                    <th className="py-2.5 px-3">注册时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line font-mono">
                  {(dashboard.referral_records || []).length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-dim font-sans">
                        暂无用户邀请注册记录
                      </td>
                    </tr>
                  ) : (
                    (dashboard.referral_records || []).map((rec) => (
                      <tr key={`ref-${rec.invitee_id}`}>
                        <td className="py-3 px-3 font-bold text-main">
                          #{rec.inviter_id} {rec.inviter_username}
                        </td>
                        <td className="py-3 px-3 text-sub">{rec.inviter_code || '--'}</td>
                        <td className="py-3 px-3 font-bold text-emerald-600 dark:text-emerald-400">
                          #{rec.invitee_id} {rec.invitee_username}
                        </td>
                        <td className="py-3 px-3 font-sans">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                              rec.invitee_is_vip
                                ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400'
                                : 'surface-subtle text-dim'
                            }`}
                          >
                            {rec.invitee_is_vip ? '有效 VIP' : '未开 VIP'}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-dim">{rec.created_at}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Full Platform Points Ledger Table */}
          <div className="surface-card p-4 sm:p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm sm:text-base font-bold text-main">全站积分变动审计流水</h3>
                <p className="text-xs text-dim mt-0.5">
                  追踪每笔邀请发分、积分兑换会员及管理员手动调整的余额变化
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2 w-full lg:w-auto">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  {[
                    { k: 'ALL', l: '全部' },
                    { k: 'INVITE', l: '邀请奖励' },
                    { k: 'REDEEM_VIP', l: '积分兑换' },
                    { k: 'ADMIN_ADJUST', l: '管理员调整' },
                  ].map((f) => (
                    <button
                      key={f.k}
                      type="button"
                      onClick={() => setLedgerFilter(f.k)}
                      className={`px-3 py-1.5 text-xs ${
                        ledgerFilter === f.k ? 'btn-primary-pill' : 'btn-soft-pill'
                      }`}
                    >
                      {f.l}
                    </button>
                  ))}
                </div>
                <div className="relative w-full sm:w-56">
                  <Search className="w-4 h-4 text-dim absolute left-3.5 top-2.5" strokeWidth={1.75} />
                  <input
                    type="text"
                    value={ledgerSearch}
                    onChange={(e) => setLedgerSearch(e.target.value)}
                    placeholder="搜索用户名 / 备注..."
                    className="w-full input-field rounded-full pl-10 pr-4 py-2 text-base sm:text-xs"
                  />
                </div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[780px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-2.5 px-3">ID</th>
                    <th className="py-2.5 px-3">用户</th>
                    <th className="py-2.5 px-3">类型</th>
                    <th className="py-2.5 px-3">变动积分</th>
                    <th className="py-2.5 px-3">变动后余额</th>
                    <th className="py-2.5 px-3">关联对象</th>
                    <th className="py-2.5 px-3">说明备注</th>
                    <th className="py-2.5 px-3">时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line font-mono">
                  {filteredPointsLedger.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-dim font-sans">
                        暂无符合条件的积分变动流水
                      </td>
                    </tr>
                  ) : (
                    filteredPointsLedger.map((row) => {
                      const delta = Number(row.change_points || 0);
                      return (
                        <tr key={row.id}>
                          <td className="py-3 px-3 text-dim">#{row.id}</td>
                          <td className="py-3 px-3 font-bold text-main">{row.username}</td>
                          <td className="py-3 px-3 font-sans">
                            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold surface-subtle text-main">
                              {formatPointsActionLabel(row.action_type)}
                            </span>
                          </td>
                          <td
                            className={`py-3 px-3 font-bold ${
                              delta >= 0
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-rose-600 dark:text-rose-400'
                            }`}
                          >
                            {delta >= 0 ? `+${delta}` : delta}
                          </td>
                          <td className="py-3 px-3 text-main">{row.balance_after}</td>
                          <td className="py-3 px-3 text-sub">{row.related_username || '--'}</td>
                          <td className="py-3 px-3 font-sans text-sub max-w-xs truncate" title={row.note}>
                            {row.note}
                          </td>
                          <td className="py-3 px-3 text-dim">{row.created_at}</td>
                        </tr>
                      );
                    })
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
