import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  Check,
  Cpu,
  CreditCard,
  DollarSign,
  Lock,
  LogOut,
  Moon,
  Percent,
  Plus,
  Power,
  Radar,
  RefreshCw,
  Search,
  Shield,
  Sliders,
  Sun,
  Terminal,
  Trash2,
  User,
  Users,
  Wallet,
  Wifi,
} from 'lucide-react';
import RadarChart from './components/RadarChart';
import EquityChart from './components/EquityChart';
import { AuthModal, MembershipCenterView, AdminSystemView } from './components/UserMembershipViews';

const STATE_PIPELINE = [
  { key: 'IDLE', step: '1', label: '空闲监控', desc: '监听价差脉冲' },
  { key: 'SIGNALED', step: '2', label: '信号锁定', desc: '同步互斥加锁' },
  { key: 'SUBMITTED', step: '3', label: '保留价报单', desc: '发射限价IOC' },
  { key: 'RECONCILE', step: '4', label: '回报对账', desc: '私有流核验' },
  { key: 'HOLDING', step: '5', label: '持仓监控', desc: '事件驱动巡检' },
  { key: 'EXITING', step: '6', label: '平仓执行', desc: '撤单与重试' },
  { key: 'CLOSED', step: '7', label: '盈亏核算', desc: '异步队列落库' },
  { key: 'COOLDOWN', step: '8', label: '防抖冷却', desc: '抑制尾随重入' },
];

const STATE_LABEL_MAP = {
  IDLE: '空闲监控',
  SIGNALED: '信号锁定',
  SUBMITTED: '保留价报单',
  RECONCILE: '回报对账',
  HOLDING: '持仓监控',
  EXITING: '平仓执行',
  CLOSED: '盈亏核算',
  COOLDOWN: '防抖冷却',
};

const DIRECTION_LABEL_MAP = {
  BOTH: '多空双向',
  LONG_ONLY: '仅限做多',
  SHORT_ONLY: '仅限做空',
};

const EXIT_BADGE_MAP = {
  TAKE_PROFIT: {
    text: '目标止盈',
    cls: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
  TRAILING_TP: {
    text: '追踪止盈',
    cls: 'bg-teal-500/10 text-teal-600 dark:text-teal-400 border-teal-500/30',
  },
  REVERSAL_SL: {
    text: '反转止损',
    cls: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30',
  },
  HARD_SL: {
    text: '硬性止损',
    cls: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30',
  },
  TIMEOUT: {
    text: '超时平仓',
    cls: 'surface-subtle text-sub',
  },
  KILL_SWITCH: {
    text: '熔断强平',
    cls: 'bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40',
  },
};

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('sniper_theme') || 'light');
  const [activeTab, setActiveTab] = useState('radar');
  const [selectedSymbol, setSelectedSymbol] = useState('XRP/USDT:USDT');
  const [wsConnected, setWsConnected] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('sniper_theme', theme);
  }, [theme]);

  const [snapshot, setSnapshot] = useState({
    ts: 0,
    trading_mode: 'SHADOW',
    feed_source: 'HIGH_FREQ_SIM',
    live_status: {
      binance_ws_connected: false,
      okx_ws_connected: false,
      follower_has_api_key: false,
      follower_exchange_name: 'OKX Swap (欧易永续)',
      leader_exchange_name: 'Binance USDM (币安合约)',
      last_ws_error: '',
    },
    last_order_rtt_ms: 24.5,
    risk: {
      kill_switch_active: false,
      trading_mode: 'SHADOW',
      feed_source: 'AUTO',
      max_daily_loss_usdt: 150,
      max_consecutive_losses: 5,
      max_concurrent_positions: 2,
      max_exposure_usdt: 1500,
      latency_circuit_breaker_ms: 250,
      auto_disable_rolling_trades: 8,
      cooldown_ms: 500,
      shadow_base_latency_ms: 26,
      shadow_latency_jitter_ms: 12,
      shadow_adverse_selection_prob: 0.25,
    },
    exchange_health: {},
    telemetry: {},
    active_positions: [],
    radar_series: {},
    logs: [],
    reality_gap: {
      shadow_trades: 0,
      shadow_win_rate_pct: 72.0,
      shadow_fill_rate_pct: 68.0,
      shadow_net_pnl_usdt: 0,
      projected_live_fill_rate_pct: 12.5,
      projected_live_win_rate_pct: 51.0,
      breakeven_win_rate_pct: 64.3,
      colocation_gap_ms: 22.0,
      mainstream_verdict: '极难盈利 (做市商专线 <2ms 垄断，逆向选择严重)',
      altcoin_verdict: '具备边际 (中小市值合约 + 二线执行所滞后达 80~250ms)',
    },
    stats: {
      total_signals: 0,
      ioc_filled_count: 0,
      ioc_missed_count: 0,
      reconciled_count: 0,
      win_count: 0,
      loss_count: 0,
      win_rate_pct: 0,
      fill_rate_pct: 0,
      profit_factor: 0,
      total_gross_pnl: 0,
      total_fees: 0,
      total_net_pnl: 0,
      daily_net_pnl: 0,
      consecutive_losses: 0,
      equity_curve: [],
      recent_trades: [],
    },
  });

  const [strategies, setStrategies] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [exchanges, setExchanges] = useState([]);
  const [riskForm, setRiskForm] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSector, setSelectedSector] = useState('ALL');
  const [toast, setToast] = useState(null);
  const [logFilter, setLogFilter] = useState('ALL');

  // User Auth & Membership State
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('sniper_auth_token') || '');
  const [currentUser, setCurrentUser] = useState(null);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState('login');

  const showToast = useCallback((msg, type = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3400);
  }, []);

  const authHeaders = useMemo(() => {
    return authToken ? { Authorization: `Bearer ${authToken}` } : {};
  }, [authToken]);

  const openAuthModal = useCallback((mode = 'login') => {
    setAuthModalMode(mode);
    setAuthModalOpen(true);
  }, []);

  const refreshCurrentUser = useCallback(
    async (tokenOverride) => {
      const tk = tokenOverride !== undefined ? tokenOverride : authToken;
      if (!tk) {
        setCurrentUser(null);
        return null;
      }
      try {
        const res = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${tk}` },
        });
        if (res.ok) {
          const data = await res.json();
          if (data.authenticated && data.user) {
            setCurrentUser(data.user);
            return data.user;
          }
        }
      } catch (e) {
        // ignore
      }
      setCurrentUser(null);
      setAuthToken('');
      localStorage.removeItem('sniper_auth_token');
      return null;
    },
    [authToken]
  );

  useEffect(() => {
    refreshCurrentUser();
  }, [refreshCurrentUser]);

  const handleAuthSuccess = useCallback(
    (token, user, message) => {
      setAuthToken(token);
      localStorage.setItem('sniper_auth_token', token);
      setCurrentUser(user);
      showToast(message || `欢迎登录，${user.username}`, 'success');
    },
    [showToast]
  );

  const handleLogout = useCallback(async () => {
    setAuthToken('');
    localStorage.removeItem('sniper_auth_token');
    setCurrentUser(null);
    if (activeTab === 'admin' || activeTab === 'exchanges') {
      setActiveTab('radar');
    }
    if (snapshot.risk?.trading_mode === 'LIVE') {
      const shadowRisk = { ...(riskForm || snapshot.risk), trading_mode: 'SHADOW' };
      setRiskForm(shadowRisk);
      const res = await fetch('/api/risk/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(shadowRisk),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.snapshot) setSnapshot(data.snapshot);
      }
    }
    showToast('您已安全退出账号，实盘系统已隐藏（仅展示影子模拟盘）', 'info');
  }, [activeTab, riskForm, snapshot.risk, showToast]);

  const fetchInitialData = useCallback(async () => {
    try {
      const [symRes, exRes, riskRes, statRes] = await Promise.all([
        fetch('/api/symbols').then((r) => r.json()),
        fetch('/api/exchanges').then((r) => r.json()),
        fetch('/api/risk').then((r) => r.json()),
        fetch('/api/stats').then((r) => r.json()),
      ]);
      if (symRes.strategies) {
        setStrategies(symRes.strategies);
        if (symRes.strategies.length > 0 && !symRes.strategies.some((s) => s.symbol === selectedSymbol)) {
          setSelectedSymbol(symRes.strategies[0].symbol);
        }
      }
      if (symRes.catalog) setCatalog(symRes.catalog);
      if (exRes.exchanges) setExchanges(exRes.exchanges);
      if (riskRes.risk) setRiskForm(riskRes.risk);
      if (statRes.stats) setSnapshot(statRes);
    } catch (err) {
      console.error('Initial load error:', err);
    }
  }, [selectedSymbol]);

  useEffect(() => {
    fetchInitialData();
  }, [fetchInitialData]);

  useEffect(() => {
    let ws = null;
    let reconnectTimer = null;
    let unmounted = false;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${proto}//${window.location.host}/ws/dashboard`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        if (!unmounted) setWsConnected(true);
      };

      ws.onmessage = (ev) => {
        if (unmounted) return;
        try {
          const data = JSON.parse(ev.data);
          setSnapshot(data);
        } catch (e) {
          console.error('WS parse error:', e);
        }
      };

      ws.onclose = () => {
        if (!unmounted) {
          setWsConnected(false);
          reconnectTimer = setTimeout(connect, 1500);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();
    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  const handleKillSwitch = async () => {
    const nextState = !snapshot.risk?.kill_switch_active;
    const res = await fetch(`/api/risk/kill-switch?active=${nextState}`, { method: 'POST' });
    if (res.ok) {
      showToast(
        nextState ? '全局紧急熔断已启动，所有挂单已撤销并暂停开仓' : '全局紧急熔断已解除，恢复毫秒级监控',
        nextState ? 'error' : 'success'
      );
      const riskRes = await fetch('/api/risk').then((r) => r.json());
      if (riskRes.risk) setRiskForm(riskRes.risk);
    }
  };

  const handleModeToggle = async (newMode) => {
    if (newMode === 'LIVE' && !currentUser) {
      showToast('注册并登录账号后才能看到实盘系统', 'error');
      openAuthModal('register');
      return;
    }
    const updated = { ...(riskForm || snapshot.risk), trading_mode: newMode };
    setRiskForm(updated);
    const res = await fetch('/api/risk/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(updated),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '切换通道失败', 'error');
      return;
    }
    const data = await res.json();
    if (data.snapshot) {
      setSnapshot(data.snapshot);
    } else {
      const statRes = await fetch('/api/stats').then((r) => r.json());
      if (statRes.stats) setSnapshot(statRes);
    }
    const exRes = await fetch('/api/exchanges').then((r) => r.json());
    if (exRes.exchanges) setExchanges(exRes.exchanges);

    if (newMode === 'SHADOW') {
      showToast('已切换至「影子模拟盘」：使用高频脉冲仿真流与延迟模拟撮合', 'success');
    } else if (currentUser?.can_use_live) {
      showToast('已切换至「实盘通道」：VIP 会员已生效，已载入真实盘口价格与实盘执行器', 'success');
    } else {
      showToast('已进入「实盘通道」预览真实行情：开通 VIP 会员后即可绑定 API 并启动实盘自动交易', 'info');
    }
  };

  const handleStartTrading = async () => {
    if (snapshot.risk?.trading_mode === 'LIVE') {
      if (!currentUser) {
        showToast('请先注册并登录账号', 'error');
        openAuthModal('login');
        return;
      }
      if (!currentUser.can_use_live) {
        showToast('注册用户开通 VIP 会员后才可以使用实盘交易系统，请先开通会员', 'error');
        setActiveTab('membership');
        return;
      }
    }
    const res = await fetch('/api/trading/start', {
      method: 'POST',
      headers: { ...authHeaders },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.snapshot) setSnapshot(data.snapshot);
      if (data.require_vip) {
        showToast(data.message, 'error');
        setActiveTab('membership');
        return;
      }
      const exRes = await fetch('/api/exchanges').then((r) => r.json());
      if (exRes.exchanges) setExchanges(exRes.exchanges);
      showToast(data.message, data.ok ? 'success' : 'error');
    }
  };

  const handleStopTrading = async () => {
    const res = await fetch('/api/trading/stop', { method: 'POST', headers: { ...authHeaders } });
    if (res.ok) {
      const data = await res.json();
      if (data.snapshot) setSnapshot(data.snapshot);
      showToast(data.message, 'info');
    }
  };

  const handleInjectPulse = async (side, jump_pct, follower_scenario, labelText) => {
    if (snapshot.risk?.trading_mode === 'LIVE') {
      showToast('实盘通道禁止注入虚拟脉冲数据，请先切换至「影子模拟盘」', 'error');
      return;
    }
    const res = await fetch('/api/pulse/inject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: selectedSymbol,
        side,
        jump_pct,
        follower_scenario,
      }),
    });
    if (res.ok) {
      showToast(`已向 ${selectedSymbol} 触发「${labelText}」行情序列`, 'info');
    }
  };

  const handleSaveSymbol = async (cfg) => {
    const res = await fetch('/api/symbol/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    if (res.ok) {
      const data = await res.json();
      setStrategies(data.strategies);
      showToast(`已保存并立即激活 ${cfg.symbol} 策略参数`, 'success');
    }
  };

  const handleDeleteSymbol = async (sym) => {
    const res = await fetch(`/api/symbol/${encodeURIComponent(sym)}`, { method: 'DELETE' });
    if (res.ok) {
      const data = await res.json();
      setStrategies(data.strategies);
      if (selectedSymbol === sym && data.strategies.length > 0) {
        setSelectedSymbol(data.strategies[0].symbol);
      }
      showToast(`已移除监控合约 ${sym}`, 'info');
    } else {
      showToast('当前合约存在在途状态，无法移除', 'error');
    }
  };

  const handleAddSymbolByCode = async (rawSymbolInput) => {
    const target = (rawSymbolInput || searchQuery).trim();
    if (!target) {
      showToast('请输入或选择要添加的合约代号（如 SUI、ARB、WIF）', 'error');
      return;
    }
    const vRes = await fetch(`/api/symbol/verify?symbol=${encodeURIComponent(target)}`).then((r) => r.json());
    const newCfg = {
      symbol: vRes.symbol,
      direction: 'BOTH',
      order_usdt: 300,
      leverage: 5,
      window_ms: 150,
      min_jump_pct: 0.006,
      adaptive_z_score: 3.0,
      lag_confirm_max_pct: 0.001,
      take_profit_pct: 0.0025,
      stop_loss_pct: 0.0035,
      reversal_stop_ratio: 0.5,
      trailing_stop_enabled: true,
      trailing_retracement_pct: 0.0008,
      max_hold_ms: 1500,
      slippage_pct: 0.0005,
      beta_coeff: 0.88,
      auto_beta_enabled: true,
      require_volume_confirm: true,
      use_post_only_tp: true,
      max_signal_age_ms: 80,
      max_book_staleness_ms: 600,
      contract_size: 1.0,
      min_qty: vRes.recommended_precision?.min_qty ?? 0.1,
      qty_precision: vRes.recommended_precision?.qty_precision ?? 2,
      price_precision: vRes.recommended_precision?.price_precision ?? 4,
      is_running: true,
    };
    await handleSaveSymbol(newCfg);
    setSelectedSymbol(vRes.symbol);
    setSearchQuery('');
  };

  const handleSaveExchange = async (ex) => {
    if (!currentUser) {
      showToast('请先注册并登录账号', 'error');
      openAuthModal('login');
      return;
    }
    if (!currentUser.can_use_live) {
      showToast('注册用户开通 VIP 会员后才可绑定与使用实盘 API，请先开通会员', 'error');
      setActiveTab('membership');
      return;
    }
    const res = await fetch('/api/exchanges/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(ex),
    });
    if (res.ok) {
      const data = await res.json();
      setExchanges(data.exchanges);
      const ver = data.verify_result;
      if (ver) {
        if (ver.ok) {
          showToast(`${ex.display_name}: ${ver.auth_message}`, 'success');
        } else if (ver.auth_status !== 'UNCONFIGURED') {
          showToast(`${ex.display_name}: ${ver.auth_message}`, 'error');
        } else {
          showToast(`已更新 ${ex.display_name} 费率与角色配置`, 'info');
        }
      } else {
        showToast(`已更新 ${ex.display_name} 接口与费率配置`, 'success');
      }
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '保存失败，请确认已开通 VIP 会员', 'error');
    }
  };

  const handleVerifyExchange = async (exchangeId, displayName) => {
    if (!currentUser?.can_use_live) {
      showToast('注册用户开通 VIP 会员后才可验证实盘 API 通道，请先开通会员', 'error');
      setActiveTab('membership');
      return;
    }
    showToast(`正在连接 ${displayName} 验证 API 密钥与账户权限...`, 'info');
    const res = await fetch(`/api/exchanges/verify?exchange_id=${encodeURIComponent(exchangeId)}`, {
      method: 'POST',
      headers: { ...authHeaders },
    });
    if (res.ok) {
      const data = await res.json();
      setExchanges(data.exchanges);
      const ver = data.verify_result;
      if (ver) {
        showToast(`${displayName}: ${ver.auth_message}`, ver.ok ? 'success' : 'error');
      }
    }
  };

  const handleClearExchangeCredentials = async (exchangeId, displayName) => {
    if (!currentUser?.can_use_live) {
      showToast('注册用户开通 VIP 会员后才可操作实盘 API 配置', 'error');
      setActiveTab('membership');
      return;
    }
    const res = await fetch(`/api/exchanges/clear-credentials?exchange_id=${encodeURIComponent(exchangeId)}`, {
      method: 'POST',
      headers: { ...authHeaders },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.exchanges) {
        setExchanges(
          data.exchanges.map((item) => ({
            ...item,
            api_key: '',
            api_secret: '',
            passphrase: '',
          }))
        );
      }
      if (data.snapshot) setSnapshot(data.snapshot);
      showToast(data.clear_result?.message || `已清除 ${displayName} 保存的 API 数据`, 'info');
    }
  };

  const handleAutoFetchVipFee = async (ex, vipLevel) => {
    const res = await fetch(
      `/api/exchanges/fetch-fees?exchange_id=${encodeURIComponent(ex.exchange_id)}&vip_level=${encodeURIComponent(vipLevel)}`,
      { method: 'POST' }
    ).then((r) => r.json());
    const updated = {
      ...ex,
      vip_level: res.vip_level,
      taker_fee: res.taker_fee,
      maker_fee: res.maker_fee,
    };
    await handleSaveExchange(updated);
    showToast(res.message, 'info');
  };

  const handleSaveRisk = async () => {
    if (!riskForm) return;
    const res = await fetch('/api/risk/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(riskForm),
    });
    if (res.ok) {
      showToast('全局风控与延迟仿真参数已更新', 'success');
    }
  };

  const filteredCatalog = useMemo(() => {
    const monitoredSet = new Set(strategies.map((s) => s.symbol));
    return catalog.filter((item) => {
      const matchesSector = selectedSector === 'ALL' || item.sector === selectedSector;
      const q = searchQuery.trim().toUpperCase();
      const matchesQuery = !q || item.symbol.includes(q) || item.base.includes(q);
      return matchesSector && matchesQuery && !monitoredSet.has(item.symbol);
    });
  }, [catalog, strategies, selectedSector, searchQuery]);

  const currentStrategyIdx = strategies.findIndex((s) => s.symbol === selectedSymbol);
  const currentStrategy =
    currentStrategyIdx >= 0 ? strategies[currentStrategyIdx] : strategies[0] || {};
  const activeStrategyIndex = currentStrategyIdx >= 0 ? currentStrategyIdx : 0;

  const updateCurrentStrategyField = (patch) => {
    if (!strategies.length) return;
    const next = [...strategies];
    next[activeStrategyIndex] = { ...currentStrategy, ...patch };
    setStrategies(next);
  };

  const currentTelemetry = snapshot.telemetry?.[selectedSymbol] || {};
  const currentRadarSeries = snapshot.radar_series?.[selectedSymbol] || [];
  const stats = snapshot.stats || {};
  const realityGap = snapshot.reality_gap || {};
  const liveStatus = snapshot.live_status || {};
  const tradingControl = snapshot.trading_control || {
    trading_active: !snapshot.risk?.kill_switch_active,
    status_code: 'TRADING_ACTIVE',
    status_label: '自动交易运行中',
    status_detail: '正在毫秒级监听主从所价差脉冲',
    scan_ticks_count: 0,
  };
  const isKillActive = snapshot.risk?.kill_switch_active;
  const isLiveMode = snapshot.risk?.trading_mode === 'LIVE';
  const isTradingActive = Boolean(tradingControl.trading_active);

  const filteredLogs = (snapshot.logs || []).filter((l) => {
    if (logFilter === 'ALL') return true;
    if (logFilter === 'SIGNAL') return l.level === 'SIGNAL' || l.level === 'FILL' || l.level.startsWith('EXIT');
    if (logFilter === 'MISS') return l.level === 'MISS';
    if (logFilter === 'RISK') return l.level === 'WARN' || l.level === 'CRITICAL';
    return true;
  });

  return (
    <div className="min-h-screen flex flex-col">
      {/* Notification Toast */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 px-5 py-3 rounded-full shadow-lg text-xs font-semibold flex items-center gap-2.5 transition-all surface-card ${
            toast.type === 'error'
              ? 'border-rose-500/60 text-rose-600 dark:text-rose-300'
              : toast.type === 'success'
              ? 'border-emerald-500/60 text-emerald-600 dark:text-emerald-300'
              : 'text-main'
          }`}
        >
          {toast.type === 'error' ? (
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-500" strokeWidth={2} />
          ) : (
            <Check className="w-4 h-4 shrink-0 text-emerald-500" strokeWidth={2} />
          )}
          <span>{toast.msg}</span>
        </div>
      )}

      {/* ==================== TOP HEADER ==================== */}
      <header
        className="sticky top-0 z-30 backdrop-blur-md border-b border-line px-6 py-3"
        style={{ backgroundColor: 'var(--bg-header)' }}
      >
        <div className="max-w-[1520px] mx-auto flex flex-wrap items-center justify-between gap-3">
          {/* Left: Brand Identity + Pill Mode Switcher */}
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-3">
              <div className="icon-box w-10 h-10">
                <svg className="w-5 h-5 text-main" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M2 12h4l3-8 4 16 3-8h6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div>
                <h1 className="font-bold text-base tracking-tight text-main leading-tight">
                  跨所价差脉冲高频狙击系统
                </h1>
                <p className="text-[11px] text-dim hidden sm:block mt-0.5">
                  比例对数基差 · 保留价限价抢单 · 被动挂单止盈 · 八阶段同步锁状态机
                </p>
              </div>
            </div>

            {/* Pill Mode Switcher (Registered users only can see LIVE mode) */}
            <div className="flex items-center rounded-full surface-subtle p-1 text-xs">
              <button
                onClick={() => handleModeToggle('SHADOW')}
                className={`px-3.5 py-1.5 rounded-full font-semibold transition ${
                  !isLiveMode ? 'surface-card text-main shadow-sm' : 'text-dim hover:text-main'
                }`}
              >
                影子模拟盘
              </button>
              {currentUser ? (
                <button
                  onClick={() => handleModeToggle('LIVE')}
                  className={`px-3.5 py-1.5 rounded-full font-semibold transition flex items-center gap-1.5 ${
                    isLiveMode ? 'bg-[#0f1419] text-white shadow-sm' : 'text-dim hover:text-main'
                  }`}
                  title={
                    currentUser.can_use_live
                      ? '切换至实盘交易通道'
                      : '已注册用户可查看实盘行情，开通VIP会员后可启动实盘自动交易'
                  }
                >
                  <span>实盘通道</span>
                  {!currentUser.can_use_live && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-500 font-bold">
                      需VIP
                    </span>
                  )}
                </button>
              ) : (
                <button
                  onClick={() => openAuthModal('register')}
                  className="px-3.5 py-1.5 rounded-full font-semibold text-dim hover:text-main transition flex items-center gap-1"
                  title="注册并登录账号后才可查看实盘系统"
                >
                  <Lock className="w-3 h-3" strokeWidth={1.75} />
                  <span>注册看实盘</span>
                </button>
              )}
            </div>
          </div>

          {/* Right: Telemetry Pill, Execution Controls, User Badge & Theme */}
          <div className="flex flex-wrap items-center gap-2.5 text-xs">
            <div
              className="hidden xl:flex items-center gap-2 px-3.5 py-1.5 rounded-full surface-subtle font-mono text-xs"
              title="行情源与报单网络往返延迟 (RTT)"
            >
              <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span className="font-sans text-main font-medium">
                {snapshot.feed_source === 'LIVE_WS' ? '实盘原生直连' : '50Hz仿真流'}
              </span>
              <span className="text-dim">|</span>
              <Wifi className="w-3.5 h-3.5 text-sub" strokeWidth={1.75} />
              <span className="text-main font-bold">{snapshot.last_order_rtt_ms}ms</span>
            </div>

            {/* Unified Execution Control Group */}
            <div className="flex items-center rounded-full surface-subtle p-1 gap-1">
              <div
                className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
                  isTradingActive
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                    ? 'text-rose-600 dark:text-rose-400'
                    : 'text-amber-600 dark:text-amber-400'
                }`}
                title={tradingControl.status_detail || ''}
              >
                <span
                  className={`w-2 h-2 rounded-full ${
                    isTradingActive
                      ? 'bg-emerald-500 animate-ping'
                      : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                      ? 'bg-rose-500'
                      : 'bg-amber-500'
                  }`}
                />
                <span>{tradingControl.status_label || (isTradingActive ? '运行中' : '已停止')}</span>
              </div>

              <button
                onClick={handleStartTrading}
                className={`px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 ${
                  isTradingActive ? 'bg-[#0f1419] text-white shadow-sm' : 'text-main hover:bg-black/5'
                }`}
                title="启动毫秒级自动狙击交易"
              >
                <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>开始</span>
              </button>

              <button
                onClick={handleStopTrading}
                className={`px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 ${
                  !isTradingActive ? 'surface-card text-main shadow-sm' : 'text-sub hover:text-main'
                }`}
                title="停止发送新订单，仅保留实时行情监控"
              >
                <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
                <span>停止</span>
              </button>

              <button
                onClick={handleKillSwitch}
                className={`px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 ${
                  isKillActive ? 'bg-emerald-600 text-white' : 'text-rose-600 dark:text-rose-400 hover:bg-rose-500/10'
                }`}
                title="全局紧急熔断：立即撤单平仓并暂停策略"
              >
                <Power className="w-3 h-3" strokeWidth={2} />
                <span>{isKillActive ? '解除熔断' : '熔断'}</span>
              </button>
            </div>

            {/* User Account & VIP Status Pill */}
            {currentUser ? (
              <div className="flex items-center gap-1.5 px-3 py-1 rounded-full surface-subtle">
                <button
                  onClick={() => setActiveTab('membership')}
                  className="flex items-center gap-1.5 text-left hover:opacity-85 transition"
                  title="点击进入会员中心"
                >
                  <User className="w-3.5 h-3.5 text-main" strokeWidth={1.75} />
                  <span className="font-mono font-bold text-main">{currentUser.username}</span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                      currentUser.is_vip
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                        : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                    }`}
                  >
                    {currentUser.is_vip ? currentUser.vip_label : '未开VIP'}
                  </span>
                </button>
                <button
                  onClick={() => openAuthModal('login')}
                  className="px-2 py-0.5 rounded-full text-[10px] surface-card text-sub hover:text-main font-semibold transition"
                  title="切换账号"
                >
                  切换
                </button>
                <button
                  onClick={handleLogout}
                  className="p-1 rounded-full text-dim hover:text-rose-500 transition"
                  title="退出当前账号"
                >
                  <LogOut className="w-3.5 h-3.5" strokeWidth={1.75} />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <button onClick={() => openAuthModal('login')} className="btn-soft-pill px-4 py-1.5 text-xs">
                  登录
                </button>
                <button onClick={() => openAuthModal('register')} className="btn-primary-pill px-4 py-1.5 text-xs">
                  注册账号
                </button>
              </div>
            )}

            {/* Theme Switcher */}
            <button
              onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
              className="w-8 h-8 rounded-full surface-subtle flex items-center justify-center hover:opacity-85 text-main transition"
              title={theme === 'light' ? '切换至深色模式' : '切换至明亮模式'}
            >
              {theme === 'light' ? (
                <Moon className="w-3.5 h-3.5 text-slate-700" strokeWidth={1.75} />
              ) : (
                <Sun className="w-3.5 h-3.5 text-amber-400" strokeWidth={1.75} />
              )}
            </button>
          </div>
        </div>

        {/* Row 2: Pill Navigation Tabs (Left) | Context Bar (Right) */}
        <div className="max-w-[1520px] mx-auto mt-3 pt-2.5 border-t border-line flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {[
              { id: 'radar', label: '脉冲狙击台', icon: Radar, visible: true },
              { id: 'watchlist', label: `策略参数矩阵 (${strategies.length})`, icon: Sliders, visible: true },
              { id: 'exchanges', label: '交易所与全局风控', icon: Shield, visible: Boolean(currentUser) },
              { id: 'analytics', label: '盈亏统计与诊断', icon: BarChart2, visible: true },
              {
                id: 'membership',
                label: currentUser?.is_vip ? '会员中心 (VIP)' : '购买会员 / 解锁实盘',
                icon: CreditCard,
                visible: true,
              },
              {
                id: 'admin',
                label: '后台管理中心',
                icon: Users,
                visible: true,
              },
            ]
              .filter((t) => t.visible)
              .map((tab) => {
                const Icon = tab.icon;
                const active = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-semibold transition ${
                      active ? 'btn-primary-pill shadow-sm' : 'text-sub hover:text-main hover:bg-black/5'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                    {tab.label}
                  </button>
                );
              })}
          </div>

          {/* Right Context Bar: Live Connection Summary OR Shadow Pulse Test Buttons */}
          {isLiveMode ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="px-3 py-1 rounded-full surface-subtle text-emerald-600 dark:text-emerald-400 font-semibold">
                Binance {liveStatus.binance_ws_connected ? '已连接' : '重连中'} · OKX{' '}
                {liveStatus.okx_ws_connected ? '已连接' : '重连中'}
              </span>
              <span
                onClick={() => setActiveTab('exchanges')}
                className="cursor-pointer px-3 py-1 rounded-full surface-subtle text-main font-medium hover:opacity-85 transition"
              >
                {liveStatus.follower_auth_message || '点击配置执行端 API'}
              </span>
            </div>
          ) : (
            activeTab === 'radar' && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="text-dim mr-1">脉冲注入 ({selectedSymbol.split('/')[0]}):</span>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.0085, 'CONVERGE_TP', '多头拉升收敛止盈')}
                  className="btn-soft-pill px-3 py-1 text-xs text-emerald-600 dark:text-emerald-400"
                >
                  <ArrowUpRight className="w-3.5 h-3.5" strokeWidth={2} />
                  拉升止盈
                </button>
                <button
                  onClick={() => handleInjectPulse('SHORT', 0.0085, 'CONVERGE_TP', '空头下挫收敛止盈')}
                  className="btn-soft-pill px-3 py-1 text-xs text-rose-600 dark:text-rose-400"
                >
                  <ArrowDownRight className="w-3.5 h-3.5" strokeWidth={2} />
                  下挫止盈
                </button>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.0082, 'REVERSAL_SL', '主所假突破反转止损')}
                  className="btn-soft-pill px-3 py-1 text-xs text-amber-600 dark:text-amber-400"
                >
                  <RefreshCw className="w-3 h-3" strokeWidth={2} />
                  反转止损
                </button>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.008, 'MM_WITHDRAW_MISS', '做市商抢先撤单未成交')}
                  className="btn-soft-pill px-3 py-1 text-xs"
                >
                  <Activity className="w-3.5 h-3.5" strokeWidth={1.75} />
                  撤单未成
                </button>
              </div>
            )
          )}
        </div>
      </header>

      {/* Auth Login / Register Modal */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        initialMode={authModalMode}
        onAuthSuccess={handleAuthSuccess}
        showToast={showToast}
      />

      {/* ==================== MAIN WORKSPACE ==================== */}
      <main className="flex-1 max-w-[1520px] w-full mx-auto px-6 py-6 space-y-5">
        {/* VIP REQUIREMENT BANNER WHEN LOGGED IN AS NON-VIP IN LIVE MODE */}
        {isLiveMode && currentUser && !currentUser.can_use_live && (
          <div className="surface-card p-6 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3.5">
              <div className="icon-box w-12 h-12 shrink-0 text-amber-500">
                <Lock className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div>
                <div className="text-base font-bold text-main">
                  当前账号【{currentUser.username}】尚未开通 VIP 会员
                </div>
                <div className="text-xs text-dim mt-1">
                  您已拥有实盘通道实时行情查看权限；开通 VIP 会员后即可绑定交易所 API 并启动实盘自动狙击。
                </div>
              </div>
            </div>
            <button onClick={() => setActiveTab('membership')} className="btn-primary-pill px-6 py-2.5 text-xs">
              立即开通 VIP 会员
            </button>
          </div>
        )}

        {/* Top 4 KPI Summary Cards (Matching Screenshot 1 Stat Cards 1:1) */}
        {(activeTab === 'radar' || activeTab === 'analytics') && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            <div className="surface-card p-6 flex items-center gap-4">
              <div className="icon-box w-12 h-12 shrink-0">
                <DollarSign className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div>
                <div
                  className={`text-2xl font-bold font-mono leading-tight ${
                    (stats.total_net_pnl ?? 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'
                  }`}
                >
                  {(stats.total_net_pnl ?? 0) >= 0 ? '+' : ''}${(stats.total_net_pnl ?? 0).toFixed(2)}
                </div>
                <div className="text-xs text-dim mt-1">
                  {isLiveMode ? '实盘累计净收益' : '模拟盘累计净收益'} (毛利 ${(stats.total_gross_pnl ?? 0).toFixed(2)})
                </div>
              </div>
            </div>

            <div className="surface-card p-6 flex items-center gap-4">
              <div className="icon-box w-12 h-12 shrink-0">
                <Activity className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div>
                <div className="text-2xl font-bold font-mono text-main leading-tight">
                  {stats.fill_rate_pct ?? 0}%
                </div>
                <div className="text-xs text-dim mt-1">
                  IOC 抢单成交率 ({stats.ioc_filled_count ?? 0}成 / {stats.ioc_missed_count ?? 0}撤)
                </div>
              </div>
            </div>

            <div className="surface-card p-6 flex items-center gap-4">
              <div className="icon-box w-12 h-12 shrink-0">
                <Percent className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div>
                <div className="text-2xl font-bold font-mono text-main leading-tight">
                  {stats.win_rate_pct ?? 0}%
                </div>
                <div className="text-xs text-dim mt-1">
                  平仓闭环胜率 ({stats.win_count ?? 0}盈 / {stats.loss_count ?? 0}亏)
                </div>
              </div>
            </div>

            <div className="surface-card p-6 flex items-center gap-4">
              <div className="icon-box w-12 h-12 shrink-0">
                <Wallet className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div>
                <div className="text-2xl font-bold font-mono text-main leading-tight">
                  {stats.total_signals ?? 0}
                </div>
                <div className="text-xs text-dim mt-1">
                  累计触发信号 · 在途 {(snapshot.active_positions || []).length}/
                  {snapshot.risk?.max_concurrent_positions ?? 2} 仓
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ==================== VIEW 1: LIVE RADAR DASHBOARD ==================== */}
        {activeTab === 'radar' && (
          <div className="space-y-5">
            {/* Monitored Contracts Grid (Matching Screenshot 2 Sub-Card / Clean Card aesthetic) */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {strategies.map((s) => {
                const t = snapshot.telemetry?.[s.symbol] || {};
                const isSelected = s.symbol === selectedSymbol;
                const stateKey = t.state || 'IDLE';
                const rawPrec = s.price_precision ?? 4;
                const prec = t.follower_mid && t.follower_mid < 0.0005 ? Math.max(8, rawPrec) : rawPrec;
                const pushGapMs = t.follower_age_ms ?? 15;
                const isStale = pushGapMs > (s.max_book_staleness_ms || 600);
                const maxJumpPct = t.max_jump_seen_pct ?? 0;
                const targetJumpPct = (s.min_jump_pct ?? 0.006) * 100;
                const jumpProgress = Math.min(100, Math.round((maxJumpPct / Math.max(0.01, targetJumpPct)) * 100));

                return (
                  <div
                    key={s.symbol}
                    onClick={() => setSelectedSymbol(s.symbol)}
                    className={`cursor-pointer surface-card p-6 transition-all ${
                      isSelected ? 'ring-2 ring-[#0f1419] dark:ring-sky-400' : 'hover:opacity-95'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-3.5">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-base text-main">
                          {s.symbol.replace(':USDT', '')}
                        </span>
                        <span className="text-[11px] px-2.5 py-0.5 rounded-full surface-subtle text-sub font-medium">
                          {DIRECTION_LABEL_MAP[s.direction] || s.direction}
                        </span>
                      </div>
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                          stateKey === 'IDLE'
                            ? 'surface-subtle text-dim'
                            : stateKey === 'HOLDING'
                            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                            : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                        }`}
                      >
                        {STATE_LABEL_MAP[stateKey] || stateKey}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2.5 text-xs font-mono">
                      <div className="surface-subtle p-3">
                        <div className="text-[11px] text-dim font-sans">主所 (Binance)</div>
                        <div className="text-main font-bold text-sm mt-1">
                          {t.leader_mid ? Number(t.leader_mid).toFixed(prec) : '--'}
                        </div>
                      </div>
                      <div className="surface-subtle p-3">
                        <div className="text-[11px] text-dim font-sans">从所 (OKX)</div>
                        <div className="text-main font-bold text-sm mt-1 truncate">
                          {t.follower_mid ? Number(t.follower_mid).toFixed(prec) : '--'}
                        </div>
                      </div>
                    </div>

                    {/* Visual Pulse Progress Bar */}
                    <div className="mt-3.5 pt-3 border-t border-line space-y-1.5">
                      <div className="flex items-center justify-between text-[11px] font-mono text-dim">
                        <span>基差 {t.basis_bps ?? 0}bp</span>
                        <span>
                          峰值 {maxJumpPct.toFixed(2)}%/{targetJumpPct.toFixed(2)}%
                        </span>
                        <span className={isStale ? 'text-rose-500 font-bold' : 'text-emerald-500 font-semibold'}>
                          {pushGapMs}ms
                        </span>
                      </div>
                      <div className="w-full h-1.5 rounded-full surface-subtle overflow-hidden">
                        <div
                          className={`h-full transition-all duration-300 ${
                            jumpProgress >= 100
                              ? 'bg-emerald-500'
                              : jumpProgress >= 50
                              ? 'bg-amber-500'
                              : 'bg-[#0f1419] dark:bg-sky-400'
                          }`}
                          style={{ width: `${Math.max(5, jumpProgress)}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* 8-Stage State Machine Pipeline (Matching Screenshot 1 `返佣规则` Step Flow Card) */}
            <div className="surface-card p-7">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-5">
                <div className="flex items-center gap-2.5">
                  <Cpu className="w-5 h-5 text-main" strokeWidth={1.75} />
                  <h3 className="text-base font-bold text-main">
                    毫秒级持仓状态机 ({selectedSymbol.replace(':USDT', '')})
                  </h3>
                </div>
                <span className="text-xs text-dim font-mono">
                  目标净利 ≥ {((currentStrategy.take_profit_pct ?? 0.0025) * 100).toFixed(2)}% · 窗口{' '}
                  {currentStrategy.window_ms ?? 150}ms · 脉冲阈值{' '}
                  {((currentStrategy.min_jump_pct ?? 0.006) * 100).toFixed(2)}%
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-4">
                {STATE_PIPELINE.map((st) => {
                  const activeState = currentTelemetry.state || 'IDLE';
                  const isCurrent = activeState === st.key;
                  return (
                    <div key={st.key} className="flex items-center gap-3">
                      <div
                        className={`w-11 h-11 rounded-2xl flex items-center justify-center font-mono text-xs font-bold shrink-0 transition ${
                          isCurrent
                            ? 'bg-[#0f1419] text-white shadow-sm'
                            : 'surface-subtle text-sub'
                        }`}
                      >
                        0{st.step}
                      </div>
                      <div className="min-w-0">
                        <div className="text-xs font-bold text-main truncate">
                          {st.step}. {st.label}
                        </div>
                        <div className="text-[11px] text-dim truncate mt-0.5">{st.desc}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Active Position Status Bar */}
              {(snapshot.active_positions || []).length > 0 && (
                <div className="mt-5 pt-4 border-t border-line flex flex-wrap items-center justify-between gap-3 surface-subtle px-5 py-3.5">
                  {snapshot.active_positions.map((pos) => (
                    <div key={pos.symbol} className="flex flex-wrap items-center gap-4 text-xs font-mono">
                      <span className="px-3 py-1 rounded-full bg-[#0f1419] text-white font-semibold font-sans">
                        持仓中: {pos.symbol} ({pos.side === 'LONG' ? '多头' : '空头'})
                      </span>
                      <span className="text-main">均价: {pos.entry_price}</span>
                      <span className="text-emerald-500 font-semibold">止盈价: {pos.tp_price}</span>
                      <span className="text-rose-500 font-semibold">止损价: {pos.sl_price}</span>
                      <span className="text-main">
                        浮盈:{' '}
                        <strong className={pos.unrealized_net_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                          {(pos.unrealized_net_pct * 100).toFixed(3)}%
                        </strong>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Chart + Real-Time Terminal Log */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
              <div className="lg:col-span-7">
                <RadarChart
                  symbol={selectedSymbol}
                  series={currentRadarSeries}
                  telemetry={currentTelemetry}
                  strategy={currentStrategy}
                  theme={theme}
                />
              </div>

              {/* Real-Time Millisecond Terminal Log */}
              <div className="lg:col-span-5 surface-card p-6 flex flex-col h-[420px]">
                <div className="flex items-center justify-between pb-3.5 mb-3.5 border-b border-line">
                  <div className="flex items-center gap-2.5">
                    <div className="icon-box w-9 h-9">
                      <Terminal className="w-4 h-4 text-main" strokeWidth={1.75} />
                    </div>
                    <h3 className="text-base font-bold text-main">
                      {isLiveMode ? '实盘执行与风控日志' : '毫秒级狙击日志'}
                    </h3>
                  </div>
                  <div className="flex items-center gap-1 rounded-full surface-subtle p-1 text-xs">
                    {[
                      { id: 'ALL', label: '全部' },
                      { id: 'SIGNAL', label: '成交' },
                      { id: 'MISS', label: '撤单' },
                      { id: 'RISK', label: '风控' },
                    ].map((f) => (
                      <button
                        key={f.id}
                        onClick={() => setLogFilter(f.id)}
                        className={`px-2.5 py-1 rounded-full font-semibold transition ${
                          logFilter === f.id ? 'surface-card text-main shadow-sm' : 'text-dim hover:text-main'
                        }`}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto space-y-2 font-mono text-xs pr-1">
                  {filteredLogs.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-center py-10 font-sans">
                      <div className="icon-box w-14 h-14 mb-3">
                        <Terminal className="w-6 h-6" strokeWidth={1.6} />
                      </div>
                      <div className="text-sm font-bold text-main">暂无匹配日志记录</div>
                      <div className="text-xs text-dim mt-1">
                        {isLiveMode
                          ? '当前实盘通道等待脉冲信号触发中'
                          : '可等待行情脉冲或点击右上角「脉冲注入」按钮测试'}
                      </div>
                    </div>
                  ) : (
                    filteredLogs.map((log) => {
                      let badgeCls = 'surface-subtle text-sub';
                      if (log.level === 'SIGNAL')
                        badgeCls = 'bg-amber-500/10 border border-amber-500/25 text-amber-700 dark:text-amber-300';
                      else if (log.level === 'FILL')
                        badgeCls = 'bg-sky-500/10 border border-sky-500/25 text-sky-700 dark:text-sky-300';
                      else if (log.level === 'EXIT_WIN')
                        badgeCls =
                          'bg-emerald-500/10 border border-emerald-500/25 text-emerald-700 dark:text-emerald-300';
                      else if (log.level === 'EXIT_LOSS' || log.level === 'CRITICAL')
                        badgeCls = 'bg-rose-500/10 border border-rose-500/25 text-rose-700 dark:text-rose-300';

                      return (
                        <div key={log.id} className={`px-3.5 py-2.5 rounded-2xl leading-relaxed ${badgeCls}`}>
                          <span className="text-dim mr-1.5">[{log.time}]</span>
                          <span>{log.message}</span>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ==================== VIEW 2: WATCHLIST & STRATEGY CONFIG (Screenshot 3 Split Master-Detail Style) ==================== */}
        {activeTab === 'watchlist' && (
          <div className="space-y-5">
            {/* Top Title Banner Card (Matching Screenshot 1 & 3) */}
            <div className="surface-card p-7 flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-xl font-bold text-main">策略参数矩阵与监控合约库</h2>
                <p className="text-sm text-dim mt-1">
                  内置 {catalog.length} 个永续合约与任意币种搜索添加，支持独立配置每个合约的脉冲窗口与止盈止损参数
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                {[
                  { id: 'ALL', label: '全部热门' },
                  { id: 'AI与DeFi中小盘', label: 'AI与DeFi中小盘' },
                  { id: '高弹性Meme与中小市值', label: '高弹性Meme' },
                  { id: '公链生态', label: '主流公链' },
                ].map((sec) => (
                  <button
                    key={sec.id}
                    onClick={() => setSelectedSector(sec.id)}
                    className={`px-3.5 py-1.5 rounded-full font-semibold transition ${
                      selectedSector === sec.id ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    {sec.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Split Master-Detail Card (Matching Screenshot 3 `工单系统` 1:1) */}
            <div className="surface-card overflow-hidden grid grid-cols-1 lg:grid-cols-12 min-h-[580px]">
              {/* Left Sidebar: Pill Search + Solid Black Add Button + Monitored Contract List */}
              <div className="lg:col-span-4 border-b lg:border-b-0 lg:border-r border-line p-6 flex flex-col justify-between">
                <div className="space-y-4">
                  {/* Pill Search Input (Matching Screenshot 3 `搜索工单...`) */}
                  <div className="relative">
                    <Search className="w-4 h-4 text-dim absolute left-4 top-1/2 -translate-y-1/2" strokeWidth={1.75} />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleAddSymbolByCode(searchQuery);
                      }}
                      placeholder="搜索或输入币种代号 (如 SUI / ARB / WIF)..."
                      className="w-full rounded-full surface-subtle pl-10 pr-4 py-2.5 text-xs font-mono text-main outline-none"
                    />
                  </div>

                  {/* Full-Width Solid Black Pill Button (Matching Screenshot 3 `+ 新建工单`) */}
                  <button
                    onClick={() => handleAddSymbolByCode(searchQuery)}
                    className="btn-primary-pill w-full py-3 text-xs"
                  >
                    <Plus className="w-4 h-4" strokeWidth={2} />
                    <span>+ 添加并激活监控合约</span>
                  </button>

                  {/* Monitored Contracts List */}
                  <div className="pt-2">
                    <div className="text-xs font-bold text-dim mb-2.5 px-1">
                      当前监控中合约 ({strategies.length})
                    </div>
                    <div className="space-y-2 max-h-[290px] overflow-y-auto pr-1">
                      {strategies.map((item) => {
                        const active = item.symbol === currentStrategy.symbol;
                        return (
                          <div
                            key={item.symbol}
                            onClick={() => setSelectedSymbol(item.symbol)}
                            className={`cursor-pointer p-3.5 rounded-2xl transition flex items-center justify-between ${
                              active
                                ? 'bg-[#0f1419] text-white shadow-sm'
                                : 'surface-subtle text-main hover:opacity-90'
                            }`}
                          >
                            <div>
                              <div className="font-mono font-bold text-sm">{item.symbol}</div>
                              <div className={`text-[11px] mt-0.5 ${active ? 'text-white/75' : 'text-dim'}`}>
                                {DIRECTION_LABEL_MAP[item.direction]} · {item.order_usdt}U · {item.leverage}x
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                                  item.is_running
                                    ? active
                                      ? 'bg-emerald-500 text-white'
                                      : 'bg-emerald-500/15 text-emerald-600'
                                    : 'bg-slate-500/20 text-dim'
                                }`}
                              >
                                {item.is_running ? '监控中' : '已暂停'}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Quick-Add Recommended Contract Chips */}
                <div className="pt-4 mt-4 border-t border-line">
                  <div className="text-xs font-bold text-dim mb-2">一键快速添加推荐合约</div>
                  <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
                    {filteredCatalog.slice(0, 14).map((item) => (
                      <button
                        key={item.symbol}
                        onClick={() => handleAddSymbolByCode(item.symbol)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full surface-subtle hover:opacity-80 text-xs font-mono transition"
                      >
                        <Plus className="w-3 h-3 text-main" strokeWidth={2} />
                        <span className="font-bold text-main">{item.base}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Right Detail Area: Selected Contract Parameter Editor (Matching Screenshot 2 Form Style) */}
              <div className="lg:col-span-8 p-7 flex flex-col justify-between">
                {currentStrategy && currentStrategy.symbol ? (
                  <div className="space-y-6">
                    <div className="flex flex-wrap items-center justify-between gap-4 pb-5 border-b border-line">
                      <div className="flex items-center gap-3.5">
                        <div className="icon-box w-12 h-12 font-mono font-bold text-sm text-main">
                          {currentStrategy.symbol.split('/')[0]}
                        </div>
                        <div>
                          <h3 className="text-lg font-bold font-mono text-main">{currentStrategy.symbol}</h3>
                          <p className="text-xs text-dim mt-0.5">
                            独立毫秒级脉冲检测窗口、保留价滑点保护与动态收敛系数配置
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2.5">
                        <select
                          value={currentStrategy.direction || 'BOTH'}
                          onChange={(e) => updateCurrentStrategyField({ direction: e.target.value })}
                          className="input-field py-2 px-3.5 text-xs font-semibold w-auto"
                        >
                          <option value="BOTH">多空双向狙击</option>
                          <option value="LONG_ONLY">仅限做多</option>
                          <option value="SHORT_ONLY">仅限做空</option>
                        </select>

                        <button
                          onClick={() => handleDeleteSymbol(currentStrategy.symbol)}
                          className="btn-soft-pill px-4 py-2 text-xs text-rose-600"
                          title="移除该监控合约"
                        >
                          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                          <span>移除合约</span>
                        </button>

                        <button
                          onClick={() => handleSaveSymbol(currentStrategy)}
                          className="btn-primary-pill px-6 py-2.5 text-xs"
                        >
                          保存策略参数
                        </button>
                      </div>
                    </div>

                    {/* 12 Numeric Parameters Grid */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 text-xs">
                      <div>
                        <label className="font-bold text-main block mb-2">单笔名义金额 (USDT)</label>
                        <input
                          type="number"
                          value={currentStrategy.order_usdt ?? 300}
                          onChange={(e) => updateCurrentStrategyField({ order_usdt: Number(e.target.value) })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">合约杠杆倍数</label>
                        <input
                          type="number"
                          value={currentStrategy.leverage ?? 5}
                          onChange={(e) => updateCurrentStrategyField({ leverage: Number(e.target.value) })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">脉冲检测窗口 (毫秒)</label>
                        <input
                          type="number"
                          value={currentStrategy.window_ms ?? 150}
                          onChange={(e) => updateCurrentStrategyField({ window_ms: Number(e.target.value) })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">最小脉冲幅度 (%)</label>
                        <input
                          type="number"
                          step="0.05"
                          value={((currentStrategy.min_jump_pct ?? 0.006) * 100).toFixed(2)}
                          onChange={(e) => updateCurrentStrategyField({ min_jump_pct: Number(e.target.value) / 100 })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">扣费目标净止盈 (%)</label>
                        <input
                          type="number"
                          step="0.05"
                          value={((currentStrategy.take_profit_pct ?? 0.0025) * 100).toFixed(2)}
                          onChange={(e) =>
                            updateCurrentStrategyField({ take_profit_pct: Number(e.target.value) / 100 })
                          }
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">最大硬性止损 (%)</label>
                        <input
                          type="number"
                          step="0.05"
                          value={((currentStrategy.stop_loss_pct ?? 0.0035) * 100).toFixed(2)}
                          onChange={(e) => updateCurrentStrategyField({ stop_loss_pct: Number(e.target.value) / 100 })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">最长持仓限时 (毫秒)</label>
                        <input
                          type="number"
                          step="100"
                          value={currentStrategy.max_hold_ms ?? 1500}
                          onChange={(e) => updateCurrentStrategyField({ max_hold_ms: Number(e.target.value) })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">IOC 容忍滑点上限 (%)</label>
                        <input
                          type="number"
                          step="0.01"
                          value={((currentStrategy.slippage_pct ?? 0.0005) * 100).toFixed(2)}
                          onChange={(e) => updateCurrentStrategyField({ slippage_pct: Number(e.target.value) / 100 })}
                          className="input-field font-mono"
                        />
                      </div>

                      <div>
                        <label className="font-bold text-main block mb-2">从所滞后确认上限 (%)</label>
                        <input
                          type="number"
                          step="0.02"
                          value={((currentStrategy.lag_confirm_max_pct ?? 0.001) * 100).toFixed(2)}
                          onChange={(e) =>
                            updateCurrentStrategyField({ lag_confirm_max_pct: Number(e.target.value) / 100 })
                          }
                          className="input-field font-mono"
                        />
                      </div>
                    </div>

                    {/* Checkboxes in Screenshot 2 `邮件通知偏好` Sub-Card Row Style */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
                      {[
                        {
                          field: 'is_running',
                          title: '启用该合约实时脉冲监控',
                          desc: '关闭后暂停该合约的新信号触发',
                        },
                        {
                          field: 'use_post_only_tp',
                          title: '开仓即挂被动限价止盈单 (Post-Only)',
                          desc: '优先享受 Maker 低费率平仓',
                        },
                        {
                          field: 'trailing_stop_enabled',
                          title: '启用移动追踪止盈',
                          desc: '达到止盈阈值后跟随锁定利润',
                        },
                        {
                          field: 'require_volume_confirm',
                          title: '微观价格与成交量同向确认',
                          desc: '过滤无量虚晃假突破脉冲',
                        },
                      ].map((opt) => (
                        <label
                          key={opt.field}
                          className="surface-subtle p-4 flex items-center justify-between gap-3 cursor-pointer"
                        >
                          <div>
                            <div className="text-xs font-bold text-main">{opt.title}</div>
                            <div className="text-[11px] text-dim mt-0.5">{opt.desc}</div>
                          </div>
                          <input
                            type="checkbox"
                            checked={Boolean(currentStrategy[opt.field])}
                            onChange={(e) => updateCurrentStrategyField({ [opt.field]: e.target.checked })}
                            className="w-4 h-4 accent-[#0f1419]"
                          />
                        </label>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center py-20">
                    <div className="icon-box w-16 h-16 mb-3">
                      <Sliders className="w-7 h-7" strokeWidth={1.6} />
                    </div>
                    <div className="text-base font-bold text-main">请在左侧选择监控合约查看详情</div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ==================== VIEW 3: EXCHANGE CONNECTOR & RISK CONTROL ==================== */}
        {activeTab === 'exchanges' && (
          <div className="space-y-5">
            {/* Top Banner Card (Matching Screenshot 1 & 2) */}
            <div className="surface-card p-7 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <Shield className="w-6 h-6 text-main" strokeWidth={1.75} />
                <div>
                  <h2 className="text-xl font-bold text-main">交易所连接配置与全局风控中心</h2>
                  <p className="text-sm text-dim mt-1">
                    管理主信号源 (Leader) 与实盘执行端 (Follower) API 密钥、费率结构与全局熔断阈值
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2.5">
                <button
                  onClick={() => handleClearExchangeCredentials('ALL', '全部交易所')}
                  className="btn-soft-pill px-4 py-2 text-xs text-rose-600"
                >
                  <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                  <span>清除全部已存 API 数据</span>
                </button>
                <span className="btn-primary-pill px-5 py-2 text-xs pointer-events-none">
                  <Lock className="w-3.5 h-3.5" strokeWidth={1.75} />
                  <span>AES-256-GCM 本地加密</span>
                </span>
              </div>
            </div>

            {/* Exchange Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
              {exchanges.map((ex, idx) => {
                const health = snapshot.exchange_health?.[ex.exchange_id] || {};
                const authStatus = ex.auth_status || 'UNCONFIGURED';
                const isVerified = authStatus === 'VERIFIED';
                const isError = authStatus === 'ERROR' || authStatus === 'CONFIG_INCOMPLETE';
                const needsPass = ex.exchange_id === 'okx' || ex.exchange_id === 'bitget';

                return (
                  <div key={ex.exchange_id} className="surface-card p-7 flex flex-col justify-between space-y-5">
                    <div className="space-y-4">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-base text-main">{ex.display_name}</span>
                            <span
                              className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                                isVerified
                                  ? 'bg-emerald-500/15 text-emerald-600'
                                  : isError
                                  ? 'bg-rose-500/15 text-rose-600'
                                  : 'surface-subtle text-dim'
                              }`}
                            >
                              {isVerified ? '验证通过' : isError ? '配置异常' : '未配置'}
                            </span>
                          </div>
                          <div className="text-xs font-mono text-dim mt-1">
                            订阅流: {health.mode || ex.stream_mode || 'BBO-TBT'} · 延迟{' '}
                            <span className="text-emerald-500 font-bold">{health.ping_ms ?? ex.ping_ms} ms</span>
                          </div>
                        </div>

                        <select
                          value={ex.role}
                          onChange={(e) => {
                            const next = [...exchanges];
                            next[idx] = { ...ex, role: e.target.value };
                            setExchanges(next);
                          }}
                          className="px-3 py-1.5 rounded-full text-xs font-semibold surface-subtle text-main outline-none"
                        >
                          <option value="leader">主所 (信号源)</option>
                          <option value="follower">从所 (执行端)</option>
                        </select>
                      </div>

                      {/* Sub-card Status Box (Matching Screenshot 2 Sub-Card) */}
                      <div className="surface-subtle p-4 text-xs flex items-start gap-2.5">
                        {isVerified ? (
                          <Check className="w-4 h-4 shrink-0 mt-0.5 text-emerald-500" strokeWidth={2} />
                        ) : isError ? (
                          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-500" strokeWidth={2} />
                        ) : (
                          <Lock className="w-4 h-4 shrink-0 mt-0.5 text-dim" strokeWidth={1.75} />
                        )}
                        <div className="leading-relaxed">
                          <div className="font-semibold text-main">{ex.auth_message || '未配置 API 密钥'}</div>
                          {ex.last_verified_at && (
                            <div className="text-[11px] text-dim font-mono mt-0.5">
                              最近校验: {ex.last_verified_at}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="space-y-3.5 text-xs">
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="font-bold text-main block mb-1.5">吃单费率 Taker (%)</label>
                            <input
                              type="number"
                              step="0.005"
                              value={(ex.taker_fee * 100).toFixed(3)}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, taker_fee: Number(e.target.value) / 100 };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                          <div>
                            <label className="font-bold text-main block mb-1.5">挂单费率 Maker (%)</label>
                            <input
                              type="number"
                              step="0.005"
                              value={(ex.maker_fee * 100).toFixed(3)}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, maker_fee: Number(e.target.value) / 100 };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                        </div>

                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <label className="font-bold text-main">API Key</label>
                            <span className="text-[11px] font-mono text-dim">
                              {ex.has_api_key ? `已存 (${ex.api_key_masked})` : '必填'}
                            </span>
                          </div>
                          <input
                            type="text"
                            value={ex.api_key || ''}
                            placeholder={ex.has_api_key ? `已存: ${ex.api_key_masked} (输入新值覆盖)` : '输入完整 API Key'}
                            onChange={(e) => {
                              const next = [...exchanges];
                              next[idx] = { ...ex, api_key: e.target.value };
                              setExchanges(next);
                            }}
                            className="input-field font-mono py-2.5"
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <div className="flex items-center justify-between mb-1.5">
                              <label className="font-bold text-main">API Secret</label>
                              <span className="text-[11px] font-mono text-dim">
                                {ex.has_api_secret ? '已保存' : '未填'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.api_secret || ''}
                              placeholder={ex.has_api_secret ? '已加密保存' : '输入 Secret'}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, api_secret: e.target.value };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                          <div>
                            <div className="flex items-center justify-between mb-1.5">
                              <label className="font-bold text-main">Passphrase</label>
                              <span className="text-[11px] font-mono text-dim">
                                {ex.has_passphrase ? '已保存' : needsPass ? '欧易必填' : '选填'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.passphrase || ''}
                              placeholder={ex.has_passphrase ? '已加密保存' : '创建API时设的密码'}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, passphrase: e.target.value };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="pt-4 border-t border-line flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => handleClearExchangeCredentials(ex.exchange_id, ex.display_name)}
                          className="btn-soft-pill px-3 py-1.5 text-xs text-rose-600"
                          title="清除该交易所保存的 API 密钥"
                        >
                          清除
                        </button>
                        <button
                          onClick={() => handleAutoFetchVipFee(ex, 'VIP 1')}
                          className="btn-soft-pill px-3 py-1.5 text-xs"
                        >
                          同步费率
                        </button>
                        <button
                          onClick={() => handleVerifyExchange(ex.exchange_id, ex.display_name)}
                          className="btn-soft-pill px-3 py-1.5 text-xs"
                        >
                          测试连接
                        </button>
                      </div>
                      <button onClick={() => handleSaveExchange(ex)} className="btn-primary-pill px-5 py-2 text-xs">
                        验证并保存
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            {riskForm && (
              <div className="surface-card p-7">
                <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-main">全局风控阈值与高保真影子盘参数</h3>
                    <p className="text-xs text-dim mt-1">
                      单日亏损熔断、连续亏损熔断、并发持仓上限、延迟熔断与对数正态网络延迟仿真参数
                    </p>
                  </div>
                  <button onClick={handleSaveRisk} className="btn-primary-pill px-6 py-2.5 text-xs">
                    保存全局风控配置
                  </button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                  <div>
                    <label className="font-bold text-main block mb-2">单日最大亏损 (USDT)</label>
                    <input
                      type="number"
                      value={riskForm.max_daily_loss_usdt}
                      onChange={(e) => setRiskForm({ ...riskForm, max_daily_loss_usdt: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">连续亏损熔断 (笔)</label>
                    <input
                      type="number"
                      value={riskForm.max_consecutive_losses}
                      onChange={(e) => setRiskForm({ ...riskForm, max_consecutive_losses: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">最大并发持仓数</label>
                    <input
                      type="number"
                      value={riskForm.max_concurrent_positions}
                      onChange={(e) => setRiskForm({ ...riskForm, max_concurrent_positions: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">最大名义总敞口 (USDT)</label>
                    <input
                      type="number"
                      value={riskForm.max_exposure_usdt}
                      onChange={(e) => setRiskForm({ ...riskForm, max_exposure_usdt: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">报单延迟熔断线 (ms)</label>
                    <input
                      type="number"
                      value={riskForm.latency_circuit_breaker_ms}
                      onChange={(e) =>
                        setRiskForm({ ...riskForm, latency_circuit_breaker_ms: Number(e.target.value) })
                      }
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">影子盘基准延迟 (ms)</label>
                    <input
                      type="number"
                      value={riskForm.shadow_base_latency_ms}
                      onChange={(e) => setRiskForm({ ...riskForm, shadow_base_latency_ms: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">做市商撤单逆向概率</label>
                    <input
                      type="number"
                      step="0.05"
                      value={riskForm.shadow_adverse_selection_prob}
                      onChange={(e) =>
                        setRiskForm({ ...riskForm, shadow_adverse_selection_prob: Number(e.target.value) })
                      }
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">行情源接入模式</label>
                    <select
                      value={riskForm.feed_source}
                      onChange={(e) => setRiskForm({ ...riskForm, feed_source: e.target.value })}
                      className="input-field"
                    >
                      <option value="AUTO">自动容灾切换</option>
                      <option value="SIMULATOR">高频仿真引擎</option>
                      <option value="REAL_WS">仅限实盘原生流</option>
                    </select>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ==================== VIEW 4: ANALYTICS, REALITY GAP & PNL REVIEW ==================== */}
        {activeTab === 'analytics' && (
          <div className="space-y-5">
            <EquityChart equityCurve={stats.equity_curve || []} stats={stats} theme={theme} />

            {/* Shadow-to-Live Reality Gap & Decay Diagnostic Panel (Matching Screenshot 2 4-Subcard Style) */}
            <div className="surface-card p-7">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-5">
                <div className="flex items-center gap-2.5">
                  <Shield className="w-5 h-5 text-main" strokeWidth={1.75} />
                  <h3 className="text-lg font-bold text-main">
                    模拟盘数据可靠性与实盘衰减量化诊断 (Shadow-to-Live Reality Gap)
                  </h3>
                </div>
                <span className="text-xs font-mono text-dim">
                  基于当前报单延迟 ({snapshot.last_order_rtt_ms}ms) 与双边费率结构实时测算
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
                <div className="surface-subtle p-5">
                  <div className="text-dim">1. 抢单成交率衰减 (同机房竞争)</div>
                  <div className="mt-2 flex items-baseline justify-between font-mono">
                    <span className="text-sub">模拟: {realityGap.shadow_fill_rate_pct}%</span>
                    <span className="text-main font-bold text-base">
                      实盘预计 ~{realityGap.projected_live_fill_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-2 leading-relaxed">
                    机房托管做市商反应时间 &lt;2ms，普通服务器存在约 {realityGap.colocation_gap_ms}ms 物理延迟劣势。
                  </p>
                </div>

                <div className="surface-subtle p-5">
                  <div className="text-dim">2. 逆向选择与胜率衰减</div>
                  <div className="mt-2 flex items-baseline justify-between font-mono">
                    <span className="text-sub">模拟: {realityGap.shadow_win_rate_pct}%</span>
                    <span className="text-main font-bold text-base">
                      实盘预计 ~{realityGap.projected_live_win_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-2 leading-relaxed">
                    实盘中在 30ms 后依然能抢到的挂单往往偏向逆向选择，需严格启用成交量同向确认。
                  </p>
                </div>

                <div className="surface-subtle p-5">
                  <div className="text-dim">3. 盈亏平衡胜率红线 (含手续费)</div>
                  <div className="mt-2 flex items-baseline justify-between font-mono">
                    <span className="text-sub">Post-Only止盈</span>
                    <span className="text-emerald-500 font-bold text-base">
                      需 ≥ {realityGap.breakeven_win_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-2 leading-relaxed">
                    目标净止盈 0.25% 对比硬止损 0.35%+双边成本，收敛胜率需高于 64.3% 方可产生正期望。
                  </p>
                </div>

                <div className="surface-subtle p-5">
                  <div className="text-dim">4. 实盘可行性区间结论</div>
                  <div className="mt-2 font-bold text-sm text-main">聚焦中小盘 + 二线执行所</div>
                  <p className="text-[11px] text-dim mt-2 leading-relaxed">
                    主流币：{realityGap.mainstream_verdict}；中小盘：{realityGap.altcoin_verdict}。
                  </p>
                </div>
              </div>
            </div>

            {/* Historical Trades Table */}
            <div className="surface-card p-7">
              <div className="flex items-center justify-between mb-5">
                <div>
                  <h3 className="text-lg font-bold text-main">
                    {isLiveMode ? '实盘通道历史成交复盘明细表' : '影子模拟盘历史狙击复盘明细表'}
                  </h3>
                  <p className="text-xs text-dim mt-1">
                    由独立后台队列批量写入 SQLite 数据库，模拟盘与实盘通道账本严格分表隔离
                  </p>
                </div>
                <span className="text-xs font-mono text-dim">
                  最近 {(stats.recent_trades || []).length} 笔闭环交易
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                      <th className="py-3 px-3.5 font-medium rounded-l-xl">触发时间</th>
                      <th className="py-3 px-3.5 font-medium">合约代号</th>
                      <th className="py-3 px-3.5 font-medium">方向</th>
                      <th className="py-3 px-3.5 font-medium">主所脉冲幅度</th>
                      <th className="py-3 px-3.5 font-medium">收敛系数 β</th>
                      <th className="py-3 px-3.5 font-medium">开仓均价</th>
                      <th className="py-3 px-3.5 font-medium">平仓均价</th>
                      <th className="py-3 px-3.5 font-medium">信号至成交</th>
                      <th className="py-3 px-3.5 font-medium">持仓耗时</th>
                      <th className="py-3 px-3.5 font-medium">平仓方式</th>
                      <th className="py-3 px-3.5 font-medium">双边手续费</th>
                      <th className="py-3 px-3.5 font-medium">净收益 (USDT)</th>
                      <th className="py-3 px-3.5 font-medium rounded-r-xl">平仓归因</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y border-line font-mono">
                    {(stats.recent_trades || []).length === 0 ? (
                      <tr>
                        <td colSpan={13} className="py-14 text-center text-dim font-sans">
                          {isLiveMode
                            ? '当前实盘通道尚无真实成交记录（已完全隔离模拟盘数据）'
                            : '暂无成交记录，请等待脉冲信号或点击顶部「脉冲注入」按钮验证全流程'}
                        </td>
                      </tr>
                    ) : (
                      (stats.recent_trades || []).map((tr, i) => {
                        const badge = EXIT_BADGE_MAP[tr.reason] || {
                          text: tr.reason,
                          cls: 'surface-subtle text-sub',
                        };
                        return (
                          <tr key={tr.id || i} className="hover:opacity-90 transition">
                            <td className="py-3 px-3.5 text-dim">{tr.time}</td>
                            <td className="py-3 px-3.5 font-bold text-main">{tr.symbol}</td>
                            <td className="py-3 px-3.5 font-sans">
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                                  tr.side === 'LONG'
                                    ? 'bg-emerald-500/12 text-emerald-600'
                                    : 'bg-rose-500/12 text-rose-600'
                                }`}
                              >
                                {tr.side === 'LONG' ? '做多' : '做空'}
                              </span>
                            </td>
                            <td
                              className={`py-3 px-3.5 font-bold ${
                                tr.jump_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'
                              }`}
                            >
                              {tr.jump_pct >= 0 ? `+${tr.jump_pct}%` : `${tr.jump_pct}%`}
                            </td>
                            <td className="py-3 px-3.5 text-sub">{tr.beta_est}</td>
                            <td className="py-3 px-3.5 text-main">{tr.entry}</td>
                            <td className="py-3 px-3.5 text-main">{tr.exit}</td>
                            <td className="py-3 px-3.5 text-sub">{tr.signal_to_fill_ms} ms</td>
                            <td className="py-3 px-3.5 text-sub">{tr.hold_ms} ms</td>
                            <td className="py-3 px-3.5 font-sans">
                              <span className="px-2.5 py-0.5 rounded-full text-[11px] surface-subtle text-sub">
                                {tr.exit_order_type === 'MAKER_POST_ONLY' ? '被动挂单 (Maker)' : '主动吃单 (Taker)'}
                              </span>
                            </td>
                            <td className="py-3 px-3.5 text-dim">{Number(tr.fee).toFixed(4)}</td>
                            <td
                              className={`py-3 px-3.5 font-bold ${
                                tr.net_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'
                              }`}
                            >
                              {tr.net_pnl >= 0 ? `+${Number(tr.net_pnl).toFixed(4)}` : Number(tr.net_pnl).toFixed(4)}
                            </td>
                            <td className="py-3 px-3.5 font-sans">
                              <span className={`px-2.5 py-0.5 rounded-full border text-[11px] font-medium ${badge.cls}`}>
                                {badge.text}
                              </span>
                            </td>
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

        {/* ==================== TAB 5: MEMBERSHIP PURCHASE & MEMBER CENTER ==================== */}
        {activeTab === 'membership' && (
          <MembershipCenterView
            currentUser={currentUser}
            authToken={authToken}
            onUserUpdated={(updatedUser) => setCurrentUser(updatedUser)}
            onOpenAuthModal={openAuthModal}
            onSwitchToLive={() => {
              handleModeToggle('LIVE');
              setActiveTab('radar');
            }}
            showToast={showToast}
          />
        )}

        {/* ==================== TAB 6: ADMIN USER & MEMBERSHIP MANAGEMENT ==================== */}
        {activeTab === 'admin' && (
          <AdminSystemView
            currentUser={currentUser}
            authToken={authToken}
            onCurrentUserRefresh={() => refreshCurrentUser()}
            onOpenAuthModal={openAuthModal}
            showToast={showToast}
          />
        )}
      </main>
    </div>
  );
}
