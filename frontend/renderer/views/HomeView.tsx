// Phase 1.5 - HomeView 首页
// 顶部: 壁纸背景(由设置页"主题壁纸"管理) + 暖心短语 + 天气
// 中部: 三个模式按钮(工作/分析/设计)
// 2026-08-08: 智能体标识与改名入口从首页移除, 合并到左侧边栏(避免重复)
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

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

// 2026-08-20: WMO weather code → 中文 + emoji(对应 Open-Meteo current.weather_code)
const WMO_WEATHER: Record<number, { label: string; emoji: string }> = {
  0: { label: "晴", emoji: "☀️" },
  1: { label: "晴间多云", emoji: "🌤️" },
  2: { label: "多云", emoji: "⛅" },
  3: { label: "阴", emoji: "☁️" },
  45: { label: "雾", emoji: "🌫️" },
  48: { label: "雾凇", emoji: "🌫️" },
  51: { label: "毛毛雨", emoji: "🌦️" },
  53: { label: "小雨", emoji: "🌦️" },
  55: { label: "中雨", emoji: "🌧️" },
  56: { label: "冻雨", emoji: "🌧️" },
  61: { label: "小雨", emoji: "🌧️" },
  63: { label: "中雨", emoji: "🌧️" },
  65: { label: "大雨", emoji: "🌧️" },
  66: { label: "冻雨", emoji: "🌧️" },
  67: { label: "强冻雨", emoji: "🌧️" },
  71: { label: "小雪", emoji: "🌨️" },
  73: { label: "中雪", emoji: "🌨️" },
  75: { label: "大雪", emoji: "❄️" },
  77: { label: "雪粒", emoji: "🌨️" },
  80: { label: "阵雨", emoji: "🌦️" },
  81: { label: "阵雨", emoji: "🌧️" },
  82: { label: "强阵雨", emoji: "⛈️" },
  85: { label: "阵雪", emoji: "🌨️" },
  86: { label: "强阵雪", emoji: "❄️" },
  95: { label: "雷阵雨", emoji: "⛈️" },
  96: { label: "雷阵雨伴冰雹", emoji: "⛈️" },
  99: { label: "强雷阵雨伴冰雹", emoji: "⛈️" },
};

// 2026-08-20: 省份 → 地级市静态数据集(离线, 无需联网)。
// 用于首页天气"城市切换"弹窗(双列: 左省 / 右市)。名称与 Open-Meteo 中文
// geocoding 对齐(地级市级均可解析), 点选后写回 App 的 city 状态(pa:city)。
const PROVINCE_CITIES: Record<string, string[]> = {
  北京: ["北京"],
  天津: ["天津"],
  上海: ["上海"],
  重庆: ["重庆"],
  河北: ["石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"],
  山西: ["太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁"],
  内蒙古: ["呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布", "兴安盟", "锡林郭勒盟", "阿拉善盟"],
  辽宁: ["沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛"],
  吉林: ["长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边"],
  黑龙江: ["哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化", "大兴安岭"],
  江苏: ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"],
  浙江: ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"],
  安徽: ["合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城"],
  福建: ["福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德"],
  江西: ["南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"],
  山东: ["济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽"],
  河南: ["郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店", "济源"],
  湖北: ["武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施"],
  湖南: ["长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西"],
  广东: ["广州", "韶关", "深圳", "珠海", "汕头", "佛山", "江门", "湛江", "茂名", "肇庆", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮"],
  广西: ["南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左"],
  海南: ["海口", "三亚", "三沙", "儋州"],
  四川: ["成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "阿坝", "甘孜", "凉山"],
  贵州: ["贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔西南", "黔东南", "黔南"],
  云南: ["昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄", "红河", "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆"],
  西藏: ["拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里"],
  陕西: ["西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛"],
  甘肃: ["兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "临夏", "甘南"],
  青海: ["西宁", "海东", "海北", "黄南", "海南", "果洛", "玉树", "海西"],
  宁夏: ["银川", "石嘴山", "吴忠", "固原", "中卫"],
  新疆: ["乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "昌吉", "博尔塔拉", "巴音郭楞", "阿克苏", "克孜勒苏", "喀什", "和田", "伊犁", "塔城", "阿勒泰", "石河子"],
  香港: ["香港"],
  澳门: ["澳门"],
  台湾: ["台北", "高雄", "台中", "台南", "基隆", "新竹", "嘉义"],
};
const PROVINCES: string[] = Object.keys(PROVINCE_CITIES);

export default function HomeView({
  onPickMode,
  activeSkill,
  sessionId,
  theme,
  city,
  setCity,
  slotActive,
}: {
  onPickMode: (skill: "office" | "data_analysis" | "frontend_design") => void;
  activeSkill: string | null;
  sessionId: number | null;
  theme?: "light" | "dark";
  /** 2026-08-20: 天气城市(首页实时天气按此取数) */
  city?: string;
  /** 2026-08-20: 城市切换写回(首页天气弹窗选城市时回写 pa:city) */
  setCity?: (v: string) => void;
  /** 0.5.0 P5: 场景技能 → 是否有未关闭对话(绿=对话中/红=无对话) */
  slotActive?: (skill: string) => boolean;
}): JSX.Element {
  const greeting = pickGreeting();
  const dateLabel = pickDateLabel();
  // 2026-08-20: 首页天气改由 Open-Meteo 实时获取(按 city), 不再硬编码。
  // 加载中 weather=null; 失败 weatherErr=true(诚实显示, 绝不回退假数据)。
  // 2026-08-20(增强): 同时取 daily(未来 7 日)用于"点击图标向右侧扩展"预报;
  // daily 结构 { date, weekday, label, emoji, tmin, tmax }。
  const [weather, setWeather] = useState<{ label: string; temp: string; emoji: string } | null>(null);
  const [daily, setDaily] = useState<
    { date: string; weekday: string; label: string; emoji: string; tmin: number; tmax: number }[] | null
  >(null);
  const [weatherErr, setWeatherErr] = useState(false);
  // 2026-08-20(增强): 交互态 —— 7 日预报展开 / 城市选择弹窗 / 弹窗内选中省份
  const [showForecast, setShowForecast] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [pickerProvince, setPickerProvince] = useState<string>("湖北");
  // 2026-08-20(修复): 弹窗锚点(城市按钮的视口坐标), 用于 portal 固定定位;
  // 避免嵌在首页滚动容器 + 祖先 animation 层叠上下文导致被壁纸遮挡。
  const [pickerAnchor, setPickerAnchor] = useState<{ top: number; right: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const cityName = (city ?? "武汉").trim();
    if (!cityName) {
      setWeather(null);
      setDaily(null);
      setWeatherErr(false);
      return;
    }
    const cacheKey = `pa:weather:${cityName}`;
    try {
      const cached = JSON.parse(localStorage.getItem(cacheKey) ?? "null");
      if (cached && typeof cached.t === "number" && Date.now() - cached.t < 30 * 60 * 1000) {
        setWeather(cached.w);
        setDaily(cached.d ?? null);
        setWeatherErr(false);
        return;
      }
    } catch {
      /* ignore */
    }
    void (async () => {
      try {
        const geoResp = await fetch(
          `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(cityName)}&count=1&language=zh&format=json`
        );
        if (!geoResp.ok) throw new Error("geocode");
        const geo = await geoResp.json();
        const loc = geo?.results?.[0];
        if (!loc) throw new Error("no-location");
        const fcResp = await fetch(
          `https://api.open-meteo.com/v1/forecast?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min` +
            `&forecast_days=7&timezone=auto`
        );
        if (!fcResp.ok) throw new Error("forecast");
        const fc = await fcResp.json();
        const temp = Math.round(Number(fc?.current?.temperature_2m));
        const code = Number(fc?.current?.weather_code);
        const m = WMO_WEATHER[code] ?? { label: "未知", emoji: "🌡️" };
        const w = { label: m.label, temp: `${temp}°C`, emoji: m.emoji };
        // 解析 daily → 7 日预报(今天/周几 + emoji + 温度区间)
        const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
        const dRaw = fc?.daily;
        const d: { date: string; weekday: string; label: string; emoji: string; tmin: number; tmax: number }[] = [];
        if (Array.isArray(dRaw?.time)) {
          for (let i = 0; i < dRaw.time.length && i < 7; i++) {
            const dt = new Date(`${dRaw.time[i]}T00:00:00`);
            const dc = Number(dRaw.weather_code?.[i]);
            const dm = WMO_WEATHER[dc] ?? { label: "未知", emoji: "🌡️" };
            d.push({
              date: `${dt.getMonth() + 1}/${dt.getDate()}`,
              weekday: i === 0 ? "今天" : weekdays[dt.getDay()],
              label: dm.label,
              emoji: dm.emoji,
              tmin: Math.round(Number(dRaw.temperature_2m_min?.[i])),
              tmax: Math.round(Number(dRaw.temperature_2m_max?.[i])),
            });
          }
        }
        if (!cancelled) {
          setWeather(w);
          setDaily(d);
          setWeatherErr(false);
          try {
            localStorage.setItem(cacheKey, JSON.stringify({ w, d, t: Date.now() }));
          } catch {
            /* ignore */
          }
        }
      } catch {
        if (!cancelled) setWeatherErr(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [city]);
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
          <div className="flex-1-min0">
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
          {/* 2026-08-20(增强): 天气区 = 当前天气块 + (展开)7日预报 + 城市名切换弹窗 */}
          <div style={{ flexShrink: 0, display: "flex", alignItems: "stretch", gap: 12, position: "relative" }}>
            {/* 当前天气块(右对齐) */}
            <div style={{ textAlign: "right", display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
              {/* 天气图标: 点击切换 7 日预报(toggle, 向右侧扩展) */}
              <button
                type="button"
                onClick={() => {
                  setShowForecast((f) => !f);
                  setShowPicker(false);
                }}
                title={showForecast ? "收起 7 日预报" : "点击查看未来 7 日天气"}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 0,
                  fontSize: 30,
                  lineHeight: 1,
                  filter: showForecast ? "drop-shadow(0 0 6px rgba(167,139,250,0.6))" : "none",
                }}
              >
                {weather ? weather.emoji : "🌡️"}
              </button>
              <div style={{ fontSize: "var(--fs-hero)", fontWeight: 700, letterSpacing: "-0.02em", marginTop: 2 }}>
                {weather ? weather.temp : "—"}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                {weather ? weather.label : weatherErr ? "天气暂不可用" : "获取中…"}
              </div>
              {/* 城市名(点击弹出省份-城市选择弹窗) */}
              <button
                type="button"
                onClick={(e) => {
                  const cur = (city ?? "武汉").trim();
                  const prov = PROVINCES.find((p) => (PROVINCE_CITIES[p] ?? []).includes(cur)) ?? "湖北";
                  setPickerProvince(prov);
                  // 记录城市按钮视口坐标, 供 portal 固定定位(弹窗出现在按钮下方)
                  const r = e.currentTarget.getBoundingClientRect();
                  setPickerAnchor({ top: r.bottom, right: window.innerWidth - r.right });
                  setShowPicker((p) => !p);
                  setShowForecast(false);
                }}
                title="点击切换城市"
                style={{
                  marginTop: 6,
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                  padding: 0,
                }}
              >
                📍 {city ?? "武汉"} ▾
              </button>
            </div>

            {/* 7 日预报(点击图标向右侧扩展; 无数据时提示) */}
            {showForecast && (
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "center",
                  borderLeft: "1px solid rgba(148,163,184,0.18)",
                  paddingLeft: 12,
                  animation: "toast-slide-in 0.3s ease both",
                }}
              >
                {daily && daily.length > 0 ? (
                  daily.map((dd, i) => (
                    <div key={i} style={{ textAlign: "center", minWidth: 46 }}>
                      <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{dd.weekday}</div>
                      <div style={{ fontSize: 20, lineHeight: 1.3 }}>{dd.emoji}</div>
                      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                        {dd.tmin}°~{dd.tmax}°
                      </div>
                      <div style={{ fontSize: 9, color: "var(--text-tertiary)", marginTop: 1 }}>{dd.label}</div>
                    </div>
                  ))
                ) : (
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
                    {weatherErr ? "天气数据暂不可用" : "预报加载中…"}
                  </div>
                )}
              </div>
            )}

            {/* 省份-城市选择弹窗(portal 渲染到 body 最顶层, 避免被壁纸/层叠上下文遮挡) */}
            {showPicker && pickerAnchor && createPortal(
              <>
                <div
                  onClick={() => setShowPicker(false)}
                  style={{ position: "fixed", inset: 0, zIndex: 9998 }}
                />
                <div
                  style={{
                    position: "fixed",
                    top: pickerAnchor.top + 8,
                    right: pickerAnchor.right,
                    zIndex: 9999,
                    width: 320,
                    maxHeight: 300,
                    background: "var(--panel-bg)",
                    border: "1px solid rgba(148,163,184,0.2)",
                    borderRadius: 12,
                    boxShadow: "0 12px 40px rgba(0,0,0,0.18)",
                    display: "flex",
                    overflow: "hidden",
                    animation: "toast-slide-in 0.25s ease both",
                  }}
                >
                  {/* 左: 省份列 */}
                  <div
                    style={{
                      width: 104,
                      overflowY: "auto",
                      borderRight: "1px solid rgba(148,163,184,0.15)",
                      padding: "6px 0",
                    }}
                  >
                    {PROVINCES.map((p) => (
                      <div
                        key={p}
                        onClick={() => setPickerProvince(p)}
                        style={{
                          padding: "7px 12px",
                          fontSize: 13,
                          cursor: "pointer",
                          background: pickerProvince === p ? "var(--panel-bg-hover)" : "transparent",
                          color: pickerProvince === p ? "var(--accent-soft-text)" : "var(--text-primary)",
                          fontWeight: pickerProvince === p ? 700 : 400,
                        }}
                      >
                        {p}
                      </div>
                    ))}
                  </div>
                  {/* 右: 城市列(按省份筛选, 可滚动) */}
                  <div
                    style={{
                      flex: 1,
                      overflowY: "auto",
                      padding: 8,
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 6,
                      alignContent: "flex-start",
                    }}
                  >
                    {(PROVINCE_CITIES[pickerProvince] ?? []).map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => {
                          setCity?.(c);
                          setShowPicker(false);
                        }}
                        style={{
                          fontSize: 12,
                          padding: "5px 10px",
                          borderRadius: 8,
                          border: "1px solid rgba(148,163,184,0.25)",
                          background: "var(--panel-bg-hover)",
                          color: "var(--text-primary)",
                          cursor: "pointer",
                        }}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                </div>
              </>,
              document.body
            )}
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
      <div className="glass-panel pad-lg">
        <div style={{ fontSize: "var(--fs-title)", fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
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
                // P1-2(2026-08-17): hover 位移由 CSS .mode-card:hover 承担(原 JS onMouseEnter)
                className={`animate-in mode-card${isActive ? " active" : ""}`}
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
                  fontFamily: "inherit",
                  color: "var(--text-primary)",
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