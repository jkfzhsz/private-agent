// FlowSpace 设计系统 §6 - 有机液体动效背景
// 对齐原始 Demo 实现: blob 径向渐变光晕 + 波浪边缘 + 5 色涟漪 + 描边拖尾 + 双谐波环境波
import { useEffect, useRef } from "react";

const BLOB_PALETTE: [number, number, number][] = [
  [168, 216, 255],
  [147, 197, 253],
  [196, 181, 253],
  [216, 180, 254],
  [249, 168, 212],
  [251, 191, 220],
];

const RIPPLE_COLORS: [number, number, number][] = [
  [168, 216, 255],
  [147, 197, 253],
  [196, 181, 253],
  [249, 168, 212],
  [216, 180, 254],
];

const BLOB_COUNT = 7;
const TRAIL_INTERVAL_MS = 180;
const TRAIL_MAX = 30;
const TRAIL_LIFETIME = 1800;
const RIPPLE_LIFETIME = 2800;

interface Blob {
  x: number;
  y: number;
  radius: number;
  vx: number;
  vy: number;
  amplitudeX: number;
  amplitudeY: number;
  freqX: number;
  freqY: number;
  phaseX: number;
  phaseY: number;
  waveAmp: number;
  color: [number, number, number];
  alpha: number;
  currentRadius: number;
}

interface Ripple {
  x: number;
  y: number;
  birth: number;
  maxRadius: number;
  color: [number, number, number];
}

interface Trail {
  x: number;
  y: number;
  birth: number;
  color: [number, number, number];
}

export default function LiquidBackground(): JSX.Element | null {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reducedRef = useRef(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      ctx = null; // jsdom 等环境无 canvas 实现
    }
    if (!ctx) return;

    const reduced = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : true;
    reducedRef.current = reduced;

    let raf = 0;
    let running = true;
    let w = 0;
    let h = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const random = (min: number, max: number): number =>
      min + Math.random() * (max - min);

    const resize = (): void => {
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const blobs: Blob[] = Array.from({ length: BLOB_COUNT }, () => ({
      x: random(0, window.innerWidth),
      y: random(0, window.innerHeight),
      radius: random(80, 260),
      vx: random(-0.35, 0.35),
      vy: random(-0.35, 0.35),
      amplitudeX: random(30, 90),
      amplitudeY: random(20, 70),
      freqX: 0.0008 + random(0, 0.0012),
      freqY: 0.0006 + random(0, 0.001),
      phaseX: random(0, Math.PI * 2),
      phaseY: random(0, Math.PI * 2),
      waveAmp: 8 + random(0, 14),
      color: BLOB_PALETTE[Math.floor(random(0, BLOB_PALETTE.length))],
      alpha: 0.35 + random(0, 0.25),
      currentRadius: 0,
    }));

    const ripples: Ripple[] = [];
    const trails: Trail[] = [];
    let lastTrailTs = 0;
    let mouseActive = false;
    let mouseX = -999;
    let mouseY = -999;

    // §6.3/原始 Demo: 波浪边缘变形 + 径向渐变光晕(液体质感核心)
    const drawWavyBlob = (blob: Blob, r: number, time: number): void => {
      const { color, alpha, waveAmp } = blob;
      const segments = 64;
      ctx.beginPath();
      for (let i = 0; i < segments; i++) {
        const angle = (i / segments) * Math.PI * 2;
        const distortion =
          r +
          Math.sin(angle * 5 + time * 0.002) * waveAmp +
          Math.cos(angle * 3 + time * 0.003 + blob.phaseX) * waveAmp * 0.5 +
          Math.sin(angle * 7 + time * 0.004) * waveAmp * 0.25;
        const px = blob.x + Math.cos(angle) * distortion;
        const py = blob.y + Math.sin(angle) * distortion;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      // 径向渐变: 中心高亮 → 边缘透明(原始 Demo §6.3)
      const gradient = ctx.createRadialGradient(
        blob.x, blob.y, r * 0.08,
        blob.x, blob.y, r + waveAmp,
      );
      const [cr, cg, cb] = color;
      gradient.addColorStop(0, `rgba(${cr},${cg},${cb},${alpha + 0.12})`);
      gradient.addColorStop(0.35, `rgba(${cr},${cg},${cb},${alpha})`);
      gradient.addColorStop(0.65, `rgba(${cr},${cg},${cb},${alpha * 0.45})`);
      gradient.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
      ctx.fillStyle = gradient;
      ctx.fill();
      // 内层高亮环(带轻微变形)
      ctx.beginPath();
      for (let i = 0; i < segments; i++) {
        const angle = (i / segments) * Math.PI * 2;
        const innerDistortion =
          r - waveAmp * 0.6 +
          Math.sin(angle * 5 + time * 0.002) * waveAmp * 0.7;
        const px = blob.x + Math.cos(angle) * innerDistortion;
        const py = blob.y + Math.sin(angle) * innerDistortion;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.strokeStyle = "rgba(255,255,255,0.2)";
      ctx.lineWidth = 1;
      ctx.stroke();
    };

    // 点击涟漪: 4 同心环交错(原始 Demo §6.4)
    const drawRipple = (rp: Ripple, time: number): void => {
      const elapsed = time - rp.birth;
      if (elapsed < 0 || elapsed > RIPPLE_LIFETIME) return;
      const progress = elapsed / RIPPLE_LIFETIME;
      const [cr, cg, cb] = rp.color;
      for (let i = 0; i < 4; i++) {
        const stagger = i * 0.12;
        const ringProgress = Math.max(
          0, Math.min(1, (progress - stagger) / (0.75 - stagger))
        );
        if (ringProgress <= 0) continue;
        ctx.beginPath();
        ctx.arc(rp.x, rp.y, ringProgress * rp.maxRadius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${cr},${cg},${cb},${(1 - ringProgress) * 0.35 * (1 - i * 0.18)})`;
        ctx.lineWidth = Math.max(0.5, 2.5 - ringProgress * 2);
        ctx.stroke();
      }
    };

    // 鼠标拖尾: 描边圆环(原始 Demo §6.5)
    const drawTrail = (tr: Trail, time: number): void => {
      const elapsed = time - tr.birth;
      if (elapsed < 0 || elapsed > TRAIL_LIFETIME) return;
      const progress = elapsed / TRAIL_LIFETIME;
      const r = progress * 50;
      const alpha = (1 - progress) * (1 - progress) * 0.28;
      const [cr, cg, cb] = tr.color;
      ctx.beginPath();
      ctx.arc(tr.x, tr.y, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${cr},${cg},${cb},${alpha})`;
      ctx.lineWidth = Math.max(0.4, 3 - progress * 2.5);
      ctx.stroke();
    };

    // 底部环境波: 3 条双谐波正弦(原始 Demo §6.6)
    const drawBottomWaves = (time: number): void => {
      const yBase = h * 0.82 + Math.sin(time * 0.00015) * 30;
      const waves = [
        { amp: 16, freq: 0.0035, speed: 0.0005, color: "rgba(147,197,253,0.12)", lw: 2 },
        { amp: 22, freq: 0.0028, speed: 0.00035, color: "rgba(196,181,253,0.10)", lw: 3 },
        { amp: 12, freq: 0.0042, speed: 0.0006, color: "rgba(249,168,212,0.09)", lw: 1.5 },
      ];
      for (const wave of waves) {
        ctx.beginPath();
        ctx.moveTo(0, yBase);
        for (let x = 0; x <= w; x += 3) {
          const y =
            yBase +
            Math.sin(x * wave.freq + time * wave.speed) * wave.amp +
            Math.cos(x * wave.freq * 1.7 + time * wave.speed * 0.6) * wave.amp * 0.4;
          ctx.lineTo(x, y);
        }
        ctx.strokeStyle = wave.color;
        ctx.lineWidth = wave.lw;
        ctx.stroke();
      }
    };

    const frame = (time: number): void => {
      if (!running) return;
      ctx.clearRect(0, 0, w, h);
      drawBottomWaves(time);
      for (const blob of blobs) {
        blob.x += blob.vx + Math.sin(time * blob.freqX + blob.phaseX) * blob.amplitudeX * 0.003;
        blob.y += blob.vy + Math.cos(time * blob.freqY + blob.phaseY) * blob.amplitudeY * 0.003;
        const margin = blob.radius;
        if (blob.x < -margin) blob.x = w + margin;
        if (blob.x > w + margin) blob.x = -margin;
        if (blob.y < -margin) blob.y = h + margin;
        if (blob.y > h + margin) blob.y = -margin;
        blob.currentRadius = blob.radius * (1 + Math.sin(time * 0.001 + blob.phaseX) * 0.06);
        drawWavyBlob(blob, blob.currentRadius, time);
      }
      for (const rp of ripples) drawRipple(rp, time);
      if (mouseActive && time - lastTrailTs > TRAIL_INTERVAL_MS) {
        lastTrailTs = time;
        trails.push({
          x: mouseX,
          y: mouseY,
          birth: time,
          color: RIPPLE_COLORS[Math.floor(random(0, RIPPLE_COLORS.length))],
        });
        if (trails.length > TRAIL_MAX) trails.splice(0, trails.length - TRAIL_MAX);
      }
      for (const tr of trails) drawTrail(tr, time);
      raf = window.requestAnimationFrame(frame);
    };

    const onClick = (e: MouseEvent): void => {
      ripples.push({
        x: e.clientX,
        y: e.clientY,
        birth: performance.now(),
        maxRadius: random(120, 200),
        color: RIPPLE_COLORS[Math.floor(random(0, RIPPLE_COLORS.length))],
      });
    };

    const onMove = (e: MouseEvent): void => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      mouseActive = true;
    };

    const onLeave = (): void => {
      mouseActive = false;
    };

    resize();
    if (reduced) {
      for (const blob of blobs) drawWavyBlob(blob, blob.radius, 0);
      drawBottomWaves(0);
    } else {
      raf = window.requestAnimationFrame(frame);
      document.addEventListener("click", onClick);
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseleave", onLeave);
    }
    window.addEventListener("resize", resize);

    return () => {
      running = false;
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("click", onClick);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseleave", onLeave);
    };
  }, []);

  if (reducedRef.current) return null;
  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        zIndex: 0,
      }}
    />
  );
}
