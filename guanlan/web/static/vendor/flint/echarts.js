// src/core/spec-updates.ts
function noteSpecUpdates(output, input) {
  const count = input.updates?.length ?? 0;
  if (!count || !output || typeof output !== "object") return output;
  const warning = {
    severity: "info",
    code: "updates-not-compiled",
    message: `The spec's ${count} update${count === 1 ? "" : "s"} (${input.updates.map((update) => `"${update.id}"`).join(", ")}) are applied by the interactive runtime (mountChart, <FlintChart>) once the chart is drawn; this output draws the chart without them.`
  };
  const notes = output;
  notes._warnings = [...notes._warnings ?? [], warning];
  return output;
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
  // A measure whose values should not be added up (or whose additivity is unknown); use Count, Amount, Quantity or Profit for totals.
  Number: { t0: "Measure", t1: "GenericMeasure", visEncodings: ["quantitative"], aggRole: "measure", domainShape: "open", diverging: "none", formatClass: "decimal", zeroBaseline: "meaningful", zeroPad: 0 },
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

// src/core/temporal-parse.ts
var MONTH_NUMBER = {
  jan: 1,
  feb: 2,
  mar: 3,
  apr: 4,
  may: 5,
  jun: 6,
  jul: 7,
  aug: 8,
  sep: 9,
  oct: 10,
  nov: 11,
  dec: 12
};
var MONTH = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\\.?";
var SEP = "[\\s\\-/._,]*";
var YEAR = "(\\d{4})";
var SHORT_YEAR = "'?(\\d{2})";
var ORDINAL = "(?:st|nd|rd|th)?";
var re = (source) => new RegExp(`^${source}$`, "i");
var monthOf = (name) => MONTH_NUMBER[name.slice(0, 3).toLowerCase()];
var fullYear = (twoDigits) => {
  const n = Number(twoDigits);
  return n <= 49 ? 2e3 + n : 1900 + n;
};
var pad = (n) => String(n).padStart(2, "0");
function yearMonth(year, month) {
  if (!Number.isInteger(year) || year < 1e3 || year > 2999) return null;
  if (!Number.isInteger(month) || month < 1 || month > 12) return null;
  return `${year}-${pad(month)}`;
}
function date(year, month, day) {
  const ym = yearMonth(year, month);
  if (!ym || !Number.isInteger(day) || day < 1) return null;
  if (new Date(Date.UTC(year, month - 1, day)).getUTCDate() !== day) return null;
  return `${ym}-${pad(day)}`;
}
var YEAR_MONTH_PARSERS = [
  // 202501
  [re("(\\d{4})(\\d{2})"), (m) => yearMonth(+m[1], +m[2])],
  // 2025-1, 2025/01, 2025.01, 2025 01
  [re(`${YEAR}[\\-/. ](\\d{1,2})`), (m) => yearMonth(+m[1], +m[2])],
  // 2025M01 (statistical-office style)
  [re(`${YEAR}\\s*M(\\d{1,2})`), (m) => yearMonth(+m[1], +m[2])],
  // 01/2025, 1-2025, 01.2025
  [re(`(\\d{1,2})[\\-/. ]${YEAR}`), (m) => yearMonth(+m[2], +m[1])],
  // 2025-Jul, 2025 July, 2025Jul
  [re(`${YEAR}${SEP}${MONTH}`), (m) => yearMonth(+m[1], monthOf(m[2]))],
  // Jul-2025, July 2025, Jul2025, July, 2025
  [re(`${MONTH}${SEP}${YEAR}`), (m) => yearMonth(+m[2], monthOf(m[1]))],
  // Jul-25, Jul '25
  [re(`${MONTH}[\\s\\-/.]*${SHORT_YEAR}`), (m) => yearMonth(fullYear(m[2]), monthOf(m[1]))]
];
var DATE_PARSERS = [
  // 20250115
  [re("(\\d{4})(\\d{2})(\\d{2})"), (m) => date(+m[1], +m[2], +m[3])],
  // 2025/01/15, 2025.1.15 (dashes already parse as ISO)
  [re(`${YEAR}[/.](\\d{1,2})[/.](\\d{1,2})`), (m) => date(+m[1], +m[2], +m[3])],
  // 2025-Jan-15, 2025 January 15
  [re(`${YEAR}${SEP}${MONTH}${SEP}(\\d{1,2})${ORDINAL}`), (m) => date(+m[1], monthOf(m[2]), +m[3])],
  // Jan 15, 2025; January 15th 2025; Jan-15-2025
  [re(`${MONTH}${SEP}(\\d{1,2})${ORDINAL}${SEP}${YEAR}`), (m) => date(+m[3], monthOf(m[1]), +m[2])],
  // 15 Jan 2025, 15-Jan-2025, 15th January, 2025
  [re(`(\\d{1,2})${ORDINAL}${SEP}${MONTH}${SEP}${YEAR}`), (m) => date(+m[3], monthOf(m[2]), +m[1])]
];
var PARSERS_BY_TYPE = {
  YearMonth: YEAR_MONTH_PARSERS,
  Date: DATE_PARSERS,
  DateTime: DATE_PARSERS
};
function hasTemporalParsers(semanticType) {
  return semanticType in PARSERS_BY_TYPE;
}
var ISO_TIMESTAMP = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/i;
function readIsoTimestamp(value, calendarGrain) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(ISO_TIMESTAMP);
  if (!match) return null;
  const [, day, hours, minutes, seconds = "00", fraction = "", zone] = match;
  const midnight = Number(hours) === 0 && Number(minutes) === 0 && Number(seconds) === 0 && !/[1-9]/.test(fraction);
  if (calendarGrain && midnight) return day;
  if (zone) return null;
  return `${day}T${hours}:${minutes}:${seconds}${fraction}`;
}
function parseTemporalByType(value, semanticType) {
  const parsers = PARSERS_BY_TYPE[semanticType];
  if (!parsers) return null;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) return null;
    value = String(value);
  }
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text) return null;
  for (const [pattern, read] of parsers) {
    const match = text.match(pattern);
    if (match) {
      const out = read(match);
      if (out) return out;
    }
  }
  return null;
}

// src/core/semantic-types.ts
var measureTypes = new Set(
  getRegisteredTypes().filter((t) => {
    const e = getRegistryEntry(t);
    return ["additive", "intensive", "signed-additive", "measure"].includes(e.aggRole) && e.t1 !== "Score";
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
var ISO_DATE = /^\d{4}-\d{2}(-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?)?$/;
var US_SLASH_DATE = /^\d{1,2}\/\d{1,2}\/\d{4}$/;
function isUnambiguousDateString(value) {
  const text = value.trim();
  if (ISO_DATE.test(text)) return Number.isFinite(Date.parse(text));
  if (US_SLASH_DATE.test(text)) return Number.isFinite(Date.parse(text));
  return parseTemporalByType(text, "Date") !== null;
}
function inferVisCategory(values) {
  if (values.length === 0) return "nominal";
  const isBoolean = (v) => v === true || v === false || Object.prototype.toString.call(v) === "[object Boolean]";
  const isNumber = (v) => !isNaN(+v) && !(Object.prototype.toString.call(v) === "[object Date]");
  const isDate = (v) => {
    if (v instanceof Date) return !isNaN(v.getTime());
    if (typeof v === "string") return isUnambiguousDateString(v);
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
var DISTRIBUTION_CHARTS = /* @__PURE__ */ new Set(["Boxplot", "Violin Plot", "Density Plot", "Ridgeline Plot", "ECDF Plot"]);
function zeroMarkType(chartType, templateMarkType) {
  if (chartType && DISTRIBUTION_CHARTS.has(chartType)) return "boxplot";
  return templateMarkType || "point";
}
function computeZeroDecision(semanticType, channel, markType, values) {
  const isBarLike = ["bar", "area", "rect"].includes(markType);
  const isScatterMark = markType === "circle" || markType === "point" || markType === "boxplot";
  const isLineIndex = (markType === "line" || markType === "trail") && channel === "x";
  const isPositional = ["x", "y"].includes(channel);
  const entry = getRegistryEntry(semanticType);
  const zeroClass = getZeroClass(semanticType);
  if (markType === "boxplot" && isPositional && values && values.length > 0 && Math.min(...values) <= 0) {
    return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
  }
  if (zeroClass === "meaningful") {
    if (isBarLike) {
      return { zero: true, domainPadFraction: 0, zeroClass, forced: true, uncertain: false };
    }
    if (isPositional && (isScatterMark || isLineIndex)) {
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
var DIVERGING_WARM_HIGH = "blueorange";
var DIVERGING_WARM_LOW = "redblue";
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
      return { scheme: DIVERGING_WARM_HIGH, type: "diverging", reason: "temperature diverging around freezing point, warm end high" };
    }
    return { scheme: "reds", type: "sequential", reason: "temperature single-direction uses sequential" };
  }
  if (semanticType === "Percentage") {
    if (colorHint?.type === "diverging") {
      return { scheme: DIVERGING_WARM_LOW, type: "diverging", reason: "percentage spans positive and negative, red is the losing side" };
    }
    return { scheme: "oranges", type: "sequential", reason: "percentage all same sign uses sequential" };
  }
  if (["Price", "Amount"].includes(semanticType)) {
    if (colorHint?.type === "diverging") {
      return { scheme: DIVERGING_WARM_LOW, type: "diverging", reason: "financial data spans positive and negative, red is the losing side" };
    }
    return { scheme: "goldgreen", type: "sequential", reason: "financial data uses gold-green" };
  }
  if (semanticType === "Score") {
    if (colorHint?.type === "diverging") {
      return { scheme: DIVERGING_WARM_LOW, type: "diverging", reason: "score diverging around midpoint, red is the poor side" };
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
      return { scheme: "tableau10", type: "categorical", reason: "places are named categories, at a contrast that survives thin marks" };
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
      scheme: uniqueValueCount > 8 ? "tableau20" : "tableau10",
      type: "categorical",
      reason: "names use readable categorical"
    };
  }
  if (semanticType === "Duration") {
    return { scheme: "oranges", type: "sequential", reason: "duration uses intensity-based sequential" };
  }
  if (measureTypes.has(semanticType)) {
    if (colorHint?.type === "diverging") {
      const signed = getRegistryEntry(semanticType).t1 === "SignedMeasure";
      return signed ? { scheme: DIVERGING_WARM_LOW, type: "diverging", reason: "signed measure, red is the negative side" } : { scheme: DIVERGING_WARM_HIGH, type: "diverging", reason: "measure with no valence, warm end high" };
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
function percentRepresentation(annotation, values) {
  if (annotation?.percentScale === "fraction") return "0-1";
  if (annotation?.percentScale === "percent") return "0-100";
  const domain = annotation?.intrinsicDomain;
  if (Array.isArray(domain) && domain.length === 2 && domain.every(Number.isFinite)) {
    const reach = Math.max(Math.abs(domain[0]), Math.abs(domain[1]));
    if (reach > 0 && reach <= 1) return "0-1";
    if (reach >= 100) return "0-100";
  }
  return detectPercentageRepresentation(values);
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
        const p = Math.min(detectPrecision(nums), 2);
        const axisPattern = semanticType === "Price" ? ",.2f" : p === 0 ? ",d" : `,.${p}~f`;
        return {
          format: { pattern: axisPattern, prefix: pfx },
          tooltipFormat: { pattern: ",.2f", prefix: pfx }
        };
      }
      return { tooltipFormat: { pattern: ",.2f" } };
    }
    case "percent": {
      const rep = percentRepresentation(annotation, nums);
      if (!annotation.intrinsicDomain && !annotation.percentScale && rep !== "0-1") {
        return { tooltipFormat: { pattern: precisionFormat(nums) } };
      }
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
    case "measure":
      return void 0;
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
      const rep = percentRepresentation(annotation, nums);
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
  if (annotation.divergingMidpoint !== void 0) {
    return { midpoint: annotation.divergingMidpoint, inherent: true, source: "annotation" };
  }
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
    case "measure":
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
  const { format: format2, tooltipFormat } = resolveFormat(semanticType, annotation, values);
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
    format: format2,
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

// src/core/theme/presence.ts
var HEX3 = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i;
var HEX6 = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;
var HEX8 = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;
function parseColor(input) {
  if (!input) return null;
  const s = String(input).trim();
  let m = HEX8.exec(s);
  if (m) {
    return {
      r: parseInt(m[1], 16),
      g: parseInt(m[2], 16),
      b: parseInt(m[3], 16),
      a: parseInt(m[4], 16) / 255
    };
  }
  m = HEX6.exec(s);
  if (m) {
    return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16), a: 1 };
  }
  m = HEX3.exec(s);
  if (m) {
    return {
      r: parseInt(m[1] + m[1], 16),
      g: parseInt(m[2] + m[2], 16),
      b: parseInt(m[3] + m[3], 16),
      a: 1
    };
  }
  return null;
}
function toHex(c) {
  const h = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${h(c.r)}${h(c.g)}${h(c.b)}`;
}
function mix(a, b, t) {
  const k = Math.max(0, Math.min(1, t));
  return {
    r: a.r + (b.r - a.r) * k,
    g: a.g + (b.g - a.g) * k,
    b: a.b + (b.b - a.b) * k,
    a: 1
  };
}
function mixHex(a, b, t, fallback = "#000000") {
  const ca = parseColor(a);
  const cb = parseColor(b);
  if (!ca || !cb) return fallback;
  return toHex(mix(ca, cb, t));
}

// src/core/color-controls.ts
var COLOR_CONTROL_CHANNELS = ["color", "group"];
function colorEncoding(encodings) {
  for (const channel of COLOR_CONTROL_CHANNELS) {
    const enc = encodings?.[channel];
    if (enc && typeof enc === "object" && !Array.isArray(enc) && enc.field) return enc;
  }
  return void 0;
}
function withEncodingColorAnnotations(semanticTypes, encodings) {
  const enc = colorEncoding(encodings);
  if (!enc?.field || enc.colors === void 0 && enc.mid === void 0) return semanticTypes;
  const base = normalizeAnnotation(semanticTypes[enc.field]);
  const folded = {
    ...base,
    semanticType: base.semanticType === "Unknown" ? "" : base.semanticType,
    ...typeof enc.mid === "number" ? { divergingMidpoint: enc.mid } : {},
    ...enc.colors ? { colors: { ...base.colors ?? {}, ...enc.colors } } : {}
  };
  return { ...semanticTypes, [enc.field]: folded };
}
function authoredColorScale(encodings) {
  const enc = colorEncoding(encodings);
  if (!enc?.field) return void 0;
  const named = typeof enc.scheme === "string" && enc.scheme !== "default" ? enc.scheme : void 0;
  const stops = Array.isArray(enc.scheme) && enc.scheme.length >= 2 ? enc.scheme.map(String) : void 0;
  const bins = typeof enc.bins === "number" && Number.isInteger(enc.bins) && (enc.bins === 0 || enc.bins >= 2) ? enc.bins : void 0;
  const domain = Array.isArray(enc.domain) && enc.domain.length === 2 && enc.domain.every((v) => typeof v === "number") ? [enc.domain[0], enc.domain[1]] : void 0;
  if (!named && !stops && enc.reverse === void 0 && !domain && bins === void 0) return void 0;
  return {
    field: enc.field,
    ...named ? { scheme: named } : {},
    ...stops ? { stops: bins ? resampleStops(stops, bins) : stops } : {},
    ...enc.reverse !== void 0 ? { reverse: Boolean(enc.reverse) } : {},
    ...domain ? { domain } : {},
    ...bins !== void 0 ? { bins } : {}
  };
}
function resampleStops(stops, count) {
  if (count < 2 || stops.length < 2) return stops.slice(0, Math.max(1, count));
  if (stops.length === count) return stops;
  const out = [];
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1) * (stops.length - 1);
    const k = Math.min(stops.length - 2, Math.floor(t));
    out.push(mixHex(stops[k], stops[k + 1], t - k, stops[k]));
  }
  return out;
}
function schemeName(scheme) {
  return typeof scheme === "string" ? scheme : void 0;
}
var ROUND_STEPS = [1, 2, 2.5, 5];
function roundBinEdges(min, max, count) {
  if (!(count >= 2) || !Number.isFinite(min) || !Number.isFinite(max) || !(max > min)) return void 0;
  const raw = (max - min) / count;
  let best;
  for (let exp = Math.floor(Math.log10(raw)) - 1; exp <= Math.floor(Math.log10(raw)) + 1; exp++) {
    for (const unit of ROUND_STEPS) {
      const step = unit * 10 ** exp;
      const first = Math.round(((min + max) / 2 - step * (count - 2) / 2) / step) * step;
      const edges = Array.from({ length: count - 1 }, (_, i) => Number((first + i * step).toPrecision(12)));
      if (edges[0] <= min || edges[edges.length - 1] >= max) continue;
      const cost = Math.abs(edges[0] - min - step) / step + Math.abs(max - edges[edges.length - 1] - step) / step;
      if (!best || cost < best.cost) best = { edges, cost };
    }
  }
  return best?.edges;
}
var SIMPLEST_STEPS = [1, 5, 2, 2.5];
var MAX_CLASSES_PER_SIDE = 4;
function roundColorClasses(values, target, pivot, options = {}) {
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!(target >= 2) || !(max > min)) return void 0;
  const crosses = pivot !== void 0 && Number.isFinite(pivot) && min < pivot && max > pivot;
  const anchor = crosses ? pivot : 0;
  const band = crosses ? options.neutral : void 0;
  const span = max - min;
  const most = Math.max(3, target + 2);
  const round = (value) => Number(value.toPrecision(12));
  let best;
  for (let exp = Math.floor(Math.log10(span / (most + 1))) - 1; exp <= Math.ceil(Math.log10(span)); exp++) {
    for (const unit of ROUND_STEPS) {
      const step = unit * 10 ** exp;
      if (band === "auto" && unit === 2.5) continue;
      const half = band === void 0 ? 0 : band === "auto" ? step / 2 : band;
      if (!(half >= 0)) continue;
      const reach = (extent) => Math.max(0, Math.ceil((extent - half) / step - 1e-9));
      const up = crosses ? reach(max - anchor) : 0;
      const down = crosses ? reach(anchor - min) : 0;
      const lo = crosses ? round(anchor - half - down * step) : round(Math.floor(min / step + 1e-9) * step);
      const hi = crosses ? round(anchor + half + up * step) : round(Math.ceil(max / step - 1e-9) * step);
      const breaks = crosses ? [
        ...Array.from({ length: down }, (_, i) => round(anchor - half - (down - 1 - i) * step)),
        ...Array.from({ length: up }, (_, i) => round(anchor + half + i * step))
      ].filter((edge, i, all) => i === 0 || edge !== all[i - 1]) : Array.from({ length: Math.round((hi - lo) / step) - 1 }, (_, i) => round(lo + (i + 1) * step));
      const count = breaks.length + 1;
      if (count < 3 || count > most) continue;
      if (options.exact && count !== target) continue;
      const simplicity = 1 - SIMPLEST_STEPS.indexOf(unit) / (SIMPLEST_STEPS.length - 1);
      const coverage = 1 - 0.5 * ((max - hi) ** 2 + (min - lo) ** 2) / (0.1 * span) ** 2;
      const density = 2 - Math.max(count / target, target / count);
      const score = 0.25 * simplicity + 0.2 * coverage + 0.5 * density;
      const capped = Math.max(down, up) > MAX_CLASSES_PER_SIDE;
      if (best && (capped && !best.capped || capped === best.capped && score <= best.score)) continue;
      best = {
        score,
        capped,
        classes: {
          breaks,
          lo,
          hi,
          ...crosses ? { below: down, above: up, ...half > 0 ? { neutral: true } : {} } : {}
        }
      };
    }
  }
  if (best || !options.exact) return best?.classes;
  const edges = roundBinEdges(min - anchor, max - anchor, target)?.map((edge) => round(edge + anchor));
  if (!edges) return void 0;
  const at = crosses ? edges.findIndex((edge) => Math.abs(edge - anchor) < 1e-9 * span) : -1;
  return { breaks: edges, ...at >= 0 ? { below: at + 1, above: target - at - 1 } : {} };
}
function rampAt(stops, t) {
  if (stops.length < 2) return stops[0];
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const k = Math.min(stops.length - 2, Math.floor(x));
  return mixHex(stops[k], stops[k + 1], x - k, stops[k]);
}
function classColors(stops, classes) {
  const { below, above } = classes;
  if (below === void 0 || above === void 0) return resampleStops(stops, classes.breaks.length + 1);
  const side = Math.max(1, below, above);
  return [
    ...Array.from({ length: below }, (_, i) => rampAt(stops, 0.5 - 0.5 * (below - i) / side)),
    ...classes.neutral ? [rampAt(stops, 0.5)] : [],
    ...Array.from({ length: above }, (_, i) => rampAt(stops, 0.5 + 0.5 * (i + 1) / side))
  ];
}

// ../../node_modules/d3-format/src/formatDecimal.js
function formatDecimal_default(x) {
  return Math.abs(x = Math.round(x)) >= 1e21 ? x.toLocaleString("en").replace(/,/g, "") : x.toString(10);
}
function formatDecimalParts(x, p) {
  if (!isFinite(x) || x === 0) return null;
  var i = (x = p ? x.toExponential(p - 1) : x.toExponential()).indexOf("e"), coefficient = x.slice(0, i);
  return [
    coefficient.length > 1 ? coefficient[0] + coefficient.slice(2) : coefficient,
    +x.slice(i + 1)
  ];
}

// ../../node_modules/d3-format/src/exponent.js
function exponent_default(x) {
  return x = formatDecimalParts(Math.abs(x)), x ? x[1] : NaN;
}

// ../../node_modules/d3-format/src/formatGroup.js
function formatGroup_default(grouping, thousands) {
  return function(value, width) {
    var i = value.length, t = [], j = 0, g = grouping[0], length = 0;
    while (i > 0 && g > 0) {
      if (length + g + 1 > width) g = Math.max(1, width - length);
      t.push(value.substring(i -= g, i + g));
      if ((length += g + 1) > width) break;
      g = grouping[j = (j + 1) % grouping.length];
    }
    return t.reverse().join(thousands);
  };
}

// ../../node_modules/d3-format/src/formatNumerals.js
function formatNumerals_default(numerals) {
  return function(value) {
    return value.replace(/[0-9]/g, function(i) {
      return numerals[+i];
    });
  };
}

// ../../node_modules/d3-format/src/formatSpecifier.js
var re2 = /^(?:(.)?([<>=^]))?([+\-( ])?([$#])?(0)?(\d+)?(,)?(\.\d+)?(~)?([a-z%])?$/i;
function formatSpecifier(specifier) {
  if (!(match = re2.exec(specifier))) throw new Error("invalid format: " + specifier);
  var match;
  return new FormatSpecifier({
    fill: match[1],
    align: match[2],
    sign: match[3],
    symbol: match[4],
    zero: match[5],
    width: match[6],
    comma: match[7],
    precision: match[8] && match[8].slice(1),
    trim: match[9],
    type: match[10]
  });
}
formatSpecifier.prototype = FormatSpecifier.prototype;
function FormatSpecifier(specifier) {
  this.fill = specifier.fill === void 0 ? " " : specifier.fill + "";
  this.align = specifier.align === void 0 ? ">" : specifier.align + "";
  this.sign = specifier.sign === void 0 ? "-" : specifier.sign + "";
  this.symbol = specifier.symbol === void 0 ? "" : specifier.symbol + "";
  this.zero = !!specifier.zero;
  this.width = specifier.width === void 0 ? void 0 : +specifier.width;
  this.comma = !!specifier.comma;
  this.precision = specifier.precision === void 0 ? void 0 : +specifier.precision;
  this.trim = !!specifier.trim;
  this.type = specifier.type === void 0 ? "" : specifier.type + "";
}
FormatSpecifier.prototype.toString = function() {
  return this.fill + this.align + this.sign + this.symbol + (this.zero ? "0" : "") + (this.width === void 0 ? "" : Math.max(1, this.width | 0)) + (this.comma ? "," : "") + (this.precision === void 0 ? "" : "." + Math.max(0, this.precision | 0)) + (this.trim ? "~" : "") + this.type;
};

// ../../node_modules/d3-format/src/formatTrim.js
function formatTrim_default(s) {
  out: for (var n = s.length, i = 1, i0 = -1, i1; i < n; ++i) {
    switch (s[i]) {
      case ".":
        i0 = i1 = i;
        break;
      case "0":
        if (i0 === 0) i0 = i;
        i1 = i;
        break;
      default:
        if (!+s[i]) break out;
        if (i0 > 0) i0 = 0;
        break;
    }
  }
  return i0 > 0 ? s.slice(0, i0) + s.slice(i1 + 1) : s;
}

// ../../node_modules/d3-format/src/formatPrefixAuto.js
var prefixExponent;
function formatPrefixAuto_default(x, p) {
  var d = formatDecimalParts(x, p);
  if (!d) return prefixExponent = void 0, x.toPrecision(p);
  var coefficient = d[0], exponent = d[1], i = exponent - (prefixExponent = Math.max(-8, Math.min(8, Math.floor(exponent / 3))) * 3) + 1, n = coefficient.length;
  return i === n ? coefficient : i > n ? coefficient + new Array(i - n + 1).join("0") : i > 0 ? coefficient.slice(0, i) + "." + coefficient.slice(i) : "0." + new Array(1 - i).join("0") + formatDecimalParts(x, Math.max(0, p + i - 1))[0];
}

// ../../node_modules/d3-format/src/formatRounded.js
function formatRounded_default(x, p) {
  var d = formatDecimalParts(x, p);
  if (!d) return x + "";
  var coefficient = d[0], exponent = d[1];
  return exponent < 0 ? "0." + new Array(-exponent).join("0") + coefficient : coefficient.length > exponent + 1 ? coefficient.slice(0, exponent + 1) + "." + coefficient.slice(exponent + 1) : coefficient + new Array(exponent - coefficient.length + 2).join("0");
}

// ../../node_modules/d3-format/src/formatTypes.js
var formatTypes_default = {
  "%": (x, p) => (x * 100).toFixed(p),
  "b": (x) => Math.round(x).toString(2),
  "c": (x) => x + "",
  "d": formatDecimal_default,
  "e": (x, p) => x.toExponential(p),
  "f": (x, p) => x.toFixed(p),
  "g": (x, p) => x.toPrecision(p),
  "o": (x) => Math.round(x).toString(8),
  "p": (x, p) => formatRounded_default(x * 100, p),
  "r": formatRounded_default,
  "s": formatPrefixAuto_default,
  "X": (x) => Math.round(x).toString(16).toUpperCase(),
  "x": (x) => Math.round(x).toString(16)
};

// ../../node_modules/d3-format/src/identity.js
function identity_default(x) {
  return x;
}

// ../../node_modules/d3-format/src/locale.js
var map = Array.prototype.map;
var prefixes = ["y", "z", "a", "f", "p", "n", "\xB5", "m", "", "k", "M", "G", "T", "P", "E", "Z", "Y"];
function locale_default(locale2) {
  var group = locale2.grouping === void 0 || locale2.thousands === void 0 ? identity_default : formatGroup_default(map.call(locale2.grouping, Number), locale2.thousands + ""), currencyPrefix = locale2.currency === void 0 ? "" : locale2.currency[0] + "", currencySuffix = locale2.currency === void 0 ? "" : locale2.currency[1] + "", decimal = locale2.decimal === void 0 ? "." : locale2.decimal + "", numerals = locale2.numerals === void 0 ? identity_default : formatNumerals_default(map.call(locale2.numerals, String)), percent = locale2.percent === void 0 ? "%" : locale2.percent + "", minus = locale2.minus === void 0 ? "\u2212" : locale2.minus + "", nan = locale2.nan === void 0 ? "NaN" : locale2.nan + "";
  function newFormat(specifier, options) {
    specifier = formatSpecifier(specifier);
    var fill = specifier.fill, align = specifier.align, sign = specifier.sign, symbol = specifier.symbol, zero = specifier.zero, width = specifier.width, comma = specifier.comma, precision = specifier.precision, trim = specifier.trim, type = specifier.type;
    if (type === "n") comma = true, type = "g";
    else if (!formatTypes_default[type]) precision === void 0 && (precision = 12), trim = true, type = "g";
    if (zero || fill === "0" && align === "=") zero = true, fill = "0", align = "=";
    var prefix = (options && options.prefix !== void 0 ? options.prefix : "") + (symbol === "$" ? currencyPrefix : symbol === "#" && /[boxX]/.test(type) ? "0" + type.toLowerCase() : ""), suffix = (symbol === "$" ? currencySuffix : /[%p]/.test(type) ? percent : "") + (options && options.suffix !== void 0 ? options.suffix : "");
    var formatType = formatTypes_default[type], maybeSuffix = /[defgprs%]/.test(type);
    precision = precision === void 0 ? 6 : /[gprs]/.test(type) ? Math.max(1, Math.min(21, precision)) : Math.max(0, Math.min(20, precision));
    function format2(value) {
      var valuePrefix = prefix, valueSuffix = suffix, i, n, c;
      if (type === "c") {
        valueSuffix = formatType(value) + valueSuffix;
        value = "";
      } else {
        value = +value;
        var valueNegative = value < 0 || 1 / value < 0;
        value = isNaN(value) ? nan : formatType(Math.abs(value), precision);
        if (trim) value = formatTrim_default(value);
        if (valueNegative && +value === 0 && sign !== "+") valueNegative = false;
        valuePrefix = (valueNegative ? sign === "(" ? sign : minus : sign === "-" || sign === "(" ? "" : sign) + valuePrefix;
        valueSuffix = (type === "s" && !isNaN(value) && prefixExponent !== void 0 ? prefixes[8 + prefixExponent / 3] : "") + valueSuffix + (valueNegative && sign === "(" ? ")" : "");
        if (maybeSuffix) {
          i = -1, n = value.length;
          while (++i < n) {
            if (c = value.charCodeAt(i), 48 > c || c > 57) {
              valueSuffix = (c === 46 ? decimal + value.slice(i + 1) : value.slice(i)) + valueSuffix;
              value = value.slice(0, i);
              break;
            }
          }
        }
      }
      if (comma && !zero) value = group(value, Infinity);
      var length = valuePrefix.length + value.length + valueSuffix.length, padding = length < width ? new Array(width - length + 1).join(fill) : "";
      if (comma && zero) value = group(padding + value, padding.length ? width - valueSuffix.length : Infinity), padding = "";
      switch (align) {
        case "<":
          value = valuePrefix + value + valueSuffix + padding;
          break;
        case "=":
          value = valuePrefix + padding + value + valueSuffix;
          break;
        case "^":
          value = padding.slice(0, length = padding.length >> 1) + valuePrefix + value + valueSuffix + padding.slice(length);
          break;
        default:
          value = padding + valuePrefix + value + valueSuffix;
          break;
      }
      return numerals(value);
    }
    format2.toString = function() {
      return specifier + "";
    };
    return format2;
  }
  function formatPrefix2(specifier, value) {
    var e = Math.max(-8, Math.min(8, Math.floor(exponent_default(value) / 3))) * 3, k = Math.pow(10, -e), f = newFormat((specifier = formatSpecifier(specifier), specifier.type = "f", specifier), { suffix: prefixes[8 + e / 3] });
    return function(value2) {
      return f(k * value2);
    };
  }
  return {
    format: newFormat,
    formatPrefix: formatPrefix2
  };
}

// ../../node_modules/d3-format/src/defaultLocale.js
var locale;
var format;
defaultLocale({
  thousands: ",",
  grouping: [3],
  currency: ["$", ""]
});
function defaultLocale(definition) {
  locale = locale_default(definition);
  format = locale.format;
  locale.formatPrefix;
  return locale;
}
var clean = (value) => Number(Math.abs(value).toPrecision(12));
function decimalsOf(value) {
  if (!Number.isFinite(value)) return 0;
  const match = /^\d+(?:\.(\d+))?(?:e([+-]\d+))?$/.exec(String(clean(value)));
  if (!match) return 0;
  return Math.max(0, (match[1]?.length ?? 0) - Number(match[2] ?? 0));
}
function exactPattern(values, pattern) {
  const finite = values.filter(Number.isFinite);
  const decimals = (scale = 1) => Math.max(0, ...finite.map((value) => decimalsOf(value * scale)));
  return `,.${decimals()}~f`;
}
function exactFormat(values, fmt2) {
  return { ...fmt2, pattern: exactPattern(values) };
}
function printFormatted(value, fmt2) {
  const prefix = fmt2?.prefix ?? "";
  const suffix = fmt2?.suffix ?? "";
  if (fmt2?.abbreviate) {
    const abs = Math.abs(value);
    const [divisor, unit] = abs >= 1e12 ? [1e12, "T"] : abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : abs >= 1e3 ? [1e3, "K"] : [1, ""];
    return `${prefix}${unit ? format("~g")(value / divisor) + unit : format(",")(value)}${suffix}`;
  }
  let shown;
  try {
    shown = fmt2?.pattern ? format(fmt2.pattern)(value) : String(value);
  } catch {
    shown = String(value);
  }
  return `${prefix}${shown}${suffix}`;
}

// src/core/decisions.ts
var BANDED_CHART_TYPES = /* @__PURE__ */ new Set([
  "Bar Chart",
  "Stacked Bar Chart",
  "Grouped Bar Chart",
  "Lollipop Chart",
  "Bar Table",
  "Waterfall Chart",
  "Pyramid Chart",
  "Heatmap",
  "Boxplot",
  "Violin Plot",
  "Strip Plot",
  "Ranged Dot Plot"
]);
var SNAPSHOT_MAX_DATES = 8;
var SNAPSHOT_MAX_COVERAGE = 0.5;
var SNAPSHOT_MAX_BANDS = 50;
var DAY_MS = 864e5;
function snapshotTime(value) {
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value.getTime() : void 0;
  if (typeof value === "number") {
    if (Number.isInteger(value) && value >= 1e3 && value <= 2999) return Date.UTC(value, 0, 1);
    return Number.isFinite(value) ? value : void 0;
  }
  if (typeof value !== "string") return void 0;
  const text = value.trim();
  if (/^\d{4}$/.test(text)) return Date.UTC(Number(text), 0, 1);
  const time = Date.parse(text);
  return Number.isFinite(time) ? time : void 0;
}
var gcd = (a, b) => b === 0 ? a : gcd(b, a % b);
function isDateField(semanticType, values) {
  if (semanticType && isRegistered(semanticType)) {
    const entry = getRegistryEntry(semanticType);
    return entry.t0 === "Temporal" && entry.t1 !== "Duration" && entry.domainShape !== "cyclic";
  }
  return inferVisCategory([...values]) === "temporal";
}
function chronologicalOrder(values) {
  const seen = /* @__PURE__ */ new Map();
  for (const value of values) {
    if (value == null || value === "") continue;
    const key = value instanceof Date ? value.toISOString() : String(value);
    if (seen.has(key)) continue;
    const time = snapshotTime(value);
    if (time === void 0) return void 0;
    seen.set(key, time);
  }
  if (seen.size < 2) return void 0;
  return [...seen.entries()].sort((a, b) => a[1] - b[1]).map(([key]) => key);
}
var MONTH_DAYS = 365.2425 / 12;
var MONTH_SLACK_DAYS = 4;
var CADENCE_SLACK = 0.25;
function dateCadence(times) {
  if (times.length < 2) return void 0;
  const count = times.length;
  const span = times[count - 1] - times[0];
  const gaps = times.slice(1).map((time, i) => time - times[i]);
  const month = times.map((time) => {
    const d = new Date(time);
    return d.getUTCFullYear() * 12 + d.getUTCMonth();
  });
  const monthGaps = month.slice(1).map((value, i) => value - month[i]);
  if (monthGaps.every((g, i) => g > 0 && Math.abs(gaps[i] / DAY_MS - g * MONTH_DAYS) <= MONTH_SLACK_DAYS)) {
    const step = monthGaps.reduce((g, value) => gcd(g, value), 0);
    return { months: step, exact: true, count, periods: (month[count - 1] - month[0]) / step + 1 };
  }
  const least = Math.min(...gaps);
  const common = gaps.reduce((g, value) => gcd(g, value), 0);
  if (!(least > 0) || !(common > 0)) return void 0;
  if (common === least) return { ms: least, exact: true, count, periods: Math.round(span / least) + 1 };
  const usual = [...gaps].sort((a, b) => a - b)[Math.floor(gaps.length / 2)];
  const keeps = gaps.every((g) => Math.abs(g - Math.max(1, Math.round(g / usual)) * usual) <= CADENCE_SLACK * usual);
  if (keeps) return { ms: usual, exact: false, count, periods: Math.round(span / usual) + 1 };
  const midnight = times.every((time) => time % DAY_MS === 0);
  return midnight ? { ms: common, exact: true, count, periods: span / common + 1 } : void 0;
}
function calendarCoverage(times) {
  const cadence = dateCadence(times);
  if (!cadence) return void 0;
  return {
    count: cadence.count,
    periods: cadence.periods,
    monthly: cadence.months !== void 0,
    step: cadence.months ?? Math.round(cadence.ms / DAY_MS)
  };
}
var distinctTimes = (values) => [...new Set(values.map(snapshotTime).filter((time) => time !== void 0))].sort((a, b) => a - b);
function readsAsSnapshots(values) {
  const times = distinctTimes(values);
  if (times.length < 2) return false;
  if (times.length <= SNAPSHOT_MAX_DATES) return true;
  if (times.length > SNAPSHOT_MAX_BANDS) return false;
  const coverage = calendarCoverage(times);
  return !!coverage && coverage.count / coverage.periods < SNAPSHOT_MAX_COVERAGE;
}
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
  const sampleValues = [];
  const seen = /* @__PURE__ */ new Set();
  for (const row of data) {
    const value = row[fieldName];
    if (value == null) continue;
    const key = value instanceof Date ? `date:${value.getTime()}` : `${typeof value}:${String(value)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    sampleValues.push(value);
    if (sampleValues.length >= 15) break;
  }
  if (sampleValues.length === 0) return false;
  if (sampleValues.length <= 1) return false;
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
function resolveTemporalEncoding(visCategory, channel, data, fieldName, fromRegistry, context = {}, fieldValues = data.map((r) => r[fieldName])) {
  if (["size", "column", "row"].includes(channel)) {
    return { vlType: "ordinal", visCategory, channelOverride: true, cardinalityGuard: false };
  }
  if (channel === "color" || channel === "group") {
    const uniqueCount = new Set(data.map((r) => r[fieldName])).size;
    if (uniqueCount <= 12) {
      return { vlType: "ordinal", visCategory, channelOverride: true, cardinalityGuard: false };
    }
  }
  if (!validateTemporalParsing(data, fieldName, fromRegistry)) {
    return { vlType: "ordinal", visCategory, channelOverride: false, cardinalityGuard: false };
  }
  if ((channel === "x" || channel === "y") && context.chartType && BANDED_CHART_TYPES.has(context.chartType) && readsAsSnapshots(fieldValues)) {
    return { vlType: "ordinal", visCategory, channelOverride: true, cardinalityGuard: true, snapshots: true };
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
function disambiguateMultiEncoding(candidates, channel, data, fieldName, fieldValues, context = {}) {
  const has = (vc) => candidates.includes(vc);
  if (has("temporal") && has("ordinal")) {
    return resolveTemporalEncoding("temporal", channel, data, fieldName, true, context, fieldValues);
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
function resolveEncodingType(semanticType, fieldValues, channel, data, fieldName, context = {}) {
  if (semanticType && isRegistered(semanticType)) {
    const entry = getRegistryEntry(semanticType);
    const candidates = entry.visEncodings;
    if (candidates.length > 1) {
      return disambiguateMultiEncoding(candidates, channel, data, fieldName, fieldValues, context);
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
      return resolveTemporalEncoding(baseType, channel, data, fieldName, true, context, fieldValues);
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
      return resolveTemporalEncoding(visCategory, channel, data, fieldName, false, context, fieldValues);
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
  const itemCount = nominalCount > 0 ? nominalCount : continuousCount;
  if (itemCount > 0) {
    const fit = Math.max(0, Math.min(1, params.bandStepFit ?? 0));
    const baseSpanStep = baseDimension / itemCount;
    const capacityStep = baseDimension * (params.bandStepFitCapacity ?? 1) / itemCount;
    const fitted = fit > 0 && baseSpanStep > params.defaultStepSize && (params.bandStepFitCapacity ?? 1) > 1;
    const preferredStep = baseSpanStep > params.defaultStepSize ? params.defaultStepSize * (1 - fit) + capacityStep * fit : params.defaultStepSize;
    if (preferredStep <= baseSpanStep) {
      return { step: Math.floor(preferredStep), budget: baseDimension, itemCount, fitted };
    }
    const { budget } = computeElasticBudget(itemCount, baseDimension, {
      ...params,
      defaultStepSize: preferredStep
    });
    return {
      step: Math.floor(Math.min(preferredStep, budget / itemCount)),
      budget,
      itemCount,
      fitted
    };
  }
  return { step: params.defaultStepSize, budget: baseDimension, itemCount: 0, fitted: false };
}
function fitTwoLineLabels(labels, fontSize, maxWidth, ellipsis = false, widthOf = (text) => text.length * fontSize * 0.62) {
  const lines = [];
  let width = 0;
  for (const label of labels) {
    if (widthOf(label) <= maxWidth) {
      lines.push([label]);
      width = Math.max(width, widthOf(label));
      continue;
    }
    let best;
    let bestWidth = Infinity;
    for (const match of label.matchAll(/\s+/g)) {
      const candidate = [label.slice(0, match.index), label.slice(match.index + match[0].length)];
      if (candidate.some((line) => !line)) continue;
      const candidateWidth = Math.max(...candidate.map(widthOf));
      const avoidsWidow = /\s/.test(candidate[1].trim());
      const bestAvoidsWidow = best && /\s/.test(best[1].trim());
      if (candidateWidth <= maxWidth ? bestWidth > maxWidth || avoidsWidow || !bestAvoidsWidow : bestWidth > maxWidth && candidateWidth < bestWidth) {
        best = candidate;
        bestWidth = candidateWidth;
      }
    }
    if (!best || bestWidth > maxWidth) {
      if (!ellipsis) return null;
      let split;
      for (const match of label.matchAll(/\s+/g)) {
        if (widthOf(label.slice(0, match.index)) <= maxWidth) split = match;
      }
      if (split) {
        lines.push([label.slice(0, split.index), label.slice(split.index + split[0].length)]);
      } else {
        lines.push(best ?? [label]);
      }
      width = Math.max(width, maxWidth);
      continue;
    }
    lines.push(best);
    width = Math.max(width, bestWidth);
  }
  return lines.some((label) => label.length > 1) ? { lines, width } : null;
}
function computeBandLabelLayout(input) {
  const { labels, axis, fontSize, step, baseSpan, maxSpan, gutterLimit, baselineLimit, elasticity, measure: measure2 } = input;
  if (!labels.length || baseSpan <= 0 || maxSpan <= 0) return null;
  const widthOf = measure2 ?? ((text) => text.length * fontSize * 0.62);
  const lineHeight2 = Math.ceil(fontSize * 1.2);
  const widths = labels.map(widthOf);
  const demands = labels.map((label, index) => {
    let width2 = widths[index];
    for (const match of label.matchAll(/\s+/g)) {
      if (!match.index || match.index + match[0].length === label.length) continue;
      width2 = Math.min(width2, Math.max(widthOf(label.slice(0, match.index)), widthOf(label.slice(match.index + match[0].length))));
    }
    return width2;
  });
  const count = labels.length;
  const baseStep = baseSpan / count;
  const preferredWidth = Math.min(Math.max(...demands), gutterLimit);
  const wrappedLabelBandFraction = 0.75;
  const minimumYStepForLines = (rows2) => rows2 > 1 ? Math.ceil((fontSize + (rows2 - 1) * lineHeight2) / wrappedLabelBandFraction) : fontSize + 4;
  const preferredDemands = axis === "x" ? demands.map((width2) => width2 + 6) : widths.map((width2) => width2 > preferredWidth ? minimumYStepForLines(2) : lineHeight2 + 6);
  const excess = preferredDemands.reduce((sum, demand) => sum + Math.min(baseStep, Math.max(0, demand - baseStep)), 0) / count;
  const preferredStep = Math.max(step, Math.min(Math.max(...preferredDemands), baseStep) + excess);
  const solved = computeAxisStep(count, 0, baseSpan, {
    defaultStepSize: Math.ceil(preferredStep),
    minStep: Math.min(step, baseStep),
    elasticity,
    maxStretch: Math.max(1, maxSpan / baseSpan)
  });
  const minimumYStep = minimumYStepForLines(widths.some((width2) => width2 > preferredWidth) ? 2 : 1);
  const needed = input.stretchToFit && axis === "x" ? Math.ceil(Math.max(...preferredDemands)) : 0;
  const proposedStep = Math.min(maxSpan / count, Math.max(step, solved.step, needed, axis === "y" ? minimumYStep : 0));
  const width = axis === "x" ? proposedStep - 6 : preferredWidth;
  const widestWord = Math.max(0, ...labels.flatMap((label) => label.split(/\s+/)).map(widthOf));
  if ((measure2 ? width < widestWord : width < fontSize * 4) && widths.some((labelWidth) => labelWidth > width)) return null;
  const fit = fitTwoLineLabels(labels, fontSize, width, true, widthOf);
  const lines = fit?.lines ?? labels.map((label) => [label]);
  const rows = Math.max(...lines.map((parts) => parts.length));
  if (axis === "y" && minimumYStepForLines(rows) > proposedStep) return null;
  const retainedWidth = (text, limit) => widthOf(text) <= limit ? widthOf(text) : Math.max(0, limit - fontSize);
  const loss = (parts, limit) => parts.reduce((sum, row, index) => {
    const retained = row.reduce((total, line) => total + retainedWidth(line, limit), 0) + (row.length - 1) * widthOf(" ");
    return sum + Math.max(0, 1 - retained / Math.max(1, widths[index]));
  }, 0) / count;
  const imbalance = (parts, limit) => {
    const extents = parts.map((row) => Math.min(limit, Math.max(...row.map(widthOf))));
    return (Math.max(...extents) - extents.reduce((sum, value) => sum + value, 0) / count) / Math.max(1, gutterLimit);
  };
  const baseline = labels.map((label) => [label]);
  const expansion = Math.max(0, (Math.min(proposedStep * count, baseSpan) - Math.min(step * count, baseSpan)) / baseSpan);
  const stretch = Math.max(0, proposedStep * count / baseSpan - 1);
  const baselineStretch = Math.max(0, step * count / baseSpan - 1);
  const wrappedShare = lines.filter((row) => row.length > 1).length / count;
  const horizontalCost = loss(lines, width) + 0.18 * expansion + Math.max(0, stretch * stretch - baselineStretch * baselineStretch) + 0.04 * wrappedShare + (axis === "y" ? 0.15 * imbalance(lines, width) : 0);
  const baselineCost = loss(baseline, baselineLimit) + (axis === "x" ? 0.3 : 0.15 * imbalance(baseline, baselineLimit));
  if (input.compareBaseline !== false && horizontalCost > baselineCost) return null;
  if (axis === "x" && !fit && widths.some((labelWidth) => labelWidth > width)) return null;
  if (axis === "y" && !fit) return null;
  return { step: proposedStep, width, lines, lineHeight: lineHeight2, cost: horizontalCost };
}
function computeLabelSizing(effectiveStep, hasDiscreteItems, opts) {
  const baseFont = opts?.baseFont ?? 10;
  const minFont = opts?.minFont ?? 6;
  const defaultLimit = 100;
  if (!hasDiscreteItems) {
    return { fontSize: baseFont, labelLimit: defaultLimit };
  }
  const fontStep = Math.floor(effectiveStep);
  let fontSize = Math.max(minFont, Math.min(baseFont, fontStep - 1));
  let labelLimit = Math.max(30, Math.min(100, effectiveStep * 8));
  let labelAngle;
  let labelAlign;
  let labelBaseline;
  if (effectiveStep < 10) {
    labelAngle = -90;
    fontSize = Math.max(minFont, Math.min(baseFont - 2, fontStep));
    labelLimit = 40;
    labelAlign = "right";
    labelBaseline = "middle";
  } else if (effectiveStep < 16) {
    labelAngle = -90;
    fontSize = Math.max(minFont, Math.min(baseFont - 1, fontStep));
    labelLimit = 60;
    labelAlign = "right";
    labelBaseline = "middle";
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
var isDiscrete = (type) => type === "nominal" || type === "ordinal";
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
  if (xCS && isDiscrete(xCS.type)) {
    return { categoryAxis: "x", valueAxis: "y" };
  }
  if (yCS && isDiscrete(yCS.type)) {
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

// src/echarts/color-controls.ts
function applyAuthoredColorScaleEC(option, ctl, rows = [], pivot, neutral) {
  const maps = Array.isArray(option?.visualMap) ? option.visualMap : option?.visualMap ? [option.visualMap] : [];
  const values = rows.map((row) => row?.[ctl.field]).filter((value) => typeof value === "number" && Number.isFinite(value));
  for (const vm of maps) {
    const current = vm.inRange?.color;
    if (!Array.isArray(current)) continue;
    let colors = ctl.stops ?? (ctl.scheme ? getPaletteForScheme(ctl.scheme) : void 0) ?? current;
    if (ctl.bins !== void 0 && ctl.bins > 0) {
      vm.type = "piecewise";
      delete vm.calculable;
      const classes = ctl.domain ? void 0 : roundColorClasses(values, ctl.bins, pivot, { exact: true, neutral });
      if (classes) {
        const ramp = ctl.reverse ? [...colors].reverse() : colors;
        const inks = classColors(ramp, classes);
        delete vm.splitNumber;
        if (classes.lo !== void 0 && classes.hi !== void 0) {
          vm.pieces = classPieces(classes.breaks, classes.lo, classes.hi, inks);
          vm.min = classes.lo;
          vm.max = classes.hi;
        } else vm.pieces = binPieces(classes.breaks, inks);
        vm.inRange = { ...vm.inRange, color: inks };
        continue;
      }
      colors = resampleStops(colors, ctl.bins);
      const lo = Number(ctl.domain?.[0] ?? vm.min);
      const hi = Number(ctl.domain?.[1] ?? vm.max);
      const edges = roundBinEdges(lo, hi, ctl.bins);
      if (edges) {
        delete vm.splitNumber;
        vm.pieces = binPieces(edges, ctl.reverse ? [...colors].reverse() : colors);
        vm.inRange = { ...vm.inRange, color: ctl.reverse ? [...colors].reverse() : colors };
        if (ctl.domain) {
          vm.min = ctl.domain[0];
          vm.max = ctl.domain[1];
        }
        continue;
      }
      vm.splitNumber = ctl.bins;
    } else if (ctl.bins === 0 && vm.type === "piecewise") {
      vm.type = "continuous";
      delete vm.splitNumber;
    }
    if (ctl.reverse) colors = [...colors].reverse();
    vm.inRange = { ...vm.inRange, color: colors };
    if (ctl.domain) {
      vm.min = ctl.domain[0];
      vm.max = ctl.domain[1];
    }
  }
}
var fmt = (value) => value.toLocaleString("en-US").replace("-", "\u2212");
function binPieces(edges, colors) {
  const pieces = [{ lt: edges[0], label: `< ${fmt(edges[0])}`, color: colors[0] }];
  for (let i = 1; i < edges.length; i++) {
    pieces.push({ gte: edges[i - 1], lt: edges[i], label: `${fmt(edges[i - 1])} \u2013 ${fmt(edges[i])}`, color: colors[i] });
  }
  pieces.push({ gte: edges[edges.length - 1], label: `\u2265 ${fmt(edges[edges.length - 1])}`, color: colors[edges.length] });
  return pieces;
}
function classPieces(breaks, lo, hi, colors) {
  const edges = [lo, ...breaks, hi];
  const exact = exactFormat(edges);
  const print = (value) => printFormatted(value, exact).replace("-", "\u2212");
  return colors.map((color, i) => ({
    gte: edges[i],
    ...i === colors.length - 1 ? { lte: edges[i + 1] } : { lt: edges[i + 1] },
    label: `${print(edges[i])} \u2013 ${print(edges[i + 1])}`,
    color
  }));
}

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
var ONE_MARK_PER_KEY = {
  Heatmap: ["color"]
};
function resolveImplicitAggregation(chartType, encodings, data, semanticTypes) {
  const measures = ONE_MARK_PER_KEY[chartType];
  if (!measures || !data || data.length < 2) return { encodings, warnings: [] };
  const open = measures.filter((ch) => encodings[ch]?.field && !encodings[ch].aggregate);
  if (open.length === 0) return { encodings, warnings: [] };
  const keyFields = [...new Set(Object.entries(encodings).filter(([ch, enc]) => !measures.includes(ch) && enc?.field && !enc.aggregate).map(([, enc]) => enc.field))];
  const seen = /* @__PURE__ */ new Set();
  let rowsPerKey = 1;
  const counts = /* @__PURE__ */ new Map();
  for (const row of data) {
    const key = JSON.stringify(keyFields.map((f) => row[f] ?? null));
    const n = (counts.get(key) ?? 0) + 1;
    counts.set(key, n);
    if (n > rowsPerKey) rowsPerKey = n;
    seen.add(key);
  }
  if (rowsPerKey < 2) return { encodings, warnings: [] };
  const next = { ...encodings };
  const warnings = [];
  for (const ch of open) {
    const field = encodings[ch].field;
    const values = data.map((r) => r[field]).filter((v) => v != null);
    if (!values.length || !values.every((v) => typeof v === "number" || Number.isFinite(Number(v)))) continue;
    const op = resolveAggregationDefault(toTypeString(semanticTypes[field])) === "sum" ? "sum" : "average";
    next[ch] = { ...encodings[ch], aggregate: op };
    warnings.push({
      severity: "warning",
      code: "implicit-aggregate",
      channel: ch,
      field,
      message: `Up to ${rowsPerKey} rows share one ${keyFields.join(" \xD7 ")} cell; ${field} is aggregated with ${op} (${seen.size} cells from ${data.length} rows). Set "aggregate" on ${ch} to choose another operation.`
    });
  }
  return { encodings: next, warnings };
}
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
    { to: "Marginal Plot", label: "Margins" },
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
  "Ridgeline Plot": [
    { to: "Density Plot", label: "Density" }
  ],
  "Marginal Plot": [
    { to: "Scatter Plot", label: "Scatter" }
  ],
  "Density Plot": [
    { to: "Ridgeline Plot", label: "Ridgeline" },
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
function isDiscrete2(enc) {
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
  if (isDiscrete2(enc) || isTemporal(enc) && temporalActsDiscrete(template)) return "category";
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
    if (channels.includes(channel) && isDiscrete2(base[channel])) {
      return { channel, enc: base[channel] };
    }
  }
  return null;
}
function seriesRoutingStates(base, template, data, shiftChannels, facetBudget, preferredFacet) {
  const channels = template.channels ?? [];
  const out = [];
  const identitySource = isDiscrete2(base.color) ? "color" : !base.color?.field && isDiscrete2(base.group) ? "group" : void 0;
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
    (channel) => shiftChannels.includes(channel) && channels.includes(channel) && isDiscrete2(base[channel])
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
    const measure2 = measureAxisEnc(base);
    if (measure2?.field) {
      for (const row of data) {
        const v = row?.[measure2.field];
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
      if (isDiscrete2(base[ch])) return false;
    }
  }
  if (t.requireSeries) {
    const seriesChannels = ["color", "group", "detail", "column", "row"];
    if (!seriesChannels.some((ch) => isDiscrete2(base[ch]))) return false;
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
    if (t.requireDiscreteSource && !isDiscrete2(srcEnc)) return null;
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

// src/echarts/graphic-ids.ts
var EC_LEGEND_TITLE_ID = "flint-legend-title";
var EC_SIZE_KEY_ID = "flint-size-key";
var rampId = (channel) => `flint-ramp:${channel}`;
var rampTitleId = (channel) => `flint-ramp-title:${channel}`;

// src/core/text-widths.generated.ts
var TEXT_WIDTHS = {
  "Helvetica Neue": {
    normal: [0.278, 0.259, 0.426, 0.556, 0.556, 1, 0.63, 0.278, 0.259, 0.259, 0.352, 0.6, 0.278, 0.389, 0.278, 0.333, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.278, 0.278, 0.6, 0.6, 0.6, 0.556, 0.8, 0.648, 0.685, 0.722, 0.704, 0.611, 0.574, 0.759, 0.722, 0.259, 0.519, 0.667, 0.556, 0.871, 0.722, 0.76, 0.648, 0.76, 0.685, 0.648, 0.574, 0.722, 0.611, 0.926, 0.611, 0.648, 0.611, 0.259, 0.333, 0.259, 0.6, 0.5, 0.222, 0.537, 0.593, 0.537, 0.593, 0.537, 0.296, 0.574, 0.556, 0.222, 0.222, 0.519, 0.222, 0.853, 0.556, 0.574, 0.593, 0.593, 0.333, 0.5, 0.315, 0.556, 0.5, 0.758, 0.518, 0.5, 0.48, 0.333, 0.222, 0.333, 0.6],
    bold: [0.278, 0.278, 0.463, 0.556, 0.556, 1, 0.685, 0.278, 0.296, 0.296, 0.407, 0.6, 0.278, 0.407, 0.278, 0.371, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.278, 0.278, 0.6, 0.6, 0.6, 0.556, 0.8, 0.685, 0.704, 0.741, 0.741, 0.648, 0.593, 0.759, 0.741, 0.295, 0.556, 0.722, 0.593, 0.907, 0.741, 0.778, 0.667, 0.778, 0.722, 0.649, 0.611, 0.741, 0.63, 0.944, 0.667, 0.667, 0.648, 0.333, 0.371, 0.333, 0.6, 0.5, 0.259, 0.574, 0.611, 0.574, 0.611, 0.574, 0.333, 0.611, 0.593, 0.258, 0.278, 0.574, 0.258, 0.906, 0.593, 0.611, 0.611, 0.611, 0.389, 0.537, 0.352, 0.593, 0.52, 0.814, 0.537, 0.519, 0.519, 0.333, 0.223, 0.333, 0.6]
  },
  "Helvetica": {
    normal: [0.278, 0.278, 0.355, 0.556, 0.556, 0.889, 0.667, 0.191, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.278, 0.278, 0.584, 0.584, 0.584, 0.556, 1.015, 0.667, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.5, 0.667, 0.556, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.278, 0.278, 0.278, 0.469, 0.556, 0.333, 0.556, 0.556, 0.5, 0.556, 0.556, 0.278, 0.556, 0.556, 0.222, 0.222, 0.5, 0.222, 0.833, 0.556, 0.556, 0.556, 0.556, 0.333, 0.5, 0.278, 0.556, 0.5, 0.722, 0.5, 0.5, 0.5, 0.334, 0.26, 0.334, 0.584],
    bold: [0.278, 0.333, 0.474, 0.556, 0.556, 0.889, 0.722, 0.238, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.333, 0.333, 0.584, 0.584, 0.584, 0.611, 0.975, 0.722, 0.722, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.556, 0.722, 0.611, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.333, 0.278, 0.333, 0.584, 0.556, 0.333, 0.556, 0.611, 0.556, 0.611, 0.556, 0.333, 0.611, 0.611, 0.278, 0.278, 0.556, 0.278, 0.889, 0.611, 0.611, 0.611, 0.611, 0.389, 0.556, 0.333, 0.611, 0.556, 0.778, 0.556, 0.556, 0.5, 0.389, 0.28, 0.389, 0.584]
  },
  "Arial": {
    normal: [0.278, 0.278, 0.355, 0.556, 0.556, 0.889, 0.667, 0.191, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.278, 0.278, 0.584, 0.584, 0.584, 0.556, 1.015, 0.667, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.5, 0.667, 0.556, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.278, 0.278, 0.278, 0.469, 0.556, 0.333, 0.556, 0.556, 0.5, 0.556, 0.556, 0.278, 0.556, 0.556, 0.222, 0.222, 0.5, 0.222, 0.833, 0.556, 0.556, 0.556, 0.556, 0.333, 0.5, 0.278, 0.556, 0.5, 0.722, 0.5, 0.5, 0.5, 0.334, 0.26, 0.334, 0.584],
    bold: [0.278, 0.333, 0.474, 0.556, 0.556, 0.889, 0.722, 0.238, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.333, 0.333, 0.584, 0.584, 0.584, 0.611, 0.975, 0.722, 0.722, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.556, 0.722, 0.611, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.333, 0.278, 0.333, 0.584, 0.556, 0.333, 0.556, 0.611, 0.556, 0.611, 0.556, 0.333, 0.611, 0.611, 0.278, 0.278, 0.556, 0.278, 0.889, 0.611, 0.611, 0.611, 0.611, 0.389, 0.556, 0.333, 0.611, 0.556, 0.778, 0.556, 0.556, 0.5, 0.389, 0.28, 0.389, 0.584]
  },
  "Verdana": {
    normal: [0.352, 0.394, 0.459, 0.818, 0.636, 1.076, 0.727, 0.269, 0.454, 0.454, 0.636, 0.818, 0.364, 0.454, 0.364, 0.454, 0.636, 0.636, 0.636, 0.636, 0.636, 0.636, 0.636, 0.636, 0.636, 0.636, 0.454, 0.454, 0.818, 0.818, 0.818, 0.545, 1, 0.684, 0.686, 0.698, 0.771, 0.632, 0.575, 0.775, 0.751, 0.421, 0.455, 0.693, 0.557, 0.843, 0.748, 0.787, 0.603, 0.787, 0.695, 0.684, 0.616, 0.732, 0.684, 0.989, 0.685, 0.615, 0.685, 0.454, 0.454, 0.454, 0.818, 0.636, 0.636, 0.601, 0.623, 0.521, 0.623, 0.596, 0.352, 0.623, 0.633, 0.274, 0.344, 0.592, 0.274, 0.973, 0.633, 0.607, 0.623, 0.623, 0.427, 0.521, 0.394, 0.633, 0.592, 0.818, 0.592, 0.592, 0.525, 0.635, 0.454, 0.635, 0.818],
    bold: [0.342, 0.402, 0.587, 0.867, 0.711, 1.272, 0.862, 0.332, 0.543, 0.543, 0.711, 0.867, 0.361, 0.48, 0.361, 0.689, 0.711, 0.711, 0.711, 0.711, 0.711, 0.711, 0.711, 0.711, 0.711, 0.711, 0.402, 0.402, 0.867, 0.867, 0.867, 0.617, 0.964, 0.776, 0.762, 0.724, 0.83, 0.683, 0.65, 0.811, 0.837, 0.546, 0.555, 0.771, 0.637, 0.948, 0.847, 0.85, 0.733, 0.85, 0.782, 0.71, 0.682, 0.812, 0.764, 1.128, 0.764, 0.737, 0.692, 0.543, 0.689, 0.543, 0.867, 0.711, 0.711, 0.668, 0.699, 0.588, 0.699, 0.664, 0.422, 0.699, 0.712, 0.342, 0.403, 0.671, 0.342, 1.058, 0.712, 0.687, 0.699, 0.699, 0.497, 0.593, 0.456, 0.712, 0.65, 0.979, 0.669, 0.651, 0.597, 0.711, 0.543, 0.711, 0.867]
  },
  "Arial Black": {
    normal: [0.333, 0.333, 0.5, 0.66, 0.667, 1, 0.889, 0.278, 0.389, 0.389, 0.556, 0.66, 0.333, 0.333, 0.333, 0.278, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.333, 0.333, 0.66, 0.66, 0.66, 0.611, 0.74, 0.778, 0.778, 0.778, 0.778, 0.722, 0.667, 0.833, 0.833, 0.389, 0.667, 0.833, 0.667, 0.944, 0.833, 0.833, 0.722, 0.833, 0.778, 0.722, 0.722, 0.833, 0.778, 1, 0.778, 0.778, 0.722, 0.389, 0.278, 0.389, 0.66, 0.5, 0.333, 0.667, 0.667, 0.667, 0.667, 0.667, 0.389, 0.667, 0.667, 0.333, 0.333, 0.667, 0.333, 1, 0.667, 0.667, 0.667, 0.667, 0.444, 0.611, 0.444, 0.667, 0.611, 0.944, 0.667, 0.611, 0.556, 0.389, 0.278, 0.389, 0.66],
    bold: [0.333, 0.333, 0.5, 0.66, 0.667, 1, 0.889, 0.278, 0.389, 0.389, 0.556, 0.66, 0.333, 0.333, 0.333, 0.278, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.667, 0.333, 0.333, 0.66, 0.66, 0.66, 0.611, 0.74, 0.778, 0.778, 0.778, 0.778, 0.722, 0.667, 0.833, 0.833, 0.389, 0.667, 0.833, 0.667, 0.944, 0.833, 0.833, 0.722, 0.833, 0.778, 0.722, 0.722, 0.833, 0.778, 1, 0.778, 0.778, 0.722, 0.389, 0.278, 0.389, 0.66, 0.5, 0.333, 0.667, 0.667, 0.667, 0.667, 0.667, 0.389, 0.667, 0.667, 0.333, 0.333, 0.667, 0.333, 1, 0.667, 0.667, 0.667, 0.667, 0.444, 0.611, 0.444, 0.667, 0.611, 0.944, 0.667, 0.611, 0.556, 0.389, 0.278, 0.389, 0.66]
  },
  "Georgia": {
    normal: [0.241, 0.331, 0.412, 0.643, 0.61, 0.817, 0.71, 0.215, 0.375, 0.375, 0.472, 0.643, 0.27, 0.374, 0.27, 0.469, 0.614, 0.43, 0.559, 0.552, 0.565, 0.528, 0.566, 0.502, 0.596, 0.566, 0.313, 0.313, 0.643, 0.643, 0.643, 0.479, 0.929, 0.671, 0.654, 0.642, 0.749, 0.653, 0.599, 0.725, 0.815, 0.39, 0.518, 0.694, 0.604, 0.927, 0.767, 0.744, 0.61, 0.744, 0.702, 0.561, 0.619, 0.756, 0.667, 0.976, 0.71, 0.615, 0.602, 0.375, 0.469, 0.375, 0.643, 0.643, 0.5, 0.504, 0.56, 0.454, 0.574, 0.483, 0.325, 0.509, 0.582, 0.293, 0.292, 0.536, 0.286, 0.881, 0.591, 0.539, 0.571, 0.56, 0.41, 0.432, 0.345, 0.575, 0.497, 0.737, 0.505, 0.492, 0.444, 0.43, 0.375, 0.43, 0.643],
    bold: [0.254, 0.376, 0.51, 0.703, 0.641, 0.879, 0.799, 0.269, 0.447, 0.447, 0.482, 0.703, 0.328, 0.379, 0.328, 0.472, 0.701, 0.49, 0.626, 0.625, 0.649, 0.599, 0.648, 0.554, 0.676, 0.648, 0.367, 0.367, 0.703, 0.703, 0.703, 0.548, 0.967, 0.758, 0.757, 0.715, 0.834, 0.721, 0.671, 0.807, 0.913, 0.446, 0.595, 0.817, 0.686, 1.023, 0.839, 0.82, 0.701, 0.82, 0.797, 0.649, 0.684, 0.833, 0.762, 1.126, 0.809, 0.732, 0.689, 0.447, 0.472, 0.447, 0.703, 0.703, 0.5, 0.596, 0.646, 0.531, 0.663, 0.572, 0.393, 0.577, 0.68, 0.354, 0.346, 0.632, 0.344, 1.016, 0.69, 0.636, 0.658, 0.648, 0.52, 0.513, 0.397, 0.677, 0.567, 0.863, 0.588, 0.562, 0.525, 0.5, 0.388, 0.5, 0.703]
  },
  "Comic Sans MS": {
    normal: [0.299, 0.238, 0.424, 0.843, 0.693, 0.82, 0.654, 0.388, 0.366, 0.366, 0.53, 0.48, 0.277, 0.417, 0.249, 0.512, 0.61, 0.45, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.299, 0.299, 0.381, 0.51, 0.381, 0.524, 0.931, 0.731, 0.63, 0.603, 0.722, 0.625, 0.607, 0.68, 0.768, 0.546, 0.665, 0.611, 0.551, 0.883, 0.797, 0.798, 0.521, 0.876, 0.628, 0.693, 0.68, 0.737, 0.65, 1.04, 0.724, 0.635, 0.693, 0.376, 0.55, 0.376, 0.581, 0.627, 0.556, 0.512, 0.593, 0.514, 0.587, 0.548, 0.508, 0.531, 0.578, 0.28, 0.403, 0.54, 0.274, 0.777, 0.523, 0.526, 0.535, 0.52, 0.48, 0.487, 0.471, 0.52, 0.486, 0.684, 0.59, 0.521, 0.538, 0.366, 0.421, 0.366, 0.598],
    bold: [0.434, 0.238, 0.438, 0.843, 0.693, 0.82, 0.654, 0.434, 0.366, 0.366, 0.53, 0.61, 0.434, 0.61, 0.434, 0.512, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.61, 0.434, 0.434, 0.61, 0.61, 0.61, 0.566, 0.931, 0.731, 0.63, 0.619, 0.722, 0.625, 0.607, 0.68, 0.768, 0.546, 0.665, 0.611, 0.551, 0.883, 0.813, 0.798, 0.533, 0.876, 0.64, 0.693, 0.696, 0.737, 0.675, 1.04, 0.724, 0.635, 0.693, 0.376, 0.55, 0.376, 0.61, 0.627, 0.556, 0.556, 0.593, 0.514, 0.587, 0.559, 0.508, 0.531, 0.578, 0.28, 0.403, 0.54, 0.274, 0.777, 0.523, 0.526, 0.535, 0.52, 0.48, 0.487, 0.471, 0.52, 0.486, 0.684, 0.59, 0.553, 0.538, 0.366, 0.421, 0.366, 0.61]
  },
  "Chalkboard SE": {
    normal: [0.382, 0.255, 0.453, 0.798, 0.579, 0.811, 0.708, 0.193, 0.315, 0.315, 0.498, 0.539, 0.255, 0.476, 0.295, 0.545, 0.569, 0.569, 0.569, 0.569, 0.569, 0.569, 0.569, 0.569, 0.569, 0.569, 0.285, 0.362, 0.425, 0.487, 0.442, 0.45, 0.817, 0.645, 0.57, 0.587, 0.636, 0.547, 0.501, 0.664, 0.622, 0.443, 0.55, 0.585, 0.518, 0.828, 0.656, 0.726, 0.579, 0.76, 0.624, 0.588, 0.569, 0.657, 0.646, 0.988, 0.655, 0.587, 0.656, 0.31, 0.519, 0.298, 0.483, 0.635, 0.295, 0.536, 0.559, 0.493, 0.514, 0.526, 0.432, 0.527, 0.544, 0.25, 0.388, 0.528, 0.301, 0.737, 0.51, 0.487, 0.528, 0.516, 0.438, 0.431, 0.457, 0.528, 0.465, 0.72, 0.557, 0.536, 0.535, 0.344, 0.252, 0.351, 0.639],
    bold: [0.414, 0.299, 0.497, 0.842, 0.623, 0.855, 0.752, 0.238, 0.359, 0.359, 0.543, 0.583, 0.299, 0.52, 0.339, 0.589, 0.613, 0.613, 0.613, 0.613, 0.613, 0.613, 0.613, 0.613, 0.613, 0.613, 0.329, 0.407, 0.47, 0.531, 0.486, 0.494, 0.861, 0.667, 0.592, 0.609, 0.659, 0.569, 0.523, 0.686, 0.644, 0.465, 0.572, 0.607, 0.54, 0.85, 0.678, 0.748, 0.601, 0.782, 0.646, 0.61, 0.591, 0.68, 0.669, 1.01, 0.677, 0.609, 0.678, 0.355, 0.564, 0.343, 0.527, 0.68, 0.339, 0.558, 0.581, 0.515, 0.6, 0.548, 0.454, 0.549, 0.566, 0.272, 0.41, 0.55, 0.323, 0.759, 0.533, 0.509, 0.55, 0.538, 0.46, 0.453, 0.48, 0.55, 0.487, 0.743, 0.579, 0.558, 0.557, 0.388, 0.296, 0.396, 0.683]
  },
  "Marker Felt": {
    normal: [0.25, 0.2, 0.31, 0.568, 0.53, 0.7, 0.56, 0.16, 0.26, 0.26, 0.465, 0.44, 0.19, 0.37, 0.19, 0.52, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.23, 0.19, 0.38, 0.39, 0.38, 0.41, 0.906, 0.62, 0.49, 0.45, 0.49, 0.43, 0.42, 0.49, 0.5, 0.24, 0.43, 0.56, 0.42, 0.93, 0.6, 0.54, 0.45, 0.58, 0.57, 0.49, 0.48, 0.54, 0.54, 0.95, 0.61, 0.59, 0.6, 0.39, 0.522, 0.39, 0.3, 0.49, 0.21, 0.46, 0.43, 0.38, 0.43, 0.38, 0.36, 0.42, 0.47, 0.2, 0.21, 0.48, 0.2, 0.72, 0.49, 0.41, 0.43, 0.43, 0.38, 0.37, 0.36, 0.41, 0.44, 0.72, 0.41, 0.47, 0.48, 0.39, 0.204, 0.39, 0.33],
    bold: [0.25, 0.22, 0.45, 0.75, 0.434, 0.63, 0.63, 0.22, 0.33, 0.33, 0.59, 0.54, 0.3, 0.49, 0.19, 0.61, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.21, 0.21, 0.439, 0.51, 0.456, 0.48, 0.862, 0.66, 0.55, 0.54, 0.54, 0.48, 0.48, 0.58, 0.6, 0.24, 0.51, 0.57, 0.48, 0.94, 0.62, 0.63, 0.52, 0.61, 0.6, 0.51, 0.57, 0.57, 0.59, 0.88, 0.67, 0.65, 0.64, 0.46, 0.56, 0.46, 0.42, 0.76, 0.28, 0.51, 0.5, 0.45, 0.53, 0.45, 0.38, 0.46, 0.5, 0.24, 0.26, 0.51, 0.23, 0.79, 0.5, 0.48, 0.53, 0.5, 0.4, 0.41, 0.4, 0.5, 0.5, 0.7, 0.56, 0.56, 0.51, 0.38, 0.21, 0.38, 0.46]
  },
  "-apple-system": {
    normal: [0.25, 0.333, 0.408, 0.5, 0.5, 0.833, 0.778, 0.18, 0.333, 0.333, 0.5, 0.564, 0.25, 0.333, 0.25, 0.278, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.278, 0.278, 0.564, 0.564, 0.564, 0.444, 0.921, 0.722, 0.667, 0.667, 0.722, 0.611, 0.556, 0.722, 0.722, 0.333, 0.389, 0.722, 0.611, 0.889, 0.722, 0.722, 0.556, 0.722, 0.667, 0.556, 0.611, 0.722, 0.722, 0.944, 0.722, 0.722, 0.611, 0.333, 0.278, 0.333, 0.469, 0.5, 0.333, 0.444, 0.5, 0.444, 0.5, 0.444, 0.333, 0.5, 0.5, 0.278, 0.278, 0.5, 0.278, 0.778, 0.5, 0.5, 0.5, 0.5, 0.333, 0.389, 0.278, 0.5, 0.5, 0.722, 0.5, 0.5, 0.444, 0.48, 0.2, 0.48, 0.541],
    bold: [0.25, 0.333, 0.555, 0.5, 0.5, 1, 0.833, 0.278, 0.333, 0.333, 0.5, 0.57, 0.25, 0.333, 0.25, 0.278, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.333, 0.333, 0.57, 0.57, 0.57, 0.5, 0.93, 0.722, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.778, 0.389, 0.5, 0.778, 0.667, 0.944, 0.722, 0.778, 0.611, 0.778, 0.722, 0.556, 0.667, 0.722, 0.722, 1, 0.722, 0.722, 0.667, 0.333, 0.278, 0.333, 0.581, 0.5, 0.333, 0.5, 0.556, 0.444, 0.556, 0.444, 0.333, 0.5, 0.556, 0.278, 0.333, 0.556, 0.278, 0.833, 0.556, 0.5, 0.556, 0.556, 0.444, 0.389, 0.333, 0.556, 0.5, 0.722, 0.5, 0.5, 0.444, 0.394, 0.22, 0.394, 0.52]
  },
  "BlinkMacSystemFont": {
    normal: [0.205, 0.267, 0.403, 0.604, 0.604, 0.808, 0.669, 0.254, 0.322, 0.322, 0.4, 0.604, 0.215, 0.429, 0.215, 0.279, 0.606, 0.443, 0.566, 0.592, 0.604, 0.585, 0.617, 0.547, 0.6, 0.617, 0.215, 0.215, 0.604, 0.604, 0.604, 0.489, 0.875, 0.635, 0.604, 0.686, 0.679, 0.552, 0.528, 0.707, 0.699, 0.225, 0.495, 0.601, 0.524, 0.831, 0.699, 0.731, 0.578, 0.731, 0.599, 0.593, 0.581, 0.696, 0.631, 0.924, 0.635, 0.612, 0.617, 0.322, 0.279, 0.322, 0.604, 0.527, 0.5, 0.502, 0.554, 0.501, 0.554, 0.512, 0.304, 0.549, 0.539, 0.206, 0.205, 0.485, 0.204, 0.803, 0.527, 0.531, 0.549, 0.549, 0.308, 0.465, 0.301, 0.527, 0.483, 0.715, 0.468, 0.486, 0.468, 0.322, 0.216, 0.322, 0.604],
    bold: [0.198, 0.316, 0.456, 0.639, 0.647, 0.89, 0.702, 0.27, 0.382, 0.382, 0.449, 0.647, 0.241, 0.441, 0.241, 0.303, 0.656, 0.48, 0.603, 0.63, 0.647, 0.625, 0.652, 0.572, 0.649, 0.652, 0.241, 0.241, 0.647, 0.647, 0.647, 0.534, 0.888, 0.692, 0.64, 0.712, 0.7, 0.573, 0.548, 0.727, 0.729, 0.265, 0.558, 0.653, 0.543, 0.861, 0.714, 0.752, 0.618, 0.752, 0.64, 0.631, 0.602, 0.708, 0.678, 0.965, 0.686, 0.665, 0.631, 0.382, 0.303, 0.382, 0.647, 0.576, 0.5, 0.547, 0.597, 0.545, 0.596, 0.555, 0.356, 0.593, 0.584, 0.241, 0.24, 0.538, 0.242, 0.863, 0.576, 0.573, 0.593, 0.593, 0.364, 0.515, 0.355, 0.576, 0.528, 0.79, 0.528, 0.539, 0.505, 0.382, 0.242, 0.382, 0.647]
  },
  "system-ui": {
    normal: [0.205, 0.267, 0.403, 0.604, 0.604, 0.808, 0.669, 0.254, 0.322, 0.322, 0.4, 0.604, 0.215, 0.429, 0.215, 0.279, 0.606, 0.443, 0.566, 0.592, 0.604, 0.585, 0.617, 0.547, 0.6, 0.617, 0.215, 0.215, 0.604, 0.604, 0.604, 0.489, 0.875, 0.635, 0.604, 0.686, 0.679, 0.552, 0.528, 0.707, 0.699, 0.225, 0.495, 0.601, 0.524, 0.831, 0.699, 0.731, 0.578, 0.731, 0.599, 0.593, 0.581, 0.696, 0.631, 0.924, 0.635, 0.612, 0.617, 0.322, 0.279, 0.322, 0.604, 0.527, 0.5, 0.502, 0.554, 0.501, 0.554, 0.512, 0.304, 0.549, 0.539, 0.206, 0.205, 0.485, 0.204, 0.803, 0.527, 0.531, 0.549, 0.549, 0.308, 0.465, 0.301, 0.527, 0.483, 0.715, 0.468, 0.486, 0.468, 0.322, 0.216, 0.322, 0.604],
    bold: [0.198, 0.316, 0.456, 0.639, 0.647, 0.89, 0.702, 0.27, 0.382, 0.382, 0.449, 0.647, 0.241, 0.441, 0.241, 0.303, 0.656, 0.48, 0.603, 0.63, 0.647, 0.625, 0.652, 0.572, 0.649, 0.652, 0.241, 0.241, 0.647, 0.647, 0.647, 0.534, 0.888, 0.692, 0.64, 0.712, 0.7, 0.573, 0.548, 0.727, 0.729, 0.265, 0.558, 0.653, 0.543, 0.861, 0.714, 0.752, 0.618, 0.752, 0.64, 0.631, 0.602, 0.708, 0.678, 0.965, 0.686, 0.665, 0.631, 0.382, 0.303, 0.382, 0.647, 0.576, 0.5, 0.547, 0.597, 0.545, 0.596, 0.555, 0.356, 0.593, 0.584, 0.241, 0.24, 0.538, 0.242, 0.863, 0.576, 0.573, 0.593, 0.593, 0.364, 0.515, 0.355, 0.576, 0.528, 0.79, 0.528, 0.539, 0.505, 0.382, 0.242, 0.382, 0.647]
  },
  "sans-serif": {
    normal: [0.278, 0.278, 0.355, 0.556, 0.556, 0.889, 0.667, 0.191, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.278, 0.278, 0.584, 0.584, 0.584, 0.556, 1.015, 0.667, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.5, 0.667, 0.556, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.278, 0.278, 0.278, 0.469, 0.556, 0.333, 0.556, 0.556, 0.5, 0.556, 0.556, 0.278, 0.556, 0.556, 0.222, 0.222, 0.5, 0.222, 0.833, 0.556, 0.556, 0.556, 0.556, 0.333, 0.5, 0.278, 0.556, 0.5, 0.722, 0.5, 0.5, 0.5, 0.334, 0.26, 0.334, 0.584],
    bold: [0.278, 0.333, 0.474, 0.556, 0.556, 0.889, 0.722, 0.238, 0.333, 0.333, 0.389, 0.584, 0.278, 0.333, 0.278, 0.278, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.333, 0.333, 0.584, 0.584, 0.584, 0.611, 0.975, 0.722, 0.722, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.556, 0.722, 0.611, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.333, 0.278, 0.333, 0.584, 0.556, 0.333, 0.556, 0.611, 0.556, 0.611, 0.556, 0.333, 0.611, 0.611, 0.278, 0.278, 0.556, 0.278, 0.889, 0.611, 0.611, 0.611, 0.611, 0.389, 0.556, 0.333, 0.611, 0.556, 0.778, 0.556, 0.556, 0.5, 0.389, 0.28, 0.389, 0.584]
  },
  "serif": {
    normal: [0.25, 0.333, 0.408, 0.5, 0.5, 0.833, 0.778, 0.18, 0.333, 0.333, 0.5, 0.564, 0.25, 0.333, 0.25, 0.278, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.278, 0.278, 0.564, 0.564, 0.564, 0.444, 0.921, 0.722, 0.667, 0.667, 0.722, 0.611, 0.556, 0.722, 0.722, 0.333, 0.389, 0.722, 0.611, 0.889, 0.722, 0.722, 0.556, 0.722, 0.667, 0.556, 0.611, 0.722, 0.722, 0.944, 0.722, 0.722, 0.611, 0.333, 0.278, 0.333, 0.469, 0.5, 0.333, 0.444, 0.5, 0.444, 0.5, 0.444, 0.333, 0.5, 0.5, 0.278, 0.278, 0.5, 0.278, 0.778, 0.5, 0.5, 0.5, 0.5, 0.333, 0.389, 0.278, 0.5, 0.5, 0.722, 0.5, 0.5, 0.444, 0.48, 0.2, 0.48, 0.541],
    bold: [0.25, 0.333, 0.555, 0.5, 0.5, 1, 0.833, 0.278, 0.333, 0.333, 0.5, 0.57, 0.25, 0.333, 0.25, 0.278, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.333, 0.333, 0.57, 0.57, 0.57, 0.5, 0.93, 0.722, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.778, 0.389, 0.5, 0.778, 0.667, 0.944, 0.722, 0.778, 0.611, 0.778, 0.722, 0.556, 0.667, 0.722, 0.722, 1, 0.722, 0.722, 0.667, 0.333, 0.278, 0.333, 0.581, 0.5, 0.333, 0.5, 0.556, 0.444, 0.556, 0.444, 0.333, 0.5, 0.556, 0.278, 0.333, 0.556, 0.278, 0.833, 0.556, 0.5, 0.556, 0.556, 0.444, 0.389, 0.333, 0.556, 0.5, 0.722, 0.5, 0.5, 0.444, 0.394, 0.22, 0.394, 0.52]
  },
  "cursive": {
    normal: [0.278, 0.256, 0.38, 0.556, 0.388, 0.734, 0.885, 0.207, 0.34, 0.34, 0.446, 0.556, 0.207, 0.362, 0.207, 0.434, 0.516, 0.431, 0.559, 0.461, 0.618, 0.483, 0.567, 0.509, 0.517, 0.567, 0.207, 0.207, 0.556, 0.556, 0.556, 0.364, 0.692, 0.717, 0.721, 0.623, 0.843, 0.731, 0.701, 0.662, 0.847, 0.457, 0.455, 0.801, 0.64, 0.991, 0.882, 0.686, 0.669, 0.692, 0.787, 0.525, 0.762, 0.854, 0.69, 0.898, 0.701, 0.645, 0.589, 0.316, 0.349, 0.316, 0.556, 0.556, 0.446, 0.513, 0.485, 0.383, 0.513, 0.372, 0.312, 0.466, 0.532, 0.296, 0.266, 0.462, 0.267, 0.789, 0.564, 0.429, 0.498, 0.485, 0.417, 0.355, 0.354, 0.561, 0.451, 0.627, 0.501, 0.542, 0.412, 0.348, 0.204, 0.348, 0.556],
    bold: [0.278, 0.256, 0.38, 0.556, 0.388, 0.734, 0.885, 0.207, 0.34, 0.34, 0.446, 0.556, 0.207, 0.362, 0.207, 0.434, 0.516, 0.431, 0.559, 0.461, 0.618, 0.483, 0.567, 0.509, 0.517, 0.567, 0.207, 0.207, 0.556, 0.556, 0.556, 0.364, 0.692, 0.717, 0.721, 0.623, 0.843, 0.731, 0.701, 0.662, 0.847, 0.457, 0.455, 0.801, 0.64, 0.991, 0.882, 0.686, 0.669, 0.692, 0.787, 0.525, 0.762, 0.854, 0.69, 0.898, 0.701, 0.645, 0.589, 0.316, 0.349, 0.316, 0.556, 0.556, 0.446, 0.513, 0.485, 0.383, 0.513, 0.372, 0.312, 0.466, 0.532, 0.296, 0.266, 0.462, 0.267, 0.789, 0.564, 0.429, 0.498, 0.485, 0.417, 0.355, 0.354, 0.561, 0.451, 0.627, 0.501, 0.542, 0.412, 0.348, 0.204, 0.348, 0.556]
  }
};

// src/core/text-width.ts
var isCJK = (code) => code >= 12288 && code <= 12351 || code >= 13312 && code <= 40959 || code >= 63744 && code <= 64255 || code >= 65280 && code <= 65519;
var families = /* @__PURE__ */ new Map();
function knownFamily(font) {
  const stack = font ?? "sans-serif";
  if (families.has(stack)) return families.get(stack);
  let known;
  for (const entry of stack.split(",")) {
    const family = entry.trim().replace(/^['"]|['"]$/g, "");
    if (TEXT_WIDTHS[family]) {
      known = family;
      break;
    }
  }
  families.set(stack, known);
  return known;
}
var measured = /* @__PURE__ */ new Map();
var MEASURED_LIMIT = 2e4;
function textWidth(text, fontSize, font, weight = "normal") {
  return emMeasurer(font, weight)(text) * fontSize;
}
function emMeasurer(font, weight) {
  const family = knownFamily(font);
  const bold = weight === "bold" || Number(weight) >= 600;
  const key = `${family ?? ""}|${bold}`;
  let byText = measured.get(key);
  if (!byText) measured.set(key, byText = /* @__PURE__ */ new Map());
  const cache = byText;
  const widths = family ? TEXT_WIDTHS[family][bold ? "bold" : "normal"] : void 0;
  return (text) => {
    let em = cache.get(text);
    if (em === void 0) {
      em = 0;
      for (const char of text) {
        const code = char.charCodeAt(0);
        em += isCJK(code) ? 1 : widths && code >= 32 && code < 127 ? widths[code - 32] : 0.62;
      }
      if (cache.size >= MEASURED_LIMIT) cache.clear();
      cache.set(text, em);
    }
    return em;
  };
}
var tableTextMeasurer = (fontSize, font, weight) => {
  const em = emMeasurer(font, weight);
  return (text) => em(text) * fontSize;
};

// src/core/calendar-labels.ts
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function calendarLabels(values) {
  if (values.length < 2) return void 0;
  const times = [];
  for (const value of values) {
    const time = snapshotTime(value);
    if (time === void 0) return void 0;
    times.push(time);
  }
  const dates = times.map((time) => new Date(time));
  const midnight = dates.every((d) => d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0 && d.getUTCMilliseconds() === 0);
  if (!midnight) return void 0;
  const monthly = dates.every((d) => d.getUTCDate() === 1);
  if (monthly && dates.every((d) => d.getUTCMonth() === 0)) return void 0;
  const chronological = times.every((time, i) => i === 0 || time > times[i - 1]);
  return dates.map((d, i) => {
    const year = d.getUTCFullYear();
    const opensYear = !chronological || i === 0 || dates[i - 1].getUTCFullYear() !== year;
    const month = MONTHS[d.getUTCMonth()];
    const name = monthly ? month : `${month} ${d.getUTCDate()}`;
    return opensYear ? [name, String(year)] : [name];
  });
}
function widestLines(names, widthOf) {
  return Math.max(0, ...names.flatMap((lines) => lines.map(widthOf)));
}
function crowdedCalendarLabels(values, band, fontSize) {
  if (!(band > 0) || !(fontSize > 0)) return void 0;
  const calendar = calendarLabels(values);
  if (!calendar) return void 0;
  const widthOf = (text) => textWidth(text, fontSize);
  const gap = Math.max(6, fontSize / 2);
  if (Math.max(0, ...values.map(widthOf)) + gap <= band) return void 0;
  return widestLines(calendar, widthOf) + gap <= band ? calendar : void 0;
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
var COMPANION_SUFFIX = / \((?:outliers|points)\)$/;
var isCompanionSeries = (s) => s?._companion === true;
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
  const topY = Math.max(4, gt - 24);
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
    const pad2 = dataRange / (bandedCount - 1) / 2;
    axisObj.min = minVal - pad2;
    axisObj.max = maxVal + pad2;
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
            id: EC_LEGEND_TITLE_ID,
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
        const CANVAS_BUFFER3 = 16;
        const { left: _ignoredLeft, ...legendRest } = option.legend;
        option.legend = {
          ...legendRest,
          top: legendTitle != null ? 20 : 0,
          right: CANVAS_BUFFER3,
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
            id: EC_LEGEND_TITLE_ID,
            type: "text",
            right: CANVAS_BUFFER3,
            top: 4,
            z: 100,
            style: {
              text: legendTitle,
              fontSize: layout.legendFontSize,
              fontWeight: "bold",
              fill: "#333",
              textAlign: "left",
              width: option._legendWidth
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
  const CANVAS_BUFFER2 = 16;
  const LEGEND_GAP = 12;
  const VISUALMAP_GAP = 18;
  const VISUALMAP_RIGHT_OFFSET = 10;
  const legendWidth = hasLegend ? option._legendWidth || 120 : 20;
  const visualMapWidth = option._visualMapWidth || 0;
  if (visualMapWidth) delete option._visualMapWidth;
  const rightMargin = isDualLegend ? hasVisualMap ? VISUALMAP_RIGHT_OFFSET + visualMapWidth + VISUALMAP_GAP : 10 : (hasLegend ? legendWidth : hasVisualMap ? visualMapWidth + VISUALMAP_GAP : 10) + LEGEND_GAP;
  const bottomLegendExtra = isDualLegend ? 30 : 0;
  const gridMargin = {
    left: (hasYTitle ? 70 : 50) + CANVAS_BUFFER2,
    right: rightMargin + CANVAS_BUFFER2,
    top: 20 + CANVAS_BUFFER2,
    bottom: (hasXTitle ? 45 : 30) + CANVAS_BUFFER2 + bottomLegendExtra
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
      const formatter = (value) => {
        if (typeof value === "string" && value.length > maxLen) {
          return value.substring(0, maxLen) + "\u2026";
        }
        return value;
      };
      formatter._flintCalendar = true;
      option.xAxis.axisLabel.formatter = formatter;
    }
  }
  {
    const xAxis = option.xAxis;
    const xcs = channelSemantics.x;
    if (xAxis && !Array.isArray(xAxis) && xAxis.type === "category" && Array.isArray(xAxis.data) && xcs?.field && (xcs.type === "nominal" || xcs.type === "ordinal")) {
      const values = xAxis.data.map((d) => String(d?.value ?? d));
      const semanticType = toTypeString(context.semanticTypes[xcs.field]);
      const named = isDateField(semanticType, values) ? crowdedCalendarLabels(values, layout.xStep, xAxis.axisLabel?.fontSize ?? layout.xLabel?.fontSize ?? 11) : void 0;
      if (named) {
        const byValue = new Map(values.map((value, index) => [value, named[index].join("\n")]));
        const existing = xAxis.axisLabel?.formatter;
        const formatter = (value) => byValue.get(String(value)) ?? (typeof existing === "function" ? existing(value) : value);
        formatter._flintCalendar = true;
        xAxis.axisLabel = { ...xAxis.axisLabel, formatter, rotate: 0 };
      }
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
        const fmt2 = cs.temporalFormat;
        axisObj.axisLabel.formatter = (val) => formatTimestamp(val, fmt2);
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
        const baseName = rawName.replace(COMPANION_SUFFIX, "");
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
      const cRight = pal.length > 3 ? pal[3] : pal[Math.max(0, pal.length - 1)];
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
        const colorableCount = option.series.filter(
          (s) => s && s.itemStyle?.color == null && !isCompanionSeries(s)
        ).length;
        const spacedIndices = useEvenSpacing && colorableCount > 0 ? pickEvenlySpacedColorIndices(n, colorableCount) : null;
        let colorIdx = 0;
        option.series.forEach((s, idx) => {
          if (!s) return;
          s.itemStyle = s.itemStyle || {};
          if (s.itemStyle.color != null) return;
          if (isCompanionSeries(s)) {
            const owner = option.series[idx - 1];
            if (owner?.itemStyle?.color != null) {
              s.itemStyle.color = owner.itemStyle.color;
              return;
            }
          }
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
function formatTimestamp(val, d3Format) {
  const d = new Date(val);
  const pad2 = (n) => n < 10 ? "0" + n : String(n);
  return d3Format.replace(/%Y/g, String(d.getFullYear())).replace(/%y/g, String(d.getFullYear()).slice(-2)).replace(/%B/g, MONTH_FULL2[d.getMonth()]).replace(/%b/g, MONTH_ABBR[d.getMonth()]).replace(/%m/g, pad2(d.getMonth() + 1)).replace(/%d/g, pad2(d.getDate())).replace(/%H/g, pad2(d.getHours())).replace(/%M/g, pad2(d.getMinutes())).replace(/%S/g, pad2(d.getSeconds()));
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
    const seriesSuffix = enc.seriesSuffix;
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
        const lane = item?.data && typeof item.data === "object" && !Array.isArray(item.data) ? item.data.laneGroup : void 0;
        if (lane === void 0 && String(item?.seriesName ?? "").startsWith("__")) continue;
        let name = lane ?? item.seriesName ?? valueLabel;
        let val = item.value != null ? item.value : Array.isArray(item.data) ? item.data[item.dataIndex] : item.data;
        if (seriesSuffix && Array.isArray(val)) {
          const suffix = seriesSuffix.categoryNames[Number(val[seriesSuffix.index])];
          if (suffix) name = `${name} \xB7 ${suffix}`;
        }
        if (Array.isArray(val) && val.length >= 2) val = val[1];
        if (val && typeof val === "object" && "value" in val) val = val.value;
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
        val = params.data?.laneGroup ?? params.seriesName ?? params.name;
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
        const [date2, value, name] = params.data;
        const color = params.color || "#333";
        const dateStr = date2 instanceof Date ? date2.toLocaleDateString() : String(date2);
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
var SIZE_KEY_RIGHT = 28;
function sizeKeyFill(ctx, hasColorEncoding) {
  if (hasColorEncoding) return "#cccccc";
  const fallback = (getPaletteForScheme("cat10") ?? DEFAULT_COLORS)[0];
  return ctx.resolvedEncodings?.color?.colorPalette?.[0] ?? fallback;
}
function sizeKeyGraphic(entries, title, fill) {
  const gap = 8;
  const rowGap = 6;
  const fontSize = 10;
  const titleHeight = 20;
  const maxR = Math.max(...entries.map((e) => e.diameter / 2));
  const labelWidth = Math.max(...entries.map((e) => e.label.length * fontSize * 0.6), 20);
  const titleWidth = title.length * 11 * 0.62;
  const width = Math.ceil(Math.max(2 * maxR + gap + labelWidth, titleWidth));
  const children = [{ type: "text", left: 0, top: 0, style: { text: title, fontSize: 11, fontWeight: "bold", fill: "#333", textAlign: "left" } }];
  let rowTop = titleHeight;
  for (const entry of entries) {
    const r = entry.diameter / 2;
    const rowH = Math.max(entry.diameter, 16) + rowGap;
    children.push({ type: "circle", left: maxR - r, top: rowTop + (rowH - entry.diameter) / 2, shape: { cx: r, cy: r, r }, style: { fill } });
    children.push({ type: "text", left: 2 * maxR + gap, top: rowTop + (rowH - fontSize) / 2, style: { text: entry.label, fontSize, fill: "#333", textAlign: "left" } });
    rowTop += rowH;
  }
  return { graphic: { id: EC_SIZE_KEY_ID, type: "group", right: SIZE_KEY_RIGHT, top: titleHeight, width, z: 100, children }, width };
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
    const xIsTemporal = xType === "temporal";
    const yIsTemporal = yType === "temporal";
    const toTime = (v) => typeof v === "number" ? v : new Date(String(v)).getTime();
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
      } : { type: xIsTemporal ? "time" : "value", name: xField, nameLocation: "middle", nameGap: 30 },
      yAxis: yIsCategorical ? {
        type: "category",
        data: yCategories,
        name: yField,
        nameLocation: "middle",
        nameGap: 40,
        axisLabel: { interval: 0, rotate: 0 },
        axisTick: { show: true, alignWithLabel: true },
        axisLine: { show: true }
      } : { type: yIsTemporal ? "time" : "value", name: yField, nameLocation: "middle", nameGap: 40 },
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
      const hasColorEncoding = !!channelSemantics.color?.field;
      const key = sizeKeyGraphic(
        sizeOrderForLegend.map((name) => ({ label: name, diameter: scaleSize(name) })),
        sizeField,
        sizeKeyFill(ctx, hasColorEncoding)
      );
      option._visualMapWidth = key.width + SIZE_KEY_RIGHT - 10;
      option.graphic = [...Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [], key.graphic];
    } else if (useContinuousSizeVisualMap) {
      const SIZE_SPREAD_MIN = 20;
      const sizeMaxForMap = Math.max(rangeMaxClamped, rangeMin + SIZE_SPREAD_MIN);
      const fmtSize = (v) => v.toLocaleString("en-US", {
        maximumFractionDigits: v === 0 ? 1 : Math.min(20, Math.max(1, 1 - Math.floor(Math.log10(Math.abs(v)))))
      });
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
        name: sizeField,
        id: rampId("size")
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
          id: rampTitleId("size"),
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
    const xVal = (row) => xIsCategorical ? xCategoryToIndex.get(String(row[xField] ?? "")) ?? 0 : xIsTemporal ? toTime(row[xField]) : row[xField];
    const yVal = (row) => yIsCategorical ? yCategoryToIndex.get(String(row[yField] ?? "")) ?? 0 : yIsTemporal ? toTime(row[yField]) : row[yField];
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
      const scheme = schemeName(ctx.encodings?.color?.scheme) ?? "";
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
        name: colorField,
        id: rampId("color")
      };
      if (option.visualMap) {
        option.visualMap.push(colorVisualMap);
      } else {
        option.visualMap = colorVisualMap;
      }
      option._visualMapWidth = hasSizeVisualMap ? VM_BAR_WIDTH + VM_GAP + VM_BAR_WIDTH : VM_BAR_WIDTH;
      const vmGraphics = [
        {
          id: rampTitleId("color"),
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
  const measure2 = candidates.find((c) => isMeasureEnc(encodings[c]));
  if (!category || !measure2 || category === measure2) return null;
  return { category, measure: measure2 };
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
      const { category, measure: measure2 } = resolved;
      const enc = encodings[category];
      if (enc.sortBy === measure2) {
        return enc.sortOrder === "descending" ? "value-desc" : "value-asc";
      }
      return void 0;
    },
    set: (encodings, value) => {
      const resolved = resolveSortChannels(encodings, candidates);
      if (!resolved) return encodings;
      const { category, measure: measure2 } = resolved;
      const base = encodings[category];
      let next;
      switch (value) {
        case "value-asc":
          next = { ...base, sortBy: measure2, sortOrder: "ascending" };
          break;
        case "value-desc":
          next = { ...base, sortBy: measure2, sortOrder: "descending" };
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
      return v === void 0 ? "-" : { value: v, laneGroup: g, itemStyle: { color: groupColor(g) } };
    })
  }));
  const legendData = globalGroups.map((g) => ({ name: g, itemStyle: { color: groupColor(g) } }));
  return { series, legendData };
}
function buildCategoryValues(rows, categoryField, valueField, categories) {
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const cat = String(row[categoryField] ?? "");
    const val = row[valueField];
    if (val != null && !isNaN(val)) {
      map2.set(cat, (map2.get(cat) ?? 0) + Number(val));
    }
  }
  return categories.map((cat) => map2.get(cat) ?? null);
}
function buildCategoryCounts(rows, categoryField, categories) {
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const cat = String(row[categoryField] ?? "");
    map2.set(cat, (map2.get(cat) ?? 0) + 1);
  }
  return categories.map((cat) => map2.get(cat) ?? 0);
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
var LINE_DASHES = ["solid", [8, 4], [2, 3], [8, 3, 2, 3]];
var DASH_KEY_COLOR = "#777777";
var dashKeyName = (label) => `${label}\u200B`;
function dashPattern(index) {
  const dash = LINE_DASHES[index % LINE_DASHES.length];
  return typeof dash === "string" ? dash : [...dash];
}
var dashLabelOf = (value) => value == null ? "null" : String(value);
function resolveDashLabels(ctx, dashField) {
  const source = ctx.fullTable ?? ctx.table;
  const order = getCategoryOrder({ ...ctx, table: source }, "strokeDash");
  const labels = extractCategories(source, dashField, order);
  if (source.some((row) => row[dashField] == null) && !labels.includes("null")) labels.push("null");
  return labels;
}
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
  channels: ["x", "y", "color", "strokeDash", "opacity", "column", "row"],
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
    const dashField = channelSemantics.strokeDash?.field;
    if (!xCS?.field || !yCS?.field) return;
    const xField = xCS.field;
    const yField = yCS.field;
    const xIsDiscrete = isDiscrete5(xCS.type);
    const xIsTemporal = xCS.type === "temporal";
    const yIsDiscrete = isDiscrete5(yCS.type);
    const isContinuousColor = !!colorField && (colorType === "quantitative" || colorType === "temporal");
    const discreteColorField = colorField && isDiscrete5(colorType) ? colorField : void 0;
    const dashLabels = dashField ? resolveDashLabels(ctx, dashField) : [];
    const dashIndexOf = (row) => dashField ? Math.max(0, dashLabels.indexOf(dashLabelOf(row[dashField]))) : 0;
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
      const pointData = sorted.map((r) => dashField ? [r[xField], r[yField], r[colorField], dashIndexOf(r)] : [r[xField], r[yField], r[colorField]]);
      const lineGroups = [];
      if (dashField) {
        const byDash = /* @__PURE__ */ new Map();
        for (const r of sorted) {
          const dashIndex = dashIndexOf(r);
          const rows = byDash.get(dashIndex);
          if (rows) rows.push(r);
          else byDash.set(dashIndex, [r]);
        }
        lineGroups.push(...[...byDash].sort((a, b) => a[0] - b[0]));
      } else {
        lineGroups.push([0, sorted]);
      }
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
        seriesIndex: lineGroups.length,
        // apply to point series, which follows the lines
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
      for (const [dashIndex, rows] of lineGroups) {
        option.series.push({
          type: "line",
          ...dashField ? { name: dashLabels[dashIndex] } : {},
          data: rows.map((r) => [r[xField], r[yField]]),
          itemStyle: { color: "#cccccc" },
          lineStyle: { color: "#cccccc", ...dashField ? { type: dashPattern(dashIndex) } : {} },
          showSymbol: false,
          symbol: "none",
          ...smooth ? { smooth: true } : {},
          ...step ? { step } : {}
        });
      }
      option.series.push({
        type: "scatter",
        data: pointData,
        symbol: "circle",
        symbolSize: 7,
        itemStyle: { opacity: 1 }
      });
      if (dashField) {
        option.legend = { data: dashLabels.map((name) => ({ name, itemStyle: { opacity: 0 } })) };
        option._legendTitle = dashField;
        option._encodingTooltip.parts.push({
          from: "data",
          index: 3,
          label: dashField,
          format: "category",
          categoryNames: dashLabels
        });
      }
    } else if (discreteColorField || dashField) {
      const factored = !!discreteColorField && !!dashField && discreteColorField !== dashField;
      const nameOf = (row) => discreteColorField ? String(row[discreteColorField] ?? "") : dashLabelOf(row[dashField]);
      const groups = /* @__PURE__ */ new Map();
      for (const row of table) {
        const name = nameOf(row);
        const dashIndex = dashIndexOf(row);
        let byDash = groups.get(name);
        if (!byDash) {
          byDash = /* @__PURE__ */ new Map();
          groups.set(name, byDash);
        }
        const rows = byDash.get(dashIndex);
        if (rows) rows.push(row);
        else byDash.set(dashIndex, [row]);
      }
      const names = [...groups.keys()];
      if (!discreteColorField) names.sort((a, b) => dashLabels.indexOf(a) - dashLabels.indexOf(b));
      const buildSeriesData = (rows, dashIndex) => {
        if (yIsDiscrete && yCategories) {
          const points = buildCategoryAlignedXYData(rows, xField, yField, yCategories);
          return factored ? points.map((p) => [...p, dashIndex]) : points;
        }
        if (xIsDiscrete) {
          const values = buildCategoryAlignedData(rows, xField, yField, categories);
          return factored ? values.map((v, i) => [categories[i], v, dashIndex]) : values;
        }
        return rows.map((r) => factored ? [r[xField], r[yField], dashIndex] : [r[xField], r[yField]]);
      };
      for (const name of names) {
        const byDash = [...groups.get(name)].sort((a, b) => a[0] - b[0]);
        byDash.forEach(([dashIndex, rows], k) => {
          const series = {
            name,
            type: "line",
            data: buildSeriesData(rows, dashIndex),
            // Default line chart: don't draw point markers (unless showPoints is set).
            showSymbol: !!showPoints,
            symbol: showPoints ? "circle" : "none",
            ...showPoints ? { symbolSize: 6 } : {}
          };
          if (dashField) series.lineStyle = { type: dashPattern(dashIndex) };
          if (k > 0 || !discreteColorField && option.series.length > 0) series._companion = true;
          if (smooth) series.smooth = true;
          if (step) series.step = step;
          option.series.push(series);
        });
      }
      if (factored) {
        option.legend = {
          data: [
            ...names.map((name) => ({ name, lineStyle: { type: "solid" } })),
            ...dashLabels.map((label, i) => ({
              name: dashKeyName(label),
              lineStyle: { type: dashPattern(i), color: DASH_KEY_COLOR },
              itemStyle: { color: DASH_KEY_COLOR, opacity: 0 }
            }))
          ]
        };
        dashLabels.forEach((label, i) => {
          option.series.push({
            name: dashKeyName(label),
            type: "line",
            data: [],
            silent: true,
            showSymbol: false,
            symbol: "none",
            lineStyle: { type: dashPattern(i), color: DASH_KEY_COLOR },
            itemStyle: { color: DASH_KEY_COLOR }
          });
        });
        option._encodingTooltip = {
          ...option._encodingTooltip,
          seriesSuffix: { index: 2, categoryNames: dashLabels }
        };
      } else if (dashField && !discreteColorField) {
        option.legend = { data: dashLabels.map((name) => ({ name, itemStyle: { opacity: 0 } })) };
        option._legendTitle = dashField;
      } else {
        option.legend = { data: names };
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
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) map2.set(String(row[xField]), Number(v));
  }
  return categories.map((cat) => {
    const v = map2.get(cat);
    return v != null ? yTransform ? yTransform(v) : v : null;
  });
}
function buildCategoryAlignedXYData(rows, xField, yField, yCategories) {
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const key = String(row[yField] ?? "");
    if (!map2.has(key)) {
      map2.set(key, row[xField]);
    }
  }
  return yCategories.filter((cat) => map2.has(cat)).map((cat) => [map2.get(cat), cat]);
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
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) map2.set(String(row[xField]), Number(v));
  }
  return categories.map((cat) => {
    const v = map2.get(cat);
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
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const v = row[yField];
    if (v != null && !isNaN(Number(v))) {
      const k = String(row[xField]);
      map2.set(k, (map2.get(k) ?? 0) + Number(v));
    }
  }
  return categories.map((cat) => map2.get(cat) ?? null);
}
function buildCategoryAlignedXYData2(rows, xField, yField, yCategories) {
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const key = String(row[yField] ?? "");
    if (!map2.has(key)) {
      map2.set(key, row[xField]);
    }
  }
  return yCategories.filter((cat) => map2.has(cat)).map((cat) => [map2.get(cat), cat]);
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
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const x = Number(row[xField]);
    const y = Number(row[yField]);
    if (!Number.isFinite(x)) continue;
    map2.set(x, Number.isFinite(y) ? y : 0);
  }
  return sortedX.map((x) => map2.get(x) ?? 0);
}
function buildTimeAlignedData(rows, xField, yField, sortedDates) {
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const n = Number(row[yField]);
    map2.set(String(row[xField]), Number.isFinite(n) ? n : 0);
  }
  return sortedDates.map((d) => [d, map2.get(d) ?? 0]);
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
  const map2 = /* @__PURE__ */ new Map();
  for (const row of rows) {
    const lo = Number(row[lowField]);
    const hi = Number(row[highField]);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) continue;
    map2.set(String(row[xField]), { low: Math.min(lo, hi), high: Math.max(lo, hi) });
  }
  return labels.map((l) => {
    const v = map2.get(l);
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
    // (defaultBandSize=20, minStep=8), matching VL heatmap sizing.
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
    const encScheme = schemeName(encodings?.color?.scheme);
    const userScheme = encScheme && encScheme !== "default" ? encScheme : void 0;
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    const semanticIsDiverging = decision?.schemeType === "diverging";
    const schemeName2 = userScheme || (semanticIsDiverging ? "redblue" : DEFAULT_HEATMAP_SCHEME);
    const isDivergingScale = semanticIsDiverging || isDivergingHeatmapScheme(schemeName2);
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
        schemeColors = SCHEME_COLORS[schemeName2] || SCHEME_COLORS[DEFAULT_HEATMAP_SCHEME];
      }
    } else {
      schemeColors = SCHEME_COLORS[schemeName2] || SCHEME_COLORS[DEFAULT_HEATMAP_SCHEME];
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
      get: (enc) => schemeName(enc.color?.scheme),
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
function boxplotLaneGeometry(bandWidth, laneCount, laneIndex) {
  const availableWidth = bandWidth * 0.8 - 2;
  const boxGap = availableWidth / laneCount * 0.3;
  const boxWidth = (availableWidth - boxGap * (laneCount - 1)) / laneCount;
  return {
    offset: boxWidth / 2 - availableWidth / 2 + laneIndex * (boxGap + boxWidth),
    boxWidth
  };
}
var POINT_JITTER_FRACTION = 0.35;
function jitterAt(index) {
  return index * 0.6180339887498949 % 1 * 2 - 1;
}
function laneBoxTooltip(p) {
  const [min, q1, median, q3, max] = Array.isArray(p?.data?.value) ? p.data.value : [];
  return [
    `${p?.name ?? ""}`,
    `${p?.marker ?? ""}${p?.data?.laneGroup ?? ""}`,
    `max: ${max}`,
    `Q3: ${q3}`,
    `median: ${median}`,
    `Q1: ${q1}`,
    `min: ${min}`
  ].join("<br/>");
}
function lanePointTooltip(p) {
  return `${p?.marker ?? ""}${p?.data?.laneGroup ?? ""}: ${Array.isArray(p?.data?.value) ? p.data.value[1] : ""}`;
}
function makePointSeries(name, data, laneIndex, laneCount, horizontal, fullSample = false) {
  return {
    name,
    type: "custom",
    coordinateSystem: "cartesian2d",
    data,
    encode: { tooltip: [0, 1] },
    z: 3,
    // Marks this as an overlay belonging to the boxplot series before it, so
    // palette assignment gives it that box's colour instead of a new slot.
    _companion: true,
    renderItem: (_params, api) => {
      const category = Number(api.value(0));
      const value = Number(api.value(1));
      const jitter2 = Number(api.value(2)) || 0;
      const point = horizontal ? api.coord([value, category]) : api.coord([category, value]);
      const size = api.size(horizontal ? [0, 1] : [1, 0]);
      const bandWidth = Math.abs(horizontal ? size[1] : size[0]);
      const lane = boxplotLaneGeometry(bandWidth, laneCount, laneIndex);
      const offset = lane.offset + jitter2 * lane.boxWidth * POINT_JITTER_FRACTION;
      const color = api.visual("color");
      return {
        type: "circle",
        shape: {
          cx: point[0] + (horizontal ? 0 : offset),
          cy: point[1] + (horizontal ? offset : 0),
          r: fullSample ? Math.max(1.6, Math.min(3.2, lane.boxWidth * 0.08)) : 2
        },
        // With the whole sample drawn the box goes hollow and the points
        // become the mark that spends saturated ink, so they carry the
        // group colour. Slight transparency lets dense regions read as
        // density; the hairline halo keeps overlaps countable.
        style: fullSample ? { fill: color, opacity: 0.7, stroke: "#ffffff", lineWidth: 0.5 } : { fill: color }
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
    const showPoints = ctx.chartProperties?.showPoints === true;
    const showOutliers = !showPoints && whiskerMethod === "iqr" && ctx.chartProperties?.showOutliers !== false;
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
        const pointData = [];
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
          boxData.push({
            value: fiveNumberSummary(values, whiskerMethod),
            // The group this lane draws at this band, so the tooltip names it, not the lane.
            laneGroup: g,
            // Hollow box when the sample is drawn (see makePointSeries).
            itemStyle: { color: showPoints ? "transparent" : c, borderColor: c }
          });
          if (showPoints) {
            values.forEach((v, k) => pointData.push({ value: [i, v, jitterAt(k)], laneGroup: g }));
          } else if (showOutliers) {
            for (const o of findOutliers(values)) pointData.push({ value: [i, o], laneGroup: g, itemStyle: { color: c } });
          }
        }
        option.series.push({
          name: `__lane${lane}`,
          type: "boxplot",
          data: boxData,
          ...showPoints ? { itemStyle: { borderWidth: 1.5 } } : {},
          tooltip: { formatter: laneBoxTooltip }
        });
        if (pointData.length > 0) {
          option.series.push({
            ...makePointSeries(`__lane${lane} (points)`, pointData, lane, maxPerBand, isHorizontal, showPoints),
            tooltip: { formatter: lanePointTooltip }
          });
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
        const pointData = [];
        for (let i = 0; i < categories.length; i++) {
          const cat = categories[i];
          const rows = (catGroups.get(cat) || []).filter(
            (r) => String(r[colorField] ?? "") === colorName
          );
          const values = rows.map((r) => Number(r[valField])).filter((v) => isFinite(v));
          boxData.push(
            !values.length ? "-" : showPoints ? { value: fiveNumberSummary(values, whiskerMethod), itemStyle: { color: "transparent" } } : fiveNumberSummary(values, whiskerMethod)
          );
          if (showPoints) {
            values.forEach((v, k) => pointData.push([i, v, jitterAt(k)]));
          } else if (showOutliers) {
            for (const o of findOutliers(values)) {
              pointData.push([i, o]);
            }
          }
        }
        option.series.push({
          name: colorName,
          type: "boxplot",
          data: boxData,
          ...showPoints ? { itemStyle: { borderWidth: 1.5 } } : {}
          // itemStyle 由 ecApplyLayoutToSpec 按 colorDecisions 填充
        });
        if (pointData.length > 0) {
          option.series.push(makePointSeries(
            colorName + " (points)",
            pointData,
            cIdx,
            colorCategories.length,
            isHorizontal,
            showPoints
          ));
        }
      }
      option.legend = { data: colorCategories };
      option._legendTitle = colorField;
    } else {
      const catGroups = groupBy(table, catField);
      const boxData = [];
      const pointData = [];
      for (let i = 0; i < categories.length; i++) {
        const cat = categories[i];
        const rows = catGroups.get(cat) || [];
        const values = rows.map((r) => Number(r[valField])).filter((v) => isFinite(v));
        const summary = fiveNumberSummary(values, whiskerMethod);
        boxData.push(showPoints ? { value: summary, itemStyle: { color: "transparent" } } : summary);
        if (showPoints) {
          values.forEach((v, k) => pointData.push([i, v, jitterAt(k)]));
        } else if (showOutliers) {
          for (const o of findOutliers(values)) {
            pointData.push([i, o]);
          }
        }
      }
      option.series.push({
        type: "boxplot",
        data: boxData,
        ...showPoints ? { itemStyle: { borderWidth: 1.5 } } : {}
        // 单系列颜色由 ecApplyLayoutToSpec 使用 cat10[0] 等统一默认
      });
      if (pointData.length > 0) {
        option.series.push(makePointSeries("Points", pointData, 0, 1, isHorizontal, showPoints));
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
      key: "showPoints",
      label: "Points",
      type: "binary",
      defaultValue: false,
      // Jitter needs a band to scatter within, so this is only meaningful
      // once one position axis is discrete.
      check: (ctx) => ({
        applicable: isDiscrete8(ctx.channelSemantics?.x?.type) || isDiscrete8(ctx.channelSemantics?.y?.type)
      })
    },
    {
      key: "showOutliers",
      label: "Outliers",
      type: "binary",
      defaultValue: true,
      // Outliers exist only with Tukey whiskers; min–max whiskers absorb
      // every point. And once every observation is drawn, the outlier
      // marks are just duplicates.
      check: (ctx) => ({
        applicable: ctx.chartProperties?.whiskerMethod !== "minmax" && ctx.chartProperties?.showPoints !== true
      })
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
        delete option.legend.left;
        option.legend.right = BUFFER;
        option.legend.top = 20;
        option.legend.orient = option.legend.orient || "vertical";
        option.legend.align = "left";
        if (Array.isArray(option.graphic)) {
          for (const g of option.graphic) {
            if (g.type === "text" && (g.top === 4 || g.top === 20) && g.style && g.style.fontWeight === "bold") {
              delete g.left;
              g.right = BUFFER;
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
    const fmt2 = (n) => {
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
      outerText.push(fmt2(top));
      innerText.push((t === "delta" && v > 0 ? "+" : "") + fmt2(v));
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
    const encScheme = schemeName(encodings?.color?.scheme);
    const userScheme = encScheme && encScheme !== "default" ? encScheme : void 0;
    const schemeName2 = userScheme || "viridis";
    const decision = colorDecisions?.color ?? colorDecisions?.group;
    let schemeColors = SCHEME_COLORS2[schemeName2] || SCHEME_COLORS2.viridis;
    if (decision?.schemeId) {
      const fromDecision = getPaletteForScheme(decision.schemeId);
      if (fromDecision && fromDecision.length > 0) schemeColors = fromDecision;
    }
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const [date2, val] = params.value;
          return `${date2}<br/>${valueField ?? "Count"}: ${val}`;
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
      get: (enc) => schemeName(enc.color?.scheme),
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
    const pad2 = Math.abs(min) > 1e-9 ? Math.abs(min) * 0.1 : 1;
    return [min - pad2, max + pad2];
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
    const pad2 = 64;
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
        left: pad2,
        right: pad2,
        top: pad2,
        bottom: pad2
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
    const valueOf2 = (cat) => Number(byCat.get(cat)?.[valueField]);
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
      const v = valueOf2(cat);
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
          const v = valueOf2(cat);
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

// src/core/resolve-semantics.ts
var isCategoryValue = (value) => typeof value === "string" || typeof value === "boolean" || typeof value === "number" && Number.isFinite(value);
function inspectEncodingSort(encoding, channel, encodings, dataFields, options = {}) {
  const issues = [];
  const describe = (property, value) => `encodings.${channel}.${property} ${typeof value === "string" ? `"${value}"` : JSON.stringify(value)}`;
  let sortOrder;
  if (encoding.sortOrder === "ascending" || encoding.sortOrder === "descending") {
    sortOrder = encoding.sortOrder;
  } else if (encoding.sortOrder !== void 0) {
    issues.push({
      property: "sortOrder",
      value: encoding.sortOrder,
      error: `encodings.${channel}.sortOrder must be "ascending" or "descending".`,
      warning: `${describe("sortOrder", encoding.sortOrder)} must be "ascending" or "descending"; ignoring it.`
    });
  }
  const sortBy = encoding.sortBy;
  if (sortBy === void 0) return { sortOrder, issues };
  const reject = (error, reason) => {
    issues.push({
      property: "sortBy",
      value: sortBy,
      error,
      warning: `${describe("sortBy", sortBy)} ${reason}; using default order.`
    });
    return { issues };
  };
  const invalid = `encodings.${channel}.sortBy must name an existing data field, a mapped channel (x, y, color), or a JSON category-order array; received ${JSON.stringify(sortBy)}.`;
  if (typeof sortBy !== "string" || !sortBy.trim()) {
    return reject(invalid, "is not a data field, mapped channel, or JSON category array");
  }
  if (sortBy === "x" || sortBy === "y" || sortBy === "color") {
    const binding = encodings[sortBy];
    const targets = Array.isArray(binding) ? binding : [binding];
    if (!targets.some((target) => typeof target === "string" ? target.length > 0 : target && typeof target === "object" && (typeof target.field === "string" || target.aggregate === "count"))) {
      return reject(
        `encodings.${channel}.sortBy: channel "${sortBy}" must be mapped to a field or count aggregation.`,
        "names a channel that is not mapped"
      );
    }
    return { sort: { kind: "channel", channel: sortBy }, sortBy, sortOrder, issues };
  }
  if (dataFields.has(sortBy)) {
    if (options.fieldSort === false) {
      return reject(
        `chart_spec.encodings.${channel}.sortBy: raw field-name sorting is not supported by this backend; bind the sort field to a supported measure channel and sort by that channel.`,
        "sorts by a raw data field, which this backend does not support"
      );
    }
    return { sort: { kind: "field", field: sortBy }, sortBy, sortOrder, issues };
  }
  let values;
  try {
    values = JSON.parse(sortBy);
  } catch {
    return reject(invalid, "is not a data field");
  }
  if (!Array.isArray(values)) return reject(invalid, "is not a data field or JSON category array");
  const valid = values.filter(isCategoryValue);
  if (valid.length === 0 && values.length > 0) return reject(invalid, "contains no valid category values");
  if (valid.length < values.length) {
    const dropped = values.length - valid.length;
    issues.push({
      property: "sortBy",
      value: sortBy,
      error: invalid,
      warning: `${describe("sortBy", sortBy)} contains ${dropped} invalid category value${dropped === 1 ? "" : "s"}; ignoring ${dropped === 1 ? "it" : "them"}.`
    });
    return { sort: { kind: "values", values: valid }, sortBy: JSON.stringify(valid), sortOrder, issues };
  }
  return { sort: { kind: "values", values: valid }, sortBy, sortOrder, issues };
}
function repairEncodingSorts(encodings, data, options = {}) {
  const warnings = [];
  if (!encodings || typeof encodings !== "object") return { encodings, warnings };
  const dataFields = /* @__PURE__ */ new Set();
  let fieldsCollected = false;
  const repair = (channel, entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return entry;
    const encoding = entry;
    const sortByValue = encoding.sortBy;
    if (!fieldsCollected && typeof sortByValue === "string" && sortByValue.trim() && sortByValue !== "x" && sortByValue !== "y" && sortByValue !== "color") {
      for (const row of data) {
        if (row && typeof row === "object") {
          const fields = Object.keys(row);
          for (let index = 0; index < fields.length; index++) dataFields.add(fields[index]);
        }
      }
      fieldsCollected = true;
    }
    const { sortBy, sortOrder, issues } = inspectEncodingSort(encoding, channel, encodings, dataFields, options);
    if (issues.length === 0) return entry;
    for (const issue of issues) {
      warnings.push({ severity: "warning", code: "invalid_sort", message: issue.warning, channel });
    }
    const next = { ...encoding };
    delete next.sortBy;
    delete next.sortOrder;
    if (sortBy !== void 0) next.sortBy = sortBy;
    if (sortOrder !== void 0) next.sortOrder = sortOrder;
    return next;
  };
  const repaired = Object.fromEntries(Object.entries(encodings).map(([channel, value]) => [
    channel,
    Array.isArray(value) ? value.map((entry) => repair(channel, entry)) : repair(channel, value)
  ]));
  return { encodings: repaired, warnings };
}
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
function inferImplicitSemanticType(fieldName, values) {
  const tokens = fieldName.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  if (!tokens.includes("year")) return "";
  const observed = values.filter((value) => value != null && value !== "");
  if (new Set(observed.map(String)).size <= 1) return "";
  const allYears = observed.every((value) => {
    const numeric = typeof value === "number" ? value : Number(value);
    return Number.isInteger(numeric) && numeric >= 1500 && numeric <= 2200;
  });
  return allYears ? "Year" : "";
}
var ISO_PREFIX = /^\d{4}-\d{2}/;
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
  const fieldValues = Object.fromEntries(keys.map((key) => [key, data.map((row) => row[key])]));
  const effectiveSemanticTypes = Object.fromEntries(keys.map((key) => [
    key,
    toTypeString(semanticTypes[key]) || inferImplicitSemanticType(key, fieldValues[key])
  ]));
  const temporalKeys = keys.filter((k) => {
    const st = effectiveSemanticTypes[k];
    const vc = inferVisCategory(fieldValues[k]);
    const stCategory = st ? getVisCategory(st) : null;
    return vc === "temporal" || stCategory === "temporal" || st === "Decade";
  });
  if (temporalKeys.length === 0) return data;
  const values = structuredClone(data);
  return values.map((r) => {
    for (const temporalKey of temporalKeys) {
      const val = r[temporalKey];
      const st = effectiveSemanticTypes[temporalKey];
      const calendar = (SEMANTIC_LEVEL[st] ?? -1) >= SEMANTIC_LEVEL.Date;
      const parsed = readIsoTimestamp(val, calendar) ?? parseTemporalByType(val, st) ?? (!st && typeof val === "string" && !ISO_PREFIX.test(val.trim()) ? parseTemporalByType(val, "Date") : null);
      if (parsed !== null) {
        r[temporalKey] = parsed;
      } else if (typeof val === "number") {
        if (st === "Year" || st === "Decade") {
          r[temporalKey] = `${Math.floor(val)}`;
        } else if (isLikelyTimestamp(val)) {
          r[temporalKey] = new Date(timestampToMs(val)).toISOString();
        } else {
          r[temporalKey] = String(val);
        }
      } else if (val instanceof Date) {
        const localMidnight = !val.getHours() && !val.getMinutes() && !val.getSeconds() && !val.getMilliseconds();
        r[temporalKey] = calendar && localMidnight && Number.isFinite(val.getTime()) ? `${val.getFullYear()}-${String(val.getMonth() + 1).padStart(2, "0")}-${String(val.getDate()).padStart(2, "0")}` : val.toISOString();
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
function resolveFieldChannelType(channel, encoding, fieldName, data, semanticTypes, context = {}) {
  const rawAnnotation = semanticTypes[fieldName];
  const suppliedSemanticType = typeof rawAnnotation === "string" ? rawAnnotation || "" : rawAnnotation?.semanticType ?? "";
  const fieldValues = data.map((r) => r[fieldName]);
  const semanticType = suppliedSemanticType || inferImplicitSemanticType(fieldName, fieldValues);
  let decisionData = data;
  let decisionValues = fieldValues;
  if (hasTemporalParsers(semanticType)) {
    const parsed = fieldValues.map((v) => parseTemporalByType(v, semanticType) ?? v);
    if (parsed.some((v, i) => v !== fieldValues[i])) {
      decisionValues = parsed;
      decisionData = data.map((r, i) => ({ ...r, [fieldName]: parsed[i] }));
    }
  }
  const typeDecision = resolveEncodingType(
    semanticType,
    decisionValues,
    channel,
    decisionData,
    fieldName,
    context
  );
  let type = typeDecision.vlType;
  if (encoding.type) {
    type = encoding.type;
  } else if (channel === "column" || channel === "row") {
    if (type !== "nominal" && type !== "ordinal") {
      type = "nominal";
    }
  }
  if (type === "quantitative") {
    const sampleValues = data.slice(0, 15).filter((r) => r[fieldName] != void 0).map((r) => r[fieldName]);
    const isoDateRegex = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;
    if (sampleValues.length > 0 && sampleValues.every((val) => isoDateRegex.test(`${val}`.trim()))) {
      type = "temporal";
    }
  }
  const snapshots = typeDecision.snapshots === true && (type === "ordinal" || type === "nominal");
  return { semanticType, fieldValues, type, snapshots };
}
function resolveChannelSemantics(encodings, data, semanticTypes, convertedData, context = {}) {
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
    const { semanticType, fieldValues, type: resolvedType, snapshots } = resolveFieldChannelType(
      channel,
      encoding,
      fieldName,
      data,
      semanticTypes,
      context
    );
    const fc = resolveFieldSemantics(rawAnnotation || semanticType, fieldName, fieldValues);
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
      stackable,
      ...snapshots && !encoding.aggregate ? { snapshots: true } : {}
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
      const named = schemeName(encoding.scheme);
      if (named && named !== "default") {
        cs.colorScheme = {
          scheme: named,
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
            const neutral = typeof annotation.divergingNeutral === "number" && annotation.divergingNeutral >= 0 ? annotation.divergingNeutral : annotation.divergingNeutral === void 0 && divInfo.inherent ? "auto" : void 0;
            if (neutral !== void 0) cs.colorScheme.neutral = neutral;
          }
          if (annotation.polarity === "higher-is-better") {
            cs.colorScheme = { ...cs.colorScheme, scheme: "redblue", reason: "declared higher-is-better: red is the low side" };
          } else if (annotation.polarity === "lower-is-better") {
            cs.colorScheme = { ...cs.colorScheme, scheme: "blueorange", reason: "declared lower-is-better: the warm end is high" };
          }
        }
      }
    }
    if (cs.type === "temporal" || semanticType && getVisCategory(semanticType) === "temporal") {
      const convertedFieldValues = temporalData.map((r) => r[fieldName]);
      const fmt2 = resolveTemporalFormat(convertedFieldValues, semanticType);
      if (fmt2) cs.temporalFormat = fmt2;
    }
    if (cs.type === "ordinal" || cs.type === "nominal") {
      if (encoding.sortBy === void 0) {
        const ordinalSort = inferOrdinalSortOrder(semanticType, fieldValues);
        if (ordinalSort) {
          cs.ordinalSortOrder = ordinalSort;
        } else if (isDateField(semanticType, fieldValues)) {
          const chronological = chronologicalOrder(temporalData.map((r) => r[fieldName]));
          if (chronological) cs.ordinalSortOrder = chronological;
        }
      }
    }
    result[channel] = cs;
  }
  return result;
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
  const viewports = [];
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
      const orderedValues = strategy === defaultOverflowStrategy ? defaultOverflowOrder(channel, fieldName, uniqueValues, strategyContext) : void 0;
      const valuesToKeep = orderedValues ? orderedValues.slice(0, maxToKeep) : strategy(channel, fieldName, uniqueValues, maxToKeep, strategyContext);
      if ((channel === "x" || channel === "y") && orderedValues) {
        viewports.push({
          channel,
          field: fieldName,
          orderedValues,
          visibleCount: valuesToKeep.length,
          totalCount: orderedValues.length
        });
      }
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
  return { filteredData, nominalCounts, truncations, warnings, viewports };
}
var defaultOverflowStrategy = (channel, fieldName, uniqueValues, maxToKeep, context) => defaultOverflowOrder(channel, fieldName, uniqueValues, context).slice(0, maxToKeep);
function defaultOverflowOrder(channel, fieldName, uniqueValues, context) {
  const { data, channelSemantics, encodings, allMarkTypes } = context;
  const encoding = encodings[channel];
  const { sort: explicitSort, sortBy, sortOrder } = inspectEncodingSort(
    encoding ?? {},
    channel,
    encodings,
    new Set(data.flatMap((row) => Object.keys(row)))
  );
  if (explicitSort?.kind === "field") {
    const minimums = /* @__PURE__ */ new Map();
    for (const row of data) {
      const value = row[explicitSort.field];
      if (value == null || value !== value) continue;
      const category = row[fieldName];
      if (!minimums.has(category) || value < minimums.get(category)) minimums.set(category, value);
    }
    const direction = sortOrder === "descending" ? -1 : 1;
    return [...uniqueValues].sort((left, right) => {
      const leftValue = minimums.get(left);
      const rightValue = minimums.get(right);
      if (leftValue == null) return rightValue == null ? 0 : -direction;
      if (rightValue == null) return direction;
      return direction * (leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0);
    });
  }
  if (explicitSort?.kind === "values") {
    const ordered = sortOrder === "descending" ? [...explicitSort.values].reverse() : explicitSort.values;
    const present = new Set(uniqueValues);
    return ordered.filter((value) => present.has(value));
  }
  let sortField;
  let sortFieldType;
  let isDescending = false;
  if (sortBy) {
    if (sortBy === "x" || sortBy === "y" || sortBy === "color") {
      const sortCS = channelSemantics[sortBy];
      sortField = sortCS?.field;
      sortFieldType = sortCS?.type;
      isDescending = sortOrder === "descending" || sortOrder !== "ascending" && sortBy !== channel;
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
    return Array.from(valueAggregates.entries()).map(([value, agg]) => ({ value, agg })).sort((a, b) => isDescending ? b.agg - a.agg : a.agg - b.agg).map((v) => v.value);
  }
  const canonicalOrder = channelSemantics[channel]?.ordinalSortOrder;
  if (!sortBy && canonicalOrder?.length) {
    const present = new Set(uniqueValues);
    const ordered = canonicalOrder.filter((value) => present.has(value));
    const canonicalValues = new Set(ordered);
    ordered.push(...uniqueValues.filter((value) => !canonicalValues.has(value)));
    return sortOrder === "descending" ? ordered.reverse() : ordered;
  }
  const fieldOriginalType = inferVisCategory(data.map((r) => r[fieldName]));
  if (fieldOriginalType === "quantitative" || channel === "color") {
    return [...uniqueValues].sort((a, b) => Number(a) - Number(b));
  }
  if (channel === "column" || channel === "row") {
    return uniqueValues;
  }
  if (sortOrder === "descending") {
    return [...uniqueValues].sort((a, b) => String(b).localeCompare(String(a), void 0, { numeric: true }));
  }
  if (sortOrder === "ascending") {
    return [...uniqueValues].sort((a, b) => String(a).localeCompare(String(b), void 0, { numeric: true }));
  }
  return uniqueValues;
}

// src/core/text-case.ts
var MINOR = /* @__PURE__ */ new Set(["a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "per", "the", "to", "vs", "via"]);
var capitalise = (word) => word.charAt(0).toUpperCase() + word.slice(1);
function applyTextCase(text, textCase) {
  if (!text || !textCase || textCase === "asIs") return text;
  if (textCase === "upper") return text.toUpperCase();
  if (textCase === "lower") return text.toLowerCase();
  if (textCase === "sentence") return capitalise(text);
  const words = text.split(/(\s+)/);
  const last = words.length - 1;
  return words.map((w, i) => {
    if (!w.trim()) return w;
    if (i > 0 && i < last && MINOR.has(w.toLowerCase())) return w.toLowerCase();
    return capitalise(w);
  }).join("");
}

// src/core/label-sequence.ts
var KIND_OF = {
  Time: "minute",
  DateTime: "minute",
  Hour: "hour",
  Date: "day",
  YearMonth: "month",
  Month: "monthOfYear",
  YearQuarter: "quarter",
  Quarter: "cycle",
  Year: "year",
  Decade: "year",
  Rank: "number",
  Week: "cycle",
  YearWeek: "cycle",
  Day: "cycle"
};
var ROUND = {
  minute: [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440],
  hour: [1, 2, 3, 6, 12, 24],
  day: [1, 2, 7, 14, 28],
  month: [1, 2, 3, 6, 12, 24, 60, 120],
  monthOfYear: [1, 2, 3, 6],
  quarter: [1, 2, 4, 8, 20, 40],
  year: [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1e3],
  number: [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1e3],
  cycle: [1, 2, 3, 4, 5, 7, 10]
};
var MONTHS2 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
var WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
function sequenceKindOf(semanticType) {
  return semanticType ? KIND_OF[semanticType] : void 0;
}
function valueOf(kind, name) {
  const text = name.trim();
  const digits = () => Number(text.match(/-?\d+(\.\d+)?/)?.[0] ?? NaN);
  switch (kind) {
    case "minute": {
      const time = text.match(/(\d{1,2}):(\d{2})/);
      if (time) return Number(time[1]) * 60 + Number(time[2]);
      const parsed = Date.parse(text);
      return Number.isFinite(parsed) ? Math.round(parsed / 6e4) : NaN;
    }
    case "hour": {
      const time = text.match(/^(\d{1,2})(:00)?/);
      return time ? Number(time[1]) : NaN;
    }
    case "day": {
      const parsed = Date.parse(text);
      return Number.isFinite(parsed) ? Math.round(parsed / 864e5) : NaN;
    }
    case "month": {
      const iso = text.match(/^(\d{4})-(\d{1,2})/);
      if (iso) return Number(iso[1]) * 12 + Number(iso[2]) - 1;
      const parsed = new Date(Date.parse(text));
      return Number.isFinite(parsed.getTime()) ? parsed.getFullYear() * 12 + parsed.getMonth() : NaN;
    }
    case "monthOfYear": {
      const index = MONTHS2.indexOf(text.slice(0, 3).toLowerCase());
      return index >= 0 ? index : digits() - 1;
    }
    case "quarter": {
      const match = text.match(/(\d{4})\D*Q?([1-4])$/i) ?? text.match(/Q([1-4])\D*(\d{4})/i);
      if (!match) return NaN;
      const [year, quarter] = match[1].length === 4 ? [match[1], match[2]] : [match[2], match[1]];
      return Number(year) * 4 + Number(quarter) - 1;
    }
    case "cycle": {
      const day = WEEKDAYS.indexOf(text.slice(0, 3).toLowerCase());
      return day >= 0 ? day : digits();
    }
    default:
      return digits();
  }
}
function roundSkip(kind, names, band, needed) {
  const values = names.map((name) => valueOf(kind, name));
  if (values.some((value) => !Number.isFinite(value))) return void 0;
  const sorted = [...new Set(values)].sort((a, b) => a - b);
  let unit = Infinity;
  for (let i = 1; i < sorted.length; i++) unit = Math.min(unit, sorted[i] - sorted[i - 1]);
  if (!Number.isFinite(unit) || unit <= 0) return void 0;
  for (const interval of ROUND[kind]) {
    if (interval < unit || interval % unit !== 0) continue;
    if (interval / unit * band < needed) continue;
    const shown = names.filter((_, i) => (values[i] % interval + interval) % interval === 0);
    if (shown.length >= 2) return { shown, spacing: interval / unit };
    break;
  }
  return void 0;
}

// src/core/band-label-choice.ts
var TURN_COST = { rotate45: 0.3, rotate90: 0.34 };
var SHRINK_COST = 0.25;
var SKIP_COST = 0.15;
var WRAP_COST = 0.04;
var EXPANSION_COST = 0.18;
function cutLoss(texts, widthOf, limit, fontSize) {
  if (!Number.isFinite(limit)) return 0;
  let sum = 0;
  for (const lines of texts) {
    const full = lines.reduce((total, line) => total + widthOf(line), 0);
    if (full <= 0) continue;
    const kept = lines.reduce((total, line) => total + (widthOf(line) <= limit ? widthOf(line) : Math.max(0, limit - fontSize)), 0);
    sum += Math.max(0, 1 - kept / full);
  }
  return sum / Math.max(1, texts.length);
}
function gapAt(thresholds, size) {
  return thresholds.gapEm !== void 0 ? thresholds.gapEm * size : Math.max(6, size / 2);
}
function trimKeeps(width, fontSize, minChars) {
  return width >= (minChars + 1) * fontSize * 0.6;
}
function chooseXLabels(input) {
  const { values, shown, stated, floor, step, fillStep, thresholds } = input;
  if (!shown.length || !(step > 0)) return void 0;
  const may = (way) => input.steps.includes(way) && (way !== "skip" || input.sequence !== void 0);
  const trim = !input.noTrim;
  const count = shown.length;
  const span = Math.max(1, input.baseSpan);
  const widen = (band) => EXPANSION_COST * Math.max(0, Math.min(band * count, span) - Math.min(step * count, span)) / span;
  const options = [];
  const sizes = [];
  for (let size2 = stated; size2 >= floor - 1e-9; size2 -= 0.5) sizes.push(Math.round(size2 * 2) / 2);
  if (!sizes.length) sizes.push(stated);
  for (const size2 of sizes) {
    const widthOf2 = input.measure(size2);
    const widest2 = Math.max(0, ...shown.map(widthOf2));
    const gap = gapAt(thresholds, size2);
    const needed = widest2 + gap;
    const shrink = SHRINK_COST * (stated - size2) / stated;
    const one2 = shown.map((text) => [text]);
    const flatBand = Math.max(step, fillStep);
    if (needed <= step) {
      options.push({ step: "fit", trimmed: false, fontSize: size2, angle: 0, band: step, needed, cost: shrink });
      continue;
    }
    for (const stretch of size2 === sizes[sizes.length - 1] ? [false, true] : [false]) {
      const held = input.wrapAt(size2, stretch);
      if (!held) continue;
      const twoLines = held.lines.some((parts) => parts.length > 1);
      const loss = cutLoss(held.lines, widthOf2, held.width, size2);
      if (loss > 0 && (!trim || !trimKeeps(held.width, size2, thresholds.minChars))) continue;
      if ((twoLines || loss > 0) && !may("wrap")) continue;
      options.push({
        step: twoLines || loss > 0 ? "wrap" : "fit",
        trimmed: loss > 0,
        fontSize: size2,
        angle: 0,
        band: held.step,
        limit: held.width,
        ...twoLines ? { lines: held.lines, lineHeight: held.lineHeight } : {},
        needed,
        cost: held.cost + shrink
      });
      break;
    }
    const cutWidth = flatBand - gap;
    if (may("wrap") && trim && trimKeeps(cutWidth, size2, thresholds.minChars)) {
      const fit = fitTwoLineLabels(shown, size2, cutWidth, true, widthOf2);
      const lines = fit?.lines ?? one2;
      const wrapped = lines.filter((parts) => parts.length > 1).length / lines.length;
      options.push({
        step: "wrap",
        trimmed: true,
        fontSize: size2,
        angle: 0,
        band: flatBand,
        limit: Math.floor(cutWidth),
        ...wrapped > 0 ? { lines, lineHeight: Math.ceil(size2 * 1.2) } : {},
        needed,
        cost: cutLoss(lines, widthOf2, cutWidth, size2) + WRAP_COST * wrapped + widen(flatBand) + shrink
      });
    }
    if (may("skip") && input.sequence) {
      const whole = roundSkip(input.sequence, values, flatBand, needed);
      const room = trim ? roundSkip(input.sequence, values, flatBand, (thresholds.minChars + 1) * size2 * 0.6 + gap) : void 0;
      for (const [found, trimmed] of [[whole, false], [whole ? void 0 : room, true]]) {
        if (!found) continue;
        const limit = Math.floor(found.spacing * flatBand - gap);
        const printed = found.shown.map((name) => [shown[values.indexOf(name)] ?? name]);
        options.push({
          step: "skip",
          trimmed,
          fontSize: size2,
          angle: 0,
          band: flatBand,
          shown: found.shown,
          ...trimmed ? { limit } : {},
          needed,
          cost: SKIP_COST * (1 - found.shown.length / values.length) + (trimmed ? cutLoss(printed, widthOf2, limit, size2) : 0) + widen(flatBand) + shrink
        });
      }
    }
    const line = size2 * 1.2;
    for (const [way, room] of [["rotate45", line * Math.SQRT2], ["rotate90", line]]) {
      if (!may(way) || step < room) continue;
      const loss = trim ? cutLoss(one2, widthOf2, thresholds.turnedLimit, size2) : 0;
      options.push({
        step: way,
        trimmed: loss > 0,
        fontSize: size2,
        angle: way === "rotate45" ? -45 : -90,
        band: step,
        ...trim && Number.isFinite(thresholds.turnedLimit) ? { limit: Math.floor(thresholds.turnedLimit) } : {},
        needed,
        cost: TURN_COST[way] + loss + shrink
      });
    }
  }
  const at = (option) => option.step === "fit" ? -1 : input.steps.indexOf(option.step);
  const wholeAt = Math.min(...options.filter((option) => !option.trimmed).map(at));
  let open = Number.isFinite(wholeAt) ? options.filter((option) => at(option) <= wholeAt) : options;
  if (open.some((option) => option.step === "skip")) open = open.filter((option) => option.angle === 0);
  if (open.some((option) => option.angle === -45)) open = open.filter((option) => option.angle !== -90);
  if (open.length) return open.reduce((best, option) => option.cost < best.cost ? option : best);
  const size = Math.max(1, Math.min(floor, Math.floor(step / 1.2 * 2) / 2));
  const widthOf = input.measure(size);
  const widest = Math.max(0, ...shown.map(widthOf));
  return {
    step: "rotate90",
    trimmed: trim && widest > thresholds.turnedLimit,
    fontSize: size,
    angle: -90,
    band: step,
    ...trim && Number.isFinite(thresholds.turnedLimit) ? { limit: Math.floor(thresholds.turnedLimit) } : {},
    needed: widest + gapAt(thresholds, size),
    cost: Infinity
  };
}

// src/core/axis-labels.ts
var DEFAULT_LABEL_OVERFLOW = ["wrap", "rotate90"];
var DEFAULT_SEQUENCE_OVERFLOW = ["wrap", "skip", "rotate90"];
var FIT_STEPS = ["wrap", "skip", "rotate45", "rotate90"];
function statedSteps(stated) {
  const list = (Array.isArray(stated) ? stated : stated != null ? [stated] : []).filter((step) => FIT_STEPS.includes(step));
  return list.length ? list : void 0;
}
function resolveLabelOverflow(labels, chartOverflow, axis = "names") {
  return statedSteps(chartOverflow) ?? statedSteps(labels?.overflow) ?? [...axis === "sequence" ? DEFAULT_SEQUENCE_OVERFLOW : DEFAULT_LABEL_OVERFLOW];
}

// src/core/natural-shape.ts
var NATURAL_MIN_SPREAD = 1.25;
var FALLBACK_WIDEN = 1.5;
function naturalAspectRange(base, stretch, override = {}) {
  const centre = override.centre && override.centre > 0 ? override.centre : base.width / Math.max(1, base.height);
  if (override.spread && override.spread >= 1) return { lo: centre / override.spread, hi: centre * override.spread };
  return {
    lo: centre / Math.max(NATURAL_MIN_SPREAD, stretch.y),
    hi: centre * Math.max(NATURAL_MIN_SPREAD, stretch.x)
  };
}
function widenRange(range, factor) {
  return { lo: range.lo / factor, hi: range.hi * factor };
}
function shapeDivergence(shape, range) {
  if (shape.lo > range.hi) return shape.lo / range.hi;
  if (shape.hi < range.lo) return shape.hi / range.lo;
  return 1;
}

// src/core/compute-layout.ts
var VL_SHORT_DISCRETE_CATEGORY_COUNT = 4;
var VL_SHORT_DISCRETE_LABEL_MAX_LEN = 8;
var APPROX_CHAR_WIDTH_RATIO = 0.62;
var SPARSE_FIT_BAND_CEILING = 100;
var SPARSE_FILL_SHARE = 0.85;
var SPARSE_FILL_BAR_CAP = 200;
var SPARSE_FILL_POINT_CAP = 360;
var DEFAULT_MIN_STEP = 8;
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
    meanLen: labels.reduce((sum, s) => sum + s.length, 0) / labels.length,
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
var CALENDAR_COST = 0.05;
function flatCalendarFit(lines, at) {
  const span = Math.max(1, at.baseSpan);
  const count = lines.length;
  let best;
  for (let size = at.stated; size >= at.floor - 1e-9; size -= 0.5) {
    const widthOf = at.measure(size);
    const gap = at.gapEm !== void 0 ? at.gapEm * size : Math.max(6, size / 2);
    const needed = Math.max(0, ...lines.flat().map(widthOf)) + gap;
    const band = Math.max(at.step, needed);
    if (band > at.step && band * count > span + 1e-9) continue;
    const cost = SHRINK_COST * (at.stated - size) / at.stated + EXPANSION_COST * Math.max(0, band * count - at.step * count) / span;
    if (!best || cost < best.cost) best = { step: "fit", trimmed: false, fontSize: size, angle: 0, band, needed, cost };
  }
  return best;
}
var AXIS_LABEL_LIMIT = 180;
function planBandLabels(context, options = {}) {
  const { layout, canvasSize, channelSemantics } = context;
  const assembleOptions = context.assembleOptions ?? {};
  const caps = resolveStretchCaps(assembleOptions);
  for (const channel of ["x", "y"]) {
    const semantics = channelSemantics[channel];
    const discrete = channel === "x" ? layout.xNominalCount > 0 : layout.yNominalCount > 0;
    if (!discrete || !semantics?.field || !["nominal", "ordinal"].includes(semantics.type)) continue;
    if (options.skip?.(channel)) continue;
    const labels = [...new Set(context.table.map((row) => row[semantics.field]).filter((value) => value != null).map(String))];
    const numeric = labels.every((label) => label.trim() !== "" && Number.isFinite(Number(label)));
    if (!labels.length || numeric && !(channel === "x" && options.sequence)) continue;
    const sizing = channel === "x" ? layout.xLabel : layout.yLabel;
    const step = channel === "x" ? layout.xStep : layout.yStep;
    const panels = channel === "x" ? layout.facet?.columns ?? 1 : layout.facet?.rows ?? 1;
    const fixed = channel === "x" ? assembleOptions.facetFixedPadding?.width ?? 0 : assembleOptions.facetFixedPadding?.height ?? 0;
    const canvasSpan = channel === "x" ? canvasSize.width : canvasSize.height;
    const subplotSpan = channel === "x" ? layout.subplotWidth : layout.subplotHeight;
    const maxSpan = (canvasSpan * caps[channel] - fixed) / panels - layout.effectiveFacetGap;
    const baseSpan = Math.min(subplotSpan, (canvasSpan - fixed) / panels - layout.effectiveFacetGap);
    const stated = options.fontSize ?? (channel === "x" ? Math.max(sizing.fontSize, BASE_TICK_FONT_SIZE) : sizing.fontSize);
    const shown = labels.map((label) => applyTextCase(label, options.textCase));
    const givenRowWidth = channel === "y" ? assembleOptions.canvasTreatment?.rowNames : void 0;
    const ownGutter = rowLabelLimit(options.fit, canvasSize.width, layout.subplotWidth);
    const fitAt = (fontSize2, stretchToFit = false, compareBaseline = true, gutterLimit = givenRowWidth ?? ownGutter, texts = shown) => computeBandLabelLayout({
      labels: texts,
      axis: channel,
      fontSize: fontSize2,
      step,
      baseSpan,
      maxSpan,
      gutterLimit,
      baselineLimit: sizing.labelLimit,
      elasticity: assembleOptions.elasticity ?? 0.5,
      ...options.measure ? { measure: options.measure(fontSize2) } : {},
      ...stretchToFit ? { stretchToFit } : {},
      ...compareBaseline ? {} : { compareBaseline }
    });
    if (channel === "x") {
      const sequence = options.overflow ?? (options.sequence ? DEFAULT_SEQUENCE_OVERFLOW : DEFAULT_LABEL_OVERFLOW);
      const floor = Math.min(stated, Math.max(options.minFontSize ?? 0, stated * 0.75));
      const measure2 = options.measure ?? ((fontSize2) => (text) => text.length * fontSize2 * APPROX_CHAR_WIDTH_RATIO);
      const choiceInput = {
        values: labels,
        shown,
        stated,
        floor,
        measure: measure2,
        step,
        fillStep: Math.floor(baseSpan / labels.length),
        baseSpan,
        wrapAt: (fontSize2, stretch) => fitAt(fontSize2, stretch, false),
        steps: sequence,
        ...options.sequence ? { sequence: options.sequence } : {},
        noTrim: options.noLimit === true,
        thresholds: labelFitThresholds(options.fit, layout.subplotHeight)
      };
      let choice = chooseXLabels(choiceInput);
      if (!choice) continue;
      let calendar;
      if ((choice.step !== "fit" || choice.fontSize < stated) && isDateField(semantics.semanticAnnotation?.semanticType, labels)) {
        const order = semantics.ordinalSortOrder?.map(String);
        const axisOrder = order && labels.every((label) => order.includes(label)) ? order.filter((value) => labels.includes(value)) : labels;
        const named = calendarLabels(axisOrder);
        if (named) {
          const byValue = new Map(axisOrder.map((value, index) => [value, named[index]]));
          const lines = labels.map((label) => (byValue.get(label) ?? [label]).map((line) => applyTextCase(line, options.textCase)));
          const alt = flatCalendarFit(lines, {
            stated,
            floor,
            measure: measure2,
            step,
            baseSpan,
            gapEm: labelFitThresholds(options.fit, layout.subplotHeight).gapEm
          });
          if (alt && alt.cost + CALENDAR_COST < choice.cost) {
            choice = alt;
            calendar = lines;
          }
        }
      }
      layout.xStep = choice.band;
      const flat = choice.angle === 0;
      Object.assign(sizing, {
        fontSize: choice.fontSize,
        labelAngle: choice.angle,
        labelAlign: flat ? "center" : "right",
        labelBaseline: flat ? "top" : choice.angle === -90 ? "middle" : "top",
        labelLimit: choice.limit !== void 0 ? choice.limit + 2 : flat ? Math.ceil(Math.max(choice.needed, Math.min(choice.needed * 1.15, choice.band - 4))) + 2 : options.noLimit ? 0 : sizing.labelLimit,
        ...choice.lines ? { labelValues: labels, labelLines: choice.lines, labelLineHeight: choice.lineHeight } : calendar ? {
          labelValues: labels,
          labelLines: calendar,
          labelLineHeight: calendar.some((lines) => lines.length > 1) ? Math.ceil(choice.fontSize * 1.2) : void 0
        } : { labelValues: void 0, labelLines: void 0, labelLineHeight: void 0 },
        labelForm: calendar ? "calendar" : void 0,
        ...choice.fontSize < stated ? { fittedFontSize: choice.fontSize } : { fittedFontSize: void 0 },
        ...choice.step === "fit" ? { overflow: void 0 } : {
          overflow: {
            step: choice.step,
            trimmed: choice.trimmed,
            needed: choice.needed,
            band: choice.band,
            ...choice.shown ? { shown: choice.shown } : {}
          }
        }
      });
      continue;
    }
    const whole = (laid, size) => {
      if (!laid || givenRowWidth === void 0) return laid;
      const widthOf2 = options.measure?.(size) ?? ((text) => text.length * size * APPROX_CHAR_WIDTH_RATIO);
      return laid.lines.every((row) => row.length <= 2 && row.every((line) => widthOf2(line) <= laid.width + 1)) ? laid : null;
    };
    let fontSize = stated;
    let fit = whole(fitAt(stated, false, givenRowWidth === void 0), stated);
    if (!fit && givenRowWidth !== void 0) {
      const floor = Math.min(stated, Math.max(options.minFontSize ?? 0, stated * 0.75), sizing.fontSize);
      for (let size = stated - 1; !fit && size >= floor; size--) {
        fit = whole(fitAt(size, false, false), size);
        fontSize = size;
      }
      if (!fit) {
        fontSize = stated;
        fit = fitAt(stated, false, true, ownGutter);
      }
    }
    const widthOf = options.measure?.(fontSize) ?? ((text) => text.length * fontSize * APPROX_CHAR_WIDTH_RATIO);
    const cap = options.rowCodes ? void 0 : adaptiveRowNameCap(shown.map(widthOf), layout.subplotWidth, fontSize);
    const widest = fit ? Math.max(...fit.lines.flat().map(widthOf)) : Math.max(...shown.map(widthOf));
    let cut = false;
    if (cap !== void 0 && widest > cap + 1) {
      const capped = fitAt(fontSize, false, false, cap);
      if (!capped) {
        Object.assign(sizing, {
          fontSize,
          labelAngle: 0,
          labelAlign: "right",
          labelBaseline: "middle",
          labelLimit: Math.floor(cap),
          labelCut: true,
          labelValues: void 0,
          labelLines: void 0,
          labelLineHeight: void 0
        });
        continue;
      }
      fit = capped;
      cut = !capped.lines.every((row) => row.length <= 2 && row.every((line) => widthOf(line) <= capped.width + 1));
    }
    if (!fit) continue;
    layout.yStep = fit.step;
    Object.assign(sizing, {
      fontSize,
      labelAngle: 0,
      labelAlign: "right",
      labelBaseline: "middle",
      labelLimit: cut ? Math.floor(cap) : Math.ceil(fit.width) + 2,
      labelValues: labels,
      labelLines: fit.lines,
      labelLineHeight: fit.lineHeight,
      ...cut ? { labelCut: true } : {}
    });
  }
}
function rowNameCap(plotWidth, fontSize) {
  return Math.max(ROW_NAME_CAP_EM * fontSize, plotWidth * ROW_NAME_CAP_SHARE - ROW_NAME_TICK_ROOM);
}
var ROW_NAME_CAP_EM = 12;
function adaptiveRowNameCap(widths, plotWidth, fontSize) {
  const soft = rowNameCap(plotWidth, fontSize);
  if (!widths.length) return soft;
  const ceiling = Math.max(soft, plotWidth - ROW_NAME_TICK_ROOM);
  const long = widths.filter((width) => width > soft).map((width) => Math.min(width, ceiling));
  if (!long.length) return soft;
  const runTo = long.reduce((sum, width) => sum + width, 0) / long.length;
  const lift = smoothstep((long.length / widths.length - ROW_NAME_FEW) / (ROW_NAME_MANY - ROW_NAME_FEW));
  return Math.min(ceiling, soft + (runTo - soft) * lift);
}
var ROW_NAME_FEW = 0.1;
var ROW_NAME_MANY = 0.4;
function smoothstep(t) {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
}
var ROW_NAME_CAP_SHARE = 0.6;
var ROW_NAME_TICK_ROOM = 10;
var BASE_TICK_FONT_SIZE = 10;
function labelFitThresholds(fit, plotHeight) {
  const turned = fit?.turnedLimit ?? "40%";
  const turnedLimit = typeof turned === "number" ? turned : Math.max(30, Number.parseFloat(turned) / 100 * Math.max(1, plotHeight));
  return { minChars: fit?.minChars ?? 4, turnedLimit, ...fit?.gap !== void 0 ? { gapEm: fit.gap } : {} };
}
function rowLabelLimit(fit, canvasWidth, plotWidth) {
  const width = Math.min(canvasWidth, plotWidth || canvasWidth);
  const stated = fit?.rowLimit;
  if (typeof stated === "number") return stated;
  if (typeof stated === "string") return Number.parseFloat(stated) / 100 * canvasWidth;
  return Math.max(AXIS_LABEL_LIMIT, width / 4);
}
var DEFAULT_MAX_STRETCH = 1.5;
function resolveBaseSize(specBaseSize, ceiling) {
  const base = specBaseSize ?? { ...DEFAULT_BASE_SIZE };
  if (!ceiling) return { width: base.width, height: base.height };
  return {
    width: Math.min(base.width, ceiling.width),
    height: Math.min(base.height, ceiling.height)
  };
}
function facetPanelLength(canvas, panels, cap, elasticity, fixed, gap) {
  const stretch = Math.min(cap, Math.pow(panels, elasticity));
  return (canvas * stretch - fixed) / panels - gap;
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
var CELL_BAND_SIZE = 28;
var SQUARE_CELL_TOLERANCE = 1.5;
var CELL_GROWTH_MAX = 2;
var LABEL_ROOM_SHARE = 0.3;
var LABEL_ROOM_FLOOR = 40;
var VALUE_LABEL_CHARS = [3, 6];
var TICK_ROOM = 6;
var BALANCE_STEP_GROWTH = 2;
var BALANCE_SIDE_FLOOR = 0.5;
function computeLayout(channelSemantics, declaration, table, canvasSize, options = {}, facetGrid, pinnedPlot) {
  const {
    elasticity: elasticityVal = 0.5,
    facetElasticity: facetElasticityVal = 0.3,
    minStep: minStepVal = DEFAULT_MIN_STEP,
    minSubplotSize: minSubplotVal = 60,
    stepPadding: stepPaddingVal = 0.1,
    bandStepFit: bandStepFitVal = 0,
    maintainContinuousAxisRatio = false,
    continuousMarkCrossSection,
    facetAspectRatioResistance = 0
  } = options;
  const { x: maxStretchX, y: maxStretchY } = resolveStretchCaps(options);
  const defaultChartWidth = canvasSize.width;
  const defaultChartHeight = canvasSize.height;
  const fixW = options.facetFixedPadding?.width ?? 0;
  const fixH = options.facetFixedPadding?.height ?? 0;
  const gapX = options.facetGapColumn ?? options.facetGap ?? 0;
  const gapY = options.facetGapRow ?? options.facetGap ?? 0;
  const baseRefSize = 300;
  const sizeRatio = Math.max(defaultChartWidth, defaultChartHeight) / baseRefSize;
  const baseBandSize = options.defaultBandSize ?? 20;
  const defaultStepSize = Math.round(baseBandSize * Math.max(1, sizeRatio));
  const r1 = (value) => Math.round(value * 10) / 10;
  const trace = { width: [], height: [], xStep: [], yStep: [] };
  const stage = (key, rule, value, read, note) => {
    trace[key].push({ rule, value: typeof value === "number" ? r1(value) : value, ...read ? { read } : {}, ...note ? { note } : {} });
  };
  stage("width", "canvas", defaultChartWidth, { base: canvasSize.width, stretchCap: r1(maxStretchX) }, "the base width; the plot may stretch past it up to the cap");
  stage("height", "canvas", defaultChartHeight, { base: canvasSize.height, stretchCap: r1(maxStretchY) }, "the base height; the plot may stretch past it up to the cap");
  for (const key of ["xStep", "yStep"]) {
    stage(key, "house band", defaultStepSize, { band: baseBandSize, sizeRatio: r1(sizeRatio) }, "the band one category gets, scaled with the canvas past 300 px");
  }
  const isDiscreteType = (t) => t === "nominal" || t === "ordinal";
  const effectiveTypes = {};
  for (const [ch, cs] of Object.entries(channelSemantics)) {
    effectiveTypes[ch] = declaration.resolvedTypes?.[ch] || cs.type;
  }
  const axisFlags = declaration.axisFlags || {};
  const xBanded = axisFlags.x?.banded ?? false;
  const yBanded = axisFlags.y?.banded ?? false;
  const effectiveBandStepFit = xBanded && yBanded ? 0 : bandStepFitVal;
  const maxBandSize = options.maxBandSize == null ? effectiveBandStepFit > 0 ? Math.max(baseBandSize, SPARSE_FIT_BAND_CEILING) : baseBandSize : Math.max(baseBandSize, options.maxBandSize);
  const maxStepSize = options.maxBandSize == null && effectiveBandStepFit > 0 ? Math.max(defaultStepSize, SPARSE_FIT_BAND_CEILING) : Math.round(maxBandSize * Math.max(1, sizeRatio));
  const rowScale = Math.max(1, options.rowNamesAbove?.scale ?? 1);
  const yDefaultStep = Math.round(defaultStepSize * rowScale);
  const yMinStep = Math.max(Math.round(minStepVal * rowScale), options.rowNamesAbove?.minStep ?? 0);
  const yMaxStep = Math.round(maxStepSize * rowScale);
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
    subplotWidth = Math.round(Math.max(
      minContinuousSizeX,
      facetPanelLength(defaultChartWidth, facetCols, maxStretchX, facetElasticityVal, fixW, gapX)
    ));
  } else {
    subplotWidth = defaultChartWidth;
  }
  let subplotHeight;
  if (facetRows > 1) {
    subplotHeight = Math.round(Math.max(
      minContinuousSizeY,
      facetPanelLength(defaultChartHeight, facetRows, maxStretchY, facetElasticityVal, fixH, gapY)
    ));
  } else {
    subplotHeight = defaultChartHeight;
  }
  if (facetCols > 1) stage("width", "facet panel", subplotWidth, { columns: facetCols, allowance: fixW, gap: gapX, elasticity: facetElasticityVal }, "the canvas stretched a little for its columns, less the allowance, shared between them");
  if (facetRows > 1) stage("height", "facet panel", subplotHeight, { rows: facetRows, allowance: fixH, gap: gapY, elasticity: facetElasticityVal }, "the canvas stretched a little for its rows, less the allowance, shared between them");
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
          (defaultChartWidth * Math.min(maxStretchX, Math.pow(facetCols, facetElasticityVal)) - fixW) / facetCols - gapX
        ) : defaultChartWidth;
        const perSubplotCanvasH = facetRows > 1 ? Math.max(
          minContinuousSizeY,
          (defaultChartHeight * Math.min(maxStretchY, Math.pow(facetRows, facetElasticityVal)) - fixH) / facetRows - gapY
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
        const availW = facetCols > 1 ? Math.max(minContinuousSizeX, (defaultChartWidth * maxStretchX - fixW) / facetCols - gapX) : defaultChartWidth * maxStretchX;
        const availH = facetRows > 1 ? Math.max(minContinuousSizeY, (defaultChartHeight * maxStretchY - fixH) / facetRows - gapY) : defaultChartHeight * maxStretchY;
        const scaleX = idealW > availW ? availW / idealW : 1;
        const scaleY = idealH > availH ? availH / idealH : 1;
        const fitScale = Math.min(scaleX, scaleY);
        let finalW = idealW * fitScale;
        let finalH = idealH * fitScale;
        finalW = Math.max(finalW, minContinuousSizeX);
        finalH = Math.max(finalH, minContinuousSizeY);
        subplotWidth = Math.round(finalW);
        subplotHeight = Math.round(finalH);
        stage("width", "crowding", subplotWidth, { points: xNumeric.length, stretchX: r1(idealResult.rawStretchX), banked: useBanking, budget: r1(availW) }, "both axes continuous: the points press the plot wider by their crowding, the shape is banked toward readable slopes, then fitted to the stretch budget");
        stage("height", "crowding", subplotHeight, { points: yNumeric.length, stretchY: r1(idealResult.rawStretchY), banked: useBanking, budget: r1(availH) }, "both axes continuous: the points press the plot taller by their crowding, the shape is banked toward readable slopes, then fitted to the stretch budget");
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
  if (!bothContinuousNonBanded && (xIsContinuousNonBanded || yIsContinuousNonBanded)) {
    const key = xIsContinuousNonBanded ? "width" : "height";
    const value = xIsContinuousNonBanded ? subplotWidth : subplotHeight;
    const last = trace[key][trace[key].length - 1];
    if (last.value !== r1(value)) stage(key, "crowding", value, { rows: table.length }, "the values along the continuous axis press it longer, by a power of their crowding");
  }
  const elasticParamsX = {
    elasticity: elasticityVal,
    maxStretch: maxStretchX,
    defaultStepSize,
    bandStepFit: effectiveBandStepFit,
    bandStepFitCapacity: Math.max(
      1,
      (options.bandStepFitCapacityX ?? defaultChartWidth) / defaultChartWidth
    ),
    minStep: minStepVal
  };
  const elasticParamsY = {
    elasticity: elasticityVal,
    maxStretch: maxStretchY,
    defaultStepSize: yDefaultStep,
    bandStepFit: effectiveBandStepFit,
    bandStepFitCapacity: Math.max(
      1,
      (options.bandStepFitCapacityY ?? defaultChartHeight) / defaultChartHeight
    ),
    minStep: yMinStep
  };
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
  let xStepFitted = false;
  let yStepFitted = false;
  if (xIsDiscrete && xHasGrouping) {
    const itemsPerGroup = nominalCount.group;
    const defaultGroupStep = itemsPerGroup * maxStepSize;
    const minGroupStep = Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * itemsPerGroup);
    const groupElasticX = {
      ...elasticParamsX,
      defaultStepSize: elasticParamsX.defaultStepSize * itemsPerGroup
    };
    const groupAxis2 = computeAxisStep(nominalCount.x, 0, subplotWidth, groupElasticX);
    const groupStep = Math.max(minGroupStep, Math.min(defaultGroupStep, groupAxis2.step));
    xStepSize = groupStep;
    xStepUnit = "group";
    xStepFitted = groupAxis2.fitted;
  } else if (xIsDiscrete || xContinuousAsDiscrete > 0) {
    xStepSize = Math.max(minStepVal, Math.min(maxStepSize, xAxis.step));
    xStepFitted = xAxis.fitted;
  } else {
    xStepSize = defaultStepSize;
  }
  if (yIsDiscrete && yHasGrouping) {
    const itemsPerGroup = nominalCount.group;
    const defaultGroupStep = itemsPerGroup * maxStepSize;
    const minGroupStep = Math.max(Math.ceil(MIN_GROUP_GAP_PX / stepPaddingVal), 2 * itemsPerGroup);
    const groupElasticY = {
      ...elasticParamsY,
      defaultStepSize: elasticParamsY.defaultStepSize * itemsPerGroup
    };
    const groupAxis2 = computeAxisStep(nominalCount.y, 0, subplotHeight, groupElasticY);
    const groupStep = Math.max(minGroupStep, Math.min(defaultGroupStep, groupAxis2.step));
    yStepSize = groupStep;
    yStepUnit = "group";
    yStepFitted = groupAxis2.fitted;
  } else if (yIsDiscrete || yContinuousAsDiscrete > 0) {
    yStepSize = Math.max(yMinStep, Math.min(yMaxStep, yAxis.step));
    yStepFitted = yAxis.fitted;
  } else {
    yStepSize = defaultStepSize;
  }
  for (const axis of ["x", "y"]) {
    const key = axis === "x" ? "xStep" : "yStep";
    const items = axis === "x" ? xTotalNominalCount || xContinuousAsDiscrete : yTotalNominalCount || yContinuousAsDiscrete;
    const step = axis === "x" ? xStepSize : yStepSize;
    if (items <= 0) {
      stage(key, "no bands", step, {}, "the axis is continuous: no step sizes it");
      continue;
    }
    const grouped = axis === "x" ? xIsDiscrete && xHasGrouping : yIsDiscrete && yHasGrouping;
    const span = axis === "x" ? subplotWidth : subplotHeight;
    const decision = axis === "x" ? xAxis : yAxis;
    if (grouped) {
      stage(key, "group step", step, { groups: axis === "x" ? nominalCount.x : nominalCount.y, lanes: nominalCount.group, span }, "one step holds a group of lanes: a house band per lane, held within the room the groups share");
      continue;
    }
    const perBand = span / items;
    const pressed = decision.step < defaultStepSize;
    const stretched = decision.budget > span + 0.5;
    stage(
      key,
      "bands in the span",
      decision.step,
      { bands: items, span, spanPerBand: r1(perBand), budget: r1(decision.budget), stretch: r1(decision.budget / span), fitted: decision.fitted },
      pressed && stretched ? "the house band would overrun the span: the span stretches by a power of the pressure, to its cap, and the step shrinks to share it" : pressed ? "the house band would overrun the span and the cap allows no stretch, so the step is the span per band" : decision.fitted ? "sparse bands grow toward the room the canvas leaves" : "the house band fits the span and keeps its size"
    );
    if (step !== decision.step) stage(key, "clamped", step, axis === "y" ? { min: yMinStep, max: yMaxStep } : { min: minStepVal, max: maxStepSize }, "held between the smallest readable step and the widest a band may be");
  }
  const fill = options.sparseFill;
  if (fill && true && facetCols === 1 && xTotalNominalCount > 0 && yTotalNominalCount === 0 && yContinuousAsDiscrete === 0) {
    const tuning = typeof fill === "object" ? fill : {};
    const share = Math.max(0, Math.min(1, tuning.share ?? SPARSE_FILL_SHARE));
    const room = options.bandStepFitCapacityX ?? defaultChartWidth;
    const tick = computeFontSizing(Math.min(subplotWidth, subplotHeight), { baseLabelFontSize: options.baseLabelFontSize, baseTitleFontSize: options.baseTitleFontSize }).tickBase;
    const gutter = VALUE_LABEL_CHARS[1] * tick * APPROX_CHAR_WIDTH_RATIO + TICK_ROOM;
    const grouped = xIsDiscrete && xHasGrouping;
    const bands = grouped ? nominalCount.x : xTotalNominalCount;
    const lanes = grouped ? nominalCount.group : 1;
    const cap = (options.sparseFillBars === false ? tuning.pointCap ?? SPARSE_FILL_POINT_CAP : tuning.barCap ?? SPARSE_FILL_BAR_CAP) * lanes;
    const want = Math.floor(Math.min(cap, Math.max(0, share * room - gutter) / bands));
    if (want > xStepSize) {
      const from = xStepSize;
      xStepSize = want;
      stage("xStep", "sparse fill", xStepSize, { from, bands, room: r1(room), gutter: r1(gutter), share, cap }, "few bands thicken until the drawing spans a share of the room the canvas leaves it, each band no wider than one mark reads");
    }
  }
  for (const axis of ["x", "y"]) {
    const need = options.labelStep?.[axis];
    const banded = axis === "x" ? xIsDiscrete || xContinuousAsDiscrete > 0 : yIsDiscrete || yContinuousAsDiscrete > 0;
    const step = axis === "x" ? xStepSize : yStepSize;
    if (!need || !banded || need <= step) continue;
    if (axis === "x") xStepSize = need;
    else yStepSize = need;
    stage(axis === "x" ? "xStep" : "yStep", "widened for value labels", need, { from: step }, "each band widens to hold its value label, into the room the canvas has");
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
  const maxSubplotW = (defaultChartWidth * maxStretchX - fixW) / facetCols - gapX;
  const maxSubplotH = (defaultChartHeight * maxStretchY - fixH) / facetRows - gapY;
  const budgetNote = "the plot may stretch only to the cap times the base, less the allowance; the bands share that";
  if (xTotalNominalCount > 0) {
    const divisor = xStepUnit === "group" ? nominalCount.x : xTotalNominalCount;
    const cap = Math.max(minStepVal, Math.floor(maxSubplotW / divisor));
    if (xStepSize > cap) {
      xStepSize = cap;
      stage("xStep", "stretch budget", cap, { budget: r1(maxSubplotW), bands: divisor, stretchCap: r1(maxStretchX), allowance: fixW }, budgetNote);
    }
  }
  if (xContinuousAsDiscrete > 0) {
    const cap = Math.max(minStepVal, Math.floor(maxSubplotW / (xContinuousAsDiscrete + 1)));
    if (xStepSize > cap) {
      xStepSize = cap;
      stage("xStep", "stretch budget", cap, { budget: r1(maxSubplotW), bands: xContinuousAsDiscrete + 1, stretchCap: r1(maxStretchX), allowance: fixW }, budgetNote);
    }
  }
  let rowNamesAboveFit;
  if (yTotalNominalCount > 0) {
    const divisor = yStepUnit === "group" ? nominalCount.y : yTotalNominalCount;
    if (rowScale > 1) rowNamesAboveFit = Math.floor(maxSubplotH / divisor) >= yMinStep;
    const cap = Math.max(yStepUnit === "group" ? minStepVal : yMinStep, Math.floor(maxSubplotH / divisor));
    if (yStepSize > cap) {
      yStepSize = cap;
      stage("yStep", "stretch budget", cap, { budget: r1(maxSubplotH), bands: divisor, stretchCap: r1(maxStretchY), allowance: fixH }, budgetNote);
    }
  }
  if (yContinuousAsDiscrete > 0) {
    const cap = Math.max(minStepVal, Math.floor(maxSubplotH / (yContinuousAsDiscrete + 1)));
    if (yStepSize > cap) {
      yStepSize = cap;
      stage("yStep", "stretch budget", cap, { budget: r1(maxSubplotH), bands: yContinuousAsDiscrete + 1, stretchCap: r1(maxStretchY), allowance: fixH }, budgetNote);
    }
  }
  for (const axis of ["x", "y"]) {
    const count = axis === "x" ? xContinuousAsDiscrete : yContinuousAsDiscrete;
    if (count <= 0) continue;
    const stepSize = axis === "x" ? xStepSize : yStepSize;
    if (axis === "x") subplotWidth = Math.round(stepSize * (count + 1));
    else subplotHeight = Math.round(stepSize * (count + 1));
    stage(axis === "x" ? "width" : "height", "banded values", Math.round(stepSize * (count + 1)), { values: count, step: r1(stepSize) }, "a continuous field drawn in bands spans one step per value, plus one");
  }
  const isConnectedMark = typeof continuousMarkCrossSection === "object" && !!continuousMarkCrossSection.seriesCountAxis;
  const bothDiscreteConnected = isConnectedMark && xTotalNominalCount > 0 && yTotalNominalCount > 0 && !xHasGrouping && !yHasGrouping;
  if (bothDiscreteConnected && typeof continuousMarkCrossSection === "object") {
    const csX = continuousMarkCrossSection.x ?? 0;
    const csY = continuousMarkCrossSection.y ?? 0;
    if (csX > 0 && csY > 0) {
      const RUN_AR_CAP = 2;
      const runIsX = csX >= csY;
      const crossCS = runIsX ? csY : csX;
      const runBudget = runIsX ? Math.floor(maxSubplotW / xTotalNominalCount) : Math.floor(maxSubplotH / yTotalNominalCount);
      const cross = Math.max(
        minStepVal,
        Math.min(runIsX ? yStepSize : xStepSize, crossCS)
      );
      const ratio = Math.min(RUN_AR_CAP, Math.max(1, Math.max(csX, csY) / Math.min(csX, csY)));
      const run = Math.max(
        runIsX ? xStepSize : yStepSize,
        Math.min(runBudget, Math.round(cross * ratio))
      );
      if (runIsX) {
        xStepSize = run;
        yStepSize = cross;
      } else {
        yStepSize = run;
        xStepSize = cross;
      }
    }
  }
  let squaredCells = false;
  if (xTotalNominalCount > 0 && yTotalNominalCount > 0 && !xHasGrouping && !yHasGrouping && !bothDiscreteConnected) {
    const capX = Math.floor(maxSubplotW / xTotalNominalCount);
    const capY = Math.floor(maxSubplotH / yTotalNominalCount);
    const fill2 = Math.min(defaultChartWidth / xTotalNominalCount, defaultChartHeight / yTotalNominalCount);
    const growth = Math.min(1, Math.max(0, options.cellGrowth ?? 0));
    const generous = CELL_BAND_SIZE + Math.max(0, Math.min(fill2, CELL_BAND_SIZE * CELL_GROWTH_MAX) - CELL_BAND_SIZE) * growth;
    const wanted = Math.max(generous, Math.min(xStepSize, yStepSize));
    const square = Math.min(capX, capY, wanted);
    const widest = Math.max(xStepSize, yStepSize);
    if (square >= minStepVal && square * SQUARE_CELL_TOLERANCE >= widest) {
      xStepSize = square;
      yStepSize = square;
      squaredCells = true;
      for (const key of ["xStep", "yStep"]) stage(key, "square cells", square, { wanted, capX, capY }, "both axes banded: the marks are cells, pulled to one size so tones compare by area");
    }
  }
  if (subplotWidth > Math.round(maxSubplotW)) stage("width", "stretch budget", Math.round(maxSubplotW), { stretchCap: r1(maxStretchX), allowance: fixW, columns: facetCols, gap: gapX }, "held to the cap times the base, less the allowance and the gaps, shared by the columns");
  if (subplotHeight > Math.round(maxSubplotH)) stage("height", "stretch budget", Math.round(maxSubplotH), { stretchCap: r1(maxStretchY), allowance: fixH, rows: facetRows, gap: gapY }, "held to the cap times the base, less the allowance and the gaps, shared by the rows");
  subplotWidth = Math.min(subplotWidth, Math.round(maxSubplotW));
  subplotHeight = Math.min(subplotHeight, Math.round(maxSubplotH));
  if (xContinuousAsDiscrete > 0) xStepSize = Math.min(xStepSize, subplotWidth / (xContinuousAsDiscrete + 1));
  if (yContinuousAsDiscrete > 0) yStepSize = Math.min(yStepSize, subplotHeight / (yContinuousAsDiscrete + 1));
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
        stage("height", "band shape", subplotHeight, { bandRatio: r1(actualBandAR), target: targetBandAR, step: r1(xStepSize), ideal: r1(idealH) }, "bars taller than the target ratio of their step blend halfway toward it, in log space");
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
        stage("width", "band shape", subplotWidth, { bandRatio: r1(actualBandAR), target: targetBandAR, step: r1(yStepSize), ideal: r1(idealW) }, "bars longer than the target ratio of their step blend halfway toward it, in log space");
      }
    }
  }
  {
    const xBands = xTotalNominalCount > 0 && xContinuousAsDiscrete === 0 ? xStepUnit === "group" ? nominalCount.x : xTotalNominalCount : 0;
    const yBands = yTotalNominalCount > 0 && yContinuousAsDiscrete === 0 ? yStepUnit === "group" ? nominalCount.y : yTotalNominalCount : 0;
    const oneBanded = xBands > 0 && !yBands && yIsContinuousNonBanded || yBands > 0 && !xBands && xIsContinuousNonBanded;
    if (options.bandBalance !== false && true && oneBanded) {
      const xStats = xBands ? computeDiscreteLabelStats(channelSemantics.x?.field, table) : null;
      const yStats = yBands && rowScale === 1 ? computeDiscreteLabelStats(channelSemantics.y?.field, table) : null;
      const plotW = (z) => xBands ? z.xStep * xBands : z.w;
      const plotH = (z) => yBands ? z.yStep * yBands : z.h;
      const gridW = (z) => facetCols * plotW(z) + (facetCols - 1) * gapX;
      const gridH = (z) => facetRows * plotH(z) + (facetRows - 1) * gapY;
      const fontsOf = (z) => computeFontSizing(Math.min(plotW(z), plotH(z)), { baseLabelFontSize: options.baseLabelFontSize, baseTitleFontSize: options.baseTitleFontSize });
      const nameFont = (step, z) => computeLabelSizing(step, true, { baseFont: fontsOf(z).tickBase, minFont: 6 });
      const px = (chars, font) => chars * font * APPROX_CHAR_WIDTH_RATIO;
      const titleLine = (z) => fontsOf(z).titleFontSize * 1.25 + 4;
      const beside = (z) => {
        const tick = fontsOf(z).tickBase;
        let lo = px(VALUE_LABEL_CHARS[0], tick), hi = px(VALUE_LABEL_CHARS[1], tick);
        if (yStats) {
          const font = nameFont(z.yStep, z).fontSize;
          const cap = Math.max(LABEL_ROOM_FLOOR, LABEL_ROOM_SHARE * plotW(z));
          lo = Math.min(cap, px(yStats.meanLen, font));
          hi = Math.min(cap, px(yStats.maxLen, font));
        }
        return { lo: lo + TICK_ROOM, hi: hi + TICK_ROOM + titleLine(z) + (facetRows > 1 ? titleLine(z) : 0) };
      };
      const below = (z) => {
        let lo = fontsOf(z).tickBase * 1.25, hi = lo;
        if (xStats) {
          const sizing = nameFont(z.xStep, z);
          const line = sizing.fontSize * 1.25;
          const cap = Math.max(LABEL_ROOM_FLOOR, LABEL_ROOM_SHARE * plotH(z));
          const mean = px(xStats.meanLen, sizing.fontSize), longest = px(xStats.maxLen, sizing.fontSize);
          if (sizing.labelAngle === -90) {
            lo = Math.min(cap, mean);
            hi = Math.min(cap, longest);
          } else {
            lo = mean <= z.xStep ? line : 2 * line;
            hi = longest <= z.xStep ? line : Math.max(2 * line, Math.min(cap, longest));
          }
        }
        return { lo: lo + TICK_ROOM, hi: hi + TICK_ROOM + titleLine(z) + (facetCols > 1 ? titleLine(z) : 0) };
      };
      const shapeOf = (z) => {
        const b = beside(z), u = below(z);
        return { lo: (gridW(z) + b.lo) / (gridH(z) + u.hi), hi: (gridW(z) + b.hi) / (gridH(z) + u.lo) };
      };
      const off = (z, range) => Math.abs(Math.log(shapeDivergence(shapeOf(z), range))) > 0.01;
      const panel = (total, n, gap) => (total - (n - 1) * gap) / n;
      const stepCeiling = (bands, budget, rows = false) => Math.min(
        Math.floor(budget / bands),
        rows ? Math.max(yMaxStep, Math.round(yDefaultStep * BALANCE_STEP_GROWTH)) : Math.max(maxStepSize, Math.round(defaultStepSize * BALANCE_STEP_GROWTH))
      );
      const floorW = Math.max(Math.round(defaultChartWidth * BALANCE_SIDE_FLOOR / facetCols), minContinuousSizeX);
      const floorH = Math.max(Math.round(defaultChartHeight * BALANCE_SIDE_FLOOR / facetRows), minContinuousSizeY);
      const lean = (range, from) => {
        const z = { ...from };
        const wide = shapeDivergence(shapeOf(z), range) > 1;
        if (wide) {
          const wantH = () => panel((gridW(z) + beside(z).lo) / range.hi - below(z).hi, facetRows, gapY);
          if (yBands) z.yStep = Math.max(z.yStep, Math.min(stepCeiling(yBands, maxSubplotH, true), Math.ceil(wantH() / yBands)));
          else z.h = Math.max(z.h, Math.min(Math.round(maxSubplotH), Math.ceil(wantH())));
          if (yBands && off(z, range)) z.w = Math.min(z.w, Math.max(floorW, Math.floor(panel(range.hi * (gridH(z) + below(z).hi) - beside(z).lo, facetCols, gapX))));
        } else {
          const wantW = () => panel(range.lo * (gridH(z) + below(z).lo) - beside(z).hi, facetCols, gapX);
          if (xBands) z.xStep = Math.max(z.xStep, Math.min(stepCeiling(xBands, maxSubplotW), Math.ceil(wantW() / xBands)));
          else z.w = Math.max(z.w, Math.min(Math.round(maxSubplotW), Math.ceil(wantW())));
          if (xBands && off(z, range)) z.h = Math.min(z.h, Math.max(floorH, Math.floor(panel((gridW(z) + beside(z).hi) / range.lo - below(z).lo, facetRows, gapY))));
        }
        return z;
      };
      const natural = naturalAspectRange(
        { width: defaultChartWidth, height: defaultChartHeight },
        { x: maxStretchX, y: maxStretchY },
        { centre: options.targetAspect, spread: options.aspectTolerance }
      );
      const start = { w: subplotWidth, h: subplotHeight, xStep: xStepSize, yStep: yStepSize };
      if (off(start, natural)) {
        let range = natural;
        let tier = "its own range";
        let next = lean(natural, start);
        if (off(next, natural)) {
          range = widenRange(natural, FALLBACK_WIDEN);
          tier = "the looser range round its base";
          next = lean(range, start);
        }
        const shape = shapeOf(start);
        const read = { shape: `${r1(shape.lo)}\u2013${r1(shape.hi)}`, range: `${r1(range.lo)}\u2013${r1(range.hi)}`, columns: facetCols, rows: facetRows };
        const note = `with the labels it cannot drop, the chart fell outside the shapes its base takes on its own; it leans back to the nearer end of ${tier}, by its free sides`;
        if (next.xStep !== xStepSize) {
          xStepSize = next.xStep;
          stage("xStep", "band balance", xStepSize, read, note);
        }
        if (next.yStep !== yStepSize) {
          yStepSize = next.yStep;
          stage("yStep", "band balance", yStepSize, read, note);
        }
        if (next.w !== subplotWidth) {
          subplotWidth = next.w;
          stage("width", "band balance", subplotWidth, read, note);
        }
        if (next.h !== subplotHeight) {
          subplotHeight = next.h;
          stage("height", "band balance", subplotHeight, read, note);
        }
      }
    }
  }
  const given = options.canvasTreatment?.plotSpace;
  if (given && true && facetCols === 1 && facetRows === 1) {
    const note = "the room the box leaves once what is around the chart is settled";
    if (given.width && xIsContinuousNonBanded) {
      subplotWidth = Math.max(minContinuousSizeX, Math.round(given.width));
      stage("width", "given space", subplotWidth, { given: r1(given.width) }, note);
    }
    if (given.height && yIsContinuousNonBanded) {
      subplotHeight = Math.max(minContinuousSizeY, Math.round(given.height));
      stage("height", "given space", subplotHeight, { given: r1(given.height) }, note);
    }
  }
  const xHasDiscreteItems = !declaration.binnedAxes?.x && (xTotalNominalCount > 0 || xContinuousAsDiscrete > 0);
  const yHasDiscreteItems = !declaration.binnedAxes?.y && (yTotalNominalCount > 0 || yContinuousAsDiscrete > 0);
  const fontSizing = computeFontSizing(Math.min(subplotWidth, subplotHeight), {
    baseLabelFontSize: options.baseLabelFontSize,
    baseTitleFontSize: options.baseTitleFontSize
  });
  const labelOpts = { baseFont: fontSizing.tickBase, minFont: options.minLabelFontSize ?? 6 };
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
            stage("xStep", "room for names", xStepSize, { widestName: r1(labelPx), wanted: desiredStep, cap }, "a few short names read straight, so the band widens to hold the widest with a gap, within the stretch budget");
            if (squaredCells && yStepSize < xStepSize && xStepSize <= Math.floor(maxSubplotH / yTotalNominalCount)) {
              yStepSize = xStepSize;
              stage("yStep", "square cells", yStepSize, { columns: xStepSize }, "the columns widened for their names, so the rows widen with them and the cells stay square");
            }
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
            labelAngle: -90,
            labelAlign: "right",
            labelBaseline: "middle"
          };
        }
      } else if (numericLike && labelPx > xStepSize && xLabel.labelAngle === void 0) {
        xLabel = {
          ...xLabel,
          labelAngle: -90,
          labelAlign: "right",
          labelBaseline: "middle"
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
    ...rowNamesAboveFit !== void 0 ? { rowNamesAboveFit } : {},
    xStepUnit,
    yStepUnit,
    xStepFitted,
    yStepFitted,
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
    effectiveFacetGap: gapX,
    truncations: [],
    // Overflow truncations are handled by filterOverflow
    trace
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
    minStep: minStepVal = DEFAULT_MIN_STEP,
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
  const minStep = options.minStep ?? DEFAULT_MIN_STEP;
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

// src/core/title-fit.ts
var MIN_HEADLINE_SCALE = 0.82;
function fullestLines(text, count, block, shortest, fontSize, measure2) {
  const words = text.split(/\s+/).filter(Boolean);
  const n = words.length;
  if (count < 1 || count > n) return void 0;
  const widths = /* @__PURE__ */ new Map();
  const widthOf = (from2, to) => {
    const key = `${from2}:${to}`;
    let width = widths.get(key);
    if (width === void 0) widths.set(key, width = measure2(words.slice(from2, to).join(" "), fontSize));
    return width;
  };
  const cost = Array.from({ length: count + 1 }, () => new Array(n + 1).fill(Infinity));
  const from = Array.from({ length: count + 1 }, () => new Array(n + 1).fill(-1));
  cost[0][0] = 0;
  for (let k = 1; k <= count; k++) {
    const last = k === count;
    for (let j = last ? n : k; j <= n - (count - k); j++) {
      for (let i = k - 1; i < j; i++) {
        if (cost[k - 1][i] === Infinity) continue;
        const width = widthOf(i, j);
        if (width > block || last && width < shortest) continue;
        const c = cost[k - 1][i] + (last ? 0 : (block - width) ** 2);
        if (c < cost[k][j]) {
          cost[k][j] = c;
          from[k][j] = i;
        }
      }
    }
  }
  if (cost[count][n] === Infinity) return void 0;
  const out = [];
  for (let k = count, j = n; k > 0; j = from[k][j], k--) out.unshift(words.slice(from[k][j], j).join(" "));
  return out;
}
function filledLines(text, block, fontSize, measure2) {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [text];
  const lines = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (line && measure2(candidate, fontSize) > block) {
      lines.push(line);
      line = word;
    } else {
      line = candidate;
    }
  }
  if (line) lines.push(line);
  const shortest = block * WIDOW_SHARE;
  if (lines.length >= 2 && measure2(lines[lines.length - 1], fontSize) < shortest) {
    return fullestLines(text, lines.length, block, shortest, fontSize, measure2) ?? lines;
  }
  return lines;
}
var WIDOW_SHARE = 1 / 3;
var MIN_PLOT_HEIGHT = 80;
function plotHeightAfter(height, px) {
  return Math.max(MIN_PLOT_HEIGHT, height - px);
}
function fitHeadlineText(text, block, fontSize, deckFontSize, measure2) {
  if (measure2(text, fontSize) <= block) return void 0;
  const smaller = Math.max(Math.round(fontSize * MIN_HEADLINE_SCALE), deckFontSize + 1);
  if (smaller < fontSize && measure2(text, smaller) <= block) return { smaller, fontSize: smaller, taken: 0 };
  const own = filledLines(text, block, fontSize, measure2);
  const down = smaller < fontSize ? filledLines(text, block, smaller, measure2) : own;
  const size = down.length < own.length ? smaller : fontSize;
  const lines = size === fontSize ? own : down;
  if (lines.length < 2) return void 0;
  return { smaller, ...size < fontSize ? { fontSize: size } : {}, lines, taken: Math.round((lines.length - 1) * size * 1.3) };
}
function fitDeckText(text, block, fontSize, measure2) {
  if (measure2(text, fontSize) <= block) return void 0;
  const lines = filledLines(text, block, fontSize, measure2);
  if (lines.length < 2) return void 0;
  return { lines, taken: Math.round((lines.length - 1) * fontSize * 1.3) };
}

// src/core/ruler.ts
var E10 = Math.sqrt(50);
var E5 = Math.sqrt(10);
var E2 = Math.SQRT2;
function tickIncrement(start, stop, count) {
  const step = (stop - start) / Math.max(0, count);
  const power = Math.floor(Math.log10(step));
  const error = step / 10 ** power;
  const factor = error >= E10 ? 10 : error >= E5 ? 5 : error >= E2 ? 2 : 1;
  return power >= 0 ? factor * 10 ** power : -(10 ** -power) / factor;
}
function niceTicks(start, stop, count) {
  if (!(count > 0)) return [];
  if (start === stop) return [start];
  const step = tickIncrement(start, stop, count);
  if (!Number.isFinite(step) || step === 0) return [];
  return multiples(start, stop, step);
}
function multiples(start, stop, step) {
  const out = [];
  if (step < 0) {
    const inc = -step;
    let i1 = Math.round(start * inc);
    let i2 = Math.round(stop * inc);
    if (i1 / inc < start) i1 += 1;
    if (i2 / inc > stop) i2 -= 1;
    for (let i = i1; i <= i2; i += 1) out.push(i / inc);
  } else {
    let i1 = Math.round(start / step);
    let i2 = Math.round(stop / step);
    if (i1 * step < start) i1 += 1;
    if (i2 * step > stop) i2 -= 1;
    for (let i = i1; i <= i2; i += 1) out.push(i * step);
  }
  return out;
}

// src/core/header-gaps.ts
var KEY_STACK_GAP_EM = 0.6;
var KEY_ROW_GAP_EM = 2.25;
var EASE = { from: 100, to: 500, least: 0.7, most: 1.3 };
var GAP_FLOOR = 4;
function gapEase(plotSide) {
  if (!plotSide || !Number.isFinite(plotSide)) return 1;
  const t = Math.min(1, Math.max(0, (plotSide - EASE.from) / (EASE.to - EASE.from)));
  return EASE.least + (EASE.most - EASE.least) * t * t * (3 - 2 * t);
}
function gapPx(em, labelFontSize, plotSide, floor = GAP_FLOOR, scale = 1) {
  return Math.max(floor, Math.round(em * labelFontSize * gapEase(plotSide) * scale));
}
function keyStackGap(labelFontSize, plotSide, spec, scale = 1) {
  return gapPx(spec?.stack ?? KEY_STACK_GAP_EM, labelFontSize, plotSide, GAP_FLOOR, scale);
}
function keyRowGap(labelFontSize, plotSide, spec, scale = 1) {
  return gapPx(spec?.row ?? KEY_ROW_GAP_EM, labelFontSize, plotSide, GAP_FLOOR, scale);
}

// src/core/layout-costs.ts
var LAYOUT_COSTS = {
  /** Where keys stand, and how several share an edge. */
  keys: {
    /** A key moved off its preferred edge, to the left, or to an edge its house did not rank. */
    otherEdge: 0.25,
    leftEdge: 0.45,
    unrankedEdge: 0.75,
    /**
     * A key's title set against the house's stated position. Where the house states none, either
     * seat costs nothing: the title stays on its entries' line where the row holds it, a line saved.
     */
    offSeat: 0.3,
    quietInlineTop: 0,
    /**
     * A row of keys where some titles stand above and others do not: one key reads as wrapped
     * and the other not, so a row seats its titles alike, or the keys stack.
     */
    mixedSeats: 1.2,
    /**
     * Several keys too wide to stand at the house's anchor, which moves them (`keys-anchor-moved`).
     * Less than a key moved to another edge: keys standing apart are worse than a moved anchor.
     */
    offAnchor: 0.2,
    /** Keys standing against the house's stated arrangement. */
    offArrangement: 0.6,
    /** Each extra row of keys above or below the plot, where the house did not ask for a stack. */
    stackedRow: 0.5,
    /** Each edge beyond the first that holds keys: keys split across edges are read in two places. */
    extraEdge: 0.3,
    /** Keys running past their edge's budget, per share past it: across the plot, beside it. */
    overflowAcross: 20,
    overflowBeside: 4,
    /** The share of its edge's budget the keys take. */
    edgeShare: 0.15
  },
  /** How a key's entries are set: their columns, widths and line breaks. */
  keyEntries: {
    /** Entries running past their budget, per share past it: in height; in width across the plot, beside it. */
    overflowHeight: 10,
    overflowAcross: 10,
    overflowBeside: 2,
    /** The share of the room the entries' width takes: across the plot, beside it. */
    widthAcross: 0.12,
    widthBeside: 1,
    /** A name broken onto a second line, and each line past the second, on average over the names. */
    wrapped: 0.6,
    extraLine: 1.4,
    /** How unevenly the names fill their columns. */
    imbalance: 0.25,
    /** The share of a name cut, on average, and any cut at all. */
    cut: 4,
    anyCut: 0.25,
    /** The share of the height budget a row of entries takes above or below the plot. */
    rowHeight: 0.6,
    /** Each column, so the fewest that hold win a tie. */
    column: 5e-3
  }
};

// src/core/legend-layout.ts
var TITLE_PADDING = 5;
var GRADIENT_FLOOR = 60;
var GRADIENT_FLOOR_LINES = 5;
var INLINE_TITLE_GAP = 1.5;
var K = LAYOUT_COSTS.keys;
var E = LAYOUT_COSTS.keyEntries;
var KEY_LINE_EM = 1.6;
var SHARE_FRACTIONS = [1, 0.65, 0.5, 0.35];
var SIDE_KEY_SHARE = 0.6;
var wrapLines = (text, width, maxLines, widthOf) => {
  if (text.includes("\n")) return text.split("\n");
  const words = text.split(/\s+/);
  const lines = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (line && widthOf(next) > width && lines.length < maxLines - 1) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  return [...lines, line];
};
function symbolGridWidth(key, columns, symbol, labelWidthFallback, labelWidth) {
  const s = key.style;
  const texts = s.labelsStated ? void 0 : key.labels ?? key.valueLabels;
  if (!texts?.length) return columns * (labelWidthFallback + symbol + 4);
  const widths = new Array(Math.min(columns, texts.length)).fill(0);
  texts.forEach((text, index) => {
    const column = index % widths.length;
    widths[column] = Math.max(widths[column], Math.min(s.labelLimit || Infinity, labelWidth(text)));
  });
  const gap = s.columnPadding ?? 10;
  return widths.reduce((sum, width) => sum + symbol + (s.labelOffset ?? 4) + width, 0) + gap * (widths.length - 1);
}
function fitKey(key, at, block, plotHeight, measure2, budget) {
  const s = key.style;
  const fontSize = s.labelFontSize;
  const titleFontSize = s.titleFontSize;
  const measureLabel = measure2(fontSize, s.labelFont, s.labelFontWeight, s.labelFontStyle);
  const labelWidth = (text) => measureLabel(applyTextCase(text, s.labelCase));
  const measureTitle = measure2(titleFontSize, s.titleFont, s.titleFontWeight, s.titleFontStyle);
  const sideWidth = fontSize * 18;
  const horizontal = ["top", "bottom"].includes(at.orient) && at.direction !== "vertical";
  const inlineTitle = horizontal && ["left", "right"].includes(at.titleOrient ?? "");
  const titleWidth = horizontal ? inlineTitle ? block / 3 : block : sideWidth;
  const layout = {
    orient: at.orient,
    moved: false,
    inlineTitle: false,
    labelsFitted: false,
    titleFitted: false,
    ...at.direction !== void 0 ? { direction: at.direction } : {},
    ...at.titleOrient !== void 0 ? { titleOrient: at.titleOrient } : {},
    labelFontSize: fontSize,
    labelLimit: s.labelLimit ?? 0,
    rowPadding: s.rowPadding ?? 2,
    width: 0,
    height: 0,
    ...s.symbolSize !== void 0 ? { symbolSize: s.symbolSize } : {}
  };
  let titleLines;
  if (typeof key.title === "string" && s.titleLimit === void 0) {
    layout.titleFitted = true;
    const lines = wrapLines(key.title, titleWidth, 3, measureTitle);
    if (lines.length > 1) {
      titleLines = lines;
      layout.titleLines = lines;
      layout.titleLineHeight = Math.ceil(titleFontSize * 1.2);
    }
  }
  const titleTexts = titleLines ?? (key.title ? [key.title] : []);
  let titleExtent = 0;
  if (inlineTitle && key.title) {
    const gap = s.titlePadding ?? Math.round(INLINE_TITLE_GAP * (s.columnPadding ?? 10));
    layout.titlePadding = gap;
    titleExtent = Math.min(s.titleLimit || Infinity, Math.max(...titleTexts.map(measureTitle))) + gap;
  }
  const titleCount = titleTexts.length;
  const titleHeight = inlineTitle ? 0 : titleCount * Math.ceil(titleFontSize * 1.2) + (titleCount ? s.titlePadding ?? TITLE_PADDING : 0);
  const count = key.count;
  const nativeRows = Math.ceil(count / (s.columns || (horizontal ? count : 1)));
  const nativeSymbol = Math.sqrt(s.symbolSize ?? key.largestSymbol ?? 100) + 2;
  const nativeLabelWidth = s.labelLimit ?? Math.min(sideWidth, Math.max(fontSize * 4, ...(key.labels ?? key.valueLabels ?? []).map(labelWidth)));
  let gradientLength = s.gradientLength ?? 200;
  if (key.gradient && !horizontal) {
    const room = (budget?.height ?? plotHeight) - titleHeight - 2 * s.padding;
    const floor = Math.max(GRADIENT_FLOOR, Math.ceil(fontSize * 1.2 * GRADIENT_FLOOR_LINES));
    const fitted = Math.max(floor, Math.min(gradientLength, Math.floor(room)));
    if (fitted < gradientLength) {
      gradientLength = fitted;
      layout.gradientLength = fitted;
    }
  }
  if ((key.gradient || key.bar) && s.gradientThickness !== void 0) layout.gradientThickness = s.gradientThickness;
  const nativeColumns = s.columns || (horizontal ? count : 1);
  const ramp = key.gradient || key.bar === true && horizontal;
  let box = {
    width: titleExtent + (ramp ? horizontal ? gradientLength : (s.gradientThickness ?? 16) + nativeLabelWidth + 6 : symbolGridWidth(key, nativeColumns, nativeSymbol, nativeLabelWidth, labelWidth)) + 2 * s.padding,
    height: titleHeight + (ramp ? horizontal ? (s.gradientThickness ?? 16) + fontSize * 1.5 : gradientLength : nativeRows * (Math.max(fontSize * 1.2, nativeSymbol) + (s.rowPadding ?? 2))) + 2 * s.padding,
    fontSize,
    cost: 0,
    entryRows: ramp ? 1 : nativeRows,
    entryLines: 1,
    layout
  };
  if (horizontal && !key.gradient && !key.bar && !key.labels?.length && s.columns === void 0) {
    const room = (budget?.width ?? block) - titleExtent - 2 * s.padding;
    const gridWidth = (columns2) => symbolGridWidth(key, columns2, nativeSymbol, nativeLabelWidth, labelWidth);
    let columns = nativeColumns;
    while (columns > 1 && gridWidth(columns) > room) columns--;
    if (columns < nativeColumns) {
      const rows = Math.ceil(count / columns);
      layout.columns = columns;
      box = {
        ...box,
        width: titleExtent + gridWidth(columns) + 2 * s.padding,
        height: titleHeight + rows * (Math.max(fontSize * 1.2, nativeSymbol) + (s.rowPadding ?? 2)) + 2 * s.padding,
        entryRows: rows
      };
    }
  }
  const labels = key.labels ?? [];
  if (!key.gradient && labels.length && !s.labelsStated && s.labelLimit === void 0) {
    layout.labelsFitted = true;
    const gap = s.columnPadding ?? 10;
    const symbol = Math.sqrt(s.symbolSize ?? 100) + (s.labelOffset ?? 4);
    const available = Math.max(1, (budget?.width ?? (horizontal ? block : sideWidth + symbol)) - titleExtent - 2 * s.padding);
    const heightBudget = budget?.height ?? (horizontal ? Math.max(48, plotHeight * 0.4) : plotHeight);
    const rowGap = s.rowPadding ?? 6;
    let bestCost = Infinity;
    let columns = 1;
    let selectedLines;
    let selectedLimit = 0;
    const columnCounts = typeof s.columns === "number" ? [s.columns > 0 ? Math.min(s.columns, labels.length) : labels.length] : Array.from({ length: labels.length }, (_, index) => index + 1);
    const naturalWidth = Math.max(...labels.map(labelWidth));
    const lineHeight2 = Math.ceil(fontSize * 1.2);
    const headingWidth = inlineTitle || !key.title ? 0 : Math.min(s.titleLimit || Infinity, Math.max(...titleTexts.map(measureTitle)));
    const ellipsis = labelWidth("\u2026");
    const wrapped = /* @__PURE__ */ new Map();
    const wrapAll = (width, maxLines) => {
      const at2 = `${width}|${maxLines}`;
      let wrapping = wrapped.get(at2);
      if (!wrapping) {
        const lines = labels.map((label) => wrapLines(applyTextCase(label, s.labelCase), width, maxLines, measureLabel));
        wrapping = {
          lines,
          rowLines: Math.max(...lines.map((entry) => entry.length)),
          heights: lines.map((entry) => Math.max(Math.sqrt(s.symbolSize ?? 100) + 2, lineHeight2 * entry.length))
        };
        wrapped.set(at2, wrapping);
      }
      return wrapping;
    };
    const settle = (wrapping, width) => {
      if (wrapping.widths === void 0) {
        const { lines } = wrapping;
        wrapping.widths = lines.map((entry) => Math.max(...entry.map((line) => Math.min(width, labelWidth(line)))));
        wrapping.loss = lines.reduce((total, entry, index) => total + entry.reduce((lost, line) => lost + (labelWidth(line) > width ? labelWidth(line) - Math.max(0, width - ellipsis) : 0), 0) / Math.max(1, labelWidth(labels[index])), 0) / labels.length;
        wrapping.wrappingCost = lines.reduce((sum, entry) => sum + (entry.length > 1 ? E.wrapped : 0) + Math.max(0, entry.length - 2) * E.extraLine, 0) / labels.length;
      }
      return wrapping;
    };
    for (const columnCount of columnCounts) {
      const limit = columnCount === labels.length ? available - symbol : (available - gap * (columnCount - 1)) / columnCount - symbol;
      if (limit < fontSize * 4 && columnCount > 1) continue;
      const startWidth = Math.max(Math.min(naturalWidth, fontSize * 4), Math.min(naturalWidth, limit));
      const minWidth = Math.min(startWidth, fontSize * 4);
      const steps = startWidth > minWidth ? 24 : 0;
      for (let step = 0; step <= steps; step++) {
        const textWidth2 = startWidth - (startWidth - minWidth) * step / Math.max(1, steps);
        let wrapping;
        let height = 0;
        for (let maxLines = 3; maxLines >= 1; maxLines--) {
          wrapping = wrapAll(textWidth2, maxLines);
          const { rowLines, heights } = wrapping;
          const rowCount = Math.ceil(labels.length / columnCount);
          height = titleHeight + Math.max(0, rowCount - 1) * rowGap + 2 * s.padding;
          for (let row = 0; row < rowCount; row++) {
            height += horizontal && rowLines > 1 ? Math.max(fontSize + (rowLines - 1) * lineHeight2, Math.sqrt(s.symbolSize ?? 100)) + 4 : Math.max(...heights.slice(row * columnCount, (row + 1) * columnCount));
          }
          if (height <= heightBudget) break;
        }
        const { lines, widths, loss, wrappingCost } = settle(wrapping, textWidth2);
        const columnWidths = Array.from({ length: columnCount }, (_, column) => Math.max(...widths.filter((_2, index) => index % columnCount === column)));
        const extent = columnWidths.reduce((sum, column) => sum + column + symbol, 0) + gap * (columnCount - 1);
        const width = Math.max(extent + titleExtent, headingWidth) + 2 * s.padding;
        const widest = Math.max(...widths, 1);
        const imbalance = (widest - widths.reduce((sum, value) => sum + value, 0) / widths.length) / widest;
        const overflow = Math.max(0, height / heightBudget - 1) * E.overflowHeight + Math.max(0, extent / available - 1) * (horizontal ? E.overflowAcross : E.overflowBeside);
        const widthCost = (horizontal ? E.widthAcross : E.widthBeside) * width / (available + titleExtent + 2 * s.padding);
        const cost = overflow + widthCost + wrappingCost + E.imbalance * imbalance + E.cut * loss + (loss ? E.anyCut : 0) + (horizontal ? E.rowHeight * height / heightBudget : 0) + E.column * columnCount;
        if (cost < bestCost) {
          bestCost = cost;
          columns = columnCount;
          selectedLines = lines;
          selectedLimit = loss ? textWidth2 : 0;
          box = {
            width,
            height,
            fontSize,
            cost: cost - overflow - widthCost,
            entryRows: Math.ceil(labels.length / columnCount),
            entryLines: Math.max(...lines.map((entry) => entry.length)),
            layout
          };
        }
      }
    }
    if (s.columns === void 0 && (!horizontal || columns < labels.length)) layout.columns = columns;
    layout.labelLimit = selectedLimit;
    layout.rowPadding = rowGap;
    if (selectedLines?.some((lines) => lines.length > 1)) layout.labelLines = selectedLines;
  }
  if (key.title) {
    const measuredTitleWidth = Math.min(s.titleLimit || Infinity, Math.max(...titleTexts.map(measureTitle)));
    if (!inlineTitle) box.width = Math.max(box.width, measuredTitleWidth + 2 * s.padding);
    else box.height = Math.max(box.height, titleTexts.length * Math.ceil(titleFontSize * 1.2) + 2 * s.padding);
  }
  layout.width = box.width;
  layout.height = box.height;
  return box;
}
function planKeyLayout(keys, plot, measure2, preferences = {}) {
  const laid = keys.map((key, index) => ({ key, index })).filter((entry) => Boolean(entry.key));
  const out = { keys: keys.map(() => void 0), edges: [], footprint: [], overflow: [], plot, notes: [] };
  if (!laid.length) return out;
  const labelSize = laid[0].key.style.labelFontSize;
  const nativeSide = labelSize * 24 + 14;
  const sides = laid.map(({ key }) => {
    if (key.gradient || !key.labels?.length || key.style.labelsStated || key.style.labelLimit !== void 0) return { text: 0, swatch: 0, wrapped: 0 };
    const widthOf = measure2(key.style.labelFontSize, key.style.labelFont, key.style.labelFontWeight, key.style.labelFontStyle);
    const text = Math.max(...key.labels.map((label) => widthOf(applyTextCase(label, key.style.labelCase))));
    const swatch2 = Math.sqrt(key.style.symbolSize ?? 100) + (key.style.labelOffset ?? 4) + 2 * key.style.padding;
    return { text, swatch: swatch2, wrapped: text / 3 * 1.25 + swatch2 };
  });
  const phrases = sides.some((side) => side.text > labelSize * 18);
  const swatch = Math.max(0, ...sides.map((side) => side.swatch));
  const wrappedSide = Math.max(0, ...sides.map((side) => side.wrapped));
  const narrowSide = Math.max(labelSize * 12 + swatch, plot.width * SIDE_KEY_SHARE);
  const standsWhole = (width) => {
    let height = 0;
    for (const { key } of laid) {
      const style = key.style;
      const lineHeight2 = Math.ceil(style.labelFontSize * 1.2);
      const symbol = Math.sqrt(style.symbolSize ?? 100) + 2;
      const titleHeight = key.title ? Math.ceil(style.titleFontSize * 1.2) + (style.titlePadding ?? TITLE_PADDING) : 0;
      if (key.gradient || !key.labels?.length || style.labelsStated || style.labelLimit !== void 0) {
        height += titleHeight + key.count * (symbol + (style.rowPadding ?? 2));
        continue;
      }
      const widthOf = measure2(style.labelFontSize, style.labelFont, style.labelFontWeight, style.labelFontStyle);
      const room = width - (Math.sqrt(style.symbolSize ?? 100) + (style.labelOffset ?? 4) + 2 * style.padding);
      if (room <= 0) return false;
      for (const label of key.labels) {
        const lines = Math.ceil(widthOf(applyTextCase(label, style.labelCase)) * 1.15 / room);
        if (lines > 3) return false;
        height += Math.max(symbol, lines * lineHeight2) + (style.rowPadding ?? 2);
      }
      height += titleHeight + 2 * style.padding;
    }
    return height <= plot.height;
  };
  const widen = (width) => {
    for (let at = Math.round(width); at < nativeSide; at += 8) if (standsWhole(at)) return at;
    return nativeSide;
  };
  const narrowBudget = preferences.sideWidth !== void 0 ? widen(Math.max(preferences.sideWidth, Math.min(nativeSide, wrappedSide))) : phrases && narrowSide < nativeSide ? widen(Math.max(narrowSide, Math.min(nativeSide, wrappedSide))) : nativeSide;
  const layoutAt = (sideBudget) => {
    const out2 = { keys: keys.map(() => void 0), edges: [], footprint: [], overflow: [], plot, notes: [] };
    const horizontalBudget = Math.max(48, plot.height * 0.4);
    const rowWidth = plot.width + (preferences.besidePlot?.start ?? 0) + (preferences.besidePlot?.end ?? 0);
    const plotSide = Math.min(plot.width, plot.height);
    const edgeGap = keyRowGap(laid[0].key.style.labelFontSize, plotSide, preferences.gaps, preferences.gapScale);
    const stackGap = keyStackGap(laid[0].key.style.labelFontSize, plotSide, preferences.gaps, preferences.gapScale);
    const houseAnchor = preferences.align === "center" ? "middle" : preferences.align === "end" ? "end" : "start";
    const fitsAnchor = (width) => {
      const start = preferences.besidePlot?.start ?? 0;
      const end = preferences.besidePlot?.end ?? 0;
      return houseAnchor === "start" ? width <= plot.width + end : houseAnchor === "end" ? width <= plot.width + start : (width - plot.width) / 2 <= Math.min(start, end);
    };
    const candidatesOf = laid.map(({ key }) => {
      const candidates = [];
      const fitted = /* @__PURE__ */ new Map();
      const symbolLines = key.largestSymbol === void 0 ? 0 : Math.max(0, Math.round(Math.sqrt(key.style.symbolSize ?? key.largestSymbol) / (key.style.labelFontSize * KEY_LINE_EM)) - 1);
      const preferred = key.orient;
      const orients = key.fixed ? [preferred] : key.moveTo ? [key.moveTo] : [.../* @__PURE__ */ new Set([preferred, ...key.edges ?? [], "right", "bottom", "top", "left"])];
      for (const orient of orients) {
        const horizontal = orient === "top" || orient === "bottom";
        for (const fraction of laid.length > 1 ? SHARE_FRACTIONS : [1]) {
          for (const inline of horizontal && !key.fixedTitle ? [false, true] : [false]) {
            const at = orient === preferred ? { orient, direction: key.ownDirection ?? key.direction, titleOrient: key.ownTitleOrient ?? key.titleOrient } : { orient, direction: key.ownDirection ?? (horizontal ? "horizontal" : "vertical"), titleOrient: key.ownTitleOrient ?? "top" };
            if (inline) at.titleOrient = "left";
            const budget = {
              width: horizontal ? rowWidth * fraction : sideBudget,
              height: horizontal ? horizontalBudget : plot.height * fraction
            };
            const fit = `${horizontal}|${at.direction}|${at.titleOrient}|${budget.width}|${budget.height}`;
            let shared = fitted.get(fit);
            if (!shared) fitted.set(fit, shared = fitKey(key, at, horizontal ? rowWidth : plot.width, plot.height, measure2, budget));
            const box = { ...shared, layout: { ...shared.layout, orient } };
            box.layout.moved = orient !== preferred;
            box.layout.inlineTitle = inline;
            if (inline && (box.entryRows !== 1 || box.entryLines !== 1 || box.width > (horizontal ? rowWidth : plot.width) * fraction || (box.layout.titleLines?.length ?? 1) > 1)) continue;
            const edge = orient === preferred ? 0 : key.edges && !key.edges.includes(orient) ? K.unrankedEdge : orient === "left" ? K.leftEdge : K.otherEdge;
            const seat = !horizontal || !key.title || key.fixedTitle ? 0 : preferences.titlePosition ? inline === (preferences.titlePosition === "inline") ? 0 : K.offSeat : orient === "top" && inline ? K.quietInlineTop : 0;
            candidates.push({ ...box, orient, preference: edge + seat, titleAbove: Boolean(key.title) && !inline && at.titleOrient !== "left", symbolLines: symbolLines * Math.max(1, box.entryRows), forced: Boolean(key.moveTo) });
          }
        }
      }
      const fittingSide = candidates.some((candidate) => ["left", "right"].includes(candidate.orient) && candidate.width <= sideBudget && candidate.height <= plot.height);
      return !key.fixed && fittingSide ? candidates.filter((candidate) => !["top", "bottom"].includes(candidate.orient) || candidate.entryRows <= 2 && candidate.entryLines <= 2) : candidates;
    });
    const footprint = (choices) => {
      const edges = [...new Set(choices.map((choice) => choice.orient))].map((orient) => {
        const entries = choices.filter((choice) => choice.orient === orient);
        const horizontal = orient === "top" || orient === "bottom";
        const widthBudget = horizontal ? rowWidth : sideBudget;
        const heightBudget = horizontal ? horizontalBudget : plot.height;
        const shared = entries.length > 1;
        const asked = horizontal && shared ? preferences.arrangement : void 0;
        const alternatives = ["vertical", "horizontal"].map((direction) => {
          const width = direction === "vertical" ? Math.max(...entries.map((entry) => entry.width)) : entries.reduce((sum, entry) => sum + entry.width, 0) + edgeGap * (entries.length - 1);
          const height = direction === "horizontal" ? Math.max(...entries.map((entry) => entry.height)) : entries.reduce((sum, entry) => sum + entry.height, 0) + (horizontal ? stackGap : edgeGap) * (entries.length - 1);
          const overflow = Math.max(0, width / widthBudget - 1) + Math.max(0, height / heightBudget - 1);
          const row = direction === "horizontal";
          const rows = entries.map((entry) => Math.max(1, entry.entryRows) * Math.max(1, entry.entryLines));
          const lines = row ? Math.max(...rows) : rows.reduce((sum, count) => sum + count, 0);
          const natural = !row && asked === "stack" ? entries.length : 1;
          const tall = entries.map((entry) => entry.symbolLines);
          const taller = row ? Math.max(...tall) : tall.reduce((sum, count) => sum + count, 0);
          const stacked = horizontal && shared ? Math.max(0, lines + taller - natural) * K.stackedRow : 0;
          const arrangement = asked && row !== (asked === "row") ? K.offArrangement : 0;
          const seats = horizontal && row && shared && entries.some((entry) => entry.titleAbove) && entries.some((entry) => !entry.titleAbove) ? K.mixedSeats : 0;
          const anchored = horizontal && shared && !fitsAnchor(width) ? K.offAnchor : 0;
          return {
            orient,
            direction,
            width,
            height,
            widthBudget,
            heightBudget,
            overflow,
            asked,
            count: entries.length,
            cost: stacked + arrangement + seats + anchored + Math.max(0, height / heightBudget - 1) * K.overflowAcross + Math.max(0, width / widthBudget - 1) * (horizontal ? K.overflowAcross : K.overflowBeside) + K.edgeShare * (horizontal ? height / heightBudget : width / widthBudget)
          };
        });
        return alternatives.sort((first, second) => first.cost - second.cost)[0];
      });
      const split = choices.some((choice) => choice.forced) ? 0 : Math.max(0, edges.length - 1) * K.extraEdge;
      const cost = edges.reduce((sum, edge) => sum + edge.cost, 0) + choices.reduce((sum, choice) => sum + choice.cost + choice.preference, 0) + split;
      return { cost, edges };
    };
    let plans = [{ choices: [], cost: 0 }];
    for (const candidates of candidatesOf) {
      plans = plans.flatMap((plan) => candidates.map((candidate) => {
        const choices = [...plan.choices, candidate];
        return { choices, cost: footprint(choices).cost };
      })).sort((first, second) => first.cost - second.cost).slice(0, 32);
    }
    const chosen = plans[0].choices;
    const result = footprint(chosen);
    laid.forEach(({ index }, position) => {
      out2.keys[index] = chosen[position].layout;
    });
    const anchor = preferences.align === "center" ? "middle" : preferences.align === "end" ? "end" : "start";
    const beside = (side) => result.edges.filter((edge) => edge.orient === side).reduce((sum, edge) => sum + edge.width + edgeGap, 0);
    const room = {
      start: (preferences.besidePlot?.start ?? 0) + beside("left"),
      end: (preferences.besidePlot?.end ?? 0) + beside("right")
    };
    const anchorOf = (edge) => {
      if (edge.orient !== "top" && edge.orient !== "bottom") return anchor;
      const width = edge.width;
      const fits = (at) => at === "start" ? width <= plot.width + room.end : at === "end" ? width <= plot.width + room.start : (width - plot.width) / 2 <= Math.min(room.start, room.end);
      if (fits(anchor)) return anchor;
      const nearest = anchor === "middle" ? room.start >= room.end ? ["end", "start"] : ["start", "end"] : anchor === "start" ? ["middle", "end"] : ["middle", "start"];
      const moved = nearest.find(fits);
      if (!moved) return anchor;
      out2.notes.push({ path: "legend.align", code: "keys-anchor-moved", message: `the ${edge.orient} keys stand at the ${moved === "middle" ? "middle" : moved} of the plot rather than its ${anchor === "middle" ? "middle" : anchor} \u2014 ${Math.round(width)}px wide, they would run past the drawing's edge there` });
      return moved;
    };
    out2.edges = result.edges.map((edge) => ({ edge, anchor: anchorOf(edge) })).filter(({ edge, anchor: at }) => edge.count >= 2 || at !== "start").map(({ edge, anchor: anchor2 }) => {
      const horizontal = edge.orient === "top" || edge.orient === "bottom";
      const entries = chosen.filter((choice) => choice.orient === edge.orient);
      const along = horizontal === (edge.direction === "horizontal");
      let gap = edge.direction === "vertical" && horizontal ? stackGap : edgeGap;
      if (preferences.align === "spread" && along && entries.length > 1) {
        const extent = (entry) => horizontal ? entry.width : entry.height;
        const spare = (horizontal ? plot.width : plot.height) - entries.reduce((sum, entry) => sum + extent(entry), 0);
        if (spare / (entries.length - 1) >= Math.min(...entries.map(extent))) gap = Math.floor(spare / (entries.length - 1));
        else out2.notes.push({ path: "legend.align", code: "keys-spread-gave-way", message: `the ${edge.orient} keys stand together rather than spread \u2014 ${Math.round(spare)}px left over would read as a gap, not a spread` });
      }
      if (edge.asked && edge.direction === "horizontal" !== (edge.asked === "row")) {
        out2.notes.push({ path: "legend.arrangement", code: "keys-arrangement-gave-way", message: `the ${edge.orient} keys ${edge.direction === "horizontal" ? "share a row" : "stand one above another"} \u2014 ${edge.asked === "row" ? "a row" : "a stack"} would overflow the edge` });
      }
      return { orient: edge.orient, direction: edge.direction, anchor: anchor2, gap };
    });
    out2.footprint = result.edges.map(({ orient, width, height }) => ({ orient, width, height }));
    out2.overflow = result.edges.filter((edge) => edge.overflow > 0).map(({ orient, width, height, widthBudget, heightBudget }) => ({ orient, width, height, widthBudget, heightBudget }));
    return out2;
  };
  const narrowed = layoutAt(narrowBudget);
  if (narrowBudget >= nativeSide) return narrowed;
  const cuts = (planned) => planned.keys.some((key) => key && (key.orient === "left" || key.orient === "right") && key.labelsFitted && key.labelLimit > 0);
  if (!cuts(narrowed)) return narrowed;
  const native = layoutAt(nativeSide);
  return cuts(native) ? narrowed : native;
}

// src/core/axis-title-fit.ts
function fitAxisTitle(text, length, widthOf, fontSize) {
  if (!text || !(length > 0) || widthOf(text) <= length) return void 0;
  const lineHeight2 = Math.ceil(fontSize * 1.2);
  const limit = Math.floor(length);
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length < 2) return { lines: [text], limit, cut: true, lineHeight: lineHeight2 };
  let best;
  for (let at2 = 1; at2 < words.length; at2++) {
    const lines = [words.slice(0, at2).join(" "), words.slice(at2).join(" ")];
    const widest = Math.max(...lines.map(widthOf));
    if (!best || widest < best.widest) best = { lines, widest };
  }
  if (best && best.widest <= length) return { lines: best.lines, limit, cut: false, lineHeight: lineHeight2 };
  let at = 1;
  while (at < words.length && widthOf(words.slice(0, at + 1).join(" ")) <= length) at++;
  return { lines: [words.slice(0, at).join(" "), words.slice(at).join(" ")], limit, cut: true, lineHeight: lineHeight2 };
}

// src/core/canvas-fit.ts
var CANVAS_FIT_LIMITS = {
  /** The share of the drawing's height or width the title, keys and chrome may take. */
  around: 0.4,
  /** The share of a panel's width (or height) its own y (or x) labels may take. */
  labels: 0.4,
  /** How far what is around the chart may stretch the drawing's shape from its box's, as a ratio of aspects. */
  stretch: 1.35,
  /** The smallest a host may shrink the drawing to enter its box before its text is too small. */
  scale: 0.7,
  /** A single plot's widest shape either way, as a ratio of its sides. */
  aspect: 3,
  /** A single plot's shortest side, as shown. */
  minSide: 100
};
var HEADER_ALLOWANCE = 0.25;
var SIDE_ALLOWANCE = 0.33;
var DRAWING_PADDING = 5;
var over = (value, limit) => Math.max(0, value - limit) / limit;
var ESTIMATE_MARGIN = 0.95;
function judgeCanvas(parts) {
  const { panel, grid, reserve, chrome, box, base } = parts;
  const p = reserve.parts;
  const gridWidth = panel.width * grid.columns + grid.gapX * Math.max(0, grid.columns - 1);
  const gridHeight = panel.height * grid.rows + grid.gapY * Math.max(0, grid.rows - 1);
  const yOwn = (p.yLabels ?? 0) + (p.yTitle ?? 0);
  const xOwn = (p.xLabels ?? 0) + (p.xTitle ?? 0);
  const area = { width: gridWidth + yOwn, height: gridHeight + xOwn + (p.facetHeaders ?? 0) };
  const aroundWidth = (p.sideKeys ?? 0) + (p.seriesEnd ?? 0);
  const aroundHeight = (p.title ?? 0) + (p.flatYTitle ?? 0) + (p.stackedKeys ?? 0) + chrome.top + chrome.bottom;
  const pad2 = parts.padding ?? DRAWING_PADDING;
  const drawing = {
    width: Math.max(area.width + aroundWidth, parts.keyRowWidth ?? 0) + 2 * pad2,
    height: area.height + aroundHeight + 2 * pad2
  };
  const around = { height: aroundHeight / (drawing.height || 1), width: aroundWidth / (drawing.width || 1) };
  const labels = { y: yOwn / (yOwn + panel.width || 1), x: xOwn / (xOwn + panel.height || 1) };
  const aspect = (size) => size.width / (size.height || 1);
  const shaped = {
    width: area.width + Math.max(0, drawing.width - area.width - area.width * SIDE_ALLOWANCE),
    height: area.height + Math.max(0, drawing.height - area.height - area.height * HEADER_ALLOWANCE)
  };
  const own = parts.ownPanel ? { width: area.width - (parts.panel.width - parts.ownPanel.width), height: area.height - (parts.panel.height - parts.ownPanel.height) } : area;
  const stretch = aspect(shaped) / aspect(box) / (aspect(own) / aspect(base));
  const scale = Math.min(1, box.width / (drawing.width || 1), box.height / (drawing.height || 1));
  const strict = (limit) => limit * ESTIMATE_MARGIN;
  const L = {
    ...CANVAS_FIT_LIMITS,
    around: strict(CANVAS_FIT_LIMITS.around),
    labels: strict(CANVAS_FIT_LIMITS.labels),
    // The shape, padding and all, runs a little large in the estimate already: no margin on it.
    stretch: CANVAS_FIT_LIMITS.stretch,
    scale: CANVAS_FIT_LIMITS.scale / ESTIMATE_MARGIN
  };
  const flags = [];
  const kinds = [];
  let excess = 0;
  const flag = (amount, message, kind) => {
    if (amount <= 0) return;
    flags.push(message);
    kinds.push(kind);
    excess += amount;
  };
  const pct = (value) => `${Math.round(value * 100)}%`;
  const held = scale < 0.99;
  const designedHeight = (parts.designed ?? panel).height;
  const takenFromPlot = (designedHeight - panel.height) / (designedHeight || 1) > TAKEN_FROM_PLOT;
  flag(held || takenFromPlot ? over(around.height, L.around) : 0, `title, keys and chrome take ${pct(around.height)} of the height`, "height");
  flag(held ? over(around.width, L.around) : 0, `keys take ${pct(around.width)} of the width`, "width");
  flag(over(labels.y, L.labels), `y labels take ${pct(labels.y)} of the plot's width`, "yLabels");
  flag(over(labels.x, L.labels), `x labels take ${pct(labels.x)} of the plot's height`, "xLabels");
  const stretchExcess = held ? Math.max(0, Math.abs(Math.log(stretch)) - Math.log(L.stretch)) / Math.log(L.stretch) : 0;
  flag(stretchExcess, `what is around the chart stretches the drawing ${(stretch > 1 ? stretch : 1 / stretch).toFixed(1)}\xD7 ${stretch > 1 ? "wider" : "taller"} than its box`, stretch > 1 ? "wider" : "taller");
  flag(scale < L.scale ? 3 * (L.scale - scale) / L.scale : 0, `shrunk to ${pct(scale)} to fit its box`, "shrunk");
  const plotFlags = [];
  let plotExcess = 0;
  if (grid.columns * grid.rows === 1) {
    const plotFlag = (amount, message) => {
      if (amount <= 0) return;
      plotFlags.push(message);
      plotExcess += amount;
    };
    const shape = aspect(panel);
    plotFlag(Math.max(0, Math.abs(Math.log(shape)) - Math.log(L.aspect)) / Math.log(L.aspect), `the plot is ${shape.toFixed(1)}:1`);
    const side = Math.min(panel.width, panel.height) * scale;
    plotFlag(side < L.minSide ? (L.minSide - side) / L.minSide : 0, `the plot's short side shows ${Math.round(side)}px`);
  }
  return { drawing, panel, designed: parts.designed ?? panel, area, around, labels, stretch, scale, flags, kinds, plotFlags, plotExcess, excess: excess + plotExcess };
}
var TAKEN_FROM_PLOT = 0.1;
function plotFitScale(estimate, box) {
  const furniture = { width: estimate.drawing.width - estimate.area.width, height: estimate.drawing.height - estimate.area.height };
  if (estimate.scale >= CANVAS_FIT_LIMITS.scale / ESTIMATE_MARGIN) return void 0;
  const scale = Math.min((box.width - furniture.width) / estimate.area.width, (box.height - furniture.height) / estimate.area.height) * 0.93;
  if (!(scale < 0.97)) return void 0;
  return Math.max(PLOT_SCALE_FLOOR, Math.round(scale * 100) / 100);
}
var PLOT_SCALE_FLOOR = 0.6;

// src/core/line-density.ts
var MIN_DENSE_STROKE = 0.75;
var LINE_INK_SHARE = 0.15;
function panelCount(rows, channelSemantics) {
  let panels = 1;
  for (const field of [channelSemantics.column?.field, channelSemantics.row?.field]) {
    if (field) panels *= Math.max(1, new Set(rows.map((row) => row?.[field]).filter((value) => value != null)).size);
  }
  return panels;
}
function linesPerPanel(rows, channelSemantics) {
  const seriesFields = ["color", "detail", "strokeDash"].map((channel) => channelSemantics[channel]?.field).filter((field) => !!field);
  if (seriesFields.length === 0 || rows.length === 0) return 0;
  const facetFields = [channelSemantics.column?.field, channelSemantics.row?.field].filter((field) => !!field);
  const keys = new Set(rows.map((row) => [...facetFields, ...seriesFields].map((field) => String(row?.[field])).join("\0")));
  return keys.size / panelCount(rows, channelSemantics);
}
function denseLineWidth(base, lines, plotHeight) {
  if (!(plotHeight > 0) || !(lines > 0)) return void 0;
  const width = Math.max(MIN_DENSE_STROKE, Math.min(base, Math.round(plotHeight * LINE_INK_SHARE / lines * 100) / 100));
  return width < base ? width : void 0;
}

// src/core/plan.ts
var IDENTIFIER_TYPES = /* @__PURE__ */ new Set(["ID", "ZipCode"]);
function xLabelOverflow(theme, chartProperties, channelSemantics) {
  const semanticType = channelSemantics.x?.semanticAnnotation?.semanticType;
  const sequence = sequenceKindOf(semanticType);
  const code = IDENTIFIER_TYPES.has(semanticType);
  const overflow = resolveLabelOverflow(theme?.labels, chartProperties?.labelOverflow, sequence ? "sequence" : "names").filter((step) => !(channelSemantics.x?.snapshots && step === "skip"));
  return {
    overflow,
    ...sequence ? { sequence } : {},
    ...code ? { noLimit: true } : {},
    ...code ? { codes: true } : {},
    ...IDENTIFIER_TYPES.has(channelSemantics.y?.semanticAnnotation?.semanticType) ? { rowCodes: true } : {},
    ...{}
  };
}

// src/core/backend-frame.ts
var BACKEND_MIN_LABEL_SIZE = 10;
function lineHeightOf(style) {
  return Math.ceil(style.fontSize * (style.lineHeight ?? 1.2));
}
function planTitleBlock(text, width, style, measure2 = tableTextMeasurer) {
  const headlineText = text.title?.trim();
  const deckText = text.subtitle?.trim();
  if (!headlineText && !deckText) return void 0;
  const titleMeasure = (s) => (value, fontSize) => measure2(fontSize, s.font ?? "sans-serif", s.fontWeight ?? "normal", "normal")(value);
  const block = Math.max(40, width);
  let headline;
  if (headlineText) {
    const m = titleMeasure(style.headline);
    const fit = fitHeadlineText(headlineText, block, style.headline.fontSize, style.deck.fontSize, m);
    const fontSize = fit?.fontSize ?? style.headline.fontSize;
    const lines = fit?.lines ?? [headlineText];
    headline = {
      text: headlineText,
      lines,
      fontSize,
      lineHeight: lineHeightOf({ ...style.headline, fontSize }),
      width: Math.max(...lines.map((line) => m(line, fontSize)))
    };
  }
  let deck;
  if (deckText) {
    const m = titleMeasure(style.deck);
    const fit = fitDeckText(deckText, block, style.deck.fontSize, m);
    const lines = fit?.lines ?? [deckText];
    deck = {
      text: deckText,
      lines,
      fontSize: style.deck.fontSize,
      lineHeight: lineHeightOf(style.deck),
      width: Math.max(...lines.map((line) => m(line, style.deck.fontSize)))
    };
  }
  const linesHeight = (part) => part ? part.lines.length * part.lineHeight : 0;
  const height = linesHeight(headline) + linesHeight(deck) + (headline && deck ? style.gap : 0);
  const broken = (headline ? (headline.lines.length - 1) * headline.lineHeight : 0) + (deck ? (deck.lines.length - 1) * deck.lineHeight : 0);
  return { headline, deck, height, broken, width: Math.max(headline?.width ?? 0, deck?.width ?? 0) };
}
function widestLine(labels, widthOf) {
  let widest = 0;
  for (const label of labels) {
    for (const line of typeof label === "string" ? [label] : label) widest = Math.max(widest, widthOf(line));
  }
  return widest;
}
function cutToWidth(text, limit, widthOf) {
  if (!(limit && limit > 0) || widthOf(text) <= limit) return text;
  let end = text.length;
  while (end > 1 && widthOf(`${text.slice(0, end).trimEnd()}\u2026`) > limit) end--;
  return `${text.slice(0, end).trimEnd()}\u2026`;
}
function onNiceTick(value, domain, count = 5) {
  const [lo, hi] = domain;
  if (!Number.isFinite(value) || !(hi > lo)) return true;
  return niceTicks(lo, hi, count).some((tick) => Math.abs(tick - value) <= (hi - lo) * 1e-9);
}
function valueTickLabels(domain, count = 5, format2 = (v) => v.toLocaleString("en-US")) {
  const [lo, hi] = domain;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (lo === hi) return [format2(lo)];
  return niceTicks(Math.min(lo, hi), Math.max(lo, hi), count).map(format2);
}
function fitBox(baseSize, canvasSize, maxStretch) {
  const stretch = maxStretch ?? DEFAULT_MAX_STRETCH;
  return canvasSize ?? { width: baseSize.width * stretch, height: baseSize.height * stretch };
}
function judgeBackendCanvas(chrome) {
  const parts = chrome.parts;
  const width = Object.entries(parts).filter(([k]) => ["yLabels", "yTitle", "sideKeys", "seriesEnd"].includes(k)).reduce((s, [, v]) => s + (v ?? 0), 0);
  const height = Object.entries(parts).filter(([k]) => !["yLabels", "yTitle", "sideKeys", "seriesEnd"].includes(k)).reduce((s, [, v]) => s + (v ?? 0), 0);
  const estimate = judgeCanvas({
    panel: chrome.plot,
    ...chrome.designed ? { designed: chrome.designed } : {},
    grid: chrome.grid ?? { columns: 1, rows: 1, gapX: 0, gapY: 0 },
    reserve: { width, height, parts },
    chrome: chrome.chrome ?? { top: 0, bottom: 0 },
    ...chrome.keyRowWidth !== void 0 ? { keyRowWidth: chrome.keyRowWidth } : {},
    ...chrome.padding !== void 0 ? { padding: chrome.padding } : {},
    box: chrome.box,
    base: chrome.base
  });
  return { estimate, plotScale: plotFitScale(estimate, chrome.box), stackedKeys: (parts.stackedKeys ?? 0) > 0, box: chrome.box };
}
function canvasFitSummary(estimate, box, treatment) {
  return {
    box,
    drawing: { width: Math.round(estimate.drawing.width), height: Math.round(estimate.drawing.height) },
    scale: Math.round(estimate.scale * 100) / 100,
    flags: estimate.flags,
    excess: Math.round(estimate.excess * 1e3) / 1e3,
    ...treatment?.plotScale !== void 0 ? { plotScale: treatment.plotScale } : {},
    ...treatment ? { treatment } : {}
  };
}
var REFIT_MARGIN = 0.01;
var SHORT_KEY_NAMES = { keyWidth: 120 };
var SHORTER_KEY_NAMES = { keyWidth: 90 };
var HOST_SCALE_TARGET = 0.9;
function fitScore(fit) {
  return fit.excess + Math.max(0, HOST_SCALE_TARGET - fit.scale);
}
function refitCanvas(judged, options, reassemble) {
  if (options?.canvasFit === "off" || options?.canvasTreatment) return void 0;
  const kinds = judged.estimate.kinds;
  const held = judged.estimate.scale < HOST_SCALE_TARGET;
  const keysWide = held || kinds.includes("width") || kinds.includes("wider") || kinds.includes("shrunk");
  const keysTall = judged.stackedKeys === true && (held || kinds.includes("height") || kinds.includes("taller") || kinds.includes("shrunk"));
  const plotScale = judged.plotScale;
  const smaller = { keyScale: KEY_SCALE };
  const smallType = { keyScale: KEY_SCALE, labelScale: LABEL_SCALE, titleScale: TITLE_SCALE };
  const short = { names: SHORT_KEY_NAMES };
  const tries = [
    ...keysTall ? [{ keys: "side" }, { keys: "side", ...short }, { keys: "side", ...short, ...smaller }] : [],
    ...keysTall && held ? [{ keys: "side", names: SHORTER_KEY_NAMES, ...smaller }] : [],
    ...keysWide ? [short, { keys: "bottom" }, { ...short, ...smaller }] : [],
    ...keysWide && held ? [{ names: SHORTER_KEY_NAMES, ...smaller }, { ...short, ...smallType }] : [],
    ...keysTall && held ? [{ keys: "side", ...short, ...smallType }] : [],
    ...held && !keysWide && !keysTall ? [smallType] : [],
    ...plotScale ? [{ plotScale }] : [],
    ...keysWide && plotScale ? [{ ...short, plotScale }, { keys: "bottom", plotScale }] : []
  ];
  let best;
  let bestScore = fitScore(judged.estimate) - REFIT_MARGIN;
  for (const treatment of tries) {
    const refit = reassemble(treatment);
    const fit = refit?._canvasFit;
    if (!fit) continue;
    if (treatment.labelScale && fit.scale * treatment.labelScale < judged.estimate.scale - 5e-3) continue;
    const score = fitScore(fit) + treatmentPenalty(treatment);
    if (score < bestScore) {
      best = refit;
      bestScore = score - REFIT_MARGIN;
    }
  }
  return best;
}
var KEY_SCALE = 0.84;
var LABEL_SCALE = 0.84;
var TITLE_SCALE = 0.85;
function treatmentPenalty(t) {
  return (t.keyScale ? 0.01 : 0) + (t.labelScale ? 0.03 : 0) + (t.titleScale ? 0.02 : 0) + (t.names ? t.names.keyWidth < 120 ? 0.02 : 5e-3 : 0) + (t.plotScale ? 0.02 : 0);
}
function treatedKeyFontSize(size, options) {
  const scale = options?.canvasTreatment?.keyScale;
  return scale ? Math.max(BACKEND_MIN_LABEL_SIZE, Math.min(size, Math.round(size * scale * 2) / 2)) : size;
}
function withTreatment(input, treatment, box) {
  const base = resolveBaseSize(input.chart_spec.baseSize, input.chart_spec.canvasSize);
  const scale = treatment.plotScale;
  return {
    ...input,
    chart_spec: {
      ...input.chart_spec,
      ...scale ? { baseSize: { width: Math.round(base.width * scale), height: Math.round(base.height * scale) }, canvasSize: box } : {}
    },
    options: { ...input.options ?? {}, canvasTreatment: { ...input.options?.canvasTreatment ?? {}, ...treatment } }
  };
}
function treatedKeyEdge(options) {
  const keys = options?.canvasTreatment?.keys;
  return keys === "top" || keys === "bottom" ? keys : void 0;
}
function treatedKeySide(options, key) {
  const names = options?.canvasTreatment?.names?.keyWidth;
  return names === void 0 ? void 0 : names + key.lead + 2 * key.pad.x;
}

// src/core/backend-profile.ts
function keyBlock(g, input) {
  const { entries, fontSize, widthOf } = input;
  const titleLines = input.title?.lines ?? [];
  const titleWidth = titleLines.length ? Math.max(...titleLines.map(input.title.widthOf)) : 0;
  const titleHeight = titleLines.length ? titleLines.length * input.title.fontSize * g.lineHeight : 0;
  const heights = entries.map((lines) => g.entryHeight(fontSize, Math.max(1, lines.length)));
  const widths = entries.map((lines) => g.lead + Math.max(0, ...lines.map(widthOf)));
  const inset = g.pad.y + Math.max(0, (g.entryHeight(fontSize, 1) - fontSize * g.lineHeight) / 2);
  if (!entries.length) return { width: 0, height: 0, rows: 0, inset: 0 };
  if (input.orientation === "vertical") {
    const body = heights.reduce((sum, h) => sum + h, 0) + g.rowGap * (entries.length - 1);
    const top2 = titleHeight ? titleHeight + g.titleRowGap : 0;
    return {
      width: Math.ceil(Math.max(Math.max(...widths), titleWidth) + 2 * g.pad.x),
      height: Math.ceil(top2 + body + 2 * g.pad.y),
      rows: entries.length,
      inset: titleHeight ? g.pad.y : inset
    };
  }
  const inlineTitle = titleHeight > 0 && g.rowTitle === "start" ? titleWidth + g.titleGap : 0;
  const room = Math.max(1, (input.room ?? Infinity) - 2 * g.pad.x);
  const oneRow = widths.reduce((sum, w) => sum + w, 0) + g.gap * (widths.length - 1) < room;
  const widest = Math.max(...widths);
  const cell = (w) => g.wrap === "grid" && !oneRow ? widest : w;
  let rows = 1;
  let run = 0;
  let rowHeight = 0;
  let height = 0;
  let rowWidth = 0;
  entries.forEach((_, i) => {
    const w = cell(widths[i]);
    const end = run + (run > 0 ? g.gap : 0) + w;
    const past = g.breakAt === "reach" ? end >= room : g.breakAt === "overWithGap" ? end + g.gap > room : end > room;
    if (run > 0 && past) {
      rows += 1;
      height += rowHeight + g.rowGap;
      rowWidth = Math.max(rowWidth, run);
      run = w;
      rowHeight = heights[i];
    } else {
      run = end;
      rowHeight = Math.max(rowHeight, heights[i]);
    }
  });
  height += rowHeight;
  rowWidth = Math.max(rowWidth, run);
  const top = titleHeight && g.rowTitle === "top" ? titleHeight + g.titleRowGap : 0;
  return {
    width: Math.ceil(inlineTitle + rowWidth + 2 * g.pad.x),
    height: Math.ceil(Math.max(height, g.rowTitle === "start" ? titleHeight : 0) + top + 2 * g.pad.y),
    rows,
    inset: top ? g.pad.y : inset
  };
}
function keyPlanStyle(g) {
  return {
    symbolSize: g.symbol * g.symbol,
    labelOffset: Math.max(0, g.lead - g.symbol),
    padding: Math.max(g.pad.x, g.pad.y),
    rowPadding: g.rowGap,
    columnPadding: g.gap,
    ...g.sideColumns === "one" ? { columns: 1 } : {}
  };
}

// src/echarts/profile.ts
var ECHARTS_FONT = "sans-serif";
var lineHeight = 1.2;
var ECHARTS_PROFILE = {
  key: {
    lead: 25 + 5,
    symbol: 14,
    gap: 10,
    rowGap: 10,
    pad: { x: 5, y: 5 },
    entryHeight: (fontSize, lines) => Math.max(14, lines * Math.ceil(fontSize * lineHeight)),
    lineHeight,
    wrap: "flow",
    breakAt: "over",
    rowTitle: "top",
    titleGap: 0,
    titleRowGap: 6,
    sideColumns: "one"
  }};
function ecKeyGeometry(component, defaults = {}) {
  const base = ECHARTS_PROFILE.key;
  const itemWidth = component?.itemWidth ?? defaults.itemWidth ?? 25;
  const itemHeight = component?.itemHeight ?? defaults.itemHeight ?? 14;
  const gap = component?.itemGap ?? base.gap;
  const pad2 = typeof component?.padding === "number" ? component.padding : base.pad.x;
  return {
    ...base,
    lead: itemWidth + 5,
    symbol: itemHeight,
    gap,
    rowGap: gap,
    pad: { x: pad2, y: pad2 },
    entryHeight: (fontSize, lines) => Math.max(itemHeight, lines * Math.ceil(fontSize * lineHeight))
  };
}

// src/echarts/frame.ts
var FONT = ECHARTS_FONT;
var EC_TITLE = {
  top: 15,
  padding: 5,
  gap: 10,
  headline: { fontSize: 18, fontWeight: "bold", font: FONT, lineHeight: 1.25 },
  deck: { fontSize: 12, fontWeight: "normal", font: FONT, lineHeight: 1.4 }
};
var CANVAS_BUFFER = 16;
var LABEL_MARGIN = 8;
var NAME_GAP = 10;
var KEY_GAP = 12;
var TITLE_GAP = 8;
var FOOT_LEGEND = 30;
var FOOT_GAP = 8;
var TOP_OVERHANG = 12;
var EC_LINE_WIDTH = 2;
var measure = (fontSize, weight = "normal") => tableTextMeasurer(fontSize, FONT, weight);
var one = (value) => Array.isArray(value) ? value.length === 1 ? value[0] : void 0 : value;
function singleGrid(option) {
  const grid = one(option.grid);
  const xAxis = one(option.xAxis);
  const yAxis = one(option.yAxis);
  if (!grid || !xAxis || !yAxis || typeof grid.left !== "number" || typeof grid.top !== "number") return void 0;
  if (!(Number(option._width) > 0) || !(Number(option._height) > 0)) return void 0;
  return { grid, xAxis, yAxis };
}
function bandTexts(axis, sizing) {
  if (axis?.type !== "category" || !Array.isArray(axis.data)) return void 0;
  const values = axis.data.map((d) => String(d?.value ?? d));
  const fontSize = axis.axisLabel?.fontSize ?? sizing.fontSize ?? 12;
  const widthOf = measure(fontSize);
  const lines = new Map((sizing.labelValues ?? []).map((value, index) => [value, sizing.labelLines?.[index]]));
  const limit = sizing.labelLimit > 0 && sizing.labelForm !== "calendar" ? sizing.labelLimit : void 0;
  return new Map(values.map((value) => [value, (lines.get(value) ?? [value]).map((line) => cutToWidth(line, limit, widthOf))]));
}
function realizeBandLabels(axis, channel, sizing) {
  const texts = bandTexts(axis, sizing);
  if (!texts) return;
  const label = axis.axisLabel ?? (axis.axisLabel = {});
  if (sizing.fontSize) label.fontSize = sizing.fontSize;
  const fontSize = label.fontSize ?? 12;
  const byValue = new Map([...texts].map(([value, lines]) => [value, lines.join("\n")]));
  if ([...byValue].some(([value, text]) => text !== value)) label.formatter = (value) => byValue.get(String(value)) ?? String(value);
  else if (label.formatter?._flintCalendar) delete label.formatter;
  if ([...texts.values()].some((lines) => lines.length > 1)) label.lineHeight = sizing.labelLineHeight ?? Math.ceil(fontSize * 1.2);
  if (channel === "x") {
    label.rotate = sizing.labelAngle ? -sizing.labelAngle : 0;
    const shown = sizing.overflow?.step === "skip" && sizing.overflow.shown ? new Set(sizing.overflow.shown) : void 0;
    label.interval = shown ? (_index, value) => shown.has(String(value)) : 0;
  } else {
    label.interval = 0;
  }
}
function seriesExtent(option, valueChannel) {
  const index = valueChannel === "y" ? 1 : 0;
  const stacks = /* @__PURE__ */ new Map();
  let lo = Infinity;
  let hi = -Infinity;
  const read = (item) => {
    const raw = item && typeof item === "object" && !Array.isArray(item) ? item.value : item;
    const value = Array.isArray(raw) ? raw[index] : raw;
    return typeof value === "number" && Number.isFinite(value) ? value : void 0;
  };
  for (const series of option.series ?? []) {
    if (!Array.isArray(series?.data)) continue;
    const stack = series.stack != null ? String(series.stack) : void 0;
    series.data.forEach((item, i) => {
      const value = read(item);
      if (value === void 0) return;
      if (stack) {
        const sums = stacks.get(stack) ?? [];
        sums[i] = (sums[i] ?? 0) + value;
        stacks.set(stack, sums);
      }
      lo = Math.min(lo, value);
      hi = Math.max(hi, value);
    });
  }
  for (const sums of stacks.values()) for (const sum of sums) if (sum !== void 0) {
    lo = Math.min(lo, sum);
    hi = Math.max(hi, sum);
  }
  return Number.isFinite(lo) && Number.isFinite(hi) ? [lo, hi] : void 0;
}
function printedLabels(option, axis, channel) {
  if (axis?.axisLabel?.show === false) return [];
  const formatter = axis.axisLabel?.formatter;
  const format2 = (value) => {
    if (typeof formatter === "function") {
      try {
        return String(formatter(value, 0));
      } catch {
        return String(value);
      }
    }
    if (typeof formatter === "string") return formatter.replace("{value}", String(value));
    return typeof value === "number" ? value.toLocaleString("en-US") : String(value);
  };
  if (axis.type === "category" && Array.isArray(axis.data)) {
    return axis.data.map((d) => format2(d?.value ?? d).split("\n"));
  }
  if (axis.type === "value" || axis.type === "log" || axis.type == null) {
    const extent = seriesExtent(option, channel);
    if (!extent) return [];
    const min = typeof axis.min === "number" ? axis.min : axis.scale ? extent[0] : Math.min(0, extent[0]);
    const max = typeof axis.max === "number" ? axis.max : axis.scale ? extent[1] : Math.max(0, extent[1]);
    return valueTickLabels([min, max], 5, (value) => format2(value)).map((text) => text.split("\n"));
  }
  return [["Sep 2024"]];
}
function labelRun(option, axis, channel) {
  const labels = printedLabels(option, axis, channel);
  if (!labels.length) return 0;
  const fontSize = axis.axisLabel?.fontSize ?? 12;
  const widest = widestLine(labels, measure(fontSize));
  const lineHeight2 = axis.axisLabel?.lineHeight ?? Math.ceil(fontSize * 1.2);
  const lines = Math.max(1, ...labels.map((l) => l.length));
  const angle = Math.abs(Number(axis.axisLabel?.rotate) || 0) * Math.PI / 180;
  if (channel === "y") return angle ? Math.ceil(Math.cos(angle) * widest + Math.sin(angle) * fontSize) : Math.ceil(widest);
  return angle ? Math.ceil(Math.sin(angle) * widest + Math.cos(angle) * lines * lineHeight2) : lines * lineHeight2;
}
function hidePaddedEnds(axis) {
  if (axis?.type !== "value" || typeof axis.min !== "number" || typeof axis.max !== "number") return;
  const domain = [axis.min, axis.max];
  const label = axis.axisLabel ?? (axis.axisLabel = {});
  if (!onNiceTick(axis.min, domain) && label.showMinLabel == null) label.showMinLabel = false;
  if (!onNiceTick(axis.max, domain) && label.showMaxLabel == null) label.showMaxLabel = false;
}
function flattenTimeLabels(axis, plotWidth) {
  if (axis?.type !== "time" || Math.abs(Number(axis.axisLabel?.rotate) || 0) !== 90) return;
  const formatter = axis.axisLabel?.formatter;
  const sampleAt = Date.UTC(2024, 8, 28, 12, 0, 0);
  let sample = "Sep 2024";
  if (typeof formatter === "function") {
    try {
      sample = String(formatter(sampleAt, 0));
    } catch {
    }
  } else if (typeof formatter === "string") {
    const parts = { yyyy: "2024", yy: "24", MMMM: "September", MMM: "Sep", MM: "09", M: "9", dd: "28", d: "28", HH: "12", H: "12", hh: "12", mm: "00", ss: "00" };
    sample = formatter.replace(/\{(\w+)\}/g, (_m, key) => parts[key] ?? key);
  }
  const fontSize = axis.axisLabel?.fontSize ?? 12;
  const widest = Math.max(...sample.split("\n").map(measure(fontSize)));
  if (6 * (widest + 12) > plotWidth) return;
  axis.axisLabel.rotate = 0;
  axis.axisLabel.hideOverlap = true;
}
function fitAxisName(axis, length) {
  const name = axis?.name;
  if (typeof name !== "string" || !name.trim()) return 0;
  const fontSize = axis.nameTextStyle?.fontSize ?? 12;
  const widthOf = measure(fontSize);
  const fit = fitAxisTitle(name.replace(/\n/g, " "), length, widthOf, fontSize);
  const lineHeight2 = Math.ceil(fontSize * 1.2);
  if (fit) {
    axis.name = fit.lines.map((line) => cutToWidth(line, fit.limit, widthOf)).join("\n");
    axis.nameTextStyle = { ...axis.nameTextStyle ?? {}, lineHeight: lineHeight2 };
  }
  return (fit?.lines.length ?? name.split("\n").length) * lineHeight2;
}
function legendTitleGraphic(option) {
  const graphics = Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [];
  return graphics.find((g) => g?.id === EC_LEGEND_TITLE_ID);
}
function placeLegend(option, plot, canvasWidth, sideWidth, moveTo) {
  const legend = one(option.legend);
  if (!legend || option.visualMap || legend.orient !== "vertical" || legend.right !== CANVAS_BUFFER) return void 0;
  const names = (Array.isArray(legend.data) ? legend.data : (option.series ?? []).map((s) => s?.name)).map((d) => typeof d === "string" ? d : d?.name).filter((name) => typeof name === "string" && name.length > 0);
  if (!names.length) return void 0;
  const fontSize = legend.textStyle?.fontSize ?? 12;
  const titleGraphic = legendTitleGraphic(option);
  const title = titleGraphic?.style?.text ? String(titleGraphic.style.text) : null;
  const geometry = ecKeyGeometry(legend);
  const itemWidth = legend.itemWidth ?? 25;
  const key = {
    gradient: false,
    labels: names,
    count: names.length,
    title,
    style: {
      labelFont: FONT,
      labelFontSize: fontSize,
      labelFontWeight: "normal",
      labelFontStyle: "normal",
      titleFont: FONT,
      titleFontSize: titleGraphic?.style?.fontSize ?? fontSize,
      titleFontWeight: "bold",
      titleFontStyle: "normal",
      // ECharts sets a key beside the plot in one column, scrolling past its height.
      ...keyPlanStyle(geometry),
      titlePadding: 6
    },
    orient: "right",
    edges: ["right", "bottom"],
    fixed: false,
    fixedTitle: false,
    ...moveTo ? { moveTo: "bottom" } : {}
  };
  const laid = planKeyLayout([key], plot, tableTextMeasurer, sideWidth !== void 0 ? { sideWidth } : {}).keys[0];
  if (!laid) return void 0;
  const cutAt = laid.labelLimit > 0 ? laid.labelLimit : void 0;
  const lineWidthOf = measure(fontSize);
  const lines = new Map(names.map((name, index) => [name, (laid.labelLines?.[index] ?? [name]).map((line) => cutToWidth(line, cutAt, lineWidthOf))]));
  const multiline = [...lines.values()].some((l) => l.length > 1);
  const lineHeight2 = Math.ceil(fontSize * 1.2);
  const titleLines = laid.titleLines ?? (title ? [title] : []);
  const titleFontSize = key.style.titleFontSize;
  const titleHeight = titleLines.length ? titleLines.length * Math.ceil(titleFontSize * 1.2) + 6 : 0;
  const formatter = (name) => (lines.get(name) ?? [name]).join("\n");
  const textStyle = { ...legend.textStyle ?? {}, ...multiline ? { lineHeight: lineHeight2 } : {} };
  if (laid.orient === "bottom" || laid.orient === "top") {
    const { right: _r, top: _t, formatter: _f, ...rest } = legend;
    const widthOf2 = measure(fontSize);
    const room = canvasWidth - 2 * CANVAS_BUFFER - itemWidth - 5;
    const cut = new Map(names.map((name) => [name, cutToWidth(name, room, widthOf2)]));
    option.legend = {
      ...rest,
      orient: "horizontal",
      left: "center",
      bottom: CANVAS_BUFFER,
      textStyle: legend.textStyle ?? {},
      ...[...cut].some(([name, text]) => text !== name) ? { formatter: (name) => cut.get(name) ?? name } : {}
    };
    if (titleGraphic) {
      const { right: _gr, top: _gt, ...g } = titleGraphic;
      Object.assign(titleGraphic, { ...g, left: "center", style: { ...g.style, text: title, textAlign: "center", width: void 0 } });
      delete titleGraphic.right;
      delete titleGraphic.top;
    }
    return { bottom: footRows(option, option.legend, canvasWidth) };
  }
  const top = typeof legend.top === "number" ? legend.top : 0;
  option.legend = {
    ...legend,
    top: titleHeight ? Math.max(top, 4 + titleHeight) : top,
    formatter,
    textStyle,
    ...laid.height > plot.height + 24 ? { type: "scroll" } : {}
  };
  const widthOf = measure(fontSize);
  const padding = geometry.pad.x;
  const titleWidthOf = measure(titleFontSize, "bold");
  const titleWidth = titleLines.length ? Math.max(...titleLines.map(titleWidthOf)) : 0;
  const box = keyBlock(geometry, {
    entries: names.map((name) => lines.get(name) ?? [name]),
    fontSize,
    widthOf,
    orientation: "vertical",
    ...titleLines.length ? { title: { lines: titleLines, fontSize: titleFontSize, widthOf: titleWidthOf } } : {}
  }).width;
  if (titleGraphic) {
    const { width: _w, ...style } = titleGraphic.style ?? {};
    titleGraphic.style = { ...style, text: titleLines.join("\n"), textAlign: "left", ...titleLines.length > 1 ? { lineHeight: Math.ceil(titleFontSize * 1.2) } : {} };
    titleGraphic.right = CANVAS_BUFFER + Math.max(0, box - padding - Math.ceil(titleWidth));
  }
  return { right: box + KEY_GAP };
}
function footRows(option, legend, canvasWidth) {
  const names = (Array.isArray(legend.data) ? legend.data : (option.series ?? []).map((s) => s?.name)).map((d) => typeof d === "string" ? d : d?.name).filter((name) => typeof name === "string" && name.length > 0);
  if (!names.length) return FOOT_LEGEND;
  const fontSize = legend.textStyle?.fontSize ?? 12;
  const rowsHeight = keyBlock(ecKeyGeometry(legend), {
    entries: names.map((name) => [String(legend.formatter?.(name) ?? name)]),
    fontSize,
    widthOf: measure(fontSize),
    orientation: "horizontal",
    room: canvasWidth
  }).height;
  const bottom = typeof legend.bottom === "number" ? legend.bottom : 0;
  const title = legendTitleGraphic(option);
  if (title) {
    title.bottom = bottom + rowsHeight + 2;
    return bottom + rowsHeight + 2 + Math.ceil((title.style?.fontSize ?? fontSize) * 1.2) + 4;
  }
  return bottom + rowsHeight + 4;
}
function placeSideRamps(option, graphics, plotTop) {
  const maps = (Array.isArray(option.visualMap) ? option.visualMap : option.visualMap ? [option.visualMap] : []).filter((m) => m && m.show !== false && m.type !== "piecewise" && m.orient === "vertical" && typeof m.id === "string" && m.id.startsWith("flint-ramp:") && typeof m.right === "number").sort((a, b) => a.right - b.right);
  let needed = 0;
  let leftmost = -Infinity;
  for (const map2 of maps) {
    const itemWidth = map2.itemWidth ?? 20;
    const title = graphics.find((g) => g?.id === map2.id.replace("flint-ramp:", "flint-ramp-title:"));
    const fontSize = title?.style?.fontSize ?? 11;
    const titleWidth = title?.style?.text ? measure(fontSize, "bold")(String(title.style.text)) : 0;
    let centre = map2.right + itemWidth / 2;
    if (titleWidth && centre - titleWidth / 2 < leftmost + 8) {
      centre = leftmost + 8 + titleWidth / 2;
      map2.right = Math.round(centre - itemWidth / 2);
    }
    const titleRight = Math.max(4, Math.round(centre - titleWidth / 2));
    const titleHeight = titleWidth ? Math.ceil(fontSize * 1.2) + 4 : 0;
    if (title) {
      title.right = titleRight;
      title.top = plotTop;
      title.style = { ...title.style, textAlign: "left" };
    }
    map2.top = plotTop + titleHeight;
    delete map2.bottom;
    leftmost = Math.max(titleRight + titleWidth, map2.right + itemWidth + 6);
    needed = Math.max(needed, Math.ceil(leftmost) + KEY_GAP);
  }
  return needed;
}
function footRamps(option, canvasWidth) {
  const maps = Array.isArray(option.visualMap) ? option.visualMap : option.visualMap ? [option.visualMap] : [];
  let foot = 0;
  for (const map2 of maps) {
    if (!map2 || map2.show === false || map2.orient !== "horizontal" || map2.top != null) continue;
    const bottom = typeof map2.bottom === "number" ? map2.bottom : 0;
    const fontSize = map2.textStyle?.fontSize ?? 12;
    const padding = typeof map2.padding === "number" ? map2.padding : 5;
    let block;
    if (map2.type === "piecewise") {
      const count = Array.isArray(map2.pieces) ? map2.pieces.length : Array.isArray(map2.categories) ? map2.categories.length : map2.splitNumber ?? 5;
      const labels = Array.isArray(map2.pieces) && map2.pieces.every((p) => p?.label) ? map2.pieces.map((p) => String(p.label)) : new Array(count).fill(`${Math.round(Number(map2.min) || 0)} - ${Math.round(Number(map2.max) || 0)}`);
      block = keyBlock(ecKeyGeometry(map2, { itemWidth: 20 }), {
        entries: labels.map((label) => [label]),
        fontSize,
        widthOf: measure(fontSize),
        orientation: "horizontal",
        room: canvasWidth
      }).height - 2 * padding;
    } else {
      block = (map2.itemWidth ?? 20) + (map2.calculable ? Math.ceil(fontSize * 1.2) + 8 : 0);
    }
    foot = Math.max(foot, bottom + block + 2 * padding);
  }
  return foot;
}
function contentTop(option, width, height) {
  const px = (value, of) => typeof value === "number" ? value : typeof value === "string" && value.endsWith("%") ? parseFloat(value) / 100 * of : typeof value === "string" && value.endsWith("px") ? parseFloat(value) : void 0;
  let top = Infinity;
  for (const series of option.series ?? []) {
    if (series?.type !== "pie" && series?.type !== "sunburst") return void 0;
    const [, cy] = Array.isArray(series.center) ? series.center : ["50%", "50%"];
    const radius = Array.isArray(series.radius) ? series.radius[series.radius.length - 1] : series.radius ?? "75%";
    const y = px(cy, height);
    const r = px(radius, Math.min(width, height) / 2);
    if (y === void 0 || r === void 0) return void 0;
    const outside = series.type === "pie" && series.label?.show !== false && series.label?.position !== "inside" && series.label?.position !== "center";
    const labels = outside ? (series.labelLine?.length ?? 15) + 2 * Math.ceil((series.label?.fontSize ?? 12) * 1.2) : 0;
    top = Math.min(top, y - r - labels);
  }
  for (const key of ["legend", "visualMap", "graphic", "polar", "radar", "singleAxis", "calendar", "parallel", "geo"]) {
    const value = option[key];
    for (const component of Array.isArray(value) ? value : value ? [value] : []) {
      if (key === "graphic" || key === "legend" || key === "visualMap") {
        if (component?.show === false) continue;
        if (typeof component?.top === "number") {
          top = Math.min(top, component.top);
          continue;
        }
        if (component?.top == null && component?.bottom != null) continue;
      }
      return void 0;
    }
  }
  return Number.isFinite(top) ? Math.max(0, Math.floor(top)) : void 0;
}
function shiftDown(option, by, oldHeight, oldWidth) {
  if (!(by > 0)) return;
  const shift = (component) => {
    if (!component || typeof component !== "object") return;
    const pct = (value) => typeof value === "string" && value.endsWith("%") ? parseFloat(value) / 100 * oldHeight : void 0;
    if (typeof component.top === "number") component.top += by;
    else if (pct(component.top) !== void 0) component.top = pct(component.top) + by;
    if (pct(component.bottom) !== void 0) component.bottom = pct(component.bottom);
    if (Array.isArray(component.center) && component.center.length === 2) {
      const [cx, cy] = component.center;
      const y = typeof cy === "string" && cy.endsWith("%") ? parseFloat(cy) / 100 * oldHeight : typeof cy === "string" && cy.endsWith("px") ? parseFloat(cy) : typeof cy === "number" ? cy : void 0;
      if (y !== void 0) component.center = [cx, y + by];
      const r = component.radius;
      const toPx = (v) => typeof v === "string" && v.endsWith("%") ? parseFloat(v) / 100 * Math.min(oldWidth, oldHeight) / 2 : v;
      if (Array.isArray(r)) component.radius = r.map(toPx);
      else if (r !== void 0) component.radius = toPx(r);
    }
  };
  for (const key of ["grid", "legend", "visualMap", "graphic", "polar", "radar", "singleAxis", "calendar", "parallel", "series", "geo", "matrix"]) {
    const value = option[key];
    for (const component of Array.isArray(value) ? value : [value]) {
      if (key === "series" && component?.coordinateSystem && component.coordinateSystem !== "none" && component.type !== "pie") continue;
      shift(component);
    }
  }
}
function writeTitle(option, block) {
  const existing = typeof option.title === "object" && !Array.isArray(option.title) ? option.title : {};
  option.title = {
    ...existing,
    left: "center",
    top: EC_TITLE.top,
    itemGap: EC_TITLE.gap,
    ...block.headline ? {
      text: block.headline.lines.join("\n"),
      textStyle: { ...existing.textStyle ?? {}, fontSize: block.headline.fontSize, lineHeight: block.headline.lineHeight }
    } : { text: "" },
    ...block.deck ? {
      subtext: block.deck.lines.join("\n"),
      subtextStyle: { ...existing.subtextStyle ?? {}, fontSize: block.deck.fontSize, lineHeight: block.deck.lineHeight }
    } : {}
  };
}
function thinLines(option, context, plotHeight) {
  const lines = (option.series ?? []).filter((s) => s?.type === "line" && !s._companion);
  if (lines.length < 2) return;
  const width = denseLineWidth(EC_LINE_WIDTH, Math.max(lines.length, linesPerPanel(context.table, context.channelSemantics)), plotHeight);
  if (width === void 0) return;
  for (const series of lines) {
    if (series.lineStyle?.width != null && series.lineStyle.width !== EC_LINE_WIDTH) continue;
    series.lineStyle = { ...series.lineStyle ?? {}, width };
  }
}
function ecFitFrame(option, context, text, planned, titleRoom = 0) {
  const { layout } = context;
  const single = singleGrid(option);
  const parts = {};
  if (!single) {
    const width2 = Number(option._width) || context.canvasSize.width;
    const height = Number(option._height) || context.canvasSize.height;
    const block2 = planTitleBlock(text, width2 - 2 * (EC_TITLE.padding + CANVAS_BUFFER), EC_TITLE);
    let titleRoom2 = 0;
    if (block2) {
      titleRoom2 = EC_TITLE.top + 2 * EC_TITLE.padding + block2.height + TITLE_GAP;
      const top = contentTop(option, width2, height);
      const by = top === void 0 ? titleRoom2 : Math.max(0, titleRoom2 - top);
      shiftDown(option, by, height, width2);
      writeTitle(option, block2);
      option._width = width2;
      option._height = height + by;
      parts.title = titleRoom2;
    }
    const plot2 = { width: width2, height };
    return { plot: plot2, designed: plot2, parts, ...block2 ? { title: block2 } : {} };
  }
  const { grid, xAxis, yAxis } = single;
  const plot = {
    width: Math.max(1, option._width - grid.left - grid.right),
    height: Math.max(1, option._height - grid.top - grid.bottom)
  };
  const designed = { ...plot };
  if (planned.x) realizeBandLabels(xAxis, "x", layout.xLabel);
  if (planned.y) realizeBandLabels(yAxis, "y", layout.yLabel);
  hidePaddedEnds(xAxis);
  hidePaddedEnds(yAxis);
  flattenTimeLabels(xAxis, plot.width);
  const yLeft = yAxis.position !== "right";
  const yRun = labelRun(option, yAxis, "y");
  const yName = fitAxisName(yAxis, plot.height);
  if (yName) yAxis.nameGap = LABEL_MARGIN + yRun + NAME_GAP;
  const yRoom = CANVAS_BUFFER + LABEL_MARGIN + yRun;
  const yNameRoom = yName ? NAME_GAP + yName : 0;
  if (yLeft) grid.left = Math.max(CANVAS_BUFFER + 24, yRoom + yNameRoom);
  const xRun = labelRun(option, xAxis, "x");
  const xName = fitAxisName(xAxis, plot.width);
  if (xName) xAxis.nameGap = LABEL_MARGIN + xRun + NAME_GAP;
  const xRoom = CANVAS_BUFFER + LABEL_MARGIN + xRun;
  const xNameRoom = xName ? NAME_GAP + xName : 0;
  const shown = one(option.legend);
  if (shown && context.assembleOptions?.canvasTreatment?.keyScale) {
    shown.textStyle = { ...shown.textStyle ?? {}, fontSize: treatedKeyFontSize(shown.textStyle?.fontSize ?? 12, context.assembleOptions) };
  }
  const legend = placeLegend(option, plot, plot.width + grid.left + CANVAS_BUFFER + 22, treatedKeySide(context.assembleOptions, ecKeyGeometry(shown ?? {})), treatedKeyEdge(context.assembleOptions));
  if (legend?.right !== void 0) grid.right = CANVAS_BUFFER + legend.right;
  else if (legend?.bottom !== void 0) grid.right = Math.min(grid.right, CANVAS_BUFFER + 22);
  const footKeys = legend?.bottom ?? 0;
  const footLegend = one(option.legend);
  const keptFoot = !legend && footLegend && footLegend.orient === "horizontal" && footLegend.bottom != null ? footRows(option, footLegend, plot.width + grid.left + grid.right) : 0;
  const rampFoot = footRamps(option, plot.width + grid.left + grid.right);
  const foot = Math.max(footKeys, keptFoot, rampFoot);
  grid.bottom = xRoom + xNameRoom + (foot ? Math.max(0, foot + FOOT_GAP - CANVAS_BUFFER) : 0);
  const width = plot.width + grid.left + grid.right;
  const pad2 = 2 * (EC_TITLE.padding + CANVAS_BUFFER);
  const block = planTitleBlock(text, Math.max(width, titleRoom) - pad2, EC_TITLE);
  if (block && block.width + pad2 > width) plot.width += Math.ceil(block.width + pad2 - width);
  if (block) {
    const room = EC_TITLE.top + 2 * EC_TITLE.padding + block.height + TITLE_GAP;
    const continuousY = yAxis.type !== "category";
    if (continuousY && block.broken > 0) plot.height = plotHeightAfter(plot.height, block.broken);
    const by = Math.max(0, room + TOP_OVERHANG - grid.top);
    shiftDown(option, by, option._height, option._width);
    const sideLegend = one(option.legend);
    if (legend?.right !== void 0 && sideLegend && typeof sideLegend.top === "number") {
      const titleGraphic = legendTitleGraphic(option);
      const topmost = typeof titleGraphic?.top === "number" ? titleGraphic.top : sideLegend.top;
      const under = Math.max(0, room - topmost);
      sideLegend.top += under;
      if (titleGraphic && typeof titleGraphic.top === "number") titleGraphic.top += under;
    }
    writeTitle(option, block);
  }
  thinLines(option, context, plot.height);
  const graphics = Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [];
  const sizeKey = graphics.find((g) => g?.id === EC_SIZE_KEY_ID);
  if (sizeKey) sizeKey.top = grid.top;
  grid.right = Math.max(grid.right, placeSideRamps(option, graphics, grid.top));
  option._width = Math.round(plot.width + grid.left + grid.right);
  option._height = Math.round(plot.height + grid.top + grid.bottom);
  option._planned = { plot: { width: Math.round(plot.width), height: Math.round(plot.height) }, left: grid.left, right: grid.right, top: grid.top, bottom: grid.bottom };
  Object.assign(parts, {
    yLabels: yRoom,
    ...yNameRoom ? { yTitle: yNameRoom } : {},
    xLabels: xRoom,
    ...xNameRoom ? { xTitle: xNameRoom } : {},
    title: grid.top,
    sideKeys: grid.right,
    ...footKeys || keptFoot ? { stackedKeys: Math.max(footKeys, keptFoot) } : {}
  });
  return { plot, designed, parts, ...block ? { title: block } : {} };
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
  const { left: _ignoredLeft, ...legendRest } = combined.legend;
  combined.legend = {
    ...legendRest,
    right: BUFFER,
    top: combined.legend.top ?? 20,
    orient: combined.legend.orient || "vertical",
    align: "left",
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
  const { left: _ignoredLeft, ...legendRest } = combined.legend;
  combined.legend = {
    ...legendRest,
    right: BUFFER,
    top: combined.legend.top ?? 20,
    orient: combined.legend.orient || "vertical",
    align: "left",
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
  const named = schemeName(encoding.scheme);
  if (named && named !== "default") {
    const distinct2 = countDistinctValues(ctx.table, cs.field);
    const { schemeType: schemeType2 } = decideSchemeTypeFromChannel(channel, cs);
    return {
      channel,
      schemeType: schemeType2,
      schemeId: named,
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
function normalizeChartEncodingAliases(chartType, encodings) {
  if (chartType !== "Grouped Bar Chart" || encodings.group || !encodings.color) {
    return encodings;
  }
  const normalized = { ...encodings, group: encodings.color };
  delete normalized.color;
  return normalized;
}
function normalizeStaticSeries(rawEncodings, data, semanticTypes, chartType = "") {
  const encodings = normalizeChartEncodingAliases(
    chartType,
    normalizeEncodingShorthand(rawEncodings)
  );
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
function sameOptionValue(option, value) {
  if (option === value) return true;
  return Array.isArray(option) && Array.isArray(value) && option.length === value.length && option.every((entry, index) => entry === value[index]);
}
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
    if (def.options.some((o) => sameOptionValue(o.value, value))) continue;
    if (def.accepts?.(value)) continue;
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
    const shown = typeof value === "object" ? JSON.stringify(value).slice(0, 80) : `'${value}'`;
    const copy = ensureCopy();
    delete copy[def.key];
    warnings.push({
      severity: "warning",
      code: "invalid-option-value",
      message: `chartProperties.${def.key}: ${shown} is not a valid option (accepted: ${accepted}${def.acceptsLabel ? `, or ${def.acceptsLabel}` : ""}). Falling back to the default.`
    });
  }
  return { chartProperties: result ?? chartProperties, warnings };
}

// src/echarts/assemble.ts
function applyFieldDisplayNames(option, names) {
  if (!names) return;
  const displayName = (value) => typeof value === "string" ? names[value] ?? value : value;
  for (const axisKey of ["xAxis", "yAxis", "singleAxis"]) {
    const axes = Array.isArray(option[axisKey]) ? option[axisKey] : [option[axisKey]];
    for (const axis of axes) {
      if (axis?.name) axis.name = displayName(axis.name);
    }
  }
  for (const series of option.series ?? []) {
    if (series?.name) series.name = displayName(series.name);
  }
  const graphics = Array.isArray(option.graphic) ? option.graphic : option.graphic ? [option.graphic] : [];
  for (const graphic of graphics) {
    if (graphic?.style?.text) graphic.style.text = displayName(graphic.style.text);
  }
}
function assembleECharts(input) {
  const chartType = input.chart_spec.chartType;
  const semanticTypes = withEncodingColorAnnotations(input.semantic_types ?? {}, input.chart_spec.encodings);
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
  const sortRepair = repairEncodingSorts(input.chart_spec.encodings, rawData, { fieldSort: false });
  warnings.push(...sortRepair.warnings);
  const normalized = normalizeStaticSeries(
    sortRepair.encodings,
    rawData,
    semanticTypes,
    chartType
  );
  let data = normalized.data;
  const staticSeries = normalized.staticSeries;
  const prelimConvertedData = convertTemporalData(data, semanticTypes);
  const prelimSemantics = resolveChannelSemantics(
    normalized.encodings,
    data,
    semanticTypes,
    prelimConvertedData,
    { chartType: chartTemplate.chart }
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
  const overridden = applyEncodingOverrides(chartTemplate, transformed.encodings, chartProperties);
  const implicit = resolveImplicitAggregation(chartTemplate.chart, overridden, data, semanticTypes);
  const encodings = implicit.encodings;
  warnings.push(...implicit.warnings);
  data = applyAggregation(encodings, data);
  const tplMark = chartTemplate.template?.mark;
  const templateMarkType = typeof tplMark === "string" ? tplMark : tplMark?.type;
  const convertedData = convertTemporalData(data, semanticTypes);
  const channelSemantics = resolveChannelSemantics(
    encodings,
    data,
    semanticTypes,
    convertedData,
    { chartType: chartTemplate.chart }
  );
  const effectiveMarkType = zeroMarkType(chartTemplate.chart, templateMarkType);
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
    // ECharts bars natively distribute across the available grid.
    bandStepFit: 1,
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
  effectiveOptions.bandStepFitCapacityX = sizeCeiling?.width ?? baseSize.width;
  effectiveOptions.bandStepFitCapacityY = sizeCeiling?.height ?? baseSize.height;
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
  const labelsBefore = { x: JSON.stringify(layoutResult.xLabel), y: JSON.stringify(layoutResult.yLabel) };
  planBandLabels(instantiateContext, {
    measure: (fontSize) => tableTextMeasurer(fontSize, "sans-serif", "normal"),
    minFontSize: BACKEND_MIN_LABEL_SIZE,
    ...xLabelOverflow(void 0, chartProperties, channelSemantics)
  });
  const plannedLabels = {
    x: JSON.stringify(layoutResult.xLabel) !== labelsBefore.x,
    y: JSON.stringify(layoutResult.yLabel) !== labelsBefore.y
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
  const authoredColor = authoredColorScale(input.chart_spec.encodings);
  if (authoredColor) {
    const colorSemantics = Object.values(channelSemantics).find((cs) => cs?.field === authoredColor.field && cs.colorScheme);
    const pivot = colorSemantics?.colorScheme?.type === "diverging" ? colorSemantics.colorScheme.domainMid ?? 0 : void 0;
    applyAuthoredColorScaleEC(ecOption, authoredColor, values, pivot, colorSemantics?.colorScheme?.neutral);
  }
  applyFieldDisplayNames(ecOption, input.field_display_names);
  const frame = ecFitFrame(
    ecOption,
    instantiateContext,
    { title: input.chart_spec.title, subtitle: input.chart_spec.subtitle },
    plannedLabels,
    fitBox(baseSize, sizeCeiling, options.maxStretch).width
  );
  const box = fitBox(baseSize, sizeCeiling, options.maxStretch);
  const judged = judgeBackendCanvas({ plot: frame.plot, designed: frame.designed, parts: frame.parts, padding: 0, box, base: baseSize });
  const refit = refitCanvas(judged, options, (treatment) => assembleECharts(withTreatment(input, treatment, box)));
  if (refit) return refit;
  ecOption._canvasFit = canvasFitSummary(judged.estimate, box, options.canvasTreatment);
  if (warnings.length > 0) {
    ecOption._warnings = warnings;
  }
  if (overflowResult.viewports.length > 0) {
    ecOption._viewports = overflowResult.viewports;
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
  return noteSpecUpdates(ecOption, input);
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
  "Ridgeline Plot": FAMILY_DENSITY,
  "Marginal Plot": FAMILY_XY_STANDARD,
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
