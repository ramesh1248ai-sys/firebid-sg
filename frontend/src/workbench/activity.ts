import { useEffect, useRef } from "react";

import { api } from "@/api/client";

/**
 * Time on task (P1-11): once a minute, while the workbench is visible and the person has used
 * it in that minute (a key, a click, a drag), tell the server. It keeps one row per minute,
 * so time on task is minutes actually spent working, not minutes a tab was left open.
 */
export function useActivityHeartbeat(bidId: string, area = "review", everyMs = 60_000) {
  const used = useRef(false);
  useEffect(() => {
    const mark = () => {
      used.current = true;
    };
    const events = ["keydown", "pointerdown", "wheel"] as const;
    for (const name of events) window.addEventListener(name, mark, { passive: true });
    const beat = () => {
      if (!used.current || document.visibilityState !== "visible") return;
      used.current = false;
      void api
        .POST("/bids/{bid_id}/review/activity", {
          params: { path: { bid_id: bidId } },
          body: { area },
        })
        .catch(() => undefined); // a missed minute is not worth interrupting anyone over
    };
    const timer = window.setInterval(beat, everyMs);
    return () => {
      window.clearInterval(timer);
      for (const name of events) window.removeEventListener(name, mark);
      beat(); // the minute in progress when the page is left
    };
  }, [bidId, area, everyMs]);
}
