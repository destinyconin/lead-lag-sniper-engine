import { useEffect, useRef, useState } from 'react';

/**
 * Tracks a chart container's rendered width via ResizeObserver so SVGs keep
 * readable typography, crisp aspect ratios, and accurate touch/mouse scrubbing
 * across mobile phones, tablets, and desktops.
 */
export function useContainerChartSize({
  defaultWidth = 840,
  mobileHeight = 205,
  tabletHeight = 225,
  desktopHeight = 245,
} = {}) {
  const containerRef = useRef(null);
  const [measuredWidth, setMeasuredWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    const updateWidth = () => {
      const next = Math.round(el.getBoundingClientRect().width);
      if (next > 0) setMeasuredWidth((prev) => (prev === next ? prev : next));
    };

    updateWidth();

    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const next = Math.round(entry.contentRect.width);
          if (next > 0) setMeasuredWidth((prev) => (prev === next ? prev : next));
        }
      });
      ro.observe(el);
      return () => ro.disconnect();
    }

    window.addEventListener('resize', updateWidth);
    return () => window.removeEventListener('resize', updateWidth);
  }, []);

  const width = measuredWidth > 0 ? Math.max(300, measuredWidth) : defaultWidth;
  const isCompact = width < 520;
  const height = isCompact ? mobileHeight : width < 800 ? tabletHeight : desktopHeight;

  return { containerRef, width, height, isCompact };
}

/**
 * Maps mouse or touch clientX coordinates inside an SVG to the closest data index.
 */
export function getChartPointIndex(clientX, svgElement, viewWidth, padLeft, plotW, pointCount) {
  if (!svgElement || !pointCount || plotW <= 0) return null;
  const rect = svgElement.getBoundingClientRect();
  if (!rect.width) return null;
  const relX = ((clientX - rect.left) / rect.width) * viewWidth;
  const ratio = Math.max(0, Math.min(1, (relX - padLeft) / plotW));
  return Math.round(ratio * (pointCount - 1));
}
