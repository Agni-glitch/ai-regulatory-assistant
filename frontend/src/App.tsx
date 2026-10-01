import { useEffect, useRef, useState } from "react";
import {
  AuthUser,
  Citation,
  IngestStatus,
  Jurisdiction,
  KnowledgeDocument,
  Message,
  deleteDocument,
  fetchDocumentFileUrl,
  fetchDocuments,
  fetchJurisdictions,
  fetchMe,
  getIngestStatus,
  getReprocessStatus,
  login,
  reprocessDocument,
  sendChat,
  setAccessToken,
  triggerIngest,
  uploadDocument,
} from "./api";

interface ChatTurn extends Message {
  citations?: Citation[];
}

export default function App() {
  const [view, setView] = useState<"chat" | "documents">("chat");
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [loginUsername, setLoginUsername] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("admin123");
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [jurisdictions, setJurisdictions] = useState<Jurisdiction[]>([]);
  const [scope, setScope] = useState<string>("ALL");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // --- Knowledge documents state ---
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [docFilter, setDocFilter] = useState("");
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  // --- Per-row reprocess state: source_path -> task_id ---
  const [reprocessTasks, setReprocessTasks] = useState<Record<string, string>>({});
  const [reprocessErrors, setReprocessErrors] = useState<Record<string, string>>({});

  // --- Per-row delete state ---
  const [deletingRows, setDeletingRows] = useState<Set<string>>(new Set());

  // --- Upload modal state ---
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadJurisdiction, setUploadJurisdiction] = useState("");
  const [uploadNewJur, setUploadNewJur] = useState("");
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadTaskId, setUploadTaskId] = useState<string | null>(null);
  const [uploadPollTick, setUploadPollTick] = useState(0);

  // --- Embedding (ingestion) job state ---
  const [ingest, setIngest] = useState<IngestStatus | null>(null);
  const [ingestReset, setIngestReset] = useState(true);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const ingestActive =
    ingest != null && !["SUCCESS", "FAILURE"].includes(ingest.state);

  const canReadDocuments = hasPrivilege("documents:read");
  const canUploadDocuments = hasPrivilege("documents:upload");
  const canReprocessDocuments = hasPrivilege("documents:reprocess");
  const canDeleteDocuments = hasPrivilege("documents:delete");
  const canRunIngest = hasPrivilege("ingest:run");

  function hasPrivilege(privilege: string) {
    return authUser?.privileges.includes(privilege) ?? false;
  }

  function resetWorkspaceState() {
    setView("chat");
    setTurns([]);
    setDocuments([]);
    setJurisdictions([]);
    setScope("ALL");
    setError(null);
    setDocError(null);
    setIngest(null);
    setIngestError(null);
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoginError(null);
    setLoginLoading(true);
    try {
      const resp = await login(loginUsername, loginPassword);
      setAccessToken(resp.access_token);
      setAuthUser(resp.user);
    } catch (e: any) {
      setLoginError(e.message ?? "Login failed.");
      setAccessToken(null);
    } finally {
      setLoginLoading(false);
    }
  }

  function handleLogout() {
    setAccessToken(null);
    setAuthUser(null);
    resetWorkspaceState();
  }

  function loadJurisdictions() {
    fetchJurisdictions()
      .then(setJurisdictions)
      .catch(() => setJurisdictions([]));
  }

  function loadDocuments() {
    if (!canReadDocuments) return;
    setDocLoading(true);
    setDocError(null);
    fetchDocuments()
      .then(setDocuments)
      .catch((e: any) => setDocError(e.message ?? "Failed to load documents."))
      .finally(() => setDocLoading(false));
  }

  useEffect(() => {
    fetchMe()
      .then(setAuthUser)
      .catch(() => {
        setAccessToken(null);
        setAuthUser(null);
      })
      .finally(() => setAuthChecking(false));
  }, []);

  useEffect(() => {
    if (authUser) loadJurisdictions();
  }, [authUser]);

  useEffect(() => {
    if (view === "documents" && canReadDocuments) loadDocuments();
  }, [view, canReadDocuments]);

  // Poll active reprocess tasks every 2 s.
  useEffect(() => {
    const activePaths = Object.keys(reprocessTasks);
    if (activePaths.length === 0) return;
    const timer = setTimeout(async () => {
      const updates: Record<string, string> = { ...reprocessTasks };
      for (const path of activePaths) {
        const taskId = reprocessTasks[path];
        try {
          const status = await getReprocessStatus(taskId);
          if (status.state === "SUCCESS" || status.state === "FAILURE") {
            delete updates[path];
            if (status.state === "FAILURE") {
              setReprocessErrors(prev => ({ ...prev, [path]: status.error ?? "Reprocess failed" }));
            } else {
              setReprocessErrors(prev => { const n = { ...prev }; delete n[path]; return n; });
              loadDocuments();
            }
          }
        } catch {
          delete updates[path];
        }
      }
      setReprocessTasks(updates);
    }, 2000);
    return () => clearTimeout(timer);
  }, [reprocessTasks]);

  // Poll the upload embedding task.
  useEffect(() => {
    if (!uploadTaskId) return;
    const timer = setTimeout(async () => {
      try {
        const status = await getReprocessStatus(uploadTaskId);
        if (status.state === "PROGRESS" && status.progress) {
          const p = status.progress as any;
          setUploadProgress(`${p.stage ?? "processing"} (${p.step ?? "-"}/${p.total ?? "-"})`);
        }
        if (status.state === "SUCCESS") {
          setUploadTaskId(null);
          setUploadLoading(false);
          setUploadProgress(null);
          setUploadOpen(false);
          setUploadFile(null);
          setUploadJurisdiction("");
          setUploadNewJur("");
          loadDocuments();
          loadJurisdictions();
        } else if (status.state === "FAILURE") {
          setUploadTaskId(null);
          setUploadLoading(false);
          setUploadProgress(null);
          setUploadError(status.error ?? "Embedding failed");
        } else {
          // Still running — bump tick to trigger next poll.
          setUploadPollTick(t => t + 1);
        }
      } catch {
        setUploadTaskId(null);
        setUploadLoading(false);
        setUploadError("Lost connection to embedding job");
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [uploadTaskId, uploadPollTick]);

  async function handleReprocess(sourcePath: string) {
    setReprocessErrors(prev => { const n = { ...prev }; delete n[sourcePath]; return n; });
    try {
      const { task_id } = await reprocessDocument(sourcePath);
      setReprocessTasks(prev => ({ ...prev, [sourcePath]: task_id }));
    } catch (e: any) {
      setReprocessErrors(prev => ({ ...prev, [sourcePath]: e.message ?? "Failed to start" }));
    }
  }

  async function handleDelete(sourcePath: string, fileName: string) {
    if (!canDeleteDocuments) return;
    if (!window.confirm(`Delete "${fileName}" and remove it from ChromaDB?\nThis cannot be undone.`)) return;
    setDeletingRows(prev => new Set(prev).add(sourcePath));
    try {
      await deleteDocument(sourcePath);
      loadDocuments();
    } catch (e: any) {
      alert(`Delete failed: ${e.message}`);
    } finally {
      setDeletingRows(prev => { const s = new Set(prev); s.delete(sourcePath); return s; });
    }
  }

  async function handleUpload() {
    if (!canUploadDocuments) return;
    if (!uploadFile) return;
    const jur = uploadJurisdiction === "__new__" ? uploadNewJur.trim() : uploadJurisdiction;
    if (!jur) return;
    setUploadError(null);
    setUploadLoading(true);
    setUploadProgress("Uploading file…");
    try {
      const { task_id } = await uploadDocument(uploadFile, jur);
      setUploadProgress("Embedding in progress…");
      setUploadTaskId(task_id);
    } catch (e: any) {
      setUploadError(e.message ?? "Upload failed");
      setUploadLoading(false);
      setUploadProgress(null);
    }
  }

  async function startIngest() {
    if (!canRunIngest) return;
    setIngestError(null);
    try {
      const { task_id } = await triggerIngest(ingestReset);
      setIngest({ task_id, state: "PENDING" });
    } catch (e: any) {
      setIngestError(e.message ?? "Failed to start the embedding job.");
    }
  }

  // Poll the Celery job while it is running.
  useEffect(() => {
    if (!ingest || !ingestActive) return;
    const timer = setTimeout(async () => {
      try {
        const status = await getIngestStatus(ingest.task_id);
        setIngest(status);
        if (status.state === "SUCCESS") {
          loadJurisdictions();
          loadDocuments();
        }
      } catch (e: any) {
        setIngestError(e.message ?? "Lost connection to the job.");
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [ingest, ingestActive]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading]);

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;
    setError(null);
    setInput("");

    const history: Message[] = turns.map((t) => ({
      role: t.role,
      content: t.content,
    }));
    const nextTurns: ChatTurn[] = [...turns, { role: "user", content: question }];
    setTurns(nextTurns);
    setLoading(true);

    try {
      const resp = await sendChat(question, history, scope === "ALL" ? null : scope);
      setTurns([
        ...nextTurns,
        { role: "assistant", content: resp.answer, citations: resp.citations },
      ]);
    } catch (e: any) {
      setError(e.message ?? "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  async function handleViewDocument(sourcePath: string) {
    if (!sourcePath) return;
    try {
      const url = await fetchDocumentFileUrl(sourcePath);
      window.open(url, "_blank", "noopener,noreferrer");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e: any) {
      alert(`Open failed: ${e.message}`);
    }
  }

  if (authChecking) {
    return (
      <div className="auth-screen">
        <div className="auth-panel">
          <div className="brand">
            <h1>ComplyNexus</h1>
            <p>Checking your session…</p>
          </div>
        </div>
      </div>
    );
  }

  if (!authUser) {
    return (
      <div className="auth-screen">
        <form className="auth-panel" onSubmit={handleLogin}>
          <div className="brand">
            <h1>ComplyNexus</h1>
            <p>Sign in with JWT-backed RBAC</p>
          </div>
          <label className="field-label" htmlFor="username">Username</label>
          <input
            id="username"
            className="auth-input"
            value={loginUsername}
            onChange={(e) => setLoginUsername(e.target.value)}
            autoComplete="username"
          />
          <label className="field-label" htmlFor="password">Password</label>
          <input
            id="password"
            className="auth-input"
            type="password"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            autoComplete="current-password"
          />
          {loginError && <div className="error small">{loginError}</div>}
          <button className="auth-submit" disabled={loginLoading || !loginUsername || !loginPassword}>
            {loginLoading ? "Signing in…" : "Sign in"}
          </button>
          <div className="auth-demo">
            Demo users: admin/admin123, privs/privs123, user/user123
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>ComplyNexus</h1>
          <p>AI Regulatory Assistant</p>
        </div>

        <div className="session-card">
          <div>
            <span className="field-label">Signed in</span>
            <strong>{authUser.username}</strong>
          </div>
          <span className={`role-pill role-${authUser.role.toLowerCase()}`}>{authUser.role}</span>
          <button className="ghost" onClick={handleLogout}>Sign out</button>
        </div>

        <nav className="sidebar-nav">
          <button
            className={`nav-item${view === "chat" ? " active" : ""}`}
            onClick={() => setView("chat")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            Chat
          </button>
          {canReadDocuments && (
            <button
              className={`nav-item${view === "documents" ? " active" : ""}`}
              onClick={() => setView("documents")}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              Knowledge Documents
            </button>
          )}
        </nav>

        {view === "chat" && (
          <>
            <label className="field-label">Jurisdiction scope</label>
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="ALL">All jurisdictions</option>
              {jurisdictions.map((j) => (
                <option key={j.jurisdiction} value={j.jurisdiction}>
                  {j.jurisdiction} ({j.documents})
                </option>
              ))}
            </select>
            <button className="ghost" onClick={() => setTurns([])} disabled={loading}>
              New chat
            </button>
          </>
        )}

        {canRunIngest && <div className="ingest">
          <label className="field-label">Knowledge base</label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={ingestReset}
              onChange={(e) => setIngestReset(e.target.checked)}
              disabled={ingestActive}
            />
            Rebuild from scratch
          </label>
          <button className="primary" onClick={startIngest} disabled={ingestActive}>
            {ingestActive ? "Building embeddings…" : "Build embeddings"}
          </button>

          {ingest && (
            <div className="job">
              <div className={`job-state state-${ingest.state.toLowerCase()}`}>
                {ingest.state === "PROGRESS" && ingest.progress
                  ? `Stage ${ingest.progress.step}/${ingest.progress.total}: ${ingest.progress.stage}`
                  : ingest.state}
              </div>
              {ingest.state === "PROGRESS" && ingest.progress?.total ? (
                <div className="bar">
                  <div
                    className="bar-fill"
                    style={{
                      width: `${Math.round(
                        ((ingest.progress.step ?? 0) / ingest.progress.total) * 100
                      )}%`,
                    }}
                  />
                </div>
              ) : null}
              {(ingest.state === "PENDING" || ingest.state === "STARTED") && (
                <div className="bar indeterminate">
                  <div className="bar-fill" />
                </div>
              )}
              {ingest.state === "SUCCESS" && ingest.result && (
                <div className="job-msg ok">
                  Indexed {ingest.result.total_chunks ?? 0} chunks across{" "}
                  {Object.keys(ingest.result.per_jurisdiction ?? {}).length}{" "}
                  jurisdictions.
                </div>
              )}
              {ingest.state === "FAILURE" && (
                <div className="error small">{ingest.error}</div>
              )}
            </div>
          )}
          {ingestError && <div className="error small">{ingestError}</div>}
        </div>}

        <div className="hint">
          Answers are grounded in the indexed regulatory corpus and cite their
          sources. Ask about a country's AI rules, or compare jurisdictions.
        </div>
      </aside>

      <main className="chat">
        {view === "chat" ? (
          <>
            <div className="messages">
              {turns.length === 0 && (
                <div className="empty">
                  <h2>Ask about AI regulation</h2>
                  <ul>
                    <li>"What does the EU AI Act require for high-risk systems?"</li>
                    <li>"Compare AI governance in Singapore and the US."</li>
                    <li>"Does Nepal regulate AI in banking?"</li>
                  </ul>
                </div>
              )}
              {turns.map((t, i) => (
                <div key={i} className={`turn ${t.role}`}>
                  <div className="bubble">
                    <div className="content">{t.content}</div>
                    {t.citations && t.citations.length > 0 && (
                      <div className="citations">
                        <div className="citations-title">Sources</div>
                        {t.citations.map((c, ci) => (
                          <div key={ci} className="citation" title={c.snippet}>
                            <span className="badge">{c.jurisdiction}</span>
                            <span className="file">{c.file_name}</span>
                            {c.page_number != null && (
                              <span className="page">p.{c.page_number}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="turn assistant">
                  <div className="bubble">
                    <div className="thinking">Researching the corpus…</div>
                  </div>
                </div>
              )}
              {error && <div className="error">{error}</div>}
              <div ref={endRef} />
            </div>

            <div className="composer">
              <textarea
                value={input}
                placeholder="Ask a question about AI regulation…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={2}
              />
              <button onClick={handleSend} disabled={loading || !input.trim()}>
                Send
              </button>
            </div>
          </>
        ) : (
          <div className="docs-view">
            <div className="docs-header">
              <div>
                <h2 className="docs-title">Knowledge Document Management</h2>
                <p className="docs-subtitle">
                  Documents processed and stored as embeddings in ChromaDB
                </p>
              </div>
              <div className="docs-header-actions">
                {canUploadDocuments && (
                  <button className="primary" onClick={() => { setUploadError(null); setUploadOpen(true); }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{marginRight:6}}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    Upload Document
                  </button>
                )}
                <button className="ghost" onClick={loadDocuments} disabled={docLoading}>
                  {docLoading ? "Refreshing…" : "Refresh"}
                </button>
              </div>
            </div>

            <div className="docs-toolbar">
              <input
                className="docs-search"
                placeholder="Filter by jurisdiction or file name…"
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
              />
              <span className="docs-count">
                {documents.length === 0
                  ? "No documents indexed"
                  : `${documents.filter(d =>
                      !docFilter ||
                      (d.jurisdiction ?? "").toLowerCase().includes(docFilter.toLowerCase()) ||
                      (d.file_name ?? "").toLowerCase().includes(docFilter.toLowerCase())
                    ).length} of ${documents.length} documents`}
              </span>
            </div>

            {docError && <div className="error">{docError}</div>}

            {docLoading ? (
              <div className="docs-loading">Loading documents…</div>
            ) : documents.length === 0 && !docError ? (
              <div className="docs-empty">
                No documents are indexed yet. Use "Build embeddings" in the sidebar to ingest the corpus.
              </div>
            ) : (
              <div className="docs-table-wrap">
                <table className="docs-table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Jurisdiction</th>
                      <th>File Name</th>
                      <th>Path</th>
                      <th>Pages</th>
                      <th>Chunks</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {documents
                      .filter(d =>
                        !docFilter ||
                        (d.jurisdiction ?? "").toLowerCase().includes(docFilter.toLowerCase()) ||
                        (d.file_name ?? "").toLowerCase().includes(docFilter.toLowerCase())
                      )
                      .map((d, i) => (
                        <tr key={i}>
                          <td>
                            <span className={`status-badge ${d.indexed ? "indexed" : "pending"}`}>
                              {d.indexed ? "Indexed" : "Pending"}
                            </span>
                          </td>
                          <td>
                            <span className="badge">{d.jurisdiction ?? "—"}</span>
                          </td>
                          <td className="doc-filename">{d.file_name ?? "—"}</td>
                          <td className="doc-path">{d.source_path ?? "—"}</td>
                          <td className="doc-num">{d.indexed ? d.pages : "—"}</td>
                          <td className="doc-num">{d.indexed ? d.chunks : "—"}</td>
                          <td className="doc-actions">
                            <button
                              className="doc-view-btn"
                              title="View PDF"
                              onClick={() => handleViewDocument(d.source_path ?? "")}
                            >
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            </button>
                            {canReprocessDocuments && (
                              <button
                                className={`doc-view-btn reprocess-btn${reprocessTasks[d.source_path ?? ""] ? " spinning" : ""}`}
                                title={reprocessTasks[d.source_path ?? ""] ? "Reprocessing…" : "Reprocess embeddings"}
                                disabled={!!reprocessTasks[d.source_path ?? ""]}
                                onClick={() => handleReprocess(d.source_path ?? "")}
                              >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                              </button>
                            )}
                            {reprocessErrors[d.source_path ?? ""] && (
                              <span className="reprocess-error" title={reprocessErrors[d.source_path ?? ""]}>!</span>
                            )}
                            {canDeleteDocuments && (
                              <button
                                className="doc-view-btn delete-btn"
                                title="Delete document"
                                disabled={deletingRows.has(d.source_path ?? "") || !!reprocessTasks[d.source_path ?? ""]}
                                onClick={() => handleDelete(d.source_path ?? "", d.file_name ?? d.source_path ?? "")}
                              >
                                {deletingRows.has(d.source_path ?? "") ? (
                                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                                ) : (
                                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                                )}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </main>

      {/* Upload Document Modal */}
      {uploadOpen && (
        <div className="modal-backdrop" onClick={() => { if (!uploadLoading) { setUploadOpen(false); setUploadError(null); } }}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Upload Document</h3>
              <button className="modal-close" disabled={uploadLoading} onClick={() => { setUploadOpen(false); setUploadError(null); }}>×</button>
            </div>

            <div className="modal-body">
              <label className="field-label">PDF File</label>
              <input
                className="upload-file-input"
                type="file"
                accept=".pdf"
                disabled={uploadLoading}
                onChange={e => setUploadFile(e.target.files?.[0] ?? null)}
              />
              {uploadFile && <p className="upload-filename">{uploadFile.name}</p>}

              <label className="field-label" style={{marginTop: 16}}>Jurisdiction</label>
              <select
                className="upload-select"
                value={uploadJurisdiction}
                disabled={uploadLoading}
                onChange={e => { setUploadJurisdiction(e.target.value); setUploadNewJur(""); }}
              >
                <option value="">Select jurisdiction…</option>
                {jurisdictions.map(j => (
                  <option key={j.jurisdiction} value={j.jurisdiction}>{j.jurisdiction}</option>
                ))}
                <option value="__new__">＋ New jurisdiction…</option>
              </select>

              {uploadJurisdiction === "__new__" && (
                <>
                  <label className="field-label" style={{marginTop: 12}}>New jurisdiction name</label>
                  <input
                    className="upload-select"
                    type="text"
                    placeholder="e.g. CANADA"
                    value={uploadNewJur}
                    disabled={uploadLoading}
                    onChange={e => setUploadNewJur(e.target.value.toUpperCase())}
                  />
                </>
              )}

              {uploadProgress && (
                <div className="upload-progress">
                  <div className="bar indeterminate"><div className="bar-fill" /></div>
                  <span className="upload-progress-label">{uploadProgress}</span>
                </div>
              )}

              {uploadError && <div className="error small" style={{marginTop: 12}}>{uploadError}</div>}
            </div>

            <div className="modal-footer">
              <button
                className="ghost"
                disabled={uploadLoading}
                onClick={() => { setUploadOpen(false); setUploadError(null); }}
              >
                Cancel
              </button>
              <button
                className="primary"
                disabled={
                  uploadLoading ||
                  !uploadFile ||
                  !uploadJurisdiction ||
                  (uploadJurisdiction === "__new__" && !uploadNewJur.trim())
                }
                onClick={handleUpload}
              >
                {uploadLoading ? "Processing…" : "Upload & Embed"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
