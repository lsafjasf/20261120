import { parseMidi, noteName } from "./midi.js";
import { analyzeMidi, eventKind, eventTypeLabel } from "./analysis.js";
import { synthesizeWav } from "./synth.js";

const $ = (selector) => document.querySelector(selector);
const dom = {
  fileInput: $("#fileInput"),
  fileState: $("#fileState"),
  fileStateText: $("#fileStateText"),
  fileTitle: $("#fileTitle"),
  fileMeta: $("#fileMeta"),
  formatBadge: $("#formatBadge"),
  verificationBadge: $("#verificationBadge"),
  statNotes: $("#statNotes"),
  statSustain: $("#statSustain"),
  statAnomalies: $("#statAnomalies"),
  statAnomaliesSub: $("#statAnomaliesSub"),
  statDuration: $("#statDuration"),
  statDurationSub: $("#statDurationSub"),
  statTimeline: $("#statTimeline"),
  statTimelineSub: $("#statTimelineSub"),
  rollViewport: $("#rollViewport"),
  rollEmpty: $("#rollEmpty"),
  rollInner: $("#rollInner"),
  rollRuler: $("#rollRuler"),
  pianoKeys: $("#pianoKeys"),
  rollGrid: $("#rollGrid"),
  tempoMarkers: $("#tempoMarkers"),
  rollPlayhead: $("#rollPlayhead"),
  currentTime: $("#currentTime"),
  totalTime: $("#totalTime"),
  transportTrack: $("#transportTrack"),
  transportFill: $("#transportFill"),
  transportHead: $("#transportHead"),
  transportBpm: $("#transportBpm"),
  playButton: $("#playButton"),
  stopButton: $("#stopButton"),
  eventFilters: $("#eventFilters"),
  eventTableBody: $("#eventTableBody"),
  eventCountLabel: $("#eventCountLabel"),
  checksList: $("#checksList"),
  checkScore: $("#checkScore"),
  inspectorEmpty: $("#inspectorEmpty"),
  inspectorContent: $("#inspectorContent"),
  clearSelection: $("#clearSelection"),
  exportButton: $("#exportButton"),
  exportPopover: $("#exportPopover"),
  dropOverlay: $("#dropOverlay"),
  toastRegion: $("#toastRegion"),
};

const state = {
  analysis: null,
  fileName: "",
  fixtureMode: null,
  filter: "all",
  selectedNoteId: null,
  selectedEventId: null,
  checks: [],
  audioUrl: null,
  audio: null,
  isPlaying: false,
  animationFrame: null,
  loadToken: 0,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatSeconds(seconds, compact = false) {
  const safe = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safe / 60);
  const remainder = safe - minutes * 60;
  if (compact) return `${minutes}:${remainder.toFixed(2).padStart(5, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function formatTick(tick) {
  return Number(tick).toLocaleString("en-US");
}

function formatBpm(bpm) {
  return `${Math.round(bpm * 1000) / 1000}`;
}

function noteById(noteId) {
  return state.analysis?.notes.find((note) => note.id === noteId) ?? null;
}

function eventById(eventId) {
  return state.analysis?.events.find((event) => event.id === eventId) ?? null;
}

function anomaliesForNote(noteId) {
  return state.analysis?.anomalies.filter((item) => item.noteId === noteId) ?? [];
}

function toast(message, type = "ok") {
  const element = document.createElement("div");
  element.className = `toast${type === "error" ? " is-error" : ""}`;
  element.textContent = message;
  dom.toastRegion.append(element);
  setTimeout(() => element.remove(), 3600);
}

function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function baseFileName() {
  return (state.fileName || "smf").replace(/\.(mid|midi)$/i, "").replace(/[^\w.-]+/g, "-");
}

function resetPlayback() {
  if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
  state.animationFrame = null;
  if (state.audio) {
    state.audio.pause();
    state.audio = null;
  }
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audioUrl = null;
  state.wavBlob = null;
  state.isPlaying = false;
  dom.playButton.classList.remove("is-playing");
  updatePlayback(0);
}

function updatePlayback(seconds) {
  const duration = state.analysis?.durationSeconds || 0;
  const safeSeconds = duration > 0 ? Math.min(duration, Math.max(0, seconds)) : 0;
  const progress = duration > 0 ? safeSeconds / duration : 0;
  dom.currentTime.textContent = formatSeconds(safeSeconds);
  dom.transportFill.style.width = `${progress * 100}%`;
  dom.transportHead.style.left = `${progress * 100}%`;
  if (state.analysis && duration > 0) {
    const marker = state.analysis.tempoMap.tempoAtTick(
      state.analysis.tempoMap.secondsToTick(seconds),
    );
    dom.transportBpm.textContent = `${formatBpm(marker.bpm)} BPM`;
    const x = 64 + progress * (dom.rollGrid.offsetWidth || 1);
    dom.rollPlayhead.style.left = `${x}px`;
    dom.rollPlayhead.style.opacity = seconds > 0 || state.isPlaying ? "1" : "0";
  }
}

function findNoteForEvent(event) {
  if (!state.analysis || !event) return null;
  return (
    state.analysis.notes.find((note) => note.startEvent.id === event.id) ??
    state.analysis.notes.find((note) => note.endEvent?.id === event.id) ??
    null
  );
}

function resetSelection() {
  state.selectedNoteId = null;
  state.selectedEventId = null;
  renderInspector();
  renderRollSelection();
  renderEventSelection();
}

function selectNote(noteId, source = "roll") {
  const note = noteById(noteId);
  if (!note) return;
  state.selectedNoteId = noteId;
  state.selectedEventId = note.startEvent.id;
  renderInspector(note, source);
  renderRollSelection();
  renderEventSelection();
  if (source !== "roll") {
    document.querySelector(`.roll-note[data-note-id="${noteId}"]`)?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: "smooth",
    });
  }
}

function selectEvent(eventId) {
  const event = eventById(eventId);
  if (!event) return;
  const note = findNoteForEvent(event);
  if (note) {
    selectNote(note.id, "event");
    return;
  }
  state.selectedEventId = eventId;
  state.selectedNoteId = null;
  renderInspector(event, "event");
  renderRollSelection();
  renderEventSelection();
}

function renderRollSelection() {
  document.querySelectorAll(".roll-note").forEach((element) => {
    element.classList.toggle("is-selected", element.dataset.noteId === state.selectedNoteId);
  });
  if (state.selectedNoteId) {
    const element = document.querySelector(
      `.roll-note[data-note-id="${state.selectedNoteId}"]`,
    );
    element?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

function renderEventSelection() {
  document.querySelectorAll(".event-table tbody tr").forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.eventId === state.selectedEventId);
  });
}

async function loadArrayBuffer(buffer, fileName, options = {}) {
  const token = ++state.loadToken;
  try {
    const parsed = parseMidi(buffer);
    const analysis = analyzeMidi(parsed);
    if (token !== state.loadToken) return;

    resetPlayback();
    state.analysis = analysis;
    state.fileName = fileName;
    state.fixtureMode = options.fixture ?? null;
    state.selectedNoteId = null;
    state.selectedEventId = null;
    state.filter = "all";
    document.querySelectorAll("#eventFilters button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.filter === "all");
    });

    dom.fileState.classList.add("is-ready");
    dom.fileStateText.textContent = `${fileName} · ${analysis.tracks.length}T · ${analysis.ppq} PPQ`;
    dom.fileTitle.textContent =
      options.title || analysis.tracks[0]?.name || fileName.replace(/\.(mid|midi)$/i, "");
    dom.formatBadge.textContent = `TYPE ${analysis.format}`;
    dom.fileMeta.textContent = `${analysis.tracks.length} 轨 · ${analysis.stats.eventCount} 个源事件 · ${analysis.ppq} ticks / 四分音符 · ${formatBpm(analysis.tempoMap.segments[0].bpm)} BPM 起始`;
    dom.totalTime.textContent = formatSeconds(analysis.durationSeconds);

    renderSummary(analysis);
    renderRoll(analysis);
    renderEvents();
    renderInspector();
    buildFixtureChecks();
    renderChecks();
    updatePlayback(0);
    dom.exportButton.disabled = false;
    toast(
      `已读取 ${fileName}：${analysis.notes.length} 个音符，${analysis.anomalies.length} 条诊断`,
    );
  } catch (error) {
    console.error(error);
    toast(`MIDI 读取失败：${error.message}`, "error");
    dom.fileState.classList.remove("is-ready");
    dom.fileStateText.textContent = "读取失败";
  }
}

async function loadFixture(name, fixtureMode) {
  try {
    const url = new URL(`../fixtures/${name}`, import.meta.url);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const buffer = await response.arrayBuffer();
    await loadArrayBuffer(buffer, name, {
      fixture: fixtureMode,
      title:
        fixtureMode === "tempo"
          ? "速度变化处松踏板"
          : fixtureMode === "missing"
            ? "缺少 Note Off"
            : undefined,
    });
  } catch (error) {
    toast(`无法载入测试曲：${error.message}`, "error");
  }
}

function renderSummary(analysis) {
  dom.statNotes.textContent = String(analysis.stats.noteCount).padStart(2, "0");
  dom.statSustain.textContent = String(analysis.stats.sustainCount).padStart(2, "0");
  dom.statAnomalies.textContent = String(analysis.anomalies.length).padStart(2, "0");
  dom.statAnomaliesSub.textContent =
    analysis.stats.errorCount > 0
      ? `${analysis.stats.errorCount} 个错误`
      : analysis.anomalies.length > 0
        ? "无阻断错误"
        : "未发现异常";
  dom.statDuration.textContent = `${analysis.durationSeconds.toFixed(2)}s`;
  dom.statDurationSub.textContent = `${formatTick(analysis.maxTick)} ticks · 实际播放`;
  dom.statTimeline.textContent = `TYPE ${analysis.format} / ${analysis.tracks.length} TRACK${
    analysis.tracks.length > 1 ? "S" : ""
  }`;
  dom.statTimelineSub.textContent = `${analysis.ppq} PPQ · 统一 tick 时间线`;
}

function visibleFilterEvents() {
  if (!state.analysis) return [];
  if (state.filter === "all") return state.analysis.events;
  if (state.filter === "anomaly") {
    const ids = new Set();
    for (const anomaly of state.analysis.anomalies) {
      if (anomaly.globalNumber) {
        const event = state.analysis.events.find(
          (item) => item.globalNumber === anomaly.globalNumber,
        );
        if (event) ids.add(event.id);
      }
      if (anomaly.noteId) {
        const note = noteById(anomaly.noteId);
        if (note) {
          ids.add(note.startEvent.id);
          if (note.endEvent) ids.add(note.endEvent.id);
        }
      }
    }
    return state.analysis.events.filter((event) => ids.has(event.id));
  }
  return state.analysis.events.filter((event) => eventKind(event) === state.filter);
}

function eventDataHtml(event) {
  if (event.type === "noteOn" || event.type === "noteOff") {
    return `${escapeHtml(noteName(event.data.note))} <em>· velocity ${event.data.velocity}</em>`;
  }
  if (event.type === "controlChange") {
    return `${event.data.controller} → ${event.data.value} <em>· ${
      event.data.controller === 64
        ? event.data.value >= 64
          ? "DOWN"
          : "UP"
        : "CC"
    }</em>`;
  }
  if (event.type === "tempo") {
    return `${formatBpm(event.data.bpm)} BPM <em>· ${event.data.microseconds} µs/QN</em>`;
  }
  return escapeHtml(event.label);
}

function renderEvents() {
  if (!state.analysis) return;
  const events = visibleFilterEvents();
  const anomalyIds = new Set(
    state.analysis.anomalies
      .map((anomaly) => anomaly.globalNumber)
      .filter(Boolean),
  );
  const visible = events.slice(0, 700);

  if (visible.length === 0) {
    dom.eventTableBody.innerHTML =
      '<tr class="table-empty"><td colspan="6">当前筛选没有事件</td></tr>';
  } else {
    dom.eventTableBody.innerHTML = visible
      .map((event) => {
        const kind = eventKind(event);
        const isAnomaly = anomalyIds.has(event.globalNumber);
        return `<tr data-event-id="${event.id}" class="${isAnomaly ? "is-anomaly" : ""}" tabindex="0">
          <td class="ord">${String(event.globalNumber).padStart(2, "0")}</td>
          <td class="tick">${formatTick(event.tick)}</td>
          <td class="play-time">${event.seconds.toFixed(3)}s</td>
          <td><span class="source-track">T${event.trackIndex + 1}</span> <span class="source-event">/ E${event.eventNumber}</span></td>
          <td><span class="event-type is-${kind}${isAnomaly ? " is-anomaly" : ""}">${eventTypeLabel(event)}</span></td>
          <td class="event-data">${eventDataHtml(event)}</td>
        </tr>`;
      })
      .join("");
  }

  const shown = Math.min(events.length, 700);
  dom.eventCountLabel.textContent = `${events.length} EVENTS${
    events.length > shown ? ` · 显示 ${shown}` : ""
  }`;
  renderEventSelection();
}

function pitchClass(pitch) {
  return ((pitch % 12) + 12) % 12;
}

function isBlackKey(pitch) {
  return [1, 3, 6, 8, 10].includes(pitchClass(pitch));
}

function renderRoll(analysis) {
  const hasNotes = analysis.notes.length > 0;
  dom.rollEmpty.hidden = hasNotes;
  dom.rollInner.hidden = !hasNotes;
  if (!hasNotes) return;

  let lowPitch = Math.max(0, analysis.stats.minPitch - 3);
  let highPitch = Math.min(127, analysis.stats.maxPitch + 3);
  const minimumRange = 18;
  while (highPitch - lowPitch + 1 < minimumRange) {
    if (lowPitch > 0) lowPitch -= 1;
    if (highPitch - lowPitch + 1 < minimumRange && highPitch < 127) highPitch += 1;
  }
  const pitchCount = highPitch - lowPitch + 1;
  const rowHeight = pitchCount > 30 ? 15 : pitchCount > 22 ? 18 : 21;
  const rollHeight = pitchCount * rowHeight;
  const rulerHeight = 34;
  const viewportWidth = Math.max(760, dom.rollViewport.clientWidth || 1100);
  const timelineWidth = Math.max(
    viewportWidth - 64,
    Math.ceil(Math.max(0.5, analysis.durationSeconds) * 115),
  );
  const totalHeight = rulerHeight + rollHeight;
  const secondToX = (seconds) =>
    (Math.max(0, seconds) / Math.max(0.001, analysis.durationSeconds)) * timelineWidth;

  const rootStyle = document.documentElement.style;
  dom.rollInner.style.width = `${64 + timelineWidth}px`;
  dom.rollInner.style.height = `${totalHeight}px`;
  dom.rollInner.style.setProperty("--row-height", `${rowHeight}px`);
  dom.rollInner.style.setProperty("--ruler-height", `${rulerHeight}px`);
  dom.rollRuler.style.width = `${timelineWidth}px`;
  dom.pianoKeys.style.top = `${rulerHeight}px`;
  dom.pianoKeys.style.height = `${rollHeight}px`;
  dom.rollGrid.style.top = `${rulerHeight}px`;
  dom.rollGrid.style.width = `${timelineWidth}px`;
  dom.rollGrid.style.height = `${rollHeight}px`;
  dom.rollGrid.style.setProperty("--row-height", `${rowHeight}px`);
  dom.tempoMarkers.style.left = "64px";
  dom.tempoMarkers.style.height = `${totalHeight}px`;
  dom.tempoMarkers.style.width = `${timelineWidth}px`;
  dom.rollPlayhead.style.height = `${totalHeight}px`;
  rootStyle.setProperty("--roll-row-height", `${rowHeight}px`);

  const totalBeats = Math.max(1, analysis.maxTick / analysis.ppq);
  let beatStep = 1;
  if (totalBeats > 120) beatStep = 16;
  else if (totalBeats > 64) beatStep = 8;
  else if (totalBeats > 32) beatStep = 4;
  else if (totalBeats > 16) beatStep = 2;

  const rulerTicks = [];
  const gridBars = [];
  for (let beat = 0; beat * analysis.ppq <= analysis.maxTick; beat += beatStep) {
    const tick = beat * analysis.ppq;
    const seconds = analysis.tempoMap.tickToSeconds(tick);
    const x = secondToX(seconds);
    const isBar = beat % 4 === 0;
    rulerTicks.push(
      `<div class="ruler-tick ${isBar ? "is-bar" : ""}" style="left:${x}px"><span>${seconds.toFixed(2)}s</span></div>`,
    );
    gridBars.push(
      `<span class="grid-bar ${isBar ? "is-bar" : ""}" style="left:${x}px"></span>`,
    );
  }
  dom.rollRuler.innerHTML = rulerTicks.join("");

  const keyRows = [];
  for (let pitch = highPitch; pitch >= lowPitch; pitch -= 1) {
    const className = [
      "piano-key",
      isBlackKey(pitch) ? "is-black" : "",
      pitchClass(pitch) === 0 ? "is-c" : "",
    ]
      .filter(Boolean)
      .join(" ");
    keyRows.push(
      `<div class="${className}" style="height:${rowHeight}px">${
        pitchClass(pitch) === 0 || rowHeight >= 20 ? noteName(pitch) : ""
      }</div>`,
    );
  }
  dom.pianoKeys.innerHTML = keyRows.join("");

  const pedalBands = analysis.pedalIntervals.map((interval) => {
    const start = analysis.tempoMap.tickToSeconds(interval.startTick);
    const end = analysis.tempoMap.tickToSeconds(interval.endTickResolved);
    return `<span class="pedal-band" title="Ch ${interval.channel + 1} 踏板 ${start.toFixed(
      2,
    )}s–${end.toFixed(2)}s" style="left:${secondToX(start)}px;width:${Math.max(
      2,
      secondToX(end) - secondToX(start),
    )}px;top:1px"></span>`;
  });

  const noteElements = analysis.notes.map((note) => {
    const keyWidth = Math.max(3, secondToX(note.endSeconds) - secondToX(note.startSeconds));
    const sustainWidth =
      note.sustainEndSeconds === null
        ? 0
        : Math.max(
            0,
            secondToX(note.sustainEndSeconds) - secondToX(note.endSeconds),
          );
    const left = secondToX(note.startSeconds);
    const top = (highPitch - note.pitch) * rowHeight;
    const hasAnomaly = note.anomalyCodes.length > 0;
    const title = `${note.name} · Ch ${note.channel + 1}\n按键 ${analysis.tempoMap.tickToSeconds(
      note.startTick,
    ).toFixed(3)}s–${note.endSeconds.toFixed(3)}s\n延音 ${
      note.sustainEndSeconds === null
        ? "无"
        : `${note.endSeconds.toFixed(3)}s–${note.sustainEndSeconds.toFixed(3)}s`
    }`;
    return `<button class="roll-note ${hasAnomaly ? "is-anomaly" : ""}" data-note-id="${
      note.id
    }" style="left:${left}px;top:${top + 1}px;width:${keyWidth + sustainWidth + 1}px" title="${escapeHtml(
      title,
    )}" aria-label="${note.name} 音符，源轨 ${note.startEvent.trackIndex + 1} 事件 ${
      note.startEvent.eventNumber
    }">
      <span class="note-key" style="width:${keyWidth}px"></span>
      ${
        sustainWidth > 0
          ? `<span class="note-sustain" style="left:${keyWidth}px;width:${sustainWidth}px"></span>`
          : ""
      }
      ${keyWidth > 27 ? `<span class="note-label">${note.name}</span>` : ""}
    </button>`;
  });

  dom.rollGrid.innerHTML = `${gridBars.join("")}${pedalBands.join("")}${noteElements.join("")}`;

  const markers = analysis.tempoMarkers.map((marker) => {
    const x = secondToX(marker.startSeconds);
    return `<div class="tempo-marker" style="left:${x}px"><span>${formatBpm(
      marker.bpm,
    )} BPM · ${marker.startSeconds.toFixed(2)}s</span></div>`;
  });
  dom.tempoMarkers.innerHTML = markers.join("");
  updatePlayback(state.audio?.currentTime || 0);
}

function highestSeverity(anomalies) {
  if (anomalies.some((item) => item.severity === "error")) return "error";
  if (anomalies.some((item) => item.severity === "warning")) return "warning";
  if (anomalies.length > 0) return "info";
  return "ok";
}

function renderInspector(selection = null, source = "none") {
  if (!state.analysis) {
    dom.inspectorEmpty.hidden = false;
    dom.inspectorContent.hidden = true;
    dom.clearSelection.hidden = true;
    return;
  }
  if (!selection && state.selectedNoteId) {
    selection = noteById(state.selectedNoteId);
  } else if (!selection && state.selectedEventId) {
    selection = eventById(state.selectedEventId);
  }
  if (!selection) {
    dom.inspectorEmpty.hidden = false;
    dom.inspectorContent.hidden = true;
    dom.clearSelection.hidden = true;
    return;
  }
  dom.inspectorEmpty.hidden = true;
  dom.inspectorContent.hidden = false;
  dom.clearSelection.hidden = false;

  if (selection.startTick !== undefined && selection.startEvent) {
    const note = selection;
    const anomalies = anomaliesForNote(note.id);
    const severity = highestSeverity(anomalies);
    const severityLabel =
      severity === "ok" ? "VERIFIED" : severity.toUpperCase();
    const message =
      anomalies.length > 0
        ? anomalies.map((item) => item.detail).join(" ")
        : `按键段与踏板状态一致；Note On 位于源轨 T${
            note.startEvent.trackIndex + 1
          } 的事件 E${note.startEvent.eventNumber}。`;
    const sustainText =
      note.sustainEndSeconds === null
        ? "无延音"
        : `${note.endSeconds.toFixed(3)}s → ${note.sustainEndSeconds.toFixed(3)}s`;
    const otherAnomalies = state.analysis.anomalies
      .filter((item) => item.noteId !== note.id && item.noteId)
      .slice(0, 3);
    dom.inspectorContent.innerHTML = `
      <div class="inspector-hero ${severity === "error" ? "is-anomaly" : ""}">
        <div class="inspector-note"><strong>${escapeHtml(note.name)}</strong><span>CH ${
          note.channel + 1
        } · VEL ${note.velocity}</span></div>
        <span class="severity-badge is-${severity}">${severityLabel}</span>
      </div>
      <p class="inspector-message">${escapeHtml(message)}</p>
      <div class="detail-grid">
        <div class="detail-item"><span>源轨 / SOURCE TRACK</span><strong>T${
          note.startEvent.trackIndex + 1
        } · ${escapeHtml(note.startEvent.trackName)}</strong></div>
        <div class="detail-item"><span>源事件 / EVENT #</span><strong class="is-mint">E${
          note.startEvent.eventNumber
        } · #${note.startEvent.globalNumber}</strong></div>
        <div class="detail-item"><span>TICK 起点</span><strong>${formatTick(
          note.startTick,
        )}</strong></div>
        <div class="detail-item"><span>实际播放</span><strong>${note.startSeconds.toFixed(
          3,
        )}s</strong></div>
        <div class="detail-item"><span>按键段</span><strong>${note.startSeconds.toFixed(
          3,
        )}s → ${note.endSeconds.toFixed(3)}s</strong></div>
        <div class="detail-item"><span>延音段</span><strong class="is-amber">${sustainText}</strong></div>
      </div>
      ${
        anomalies.length > 0
          ? `<div class="anomaly-stack"><span>DIAGNOSTICS</span>${anomalies
              .map(
                (item) =>
                  `<button class="anomaly-link" type="button" data-note-id="${note.id}"><strong>${escapeHtml(
                    item.title,
                  )}</strong><small>tick ${formatTick(item.tick)}</small></button>`,
              )
              .join("")}</div>`
          : ""
      }
      ${
        otherAnomalies.length > 0
          ? `<div class="anomaly-stack"><span>OTHER ANOMALIES</span>${otherAnomalies
              .map(
                (item) =>
                  `<button class="anomaly-link" type="button" data-note-id="${item.noteId}"><strong>${escapeHtml(
                    item.title,
                  )}</strong><small>${escapeHtml(
                    noteById(item.noteId)?.name ?? "",
                  )}</small></button>`,
              )
              .join("")}</div>`
          : ""
      }
    `;
    return;
  }

  const event = selection;
  dom.inspectorContent.innerHTML = `
    <div class="inspector-hero">
      <div class="inspector-note"><strong>${escapeHtml(
        eventTypeLabel(event),
      )}</strong><span>CH ${event.channel === null ? "—" : event.channel + 1}</span></div>
      <span class="severity-badge is-ok">EVENT</span>
    </div>
    <p class="inspector-message">${escapeHtml(event.label)}。事件位于 tick ${formatTick(
      event.tick,
    )}，换算为 ${event.seconds.toFixed(3)} 秒。</p>
    <div class="detail-grid">
      <div class="detail-item"><span>源轨 / SOURCE TRACK</span><strong>T${
        event.trackIndex + 1
      } · ${escapeHtml(event.trackName)}</strong></div>
      <div class="detail-item"><span>源事件 / EVENT #</span><strong class="is-mint">E${
        event.eventNumber
      } · #${event.globalNumber}</strong></div>
      <div class="detail-item"><span>TICK</span><strong>${formatTick(
        event.tick,
      )}</strong></div>
      <div class="detail-item"><span>实际播放</span><strong>${event.seconds.toFixed(
        3,
      )}s</strong></div>
    </div>
  `;
}

function check(label, detail, pass, value) {
  return { label, detail, pass: Boolean(pass), value };
}

function buildFixtureChecks() {
  state.checks = [];
  if (!state.analysis) return;
  const analysis = state.analysis;

  if (state.fixtureMode === "tempo") {
    const twoSeconds = analysis.tempoMap.tickToSeconds(1920);
    const threeSeconds = analysis.tempoMap.tickToSeconds(2400);
    const fourSeconds = analysis.tempoMap.tickToSeconds(2880);
    const tempoAtChange = analysis.tempoMap.tempoAtTick(1920);
    state.checks.push(
      check(
        "tick 1920 → 2.000 秒",
        "120 BPM 前段结束、60 BPM 后段开始",
        Math.abs(twoSeconds - 2) < 0.000001 && Math.abs(tempoAtChange.bpm - 60) < 0.0001,
        `${twoSeconds.toFixed(3)}s / ${formatBpm(tempoAtChange.bpm)} BPM`,
      ),
      check(
        "tick 2400 → 3.000 秒",
        "速度变化后的实际播放位置",
        Math.abs(threeSeconds - 3) < 0.000001,
        `${threeSeconds.toFixed(3)}s`,
      ),
      check(
        "tick 2880 → 4.000 秒",
        "全曲末端按分段速度累计",
        Math.abs(fourSeconds - 4) < 0.000001,
        `${fourSeconds.toFixed(3)}s`,
      ),
      check(
        "速度点与踏板 Up 同 tick",
        "Up 先于 Note Off 生效，不应产生踏板延音",
        !analysis.notes.some(
          (note) => note.endTick <= 1920 && note.sustainEndTick > 1920,
        ),
        "无延音溢出",
      ),
      check(
        "tick 960 同音重触发",
        "Note Off 先关闭旧音，Note On 再开启新音",
        analysis.notes.filter(
          (note) => note.pitch === 60 && (note.startTick === 0 || note.startTick === 960),
        ).length === 2,
        "保留 2 个独立音符",
      ),
    );
  } else if (state.fixtureMode === "missing") {
    const missing = analysis.anomalies.filter(
      (item) => item.code === "missing-note-off",
    );
    const target = analysis.notes.find((note) => note.missingNoteOff);
    state.checks.push(
      check(
        "Type 0 统一轨道解析",
        "格式 0 的所有通道事件进入同一时间线",
        analysis.format === 0,
        `TYPE ${analysis.format} · ${analysis.tracks.length} TRACK`,
      ),
      check(
        "缺失 Note Off 报告",
        "曲末仍处于活动状态的 C4 应被标记为错误",
        missing.length === 1 && target?.name === "C4",
        `${missing.length} 条 / ${target?.name ?? "—"}`,
      ),
      check(
        "异常可回溯到源事件",
        "异常记录应携带轨道号与 Note On 事件编号",
        target?.startEvent.trackIndex === 0 && target?.startEvent.eventNumber === 5,
        target ? `T1 / E${target.startEvent.eventNumber}` : "未找到",
      ),
      check(
        "曲末边界闭合",
        "没有 Note Off 的音符使用曲末 tick 作为诊断终点",
        target?.endTick === analysis.maxTick,
        target ? `tick ${formatTick(target.endTick)}` : "未找到",
      ),
    );
  } else {
    const sourceEventCount = analysis.tracks.reduce(
      (total, track) => total + track.eventCount,
      0,
    );
    state.checks.push(
      check(
        "源事件完整合并",
        "所有轨道的源事件数等于统一时间线事件数",
        sourceEventCount === analysis.events.length,
        `${analysis.events.length} / ${sourceEventCount}`,
      ),
      check(
        "非负音符边界",
        "所有音符的结束 tick 不早于起点 tick",
        analysis.notes.every((note) => note.endTick >= note.startTick),
        `${analysis.notes.length} NOTES`,
      ),
      check(
        "速度映射单调",
        "实际秒随 tick 单调增加",
        analysis.tempoMarkers.every(
          (marker, index) =>
            index === 0 || marker.startSeconds >= analysis.tempoMarkers[index - 1].startSeconds,
        ),
        `${analysis.tempoMarkers.length} SEGMENTS`,
      ),
    );
  }
}

function renderChecks() {
  const passed = state.checks.filter((item) => item.pass).length;
  const total = state.checks.length;
  const allPass = total > 0 && passed === total;
  dom.checkScore.textContent = total > 0 ? `${passed}/${total}` : "—";
  dom.checkScore.classList.toggle("is-pass", allPass);
  dom.checkScore.classList.toggle("is-fail", total > 0 && !allPass);

  if (total === 0) {
    dom.checksList.innerHTML =
      '<p class="checks-empty">载入内置测试曲后，自动核对速度换算与异常报告。</p>';
    setVerificationBadge("", "FIXTURE CHECK", "等待固件");
    return;
  }

  dom.checksList.innerHTML = state.checks
    .map(
      (item) => `<div class="check-item ${item.pass ? "" : "is-fail"}">
        <span class="check-symbol">${item.pass ? "✓" : "!"}</span>
        <span class="check-copy"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(
          item.detail,
        )}</small></span>
        <span class="check-value">${escapeHtml(item.value)}</span>
      </div>`,
    )
    .join("");

  if (state.fixtureMode) {
    setVerificationBadge(
      allPass ? "is-pass" : "is-fail",
      allPass ? "FIXTURE PASS" : "FIXTURE FAIL",
      allPass ? `${passed}/${total} 项通过` : `${total - passed} 项未通过`,
    );
  } else {
    setVerificationBadge("", "MANUAL IMPORT", "结构检查完成");
  }
}

function setVerificationBadge(className, small, strong) {
  dom.verificationBadge.classList.remove("is-pass", "is-fail");
  if (className) dom.verificationBadge.classList.add(className);
  dom.verificationBadge.querySelector(".verification-icon").textContent =
    className === "is-pass" ? "✓" : className === "is-fail" ? "!" : "◇";
  dom.verificationBadge.querySelector("small").textContent = small;
  dom.verificationBadge.querySelector("strong").textContent = strong;
}

async function ensureAudio() {
  if (state.audioUrl && state.audio) {
    return state.audio;
  }
  if (!state.analysis) throw new Error("尚未载入 MIDI");
  toast("正在本地合成参考音频…");
  await new Promise((resolve) => setTimeout(resolve, 20));
  const bytes = synthesizeWav(state.analysis);
  state.wavBlob = new Blob([bytes], { type: "audio/wav" });
  state.audioUrl = URL.createObjectURL(state.wavBlob);
  state.audio = new Audio(state.audioUrl);
  state.audio.preload = "auto";
  state.audio.addEventListener("ended", () => {
    state.isPlaying = false;
    dom.playButton.classList.remove("is-playing");
    updatePlayback(state.analysis?.durationSeconds ?? 0);
  });
  await new Promise((resolve) => {
    state.audio.addEventListener("loadedmetadata", resolve, { once: true });
    setTimeout(resolve, 700);
  });
  return state.audio;
}

function playbackLoop() {
  if (!state.audio || !state.isPlaying) return;
  if (state.audio.currentTime >= state.analysis.durationSeconds) {
    state.audio.pause();
    state.audio.currentTime = state.analysis.durationSeconds;
    state.isPlaying = false;
    dom.playButton.classList.remove("is-playing");
    updatePlayback(state.analysis.durationSeconds);
    return;
  }
  updatePlayback(state.audio.currentTime);
  state.animationFrame = requestAnimationFrame(playbackLoop);
}

async function togglePlayback() {
  if (!state.analysis) {
    toast("请先载入 MIDI 文件", "error");
    return;
  }
  try {
    const audio = await ensureAudio();
    if (state.isPlaying) {
      audio.pause();
      state.isPlaying = false;
      dom.playButton.classList.remove("is-playing");
      if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
      state.animationFrame = null;
      return;
    }
    if (audio.currentTime >= state.analysis.durationSeconds - 0.01) {
      audio.currentTime = 0;
    }
    await audio.play();
    state.isPlaying = true;
    dom.playButton.classList.add("is-playing");
    playLoopStart();
  } catch (error) {
    console.error(error);
    toast(`播放失败：${error.message}`, "error");
  }
}

function playLoopStart() {
  if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
  state.animationFrame = requestAnimationFrame(playbackLoop);
}

function stopPlayback() {
  if (state.audio) state.audio.currentTime = 0;
  state.isPlaying = false;
  dom.playButton.classList.remove("is-playing");
  if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
  state.animationFrame = null;
  updatePlayback(0);
}

async function seekPlayback(seconds) {
  if (!state.analysis) return;
  const safe = Math.max(0, Math.min(state.analysis.durationSeconds, seconds));
  try {
    const audio = await ensureAudio();
    audio.currentTime = Math.min(safe, state.analysis.durationSeconds);
    updatePlayback(safe);
  } catch {
    updatePlayback(safe);
  }
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function exportCsv() {
  if (!state.analysis) {
    toast("请先载入 MIDI 文件", "error");
    return;
  }
  const rows = [
    [
      "global_order",
      "source_track",
      "track_name",
      "source_event",
      "tick",
      "play_seconds",
      "type",
      "channel",
      "note",
      "velocity",
      "controller",
      "value",
      "bpm",
      "label",
    ],
  ];
  for (const event of state.analysis.events) {
    rows.push([
      event.globalNumber,
      event.trackIndex + 1,
      event.trackName,
      event.eventNumber,
      event.tick,
      event.seconds.toFixed(6),
      eventTypeLabel(event),
      event.channel === null ? "" : event.channel + 1,
      event.data.note === undefined ? "" : noteName(event.data.note),
      event.data.velocity ?? "",
      event.data.controller ?? "",
      event.data.value ?? "",
      event.type === "tempo" ? formatBpm(event.data.bpm) : "",
      event.label,
    ]);
  }
  const csv = `\ufeff${rows
    .map((row) => row.map(csvEscape).join(","))
    .join("\r\n")}\r\n`;
  downloadBlob(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
    `${baseFileName()}-events.csv`,
  );
  toast(`事件清单已导出：${state.analysis.events.length} 行`);
}

async function exportWav() {
  if (!state.analysis) {
    toast("请先载入 MIDI 文件", "error");
    return;
  }
  try {
    await ensureAudio();
    downloadBlob(state.wavBlob, `${baseFileName()}-reference.wav`);
    toast("参考音频已导出（本地合成 WAV）");
  } catch (error) {
    toast(`音频导出失败：${error.message}`, "error");
  }
}

function bindEvents() {
  dom.fileInput.addEventListener("change", async () => {
    const file = dom.fileInput.files?.[0];
    if (!file) return;
    const buffer = await file.arrayBuffer();
    await loadArrayBuffer(buffer, file.name);
    dom.fileInput.value = "";
  });

  $("#loadTempoFixture").addEventListener("click", () =>
    loadFixture("tempo-pedal-type1.mid", "tempo"),
  );
  $("#loadMissingFixture").addEventListener("click", () =>
    loadFixture("missing-off-type0.mid", "missing"),
  );

  dom.eventFilters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    dom.eventFilters.querySelectorAll("button").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
    renderEvents();
  });

  dom.eventTableBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-event-id]");
    if (row) selectEvent(row.dataset.eventId);
  });
  dom.eventTableBody.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("tr[data-event-id]");
    if (row) selectEvent(row.dataset.eventId);
  });

  dom.rollGrid.addEventListener("click", (event) => {
    const note = event.target.closest(".roll-note");
    if (note) selectNote(note.dataset.noteId, "roll");
  });

  dom.inspectorContent.addEventListener("click", (event) => {
    const link = event.target.closest("[data-note-id]");
    if (link) selectNote(link.dataset.noteId, "inspector");
  });
  dom.clearSelection.addEventListener("click", resetSelection);

  dom.playButton.addEventListener("click", togglePlayback);
  dom.stopButton.addEventListener("click", stopPlayback);

  const makeSeek = (event) => {
    if (!state.analysis) return;
    const rect = dom.transportTrack.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    seekPlayback(ratio * state.analysis.durationSeconds);
  };
  dom.transportTrack.addEventListener("pointerdown", makeSeek);
  dom.transportTrack.addEventListener("keydown", (event) => {
    if (!state.analysis) return;
    const current = state.audio?.currentTime ?? 0;
    const step = state.analysis.durationSeconds * 0.05;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      seekPlayback(current + step);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekPlayback(current - step);
    } else if (event.key === "Home") {
      event.preventDefault();
      seekPlayback(0);
    } else if (event.key === "End") {
      event.preventDefault();
      seekPlayback(state.analysis.durationSeconds);
    }
  });

  dom.exportButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = dom.exportPopover.hidden;
    dom.exportPopover.hidden = !open;
    dom.exportButton.setAttribute("aria-expanded", String(open));
  });
  dom.exportPopover.addEventListener("click", (event) => {
    event.stopPropagation();
    const button = event.target.closest("button[data-export]");
    if (!button) return;
    dom.exportPopover.hidden = true;
    dom.exportButton.setAttribute("aria-expanded", "false");
    if (button.dataset.export === "csv") exportCsv();
    else exportWav();
  });
  document.addEventListener("click", () => {
    dom.exportPopover.hidden = true;
    dom.exportButton.setAttribute("aria-expanded", "false");
  });

  let dragDepth = 0;
  window.addEventListener("dragenter", (event) => {
    event.preventDefault();
    dragDepth += 1;
    dom.dropOverlay.hidden = false;
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("dragleave", (event) => {
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dom.dropOverlay.hidden = true;
  });
  window.addEventListener("drop", async (event) => {
    event.preventDefault();
    dragDepth = 0;
    dom.dropOverlay.hidden = true;
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (!/\.(mid|midi)$/i.test(file.name)) {
      toast("请选择 .mid 或 .midi 文件", "error");
      return;
    }
    const buffer = await file.arrayBuffer();
    await loadArrayBuffer(buffer, file.name);
  });

  window.addEventListener("keydown", (event) => {
    if (event.code === "Space" && event.target === document.body) {
      event.preventDefault();
      togglePlayback();
    }
    if (event.key === "Escape") {
      dom.exportPopover.hidden = true;
      dom.exportButton.setAttribute("aria-expanded", "false");
    }
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.analysis) renderRoll(state.analysis);
    }, 120);
  });
}

async function init() {
  dom.exportButton.disabled = true;
  bindEvents();
  dom.totalTime.textContent = formatSeconds(0);
  await loadFixture("tempo-pedal-type1.mid", "tempo");
}

init();
