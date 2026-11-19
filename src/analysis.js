import { noteName } from "./midi.js";

export const DEFAULT_TEMPO = 500000;

function eventPriority(event) {
  if (event.type === "tempo" || event.type === "timeSignature") return 0;
  if (event.type === "controlChange" && event.data.controller === 64) {
    return event.data.value < 64 ? 10 : 40;
  }
  if (event.type === "noteOff") return 20;
  if (event.type === "noteOn" && event.data.velocity === 0) return 20;
  if (event.type === "noteOn") return 30;
  return 50;
}

export function buildTempoMap(events, ppq) {
  const tempoEvents = events
    .filter((event) => event.type === "tempo")
    .sort(
      (left, right) =>
        left.tick - right.tick ||
        left.trackIndex - right.trackIndex ||
        left.eventNumber - right.eventNumber,
    );
  const points = [];
  const appendOrReplace = (point) => {
    if (points.length > 0 && points[points.length - 1].tick === point.tick) {
      points[points.length - 1] = point;
    } else {
      points.push(point);
    }
  };

  appendOrReplace({ tick: 0, microseconds: DEFAULT_TEMPO, source: null });
  for (const event of tempoEvents) {
    appendOrReplace({
      tick: event.tick,
      microseconds: event.data.microseconds,
      source: event,
    });
  }

  let elapsedSeconds = 0;
  const segments = points.map((point, index) => {
    if (index > 0) {
      const previous = points[index - 1];
      elapsedSeconds +=
        ((point.tick - previous.tick) * previous.microseconds) / (ppq * 1000000);
    }
    return {
      ...point,
      startTick: point.tick,
      startSeconds: elapsedSeconds,
      bpm: 60000000 / point.microseconds,
    };
  });

  const findSegment = (tick) => {
    let low = 0;
    let high = segments.length - 1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (segments[middle].startTick <= tick) low = middle + 1;
      else high = middle - 1;
    }
    return segments[Math.max(0, high)];
  };

  return {
    ppq,
    segments,
    tickToSeconds(tick) {
      const segment = findSegment(Math.max(0, tick));
      return (
        segment.startSeconds +
        ((Math.max(0, tick) - segment.startTick) * segment.microseconds) /
          (ppq * 1000000)
      );
    },
    secondsToTick(seconds) {
      const safeSeconds = Math.max(0, seconds);
      let low = 0;
      let high = segments.length - 1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if (segments[middle].startSeconds <= safeSeconds) low = middle + 1;
        else high = middle - 1;
      }
      const segment = segments[Math.max(0, high)];
      return (
        segment.startTick +
        ((safeSeconds - segment.startSeconds) * ppq * 1000000) /
          segment.microseconds
      );
    },
    tempoAtTick(tick) {
      return findSegment(Math.max(0, tick));
    },
  };
}

export function mergeEvents(parsed) {
  const events = [];
  for (const track of parsed.tracks) {
    for (const event of track.events) {
      events.push({
        ...event,
        trackName: track.name,
        priority: eventPriority(event),
      });
    }
  }
  events.sort(
    (left, right) => {
      if (left.tick !== right.tick) return left.tick - right.tick;
      if (left.trackIndex === right.trackIndex) {
        return left.eventNumber - right.eventNumber;
      }
      return (
        left.priority - right.priority ||
        left.trackIndex - right.trackIndex ||
        left.eventNumber - right.eventNumber
      );
    },
  );
  events.forEach((event, index) => {
    event.globalNumber = index + 1;
  });
  return events;
}

function buildPedalIntervals(events) {
  const byChannel = new Map();
  for (const event of events) {
    if (event.type !== "controlChange" || event.data.controller !== 64) continue;
    if (!byChannel.has(event.channel)) byChannel.set(event.channel, []);
    byChannel.get(event.channel).push(event);
  }

  const intervals = [];
  for (const [channel, pedalEvents] of byChannel) {
    pedalEvents.sort(
      (left, right) =>
        left.tick - right.tick ||
        left.trackIndex - right.trackIndex ||
        left.eventNumber - right.eventNumber,
    );
    let openEvent = null;
    for (const event of pedalEvents) {
      const isDown = event.data.value >= 64;
      if (isDown && openEvent === null) {
        openEvent = event;
      } else if (!isDown && openEvent !== null) {
        if (event.tick >= openEvent.tick) {
          intervals.push({
            channel,
            startTick: openEvent.tick,
            endTick: event.tick,
            downEvent: openEvent,
            upEvent: event,
            open: false,
          });
        }
        openEvent = null;
      }
    }
    if (openEvent !== null) {
      intervals.push({
        channel,
        startTick: openEvent.tick,
        endTick: Number.POSITIVE_INFINITY,
        downEvent: openEvent,
        upEvent: null,
        open: true,
      });
    }
  }
  return intervals.sort(
    (left, right) => left.startTick - right.startTick || left.channel - right.channel,
  );
}

function pedalAt(intervals, channel, tick) {
  let found = null;
  for (const interval of intervals) {
    if (interval.startTick > tick) break;
    if (
      interval.channel === channel &&
      interval.startTick <= tick &&
      tick < interval.endTick
    ) {
      found = interval;
    }
  }
  return found;
}

function noteKey(channel, pitch) {
  return `${channel}:${pitch}`;
}

export function analyzeMidi(parsed) {
  const events = mergeEvents(parsed);
  const maxTick = events.reduce((maximum, event) => Math.max(maximum, event.tick), 0);
  const tempoMap = buildTempoMap(events, parsed.ppq);
  for (const event of events) {
    event.seconds = tempoMap.tickToSeconds(event.tick);
  }

  const pedalIntervals = buildPedalIntervals(events);
  for (const interval of pedalIntervals) {
    interval.endTickResolved = Math.min(interval.endTick, maxTick);
    interval.endSeconds = tempoMap.tickToSeconds(interval.endTickResolved);
  }

  const activeVoices = new Map();
  const notes = [];
  const anomalies = [];
  let noteOrder = 0;
  let orphanCount = 0;

  const closeVoice = (voice, endEvent, endTick, reason) => {
    const keyEndTick = Math.max(voice.startTick, endTick);
    const sustainInterval = pedalAt(pedalIntervals, voice.channel, keyEndTick);
    let sustainEndTick =
      sustainInterval && sustainInterval.endTickResolved > keyEndTick
        ? sustainInterval.endTickResolved
        : null;

    const note = {
      id: `n${noteOrder + 1}`,
      order: noteOrder + 1,
      channel: voice.channel,
      pitch: voice.pitch,
      name: noteName(voice.pitch),
      velocity: voice.velocity,
      startTick: voice.startTick,
      endTick: keyEndTick,
      sustainEndTick,
      startSeconds: tempoMap.tickToSeconds(voice.startTick),
      endSeconds: tempoMap.tickToSeconds(keyEndTick),
      sustainEndSeconds:
        sustainEndTick === null ? null : tempoMap.tickToSeconds(sustainEndTick),
      startEvent: voice.startEvent,
      endEvent,
      sustainEvent: sustainInterval?.upEvent ?? null,
      endReason: reason,
      retriggered: reason === "retrigger",
      missingNoteOff: reason === "missing-note-off",
      anomalyCodes: [],
    };
    if (note.missingNoteOff) note.anomalyCodes.push("missing-note-off");
    noteOrder += 1;
    notes.push(note);
    return note;
  };

  for (const event of events) {
    const isNoteOn = event.type === "noteOn" && event.data.velocity > 0;
    const isNoteOff =
      event.type === "noteOff" ||
      (event.type === "noteOn" && event.data.velocity === 0);
    if (!isNoteOn && !isNoteOff) continue;

    const pitch = event.data.note;
    const key = noteKey(event.channel, pitch);
    const voices = activeVoices.get(key) ?? [];

    if (isNoteOn) {
      if (voices.length > 0) {
        const previous = voices.shift();
        closeVoice(previous, event, event.tick, "retrigger");
      }
      voices.push({
        channel: event.channel,
        pitch,
        velocity: event.data.velocity,
        startTick: event.tick,
        startEvent: event,
      });
      activeVoices.set(key, voices);
      continue;
    }

    if (voices.length === 0) {
      orphanCount += 1;
      anomalies.push({
        id: `a-orphan-${orphanCount}`,
        severity: "error",
        code: "orphan-note-off",
        title: "孤立的 Note Off",
        detail: `${noteName(pitch)} 在 tick ${event.tick} 收到 Note Off，但没有对应的 Note On。`,
        tick: event.tick,
        seconds: event.seconds,
        trackIndex: event.trackIndex,
        trackName: event.trackName,
        eventNumber: event.eventNumber,
        globalNumber: event.globalNumber,
        noteId: null,
      });
      continue;
    }

    const voice = voices.shift();
    closeVoice(voice, event, event.tick, "note-off");
    if (voices.length === 0) activeVoices.delete(key);
  }

  for (const voices of activeVoices.values()) {
    for (const voice of voices) closeVoice(voice, null, maxTick, "missing-note-off");
  }

  notes.sort(
    (left, right) =>
      left.startTick - right.startTick ||
      left.pitch - right.pitch ||
      left.order - right.order,
  );

  const notesByKey = new Map();
  for (const note of notes) {
    const key = noteKey(note.channel, note.pitch);
    if (!notesByKey.has(key)) notesByKey.set(key, []);
    notesByKey.get(key).push(note);
  }
  for (const group of notesByKey.values()) {
    group.sort((left, right) => left.startTick - right.startTick || left.order - right.order);
    for (let index = 0; index < group.length - 1; index += 1) {
      const note = group[index];
      const next = group[index + 1];
      if (
        note.sustainEndTick !== null &&
        next.startTick >= note.endTick &&
        next.startTick < note.sustainEndTick
      ) {
        note.sustainEndTick = next.startTick > note.endTick ? next.startTick : null;
        note.sustainEndSeconds =
          note.sustainEndTick === null ? null : tempoMap.tickToSeconds(next.startTick);
        note.sustainEvent = null;
        note.sustainCutBy = next.id;
      }
    }
  }

  for (const note of notes) {
    if (note.missingNoteOff) {
      anomalies.push({
        id: `a-missing-${note.id}`,
        severity: "error",
        code: "missing-note-off",
        title: "缺少 Note Off",
        detail: `${note.name}（Ch ${note.channel + 1}）从 tick ${note.startTick} 起持续到曲末，未找到对应的 Note Off。`,
        tick: note.startTick,
        seconds: note.startSeconds,
        trackIndex: note.startEvent.trackIndex,
        trackName: note.startEvent.trackName,
        eventNumber: note.startEvent.eventNumber,
        globalNumber: note.startEvent.globalNumber,
        noteId: note.id,
      });
    }
    if (note.endTick === note.startTick) {
      note.anomalyCodes.push("zero-length");
      anomalies.push({
        id: `a-zero-${note.id}`,
        severity: "warning",
        code: "zero-length",
        title: "零长度音符",
        detail: `${note.name} 的 Note On 与 Note Off 位于同一 tick，且没有可听时值。`,
        tick: note.startTick,
        seconds: note.startSeconds,
        trackIndex: note.startEvent.trackIndex,
        trackName: note.startEvent.trackName,
        eventNumber: note.startEvent.eventNumber,
        globalNumber: note.startEvent.globalNumber,
        noteId: note.id,
      });
    }
    if (note.retriggered) {
      note.anomalyCodes.push("retrigger-overlap");
      anomalies.push({
        id: `a-retrigger-${note.id}`,
        severity: "info",
        code: "retrigger-overlap",
        title: "同音提前重触发",
        detail: `${note.name} 在缺少 Note Off 的情况下被新的 Note On 截断；已按重触发处理。`,
        tick: note.endTick,
        seconds: note.endSeconds,
        trackIndex: note.endEvent.trackIndex,
        trackName: note.endEvent.trackName,
        eventNumber: note.endEvent.eventNumber,
        globalNumber: note.endEvent.globalNumber,
        noteId: note.id,
      });
    }
  }

  anomalies.sort(
    (left, right) =>
      left.tick - right.tick ||
      (left.severity === "error" ? -1 : left.severity === "warning" ? 0 : 1) -
        (right.severity === "error" ? -1 : right.severity === "warning" ? 0 : 1),
  );

  const sustainCount = notes.filter(
    (note) => note.sustainEndTick !== null && note.sustainEndTick > note.endTick,
  ).length;
  const pitches = notes.map((note) => note.pitch);
  const minPitch = pitches.length ? Math.min(...pitches) : 48;
  const maxPitch = pitches.length ? Math.max(...pitches) : 72;
  const errorCount = anomalies.filter((item) => item.severity === "error").length;

  return {
    ...parsed,
    events,
    maxTick,
    durationSeconds: tempoMap.tickToSeconds(maxTick),
    tempoMap,
    tempoMarkers: tempoMap.segments,
    pedalIntervals,
    notes,
    anomalies,
    stats: {
      noteCount: notes.length,
      sustainCount,
      animationCount: anomalies.length,
      errorCount,
      tempoCount: Math.max(0, tempoMap.segments.length - 1),
      pedalCount: pedalIntervals.length,
      eventCount: events.length,
      minPitch,
      maxPitch,
    },
  };
}

export function eventKind(event) {
  if (event.type === "tempo" || event.type === "timeSignature") return "tempo";
  if (event.type === "controlChange" && event.data.controller === 64) return "pedal";
  if (event.type === "noteOn" || event.type === "noteOff") return "note";
  return "other";
}

export function eventTypeLabel(event) {
  if (event.type === "noteOn") return "NOTE ON";
  if (event.type === "noteOff") return "NOTE OFF";
  if (event.type === "controlChange") return `CC ${event.data.controller}`;
  if (event.type === "tempo") return "TEMPO";
  if (event.type === "timeSignature") return "TIME SIG";
  if (event.type === "trackName") return "TRACK NAME";
  if (event.type === "programChange") return "PROGRAM";
  if (event.type === "endOfTrack") return "END";
  if (event.type === "sysex") return "SYSEX";
  if (event.type === "meta") return "META";
  return event.type.toUpperCase();
}
