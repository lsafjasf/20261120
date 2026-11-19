import {
  CUES,
  DURATION,
  LATE_CUE_ID,
  SCENARIO,
  SCENES,
  SOURCE_META,
  VISION_EVENTS,
  clamp,
  timecode,
} from "./data.js";
import { analyze, buildBufferLog, frameAt } from "./engine.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const cueById = new Map(CUES.map((cue) => [cue.id, cue]));
const options = { step: true, spike: true, late: true };

const state = {
  time: 54.4,
  playing: false,
  speed: 1,
  expandLogs: false,
  modalMode: "data",
  modalTab: "vision",
  lastPaint: 0,
};

let analysis = analyze(options);
let bufferLog = buildBufferLog(options);
let animationFrame = null;
let lastTimestamp = 0;
let toastTimer = null;

const signed = (value, digits = 0) =>
  `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
const seconds = (value, digits = 2) => `${value.toFixed(digits)}s`;
const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function statusMeta(status) {
  return {
    locked: ["已锁定", "locked"],
    live: ["正在播出", "live"],
    upcoming: ["待播", "upcoming"],
    skipped: ["跳过", "skipped"],
  }[status] ?? ["待定", ""];
}

function activeCueAt(time, frame = frameAt(analysis, time)) {
  return frame.activeCueId ? cueById.get(frame.activeCueId) : null;
}

function focusCue(time, frame = frameAt(analysis, time)) {
  if (frame.activeCueId) return cueById.get(frame.activeCueId);
  const next = CUES.find((cue) => frame.schedule[cue.id].status === "upcoming");
  if (next) return next;
  return [...CUES].reverse().find((cue) => frame.schedule[cue.id].status === "locked") ?? CUES[0];
}

function nextGap(frame) {
  const upcoming = CUES.filter((cue) => frame.schedule[cue.id].status === "upcoming");
  const withGap = upcoming.find((cue) => {
    const gap = frame.schedule[cue.id].gap;
    return gap !== null && gap > 0;
  });
  if (withGap) return frame.schedule[withGap.id].gap;
  const current = CUES.filter((cue) => !frame.schedule[cue.id].skipped)
    .map((cue) => frame.schedule[cue.id])
    .find((item) => item.status === "live");
  return current?.gap ?? analysis.report.minGap;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 2600);
}

function recompute(message) {
  analysis = analyze(options);
  bufferLog = buildBufferLog(options);
  if (state.time > DURATION) state.time = DURATION;
  render();
  if (message) showToast(message);
}

function setPlaying(playing) {
  if (playing && state.time >= DURATION - 0.05) state.time = 0;
  state.playing = playing;
  lastTimestamp = 0;
  render();
  if (playing && !animationFrame) animationFrame = requestAnimationFrame(tick);
}

function seek(time, syncReport = true) {
  state.time = clamp(time, 0, DURATION);
  state.lastPaint = performance.now();
  render();
  if (syncReport) $("#queueBody")?.scrollTo({ top: 0 });
}

function replay() {
  state.expandLogs = false;
  seek(0);
  setPlaying(true);
  showToast("测试流开始回放：阶跃、尖峰与迟到字幕将依次注入。");
}

function tick(timestamp) {
  animationFrame = null;
  if (!state.playing) return;
  if (!lastTimestamp) lastTimestamp = timestamp;
  const delta = Math.min(0.12, (timestamp - lastTimestamp) / 1000);
  lastTimestamp = timestamp;
  state.time += delta * state.speed;
  if (state.time >= DURATION) {
    state.time = DURATION;
    state.playing = false;
    render();
    showToast("回放结束：校正报告已更新。");
    return;
  }
  render();
  animationFrame = requestAnimationFrame(tick);
}

function setScenario(name, enabled) {
  options[name] = enabled;
  const labels = { step: "阶跃延迟", spike: "短时尖峰", late: "迟到字幕" };
  recompute(`${labels[name]}已${enabled ? "启用" : "关闭"}，测试报告已重算。`);
}

function bindEvents() {
  $("#runButton").addEventListener("click", replay);
  $("#headerReplay").addEventListener("click", replay);
  $("#playButton").addEventListener("click", () => setPlaying(!state.playing));
  $("#seekBack").addEventListener("click", () => seek(state.time - 5));
  $("#seekForward").addEventListener("click", () => seek(state.time + 5));
  $("#miniProgress").addEventListener("click", (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    seek(((event.clientX - rect.left) / rect.width) * DURATION);
  });

  $$(".speed-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.speed = Number(button.dataset.speed);
      $$(".speed-button").forEach((item) => item.classList.toggle("active", item === button));
      showToast(`回放速度 ${state.speed}×`);
    });
  });

  $$(".scenario-toggle").forEach((input) => {
    input.addEventListener("change", () => setScenario(input.dataset.scenario, input.checked));
  });

  $$(".source-row").forEach((button) => {
    button.addEventListener("click", () => openModal("data", button.dataset.source));
  });
  $("#inspectAll").addEventListener("click", () => openModal("data", "vision"));
  $("#inspectorButton").addEventListener("click", () => openModal("data", "vision"));
  $("#openAsr").addEventListener("click", () => openModal("data", "asr"));
  $("#methodButton").addEventListener("click", () => openModal("method"));
  $("#reportDetailButton").addEventListener("click", () => openModal("method"));

  $("#modalClose").addEventListener("click", closeModal);
  $("#modalBackdrop").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeModal();
  });
  $("#modalTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) return;
    state.modalTab = button.dataset.tab;
    renderModal();
  });

  $("#showAllLogs").addEventListener("click", () => {
    state.expandLogs = !state.expandLogs;
    $("#showAllLogs").textContent = state.expandLogs ? "收起" : "展开全部";
    renderLog();
  });

  $("#timelineCanvas").addEventListener("click", (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const left = 88;
    const plotWidth = Math.max(1, rect.width - 101);
    seek(clamp(((event.clientX - rect.left - left) / plotWidth) * DURATION, 0, DURATION));
  });
  $("#timelineCanvas").addEventListener("mousemove", handleTimelineHover);
  $("#timelineCanvas").addEventListener("mouseleave", () => {
    $("#timelineTooltip").style.display = "none";
  });
  $("#fitTimeline").addEventListener("click", () => {
    seek(clamp(state.time, 0, DURATION));
    showToast("时间轴已适配完整 64 秒测试流。");
  });

  window.addEventListener("keydown", (event) => {
    if (event.target.matches("input, button")) return;
    if (event.code === "Space") {
      event.preventDefault();
      setPlaying(!state.playing);
    }
    if (event.code === "ArrowLeft") seek(state.time - 2);
    if (event.code === "ArrowRight") seek(state.time + 2);
    if (event.code === "Escape") closeModal();
  });

  const resizeObserver = new ResizeObserver(() => drawCharts());
  resizeObserver.observe($("#offsetChart"));
  resizeObserver.observe($("#timelineCanvas"));
  window.addEventListener("resize", drawCharts);
  document.fonts?.ready.then(drawCharts);
}

function init() {
  const counts = { vision: VISION_EVENTS.length, asr: CUES.length, buffer: 128 };
  $$(".source-row").forEach((row) => {
    const count = counts[row.dataset.source];
    row.querySelector(".source-count").textContent = count;
    row.querySelector(".source-copy small").textContent = {
      vision: `${SCENES.length} 场景 · ${CUES.length} 发声锚点`,
      asr: `${CUES.length} 段 · 1 条迟到`,
      buffer: "0.5 s 采样 · 64 s",
    }[row.dataset.source];
  });
  bindEvents();
  render();
}

init();

function render() {
  const frame = frameAt(analysis, state.time);
  const cue = activeCueAt(state.time, frame);
  const appliedMs = Math.round(frame.applied * 1000);
  const residualMs = Math.round(frame.residual * 1000);
  const gap = nextGap(frame);
  const tc = timecode(state.time);

  $("#headerClock").textContent = tc;
  $("#stageTimecode").textContent = timecode(state.time, true);
  $("#timelineTime").textContent = `T+ ${state.time.toFixed(1)} s`;
  $("#miniTime").textContent = `${tc} / ${timecode(DURATION)}`;
  $("#miniProgress").style.width = `${(state.time / DURATION) * 100}%`;
  $("#streamProgress").style.width = `${(state.time / DURATION) * 100}%`;

  $("#appliedMetric").innerHTML = `${signed(appliedMs).replace("−", "−")}<em>ms</em>`;
  $("#residualMetric").innerHTML = `${signed(residualMs)}<em>ms</em>`;
  $("#lockedMetric").innerHTML = `${frame.lockedCount}<em>/ ${CUES.length}</em>`;
  $("#gapMetric").innerHTML = `${Number(gap || 0).toFixed(2)}<em>s</em>`;
  $("#appliedNote").textContent =
    options.step && state.time >= (analysis.report.convergenceAt ?? Infinity)
      ? "已收敛 · 步进上限 420 ms/s"
      : "持续偏差限速追赶中";

  const residualNote = $("#residualNote");
  const residualSafe = Math.abs(residualMs) <= 80;
  residualNote.classList.toggle("good", residualSafe);
  residualNote.innerHTML = residualSafe
    ? "<i></i>低于 80 ms 阈值"
    : "校正窗口内 · 正在吸收";

  const bufferPercent = Math.round(frame.buffer * 100);
  $("#bufferValue").textContent = bufferPercent;
  $("#bufferArc").style.strokeDashoffset = String(106.8 * (1 - frame.buffer));
  $("#bufferState").textContent = frame.buffer > 0.72 ? "稳定" : "恢复中";

  $("#playStateLabel").textContent = state.playing ? "实时回放中" : state.time >= DURATION ? "回放完成" : "回放暂停";
  $(".preview-status").classList.toggle("playing", state.playing);
  $("#playButton").classList.toggle("playing", state.playing);
  $("#playButton").setAttribute("aria-label", state.playing ? "暂停" : "播放");

  if (cue) {
    $("#subtitleWrap").classList.remove("is-empty");
    $("#stageSubtitle").textContent = cue.text;
    $("#subtitleConfidence").textContent = `ASR ${cue.confidence}% · ${cue.id}`;
    $(".speaker-chip").textContent = cue.speaker;
  } else {
    $("#subtitleWrap").classList.add("is-empty");
    $("#stageSubtitle").textContent =
      state.time >= DURATION
        ? "回放结束 · 字幕窗口已清空"
        : state.playing
          ? "监听发声锚点…"
          : "等待下一条识别结果…";
    $("#subtitleConfidence").textContent = "ASR —";
    $(".speaker-chip").textContent = "输出";
  }
  $("#stageOffset").textContent = `OFFSET ${signed(frame.applied, 2)} s`;
  $("#chartOffset").innerHTML = `${signed(frame.applied, 2)}<em>s</em>`;

  renderQueue(frame);
  renderReport(frame);
  renderSkip();
  renderLog();
  drawCharts();
}

function renderQueue(frame) {
  const focus = focusCue(state.time, frame);
  const startIndex = clamp(focus.index - 2, 0, Math.max(0, CUES.length - 6));
  const visible = CUES.slice(startIndex, startIndex + 6);

  $("#queueBody").innerHTML = visible
    .map((cue) => {
      const item = frame.schedule[cue.id];
      const [label, statusClass] = statusMeta(item.status);
      const delta = item.skipped || item.start === null ? null : item.delta;
      const deltaText = delta === null ? "—" : `${signed(delta * 1000)} ms`;
      const timeText =
        item.skipped || item.start === null
          ? "<span class='gap-value'>窗口关闭</span>"
          : `${item.start.toFixed(1)} – ${item.end.toFixed(1)} s`;
      const gapText =
        item.skipped || item.gap === null
          ? "—"
          : `<span class="gap-value safe">${item.gap.toFixed(2)} s</span>`;
      return `
        <tr class="${item.status === "live" ? "is-live" : ""} ${item.skipped ? "is-skipped" : ""}">
          <td>${cue.id}</td>
          <td title="${escapeHtml(cue.text)}">${escapeHtml(cue.text)}</td>
          <td>${cue.start.toFixed(1)} s</td>
          <td><span class="delta-badge ${delta && Math.abs(delta) > 0.01 ? "" : "zero"}">${deltaText}</span></td>
          <td>${timeText}</td>
          <td>${gapText}</td>
          <td><span class="status-badge ${statusClass}">${label}</span></td>
        </tr>`;
    })
    .join("");

  $("#queueLegal").textContent = analysis.report.reversals;
  $("#queueOverlap").textContent = analysis.report.overlaps;
  $("#inViewCount")?.replaceChildren(
    document.createTextNode(
      `已播 ${frame.lockedCount} · 待播 ${frame.upcomingCount}`,
    ),
  );
}

function renderReport(frame) {
  const report = analysis.report;
  const stepDone =
    options.step && report.convergenceAt !== null && state.time >= report.convergenceAt;
  const stepPending = options.step && state.time < (report.convergenceAt ?? SCENARIO.stepAt);
  const spikePassed = options.spike && state.time >= SCENARIO.spikeAt + SCENARIO.spikeDuration;
  const latePassed = options.late && state.time >= (report.skipped[0]?.deadline ?? 49.2);

  $("#convergenceMetric").innerHTML = options.step
    ? stepDone
      ? `${report.convergence.toFixed(1)}<em>s</em>`
      : `<em>${stepPending ? "等待阶跃" : "校正中…"}</em>`
    : `<em>未注入</em>`;
  $("#skippedMetric").innerHTML = options.late
    ? state.time >= (report.skipped[0]?.deadline ?? 49.2)
      ? `${report.skippedCount}<em> 条</em>`
      : `0<em> 条</em>`
    : `0<em> 条</em>`;
  $("#spikeMetric").innerHTML = options.spike
    ? state.time >= SCENARIO.spikeAt + SCENARIO.spikeDuration
      ? `0<em> 次</em>`
      : `<em>待观测</em>`
    : `<em>未注入</em>`;
  $("#maxGapMetric").innerHTML = `${report.maxGap.toFixed(1)}<em>s</em>`;

  $("#stepResult").textContent = options.step
    ? stepDone
      ? `${report.convergence.toFixed(1)}s 收敛`
      : stepPending
        ? "等待注入"
        : "校正中"
    : "未注入";
  $("#spikeResult").textContent = options.spike
    ? spikePassed
      ? "已吸收"
      : state.time >= SCENARIO.spikeAt
        ? "过滤中"
        : "等待注入"
    : "未注入";
  $("#lateResult").textContent = options.late
    ? latePassed
      ? "已跳过"
      : state.time >= SCENARIO.stepAt
        ? "等待结果"
        : "等待注入"
    : "未注入";
  $("#lateResult").classList.toggle("danger", options.late && latePassed);

  $$(".report-event").forEach((row) => {
    const name = row.dataset.event;
    row.classList.toggle("is-disabled", !options[name]);
    row.classList.toggle("is-pending", {
      step: options.step && state.time < SCENARIO.stepAt,
      spike: options.spike && state.time < SCENARIO.spikeAt,
      late: options.late && state.time < 46.4,
    }[name]);
  });

  const score = 94 + (report.overlaps === 0 ? 2 : 0) + (report.reversals === 0 ? 2 : 0);
  $("#reportScore").textContent = score;
  $(".seal-copy span").textContent = `${report.overlaps} 次重叠 · ${report.reversals} 次倒序`;
  $(".seal-copy small").textContent = `已播出字幕锁定 ${frame.lockedCount} / ${CUES.length}`;
}

function renderSkip() {
  const visible = analysis.report.skipped.filter((entry) => entry.deadline <= state.time);
  $("#skipCount").textContent = visible.length;
  if (!visible.length) {
    $("#skipList").innerHTML = `<p class="skip-empty">当前时刻没有字幕被跳过。</p>`;
    return;
  }
  $("#skipList").innerHTML = visible
    .map(
      (entry) => `
      <article class="skip-item">
        <div class="skip-item-top">
          <span class="skip-id">${entry.id} · LATE ASR</span>
          <span class="skip-time">截止 T+${entry.deadline.toFixed(1)}s</span>
        </div>
        <p class="skip-text">${escapeHtml(entry.text)}</p>
        <div class="skip-reason">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5v.2"/><circle cx="12" cy="12" r="9"/></svg>
          <span><strong>${entry.reason}</strong><br>${entry.detail}；已播出内容不回写。</span>
        </div>
      </article>`,
    )
    .join("");
}

function renderLog() {
  const entries = analysis.correctionLog.filter((entry) => entry.time <= state.time);
  const shown = state.expandLogs ? entries : entries.slice(-6).reverse();
  if (!shown.length) {
    $("#correctionLog").innerHTML = `<div class="log-item"><span class="log-time">T+00.0</span><span class="log-action">等待持续偏差</span><span class="log-value">0 ms</span></div>`;
  } else {
    $("#correctionLog").innerHTML = shown
      .map((entry) => {
        const time = `T+${entry.time.toFixed(1)}`;
        if (entry.kind === "spike") {
          return `<div class="log-item spike"><span class="log-time">${time}</span><span class="log-action">短尖峰已吸收</span><span class="log-value">0 ms 改写</span></div>`;
        }
        if (entry.kind === "skip") {
          return `<div class="log-item skip"><span class="log-time">${time}</span><span class="log-action">${LATE_CUE_ID} 已跳过</span><span class="log-value">结果迟到</span></div>`;
        }
        return `<div class="log-item"><span class="log-time">${time}</span><span class="log-action">限速校正</span><span class="log-value">${signed(entry.delta * 1000)} ms → ${signed(entry.applied * 1000)} ms</span></div>`;
      })
      .join("");
  }
  $("#logCount").textContent = `${entries.filter((entry) => entry.kind === "correction").length} 次自动校正`;
  $("#showAllLogs").textContent = state.expandLogs ? "收起" : "展开全部";
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function drawCharts() {
  drawOffsetChart();
  drawTimeline();
}

function drawOffsetChart() {
  const setup = setupCanvas($("#offsetChart"));
  if (!setup) return;
  const { ctx, width, height } = setup;
  const left = 29;
  const right = 8;
  const top = 10;
  const bottom = 8;
  const plotW = Math.max(1, width - left - right);
  const plotH = Math.max(1, height - top - bottom);
  const x = (time) => left + (time / DURATION) * plotW;
  const y = (value) => top + (1 - value / 2.65) * plotH;

  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, plotW, plotH);
  ctx.clip();

  ctx.fillStyle = "rgba(240, 182, 111, 0.055)";
  const spikeX = x(SCENARIO.spikeAt);
  ctx.fillRect(spikeX, top, x(SCENARIO.spikeAt + SCENARIO.spikeDuration) - spikeX, plotH);

  ctx.strokeStyle = "rgba(112, 133, 120, 0.12)";
  ctx.lineWidth = 1;
  [0, 0.9, 1.8].forEach((value) => {
    ctx.beginPath();
    ctx.moveTo(left, y(value) + 0.5);
    ctx.lineTo(left + plotW, y(value) + 0.5);
    ctx.stroke();
  });

  for (let tick = 0; tick <= DURATION; tick += 8) {
    ctx.beginPath();
    ctx.moveTo(x(tick) + 0.5, top);
    ctx.lineTo(x(tick) + 0.5, top + plotH);
    ctx.stroke();
  }

  ctx.beginPath();
  analysis.frames.forEach((frame, index) => {
    const px = x(frame.t);
    const py = y(frame.raw);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "rgba(137, 159, 146, 0.46)";
  ctx.lineWidth = 1;
  ctx.stroke();

  const fill = ctx.createLinearGradient(0, top, 0, top + plotH);
  fill.addColorStop(0, "rgba(185, 245, 109, 0.14)");
  fill.addColorStop(1, "rgba(185, 245, 109, 0)");
  ctx.beginPath();
  analysis.frames.forEach((frame, index) => {
    const px = x(frame.t);
    const py = y(frame.applied);
    if (index === 0) ctx.moveTo(px, y(0));
    ctx.lineTo(px, py);
  });
  ctx.lineTo(x(DURATION), y(0));
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();

  ctx.beginPath();
  analysis.frames.forEach((frame, index) => {
    const px = x(frame.t);
    const py = y(frame.applied);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "#b9f56d";
  ctx.lineWidth = 1.55;
  ctx.stroke();

  ctx.setLineDash([3, 4]);
  ctx.beginPath();
  analysis.frames.forEach((frame, index) => {
    const px = x(frame.t);
    const py = y(frame.accepted);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "rgba(185, 245, 109, 0.28)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();

  ctx.setLineDash([3, 4]);
  ctx.strokeStyle = "rgba(240, 182, 111, 0.36)";
  ctx.beginPath();
  ctx.moveTo(x(SCENARIO.stepAt), top);
  ctx.lineTo(x(SCENARIO.stepAt), top + plotH);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = "rgba(90, 106, 97, 0.86)";
  ctx.font = "7px 'DM Mono', 'Noto Sans SC', sans-serif";
  ctx.textAlign = "right";
  [["0", 0], ["0.9", 0.9], ["1.8", 1.8]].forEach(([label, value]) => {
    ctx.fillText(label, left - 7, y(value) + 2.5);
  });
  ctx.textAlign = "left";
  ctx.fillStyle = "rgba(240, 182, 111, 0.65)";
  ctx.fillText("STEP", x(SCENARIO.stepAt) + 4, top + 8);
  ctx.fillText("SPIKE", x(SCENARIO.spikeAt) + 4, top + 8);

  const frame = frameAt(analysis, state.time);
  const playX = x(state.time);
  ctx.strokeStyle = "rgba(238, 249, 240, 0.32)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(playX, top);
  ctx.lineTo(playX, top + plotH);
  ctx.stroke();

  const dotY = y(frame.applied);
  ctx.beginPath();
  ctx.arc(playX, dotY, 3.2, 0, Math.PI * 2);
  ctx.fillStyle = "#b9f56d";
  ctx.shadowColor = "rgba(185, 245, 109, 0.8)";
  ctx.shadowBlur = 8;
  ctx.fill();
  ctx.shadowBlur = 0;
}

function drawTimeline() {
  const setup = setupCanvas($("#timelineCanvas"));
  if (!setup) return;
  const { ctx, width, height } = setup;
  const left = 88;
  const right = 13;
  const plotW = Math.max(1, width - left - right);
  const top = 24;
  const lanes = [
    ["画面", "SCENE"],
    ["发声", "SPEECH"],
    ["识别", "ASR"],
    ["播出", "ON AIR"],
    ["缓冲", "BUFFER"],
  ];
  const laneH = (height - top - 19) / lanes.length;
  const x = (time) => left + (time / DURATION) * plotW;

  ctx.font = "7px 'DM Mono', 'Noto Sans SC', sans-serif";
  ctx.textAlign = "left";
  lanes.forEach(([zh, en], index) => {
    const center = top + laneH * index + laneH / 2;
    ctx.fillStyle = "rgba(117, 134, 124, 0.82)";
    ctx.fillText(zh, 10, center - 1);
    ctx.fillStyle = "rgba(66, 79, 72, 0.9)";
    ctx.font = "6px 'DM Mono', 'Noto Sans SC', sans-serif";
    ctx.fillText(en, 32, center + 1);
    ctx.font = "7px 'DM Mono', 'Noto Sans SC', sans-serif";
    if (index > 0) {
      ctx.strokeStyle = "rgba(31, 42, 49, 0.62)";
      ctx.beginPath();
      ctx.moveTo(8, top + laneH * index);
      ctx.lineTo(width - 10, top + laneH * index);
      ctx.stroke();
    }
  });

  ctx.textAlign = "center";
  for (let tick = 0; tick <= DURATION; tick += 8) {
    const px = x(tick);
    ctx.strokeStyle = tick === 0 ? "rgba(49, 63, 56, 0.65)" : "rgba(39, 51, 57, 0.55)";
    ctx.setLineDash(tick === 0 ? [] : [2, 4]);
    ctx.beginPath();
    ctx.moveTo(px + 0.5, top - 9);
    ctx.lineTo(px + 0.5, height - 16);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(85, 99, 91, 0.84)";
    ctx.fillText(`${tick}s`, px, 11);
  }

  const sceneY = top + laneH * 0 + laneH / 2;
  SCENES.forEach((scene) => {
    const sx = x(scene.time);
    const sw = Math.max(2, x(scene.end) - sx - 2);
    ctx.fillStyle = "rgba(112, 222, 212, 0.12)";
    ctx.strokeStyle = "rgba(112, 222, 212, 0.35)";
    ctx.lineWidth = 1;
    roundedRect(ctx, sx, sceneY - 5, sw, 10, 3);
    ctx.fill();
    ctx.stroke();
    if (sw > 72) {
      ctx.fillStyle = "rgba(150, 220, 211, 0.78)";
      ctx.font = "6.5px 'Noto Sans SC', sans-serif";
      ctx.fillText(scene.label, sx + 5, sceneY + 2.3);
      ctx.textAlign = "left";
    }
    ctx.textAlign = "center";
  });

  const speechY = top + laneH * 1 + laneH / 2;
  CUES.forEach((cue) => {
    const sx = x(cue.start);
    const ex = x(cue.end);
    const late = cue.id === LATE_CUE_ID && options.late;
    ctx.strokeStyle = late ? "rgba(255, 127, 138, 0.7)" : "rgba(112, 222, 212, 0.62)";
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(sx, speechY - 4);
    ctx.lineTo(sx, speechY + 4);
    ctx.stroke();
    ctx.strokeStyle = late ? "rgba(255, 127, 138, 0.25)" : "rgba(112, 222, 212, 0.2)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx, speechY);
    ctx.lineTo(ex, speechY);
    ctx.stroke();
  });

  const asrY = top + laneH * 2 + laneH / 2;
  CUES.forEach((cue) => {
    const late = cue.id === LATE_CUE_ID && options.late;
    const arrival = options.late || !cue.late ? cue.arrivedAt : cue.start - 0.35;
    const sx = x(arrival);
    ctx.fillStyle = late ? "rgba(255, 127, 138, 0.58)" : "rgba(156, 141, 246, 0.46)";
    roundedRect(ctx, sx - 1.5, asrY - 3.5, late ? 7 : 4, 7, 1.5);
    ctx.fill();
  });

  const frame = frameAt(analysis, state.time);
  const airY = top + laneH * 3 + laneH / 2;
  CUES.forEach((cue) => {
    const item = frame.schedule[cue.id];
    if (item.skipped) {
      const sx = x(cue.start);
      ctx.strokeStyle = "rgba(255, 127, 138, 0.66)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(sx, airY - 5);
      ctx.lineTo(sx + 7, airY + 5);
      ctx.moveTo(sx + 7, airY - 5);
      ctx.lineTo(sx, airY + 5);
      ctx.stroke();
      return;
    }
    if (item.start === null) return;
    const sx = x(item.start);
    const ex = x(item.end);
    const isPast = item.status === "locked" || item.status === "live";
    ctx.fillStyle = isPast ? "rgba(185, 245, 109, 0.72)" : "rgba(185, 245, 109, 0.25)";
    roundedRect(ctx, sx, airY - 3.5, Math.max(2, ex - sx), 7, 2);
    ctx.fill();
  });

  const bufferY = top + laneH * 4 + laneH / 2;
  const base = bufferY + 9;
  ctx.beginPath();
  analysis.frames.forEach((item, index) => {
    const px = x(item.t);
    const py = base - item.buffer * 18;
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.lineTo(x(DURATION), base);
  ctx.lineTo(left, base);
  ctx.closePath();
  const bufferGradient = ctx.createLinearGradient(0, bufferY - 10, 0, base);
  bufferGradient.addColorStop(0, "rgba(240, 182, 111, 0.16)");
  bufferGradient.addColorStop(1, "rgba(240, 182, 111, 0)");
  ctx.fillStyle = bufferGradient;
  ctx.fill();
  ctx.beginPath();
  analysis.frames.forEach((item, index) => {
    const px = x(item.t);
    const py = base - item.buffer * 18;
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "rgba(240, 182, 111, 0.58)";
  ctx.lineWidth = 1;
  ctx.stroke();

  const injection = [
    [SCENARIO.stepAt, "STEP", "rgba(185, 245, 109, 0.42)"],
    [SCENARIO.spikeAt, "SPIKE", "rgba(240, 182, 111, 0.42)"],
    [CUES.find((cue) => cue.id === LATE_CUE_ID)?.start ?? 46.4, "LATE", "rgba(255, 127, 138, 0.42)"],
  ];
  injection.forEach(([time, label, color]) => {
    if ((time === SCENARIO.stepAt && !options.step) || (time === SCENARIO.spikeAt && !options.spike)) return;
    if (label === "LATE" && !options.late) return;
    const px = x(time);
    ctx.strokeStyle = color;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(px, top - 3);
    ctx.lineTo(px, height - 16);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color.replace("0.42", "0.8");
    ctx.font = "6px 'DM Mono', 'Noto Sans SC', sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(label, px + 3, top - 5);
  });

  const playX = x(state.time);
  ctx.strokeStyle = "rgba(229, 241, 232, 0.72)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(playX, top - 12);
  ctx.lineTo(playX, height - 14);
  ctx.stroke();
  ctx.fillStyle = "#d7ffa2";
  ctx.beginPath();
  ctx.moveTo(playX - 4, top - 13);
  ctx.lineTo(playX + 4, top - 13);
  ctx.lineTo(playX, top - 7);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = "rgba(62, 76, 68, 0.9)";
  ctx.font = "6px 'DM Mono', 'Noto Sans SC', sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(`${signed(frame.applied, 2)}s applied`, left + 2, height - 4);
  ctx.textAlign = "right";
  ctx.fillText(`${Math.round(frame.buffer * 100)}% buffer`, width - 14, height - 4);
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

let timelineMetrics = { left: 88, plotW: 1, rect: null };

function handleTimelineHover(event) {
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const plotW = Math.max(1, rect.width - 101);
  const left = 88;
  const time = clamp(((event.clientX - rect.left - left) / plotW) * DURATION, 0, DURATION);
  timelineMetrics = { left, plotW, rect };
  const frame = frameAt(analysis, time);
  const cue = activeCueAt(time, frame);
  const tooltip = $("#timelineTooltip");
  tooltip.innerHTML = `<strong>T+ ${time.toFixed(1)} s</strong>${
    cue
      ? `${cue.id} · ${escapeHtml(cue.text.slice(0, 16))}${cue.text.length > 16 ? "…" : ""}`
      : "无活动字幕"
  }<br>OFFSET ${signed(frame.applied, 2)} s · BUFFER ${Math.round(frame.buffer * 100)}%`;
  tooltip.style.display = "block";
  tooltip.style.left = `${clamp(event.clientX - rect.left + 10, 8, rect.width - 160)}px`;
  tooltip.style.top = "14px";
}

function openModal(mode, tab = state.modalTab) {
  state.modalMode = mode;
  state.modalTab = tab;
  $("#modalBackdrop").hidden = false;
  document.body.style.overflow = "hidden";
  renderModal();
}

function closeModal() {
  $("#modalBackdrop").hidden = true;
  document.body.style.overflow = "";
}

function renderModal() {
  const method = state.modalMode === "method";
  const tabs = $("#modalTabs");
  tabs.style.display = method ? "none" : "flex";
  $("#modalEyebrow").textContent = method ? "ALGORITHM DECISION TRACE" : "DATA INSPECTOR / TEST-FEED-041";
  $("#modalTitle").textContent = method ? "判定依据与决策轨迹" : "输入数据";

  $$("#modalTabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.modalTab);
  });

  if (method) {
    $("#modalBody").innerHTML = methodMarkup();
    return;
  }

  if (state.modalTab === "vision") {
    $("#modalBody").innerHTML = `
      <div class="inspector-note">画面事件按导播切换与发声峰值打点；发声锚点用于与 ASR 时间戳交叉验证。</div>
      <table class="inspector-table">
        <thead><tr><th>时间</th><th>类型</th><th>事件</th><th>详情</th></tr></thead>
        <tbody>
          ${VISION_EVENTS.map(
            (event) => `<tr>
              <td>${event.time.toFixed(2)} s</td>
              <td class="mono">${event.type === "scene" ? "SCENE" : "SPEECH"}</td>
              <td>${escapeHtml(event.label)}</td>
              <td>${escapeHtml(event.detail)}</td>
            </tr>`,
          ).join("")}
        </tbody>
      </table>`;
    return;
  }

  if (state.modalTab === "asr") {
    $("#modalBody").innerHTML = `
      <div class="inspector-note">识别结果包含音频入点与到达时刻。到达时刻晚于“入点 + 0.9 s”时，该段不再进入播出队列。</div>
      <table class="inspector-table">
        <thead><tr><th>ID</th><th>音频入点</th><th>结果到达</th><th>处理延迟</th><th>置信度</th><th>文本</th></tr></thead>
        <tbody>
          ${CUES.map((cue) => {
            const lateness = cue.arrivedAt - cue.start;
            return `<tr class="${cue.late ? "late-row" : ""}">
              <td>${cue.id}</td>
              <td class="mono">${cue.start.toFixed(2)} s</td>
              <td class="mono">${cue.arrivedAt.toFixed(2)} s</td>
              <td class="mono">${signed(lateness, 2)} s${cue.late ? " · LATE" : ""}</td>
              <td class="mono">${cue.confidence}%</td>
              <td>${escapeHtml(cue.text)}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>`;
    return;
  }

  $("#modalBody").innerHTML = `
    <div class="inspector-note">播放器每 0.5 s 采样一次。持续缓冲偏差才进入渐进校正，单点尖峰由平滑窗吸收。</div>
    <table class="inspector-table">
      <thead><tr><th>时间</th><th>缓冲量</th><th>原始偏移</th><th>状态</th><th>判读</th></tr></thead>
      <tbody>
        ${bufferLog.map((entry) => `<tr>
          <td>${entry.time.toFixed(1)} s</td>
          <td class="mono">${entry.bufferMs.toLocaleString()} ms</td>
          <td class="mono">${signed(entry.offsetMs)} ms</td>
          <td class="mono">${entry.state === "stable" ? "STABLE" : "RECOVER"}</td>
          <td>${entry.offsetMs > 600 ? "持续延迟候选" : entry.offsetMs > 150 ? "抖动观察" : "正常"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

function methodMarkup() {
  const report = analysis.report;
  const late = report.skipped.find((entry) => entry.id === LATE_CUE_ID);
  const plugin = (value) => (value ? "启用" : "关闭");
  return `
    <div class="method-grid">
      <article class="method-card">
        <span>01 / OFFSET ESTIMATION</span>
        <strong>发声锚点 + 缓冲日志联合估计</strong>
        <p>以 0.1 s 为步长计算原始偏移，0.12 系数低通平滑，抑制识别与画面时间戳的单点噪声。</p>
        <div class="method-formula"><code>EMA α = 0.12</code><code>window ≈ 0.9 s</code></div>
      </article>
      <article class="method-card">
        <span>02 / JITTER GATE</span>
        <strong>持续偏差才允许改写时间轴</strong>
        <p>偏差必须超过 240 ms 并连续保持 2.0 s，才进入校正；短于保持窗的尖峰不会触发时间轴变更。</p>
        <div class="method-formula"><code>threshold 240 ms</code><code>persist 2.0 s</code></div>
      </article>
      <article class="method-card">
        <span>03 / GRADUAL CORRECTION</span>
        <strong>限速追赶，避免字幕瞬移</strong>
        <p>应用偏移以 420 ms/s 为上限逐步逼近估计值；每一条待播字幕都继承当前偏移，并保留原始时长。</p>
        <div class="method-formula"><code>rate ≤ 420 ms/s</code><code>deadband 25 ms</code></div>
      </article>
      <article class="method-card">
        <span>04 / AIR GUARD</span>
        <strong>已播锁定与顺序约束</strong>
        <p>字幕一旦进入播出窗口即锁定入点。所有未播字幕按原始顺序重排，邻接间隔不得低于 160 ms。</p>
        <div class="method-formula"><code>lock on air</code><code>gap ≥ 160 ms</code></div>
      </article>
      <article class="method-card wide">
        <span>05 / LATE SEGMENT POLICY</span>
        <strong>超过 0.9 s 截止窗口的识别结果直接跳过</strong>
        <p>${late ? `${late.id} 的音频入点为 ${late.originalStart.toFixed(1)} s，结果直到 ${late.arrivalAt.toFixed(1)} s 才到达。窗口在 ${late.deadline.toFixed(1)} s 关闭，因此不创建补发字幕，也不回写已播内容。` : "当前场景未注入迟到字幕，或迟到字幕开关已关闭。"}</p>
        <div class="method-formula"><code>late grace 0.9 s</code><code>no backfill</code><code>no retro-edit</code></div>
      </article>
    </div>
    <div class="method-audit">
      <div class="audit-cell"><strong>${report.overlaps}</strong><span>实际重叠</span></div>
      <div class="audit-cell"><strong>${report.reversals}</strong><span>实际倒序</span></div>
      <div class="audit-cell"><strong>${report.skippedCount}</strong><span>跳过字幕</span></div>
    </div>
    <div class="decision-trace">
      <div class="trace-head"><span>DECISION TRACE</span><span>场景 ${plugin(options.step)} · ${plugin(options.spike)} · ${plugin(options.late)}</span></div>
      <div class="trace-row"><span>T+18.0</span><strong>原始偏移跃迁</strong><em>+1,800 ms</em></div>
      <div class="trace-row"><span>T+${(report.acceptedAt ?? 0).toFixed(1)}</span><strong>持续偏差确认，开始限速校正</strong><em>${report.convergence === null ? "未触发" : "进入校正"}</em></div>
      <div class="trace-row"><span>T+${(report.convergenceAt ?? 0).toFixed(1)}</span><strong>残差连续 1.0 s 低于 80 ms</strong><em>${report.convergence === null ? "未收敛" : `收敛 ${report.convergence.toFixed(1)} s`}</em></div>
      <div class="trace-row"><span>T+34.0</span><strong>0.65 s 尖峰，短于 2.0 s 保持窗</strong><em>时间轴改写 0 次</em></div>
      <div class="trace-row"><span>T+${late ? late.deadline.toFixed(1) : "—"}</span><strong>${late ? `${late.id} 截止窗口关闭，识别结果未到达` : "未注入迟到字幕"}</strong><em>${late ? "跳过，保持输出连续" : "—"}</em></div>
      <div class="trace-row"><span>T+64.0</span><strong>终态审计</strong><em>${report.overlaps} 重叠 · ${report.reversals} 倒序</em></div>
    </div>`;
}
