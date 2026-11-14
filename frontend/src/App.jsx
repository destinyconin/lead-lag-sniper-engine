import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  Check,
  Cpu,
  Lock,
  Moon,
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
  Wifi,
} from 'lucide-react';
import RadarChart from './components/RadarChart';
import EquityChart from './components/EquityChart';

const STATE_PIPELINE = [
  { key: 'IDLE', step: '01', label: '空闲监控', desc: '监听价差脉冲' },
  { key: 'SIGNALED', step: '02', label: '信号锁定', desc: '同步互斥加锁' },
  { key: 'SUBMITTED', step: '03', label: '保留价报单', desc: '发射限价IOC' },
  { key: 'RECONCILE', step: '04', label: '回报对账', desc: '私有流核验' },
  { key: 'HOLDING', step: '05', label: '持仓监控', desc: '事件驱动巡检' },
  { key: 'EXITING', step: '06', label: '平仓执行', desc: '撤单与重试' },
  { key: 'CLOSED', step: '07', label: '盈亏核算', desc: '异步队列落库' },
  { key: 'COOLDOWN', step: '08', label: '防抖冷却', desc: '抑制尾随重入' },
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
    cls: 'bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/30',
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

  const showToast = useCallback((msg, type = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3400);
  }, []);

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
    const updated = { ...(riskForm || snapshot.risk), trading_mode: newMode };
    setRiskForm(updated);
    const res = await fetch('/api/risk/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updated),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.snapshot) {
        setSnapshot(data.snapshot);
      } else {
        const statRes = await fetch('/api/stats').then((r) => r.json());
        if (statRes.stats) setSnapshot(statRes);
      }
      const exRes = await fetch('/api/exchanges').then((r) => r.json());
      if (exRes.exchanges) setExchanges(exRes.exchanges);
    }
    showToast(
      newMode === 'SHADOW'
        ? '已切换至「影子模拟盘」：使用高频脉冲仿真流与延迟模拟撮合'
        : '已切换至「实盘通道」：已载入 Binance 与 OKX 真实盘口价格及独立实盘账本',
      'success'
    );
  };

  const handleStartTrading = async () => {
    const res = await fetch('/api/trading/start', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      if (data.snapshot) setSnapshot(data.snapshot);
      const exRes = await fetch('/api/exchanges').then((r) => r.json());
      if (exRes.exchanges) setExchanges(exRes.exchanges);
      showToast(data.message, data.ok ? 'success' : 'error');
    }
  };

  const handleStopTrading = async () => {
    const res = await fetch('/api/trading/stop', { method: 'POST' });
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
    const res = await fetch('/api/exchanges/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
    }
  };

  const handleVerifyExchange = async (exchangeId, displayName) => {
    showToast(`正在连接 ${displayName} 验证 API 密钥与账户权限...`, 'info');
    const res = await fetch(`/api/exchanges/verify?exchange_id=${encodeURIComponent(exchangeId)}`, {
      method: 'POST',
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(riskForm),
    });
    if (res.ok) {
      showToast('全局风控与延迟仿真参数已更新', 'success');
    }
  };

  // Filtered catalog for quick search & sector selection
  const filteredCatalog = useMemo(() => {
    const monitoredSet = new Set(strategies.map((s) => s.symbol));
    return catalog.filter((item) => {
      const matchesSector = selectedSector === 'ALL' || item.sector === selectedSector;
      const q = searchQuery.trim().toUpperCase();
      const matchesQuery = !q || item.symbol.includes(q) || item.base.includes(q);
      return matchesSector && matchesQuery && !monitoredSet.has(item.symbol);
    });
  }, [catalog, strategies, selectedSector, searchQuery]);

  const currentStrategy = strategies.find((s) => s.symbol === selectedSymbol) || strategies[0] || {};
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
          className={`fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-lg border shadow-lg text-xs font-medium flex items-center gap-2 transition-all surface-card ${
            toast.type === 'error'
              ? 'border-rose-500/60 text-rose-600 dark:text-rose-300'
              : toast.type === 'success'
              ? 'border-emerald-500/60 text-emerald-600 dark:text-emerald-300'
              : 'border-sky-500/60 text-sky-600 dark:text-sky-300'
          }`}
        >
          {toast.type === 'error' ? (
            <AlertCircle className="w-4 h-4 shrink-0" strokeWidth={1.75} />
          ) : (
            <Check className="w-4 h-4 shrink-0" strokeWidth={1.75} />
          )}
          <span>{toast.msg}</span>
        </div>
      )}

      {/* ==================== TOP HEADER ==================== */}
      <header
        className="sticky top-0 z-30 backdrop-blur-md border-b border-line px-4 py-2.5"
        style={{ backgroundColor: 'var(--bg-header)' }}
      >
        <div className="max-w-[1600px] mx-auto flex flex-wrap items-center justify-between gap-3">
          {/* Brand Identity */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg border border-sky-500/30 bg-sky-500/10 flex items-center justify-center text-sky-600 dark:text-sky-400">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M2 12h4l3-8 4 16 3-8h6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="font-bold text-base tracking-tight text-main">跨所价差脉冲高频狙击系统</h1>
                <span
                  className={`px-2 py-0.5 text-[11px] font-semibold rounded border ${
                    isLiveMode
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                      : 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/25'
                  }`}
                >
                  {isLiveMode ? '当前：实盘交易通道 (独立账本)' : '当前：影子模拟盘 (含延迟与逆向选择仿真)'}
                </span>
              </div>
              <p className="text-[11px] text-dim">
                时间衰减比例基差 · 保留价限价抢单 · 被动挂单止盈 · 八阶段同步锁与回报对账状态机
              </p>
            </div>
          </div>

          {/* Right Status & Controls */}
          <div className="flex flex-wrap items-center gap-2.5 text-xs">
            {/* Data Stream Status */}
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg surface-subtle">
              <span
                className={`w-2 h-2 rounded-full ${
                  wsConnected ? 'bg-emerald-500' : 'bg-rose-500'
                }`}
              />
              <span className="text-dim">行情通道:</span>
              <span className="font-medium text-main">
                {snapshot.feed_source === 'LIVE_WS'
                  ? '交易所原生直连'
                  : snapshot.feed_source === 'LIVE_WS_WAITING'
                  ? '等待实盘直连'
                  : '高频脉冲仿真流 (50Hz)'}
              </span>
            </div>

            {/* Order RTT */}
            <div
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg surface-subtle font-mono"
              title="报单网络往返延迟 (Round-Trip Time)"
            >
              <Wifi className="w-3.5 h-3.5 text-sky-500" strokeWidth={1.75} />
              <span className="text-dim">报单往返延迟:</span>
              <span className="font-semibold text-main">{snapshot.last_order_rtt_ms} ms</span>
            </div>

            {/* Real-Time Trading Execution Status Pill */}
            <div
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border font-medium ${
                isTradingActive
                  ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-600 dark:text-emerald-400'
                  : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                  ? 'bg-rose-500/12 border-rose-500/35 text-rose-600 dark:text-rose-400'
                  : 'bg-amber-500/12 border-amber-500/35 text-amber-600 dark:text-amber-400'
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
              <span>{tradingControl.status_label || (isTradingActive ? '自动交易运行中' : '已停止待命')}</span>
            </div>

            {/* Start / Stop Trading Controls */}
            <div className="flex items-center rounded-lg surface-subtle p-0.5 border border-line">
              <button
                onClick={handleStartTrading}
                className={`px-2.5 py-1 rounded-md text-xs font-semibold transition flex items-center gap-1 ${
                  isTradingActive
                    ? 'bg-emerald-600 text-white shadow-sm'
                    : 'text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/15'
                }`}
                title="验证执行端状态并启动毫秒级自动狙击交易"
              >
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>开始交易</span>
              </button>
              <button
                onClick={handleStopTrading}
                className={`px-2.5 py-1 rounded-md text-xs font-semibold transition flex items-center gap-1 ${
                  !isTradingActive
                    ? 'bg-slate-600 text-white shadow-sm'
                    : 'text-rose-600 dark:text-rose-400 hover:bg-rose-500/15'
                }`}
                title="停止发送新订单，仅保留实时行情监控"
              >
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
                <span>停止交易</span>
              </button>
            </div>

            {/* Mode Switcher */}
            <div className="flex items-center rounded-lg surface-subtle p-0.5">
              <button
                onClick={() => handleModeToggle('SHADOW')}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                  !isLiveMode ? 'bg-sky-600 text-white shadow-sm' : 'text-dim hover:text-main'
                }`}
              >
                影子模拟盘
              </button>
              <button
                onClick={() => handleModeToggle('LIVE')}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                  isLiveMode ? 'bg-emerald-600 text-white shadow-sm' : 'text-dim hover:text-main'
                }`}
              >
                实盘通道
              </button>
            </div>

            {/* Light / Dark Mode Switcher */}
            <button
              onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg surface-subtle hover:opacity-85 text-main font-medium transition"
              title="切换明亮模式与深色模式"
            >
              {theme === 'light' ? (
                <>
                  <Moon className="w-3.5 h-3.5 text-slate-600" strokeWidth={1.75} />
                  <span>深色模式</span>
                </>
              ) : (
                <>
                  <Sun className="w-3.5 h-3.5 text-amber-400" strokeWidth={1.75} />
                  <span>明亮模式</span>
                </>
              )}
            </button>

            {/* Global Kill Switch */}
            <button
              onClick={handleKillSwitch}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition ${
                isKillActive
                  ? 'bg-emerald-600 hover:bg-emerald-500 border-emerald-500 text-white'
                  : 'bg-rose-600 hover:bg-rose-500 border-rose-500 text-white'
              }`}
            >
              <Power className="w-3.5 h-3.5" strokeWidth={2} />
              {isKillActive ? '解除熔断恢复监控' : '全局紧急熔断'}
            </button>
          </div>
        </div>

        {/* Navigation & Pulse Simulation Bar */}
        <div className="max-w-[1600px] mx-auto mt-2.5 pt-2 border-t border-line flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            {[
              { id: 'radar', label: '实时脉冲雷达与狙击台', icon: Radar },
              { id: 'watchlist', label: `监控合约与参数矩阵 (${strategies.length})`, icon: Sliders },
              { id: 'exchanges', label: '交易所连接与全局风控', icon: Shield },
              { id: 'analytics', label: '盈亏统计与实盘可靠性诊断', icon: BarChart2 },
            ].map((tab) => {
              const Icon = tab.icon;
              const active = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                    active
                      ? 'bg-sky-500/12 text-sky-600 dark:text-sky-400 border border-sky-500/30 font-semibold'
                      : 'text-dim hover:text-main'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                  {tab.label}
                </button>
              );
            })}
          </div>

          {/* Clean Line-Icon Pulse Test Bar (Disabled in LIVE mode to protect real data integrity) */}
          {isLiveMode ? (
            <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
              <Lock className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>实盘通道已开启数据保护：仅展示真实交易所成交与私有流回报，已隔离模拟盘数据</span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-dim mr-1">模拟盘脉冲注入 ({selectedSymbol.split('/')[0]}):</span>
              <button
                onClick={() => handleInjectPulse('LONG', 0.0085, 'CONVERGE_TP', '多头拉升收敛止盈')}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25 font-medium transition"
              >
                <ArrowUpRight className="w-3.5 h-3.5" strokeWidth={1.75} />
                拉升止盈测试
              </button>

              <button
                onClick={() => handleInjectPulse('SHORT', 0.0085, 'CONVERGE_TP', '空头下挫收敛止盈')}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/25 font-medium transition"
              >
                <ArrowDownRight className="w-3.5 h-3.5" strokeWidth={1.75} />
                下挫止盈测试
              </button>

              <button
                onClick={() => handleInjectPulse('LONG', 0.0082, 'REVERSAL_SL', '主所假突破反转止损')}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/25 font-medium transition"
              >
                <RefreshCw className="w-3 h-3" strokeWidth={1.75} />
                假突破反转止损
              </button>

              <button
                onClick={() => handleInjectPulse('LONG', 0.008, 'MM_WITHDRAW_MISS', '做市商抢先撤单未成交')}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md surface-subtle hover:opacity-80 text-sub font-medium transition"
              >
                <Activity className="w-3.5 h-3.5" strokeWidth={1.75} />
                撤单未成测试
              </button>
            </div>
          )}
        </div>
      </header>

      {/* ==================== MAIN WORKSPACE ==================== */}
      <main className="flex-1 max-w-[1600px] w-full mx-auto p-4 space-y-4">
        {/* TRADING EXECUTION & LIVE CHANNEL STATUS BANNER */}
        <div
          className={`surface-card rounded-xl p-3.5 border-l-4 flex flex-wrap items-center justify-between gap-3 ${
            isTradingActive
              ? 'border-l-emerald-500'
              : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
              ? 'border-l-rose-500'
              : 'border-l-amber-500'
          }`}
        >
          <div className="flex items-center gap-3">
            <div
              className={`p-2 rounded-lg ${
                isTradingActive
                  ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                  : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                  ? 'bg-rose-500/12 text-rose-600 dark:text-rose-400'
                  : 'bg-amber-500/12 text-amber-600 dark:text-amber-400'
              }`}
            >
              <Shield className="w-5 h-5" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-xs font-semibold text-main flex flex-wrap items-center gap-2">
                <span>
                  {isLiveMode ? '实盘交易通道运行控制台' : '影子模拟盘交易控制台'} —{' '}
                  {tradingControl.status_label || (isTradingActive ? '自动交易运行中' : '已停止待命')}
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${
                    isTradingActive
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                      : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                      ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30'
                      : 'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30'
                  }`}
                >
                  {isTradingActive ? '正在自动监控脉冲并执行抢单' : '当前仅监听行情 · 不发送新订单'}
                </span>
                {isLiveMode && (
                  <span className="px-2 py-0.5 rounded text-[11px] bg-sky-500/12 text-sky-600 dark:text-sky-400 border border-sky-500/25">
                    真实 Binance & OKX 盘口价格已接入
                  </span>
                )}
              </div>
              <p className="text-xs text-dim mt-1">
                {isLiveMode ? (
                  <>
                    主信号源: <strong className="text-main">{liveStatus.leader_exchange_name}</strong> (
                    {liveStatus.binance_ws_connected ? '真实盘口已连接' : '重连中'}) · 执行所:{' '}
                    <strong className="text-main">{liveStatus.follower_exchange_name}</strong> (
                    {liveStatus.okx_ws_connected ? '真实盘口已连接' : '重连中'}) · API 认证状态:{' '}
                    <strong
                      className={
                        liveStatus.follower_auth_status === 'VERIFIED'
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : liveStatus.follower_auth_status === 'UNCONFIGURED'
                          ? 'text-amber-600 dark:text-amber-400'
                          : 'text-rose-600 dark:text-rose-400'
                      }
                    >
                      {liveStatus.follower_auth_message || '未配置 API 密钥'}
                    </strong>
                  </>
                ) : (
                  <span>
                    {tradingControl.status_detail || '正在使用高频脉冲仿真流进行策略模拟撮合'}
                  </span>
                )}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {!isTradingActive ? (
              <button
                onClick={handleStartTrading}
                className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition flex items-center gap-1.5 shadow-sm"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>{isLiveMode ? '开始实盘自动交易' : '开始自动交易'}</span>
              </button>
            ) : (
              <button
                onClick={handleStopTrading}
                className="px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold transition flex items-center gap-1.5 shadow-sm"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
                <span>停止自动交易</span>
              </button>
            )}

            {isLiveMode && (
              <button
                onClick={() => setActiveTab('exchanges')}
                className="px-3 py-1.5 rounded-lg surface-subtle hover:opacity-85 text-main border border-line text-xs font-medium transition"
              >
                {liveStatus.follower_auth_status === 'VERIFIED' ? '查看/修改 API 配置' : '前往配置/修复执行端 API'}
              </button>
            )}
          </div>
        </div>

        {/* Top 6 KPI Summary Cards (Mode-Isolated) */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">
              {isLiveMode ? '实盘累计净收益 (USDT)' : '模拟盘累计净收益 (USDT)'}
            </div>
            <div
              className={`text-lg font-bold font-mono mt-1 ${
                (stats.total_net_pnl ?? 0) >= 0
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-600 dark:text-rose-400'
              }`}
            >
              {(stats.total_net_pnl ?? 0) >= 0 ? '+' : ''}
              {(stats.total_net_pnl ?? 0).toFixed(4)} USDT
            </div>
            <div className="text-[11px] text-dim font-mono mt-0.5">
              毛利 {(stats.total_gross_pnl ?? 0).toFixed(3)} · 手续费 {(stats.total_fees ?? 0).toFixed(3)}
            </div>
          </div>

          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">限价 IOC 抢单成交率</div>
            <div className="text-lg font-bold font-mono text-sky-600 dark:text-sky-400 mt-1">
              {stats.fill_rate_pct ?? 0}%
            </div>
            <div className="text-[11px] text-dim font-mono mt-0.5">
              成交 {stats.ioc_filled_count ?? 0} 笔 · 撤单 {stats.ioc_missed_count ?? 0} 笔
            </div>
          </div>

          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">平仓闭环胜率</div>
            <div className="text-lg font-bold font-mono text-main mt-1">{stats.win_rate_pct ?? 0}%</div>
            <div className="text-[11px] text-dim font-mono mt-0.5">
              盈利 {stats.win_count ?? 0} 笔 · 亏损 {stats.loss_count ?? 0} 笔
            </div>
          </div>

          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">累计盈亏比 (盈利/亏损)</div>
            <div className="text-lg font-bold font-mono text-main mt-1">{stats.profit_factor ?? 0}</div>
            <div className="text-[11px] text-dim font-mono mt-0.5">
              累计触发信号 {stats.total_signals ?? 0} 次
            </div>
          </div>

          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">被动挂单止盈占比</div>
            <div className="text-lg font-bold font-mono text-amber-600 dark:text-amber-400 mt-1">
              {stats.maker_tp_count ?? 0} / {(stats.maker_tp_count ?? 0) + (stats.taker_exit_count ?? 0)} 笔
            </div>
            <div className="text-[11px] text-dim mt-0.5">开仓即挂 Post-Only 限价止盈</div>
          </div>

          <div className="surface-card rounded-xl p-3.5">
            <div className="text-xs text-dim">在途敞口与连续止损监控</div>
            <div className="text-lg font-bold font-mono text-main mt-1">
              {(snapshot.active_positions || []).length} / {snapshot.risk?.max_concurrent_positions ?? 2} 仓
            </div>
            <div className="text-[11px] text-dim font-mono mt-0.5">
              连续亏损 {stats.consecutive_losses ?? 0} / {snapshot.risk?.max_consecutive_losses ?? 5} 笔
            </div>
          </div>
        </div>

        {/* ==================== VIEW 1: LIVE RADAR DASHBOARD ==================== */}
        {activeTab === 'radar' && (
          <div className="space-y-4">
            {/* Monitored Contracts Strip */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
              {strategies.map((s) => {
                const t = snapshot.telemetry?.[s.symbol] || {};
                const isSelected = s.symbol === selectedSymbol;
                const stateKey = t.state || 'IDLE';
                const prec = s.price_precision ?? 4;
                const pushGapMs = t.follower_age_ms ?? 15;
                const isStale = pushGapMs > (s.max_book_staleness_ms || 600);

                return (
                  <div
                    key={s.symbol}
                    onClick={() => setSelectedSymbol(s.symbol)}
                    className={`cursor-pointer rounded-xl p-3.5 transition-all surface-card ${
                      isSelected ? 'ring-2 ring-sky-500/70' : 'hover:opacity-95'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2.5">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-sm text-main">{s.symbol}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded surface-subtle text-sub">
                          {DIRECTION_LABEL_MAP[s.direction] || s.direction}
                        </span>
                      </div>
                      <span
                        className={`px-2 py-0.5 rounded text-[11px] font-medium border ${
                          stateKey === 'IDLE'
                            ? 'surface-subtle text-dim'
                            : stateKey === 'HOLDING'
                            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                            : 'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30'
                        }`}
                      >
                        {STATE_LABEL_MAP[stateKey] || stateKey}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                      <div>
                        <div className="text-[10px] text-dim font-sans">主所中枢价 (Binance)</div>
                        <div className="text-amber-600 dark:text-amber-400 font-semibold mt-0.5">
                          {t.leader_mid ? Number(t.leader_mid).toFixed(prec) : '--'}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-dim font-sans">从所买一 / 卖一 (OKX)</div>
                        <div className="text-sky-600 dark:text-sky-400 font-semibold mt-0.5">
                          {t.follower_bid ? Number(t.follower_bid).toFixed(prec) : '--'} /{' '}
                          {t.follower_ask ? Number(t.follower_ask).toFixed(prec) : '--'}
                        </div>
                      </div>
                    </div>

                    <div className="mt-2.5 pt-2 border-t border-line flex items-center justify-between text-[11px] font-mono text-dim">
                      <span title="过去30秒主从交易所滚动对数基差">常驻基差: {t.basis_bps ?? 0} 基点</span>
                      <span title="从所相对主所脉冲的跟随收敛系数">收敛系数 β: {t.estimated_beta ?? s.beta_coeff}</span>
                      <span
                        title="距离上一次收到从所盘口推送的时间间隔（用于检测断流或卡顿，超过600ms自动拒单保护）"
                        className={
                          isStale
                            ? 'text-rose-500 font-semibold'
                            : 'text-emerald-600 dark:text-emerald-400'
                        }
                      >
                        行情延迟: {pushGapMs}ms ({isStale ? '超时' : '实时'})
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* 8-Stage State Machine Pipeline */}
            <div className="surface-card rounded-xl p-4">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-sky-500" strokeWidth={1.75} />
                  <span className="text-xs font-semibold text-main">
                    毫秒级持仓状态机拓扑 ({selectedSymbol}) — 同步互斥加锁 · 保留价限价单 · 事件驱动唤醒
                  </span>
                </div>
                <span className="text-xs text-dim font-mono">
                  开仓门槛: 预期净收益率 ≥ {((currentStrategy.take_profit_pct ?? 0.0025) * 100).toFixed(2)}% · 信号有效窗口 &lt;{' '}
                  {currentStrategy.max_signal_age_ms ?? 80}ms
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
                {STATE_PIPELINE.map((st) => {
                  const activeState = currentTelemetry.state || 'IDLE';
                  const isCurrent = activeState === st.key;
                  return (
                    <div
                      key={st.key}
                      className={`px-3 py-2 rounded-lg border transition-all ${
                        isCurrent
                          ? 'bg-sky-500/12 border-sky-500 text-sky-600 dark:text-sky-300 shadow-sm'
                          : 'surface-subtle text-dim'
                      }`}
                    >
                      <div className="flex items-center justify-between text-[10px] font-mono">
                        <span>阶段 {st.step}</span>
                        {isCurrent && <span className="w-1.5 h-1.5 rounded-full bg-sky-500 animate-ping" />}
                      </div>
                      <div className="text-xs font-semibold text-main mt-0.5">{st.label}</div>
                      <div className="text-[10px] text-dim truncate">{st.desc}</div>
                    </div>
                  );
                })}
              </div>

              {/* Active Position Status Bar */}
              {(snapshot.active_positions || []).length > 0 && (
                <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-center justify-between gap-3 bg-emerald-500/10 px-3.5 py-2 rounded-lg border border-emerald-500/25">
                  {snapshot.active_positions.map((pos) => (
                    <div key={pos.symbol} className="flex flex-wrap items-center gap-4 text-xs font-mono">
                      <span className="px-2 py-0.5 rounded bg-emerald-600 text-white font-semibold font-sans">
                        持仓监控中: {pos.symbol} ({pos.side === 'LONG' ? '多头' : '空头'})
                      </span>
                      <span className="text-main">成交均价: {pos.entry_price}</span>
                      <span className="text-emerald-600 dark:text-emerald-400">被动止盈挂单价: {pos.tp_price}</span>
                      <span className="text-rose-600 dark:text-rose-400">条件止损触发价: {pos.sl_price}</span>
                      <span className="text-main">
                        浮动净收益率:{' '}
                        <strong
                          className={
                            pos.unrealized_net_pct >= 0
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : 'text-rose-600 dark:text-rose-400'
                          }
                        >
                          {(pos.unrealized_net_pct * 100).toFixed(3)}%
                        </strong>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Chart + Real-Time Terminal Log */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
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
              <div className="lg:col-span-5 surface-card rounded-xl p-4 flex flex-col h-[406px]">
                <div className="flex items-center justify-between pb-2.5 mb-2.5 border-b border-line">
                  <div className="flex items-center gap-2">
                    <Terminal className="w-4 h-4 text-sky-500" strokeWidth={1.75} />
                    <h3 className="text-sm font-semibold text-main">
                      {isLiveMode ? '实盘通道执行与风控日志' : '模拟盘毫秒级狙击日志'}
                    </h3>
                  </div>
                  <div className="flex items-center gap-1 text-xs">
                    {[
                      { id: 'ALL', label: '全部记录' },
                      { id: 'SIGNAL', label: '成交闭环' },
                      { id: 'MISS', label: '未成撤单' },
                      { id: 'RISK', label: '风控告警' },
                    ].map((f) => (
                      <button
                        key={f.id}
                        onClick={() => setLogFilter(f.id)}
                        className={`px-2 py-0.5 rounded transition ${
                          logFilter === f.id
                            ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400 font-semibold border border-sky-500/30'
                            : 'text-dim hover:text-main'
                        }`}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto space-y-1.5 font-mono text-[11px] pr-1">
                  {filteredLogs.length === 0 ? (
                    <div className="text-dim text-center py-14 font-sans">
                      {isLiveMode
                        ? '当前实盘通道暂无触发记录（配置执行所 API Key 后将自动执行实盘抢单）'
                        : '暂无匹配日志，可等待行情触发或点击顶部「模拟盘脉冲注入」按钮'}
                    </div>
                  ) : (
                    filteredLogs.map((log) => {
                      let badgeCls = 'surface-subtle text-sub';
                      if (log.level === 'SIGNAL')
                        badgeCls = 'bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-300';
                      else if (log.level === 'FILL')
                        badgeCls = 'bg-sky-500/10 border border-sky-500/30 text-sky-700 dark:text-sky-300';
                      else if (log.level === 'EXIT_WIN')
                        badgeCls =
                          'bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300';
                      else if (log.level === 'EXIT_LOSS' || log.level === 'CRITICAL')
                        badgeCls = 'bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300';

                      return (
                        <div key={log.id} className={`px-2.5 py-1.5 rounded-md leading-relaxed ${badgeCls}`}>
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

        {/* ==================== VIEW 2: WATCHLIST & STRATEGY CONFIG MATRIX ==================== */}
        {activeTab === 'watchlist' && (
          <div className="space-y-4">
            {/* Upgraded Search, Sector Filter & Instant Custom Add Panel */}
            <div className="surface-card rounded-xl p-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-main">
                    自选监控合约库与自定义搜索添加 (内置 {catalog.length} 个永续合约 + 支持任意币种输入)
                  </h2>
                  <p className="text-xs text-dim mt-0.5">
                    输入币种简称（如 SUI、ARB、WIF、ORDI、ONDO、TRUMP）回车即可一键校验主从所精度并零延迟启动 Tick 监控
                  </p>
                </div>

                {/* Search Input + Instant Add Button */}
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <Search className="w-3.5 h-3.5 text-dim absolute left-3 top-1/2 -translate-y-1/2" strokeWidth={1.75} />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleAddSymbolByCode(searchQuery);
                      }}
                      placeholder="搜索或输入任意币种 (如 ARB / WIF / KAITO)"
                      className="surface-input rounded-lg pl-8 pr-3 py-1.5 text-xs font-mono w-72"
                    />
                  </div>
                  <button
                    onClick={() => handleAddSymbolByCode(searchQuery)}
                    className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
                  >
                    <Plus className="w-3.5 h-3.5" strokeWidth={2} />
                    立即添加并激活监控
                  </button>
                </div>
              </div>

              {/* Sector Filter Tabs */}
              <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-line text-xs">
                <span className="text-dim mr-1">合约板块筛选:</span>
                {[
                  { id: 'ALL', label: '全部热门合约' },
                  { id: 'AI与DeFi中小盘', label: 'AI与DeFi中小盘 (推荐：滞后空间大)' },
                  { id: '高弹性Meme与中小市值', label: '高弹性Meme与中小市值 (推荐)' },
                  { id: '公链生态', label: '主流公链与Layer2' },
                  { id: '主流龙头', label: '顶级主流币 (做市商竞争激烈)' },
                ].map((sec) => (
                  <button
                    key={sec.id}
                    onClick={() => setSelectedSector(sec.id)}
                    className={`px-2.5 py-1 rounded-md transition ${
                      selectedSector === sec.id
                        ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400 font-semibold border border-sky-500/30'
                        : 'surface-subtle text-sub hover:text-main'
                    }`}
                  >
                    {sec.label}
                  </button>
                ))}
              </div>

              {/* One-Click Add Contract Chips */}
              <div className="flex flex-wrap items-center gap-1.5 max-h-28 overflow-y-auto pt-1">
                {filteredCatalog.slice(0, 28).map((item) => (
                  <button
                    key={item.symbol}
                    onClick={() => handleAddSymbolByCode(item.symbol)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md surface-subtle hover:border-sky-500/50 text-xs font-mono transition group"
                    title={`点击立即添加 ${item.symbol} (参考价: ${item.ref_price})`}
                  >
                    <Plus className="w-3 h-3 text-sky-500 group-hover:scale-110 transition" strokeWidth={2} />
                    <span className="font-semibold text-main">{item.base}</span>
                    <span className="text-[10px] text-dim">{item.ref_price}</span>
                  </button>
                ))}
                {searchQuery.trim() && filteredCatalog.length === 0 && (
                  <button
                    onClick={() => handleAddSymbolByCode(searchQuery)}
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-sky-500/10 border border-sky-500/30 text-sky-600 dark:text-sky-400 text-xs font-mono"
                  >
                    <Plus className="w-3.5 h-3.5" strokeWidth={2} />
                    直接创建并添加自定义永续合约: {searchQuery.toUpperCase()}
                  </button>
                )}
              </div>
            </div>

            {/* Strategy Cards Matrix */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {strategies.map((cfg, idx) => (
                <div key={cfg.symbol} className="surface-card rounded-xl p-4 space-y-3.5">
                  <div className="flex items-center justify-between border-b border-line pb-3">
                    <div className="flex items-center gap-3">
                      <span className="text-base font-bold font-mono text-main">{cfg.symbol}</span>
                      <select
                        value={cfg.direction}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, direction: e.target.value };
                          setStrategies(next);
                        }}
                        className="surface-input rounded-md px-2.5 py-1 text-xs"
                      >
                        <option value="BOTH">多空双向狙击</option>
                        <option value="LONG_ONLY">仅限做多</option>
                        <option value="SHORT_ONLY">仅限做空</option>
                      </select>

                      <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                        <input
                          type="checkbox"
                          checked={cfg.is_running}
                          onChange={(e) => {
                            const next = [...strategies];
                            next[idx] = { ...cfg, is_running: e.target.checked, auto_disabled_reason: null };
                            setStrategies(next);
                          }}
                          className="accent-emerald-600"
                        />
                        <span
                          className={
                            cfg.is_running ? 'text-emerald-600 dark:text-emerald-400 font-semibold' : 'text-dim'
                          }
                        >
                          {cfg.is_running ? '监控运行中' : '已暂停'}
                        </span>
                      </label>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleSaveSymbol(cfg)}
                        className="px-3 py-1 rounded-md bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
                      >
                        保存参数
                      </button>
                      <button
                        onClick={() => handleDeleteSymbol(cfg.symbol)}
                        className="p-1.5 rounded-md bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/25 transition"
                        title="移除监控合约"
                      >
                        <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                      </button>
                    </div>
                  </div>

                  {cfg.auto_disabled_reason && (
                    <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-300 text-xs flex items-center gap-2">
                      <AlertCircle className="w-4 h-4 shrink-0" strokeWidth={1.75} />
                      <span>{cfg.auto_disabled_reason}</span>
                    </div>
                  )}

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    <div>
                      <label className="text-[11px] text-dim block mb-1">单笔名义金额 (USDT)</label>
                      <input
                        type="number"
                        value={cfg.order_usdt}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, order_usdt: Number(e.target.value) };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">合约杠杆倍数</label>
                      <input
                        type="number"
                        value={cfg.leverage}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, leverage: Number(e.target.value) };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">脉冲检测窗口 (毫秒)</label>
                      <input
                        type="number"
                        value={cfg.window_ms}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, window_ms: Number(e.target.value) };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">最小脉冲幅度 (%)</label>
                      <input
                        type="number"
                        step="0.05"
                        value={(cfg.min_jump_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, min_jump_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">扣费目标净止盈 (%)</label>
                      <input
                        type="number"
                        step="0.05"
                        value={(cfg.take_profit_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, take_profit_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">最大硬性止损 (%)</label>
                      <input
                        type="number"
                        step="0.05"
                        value={(cfg.stop_loss_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, stop_loss_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">最长持仓限时 (毫秒)</label>
                      <input
                        type="number"
                        step="100"
                        value={cfg.max_hold_ms}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, max_hold_ms: Number(e.target.value) };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">IOC 容忍滑点上限 (%)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={(cfg.slippage_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, slippage_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">从所滞后确认上限 (%)</label>
                      <input
                        type="number"
                        step="0.02"
                        value={(cfg.lag_confirm_max_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, lag_confirm_max_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">主所回撤反转止损比 (%)</label>
                      <input
                        type="number"
                        step="5"
                        value={(cfg.reversal_stop_ratio * 100).toFixed(0)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, reversal_stop_ratio: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">移动追踪回撤阈值 (%)</label>
                      <input
                        type="number"
                        step="0.02"
                        value={(cfg.trailing_retracement_pct * 100).toFixed(2)}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, trailing_retracement_pct: Number(e.target.value) / 100 };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-dim block mb-1">基准收敛系数 β</label>
                      <input
                        type="number"
                        step="0.02"
                        value={cfg.beta_coeff}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, beta_coeff: Number(e.target.value) };
                          setStrategies(next);
                        }}
                        className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                      />
                    </div>
                  </div>

                  <div className="pt-2.5 border-t border-line flex flex-wrap items-center gap-5 text-xs text-sub">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={cfg.use_post_only_tp}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, use_post_only_tp: e.target.checked };
                          setStrategies(next);
                        }}
                        className="accent-sky-600"
                      />
                      <span>开仓即挂被动限价止盈单 (Post-Only)</span>
                    </label>

                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={cfg.trailing_stop_enabled}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, trailing_stop_enabled: e.target.checked };
                          setStrategies(next);
                        }}
                        className="accent-sky-600"
                      />
                      <span>启用移动追踪止盈</span>
                    </label>

                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={cfg.require_volume_confirm}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, require_volume_confirm: e.target.checked };
                          setStrategies(next);
                        }}
                        className="accent-sky-600"
                      />
                      <span>微观价格与主动成交量同向确认</span>
                    </label>

                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={cfg.auto_beta_enabled}
                        onChange={(e) => {
                          const next = [...strategies];
                          next[idx] = { ...cfg, auto_beta_enabled: e.target.checked };
                          setStrategies(next);
                        }}
                        className="accent-sky-600"
                      />
                      <span>滚动回归动态更新收敛系数 β</span>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ==================== VIEW 3: EXCHANGE CONNECTOR & RISK CONTROL ==================== */}
        {activeTab === 'exchanges' && (
          <div className="space-y-4">
            <div className="surface-card rounded-xl p-4">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                <div>
                  <h2 className="text-sm font-semibold text-main">交易所连接配置与费率管理中心</h2>
                  <p className="text-xs text-dim mt-0.5">
                    自由指定主信号源 (Leader) 与实盘执行端 (Follower)，支持自动同步账户 VIP 等级费率及手动覆盖
                  </p>
                </div>
                <span className="px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/25 text-emerald-600 dark:text-emerald-400 text-xs font-medium flex items-center gap-1.5">
                  <Lock className="w-3.5 h-3.5" strokeWidth={1.75} />
                  API 密钥本地 AES-256-GCM 加密存储
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {exchanges.map((ex, idx) => {
                  const health = snapshot.exchange_health?.[ex.exchange_id] || {};
                  const authStatus = ex.auth_status || 'UNCONFIGURED';
                  const isVerified = authStatus === 'VERIFIED';
                  const isError = authStatus === 'ERROR' || authStatus === 'CONFIG_INCOMPLETE';
                  const needsPass = ex.exchange_id === 'okx' || ex.exchange_id === 'bitget';

                  return (
                    <div
                      key={ex.exchange_id}
                      className={`rounded-xl p-4 border transition surface-subtle ${
                        isVerified
                          ? 'border-emerald-500/40'
                          : isError
                          ? 'border-rose-500/45'
                          : ex.is_active
                          ? 'border-line'
                          : 'opacity-70'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2.5">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-sm text-main">{ex.display_name}</span>
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                                isVerified
                                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                                  : isError
                                  ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30'
                                  : 'surface-card text-dim border-line'
                              }`}
                            >
                              {isVerified ? '已配置 · 验证通过' : isError ? '配置错误 / 未完整' : '未配置密钥'}
                            </span>
                          </div>
                          <div className="text-[11px] font-mono text-dim mt-0.5">
                            订阅流: {health.mode || ex.stream_mode || 'BBO-TBT'} · 网络延迟:{' '}
                            <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                              {health.ping_ms ?? ex.ping_ms} ms
                            </span>
                          </div>
                        </div>

                        <select
                          value={ex.role}
                          onChange={(e) => {
                            const next = [...exchanges];
                            next[idx] = { ...ex, role: e.target.value };
                            setExchanges(next);
                          }}
                          className={`px-2 py-1 rounded text-xs font-semibold border ${
                            ex.role === 'leader'
                              ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
                              : 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30'
                          }`}
                        >
                          <option value="leader">主所 (信号源)</option>
                          <option value="follower">从所 (执行端)</option>
                        </select>
                      </div>

                      {/* Real-Time API Verification Status Alert Box */}
                      <div
                        className={`mb-3 px-2.5 py-2 rounded-lg border text-[11px] flex items-start gap-2 ${
                          isVerified
                            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300'
                            : isError
                            ? 'bg-rose-500/10 border-rose-500/35 text-rose-700 dark:text-rose-300'
                            : 'surface-card border-line text-dim'
                        }`}
                      >
                        {isVerified ? (
                          <Check className="w-3.5 h-3.5 shrink-0 mt-0.5 text-emerald-500" strokeWidth={2} />
                        ) : isError ? (
                          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-rose-500" strokeWidth={2} />
                        ) : (
                          <Lock className="w-3.5 h-3.5 shrink-0 mt-0.5 text-dim" strokeWidth={1.75} />
                        )}
                        <div className="leading-snug">
                          <div className="font-medium">{ex.auth_message || '未配置 API 密钥'}</div>
                          {ex.last_verified_at && (
                            <div className="text-[10px] opacity-80 font-mono mt-0.5">
                              最近校验时间: {ex.last_verified_at}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="space-y-2.5 text-xs">
                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-[11px] text-dim block mb-0.5">吃单费率 Taker (%)</label>
                            <input
                              type="number"
                              step="0.005"
                              value={(ex.taker_fee * 100).toFixed(3)}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, taker_fee: Number(e.target.value) / 100 };
                                setExchanges(next);
                              }}
                              className="w-full surface-input rounded px-2.5 py-1 font-mono"
                            />
                          </div>
                          <div>
                            <label className="text-[11px] text-dim block mb-0.5">挂单费率 Maker (%)</label>
                            <input
                              type="number"
                              step="0.005"
                              value={(ex.maker_fee * 100).toFixed(3)}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, maker_fee: Number(e.target.value) / 100 };
                                setExchanges(next);
                              }}
                              className="w-full surface-input rounded px-2.5 py-1 font-mono"
                            />
                          </div>
                        </div>

                        <div>
                          <div className="flex items-center justify-between mb-0.5">
                            <label className="text-[11px] text-dim">API Key</label>
                            <span
                              className={`text-[10px] font-mono ${
                                ex.has_api_key
                                  ? 'text-emerald-600 dark:text-emerald-400 font-semibold'
                                  : isError
                                  ? 'text-rose-600 dark:text-rose-400 font-semibold'
                                  : 'text-dim'
                              }`}
                            >
                              {ex.has_api_key ? `已保存 (${ex.api_key_masked})` : '未填写 (必填)'}
                            </span>
                          </div>
                          <input
                            type="text"
                            value={ex.api_key || ''}
                            placeholder={ex.has_api_key ? `已存: ${ex.api_key_masked} (输入新值可覆盖)` : '请粘贴完整的 API Key'}
                            onChange={(e) => {
                              const next = [...exchanges];
                              next[idx] = { ...ex, api_key: e.target.value };
                              setExchanges(next);
                            }}
                            className="w-full surface-input rounded px-2.5 py-1 font-mono"
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <div className="flex items-center justify-between mb-0.5">
                              <label className="text-[11px] text-dim">API Secret</label>
                              <span
                                className={`text-[10px] font-mono ${
                                  ex.has_api_secret
                                    ? 'text-emerald-600 dark:text-emerald-400 font-semibold'
                                    : isError
                                    ? 'text-rose-600 dark:text-rose-400 font-semibold'
                                    : 'text-dim'
                                }`}
                              >
                                {ex.has_api_secret ? '已加密保存' : '未填写'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.api_secret || ''}
                              placeholder={ex.has_api_secret ? '已加密保存 (留空保持不变)' : '请输入 Secret'}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, api_secret: e.target.value };
                                setExchanges(next);
                              }}
                              className="w-full surface-input rounded px-2.5 py-1 font-mono"
                            />
                          </div>
                          <div>
                            <div className="flex items-center justify-between mb-0.5">
                              <label className="text-[11px] text-dim">API 口令 (Passphrase)</label>
                              <span
                                className={`text-[10px] font-mono ${
                                  ex.has_passphrase
                                    ? 'text-emerald-600 dark:text-emerald-400 font-semibold'
                                    : needsPass && isError
                                    ? 'text-rose-600 dark:text-rose-400 font-semibold'
                                    : 'text-dim'
                                }`}
                              >
                                {ex.has_passphrase ? '已加密保存' : needsPass ? '未填写(必填)' : '选填'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.passphrase || ''}
                              placeholder={ex.has_passphrase ? '已加密保存 (留空保持不变)' : 'OKX/Bitget API口令'}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, passphrase: e.target.value };
                                setExchanges(next);
                              }}
                              className="w-full surface-input rounded px-2.5 py-1 font-mono"
                            />
                          </div>
                        </div>

                        <div className="pt-2 flex flex-wrap items-center justify-between gap-2">
                          <label className="flex items-center gap-1.5 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={ex.is_active}
                              onChange={(e) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, is_active: e.target.checked };
                                setExchanges(next);
                              }}
                              className="accent-sky-600"
                            />
                            <span className="text-sub">启用该交易所</span>
                          </label>

                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => handleAutoFetchVipFee(ex, 'VIP 1')}
                              className="px-2 py-1 rounded surface-card hover:opacity-80 text-sub text-xs font-medium border border-line"
                            >
                              同步费率
                            </button>
                            <button
                              onClick={() => handleVerifyExchange(ex.exchange_id, ex.display_name)}
                              className="px-2.5 py-1 rounded surface-card hover:opacity-85 text-sky-600 dark:text-sky-400 border border-sky-500/30 font-medium text-xs"
                            >
                              测试连接
                            </button>
                            <button
                              onClick={() => handleSaveExchange(ex)}
                              className="px-3 py-1 rounded bg-sky-600 hover:bg-sky-500 text-white font-semibold text-xs shadow-sm"
                            >
                              验证并保存
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {riskForm && (
              <div className="surface-card rounded-xl p-4">
                <div className="flex items-center justify-between mb-3.5">
                  <div>
                    <h3 className="text-sm font-semibold text-main">全局风控阈值与高保真影子盘参数</h3>
                    <p className="text-xs text-dim mt-0.5">
                      包含单日亏损熔断、连续亏损熔断、并发持仓上限、延迟熔断与对数正态网络延迟仿真参数
                    </p>
                  </div>
                  <button
                    onClick={handleSaveRisk}
                    className="px-4 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition"
                  >
                    保存全局风控配置
                  </button>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 text-xs">
                  <div>
                    <label className="text-[11px] text-dim block mb-1">单日最大亏损 (USDT)</label>
                    <input
                      type="number"
                      value={riskForm.max_daily_loss_usdt}
                      onChange={(e) => setRiskForm({ ...riskForm, max_daily_loss_usdt: Number(e.target.value) })}
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">连续亏损熔断 (笔)</label>
                    <input
                      type="number"
                      value={riskForm.max_consecutive_losses}
                      onChange={(e) => setRiskForm({ ...riskForm, max_consecutive_losses: Number(e.target.value) })}
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">最大并发持仓数</label>
                    <input
                      type="number"
                      value={riskForm.max_concurrent_positions}
                      onChange={(e) => setRiskForm({ ...riskForm, max_concurrent_positions: Number(e.target.value) })}
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">最大名义总敞口 (U)</label>
                    <input
                      type="number"
                      value={riskForm.max_exposure_usdt}
                      onChange={(e) => setRiskForm({ ...riskForm, max_exposure_usdt: Number(e.target.value) })}
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">报单延迟熔断线 (ms)</label>
                    <input
                      type="number"
                      value={riskForm.latency_circuit_breaker_ms}
                      onChange={(e) =>
                        setRiskForm({ ...riskForm, latency_circuit_breaker_ms: Number(e.target.value) })
                      }
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">影子盘基准延迟 (ms)</label>
                    <input
                      type="number"
                      value={riskForm.shadow_base_latency_ms}
                      onChange={(e) => setRiskForm({ ...riskForm, shadow_base_latency_ms: Number(e.target.value) })}
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">做市商撤单逆向概率</label>
                    <input
                      type="number"
                      step="0.05"
                      value={riskForm.shadow_adverse_selection_prob}
                      onChange={(e) =>
                        setRiskForm({ ...riskForm, shadow_adverse_selection_prob: Number(e.target.value) })
                      }
                      className="w-full surface-input rounded-md px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-dim block mb-1">行情源接入模式</label>
                    <select
                      value={riskForm.feed_source}
                      onChange={(e) => setRiskForm({ ...riskForm, feed_source: e.target.value })}
                      className="w-full surface-input rounded-md px-2 py-1.5"
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
          <div className="space-y-4">
            <EquityChart equityCurve={stats.equity_curve || []} stats={stats} theme={theme} />

            {/* Shadow-to-Live Reality Gap & Decay Diagnostic Panel */}
            <div className="surface-card rounded-xl p-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-line">
                <div className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-amber-500" strokeWidth={1.75} />
                  <h3 className="text-sm font-semibold text-main">
                    模拟盘数据可靠性与实盘衰减量化诊断 (Shadow-to-Live Reality Gap)
                  </h3>
                </div>
                <span className="text-xs font-mono text-dim">
                  基于当前报单延迟 ({snapshot.last_order_rtt_ms}ms) 与双边费率结构实时测算
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
                <div className="surface-subtle rounded-lg p-3">
                  <div className="text-dim">1. 抢单成交率衰减 (同机房竞争)</div>
                  <div className="mt-1.5 flex items-baseline justify-between font-mono">
                    <span className="text-sub">模拟盘: {realityGap.shadow_fill_rate_pct}%</span>
                    <span className="text-amber-600 dark:text-amber-400 font-bold text-sm">
                      实盘预计: ~{realityGap.projected_live_fill_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-1 leading-relaxed">
                    机房托管做市商反应时间 &lt;2ms，普通服务器存在约 {realityGap.colocation_gap_ms}ms 物理延迟劣势，真实滞后挂单大部分会被做市商抢先撤单。
                  </p>
                </div>

                <div className="surface-subtle rounded-lg p-3">
                  <div className="text-dim">2. 逆向选择与胜率衰减 (赢家诅咒)</div>
                  <div className="mt-1.5 flex items-baseline justify-between font-mono">
                    <span className="text-sub">模拟盘: {realityGap.shadow_win_rate_pct}%</span>
                    <span className="text-rose-600 dark:text-rose-400 font-bold text-sm">
                      实盘预计: ~{realityGap.projected_live_win_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-1 leading-relaxed">
                    实盘中能让你在 30ms 后依然抢到的挂单，往往是做市商判断主所为假突破而故意留下的对手盘，成交样本天然偏向不利。
                  </p>
                </div>

                <div className="surface-subtle rounded-lg p-3">
                  <div className="text-dim">3. 盈亏平衡胜率红线 (含手续费)</div>
                  <div className="mt-1.5 flex items-baseline justify-between font-mono">
                    <span className="text-sub">Post-Only止盈模式</span>
                    <span className="text-sky-600 dark:text-sky-400 font-bold text-sm">
                      需 ≥ {realityGap.breakeven_win_rate_pct}%
                    </span>
                  </div>
                  <p className="text-[11px] text-dim mt-1 leading-relaxed">
                    目标净止盈 0.25% 对比硬止损 0.35%+双边成本 0.10%，实盘成交后的收敛胜率必须稳定高于 64.3% 才能产生正向净期望。
                  </p>
                </div>

                <div className="surface-subtle rounded-lg p-3">
                  <div className="text-dim">4. 实盘可行性区间结论</div>
                  <div className="mt-1.5 font-semibold text-emerald-600 dark:text-emerald-400">
                    避开 BTC/ETH，聚焦中小盘 + 二线所
                  </div>
                  <p className="text-[11px] text-dim mt-1 leading-relaxed">
                    主流币：{realityGap.mainstream_verdict}；中小盘：{realityGap.altcoin_verdict}。
                  </p>
                </div>
              </div>
            </div>

            {/* Historical Trades Table */}
            <div className="surface-card rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-sm font-semibold text-main">
                    {isLiveMode ? '实盘通道历史成交复盘明细表' : '影子模拟盘历史狙击复盘明细表'}
                  </h3>
                  <p className="text-xs text-dim mt-0.5">
                    由独立后台队列批量写入 SQLite 数据库，模拟盘与实盘通道账本严格分表隔离
                  </p>
                </div>
                <span className="text-xs font-mono text-dim">
                  当前通道最近 {(stats.recent_trades || []).length} 笔闭环交易
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-line text-[11px] text-dim surface-subtle">
                      <th className="py-2.5 px-3 font-medium">触发时间</th>
                      <th className="py-2.5 px-3 font-medium">合约代号</th>
                      <th className="py-2.5 px-3 font-medium">方向</th>
                      <th className="py-2.5 px-3 font-medium">主所脉冲幅度</th>
                      <th className="py-2.5 px-3 font-medium">收敛系数 β</th>
                      <th className="py-2.5 px-3 font-medium">开仓均价</th>
                      <th className="py-2.5 px-3 font-medium">平仓均价</th>
                      <th className="py-2.5 px-3 font-medium">信号至成交</th>
                      <th className="py-2.5 px-3 font-medium">持仓耗时</th>
                      <th className="py-2.5 px-3 font-medium">平仓方式</th>
                      <th className="py-2.5 px-3 font-medium">双边手续费</th>
                      <th className="py-2.5 px-3 font-medium">净收益 (USDT)</th>
                      <th className="py-2.5 px-3 font-medium">平仓归因</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y border-line font-mono">
                    {(stats.recent_trades || []).length === 0 ? (
                      <tr>
                        <td colSpan={13} className="py-12 text-center text-dim font-sans">
                          {isLiveMode
                            ? '当前实盘通道尚无真实成交记录（已完全隔离模拟盘数据）'
                            : '暂无成交记录，请等待脉冲信号或点击顶部「模拟盘脉冲注入」按钮验证全流程'}
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
                            <td className="py-2.5 px-3 text-dim">{tr.time}</td>
                            <td className="py-2.5 px-3 font-semibold text-main">{tr.symbol}</td>
                            <td className="py-2.5 px-3 font-sans">
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                                  tr.side === 'LONG'
                                    ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                                    : 'bg-rose-500/12 text-rose-600 dark:text-rose-400'
                                }`}
                              >
                                {tr.side === 'LONG' ? '做多' : '做空'}
                              </span>
                            </td>
                            <td
                              className={`py-2.5 px-3 font-semibold ${
                                tr.jump_pct >= 0
                                  ? 'text-emerald-600 dark:text-emerald-400'
                                  : 'text-rose-600 dark:text-rose-400'
                              }`}
                            >
                              {tr.jump_pct >= 0 ? `+${tr.jump_pct}%` : `${tr.jump_pct}%`}
                            </td>
                            <td className="py-2.5 px-3 text-sub">{tr.beta_est}</td>
                            <td className="py-2.5 px-3 text-main">{tr.entry}</td>
                            <td className="py-2.5 px-3 text-main">{tr.exit}</td>
                            <td className="py-2.5 px-3 text-sub">{tr.signal_to_fill_ms} ms</td>
                            <td className="py-2.5 px-3 text-sub">{tr.hold_ms} ms</td>
                            <td className="py-2.5 px-3 font-sans">
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] ${
                                  tr.exit_order_type === 'MAKER_POST_ONLY'
                                    ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/25'
                                    : 'surface-subtle text-dim'
                                }`}
                              >
                                {tr.exit_order_type === 'MAKER_POST_ONLY' ? '被动挂单 (Maker)' : '主动吃单 (Taker)'}
                              </span>
                            </td>
                            <td className="py-2.5 px-3 text-dim">{Number(tr.fee).toFixed(4)}</td>
                            <td
                              className={`py-2.5 px-3 font-bold ${
                                tr.net_pnl >= 0
                                  ? 'text-emerald-600 dark:text-emerald-400'
                                  : 'text-rose-600 dark:text-rose-400'
                              }`}
                            >
                              {tr.net_pnl >= 0 ? `+${Number(tr.net_pnl).toFixed(4)}` : Number(tr.net_pnl).toFixed(4)}
                            </td>
                            <td className="py-2.5 px-3 font-sans">
                              <span className={`px-2 py-0.5 rounded border text-[11px] font-medium ${badge.cls}`}>
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
      </main>
    </div>
  );
}
