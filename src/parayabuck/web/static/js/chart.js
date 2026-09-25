// ParayaBuck chart.js — dependency-free responsive SVG line chart with a hover
// crosshair. Colours come from CSS classes so both themes work without redraw.
(function (global) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  let gradientSeq = 0;

  function svgEl(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }

  function niceStep(span, count) {
    const raw = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  }

  function nearestIndex(points, t) {
    let lo = 0;
    let hi = points.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (points[mid].t < t) lo = mid; else hi = mid;
    }
    return Math.abs(points[lo].t - t) <= Math.abs(points[hi].t - t) ? lo : hi;
  }

  // series: [{cls: "main"|"bench", area: bool, points: [{t: ms, v: number}]}]
  function lineChart(container, options) {
    const state = { options: options };
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;

    function draw() {
      const opts = state.options;
      container.replaceChildren();
      const series = (opts.series || []).filter(function (s) { return s.points && s.points.length > 1; });
      if (!series.length) {
        const empty = document.createElement("div");
        empty.className = "chart-empty";
        empty.textContent = opts.emptyText || "Gösterilecek veri yok";
        container.appendChild(empty);
        return;
      }
      const width = Math.max(240, container.clientWidth);
      const height = Math.max(160, container.clientHeight);
      const pad = { l: 8, r: 58, t: 12, b: 26 };
      const iw = width - pad.l - pad.r;
      const ih = height - pad.t - pad.b;

      let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
      series.forEach(function (s) {
        s.points.forEach(function (p) {
          if (p.t < tMin) tMin = p.t;
          if (p.t > tMax) tMax = p.t;
          if (p.v < vMin) vMin = p.v;
          if (p.v > vMax) vMax = p.v;
        });
      });
      if (opts.includeZero) { vMin = Math.min(vMin, 0); vMax = Math.max(vMax, 0); }
      if (vMax - vMin < 1e-9) { vMax += 1; vMin -= 1; }
      const step = niceStep(vMax - vMin, 4);
      vMin = Math.floor(vMin / step) * step;
      vMax = Math.ceil(vMax / step) * step;
      const tSpan = Math.max(1, tMax - tMin);
      const x = function (t) { return pad.l + ((t - tMin) / tSpan) * iw; };
      const y = function (v) { return pad.t + (1 - (v - vMin) / (vMax - vMin)) * ih; };

      const svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, role: "img" });
      const gradId = "pbgrad" + (++gradientSeq);
      const defs = svgEl("defs");
      const grad = svgEl("linearGradient", { id: gradId, x1: "0", y1: "0", x2: "0", y2: "1" });
      grad.appendChild(svgEl("stop", { offset: "0%", class: "grad-start" }));
      grad.appendChild(svgEl("stop", { offset: "100%", class: "grad-end" }));
      defs.appendChild(grad);
      svg.appendChild(defs);

      const grid = svgEl("g", { class: "chart-grid" });
      const axis = svgEl("g", { class: "chart-axis" });
      for (let v = vMin; v <= vMax + step / 2; v += step) {
        const yy = y(v);
        grid.appendChild(svgEl("line", { x1: pad.l, x2: pad.l + iw, y1: yy, y2: yy }));
        const label = svgEl("text", { x: width - 4, y: yy + 4, "text-anchor": "end" });
        label.textContent = opts.yFormat ? opts.yFormat(v) : String(v);
        axis.appendChild(label);
      }
      const ticks = Math.max(2, Math.min(6, Math.floor(iw / 110)));
      for (let i = 0; i <= ticks; i++) {
        const t = tMin + (tSpan * i) / ticks;
        const anchor = i === 0 ? "start" : i === ticks ? "end" : "middle";
        const label = svgEl("text", { x: x(t), y: height - 6, "text-anchor": anchor });
        label.textContent = opts.xFormat ? opts.xFormat(t, tSpan) : new Date(t).toLocaleDateString();
        axis.appendChild(label);
      }
      svg.appendChild(grid);
      if (vMin < 0 && vMax > 0) svg.appendChild(svgEl("line", { class: "chart-zero", x1: pad.l, x2: pad.l + iw, y1: y(0), y2: y(0) }));
      svg.appendChild(axis);

      // bench first so the portfolio line sits on top
      series.slice().sort(function (a) { return a.cls === "main" ? 1 : -1; }).forEach(function (s) {
        const d = s.points.map(function (p, i) { return (i ? "L" : "M") + x(p.t).toFixed(1) + " " + y(p.v).toFixed(1); }).join("");
        if (s.area) {
          const base = y(Math.max(vMin, Math.min(vMax, opts.areaBase === undefined ? vMin : opts.areaBase)));
          const first = s.points[0];
          const last = s.points[s.points.length - 1];
          svg.appendChild(svgEl("path", {
            class: "chart-area",
            fill: "url(#" + gradId + ")",
            d: d + "L" + x(last.t).toFixed(1) + " " + base + "L" + x(first.t).toFixed(1) + " " + base + "Z"
          }));
        }
        svg.appendChild(svgEl("path", { class: "chart-line " + s.cls, d: d }));
      });

      const cross = svgEl("line", { class: "chart-cross", y1: pad.t, y2: pad.t + ih, visibility: "hidden" });
      svg.appendChild(cross);
      const markers = series.map(function (s) {
        const m = svgEl("circle", { class: "chart-marker " + s.cls, r: 4.5, visibility: "hidden" });
        svg.appendChild(m);
        return m;
      });
      const overlay = svgEl("rect", { x: pad.l, y: pad.t, width: iw, height: ih, fill: "transparent" });
      svg.appendChild(overlay);
      container.appendChild(svg);
      container.appendChild(tip);

      const main = series.find(function (s) { return s.cls === "main"; }) || series[0];
      const hide = function () {
        cross.setAttribute("visibility", "hidden");
        markers.forEach(function (m) { m.setAttribute("visibility", "hidden"); });
        tip.hidden = true;
      };
      const move = function (evt) {
        const rect = svg.getBoundingClientRect();
        const scale = width / rect.width;
        const px = (evt.clientX - rect.left) * scale;
        const t = tMin + ((px - pad.l) / iw) * tSpan;
        const idx = nearestIndex(main.points, t);
        const at = main.points[idx].t;
        const cx = x(at);
        cross.setAttribute("x1", cx);
        cross.setAttribute("x2", cx);
        cross.setAttribute("visibility", "visible");
        const hits = series.map(function (s, i) {
          const p = s.points[nearestIndex(s.points, at)];
          markers[i].setAttribute("cx", x(p.t));
          markers[i].setAttribute("cy", y(p.v));
          markers[i].setAttribute("visibility", "visible");
          return { cls: s.cls, point: p };
        });
        if (opts.tip) {
          tip.replaceChildren.apply(tip, opts.tip(at, hits));
          tip.hidden = false;
          const top = Math.min.apply(null, hits.map(function (h) { return y(h.point.v); })) / scale;
          const left = Math.max(80, Math.min(rect.width - 80, cx / scale));
          tip.style.left = left + "px";
          tip.style.top = Math.max(60, top - 10) + "px";
        }
      };
      overlay.addEventListener("pointermove", move);
      overlay.addEventListener("pointerdown", move);
      overlay.addEventListener("pointerleave", hide);
    }

    draw();
    let frame = 0;
    if ("ResizeObserver" in global) {
      const ro = new ResizeObserver(function () {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(draw);
      });
      ro.observe(container);
    }
    return {
      update: function (options) { state.options = options; draw(); }
    };
  }

  global.PBChart = { line: lineChart };
})(window);
