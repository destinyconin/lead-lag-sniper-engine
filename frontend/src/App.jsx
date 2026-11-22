import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  Check,
  Cpu,
  CreditCard,
  DollarSign,
  Grid,
  Layers,
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
  TrendingUp,
  User,
  Users,
  Wallet,
  Wifi,
  Zap,
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
  EXIT_UNCERTAIN: '平仓核验中',
  CLOSED: '盈亏核算',
  COOLDOWN: '防抖冷却',
  GRID_WATCH: '网格巡航中',
  GRID_HOLDING: '网格持仓中',
};

const STRATEGY_LABEL_MAP = {
  LEAD_LAG: '脉冲狙击',
  LONG_SHORT_GRID: '传统网格',
  REVERSE_GRID: '多空震荡',
  TREND_GRID: '顺势网格',
  ADAPTIVE_GRID: 'VCP双模式',
};

const EXCHANGE_SHORT_LABEL_MAP = {
  okx: 'OKX',
  binanceusdm: '币安',
  bybit: 'Bybit',
  gate: 'Gate.io',
  bitget: 'Bitget',
  krakenfutures: 'Kraken',
  futu: '富途牛牛',
  moomoo: 'moomoo',
  ibkr: 'IBKR盈透',
  schwab: 'Schwab嘉信',
};

const EXCHANGE_SELECT_OPTIONS = [
  { id: 'okx', label: 'OKX Swap (欧易永续)', group: 'crypto' },
  { id: 'binanceusdm', label: 'Binance USDM (币安合约)', group: 'crypto' },
  { id: 'bybit', label: 'Bybit Unified (Bybit永续)', group: 'crypto' },
  { id: 'gate', label: 'Gate.io Futures (芝麻开门)', group: 'crypto' },
  { id: 'bitget', label: 'Bitget Futures (Bitget合约)', group: 'crypto' },
  { id: 'krakenfutures', label: 'Kraken Futures (Kraken合约)', group: 'crypto' },
  { id: 'futu', label: '富途牛牛 (Futu OpenD 证券)', group: 'broker' },
  { id: 'moomoo', label: 'moomoo (Moomoo OpenD 国际)', group: 'broker' },
  { id: 'ibkr', label: 'IBKR (盈透证券 TWS/Web API)', group: 'broker' },
  { id: 'schwab', label: 'Charles Schwab (嘉信理财 API)', group: 'broker' },
];

const TRADITIONAL_BROKER_IDS = new Set(['futu', 'moomoo', 'ibkr', 'schwab']);

const isGridStrategyType = (st) =>
  st === 'LONG_SHORT_GRID' || st === 'REVERSE_GRID' || st === 'TREND_GRID' || st === 'ADAPTIVE_GRID';

const calcPyramidWeights = (count, decayEnabled = true) => {
  const n = Math.max(1, Math.min(30, Math.round(Number(count) || 1)));
  if (!decayEnabled || n === 1) return Array.from({ length: n }, () => 1 / n);
  const presets = {
    2: [0.6, 0.4],
    3: [0.5, 0.3, 0.2],
    4: [0.38, 0.27, 0.2, 0.15],
    5: [0.3, 0.24, 0.19, 0.15, 0.12],
  };
  if (presets[n]) return presets[n];
  const raw = Array.from({ length: n }, (_, i) => 0.65 ** i);
  const total = raw.reduce((a, b) => a + b, 0);
  return raw.map((w) => w / total);
};

const suggestLinkedGridRange = (levels, stepRatio) => {
  const n = Math.max(1, Math.min(30, Math.round(Number(levels) || 3)));
  const step = Math.max(0.0005, Number(stepRatio) || 0.006);
  const span = n * step;
  const buffered = Math.max(span * 1.15, span + 0.005);
  // Round up cleanly to nearest 0.1% (0.001)
  return Math.min(0.8, Math.ceil(buffered * 1000) / 1000);
};

const buildGridPreviewAndDiagnostics = (cfg, okxMinNotional = 0, followerMid = 0) => {
  const n = Math.max(1, Math.min(30, Math.round(Number(cfg.grid_levels ?? 3) || 3)));
  const step = Math.max(0.0001, Number(cfg.grid_step_pct ?? 0.008) || 0.008);
  const tp = Math.max(0.0001, Number(cfg.grid_take_profit_pct ?? 0.0085) || 0.0085);
  const rng = Math.max(0.001, Number(cfg.grid_range_pct ?? 0.028) || 0.028);
  const sl = Math.max(0.002, Number(cfg.grid_stop_loss_pct ?? 0.038) || 0.038);
  const lev = Math.max(1, Number(cfg.leverage ?? 5) || 5);
  const orderUsdt = Math.max(1, Number(cfg.order_usdt ?? 10) || 10);
  const dzMult = Math.max(0.5, Number(cfg.trend_dead_zone_mult ?? 1.25) || 1.25);
  const st = cfg.strategy_type || 'LONG_SHORT_GRID';
  const isTrendLike = st === 'TREND_GRID' || st === 'ADAPTIVE_GRID';
  const decay = Boolean(cfg.trend_pyramid_decay ?? true);
  const usePo = Boolean(cfg.use_post_only_tp ?? true);
  const effRtFee = usePo && st !== 'TREND_GRID' ? 0.0007 : 0.001;

  const totalSpan = n * step;
  const sideBudget = orderUsdt * n;
  const sideMargin = sideBudget / lev;
  const weights = isTrendLike ? calcPyramidWeights(n, decay) : Array.from({ length: n }, () => 1 / n);
  const legs = weights.map((w, idx) => {
    const levelIdx = idx + 1;
    const legUsdt = isTrendLike && decay ? Number((sideBudget * w).toFixed(2)) : Number(orderUsdt.toFixed(2));
    const mrDistPct = levelIdx * step;
    const clippedInMr = st !== 'TREND_GRID' && levelIdx > 1 && mrDistPct > rng + 1e-6;
    const belowMin = okxMinNotional > 0 && legUsdt < okxMinNotional * 1.02;
    return {
      slot: `L${levelIdx}`,
      levelIdx,
      weightPct: Number((w * 100).toFixed(1)),
      usdt: legUsdt,
      mrDistPct,
      clippedInMr,
      belowMin,
    };
  });

  const activeMrLegs = legs.filter((l) => !l.clippedInMr);
  const thinnestLeg = legs.reduce((minL, l) => (l.usdt < minL.usdt ? l : minL), legs[0]);
  // Estimate worst-case full-side stop-loss loss in USDT (average entry to center +/- SL in MR mode, per-slot SL in REVERSE_GRID, or full budget * SL)
  const worstSlLossUsdt =
    st === 'TREND_GRID'
      ? Number((orderUsdt * n * weights[0] * Math.min(sl, dzMult * step) + sideBudget * 0.001).toFixed(2))
      : st === 'REVERSE_GRID'
      ? Number(
          (
            activeMrLegs.reduce((acc, l) => acc + l.usdt * sl, 0) +
            activeMrLegs.reduce((acc, l) => acc + l.usdt, 0) * 0.001
          ).toFixed(2)
        )
      : Number(
          (
            activeMrLegs.reduce((acc, l) => acc + l.usdt * Math.max(0, sl - l.mrDistPct), 0) +
            activeMrLegs.reduce((acc, l) => acc + l.usdt, 0) * 0.001
          ).toFixed(2)
        );
  const slRoePct = Number((sl * lev * 100).toFixed(1));
  const minSuggestedOrderUsdt =
    okxMinNotional > 0 && isTrendLike && decay && weights.length > 0
      ? Math.ceil((okxMinNotional * 1.08) / (n * Math.min(...weights)))
      : okxMinNotional > 0
      ? Math.ceil(okxMinNotional * 1.08)
      : 0;

  const alerts = [];
  if (st !== 'TREND_GRID' && totalSpan > rng + 1e-6) {
    alerts.push({
      severity: 'error',
      msg: `层数×间距 (${n}×${(step * 100).toFixed(2)}%=${(totalSpan * 100).toFixed(2)}%) 超出区间总跨度 ±${(
        rng * 100
      ).toFixed(2)}%，外层 ${n - activeMrLegs.length} 格会被裁切不铺`,
    });
  }
  if (st !== 'TREND_GRID' && tp < step - 1e-6) {
    alerts.push({
      severity: 'error',
      msg: `单格止盈 (${(tp * 100).toFixed(2)}%) 低于每格间距 (${(step * 100).toFixed(2)}%)，存在止盈倒挂`,
    });
  }
  if (st !== 'TREND_GRID' && tp <= effRtFee) {
    alerts.push({
      severity: 'error',
      msg: `单格止盈 (${(tp * 100).toFixed(2)}%) 未覆盖双边手续费 (${(effRtFee * 100).toFixed(2)}%)`,
    });
  }
  if ((st === 'LONG_SHORT_GRID' || st === 'ADAPTIVE_GRID') && sl <= rng + 1e-6) {
    alerts.push({
      severity: 'error',
      msg: `破网硬止损 (${(sl * 100).toFixed(2)}%) ≤ 区间总跨度 (±${(rng * 100).toFixed(2)}%)，未出区间即强平`,
    });
  }
  if (st === 'REVERSE_GRID' && sl <= step + 1e-6) {
    alerts.push({
      severity: 'error',
      msg: `单格硬止损 (${(sl * 100).toFixed(2)}%) ≤ 每格间距 (${(step * 100).toFixed(2)}%)，轻微回撤即触发止损`,
    });
  } else if (st === 'REVERSE_GRID' && sl > Math.max(step * 3.2, tp * 3.0, 0.026)) {
    alerts.push({
      severity: 'warn',
      msg: `单格硬止损 (${(sl * 100).toFixed(2)}%) 远宽于单格止盈 (${(tp * 100).toFixed(2)}%)，易造成多空对锁且亏多赚少，建议设为 ${(
        Math.max(step * 2, step + 0.005) * 100
      ).toFixed(2)}%`,
    });
  }
  if (isTrendLike && dzMult * step > rng + 1e-6) {
    alerts.push({
      severity: 'warn',
      msg: `顺势死区 (${(dzMult * step * 100).toFixed(2)}%) 超出区间 (±${(rng * 100).toFixed(2)}%)`,
    });
  }
  if (slRoePct > 35) {
    alerts.push({
      severity: 'warn',
      msg: `${lev}x 杠杆折算保证金硬止损达 ${slRoePct.toFixed(1)}% (>35%)，单边极端行情回撤偏大`,
    });
  }
  if (okxMinNotional > 0 && thinnestLeg.usdt < okxMinNotional * 1.02) {
    alerts.push({
      severity: 'error',
      msg: `最薄单层 ${thinnestLeg.slot} (${thinnestLeg.usdt.toFixed(1)}U) 低于 OKX 实时最小开仓额 (${okxMinNotional.toFixed(
        2
      )}U)${minSuggestedOrderUsdt > orderUsdt ? `，建议单格金额 ≥ ${minSuggestedOrderUsdt}U` : ''}`,
    });
  }

  return {
    n,
    step,
    tp,
    rng,
    sl,
    lev,
    totalSpan,
    sideBudget,
    sideMargin,
    legs,
    activeMrCount: activeMrLegs.length,
    thinnestLeg,
    worstSlLossUsdt,
    slRoePct,
    okxMinNotional,
    minSuggestedOrderUsdt,
    followerMid,
    alerts,
  };
};

const strategyBadgeClass = (st, active = false) => {
  if (st === 'TREND_GRID') {
    return active ? 'bg-violet-500 text-white' : 'bg-violet-500/15 text-violet-600 dark:text-violet-300';
  }
  if (st === 'ADAPTIVE_GRID') {
    return active ? 'bg-amber-500 text-white' : 'bg-amber-500/15 text-amber-600 dark:text-amber-300';
  }
  if (st === 'REVERSE_GRID') {
    return active ? 'bg-teal-500 text-white' : 'bg-teal-500/15 text-teal-600 dark:text-teal-300';
  }
  if (st === 'LONG_SHORT_GRID') {
    return active ? 'bg-sky-500 text-white' : 'bg-sky-500/15 text-sky-600 dark:text-sky-400';
  }
  return active ? 'bg-white/15 text-white' : 'surface-subtle text-sub';
};

const GRID_MODE_LABEL_MAP = {
  LONG_SHORT: '多空双开网格',
  LONG_ONLY: '仅做多网格',
  SHORT_ONLY: '仅做空网格',
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
  GRID_TP: {
    text: '网格止盈',
    cls: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
  GRID_SL: {
    text: '网格止损',
    cls: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30',
  },
  TREND_TRAIL_TP: {
    text: '顺势追踪止盈',
    cls: 'bg-teal-500/15 text-teal-600 dark:text-teal-300 border-teal-500/30',
  },
  TREND_SL: {
    text: '假突破止损',
    cls: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30',
  },
  TREND_BE_SL: {
    text: '浮盈保本止损',
    cls: 'bg-sky-500/10 text-sky-600 dark:text-sky-300 border-sky-500/30',
  },
  MANUAL_CLOSE: {
    text: '手动平仓',
    cls: 'surface-subtle text-sub',
  },
};

function PercentNumberInput({
  valueRatio,
  defaultRatio = 0,
  scale = 100,
  decimals = 2,
  step = '0.05',
  min,
  max,
  onChangeRatio,
  className = 'input-field font-mono',
}) {
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState('');
  const formatted = ((Number(valueRatio ?? defaultRatio) || 0) * scale).toFixed(decimals);

  return (
    <input
      type="number"
      step={step}
      min={min}
      max={max}
      value={focused ? draft : formatted}
      onFocus={() => {
        setFocused(true);
        setDraft(formatted);
      }}
      onChange={(e) => {
        const raw = e.target.value;
        setDraft(raw);
        if (raw !== '' && raw !== '-' && raw !== '.') {
          const parsed = Number(raw);
          if (Number.isFinite(parsed)) {
            onChangeRatio(Number((parsed / scale).toFixed(decimals + 4)));
          }
        }
      }}
      onBlur={() => {
        setFocused(false);
      }}
      className={className}
    />
  );
}

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('sniper_theme') || 'light');
  const [activeTab, setActiveTab] = useState('radar');
  const [selectedSymbol, setSelectedSymbol] = useState('XRP/USDT:USDT');
  const selectedSymbolRef = useRef('XRP/USDT:USDT');
  selectedSymbolRef.current = selectedSymbol;
  const wsRef = useRef(null);
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
    position_pnl_summary: {
      total_positions: 0,
      profit_count: 0,
      loss_count: 0,
      flat_count: 0,
      total_profit_usdt: 0,
      total_loss_usdt: 0,
      net_unrealized_usdt: 0,
      total_notional_usdt: 0,
      net_unrealized_pct: 0,
    },
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
      stats_reset_at_ms: 0,
    },
  });

  const [strategies, setStrategies] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [exchanges, setExchanges] = useState([]);
  const [riskForm, setRiskForm] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSector, setSelectedSector] = useState('ALL');
  const [selectedExchangeFilter, setSelectedExchangeFilter] = useState('ALL');
  const [toast, setToast] = useState(null);
  const [logFilter, setLogFilter] = useState('ALL');
  const [analyticsSymbolFilter, setAnalyticsSymbolFilter] = useState('ALL');
  const [showRunningOnly, setShowRunningOnly] = useState(true);

  // User Auth & Membership State
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('sniper_auth_token') || '');
  const [currentUser, setCurrentUser] = useState(null);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState('login');
  const [preflightResult, setPreflightResult] = useState(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [syncingPositions, setSyncingPositions] = useState(false);
  const [closingPosKey, setClosingPosKey] = useState('');
  const [resettingStats, setResettingStats] = useState(false);
  const [confirmResetStats, setConfirmResetStats] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchConfirmApply, setBatchConfirmApply] = useState(false);
  const [batchReport, setBatchReport] = useState(null);
  const [autoLinkGridParams, setAutoLinkGridParams] = useState(true);
  const [batchForm, setBatchForm] = useState({
    strategy_filter: 'ALL_GRID',
    only_running: false,
    order_usdt_str: '',
    grid_levels_str: '',
    grid_step_pct_str: '0.60',
    grid_take_profit_pct_str: '0.65',
    grid_range_pct_str: '',
    grid_stop_loss_pct_str: '',
    trend_trailing_mult_str: '',
    use_post_only_tp_mode: 'keep',
    sl_trigger_px_type_mode: 'keep',
    auto_fix_anomalies: true,
    auto_link_batch: true,
  });

  const toastTimerRef = useRef(null);

  const showToast = useCallback((rawMsg, type = 'info') => {
    const fieldNameMap = {
      order_usdt: '下单名义金额(USDT)',
      leverage: '合约杠杆倍数',
      price_precision: '价格精度',
      qty_precision: '数量精度',
      min_qty: '最小下单量',
      grid_levels: '单边网格层数',
      grid_step_pct: '每格触发间距',
      grid_take_profit_pct: '单格止盈目标',
      grid_range_pct: '区间总跨度',
      grid_stop_loss_pct: '破网硬止损阈值',
      sl_trigger_px_type: '止损触发价类型',
      window_ms: '脉冲检测窗口',
      min_jump_pct: '最小脉冲幅度',
      take_profit_pct: '目标净止盈',
      stop_loss_pct: '最大硬性止损',
    };
    let safeMsg = '操作提示';
    if (typeof rawMsg === 'string' && rawMsg.trim()) {
      safeMsg = rawMsg.trim();
    } else if (Array.isArray(rawMsg)) {
      const parts = rawMsg
        .map((item) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object') {
            const loc = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : '';
            const label = fieldNameMap[loc] || loc || '参数';
            return `${label}: ${item.msg || '格式或范围无效'}`;
          }
          return '';
        })
        .filter(Boolean);
      safeMsg = parts.length ? `参数校验未通过（${parts.join('；')}）` : '请求参数校验未通过，请检查输入项';
    } else if (rawMsg && typeof rawMsg === 'object') {
      const inner = rawMsg.detail ?? rawMsg.message ?? rawMsg.error;
      if (typeof inner === 'string' && inner.trim()) {
        safeMsg = inner.trim();
      } else if (Array.isArray(inner)) {
        safeMsg = `参数校验未通过（${inner.map((x) => x?.msg || '').filter(Boolean).join('；') || '请检查输入'}）`;
      } else {
        safeMsg = '操作失败，请检查参数后重试';
      }
    }
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ msg: String(safeMsg), type });
    toastTimerRef.current = setTimeout(() => setToast(null), 4200);
  }, []);

  const authHeaders = useMemo(() => {
    return authToken ? { Authorization: `Bearer ${authToken}` } : {};
  }, [authToken]);

  const openAuthModal = useCallback((mode = 'login') => {
    setAuthModalMode(mode);
    setAuthModalOpen(true);
  }, []);

  const ensureServerInLiveMode = useCallback(async (tk) => {
    if (!tk) return;
    try {
      const res = await fetch('/api/mode/enter-live', {
        method: 'POST',
        headers: { Authorization: `Bearer ${tk}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.risk) setRiskForm(data.risk);
        if (data.snapshot) setSnapshot(data.snapshot);
      }
    } catch (e) {
      // ignore
    }
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
            await ensureServerInLiveMode(tk);
            return data.user;
          }
          // Explicitly unauthenticated (expired or password changed)
          setCurrentUser(null);
          setAuthToken('');
          localStorage.removeItem('sniper_auth_token');
          showToast('登录凭证已过期或密码已更新，请重新登录', 'info');
          return null;
        }
        if (res.status === 401 || res.status === 403) {
          setCurrentUser(null);
          setAuthToken('');
          localStorage.removeItem('sniper_auth_token');
          return null;
        }
        // Transient 502/503/504 server restart: preserve token & session!
        return null;
      } catch (e) {
        // Transient network hiccup: preserve localStorage token!
        return null;
      }
    },
    [authToken, ensureServerInLiveMode, showToast]
  );

  useEffect(() => {
    refreshCurrentUser();
  }, [refreshCurrentUser]);

  const handleAuthSuccess = useCallback(
    async (token, user, message) => {
      setAuthToken(token);
      localStorage.setItem('sniper_auth_token', token);
      setCurrentUser(user);
      await ensureServerInLiveMode(token);
      showToast(message || `欢迎登录，${user.username}（已进入实盘系统）`, 'success');
    },
    [ensureServerInLiveMode, showToast]
  );

  const handleLogout = useCallback(async () => {
    setAuthToken('');
    localStorage.removeItem('sniper_auth_token');
    setCurrentUser(null);
    setExchanges([]);
    setStrategies([]);
    setRiskForm(null);
    setPreflightResult(null);
    setBatchReport(null);
    setBatchConfirmApply(false);
    if (activeTab === 'admin' || activeTab === 'exchanges') {
      setActiveTab('radar');
    }
    showToast('您已安全退出当前浏览器会话（后台已配置的实盘策略仍将在服务器上继续运行）', 'info');
  }, [activeTab, showToast]);

  const fetchInitialData = useCallback(async () => {
    try {
      const hdrs = authToken ? { Authorization: `Bearer ${authToken}` } : {};
      const radarQs = selectedSymbolRef.current
        ? `?radar_symbol=${encodeURIComponent(selectedSymbolRef.current)}`
        : '';
      const requests = [
        fetch('/api/symbols', { headers: hdrs }).then((r) => r.json()),
        fetch('/api/risk', { headers: hdrs }).then((r) => r.json()),
        fetch(`/api/stats${radarQs}`, { headers: hdrs }).then((r) => r.json()),
        authToken
          ? fetch('/api/exchanges', { headers: hdrs }).then((r) => (r.ok ? r.json() : { exchanges: [] }))
          : Promise.resolve({ exchanges: [] }),
      ];
      const [symRes, riskRes, statRes, exRes] = await Promise.all(requests);
      if (symRes.strategies) {
        setStrategies(symRes.strategies);
        if (symRes.strategies.length > 0 && !symRes.strategies.some((s) => s.symbol === selectedSymbolRef.current)) {
          setSelectedSymbol(symRes.strategies[0].symbol);
        }
      }
      if (symRes.catalog) setCatalog(symRes.catalog);
      setExchanges(exRes.exchanges || []);
      if (riskRes.risk) setRiskForm(riskRes.risk);
      if (statRes.stats) setSnapshot(statRes);
    } catch (err) {
      console.error('Initial load error:', err);
    }
  }, [authToken]);

  useEffect(() => {
    fetchInitialData();
  }, [fetchInitialData]);

  useEffect(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && selectedSymbol) {
      try {
        ws.send(JSON.stringify({ type: 'select_symbol', symbol: selectedSymbol }));
      } catch {}
    }
  }, [selectedSymbol]);

  useEffect(() => {
    let ws = null;
    let reconnectTimer = null;
    let unmounted = false;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const params = new URLSearchParams();
      if (authToken) params.set('token', authToken);
      if (selectedSymbolRef.current) params.set('radar_symbol', selectedSymbolRef.current);
      const qsStr = params.toString();
      const wsUrl = `${proto}//${window.location.host}/ws/dashboard${qsStr ? `?${qsStr}` : ''}`;
      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!unmounted) {
          setWsConnected(true);
          if (selectedSymbolRef.current) {
            try {
              ws.send(JSON.stringify({ type: 'select_symbol', symbol: selectedSymbolRef.current }));
            } catch {}
          }
        }
      };

      let lastHiddenApplyMs = 0;
      let latestPendingData = null;

      const handleVisibilityChange = () => {
        if (!unmounted && typeof document !== 'undefined' && !document.hidden && latestPendingData) {
          setSnapshot(latestPendingData);
          latestPendingData = null;
        }
      };
      if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
        document.addEventListener('visibilitychange', handleVisibilityChange);
      }

      ws.onmessage = (ev) => {
        if (unmounted) return;
        try {
          const data = JSON.parse(ev.data);
          if (typeof document !== 'undefined' && document.hidden) {
            latestPendingData = data;
            const now = Date.now();
            if (now - lastHiddenApplyMs < 2500) {
              return;
            }
            lastHiddenApplyMs = now;
          }
          latestPendingData = null;
          setSnapshot(data);
        } catch (e) {
          console.error('WS parse error:', e);
        }
      };

      ws.onclose = () => {
        if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
          document.removeEventListener('visibilitychange', handleVisibilityChange);
        }
        if (wsRef.current === ws) wsRef.current = null;
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
      if (wsRef.current === ws) wsRef.current = null;
      if (ws) ws.close();
    };
  }, [authToken]);

  const handleKillSwitch = async () => {
    const nextState = !snapshot.risk?.kill_switch_active;
    const res = await fetch(`/api/risk/kill-switch?active=${nextState}`, {
      method: 'POST',
      headers: { ...authHeaders },
    });
    if (res.ok) {
      const kData = await res.json().catch(() => ({}));
      showToast(
        nextState ? '全局紧急熔断已启动，所有挂单已撤销并暂停开仓' : '全局紧急熔断已解除，恢复毫秒级监控',
        nextState ? 'error' : 'success'
      );
      if (kData.risk) {
        setRiskForm(kData.risk);
      } else {
        const riskRes = await fetch('/api/risk', { headers: { ...authHeaders } }).then((r) => r.json());
        if (riskRes.risk) setRiskForm(riskRes.risk);
      }
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '操作失败，请先登录或确认会员权限', 'error');
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
    if (data.risk) setRiskForm(data.risk);
    if (data.snapshot) {
      setSnapshot(data.snapshot);
    } else {
      const radarQs = selectedSymbolRef.current
        ? `?radar_symbol=${encodeURIComponent(selectedSymbolRef.current)}`
        : '';
      const statRes = await fetch(`/api/stats${radarQs}`, { headers: { ...authHeaders } }).then((r) => r.json());
      if (statRes.stats) setSnapshot(statRes);
    }
    const exRes = await fetch('/api/exchanges', { headers: { ...authHeaders } }).then((r) => r.json());
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
      if (data.require_login) {
        showToast(data.message || '请先注册并登录账号', 'error');
        openAuthModal('login');
        return;
      }
      if (data.require_vip) {
        showToast(data.message, 'error');
        setActiveTab('membership');
        return;
      }
      if (authToken) {
        const exRes = await fetch('/api/exchanges', { headers: { ...authHeaders } }).then((r) =>
          r.ok ? r.json() : {}
        );
        if (exRes.exchanges) setExchanges(exRes.exchanges);
      }
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

  const handleSaveSymbol = async (cfg, customSuccessMsg = '') => {
    if (!cfg || !cfg.symbol) {
      showToast('未选中有效合约，无法保存策略参数', 'error');
      return false;
    }
    const orderUsdt = Number(cfg.order_usdt);
    if (!Number.isFinite(orderUsdt) || orderUsdt <= 0) {
      showToast('保存失败：下单名义金额 (USDT) 必须大于 0', 'error');
      return false;
    }
    const leverage = Math.round(Number(cfg.leverage ?? 5));
    if (!Number.isFinite(leverage) || leverage < 1 || leverage > 50) {
      showToast('保存失败：合约杠杆倍数需在 1 ~ 50 倍之间', 'error');
      return false;
    }
    const rawPricePrec = Number(cfg.price_precision);
    const rawQtyPrec = Number(cfg.qty_precision);
    const sanitizedCfg = {
      ...cfg,
      exchange_id: cfg.exchange_id || 'okx',
      order_usdt: orderUsdt,
      leverage,
      price_precision: Math.min(12, Math.max(0, Math.round(Number.isFinite(rawPricePrec) ? rawPricePrec : 4))),
      qty_precision: Math.min(12, Math.max(0, Math.round(Number.isFinite(rawQtyPrec) ? rawQtyPrec : 2))),
      min_qty: Math.max(0.00000001, Number(cfg.min_qty) || 0.1),
      grid_levels: Math.min(30, Math.max(1, Math.round(Number(cfg.grid_levels ?? 3) || 3))),
      grid_step_pct: Math.min(0.5, Math.max(0.0001, Number(cfg.grid_step_pct ?? 0.008) || 0.008)),
      grid_take_profit_pct: Math.min(0.5, Math.max(0.0001, Number(cfg.grid_take_profit_pct ?? 0.0085) || 0.0085)),
      grid_range_pct: Math.min(0.8, Math.max(0.001, Number(cfg.grid_range_pct ?? 0.028) || 0.028)),
      grid_stop_loss_pct: Math.min(0.9, Math.max(0.002, Number(cfg.grid_stop_loss_pct ?? 0.038) || 0.038)),
      sl_trigger_px_type: cfg.sl_trigger_px_type === 'last' ? 'last' : 'mark',
    };
    try {
      const res = await fetch('/api/symbol/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify(sanitizedCfg),
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.strategies)) {
          setStrategies(data.strategies);
        }
        if (data.warning) {
          showToast(
            customSuccessMsg
              ? `${customSuccessMsg}（${data.warning}）`
              : `已保存 ${sanitizedCfg.symbol}（提示：${data.warning}）`,
            'info'
          );
        } else {
          showToast(customSuccessMsg || `已保存并立即激活 ${sanitizedCfg.symbol} 策略参数`, 'success');
        }
        return true;
      }
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || err.error || '保存策略失败，实盘通道需 VIP 会员权限', 'error');
      return false;
    } catch (e) {
      showToast('保存请求失败，请检查网络连接后重试', 'error');
      return false;
    }
  };

  const handleDeleteSymbol = async (sym) => {
    const res = await fetch(`/api/symbol/${encodeURIComponent(sym)}`, {
      method: 'DELETE',
      headers: { ...authHeaders },
    });
    if (res.ok) {
      const data = await res.json();
      setStrategies(data.strategies);
      if (selectedSymbol === sym && data.strategies.length > 0) {
        setSelectedSymbol(data.strategies[0].symbol);
      }
      showToast(`已移除监控合约 ${sym}`, 'info');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '当前合约存在在途状态或权限不足，无法移除', 'error');
    }
  };

  const handleAddSymbolByCode = async (rawSymbolInput, explicitExchangeId = '') => {
    const target = (rawSymbolInput || searchQuery).trim();
    if (!target) {
      showToast('请输入或选择要添加的标的代号（如 SPY、SPX、AAPL、NVDA、SUI、ARB）', 'error');
      return;
    }
    const effExId = explicitExchangeId || (selectedExchangeFilter !== 'ALL' ? selectedExchangeFilter : '');
    try {
      const qs = `/api/symbol/verify?symbol=${encodeURIComponent(target)}${
        effExId ? `&exchange_id=${encodeURIComponent(effExId)}` : ''
      }`;
      const verifyResp = await fetch(qs, {
        headers: { ...authHeaders },
      });
      const vRes = await verifyResp.json().catch(() => ({}));
      if (!verifyResp.ok || !vRes || vRes.verified === false) {
        showToast(
          vRes?.error || vRes?.detail || `无法添加标的「${target}」：未在当前所选交易所/券商找到该标的，请检查代号`,
          'error'
        );
        return;
      }
      if (vRes.already_monitored || strategies.some((s) => s.symbol === vRes.symbol)) {
        const existingCfg = strategies.find((s) => s.symbol === vRes.symbol);
        if (existingCfg && effExId && existingCfg.exchange_id !== effExId) {
          await handleSaveSymbol(
            { ...existingCfg, exchange_id: effExId },
            `已将 ${vRes.symbol} 的执行通道切换为 [${effExId.toUpperCase()}]`
          );
        } else {
          showToast(`标的 ${vRes.symbol} 已在监控列表中，已为您直接定位并选中`, 'info');
        }
        setSelectedSymbol(vRes.symbol);
        setSearchQuery('');
        return;
      }
      const defaultStrat = vRes.recommended_strategy_type || 'LEAD_LAG';
      const defaultOrderUsdt = Number(vRes.recommended_order_usdt) > 0 ? Number(vRes.recommended_order_usdt) : 300;
      const targetExId = vRes.recommended_exchange_id || effExId || 'okx';
      const newCfg = {
        symbol: vRes.symbol,
        exchange_id: targetExId,
        strategy_type: defaultStrat,
        direction: 'BOTH',
        order_usdt: defaultOrderUsdt,
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
        sl_trigger_px_type: 'mark',
        max_signal_age_ms: 80,
        max_book_staleness_ms: 600,
        contract_size: 1.0,
        min_qty: vRes.recommended_precision?.min_qty ?? 0.1,
        qty_precision: Math.min(12, Math.max(0, Number(vRes.recommended_precision?.qty_precision ?? 2))),
        price_precision: Math.min(12, Math.max(0, Number(vRes.recommended_precision?.price_precision ?? 4))),
        grid_side_mode: 'LONG_SHORT',
        grid_levels: 3,
        grid_step_pct: 0.008,
        grid_take_profit_pct: 0.0085,
        grid_range_pct: 0.028,
        grid_stop_loss_pct: 0.038,
        grid_recenter_enabled: true,
        trend_dead_zone_mult: 1.25,
        trend_trailing_mult: 1.25,
        trend_pyramid_decay: true,
        trend_use_bb_breakout: true,
        bbw_timeframe: '15m',
        bbw_squeeze_percentile: 20.0,
        is_running: true,
      };
      const saved = await handleSaveSymbol(
        newCfg,
        vRes.warning ? `已添加 ${vRes.symbol}（${vRes.warning}）` : `已成功添加并激活 ${vRes.symbol} 监控合约`
      );
      if (saved) {
        setSelectedSymbol(vRes.symbol);
        setSearchQuery('');
      }
    } catch (e) {
      showToast(`添加合约「${target}」失败，请检查代号或网络后重试`, 'error');
    }
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

  const handleSwitchOkxAccountMode = async (exchangeId = 'okx', acctLv = '2') => {
    if (!currentUser?.can_use_live) {
      showToast('需开通 VIP 会员后才可切换交易所账户模式', 'error');
      return;
    }
    showToast('正在调用 OKX 官方接口切换账户模式为单币种保证金模式...', 'info');
    const res = await fetch(
      `/api/exchanges/switch-account-mode?exchange_id=${encodeURIComponent(exchangeId)}&acct_lv=${encodeURIComponent(acctLv)}`,
      { method: 'POST', headers: { ...authHeaders } }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.exchanges) setExchanges(data.exchanges);
      if (data.snapshot) setSnapshot(data.snapshot);
      const sw = data.switch_result;
      showToast(sw?.message || '账户模式操作完成', sw?.ok ? 'success' : 'error');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '切换账户模式失败', 'error');
    }
  };

  const handleRecenterGrid = async (symbol) => {
    const res = await fetch(`/api/symbol/grid-recenter?symbol=${encodeURIComponent(symbol)}`, {
      method: 'POST',
      headers: { ...authHeaders },
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      if (data.snapshot) setSnapshot(data.snapshot);
      showToast(data.message || '网格中枢已重锚', 'success');
    } else {
      showToast(data.detail || '重锚网格失败', 'error');
    }
  };

  const handleSyncPositions = async () => {
    if (!authToken) {
      openAuthModal('login');
      return;
    }
    setSyncingPositions(true);
    try {
      const res = await fetch('/api/positions/sync', {
        method: 'POST',
        headers: { ...authHeaders },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok !== false) {
        if (data.snapshot) setSnapshot(data.snapshot);
        const cnt = Number(data.synced_count || 0);
        const rec = Number(data.recovered_count || 0);
        showToast(
          cnt > 0
            ? `已同步交易所 ${cnt} 笔在途持仓` + (rec > 0 ? `（新接管 ${rec} 笔至策略监控）` : '')
            : '同步完成：当前交易所无未平仓持仓',
          'success'
        );
      } else {
        showToast(data.error || data.detail || '同步交易所持仓失败，请先确认已配置有效 API', 'error');
      }
    } finally {
      setSyncingPositions(false);
    }
  };

  useEffect(() => {
    if (!confirmResetStats) return undefined;
    const t = setTimeout(() => setConfirmResetStats(false), 6000);
    return () => clearTimeout(t);
  }, [confirmResetStats]);

  const handleResetStats = async () => {
    if (!authToken || !currentUser) {
      openAuthModal('login');
      return;
    }
    setResettingStats(true);
    try {
      const radarQs = selectedSymbolRef.current
        ? `?radar_symbol=${encodeURIComponent(selectedSymbolRef.current)}`
        : '';
      const res = await fetch(`/api/stats/reset${radarQs}`, {
        method: 'POST',
        headers: { ...authHeaders },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok !== false) {
        if (data.snapshot) setSnapshot(data.snapshot);
        setConfirmResetStats(false);
        showToast(data.message || '已一键清零统计数据并从 0 重新开始统计', 'success');
      } else {
        showToast(data.detail || data.message || '清零统计失败，请稍后重试', 'error');
      }
    } catch (e) {
      showToast('清零统计请求失败，请检查网络连接', 'error');
    } finally {
      setResettingStats(false);
    }
  };

  const handleClosePosition = async (pos) => {
    if (!pos?.symbol) return;
    const posKey = `${pos.symbol}:${pos.side || 'LONG'}:${pos.grid_slot_id || ''}`;
    if (closingPosKey === posKey) return;
    setClosingPosKey(posKey);
    try {
      const res = await fetch('/api/positions/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({
          symbol: pos.symbol,
          side: pos.side || 'LONG',
          slot_id: pos.grid_slot_id || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        if (data.snapshot) setSnapshot(data.snapshot);
        showToast(data.message || `已触发平仓 ${pos.symbol}`, 'success');
      } else {
        showToast(data.detail || '平仓失败', 'error');
      }
    } catch (e) {
      showToast('平仓请求异常，请稍后重试', 'error');
    } finally {
      setClosingPosKey('');
    }
  };

  const handleAdoptGridPosition = async (symbol) => {
    if (!symbol) return;
    try {
      const res = await fetch('/api/positions/adopt-grid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ symbol }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        if (Array.isArray(data.strategies)) setStrategies(data.strategies);
        if (data.snapshot) setSnapshot(data.snapshot);
        setSelectedSymbol(symbol);
        showToast(data.message || `已将 ${symbol} 纳入多空网格策略`, 'success');
      } else {
        showToast(data.detail || '纳入策略失败', 'error');
      }
    } catch (e) {
      showToast('请求失败，请检查网络', 'error');
    }
  };

  const formatPositionPrice = (px, sym) => {
    const val = Number(px || 0);
    if (!val || !Number.isFinite(val)) return '--';
    const matched = strategies.find((s) => s.symbol === sym);
    let prec = Number(matched?.price_precision ?? 4);
    if (val < 0.0001) prec = Math.max(prec, 10);
    else if (val < 0.01) prec = Math.max(prec, 6);
    prec = Math.min(12, Math.max(0, Math.round(prec)));
    return val.toFixed(prec);
  };

  const handleRunPreflight = async () => {
    if (!authToken) {
      openAuthModal('login');
      return;
    }
    setPreflightLoading(true);
    try {
      const res = await fetch('/api/exchanges/preflight', {
        method: 'POST',
        headers: { ...authHeaders },
      });
      if (res.ok) {
        const data = await res.json();
        setPreflightResult(data);
        showToast(
          data.ok ? '实盘只读预检通过：所有启用合约均具备真实下单条件' : '实盘只读预检发现阻断项，请查看下方诊断报告',
          data.ok ? 'success' : 'error'
        );
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || '预检请求失败', 'error');
      }
    } finally {
      setPreflightLoading(false);
    }
  };

  useEffect(() => {
    if (!batchConfirmApply) return undefined;
    const t = setTimeout(() => setBatchConfirmApply(false), 6000);
    return () => clearTimeout(t);
  }, [batchConfirmApply]);

  const handleBatchSymbols = async (action = 'validate') => {
    if (!authToken || !currentUser) {
      openAuthModal('login');
      return;
    }
    if (action === 'apply' && !currentUser.can_use_live) {
      showToast('批量修改实盘策略参数需开通 VIP 会员', 'error');
      setActiveTab('membership');
      return;
    }
    const payload = {
      action,
      strategy_filter: batchForm.strategy_filter || 'ALL_GRID',
      only_running: Boolean(batchForm.only_running),
      auto_fix_anomalies: Boolean(batchForm.auto_fix_anomalies),
    };
    if (action === 'apply') {
      const ordU = Number(batchForm.order_usdt_str);
      if (batchForm.order_usdt_str !== '' && Number.isFinite(ordU) && ordU >= 5) {
        payload.order_usdt = Number(ordU.toFixed(2));
      }
      const lvls = Math.round(Number(batchForm.grid_levels_str));
      if (batchForm.grid_levels_str !== '' && Number.isFinite(lvls) && lvls >= 1 && lvls <= 30) {
        payload.grid_levels = lvls;
      }
      const stepPct = Number(batchForm.grid_step_pct_str);
      if (batchForm.grid_step_pct_str !== '' && Number.isFinite(stepPct) && stepPct > 0) {
        payload.grid_step_pct = Number((stepPct / 100).toFixed(6));
      }
      const tpPct = Number(batchForm.grid_take_profit_pct_str);
      if (batchForm.grid_take_profit_pct_str !== '' && Number.isFinite(tpPct) && tpPct > 0) {
        payload.grid_take_profit_pct = Number((tpPct / 100).toFixed(6));
      }
      const rngPct = Number(batchForm.grid_range_pct_str);
      if (batchForm.grid_range_pct_str !== '' && Number.isFinite(rngPct) && rngPct >= 0.1) {
        payload.grid_range_pct = Number((rngPct / 100).toFixed(4));
      }
      const slPct = Number(batchForm.grid_stop_loss_pct_str);
      if (batchForm.grid_stop_loss_pct_str !== '' && Number.isFinite(slPct) && slPct >= 0.2) {
        payload.grid_stop_loss_pct = Number((slPct / 100).toFixed(6));
      }
      const trailMult = Number(batchForm.trend_trailing_mult_str);
      if (batchForm.trend_trailing_mult_str !== '' && Number.isFinite(trailMult) && trailMult >= 0.3) {
        payload.trend_trailing_mult = Number(trailMult.toFixed(3));
      }
      if (batchForm.use_post_only_tp_mode === 'on') {
        payload.use_post_only_tp = true;
      } else if (batchForm.use_post_only_tp_mode === 'off') {
        payload.use_post_only_tp = false;
      }
      if (batchForm.sl_trigger_px_type_mode === 'mark' || batchForm.sl_trigger_px_type_mode === 'last') {
        payload.sl_trigger_px_type = batchForm.sl_trigger_px_type_mode;
      }
    }
    setBatchLoading(true);
    try {
      const res = await fetch('/api/symbols/batch-update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok !== false) {
        setBatchReport(data);
        setBatchConfirmApply(false);
        if (Array.isArray(data.strategies)) setStrategies(data.strategies);
        if (data.snapshot) setSnapshot(data.snapshot);
        showToast(data.message || (action === 'apply' ? '批量参数更新完成' : '存量参数体检完成'), 'success');
      } else {
        showToast(data.detail || data.message || '批量参数操作失败', 'error');
      }
    } catch (e) {
      showToast('请求失败，请检查网络连接', 'error');
    } finally {
      setBatchLoading(false);
    }
  };

  const handleClearExchangeCredentials = async (exchangeId, displayName) => {
    if (!currentUser) {
      showToast('请先登录账号后再清除 API 配置', 'error');
      openAuthModal('login');
      return;
    }
    if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
      if (!window.confirm(`确认清除 ${displayName} 已保存的 API 密钥配置？`)) {
        return;
      }
    }
    try {
      const res = await fetch(`/api/exchanges/clear-credentials?exchange_id=${encodeURIComponent(exchangeId)}`, {
        method: 'POST',
        headers: { ...authHeaders },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
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
      } else {
        showToast(data.detail || `清除 ${displayName} API 失败`, 'error');
      }
    } catch (e) {
      showToast('清除 API 请求失败，请检查网络', 'error');
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
    if (!currentUser?.can_use_live) {
      showToast('修改全局实盘风控参数需开通 VIP 会员', 'error');
      setActiveTab('membership');
      return;
    }
    try {
      const res = await fetch('/api/risk/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify(riskForm),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        if (data.risk) setRiskForm(data.risk);
        if (data.snapshot) setSnapshot(data.snapshot);
        showToast('全局风控与延迟仿真参数已更新', 'success');
      } else {
        showToast(data.detail || '保存全局风控参数失败', 'error');
      }
    } catch (e) {
      showToast('保存全局风控请求失败，请检查网络', 'error');
    }
  };

  const filteredCatalog = useMemo(() => {
    const monitoredSet = new Set(strategies.map((s) => s.symbol));
    const isTradFilter = ['futu', 'moomoo', 'ibkr', 'schwab'].includes(selectedExchangeFilter);
    const list = catalog.filter((item) => {
      const matchesSector = selectedSector === 'ALL' || item.sector === selectedSector;
      const matchesExchange =
        selectedExchangeFilter === 'ALL' ||
        !Array.isArray(item.exchanges) ||
        item.exchanges.includes(selectedExchangeFilter);
      const q = searchQuery.trim().toUpperCase();
      const matchesQuery =
        !q ||
        item.symbol.includes(q) ||
        item.base.includes(q) ||
        (item.name_cn && item.name_cn.toUpperCase().includes(q));
      return matchesSector && matchesExchange && matchesQuery && !monitoredSet.has(item.symbol);
    });
    if (isTradFilter || selectedSector === '标普500') {
      return [...list].sort((a, b) => (a.sector === '标普500' ? -1 : 1) - (b.sector === '标普500' ? -1 : 1));
    }
    return list;
  }, [catalog, strategies, selectedSector, selectedExchangeFilter, searchQuery]);

  const currentStrategyIdx = strategies.findIndex((s) => s.symbol === selectedSymbol);
  const currentStrategy =
    currentStrategyIdx >= 0 ? strategies[currentStrategyIdx] : strategies[0] || {};
  const activeStrategyIndex = currentStrategyIdx >= 0 ? currentStrategyIdx : 0;

  const updateCurrentStrategyField = (patch) => {
    if (!strategies.length) return;
    const mergedPatch = { ...patch };
    if (autoLinkGridParams && patch.strategy_type && patch.strategy_type !== currentStrategy.strategy_type) {
      const step = Math.max(0.0005, Number(currentStrategy.grid_step_pct ?? 0.006) || 0.006);
      const curRng = Math.max(0.005, Number(currentStrategy.grid_range_pct ?? 0.035) || 0.035);
      const curSl = Math.max(0.002, Number(currentStrategy.grid_stop_loss_pct ?? 0.038) || 0.038);
      if (
        patch.strategy_type === 'REVERSE_GRID' &&
        (curSl <= step + 1e-6 || curSl > Math.max(step * 3.2, 0.026))
      ) {
        mergedPatch.grid_stop_loss_pct = Number(Math.min(0.9, Math.max(step * 2, step + 0.005)).toFixed(4));
      } else if (
        (patch.strategy_type === 'LONG_SHORT_GRID' || patch.strategy_type === 'ADAPTIVE_GRID') &&
        curSl <= curRng + 1e-6
      ) {
        mergedPatch.grid_stop_loss_pct = Number(
          Math.min(0.9, Math.ceil((curRng + 0.01) * 1000) / 1000).toFixed(4)
        );
      }
    }
    const next = [...strategies];
    next[activeStrategyIndex] = { ...currentStrategy, ...mergedPatch };
    setStrategies(next);
  };

  const updateGridParamWithLinkage = (field, rawVal) => {
    if (!strategies.length) return;
    const base = { ...currentStrategy };
    if (!autoLinkGridParams || !isGridStrategyType(base.strategy_type)) {
      updateCurrentStrategyField({ [field]: rawVal });
      return;
    }
    const patch = { [field]: rawVal };
    const n = field === 'grid_levels' ? Math.max(1, Math.min(30, Math.round(Number(rawVal) || 1))) : Math.max(1, Number(base.grid_levels ?? 5) || 5);
    const step = field === 'grid_step_pct' ? Math.max(0.0005, Number(rawVal) || 0.006) : Math.max(0.0005, Number(base.grid_step_pct ?? 0.006) || 0.006);
    const curTp = Number(base.grid_take_profit_pct ?? 0.0065) || 0.0065;
    const curRng = Number(base.grid_range_pct ?? 0.05) || 0.05;
    const curSl = Number(base.grid_stop_loss_pct ?? 0.08) || 0.08;

    const isCenterAnchoredSl = base.strategy_type === 'LONG_SHORT_GRID' || base.strategy_type === 'ADAPTIVE_GRID';
    if (field === 'grid_levels' || field === 'grid_step_pct') {
      const nextRng = suggestLinkedGridRange(n, step);
      patch.grid_range_pct = nextRng;
      if (field === 'grid_step_pct' || curTp < step) {
        patch.grid_take_profit_pct = Number(Math.min(0.5, Math.max(step + 0.0005, 0.0012)).toFixed(6));
      }
      if (isCenterAnchoredSl && curSl <= nextRng + 1e-6) {
        patch.grid_stop_loss_pct = Number(Math.min(0.9, Math.ceil((nextRng + 0.01) * 1000) / 1000).toFixed(4));
      } else if (base.strategy_type === 'REVERSE_GRID' && (curSl <= step + 1e-6 || curSl > Math.max(step * 3.2, 0.026))) {
        patch.grid_stop_loss_pct = Number(Math.min(0.9, Math.max(step * 2, step + 0.005)).toFixed(4));
      }
    } else if (field === 'grid_stop_loss_pct') {
      const nextSl = Math.max(0.002, Math.min(0.9, Number(rawVal) || 0.05));
      if (isCenterAnchoredSl && curRng >= nextSl - 1e-6) {
        patch.grid_range_pct = Number(Math.max(0.005, Math.floor(nextSl * 0.8 * 1000) / 1000).toFixed(4));
      }
    } else if (field === 'grid_range_pct') {
      const nextRng = Math.max(0.001, Math.min(0.8, Number(rawVal) || 0.05));
      if (isCenterAnchoredSl && curSl <= nextRng + 1e-6) {
        patch.grid_stop_loss_pct = Number(Math.min(0.9, Math.ceil((nextRng + 0.01) * 1000) / 1000).toFixed(4));
      }
    }
    updateCurrentStrategyField(patch);
  };

  const handleAutoAlignCurrentGrid = () => {
    if (!currentStrategy || !isGridStrategyType(currentStrategy.strategy_type)) return;
    const st = currentStrategy.strategy_type;
    const isTrendLike = st === 'TREND_GRID' || st === 'ADAPTIVE_GRID';
    const isCenterAnchoredSl = st === 'LONG_SHORT_GRID' || st === 'ADAPTIVE_GRID';
    const n = Math.max(1, Math.min(30, Math.round(Number(currentStrategy.grid_levels ?? 5) || 5)));
    const step = Math.max(0.0005, Number(currentStrategy.grid_step_pct ?? 0.006) || 0.006);
    const dz = Math.max(0.5, Number(currentStrategy.trend_dead_zone_mult ?? 2.0) || 2.0);
    const minRng = isTrendLike
      ? Math.max(suggestLinkedGridRange(n, step), dz * step * 1.3)
      : suggestLinkedGridRange(n, step);
    const nextRng = Math.min(0.8, Math.ceil(minRng * 1000) / 1000);
    const nextTp = Number(Math.max(Number(currentStrategy.grid_take_profit_pct ?? 0.0065) || 0.0065, step + 0.0005).toFixed(6));
    const curSl = Number(currentStrategy.grid_stop_loss_pct ?? 0.08) || 0.08;
    const nextSl = isCenterAnchoredSl
      ? curSl <= nextRng + 0.002
        ? Number(Math.min(0.9, Math.ceil((nextRng + 0.01) * 1000) / 1000).toFixed(4))
        : curSl
      : st === 'REVERSE_GRID' && (curSl <= step + 1e-6 || curSl > Math.max(step * 3.2, 0.026))
      ? Number(Math.min(0.9, Math.max(step * 2, step + 0.005)).toFixed(4))
      : curSl;
    updateCurrentStrategyField({
      grid_range_pct: nextRng,
      grid_take_profit_pct: nextTp,
      grid_stop_loss_pct: nextSl,
    });
    showToast(
      `已对齐 ${currentStrategy.symbol}：区间 ±${(nextRng * 100).toFixed(1)}% · 止盈 ${(nextTp * 100).toFixed(2)}% · 止损 ${(nextSl * 100).toFixed(1)}%（请点击保存生效）`,
      'success'
    );
  };

  const updateBatchFieldWithLinkage = (field, value) => {
    setBatchForm((prev) => {
      const next = { ...prev, [field]: value };
      if (!prev.auto_link_batch) return next;
      const lvls = Math.round(Number(next.grid_levels_str));
      const stepPct = Number(next.grid_step_pct_str);
      if ((field === 'grid_levels_str' || field === 'grid_step_pct_str') && Number.isFinite(stepPct) && stepPct > 0) {
        if (field === 'grid_step_pct_str') {
          const curTp = Number(next.grid_take_profit_pct_str);
          if (!Number.isFinite(curTp) || curTp < stepPct) {
            next.grid_take_profit_pct_str = (stepPct + 0.05).toFixed(2);
          }
        }
        if (next.grid_levels_str !== '' && Number.isFinite(lvls) && lvls >= 1 && lvls <= 30) {
          const recRngPct = suggestLinkedGridRange(lvls, stepPct / 100) * 100;
          next.grid_range_pct_str = recRngPct.toFixed(1);
          const curSl = Number(next.grid_stop_loss_pct_str);
          if (next.grid_stop_loss_pct_str !== '' && Number.isFinite(curSl) && curSl <= recRngPct) {
            next.grid_stop_loss_pct_str = (recRngPct + 1.0).toFixed(1);
          }
        }
      } else if (field === 'grid_stop_loss_pct_str') {
        const slPct = Number(value);
        const curRng = Number(next.grid_range_pct_str);
        if (value !== '' && Number.isFinite(slPct) && slPct >= 0.5 && next.grid_range_pct_str !== '' && Number.isFinite(curRng) && curRng >= slPct) {
          next.grid_range_pct_str = Math.max(0.5, Math.floor(slPct * 0.8 * 10) / 10).toFixed(1);
        }
      }
      return next;
    });
  };

  const currentTelemetry = snapshot.telemetry?.[selectedSymbol] || {};
  const currentRadarSeries = snapshot.radar_series?.[selectedSymbol] || [];
  const stats = snapshot.stats || {};
  const realityGap = snapshot.reality_gap || {};
  const liveStatus = snapshot.live_status || {};

  const formatUsdtSigned = useCallback((val, showPlus = true) => {
    const n = Number(val || 0);
    if (!Number.isFinite(n) || Math.abs(n) < 1e-8) return '$0.0000';
    const abs = Math.abs(n);
    const digits = abs >= 100 ? 2 : abs >= 10 ? 3 : 4;
    const prefix = n > 0 ? (showPlus ? '+$' : '$') : '-$';
    return `${prefix}${abs.toFixed(digits)}`;
  }, []);

  const formatResetTime = useCallback((ms) => {
    const n = Number(ms || 0);
    if (!Number.isFinite(n) || n <= 0) return '';
    const d = new Date(n);
    if (Number.isNaN(d.getTime())) return '';
    const pad = (v) => String(v).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }, []);

  const positionPnlSummary = useMemo(() => {
    const srv = snapshot.position_pnl_summary || stats.position_pnl_summary;
    if (srv && typeof srv.total_positions === 'number') {
      return srv;
    }
    const list = snapshot.active_positions || [];
    let totalProfit = 0;
    let totalLoss = 0;
    let totalNotional = 0;
    let profitCount = 0;
    let lossCount = 0;
    let flatCount = 0;
    for (const p of list) {
      const notional = Number(p.notional_usdt || 0);
      totalNotional += notional;
      const hasUpl = p.unrealized_usdt !== null && p.unrealized_usdt !== undefined && Number.isFinite(Number(p.unrealized_usdt));
      const upl = hasUpl ? Number(p.unrealized_usdt) : notional * Number(p.unrealized_net_pct || 0);
      if (upl > 1e-9) {
        totalProfit += upl;
        profitCount += 1;
      } else if (upl < -1e-9) {
        totalLoss += upl;
        lossCount += 1;
      } else {
        flatCount += 1;
      }
    }
    const netUpl = totalProfit + totalLoss;
    return {
      total_positions: list.length,
      profit_count: profitCount,
      loss_count: lossCount,
      flat_count: flatCount,
      total_profit_usdt: Number(totalProfit.toFixed(4)),
      total_loss_usdt: Number(totalLoss.toFixed(4)),
      net_unrealized_usdt: Number(netUpl.toFixed(4)),
      total_notional_usdt: Number(totalNotional.toFixed(2)),
      net_unrealized_pct: totalNotional > 0 ? Number(((netUpl / totalNotional) * 100).toFixed(2)) : 0,
    };
  }, [snapshot.position_pnl_summary, snapshot.active_positions, stats.position_pnl_summary]);
  const tradingControl = snapshot.trading_control || {
    trading_active: !snapshot.risk?.kill_switch_active,
    status_code: 'TRADING_ACTIVE',
    status_label: '自动交易运行中',
    status_detail: '正在毫秒级监听主从所价差脉冲',
    scan_ticks_count: 0,
  };
  const isKillActive = snapshot.risk?.kill_switch_active;
  const isLiveMode = Boolean(currentUser) || snapshot.risk?.trading_mode === 'LIVE';
  const isTradingActive = Boolean(tradingControl.trading_active);

  const filteredLogs = (snapshot.logs || []).filter((l) => {
    if (logFilter === 'ALL') return true;
    if (logFilter === 'SIGNAL') return l.level === 'SIGNAL' || l.level === 'FILL' || l.level.startsWith('EXIT');
    if (logFilter === 'MISS') return l.level === 'MISS';
    if (logFilter === 'RISK') return l.level === 'WARN' || l.level === 'CRITICAL';
    return true;
  });

  const filteredRecentTrades = (stats.recent_trades || [])
    .filter((tr) => analyticsSymbolFilter === 'ALL' || tr.symbol === analyticsSymbolFilter)
    .map((tr) => {
      const reason = String(tr.reason || '');
      const mSlot = reason.match(/^(.*)_(L\d+|S\d+)$/);
      const baseReason = mSlot
        ? mSlot[1]
        : reason.startsWith('GRID_TP_')
        ? 'GRID_TP'
        : reason.startsWith('GRID_SL_')
        ? 'GRID_SL'
        : reason;
      const slotSuffix = mSlot ? mSlot[2] : baseReason !== reason ? reason.split('_').slice(-1)[0] : '';
      const baseBadge = EXIT_BADGE_MAP[baseReason];
      const badge = baseBadge
        ? {
            ...baseBadge,
            text: slotSuffix ? `${baseBadge.text} (${slotSuffix})` : baseBadge.text,
          }
        : { text: reason || '--', cls: 'surface-subtle text-sub' };
      return { ...tr, _badge: badge };
    });

  return (
    <div className="min-h-screen flex flex-col">
      {/* Notification Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 left-3 right-3 sm:left-auto sm:bottom-6 sm:right-6 sm:max-w-md z-50 px-4 sm:px-5 py-3 rounded-2xl sm:rounded-full shadow-lg text-xs font-semibold flex items-center gap-2.5 transition-all surface-card ${
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
          <span className="leading-snug break-words">{toast.msg}</span>
        </div>
      )}

      {/* ==================== TOP HEADER ==================== */}
      <header
        className="sticky top-0 z-30 backdrop-blur-md border-b border-line px-3 sm:px-5 lg:px-6 py-2.5 sm:py-3"
        style={{ backgroundColor: 'var(--bg-header)' }}
      >
        <div className="max-w-[1520px] mx-auto flex flex-col lg:flex-row lg:items-center lg:justify-between gap-2.5 sm:gap-3">
          {/* Left: Brand Identity + Pill Mode Switcher */}
          <div className="flex flex-wrap items-center justify-between sm:justify-start gap-2.5 sm:gap-4 min-w-0">
            <div className="flex items-center gap-2.5 sm:gap-3 min-w-0">
              <div className="icon-box w-9 h-9 sm:w-10 sm:h-10">
                <svg className="w-4 h-4 sm:w-5 sm:h-5 text-main" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M2 12h4l3-8 4 16 3-8h6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="min-w-0">
                <h1 className="font-bold text-sm sm:text-base tracking-tight text-main leading-tight truncate">
                  跨所价差脉冲高频狙击系统
                </h1>
                <p className="text-[11px] text-dim hidden sm:block mt-0.5 truncate">
                  比例对数基差 · 保留价限价抢单 · 被动挂单止盈 · 八阶段同步锁状态机
                </p>
              </div>
            </div>

            {/* Pill Mode Indicator: Logged-in users ONLY see Live Mode (No Simulator shown) */}
            <div className="flex flex-wrap items-center rounded-full surface-subtle p-1 text-[11px] sm:text-xs max-w-full">
              {currentUser ? (
                <div
                  className="px-3 sm:px-4 py-1 sm:py-1.5 rounded-full font-semibold bg-[#0f1419] text-white shadow-sm flex flex-wrap items-center gap-1.5 sm:gap-2"
                  title="配置保存后，即使关闭网页或退出浏览器，服务器仍将7×24小时按您的设置持续运行"
                >
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                  <span>实盘交易系统</span>
                  <span className="hidden sm:inline-block text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-mono">
                    云端24h托管
                  </span>
                  {!currentUser.can_use_live && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/25 text-amber-400 font-bold">
                      需VIP激活交易
                    </span>
                  )}
                </div>
              ) : (
                <>
                  <span className="px-2.5 sm:px-3.5 py-1 sm:py-1.5 rounded-full font-semibold surface-card text-main shadow-sm">
                    访客预览
                  </span>
                  <button
                    onClick={() => openAuthModal('register')}
                    className="px-2.5 sm:px-3.5 py-1 sm:py-1.5 rounded-full font-semibold text-dim hover:text-main transition flex items-center gap-1"
                    title="注册并登录账号后进入实盘系统"
                  >
                    <Lock className="w-3 h-3" strokeWidth={1.75} />
                    <span>注册实盘</span>
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Right: Telemetry Pill, Execution Controls, User Badge & Theme */}
          <div className="flex flex-wrap items-center justify-between sm:justify-end gap-2 text-xs">
            <div
              className="flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3.5 py-1.5 rounded-full surface-subtle font-mono text-[11px] sm:text-xs"
              title="行情源与报单网络往返延迟 (RTT)"
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${wsConnected ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span className="hidden xl:inline font-sans text-main font-medium">
                {snapshot.feed_source === 'LIVE_WS' ? '实盘原生直连' : '50Hz仿真流'}
              </span>
              <span className="hidden xl:inline text-dim">|</span>
              <Wifi className="w-3.5 h-3.5 text-sub shrink-0" strokeWidth={1.75} />
              <span className="text-main font-bold">{snapshot.last_order_rtt_ms}ms</span>
            </div>

            {/* Unified Execution Control Group */}
            <div className="order-last sm:order-none w-full sm:w-auto flex items-center justify-between sm:justify-start rounded-full surface-subtle p-1 gap-1">
              <div
                className={`flex items-center gap-1.5 px-2.5 sm:px-3 py-1 rounded-full text-[11px] sm:text-xs font-semibold min-w-0 ${
                  isTradingActive
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                    ? 'text-rose-600 dark:text-rose-400'
                    : 'text-amber-600 dark:text-amber-400'
                }`}
                title={tradingControl.status_detail || ''}
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    isTradingActive
                      ? 'bg-emerald-500 animate-ping'
                      : tradingControl.status_code === 'AUTH_ERROR' || tradingControl.status_code === 'KILL_SWITCH'
                      ? 'bg-rose-500'
                      : 'bg-amber-500'
                  }`}
                />
                <span className="truncate max-w-[92px] sm:max-w-none">
                  {tradingControl.status_label || (isTradingActive ? '运行中' : '已停止')}
                </span>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={handleStartTrading}
                  className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 whitespace-nowrap ${
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
                  className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 whitespace-nowrap ${
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
                  className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-semibold transition flex items-center gap-1 whitespace-nowrap ${
                    isKillActive ? 'bg-emerald-600 text-white' : 'text-rose-600 dark:text-rose-400 hover:bg-rose-500/10'
                  }`}
                  title="全局紧急熔断：立即撤单平仓并暂停策略"
                >
                  <Power className="w-3 h-3" strokeWidth={2} />
                  <span>{isKillActive ? '解熔断' : '熔断'}</span>
                </button>
              </div>
            </div>

            <div className="flex items-center gap-1.5 sm:gap-2">
              {/* User Account & VIP Status Pill */}
              {currentUser ? (
                <div className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1 rounded-full surface-subtle max-w-full">
                  <button
                    onClick={() => setActiveTab('membership')}
                    className="flex items-center gap-1.5 text-left hover:opacity-85 transition min-w-0"
                    title="点击进入会员中心"
                  >
                    <User className="w-3.5 h-3.5 text-main shrink-0" strokeWidth={1.75} />
                    <span className="font-mono font-bold text-main truncate max-w-[78px] sm:max-w-[140px]">
                      {currentUser.username}
                    </span>
                    <span
                      className={`px-1.5 sm:px-2 py-0.5 rounded-full text-[10px] font-bold shrink-0 ${
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
                    className="px-2 py-0.5 rounded-full text-[10px] surface-card text-sub hover:text-main font-semibold transition shrink-0"
                    title="切换账号"
                  >
                    切换
                  </button>
                  <button
                    onClick={handleLogout}
                    className="p-1 rounded-full text-dim hover:text-rose-500 transition shrink-0"
                    title="退出当前账号"
                  >
                    <LogOut className="w-3.5 h-3.5" strokeWidth={1.75} />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  <button onClick={() => openAuthModal('login')} className="btn-soft-pill px-3 sm:px-4 py-1.5 text-xs">
                    登录
                  </button>
                  <button onClick={() => openAuthModal('register')} className="btn-primary-pill px-3 sm:px-4 py-1.5 text-xs">
                    注册
                  </button>
                </div>
              )}

              {/* Theme Switcher */}
              <button
                onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
                className="w-8 h-8 rounded-full surface-subtle flex items-center justify-center hover:opacity-85 text-main transition shrink-0"
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
        </div>

        {/* Row 2: Pill Navigation Tabs (Left) | Context Bar (Right) */}
        <div className="max-w-[1520px] mx-auto mt-2.5 sm:mt-3 pt-2 sm:pt-2.5 border-t border-line flex flex-col xl:flex-row xl:items-center xl:justify-between gap-2">
          <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar sm:flex-wrap pb-0.5 sm:pb-0">
            {[
              { id: 'radar', label: '脉冲狙击台', icon: Radar, visible: true },
              { id: 'watchlist', label: `策略参数矩阵 (${strategies.length})`, icon: Sliders, visible: true },
              { id: 'exchanges', label: '交易所与风控', icon: Shield, visible: Boolean(currentUser) },
              { id: 'analytics', label: '盈亏统计与诊断', icon: BarChart2, visible: true },
              {
                id: 'membership',
                label: currentUser?.is_vip ? '会员中心 (VIP)' : '购买会员 / 解锁实盘',
                icon: CreditCard,
                visible: true,
              },
              {
                id: 'admin',
                label: '后台管理',
                icon: Users,
                visible: Boolean(currentUser?.is_admin),
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
                    className={`flex items-center gap-1.5 px-3.5 sm:px-4 py-1.5 rounded-full text-xs font-semibold transition whitespace-nowrap shrink-0 ${
                      active ? 'btn-primary-pill shadow-sm' : 'text-sub hover:text-main hover:bg-black/5'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                    {tab.label}
                  </button>
                );
              })}
          </div>

          {/* Right Context Bar: Live Connection Summary OR Shadow Pulse Test Buttons */}
          {isLiveMode ? (
            <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 text-[11px] sm:text-xs">
              <span className="px-2.5 sm:px-3 py-1 rounded-full surface-subtle text-emerald-600 dark:text-emerald-400 font-semibold whitespace-nowrap">
                Binance {liveStatus.binance_ws_connected ? '已连接' : '重连中'} · OKX{' '}
                {liveStatus.okx_ws_connected ? '已连接' : '重连中'}
              </span>
              {liveStatus.follower_auth_status === 'VERIFIED' && (
                <>
                  <span
                    className={`px-2.5 sm:px-3 py-1 rounded-full text-[11px] font-bold whitespace-nowrap ${
                      liveStatus.okx_pos_mode === 'long_short_mode'
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                        : 'bg-amber-500/15 text-amber-600 dark:text-amber-300'
                    }`}
                  >
                    {liveStatus.okx_pos_mode === 'long_short_mode' ? 'OKX 双向持仓' : 'OKX 单向持仓'}
                  </span>
                  <span
                    title="OKX 私有 WebSocket 订单/条件单实时推送通道（毫秒级感知 Maker 止盈与条件止损成交）"
                    className={`px-2.5 sm:px-3 py-1 rounded-full text-[11px] font-bold whitespace-nowrap ${
                      liveStatus.okx_private_ws_connected
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                        : 'bg-slate-500/15 text-dim'
                    }`}
                  >
                    私有推单WS {liveStatus.okx_private_ws_connected ? '实时在线' : '轮询兜底'}
                  </span>
                  {Number(liveStatus.usdt_avail_usdt || 0) > 0 && (
                    <span className="px-2.5 sm:px-3 py-1 rounded-full surface-subtle text-[11px] font-mono font-semibold text-main whitespace-nowrap">
                      可用 {Number(liveStatus.usdt_avail_usdt).toFixed(2)}U
                    </span>
                  )}
                </>
              )}
              <span
                onClick={() => setActiveTab('exchanges')}
                className="cursor-pointer px-2.5 sm:px-3 py-1 rounded-full surface-subtle text-main font-medium hover:opacity-85 transition truncate max-w-full sm:max-w-[340px]"
              >
                {liveStatus.follower_auth_message || '点击配置执行端 API'}
              </span>
            </div>
          ) : (
            activeTab === 'radar' && (
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] sm:text-xs">
                <span className="text-dim mr-0.5">脉冲注入 ({selectedSymbol.split('/')[0]}):</span>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.0085, 'CONVERGE_TP', '多头拉升收敛止盈')}
                  className="btn-soft-pill px-2.5 sm:px-3 py-1 text-xs text-emerald-600 dark:text-emerald-400"
                >
                  <ArrowUpRight className="w-3.5 h-3.5" strokeWidth={2} />
                  拉升止盈
                </button>
                <button
                  onClick={() => handleInjectPulse('SHORT', 0.0085, 'CONVERGE_TP', '空头下挫收敛止盈')}
                  className="btn-soft-pill px-2.5 sm:px-3 py-1 text-xs text-rose-600 dark:text-rose-400"
                >
                  <ArrowDownRight className="w-3.5 h-3.5" strokeWidth={2} />
                  下挫止盈
                </button>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.0082, 'REVERSAL_SL', '主所假突破反转止损')}
                  className="btn-soft-pill px-2.5 sm:px-3 py-1 text-xs text-amber-600 dark:text-amber-400"
                >
                  <RefreshCw className="w-3 h-3" strokeWidth={2} />
                  反转止损
                </button>
                <button
                  onClick={() => handleInjectPulse('LONG', 0.008, 'MM_WITHDRAW_MISS', '做市商抢先撤单未成交')}
                  className="btn-soft-pill px-2.5 sm:px-3 py-1 text-xs"
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
      <main className="flex-1 max-w-[1520px] w-full mx-auto px-3 sm:px-5 lg:px-6 py-4 sm:py-6 space-y-4 sm:space-y-5">
        {/* VIP REQUIREMENT BANNER WHEN LOGGED IN AS NON-VIP IN LIVE MODE */}
        {isLiveMode && currentUser && !currentUser.can_use_live && (
          <div className="surface-card p-4 sm:p-6 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
            <div className="flex items-center gap-3 sm:gap-3.5 min-w-0">
              <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 shrink-0 text-amber-500">
                <Lock className="w-5 h-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-sm sm:text-base font-bold text-main">
                  当前账号【{currentUser.username}】尚未开通 VIP 会员
                </div>
                <div className="text-xs text-dim mt-1">
                  您已拥有实盘通道实时行情查看权限；开通 VIP 会员后即可绑定交易所 API 并启动实盘自动狙击。
                </div>
              </div>
            </div>
            <button onClick={() => setActiveTab('membership')} className="btn-primary-pill w-full sm:w-auto px-6 py-2.5 text-xs">
              立即开通 VIP 会员
            </button>
          </div>
        )}

        {/* Top 4 KPI Summary Cards (Matching Screenshot 1 Stat Cards 1:1) */}
        {(activeTab === 'radar' || activeTab === 'analytics') && (
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2 px-0.5">
              <div className="flex flex-wrap items-center gap-2 text-xs text-sub">
                <span className="font-semibold text-main">
                  {isLiveMode ? '实盘核心指标统计' : '模拟核心指标统计'}
                </span>
                <span className="text-[11px] text-dim hidden sm:inline">
                  净收益 · 抢单成交 · 闭环胜率 · 触发信号
                </span>
                {Number(stats.stats_reset_at_ms || 0) > 0 && (
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20">
                    统计起点 {formatResetTime(stats.stats_reset_at_ms)}
                  </span>
                )}
              </div>
              {currentUser && (
                <div className="flex items-center gap-1.5">
                  {confirmResetStats ? (
                    <>
                      <button
                        type="button"
                        onClick={handleResetStats}
                        disabled={resettingStats}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-rose-500 text-white hover:bg-rose-600 disabled:opacity-50 transition-colors cursor-pointer"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${resettingStats ? 'animate-spin' : ''}`} />
                        {resettingStats ? '清零中...' : '确认清零并重新统计'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmResetStats(false)}
                        disabled={resettingStats}
                        className="px-2 py-1 rounded-lg text-xs text-sub border border-line bg-white/80 dark:bg-slate-800/80 hover:text-main transition-colors cursor-pointer"
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmResetStats(true)}
                      disabled={resettingStats}
                      title="一键清零净收益、抢单成交、闭环胜率、触发信号统计并从 0 重新累计（不会平仓或影响在途持仓）"
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border border-line bg-white/80 dark:bg-slate-800/80 text-sub hover:text-main hover:bg-sky-500/10 hover:border-sky-500/40 disabled:opacity-50 transition-colors cursor-pointer"
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${resettingStats ? 'animate-spin' : ''}`} />
                      <span>一键刷新清零统计</span>
                    </button>
                  )}
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 lg:gap-5">
            <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4 min-w-0">
              <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 shrink-0">
                <DollarSign className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div
                  className={`text-lg sm:text-2xl font-bold font-mono leading-tight truncate ${
                    (stats.total_net_pnl ?? 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'
                  }`}
                >
                  {(stats.total_net_pnl ?? 0) >= 0
                    ? `+$${Number(stats.total_net_pnl ?? 0).toFixed(2)}`
                    : `-$${Math.abs(Number(stats.total_net_pnl ?? 0)).toFixed(2)}`}
                </div>
                <div className="text-[11px] sm:text-xs text-dim mt-0.5 sm:mt-1 truncate">
                  {isLiveMode ? '实盘净收益' : '模拟净收益'} (毛{' '}
                  {(stats.total_gross_pnl ?? 0) >= 0
                    ? `$${Number(stats.total_gross_pnl ?? 0).toFixed(2)}`
                    : `-$${Math.abs(Number(stats.total_gross_pnl ?? 0)).toFixed(2)}`}
                  )
                </div>
                {positionPnlSummary.total_positions > 0 && (
                  <div className="text-[10px] sm:text-[11px] font-mono mt-0.5 truncate flex items-center gap-1.5">
                    <span className="text-dim">持仓浮盈亏:</span>
                    <span
                      className={`font-semibold ${
                        positionPnlSummary.net_unrealized_usdt >= 0 ? 'text-emerald-500' : 'text-rose-500'
                      }`}
                    >
                      {formatUsdtSigned(positionPnlSummary.net_unrealized_usdt)}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4 min-w-0">
              <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 shrink-0">
                <Activity className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-2xl font-bold font-mono text-main leading-tight">
                  {stats.fill_rate_pct ?? 0}%
                </div>
                <div className="text-[11px] sm:text-xs text-dim mt-0.5 sm:mt-1 truncate">
                  抢单成交 ({stats.ioc_filled_count ?? 0}成/{stats.ioc_missed_count ?? 0}撤)
                </div>
              </div>
            </div>

            <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4 min-w-0">
              <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 shrink-0">
                <Percent className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-2xl font-bold font-mono text-main leading-tight">
                  {stats.win_rate_pct ?? 0}%
                </div>
                <div className="text-[11px] sm:text-xs text-dim mt-0.5 sm:mt-1 truncate">
                  闭环胜率 ({stats.win_count ?? 0}盈/{stats.loss_count ?? 0}亏)
                </div>
              </div>
            </div>

            <div className="surface-card p-3.5 sm:p-5 lg:p-6 flex items-center gap-2.5 sm:gap-4 min-w-0">
              <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 shrink-0">
                <Wallet className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <div className="text-lg sm:text-2xl font-bold font-mono text-main leading-tight">
                  {stats.total_signals ?? 0}
                </div>
                <div className="text-[11px] sm:text-xs text-dim mt-0.5 sm:mt-1 truncate">
                  触发信号 · 在途 {(snapshot.active_positions || []).length}/
                  {snapshot.risk?.max_concurrent_positions ?? 2} 仓
                </div>
              </div>
            </div>
            </div>
          </div>
        )}

        {/* ==================== VIEW 1: LIVE RADAR DASHBOARD ==================== */}
        {activeTab === 'radar' && (
          <div className="space-y-4 sm:space-y-5">
            {/* Mobile Quick Coin Jump Strip */}
            {strategies.length > 0 && (
              <div className="sm:hidden flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-0.5">
                {strategies.map((s) => {
                  const active = s.symbol === selectedSymbol;
                  return (
                    <button
                      key={`mob-chip-${s.symbol}`}
                      type="button"
                      onClick={() => setSelectedSymbol(s.symbol)}
                      className={`px-3 py-1 rounded-full text-[11px] font-mono font-bold whitespace-nowrap shrink-0 transition ${
                        active ? 'btn-primary-pill shadow-sm' : 'surface-subtle text-sub'
                      }`}
                    >
                      {s.symbol.split('/')[0]}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Monitored Contracts Grid (Mobile: horizontal snap carousel; Tablet/Desktop: responsive grid) */}
            <div className="flex overflow-x-auto snap-x snap-mandatory gap-3 pb-1 no-scrollbar sm:overflow-visible sm:pb-0 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 sm:gap-4 lg:gap-5">
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
                    className={`w-[80vw] max-w-[295px] shrink-0 snap-start sm:w-auto sm:max-w-none cursor-pointer surface-card p-4 sm:p-5 lg:p-6 transition-all ${
                      isSelected ? 'ring-2 ring-[#0f1419] dark:ring-sky-400' : 'hover:opacity-95'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-3 sm:mb-3.5">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="font-mono font-bold text-sm sm:text-base text-main truncate">
                          {s.symbol.replace(':USDT', '')}
                        </span>
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full font-bold shrink-0 ${strategyBadgeClass(
                            s.strategy_type || 'LEAD_LAG'
                          )}`}
                        >
                          {STRATEGY_LABEL_MAP[s.strategy_type || 'LEAD_LAG'] || s.strategy_type}
                        </span>
                      </div>
                      <span
                        className={`px-2 sm:px-2.5 py-0.5 rounded-full text-[10px] sm:text-[11px] font-semibold shrink-0 ${
                          stateKey === 'IDLE' || stateKey === 'GRID_WATCH'
                            ? 'surface-subtle text-dim'
                            : stateKey === 'HOLDING' || stateKey === 'GRID_HOLDING'
                            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                            : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                        }`}
                      >
                        {STATE_LABEL_MAP[stateKey] || stateKey}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 sm:gap-2.5 text-xs font-mono">
                      <div className="surface-subtle p-2.5 sm:p-3 min-w-0">
                        <div className="text-[11px] text-dim font-sans">主所 (Binance)</div>
                        <div className="text-main font-bold text-xs sm:text-sm mt-1 truncate">
                          {t.leader_mid ? Number(t.leader_mid).toFixed(prec) : '--'}
                        </div>
                      </div>
                      <div className="surface-subtle p-2.5 sm:p-3 min-w-0">
                        <div className="text-[11px] text-dim font-sans">从所 (OKX)</div>
                        <div className="text-main font-bold text-xs sm:text-sm mt-1 truncate">
                          {t.follower_mid ? Number(t.follower_mid).toFixed(prec) : '--'}
                        </div>
                      </div>
                    </div>

                    {/* Visual Pulse Progress Bar */}
                    <div className="mt-3 sm:mt-3.5 pt-2.5 sm:pt-3 border-t border-line space-y-1.5">
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
            <div className="surface-card p-4 sm:p-6 lg:p-7">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4 sm:mb-5">
                <div className="flex items-center gap-2.5">
                  <Cpu className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                  <h3 className="text-sm sm:text-base font-bold text-main">
                    毫秒级持仓状态机 ({selectedSymbol.replace(':USDT', '')})
                  </h3>
                </div>
                <span className="text-[11px] sm:text-xs text-dim font-mono">
                  净利 ≥ {((currentStrategy.take_profit_pct ?? 0.0025) * 100).toFixed(2)}% · 窗口{' '}
                  {currentStrategy.window_ms ?? 150}ms · 阈值{' '}
                  {((currentStrategy.min_jump_pct ?? 0.006) * 100).toFixed(2)}%
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 sm:gap-4">
                {STATE_PIPELINE.map((st) => {
                  const activeState = currentTelemetry.state || 'IDLE';
                  const isCurrent = activeState === st.key;
                  return (
                    <div key={st.key} className="flex items-center gap-2.5 sm:gap-3">
                      <div
                        className={`w-9 h-9 sm:w-11 sm:h-11 rounded-xl sm:rounded-2xl flex items-center justify-center font-mono text-[11px] sm:text-xs font-bold shrink-0 transition ${
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

              {/* Active Position Status Bar: Live Exchange Positions & Strategy Takeover */}
              {((snapshot.active_positions || []).length > 0 || isLiveMode) && (
                <div className="mt-4 sm:mt-5 pt-3.5 sm:pt-4 border-t border-line flex flex-col gap-3 surface-subtle px-3.5 sm:px-5 py-3.5 rounded-2xl">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-semibold text-main">
                        实盘在途持仓与策略接管监控 ({(snapshot.active_positions || []).length})
                      </span>
                      <span className="text-[11px] text-slate-500">
                        重启/暂停恢复时自动同步 OKX 持仓并按当前策略参数恢复止盈止损监控
                      </span>
                    </div>
                    {currentUser && (
                      <button
                        type="button"
                        onClick={handleSyncPositions}
                        disabled={syncingPositions}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium border border-line bg-white/80 dark:bg-slate-800/80 text-main hover:bg-sky-500/10 hover:border-sky-500/40 disabled:opacity-50 transition-colors cursor-pointer"
                      >
                        {syncingPositions ? '同步中...' : '同步交易所持仓'}
                      </button>
                    )}
                  </div>

                  {/* Real-Time Unrealized Profit & Loss Total Summary Strip */}
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-2.5">
                    <div className="px-3 py-2 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] flex flex-col gap-0.5 min-w-0">
                      <div className="flex items-center justify-between gap-1 text-[11px] text-sub">
                        <span className="truncate">持仓实时盈利总和</span>
                        <span className="font-mono text-[10px] text-emerald-600 dark:text-emerald-400 shrink-0">
                          {positionPnlSummary.profit_count} 仓
                        </span>
                      </div>
                      <div className="text-sm sm:text-base font-bold font-mono text-emerald-500 truncate">
                        {formatUsdtSigned(positionPnlSummary.total_profit_usdt)}
                      </div>
                    </div>

                    <div className="px-3 py-2 rounded-xl border border-rose-500/25 bg-rose-500/[0.06] flex flex-col gap-0.5 min-w-0">
                      <div className="flex items-center justify-between gap-1 text-[11px] text-sub">
                        <span className="truncate">持仓实时亏损总和</span>
                        <span className="font-mono text-[10px] text-rose-500 shrink-0">
                          {positionPnlSummary.loss_count} 仓
                        </span>
                      </div>
                      <div
                        className={`text-sm sm:text-base font-bold font-mono truncate ${
                          positionPnlSummary.total_loss_usdt < 0 ? 'text-rose-500' : 'text-dim'
                        }`}
                      >
                        {formatUsdtSigned(positionPnlSummary.total_loss_usdt, false)}
                      </div>
                    </div>

                    <div className="px-3 py-2 rounded-xl border border-line bg-white/70 dark:bg-slate-900/60 flex flex-col gap-0.5 min-w-0">
                      <div className="flex items-center justify-between gap-1 text-[11px] text-sub">
                        <span className="truncate">持仓实时净浮盈亏</span>
                        <span
                          className={`font-mono text-[10px] shrink-0 ${
                            positionPnlSummary.net_unrealized_usdt >= 0 ? 'text-emerald-500' : 'text-rose-500'
                          }`}
                        >
                          {positionPnlSummary.net_unrealized_pct >= 0 ? '+' : ''}
                          {Number(positionPnlSummary.net_unrealized_pct || 0).toFixed(2)}%
                        </span>
                      </div>
                      <div
                        className={`text-sm sm:text-base font-bold font-mono truncate ${
                          positionPnlSummary.net_unrealized_usdt >= 0 ? 'text-emerald-500' : 'text-rose-500'
                        }`}
                      >
                        {formatUsdtSigned(positionPnlSummary.net_unrealized_usdt)}
                      </div>
                    </div>

                    <div className="px-3 py-2 rounded-xl border border-line bg-white/70 dark:bg-slate-900/60 flex flex-col gap-0.5 min-w-0">
                      <div className="flex items-center justify-between gap-1 text-[11px] text-sub">
                        <span className="truncate">在途持仓总名义</span>
                        <span className="font-mono text-[10px] text-dim shrink-0">
                          {positionPnlSummary.total_positions} 合约
                          {(positionPnlSummary.strategy_slots || 0) > positionPnlSummary.total_positions
                            ? ` / ${positionPnlSummary.strategy_slots} 槽位`
                            : ''}
                        </span>
                      </div>
                      <div className="text-sm sm:text-base font-bold font-mono text-main truncate">
                        ${Number(positionPnlSummary.total_notional_usdt || 0).toFixed(2)}
                      </div>
                    </div>
                  </div>

                  {(snapshot.active_positions || []).length === 0 ? (
                    <div className="text-xs text-slate-500 py-1">
                      当前交易所账户暂无在途永续合约持仓（若刚在 OKX 手动开仓，可点击右上角“同步交易所持仓”立即刷新）
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {(snapshot.active_positions || []).map((pos) => {
                        const rowKey = `${pos.symbol}:${pos.side}:${pos.grid_slot_id || pos.strategy_type || 'POS'}`;
                        const stratType = pos.strategy_type || (pos.managed_by_strategy ? 'LEAD_LAG' : 'UNMANAGED');
                        const isTrendPos =
                          pos.grid_mode === 'TREND_BREAKOUT' || stratType === 'TREND_GRID';
                        const isReverseOscPos =
                          pos.grid_mode === 'REVERSE_OSCILLATING' || stratType === 'REVERSE_GRID';
                        const stratLabel = isGridStrategyType(stratType)
                          ? `${
                              isTrendPos
                                ? '顺势突破'
                                : isReverseOscPos
                                ? '多空震荡'
                                : stratType === 'ADAPTIVE_GRID'
                                ? 'VCP震荡'
                                : '传统网格'
                            }${
                              pos.grid_slot_id ? ` · ${pos.grid_slot_id}` : ''
                            }`
                          : stratType === 'LEAD_LAG'
                            ? 'Lead-Lag 狙击'
                            : '交易所未纳管持仓';
                        const netPct = Number(pos.unrealized_net_pct || 0);
                        const hasUplUsdt = pos.unrealized_usdt !== null && pos.unrealized_usdt !== undefined && Number.isFinite(Number(pos.unrealized_usdt));
                        const uplUsdt = hasUplUsdt ? Number(pos.unrealized_usdt) : 0;
                        const isProfit = (hasUplUsdt ? uplUsdt : netPct) >= 0;
                        const hasNotional = Number(pos.notional_usdt || 0) > 0;
                        const hasTp = Number(pos.tp_price || 0) > 0;
                        const hasTrailStop = Number(pos.trail_stop_price || 0) > 0;
                        const hasSl = Number(pos.sl_price || 0) > 0;
                        const hasMark = Number(pos.mark_price || 0) > 0;

                        return (
                          <div
                            key={rowKey}
                            className="flex flex-col xl:flex-row xl:items-center justify-between gap-2.5 px-3 py-2.5 rounded-xl border border-line bg-white/60 dark:bg-slate-900/50 text-xs font-mono"
                          >
                            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                              <button
                                type="button"
                                onClick={() => setSelectedSymbol(pos.symbol)}
                                className={`px-2.5 py-1 rounded-full font-semibold font-sans cursor-pointer transition-opacity hover:opacity-85 ${
                                  pos.side === 'LONG'
                                    ? 'bg-emerald-600 text-white'
                                    : 'bg-rose-600 text-white'
                                }`}
                                title="点击切换到该币种监控视图"
                              >
                                {pos.symbol} · {pos.side === 'LONG' ? '多' : '空'}
                                {Number(pos.leverage || 0) > 0 ? ` ${Number(pos.leverage)}x` : ''}
                              </button>

                              <span
                                className={`px-2 py-0.5 rounded-md font-sans text-[11px] font-medium border ${
                                  pos.managed_by_strategy
                                    ? 'bg-sky-500/10 text-sky-600 dark:text-sky-300 border-sky-500/25'
                                    : 'bg-amber-500/10 text-amber-600 dark:text-amber-300 border-amber-500/30'
                                }`}
                              >
                                {stratLabel}
                              </span>

                              {pos.recovered && (
                                <span className="px-2 py-0.5 rounded-md font-sans text-[11px] font-medium bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 border border-indigo-500/25">
                                  已恢复接管
                                </span>
                              )}

                              {pos.maker_tp_live && (
                                <span className="px-2 py-0.5 rounded-md font-sans text-[10px] font-semibold bg-emerald-500/12 text-emerald-600 dark:text-emerald-300 border border-emerald-500/25">
                                  Maker挂单止盈
                                </span>
                              )}

                              {pos.cond_sl_live && (
                                <span className="px-2 py-0.5 rounded-md font-sans text-[10px] font-semibold bg-amber-500/12 text-amber-600 dark:text-amber-300 border border-amber-500/25">
                                  OKX条件止损
                                </span>
                              )}

                              <span className="text-main">
                                均价: {formatPositionPrice(pos.entry_price, pos.symbol)}
                              </span>

                              {hasMark && (
                                <span className="text-slate-500">
                                  现价: {formatPositionPrice(pos.mark_price, pos.symbol)}
                                </span>
                              )}

                              {hasNotional && (
                                <span className="text-main">
                                  名义: {Number(pos.notional_usdt).toFixed(2)}U
                                  {Number(pos.contracts || 0) > 0 ? ` (${Number(pos.contracts)}张)` : ''}
                                </span>
                              )}

                              {hasTp ? (
                                <span className="text-emerald-500 font-semibold">
                                  止盈: {formatPositionPrice(pos.tp_price, pos.symbol)}
                                </span>
                              ) : hasTrailStop ? (
                                <span className="text-teal-500 font-semibold">
                                  追踪止盈: {formatPositionPrice(pos.trail_stop_price, pos.symbol)}
                                </span>
                              ) : null}

                              {hasSl && (
                                <span className="text-rose-500 font-semibold">
                                  止损: {formatPositionPrice(pos.sl_price, pos.symbol)}
                                </span>
                              )}

                              <span className="text-main">
                                浮盈:{' '}
                                <strong className={isProfit ? 'text-emerald-500' : 'text-rose-500'}>
                                  {hasUplUsdt ? `${uplUsdt >= 0 ? '+' : ''}${uplUsdt.toFixed(3)}U ` : ''}
                                  ({netPct >= 0 ? '+' : ''}{(netPct * 100).toFixed(2)}%)
                                </strong>
                              </span>
                            </div>

                            {currentUser && (
                              <div className="flex flex-wrap items-center gap-2 shrink-0 font-sans">
                                {!pos.managed_by_strategy && (
                                  <button
                                    type="button"
                                    onClick={() => handleAdoptGridPosition(pos.symbol)}
                                    className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-sky-500/15 text-sky-600 dark:text-sky-300 border border-sky-500/30 hover:bg-sky-500/25 transition-colors cursor-pointer"
                                  >
                                    + 纳入网格策略
                                  </button>
                                )}
                                <button
                                  type="button"
                                  disabled={closingPosKey === `${pos.symbol}:${pos.side || 'LONG'}:${pos.grid_slot_id || ''}`}
                                  onClick={() => handleClosePosition(pos)}
                                  className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-rose-500/10 text-rose-500 border border-rose-500/30 hover:bg-rose-500/20 disabled:opacity-50 transition-colors cursor-pointer"
                                >
                                  {closingPosKey === `${pos.symbol}:${pos.side || 'LONG'}:${pos.grid_slot_id || ''}` ? '平仓中...' : '平仓'}
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Chart + Real-Time Terminal Log */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-5">
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
              <div className="lg:col-span-5 surface-card p-4 sm:p-6 flex flex-col h-[340px] sm:h-[380px] lg:h-[420px]">
                <div className="flex flex-wrap items-center justify-between gap-2 pb-3 sm:pb-3.5 mb-3 sm:mb-3.5 border-b border-line">
                  <div className="flex items-center gap-2 sm:gap-2.5">
                    <div className="icon-box w-8 h-8 sm:w-9 sm:h-9">
                      <Terminal className="w-4 h-4 text-main" strokeWidth={1.75} />
                    </div>
                    <h3 className="text-sm sm:text-base font-bold text-main">
                      {isLiveMode ? '实盘执行与风控日志' : '毫秒级狙击日志'}
                    </h3>
                  </div>
                  <div className="flex items-center gap-1 rounded-full surface-subtle p-1 text-[11px] sm:text-xs">
                    {[
                      { id: 'ALL', label: '全部' },
                      { id: 'SIGNAL', label: '成交' },
                      { id: 'MISS', label: '撤单' },
                      { id: 'RISK', label: '风控' },
                    ].map((f) => (
                      <button
                        key={f.id}
                        onClick={() => setLogFilter(f.id)}
                        className={`px-2 sm:px-2.5 py-1 rounded-full font-semibold transition ${
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
          <div className="space-y-4 sm:space-y-5">
            {/* Top Title Banner Card (Matching Screenshot 1 & 3) */}
            <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
              <div>
                <h2 className="text-lg sm:text-xl font-bold text-main">策略参数矩阵与监控合约库</h2>
                <p className="text-xs sm:text-sm text-dim mt-1">
                  内置 {catalog.length} 个加密永续与标普500指数/成分股标的，支持按交易所或传统券商（富途/moomoo/IBKR/Schwab）选标的与独立配置策略参数
                </p>
              </div>
              <div className="flex items-center gap-1.5 text-xs overflow-x-auto no-scrollbar sm:flex-wrap w-full sm:w-auto pb-0.5 sm:pb-0">
                {[
                  { id: 'ALL', label: '全部热门' },
                  { id: '标普500', label: '标普500 (S&P 500)' },
                  { id: 'AI与DeFi中小盘', label: 'AI与DeFi中小盘' },
                  { id: '高弹性Meme与中小市值', label: '高弹性Meme' },
                  { id: '公链生态', label: '主流公链' },
                ].map((sec) => (
                  <button
                    key={sec.id}
                    onClick={() => setSelectedSector(sec.id)}
                    className={`px-3 sm:px-3.5 py-1.5 rounded-full font-semibold transition whitespace-nowrap shrink-0 ${
                      selectedSector === sec.id ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    {sec.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Batch Audit & One-Click Bulk Update Panel */}
            <div className="surface-card p-4 sm:p-5 space-y-3.5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Sliders className="w-4 h-4 text-main shrink-0" strokeWidth={1.9} />
                    <h3 className="text-sm font-bold text-main">存量参数体检与一键批量更新</h3>
                    <span className="text-[11px] font-medium px-2 py-0.5 rounded-full surface-subtle text-sub">
                      支持真实 Maker 挂单止盈省 60% 平仓费 · 价格止损 × 杠杆 = 保证金亏损%
                    </span>
                  </div>
                  <p className="text-xs text-dim mt-1">
                    一键检测并批量设置存量合约的名义金额、步长、止盈、止损、顺势追踪倍率与 Maker 挂单止盈开关；批量更新不强平在途持仓，空仓重锚后自动生效。
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleBatchSymbols('validate')}
                    disabled={batchLoading}
                    className="btn-soft-pill px-3.5 py-2 text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
                  >
                    <Shield className="w-3.5 h-3.5" strokeWidth={2} />
                    <span>{batchLoading ? '校验中...' : '一键校验存量参数'}</span>
                  </button>
                  {!batchConfirmApply ? (
                    <button
                      type="button"
                      onClick={() => setBatchConfirmApply(true)}
                      disabled={batchLoading}
                      className="btn-primary-pill px-3.5 py-2 text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <Check className="w-3.5 h-3.5" strokeWidth={2} />
                      <span>一键批量更新</span>
                    </button>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleBatchSymbols('apply')}
                        disabled={batchLoading}
                        className="px-3.5 py-2 rounded-full text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition flex items-center gap-1.5 disabled:opacity-50"
                      >
                        <Check className="w-3.5 h-3.5" strokeWidth={2} />
                        <span>{batchLoading ? '更新中...' : '确认批量更新'}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setBatchConfirmApply(false)}
                        disabled={batchLoading}
                        className="btn-soft-pill px-3 py-2 text-xs font-semibold"
                      >
                        取消
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5 items-end">
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">作用范围</label>
                  <select
                    value={batchForm.strategy_filter}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, strategy_filter: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-semibold text-main outline-none"
                  >
                    <option value="ALL_GRID">全部网格策略</option>
                    <option value="ADAPTIVE_GRID">仅 VCP 双模式</option>
                    <option value="TREND_GRID">仅顺势突破网格</option>
                    <option value="LONG_SHORT_GRID">仅传统震荡网格</option>
                    <option value="REVERSE_GRID">仅多空震荡网格 (反向)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">名义金额 (USDT)</label>
                  <input
                    type="number"
                    step="5"
                    min="5"
                    max="500000"
                    placeholder="留空不改 (如 20)"
                    value={batchForm.order_usdt_str}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, order_usdt_str: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">单边层数 (N)</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    max="30"
                    placeholder="留空不改 (如 3 或 5)"
                    value={batchForm.grid_levels_str}
                    onChange={(e) => updateBatchFieldWithLinkage('grid_levels_str', e.target.value)}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">目标步长 (%)</label>
                  <input
                    type="number"
                    step="0.05"
                    min="0.05"
                    max="20"
                    placeholder="留空不改"
                    value={batchForm.grid_step_pct_str}
                    onChange={(e) => updateBatchFieldWithLinkage('grid_step_pct_str', e.target.value)}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">目标止盈 (%)</label>
                  <input
                    type="number"
                    step="0.05"
                    min="0.05"
                    max="25"
                    placeholder="留空不改"
                    value={batchForm.grid_take_profit_pct_str}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, grid_take_profit_pct_str: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">区间总跨度 ±(%)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.5"
                    max="80"
                    placeholder="留空按层数自动匹配"
                    value={batchForm.grid_range_pct_str}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, grid_range_pct_str: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">
                    价格止损 (%)
                    {batchForm.grid_stop_loss_pct_str !== '' && Number(batchForm.grid_stop_loss_pct_str) > 0 && (
                      <span className="ml-1 text-amber-600 dark:text-amber-300 font-mono">
                        5x≈{(Number(batchForm.grid_stop_loss_pct_str) * 5).toFixed(1)}%
                      </span>
                    )}
                  </label>
                  <input
                    type="number"
                    step="0.2"
                    min="0.2"
                    max="90"
                    placeholder="留空不改 (如 4.5)"
                    value={batchForm.grid_stop_loss_pct_str}
                    onChange={(e) => updateBatchFieldWithLinkage('grid_stop_loss_pct_str', e.target.value)}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">顺势追踪倍率 (x)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.3"
                    max="10"
                    placeholder="留空不改 (如 2.2)"
                    value={batchForm.trend_trailing_mult_str}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, trend_trailing_mult_str: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-mono text-main outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">Maker挂单止盈</label>
                  <select
                    value={batchForm.use_post_only_tp_mode}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, use_post_only_tp_mode: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-semibold text-main outline-none"
                  >
                    <option value="keep">保持原状</option>
                    <option value="on">一键开启 (省费)</option>
                    <option value="off">关闭 (仅Taker)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-dim mb-1">条件止损触发价</label>
                  <select
                    value={batchForm.sl_trigger_px_type_mode}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, sl_trigger_px_type_mode: e.target.value }))}
                    className="w-full rounded-xl surface-subtle px-3 py-2 text-xs font-semibold text-main outline-none"
                  >
                    <option value="keep">保持原状</option>
                    <option value="mark">标记价 Mark (抗插针推荐)</option>
                    <option value="last">最新价 Last</option>
                  </select>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-4 pt-0.5">
                <label className="flex items-center gap-2 text-xs text-sub cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={batchForm.auto_link_batch}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, auto_link_batch: e.target.checked }))}
                    className="rounded accent-emerald-600"
                  />
                  <span>输入层数/步长自动联动区间与止盈</span>
                </label>
                <label className="flex items-center gap-2 text-xs text-sub cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={batchForm.auto_fix_anomalies}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, auto_fix_anomalies: e.target.checked }))}
                    className="rounded accent-emerald-600"
                  />
                  <span>自动修复倒挂/精度/区间匹配</span>
                </label>
                <label className="flex items-center gap-2 text-xs text-sub cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={batchForm.only_running}
                    onChange={(e) => setBatchForm((prev) => ({ ...prev, only_running: e.target.checked }))}
                    className="rounded accent-emerald-600"
                  />
                  <span>仅作用于运行中合约</span>
                </label>
                <span className="text-[11px] text-dim">
                  提示：价格止损 1.0% 在 5x 杠杆下对应保证金亏损 5.0%（传统 5 格网建议价格止损 ≥ 3.5%，或缩小格数至 3 格）
                </span>
              </div>

              {batchReport && (
                <div className="rounded-2xl border border-line surface-subtle p-3.5 space-y-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-bold text-main">
                        {batchReport.action === 'apply' ? '批量更新结果' : '存量参数体检报告'}
                      </span>
                      <span className="px-2 py-0.5 rounded-full bg-slate-500/15 text-main font-mono">
                        扫描 {batchReport.scanned_count ?? 0} 个
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded-full font-mono ${
                          (batchReport.anomaly_count ?? 0) > 0
                            ? 'bg-amber-500/15 text-amber-600 dark:text-amber-300'
                            : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300'
                        }`}
                      >
                        异常/待优化 {batchReport.anomaly_count ?? 0} 个
                      </span>
                      {batchReport.action === 'apply' && (
                        <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 font-mono">
                          已更新 {batchReport.updated_count ?? 0} 个
                          {(batchReport.open_deferred_count ?? 0) > 0
                            ? `（${batchReport.open_deferred_count} 个待空仓重锚生效）`
                            : ''}
                        </span>
                      )}
                      <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 font-mono">
                        Maker挂单双边 {Number(batchReport.maker_round_trip_fee_pct ?? 0.07).toFixed(3)}%
                      </span>
                      <span className="px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-300 font-mono">
                        2×Taker {Number(batchReport.grid_round_trip_fee_pct ?? 0.1).toFixed(3)}%
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setBatchReport(null)}
                      className="text-[11px] text-dim hover:text-main transition"
                    >
                      收起报告
                    </button>
                  </div>
                  <p className="text-xs text-sub">{batchReport.message}</p>
                  {Array.isArray(batchReport.items) &&
                    batchReport.items.some(
                      (it) => (it.issues && it.issues.length > 0) || (it.fixes_applied && it.fixes_applied.length > 0)
                    ) && (
                      <div className="max-h-48 overflow-y-auto divide-y divide-line rounded-xl border border-line bg-[var(--bg-card)]">
                        {batchReport.items
                          .filter(
                            (it) =>
                              (it.issues && it.issues.length > 0) ||
                              (it.fixes_applied && it.fixes_applied.length > 0)
                          )
                          .map((it) => (
                            <div
                              key={it.symbol}
                              onClick={() => setSelectedSymbol(it.symbol)}
                              className="px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-xs cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition"
                            >
                              <div className="flex flex-wrap items-center gap-2 min-w-0">
                                <span className="font-mono font-bold text-main">{it.symbol.replace('/USDT:USDT', '')}</span>
                                <span className="font-mono text-[11px] text-dim">
                                  {Number(it.order_usdt || 0).toFixed(0)}U · {it.grid_levels || 5}层 · 步长{' '}
                                  {(Number(it.grid_step_pct || 0) * 100).toFixed(2)}% · 区间 ±
                                  {(Number(it.grid_range_pct || 0) * 100).toFixed(1)}% · 止盈{' '}
                                  {(Number(it.grid_take_profit_pct || 0) * 100).toFixed(2)}% · 止损{' '}
                                  {(Number(it.grid_stop_loss_pct || 0) * 100).toFixed(1)}%
                                  {it.sl_roe_pct ? `(${it.leverage || 5}x≈${it.sl_roe_pct}%)` : ''}
                                </span>
                                {it.has_open_position && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-sky-500/15 text-sky-600 dark:text-sky-300">
                                    在途持仓
                                  </span>
                                )}
                              </div>
                              <div className="flex flex-wrap items-center gap-1.5">
                                {(it.issues || []).map((iss, idx) => (
                                  <span
                                    key={`i-${idx}`}
                                    className="px-2 py-0.5 rounded-full text-[11px] bg-amber-500/15 text-amber-700 dark:text-amber-300"
                                  >
                                    {iss}
                                  </span>
                                ))}
                                {(it.fixes_applied || []).map((fx, idx) => (
                                  <span
                                    key={`f-${idx}`}
                                    className="px-2 py-0.5 rounded-full text-[11px] bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                                  >
                                    已修复: {fx}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ))}
                      </div>
                    )}
                </div>
              )}
            </div>

            {/* Split Master-Detail Card (Matching Screenshot 3 `工单系统` 1:1) */}
            <div className="surface-card overflow-hidden grid grid-cols-1 lg:grid-cols-12 lg:min-h-[680px]">
              {/* Left Sidebar: Pill Search + Solid Black Add Button + Full-Height Monitored Contract List */}
              <div className="lg:col-span-4 lg:relative border-b lg:border-b-0 lg:border-r border-line">
                <div className="lg:absolute lg:inset-0 p-4 sm:p-6 flex flex-col">
                  <div className="shrink-0 space-y-3 sm:space-y-3.5">
                    {/* Exchange / Broker Selector for Picking Symbols */}
                    <div className="surface-subtle p-2.5 sm:p-3 rounded-2xl space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-bold text-main">选择交易所 / 券商选标的</span>
                        <select
                          value={selectedExchangeFilter}
                          onChange={(e) => {
                            const val = e.target.value;
                            setSelectedExchangeFilter(val);
                            if (TRADITIONAL_BROKER_IDS.has(val)) {
                              setSelectedSector('标普500');
                            }
                          }}
                          className="rounded-xl bg-[var(--bg-card)] border border-line px-2.5 py-1 text-xs font-semibold text-main outline-none"
                        >
                          <option value="ALL">全部交易所与券商</option>
                          <optgroup label="加密货币合约交易所">
                            {EXCHANGE_SELECT_OPTIONS.filter((o) => o.group === 'crypto').map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.label}
                              </option>
                            ))}
                          </optgroup>
                          <optgroup label="传统美股/标普500券商">
                            {EXCHANGE_SELECT_OPTIONS.filter((o) => o.group === 'broker').map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.label}
                              </option>
                            ))}
                          </optgroup>
                        </select>
                      </div>
                      <div className="flex items-center gap-1 overflow-x-auto no-scrollbar pb-0.5">
                        {[
                          { id: 'ALL', label: '全部' },
                          { id: 'okx', label: 'OKX' },
                          { id: 'binanceusdm', label: '币安' },
                          { id: 'futu', label: '富途牛牛' },
                          { id: 'moomoo', label: 'moomoo' },
                          { id: 'ibkr', label: 'IBKR' },
                          { id: 'schwab', label: 'Schwab' },
                        ].map((exOpt) => (
                          <button
                            key={exOpt.id}
                            type="button"
                            onClick={() => {
                              setSelectedExchangeFilter(exOpt.id);
                              if (TRADITIONAL_BROKER_IDS.has(exOpt.id)) {
                                setSelectedSector('标普500');
                              }
                            }}
                            className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold transition whitespace-nowrap shrink-0 ${
                              selectedExchangeFilter === exOpt.id
                                ? 'bg-[#0f1419] text-white dark:bg-sky-500'
                                : 'bg-[var(--bg-card)] text-sub hover:text-main'
                            }`}
                          >
                            {exOpt.label}
                          </button>
                        ))}
                      </div>
                    </div>

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
                        placeholder="搜索加密或标普500标的 (如 SPY / SPX / AAPL / NVDA / SUI)..."
                        className="w-full rounded-full surface-subtle pl-10 pr-4 py-2.5 text-base sm:text-xs font-mono text-main outline-none"
                      />
                    </div>

                    {/* Full-Width Solid Black Pill Button (Matching Screenshot 3 `+ 新建工单`) */}
                    <button
                      onClick={() => handleAddSymbolByCode(searchQuery)}
                      className="btn-primary-pill w-full py-2.5 sm:py-3 text-xs"
                    >
                      <Plus className="w-4 h-4" strokeWidth={2} />
                      <span>
                        + 添加并激活监控标的
                        {selectedExchangeFilter !== 'ALL'
                          ? ` (${EXCHANGE_SHORT_LABEL_MAP[selectedExchangeFilter] || selectedExchangeFilter.toUpperCase()})`
                          : ''}
                      </span>
                    </button>
                  </div>

                  {/* Monitored Contracts List — fills remaining full height on desktop down to Quick-Add footer */}
                  <div className="flex-1 min-h-0 flex flex-col pt-3.5 sm:pt-4">
                    <div className="shrink-0 flex items-center justify-between text-xs font-bold text-dim mb-2 px-1">
                      <span>当前监控中合约 ({strategies.length})</span>
                      <span className="text-[11px] font-mono">
                        持仓中{' '}
                        {
                          strategies.filter((s) => {
                            const hasPos = (snapshot?.active_positions || []).some(
                              (p) => p && p.symbol === s.symbol && Number(p.qty ?? p.contracts ?? 1) > 0
                            );
                            const tg = snapshot?.telemetry?.[s.symbol]?.grid;
                            const gCnt = (Number(tg?.open_long_count) || 0) + (Number(tg?.open_short_count) || 0);
                            return hasPos || gCnt > 0 || snapshot?.telemetry?.[s.symbol]?.state === 'HOLDING';
                          }).length
                        }{' '}
                        / 空仓{' '}
                        {
                          strategies.filter((s) => {
                            const hasPos = (snapshot?.active_positions || []).some(
                              (p) => p && p.symbol === s.symbol && Number(p.qty ?? p.contracts ?? 1) > 0
                            );
                            const tg = snapshot?.telemetry?.[s.symbol]?.grid;
                            const gCnt = (Number(tg?.open_long_count) || 0) + (Number(tg?.open_short_count) || 0);
                            return !(hasPos || gCnt > 0 || snapshot?.telemetry?.[s.symbol]?.state === 'HOLDING');
                          }).length
                        }
                      </span>
                    </div>
                    <div className="flex-1 min-h-0 space-y-2 max-h-[380px] sm:max-h-[440px] lg:max-h-none overflow-y-auto pr-1">
                      {strategies.map((item) => {
                        const active = item.symbol === currentStrategy.symbol;
                        const st = item.strategy_type || 'LEAD_LAG';
                        const isGrid = isGridStrategyType(st);
                        const symPositions = (snapshot?.active_positions || []).filter(
                          (p) => p && p.symbol === item.symbol && Number(p.qty ?? p.contracts ?? 1) > 0
                        );
                        const telGrid = snapshot?.telemetry?.[item.symbol]?.grid;
                        const gridOpenCnt =
                          (Number(telGrid?.open_long_count) || 0) + (Number(telGrid?.open_short_count) || 0);
                        const hasOpenPosition =
                          symPositions.length > 0 ||
                          gridOpenCnt > 0 ||
                          snapshot?.telemetry?.[item.symbol]?.state === 'HOLDING';
                        return (
                          <div
                            key={item.symbol}
                            onClick={() => setSelectedSymbol(item.symbol)}
                            className={`cursor-pointer p-3 sm:p-3.5 rounded-2xl transition flex items-center justify-between gap-2 ${
                              active
                                ? 'bg-[#0f1419] text-white shadow-sm'
                                : 'surface-subtle text-main hover:opacity-90'
                            }`}
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                <span className="font-mono font-bold text-sm truncate">{item.symbol}</span>
                                <span
                                  className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold shrink-0 ${
                                    active
                                      ? 'bg-white/15 text-white'
                                      : TRADITIONAL_BROKER_IDS.has(item.exchange_id)
                                      ? 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300'
                                      : 'bg-slate-500/15 text-sub'
                                  }`}
                                >
                                  {EXCHANGE_SHORT_LABEL_MAP[item.exchange_id || 'okx'] || (item.exchange_id || 'OKX').toUpperCase()}
                                </span>
                                <span
                                  className={`px-2 py-0.5 rounded-full text-[10px] font-bold shrink-0 ${strategyBadgeClass(
                                    st,
                                    active
                                  )}`}
                                >
                                  {STRATEGY_LABEL_MAP[st] || st}
                                </span>
                              </div>
                              <div className={`text-[11px] mt-0.5 truncate ${active ? 'text-white/75' : 'text-dim'}`}>
                                {isGrid
                                  ? `${GRID_MODE_LABEL_MAP[item.grid_side_mode || 'LONG_SHORT']} · ${item.grid_levels ?? 3}层 · ${item.order_usdt}U`
                                  : `${DIRECTION_LABEL_MAP[item.direction]} · ${item.order_usdt}U · ${item.leverage}x`}
                              </div>
                            </div>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span
                                className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                                  hasOpenPosition
                                    ? active
                                      ? 'bg-amber-500 text-white'
                                      : 'bg-amber-500/15 text-amber-600'
                                    : active
                                    ? 'bg-white/15 text-white/80'
                                    : 'bg-slate-500/15 text-dim'
                                }`}
                              >
                                {hasOpenPosition && (
                                  <span
                                    className={`w-1.5 h-1.5 rounded-full ${
                                      active ? 'bg-white' : 'bg-amber-500'
                                    } animate-pulse`}
                                  />
                                )}
                                {hasOpenPosition ? '持仓中' : '空仓'}
                              </span>
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                                  item.is_running
                                    ? active
                                      ? 'bg-emerald-500 text-white'
                                      : 'bg-emerald-500/15 text-emerald-600'
                                    : 'bg-slate-500/20 text-dim'
                                }`}
                              >
                                {item.is_running ? '运行中' : '已暂停'}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Quick-Add Recommended Contract Chips */}
                  <div className="shrink-0 pt-4 mt-4 border-t border-line">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <div className="text-xs font-bold text-dim">
                        一键快速添加推荐标的
                        {selectedExchangeFilter !== 'ALL'
                          ? ` · ${EXCHANGE_SHORT_LABEL_MAP[selectedExchangeFilter] || selectedExchangeFilter.toUpperCase()}`
                          : ''}
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          setSelectedSector((prev) => (prev === '标普500' ? 'ALL' : '标普500'))
                        }
                        className={`px-2 py-0.5 rounded-full text-[10px] font-bold transition ${
                          selectedSector === '标普500'
                            ? 'bg-indigo-600 text-white'
                            : 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300'
                        }`}
                      >
                        {selectedSector === '标普500' ? '显示全部板块' : '标普500专区'}
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
                      {filteredCatalog.slice(0, 18).map((item) => (
                        <button
                          key={item.symbol}
                          onClick={() => handleAddSymbolByCode(item.symbol)}
                          title={item.name_cn ? `${item.base} (${item.name_cn})` : item.symbol}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full hover:opacity-80 text-xs font-mono transition ${
                            item.sector === '标普500'
                              ? 'bg-indigo-500/12 border border-indigo-500/25 text-indigo-700 dark:text-indigo-300'
                              : 'surface-subtle text-main'
                          }`}
                        >
                          <Plus className="w-3 h-3 shrink-0" strokeWidth={2} />
                          <span className="font-bold">{item.base}</span>
                          {item.name_cn && (
                            <span className="text-[10px] font-sans opacity-80">
                              {item.name_cn.split(' ')[0]}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Right Detail Area: Selected Contract Strategy & Parameter Editor */}
              <div className="lg:col-span-8 p-4 sm:p-6 lg:p-7 flex flex-col justify-between">
                {currentStrategy && currentStrategy.symbol ? (
                  <div className="space-y-5 sm:space-y-6">
                    <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-3.5 sm:gap-4 pb-4 sm:pb-5 border-b border-line">
                      <div className="flex items-center gap-3 sm:gap-3.5 min-w-0">
                        <div className="icon-box w-10 h-10 sm:w-12 sm:h-12 font-mono font-bold text-xs sm:text-sm text-main">
                          {currentStrategy.symbol.split('/')[0]}
                        </div>
                        <div className="min-w-0">
                          <h3 className="text-base sm:text-lg font-bold font-mono text-main truncate">
                            {currentStrategy.symbol}
                          </h3>
                          <p className="text-xs text-dim mt-0.5 leading-snug">
                            {currentStrategy.strategy_type === 'TREND_GRID'
                              ? '顺势突破网格 · 涨破死区/布林带上轨追多、跌破下轨追空，递减金字塔重底仓 + 利弗莫尔保本上移止损 + 移动追踪止盈'
                              : currentStrategy.strategy_type === 'ADAPTIVE_GRID'
                              ? 'VCP 双模式智能网格 · 常规震荡跑传统网格吃70%震荡利润；15m/1H BBW 极度压缩时在空仓重锚自动切顺势突破抓30%大单边'
                              : currentStrategy.strategy_type === 'REVERSE_GRID'
                              ? '多空震荡网格 · 在中心价格上下反向对称铺网（跌破网格开空、涨破网格开多），单格固定止盈 + 穿越重装防重复堆仓'
                              : currentStrategy.strategy_type === 'LONG_SHORT_GRID'
                              ? '传统多空动态网格 · 在中心价格上下对称铺网（跌破接多、涨破开空），逐格固定止盈捕获震荡利润'
                              : '跨所脉冲狙击策略 · 独立毫秒级脉冲检测窗口、保留价滑点保护与动态收敛系数'}
                          </p>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 sm:flex sm:flex-wrap items-center gap-2 sm:gap-2.5 w-full sm:w-auto">
                        <select
                          value={currentStrategy.exchange_id || 'okx'}
                          onChange={(e) => updateCurrentStrategyField({ exchange_id: e.target.value })}
                          title="选择该标的绑定的执行交易所或传统券商通道"
                          className="input-field py-2 px-3 sm:px-3.5 text-sm sm:text-xs font-semibold sm:w-auto"
                        >
                          <optgroup label="加密货币合约交易所">
                            {EXCHANGE_SELECT_OPTIONS.filter((o) => o.group === 'crypto').map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.label}
                              </option>
                            ))}
                          </optgroup>
                          <optgroup label="传统美股/标普500券商">
                            {EXCHANGE_SELECT_OPTIONS.filter((o) => o.group === 'broker').map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.label}
                              </option>
                            ))}
                          </optgroup>
                        </select>
                        {isGridStrategyType(currentStrategy.strategy_type || 'LEAD_LAG') ? (
                          <select
                            value={currentStrategy.grid_side_mode || 'LONG_SHORT'}
                            onChange={(e) => updateCurrentStrategyField({ grid_side_mode: e.target.value })}
                            className="input-field py-2 px-3 sm:px-3.5 text-sm sm:text-xs font-semibold sm:w-auto"
                          >
                            <option value="LONG_SHORT">多空双开网格</option>
                            <option value="LONG_ONLY">仅做多网格</option>
                            <option value="SHORT_ONLY">仅做空网格</option>
                          </select>
                        ) : (
                          <select
                            value={currentStrategy.direction || 'BOTH'}
                            onChange={(e) => updateCurrentStrategyField({ direction: e.target.value })}
                            className="input-field py-2 px-3 sm:px-3.5 text-sm sm:text-xs font-semibold sm:w-auto"
                          >
                            <option value="BOTH">多空双向狙击</option>
                            <option value="LONG_ONLY">仅限做多</option>
                            <option value="SHORT_ONLY">仅限做空</option>
                          </select>
                        )}

                        <button
                          onClick={() => handleDeleteSymbol(currentStrategy.symbol)}
                          className="btn-soft-pill px-3.5 sm:px-4 py-2 text-xs text-rose-600"
                          title="移除该监控合约"
                        >
                          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                          <span>移除合约</span>
                        </button>

                        <button
                          onClick={() => handleSaveSymbol(currentStrategy)}
                          className="col-span-2 sm:col-span-1 btn-primary-pill px-6 py-2.5 text-xs"
                        >
                          保存策略参数
                        </button>
                      </div>
                    </div>

                    {/* Per-Symbol Strategy Type Selector (5 Strategies incl. Reverse Oscillating Grid, Trend & Adaptive Dual-Mode) */}
                    <div className="surface-subtle p-3.5 sm:p-4 flex flex-col gap-3">
                      <div className="text-xs">
                        <div className="font-bold text-main">该币种运行策略选择（支持传统网格 / 多空震荡网格 / 顺势突破 / VCP 双模式）</div>
                        <div className="text-[11px] text-dim mt-0.5">
                          可为不同币种独立指定策略，点击「保存策略参数」即时生效；选择「多空震荡网格」为跌破开空/涨破开多，选择「VCP 双模式」将在空仓重锚时按 15m/1H 布林带宽度自动切换
                        </div>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-2">
                        <button
                          type="button"
                          onClick={() => updateCurrentStrategyField({ strategy_type: 'LEAD_LAG' })}
                          className={`px-3.5 py-2.5 rounded-xl text-xs font-semibold transition text-left ${
                            (currentStrategy.strategy_type || 'LEAD_LAG') === 'LEAD_LAG'
                              ? 'btn-primary-pill shadow-sm'
                              : 'surface-card text-sub hover:text-main'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <Zap className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                            <span>跨所脉冲狙击</span>
                          </div>
                          <div className="text-[10px] opacity-75 font-normal mt-0.5">LEAD_LAG 高频价差</div>
                        </button>
                        <button
                          type="button"
                          onClick={() => updateCurrentStrategyField({ strategy_type: 'LONG_SHORT_GRID' })}
                          className={`px-3.5 py-2.5 rounded-xl text-xs font-semibold transition text-left ${
                            currentStrategy.strategy_type === 'LONG_SHORT_GRID'
                              ? 'bg-sky-600 text-white shadow-sm'
                              : 'surface-card text-sub hover:text-main'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <Grid className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                            <span>传统震荡网格</span>
                          </div>
                          <div className="text-[10px] opacity-80 font-normal mt-0.5">跌破接多 / 涨破开空</div>
                        </button>
                        <button
                          type="button"
                          onClick={() => updateCurrentStrategyField({ strategy_type: 'REVERSE_GRID' })}
                          className={`px-3.5 py-2.5 rounded-xl text-xs font-semibold transition text-left ${
                            currentStrategy.strategy_type === 'REVERSE_GRID'
                              ? 'bg-teal-600 text-white shadow-sm'
                              : 'surface-card text-sub hover:text-main'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <Layers className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                            <span>多空震荡网格</span>
                          </div>
                          <div className="text-[10px] opacity-80 font-normal mt-0.5">跌破开空 / 涨破开多</div>
                        </button>
                        <button
                          type="button"
                          onClick={() => updateCurrentStrategyField({ strategy_type: 'TREND_GRID' })}
                          className={`px-3.5 py-2.5 rounded-xl text-xs font-semibold transition text-left ${
                            currentStrategy.strategy_type === 'TREND_GRID'
                              ? 'bg-violet-600 text-white shadow-sm'
                              : 'surface-card text-sub hover:text-main'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <TrendingUp className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                            <span>顺势突破网格</span>
                          </div>
                          <div className="text-[10px] opacity-80 font-normal mt-0.5">涨破追多 / 追踪止盈</div>
                        </button>
                        <button
                          type="button"
                          onClick={() => updateCurrentStrategyField({ strategy_type: 'ADAPTIVE_GRID' })}
                          className={`px-3.5 py-2.5 rounded-xl text-xs font-semibold transition text-left ${
                            currentStrategy.strategy_type === 'ADAPTIVE_GRID'
                              ? 'bg-amber-600 text-white shadow-sm'
                              : 'surface-card text-sub hover:text-main'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <Activity className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                            <span>VCP 双模式切换</span>
                          </div>
                          <div className="text-[10px] opacity-80 font-normal mt-0.5">BBW极缩自动切顺势</div>
                        </button>
                      </div>
                    </div>

                    {isGridStrategyType(currentStrategy.strategy_type) ? (() => {
                      const symTel = snapshot?.telemetry?.[currentStrategy.symbol] || {};
                      const gridDiag = buildGridPreviewAndDiagnostics(
                        currentStrategy,
                        Number(symTel.okx_min_notional_usdt || 0),
                        Number(symTel.follower_mid || 0)
                      );
                      const fitLevelsByRange = Math.max(1, Math.min(30, Math.floor((gridDiag.rng + 1e-9) / gridDiag.step)));
                      const fitStepByRange = Number(Math.max(0.0005, gridDiag.rng / gridDiag.n).toFixed(4));
                      return (
                      <>
                        {/* Auto-Linkage Control Bar */}
                        <div className="flex flex-wrap items-center justify-between gap-2.5 px-3.5 py-2.5 rounded-2xl surface-subtle text-xs">
                          <label className="flex items-center gap-2 cursor-pointer select-none font-semibold text-main">
                            <input
                              type="checkbox"
                              checked={autoLinkGridParams}
                              onChange={(e) => setAutoLinkGridParams(e.target.checked)}
                              className="rounded accent-emerald-600"
                            />
                            <span>参数自动联动匹配</span>
                            <span className="font-normal text-[11px] text-dim hidden sm:inline">
                              （改层数/步长自动匹配区间±缓冲与止盈止损，关闭后可自由微调）
                            </span>
                          </label>
                          <button
                            type="button"
                            onClick={handleAutoAlignCurrentGrid}
                            className="btn-soft-pill px-3 py-1 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400"
                          >
                            一键规范对齐
                          </button>
                        </div>

                        {/* Common Grid Parameters */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 text-xs">
                          <div>
                            <label className="font-bold text-main block mb-2">
                              {currentStrategy.strategy_type === 'TREND_GRID'
                                ? '单格基准名义金额 (USDT · 递减分配)'
                                : '每格下单名义金额 (USDT)'}
                            </label>
                            <input
                              type="number"
                              value={currentStrategy.order_usdt ?? 10}
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
                            <label className="font-bold text-main block mb-2">单边网格层数 (建议顺势设3~5层)</label>
                            <input
                              type="number"
                              min={1}
                              max={30}
                              value={currentStrategy.grid_levels ?? 5}
                              onChange={(e) => updateGridParamWithLinkage('grid_levels', Number(e.target.value))}
                              className="input-field font-mono"
                            />
                            <div className="text-[10px] text-dim mt-1 leading-snug">
                              满网覆盖半径：{gridDiag.n}×{(gridDiag.step * 100).toFixed(2)}% ={' '}
                              <span className="font-mono font-bold text-main">
                                ±{(gridDiag.totalSpan * 100).toFixed(2)}%
                              </span>
                            </div>
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">每格触发间距 (%)</label>
                            <PercentNumberInput
                              step="0.05"
                              valueRatio={currentStrategy.grid_step_pct}
                              defaultRatio={0.006}
                              onChangeRatio={(v) => updateGridParamWithLinkage('grid_step_pct', v)}
                            />
                            <div className="text-[10px] text-dim mt-1 leading-snug">
                              默认 0.60%（低波/主流可调 0.35%~0.45%，高波山寨 0.60%~0.80%）
                            </div>
                          </div>

                          {currentStrategy.strategy_type !== 'TREND_GRID' && (
                            <div>
                              <label className="font-bold text-main block mb-2">
                                {currentStrategy.strategy_type === 'REVERSE_GRID'
                                  ? '多空震荡单格止盈目标 (%)'
                                  : '传统模式单格止盈目标 (%)'}
                              </label>
                              <PercentNumberInput
                                step="0.05"
                                valueRatio={currentStrategy.grid_take_profit_pct}
                                defaultRatio={0.0065}
                                onChangeRatio={(v) => updateCurrentStrategyField({ grid_take_profit_pct: v })}
                              />
                              <div className="text-[10px] text-dim mt-1 leading-snug">
                                标的价格涨幅 · {currentStrategy.leverage ?? 5}x 杠杆≈保证金收益{' '}
                                {((currentStrategy.grid_take_profit_pct ?? 0.0065) * (currentStrategy.leverage ?? 5) * 100).toFixed(1)}%
                              </div>
                            </div>
                          )}

                          <div>
                            <label className="font-bold text-main block mb-2">区间总跨度 ±(%)</label>
                            <PercentNumberInput
                              step="0.1"
                              valueRatio={currentStrategy.grid_range_pct}
                              defaultRatio={0.05}
                              onChangeRatio={(v) => updateGridParamWithLinkage('grid_range_pct', v)}
                            />
                            <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                              <button
                                type="button"
                                onClick={() => updateCurrentStrategyField({ grid_levels: fitLevelsByRange })}
                                className="px-2 py-0.5 rounded-full surface-subtle text-[10px] font-semibold text-sub hover:text-main transition"
                              >
                                按步长适配层数→{fitLevelsByRange}层
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  updateCurrentStrategyField({
                                    grid_step_pct: fitStepByRange,
                                    grid_take_profit_pct: Number(
                                      Math.max(
                                        Number(currentStrategy.grid_take_profit_pct ?? 0.0065),
                                        fitStepByRange + 0.0005
                                      ).toFixed(6)
                                    ),
                                  })
                                }
                                className="px-2 py-0.5 rounded-full surface-subtle text-[10px] font-semibold text-sub hover:text-main transition"
                              >
                                按层数适配步长→{(fitStepByRange * 100).toFixed(2)}%
                              </button>
                            </div>
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">
                              {currentStrategy.strategy_type === 'REVERSE_GRID'
                                ? '单格硬止损上限 (% · 相对开仓价)'
                                : '破网硬止损上限 (% · 标的价格)'}
                            </label>
                            <PercentNumberInput
                              step="0.2"
                              valueRatio={currentStrategy.grid_stop_loss_pct}
                              defaultRatio={0.08}
                              onChangeRatio={(v) => updateGridParamWithLinkage('grid_stop_loss_pct', v)}
                            />
                            <div className="text-[10px] text-amber-600 dark:text-amber-300 mt-1 leading-snug">
                              当前 {currentStrategy.leverage ?? 5}x 杠杆 → 保证金止损 ≈{' '}
                              {gridDiag.slRoePct.toFixed(1)}%
                              （如欲控制保证金止损 15%，此处设 {(15 / Math.max(1, currentStrategy.leverage ?? 5)).toFixed(2)}%）
                            </div>
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">OKX 条件止损触发价类型</label>
                            <select
                              value={currentStrategy.sl_trigger_px_type || 'mark'}
                              onChange={(e) => updateCurrentStrategyField({ sl_trigger_px_type: e.target.value })}
                              className="w-full rounded-xl surface-subtle px-3 py-2.5 text-xs font-semibold text-main outline-none"
                            >
                              <option value="mark">标记价 Mark（推荐 · 防单所插针误扫止损）</option>
                              <option value="last">最新成交价 Last（更贴近盘口瞬时价）</option>
                            </select>
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">IOC 容忍滑点上限 (%)</label>
                            <PercentNumberInput
                              step="0.01"
                              valueRatio={currentStrategy.slippage_pct}
                              defaultRatio={0.0005}
                              onChangeRatio={(v) => updateCurrentStrategyField({ slippage_pct: v })}
                            />
                          </div>
                        </div>

                        {/* Live Risk, Thinnest-Leg & Per-Level Preview Panel */}
                        <div className="rounded-2xl border border-line surface-subtle p-3.5 sm:p-4 space-y-3 text-xs">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-bold text-main flex items-center gap-1.5">
                              <BarChart2 className="w-3.5 h-3.5 shrink-0 text-sub" strokeWidth={1.75} />
                              <span>实时风险、最薄层门槛与逐层铺网预览</span>
                            </div>
                            {gridDiag.alerts.length === 0 ? (
                              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                                <Check className="w-3 h-3 shrink-0" strokeWidth={2} />
                                <span>参数逻辑校验全部通过</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400">
                                <AlertTriangle className="w-3 h-3 shrink-0" strokeWidth={2} />
                                <span>发现 {gridDiag.alerts.length} 项参数冲突/风险</span>
                              </span>
                            )}
                          </div>

                          {gridDiag.alerts.length > 0 && (
                            <div className="space-y-1.5">
                              {gridDiag.alerts.map((al, idx) => (
                                <div
                                  key={idx}
                                  className={`flex items-start gap-1.5 px-3 py-1.5 rounded-xl text-[11px] font-medium ${
                                    al.severity === 'error'
                                      ? 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border border-rose-500/25'
                                      : 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/25'
                                  }`}
                                >
                                  {al.severity === 'error' ? (
                                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" strokeWidth={1.75} />
                                  ) : (
                                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" strokeWidth={1.75} />
                                  )}
                                  <span>{al.msg}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
                            <div className="rounded-xl surface-card px-3 py-2.5">
                              <div className="text-[10px] text-dim">单边满格名义 / 保证金</div>
                              <div className="font-mono font-bold text-main mt-0.5">
                                {gridDiag.sideBudget.toFixed(0)}U / {gridDiag.sideMargin.toFixed(1)}U
                              </div>
                            </div>
                            <div className="rounded-xl surface-card px-3 py-2.5">
                              <div className="text-[10px] text-dim">覆盖半径 vs 区间边界</div>
                              <div className="font-mono font-bold text-main mt-0.5">
                                ±{(gridDiag.totalSpan * 100).toFixed(2)}% / ±{(gridDiag.rng * 100).toFixed(1)}%
                              </div>
                            </div>
                            <div className="rounded-xl surface-card px-3 py-2.5">
                              <div className="text-[10px] text-dim">最坏单边满格硬止损估算</div>
                              <div className="font-mono font-bold text-rose-600 dark:text-rose-400 mt-0.5">
                                ≈ -{gridDiag.worstSlLossUsdt.toFixed(2)} USDT
                              </div>
                            </div>
                            <div className="rounded-xl surface-card px-3 py-2.5">
                              <div className="text-[10px] text-dim">
                                最薄单层 ({gridDiag.thinnestLeg.slot}) vs OKX最小额
                              </div>
                              <div
                                className={`font-mono font-bold mt-0.5 ${
                                  gridDiag.okxMinNotional > 0 && gridDiag.thinnestLeg.usdt < gridDiag.okxMinNotional * 1.02
                                    ? 'text-rose-600 dark:text-rose-400'
                                    : 'text-emerald-600 dark:text-emerald-400'
                                }`}
                              >
                                {gridDiag.thinnestLeg.usdt.toFixed(1)}U /{' '}
                                {gridDiag.okxMinNotional > 0 ? `${gridDiag.okxMinNotional.toFixed(2)}U` : '待行情'}
                              </div>
                            </div>
                          </div>

                          <div className="flex flex-wrap gap-1.5 pt-0.5">
                            {gridDiag.legs.slice(0, 10).map((lg) => (
                              <span
                                key={lg.slot}
                                className={`px-2.5 py-1 rounded-full font-mono text-[11px] font-semibold ${
                                  lg.belowMin || lg.clippedInMr
                                    ? 'bg-rose-500/15 text-rose-600 dark:text-rose-300 border border-rose-500/30'
                                    : 'surface-card text-main border border-line'
                                }`}
                              >
                                {lg.slot}: {lg.usdt.toFixed(1)}U
                                {(currentStrategy.strategy_type === 'TREND_GRID' ||
                                  currentStrategy.strategy_type === 'ADAPTIVE_GRID') &&
                                (currentStrategy.trend_pyramid_decay ?? true)
                                  ? ` (${lg.weightPct}%)`
                                  : ` (±${(lg.mrDistPct * 100).toFixed(2)}%)`}
                                {lg.clippedInMr ? ' [被裁切]' : lg.belowMin ? ' [不足最小额]' : ''}
                              </span>
                            ))}
                            {gridDiag.legs.length > 10 && (
                              <span className="px-2 py-1 text-[11px] text-dim">
                                ...共 {gridDiag.legs.length} 层
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Trend Breakout & VCP Adaptive Parameters */}
                        {(currentStrategy.strategy_type === 'TREND_GRID' ||
                          currentStrategy.strategy_type === 'ADAPTIVE_GRID') && (
                          <div className="p-4 rounded-2xl border border-violet-500/25 bg-violet-500/[0.04] space-y-4 text-xs">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div>
                                <div className="font-bold text-main flex items-center gap-1.5">
                                  <TrendingUp className="w-3.5 h-3.5 shrink-0 text-violet-500" strokeWidth={1.75} />
                                  <span>顺势突破（反向网格）与 VCP 波动率切换参数</span>
                                </div>
                                <div className="text-[11px] text-dim mt-0.5">
                                  取消固定止盈封顶，使用移动追踪止盈一键全平盈利槽位；每加一格自动将前序仓位止损上移至保本/上一格位
                                </div>
                              </div>
                            </div>

                            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3.5">
                              <div>
                                <label className="font-bold text-main block mb-1.5">首格缓冲死区倍数 (×间距)</label>
                                <input
                                  type="number"
                                  step="0.25"
                                  min={0.5}
                                  max={10}
                                  value={currentStrategy.trend_dead_zone_mult ?? 1.25}
                                  onChange={(e) =>
                                    updateCurrentStrategyField({ trend_dead_zone_mult: Number(e.target.value) })
                                  }
                                  className="input-field font-mono"
                                />
                                <div className="text-[10px] text-dim mt-1 leading-snug">
                                  L1 ≥ 中枢 ±{(
                                    (currentStrategy.trend_dead_zone_mult ?? 1.25) *
                                    (currentStrategy.grid_step_pct ?? 0.008) *
                                    100
                                  ).toFixed(2)}%，过滤盘整假突破（默认 1.25x）
                                </div>
                              </div>

                              <div>
                                <label className="font-bold text-main block mb-1.5">移动追踪回撤倍数 (×间距)</label>
                                <input
                                  type="number"
                                  step="0.1"
                                  min={0.3}
                                  max={10}
                                  value={currentStrategy.trend_trailing_mult ?? 1.25}
                                  onChange={(e) =>
                                    updateCurrentStrategyField({ trend_trailing_mult: Number(e.target.value) })
                                  }
                                  className="input-field font-mono"
                                />
                                <div className="text-[10px] text-dim mt-1 leading-snug">
                                  极值回撤 {(
                                    (currentStrategy.trend_trailing_mult ?? 1.25) *
                                    (currentStrategy.grid_step_pct ?? 0.008) *
                                    100
                                  ).toFixed(2)}% 全平盈利槽（内置组合均价 25% 锁利保护 · 默认 1.25x）
                                </div>
                              </div>

                              <div>
                                <label className="font-bold text-main block mb-1.5">VCP / BBW 检测 K 线周期</label>
                                <select
                                  value={currentStrategy.bbw_timeframe || '15m'}
                                  onChange={(e) => updateCurrentStrategyField({ bbw_timeframe: e.target.value })}
                                  className="input-field font-semibold"
                                >
                                  <option value="15m">15 分钟 (15m · 灵敏)</option>
                                  <option value="1H">1 小时 (1H · 稳健)</option>
                                </select>
                              </div>

                              <div>
                                <label className="font-bold text-main block mb-1.5">BBW 极度压缩分位阈值 (%)</label>
                                <input
                                  type="number"
                                  step="1"
                                  min={1}
                                  max={50}
                                  value={currentStrategy.bbw_squeeze_percentile ?? 20}
                                  onChange={(e) =>
                                    updateCurrentStrategyField({ bbw_squeeze_percentile: Number(e.target.value) })
                                  }
                                  className="input-field font-mono"
                                />
                                <div className="text-[10px] text-dim mt-1">
                                  ≤{currentStrategy.bbw_squeeze_percentile ?? 20}% 切顺势，≥
                                  {Math.min(85, Number(currentStrategy.bbw_squeeze_percentile ?? 20) + 15)}% 回震荡
                                </div>
                              </div>
                            </div>

                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                              <label className="surface-card p-3.5 rounded-xl flex items-center justify-between gap-3 cursor-pointer">
                                <div>
                                  <div className="text-xs font-bold text-main">递减金字塔仓位（底仓最重）</div>
                                  <div className="text-[11px] text-dim mt-0.5">
                                    3层按 50% / 30% / 20%（5层按 38%/25%/17%/12%/8%）分配总预算，降低持仓均价
                                  </div>
                                </div>
                                <input
                                  type="checkbox"
                                  checked={Boolean(currentStrategy.trend_pyramid_decay ?? true)}
                                  onChange={(e) =>
                                    updateCurrentStrategyField({ trend_pyramid_decay: e.target.checked })
                                  }
                                  className="w-4 h-4 accent-[#0f1419]"
                                />
                              </label>
                              <label className="surface-card p-3.5 rounded-xl flex items-center justify-between gap-3 cursor-pointer">
                                <div>
                                  <div className="text-xs font-bold text-main">首格结合布林带上/下轨外侧确认</div>
                                  <div className="text-[11px] text-dim mt-0.5">
                                    同时突破缓冲死区与 {currentStrategy.bbw_timeframe || '15m'} 布林带轨道才触发首格，过滤假突破
                                  </div>
                                </div>
                                <input
                                  type="checkbox"
                                  checked={Boolean(currentStrategy.trend_use_bb_breakout ?? true)}
                                  onChange={(e) =>
                                    updateCurrentStrategyField({ trend_use_bb_breakout: e.target.checked })
                                  }
                                  className="w-4 h-4 accent-[#0f1419]"
                                />
                              </label>
                            </div>
                          </div>
                        )}

                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
                          {[
                            {
                              field: 'is_running',
                              title: '启用该合约策略运行',
                              desc: '关闭后暂停该合约的新网格开仓触发',
                            },
                            {
                              field: 'grid_recenter_enabled',
                              title: '空仓超出区间自动重锚网格中枢',
                              desc: '无持仓突破 ±区间或 VCP 波动率状态切换时自动重置网格',
                            },
                            {
                              field: 'use_post_only_tp',
                              title: '真实 Maker Post-Only 挂单止盈 + OKX 条件止损',
                              desc: '成交即在交易所挂出被动限价止盈单（省 60% 平仓费）与条件止损单防断网插针',
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

                        {snapshot.telemetry?.[currentStrategy.symbol]?.grid && (() => {
                          const gSnap = snapshot.telemetry[currentStrategy.symbol].grid;
                          const isTrendActive = gSnap.active_grid_mode === 'TREND_BREAKOUT';
                          const isReverseOscActive = gSnap.active_grid_mode === 'REVERSE_OSCILLATING';
                          const gridPrec = Math.min(
                            12,
                            Math.max(0, Math.round(Number(currentStrategy.price_precision ?? 4) || 4))
                          );
                          return (
                            <div className="surface-subtle p-4 space-y-3 text-xs">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-bold text-main">
                                    实时网格档位分布（中枢价:{' '}
                                    <span className="font-mono">
                                      {Number(gSnap.center_price || 0).toFixed(gridPrec)}
                                    </span>
                                    ）
                                  </span>
                                  <span
                                    className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                                      isTrendActive
                                        ? 'bg-violet-500/15 text-violet-600 dark:text-violet-300'
                                        : isReverseOscActive
                                        ? 'bg-teal-500/15 text-teal-600 dark:text-teal-300'
                                        : 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
                                    }`}
                                  >
                                    {isTrendActive
                                      ? '当前激活：顺势突破模式'
                                      : isReverseOscActive
                                      ? '当前激活：多空震荡模式 (跌破空/涨破多)'
                                      : '当前激活：传统震荡模式'}
                                  </span>
                                  {gSnap.bbw_pct !== null && gSnap.bbw_pct !== undefined && (
                                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-amber-500/15 text-amber-700 dark:text-amber-300">
                                      {gSnap.bbw_timeframe || currentStrategy.bbw_timeframe || '15m'} BBW{' '}
                                      {Number(gSnap.bbw_pct).toFixed(2)}% · 历史分位 {Number(gSnap.bbw_percentile).toFixed(1)}%
                                      {gSnap.vcp_contraction ? ' · VCP收窄' : ''}
                                    </span>
                                  )}
                                  {gSnap.trend_bias && (
                                    <span
                                      className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                                        gSnap.trend_bias === 'BULL'
                                          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                                          : gSnap.trend_bias === 'BEAR'
                                          ? 'bg-rose-500/15 text-rose-700 dark:text-rose-300'
                                          : 'bg-slate-500/15 text-slate-600 dark:text-slate-300'
                                      }`}
                                    >
                                      {gSnap.trend_bias === 'BULL'
                                        ? '多头趋势 · 顺多禁逆空'
                                        : gSnap.trend_bias === 'BEAR'
                                        ? '空头趋势 · 顺空禁逆多'
                                        : '震荡区间 · 多空双开'}
                                      {gSnap.ema_gap_pct !== null && gSnap.ema_gap_pct !== undefined
                                        ? ` (EMA ${Number(gSnap.ema_gap_pct) >= 0 ? '+' : ''}${Number(gSnap.ema_gap_pct).toFixed(2)}%)`
                                        : ''}
                                      {gSnap.breakout_expansion === 'UP_BREAKOUT'
                                        ? ' · 放量上破'
                                        : gSnap.breakout_expansion === 'DOWN_BREAKOUT'
                                        ? ' · 放量破位'
                                        : ''}
                                    </span>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  onClick={() => handleRecenterGrid(currentStrategy.symbol)}
                                  className="btn-soft-pill px-3 py-1 text-[11px]"
                                >
                                  <RefreshCw className="w-3 h-3" strokeWidth={1.75} />
                                  <span>重锚网格中枢</span>
                                </button>
                              </div>
                              {gSnap.regime_reason && (
                                <div className="text-[11px] text-dim">{gSnap.regime_reason}</div>
                              )}
                              {isTrendActive &&
                                (Number(gSnap.long_trail_stop_price || 0) > 0 ||
                                  Number(gSnap.short_trail_stop_price || 0) > 0) && (
                                  <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono">
                                    {Number(gSnap.long_trail_stop_price || 0) > 0 && (
                                      <span className="text-teal-600 dark:text-teal-400 font-semibold">
                                        多头峰值 {Number(gSnap.long_peak_price).toFixed(gridPrec)} → 移动追踪止盈线{' '}
                                        {Number(gSnap.long_trail_stop_price).toFixed(gridPrec)}
                                      </span>
                                    )}
                                    {Number(gSnap.short_trail_stop_price || 0) > 0 && (
                                      <span className="text-teal-600 dark:text-teal-400 font-semibold">
                                        空头低点 {Number(gSnap.short_trough_price).toFixed(gridPrec)} → 移动追踪止盈线{' '}
                                        {Number(gSnap.short_trail_stop_price).toFixed(gridPrec)}
                                      </span>
                                    )}
                                  </div>
                                )}
                              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2 font-mono text-[11px]">
                                {(gSnap.levels || []).map((lv) => (
                                  <div
                                    key={lv.slot_id}
                                    className={`p-2.5 rounded-xl border ${
                                      lv.status === 'OPEN'
                                        ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-700 dark:text-emerald-300'
                                        : lv.side === 'LONG'
                                        ? 'surface-card border-line text-main'
                                        : 'surface-card border-line text-sub'
                                    }`}
                                  >
                                    <div className="flex items-center justify-between font-bold">
                                      <span>
                                        {lv.slot_id} ({lv.side === 'LONG' ? '多' : '空'})
                                      </span>
                                      <span>{lv.status}</span>
                                    </div>
                                    <div className="text-[10px] mt-1">
                                      触发 {Number(lv.trigger_price).toFixed(gridPrec)}
                                    </div>
                                    {isTrendActive ? (
                                      <>
                                        <div className="text-[10px] text-rose-500">
                                          止损 {Number(lv.sl_price || 0).toFixed(gridPrec)}
                                        </div>
                                        <div className="text-[10px] text-dim">
                                          仓位 {Number(lv.order_usdt || 0).toFixed(1)}U ({Number(lv.weight_pct || 0).toFixed(0)}%)
                                        </div>
                                      </>
                                    ) : isReverseOscActive ? (
                                      <>
                                        <div className="text-[10px] text-emerald-600 dark:text-emerald-400">
                                          止盈 {Number(lv.tp_price).toFixed(gridPrec)}
                                        </div>
                                        <div className="text-[10px] text-rose-500">
                                          止损 {Number(lv.sl_price || 0).toFixed(gridPrec)}
                                        </div>
                                        {lv.status === 'IDLE' && lv.armed === false && (
                                          <div className="text-[9px] font-sans text-amber-600 dark:text-amber-300 mt-0.5">
                                            {lv.side === 'LONG' ? '待回踩重装' : '待反弹重装'}
                                          </div>
                                        )}
                                      </>
                                    ) : (
                                      <div className="text-[10px] text-dim">
                                        止盈 {Number(lv.tp_price).toFixed(gridPrec)}
                                      </div>
                                    )}
                                    {(lv.maker_tp_live || lv.cond_sl_live) && (
                                      <div className="flex flex-wrap gap-1 mt-1 font-sans">
                                        {lv.maker_tp_live && (
                                          <span className="px-1.5 py-0.2 rounded text-[9px] bg-emerald-500/20 text-emerald-700 dark:text-emerald-300">
                                            Maker止盈
                                          </span>
                                        )}
                                        {lv.cond_sl_live && (
                                          <span className="px-1.5 py-0.2 rounded text-[9px] bg-amber-500/20 text-amber-700 dark:text-amber-300">
                                            条件止损
                                          </span>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                      </>
                      );
                    })() : (
                      <>
                        {/* Lead-Lag 9 Numeric Parameters Grid */}
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
                            <PercentNumberInput
                              step="0.05"
                              valueRatio={currentStrategy.min_jump_pct}
                              defaultRatio={0.006}
                              onChangeRatio={(v) => updateCurrentStrategyField({ min_jump_pct: v })}
                            />
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">扣费目标净止盈 (%)</label>
                            <PercentNumberInput
                              step="0.05"
                              valueRatio={currentStrategy.take_profit_pct}
                              defaultRatio={0.0025}
                              onChangeRatio={(v) => updateCurrentStrategyField({ take_profit_pct: v })}
                            />
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">最大硬性止损 (%)</label>
                            <PercentNumberInput
                              step="0.05"
                              valueRatio={currentStrategy.stop_loss_pct}
                              defaultRatio={0.0035}
                              onChangeRatio={(v) => updateCurrentStrategyField({ stop_loss_pct: v })}
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
                            <label className="font-bold text-main block mb-2">OKX 条件止损触发价类型</label>
                            <select
                              value={currentStrategy.sl_trigger_px_type || 'mark'}
                              onChange={(e) => updateCurrentStrategyField({ sl_trigger_px_type: e.target.value })}
                              className="w-full rounded-xl surface-subtle px-3 py-2.5 text-xs font-semibold text-main outline-none"
                            >
                              <option value="mark">标记价 Mark（推荐 · 防单所插针误扫止损）</option>
                              <option value="last">最新成交价 Last（更贴近盘口瞬时价）</option>
                            </select>
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">IOC 容忍滑点上限 (%)</label>
                            <PercentNumberInput
                              step="0.01"
                              valueRatio={currentStrategy.slippage_pct}
                              defaultRatio={0.0005}
                              onChangeRatio={(v) => updateCurrentStrategyField({ slippage_pct: v })}
                            />
                          </div>

                          <div>
                            <label className="font-bold text-main block mb-2">从所滞后确认上限 (%)</label>
                            <PercentNumberInput
                              step="0.02"
                              valueRatio={currentStrategy.lag_confirm_max_pct}
                              defaultRatio={0.001}
                              onChangeRatio={(v) => updateCurrentStrategyField({ lag_confirm_max_pct: v })}
                            />
                          </div>
                        </div>

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
                      </>
                    )}
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
          <div className="space-y-4 sm:space-y-5">
            {/* Top Banner Card (Matching Screenshot 1 & 2) */}
            <div className="surface-card p-4 sm:p-6 lg:p-7 flex flex-wrap items-center justify-between gap-3 sm:gap-4">
              <div className="flex items-center gap-3">
                <Shield className="w-5 h-5 sm:w-6 sm:h-6 text-main shrink-0" strokeWidth={1.75} />
                <div>
                  <h2 className="text-lg sm:text-xl font-bold text-main">交易所连接配置与全局风控中心</h2>
                  <p className="text-xs sm:text-sm text-dim mt-1">
                    管理主信号源 (Leader) 与实盘执行端 (Follower) API 密钥、费率结构与全局熔断阈值
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 sm:gap-2.5 w-full sm:w-auto">
                <button
                  onClick={handleRunPreflight}
                  disabled={preflightLoading}
                  className="btn-soft-pill px-3.5 sm:px-4 py-2 text-xs text-sky-600 dark:text-sky-400"
                  title="仅执行只读账户配置、合约面值与最大下单量校验，绝不产生真实委托单"
                >
                  <Check className="w-3.5 h-3.5" strokeWidth={2} />
                  <span>{preflightLoading ? '只读预检中...' : '实盘下单可行性预检 (只读)'}</span>
                </button>
                <button
                  onClick={() => handleClearExchangeCredentials('ALL', '全部交易所')}
                  className="btn-soft-pill px-3.5 sm:px-4 py-2 text-xs text-rose-600"
                >
                  <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                  <span>清除全部 API</span>
                </button>
                <span className="btn-primary-pill px-4 sm:px-5 py-2 text-xs pointer-events-none">
                  <Lock className="w-3.5 h-3.5" strokeWidth={1.75} />
                  <span>AES-256-GCM 加密</span>
                </span>
              </div>
            </div>

            {preflightResult && (
              <div className="surface-card p-4 sm:p-6 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    {preflightResult.ok ? (
                      <Check className="w-5 h-5 text-emerald-500 shrink-0" strokeWidth={2} />
                    ) : (
                      <AlertCircle className="w-5 h-5 text-rose-500 shrink-0" strokeWidth={2} />
                    )}
                    <div className="min-w-0">
                      <h3 className="text-sm font-bold text-main">
                        实盘下单可行性只读预检报告 ·{' '}
                        <span className={preflightResult.ok ? 'text-emerald-600' : 'text-rose-600'}>
                          {preflightResult.ok ? '全部就绪 (可执行)' : '存在阻断项'}
                        </span>
                      </h3>
                      <p className="text-xs text-dim mt-0.5 break-words">
                        {preflightResult.account?.auth_message || '--'} · 账户模式 acctLv=
                        {preflightResult.account?.acct_lv || '--'} · 持仓模式{' '}
                        {preflightResult.account?.pos_mode || '--'}
                      </p>
                    </div>
                  </div>
                  <button onClick={() => setPreflightResult(null)} className="btn-soft-pill px-3 py-1 text-xs">
                    收起报告
                  </button>
                </div>

                {preflightResult.blockers?.length > 0 && (
                  <div className="p-3.5 rounded-2xl bg-rose-500/10 text-rose-700 dark:text-rose-300 text-xs space-y-1">
                    {preflightResult.blockers.map((b, bi) => (
                      <div key={bi} className="font-semibold">
                        • 阻断项: {b}
                      </div>
                    ))}
                  </div>
                )}

                {preflightResult.symbols?.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[660px] text-left border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-line text-[11px] text-dim">
                          <th className="py-2 px-2">合约</th>
                          <th className="py-2 px-2">OKX/BN 状态</th>
                          <th className="py-2 px-2">ctVal / lotSz / minSz</th>
                          <th className="py-2 px-2">最小开仓额</th>
                          <th className="py-2 px-2">拟报单张数</th>
                          <th className="py-2 px-2">检查结论</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y border-line font-mono text-[11px]">
                        {preflightResult.symbols.map((sr) => (
                          <tr key={sr.symbol}>
                            <td className="py-2 px-2 font-bold text-main">{sr.symbol}</td>
                            <td className="py-2 px-2">
                              {sr.okx_state} / {sr.binance_status}
                            </td>
                            <td className="py-2 px-2">
                              {sr.ctVal} / {sr.lotSz} / {sr.minSz}
                            </td>
                            <td className="py-2 px-2">${sr.min_notional_usdt}</td>
                            <td className="py-2 px-2">
                              {sr.planned_sz || '--'} 张 ({sr.planned_base_qty} 币)
                            </td>
                            <td className="py-2 px-2 font-sans">
                              {sr.ready ? (
                                <span className="text-emerald-600 font-semibold">就绪</span>
                              ) : (
                                <span className="text-rose-600 font-semibold">
                                  {(sr.issues || []).join('；') || '账户前置未就绪'}
                                </span>
                              )}
                              {sr.warnings?.length > 0 && (
                                <div className="text-amber-600 mt-0.5">{sr.warnings.join('；')}</div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Exchange Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-5">
              {exchanges.map((ex, idx) => {
                const health = snapshot.exchange_health?.[ex.exchange_id] || {};
                const authStatus = ex.auth_status || 'UNCONFIGURED';
                const isVerified = authStatus === 'VERIFIED';
                const isError =
                  authStatus === 'ERROR' ||
                  authStatus === 'CONFIG_INCOMPLETE' ||
                  authStatus === 'ACCOUNT_MODE_UNSUPPORTED' ||
                  authStatus === 'READ_ONLY_KEY';
                const needsPass = ex.exchange_id === 'okx' || ex.exchange_id === 'bitget';
                const isTradBroker = TRADITIONAL_BROKER_IDS.has(ex.exchange_id);
                const keyLabel =
                  ex.exchange_id === 'futu' || ex.exchange_id === 'moomoo'
                    ? 'OpenD 网关地址 / API Key (Account ID)'
                    : ex.exchange_id === 'ibkr'
                    ? 'IBKR Client Portal 网关 / Consumer Key'
                    : ex.exchange_id === 'schwab'
                    ? 'Schwab App Key (Client ID)'
                    : 'API Key';
                const secLabel =
                  ex.exchange_id === 'futu' || ex.exchange_id === 'moomoo'
                    ? 'RSA 私钥签名 / API Secret'
                    : ex.exchange_id === 'ibkr'
                    ? 'OAuth Access Token / Secret'
                    : ex.exchange_id === 'schwab'
                    ? 'App Secret / Refresh Token'
                    : 'API Secret';
                const passLabel =
                  ex.exchange_id === 'futu' || ex.exchange_id === 'moomoo'
                    ? '交易解锁密码 (Unlock PIN)'
                    : ex.exchange_id === 'ibkr'
                    ? 'IBKR 账户号 (如 U1234567)'
                    : ex.exchange_id === 'schwab'
                    ? 'Account Hash (加密账户ID)'
                    : 'Passphrase';

                return (
                  <div key={ex.exchange_id} className="surface-card p-4 sm:p-6 lg:p-7 flex flex-col justify-between space-y-5">
                    <div className="space-y-4">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-bold text-base text-main">{ex.display_name}</span>
                            {isTradBroker && (
                              <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500/15 text-indigo-600 dark:text-indigo-300">
                                传统券商 · 标普500
                              </span>
                            )}
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
                      <div className="surface-subtle p-3.5 sm:p-4 text-xs flex flex-col gap-2.5">
                        <div className="flex items-start gap-2.5">
                          {isVerified ? (
                            <Check className="w-4 h-4 shrink-0 mt-0.5 text-emerald-500" strokeWidth={2} />
                          ) : isError ? (
                            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-500" strokeWidth={2} />
                          ) : (
                            <Lock className="w-4 h-4 shrink-0 mt-0.5 text-dim" strokeWidth={1.75} />
                          )}
                          <div className="leading-relaxed min-w-0">
                            <div className="font-semibold text-main break-words">{ex.auth_message || '未配置 API 密钥'}</div>
                            {ex.last_verified_at && (
                              <div className="text-[11px] text-dim font-mono mt-0.5">
                                最近校验: {ex.last_verified_at}
                              </div>
                            )}
                          </div>
                        </div>
                        {ex.exchange_id === 'okx' && authStatus === 'ACCOUNT_MODE_UNSUPPORTED' && (
                          <button
                            type="button"
                            onClick={() => handleSwitchOkxAccountMode('okx', '2')}
                            className="btn-primary-pill px-4 py-2 text-xs self-start whitespace-normal text-left"
                          >
                            一键切换单币种保证金模式 (修复51010)
                          </button>
                        )}
                      </div>

                      <div className="space-y-3.5 text-xs">
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="font-bold text-main block mb-1.5">吃单费率 Taker (%)</label>
                            <PercentNumberInput
                              step="0.005"
                              decimals={3}
                              valueRatio={ex.taker_fee}
                              defaultRatio={0.0005}
                              onChangeRatio={(v) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, taker_fee: v };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                          <div>
                            <label className="font-bold text-main block mb-1.5">挂单费率 Maker (%)</label>
                            <PercentNumberInput
                              step="0.005"
                              decimals={3}
                              valueRatio={ex.maker_fee}
                              defaultRatio={0.0002}
                              onChangeRatio={(v) => {
                                const next = [...exchanges];
                                next[idx] = { ...ex, maker_fee: v };
                                setExchanges(next);
                              }}
                              className="input-field font-mono py-2.5"
                            />
                          </div>
                        </div>

                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <label className="font-bold text-main">{keyLabel}</label>
                            <span className="text-[11px] font-mono text-dim">
                              {ex.has_api_key ? `已存 (${ex.api_key_masked})` : '必填'}
                            </span>
                          </div>
                          <input
                            type="text"
                            value={ex.api_key || ''}
                            placeholder={
                              ex.has_api_key
                                ? `已存: ${ex.api_key_masked} (输入新值覆盖)`
                                : isTradBroker
                                ? '输入券商 API Key / Account ID 或网关地址'
                                : '输入完整 API Key'
                            }
                            onChange={(e) => {
                              const next = [...exchanges];
                              next[idx] = { ...ex, api_key: e.target.value };
                              setExchanges(next);
                            }}
                            className="input-field font-mono py-2.5"
                          />
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          <div>
                            <div className="flex items-center justify-between mb-1.5">
                              <label className="font-bold text-main">{secLabel}</label>
                              <span className="text-[11px] font-mono text-dim">
                                {ex.has_api_secret ? '已保存' : '未填'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.api_secret || ''}
                              placeholder={ex.has_api_secret ? '已加密保存' : '输入 Secret / Token'}
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
                              <label className="font-bold text-main">{passLabel}</label>
                              <span className="text-[11px] font-mono text-dim">
                                {ex.has_passphrase ? '已保存' : needsPass ? '必填 (OKX/Bitget)' : '选填'}
                              </span>
                            </div>
                            <input
                              type="password"
                              value={ex.passphrase || ''}
                              placeholder={
                                ex.has_passphrase
                                  ? '已加密保存'
                                  : isTradBroker
                                  ? '交易解锁密码或证券账户号'
                                  : '创建API时设的密码'
                              }
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
                      <div className="flex flex-wrap items-center gap-1.5">
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
                      <button onClick={() => handleSaveExchange(ex)} className="btn-primary-pill w-full sm:w-auto px-5 py-2 text-xs">
                        验证并保存
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            {riskForm && (
              <div className="surface-card p-4 sm:p-6 lg:p-7">
                <div className="flex flex-wrap items-center justify-between gap-3 sm:gap-4 mb-5 sm:mb-6">
                  <div>
                    <h3 className="text-base sm:text-lg font-bold text-main">全局风控阈值与高保真影子盘参数</h3>
                    <p className="text-xs text-dim mt-1">
                      单日亏损熔断、连续亏损熔断、并发持仓上限、延迟熔断与对数正态网络延迟仿真参数
                    </p>
                  </div>
                  <button onClick={handleSaveRisk} className="btn-primary-pill w-full sm:w-auto px-6 py-2.5 text-xs">
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
                    <label className="font-bold text-main block mb-2">滚动亏损自动停用币种 (笔)</label>
                    <input
                      type="number"
                      value={riskForm.auto_disable_rolling_trades}
                      onChange={(e) =>
                        setRiskForm({ ...riskForm, auto_disable_rolling_trades: Number(e.target.value) })
                      }
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">平仓后防抖冷却时间 (ms)</label>
                    <input
                      type="number"
                      value={riskForm.cooldown_ms}
                      onChange={(e) => setRiskForm({ ...riskForm, cooldown_ms: Number(e.target.value) })}
                      className="input-field font-mono"
                    />
                  </div>
                  <div>
                    <label className="font-bold text-main block mb-2">服务器后台常驻托管状态</label>
                    <div className="input-field px-3.5 py-2.5 text-emerald-600 dark:text-emerald-400 font-semibold flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                      <span>已开启 24h 脱机守护（关闭网页继续运行）</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ==================== VIEW 4: ANALYTICS, REALITY GAP & PNL REVIEW ==================== */}
        {activeTab === 'analytics' && (
          <div className="space-y-4 sm:space-y-5">
            <EquityChart equityCurve={stats.equity_curve || []} stats={stats} theme={theme} />

            {/* All Open Positions Real-Time Profit & Loss Totals Panel */}
            <div className="surface-card p-4 sm:p-6 lg:p-7">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <Wallet className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                    <h3 className="text-base sm:text-lg font-bold text-main">
                      所有在途持仓实时盈亏总和统计
                    </h3>
                  </div>
                  <p className="text-xs text-dim mt-1">
                    按毫秒级最新盘口实时汇总全部策略持仓与交易所未纳管仓位的实时盈利总和、实时亏损总和与净浮盈亏
                  </p>
                </div>
                {currentUser && (
                  <button
                    type="button"
                    onClick={handleSyncPositions}
                    disabled={syncingPositions}
                    className="btn-soft-pill px-3.5 py-1.5 text-xs font-semibold disabled:opacity-50"
                  >
                    {syncingPositions ? '同步中...' : '同步交易所持仓'}
                  </button>
                )}
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
                <div className="p-3.5 sm:p-4 rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.06] min-w-0">
                  <div className="flex items-center justify-between gap-2 text-xs text-sub">
                    <span className="truncate">实时盈利总和</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 shrink-0">
                      {positionPnlSummary.profit_count} 仓盈利
                    </span>
                  </div>
                  <div className="text-lg sm:text-2xl font-bold font-mono text-emerald-500 mt-1.5 truncate">
                    {formatUsdtSigned(positionPnlSummary.total_profit_usdt)}
                  </div>
                  <div className="text-[11px] text-dim mt-1 truncate">所有浮盈为正的在途持仓合计</div>
                </div>

                <div className="p-3.5 sm:p-4 rounded-2xl border border-rose-500/25 bg-rose-500/[0.06] min-w-0">
                  <div className="flex items-center justify-between gap-2 text-xs text-sub">
                    <span className="truncate">实时亏损总和</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-rose-500/15 text-rose-500 shrink-0">
                      {positionPnlSummary.loss_count} 仓亏损
                    </span>
                  </div>
                  <div
                    className={`text-lg sm:text-2xl font-bold font-mono mt-1.5 truncate ${
                      positionPnlSummary.total_loss_usdt < 0 ? 'text-rose-500' : 'text-dim'
                    }`}
                  >
                    {formatUsdtSigned(positionPnlSummary.total_loss_usdt, false)}
                  </div>
                  <div className="text-[11px] text-dim mt-1 truncate">所有浮盈为负的在途持仓合计</div>
                </div>

                <div className="p-3.5 sm:p-4 rounded-2xl border border-line surface-subtle min-w-0">
                  <div className="flex items-center justify-between gap-2 text-xs text-sub">
                    <span className="truncate">实时净浮盈亏总和</span>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold shrink-0 ${
                        positionPnlSummary.net_unrealized_usdt >= 0
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                          : 'bg-rose-500/15 text-rose-500'
                      }`}
                    >
                      {positionPnlSummary.net_unrealized_pct >= 0 ? '+' : ''}
                      {Number(positionPnlSummary.net_unrealized_pct || 0).toFixed(2)}%
                    </span>
                  </div>
                  <div
                    className={`text-lg sm:text-2xl font-bold font-mono mt-1.5 truncate ${
                      positionPnlSummary.net_unrealized_usdt >= 0 ? 'text-emerald-500' : 'text-rose-500'
                    }`}
                  >
                    {formatUsdtSigned(positionPnlSummary.net_unrealized_usdt)}
                  </div>
                  <div className="text-[11px] text-dim mt-1 truncate">盈利总和 + 亏损总和实时净值</div>
                </div>

                <div className="p-3.5 sm:p-4 rounded-2xl border border-line surface-subtle min-w-0">
                  <div className="flex items-center justify-between gap-2 text-xs text-sub">
                    <span className="truncate">在途持仓总规模</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-slate-500/10 text-sub shrink-0">
                      共 {positionPnlSummary.total_positions} 仓
                    </span>
                  </div>
                  <div className="text-lg sm:text-2xl font-bold font-mono text-main mt-1.5 truncate">
                    ${Number(positionPnlSummary.total_notional_usdt || 0).toFixed(2)}
                  </div>
                  <div className="text-[11px] text-dim mt-1 truncate">
                    盈 {positionPnlSummary.profit_count} / 亏 {positionPnlSummary.loss_count}
                    {positionPnlSummary.flat_count > 0 ? ` / 平 ${positionPnlSummary.flat_count}` : ''} 合约
                    {(positionPnlSummary.strategy_slots || 0) > positionPnlSummary.total_positions
                      ? ` · ${positionPnlSummary.strategy_slots} 策略槽位`
                      : ''}
                  </div>
                </div>
              </div>
            </div>

            {/* Per-Symbol Running PnL Breakdown Panel */}
            <div className="surface-card p-4 sm:p-6 lg:p-7">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4 sm:mb-5">
                <div>
                  <div className="flex items-center gap-2.5">
                    <BarChart2 className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                    <h3 className="text-base sm:text-lg font-bold text-main">
                      运行中监控币种独立盈亏与表现归因
                    </h3>
                  </div>
                  <p className="text-xs text-dim mt-1">
                    实时统计每个运行币种的净收益、毛利、双边手续费、多空胜率及抢单成交率（点击任意币种卡片可筛选下方历史成交明细）
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => setShowRunningOnly(true)}
                    className={`px-3.5 sm:px-4 py-1.5 rounded-full font-semibold transition ${
                      showRunningOnly ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    仅看运行中币种 ({(stats.symbol_pnl_breakdown || []).filter((item) => item.is_running).length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowRunningOnly(false)}
                    className={`px-3.5 sm:px-4 py-1.5 rounded-full font-semibold transition ${
                      !showRunningOnly ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    全部币种 ({(stats.symbol_pnl_breakdown || []).length})
                  </button>
                  {analyticsSymbolFilter !== 'ALL' && (
                    <button
                      type="button"
                      onClick={() => setAnalyticsSymbolFilter('ALL')}
                      className="btn-soft-pill px-3.5 py-1.5 text-xs text-amber-600 dark:text-amber-400"
                    >
                      清除单币种筛选 ({analyticsSymbolFilter.split('/')[0]})
                    </button>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3.5 sm:gap-4">
                {(stats.symbol_pnl_breakdown || [])
                  .filter((item) => (showRunningOnly ? item.is_running : true))
                  .map((item) => {
                    const isFiltered = analyticsSymbolFilter === item.symbol;
                    const netPositive = (item.net_pnl ?? 0) >= 0;
                    const stateText = STATE_LABEL_MAP[item.state] || item.state || '空闲监控';
                    const isHolding =
                      item.state === 'HOLDING' || item.state === 'GRID_HOLDING' || item.state === 'EXIT_UNCERTAIN';
                    return (
                      <div
                        key={item.symbol}
                        onClick={() =>
                          setAnalyticsSymbolFilter(isFiltered ? 'ALL' : item.symbol)
                        }
                        className={`surface-subtle p-4 sm:p-5 cursor-pointer transition flex flex-col justify-between ${
                          isFiltered
                            ? 'ring-2 ring-slate-900 dark:ring-sky-400'
                            : 'hover:opacity-95'
                        }`}
                      >
                        <div>
                          {/* Card Top Row: Symbol & Running Status */}
                          <div className="flex flex-wrap items-center justify-between gap-1.5">
                            <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                              <span className="font-mono font-bold text-sm text-main">
                                {item.symbol.split(':')[0]}
                              </span>
                              <span
                                className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${strategyBadgeClass(
                                  item.strategy_type || 'LEAD_LAG'
                                )}`}
                              >
                                {STRATEGY_LABEL_MAP[item.strategy_type || 'LEAD_LAG'] || item.strategy_type}
                              </span>
                              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold surface-card text-dim">
                                {isGridStrategyType(item.strategy_type)
                                  ? GRID_MODE_LABEL_MAP[item.grid_side_mode || 'LONG_SHORT']
                                  : DIRECTION_LABEL_MAP[item.direction] || '多空双向'}
                              </span>
                            </div>
                            <span
                              className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 ${
                                isHolding
                                  ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
                                  : item.is_running
                                  ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400'
                                  : 'surface-card text-dim'
                              }`}
                            >
                              <span
                                className={`w-1.5 h-1.5 rounded-full ${
                                  isHolding
                                    ? 'bg-sky-500 animate-ping'
                                    : item.is_running
                                    ? 'bg-emerald-500'
                                    : 'bg-slate-400'
                                }`}
                              />
                              {item.is_running ? stateText : '已暂停'}
                            </span>
                          </div>

                          {/* Net PnL Main Number */}
                          <div className="mt-4 flex items-baseline justify-between">
                            <div>
                              <div className="text-[11px] text-dim">累计已实现净收益 (USDT)</div>
                              <div
                                className={`text-2xl font-bold font-mono mt-0.5 ${
                                  netPositive ? 'text-emerald-500' : 'text-rose-500'
                                }`}
                              >
                                {netPositive
                                  ? `+$${Number(item.net_pnl || 0).toFixed(4)}`
                                  : `-$${Math.abs(Number(item.net_pnl || 0)).toFixed(4)}`}
                              </div>
                            </div>
                            {item.unrealized_pnl !== 0 && (
                              <div className="text-right font-mono">
                                <div className="text-[10px] text-dim">在途浮盈</div>
                                <div
                                  className={`text-xs font-bold ${
                                    item.unrealized_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'
                                  }`}
                                >
                                  {item.unrealized_pnl >= 0 ? '+' : ''}
                                  {Number(item.unrealized_pnl).toFixed(3)}U
                                </div>
                              </div>
                            )}
                          </div>

                          {/* Gross PnL & Fees Sub-row */}
                          <div className="mt-2.5 flex items-center justify-between text-[11px] font-mono text-sub">
                            <span>
                              毛利:{' '}
                              <strong className="text-main">
                                {(item.gross_pnl ?? 0) >= 0
                                  ? `+$${Number(item.gross_pnl || 0).toFixed(4)}`
                                  : `-$${Math.abs(Number(item.gross_pnl || 0)).toFixed(4)}`}
                              </strong>
                            </span>
                            <span>
                              手续费: <strong className="text-dim">${Number(item.fees || 0).toFixed(4)}</strong>
                            </span>
                          </div>

                          {/* Win Rate Progress Bar */}
                          <div className="mt-3.5 pt-3 border-t border-line space-y-2 text-[11px] font-mono">
                            <div className="flex items-center justify-between">
                              <span className="text-dim font-sans">平仓胜率 ({item.trades}笔)</span>
                              <span className="font-bold text-main">
                                {item.win_rate_pct}% ({item.wins}盈 / {item.losses}亏)
                              </span>
                            </div>
                            <div className="w-full h-1.5 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden">
                              <div
                                className={`h-full transition-all duration-300 ${
                                  item.win_rate_pct >= 60 ? 'bg-emerald-500' : item.trades > 0 ? 'bg-amber-500' : 'bg-slate-400'
                                }`}
                                style={{ width: `${Math.min(100, item.win_rate_pct || 0)}%` }}
                              />
                            </div>

                            <div className="flex items-center justify-between pt-1">
                              <span className="text-dim font-sans">IOC 抢单成交率</span>
                              <span className="text-sub">
                                {item.fill_rate_pct}% ({item.filled}成 / {item.missed}撤)
                              </span>
                            </div>
                          </div>
                        </div>

                        {/* Long / Short Breakdown & Extremes Footer */}
                        <div className="mt-3.5 pt-3 border-t border-line space-y-1.5 text-[11px] font-mono">
                          <div className="flex items-center justify-between">
                            <span className="text-dim">
                              做多({item.long_trades}):{' '}
                              <span className={item.long_net_pnl >= 0 ? 'text-emerald-500 font-semibold' : 'text-rose-500 font-semibold'}>
                                {item.long_net_pnl >= 0 ? '+' : ''}{Number(item.long_net_pnl || 0).toFixed(3)}U
                              </span>
                            </span>
                            <span className="text-dim">
                              做空({item.short_trades}):{' '}
                              <span className={item.short_net_pnl >= 0 ? 'text-emerald-500 font-semibold' : 'text-rose-500 font-semibold'}>
                                {item.short_net_pnl >= 0 ? '+' : ''}{Number(item.short_net_pnl || 0).toFixed(3)}U
                              </span>
                            </span>
                          </div>
                          <div className="flex items-center justify-between text-dim">
                            <span>
                              最佳/最差:{' '}
                              <span className="text-emerald-500">+{Number(item.best_trade_pnl || 0).toFixed(2)}</span>
                              {' / '}
                              <span className="text-rose-500">{Number(item.worst_trade_pnl || 0).toFixed(2)}</span>
                            </span>
                            <span>均值持仓: {item.avg_hold_ms || 0}ms</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
              </div>
            </div>

            {/* Shadow-to-Live Reality Gap & Decay Diagnostic Panel (Shown only for unauthenticated preview) */}
            {!currentUser && (
            <div className="surface-card p-4 sm:p-6 lg:p-7">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4 sm:mb-5">
                <div className="flex items-center gap-2.5">
                  <Shield className="w-4 h-4 sm:w-5 sm:h-5 text-main shrink-0" strokeWidth={1.75} />
                  <h3 className="text-base sm:text-lg font-bold text-main">
                    模拟盘数据可靠性与实盘衰减量化诊断 (Shadow-to-Live Reality Gap)
                  </h3>
                </div>
                <span className="text-[11px] sm:text-xs font-mono text-dim">
                  基于当前报单延迟 ({snapshot.last_order_rtt_ms}ms) 与双边费率结构实时测算
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3.5 sm:gap-4 text-xs">
                <div className="surface-subtle p-4 sm:p-5">
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

                <div className="surface-subtle p-4 sm:p-5">
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

                <div className="surface-subtle p-4 sm:p-5">
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

                <div className="surface-subtle p-4 sm:p-5">
                  <div className="text-dim">4. 实盘可行性区间结论</div>
                  <div className="mt-2 font-bold text-sm text-main">聚焦中小盘 + 二线执行所</div>
                  <p className="text-[11px] text-dim mt-2 leading-relaxed">
                    主流币：{realityGap.mainstream_verdict}；中小盘：{realityGap.altcoin_verdict}。
                  </p>
                </div>
              </div>
            </div>
            )}

            {/* Historical Trades Table */}
            <div className="surface-card p-4 sm:p-6 lg:p-7">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4 sm:mb-5">
                <div>
                  <h3 className="text-base sm:text-lg font-bold text-main">
                    {isLiveMode ? '实盘通道历史成交复盘明细表' : '影子模拟盘历史狙击复盘明细表'}
                    {analyticsSymbolFilter !== 'ALL' && (
                      <span className="ml-2 text-sm font-mono text-emerald-600 dark:text-emerald-400">
                        [{analyticsSymbolFilter}]
                      </span>
                    )}
                  </h3>
                  <p className="text-xs text-dim mt-1">
                    由独立后台队列批量写入 SQLite 数据库，模拟盘与实盘通道账本严格分表隔离
                  </p>
                </div>
                <div className="flex items-center gap-1.5 text-xs overflow-x-auto no-scrollbar sm:flex-wrap w-full sm:w-auto pb-0.5 sm:pb-0">
                  <button
                    type="button"
                    onClick={() => setAnalyticsSymbolFilter('ALL')}
                    className={`px-3 py-1 rounded-full font-semibold transition whitespace-nowrap shrink-0 ${
                      analyticsSymbolFilter === 'ALL' ? 'btn-primary-pill' : 'btn-soft-pill'
                    }`}
                  >
                    全部币种 ({(stats.recent_trades || []).length})
                  </button>
                  {(stats.symbol_pnl_breakdown || []).map((item) => (
                    <button
                      key={item.symbol}
                      type="button"
                      onClick={() => setAnalyticsSymbolFilter(item.symbol)}
                      className={`px-3 py-1 rounded-full font-mono text-[11px] font-semibold transition whitespace-nowrap shrink-0 ${
                        analyticsSymbolFilter === item.symbol ? 'btn-primary-pill' : 'btn-soft-pill'
                      }`}
                    >
                      {item.symbol.split('/')[0]} ({item.trades})
                    </button>
                  ))}
                </div>
              </div>

              {/* Mobile Card View (< md) */}
              <div className="md:hidden space-y-2.5">
                {filteredRecentTrades.length === 0 ? (
                  <div className="py-10 text-center text-xs text-dim">
                    {isLiveMode
                      ? '当前实盘通道尚无符合筛选条件的真实成交记录'
                      : '暂无符合筛选条件的成交记录，请等待脉冲信号或点击顶部「脉冲注入」按钮验证全流程'}
                  </div>
                ) : (
                  filteredRecentTrades.map((tr, i) => (
                    <div key={`mob-tr-${tr.id || i}`} className="surface-subtle p-3.5 space-y-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="font-mono font-bold text-main truncate">{tr.symbol}</span>
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              tr.side === 'LONG' ? 'bg-emerald-500/12 text-emerald-600' : 'bg-rose-500/12 text-rose-600'
                            }`}
                          >
                            {tr.side === 'LONG' ? '做多' : '做空'}
                          </span>
                        </div>
                        <span className={`font-mono font-bold ${tr.net_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                          {tr.net_pnl >= 0 ? `+${Number(tr.net_pnl).toFixed(4)}` : Number(tr.net_pnl).toFixed(4)} U
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-1.5 font-mono text-[11px] text-sub">
                        <div>开/平: {tr.entry} → {tr.exit}</div>
                        <div className="text-right">耗时: {tr.hold_ms}ms · {tr.signal_to_fill_ms}ms</div>
                        <div>脉冲: {tr.jump_pct >= 0 ? `+${tr.jump_pct}%` : `${tr.jump_pct}%`}</div>
                        <div className="text-right text-dim">手续费: {Number(tr.fee).toFixed(4)}</div>
                      </div>
                      <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-line">
                        <span className="text-[11px] font-mono text-dim">{tr.time}</span>
                        <span className={`px-2 py-0.5 rounded-full border text-[10px] font-medium ${tr._badge.cls}`}>
                          {tr._badge.text}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>

              {/* Tablet / Desktop Table (>= md) */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full min-w-[980px] text-left border-collapse text-xs">
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
                    {filteredRecentTrades.length === 0 ? (
                      <tr>
                        <td colSpan={13} className="py-14 text-center text-dim font-sans">
                          {isLiveMode
                            ? '当前实盘通道尚无符合筛选条件的真实成交记录'
                            : '暂无符合筛选条件的成交记录，请等待脉冲信号或点击顶部「脉冲注入」按钮验证全流程'}
                        </td>
                      </tr>
                    ) : (
                      filteredRecentTrades.map((tr, i) => (
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
                              <span className={`px-2.5 py-0.5 rounded-full border text-[11px] font-medium ${tr._badge.cls}`}>
                                {tr._badge.text}
                              </span>
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

        {/* ==================== TAB 5: MEMBERSHIP PURCHASE & MEMBER CENTER ==================== */}
        {activeTab === 'membership' && (
          <MembershipCenterView
            currentUser={currentUser}
            authToken={authToken}
            onUserUpdated={setCurrentUser}
            onTokenUpdated={(newToken) => {
              setAuthToken(newToken);
              localStorage.setItem('sniper_auth_token', newToken);
            }}
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
