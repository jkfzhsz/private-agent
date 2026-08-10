// Phase 1.5 - HomeView 首页
// 顶部: 壁纸背景(由设置页"主题壁纸"管理) + 暖心短语 + 天气
// 中部: 三个模式按钮(工作/分析/设计)
// 2026-08-08: 智能体标识与改名入口从首页移除, 合并到左侧边栏(避免重复)
import { useEffect, useMemo, useRef, useState } from "react";

import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://127.0.0.1:8765/admin";
const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

// 0.5.0 M1(2026-08-08): 场景中文名(本地映射, 与后端 skill.yaml scene_name 同步)
const SCENARIO_LABELS: Record<string, string> = {
  office: "子瞻",
  data_analysis: "白圭",
  frontend_design: "清和",
};

// 0.5.0 M1(2026-08-08): 三场景命名 —— 子瞻/白圭/清和
// 2026-08-08 蒋先生反馈: 按钮标题只保留两字场景名, 详细职责放 subtitle
const MODE_BUTTONS: { skill: "office" | "data_analysis" | "frontend_design"; title: string; subtitle: string; gradient: string }[] = [
  {
    skill: "office",
    title: "子瞻",
    subtitle: "工作与学习 · 文档处理 · 数据分析 · 学习辅导",
    gradient: "linear-gradient(135deg, #818cf8, #6366f1)",
  },
  {
    skill: "data_analysis",
    title: "白圭",
    subtitle: "投资与理财 · 行情 · 基金 · 宏观 · 财务分析",
    gradient: "linear-gradient(135deg, #c084fc, #a855f7)",
  },
  {
    skill: "frontend_design",
    title: "清和",
    subtitle: "生活健康与美学 · 健康管理 · 美学设计 · 前端设计",
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
  theme,
  slotActive,
}: {
  onPickMode: (skill: "office" | "data_analysis" | "frontend_design") => void;
  activeSkill: string | null;
  sessionId: number | null;
  theme?: "light" | "dark";
  /** 0.5.0 P5: 场景技能 → 是否有未关闭对话(绿=对话中/红=无对话) */
  slotActive?: (skill: string) => boolean;
}): JSX.Element {
  const greeting = pickGreeting();
  const dateLabel = pickDateLabel();
  const weather = pickWeather();
  const seed = useMemo(() => sessionId ?? 0, [sessionId]);
  const quote = useMemo(() => {
    const idx = (seed + new Date().getDate()) % WARM_QUOTES.length;
    return WARM_QUOTES[idx];
  }, [seed]);

  // 壁纸/视频背景(由设置页管理, 这里只负责加载显示; 暗色/亮色各自独立)
  const [wallpaper, setWallpaper] = useState<string | null>(null);
  const [wpType, setWpType] = useState<"image" | "video">("image");
  // 2026-08-08: 缩放 + 位置 + 旋转(设置页调整选取显示区域)。图片以完整
  // 显示(contain)为基线, scale>100 时放大, position 选择显示的图片区域。
  // 亮色/暗色主题各自保存/加载独立背景与样式。
  const [posX, setPosX] = useState(50);
  const [posY, setPosY] = useState(50);
  const [scale, setScale] = useState(100);
  const [rotate, setRotate] = useState(0);
  // 2026-08-08: 主题切换交叉淡化(纯 CSS animation 方案) —— 切主题时旧背景
  // 保留显示(带自己的样式参数), 挂载即播放 wp-fade-out 淡出; 新背景 img/video
  // 用 key={src} 强制重挂载并播放 wp-fade-in 淡入, 两者同步交叉淡化。
  // 不用 transition 的原因: 本地图加载极快, opacity:0 帧可能未被绘制,
  // transition 无起点 → 淡入失效(见 design-tokens.css keyframes 注释)。
  const [prevLayer, setPrevLayer] = useState<{
    src: string;
    type: "image" | "video";
    posX: number;
    posY: number;
    scale: number;
    rotate: number;
  } | null>(null);
  // 渲染时同步的"当前显示"引用: effect 里读它判断切换前显示的旧背景
  const shownRef = useRef<string | null>(null);
  shownRef.current = wallpaper;

  useEffect(() => {
    let cancelled = false;
    const themeKey = theme ?? "light";
    (async () => {
      try {
        const resp = await adminFetch(
          `${API_BASE}/wallpaper?theme=${encodeURIComponent(themeKey)}`
        );
        const data = await resp.json();
        if (!cancelled) {
          // URL 加时间戳强制绕过浏览器缓存(壁纸文件名固定, 换图后必须重新拉取)
          const url = data.wallpaper
            ? `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`
            : null;
          const type = data.type === "video" ? "video" : "image";
          // 当前有背景且新背景不同(主题切换/换图) → 保留旧层播放淡出动画
          if (shownRef.current && url !== shownRef.current) {
            setPrevLayer({
              src: shownRef.current,
              type: wpType,
              posX,
              posY,
              scale,
              rotate,
            });
          } else {
            setPrevLayer(null);
          }
          setWallpaper(url);
          setWpType(type);
          if (data.style) {
            setPosX(Number(data.style.position_x) || 50);
            setPosY(Number(data.style.position_y) || 50);
            setScale(Number(data.style.scale) || 100);
            setRotate(Number(data.style.rotate) || 0);
          }
        }
      } catch {
        if (!cancelled) {
          setWallpaper(null);
          setPrevLayer(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [theme]);

  // scale 最小 100(完整显示); position 仅在放大溢出时有意义, 否则强制居中
  const s = (scale ?? 100) / 100;
  const hasOverflow = s > 1.001;
  const px = hasOverflow ? posX : 50;
  const py = hasOverflow ? posY : 50;

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 16,
        overflowY: "auto",
        // 2026-08-10: 移除右侧滚动条余量 paddingRight: 4 —— 它使首页内容右缘比
        // 左缘多缩进 4px, 叠加 App 容器中间区 margin 12 后右侧视觉空隙 = 16px、
        // 左侧 = 12px, 与设置页/对话页(纯 12px)不一致。内容左右对称贴边后,
        // 三处栏间空隙统一由 App 容器 margin: "0 12px" 提供, 各状态完全一致。
        // 2026-08-10 21:15: scrollbarGutter: stable —— 恒定预留 5px 滚动条槽位,
        // 内容不滚动时也占位, 消除"首页无滚动条/记忆知识库有滚动条"的宽度差,
        // 所有视图中间区域内容宽度统一。
        scrollbarGutter: "stable",
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
            {/* 2026-08-08: 智能体标识与改名入口已合并到左侧边栏, 此处仅保留问候语 */}
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.1em",
                padding: "3px 10px",
                borderRadius: 20,
                background: "var(--panel-bg-hover)",
                color: "var(--text-primary)",
                display: "inline-block",
                marginBottom: 8,
              }}
            >
              今日工作台
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
          <div style={{ textAlign: "right", flexShrink: 0, display: "flex", alignItems: "center", gap: 12 }}>
            <div>
              <div style={{ fontSize: 30, lineHeight: 1 }}>{weather.emoji}</div>
              <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em", marginTop: 2 }}>
                {weather.temp}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                {weather.label} · 适宜
              </div>
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

      {/* ② 中间: 壁纸区(居中显示, 占满剩余空间; 随侧边栏拖动自适应, cover 永远铺满) */}
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
        {/* 默认渐变(跟随主题: 暗色下用深色渐变, 无壁纸时兜底) */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              theme === "dark"
                ? "linear-gradient(135deg, #0f172a 0%, #1e1b4b 45%, #111827 100%)"
                : "linear-gradient(135deg, #eef1f8 0%, #e6ebf6 45%, #ece7f7 100%)",
            transition: "background 0.4s ease",
          }}
        />
        {/* 旧背景层(主题切换交叉淡化: 挂载即播放淡出动画, 结束后卸载) */}
        {prevLayer && prevLayer.type === "video" && (
          <video
            src={prevLayer.src}
            autoPlay
            loop
            muted
            playsInline
            aria-hidden
            style={{
              position: "absolute",
              left: `${prevLayer.posX}%`,
              top: `${prevLayer.posY}%`,
              width: `${Math.max(prevLayer.scale, 100)}%`,
              height: `${Math.max(prevLayer.scale, 100)}%`,
              objectFit: "contain",
              transform: `translate(-${prevLayer.posX}%, -${prevLayer.posY}%) rotate(${prevLayer.rotate}deg)`,
              animation: "wp-fade-out 0.5s ease both",
            }}
            onAnimationEnd={() => setPrevLayer(null)}
          />
        )}
        {prevLayer && prevLayer.type === "image" && (
          <img
            src={prevLayer.src}
            alt=""
            aria-hidden
            style={{
              position: "absolute",
              left: `${prevLayer.posX}%`,
              top: `${prevLayer.posY}%`,
              width: `${Math.max(prevLayer.scale, 100)}%`,
              height: `${Math.max(prevLayer.scale, 100)}%`,
              objectFit: "contain",
              transform: `translate(-${prevLayer.posX}%, -${prevLayer.posY}%) rotate(${prevLayer.rotate}deg)`,
              animation: "wp-fade-out 0.5s ease both",
            }}
            onAnimationEnd={() => setPrevLayer(null)}
          />
        )}
        {/* 新背景层: 完整显示(contain)为基线, scale 放大 + position 选区域 +
            rotate 修正方向; key={src} 保证切换时重挂载并播放淡入动画。 */}
        {wallpaper && wpType === "video" && (
          <video
            key={wallpaper}
            src={wallpaper}
            autoPlay
            loop
            muted
            playsInline
            aria-label="动态背景"
            style={{
              position: "absolute",
              left: `${px}%`,
              top: `${py}%`,
              // 缩放落元素尺寸(width/height=scale%), left%(按容器)与
              // translate(-%)(按元素)基数不同才有净偏移; scale 下限 100(完整显示)
              width: `${Math.max(scale, 100)}%`,
              height: `${Math.max(scale, 100)}%`,
              objectFit: "contain",
              transform: `translate(-${px}%, -${py}%) rotate(${rotate}deg)`,
              animation: "wp-fade-in 0.5s ease both",
            }}
          />
        )}
        {wallpaper && wpType === "image" && (
          <img
            key={wallpaper}
            src={wallpaper}
            alt="壁纸"
            style={{
              position: "absolute",
              left: `${px}%`,
              top: `${py}%`,
              width: `${Math.max(scale, 100)}%`,
              height: `${Math.max(scale, 100)}%`,
              objectFit: "contain",
              transform: `translate(-${px}%, -${py}%) rotate(${rotate}deg)`,
              animation: "wp-fade-in 0.5s ease both",
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
            const hasDialog = slotActive ? slotActive(m.skill) : false;
            return (
              <button
                key={m.skill}
                onClick={() => onPickMode(m.skill)}
                className="animate-in"
                data-testid={`mode-btn-${m.skill}`}
                style={{
                  position: "relative",
                  padding: "16px 18px",
                  borderRadius: "var(--radius-md)",
                  border: isActive
                    ? "1px solid rgba(139,92,246,0.5)"
                    : "1px solid var(--glass-border)",
                  background: isActive
                    ? "var(--panel-bg-hover)"
                    : "var(--panel-bg)",
                  backdropFilter: "blur(20px) saturate(180%)",
                  boxShadow: isActive
                    ? "0 8px 32px rgba(139,92,246,0.18), inset 0 1px 0 rgba(255,255,255,0.06)"
                    : "0 4px 16px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.06)",
                  cursor: "pointer",
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                  transition: "all 0.25s var(--transition-smooth)",
                  fontFamily: "inherit",
                  color: "var(--text-primary)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = "translateY(-2px)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "translateY(0)";
                }}
              >
                {/* 0.5.0 P5(2026-08-08 蒋先生反馈): 排列方式与左下角"本地用户"卡片一致 ——
                    图标在左, 名称加粗在上, 状态行(7px 圆点 + 文字)在名称下方 */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <div
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: 10,
                      background: m.gradient,
                      boxShadow: "0 4px 12px rgba(139,92,246,0.2)",
                      flexShrink: 0,
                    }}
                  />
                  <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.2 }}>
                      {m.title}
                    </div>
                    <div
                      title={hasDialog ? "对话中(点击进入未结束对话)" : "无对话(点击开启新对话)"}
                      style={{
                        fontSize: 11,
                        color: "var(--text-tertiary)",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        lineHeight: 1.2,
                      }}
                    >
                      <span
                        style={{
                          display: "inline-block",
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          backgroundColor: hasDialog ? "#22c55e" : "#ef4444",
                          flexShrink: 0,
                        }}
                      />
                      {hasDialog ? "对话中" : "无对话"}
                    </div>
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
                    {m.subtitle}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        {activeSkill && (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 14, textAlign: "center" }}>
            {/* 0.5.0 M1: 显示场景中文名而非技术标识 */}
            当前会话已激活 <span style={{ color: "var(--success-text)", fontWeight: 600 }}>{SCENARIO_LABELS[activeSkill] ?? activeSkill}</span> · session={sessionId ?? "—"}
          </div>
        )}
      </div>
    </div>
  );
}