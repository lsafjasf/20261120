import { mkdir, writeFile } from "node:fs/promises";
import { FACES, convertEquirectToCube } from "../src/equirect-cube.js";
import { measureCubeSeams, simulateRotationCadence } from "../src/metrics.js";
import { createProceduralEquirect } from "./panorama.mjs";
import { encodePng } from "./png.mjs";

const ROOT = new URL("../", import.meta.url);
const ASSET_DIR = new URL("assets/", ROOT);
const CUBE_DIR = new URL("assets/cube/", ROOT);
const REPORT_DIR = new URL("reports/", ROOT);
const FACE_SIZE = 512;
const SOURCE_WIDTH = 2048;
const SOURCE_HEIGHT = 1024;

function pngName(face) {
  return `${face.id}.png`;
}

async function main() {
  await mkdir(ASSET_DIR, { recursive: true });
  await mkdir(CUBE_DIR, { recursive: true });
  await mkdir(REPORT_DIR, { recursive: true });

  const started = performance.now();
  const source = createProceduralEquirect(SOURCE_WIDTH, SOURCE_HEIGHT);
  const faces = convertEquirectToCube(source, FACE_SIZE);
  const conversionMs = performance.now() - started;
  const metrics = measureCubeSeams(faces, FACE_SIZE);
  const cadence = simulateRotationCadence({
    angularVelocityDegPerSec: 30,
    refreshRates: [60, 90, 120],
    durationSeconds: 10,
  });

  await writeFile(new URL("equirect.png", ASSET_DIR), encodePng(source.width, source.height, source.data));
  for (let index = 0; index < faces.length; index += 1) {
    const face = FACES[index];
    await writeFile(new URL(pngName(face), CUBE_DIR), encodePng(FACE_SIZE, FACE_SIZE, faces[index]));
  }

  const manifest = {
    source: {
      name: source.name,
      url: "./assets/equirect.png",
      width: source.width,
      height: source.height,
    },
    cube: {
      faceSize: FACE_SIZE,
      faces: FACES.map((face) => ({
        ...face,
        width: FACE_SIZE,
        height: FACE_SIZE,
        url: `./assets/cube/${pngName(face)}`,
      })),
    },
    metrics,
  };

  const report = {
    title: "Equirectangular to Cube Map conversion report",
    generatedAt: new Date().toISOString(),
    conversion: {
      sourceProjection: "equirectangular",
      outputProjection: "cube map",
      sourceResolution: `${source.width} x ${source.height}`,
      faceResolution: `${FACE_SIZE} x ${FACE_SIZE}`,
      faceCount: 6,
      sampler: "bilinear, longitude-wrap, polar clamp",
      seamPolicy: "direction-matched edge duplication + eight-corner synchronization",
      directionPolicy: "continuous unit direction vectors; no Euler interpolation across faces",
      conversionMs: Number(conversionMs.toFixed(3)),
    },
    results: {
      faceResolutions: metrics.faces,
      seamError: {
        verdict: metrics.exactZero ? "PASS: all seams are exact" : "FAIL",
        maxChannelDelta: metrics.maxChannelDelta,
        maxLumaDelta: metrics.maxLumaDelta,
        meanAbsChannelDelta: metrics.meanAbsChannelDelta,
        mismatchPixels: metrics.mismatchPixels,
        comparisons: metrics.totalComparisons,
      },
      reprojectionError: {
        maxPx: metrics.maxReprojectionPx,
        meanPx: metrics.meanReprojectionPx,
        maxAngularErrorDeg: metrics.maxAngularErrorDeg,
      },
      seamPairs: metrics.pairs,
    },
    cadence: {
      relation: "degreesPerUpdate = angularVelocityDegPerSec / updateHz",
      angularVelocityDegPerSec: 30,
      rates: cadence,
      droppedFrames: cadence.reduce((sum, item) => sum + item.droppedFrames, 0),
      verdict: cadence.every((item) => item.droppedFrames === 0)
        ? "PASS: deterministic rotation ticks have no dropped updates"
        : "FAIL: dropped updates detected",
    },
  };

  await writeFile(new URL("cube-manifest.json", CUBE_DIR), JSON.stringify(manifest, null, 2));
  await writeFile(new URL("conversion-report.json", REPORT_DIR), JSON.stringify(report, null, 2));
  await writeFile(new URL("conversion-report.md", REPORT_DIR), renderMarkdown(report));

  console.log(`Generated ${SOURCE_WIDTH}x${SOURCE_HEIGHT} equirect + 6 x ${FACE_SIZE}x${FACE_SIZE} cube faces`);
  console.log(`Seam max channel delta: ${metrics.maxChannelDelta}; mismatch pixels: ${metrics.mismatchPixels}`);
  console.log(`Reprojection max: ${metrics.maxReprojectionPx.toExponential(3)} px`);
  console.log(`Conversion time: ${conversionMs.toFixed(1)} ms`);
}

function renderMarkdown(report) {
  const seam = report.results.seamError;
  const reprojection = report.results.reprojectionError;
  const rates = report.cadence.rates.map((item) => (
    `| ${item.updateHz} Hz | ${item.degreesPerUpdate.toFixed(6)}° | ${item.expectedUpdates} | ${item.actualUpdates} | ${item.droppedFrames} |`
  )).join("\n");
  return `# Equirect → Cube Map 验证报告

生成时间：${report.generatedAt}

## 转换参数

| 项目 | 值 |
| --- | --- |
| 源图 | ${report.conversion.sourceResolution} equirectangular |
| 输出 | 6 × ${report.conversion.faceResolution} |
| 采样 | ${report.conversion.sampler} |
| 接缝策略 | ${report.conversion.seamPolicy} |
| 方向策略 | ${report.conversion.directionPolicy} |
| 转换耗时 | ${report.conversion.conversionMs} ms |

## 接缝检查

**${seam.verdict}**

- 12 条共享边，共 ${seam.comparisons.toLocaleString()} 次通道比较
- 最大单通道差值：${seam.maxChannelDelta}
- 最大亮度差：${seam.maxLumaDelta}
- 平均绝对通道差：${seam.meanAbsChannelDelta}
- 不一致像素：${seam.mismatchPixels}
- 六面接缝像素差：${seam.maxChannelDelta === 0 ? "0（严格为零）" : seam.maxChannelDelta}

## 重投影误差

- 最大像素误差：${reprojection.maxPx.toExponential(3)} px
- 平均像素误差：${reprojection.meanPx.toExponential(3)} px
- 最大方向角误差：${reprojection.maxAngularErrorDeg.toExponential(3)}°

## 旋转更新关系

\`Δθ = Ω / f\`，其中 \`Ω\` 为角速度，\`f\` 为更新频率。基准 \`Ω = ${report.cadence.angularVelocityDegPerSec}°/s\`，10 秒确定性时钟：

| 更新频率 | 每帧角度 | 预期更新 | 实际更新 | 掉帧 |
| --- | --- | --- | --- | --- |
${rates}

**${report.cadence.verdict}**
`;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
