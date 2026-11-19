import { decodeEdges, parseEdgeCsv } from "./src/aes3.js";
import { downloadBlob, encodeWav24, errorsToCsv } from "./src/export.js";

const $ = (selector) => document.querySelector(selector);
const COLORS = {
  bg: "#080c11",
  grid: "rgba(128, 154, 174, 0.11)",
  gridStrong: "rgba(128, 154, 174, 0.2)",
  text: "#8193a1",
  dim: "#4b5b68",
  cyan: "#57dcff",
  green: "#a7ff63",
  amber: "#ffb64d",
  red: "#ff6372",
  orange: "#ff9f5a"
};

const state = {
  manifest: null,
  currentId: null,
  scenario: null,
  decoded: null,
  selectedTimeNs: 0,
  hoverTimeNs: null,
  loading: false
};

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3200);
}

function setLoading(loading) {
  state.loading = loading;
  document.body.classList.toggle("is-loading", loading);
  for (const button of document.querySelectorAll(".action-block button")) {
    button.disabled = loading;
  }
  if (loading) {
    $("#headerState").textContent = "ANALYZING";
  }
}

function scenarioButtons() {
  const deck = $("#scenarioDeck");
  deck.innerHTML = "";
  for (const [id, scenario] of Object.entries(state.manifest.scenarios)) {
    const button = document.createElement("button");
    button.className = "scenario-chip";
    button.dataset.scenario = id;
    button.innerHTML = `
      <span class="scenario-dot"></span>
      <span>${scenario.title}</span>
      <small>${scenario.summary.frameCount}F</small>
    `;
    button.addEventListener("click", () => loadScenario(id));
    if (scenario.summary.parityErrorCount || scenario.summary.dropoutCount) {
      button.classList.add("faulty");
    } else if (scenario.summary.jitterCount) {
      button.classList.add("warning");
    }
    deck.appendChild(button);
  }
}

function markActiveScenario(id) {
  for (const button of document.querySelectorAll(".scenario-chip")) {
    button.classList.toggle("active", button.dataset.scenario === id);
  }
}

function chooseInitialTime(result) {
  if (!result.errors.length) {
    return Math.min(result.summary.duration * 0.32, result.edges.at(-1).timeNs) * 1e9;
  }
  const error = result.errors[0];
  if (error.type === "DROPOUT") {
    return Math.min(result.summary.duration, error.end_s + 0.002) * 1e9;
  }
  if (error.type === "CLOCK_JITTER") {
    return ((error.time_s + (error.end_s || error.time_s)) / 2) * 1e9;
  }
  return error.time_s * 1e9;
}

async function loadScenario(id) {
  if (state.loading) return;
  const scenario = state.manifest.scenarios[id];
  if (!scenario) return;
  setLoading(true);
  markActiveScenario(id);
  try {
    const response = await fetch(`data/scenarios/${scenario.file}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`无法读取 ${scenario.file}`);
    const { edges } = parseEdgeCsv(await response.text());
    const decoded = decodeEdges(edges);
    state.currentId = id;
    state.scenario = scenario;
    state.decoded = decoded;
    state.selectedTimeNs = chooseInitialTime(decoded);
    state.hoverTimeNs = null;
    $("#footerFile").textContent = `data/scenarios/${scenario.file}`;
    renderInterface();
    showToast(`${scenario.title} · ${decoded.summary.edgeCount.toLocaleString()} 边沿解码完成`);
  } catch (error) {
    console.error(error);
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function loadUploadedFile(file) {
  if (!file || state.loading) return;
  setLoading(true);
  try {
    const { edges } = parseEdgeCsv(await file.text());
    const decoded = decodeEdges(edges);
    state.currentId = "upload";
    state.scenario = {
      title: file.name.replace(/\.csv$/i, ""),
      file: file.name,
      validation: null,
      faults: null,
      summary: decoded.summary,
      tones: null
    };
    state.decoded = decoded;
    state.selectedTimeNs = chooseInitialTime(decoded);
    state.hoverTimeNs = null;
    markActiveScenario(null);
    $("#footerFile").textContent = file.name;
    renderInterface();
    showToast(`${file.name} · 自动恢复 ${Math.round(decoded.summary.bitClock / 1000)} kHz 位时钟`);
  } catch (error) {
    console.error(error);
    showToast(`导入失败：${error.message}`, true);
  } finally {
    setLoading(false);
  }
}

function formatPcm(value) {
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function hex24(value) {
  return `0x${(Number(value) & 0xffffff).toString(16).padStart(6, "0").toUpperCase()}`;
}

function formatMs(ns, digits = 3) {
  return `${(ns / 1e6).toFixed(digits)} ms`;
}

function formatFrequency(hz) {
  return hz >= 1000
    ? `${(hz / 1000).toFixed(3)} kHz`
    : `${hz.toFixed(3)} Hz`;
}

function selectedFrame() {
  const decoded = state.decoded;
  if (!decoded) return null;
  const index = Math.floor((state.selectedTimeNs - decoded.originNs) / decoded.framePeriodNs);
  return decoded.frames[Math.max(0, Math.min(decoded.frames.length - 1, index))];
}

function subframesForFrame(frameIndex) {
  return state.decoded.subframes.filter((subframe) => subframe.frameIndex === frameIndex);
}

function updateKpis() {
  const result = state.decoded;
  const summary = result.summary;
  const hasLoss = summary.lockPercent < 99.999;
  $("#headerState").textContent = hasLoss ? "RELOCKED" : "LOCKED";
  $("#lockValue").textContent = hasLoss ? "RELOCK" : "LOCKED";
  $("#lockPercent").textContent = `${summary.lockPercent.toFixed(2)}%`;
  $("#lockMeter").style.width = `${summary.lockPercent}%`;
  $("#bitClockValue").innerHTML = `${(summary.bitClock / 1000).toFixed(3)}<span>kHz</span>`;
  $("#uiValue").textContent = `${(result.unitNs / 1000).toFixed(3)} µs`;
  $("#sampleRateValue").textContent = formatFrequency(summary.sampleRate);
  $("#frameValue").innerHTML = `${summary.frameCount}<span>/${summary.totalFrameSlots}</span>`;
  $("#subframeValue").textContent = summary.subframeCount;
  $("#edgeValue").textContent = summary.edgeCount.toLocaleString();
  $("#jitterValue").innerHTML = `${summary.maxJitterUI.toFixed(3)}<span>UI</span>`;
  $("#jitterEvents").textContent = summary.jitterCount;
  $("#parityCount").textContent = summary.parityErrorCount;
  $("#dropoutCount").textContent = summary.dropoutCount;
  const faultCount = summary.parityErrorCount + summary.dropoutCount + summary.jitterCount;
  $("#faultValue").textContent = String(faultCount).padStart(2, "0");
  $("#faultCard").classList.toggle("warning", summary.jitterCount > 0 && summary.parityErrorCount + summary.dropoutCount === 0);
  $("#faultCard").classList.toggle("error", summary.parityErrorCount + summary.dropoutCount > 0);
  $("#eyeRms").textContent = `${summary.jitterRmsUI.toFixed(3)} UI`;
  $("#eyePeak").textContent = `${summary.maxJitterUI.toFixed(3)} UI`;
  $("#eyeLock").textContent = `${summary.lockPercent.toFixed(2)}%`;
  const eyeStatus = $("#eyeStatus");
  eyeStatus.textContent = summary.jitterCount ? "JITTER" : hasLoss ? "GAPPED" : "OPEN";
  eyeStatus.className = "mini-status";
  if (summary.jitterCount) eyeStatus.classList.add("warning");
  if (hasLoss) eyeStatus.classList.add("warning");
  $("#timelineStart").textContent = formatMs(result.edges[0].timeNs);
  $("#timelineEnd").textContent = formatMs(result.edges.at(-1).timeNs);
}

function updateInspector() {
  const frame = selectedFrame();
  if (!frame) return;
  const subs = subframesForFrame(frame.frameIndex);
  const left = subs.find((item) => item.channel === "A");
  const right = subs.find((item) => item.channel === "B");
  $("#selectionBadge").textContent = `FRAME ${String(frame.frameIndex).padStart(3, "0")} · ${formatMs(state.decoded.originNs + frame.frameIndex * state.decoded.framePeriodNs)}`;
  $("#frameMeta").textContent = `64 UI · ${(state.decoded.framePeriodNs / 1000).toFixed(3)} µs`;
  fillChannel("left", left, frame.left, frame.leftPresent);
  fillChannel("right", right, frame.right, frame.rightPresent);
  $("#sampleIndex").textContent = `#${String(frame.frameIndex).padStart(3, "0")}`;
  $("#sampleLeft").textContent = frame.leftPresent ? formatPcm(frame.left) : "—";
  $("#sampleRight").textContent = frame.rightPresent ? formatPcm(frame.right) : "—";
  $("#sampleHex").textContent = `${hex24(frame.left)} / ${hex24(frame.right)}`;
  $("#transportFill").style.width = `${(100 * frame.frameIndex) / Math.max(1, state.decoded.frames.length - 1)}%`;
}

function fillChannel(side, subframe, pcm, present) {
  $(`#${side}Preamble`).textContent = subframe?.id || "—";
  $(`#${side}Pcm`).textContent = present ? formatPcm(pcm) : "NO DATA";
  $(`#${side}Detail`).textContent = present
    ? `${hex24(pcm)} · slot 04–27`
    : "前导缺失 / 失锁";
  const flags = [];
  if (!subframe) {
    flags.push(`<span class="flag hot">LOST</span>`);
  } else {
    flags.push(`<span class="flag ${subframe.validity ? "hot" : "ok"}">V${subframe.validity ? 1 : 0}</span>`);
    flags.push(`<span class="flag">U${subframe.user}</span>`);
    flags.push(`<span class="flag">C${subframe.channelStatus}</span>`);
    flags.push(`<span class="flag ${subframe.parityError ? "hot" : "ok"}">P${subframe.parityError ? "!" : "✓"}</span>`);
  }
  $(`#${side}Flags`).innerHTML = flags.join("");
  $(`#${side}ChannelCard`).classList.toggle("channel-lost", !present);
}

function timeFromPointer(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const left = canvas === $("#timelineCanvas") ? 44 : 34;
  const right = 18;
  const fraction = (event.clientX - rect.left - left) / Math.max(1, rect.width - left - right);
  const start = state.decoded.edges[0].timeNs;
  const end = state.decoded.edges.at(-1).timeNs;
  return start + Math.max(0, Math.min(1, fraction)) * (end - start);
}

function showHover(event, canvas) {
  if (!state.decoded) return;
  const time = timeFromPointer(event, canvas);
  state.hoverTimeNs = time;
  const frameIndex = Math.max(0, Math.min(
    state.decoded.frames.length - 1,
    Math.floor((time - state.decoded.originNs) / state.decoded.framePeriodNs)
  ));
  const frame = state.decoded.frames[frameIndex];
  const readout = $("#hoverReadout");
  readout.textContent = `${formatMs(time)} · F${String(frameIndex).padStart(3, "0")} · L ${frame.leftPresent ? formatPcm(frame.left) : "LOST"} · R ${frame.rightPresent ? formatPcm(frame.right) : "LOST"}`;
  readout.style.opacity = "1";
  readout.style.left = `${Math.max(44, Math.min(canvas.getBoundingClientRect().width - 280, event.clientX - canvas.getBoundingClientRect().left + 10))}px`;
  if (!showHover.frame) {
    showHover.frame = requestAnimationFrame(() => {
      showHover.frame = null;
      drawTimeline();
      drawAudio();
    });
  }
}

function clearHover() {
  state.hoverTimeNs = null;
  $("#hoverReadout").style.opacity = "0";
  drawTimeline();
  drawAudio();
}

function updateErrorList() {
  const list = $("#errorList");
  const errors = state.decoded.errors;
  $("#errorCountBadge").textContent = errors.length;
  $("#errorCountBadge").classList.toggle("zero", errors.length === 0);
  list.innerHTML = "";
  if (!errors.length) {
    list.innerHTML = `
      <div class="empty-ledger">
        <span class="empty-check">✓</span>
        <span>NO EVENTS · CLEAN LOCK</span>
      </div>`;
    return;
  }
  for (const error of errors) {
    const button = document.createElement("button");
    button.className = `error-row ${error.severity}`;
    const marker = error.severity === "warning" ? "!" : "×";
    const end = error.end_s ? ` → ${(error.end_s * 1000).toFixed(3)}` : "";
    button.innerHTML = `
      <span class="error-severity">${marker}</span>
      <span class="error-main">
        <span class="error-title">${error.id} · ${error.title}</span>
        <span class="error-detail">${error.detail}</span>
      </span>
      <span class="error-time">${(error.time_s * 1000).toFixed(3)}${end} ms</span>
    `;
    button.addEventListener("click", () => {
      state.selectedTimeNs = ((error.time_s + (error.end_s || error.time_s)) / 2) * 1e9;
      renderInterface();
    });
    list.appendChild(button);
  }
}

function updateValidation() {
  const validation = state.scenario?.validation;
  const list = $("#qaList");
  if (!validation) {
    $("#qaScore").textContent = "LIVE";
    list.innerHTML = `
      <div class="qa-item">
        <span class="qa-icon">i</span>
        <span class="qa-copy">
          <span class="qa-label">导入序列实时分析</span>
          <span class="qa-detail">${state.decoded.summary.subframeCount} 个子帧通过前导同步与奇偶校验</span>
        </span>
      </div>`;
    return;
  }
  const passed = validation.checks.filter((check) => check.pass).length;
  $("#qaScore").textContent = `${passed}/${validation.checks.length}`;
  list.innerHTML = validation.checks.map((check) => `
    <div class="qa-item ${check.pass ? "" : "fail"}">
      <span class="qa-icon">${check.pass ? "✓" : "×"}</span>
      <span class="qa-copy">
        <span class="qa-label">${check.label}</span>
        <span class="qa-detail">${check.detail}</span>
      </span>
    </div>
  `).join("");
}

function updateStatusPanel() {
  const decoded = state.decoded;
  $("#statusALabel").textContent = `${decoded.channelStatus.A.label} / LEFT`;
  $("#statusBLabel").textContent = `${decoded.channelStatus.B.label} / RIGHT`;
  $("#statusBytes").textContent = decoded.channelStatus.A.hex;
  $("#validityValue").textContent = decoded.summary.invalidSamples;
  $("#channelMap").textContent = "A=L · B=R";
}

function renderInterface() {
  if (!state.decoded) return;
  updateKpis();
  updateInspector();
  updateErrorList();
  updateValidation();
  updateStatusPanel();
  drawTimeline();
  drawDetail();
  drawAudio();
  drawEye();
}

function setSelectedFromEvent(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const left = canvas === $("#timelineCanvas") ? 44 : 34;
  const right = canvas === $("#timelineCanvas") ? 18 : 18;
  const fraction = (x - left) / Math.max(1, rect.width - left - right);
  const decoded = state.decoded;
  const start = decoded.edges[0].timeNs;
  const end = decoded.edges.at(-1).timeNs;
  state.selectedTimeNs = start + Math.max(0, Math.min(1, fraction)) * (end - start);
  state.hoverTimeNs = state.selectedTimeNs;
  renderInterface();
}

async function initialize() {
  try {
    const response = await fetch("data/scenarios/manifest.json", { cache: "no-store" });
    if (!response.ok) throw new Error("场景清单缺失，请先运行 npm run generate");
    state.manifest = await response.json();
    scenarioButtons();
    $("#importButton").addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (event) => {
      loadUploadedFile(event.target.files?.[0]);
      event.target.value = "";
    });
    $("#timelineCanvas").addEventListener("click", (event) => setSelectedFromEvent(event, event.currentTarget));
    $("#audioCanvas").addEventListener("click", (event) => setSelectedFromEvent(event, event.currentTarget));
    $("#timelineCanvas").addEventListener("mousemove", (event) => showHover(event, event.currentTarget));
    $("#audioCanvas").addEventListener("mousemove", (event) => showHover(event, event.currentTarget));
    $("#timelineCanvas").addEventListener("mouseleave", clearHover);
    $("#audioCanvas").addEventListener("mouseleave", clearHover);
    $("#exportWavButton").addEventListener("click", exportWav);
    $("#exportErrorsButton").addEventListener("click", exportErrorCsv);
    window.addEventListener("resize", renderInterface);
    await loadScenario("clean");
  } catch (error) {
    console.error(error);
    showToast(error.message, true);
    $("#scenarioDeck").textContent = "FIXTURE ERROR";
  }
}

function exportWav() {
  const decoded = state.decoded;
  if (!decoded) return;
  const wav = encodeWav24(decoded.frames, Math.round(decoded.summary.sampleRate));
  const name = `aes3-${state.currentId || "decoded"}-${Math.round(decoded.summary.sampleRate)}hz-24bit.wav`;
  downloadBlob(name, new Blob([wav], { type: "audio/wav" }));
  showToast(`已导出 ${name}`);
}

function exportErrorCsv() {
  if (!state.decoded) return;
  const csv = errorsToCsv(state.decoded.errors);
  const name = `aes3-${state.currentId || "decoded"}-errors.csv`;
  downloadBlob(name, new Blob([csv], { type: "text/csv;charset=utf-8" }));
  showToast(`已导出 ${name}`);
}

function prepared(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, width, height);
  return { ctx, width, height };
}

function wrapNs(value, period) {
  return value - Math.round(value / period) * period;
}

function levelAt(timeNs) {
  const edges = state.decoded.edges;
  let low = 0;
  let high = edges.length - 1;
  let result = edges[0].level;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (edges[middle].timeNs <= timeNs) {
      result = edges[middle].level;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return result;
}

function drawTimeline() {
  const canvas = $("#timelineCanvas");
  const { ctx, width, height } = prepared(canvas);
  const decoded = state.decoded;
  const start = decoded.edges[0].timeNs;
  const end = decoded.edges.at(-1).timeNs;
  const span = Math.max(1, end - start);
  const left = 44;
  const right = width - 18;
  const top = 18;
  const plotBottom = height - 29;
  const xFor = (time) => left + ((time - start) / span) * (right - left);

  ctx.font = "8px " + getComputedStyle(document.documentElement).getPropertyValue("--mono");
  ctx.textBaseline = "middle";

  for (const event of decoded.jitter) {
    const x0 = xFor(event.startNs);
    const x1 = xFor(event.endNs);
    ctx.fillStyle = "rgba(255, 182, 77, 0.075)";
    ctx.fillRect(x0, top, Math.max(1, x1 - x0), plotBottom - top);
    ctx.fillStyle = "rgba(255, 182, 77, 0.42)";
    ctx.fillRect(x0, top, 1, plotBottom - top);
  }
  for (const event of decoded.dropouts) {
    const x0 = xFor(event.startNs);
    const x1 = xFor(event.endNs);
    ctx.fillStyle = "rgba(255, 99, 114, 0.12)";
    ctx.fillRect(x0, top, Math.max(2, x1 - x0), plotBottom - top);
    ctx.fillStyle = "rgba(255, 99, 114, 0.75)";
    ctx.fillRect(x0, top, 1, plotBottom - top);
    ctx.fillRect(x1, top, 1, plotBottom - top);
    ctx.fillStyle = "#ff8c98";
    ctx.font = "7px " + getComputedStyle(document.documentElement).getPropertyValue("--mono");
    ctx.fillText("LOS", x0 + 4, top + 9);
  }

  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  for (let tick = 0; tick <= 8; tick += 1) {
    const x = left + ((right - left) * tick) / 8;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, plotBottom);
    ctx.stroke();
    ctx.fillStyle = COLORS.dim;
    ctx.textAlign = "center";
    ctx.fillText(`${(tick * span / 8 / 1e6).toFixed(1)}`, x, height - 11);
  }
  ctx.textAlign = "right";
  ctx.fillStyle = COLORS.dim;
  ctx.fillText("MS", right, height - 11);

  const waveHigh = 38;
  const waveLow = 92;
  const binWidth = Math.max(1, Math.floor(right - left));
  const bins = new Uint32Array(binWidth);
  for (const edge of decoded.edges) {
    const bin = Math.max(0, Math.min(binWidth - 1, Math.floor(xFor(edge.timeNs) - left)));
    bins[bin] += 1;
  }
  const maxBin = Math.max(1, ...bins);
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, right - left, plotBottom - top);
  ctx.clip();
  for (let bin = 0; bin < bins.length; bin += 1) {
    if (!bins[bin]) continue;
    const energy = Math.log1p(bins[bin]) / Math.log1p(maxBin);
    const barHeight = 11 + energy * (waveLow - waveHigh - 11);
    ctx.fillStyle = `rgba(87, 220, 255, ${0.16 + energy * 0.42})`;
    ctx.fillRect(left + bin, waveLow - barHeight, 1, barHeight);
  }
  ctx.strokeStyle = "rgba(87, 220, 255, 0.25)";
  ctx.beginPath();
  ctx.moveTo(left, waveLow + 0.5);
  ctx.lineTo(right, waveLow + 0.5);
  ctx.stroke();

  const errorLane = 126;
  ctx.strokeStyle = "rgba(124, 139, 153, 0.18)";
  ctx.beginPath();
  ctx.moveTo(left, errorLane);
  ctx.lineTo(right, errorLane);
  ctx.stroke();
  for (const edge of decoded.edges) {
    const x = xFor(edge.timeNs);
    const residual = wrapNs(edge.timeNs - decoded.phaseNs, decoded.halfUnitNs) / (decoded.halfUnitNs * 2);
    const y = errorLane - Math.max(-1.3, Math.min(1.3, residual)) * 18;
    ctx.strokeStyle = Math.abs(residual) > 0.04 ? "rgba(255, 182, 77, 0.68)" : "rgba(87, 220, 255, 0.22)";
    ctx.beginPath();
    ctx.moveTo(x, errorLane);
    ctx.lineTo(x, y);
    ctx.stroke();
  }
  ctx.restore();

  ctx.textAlign = "left";
  ctx.fillStyle = "#536674";
  ctx.font = "7px " + getComputedStyle(document.documentElement).getPropertyValue("--mono");
  ctx.fillText("EDGE", 8, 52);
  ctx.fillText("DENS", 8, 64);
  ctx.fillText("EDGE", 8, 118);
  ctx.fillText("±0.5", 8, 132);
  ctx.fillText("UI", 8, 144);

  const selectedX = xFor(state.selectedTimeNs);
  ctx.strokeStyle = COLORS.green;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(selectedX, top);
  ctx.lineTo(selectedX, plotBottom);
  ctx.stroke();
  ctx.fillStyle = COLORS.green;
  ctx.beginPath();
  ctx.moveTo(selectedX - 4, top);
  ctx.lineTo(selectedX + 4, top);
  ctx.lineTo(selectedX, top + 6);
  ctx.closePath();
  ctx.fill();

  for (const error of decoded.errors) {
    const x = xFor(error.time_s * 1e9);
    ctx.fillStyle = error.severity === "error" ? COLORS.red : COLORS.amber;
    ctx.beginPath();
    ctx.arc(x, plotBottom - 4, 2, 0, Math.PI * 2);
    ctx.fill();
  }

  if (state.hoverTimeNs !== null) {
    const x = xFor(state.hoverTimeNs);
    ctx.strokeStyle = "rgba(230, 237, 242, 0.3)";
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, plotBottom);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawDetail() {
  const canvas = $("#detailCanvas");
  const { ctx, width, height } = prepared(canvas);
  const frame = selectedFrame();
  if (!frame) return;
  const decoded = state.decoded;
  const unit = decoded.unitNs;
  const frameStart = decoded.originNs + frame.frameIndex * decoded.framePeriodNs;
  const t0 = frameStart - unit * 4;
  const t1 = frameStart + unit * 68;
  const left = 18;
  const right = width - 18;
  const top = 22;
  const bottom = height - 29;
  const xFor = (time) => left + ((time - t0) / (t1 - t0)) * (right - left);
  const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono");
  ctx.font = "8px " + mono;

  const preambleA = [frameStart, frameStart + unit * 4];
  const preambleB = [frameStart + unit * 32, frameStart + unit * 36];
  for (const [index, [p0, p1]] of [preambleA, preambleB].entries()) {
    const x0 = xFor(p0);
    const x1 = xFor(p1);
    ctx.fillStyle = index === 0 ? "rgba(255, 159, 90, 0.12)" : "rgba(87, 220, 255, 0.10)";
    ctx.fillRect(x0, top, x1 - x0, bottom - top);
    ctx.fillStyle = index === 0 ? "rgba(255, 159, 90, 0.55)" : "rgba(87, 220, 255, 0.5)";
    ctx.fillRect(x0, top, 1, bottom - top);
    ctx.fillRect(x1, top, 1, bottom - top);
    ctx.fillStyle = index === 0 ? COLORS.orange : COLORS.cyan;
    ctx.fillText(index === 0 ? "PREAMBLE A" : "PREAMBLE B", x0 + 4, top + 9);
  }

  for (let slot = 0; slot <= 64; slot += 1) {
    const time = frameStart + slot * unit;
    if (time < t0 || time > t1) continue;
    const x = xFor(time);
    ctx.strokeStyle = slot % 8 === 0 ? "rgba(128, 154, 174, 0.19)" : "rgba(128, 154, 174, 0.065)";
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    if (slot % 8 === 0 && slot < 64) {
      ctx.fillStyle = COLORS.dim;
      ctx.fillText(String(slot).padStart(2, "0"), x + 2, bottom + 14);
    }
  }

  const high = 58;
  const low = 129;
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = "#8de9ff";
  ctx.beginPath();
  let level = levelAt(t0);
  ctx.moveTo(left, level ? high : low);
  for (const edge of decoded.edges) {
    if (edge.timeNs <= t0) continue;
    if (edge.timeNs > t1) break;
    const x = xFor(edge.timeNs);
    ctx.lineTo(x, level ? high : low);
    ctx.lineTo(x, edge.level ? high : low);
    level = edge.level;
  }
  ctx.lineTo(right, level ? high : low);
  ctx.stroke();

  const subs = subframesForFrame(frame.frameIndex);
  const missing = !subs.some((subframe) => subframe.channel === "A") || !subs.some((subframe) => subframe.channel === "B");
  if (missing) {
    ctx.fillStyle = "rgba(255, 99, 114, 0.08)";
    ctx.fillRect(left, top, right - left, bottom - top);
    ctx.fillStyle = "rgba(255, 99, 114, 0.8)";
    ctx.textAlign = "center";
    ctx.font = "10px " + mono;
    ctx.fillText("NO SUBFRAME / LOSS OF LOCK", (left + right) / 2, (top + bottom) / 2);
    ctx.textAlign = "left";
  }

  if (state.selectedTimeNs >= t0 && state.selectedTimeNs <= t1) {
    const x = xFor(state.selectedTimeNs);
    ctx.strokeStyle = "rgba(167, 255, 99, 0.85)";
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.fillStyle = COLORS.dim;
  ctx.font = "7px " + mono;
  ctx.fillText(`FRAME ${String(frame.frameIndex).padStart(3, "0")} / UI ${unit.toFixed(3)} ns`, left, height - 8);
  ctx.textAlign = "right";
  ctx.fillText(`${formatMs(frameStart)} → ${formatMs(frameStart + decoded.framePeriodNs)}`, right, height - 8);
  ctx.textAlign = "left";
}

function drawAudio() {
  const canvas = $("#audioCanvas");
  const { ctx, width, height } = prepared(canvas);
  const decoded = state.decoded;
  const frames = decoded.frames;
  const start = decoded.edges[0].timeNs;
  const end = decoded.edges.at(-1).timeNs;
  const left = 34;
  const right = width - 16;
  const top = 18;
  const bottom = height - 23;
  const xFor = (index) => left + (index / Math.max(1, frames.length - 1)) * (right - left);
  const laneGap = 12;
  const laneHeight = (bottom - top - laneGap) / 2;
  const lanes = [
    { key: "left", present: "leftPresent", center: top + laneHeight / 2, color: COLORS.orange, label: "L" },
    { key: "right", present: "rightPresent", center: top + laneHeight + laneGap + laneHeight / 2, color: COLORS.cyan, label: "R" }
  ];
  const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono");
  ctx.font = "8px " + mono;

  for (const event of decoded.dropouts) {
    const frameA = Math.floor((event.startNs - decoded.originNs) / decoded.framePeriodNs);
    const frameB = Math.ceil((event.endNs - decoded.originNs) / decoded.framePeriodNs);
    const x0 = xFor(Math.max(0, frameA));
    const x1 = xFor(Math.min(frames.length, frameB));
    ctx.fillStyle = "rgba(255, 99, 114, 0.10)";
    ctx.fillRect(x0, top, Math.max(1, x1 - x0), bottom - top);
    ctx.fillStyle = "rgba(255, 99, 114, 0.45)";
    ctx.fillRect(x0, top, 1, bottom - top);
    ctx.fillRect(x1, top, 1, bottom - top);
  }

  ctx.strokeStyle = COLORS.grid;
  for (let tick = 0; tick <= 8; tick += 1) {
    const x = left + ((right - left) * tick) / 8;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
  }

  for (const lane of lanes) {
    ctx.strokeStyle = "rgba(128, 154, 174, 0.16)";
    ctx.beginPath();
    ctx.moveTo(left, lane.center);
    ctx.lineTo(right, lane.center);
    ctx.stroke();
    ctx.fillStyle = lane.color;
    ctx.fillText(lane.label, 12, lane.center);

    const path = new Path2D();
    let open = false;
    for (let index = 0; index < frames.length; index += 1) {
      const frame = frames[index];
      if (!frame[lane.present]) {
        open = false;
        continue;
      }
      const x = xFor(index);
      const amplitude = Math.max(-1, Math.min(1, frame[lane.key] / 8388608));
      const y = lane.center - amplitude * laneHeight * 0.42;
      if (!open) {
        path.moveTo(x, y);
        open = true;
      } else {
        path.lineTo(x, y);
      }
    }
    ctx.strokeStyle = lane.color;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.88;
    ctx.stroke(path);
    ctx.globalAlpha = 1;
  }

  const frame = selectedFrame();
  if (frame) {
    const x = xFor(frame.frameIndex);
    ctx.strokeStyle = "rgba(167, 255, 99, 0.8)";
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    for (const lane of lanes) {
      if (!frame[lane.present]) continue;
      const amplitude = Math.max(-1, Math.min(1, frame[lane.key] / 8388608));
      const y = lane.center - amplitude * laneHeight * 0.42;
      ctx.fillStyle = lane.color;
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (state.hoverTimeNs !== null) {
    const hoverFrame = Math.floor((state.hoverTimeNs - decoded.originNs) / decoded.framePeriodNs);
    const x = xFor(Math.max(0, Math.min(frames.length - 1, hoverFrame)));
    ctx.strokeStyle = "rgba(230, 237, 242, 0.28)";
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.fillStyle = COLORS.dim;
  ctx.fillText("PCM 24-BIT", 8, top + 4);
  ctx.textAlign = "right";
  ctx.fillText(`${(frames.length / decoded.summary.sampleRate * 1000).toFixed(3)} MS`, right, height - 8);
  ctx.textAlign = "left";
}

function drawEye() {
  const canvas = $("#eyeCanvas");
  const { ctx, width, height } = prepared(canvas);
  const decoded = state.decoded;
  const left = 24;
  const right = width - 16;
  const top = 16;
  const bottom = height - 18;
  const high = top + 20;
  const low = bottom - 12;
  const xFor = (phase) => left + phase * (right - left);
  const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono");
  ctx.font = "7px " + mono;

  for (let unit = 0; unit <= 2; unit += 1) {
    const x = xFor(unit / 2);
    ctx.strokeStyle = unit === 1 ? "rgba(167, 255, 99, 0.24)" : COLORS.grid;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
  }
  for (const y of [high, (high + low) / 2, low]) {
    ctx.strokeStyle = y === (high + low) / 2 ? COLORS.gridStrong : COLORS.grid;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }

  const edges = decoded.edges;
  const step = Math.max(1, Math.floor(edges.length / 190));
  ctx.strokeStyle = "rgba(87, 220, 255, 0.15)";
  ctx.lineWidth = 1;
  for (let seed = 0; seed < edges.length; seed += step) {
    const center = edges[seed].timeNs;
    const t0 = center - decoded.unitNs;
    const t1 = center + decoded.unitNs;
    let level = levelAt(t0);
    const path = new Path2D();
    path.moveTo(xFor(0), level ? high : low);
    for (let index = seed - 12; index < seed + 32; index += 1) {
      const edge = edges[index];
      if (!edge || edge.timeNs <= t0 || edge.timeNs >= t1) continue;
      const phase = (edge.timeNs - t0) / (t1 - t0);
      path.lineTo(xFor(phase), level ? high : low);
      path.lineTo(xFor(phase), edge.level ? high : low);
      level = edge.level;
    }
    path.lineTo(xFor(1), level ? high : low);
    ctx.stroke(path);
  }
  ctx.fillStyle = COLORS.dim;
  ctx.fillText("0 UI", left, height - 5);
  ctx.textAlign = "center";
  ctx.fillText("1 UI", xFor(0.5), height - 5);
  ctx.textAlign = "right";
  ctx.fillText("2 UI", right, height - 5);
  ctx.textAlign = "left";
}

initialize();
