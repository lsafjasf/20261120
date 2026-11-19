// Ranking-consistency check on a SYNTHETIC benchmark (no real listening-test
// data exists in this repository). Each segment is a rendered audio clip with
// a known degradation level; an objective score and a noisy subjective MOS are
// compared, and segments whose ranks disagree are reported with time ranges.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  makePinkNoise,
  applyBiquad,
  lowpass,
  rmsDb,
  encodeWav,
} from "./dsp.js";
import { toCsv } from "./csv.js";

const SAMPLE_RATE = 48000;
const SEGMENT_SEC = 2.5;
const SEGMENT_COUNT = 10;
const OUT_DIR = join("artifacts", "results", "ranking");
mkdirSync(OUT_DIR, { recursive: true });

// Deterministic, ordered degradation plan. Lower objective = more degraded.
// Two planned inversions emulate human-rating disagreement with the metric:
// seg-04 is rated worse than its objective rank, seg-07 is rated better.
const plan = [
  { id: "seg-01", noiseDb: -60, cutoff: 20000, mosShift: 0.05 },
  { id: "seg-02", noiseDb: -52, cutoff: 16000, mosShift: -0.1 },
  { id: "seg-03", noiseDb: -46, cutoff: 12000, mosShift: 0.15 },
  { id: "seg-04", noiseDb: -42, cutoff: 9000, mosShift: -1.25 },
  { id: "seg-05", noiseDb: -38, cutoff: 7000, mosShift: 0.1 },
  { id: "seg-06", noiseDb: -34, cutoff: 5500, mosShift: -0.15 },
  { id: "seg-07", noiseDb: -30, cutoff: 4200, mosShift: 1.3 },
  { id: "seg-08", noiseDb: -26, cutoff: 3200, mosShift: 0.05 },
  { id: "seg-09", noiseDb: -22, cutoff: 2400, mosShift: -0.1 },
  { id: "seg-10", noiseDb: -18, cutoff: 1800, mosShift: 0.0 },
];

const clean = makePinkNoise(SAMPLE_RATE * SEGMENT_SEC, 99173);
const rows = [];

plan.forEach((spec, index) => {
  const startSec = index * SEGMENT_SEC;
  const endSec = startSec + SEGMENT_SEC;

  const filtered = applyBiquad(
    clean,
    lowpass(spec.cutoff, 0.707, SAMPLE_RATE),
  );
  const noise = makePinkNoise(filtered.length, 5000 + index).map(
    (v) => v * Math.pow(10, spec.noiseDb / 20),
  );
  const degraded = filtered.map((v, i) => v + noise[i]);

  const segmentSnr = -spec.noiseDb; // noise level is relative to unit source
  // Objective 0-100 score: SNR and high-frequency retention blended.
  const snrScore = Math.max(0, Math.min(60, (segmentSnr - 10) * 1.2));
  const bandwidthScore = Math.max(0, Math.min(40, (spec.cutoff / 20000) * 40));
  const objective = Number((snrScore + bandwidthScore).toFixed(1));

  // Subjective MOS 1-5: follows objective ranking plus the planned shifts.
  const baseMos = 1 + (objective / 100) * 4;
  const mos = Number(
    Math.max(1, Math.min(5, baseMos + spec.mosShift)).toFixed(2),
  );

  let peak = 0;
  for (const v of degraded) peak = Math.max(peak, Math.abs(v));
  const normalized = degraded.map((v) => (v / peak) * 0.9);
  writeFileSync(
    join(OUT_DIR, `${spec.id}.wav`),
    encodeWav([normalized], SAMPLE_RATE),
  );

  rows.push({
    segment_id: spec.id,
    start_sec: startSec.toFixed(2),
    end_sec: endSec.toFixed(2),
    measured_rms_dbfs: rmsDb(degraded).toFixed(2),
    noise_floor_dbfs: spec.noiseDb,
    lowpass_cutoff_hz: spec.cutoff,
    objective_score: objective,
    subjective_mos: mos,
  });
});

function rankDescending(values) {
  const order = values
    .map((value, index) => ({ value, index }))
    .sort((a, b) => b.value - a.value);
  const ranks = new Array(values.length);
  order.forEach((entry, position) => {
    ranks[entry.index] = position + 1;
  });
  return ranks;
}

const objectiveRanks = rankDescending(rows.map((r) => r.objective_score));
const subjectiveRanks = rankDescending(rows.map((r) => r.subjective_mos));

const mismatches = [];
rows.forEach((row, i) => {
  const objectiveRank = objectiveRanks[i];
  const subjectiveRank = subjectiveRanks[i];
  if (objectiveRank !== subjectiveRank) {
    mismatches.push({
      segment_id: row.segment_id,
      start_sec: row.start_sec,
      end_sec: row.end_sec,
      objective_score: row.objective_score,
      objective_rank: objectiveRank,
      subjective_mos: row.subjective_mos,
      subjective_rank: subjectiveRank,
      rank_delta: subjectiveRank - objectiveRank,
    });
  }
});

// Spearman rank correlation over all segments.
const d2 = objectiveRanks.reduce(
  (sum, rank, i) => sum + (rank - subjectiveRanks[i]) ** 2,
  0,
);
const spearman =
  1 - (6 * d2) / (SEGMENT_COUNT * (SEGMENT_COUNT * SEGMENT_COUNT - 1));

// Low-score audit: confirm the bottom three objective segments also carry the
// lowest MOS values (allowing the planned inversions to be visible).
const bottomObjective = [...rows]
  .sort((a, b) => a.objective_score - b.objective_score)
  .slice(0, 3)
  .map((r) => r.segment_id);
const bottomSubjective = [...rows]
  .sort((a, b) => a.subjective_mos - b.subjective_mos)
  .slice(0, 3)
  .map((r) => r.segment_id);

writeFileSync(join(OUT_DIR, "segments.csv"), toCsv(rows));
writeFileSync(
  join(OUT_DIR, "ranking_mismatches.csv"),
  toCsv(mismatches),
);
writeFileSync(
  join(OUT_DIR, "ranking_summary.json"),
  JSON.stringify(
    {
      dataset: "synthetic-demo",
      segmentCount: SEGMENT_COUNT,
      spearmanRankCorrelation: Number(spearman.toFixed(4)),
      mismatchCount: mismatches.length,
      bottomObjectiveSegments: bottomObjective,
      bottomSubjectiveSegments: bottomSubjective,
      lowScoreAgreement:
        bottomObjective.filter((id) => bottomSubjective.includes(id)).length,
    },
    null,
    2,
  ),
);

console.log("Ranking analysis complete. Artifacts in", OUT_DIR);
console.log("Spearman rho:", spearman.toFixed(4));
for (const row of mismatches) console.log(row);
console.log("Bottom objective:", bottomObjective.join(", "));
console.log("Bottom subjective:", bottomSubjective.join(", "));
