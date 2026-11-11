import React, { useMemo, useState } from 'react';

export default function RadarChart({ symbol, series = [], telemetry = {}, strategy = {}, theme = 'light' }) {
  const [hoverIdx, setHoverIdx] = useState(null);

  const width = 880;
  const height = 245;
  const padLeft = 70;
  const padRight = 20;
  const padTop = 18;
  const padBottom = 26;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

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

    const span = Math.max(maxVal - minVal, maxVal * 0.00055, 1e-6);
    const lo = minVal - span * 0.22;
    const hi = maxVal + span * 0.22;

    const toX = (i) => padLeft + (i / (series.length - 1)) * plotW;
    const toY = (v) => padTop + (1 - (v - lo) / (hi - lo)) * plotH;

    const pts = [];
    for (let i = 0; i < series.length; i++) {
      const l = Number(series[i].leader_calibrated || series[i].leader_mid);
      const f = Number(series[i].follower_mid);
      const x = toX(i);
      const yl = toY(l);
      const yf = toY(f);
      const prevL = i > 0 ? Number(series[i - 1].leader_calibrated || series[i - 1].leader_mid) : l;
      const prevF = i > 0 ? Number(series[i - 1].follower_mid) : f;
      pts.push({
        x,
        yl,
        yf,
        lChanged: Math.abs(l - prevL) > 1e-9,
        fChanged: Math.abs(f - prevF) > 1e-9,
        raw: series[i],
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
  }, [series, plotW, plotH]);

  const pricePrec = strategy?.price_precision ?? 4;
  const activePoint =
    hoverIdx !== null && coordList[hoverIdx] ? coordList[hoverIdx] : coordList[coordList.length - 1] || null;
  const activeRaw = activePoint ? activePoint.raw : latest;
  const spreadBps = activeRaw ? Number(activeRaw.spread_bps || 0) : telemetry?.live_spread_bps ?? 0;
  const tpThresholdBps = ((strategy?.take_profit_pct ?? 0.0025) + 0.0012) * 10000;

  return (
    <div className="surface-card rounded-xl p-4">
      {/* Chart Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3 pb-2.5 border-b border-line">
        <div className="flex items-center gap-2.5">
          <span className="px-2.5 py-0.5 rounded text-xs font-mono font-semibold bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/25">
            {symbol}
          </span>
          <h3 className="text-sm font-semibold text-main">双所毫秒级对齐逐笔 Tick 阶梯曲线与套利窗口</h3>
          {telemetry?.basis_frozen && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400">
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              脉冲期基差已锁定
            </span>
          )}
        </div>

        {/* Step-Line Legend & Readout */}
        <div className="flex flex-wrap items-center gap-4 text-xs font-mono">
          <div className="flex items-center gap-1.5">
            <svg className="w-4 h-3" viewBox="0 0 16 10" fill="none">
              <path d="M1 8 H8 V2 H15" stroke={leaderColor} strokeWidth="2" strokeLinecap="square" />
            </svg>
            <span className="text-dim">主所校准 Tick:</span>
            <span className="font-semibold text-main">
              {activeRaw ? Number(activeRaw.leader_calibrated).toFixed(pricePrec) : '--'}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <svg className="w-4 h-3" viewBox="0 0 16 10" fill="none">
              <path d="M1 8 H8 V2 H15" stroke={followerColor} strokeWidth="2" strokeLinecap="square" />
            </svg>
            <span className="text-dim">从所盘口 Tick:</span>
            <span className="font-semibold text-main">
              {activeRaw ? Number(activeRaw.follower_mid).toFixed(pricePrec) : '--'}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
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
      <div className="relative w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-56 select-none cursor-crosshair"
          onMouseLeave={() => setHoverIdx(null)}
          onMouseMove={(e) => {
            if (!coordList.length) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const relX = ((e.clientX - rect.left) / rect.width) * width;
            const ratio = Math.max(0, Math.min(1, (relX - padLeft) / plotW));
            const idx = Math.round(ratio * (coordList.length - 1));
            setHoverIdx(idx);
          }}
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
                  strokeDasharray="3 3"
                  strokeWidth="1"
                />
                <text x={padLeft - 8} y={y + 4} textAnchor="end" fill={axisTextFill} className="font-mono text-[10px]">
                  {val.toFixed(pricePrec)}
                </text>
              </g>
            );
          })}

          {/* Shaded Step-After Lead-Lag Divergence Window */}
          {stepAreaPath && <path d={stepAreaPath} fill="url(#hornStepSpreadFill)" />}

          {/* Follower Step-After Tick Path */}
          {followerStepPath && (
            <path
              d={followerStepPath}
              fill="none"
              stroke={followerColor}
              strokeWidth="1.9"
              strokeLinejoin="miter"
              strokeLinecap="butt"
            />
          )}

          {/* Leader Calibrated Step-After Tick Path */}
          {leaderStepPath && (
            <path
              d={leaderStepPath}
              fill="none"
              stroke={leaderColor}
              strokeWidth="2.1"
              strokeLinejoin="miter"
              strokeLinecap="butt"
            />
          )}

          {/* Discrete Tick Jump Dots on Price Changes */}
          {coordList.map((pt, i) => (
            <g key={i}>
              {pt.fChanged && <circle cx={pt.x} cy={pt.yf} r="1.7" fill={followerColor} />}
              {pt.lChanged && <circle cx={pt.x} cy={pt.yl} r="1.9" fill={leaderColor} />}
            </g>
          ))}

          {/* Interactive Crosshair & Endpoint Markers */}
          {activePoint && (
            <g>
              <line
                x1={activePoint.x}
                y1={padTop}
                x2={activePoint.x}
                y2={height - padBottom}
                stroke={axisTextFill}
                strokeDasharray="2 2"
                strokeWidth="1"
              />
              <rect
                x={activePoint.x - 3.5}
                y={activePoint.yl - 3.5}
                width="7"
                height="7"
                fill={leaderColor}
                stroke="#fff"
                strokeWidth="1.2"
              />
              <rect
                x={activePoint.x - 3.5}
                y={activePoint.yf - 3.5}
                width="7"
                height="7"
                fill={followerColor}
                stroke="#fff"
                strokeWidth="1.2"
              />
            </g>
          )}
        </svg>
      </div>

      {/* Bottom Bidirectional Spread Oscillator */}
      <div className="mt-2 pt-3 border-t border-line">
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-dim mb-1.5">
          <span>实时基差校准偏离振荡器 (正值做多窗口 / 负值做空窗口 · 开仓触发线 ±{tpThresholdBps.toFixed(0)} 基点)</span>
          <div className="flex items-center gap-3">
            <span>
              30秒对数基差: <strong className="text-main">{telemetry?.basis_bps ?? 0} 基点</strong>
            </span>
            <span>
              回归收敛系数 β: <strong className="text-main">{telemetry?.estimated_beta ?? 0.88}</strong>
            </span>
            <span>
              已实现波动率 σ: <strong className="text-main">{telemetry?.volatility_sigma_bps ?? 0} 基点</strong>
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
