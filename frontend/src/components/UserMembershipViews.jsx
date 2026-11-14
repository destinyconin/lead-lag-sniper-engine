import React, { useState, useEffect, useCallback } from 'react';
import {
  BarChart2,
  Check,
  Copy,
  CreditCard,
  Key,
  Lock,
  Plus,
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 backdrop-blur-sm p-4">
      <div className="surface-card w-full max-w-md p-7 relative">
        <button
          onClick={onClose}
          className="absolute top-6 right-6 w-8 h-8 rounded-full surface-subtle flex items-center justify-center text-dim hover:text-main transition"
        >
          <X className="w-4 h-4" strokeWidth={1.75} />
        </button>

        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="icon-box w-11 h-11">
            <Shield className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div>
            <h3 className="text-base font-bold text-main">
              {mode === 'login' ? '登录量化狙击系统' : '注册新用户账号'}
            </h3>
            <p className="text-xs text-dim mt-0.5">
              注册登录可查看实盘系统 · 开通会员可使用实盘系统
            </p>
          </div>
        </div>

        {/* Mode Switcher Pill */}
        <div className="grid grid-cols-2 p-1 rounded-2xl surface-subtle mb-5 text-xs font-semibold">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={`py-2 rounded-xl transition ${
              mode === 'login' ? 'bg-white dark:bg-slate-800 text-main shadow-sm' : 'text-dim hover:text-main'
            }`}
          >
            账号登录
          </button>
          <button
            type="button"
            onClick={() => setMode('register')}
            className={`py-2 rounded-xl transition ${
              mode === 'register' ? 'bg-white dark:bg-slate-800 text-main shadow-sm' : 'text-dim hover:text-main'
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
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="输入登录用户名 (至少3位)"
              className="w-full input-field px-4 py-3 text-xs font-mono"
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
                className="w-full input-field px-4 py-3 text-xs font-mono"
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
              className="w-full input-field px-4 py-3 text-xs font-mono"
            />
          </div>

          <div className="pt-1">
            <button type="submit" disabled={loading} className="btn-primary-pill w-full py-3 text-xs">
              {loading ? '正在验证...' : mode === 'login' ? '立即登录系统' : '完成注册并登录'}
            </button>
          </div>
        </form>

        {/* Quick Demo Account Switcher */}
        <div className="mt-6 pt-5 border-t border-line text-xs">
          <div className="text-dim font-medium mb-2.5">一键切换演示账号（快速测试不同实盘权限）：</div>
          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('reg_user', '123456')}
              className="p-2.5 surface-subtle hover:opacity-85 text-left transition"
            >
              <div className="font-bold text-main text-[11px]">普通注册用户</div>
              <div className="text-[10px] text-dim mt-0.5">可看实盘/需VIP</div>
            </button>
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('vip_user', '123456')}
              className="p-2.5 surface-subtle hover:opacity-85 text-left transition"
            >
              <div className="font-bold text-emerald-600 dark:text-emerald-400 text-[11px]">VIP 会员账号</div>
              <div className="text-[10px] text-dim mt-0.5">可使用全部实盘</div>
            </button>
            <button
              type="button"
              onClick={() => handleQuickDemoLogin('admin', 'admin123456')}
              className="p-2.5 surface-subtle hover:opacity-85 text-left transition"
            >
              <div className="font-bold text-sky-600 dark:text-sky-400 text-[11px]">后台管理员</div>
              <div className="text-[10px] text-dim mt-0.5">实盘+后台管理</div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ==================== 2. MEMBERSHIP PURCHASE & ACCOUNT SECURITY VIEW ====================
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

  // Password Change State (Matches Screenshot 2)
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [pwdSaving, setPwdSaving] = useState(false);

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
        setOldPassword('');
        setNewPassword('');
      }
    } finally {
      setPwdSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* 1. Top Page Header Banner Card (Matches Screenshot 1 & 3) */}
      <div className="surface-card p-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-main">会员中心与账户安全设置</h2>
          <p className="text-xs text-dim mt-1">
            注册用户可查看实盘系统实时行情，开通 VIP 会员后即可绑定 API 并启动毫秒级实盘自动狙击
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          {!currentUser ? (
            <>
              <button onClick={() => onOpenAuthModal('login')} className="btn-soft-pill px-5 py-2.5 text-xs">
                账号登录
              </button>
              <button onClick={() => onOpenAuthModal('register')} className="btn-primary-pill px-5 py-2.5 text-xs">
                免费注册账号
              </button>
            </>
          ) : currentUser.can_use_live ? (
            <button onClick={onSwitchToLive} className="btn-primary-pill px-5 py-2.5 text-xs">
              进入实盘交易通道
            </button>
          ) : (
            <button onClick={() => onOpenAuthModal('login')} className="btn-soft-pill px-5 py-2.5 text-xs">
              切换登录账号
            </button>
          )}
        </div>
      </div>

      {/* 2. Account & Security Overview Card (Matches Screenshot 2 `账户与安全设置` 1:1) */}
      <div className="surface-card p-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5">
              <Shield className="w-5 h-5 text-main" strokeWidth={1.75} />
              <h3 className="text-base font-bold text-main">账户与实盘权限状态</h3>
            </div>
            <p className="text-xs text-dim mt-1">
              管理登录账户、VIP 会员等级、实盘访问权限与安全密钥
            </p>
          </div>
          {currentUser && (
            <button
              onClick={() => onOpenAuthModal('login')}
              className="btn-primary-pill px-5 py-2 text-xs"
            >
              切换其他账号 ({currentUser.username})
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-5">
          <div className="surface-subtle p-5">
            <div className="text-xs text-dim font-medium">当前登录账号 / 邮箱</div>
            <div className="text-base font-bold text-main mt-1.5 truncate font-mono">
              {currentUser ? currentUser.email || currentUser.username : '未登录 (访客模式)'}
            </div>
          </div>

          <div className="surface-subtle p-5">
            <div className="text-xs text-dim font-medium">当前会员等级</div>
            <div className="text-base font-bold text-main mt-1.5">
              {currentUser ? currentUser.vip_label : '未开通'}
            </div>
          </div>

          <div className="surface-subtle p-5">
            <div className="text-xs text-dim font-medium">实盘系统访问权限</div>
            <div
              className={`text-base font-bold font-mono mt-1.5 ${
                currentUser?.can_use_live
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

          <div className="surface-subtle p-5">
            <div className="text-xs text-dim font-medium">会员到期时间</div>
            <div className="text-base font-bold text-main mt-1.5">
              {currentUser ? currentUser.vip_expires_str : '未开通'}
            </div>
          </div>
        </div>
      </div>

      {/* 3. 4-Step Permission Rules Card (Matches Screenshot 1 `返佣规则` 1:1) */}
      <div className="surface-card p-7">
        <h3 className="text-base font-bold text-main mb-5">实盘系统分级解锁规则</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
          <div className="flex items-center gap-3.5">
            <div className="icon-box w-12 h-12">
              <UserPlus className="w-5 h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">1. 注册账号</div>
              <div className="text-xs text-dim mt-0.5">免费注册并登录系统</div>
            </div>
          </div>

          <div className="flex items-center gap-3.5">
            <div className="icon-box w-12 h-12">
              <Share2 className="w-5 h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">2. 查看实盘</div>
              <div className="text-xs text-dim mt-0.5">注册用户可查看实盘行情</div>
            </div>
          </div>

          <div className="flex items-center gap-3.5">
            <div className="icon-box w-12 h-12">
              <ShoppingCart className="w-5 h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">3. 购买会员</div>
              <div className="text-xs text-dim mt-0.5">选购套餐或兑换激活码</div>
            </div>
          </div>

          <div className="flex items-center gap-3.5">
            <div className="icon-box w-12 h-12">
              <CreditCard className="w-5 h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-bold text-main">4. 开启实盘</div>
              <div className="text-xs text-dim mt-0.5">解锁 API 绑定与自动狙击</div>
            </div>
          </div>
        </div>
      </div>

      {/* 4. Membership Plans Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {plans.map((plan) => {
          const isSelected = selectedPlan?.id === plan.id;
          return (
            <div
              key={plan.id}
              onClick={() => setSelectedPlan(plan)}
              className={`surface-card p-6 cursor-pointer transition flex flex-col justify-between relative ${
                isSelected ? 'ring-2 ring-slate-900 dark:ring-sky-400' : 'hover:opacity-95'
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
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedPlan(plan);
                }}
                className={`mt-6 w-full py-2.5 text-xs ${
                  isSelected ? 'btn-primary-pill' : 'btn-soft-pill'
                }`}
              >
                {isSelected ? '已选择该套餐' : '选择该套餐'}
              </button>
            </div>
          );
        })}
      </div>

      {/* 5. Checkout & Activation Code Redemption Row (Matches Screenshot 1 Bottom Action Card) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left: Online Order Checkout */}
        <div className="lg:col-span-7 surface-card p-7 flex flex-col justify-between space-y-5">
          <div>
            <h3 className="text-base font-bold text-main">在线开通 VIP 会员套餐</h3>
            <p className="text-xs text-dim mt-1">
              选择支付方式并完成结算后，系统将立即为您叠加 VIP 会员天数并解锁实盘交易
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-5 text-xs">
              <div>
                <label className="block text-main font-semibold mb-1.5">支付方式</label>
                <select
                  value={payMethod}
                  onChange={(e) => setPayMethod(e.target.value)}
                  className="w-full input-field px-4 py-3 text-xs"
                >
                  <option value="INSTANT_PAY">极速在线支付 (秒级自动开通)</option>
                  <option value="USDT_TRC20">USDT-TRC20 链上转账</option>
                  <option value="USDT_ERC20">USDT-ERC20 链上转账</option>
                  <option value="ALIPAY_WECHAT">扫码支付 (支付宝 / 微信)</option>
                </select>
              </div>

              <div>
                <label className="block text-main font-semibold mb-1.5">结算审核模式</label>
                <select
                  value={autoConfirm ? 'AUTO' : 'MANUAL'}
                  onChange={(e) => setAutoConfirm(e.target.value === 'AUTO')}
                  className="w-full input-field px-4 py-3 text-xs"
                >
                  <option value="AUTO">支付完成立即自动开通会员</option>
                  <option value="MANUAL">提交订单至后台待管理员审核</option>
                </select>
              </div>
            </div>

            {payMethod !== 'INSTANT_PAY' && (
              <div className="mt-4 text-xs">
                <label className="block text-main font-semibold mb-1.5">交易哈希 (TXID) 或转账流水备注</label>
                <input
                  type="text"
                  value={txHash}
                  onChange={(e) => setTxHash(e.target.value)}
                  placeholder="输入链上交易哈希或支付备注"
                  className="w-full input-field px-4 py-3 text-xs font-mono"
                />
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-line flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-8">
              <div>
                <div className="text-xs text-dim">当前选中套餐</div>
                <div className="text-base font-bold text-main mt-0.5">
                  {selectedPlan ? selectedPlan.name : '--'}
                </div>
              </div>
              <div>
                <div className="text-xs text-dim">应付金额</div>
                <div className="text-2xl font-bold font-mono text-main mt-0.5">
                  ${selectedPlan ? Number(selectedPlan.price_usdt).toFixed(2) : '0.00'}
                </div>
              </div>
            </div>

            <button
              type="button"
              disabled={submitting || !selectedPlan}
              onClick={handlePurchase}
              className="btn-primary-pill px-6 py-3 text-xs"
            >
              <CreditCard className="w-4 h-4" strokeWidth={1.75} />
              <span>
                {submitting
                  ? '正在处理...'
                  : autoConfirm
                  ? '立即支付并开通会员'
                  : '提交订单待后台审核'}
              </span>
            </button>
          </div>
        </div>

        {/* Right: Activation Code Redemption */}
        <div className="lg:col-span-5 surface-card p-7 flex flex-col justify-between space-y-4">
          <div>
            <h3 className="text-base font-bold text-main">卡密 / 激活码兑换会员</h3>
            <p className="text-xs text-dim mt-1">
              输入管理员发放或购买的 VIP 激活码，一键激活实盘交易权限
            </p>

            <form onSubmit={handleRedeem} className="mt-5 space-y-3">
              <div>
                <label className="block text-xs font-semibold text-main mb-1.5">激活码 (VIP Code)</label>
                <input
                  type="text"
                  value={redeemCode}
                  onChange={(e) => setRedeemCode(e.target.value.toUpperCase())}
                  placeholder="输入激活码，如 VIP-90D-9999"
                  className="w-full input-field px-4 py-3 text-xs font-mono uppercase"
                />
              </div>
              <button type="submit" disabled={submitting} className="btn-primary-pill px-6 py-3 text-xs">
                <Key className="w-3.5 h-3.5" strokeWidth={1.75} />
                <span>立即兑换激活会员</span>
              </button>
            </form>
          </div>

          <div className="surface-subtle p-4 text-xs">
            <div className="text-dim font-medium mb-2">预置测试可用激活码（点击填入）：</div>
            <div className="flex flex-wrap gap-2 font-mono">
              {['VIP-30D-8888', 'VIP-90D-9999', 'VIP-365D-6666'].map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setRedeemCode(c)}
                  className="px-3 py-1.5 rounded-full bg-white dark:bg-slate-800 border border-line text-main font-semibold hover:opacity-85 transition"
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* 6. Change Password Card (Matches Screenshot 2 `修改登录密码` 1:1) */}
      <form onSubmit={handleChangePassword} className="surface-card p-7 space-y-5">
        <div className="flex items-center gap-2.5">
          <Lock className="w-5 h-5 text-main" strokeWidth={1.75} />
          <h3 className="text-base font-bold text-main">修改登录密码</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-xs">
          <div>
            <label className="block text-main font-semibold mb-2">旧密码</label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              placeholder="输入当前登录密码"
              className="w-full input-field px-4 py-3 text-xs"
            />
          </div>
          <div>
            <label className="block text-main font-semibold mb-2">新密码 (至少6位)</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="输入新密码"
              className="w-full input-field px-4 py-3 text-xs"
            />
          </div>
        </div>

        <div>
          <button type="submit" disabled={pwdSaving} className="btn-primary-pill px-6 py-2.5 text-xs">
            {pwdSaving ? '保存中...' : '保存新密码'}
          </button>
        </div>
      </form>

      {/* 7. User Order History */}
      <div className="surface-card p-7">
        <h3 className="text-base font-bold text-main mb-4">我的会员订单与兑换记录</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] text-dim">
                <th className="py-3 px-3">订单编号</th>
                <th className="py-3 px-3">会员套餐名称</th>
                <th className="py-3 px-3">授权天数</th>
                <th className="py-3 px-3">金额 (USDT)</th>
                <th className="py-3 px-3">开通方式</th>
                <th className="py-3 px-3">流水号 / 激活码</th>
                <th className="py-3 px-3">状态</th>
                <th className="py-3 px-3">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y border-line font-mono">
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-10 text-center text-dim font-sans">
                    暂无会员订单记录
                  </td>
                </tr>
              ) : (
                orders.map((o) => (
                  <tr key={o.id}>
                    <td className="py-3 px-3 text-main font-semibold">{o.order_no}</td>
                    <td className="py-3 px-3 font-sans text-main font-medium">{o.plan_name}</td>
                    <td className="py-3 px-3 text-sub">+{o.duration_days} 天</td>
                    <td className="py-3 px-3 text-main font-bold">${Number(o.amount_usdt).toFixed(2)}</td>
                    <td className="py-3 px-3 font-sans text-sub">{o.pay_method}</td>
                    <td className="py-3 px-3 text-dim">{o.tx_hash}</td>
                    <td className="py-3 px-3 font-sans">
                      <span
                        className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${
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
                    <td className="py-3 px-3 text-dim">{o.created_at}</td>
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
  const [editingPlan, setEditingPlan] = useState(null);
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
          后台管理系统（含后台用户管理系统与后台会员管理系统）仅对管理员账号开放。请点击下方按钮切换至管理员账号（默认账号：admin / 密码：admin123456）。
        </p>
        <button onClick={() => onOpenAuthModal('login')} className="btn-primary-pill px-6 py-3 text-xs">
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
    <div className="space-y-5">
      {/* 1. Top Header Banner Card (Matches Screenshot 1 & 3) */}
      <div className="surface-card p-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-main">后台用户与会员管理系统</h2>
          <p className="text-xs text-dim mt-1">
            管理注册用户账号、手动开通/收回 VIP 会员资格、审核充值订单与批量生成会员激活码
          </p>
        </div>
        <button onClick={loadAdminDashboard} className="btn-primary-pill px-5 py-2.5 text-xs">
          <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.75} />
          <span>刷新后台数据</span>
        </button>
      </div>

      {/* 2. 4 Stat Cards with Soft Rounded-2xl Icon Boxes (Matches Screenshot 1 Stat Row 1:1) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="surface-card p-6 flex items-center gap-4">
          <div className="icon-box w-12 h-12">
            <Users className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-main">{st.total_users}</div>
            <div className="text-xs text-dim mt-0.5">已注册用户总数</div>
          </div>
        </div>

        <div className="surface-card p-6 flex items-center gap-4">
          <div className="icon-box w-12 h-12">
            <UserCheck className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-main">{st.active_vip_users}</div>
            <div className="text-xs text-dim mt-0.5">有效 VIP 会员人数</div>
          </div>
        </div>

        <div className="surface-card p-6 flex items-center gap-4">
          <div className="icon-box w-12 h-12">
            <Wallet className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-main">
              ${Number(st.total_revenue_usdt || 0).toFixed(2)}
            </div>
            <div className="text-xs text-dim mt-0.5">累计获得会员营收</div>
          </div>
        </div>

        <div className="surface-card p-6 flex items-center gap-4">
          <div className="icon-box w-12 h-12">
            <BarChart2 className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-main">
              {st.unused_codes_count} / {st.total_codes_count}
            </div>
            <div className="text-xs text-dim mt-0.5">可用未兑换激活码</div>
          </div>
        </div>
      </div>

      {/* 3. Sub-Navigation Pill Bar */}
      <div className="surface-card p-3 flex flex-wrap items-center gap-2">
        {[
          { id: 'users', label: `后台用户管理 (${(dashboard.users || []).length})`, icon: Users },
          { id: 'orders', label: `会员订单与审核 (${(dashboard.orders || []).length})`, icon: CreditCard },
          { id: 'plans', label: `会员套餐配置 (${(dashboard.plans || []).length})`, icon: Sliders },
          { id: 'codes', label: `会员卡密生成 (${(dashboard.vip_codes || []).length})`, icon: Key },
        ].map((t) => {
          const Icon = t.icon;
          const active = subTab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setSubTab(t.id)}
              className={`px-5 py-2.5 text-xs ${
                active ? 'btn-primary-pill' : 'btn-soft-pill border-transparent'
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
        <div className="space-y-5">
          {/* Create User Card */}
          <form onSubmit={handleCreateUser} className="surface-card p-7 text-xs space-y-4">
            <div className="flex items-center gap-2.5">
              <UserPlus className="w-5 h-5 text-main" strokeWidth={1.75} />
              <h3 className="text-base font-bold text-main">新增注册用户 / 直接授予会员</h3>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
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
              <button type="submit" className="btn-primary-pill px-6 py-2.5 text-xs">
                + 新建用户账号
              </button>
            </div>
          </form>

          {/* Grant / Modify VIP Inline Card */}
          {grantTarget && (
            <div className="surface-card p-6 ring-2 ring-slate-900 dark:ring-sky-400 flex flex-wrap items-center justify-between gap-4 text-xs">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-bold text-sm text-main">
                  调整用户【{grantTarget.username}】会员资格：
                </span>
                <select
                  value={grantTier}
                  onChange={(e) => setGrantTier(e.target.value)}
                  className="input-field px-3.5 py-2"
                >
                  <option value="月度专业版会员">月度专业版会员</option>
                  <option value="季度旗舰版会员">季度旗舰版会员</option>
                  <option value="年度机构版会员">年度机构版会员</option>
                  <option value="终身至尊版会员">终身至尊版会员</option>
                </select>
                <select
                  value={grantDays}
                  onChange={(e) => setGrantDays(Number(e.target.value))}
                  className="input-field px-3.5 py-2 font-mono"
                >
                  <option value={7}>+7 天 (试用周卡)</option>
                  <option value={30}>+30 天 (月度)</option>
                  <option value={90}>+90 天 (季度)</option>
                  <option value={365}>+365 天 (年度)</option>
                  <option value={36500}>+36500 天 (永久)</option>
                </select>
              </div>
              <div className="flex items-center gap-2.5">
                <button
                  onClick={() => handleGrantVip(grantTarget.id, grantDays, grantTier, false)}
                  className="btn-primary-pill px-5 py-2 text-xs"
                >
                  确认开通 / 延期会员
                </button>
                <button
                  onClick={() => handleGrantVip(grantTarget.id, 0, 'NONE', true)}
                  className="btn-soft-pill px-4 py-2 text-xs text-rose-600 dark:text-rose-400"
                >
                  收回会员资格
                </button>
                <button onClick={() => setGrantTarget(null)} className="btn-soft-pill px-4 py-2 text-xs">
                  取消
                </button>
              </div>
            </div>
          )}

          {/* Users Table Card */}
          <div className="surface-card p-7">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div>
                <h3 className="text-base font-bold text-main">全站注册用户与实盘会员列表</h3>
                <p className="text-xs text-dim mt-0.5">
                  未开通会员的注册用户仅可查看实盘行情；开通会员后自动解锁实盘 API 配置与自动交易
                </p>
              </div>
              <div className="relative w-72">
                <Search className="w-4 h-4 text-dim absolute left-3.5 top-3" strokeWidth={1.75} />
                <input
                  type="text"
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  placeholder="搜索用户名 / 邮箱 / 会员等级..."
                  className="w-full input-field rounded-full pl-10 pr-4 py-2.5 text-xs"
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-dim">
                    <th className="py-3 px-3">ID</th>
                    <th className="py-3 px-3">用户名 / 邮箱</th>
                    <th className="py-3 px-3">角色</th>
                    <th className="py-3 px-3">会员等级</th>
                    <th className="py-3 px-3">到期时间</th>
                    <th className="py-3 px-3">实盘系统权限</th>
                    <th className="py-3 px-3">状态</th>
                    <th className="py-3 px-3 text-right">管理操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-line">
                  {filteredUsers.map((u) => (
                    <tr key={u.id}>
                      <td className="py-3.5 px-3 font-mono text-dim">#{u.id}</td>
                      <td className="py-3.5 px-3">
                        <div className="font-bold text-main font-mono">{u.username}</div>
                        <div className="text-[11px] text-dim">{u.email || '未设置邮箱'}</div>
                      </td>
                      <td className="py-3.5 px-3">
                        <span className="px-2.5 py-1 rounded-full text-[11px] font-semibold surface-subtle text-main">
                          {u.role === 'admin' ? '管理员' : '注册用户'}
                        </span>
                      </td>
                      <td className="py-3.5 px-3 font-semibold text-main">{u.vip_label}</td>
                      <td className="py-3.5 px-3 font-mono text-sub">{u.vip_expires_str}</td>
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
                          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${
                            u.is_active
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
                            setGrantTier(u.vip_tier !== 'NONE' ? u.vip_tier : '季度旗舰版会员');
                          }}
                          className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
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
                              className="btn-soft-pill px-3 py-1.5 text-[11px]"
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
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 2: MEMBERSHIP ORDERS & AUDIT ==================== */}
      {subTab === 'orders' && (
        <div className="surface-card p-7">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
            <div>
              <h3 className="text-base font-bold text-main">会员充值订单与审核流水</h3>
              <p className="text-xs text-dim mt-0.5">
                审核待确认订单后，系统将自动为对应注册用户增加 VIP 会员时长并开放实盘交易
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs">
              {[
                { k: 'ALL', l: '全部订单' },
                { k: 'PENDING', l: '待审核' },
                { k: 'PAID', l: '已开通' },
                { k: 'REJECTED', l: '已拒绝' },
              ].map((f) => (
                <button
                  key={f.k}
                  onClick={() => setOrderFilter(f.k)}
                  className={`px-4 py-2 text-xs ${
                    orderFilter === f.k ? 'btn-primary-pill' : 'btn-soft-pill'
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
                <tr className="border-b border-line text-[11px] text-dim">
                  <th className="py-3 px-3">订单编号</th>
                  <th className="py-3 px-3">用户名</th>
                  <th className="py-3 px-3">会员套餐</th>
                  <th className="py-3 px-3">时长</th>
                  <th className="py-3 px-3">金额 (USDT)</th>
                  <th className="py-3 px-3">支付方式</th>
                  <th className="py-3 px-3">交易哈希 / 备注</th>
                  <th className="py-3 px-3">状态</th>
                  <th className="py-3 px-3">时间</th>
                  <th className="py-3 px-3 text-right">操作</th>
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
                      <td className="py-3.5 px-3 font-bold text-main">${Number(o.amount_usdt).toFixed(2)}</td>
                      <td className="py-3.5 px-3 font-sans text-sub">{o.pay_method}</td>
                      <td className="py-3.5 px-3 text-dim">{o.tx_hash}</td>
                      <td className="py-3.5 px-3 font-sans">
                        <span
                          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${
                            o.status === 'PAID'
                              ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                              : o.status === 'PENDING'
                              ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                              : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                          }`}
                        >
                          {o.status === 'PAID' ? '已支付开通' : o.status === 'PENDING' ? '待审核' : '已拒绝'}
                        </span>
                      </td>
                      <td className="py-3.5 px-3 text-dim">{o.created_at}</td>
                      <td className="py-3.5 px-3 text-right font-sans space-x-2">
                        {o.status === 'PENDING' ? (
                          <>
                            <button
                              onClick={() => handleAuditOrder(o.id, 'APPROVE')}
                              className="btn-primary-pill px-3.5 py-1.5 text-[11px]"
                            >
                              确认收款并开通
                            </button>
                            <button
                              onClick={() => handleAuditOrder(o.id, 'REJECT')}
                              className="btn-soft-pill px-3 py-1.5 text-[11px] text-rose-600"
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
        <div className="surface-card p-7 space-y-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-bold text-main">会员套餐定价与特权管理</h3>
              <p className="text-xs text-dim mt-0.5">
                修改套餐价格、有效天数或特权描述后，前台会员购买页将实时同步更新
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
              className="btn-primary-pill px-5 py-2.5 text-xs"
            >
              <Plus className="w-3.5 h-3.5" strokeWidth={2} />
              <span>新增会员套餐</span>
            </button>
          </div>

          {editingPlan && (
            <form onSubmit={handleSavePlan} className="surface-subtle p-6 space-y-4 text-xs">
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
                  className="w-full input-field p-3.5 text-xs"
                />
              </div>
              <div className="flex items-center justify-end gap-2.5">
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

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {(dashboard.plans || []).map((p) => (
              <div key={p.id} className="surface-subtle p-5 flex flex-col justify-between">
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
        <div className="space-y-5">
          <form onSubmit={handleGenerateCodes} className="surface-card p-7 text-xs space-y-4">
            <div className="flex items-center gap-2.5">
              <Key className="w-5 h-5 text-main" strokeWidth={1.75} />
              <h3 className="text-base font-bold text-main">批量制卡 / 生成会员激活码</h3>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
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

          <div className="surface-card p-7">
            <h3 className="text-base font-bold text-main mb-4">激活码库存与兑换记录</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
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
                  {(dashboard.vip_codes || []).map((c) => (
                    <tr key={c.id}>
                      <td className="py-3.5 px-3 text-dim">#{c.id}</td>
                      <td className="py-3.5 px-3 font-bold text-main">{c.code}</td>
                      <td className="py-3.5 px-3 font-sans text-main">{c.plan_name}</td>
                      <td className="py-3.5 px-3 text-sub">+{c.duration_days} 天</td>
                      <td className="py-3.5 px-3 font-sans">
                        <span
                          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${
                            c.status === 'UNUSED'
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
