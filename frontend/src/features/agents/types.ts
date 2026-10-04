export interface Treatment {
  key: string;
  name: string;
  price_eur: number;
}
/** A question the practice wants asked before a booking; the answer goes into the appointment note. */
export interface BookingQuestion {
  label: string;
  ask: string;
  required: boolean;
}
export interface AgentConfig {
  name: string;
  practice_name: string;
  street: string;
  postcode: string;
  city: string;
  phone: string;
  email: string;
  recipients: string[];
  // E.164, or "" for no transfer: a caller who asks for a person gets a callback instead.
  transfer_number: string;
  timezone: string;
  locale: "de" | "en" | "ru" | "ar";
  greeting: string;
  instructions: string;
  voice_id: string;
  open_from: string;
  open_until: string;
  break_from: string;
  break_until: string;
  weekdays: number[];
  slot_minutes: number;
  resources: string[];
  treatments: Treatment[];
  booking_questions: BookingQuestion[];
  consent_policy_id: string;
  transcript_retention_days: number;
  /** Free cancellation or move until this many hours before; inside it staff decide (ADR-0021). */
  cancellation_free_hours: number;
  /** What the caller is told when a change is inside that window. */
  cancellation_policy: string;
  /** Workspace documents this agent knows, in reading order (ADR-0017). */
  knowledge_ids: string[];
  booking_enabled: boolean;
  // null means every capability — what every agent published before this field existed has.
  skills: string[] | null;
  voice_engine: "cascaded" | "realtime";
  realtime_voice: string;
  recall_policy: "off" | "greeting" | "full";
  recall_acknowledged: boolean;
}

/** One capability an agent can be given, as the runtime describes it. */
export interface AgentCapability {
  name: string;
  display_name: string;
  description: string;
  tools: string[];
  state_changing: boolean;
  /** Connection roles this capability cannot work without — [] for one that needs no system. */
  requires: string[];
}

/** What a connection role holds right now, and what could fill it. */
export interface AgentSystems {
  calendar: {
    bound: {
      id: string;
      adapter: string;
      label: string;
      /** Connected is not the same as usable: a calendar with no room mapping is neither. */
      configured: boolean;
      /** What a call on this system can do: availability, patients, booking. */
      capabilities?: string[];
    } | null;
    supported: string[];
  };
}
export interface AgentRevision {
  id: string;
  revision: number;
  config: AgentConfig;
  created_at: string;
}
export interface StudioAgent {
  id: string;
  config: AgentConfig;
  /** What the runtime resolved this draft to — never re-derived in the UI. */
  skills?: string[];
  tools?: string[];
  systems?: AgentSystems;
  generation: number;
  published_instance_id: string | null;
  issues: string[];
  history: AgentRevision[];
  channel: {
    phone_number: string;
    active: boolean;
    instance_id: string;
    connection_id: string;
  } | null;
}
