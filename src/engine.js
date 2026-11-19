import { CUES, DURATION, LATE_CUE_ID, SCENARIO, clamp } from "./data.js";

const DT = 0.1;
const ROUND = (value, digits = 2) => Number(value.toFixed(digits));

function measuredOffset(t, options) {
  const step = options.step && t >= SCENARIO.stepAt ? SCENARIO.stepDelay : 0;
  const spike =
    options.spike &&
    t >= SCENARIO.spikeAt &&
    t < SCENARIO.spikeAt + SCENARIO.spikeDuration
      ? SCENARIO.spikeAmplitude
      : 0;
  const jitter =
    0.022 * Math.sin(t * 2.17) +
    0.013 * Math.sin(t * 5.71 + 0.8) +
    0.007 * Math.sin(t * 11.3 + 2.1);
  return ROUND(step + spike + jitter, 3);
}

function bufferHealth(t, options) {
  const stepPressure = options.step && t >= SCENARIO.stepAt ? 0.14 : 0;
  const spikePressure =
    options.spike && t >= SCENARIO.spikeAt && t < SCENARIO.spikeAt + SCENARIO.spikeDuration
      ? 0.09
      : 0;
  const wave = 0.035 * Math.sin(t * 0.32) + 0.018 * Math.sin(t * 1.27);
  return clamp(0.82 - stepPressure - spikePressure + wave, 0.42, 0.96);
}

function initialSchedule() {
  return Object.fromEntries(
    CUES.map((cue) => [
      cue.id,
      {
        recognized: false,
        skipped: false,
        skipTime: null,
        skipReason: null,
        skipDetail: null,
        lockedStart: null,
        start: cue.start,
        end: cue.end,
        delta: 0,
        gap: null,
        status: "upcoming",
      },
    ]),
  );
}

function arrivalForCue(cue, options) {
  return options.late || !cue.late ? cue.arrivedAt : cue.start - 0.35;
}

function layoutFrame(t, applied, schedule, options) {
  let previousEnd = null;
  let activeCue = null;

  for (const cue of CUES) {
    const item = schedule[cue.id];
    const arrival = arrivalForCue(cue, options);
    if (!item.recognized && t >= arrival) item.recognized = true;

    if (
      !item.skipped &&
      !item.recognized &&
      t > cue.start + applied + SCENARIO.lateGrace
    ) {
      item.skipped = true;
      item.skipTime = ROUND(cue.start + applied + SCENARIO.lateGrace, 1);
      item.skipReason = "识别结果超过截止窗口";
      item.skipDetail = `T+${arrival.toFixed(1)} s 才到达，晚于截止 ${(
        arrival -
        item.skipTime
      ).toFixed(1)} s`;
    }

    if (item.skipped) {
      item.status = "skipped";
      item.start = null;
      item.end = null;
      item.gap = null;
      continue;
    }

    let start;
    if (item.lockedStart !== null) {
      start = item.lockedStart;
    } else {
      const candidate = Math.max(0, cue.start + applied);
      const floor = previousEnd === null ? 0 : previousEnd + SCENARIO.minGap;
      start = Math.max(candidate, floor);
      if (item.recognized && t >= start - 0.001) {
        item.lockedStart = start;
      }
    }

    const end = start + cue.duration;
    item.start = ROUND(start, 3);
    item.end = ROUND(end, 3);
    item.delta = ROUND(start - cue.start, 3);
    item.gap = previousEnd === null ? null : ROUND(start - previousEnd, 3);
    item.status = t < start ? "upcoming" : t < end ? "live" : "locked";
    if (item.status === "live" && item.recognized) activeCue = cue;
    previousEnd = end;
  }
  return activeCue;
}

function auditFrame(schedule) {
  let overlaps = 0;
  let reversals = 0;
  const active = CUES.filter((cue) => !schedule[cue.id].skipped)
    .map((cue) => ({ id: cue.id, ...schedule[cue.id] }))
    .filter((item) => item.start !== null);

  for (let index = 1; index < active.length; index += 1) {
    const previous = active[index - 1];
    const current = active[index];
    if (current.start < previous.start) reversals += 1;
    if (current.start < previous.end) overlaps += 1;
  }
  return { overlaps, reversals };
}

export function analyze(options) {
  const schedule = initialSchedule();
  const frames = [];
  let smooth = 0;
  let accepted = 0;
  let applied = 0;
  let hold = 0;
  let aligning = false;
  let lastLogAt = -1;
  let previousAudit = { overlaps: 0, reversals: 0 };
  const correctionLog = [];
  const skipped = [];
  let stepAcceptedAt = null;
  let spikeShiftPeak = 0;
  let appliedAtSpike = 0;
  let overlapEvents = 0;
  let reversalEvents = 0;

  for (let index = 0; index <= Math.round(DURATION / DT); index += 1) {
    const t = ROUND(index * DT, 1);
    const raw = measuredOffset(t, options);
    const buffer = bufferHealth(t, options);
    smooth += (raw - smooth) * 0.12;
    const gap = smooth - accepted;
    if (!aligning && Math.abs(gap) > 0.24) hold += DT;
    else if (!aligning) hold = 0;

    if (!aligning && hold >= SCENARIO.persistenceSeconds) aligning = true;
    if (aligning) {
      accepted += clamp(gap, -0.052, 0.052);
      if (stepAcceptedAt === null && accepted > 0.16) stepAcceptedAt = t;
      if (Math.abs(gap) < 0.025) {
        aligning = false;
        hold = 0;
      }
    }

    const before = applied;
    applied += clamp(accepted - applied, -0.042, 0.042);
    const appliedDelta = applied - before;

    if (t === SCENARIO.spikeAt) appliedAtSpike = applied;
    if (t >= SCENARIO.spikeAt && t <= SCENARIO.spikeAt + 2.5) {
      spikeShiftPeak = Math.max(spikeShiftPeak, Math.abs(applied - appliedAtSpike));
    }

    const activeCue = layoutFrame(t, applied, schedule, options);
    const audit = auditFrame(schedule);
    if (audit.overlaps > 0 && previousAudit.overlaps === 0) overlapEvents += 1;
    if (audit.reversals > 0 && previousAudit.reversals === 0) reversalEvents += 1;
    previousAudit = audit;

    for (const cue of CUES) {
      const item = schedule[cue.id];
      if (item.skipped && !skipped.some((entry) => entry.id === cue.id)) {
        skipped.push({
          id: cue.id,
          text: cue.text,
          originalStart: cue.start,
          arrivalAt: cue.arrivedAt,
          deadline: item.skipTime,
          reason: item.skipReason,
          detail: item.skipDetail,
        });
      }
    }

    if (
      t - lastLogAt >= 0.4 &&
      Math.abs(appliedDelta) > 0.002 &&
      Math.abs(accepted - applied) > 0.025
    ) {
      correctionLog.push({
        time: t,
        delta: appliedDelta,
        applied,
        residual: smooth - applied,
        kind: "correction",
      });
      lastLogAt = t;
    }

    frames.push({
      t,
      raw,
      smooth,
      accepted,
      applied,
      residual: smooth - applied,
      buffer,
      schedule: Object.fromEntries(
        CUES.map((cue) => {
          const item = schedule[cue.id];
          return [
            cue.id,
            {
              recognized: item.recognized,
              skipped: item.skipped,
              start: item.start,
              end: item.end,
              delta: item.delta,
              gap: item.gap,
              status: item.status,
              skipTime: item.skipTime,
              skipReason: item.skipReason,
              skipDetail: item.skipDetail,
            },
          ];
        }),
      ),
      activeCueId: activeCue?.id ?? null,
      audit,
      lockedCount: CUES.filter((cue) => {
        const item = schedule[cue.id];
        return !item.skipped && item.start !== null && t >= item.start;
      }).length,
      upcomingCount: CUES.filter((cue) => schedule[cue.id].status === "upcoming").length,
    });
  }

  const final = frames[frames.length - 1];
  const stepStart = SCENARIO.stepAt;
  let convergenceAt = null;
  if (options.step) {
    for (let index = Math.round(stepStart / DT); index < frames.length; index += 1) {
      const window = frames.slice(index, index + 11);
      if (window.every((frame) => Math.abs(frame.applied - SCENARIO.stepDelay) <= 0.08)) {
        convergenceAt = window[0].t;
        break;
      }
    }
  }

  const stableGaps = CUES.flatMap((cue) => {
    const item = final.schedule[cue.id];
    return item.gap !== null && item.gap > 0 ? [item.gap] : [];
  });
  const maxGap = stableGaps.length ? Math.max(...stableGaps) : 0;
  const minGap = stableGaps.length ? Math.min(...stableGaps) : 0;

  if (options.spike) {
    correctionLog.push({
      time: SCENARIO.spikeAt + SCENARIO.spikeDuration,
      delta: 0,
      applied: appliedAtSpike,
      residual: 0,
      kind: "spike",
    });
  }
  if (options.late && skipped.length) {
    const late = skipped.find((entry) => entry.id === LATE_CUE_ID);
    if (late) {
      correctionLog.push({
        time: late.deadline,
        delta: 0,
        applied: final.applied,
        residual: 0,
        kind: "skip",
      });
    }
  }

  const report = {
    convergence: convergenceAt === null ? null : ROUND(convergenceAt - stepStart, 1),
    convergenceAt,
    acceptedAt: stepAcceptedAt,
    skipped,
    skippedCount: skipped.length,
    overlaps: overlapEvents,
    reversals: reversalEvents,
    spikeShift: ROUND(spikeShiftPeak * 1000, 0),
    maxGap: ROUND(maxGap, 2),
    minGap: ROUND(minGap, 2),
    correctionCount: correctionLog.filter((entry) => entry.kind === "correction").length,
    finalApplied: final.applied,
    finalResidual: final.residual,
    finalLocked: final.lockedCount,
    sceneCount: 5,
  };

  return { frames, correctionLog, report };
}

export function buildBufferLog(options = { step: true, spike: true }) {
  const entries = [];
  for (let index = 0; index < DURATION / 0.5; index += 1) {
    const time = ROUND(index * 0.5, 1);
    const buffer = bufferHealth(time, options);
    entries.push({
      time,
      bufferMs: Math.round(2200 + buffer * 2500),
      offsetMs: Math.round(measuredOffset(time, options) * 1000),
      state: buffer > 0.72 ? "stable" : "recovering",
    });
  }
  return entries;
}

export function frameAt(analysis, time) {
  const index = clamp(Math.round(time / DT), 0, analysis.frames.length - 1);
  return analysis.frames[index];
}
