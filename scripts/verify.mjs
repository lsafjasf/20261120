import { readFile } from "node:fs/promises";
import { measureCubeSeams } from "../src/metrics.js";
import { decodePng } from "./png.mjs";

const report = JSON.parse(await readFile(new URL("../reports/conversion-report.json", import.meta.url)));
const manifest = JSON.parse(await readFile(new URL("../assets/cube/cube-manifest.json", import.meta.url)));
const seam = report.results.seamError;
const reprojection = report.results.reprojectionError;
const decodedFaces = [];
for (const face of manifest.cube.faces) {
  const png = await readFile(new URL(`../${face.url.replace("./", "")}`, import.meta.url));
  decodedFaces.push(decodePng(png).data);
}
const decodedMetrics = measureCubeSeams(decodedFaces, manifest.cube.faceSize);

console.log("FACE RESOLUTIONS");
for (const face of report.results.faceResolutions) {
  console.log(`  ${face.label.padEnd(3)} ${face.name.padEnd(7)} ${face.width} x ${face.height}`);
}

console.log("\nSEAM PIXEL DIFFERENCE");
console.log(`  verdict:              ${seam.verdict}`);
console.log(`  max channel delta:    ${seam.maxChannelDelta}`);
console.log(`  max luma delta:       ${seam.maxLumaDelta}`);
console.log(`  mean abs delta:       ${seam.meanAbsChannelDelta}`);
console.log(`  mismatched pixels:    ${seam.mismatchPixels}`);
console.log(`  channel comparisons:  ${seam.comparisons}`);
console.log(`  decoded-file check:   max ${decodedMetrics.maxChannelDelta}, `
  + `${decodedMetrics.mismatchPixels} mismatched pixels`);

console.log("\nINTER-FACE REPROJECTION");
console.log(`  max pixel error:      ${reprojection.maxPx.toExponential(6)} px`);
console.log(`  mean pixel error:     ${reprojection.meanPx.toExponential(6)} px`);
console.log(`  max angular error:    ${reprojection.maxAngularErrorDeg.toExponential(6)} deg`);

console.log("\nROTATION CADENCE  (delta-theta = omega / updateHz)");
for (const rate of report.cadence.rates) {
  console.log(
    `  ${String(rate.updateHz).padStart(3)} Hz -> ${rate.degreesPerUpdate.toFixed(6)} deg/update, `
    + `${rate.actualUpdates}/${rate.expectedUpdates} updates, ${rate.droppedFrames} dropped`,
  );
}
console.log(`  verdict: ${report.cadence.verdict}`);

const failures = [];
if (decodedMetrics.maxChannelDelta !== 0) failures.push("decoded PNG seam channel delta is not zero");
if (decodedMetrics.mismatchPixels !== 0) failures.push("decoded PNG seam mismatch count is not zero");
if (reprojection.maxAngularErrorDeg !== 0) failures.push("edge direction mismatch is not zero");
if (report.cadence.droppedFrames !== 0) failures.push("cadence simulation dropped updates");
if (manifest.metrics.pairs.length !== 12) failures.push("seam pair count is not twelve");

if (failures.length) {
  console.error(`\nFAIL\n- ${failures.join("\n- ")}`);
  process.exitCode = 1;
} else {
  console.log("\nPASS: 6 faces, 12 exact seams, 0 seam pixel difference, 0 dropped updates.");
}
