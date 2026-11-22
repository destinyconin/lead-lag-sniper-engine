"""
Hourly Crypto Market vs Traditional Market Sentiment Comparison Service.

Collects real hourly data from:
- Crypto (24/7):
  * OKX V5 1H candles for BTC-USDT-SWAP, ETH-USDT-SWAP, SOL-USDT-SWAP
  * OKX V5 1H BTC Taker Buy/Sell Volume & Long/Short Account Ratio
  * OKX V5 BTC-USDT-SWAP Funding Rate
  * Alternative.me Crypto Fear & Greed Index (daily macro anchor)
- Traditional Markets (Wall Street / US Equities & Macro):
  * Yahoo Finance 1H candles for ES=F (S&P 500 Futures), NQ=F (Nasdaq 100 Futures),
    ^VIX (CBOE Volatility Index), GC=F (Gold Futures safe-haven gauge)
  * CNN Fear & Greed Index (daily macro anchor)

Aligns both markets onto UTC top-of-hour buckets, computes transparent 0~100 hourly
sentiment scores for both markets, tracks hourly divergence (`crypto - trad`) and
24H overlapping Pearson correlation, and caches results in memory with background refresh.
"""

from __future__ import annotations

import asyncio
import datetime
import math
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
from backend.market_gateway import get_system_proxy


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if not math.isfinite(val):
        return 50.0
    return max(lo, min(hi, val))


def _tanh_score(z: float, amplitude: float = 46.0) -> float:
    if not math.isfinite(z):
        return 50.0
    return _clamp(50.0 + amplitude * math.tanh(z), 2.0, 98.0)


def sentiment_regime_label(score: float) -> str:
    if score < 25.0:
        return "极度恐慌"
    if score < 45.0:
        return "恐慌"
    if score <= 55.0:
        return "中性"
    if score < 75.0:
        return "贪婪"
    return "极度贪婪"


def _compute_ema_map(candles: Dict[int, dict], span: int = 20) -> Dict[int, float]:
    hours = sorted(candles.keys())
    if not hours:
        return {}
    alpha = 2.0 / (span + 1.0)
    ema_map: Dict[int, float] = {}
    ema_val = float(candles[hours[0]]["c"])
    for h in hours:
        c = float(candles[h]["c"])
        ema_val = alpha * c + (1.0 - alpha) * ema_val
        ema_map[h] = ema_val
    return ema_map


def _return_over_steps(sorted_hours: List[int], idx: int, candles: Dict[int, dict], steps: int) -> float:
    if idx < 0 or idx >= len(sorted_hours):
        return 0.0
    prev_idx = max(0, idx - steps)
    c_now = float(candles[sorted_hours[idx]]["c"])
    c_prev = float(candles[sorted_hours[prev_idx]]["c"])
    if c_prev <= 0 or c_now <= 0:
        return 0.0
    return (c_now - c_prev) / c_prev


def _pearson_corr(xs: List[float], ys: List[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 4:
        return None
    xs = xs[-n:]
    ys = ys[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-9 or dy < 1e-9:
        return 0.0
    return round(max(-1.0, min(1.0, num / (dx * dy))), 2)


def _date_key_utc(ts_sec: int) -> str:
    return datetime.datetime.fromtimestamp(ts_sec, datetime.timezone.utc).strftime("%Y-%m-%d")


def _lookup_daily_anchor(anchor_map: Dict[str, float], ts_sec: int, default_val: Optional[float]) -> Optional[float]:
    if not anchor_map:
        return default_val
    d_key = _date_key_utc(ts_sec)
    if d_key in anchor_map:
        return anchor_map[d_key]
    # Walk backwards up to 5 days to bridge weekends/holidays cleanly
    dt = datetime.datetime.fromtimestamp(ts_sec, datetime.timezone.utc)
    for back in range(1, 6):
        prev_key = (dt - datetime.timedelta(days=back)).strftime("%Y-%m-%d")
        if prev_key in anchor_map:
            return anchor_map[prev_key]
    return default_val


def _parse_okx_candles(payload: Any) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return out
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            ts_sec = (int(row[0]) // 1000 // 3600) * 3600
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            vol = float(row[5])
            if c > 0 and h > 0 and l > 0:
                out[ts_sec] = {"o": o, "h": h, "l": l, "c": c, "v": vol}
        except Exception:
            continue
    return out


def _parse_okx_taker_vol(payload: Any) -> Dict[int, float]:
    """Parses OKX V5 rubik/stat/taker-volume -> {hour_sec: buy_ratio (0..1)}."""
    out: Dict[int, float] = {}
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return out
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            ts_sec = (int(row[0]) // 1000 // 3600) * 3600
            sell_v = float(row[1])
            buy_v = float(row[2])
            tot = buy_v + sell_v
            if tot > 0:
                out[ts_sec] = buy_v / tot
        except Exception:
            continue
    return out


def _parse_okx_ls_ratio(payload: Any) -> Dict[int, float]:
    out: Dict[int, float] = {}
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return out
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            ts_sec = (int(row[0]) // 1000 // 3600) * 3600
            val = float(row[1])
            if val > 0:
                out[ts_sec] = val
        except Exception:
            continue
    return out


def _parse_yahoo_candles(payload: Any) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    if not isinstance(payload, dict):
        return out
    try:
        res = (payload.get("chart") or {}).get("result") or []
        if not res:
            return out
        node = res[0]
        timestamps = node.get("timestamp") or []
        quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        for i, ts in enumerate(timestamps):
            if ts is None or i >= len(closes) or closes[i] is None:
                continue
            c = float(closes[i])
            if c <= 0 or not math.isfinite(c):
                continue
            o = float(opens[i]) if i < len(opens) and opens[i] is not None else c
            h = float(highs[i]) if i < len(highs) and highs[i] is not None else max(o, c)
            l = float(lows[i]) if i < len(lows) and lows[i] is not None else min(o, c)
            v = float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0
            ts_sec = (int(ts) // 3600) * 3600
            out[ts_sec] = {"o": o, "h": h, "l": l, "c": c, "v": v}
    except Exception:
        return out
    return out


def compute_hourly_sentiment_comparison(
    btc_candles: Dict[int, dict],
    eth_candles: Dict[int, dict],
    sol_candles: Dict[int, dict],
    taker_buy_ratio: Dict[int, float],
    ls_ratio_map: Dict[int, float],
    funding_rate: Optional[float],
    crypto_fng_daily: Dict[str, float],
    crypto_fng_latest: Optional[float],
    es_candles: Dict[int, dict],
    nq_candles: Dict[int, dict],
    vix_candles: Dict[int, dict],
    gc_candles: Dict[int, dict],
    cnn_fng_daily: Dict[str, float],
    cnn_fng_latest: Optional[float],
    now_ts_sec: Optional[int] = None,
    data_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Pure deterministic calculator that turns raw hourly crypto & tradfi series into
    aligned hourly 0~100 sentiment curves (`series_72h` and `series_overlap_24h`)
    plus summary metrics and divergence/correlation analysis.
    """
    now_sec = int(now_ts_sec if now_ts_sec is not None else time.time())
    btc_hours = sorted(btc_candles.keys())
    eth_hours = sorted(eth_candles.keys())
    sol_hours = sorted(sol_candles.keys())
    es_hours = sorted(es_candles.keys())
    nq_hours = sorted(nq_candles.keys())
    vix_hours = sorted(vix_candles.keys())
    gc_hours = sorted(gc_candles.keys())

    btc_idx = {h: i for i, h in enumerate(btc_hours)}
    eth_idx = {h: i for i, h in enumerate(eth_hours)}
    sol_idx = {h: i for i, h in enumerate(sol_hours)}
    es_idx = {h: i for i, h in enumerate(es_hours)}
    nq_idx = {h: i for i, h in enumerate(nq_hours)}
    vix_idx = {h: i for i, h in enumerate(vix_hours)}
    gc_idx = {h: i for i, h in enumerate(gc_hours)}

    btc_ema = _compute_ema_map(btc_candles, 20)
    eth_ema = _compute_ema_map(eth_candles, 20)
    sol_ema = _compute_ema_map(sol_candles, 20)
    es_ema = _compute_ema_map(es_candles, 20)
    nq_ema = _compute_ema_map(nq_candles, 20)

    # 1. Compute Crypto hourly sentiment for every BTC hour
    crypto_by_hour: Dict[int, dict] = {}
    ls_hours_sorted = sorted(ls_ratio_map.keys())
    ls_idx = {h: i for i, h in enumerate(ls_hours_sorted)}
    fund_val = float(funding_rate) if funding_rate is not None and math.isfinite(funding_rate) else 0.0
    z_fund = fund_val / 0.00025  # +0.025% funding => z=+1

    for i, h in enumerate(btc_hours):
        b_bar = btc_candles[h]
        b_c = float(b_bar["c"])
        b_r1 = _return_over_steps(btc_hours, i, btc_candles, 1)
        b_r6 = _return_over_steps(btc_hours, i, btc_candles, 6)
        b_r24 = _return_over_steps(btc_hours, i, btc_candles, 24)
        b_dev = (b_c - btc_ema.get(h, b_c)) / max(1e-9, btc_ema.get(h, b_c))

        # Blend ETH & SOL when aligned
        r1_sum, r6_sum, r24_sum, dev_sum, w_sum = b_r1 * 0.50, b_r6 * 0.50, b_r24 * 0.50, b_dev * 0.50, 0.50
        if h in eth_idx:
            ei = eth_idx[h]
            ec = float(eth_candles[h]["c"])
            r1_sum += _return_over_steps(eth_hours, ei, eth_candles, 1) * 0.30
            r6_sum += _return_over_steps(eth_hours, ei, eth_candles, 6) * 0.30
            r24_sum += _return_over_steps(eth_hours, ei, eth_candles, 24) * 0.30
            dev_sum += ((ec - eth_ema.get(h, ec)) / max(1e-9, eth_ema.get(h, ec))) * 0.30
            w_sum += 0.30
        if h in sol_idx:
            si = sol_idx[h]
            sc = float(sol_candles[h]["c"])
            r1_sum += _return_over_steps(sol_hours, si, sol_candles, 1) * 0.20
            r6_sum += _return_over_steps(sol_hours, si, sol_candles, 6) * 0.20
            r24_sum += _return_over_steps(sol_hours, si, sol_candles, 24) * 0.20
            dev_sum += ((sc - sol_ema.get(h, sc)) / max(1e-9, sol_ema.get(h, sc))) * 0.20
            w_sum += 0.20

        r1 = r1_sum / w_sum
        r6 = r6_sum / w_sum
        r24 = r24_sum / w_sum
        ema_dev = dev_sum / w_sum

        z_mom = 0.32 * (r1 / 0.008) + 0.30 * (r6 / 0.020) + 0.23 * (r24 / 0.045) + 0.15 * (ema_dev / 0.022)
        mom_score = _tanh_score(z_mom, amplitude=45.0)

        # Hourly aggressive taker buy pressure + L/S momentum + funding
        buy_ratio = taker_buy_ratio.get(h)
        z_taker = ((buy_ratio - 0.50) / 0.06) if buy_ratio is not None else (r1 / 0.008)
        ls_val = ls_ratio_map.get(h)
        z_ls = 0.0
        if ls_val is not None and h in ls_idx:
            li = ls_idx[h]
            prev_ls = ls_ratio_map.get(ls_hours_sorted[max(0, li - 6)], ls_val)
            if prev_ls > 0:
                z_ls = ((ls_val - prev_ls) / prev_ls) / 0.08
        flow_score = _tanh_score(0.72 * z_taker + 0.16 * z_ls + 0.12 * z_fund, amplitude=44.0)

        # Realized hourly candle direction & volatility stress
        bar_range = (float(b_bar["h"]) - float(b_bar["l"])) / max(1e-9, b_c)
        bar_body = (b_c - float(b_bar["o"])) / max(1e-9, float(b_bar["o"]))
        vol_z = (bar_body / max(0.004, bar_range)) * 0.8 - max(0.0, bar_range - 0.015) * 30.0 * (1.0 if bar_body < 0 else 0.0)
        vol_score = _tanh_score(vol_z, amplitude=40.0)

        anchor = _lookup_daily_anchor(crypto_fng_daily, h, crypto_fng_latest)
        if anchor is not None:
            score = 0.42 * mom_score + 0.23 * flow_score + 0.10 * vol_score + 0.25 * float(anchor)
        else:
            score = 0.54 * mom_score + 0.32 * flow_score + 0.14 * vol_score

        crypto_by_hour[h] = {
            "score": round(_clamp(score, 2.0, 98.0), 1),
            "btc_px": round(b_c, 1),
            "btc_ret_1h_pct": round(b_r1 * 100.0, 2),
            "taker_buy_pct": round(buy_ratio * 100.0, 1) if buy_ratio is not None else None,
            "ls_ratio": round(ls_val, 2) if ls_val is not None else None,
            "anchor": round(float(anchor), 1) if anchor is not None else None,
        }

    # 2. Compute Traditional Market hourly sentiment for every ES/NQ trading hour
    trad_by_hour: Dict[int, dict] = {}
    trad_union_hours = sorted(set(es_hours) | set(nq_hours))
    last_vix_val: Optional[float] = None
    last_vix_prev: Optional[float] = None

    for h in trad_union_hours:
        # Resolve ES & NQ returns
        r1_sum, r6_sum, r24_sum, dev_sum, w_sum = 0.0, 0.0, 0.0, 0.0, 0.0
        es_px: Optional[float] = None
        es_r1_val: Optional[float] = None
        nq_r1_val: Optional[float] = None
        es_r6_val: Optional[float] = None

        if h in es_idx:
            ei = es_idx[h]
            ec = float(es_candles[h]["c"])
            es_px = ec
            er1 = _return_over_steps(es_hours, ei, es_candles, 1)
            er6 = _return_over_steps(es_hours, ei, es_candles, 6)
            er24 = _return_over_steps(es_hours, ei, es_candles, 24)
            edev = (ec - es_ema.get(h, ec)) / max(1e-9, es_ema.get(h, ec))
            es_r1_val = er1
            es_r6_val = er6
            r1_sum += er1 * 0.55
            r6_sum += er6 * 0.55
            r24_sum += er24 * 0.55
            dev_sum += edev * 0.55
            w_sum += 0.55

        if h in nq_idx:
            ni = nq_idx[h]
            nc = float(nq_candles[h]["c"])
            nr1 = _return_over_steps(nq_hours, ni, nq_candles, 1)
            nr6 = _return_over_steps(nq_hours, ni, nq_candles, 6)
            nr24 = _return_over_steps(nq_hours, ni, nq_candles, 24)
            ndev = (nc - nq_ema.get(h, nc)) / max(1e-9, nq_ema.get(h, nc))
            nq_r1_val = nr1
            r1_sum += nr1 * 0.45
            r6_sum += nr6 * 0.45
            r24_sum += nr24 * 0.45
            dev_sum += ndev * 0.45
            w_sum += 0.45

        if w_sum <= 0:
            continue
        r1 = r1_sum / w_sum
        r6 = r6_sum / w_sum
        r24 = r24_sum / w_sum
        ema_dev = dev_sum / w_sum

        z_mom = 0.32 * (r1 / 0.0035) + 0.30 * (r6 / 0.0090) + 0.23 * (r24 / 0.0180) + 0.15 * (ema_dev / 0.010)
        mom_score = _tanh_score(z_mom, amplitude=45.0)

        # VIX fear gauge (inverted: higher VIX => lower sentiment)
        vix_val: Optional[float] = None
        vix_ret_1h = 0.0
        if h in vix_idx:
            vi = vix_idx[h]
            vix_val = float(vix_candles[h]["c"])
            vix_ret_1h = _return_over_steps(vix_hours, vi, vix_candles, 1)
            last_vix_prev = last_vix_val
            last_vix_val = vix_val
        elif last_vix_val is not None:
            vix_val = last_vix_val

        if vix_val is not None and vix_val > 0:
            vix_base = 50.0 + 45.0 * math.tanh((19.5 - vix_val) / 9.5)
            vix_score = _clamp(vix_base - 14.0 * math.tanh(vix_ret_1h / 0.04), 2.0, 98.0)
        else:
            vix_score = mom_score

        # Equities vs Gold safe-haven spread
        if h in gc_idx and es_r6_val is not None:
            gi = gc_idx[h]
            gc_r6 = _return_over_steps(gc_hours, gi, gc_candles, 6)
            haven_score = _tanh_score((es_r6_val - gc_r6) / 0.012, amplitude=42.0)
        else:
            haven_score = mom_score

        anchor = _lookup_daily_anchor(cnn_fng_daily, h, cnn_fng_latest)
        if anchor is not None:
            score = 0.40 * mom_score + 0.25 * vix_score + 0.10 * haven_score + 0.25 * float(anchor)
        else:
            score = 0.54 * mom_score + 0.34 * vix_score + 0.12 * haven_score

        trad_by_hour[h] = {
            "score": round(_clamp(score, 2.0, 98.0), 1),
            "es_px": round(es_px, 2) if es_px is not None else None,
            "es_ret_1h_pct": round(es_r1_val * 100.0, 2) if es_r1_val is not None else None,
            "nq_ret_1h_pct": round(nq_r1_val * 100.0, 2) if nq_r1_val is not None else None,
            "vix": round(vix_val, 2) if vix_val is not None else None,
            "anchor": round(float(anchor), 1) if anchor is not None else None,
        }

    # 3. Build aligned 72H chronological timeline & latest 24H overlapping active trading timeline
    all_crypto_hours = sorted(crypto_by_hour.keys())
    all_trad_hours = sorted(trad_by_hour.keys())
    latest_trad_hour = all_trad_hours[-1] if all_trad_hours else 0
    trad_market_open = bool(latest_trad_hour > 0 and (now_sec - latest_trad_hour) <= 4 * 3600)

    def _build_point(h: int, c_node: dict, t_node: Optional[dict], is_trad_live: bool) -> dict:
        c_sc = float(c_node["score"])
        t_sc = float(t_node["score"]) if t_node else float(cnn_fng_latest if cnn_fng_latest is not None else 50.0)
        spread = round(c_sc - t_sc, 1)
        return {
            "ts_ms": h * 1000,
            "crypto_score": round(c_sc, 1),
            "trad_score": round(t_sc, 1),
            "spread": spread,
            "trad_live": is_trad_live,
            "btc_px": c_node.get("btc_px"),
            "btc_ret_1h_pct": c_node.get("btc_ret_1h_pct"),
            "taker_buy_pct": c_node.get("taker_buy_pct"),
            "ls_ratio": c_node.get("ls_ratio"),
            "es_px": t_node.get("es_px") if t_node else None,
            "es_ret_1h_pct": t_node.get("es_ret_1h_pct") if t_node else None,
            "nq_ret_1h_pct": t_node.get("nq_ret_1h_pct") if t_node else None,
            "vix": t_node.get("vix") if t_node else None,
        }

    # Timeline 1: Last 72 hours (chronological, carrying forward TradFi last active bar during CME/weekend closures)
    timeline_hours = all_crypto_hours[-72:] if all_crypto_hours else all_trad_hours[-72:]
    series_72h: List[dict] = []
    last_known_trad: Optional[dict] = None
    if timeline_hours and all_trad_hours:
        first_h = timeline_hours[0]
        prior_trad = [th for th in all_trad_hours if th <= first_h]
        if prior_trad:
            last_known_trad = trad_by_hour[prior_trad[-1]]
        else:
            last_known_trad = trad_by_hour[all_trad_hours[0]]

    for h in timeline_hours:
        c_node = crypto_by_hour.get(h)
        if not c_node:
            continue
        if h in trad_by_hour:
            last_known_trad = trad_by_hour[h]
            series_72h.append(_build_point(h, c_node, last_known_trad, True))
        else:
            series_72h.append(_build_point(h, c_node, last_known_trad, False))

    # Timeline 2: Latest 24 overlapping active trading hours (both Crypto & TradFi live!)
    overlap_hours = sorted(set(crypto_by_hour.keys()) & set(trad_by_hour.keys()))[-24:]
    series_overlap_24h: List[dict] = [
        _build_point(h, crypto_by_hour[h], trad_by_hour[h], True) for h in overlap_hours
    ]

    # Compute summary KPIs
    latest_pt = series_72h[-1] if series_72h else (series_overlap_24h[-1] if series_overlap_24h else None)
    prev_pt = series_72h[-2] if len(series_72h) >= 2 else latest_pt
    latest_trad_pt = (
        series_overlap_24h[-1]
        if series_overlap_24h
        else (latest_pt if latest_pt else None)
    )
    prev_trad_pt = (
        series_overlap_24h[-2]
        if len(series_overlap_24h) >= 2
        else latest_trad_pt
    )

    crypto_cur = float(latest_pt["crypto_score"]) if latest_pt else 50.0
    crypto_prev = float(prev_pt["crypto_score"]) if prev_pt else crypto_cur
    trad_cur = float(latest_pt["trad_score"]) if latest_pt else 50.0
    trad_prev = float(prev_trad_pt["trad_score"]) if prev_trad_pt else trad_cur
    spread_cur = round(crypto_cur - trad_cur, 1)

    corr_src = series_overlap_24h if len(series_overlap_24h) >= 4 else series_72h[-24:]
    corr_24h = _pearson_corr(
        [float(p["crypto_score"]) for p in corr_src],
        [float(p["trad_score"]) for p in corr_src],
    )

    crypto_lbl = sentiment_regime_label(crypto_cur)
    trad_lbl = sentiment_regime_label(trad_cur)
    if abs(spread_cur) <= 6.0:
        if crypto_cur < 35 and trad_cur < 35:
            regime_summary = f"双市场同步{crypto_lbl}（剪刀差 {spread_cur:+.1f}），避险情绪主导，慎防顺势假突破与插针"
        elif crypto_cur > 65 and trad_cur > 65:
            regime_summary = f"双市场同步{crypto_lbl}（剪刀差 {spread_cur:+.1f}），风险偏好共振，顺势突破延续性较好"
        else:
            regime_summary = f"加密与传统市场情绪基本同步（剪刀差 {spread_cur:+.1f}），适合震荡网格双向高抛低吸"
    elif spread_cur > 6.0:
        regime_summary = f"加密情绪强于传统市场 {spread_cur:+.1f} 分（加密{crypto_lbl} vs 传统{trad_lbl}），加密资金风险偏好相对领先"
    else:
        regime_summary = f"加密情绪弱于传统市场 {spread_cur:+.1f} 分（加密{crypto_lbl} vs 传统{trad_lbl}），加密端偏恐慌或存在超跌修复机会"

    return {
        "ok": bool(series_72h or series_overlap_24h),
        "is_live_data": bool(series_72h and (len(es_candles) > 0 or len(nq_candles) > 0)),
        "updated_at_ms": now_sec * 1000,
        "trad_market_open": trad_market_open,
        "trad_last_bar_ms": latest_trad_hour * 1000 if latest_trad_hour > 0 else 0,
        "summary": {
            "crypto_score": round(crypto_cur, 1),
            "crypto_label": crypto_lbl,
            "crypto_delta_1h": round(crypto_cur - crypto_prev, 1),
            "crypto_fng_anchor": round(float(crypto_fng_latest), 1) if crypto_fng_latest is not None else None,
            "btc_price": latest_pt.get("btc_px") if latest_pt else None,
            "btc_funding_pct": round(fund_val * 100.0, 4) if funding_rate is not None else None,
            "btc_ls_ratio": latest_pt.get("ls_ratio") if latest_pt else None,
            "btc_taker_buy_pct": latest_pt.get("taker_buy_pct") if latest_pt else None,
            "trad_score": round(trad_cur, 1),
            "trad_label": trad_lbl,
            "trad_delta_1h": round(trad_cur - trad_prev, 1),
            "trad_fng_anchor": round(float(cnn_fng_latest), 1) if cnn_fng_latest is not None else None,
            "es_price": latest_trad_pt.get("es_px") if latest_trad_pt else None,
            "vix_current": latest_trad_pt.get("vix") if latest_trad_pt else None,
            "spread": spread_cur,
            "correlation_24h": corr_24h,
            "regime_summary": regime_summary,
        },
        "series_72h": series_72h,
        "series_overlap_24h": series_overlap_24h,
        "data_sources": data_sources or [],
    }


class MarketSentimentService:
    """Background-refreshed, in-memory cached hourly Crypto vs Traditional market sentiment service."""

    def __init__(self, refresh_interval_sec: float = 300.0):
        self.refresh_interval_sec = max(60.0, float(refresh_interval_sec))
        self._cache: Dict[str, Any] = {
            "ok": False,
            "is_live_data": False,
            "updated_at_ms": 0,
            "trad_market_open": False,
            "trad_last_bar_ms": 0,
            "summary": {
                "crypto_score": 50.0,
                "crypto_label": "加载中",
                "crypto_delta_1h": 0.0,
                "crypto_fng_anchor": None,
                "btc_price": None,
                "btc_funding_pct": None,
                "btc_ls_ratio": None,
                "btc_taker_buy_pct": None,
                "trad_score": 50.0,
                "trad_label": "加载中",
                "trad_delta_1h": 0.0,
                "trad_fng_anchor": None,
                "es_price": None,
                "vix_current": None,
                "spread": 0.0,
                "correlation_24h": None,
                "regime_summary": "正在拉取加密市场与传统市场每小时情绪数据...",
            },
            "series_72h": [],
            "series_overlap_24h": [],
            "data_sources": [],
        }
        self._last_fetch_mono: float = 0.0
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _refresh_loop(self):
        # Small initial delay so FastAPI startup stays instant, then fetch live sentiment immediately
        try:
            await asyncio.sleep(0.5)
            while self._running:
                try:
                    await self.refresh(force=True)
                except Exception:
                    pass
                await asyncio.sleep(self.refresh_interval_sec)
        except asyncio.CancelledError:
            return

    def get_cached(self) -> Dict[str, Any]:
        return self._cache

    async def get_sentiment(self, force_refresh: bool = False) -> Dict[str, Any]:
        now_m = time.monotonic()
        need_fetch = (not self._cache.get("ok")) or (
            force_refresh and (now_m - self._last_fetch_mono >= 15.0)
        ) or (now_m - self._last_fetch_mono >= self.refresh_interval_sec)
        if need_fetch:
            await self.refresh(force=force_refresh)
        return self._cache

    async def refresh(self, force: bool = False) -> Dict[str, Any]:
        async with self._lock:
            now_m = time.monotonic()
            if (
                not force
                and self._cache.get("ok")
                and (now_m - self._last_fetch_mono < self.refresh_interval_sec)
            ):
                return self._cache

            proxy = get_system_proxy()
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
            }
            sources_ok: List[str] = []

            async def _fetch_json(client: httpx.AsyncClient, url: str) -> Any:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.json()
                except Exception:
                    return None
                return None

            try:
                client_kwargs: Dict[str, Any] = {
                    "timeout": 9.0,
                    "headers": headers,
                    "follow_redirects": True,
                }
                if proxy:
                    client_kwargs["proxy"] = proxy
                async with httpx.AsyncClient(**client_kwargs) as client:
                    results = await asyncio.gather(
                        _fetch_json(client, "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=100"),
                        _fetch_json(client, "https://www.okx.com/api/v5/market/candles?instId=ETH-USDT-SWAP&bar=1H&limit=100"),
                        _fetch_json(client, "https://www.okx.com/api/v5/market/candles?instId=SOL-USDT-SWAP&bar=1H&limit=100"),
                        _fetch_json(client, "https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy=BTC&instType=CONTRACTS&period=1H"),
                        _fetch_json(client, "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H"),
                        _fetch_json(client, "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"),
                        _fetch_json(client, "https://api.alternative.me/fng/?limit=7"),
                        _fetch_json(client, "https://query1.finance.yahoo.com/v8/finance/chart/ES=F?interval=1h&range=5d"),
                        _fetch_json(client, "https://query1.finance.yahoo.com/v8/finance/chart/NQ=F?interval=1h&range=5d"),
                        _fetch_json(client, "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1h&range=5d"),
                        _fetch_json(client, "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1h&range=5d"),
                        _fetch_json(client, "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"),
                    )
            except Exception:
                return self._cache

            (
                okx_btc_raw,
                okx_eth_raw,
                okx_sol_raw,
                okx_taker_raw,
                okx_ls_raw,
                okx_fund_raw,
                alt_fng_raw,
                yf_es_raw,
                yf_nq_raw,
                yf_vix_raw,
                yf_gc_raw,
                cnn_fng_raw,
            ) = results

            btc_candles = _parse_okx_candles(okx_btc_raw)
            eth_candles = _parse_okx_candles(okx_eth_raw)
            sol_candles = _parse_okx_candles(okx_sol_raw)
            taker_buy_ratio = _parse_okx_taker_vol(okx_taker_raw)
            ls_ratio_map = _parse_okx_ls_ratio(okx_ls_raw)

            funding_rate: Optional[float] = None
            if isinstance(okx_fund_raw, dict) and str(okx_fund_raw.get("code")) == "0":
                fdata = okx_fund_raw.get("data") or []
                if fdata and isinstance(fdata[0], dict):
                    try:
                        funding_rate = float(fdata[0].get("fundingRate"))
                    except Exception:
                        funding_rate = None

            crypto_fng_daily: Dict[str, float] = {}
            crypto_fng_latest: Optional[float] = None
            if isinstance(alt_fng_raw, dict):
                for idx_f, item in enumerate(alt_fng_raw.get("data") or []):
                    try:
                        val = float(item.get("value"))
                        ts_s = int(item.get("timestamp"))
                        if idx_f == 0:
                            crypto_fng_latest = val
                        crypto_fng_daily[_date_key_utc(ts_s)] = val
                    except Exception:
                        continue

            es_candles = _parse_yahoo_candles(yf_es_raw)
            nq_candles = _parse_yahoo_candles(yf_nq_raw)
            vix_candles = _parse_yahoo_candles(yf_vix_raw)
            gc_candles = _parse_yahoo_candles(yf_gc_raw)

            cnn_fng_daily: Dict[str, float] = {}
            cnn_fng_latest: Optional[float] = None
            if isinstance(cnn_fng_raw, dict):
                fg_now = cnn_fng_raw.get("fear_and_greed") or {}
                if fg_now.get("score") is not None:
                    try:
                        cnn_fng_latest = float(fg_now["score"])
                    except Exception:
                        cnn_fng_latest = None
                hist_list = (cnn_fng_raw.get("fear_and_greed_historical") or {}).get("data") or []
                for pt in hist_list[-14:]:
                    try:
                        ts_s = int(float(pt["x"]) // 1000)
                        cnn_fng_daily[_date_key_utc(ts_s)] = float(pt["y"])
                    except Exception:
                        continue

            if btc_candles:
                sources_ok.append("OKX BTC/ETH/SOL 1H")
            if taker_buy_ratio:
                sources_ok.append("OKX 1H 主动买卖量")
            if crypto_fng_latest is not None:
                sources_ok.append("Alternative.me 加密恐贪")
            if es_candles or nq_candles:
                sources_ok.append("CME 标普/纳指期货 1H")
            if vix_candles:
                sources_ok.append("CBOE VIX 1H")
            if cnn_fng_latest is not None:
                sources_ok.append("CNN 传统恐贪指数")

            computed = compute_hourly_sentiment_comparison(
                btc_candles=btc_candles,
                eth_candles=eth_candles,
                sol_candles=sol_candles,
                taker_buy_ratio=taker_buy_ratio,
                ls_ratio_map=ls_ratio_map,
                funding_rate=funding_rate,
                crypto_fng_daily=crypto_fng_daily,
                crypto_fng_latest=crypto_fng_latest,
                es_candles=es_candles,
                nq_candles=nq_candles,
                vix_candles=vix_candles,
                gc_candles=gc_candles,
                cnn_fng_daily=cnn_fng_daily,
                cnn_fng_latest=cnn_fng_latest,
                data_sources=sources_ok,
            )
            if computed.get("ok"):
                self._cache = computed
                self._last_fetch_mono = time.monotonic()
            return self._cache


market_sentiment_service = MarketSentimentService(refresh_interval_sec=300.0)
