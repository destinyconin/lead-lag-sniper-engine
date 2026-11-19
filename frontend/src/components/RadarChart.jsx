import React, { useMemo, useState } from 'react';
import { getChartPointIndex, useContainerChartSize } from '../utils/useContainerChartSize';

export default function RadarChart({ symbol, series = [], telemetry = {}, strategy = {}, theme = 'light' }) {
  const [hoverIdx, setHoverIdx] = useState(null);
  const pricePrec = Math.min(12, Math.max(0, Math.round(Number(strategy?.price_precision ?? 4) || 4)));

  const { containerRef, width, height, isCompact } = useContainerChartSize({
    defaultWidth: 880,
    mobileHeight: 205,
    tabletHeight: 225,
    desktopHeight: 245,
  });

  const padLeft = pricePrec >= 6 ? (isCompact ? 64 : 76) : isCompact ? 50 : 68;
  const padRight = isCompact ? 10 : 20;
  const padTop = 16;
  const padBottom = isCompact ? 18 : 24;
  const plotW = Math.max(120, width - padLeft - padRight);
  const plotH = Math.max(80, height - padTop - padBottom);

  const isLight = theme === 'light';
  const gridStroke = isLight ? '#e2e8f0' : '#1e293b';
  const axisTextFill = isLight ? '#64748b' : '#94a3b8';
  const leaderColor = isLight ? '#d97706' : '#f59e0b';
  const followerColor = isLight ? '#0284c7' : '#38bdf8';

  const { leaderStepPath, followerStepPath, stepAreaPath, minP, maxP, latest, coordList } = useMemo(() => {
    if (!series || series.length < 2) {
      return {
        leaderStepPath: '',
        followerStepPath: '',
        stepAreaPath: '',
        minP: 0,
        maxP: 1,
        latest: null,
        coordList: [],
      };
    }

    let minVal = Infinity;
    let maxVal = -Infinity;
    for (const pt of series) {
      const l = Number(pt.leader_calibrated || pt.leader_mid || 0);
      const f = Number(pt.follower_mid || 0);
      if (l > 0 && f > 0) {
        if (l < minVal) minVal = l;
        if (f < minVal) minVal = f;
        if (l > maxVal) maxVal = l;
        if (f > maxVal) maxVal = f;
      }
    }

    if (!isFinite(minVal) || !isFinite(maxVal)) {
      return {
        leaderStepPath: '',
        followerStepPath: '',
        stepAreaPath: '',
        minP: 0,
        maxP: 1,
        latest: null,
        coordList: [],
      };
    }

    const span = Math.max(maxVal - minVal, maxVal * 0.0004, 1e-12);
    const lo = minVal - span * 0.22;
    const hi = maxVal + span * 0.22;
    const denom = Math.max(hi - lo, 1e-12);
    const changeEps = Math.max(maxVal * 1e-7, 1e-12);

    const validSeries = series.filter((pt) => {
      const l = Number(pt?.leader_calibrated || pt?.leader_mid || 0);
      const f = Number(pt?.follower_mid || 0);
      return Number.isFinite(l) && Number.isFinite(f) && l > 0 && f > 0;
    });
    if (validSeries.length < 2) {
      return {
        leaderStepPath: '',
        followerStepPath: '',
        stepAreaPath: '',
        minP: lo,
        maxP: hi,
        latest: series[series.length - 1] || null,
        coordList: [],
      };
    }

    const toX = (i) => padLeft + (i / (validSeries.length - 1)) * plotW;
    const toY = (v) => padTop + (1 - (v - lo) / denom) * plotH;

    const pts = [];
    for (let i = 0; i < validSeries.length; i++) {
      const l = Number(validSeries[i].leader_calibrated || validSeries[i].leader_mid);
      const f = Number(validSeries[i].follower_mid);
      const x = toX(i);
      const yl = toY(l);
      const yf = toY(f);
      const prevL = i > 0 ? Number(validSeries[i - 1].leader_calibrated || validSeries[i - 1].leader_mid) : l;
      const prevF = i > 0 ? Number(validSeries[i - 1].follower_mid) : f;
      pts.push({
        x,
        yl,
        yf,
        lChanged: Math.abs(l - prevL) > changeEps,
        fChanged: Math.abs(f - prevF) > changeEps,
        raw: validSeries[i],
      });
    }

    // Build true Step-After SVG path (`M x0 y0 H x1 V y1 H x2 V y2 ...`) for authentic Tick look
    const buildStepAfterPath = (key) => {
      let d = `M ${pts[0].x.toFixed(1)} ${pts[0][key].toFixed(1)}`;
      for (let i = 1; i < pts.length; i++) {
        d += ` H ${pts[i].x.toFixed(1)} V ${pts[i][key].toFixed(1)}`;
      }
      return d;
    };

    const lPath = buildStepAfterPath('yl');
    const fPath = buildStepAfterPath('yf');

    // Build closed step-after area between Leader and Follower
    let areaD = `M ${pts[0].x.toFixed(1)} ${pts[0].yl.toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      areaD += ` H ${pts[i].x.toFixed(1)} V ${pts[i].yl.toFixed(1)}`;
    }
    areaD += ` L ${pts[pts.length - 1].x.toFixed(1)} ${pts[pts.length - 1].yf.toFixed(1)}`;
    for (let i = pts.length - 1; i >= 1; i--) {
      areaD += ` V ${pts[i - 1].yf.toFixed(1)} H ${pts[i - 1].x.toFixed(1)}`;
    }
    areaD += ' Z';

    return {
      leaderStepPath: lPath,
      followerStepPath: fPath,
      stepAreaPath: areaD,
      minP: lo,
      maxP: hi,
      latest: series[series.length - 1],
      coordList: pts,
    };
  }, [series, plotW, plotH, padLeft, padTop]);

  const activePoint =
    hoverIdx !== null && coordList[hoverIdx] ? coordList[hoverIdx] : coordList[coordList.length - 1] || null;
  const activeRaw = activePoint ? activePoint.raw : latest;
  const refPrice = activeRaw ? Number(activeRaw.follower_mid || activeRaw.leader_calibrated || 0) : maxP;
  const effPricePrec =
    refPrice > 0 && refPrice < 0.0001
      ? Math.max(pricePrec, 8)
      : refPrice > 0 && refPrice < 0.01
      ? Math.max(pricePrec, 6)
      : pricePrec;
  const spreadBps = activeRaw ? Number(activeRaw.spread_bps || 0) : telemetry?.live_spread_bps ?? 0;
  const tpThresholdBps = ((strategy?.take_profit_pct ?? 0.0025) + 0.0012) * 10000;

  const handleScrub = (clientX, target) => {
    const idx = getChartPointIndex(clientX, target, width, padLeft, plotW, coordList.length);
    if (idx !== null) setHoverIdx(idx);
  };

  return (
    <div className="surface-card p-4 sm:p-6">
      {/* Chart Header */}
      <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-3 mb-3.5 sm:mb-4 pb-3 border-b border-line">
        <div className="flex flex-wrap items-center gap-2">
          <span className="px-2.5 sm:px-3 py-1 rounded-full text-xs font-mono font-bold surface-subtle text-main">
            {symbol}
          </span>
          <h3 className="text-sm sm:text-base font-bold text-main">双所毫秒级对齐逐笔 Tick 阶梯曲线</h3>
          {telemetry?.basis_frozen && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 text-[11px] font-semibold rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400">
              脉冲期基差已锁定
            </span>
          )}
        </div>

        {/* Step-Line Legend & Readout */}
        <div className="grid grid-cols-2 sm:flex sm:flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] sm:text-xs font-mono">
          <div className="flex items-center gap-1.5">
            <svg className="w-4 h-3 shrink-0" viewBox="0 0 16 10" fill="none">
              <path
                d="M1 8 H8 V2 H15"
                stroke={leaderColor}
                strokeWidth="1.15"
                vectorEffect="non-scaling-stroke"
                strokeLinecap="butt"
              />
            </svg>
            <span className="text-dim">主所:</span>
            <span className="font-semibold text-main">
              {activeRaw ? Number(activeRaw.leader_calibrated || activeRaw.leader_mid || 0).toFixed(effPricePrec) : '--'}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <svg className="w-4 h-3 shrink-0" viewBox="0 0 16 10" fill="none">
              <path
                d="M1 8 H8 V2 H15"
                stroke={followerColor}
                strokeWidth="1.05"
                vectorEffect="non-scaling-stroke"
                strokeLinecap="butt"
              />
            </svg>
            <span className="text-dim">从所:</span>
            <span className="font-semibold text-main">
              {activeRaw ? Number(activeRaw.follower_mid || 0).toFixed(effPricePrec) : '--'}
            </span>
          </div>

          <div className="col-span-2 sm:col-span-1 flex items-center gap-1.5">
            <span className="text-dim">滞后套利空间:</span>
            <span
              className={`font-semibold px-1.5 py-0.5 rounded border ${
                Math.abs(spreadBps) >= tpThresholdBps
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                  : 'surface-subtle text-sub'
              }`}
            >
              {spreadBps >= 0 ? `+${spreadBps.toFixed(1)}` : spreadBps.toFixed(1)} 基点
            </span>
          </div>
        </div>
      </div>

      {/* Main Step-After Tick Chart */}
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
            <linearGradient id="hornStepSpreadFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={spreadBps >= 0 ? '#10b981' : '#f43f5e'} stopOpacity={isLight ? '0.18' : '0.26'} />
              <stop offset="100%" stopColor={spreadBps >= 0 ? '#10b981' : '#f43f5e'} stopOpacity="0.04" />
            </linearGradient>
          </defs>

          {/* Horizontal Grid Lines & Y-Axis Labels */}
          {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
            const y = padTop + ratio * plotH;
            const val = maxP - ratio * (maxP - minP);
            return (
              <g key={idx}>
                <line
                  x1={padLeft}
                  y1={y}
                  x2={width - padRight}
                  y2={y}
                  stroke={gridStroke}
                  strokeDasharray="2.5 2.5"
                  strokeWidth="0.65"
                  vectorEffect="non-scaling-stroke"
                  shapeRendering="crispEdges"
                />
                <text x={padLeft - 8} y={y + 4} textAnchor="end" fill={axisTextFill} className="font-mono text-[10px]">
                  {val.toFixed(effPricePrec)}
                </text>
              </g>
            );
          })}

          {/* Shaded Step-After Lead-Lag Divergence Window */}
          {stepAreaPath && <path d={stepAreaPath} fill="url(#hornStepSpreadFill)" />}

          {/* Follower Step-After Tick Path (Fine Vector Line) */}
          {followerStepPath && (
            <path
              d={followerStepPath}
              fill="none"
              stroke={followerColor}
              strokeWidth="1.0"
              vectorEffect="non-scaling-stroke"
              shapeRendering="geometricPrecision"
              strokeLinejoin="miter"
              strokeLinecap="butt"
            />
          )}

          {/* Leader Calibrated Step-After Tick Path (Fine Vector Line) */}
          {leaderStepPath && (
            <path
              d={leaderStepPath}
              fill="none"
              stroke={leaderColor}
              strokeWidth="1.15"
              vectorEffect="non-scaling-stroke"
              shapeRendering="geometricPrecision"
              strokeLinejoin="miter"
              strokeLinecap="butt"
            />
          )}

          {/* Subtle Micro Tick Jump Dots on Price Changes */}
          {coordList.map((pt, i) => (
            <g key={i}>
              {pt.fChanged && <circle cx={pt.x} cy={pt.yf} r="0.95" fill={followerColor} />}
              {pt.lChanged && <circle cx={pt.x} cy={pt.yl} r="1.05" fill={leaderColor} />}
            </g>
          ))}

          {/* Interactive Crosshair & Fine Endpoint Markers */}
          {activePoint && (
            <g>
              <line
                x1={activePoint.x}
                y1={padTop}
                x2={activePoint.x}
                y2={height - padBottom}
                stroke={axisTextFill}
                strokeDasharray="2 2"
                strokeWidth="0.7"
                vectorEffect="non-scaling-stroke"
                shapeRendering="crispEdges"
              />
              <circle
                cx={activePoint.x}
                cy={activePoint.yl}
                r="2.4"
                fill={leaderColor}
                stroke="#fff"
                strokeWidth="0.9"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={activePoint.x}
                cy={activePoint.yf}
                r="2.4"
                fill={followerColor}
                stroke="#fff"
                strokeWidth="0.9"
                vectorEffect="non-scaling-stroke"
              />
            </g>
          )}
        </svg>
      </div>

      {/* Bottom Bidirectional Spread Oscillator */}
      <div className="mt-2 pt-3 border-t border-line">
        <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-1.5 sm:gap-2 text-[11px] font-mono text-dim mb-1.5">
          <span>实时偏离振荡器 (正值做多 / 负值做空 · 触发线 ±{tpThresholdBps.toFixed(0)}bp)</span>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              30秒基差: <strong className="text-main">{telemetry?.basis_bps ?? 0}bp</strong>
            </span>
            <span>
              系数 β: <strong className="text-main">{telemetry?.estimated_beta ?? 0.88}</strong>
            </span>
            <span>
              波动率 σ: <strong className="text-main">{telemetry?.volatility_sigma_bps ?? 0}bp</strong>
            </span>
          </div>
        </div>

        <div className="relative h-14 surface-subtle rounded-lg px-2 flex items-center gap-[2px] overflow-hidden">
          <div className="absolute inset-x-0 top-1/2 h-[1px] bg-slate-400/30 pointer-events-none" />

          {series.slice(-80).map((pt, i) => {
            const bps = Number(pt.spread_bps || 0);
            const maxScale = Math.max(tpThresholdBps * 1.5, 38);
            const halfHeightPct = Math.min(48, (Math.abs(bps) / maxScale) * 48);
            const isFire = Math.abs(bps) >= tpThresholdBps;
            const isPositive = bps >= 0;

            const barColor = isPositive
              ? isFire
                ? 'bg-emerald-500'
                : 'bg-emerald-500/45'
              : isFire
              ? 'bg-rose-500'
              : 'bg-rose-500/45';

            return (
              <div key={i} className="relative flex-1 h-full">
                <div
                  className={`absolute inset-x-0 rounded-[1px] transition-all duration-75 ${barColor}`}
                  style={
                    isPositive
                      ? { bottom: '50%', height: `${Math.max(3, halfHeightPct)}%` }
                      : { top: '50%', height: `${Math.max(3, halfHeightPct)}%` }
                  }
                  title={`校准偏离: ${bps} 基点 | 主所瞬时脉冲: ${pt.jump_bps} 基点`}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
