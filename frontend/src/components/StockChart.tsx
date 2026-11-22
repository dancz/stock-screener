import React, { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  LineStyle,
  SeriesAttachedParameter,
  SeriesMarker,
  SeriesPrimitivePaneViewZOrder,
  SeriesType,
  Time,
  UTCTimestamp,
  createChart,
} from "lightweight-charts";
import type { CandleBar, ChartOverlay, OverlayLayer } from "../types/screener";
import { useScreenerStore } from "../store/useScreenerStore";

function hexToRgba(hex: string, alpha: number): string {
  const clean = hex.replace("#", "");
  if (clean.length !== 6) return `rgba(56, 189, 248, ${alpha})`;
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

class ZonesAndPlanPrimitive implements ISeriesPrimitive<Time> {
  private _series: ISeriesApi<SeriesType, Time> | null = null;
  private _requestUpdate?: () => void;
  private _overlays: ChartOverlay[] = [];

  private _paneView: ISeriesPrimitivePaneView = {
    zOrder: (): SeriesPrimitivePaneViewZOrder => "bottom",
    renderer: (): ISeriesPrimitivePaneRenderer => ({
      draw: (target) => {
        if (!this._series || this._overlays.length === 0) return;
        const series = this._series;
        target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
          const width = mediaSize.width;
          ctx.save();
          ctx.font = "600 10px Inter, system-ui, sans-serif";

          for (const ov of this._overlays) {
            if (ov.kind === "horizontal_zone") {
              const pLow = Number(ov.data.priceLow);
              const pHigh = Number(ov.data.priceHigh);
              const pMid = Number(ov.data.mid ?? (pLow + pHigh) / 2);
              const yHigh = series.priceToCoordinate(pHigh);
              const yLow = series.priceToCoordinate(pLow);
              const yMid = series.priceToCoordinate(pMid);
              if (yHigh === null || yLow === null) continue;

              const top = Math.min(yHigh, yLow);
              const h = Math.max(2, Math.abs(yLow - yHigh));
              const role = String(ov.data.role || "");

              if (role === "poc") {
                if (yMid !== null) {
                  ctx.strokeStyle = hexToRgba(ov.color || "#eab308", 0.85);
                  ctx.lineWidth = 1.5;
                  ctx.setLineDash([6, 4]);
                  ctx.beginPath();
                  ctx.moveTo(0, yMid);
                  ctx.lineTo(width, yMid);
                  ctx.stroke();
                  ctx.setLineDash([]);
                  ctx.fillStyle = "#fde047";
                  ctx.fillText(ov.label, 8, yMid - 4);
                }
              } else {
                ctx.fillStyle = hexToRgba(ov.color, 0.12);
                ctx.fillRect(0, top, width, h);
                ctx.strokeStyle = hexToRgba(ov.color, 0.45);
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(0, top);
                ctx.lineTo(width, top);
                ctx.moveTo(0, top + h);
                ctx.lineTo(width, top + h);
                ctx.stroke();
                ctx.setLineDash([]);

                ctx.fillStyle = hexToRgba(ov.color, 0.9);
                ctx.fillText(ov.label, 8, Math.max(12, top + h - 3));
              }
            } else if (ov.kind === "price_box") {
              const entryLow = Number(ov.data.entryLow);
              const entryHigh = Number(ov.data.entryHigh);
              const stopLoss = Number(ov.data.stopLoss);
              const tp1 = Number(ov.data.tp1);
              const tp2 = Number(ov.data.tp2);
              const midEntry = (entryLow + entryHigh) / 2;

              const yEntryLow = series.priceToCoordinate(entryLow);
              const yEntryHigh = series.priceToCoordinate(entryHigh);
              const yMid = series.priceToCoordinate(midEntry);
              const ySl = series.priceToCoordinate(stopLoss);
              const yTp1 = series.priceToCoordinate(tp1);
              const yTp2 = series.priceToCoordinate(tp2);

              // Right-half box so historical bars remain unobstructed
              const xStart = width * 0.62;
              const boxW = width - xStart;

              if (yMid !== null && yTp2 !== null) {
                const top = Math.min(yMid, yTp2);
                const h = Math.abs(yTp2 - yMid);
                ctx.fillStyle = "rgba(16, 185, 129, 0.11)";
                ctx.fillRect(xStart, top, boxW, h);
              }
              if (yMid !== null && ySl !== null) {
                const top = Math.min(yMid, ySl);
                const h = Math.abs(ySl - yMid);
                ctx.fillStyle = "rgba(244, 63, 94, 0.12)";
                ctx.fillRect(xStart, top, boxW, h);
              }
              if (yEntryLow !== null && yEntryHigh !== null) {
                const top = Math.min(yEntryLow, yEntryHigh);
                const h = Math.max(2, Math.abs(yEntryLow - yEntryHigh));
                ctx.fillStyle = "rgba(56, 189, 248, 0.22)";
                ctx.fillRect(xStart, top, boxW, h);
              }

              const drawPriceLine = (y: number | null, color: string, label: string) => {
                if (y === null) return;
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.25;
                ctx.setLineDash([5, 3]);
                ctx.beginPath();
                ctx.moveTo(xStart, y);
                ctx.lineTo(width, y);
                ctx.stroke();
                ctx.setLineDash([]);
                ctx.fillStyle = color;
                ctx.fillText(label, xStart + 6, y - 4);
              };

              drawPriceLine(yTp2, "#34d399", `TP2 $${tp2.toFixed(2)}`);
              drawPriceLine(yTp1, "#10b981", `TP1 $${tp1.toFixed(2)}`);
              drawPriceLine(ySl, "#fb7185", `SL $${stopLoss.toFixed(2)}`);
            }
          }

          ctx.restore();
        });
      },
    }),
  };

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = undefined;
  }

  setOverlays(overlays: ChartOverlay[]): void {
    this._overlays = overlays;
    this._requestUpdate?.();
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return [this._paneView];
  }
}

function snapTime(ts: number, candles: CandleBar[]): UTCTimestamp {
  if (candles.length === 0) return ts as UTCTimestamp;
  let best = candles[0].time;
  let bestDiff = Math.abs(best - ts);
  for (const c of candles) {
    const diff = Math.abs(c.time - ts);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = c.time;
    }
  }
  return best as UTCTimestamp;
}

function computeEmaSeries(candles: CandleBar[], period: number): Array<{ time: Time; value: number }> {
  if (candles.length === 0) return [];
  const k = 2 / (period + 1);
  let ema = candles[0].close;
  return candles.map((c, idx) => {
    ema = idx === 0 ? c.close : c.close * k + ema * (1 - k);
    return { time: c.time as UTCTimestamp, value: Number(ema.toFixed(2)) };
  });
}

const LAYER_META: Array<{ key: OverlayLayer; label: string; colorClass: string }> = [
  { key: "channels", label: "Channels", colorClass: "text-emerald-300" },
  { key: "sr_zones", label: "S/R Zones", colorClass: "text-sky-300" },
  { key: "price_action", label: "Price Action", colorClass: "text-emerald-300" },
  { key: "trade_plan", label: "Trade Plan", colorClass: "text-sky-300" },
  { key: "emas", label: "EMAs", colorClass: "text-amber-300" },
  { key: "volume_profile", label: "Vol POC", colorClass: "text-yellow-300" },
  { key: "volume", label: "Volume", colorClass: "text-slate-300" },
];

export const StockChart: React.FC = () => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const extraSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const primitiveRef = useRef<ZonesAndPlanPrimitive | null>(null);

  const detail = useScreenerStore((s) => s.selectedDetail);
  const candles = useScreenerStore((s) => s.candles);
  const timeframe = useScreenerStore((s) => s.timeframe);
  const setTimeframe = useScreenerStore((s) => s.setTimeframe);
  const layers = useScreenerStore((s) => s.layers);
  const toggleLayer = useScreenerStore((s) => s.toggleLayer);
  const loadingCandles = useScreenerStore((s) => s.loadingCandles);

  // Create chart once
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#0d131f" },
        textColor: "#94a3b8",
        fontFamily: "Inter, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(37, 48, 71, 0.4)" },
        horzLines: { color: "rgba(37, 48, 71, 0.4)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#253047" },
      timeScale: { borderColor: "#253047", timeVisible: true, secondsVisible: false },
      width: el.clientWidth,
      height: 430,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderUpColor: "#10b981",
      borderDownColor: "#f43f5e",
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    const primitive = new ZonesAndPlanPrimitive();
    candleSeries.attachPrimitive(primitive);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    primitiveRef.current = primitive;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
        }
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      primitiveRef.current = null;
      extraSeriesRef.current = [];
    };
  }, []);

  // Update candles & generic plugin overlays
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const primitive = primitiveRef.current;
    if (!chart || !candleSeries || !volumeSeries || !primitive) return;

    // Clear previously drawn overlay LineSeries
    for (const s of extraSeriesRef.current) {
      try {
        chart.removeSeries(s);
      } catch {
        // ignore
      }
    }
    extraSeriesRef.current = [];

    if (!candles || candles.length === 0) {
      candleSeries.setData([]);
      volumeSeries.setData([]);
      candleSeries.setMarkers([]);
      primitive.setOverlays([]);
      return;
    }

    candleSeries.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    if (layers.volume) {
      volumeSeries.setData(
        candles.map((c) => ({
          time: c.time as UTCTimestamp,
          value: c.volume,
          color: c.close >= c.open ? "rgba(16, 185, 129, 0.35)" : "rgba(244, 63, 94, 0.35)",
        }))
      );
    } else {
      volumeSeries.setData([]);
    }

    const activeOverlays = (detail?.overlays || []).filter((ov) => layers[ov.layer]);

    // 1. Primitive overlays (horizontal_zone & price_box)
    primitive.setOverlays(
      activeOverlays.filter((ov) => ov.kind === "horizontal_zone" || ov.kind === "price_box")
    );

    // 2. Price action markers
    const markers: SeriesMarker<Time>[] = [];
    for (const ov of activeOverlays) {
      if (ov.kind === "marker") {
        const rawTime = Number(ov.data.time);
        markers.push({
          time: snapTime(rawTime, candles),
          position: (ov.data.position as "aboveBar" | "belowBar") || "belowBar",
          color: ov.color || "#10b981",
          shape: (ov.data.shape as "arrowUp" | "arrowDown" | "circle") || "arrowUp",
          text: String(ov.data.text || ov.label),
        });
      }
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    candleSeries.setMarkers(markers);

    // 3. Trend channels & Line series
    const firstT = candles[0].time;
    const lastT = candles[candles.length - 1].time;

    for (const ov of activeOverlays) {
      if (ov.kind === "channel") {
        const sTs = Number(ov.data.startTs);
        const eTs = Number(ov.data.endTs);
        const span = Math.max(eTs - sTs, 1);

        const clampPoint = (ts: number, startVal: number, endVal: number): { time: Time; value: number } => {
          const clampedTs = Math.max(firstT, Math.min(lastT, ts));
          const frac = (clampedTs - sTs) / span;
          const val = startVal + (endVal - startVal) * frac;
          return { time: snapTime(clampedTs, candles), value: Number(val.toFixed(2)) };
        };

        const addRail = (
          startVal: number,
          endVal: number,
          lineStyle: LineStyle,
          lineWidth: 1 | 2,
          color: string
        ) => {
          const p1 = clampPoint(sTs, startVal, endVal);
          const p2 = clampPoint(eTs, startVal, endVal);
          if (Number(p1.time) >= Number(p2.time)) return;
          const ls = chart.addLineSeries({
            color,
            lineWidth,
            lineStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          ls.setData([p1, p2]);
          extraSeriesRef.current.push(ls);
        };

        const cColor = ov.color || "#10b981";
        addRail(Number(ov.data.upperStart), Number(ov.data.upperEnd), LineStyle.Solid, 2, cColor);
        addRail(Number(ov.data.midStart), Number(ov.data.midEnd), LineStyle.Dashed, 1, hexToRgba(cColor, 0.7));
        addRail(Number(ov.data.lowerStart), Number(ov.data.lowerEnd), LineStyle.Solid, 2, cColor);
      } else if (ov.kind === "line_series" && timeframe === "1d") {
        const pts = (ov.data.points as Array<{ time: number; value: number }> | undefined) || [];
        const filtered = pts
          .filter((p) => p.time >= firstT && p.time <= lastT)
          .map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
        if (filtered.length > 1) {
          const ls = chart.addLineSeries({
            color: ov.color || "#38bdf8",
            lineWidth: (Number(ov.data.lineWidth) === 2 ? 2 : 1) as 1 | 2,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          ls.setData(filtered);
          extraSeriesRef.current.push(ls);
        }
      } else if (ov.kind === "trend_line") {
        const pts = (ov.data.points as Array<{ time: number; value: number }> | undefined) || [];
        if (pts.length >= 2) {
          const ls = chart.addLineSeries({
            color: ov.color || "#38bdf8",
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          ls.setData(pts.map((p) => ({ time: snapTime(p.time, candles), value: p.value })));
          extraSeriesRef.current.push(ls);
        }
      }
    }

    // On 1H timeframe, compute crisp local EMAs 20/50/200 when EMAs layer is enabled
    if (timeframe === "1h" && layers.emas) {
      for (const [period, color] of [
        [20, "#38bdf8"],
        [50, "#f59e0b"],
        [200, "#a855f7"],
      ] as const) {
        const ls = chart.addLineSeries({
          color,
          lineWidth: period === 200 ? 2 : 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        ls.setData(computeEmaSeries(candles, period));
        extraSeriesRef.current.push(ls);
      }
    }

    chart.timeScale().fitContent();
  }, [candles, detail, layers, timeframe]);

  return (
    <section className="rounded-xl border border-border bg-panel overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          {(["1d", "1h"] as const).map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              className={`rounded-md px-2.5 py-1 text-xs font-bold uppercase transition-colors ${
                timeframe === tf
                  ? "bg-sky-600 text-white"
                  : "bg-card text-slate-400 hover:text-white"
              }`}
            >
              {tf}
            </button>
          ))}
          {loadingCandles && <span className="ml-2 text-[11px] text-slate-400">Loading candles...</span>}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {LAYER_META.map((item) => {
            const active = layers[item.key];
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => toggleLayer(item.key)}
                className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                  active
                    ? `border-sky-500/40 bg-sky-500/10 ${item.colorClass}`
                    : "border-border bg-canvas text-slate-500 hover:text-slate-300"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Chart Container */}
      <div ref={containerRef} className="w-full" style={{ height: 430 }} />
    </section>
  );
};
