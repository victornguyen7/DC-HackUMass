// Shapes of the static demo bundle (web/public/cache), written by `dcsim bundle`.

export type Range = { p10: number; p50: number; p90: number };

export type ManifestEntry = {
  key: string;
  date: string;
  preset: string | null;
  lever: string;
  label: string;
  config_hash: string;
  file: string;
  trace: string | null;
  narration: string | null;
};

export type Manifest = {
  generated_at: string;
  engine_version: string;
  n_nights: number;
  featured_date: string;
  dates: string[];
  presets: string[];
  entries: ManifestEntry[];
};

export type Plan = {
  batch_size: number;
  starting_batches: number;
  reorder_point: number;
  cook_min: number;
  hold_min: number;
  max_batches: number;
  last_batch_min: number;
  last_batch_clock: string;
};

export type CookRow = {
  dish_id: string;
  name: string;
  station: string;
  role: string;
  period?: "dinner" | "late_night";
  diet_tags: string[];
  hot: boolean;
  to_order: boolean;
  popularity: number;
  popularity_source: string;
  portions_to_prepare: Range;
  served: Range;
  waste: Range;
  expired: Range;
  leftover: Range;
  stockout_min: Range;
  runout_share: number;
  runout_window: { from: string; to: string; p50_min: number } | null;
  first_choice_demand: Range;
  plan: Plan | null;
};

export type Flag = { kind: string; severity: "info" | "warn" | "bad" | "good" | string; dish_id?: string; station?: string; text: string };

export type StationResult = {
  id: string;
  name: string;
  pos: [number, number];
  servers: number;
  self_serve: boolean;
  to_order: boolean;
  service_median_min: number;
  max_queue: Range;
};

export type Verdict = "better" | "worse" | "inconclusive" | "same"; // "same": difference below the minimum that matters (engine 0.6.2)
export type Compare = {
  n_pairs: number;
  metrics: Record<string, { diff: Range; win_rate: number; verdict: Verdict; min_meaningful?: number }>;
};

export type Headline = {
  waste_total: Range;
  stockout_min_total: Range;
  missed_first_choice_rate: Range;
  wait_p90: Range;
  missed_meal: Range;
  students: Range;
  // engine 0.5.0 (absent in older bundles)
  batch_wait_min?: Range; // minutes batches waited for a cook or equipment
  cook_busy_share?: Range;
  empty_wait_min?: Range; // minutes students waited at empty pans for a batch
  seat_wait_min?: Range;
  peak_seated?: Range;
};

export type Band = { p10: number[]; p50: number[]; p90: number[] };
/** Index i is minute t0 + i * step. */
export type Series = {
  t0: number;
  step: number;
  n_nights: number;
  inside: Band; // students in the building
  queue: Record<string, Band>; // students in line, by station id
  pans: Record<string, Band>; // portions on hand (line + hot-hold), by dish id
};

export type Result = {
  config_hash: string;
  label: string;
  date: string;
  meal: string;
  n_nights: number;
  provisional: boolean;
  engine_version: string;
  population_status: string;
  open: number;
  /** Serving stops (end of late night when there is one). */
  close: number;
  /** End of dinner; late night runs dinner_close..close. Missing in pre-0.4 bundles. */
  dinner_close?: number;
  late_close?: number | null;
  headline: Headline;
  cook_sheet: CookRow[];
  flags: Flag[];
  stations: StationResult[];
  arrivals: { start: string; start_min: number; expected: number }[];
  median_seed: number;
  /** Per-minute p10/p50/p90 bands, only with GET /api/results/{hash}?include=series (engine 0.6.0). */
  series?: Series;
  /** Servers needed per station and hour ("19" = 7–8 pm) for ≤85% busy (engine 0.5.0). */
  staffing?: Record<string, Record<string, number>>;
  baseline_hash: string;
  vs_baseline?: Compare;
};

export type TraceEvent = {
  t: number;
  type: "enter" | "queue_join" | "served" | "balk" | "empty_pan" | "sit" | "seconds" | "retry" | "closed" | "dish_return" | "exit";
  station?: string;
  dish?: string;
  line?: number;
  vessel?: "plate" | "bowl" | "cup";
  seat?: string;
};

export type TraceStudent = { id: number; persona: string; diet: string; events: TraceEvent[] };
export type PanEvent = { t: number; dish: string; portions: number; event: "open_stock" | "batch_start" | "batch_ready" | "expire" | "close" };
export type Trace = { night_seed: number; students: TraceStudent[]; pans: PanEvent[] };

export type NarrationEvent = TraceEvent & { clock: string; text: string; dish_name?: string; station_name?: string };
export type NarrationStudent = {
  card: {
    student_id: number;
    title: string;
    persona: { id: string; label: string; diet: string };
    first_choice: string | null;
    got: string[];
    got_first_choice: boolean;
    missed_meal: boolean;
    events: NarrationEvent[];
  };
  text: string;
  source: string;
};
export type Narration = {
  config_hash: string;
  seed: number;
  highlights: { student_id: number; reason: string; title: string }[];
  students: Record<string, NarrationStudent>;
};

export type DateInfo = {
  date: string;
  weekday: string;
  weekend: boolean;
  events_at_or_affecting_worcester: { name: string }[];
  events_elsewhere_on_campus: string[];
  building_hours: { open: string; close: string } | null;
};

export type MenuRef = {
  date: string;
  meal: string;
  open: string;
  close: string;
  stations: { id: string; name: string; to_order: boolean; self_serve: boolean; servers: number }[];
  dishes?: unknown[];
  addons?: { name: string; station?: string }[];
};

export type HistoryNight = {
  date: string;
  weekday: string;
  students: number;
  adopted: boolean;
  waste_portions: number;
  waste_lb: number;
  waste_cost_usd: number;
  expired_lb: number;
  close_lb: number;
  plate_lb: number;
  stockout_min: number;
  cooked_portions: number;
};
export type WasteHistory = {
  nights: HistoryNight[];
  top_dishes: { dish_id: string; dish: string; station: string; expired_lb: number; close_lb: number; plate_lb: number; portions: number; pre_consumer_lb: number }[];
};
export type BacktestNight = {
  date: string;
  weekday: string;
  students: number;
  recorded_waste_lb: number;
  recorded_stockout_min: number;
  baseline_plan_waste_lb: Range;
  plan_waste_lb: Range;
  plan_stockout_min: Range;
  plan_wins: boolean;
  /** recorded vs plan (mean of seeds) pre-consumer lb per station; older bundles lack it */
  by_station?: Record<string, { recorded_lb: number; plan_lb: number }>;
};
export type BacktestDish = {
  dish_id: string;
  dish: string;
  station: string;
  nights: number;
  recorded_lb_per_night: number;
  plan_lb_per_night: number;
  change_pct: number | null;
  usd_saved_per_week: number;
};
export type Backtest = {
  plan: string;
  headline: {
    nights: number;
    plan_wins: number;
    reduction_pct: Range;
    lb_per_week: Range;
    usd_per_week: Range;
    recorded_lb_per_week: number;
    stockout_min_per_night: { recorded: number; baseline_plan: Range; plan: Range };
  };
  nights: BacktestNight[];
  dishes?: BacktestDish[];
  adopted_nights: {
    date: string;
    weekday: string;
    plan: string;
    predicted_waste_lb: Range;
    recorded_waste_lb: number;
    predicted_stockout_min: Range;
    recorded_stockout_min: number;
    within_range: boolean;
  }[];
};
export type WasteFacts = {
  destinations_and_targets: {
    destination_distribution_fy2026: { anaerobic_digestion_pct: number; donation_pct: number; reuse_pct: number };
  };
};
