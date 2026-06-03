"use client";

import { useEffect, useState } from "react";

export type ReviewEventState = {
  pendingReviewCount: number;
  interventionCount: number;
  pendingAiMessages: number;
  pendingKnowledgeDrafts: number;
  pendingDocuments: number;
  connected: boolean;
};

const initialState: ReviewEventState = {
  pendingReviewCount: 0,
  interventionCount: 0,
  pendingAiMessages: 0,
  pendingKnowledgeDrafts: 0,
  pendingDocuments: 0,
  connected: false,
};

export function useSSE() {
  const [state, setState] = useState<ReviewEventState>(initialState);

  useEffect(() => {
    const source = new EventSource("/api/events/stream");

    source.onopen = () => {
      setState((current) => ({ ...current, connected: true }));
    };

    source.onerror = () => {
      setState((current) => ({ ...current, connected: false }));
    };

    source.addEventListener("pending_review", (event) => {
      const data = parseEventData(event);
      setState((current) => ({
        ...current,
        pendingReviewCount: numberValue(data.pending_total),
        pendingAiMessages: numberValue(data.pending_ai_messages),
        pendingKnowledgeDrafts: numberValue(data.pending_knowledge_drafts),
        pendingDocuments: numberValue(data.pending_documents),
      }));
    });

    source.addEventListener("intervention", (event) => {
      const data = parseEventData(event);
      setState((current) => ({
        ...current,
        interventionCount: numberValue(data.count),
      }));
    });

    return () => source.close();
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
