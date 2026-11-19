"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  sessionId: null,
  report: null,
  frame: 0,
  previewUrl: null,
  previewToken: 0,
  previewTimer: null,
  analyzing: false,
};

const ANOMALY_NAMES = {
  eye_order_swapped: "眼别接反",
  eye_order_inconsistent: "眼别顺序不稳定",
  eye_identity_unconfirmed: "眼别待人工判读",
  metadata_order_conflict: "元数据眼别冲突",
  timeline_gap: "源帧序列缺口",
  timeline_reorder: "源帧倒退或重复",
  frame_pair_mismatch: "左右帧号错配",
  parallax_exceeded: "水平视差超限",
  vertical_offset: "垂直偏移超限",
  measurement_missing: "测点不可测",
  frame_clock_unconfirmed: "帧号不可读",
};

const EYE_NAMES = {
  first: "A 槽",
  second: "B 槽",
  foreground: "前景",
  background: "背景",
  both: "双眼",
};

function setNotice(element, text, kind = "") {
  element.textContent = text;
  element.className = `inline-status ${kind}`.trim();
}

function signed(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(Number(value) * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded}`;
}

function padFrame(value) {
  return String(value ?? 0).padStart(6, "0");
}

function intValue(selector, fallback = 0) {
  const value = Number.parseInt($(selector).value, 10);
  return Number.isFinite(value) ? value : fallback;
}

function selectedOrder() {
  const explicit = $("#export-order").value;
  if (explicit !== "auto") return explicit;
  return state.report?.eye_identity?.selected_order || "ab";
}

function sourceIndex(outputIndex, offset) {
  const source = outputIndex - offset;
  return source >= 0 && source < (state.report?.frames.length || 0) ? source : null;
}

function slotFor(side, order) {
  if (side === "left") return order === "ab" ? "first" : "second";
  return order === "ab" ? "second" : "first";
}

function eyeAt(side) {
  if (!state.report) return null;
  const order = selectedOrder();
  const offset = side === "left" ? intValue("#offset-left") : intValue("#offset-right");
  const index = sourceIndex(state.frame, offset);
  if (index === null) return { index: null, eye: null };
  return { index, eye: state.report.frames[index][slotFor(side, order)] };
}

function frameDiagnosis() {
  if (!state.report) return { text: "—", className: "" };
  const left = eyeAt("left");
  const right = eyeAt("right");
  if (!left.eye || !right.eye) return { text: "偏移后超出素材范围", className: "warn" };
  const flags = new Set([
    ...state.report.frames[left.index].flags,
    ...state.report.frames[right.index].flags,
  ]);
  if (flags.has("frame_pair_mismatch")) return { text: "错帧：左右源帧不同", className: "bad" };
  if (flags.has("first_timeline_gap") || flags.has("second_timeline_gap")) {
    return { text: "缺帧：源帧号不连续", className: "bad" };
  }
  if (flags.has("first_timeline_reorder") || flags.has("second_timeline_reorder")) {
    return { text: "源帧倒退或重复", className: "bad" };
  }
  if (flags.has("foreground_parallax_exceeded") || flags.has("background_parallax_exceeded")) {
    return { text: "水平视差超限", className: "bad" };
  }
  if (flags.has("foreground_vertical_offset") || flags.has("background_vertical_offset")) {
    return { text: "垂直偏移超限", className: "bad" };
  }
  if (flags.has("foreground_missing") || flags.has("background_missing")) {
    return { text: "测点不可测", className: "warn" };
  }
  return { text: "正常", className: "good" };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { error: `HTTP ${response.status}` };
  }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function guessOutputPath(source) {
  const clean = source.trim().replace(/[\\/]+$/, "");
  const match = clean.match(/^(.*)[\\/]([^\\/]+)$/);
  const name = (match ? match[2] : clean).replace(/\.[^.]+$/, "");
  const directory = match ? match[1] : ".";
  return `${directory}/${name}_preflight.mp4`;
}

async function analyze() {
  if (state.analyzing) return;
  const path = $("#source-path").value.trim();
  if (!path) {
    setNotice($("#analyze-status"), "请先填写视频文件路径。", "error");
    $("#source-path").focus();
    return;
  }
  const packing = $('input[name="packing"]:checked').value;
  const maxDisparity = $("#max-disparity").value.trim();
  const payload = {
    path,
    packing,
    max_disparity_px: maxDisparity ? Number.parseInt(maxDisparity, 10) : null,
    max_vertical_px: Number.parseFloat($("#max-vertical").value || "2"),
    assumed_order: $("#assumed-order").value || null,
    save_report: $("#report-path").value.trim() || null,
  };
  const button = $("#analyze-button");
  state.analyzing = true;
  button.disabled = true;
  setNotice($("#analyze-status"), "正在逐帧解码、识别标记并测量…");
  try {
    const data = await fetchJson("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.sessionId = data.session_id;
    state.report = data.report;
    state.frame = 0;
    if (!$("#output-path").value.trim()) $("#output-path").value = guessOutputPath(path);
    renderReport();
    const reportNote = data.report_path ? `；报告已保存到 ${data.report_path}` : "";
    setNotice($("#analyze-status"), `完成：${data.report.summary.frames_analyzed} 帧${reportNote}`, "success");
  } catch (error) {
    setNotice($("#analyze-status"), `分析失败：${error.message}`, "error");
  } finally {
    state.analyzing = false;
    button.disabled = false;
  }
}

function renderReport() {
  const report = state.report;
  const summary = report.summary;
  $("#empty-state").hidden = true;
  $("#results").hidden = false;
  $("#status-text").textContent = summary.status.toUpperCase();
  $("#status-dot").className = `status-dot ${summary.status}`;
  $("#media-summary").textContent = `${report.input.name} · ${report.media.width}×${report.media.height} ${report.media.packing.toUpperCase()}`;
  $("#rate-summary").textContent = `${report.media.frame_rate} fps · ${report.media.frame_count} 帧`;
  $("#anomaly-summary").textContent = `${summary.critical_count} 严重 / ${summary.warning_count} 警告`;

  const identity = report.eye_identity;
  $("#identity-card").hidden = false;
  $("#identity-order").textContent = identity.selected_order
    ? `${identity.first_slot}=${identity.selected_order === "ab" ? "L" : "R"} · ${identity.second_slot}=${identity.selected_order === "ab" ? "R" : "L"}`
    : "待人工判读";
  const sourceNames = {
    "burned-in-markers": "机读 L/R 标记",
    metadata: "容器元数据",
    operator: "人工判读",
    unconfirmed: "无法确认",
  };
  $("#identity-source").textContent = sourceNames[identity.source] || identity.source;
  const coverage = Math.round(identity.label_coverage * 100);
  $("#coverage-bar").style.width = `${coverage}%`;
  $("#coverage-text").textContent = `${identity.labeled_frames}/${report.frames.length} 帧有标记`;
  $("#coverage-bar").style.background = identity.confirmed ? "var(--green)" : "var(--warning)";
  $("#identity-warning").hidden = !identity.manual_review_required;
  $("#identity-warning").textContent = identity.manual_review_required
    ? "缺少可确认的眼别标记，导出前必须人工指定 AB 或 BA。"
    : "";

  $("#frame-slider").max = Math.max(0, report.frames.length - 1);
  $("#frame-total").textContent = report.frames.length;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  renderAnomalies();
  renderFrame();
  $("#validation-box").className = "validation-box";
  $("#validation-box").textContent = "修正参数已改变，请重新验证。";
}

function renderAnomalies() {
  const list = $("#anomaly-list");
  list.replaceChildren();
  const anomalies = state.report.anomalies;
  if (!anomalies.length) {
    const empty = document.createElement("div");
    empty.className = "anomaly-empty";
    empty.textContent = "未发现异常";
    list.append(empty);
    return;
  }
  for (const anomaly of anomalies) {
    const button = document.createElement("button");
    button.className = `anomaly-item ${anomaly.severity}`;
    button.type = "button";
    const severity = document.createElement("i");
    severity.className = "anomaly-severity";
    const copy = document.createElement("div");
    copy.className = "anomaly-copy";
    const title = document.createElement("strong");
    title.textContent = `${ANOMALY_NAMES[anomaly.type] || anomaly.type} · ${EYE_NAMES[anomaly.eye] || anomaly.eye}`;
    const detail = document.createElement("span");
    detail.textContent = anomalyText(anomaly);
    copy.append(title, detail);
    const range = document.createElement("span");
    range.className = "anomaly-frame";
    range.textContent = anomaly.start_frame === anomaly.end_frame
      ? `#${padFrame(anomaly.start_frame)}`
      : `#${padFrame(anomaly.start_frame)}–${padFrame(anomaly.end_frame)}`;
    button.append(severity, copy, range);
    button.addEventListener("click", () => goToFrame(anomaly.start_frame));
    list.append(button);
  }
}

function anomalyText(anomaly) {
  const details = anomaly.details || {};
  if (anomaly.type === "timeline_gap") {
    const missing = details.missing_source_frames || [];
    return `源帧 ${details.previous_source_frame} → ${details.observed_source_frame}，缺 ${missing.join(", ") || "若干帧"}。`
  }
  if (anomaly.type === "frame_pair_mismatch") {
    return `B−A = ${details.second_minus_first > 0 ? "+" : ""}${details.second_minus_first} 帧；后续片段不得自动配准。`;
  }
  if (anomaly.type === "parallax_exceeded" || anomaly.type === "vertical_offset") {
    return `峰值 ${anomaly.max_value} px，上限 ${details.limit_px} px。`;
  }
  if (anomaly.type === "eye_order_swapped") return "A 槽为右眼、B 槽为左眼，需交换后导出。";
  if (anomaly.type === "eye_identity_unconfirmed") return anomaly.message;
  return anomaly.message;
}

function renderFrame() {
  if (!state.report) return;
  const total = state.report.frames.length;
  state.frame = Math.max(0, Math.min(total - 1, state.frame));
  $("#frame-slider").value = state.frame;
  $("#frame-current").textContent = state.frame;
  $("#frame-badge").textContent = `F${padFrame(state.frame)}`;

  const left = eyeAt("left");
  const right = eyeAt("right");
  $("#frame-ids").textContent = `L ${left.eye?.source_frame ?? "—"} / R ${right.eye?.source_frame ?? "—"}`;
  if (left.eye?.foreground && right.eye?.foreground) {
    const dx = right.eye.foreground[0] - left.eye.foreground[0];
    const dy = right.eye.foreground[1] - left.eye.foreground[1];
    $("#fg-readout").textContent = `${signed(dx)} / ${signed(dy)} px`;
  } else {
    $("#fg-readout").textContent = "—";
  }
  if (left.eye?.background && right.eye?.background) {
    const dx = right.eye.background[0] - left.eye.background[0];
    const dy = right.eye.background[1] - left.eye.background[1];
    $("#bg-readout").textContent = `${signed(dx)} / ${signed(dy)} px`;
  } else {
    $("#bg-readout").textContent = "—";
  }
  const diagnosis = frameDiagnosis();
  $("#frame-diagnosis").textContent = diagnosis.text;
  $("#frame-diagnosis").className = diagnosis.className;
  schedulePreview();
}

function goToFrame(index) {
  if (!state.report) return;
  state.frame = Math.max(0, Math.min(state.report.frames.length - 1, index));
  renderFrame();
}

function schedulePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(loadPreview, 70);
}

async function loadPreview() {
  if (!state.sessionId || !state.report) return;
  const token = ++state.previewToken;
  const mode = $('input[name="view"]:checked').value;
  const params = new URLSearchParams({
    mode,
    order: selectedOrder(),
    offset_left: String(intValue("#offset-left")),
    offset_right: String(intValue("#offset-right")),
    marks: $("#show-marks").checked ? "1" : "0",
  });
  $("#viewer-loading").hidden = false;
  $("#viewer-loading").textContent = "读取帧…";
  try {
    const response = await fetch(
      `/api/session/${state.sessionId}/frame/${state.frame}.png?${params.toString()}`
    );
    if (!response.ok) throw new Error(`预览失败：HTTP ${response.status}`);
    const blob = await response.blob();
    if (token !== state.previewToken) return;
    const objectUrl = URL.createObjectURL(blob);
    const image = $("#preview-image");
    image.onload = () => {
      if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
      state.previewUrl = objectUrl;
      $("#viewer-loading").hidden = true;
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      $("#viewer-loading").hidden = false;
      $("#viewer-loading").textContent = "无法显示预览";
    };
    image.src = objectUrl;
  } catch (error) {
    if (token === state.previewToken) {
      $("#viewer-loading").hidden = false;
      $("#viewer-loading").textContent = error.message;
    }
  }
}

function correctionPayload() {
  return {
    order: $("#export-order").value,
    offset_left: intValue("#offset-left"),
    offset_right: intValue("#offset-right"),
  };
}

function correctionsChanged() {
  if (!state.report) return;
  const box = $("#validation-box");
  box.className = "validation-box";
  box.textContent = "修正参数已改变，请重新验证。";
  setNotice($("#export-status"), "");
  renderFrame();
}

async function validateCorrections() {
  if (!state.sessionId) return null;
  const button = $("#validate-button");
  button.disabled = true;
  const box = $("#validation-box");
  box.className = "validation-box";
  box.textContent = "正在检查修正后的逐帧配对…";
  try {
    const result = await fetchJson(`/api/session/${state.sessionId}/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(correctionPayload()),
    });
    const messages = [];
    for (const error of result.errors) messages.push(`阻止导出：${error.message}`);
    if (result.unresolved_safety_anomalies.length) {
      messages.push(`未修正的安全异常：${result.unresolved_safety_anomalies.join("、")}，导出需人工确认。`);
    }
    messages.push(...result.warnings);
    if (!result.errors.length && !result.unresolved_safety_anomalies.length) {
      messages.unshift("逐帧配对通过，可按原帧率导出。");
      box.className = "validation-box ok";
    } else {
      box.className = result.errors.length ? "validation-box bad" : "validation-box";
    }
    box.textContent = messages.join("\n");
    return result;
  } catch (error) {
    box.className = "validation-box bad";
    box.textContent = `校验失败：${error.message}`;
    return null;
  } finally {
    button.disabled = false;
  }
}

async function exportVideo() {
  if (!state.sessionId) return;
  const output = $("#output-path").value.trim();
  if (!output) {
    setNotice($("#export-status"), "请填写输出文件路径。", "error");
    $("#output-path").focus();
    return;
  }
  const validation = await validateCorrections();
  if (!validation) return;
  const force = $("#force-export").checked;
  if (validation.errors.length && !force) {
    setNotice($("#export-status"), "仍有未解决的帧配对或眼别问题，请修正或勾选人工确认后再导出。", "error");
    return;
  }
  if (validation.unresolved_safety_anomalies.length && !force) {
    setNotice($("#export-status"), "仍有视差/垂直安全异常，需勾选人工确认后才能导出。", "error");
    return;
  }
  const button = $("#export-button");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "导出中…";
  setNotice($("#export-status"), "正在按原帧率重组并编码，请稍候…");
  try {
    const result = await fetchJson(`/api/session/${state.sessionId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...correctionPayload(),
        output,
        packaging: $("#export-packing").value || null,
        force,
      }),
    });
    const warning = result.warnings.length ? `；提示：${result.warnings.join("；")}` : "";
    setNotice(
      $("#export-status"),
      `导出完成：${result.output}（${result.frame_rate} fps，${result.frame_count} 帧）${warning}`,
      "success"
    );
  } catch (error) {
    setNotice($("#export-status"), `导出失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function bindEvents() {
  $("#analyze-button").addEventListener("click", analyze);
  $("#validate-button").addEventListener("click", validateCorrections);
  $("#export-button").addEventListener("click", exportVideo);
  $("#prev-frame").addEventListener("click", () => goToFrame(state.frame - 1));
  $("#next-frame").addEventListener("click", () => goToFrame(state.frame + 1));
  $("#frame-slider").addEventListener("input", (event) => goToFrame(Number(event.target.value)));
  $$('input[name="view"]').forEach((input) => input.addEventListener("change", schedulePreview));
  $("#show-marks").addEventListener("change", schedulePreview);
  $("#export-order").addEventListener("change", correctionsChanged);
  $("#offset-left").addEventListener("input", correctionsChanged);
  $("#offset-right").addEventListener("input", correctionsChanged);
  $("#export-packing").addEventListener("change", correctionsChanged);
  $("#source-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") analyze();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      analyze();
      return;
    }
    if (!state.report || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goToFrame(state.frame - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      goToFrame(state.frame + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      goToFrame(0);
    } else if (event.key === "End") {
      event.preventDefault();
      goToFrame(state.report.frames.length - 1);
    }
  });
}

bindEvents();
