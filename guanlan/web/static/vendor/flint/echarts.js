// src/core/encoding-overrides.ts
function applyEncodingOverrides(template, encodings, chartProperties) {
  const actions = template.encodingActions;
  if (!actions || actions.length === 0 || !chartProperties) return encodings;
  let result = encodings;
  for (const action of actions) {
    const override = chartProperties[action.key];
    if (override !== void 0) {
      result = action.set(result, override);
    }
  }
  return result;
}

// src/core/aggregate.ts
function applyAggregation(encodings, data) {
  if (!data || data.length === 0) return data;
  const specs = [];
  for (const enc of Object.values(encodings)) {
    if (!enc || !enc.aggregate) continue;
    const op = enc.aggregate;
    if (op !== "count" && !enc.field) continue;
    const target = op === "count" ? "_count" : `${enc.field}_${op}`;
    specs.push({ field: enc.field, op, target });
  }
  if (specs.length === 0) return data;
  const firstRow = data[0];
  const allPresent = specs.every(
    (s) => Object.prototype.hasOwnProperty.call(firstRow, s.target)
  );
  if (allPresent) return data;
  const groupFields = [];
  const seen = /* @__PURE__ */ new Set();
  for (const enc of Object.values(encodings)) {
    if (!enc || enc.aggregate || !enc.field) continue;
    if (seen.has(enc.field)) continue;
    seen.add(enc.field);
    groupFields.push(enc.field);
  }
  const groups = /* @__PURE__ */ new Map();
  for (const row of data) {
    const key = JSON.stringify(groupFields.map((f) => row[f] ?? null));
    let bucket = groups.get(key);
    if (!bucket) {
      bucket = [];
      groups.set(key, bucket);
    }
    bucket.push(row);
  }
  const toNum = (v) => typeof v === "number" ? v : Number(v);
  const reduceOp = (rows, spec) => {
    if (spec.op === "count") return rows.length;
    const nums = rows.map((r) => toNum(r[spec.field])).filter((v) => Number.isFinite(v));
    if (nums.length === 0) return 0;
    const sum = nums.reduce((a, b) => a + b, 0);
    return spec.op === "sum" ? sum : sum / nums.length;
  };
  const out = [];
  for (const rows of groups.values()) {
    const head = rows[0];
    const aggregated = {};
    for (const f of groupFields) aggregated[f] = head[f];
    for (const spec of specs) {
      const val = reduceOp(rows, spec);
      aggregated[spec.target] = val;
      if (spec.op !== "count" && spec.field) aggregated[spec.field] = val;
    }
    out.push(aggregated);
  }
  return out;
}

// src/core/chart-transitions.ts
var CHART_TRANSITIONS = {
  // ── Categorical comparison — D × M (§4.1) ──────────────────────────────
  "Bar Chart": [
    // Ordered-axis bridge into the trend family (§4.9). Only when the domain
    // axis is temporal/ordinal — an unordered nominal bar never sprouts a line.
    // orientDomainAxis:'x' re-orients a horizontal bar so time stays horizontal.
    { to: "Line Chart", label: "Line", requireOrderedAxis: true, orientDomainAxis: "x" },
    { to: "Area Chart", label: "Area", requireOrderedAxis: true, requireNonNegative: true, orientDomainAxis: "x" },
    // Same D×M signature, lighter ink.
    { to: "Lollipop Chart", label: "Lollipop" }
  ],
  "Lollipop Chart": [
    { to: "Bar Chart", label: "Bar" }
  ],
  "Grouped Bar Chart": [
    {
      to: "Stacked Bar Chart",
      label: "Stacked",
      route: { from: "group", to: "color", mode: "move" },
      requireDiscreteSource: true
    },
    // A 2-sided grouped bar reads as a population pyramid (mirrored).
    {
      to: "Pyramid Chart",
      label: "Pyramid",
      route: { from: "group", to: "color", mode: "move" },
      requireDiscreteSource: true,
      maxSourceCardinality: 2
    }
  ],
  "Stacked Bar Chart": [
    {
      to: "Grouped Bar Chart",
      label: "Grouped",
      route: { from: "color", to: "group", mode: "move" },
      requireDiscreteSource: true,
      maxSourceCardinality: 12
    }
  ],
  // Population pyramid = a 2-sided category × measure; its complement is the
  // side-by-side grouped bar (the 2 sides dodged instead of mirrored).
  "Pyramid Chart": [
    {
      to: "Grouped Bar Chart",
      label: "Grouped",
      route: { from: "color", to: "group", mode: "move" },
      requireDiscreteSource: true
    }
  ],
  // ── Trend over an ordered domain — T × M (§4.2) ────────────────────────
  "Line Chart": [
    { to: "Area Chart", label: "Area", requireNonNegative: true },
    // Back to discrete-period comparison; only readable with few ticks.
    { to: "Bar Chart", label: "Bar", maxCategoryCardinality: 30 },
    // Small-multiple trend strips (one per series) — needs a series. Route
    // the series onto `color` (from wherever it sits — color OR a column/row
    // facet) so the Sparkline template picks it up as its row series.
    { to: "Sparkline", label: "Sparklines", requireSeries: true, route: { from: "series", to: "color", mode: "move" } }
  ],
  "Area Chart": [
    { to: "Line Chart", label: "Line" },
    { to: "Bar Chart", label: "Bar", maxCategoryCardinality: 30 },
    { to: "Streamgraph", label: "Stream", requireSeries: true, requireNonNegative: true, route: { from: "series", to: "color", mode: "move" } }
  ],
  // Small-multiple trend table → a single overlaid multi-series line.
  "Sparkline": [
    { to: "Line Chart", label: "Line" }
  ],
  // Flowing composition → back to baseline-anchored trend / area. Both reads
  // are safe; note Streamgraph → Line is intentionally *one-directional* (there
  // is no Line → Streamgraph — see the note above).
  "Streamgraph": [
    { to: "Area Chart", label: "Area" },
    { to: "Line Chart", label: "Line" }
  ],
  // ── Two-measure relationship — M₁ × M₂ (§4.3) ──────────────────────────
  "Scatter Plot": [
    {
      to: "Strip Plot",
      label: "Jitter",
      route: { from: "series", to: "x", mode: "swap", spill: "color" }
    },
    // Add a fitted trend layer over the same cloud — only a clean
    // two-measure scatter (both axes quantitative, no size bubble).
    { to: "Regression", label: "Trend", requireBiaxialMeasure: true, requireNoSize: true }
  ],
  "Regression": [
    { to: "Scatter Plot", label: "Scatter" }
  ],
  "Strip Plot": [
    {
      to: "Scatter Plot",
      label: "Scatter",
      route: { from: "color", to: "x", mode: "swap", spill: "color" }
    },
    // A strip plot is a per-category distribution: box (summary) + violin
    // (density) are the same {x:category, y:measure} layout, no route.
    { to: "Boxplot", label: "Box" },
    { to: "Violin Plot", label: "Violin" }
  ],
  // ── Univariate distribution — M (§4.4) ─────────────────────────────────
  "Histogram": [
    { to: "Density Plot", label: "Density" },
    { to: "ECDF Plot", label: "ECDF" }
  ],
  "Density Plot": [
    { to: "Histogram", label: "Histogram" },
    { to: "ECDF Plot", label: "ECDF" }
  ],
  "ECDF Plot": [
    { to: "Histogram", label: "Histogram" },
    { to: "Density Plot", label: "Density" }
  ],
  "Boxplot": [
    { to: "Violin Plot", label: "Violin" },
    { to: "Strip Plot", label: "Strip" }
  ],
  "Violin Plot": [
    { to: "Boxplot", label: "Box" },
    { to: "Strip Plot", label: "Strip" }
  ]
};
function getChartTransitions(chart) {
  if (!chart) return [];
  return CHART_TRANSITIONS[chart] ?? [];
}

// src/core/pivot.ts
var DISCRETE_TYPES = /* @__PURE__ */ new Set(["nominal", "ordinal"]);
function isDiscrete(enc) {
  return !!enc?.field && !!enc.type && DISCRETE_TYPES.has(enc.type);
}
function isMeasure(enc) {
  return !!enc?.field && (enc.type === "quantitative" || !!enc.aggregate);
}
function isTemporal(enc) {
  return enc?.type === "temporal";
}
function temporalActsDiscrete(template) {
  return template.markCognitiveChannel !== "position";
}
function clone(encodings) {
  const out = {};
  for (const [ch, enc] of Object.entries(encodings)) {
    out[ch] = { ...enc };
  }
  return out;
}
function distinctCount(data, field) {
  if (!field || !Array.isArray(data)) return 0;
  const seen = /* @__PURE__ */ new Set();
  for (const row of data) {
    if (row && row[field] != null) seen.add(row[field]);
  }
  return seen.size;
}
function makeCartesianPivot(opts = {}) {
  return {
    key: opts.key ?? "pivot",
    label: opts.label ?? "View",
    transpose: opts.transpose ?? [],
    permute: opts.permute ?? [],
    shift: opts.shift ?? [],
    facetBudget: opts.facetBudget ?? 12,
    transitions: opts.transitions
  };
}
var CHANNEL_ORDER = ["x", "y", "color", "size", "group", "column", "row"];
function orderPair(a, b) {
  const ia = CHANNEL_ORDER.indexOf(a);
  const ib = CHANNEL_ORDER.indexOf(b);
  return ia <= ib ? [a, b] : [b, a];
}
var CHANNEL_DISPLAY = {
  x: "X",
  y: "Y",
  color: "Color",
  size: "Size",
  group: "Groups",
  column: "Columns",
  row: "Rows",
  detail: "Detail",
  opacity: "Opacity"
};
function chDisplay(ch) {
  return CHANNEL_DISPLAY[ch] ?? ch.charAt(0).toUpperCase() + ch.slice(1);
}
function changedChannels(a, b) {
  const out = /* @__PURE__ */ new Set();
  for (const ch of /* @__PURE__ */ new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (a[ch]?.field !== b[ch]?.field) out.add(ch);
  }
  return out;
}
function transposeState(base, template, pair) {
  const [a, b] = orderPair(pair[0], pair[1]);
  const ea = base[a];
  const eb = base[b];
  if (!ea?.field || !eb?.field) return null;
  if (!temporalActsDiscrete(template) && (isTemporal(ea) || isTemporal(eb))) return null;
  const next = clone(base);
  next[a] = { ...eb };
  next[b] = { ...ea };
  return { id: `flip:${a}-${b}`, label: `${chDisplay(a)} \u21C4 ${chDisplay(b)}`, enc: next };
}
function channelProfile(enc, template) {
  if (!enc?.field) return null;
  if (isMeasure(enc)) return "measure";
  if (isDiscrete(enc) || isTemporal(enc) && temporalActsDiscrete(template)) return "category";
  return "time";
}
function permuteSwapState(base, template, pair) {
  const [a, b] = orderPair(pair[0], pair[1]);
  const posCh = a === "x" || a === "y" ? a : null;
  const auxCh = b;
  if (!posCh || auxCh !== "color" && auxCh !== "size") return null;
  const posEnc = base[posCh];
  const auxEnc = base[auxCh];
  if (!posEnc?.field || !auxEnc?.field) return null;
  if (posEnc.field === auxEnc.field) return null;
  const profile = channelProfile(posEnc, template);
  if (!profile || profile !== channelProfile(auxEnc, template)) return null;
  const id = `swap:${a}-${b}`;
  const label = `${chDisplay(a)} \u21C4 ${chDisplay(b)}`;
  if (profile === "measure") {
    if (template.markCognitiveChannel !== "position") return null;
    const next = clone(base);
    next[posCh] = measureCore(auxEnc);
    next[auxCh] = measureCore(posEnc);
    return { id, label, enc: next };
  }
  if (profile === "category") {
    if (auxCh !== "color") return null;
    const next = clone(base);
    next[posCh] = { ...auxEnc };
    next.color = { ...posEnc };
    return { id, label, enc: next };
  }
  return null;
}
function measureCore(enc) {
  const core = { field: enc.field, type: enc.type };
  if (enc.aggregate) core.aggregate = enc.aggregate;
  return core;
}
var GROUPING_CHANNELS = ["color", "group", "column", "row"];
function routeBudget(target, facetBudget) {
  if (target === "column" || target === "row") return facetBudget;
  if (target === "group") return 12;
  return 20;
}
function routeLabel(from, to) {
  return `${chDisplay(from)} \u21C4 ${chDisplay(to)}`;
}
function findTransitionSeries(base, candidates, channels) {
  for (const channel of candidates) {
    if (channels.includes(channel) && isDiscrete(base[channel])) {
      return { channel, enc: base[channel] };
    }
  }
  return null;
}
function seriesRoutingStates(base, template, data, shiftChannels, facetBudget, preferredFacet) {
  const channels = template.channels ?? [];
  const out = [];
  const identitySource = isDiscrete(base.color) ? "color" : !base.color?.field && isDiscrete(base.group) ? "group" : void 0;
  if (identitySource && shiftChannels.includes(identitySource) && channels.includes(identitySource)) {
    const identityEncoding = base[identitySource];
    const card = distinctCount(data, identityEncoding.field);
    const facetTargets = preferredFacet ? [preferredFacet] : ["column", "row"];
    for (const target of facetTargets) {
      if (!shiftChannels.includes(target) || !channels.includes(target)) continue;
      if (base[target]?.field || card > routeBudget(target, facetBudget)) continue;
      const next = clone(base);
      delete next[identitySource];
      next[target] = { ...identityEncoding };
      out.push({
        id: `augment:${target}`,
        enc: next,
        label: `Color + ${chDisplay(target)}`,
        augmentation: {
          kind: "facet-identity",
          sourceChannel: identitySource,
          facetChannel: target,
          colorEncoding: { ...identityEncoding }
        }
      });
    }
  }
  const facetSource = ["column", "row"].find(
    (channel) => shiftChannels.includes(channel) && channels.includes(channel) && isDiscrete(base[channel])
  );
  if (facetSource) {
    const facetEncoding = base[facetSource];
    const card = distinctCount(data, facetEncoding.field);
    for (const target of shiftChannels) {
      if (target === facetSource || target === "group") continue;
      if ((target === "column" || target === "row") && preferredFacet && target !== preferredFacet) continue;
      if (!channels.includes(target) || base[target]?.field) continue;
      if (card > routeBudget(target, facetBudget)) continue;
      const next = clone(base);
      delete next[facetSource];
      next[target] = { ...facetEncoding };
      out.push({ id: `series:${target}`, enc: next, label: routeLabel(facetSource, target) });
    }
  }
  return out;
}
function preferredFacetTarget(base) {
  const domain = domainAxisEnc(base);
  return domain === base.y ? "row" : "column";
}
function changedFacetTarget(authored, transformed) {
  for (const channel of ["column", "row"]) {
    if (transformed[channel]?.field && transformed[channel]?.field !== authored[channel]?.field) {
      return channel;
    }
  }
  return void 0;
}
function domainAxisEnc(base) {
  if (base.x?.field && !isMeasure(base.x)) return base.x;
  if (base.y?.field && !isMeasure(base.y)) return base.y;
  return void 0;
}
function measureAxisEnc(base) {
  if (base.x?.field && isMeasure(base.x)) return base.x;
  if (base.y?.field && isMeasure(base.y)) return base.y;
  return void 0;
}
function transitionGatesPass(base, data, t) {
  if (t.requireOrderedAxis) {
    const domain = domainAxisEnc(base);
    if (!domain || !(domain.type === "temporal" || domain.type === "ordinal")) return false;
  }
  if (t.requireNonNegative) {
    const measure = measureAxisEnc(base);
    if (measure?.field) {
      for (const row of data) {
        const v = row?.[measure.field];
        if (typeof v === "number" && v < 0) return false;
      }
    }
  }
  if (t.maxCategoryCardinality != null) {
    const domain = domainAxisEnc(base);
    if (domain?.field && distinctCount(data, domain.field) > t.maxCategoryCardinality) return false;
  }
  if (t.requireNoSeries) {
    for (const ch of GROUPING_CHANNELS) {
      if (isDiscrete(base[ch])) return false;
    }
  }
  if (t.requireSeries) {
    const seriesChannels = ["color", "group", "detail", "column", "row"];
    if (!seriesChannels.some((ch) => isDiscrete(base[ch]))) return false;
  }
  if (t.requireBiaxialMeasure) {
    if (!isMeasure(base.x) || !isMeasure(base.y)) return false;
  }
  if (t.requireNoSize) {
    if (base.size?.field) return false;
  }
  return true;
}
function transitionState(base, data, template, t) {
  if (!transitionGatesPass(base, data, t)) return null;
  const enc = clone(base);
  const route = t.route;
  if (route) {
    const fromCh = route.from === "series" ? findTransitionSeries(base, GROUPING_CHANNELS, template.channels ?? [])?.channel : route.from;
    if (!fromCh) return null;
    const srcEnc = base[fromCh];
    if (!srcEnc?.field) return null;
    if (t.requireDiscreteSource && !isDiscrete(srcEnc)) return null;
    if (t.maxSourceCardinality != null && distinctCount(data, srcEnc.field) > t.maxSourceCardinality) return null;
    const mode = route.mode ?? "move";
    const dstEnc = base[route.to];
    if (mode === "swap") {
      const spillCh = route.spill ?? fromCh;
      if (spillCh !== fromCh && base[spillCh]?.field) return null;
      enc[route.to] = { ...srcEnc };
      delete enc[fromCh];
      if (dstEnc?.field) enc[spillCh] = { ...dstEnc };
      else delete enc[spillCh];
    } else {
      if (fromCh !== route.to) {
        if (dstEnc?.field) return null;
        delete enc[fromCh];
        enc[route.to] = { ...srcEnc };
      }
    }
  }
  if (t.orientDomainAxis) {
    const target = t.orientDomainAxis;
    const other = target === "x" ? "y" : "x";
    const domainOnOther = !!enc[other]?.field && !isMeasure(enc[other]);
    const targetFreeForDomain = !enc[target]?.field || isMeasure(enc[target]);
    if (domainOnOther && targetFreeForDomain) {
      const a = enc[target];
      const b = enc[other];
      if (b) enc[target] = { ...b };
      else delete enc[target];
      if (a) enc[other] = { ...a };
      else delete enc[other];
    }
  }
  return { enc, chartType: t.to, label: t.label };
}
function pivotSteps(template, enc, data, opts, resolveTemplate) {
  const def = template.pivot;
  if (!def) return [];
  const includeTransitions = opts?.transitions !== false;
  const steps = [];
  for (const pair of def.transpose ?? []) {
    if (pair.length !== 2) continue;
    const s = transposeState(enc, template, [pair[0], pair[1]]);
    if (s) steps.push({ id: s.id, label: s.label, enc: s.enc });
  }
  for (const block of def.permute ?? []) {
    for (let i = 0; i < block.length; i++) {
      for (let j = i + 1; j < block.length; j++) {
        const s = permuteSwapState(enc, template, [block[i], block[j]]);
        if (s) steps.push({ id: s.id, label: s.label, enc: s.enc });
      }
    }
  }
  if (def.shift && def.shift.length) {
    const preferredFacet = opts?.preferFacetTarget ? preferredFacetTarget(enc) : void 0;
    for (const s of seriesRoutingStates(enc, template, data, def.shift, def.facetBudget ?? 12, preferredFacet)) {
      steps.push({ id: s.id, label: s.label, enc: s.enc, augmentation: s.augmentation });
    }
  }
  if (includeTransitions) {
    for (const t of getChartTransitions(template.chart)) {
      if (resolveTemplate && !resolveTemplate(t.to)) continue;
      const st = transitionState(enc, data, template, t);
      if (st) steps.push({ id: `type:${t.to}`, label: `\u03B8_\u2192${t.label.toLowerCase()}`, enc: st.enc, chartType: st.chartType });
    }
  }
  return steps;
}
function encodingKey(enc, chartType) {
  const cells = Object.keys(enc).filter((ch) => enc[ch]?.field).sort().map((ch) => {
    const e = enc[ch];
    return `${ch}=${e.field}/${e.type ?? ""}/${e.aggregate ?? ""}`;
  });
  return `${chartType ?? ""}::${cells.join(",")}`;
}
function isRenderableState(template, enc) {
  const channels = template.channels ?? [];
  if (channels.includes("x") && channels.includes("y")) {
    if (!enc.x?.field || !enc.y?.field) return false;
  }
  return true;
}
var MAX_PIVOT_STATES = 12;
function computePivot(template, base, data, resolveTemplate, opts) {
  const def = template.pivot;
  if (!def) return null;
  const key = opts?.key ?? def.key ?? "pivot";
  const label = opts?.label ?? def.label ?? "View";
  const includeTransitions = opts?.includeTransitions !== false;
  const ids = ["default"];
  const labels = ["Default"];
  const statesById = {
    default: clone(base)
  };
  const augmentationById = {
    default: void 0
  };
  const chartTypeById = {
    default: void 0
  };
  const seen = /* @__PURE__ */ new Set([encodingKey(base, void 0)]);
  const queue = [{ id: "default", label: "Default", enc: clone(base), chartType: void 0, template, augmentation: void 0 }];
  const authoredChart = template.chart;
  while (queue.length > 0 && ids.length < MAX_PIVOT_STATES) {
    const cur = queue.shift();
    for (const step of pivotSteps(cur.template, cur.enc, data, {
      transitions: includeTransitions,
      preferFacetTarget: opts?.preferFacetTarget
    }, resolveTemplate)) {
      let nextChartType = step.chartType ?? cur.chartType;
      if (nextChartType === authoredChart) nextChartType = void 0;
      const resolved = step.chartType ? resolveTemplate?.(step.chartType) : void 0;
      const nextTemplate = step.chartType ? resolved ?? { ...cur.template, pivot: void 0 } : cur.template;
      if (!isRenderableState(nextTemplate, step.enc)) continue;
      if (opts?.preferFacetTarget) {
        const facetTarget = changedFacetTarget(base, step.enc);
        if (facetTarget && facetTarget !== preferredFacetTarget(step.enc)) continue;
      }
      if (step.chartType === void 0 && cur.id !== "default") {
        const already = changedChannels(base, cur.enc);
        const now = changedChannels(cur.enc, step.enc);
        let overlaps = false;
        for (const ch of now) {
          if (already.has(ch)) {
            overlaps = true;
            break;
          }
        }
        if (overlaps) continue;
      }
      const fp = encodingKey(step.enc, nextChartType);
      if (seen.has(fp)) continue;
      seen.add(fp);
      const id = cur.id === "default" ? step.id : `${cur.id}|${step.id}`;
      const stepLabel = cur.id === "default" ? step.label : `${cur.label} \xB7 ${step.label}`;
      ids.push(id);
      labels.push(stepLabel);
      statesById[id] = step.enc;
      chartTypeById[id] = nextChartType;
      const augmentation = step.chartType ? void 0 : step.augmentation ?? cur.augmentation;
      augmentationById[id] = augmentation;
      queue.push({ id, label: stepLabel, enc: step.enc, chartType: nextChartType, template: nextTemplate, augmentation });
      if (ids.length >= MAX_PIVOT_STATES) break;
    }
  }
  return { key, label, ids, labels, statesById, augmentationById, chartTypeById };
}
function applyPivot(template, base, data, chartProperties, resolveTemplate) {
  const comp = computePivot(template, base, data, resolveTemplate);
  if (!comp || comp.ids.length <= 1) {
    return { encodings: base, augmentation: void 0, chartType: void 0, surface: void 0 };
  }
  const stored = chartProperties?.[comp.key];
  const id = typeof stored === "string" && comp.ids.includes(stored) ? stored : comp.ids[0];
  const index = comp.ids.indexOf(id);
  return {
    encodings: comp.statesById[id],
    augmentation: comp.augmentationById[id],
    chartType: comp.chartTypeById[id],
    surface: {
      key: comp.key,
      label: comp.label,
      length: comp.ids.length,
      index,
      ids: comp.ids,
      labels: comp.labels
    }
  };
}
var TRANSFORM_CHART_TYPE_KEY = "chartType";
var TRANSFORM_ARRANGE_KEY = "arrange";
function buildSurface(comp, id) {
  const index = Math.max(0, comp.ids.indexOf(id));
  return {
    key: comp.key,
    label: comp.label,
    length: comp.ids.length,
    index,
    ids: comp.ids,
    labels: comp.labels
  };
}
function computeArrangeStates(template, base, data) {
  return computePivot(template, base, data, void 0, {
    includeTransitions: false,
    key: TRANSFORM_ARRANGE_KEY,
    label: "Arrange",
    preferFacetTarget: true
  });
}
function computeChartTypeStates(template, base, data, resolveTemplate) {
  const transitions = getChartTransitions(template.chart);
  if (transitions.length === 0) return null;
  const ids = ["default"];
  const labels = [template.chart];
  const statesById = { default: clone(base) };
  const augmentationById = { default: void 0 };
  const chartTypeById = { default: void 0 };
  const seenChartTypes = /* @__PURE__ */ new Set([template.chart]);
  for (const t of transitions) {
    if (resolveTemplate && !resolveTemplate(t.to)) continue;
    const st = transitionState(base, data, template, t);
    if (!st) continue;
    if (seenChartTypes.has(st.chartType)) continue;
    seenChartTypes.add(st.chartType);
    const id = `type:${t.to}`;
    ids.push(id);
    labels.push(st.chartType);
    statesById[id] = st.enc;
    chartTypeById[id] = st.chartType;
  }
  if (ids.length <= 1) return null;
  return { key: TRANSFORM_CHART_TYPE_KEY, label: "Chart type", ids, labels, statesById, augmentationById, chartTypeById };
}
function resolveTransformOverrides(chartProperties) {
  let chartTypeId = chartProperties?.[TRANSFORM_CHART_TYPE_KEY];
  let arrangeId = chartProperties?.[TRANSFORM_ARRANGE_KEY];
  if (typeof chartTypeId !== "string") chartTypeId = void 0;
  if (typeof arrangeId !== "string") arrangeId = void 0;
  if (chartTypeId === void 0 && arrangeId === void 0) {
    const legacy = chartProperties?.pivot;
    if (typeof legacy === "string" && legacy.length > 0 && legacy !== "default") {
      const tokens = legacy.split("|");
      const typeIdx = tokens.findIndex((t) => t.startsWith("type:"));
      if (typeIdx >= 0) {
        chartTypeId = tokens[typeIdx];
        const local = tokens.slice(0, typeIdx);
        arrangeId = local.length ? local.join("|") : void 0;
      } else {
        arrangeId = legacy;
      }
    }
  }
  return { chartTypeId, arrangeId };
}
function applyTransform(template, base, data, chartProperties, resolveTemplate) {
  const { chartTypeId, arrangeId } = resolveTransformOverrides(chartProperties);
  let effectiveTemplate = template;
  let effectiveEnc = base;
  let chartType;
  let chartTypeSurface;
  const ctComp = computeChartTypeStates(template, base, data, resolveTemplate);
  if (ctComp && ctComp.ids.length > 1) {
    const id = chartTypeId && ctComp.ids.includes(chartTypeId) ? chartTypeId : "default";
    effectiveEnc = ctComp.statesById[id];
    chartType = ctComp.chartTypeById[id];
    if (chartType) {
      const resolved = resolveTemplate?.(chartType);
      if (resolved) effectiveTemplate = resolved;
    }
    chartTypeSurface = buildSurface(ctComp, id);
  }
  let encodings = effectiveEnc;
  let augmentation;
  let arrangeSurface;
  const arrComp = computeArrangeStates(effectiveTemplate, effectiveEnc, data);
  if (arrComp && arrComp.ids.length > 1) {
    const id = arrangeId && arrComp.ids.includes(arrangeId) ? arrangeId : arrComp.ids[0];
    encodings = arrComp.statesById[id];
    augmentation = arrComp.augmentationById[id];
    arrangeSurface = buildSurface(arrComp, id);
  }
  return {
    encodings,
    augmentation,
    chartType,
    surface: { chartType: chartTypeSurface, arrange: arrangeSurface }
  };
}

// src/core/type-registry.ts
var TYPE_REGISTRY = {
  // --- Temporal: DateTime ---
  DateTime: { t0: "Temporal", t1: "DateTime", visEncodings: ["temporal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Date: { t0: "Temporal", t1: "DateTime", visEncodings: ["temporal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Time: { t0: "Temporal", t1: "DateTime", visEncodings: ["temporal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Timestamp: { t0: "Temporal", t1: "DateTime", visEncodings: ["temporal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  // --- Temporal: DateGranule ---
  Year: { t0: "Temporal", t1: "DateGranule", visEncodings: ["temporal", "ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "integer", zeroBaseline: "arbitrary", zeroPad: 0.03 },
  Quarter: { t0: "Temporal", t1: "DateGranule", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Month: { t0: "Temporal", t1: "DateGranule", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Week: { t0: "Temporal", t1: "DateGranule", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Day: { t0: "Temporal", t1: "DateGranule", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Hour: { t0: "Temporal", t1: "DateGranule", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "integer", zeroBaseline: "arbitrary", zeroPad: 0 },
  YearMonth: { t0: "Temporal", t1: "DateGranule", visEncodings: ["temporal", "ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  YearQuarter: { t0: "Temporal", t1: "DateGranule", visEncodings: ["temporal", "ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  YearWeek: { t0: "Temporal", t1: "DateGranule", visEncodings: ["temporal", "ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Decade: { t0: "Temporal", t1: "DateGranule", visEncodings: ["temporal", "ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "integer", zeroBaseline: "arbitrary", zeroPad: 0.03 },
  // --- Temporal: Duration ---
  Duration: { t0: "Temporal", t1: "Duration", visEncodings: ["quantitative"], aggRole: "additive", domainShape: "open", diverging: "none", formatClass: "unit-suffix", zeroBaseline: "meaningful", zeroPad: 0 },
  // --- Measure: Amount ---
  Amount: { t0: "Measure", t1: "Amount", visEncodings: ["quantitative"], aggRole: "additive", domainShape: "open", diverging: "none", formatClass: "currency", zeroBaseline: "meaningful", zeroPad: 0 },
  Price: { t0: "Measure", t1: "Amount", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "open", diverging: "none", formatClass: "currency", zeroBaseline: "meaningful", zeroPad: 0 },
  // --- Measure: Physical ---
  Quantity: { t0: "Measure", t1: "Physical", visEncodings: ["quantitative"], aggRole: "additive", domainShape: "open", diverging: "none", formatClass: "unit-suffix", zeroBaseline: "meaningful", zeroPad: 0 },
  Temperature: { t0: "Measure", t1: "Physical", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "open", diverging: "conditional", formatClass: "unit-suffix", zeroBaseline: "arbitrary", zeroPad: 0.05 },
  // --- Measure: Proportion ---
  Percentage: { t0: "Measure", t1: "Proportion", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "bounded", diverging: "none", formatClass: "percent", zeroBaseline: "contextual", zeroPad: 0 },
  // --- Measure: SignedMeasure ---
  Profit: { t0: "Measure", t1: "SignedMeasure", visEncodings: ["quantitative"], aggRole: "signed-additive", domainShape: "open", diverging: "conditional", formatClass: "decimal", zeroBaseline: "meaningful", zeroPad: 0 },
  PercentageChange: { t0: "Measure", t1: "SignedMeasure", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "open", diverging: "conditional", formatClass: "percent", zeroBaseline: "contextual", zeroPad: 0.05 },
  Sentiment: { t0: "Measure", t1: "SignedMeasure", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "open", diverging: "inherent", formatClass: "decimal", zeroBaseline: "meaningful", zeroPad: 0 },
  Correlation: { t0: "Measure", t1: "SignedMeasure", visEncodings: ["quantitative"], aggRole: "intensive", domainShape: "bounded", diverging: "inherent", formatClass: "decimal", zeroBaseline: "meaningful", zeroPad: 0 },
  // --- Measure: GenericMeasure ---
  Count: { t0: "Measure", t1: "GenericMeasure", visEncodings: ["quantitative"], aggRole: "additive", domainShape: "open", diverging: "none", formatClass: "integer", zeroBaseline: "meaningful", zeroPad: 0 },
  Number: { t0: "Measure", t1: "GenericMeasure", visEncodings: ["quantitative"], aggRole: "additive", domainShape: "open", diverging: "none", formatClass: "decimal", zeroBaseline: "meaningful", zeroPad: 0 },
  // --- Discrete ---
  Rank: { t0: "Discrete", t1: "Rank", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "integer", zeroBaseline: "arbitrary", zeroPad: 0.08 },
  Score: { t0: "Discrete", t1: "Score", visEncodings: ["quantitative", "ordinal"], aggRole: "intensive", domainShape: "bounded", diverging: "conditional", formatClass: "decimal", zeroBaseline: "contextual", zeroPad: 0.05 },
  ID: { t0: "Identifier", t1: "ID", visEncodings: ["nominal"], aggRole: "identifier", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "arbitrary", zeroPad: 0 },
  // --- Geographic ---
  Latitude: { t0: "Geographic", t1: "GeoCoordinate", visEncodings: ["quantitative", "geographic"], aggRole: "dimension", domainShape: "fixed", diverging: "none", formatClass: "decimal", zeroBaseline: "arbitrary", zeroPad: 0.02 },
  Longitude: { t0: "Geographic", t1: "GeoCoordinate", visEncodings: ["quantitative", "geographic"], aggRole: "dimension", domainShape: "fixed", diverging: "none", formatClass: "decimal", zeroBaseline: "arbitrary", zeroPad: 0.02 },
  Country: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  State: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  City: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Region: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Address: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  ZipCode: { t0: "Geographic", t1: "GeoPlace", visEncodings: ["nominal"], aggRole: "identifier", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  // --- Categorical: Entity ---
  Category: { t0: "Categorical", t1: "Entity", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Name: { t0: "Categorical", t1: "Entity", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  // --- Categorical: Coded ---
  Status: { t0: "Categorical", t1: "Coded", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Boolean: { t0: "Categorical", t1: "Coded", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "fixed", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  Direction: { t0: "Categorical", t1: "Coded", visEncodings: ["ordinal", "nominal"], aggRole: "dimension", domainShape: "cyclic", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  // --- Categorical: Binned ---
  Range: { t0: "Categorical", t1: "Binned", visEncodings: ["ordinal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 },
  // --- Fallbacks ---
  Unknown: { t0: "Categorical", t1: "Entity", visEncodings: ["nominal"], aggRole: "dimension", domainShape: "open", diverging: "none", formatClass: "plain", zeroBaseline: "none", zeroPad: 0 }
};
var UNKNOWN_ENTRY = {
  t0: "Categorical",
  t1: "Entity",
  visEncodings: ["nominal"],
  aggRole: "dimension",
  domainShape: "open",
  diverging: "none",
  formatClass: "plain",
  zeroBaseline: "none",
  zeroPad: 0
};
function getRegistryEntry(semanticType) {
  return TYPE_REGISTRY[semanticType] ?? UNKNOWN_ENTRY;
}
function isRegistered(semanticType) {
  return semanticType in TYPE_REGISTRY;
}
function getRegisteredTypes() {
  return Object.keys(TYPE_REGISTRY);
}

// src/core/semantic-types.ts
var measureTypes = new Set(
  getRegisteredTypes().filter((t) => {
    const e = getRegistryEntry(t);
    return ["additive", "intensive", "signed-additive"].includes(e.aggRole) && e.t1 !== "Score";
  })
);
var nonMeasureNumericTypes = /* @__PURE__ */ new Set([
  "Rank",
  "ID",
  "Score",
  "Year",
  "Month",
  "Day",
  "Hour",
  "Latitude",
  "Longitude"
]);
var categoricalTypes = new Set(
  getRegisteredTypes().filter((t) => {
    const e = getRegistryEntry(t);
    return e.visEncodings.includes("nominal") && e.aggRole !== "identifier" || e.t1 === "Binned";
  })
);
var ordinalTypes = new Set(
  getRegisteredTypes().filter((t) => {
    const e = getRegistryEntry(t);
    return e.visEncodings.includes("ordinal");
  })
);
function getVisCategory(semanticType) {
  if (!semanticType || !isRegistered(semanticType)) return null;
  return getRegistryEntry(semanticType).visEncodings[0] ?? null;
}
function inferVisCategory(values) {
  if (values.length === 0) return "nominal";
  const isBoolean = (v) => v === true || v === false || Object.prototype.toString.call(v) === "[object Boolean]";
  const isNumber = (v) => !isNaN(+v) && !(Object.prototype.toString.call(v) === "[object Date]");
  const looksLikeDate = (s) => /^\d|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(s.trim());
  const isDate = (v) => {
    if (v instanceof Date) return !isNaN(v.getTime());
    if (typeof v === "string") return looksLikeDate(v) && !isNaN(Date.parse(v));
    return !isNaN(Date.parse(v));
  };
  const nonNull = values.filter((v) => v != null);
  if (nonNull.length === 0) return "nominal";
  if (nonNull.every(isBoolean)) return "nominal";
  if (nonNull.every(isNumber)) return "quantitative";
  if (nonNull.every(isDate)) return "temporal";
  return "nominal";
}
function isMeasureType(semanticType) {
  return measureTypes.has(semanticType);
}
function isTimeSeriesType(semanticType) {
  const entry = getRegistryEntry(semanticType);
  return entry.t0 === "Temporal" && entry.t1 !== "Duration";
}
function isCategoricalType(semanticType) {
  return categoricalTypes.has(semanticType);
}
function isOrdinalType(semanticType) {
  return ordinalTypes.has(semanticType);
}
function isGeoCoordinateType(semanticType) {
  return getRegistryEntry(semanticType).t1 === "GeoCoordinate";
}
function isGeoLocationString(semanticType) {
  return getRegistryEntry(semanticType).t1 === "GeoPlace";
}
function isNonMeasureNumeric(semanticType) {
  return nonMeasureNumericTypes.has(semanticType);
}
function getZeroClass(semanticType) {
  const baseline = getRegistryEntry(semanticType).zeroBaseline;
  if (baseline === "none") return "unknown";
  return baseline;
}
var ZERO_BASELINE_GAP_THRESHOLD = 0.5;
function dataFarFromZero(values) {
  if (!values || values.length === 0) return false;
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  if (dataMin <= 0 || dataMax <= 0) return false;
  return dataMin / dataMax >= ZERO_BASELINE_GAP_THRESHOLD;
}
function computeZeroDecision(semanticType, channel, markType, values) {
  const isBarLike = ["bar", "area", "rect"].includes(markType);
  const isScatterMark = markType === "circle" || markType === "point";
  const isPositional = ["x", "y"].includes(channel);
  const entry = getRegistryEntry(semanticType);
  const zeroClass = getZeroClass(semanticType);
  if (zeroClass === "meaningful") {
    if (isBarLike) {
      return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
    }
    if (isPositional && isScatterMark) {
      if (values && values.length > 0 && Math.min(...values) <= 0) {
        return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
      }
      return {
        zero: false,
        domainPadFraction: entry.zeroPad || 0.05,
        zeroClass,
        forced: false,
        uncertain: true
      };
    }
    return {
      zero: true,
      domainPadFraction: 0,
      zeroClass,
      forced: false,
      uncertain: dataFarFromZero(values)
    };
  }
  if (zeroClass === "arbitrary") {
    if (isBarLike && values && values.length > 0) {
      const dataMin = Math.min(...values);
      if (dataMin <= 0) {
        return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
      }
    }
    return {
      zero: false,
      domainPadFraction: entry.zeroPad || 0.05,
      zeroClass,
      forced: false,
      uncertain: false
    };
  }
  if (zeroClass === "contextual" && values && values.length > 0) {
    const dataMin = Math.min(...values);
    const dataMax = Math.max(...values);
    if (dataMin <= 0) {
      return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
    }
    const proximity = dataMax > 0 ? dataMin / dataMax : 0;
    if (proximity < 0.3) {
      return { zero: true, domainPadFraction: 0, zeroClass, forced: false, uncertain: false };
    }
    if (isBarLike) {
      return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
    }
    return { zero: false, domainPadFraction: 0.05, zeroClass, forced: false, uncertain: false };
  }
  if (isBarLike && isPositional) {
    return { zero: true, domainPadFraction: 0, zeroClass: "unknown", forced: true, uncertain: false };
  }
  return { zero: false, domainPadFraction: 0.05, zeroClass: "unknown", forced: true, uncertain: false };
}
function computePaddedDomain(values, padFraction) {
  if (padFraction <= 0 || values.length < 2) return null;
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const span = dataMax - dataMin;
  if (span <= 0) return null;
  const padding = span * padFraction;
  return [dataMin - padding, dataMax + padding];
}
function getRecommendedColorScheme(semanticType, encodingType, uniqueValueCount = 10, fieldName = "", values = [], colorHint) {
  const pickScheme = (schemes, name) => {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
      hash = (hash << 5) - hash + name.charCodeAt(i);
      hash = hash & hash;
    }
    return schemes[Math.abs(hash) % schemes.length];
  };
  if (!semanticType) {
    if (encodingType === "quantitative") {
      return { scheme: "viridis", type: "sequential", reason: "default for quantitative" };
    }
    if (encodingType === "ordinal") {
      return { scheme: "blues", type: "sequential", reason: "default for ordinal" };
    }
    return {
      scheme: uniqueValueCount > 10 ? "tableau20" : "tableau10",
      type: "categorical",
      reason: "default for categorical"
    };
  }
  if (semanticType === "Temperature") {
    if (colorHint?.type === "diverging") {
      return { scheme: "redblue", type: "diverging", reason: "temperature diverging around freezing point" };
    }
    return { scheme: "reds", type: "sequential", reason: "temperature single-direction uses sequential" };
  }
  if (semanticType === "Percentage") {
    if (colorHint?.type === "diverging") {
      return { scheme: "redblue", type: "diverging", reason: "percentage spans positive and negative" };
    }
    return { scheme: "oranges", type: "sequential", reason: "percentage all same sign uses sequential" };
  }
  if (["Price", "Amount"].includes(semanticType)) {
    if (colorHint?.type === "diverging") {
      return { scheme: "redblue", type: "diverging", reason: "financial data spans positive and negative" };
    }
    return { scheme: "goldgreen", type: "sequential", reason: "financial data uses gold-green" };
  }
  if (semanticType === "Score") {
    if (colorHint?.type === "diverging") {
      return { scheme: "redblue", type: "diverging", reason: "score/rating diverging around midpoint" };
    }
    return { scheme: "yelloworangebrown", type: "sequential", reason: "scores use warm sequential" };
  }
  if (semanticType === "Rank") {
    return { scheme: "purples", type: "sequential", reason: "ranks use single-hue sequential" };
  }
  if (semanticType === "Range") {
    return { scheme: "blues", type: "sequential", reason: "range groups use sequential" };
  }
  if (ordinalTypes.has(semanticType) && ["Year", "Quarter", "Month", "Week", "Day", "Hour", "Decade"].includes(semanticType)) {
    return { scheme: "viridis", type: "sequential", reason: "temporal granules use perceptually uniform" };
  }
  if (getRegistryEntry(semanticType ?? "").t1 === "GeoPlace") {
    if (uniqueValueCount <= 10) {
      return { scheme: "set2", type: "categorical", reason: "geographic regions use distinct pastels" };
    }
    return { scheme: "tableau20", type: "categorical", reason: "many regions use large categorical" };
  }
  if (["Status", "Boolean"].includes(semanticType)) {
    return { scheme: "set1", type: "categorical", reason: "status uses high-contrast categorical" };
  }
  if (semanticType === "Category") {
    return {
      scheme: uniqueValueCount > 10 ? "tableau20" : "tableau10",
      type: "categorical",
      reason: "categories use standard categorical"
    };
  }
  if (semanticType === "Name") {
    return {
      scheme: uniqueValueCount > 8 ? "tableau20" : "set2",
      type: "categorical",
      reason: "names use readable categorical"
    };
  }
  if (semanticType === "Duration") {
    return { scheme: "oranges", type: "sequential", reason: "duration uses intensity-based sequential" };
  }
  if (measureTypes.has(semanticType)) {
    if (colorHint?.type === "diverging") {
      return { scheme: "redblue", type: "diverging", reason: "measure with diverging nature" };
    }
    const sequentialSchemes = ["viridis", "blues", "greens", "reds", "yelloworangebrown", "goldgreen"];
    return {
      scheme: pickScheme(sequentialSchemes, fieldName),
      type: "sequential",
      reason: "measures use perceptually uniform sequential"
    };
  }
  if (ordinalTypes.has(semanticType) || encodingType === "ordinal") {
    const ordinalSchemes = ["blues", "greens", "purples", "oranges"];
    return {
      scheme: pickScheme(ordinalSchemes, fieldName),
      type: "sequential",
      reason: "ordinal data uses sequential scheme"
    };
  }
  if (encodingType === "nominal" || encodingType === "temporal") {
    return {
      scheme: uniqueValueCount > 10 ? "tableau20" : "tableau10",
      type: "categorical",
      reason: "default categorical palette"
    };
  }
  return { scheme: "viridis", type: "sequential", reason: "universal fallback" };
}
var MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
var MONTH_ABBR3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
var MONTH_NUM = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
var DOW_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
var DOW_ABBR3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
var DOW_ABBR2 = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
var DOW_FULL_SUN = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
var DOW_ABBR3_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
var QUARTER_LABELS = ["Q1", "Q2", "Q3", "Q4"];
var COMPASS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
var COMPASS_8_FULL = ["North", "Northeast", "East", "Southeast", "South", "Southwest", "West", "Northwest"];
var COMPASS_4 = ["N", "E", "S", "W"];
var COMPASS_4_FULL = ["North", "East", "South", "West"];
var ORDINAL_SEQUENCES = {
  Month: [
    { labels: MONTH_FULL, caseInsensitive: true },
    { labels: MONTH_ABBR3, caseInsensitive: true },
    { labels: MONTH_NUM, caseInsensitive: false }
  ],
  Day: [
    { labels: DOW_FULL, caseInsensitive: true },
    { labels: DOW_ABBR3, caseInsensitive: true },
    { labels: DOW_ABBR2, caseInsensitive: true },
    { labels: DOW_FULL_SUN, caseInsensitive: true },
    { labels: DOW_ABBR3_SUN, caseInsensitive: true }
  ],
  Quarter: [
    { labels: QUARTER_LABELS, caseInsensitive: true }
  ],
  Direction: [
    { labels: COMPASS_8, caseInsensitive: true },
    { labels: COMPASS_8_FULL, caseInsensitive: true },
    { labels: COMPASS_4, caseInsensitive: true },
    { labels: COMPASS_4_FULL, caseInsensitive: true }
  ]
};
function buildLookup(seq) {
  const m = /* @__PURE__ */ new Map();
  for (let i = 0; i < seq.labels.length; i++) {
    const key = seq.caseInsensitive ? seq.labels[i].toLowerCase() : seq.labels[i];
    m.set(key, i);
  }
  return m;
}
function matchSequence(values, sequences) {
  const uniqueValues = [...new Set(values.map((v) => v != null ? String(v) : ""))].filter((v) => v !== "");
  if (uniqueValues.length === 0) return void 0;
  for (const seq of sequences) {
    const lookup = buildLookup(seq);
    const matched = [];
    const unmatched = [];
    for (const val of uniqueValues) {
      const key = seq.caseInsensitive ? val.toLowerCase() : val;
      const idx = lookup.get(key);
      if (idx !== void 0) {
        matched.push({ value: val, index: idx });
      } else {
        unmatched.push(val);
      }
    }
    if (matched.length >= uniqueValues.length * 0.6 && matched.length >= 2) {
      matched.sort((a, b) => a.index - b.index);
      const result = matched.map((m) => m.value);
      result.push(...unmatched);
      return result;
    }
  }
  return void 0;
}
function inferOrdinalSortOrder(semanticType, values) {
  const sequences = ORDINAL_SEQUENCES[semanticType];
  if (sequences) {
    return matchSequence(values, sequences);
  }
  if (!semanticType || semanticType === "Category" || semanticType === "Unknown") {
    for (const seqs of Object.values(ORDINAL_SEQUENCES)) {
      const result = matchSequence(values, seqs);
      if (result) return result;
    }
  }
  return void 0;
}

// src/core/field-semantics.ts
function toTypeString(input) {
  if (!input) return "";
  if (typeof input === "string") return input;
  return input.semanticType || "";
}
function normalizeAnnotation(input) {
  if (!input) return { semanticType: "Unknown" };
  if (typeof input === "string") return { semanticType: input || "Unknown" };
  return { ...input, semanticType: input.semanticType || "Unknown" };
}
var CURRENCY_MAP = {
  USD: "$",
  EUR: "\u20AC",
  GBP: "\xA3",
  JPY: "\xA5",
  CNY: "\xA5",
  KRW: "\u20A9",
  INR: "\u20B9",
  BRL: "R$",
  CAD: "CA$",
  AUD: "A$",
  CHF: "CHF",
  SEK: "kr",
  NOK: "kr",
  DKK: "kr"
};
var UNIT_SUFFIX_MAP = {
  // Temperature
  "\xB0C": "\xB0C",
  "\xB0F": "\xB0F",
  C: "\xB0C",
  F: "\xB0F",
  // Mass
  kg: " kg",
  lb: " lb",
  // Distance
  km: " km",
  mi: " mi",
  m: " m",
  ft: " ft",
  // Speed
  "km/h": " km/h",
  mph: " mph",
  // Time
  sec: " s",
  min: " min",
  hr: " hr",
  seconds: " s",
  minutes: " min",
  hours: " hr",
  // Percentage (handled by formatClass, but allow explicit suffix)
  "%": "%"
};
function detectPercentageRepresentation(values) {
  if (values.length === 0) return "0-100";
  const abs = values.map(Math.abs);
  const countBelow1 = abs.filter((v) => v <= 1).length;
  if (countBelow1 / abs.length >= 0.8) return "0-1";
  return "0-100";
}
function detectPrecision(values) {
  let maxDecimals = 0;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    const s = v.toFixed(10);
    const dot = s.indexOf(".");
    if (dot === -1) continue;
    let end = s.length - 1;
    while (end > dot && s[end] === "0") end--;
    const decimals = end > dot ? end - dot : 0;
    if (decimals > maxDecimals) maxDecimals = decimals;
  }
  return Math.min(maxDecimals, 4);
}
function precisionFormat(values, useGrouping = true, signMode = "") {
  const p = detectPrecision(values);
  const group = useGrouping ? "," : "";
  if (p === 0) return `${signMode}${group}d`;
  return `${signMode}${group}.${p}f`;
}
function resolveFormat(semanticType, annotation, values) {
  const entry = getRegistryEntry(semanticType);
  const unit = annotation.unit;
  const currencyPrefix = unit ? CURRENCY_MAP[unit.toUpperCase()] ?? CURRENCY_MAP[unit] : void 0;
  const unitSuffix = unit ? UNIT_SUFFIX_MAP[unit] : void 0;
  const nums = values.filter((v) => typeof v === "number" && !isNaN(v));
  switch (entry.formatClass) {
    case "currency": {
      const pfx = currencyPrefix;
      if (pfx) {
        const axisPattern = semanticType === "Price" ? ",.2f" : precisionFormat(nums);
        return {
          format: { pattern: axisPattern, prefix: pfx },
          tooltipFormat: { pattern: ",.2f", prefix: pfx }
        };
      }
      return { tooltipFormat: { pattern: ",.2f" } };
    }
    case "percent": {
      if (!annotation.intrinsicDomain) {
        return { tooltipFormat: { pattern: precisionFormat(nums) } };
      }
      const rep = detectPercentageRepresentation(nums);
      if (rep === "0-1") {
        const p = detectPrecision(nums);
        const axisP = Math.max(0, p - 2);
        const tipP = Math.min(axisP + 1, 4);
        return {
          format: { pattern: `.${axisP}~%` },
          tooltipFormat: { pattern: `.${tipP}%` }
        };
      }
      return {
        tooltipFormat: { pattern: precisionFormat(nums, false), suffix: "%" }
      };
    }
    case "unit-suffix":
      return {
        tooltipFormat: unitSuffix ? { pattern: precisionFormat(nums), suffix: unitSuffix } : { pattern: precisionFormat(nums) }
      };
    case "integer":
      if (semanticType === "Year" || semanticType === "Decade") {
        return {};
      }
      return { tooltipFormat: { pattern: ",d" } };
    case "decimal":
      return { tooltipFormat: { pattern: precisionFormat(nums) } };
    case "plain":
    default:
      return {};
  }
}
function resolveDefaultVisType(semanticType, values) {
  if (!isRegistered(semanticType)) {
    return inferVisCategory(values);
  }
  const entry = getRegistryEntry(semanticType);
  const candidates = entry.visEncodings;
  if (candidates.length === 1) {
    if (candidates[0] === "quantitative") {
      const nonNull = values.filter((v) => v != null);
      const allNumeric = nonNull.length > 0 && nonNull.every((v) => typeof v === "number" || typeof v === "string" && !isNaN(+v) && v.trim() !== "");
      if (!allNumeric) {
        return inferVisCategory(values);
      }
    }
    return candidates[0];
  }
  if (candidates.includes("quantitative") && candidates.includes("ordinal")) {
    const distinct = new Set(values.filter((v) => v != null)).size;
    return distinct <= 12 ? "ordinal" : "quantitative";
  }
  if (candidates.includes("temporal") && candidates.includes("ordinal")) {
    const distinct = new Set(values.filter((v) => v != null)).size;
    return distinct <= 6 ? "ordinal" : "temporal";
  }
  if (candidates.includes("geographic") && candidates.includes("quantitative")) {
    return "quantitative";
  }
  return candidates[0];
}
function resolveAggregationDefault(semanticType) {
  const entry = getRegistryEntry(semanticType);
  switch (entry.aggRole) {
    case "additive":
      return "sum";
    case "signed-additive":
      return "sum";
    case "intensive":
      return "average";
    case "dimension":
      return void 0;
    case "identifier":
      return void 0;
    default:
      return void 0;
  }
}
function resolveZeroClassFromAnnotation(semanticType, domain) {
  if (domain && domain[0] > 0) return "arbitrary";
  return getZeroClass(semanticType);
}
function resolveScaleType(semanticType, values) {
  const entry = getRegistryEntry(semanticType);
  const eligible = entry.aggRole === "additive" && entry.domainShape === "open" && entry.t1 !== "GenericMeasure";
  if (!eligible) return void 0;
  if (values.length < 10) return void 0;
  const filtered = values.filter((v) => typeof v === "number" && !isNaN(v) && isFinite(v));
  if (filtered.length < 10) return void 0;
  const min = Math.min(...filtered);
  const max = Math.max(...filtered);
  if (max <= 0 || min === max) return void 0;
  if (min < 0) return void 0;
  const positiveMin = Math.min(...filtered.filter((v) => v > 0));
  if (positiveMin > 0 && max / positiveMin >= 1e6) {
    const hasZeros = filtered.some((v) => v === 0);
    return hasZeros ? "symlog" : "log";
  }
  return void 0;
}
function mergeIntrinsicWithData(intrinsic, values, hard) {
  if (hard) {
    return { min: intrinsic[0], max: intrinsic[1], clamp: true };
  }
  const nums = values.filter((v) => typeof v === "number" && !isNaN(v));
  if (nums.length === 0) {
    return { min: intrinsic[0], max: intrinsic[1], clamp: false };
  }
  const dataMin = Math.min(...nums);
  const dataMax = Math.max(...nums);
  return {
    min: Math.min(intrinsic[0], dataMin),
    max: Math.max(intrinsic[1], dataMax),
    clamp: false
  };
}
function snapToBoundHeuristic(intrinsic, values) {
  const nums = values.filter((v) => typeof v === "number" && !isNaN(v));
  if (nums.length === 0) return void 0;
  const [lo, hi] = intrinsic;
  const range = hi - lo;
  if (range <= 0) return void 0;
  const dataMin = Math.min(...nums);
  const dataMax = Math.max(...nums);
  const zeroInside = lo < 0 && hi > 0;
  const thresholdLo = 0.25 * (zeroInside ? 0 - lo : range);
  const thresholdHi = 0.25 * (zeroInside ? hi : range);
  let snapMin;
  let snapMax;
  if (dataMin >= lo && dataMin <= lo + thresholdLo) {
    snapMin = lo;
  }
  if (dataMax <= hi && dataMax >= hi - thresholdHi) {
    snapMax = hi;
  }
  if (snapMin === void 0 && snapMax === void 0) return void 0;
  return { min: snapMin, max: snapMax, clamp: false };
}
function resolveDomainConstraint(semanticType, annotation, values) {
  const entry = getRegistryEntry(semanticType);
  if (annotation.intrinsicDomain) {
    if (entry.t1 === "Proportion" || entry.t1 === "SignedMeasure") {
      return snapToBoundHeuristic(annotation.intrinsicDomain, values);
    }
    return mergeIntrinsicWithData(annotation.intrinsicDomain, values, false);
  }
  if (semanticType === "Latitude") return mergeIntrinsicWithData([-90, 90], values, true);
  if (semanticType === "Longitude") return mergeIntrinsicWithData([-180, 180], values, true);
  if (semanticType === "Correlation") return mergeIntrinsicWithData([-1, 1], values, true);
  if (semanticType === "Percentage") {
    const nums = values.filter((v) => typeof v === "number" && !isNaN(v));
    if (nums.length > 0) {
      const rep = detectPercentageRepresentation(nums);
      const M = rep === "0-1" ? 1 : 100;
      return snapToBoundHeuristic([0, M], values);
    }
  }
  return void 0;
}
function resolveTickConstraint(semanticType, domain) {
  const entry = getRegistryEntry(semanticType);
  if (entry.formatClass === "integer") {
    const tc = { integersOnly: true, minStep: 1 };
    if (domain) {
      const span = domain[1] - domain[0];
      if (span <= 20 && span > 0) {
        tc.exactTicks = [];
        for (let i = domain[0]; i <= domain[1]; i++) {
          tc.exactTicks.push(i);
        }
      }
    }
    return tc;
  }
  if (semanticType === "Score" && domain) {
    const span = domain[1] - domain[0];
    if (span >= 2) {
      const tc = { integersOnly: true, minStep: 1 };
      if (span <= 20) {
        tc.exactTicks = [];
        for (let i = domain[0]; i <= domain[1]; i++) {
          tc.exactTicks.push(i);
        }
      }
      return tc;
    }
  }
  return void 0;
}
function resolveCanonicalOrder(semanticType, annotation, values) {
  if (annotation.sortOrder && annotation.sortOrder.length > 0) {
    return annotation.sortOrder;
  }
  return inferOrdinalSortOrder(semanticType, values);
}
function resolveCyclic(semanticType) {
  const entry = getRegistryEntry(semanticType);
  return entry.domainShape === "cyclic";
}
function resolveReversed(semanticType, channel) {
  if (semanticType === "Rank") {
    return channel !== "x";
  }
  return false;
}
function resolveNice(semanticType, domainConstraint) {
  if (domainConstraint?.clamp) return false;
  if (domainConstraint && domainConstraint.min !== void 0 && domainConstraint.max !== void 0) {
    return false;
  }
  const entry = getRegistryEntry(semanticType);
  if (entry.domainShape === "fixed") return false;
  return true;
}
function resolveDivergingInfo(semanticType, annotation, values) {
  const entry = getRegistryEntry(semanticType);
  if (semanticType === "Temperature" && annotation.unit) {
    const unitMidpoints = {
      "\xB0C": 0,
      "\xB0F": 32,
      "K": 273.15,
      C: 0,
      F: 32
    };
    const mid = unitMidpoints[annotation.unit];
    if (mid !== void 0) {
      return { midpoint: mid, inherent: false, source: "unit" };
    }
  }
  if (entry.diverging === "inherent") {
    return { midpoint: 0, inherent: true, source: "type-intrinsic" };
  }
  if (entry.diverging === "conditional") {
    return { midpoint: 0, inherent: false, source: "type-intrinsic" };
  }
  if (annotation.intrinsicDomain) {
    return {
      midpoint: (annotation.intrinsicDomain[0] + annotation.intrinsicDomain[1]) / 2,
      inherent: false,
      source: "domain"
    };
  }
  if (values.length > 0) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    if (min < 0 && max > 0) {
      return { midpoint: 0, inherent: false, source: "data" };
    }
  }
  return void 0;
}
function resolveColorSchemeHint(semanticType, annotation, values) {
  const entry = getRegistryEntry(semanticType);
  const nums = values.filter((v) => typeof v === "number" && !isNaN(v));
  const divInfo = resolveDivergingInfo(semanticType, annotation, nums);
  if (divInfo) {
    const min = nums.length > 0 ? Math.min(...nums) : 0;
    const max = nums.length > 0 ? Math.max(...nums) : 0;
    const spansBothSides = min < divInfo.midpoint && max > divInfo.midpoint;
    if (divInfo.inherent || spansBothSides) {
      return {
        type: "diverging",
        divergingMidpoint: divInfo.midpoint,
        inherentlyDiverging: divInfo.inherent
      };
    }
  }
  if (entry.visEncodings.includes("quantitative")) {
    return { type: "sequential" };
  }
  return { type: "categorical" };
}
function resolveBinningSuggested(semanticType, domain) {
  const entry = getRegistryEntry(semanticType);
  if (!entry.visEncodings.includes("quantitative")) return false;
  if (entry.aggRole === "identifier" || entry.aggRole === "dimension") return false;
  if (semanticType === "Year" || semanticType === "Decade") return false;
  if (domain && domain[1] - domain[0] <= 20) return false;
  if (semanticType === "Score" && !domain) return false;
  return true;
}
function resolveStackable(semanticType) {
  const entry = getRegistryEntry(semanticType);
  switch (entry.aggRole) {
    case "additive":
      return "sum";
    case "signed-additive":
      return "sum";
    case "intensive":
      if (semanticType === "Percentage") return "normalize";
      return false;
    case "dimension":
      return false;
    case "identifier":
      return false;
    default:
      return false;
  }
}
function resolveSortDirection(semanticType) {
  if (semanticType === "Rank") return "descending";
  return "ascending";
}
function resolveFieldSemantics(input, fieldName, values) {
  const annotation = normalizeAnnotation(input);
  const semanticType = annotation.semanticType;
  const numericValues = values.filter((v) => typeof v === "number" && !isNaN(v) && isFinite(v));
  const defaultVisType = resolveDefaultVisType(semanticType, values);
  const { format, tooltipFormat } = resolveFormat(semanticType, annotation, values);
  let aggregationDefault = resolveAggregationDefault(semanticType);
  let zeroClass = resolveZeroClassFromAnnotation(semanticType, annotation.intrinsicDomain);
  const scaleType = resolveScaleType(semanticType, numericValues);
  const domainConstraint = resolveDomainConstraint(semanticType, annotation, values);
  const canonicalOrder = resolveCanonicalOrder(semanticType, annotation, values);
  const cyclic = resolveCyclic(semanticType);
  let binningSuggested = resolveBinningSuggested(semanticType, annotation.intrinsicDomain);
  const sortDirection = resolveSortDirection(semanticType);
  if (!isRegistered(semanticType) && defaultVisType === "quantitative") {
    if (!aggregationDefault) aggregationDefault = "sum";
    if (zeroClass === "unknown") zeroClass = "meaningful";
    binningSuggested = true;
  }
  return {
    semanticAnnotation: annotation,
    defaultVisType,
    format,
    tooltipFormat,
    aggregationDefault,
    zeroClass,
    scaleType: scaleType ?? void 0,
    domainConstraint,
    canonicalOrder,
    cyclic,
    sortDirection,
    binningSuggested
  };
}

// src/core/decisions.ts
function visCategoryToVLType(vc) {
  switch (vc) {
    case "quantitative":
      return "quantitative";
    case "ordinal":
      return "ordinal";
    case "temporal":
      return "temporal";
    case "geographic":
      return "quantitative";
    case "nominal":
    default:
      return "nominal";
  }
}
function validateTemporalParsing(data, fieldName, fromRegistry) {
  const sampleValues = data.map((r) => r[fieldName]).slice(0, 15).filter((v) => v != null);
  if (sampleValues.length === 0) return false;
  const uniqueValues = new Set(sampleValues.map(String));
  if (uniqueValues.size <= 1) return false;
  const looksTemporalValue = (val) => {
    if (val instanceof Date) return true;
    if (typeof val === "number") {
      if (val >= 1500 && val <= 2200 && val % 1 === 0) return true;
      if (val > 864e5 && val < 42e11) return true;
      return false;
    }
    if (typeof val === "string") {
      const trimmed = val.trim();
      if (!trimmed) return false;
      if (/^\d{4}$/.test(trimmed)) return true;
      return !Number.isNaN(Date.parse(trimmed));
    }
    return false;
  };
  const passingCount = sampleValues.filter(looksTemporalValue).length;
  const minFraction = fromRegistry ? 0.3 : 0.5;
  return passingCount / sampleValues.length >= minFraction;
}
function resolveTemporalEncoding(visCategory, channel, data, fieldName, fromRegistry) {
  if (["size", "column", "row"].includes(channel)) {
    return { vlType: "ordinal", visCategory, channelOverride: true, cardinalityGuard: false };
  }
  if (channel === "color") {
    const uniqueCount = new Set(data.map((r) => r[fieldName])).size;
    if (uniqueCount <= 12) {
      return { vlType: "ordinal", visCategory, channelOverride: true, cardinalityGuard: false };
    }
  }
  if (!validateTemporalParsing(data, fieldName, fromRegistry)) {
    return { vlType: "ordinal", visCategory, channelOverride: false, cardinalityGuard: false };
  }
  return { vlType: "temporal", visCategory, channelOverride: false, cardinalityGuard: false };
}
function applyOrdinalGuards(visCategory, channel, data, fieldName, fieldValues, fromRegistry) {
  const numericVals = fieldValues.filter((v) => v != null && !isNaN(+v)).map(Number);
  if (numericVals.length > 0) {
    const uniqueCount = new Set(numericVals).size;
    const hasFractions = numericVals.some((v) => v % 1 !== 0);
    if (!fromRegistry && hasFractions && uniqueCount > 20) {
      return { vlType: "quantitative", visCategory, channelOverride: false, cardinalityGuard: true };
    }
    if (!hasFractions && uniqueCount > 12 && ["color", "group"].includes(channel)) {
      return { vlType: "quantitative", visCategory, channelOverride: true, cardinalityGuard: true };
    }
    if (!hasFractions && uniqueCount > 12 && ["x", "y"].includes(channel)) {
      return { vlType: "quantitative", visCategory, channelOverride: true, cardinalityGuard: true };
    }
  }
  return { vlType: "ordinal", visCategory, channelOverride: false, cardinalityGuard: false };
}
function disambiguateMultiEncoding(candidates, channel, data, fieldName, fieldValues) {
  const has = (vc) => candidates.includes(vc);
  if (has("temporal") && has("ordinal")) {
    return resolveTemporalEncoding("temporal", channel, data, fieldName, true);
  }
  if (has("quantitative") && has("ordinal")) {
    if (["color", "group"].includes(channel)) {
      const uniqueCount = new Set(data.map((r) => r[fieldName])).size;
      if (uniqueCount <= 12) {
        return { vlType: "ordinal", visCategory: "ordinal", channelOverride: false, cardinalityGuard: false };
      }
      return { vlType: "quantitative", visCategory: "quantitative", channelOverride: false, cardinalityGuard: true };
    }
    if (["column", "row"].includes(channel)) {
      return { vlType: "ordinal", visCategory: "ordinal", channelOverride: false, cardinalityGuard: false };
    }
    return { vlType: "quantitative", visCategory: "quantitative", channelOverride: false, cardinalityGuard: false };
  }
  if (has("quantitative") && has("geographic")) {
    return { vlType: "quantitative", visCategory: "quantitative", channelOverride: false, cardinalityGuard: false };
  }
  if (has("ordinal") && has("nominal")) {
    if (["color", "group"].includes(channel)) {
      return { vlType: "nominal", visCategory: "nominal", channelOverride: false, cardinalityGuard: false };
    }
    return { vlType: "ordinal", visCategory: "ordinal", channelOverride: false, cardinalityGuard: false };
  }
  const fallback = candidates[0];
  return { vlType: visCategoryToVLType(fallback), visCategory: fallback, channelOverride: false, cardinalityGuard: false };
}
function resolveEncodingType(semanticType, fieldValues, channel, data, fieldName) {
  if (semanticType && isRegistered(semanticType)) {
    const entry = getRegistryEntry(semanticType);
    const candidates = entry.visEncodings;
    if (candidates.length > 1) {
      return disambiguateMultiEncoding(candidates, channel, data, fieldName);
    }
    const baseType = candidates[0];
    if (baseType === "quantitative") {
      const nonNull = fieldValues.filter((v) => v != null);
      const allNumeric = nonNull.length > 0 && nonNull.every((v) => typeof v === "number" || typeof v === "string" && !isNaN(+v) && v.trim() !== "");
      if (!allNumeric) {
        const inferred = inferVisCategory(fieldValues);
        return {
          vlType: visCategoryToVLType(inferred),
          visCategory: inferred,
          channelOverride: false,
          cardinalityGuard: false
        };
      }
    }
    if (baseType === "temporal") {
      return resolveTemporalEncoding(baseType, channel, data, fieldName, true);
    }
    if (baseType === "ordinal") {
      return applyOrdinalGuards(baseType, channel, data, fieldName, fieldValues, true);
    }
    return {
      vlType: visCategoryToVLType(baseType),
      visCategory: baseType,
      channelOverride: false,
      cardinalityGuard: false
    };
  }
  const visCategory = inferVisCategory(fieldValues);
  const channelOverride = false;
  const cardinalityGuard = false;
  switch (visCategory) {
    case "temporal":
      return resolveTemporalEncoding(visCategory, channel, data, fieldName, false);
    case "ordinal":
      return applyOrdinalGuards(visCategory, channel, data, fieldName, fieldValues, false);
    case "quantitative":
      return { vlType: "quantitative", visCategory, channelOverride, cardinalityGuard };
    case "geographic":
      return { vlType: "quantitative", visCategory, channelOverride, cardinalityGuard };
    case "nominal":
    default:
      return { vlType: "nominal", visCategory, channelOverride, cardinalityGuard };
  }
}
var DEFAULT_GAS_PRESSURE_PARAMS = {
  markCrossSection: 30,
  elasticity: 0.3,
  maxStretch: 1.5
};
function computeGasPressure(xValues, yValues, xDomain, yDomain, canvasWidth, canvasHeight, params = DEFAULT_GAS_PRESSURE_PARAMS) {
  const N = xValues.length;
  if (N <= 1 || canvasWidth <= 0 || canvasHeight <= 0) {
    return { stretchX: 1, stretchY: 1, rawStretchX: 1, rawStretchY: 1 };
  }
  const sigma1dDefault = Math.sqrt(params.markCrossSection);
  const computeAxisStretch = (values, domain, baseDim, sigma1d) => {
    if (baseDim <= 0 || values.length <= 1) return [1, 1];
    const range = domain[1] - domain[0];
    if (range <= 0) return [1, 1];
    const pxPerUnit = baseDim / range;
    const seen = /* @__PURE__ */ new Set();
    for (const v of values) {
      seen.add(Math.round((v - domain[0]) * pxPerUnit));
    }
    const uniquePositions = seen.size;
    const pressure = uniquePositions * sigma1d / baseDim;
    if (pressure <= 1) return [1, 1];
    const raw = Math.pow(pressure, params.elasticity);
    return [Math.min(params.maxStretch, raw), raw];
  };
  const sigma1dX = params.markCrossSectionX != null ? Math.sqrt(params.markCrossSectionX) : sigma1dDefault;
  const sigma1dY = params.markCrossSectionY != null ? Math.sqrt(params.markCrossSectionY) : sigma1dDefault;
  const computeStretchForAxis = (values, domain, baseDim, sigma1d, sigmaRaw, itemCountOverride) => {
    if (itemCountOverride != null && sigmaRaw > 0) {
      const pressure = itemCountOverride * sigmaRaw / baseDim;
      if (pressure <= 1) return [1, 1];
      const raw = Math.pow(pressure, params.elasticity);
      return [Math.min(params.maxStretch, raw), raw];
    }
    return sigma1d > 0 ? computeAxisStretch(values, domain, baseDim, sigma1d) : [1, 1];
  };
  const sigmaRawX = params.markCrossSectionX ?? params.markCrossSection;
  const sigmaRawY = params.markCrossSectionY ?? params.markCrossSection;
  const [stretchX, rawStretchX] = computeStretchForAxis(xValues, xDomain, canvasWidth, sigma1dX, sigmaRawX, params.xItemCountOverride);
  const [stretchY, rawStretchY] = computeStretchForAxis(yValues, yDomain, canvasHeight, sigma1dY, sigmaRawY, params.yItemCountOverride);
  return { stretchX, stretchY, rawStretchX, rawStretchY };
}
function computeElasticBudget(itemCount, baseDimension, params) {
  if (itemCount <= 0) {
    return { budget: baseDimension, stretchFactor: 1 };
  }
  const pressure = itemCount * params.defaultStepSize / baseDimension;
  if (pressure <= 1) {
    return { budget: baseDimension, stretchFactor: 1 };
  }
  const stretchFactor = Math.min(params.maxStretch, Math.pow(pressure, params.elasticity));
  return {
    budget: baseDimension * stretchFactor,
    stretchFactor
  };
}
function computeAxisStep(nominalCount, continuousCount, baseDimension, params) {
  if (nominalCount > 0) {
    const { budget } = computeElasticBudget(nominalCount, baseDimension, params);
    return { step: Math.floor(budget / nominalCount), budget, itemCount: nominalCount };
  }
  if (continuousCount > 0) {
    const { budget } = computeElasticBudget(continuousCount, baseDimension, params);
    return { step: Math.floor(budget / continuousCount), budget, itemCount: continuousCount };
  }
  return { step: params.defaultStepSize, budget: baseDimension, itemCount: 0 };
}
function computeLabelSizing(effectiveStep, hasDiscreteItems, opts) {
  const baseFont = opts?.baseFont ?? 10;
  const minFont = opts?.minFont;
  const defaultLimit = 100;
  if (!hasDiscreteItems) {
    return { fontSize: baseFont, labelLimit: defaultLimit };
  }
  let fontSize = Math.max(minFont, Math.min(baseFont, effectiveStep - 1));
  let labelLimit = Math.max(30, Math.min(100, effectiveStep * 8));
  let labelAngle;
  let labelAlign;
  let labelBaseline;
  if (effectiveStep < 10) {
    labelAngle = -90;
    fontSize = Math.max(minFont, Math.min(baseFont - 2, effectiveStep));
    labelLimit = 40;
    labelAlign = "right";
    labelBaseline = "middle";
  } else if (effectiveStep < 16) {
    labelAngle = -45;
    fontSize = Math.max(minFont, Math.min(baseFont - 1, effectiveStep));
    labelLimit = 60;
    labelAlign = "right";
    labelBaseline = "top";
  }
  return { fontSize, labelLimit, labelAngle, labelAlign, labelBaseline };
}
function computeFontSizing(minPlotDimension, opts) {
  const baseLabel = opts?.baseLabelFontSize ?? 10;
  const baseTitle = opts?.baseTitleFontSize ?? 11;
  const minDim = minPlotDimension || 320;
  const ratio = minDim >= 220 ? 1 : Math.max(0.7, minDim / 220);
  const atMostNative = (base) => Math.round(Math.max(base - 2, Math.min(base, base * ratio)));
  const tickBase = atMostNative(baseLabel);
  const titleFontSize = atMostNative(baseTitle);
  const legendFontSize = Math.max(baseTitle - 2, titleFontSize - 1);
  return { tickBase, titleFontSize, legendFontSize };
}
function computeCircumferencePressure(effectiveItemCount, canvasSize, params = {}) {
  const {
    minArcPx = 45,
    minRadius = 60,
    maxRadius = 400,
    elasticity = 0.5,
    maxStretch = 2,
    margin = 20
  } = params;
  const maxStretchX = Math.max(1, params.maxStretchX ?? maxStretch);
  const maxStretchY = Math.max(1, params.maxStretchY ?? maxStretch);
  const baseW = canvasSize.width;
  const baseH = canvasSize.height;
  const baseRadius = Math.max(
    minRadius,
    Math.min(baseW, baseH) / 2 - margin
  );
  const maxCanvasW = baseW * maxStretchX;
  const maxCanvasH = baseH * maxStretchY;
  const maxDiameter = Math.min(maxCanvasW, maxCanvasH);
  const effectiveMaxRadius = Math.min(
    maxRadius,
    (maxDiameter - 2 * margin) / 2
  );
  const effectiveMaxStretch = Math.max(1, effectiveMaxRadius / baseRadius);
  const baseCircumference = 2 * Math.PI * baseRadius;
  const pressure = effectiveItemCount * minArcPx / baseCircumference;
  let radius;
  if (pressure <= 1) {
    radius = baseRadius;
  } else {
    const stretch = Math.min(effectiveMaxStretch, Math.pow(pressure, elasticity));
    radius = Math.round(baseRadius * stretch);
  }
  radius = Math.min(maxRadius, Math.max(minRadius, radius));
  const diameter = 2 * radius + 2 * margin;
  const canvasW = Math.max(baseW, diameter);
  const canvasH = Math.max(baseH, diameter);
  return { radius, canvasW, canvasH };
}
function computeEffectiveBarCount(values) {
  if (values.length === 0) return 0;
  const positiveValues = values.filter((v) => v > 0);
  if (positiveValues.length === 0) return values.length;
  const total = positiveValues.reduce((s, v) => s + v, 0);
  const minVal = Math.min(...positiveValues);
  const effective = total / minVal;
  return Math.min(100, effective);
}

// src/core/resolve-semantics.ts
var MAX_TIMESTAMP_SEC = 4102444800;
var MAX_TIMESTAMP_MS = 41024448e5;
function isLikelyTimestamp(val) {
  if (val >= 1e9 && val <= MAX_TIMESTAMP_SEC) return true;
  if (val > MAX_TIMESTAMP_SEC && val <= MAX_TIMESTAMP_MS) return true;
  return false;
}
function timestampToMs(val) {
  return val <= MAX_TIMESTAMP_SEC ? val * 1e3 : val;
}
function looksLikeDateString(s) {
  const t = s.trim();
  return /^\d|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(t);
}
function analyzeTemporalField(fieldValues) {
  const dates = [];
  let nonNull = 0;
  for (const v of fieldValues.slice(0, 100)) {
    if (v == null) continue;
    nonNull++;
    const d = v instanceof Date ? v : new Date(v);
    if (!isNaN(d.getTime())) dates.push(d);
  }
  if (dates.length < 2 || dates.length < nonNull * 0.5) return null;
  const monthSet = new Set(dates.map((d) => d.getUTCMonth()));
  const daySet = new Set(dates.map((d) => d.getUTCDate()));
  const hourSet = new Set(dates.map((d) => d.getUTCHours()));
  const minuteSet = new Set(dates.map((d) => d.getUTCMinutes()));
  const secondSet = new Set(dates.map((d) => d.getUTCSeconds()));
  const yearSet = new Set(dates.map((d) => d.getUTCFullYear()));
  const isSmallSpread = (s, maxSpread = 1) => {
    if (s.size <= 1) return true;
    const arr = [...s];
    return Math.max(...arr) - Math.min(...arr) <= maxSpread;
  };
  const same = {
    month: monthSet.size === 1,
    day: daySet.size === 1,
    hour: isSmallSpread(hourSet, 1),
    minute: minuteSet.size === 1,
    second: secondSet.size === 1
  };
  const sameYear = yearSet.size === 1;
  const sameMonth = sameYear && same.month;
  const sameDay = sameMonth && same.day;
  return { dates, same, sameYear, sameMonth, sameDay };
}
function computeDataVotes(same) {
  const votes = [0, 0, 0, 0, 0, 0];
  if (same.second) votes[5] += 1;
  if (same.minute && same.second) votes[5] += 1;
  if (same.hour && same.minute && same.second) votes[5] += 1;
  if (same.day && same.hour && same.minute && same.second) votes[5] += 2;
  if (same.month && same.day && same.hour && same.minute && same.second) votes[5] += 3;
  if (same.second) votes[4] += 1;
  if (same.minute && same.second) votes[4] += 1;
  if (same.hour && same.minute && same.second) votes[4] += 1;
  if (same.day && same.hour && same.minute && same.second) votes[4] += 2;
  if (!same.month && same.day && same.hour && same.minute && same.second) votes[4] += 3;
  if (same.second) votes[3] += 1;
  if (same.minute && same.second) votes[3] += 1;
  if (same.hour && same.minute && same.second) votes[3] += 1;
  if (!same.day && same.hour && same.minute && same.second) votes[3] += 3;
  if (same.second) votes[2] += 1;
  if (same.minute && same.second) votes[2] += 1;
  if (!same.hour && same.minute && same.second) votes[2] += 3;
  if (same.second) votes[1] += 1;
  if (!same.minute && same.second) votes[1] += 3;
  if (!same.second) votes[0] += 4;
  return votes;
}
var SEMANTIC_LEVEL = {
  Year: 5,
  Decade: 5,
  YearMonth: 4,
  Month: 4,
  YearQuarter: 4,
  Quarter: 4,
  Date: 3,
  Day: 3,
  Hour: 2,
  DateTime: 1,
  Timestamp: 0
};
function pickBestLevel(votes) {
  let bestLevel = 0;
  let bestScore = votes[0];
  for (let i = 1; i <= 5; i++) {
    if (votes[i] >= bestScore) {
      bestScore = votes[i];
      bestLevel = i;
    }
  }
  return { level: bestLevel, score: bestScore };
}
function levelToFormat(level, analysis) {
  switch (level) {
    case 5:
      return "%Y";
    case 4:
      return analysis.sameYear ? "%b" : "%b %Y";
    case 3:
      return analysis.sameYear ? "%b %d" : "%b %d, %Y";
    case 2:
      return analysis.sameDay ? "%H:00" : "%b %d %H:00";
    case 1:
      return analysis.sameDay ? "%H:%M" : "%b %d %H:%M";
    case 0:
      return analysis.sameDay ? "%H:%M:%S" : "%b %d %H:%M:%S";
    default:
      return null;
  }
}
function resolveTemporalFormat(fieldValues, semanticType) {
  const analysis = analyzeTemporalField(fieldValues);
  if (!analysis) return null;
  const votes = computeDataVotes(analysis.same);
  const semLevel = SEMANTIC_LEVEL[semanticType];
  if (semLevel !== void 0) votes[semLevel] += 3;
  const { level } = pickBestLevel(votes);
  return levelToFormat(level, analysis);
}
function expandToFullYear(val) {
  const trimmed = val.trim();
  if (/^\d{2}$/.test(trimmed)) {
    const n = parseInt(trimmed, 10);
    return String(n <= 49 ? 2e3 + n : 1900 + n);
  }
  return val;
}
function convertTemporalData(data, semanticTypes) {
  if (data.length === 0) return data;
  const keys = Object.keys(data[0]);
  const temporalKeys = keys.filter((k) => {
    const st = toTypeString(semanticTypes[k]);
    const vc = inferVisCategory(data.map((r) => r[k]));
    const stCategory = st ? getVisCategory(st) : null;
    return vc === "temporal" || stCategory === "temporal" || st === "Decade";
  });
  if (temporalKeys.length === 0) return data;
  const values = structuredClone(data);
  return values.map((r) => {
    for (const temporalKey of temporalKeys) {
      const val = r[temporalKey];
      const st = toTypeString(semanticTypes[temporalKey]);
      if (typeof val === "number") {
        if (st === "Year" || st === "Decade") {
          r[temporalKey] = `${Math.floor(val)}`;
        } else if (isLikelyTimestamp(val)) {
          r[temporalKey] = new Date(timestampToMs(val)).toISOString();
        } else {
          r[temporalKey] = String(val);
        }
      } else if (val instanceof Date) {
        r[temporalKey] = val.toISOString();
      } else {
        if ((st === "Year" || st === "Decade") && typeof val === "string") {
          r[temporalKey] = expandToFullYear(val);
        } else {
          r[temporalKey] = String(val);
        }
      }
    }
    return r;
  });
}
function resolveChannelSemantics(encodings, data, semanticTypes, convertedData) {
  const result = {};
  const temporalData = convertedData ?? data;
  for (const [channel, encoding] of Object.entries(encodings)) {
    const fieldName = encoding.field;
    if (!fieldName && encoding.aggregate !== "count") continue;
    if (!fieldName && encoding.aggregate === "count") {
      result[channel] = {
        field: "_count",
        semanticAnnotation: { semanticType: "Count" },
        type: "quantitative",
        aggregationDefault: "sum"
      };
      continue;
    }
    if (!fieldName) continue;
    const rawAnnotation = semanticTypes[fieldName];
    const semanticType = typeof rawAnnotation === "string" ? rawAnnotation || "" : rawAnnotation?.semanticType ?? "";
    const fieldValues = data.map((r) => r[fieldName]);
    const typeDecision = resolveEncodingType(
      semanticType,
      fieldValues,
      channel,
      data,
      fieldName
    );
    let resolvedType = typeDecision.vlType;
    if (encoding.type) {
      resolvedType = encoding.type;
    } else if (channel === "column" || channel === "row") {
      if (resolvedType !== "nominal" && resolvedType !== "ordinal") {
        resolvedType = "nominal";
      }
    }
    if (resolvedType === "quantitative") {
      const sampleValues = data.slice(0, 15).filter((r) => r[fieldName] != void 0).map((r) => r[fieldName]);
      const isoDateRegex = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;
      if (sampleValues.length > 0 && sampleValues.every((val) => isoDateRegex.test(`${val}`.trim()))) {
        resolvedType = "temporal";
      }
    }
    const fc = resolveFieldSemantics(rawAnnotation, fieldName, fieldValues);
    const annotation = fc.semanticAnnotation;
    const tickConstraint = resolveTickConstraint(annotation.semanticType, annotation.intrinsicDomain);
    const reversed = resolveReversed(annotation.semanticType, channel);
    const nice = resolveNice(annotation.semanticType, fc.domainConstraint);
    const stackable = resolveStackable(annotation.semanticType);
    const cs = {
      field: fieldName,
      semanticAnnotation: annotation,
      type: resolvedType,
      // From FieldSemantics (data identity)
      format: fc.format,
      tooltipFormat: fc.tooltipFormat,
      aggregationDefault: fc.aggregationDefault,
      scaleType: fc.scaleType,
      domainConstraint: fc.domainConstraint,
      cyclic: fc.cyclic || void 0,
      sortDirection: fc.sortDirection,
      binningSuggested: fc.binningSuggested || void 0,
      // Channel-specific visualization decisions
      nice,
      tickConstraint,
      reversed: reversed || void 0,
      stackable
    };
    if (encoding.aggregate) {
      if (encoding.aggregate === "count") {
        cs.field = "_count";
        cs.type = "quantitative";
      } else {
        cs.field = `${fieldName}_${encoding.aggregate}`;
        cs.type = "quantitative";
      }
    }
    if ((channel === "color" || channel === "group") && fieldName) {
      if (encoding.scheme && encoding.scheme !== "default") {
        cs.colorScheme = {
          scheme: encoding.scheme,
          type: "categorical",
          reason: "explicit user scheme"
        };
      } else {
        const encodingVLType = cs.type;
        const colorHint = resolveColorSchemeHint(semanticType, annotation, fieldValues);
        const uniqueValues = [...new Set(fieldValues)];
        cs.colorScheme = getRecommendedColorScheme(
          semanticType,
          encodingVLType,
          uniqueValues.length,
          fieldName,
          fieldValues,
          { type: colorHint.type }
        );
        if (cs.colorScheme.type === "diverging" && encodingVLType === "quantitative") {
          const nums = fieldValues.filter((v) => typeof v === "number" && !isNaN(v));
          const divInfo = resolveDivergingInfo(semanticType, annotation, nums);
          if (divInfo) {
            cs.colorScheme.domainMid = divInfo.midpoint;
          }
        }
      }
    }
    if (cs.type === "temporal" || semanticType && getVisCategory(semanticType) === "temporal") {
      const convertedFieldValues = temporalData.map((r) => r[fieldName]);
      const fmt = resolveTemporalFormat(convertedFieldValues, semanticType);
      if (fmt) cs.temporalFormat = fmt;
    }
    if (cs.type === "ordinal" || cs.type === "nominal") {
      if (!encoding.sortOrder && !encoding.sortBy) {
        const ordinalSort = inferOrdinalSortOrder(semanticType, fieldValues);
        if (ordinalSort) {
          cs.ordinalSortOrder = ordinalSort;
        }
      }
    }
    result[channel] = cs;
  }
  return result;
}

// src/echarts/templates/utils.ts
function getCategoryOrder(ctx, channel) {
  const resolved = ctx.resolvedEncodings?.[channel];
  if (Array.isArray(resolved?.sortValues) && resolved.sortValues.length > 0) {
    return resolved.sortValues.map(String);
  }
  const ordinal = resolved?.ordinalSortOrder ?? ctx.channelSemantics?.[channel]?.ordinalSortOrder;
  const enc = ctx.encodings?.[channel];
  const sortByChannel = enc?.sortBy;
  if (typeof sortByChannel === "string" && (sortByChannel === "x" || sortByChannel === "y" || sortByChannel === "color" || sortByChannel === "size" || sortByChannel === "group")) {
    const catField = ctx.channelSemantics?.[channel]?.field ?? enc?.field;
    const sortByField = ctx.channelSemantics?.[sortByChannel]?.field ?? ctx.encodings?.[sortByChannel]?.field;
    if (catField && sortByField && ctx.table) {
      return resolveCategoryOrder(ctx.table, catField, {
        ordinalSortOrder: ordinal,
        sortBy: sortByField,
        sortOrder: enc?.sortOrder
      });
    }
  }
  return ordinal;
}
function resolveCategoryOrder(data, catField, opts) {
  const base = extractCategories(data, catField, opts?.ordinalSortOrder);
  if (!opts?.sortBy) return base;
  const agg = /* @__PURE__ */ new Map();
  for (const row of data) {
    const cat = String(row[catField] ?? "");
    const v = Number(row[opts.sortBy]);
    if (Number.isFinite(v)) agg.set(cat, (agg.get(cat) ?? 0) + v);
  }
  const dir = opts.sortOrder === "ascending" ? 1 : -1;
  return [...base].sort((a, b) => dir * ((agg.get(a) ?? 0) - (agg.get(b) ?? 0)));
}
var isDiscrete2 = (type) => type === "nominal" || type === "ordinal";
function extractCategories(data, field, ordinalSortOrder) {
  const seen = /* @__PURE__ */ new Set();
  const result = [];
  for (const row of data) {
    const val = row[field];
    if (val != null) {
      const key = String(val);
      if (!seen.has(key)) {
        seen.add(key);
        result.push(key);
      }
    }
  }
  if (ordinalSortOrder && ordinalSortOrder.length > 0) {
    const orderMap = new Map(ordinalSortOrder.map((v, i) => [v, i]));
    result.sort((a, b) => {
      const ia = orderMap.get(a);
      const ib = orderMap.get(b);
      if (ia !== void 0 && ib !== void 0) return ia - ib;
      if (ia !== void 0) return -1;
      if (ib !== void 0) return 1;
      return 0;
    });
  }
  return result;
}
function groupBy(data, field) {
  const groups = /* @__PURE__ */ new Map();
  for (const row of data) {
    const key = String(row[field] ?? "");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return groups;
}
var DEFAULT_COLORS = [
  "#5470c6",
  "#91cc75",
  "#fac858",
  "#ee6666",
  "#73c0de",
  "#3ba272",
  "#fc8452",
  "#9a60b4",
  "#ea7ccc",
  "#48b8d0"
];
function detectAxes(channelSemantics) {
  const xCS = channelSemantics.x;
  const yCS = channelSemantics.y;
  if (xCS && isDiscrete2(xCS.type)) {
    return { categoryAxis: "x", valueAxis: "y" };
  }
  if (yCS && isDiscrete2(yCS.type)) {
    return { categoryAxis: "y", valueAxis: "x" };
  }
  if (xCS?.type === "quantitative" && yCS?.type === "temporal") {
    return { categoryAxis: "y", valueAxis: "x" };
  }
  if (xCS?.type === "temporal" && yCS?.type === "quantitative") {
    return { categoryAxis: "x", valueAxis: "y" };
  }
  return { categoryAxis: "x", valueAxis: "y" };
}

// src/echarts/colormap.ts
var ECHARTS_COLOR_MAPS = [
  {
    id: "cat10",
    type: "categorical",
    supportsDiscrete: true,
    supportsContinuous: false,
    background: "any",
    maxCategories: 10,
    colorblindSafe: false,
    colors: [
      "#5470c6",
      "#91cc75",
      "#fac858",
      "#ee6666",
      "#73c0de",
      "#3ba272",
      "#fc8452",
      "#9a60b4",
      "#ea7ccc",
      "#d48265"
    ]
  },
  {
    id: "cat20",
    type: "categorical",
    supportsDiscrete: true,
    supportsContinuous: false,
    background: "any",
    maxCategories: 20,
    colorblindSafe: false,
    colors: [
      "#5470c6",
      "#91cc75",
      "#fac858",
      "#ee6666",
      "#73c0de",
      "#3ba272",
      "#fc8452",
      "#9a60b4",
      "#ea7ccc",
      "#d48265",
      "#749f83",
      "#ca8622",
      "#bda29a",
      "#6e7074",
      "#546570",
      "#c4ccd3",
      "#4b565b",
      "#2f4554",
      "#61a0a8",
      "#c23531"
    ]
  },
  {
    id: "viridis",
    type: "sequential",
    supportsDiscrete: true,
    supportsContinuous: true,
    background: "any",
    colorblindSafe: true,
    colors: [
      "#440154",
      "#46327e",
      "#365c8d",
      "#277f8e",
      "#1fa187",
      "#4ac16d",
      "#a0da39",
      "#fde725"
    ]
  },
  {
    id: "RdBu",
    type: "diverging",
    supportsDiscrete: true,
    supportsContinuous: true,
    background: "any",
    colorblindSafe: false,
    diverging: true,
    preferredMidpoint: 0,
    colors: [
      "#b2182b",
      "#d6604d",
      "#f4a582",
      "#fddbc7",
      "#f7f7f7",
      "#d1e5f0",
      "#92c5de",
      "#4393c3",
      "#2166ac"
    ]
  }
];
function getMapById(id) {
  if (!id) return void 0;
  const key = String(id).toLowerCase();
  return ECHARTS_COLOR_MAPS.find((m) => m.id.toLowerCase() === key);
}
function getPaletteForScheme(id) {
  const entry = getMapById(id);
  return entry?.colors;
}
function pickEChartsPalette(decision) {
  if (!decision) {
    return DEFAULT_COLORS;
  }
  const { schemeType, schemeId, categoryCount } = decision;
  if (schemeId) {
    const fromId = getPaletteForScheme(schemeId);
    if (fromId && fromId.length > 0) {
      return fromId;
    }
  }
  const mapsOfType = ECHARTS_COLOR_MAPS.filter((m) => m.type === schemeType);
  if (schemeType === "categorical") {
    const k = categoryCount ?? 0;
    if (mapsOfType.length) {
      const candidates = mapsOfType.filter((m) => m.supportsDiscrete);
      if (candidates.length) {
        const byCapacity = candidates.filter((m) => m.maxCategories == null || m.maxCategories >= k).sort((a, b) => (a.maxCategories ?? Infinity) - (b.maxCategories ?? Infinity));
        const picked = byCapacity[0] ?? candidates[0];
        if (picked.colors.length) {
          return picked.colors;
        }
      }
    }
  } else if (schemeType === "sequential") {
    const seq = mapsOfType.find((m) => m.supportsContinuous) ?? getMapById("viridis");
    if (seq && seq.colors.length) {
      return seq.colors;
    }
  } else if (schemeType === "diverging") {
    const divergingFirst = mapsOfType.find((m) => m.diverging) ?? getMapById("RdBu");
    if (divergingFirst && divergingFirst.colors.length) {
      return divergingFirst.colors;
    }
  }
  return DEFAULT_COLORS;
}

// src/echarts/templates/rose.ts
var EC_ROSE_LEGEND_BRIDGE_SERIES_NAME = "__dfRoseLegendBridge__";
function roseRadius(value) {
  return Math.sqrt(Math.max(0, value));
}
var ecRoseChartDef = {
  chart: "Rose Chart",
  template: { mark: "arc", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "area",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const catField = channelSemantics.x?.field;
    const valField = channelSemantics.y?.field;
    const colorField = channelSemantics.color?.field;
    if (!catField || !valField) return;
    const categories = extractCategories(table, catField, channelSemantics.x?.ordinalSortOrder);
    if (categories.length === 0) return;
    const sortSlices = ctx.chartProperties?.sortSlices;
    if (sortSlices === "descending" || sortSlices === "ascending") {
      const totals = /* @__PURE__ */ new Map();
      for (const c of categories) totals.set(c, 0);
      for (const row of table) {
        const c = String(row[catField] ?? "");
        if (totals.has(c)) totals.set(c, totals.get(c) + (Number(row[valField]) || 0));
      }
      categories.sort(
        (a, b) => sortSlices === "descending" ? (totals.get(b) ?? 0) - (totals.get(a) ?? 0) : (totals.get(a) ?? 0) - (totals.get(b) ?? 0)
      );
    }
    const seriesArr = [];
    const legendData = [];
    if (colorField) {
      const groups = groupBy(table, colorField);
      const cumSum = categories.map(() => 0);
      for (const [name, rows] of groups) {
        legendData.push(name);
        const catAgg = /* @__PURE__ */ new Map();
        for (const row of rows) {
          const cat = String(row[catField] ?? "");
          const val = Number(row[valField]) || 0;
          catAgg.set(cat, (catAgg.get(cat) ?? 0) + val);
        }
        const data = categories.map((c, i) => {
          const val = catAgg.get(c) ?? 0;
          const prev = cumSum[i];
          const next = prev + val;
          cumSum[i] = next;
          return { value: roseRadius(next) - roseRadius(prev), _rawValue: val };
        });
        seriesArr.push({
          type: "bar",
          name,
          data,
          coordinateSystem: "polar",
          stack: "rose",
          emphasis: { focus: "series" }
        });
      }
    } else {
      const catAgg = /* @__PURE__ */ new Map();
      for (const row of table) {
        const cat = String(row[catField] ?? "");
        const val = Number(row[valField]) || 0;
        catAgg.set(cat, (catAgg.get(cat) ?? 0) + val);
      }
      const values = categories.map((c) => catAgg.get(c) ?? 0);
      for (const c of categories) {
        legendData.push(String(c));
      }
      seriesArr.push({
        type: "bar",
        data: categories.map((c, i) => ({
          value: roseRadius(values[i]),
          name: String(c),
          _rawValue: values[i]
        })),
        coordinateSystem: "polar",
        emphasis: { focus: "series" }
      });
      seriesArr.push({
        type: "pie",
        name: EC_ROSE_LEGEND_BRIDGE_SERIES_NAME,
        z: -10,
        silent: true,
        tooltip: { show: false },
        radius: 0,
        center: ["50%", "50%"],
        label: { show: false },
        labelLine: { show: false },
        emphasis: { disabled: true },
        data: categories.map((c) => ({
          name: String(c),
          value: 1,
          label: { show: false },
          labelLine: { show: false }
        }))
      });
    }
    const alignment = ctx.chartProperties?.alignment ?? "left";
    const n = categories.length;
    const startAngle = alignment === "center" && n > 0 ? 90 + 180 / n : 90;
    const hasLegend = legendData.length > 0;
    const maxLabelLen = hasLegend ? Math.max(...legendData.map((d) => d.length), 3) : 0;
    const estimatedLegendWidth = hasLegend ? Math.min(150, maxLabelLen * 7 + 40) : 0;
    const { radius: pressureRadius, canvasW: rawCanvasW, canvasH } = computeCircumferencePressure(categories.length, ctx.canvasSize, {
      minArcPx: 45,
      minRadius: 80,
      maxStretch: ctx.assembleOptions?.maxStretch,
      maxStretchX: ctx.assembleOptions?.maxStretchX,
      maxStretchY: ctx.assembleOptions?.maxStretchY
    });
    const canvasW = rawCanvasW + (hasLegend ? estimatedLegendWidth : 0);
    const polarRadius = hasLegend ? Math.min(pressureRadius, (canvasW - estimatedLegendWidth - 40) / 2, (canvasH - 40) / 2) : pressureRadius;
    const polarCenter = hasLegend ? [`${Math.round((canvasW - estimatedLegendWidth) / 2)}px`, "50%"] : void 0;
    const option = {
      tooltip: {
        trigger: "item",
        // Radii are sqrt-transformed for area-truth; show the true value
        // (stashed on each data item as `_rawValue`) instead of the radius.
        formatter: (params) => {
          const raw = params?.data?._rawValue;
          const shown = raw != null ? raw : params?.value;
          const cat = params?.name != null && params.name !== "" ? String(params.name) : "";
          const series = params?.seriesName;
          const head = series && series !== cat ? cat ? `${cat} \xB7 ${series}` : String(series) : cat;
          const marker = params?.marker ?? "";
          return `${marker}${head}: <b>${shown}</b>`;
        }
      },
      angleAxis: {
        type: "category",
        data: categories,
        startAngle
      },
      radiusAxis: {
        // hide axis line for cleaner look
        axisLine: { show: false },
        axisTick: { show: false },
        // Radii encode sqrt(value); showing the raw sqrt tick numbers would
        // misrepresent the scale, so suppress them (values live in tooltips).
        axisLabel: { show: false }
      },
      polar: {
        radius: polarRadius,
        ...polarCenter != null ? { center: polarCenter } : {}
      },
      series: seriesArr,
      // 颜色调色板由 color-decisions / ecApplyLayoutToSpec 注入到 option.color
      // Canvas size
      _width: canvasW,
      _height: canvasH
    };
    if (hasLegend) {
      option.legend = {
        data: legendData,
        type: legendData.length > 8 ? "scroll" : "plain",
        orient: "vertical",
        right: 10,
        top: "middle",
        textStyle: { fontSize: ctx.layout.legendFontSize }
      };
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "alignment",
      label: "Alignment",
      type: "discrete",
      options: [
        { value: "left", label: "Left (default)" },
        { value: "center", label: "Center" }
      ]
    },
    {
      key: "sortSlices",
      label: "Sort slices",
      type: "discrete",
      options: [
        { value: "none", label: "Data order" },
        { value: "descending", label: "Largest first" },
        { value: "ascending", label: "Smallest first" }
      ],
      defaultValue: "none"
    }
  ]
};

// src/echarts/instantiate-spec.ts
function pickEvenlySpacedColorIndices(paletteLength, count) {
  if (paletteLength <= 0 || count <= 0) return [];
  if (count === 1) return [0];
  if (count >= paletteLength) {
    return Array.from({ length: count }, (_, i) => i % paletteLength);
  }
  const maxIndex = paletteLength - 1;
  const step = maxIndex / (count - 1);
  const indices = [];
  for (let i = 0; i < count; i += 1) {
    const idx = Math.round(i * step);
    indices.push(idx);
  }
  return indices;
}
function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const intVal = parseInt(m[1], 16);
  return {
    r: intVal >> 16 & 255,
    g: intVal >> 8 & 255,
    b: intVal & 255
  };
}
function componentToHex(c) {
  const v = Math.max(0, Math.min(255, Math.round(c)));
  const s = v.toString(16);
  return s.length === 1 ? `0${s}` : s;
}
function rgbToHex(r, g, b) {
  return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`;
}
function samplePaletteTo256(base) {
  if (base.length === 0) return [];
  if (base.length === 1) return new Array(256).fill(base[0]);
  const stops = base.map(hexToRgb);
  if (stops.some((s) => s == null)) {
    return Array.from({ length: 256 }, (_, i) => base[i % base.length]);
  }
  const rgbStops = stops;
  const segmentCount = rgbStops.length - 1;
  const result = [];
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    const pos = t * segmentCount;
    const idx = Math.floor(pos);
    const localT = pos - idx;
    const c0 = rgbStops[idx];
    const c1 = rgbStops[Math.min(idx + 1, segmentCount)];
    const r = c0.r + (c1.r - c0.r) * localT;
    const g = c0.g + (c1.g - c0.g) * localT;
    const b = c0.b + (c1.b - c0.b) * localT;
    result.push(rgbToHex(r, g, b));
  }
  return result;
}
function buildRankColorLookupFromLegend(legendData, palette) {
  const labels = legendData.map(
    (d) => typeof d === "string" ? d : d?.name ?? String(d ?? "")
  );
  const numericEntries = labels.map((name) => {
    const v = Number(name);
    return Number.isFinite(v) ? { name, value: v } : null;
  }).filter((x) => x != null);
  if (numericEntries.length === 0) return /* @__PURE__ */ new Map();
  const values = numericEntries.map((e) => e.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const span = maxVal - minVal;
  const sampled = samplePaletteTo256(palette);
  if (sampled.length === 0) return /* @__PURE__ */ new Map();
  const colorMap = /* @__PURE__ */ new Map();
  for (const { name, value } of numericEntries) {
    let t;
    if (!Number.isFinite(span) || span === 0) {
      t = 0.5;
    } else {
      t = (value - minVal) / span;
      if (t < 0) t = 0;
      if (t > 1) t = 1;
    }
    const idx = Math.round(t * (sampled.length - 1));
    colorMap.set(name, sampled[idx]);
  }
  return colorMap;
}
function roundAxisNumber(v) {
  if (!Number.isFinite(v)) return v;
  return Number(v.toFixed(10));
}
function cleanAxisNumericFields(axis) {
  if (!axis || typeof axis !== "object") return;
  for (const key of ["min", "max", "interval"]) {
    if (typeof axis[key] === "number") {
      axis[key] = roundAxisNumber(axis[key]);
    }
  }
}
function pyramidPanelHeightMatchVegaLite(yCardinality, canvasSize) {
  const baseWidth = canvasSize.width ?? 400;
  const baseHeight = canvasSize.height ?? 320;
  const baseRefSize = 300;
  const sizeRatio = Math.max(baseWidth, baseHeight) / baseRefSize;
  const defaultStep = Math.round(20 * Math.max(1, sizeRatio));
  let panelHeight = baseHeight;
  if (yCardinality > 0) {
    const pressure = yCardinality * defaultStep / baseHeight;
    if (pressure > 1) {
      const stretch = Math.min(2, Math.pow(pressure, 0.5));
      panelHeight = Math.round(baseHeight * stretch);
    }
  }
  return panelHeight;
}
function estimatePyramidYCategoryInsetPx(option, gw) {
  const data = option.yAxis?.data;
  if (!Array.isArray(data) || data.length === 0) return 0;
  const maxLen = Math.max(...data.map((d) => String(d).length), 1);
  const est = Math.round(8 + maxLen * 7.5 + 4);
  return Math.min(Math.max(0, est), Math.floor(gw * 0.45));
}
function pyramidNiceSymmetricMax(absMax) {
  if (!Number.isFinite(absMax) || absMax <= 0) return 1;
  const exp = Math.floor(Math.log10(absMax));
  const f = absMax / 10 ** exp;
  let ceilF;
  if (f <= 1) ceilF = 1;
  else if (f <= 2) ceilF = 2;
  else if (f <= 3) ceilF = 3;
  else if (f <= 5) ceilF = 5;
  else if (f <= 6) ceilF = 6;
  else if (f <= 10) ceilF = 10;
  else ceilF = 10;
  return ceilF * 10 ** exp;
}
function pyramidNiceTickStep(niceMax2) {
  const candidates = [1, 2, 2.5, 5, 10].flatMap((m) => [m, m * 10, m * 100, m * 1e3, m * 1e4, m * 1e5, m * 1e6]);
  const sorted = [...new Set(candidates)].filter((s) => s > 0 && s <= niceMax2 / 2).sort((a, b) => b - a);
  for (const step of sorted) {
    const n = niceMax2 / step;
    if (n >= 2 && n <= 8 && Number.isInteger(n)) return step;
  }
  return niceMax2 / 4;
}
function estimateGroupedBoxplotMinPlotWidth(option, layout) {
  if (option?.xAxis?.type !== "category" || !Array.isArray(option?.series)) return 0;
  const boxplotSeriesCount = option.series.filter((s) => s?.type === "boxplot").length;
  if (boxplotSeriesCount <= 1) return 0;
  let categoryCount = Array.isArray(option.xAxis?.data) ? option.xAxis.data.length : 0;
  if (categoryCount <= 0 && layout.xNominalCount > 0) {
    categoryCount = Math.max(1, Math.round(layout.xNominalCount / boxplotSeriesCount));
  }
  if (categoryCount <= 0) return 0;
  const MIN_BOX_WIDTH = 10;
  const MIN_INNER_GAP = 3;
  const SIDE_PADDING = 6;
  const perCategoryWidth = boxplotSeriesCount * MIN_BOX_WIDTH + (boxplotSeriesCount - 1) * MIN_INNER_GAP + SIDE_PADDING * 2;
  return categoryCount * perCategoryWidth;
}
function placePyramidChannelHeaders(option) {
  const hdr = option._pyramidChannelHeader;
  if (!hdr || !option.grid) return;
  const cw = Number(option._width);
  if (!Number.isFinite(cw) || cw <= 0) return;
  delete option._pyramidChannelHeader;
  const gl = Number(option.grid.left) || 0;
  const gr = Number(option.grid.right) || 0;
  const gt = Number(option.grid.top) || 0;
  const gw = Math.max(0, cw - gl - gr);
  const centerX = gl + gw / 2;
  const dx = gw / 4;
  const topY = Math.max(4, gt - 10);
  const L = estimatePyramidYCategoryInsetPx(option, gw);
  const innerW = Math.max(gw - L, 1);
  const zX = gl + L + innerW / 2;
  const style = {
    fontSize: 11,
    fontWeight: "bold",
    fill: "#555",
    textAlign: "center",
    textVerticalAlign: "bottom"
  };
  if (hdr.mode === "single") {
    option.graphic = [{ type: "text", left: zX, top: topY, z: 100, style: { ...style, text: hdr.text } }];
  } else {
    const directX = centerX - dx;
    const geoPartnerX = centerX + dx;
    const innerPartnerX = zX + innerW / 4;
    const partnerX = 0.9 * geoPartnerX - 0.02 * innerPartnerX;
    option.graphic = [
      { type: "text", left: directX, top: topY, z: 100, style: { ...style, text: hdr.left } },
      { type: "text", left: partnerX, top: topY, z: 100, style: { ...style, text: hdr.right } }
    ];
  }
}
function ecApplyLayoutToSpec(option, context, warnings) {
  const { channelSemantics, layout, canvasSize } = context;
  const hasAxes = !!(option.xAxis || option.yAxis);
  for (const axis of ["x", "y"]) {
    const axisObj = option[`${axis}Axis`];
    if (!axisObj || axisObj.type !== "value") continue;
    const cs = channelSemantics[axis];
    if (!cs?.zero) continue;
    const decision = cs.zero;
    if (axisObj.scale === void 0) {
      axisObj.scale = !decision.zero;
    }
    if (!decision.zero && decision.domainPadFraction > 0 && cs.field) {
      const pairField = axis === "y" ? channelSemantics.y2?.field : axis === "x" ? channelSemantics.x2?.field : void 0;
      const domainFields = pairField ? [cs.field, pairField] : [cs.field];
      const numericValues = context.table.flatMap((r) => domainFields.map((f) => r[f])).filter((v) => v != null && typeof v === "number" && !isNaN(v));
      const padded = computePaddedDomain(numericValues, decision.domainPadFraction);
      if (padded) {
        axisObj.min = padded[0];
        axisObj.max = padded[1];
      }
    }
  }
  for (const axis of ["x", "y"]) {
    const bandedCount = axis === "x" ? layout.xContinuousAsDiscrete : layout.yContinuousAsDiscrete;
    if (bandedCount <= 1) continue;
    const axisObj = option[`${axis}Axis`];
    if (!axisObj || axisObj.type !== "value" || axisObj.min != null) continue;
    const cs = channelSemantics[axis];
    if (!cs?.field || cs.type !== "quantitative" && cs.type !== "temporal") continue;
    const isTemporal2 = cs.type === "temporal";
    const numericVals = context.table.map((r) => {
      const raw = r[cs.field];
      if (raw == null) return NaN;
      return isTemporal2 ? +new Date(raw) : +raw;
    }).filter((v) => !isNaN(v));
    if (numericVals.length <= 1) continue;
    const minVal = Math.min(...numericVals);
    const maxVal = Math.max(...numericVals);
    const dataRange = maxVal - minVal;
    if (dataRange === 0) continue;
    const pad = dataRange / (bandedCount - 1) / 2;
    axisObj.min = minVal - pad;
    axisObj.max = maxVal + pad;
  }
  if (option.xAxis) {
    if (option.xAxis.name) {
      option.xAxis.nameLocation = option.xAxis.nameLocation || "middle";
      option.xAxis.nameGap = option.xAxis.nameGap || 25;
      option.xAxis.nameTextStyle = { fontSize: layout.titleFontSize, ...option.xAxis.nameTextStyle || {} };
    }
  }
  if (option.yAxis) {
    if (option.yAxis.name) {
      option.yAxis.nameLocation = option.yAxis.nameLocation || "middle";
      option.yAxis.nameGap = option.yAxis.nameGap || 45;
      option.yAxis.nameTextStyle = { fontSize: layout.titleFontSize, ...option.yAxis.nameTextStyle || {} };
    }
  }
  if (option.singleAxis) {
    if (option.singleAxis.name) {
      option.singleAxis.nameLocation = option.singleAxis.nameLocation || "middle";
      option.singleAxis.nameGap = option.singleAxis.nameGap || 25;
      option.singleAxis.nameTextStyle = { fontSize: layout.titleFontSize, ...option.singleAxis.nameTextStyle || {} };
    }
    if (!option.singleAxis.axisLabel) option.singleAxis.axisLabel = {};
    option.singleAxis.axisLabel.fontSize = option.singleAxis.axisLabel.fontSize || layout.xLabel.fontSize;
  }
  const hasLegend = !!option.legend;
  const hasVisualMap = !!option.visualMap;
  const isDualLegend = hasLegend && hasVisualMap;
  if (hasLegend) {
    const alreadyPositioned = option.legend.orient && (option.legend.right !== void 0 || option.legend.left !== void 0);
    let legendTitle = option._legendTitle;
    if (legendTitle == null) {
      const colorField = channelSemantics?.color?.field;
      const groupField = channelSemantics?.group?.field;
      legendTitle = colorField || groupField;
    }
    if (legendTitle != null) delete option._legendTitle;
    if (!alreadyPositioned) {
      const rawLegendData = option.legend.data || [];
      const legendLabels = rawLegendData.map((d) => typeof d === "string" ? d : d?.name ?? "");
      if (isDualLegend) {
        const highCardinality = legendLabels.length >= 16;
        option._legendWidth = 0;
        option.legend = {
          ...option.legend,
          bottom: 0,
          left: "center",
          orient: "horizontal",
          textStyle: {
            fontSize: highCardinality ? 8 : layout.legendFontSize,
            ...option.legend.textStyle || {}
          },
          ...legendLabels.length > 10 ? { type: "scroll" } : {},
          ...highCardinality ? { itemWidth: 12, itemHeight: 12 } : {}
        };
        if (legendTitle != null) {
          const titleGraphic = {
            type: "text",
            bottom: 22,
            left: "center",
            z: 100,
            style: {
              text: legendTitle,
              fontSize: layout.legendFontSize,
              fontWeight: "bold",
              fill: "#333",
              textAlign: "center"
            }
          };
          const existing = option.graphic;
          option.graphic = Array.isArray(existing) ? [...existing, titleGraphic] : existing ? [existing, titleGraphic] : [titleGraphic];
        }
      } else {
        const maxLabelLen = Math.max(...legendLabels.map((l) => l.length), 3);
        const highCardinality = legendLabels.length >= 16;
        const legendSymbolWidth = highCardinality ? 12 : 14;
        const legendItemGap = 5;
        const estimatedTextWidth = Math.min(120, maxLabelLen * 7 + 30);
        option._legendWidth = legendSymbolWidth + legendItemGap + estimatedTextWidth;
        const LEGEND_GAP2 = 12;
        const CANVAS_BUFFER2 = 16;
        const rightMarginPx = option._legendWidth + LEGEND_GAP2 + CANVAS_BUFFER2;
        const hasYTitle2 = !!option.yAxis?.name;
        const gridLeft = (hasYTitle2 ? 70 : 50) + CANVAS_BUFFER2;
        let plotW = layout?.subplotWidth ?? canvasSize?.width ?? 400;
        const xIsDiscreteForLegend = layout.xNominalCount > 0 || layout.xContinuousAsDiscrete > 0;
        if (xIsDiscreteForLegend) {
          let xItemCount = layout.xNominalCount || layout.xContinuousAsDiscrete || 0;
          if (layout.xStepUnit === "group" && option.series && Array.isArray(option.series) && layout.xNominalCount > 0) {
            const barSeriesCount = option.series.filter((s) => s.type === "bar").length || option.series.length;
            if (barSeriesCount > 0) {
              xItemCount = Math.max(1, Math.round(layout.xNominalCount / barSeriesCount));
            }
          }
          plotW = xItemCount > 0 ? layout.xStep * xItemCount : plotW;
        }
        const boxplotMinWForLegend = estimateGroupedBoxplotMinPlotWidth(option, layout);
        if (boxplotMinWForLegend > 0) {
          plotW = Math.max(plotW, boxplotMinWForLegend);
        }
        const effectiveChartWidth = plotW + gridLeft + rightMarginPx;
        const legendLeftPx = Math.max(0, effectiveChartWidth - rightMarginPx);
        option.legend = {
          ...option.legend,
          top: legendTitle != null ? 20 : 0,
          left: legendLeftPx,
          orient: option.legend.orient || "vertical",
          align: "left",
          // icon on left, text on right
          textStyle: {
            fontSize: highCardinality ? 8 : layout.legendFontSize,
            ...option.legend.textStyle || {}
          },
          ...legendLabels.length > 10 ? { type: "scroll" } : {},
          ...highCardinality ? { itemWidth: 12, itemHeight: 12 } : {}
        };
        if (legendTitle != null) {
          const titleGraphic = {
            type: "text",
            left: legendLeftPx,
            top: 4,
            z: 100,
            style: {
              text: legendTitle,
              fontSize: layout.legendFontSize,
              fontWeight: "bold",
              fill: "#333",
              textAlign: "left"
            }
          };
          const existing = option.graphic;
          option.graphic = Array.isArray(existing) ? [...existing, titleGraphic] : existing ? [existing, titleGraphic] : [titleGraphic];
        }
      }
    } else {
      const rawData = option.legend.data || [];
      const legendLabels = rawData.map((d) => typeof d === "string" ? d : d?.name ?? "");
      const maxLabelLen = Math.max(...legendLabels.map((l) => l.length), 3);
      option._legendWidth = Math.min(150, maxLabelLen * 7 + 30);
    }
  }
  const hasXTitle = !!option.xAxis?.name;
  const hasYTitle = !!option.yAxis?.name;
  const CANVAS_BUFFER = 16;
  const LEGEND_GAP = 12;
  const VISUALMAP_GAP = 18;
  const VISUALMAP_RIGHT_OFFSET = 10;
  const legendWidth = hasLegend ? option._legendWidth || 120 : 20;
  const visualMapWidth = option._visualMapWidth || 0;
  if (visualMapWidth) delete option._visualMapWidth;
  const rightMargin = isDualLegend ? hasVisualMap ? VISUALMAP_RIGHT_OFFSET + visualMapWidth + VISUALMAP_GAP : 10 : (hasLegend ? legendWidth : hasVisualMap ? visualMapWidth + VISUALMAP_GAP : 10) + LEGEND_GAP;
  const bottomLegendExtra = isDualLegend ? 30 : 0;
  const gridMargin = {
    left: (hasYTitle ? 70 : 50) + CANVAS_BUFFER,
    right: rightMargin + CANVAS_BUFFER,
    top: 20 + CANVAS_BUFFER,
    bottom: (hasXTitle ? 45 : 30) + CANVAS_BUFFER + bottomLegendExtra
  };
  if (hasAxes) {
    if (!option.grid) option.grid = {};
    option.grid.left = gridMargin.left;
    option.grid.right = gridMargin.right;
    option.grid.top = gridMargin.top;
    option.grid.bottom = gridMargin.bottom;
  }
  if ((hasAxes || option.singleAxis) && !option._width) {
    const xIsDiscrete = layout.xNominalCount > 0 || layout.xContinuousAsDiscrete > 0;
    const yIsDiscrete = layout.yNominalCount > 0 || layout.yContinuousAsDiscrete > 0;
    let plotWidth;
    let plotHeight;
    if (xIsDiscrete) {
      let xItemCount = layout.xNominalCount || layout.xContinuousAsDiscrete || 0;
      if (layout.xStepUnit === "group" && option.series && Array.isArray(option.series) && layout.xNominalCount > 0) {
        const barSeriesCount = option.series.filter((s) => s.type === "bar").length || option.series.length;
        if (barSeriesCount > 0) {
          xItemCount = Math.max(1, Math.round(layout.xNominalCount / barSeriesCount));
        }
      }
      plotWidth = xItemCount > 0 ? layout.xStep * xItemCount : layout.subplotWidth || canvasSize.width;
      const boxplotMinW = estimateGroupedBoxplotMinPlotWidth(option, layout);
      if (boxplotMinW > 0) {
        plotWidth = Math.max(plotWidth, boxplotMinW);
      }
    } else {
      plotWidth = layout.subplotWidth || canvasSize.width;
    }
    if (yIsDiscrete && layout.yStepUnit !== "group") {
      const yItemCount = layout.yNominalCount || layout.yContinuousAsDiscrete || 0;
      plotHeight = yItemCount > 0 ? layout.yStep * yItemCount : layout.subplotHeight || canvasSize.height;
    } else {
      plotHeight = layout.subplotHeight || canvasSize.height;
    }
    if (context.chartType === "Pyramid Chart" && yIsDiscrete && layout.yStepUnit !== "group") {
      const yItemCount = layout.yNominalCount || layout.yContinuousAsDiscrete || 0;
      if (yItemCount > 0) {
        plotHeight = Math.max(
          plotHeight,
          pyramidPanelHeightMatchVegaLite(yItemCount, canvasSize)
        );
      }
    }
    option._width = plotWidth + gridMargin.left + gridMargin.right;
    option._height = plotHeight + gridMargin.top + gridMargin.bottom;
  }
  if (context.chartType === "Pyramid Chart") {
    placePyramidChannelHeaders(option);
  }
  if (option.series && Array.isArray(option.series)) {
    const barSeries = option.series.filter((s) => s.type === "bar");
    if (barSeries.length > 0) {
      const catAxis = option.xAxis?.type === "category" ? "x" : "y";
      const step = catAxis === "x" ? layout.xStep : layout.yStep;
      const stepUnit = catAxis === "x" ? layout.xStepUnit : layout.yStepUnit;
      const isStacked = barSeries.some((s) => s.stack != null);
      const isPyramidMirror = context.chartType === "Pyramid Chart" && barSeries.length === 2 && !isStacked;
      const isRosePolar = context.chartType === "Rose Chart" && barSeries.every((s) => s.coordinateSystem === "polar");
      if (step > 0) {
        const bandPadding = layout.stepPadding;
        const catGapPct = `${Math.round(bandPadding * 100)}%`;
        if (!isStacked && (stepUnit === "group" || barSeries.length > 1) && !isPyramidMirror && !isRosePolar) {
          const usableStep = step * (1 - bandPadding);
          const barW = Math.max(1, Math.floor(usableStep / (barSeries.length + 1)));
          for (const s of barSeries) {
            s.barWidth = barW;
            s.barGap = "0%";
          }
          barSeries[0].barCategoryGap = catGapPct;
        } else {
          for (const s of barSeries) {
            s.barCategoryGap = catGapPct;
          }
          if (isPyramidMirror) {
            for (const s of barSeries) {
              s.barGap = "-100%";
            }
          }
        }
      }
    }
  }
  if (option.xAxis && layout.xLabel) {
    if (!option.xAxis.axisLabel) option.xAxis.axisLabel = {};
    const templateRotate = option.xAxis.axisLabel.rotate;
    const isCategoryX = option.xAxis.type === "category";
    const isTimeX = option.xAxis.type === "time";
    const preserveTemplateRotate = isCategoryX && (templateRotate === 0 || templateRotate === 90) || isTimeX && templateRotate === 90;
    if (layout.xLabel.labelAngle != null && layout.xLabel.labelAngle !== 0 && !preserveTemplateRotate) {
      option.xAxis.axisLabel.rotate = -layout.xLabel.labelAngle;
    }
    if (layout.xLabel.fontSize) {
      option.xAxis.axisLabel.fontSize = layout.xLabel.fontSize;
    }
    if (layout.xLabel.labelLimit && layout.xLabel.labelLimit < 100) {
      const maxLen = layout.xLabel.labelLimit;
      option.xAxis.axisLabel.formatter = (value) => {
        if (typeof value === "string" && value.length > maxLen) {
          return value.substring(0, maxLen) + "\u2026";
        }
        return value;
      };
    }
  }
  if (option.xAxis && option.grid && option.xAxis.axisLabel) {
    const rotate = Math.abs(option.xAxis.axisLabel.rotate || 0);
    if (rotate >= 45) {
      const labelFontSize = option.xAxis.axisLabel.fontSize || 11;
      const labelLimit = layout.xLabel?.labelLimit || 20;
      const categoryData = Array.isArray(option.xAxis.data) ? option.xAxis.data : void 0;
      const actualMaxChars = categoryData && categoryData.length > 0 ? Math.max(...categoryData.map((d) => String(d?.value ?? d ?? "").length)) : labelLimit;
      const maxChars = Math.min(actualMaxChars, labelLimit);
      const estimatedLabelWidth = maxChars * labelFontSize * 0.6;
      const rotatedHeight = Math.min(estimatedLabelWidth, 120);
      const extraBottom = Math.max(0, rotatedHeight - 30);
      if (extraBottom > 0) {
        option.grid.bottom = (option.grid.bottom || 61) + extraBottom;
        if (option.xAxis.name) {
          option.xAxis.nameGap = (option.xAxis.nameGap || 25) + extraBottom;
        }
        if (option._height) {
          option._height = option._height + extraBottom;
        }
      }
    }
  }
  if (option.yAxis && layout.yLabel) {
    if (!option.yAxis.axisLabel) option.yAxis.axisLabel = {};
    if (layout.yLabel.labelAngle && layout.yLabel.labelAngle !== 0 && option.yAxis.type !== "category") {
      option.yAxis.axisLabel.rotate = -layout.yLabel.labelAngle;
    }
    if (layout.yLabel.fontSize) {
      option.yAxis.axisLabel.fontSize = layout.yLabel.fontSize;
    }
  }
  if (context.chartType === "Pyramid Chart") {
    const lineStyle = { color: "#333", width: 1 };
    const tickStyle = { color: "#333", width: 1 };
    if (option.xAxis?.type === "value") {
      const xAxis = option.xAxis;
      const rawAbs = Math.max(
        Math.abs(Number(xAxis.min) || 0),
        Math.abs(Number(xAxis.max) || 0)
      );
      if (rawAbs > 0 && Number.isFinite(rawAbs)) {
        const nice = pyramidNiceSymmetricMax(rawAbs);
        xAxis.min = -nice;
        xAxis.max = nice;
        xAxis.interval = pyramidNiceTickStep(nice);
      }
      xAxis.axisLine = { show: true, lineStyle, ...xAxis.axisLine || {} };
      xAxis.axisLine.show = true;
      xAxis.axisTick = {
        show: true,
        length: 6,
        lineStyle: tickStyle,
        ...typeof xAxis.axisTick === "object" ? xAxis.axisTick : {}
      };
      xAxis.axisTick.show = true;
      if (!xAxis.axisLabel) xAxis.axisLabel = {};
      if (xAxis.axisLabel.fontSize == null) xAxis.axisLabel.fontSize = 11;
      if (xAxis.axisLabel.color == null) xAxis.axisLabel.color = "#333";
      if (!xAxis.nameTextStyle) xAxis.nameTextStyle = { fontSize: 12, color: "#333" };
    }
    if (option.yAxis?.type === "category") {
      const yAxis = option.yAxis;
      if (yAxis.boundaryGap === void 0) yAxis.boundaryGap = true;
      yAxis.axisLine = {
        show: true,
        onZero: false,
        lineStyle,
        ...typeof yAxis.axisLine === "object" && yAxis.axisLine ? yAxis.axisLine : {}
      };
      yAxis.axisLine.show = true;
      yAxis.axisTick = {
        show: true,
        alignWithLabel: true,
        interval: 0,
        length: 6,
        lineStyle: tickStyle,
        ...typeof yAxis.axisTick === "object" && yAxis.axisTick ? yAxis.axisTick : {}
      };
      yAxis.axisTick.show = true;
      if (!yAxis.axisLabel) yAxis.axisLabel = {};
      if (yAxis.axisLabel.fontSize == null) yAxis.axisLabel.fontSize = 11;
      if (yAxis.axisLabel.color == null) yAxis.axisLabel.color = "#333";
      if (!yAxis.nameTextStyle) yAxis.nameTextStyle = { fontSize: 12, color: "#333" };
    }
  }
  for (const axis of ["x", "y"]) {
    const axisObj = option[`${axis}Axis`];
    if (!axisObj || axisObj.type !== "category") continue;
    const cs = channelSemantics[axis];
    if (!cs?.field || cs.type !== "nominal" && cs.type !== "ordinal") continue;
    const semanticType = toTypeString(context.semanticTypes[cs.field]);
    if (getVisCategory(semanticType) !== "temporal") continue;
    const fieldVals = context.table.map((r) => r[cs.field]).filter((v) => v != null);
    const datelikeCnt = fieldVals.filter(
      (v) => typeof v !== "string" || looksLikeDateString(String(v))
    ).length;
    if (datelikeCnt < fieldVals.length * 0.5) continue;
    const analysis = analyzeTemporalField(fieldVals);
    if (!analysis) continue;
    const votes = computeDataVotes(analysis.same);
    const semLevel = SEMANTIC_LEVEL[semanticType];
    if (semLevel !== void 0) votes[semLevel] += 3;
    const { level, score } = pickBestLevel(votes);
    if (score < 5) continue;
    const fmt = levelToFormat(level, analysis);
    if (!fmt) continue;
    if (!axisObj.axisLabel) axisObj.axisLabel = {};
    const existingFormatter = axisObj.axisLabel.formatter;
    axisObj.axisLabel.formatter = (value) => {
      const formatted = formatCategoryTemporal(value, fmt);
      return typeof existingFormatter === "function" ? existingFormatter(formatted) : formatted;
    };
  }
  for (const axis of ["x", "y"]) {
    const cs = channelSemantics[axis];
    if (cs?.temporalFormat && option[`${axis}Axis`]) {
      const axisObj = option[`${axis}Axis`];
      if (axisObj.type === "time") {
        if (!axisObj.axisLabel) axisObj.axisLabel = {};
        axisObj.axisLabel.formatter = convertTemporalFormat(cs.temporalFormat);
      }
      if (axisObj.type === "value" && cs.type === "temporal") {
        if (axisObj.name === "Count") continue;
        if (!axisObj.axisLabel) axisObj.axisLabel = {};
        const fmt = cs.temporalFormat;
        axisObj.axisLabel.formatter = (val) => formatTimestamp(val, fmt);
      }
    }
  }
  for (const axisKey of ["xAxis", "yAxis"]) {
    const axisVal = option[axisKey];
    if (Array.isArray(axisVal)) {
      axisVal.forEach(cleanAxisNumericFields);
    } else {
      cleanAxisNumericFields(axisVal);
    }
  }
  const enc = option._encodingTooltip;
  if (enc?.trigger === "axis" && enc.categoryLabel != null && option.xAxis?.type === "time") {
    const xFmt = channelSemantics?.x?.temporalFormat;
    if (xFmt) {
      option._encodingTooltip = { ...enc, categoryFormat: "temporal", temporalFormat: xFmt };
    }
  }
  const visualMapOwnsSeriesColor = context.chartType === "Heatmap" && !!option.visualMap;
  const decisions = context.colorDecisions;
  const colorDecision = decisions ? decisions.color ?? decisions.group : void 0;
  let effectivePalette;
  if (decisions && colorDecision) {
    let palette;
    const isCategoricalScheme = colorDecision.schemeType === "categorical";
    if (isCategoricalScheme) {
      const fromResolved = context.resolvedEncodings?.color?.colorPalette ?? context.resolvedEncodings?.group?.colorPalette;
      if (colorDecision.schemeId) {
        const fromRegistry = getPaletteForScheme(colorDecision.schemeId);
        if (fromRegistry && fromRegistry.length > 0) {
          palette = fromRegistry;
        }
      }
      if (!palette) {
        const targetId = (colorDecision.categoryCount ?? 0) > 10 ? "cat20" : "cat10";
        palette = getPaletteForScheme(targetId) ?? (fromResolved && fromResolved.length > 0 ? fromResolved : DEFAULT_COLORS);
      }
    } else {
      if (colorDecision.schemeId) {
        const fromRegistry = getPaletteForScheme(colorDecision.schemeId);
        if (fromRegistry && fromRegistry.length > 0) {
          palette = fromRegistry;
        }
      }
      if (!palette) {
        if (colorDecision.schemeType === "sequential") {
          palette = getPaletteForScheme("viridis") ?? DEFAULT_COLORS;
        } else if (colorDecision.schemeType === "diverging") {
          palette = getPaletteForScheme("RdBu") ?? DEFAULT_COLORS;
        } else {
          palette = DEFAULT_COLORS;
        }
      }
    }
    if (palette && palette.length) {
      option.color = [...palette];
      effectivePalette = palette;
    }
  } else {
    const colorPalette = context.resolvedEncodings?.color?.colorPalette ?? context.resolvedEncodings?.group?.colorPalette;
    if (colorPalette?.length) {
      option.color = [...colorPalette];
      effectivePalette = colorPalette;
    }
  }
  if (!effectivePalette || effectivePalette.length === 0) {
    const cat10 = getPaletteForScheme("cat10");
    if (cat10 && cat10.length > 0) {
      effectivePalette = cat10;
      if (!option.color) {
        option.color = [...cat10];
      }
    }
  }
  if (visualMapOwnsSeriesColor) {
    delete option.color;
    effectivePalette = void 0;
  }
  if (effectivePalette && effectivePalette.length > 0 && Array.isArray(option.series)) {
    const palette_ = effectivePalette;
    const n = palette_.length;
    const schemeType = colorDecision?.schemeType;
    let drivingColorChannel;
    if (decisions?.color && channelSemantics.color) {
      drivingColorChannel = channelSemantics.color;
    } else if (decisions?.group && channelSemantics.group) {
      drivingColorChannel = channelSemantics.group;
    }
    const colorSemanticType = drivingColorChannel?.semanticAnnotation?.semanticType;
    const isRankLikeColor = !!colorSemanticType && colorSemanticType === "Rank";
    const useEvenSpacing = !isRankLikeColor && (schemeType === "sequential" || schemeType === "diverging");
    if (context.chartType === "Regression" && option.legend && Array.isArray(option.legend.data)) {
      const legendLabels = option.legend.data.map(
        (d) => typeof d === "string" ? d : d?.name ?? ""
      );
      const categoryToColor = /* @__PURE__ */ new Map();
      if (useEvenSpacing) {
        const spacedLegendIndices = pickEvenlySpacedColorIndices(n, legendLabels.length);
        legendLabels.forEach((name, i) => {
          if (!name) return;
          const paletteIndex = spacedLegendIndices[i] ?? i % n;
          categoryToColor.set(name, palette_[paletteIndex]);
        });
      } else {
        let colorIdx = 0;
        for (const name of legendLabels) {
          if (!name) continue;
          categoryToColor.set(name, palette_[colorIdx % n]);
          colorIdx += 1;
        }
      }
      option.series.forEach((s, idx) => {
        if (!s) return;
        const rawName = typeof s.name === "string" ? s.name : "";
        const baseName = rawName.endsWith(" (trend)") ? rawName.slice(0, -" (trend)".length) : rawName;
        const mappedColor = baseName && categoryToColor.has(baseName) ? categoryToColor.get(baseName) : palette_[idx % n];
        s.itemStyle = s.itemStyle || {};
        s.itemStyle.color = mappedColor;
      });
    } else if (context.chartType === "Boxplot" && option.legend && Array.isArray(option.legend.data)) {
      const legendLabels = option.legend.data.map(
        (d) => typeof d === "string" ? d : d?.name ?? ""
      );
      const categoryToColor = /* @__PURE__ */ new Map();
      legendLabels.forEach((name, i) => {
        if (!name) return;
        categoryToColor.set(name, palette_[i % n]);
      });
      option.series.forEach((s, idx) => {
        if (!s) return;
        const rawName = typeof s.name === "string" ? s.name : s.name != null ? String(s.name) : "";
        const baseName = rawName.endsWith(" (outliers)") ? rawName.slice(0, -" (outliers)".length) : rawName;
        const mappedColor = baseName && categoryToColor.has(baseName) ? categoryToColor.get(baseName) : palette_[idx % n];
        s.itemStyle = s.itemStyle || {};
        s.itemStyle.color = mappedColor;
        if (s.type === "boxplot") {
          s.itemStyle.borderColor = mappedColor;
        }
      });
    } else if (context.chartType === "Pyramid Chart" && Array.isArray(option.series)) {
      const pal = effectivePalette && effectivePalette.length > 0 ? effectivePalette : DEFAULT_COLORS;
      const cLeft = pal[0];
      const cRight = pal.length > 3 ? pal[3] : pal[Math.min(1, pal.length - 1)];
      let barIdx = 0;
      for (const s of option.series) {
        if (!s || s.type !== "bar") continue;
        s.itemStyle = s.itemStyle || {};
        s.itemStyle.color = barIdx === 0 ? cLeft : cRight;
        barIdx += 1;
        if (barIdx >= 2) break;
      }
    } else if (context.chartType === "Rose Chart" && Array.isArray(option.series)) {
      const polarBars = option.series.filter(
        (s) => s && s.type === "bar" && s.coordinateSystem === "polar"
      );
      const stacked = polarBars.some((s) => s.stack != null && s.stack !== "");
      const single = polarBars.length === 1 && !stacked;
      if (single && Array.isArray(polarBars[0].data)) {
        const legendLabels = option.legend?.data?.map(
          (d) => typeof d === "string" ? d : d?.name ?? ""
        ) ?? [];
        const categoryToColor = /* @__PURE__ */ new Map();
        legendLabels.forEach((name, i) => {
          if (!name) return;
          categoryToColor.set(name, effectivePalette[i % n]);
        });
        const s0 = polarBars[0];
        s0.data.forEach((item, i) => {
          const rawName = typeof item === "object" && item != null && typeof item.name === "string" ? item.name : legendLabels[i] ?? "";
          const mapped = rawName && categoryToColor.has(rawName) ? categoryToColor.get(rawName) : effectivePalette[i % n];
          const color = mapped;
          if (item != null && typeof item === "object") {
            item.itemStyle = item.itemStyle || {};
            if (item.itemStyle.color == null) item.itemStyle.color = color;
          } else {
            s0.data[i] = { value: item, name: String(rawName || i), itemStyle: { color } };
          }
        });
        const bridge = option.series.find(
          (s) => s && s.type === "pie" && s.name === EC_ROSE_LEGEND_BRIDGE_SERIES_NAME
        );
        if (bridge && Array.isArray(bridge.data)) {
          bridge.data.forEach((item, i) => {
            const rawName = typeof item === "object" && item != null && typeof item.name === "string" ? item.name : "";
            const color = (rawName && categoryToColor.get(rawName)) ?? effectivePalette[i % n];
            if (item != null && typeof item === "object") {
              item.itemStyle = item.itemStyle || {};
              if (item.itemStyle.color == null) item.itemStyle.color = color;
            }
          });
        }
        if (option.legend) {
          const order = Array.isArray(option.angleAxis?.data) && option.angleAxis.data.length > 0 ? option.angleAxis.data.map((c) => String(c)) : legendLabels.filter(Boolean);
          const names = order.length > 0 ? order : s0.data.map((item, j) => typeof item === "object" && item != null && item.name != null ? String(item.name) : String(j));
          names.forEach((name, i) => {
            if (!name || categoryToColor.has(name)) return;
            categoryToColor.set(name, effectivePalette[i % n]);
          });
          option.legend.show = true;
          option.legend.selectedMode = false;
          option.legend.data = names;
        }
      }
    } else {
      const colorByDataItem = context.chartType === "Pie Chart" || context.chartType === "Rose Chart" || context.chartType === "Streamgraph" || context.chartType === "Sunburst Chart";
      if (colorByDataItem) ; else if (context.chartType === "Radar Chart") {
        const legendLabels = option.legend?.data?.map(
          (d) => typeof d === "string" ? d : d?.name ?? ""
        ) ?? [];
        const categoryToColor = /* @__PURE__ */ new Map();
        legendLabels.forEach((name, i) => {
          if (!name) return;
          categoryToColor.set(name, palette_[i % n]);
        });
        const radarSeries = Array.isArray(option.series) ? option.series.find((s) => s && s.type === "radar") : null;
        if (radarSeries && Array.isArray(radarSeries.data)) {
          radarSeries.data.forEach((item, i) => {
            if (!item) return;
            const rawName = typeof item.name === "string" ? item.name : "";
            const mapped = rawName && categoryToColor.has(rawName) ? categoryToColor.get(rawName) : palette_[i % n];
            const color = mapped;
            item.itemStyle = item.itemStyle || {};
            if (item.itemStyle.color == null) item.itemStyle.color = color;
            item.areaStyle = item.areaStyle || {};
            if (item.areaStyle.color == null) item.areaStyle.color = color;
            item.lineStyle = item.lineStyle || {};
            if (item.lineStyle.color == null) item.lineStyle.color = color;
          });
        }
      } else {
        const hasLegend2 = !!option.legend && Array.isArray(option.legend.data);
        const rankLegendColorMap = isRankLikeColor && hasLegend2 ? buildRankColorLookupFromLegend(option.legend.data, palette_) : /* @__PURE__ */ new Map();
        const colorableCount = option.series.filter((s) => s && s.itemStyle?.color == null).length;
        const spacedIndices = useEvenSpacing && colorableCount > 0 ? pickEvenlySpacedColorIndices(n, colorableCount) : null;
        let colorIdx = 0;
        option.series.forEach((s, idx) => {
          if (!s) return;
          s.itemStyle = s.itemStyle || {};
          if (s.itemStyle.color != null) return;
          if (isRankLikeColor && rankLegendColorMap.size > 0) {
            const rawName = typeof s.name === "string" ? s.name : s.name != null ? String(s.name) : "";
            const mapped = rawName ? rankLegendColorMap.get(rawName) : void 0;
            if (mapped) {
              s.itemStyle.color = mapped;
              if (s.type === "boxplot") s.itemStyle.borderColor = mapped;
              colorIdx += 1;
              return;
            }
          }
          const paletteIndex = spacedIndices ? spacedIndices[colorIdx] ?? colorIdx % n : colorIdx % n;
          const color = palette_[paletteIndex];
          s.itemStyle.color = color;
          if (s.type === "boxplot") {
            s.itemStyle.borderColor = color;
          }
          colorIdx += 1;
        });
      }
    }
  }
  if (layout.truncations && layout.truncations.length > 0) {
    const axisPlaceholders = { xAxis: /* @__PURE__ */ new Set(), yAxis: /* @__PURE__ */ new Set() };
    for (const trunc of layout.truncations) {
      warnings.push({
        severity: "warning",
        code: "overflow",
        message: trunc.message,
        channel: trunc.channel,
        field: trunc.field
      });
      const axisKey = trunc.channel === "x" ? "xAxis" : "yAxis";
      if (trunc.channel === "x" || trunc.channel === "y") {
        axisPlaceholders[axisKey].add(trunc.placeholder);
        if (option[axisKey]?.data && Array.isArray(option[axisKey].data)) {
          option[axisKey].data.push(trunc.placeholder);
        }
      }
    }
    for (const axisKey of ["xAxis", "yAxis"]) {
      const placeholders = axisPlaceholders[axisKey];
      if (placeholders.size === 0 || !option[axisKey]) continue;
      if (!option[axisKey].axisLabel) option[axisKey].axisLabel = {};
      const existingColor = option[axisKey].axisLabel.color;
      option[axisKey].axisLabel.color = (params) => placeholders.has(params) ? "#999999" : typeof existingColor === "function" ? existingColor(params) : existingColor ?? "#000";
    }
  }
}
function convertTemporalFormat(d3Format) {
  return d3Format.replace(/%Y/g, "{yyyy}").replace(/%y/g, "{yy}").replace(/%b/g, "{MMM}").replace(/%B/g, "{MMMM}").replace(/%m/g, "{MM}").replace(/%d/g, "{dd}").replace(/%H/g, "{HH}").replace(/%M/g, "{mm}").replace(/%S/g, "{ss}");
}
var MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
var MONTH_FULL2 = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function formatCategoryTemporal(value, d3Format) {
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return formatTimestamp(d.getTime(), d3Format);
}
function formatTimestamp(val, d3Format) {
  const d = new Date(val);
  const pad = (n) => n < 10 ? "0" + n : String(n);
  return d3Format.replace(/%Y/g, String(d.getFullYear())).replace(/%y/g, String(d.getFullYear()).slice(-2)).replace(/%B/g, MONTH_FULL2[d.getMonth()]).replace(/%b/g, MONTH_ABBR[d.getMonth()]).replace(/%m/g, pad(d.getMonth() + 1)).replace(/%d/g, pad(d.getDate())).replace(/%H/g, pad(d.getHours())).replace(/%M/g, pad(d.getMinutes())).replace(/%S/g, pad(d.getSeconds()));
}
function fmtNumForTooltip(v) {
  if (v == null) return "";
  const n = Number(v);
  return isNaN(n) ? String(v) : Number.isInteger(n) ? String(n) : n.toFixed(1);
}
function buildEncodingTooltipFormatter(option) {
  const enc = option._encodingTooltip;
  if (!enc) return null;
  if (enc.trigger === "axis" && enc.categoryLabel != null) {
    const categoryLabel = enc.categoryLabel;
    const valueLabel = enc.valueLabel ?? "Value";
    const categoryFormat = enc.categoryFormat;
    const temporalFormat = enc.temporalFormat ?? "%b %d, %Y";
    const filterScatterOnly = !!enc.filterScatterOnly;
    return (params) => {
      const rawList = Array.isArray(params) ? params : [params];
      const list = filterScatterOnly ? rawList.filter((item) => item && item.seriesType === "scatter") : rawList;
      if (list.length === 0) return "";
      const p = list[0];
      let cat;
      const rawCat = p.axisValue ?? p.name ?? "";
      if (categoryFormat === "temporal" && (rawCat !== "" && rawCat != null)) {
        const ts = typeof rawCat === "number" ? rawCat : new Date(rawCat).getTime();
        cat = Number.isFinite(ts) ? formatTimestamp(ts, temporalFormat) : String(rawCat);
      } else {
        cat = String(rawCat);
      }
      const parts2 = [`${categoryLabel}: ${cat}`];
      for (const item of list) {
        const name = item.seriesName ?? valueLabel;
        let val = item.value != null ? item.value : Array.isArray(item.data) ? item.data[item.dataIndex] : item.data;
        if (Array.isArray(val) && val.length >= 2) val = val[1];
        parts2.push(`${name}: ${fmtNumForTooltip(val)}`);
      }
      return parts2.join("<br/>");
    };
  }
  const parts = enc.parts;
  if (!parts || !Array.isArray(parts) || parts.length === 0) return null;
  return (params) => {
    if (params == null) return "";
    const d = Array.isArray(params.data) ? params.data : params.data != null ? [params.data] : [];
    const out = [];
    for (const p of parts) {
      let val;
      if (p.from === "series") {
        val = params.seriesName ?? params.name;
      } else if (p.from === "name") {
        val = params.name;
      } else if (p.from === "value") {
        val = params.value;
      } else {
        const idx = p.index ?? 0;
        val = d[idx];
        if (val != null && typeof val === "object" && "value" in val) val = val.value;
      }
      if (val == null && p.from !== "series" && p.from !== "name") continue;
      let str;
      if (p.format === "temporal") {
        const ts = typeof val === "number" ? val : new Date(val).getTime();
        str = Number.isFinite(ts) ? formatTimestamp(ts, p.temporalFormat ?? "%b %d, %Y") : String(val ?? "");
      } else if (p.format === "category" && p.categoryNames) {
        const i = Number(val);
        str = Number.isInteger(i) && p.categoryNames[i] != null ? p.categoryNames[i] : String(val ?? "");
      } else if (p.format === "number" || p.from === "data" && p.format !== "category") {
        str = fmtNumForTooltip(val);
      } else {
        str = String(val ?? "");
      }
      out.push(`${p.label}: ${str}`);
    }
    return out.join("<br/>");
  };
}
function ecApplyTooltips(option) {
  if (!option.tooltip) {
    option.tooltip = {};
  }
  const encodingFormatter = buildEncodingTooltipFormatter(option);
  if (encodingFormatter) {
    delete option._encodingTooltip;
    option.tooltip.formatter = encodingFormatter;
  }
  if (!option.tooltip.trigger) {
    const hasScatter = option.series?.some((s) => s.type === "scatter");
    const hasPie = option.series?.some((s) => s.type === "pie");
    const hasRadar = option.series?.some((s) => s.type === "radar");
    const hasHeatmap = option.series?.some((s) => s.type === "heatmap");
    const hasCandlestick = option.series?.some((s) => s.type === "candlestick");
    const hasThemeRiver = option.series?.some((s) => s.type === "themeRiver");
    option.tooltip.trigger = hasScatter || hasPie || hasRadar || hasHeatmap || hasThemeRiver ? "item" : "axis";
    if (hasCandlestick && !option.tooltip.axisPointer) {
      option.tooltip.axisPointer = { type: "cross" };
    }
    if (hasThemeRiver && !option.tooltip.formatter) {
      option.tooltip.formatter = (params) => {
        if (!params || !params.data) return "";
        const [date, value, name] = params.data;
        const color = params.color || "#333";
        const dateStr = date instanceof Date ? date.toLocaleDateString() : String(date);
        return `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:6px;"></span><b>${name}</b><br/>${dateStr}: ${value}`;
      };
    }
  }
}

// src/echarts/templates/scatter.ts
function computeSymbolSize(width, height, pointCount) {
  const canvasArea = width * height;
  const areaPerPoint = canvasArea / Math.max(1, pointCount);
  const idealDiameter = Math.sqrt(areaPerPoint * 0.05);
  return Math.max(3, Math.min(12, Math.round(idealDiameter)));
}
var ecScatterPlotDef = {
  chart: "Scatter Plot",
  template: { mark: "circle", encoding: {} },
  // skeleton for compatibility
  channels: ["x", "y", "color", "size", "opacity", "column", "row"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const xField = channelSemantics.x?.field;
    const yField = channelSemantics.y?.field;
    const colorField = channelSemantics.color?.field;
    const sizeField = channelSemantics.size?.field;
    const sizeRange = ctx.resolvedEncodings?.size?.sizeRange;
    const sizeType = channelSemantics.size?.type;
    if (!xField || !yField) return;
    const EC_SIZE_MIN = 4;
    const EC_SIZE_MAX = 30;
    let rangeMin = Math.max(EC_SIZE_MIN, Math.min(EC_SIZE_MAX, sizeRange?.[0] ?? 6));
    let rangeMaxClamped = Math.max(EC_SIZE_MIN, Math.min(EC_SIZE_MAX, sizeRange?.[1] ?? 20));
    rangeMaxClamped = Math.max(rangeMin, rangeMaxClamped);
    if (rangeMaxClamped <= rangeMin) {
      rangeMin = EC_SIZE_MIN;
      rangeMaxClamped = EC_SIZE_MAX;
    }
    const sizeUniqueCount = sizeField && table.length > 0 ? new Set(table.map((r) => String(r[sizeField]))).size : 0;
    const sizeValuesSample = sizeField && table.length > 0 ? table.slice(0, 50).map((r) => r[sizeField]).filter((v) => v != null) : [];
    const allSizeValuesNumeric = sizeValuesSample.length > 0 && sizeValuesSample.every((v) => !isNaN(Number(v)) && String(v).trim() !== "");
    const useOrdinalSize = sizeType === "ordinal" || sizeType === "nominal" || sizeType === "quantitative" && sizeUniqueCount >= 2 && sizeUniqueCount <= 12 || sizeField && sizeUniqueCount >= 2 && sizeUniqueCount <= 12 && !allSizeValuesNumeric;
    let scaleSize;
    let sizeOrderForLegend;
    let sizeDomainMin;
    let sizeDomainMax;
    if (useOrdinalSize && sizeField) {
      const sizeOrder = extractCategories(table, sizeField, getCategoryOrder(ctx, "size"));
      sizeOrderForLegend = sizeOrder;
      const orderMap = new Map(sizeOrder.map((val, i) => [String(val), i]));
      const n = sizeOrder.length;
      scaleSize = (raw) => {
        if (raw == null) return rangeMin;
        const key = String(raw);
        const index = orderMap.get(key);
        if (index === void 0) return rangeMin;
        const t = n > 1 ? index / (n - 1) : 0;
        return Math.round(rangeMin + t * (rangeMaxClamped - rangeMin));
      };
    } else if (sizeField) {
      const vals = table.map((r) => r[sizeField]).map((v) => v != null ? Number(v) : NaN).filter((v) => !isNaN(v));
      const sizeMin = vals.length ? Math.min(...vals) : 0;
      const sizeMax = vals.length ? Math.max(...vals) : 1;
      sizeDomainMin = sizeMin;
      sizeDomainMax = sizeMax;
      scaleSize = (raw) => {
        const v = raw != null ? Number(raw) : NaN;
        if (isNaN(v)) return rangeMin;
        let t;
        if (sizeMax === sizeMin) t = 0.5;
        else {
          const sqrtMin = Math.sqrt(Math.max(0, sizeMin));
          const sqrtMax = Math.sqrt(Math.max(0, sizeMax));
          const sqrtV = Math.sqrt(Math.max(0, v));
          t = (sqrtV - sqrtMin) / (sqrtMax - sqrtMin);
        }
        t = Math.max(0, Math.min(1, t));
        return Math.round(rangeMin + t * (rangeMaxClamped - rangeMin));
      };
    } else {
      scaleSize = () => rangeMin;
    }
    const usePiecewiseSizeVisualMap = sizeOrderForLegend && sizeOrderForLegend.length > 0 && sizeField;
    const useContinuousSizeVisualMap = sizeField != null && sizeDomainMin !== void 0 && sizeDomainMax !== void 0;
    const useVisualMapForSize = usePiecewiseSizeVisualMap || useContinuousSizeVisualMap;
    const xType = channelSemantics.x?.type;
    const yType = channelSemantics.y?.type;
    const xIsCategorical = xType === "nominal" || xType === "ordinal";
    const yIsCategorical = yType === "nominal" || yType === "ordinal";
    const xCategories = xIsCategorical ? extractCategories(table, xField, getCategoryOrder(ctx, "x")) : [];
    const yCategories = yIsCategorical ? extractCategories(table, yField, getCategoryOrder(ctx, "y")) : [];
    const xCategoryToIndex = new Map(xCategories.map((c, i) => [String(c), i]));
    const yCategoryToIndex = new Map(yCategories.map((c, i) => [String(c), i]));
    const option = {
      tooltip: { trigger: "item" },
      xAxis: xIsCategorical ? {
        type: "category",
        data: xCategories,
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        axisLabel: { interval: 0, rotate: 90 },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      } : { type: "value", name: xField, nameLocation: "middle", nameGap: 30 },
      yAxis: yIsCategorical ? {
        type: "category",
        data: yCategories,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisLabel: { interval: 0, rotate: 0 },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      } : { type: "value", name: yField, nameLocation: "middle", nameGap: 40 },
      series: []
    };
    if (usePiecewiseSizeVisualMap) {
      option.visualMap = [
        {
          type: "piecewise",
          show: false,
          dimension: 2,
          pieces: sizeOrderForLegend.map((name) => ({
            value: name,
            symbolSize: scaleSize(name)
          })),
          orient: "vertical",
          right: 10,
          top: "center",
          itemGap: 8,
          itemSymbol: "circle",
          formatter: (value) => value,
          title: sizeField
        }
      ];
      option._visualMapWidth = 88;
      const ordLegendRight = 28;
      const ordGap = 8;
      const ordRowGap = 6;
      const ordFontSize = 10;
      const ordTitleHeight = 20;
      const ordLabelWidth = 44;
      const canvasH = ctx.canvasSize?.height ?? 300;
      const maxCircleR = Math.max(...sizeOrderForLegend.map((name) => scaleSize(name) / 2));
      const legendWidth = ordLabelWidth + ordGap + 2 * maxCircleR;
      const hasColorEncoding = !!channelSemantics.color?.field;
      const fallbackPalette = getPaletteForScheme("cat10") ?? DEFAULT_COLORS;
      const fallbackColor = fallbackPalette[0];
      const scatterColor = hasColorEncoding ? "#cccccc" : ctx.resolvedEncodings?.color?.colorPalette?.[0] ?? fallbackColor;
      const rowHeights = sizeOrderForLegend.map((name) => Math.max(scaleSize(name), 16) + ordRowGap);
      const totalLegendHeight = ordTitleHeight + rowHeights.reduce((a, b) => a + b, 0);
      const ordLegendTop = Math.max(10, (canvasH - totalLegendHeight) / 2);
      const legendChildren = [
        {
          type: "text",
          left: 0,
          top: 0,
          style: {
            text: sizeField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "left"
          }
        }
      ];
      let rowTop = ordTitleHeight;
      for (let i = 0; i < sizeOrderForLegend.length; i++) {
        const name = sizeOrderForLegend[i];
        const r = scaleSize(name) / 2;
        const rowH = rowHeights[i];
        const circleTop = rowTop + (rowH - scaleSize(name)) / 2;
        const textTop = rowTop + (rowH - ordFontSize) / 2;
        legendChildren.push({
          type: "circle",
          left: maxCircleR - r,
          top: circleTop - r,
          shape: { cx: r, cy: r, r },
          style: { fill: scatterColor }
        });
        legendChildren.push({
          type: "text",
          left: 2 * maxCircleR + ordGap,
          top: textTop,
          style: {
            text: name,
            fontSize: ordFontSize,
            fill: "#333",
            textAlign: "left"
          }
        });
        rowTop += rowH;
      }
      const ordLegendGraphic = {
        type: "group",
        right: ordLegendRight,
        top: ordLegendTop,
        width: legendWidth,
        z: 100,
        children: legendChildren
      };
      const existingGraphic = option.graphic;
      option.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, ordLegendGraphic] : existingGraphic ? [existingGraphic, ordLegendGraphic] : [ordLegendGraphic];
    } else if (useContinuousSizeVisualMap) {
      const SIZE_SPREAD_MIN = 20;
      const sizeMaxForMap = Math.max(rangeMaxClamped, rangeMin + SIZE_SPREAD_MIN);
      const fmtSize = (v) => Number.isInteger(v) ? String(v) : v.toFixed(1);
      const sizeVisualMap = {
        type: "continuous",
        show: true,
        min: sizeDomainMin,
        max: sizeDomainMax,
        dimension: 2,
        inRange: { symbolSize: [rangeMin, sizeMaxForMap] },
        orient: "vertical",
        right: 50,
        top: "10.0%",
        bottom: "10.0%",
        padding: 0,
        itemGap: 0,
        text: [fmtSize(sizeDomainMax), fmtSize(sizeDomainMin)],
        textStyle: { fontSize: 10 },
        seriesIndex: 0,
        name: sizeField
      };
      const hasColorEncoding = !!colorField;
      if (hasColorEncoding) {
        sizeVisualMap.controller = {
          inRange: {
            color: ["#888"]
          }
        };
      } else {
        const basePalette = ctx.resolvedEncodings?.color?.colorPalette ?? getPaletteForScheme("cat10") ?? DEFAULT_COLORS;
        const baseColor = basePalette[0];
        sizeVisualMap.controller = {
          inRange: {
            color: [baseColor]
          }
        };
      }
      if (option.visualMap) {
        option.visualMap.push(sizeVisualMap);
      } else {
        option.visualMap = [sizeVisualMap];
      }
      option._visualMapWidth = 70;
      option.graphic = option.graphic || [];
      const existingGraphic = Array.isArray(option.graphic) ? option.graphic : [option.graphic];
      option.graphic = [
        ...existingGraphic,
        {
          type: "text",
          right: 50,
          top: 10,
          z: 100,
          style: {
            text: sizeField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        }
      ];
    }
    if (!xIsCategorical) {
      option.xAxis.scale = channelSemantics.x?.zero ? !channelSemantics.x.zero.zero : true;
    }
    if (!yIsCategorical) {
      option.yAxis.scale = channelSemantics.y?.zero ? !channelSemantics.y.zero.zero : true;
    }
    const opacity = chartProperties?.opacity ?? 1;
    const xVal = (row) => xIsCategorical ? xCategoryToIndex.get(String(row[xField] ?? "")) ?? 0 : row[xField];
    const yVal = (row) => yIsCategorical ? yCategoryToIndex.get(String(row[yField] ?? "")) ?? 0 : row[yField];
    const pointData = (row) => sizeField != null ? [xVal(row), yVal(row), row[sizeField]] : [xVal(row), yVal(row)];
    const colorPalette = ctx.resolvedEncodings?.color?.colorPalette ?? ctx.resolvedEncodings?.group?.colorPalette ?? DEFAULT_COLORS;
    const legendOpts = ctx.resolvedEncodings?.color ?? ctx.resolvedEncodings?.group;
    const colorType = channelSemantics.color?.type ?? ctx.resolvedEncodings?.color?.type;
    const isTemporalColor = colorField && colorType === "temporal";
    const isContinuousColor = colorField && (colorType === "quantitative" || colorType === "temporal");
    if (isContinuousColor) {
      const colorDim = sizeField != null ? 3 : 2;
      const toColorVal = isTemporalColor ? (v) => v != null ? new Date(v).getTime() : NaN : (v) => v != null ? Number(v) : NaN;
      const pointDataWithColor = (row) => {
        const x = xVal(row);
        const y = yVal(row);
        const c = toColorVal(row[colorField]);
        if (sizeField != null) return [x, y, row[sizeField], c];
        return [x, y, c];
      };
      const colorVals = table.map((r) => toColorVal(r[colorField])).filter((v) => !isNaN(v));
      const colorMin = colorVals.length ? Math.min(...colorVals) : isTemporalColor ? Date.now() : 0;
      const colorMax = colorVals.length ? Math.max(...colorVals) : isTemporalColor ? Date.now() : 1;
      const scheme = ctx.encodings?.color?.scheme ?? "";
      const defaultGrayRange = ["#f5f5f5", "#e0e0e0", "#9e9e9e", "#616161", "#424242"];
      const greensRange = ["#f7fcf5", "#c7e9c0", "#41ab5d", "#006d2c", "#00441b"];
      const decisionSchemeId = colorDecisions?.color?.schemeId ?? colorDecisions?.group?.schemeId;
      const paletteFromDecision = decisionSchemeId ? getPaletteForScheme(decisionSchemeId) : void 0;
      const inRange = paletteFromDecision && paletteFromDecision.length > 0 ? paletteFromDecision : /green/i.test(scheme) ? greensRange : colorPalette.length >= 2 ? [colorPalette[colorPalette.length - 1], colorPalette[0]] : defaultGrayRange;
      const VM_BAR_RIGHT = 50;
      const VM_BAR_WIDTH = 70;
      const VM_GAP = 16;
      const VM_TITLE_TOP = 10;
      const VM_FONT_SIZE = 10;
      const REF_H = 400;
      const VM_BAR_TOP_PX = 40;
      const VM_BAR_BOTTOM_PX = 40;
      const VM_TOP_PCT = (VM_BAR_TOP_PX / REF_H * 100).toFixed(1) + "%";
      const VM_BOTTOM_PCT = (VM_BAR_BOTTOM_PX / REF_H * 100).toFixed(1) + "%";
      const hasSizeVisualMap = option.visualMap && Array.isArray(option.visualMap) && option.visualMap.some((vm) => vm.inRange?.symbolSize != null);
      const colorBarRight = hasSizeVisualMap ? VM_BAR_RIGHT + VM_BAR_WIDTH + VM_GAP : VM_BAR_RIGHT;
      const temporalFormat2 = channelSemantics.color?.temporalFormat ?? "%b %d, %Y";
      const formatColorLabel = (val) => isTemporalColor ? formatTimestamp(val, temporalFormat2) : String(val);
      const colorVisualMap = {
        type: "continuous",
        min: colorMin,
        max: colorMax,
        dimension: colorDim,
        inRange: { color: inRange },
        orient: "vertical",
        right: colorBarRight,
        top: VM_TOP_PCT,
        bottom: VM_BOTTOM_PCT,
        padding: 0,
        itemGap: 0,
        text: [formatColorLabel(colorMax), formatColorLabel(colorMin)],
        formatter: formatColorLabel,
        textStyle: { fontSize: VM_FONT_SIZE },
        show: true,
        seriesIndex: 0,
        name: colorField
      };
      if (option.visualMap) {
        option.visualMap.push(colorVisualMap);
      } else {
        option.visualMap = colorVisualMap;
      }
      option._visualMapWidth = hasSizeVisualMap ? VM_BAR_WIDTH + VM_GAP + VM_BAR_WIDTH : VM_BAR_WIDTH;
      const vmGraphics = [
        {
          type: "text",
          right: colorBarRight,
          top: VM_TITLE_TOP,
          z: 100,
          style: {
            text: colorField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        }
      ];
      const existingGraphic = option.graphic;
      option.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, ...vmGraphics] : existingGraphic ? [existingGraphic, ...vmGraphics] : vmGraphics;
      const data = table.map((row) => pointDataWithColor(row));
      const seriesOpt = {
        type: "scatter",
        data,
        itemStyle: { opacity }
      };
      if (sizeField != null && !useVisualMapForSize) {
        seriesOpt.symbolSize = (value) => scaleSize(Array.isArray(value) ? value[2] : value);
      }
      option.series.push(seriesOpt);
    } else if (colorField) {
      const colorOrder = extractCategories(table, colorField, getCategoryOrder(ctx, "color"));
      const groups = /* @__PURE__ */ new Map();
      for (const row of table) {
        const key = String(row[colorField] ?? "");
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(pointData(row));
      }
      const legendNames = colorOrder.length > 0 ? colorOrder : [...groups.keys()];
      const hasSizeBySeries = sizeField != null && !useVisualMapForSize;
      option.legend = {
        data: legendNames.map((name) => {
          const data = groups.get(name) ?? [];
          if (!hasSizeBySeries || data.length === 0) return name;
          const sizes = data.map((d) => d.length >= 3 ? scaleSize(d[2]) : rangeMin);
          sizes.sort((a, b) => a - b);
          const medianSize = sizes[Math.floor(sizes.length / 2)] ?? rangeMin;
          return { name, symbolSize: medianSize, itemStyle: { symbolSize: medianSize } };
        }),
        show: true
      };
      option._legendTitle = colorField;
      if (legendOpts?.legendSymbolSize != null && !hasSizeBySeries) {
        option.legend.itemWidth = legendOpts.legendSymbolSize;
        option.legend.itemHeight = legendOpts.legendSymbolSize;
        option.legend.itemGap = 8;
      }
      if (legendOpts?.legendLabelFontSize != null) {
        option.legend.textStyle = option.legend.textStyle ?? {};
        option.legend.textStyle.fontSize = legendOpts.legendLabelFontSize;
      }
      legendNames.forEach((name) => {
        const data = groups.get(name) ?? [];
        if (data.length === 0) return;
        const seriesOpt = {
          name,
          type: "scatter",
          data,
          // 不在模板中显式设置颜色，交由 ecApplyLayoutToSpec 使用
          // colorDecisions / colormap（通常是 cat10）统一分配。
          itemStyle: {
            opacity
          }
        };
        if (hasSizeBySeries) {
          seriesOpt.symbolSize = (value) => scaleSize(Array.isArray(value) ? value[2] : value);
        }
        option.series.push(seriesOpt);
      });
    } else {
      const data = table.map((row) => pointData(row));
      const seriesOpt = {
        type: "scatter",
        data,
        itemStyle: { opacity }
      };
      if (sizeField != null && !useVisualMapForSize) {
        seriesOpt.symbolSize = (value) => scaleSize(Array.isArray(value) ? value[2] : value);
      } else if (useContinuousSizeVisualMap && sizeDomainMin !== void 0 && sizeDomainMax !== void 0) {
        const SIZE_SPREAD_MIN = 20;
        const sizeSpread = Math.max(rangeMaxClamped - rangeMin, SIZE_SPREAD_MIN);
        const sizeMaxMapped = rangeMin + sizeSpread;
        seriesOpt.symbolSize = (value) => {
          const v = Array.isArray(value) ? value[2] : value;
          const num = Number(v);
          if (v == null || isNaN(num)) return rangeMin;
          const span = sizeDomainMax - sizeDomainMin;
          const t = span <= 0 ? 0.5 : Math.max(0, Math.min(1, (num - sizeDomainMin) / span));
          return Math.round(rangeMin + t * (sizeMaxMapped - rangeMin));
        };
      }
      option.series.push(seriesOpt);
    }
    const xName = option.xAxis?.name ?? xField ?? "X";
    const yName = option.yAxis?.name ?? yField ?? "Y";
    const sizeName = sizeField ?? null;
    const colorName = colorField ?? null;
    const temporalFormat = channelSemantics.color?.temporalFormat ?? "%b %d, %Y";
    const tooltipParts = [
      { from: "data", index: 0, label: xName, format: xIsCategorical ? "category" : "number", categoryNames: xIsCategorical ? xCategories : void 0 },
      { from: "data", index: 1, label: yName, format: yIsCategorical ? "category" : "number", categoryNames: yIsCategorical ? yCategories : void 0 }
    ];
    if (sizeName != null) tooltipParts.push({ from: "data", index: 2, label: sizeName, format: "number" });
    if (colorName != null) {
      if (isContinuousColor) {
        tooltipParts.push({
          from: "data",
          index: sizeField != null ? 3 : 2,
          label: colorName,
          format: isTemporalColor ? "temporal" : "number",
          temporalFormat
        });
      } else {
        tooltipParts.push({ from: "series", label: colorName });
      }
    }
    option.tooltip = option.tooltip ?? {};
    option._encodingTooltip = { trigger: "item", parts: tooltipParts };
    const vmList = Array.isArray(option.visualMap) ? option.visualMap : option.visualMap ? [option.visualMap] : [];
    const seriesCount = option.series?.length ?? 0;
    if (seriesCount > 1) {
      const allIndices = option.series.map((_, i) => i);
      for (const vm of vmList) {
        if (vm.type === "continuous" && vm.inRange?.symbolSize != null) {
          vm.seriesIndex = allIndices;
        }
      }
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "opacity", label: "Opacity", type: "continuous", min: 0.1, max: 1, step: 0.05, defaultValue: 1 }
  ],
  pivot: makeCartesianPivot({
    transpose: [["x", "y"]],
    permute: [["x", "y", "color", "size"]],
    shift: ["color", "group", "column", "row"]
    // θ transitions declared centrally in core/chart-transitions.ts.
  }),
  postProcess: (option, ctx) => {
    if (!option.series || !Array.isArray(option.series)) return;
    const vmList = Array.isArray(option.visualMap) ? option.visualMap : option.visualMap ? [option.visualMap] : [];
    const visualMapControlsSize = vmList.some(
      (vm) => vm.type === "piecewise" && Array.isArray(vm.pieces) && vm.pieces.some((p) => p.symbolSize != null) || vm.type === "continuous" && vm.inRange?.symbolSize != null
    );
    if (visualMapControlsSize) return;
    const w = option._width || ctx.canvasSize.width;
    const h = option._height || ctx.canvasSize.height;
    const pointCount = ctx.table.length;
    const size = computeSymbolSize(w, h, pointCount);
    for (const series of option.series) {
      if (series.type !== "scatter") continue;
      const hasSizeEncoding = series.data?.length && Array.isArray(series.data[0]) && series.data[0].length >= 3;
      if (hasSizeEncoding) continue;
      if (series.symbolSize == null) {
        series.symbolSize = size;
      }
    }
  }
};
function linearRegression(data) {
  const n = data.length;
  if (n === 0) return { slope: 0, intercept: 0, xMin: 0, xMax: 0 };
  let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
  let xMin = data[0][0], xMax = data[0][0];
  for (const [x, y] of data) {
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumXX += x * x;
    if (x < xMin) xMin = x;
    if (x > xMax) xMax = x;
  }
  const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX) || 0;
  const intercept = (sumY - slope * sumX) / n;
  return { slope, intercept, xMin, xMax };
}
function polyRegression(data, order) {
  const n = data.length;
  if (n === 0) return { coeffs: [0], xMin: 0, xMax: 0 };
  let xMin = data[0][0];
  let xMax = data[0][0];
  for (const [x] of data) {
    if (x < xMin) xMin = x;
    if (x > xMax) xMax = x;
  }
  const k = order + 1;
  const xtx = Array.from({ length: k }, () => new Array(k).fill(0));
  const xty = new Array(k).fill(0);
  for (const [x, y] of data) {
    const xp = new Array(2 * order + 1);
    xp[0] = 1;
    for (let p = 1; p < xp.length; p++) {
      xp[p] = xp[p - 1] * x;
    }
    for (let i = 0; i < k; i++) {
      xty[i] += y * xp[i];
      for (let j = 0; j < k; j++) {
        xtx[i][j] += xp[i + j];
      }
    }
  }
  const aug = xtx.map((row, i) => [...row, xty[i]]);
  for (let col = 0; col < k; col++) {
    let maxRow = col;
    for (let row = col + 1; row < k; row++) {
      if (Math.abs(aug[row][col]) > Math.abs(aug[maxRow][col])) {
        maxRow = row;
      }
    }
    if (maxRow !== col) {
      [aug[col], aug[maxRow]] = [aug[maxRow], aug[col]];
    }
    const pivot = aug[col][col];
    if (Math.abs(pivot) < 1e-12) continue;
    for (let j = col; j <= k; j++) {
      aug[col][j] /= pivot;
    }
    for (let row = 0; row < k; row++) {
      if (row === col) continue;
      const factor = aug[row][col];
      for (let j = col; j <= k; j++) {
        aug[row][j] -= factor * aug[col][j];
      }
    }
  }
  const coeffs = aug.map((row) => row[k]);
  return { coeffs, xMin, xMax };
}
function polyEval(coeffs, x) {
  let result = 0, xp = 1;
  for (const c of coeffs) {
    result += c * xp;
    xp *= x;
  }
  return result;
}
function regressionCurvePoints(data, method, order, numPoints = 50) {
  if (data.length === 0) return [];
  if (method === "linear" || !method) {
    const reg2 = linearRegression(data);
    return [
      [reg2.xMin, reg2.slope * reg2.xMin + reg2.intercept],
      [reg2.xMax, reg2.slope * reg2.xMax + reg2.intercept]
    ];
  }
  if (method === "log") {
    const filtered = data.filter(([x]) => x > 0);
    if (filtered.length < 2) return [];
    const logData = filtered.map(([x, y]) => [Math.log(x), y]);
    const reg2 = linearRegression(logData);
    let xMin = Infinity, xMax = -Infinity;
    for (const [x] of filtered) {
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
    }
    const pts = [];
    for (let i = 0; i < numPoints; i++) {
      const x = xMin + (xMax - xMin) * i / (numPoints - 1);
      pts.push([x, reg2.intercept + reg2.slope * Math.log(x)]);
    }
    return pts;
  }
  if (method === "exp") {
    const filtered = data.filter(([, y]) => y > 0);
    if (filtered.length < 2) return [];
    const logData = filtered.map(([x, y]) => [x, Math.log(y)]);
    const reg2 = linearRegression(logData);
    let xMin = Infinity, xMax = -Infinity;
    for (const [x] of filtered) {
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
    }
    const pts = [];
    for (let i = 0; i < numPoints; i++) {
      const x = xMin + (xMax - xMin) * i / (numPoints - 1);
      pts.push([x, Math.exp(reg2.intercept + reg2.slope * x)]);
    }
    return pts;
  }
  if (method === "pow") {
    const filtered = data.filter(([x, y]) => x > 0 && y > 0);
    if (filtered.length < 2) return [];
    const logData = filtered.map(([x, y]) => [Math.log(x), Math.log(y)]);
    const reg2 = linearRegression(logData);
    let xMin = Infinity, xMax = -Infinity;
    for (const [x] of filtered) {
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
    }
    const pts = [];
    for (let i = 0; i < numPoints; i++) {
      const x = xMin + (xMax - xMin) * i / (numPoints - 1);
      pts.push([x, Math.exp(reg2.intercept) * Math.pow(x, reg2.slope)]);
    }
    return pts;
  }
  if (method === "quad") {
    const reg2 = polyRegression(data, 2);
    const pts = [];
    for (let i = 0; i < numPoints; i++) {
      const x = reg2.xMin + (reg2.xMax - reg2.xMin) * i / (numPoints - 1);
      pts.push([x, polyEval(reg2.coeffs, x)]);
    }
    return pts;
  }
  if (method === "poly") {
    const reg2 = polyRegression(data, order);
    const pts = [];
    for (let i = 0; i < numPoints; i++) {
      const x = reg2.xMin + (reg2.xMax - reg2.xMin) * i / (numPoints - 1);
      pts.push([x, polyEval(reg2.coeffs, x)]);
    }
    return pts;
  }
  const reg = linearRegression(data);
  return [
    [reg.xMin, reg.slope * reg.xMin + reg.intercept],
    [reg.xMax, reg.slope * reg.xMax + reg.intercept]
  ];
}
var ecRegressionDef = {
  chart: "Regression",
  template: { mark: "circle", encoding: {} },
  channels: ["x", "y", "size", "color", "column", "row"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xField = channelSemantics.x?.field;
    const yField = channelSemantics.y?.field;
    const colorField = channelSemantics.color?.field;
    if (!xField || !yField) return;
    const method = chartProperties?.regressionMethod ?? "linear";
    const polyOrder = chartProperties?.polyOrder ?? 3;
    const option = {
      tooltip: { trigger: "item" },
      xAxis: { type: "value", name: xField, nameLocation: "middle", nameGap: 30, axisTick: { show: true } },
      yAxis: { type: "value", name: yField, nameLocation: "middle", nameGap: 40, axisTick: { show: true } },
      series: []
    };
    if (channelSemantics.x?.zero) option.xAxis.scale = !channelSemantics.x.zero.zero;
    if (channelSemantics.y?.zero) option.yAxis.scale = !channelSemantics.y.zero.zero;
    const opacity = chartProperties?.opacity ?? 1;
    if (colorField) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      option._legendTitle = colorField;
      let colorIdx = 0;
      for (const [name, rows] of groups) {
        const data = rows.map((r) => [r[xField], r[yField]]);
        const lineData = regressionCurvePoints(data, method, polyOrder);
        option.series.push({
          name,
          type: "scatter",
          data,
          itemStyle: { color: DEFAULT_COLORS[colorIdx % DEFAULT_COLORS.length], opacity }
        });
        option.series.push({
          name: `${name} (trend)`,
          type: "line",
          data: lineData,
          showSymbol: false,
          smooth: method !== "linear",
          lineStyle: { color: DEFAULT_COLORS[colorIdx % DEFAULT_COLORS.length], width: 2 }
        });
        colorIdx++;
      }
    } else {
      const data = table.map((r) => [r[xField], r[yField]]);
      const lineData = regressionCurvePoints(data, method, polyOrder);
      option.series.push({ type: "scatter", data, itemStyle: { opacity } });
      option.series.push({
        name: "Trend",
        type: "line",
        data: lineData,
        showSymbol: false,
        smooth: method !== "linear",
        lineStyle: { color: "#ee6666", width: 2 }
      });
    }
    const xName = option.xAxis?.name ?? xField ?? "X";
    const yName = option.yAxis?.name ?? yField ?? "Y";
    const tooltipParts = [
      { from: "data", index: 0, label: xName, format: "number" },
      { from: "data", index: 1, label: yName, format: "number" }
    ];
    if (colorField) tooltipParts.push({ from: "series", label: colorField });
    option._encodingTooltip = { trigger: "item", parts: tooltipParts };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "regressionMethod",
      label: "Method",
      type: "discrete",
      options: [
        { value: "linear", label: "Linear" },
        { value: "log", label: "Logarithmic" },
        { value: "exp", label: "Exponential" },
        { value: "pow", label: "Power" },
        { value: "quad", label: "Quadratic" },
        { value: "poly", label: "Polynomial" }
      ],
      defaultValue: "linear"
    },
    {
      key: "polyOrder",
      label: "Poly Order",
      type: "continuous",
      min: 2,
      max: 10,
      step: 1,
      defaultValue: 3
    }
  ]
};

// src/echarts/templates/connected-scatter.ts
function sortByOrder(rows, field) {
  if (!field) return rows;
  const tagged = rows.map((row, idx) => ({ row, idx, key: row[field] }));
  const present = tagged.filter((t) => t.key != null && t.key !== "");
  const allNumeric = present.length > 0 && present.every((t) => typeof t.key === "number" || typeof t.key === "string" && t.key.trim() !== "" && !isNaN(Number(t.key)));
  const allDates = !allNumeric && present.length > 0 && present.every((t) => !isNaN(Date.parse(String(t.key))));
  const rank = (k) => {
    if (allNumeric) return Number(k);
    if (allDates) return Date.parse(String(k));
    return String(k);
  };
  return [...tagged].sort((a, b) => {
    const ra = rank(a.key);
    const rb = rank(b.key);
    if (ra < rb) return -1;
    if (ra > rb) return 1;
    return a.idx - b.idx;
  }).map((t) => t.row);
}
function toPoints(rows, xField, yField) {
  return rows.map((r) => {
    const x = r[xField];
    const y = r[yField];
    return [
      x != null && !isNaN(Number(x)) ? Number(x) : null,
      y != null && !isNaN(Number(y)) ? Number(y) : null
    ];
  });
}
var ecConnectedScatterDef = {
  chart: "Connected Scatter Plot",
  template: { mark: "line", encoding: {} },
  channels: ["x", "y", "order", "color", "detail", "column", "row"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const orderField = channelSemantics.order?.field;
    const groupField = channelSemantics.color?.field ?? channelSemantics.detail?.field;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const option = {
      tooltip: { trigger: "item" },
      xAxis: {
        type: "value",
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        axisTick: { show: true }
      },
      yAxis: {
        type: "value",
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true }
      },
      series: []
    };
    option.xAxis.scale = channelSemantics.x?.zero ? !channelSemantics.x.zero.zero : true;
    option.yAxis.scale = channelSemantics.y?.zero ? !channelSemantics.y.zero.zero : true;
    const baseSeriesOpt = {
      type: "line",
      showSymbol: true,
      symbol: "circle",
      symbolSize: 8,
      // Straight segments — never smooth, so a looping path crosses itself.
      smooth: false,
      lineStyle: { width: 2 },
      // Don't clip symbols at the grid edge: a point that lands exactly on
      // an axis bound would otherwise have its marker cut in half.
      clip: false
    };
    if (groupField) {
      const groups = groupBy(table, groupField);
      option.legend = { data: [...groups.keys()] };
      for (const [name, rows] of groups) {
        const sorted = sortByOrder(rows, orderField);
        option.series.push({
          name,
          ...baseSeriesOpt,
          data: toPoints(sorted, xField, yField)
          // Colors assigned by ecApplyLayoutToSpec from colorDecisions.
        });
      }
    } else {
      const sorted = sortByOrder(table, orderField);
      option.series.push({
        ...baseSeriesOpt,
        data: toPoints(sorted, xField, yField)
      });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/core/band-dodge.ts
var DEFAULT_NESTED_SNAP_THRESHOLD = 0.9;
function recommendMode(maxPerBand, globalCount, nestedFraction, threshold) {
  if (maxPerBand <= 1) return "none";
  if (nestedFraction >= threshold) return "none";
  if (maxPerBand >= globalCount) return "global";
  return "local";
}
function planBandDodge(table, axisField, subField, options) {
  const perBand = /* @__PURE__ */ new Map();
  const global = /* @__PURE__ */ new Set();
  for (const row of table) {
    global.add(row[subField]);
    const key = row[axisField];
    let bandSet = perBand.get(key);
    if (!bandSet) perBand.set(key, bandSet = /* @__PURE__ */ new Set());
    bandSet.add(row[subField]);
  }
  const globalCount = Math.max(1, global.size);
  const bandCount = perBand.size;
  let maxPerBand = 0;
  let singleValuedBands = 0;
  let completeBands = 0;
  for (const bandSet of perBand.values()) {
    if (bandSet.size > maxPerBand) maxPerBand = bandSet.size;
    if (bandSet.size <= 1) singleValuedBands++;
    if (bandSet.size === globalCount) completeBands++;
  }
  const threshold = options?.nestedSnapThreshold ?? DEFAULT_NESTED_SNAP_THRESHOLD;
  const nestedFraction = bandCount > 0 ? singleValuedBands / bandCount : 1;
  const mode = recommendMode(maxPerBand, globalCount, nestedFraction, threshold);
  return {
    mode,
    dodge: mode !== "none",
    laneCount: globalCount,
    ambiguous: maxPerBand > 1 && completeBands < bandCount,
    maxPerBand,
    global: globalCount,
    bandCount
  };
}
function laneCountForMode(plan, mode) {
  if (mode === "global") return plan.global;
  if (mode === "local") return Math.max(1, plan.maxPerBand);
  return 1;
}
function resolveDodge(plan, override) {
  let mode = override === "none" || override === "local" || override === "global" ? override : plan.mode;
  if (mode !== "none" && plan.maxPerBand <= 1) mode = "none";
  return { mode, laneCount: laneCountForMode(plan, mode) };
}

// src/core/axis-detection.ts
var isDiscrete3 = (type) => type === "nominal" || type === "ordinal";
var getFieldCardinality = (field, table) => new Set(table.map((row) => row[field]).filter((value) => value != null)).size;
function resolveDiscreteType(currentType, field, table) {
  if (currentType === "nominal") return "nominal";
  if (currentType === "ordinal") return "ordinal";
  if (currentType === "temporal") return "ordinal";
  if (currentType === "quantitative" && field && table.length > 0) {
    return getFieldCardinality(field, table) <= 20 ? "ordinal" : "nominal";
  }
  return "nominal";
}
function detectBandedAxisFromSemantics(channelSemantics, table, options = {}) {
  const xType = channelSemantics.x?.type;
  const yType = channelSemantics.y?.type;
  if (xType && isDiscrete3(xType)) return { axis: "x" };
  if (yType && isDiscrete3(yType)) return { axis: "y" };
  if (xType && yType) {
    if (xType === "quantitative" && yType !== "quantitative") {
      return { axis: "y" };
    }
    if (yType === "quantitative" && xType !== "quantitative") {
      return { axis: "x" };
    }
    return { axis: options.preferAxis || "x" };
  }
  if (xType) {
    const newType = resolveDiscreteType(xType, channelSemantics.x?.field, table);
    return { axis: "x", resolvedTypes: { x: newType } };
  }
  if (yType) {
    const newType = resolveDiscreteType(yType, channelSemantics.y?.field, table);
    return { axis: "y", resolvedTypes: { y: newType } };
  }
  return null;
}
function detectBandedAxisForceDiscrete(channelSemantics, table, options = {}) {
  const result = detectBandedAxisFromSemantics(channelSemantics, table, options);
  if (!result) return null;
  const axis = result.axis;
  const semantics = channelSemantics[axis];
  if (!semantics) return result;
  if (!isDiscrete3(semantics.type)) {
    const newType = resolveDiscreteType(semantics.type, semantics.field, table);
    return {
      axis,
      resolvedTypes: { ...result.resolvedTypes, [axis]: newType }
    };
  }
  return result;
}

// src/core/encoding-actions.ts
var isMeasureEnc = (e) => !!e?.field && (!!e.aggregate || e.type === "quantitative");
var isDiscreteCategoryEnc = (e) => !!e?.field && !e.aggregate && e.type !== "quantitative" && e.type !== "temporal";
function resolveSortChannels(encodings, candidates) {
  const category = candidates.find((c) => isDiscreteCategoryEnc(encodings[c]));
  const measure = candidates.find((c) => isMeasureEnc(encodings[c]));
  if (!category || !measure || category === measure) return null;
  return { category, measure };
}
function makeSortAction(options) {
  const candidates = ["x", "y"];
  return {
    key: "sort",
    label: "Sort",
    dependencies: candidates,
    isApplicable: (ctx) => resolveSortChannels(ctx.encodings, candidates) !== null,
    control: {
      type: "discrete",
      options: [
        { value: void 0, label: "Default" },
        { value: "value-desc", label: "Value \u2193" },
        { value: "value-asc", label: "Value \u2191" }
      ]
    },
    get: (encodings) => {
      const resolved = resolveSortChannels(encodings, candidates);
      if (!resolved) return void 0;
      const { category, measure } = resolved;
      const enc = encodings[category];
      if (enc.sortBy === measure) {
        return enc.sortOrder === "descending" ? "value-desc" : "value-asc";
      }
      return void 0;
    },
    set: (encodings, value) => {
      const resolved = resolveSortChannels(encodings, candidates);
      if (!resolved) return encodings;
      const { category, measure } = resolved;
      const base = encodings[category];
      let next;
      switch (value) {
        case "value-asc":
          next = { ...base, sortBy: measure, sortOrder: "ascending" };
          break;
        case "value-desc":
          next = { ...base, sortBy: measure, sortOrder: "descending" };
          break;
        default:
          next = { ...base, sortBy: void 0, sortOrder: void 0 };
      }
      return { ...encodings, [category]: next };
    }
  };
}

// src/echarts/templates/bar.ts
var isDiscrete4 = (type) => type === "nominal" || type === "ordinal";
function buildLocalLaneSeries(table, categories, catField, groupField, valField, groupColor) {
  const globalGroups = [...new Set(table.map((r) => String(r[groupField] ?? "")))].filter(Boolean);
  const perBand = /* @__PURE__ */ new Map();
  for (const cat of categories) perBand.set(cat, []);
  for (const r of table) {
    const cat = String(r[catField] ?? "");
    const g = String(r[groupField] ?? "");
    if (!perBand.has(cat) || !g) continue;
    const arr = perBand.get(cat);
    if (!arr.includes(g)) arr.push(g);
  }
  for (const arr of perBand.values()) arr.sort();
  const maxPerBand = Math.max(1, ...[...perBand.values()].map((a) => a.length));
  if (maxPerBand <= 1) return null;
  const valAt = /* @__PURE__ */ new Map();
  for (const r of table) {
    const v = Number(r[valField]);
    if (isFinite(v)) valAt.set(`${r[catField]}\0${r[groupField]}`, v);
  }
  const series = Array.from({ length: maxPerBand }, (_, lane) => ({
    type: "bar",
    name: `__lane${lane}`,
    data: categories.map((cat) => {
      const g = perBand.get(cat)?.[lane];
      if (g === void 0) return "-";
      const v = valAt.get(`${cat}\0${g}`);
      return v === void 0 ? "-" : { value: v, itemStyle: { color: groupColor(g) } };
    })
  }));
  const legendData = globalGroups.map((g) => ({ name: g, itemStyle: { color: groupColor(g) } }));
  return { series, legendData };
}
function buildCategoryValues(rows, categoryField, valueField, categories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const cat = String(row[categoryField] ?? "");
    const val = row[valueField];
    if (val != null && !isNaN(val)) {
      map.set(cat, (map.get(cat) ?? 0) + Number(val));
    }
  }
  return categories.map((cat) => map.get(cat) ?? null);
}
function buildCategoryCounts(rows, categoryField, categories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const cat = String(row[categoryField] ?? "");
    map.set(cat, (map.get(cat) ?? 0) + 1);
  }
  return categories.map((cat) => map.get(cat) ?? 0);
}
function buildCategoryGroupCounts(rows, categoryField, groupField, categories, groups) {
  return groups.map(
    (group) => categories.map(
      (cat) => rows.filter((r) => String(r[categoryField] ?? "") === cat && String(r[groupField] ?? "") === group).length
    )
  );
}
function areHeatmapCategoriesNumeric(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var EC_BAR_SHORT_CATEGORY_COUNT = 4;
var EC_BAR_SHORT_CATEGORY_LABEL_LEN = 8;
function categoryAxisLabelRotateDeg(categories, channelType) {
  if (channelType === "quantitative") return 0;
  const labels = categories.map((c) => String(c));
  if (labels.length === 0) return 0;
  const maxLen = Math.max(...labels.map((s) => s.length));
  if (labels.length <= EC_BAR_SHORT_CATEGORY_COUNT && maxLen <= EC_BAR_SHORT_CATEGORY_LABEL_LEN) {
    return 0;
  }
  return 90;
}
var ecBarChartDef = {
  chart: "Bar Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "color", "opacity", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: (cs, table) => {
    const result = detectBandedAxisFromSemantics(cs, table, { preferAxis: "x" });
    return {
      axisFlags: result ? { [result.axis]: { banded: true } } : { x: { banded: true } },
      resolvedTypes: result?.resolvedTypes
    };
  },
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const { categoryAxis, valueAxis } = detectAxes(channelSemantics);
    const catField = channelSemantics[categoryAxis]?.field;
    const valField = channelSemantics[valueAxis]?.field;
    if (!catField || !valField) return;
    const catCS = channelSemantics[categoryAxis];
    const valCS = channelSemantics[valueAxis];
    const colorField = channelSemantics.color?.field;
    const bothDiscrete = isDiscrete4(channelSemantics.x?.type) && isDiscrete4(channelSemantics.y?.type);
    if (bothDiscrete) {
      const categories2 = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
      const groups = extractCategories(table, valField, getCategoryOrder(ctx, valueAxis));
      const countMatrix = buildCategoryGroupCounts(table, catField, valField, categories2, groups);
      const heatData = [];
      let minVal = Infinity;
      let maxVal = -Infinity;
      for (let yi = 0; yi < groups.length; yi++) {
        for (let xi = 0; xi < categories2.length; xi++) {
          const v = countMatrix[yi][xi];
          heatData.push([xi, yi, v]);
          if (v < minVal) minVal = v;
          if (v > maxVal) maxVal = v;
        }
      }
      if (minVal === Infinity) minVal = 0;
      if (maxVal === -Infinity) maxVal = 1;
      const option2 = {
        tooltip: { position: "top" },
        _encodingTooltip: {
          trigger: "item",
          parts: [
            { from: "data", index: 0, label: catField, format: "category", categoryNames: categories2 },
            { from: "data", index: 1, label: valField, format: "category", categoryNames: groups },
            { from: "data", index: 2, label: "Count", format: "number" }
          ]
        },
        xAxis: {
          type: "category",
          data: categories2,
          name: catField,
          splitArea: { show: true },
          axisTick: { show: true, alignWithLabel: true },
          axisLabel: {
            rotate: areHeatmapCategoriesNumeric(categories2) ? 0 : categoryAxisLabelRotateDeg(categories2, catCS?.type)
          }
        },
        yAxis: {
          type: "category",
          data: groups,
          name: valField,
          splitArea: { show: true },
          axisTick: { show: true, alignWithLabel: true },
          axisLabel: { rotate: 0 }
        },
        visualMap: {
          min: minVal,
          max: maxVal,
          calculable: true,
          orient: "vertical",
          right: 10,
          top: "center",
          itemGap: 15,
          inRange: { color: ["#f0f9ff", "#0ea5e9", "#0369a1"] }
        },
        _visualMapWidth: 50,
        series: [{
          type: "heatmap",
          data: heatData,
          label: { show: heatData.length <= 100 },
          emphasis: {
            itemStyle: { shadowBlur: 10, shadowColor: "rgba(0, 0, 0, 0.5)" }
          }
        }]
      };
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    if (colorField && valCS?.type === "quantitative") {
      const categories2 = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
      const isHorizontal2 = categoryAxis === "y";
      const option2 = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: isHorizontal2 ? { type: "value", name: valField } : {
          type: "category",
          data: categories2,
          name: catField,
          axisLabel: { rotate: categoryAxisLabelRotateDeg(categories2, catCS?.type) },
          axisTick: { show: true, alignWithLabel: true },
          axisLine: { show: true }
        },
        yAxis: isHorizontal2 ? { type: "category", data: categories2, name: catField } : { type: "value", name: valField },
        series: []
      };
      option2._encodingTooltip = { trigger: "axis", categoryLabel: catField, valueLabel: valField };
      const groups = groupBy(table, colorField);
      const legendKeys = [...groups.keys()];
      const highCardinality = legendKeys.length > 10;
      option2.legend = {
        data: legendKeys,
        orient: "vertical",
        right: 10,
        top: highCardinality ? 30 : 20,
        bottom: highCardinality ? 10 : void 0,
        type: highCardinality ? "scroll" : "plain",
        align: "left"
      };
      if (colorField) {
        const titleGraphic = {
          type: "text",
          right: 10,
          top: 4,
          z: 100,
          style: {
            text: colorField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        };
        const existingGraphic = spec.graphic ?? option2.graphic;
        option2.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, titleGraphic] : existingGraphic ? [existingGraphic, titleGraphic] : [titleGraphic];
      }
      for (const [name, rows] of groups) {
        const data = buildCategoryValues(rows, catField, valField, categories2);
        option2.series.push({
          name,
          type: "bar",
          data,
          stack: "total"
          // 颜色由 ecApplyLayoutToSpec 中的 palette 决定，这里不再硬编码。
        });
      }
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    if (categoryAxis === "y" && valCS?.type === "temporal") {
      const dateCategories = extractCategories(table, valField, getCategoryOrder(ctx, valueAxis));
      dateCategories.sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
      const groups = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
      const countMatrix = buildCategoryGroupCounts(table, valField, catField, dateCategories, groups);
      const virtualDecision = {
        schemeType: "categorical",
        // 这里没有真实的 encoding.color，但我们知道会画按 group 分类的条形，
        // 因此用 group 数作为 categoryCount，方便 colormap 选择 cat10/cat20。
        categoryCount: groups.length || void 0};
      const palette = pickEChartsPalette(virtualDecision);
      const option2 = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        legend: { data: groups },
        xAxis: {
          type: "category",
          data: dateCategories,
          name: valField,
          axisLabel: { rotate: categoryAxisLabelRotateDeg(dateCategories, "temporal") },
          axisTick: { show: true, alignWithLabel: true },
          axisLine: { show: true }
        },
        yAxis: { type: "value", name: "Count", axisTick: { show: true } },
        // 显式把 palette 写到 option.color，方便和其它图类型保持一致
        color: palette,
        series: groups.map((name, i) => ({
          name,
          type: "bar",
          data: countMatrix[i],
          itemStyle: {
            color: palette[i % palette.length],
            borderRadius: chartProperties?.cornerRadius ?? 0
          }
        }))
      };
      option2._encodingTooltip = { trigger: "axis", categoryLabel: valField, valueLabel: "Count", groupLabel: catField };
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    let categories = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
    let values;
    if (valCS?.type === "temporal") {
      values = buildCategoryCounts(table, catField, categories);
    } else {
      values = buildCategoryValues(table, catField, valField, categories);
    }
    if (catCS?.type === "temporal") {
      const pairs = categories.map((c, i) => [c, values[i]]);
      pairs.sort((a, b) => new Date(a[0]).getTime() - new Date(b[0]).getTime());
      categories = pairs.map((p) => p[0]);
      values = pairs.map((p) => p[1]);
    }
    const isHorizontal = categoryAxis === "y";
    const valueLabel = valCS?.type === "temporal" ? "Count" : valField;
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: isHorizontal ? { type: "value", name: valueLabel } : {
        type: "category",
        data: categories,
        name: catField,
        axisLabel: { rotate: categoryAxisLabelRotateDeg(categories, catCS?.type) },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      },
      yAxis: isHorizontal ? { type: "category", data: categories, name: catField } : { type: "value", name: valueLabel },
      series: [{
        type: "bar",
        data: values,
        itemStyle: {
          borderRadius: chartProperties?.cornerRadius ?? 0
        }
      }]
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: catField, valueLabel };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "cornerRadius", label: "Corners", type: "continuous", min: 0, max: 15, step: 1, defaultValue: 0 }
  ],
  encodingActions: [makeSortAction()],
  pivot: makeCartesianPivot({
    transpose: [["x", "y"]],
    permute: [["x", "y", "color"]],
    shift: ["color", "column", "row"]
  })
};
var ecStackedBarChartDef = {
  chart: "Stacked Bar Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: (cs, table) => {
    const result = detectBandedAxisFromSemantics(cs, table, { preferAxis: "x" });
    return {
      axisFlags: result ? { [result.axis]: { banded: true } } : { x: { banded: true } },
      resolvedTypes: result?.resolvedTypes,
      paramOverrides: { continuousMarkCrossSection: { x: 20, y: 20, seriesCountAxis: "auto" } }
    };
  },
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const { categoryAxis, valueAxis } = detectAxes(channelSemantics);
    const colorField = channelSemantics.color?.field;
    const catField = channelSemantics[categoryAxis]?.field;
    const valField = channelSemantics[valueAxis]?.field;
    if (!catField || !valField) return;
    const catCS = channelSemantics[categoryAxis];
    const valCS = channelSemantics[valueAxis];
    let categories = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
    if (catCS?.type === "temporal") {
      categories = [...categories].sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
    }
    const isHorizontal = categoryAxis === "y";
    const valueLabel = valCS?.type === "temporal" ? "Count" : valField;
    if (colorField && isDiscrete4(channelSemantics.x?.type) && isDiscrete4(channelSemantics.y?.type)) {
      const categoriesX = extractCategories(table, channelSemantics.x.field, getCategoryOrder(ctx, "x"));
      const groups = groupBy(table, colorField);
      const option2 = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: {
          type: "category",
          data: categoriesX,
          name: channelSemantics.x.field,
          axisLabel: {
            rotate: categoryAxisLabelRotateDeg(categoriesX, channelSemantics.x?.type)
          },
          axisTick: { show: true, alignWithLabel: true },
          axisLine: { show: true }
        },
        yAxis: { type: "value", name: "Count", axisTick: { show: true } },
        series: []
      };
      option2._encodingTooltip = {
        trigger: "axis",
        categoryLabel: channelSemantics.x.field,
        valueLabel: "Count",
        groupLabel: colorField
      };
      const legendKeys = [...groups.keys()];
      const highCardinality = legendKeys.length > 10;
      option2.legend = {
        data: legendKeys,
        orient: "vertical",
        right: 10,
        top: highCardinality ? 30 : 20,
        bottom: highCardinality ? 10 : void 0,
        type: highCardinality ? "scroll" : "plain",
        align: "left"
      };
      const titleGraphic = {
        type: "text",
        right: 10,
        top: 4,
        z: 100,
        style: {
          text: colorField,
          fontSize: 11,
          fontWeight: "bold",
          fill: "#333",
          textAlign: "right"
        }
      };
      const existingGraphic = spec.graphic ?? option2.graphic;
      option2.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, titleGraphic] : existingGraphic ? [existingGraphic, titleGraphic] : [titleGraphic];
      for (const [name, rows] of groups) {
        const data = buildCategoryCounts(rows, channelSemantics.x.field, categoriesX);
        option2.series.push({
          name,
          type: "bar",
          data,
          stack: "total"
          // 颜色由全局 palette 决定。
        });
      }
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: isHorizontal ? { type: "value", name: valueLabel } : {
        type: "category",
        data: categories,
        name: catField,
        axisLabel: { rotate: categoryAxisLabelRotateDeg(categories, catCS?.type) },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      },
      yAxis: isHorizontal ? { type: "category", data: categories, name: catField } : { type: "value", name: valueLabel },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: catField, valueLabel };
    const stackMode = colorField ? chartProperties?.stackMode : void 0;
    const stackGroup = colorField && stackMode !== "layered" ? "total" : void 0;
    if (colorField) {
      const groups = groupBy(table, colorField);
      const legendKeys = [...groups.keys()];
      const highCardinality = legendKeys.length > 10;
      option.legend = {
        data: legendKeys,
        orient: "vertical",
        right: 10,
        top: highCardinality ? 30 : 20,
        bottom: highCardinality ? 10 : void 0,
        type: highCardinality ? "scroll" : "plain",
        align: "left"
      };
      const titleField = colorField;
      if (titleField) {
        const titleGraphic = {
          type: "text",
          right: 10,
          top: 4,
          z: 100,
          style: {
            text: titleField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        };
        const existingGraphic = spec.graphic ?? option.graphic;
        option.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, titleGraphic] : existingGraphic ? [existingGraphic, titleGraphic] : [titleGraphic];
      }
      for (const [name, rows] of groups) {
        const data = valCS?.type === "temporal" ? buildCategoryCounts(rows, catField, categories) : buildCategoryValues(rows, catField, valField, categories);
        const series = {
          name,
          type: "bar",
          data
          // 颜色由全局 palette 决定。
        };
        if (stackGroup) {
          series.stack = stackGroup;
        }
        if (stackMode === "normalize") {
          series.stack = "total";
        }
        option.series.push(series);
      }
    } else {
      const data = valCS?.type === "temporal" ? buildCategoryCounts(table, catField, categories) : buildCategoryValues(table, catField, valField, categories);
      option.series.push({ type: "bar", data });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "stackMode",
      label: "Stack",
      type: "discrete",
      options: [
        { value: void 0, label: "Stacked (default)" },
        { value: "normalize", label: "Normalize (100%)" }
      ],
      check: (ctx) => ({ applicable: !!ctx.encodings.color?.field })
    }
  ],
  encodingActions: [makeSortAction()],
  pivot: makeCartesianPivot({
    transpose: [["x", "y"]],
    permute: [["x", "y", "color"]],
    shift: ["color", "group", "column", "row"]
    // θ (→ Grouped Bar) declared centrally in core/chart-transitions.ts.
  })
};
var ecGroupedBarChartDef = {
  chart: "Grouped Bar Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "group", "color", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: (cs, table, chartProperties) => {
    const result = detectBandedAxisForceDiscrete(cs, table, { preferAxis: "x" });
    const axis = result?.axis || "x";
    const decl = {
      axisFlags: { [axis]: { banded: true } },
      resolvedTypes: result?.resolvedTypes
    };
    const groupField = cs.group?.field || cs.color?.field;
    const axisField = cs[axis]?.field;
    if (groupField && axisField) {
      const plan = planBandDodge(table, axisField, groupField);
      const { mode } = resolveDodge(plan, chartProperties?.dodge);
      if (mode === "local") decl.groupLaneCount = Math.max(1, plan.maxPerBand);
    }
    return decl;
  },
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const groupField = channelSemantics.group?.field || channelSemantics.color?.field;
    if (channelSemantics.x?.type === "temporal" && isDiscrete4(channelSemantics.y?.type) && groupField && channelSemantics.x.field) {
      const xField = channelSemantics.x.field;
      const xCS = channelSemantics.x;
      const dateCategories = extractCategories(table, xField, getCategoryOrder(ctx, "x"));
      dateCategories.sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
      const segments = extractCategories(table, groupField, getCategoryOrder(ctx, "group"));
      const countMatrix = buildCategoryGroupCounts(table, xField, groupField, dateCategories, segments);
      const option2 = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        legend: { data: segments },
        xAxis: {
          type: "category",
          data: dateCategories,
          name: xField,
          axisLabel: { rotate: categoryAxisLabelRotateDeg(dateCategories, xCS?.type) },
          axisTick: { show: true, alignWithLabel: true },
          axisLine: { show: true }
        },
        yAxis: { type: "value", name: "Count", axisTick: { show: true } },
        series: segments.map((name, i) => ({
          name,
          type: "bar",
          data: countMatrix[i]
          // 颜色由全局 palette 决定。
        }))
      };
      option2._legendTitle = groupField;
      option2._encodingTooltip = {
        trigger: "axis",
        categoryLabel: xField,
        valueLabel: "Count",
        groupLabel: groupField
      };
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    const { categoryAxis, valueAxis } = detectAxes(channelSemantics);
    const catField = channelSemantics[categoryAxis]?.field;
    const valField = channelSemantics[valueAxis]?.field;
    const valType = channelSemantics[valueAxis]?.type;
    if ((!valField || valType === "nominal" || valType === "ordinal") && groupField && channelSemantics.x?.field) {
      const xField = channelSemantics.x.field;
      const xCS = channelSemantics.x;
      let categories2 = extractCategories(table, xField, getCategoryOrder(ctx, "x"));
      if (xCS?.type === "temporal") {
        categories2 = [...categories2].sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
      }
      const groups = groupBy(table, groupField);
      const legendKeys = [...groups.keys()];
      const highCardinality = legendKeys.length > 10;
      const option2 = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: {
          type: "category",
          data: categories2,
          name: xField,
          axisLabel: { rotate: categoryAxisLabelRotateDeg(categories2, xCS?.type) },
          axisTick: { show: true, alignWithLabel: true },
          axisLine: { show: true }
        },
        yAxis: { type: "value", name: "Count", axisTick: { show: true } },
        series: []
      };
      option2._encodingTooltip = {
        trigger: "axis",
        categoryLabel: xField,
        valueLabel: "Count",
        groupLabel: groupField
      };
      option2.legend = {
        data: legendKeys,
        orient: "vertical",
        right: 10,
        top: highCardinality ? 30 : 20,
        bottom: highCardinality ? 10 : void 0,
        type: highCardinality ? "scroll" : "plain",
        align: "left"
      };
      const titleGraphic = {
        type: "text",
        right: 10,
        top: 4,
        z: 100,
        style: {
          text: groupField,
          fontSize: 11,
          fontWeight: "bold",
          fill: "#333",
          textAlign: "right"
        }
      };
      const existingGraphic = spec.graphic ?? option2.graphic;
      option2.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, titleGraphic] : existingGraphic ? [existingGraphic, titleGraphic] : [titleGraphic];
      for (const [name, rows] of groups) {
        const data = buildCategoryCounts(rows, xField, categories2);
        option2.series.push({
          name,
          type: "bar",
          data
          // 颜色由全局 palette 决定。
        });
      }
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    if (!catField || !valField) return;
    const catCS = channelSemantics[categoryAxis];
    let categories = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis));
    if (catCS?.type === "temporal") {
      categories = [...categories].sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
    }
    const isHorizontal = categoryAxis === "y";
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: isHorizontal ? { type: "value", name: valField } : {
        type: "category",
        data: categories,
        name: catField,
        axisLabel: { rotate: categoryAxisLabelRotateDeg(categories, catCS?.type) },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      },
      yAxis: isHorizontal ? { type: "category", data: categories, name: catField } : { type: "value", name: valField },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: catField, valueLabel: valField };
    if (groupField) {
      const groups = groupBy(table, groupField);
      const legendKeys = [...groups.keys()];
      const highCardinality = legendKeys.length > 10;
      option.legend = {
        data: legendKeys,
        orient: "vertical",
        right: 10,
        top: highCardinality ? 30 : 20,
        bottom: highCardinality ? 10 : void 0,
        type: highCardinality ? "scroll" : "plain",
        align: "left"
      };
      const titleField = groupField;
      if (titleField) {
        const titleGraphic = {
          type: "text",
          right: 10,
          top: 4,
          z: 100,
          style: {
            text: titleField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        };
        const existingGraphic = spec.graphic ?? option.graphic;
        option.graphic = Array.isArray(existingGraphic) ? [...existingGraphic, titleGraphic] : existingGraphic ? [existingGraphic, titleGraphic] : [titleGraphic];
      }
      for (const [name, rows] of groups) {
        const data = buildCategoryValues(rows, catField, valField, categories);
        option.series.push({
          name,
          type: "bar",
          data
          // 颜色由全局 palette 决定。
        });
      }
      const gAxisField = channelSemantics[categoryAxis]?.field;
      if (gAxisField) {
        const plan = planBandDodge(ctx.fullTable ?? table, gAxisField, groupField);
        const { mode } = resolveDodge(plan, ctx.chartProperties?.dodge);
        if (mode === "local") {
          const palette = pickEChartsPalette(ctx.colorDecisions?.group ?? ctx.colorDecisions?.color);
          const colorFor = (g) => palette[legendKeys.indexOf(g) % palette.length] ?? palette[0];
          const built = buildLocalLaneSeries(table, categories, catField, groupField, valField, colorFor);
          if (built) {
            option.series = built.series;
            option.legend.data = built.legendData;
          }
        }
      }
    } else {
      const data = buildCategoryValues(table, catField, valField, categories);
      option.series.push({ type: "bar", data });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "dodge",
      label: "Dodge",
      type: "discrete",
      options: [
        { value: "auto", label: "Auto" },
        { value: "local", label: "Local (compact)" },
        { value: "global", label: "Global (aligned)" }
      ],
      defaultValue: "auto",
      check: (ctx) => {
        const groupField = ctx.channelSemantics?.group?.field ?? ctx.encodings?.group?.field;
        const axisField = isDiscrete4(ctx.channelSemantics?.x?.type) ? ctx.channelSemantics?.x?.field : ctx.channelSemantics?.y?.field;
        const rows = ctx.data;
        if (!groupField || !axisField || !rows) return { applicable: false };
        const plan = planBandDodge(rows, axisField, groupField);
        return { applicable: plan.ambiguous, recommendedValue: plan.mode === "none" ? "auto" : plan.mode };
      }
    }
  ],
  encodingActions: [makeSortAction()],
  pivot: makeCartesianPivot({
    transpose: [["x", "y"]],
    permute: [["x", "y", "color"]],
    shift: ["color", "group", "column", "row"]
    // θ (→ Stacked Bar) declared centrally in core/chart-transitions.ts.
  })
};

// src/echarts/templates/line.ts
var isDiscrete5 = (type) => type === "nominal" || type === "ordinal";
function areCategoriesNumeric(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var ecLineChartDef = {
  chart: "Line Chart",
  template: { mark: "line", encoding: {} },
  channels: ["x", "y", "color", "opacity", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({
    paramOverrides: { continuousMarkCrossSection: { x: 100, y: 20, seriesCountAxis: "auto" } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorField = channelSemantics.color?.field;
    const colorType = channelSemantics.color?.type;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const xIsDiscrete = isDiscrete5(xCS.type);
    const xIsTemporal = xCS.type === "temporal";
    const yIsDiscrete = isDiscrete5(yCS.type);
    const isContinuousColor = !!colorField && (colorType === "quantitative" || colorType === "temporal");
    const categories = xIsDiscrete ? extractCategories(table, xField, getCategoryOrder(ctx, "x")) : void 0;
    const yCategories = yIsDiscrete ? extractCategories(table, yField, getCategoryOrder(ctx, "y")) : void 0;
    const option = {
      tooltip: {
        trigger: "axis"
      },
      xAxis: (() => {
        const type = xIsDiscrete ? "category" : xIsTemporal ? "time" : "value";
        const base = {
          type,
          name: xField,
          nameLocation: "middle",
          nameGap: 30,
          ...categories ? { data: categories } : {}
        };
        if (xIsDiscrete && categories) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: areCategoriesNumeric(categories) ? 0 : 90 };
        } else if (xIsTemporal) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: 90 };
        } else {
          base.axisTick = { show: true };
        }
        return base;
      })(),
      yAxis: yIsDiscrete && yCategories ? {
        type: "category",
        data: yCategories,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      } : {
        type: "value",
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    option._encodingTooltip = isContinuousColor ? {
      trigger: "item",
      parts: [
        { from: "data", index: 0, label: xField, format: "number" },
        { from: "data", index: 1, label: yField, format: "number" },
        { from: "data", index: 2, label: colorField, format: "number" }
      ]
    } : { trigger: "axis", categoryLabel: xField, valueLabel: yField };
    if (channelSemantics.y?.zero) {
      option.yAxis.scale = !channelSemantics.y.zero.zero;
    }
    const interpolate = chartProperties?.interpolate;
    const showPoints = !!chartProperties?.showPoints;
    const smooth = interpolate === "monotone" || interpolate === "basis" || interpolate === "cardinal" || interpolate === "catmull-rom";
    const step = interpolate === "step" ? "middle" : interpolate === "step-before" ? "start" : interpolate === "step-after" ? "end" : void 0;
    if (isContinuousColor && colorField) {
      const sorted = [...table].sort((a, b) => {
        const ax = a[xField];
        const bx = b[xField];
        if (xIsTemporal) return new Date(ax).getTime() - new Date(bx).getTime();
        const na = Number(ax);
        const nb = Number(bx);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        return String(ax).localeCompare(String(bx));
      });
      const pointData = sorted.map((r) => [r[xField], r[yField], r[colorField]]);
      const lineData = sorted.map((r) => [r[xField], r[yField]]);
      const nums = sorted.map((r) => Number(r[colorField])).filter((v) => !isNaN(v) && isFinite(v));
      const cMin = nums.length ? Math.min(...nums) : 0;
      const cMax = nums.length ? Math.max(...nums) : 1;
      const decisionSchemeId = colorDecisions?.color?.schemeId;
      const paletteFromDecision = decisionSchemeId ? getPaletteForScheme(decisionSchemeId) : void 0;
      option.visualMap = {
        type: "continuous",
        min: cMin,
        max: cMax,
        dimension: 2,
        // [x, y, color]
        orient: "vertical",
        right: 10,
        top: "center",
        // 优先使用 colordecisions palette，找不到时退回原来的绿色色带。
        inRange: {
          color: paletteFromDecision && paletteFromDecision.length > 0 ? paletteFromDecision : ["#f7fcf5", "#74c476", "#00441b"]
        },
        seriesIndex: 1,
        // apply to point series
        name: colorField,
        textStyle: { fontSize: 10 },
        calculable: true
      };
      option._visualMapWidth = 70;
      option.graphic = [
        ...Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [],
        {
          type: "text",
          right: 10,
          top: 4,
          z: 100,
          style: {
            text: colorField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        }
      ];
      option.series.push({
        type: "line",
        data: lineData,
        itemStyle: { color: "#cccccc" },
        lineStyle: { color: "#cccccc" },
        showSymbol: false,
        symbol: "none",
        ...smooth ? { smooth: true } : {},
        ...step ? { step } : {}
      });
      option.series.push({
        type: "scatter",
        data: pointData,
        symbol: "circle",
        symbolSize: 7,
        itemStyle: { opacity: 1 }
      });
    } else if (colorField && isDiscrete5(colorType)) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      for (const [name, rows] of groups) {
        const seriesData = yIsDiscrete && yCategories ? buildCategoryAlignedXYData(rows, xField, yField, yCategories) : xIsDiscrete ? buildCategoryAlignedData(rows, xField, yField, categories) : rows.map((r) => [r[xField], r[yField]]);
        const series = {
          name,
          type: "line",
          data: seriesData,
          // Default line chart: don't draw point markers (unless showPoints is set).
          showSymbol: !!showPoints,
          symbol: showPoints ? "circle" : "none",
          ...showPoints ? { symbolSize: 6 } : {}
        };
        if (smooth) series.smooth = true;
        if (step) series.step = step;
        option.series.push(series);
      }
    } else {
      const seriesData = yIsDiscrete && yCategories ? buildCategoryAlignedXYData(table, xField, yField, yCategories) : xIsDiscrete ? categories.map((cat) => {
        const row = table.find((r) => String(r[xField]) === cat);
        return row ? row[yField] : null;
      }) : table.map((r) => [r[xField], r[yField]]);
      const series = {
        type: "line",
        data: seriesData,
        // Default line chart: don't draw point markers (unless showPoints is set).
        showSymbol: !!showPoints,
        symbol: showPoints ? "circle" : "none",
        ...showPoints ? { symbolSize: 6 } : {}
      };
      if (smooth) series.smooth = true;
      if (step) series.step = step;
      option.series.push(series);
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "interpolate",
      label: "Curve",
      type: "discrete",
      options: [
        { value: void 0, label: "Default (linear)" },
        { value: "linear", label: "Linear" },
        { value: "monotone", label: "Monotone (smooth)" },
        { value: "step", label: "Step" },
        { value: "step-before", label: "Step Before" },
        { value: "step-after", label: "Step After" },
        { value: "basis", label: "Basis (smooth)" },
        { value: "cardinal", label: "Cardinal" },
        { value: "catmull-rom", label: "Catmull-Rom" }
      ]
    },
    { key: "showPoints", label: "Points", type: "binary", defaultValue: false }
  ],
  pivot: makeCartesianPivot({
    permute: [["y", "color"]],
    shift: ["color", "group", "column", "row"]
  })
};
function buildCategoryAlignedData(rows, xField, yField, categories, yTransform) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) map.set(String(row[xField]), Number(v));
  }
  return categories.map((cat) => {
    const v = map.get(cat);
    return v != null ? yTransform ? yTransform(v) : v : null;
  });
}
function buildCategoryAlignedXYData(rows, xField, yField, yCategories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const key = String(row[yField] ?? "");
    if (!map.has(key)) {
      map.set(key, row[xField]);
    }
  }
  return yCategories.filter((cat) => map.has(cat)).map((cat) => [map.get(cat), cat]);
}
var RANK_SEMANTIC_TYPES = /* @__PURE__ */ new Set(["Rank", "Score", "Level"]);
var ecBumpChartDef = {
  chart: "Bump Chart",
  template: { mark: "line", encoding: {} },
  channels: ["x", "y", "color", "detail", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({
    paramOverrides: { continuousMarkCrossSection: { x: 80, y: 20, seriesCountAxis: "auto" } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, semanticTypes } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorField = channelSemantics.color?.field;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const ySemType = toTypeString(semanticTypes?.[yField]);
    const xSemType = toTypeString(semanticTypes?.[xField]);
    const yIsRank = RANK_SEMANTIC_TYPES.has(ySemType);
    const xIsRank = RANK_SEMANTIC_TYPES.has(xSemType);
    const rankOnY = yIsRank && !xIsRank;
    const xIsDiscrete = isDiscrete5(xCS.type);
    const xIsTemporal = xCS.type === "temporal";
    const categories = xIsDiscrete ? extractCategories(table, xField, getCategoryOrder(ctx, "x")) : void 0;
    const rankValues = table.map((r) => Number(r[yField])).filter((v) => !isNaN(v) && isFinite(v));
    const maxRank = rankValues.length ? Math.max(...rankValues) : 1;
    const rankCategories = Array.from({ length: maxRank }, (_, i) => String(i + 1));
    const rankToIndex = (rank) => Math.max(0, Math.min(maxRank - 1, Math.round(rank) - 1));
    const toXValue = (v) => {
      if (v == null) return NaN;
      if (xIsTemporal) return typeof v === "number" ? v : new Date(String(v)).getTime();
      const n = Number(v);
      return isNaN(n) ? String(v) : n;
    };
    const sortRowsByX = (rows) => [...rows].sort((a, b) => {
      const ax = toXValue(a[xField]);
      const bx = toXValue(b[xField]);
      if (typeof ax === "number" && typeof bx === "number") return ax - bx;
      return String(ax).localeCompare(String(bx));
    });
    const option = {
      tooltip: { trigger: "axis" },
      xAxis: (() => {
        const type = xIsDiscrete ? "category" : xIsTemporal ? "time" : "value";
        const base = {
          type,
          name: xField,
          nameLocation: "middle",
          nameGap: 30,
          axisLine: { show: true },
          ...categories ? { data: categories } : {}
        };
        if (xIsDiscrete && categories) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: areCategoriesNumeric(categories) ? 0 : 90 };
        } else if (xIsTemporal) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: 90 };
        } else {
          base.axisTick = { show: true };
        }
        return base;
      })(),
      yAxis: rankOnY ? {
        type: "category",
        data: rankCategories,
        inverse: true,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisLabel: { rotate: 0 },
        axisTick: { show: true, alignWithLabel: true }
      } : {
        type: "value",
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    if (rankOnY) {
      option.tooltip = {
        trigger: "axis",
        formatter: (params) => {
          const list = Array.isArray(params) ? params : [params];
          if (list.length === 0) return "";
          const p = list[0];
          const cat = p.axisValue ?? p.name ?? "";
          let html = `<b>${cat}</b><br/>`;
          list.forEach((item) => {
            const idx = item.value != null ? Number(item.value) : null;
            const displayRank = idx != null && Number.isInteger(idx) ? String(idx + 1) : "\u2013";
            html += `${item.marker} ${item.seriesName}: ${displayRank}<br/>`;
          });
          return html;
        }
      };
    } else {
      option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: yField };
    }
    const baseSeriesOpt = { showSymbol: true, symbolSize: 6, smooth: true };
    if (colorField) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      for (const [name, rows] of groups) {
        const orderedRows = xIsDiscrete ? rows : sortRowsByX(rows);
        const seriesData = xIsDiscrete ? buildCategoryAlignedData(rows, xField, yField, categories, rankOnY ? rankToIndex : void 0) : orderedRows.map((r) => [toXValue(r[xField]), rankOnY ? rankToIndex(Number(r[yField])) : r[yField]]);
        option.series.push({
          name,
          type: "line",
          data: seriesData,
          ...baseSeriesOpt
          // 颜色由 ecApplyLayoutToSpec 根据 colorDecisions 统一分配
        });
      }
    } else {
      const rows = xIsDiscrete ? table : sortRowsByX(table);
      const seriesData = xIsDiscrete ? buildCategoryAlignedData(rows, xField, yField, categories, rankOnY ? rankToIndex : void 0) : rows.map((r) => [toXValue(r[xField]), rankOnY ? rankToIndex(Number(r[yField])) : r[yField]]);
      option.series.push({ type: "line", data: seriesData, ...baseSeriesOpt });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/slope.ts
function orderPeriods(categories) {
  if (categories.length <= 1) return categories;
  const allNumeric = categories.every((c) => c.trim() !== "" && !isNaN(Number(c)));
  if (allNumeric) return [...categories].sort((a, b) => Number(a) - Number(b));
  const allDates = categories.every((c) => !isNaN(Date.parse(c)));
  if (allDates) return [...categories].sort((a, b) => Date.parse(a) - Date.parse(b));
  return categories;
}
function alignToPeriods(rows, xField, yField, categories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) map.set(String(row[xField]), Number(v));
  }
  return categories.map((cat) => {
    const v = map.get(cat);
    return v != null ? v : null;
  });
}
var ecSlopeChartDef = {
  chart: "Slope Chart",
  template: { mark: "line", encoding: {} },
  channels: ["x", "y", "color", "detail", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({
    axisFlags: { x: { banded: true } },
    paramOverrides: {
      defaultBandSize: 120,
      continuousMarkCrossSection: { x: 0, y: 0, seriesCountAxis: "auto" }
    }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const groupField = channelSemantics.color?.field ?? channelSemantics.detail?.field;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const categories = orderPeriods(extractCategories(table, xField, getCategoryOrder(ctx, "x")));
    const option = {
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: categories,
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        boundaryGap: true,
        axisLine: { show: true },
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      },
      yAxis: {
        type: "value",
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: yField };
    if (channelSemantics.y?.zero) {
      option.yAxis.scale = !channelSemantics.y.zero.zero;
    } else {
      option.yAxis.scale = true;
    }
    const baseSeriesOpt = {
      type: "line",
      showSymbol: true,
      symbol: "circle",
      symbolSize: 7,
      // Straight segments — never smooth/monotone for a slopegraph.
      smooth: false
    };
    if (groupField) {
      const groups = groupBy(table, groupField);
      option.legend = { data: [...groups.keys()] };
      for (const [name, rows] of groups) {
        option.series.push({
          name,
          ...baseSeriesOpt,
          data: alignToPeriods(rows, xField, yField, categories)
          // Colors assigned by ecApplyLayoutToSpec from colorDecisions.
        });
      }
    } else {
      option.series.push({
        ...baseSeriesOpt,
        data: alignToPeriods(table, xField, yField, categories)
      });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/area.ts
var isDiscrete6 = (type) => type === "nominal" || type === "ordinal";
function areCategoriesNumeric2(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var ecAreaChartDef = {
  chart: "Area Chart",
  template: { mark: "area", encoding: {} },
  channels: ["x", "y", "color", "opacity", "column", "row"],
  markCognitiveChannel: "area",
  declareLayoutMode: () => ({
    paramOverrides: { continuousMarkCrossSection: { x: 100, y: 20, seriesCountAxis: "auto" } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorField = channelSemantics.color?.field;
    const colorType = channelSemantics.color?.type;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const xIsDiscrete = isDiscrete6(xCS.type);
    const xIsTemporal = xCS.type === "temporal";
    const yIsDiscrete = isDiscrete6(yCS.type);
    const isContinuousColor = !!colorField && (colorType === "quantitative" || colorType === "temporal");
    const categories = xIsDiscrete ? extractCategories(table, xField, getCategoryOrder(ctx, "x")) : void 0;
    const yCategories = yIsDiscrete ? extractCategories(table, yField, getCategoryOrder(ctx, "y")) : void 0;
    const option = {
      tooltip: { trigger: "axis" },
      xAxis: (() => {
        const type = xIsDiscrete ? "category" : xIsTemporal ? "time" : "value";
        const base = {
          type,
          name: xField,
          nameLocation: "middle",
          nameGap: 30,
          boundaryGap: xIsDiscrete,
          ...categories ? { data: categories } : {}
        };
        if (xIsDiscrete && categories) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: areCategoriesNumeric2(categories) ? 0 : 90 };
        } else if (xIsTemporal) {
          base.axisTick = { show: true, alignWithLabel: true };
          base.axisLabel = { rotate: 90 };
        } else {
          base.axisTick = { show: true };
          base.axisLabel = { rotate: 0 };
        }
        return base;
      })(),
      yAxis: yIsDiscrete && yCategories ? {
        type: "category",
        data: yCategories,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      } : {
        type: "value",
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: yField };
    if (channelSemantics.y?.zero) {
      option.yAxis.scale = !channelSemantics.y.zero.zero;
    }
    const stackMode = chartProperties?.stackMode;
    const stackGroup = stackMode === "layered" ? void 0 : "total";
    const opacity = chartProperties?.opacity ?? 0.7;
    const interpolate = chartProperties?.interpolate;
    const smooth = interpolate === "monotone" || interpolate === "basis" || interpolate === "cardinal" || interpolate === "catmull-rom";
    const step = interpolate === "step" ? "middle" : interpolate === "step-before" ? "start" : interpolate === "step-after" ? "end" : void 0;
    if (isContinuousColor && colorField) {
      const sorted = [...table].sort((a, b) => {
        const ax = a[xField];
        const bx = b[xField];
        if (xIsTemporal) return new Date(ax).getTime() - new Date(bx).getTime();
        const na = Number(ax);
        const nb = Number(bx);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        return String(ax).localeCompare(String(bx));
      });
      const pointData = sorted.map((r) => [r[xField], r[yField], r[colorField]]);
      const lineData = sorted.map((r) => [r[xField], r[yField]]);
      const nums = sorted.map((r) => Number(r[colorField])).filter((v) => !isNaN(v) && isFinite(v));
      const cMin = nums.length ? Math.min(...nums) : 0;
      const cMax = nums.length ? Math.max(...nums) : 1;
      option.tooltip = { trigger: "item" };
      option._encodingTooltip = {
        trigger: "item",
        parts: [
          { from: "data", index: 0, label: xField, format: xIsTemporal ? "temporal" : "number", temporalFormat: channelSemantics.x?.temporalFormat ?? "%b %d, %Y" },
          { from: "data", index: 1, label: yField, format: "number" },
          { from: "data", index: 2, label: colorField, format: colorType === "temporal" ? "temporal" : "number", temporalFormat: channelSemantics.color?.temporalFormat ?? "%b %d, %Y" }
        ]
      };
      const decisionSchemeId = colorDecisions?.color?.schemeId;
      const paletteFromDecision = decisionSchemeId ? getPaletteForScheme(decisionSchemeId) : void 0;
      option.visualMap = {
        type: "continuous",
        min: cMin,
        max: cMax,
        dimension: 2,
        orient: "vertical",
        right: 10,
        top: "center",
        inRange: {
          color: paletteFromDecision && paletteFromDecision.length > 0 ? paletteFromDecision : ["#f7fcf5", "#74c476", "#00441b"]
        },
        seriesIndex: 1,
        name: colorField,
        textStyle: { fontSize: 10 },
        calculable: true
      };
      option._visualMapWidth = 70;
      option.graphic = [
        ...Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [],
        {
          type: "text",
          right: 10,
          top: 4,
          z: 100,
          style: {
            text: colorField,
            fontSize: 11,
            fontWeight: "bold",
            fill: "#333",
            textAlign: "right"
          }
        }
      ];
      option.series.push({
        type: "line",
        data: lineData,
        showSymbol: false,
        symbol: "none",
        areaStyle: { opacity },
        itemStyle: { color: "#999" },
        lineStyle: { color: "#999" },
        ...smooth ? { smooth: true } : {},
        ...step ? { step } : {}
      });
      option.series.push({
        type: "scatter",
        data: pointData,
        symbol: "circle",
        symbolSize: 8,
        itemStyle: { opacity: 1 }
      });
    } else if (colorField) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      const useValueAlignedStack = stackGroup && !xIsDiscrete && !xIsTemporal && !yIsDiscrete;
      const sortedX = useValueAlignedStack ? getSortedUniqueXValues(table, xField) : void 0;
      if (useValueAlignedStack && sortedX && sortedX.length > 0) {
        option.xAxis = {
          type: "category",
          data: sortedX,
          boundaryGap: false,
          name: xField,
          nameLocation: "middle",
          nameGap: 30,
          axisLabel: { rotate: 0 }
        };
      }
      const sortedDates = xIsTemporal ? getSortedUniqueDates(table, xField) : void 0;
      for (const [name, rows] of groups) {
        const seriesData = xIsDiscrete ? buildCategoryAlignedData2(rows, xField, yField, categories) : useValueAlignedStack && sortedX ? buildValueAlignedYData(rows, xField, yField, sortedX) : sortedDates ? buildTimeAlignedData(rows, xField, yField, sortedDates) : rows.map((r) => [r[xField], r[yField]]);
        const series = {
          name,
          type: "line",
          data: seriesData,
          showSymbol: false,
          symbol: "none",
          areaStyle: { opacity }
          // 颜色由 ecApplyLayoutToSpec 根据 colorDecisions 统一分配
        };
        if (stackGroup) series.stack = stackGroup;
        if (smooth) series.smooth = true;
        if (step) series.step = step;
        option.series.push(series);
      }
    } else {
      const seriesData = yIsDiscrete && yCategories ? buildCategoryAlignedXYData2(table, xField, yField, yCategories) : xIsDiscrete ? categories.map((cat) => {
        const row = table.find((r) => String(r[xField]) === cat);
        return row ? row[yField] : null;
      }) : xIsTemporal ? (() => {
        const sorted = [...table].sort((a, b) => new Date(a[xField]).getTime() - new Date(b[xField]).getTime());
        return sorted.map((r) => [r[xField], r[yField]]);
      })() : table.map((r) => [r[xField], r[yField]]);
      const series = {
        type: "line",
        data: seriesData,
        showSymbol: false,
        symbol: "none",
        areaStyle: { opacity }
      };
      if (smooth) series.smooth = true;
      if (step) series.step = step;
      option.series.push(series);
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "interpolate",
      label: "Curve",
      type: "discrete",
      options: [
        { value: void 0, label: "Default (linear)" },
        { value: "linear", label: "Linear" },
        { value: "monotone", label: "Monotone (smooth)" },
        { value: "step", label: "Step" },
        { value: "step-before", label: "Step Before" },
        { value: "step-after", label: "Step After" },
        { value: "basis", label: "Basis (smooth)" },
        { value: "cardinal", label: "Cardinal" },
        { value: "catmull-rom", label: "Catmull-Rom" }
      ]
    },
    {
      key: "opacity",
      label: "Opacity",
      type: "continuous",
      min: 0.1,
      max: 1,
      step: 0.05,
      defaultValue: 0.7,
      check: (ctx) => ({
        applicable: !!ctx.encodings.color?.field && ctx.chartProperties?.stackMode === "layered"
      })
    },
    {
      key: "stackMode",
      label: "Stack",
      type: "discrete",
      options: [
        { value: void 0, label: "Stacked (default)" },
        { value: "normalize", label: "Normalize (100%)" },
        { value: "center", label: "Center" },
        { value: "layered", label: "Layered (overlap)" }
      ]
    }
  ]
};
function buildCategoryAlignedData2(rows, xField, yField, categories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) {
      const k = String(row[xField]);
      map.set(k, (map.get(k) ?? 0) + Number(v));
    }
  }
  return categories.map((cat) => map.get(cat) ?? null);
}
function buildCategoryAlignedXYData2(rows, xField, yField, yCategories) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const key = String(row[yField] ?? "");
    if (!map.has(key)) {
      map.set(key, row[xField]);
    }
  }
  return yCategories.filter((cat) => map.has(cat)).map((cat) => [map.get(cat), cat]);
}
function getSortedUniqueDates(table, xField) {
  const set = /* @__PURE__ */ new Set();
  for (const row of table) {
    const v = row[xField];
    if (v != null && v !== "") set.add(String(v));
  }
  return [...set].sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
}
function getSortedUniqueXValues(table, xField) {
  const set = /* @__PURE__ */ new Set();
  for (const row of table) {
    const v = row[xField];
    if (v == null || v === "") continue;
    const n = Number(v);
    if (Number.isFinite(n)) set.add(n);
  }
  return [...set].sort((a, b) => a - b);
}
function buildValueAlignedYData(rows, xField, yField, sortedX) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const x = Number(row[xField]);
    const y = Number(row[yField]);
    if (!Number.isFinite(x)) continue;
    map.set(x, Number.isFinite(y) ? y : 0);
  }
  return sortedX.map((x) => map.get(x) ?? 0);
}
function buildTimeAlignedData(rows, xField, yField, sortedDates) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const n = Number(row[yField]);
    map.set(String(row[xField]), Number.isFinite(n) ? n : 0);
  }
  return sortedDates.map((d) => [d, map.get(d) ?? 0]);
}

// src/echarts/templates/range-area.ts
var isDiscrete7 = (type) => type === "nominal" || type === "ordinal";
function orderedXLabels(table, xField, xType, ordinalOrder) {
  if (isDiscrete7(xType)) {
    return { labels: extractCategories(table, xField, ordinalOrder), isTemporal: false };
  }
  const isTemporal2 = xType === "temporal";
  const seen = /* @__PURE__ */ new Map();
  for (const row of table) {
    const v = row[xField];
    if (v == null || v === "") continue;
    const key = String(v);
    if (!seen.has(key)) seen.set(key, v);
  }
  const raw = [...seen.values()];
  if (isTemporal2) {
    raw.sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  } else {
    raw.sort((a, b) => Number(a) - Number(b));
  }
  return { labels: raw.map((v) => String(v)), isTemporal: isTemporal2 };
}
function fmtTemporalLabel(s) {
  const t = new Date(s).getTime();
  if (!Number.isFinite(t)) return s;
  return new Date(t).toLocaleDateString(void 0, { month: "short", day: "numeric", year: "numeric" });
}
function alignBounds(rows, xField, lowField, highField, labels) {
  const map = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const lo = Number(row[lowField]);
    const hi = Number(row[highField]);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) continue;
    map.set(String(row[xField]), { low: Math.min(lo, hi), high: Math.max(lo, hi) });
  }
  return labels.map((l) => {
    const v = map.get(l);
    return v ? { low: v.low, high: v.high } : { low: null, high: null };
  });
}
var ecRangeAreaChartDef = {
  chart: "Range Area Chart",
  template: { mark: "area", encoding: {} },
  channels: ["x", "y", "y2", "color", "column", "row"],
  markCognitiveChannel: "area",
  declareLayoutMode: () => ({
    paramOverrides: { continuousMarkCrossSection: { x: 100, y: 20, seriesCountAxis: "auto" } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const y2CS = channelSemantics.y2;
    const colorField = channelSemantics.color?.field;
    if (!xCS?.field || !yCS?.field || !y2CS?.field) return;
    const xField = xCS.field;
    const lowField = yCS.field;
    const highField = y2CS.field;
    const { labels, isTemporal: isTemporal2 } = orderedXLabels(
      table,
      xField,
      xCS.type,
      getCategoryOrder(ctx, "x")
    );
    const displayLabels = isTemporal2 ? labels.map(fmtTemporalLabel) : labels;
    const opacity = ctx.chartProperties?.opacity ?? 0.35;
    const valueTitle = lowField === highField ? lowField : `${lowField}, ${highField}`;
    const option = {
      tooltip: {
        trigger: "axis",
        // Show the low–high range at each x (the delta series carries the
        // original bounds as `_low` / `_high`; the transparent base
        // series has plain numeric data and is skipped).
        formatter: (params) => {
          const list = Array.isArray(params) ? params : [params];
          if (list.length === 0) return "";
          const head = list[0].axisValueLabel ?? list[0].axisValue ?? list[0].name ?? "";
          const lines = [`${xField}: ${head}`];
          for (const p of list) {
            const d = p?.data;
            if (d && typeof d === "object" && d._high != null) {
              const nm = p.seriesName && !String(p.seriesName).startsWith("__base") ? p.seriesName : "Range";
              lines.push(`${nm}: ${fmtNum(d._low)} \u2013 ${fmtNum(d._high)}`);
            }
          }
          return lines.join("<br/>");
        }
      },
      xAxis: {
        type: "category",
        data: displayLabels,
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        boundaryGap: false,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: isTemporal2 ? 30 : 0 }
      },
      yAxis: {
        type: "value",
        // A ranged area reads its extent, not its distance from zero —
        // fit the band rather than forcing a zero baseline.
        scale: true,
        name: valueTitle,
        nameLocation: "middle",
        nameGap: 45,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    const pushBand = (rows, name, idx) => {
      const bounds = alignBounds(rows, xField, lowField, highField, labels);
      const stackId = `band-${idx}`;
      const baseName = `__base-${idx}`;
      option.series.push({
        name: baseName,
        type: "line",
        stack: stackId,
        // Cumulative stacking regardless of sign — otherwise ECharts
        // routes a negative lower bound into a separate negative stack
        // and the band collapses to the zero baseline (see the
        // zero-crossing case).
        stackStrategy: "all",
        data: bounds.map((b) => b.low),
        symbol: "none",
        showSymbol: false,
        lineStyle: { opacity: 0 },
        itemStyle: { color: "transparent" },
        silent: true,
        z: 1
      });
      option.series.push({
        name: name ?? lowField,
        type: "line",
        stack: stackId,
        stackStrategy: "all",
        data: bounds.map(
          (b) => b.low != null && b.high != null ? { value: b.high - b.low, _low: b.low, _high: b.high } : { value: null, _low: null, _high: null }
        ),
        symbol: "none",
        showSymbol: false,
        lineStyle: { width: 1.5, opacity: 0.9 },
        areaStyle: { opacity },
        emphasis: { focus: "series" },
        z: 2
      });
    };
    if (colorField) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      let idx = 0;
      for (const [name, rows] of groups) {
        pushBand(rows, name, idx);
        idx++;
      }
    } else {
      pushBand(table, void 0, 0);
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};
function fmtNum(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? "");
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

// src/echarts/templates/pie.ts
var ecPieChartDef = {
  chart: "Pie Chart",
  template: { mark: "arc", encoding: {} },
  channels: ["size", "color", "column", "row"],
  markCognitiveChannel: "area",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const colorField = channelSemantics.color?.field;
    const sizeField = channelSemantics.size?.field;
    const pieData = [];
    if (colorField && sizeField) {
      const agg = /* @__PURE__ */ new Map();
      for (const row of table) {
        const cat = String(row[colorField] ?? "");
        const val = Number(row[sizeField]) || 0;
        agg.set(cat, (agg.get(cat) ?? 0) + val);
      }
      const categories = extractCategories(table, colorField, channelSemantics.color?.ordinalSortOrder);
      for (const cat of categories) {
        pieData.push({ name: cat, value: agg.get(cat) ?? 0 });
      }
    } else if (colorField) {
      const counts = /* @__PURE__ */ new Map();
      for (const row of table) {
        const cat = String(row[colorField] ?? "");
        counts.set(cat, (counts.get(cat) ?? 0) + 1);
      }
      const categories = extractCategories(table, colorField, channelSemantics.color?.ordinalSortOrder);
      for (const cat of categories) {
        pieData.push({ name: cat, value: counts.get(cat) ?? 0 });
      }
    } else if (sizeField) {
      for (const row of table) {
        const val = Number(row[sizeField]) || 0;
        pieData.push({ name: String(val), value: val });
      }
    }
    const innerRadius = chartProperties?.innerRadius ?? 0;
    const sortSlices = chartProperties?.sortSlices;
    if (sortSlices === "descending") {
      pieData.sort((a, b) => b.value - a.value);
    } else if (sortSlices === "ascending") {
      pieData.sort((a, b) => a.value - b.value);
    }
    const labelType = chartProperties?.labelType ?? "categoryPercent";
    const labelFormatter = {
      none: void 0,
      category: "{b}",
      value: "{c}",
      percent: "{d}%",
      categoryPercent: "{b}: {d}%"
    };
    const formatter = labelFormatter[labelType] ?? "{b}: {d}%";
    const sliceValues = pieData.map((d) => d.value);
    const effectiveCount = computeEffectiveBarCount(sliceValues);
    const { radius: pressureRadius, canvasW: rawCanvasW, canvasH } = computeCircumferencePressure(effectiveCount, ctx.canvasSize, {
      minArcPx: 45,
      minRadius: 60,
      maxStretch: ctx.assembleOptions?.maxStretch,
      maxStretchX: ctx.assembleOptions?.maxStretchX,
      maxStretchY: ctx.assembleOptions?.maxStretchY,
      // 增大 margin，给外侧标签留出更多画布空间，避免文字被裁切。
      margin: 80
    });
    const canvasW = rawCanvasW;
    const n = pieData.length;
    const labelFontSize = n <= 4 ? 13 : n <= 8 ? 11 : n <= 15 ? 10 : 9;
    const maxLabelChars = pieData.reduce((m, d) => {
      const len = String(d.name ?? "").length;
      return len > m ? len : m;
    }, 0);
    const approxCharWidth = labelFontSize * 0.55;
    const neededLabelWidth = Math.max(40, maxLabelChars * approxCharWidth);
    const baseRadiusFraction = n <= 4 ? 0.72 : n <= 8 ? 0.62 : n <= 15 ? 0.54 : 0.48;
    const halfCanvas = (canvasW - 40) / 2;
    const padding = 16;
    const maxLabelWidthAvailable = Math.max(40, halfCanvas - halfCanvas * baseRadiusFraction - padding);
    const labelBudget = Math.min(neededLabelWidth, maxLabelWidthAvailable);
    const radiusFraction = baseRadiusFraction;
    const labelLineLength = Math.max(10, Math.min(22, 10 + neededLabelWidth * 0.1));
    const labelLineLength2 = Math.max(8, Math.min(26, 8 + neededLabelWidth * 0.15));
    const outerRadiusPx = Math.max(60, Math.round(
      Math.min(
        pressureRadius,
        (canvasW - 40) / 2 * radiusFraction,
        (canvasH - 40) / 2 * radiusFraction
      )
    ));
    const outerRadius = `${outerRadiusPx}px`;
    const categoryLabel = colorField ?? "Category";
    const valueLabel = sizeField ?? "Value";
    const option = {
      tooltip: { trigger: "item" },
      _encodingTooltip: {
        trigger: "item",
        parts: [
          { from: "name", label: categoryLabel },
          { from: "value", label: valueLabel, format: "number" }
        ]
      },
      series: [{
        type: "pie",
        radius: innerRadius > 0 ? [`${Math.round(outerRadiusPx * innerRadius / 100)}px`, outerRadius] : ["0%", outerRadius],
        center: ["50%", "50%"],
        data: pieData,
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: "rgba(0, 0, 0, 0.5)"
          }
        },
        label: {
          show: labelType !== "none",
          formatter: formatter ?? "{b}: {d}%",
          fontSize: labelFontSize,
          width: labelBudget,
          overflow: "break"
          // word-wrap long labels
        },
        // 让 ECharts 尝试自动避免标签重叠，并在必要时隐藏重叠标签，
        // 减少标签被挤到画布外的概率。
        avoidLabelOverlap: true,
        labelLayout: {
          hideOverlap: true
        },
        labelLine: {
          show: true,
          length: labelLineLength,
          length2: labelLineLength2
        },
        itemStyle: {
          borderRadius: chartProperties?.cornerRadius ?? 0
        }
      }]
      // 颜色由 ecApplyLayoutToSpec 根据 colorDecisions 设置 option.color
    };
    option._width = canvasW;
    option._height = canvasH;
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "innerRadius", label: "Donut", type: "continuous", min: 0, max: 60, step: 5, defaultValue: 0 },
    { key: "cornerRadius", label: "Corners", type: "continuous", min: 0, max: 10, step: 1, defaultValue: 0 },
    {
      key: "sortSlices",
      label: "Sort slices",
      type: "discrete",
      options: [
        { value: "none", label: "Data order" },
        { value: "descending", label: "Largest first" },
        { value: "ascending", label: "Smallest first" }
      ],
      defaultValue: "none"
    },
    {
      key: "labelType",
      label: "Labels",
      type: "discrete",
      options: [
        { value: "categoryPercent", label: "Name + %" },
        { value: "category", label: "Name" },
        { value: "value", label: "Value" },
        { value: "percent", label: "Percent" },
        { value: "none", label: "None" }
      ],
      defaultValue: "categoryPercent"
    }
  ]
};

// src/echarts/templates/heatmap.ts
function areCategoriesNumeric3(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var SCHEME_COLORS = {
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  inferno: ["#000004", "#420a68", "#932667", "#dd513a", "#fca50a", "#fcffa4"],
  magma: ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fcfdbf"],
  plasma: ["#0d0887", "#6a00a8", "#b12a90", "#e16462", "#fca636", "#f0f921"],
  turbo: ["#30123b", "#4662d7", "#35abed", "#1ae4b6", "#72fe5e", "#c8ef34", "#faba39", "#f66b19", "#d23105", "#7a0403"],
  blues: ["#f7fbff", "#6baed6", "#08519c"],
  reds: ["#fff5f0", "#fb6a4a", "#a50f15"],
  greens: ["#f7fcf5", "#74c476", "#00441b"],
  oranges: ["#fff5eb", "#fd8d3c", "#7f2704"],
  purples: ["#fcfbfd", "#9e9ac8", "#3f007d"],
  greys: ["#ffffff", "#969696", "#252525"],
  blueorange: ["#08519c", "#f7fbff", "#ff7f00"],
  redblue: ["#a50f15", "#ffffff", "#08519c"]
};
var DEFAULT_HEATMAP_SCHEME = "blues";
function isDivergingHeatmapScheme(scheme) {
  return scheme === "blueorange" || scheme === "redblue";
}
var ecHeatmapDef = {
  chart: "Heatmap",
  template: { mark: "rect", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "color",
  declareLayoutMode: () => ({
    axisFlags: { x: { banded: true }, y: { banded: true } }
    // No paramOverrides needed — uses the backend default band size
    // (defaultBandSize=20, minStep=6), matching VL heatmap sizing.
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, colorDecisions, encodings } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorCS = channelSemantics.color;
    const xField = xCS?.field;
    const yField = yCS?.field;
    const colorField = colorCS?.field;
    if (!xField || !yField) return;
    const xCategories = extractCategories(table, xField, xCS?.ordinalSortOrder);
    const yCategories = extractCategories(table, yField, yCS?.ordinalSortOrder);
    const xIndexMap = new Map(xCategories.map((c, i) => [c, i]));
    const yIndexMap = new Map(yCategories.map((c, i) => [c, i]));
    const heatData = [];
    let minVal = Infinity;
    let maxVal = -Infinity;
    const cellMap = /* @__PURE__ */ new Map();
    for (const row of table) {
      const xKey = String(row[xField]);
      const yKey = String(row[yField]);
      const val = colorField ? Number(row[colorField]) || 0 : 1;
      const cellKey = `${xKey}|||${yKey}`;
      cellMap.set(cellKey, (cellMap.get(cellKey) ?? 0) + val);
    }
    for (const [cellKey, val] of cellMap) {
      const [xKey, yKey] = cellKey.split("|||");
      const xi = xIndexMap.get(xKey);
      const yi = yIndexMap.get(yKey);
      if (xi !== void 0 && yi !== void 0) {
        heatData.push([xi, yi, val]);
        if (val < minVal) minVal = val;
        if (val > maxVal) maxVal = val;
      }
    }
    if (minVal === Infinity) minVal = 0;
    if (maxVal === -Infinity) maxVal = 1;
    const encScheme = encodings?.color?.scheme;
    const userScheme = encScheme && encScheme !== "default" ? encScheme : void 0;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    const semanticIsDiverging = decision?.schemeType === "diverging";
    const schemeName = userScheme || (semanticIsDiverging ? "redblue" : DEFAULT_HEATMAP_SCHEME);
    const isDivergingScale = semanticIsDiverging || isDivergingHeatmapScheme(schemeName);
    if (isDivergingScale && minVal < 0 && maxVal > 0) {
      const sym = Math.max(Math.abs(minVal), Math.abs(maxVal));
      minVal = -sym;
      maxVal = sym;
    }
    let schemeColors;
    if (decision) {
      let paletteFromDecision;
      if (decision.schemeId) {
        paletteFromDecision = getPaletteForScheme(decision.schemeId);
      }
      if (!paletteFromDecision || paletteFromDecision.length === 0) {
        if (decision.schemeType === "diverging") {
          paletteFromDecision = getPaletteForScheme("RdBu");
        } else if (decision.schemeType === "sequential") {
          paletteFromDecision = SCHEME_COLORS[DEFAULT_HEATMAP_SCHEME];
        }
      }
      if (paletteFromDecision && paletteFromDecision.length > 0) {
        schemeColors = paletteFromDecision;
      } else {
        schemeColors = SCHEME_COLORS[schemeName] || SCHEME_COLORS[DEFAULT_HEATMAP_SCHEME];
      }
    } else {
      schemeColors = SCHEME_COLORS[schemeName] || SCHEME_COLORS[DEFAULT_HEATMAP_SCHEME];
    }
    const option = {
      tooltip: { position: "top" },
      _encodingTooltip: {
        trigger: "item",
        parts: [
          { from: "data", index: 0, label: xField, format: "category", categoryNames: xCategories },
          { from: "data", index: 1, label: yField, format: "category", categoryNames: yCategories },
          { from: "data", index: 2, label: colorField ?? "Value", format: "number" }
        ]
      },
      xAxis: {
        type: "category",
        data: xCategories,
        name: xField,
        splitArea: { show: true },
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: {
          rotate: areCategoriesNumeric3(xCategories) ? 0 : 90
        }
      },
      yAxis: {
        type: "category",
        data: yCategories,
        name: yField,
        splitArea: { show: true },
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      },
      visualMap: {
        min: minVal,
        max: maxVal,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        inRange: {
          color: schemeColors
        }
      },
      series: [{
        type: "heatmap",
        data: heatData,
        label: {
          show: heatData.length <= 100
          // Show labels for small heatmaps
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: "rgba(0, 0, 0, 0.5)"
          }
        }
      }]
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  postProcess: (option, ctx) => {
    const heatSeries = option.series?.find((s) => s.type === "heatmap");
    const { layout } = ctx;
    const cellW = layout.xStep || 50;
    const cellH = layout.yStep || 50;
    const minDim = Math.min(cellW, cellH);
    if (heatSeries?.label) {
      if (minDim < 30) {
        heatSeries.label.show = false;
      } else {
        const fontSize = Math.max(8, Math.min(12, Math.round(minDim * 0.2)));
        heatSeries.label.fontSize = fontSize;
        if (cellW < 50) {
          const maxChars = Math.max(2, Math.floor(cellW / (fontSize * 0.6)));
          heatSeries.label.formatter = (params) => {
            const val = params.data[2];
            const s = String(val);
            return s.length > maxChars ? s.slice(0, maxChars) : s;
          };
        }
      }
    }
    if (option.visualMap && option.grid) {
      const vmHeight = 50;
      option.grid.bottom = (option.grid.bottom || 30) + vmHeight;
      option.visualMap.bottom = 5;
      if (option._height) {
        option._height += vmHeight;
      }
    }
  },
  encodingActions: [
    {
      key: "colorScheme",
      label: "Scheme",
      isApplicable: (ctx) => !!ctx.encodings.color?.field,
      dependencies: ["color"],
      control: {
        type: "discrete",
        options: [
          { value: void 0, label: "Default (Blues)" },
          { value: "viridis", label: "Viridis" },
          { value: "inferno", label: "Inferno" },
          { value: "magma", label: "Magma" },
          { value: "plasma", label: "Plasma" },
          { value: "turbo", label: "Turbo" },
          { value: "blues", label: "Blues" },
          { value: "reds", label: "Reds" },
          { value: "greens", label: "Greens" },
          { value: "oranges", label: "Oranges" },
          { value: "purples", label: "Purples" },
          { value: "greys", label: "Greys" },
          { value: "blueorange", label: "Blue-Orange (diverging)" },
          { value: "redblue", label: "Red-Blue (diverging)" }
        ]
      },
      get: (enc) => enc.color?.scheme,
      set: (enc, value) => ({ ...enc, color: { ...enc.color, scheme: value } })
    }
  ],
  pivot: makeCartesianPivot({ transpose: [["x", "y"]] })
};

// src/echarts/templates/histogram.ts
var ecHistogramDef = {
  chart: "Histogram",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "color", "column", "row"],
  markCognitiveChannel: "length",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xField = channelSemantics.x?.field;
    const colorField = channelSemantics.color?.field;
    if (!xField) return;
    const values = table.map((r) => Number(r[xField])).filter((v) => isFinite(v));
    if (values.length === 0) return;
    const binCount = chartProperties?.binCount || 10;
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const range = maxVal - minVal;
    const binWidth = range > 0 ? range / binCount : 1;
    if (!colorField) {
      const counts = new Array(binCount).fill(0);
      for (const v of values) {
        let idx = Math.floor((v - minVal) / binWidth);
        if (idx >= binCount) idx = binCount - 1;
        counts[idx]++;
      }
      const categories = counts.map((_, i) => {
        const lo = (minVal + i * binWidth).toFixed(1);
        const hi = (minVal + (i + 1) * binWidth).toFixed(1);
        return `${lo}\u2013${hi}`;
      });
      const option = {
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" }
        },
        xAxis: {
          type: "category",
          data: categories,
          name: xField,
          nameLocation: "middle",
          nameGap: 25,
          axisTick: { show: true, alignWithLabel: true },
          axisLabel: { rotate: categories.length > 10 ? 45 : 0 }
        },
        yAxis: {
          type: "value",
          name: "Count",
          nameLocation: "middle",
          nameGap: 40,
          axisTick: { show: true }
        },
        series: [{
          type: "bar",
          data: counts,
          barCategoryGap: "0%",
          // contiguous bars
          itemStyle: {
            borderColor: "#fff",
            borderWidth: 0.5
          }
        }]
      };
      option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: "Count" };
      Object.assign(spec, option);
    } else {
      const groupValues = /* @__PURE__ */ new Map();
      for (const row of table) {
        const v = Number(row[xField]);
        if (!isFinite(v)) continue;
        const g = String(row[colorField] ?? "");
        if (!groupValues.has(g)) groupValues.set(g, []);
        groupValues.get(g).push(v);
      }
      const categories = Array.from({ length: binCount }, (_, i) => {
        const lo = (minVal + i * binWidth).toFixed(1);
        const hi = (minVal + (i + 1) * binWidth).toFixed(1);
        return `${lo}\u2013${hi}`;
      });
      const series = [];
      const legendData = [];
      for (const [name, vals] of groupValues) {
        const counts = new Array(binCount).fill(0);
        for (const v of vals) {
          let idx = Math.floor((v - minVal) / binWidth);
          if (idx >= binCount) idx = binCount - 1;
          counts[idx]++;
        }
        legendData.push(name);
        series.push({
          name,
          type: "bar",
          data: counts,
          stack: "total",
          barCategoryGap: "0%",
          itemStyle: {
            borderColor: "#fff",
            borderWidth: 0.5
          }
        });
      }
      const option = {
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        legend: { data: legendData },
        xAxis: {
          type: "category",
          data: categories,
          name: xField,
          nameLocation: "middle",
          nameGap: 25,
          axisTick: { show: true, alignWithLabel: true },
          axisLabel: { rotate: categories.length > 10 ? 45 : 0 }
        },
        yAxis: {
          type: "value",
          name: "Count",
          nameLocation: "middle",
          nameGap: 40,
          axisTick: { show: true }
        },
        series
      };
      option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: "Count" };
      Object.assign(spec, option);
    }
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "binCount", label: "Max Bins", type: "continuous", min: 5, max: 50, step: 1, defaultValue: 0 }
  ]
};

// src/echarts/templates/boxplot.ts
var isDiscrete8 = (type) => type === "nominal" || type === "ordinal";
function areCategoriesNumeric4(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
function fiveNumberSummary(values, whiskerMethod = "iqr") {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 0) return [0, 0, 0, 0, 0];
  if (n === 1) return [sorted[0], sorted[0], sorted[0], sorted[0], sorted[0]];
  const median = quantile(sorted, 0.5);
  const q1 = quantile(sorted, 0.25);
  const q3 = quantile(sorted, 0.75);
  if (whiskerMethod === "minmax") {
    return [sorted[0], q1, median, q3, sorted[n - 1]];
  }
  const iqr = q3 - q1;
  const lowerFence = q1 - 1.5 * iqr;
  const upperFence = q3 + 1.5 * iqr;
  const whiskerLow = sorted.find((v) => v >= lowerFence) ?? sorted[0];
  const whiskerHigh = [...sorted].reverse().find((v) => v <= upperFence) ?? sorted[n - 1];
  return [whiskerLow, q1, median, q3, whiskerHigh];
}
function quantile(sorted, p) {
  const n = sorted.length;
  const idx = p * (n - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  const frac = idx - lo;
  return sorted[lo] * (1 - frac) + sorted[hi] * frac;
}
function findOutliers(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const q1 = quantile(sorted, 0.25);
  const q3 = quantile(sorted, 0.75);
  const iqr = q3 - q1;
  const lo = q1 - 1.5 * iqr;
  const hi = q3 + 1.5 * iqr;
  return values.filter((v) => v < lo || v > hi);
}
function boxplotLaneOffset(bandWidth, laneCount, laneIndex) {
  const availableWidth = bandWidth * 0.8 - 2;
  const boxGap = availableWidth / laneCount * 0.3;
  const boxWidth = (availableWidth - boxGap * (laneCount - 1)) / laneCount;
  return boxWidth / 2 - availableWidth / 2 + laneIndex * (boxGap + boxWidth);
}
function makeOutlierSeries(name, data, laneIndex, laneCount, horizontal) {
  return {
    name,
    type: "custom",
    coordinateSystem: "cartesian2d",
    data,
    encode: { tooltip: [0, 1] },
    z: 3,
    renderItem: (_params, api) => {
      const category = Number(api.value(0));
      const value = Number(api.value(1));
      const point = horizontal ? api.coord([value, category]) : api.coord([category, value]);
      const size = api.size(horizontal ? [0, 1] : [1, 0]);
      const bandWidth = Math.abs(horizontal ? size[1] : size[0]);
      const offset = boxplotLaneOffset(bandWidth, laneCount, laneIndex);
      return {
        type: "circle",
        shape: {
          cx: point[0] + (horizontal ? 0 : offset),
          cy: point[1] + (horizontal ? offset : 0),
          r: 2
        },
        style: { fill: api.visual("color") }
      };
    }
  };
}
function makeGroupSeparatorSeries(categoryCount, horizontal) {
  return {
    name: "__groupSeparators",
    type: "custom",
    coordinateSystem: "cartesian2d",
    data: Array.from({ length: Math.max(0, categoryCount - 1) }, (_, index) => [index]),
    silent: true,
    tooltip: { show: false },
    z: 0,
    renderItem: (params, api) => {
      const index = Number(api.value(0));
      const current = horizontal ? api.coord([0, index]) : api.coord([index, 0]);
      const next = horizontal ? api.coord([0, index + 1]) : api.coord([index + 1, 0]);
      const boundary = horizontal ? (current[1] + next[1]) / 2 : (current[0] + next[0]) / 2;
      return {
        type: "line",
        shape: horizontal ? { x1: params.coordSys.x, y1: boundary, x2: params.coordSys.x + params.coordSys.width, y2: boundary } : { x1: boundary, y1: params.coordSys.y, x2: boundary, y2: params.coordSys.y + params.coordSys.height },
        style: { stroke: "#c9ced6", lineWidth: 1, lineDash: [4, 4], opacity: 0.75 }
      };
    }
  };
}
var ecBoxplotDef = {
  chart: "Boxplot",
  template: { mark: "boxplot", encoding: {} },
  channels: ["x", "y", "color", "opacity", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: (cs, table, chartProperties) => {
    if (!cs.x?.field || !cs.y?.field) return {};
    const result = detectBandedAxisForceDiscrete(cs, table, { preferAxis: "x" });
    if (!result) return {};
    const decl = {
      axisFlags: { [result.axis]: { banded: true } },
      resolvedTypes: result.resolvedTypes,
      paramOverrides: { defaultBandSize: 28 }
      // box+whisker needs wider bands
    };
    const colorField = cs.color?.field;
    const axisField = cs[result.axis]?.field;
    if (colorField && axisField && isDiscrete8(cs.color?.type)) {
      const plan = planBandDodge(table, axisField, colorField);
      const { mode } = resolveDodge(plan, chartProperties?.dodge);
      if (mode === "local") decl.groupLaneCount = Math.max(1, plan.maxPerBand);
    }
    return decl;
  },
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorField = channelSemantics.color?.field;
    const colorType = channelSemantics.color?.type;
    const colorIsDiscrete = colorField && isDiscrete8(colorType);
    if (!xCS?.field || !yCS?.field) return;
    const whiskerMethod = ctx.chartProperties?.whiskerMethod === "minmax" ? "minmax" : "iqr";
    const showOutliers = whiskerMethod === "iqr" && ctx.chartProperties?.showOutliers !== false;
    const xIsDiscrete = isDiscrete8(xCS.type);
    const yIsDiscrete = isDiscrete8(yCS.type);
    let catAxis = "x";
    let valAxis = "y";
    if (yIsDiscrete && !xIsDiscrete) {
      catAxis = "y";
      valAxis = "x";
    }
    const catField = channelSemantics[catAxis].field;
    const valField = channelSemantics[valAxis].field;
    const catCS = channelSemantics[catAxis];
    const categories = extractCategories(table, catField, catCS?.ordinalSortOrder);
    const dodgePlan = colorIsDiscrete && colorField ? planBandDodge(ctx.fullTable ?? table, catField, colorField, {
      nestedSnapThreshold: ctx.chartProperties?.nestedSnapThreshold
    }) : null;
    const dodgeMode = dodgePlan ? resolveDodge(dodgePlan, ctx.chartProperties?.dodge).mode : "none";
    const dodgeColor = dodgeMode !== "none";
    const isHorizontal = catAxis === "y";
    const catAxisLabel = {
      rotate: isHorizontal ? 0 : areCategoriesNumeric4(categories) ? 0 : 90
    };
    const option = {
      tooltip: { trigger: "item" },
      [isHorizontal ? "yAxis" : "xAxis"]: {
        type: "category",
        data: categories,
        name: catField,
        boundaryGap: true,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: catAxisLabel
      },
      [isHorizontal ? "xAxis" : "yAxis"]: {
        type: "value",
        name: valField,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: []
    };
    if (colorIsDiscrete && colorField && dodgeMode === "local") {
      const globalColors = [...new Set(
        (ctx.fullTable ?? table).map((r) => String(r[colorField] ?? ""))
      )].filter(Boolean).sort();
      const palette = pickEChartsPalette(ctx.colorDecisions?.color);
      const colorFor = (g) => palette[Math.max(0, globalColors.indexOf(g)) % palette.length];
      const perBand = /* @__PURE__ */ new Map();
      for (const cat of categories) perBand.set(cat, []);
      for (const r of table) {
        const cat = String(r[catField] ?? "");
        const g = String(r[colorField] ?? "");
        if (!perBand.has(cat) || !g) continue;
        const arr = perBand.get(cat);
        if (!arr.includes(g)) arr.push(g);
      }
      for (const arr of perBand.values()) arr.sort();
      const maxPerBand = Math.max(1, ...[...perBand.values()].map((a) => a.length));
      const catGroups = groupBy(table, catField);
      for (let lane = 0; lane < maxPerBand; lane++) {
        const boxData = [];
        const outlierData = [];
        for (let i = 0; i < categories.length; i++) {
          const cat = categories[i];
          const g = perBand.get(cat)?.[lane];
          if (g === void 0) {
            boxData.push("-");
            continue;
          }
          const rows = (catGroups.get(cat) || []).filter((r) => String(r[colorField] ?? "") === g);
          const values = rows.map((r) => Number(r[valField])).filter((v) => isFinite(v));
          if (!values.length) {
            boxData.push("-");
            continue;
          }
          const c = colorFor(g);
          boxData.push({ value: fiveNumberSummary(values, whiskerMethod), itemStyle: { color: c, borderColor: c } });
          if (showOutliers) {
            for (const o of findOutliers(values)) outlierData.push({ value: [i, o], itemStyle: { color: c } });
          }
        }
        option.series.push({ name: `__lane${lane}`, type: "boxplot", data: boxData });
        if (outlierData.length > 0) {
          option.series.push(makeOutlierSeries(
            `__lane${lane} (outliers)`,
            outlierData,
            lane,
            maxPerBand,
            isHorizontal
          ));
        }
      }
      option.series.push(makeGroupSeparatorSeries(categories.length, isHorizontal));
      option.legend = { data: globalColors.map((g) => ({ name: g, itemStyle: { color: colorFor(g) } })) };
      option._legendTitle = colorField;
    } else if (colorIsDiscrete && colorField && dodgeColor) {
      const colorCategories = extractCategories(table, colorField, getCategoryOrder(ctx, "color"));
      const catGroups = groupBy(table, catField);
      for (let cIdx = 0; cIdx < colorCategories.length; cIdx++) {
        const colorName = colorCategories[cIdx];
        const boxData = [];
        const outlierData = [];
        for (let i = 0; i < categories.length; i++) {
          const cat = categories[i];
          const rows = (catGroups.get(cat) || []).filter(
            (r) => String(r[colorField] ?? "") === colorName
          );
          const values = rows.map((r) => Number(r[valField])).filter((v) => isFinite(v));
          boxData.push(values.length ? fiveNumberSummary(values, whiskerMethod) : "-");
          if (showOutliers) {
            for (const o of findOutliers(values)) {
              outlierData.push([i, o]);
            }
          }
        }
        option.series.push({
          name: colorName,
          type: "boxplot",
          data: boxData
          // itemStyle 由 ecApplyLayoutToSpec 按 colorDecisions 填充
        });
        if (outlierData.length > 0) {
          option.series.push(makeOutlierSeries(
            colorName + " (outliers)",
            outlierData,
            cIdx,
            colorCategories.length,
            isHorizontal
          ));
        }
      }
      option.legend = { data: colorCategories };
      option._legendTitle = colorField;
    } else {
      const catGroups = groupBy(table, catField);
      const boxData = [];
      const outlierData = [];
      for (let i = 0; i < categories.length; i++) {
        const cat = categories[i];
        const rows = catGroups.get(cat) || [];
        const values = rows.map((r) => Number(r[valField])).filter((v) => isFinite(v));
        boxData.push(fiveNumberSummary(values, whiskerMethod));
        if (showOutliers) {
          for (const o of findOutliers(values)) {
            outlierData.push([i, o]);
          }
        }
      }
      option.series.push({
        type: "boxplot",
        data: boxData
        // 单系列颜色由 ecApplyLayoutToSpec 使用 cat10[0] 等统一默认
      });
      if (outlierData.length > 0) {
        option.series.push(makeOutlierSeries("Outliers", outlierData, 0, 1, isHorizontal));
      }
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "whiskerMethod",
      label: "Whiskers",
      type: "discrete",
      options: [
        { value: "iqr", label: "Tukey (1.5 \xD7 IQR)" },
        { value: "minmax", label: "Min\u2013Max" }
      ],
      defaultValue: "iqr"
    },
    {
      key: "showOutliers",
      label: "Outliers",
      type: "binary",
      defaultValue: true,
      check: (ctx) => ({ applicable: ctx.chartProperties?.whiskerMethod !== "minmax" })
    },
    {
      key: "dodge",
      label: "Dodge",
      type: "discrete",
      options: [
        { value: "auto", label: "Auto" },
        { value: "local", label: "Local (compact)" },
        { value: "global", label: "Global (aligned)" }
      ],
      defaultValue: "auto",
      check: (ctx) => {
        const colorField = ctx.channelSemantics?.color?.field;
        const colorType = ctx.channelSemantics?.color?.type;
        const axisField = isDiscrete8(ctx.channelSemantics?.x?.type) ? ctx.channelSemantics?.x?.field : ctx.channelSemantics?.y?.field;
        const rows = ctx.data;
        if (!colorField || !axisField || !isDiscrete8(colorType) || !rows) {
          return { applicable: false };
        }
        const plan = planBandDodge(rows, axisField, colorField, {
          nestedSnapThreshold: ctx.chartProperties?.nestedSnapThreshold
        });
        return { applicable: plan.ambiguous, recommendedValue: plan.mode === "none" ? "auto" : plan.mode };
      }
    }
  ]
};

// src/echarts/templates/radar.ts
function niceMax(v) {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const mantissa = v / pow;
  const nice = mantissa <= 1 ? 1 : mantissa <= 2 ? 2 : mantissa <= 2.5 ? 2.5 : mantissa <= 5 ? 5 : 10;
  return nice * pow;
}
var ecRadarChartDef = {
  chart: "Radar Chart",
  template: { mark: "point", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const axisField = channelSemantics.x?.field;
    const valueField = channelSemantics.y?.field;
    const groupField = channelSemantics.color?.field;
    if (!axisField || !valueField) return;
    const metrics = extractCategories(table, axisField, channelSemantics.x?.ordinalSortOrder);
    if (metrics.length < 2) return;
    const metricMax = /* @__PURE__ */ new Map();
    for (const m of metrics) {
      const vals = table.filter((r) => String(r[axisField]) === m).map((r) => Number(r[valueField])).filter((v) => isFinite(v));
      metricMax.set(m, niceMax(vals.length > 0 ? Math.max(...vals) : 1));
    }
    const indicator = metrics.map((m) => ({
      name: m,
      max: metricMax.get(m) || 1
    }));
    const filled = chartProperties?.filled !== false;
    const fillOpacity = chartProperties?.fillOpacity ?? 0.3;
    const seriesData = [];
    const legendData = [];
    if (groupField) {
      const groups = groupBy(table, groupField);
      for (const [name, rows] of groups) {
        legendData.push(name);
        const metricVals = /* @__PURE__ */ new Map();
        for (const row of rows) {
          const m = String(row[axisField]);
          const v = Number(row[valueField]) || 0;
          if (!metricVals.has(m)) metricVals.set(m, { sum: 0, count: 0 });
          const entry = metricVals.get(m);
          entry.sum += v;
          entry.count++;
        }
        const values = metrics.map((m) => {
          const entry = metricVals.get(m);
          return entry ? Math.round(entry.sum / entry.count * 100) / 100 : 0;
        });
        seriesData.push({
          name,
          value: values,
          areaStyle: filled ? { opacity: fillOpacity } : void 0
        });
      }
    } else {
      const metricVals = /* @__PURE__ */ new Map();
      for (const row of table) {
        const m = String(row[axisField]);
        const v = Number(row[valueField]) || 0;
        if (!metricVals.has(m)) metricVals.set(m, { sum: 0, count: 0 });
        const entry = metricVals.get(m);
        entry.sum += v;
        entry.count++;
      }
      const values = metrics.map((m) => {
        const entry = metricVals.get(m);
        return entry ? Math.round(entry.sum / entry.count * 100) / 100 : 0;
      });
      seriesData.push({
        value: values,
        areaStyle: filled ? { opacity: fillOpacity } : void 0
      });
    }
    const hasLegend = legendData.length > 0;
    const { canvasW, canvasH } = computeCircumferencePressure(metrics.length, ctx.canvasSize, {
      minArcPx: 60,
      minRadius: 80,
      maxStretch: ctx.assembleOptions?.maxStretch,
      maxStretchX: ctx.assembleOptions?.maxStretchX,
      maxStretchY: ctx.assembleOptions?.maxStretchY
    });
    const chartH = canvasH + (hasLegend ? 36 : 0);
    const option = {
      tooltip: { trigger: "item" },
      radar: {
        indicator,
        shape: chartProperties?.shape === "circle" ? "circle" : "polygon",
        center: ["50%", "46%"],
        radius: "38%",
        axisName: { fontSize: ctx.layout.titleFontSize }
      },
      series: [{
        type: "radar",
        data: seriesData,
        emphasis: {
          lineStyle: { width: 3 }
        }
      }],
      _width: canvasW,
      _height: chartH
    };
    if (hasLegend) {
      option.legend = {
        data: legendData,
        bottom: 12,
        left: "center",
        orient: "horizontal"
      };
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "shape",
      label: "Grid",
      type: "discrete",
      options: [
        { value: void 0, label: "Polygon (default)" },
        { value: "circle", label: "Circle" }
      ]
    },
    {
      key: "filled",
      label: "Fill",
      type: "discrete",
      options: [
        { value: true, label: "Filled (default)" },
        { value: false, label: "Outline only" }
      ]
    },
    { key: "fillOpacity", label: "Opacity", type: "continuous", min: 0.05, max: 0.8, step: 0.05, defaultValue: 0.3 }
  ]
};

// src/echarts/templates/candlestick.ts
var isDiscrete9 = (type) => type === "nominal" || type === "ordinal";
var ecCandlestickDef = {
  chart: "Candlestick Chart",
  template: { mark: "candlestick", encoding: {} },
  channels: ["x", "open", "high", "low", "close", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({
    axisFlags: { x: { banded: true } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xCS = channelSemantics.x;
    const openCS = channelSemantics.open;
    const highCS = channelSemantics.high;
    const lowCS = channelSemantics.low;
    const closeCS = channelSemantics.close;
    if (!xCS?.field) return;
    const xField = xCS.field;
    const openField = openCS?.field;
    const highField = highCS?.field;
    const lowField = lowCS?.field;
    const closeField = closeCS?.field;
    if (!openField || !closeField) return;
    const xIsDiscrete = isDiscrete9(xCS.type);
    const xIsTemporal = xCS.type === "temporal";
    const categories = xIsDiscrete ? extractCategories(table, xField, xCS.ordinalSortOrder) : void 0;
    const candleData = [];
    const xValues = [];
    for (const row of table) {
      const o = Number(row[openField]);
      const c = Number(row[closeField]);
      const h = highField ? Number(row[highField]) : Math.max(o, c);
      const l = lowField ? Number(row[lowField]) : Math.min(o, c);
      candleData.push([o, c, l, h]);
      xValues.push(String(row[xField]));
    }
    const option = {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" }
      },
      xAxis: {
        type: xIsDiscrete ? "category" : xIsTemporal ? "category" : "category",
        data: categories || xValues,
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        boundaryGap: true,
        axisLine: { onZero: false },
        axisTick: { show: true, alignWithLabel: true }
      },
      yAxis: {
        type: "value",
        scale: true,
        // candlestick charts should never start at zero
        name: "Price",
        nameLocation: "middle",
        nameGap: 50,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: [{
        type: "candlestick",
        data: candleData,
        itemStyle: {
          color: "#06982d",
          // bullish (close > open) — green
          color0: "#ae1325",
          // bearish (close < open) — red
          borderColor: "#06982d",
          borderColor0: "#ae1325"
        }
      }]
    };
    if (chartProperties?.showMA) {
      const maWindow = chartProperties.maWindow ?? 5;
      const closePrices = table.map((r) => Number(r[closeField]));
      const maData = computeMA(closePrices, maWindow);
      option.series.push({
        name: `MA${maWindow}`,
        type: "line",
        data: maData,
        smooth: true,
        lineStyle: { width: 1.5, opacity: 0.7 },
        symbol: "none"
      });
      option.legend = { data: [`MA${maWindow}`] };
    }
    if (table.length > 60) {
      const startPercent = Math.max(0, 100 - Math.round(60 / table.length * 100));
      option.dataZoom = [
        { type: "inside", start: startPercent, end: 100 },
        { type: "slider", start: startPercent, end: 100, bottom: 5, height: 20 }
      ];
      option._dataZoomExtra = 35;
    }
    const plotWidth = ctx.canvasSize?.width || 400;
    const barWidth = Math.max(2, Math.min(20, Math.round(plotWidth * 0.6 / table.length)));
    option.series[0].barWidth = barWidth;
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  postProcess: (option) => {
    const extra = option._dataZoomExtra ?? 0;
    if (extra > 0) {
      if (!option.grid) option.grid = {};
      const curBottom = typeof option.grid.bottom === "number" ? option.grid.bottom : 45;
      option.grid.bottom = curBottom + extra;
      if (typeof option._height === "number") {
        option._height += extra;
      }
      delete option._dataZoomExtra;
    }
  },
  properties: [
    {
      key: "showMA",
      label: "Moving average",
      type: "binary",
      defaultValue: false
    },
    {
      key: "maWindow",
      label: "Average window",
      type: "continuous",
      min: 3,
      max: 30,
      step: 1,
      defaultValue: 5
    }
  ]
};
function computeMA(prices, window) {
  const result = [];
  for (let i = 0; i < prices.length; i++) {
    if (i < window - 1) {
      result.push(null);
    } else {
      let sum = 0;
      for (let j = i - window + 1; j <= i; j++) {
        sum += prices[j];
      }
      result.push(Math.round(sum / window * 100) / 100);
    }
  }
  return result;
}

// src/echarts/templates/streamgraph.ts
var ecStreamgraphDef = {
  chart: "Streamgraph",
  template: { mark: "area", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "area",
  declareLayoutMode: () => ({
    paramOverrides: { continuousMarkCrossSection: { x: 100, y: 20, seriesCountAxis: "auto" } }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const colorField = channelSemantics.color?.field;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    if (!colorField) {
      const option2 = {
        tooltip: { trigger: "axis" },
        xAxis: {
          type: xCS.type === "temporal" ? "time" : "value",
          name: xField,
          nameLocation: "middle",
          nameGap: 30,
          axisTick: { show: true }
        },
        yAxis: { type: "value", show: false, axisTick: { show: true } },
        series: [{
          type: "line",
          data: table.map((r) => [r[xField], r[yField]]),
          areaStyle: { opacity: 0.85 },
          lineStyle: { width: 0.5 },
          symbol: "none"
        }]
      };
      Object.assign(spec, option2);
      delete spec.mark;
      delete spec.encoding;
      return;
    }
    const xValSet = /* @__PURE__ */ new Set();
    const xVals = [];
    for (const row of table) {
      const xv = String(row[xField]);
      if (!xValSet.has(xv)) {
        xValSet.add(xv);
        xVals.push(xv);
      }
    }
    const groups = groupBy(table, colorField);
    const seriesNames = [...groups.keys()];
    const valMap = /* @__PURE__ */ new Map();
    for (const row of table) {
      const key = `${row[xField]}|||${row[colorField]}`;
      const v = row[yField];
      valMap.set(key, v != null && v !== "" ? Number(v) : 0);
    }
    const xIsTemporal = xCS.type === "temporal";
    const riverData = [];
    for (let i = 0; i < xVals.length; i++) {
      const xv = xVals[i];
      for (const sn of seriesNames) {
        const key = `${xv}|||${sn}`;
        const numVal = valMap.get(key);
        const value = numVal != null && Number.isFinite(numVal) ? numVal : 0;
        riverData.push([xIsTemporal ? xv : i, value, sn]);
      }
    }
    const option = {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: "rgba(0,0,0,0.2)", width: 1, type: "solid" } },
        formatter: (params) => {
          if (!params || params.length === 0) return "";
          const xVal = params[0].value[0];
          const displayX = xIsTemporal ? xVal : xVals[xVal] ?? xVal;
          let html = `<b>${displayX}</b><br/>`;
          const sortedParams = [...params].sort((a, b) => (b.value[1] || 0) - (a.value[1] || 0));
          sortedParams.forEach((p) => {
            html += `${p.marker} ${p.value[2]}: <b>${p.value[1]}</b><br/>`;
          });
          return html;
        }
      },
      legend: {
        data: seriesNames
      },
      singleAxis: {
        ...xIsTemporal ? { type: "time" } : {
          type: "value",
          min: 0,
          max: Math.max(1, xVals.length - 1),
          axisLabel: {
            fontSize: 11,
            formatter: (value) => {
              const idx = Math.round(Number(value));
              return xVals[idx] ?? value;
            }
          }
        },
        axisTick: { show: true },
        bottom: 45,
        // enough room for tick labels + axis name below
        name: xField,
        nameLocation: "middle",
        nameGap: 25,
        nameTextStyle: { fontSize: ctx.layout.titleFontSize },
        ...xIsTemporal ? { axisLabel: { fontSize: 11 } } : {}
      },
      series: [{
        type: "themeRiver",
        data: riverData,
        label: { show: false },
        emphasis: { focus: "series" },
        itemStyle: {
          borderWidth: 0.5,
          borderColor: "rgba(255,255,255,0.3)"
        }
      }]
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  postProcess: (option) => {
    if (option.singleAxis) {
      const BUFFER = 15;
      const LEGEND_GAP = 12;
      const hasLegend = !!option.legend;
      const legendWidth = option._legendWidth || 140;
      const rightMargin = hasLegend ? legendWidth + LEGEND_GAP + BUFFER : 20;
      const minW = 600 + BUFFER;
      const minH = 350 + BUFFER;
      if (typeof option._width === "number" && option._width < minW) {
        option._width = minW;
      }
      if (typeof option._height === "number" && option._height < minH) {
        option._height = minH;
      }
      if (!option._width) option._width = minW;
      if (!option._height) option._height = minH;
      option.singleAxis.left = option.singleAxis.left || 50;
      option.singleAxis.right = Math.max(option.singleAxis.right || 0, rightMargin);
      if (hasLegend && option.legend) {
        const legendLeft = option._width - rightMargin + BUFFER;
        option.legend.left = legendLeft;
        delete option.legend.right;
        option.legend.top = 20;
        option.legend.orient = option.legend.orient || "vertical";
        option.legend.align = "left";
        if (Array.isArray(option.graphic)) {
          for (const g of option.graphic) {
            if (g.type === "text" && (g.top === 4 || g.top === 20) && g.style && g.style.fontWeight === "bold") {
              g.left = legendLeft;
              delete g.right;
            }
          }
        }
      }
      if (typeof option.singleAxis.bottom === "number") {
        option.singleAxis.bottom += BUFFER;
      }
    }
  },
  properties: []
};

// src/echarts/templates/gauge.ts
var ecGaugeChartDef = {
  chart: "Gauge Chart",
  template: { mark: "point", encoding: {} },
  channels: ["size", "column"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const valueField = channelSemantics.size?.field;
    const columnField = channelSemantics.column?.field;
    if (!valueField) return;
    const allValues = table.map((r) => Number(r[valueField])).filter((v) => isFinite(v));
    const dataMax = allValues.length > 0 ? Math.max(...allValues) : 100;
    const scaleMin = chartProperties?.min ?? 0;
    const scaleMax = chartProperties?.max ?? niceGaugeMax(dataMax);
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) {
        palette = fromRegistry;
      }
    }
    if (!palette || palette.length === 0) {
      const fallbackId = (channelSemantics.column ? Math.max(1, extractCategories(table, channelSemantics.column.field, channelSemantics.column.ordinalSortOrder).length) : 1) > 10 ? "cat20" : "cat10";
      palette = getPaletteForScheme(fallbackId) ?? DEFAULT_COLORS;
    }
    const gaugeItems = [];
    if (columnField) {
      const groups = groupBy(table, columnField);
      const categories = extractCategories(
        table,
        columnField,
        channelSemantics.column?.ordinalSortOrder
      );
      categories.forEach((cat, idx) => {
        const rows = groups.get(cat) || [];
        const vals = rows.map((r) => Number(r[valueField])).filter((v) => isFinite(v));
        const avg = vals.length > 0 ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length * 100) / 100 : 0;
        gaugeItems.push({
          name: cat,
          value: avg,
          color: palette[idx % palette.length]
        });
      });
    } else {
      const avg = allValues.length > 0 ? Math.round(allValues.reduce((a, b) => a + b, 0) / allValues.length * 100) / 100 : 0;
      gaugeItems.push({ name: valueField, value: avg });
    }
    const n = gaugeItems.length;
    const baseW = ctx.canvasSize.width;
    const baseH = ctx.canvasSize.height;
    const minCellDim = 180;
    const maxStretchFactor = ctx.assembleOptions?.maxStretchX ?? ctx.assembleOptions?.maxStretch ?? 2;
    let gridCols, gridRows;
    if (n === 1) {
      gridCols = 1;
      gridRows = 1;
    } else {
      const maxCols = Math.max(
        1,
        Math.floor(baseW * maxStretchFactor / minCellDim)
      );
      if (n <= maxCols) {
        gridCols = n;
        gridRows = 1;
      } else {
        gridRows = Math.ceil(n / maxCols);
        gridCols = Math.ceil(n / gridRows);
      }
    }
    const canvasW = Math.max(baseW, gridCols * minCellDim);
    const canvasH = Math.max(baseH, gridRows * (minCellDim + 20));
    const cellW = canvasW / gridCols;
    const cellH = canvasH / gridRows;
    const gaugeRadius = Math.max(
      40,
      Math.round(Math.min(cellW * 0.38, cellH * 0.38))
    );
    const s = gaugeRadius / 100;
    const progressWidth = Math.max(4, Math.round(12 * s));
    const pointerWidth = Math.max(2, Math.round(5 * s));
    const detailFontSize = Math.max(10, Math.round(20 * s));
    const titleFontSize = Math.max(8, Math.round(14 * s));
    const axisLabelFontSize = Math.max(6, Math.round(9 * s));
    const tickLength = Math.max(3, Math.round(5 * s));
    const tickDistance = -Math.round(16 * s);
    const splitLength = Math.max(5, Math.round(12 * s));
    const splitDistance = -Math.round(20 * s);
    const labelDistance = -Math.round(24 * s);
    const showProgress = chartProperties?.showProgress !== false;
    const series = gaugeItems.map((item, i) => {
      const col = i % gridCols;
      const row = Math.floor(i / gridCols);
      const cx = Math.round((col + 0.5) * cellW);
      const cy = Math.round((row + 0.5) * cellH);
      return {
        type: "gauge",
        min: scaleMin,
        max: scaleMax,
        center: [`${cx}px`, `${cy}px`],
        radius: `${gaugeRadius}px`,
        data: [{
          name: item.name,
          value: item.value,
          ...item.color ? { itemStyle: { color: item.color } } : {}
        }],
        detail: {
          formatter: "{value}",
          fontSize: detailFontSize,
          offsetCenter: [0, "70%"]
        },
        title: {
          fontSize: titleFontSize,
          offsetCenter: [0, "85%"]
        },
        axisLine: {
          lineStyle: { width: progressWidth }
        },
        progress: {
          show: showProgress,
          width: progressWidth,
          ...item.color ? { itemStyle: { color: item.color } } : {}
        },
        pointer: {
          length: "60%",
          width: pointerWidth,
          ...item.color ? { itemStyle: { color: item.color } } : {}
        },
        axisTick: {
          distance: tickDistance,
          length: tickLength,
          lineStyle: { color: "#999", width: 1 }
        },
        splitLine: {
          distance: splitDistance,
          length: splitLength,
          lineStyle: { color: "#999", width: 2 }
        },
        axisLabel: {
          distance: labelDistance,
          fontSize: axisLabelFontSize,
          color: "#666"
        }
      };
    });
    const option = {
      tooltip: { trigger: "item", formatter: "{b}: {c}" },
      series,
      color: DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "min", label: "Min", type: "continuous", min: 0, max: 1e3, step: 10, defaultValue: 0 },
    { key: "max", label: "Max", type: "continuous", min: 0, max: 1e4, step: 100, defaultValue: 100 },
    {
      key: "showProgress",
      label: "Progress",
      type: "discrete",
      options: [
        { value: true, label: "Show (default)" },
        { value: false, label: "Hide" }
      ]
    }
  ]
};
function niceGaugeMax(v) {
  if (v <= 0) return 100;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const mantissa = v / pow;
  const nice = mantissa <= 1 ? 1 : mantissa <= 2 ? 2 : mantissa <= 5 ? 5 : 10;
  return nice * pow;
}

// src/echarts/templates/funnel.ts
var ecFunnelChartDef = {
  chart: "Funnel Chart",
  template: { mark: "rect", encoding: {} },
  channels: ["y", "size"],
  markCognitiveChannel: "area",
  declareLayoutMode: () => ({
    axisFlags: { y: { banded: true } },
    paramOverrides: {
      defaultBandSize: 50
      // taller bands for funnel stages
    }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, layout, colorDecisions } = ctx;
    const stageField = channelSemantics.y?.field;
    const valField = channelSemantics.size?.field;
    if (!stageField) return;
    const stages = extractCategories(
      table,
      stageField,
      channelSemantics.y?.ordinalSortOrder
    );
    if (stages.length === 0) return;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) {
        palette = fromRegistry;
      }
    }
    if (!palette || palette.length === 0) {
      const catCount = stages.length;
      const fallbackId = catCount > 10 ? "cat20" : "cat10";
      palette = getPaletteForScheme(fallbackId) ?? DEFAULT_COLORS;
    }
    const funnelData = [];
    if (valField) {
      const agg = /* @__PURE__ */ new Map();
      for (const row of table) {
        const stage = String(row[stageField] ?? "");
        const val = Number(row[valField]) || 0;
        agg.set(stage, (agg.get(stage) ?? 0) + val);
      }
      for (const stage of stages) {
        funnelData.push({ name: stage, value: agg.get(stage) ?? 0 });
      }
    } else {
      const counts = /* @__PURE__ */ new Map();
      for (const row of table) {
        const stage = String(row[stageField] ?? "");
        counts.set(stage, (counts.get(stage) ?? 0) + 1);
      }
      for (const stage of stages) {
        funnelData.push({ name: stage, value: counts.get(stage) ?? 0 });
      }
    }
    const sortOrder = chartProperties?.sort ?? "descending";
    if (sortOrder === "descending") {
      funnelData.sort((a, b) => b.value - a.value);
    } else if (sortOrder === "ascending") {
      funnelData.sort((a, b) => a.value - b.value);
    }
    const stageCount = layout.yNominalCount || stages.length;
    const yStep = layout.yStep;
    const funnelBodyH = Math.max(120, yStep * stageCount);
    const topMargin = 30;
    const bottomMargin = 20;
    const canvasH = funnelBodyH + topMargin + bottomMargin;
    const maxLabelLen = Math.max(...funnelData.map((d) => d.name.length), 3);
    const estimatedLegendWidth = Math.min(150, maxLabelLen * 7 + 30);
    const canvasW = Math.max(ctx.canvasSize.width, 300);
    const funnelLeft = 40;
    const funnelRight = estimatedLegendWidth + 30;
    const funnelWidth = `${Math.max(100, canvasW - funnelLeft - funnelRight)}px`;
    const orient = chartProperties?.orient ?? "vertical";
    const option = {
      tooltip: {
        trigger: "item",
        formatter: "{b}: {c} ({d}%)"
      },
      legend: {
        data: funnelData.map((d) => d.name),
        type: funnelData.length > 8 ? "scroll" : "plain",
        orient: "vertical",
        right: 10,
        top: "middle",
        textStyle: { fontSize: layout.legendFontSize }
      },
      series: [{
        type: "funnel",
        left: funnelLeft,
        top: topMargin,
        bottom: bottomMargin,
        width: funnelWidth,
        sort: sortOrder,
        orient,
        gap: chartProperties?.gap ?? 2,
        data: funnelData.map((d, idx) => ({
          ...d,
          itemStyle: {
            ...palette ? { color: palette[idx % palette.length] } : {}
          }
        })),
        label: {
          show: true,
          position: "inside",
          formatter: "{b}\n{c}",
          fontSize: 11
        },
        emphasis: {
          label: {
            fontSize: 13
          }
        },
        itemStyle: {
          borderColor: "#fff",
          borderWidth: 1
        }
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "sort",
      label: "Sort",
      type: "discrete",
      options: [
        { value: "descending", label: "Descending (default)" },
        { value: "ascending", label: "Ascending" },
        { value: "none", label: "Original order" }
      ]
    },
    {
      key: "orient",
      label: "Orient",
      type: "discrete",
      options: [
        { value: "vertical", label: "Vertical (default)" },
        { value: "horizontal", label: "Horizontal" }
      ]
    },
    { key: "gap", label: "Gap", type: "continuous", min: 0, max: 20, step: 1, defaultValue: 2 }
  ]
};

// src/echarts/templates/treemap.ts
var ecTreemapDef = {
  chart: "Treemap",
  template: { mark: "rect", encoding: {} },
  channels: ["color", "size", "detail"],
  markCognitiveChannel: "area",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const catField = channelSemantics.color?.field;
    const valField = channelSemantics.size?.field;
    const subCatField = channelSemantics.detail?.field;
    if (!catField) return;
    const categories = extractCategories(table, catField, channelSemantics.color?.ordinalSortOrder);
    if (categories.length === 0) return;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) {
        palette = fromRegistry;
      }
    }
    if (!palette || palette.length === 0) {
      const catCount = categories.length;
      const fallbackId = catCount > 10 ? "cat20" : "cat10";
      palette = getPaletteForScheme(fallbackId) ?? DEFAULT_COLORS;
    }
    let treemapData;
    if (subCatField) {
      treemapData = categories.map((cat, catIdx) => {
        const catRows = table.filter((r) => String(r[catField]) === cat);
        const subCats = extractCategories(catRows, subCatField);
        const children = subCats.map((sub) => {
          const subRows = catRows.filter((r) => String(r[subCatField]) === sub);
          let value;
          if (valField) {
            value = subRows.reduce((sum, r) => sum + (Number(r[valField]) || 0), 0);
          } else {
            value = subRows.length;
          }
          return { name: sub, value };
        });
        return {
          name: cat,
          children,
          itemStyle: { color: palette[catIdx % palette.length] }
        };
      });
    } else {
      const agg = /* @__PURE__ */ new Map();
      if (valField) {
        for (const row of table) {
          const cat = String(row[catField] ?? "");
          const val = Number(row[valField]) || 0;
          agg.set(cat, (agg.get(cat) ?? 0) + val);
        }
      } else {
        for (const row of table) {
          const cat = String(row[catField] ?? "");
          agg.set(cat, (agg.get(cat) ?? 0) + 1);
        }
      }
      treemapData = categories.map((cat, i) => ({
        name: cat,
        value: agg.get(cat) ?? 0,
        itemStyle: { color: palette[i % palette.length] }
      }));
    }
    const leafValues = treemapData.flatMap(
      (d) => d.children ? d.children.map((c) => c.value) : [d.value]
    ).filter((v) => v > 0);
    const effectiveCount = leafValues.length > 0 ? computeEffectiveBarCount(leafValues) : categories.length;
    const baseW = ctx.canvasSize.width;
    const baseH = ctx.canvasSize.height;
    const minBarPx = 30;
    const elasticity = 0.5;
    const maxStretch = ctx.assembleOptions?.maxStretch ?? 2;
    const maxStretchX = ctx.assembleOptions?.maxStretchX ?? maxStretch;
    const maxStretchY = ctx.assembleOptions?.maxStretchY ?? maxStretch;
    const xBias = 1.5;
    const pressure = effectiveCount * minBarPx / baseW;
    const areaStretch = pressure <= 1 ? 1 : Math.min(maxStretchX * maxStretchY, Math.pow(pressure, elasticity));
    const stretchX = Math.min(maxStretchX, Math.pow(areaStretch, xBias / (xBias + 1)));
    const stretchY = Math.min(maxStretchY, Math.pow(areaStretch, 1 / (xBias + 1)));
    const canvasW = Math.round(baseW * stretchX);
    const canvasH = Math.round(baseH * stretchY);
    const showBreadcrumb = chartProperties?.breadcrumb !== false;
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const { name, value, treePathInfo } = params;
          const path = treePathInfo ? treePathInfo.map((n) => n.name).filter(Boolean).join(" \u2192 ") : name;
          return `${path}<br/>Value: ${value}`;
        }
      },
      series: [{
        type: "treemap",
        data: treemapData,
        width: "90%",
        height: showBreadcrumb ? "80%" : "90%",
        top: 10,
        left: "center",
        roam: false,
        leafDepth: subCatField ? 2 : 1,
        breadcrumb: {
          show: showBreadcrumb,
          bottom: 5
        },
        label: {
          show: true,
          formatter: "{b}",
          fontSize: 12
        },
        upperLabel: subCatField ? {
          show: true,
          height: 20,
          fontSize: 11,
          color: "#fff"
        } : void 0,
        levels: subCatField ? [
          {
            // Root level (hidden)
            itemStyle: { borderWidth: 0, gapWidth: 2 }
          },
          {
            // Top-level categories
            itemStyle: {
              borderWidth: 2,
              borderColor: "#fff",
              gapWidth: 2
            },
            upperLabel: { show: true }
          },
          {
            // Leaf level (sub-categories)
            itemStyle: {
              borderWidth: 1,
              borderColor: "rgba(255,255,255,0.5)",
              gapWidth: 1
            },
            label: { show: true, fontSize: 10 },
            colorSaturation: [0.3, 0.6],
            colorMappingBy: "value"
          }
        ] : [
          {
            itemStyle: {
              borderWidth: 2,
              borderColor: "#fff",
              gapWidth: 2
            }
          }
        ]
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "breadcrumb",
      label: "Breadcrumb",
      type: "discrete",
      options: [
        { value: true, label: "Show (default)" },
        { value: false, label: "Hide" }
      ]
    }
  ]
};

// src/echarts/templates/sunburst.ts
function collectSunburstLeafValues(nodes) {
  return nodes.flatMap((d) => {
    if (d.children?.length) {
      return collectSunburstLeafValues(d.children);
    }
    return [Number(d.value) || 0];
  });
}
var SUNBURST_OPACITY_L1 = 1;
var SUNBURST_OPACITY_L2 = 0.8;
var SUNBURST_OPACITY_L3 = 0.6;
var SUNBURST_OUTER_LABEL_MIN_ANGLE_DEG = 3;
var SUNBURST_CANVAS_SIZE_MULTIPLIER = 1.55;
function hexToRgb2(hex) {
  const s = hex.trim();
  let m = /^#?([0-9a-f]{6})$/i.exec(s);
  if (m) {
    const intVal = parseInt(m[1], 16);
    return { r: intVal >> 16 & 255, g: intVal >> 8 & 255, b: intVal & 255 };
  }
  m = /^#?([0-9a-f]{3})$/i.exec(s);
  if (m) {
    const x = m[1];
    const full = x.split("").map((c) => c + c).join("");
    const intVal = parseInt(full, 16);
    return { r: intVal >> 16 & 255, g: intVal >> 8 & 255, b: intVal & 255 };
  }
  return null;
}
function sunburstColorWithOpacity(baseColor, alpha) {
  const rgb = hexToRgb2(baseColor);
  if (rgb) {
    return `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
  }
  const rgbaM = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(baseColor.trim());
  if (rgbaM) {
    return `rgba(${rgbaM[1]},${rgbaM[2]},${rgbaM[3]},${alpha})`;
  }
  return baseColor;
}
var ecSunburstDef = {
  chart: "Sunburst Chart",
  template: { mark: "arc", encoding: {} },
  channels: ["color", "size", "detail", "group"],
  markCognitiveChannel: "area",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const catField = channelSemantics.color?.field;
    const valField = channelSemantics.size?.field;
    const middleField = channelSemantics.group?.field;
    const leafField = channelSemantics.detail?.field;
    if (!catField) return;
    const categories = extractCategories(table, catField, channelSemantics.color?.ordinalSortOrder);
    if (categories.length === 0) return;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) {
        palette = fromRegistry;
      }
    }
    if (!palette || palette.length === 0) {
      const catCount = categories.length;
      const fallbackId = catCount > 10 ? "cat20" : "cat10";
      palette = getPaletteForScheme(fallbackId) ?? DEFAULT_COLORS;
    }
    let sunburstData;
    if (middleField && leafField) {
      sunburstData = categories.map((cat, catIdx) => {
        const base = palette[catIdx % palette.length];
        const catRows = table.filter((r) => String(r[catField]) === cat);
        const subCats = extractCategories(catRows, middleField);
        const children = subCats.map((sub) => {
          const subRows = catRows.filter((r) => String(r[middleField]) === sub);
          const leaves = extractCategories(subRows, leafField);
          const grandchildren = leaves.map((leaf) => {
            const leafRows = subRows.filter((r) => String(r[leafField]) === leaf);
            let value;
            if (valField) {
              value = leafRows.reduce((sum, r) => sum + (Number(r[valField]) || 0), 0);
            } else {
              value = leafRows.length;
            }
            return {
              name: leaf,
              value,
              itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L3) }
            };
          });
          return {
            name: sub,
            children: grandchildren,
            itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L2) }
          };
        });
        return {
          name: cat,
          children,
          itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L1) }
        };
      });
    } else if (middleField) {
      sunburstData = categories.map((cat, catIdx) => {
        const base = palette[catIdx % palette.length];
        const catRows = table.filter((r) => String(r[catField]) === cat);
        const subCats = extractCategories(catRows, middleField);
        const children = subCats.map((sub) => {
          const subRows = catRows.filter((r) => String(r[middleField]) === sub);
          let value;
          if (valField) {
            value = subRows.reduce((sum, r) => sum + (Number(r[valField]) || 0), 0);
          } else {
            value = subRows.length;
          }
          return {
            name: sub,
            value,
            itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L2) }
          };
        });
        return {
          name: cat,
          children,
          itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L1) }
        };
      });
    } else if (leafField) {
      sunburstData = categories.map((cat, catIdx) => {
        const base = palette[catIdx % palette.length];
        const catRows = table.filter((r) => String(r[catField]) === cat);
        const subCats = extractCategories(catRows, leafField);
        const children = subCats.map((sub) => {
          const subRows = catRows.filter((r) => String(r[leafField]) === sub);
          let value;
          if (valField) {
            value = subRows.reduce((sum, r) => sum + (Number(r[valField]) || 0), 0);
          } else {
            value = subRows.length;
          }
          return {
            name: sub,
            value,
            itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L2) }
          };
        });
        return {
          name: cat,
          children,
          itemStyle: { color: sunburstColorWithOpacity(base, SUNBURST_OPACITY_L1) }
        };
      });
    } else {
      const agg = /* @__PURE__ */ new Map();
      if (valField) {
        for (const row of table) {
          const cat = String(row[catField] ?? "");
          const val = Number(row[valField]) || 0;
          agg.set(cat, (agg.get(cat) ?? 0) + val);
        }
      } else {
        for (const row of table) {
          const cat = String(row[catField] ?? "");
          agg.set(cat, (agg.get(cat) ?? 0) + 1);
        }
      }
      sunburstData = categories.map((cat, i) => ({
        name: cat,
        value: agg.get(cat) ?? 0,
        itemStyle: { color: palette[i % palette.length] }
      }));
    }
    let outerValues;
    if (middleField || leafField) {
      outerValues = collectSunburstLeafValues(sunburstData);
    } else {
      outerValues = sunburstData.map((d) => d.value);
    }
    const effectiveCount = computeEffectiveBarCount(outerValues);
    const sunburstCanvas = {
      width: Math.round(ctx.canvasSize.width * SUNBURST_CANVAS_SIZE_MULTIPLIER),
      height: Math.round(ctx.canvasSize.height * SUNBURST_CANVAS_SIZE_MULTIPLIER)
    };
    const { radius: pressureRadius, canvasW, canvasH } = computeCircumferencePressure(effectiveCount, sunburstCanvas, {
      minArcPx: 45,
      minRadius: Math.round(80 * SUNBURST_CANVAS_SIZE_MULTIPLIER),
      maxRadius: Math.round(400 * SUNBURST_CANVAS_SIZE_MULTIPLIER),
      maxStretch: ctx.assembleOptions?.maxStretch,
      maxStretchX: ctx.assembleOptions?.maxStretchX,
      maxStretchY: ctx.assembleOptions?.maxStretchY
    });
    const minOuterR = Math.round(80 * SUNBURST_CANVAS_SIZE_MULTIPLIER);
    const outerRadius = Math.max(
      minOuterR,
      Math.round(Math.min(pressureRadius, Math.min(canvasW, canvasH) / 2 - 20))
    );
    const innerRadius = chartProperties?.innerRadius ?? Math.round(outerRadius * 0.15);
    const span = outerRadius - innerRadius;
    const ringThird1 = innerRadius + Math.round(span / 3);
    const ringThird2 = innerRadius + Math.round(2 * span / 3);
    const ringHalf = Math.round(innerRadius + span * 0.5);
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const { name, value, treePathInfo } = params;
          const path = treePathInfo ? treePathInfo.map((n) => n.name).filter(Boolean).join(" \u2192 ") : name;
          return `${path}<br/>Value: ${value}`;
        }
      },
      series: [{
        type: "sunburst",
        data: sunburstData,
        radius: [`${innerRadius}px`, `${outerRadius}px`],
        center: ["50%", "50%"],
        label: {
          show: true,
          rotate: chartProperties?.labelRotate ?? "radial",
          fontSize: 11,
          color: "#000000"
        },
        emphasis: {
          focus: "ancestor",
          label: { color: "#000000" }
        },
        levels: middleField && leafField ? [
          {},
          {
            r0: `${innerRadius}px`,
            r: `${ringThird1}px`,
            label: { fontSize: 11, fontWeight: "bold", color: "#000000" },
            itemStyle: { borderWidth: 2, borderColor: "#fff" }
          },
          {
            r0: `${ringThird1}px`,
            r: `${ringThird2}px`,
            label: { fontSize: 10, color: "#000000" },
            itemStyle: { borderWidth: 1, borderColor: "rgba(255,255,255,0.55)" }
          },
          {
            r0: `${ringThird2}px`,
            r: `${outerRadius}px`,
            label: {
              fontSize: 9,
              color: "#000000",
              minAngle: SUNBURST_OUTER_LABEL_MIN_ANGLE_DEG
            },
            itemStyle: { borderWidth: 1, borderColor: "rgba(255,255,255,0.35)" }
          }
        ] : middleField || leafField ? [
          {},
          // root
          {
            // Inner ring (top-level categories)
            r0: `${innerRadius}px`,
            r: `${ringHalf}px`,
            label: { fontSize: 12, fontWeight: "bold", color: "#000000" },
            itemStyle: { borderWidth: 2, borderColor: "#fff" }
          },
          {
            // Outer ring (sub-categories)
            r0: `${ringHalf}px`,
            r: `${outerRadius}px`,
            label: {
              fontSize: 10,
              color: "#000000",
              minAngle: SUNBURST_OUTER_LABEL_MIN_ANGLE_DEG
            },
            itemStyle: { borderWidth: 1, borderColor: "rgba(255,255,255,0.5)" }
          }
        ] : [
          {},
          // root
          {
            label: {
              fontSize: 12,
              color: "#000000",
              minAngle: SUNBURST_OUTER_LABEL_MIN_ANGLE_DEG
            },
            itemStyle: { borderWidth: 2, borderColor: "#fff" }
          }
        ]
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "innerRadius", label: "Inner R", type: "continuous", min: 0, max: 80, step: 5, defaultValue: 0 },
    {
      key: "labelRotate",
      label: "Labels",
      type: "discrete",
      options: [
        { value: "radial", label: "Radial (default)" },
        { value: "tangential", label: "Tangential" },
        { value: 0, label: "Horizontal" }
      ]
    }
  ]
};

// src/echarts/templates/sankey.ts
var ecSankeyDef = {
  chart: "Sankey Diagram",
  template: { mark: "rect", encoding: {} },
  channels: ["x", "y", "size"],
  markCognitiveChannel: "area",
  declareLayoutMode: () => ({
    axisFlags: {
      x: { banded: true },
      y: { banded: true }
    },
    paramOverrides: {
      // Each node block needs generous space:
      // x-step covers node width (~20px) + edge routing gap (~60px)
      // y-step covers node height + nodeGap
      defaultBandSize: 60
    }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, layout, colorDecisions } = ctx;
    const sourceField = channelSemantics.x?.field;
    const targetField = channelSemantics.y?.field;
    const valueField = channelSemantics.size?.field;
    if (!sourceField || !targetField) return;
    const linkAgg = /* @__PURE__ */ new Map();
    for (const row of table) {
      const src = String(row[sourceField] ?? "");
      const tgt = String(row[targetField] ?? "");
      if (!src || !tgt || src === tgt) continue;
      const key = `${src}\0${tgt}`;
      const val = valueField ? Number(row[valueField]) || 0 : 1;
      linkAgg.set(key, (linkAgg.get(key) ?? 0) + val);
    }
    const nodeSet = /* @__PURE__ */ new Set();
    const links = [];
    for (const [key, value] of linkAgg) {
      const [source, target] = key.split("\0");
      nodeSet.add(source);
      nodeSet.add(target);
      links.push({ source, target, value });
    }
    if (links.length === 0) return;
    const nodeArr = [...nodeSet];
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) {
        palette = fromRegistry;
      }
    }
    if (!palette || palette.length === 0) {
      const catCount = nodeArr.length;
      const fallbackId = catCount > 10 ? "cat20" : "cat10";
      palette = getPaletteForScheme(fallbackId) ?? DEFAULT_COLORS;
    }
    const nodes = nodeArr.map((name, i) => ({
      name,
      itemStyle: { color: palette[i % palette.length] }
    }));
    const sourceCount = layout.xNominalCount || new Set(table.map((r) => String(r[sourceField]))).size;
    const targetCount = layout.yNominalCount || new Set(table.map((r) => String(r[targetField]))).size;
    const nodeGap = chartProperties?.nodeGap ?? 10;
    const nodeWidth = chartProperties?.nodeWidth ?? 20;
    const layerEstimate = 2;
    const canvasW = Math.max(
      300,
      layout.xStep * Math.max(sourceCount, layerEstimate) + 60
    );
    const maxNodesPerColumn = Math.max(sourceCount, targetCount);
    const canvasH = Math.max(
      250,
      layout.yStep * maxNodesPerColumn
    );
    const orient = chartProperties?.orient ?? "horizontal";
    const margin = 60;
    const option = {
      tooltip: {
        trigger: "item",
        triggerOn: "mousemove",
        formatter: (params) => {
          if (params.dataType === "edge") {
            return `${params.data.source} \u2192 ${params.data.target}<br/>Value: ${params.data.value}`;
          }
          return params.name;
        }
      },
      series: [{
        type: "sankey",
        data: nodes,
        links,
        orient,
        emphasis: {
          focus: "adjacency"
        },
        lineStyle: {
          color: "gradient",
          curveness: 0.5
        },
        nodeWidth,
        nodeGap,
        label: {
          show: true,
          fontSize: 11
        },
        left: margin,
        right: margin,
        top: 20,
        bottom: 20
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "orient",
      label: "Orient",
      type: "discrete",
      options: [
        { value: "horizontal", label: "Horizontal (default)" },
        { value: "vertical", label: "Vertical" }
      ]
    },
    { key: "nodeWidth", label: "Node Width", type: "continuous", min: 5, max: 40, step: 5, defaultValue: 20 },
    { key: "nodeGap", label: "Node Gap", type: "continuous", min: 2, max: 30, step: 2, defaultValue: 10 }
  ]
};

// src/echarts/templates/lollipop.ts
var STEM_COLOR = "#000000";
var STEM_WIDTH_PX = 1.5;
var DOT_SIZE_BASE = 10;
function areCategoriesNumeric5(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var ecLollipopChartDef = {
  chart: "Lollipop Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: (cs, table) => {
    const result = detectBandedAxisFromSemantics(cs, table, { preferAxis: "x" });
    return {
      axisFlags: result ? { [result.axis]: { banded: true } } : { x: { banded: true } },
      resolvedTypes: result?.resolvedTypes
    };
  },
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const { categoryAxis, valueAxis } = detectAxes(channelSemantics);
    const catField = channelSemantics[categoryAxis]?.field;
    const valField = channelSemantics[valueAxis]?.field;
    if (!catField || !valField) return;
    const catCS = channelSemantics[categoryAxis];
    const colorField = channelSemantics.color?.field;
    const categories = extractCategories(table, catField, getCategoryOrder(ctx, categoryAxis) ?? catCS?.ordinalSortOrder);
    const valueMap = /* @__PURE__ */ new Map();
    for (const row of table) {
      const cat = String(row[catField] ?? "");
      const val = row[valField];
      if (val != null && !isNaN(val)) {
        valueMap.set(cat, (valueMap.get(cat) ?? 0) + Number(val));
      }
    }
    const values = categories.map((cat) => valueMap.get(cat) ?? null);
    const isHorizontal = categoryAxis === "y";
    const dotSizeConfig = chartProperties?.dotSize ?? 80;
    const symbolSizePx = Math.max(6, Math.min(DOT_SIZE_BASE + (dotSizeConfig - 80) / 40, 16));
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: isHorizontal ? {
        type: "value",
        name: valField,
        nameLocation: "middle",
        nameGap: 30,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      } : {
        type: "category",
        data: categories,
        name: catField,
        nameLocation: "middle",
        nameGap: 30,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: areCategoriesNumeric5(categories) ? 0 : 90 }
      },
      yAxis: isHorizontal ? {
        type: "category",
        data: categories,
        name: catField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      } : {
        type: "value",
        name: valField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true },
        axisLabel: { rotate: 0 }
      },
      series: [
        {
          type: "bar",
          data: values,
          barWidth: STEM_WIDTH_PX,
          itemStyle: { color: STEM_COLOR }
        }
      ]
    };
    option.tooltip = option.tooltip ?? {};
    option._encodingTooltip = {
      trigger: "axis",
      categoryLabel: catField,
      valueLabel: valField,
      // 由 buildEncodingTooltipFormatter 只保留 seriesType === 'scatter' 的条目
      filterScatterOnly: true
    };
    if (colorField) {
      const groups = groupBy(table, colorField);
      const colorOrder = getCategoryOrder(ctx, "color");
      const legendKeys = colorOrder && colorOrder.length > 0 ? colorOrder.filter((k) => groups.has(k)) : [...groups.keys()];
      if (legendKeys.length > 0) {
        option.legend = { data: legendKeys };
        option._legendTitle = colorField;
      }
      for (const name of legendKeys) {
        const rows = groups.get(name) ?? [];
        const scatterData = rows.filter((r) => {
          const v = r[valField];
          return v != null && !isNaN(Number(v));
        }).map((r) => {
          const cat = String(r[catField] ?? "");
          const v = Number(r[valField]);
          return isHorizontal ? [v, cat] : [cat, v];
        });
        option.series.push({
          name,
          type: "scatter",
          data: scatterData,
          symbolSize: symbolSizePx,
          itemStyle: { borderColor: "#fff", borderWidth: 1 },
          z: 2
        });
      }
    } else {
      option.series.push({
        type: "scatter",
        data: categories.map((cat, i) => {
          const v = values[i];
          return isHorizontal ? [v, cat] : [cat, v];
        }),
        symbolSize: symbolSizePx,
        itemStyle: { color: DEFAULT_COLORS[0], borderColor: "#fff", borderWidth: 1 },
        z: 2
      });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "dotSize", label: "Dot Size", type: "continuous", min: 20, max: 300, step: 10, defaultValue: 80 }
  ],
  encodingActions: [makeSortAction()]
};

// src/echarts/templates/jitter.ts
var isDiscrete10 = (type) => type === "nominal" || type === "ordinal";
function areCategoriesNumeric6(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
function jitter(seed) {
  let s = seed;
  return () => {
    s = s * 1103515245 + 12345 & 2147483647;
    return s / 2147483647 * 2 - 1;
  };
}
var ecStripPlotDef = {
  chart: "Strip Plot",
  template: { mark: "circle", encoding: {} },
  channels: ["x", "y", "color", "size", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({
    paramOverrides: { defaultBandSize: 50, minStep: 16 }
  }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const xField = xCS?.field;
    const yField = yCS?.field;
    const colorField = channelSemantics.color?.field;
    const colorType = channelSemantics.color?.type;
    const isContinuousColor = !!colorField && (colorType === "quantitative" || colorType === "temporal");
    const isTemporalColor = colorType === "temporal";
    if (!xField || !yField) return;
    const xIsDiscrete = isDiscrete10(xCS?.type);
    const yIsDiscrete = isDiscrete10(yCS?.type);
    const catAxis = xIsDiscrete ? "x" : yIsDiscrete ? "y" : "x";
    const contAxis = catAxis === "x" ? "y" : "x";
    const catField = catAxis === "x" ? xField : yField;
    const contField = contAxis === "x" ? xField : yField;
    const categories = extractCategories(table, catField, (catAxis === "x" ? xCS : yCS)?.ordinalSortOrder);
    const catToIndex = new Map(categories.map((c, i) => [c, i]));
    const jitterHalfWidth = 0.3;
    const rand = jitter(42);
    const nCat = categories.length;
    const isHorizontal = catAxis === "y";
    const catAxisLabel = {
      rotate: isHorizontal ? 0 : areCategoriesNumeric6(categories) ? 0 : 45
    };
    const valueAxisCommon = (name) => ({
      type: "value",
      name,
      axisTick: { show: true },
      axisLabel: { rotate: 0 },
      axisLine: { onZero: false }
    });
    const catAxisIdx = isHorizontal ? "yAxis" : "xAxis";
    const valAxisIdx = isHorizontal ? "xAxis" : "yAxis";
    const option = {
      tooltip: { trigger: "item" },
      [catAxisIdx]: [
        {
          type: "category",
          data: categories,
          name: catField,
          boundaryGap: true,
          axisTick: { show: true, alignWithLabel: true },
          axisLabel: catAxisLabel
        },
        {
          // Hidden value axis aligned with the category axis for scatter jitter.
          type: "value",
          min: -0.5,
          max: nCat - 0.5,
          show: false
        }
      ],
      [valAxisIdx]: valueAxisCommon(contField),
      series: []
    };
    const catScatterAxisIndex = 1;
    const toColorVal = (value) => {
      if (value == null) return NaN;
      return isTemporalColor ? new Date(value).getTime() : Number(value);
    };
    const buildPoint = (row) => {
      const cat = String(row[catField] ?? "");
      const idx = catToIndex.get(cat) ?? 0;
      const offset = rand() * jitterHalfWidth;
      const catVal = idx + offset;
      const contVal = row[contField];
      const point = catAxis === "x" ? [catVal, contVal] : [contVal, catVal];
      if (isContinuousColor && colorField) {
        point.push(toColorVal(row[colorField]));
      }
      return point;
    };
    const scatterAxisRef = isHorizontal ? { yAxisIndex: catScatterAxisIndex } : { xAxisIndex: catScatterAxisIndex };
    if (isContinuousColor && colorField) {
      const colorVals = table.map((row) => toColorVal(row[colorField])).filter((value) => Number.isFinite(value));
      const colorMin = colorVals.length ? Math.min(...colorVals) : 0;
      const colorMax = colorVals.length ? Math.max(...colorVals) : 1;
      const palette = pickEChartsPalette(colorDecisions?.color ?? colorDecisions?.group);
      option.visualMap = {
        type: "continuous",
        min: colorMin,
        max: colorMax,
        dimension: 2,
        inRange: { color: palette },
        orient: "vertical",
        right: 10,
        top: "12%",
        bottom: "12%",
        name: colorField
      };
      option._visualMapWidth = 70;
      option.graphic = [{
        type: "text",
        right: 10,
        top: 4,
        z: 100,
        style: {
          text: colorField,
          fontSize: 11,
          fontWeight: "bold",
          fill: "#333",
          textAlign: "right"
        }
      }];
      option.series.push({
        name: colorField,
        type: "scatter",
        ...scatterAxisRef,
        data: table.map(buildPoint),
        itemStyle: { opacity: 0.7 },
        symbolSize: 8
      });
    } else if (colorField && isDiscrete10(colorType)) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      for (const [name, rows] of groups) {
        option.series.push({
          name,
          type: "scatter",
          ...scatterAxisRef,
          data: rows.map(buildPoint),
          itemStyle: { opacity: 0.7 },
          symbolSize: 8
        });
      }
    } else {
      option.series.push({
        type: "scatter",
        ...scatterAxisRef,
        data: table.map(buildPoint),
        itemStyle: { opacity: 0.7 },
        symbolSize: 8
      });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  pivot: makeCartesianPivot({
    // θ (Strip → Scatter) declared centrally in core/chart-transitions.ts.
  })
};

// src/chart-types/waterfall.ts
function waterfallLastReconciles(values) {
  if (values.length < 2) return false;
  let cumPrev = 0;
  for (let i = 0; i < values.length - 1; i++) {
    if (!Number.isFinite(values[i])) return false;
    cumPrev += values[i];
  }
  const last = values[values.length - 1];
  if (!Number.isFinite(last)) return false;
  const tol = Math.max(1e-6, 5e-3 * Math.abs(cumPrev));
  return Math.abs(last - cumPrev) <= tol;
}
function recommendedTotalsMode(values) {
  return waterfallLastReconciles(values) ? "both" : "first";
}
function resolveTotalsMode(values, explicit) {
  if (explicit === "none" || explicit === "first" || explicit === "last" || explicit === "both") {
    return explicit;
  }
  return recommendedTotalsMode(values);
}

// src/echarts/templates/waterfall.ts
function areCategoriesNumeric7(cats) {
  if (cats.length === 0) return true;
  return cats.every((c) => {
    const s = String(c).trim();
    if (s === "") return false;
    const n = Number(s);
    return !isNaN(n) && isFinite(n);
  });
}
var ecWaterfallChartDef = {
  chart: "Waterfall Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "color", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: () => ({ axisFlags: { x: { banded: true } } }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xField = channelSemantics.x?.field || "Category";
    const yField = channelSemantics.y?.field || "Amount";
    const colorField = channelSemantics.color?.field;
    const categories = extractCategories(table, xField, void 0);
    const rows = categories.map((cat) => table.find((r) => String(r[xField]) === cat)).filter(Boolean);
    const values = rows.map((r) => Number(r[yField]) || 0);
    const hasTypeCol = !!colorField;
    const totalsMode = resolveTotalsMode(values, ctx.chartProperties?.totals);
    const wantFirst = totalsMode === "first" || totalsMode === "both";
    const wantLast = totalsMode === "last" || totalsMode === "both";
    const types = hasTypeCol ? rows.map((r) => String(r[colorField] ?? "delta")) : values.map((_, i) => wantFirst && i === 0 ? "start" : wantLast && i === values.length - 1 ? "end" : "delta");
    const cumulative = [];
    let acc = 0;
    for (const v of values) {
      acc += v;
      cumulative.push(acc);
    }
    const COLOR = { startEnd: "#5470c6", increase: "#91cc75", decrease: "#ee6666" };
    const fmt = (n) => {
      const a = Math.abs(n);
      if (a >= 1e3) {
        const v = n / 1e3;
        return `${Number(v.toFixed(v % 1 === 0 ? 0 : 1)).toLocaleString("en-US")}k`;
      }
      return Number(n.toFixed(2)).toLocaleString("en-US");
    };
    const barData = [];
    const tops = [];
    const outerText = [];
    const innerText = [];
    const tipVals = [];
    const prevVals = [];
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const t = types[i];
      const top = t === "end" ? cumulative[i] - v : cumulative[i];
      const prev = t === "start" || t === "end" ? 0 : cumulative[i] - v;
      const lo = Math.min(prev, top);
      const hi = Math.max(prev, top);
      const color = t === "start" || t === "end" ? COLOR.startEnd : top >= prev ? COLOR.increase : COLOR.decrease;
      barData.push({ value: [i, lo, hi, v], itemStyle: { color } });
      tops.push(top);
      outerText.push(fmt(top));
      innerText.push((t === "delta" && v > 0 ? "+" : "") + fmt(v));
      tipVals.push(top);
      prevVals.push(prev);
    }
    const showLabels = !!ctx.chartProperties?.showTextLabels;
    const BAR_WIDTH_FRAC = 0.58;
    const connectorData = tops.slice(0, -1).map((y, i) => [i, y]);
    const legendItems = ["Start/End", "Increase", "Decrease"];
    const legendColors = [COLOR.startEnd, COLOR.increase, COLOR.decrease];
    const option = {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params) => {
          const head = params[0]?.axisValueLabel ?? params[0]?.name ?? "";
          const bar = params.find((p) => p.seriesName === "Delta" && Array.isArray(p.value));
          if (!bar) return String(head);
          return `${head}<br/>${bar.marker ?? ""} ${yField}: ${bar.value[3]}`;
        }
      },
      legend: {
        data: legendItems
      },
      xAxis: {
        type: "category",
        data: categories,
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: {
          rotate: areCategoriesNumeric7(categories) ? 0 : 90,
          formatter: (value) => value
        }
      },
      yAxis: { type: "value", name: yField, axisTick: { show: true } },
      series: [
        // Bars as floating rectangles. A custom series gives full [lo, hi]
        // control so bars crossing zero render correctly (a transparent-base +
        // delta stack would snap them back to the zero baseline, because
        // ECharts splits positive/negative values onto separate stacks).
        {
          type: "custom",
          name: "Delta",
          data: barData,
          encode: { x: 0, y: [1, 2] },
          renderItem: (params, api) => {
            const i = api.value(0);
            const lo = api.value(1);
            const hi = api.value(2);
            const pLo = api.coord([i, lo]);
            const pHi = api.coord([i, hi]);
            const band = api.size([1, 0])[0];
            const w = band * BAR_WIDTH_FRAC;
            const cx = pLo[0];
            const yTop = Math.min(pLo[1], pHi[1]);
            const h = Math.abs(pLo[1] - pHi[1]);
            const rect = {
              type: "rect",
              shape: { x: cx - w / 2, y: yTop, width: w, height: h },
              style: api.style()
            };
            if (!showLabels || band < 18) return rect;
            const idx = params.dataIndex;
            const fontSize = band >= 40 ? 10 : band >= 26 ? 9 : 8;
            const up = tipVals[idx] >= prevVals[idx];
            const pTip = api.coord([i, tipVals[idx]]);
            const children = [rect];
            children.push({
              type: "text",
              style: {
                text: outerText[idx],
                x: cx,
                y: pTip[1] + (up ? -4 : 4),
                textAlign: "center",
                textVerticalAlign: up ? "bottom" : "top",
                fill: "#374151",
                fontSize
              }
            });
            if (h >= fontSize + 4) {
              children.push({
                type: "text",
                style: {
                  text: innerText[idx],
                  x: cx,
                  y: (pLo[1] + pHi[1]) / 2,
                  textAlign: "center",
                  textVerticalAlign: "middle",
                  fill: "#ffffff",
                  fontSize
                }
              });
            }
            return { type: "group", children };
          }
        },
        // Legend-only series: no data, only for the legend colour swatches.
        ...legendItems.map((name, i) => ({
          type: "bar",
          name,
          data: [],
          barWidth: "58%",
          itemStyle: { color: legendColors[i] }
        })),
        // Connector lines: gap-only horizontal segments between adjacent bars.
        {
          type: "custom",
          name: "__connectors",
          silent: true,
          z: 5,
          data: connectorData,
          renderItem: (_params, api) => {
            const i = api.value(0);
            const y = api.value(1);
            const pThis = api.coord([i, y]);
            const pNext = api.coord([i + 1, y]);
            const half = (pNext[0] - pThis[0]) * (BAR_WIDTH_FRAC / 2);
            return {
              type: "line",
              shape: {
                x1: pThis[0] - half,
                y1: pThis[1],
                x2: pNext[0] + half,
                y2: pNext[1]
              },
              style: { stroke: "#6b7280", lineWidth: 1, opacity: 0.7 }
            };
          }
        }
      ]
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/pyramid.ts
function rowMatchesColorGroup(row, colorField, groupVal) {
  const raw = row[colorField];
  if (raw === groupVal) return true;
  if (raw == null || groupVal == null) return false;
  return String(raw) === String(groupVal);
}
var ecPyramidChartDef = {
  chart: "Pyramid Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["x", "y", "color"],
  markCognitiveChannel: "length",
  declareLayoutMode: () => ({ axisFlags: { y: { banded: true } } }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    const xField = xCS?.field;
    const yField = yCS?.field;
    if (!xField || !yField) return;
    const yDiscrete = yCS?.type === "nominal" || yCS?.type === "ordinal";
    const catField = yDiscrete ? yField : xField;
    const valField = yDiscrete ? xField : yField;
    const colorField = channelSemantics.color?.field ?? channelSemantics.group?.field;
    const catChannel = yDiscrete ? "y" : "x";
    const ordinalSort = getCategoryOrder(ctx, catChannel) ?? (yDiscrete ? yCS?.ordinalSortOrder : xCS?.ordinalSortOrder);
    const categories = extractCategories(table, catField, ordinalSort);
    const sumPerCategory = (predicate) => {
      const valueMap = /* @__PURE__ */ new Map();
      for (const row of table) {
        if (predicate && !predicate(row)) continue;
        const cat = String(row[catField] ?? "");
        const v = row[valField];
        if (v != null && !isNaN(Number(v))) {
          valueMap.set(cat, (valueMap.get(cat) ?? 0) + Number(v));
        }
      }
      return categories.map((cat) => valueMap.get(cat) ?? 0);
    };
    let leftPos;
    let rightPos;
    let leftName;
    let rightName;
    if (colorField && table.length > 0) {
      const groups = [...new Set(table.map((r) => r[colorField]))];
      const leftGroup = groups[0];
      const rightGroup = groups.length > 1 ? groups[1] : groups[0];
      leftPos = sumPerCategory((row) => rowMatchesColorGroup(row, colorField, leftGroup));
      rightPos = sumPerCategory((row) => rowMatchesColorGroup(row, colorField, rightGroup));
      leftName = String(leftGroup);
      rightName = String(rightGroup);
      if (groups.length > 2) {
        if (!spec._warnings) spec._warnings = [];
        spec._warnings.push({
          severity: "warning",
          code: "too-many-groups-pyramid",
          message: `Pyramid chart works best with exactly 2 groups, but found ${groups.length} (${groups.map((g) => `'${g}'`).join(", ")}). Only the first two are shown.`,
          channel: "color",
          field: colorField
        });
      }
    } else {
      const values = sumPerCategory();
      leftPos = values;
      rightPos = values;
    }
    const leftData = leftPos.map((v) => -v);
    const rightData = rightPos;
    const maxAbs = Math.max(0, ...leftData.map(Math.abs), ...rightData.map(Math.abs));
    const axisLineStyle = { color: "#333", width: 1 };
    const tickLineStyle = { color: "#333", width: 1 };
    const labelFont = { fontSize: 11, color: "#333" };
    const yAxisStyle = {
      type: "category",
      data: categories,
      name: catField,
      nameLocation: "middle",
      nameGap: 40,
      nameTextStyle: { fontSize: ctx.layout.titleFontSize, color: "#333" },
      boundaryGap: true,
      axisLine: { show: true, onZero: false, lineStyle: axisLineStyle },
      axisTick: {
        show: true,
        alignWithLabel: true,
        interval: 0,
        length: 6,
        lineStyle: tickLineStyle
      },
      axisLabel: { ...labelFont },
      splitLine: { show: false }
    };
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        name: valField,
        nameLocation: "middle",
        nameGap: 28,
        nameTextStyle: { fontSize: ctx.layout.titleFontSize, color: "#333" },
        axisLine: { show: true, lineStyle: axisLineStyle },
        axisTick: { show: true, length: 6, lineStyle: tickLineStyle },
        axisLabel: {
          ...labelFont,
          formatter: (v) => Math.abs(v).toString()
        },
        splitLine: { show: false },
        ...maxAbs > 0 ? { min: -maxAbs, max: maxAbs } : {}
      },
      yAxis: yAxisStyle,
      series: [
        {
          type: "bar",
          name: leftName,
          data: leftData,
          barGap: "-100%"
        },
        {
          type: "bar",
          name: rightName,
          data: rightData,
          barGap: "-100%"
        }
      ]
    };
    if (leftName != null && rightName != null) {
      option._pyramidChannelHeader = leftName === rightName ? { mode: "single", text: leftName } : { mode: "pair", left: leftName, right: rightName };
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/ranged-dot.ts
var isDiscrete11 = (type) => type === "nominal" || type === "ordinal";
var ecRangedDotPlotDef = {
  chart: "Ranged Dot Plot",
  template: { mark: "line", encoding: {} },
  channels: ["x", "y", "color"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const xField = channelSemantics.x?.field;
    const yField = channelSemantics.y?.field;
    const colorField = channelSemantics.color?.field;
    if (!xField || !yField) return;
    const xIsDiscrete = isDiscrete11(channelSemantics.x?.type);
    const yIsDiscrete = isDiscrete11(channelSemantics.y?.type);
    const xCategories = xIsDiscrete ? extractCategories(table, xField, channelSemantics.x?.ordinalSortOrder) : void 0;
    const yCategories = yIsDiscrete ? extractCategories(table, yField, getCategoryOrder(ctx, "y")) : void 0;
    const yIndexMap = yCategories ? new Map(yCategories.map((c, i) => [c, i])) : null;
    const option = {
      tooltip: { trigger: "item" },
      xAxis: {
        type: xIsDiscrete ? "category" : "value",
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        axisTick: xIsDiscrete ? { show: true, alignWithLabel: true } : { show: true },
        ...xCategories ? { data: xCategories } : {}
      },
      yAxis: yIsDiscrete && yCategories ? {
        type: "category",
        data: yCategories,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisTick: { show: true, alignWithLabel: true },
        axisLabel: { rotate: 0 }
      } : { type: "value", name: yField, nameLocation: "middle", nameGap: 40, axisTick: { show: true } },
      series: []
    };
    const pointForRow = (r) => {
      if (yIndexMap != null) {
        const yi = yIndexMap.get(String(r[yField] ?? ""));
        if (yi === void 0) return [Number(r[xField]), 0];
        return [Number(r[xField]), yi];
      }
      return [r[xField], r[yField]];
    };
    if (colorField) {
      const groups = groupBy(table, colorField);
      const colorCategories = [...groups.keys()];
      option.legend = { data: colorCategories };
      option._legendTitle = colorField;
      if (yCategories && yIndexMap) {
        const segmentData = [];
        for (let i = 0; i < yCategories.length; i++) {
          const yCat = yCategories[i];
          const rows = table.filter((r) => String(r[yField] ?? "") === yCat);
          if (xIsDiscrete && xCategories) {
            const indices = rows.map((r) => xCategories.indexOf(String(r[xField] ?? ""))).filter((idx) => idx >= 0);
            if (indices.length >= 1) {
              const minXi = Math.min(...indices);
              const maxXi = Math.max(...indices);
              segmentData.push([minXi, i], [maxXi, i], null);
            }
          } else {
            const vals = rows.map((r) => Number(r[xField])).filter((v) => isFinite(v));
            if (vals.length >= 1) {
              const minX = Math.min(...vals);
              const maxX = Math.max(...vals);
              segmentData.push([minX, i], [maxX, i], null);
            }
          }
        }
        if (segmentData.length > 0) {
          segmentData.pop();
          option.series.push({
            name: "",
            // no legend entry for connector line
            type: "line",
            data: segmentData,
            showSymbol: false,
            itemStyle: { color: "#999" },
            lineStyle: { color: "#999" }
          });
        }
      }
      for (const [name, rows] of groups) {
        const scatterData = xIsDiscrete ? xCategories.map((cat, xi) => {
          const row = rows.find((r) => String(r[xField]) === cat);
          if (!row) return null;
          return yIndexMap ? [xi, yIndexMap.get(String(row[yField] ?? "")) ?? 0] : [xi, row[yField]];
        }).filter(Boolean) : yCategories && yIndexMap ? [...rows].sort((a, b) => (yIndexMap.get(String(a[yField])) ?? 0) - (yIndexMap.get(String(b[yField])) ?? 0)).map((r) => pointForRow(r)) : rows.map((r) => [r[xField], r[yField]]);
        option.series.push({
          name,
          type: "scatter",
          data: scatterData,
          symbolSize: 8
          // 颜色由 ecApplyLayoutToSpec 根据 colorDecisions 统一分配
        });
      }
    } else {
      const lineData = xIsDiscrete ? xCategories.map((cat, xi) => {
        const row = table.find((r) => String(r[xField]) === cat);
        if (!row) return null;
        return yIndexMap ? [xi, yIndexMap.get(String(row[yField] ?? "")) ?? 0] : [xi, row[yField]];
      }) : yCategories ? [...table].sort((a, b) => (yIndexMap.get(String(a[yField])) ?? 0) - (yIndexMap.get(String(b[yField])) ?? 0)).map((r) => pointForRow(r)) : table.map((r) => [r[xField], r[yField]]);
      const scatterData = xIsDiscrete ? yIndexMap ? lineData : xCategories.map((cat, i) => [cat, lineData[i]]) : lineData;
      option.series.push({ type: "line", data: lineData, showSymbol: false });
      option.series.push({ type: "scatter", data: scatterData, symbolSize: 8 });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/density.ts
function estimateBandwidth(values) {
  const n = values.length;
  if (n < 2) return 1;
  const sorted = [...values].sort((a, b) => a - b);
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const variance = values.reduce((s, v2) => s + (v2 - mean) ** 2, 0) / n;
  const d = Math.sqrt(variance);
  const q1 = sorted[Math.floor((n - 1) * 0.25)];
  const q3 = sorted[Math.floor((n - 1) * 0.75)];
  const iqr = q3 != null && q1 != null ? q3 - q1 : 0;
  const h = iqr / 1.34;
  const v = Math.min(d, h || d) || d || 1;
  return 1.06 * v * Math.pow(n, -0.2);
}
function kde(values, steps, bandwidthMultiplier, extent) {
  if (values.length === 0) return { x: [], y: [] };
  const min = extent ? extent.min : Math.min(...values);
  const max = extent ? extent.max : Math.max(...values);
  const range = max - min || 1;
  const lo = min;
  const hi = max;
  const h = estimateBandwidth(values) * bandwidthMultiplier;
  const n = values.length;
  const x = [];
  const y = [];
  for (let i = 0; i <= steps; i++) {
    const t = lo + i / steps * (hi - lo || range);
    let sum = 0;
    for (const v of values) {
      const z = (t - v) / h;
      sum += Math.exp(-0.5 * z * z);
    }
    const density = sum / (n * h * Math.sqrt(2 * Math.PI));
    x.push(t);
    y.push(density);
  }
  return { x, y };
}
var ecDensityPlotDef = {
  chart: "Density Plot",
  template: { mark: "area", encoding: {} },
  channels: ["x", "color", "column", "row"],
  markCognitiveChannel: "area",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xField = channelSemantics.x?.field;
    const colorField = channelSemantics.color?.field;
    if (!xField) return;
    const steps = 200;
    const bandwidthMultiplier = chartProperties?.bandwidth != null && chartProperties.bandwidth > 0 ? chartProperties.bandwidth : 1;
    const option = {
      tooltip: { trigger: "axis" },
      xAxis: { type: "value", name: xField, nameLocation: "middle", nameGap: 30, axisTick: { show: true } },
      yAxis: { type: "value", name: "Density", nameLocation: "middle", nameGap: 40, axisTick: { show: true } },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: "Density" };
    if (colorField) {
      const groups = groupBy(table, colorField);
      option.legend = { data: [...groups.keys()] };
      option._legendTitle = colorField;
      const allValues = table.map((r) => Number(r[xField])).filter((v) => !isNaN(v));
      const sharedExtent = allValues.length > 0 ? { min: Math.min(...allValues), max: Math.max(...allValues) } : void 0;
      for (const [name, rows] of groups) {
        const values = rows.map((r) => Number(r[xField])).filter((v) => !isNaN(v));
        const { x, y } = kde(values, steps, bandwidthMultiplier, sharedExtent);
        const data = x.map((xi, i) => [xi, y[i]]);
        option.series.push({
          name,
          type: "line",
          data,
          symbol: "none",
          // 颜色由 color-decisions / option.color 驱动；这里只设置透明度。
          areaStyle: { opacity: 0.5 }
        });
      }
    } else {
      const values = table.map((r) => Number(r[xField])).filter((v) => !isNaN(v));
      const { x, y } = kde(values, steps, bandwidthMultiplier);
      const data = x.map((xi, i) => [xi, y[i]]);
      option.series.push({
        type: "line",
        data,
        symbol: "none",
        areaStyle: { opacity: 0.5 }
      });
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "bandwidth", label: "Bandwidth", type: "continuous", min: 0.05, max: 2, step: 0.05, defaultValue: 0 }
  ]
};

// src/echarts/templates/ecdf.ts
function ecdfPairs(values) {
  const sorted = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  const n = sorted.length;
  const pairs = [];
  if (n === 0) return pairs;
  let i = 0;
  while (i < n) {
    let j = i;
    while (j + 1 < n && sorted[j + 1] === sorted[i]) j++;
    pairs.push([sorted[i], (j + 1) / n]);
    i = j + 1;
  }
  return pairs;
}
var ecEcdfPlotDef = {
  chart: "ECDF Plot",
  template: { mark: "line", encoding: {} },
  channels: ["x", "color", "detail", "column", "row"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    const xField = channelSemantics.x?.field;
    const groupField = channelSemantics.color?.field ?? channelSemantics.detail?.field;
    if (!xField) return;
    const showPoints = !!chartProperties?.showPoints;
    const option = {
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      xAxis: {
        type: "value",
        name: xField,
        nameLocation: "middle",
        nameGap: 30,
        // Fit the measure range (an ECDF reads the value of the rise, not
        // distance from zero).
        scale: true,
        axisTick: { show: true }
      },
      yAxis: {
        type: "value",
        name: "Cumulative proportion",
        nameLocation: "middle",
        nameGap: 45,
        min: 0,
        max: 1,
        axisTick: { show: true }
      },
      series: []
    };
    option._encodingTooltip = { trigger: "axis", categoryLabel: xField, valueLabel: "Cumulative proportion" };
    const makeSeries = (name, values) => ({
      ...name != null ? { name } : {},
      type: "line",
      // step-after: hold the proportion until the next value, then jump.
      step: "end",
      data: ecdfPairs(values),
      showSymbol: showPoints,
      symbol: "circle",
      symbolSize: 6,
      lineStyle: { width: 2 },
      emphasis: { focus: "series" }
    });
    if (groupField) {
      const groups = groupBy(table, groupField);
      option.legend = { data: [...groups.keys()] };
      option._legendTitle = groupField;
      for (const [name, rows] of groups) {
        const values = rows.map((r) => Number(r[xField])).filter((v) => !isNaN(v));
        option.series.push(makeSeries(name, values));
      }
    } else {
      const values = table.map((r) => Number(r[xField])).filter((v) => !isNaN(v));
      option.series.push(makeSeries(void 0, values));
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    { key: "showPoints", label: "Points", type: "binary", defaultValue: false }
  ]
};

// src/echarts/templates/calendar.ts
var SCHEME_COLORS2 = {
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  blues: ["#f7fbff", "#6baed6", "#08519c"],
  greens: ["#f7fcf5", "#74c476", "#00441b"],
  reds: ["#fff5f0", "#fb6a4a", "#a50f15"],
  oranges: ["#fff5eb", "#fd8d3c", "#7f2704"],
  purples: ["#fcfbfd", "#9e9ac8", "#3f007d"],
  github: ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
};
function toDateString(raw) {
  if (raw == null) return null;
  let d;
  if (raw instanceof Date) {
    d = raw;
  } else if (typeof raw === "number" && isFinite(raw)) {
    d = new Date(raw < 1e12 ? raw * 1e3 : raw);
  } else {
    const s = String(raw).trim();
    d = new Date(s);
    if (isNaN(d.getTime())) return null;
  }
  if (isNaN(d.getTime())) return null;
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
var ecCalendarHeatmapDef = {
  chart: "Calendar Heatmap",
  template: { mark: "rect", encoding: {} },
  channels: ["x", "color"],
  markCognitiveChannel: "color",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, colorDecisions, encodings } = ctx;
    const dateField = channelSemantics.x?.field;
    const valueField = channelSemantics.color?.field;
    if (!dateField) return;
    const cellMap = /* @__PURE__ */ new Map();
    for (const row of table) {
      const dateStr = toDateString(row[dateField]);
      if (!dateStr) continue;
      const val = valueField ? Number(row[valueField]) || 0 : 1;
      cellMap.set(dateStr, (cellMap.get(dateStr) ?? 0) + val);
    }
    const calData = [];
    let minVal = Infinity;
    let maxVal = -Infinity;
    let minDate = "9999-12-31";
    let maxDate = "0000-01-01";
    for (const [dateStr, val] of cellMap) {
      calData.push([dateStr, val]);
      if (val < minVal) minVal = val;
      if (val > maxVal) maxVal = val;
      if (dateStr < minDate) minDate = dateStr;
      if (dateStr > maxDate) maxDate = dateStr;
    }
    if (calData.length === 0) return;
    if (minVal === Infinity) minVal = 0;
    if (maxVal === -Infinity) maxVal = 1;
    if (minVal === maxVal) maxVal = minVal + 1;
    const dayMs = 864e5;
    const spanDays = (Date.parse(maxDate) - Date.parse(minDate)) / dayMs;
    const weeks = Math.max(1, Math.ceil((spanDays + 8) / 7));
    const cell = weeks > 60 ? 12 : weeks > 30 ? 15 : 18;
    const calLeft = 44;
    const calRight = 16;
    const calTop = 34;
    const vmHeight = 70;
    const gridH = 7 * cell;
    const canvasW = calLeft + weeks * cell + calRight;
    const canvasH = calTop + gridH + vmHeight;
    const encScheme = encodings?.color?.scheme;
    const userScheme = encScheme && encScheme !== "default" ? encScheme : void 0;
    const schemeName = userScheme || "viridis";
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let schemeColors = SCHEME_COLORS2[schemeName] || SCHEME_COLORS2.viridis;
    if (decision?.schemeId) {
      const fromDecision = getPaletteForScheme(decision.schemeId);
      if (fromDecision && fromDecision.length > 0) schemeColors = fromDecision;
    }
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const [date, val] = params.value;
          return `${date}<br/>${valueField ?? "Count"}: ${val}`;
        }
      },
      visualMap: {
        type: "continuous",
        min: minVal,
        max: maxVal,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 6,
        itemWidth: 12,
        itemHeight: 100,
        text: ["high", "low"],
        inRange: { color: schemeColors }
      },
      calendar: {
        top: calTop,
        left: calLeft,
        right: calRight,
        cellSize: [cell, cell],
        range: minDate === maxDate ? minDate : [minDate, maxDate],
        orient: "horizontal",
        splitLine: { show: true, lineStyle: { color: "#ccc", width: 1 } },
        itemStyle: { borderWidth: 1, borderColor: "#fff", color: "#f4f4f4" },
        yearLabel: { show: false },
        dayLabel: { firstDay: 1, fontSize: 10, color: "#666" },
        monthLabel: { fontSize: 11, color: "#333" }
      },
      series: [{
        type: "heatmap",
        coordinateSystem: "calendar",
        data: calData
      }],
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  encodingActions: [
    {
      key: "colorScheme",
      label: "Scheme",
      isApplicable: (ctx) => !!ctx.encodings.color?.field,
      dependencies: ["color"],
      control: {
        type: "discrete",
        options: [
          { value: void 0, label: "Default (Viridis)" },
          { value: "viridis", label: "Viridis" },
          { value: "github", label: "GitHub" },
          { value: "blues", label: "Blues" },
          { value: "greens", label: "Greens" },
          { value: "reds", label: "Reds" },
          { value: "oranges", label: "Oranges" },
          { value: "purples", label: "Purples" }
        ]
      },
      get: (enc) => enc.color?.scheme,
      set: (enc, value) => ({ ...enc, color: { ...enc.color, scheme: value } })
    }
  ]
};

// src/chartjs/colormap.ts
var CHARTJS_COLOR_MAPS = [
  {
    id: "cat10",
    type: "categorical",
    supportsDiscrete: true,
    supportsContinuous: false,
    background: "any",
    maxCategories: 10,
    colorblindSafe: false,
    colors: [
      "#36a2eb",
      // blue
      "#ff6384",
      // red
      "#ffcd56",
      // yellow
      "#4bc0c0",
      // teal
      "#9966ff",
      // purple
      "#ff9f40",
      // orange
      "#2ecc71",
      // green
      "#34495e",
      // dark blue-grey
      "#e74c3c",
      // red-orange
      "#95a5a6"
      // grey
    ]
  },
  {
    id: "cat20",
    type: "categorical",
    supportsDiscrete: true,
    supportsContinuous: false,
    background: "any",
    maxCategories: 20,
    colorblindSafe: false,
    colors: [
      "#36a2eb",
      "#9ad0f5",
      "#ff6384",
      "#ff99aa",
      "#ffcd56",
      "#ffe39f",
      "#4bc0c0",
      "#8fdede",
      "#9966ff",
      "#c3a3ff",
      "#ff9f40",
      "#ffc078",
      "#2ecc71",
      "#7ee2a8",
      "#34495e",
      "#5d6d7e",
      "#e74c3c",
      "#f1948a",
      "#95a5a6",
      "#cfd4d6"
    ]
  },
  {
    id: "viridis",
    type: "sequential",
    supportsDiscrete: true,
    supportsContinuous: true,
    background: "any",
    colorblindSafe: true,
    colors: [
      "#440154",
      "#46327e",
      "#365c8d",
      "#277f8e",
      "#1fa187",
      "#4ac16d",
      "#a0da39",
      "#fde725"
    ]
  },
  {
    id: "RdBu",
    type: "diverging",
    supportsDiscrete: true,
    supportsContinuous: true,
    background: "any",
    diverging: true,
    preferredMidpoint: 0,
    colors: [
      "#b2182b",
      "#d6604d",
      "#f4a582",
      "#fddbc7",
      "#f7f7f7",
      "#d1e5f0",
      "#92c5de",
      "#4393c3",
      "#2166ac"
    ]
  }
];
function getMapById2(id) {
  if (!id) return void 0;
  const key = String(id).toLowerCase();
  return CHARTJS_COLOR_MAPS.find((m) => m.id.toLowerCase() === key);
}
function getPaletteForScheme2(id) {
  const entry = getMapById2(id);
  return entry?.colors;
}
function pickChartJsPalette(decision) {
  if (!decision) {
    const fallback2 = getPaletteForScheme2("cat10");
    return fallback2 && fallback2.length ? fallback2 : [];
  }
  const { schemeType, schemeId, categoryCount } = decision;
  if (schemeId) {
    const fromId = getPaletteForScheme2(schemeId);
    if (fromId && fromId.length > 0) {
      return fromId;
    }
  }
  const mapsOfType = CHARTJS_COLOR_MAPS.filter((m) => m.type === schemeType);
  if (schemeType === "categorical") {
    const k = categoryCount ?? 0;
    if (mapsOfType.length) {
      const candidates = mapsOfType.filter((m) => m.supportsDiscrete);
      if (candidates.length) {
        const byCapacity = candidates.filter((m) => m.maxCategories == null || m.maxCategories >= k).sort((a, b) => (a.maxCategories ?? Infinity) - (b.maxCategories ?? Infinity));
        const picked = byCapacity[0] ?? candidates[0];
        if (picked.colors.length) {
          return picked.colors;
        }
      }
    }
    const fallback2 = getPaletteForScheme2("cat10");
    if (fallback2 && fallback2.length) {
      return fallback2;
    }
  } else if (schemeType === "sequential") {
    const seq = mapsOfType.find((m) => m.supportsContinuous) ?? getMapById2("viridis");
    if (seq && seq.colors.length) {
      return seq.colors;
    }
  } else if (schemeType === "diverging") {
    const divergingFirst = mapsOfType.find((m) => m.diverging) ?? getMapById2("RdBu");
    if (divergingFirst && divergingFirst.colors.length) {
      return divergingFirst.colors;
    }
  }
  const fallback = getPaletteForScheme2("cat10");
  return fallback && fallback.length ? fallback : [];
}

// src/chartjs/templates/utils.ts
var DEFAULT_COLORS2 = [
  "rgba(54, 162, 235, 1)",
  // blue
  "rgba(255, 99, 132, 1)",
  // red
  "rgba(255, 206, 86, 1)",
  // yellow
  "rgba(75, 192, 192, 1)",
  // teal
  "rgba(153, 102, 255, 1)",
  // purple
  "rgba(255, 159, 64, 1)",
  // orange
  "rgba(46, 204, 113, 1)",
  // green
  "rgba(52, 73, 94, 1)",
  // dark blue-grey
  "rgba(231, 76, 60, 1)",
  // red-orange
  "rgba(149, 165, 166, 1)"
  // grey
];
function getChartJsPalette(ctx, preferred = "color") {
  const decisions = ctx.colorDecisions;
  const decision = preferred === "color" ? decisions?.color ?? decisions?.group : decisions?.group ?? decisions?.color;
  const palette = pickChartJsPalette(decision);
  if (palette.length > 0) {
    return palette;
  }
  return DEFAULT_COLORS2;
}

// src/echarts/templates/parallel.ts
var DEFAULT_COLORS3 = [
  "#5470c6",
  "#91cc75",
  "#fac858",
  "#ee6666",
  "#73c0de",
  "#3ba272",
  "#fc8452",
  "#9a60b4",
  "#ea7ccc",
  "#c0504d"
];
function isNumericField(table, field) {
  let total = 0;
  let numeric = 0;
  for (const row of table) {
    const v = row[field];
    if (v == null || v === "") continue;
    total++;
    if (typeof v === "number" ? isFinite(v) : !isNaN(Number(v))) numeric++;
  }
  return total > 0 && numeric / total >= 0.9;
}
function niceBounds(min, max) {
  if (!isFinite(min) || !isFinite(max)) return null;
  if (min === max) {
    const pad = Math.abs(min) > 1e-9 ? Math.abs(min) * 0.1 : 1;
    return [min - pad, max + pad];
  }
  const rawStep = (max - min) / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  return [Math.floor(min / step) * step, Math.ceil(max / step) * step];
}
var ecParallelCoordinatesDef = {
  chart: "Parallel Coordinates",
  template: { mark: "line", encoding: {} },
  channels: ["color", "detail"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties } = ctx;
    if (table.length === 0) return;
    const colorField = channelSemantics.color?.field;
    let dims = Array.isArray(chartProperties?.dimensions) ? chartProperties.dimensions.filter((d) => d in table[0]) : [];
    if (dims.length === 0) {
      dims = Object.keys(table[0]).filter(
        (k) => k !== colorField && isNumericField(table, k)
      );
    }
    if (dims.length < 2) return;
    const palette = colorField ? getChartJsPalette(ctx, "color") : DEFAULT_COLORS3;
    const colors = palette.length > 0 ? palette : DEFAULT_COLORS3;
    const parallelAxis = dims.map((name, i) => {
      let lo = Infinity;
      let hi = -Infinity;
      for (const row of table) {
        const v = Number(row[name]);
        if (isFinite(v)) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
      const bounds = niceBounds(lo, hi);
      return {
        dim: i,
        name,
        nameTextStyle: { fontSize: ctx.layout.titleFontSize },
        nameGap: 8,
        axisLabel: { fontSize: 10 },
        ...bounds ? { min: bounds[0], max: bounds[1] } : {}
      };
    });
    const toLine = (row) => dims.map((d) => {
      const v = Number(row[d]);
      return isFinite(v) ? v : null;
    });
    const series = [];
    const legendData = [];
    const lineOpacity = table.length > 200 ? 0.22 : table.length > 100 ? 0.3 : table.length > 60 ? 0.45 : 0.6;
    if (colorField) {
      const groups = /* @__PURE__ */ new Map();
      for (const row of table) {
        const key = String(row[colorField] ?? "");
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(toLine(row));
      }
      let i = 0;
      for (const [name, data] of groups) {
        legendData.push(name);
        series.push({
          name,
          type: "parallel",
          data,
          lineStyle: { width: 1.5, opacity: lineOpacity, color: colors[i % colors.length] },
          emphasis: { lineStyle: { width: 3, opacity: 0.9 } }
        });
        i++;
      }
    } else {
      series.push({
        type: "parallel",
        data: table.map(toLine),
        lineStyle: { width: 1.5, opacity: lineOpacity, color: colors[0] },
        emphasis: { lineStyle: { width: 3, opacity: 0.9 } }
      });
    }
    const hasLegend = legendData.length > 1;
    const parTop = hasLegend ? 56 : 28;
    const parBottom = 36;
    const parLeft = 56;
    const parRight = 56;
    const perDim = 96;
    const canvasW = Math.max(ctx.canvasSize.width, parLeft + parRight + (dims.length - 1) * perDim);
    const canvasH = Math.max(ctx.canvasSize.height, parTop + parBottom + 200);
    const option = {
      tooltip: {},
      parallelAxis,
      parallel: {
        top: parTop,
        bottom: parBottom,
        left: parLeft,
        right: parRight,
        parallelAxisDefault: {
          nameLocation: "end",
          nameGap: 14,
          axisLine: { lineStyle: { color: "#888" } },
          axisLabel: { color: "#555" }
        }
      },
      series,
      _width: canvasW,
      _height: canvasH
    };
    if (hasLegend) {
      option.legend = {
        data: legendData,
        top: 8,
        left: "center",
        orient: "horizontal",
        itemWidth: 18,
        textStyle: { fontSize: ctx.layout.legendFontSize },
        ...legendData.length > 10 ? { type: "scroll" } : {}
      };
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/graph.ts
var ecGraphDef = {
  chart: "Network Graph",
  template: { mark: "point", encoding: {} },
  channels: ["x", "y", "size"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const sourceField = channelSemantics.x?.field;
    const targetField = channelSemantics.y?.field;
    const weightField = channelSemantics.size?.field;
    if (!sourceField || !targetField) return;
    const linkAgg = /* @__PURE__ */ new Map();
    const degree = /* @__PURE__ */ new Map();
    for (const row of table) {
      const src = String(row[sourceField] ?? "");
      const tgt = String(row[targetField] ?? "");
      if (!src || !tgt || src === tgt) continue;
      const w = weightField ? Number(row[weightField]) || 0 : 1;
      const key = `${src}\0${tgt}`;
      linkAgg.set(key, (linkAgg.get(key) ?? 0) + w);
      degree.set(src, (degree.get(src) ?? 0) + w);
      degree.set(tgt, (degree.get(tgt) ?? 0) + w);
    }
    const links = [];
    const nodeSet = /* @__PURE__ */ new Set();
    for (const [key, value] of linkAgg) {
      const [source, target] = key.split("\0");
      nodeSet.add(source);
      nodeSet.add(target);
      links.push({ source, target, value });
    }
    if (links.length === 0) return;
    const nodeArr = [...nodeSet];
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) palette = fromRegistry;
    }
    if (!palette || palette.length === 0) {
      palette = getPaletteForScheme(nodeArr.length > 10 ? "cat20" : "cat10") ?? DEFAULT_COLORS;
    }
    const degVals = nodeArr.map((n) => degree.get(n) ?? 0);
    const dMin = Math.min(...degVals);
    const dMax = Math.max(...degVals);
    const rMin = 12, rMax = 46;
    const sizeFor = (d) => {
      if (dMax === dMin) return (rMin + rMax) / 2;
      const t = (d - dMin) / (dMax - dMin);
      const area = rMin * rMin + t * (rMax * rMax - rMin * rMin);
      return Math.sqrt(area);
    };
    const nodes = nodeArr.map((name, i) => ({
      name,
      value: degree.get(name) ?? 0,
      symbolSize: sizeFor(degree.get(name) ?? 0),
      itemStyle: { color: palette[i % palette.length] }
    }));
    const wVals = links.map((l) => l.value);
    const wMin = Math.min(...wVals);
    const wMax = Math.max(...wVals);
    const widthFor = (v) => {
      if (wMax === wMin) return 1.4;
      return 0.8 + (v - wMin) / (wMax - wMin) * 3.2;
    };
    const layout = chartProperties?.layout === "force" ? "force" : "circular";
    const side = Math.max(420, Math.min(860, Math.round(Math.sqrt(nodeArr.length) * 155) + 40));
    const pad = 64;
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          if (params.dataType === "edge") {
            return `${params.data.source} \u2192 ${params.data.target}<br/>Weight: ${params.data.value}`;
          }
          return `${params.name}<br/>Degree: ${params.value}`;
        }
      },
      series: [{
        type: "graph",
        layout,
        data: nodes,
        links: links.map((l) => ({ ...l, lineStyle: { width: widthFor(l.value) } })),
        roam: false,
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#333"
        },
        lineStyle: {
          color: "source",
          opacity: 0.5,
          curveness: layout === "circular" ? 0.3 : 0
        },
        emphasis: {
          focus: "adjacency",
          lineStyle: { width: 4 }
        },
        circular: { rotateLabel: true },
        force: { repulsion: 180, edgeLength: [50, 130], gravity: 0.08 },
        left: pad,
        right: pad,
        top: pad,
        bottom: pad
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: side,
      _height: side
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "layout",
      label: "Layout",
      type: "discrete",
      options: [
        { value: "circular", label: "Circular (default)" },
        { value: "force", label: "Force-directed" }
      ]
    }
  ]
};

// src/echarts/templates/tree.ts
var ecTreeDef = {
  chart: "Tree",
  template: { mark: "point", encoding: {} },
  channels: ["color", "detail", "size"],
  markCognitiveChannel: "position",
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, colorDecisions } = ctx;
    const catField = channelSemantics.color?.field;
    const subCatField = channelSemantics.detail?.field;
    const valField = channelSemantics.size?.field;
    if (!catField) return;
    const categories = extractCategories(table, catField, channelSemantics.color?.ordinalSortOrder);
    if (categories.length === 0) return;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let palette;
    if (decision?.schemeId) {
      const fromRegistry = getPaletteForScheme(decision.schemeId);
      if (fromRegistry && fromRegistry.length > 0) palette = fromRegistry;
    }
    if (!palette || palette.length === 0) {
      palette = getPaletteForScheme(categories.length > 10 ? "cat20" : "cat10") ?? DEFAULT_COLORS;
    }
    let leafCount = 0;
    const children = categories.map((cat, catIdx) => {
      const catRows = table.filter((r) => String(r[catField]) === cat);
      const color = palette[catIdx % palette.length];
      if (subCatField) {
        const subCats = extractCategories(catRows, subCatField);
        const subChildren = subCats.map((sub) => {
          const subRows = catRows.filter((r) => String(r[subCatField]) === sub);
          const value2 = valField ? subRows.reduce((s, r) => s + (Number(r[valField]) || 0), 0) : subRows.length;
          leafCount++;
          return { name: sub, value: value2, lineStyle: { color }, itemStyle: { color } };
        });
        return { name: cat, children: subChildren, lineStyle: { color }, itemStyle: { color } };
      }
      const value = valField ? catRows.reduce((s, r) => s + (Number(r[valField]) || 0), 0) : catRows.length;
      leafCount++;
      return { name: cat, value, lineStyle: { color }, itemStyle: { color } };
    });
    const rootName = chartProperties?.rootLabel ?? "All";
    const treeData = [{ name: rootName, children }];
    const depth = subCatField ? 3 : 2;
    const orient = chartProperties?.orient === "TB" ? "TB" : "LR";
    const canvasW = Math.max(ctx.canvasSize.width, 340 + (depth - 1) * 210);
    const canvasH = Math.max(ctx.canvasSize.height, Math.min(1400, Math.max(300, leafCount * 26)));
    const option = {
      tooltip: {
        trigger: "item",
        triggerOn: "mousemove",
        formatter: (params) => {
          const v = params.value;
          return v != null && v !== "" ? `${params.name}<br/>Value: ${v}` : params.name;
        }
      },
      series: [{
        type: "tree",
        data: treeData,
        layout: "orthogonal",
        orient,
        top: 24,
        bottom: 24,
        left: orient === "LR" ? 40 : 24,
        right: orient === "LR" ? 140 : 24,
        symbol: "circle",
        symbolSize: 8,
        initialTreeDepth: -1,
        expandAndCollapse: false,
        roam: false,
        lineStyle: { width: 1.2, curveness: 0.5, color: "#bbb" },
        label: {
          show: true,
          position: orient === "LR" ? "left" : "top",
          verticalAlign: "middle",
          align: orient === "LR" ? "right" : "center",
          fontSize: 11,
          color: "#333"
        },
        leaves: {
          label: {
            position: orient === "LR" ? "right" : "bottom",
            verticalAlign: "middle",
            align: orient === "LR" ? "left" : "center"
          }
        },
        emphasis: { focus: "descendant" }
      }],
      color: palette ?? DEFAULT_COLORS,
      _width: canvasW,
      _height: canvasH
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: [
    {
      key: "orient",
      label: "Orient",
      type: "discrete",
      options: [
        { value: "LR", label: "Left \u2192 Right (default)" },
        { value: "TB", label: "Top \u2192 Bottom" }
      ]
    }
  ]
};

// src/chart-types/gantt.ts
var GANTT_PROPERTIES = [
  { key: "taskHeight", label: "Task height", type: "continuous", min: 40, max: 90, step: 5, defaultValue: 70 },
  { key: "cornerRadius", label: "Corners", type: "continuous", min: 0, max: 8, step: 1, defaultValue: 2 },
  { key: "intervalLabels", label: "Labels", type: "binary", defaultValue: false }
];
function sortGanttRows(rows) {
  return [...rows].sort((a, b) => a.start - b.start || a.inputIndex - b.inputIndex);
}
function coerceGanttEndpoint(value, temporal) {
  if (value == null) return NaN;
  if (!temporal) return Number(value);
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return Math.abs(value) < 1e11 ? value * 1e3 : value;
  return Date.parse(String(value));
}
function isGanttTemporal(resolvedType, semanticType) {
  if (resolvedType === "temporal") return true;
  const typeName = toTypeString(semanticType);
  return typeName ? isTimeSeriesType(typeName) : false;
}
function compactNumber(value) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
}
var GANTT_DURATION_UNITS = [
  { minimumMs: 864e5, divisorMs: 864e5, suffix: "d" },
  { minimumMs: 36e5, divisorMs: 36e5, suffix: "h" },
  { minimumMs: 6e4, divisorMs: 6e4, suffix: "min" },
  { minimumMs: 1e3, divisorMs: 1e3, suffix: "s" },
  { minimumMs: 0, divisorMs: 1, suffix: "ms" }
];
function formatGanttDuration(durationMs) {
  const unit = GANTT_DURATION_UNITS.find(({ minimumMs }) => Math.abs(durationMs) >= minimumMs);
  return `${compactNumber(durationMs / unit.divisorMs)}${unit.suffix}`;
}
function formatGanttLabel(start, end, temporal) {
  const duration = end - start;
  if (!temporal) return compactNumber(duration);
  return formatGanttDuration(duration);
}
function ganttLabelReservePx(rows, temporal) {
  const maxCharacters = rows.reduce((max, row) => Math.max(max, formatGanttLabel(row.start, row.end, temporal).length), 0);
  return Math.max(40, maxCharacters * 7 + 10);
}

// src/echarts/templates/gantt.ts
function fmtDate(ms) {
  if (!Number.isFinite(ms)) return "";
  return new Date(ms).toISOString().slice(0, 10);
}
var ecGanttChartDef = {
  chart: "Gantt Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["y", "x", "x2", "color", "detail", "column", "row"],
  markCognitiveChannel: "position",
  declareLayoutMode: () => ({ axisFlags: { y: { banded: true } } }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table, chartProperties, semanticTypes } = ctx;
    const taskField = channelSemantics.y?.field;
    const startField = channelSemantics.x?.field;
    const endField = channelSemantics.x2?.field;
    const colorField = channelSemantics.color?.field;
    if (!taskField || !startField || !endField || table.length === 0) return;
    const temporal = isGanttTemporal(channelSemantics.x?.type, semanticTypes[startField]);
    const rows = sortGanttRows(table.map((r, inputIndex) => ({
      task: String(r[taskField] ?? ""),
      start: coerceGanttEndpoint(r[startField], temporal),
      end: coerceGanttEndpoint(r[endField], temporal),
      group: colorField != null ? String(r[colorField] ?? "") : void 0,
      inputIndex
    })).filter((r) => r.task && Number.isFinite(r.start) && Number.isFinite(r.end)));
    const taskHeight = chartProperties?.taskHeight ?? 70;
    const cornerRadius = chartProperties?.cornerRadius ?? 2;
    const intervalLabels = chartProperties?.intervalLabels === true;
    const labelReserve = ganttLabelReservePx(rows, temporal);
    const tasks = rows.map((r) => r.task);
    const groups = colorField ? Array.from(new Set(rows.map((r) => r.group ?? ""))) : [];
    const groupColor = /* @__PURE__ */ new Map();
    groups.forEach((g, i) => groupColor.set(g, DEFAULT_COLORS[i % DEFAULT_COLORS.length]));
    const BAR_COLOR = DEFAULT_COLORS[0];
    const baseData = rows.map((r) => r.start);
    const durationData = rows.map((r) => ({
      value: r.end - r.start,
      itemStyle: { color: colorField ? groupColor.get(r.group ?? "") ?? BAR_COLOR : BAR_COLOR }
    }));
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (p) => {
          if (p.seriesName === "_base") return "";
          const r = rows[p.dataIndex];
          if (!r) return "";
          const s = temporal ? fmtDate(r.start) : r.start;
          const e = temporal ? fmtDate(r.end) : r.end;
          const grp = r.group != null ? `<br/>${colorField}: ${r.group}` : "";
          return `${r.task}<br/>${startField}: ${s}<br/>${endField}: ${e}${grp}`;
        }
      },
      grid: {
        containLabel: true,
        ...intervalLabels ? { right: labelReserve } : {}
      },
      xAxis: {
        type: "value",
        scale: true,
        name: temporal ? "" : startField,
        nameLocation: "middle",
        nameGap: 30,
        axisLabel: temporal ? { formatter: (v) => fmtDate(v), hideOverlap: true } : {}
      },
      yAxis: {
        type: "category",
        data: tasks,
        inverse: true,
        axisTick: { show: false },
        axisLabel: { interval: 0 }
      },
      series: [
        {
          type: "bar",
          name: "_base",
          stack: "gantt",
          data: baseData,
          itemStyle: { color: "transparent" },
          silent: true,
          emphasis: { disabled: true },
          barWidth: `${taskHeight}%`
        },
        {
          type: "bar",
          name: "Task",
          stack: "gantt",
          data: durationData,
          barWidth: `${taskHeight}%`,
          itemStyle: { borderRadius: cornerRadius },
          label: {
            show: intervalLabels,
            position: "right",
            formatter: (p) => {
              const row = rows[p.dataIndex];
              return row ? formatGanttLabel(row.start, row.end, temporal) : "";
            }
          }
        }
      ]
    };
    if (colorField && groups.length > 1) {
      option.legend = { data: groups, top: 0 };
      option._legendTitle = colorField;
      for (const g of groups) {
        option.series.push({
          type: "bar",
          name: g,
          stack: "gantt",
          data: [],
          barWidth: `${taskHeight}%`,
          itemStyle: { color: groupColor.get(g) }
        });
      }
      option.grid.top = 40;
    }
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  },
  properties: GANTT_PROPERTIES
};

// src/echarts/templates/bullet.ts
var ZONE_GRAYS = ["#e2e2e2", "#ececec", "#f5f5f5"];
var STATUS_COLORS = { below: "#c44e52", met: "#2f855a" };
var STATUS_BELOW = "Below target";
var STATUS_MET = "Meets target";
var ecBulletChartDef = {
  chart: "Bullet Chart",
  template: { mark: "bar", encoding: {} },
  channels: ["y", "x", "goal", "color", "column", "row"],
  markCognitiveChannel: "length",
  declareLayoutMode: () => ({ axisFlags: { y: { banded: true } } }),
  instantiate: (spec, ctx) => {
    const { channelSemantics, table } = ctx;
    const labelField = channelSemantics.y?.field;
    const valueField = channelSemantics.x?.field;
    const goalField = channelSemantics.goal?.field;
    if (!labelField || !valueField || table.length === 0) return;
    const categories = extractCategories(
      table,
      labelField,
      getCategoryOrder(ctx, "y") ?? channelSemantics.y?.ordinalSortOrder
    );
    const byCat = /* @__PURE__ */ new Map();
    for (const r of table) byCat.set(String(r[labelField] ?? ""), r);
    const valueOf = (cat) => Number(byCat.get(cat)?.[valueField]);
    const goalOf = (cat) => goalField != null ? Number(byCat.get(cat)?.[goalField]) : NaN;
    const band1 = [];
    const band2 = [];
    const band3 = [];
    for (const cat of categories) {
      const g = goalOf(cat);
      const q = Number.isFinite(g) && g > 0 ? g / 4 : 0;
      band1.push(q);
      band2.push(q);
      band3.push(q);
    }
    const valueData = categories.map((cat) => {
      const v = valueOf(cat);
      const g = goalOf(cat);
      const met = Number.isFinite(g) ? v >= g : true;
      return {
        value: Number.isFinite(v) ? v : 0,
        itemStyle: { color: met ? STATUS_COLORS.met : STATUS_COLORS.below }
      };
    });
    const band = ctx.layout?.yStep;
    const tickH = band && band > 0 ? Math.min(band, Math.max(8, Math.round(band * 0.72))) : 18;
    const goalData = goalField ? categories.map((cat) => ({ value: [goalOf(cat), cat] })).filter((d) => Number.isFinite(d.value[0])) : [];
    const bandSeries = [band1, band2, band3].map((data, i) => ({
      type: "bar",
      name: `_band${i}`,
      stack: "bullet-bands",
      data,
      barWidth: "62%",
      itemStyle: { color: ZONE_GRAYS[i] },
      silent: true,
      emphasis: { disabled: true },
      z: 1
    }));
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (p) => {
          if (typeof p.seriesName === "string" && p.seriesName.startsWith("_")) return "";
          const cat = categories[p.dataIndex] ?? "";
          const v = valueOf(cat);
          const g = goalOf(cat);
          const goalLine = Number.isFinite(g) ? `<br/>${goalField}: ${g}` : "";
          return `${cat}<br/>${valueField}: ${v}${goalLine}`;
        }
      },
      legend: goalField ? { data: [STATUS_BELOW, STATUS_MET], top: 0 } : void 0,
      grid: { containLabel: true, top: goalField ? 40 : 20 },
      xAxis: {
        type: "value",
        min: 0,
        name: valueField,
        nameLocation: "middle",
        nameGap: 30
      },
      yAxis: {
        type: "category",
        data: categories,
        inverse: true,
        axisTick: { show: false },
        axisLabel: { interval: 0 }
      },
      series: [
        ...bandSeries,
        {
          type: "bar",
          name: "value",
          data: valueData,
          barWidth: "34%",
          barGap: "-100%",
          z: 2
        },
        {
          type: "scatter",
          name: "target",
          data: goalData,
          symbol: "rect",
          symbolSize: [4, tickH],
          itemStyle: { color: "#1a1a1a" },
          z: 4,
          silent: true
        },
        // Legend-only series for the status colors (no bar slots).
        ...goalField ? [
          { type: "scatter", name: STATUS_BELOW, data: [], itemStyle: { color: STATUS_COLORS.below } },
          { type: "scatter", name: STATUS_MET, data: [], itemStyle: { color: STATUS_COLORS.met } }
        ] : []
      ]
    };
    Object.assign(spec, option);
    delete spec.mark;
    delete spec.encoding;
  }
};

// src/echarts/templates/index.ts
var ecTemplateDefs = {
  "Scatter & Point": [ecScatterPlotDef, ecRegressionDef, ecConnectedScatterDef, ecRangedDotPlotDef, ecBoxplotDef, ecStripPlotDef],
  "Bar": [ecBarChartDef, ecGroupedBarChartDef, ecStackedBarChartDef, ecLollipopChartDef, ecPyramidChartDef, ecHeatmapDef, ecCalendarHeatmapDef],
  "Line & Area": [ecLineChartDef, ecBumpChartDef, ecSlopeChartDef, ecAreaChartDef, ecStreamgraphDef, ecRangeAreaChartDef],
  "Part-to-Whole": [ecPieChartDef, ecFunnelChartDef, ecTreemapDef, ecSunburstDef, ecTreeDef],
  "Statistical": [ecHistogramDef, ecDensityPlotDef, ecEcdfPlotDef, ecParallelCoordinatesDef],
  "Financial": [ecCandlestickDef],
  "Other": [ecWaterfallChartDef, ecGanttChartDef, ecBulletChartDef],
  "Polar": [ecRadarChartDef, ecRoseChartDef],
  "Indicator": [ecGaugeChartDef],
  "Flow": [ecSankeyDef, ecGraphDef]
};
var ecAllTemplateDefs = Object.values(ecTemplateDefs).flat();
function ecGetTemplateDef(chartType) {
  return ecAllTemplateDefs.find((t) => t.chart === chartType);
}
function ecGetTemplateChannels(chartType) {
  return ecGetTemplateDef(chartType)?.channels || [];
}

// src/core/filter-overflow.ts
function filterOverflow(channelSemantics, declaration, encodings, data, budgets, allMarkTypes) {
  const effectiveType = (ch) => declaration.resolvedTypes?.[ch] ?? channelSemantics[ch]?.type;
  const effectiveField = (ch) => {
    if (channelSemantics[ch]?.field) return channelSemantics[ch].field;
    return void 0;
  };
  const isDiscreteType = (t) => t === "nominal" || t === "ordinal";
  const nominalCounts = {
    x: 0,
    y: 0,
    column: 0,
    row: 0,
    group: 0
  };
  const truncations = [];
  const warnings = [];
  let filteredData = data;
  const groupField = channelSemantics.group?.field;
  if (groupField) {
    nominalCounts.group = new Set(data.map((r) => r[groupField])).size;
  }
  const strategyContext = {
    data,
    channelSemantics,
    encodings,
    allMarkTypes
  };
  const strategy = declaration.overflowStrategy ?? defaultOverflowStrategy;
  for (const channel of ["x", "y", "column", "row", "color"]) {
    const fieldName = effectiveField(channel);
    const type = effectiveType(channel);
    if (!fieldName) continue;
    const maxToKeep = budgets.maxValues[channel] ?? Infinity;
    if (!isDiscreteType(type)) {
      if (channel === "column" || channel === "row") {
        const uniqueValues2 = [...new Set(filteredData.map((r) => r[fieldName]))];
        nominalCounts[channel] = Math.min(uniqueValues2.length, maxToKeep);
        if (uniqueValues2.length > maxToKeep) {
          const sorted = [...uniqueValues2].sort();
          const valuesToKeep = sorted.slice(0, maxToKeep);
          const omittedCount = uniqueValues2.length - valuesToKeep.length;
          warnings.push({
            severity: "warning",
            code: "overflow",
            message: `${omittedCount} of ${uniqueValues2.length} values in '${fieldName}' were omitted (showing first ${valuesToKeep.length}).`,
            channel,
            field: fieldName
          });
          const keepSet = new Set(valuesToKeep);
          filteredData = filteredData.filter((row) => keepSet.has(row[fieldName]));
        }
      }
      continue;
    }
    const uniqueValues = [...new Set(filteredData.map((r) => r[fieldName]))];
    nominalCounts[channel] = Math.min(uniqueValues.length, maxToKeep);
    if (uniqueValues.length > maxToKeep) {
      const valuesToKeep = strategy(channel, fieldName, uniqueValues, maxToKeep, strategyContext);
      const omittedCount = uniqueValues.length - valuesToKeep.length;
      const placeholder = `...${omittedCount} items omitted`;
      warnings.push({
        severity: "warning",
        code: "overflow",
        message: `${omittedCount} of ${uniqueValues.length} values in '${fieldName}' were omitted (showing first ${valuesToKeep.length} in sort order).`,
        channel,
        field: fieldName
      });
      truncations.push({
        severity: "warning",
        code: "overflow",
        message: `${omittedCount} of ${uniqueValues.length} values in '${fieldName}' were omitted (showing first ${valuesToKeep.length} in sort order).`,
        channel,
        field: fieldName,
        keptValues: valuesToKeep,
        omittedCount,
        placeholder
      });
      if (channel !== "color") {
        filteredData = filteredData.filter((row) => valuesToKeep.includes(row[fieldName]));
      }
    }
  }
  return { filteredData, nominalCounts, truncations, warnings };
}
var defaultOverflowStrategy = (channel, fieldName, uniqueValues, maxToKeep, context) => {
  const { data, channelSemantics, encodings, allMarkTypes } = context;
  const encoding = encodings[channel];
  const sortBy = encoding?.sortBy;
  const sortOrder = encoding?.sortOrder;
  let sortField;
  let sortFieldType;
  let isDescending = false;
  if (sortBy) {
    if (sortBy === "x" || sortBy === "y" || sortBy === "color") {
      const sortCS = channelSemantics[sortBy];
      sortField = sortCS?.field;
      sortFieldType = sortCS?.type;
      isDescending = sortOrder === "descending" || sortOrder !== "ascending" && sortBy !== channel;
    } else {
      try {
        const sortedList = JSON.parse(sortBy);
        if (Array.isArray(sortedList)) {
          const orderedValues = sortOrder === "descending" ? sortedList.reverse() : sortedList;
          return orderedValues.filter((v) => uniqueValues.includes(v)).slice(0, maxToKeep);
        }
      } catch {
      }
      isDescending = sortOrder === "descending";
    }
  }
  if (sortField && sortFieldType === "quantitative") {
    let aggregateOp = Math.max;
    let initialValue = -Infinity;
    if (allMarkTypes.has("bar") && sortField !== channelSemantics.color?.field) {
      aggregateOp = (x, y) => x + y;
      initialValue = 0;
    }
    const valueAggregates = /* @__PURE__ */ new Map();
    for (const row of data) {
      const fieldValue = row[fieldName];
      const sortValue = Number(row[sortField] ?? 0);
      if (valueAggregates.has(fieldValue)) {
        valueAggregates.set(fieldValue, aggregateOp(valueAggregates.get(fieldValue), sortValue));
      } else {
        valueAggregates.set(fieldValue, aggregateOp(initialValue, sortValue));
      }
    }
    return Array.from(valueAggregates.entries()).map(([value, agg]) => ({ value, agg })).sort((a, b) => isDescending ? b.agg - a.agg : a.agg - b.agg).slice(0, maxToKeep).map((v) => v.value);
  }
  const canonicalOrder = channelSemantics[channel]?.ordinalSortOrder;
  if (!sortBy && !sortOrder && canonicalOrder?.length) {
    const present = new Set(uniqueValues);
    const ordered = canonicalOrder.filter((value) => present.has(value));
    const canonicalValues = new Set(ordered);
    ordered.push(...uniqueValues.filter((value) => !canonicalValues.has(value)));
    return ordered.slice(0, maxToKeep);
  }
  const fieldOriginalType = inferVisCategory(data.map((r) => r[fieldName]));
  if (fieldOriginalType === "quantitative" || channel === "color") {
    return [...uniqueValues].sort((a, b) => Number(a) - Number(b)).slice(0, maxToKeep);
  }
  if (channel === "column" || channel === "row") {
    return uniqueValues.slice(0, maxToKeep);
  }
  if (sortOrder === "descending") {
    return [...uniqueValues].sort((a, b) => String(b).localeCompare(String(a), void 0, { numeric: true })).slice(0, maxToKeep);
  }
  if (sortOrder === "ascending") {
    return [...uniqueValues].sort((a, b) => String(a).localeCompare(String(b), void 0, { numeric: true })).slice(0, maxToKeep);
  }
  return uniqueValues.slice(0, maxToKeep);
};

// src/core/compute-layout.ts
var VL_SHORT_DISCRETE_CATEGORY_COUNT = 4;
var VL_SHORT_DISCRETE_LABEL_MAX_LEN = 8;
var APPROX_CHAR_WIDTH_RATIO = 0.62;
function computeDiscreteLabelStats(field, table) {
  if (!field) return null;
  const uniques = /* @__PURE__ */ new Set();
  for (const row of table) {
    const v = row[field];
    if (v == null || v === "") continue;
    uniques.add(String(v));
  }
  if (uniques.size === 0) return null;
  const labels = [...uniques];
  return {
    count: labels.length,
    maxLen: Math.max(...labels.map((s) => s.length)),
    allNumeric: labels.every((s) => s.trim() !== "" && isFinite(Number(s)))
  };
}
function discreteYAxisShouldUseHorizontalLabels(field, channelType, table) {
  if (!field) return false;
  if (channelType === "quantitative") return true;
  const stats = computeDiscreteLabelStats(field, table);
  if (!stats) return false;
  if (stats.count > VL_SHORT_DISCRETE_CATEGORY_COUNT) return false;
  return stats.maxLen <= VL_SHORT_DISCRETE_LABEL_MAX_LEN;
}
function resolveStretchCaps(options) {
  const def = options.maxStretch ?? DEFAULT_MAX_STRETCH;
  return {
    x: Math.max(1, options.maxStretchX ?? def),
    y: Math.max(1, options.maxStretchY ?? def)
  };
}
var DEFAULT_BASE_SIZE = { width: 400, height: 320 };
var DEFAULT_MAX_STRETCH = 1.5;
function resolveBaseSize(specBaseSize, ceiling) {
  const base = specBaseSize ?? { ...DEFAULT_BASE_SIZE };
  if (!ceiling) return { width: base.width, height: base.height };
  return {
    width: Math.min(base.width, ceiling.width),
    height: Math.min(base.height, ceiling.height)
  };
}
function resolveFacetColumnsOption(chartProperties) {
  const raw = chartProperties?.facetColumns;
  if (raw == null) return void 0;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : void 0;
}
function deriveStretchCaps(baseSize, ceiling, options) {
  const def = options.maxStretch ?? DEFAULT_MAX_STRETCH;
  return {
    maxStretchX: ceiling ? Math.max(1, ceiling.width / baseSize.width) : def,
    maxStretchY: ceiling ? Math.max(1, ceiling.height / baseSize.height) : def
  };
}
function computeLayout(channelSemantics, declaration, table, canvasSize, options = {}, facetGrid) {
  const {
    elasticity: elasticityVal = 0.5,
    facetElasticity: facetElasticityVal = 0.3,
    minStep: minStepVal = 6,
    minSubplotSize: minSubplotVal = 60,
    stepPadding: stepPaddingVal = 0.1,
    maintainContinuousAxisRatio = false,
    continuousMarkCrossSection,
    facetAspectRatioResistance = 0
  } = options;
  const { x: maxStretchX, y: maxStretchY } = resolveStretchCaps(options);
  const defaultChartWidth = canvasSize.width;
  const defaultChartHeight = canvasSize.height;
  const fixW = options.facetFixedPadding?.width ?? 0;
  const fixH = options.facetFixedPadding?.height ?? 0;
  const gap = options.facetGap ?? 0;
  const baseRefSize = 300;
  const sizeRatio = Math.max(defaultChartWidth, defaultChartHeight) / baseRefSize;
  const baseBandSize = options.defaultBandSize ?? 20;
  const defaultStepSize = Math.round(baseBandSize * Math.max(1, sizeRatio));
  const maxBandSize = Math.max(baseBandSize, options.maxBandSize ?? baseBandSize);
  const maxStepSize = Math.round(maxBandSize * Math.max(1, sizeRatio));
  const isDiscreteType = (t) => t === "nominal" || t === "ordinal";
  const effectiveTypes = {};
  for (const [ch, cs] of Object.entries(channelSemantics)) {
    effectiveTypes[ch] = declaration.resolvedTypes?.[ch] || cs.type;
  }
  const axisFlags = declaration.axisFlags || {};
  const xBanded = axisFlags.x?.banded ?? false;
  const yBanded = axisFlags.y?.banded ?? false;
  const nominalCount = {
    x: 0,
    y: 0,
    column: 0,
    row: 0,
    group: 0
  };
  for (const channel of ["x", "y", "column", "row", "color"]) {
    const cs = channelSemantics[channel];
    if (!cs?.field) continue;
    const effectiveType = effectiveTypes[channel] || cs.type;
    if (!isDiscreteType(effectiveType)) continue;
    const uniqueValues = [...new Set(table.map((r) => r[cs.field]))];
    nominalCount[channel] = uniqueValues.length;
  }
  let groupField = channelSemantics.group?.field;
  if (!groupField && declaration.colorActsAsGroup) {
    const colorCS = channelSemantics.color;
    const colorType = effectiveTypes.color ?? colorCS?.type;
    const axisField = isDiscreteType(effectiveTypes.x ?? channelSemantics.x?.type) ? channelSemantics.x?.field : channelSemantics.y?.field;
    if (colorCS?.field && isDiscreteType(colorType) && colorCS.field !== axisField) {
      groupField = colorCS.field;
    }
  }
  if (groupField) {
    const groupAxisField = isDiscreteType(effectiveTypes.x ?? channelSemantics.x?.type) ? channelSemantics.x?.field : channelSemantics.y?.field;
    if (groupAxisField === groupField) {
      groupField = void 0;
    } else if (groupAxisField && planBandDodge(table, groupAxisField, groupField).maxPerBand <= 1) {
      groupField = void 0;
    }
  }
  let groupAxis;
  if (groupField) {
    nominalCount.group = declaration.groupLaneCount ?? new Set(table.map((r) => r[groupField])).size;
    if (isDiscreteType(effectiveTypes.x ?? channelSemantics.x?.type)) groupAxis = "x";
    else if (isDiscreteType(effectiveTypes.y ?? channelSemantics.y?.type)) groupAxis = "y";
  }
  const xGroupMultiplier = groupAxis === "x" && nominalCount.group > 1 ? nominalCount.group : 1;
  const yGroupMultiplier = groupAxis === "y" && nominalCount.group > 1 ? nominalCount.group : 1;
  const xTotalNominalCount = nominalCount.x * xGroupMultiplier;
  const yTotalNominalCount = nominalCount.y * yGroupMultiplier;
  const MIN_GROUP_GAP_PX = 3;
  let xContinuousAsDiscrete = 0;
  let yContinuousAsDiscrete = 0;
  for (const axis of ["x", "y"]) {
    const cs = channelSemantics[axis];
    if (!cs?.field) continue;
    const effectiveType = effectiveTypes[axis] || cs.type;
    if (isDiscreteType(effectiveType)) continue;
    const isBanded = axis === "x" ? xBanded : yBanded;
    const isBinned = declaration.binnedAxes?.[axis];
    if (!isBanded && !isBinned) continue;
    let count;
    if (isBinned) {
      const binDef = declaration.binnedAxes[axis];
      count = typeof binDef === "object" && binDef.maxbins ? binDef.maxbins : 10;
    } else {
      count = new Set(table.map((r) => r[cs.field])).size;
    }
    if (count <= 1) continue;
    if (axis === "x") {
      xContinuousAsDiscrete = count;
    } else {
      yContinuousAsDiscrete = count;
    }
  }
  let facetCols = 1;
  let facetRows = 1;
  if (facetGrid) {
    facetCols = facetGrid.columns;
    facetRows = facetGrid.rows;
  } else {
    if (nominalCount.column > 0) facetCols = nominalCount.column;
    if (nominalCount.row > 0) facetRows = nominalCount.row;
  }
  const LOG_PX_PER_DECADE = 40;
  let logBoostX = 0;
  let logBoostY = 0;
  for (const axis of ["x", "y"]) {
    const cs = channelSemantics[axis];
    if (!cs?.field || !cs.scaleType) continue;
    if (cs.scaleType !== "log" && cs.scaleType !== "symlog") continue;
    const vals = table.map((r) => r[cs.field]).filter((v) => typeof v === "number" && v > 0 && isFinite(v));
    if (vals.length < 2) continue;
    const decades = Math.log10(Math.max(...vals)) - Math.log10(Math.min(...vals));
    const needed = Math.ceil(Math.max(1, decades)) * LOG_PX_PER_DECADE;
    if (axis === "x") logBoostX = needed;
    else logBoostY = needed;
  }
  const minContinuousSize = Math.max(10, minStepVal);
  const minContinuousSizeX = Math.max(minContinuousSize, logBoostX);
  const minContinuousSizeY = Math.max(minContinuousSize, logBoostY);
  let subplotWidth;
  if (facetCols > 1) {
    const stretch = Math.min(maxStretchX, Math.pow(facetCols, facetElasticityVal));
    subplotWidth = Math.round(Math.max(
      minContinuousSizeX,
      (defaultChartWidth * stretch - fixW) / facetCols - gap
    ));
  } else {
    subplotWidth = defaultChartWidth;
  }
  let subplotHeight;
  if (facetRows > 1) {
    const stretch = Math.min(maxStretchY, Math.pow(facetRows, facetElasticityVal));
    subplotHeight = Math.round(Math.max(
      minContinuousSizeY,
      (defaultChartHeight * stretch - fixH) / facetRows - gap
    ));
  } else {
    subplotHeight = defaultChartHeight;
  }
  const xIsContinuousNonBanded = xTotalNominalCount === 0 && xContinuousAsDiscrete === 0;
  const yIsContinuousNonBanded = yTotalNominalCount === 0 && yContinuousAsDiscrete === 0;
  const bothContinuousNonBanded = xIsContinuousNonBanded && yIsContinuousNonBanded;
  if (facetAspectRatioResistance > 0 && !bothContinuousNonBanded && (facetCols > 1 || facetRows > 1)) {
    const baseAR = defaultChartWidth / defaultChartHeight;
    const facetAR = subplotWidth / subplotHeight;
    const arDrift = facetAR / baseAR;
    if (arDrift < 1) {
      subplotHeight = Math.round(
        Math.max(minContinuousSizeY, subplotHeight * Math.pow(arDrift, facetAspectRatioResistance))
      );
    } else if (arDrift > 1) {
      subplotWidth = Math.round(
        Math.max(minContinuousSizeX, subplotWidth * Math.pow(1 / arDrift, facetAspectRatioResistance))
      );
    }
  }
  if (bothContinuousNonBanded) {
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    if (xCS?.field && yCS?.field) {
      const isTempX = (effectiveTypes.x || xCS.type) === "temporal";
      const isTempY = (effectiveTypes.y || yCS.type) === "temporal";
      const xNumeric = [];
      const yNumeric = [];
      for (const row of table) {
        let xv = row[xCS.field];
        let yv = row[yCS.field];
        if (xv == null || yv == null) continue;
        if (isTempX) xv = +new Date(xv);
        else xv = +xv;
        if (isTempY) yv = +new Date(yv);
        else yv = +yv;
        if (isNaN(xv) || isNaN(yv)) continue;
        xNumeric.push(xv);
        yNumeric.push(yv);
      }
      if (xNumeric.length > 1) {
        const xMin = Math.min(...xNumeric);
        const xMax = Math.max(...xNumeric);
        const yMin = Math.min(...yNumeric);
        const yMax = Math.max(...yNumeric);
        const xDomain = [xMin, xMax];
        const yDomain = [yMin, yMax];
        if (xCS.zero?.zero) {
          if (xDomain[0] > 0) xDomain[0] = 0;
          if (xDomain[1] < 0) xDomain[1] = 0;
        }
        if (yCS.zero?.zero) {
          if (yDomain[0] > 0) yDomain[0] = 0;
          if (yDomain[1] < 0) yDomain[1] = 0;
        }
        const xDataCoverage = xDomain[1] - xDomain[0] > 0 ? (xMax - xMin) / (xDomain[1] - xDomain[0]) : 1;
        const yDataCoverage = yDomain[1] - yDomain[0] > 0 ? (yMax - yMin) / (yDomain[1] - yDomain[0]) : 1;
        const BANKING_COVERAGE_THRESHOLD = 0.2;
        let gasPressureParams = DEFAULT_GAS_PRESSURE_PARAMS;
        if (continuousMarkCrossSection != null) {
          if (typeof continuousMarkCrossSection === "number") {
            gasPressureParams = { ...DEFAULT_GAS_PRESSURE_PARAMS, markCrossSection: continuousMarkCrossSection };
          } else {
            const maxCS = Math.max(continuousMarkCrossSection.x, continuousMarkCrossSection.y);
            gasPressureParams = {
              ...DEFAULT_GAS_PRESSURE_PARAMS,
              markCrossSection: maxCS,
              markCrossSectionX: continuousMarkCrossSection.x,
              markCrossSectionY: continuousMarkCrossSection.y,
              ...continuousMarkCrossSection.elasticity != null && { elasticity: continuousMarkCrossSection.elasticity },
              ...continuousMarkCrossSection.maxStretch != null && { maxStretch: continuousMarkCrossSection.maxStretch }
            };
            if (continuousMarkCrossSection.seriesCountAxis) {
              const resolvedAxis = continuousMarkCrossSection.seriesCountAxis === "auto" ? "y" : continuousMarkCrossSection.seriesCountAxis;
              const nSeries = countDistinctSeries(channelSemantics, table);
              if (resolvedAxis === "y") {
                gasPressureParams.yItemCountOverride = nSeries;
              } else {
                gasPressureParams.xItemCountOverride = nSeries;
              }
            }
          }
        }
        const perSubplotCanvasW = facetCols > 1 ? Math.max(
          minContinuousSizeX,
          (defaultChartWidth * Math.min(maxStretchX, Math.pow(facetCols, facetElasticityVal)) - fixW) / facetCols - gap
        ) : defaultChartWidth;
        const perSubplotCanvasH = facetRows > 1 ? Math.max(
          minContinuousSizeY,
          (defaultChartHeight * Math.min(maxStretchY, Math.pow(facetRows, facetElasticityVal)) - fixH) / facetRows - gap
        ) : defaultChartHeight;
        const idealResult = computeGasPressure(
          xNumeric,
          yNumeric,
          xDomain,
          yDomain,
          perSubplotCanvasW,
          perSubplotCanvasH,
          gasPressureParams
        );
        const isConnected = typeof continuousMarkCrossSection === "object" && !!continuousMarkCrossSection.seriesCountAxis;
        const useBanking = xDataCoverage >= BANKING_COVERAGE_THRESHOLD && yDataCoverage >= BANKING_COVERAGE_THRESHOLD;
        let idealW;
        let idealH;
        const rawW = perSubplotCanvasW * idealResult.rawStretchX;
        const rawH = perSubplotCanvasH * idealResult.rawStretchY;
        if (useBanking) {
          const seriesFields = [];
          const colorField = channelSemantics.color?.field;
          const detailField = channelSemantics.detail?.field;
          if (colorField) seriesFields.push(colorField);
          if (detailField && detailField !== colorField) seriesFields.push(detailField);
          const perPointSeriesKeys = new Array(xNumeric.length);
          if (seriesFields.length === 0) {
            perPointSeriesKeys.fill("");
          } else {
            let idx = 0;
            for (const row of table) {
              const xv = xCS?.field ? row[xCS.field] : void 0;
              const yv = yCS?.field ? row[yCS.field] : void 0;
              if (xv == null || yv == null) continue;
              const xn = isTempX ? +new Date(xv) : +xv;
              const yn = isTempY ? +new Date(yv) : +yv;
              if (isNaN(xn) || isNaN(yn)) continue;
              perPointSeriesKeys[idx++] = seriesFields.map((f) => String(row[f] ?? "")).join("\0");
            }
          }
          const bankingAR = computeBankingAR(
            xNumeric,
            yNumeric,
            xDomain,
            yDomain,
            perPointSeriesKeys,
            isConnected
          );
          const BANKING_BLEND = 0.5;
          const gasAR = rawW / rawH;
          const blendedAR = gasAR > 0 && bankingAR > 0 ? Math.exp((1 - BANKING_BLEND) * Math.log(gasAR) + BANKING_BLEND * Math.log(bankingAR)) : bankingAR;
          const rawArea = rawW * rawH;
          const maxArea = perSubplotCanvasW * perSubplotCanvasH * Math.max(maxStretchX, maxStretchY);
          const area = Math.min(rawArea, maxArea);
          idealW = Math.sqrt(area * blendedAR);
          idealH = Math.sqrt(area / blendedAR);
        } else {
          idealW = rawW;
          idealH = rawH;
        }
        const availW = facetCols > 1 ? Math.max(minContinuousSizeX, (defaultChartWidth * maxStretchX - fixW) / facetCols - gap) : defaultChartWidth * maxStretchX;
        const availH = facetRows > 1 ? Math.max(minContinuousSizeY, (defaultChartHeight * maxStretchY - fixH) / facetRows - gap) : defaultChartHeight * maxStretchY;
        const scaleX = idealW > availW ? availW / idealW : 1;
        const scaleY = idealH > availH ? availH / idealH : 1;
        const fitScale = Math.min(scaleX, scaleY);
        let finalW = idealW * fitScale;
        let finalH = idealH * fitScale;
        finalW = Math.max(finalW, minContinuousSizeX);
        finalH = Math.max(finalH, minContinuousSizeY);
        subplotWidth = Math.round(finalW);
        subplotHeight = Math.round(finalH);
      }
    }
  } else if (xIsContinuousNonBanded || yIsContinuousNonBanded) {
    const contAxis = xIsContinuousNonBanded ? "x" : "y";
    const otherAxisHasDiscreteItems = contAxis === "x" ? yTotalNominalCount > 0 || yContinuousAsDiscrete > 0 : xTotalNominalCount > 0 || xContinuousAsDiscrete > 0;
    let seriesStretchApplied = false;
    if (typeof continuousMarkCrossSection === "object" && continuousMarkCrossSection.seriesCountAxis) {
      const resolvedAxis = continuousMarkCrossSection.seriesCountAxis === "auto" ? contAxis : continuousMarkCrossSection.seriesCountAxis;
      if (resolvedAxis === contAxis) {
        const sigmaPerSeries = contAxis === "x" ? continuousMarkCrossSection.x : continuousMarkCrossSection.y;
        const baseDim = contAxis === "x" ? subplotWidth : subplotHeight;
        const nSeries = countDistinctSeries(channelSemantics, table);
        const pressure = nSeries * sigmaPerSeries / baseDim;
        const elast = continuousMarkCrossSection.elasticity ?? DEFAULT_GAS_PRESSURE_PARAMS.elasticity;
        const maxS = continuousMarkCrossSection.maxStretch ?? DEFAULT_GAS_PRESSURE_PARAMS.maxStretch;
        if (pressure > 1) {
          const stretch = Math.min(maxS, Math.pow(pressure, elast));
          if (contAxis === "x") {
            subplotWidth = Math.round(subplotWidth * stretch);
          } else {
            subplotHeight = Math.round(subplotHeight * stretch);
          }
        }
        seriesStretchApplied = true;
      }
    }
    if (!seriesStretchApplied && !otherAxisHasDiscreteItems) {
      const contCS = channelSemantics[contAxis];
      if (contCS?.field) {
        const isTemporal2 = (effectiveTypes[contAxis] || contCS.type) === "temporal";
        const contValues = [];
        for (const row of table) {
          let v = row[contCS.field];
          if (v == null) continue;
          if (isTemporal2) v = +new Date(v);
          else v = +v;
          if (!isNaN(v)) contValues.push(v);
        }
        const sigma1d = Math.sqrt(DEFAULT_GAS_PRESSURE_PARAMS.markCrossSection);
        const baseDim = contAxis === "x" ? subplotWidth : subplotHeight;
        const pressure1d = contValues.length * sigma1d / baseDim;
        if (pressure1d > 1) {
          const stretch1d = Math.min(
            DEFAULT_GAS_PRESSURE_PARAMS.maxStretch,
            Math.pow(pressure1d, DEFAULT_GAS_PRESSURE_PARAMS.elasticity)
          );
          if (contAxis === "x") {
            subplotWidth = Math.round(subplotWidth * stretch1d);
          } else {
            subplotHeight = Math.round(subplotHeight * stretch1d);
          }
        }
      }
    }
  }
  const elasticParamsX = {
    elasticity: elasticityVal,
    maxStretch: maxStretchX,
    defaultStepSize};
  const elasticParamsY = {
    elasticity: elasticityVal,
    maxStretch: maxStretchY,
    defaultStepSize};
  const xAxis = computeAxisStep(xTotalNominalCount, xContinuousAsDiscrete, subplotWidth, elasticParamsX);
  const yAxis = computeAxisStep(yTotalNominalCount, yContinuousAsDiscrete, subplotHeight, elasticParamsY);
  const xIsDiscrete = xTotalNominalCount > 0;
  const yIsDiscrete = yTotalNominalCount > 0;
  const xHasGrouping = groupAxis === "x" && nominalCount.group > 0;
  const yHasGrouping = groupAxis === "y" && nominalCount.group > 0;
  let xStepSize;
  let yStepSize;
  let xStepUnit;
  let yStepUnit;
  if (xIsDiscrete && xHasGrouping) {
    const itemsPerGroup = nominalCount.group;
    const defaultGroupStep = itemsPerGroup * maxStepSize;
    const minGroupStep = Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * itemsPerGroup);
    const groupAxis2 = computeAxisStep(nominalCount.x, 0, subplotWidth, elasticParamsX);
    const groupStep = Math.max(minGroupStep, Math.min(defaultGroupStep, groupAxis2.step));
    xStepSize = groupStep;
    xStepUnit = "group";
  } else if (xIsDiscrete) {
    xStepSize = Math.max(minStepVal, Math.min(maxStepSize, xAxis.step));
  } else if (xContinuousAsDiscrete > 0) {
    xStepSize = Math.max(minStepVal, Math.min(maxStepSize, xAxis.step));
  } else {
    xStepSize = defaultStepSize;
  }
  if (yIsDiscrete && yHasGrouping) {
    const itemsPerGroup = nominalCount.group;
    const defaultGroupStep = itemsPerGroup * maxStepSize;
    const minGroupStep = Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * itemsPerGroup);
    const groupAxis2 = computeAxisStep(nominalCount.y, 0, subplotHeight, elasticParamsY);
    const groupStep = Math.max(minGroupStep, Math.min(defaultGroupStep, groupAxis2.step));
    yStepSize = groupStep;
    yStepUnit = "group";
  } else if (yIsDiscrete) {
    yStepSize = Math.max(minStepVal, Math.min(maxStepSize, yAxis.step));
  } else if (yContinuousAsDiscrete > 0) {
    yStepSize = Math.max(minStepVal, Math.min(maxStepSize, yAxis.step));
  } else {
    yStepSize = defaultStepSize;
  }
  for (const axis of ["x", "y"]) {
    const count = axis === "x" ? xContinuousAsDiscrete : yContinuousAsDiscrete;
    if (count <= 0) continue;
    const stepSize = axis === "x" ? xStepSize : yStepSize;
    const continuousSize = Math.round(stepSize * (count + 1));
    if (axis === "x") {
      subplotWidth = continuousSize;
    } else {
      subplotHeight = continuousSize;
    }
  }
  const maxSubplotW = (defaultChartWidth * maxStretchX - fixW) / facetCols - gap;
  const maxSubplotH = (defaultChartHeight * maxStretchY - fixH) / facetRows - gap;
  if (xTotalNominalCount > 0) {
    const divisor = xStepUnit === "group" ? nominalCount.x : xTotalNominalCount;
    const cap = Math.max(minStepVal, Math.floor(maxSubplotW / divisor));
    if (xStepSize > cap) xStepSize = cap;
  }
  if (xContinuousAsDiscrete > 0) {
    const cap = Math.max(minStepVal, Math.floor(maxSubplotW / (xContinuousAsDiscrete + 1)));
    if (xStepSize > cap) xStepSize = cap;
  }
  if (yTotalNominalCount > 0) {
    const divisor = yStepUnit === "group" ? nominalCount.y : yTotalNominalCount;
    const cap = Math.max(minStepVal, Math.floor(maxSubplotH / divisor));
    if (yStepSize > cap) yStepSize = cap;
  }
  if (yContinuousAsDiscrete > 0) {
    const cap = Math.max(minStepVal, Math.floor(maxSubplotH / (yContinuousAsDiscrete + 1)));
    if (yStepSize > cap) yStepSize = cap;
  }
  for (const axis of ["x", "y"]) {
    const count = axis === "x" ? xContinuousAsDiscrete : yContinuousAsDiscrete;
    if (count <= 0) continue;
    const stepSize = axis === "x" ? xStepSize : yStepSize;
    if (axis === "x") subplotWidth = Math.round(stepSize * (count + 1));
    else subplotHeight = Math.round(stepSize * (count + 1));
  }
  subplotWidth = Math.min(subplotWidth, Math.round(maxSubplotW));
  subplotHeight = Math.min(subplotHeight, Math.round(maxSubplotH));
  const targetBandAR = options.targetBandAR;
  if (targetBandAR && targetBandAR > 0) {
    const xIsBanded = xTotalNominalCount > 0 || xContinuousAsDiscrete > 0;
    const yIsBanded = yTotalNominalCount > 0 || yContinuousAsDiscrete > 0;
    if (xIsBanded && !yIsBanded) {
      const actualBandAR = subplotHeight / xStepSize;
      if (actualBandAR > targetBandAR) {
        const idealH = xStepSize * targetBandAR;
        const blendedH = Math.exp(
          0.5 * Math.log(subplotHeight) + 0.5 * Math.log(idealH)
        );
        subplotHeight = Math.round(
          Math.max(minContinuousSizeY, Math.min(blendedH, subplotHeight))
        );
      }
    } else if (yIsBanded && !xIsBanded) {
      const actualBandAR = subplotWidth / yStepSize;
      if (actualBandAR > targetBandAR) {
        const idealW = yStepSize * targetBandAR;
        const blendedW = Math.exp(
          0.5 * Math.log(subplotWidth) + 0.5 * Math.log(idealW)
        );
        subplotWidth = Math.round(
          Math.max(minContinuousSizeX, Math.min(blendedW, subplotWidth))
        );
      }
    }
  }
  const xHasDiscreteItems = xTotalNominalCount > 0 || xContinuousAsDiscrete > 0;
  const yHasDiscreteItems = yTotalNominalCount > 0 || yContinuousAsDiscrete > 0;
  const fontSizing = computeFontSizing(Math.min(subplotWidth, subplotHeight), {
    baseLabelFontSize: options.baseLabelFontSize,
    baseTitleFontSize: options.baseTitleFontSize
  });
  const labelOpts = { baseFont: fontSizing.tickBase, minFont: 6 };
  let xLabel = computeLabelSizing(xStepSize, xHasDiscreteItems, labelOpts);
  let yLabel = computeLabelSizing(yStepSize, yHasDiscreteItems, labelOpts);
  if (xHasDiscreteItems) {
    const xf = channelSemantics.x?.field;
    const xt = effectiveTypes.x || channelSemantics.x?.type;
    const stats = computeDiscreteLabelStats(xf, table);
    if (stats) {
      const numericLike = xt === "quantitative" || stats.allNumeric;
      let labelPx = stats.maxLen * xLabel.fontSize * APPROX_CHAR_WIDTH_RATIO;
      const fewShortStrings = !numericLike && stats.count <= VL_SHORT_DISCRETE_CATEGORY_COUNT && stats.maxLen <= VL_SHORT_DISCRETE_LABEL_MAX_LEN;
      if (fewShortStrings || numericLike && labelPx <= xStepSize) {
        if (labelPx > xStepSize) {
          const desiredStep = Math.ceil(labelPx) + 6;
          const cap = Math.max(minStepVal, Math.floor(maxSubplotW / stats.count));
          if (desiredStep <= cap) {
            xStepSize = Math.max(xStepSize, desiredStep);
            xLabel = computeLabelSizing(xStepSize, xHasDiscreteItems, labelOpts);
            labelPx = stats.maxLen * xLabel.fontSize * APPROX_CHAR_WIDTH_RATIO;
          }
        }
        if (labelPx <= xStepSize) {
          xLabel = {
            ...xLabel,
            labelAngle: 0,
            labelAlign: "center",
            labelBaseline: "top"
          };
        } else {
          xLabel = {
            ...xLabel,
            labelAngle: -45,
            labelAlign: "right",
            labelBaseline: "top"
          };
        }
      } else if (numericLike && labelPx > xStepSize && xLabel.labelAngle === void 0) {
        xLabel = {
          ...xLabel,
          labelAngle: -45,
          labelAlign: "right",
          labelBaseline: "top"
        };
      }
    }
  }
  if (yHasDiscreteItems) {
    const yf = channelSemantics.y?.field;
    const yt = effectiveTypes.y || channelSemantics.y?.type;
    if (discreteYAxisShouldUseHorizontalLabels(yf, yt, table)) {
      yLabel = {
        ...yLabel,
        labelAngle: 0,
        labelAlign: "right",
        labelBaseline: "middle"
      };
    }
  }
  const unifiedTickFont = Math.min(xLabel.fontSize, yLabel.fontSize);
  if (xLabel.fontSize !== unifiedTickFont) xLabel = { ...xLabel, fontSize: unifiedTickFont };
  if (yLabel.fontSize !== unifiedTickFont) yLabel = { ...yLabel, fontSize: unifiedTickFont };
  return {
    subplotWidth,
    subplotHeight,
    xStep: xStepSize,
    yStep: yStepSize,
    xStepUnit,
    yStepUnit,
    xContinuousAsDiscrete,
    yContinuousAsDiscrete,
    xNominalCount: xTotalNominalCount,
    yNominalCount: yTotalNominalCount,
    xLabel,
    yLabel,
    titleFontSize: fontSizing.titleFontSize,
    legendFontSize: fontSizing.legendFontSize,
    stepPadding: stepPaddingVal,
    facet: facetCols > 1 || facetRows > 1 ? {
      columns: facetCols,
      rows: facetRows,
      subplotWidth,
      subplotHeight
    } : void 0,
    effectiveFacetGap: gap,
    truncations: []
    // Overflow truncations are handled by filterOverflow
  };
}
function countDistinctSeries(channelSemantics, data) {
  const seriesFields = [];
  const colorField = channelSemantics.color?.field;
  const detailField = channelSemantics.detail?.field;
  if (colorField) seriesFields.push(colorField);
  if (detailField && detailField !== colorField) seriesFields.push(detailField);
  if (seriesFields.length === 0) return 1;
  const seriesKeys = /* @__PURE__ */ new Set();
  for (const row of data) {
    const key = seriesFields.map((f) => String(row[f] ?? "")).join("\0");
    seriesKeys.add(key);
  }
  return seriesKeys.size;
}
function computeBankingAR(xValues, yValues, xDomain, yDomain, seriesKeys, isConnected) {
  const MIN_AR = 0.5;
  const MAX_AR = 3;
  const xRange = xDomain[1] - xDomain[0];
  const yRange = yDomain[1] - yDomain[0];
  if (xRange <= 0 || yRange <= 0) return 1;
  if (!isConnected) {
    const n = xValues.length;
    let sumX = 0, sumY = 0;
    for (let i = 0; i < n; i++) {
      sumX += (xValues[i] - xDomain[0]) / xRange;
      sumY += (yValues[i] - yDomain[0]) / yRange;
    }
    const meanX = sumX / n;
    const meanY = sumY / n;
    let varX = 0, varY = 0;
    for (let i = 0; i < n; i++) {
      const dx = (xValues[i] - xDomain[0]) / xRange - meanX;
      const dy = (yValues[i] - yDomain[0]) / yRange - meanY;
      varX += dx * dx;
      varY += dy * dy;
    }
    const sdX = Math.sqrt(varX / n);
    const sdY = Math.sqrt(varY / n);
    if (sdY <= 0) return MAX_AR;
    if (sdX <= 0) return MIN_AR;
    const sdRatio = sdX / sdY;
    const ar2 = sdRatio > 1 ? 1 + (sdRatio - 1) * 0.3 : 1 - (1 - sdRatio) * 0.3;
    return Math.min(MAX_AR, Math.max(MIN_AR, ar2));
  }
  const seriesMap = /* @__PURE__ */ new Map();
  for (let i = 0; i < xValues.length; i++) {
    const key = seriesKeys[i];
    let arr = seriesMap.get(key);
    if (!arr) {
      arr = [];
      seriesMap.set(key, arr);
    }
    arr.push({ x: xValues[i], y: yValues[i] });
  }
  for (const pts of seriesMap.values()) {
    pts.sort((a, b) => a.x - b.x);
  }
  const scaleMedians = [];
  let maxSeriesLen = 0;
  for (const pts of seriesMap.values()) {
    if (pts.length > maxSeriesLen) maxSeriesLen = pts.length;
  }
  const maxScale = Math.max(0, Math.floor(Math.log2(maxSeriesLen)) - 1);
  for (let scale = 0; scale <= maxScale; scale++) {
    const windowSize = 1 << scale;
    const absSlopes = [];
    for (const pts of seriesMap.values()) {
      const n = pts.length;
      if (n < 2) continue;
      const smoothed = [];
      for (let i = 0; i < n; i += windowSize) {
        const end = Math.min(i + windowSize, n);
        let sx = 0, sy = 0;
        for (let j = i; j < end; j++) {
          sx += pts[j].x;
          sy += pts[j].y;
        }
        const cnt = end - i;
        smoothed.push({ x: sx / cnt, y: sy / cnt });
      }
      for (let i = 1; i < smoothed.length; i++) {
        const dx = (smoothed[i].x - smoothed[i - 1].x) / xRange;
        const dy = (smoothed[i].y - smoothed[i - 1].y) / yRange;
        if (dx === 0) continue;
        absSlopes.push(Math.abs(dy / dx));
      }
    }
    if (absSlopes.length === 0) continue;
    absSlopes.sort((a, b) => a - b);
    const mid = absSlopes.length >> 1;
    const median = absSlopes.length % 2 === 1 ? absSlopes[mid] : (absSlopes[mid - 1] + absSlopes[mid]) / 2;
    if (median > 0) {
      scaleMedians.push(median);
    }
  }
  if (scaleMedians.length === 0) return 1;
  let logSum = 0;
  for (const m of scaleMedians) {
    logSum += Math.log(m);
  }
  const combinedSlope = Math.exp(logSum / scaleMedians.length);
  if (combinedSlope <= 0) return MAX_AR;
  const ar = Math.max(1, combinedSlope);
  return Math.min(MAX_AR, Math.max(MIN_AR, ar));
}
function computeChannelBudgets(channelSemantics, declaration, data, canvasSize, options) {
  const {
    minStep: minStepVal = 6,
    stepPadding: stepPaddingVal = 0.1,
    maxColorValues: maxColorVal = 24
  } = options;
  const { x: maxStretchX, y: maxStretchY } = resolveStretchCaps(options);
  const fixW = options.facetFixedPadding?.width ?? 0;
  const fixH = options.facetFixedPadding?.height ?? 0;
  const gap = options.facetGap ?? 0;
  const isDiscreteType = (t) => t === "nominal" || t === "ordinal";
  const effectiveType = (ch) => declaration.resolvedTypes?.[ch] ?? channelSemantics[ch]?.type;
  const facetGrid = computeFacetGrid(
    channelSemantics,
    declaration,
    data,
    canvasSize,
    options
  );
  const facetCols = facetGrid?.columns ?? 1;
  const facetRows = facetGrid?.rows ?? 1;
  const maxSubplotW = Math.max(
    options.minSubplotSize ?? 60,
    (canvasSize.width * maxStretchX - fixW) / facetCols - gap
  );
  const maxSubplotH = Math.max(
    options.minSubplotSize ?? 60,
    (canvasSize.height * maxStretchY - fixH) / facetRows - gap
  );
  const groupField = channelSemantics.group?.field;
  let groupCount = 0;
  let groupAxis;
  if (groupField) {
    groupCount = new Set(data.map((r) => r[groupField])).size;
    if (isDiscreteType(effectiveType("x"))) groupAxis = "x";
    else if (isDiscreteType(effectiveType("y"))) groupAxis = "y";
  }
  const xGroupMultiplier = groupAxis === "x" && groupCount > 1 ? groupCount : 1;
  const yGroupMultiplier = groupAxis === "y" && groupCount > 1 ? groupCount : 1;
  const MIN_GROUP_GAP_PX = 3;
  const xMinGroupStep = xGroupMultiplier > 1 ? Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * xGroupMultiplier) : minStepVal;
  const yMinGroupStep = yGroupMultiplier > 1 ? Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * yGroupMultiplier) : minStepVal;
  let maxXToKeep = Math.floor(maxSubplotW / xMinGroupStep);
  let maxYToKeep = Math.floor(maxSubplotH / yMinGroupStep);
  if (facetGrid) {
    const canvasXCap = Math.max(1, Math.floor(canvasSize.width / xMinGroupStep));
    const canvasYCap = Math.max(1, Math.floor(canvasSize.height / yMinGroupStep));
    if (maxXToKeep > canvasXCap || maxYToKeep > canvasYCap) {
      maxXToKeep = Math.min(maxXToKeep, canvasXCap);
      maxYToKeep = Math.min(maxYToKeep, canvasYCap);
      const colField = channelSemantics.column?.field;
      const rowField = channelSemantics.row?.field;
      const colCount = colField ? new Set(data.map((r) => r[colField])).size : 0;
      if (colCount > 1 && !rowField) {
        const tighterW = Math.max(
          options.minSubplotSize ?? 60,
          maxXToKeep * xMinGroupStep
        );
        const totalW = canvasSize.width * maxStretchX - fixW;
        const totalH = canvasSize.height * maxStretchY - fixH;
        const revisedMaxCols = Math.max(1, Math.floor(
          totalW / (tighterW + gap)
        ));
        const revisedMaxRows = Math.max(1, Math.floor(
          totalH / ((options.minSubplotSize ?? 60) + gap)
        ));
        const maxTotal = revisedMaxCols * revisedMaxRows;
        const effectiveCount = Math.min(colCount, maxTotal);
        const visRows = Math.ceil(effectiveCount / revisedMaxCols);
        const visCols = Math.ceil(effectiveCount / visRows);
        facetGrid.columns = visCols;
        facetGrid.rows = visRows;
        facetGrid.maxColumnValues = maxTotal;
      }
    }
  }
  const maxValues = {
    x: maxXToKeep,
    y: maxYToKeep,
    column: facetGrid?.maxColumnValues ?? Infinity,
    row: facetGrid?.maxRowValues ?? Infinity,
    color: maxColorVal
  };
  return { maxValues, facetGrid };
}
function computeFacetGrid(channelSemantics, declaration, data, canvasSize, options) {
  const { x: msX, y: msY } = resolveStretchCaps(options);
  const fixW = options.facetFixedPadding?.width ?? 0;
  const fixH = options.facetFixedPadding?.height ?? 0;
  const gap = options.facetGap ?? 0;
  const minStep = options.minStep ?? 6;
  const stepPadding = options.stepPadding ?? 0.1;
  const baseMinSubplot = options.minSubplotSize ?? 60;
  const isDiscreteType = (t) => t === "nominal" || t === "ordinal";
  const maxW = canvasSize.width * msX - fixW;
  const maxH = canvasSize.height * msY - fixH;
  const MIN_GROUP_GAP_PX = 3;
  const groupField = channelSemantics.group?.field;
  let groupCount = 0;
  let groupAxis;
  if (groupField) {
    groupCount = new Set(data.map((r) => r[groupField])).size;
    const xType = declaration.resolvedTypes?.x ?? channelSemantics.x?.type;
    const yType = declaration.resolvedTypes?.y ?? channelSemantics.y?.type;
    if (isDiscreteType(xType)) groupAxis = "x";
    else if (isDiscreteType(yType)) groupAxis = "y";
  }
  let minSubplotWidth = baseMinSubplot;
  let minSubplotHeight = baseMinSubplot;
  const LOG_PX_PER_DECADE_FACET = 40;
  for (const axis of ["x", "y"]) {
    const cs = channelSemantics[axis];
    if (!cs?.field || !cs.scaleType) continue;
    if (cs.scaleType !== "log" && cs.scaleType !== "symlog") continue;
    const vals = data.map((r) => r[cs.field]).filter((v) => typeof v === "number" && v > 0 && isFinite(v));
    if (vals.length < 2) continue;
    const decades = Math.log10(Math.max(...vals)) - Math.log10(Math.min(...vals));
    const needed = Math.ceil(Math.max(1, decades)) * LOG_PX_PER_DECADE_FACET;
    if (axis === "x") minSubplotWidth = Math.max(minSubplotWidth, needed);
    else minSubplotHeight = Math.max(minSubplotHeight, needed);
  }
  for (const axis of ["x", "y"]) {
    const cs = channelSemantics[axis];
    if (!cs?.field) continue;
    const effectiveType = declaration.resolvedTypes?.[axis] ?? cs.type;
    const isBanded = declaration.axisFlags?.[axis]?.banded === true;
    if (!isDiscreteType(effectiveType) && !isBanded) continue;
    const valueCount = new Set(data.map((r) => r[cs.field])).size;
    const axisGroupCount = groupAxis === axis && groupCount > 1 ? groupCount : 1;
    const maxDim = axis === "x" ? maxW : maxH;
    let perCategoryStep;
    if (axisGroupCount > 1) {
      const minGroupStep = Math.max(
        Math.ceil(MIN_GROUP_GAP_PX / stepPadding),
        2 * axisGroupCount
      );
      perCategoryStep = Math.max(minStep * axisGroupCount, minGroupStep);
    } else {
      perCategoryStep = minStep;
    }
    const dataDrivenMin = Math.min(perCategoryStep * valueCount, maxDim);
    const minDim = Math.max(baseMinSubplot, dataDrivenMin);
    if (axis === "x") {
      minSubplotWidth = minDim;
    } else {
      minSubplotHeight = minDim;
    }
  }
  const xIsCont = (() => {
    const cs = channelSemantics.x;
    if (!cs?.field) return false;
    const t = declaration.resolvedTypes?.x ?? cs.type;
    return !isDiscreteType(t) && !(declaration.axisFlags?.x?.banded === true);
  })();
  const yIsCont = (() => {
    const cs = channelSemantics.y;
    if (!cs?.field) return false;
    const t = declaration.resolvedTypes?.y ?? cs.type;
    return !isDiscreteType(t) && !(declaration.axisFlags?.y?.banded === true);
  })();
  if (xIsCont && yIsCont) {
    const xCS = channelSemantics.x;
    const yCS = channelSemantics.y;
    if (xCS?.field && yCS?.field) {
      const isTempX = (declaration.resolvedTypes?.x ?? xCS.type) === "temporal";
      const isTempY = (declaration.resolvedTypes?.y ?? yCS.type) === "temporal";
      const cmcs = options.continuousMarkCrossSection;
      const isConn = typeof cmcs === "object" && !!cmcs.seriesCountAxis;
      const xNum = [];
      const yNum = [];
      const sKeys = [];
      const sFields = [];
      const colF = channelSemantics.column?.field;
      const rowF = channelSemantics.row?.field;
      if (colF) sFields.push(colF);
      if (rowF) sFields.push(rowF);
      const cf = channelSemantics.color?.field;
      const df = channelSemantics.detail?.field;
      if (cf) sFields.push(cf);
      if (df && df !== cf) sFields.push(df);
      for (const row of data) {
        const xv = row[xCS.field];
        const yv = row[yCS.field];
        if (xv == null || yv == null) continue;
        const xn = isTempX ? +new Date(xv) : +xv;
        const yn = isTempY ? +new Date(yv) : +yv;
        if (isNaN(xn) || isNaN(yn)) continue;
        xNum.push(xn);
        yNum.push(yn);
        sKeys.push(sFields.length > 0 ? sFields.map((f) => String(row[f] ?? "")).join("\0") : "");
      }
      if (xNum.length > 1) {
        const xMin = Math.min(...xNum);
        const xMax = Math.max(...xNum);
        const yMin = Math.min(...yNum);
        const yMax = Math.max(...yNum);
        const xDom = [xMin, xMax];
        const yDom = [yMin, yMax];
        if (xCS.zero?.zero) {
          if (xDom[0] > 0) xDom[0] = 0;
          if (xDom[1] < 0) xDom[1] = 0;
        }
        if (yCS.zero?.zero) {
          if (yDom[0] > 0) yDom[0] = 0;
          if (yDom[1] < 0) yDom[1] = 0;
        }
        const ar = computeBankingAR(xNum, yNum, xDom, yDom, sKeys, isConn);
        if (ar >= 1) {
          minSubplotWidth = Math.max(
            minSubplotWidth,
            Math.round(baseMinSubplot * Math.min(ar, msX))
          );
          minSubplotHeight = Math.max(minSubplotHeight, baseMinSubplot);
        } else {
          minSubplotWidth = Math.max(minSubplotWidth, baseMinSubplot);
          minSubplotHeight = Math.max(
            minSubplotHeight,
            Math.round(baseMinSubplot * Math.min(1 / ar, msY))
          );
        }
      }
    }
  }
  const effectiveW = maxW;
  const effectiveH = maxH;
  const maxFacetColumns = Math.max(1, Math.floor(
    effectiveW / (minSubplotWidth + gap)
  ));
  const maxFacetRows = Math.max(1, Math.floor(
    effectiveH / (minSubplotHeight + gap)
  ));
  const colField = channelSemantics.column?.field;
  const rowField = channelSemantics.row?.field;
  if (!colField && !rowField) return void 0;
  const colCount = colField ? new Set(data.map((r) => r[colField])).size : 0;
  const rowCount = rowField ? new Set(data.map((r) => r[rowField])).size : 0;
  if (colCount === 0 && rowCount === 0) return void 0;
  const forcedCols = options.facetColumns != null && options.facetColumns >= 1 ? Math.min(Math.max(1, Math.floor(options.facetColumns)), Math.max(1, colCount)) : void 0;
  if (colCount > 0 && rowCount === 0) {
    if (forcedCols != null) {
      const nRows2 = Math.ceil(colCount / forcedCols);
      return {
        columns: forcedCols,
        rows: nRows2,
        maxColumnValues: forcedCols * nRows2,
        maxRowValues: Math.max(maxFacetRows, nRows2)
      };
    }
    if (colCount <= maxFacetColumns) {
      return {
        columns: colCount,
        rows: 1,
        maxColumnValues: colCount,
        maxRowValues: maxFacetRows
      };
    }
    let nCols = maxFacetColumns;
    let nRows = Math.ceil(colCount / nCols);
    while (nCols > 2 && colCount % nCols === 1) {
      nCols--;
      nRows = Math.ceil(colCount / nCols);
    }
    const visRows = Math.min(nRows, maxFacetRows);
    const maxTotal = nCols * visRows;
    return {
      columns: nCols,
      rows: visRows,
      maxColumnValues: maxTotal,
      maxRowValues: maxFacetRows
    };
  }
  return {
    columns: Math.max(1, Math.min(colCount, maxFacetColumns)),
    rows: Math.max(1, Math.min(rowCount, maxFacetRows)),
    maxColumnValues: maxFacetColumns,
    maxRowValues: maxFacetRows
  };
}

// src/echarts/facet.ts
function isRadarFacet(ref) {
  return !!(ref?.radar && Array.isArray(ref.series) && ref.series.length > 0 && ref.series[0]?.type === "radar");
}
function isPolarFacet(ref) {
  return !!(ref?.polar && ref?.angleAxis && Array.isArray(ref.series) && ref.series.length > 0 && ref.series[0]?.coordinateSystem === "polar");
}
function combineRadarFacetPanels(panels, config) {
  const nRows = panels.length;
  const nCols = Math.max(1, ...panels.map((r) => r.length));
  const ref = panels[0]?.[0];
  if (!ref?.radar) return {};
  const plotW = ref._plotWidth || ref._width || 400;
  const plotH = ref._plotHeight || ref._height || 300;
  const GAP = 6;
  const COL_HEADER_H = config.colField ? 18 : 0;
  const ROW_HEADER_W = config.rowField ? 18 : 0;
  const colHeaderPerRow = config.colHeaderPerRow ?? false;
  const PAD = 4;
  const baseLeft = ROW_HEADER_W;
  const col0W = PAD + plotW + PAD;
  const colInnerW = PAD + plotW + PAD;
  const rowInnerH = PAD + plotH + PAD;
  const totalW = baseLeft + col0W + (nCols > 1 ? (nCols - 1) * (colInnerW + GAP) : 0);
  const totalH = (colHeaderPerRow ? 0 : COL_HEADER_H) + (nRows > 1 ? (nRows - 1) * (rowInnerH + GAP) : 0) + rowInnerH;
  const combined = {
    radar: [],
    series: [],
    _width: totalW,
    _height: totalH
  };
  if (ref.tooltip) combined.tooltip = { ...ref.tooltip };
  if (ref.color) combined.color = ref.color;
  if (ref.legend) combined.legend = { ...ref.legend };
  const radarRadiusRatio = 0.38;
  const headerFontSize = 11;
  const hStyle = {
    fontSize: headerFontSize,
    fontWeight: "bold",
    fill: "#555",
    textAlign: "center",
    textVerticalAlign: "middle"
  };
  const graphics = [];
  let radarIdx = 0;
  for (let ri = 0; ri < nRows; ri++) {
    for (let ci = 0; ci < nCols; ci++) {
      const panel = panels[ri]?.[ci];
      if (!panel) continue;
      const cx = baseLeft + (ci === 0 ? 0 : col0W + GAP + (ci - 1) * (colInnerW + GAP));
      const cy = colHeaderPerRow ? ri * (rowInnerH + GAP) + COL_HEADER_H : COL_HEADER_H + ri * (rowInnerH + GAP);
      const left = cx + PAD;
      const top = cy + PAD;
      const width = plotW;
      const height = plotH;
      const centerX = left + width / 2;
      const centerY = top + height / 2;
      const radius = Math.min(width, height) * radarRadiusRatio;
      combined.radar.push({
        ...ref.radar,
        indicator: ref.radar.indicator,
        shape: ref.radar.shape,
        center: [centerX, centerY],
        radius,
        axisName: ref.radar.axisName ?? { fontSize: 11 }
      });
      if (Array.isArray(panel.series)) {
        for (const s of panel.series) {
          combined.series.push({ ...s, radarIndex: radarIdx });
        }
      }
      if (config.colField && panel._colHeader && (colHeaderPerRow || ri === 0)) {
        graphics.push({
          type: "text",
          left: centerX,
          top: top - COL_HEADER_H / 2,
          style: { ...hStyle, text: String(panel._colHeader) }
        });
      }
      if (config.rowField && ci === 0 && panel._rowHeader) {
        graphics.push({
          type: "text",
          left: ROW_HEADER_W / 2,
          top: centerY,
          style: { ...hStyle, text: String(panel._rowHeader) },
          rotation: Math.PI / 2
        });
      }
      radarIdx++;
    }
  }
  if (graphics.length > 0) combined.graphic = graphics;
  return combined;
}
var POLAR_RADIUS_RATIO = 0.38;
function combinePolarFacetPanels(panels, config) {
  const nRows = panels.length;
  const nCols = Math.max(1, ...panels.map((r) => r.length));
  const ref = panels[0]?.[0];
  if (!ref?.polar || !ref?.angleAxis) return {};
  const plotW = ref._plotWidth || ref._width || 400;
  const plotH = ref._plotHeight || ref._height || 300;
  const GAP = 14;
  const COL_HEADER_H = config.colField ? 18 : 0;
  const ROW_HEADER_W = config.rowField ? 18 : 0;
  const colHeaderPerRow = config.colHeaderPerRow ?? false;
  const PAD = 4;
  const baseLeft = ROW_HEADER_W;
  const col0W = PAD + plotW + PAD;
  const colInnerW = PAD + plotW + PAD;
  const rowInnerH = PAD + plotH + PAD;
  const totalW = baseLeft + col0W + (nCols > 1 ? (nCols - 1) * (colInnerW + GAP) : 0);
  const totalH = (colHeaderPerRow ? 0 : COL_HEADER_H) + (nRows > 1 ? (nRows - 1) * (rowInnerH + GAP) : 0) + rowInnerH;
  const combined = {
    polar: [],
    angleAxis: [],
    radiusAxis: [],
    series: [],
    _width: totalW,
    _height: totalH
  };
  if (ref.tooltip) combined.tooltip = { ...ref.tooltip };
  if (ref.color) combined.color = ref.color;
  if (ref.legend) combined.legend = { ...ref.legend };
  const headerFontSize = 11;
  const hStyle = {
    fontSize: headerFontSize,
    fontWeight: "bold",
    fill: "#555",
    textAlign: "center",
    textVerticalAlign: "middle"
  };
  const graphics = [];
  let polarIdx = 0;
  for (let ri = 0; ri < nRows; ri++) {
    for (let ci = 0; ci < nCols; ci++) {
      const panel = panels[ri]?.[ci];
      if (!panel) continue;
      const cx = baseLeft + (ci === 0 ? 0 : col0W + GAP + (ci - 1) * (colInnerW + GAP));
      const cy = colHeaderPerRow ? ri * (rowInnerH + GAP) + COL_HEADER_H : COL_HEADER_H + ri * (rowInnerH + GAP);
      const left = cx + PAD;
      const top = cy + PAD;
      const width = plotW;
      const height = plotH;
      const centerX = left + width / 2;
      const centerY = top + height / 2;
      const radius = Math.min(width, height) * POLAR_RADIUS_RATIO;
      combined.polar.push({
        center: [centerX, centerY],
        radius
      });
      combined.angleAxis.push({
        ...ref.angleAxis,
        polarIndex: polarIdx
      });
      combined.radiusAxis.push({
        ...ref.radiusAxis || {},
        polarIndex: polarIdx
      });
      if (Array.isArray(panel.series)) {
        for (const s of panel.series) {
          if (s && s.coordinateSystem === "polar") {
            combined.series.push({ ...s, polarIndex: polarIdx });
          } else {
            combined.series.push({ ...s });
          }
        }
      }
      if (config.colField && panel._colHeader && (colHeaderPerRow || ri === 0)) {
        graphics.push({
          type: "text",
          left: centerX,
          top: top - COL_HEADER_H / 2,
          style: { ...hStyle, text: String(panel._colHeader) }
        });
      }
      if (config.rowField && ci === 0 && panel._rowHeader) {
        graphics.push({
          type: "text",
          left: ROW_HEADER_W / 2,
          top: centerY,
          style: { ...hStyle, text: String(panel._rowHeader) },
          rotation: Math.PI / 2
        });
      }
      polarIdx++;
    }
  }
  if (graphics.length > 0) combined.graphic = graphics;
  repositionFacetedPolarLegend(combined);
  return combined;
}
function ecCombineFacetPanels(panels, config) {
  const nRows = panels.length;
  const nCols = Math.max(1, ...panels.map((r) => r.length));
  const ref = panels[0]?.[0];
  if (!ref) return {};
  if (isRadarFacet(ref)) {
    return combineRadarFacetPanels(panels, config);
  }
  if (isPolarFacet(ref)) {
    return combinePolarFacetPanels(panels, config);
  }
  const plotW = ref._plotWidth || ref._width || 200;
  const plotH = ref._plotHeight || ref._height || 150;
  const GAP = 6;
  const COL_HEADER_H = config.colField ? 18 : 0;
  const ROW_HEADER_W = config.rowField ? 18 : 0;
  const colHeaderPerRow = config.colHeaderPerRow ?? false;
  const refX = ref.xAxis || {};
  const refY = ref.yAxis || {};
  const hasYTitle = !!refY.name;
  const sharedXTitle = refX.name || "";
  const sharedYTitle = config.rowField && hasYTitle ? refY.name || "" : "";
  const SHARED_X_H = sharedXTitle ? 18 : 0;
  const SHARED_Y_W = sharedYTitle ? 18 : 0;
  const mLeft = hasYTitle && !sharedYTitle ? 55 : 35;
  const mBottom = 22;
  const PAD = 4;
  const col0W = mLeft + plotW + PAD;
  const colInnerW = PAD + plotW + PAD;
  const rowInnerH = PAD + plotH + PAD;
  const rowBottomH = PAD + plotH + mBottom;
  const baseLeft = SHARED_Y_W + ROW_HEADER_W;
  const innerRowBlock = colHeaderPerRow ? COL_HEADER_H + rowInnerH : rowInnerH;
  const bottomRowBlock = colHeaderPerRow ? COL_HEADER_H + rowBottomH : rowBottomH;
  const totalW = baseLeft + col0W + (nCols > 1 ? (nCols - 1) * (colInnerW + GAP) : 0);
  const totalH = (colHeaderPerRow ? 0 : COL_HEADER_H) + (nRows > 1 ? (nRows - 1) * (innerRowBlock + GAP) : 0) + bottomRowBlock + SHARED_X_H;
  const combined = {
    grid: [],
    xAxis: [],
    yAxis: [],
    series: [],
    _width: totalW,
    _height: totalH
  };
  if (ref.tooltip) combined.tooltip = { ...ref.tooltip };
  if (ref.color) combined.color = ref.color;
  if (ref.legend) combined.legend = { ...ref.legend };
  const fontSize = Math.max(8, Math.round(10 * Math.min(1, plotW / 200)));
  const headerFontSize = Math.max(9, Math.round(11 * Math.min(1, plotW / 200)));
  const gridMap = [];
  let gridIdx = 0;
  for (let ri = 0; ri < nRows; ri++) {
    gridMap[ri] = [];
    for (let ci = 0; ci < nCols; ci++) {
      const panel = panels[ri]?.[ci];
      if (!panel) {
        gridMap[ri][ci] = -1;
        continue;
      }
      gridMap[ri][ci] = gridIdx;
      const isLeft = ci === 0;
      const isBottom = ri === nRows - 1;
      const cx = baseLeft + (ci === 0 ? 0 : col0W + GAP + (ci - 1) * (colInnerW + GAP));
      let cy;
      if (colHeaderPerRow) {
        const rowOff = ri * (innerRowBlock + GAP);
        cy = rowOff + COL_HEADER_H;
      } else {
        const rowOff = COL_HEADER_H + ri * (innerRowBlock + GAP);
        cy = rowOff;
      }
      const pLeft = ci === 0 ? mLeft : PAD;
      combined.grid.push({
        left: cx + pLeft,
        top: cy + PAD,
        width: plotW,
        height: plotH
      });
      const srcX = panel.xAxis ? { ...panel.xAxis } : { type: "category" };
      combined.xAxis.push({
        ...srcX,
        gridIndex: gridIdx,
        name: void 0,
        nameGap: 0,
        axisLabel: { ...srcX.axisLabel || {}, show: isBottom, fontSize },
        axisTick: { ...srcX.axisTick || {}, show: isBottom },
        axisLine: { show: true }
      });
      const srcY = panel.yAxis ? { ...panel.yAxis } : { type: "value" };
      const showYName = isLeft && !sharedYTitle;
      combined.yAxis.push({
        ...srcY,
        gridIndex: gridIdx,
        name: showYName ? srcY.name : void 0,
        nameGap: showYName ? srcY.nameGap ?? 4 : 0,
        axisLabel: { ...srcY.axisLabel || {}, show: isLeft, fontSize },
        axisTick: { ...srcY.axisTick || {}, show: isLeft },
        axisLine: { show: true }
      });
      if (Array.isArray(panel.series)) {
        for (const s of panel.series) {
          combined.series.push({ ...s, xAxisIndex: gridIdx, yAxisIndex: gridIdx });
        }
      }
      gridIdx++;
    }
  }
  const gridOf = (ri, ci) => {
    const gi = gridMap[ri]?.[ci];
    return gi != null && gi >= 0 ? combined.grid[gi] : null;
  };
  const gCX = (g) => g.left + g.width / 2;
  const gCY = (g) => g.top + g.height / 2;
  const graphics = [];
  const hStyle = {
    fontSize: headerFontSize,
    fontWeight: "bold",
    fill: "#555",
    textAlign: "center",
    textVerticalAlign: "middle"
  };
  if (config.colField) {
    const hRows = colHeaderPerRow ? nRows : 1;
    for (let ri = 0; ri < hRows; ri++) {
      for (let ci = 0; ci < nCols; ci++) {
        const p = panels[ri]?.[ci], g = gridOf(ri, ci);
        if (!p?._colHeader || !g) continue;
        graphics.push({
          type: "text",
          left: gCX(g),
          top: g.top - COL_HEADER_H / 2,
          style: { ...hStyle, text: String(p._colHeader) }
        });
      }
    }
  }
  if (config.rowField) {
    for (let ri = 0; ri < nRows; ri++) {
      const p = panels[ri]?.[0], g = gridOf(ri, 0);
      if (!p?._rowHeader || !g) continue;
      graphics.push({
        type: "text",
        left: SHARED_Y_W + ROW_HEADER_W / 2,
        top: gCY(g),
        style: { ...hStyle, text: String(p._rowHeader) },
        rotation: Math.PI / 2
      });
    }
  }
  if (sharedYTitle) {
    const first = gridOf(0, 0), last = gridOf(nRows - 1, 0);
    if (first && last) {
      graphics.push({
        type: "text",
        left: SHARED_Y_W / 2,
        top: (gCY(first) + gCY(last)) / 2,
        style: {
          text: sharedYTitle,
          fontSize: headerFontSize,
          fill: "#333",
          textAlign: "center",
          textVerticalAlign: "middle"
        },
        rotation: Math.PI / 2
      });
    }
  }
  if (sharedXTitle) {
    graphics.push({
      type: "text",
      left: totalW / 2,
      top: totalH - SHARED_X_H + 4,
      style: { text: sharedXTitle, fontSize: headerFontSize, fill: "#333", textAlign: "center" }
    });
  }
  if (graphics.length > 0) combined.graphic = graphics;
  repositionFacetedLegendBesideGrids(combined);
  return combined;
}
function repositionFacetedLegendBesideGrids(combined) {
  const grids = combined.grid;
  if (!combined.legend || !Array.isArray(grids) || grids.length <= 1) return;
  const rawData = combined.legend.data || [];
  const legendLabels = rawData.map((d) => typeof d === "string" ? d : d?.name ?? "");
  if (legendLabels.length === 0) return;
  const maxLabelLen = Math.max(...legendLabels.map((l) => l.length), 3);
  const highCardinality = legendLabels.length >= 16;
  const legendSymbolWidth = highCardinality ? 12 : 14;
  const legendItemGap = 5;
  const estimatedTextWidth = Math.min(120, maxLabelLen * 7 + 30);
  const legendW = legendSymbolWidth + legendItemGap + estimatedTextWidth;
  const GAP = 12;
  const BUFFER = 16;
  const rightMost = Math.max(...grids.map((g) => (g.left ?? 0) + (g.width ?? 0)));
  combined.legend = {
    ...combined.legend,
    left: rightMost + GAP,
    top: combined.legend.top ?? 20,
    orient: combined.legend.orient || "vertical",
    align: "left",
    right: void 0,
    textStyle: {
      fontSize: highCardinality ? 8 : 11,
      ...combined.legend.textStyle || {}
    },
    ...legendLabels.length > 10 ? { type: "scroll" } : {},
    ...highCardinality ? { itemWidth: 12, itemHeight: 12 } : {}
  };
  combined._width = Math.max(combined._width || 0, rightMost + GAP + legendW + BUFFER);
}
function repositionFacetedPolarLegend(combined) {
  const polars = combined.polar;
  if (!combined.legend || !Array.isArray(polars) || polars.length <= 1) return;
  const rawData = combined.legend.data || [];
  const legendLabels = rawData.map((d) => typeof d === "string" ? d : d?.name ?? "");
  if (legendLabels.length === 0) return;
  const maxLabelLen = Math.max(...legendLabels.map((l) => l.length), 3);
  const highCardinality = legendLabels.length >= 16;
  const legendSymbolWidth = highCardinality ? 12 : 14;
  const legendItemGap = 5;
  const estimatedTextWidth = Math.min(120, maxLabelLen * 7 + 30);
  const legendW = legendSymbolWidth + legendItemGap + estimatedTextWidth;
  const GAP = 15;
  const BUFFER = 16;
  const rightMost = Math.max(...polars.map((p) => {
    const cx = Array.isArray(p?.center) ? Number(p.center[0]) : 0;
    const r = Number(p?.radius) || 0;
    return cx + r;
  }));
  combined.legend = {
    ...combined.legend,
    left: rightMost + GAP,
    top: combined.legend.top ?? 20,
    orient: combined.legend.orient || "vertical",
    align: "left",
    right: void 0,
    textStyle: {
      fontSize: highCardinality ? 8 : 11,
      ...combined.legend.textStyle || {}
    },
    ...legendLabels.length > 10 ? { type: "scroll" } : {},
    ...highCardinality ? { itemWidth: 12, itemHeight: 12 } : {}
  };
  combined._width = Math.max(combined._width || 0, rightMost + GAP + legendW + BUFFER);
}

// src/core/color-decisions.ts
function inferColorChannelPrimary(channel, chartType) {
  if (channel === "color" || channel === "group") return true;
  return false;
}
function decideSchemeTypeFromChannel(channel, cs) {
  const hint = cs?.colorScheme;
  if (hint) {
    if (hint.type === "diverging") {
      return {
        schemeType: "diverging",
        // resolve-semantics 里用 domainMid 表示 diverging 中点
        divergingMidpoint: hint.domainMid
      };
    }
    if (hint.type === "sequential") {
      return { schemeType: "sequential" };
    }
    if (hint.type === "categorical") {
      const semType2 = cs?.semanticAnnotation?.semanticType;
      const isRankLike = semType2 === "Rank";
      if (isRankLike) {
        return { schemeType: "sequential" };
      }
      if (cs?.type === "temporal" && channel === "color") {
        return { schemeType: "sequential" };
      }
      return { schemeType: "categorical" };
    }
  }
  const encType = cs?.type;
  const semType = cs?.semanticAnnotation?.semanticType;
  if (semType === "Correlation") {
    return { schemeType: "diverging", divergingMidpoint: 0 };
  }
  if (encType === "quantitative" || encType === "temporal") {
    return { schemeType: "sequential" };
  }
  return { schemeType: "categorical" };
}
function countDistinctValues(table, field) {
  if (!field) return void 0;
  const set = /* @__PURE__ */ new Set();
  for (const row of table) {
    if (row == null) continue;
    set.add(row[field]);
  }
  return set.size;
}
function decideColorForChannel(channel, ctx) {
  const encoding = ctx.encodings[channel];
  const cs = ctx.channelSemantics[channel];
  if (!encoding || !cs?.field) return void 0;
  const dataDriven = true;
  const primary = inferColorChannelPrimary(channel);
  if (encoding.scheme && encoding.scheme !== "default") {
    const distinct2 = countDistinctValues(ctx.table, cs.field);
    const { schemeType: schemeType2 } = decideSchemeTypeFromChannel(channel, cs);
    return {
      channel,
      schemeType: schemeType2,
      schemeId: encoding.scheme,
      categoryCount: distinct2,
      primary,
      dataDriven
    };
  }
  const { schemeType, divergingMidpoint } = decideSchemeTypeFromChannel(channel, cs);
  const distinct = countDistinctValues(ctx.table, cs.field);
  return {
    channel,
    schemeType,
    divergingMidpoint,
    categoryCount: distinct,
    primary,
    dataDriven
  };
}
function decideColorMaps(ctx) {
  const result = {
    color: void 0,
    group: void 0,
    fill: void 0,
    stroke: void 0
  };
  const channels = ["color", "group"];
  for (const ch of channels) {
    const decision = decideColorForChannel(ch, ctx);
    if (decision) {
      result[ch] = decision;
    }
  }
  return result;
}

// src/core/static-series.ts
var STATIC_SERIES_KEY_COLUMN = "__flint_series_key";
var STATIC_SERIES_VALUE_COLUMN = "__flint_series_value";
var MEASURE_CHANNELS = /* @__PURE__ */ new Set(["x", "y"]);
function coerceEncodingValue(value) {
  if (typeof value === "string") {
    return { field: value };
  }
  if (Array.isArray(value)) {
    return value.map((entry) => typeof entry === "string" ? { field: entry } : entry);
  }
  return value;
}
function normalizeEncodingShorthand(encodings) {
  const out = {};
  for (const [channel, value] of Object.entries(encodings)) {
    out[channel] = coerceEncodingValue(value);
  }
  return out;
}
function normalizeStaticSeries(rawEncodings, data, semanticTypes) {
  const encodings = normalizeEncodingShorthand(rawEncodings);
  const arrayChannels = [];
  for (const [channel2, enc] of Object.entries(encodings)) {
    if (Array.isArray(enc)) {
      arrayChannels.push({ channel: channel2, entries: enc });
    }
  }
  if (arrayChannels.length === 0) {
    return {
      encodings,
      data
    };
  }
  if (arrayChannels.length > 1) {
    const channelNames = arrayChannels.map((c) => c.channel).join(", ");
    throw new Error(
      `Static series (array encoding) found on multiple channels: ${channelNames}. Only one channel may use array encoding at a time.`
    );
  }
  const { channel, entries } = arrayChannels[0];
  if (!MEASURE_CHANNELS.has(channel)) {
    throw new Error(
      `Static series (array encoding) is only allowed on measure channels (${[...MEASURE_CHANNELS].join(", ")}), not "${channel}".`
    );
  }
  if (entries.length < 2) {
    throw new Error(
      `Static series requires at least 2 fields, got ${entries.length} on channel "${channel}".`
    );
  }
  const fields = [];
  for (const entry of entries) {
    if (!entry.field) {
      throw new Error(
        `Each static series entry must have a "field" property.`
      );
    }
    fields.push(entry.field);
  }
  const fieldSet = new Set(fields);
  if (fieldSet.size !== fields.length) {
    throw new Error(
      `Static series contains duplicate fields. Each field must be unique.`
    );
  }
  if (data.length > 0) {
    const dataColumns = new Set(Object.keys(data[0]));
    for (const field of fields) {
      if (!dataColumns.has(field)) {
        throw new Error(
          `Static series field "${field}" not found in data columns. Available columns: ${[...dataColumns].join(", ")}`
        );
      }
    }
  }
  for (const entry of entries) {
    const field = entry.field;
    const explicitType = entry.type;
    if (explicitType === "nominal" || explicitType === "ordinal") {
      throw new Error(
        `Static series field "${field}" has type "${explicitType}" \u2014 only quantitative or temporal fields are allowed in static series.`
      );
    }
    if (!explicitType && data.length > 0) {
      const semType = semanticTypes[field];
      const semTypeStr = typeof semType === "string" ? semType : semType?.semanticType || "";
      const fromRegistry = semTypeStr ? getVisCategory(semTypeStr) : null;
      const inferred = fromRegistry ?? inferVisCategory(data.map((r) => r[field]));
      if (inferred === "nominal" || inferred === "ordinal") {
        throw new Error(
          `Static series field "${field}" infers as "${inferred}" from data \u2014 only quantitative or temporal fields are allowed in static series.`
        );
      }
    }
  }
  const colorEnc = encodings.color;
  if (colorEnc && !Array.isArray(colorEnc) && colorEnc.field) {
    throw new Error(
      `Cannot use static series on "${channel}" when the color channel is already bound to field "${colorEnc.field}". Static series implicitly uses the color channel for series discrimination.`
    );
  }
  const foldedData = foldData(data, fields);
  const normalizedEncodings = {};
  for (const [ch, enc] of Object.entries(encodings)) {
    if (ch === channel) {
      normalizedEncodings[ch] = { field: STATIC_SERIES_VALUE_COLUMN, type: "quantitative" };
    } else if (Array.isArray(enc)) {
      normalizedEncodings[ch] = enc[0];
    } else {
      normalizedEncodings[ch] = enc;
    }
  }
  const colorScheme = !Array.isArray(colorEnc) && colorEnc?.scheme ? colorEnc.scheme : void 0;
  normalizedEncodings.color = {
    field: STATIC_SERIES_KEY_COLUMN,
    type: "nominal",
    ...colorScheme ? { scheme: colorScheme } : {}
  };
  const metadata = {
    channel,
    fields,
    keyColumn: STATIC_SERIES_KEY_COLUMN,
    valueColumn: STATIC_SERIES_VALUE_COLUMN
  };
  return {
    encodings: normalizedEncodings,
    data: foldedData,
    staticSeries: metadata
  };
}
function foldData(data, fields) {
  const fieldSet = new Set(fields);
  const result = [];
  for (const row of data) {
    const baseRow = {};
    for (const [key, value] of Object.entries(row)) {
      if (!fieldSet.has(key)) {
        baseRow[key] = value;
      }
    }
    for (const field of fields) {
      const value = row[field];
      if (value == null) continue;
      result.push({
        ...baseRow,
        [STATIC_SERIES_KEY_COLUMN]: field,
        [STATIC_SERIES_VALUE_COLUMN]: value
      });
    }
  }
  return result;
}

// src/core/normalize-properties.ts
function normalizeChartProperties(properties, chartProperties) {
  const warnings = [];
  if (!properties || !chartProperties) {
    return { chartProperties, warnings };
  }
  let result;
  const ensureCopy = () => {
    if (!result) result = { ...chartProperties };
    return result;
  };
  for (const def of properties) {
    if (def.type !== "discrete") continue;
    if (!(def.key in chartProperties)) continue;
    const value = chartProperties[def.key];
    if (value == null) continue;
    if (def.options.some((o) => o.value === value)) continue;
    const byLabel = typeof value === "string" ? def.options.find(
      (o) => o.label != null && o.label.toLowerCase() === value.trim().toLowerCase()
    ) : void 0;
    if (byLabel) {
      ensureCopy()[def.key] = byLabel.value;
      warnings.push({
        severity: "info",
        code: "coerced-option-label",
        message: `chartProperties.${def.key}: '${value}' is a display label; using the accepted value '${byLabel.value}' instead.`
      });
      continue;
    }
    const accepted = def.options.map((o) => o.value == null ? "(default)" : `'${o.value}'`).join(", ");
    const copy = ensureCopy();
    delete copy[def.key];
    warnings.push({
      severity: "warning",
      code: "invalid-option-value",
      message: `chartProperties.${def.key}: '${value}' is not a valid option (accepted: ${accepted}). Falling back to the default.`
    });
  }
  return { chartProperties: result ?? chartProperties, warnings };
}

// src/echarts/assemble.ts
function assembleECharts(input) {
  const chartType = input.chart_spec.chartType;
  const semanticTypes = input.semantic_types ?? {};
  const sizeCeiling = input.chart_spec.canvasSize;
  const baseSize = resolveBaseSize(input.chart_spec.baseSize, sizeCeiling);
  const canvasSize = baseSize;
  const options = input.options ?? {};
  let chartTemplate = ecGetTemplateDef(chartType);
  if (!chartTemplate) {
    throw new Error(`Unknown ECharts chart type: ${chartType}. Use ecAllTemplateDefs to see available types.`);
  }
  const warnings = [];
  const normalizedProps = normalizeChartProperties(
    chartTemplate.properties,
    input.chart_spec.chartProperties
  );
  const chartProperties = normalizedProps.chartProperties;
  warnings.push(...normalizedProps.warnings);
  const rawData = input.data.values ?? [];
  const normalized = normalizeStaticSeries(
    input.chart_spec.encodings,
    rawData,
    semanticTypes
  );
  let data = normalized.data;
  const staticSeries = normalized.staticSeries;
  const prelimConvertedData = convertTemporalData(data, semanticTypes);
  const prelimSemantics = resolveChannelSemantics(
    normalized.encodings,
    data,
    semanticTypes,
    prelimConvertedData
  );
  const typedRawEncodings = {};
  for (const [ch, enc] of Object.entries(normalized.encodings)) {
    typedRawEncodings[ch] = enc.type ? enc : { ...enc, type: prelimSemantics[ch]?.type };
  }
  const authoredTemplate = chartTemplate;
  const transformed = applyTransform(chartTemplate, typedRawEncodings, data, chartProperties, ecGetTemplateDef);
  if (transformed.chartType && transformed.chartType !== chartType) {
    const swapped = ecGetTemplateDef(transformed.chartType);
    if (swapped) chartTemplate = swapped;
  }
  const encodings = applyEncodingOverrides(chartTemplate, transformed.encodings, chartProperties);
  data = applyAggregation(encodings, data);
  const tplMark = chartTemplate.template?.mark;
  const templateMarkType = typeof tplMark === "string" ? tplMark : tplMark?.type;
  const convertedData = convertTemporalData(data, semanticTypes);
  const channelSemantics = resolveChannelSemantics(
    encodings,
    data,
    semanticTypes,
    convertedData
  );
  const effectiveMarkType = templateMarkType || "point";
  for (const [channel, cs] of Object.entries(channelSemantics)) {
    if ((channel === "x" || channel === "y") && cs.type === "quantitative") {
      const numericValues = data.map((r) => r[cs.field]).filter((v) => v != null && typeof v === "number" && !isNaN(v));
      cs.zero = computeZeroDecision(
        cs.semanticAnnotation.semanticType,
        channel,
        effectiveMarkType,
        numericValues
      );
    }
  }
  const declaration = chartTemplate.declareLayoutMode ? chartTemplate.declareLayoutMode(channelSemantics, data, chartProperties) : {};
  const effectiveOptions = {
    // ECharts uses step × itemCount for discrete canvas sizing (like VL),
    // but adds ~120-160px grid margins on top.  A 24px base band gives
    // bars close to ECharts's native auto-sizing at typical category counts.
    defaultBandSize: 24,
    // ECharts fills its grid natively (bars sized by barCategoryGap), so
    // sparse categories spread out. Allow bands to expand past the base to
    // match that, capped so a couple of bars don't span the whole canvas.
    maxBandSize: 100,
    // ECharts native font defaults (labels, titles, legend all 12).
    baseLabelFontSize: 12,
    baseTitleFontSize: 12,
    ...options,
    ...declaration.paramOverrides || {}
  };
  if (effectiveOptions.facetFixedPadding == null) {
    effectiveOptions.facetFixedPadding = { width: 55, height: 22 };
  }
  if (effectiveOptions.facetGap == null) {
    effectiveOptions.facetGap = 14;
  }
  Object.assign(effectiveOptions, deriveStretchCaps(baseSize, sizeCeiling, effectiveOptions));
  effectiveOptions.facetColumns = resolveFacetColumnsOption(input.chart_spec.chartProperties);
  const {
    addTooltips: addTooltipsOpt = true
  } = effectiveOptions;
  const allMarkTypes = /* @__PURE__ */ new Set();
  if (templateMarkType) allMarkTypes.add(templateMarkType);
  const budgets = computeChannelBudgets(
    channelSemantics,
    declaration,
    convertedData,
    canvasSize,
    effectiveOptions
  );
  const facetGridResult = budgets.facetGrid;
  const overflowResult = filterOverflow(
    channelSemantics,
    declaration,
    encodings,
    convertedData,
    budgets,
    allMarkTypes
  );
  let values = overflowResult.filteredData;
  warnings.push(...overflowResult.warnings);
  const layoutResult = computeLayout(
    channelSemantics,
    declaration,
    values,
    canvasSize,
    effectiveOptions,
    facetGridResult
  );
  layoutResult.truncations = overflowResult.truncations;
  const resolvedEncodings = buildECEncodings(
    encodings,
    channelSemantics,
    declaration,
    values,
    canvasSize,
    semanticTypes,
    templateMarkType,
    chartTemplate
  );
  const colorDecisions = decideColorMaps({
    encodings,
    channelSemantics,
    table: values});
  const instantiateContext = {
    channelSemantics,
    layout: layoutResult,
    table: values,
    fullTable: convertedData,
    resolvedEncodings,
    encodings,
    chartProperties,
    staticSeries,
    canvasSize,
    semanticTypes,
    chartType,
    assembleOptions: effectiveOptions,
    colorDecisions
  };
  const colField = channelSemantics.column?.field;
  const rowField = channelSemantics.row?.field;
  const hasFacet = !!(colField || rowField);
  const hasAxes = chartTemplate.channels.includes("x") || chartTemplate.channels.includes("y");
  let ecOption;
  if (hasFacet && hasAxes) {
    const maxFacetCols = facetGridResult?.columns ?? 1;
    const maxFacetRows = facetGridResult?.rows ?? 1;
    const maxFacetNominalValues = maxFacetCols * maxFacetRows;
    let colValues;
    let rowValues;
    if (colField && channelSemantics.column?.type === "quantitative") {
      const raw = values.map((r) => r[colField]).filter((v) => v != null && typeof v === "number" && !isNaN(v));
      const uniques = new Set(raw);
      if (uniques.size > maxFacetNominalValues) {
        const numBins = Math.min(maxFacetNominalValues, 20);
        const minVal = Math.min(...raw);
        const maxVal = Math.max(...raw);
        const step = (maxVal - minVal) / numBins || 1;
        const getColBin = (v) => Math.min(numBins - 1, Math.floor((v - minVal) / step));
        values = values.map((r) => {
          const v = r[colField];
          const bin = v != null && typeof v === "number" && !isNaN(v) ? getColBin(v) : 0;
          return { ...r, _ecColumnBin: bin };
        });
        colValues = Array.from({ length: numBins }, (_, i) => String(i));
      } else {
        colValues = [...new Set(values.map((r) => String(r[colField])))];
      }
    } else {
      colValues = colField ? [...new Set(values.map((r) => String(r[colField])))] : [];
    }
    if (rowField && channelSemantics.row?.type === "quantitative") {
      const raw = values.map((r) => r[rowField]).filter((v) => v != null && typeof v === "number" && !isNaN(v));
      const uniques = new Set(raw);
      if (uniques.size > maxFacetNominalValues) {
        const numBins = Math.min(maxFacetNominalValues, 20);
        const minVal = Math.min(...raw);
        const maxVal = Math.max(...raw);
        const step = (maxVal - minVal) / numBins || 1;
        const getRowBin = (v) => Math.min(numBins - 1, Math.floor((v - minVal) / step));
        values = values.map((r) => {
          const v = r[rowField];
          const bin = v != null && typeof v === "number" && !isNaN(v) ? getRowBin(v) : 0;
          return { ...r, _ecRowBin: bin };
        });
        rowValues = Array.from({ length: numBins }, (_, i) => String(i));
      } else {
        rowValues = [...new Set(values.map((r) => String(r[rowField])))];
      }
    } else {
      rowValues = rowField ? [...new Set(values.map((r) => String(r[rowField])))] : [];
    }
    const facetLayout = computeLayout(
      channelSemantics,
      declaration,
      values,
      canvasSize,
      effectiveOptions,
      facetGridResult
    );
    facetLayout.truncations = overflowResult.truncations;
    const nRows = rowValues.length || 1;
    const nCols = colValues.length || 1;
    const maxColsPerRow = facetGridResult?.columns ?? nCols;
    const panels = [];
    const colBinned = colField && values.length > 0 && values[0]._ecColumnBin !== void 0;
    const rowBinned = rowField && values.length > 0 && values[0]._ecRowBin !== void 0;
    for (let ri = 0; ri < nRows; ri++) {
      const row = [];
      for (let ci = 0; ci < nCols; ci++) {
        const cv = colValues[ci];
        const rv = rowValues[ri];
        const panelData = values.filter((r) => {
          if (colField) {
            if (colBinned) {
              if (r._ecColumnBin !== ci) return false;
            } else if (String(r[colField]) !== cv) return false;
          }
          if (rowField) {
            if (rowBinned) {
              if (r._ecRowBin !== ri) return false;
            } else if (String(r[rowField]) !== rv) return false;
          }
          return true;
        });
        const panelOption = structuredClone(chartTemplate.template);
        const panelCtx = {
          ...instantiateContext,
          table: panelData,
          layout: facetLayout,
          canvasSize
        };
        chartTemplate.instantiate(panelOption, panelCtx);
        ecApplyLayoutToSpec(panelOption, panelCtx, []);
        if (addTooltipsOpt) ecApplyTooltips(panelOption);
        if (chartTemplate.postProcess) chartTemplate.postProcess(panelOption, panelCtx);
        const g = panelOption.grid || {};
        panelOption._plotWidth = Math.max(
          20,
          facetLayout.subplotWidth || (panelOption._width || 200) - (g.left || 0) - (g.right || 0)
        );
        panelOption._plotHeight = Math.max(
          20,
          facetLayout.subplotHeight || (panelOption._height || 150) - (g.top || 0) - (g.bottom || 0)
        );
        if (colField) panelOption._colHeader = cv;
        if (rowField) panelOption._rowHeader = rv;
        row.push(panelOption);
      }
      panels.push(row);
    }
    let finalPanels = panels;
    let colHeaderPerRow = false;
    if (colField && !rowField && maxColsPerRow < nCols) {
      const displayCols = maxColsPerRow;
      const wrapRows = Math.ceil(nCols / displayCols);
      finalPanels = [];
      for (let wr = 0; wr < wrapRows; wr++) {
        const wrapRow = [];
        for (let vc = 0; vc < displayCols; vc++) {
          const origCi = wr * displayCols + vc;
          if (origCi < nCols) {
            wrapRow.push(panels[0][origCi]);
          }
        }
        if (wrapRow.length > 0) finalPanels.push(wrapRow);
      }
      colHeaderPerRow = true;
    }
    ecOption = ecCombineFacetPanels(finalPanels, {
      colField,
      rowField,
      colHeaderPerRow
    });
  } else {
    ecOption = structuredClone(chartTemplate.template);
    chartTemplate.instantiate(ecOption, instantiateContext);
    ecApplyLayoutToSpec(ecOption, instantiateContext, warnings);
    if (addTooltipsOpt) {
      ecApplyTooltips(ecOption);
    }
    if (chartTemplate.postProcess) {
      chartTemplate.postProcess(ecOption, instantiateContext);
    }
  }
  if (warnings.length > 0) {
    ecOption._warnings = warnings;
  }
  ecOption._dataLength = values.length;
  if (transformed.surface) {
    ecOption._transform = transformed.surface;
  }
  const legacyPivot = applyPivot(authoredTemplate, typedRawEncodings, data, chartProperties, ecGetTemplateDef);
  if (legacyPivot.surface) {
    ecOption._pivot = legacyPivot.surface;
  }
  delete ecOption._legendWidth;
  return ecOption;
}
function getEChartsPivot(input) {
  const spec = assembleECharts(input);
  return spec && spec._pivot ? spec._pivot : void 0;
}
function getEChartsTransform(input) {
  const spec = assembleECharts(input);
  return spec && spec._transform ? spec._transform : void 0;
}
function buildECEncodings(encodings, channelSemantics, declaration, data, canvasSize, semanticTypes, templateMarkType, chartTemplate) {
  const resolved = {};
  const encodingsEntries = Object.entries(encodings);
  for (const [channel, encoding] of encodingsEntries) {
    const entry = {};
    const fieldName = encoding.field;
    const cs = channelSemantics[channel];
    if (channel === "radius") {
      entry.radiusScale = { type: "sqrt", zero: true };
    }
    if (!fieldName && encoding.aggregate === "count") {
      entry.field = "_count";
      entry.type = "quantitative";
    }
    if (fieldName) {
      entry.field = fieldName;
      entry.type = cs?.type ?? "nominal";
      if (encoding.type) {
        entry.type = encoding.type;
      } else if (channel === "column" || channel === "row") {
        if (entry.type !== "nominal" && entry.type !== "ordinal") {
          entry.type = "nominal";
        }
      }
      if (encoding.aggregate) {
        if (encoding.aggregate === "count") {
          entry.field = "_count";
          entry.type = "quantitative";
        } else {
          entry.field = `${fieldName}_${encoding.aggregate}`;
          entry.type = "quantitative";
        }
      }
      if (entry.type === "quantitative" && channel === "x") {
        if (templateMarkType === "line" || templateMarkType === "area" || templateMarkType === "trail" || templateMarkType === "point") {
          entry.scaleNice = false;
        }
      }
      if (entry.type === "nominal" && (channel === "color" || channel === "group")) {
        const actualDomain = [...new Set(data.map((r) => r[fieldName]))];
        if (actualDomain.length >= 16) {
          entry.legendSymbolSize = 12;
          entry.legendLabelFontSize = 8;
        }
      }
    }
    if (channel === "size") {
      const EC_SIZE_MIN_PX = 10;
      const EC_SIZE_MAX_PX = 50;
      const plotArea = canvasSize.width * canvasSize.height;
      const n = Math.max(data.length, 1);
      const fairShare = plotArea / n;
      const targetPct = 0.05;
      const idealDiameterPx = Math.sqrt(fairShare * targetPct);
      const isQuant = entry.type === "quantitative" || entry.type === "temporal";
      const maxSize = Math.round(Math.max(EC_SIZE_MIN_PX, Math.min(EC_SIZE_MAX_PX, idealDiameterPx)));
      const minSize = isQuant ? Math.max(EC_SIZE_MIN_PX, Math.round(maxSize / 3)) : Math.round(maxSize / 4);
      entry.sizeRange = [Math.max(EC_SIZE_MIN_PX, minSize), Math.max(minSize, maxSize)];
    }
    if (encoding.sortBy || encoding.sortOrder) {
      entry.sortOrder = encoding.sortOrder;
      entry.sortBy = encoding.sortBy;
      if (encoding.sortBy) {
        if (encoding.sortBy === "x" || encoding.sortBy === "y" || encoding.sortBy === "color") ; else {
          try {
            if (fieldName) {
              const fieldSemType = toTypeString(semanticTypes[fieldName]);
              const fieldVisCat = inferVisCategory(data.map((r) => r[fieldName]));
              let sortedValues = JSON.parse(encoding.sortBy);
              if (fieldVisCat === "temporal" || fieldSemType === "Year" || fieldSemType === "Decade") {
                sortedValues = sortedValues.map((v) => String(v));
              }
              entry.sortValues = encoding.sortOrder === "descending" ? [...sortedValues].reverse() : sortedValues;
            }
          } catch {
          }
        }
      }
    } else {
      const isDiscrete12 = entry.type === "nominal" || entry.type === "ordinal";
      if (isDiscrete12) {
        if (cs?.ordinalSortOrder?.length) {
          entry.ordinalSortOrder = cs.ordinalSortOrder;
        } else {
          entry.preserveDataOrder = true;
        }
      }
    }
    if (Object.keys(entry).length > 0) {
      resolved[channel] = entry;
    }
  }
  if (declaration.resolvedTypes) {
    for (const [ch, type] of Object.entries(declaration.resolvedTypes)) {
      if (resolved[ch]) {
        resolved[ch].type = type;
      }
    }
  }
  const groupCS = channelSemantics.group;
  if (groupCS?.field && resolved.group) {
    const xType = resolved.x?.type;
    const yType = resolved.y?.type;
    const isDiscrete12 = (t) => t === "nominal" || t === "ordinal";
    const groupAxis = isDiscrete12(xType) ? "x" : isDiscrete12(yType) ? "y" : "x";
    const offsetChannel = groupAxis === "x" ? "xOffset" : "yOffset";
    resolved.group.groupAxis = groupAxis;
    resolved.group.offsetChannel = offsetChannel;
    if (!resolved.color) {
      const palette = groupCS.colorScheme?.scheme ? getPaletteForScheme(groupCS.colorScheme.scheme) ?? DEFAULT_COLORS : DEFAULT_COLORS;
      resolved.color = {
        field: groupCS.field,
        type: groupCS.type ?? "nominal",
        colorPalette: palette,
        colorDomainMid: resolved.group.colorDomainMid,
        ordinalSortOrder: resolved.group.ordinalSortOrder,
        sortOrder: resolved.group.sortOrder,
        sortBy: resolved.group.sortBy,
        sortValues: resolved.group.sortValues,
        preserveDataOrder: resolved.group.preserveDataOrder,
        legendSymbolSize: resolved.group.legendSymbolSize,
        legendLabelFontSize: resolved.group.legendLabelFontSize
      };
    } else if (!resolved.color.colorPalette && groupCS.colorScheme?.scheme) {
      resolved.color.colorPalette = getPaletteForScheme(groupCS.colorScheme.scheme) ?? DEFAULT_COLORS;
    }
  }
  const templateEncoding = chartTemplate.template?.encoding;
  if (templateEncoding && typeof templateEncoding === "object") {
    for (const [ch, enc] of Object.entries(templateEncoding)) {
      if (enc && typeof enc === "object" && Object.keys(enc).length > 0 && resolved[ch]) {
        resolved[ch] = { ...enc, ...resolved[ch] };
      }
    }
  }
  return resolved;
}

// src/core/recommendation.ts
var FAMILY_XY_STANDARD = {
  x: "category",
  y: "measure",
  color: "series",
  opacity: "auxiliary",
  size: "auxiliary",
  shape: "auxiliary",
  detail: "auxiliary",
  group: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_XY_HORIZONTAL = {
  y: "category",
  x: "measure",
  color: "series",
  opacity: "auxiliary",
  size: "auxiliary",
  shape: "auxiliary",
  detail: "auxiliary",
  group: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_PIE = {
  color: "category",
  size: "measure",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_ROSE = {
  x: "category",
  y: "measure",
  color: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_RADAR = {
  x: "category",
  y: "measure",
  color: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_MAP = {
  latitude: "geo",
  longitude: "geo",
  color: "series",
  size: "auxiliary",
  opacity: "auxiliary"
};
var FAMILY_CHOROPLETH = {
  id: "geo",
  color: "measure",
  detail: "auxiliary"
};
var FAMILY_CANDLESTICK = {
  x: "category",
  open: "price",
  high: "price",
  low: "price",
  close: "price",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_HISTOGRAM = {
  x: "measure",
  color: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_DENSITY = {
  x: "measure",
  color: "series",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_DENSITY_2D = {
  x: "measure",
  y: "measure2",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_HEATMAP = {
  x: "category",
  y: "category",
  color: "measure",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_GAUGE = {
  size: "measure",
  column: "facetCol"
};
var FAMILY_FUNNEL = {
  y: "category",
  size: "measure"
};
var FAMILY_TREEMAP = {
  color: "category",
  size: "measure",
  detail: "auxiliary",
  group: "auxiliary"
};
var FAMILY_SANKEY = {
  x: "category",
  y: "category",
  size: "measure"
};
var FAMILY_GANTT = {
  y: "category",
  x: "measure",
  x2: "measure2",
  color: "series",
  detail: "auxiliary",
  column: "facetCol",
  row: "facetRow"
};
var FAMILY_BULLET = {
  y: "category",
  x: "measure",
  goal: "measure2",
  color: "series",
  column: "facetCol",
  row: "facetRow"
};
var CHART_ROLE_MAP = {
  // Axis-based (x/y standard)
  "Bar Chart": FAMILY_XY_STANDARD,
  "Pyramid Chart": FAMILY_XY_HORIZONTAL,
  "Grouped Bar Chart": FAMILY_XY_STANDARD,
  "Stacked Bar Chart": FAMILY_XY_STANDARD,
  "Lollipop Chart": FAMILY_XY_STANDARD,
  "Waterfall Chart": FAMILY_XY_STANDARD,
  "Gantt Chart": FAMILY_GANTT,
  "Bullet Chart": FAMILY_BULLET,
  "Bar Table": FAMILY_XY_HORIZONTAL,
  "Line Chart": FAMILY_XY_STANDARD,
  "Bump Chart": FAMILY_XY_STANDARD,
  "Area Chart": FAMILY_XY_STANDARD,
  "Streamgraph": FAMILY_XY_STANDARD,
  "Scatter Plot": FAMILY_XY_STANDARD,
  "Connected Scatter Plot": FAMILY_XY_STANDARD,
  "Regression": FAMILY_XY_STANDARD,
  "Ranged Dot Plot": FAMILY_XY_STANDARD,
  "Boxplot": FAMILY_XY_STANDARD,
  "Strip Plot": FAMILY_XY_STANDARD,
  // Pie-like
  "Pie Chart": FAMILY_PIE,
  // Polar
  "Rose Chart": FAMILY_ROSE,
  "Radar Chart": FAMILY_RADAR,
  // Heatmap
  "Heatmap": FAMILY_HEATMAP,
  // Histogram / Density
  "Histogram": FAMILY_HISTOGRAM,
  "Density Plot": FAMILY_DENSITY,
  "Density Contour": FAMILY_DENSITY_2D,
  // Geographic
  "Map": FAMILY_MAP,
  "Choropleth": FAMILY_CHOROPLETH,
  // Financial
  "Candlestick Chart": FAMILY_CANDLESTICK,
  // ECharts-only
  "Gauge Chart": FAMILY_GAUGE,
  "Funnel Chart": FAMILY_FUNNEL,
  "Treemap": FAMILY_TREEMAP,
  "Sunburst Chart": FAMILY_TREEMAP,
  "Sankey Diagram": FAMILY_SANKEY
};
function getChannelRole(chartType, channel) {
  const roleMap = CHART_ROLE_MAP[chartType];
  if (roleMap && channel in roleMap) return roleMap[channel];
  if (channel === "column") return "facetCol";
  if (channel === "row") return "facetRow";
  return "auxiliary";
}
function findChannelsByRole(chartType, templateChannels, role) {
  return templateChannels.filter((ch) => getChannelRole(chartType, ch) === role);
}
var FALLBACK_CHAIN = {
  measure2: ["measure", "auxiliary"],
  series: ["auxiliary"],
  category: ["series", "auxiliary"],
  measure: ["auxiliary"],
  geo: ["category"],
  price: ["measure", "auxiliary"]
};
var ROLE_PRIORITY = {
  category: 0,
  measure: 1,
  series: 2,
  facetCol: 3,
  facetRow: 4,
  measure2: 5,
  auxiliary: 6,
  geo: 7,
  price: 8
};
function adaptChannels(sourceType, targetType, targetChannels, encodings, data, semanticTypes, recommendFn) {
  if (data && data.length > 0) {
    return adaptViaRecommendation(sourceType, targetType, targetChannels, encodings, data, semanticTypes ?? {});
  }
  return adaptViaRoles(sourceType, targetType, targetChannels, encodings);
}
function adaptViaRecommendation(sourceType, targetType, targetChannels, encodings, data, semanticTypes, _recommendFn) {
  const FACET_CHANNELS = ["column", "row"];
  let facetedData = data;
  const prePinned = {};
  const prePinnedFields = /* @__PURE__ */ new Set();
  for (const ch of FACET_CHANNELS) {
    const field = encodings[ch];
    if (field && targetChannels.includes(ch)) {
      prePinned[ch] = field;
      prePinnedFields.add(field);
      if (facetedData.length > 0) {
        const firstVal = facetedData[0][field];
        facetedData = facetedData.filter((row) => row[field] === firstVal);
      }
    }
  }
  const tv = buildTableView(facetedData, semanticTypes);
  const isFieldCompatibleWithRole = (role, field) => {
    const ft = tv.fieldType[field] ?? "nominal";
    const st = tv.fieldSemanticType[field] ?? "";
    const card = tv.fieldLevels[field]?.length ?? 0;
    switch (role) {
      // 'category' is for true discrete axes (nominal/ordinal/temporal).
      // Quantitative fields — even low-cardinality ones — must NOT
      // satisfy this role, otherwise a measure can land on the
      // category axis (e.g. Bar Table y) and push the real discrete
      // field onto color.
      case "category":
        return !isQuantitativeField(ft, st) && isDiscreteLike(ft, st, card);
      case "measure":
        return isQuantitativeField(ft, st);
      case "series":
        return isDiscreteLike(ft, st, card);
      case "geo":
        return isGeoCoordinateType(st) || ft === "quantitative";
      case "facetCol":
      case "facetRow":
        return isDiscreteLike(ft, st, card);
      case "auxiliary":
        return true;
      default:
        return true;
    }
  };
  const assignCost = (srcCh, field, targetCh) => {
    const targetRole = getChannelRole(targetType, targetCh);
    if (!isFieldCompatibleWithRole(targetRole, field)) return Infinity;
    const srcRole = getChannelRole(sourceType, srcCh);
    if (srcCh === targetCh && srcRole === targetRole) return 0;
    if (srcRole === targetRole) return 0.5;
    if (srcCh === targetCh) return 1;
    return 1;
  };
  const COST_DROP = 1.5;
  const entries = Object.entries(encodings).filter(([ch, f]) => f && !FACET_CHANNELS.includes(ch) && !prePinnedFields.has(f));
  const availableTargets = targetChannels.filter((ch) => !(ch in prePinned));
  let bestCost = Infinity;
  let bestAssignment = {};
  const usedTargets = /* @__PURE__ */ new Set();
  function solve(idx, currentCost, assignment) {
    if (currentCost >= bestCost) return;
    if (idx === entries.length) {
      bestCost = currentCost;
      bestAssignment = { ...assignment };
      return;
    }
    const [srcCh, field] = entries[idx];
    for (const tch of availableTargets) {
      if (usedTargets.has(tch)) continue;
      const cost = assignCost(srcCh, field, tch);
      if (cost === Infinity) continue;
      usedTargets.add(tch);
      assignment[tch] = field;
      solve(idx + 1, currentCost + cost, assignment);
      delete assignment[tch];
      usedTargets.delete(tch);
    }
    solve(idx + 1, currentCost + COST_DROP, assignment);
  }
  solve(0, 0, {});
  const result = { ...prePinned, ...bestAssignment };
  return result;
}
function adaptViaRoles(sourceType, targetType, targetChannels, encodings) {
  const result = {};
  const filledEncodings = [];
  for (const [ch, field] of Object.entries(encodings)) {
    if (field) {
      filledEncodings.push({ channel: ch, role: getChannelRole(sourceType, ch), field });
    }
  }
  filledEncodings.sort((a, b) => ROLE_PRIORITY[a.role] - ROLE_PRIORITY[b.role]);
  const assigned = /* @__PURE__ */ new Set();
  for (const { channel: srcCh, role: srcRole, field } of filledEncodings) {
    let placed = false;
    if (targetChannels.includes(srcCh) && !assigned.has(srcCh)) {
      if (getChannelRole(targetType, srcCh) === srcRole) {
        result[srcCh] = field;
        assigned.add(srcCh);
        placed = true;
      }
    }
    if (!placed) {
      placed = tryAssign(srcRole, field, targetType, targetChannels, result, assigned, srcCh);
    }
    if (!placed) {
      const chain = FALLBACK_CHAIN[srcRole];
      if (chain) {
        for (const fallbackRole of chain) {
          placed = tryAssign(fallbackRole, field, targetType, targetChannels, result, assigned, srcCh);
          if (placed) break;
        }
      }
    }
  }
  return result;
}
function tryAssign(role, field, targetType, targetChannels, result, assigned, preferredName) {
  const candidates = findChannelsByRole(targetType, targetChannels, role).filter((ch) => !assigned.has(ch));
  if (candidates.length === 0) return false;
  const best = preferredName && candidates.includes(preferredName) ? preferredName : candidates[0];
  result[best] = field;
  assigned.add(best);
  return true;
}
function buildTableView(data, semanticTypes) {
  const names = data.length > 0 ? Object.keys(data[0]) : [];
  const fieldType = {};
  const fieldSemanticType = {};
  const fieldLevels = {};
  for (const name of names) {
    const values = data.map((r) => r[name]);
    const semanticType = semanticTypes[name] || "";
    fieldType[name] = semanticType && getVisCategory(semanticType) || inferVisCategory(values);
    fieldSemanticType[name] = semanticType;
    fieldLevels[name] = [...new Set(data.map((r) => r[name]).filter((v) => v != null))];
  }
  return { names, fieldType, fieldSemanticType, fieldLevels, rows: data };
}
var Pref = { STRONG: 3, OK: 2, WEAK: 1, EXCLUDE: -Infinity };
function resolveAssignment(tv, used, channelPrefs) {
  const candidates = tv.names.filter((n) => !used.has(n) && (!isLikelyIdentifierOrRank(n) || tv.preferredFields?.has(n)));
  const C = channelPrefs.length;
  const F = candidates.length;
  if (F < C) return {};
  const scores = [];
  for (let ci = 0; ci < C; ci++) {
    scores[ci] = [];
    for (let fi = 0; fi < F; fi++) {
      const name = candidates[fi];
      const type = tv.fieldType[name] ?? "nominal";
      const st = tv.fieldSemanticType[name] ?? "";
      const card = tv.fieldLevels[name]?.length ?? 0;
      scores[ci][fi] = channelPrefs[ci].pref(name, type, st, card, card > 0);
    }
  }
  let bestScore = -Infinity;
  let bestAssign;
  const perm = new Array(C);
  const usedF = new Uint8Array(F);
  function search(depth, totalScore) {
    if (depth === C) {
      if (totalScore > bestScore) {
        bestScore = totalScore;
        bestAssign = [...perm];
      }
      return;
    }
    for (let fi = 0; fi < F; fi++) {
      if (usedF[fi]) continue;
      const s = scores[depth][fi];
      if (s === -Infinity) continue;
      if (totalScore + s <= bestScore - (C - depth - 1) * Pref.STRONG) continue;
      perm[depth] = fi;
      usedF[fi] = 1;
      search(depth + 1, totalScore + s);
      usedF[fi] = 0;
    }
  }
  search(0, 0);
  if (!bestAssign) return {};
  const result = {};
  for (let ci = 0; ci < C; ci++) {
    const fieldName = candidates[bestAssign[ci]];
    result[channelPrefs[ci].channel] = fieldName;
    used.add(fieldName);
  }
  return result;
}
function isTemporalField(type, semanticType) {
  return type === "temporal" || isTimeSeriesType(semanticType);
}
function isQuantitativeField(type, semanticType) {
  if (isTemporalField(type, semanticType)) return false;
  if (type !== "quantitative") return false;
  if (isNonMeasureNumeric(semanticType)) return false;
  return isMeasureType(semanticType) || semanticType === "";
}
function isOrdinalField(type, semanticType, hasLevels) {
  if (hasLevels) return true;
  return isOrdinalType(semanticType);
}
function isCategoricalFieldCheck(type, semanticType) {
  if (isTemporalField(type, semanticType)) return false;
  if (isQuantitativeField(type, semanticType)) return false;
  return type === "nominal" || isCategoricalType(semanticType);
}
function isDiscreteLike(type, semanticType, cardinality, maxCard = 50) {
  if (isCategoricalFieldCheck(type, semanticType)) return true;
  if (isTemporalField(type, semanticType)) return true;
  if (isOrdinalType(semanticType)) return true;
  if (type === "quantitative" && cardinality > 0 && cardinality <= maxCard) return true;
  return false;
}
function nameMatches(name, patterns) {
  const lower = name.toLowerCase();
  return patterns.some((p) => lower === p) || patterns.some((p) => lower.includes(p));
}
function isLikelyIdentifierOrRank(name) {
  const lower = name.toLowerCase();
  const idPatterns = ["rank", "id", "index", "idx", "row", "order", "position", "pos"];
  return idPatterns.some((p) => lower === p || lower.endsWith("_" + p) || lower.endsWith(p));
}
function pick(tv, used, predicate) {
  const candidates = [];
  for (const name of tv.names) {
    if (used.has(name)) continue;
    const type = tv.fieldType[name] ?? "nominal";
    const semanticType = tv.fieldSemanticType[name] ?? "";
    const cardinality = tv.fieldLevels[name]?.length ?? 0;
    const hasLevels = cardinality > 0;
    if (predicate(name, type, semanticType, cardinality, hasLevels)) {
      candidates.push(name);
    }
  }
  if (candidates.length === 0) return void 0;
  if (tv.preferredFields) {
    const preferred = candidates.filter((n) => tv.preferredFields.has(n));
    if (preferred.length > 0) {
      const chosen2 = preferred[0];
      used.add(chosen2);
      return chosen2;
    }
  }
  const chosen = candidates[0];
  used.add(chosen);
  return chosen;
}
var pickQuantitative = (tv, u) => pick(tv, u, (name, ty, st) => isQuantitativeField(ty, st) && (!isLikelyIdentifierOrRank(name) || !!tv.preferredFields?.has(name)));
var pickTemporal = (tv, u) => pick(tv, u, (_n, ty, st) => isTemporalField(ty, st));
var pickNominal = (tv, u) => pick(tv, u, (_n, ty, st) => isCategoricalFieldCheck(ty, st));
var pickLowCardNominal = (tv, u, maxCard = 30) => pick(tv, u, (_n, ty, st, card) => isCategoricalFieldCheck(ty, st) && card > 0 && card <= maxCard);
var pickOrdinal = (tv, u) => pick(tv, u, (_n, ty, st, _card, hasLevels) => isOrdinalField(ty, st, hasLevels));
var pickDiscrete = (tv, u) => pick(tv, u, (name, ty, st, card) => isDiscreteLike(ty, st, card) && (!isLikelyIdentifierOrRank(name) || !!tv.preferredFields?.has(name)));
var pickLowCardDiscrete = (tv, u, maxCard = 30) => pick(
  tv,
  u,
  (name, ty, st, card) => isDiscreteLike(ty, st, card, maxCard) && card > 0 && card <= maxCard && (!isLikelyIdentifierOrRank(name) || !!tv.preferredFields?.has(name))
);
var pickSeriesAxis = (tv, u) => pickTemporal(tv, u) ?? pickOrdinal(tv, u) ?? pickNominal(tv, u);
var pickQuantitativeByName = (tv, u, patterns) => pick(tv, u, (name, ty, st) => isQuantitativeField(ty, st) && nameMatches(name, patterns));
function pickAllQuantitative(tv, used) {
  const result = [];
  for (const name of tv.names) {
    if (used.has(name)) continue;
    const type = tv.fieldType[name] ?? "nominal";
    const semanticType = tv.fieldSemanticType[name] ?? "";
    if (isQuantitativeField(type, semanticType) && (!isLikelyIdentifierOrRank(name) || tv.preferredFields?.has(name))) {
      result.push(name);
    }
  }
  for (const name of result) used.add(name);
  return result;
}
function hasMultipleValuesPerField(tv, fieldName) {
  if (!fieldName || !tv.rows || tv.rows.length === 0) return false;
  const seen = /* @__PURE__ */ new Set();
  for (const row of tv.rows) {
    const val = row[fieldName];
    if (seen.has(val)) return true;
    seen.add(val);
  }
  return false;
}
function isValidGroupingField(tv, xField, colorField) {
  if (!xField || !colorField || !tv.rows || tv.rows.length === 0) return false;
  const seen = /* @__PURE__ */ new Set();
  for (const row of tv.rows) {
    const key = `${row[xField]}|||${row[colorField]}`;
    if (seen.has(key)) return false;
    seen.add(key);
  }
  return true;
}
function lowestCardinality(tv, candidates) {
  let best = candidates[0];
  let bestCard = tv.fieldLevels[best]?.length ?? Infinity;
  for (let i = 1; i < candidates.length; i++) {
    const card = tv.fieldLevels[candidates[i]]?.length ?? Infinity;
    if (card < bestCard) {
      best = candidates[i];
      bestCard = card;
    }
  }
  return best;
}
function pickValidGroupingField(tv, used, xField, maxCard = 20) {
  const candidates = [];
  for (const name of tv.names) {
    if (used.has(name)) continue;
    const type = tv.fieldType[name] ?? "nominal";
    const semanticType = tv.fieldSemanticType[name] ?? "";
    const cardinality = tv.fieldLevels[name]?.length ?? 0;
    if (!isDiscreteLike(type, semanticType, cardinality, maxCard)) continue;
    if (cardinality <= 0 || cardinality > maxCard) continue;
    if (isLikelyIdentifierOrRank(name) && !tv.preferredFields?.has(name)) continue;
    if (isValidGroupingField(tv, xField, name)) candidates.push(name);
  }
  if (candidates.length === 0) return void 0;
  if (tv.preferredFields) {
    const preferred = candidates.filter((n) => tv.preferredFields.has(n));
    if (preferred.length > 0) {
      const chosen2 = lowestCardinality(tv, preferred);
      used.add(chosen2);
      return chosen2;
    }
  }
  const chosen = lowestCardinality(tv, candidates);
  used.add(chosen);
  return chosen;
}
function isValidLineSeriesData(tv, xField, colorField) {
  if (!tv.rows || tv.rows.length === 0) return false;
  const xColorCombinations = /* @__PURE__ */ new Set();
  const colorGroupCounts = /* @__PURE__ */ new Map();
  for (const row of tv.rows) {
    const xVal = row[xField];
    const colorVal = colorField ? row[colorField] : "__single__";
    const xColorKey = `${xVal}|||${colorVal}`;
    if (xColorCombinations.has(xColorKey)) return false;
    xColorCombinations.add(xColorKey);
    colorGroupCounts.set(colorVal, (colorGroupCounts.get(colorVal) ?? 0) + 1);
  }
  let validGroups = 0;
  let totalGroups = 0;
  for (const count of colorGroupCounts.values()) {
    totalGroups++;
    if (count >= 2) validGroups++;
  }
  return totalGroups > 0 && validGroups / totalGroups > 0.5;
}
function pickLineChartColorField(tv, used, xField, maxCard = 20) {
  const candidates = [];
  for (const name of tv.names) {
    if (used.has(name)) continue;
    const type = tv.fieldType[name] ?? "nominal";
    const semanticType = tv.fieldSemanticType[name] ?? "";
    const cardinality = tv.fieldLevels[name]?.length ?? 0;
    if (!isDiscreteLike(type, semanticType, cardinality, maxCard)) continue;
    if (cardinality <= 0 || cardinality > maxCard) continue;
    if (isLikelyIdentifierOrRank(name) && !tv.preferredFields?.has(name)) continue;
    if (isValidLineSeriesData(tv, xField, name)) candidates.push(name);
  }
  if (candidates.length === 0) return void 0;
  if (tv.preferredFields) {
    const preferred = candidates.filter((n) => tv.preferredFields.has(n));
    if (preferred.length > 0) {
      const chosen2 = lowestCardinality(tv, preferred);
      used.add(chosen2);
      return chosen2;
    }
  }
  const chosen = lowestCardinality(tv, candidates);
  used.add(chosen);
  return chosen;
}
function calculateMultiplicity(tv, xField, colorField) {
  if (!tv.rows || tv.rows.length === 0) return 1;
  const groups = /* @__PURE__ */ new Set();
  for (const row of tv.rows) {
    const key = colorField ? `${row[xField]}|||${row[colorField]}` : `${row[xField]}`;
    groups.add(key);
  }
  return tv.rows.length / groups.size;
}
function pickBestGroupingField(tv, used, xField, maxMultiplicity = 5) {
  const baseMultiplicity = calculateMultiplicity(tv, xField);
  if (baseMultiplicity <= 1) return void 0;
  let bestField;
  let bestMultiplicity = baseMultiplicity;
  for (const name of tv.names) {
    if (used.has(name)) continue;
    const type = tv.fieldType[name] ?? "nominal";
    const semanticType = tv.fieldSemanticType[name] ?? "";
    const cardinality = tv.fieldLevels[name]?.length ?? 0;
    if (!isDiscreteLike(type, semanticType, cardinality)) continue;
    if (isLikelyIdentifierOrRank(name) && !tv.preferredFields?.has(name)) continue;
    const multiplicity = calculateMultiplicity(tv, xField, name);
    if (multiplicity < bestMultiplicity) {
      bestMultiplicity = multiplicity;
      bestField = name;
      if (multiplicity <= 1) break;
    }
  }
  if (bestField && bestMultiplicity < baseMultiplicity && bestMultiplicity <= maxMultiplicity) {
    used.add(bestField);
    return bestField;
  }
  return void 0;
}
function recommendChannels(chartType, data, semanticTypes, recommendFn) {
  const fn = recommendFn ?? getRecommendation;
  return fn(chartType, buildTableView(data, semanticTypes));
}
function getRecommendation(chartType, tv) {
  const used = /* @__PURE__ */ new Set();
  const rec = {};
  const assign = (channel, fieldName) => {
    if (fieldName) rec[channel] = fieldName;
  };
  switch (chartType) {
    case "Scatter Plot": {
      const yField = pickQuantitative(tv, used) ?? pickTemporal(tv, used) ?? pickNominal(tv, used);
      const xField = pickQuantitative(tv, used) ?? pickTemporal(tv, used) ?? pickNominal(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      assign("color", pickLowCardNominal(tv, used));
      break;
    }
    case "Bar Chart":
    case "Stacked Bar Chart": {
      const xField = pickDiscrete(tv, used);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      if (hasMultipleValuesPerField(tv, xField)) {
        assign("color", pickBestGroupingField(tv, used, xField));
      }
      break;
    }
    case "Grouped Bar Chart": {
      const xField = pickDiscrete(tv, used);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      const seriesField = pickValidGroupingField(tv, used, xField, 20);
      if (!seriesField) return {};
      assign("x", xField);
      assign("y", yField);
      assign("group", seriesField);
      break;
    }
    case "Histogram": {
      const xField = pickQuantitative(tv, used);
      if (!xField) return {};
      assign("x", xField);
      break;
    }
    case "Heatmap": {
      const heatmapResult = resolveAssignment(tv, used, [
        {
          channel: "x",
          pref: (_n, ty, st, card) => {
            if (isTimeSeriesType(st)) return Pref.STRONG;
            if (isCategoricalType(st)) return Pref.OK;
            if (isOrdinalType(st)) return Pref.OK;
            if (isNonMeasureNumeric(st)) return Pref.OK;
            if (ty === "nominal") return Pref.OK;
            if (ty === "temporal") return Pref.STRONG;
            if (ty === "quantitative" && card > 0 && card <= 50) return Pref.WEAK;
            return Pref.EXCLUDE;
          }
        },
        {
          channel: "y",
          pref: (_n, ty, st, card) => {
            if (isCategoricalType(st)) return Pref.STRONG;
            if (isTimeSeriesType(st)) return Pref.OK;
            if (isOrdinalType(st)) return Pref.OK;
            if (isNonMeasureNumeric(st)) return Pref.OK;
            if (ty === "nominal") return Pref.STRONG;
            if (ty === "temporal") return Pref.OK;
            if (ty === "quantitative" && card > 0 && card <= 50) return Pref.WEAK;
            return Pref.EXCLUDE;
          }
        },
        {
          channel: "color",
          pref: (_n, ty, st) => {
            if (isMeasureType(st)) return Pref.STRONG;
            if (isOrdinalType(st)) return Pref.OK;
            if (ty === "quantitative" && !st) return Pref.STRONG;
            if (ty === "temporal") return Pref.WEAK;
            if (ty === "nominal") return Pref.WEAK;
            return Pref.EXCLUDE;
          }
        }
      ]);
      if (!heatmapResult["x"] || !heatmapResult["y"] || !heatmapResult["color"]) return {};
      assign("x", heatmapResult["x"]);
      assign("y", heatmapResult["y"]);
      assign("color", heatmapResult["color"]);
      break;
    }
    case "Line Chart": {
      const xField = pickSeriesAxis(tv, used);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      if (!isValidLineSeriesData(tv, xField, void 0)) {
        const colorField = pickLineChartColorField(tv, used, xField, 20) ?? pickLineChartColorField(tv, used, xField, 200);
        if (!colorField) return {};
        assign("color", colorField);
      }
      break;
    }
    case "Boxplot": {
      const xField = pickDiscrete(tv, used);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      break;
    }
    case "Pie Chart": {
      const sizeField = pickQuantitative(tv, used);
      const colorField = pickLowCardDiscrete(tv, used, 12);
      if (!sizeField || !colorField) return {};
      assign("size", sizeField);
      assign("color", colorField);
      break;
    }
    case "Area Chart": {
      const xField = pickSeriesAxis(tv, used);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      assign("color", pickLineChartColorField(tv, used, xField, 20));
      break;
    }
    case "Streamgraph": {
      const streamResult = resolveAssignment(tv, used, [
        {
          channel: "x",
          pref: (_n, ty, st, card) => {
            if (isTimeSeriesType(st)) return Pref.STRONG;
            if (isOrdinalType(st)) return Pref.OK;
            if (isCategoricalType(st)) return Pref.OK;
            if (isNonMeasureNumeric(st)) return Pref.OK;
            if (ty === "temporal") return Pref.STRONG;
            if (ty === "nominal") return Pref.OK;
            if (ty === "quantitative" && card > 0 && card <= 50) return Pref.OK;
            return Pref.EXCLUDE;
          }
        },
        {
          channel: "y",
          pref: (_n, ty, st, card) => {
            if (isMeasureType(st)) return Pref.STRONG;
            if (isTimeSeriesType(st)) return Pref.EXCLUDE;
            if (isCategoricalType(st)) return Pref.EXCLUDE;
            if (isNonMeasureNumeric(st)) return Pref.EXCLUDE;
            if (ty === "quantitative" && !st)
              return card > 20 ? Pref.STRONG : Pref.OK;
            return Pref.EXCLUDE;
          }
        },
        {
          channel: "color",
          pref: (_n, ty, st, card) => {
            if (isCategoricalType(st)) return Pref.STRONG;
            if (isOrdinalType(st)) return Pref.OK;
            if (isTimeSeriesType(st)) return Pref.OK;
            if (ty === "nominal") return Pref.STRONG;
            if (ty === "temporal" || ty === "ordinal") return Pref.OK;
            if (isDiscreteLike(ty, st, card, 20)) return Pref.WEAK;
            return Pref.EXCLUDE;
          }
        }
      ]);
      if (!streamResult["x"] || !streamResult["y"] || !streamResult["color"]) return {};
      assign("x", streamResult["x"]);
      assign("y", streamResult["y"]);
      assign("color", streamResult["color"]);
      break;
    }
    case "Radar Chart": {
      const xField = pickDiscrete(tv, used) ?? pickLowCardDiscrete(tv, used, 20);
      const yField = pickQuantitative(tv, used);
      if (!xField || !yField) return {};
      assign("x", xField);
      assign("y", yField);
      assign("color", pickLowCardDiscrete(tv, used, 20));
      break;
    }
    case "Candlestick Chart": {
      const xField = pickTemporal(tv, used) ?? pick(tv, used, (name) => nameMatches(name, ["date", "time", "day", "datetime", "timestamp", "period"])) ?? pickQuantitativeByName(tv, used, ["date", "time", "day"]) ?? pickDiscrete(tv, used);
      if (!xField) return {};
      assign("x", xField);
      const openField = pickQuantitativeByName(tv, used, ["open"]);
      const highField = pickQuantitativeByName(tv, used, ["high"]);
      const lowField = pickQuantitativeByName(tv, used, ["low"]);
      const closeField = pickQuantitativeByName(tv, used, ["close"]);
      if (openField && highField && lowField && closeField) {
        assign("open", openField);
        assign("high", highField);
        assign("low", lowField);
        assign("close", closeField);
      } else {
        const quants = pickAllQuantitative(tv, used);
        if (quants.length >= 4) {
          assign("open", quants[0]);
          assign("high", quants[1]);
          assign("low", quants[2]);
          assign("close", quants[3]);
        }
      }
      break;
    }
  }
  return rec;
}

// src/core/chart-type-recommendation.ts
var ID_NAME_PATTERNS = ["id", "index", "idx", "row", "order", "position", "pos"];
function looksLikeIdentifier(name) {
  const lower = name.toLowerCase();
  return ID_NAME_PATTERNS.some((p) => lower === p || lower.endsWith("_" + p));
}
function classifyRole(name, type, semanticType) {
  const st = semanticType;
  if (isGeoCoordinateType(st)) {
    if (st === "Latitude" || nameMatches(name, ["latitude", "lat"])) return "latitude";
    if (st === "Longitude" || nameMatches(name, ["longitude", "lon", "lng", "long"])) return "longitude";
    return "other";
  }
  if (isGeoLocationString(st)) return "geoPlace";
  if (type === "temporal" || isTimeSeriesType(st)) return "temporal";
  if (st === "ID" || looksLikeIdentifier(name)) return "identifier";
  if (type === "quantitative" && !isNonMeasureNumeric(st) && (isMeasureType(st) || st === "")) {
    return "measure";
  }
  if (isOrdinalType(st)) return "ordinal";
  if (type === "nominal" || isCategoricalType(st)) return "categorical";
  return "other";
}
function profileData(data, semanticTypes) {
  const tv = buildTableView(data, semanticTypes);
  const fields = tv.names.map((name) => ({
    name,
    type: tv.fieldType[name] ?? "nominal",
    semanticType: tv.fieldSemanticType[name] ?? "",
    cardinality: tv.fieldLevels[name]?.length ?? 0,
    role: classifyRole(name, tv.fieldType[name] ?? "nominal", tv.fieldSemanticType[name] ?? "")
  }));
  const by = (r) => fields.filter((f) => f.role === r);
  const categoricals = by("categorical");
  const ordinals = by("ordinal");
  const geoPlaces = by("geoPlace");
  const temporals = by("temporal");
  return {
    fields,
    measures: by("measure"),
    temporals,
    categoricals,
    ordinals,
    geoPlaces,
    latitudes: by("latitude"),
    longitudes: by("longitude"),
    identifiers: by("identifier"),
    dimensions: [...categoricals, ...ordinals, ...geoPlaces, ...temporals],
    rowCount: tv.rows.length
  };
}
var LOW_CARD_SERIES = 12;
var LOW_CARD_AXIS = 25;
var PIE_MAX_SLICES = 8;
var CHOROPLETH_SEMANTIC = /* @__PURE__ */ new Set(["Country", "State"]);
var CHOROPLETH_NAME_HINTS = ["country", "state", "province", "nation"];
function rankChartTypes(profile) {
  const {
    measures,
    temporals,
    categoricals,
    ordinals,
    geoPlaces,
    latitudes,
    longitudes,
    rowCount
  } = profile;
  const catLike = [...categoricals, ...ordinals, ...geoPlaces];
  const dims = profile.dimensions;
  const lowCardCatLike = catLike.filter((f) => f.cardinality >= 2 && f.cardinality <= LOW_CARD_SERIES);
  const lowCardDims = dims.filter((f) => f.cardinality >= 2 && f.cardinality <= LOW_CARD_AXIS);
  const hasMeasure = measures.length >= 1;
  const order = [];
  const acc = /* @__PURE__ */ new Map();
  const add = (chartType, score, reason) => {
    const cur = acc.get(chartType);
    if (!cur) {
      order.push(chartType);
      acc.set(chartType, { score, reasons: [reason] });
    } else {
      if (score > cur.score) cur.score = score;
      if (!cur.reasons.includes(reason)) cur.reasons.push(reason);
    }
  };
  if (latitudes.length >= 1 && longitudes.length >= 1) {
    add("Map", 96, "has latitude + longitude coordinates");
  }
  const choroGeo = geoPlaces.filter(
    (f) => CHOROPLETH_SEMANTIC.has(f.semanticType) || nameMatches(f.name, CHOROPLETH_NAME_HINTS)
  );
  if (choroGeo.length >= 1 && hasMeasure) {
    add("Choropleth", 92, "has a geographic region field + a measure");
  }
  if (temporals.length >= 1 && hasMeasure) {
    add("Line Chart", 88, "has a time field + a measure (trend over time)");
    add("Area Chart", 66, "has a time field + a measure");
  }
  if (measures.length >= 2) {
    add("Scatter Plot", 84, "has two or more measures (relationship)");
  }
  if (dims.length >= 1 && hasMeasure) {
    add("Bar Chart", 80, "has a category axis + a measure");
  }
  if (hasMeasure && dims.length === 0) {
    add("Histogram", 82, "has a measure with no category (distribution)");
  }
  if (catLike.length >= 2 && lowCardCatLike.length >= 1 && hasMeasure) {
    add("Grouped Bar Chart", 72, "has two categories + a measure");
    add("Stacked Bar Chart", 70, "has two categories + a measure");
  }
  if (lowCardDims.length >= 2 && hasMeasure) {
    add("Heatmap", 74, "has two discrete fields + a measure (matrix)");
  }
  if (catLike.length === 1 && temporals.length === 0 && hasMeasure) {
    const c = catLike[0];
    const oneRowPerCategory = rowCount <= c.cardinality * 1.5;
    if (c.cardinality >= 2 && c.cardinality <= PIE_MAX_SLICES && oneRowPerCategory) {
      add("Pie Chart", 64, "a few categories that sum to a whole");
    }
  }
  if (catLike.length >= 1 && hasMeasure && temporals.length === 0) {
    const smallest = catLike.reduce((a, b) => b.cardinality < a.cardinality ? b : a);
    if (smallest.cardinality >= 1 && rowCount >= smallest.cardinality * 2) {
      add("Boxplot", 58, "multiple measure values per group (spread)");
      add("Strip Plot", 52, "multiple measure values per group");
    }
  }
  if (!hasMeasure && dims.length >= 1) {
    add("Bar Chart", 60, "categories without a measure (counts)");
    if (lowCardDims.length >= 2) add("Heatmap", 55, "two discrete fields (cross-tab counts)");
  }
  if (order.length === 0) {
    if (measures.length >= 2) add("Scatter Plot", 20, "fallback for numeric data");
    else add("Bar Chart", 15, "fallback");
  }
  return order.map((chartType) => ({ chartType, score: acc.get(chartType).score, reasons: acc.get(chartType).reasons })).sort((a, b) => b.score - a.score);
}
function recommendChartTypesDetailed(data, semanticTypes, options = {}) {
  const profile = profileData(data, semanticTypes);
  let ranked = rankChartTypes(profile);
  if (options.supportedTypes) {
    const allowed = new Set(options.supportedTypes);
    ranked = ranked.filter((s) => allowed.has(s.chartType));
  }
  if (options.max != null) ranked = ranked.slice(0, options.max);
  return ranked;
}
function recommendChartTypes(data, semanticTypes, options = {}) {
  return recommendChartTypesDetailed(data, semanticTypes, options).map((s) => s.chartType);
}

// src/echarts/recommendation.ts
function ecGetRecommendation(chartType, tv) {
  const used = /* @__PURE__ */ new Set();
  const rec = {};
  const assign = (channel, fieldName) => {
    if (fieldName) rec[channel] = fieldName;
  };
  switch (chartType) {
    case "Gauge Chart": {
      const valueField = pickQuantitative(tv, used);
      if (!valueField) return {};
      assign("size", valueField);
      assign("column", pickLowCardDiscrete(tv, used, 10));
      return rec;
    }
    case "Funnel Chart": {
      const valueField = pickQuantitative(tv, used);
      const stageField = pickLowCardDiscrete(tv, used, 15);
      if (!valueField || !stageField) return {};
      assign("y", stageField);
      assign("size", valueField);
      return rec;
    }
    case "Treemap":
    case "Sunburst Chart": {
      const sizeField = pickQuantitative(tv, used);
      const colorField = pickLowCardDiscrete(tv, used, 20);
      if (!sizeField || !colorField) return {};
      assign("size", sizeField);
      assign("color", colorField);
      return rec;
    }
    case "Sankey Diagram": {
      const sourceField = pickDiscrete(tv, used);
      const targetField = pickDiscrete(tv, used);
      const valueField = pickQuantitative(tv, used);
      if (!sourceField || !targetField || !valueField) return {};
      assign("x", sourceField);
      assign("y", targetField);
      assign("size", valueField);
      return rec;
    }
    default:
      return getRecommendation(chartType, tv);
  }
}
function ecAdaptChart(sourceType, targetType, encodings, data, semanticTypes) {
  const targetChannels = ecGetTemplateChannels(targetType);
  return adaptChannels(sourceType, targetType, targetChannels, encodings, data, semanticTypes);
}
function ecRecommendEncodings(chartType, data, semanticTypes) {
  const rec = recommendChannels(chartType, data, semanticTypes, ecGetRecommendation);
  const validChannels = ecGetTemplateChannels(chartType);
  const result = {};
  for (const [ch, field] of Object.entries(rec)) {
    if (validChannels.includes(ch)) result[ch] = field;
  }
  return result;
}
var EC_SUPPORTED_TYPES = ecAllTemplateDefs.map((d) => d.chart);
function ecRecommendChartTypes(data, semanticTypes, options = {}) {
  return recommendChartTypes(data, semanticTypes, { ...options, supportedTypes: EC_SUPPORTED_TYPES });
}
function ecRecommendCharts(data, semanticTypes, options = {}) {
  const { max, ...rest } = options;
  const charts = ecRecommendChartTypes(data, semanticTypes, rest).map((chartType) => ({ chartType, encodings: ecRecommendEncodings(chartType, data, semanticTypes) })).filter((s) => Object.keys(s.encodings).length > 0);
  return max != null ? charts.slice(0, max) : charts;
}

export { assembleECharts, ecAdaptChart, ecAllTemplateDefs, ecApplyLayoutToSpec, ecApplyTooltips, ecGetTemplateChannels, ecGetTemplateDef, ecRecommendChartTypes, ecRecommendCharts, ecRecommendEncodings, ecTemplateDefs, getEChartsPivot, getEChartsTransform };
//# sourceMappingURL=index.js.map
//# sourceMappingURL=index.js.map