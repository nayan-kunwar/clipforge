import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { Candidate, Moment } from "../types";

const SOURCE_COLORS: Record<string, string> = {
  audio: "#6d8bff",
  comments: "#f2b84b",
  transcript: "#31d0aa",
  user: "#f2637b",
  fallback: "#93a1bd",
};

interface TimelineProps {
  duration: number;
  peaks: number[][] | null | undefined;
  candidates: Candidate[];
  moments: Moment[];
  currentTime: number;
  onChange: (moments: Moment[]) => void;
  onSeek: (time: number) => void;
}

type DragState =
  | { mode: "trim-start" | "trim-end" | "move"; index: number; startTime: number; origStart: number; origEnd: number; moved: boolean }
  | null;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

export function Timeline({ duration, peaks, candidates, moments, currentTime, onChange, onSeek }: TimelineProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const dragRef = useRef<DragState>(null);
  const movedRef = useRef(false);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const observer = new ResizeObserver(() => {
      const rect = wrap.getBoundingClientRect();
      setSize({ width: Math.max(1, rect.width), height: Math.max(1, rect.height) });
    });
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const width = size.width;
    const height = size.height;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0a0f1b";
    ctx.fillRect(0, 0, width, height);

    const safeDuration = Math.max(1, duration);
    const timeToX = (value: number) => (value / safeDuration) * width;
    const center = height * 0.56;
    const amplitude = height * 0.34;

    // Waveform.
    if (peaks && peaks.length) {
      ctx.fillStyle = "#52648c";
      const columns = Math.min(Math.floor(width), peaks.length);
      for (let x = 0; x < columns; x += 1) {
        const start = Math.floor((x / columns) * peaks.length);
        const end = Math.max(start + 1, Math.floor(((x + 1) / columns) * peaks.length));
        let low = 0;
        let high = 0;
        for (let i = start; i < end; i += 1) {
          const pair = peaks[i];
          if (!pair) continue;
          low = Math.min(low, pair[0]);
          high = Math.max(high, pair[1]);
        }
        const y1 = center - (high / 127) * amplitude;
        const y2 = center - (low / 127) * amplitude;
        ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
      }
    } else {
      ctx.strokeStyle = "#243049";
      ctx.beginPath();
      ctx.moveTo(0, center);
      ctx.lineTo(width, center);
      ctx.stroke();
    }

    // Candidate markers.
    for (const candidate of candidates) {
      const x = timeToX(candidate.peak ?? candidate.start);
      const color = SOURCE_COLORS[candidate.source] || SOURCE_COLORS.fallback;
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.75;
      ctx.beginPath();
      ctx.moveTo(x, 4);
      ctx.lineTo(x, height - 26);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, 5, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }

    // Selected moments.
    moments.forEach((moment, index) => {
      const x1 = timeToX(moment.start);
      const x2 = timeToX(moment.end);
      const active = moment.enabled;
      ctx.fillStyle = active ? "rgba(109, 139, 255, 0.2)" : "rgba(147, 161, 189, 0.12)";
      ctx.fillRect(x1, 18, Math.max(1, x2 - x1), height - 42);
      ctx.strokeStyle = active ? "#6d8bff" : "#64718c";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x1 + 0.5, 18.5, Math.max(0, x2 - x1 - 1), height - 43);
      ctx.fillStyle = active ? "#c7d2ff" : "#93a1bd";
      ctx.font = "600 10px ui-sans-serif, system-ui";
      ctx.fillText(moment.title.slice(0, 26), x1 + 5, 31 + (index % 2) * 11);
      ctx.fillStyle = active ? "#a9baff" : "#64718c";
      ctx.fillRect(x1, height - 24, 3, 18);
      ctx.fillRect(x2 - 3, height - 24, 3, 18);
    });

    // Playhead.
    const px = timeToX(currentTime);
    ctx.strokeStyle = "#ffffff";
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, height);
    ctx.stroke();
    ctx.globalAlpha = 1;

    // Time ticks.
    ctx.fillStyle = "#64718c";
    ctx.font = "10px ui-monospace, monospace";
    const tickCount = width > 700 ? 8 : 4;
    for (let i = 0; i <= tickCount; i += 1) {
      const time = (safeDuration * i) / tickCount;
      const x = timeToX(time);
      ctx.fillText(formatTick(time), Math.min(width - 34, Math.max(2, x)), height - 6);
    }
  }, [candidates, currentTime, duration, moments, peaks, size.height, size.width]);

  useEffect(() => {
    draw();
  }, [draw]);

  const timeAt = (event: ReactPointerEvent<HTMLCanvasElement>): number => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = clamp(event.clientX - rect.left, 0, rect.width);
    return (x / Math.max(1, rect.width)) * Math.max(1, duration);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const time = timeAt(event);
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const edgePx = 8;
    for (let index = 0; index < moments.length; index += 1) {
      const moment = moments[index];
      const x1 = (moment.start / Math.max(1, duration)) * rect.width;
      const x2 = (moment.end / Math.max(1, duration)) * rect.width;
      if (Math.abs(x - x1) <= edgePx) {
        dragRef.current = { mode: "trim-start", index, startTime: time, origStart: moment.start, origEnd: moment.end, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
        return;
      }
      if (Math.abs(x - x2) <= edgePx) {
        dragRef.current = { mode: "trim-end", index, startTime: time, origStart: moment.start, origEnd: moment.end, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
        return;
      }
      if (x > x1 && x < x2) {
        dragRef.current = { mode: "move", index, startTime: time, origStart: moment.start, origEnd: moment.end, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
        return;
      }
    }
    dragRef.current = null;
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const time = timeAt(event);
    const moment = moments[drag.index];
    if (!moment) return;
    const delta = time - drag.startTime;
    if (Math.abs(delta) > 0.02) {
      drag.moved = true;
      movedRef.current = true;
    }
    const next = moments.map((item) => ({ ...item }));
    if (drag.mode === "move") {
      const length = drag.origEnd - drag.origStart;
      const start = clamp(drag.origStart + delta, 0, Math.max(0, duration - length));
      next[drag.index] = { ...moment, start, end: start + length };
    } else if (drag.mode === "trim-start") {
      const start = clamp(drag.origStart + delta, Math.max(0, drag.origEnd - 60), drag.origEnd - 1);
      next[drag.index] = { ...moment, start, end: drag.origEnd };
    } else if (drag.mode === "trim-end") {
      const end = clamp(drag.origEnd + delta, drag.origStart + 1, Math.min(duration, drag.origStart + 60));
      next[drag.index] = { ...moment, start: drag.origStart, end };
    }
    onChange(next);
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current) {
      event.currentTarget.releasePointerCapture(event.pointerId);
      dragRef.current = null;
    }
    if (!movedRef.current && !dragRef.current) {
      // A click without a drag seeks the preview.
      onSeek(timeAt(event));
    }
    movedRef.current = false;
  };

  return (
    <div className="timeline-wrap">
      <div className="timeline-canvas-wrap" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={() => {
            dragRef.current = null;
            movedRef.current = false;
          }}
          aria-label="Timeline. Drag clip edges to trim, drag the body to move, click to seek."
        />
      </div>
      <div className="timeline-legend">
        <span><i className="dot" style={{ background: SOURCE_COLORS.audio }} />Audio</span>
        <span><i className="dot" style={{ background: SOURCE_COLORS.comments }} />Comments</span>
        <span><i className="dot" style={{ background: SOURCE_COLORS.transcript }} />Transcript</span>
        <span><i className="dot" style={{ background: SOURCE_COLORS.user }} />User</span>
        <span className="faint">Drag clip edges to trim · 60s max</span>
      </div>
    </div>
  );
}

function formatTick(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}
