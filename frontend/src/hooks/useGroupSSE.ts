import { useEffect, useRef } from "react";

export interface SSEHandlers {
  onPhase: (status: string, round?: number) => void;
  onProgress: (participant_count: number, submitted_count: number, round?: number) => void;
  onConsensus: (content: string, round?: number) => void;
  onError: (message: string) => void;
  onDisconnect: () => void;
  onRound?: (round: number, question: string, status: "opened" | "closed", adaptive?: boolean) => void;
}

export function useGroupSSE(pin: string, handlers: SSEHandlers) {
  // Keep a ref to the latest handlers so the SSE event listeners always call
  // the current closures (avoids stale-closure bug where is_creator etc.
  // captured at first render never update on subsequent renders).
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    const es = new EventSource(`/api/events/${pin}`);

    es.addEventListener("phase", (e: MessageEvent) => {
      const d = JSON.parse(e.data);
      ref.current.onPhase(d.status, d.round);
    });
    es.addEventListener("progress", (e: MessageEvent) => {
      const d = JSON.parse(e.data);
      ref.current.onProgress(d.participant_count, d.submitted_count, d.round);
    });
    es.addEventListener("consensus", (e: MessageEvent) => {
      const d = JSON.parse(e.data);
      ref.current.onConsensus(d.content, d.round);
    });
    es.addEventListener("round", (e: MessageEvent) => {
      if (ref.current.onRound) {
        const d = JSON.parse(e.data);
        ref.current.onRound(d.round, d.question, d.status, d.adaptive);
      }
    });
    es.onerror = () => {
      ref.current.onDisconnect();
    };

    return () => es.close();
  }, [pin]);
}
