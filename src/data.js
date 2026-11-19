export const DURATION = 64;

const cueSeed = [
  [2.2, 1.85, "晚上好，欢迎收看今天的晚间现场。"],
  [4.8, 1.72, "本台记者正在城市交通指挥中心。"],
  [7.4, 1.92, "目前晚高峰的车流已经进入峰值。"],
  [10.0, 1.66, "东三环南向北方向通行压力较大。"],
  [12.6, 1.84, "交管部门已启动分级疏导预案。"],
  [15.2, 1.75, "请驾驶员尽量避开核心施工路段。"],
  [17.8, 1.9, "现场新增了两条临时可变车道。"],
  [20.4, 1.68, "根据我们刚刚收到的画面，" ],
  [23.0, 1.88, "西侧匝道的排队长度有所回落。"],
  [25.6, 1.76, "救援车辆正在有序通过应急通道。"],
  [28.2, 1.94, "现在我们把镜头切到前方连线点。"],
  [30.8, 1.64, "记者你好，请介绍现场情况。"],
  [33.4, 1.86, "好的，我身后就是本次分流节点。"],
  [36.0, 1.7, "目前车辆平均时速约为十八公里。"],
  [38.6, 1.9, "交警正在引导车辆向北侧绕行。"],
  [41.2, 1.73, "预计这一波高峰还会持续四十分钟。"],
  [43.8, 1.83, "提醒大家保持车距并服从现场指挥。"],
  [46.4, 1.78, "预计降雨会在午夜前后逐渐减弱。"],
  [49.0, 1.9, "气象台稍早前发布了黄色预警。"],
  [51.6, 1.68, "晚些时候仍可能出现局地强降雨。"],
  [54.2, 1.86, "我们会持续关注天气和路况变化。"],
  [56.8, 1.72, "更多消息请关注本台后续报道。"],
  [59.4, 1.94, "感谢收看，我们稍后见。"],
];

export const CUES = cueSeed.map(([start, duration, text], index) => {
  const id = `C${String(index + 1).padStart(2, "0")}`;
  const isLate = index === 17;
  return {
    id,
    index,
    start,
    end: +(start + duration).toFixed(2),
    duration,
    text,
    speaker: index >= 11 && index <= 16 ? "现场记者" : "主播",
    confidence: isLate ? 91 : 94 + ((index * 7) % 6),
    arrivedAt: +(isLate ? start + 5.8 : start - 0.35).toFixed(2),
    late: isLate,
  };
});

export const LATE_CUE_ID = CUES.find((cue) => cue.late)?.id ?? null;

export const SCENES = [
  { id: "S01", time: 0, end: 13.2, label: "主播全景", camera: "CAM 02" },
  { id: "S02", time: 13.2, end: 28.0, label: "交通中心", camera: "CAM 05" },
  { id: "S03", time: 28.0, end: 44.2, label: "外景连线", camera: "LIVE 01" },
  { id: "S04", time: 44.2, end: 58.0, label: "分屏解说", camera: "MIX 03" },
  { id: "S05", time: 58.0, end: DURATION, label: "回到主播", camera: "CAM 02" },
];

export const VISION_EVENTS = [
  ...SCENES.map((scene) => ({
    time: scene.time,
    type: "scene",
    label: scene.label,
    detail: `${scene.camera} · 导播切换`,
  })),
  ...CUES.map((cue) => ({
    time: cue.start,
    type: "speech",
    label: `${cue.id} 发声锚点`,
    detail: `能量峰值 · ${cue.text.slice(0, 10)}…`,
  })),
].sort((a, b) => a.time - b.time);

export const SCENARIO = {
  stepAt: 18,
  stepDelay: 1.8,
  spikeAt: 34,
  spikeDuration: 1.2,
  spikeAmplitude: 0.65,
  lateGrace: 0.9,
  smoothingSeconds: 0.9,
  persistenceSeconds: 2,
  correctionRate: 0.42,
  minGap: 0.16,
};

export const SOURCE_META = {
  vision: { label: "画面事件", count: VISION_EVENTS.length },
  asr: { label: "识别结果", count: CUES.length },
  buffer: { label: "播放器缓冲", count: Math.round(DURATION / 0.5) },
};

export function timecode(seconds, withFrames = false) {
  const safe = Math.max(0, Math.min(DURATION, seconds));
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = Math.floor(safe % 60);
  const f = Math.floor((safe % 1) * 50);
  const base = [h, m, s].map((part) => String(part).padStart(2, "0")).join(":");
  return withFrames ? `${base}:${String(f).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${safe.toFixed(1).padStart(4, "0")}`;
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
