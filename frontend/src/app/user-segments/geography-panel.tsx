"use client";

import { useMemo, useState } from "react";
import { ChevronLeft } from "lucide-react";
import type { PopulationGeoCity, PopulationGeoProvince, PopulationGeography } from "@/lib/api";
import { MembersDrawer } from "./distribution-panel";

const MAX_PROVINCE_SLICES = 5;
const UNKNOWN_LABEL = "未采集";
const SLICE_OPACITIES = [1, 0.86, 0.72, 0.6, 0.48];

type Slice = {
  label: string;
  count: number;
  color: string;
  opacity: number;
  kind: "province" | "other" | "unknown" | "city";
  cities?: PopulationGeoCity[];
};

function polarPoint(angle: number) {
  const radians = ((angle - 90) * Math.PI) / 180;
  return { x: 100 + 82 * Math.cos(radians), y: 100 + 82 * Math.sin(radians) };
}

function arcPath(start: number, end: number) {
  const from = polarPoint(end);
  const to = polarPoint(start);
  const largeArc = end - start > 180 ? 1 : 0;
  return `M 100 100 L ${from.x} ${from.y} A 82 82 0 ${largeArc} 0 ${to.x} ${to.y} Z`;
}

function Pie({ slices, totalLabel }: { slices: Slice[]; totalLabel: string }) {
  const total = slices.reduce((sum, slice) => sum + slice.count, 0);
  let cursor = 0;
  return (
    <svg
      viewBox="0 0 200 200"
      className="mx-auto h-44 w-44 shrink-0 sm:h-52 sm:w-52"
      role="img"
      aria-label={`地域占比分布图，${totalLabel}${total}人`}
    >
      {slices.map((slice) => {
        const start = cursor;
        const end = cursor + (slice.count / total) * 360;
        cursor = end;
        if (slice.count === total) {
          return (
            <circle
              key={slice.label}
              cx="100"
              cy="100"
              r="82"
              fill={slice.color}
              fillOpacity={slice.opacity}
            >
              <title>{`${slice.label} · ${slice.count} 人`}</title>
            </circle>
          );
        }
        return (
          <path
            key={slice.label}
            d={arcPath(start, end)}
            fill={slice.color}
            fillOpacity={slice.opacity}
            stroke="white"
            strokeWidth="2"
          >
            <title>{`${slice.label} · ${slice.count} 人`}</title>
          </path>
        );
      })}
      <circle cx="100" cy="100" r="46" fill="white" />
      <text x="100" y="94" textAnchor="middle" className="fill-slate-500 text-[10px] font-semibold">{totalLabel}</text>
      <text x="100" y="113" textAnchor="middle" className="fill-slate-900 text-[18px] font-bold">{total}</text>
    </svg>
  );
}

function mergeOtherCities(provinces: PopulationGeoProvince[]) {
  const counts = new Map<string, number>();
  provinces.forEach((province) => province.cities.forEach((city) => {
    counts.set(city.city, (counts.get(city.city) ?? 0) + city.count);
  }));
  return [...counts.entries()]
    .map(([city, count]) => ({ city, count }))
    .sort((a, b) => b.count - a.count || a.city.localeCompare(b.city, "zh-CN"));
}

export function GeographyPanel({ geography }: { geography: PopulationGeography }) {
  const [selectedProvince, setSelectedProvince] = useState<PopulationGeoProvince | "other" | null>(null);
  const [activeCity, setActiveCity] = useState<string | null>(null);
  const provinceSlices = useMemo<Slice[]>(() => {
    const visible = geography.provinces.slice(0, MAX_PROVINCE_SLICES);
    const rest = geography.provinces.slice(MAX_PROVINCE_SLICES);
    const slices: Slice[] = visible.map((province, index) => ({
      label: province.province,
      count: province.count,
      color: "#0e7490",
      opacity: SLICE_OPACITIES[index % SLICE_OPACITIES.length],
      kind: "province",
      cities: province.cities,
    }));
    const otherCount = rest.reduce((sum, province) => sum + province.count, 0);
    if (otherCount > 0) {
      slices.push({
        label: "其他",
        count: otherCount,
        color: "#155e75",
        opacity: 0.36,
        kind: "other",
        cities: mergeOtherCities(rest),
      });
    }
    if (geography.unknown > 0) {
      slices.push({
        label: UNKNOWN_LABEL,
        count: geography.unknown,
        color: "#475569",
        opacity: 0.44,
        kind: "unknown",
      });
    }
    return slices;
  }, [geography.provinces, geography.unknown]);
  const citySlices = useMemo<Slice[]>(() => {
    if (!selectedProvince) return [];
    const cities = selectedProvince === "other"
      ? provinceSlices.find((slice) => slice.kind === "other")?.cities ?? []
      : selectedProvince.cities;
    return cities.map((city, index) => ({
      label: city.city,
      count: city.count,
      color: "#0e7490",
      opacity: SLICE_OPACITIES[index % SLICE_OPACITIES.length],
      kind: "city",
    }));
  }, [provinceSlices, selectedProvince]);

  const isCityLevel = selectedProvince !== null;
  const currentSlices = isCityLevel ? citySlices : provinceSlices;
  const denominator = currentSlices.reduce((sum, slice) => sum + slice.count, 0);
  const title = isCityLevel
    ? selectedProvince === "other" ? "其他省份的城市" : `${selectedProvince.province} · 城市分布`
    : "地域分布";
  const description = isCityLevel
    ? "百分比表示该城市在当前省份中的占比；选择城市可查看对应用户。"
    : "百分比以全部纳入用户为分母；较小省份合并为“其他”，未采集单独展示。";
  const totalLabel = isCityLevel ? "当前省份" : "全部用户";

  function selectSlice(slice: Slice) {
    if (isCityLevel || slice.kind === "unknown") {
      setActiveCity(slice.label);
      return;
    }
    if (slice.kind === "other") {
      setSelectedProvince("other");
      return;
    }
    setSelectedProvince(geography.provinces.find((province) => province.province === slice.label) ?? null);
  }

  return (
    <>
      <section id="geography" className="panel scroll-mt-6 overflow-hidden" aria-labelledby="geography-title">
        <div className="panel-header-clean sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {isCityLevel && (
                <button
                  type="button"
                  onClick={() => setSelectedProvince(null)}
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-cyan-800 hover:bg-cyan-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
                  aria-label="返回省级分布"
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                </button>
              )}
              <h2 id="geography-title" className="panel-title">{title}</h2>
            </div>
            <p className="panel-subtitle">{description}</p>
          </div>
          <span className="status-pill pill-neutral shrink-0 self-start">
            {isCityLevel
              ? `${denominator} 位用户`
              : `已匹配 ${geography.total}${geography.unknown > 0 ? ` · 未采集 ${geography.unknown}` : " · 已完整"}`}
          </span>
        </div>
        {currentSlices.length === 0 ? (
          <div className="panel-body"><div className="empty-state">当前层级暂无可识别的城市信息</div></div>
        ) : (
          <div className="panel-body grid gap-5 md:grid-cols-[11rem_minmax(0,1fr)] md:items-center lg:grid-cols-[13rem_minmax(0,1fr)]">
            <Pie slices={currentSlices} totalLabel={totalLabel} />
            <div className="grid gap-1.5 sm:grid-cols-2">
              {currentSlices.map((slice) => {
                const slicePercentage = denominator > 0 ? Math.round((slice.count / denominator) * 100) : 0;
                const action = isCityLevel || slice.kind === "unknown" ? "查看用户" : "查看城市";
                return (
                  <button
                    key={slice.label}
                    type="button"
                    onClick={() => selectSlice(slice)}
                    className="flex min-h-11 min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
                    title={`${action}：${slice.label}`}
                    aria-label={`${action}${slice.label}，${slice.count}人，占当前统计范围${slicePercentage}%`}
                  >
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: slice.color, opacity: slice.opacity }}
                    />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">{slice.label}</span>
                    <span className="shrink-0 text-sm tabular-nums text-slate-600">{slice.count} 人 · {slicePercentage}%</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </section>
      {activeCity !== null && (
        <MembersDrawer
          distKey="city"
          distLabel="地域分布"
          bucketLabel={activeCity}
          onClose={() => setActiveCity(null)}
        />
      )}
    </>
  );
}
