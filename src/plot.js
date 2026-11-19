// Minimal dependency-free line chart renderer producing standalone SVG files.

const PALETTE = [
  "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e",
  "#9467bd", "#8c564b", "#17becf", "#7f7f7f",
];

export function lineChart({
  width = 900,
  height = 560,
  margin = { top: 40, right: 180, bottom: 60, left: 70 },
  xValues,
  series,
  xScale = "log",
  xLabel = "Frequency (Hz)",
  yLabel = "Magnitude (dB)",
  title = "",
  yDomain,
}) {
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const xs = xValues;
  const xMin = xs[0];
  const xMax = xs[xs.length - 1];
  const mapX = (v) =>
    xScale === "log"
      ? margin.left +
        ((Math.log10(v) - Math.log10(xMin)) /
          (Math.log10(xMax) - Math.log10(xMin))) *
          innerW
      : margin.left + ((v - xMin) / (xMax - xMin)) * innerW;

  let yMin;
  let yMax;
  if (yDomain) {
    [yMin, yMax] = yDomain;
  } else {
    const all = series.flatMap((s) => s.y);
    yMin = Math.floor(Math.min(...all) / 5) * 5;
    yMax = Math.ceil(Math.max(...all) / 5) * 5;
  }
  const mapY = (v) =>
    margin.top + innerH - ((v - yMin) / (yMax - yMin)) * innerH;

  const ticks = xScale === "log" ? logTicks(xMin, xMax) : linearTicks(xMin, xMax);
  const yTicks = linearTicks(yMin, yMax);

  const parts = [];
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" font-family="Helvetica,Arial,sans-serif">`,
  );
  parts.push(
    `<rect width="100%" height="100%" fill="white"/>`,
    `<text x="${width / 2}" y="24" text-anchor="middle" font-size="18" font-weight="bold">${escapeXml(title)}</text>`,
  );
  // Grid and axes.
  for (const t of yTicks) {
    const y = mapY(t);
    parts.push(
      `<line x1="${margin.left}" y1="${y}" x2="${margin.left + innerW}" y2="${y}" stroke="#e6e6e6"/>`,
      `<text x="${margin.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#555">${t}</text>`,
    );
  }
  for (const t of ticks) {
    const x = mapX(t);
    parts.push(
      `<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${margin.top + innerH}" stroke="#f0f0f0"/>`,
      `<text x="${x}" y="${margin.top + innerH + 18}" text-anchor="middle" font-size="11" fill="#555">${formatTick(t)}</text>`,
    );
  }
  parts.push(
    `<line x1="${margin.left}" y1="${margin.top + innerH}" x2="${margin.left + innerW}" y2="${margin.top + innerH}" stroke="#333"/>`,
    `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerH}" stroke="#333"/>`,
    `<text x="${margin.left + innerW / 2}" y="${height - 14}" text-anchor="middle" font-size="13">${escapeXml(xLabel)}</text>`,
    `<text transform="translate(20,${margin.top + innerH / 2}) rotate(-90)" text-anchor="middle" font-size="13">${escapeXml(yLabel)}</text>`,
  );
  // Series polylines.
  series.forEach((s, idx) => {
    const color = s.color || PALETTE[idx % PALETTE.length];
    const points = s.y
      .map((y, i) => `${mapX(xs[i]).toFixed(2)},${mapY(y).toFixed(2)}`)
      .join(" ");
    parts.push(
      `<polyline fill="none" stroke="${color}" stroke-width="${s.dashed ? 1.6 : 2.2}" ${s.dashed ? 'stroke-dasharray="6,4"' : ""} points="${points}"/>`,
    );
  });
  // Legend.
  series.forEach((s, idx) => {
    const color = s.color || PALETTE[idx % PALETTE.length];
    const y = margin.top + 18 + idx * 20;
    const x = margin.left + innerW + 14;
    parts.push(
      `<line x1="${x}" y1="${y}" x2="${x + 26}" y2="${y}" stroke="${color}" stroke-width="2.6" ${s.dashed ? 'stroke-dasharray="6,4"' : ""}/>`,
      `<text x="${x + 32}" y="${y + 4}" font-size="12" fill="#333">${escapeXml(s.name)}</text>`,
    );
  });
  parts.push("</svg>");
  return parts.join("\n");
}

function logTicks(min, max) {
  const ticks = [];
  const startDecade = Math.floor(Math.log10(min));
  const endDecade = Math.ceil(Math.log10(max));
  for (let d = startDecade; d <= endDecade; d++) {
    for (let m = 1; m <= 9; m++) {
      const v = m * 10 ** d;
      if (v >= min && v <= max) ticks.push(v);
    }
  }
  return ticks;
}

function linearTicks(min, max) {
  const span = max - min;
  const step = niceStep(span / 8);
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

function niceStep(raw) {
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

function formatTick(v) {
  return v >= 1000 ? `${v / 1000}k` : `${v}`;
}

function escapeXml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
