"use client";

import { useEffect, useState } from "react";

export type ReviewEventState = {
  pendingReviewCount: number;
  pendingKnowledgeDrafts: number;
  pendingDocuments: number;
  connected: boolean;
};

const initialState: ReviewEventState = {
  pendingReviewCount: 0,
  pendingKnowledgeDrafts: 0,
  pendingDocuments: 0,
  connected: false,
};

/**
 * 全页共享一条 SSE 连接。
 *
 * 审核控制台同时挂了两个用到待审计数的组件，各自 new EventSource 会在后端开两条长连接、
 * 两份轮询查询；SSE 里查库还要占数据库会话，连接数是稀缺资源。故做成引用计数的单例：
 * 第一个订阅者建连，最后一个卸载时才关闭。
 */
let sharedSource: EventSource | null = null;
let subscriberCount = 0;
let sharedState: ReviewEventState = initialState;
const listeners = new Set<(state: ReviewEventState) => void>();

function publish(next: ReviewEventState) {
  sharedState = next;
  for (const listener of listeners) listener(next);
}

function openSharedSource() {
  const source = new EventSource("/api/events/stream");

  source.onopen = () => {
    publish({ ...sharedState, connected: true });
  };

  source.onerror = () => {
    publish({ ...sharedState, connected: false });
  };

  source.addEventListener("pending_review", (event) => {
    const data = parseEventData(event);
    publish({
      ...sharedState,
      pendingReviewCount: numberValue(data.pending_total),
      pendingKnowledgeDrafts: numberValue(data.pending_knowledge_drafts),
      pendingDocuments: numberValue(data.pending_documents),
    });
  });

  return source;
}

export function useSSE() {
  // 首帧必须用固定初始值，不能读上面那个共享变量：外层框架（侧边栏）先挂载并建连，
  // 会把共享状态改成"已连上"，而页面正文的服务端 HTML 还写着"连接中"，正文晚一步
  // 在浏览器接手时两边就对不上（hydration 报错）。真实状态由下面挂载后那次补发填上。
  const [state, setState] = useState<ReviewEventState>(initialState);

  useEffect(() => {
    listeners.add(setState);
    subscriberCount += 1;
    if (!sharedSource) {
      sharedSource = openSharedSource();
    }
    // 连接已存在时补发当前快照，避免后挂载的组件一直停在"连接中"。
    setState(sharedState);

    return () => {
      listeners.delete(setState);
      subscriberCount -= 1;
      if (subscriberCount <= 0) {
        subscriberCount = 0;
        sharedSource?.close();
        sharedSource = null;
        sharedState = { ...sharedState, connected: false };
      }
    };
  }, []);

  return state;
}

function parseEventData(event: Event): Record<string, unknown> {
  if (!(event instanceof MessageEvent)) return {};
  try {
    return JSON.parse(event.data) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
