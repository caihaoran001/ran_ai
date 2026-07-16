"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, MapPin } from "lucide-react";
import type { PopulationGeoCity, PopulationGeoProvince, PopulationGeography } from "@/lib/api";
import { MembersDrawer } from "./distribution-panel";

const MAX_PROVINCE_SLICES = 6;
const SLICE_COLORS = ["#0891b2", "#0e7490", "#2563eb", "#4f46e5", "#7c3aed", "#c026d3", "#db2777"];

type Slice = { label: string; count: number; color: string; cities?: PopulationGeoCity[] };

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

function Pie({ slices, onSelect }: { slices: Slice[]; onSelect: (slice: Slice) => void }) {
  const total = slices.reduce((sum, slice) => sum + slice.count, 0);
  let cursor = 0;
  return (
    <svg viewBox="0 0 200 200" className="h-52 w-52 shrink-0" role="img" aria-label="地域占比分布图">
      {slices.map((slice) => {
        const start = cursor;
        const end = cursor + (slice.count / total) * 360;
        cursor = end;
        return (
          <path
            key={slice.label}
            d={arcPath(start, end)}
            fill={slice.color}
            stroke="white"
            strokeWidth="2"
            role="button"
            tabIndex={0}
            aria-label={`查看${slice.label}，${slice.count}人`}
            className="cursor-pointer transition-opacity hover:opacity-75 focus:opacity-75"
            onClick={() => onSelect(slice)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(slice);
              }
            }}
          >
            <title>{`${slice.label} · ${slice.count} 人`}</title>
          </path>
        );
      })}
      <circle cx="100" cy="100" r="46" fill="white" />
      <text x="100" y="94" textAnchor="middle" className="fill-slate-400 text-[10px] font-semibold">已匹配城市</text>
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
    const slices = visible.map((province, index) => ({
      label: province.province,
      count: province.count,
      color: SLICE_COLORS[index % SLICE_COLORS.length],
      cities: province.cities,
    }));
    const otherCount = rest.reduce((sum, province) => sum + province.count, 0);
    if (otherCount > 0) slices.push({ label: "其他", count: otherCount, color: "#94a3b8", cities: mergeOtherCities(rest) });
    return slices;
  }, [geography.provinces]);
  const citySlices = useMemo<Slice[]>(() => {
    if (!selectedProvince) return [];
    const cities = selectedProvince === "other" ? provinceSlices.find((slice) => slice.label === "其他")?.cities ?? [] : selectedProvince.cities;
    return cities.map((city, index) => ({
      label: city.city,
      count: city.count,
      color: SLICE_COLORS[index % SLICE_COLORS.length],
    }));
  }, [provinceSlices, selectedProvince]);

  const isCityLevel = selectedProvince !== null;
  const currentSlices = isCityLevel ? citySlices : provinceSlices;
  const title = isCityLevel
    ? selectedProvince === "other" ? "其他省份的城市" : `${selectedProvince.province} · 城市分布`
    : "地域分布";
  const description = isCityLevel
    ? "选择城市查看对应用户。"
    : "先按省份查看，再下钻到城市；占比很小的省份合并为“其他”。";
  const denominator = geography.total + geography.unknown;

  return (
    <>
      <section className="panel overflow-hidden">
        <div className="panel-header-clean">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {isCityLevel && (
                <button type="button" onClick={() => setSelectedProvince(null)} className="rounded-md p-1 text-cyan-700 hover:bg-cyan-50" aria-label="返回省级分布">
                  <ChevronLeft className="h-4 w-4" />
                </button>
              )}
              <h2 className="panel-title">{title}</h2>
            </div>
            <p className="panel-subtitle">{description}</p>
          </div>
          <span className="status-pill pill-neutral">匹配 {geography.total}{geography.unknown > 0 ? ` · 未采集 ${geography.unknown}` : ""}</span>
        </div>
        {currentSlices.length === 0 ? (
          <div className="panel-body"><div className="empty-state">暂无可识别的城市信息</div></div>
        ) : (
          <div className="panel-body grid gap-5 md:grid-cols-[13rem_1fr] md:items-center">
            <Pie
              slices={currentSlices}
              onSelect={(slice) => {
                if (isCityLevel) setActiveCity(slice.label);
                else setSelectedProvince(slice.label === "其他" ? "other" : geography.provinces.find((province) => province.province === slice.label) ?? null);
              }}
            />
            <div className="grid gap-1.5 sm:grid-cols-2">
              {currentSlices.map((slice) => {
                const percentage = denominator > 0 ? Math.round((slice.count / denominator) * 100) : 0;
                return (
                  <button
                    key={slice.label}
                    type="button"
                    onClick={() => isCityLevel ? setActiveCity(slice.label) : setSelectedProvince(slice.label === "其他" ? "other" : geography.provinces.find((province) => province.province === slice.label) ?? null)}
                    className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-slate-50"
                    title={isCityLevel ? `查看「${slice.label}」的用户` : `查看「${slice.label}」的城市`}
                  >
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: slice.color }} />
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{slice.label}</span>
                    <span className="shrink-0 text-sm tabular-nums text-slate-500">{slice.count} 人 · {percentage}%</span>
                  </button>
                );
              })}
              {geography.unknown > 0 && !isCityLevel && (
                <button type="button" onClick={() => setActiveCity("未采集")} className="col-span-full flex items-center gap-2 rounded-md border border-dashed border-slate-200 px-2 py-1.5 text-left text-sm text-slate-500 hover:bg-slate-50" title="查看未采集地域的用户">
                  <MapPin className="h-3.5 w-3.5 text-slate-400" /> 未采集或未识别城市：{geography.unknown} 人
                </button>
              )}
            </div>
          </div>
        )}
      </section>
      {activeCity !== null && <MembersDrawer distKey="city" distLabel="地域分布" bucketLabel={activeCity} onClose={() => setActiveCity(null)} />}
    </>
  );
}
