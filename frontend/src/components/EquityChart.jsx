import React, { useMemo, useState } from 'react';

export default function EquityChart({ equityCurve = [], stats = {}, theme = 'dark' }) {
  const [hoverIdx, setHoverIdx] = useState(null);

  const width = 640;
  const height = 220;
  const padL = 58;
  const padR = 18;
  const padT = 18;
  const padB = 26;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

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
  }, [equityCurve, plotW, plotH]);

  const activePt =
    hoverIdx !== null && coordPts[hoverIdx] ? coordPts[hoverIdx] : coordPts[coordPts.length - 1] || null;

  const longTrades = stats.long_trades || 0;
  const shortTrades = stats.short_trades || 0;
  const totalDir = Math.max(1, longTrades + shortTrades);
  const longPct = Math.round((longTrades / totalDir) * 100);
  const shortPct = 100 - longPct;

  const makerCount = stats.maker_tp_count || 0;
  const takerCount = stats.taker_exit_count || 0;
  const totalExits = Math.max(1, makerCount + takerCount);
  const makerPct = Math.round((makerCount / totalExits) * 100);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Cumulative Equity Curve */}
      <div className="lg:col-span-2 surface-card rounded-xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3 pb-2.5 border-b border-line">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
              <polyline points="16 7 22 7 22 13" />
            </svg>
            <h3 className="text-sm font-semibold text-main">累计净收益与毛收益对比曲线</h3>
          </div>

          <div className="flex items-center gap-4 text-xs font-mono">
            <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-semibold">
              <span className="w-3 h-0.5 bg-emerald-500 inline-block"></span>
              扣费净收益: {(activePt ? Number(activePt.raw.net_pnl) : stats.total_net_pnl ?? 0).toFixed(4)} USDT
            </span>
            <span className="flex items-center gap-1.5 text-dim">
              <span className="w-3 h-0.5 bg-slate-400 inline-block"></span>
              累计毛收益: {(activePt ? Number(activePt.raw.gross_pnl) : stats.total_gross_pnl ?? 0).toFixed(4)} USDT
            </span>
          </div>
        </div>

        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-48 select-none cursor-crosshair"
          onMouseLeave={() => setHoverIdx(null)}
          onMouseMove={(e) => {
            if (!coordPts.length) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const relX = ((e.clientX - rect.left) / rect.width) * width;
            const ratio = Math.max(0, Math.min(1, (relX - padL) / plotW));
            setHoverIdx(Math.round(ratio * (coordPts.length - 1)));
          }}
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

      {/* Directional & Fee Structure Breakdown */}
      <div className="surface-card rounded-xl p-4 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2 mb-3 pb-2.5 border-b border-line">
            <svg className="w-4 h-4 text-sky-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21.21 15.89A10 10 0 1 1 8 2.83" />
              <path d="M22 12A10 10 0 0 0 12 2v10z" />
            </svg>
            <h3 className="text-sm font-semibold text-main">多空收益与平仓费率结构分布</h3>
          </div>

          {/* Long vs Short Breakdown */}
          <div className="mb-4">
            <div className="flex justify-between text-xs font-mono mb-1.5">
              <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                多头成交 ({longTrades} 笔 · {(stats.long_net_pnl ?? 0).toFixed(3)} U)
              </span>
              <span className="text-rose-600 dark:text-rose-400 font-medium">
                空头成交 ({shortTrades} 笔 · {(stats.short_net_pnl ?? 0).toFixed(3)} U)
              </span>
            </div>
            <div className="w-full h-2.5 surface-subtle rounded-full overflow-hidden flex">
              <div className="bg-emerald-500 h-full transition-all duration-300" style={{ width: `${longPct}%` }} />
              <div className="bg-rose-500 h-full transition-all duration-300" style={{ width: `${shortPct}%` }} />
            </div>
            <div className="flex justify-between text-[11px] font-mono text-dim mt-1">
              <span>多头占比 {longPct}%</span>
              <span>空头占比 {shortPct}%</span>
            </div>
          </div>

          {/* Post-Only Maker TP vs Taker IOC Exit */}
          <div>
            <div className="flex justify-between text-xs font-mono mb-1.5">
              <span className="text-amber-600 dark:text-amber-400 font-medium">
                被动挂单止盈 ({makerCount} 笔)
              </span>
              <span className="text-sky-600 dark:text-sky-400 font-medium">
                主动吃单平仓 ({takerCount} 笔)
              </span>
            </div>
            <div className="w-full h-2.5 surface-subtle rounded-full overflow-hidden flex">
              <div className="bg-amber-500 h-full transition-all duration-300" style={{ width: `${makerPct}%` }} />
              <div className="bg-sky-500 h-full transition-all duration-300" style={{ width: `${100 - makerPct}%` }} />
            </div>
            <div className="flex justify-between text-[11px] font-mono text-dim mt-1">
              <span>被动挂单省费占比 {makerPct}%</span>
              <span>累计手续费支出 {(stats.total_fees ?? 0).toFixed(4)} U</span>
            </div>
          </div>
        </div>

        <div className="mt-3 pt-3 border-t border-line grid grid-cols-2 gap-2.5 text-xs font-mono">
          <div className="surface-subtle p-2.5 rounded-lg">
            <div className="text-dim text-[11px]">成交回报对账核验</div>
            <div className="text-main font-semibold mt-0.5">{stats.reconciled_count ?? 0} 次</div>
          </div>
          <div className="surface-subtle p-2.5 rounded-lg">
            <div className="text-dim text-[11px]">当前连续亏损计数</div>
            <div className="text-main font-semibold mt-0.5">{stats.consecutive_losses ?? 0} 笔</div>
          </div>
        </div>
      </div>
    </div>
  );
}
