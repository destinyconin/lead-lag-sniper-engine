import React, { useMemo, useState } from 'react';
import { getChartPointIndex, useContainerChartSize } from '../utils/useContainerChartSize';

export default function EquityChart({ equityCurve = [], stats = {}, activePositions = [], theme = 'dark' }) {
  const [hoverIdx, setHoverIdx] = useState(null);

  const { containerRef, width, height, isCompact } = useContainerChartSize({
    defaultWidth: 640,
    mobileHeight: 195,
    tabletHeight: 210,
    desktopHeight: 220,
  });

  const padL = isCompact ? 46 : 58;
  const padR = isCompact ? 10 : 18;
  const padT = 16;
  const padB = isCompact ? 20 : 24;
  const plotW = Math.max(120, width - padL - padR);
  const plotH = Math.max(80, height - padT - padB);

  const isLight = theme === 'light';
  const gridStroke = isLight ? '#e2e8f0' : '#1e293b';
  const axisTextFill = isLight ? '#64748b' : '#94a3b8';

  const { netLine, grossLine, netArea, zeroY, minV, maxV, coordPts } = useMemo(() => {
    const pts = [{ index: 0, net_pnl: 0, gross_pnl: 0, trade_pnl: 0, symbol: '初始基准' }, ...equityCurve];
    if (pts.length < 2) {
      return {
        netLine: '',
        grossLine: '',
        netArea: '',
        zeroY: padT + plotH / 2,
        minV: -1,
        maxV: 1,
        coordPts: [],
      };
    }

    let lo = 0;
    let hi = 0.5;
    for (const p of pts) {
      lo = Math.min(lo, Number(p.net_pnl || 0), Number(p.gross_pnl || 0));
      hi = Math.max(hi, Number(p.net_pnl || 0), Number(p.gross_pnl || 0));
    }
    const span = Math.max(hi - lo, 0.5);
    const minVal = lo - span * 0.15;
    const maxVal = hi + span * 0.15;

    const toX = (i) => padL + (i / (pts.length - 1)) * plotW;
    const toY = (v) => padT + (1 - (v - minVal) / (maxVal - minVal)) * plotH;

    const nCoords = [];
    const gCoords = [];
    const mapped = [];

    for (let i = 0; i < pts.length; i++) {
      const x = toX(i);
      const yn = toY(Number(pts[i].net_pnl || 0));
      const yg = toY(Number(pts[i].gross_pnl || 0));
      nCoords.push(`${x.toFixed(1)},${yn.toFixed(1)}`);
      gCoords.push(`${x.toFixed(1)},${yg.toFixed(1)}`);
      mapped.push({ x, yn, yg, raw: pts[i] });
    }

    const baseY = toY(0);
    const areaPoints = [
      `${toX(0).toFixed(1)},${baseY.toFixed(1)}`,
      ...nCoords,
      `${toX(pts.length - 1).toFixed(1)},${baseY.toFixed(1)}`,
    ].join(' ');

    return {
      netLine: nCoords.join(' '),
      grossLine: gCoords.join(' '),
      netArea: areaPoints,
      zeroY: baseY,
      minV: minVal,
      maxV: maxVal,
      coordPts: mapped,
    };
  }, [equityCurve, plotW, plotH, padL, padT]);

  const activePt =
    hoverIdx !== null && coordPts[hoverIdx] ? coordPts[hoverIdx] : coordPts[coordPts.length - 1] || null;

  const longTrades = stats.long_trades || 0;
  const shortTrades = stats.short_trades || 0;
  const hasDirTrades = longTrades + shortTrades > 0;
  const totalDir = Math.max(1, longTrades + shortTrades);
  const longPct = hasDirTrades ? Math.round((longTrades / totalDir) * 100) : 0;
  const shortPct = hasDirTrades ? 100 - longPct : 0;

  const makerCount = stats.maker_tp_count || 0;
  const takerCount = stats.taker_exit_count || 0;
  const hasExitTrades = makerCount + takerCount > 0;
  const totalExits = Math.max(1, makerCount + takerCount);
  const makerPct = hasExitTrades ? Math.round((makerCount / totalExits) * 100) : 0;
  const takerPct = hasExitTrades ? 100 - makerPct : 0;

  const handleScrub = (clientX, target) => {
    const idx = getChartPointIndex(clientX, target, width, padL, plotW, coordPts.length);
    if (idx !== null) setHoverIdx(idx);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
      {/* Cumulative Equity Curve */}
      <div className="lg:col-span-2 surface-card p-4 sm:p-6">
        <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-2.5 mb-3.5 sm:mb-4 pb-3 border-b border-line">
          <div className="flex items-center gap-2.5">
            <div className="icon-box w-8 h-8 sm:w-9 sm:h-9">
              <svg className="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
                <polyline points="16 7 22 7 22 13" />
              </svg>
            </div>
            <h3 className="text-sm sm:text-base font-bold text-main">累计净收益与毛收益对比曲线</h3>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] sm:text-xs font-mono">
            <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-semibold">
              <span className="w-3 h-0.5 bg-emerald-500 inline-block"></span>
              扣费净收益: {(activePt ? Number(activePt.raw.net_pnl) : stats.total_net_pnl ?? 0).toFixed(4)} U
            </span>
            <span className="flex items-center gap-1.5 text-dim">
              <span className="w-3 h-0.5 bg-slate-400 inline-block"></span>
              累计毛收益: {(activePt ? Number(activePt.raw.gross_pnl) : stats.total_gross_pnl ?? 0).toFixed(4)} U
            </span>
          </div>
        </div>

        <div ref={containerRef} className="relative w-full overflow-hidden">
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
            <linearGradient id="netEquityAreaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#10b981" stopOpacity={isLight ? '0.22' : '0.28'} />
              <stop offset="100%" stopColor="#10b981" stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {[0, 0.25, 0.5, 0.75, 1].map((r, idx) => {
            const y = padT + r * plotH;
            const v = maxV - r * (maxV - minV);
            return (
              <g key={idx}>
                <line x1={padL} y1={y} x2={width - padR} y2={y} stroke={gridStroke} strokeDasharray="3 3" />
                <text x={padL - 8} y={y + 4} textAnchor="end" fill={axisTextFill} className="font-mono text-[10px]">
                  {v.toFixed(2)}
                </text>
              </g>
            );
          })}

          {/* Zero Baseline */}
          <line x1={padL} y1={zeroY} x2={width - padR} y2={zeroY} stroke={axisTextFill} strokeWidth="1" opacity="0.45" />

          {netArea && <polygon points={netArea} fill="url(#netEquityAreaGrad)" />}

          {grossLine && (
            <polyline
              fill="none"
              stroke="#94a3b8"
              strokeWidth="1.5"
              strokeDasharray="4 3"
              points={grossLine}
            />
          )}

          {netLine && (
            <polyline
              fill="none"
              stroke="#10b981"
              strokeWidth="2.4"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={netLine}
            />
          )}

          {activePt && (
            <g>
              <line
                x1={activePt.x}
                y1={padT}
                x2={activePt.x}
                y2={height - padB}
                stroke={axisTextFill}
                strokeDasharray="2 2"
                strokeWidth="1"
              />
              <circle cx={activePt.x} cy={activePt.yn} r="4" fill="#10b981" stroke="#fff" strokeWidth="1.2" />
            </g>
          )}
          </svg>
        </div>
      </div>

      {/* Directional & Fee Structure Breakdown */}
      <div className="surface-card p-4 sm:p-6 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2.5 mb-4 pb-3 border-b border-line">
            <div className="icon-box w-8 h-8 sm:w-9 sm:h-9">
              <svg className="w-4 h-4 text-sky-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.21 15.89A10 10 0 1 1 8 2.83" />
                <path d="M22 12A10 10 0 0 0 12 2v10z" />
              </svg>
            </div>
            <h3 className="text-sm sm:text-base font-bold text-main">多空收益与平仓费率分布</h3>
          </div>

          {/* Long vs Short Breakdown */}
          <div className="mb-4">
            <div className="flex flex-wrap justify-between gap-1 text-[11px] sm:text-xs font-mono mb-1.5">
              <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                多头 ({longTrades}笔 · {(stats.long_net_pnl ?? 0).toFixed(3)}U)
              </span>
              <span className="text-rose-600 dark:text-rose-400 font-medium">
                空头 ({shortTrades}笔 · {(stats.short_net_pnl ?? 0).toFixed(3)}U)
              </span>
            </div>
            <div className="w-full h-2.5 surface-subtle rounded-full overflow-hidden flex">
              <div className="bg-emerald-500 h-full transition-all duration-300" style={{ width: `${longPct}%` }} />
              <div className="bg-rose-500 h-full transition-all duration-300" style={{ width: `${shortPct}%` }} />
            </div>
            <div className="flex flex-wrap justify-between gap-x-2 gap-y-0.5 text-[11px] font-mono text-dim mt-1">
              <span>多头占比 {longPct}%</span>
              <span>空头占比 {shortPct}%</span>
            </div>
          </div>

          {/* Post-Only Maker TP vs Taker IOC Exit */}
          <div>
            <div className="flex flex-wrap justify-between gap-1 text-[11px] sm:text-xs font-mono mb-1.5">
              <span className="text-amber-600 dark:text-amber-400 font-medium">
                被动挂单止盈 ({makerCount}笔)
              </span>
              <span className="text-sky-600 dark:text-sky-400 font-medium">
                主动吃单平仓 ({takerCount}笔)
              </span>
            </div>
            <div className="w-full h-2.5 surface-subtle rounded-full overflow-hidden flex">
              <div className="bg-amber-500 h-full transition-all duration-300" style={{ width: `${makerPct}%` }} />
              <div className="bg-sky-500 h-full transition-all duration-300" style={{ width: `${takerPct}%` }} />
            </div>
            <div className="flex flex-wrap justify-between gap-1 text-[11px] font-mono text-dim mt-1">
              <span>被动省费占比 {makerPct}%</span>
              <span>累计手续费 {(stats.total_fees ?? 0).toFixed(4)}U</span>
            </div>
          </div>
        </div>

        {(() => {
          const pSum = stats.position_pnl_summary || {};
          const fallbackLongSyms = new Set(
            (activePositions || [])
              .filter((p) => String(p?.side || '').toUpperCase() === 'LONG' && p?.symbol)
              .map((p) => p.symbol)
          );
          const fallbackShortSyms = new Set(
            (activePositions || [])
              .filter((p) => String(p?.side || '').toUpperCase() === 'SHORT' && p?.symbol)
              .map((p) => p.symbol)
          );
          const activeLongSymbols =
            typeof stats.active_long_symbols_count === 'number'
              ? stats.active_long_symbols_count
              : typeof pSum.active_long_symbols_count === 'number'
              ? pSum.active_long_symbols_count
              : fallbackLongSyms.size;
          const activeShortSymbols =
            typeof stats.active_short_symbols_count === 'number'
              ? stats.active_short_symbols_count
              : typeof pSum.active_short_symbols_count === 'number'
              ? pSum.active_short_symbols_count
              : fallbackShortSyms.size;
          const activeLongSlots =
            typeof stats.active_long_slots === 'number'
              ? stats.active_long_slots
              : typeof pSum.active_long_slots === 'number'
              ? pSum.active_long_slots
              : (activePositions || []).filter((p) => String(p?.side || '').toUpperCase() === 'LONG').length;
          const activeShortSlots =
            typeof stats.active_short_slots === 'number'
              ? stats.active_short_slots
              : typeof pSum.active_short_slots === 'number'
              ? pSum.active_short_slots
              : (activePositions || []).filter((p) => String(p?.side || '').toUpperCase() === 'SHORT').length;
          return (
            <div className="mt-3 pt-3 border-t border-line grid grid-cols-2 gap-2.5 text-xs font-mono">
              <div className="p-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.05] min-w-0">
                <div className="text-dim text-[11px] font-sans leading-snug">当前做多标的</div>
                <div className="text-emerald-600 dark:text-emerald-400 font-bold text-sm mt-0.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span>{activeLongSymbols} 个</span>
                  {activeLongSlots > activeLongSymbols && (
                    <span className="text-[10px] font-normal text-dim">({activeLongSlots} 槽位)</span>
                  )}
                </div>
              </div>
              <div className="p-2.5 rounded-lg border border-rose-500/20 bg-rose-500/[0.05] min-w-0">
                <div className="text-dim text-[11px] font-sans leading-snug">当前做空标的</div>
                <div className="text-rose-600 dark:text-rose-400 font-bold text-sm mt-0.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span>{activeShortSymbols} 个</span>
                  {activeShortSlots > activeShortSymbols && (
                    <span className="text-[10px] font-normal text-dim">({activeShortSlots} 槽位)</span>
                  )}
                </div>
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
