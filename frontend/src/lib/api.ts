export interface CreateGroupResp {
  pin: string; participant_id: string; creator_token: string; status: string;
}
export interface JoinResp { participant_id: string; status: string; }
export interface GroupStateResp {
  pin: string; question: string; status: string;
  expected_count: number | null; participant_count: number; submitted_count: number;
  deadline: string | null; consensus: string | null; is_creator: boolean;
  current_round: number; max_rounds: number;
  cooldown_remaining: number | null;
  // Adaptive per-member questioning (ADAPTIVE_SPEC §7.1/§7.4)
  adaptive_questions: boolean;
  member_question_count: number | null;
}

export interface MyQuestionResp {
  round: number; question: string; is_personal: boolean;
}

export interface RoundInfo {
  round_number: number; question: string; consensus: string | null;
  stance_shift_summary: string | null;
  created_at: string; analyzed_at: string | null;
}

const BASE = "/api";

/** Error class carrying the HTTP status so callers can branch on it. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Read the error message from a non-OK response. FastAPI returns {"detail": "..."},
 *  but we also tolerate {"error": "..."} for forward-compat. */
async function readError(r: Response): Promise<string> {
  try {
    const body = await r.json();
    return body.detail || body.error || r.statusText;
  } catch {
    return r.statusText;
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(await readError(r), r.status);
  return r.json();
}

export const createGroup = (question: string, creator_nickname: string, expected_count?: number, timeout_seconds?: number, max_rounds?: number, adaptive_questions?: boolean) =>
  post<CreateGroupResp>("/groups", { question, creator_nickname, expected_count, timeout_seconds, max_rounds, adaptive_questions });

export const joinGroup = (pin: string, nickname: string) =>
  post<JoinResp>(`/groups/${pin}/join`, { nickname });

export async function getState(pin: string, token?: string): Promise<GroupStateResp> {
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  const r = await fetch(`${BASE}/groups/${pin}/state${q}`);
  if (!r.ok) throw new ApiError(await readError(r), r.status);
  return r.json();
}

export async function getRounds(pin: string): Promise<RoundInfo[]> {
  const r = await fetch(`${BASE}/groups/${pin}/rounds`);
  if (!r.ok) throw new ApiError(await readError(r), r.status);
  return r.json();
}

export const submitResponse = (pin: string, participant_id: string, content: string) =>
  post<{ ok: boolean }>(`/groups/${pin}/responses`, { participant_id, content });

export const startAnalysis = (pin: string, creator_token: string) =>
  post<{ ok: boolean }>(`/groups/${pin}/start`, { creator_token });

export const openNextRound = (pin: string, creator_token: string, question?: string, timeout_seconds?: number) =>
  post<{ round: number; question: string }>(`/groups/${pin}/rounds/next`, { creator_token, question, timeout_seconds });

export const closeGroup = (pin: string, creator_token: string) =>
  post<{ ok: boolean }>(`/groups/${pin}/rounds/close`, { creator_token });

export interface LeaveResp { ok: boolean; dissolved: boolean; }
export const leaveGroup = (pin: string, participant_id: string, creator_token?: string) =>
  post<LeaveResp>(`/groups/${pin}/leave`, { participant_id, creator_token });

/** ADAPTIVE_SPEC §7.2: the ONLY read path for one's own personalized question.
 *  Invalid pid still gets 200 + anchor (is_personal=false) — never a 404. */
export async function getMyQuestion(pin: string, participant_id: string, round?: number): Promise<MyQuestionResp> {
  const q = round ? `&round=${round}` : "";
  const r = await fetch(`${BASE}/groups/${pin}/my-question?participant_id=${encodeURIComponent(participant_id)}${q}`);
  if (!r.ok) throw new ApiError(await readError(r), r.status);
  return r.json();
}
