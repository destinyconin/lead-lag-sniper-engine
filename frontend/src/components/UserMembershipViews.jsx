import React, { useState, useEffect, useCallback } from 'react';
import {
  Check,
  Copy,
  CreditCard,
  Key,
  Lock,
  Plus,
  RefreshCw,
  Search,
  Shield,
  Sliders,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
  X,
} from 'lucide-react';

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
        onAuthSuccess(data.token, data.user, data.message);
        onClose();
      }
    } catch (err) {
      showToast('网络请求异常，请稍后重试', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleQuickDemoLogin = async (demoUser, demoPass) => {
    setLoading(true);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: demoUser, password: demoPass }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        onAuthSuccess(data.token, data.user, data.message);
        onClose();
      } else {
        showToast(data.detail || '演示账号登录失败', 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4">
      <div className="surface-card w-full max-w-md rounded-xl border border-line shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-line flex items-center justify-between surface-subtle">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-sky-500/15 border border-sky-500/30 flex items-center justify-center text-sky-600 dark:text-sky-400">
              <UserCheck className="w-4 h-4" strokeWidth={1.75} />
            </div>
            <div>
              <h3 className="text-sm font-bold text-main">
                {mode === 'login' ? '登录量化狙击系统账号' : '注册新用户账号'}
              </h3>
              <p className="text-[11px] text-dim">
                注册登录即可查看实盘通道 · 开通会员即可启动实盘自动交易
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-dim hover:text-main surface-subtle transition"
          >
            <X className="w-4 h-4" strokeWidth={1.75} />
          </button>
        </div>

        {/* Mode Tabs */}
        <div className="grid grid-cols-2 border-b border-line text-xs font-semibold">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={`py-2.5 text-center transition ${
              mode === 'login'
                ? 'text-sky-600 dark:text-sky-400 border-b-2 border-sky-500 bg-sky-500/5'
                : 'text-dim hover:text-main'
            }`}
          >
            账号登录
          </button>
          <button
            type="button"
            onClick={() => setMode('register')}
            className={`py-2.5 text-center transition ${
              mode === 'register'
                ? 'text-sky-600 dark:text-sky-400 border-b-2 border-sky-500 bg-sky-500/5'
                : 'text-dim hover:text-main'
            }`}
          >
            新用户注册
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 text-xs">
          <div>
            <label className="block text-sub font-medium mb-1">用户名 (3~32位字符)</label>
            <input
              type="text"
              required
              minLength={3}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名，如 trader01"
              className="w-full input-field rounded-lg px-3 py-2 text-xs font-mono"
            />
          </div>

          {mode === 'register' && (
            <div>
              <label className="block text-sub font-medium mb-1">联系邮箱 (选填)</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                className="w-full input-field rounded-lg px-3 py-2 text-xs font-mono"
              />
            </div>
          )}

          <div>
            <label className="block text-sub font-medium mb-1">登录密码 (至少6位)</label>
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
              className="w-full input-field rounded-lg px-3 py-2 text-xs font-mono"
            />
          </div>

          {/* Tiered Permission Notice */}
          <div className="p-3 rounded-lg surface-subtle border border-line space-y-1 text-[11px] text-sub">
            <div className="font-semibold text-main">系统三级权限说明：</div>
            <div>1. 未登录访客：仅可访问影子模拟盘，不展示实盘交易系统</div>
            <div>2. 普通注册用户：登录后可查看实盘系统与实时盘口行情</div>
            <div>3. VIP 会员用户：开通会员后可配置实盘 API 并启动实盘自动交易</div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-semibold transition shadow-sm"
          >
            {loading ? '正在处理...' : mode === 'login' ? '立即登录' : '立即注册并登录'}
          </button>
        </form>

        {/* Quick Demo Account Switcher */}
        <div className="px-6 py-3.5 border-t border-line surface-subtle text-[11px]">
          <div className="text-dim font-medium mb-2">一键切换预置账号（快速验证不同权限层级）：</div>
          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('reg_user', '123456')}
              className="px-2 py-1.5 rounded border border-line hover:border-sky-500/50 text-left surface-card transition"
            >
              <div className="font-semibold text-main">普通注册用户</div>
              <div className="text-[10px] text-dim">可看实盘 / 需开VIP</div>
            </button>
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('vip_user', '123456')}
              className="px-2 py-1.5 rounded border border-emerald-500/40 bg-emerald-500/5 hover:bg-emerald-500/10 text-left transition"
            >
              <div className="font-semibold text-emerald-600 dark:text-emerald-400">VIP 会员账号</div>
              <div className="text-[10px] text-dim">已解锁全部实盘</div>
            </button>
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('admin', 'admin123456')}
              className="px-2 py-1.5 rounded border border-amber-500/40 bg-amber-500/5 hover:bg-amber-500/10 text-left transition"
            >
              <div className="font-semibold text-amber-600 dark:text-amber-400">后台管理员</div>
              <div className="text-[10px] text-dim">实盘 + 后台管理</div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ==================== 2. MEMBERSHIP PURCHASE & MEMBER CENTER VIEW ====================
export function MembershipCenterView({
  currentUser,
  authToken,
  onUserUpdated,
  onOpenAuthModal,
  onSwitchToLive,
  showToast,
}) {
  const [plans, setPlans] = useState([]);
  const [orders, setOrders] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [payMethod, setPayMethod] = useState('INSTANT_PAY');
  const [txHash, setTxHash] = useState('');
  const [autoConfirm, setAutoConfirm] = useState(true);
  const [redeemCode, setRedeemCode] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchMembershipData = useCallback(async () => {
    try {
      const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
      const res = await fetch('/api/membership/plans', { headers });
      if (res.ok) {
        const data = await res.json();
        setPlans(data.plans || []);
        setOrders(data.orders || []);
        if (data.plans?.length > 0 && !selectedPlan) {
          setSelectedPlan(data.plans[1] || data.plans[0]);
        }
      }
    } catch (e) {
      // ignore
    }
  }, [authToken, selectedPlan]);

  useEffect(() => {
    fetchMembershipData();
  }, [fetchMembershipData]);

  const handlePurchase = async () => {
    if (!currentUser) {
      showToast('请先注册或登录账号后再开通会员', 'error');
      onOpenAuthModal('register');
      return;
    }
    if (!selectedPlan) return;
    setSubmitting(true);
    try {
      const res = await fetch('/api/membership/purchase', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          plan_id: selectedPlan.id,
          pay_method: payMethod,
          tx_hash: txHash,
          auto_confirm: autoConfirm,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || data.message || '购买失败', 'error');
      } else {
        showToast(data.message, 'success');
        if (data.user) onUserUpdated(data.user);
        if (data.orders) setOrders(data.orders);
        setTxHash('');
      }
    } finally {
      setSubmitting(false);
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

  return (
    <div className="space-y-4">
      {/* Top User Membership Status Banner */}
      <div className="surface-card rounded-xl p-5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-start gap-4">
          <div
            className={`w-12 h-12 rounded-xl border flex items-center justify-center shrink-0 ${
              currentUser?.is_vip
                ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-600 dark:text-emerald-400'
                : currentUser
                ? 'bg-amber-500/12 border-amber-500/35 text-amber-600 dark:text-amber-400'
                : 'surface-subtle border-line text-dim'
            }`}
          >
            <Shield className="w-6 h-6" strokeWidth={1.75} />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 className="text-base font-bold text-main">
                {currentUser ? `当前账号：${currentUser.username}` : '尚未登录账号（当前为访客模式）'}
              </h2>
              {currentUser ? (
                <span
                  className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border ${
                    currentUser.is_vip
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/35'
                      : 'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/35'
                  }`}
                >
                  {currentUser.vip_label}
                </span>
              ) : (
                <span className="px-2.5 py-0.5 rounded-full text-xs font-medium surface-subtle text-dim border border-line">
                  未注册登录 · 实盘系统已隐藏
                </span>
              )}
              {currentUser?.is_admin && (
                <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/30">
                  系统管理员
                </span>
              )}
            </div>

            <p className="text-xs text-sub mt-1">
              {!currentUser
                ? '规则说明：注册并登录账号后即可看见「实盘通道」；开通 VIP 会员后即可配置实盘 API 并启动实盘自动狙击。'
                : currentUser.is_vip
                ? `会员有效期至：${currentUser.vip_expires_str}（剩余 ${currentUser.vip_remaining_days} 天） · 您已拥有实盘交易系统的完整查看与执行权限`
                : '您已注册并登录，当前可查看「实盘通道」实时盘口数据；请选购下方会员套餐或使用激活码开通会员，以解锁实盘交易与 API 绑定权限。'}
            </p>

            {/* Permission Checklist */}
            <div className="flex flex-wrap items-center gap-4 mt-3 text-xs">
              <span className="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium">
                <Check className="w-3.5 h-3.5" strokeWidth={2} />
                1. 影子模拟盘演练（已解锁）
              </span>
              <span
                className={`inline-flex items-center gap-1.5 font-medium ${
                  currentUser ? 'text-emerald-600 dark:text-emerald-400' : 'text-dim'
                }`}
              >
                {currentUser ? <Check className="w-3.5 h-3.5" strokeWidth={2} /> : <Lock className="w-3.5 h-3.5" />}
                2. 查看实盘通道与真实行情（{currentUser ? '已注册解锁' : '需注册登录'}）
              </span>
              <span
                className={`inline-flex items-center gap-1.5 font-medium ${
                  currentUser?.can_use_live ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'
                }`}
              >
                {currentUser?.can_use_live ? (
                  <Check className="w-3.5 h-3.5" strokeWidth={2} />
                ) : (
                  <Lock className="w-3.5 h-3.5" />
                )}
                3. 启动实盘交易与绑定交易所 API（{currentUser?.can_use_live ? 'VIP 会员已解锁' : '需开通 VIP 会员'}）
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!currentUser ? (
            <>
              <button
                onClick={() => onOpenAuthModal('login')}
                className="px-4 py-2 rounded-lg border border-line hover:border-sky-500/50 text-xs font-semibold text-main transition"
              >
                立即登录
              </button>
              <button
                onClick={() => onOpenAuthModal('register')}
                className="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
              >
                免费注册账号
              </button>
            </>
          ) : currentUser.can_use_live ? (
            <button
              onClick={onSwitchToLive}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition shadow-sm"
            >
              进入实盘交易通道
            </button>
          ) : null}
        </div>
      </div>

      {/* Membership Plans Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {plans.map((plan) => {
          const isSelected = selectedPlan?.id === plan.id;
          return (
            <div
              key={plan.id}
              onClick={() => setSelectedPlan(plan)}
              className={`surface-card rounded-xl p-5 cursor-pointer transition flex flex-col justify-between relative ${
                isSelected
                  ? 'ring-2 ring-sky-500 border-sky-500/60'
                  : 'hover:border-sky-500/40'
              }`}
            >
              {plan.badge_text && (
                <span className="absolute top-3.5 right-3.5 px-2 py-0.5 rounded text-[10px] font-semibold bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/30">
                  {plan.badge_text}
                </span>
              )}

              <div>
                <div className="text-sm font-bold text-main">{plan.name}</div>
                <div className="text-[11px] text-dim mt-0.5">
                  授权时长：{plan.duration_days >= 3650 ? '永久有效 (终身)' : `${plan.duration_days} 天`}
                </div>

                <div className="mt-4 flex items-baseline gap-2">
                  <span className="text-2xl font-bold font-mono text-sky-600 dark:text-sky-400">
                    {plan.price_usdt}
                  </span>
                  <span className="text-xs font-semibold text-main">USDT</span>
                  {plan.original_price_usdt > plan.price_usdt && (
                    <span className="text-xs text-dim line-through font-mono">
                      {plan.original_price_usdt} USDT
                    </span>
                  )}
                </div>

                <div className="mt-4 pt-3 border-t border-line space-y-2 text-xs text-sub">
                  {(plan.features || []).map((feat, idx) => (
                    <div key={idx} className="flex items-start gap-2">
                      <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" strokeWidth={2} />
                      <span>{feat}</span>
                    </div>
                  ))}
                </div>
              </div>

              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedPlan(plan);
                }}
                className={`mt-5 w-full py-2 rounded-lg text-xs font-semibold transition ${
                  isSelected
                    ? 'bg-sky-600 text-white shadow-sm'
                    : 'surface-subtle text-main hover:bg-sky-500/15 hover:text-sky-600 dark:hover:text-sky-400 border border-line'
                }`}
              >
                {isSelected ? '当前已选中该套餐' : '选择该套餐'}
              </button>
            </div>
          );
        })}
      </div>

      {/* Checkout & Activation Code Redemption Row */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left: Online Order Checkout */}
        <div className="lg:col-span-7 surface-card rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-line pb-3">
            <div className="flex items-center gap-2">
              <CreditCard className="w-4 h-4 text-sky-500" strokeWidth={1.75} />
              <h3 className="text-sm font-bold text-main">在线购买与开通 VIP 会员</h3>
            </div>
            {selectedPlan && (
              <span className="text-xs font-mono font-semibold text-sky-600 dark:text-sky-400">
                已选：{selectedPlan.name} ({selectedPlan.price_usdt} USDT)
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div>
              <label className="block text-dim mb-1">选择支付方式</label>
              <select
                value={payMethod}
                onChange={(e) => setPayMethod(e.target.value)}
                className="w-full input-field rounded-lg px-3 py-2 text-xs"
              >
                <option value="INSTANT_PAY">在线极速直通支付 (推荐 · 秒级自动开通)</option>
                <option value="USDT_TRC20">USDT-TRC20 链上充币支付</option>
                <option value="USDT_ERC20">USDT-ERC20 链上充币支付</option>
                <option value="ALIPAY_WECHAT">扫码支付 (支付宝 / 微信)</option>
              </select>
            </div>

            <div>
              <label className="block text-dim mb-1">开通结算模式</label>
              <select
                value={autoConfirm ? 'AUTO' : 'MANUAL'}
                onChange={(e) => setAutoConfirm(e.target.value === 'AUTO')}
                className="w-full input-field rounded-lg px-3 py-2 text-xs"
              >
                <option value="AUTO">支付成功立即自动开通会员</option>
                <option value="MANUAL">提交订单至后台待管理员人工审核</option>
              </select>
            </div>
          </div>

          {payMethod !== 'INSTANT_PAY' && (
            <div className="text-xs">
              <label className="block text-dim mb-1">链上交易哈希 (TXID) 或支付流水备注 (选填)</label>
              <input
                type="text"
                value={txHash}
                onChange={(e) => setTxHash(e.target.value)}
                placeholder="例如：0x7f9a... 或转账尾号备注"
                className="w-full input-field rounded-lg px-3 py-2 text-xs font-mono"
              />
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-line">
            <div className="text-xs text-sub">
              应付总额：
              <span className="text-lg font-bold font-mono text-emerald-600 dark:text-emerald-400 ml-1">
                {selectedPlan ? `${selectedPlan.price_usdt} USDT` : '--'}
              </span>
              <span className="text-dim ml-2">
                （开通后立即解锁实盘 API 配置与毫秒级自动狙击）
              </span>
            </div>

            <button
              type="button"
              disabled={submitting || !selectedPlan}
              onClick={handlePurchase}
              className="px-5 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition shadow-sm"
            >
              {submitting
                ? '正在处理订单...'
                : autoConfirm
                ? `确认支付并立即开通 ${selectedPlan?.name || '会员'}`
                : '提交充值订单待后台审核'}
            </button>
          </div>
        </div>

        {/* Right: Activation Code Redemption */}
        <div className="lg:col-span-5 surface-card rounded-xl p-5 flex flex-col justify-between space-y-4">
          <div>
            <div className="flex items-center gap-2 border-b border-line pb-3">
              <Key className="w-4 h-4 text-amber-500" strokeWidth={1.75} />
              <h3 className="text-sm font-bold text-main">会员卡密 / 激活码兑换</h3>
            </div>

            <p className="text-xs text-sub mt-3">
              如果您持有管理员发放或渠道购买的 VIP 激活码，可在下方直接输入兑换，立即叠加会员有效期：
            </p>

            <form onSubmit={handleRedeem} className="mt-3 space-y-3">
              <input
                type="text"
                value={redeemCode}
                onChange={(e) => setRedeemCode(e.target.value.toUpperCase())}
                placeholder="请输入激活码，例如 VIP-90D-9999"
                className="w-full input-field rounded-lg px-3 py-2 text-xs font-mono uppercase"
              />
              <button
                type="submit"
                disabled={submitting}
                className="w-full py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold transition shadow-sm"
              >
                立即兑换激活会员
              </button>
            </form>
          </div>

          <div className="p-3 rounded-lg surface-subtle border border-line text-[11px]">
            <div className="text-dim font-medium mb-1.5">系统预置测试可用激活码（点击可自动填入）：</div>
            <div className="flex flex-wrap gap-1.5 font-mono">
              {['VIP-30D-8888', 'VIP-90D-9999', 'VIP-365D-6666'].map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setRedeemCode(c)}
                  className="px-2 py-1 rounded border border-line hover:border-amber-500/50 surface-card text-main transition"
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* User Order History */}
      <div className="surface-card rounded-xl p-5">
        <h3 className="text-sm font-bold text-main mb-3">我的会员购买与兑换记录</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                <th className="py-2 px-3">订单编号</th>
                <th className="py-2 px-3">会员套餐名称</th>
                <th className="py-2 px-3">授权天数</th>
                <th className="py-2 px-3">金额 (USDT)</th>
                <th className="py-2 px-3">开通方式</th>
                <th className="py-2 px-3">流水号 / 激活码</th>
                <th className="py-2 px-3">状态</th>
                <th className="py-2 px-3">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y border-line font-mono">
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-dim font-sans">
                    暂无会员订单记录
                  </td>
                </tr>
              ) : (
                orders.map((o) => (
                  <tr key={o.id}>
                    <td className="py-2.5 px-3 text-main font-semibold">{o.order_no}</td>
                    <td className="py-2.5 px-3 font-sans text-main">{o.plan_name}</td>
                    <td className="py-2.5 px-3 text-sub">+{o.duration_days} 天</td>
                    <td className="py-2.5 px-3 text-main">{Number(o.amount_usdt).toFixed(2)}</td>
                    <td className="py-2.5 px-3 font-sans text-sub">{o.pay_method}</td>
                    <td className="py-2.5 px-3 text-dim">{o.tx_hash}</td>
                    <td className="py-2.5 px-3 font-sans">
                      <span
                        className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                          o.status === 'PAID'
                            ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                            : o.status === 'PENDING'
                            ? 'bg-amber-500/12 text-amber-600 dark:text-amber-400'
                            : 'bg-rose-500/12 text-rose-600 dark:text-rose-400'
                        }`}
                      >
                        {o.status === 'PAID' ? '已开通生效' : o.status === 'PENDING' ? '待后台审核' : '已拒绝'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-dim">{o.created_at}</td>
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

  // Create user form state
  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    email: '',
    role: 'user',
    vip_days: 0,
    vip_tier: '季度旗舰版会员',
  });

  // Grant VIP modal/inline state
  const [grantTarget, setGrantTarget] = useState(null);
  const [grantDays, setGrantDays] = useState(30);
  const [grantTier, setGrantTier] = useState('月度专业版会员');

  // Plan editor state
  const [editingPlan, setEditingPlan] = useState(null);

  // Batch generate VIP codes state
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
      <div className="surface-card rounded-xl p-10 text-center space-y-4">
        <div className="w-12 h-12 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-500 flex items-center justify-center mx-auto">
          <Lock className="w-6 h-6" strokeWidth={1.75} />
        </div>
        <h3 className="text-base font-bold text-main">需要后台管理员权限</h3>
        <p className="text-xs text-sub max-w-md mx-auto">
          后台管理系统（含后台用户管理系统与后台会员管理系统）仅对管理员账号开放。请使用管理员账号（默认账号：admin / 密码：admin123456）登录。
        </p>
        <button
          onClick={() => onOpenAuthModal('login')}
          className="px-5 py-2.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
        >
          切换 / 登录管理员账号
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
      onCurrentUserRefresh();
    } else {
      showToast(data.detail || '更新失败', 'error');
    }
  };

  const handleGrantVip = async (userId, addDays, vipTier, revoke = false) => {
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
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
      setGrantTarget(null);
      onCurrentUserRefresh();
    } else {
      showToast(data.detail || '操作失败', 'error');
    }
  };

  const handleDeleteUser = async (userId) => {
    const res = await fetch(`/api/admin/users/${userId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'info');
      setDashboard(data.dashboard);
    } else {
      showToast(data.detail || '删除失败', 'error');
    }
  };

  const handleAuditOrder = async (orderId, action) => {
    const res = await fetch('/api/admin/orders/audit', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify({ order_id: orderId, action }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      setDashboard(data.dashboard);
      onCurrentUserRefresh();
    } else {
      showToast(data.detail || '审核操作失败', 'error');
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
      (u.vip_label || '').toLowerCase().includes(q)
    );
  });

  const filteredOrders = (dashboard.orders || []).filter((o) => {
    if (orderFilter === 'ALL') return true;
    return o.status === orderFilter;
  });

  return (
    <div className="space-y-4">
      {/* Top KPI Summary Row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">注册总用户数</div>
          <div className="text-xl font-bold font-mono text-main mt-1">{st.total_users}</div>
          <div className="text-[11px] text-sub mt-0.5">全部可查看实盘行情</div>
        </div>
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">有效 VIP 会员数</div>
          <div className="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400 mt-1">
            {st.active_vip_users}
          </div>
          <div className="text-[11px] text-sub mt-0.5">已解锁实盘自动交易</div>
        </div>
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">未开通会员用户</div>
          <div className="text-xl font-bold font-mono text-amber-600 dark:text-amber-400 mt-1">
            {st.non_vip_users}
          </div>
          <div className="text-[11px] text-sub mt-0.5">仅可查看不可实盘下单</div>
        </div>
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">会员累计营收 (USDT)</div>
          <div className="text-xl font-bold font-mono text-sky-600 dark:text-sky-400 mt-1">
            +{Number(st.total_revenue_usdt || 0).toFixed(2)}
          </div>
          <div className="text-[11px] text-sub mt-0.5">已支付 {st.paid_orders_count} 笔订单</div>
        </div>
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">待审核会员订单</div>
          <div className="text-xl font-bold font-mono text-amber-500 mt-1">{st.pending_orders_count}</div>
          <div className="text-[11px] text-sub mt-0.5">审核通过自动发卡</div>
        </div>
        <div className="surface-card rounded-xl p-3.5">
          <div className="text-[11px] text-dim">可用未兑换激活码</div>
          <div className="text-xl font-bold font-mono text-main mt-1">
            {st.unused_codes_count} / {st.total_codes_count}
          </div>
          <div className="text-[11px] text-sub mt-0.5">支持一键批量制卡</div>
        </div>
      </div>

      {/* Sub-Navigation Bar */}
      <div className="surface-card rounded-xl p-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {[
            { id: 'users', label: `后台用户管理系统 (${(dashboard.users || []).length})`, icon: Users },
            { id: 'orders', label: `后台会员订单与营收管理 (${(dashboard.orders || []).length})`, icon: CreditCard },
            { id: 'plans', label: `后台会员套餐配置管理 (${(dashboard.plans || []).length})`, icon: Sliders },
            { id: 'codes', label: `后台会员卡密生成管理 (${(dashboard.vip_codes || []).length})`, icon: Key },
          ].map((t) => {
            const Icon = t.icon;
            const active = subTab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setSubTab(t.id)}
                className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold transition ${
                  active
                    ? 'bg-sky-600 text-white shadow-sm'
                    : 'text-sub hover:text-main surface-subtle'
                }`}
              >
                <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                {t.label}
              </button>
            );
          })}
        </div>

        <button
          onClick={loadAdminDashboard}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg surface-subtle hover:opacity-85 text-xs text-main border border-line transition"
        >
          <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.75} />
          刷新后台数据
        </button>
      </div>

      {/* ==================== SUB-TAB 1: USER MANAGEMENT SYSTEM ==================== */}
      {subTab === 'users' && (
        <div className="space-y-4">
          {/* Create User Form */}
          <form onSubmit={handleCreateUser} className="surface-card rounded-xl p-4 text-xs">
            <div className="flex items-center gap-2 font-bold text-main mb-3">
              <UserPlus className="w-4 h-4 text-sky-500" strokeWidth={1.75} />
              <span>新增注册用户 / 直接开通会员账号</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
              <input
                type="text"
                required
                minLength={3}
                placeholder="用户名 (必填)"
                value={newUser.username}
                onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                className="input-field rounded-lg px-3 py-1.5 font-mono"
              />
              <input
                type="text"
                required
                minLength={6}
                placeholder="初始密码 (至少6位)"
                value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                className="input-field rounded-lg px-3 py-1.5 font-mono"
              />
              <input
                type="email"
                placeholder="邮箱备注 (选填)"
                value={newUser.email}
                onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                className="input-field rounded-lg px-3 py-1.5 font-mono"
              />
              <select
                value={newUser.role}
                onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                className="input-field rounded-lg px-3 py-1.5"
              >
                <option value="user">角色：普通注册用户</option>
                <option value="admin">角色：后台管理员</option>
              </select>
              <select
                value={newUser.vip_days}
                onChange={(e) => setNewUser({ ...newUser, vip_days: Number(e.target.value) })}
                className="input-field rounded-lg px-3 py-1.5"
              >
                <option value={0}>初始会员：不开通 (0天)</option>
                <option value={30}>赠送月度会员 (+30天)</option>
                <option value={90}>赠送季度会员 (+90天)</option>
                <option value={365}>赠送年度会员 (+365天)</option>
                <option value={36500}>赠送终身会员 (永久)</option>
              </select>
              <button
                type="submit"
                className="px-4 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-semibold transition"
              >
                + 立即创建用户
              </button>
            </div>
          </form>

          {/* Grant / Modify VIP Modal-Like Inline Bar */}
          {grantTarget && (
            <div className="surface-card rounded-xl p-4 border-2 border-emerald-500/50 flex flex-wrap items-center justify-between gap-3 text-xs">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-bold text-main">
                  正在管理用户【{grantTarget.username}】的会员资格：
                </span>
                <select
                  value={grantTier}
                  onChange={(e) => setGrantTier(e.target.value)}
                  className="input-field rounded-lg px-2.5 py-1.5"
                >
                  <option value="月度专业版会员">月度专业版会员</option>
                  <option value="季度旗舰版会员">季度旗舰版会员</option>
                  <option value="年度机构版会员">年度机构版会员</option>
                  <option value="终身至尊版会员">终身至尊版会员</option>
                </select>
                <select
                  value={grantDays}
                  onChange={(e) => setGrantDays(Number(e.target.value))}
                  className="input-field rounded-lg px-2.5 py-1.5 font-mono"
                >
                  <option value={7}>+7 天 (试用周卡)</option>
                  <option value={30}>+30 天 (月度)</option>
                  <option value={90}>+90 天 (季度)</option>
                  <option value={365}>+365 天 (年度)</option>
                  <option value={36500}>+36500 天 (终身永久)</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleGrantVip(grantTarget.id, grantDays, grantTier, false)}
                  className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition"
                >
                  确认开通 / 延期会员
                </button>
                <button
                  onClick={() => handleGrantVip(grantTarget.id, 0, 'NONE', true)}
                  className="px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold transition"
                >
                  收回会员资格 (降为普通注册用户)
                </button>
                <button
                  onClick={() => setGrantTarget(null)}
                  className="px-3 py-1.5 rounded-lg surface-subtle text-dim hover:text-main"
                >
                  取消
                </button>
              </div>
            </div>
          )}

          {/* Users Table */}
          <div className="surface-card rounded-xl p-5">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-sm font-bold text-main">注册用户与实盘会员权限列表</h3>
                <p className="text-xs text-dim mt-0.5">
                  支持一键为用户开通/延期/收回 VIP 会员、调整管理员角色、启用或封禁账号
                </p>
              </div>
              <div className="relative w-64">
                <Search className="w-3.5 h-3.5 text-dim absolute left-3 top-2.5" strokeWidth={1.75} />
                <input
                  type="text"
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  placeholder="搜索用户名 / 邮箱 / 会员等级..."
                  className="w-full input-field rounded-lg pl-8 pr-3 py-1.5 text-xs"
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                    <th className="py-2.5 px-3">ID</th>
                    <th className="py-2.5 px-3">用户名 / 邮箱</th>
                    <th className="py-2.5 px-3">系统角色</th>
                    <th className="py-2.5 px-3">会员等级与状态</th>
                    <th className="py-2.5 px-3">会员到期时间</th>
                    <th className="py-2.5 px-3">实盘系统权限</th>
                    <th className="py-2.5 px-3">账户状态</th>
                    <th className="py-2.5 px-3">注册时间</th>
                    <th className="py-2.5 px-3 text-right">管理操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line">
                  {filteredUsers.map((u) => (
                    <tr key={u.id} className="hover:opacity-95 transition">
                      <td className="py-3 px-3 font-mono text-dim">#{u.id}</td>
                      <td className="py-3 px-3">
                        <div className="font-bold text-main font-mono">{u.username}</div>
                        <div className="text-[11px] text-dim">{u.email || '未绑定邮箱'}</div>
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            u.role === 'admin'
                              ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/30'
                              : 'surface-subtle text-sub border border-line'
                          }`}
                        >
                          {u.role === 'admin' ? '后台管理员' : '注册用户'}
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            u.is_vip
                              ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                              : 'bg-amber-500/12 text-amber-600 dark:text-amber-400 border border-amber-500/30'
                          }`}
                        >
                          {u.vip_label}
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono text-sub">
                        {u.vip_expires_str}
                        {u.is_vip && u.vip_remaining_days < 3650 && (
                          <span className="text-[11px] text-emerald-600 dark:text-emerald-400 ml-1">
                            ({u.vip_remaining_days}天)
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        {u.can_use_live ? (
                          <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                            可查看 + 可使用实盘
                          </span>
                        ) : u.can_see_live ? (
                          <span className="text-amber-600 dark:text-amber-400 font-medium">
                            仅可查看实盘 (需开VIP)
                          </span>
                        ) : (
                          <span className="text-rose-500">已禁用访问</span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            u.is_active
                              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                              : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                          }`}
                        >
                          {u.is_active ? '正常' : '已封禁'}
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono text-dim">{u.created_at}</td>
                      <td className="py-3 px-3 text-right space-x-1.5 whitespace-nowrap">
                        <button
                          onClick={() => {
                            setGrantTarget(u);
                            setGrantTier(u.vip_tier !== 'NONE' ? u.vip_tier : '季度旗舰版会员');
                          }}
                          className="px-2.5 py-1 rounded bg-emerald-500/12 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 font-semibold transition"
                        >
                          会员授权/延期
                        </button>
                        {u.username !== 'admin' && (
                          <>
                            <button
                              onClick={() =>
                                handleUpdateUser({
                                  user_id: u.id,
                                  role: u.role === 'admin' ? 'user' : 'admin',
                                })
                              }
                              className="px-2 py-1 rounded surface-subtle hover:opacity-85 text-sub border border-line transition"
                            >
                              {u.role === 'admin' ? '设为普通用户' : '设为管理员'}
                            </button>
                            <button
                              onClick={() =>
                                handleUpdateUser({
                                  user_id: u.id,
                                  is_active: !u.is_active,
                                })
                              }
                              className="px-2 py-1 rounded surface-subtle hover:opacity-85 text-amber-600 dark:text-amber-400 border border-line transition"
                            >
                              {u.is_active ? '封禁' : '解封'}
                            </button>
                            <button
                              onClick={() => handleDeleteUser(u.id)}
                              className="px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/25 transition"
                            >
                              删除
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 2: MEMBERSHIP ORDERS & AUDIT ==================== */}
      {subTab === 'orders' && (
        <div className="surface-card rounded-xl p-5">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h3 className="text-sm font-bold text-main">后台会员订单与充值审核管理</h3>
              <p className="text-xs text-dim mt-0.5">
                查看所有会员购买流水、卡密兑换记录及管理员授权记录；对待审核订单可一键确认收款并自动发放会员
              </p>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              {[
                { k: 'ALL', l: '全部订单' },
                { k: 'PENDING', l: '待审核 (PENDING)' },
                { k: 'PAID', l: '已开通 (PAID)' },
                { k: 'REJECTED', l: '已拒绝' },
              ].map((f) => (
                <button
                  key={f.k}
                  onClick={() => setOrderFilter(f.k)}
                  className={`px-3 py-1 rounded-lg font-medium transition ${
                    orderFilter === f.k
                      ? 'bg-sky-600 text-white'
                      : 'surface-subtle text-dim hover:text-main'
                  }`}
                >
                  {f.l}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                  <th className="py-2.5 px-3">订单编号</th>
                  <th className="py-2.5 px-3">购买用户</th>
                  <th className="py-2.5 px-3">会员套餐</th>
                  <th className="py-2.5 px-3">增加天数</th>
                  <th className="py-2.5 px-3">实付金额 (USDT)</th>
                  <th className="py-2.5 px-3">支付渠道</th>
                  <th className="py-2.5 px-3">交易哈希 / 卡密备注</th>
                  <th className="py-2.5 px-3">订单状态</th>
                  <th className="py-2.5 px-3">提交时间</th>
                  <th className="py-2.5 px-3 text-right">审核操作</th>
                </tr>
              </thead>
              <tbody className="divide-y border-line font-mono">
                {filteredOrders.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="py-10 text-center text-dim font-sans">
                      暂无符合条件的会员订单记录
                    </td>
                  </tr>
                ) : (
                  filteredOrders.map((o) => (
                    <tr key={o.id}>
                      <td className="py-2.5 px-3 font-semibold text-main">{o.order_no}</td>
                      <td className="py-2.5 px-3 text-sky-600 dark:text-sky-400 font-bold">{o.username}</td>
                      <td className="py-2.5 px-3 font-sans text-main">{o.plan_name}</td>
                      <td className="py-2.5 px-3 text-sub">+{o.duration_days} 天</td>
                      <td className="py-2.5 px-3 font-bold text-main">{Number(o.amount_usdt).toFixed(2)}</td>
                      <td className="py-2.5 px-3 font-sans text-sub">{o.pay_method}</td>
                      <td className="py-2.5 px-3 text-dim">{o.tx_hash}</td>
                      <td className="py-2.5 px-3 font-sans">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            o.status === 'PAID'
                              ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                              : o.status === 'PENDING'
                              ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                              : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                          }`}
                        >
                          {o.status === 'PAID' ? '已支付开通' : o.status === 'PENDING' ? '待管理员审核' : '已拒绝'}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-dim">{o.created_at}</td>
                      <td className="py-2.5 px-3 text-right font-sans space-x-1.5">
                        {o.status === 'PENDING' ? (
                          <>
                            <button
                              onClick={() => handleAuditOrder(o.id, 'APPROVE')}
                              className="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition"
                            >
                              确认收款并开通
                            </button>
                            <button
                              onClick={() => handleAuditOrder(o.id, 'REJECT')}
                              className="px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/25 text-rose-600 dark:text-rose-400 font-semibold transition"
                            >
                              拒绝
                            </button>
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
        <div className="space-y-4">
          <div className="surface-card rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-bold text-main">会员套餐定价与权益配置管理</h3>
                <p className="text-xs text-dim mt-0.5">
                  自定义修改各档位会员价格、有效天数、角标与特权说明，保存后前台会员购买页立即生效
                </p>
              </div>
              <button
                onClick={() =>
                  setEditingPlan({
                    id: null,
                    plan_code: `PLAN_CUSTOM_${Date.now().toString().slice(-4)}`,
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
                className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
              >
                <Plus className="w-3.5 h-3.5" strokeWidth={2} />
                新增会员套餐
              </button>
            </div>

            {editingPlan && (
              <form
                onSubmit={handleSavePlan}
                className="mb-5 p-4 rounded-xl surface-subtle border border-sky-500/40 space-y-3 text-xs"
              >
                <div className="font-bold text-main">
                  {editingPlan.id ? `编辑套餐：${editingPlan.name}` : '新建会员套餐'}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                  <div>
                    <label className="block text-dim mb-1">套餐代号</label>
                    <input
                      type="text"
                      required
                      value={editingPlan.plan_code}
                      onChange={(e) => setEditingPlan({ ...editingPlan, plan_code: e.target.value })}
                      className="w-full input-field rounded-lg px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-dim mb-1">套餐名称</label>
                    <input
                      type="text"
                      required
                      value={editingPlan.name}
                      onChange={(e) => setEditingPlan({ ...editingPlan, name: e.target.value })}
                      className="w-full input-field rounded-lg px-2.5 py-1.5"
                    />
                  </div>
                  <div>
                    <label className="block text-dim mb-1">授权有效天数</label>
                    <input
                      type="number"
                      required
                      min={1}
                      value={editingPlan.duration_days}
                      onChange={(e) => setEditingPlan({ ...editingPlan, duration_days: e.target.value })}
                      className="w-full input-field rounded-lg px-2.5 py-1.5 font-mono"
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
                      className="w-full input-field rounded-lg px-2.5 py-1.5 font-mono"
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
                      className="w-full input-field rounded-lg px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-dim mb-1">推荐角标文字</label>
                    <input
                      type="text"
                      value={editingPlan.badge_text}
                      onChange={(e) => setEditingPlan({ ...editingPlan, badge_text: e.target.value })}
                      className="w-full input-field rounded-lg px-2.5 py-1.5"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-dim mb-1">套餐包含权益描述（每行一条）</label>
                  <textarea
                    rows={3}
                    value={editingPlan.features_text}
                    onChange={(e) => setEditingPlan({ ...editingPlan, features_text: e.target.value })}
                    className="w-full input-field rounded-lg p-2.5 text-xs"
                  />
                </div>
                <div className="flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setEditingPlan(null)}
                    className="px-3.5 py-1.5 rounded-lg surface-card border border-line text-dim hover:text-main"
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold"
                  >
                    保存套餐配置
                  </button>
                </div>
              </form>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
              {(dashboard.plans || []).map((p) => (
                <div key={p.id} className="p-4 rounded-xl surface-subtle border border-line flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-sm text-main">{p.name}</span>
                      <span className="text-[11px] font-mono text-sky-600 dark:text-sky-400">
                        {p.duration_days} 天
                      </span>
                    </div>
                    <div className="mt-2 flex items-baseline gap-2 font-mono">
                      <span className="text-xl font-bold text-emerald-600 dark:text-emerald-400">
                        {p.price_usdt} USDT
                      </span>
                      <span className="text-xs text-dim line-through">{p.original_price_usdt} USDT</span>
                    </div>
                    <ul className="mt-3 space-y-1 text-xs text-sub">
                      {(p.features || []).map((f, idx) => (
                        <li key={idx}>• {f}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="mt-4 pt-3 border-t border-line flex items-center justify-end gap-2 text-xs">
                    <button
                      onClick={() =>
                        setEditingPlan({
                          ...p,
                          features_text: (p.features || []).join('\n'),
                        })
                      }
                      className="px-3 py-1 rounded surface-card border border-line hover:border-sky-500/50 text-main font-medium"
                    >
                      编辑套餐
                    </button>
                    <button
                      onClick={() => handleDeletePlan(p.id)}
                      className="px-2.5 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400"
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 4: VIP ACTIVATION CODES GENERATOR ==================== */}
      {subTab === 'codes' && (
        <div className="space-y-4">
          <form onSubmit={handleGenerateCodes} className="surface-card rounded-xl p-4 text-xs">
            <div className="flex items-center gap-2 font-bold text-main mb-3">
              <Key className="w-4 h-4 text-amber-500" strokeWidth={1.75} />
              <span>批量生成会员激活码 / 卡密</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
              <div>
                <label className="block text-dim mb-1">绑定会员等级名称</label>
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
                  className="w-full input-field rounded-lg px-3 py-1.5"
                >
                  <option value="月度专业版会员">月度专业版会员 (30天)</option>
                  <option value="季度旗舰版会员">季度旗舰版会员 (90天)</option>
                  <option value="年度机构版会员">年度机构版会员 (365天)</option>
                  <option value="终身至尊版会员">终身至尊版会员 (永久)</option>
                </select>
              </div>
              <div>
                <label className="block text-dim mb-1">有效天数</label>
                <input
                  type="number"
                  min={1}
                  value={codeGenForm.duration_days}
                  onChange={(e) => setCodeGenForm({ ...codeGenForm, duration_days: e.target.value })}
                  className="w-full input-field rounded-lg px-3 py-1.5 font-mono"
                />
              </div>
              <div>
                <label className="block text-dim mb-1">生成张数 (1~50)</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={codeGenForm.count}
                  onChange={(e) => setCodeGenForm({ ...codeGenForm, count: e.target.value })}
                  className="w-full input-field rounded-lg px-3 py-1.5 font-mono"
                />
              </div>
              <div className="flex items-end">
                <button
                  type="submit"
                  className="w-full py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold transition"
                >
                  + 批量生成激活码
                </button>
              </div>
            </div>
          </form>

          <div className="surface-card rounded-xl p-5">
            <h3 className="text-sm font-bold text-main mb-3">会员激活码库存与核销明细表</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                    <th className="py-2.5 px-3">ID</th>
                    <th className="py-2.5 px-3">激活码 (Code)</th>
                    <th className="py-2.5 px-3">对应会员套餐</th>
                    <th className="py-2.5 px-3">授权天数</th>
                    <th className="py-2.5 px-3">状态</th>
                    <th className="py-2.5 px-3">兑换用户</th>
                    <th className="py-2.5 px-3">兑换时间</th>
                    <th className="py-2.5 px-3">创建时间</th>
                    <th className="py-2.5 px-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line font-mono">
                  {(dashboard.vip_codes || []).map((c) => (
                    <tr key={c.id}>
                      <td className="py-2.5 px-3 text-dim">#{c.id}</td>
                      <td className="py-2.5 px-3 font-bold text-sky-600 dark:text-sky-400">{c.code}</td>
                      <td className="py-2.5 px-3 font-sans text-main">{c.plan_name}</td>
                      <td className="py-2.5 px-3 text-sub">+{c.duration_days} 天</td>
                      <td className="py-2.5 px-3 font-sans">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            c.status === 'UNUSED'
                              ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                              : 'surface-subtle text-dim'
                          }`}
                        >
                          {c.status === 'UNUSED' ? '未使用 (可兑换)' : '已核销使用'}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-main">{c.used_by_username || '--'}</td>
                      <td className="py-2.5 px-3 text-dim">{c.used_at || '--'}</td>
                      <td className="py-2.5 px-3 text-dim">{c.created_at}</td>
                      <td className="py-2.5 px-3 text-right font-sans space-x-1.5">
                        <button
                          onClick={() => {
                            navigator.clipboard?.writeText(c.code);
                            showToast(`已复制激活码: ${c.code}`, 'info');
                          }}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded surface-subtle hover:opacity-85 text-main border border-line"
                        >
                          <Copy className="w-3 h-3" strokeWidth={1.75} />
                          复制
                        </button>
                        <button
                          onClick={() => handleDeleteCode(c.id)}
                          className="px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400"
                        >
                          <Trash2 className="w-3 h-3 inline" strokeWidth={1.75} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
