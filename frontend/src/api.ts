// API client for the ComplyNexus backend (proxied at /api by nginx / vite).

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

let accessToken: string | null = localStorage.getItem("authToken");

export function setAccessToken(token: string | null) {
  accessToken = token;
  if (token) localStorage.setItem("authToken", token);
  else localStorage.removeItem("authToken");
}

function authHeaders(extra: HeadersInit = {}): HeadersInit {
  return accessToken ? { ...extra, Authorization: `Bearer ${accessToken}` } : extra;
}

async function parseError(res: Response, fallback: string): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  return new Error(detail.detail ?? fallback);
}

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface AuthUser {
  username: string;
  role: "User" | "Privs" | "Admin" | string;
  privileges: string[];
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
}

export interface Citation {
  file_name?: string;
  jurisdiction?: string;
  page_number?: number;
  source_path?: string;
  score?: number;
  snippet?: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  steps: number;
}

export interface Jurisdiction {
  jurisdiction: string;
  documents: number;
  chunks: number;
}

export interface KnowledgeDocument {
  file_name: string | null;
  source_path: string | null;
  jurisdiction: string | null;
  pages: number;
  chunks: number;
  indexed: boolean;
}

export interface IngestProgress {
  stage?: string;
  step?: number;
  total?: number;
  message?: string;
}

export interface IngestResult {
  total_chunks?: number;
  documents_parsed?: number;
  per_jurisdiction?: Record<string, number>;
  indexed?: boolean;
}

export interface IngestStatus {
  task_id: string;
  state: "PENDING" | "STARTED" | "PROGRESS" | "SUCCESS" | "FAILURE" | string;
  progress?: IngestProgress;
  result?: IngestResult;
  error?: string;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw await parseError(res, `Login failed (${res.status})`);
  return res.json();
}

export async function fetchMe(): Promise<AuthUser> {
  const res = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res, `Failed to load profile (${res.status})`);
  return res.json();
}

export async function triggerIngest(reset: boolean): Promise<{ task_id: string }> {
  const res = await fetch(`${API_BASE}/ingest`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reset }),
  });
  if (!res.ok) throw await parseError(res, `Failed to start job (${res.status})`);
  return res.json();
}

export async function getIngestStatus(taskId: string): Promise<IngestStatus> {
  const res = await fetch(`${API_BASE}/ingest/${taskId}`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res, `Failed to fetch job status (${res.status})`);
  return res.json();
}

export async function fetchJurisdictions(): Promise<Jurisdiction[]> {
  const res = await fetch(`${API_BASE}/jurisdictions`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res, `Failed to load jurisdictions (${res.status})`);
  const data = await res.json();
  return data.jurisdictions ?? [];
}

export async function fetchDocuments(): Promise<KnowledgeDocument[]> {
  const res = await fetch(`${API_BASE}/documents`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res, `Failed to load documents (${res.status})`);
  const data = await res.json();
  return data.documents ?? [];
}

export async function reprocessDocument(sourcePath: string): Promise<{ task_id: string }> {
  const res = await fetch(`${API_BASE}/documents/reprocess`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ source_path: sourcePath }),
  });
  if (!res.ok) throw await parseError(res, `Failed to start reprocess (${res.status})`);
  return res.json();
}

export async function deleteDocument(sourcePath: string): Promise<{ deleted: string; chunks_removed: number }> {
  const res = await fetch(`${API_BASE}/documents/file?path=${encodeURIComponent(sourcePath)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw await parseError(res, `Failed to delete document (${res.status})`);
  return res.json();
}

export async function fetchDocumentFileUrl(sourcePath: string): Promise<string> {
  const res = await fetch(`${API_BASE}/documents/file?path=${encodeURIComponent(sourcePath)}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw await parseError(res, `Failed to open document (${res.status})`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function uploadDocument(
  file: File,
  jurisdiction: string,
): Promise<{ task_id: string; source_path: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("jurisdiction", jurisdiction);
  const res = await fetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw await parseError(res, `Upload failed (${res.status})`);
  return res.json();
}

export async function getReprocessStatus(taskId: string): Promise<IngestStatus> {
  const res = await fetch(`${API_BASE}/documents/reprocess/${taskId}`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res, `Failed to fetch reprocess status (${res.status})`);
  return res.json();
}

export async function sendChat(
  question: string,
  history: Message[],
  jurisdiction: string | null
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question, history, jurisdiction }),
  });
  if (!res.ok) throw await parseError(res, `Request failed (${res.status})`);
  return res.json();
}
