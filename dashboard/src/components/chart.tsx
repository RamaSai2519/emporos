'use client';
import { Component, createRef } from 'react';
import { createChart, AreaSeries, CandlestickSeries, ColorType, type IChartApi, type UTCTimestamp } from 'lightweight-charts';

export class TradingChart extends Component<{ points?: readonly { at: string; value: number }[]; candles?: readonly { time: number; open: number; high: number; low: number; close: number }[]; label: string }> {
  private readonly host = createRef<HTMLDivElement>();
  private chart: IChartApi | null = null;
  componentDidMount() { this.draw(); }
  componentDidUpdate() { this.draw(); }
  componentWillUnmount() { this.chart?.remove(); }
  private draw() {
    this.chart?.remove(); if (!this.host.current) return;
    this.chart = createChart(this.host.current, { autoSize: true, height: 280, layout: { background: { type: ColorType.Solid, color: '#141e2d' }, textColor: '#94a6bd', fontFamily: 'monospace', attributionLogo: true }, grid: { vertLines: { color: '#1c293b' }, horzLines: { color: '#243144' } }, rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: true }, localization: { timeFormatter: (time: number) => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(time * 1000) } });
    if (this.props.candles) this.chart.addSeries(CandlestickSeries, { upColor: '#63c9b0', downColor: '#f08c96', borderVisible: false, wickUpColor: '#63c9b0', wickDownColor: '#f08c96' }).setData(this.props.candles.map(point => ({ ...point, time: point.time as UTCTimestamp })));
    else this.chart.addSeries(AreaSeries, { lineColor: '#88b6ff', topColor: '#88b6ff30', bottomColor: '#88b6ff00', lineWidth: 2 }).setData((this.props.points ?? []).map(point => ({ time: Math.floor(Date.parse(point.at) / 1000) as UTCTimestamp, value: point.value })));
    this.chart.timeScale().fitContent();
  }
  render() { return <div className="chart" role="img" aria-label={this.props.label} ref={this.host} />; }
}
