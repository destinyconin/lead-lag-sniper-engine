import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { getChartPointIndex, useContainerChartSize } from '../utils/useContainerChartSize';
import {
  formatLocalHourLabel,
  getClientTimezoneOffsetLabel,
  getClientTimezoneTooltip,
} from '../utils/clientTime';

const getSentimentMeta = (score) => {
  const s = Number(score);
  if (!Number.isFinite(s)) {
    return { label: '--', cls: 'surface-subtle text-sub' };
  }
  if (s < 25) {
    return {
      label: '极度恐慌',
      cls: 'bg-rose-500/15 text-rose-600 dark:text-rose-300 border border-rose-500/30',
    };
  }
  if (s < 45) {
    return {
      label: '恐慌',
      cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30',
    };
  }
  if (s <= 55) {
    return {
      label: '中性',
      cls: 'surface-subtle text-sub border border-line',
    };
  }
  if (s < 75) {
    return {
      label: '贪婪',
      cls: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30',
    };
  }
  return {
    label: '极度贪婪',
    cls: 'bg-teal-500/15 text-teal-600 dark:text-teal-300 border border-teal-500/30',
  };
};

const formatHourLabel = (tsMs, includeDate = true) => formatLocalHourLabel(tsMs, includeDate);

const formatSigned = (val, digits = 1, suffix = '') => {
  const n = Number(val);
  if (!Number.isFinite(n)) return '--';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
};

export default function MarketSentimentChart({
  sentiment = null,
  loading = false,
  onRefresh,
  theme = 'light',
}) {
  const [rangeMode, setRangeMode] = useState('AUTO');
  const [hoverIdx, setHoverIdx] = useState(null);
  const clientTzLabel = useMemo(() => getClientTimezoneOffsetLabel(), []);
  const clientTzTooltip = useMemo(() => getClientTimezoneTooltip(), []);

  const { containerRef, width, height, isCompact } = useContainerChartSize({
    defaultWidth: 880,
    mobileHeight: 200,
    tabletHeight: 220,
    desktopHeight: 235,
  });

  const tradMarketOpen = Boolean(sentiment?.trad_market_open);
  const series72h = Array.isArray(sentiment?.series_72h) ? sentiment.series_72h : [];
  const seriesOverlap24h = Array.isArray(sentiment?.series_overlap_24h) ? sentiment.series_overlap_24h : [];
  const summary = sentiment?.summary || {};

  const effectiveMode = useMemo(() => {
    if (rangeMode !== 'AUTO') return rangeMode;
    if (!tradMarketOpen && series72h.length > 24) return '72H';
    return '24H';
  }, [rangeMode, tradMarketOpen, series72h.length]);

  const activeSeries = useMemo(() => {
    if (effectiveMode === 'OVERLAP_24H' && seriesOverlap24h.length >= 2) {
      return seriesOverlap24h;
    }
    if (effectiveMode === '24H') return series72h.slice(-24);
    if (effectiveMode === '48H') return series72h.slice(-48);
    return series72h;
  }, [effectiveMode, series72h, seriesOverlap24h]);

  useEffect(() => {
    setHoverIdx(null);
  }, [effectiveMode, activeSeries.length]);

  const padLeft = isCompact ? 38 : 48;
  const padRight = isCompact ? 10 : 18;
  const padTop = 14;
  const padBottom = isCompact ? 22 : 26;
  const plotW = Math.max(120, width - padLeft - padRight);
  const plotH = Math.max(80, height - padTop - padBottom);

  const isLight = theme === 'light';
  const gridStroke = isLight ? '#e2e8f0' : '#1e293b';
  const axisTextFill = isLight ? '#64748b' : '#94a3b8';
  const cryptoColor = isLight ? '#d97706' : '#f59e0b';
  const tradColor = isLight ? '#0284c7' : '#38bdf8';

  const { cryptoPath, tradLivePaths, tradClosedPaths, diffAreaPath, coordList, xTicks } = useMemo(() => {
    if (!activeSeries || activeSeries.length < 2) {
      return {
        cryptoPath: '',
        tradLivePaths: [],
        tradClosedPaths: [],
        diffAreaPath: '',
        coordList: [],
        xTicks: [],
      };
    }

    const toX = (i) => padLeft + (i / (activeSeries.length - 1)) * plotW;
    const toY = (v) => padTop + (1 - Math.max(0, Math.min(100, Number(v || 0))) / 100) * plotH;

    const pts = activeSeries.map((pt, i) => ({
      x: toX(i),
      yc: toY(pt.crypto_score),
      yt: toY(pt.trad_score),
      tradLive: Boolean(pt.trad_live),
      raw: pt,
    }));

    const cPath = pts
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.yc.toFixed(1)}`)
      .join(' ');

    // Split TradFi line into contiguous solid (active market) vs dashed (market closed) segments
    const liveSegs = [];
    const closedSegs = [];
    for (let i = 1; i < pts.length; i++) {
      const prev = pts[i - 1];
      const curr = pts[i];
      const seg = `M ${prev.x.toFixed(1)} ${prev.yt.toFixed(1)} L ${curr.x.toFixed(1)} ${curr.yt.toFixed(1)}`;
      if (curr.tradLive) {
        liveSegs.push(seg);
      } else {
        closedSegs.push(seg);
      }
    }

    // Closed shaded polygon between Crypto & TradFi hourly curves
    let areaD = `M ${pts[0].x.toFixed(1)} ${pts[0].yc.toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      areaD += ` L ${pts[i].x.toFixed(1)} ${pts[i].yc.toFixed(1)}`;
    }
    for (let i = pts.length - 1; i >= 0; i--) {
      areaD += ` L ${pts[i].x.toFixed(1)} ${pts[i].yt.toFixed(1)}`;
    }
    areaD += ' Z';

    const tickCount = isCompact ? 4 : 6;
    const ticks = [];
    const includeDate = effectiveMode !== '24H';
    for (let t = 0; t < tickCount; t++) {
      const idx = Math.min(pts.length - 1, Math.round((t / Math.max(1, tickCount - 1)) * (pts.length - 1)));
      ticks.push({
        x: pts[idx].x,
        label: formatHourLabel(pts[idx].raw.ts_ms, includeDate),
      });
    }

    return {
      cryptoPath: cPath,
      tradLivePaths: liveSegs,
      tradClosedPaths: closedSegs,
      diffAreaPath: areaD,
      coordList: pts,
      xTicks: ticks,
    };
  }, [activeSeries, plotW, plotH, padLeft, padTop, isCompact, effectiveMode]);

  const activePoint =
    hoverIdx !== null && coordList[hoverIdx]
      ? coordList[hoverIdx]
      : coordList[coordList.length - 1] || null;
  const activeRaw = activePoint?.raw || null;

  const displayCryptoScore = activeRaw ? Number(activeRaw.crypto_score) : Number(summary.crypto_score ?? 50);
  const displayTradScore = activeRaw ? Number(activeRaw.trad_score) : Number(summary.trad_score ?? 50);
  const displaySpread = activeRaw
    ? Number(activeRaw.spread)
    : Number(summary.spread ?? (displayCryptoScore - displayTradScore));
  const cryptoMeta = getSentimentMeta(displayCryptoScore);
  const tradMeta = getSentimentMeta(displayTradScore);

  const handleScrub = (clientX, target) => {
    const idx = getChartPointIndex(clientX, target, width, padLeft, plotW, coordList.length);
    if (idx !== null) setHoverIdx(idx);
  };

  const rangeOptions = [
    ...(seriesOverlap24h.length >= 4 && !tradMarketOpen ? [{ id: 'OVERLAP_24H', label: '同步24H' }] : []),
    { id: '24H', label: '24H' },
    { id: '48H', label: '48H' },
    { id: '72H', label: '72H' },
  ];

  const y25 = padTop + 0.75 * plotH;
  const y75 = padTop + 0.25 * plotH;
  const y50 = padTop + 0.5 * plotH;

  return (
    <div className="surface-card p-4 sm:p-6 flex flex-col justify-between">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-2.5 mb-3 pb-3 border-b border-line">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm sm:text-base font-bold text-main">加密市场 vs 传统市场 · 每小时情绪对比</h3>
          <span
            className={`inline-flex items-center gap-1 px-2.5 py-0.5 text-[11px] font-semibold rounded-full ${
              tradMarketOpen
                ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25'
                : 'bg-amber-500/12 text-amber-700 dark:text-amber-300 border border-amber-500/25'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                tradMarketOpen ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'
              }`}
            />
            {tradMarketOpen
              ? '双市场实时同步'
              : activeSeries.some((p) => p.trad_proxy)
              ? '美股休市 · 虚线为 SPY/QQQ 24H 代理'
              : '传统市场休市 · 虚线沿用收盘'}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-1 rounded-full surface-subtle p-1 text-[11px]">
            {rangeOptions.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => setRangeMode(opt.id)}
                className={`px-2.5 py-0.5 rounded-full font-semibold transition whitespace-nowrap ${
                  effectiveMode === opt.id ? 'surface-card text-main shadow-sm' : 'text-dim hover:text-main'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {onRefresh && (
            <button
              type="button"
              onClick={() => onRefresh(true)}
              disabled={loading}
              title="刷新加密与传统市场每小时情绪数据"
              className="btn-soft-pill p-1.5 text-dim hover:text-main disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} strokeWidth={1.9} />
            </button>
          )}
        </div>
      </div>

      {/* Live / Hovered Hour Dual-Market Scoreboard */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 mb-3 text-xs">
        <div className="rounded-xl surface-subtle px-3 py-2">
          <div className="flex items-center justify-between gap-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold text-sub">
              <span className="w-2.5 h-0.5 rounded-full inline-block" style={{ backgroundColor: cryptoColor }} />
              加密市场情绪
            </span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-bold ${cryptoMeta.cls}`}>
              {cryptoMeta.label}
            </span>
          </div>
          <div className="flex items-baseline gap-2 mt-1 font-mono">
            <span className="text-base sm:text-lg font-bold text-main">
              {Number.isFinite(displayCryptoScore) ? displayCryptoScore.toFixed(1) : '--'}
            </span>
            <span className="text-[10px] text-dim">/ 100</span>
            {activeRaw?.btc_ret_1h_pct !== null && activeRaw?.btc_ret_1h_pct !== undefined && (
              <span
                className={`text-[11px] font-semibold ${
                  activeRaw.btc_ret_1h_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'
                }`}
              >
                BTC 1H {formatSigned(activeRaw.btc_ret_1h_pct, 2, '%')}
              </span>
            )}
          </div>
          <div className="text-[10px] text-dim font-mono mt-0.5 truncate">
            {activeRaw?.btc_px ? `BTC $${Number(activeRaw.btc_px).toLocaleString()}` : 'BTC --'}
            {activeRaw?.taker_buy_pct !== null && activeRaw?.taker_buy_pct !== undefined
              ? ` · 买盘 ${activeRaw.taker_buy_pct}%`
              : ''}
          </div>
        </div>

        <div className="rounded-xl surface-subtle px-3 py-2">
          <div className="flex items-center justify-between gap-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold text-sub">
              <span className="w-2.5 h-0.5 rounded-full inline-block" style={{ backgroundColor: tradColor }} />
              传统市场情绪
            </span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-bold ${tradMeta.cls}`}>
              {tradMeta.label}
              {activeRaw && !activeRaw.trad_live ? (activeRaw.trad_proxy ? '·盘外' : '·休') : ''}
            </span>
          </div>
          <div className="flex items-baseline gap-2 mt-1 font-mono">
            <span className="text-base sm:text-lg font-bold text-main">
              {Number.isFinite(displayTradScore) ? displayTradScore.toFixed(1) : '--'}
            </span>
            <span className="text-[10px] text-dim">/ 100</span>
            {activeRaw?.es_ret_1h_pct !== null && activeRaw?.es_ret_1h_pct !== undefined && (
              <span
                className={`text-[11px] font-semibold ${
                  activeRaw.es_ret_1h_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'
                }`}
              >
                标普 1H {formatSigned(activeRaw.es_ret_1h_pct, 2, '%')}
              </span>
            )}
          </div>
          <div className="text-[10px] text-dim font-mono mt-0.5 truncate">
            {activeRaw?.es_px ? `ES ${Number(activeRaw.es_px).toLocaleString()}` : 'ES --'}
            {activeRaw?.vix ? ` · VIX ${Number(activeRaw.vix).toFixed(2)}` : ''}
          </div>
        </div>

        <div className="rounded-xl surface-subtle px-3 py-2">
          <div className="flex items-center justify-between gap-1.5">
            <span className="text-[11px] font-semibold text-sub">情绪差 (加密 − 传统)</span>
            <span title={clientTzTooltip} className="text-[10px] font-mono text-dim">
              {activeRaw ? `${formatHourLabel(activeRaw.ts_ms, true)} ${clientTzLabel}` : '--'}
            </span>
          </div>
          <div className="flex items-baseline gap-2 mt-1 font-mono">
            <span
              className={`text-base sm:text-lg font-bold ${
                displaySpread >= 0 ? 'text-emerald-500' : 'text-rose-500'
              }`}
            >
              {formatSigned(displaySpread, 1)}
            </span>
            <span className="text-[10px] text-dim">分</span>
            {summary.correlation_24h !== null && summary.correlation_24h !== undefined && (
              <span className="text-[11px] text-sub">
                24H 相关性 <strong className="text-main">{Number(summary.correlation_24h).toFixed(2)}</strong>
              </span>
            )}
          </div>
          <div className="text-[10px] text-dim font-mono mt-0.5 truncate">
            加密F&amp;G {summary.crypto_fng_anchor ?? '--'} · CNN恐贪 {summary.trad_fng_anchor ?? '--'}
          </div>
        </div>
      </div>

      {/* Main Hourly Sentiment Dual-Curve SVG */}
      <div ref={containerRef} className="relative w-full overflow-hidden">
        {coordList.length < 2 ? (
          <div
            style={{ height: `${height}px` }}
            className="w-full flex flex-col items-center justify-center text-xs text-dim surface-subtle rounded-xl"
          >
            <div>{loading ? '正在同步 OKX 与美股期货每小时情绪数据...' : '暂无可用的每小时情绪对比数据'}</div>
          </div>
        ) : (
          <svg
            viewBox={`0 0 ${width} ${height}`}
            style={{ height: `${height}px` }}
            className="w-full select-none cursor-crosshair touch-pan-y"
            onMouseLeave={() => setHoverIdx(null)}
            onMouseMove={(e) => handleScrub(e.clientX, e.currentTarget)}
            onTouchStart={(e) => {
              if (e.touches?.[0]) handleScrub(e.touches[0].clientX, e.currentTarget);
            }}
            onTouchMove={(e) => {
              if (e.touches?.[0]) handleScrub(e.touches[0].clientX, e.currentTarget);
            }}
            onTouchEnd={() => setHoverIdx(null)}
          >
            <defs>
              <linearGradient id="sentimentSpreadFill" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={displaySpread >= 0 ? '#10b981' : '#f43f5e'}
                  stopOpacity={isLight ? '0.14' : '0.22'}
                />
                <stop
                  offset="100%"
                  stopColor={displaySpread >= 0 ? '#10b981' : '#f43f5e'}
                  stopOpacity="0.03"
                />
              </linearGradient>
            </defs>

            {/* Fear (<25) and Greed (>75) Subtle Zone Bands */}
            <rect
              x={padLeft}
              y={padTop}
              width={plotW}
              height={Math.max(0, y75 - padTop)}
              fill="#10b981"
              opacity={isLight ? '0.045' : '0.07'}
            />
            <rect
              x={padLeft}
              y={y25}
              width={plotW}
              height={Math.max(0, padTop + plotH - y25)}
              fill="#f43f5e"
              opacity={isLight ? '0.045' : '0.07'}
            />

            {/* Horizontal Grid Lines (0, 25, 50, 75, 100) */}
            {[100, 75, 50, 25, 0].map((val) => {
              const y = padTop + (1 - val / 100) * plotH;
              const isMid = val === 50;
              return (
                <g key={val}>
                  <line
                    x1={padLeft}
                    y1={y}
                    x2={width - padRight}
                    y2={y}
                    stroke={gridStroke}
                    strokeDasharray={isMid ? '4 3' : '2.5 2.5'}
                    strokeWidth={isMid ? '1' : '0.7'}
                    vectorEffect="non-scaling-stroke"
                  />
                  <text
                    x={padLeft - 7}
                    y={y + 3.5}
                    textAnchor="end"
                    fill={axisTextFill}
                    className="font-mono text-[10px]"
                  >
                    {val}
                  </text>
                </g>
              );
            })}

            {/* Right-edge zone annotations */}
            <text
              x={width - padRight - 4}
              y={padTop + 11}
              textAnchor="end"
              fill="#10b981"
              opacity="0.7"
              className="text-[9px] font-semibold"
            >
              极度贪婪 (&gt;75)
            </text>
            <text
              x={width - padRight - 4}
              y={y50 - 4}
              textAnchor="end"
              fill={axisTextFill}
              opacity="0.7"
              className="text-[9px]"
            >
              中性 50
            </text>
            <text
              x={width - padRight - 4}
              y={padTop + plotH - 4}
              textAnchor="end"
              fill="#f43f5e"
              opacity="0.7"
              className="text-[9px] font-semibold"
            >
              极度恐慌 (&lt;25)
            </text>

            {/* Shaded Divergence Area */}
            {diffAreaPath && <path d={diffAreaPath} fill="url(#sentimentSpreadFill)" />}

            {/* Traditional Market Curve: Closed/Carried Segments (Dashed) */}
            {tradClosedPaths.map((d, i) => (
              <path
                key={`tc-${i}`}
                d={d}
                fill="none"
                stroke={tradColor}
                strokeWidth="1.45"
                strokeDasharray="4 3"
                opacity="0.65"
                vectorEffect="non-scaling-stroke"
              />
            ))}

            {/* Traditional Market Curve: Active Trading Segments (Solid) */}
            {tradLivePaths.map((d, i) => (
              <path
                key={`tl-${i}`}
                d={d}
                fill="none"
                stroke={tradColor}
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            ))}

            {/* Crypto Market Hourly Curve */}
            {cryptoPath && (
              <path
                d={cryptoPath}
                fill="none"
                stroke={cryptoColor}
                strokeWidth="2.0"
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            )}

            {/* Subtle Hourly Dots (when <= 36 points) */}
            {coordList.length <= 36 &&
              coordList.map((pt, i) => (
                <g key={`dot-${i}`}>
                  <circle cx={pt.x} cy={pt.yc} r="1.7" fill={cryptoColor} opacity="0.85" />
                  {pt.tradLive && <circle cx={pt.x} cy={pt.yt} r="1.5" fill={tradColor} opacity="0.85" />}
                </g>
              ))}

            {/* X-Axis Hourly Tick Labels */}
            {xTicks.map((tk, i) => (
              <text
                key={`xt-${i}`}
                x={tk.x}
                y={height - 5}
                textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}
                fill={axisTextFill}
                className="font-mono text-[9.5px]"
              >
                {tk.label}
              </text>
            ))}

            {/* Active / Hovered Crosshair & Dual Markers */}
            {activePoint && (
              <g>
                <line
                  x1={activePoint.x}
                  y1={padTop}
                  x2={activePoint.x}
                  y2={padTop + plotH}
                  stroke={axisTextFill}
                  strokeDasharray="2.5 2.5"
                  strokeWidth="0.85"
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  cx={activePoint.x}
                  cy={activePoint.yc}
                  r="3.4"
                  fill={cryptoColor}
                  stroke="#fff"
                  strokeWidth="1.2"
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  cx={activePoint.x}
                  cy={activePoint.yt}
                  r="3.4"
                  fill={tradColor}
                  stroke="#fff"
                  strokeWidth="1.2"
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            )}
          </svg>
        )}
      </div>

      {/* Bottom Hourly Sentiment Spread Oscillator (Crypto - Traditional) */}
      <div className="mt-2 pt-3 border-t border-line space-y-2">
        <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-1 text-[11px] font-mono text-dim">
          <span>每小时情绪差值柱状图（绿=加密情绪更强 / 红=传统市场更强）</span>
          <span className="text-[10px] font-sans text-sub">
            {summary.regime_summary || '基于 OKX 1H 合约动量/买盘/恐贪 与 CME 标普/纳指/VIX/CNN 恐贪实时合成'}
          </span>
        </div>

        <div className="relative h-12 surface-subtle rounded-lg px-2 flex items-center gap-[2px] overflow-hidden">
          <div className="absolute inset-x-0 top-1/2 h-[1px] bg-slate-400/35 pointer-events-none" />
          {activeSeries.map((pt, i) => {
            const sp = Number(pt.spread || 0);
            const maxAbs = Math.max(25, ...activeSeries.map((x) => Math.abs(Number(x.spread || 0))));
            const halfH = Math.min(48, (Math.abs(sp) / maxAbs) * 48);
            const isPos = sp >= 0;
            const isHovered = hoverIdx === i;
            const barCls = isPos
              ? isHovered
                ? 'bg-emerald-500'
                : 'bg-emerald-500/60 hover:bg-emerald-500'
              : isHovered
              ? 'bg-rose-500'
              : 'bg-rose-500/60 hover:bg-rose-500';
            return (
              <div
                key={`sp-${pt.ts_ms || i}`}
                className="relative flex-1 h-full cursor-pointer"
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)}
                title={`${formatHourLabel(pt.ts_ms, true)} ${clientTzLabel} | 加密 ${pt.crypto_score} vs 传统 ${pt.trad_score} | 差值 ${formatSigned(sp, 1)}`}
              >
                <div
                  className={`absolute inset-x-0 rounded-[1px] transition-all duration-75 ${barCls}`}
                  style={
                    isPos
                      ? { bottom: '50%', height: `${Math.max(4, halfH)}%` }
                      : { top: '50%', height: `${Math.max(4, halfH)}%` }
                  }
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
