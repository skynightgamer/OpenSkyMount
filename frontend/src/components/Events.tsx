// Sky events calendar: moon phases, eclipses, conjunctions, oppositions, meteor showers and more for the next weeks,
// computed by the server for the observer's site (GET /api/events). Each row says whether it can be seen from here.
import { useEffect, useMemo, useState } from "react";
import { resolveZone } from "../format";
import { command, isController, select, simNow, toast, useStore } from "../store";
import { DAY_CHOICES, type SkyEvent, sessionWindow, untilText } from "../events";
import { Section } from "./Panels";

export function EventsPanel() {
  const tz = useStore((s) => s.prefs.timeZone);
  const epoch = useStore((s) => s.clock?.epoch);
  const site = useStore((s) => s.config?.config.observer);
  const ctl = useStore((s) => isController(s));
  const simulated = useStore((s) => !!s.config?.capabilities.simulated);
  const [days, setDays] = useState<number>(60);
  const [highlights, setHighlights] = useState(true);
  const [events, setEvents] = useState<SkyEvent[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    setFailed(false);
    fetch(`/api/events?t=${simNow()}&days=${days}&min_importance=${highlights ? 2 : 1}`, { signal: abort.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { events: SkyEvent[] }) => setEvents(d.events))
      .catch((e) => {
        if (e.name !== "AbortError") setFailed(true);
      });
    return () => abort.abort();
  }, [days, highlights, epoch, site?.latitude, site?.longitude, site?.elevation_m]);

  const fmt = useMemo(() => {
    const zone = resolveZone(tz);
    const date = new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: "short", month: "short", day: "numeric" });
    const time = new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: "2-digit", minute: "2-digit" });
    return { date: (u: number) => date.format(u * 1000), time: (u: number) => time.format(u * 1000) };
  }, [tz]);

  const now = simNow();
  const next = events?.find((e) => e.time >= now - 3600);
  const summary = failed ? "unavailable" : events == null ? "computing…" : next ? `${next.title} ${untilText(next.time, now)}` : "none in range";

  const addToSession = (e: SkyEvent) => {
    if (!e.target) return;
    command("queue_add", { target: e.target, ...sessionWindow(e) }).then((a) => a.ok && toast(`${e.title} added to the session`));
  };
  const jump = (e: SkyEvent) =>
    command("set_clock", { time: e.time - 600 }).then((a) => a.ok && toast("Simulated clock set 10 minutes before the event"));

  return (
    <Section id="sky-events" title="Sky events" summary={summary}>
      <div className="ev-filters">
        <label>
          <input type="checkbox" checked={highlights} onChange={(e) => setHighlights(e.target.checked)} /> Highlights only
        </label>
        <label>
          Next{" "}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Days ahead">
            {DAY_CHOICES.map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </label>
      </div>
      {failed && <p className="hint">Could not compute the events.</p>}
      {events && events.length === 0 && <p className="hint">Nothing {highlights ? "notable " : ""}in the next {days} days.</p>}
      <ul className="ev-list">
        {(events ?? []).map((e) => (
          <li key={e.id} className={`ev ev-${e.kind}${e.visible ? " ev-seen" : ""}`}>
            <div className="ev-head">
              <span>
                {fmt.date(e.time)} · <b className="num">{fmt.time(e.time)}</b> <span className="ev-rel">{untilText(e.time, now)}</span>
              </span>
              <span className={e.visible ? "vis" : "dim"} title="Main object above the horizon and the Sun below −6° at that moment, from your site">
                {e.visible == null ? "" : e.visible ? "visible here" : "not visible here"}
              </span>
            </div>
            <strong>{e.title}</strong>
            <small>{e.detail}</small>
            {e.safety && <small className="ev-warn">{e.safety}</small>}
            <span className="ev-acts">
              {e.target && (
                <button onClick={() => select(e.target, true)} title="Select it and centre the sky map on it">
                  Show
                </button>
              )}
              {e.target && e.visible && (
                <button disabled={!ctl} onClick={() => addToSession(e)} title={ctl ? "Queue a GoTo for the event" : "Take control to use the session"}>
                  + Session
                </button>
              )}
              {simulated && (
                <button disabled={!ctl} onClick={() => jump(e)} title={ctl ? "Set the simulated clock to just before the event" : "Take control to change the clock"}>
                  Jump
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      <p className="hint">
        Computed offline from the JPL DE421 ephemeris for {site?.name ?? "your site"}. Times are in your display time zone.
      </p>
    </Section>
  );
}
