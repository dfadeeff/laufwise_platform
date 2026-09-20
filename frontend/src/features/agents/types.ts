export interface Treatment {
  key: string;
  name: string;
  price_eur: number;
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
  consent_policy_id: string;
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
