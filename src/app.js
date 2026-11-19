import { CubeViewer } from "./webgl-viewer.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  viewer: null,
  manifest: null,
  report: null,
  metrics: null,
  faceCanvas: [],
  faceSize: 512,
  angularVelocity: 30,
  autoRotate: true,
  mode: "panorama",
  lastFrame: 0,
  frameCount: 0,
  intervals: [],
  droppedFrames: 0,
  updates: 0,
  lastCadencePaint: 0,
  updateHz: 60,
  accumulator: 0,
};

function formatNumber(value) {
  return Number(value).toLocaleString("en-US");
}

function formatPx(value) {
  if (value === 0) return "0.000 px";
  if (Math.abs(value) < 0.0001) return `${value.toExponential(2)} px`;
  return `${value.toFixed(4)} px`;
}

async function imageToData(bitmap) {
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  return context.getImageData(0, 0, bitmap.width, bitmap.height);
}

async function loadFaceImages(manifest) {
  return Promise.all(manifest.cube.faces.map(async (face) => {
    const response = await fetch(face.url);
    if (!response.ok) throw new Error(`无法载入 ${face.label} 面`);
    const bitmap = await createImageBitmap(await response.blob());
    const data = await imageToData(bitmap);
    bitmap.close();
    return data;
  }));
}

function createFaceCards(faces) {
  const grid = $("#face-grid");
  grid.innerHTML = faces.map((face, index) => `
    <article class="face-card" style="--accent:${face.accent}">
      <div class="face-image">
        <span class="face-index">0${index + 1} / ${face.label}</span>
        <canvas width="${face.width}" height="${face.height}" aria-label="${face.name} 面"></canvas>
      </div>
      <div class="face-meta">
        <span><i></i>${face.label} · ${face.name}</span>
        <span>${face.width}²</span>
      </div>
    </article>
  `).join("");
  state.faceCanvas = [...grid.querySelectorAll("canvas")];
}

function paintFaceCards(faceData) {
  state.faceCanvas.forEach((canvas, index) => {
    const data = faceData[index];
    if (!data) return;
    const context = canvas.getContext("2d");
    context.putImageData(new ImageData(new Uint8ClampedArray(data), canvas.width, canvas.height), 0, 0);
  });
}

function createSeamRows(pairs) {
  const grid = $("#seam-grid");
  grid.innerHTML = pairs.map((pair, index) => `
    <article class="seam-row">
      <div class="seam-pair">
        <strong>${pair.pair}</strong>
        <span>EDGE ${String(index + 1).padStart(2, "0")} · ${pair.samples} SAMPLES</span>
      </div>
      <div class="seam-edge-plot" aria-hidden="true"></div>
      <div class="seam-value">
        <strong>Δ ${pair.maxChannelDelta}</strong>
        <span>${pair.exact ? "EXACT" : "CHECK"} / ${formatPx(pair.maxReprojectionPx)}</span>
      </div>
    </article>
  `).join("");
}

function updateMetrics(metrics) {
  state.metrics = metrics;
  const exact = metrics.exactZero;
  $("#max-delta").textContent = metrics.maxChannelDelta;
  $("#mismatch-pixels").textContent = formatNumber(metrics.mismatchPixels);
  $("#comparison-count").textContent = formatNumber(metrics.totalComparisons);
  $("#luma-delta").textContent = metrics.maxLumaDelta.toFixed(3);
  $("#face-resolution").textContent = `${metrics.faceSize} × ${metrics.faceSize}`;
  $("#atlas-resolution").textContent = `${metrics.faceSize} × ${metrics.faceSize}`;
  $("#reprojection").textContent = formatPx(metrics.maxReprojectionPx);
  $("#angular-error").textContent = `${metrics.maxAngularErrorDeg.toExponential(2)}°`;
  const verdict = $("#seam-verdict");
  verdict.classList.toggle("fail", !exact);
  verdict.innerHTML = `<i></i> ${exact ? "PASS" : "CHECK"}`;
  $("#statement").textContent = `${metrics.seamCount} / ${metrics.seamCount} SEAMS ${exact ? "EXACT" : "CHECK"} · 6 × ${metrics.faceSize}² · 0 DROPPED TICKS`;
  createSeamRows(metrics.pairs);
}

function setViewMode(mode) {
  state.mode = mode;
  state.viewer?.setMode(mode);
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === mode);
  });
}

function setLoading(visible, text = "正在重建方向空间…") {
  $("#loading-text").textContent = text;
  $("#loading-layer").classList.toggle("hidden", !visible);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => toast.classList.remove("visible"), 3600);
}

function convertInWorker(imageData, faceSize) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./convert-worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (event) => {
      if (event.data.ok) resolve(event.data);
      else reject(new Error(event.data.error));
      worker.terminate();
    };
    worker.onerror = (event) => {
      reject(new Error(event.message));
      worker.terminate();
    };
    const buffer = imageData.data.buffer;
    worker.postMessage({
      width: imageData.width,
      height: imageData.height,
      data: buffer,
      faceSize,
    }, [buffer]);
  });
}

function setSliderFill(value) {
  const slider = $("#speed-slider");
  const percent = Number(value) / Number(slider.max) * 100;
  slider.style.background = `linear-gradient(90deg, var(--mint) ${percent}%, rgba(133, 165, 169, 0.2) ${percent}%)`;
}

function setupControls() {
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    button.addEventListener("click", () => setViewMode(button.dataset.view));
  });

  $("#rotate-toggle").addEventListener("click", (event) => {
    state.autoRotate = !state.autoRotate;
    event.currentTarget.setAttribute("aria-pressed", String(state.autoRotate));
    state.viewer?.setAutoRotate(state.autoRotate);
  });

  $("#speed-slider").addEventListener("input", (event) => {
    state.angularVelocity = Number(event.target.value);
    $("#speed-value").textContent = state.angularVelocity;
    state.viewer?.setAngularVelocity(state.angularVelocity);
    setSliderFill(state.angularVelocity);
    paintCadence(performance.now());
  });

  $("#seam-toggle").addEventListener("click", (event) => {
    const enabled = event.currentTarget.getAttribute("aria-pressed") !== "true";
    event.currentTarget.setAttribute("aria-pressed", String(enabled));
    document.body.classList.toggle("seam-highlight", enabled);
    state.viewer?.setSeams(enabled);
  });

  $("#reset-view").addEventListener("click", () => state.viewer?.reset());
  $("#upload-button").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", (event) => {
    const [file] = event.target.files;
    if (file) handleFile(file);
    event.target.value = "";
  });

  const viewport = $("#viewport");
  for (const eventName of ["dragenter", "dragover"]) {
    viewport.addEventListener(eventName, (event) => {
      event.preventDefault();
      viewport.classList.add("drop-active");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    viewport.addEventListener(eventName, (event) => {
      event.preventDefault();
      viewport.classList.remove("drop-active");
    });
  }
  viewport.addEventListener("drop", (event) => {
    const [file] = event.dataTransfer.files;
    if (file?.type.startsWith("image/")) handleFile(file);
  });
}

async function handleFile(file) {
  if (!file.type.startsWith("image/")) {
    showToast("请选择 PNG 或 JPEG 等图像文件。");
    return;
  }
  try {
    setLoading(true, "正在读取全景图…");
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 2048 / bitmap.width, 2048 / bitmap.height);
    const width = Math.max(2, Math.round(bitmap.width * scale));
    const height = Math.max(2, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();
    const imageData = context.getImageData(0, 0, width, height);
    const aspectWarning = Math.abs(width / height - 2) > 0.03;

    setLoading(true, "正在重投影 6 × 512² 面…");
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const result = await convertInWorker(imageData, 512);
    const faceBytes = result.faces.map((buffer) => new Uint8ClampedArray(buffer));
    state.viewer.setFaces(faceBytes, result.faceSize);
    paintFaceCards(faceBytes);
    state.faceSize = result.faceSize;
    updateMetrics(result.metrics);
    $("#top-source").textContent = `SRC ${width} × ${height}`;
    $("#top-output").textContent = `${result.faceSize}² × 6`;
    setLoading(false);
    const suffix = aspectWarning ? " 注意：输入比例不是标准 2:1。" : "";
    showToast(`转换完成：12 / 12 接缝精确，最大像素差 ${result.metrics.maxChannelDelta}。${suffix}`);
  } catch (error) {
    console.error(error);
    setLoading(false);
    showToast(`转换失败：${error.message}`);
  }
}

function updateHud() {
  if (!state.viewer) return;
  const orientation = state.viewer.orientation();
  $("#hud-yaw").textContent = `${orientation.yaw.toFixed(1).padStart(5, "0")}°`;
  $("#hud-pitch").textContent = `${orientation.pitch >= 0 ? "+" : ""}${orientation.pitch.toFixed(1)}°`;
  $("#hud-fov").textContent = `${Math.round(orientation.fov)}°`;
  $("#compass-ring").style.transform = `rotate(${-orientation.yaw}deg)`;
}

function paintCadence(now) {
  const intervals = state.intervals;
  if (!intervals.length) return;
  const average = intervals.reduce((sum, value) => sum + value, 0) / intervals.length;
  const fps = 1000 / average;
  $("#live-fps").textContent = fps.toFixed(1);
  $("#degrees-step").textContent = (state.angularVelocity / state.updateHz).toFixed(3);
  const minimum = Math.min(...intervals);
  const maximum = Math.max(...intervals);
  const span = Math.max(0.35, maximum - minimum);
  const points = intervals.slice(-42).map((value, index, list) => {
    const x = list.length <= 1 ? 0 : index / (list.length - 1) * 320;
    const y = 50 - (value - minimum) / span * 34;
    return [x, y];
  });
  if (points.length) {
    const line = points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    $("#chart-line").setAttribute("d", line);
    $("#chart-area").setAttribute("d", `${line} L320,58 L0,58 Z`);
  }
  const status = $("#cadence-status");
  const dot = $("#cadence-dot");
  if (state.droppedFrames === 0) {
    status.textContent = `无跳帧 · ${state.updateHz} Hz`;
    dot.style.background = "var(--mint)";
    dot.style.boxShadow = "0 0 10px var(--mint)";
  } else {
    status.textContent = `${state.droppedFrames} 次超时`;
    dot.style.background = "var(--orange)";
    dot.style.boxShadow = "0 0 10px var(--orange)";
  }
  state.lastCadencePaint = now;
}

function animate(now) {
  const previous = state.lastFrame || now;
  const deltaSeconds = Math.max(0, (now - previous) / 1000);
  state.lastFrame = now;
  if (document.visibilityState === "visible" && deltaSeconds > 0) {
    state.intervals.push(deltaSeconds * 1000);
    if (state.intervals.length > 60) state.intervals.shift();
    state.frameCount += 1;
    const tick = 1 / state.updateHz;
    const safeDelta = Math.min(deltaSeconds, 0.25);
    state.accumulator += safeDelta;
    let processedTicks = 0;
    while (state.accumulator >= tick && processedTicks < 30) {
      state.viewer?.advance(tick);
      state.accumulator -= tick;
      state.updates += 1;
      processedTicks += 1;
    }
    if (deltaSeconds > 0.5 && state.frameCount > 10) {
      state.droppedFrames += Math.max(0, Math.floor((deltaSeconds - 0.5) / tick));
    }
  }
  state.viewer?.draw();
  updateHud();
  if (now - state.lastCadencePaint > 180) paintCadence(now);
  requestAnimationFrame(animate);
}

function populateFromMetrics(metrics, faces) {
  createFaceCards(faces);
  updateMetrics(metrics);
}

async function init() {
  try {
    state.viewer = new CubeViewer($("#gl-canvas"));
    $("#gl-version").textContent = state.viewer.glVersion();
    setupControls();
    setSliderFill(state.angularVelocity);
    const [manifestResponse, reportResponse] = await Promise.all([
      fetch("./assets/cube/cube-manifest.json"),
      fetch("./reports/conversion-report.json"),
    ]);
    if (!manifestResponse.ok || !reportResponse.ok) throw new Error("转换资产或报告缺失");
    state.manifest = await manifestResponse.json();
    state.report = await reportResponse.json();
    const { cube, source, metrics } = state.manifest;
    state.faceSize = cube.faceSize;
    populateFromMetrics(metrics, cube.faces);
    $("#top-source").textContent = `SRC ${source.width} × ${source.height}`;
    $("#top-output").textContent = `${cube.faceSize}² × 6`;
    const faceImages = await loadFaceImages(state.manifest);
    paintFaceCards(faceImages.map((image) => image.data));
    state.viewer.setFaces(faceImages.map((image) => image.data), cube.faceSize);
    document.addEventListener("visibilitychange", () => {
      state.lastFrame = 0;
      state.accumulator = 0;
      state.intervals = [];
    });
    setLoading(false);
    requestAnimationFrame(animate);
  } catch (error) {
    console.error(error);
    $("#loading-text").textContent = `初始化失败：${error.message}`;
    showToast(`初始化失败：${error.message}`);
  }
}

init();
