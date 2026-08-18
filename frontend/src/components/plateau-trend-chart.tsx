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

export type PlateauRegain = { low_date: string; low_kg: number; rise_kg: number; days: number };

/**
 * 触底回涨的细节：最低点是哪天、之后几天回涨了多少。判定全在后端，前端只负责显示。
 *
 * 平台期这一屏所有读证据的地方都放在本模块，控制台和用户详情页共用同一套解析。
 */
export function regainFromLow(evidence?: Record<string, unknown> | null): PlateauRegain | null {
  const raw = evidence?.regain_from_low;
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  if (typeof item.low_date !== "string" || typeof item.low_kg !== "number") return null;
  if (typeof item.rise_kg !== "number" || typeof item.days !== "number") return null;
  return { low_date: item.low_date, low_kg: item.low_kg, rise_kg: item.rise_kg, days: item.days };
}

/**
 * 趋势线代表什么，用一句人话说出来——只看一条斜线运营不知道该得出什么结论。
 *
 * 波动和触底回涨都要单独说：净变化确实接近 0，但写"几乎没动"会跟上面那条提示自相矛盾。
 */
function trendMeaning(trend: number, status: string, attention: string): string {
  if (attention === "swing") return "上下波动，一周下来等于没变";
  if (attention === "regain") return "整周看没动，但已经掉头往回涨";
  if (status === "rebound") return "在明显往上走";
  if (status === "plateau") return "几乎没动";
  if (trend <= -0.5) return "在稳定往下走";
  if (trend >= 0.5) return "略有上浮";
  return "几乎没动";
}

/**
 * 判定窗口内每一次称重（公斤数直接标在点上方）+ 后端算出的那条趋势线（虚线）。
 *
 * 审核控制台的待跟进列表和用户详情页共用这一份：运营在控制台就该看到"怎么走成这个结论的"，
 * 不该为了看一眼走向再跳一趟详情页。
 *
 * 三个刻意的取舍：
 * - 不画纵轴刻度：运营要的是"那天多少斤"，不是坐标，所以数值直接写在点上。
 * - 数值和日期用 HTML 而不是 SVG 文字，SVG 只画两条线。SVG 里的字号会跟着图一起缩放，
 *   手机上整块缩到四成、字只剩 5px 根本看不清，桌面宽屏又会被放大到跟正文打架。
 * - 纵轴贴着数据缩放（只兜一个 0.6 公斤的下限防止除零和噪点被放到满屏）。曾经固定铺开
 *   2 公斤，怕把没动的一周画成锯齿吓到运营，结果所有图都成了直线，等于什么也没画。
 *   现在每个点都标着公斤数、图例又写明这一周总幅度，量级读得到，形状才是图该干的事。
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

  const days = points.map((point) => Date.parse(`${point.date}T00:00:00+08:00`));
  const spanMs = Math.max(days[days.length - 1] - days[0], 1);
  const trendStart = typeof facts.trend_start_kg === "number" ? facts.trend_start_kg : null;
  const trendEnd = typeof facts.trend_end_kg === "number" ? facts.trend_end_kg : null;
  const trend = typeof facts.trend_kg === "number" ? facts.trend_kg : null;
  const attention = typeof facts.attention === "string" ? facts.attention : "";

  const values = points.map((point) => point.kg);
  if (trendStart !== null) values.push(trendStart);
  if (trendEnd !== null) values.push(trendEnd);
  const spread = Math.max(...values) - Math.min(...values);
  const middle = (Math.min(...values) + Math.max(...values)) / 2;
  const span = Math.max(spread * 1.4, 0.6);
  const low = middle - span / 2;
  // 两侧留 6% 空白，最边上的数值才不会被面板边缘切掉
  const x = (index: number) => 6 + (88 * (days[index] - days[0])) / spanMs;
  const y = (value: number) => 100 * (1 - (value - low) / span);
  const color = LINE_COLOR[status ?? "normal"] ?? LINE_COLOR.normal;
  const dayLabel = (date: string) => date.slice(5).replace("-", "/");
  const description = points.map((point) => `${point.date} ${point.kg.toFixed(1)}公斤`).join("，");

  return (
    <div className={compact ? "" : "border-t border-slate-200 px-4 py-4 sm:px-5"}>
      <div className="relative h-[84px] w-full" role="img" aria-label={`窗口内称重：${description}`}>
        {points.map((point, index) => (
          <span
            key={`value-${point.date}`}
            className="tabular absolute top-0 -translate-x-1/2 text-[11px] font-bold leading-none text-slate-900"
            style={{ left: `${x(index)}%` }}
          >
            {point.kg.toFixed(1)}
          </span>
        ))}

        <div className="absolute inset-x-0 top-[18px] h-[46px]">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden="true">
            {trendStart !== null && trendEnd !== null && (
              <line
                x1={x(0)}
                y1={y(trendStart)}
                x2={x(points.length - 1)}
                y2={y(trendEnd)}
                stroke={color}
                strokeWidth="2"
                strokeDasharray="6 4"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            )}
            <polyline
              points={points.map((point, index) => `${x(index)},${y(point.kg)}`).join(" ")}
              fill="none"
              stroke="#cbd5e1"
              strokeWidth="1.5"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          {points.map((point, index) => (
            <span
              key={`dot-${point.date}`}
              className="absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-500"
              style={{ left: `${x(index)}%`, top: `${y(point.kg)}%` }}
            />
          ))}
        </div>

        {points.map((point, index) => (
          <span
            key={`date-${point.date}`}
            className="tabular absolute bottom-0 -translate-x-1/2 text-[11px] leading-none text-slate-400"
            style={{ left: `${x(index)}%` }}
          >
            {dayLabel(point.date)}
          </span>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0 w-4 border-t-[1.5px] border-slate-300" />
          每天实际称重
        </span>
        <span className="text-slate-400">这一周最高最低相差 {spread.toFixed(1)} 公斤（纵向已按这个幅度放大）</span>
        {trend !== null && (
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: color }} />
            <span>
              判定用的趋势线：这一周
              <span className="font-semibold text-slate-700">{trendMeaning(trend, status ?? "", attention)}</span>
              （{trend > 0 ? `+${trend.toFixed(2)}` : trend.toFixed(2)} 公斤）
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
