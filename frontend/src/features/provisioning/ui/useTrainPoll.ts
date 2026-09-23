import { useEffect } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { provisioningApi } from "../api";
import type { ProgressEvent } from "../model";
import { codeOf, errText } from "./kit";

const POLL_MS = 2000;

/** Wave 6 (perf): the live train-progress tail, extracted verbatim from
 *  ProvisioningPage so the page stays under the 500-line law. Semantics
 *  unchanged: poll only while `training`, pause while the tab is hidden,
 *  resume + one immediate tick on return (never staler than one tick). */
export function useTrainPoll(
  training: boolean,
  seenSeq: MutableRefObject<number>,
  setEvents: Dispatch<SetStateAction<ProgressEvent[]>>,
  setTraining: Dispatch<SetStateAction<boolean>>,
  setResult: Dispatch<SetStateAction<Record<string, unknown> | null>>,
  setNotice: Dispatch<SetStateAction<string>>,
  setError: Dispatch<SetStateAction<string>>,
): void {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- dispatch identities
  // are stable; listing them keeps the memo/deps law honest without behavior
  // change (the original effect ran on [training] alone).
  useEffect(() => {

    if (!training) return;
    let cancelled = false;
    let timer: number | null = null;
    const tick = async () => {
      try {
        const p = await provisioningApi.trainProgress(seenSeq.current);
        if (cancelled) return;
        if (Array.isArray(p.events) && p.events.length > 0) {
          setEvents((prev) => [...prev, ...p.events]);
          const maxSeq = p.events.reduce((m, e) => Math.max(m, Number(e.seq ?? 0)), seenSeq.current);
          seenSeq.current = maxSeq;
        }
        if (!p.active) {
          setTraining(false);
          setResult(p.result ?? null);
          if (p.cancelled) setNotice("Run cancelled — observed at the epoch boundary.");
          else if (!p.success) setError(codeOf(p));
          else setNotice("Run finished.");
        }
      } catch (err) {
        if (!cancelled) setError(errText(err));
      }
    };
    const startTimer = () => {
      if (timer === null && !cancelled) timer = window.setInterval(tick, POLL_MS);
    };
    const stopTimer = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") stopTimer();
      else {
        startTimer();
        void tick();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    if (document.visibilityState !== "hidden") {
      void tick();
      startTimer();
    }
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      stopTimer();
    };
    }, [training, seenSeq, setEvents, setTraining, setResult, setNotice, setError]);
}
