type PlateauPoint = { date: string; kg: number };

const LINE_COLOR: Record<string, string> = {
  plateau: "#7c3aed",
  rebound: "#e11d48",
  normal: "#0891b2",
  insufficient_data: "#94a3b8",
};

function plateauPoints(evidence: Record<string, unknown>): PlateauPoint[] {
  const raw = Array.isArray(evidence.points) ? evidence.points : [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const point = item as Record<string, unknown>;
    const date = typeof point.date === "string" ? point.date.trim() : "";
    return date && typeof point.kg === "number" ? [{ date, kg: point.kg }] : [];
  });
}

/**
 * 判定窗口内每一次称重的折线 + 后端算出的那条趋势线（虚线）。
 *
 * 审核控制台的待跟进列表和用户详情页共用这一份：运营在控制台就该看到"怎么走成这个结论的"，
 * 不该为了看一眼走向再跳一趟详情页。compact 是列表里的小图（去掉说明文字和中间刻度）。
 *
 * 纵轴最少铺开 2 公斤（正好是"算波动"的门槛）：否则 70.0/70.2 这种没动的一周会被自动
 * 缩放放大成剧烈锯齿，看着像大起大落，运营会得出跟结论相反的印象。
 * 横轴按真实日期排，漏称的那天会自然留出空档。
 */
export function PlateauTrendChart({
  evidence,
  status,
  compact = false,
}: {
  evidence?: Record<string, unknown> | null;
  status?: string | null;
  compact?: boolean;
}) {
  const facts = evidence && typeof evidence === "object" ? (evidence as Record<string, unknown>) : {};
  const points = plateauPoints(facts);
  if (points.length < 2) return null;

  const width = 640;
  const height = compact ? 120 : 168;
  const left = compact ? 38 : 44;
  const right = 16;
  const top = compact ? 12 : 16;
  const bottom = compact ? 24 : 30;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;

  const days = points.map((point) => Date.parse(`${point.date}T00:00:00+08:00`));
  const firstDay = days[0];
  const spanMs = Math.max(days[days.length - 1] - firstDay, 1);
  const trendStart = typeof facts.trend_start_kg === "number" ? facts.trend_start_kg : null;
  const trendEnd = typeof facts.trend_end_kg === "number" ? facts.trend_end_kg : null;

  const values = points.map((point) => point.kg);
  if (trendStart !== null) values.push(trendStart);
  if (trendEnd !== null) values.push(trendEnd);
  const middle = (Math.min(...values) + Math.max(...values)) / 2;
  const span = Math.max(Math.max(...values) - Math.min(...values) + 0.6, 2);
  const low = middle - span / 2;
  const x = (index: number) => left + (plotWidth * (days[index] - firstDay)) / spanMs;
  const y = (value: number) => top + plotHeight * (1 - (value - low) / span);
  const color = LINE_COLOR[status ?? "normal"] ?? LINE_COLOR.normal;
  const ticks = compact ? [low + span, low] : [low + span, middle, low];
  const label = points.map((point) => `${point.date} ${point.kg.toFixed(1)}公斤`).join("，");
  const dayLabel = (date: string) => date.slice(5).replace("-", "/");

  return (
    <div
      className={
        compact
          ? "rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-2"
          : "border-t border-slate-200 px-3 py-4 sm:px-5"
      }
    >
      <svg viewBox={`0 0 ${width} ${height}`} role="img" className="h-auto w-full" aria-label={`窗口内称重：${label}`}>
        {ticks.map((tick, index) => (
          <g key={index}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#e2e8f0" strokeWidth="1" />
            <text x={left - 8} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#94a3b8">
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        {trendStart !== null && trendEnd !== null && (
          <line
            x1={x(0)}
            y1={y(trendStart)}
            x2={x(points.length - 1)}
            y2={y(trendEnd)}
            stroke={color}
            strokeWidth="2.5"
            strokeDasharray="7 5"
            strokeLinecap="round"
          />
        )}
        <polyline
          points={points.map((point, index) => `${x(index)},${y(point.kg)}`).join(" ")}
          fill="none"
          stroke="#94a3b8"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        {points.map((point, index) => (
          <g key={point.date}>
            <circle cx={x(index)} cy={y(point.kg)} r={compact ? 3.5 : 4.5} fill="white" stroke="#64748b" strokeWidth="2.5" />
            {(points.length <= 7 || index === 0 || index === points.length - 1) && (
              <text x={x(index)} y={height - 8} textAnchor="middle" fontSize="11" fill="#64748b">
                {dayLabel(point.date)}
              </text>
            )}
          </g>
        ))}
      </svg>
      {!compact && (
        <p className="mt-1 px-1 text-xs text-slate-500">
          灰线是每天实际称重，
          <span className="font-semibold" style={{ color }}>
            虚线
          </span>
          是判定用的趋势线；纵轴至少铺开 2 公斤，所以真没动的一周看起来就是平的。
        </p>
      )}
    </div>
  );
}
