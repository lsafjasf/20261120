"""Tiny dependency-free SVG line/bar chart generator."""

from xml.sax.saxutils import escape


class LineChart:
    def __init__(self, width=900, height=420, title="", xlabel="", ylabel=""):
        self.w = width
        self.h = height
        self.m = dict(l=64, r=20, t=44, b=52)
        self.title = title
        self.xlabel = xlabel
        self.ylabel = ylabel
        self.series = []
        self.markers = []
        self._xlim = None
        self._ylim = None

    def set_xlim(self, lo, hi):
        self._xlim = (lo, hi)

    def set_ylim(self, lo, hi):
        self._ylim = (lo, hi)

    def add_series(self, xs, ys, color="#2563eb", width=1.6, label="",
                   dashed=False):
        self.series.append(dict(xs=list(xs), ys=list(ys), color=color,
                                width=width, label=label, dashed=dashed))

    def add_marker(self, x, y, color="#dc2626", label=""):
        self.markers.append(dict(x=x, y=y, color=color, label=label))

    def _bounds(self):
        all_x = [v for s in self.series for v in s["xs"]]
        all_y = [v for s in self.series for v in s["ys"]]
        xlo, xhi = self._xlim or (min(all_x), max(all_x))
        ylo, yhi = self._ylim or (min(all_y), max(all_y))
        if yhi == ylo:
            yhi += 1.0
            ylo -= 1.0
        pad = (yhi - ylo) * 0.05
        return xlo, xhi, ylo - pad, yhi + pad

    def save(self, path):
        xlo, xhi, ylo, yhi = self._bounds()
        pw = self.w - self.m["l"] - self.m["r"]
        ph = self.h - self.m["t"] - self.m["b"]

        def px(x):
            return self.m["l"] + (x - xlo) / (xhi - xlo) * pw

        def py(y):
            return self.m["t"] + (1 - (y - ylo) / (yhi - ylo)) * ph

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
            f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
            f'font-family="Helvetica,Arial,sans-serif">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{self.w/2}" y="26" text-anchor="middle" '
            f'font-size="17" font-weight="600">{escape(self.title)}</text>',
        ]
        # gridlines + y ticks (5 divisions)
        for i in range(5):
            yv = ylo + (yhi - ylo) * i / 4
            yy = py(yv)
            parts.append(
                f'<line x1="{self.m["l"]}" y1="{yy:.1f}" '
                f'x2="{self.w-self.m["r"]}" y2="{yy:.1f}" '
                'stroke="#e5e7eb" stroke-width="1"/>')
            parts.append(
                f'<text x="{self.m["l"]-8}" y="{yy+4:.1f}" '
                f'text-anchor="end" font-size="11" fill="#374151">'
                f'{yv:.3g}</text>')
        # x ticks (6 divisions)
        for i in range(6):
            xv = xlo + (xhi - xlo) * i / 5
            xx = px(xv)
            parts.append(
                f'<text x="{xx:.1f}" y="{self.h-self.m["b"]+18}" '
                f'text-anchor="middle" font-size="11" fill="#374151">'
                f'{xv:.3g}</text>')
        parts.append(
            f'<line x1="{self.m["l"]}" y1="{self.m["t"]}" '
            f'x2="{self.m["l"]}" y2="{self.h-self.m["b"]}" '
            'stroke="#9ca3af"/>')
        parts.append(
            f'<line x1="{self.m["l"]}" y1="{self.h-self.m["b"]}" '
            f'x2="{self.w-self.m["r"]}" y2="{self.h-self.m["b"]}" '
            'stroke="#9ca3af"/>')
        for s in self.series:
            pts = " ".join(f"{px(x):.1f},{py(y):.1f}"
                           for x, y in zip(s["xs"], s["ys"]))
            dash = ' stroke-dasharray="5,4"' if s["dashed"] else ""
            parts.append(
                f'<polyline fill="none" stroke="{s["color"]}" '
                f'stroke-width="{s["width"]}"{dash} points="{pts}"/>')
        for mk in self.markers:
            parts.append(
                f'<circle cx="{px(mk["x"]):.1f}" cy="{py(mk["y"]):.1f}" '
                f'r="3.5" fill="{mk["color"]}"/>')
        parts.append(
            f'<text x="{self.w/2}" y="{self.h-10}" text-anchor="middle" '
            f'font-size="12" fill="#111827">{escape(self.xlabel)}</text>')
        parts.append(
            f'<text x="16" y="{self.h/2}" text-anchor="middle" '
            f'font-size="12" transform="rotate(-90 16 {self.h/2})" '
            f'fill="#111827">{escape(self.ylabel)}</text>')
        if any(s["label"] for s in self.series):
            lx = self.w - self.m["r"] - 10
            ly = self.m["t"] + 8
            for s in self.series:
                if not s["label"]:
                    continue
                parts.append(
                    f'<line x1="{lx-110}" y1="{ly}" x2="{lx-92}" '
                    f'y2="{ly}" stroke="{s["color"]}" stroke-width="2"/>')
                parts.append(
                    f'<text x="{lx-86}" y="{ly+4}" font-size="11" '
                    f'fill="#111827">{escape(s["label"])}</text>')
                ly += 16
        parts.append("</svg>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts))


class BarChart:
    def __init__(self, width=760, height=380, title="", xlabel="", ylabel=""):
        self.w, self.h = width, height
        self.m = dict(l=64, r=20, t=44, b=60)
        self.title, self.xlabel, self.ylabel = title, xlabel, ylabel
        self.bars = []

    def add_bar(self, label, value, color="#2563eb"):
        self.bars.append((label, value, color))

    def save(self, path):
        pw = self.w - self.m["l"] - self.m["r"]
        ph = self.h - self.m["t"] - self.m["b"]
        ymax = max((v for _, v, _ in self.bars), default=1.0) * 1.15
        n = max(len(self.bars), 1)
        bw = pw / n * 0.62
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
            f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
            'font-family="Helvetica,Arial,sans-serif">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{self.w/2}" y="26" text-anchor="middle" '
            f'font-size="17" font-weight="600">{escape(self.title)}</text>',
        ]
        for i in range(5):
            yv = ymax * i / 4
            yy = self.m["t"] + (1 - i / 4) * ph
            parts.append(
                f'<line x1="{self.m["l"]}" y1="{yy:.1f}" '
                f'x2="{self.w-self.m["r"]}" y2="{yy:.1f}" '
                'stroke="#e5e7eb"/>')
            parts.append(
                f'<text x="{self.m["l"]-8}" y="{yy+4:.1f}" '
                f'text-anchor="end" font-size="11">{yv:.3g}</text>')
        for i, (label, value, color) in enumerate(self.bars):
            cx = self.m["l"] + pw * (i + 0.5) / n
            bh = value / ymax * ph
            y = self.m["t"] + ph - bh
            parts.append(
                f'<rect x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{color}" rx="3"/>')
            parts.append(
                f'<text x="{cx:.1f}" y="{y-5:.1f}" text-anchor="middle" '
                f'font-size="11">{value:.3g}</text>')
            parts.append(
                f'<text x="{cx:.1f}" y="{self.h-self.m["b"]+18}" '
                f'text-anchor="middle" font-size="11">{escape(label)}</text>')
        parts.append(
            f'<text x="{self.w/2}" y="{self.h-8}" text-anchor="middle" '
            f'font-size="12">{escape(self.xlabel)}</text>')
        parts.append(
            f'<text x="16" y="{self.h/2}" text-anchor="middle" '
            f'font-size="12" transform="rotate(-90 16 {self.h/2})">'
            f'{escape(self.ylabel)}</text></svg>')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts))
