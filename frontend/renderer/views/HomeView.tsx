// Phase 1.5 - HomeView 首页
// 顶部: 壁纸背景(由设置页"主题壁纸"管理) + 暖心短语 + 天气
// 中部: 三个模式按钮(工作/分析/设计)
import { useEffect, useMemo, useState } from "react";

const API_BASE = "http://127.0.0.1:8765/admin";
const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

const MODE_BUTTONS: { skill: "office" | "data_analysis" | "frontend_design"; title: string; subtitle: string; gradient: string }[] = [
  {
    skill: "office",
    title: "工作模式",
    subtitle: "文档处理 · 数据分析 · 网页研究",
    gradient: "linear-gradient(135deg, #818cf8, #6366f1)",
  },
  {
    skill: "data_analysis",
    title: "分析模式",
    subtitle: "数据可视化 · 统计检验 · 报告生成",
    gradient: "linear-gradient(135deg, #c084fc, #a855f7)",
  },
  {
    skill: "frontend_design",
    title: "设计模式",
    subtitle: "HTML/React 生成 · 设计系统 RAG",
    gradient: "linear-gradient(135deg, #f472b6, #ec4899)",
  },
];

const WARM_QUOTES = [
  "慢慢来, 一切都在有条不紊地进行。",
  "今天的小成果, 是明天的大铺垫。",
  "专注当下, 杂念自会远去。",
  "一杯咖啡, 一段代码, 一个好想法。",
  "别急, 漂亮的事都需要时间。",
  "保持好奇, 持续学习, 日常便不寻常。",
];

function pickGreeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

function pickDateLabel(): string {
  const d = new Date();
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 · ${weekdays[d.getDay()]}`;
}

function pickWeather(): { label: string; temp: string; emoji: string } {
  const hour = new Date().getHours();
  const emoji = hour < 18 ? "☀️" : "🌙";
  return { label: "晴", temp: "25°C", emoji };
}

export default function HomeView({
  onPickMode,
  activeSkill,
  sessionId,
}: {
  onPickMode: (skill: "office" | "data_analysis" | "frontend_design") => void;
  activeSkill: string | null;
  sessionId: number | null;
}): JSX.Element {
  const greeting = pickGreeting();
  const dateLabel = pickDateLabel();
  const weather = pickWeather();
  const seed = useMemo(() => sessionId ?? 0, [sessionId]);
  const quote = useMemo(() => {
    const idx = (seed + new Date().getDate()) % WARM_QUOTES.length;
    return WARM_QUOTES[idx];
  }, [seed]);

  // 壁纸(由设置页管理, 这里只负责加载显示)
  const [wallpaper, setWallpaper] = useState<string | null>(null);
  const [wpStyle, setWpStyle] = useState<{
    position_x: number;
    position_y: number;
    fit: string;
    scale: number;
    rotate: number;
  }>({
    position_x: 50,
    position_y: 50,
    fit: "cover",
    scale: 100,
    rotate: 0,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${API_BASE}/wallpaper`);
        const data = await resp.json();
        if (!cancelled) {
          // URL 加时间戳强制绕过浏览器缓存(壁纸文件名固定, 换图后必须重新拉取)
          const url = data.wallpaper
            ? `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`
            : null;
          setWallpaper(url);
          if (data.style) setWpStyle(data.style);
        }
      } catch {
        if (!cancelled) setWallpaper(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 16,
        overflowY: "auto",
        paddingRight: 4,
        animation: "flow-slide-up 0.6s var(--transition-smooth) both",
      }}
    >
      {/* ① 最上方: 问候/日期/天气/暖心短语 信息卡(独立于壁纸, 不再叠加) */}
      <div className="glass-panel" style={{ padding: "18px 24px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 16,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.1em",
                padding: "3px 10px",
                borderRadius: 20,
                background: "rgba(255,255,255,0.7)",
                color: "var(--text-primary)",
                display: "inline-block",
                marginBottom: 8,
              }}
            >
              DAILY DASHBOARD · 今日工作台
            </div>
            <h1
              style={{
                fontSize: 24,
                fontWeight: 700,
                letterSpacing: "-0.03em",
                margin: 0,
                color: "var(--text-primary)",
              }}
            >
              {greeting}, 欢迎回来
            </h1>
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}>
              {dateLabel}
            </div>
          </div>
          <div style={{ textAlign: "right", flexShrink: 0 }}>
            <div style={{ fontSize: 30, lineHeight: 1 }}>{weather.emoji}</div>
            <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em", marginTop: 2 }}>
              {weather.temp}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
              {weather.label} · 适宜
            </div>
          </div>
        </div>
        <div
          style={{
            fontSize: 13,
            color: "var(--text-secondary)",
            marginTop: 10,
            lineHeight: 1.6,
            borderTop: "1px solid rgba(148,163,184,0.15)",
            paddingTop: 10,
          }}
        >
          {quote}
        </div>
      </div>

      {/* ② 中间: 壁纸区(居中显示, 占满剩余空间) */}
      <div
        className="glass-panel"
        style={{
          flex: 1,
          padding: 0,
          position: "relative",
          overflow: "hidden",
          minHeight: 220,
        }}
      >
        {/* 默认渐变(FlowSpace 蓝紫调, 无壁纸时兜底) */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(135deg, #eef1f8 0%, #e6ebf6 45%, #ece7f7 100%)",
          }}
        />
        {/* 用户壁纸(支持位置/填充/缩放/旋转) */}
        {wallpaper && (
          <img
            src={wallpaper}
            alt="壁纸"
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: wpStyle.fit === "contain" ? "contain" : "cover",
              objectPosition: `${wpStyle.position_x}% ${wpStyle.position_y}%`,
              transform: `scale(${(wpStyle.scale ?? 100) / 100}) rotate(${wpStyle.rotate ?? 0}deg)`,
            }}
          />
        )}
      </div>

      {/* 三个模式按钮 */}
      <div className="glass-panel" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          选择模式开始
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
          每个模式对应一个 Skill, 选择后即可开启对话
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 12,
          }}
        >
          {MODE_BUTTONS.map((m) => {
            const isActive = activeSkill === m.skill;
            return (
              <button
                key={m.skill}
                onClick={() => onPickMode(m.skill)}
                className="animate-in"
                style={{
                  padding: "16px 18px",
                  borderRadius: "var(--radius-md)",
                  border: isActive
                    ? "1px solid rgba(139,92,246,0.5)"
                    : "1px solid rgba(255,255,255,0.7)",
                  background: isActive
                    ? "rgba(255,255,255,0.85)"
                    : "rgba(255,255,255,0.55)",
                  backdropFilter: "blur(20px) saturate(180%)",
                  boxShadow: isActive
                    ? "0 8px 32px rgba(139,92,246,0.18), inset 0 1px 0 rgba(255,255,255,0.9)"
                    : "0 4px 16px rgba(148,163,184,0.1), inset 0 1px 0 rgba(255,255,255,0.85)",
                  cursor: "pointer",
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                  transition: "all 0.25s var(--transition-smooth)",
                  fontFamily: "inherit",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = "translateY(-2px)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "translateY(0)";
                }}
              >
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 10,
                    background: m.gradient,
                    boxShadow: "0 4px 12px rgba(139,92,246,0.2)",
                  }}
                />
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}>{m.title}</div>
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4, lineHeight: 1.5 }}>
                    {m.subtitle}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        {activeSkill && (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 14, textAlign: "center" }}>
            当前会话已激活 <span style={{ color: "var(--success-text)", fontWeight: 600 }}>{activeSkill}</span> · session={sessionId ?? "—"}
          </div>
        )}
      </div>
    </div>
  );
}
