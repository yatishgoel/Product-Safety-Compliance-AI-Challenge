import {
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

type IconName =
  | "arrow"
  | "check"
  | "chevron"
  | "close"
  | "document"
  | "flask"
  | "image"
  | "info"
  | "pdf"
  | "refresh"
  | "scan"
  | "shield"
  | "upload"
  | "warning";

type VerdictStatus = "Accepted" | "Rejected" | "Needs Review";
type ViewState = "idle" | "loading" | "success" | "error";
type FileKind = "text" | "pdf" | "image";

interface Evidence {
  ingredient: string;
  forbidden_entry: string;
  matched_by: string;
  confidence: string;
}

interface Verdict {
  product_name: string | null;
  status: VerdictStatus;
  reason: string[];
  evidence: Evidence[];
  unread: string[];
  forbidden_list_version: string;
  ingredients: string[];
  extraction_source: string | null;
}

interface PolicyEntry {
  name: string;
  status: "resolved" | "unresolved";
}

interface PolicyData {
  version: string;
  summary: {
    entries: number;
    resolved: number;
    unresolved: number;
    match_keys: number;
  };
  entries: PolicyEntry[];
}

interface ExtractorOption {
  id: string;
  name: string;
  description: string;
  available: boolean;
  supports: FileKind[];
  hint: string;
}

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const ACCEPTED_PRODUCT_TYPES = ".txt,.csv,.md,.pdf,.png,.jpg,.jpeg";

/** Only what the client can be certain of when the server never answered. */
const fallbackExtractors: ExtractorOption[] = [
  {
    id: "auto",
    name: "Automatic",
    description: "Picks the cheapest engine that can read this file.",
    available: true,
    supports: ["text", "pdf", "image"],
    hint: "",
  },
];

const sourceNames: Record<string, string> = {
  text: "Built-in text parser",
  pdf: "PDF text extraction",
  tesseract: "Tesseract OCR",
  gemini: "Gemini Vision",
};

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  const paths: Record<IconName, ReactNode> = {
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></>,
    chevron: <path d="m7 10 5 5 5-5" />,
    close: <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></>,
    document: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v5h4M10 12h5M10 16h5" /></>,
    flask: <><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3" /><path d="M8 15h8" /></>,
    image: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="9" r="1.5" /><path d="m4 17 5-5 4 4 2-2 5 4" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
    pdf: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v5h4M9.5 16v-4h1.2a1.2 1.2 0 0 1 0 2.4H9.5M13 16v-4h1a2 2 0 0 1 0 4z" /></>,
    refresh: <><path d="M20 7v5h-5" /><path d="M18.3 16a8 8 0 1 1 .5-7.5L20 12" /></>,
    scan: <><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4" /><path d="M7 12h10" /></>,
    shield: <><path d="M12 3 5 6v5c0 4.7 2.8 8.3 7 10 4.2-1.7 7-5.3 7-10V6z" /><path d="m9 12 2 2 4-4" /></>,
    upload: <><path d="M12 16V4M7.5 8.5 12 4l4.5 4.5" /><path d="M5 13v6h14v-6" /></>,
    warning: <><path d="M10.3 4.4 3.1 17a2 2 0 0 0 1.7 3h14.4a2 2 0 0 0 1.7-3L13.7 4.4a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h.01" /></>,
  };

  return <svg {...common}>{paths[name]}</svg>;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileKind(file: File): FileKind {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (extension === "pdf") return "pdf";
  if (["png", "jpg", "jpeg"].includes(extension ?? "")) return "image";
  return "text";
}

function getFileIcon(file: File): IconName {
  const kind = getFileKind(file);
  return kind === "pdf" ? "pdf" : kind === "image" ? "image" : "document";
}

function isValidProduct(file: File) {
  const suffix = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  return file.size <= MAX_FILE_BYTES && ACCEPTED_PRODUCT_TYPES.split(",").includes(suffix);
}

function getMatchDescription(evidence: Evidence) {
  if (evidence.matched_by === "literal" && evidence.confidence === "certain") {
    return { label: "Exact match", detail: "The label text directly matches the forbidden entry.", exact: true };
  }
  const methods: Record<string, { label: string; detail: string }> = {
    literal: { label: "Known synonym", detail: "A recognized chemical synonym matched the forbidden entry." },
    hill: { label: "Formula match", detail: "The normalized molecular formula points to the same substance." },
    ocr_variant: { label: "OCR correction", detail: "A likely OCR character error was normalized before matching." },
    inchikey: { label: "Structure match", detail: "The chemical structure identifier is identical." },
    inchikey_parent: { label: "Parent compound", detail: "The ingredient resolves to the same parent compound or salt." },
    pubchem_cid: { label: "PubChem identity", detail: "Both names resolve to the same PubChem compound." },
    llm: { label: "AI-assisted match", detail: "The relationship was identified through chemical reasoning." },
  };
  return { ...(methods[evidence.matched_by] ?? { label: "Normalized match", detail: "The ingredient matched after normalization." }), exact: false };
}

function App() {
  const reduceMotion = useReducedMotion();
  const policyInputRef = useRef<HTMLInputElement>(null);
  const productInputRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLElement>(null);
  const [policyFile, setPolicyFile] = useState<File | null>(null);
  const [productFile, setProductFile] = useState<File | null>(null);
  const [policyData, setPolicyData] = useState<PolicyData | null>(null);
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policyError, setPolicyError] = useState("");
  const [extractors, setExtractors] = useState<ExtractorOption[]>(fallbackExtractors);
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [viewState, setViewState] = useState<ViewState>("idle");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [error, setError] = useState("");
  const [dragTarget, setDragTarget] = useState<"policy" | "product" | null>(null);
  const [engineOnline, setEngineOnline] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      fetch("/policy", { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error("Policy unavailable");
        return response.json() as Promise<PolicyData>;
      }),
      fetch("/extractors", { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error("Extractor catalogue unavailable");
        return response.json() as Promise<{ extractors: ExtractorOption[] }>;
      }),
      fetch("/health", { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error("Engine unavailable");
        return response.json();
      }),
    ]).then(([policyResult, extractorResult, healthResult]) => {
      if (policyResult.status === "fulfilled") setPolicyData(policyResult.value);
      else setPolicyError("The default forbidden list could not be loaded.");
      // A failed catalogue used to fall back to a short hardcoded list, which hid Tesseract,
      // and Gemini behind what looked like a deliberate choice. Say so instead.
      if (extractorResult.status === "fulfilled") setExtractors(extractorResult.value.extractors);
      else setCatalogueFailed(true);
      setEngineOnline(healthResult.status === "fulfilled");
      setPolicyLoading(false);
    });
    return () => controller.abort();
  }, []);

  const productKind = productFile ? getFileKind(productFile) : null;

  // The picker is gone, but losing the "needs setup" badge with it would hide a real problem:
  // without a vision key the pipeline has nowhere to escalate, so a garbled photo becomes a
  // review task instead of a verdict. Say that once, plainly, rather than making it a setting.
  const readerNotice = useMemo(() => {
    if (catalogueFailed) {
      return "The server did not report which readers it has, so this page cannot warn you if vision is unconfigured. Screening still runs.";
    }
    const gemini = extractors.find((option) => option.id === "gemini");
    if (!gemini || gemini.available) return "";
    return `Gemini Vision is not configured, so photos the local OCR garbles are sent for review rather than re-read. ${gemini.hint}`.trim();
  }, [extractors, catalogueFailed]);

  function clearResult() {
    setVerdict(null);
    setError("");
    setViewState("idle");
  }

  async function inspectPolicy(file: File) {
    setPolicyLoading(true);
    setPolicyError("");
    const body = new FormData();
    body.append("forbidden_list", file);
    try {
      const response = await fetch("/inspect_policy", { method: "POST", body });
      const data = (await response.json()) as PolicyData & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "The forbidden list could not be parsed.");
      setPolicyFile(file);
      setPolicyData(data);
      clearResult();
    } catch (requestError) {
      setPolicyError(requestError instanceof Error ? requestError.message : "The forbidden list could not be parsed.");
    } finally {
      setPolicyLoading(false);
    }
  }

  async function restoreDefaultPolicy() {
    setPolicyLoading(true);
    setPolicyError("");
    try {
      const response = await fetch("/policy");
      if (!response.ok) throw new Error("The default forbidden list is unavailable.");
      setPolicyData((await response.json()) as PolicyData);
      setPolicyFile(null);
      if (policyInputRef.current) policyInputRef.current.value = "";
      clearResult();
    } catch (requestError) {
      setPolicyError(requestError instanceof Error ? requestError.message : "The default forbidden list is unavailable.");
    } finally {
      setPolicyLoading(false);
    }
  }

  function chooseProduct(file: File) {
    clearResult();
    if (!isValidProduct(file)) {
      setError(
        file.size > MAX_FILE_BYTES
          ? `${file.name} is larger than the 20 MB upload limit.`
          : "Choose a TXT, CSV, Markdown, PDF, PNG or JPEG product file.",
      );
      setViewState("error");
      return;
    }
    const kind = getFileKind(file);
    setProductFile(file);
  }

  function onPolicyDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragTarget(null);
    const file = event.dataTransfer.files?.[0];
    if (file) inspectPolicy(file);
  }

  function onProductDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragTarget(null);
    const file = event.dataTransfer.files?.[0];
    if (file) chooseProduct(file);
  }

  async function runScreening() {
    if (!productFile || policyLoading || viewState === "loading") return;
    setViewState("loading");
    setVerdict(null);
    setError("");
    const body = new FormData();
    body.append("file", productFile);
    if (policyFile) body.append("forbidden_list", policyFile);

    try {
      const response = await fetch("/evaluate_product", { method: "POST", body });
      const data = (await response.json()) as Verdict & { detail?: string };
      if (!response.ok && response.status !== 202) {
        throw new Error(data.detail || "The product could not be screened.");
      }
      setVerdict(data);
      setViewState("success");
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }), 80);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The product could not be screened.");
      setViewState("error");
    }
  }

  const enter = reduceMotion
    ? { initial: false as const, animate: { opacity: 1 } }
    : { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.38, ease: [0.22, 1, 0.36, 1] as const } };

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <div className="brand"><span><Icon name="shield" size={20} /></span><strong>Clearance</strong></div>
          <div className={`engine-status ${engineOnline ? "online" : ""}`}><span />{engineOnline ? "Screening engine ready" : "Connecting to engine"}</div>
        </div>
      </header>

      <main className="page">
        <motion.section className="intro" {...enter}>
          <h1>Screen product ingredients</h1>
          <p>Upload your forbidden list and a product label. We’ll extract every ingredient, explain each match, and return a clear decision.</p>
        </motion.section>

        <motion.div className="screening-form" {...enter} transition={{ ...enter.transition, delay: reduceMotion ? 0 : 0.06 }}>
          <section className="form-section" aria-labelledby="policy-heading">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div><h2 id="policy-heading">Forbidden ingredient list</h2><p>CSV, JSON or plain text</p></div>
            </div>

            <div
              className={`compact-picker ${dragTarget === "policy" ? "dragging" : ""}`}
              role="button" tabIndex={0}
              onClick={() => policyInputRef.current?.click()}
              onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") policyInputRef.current?.click(); }}
              onDragEnter={(event) => { event.preventDefault(); setDragTarget("policy"); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragTarget(null)}
              onDrop={onPolicyDrop}
            >
              <span className="picker-icon policy"><Icon name="flask" size={19} /></span>
              <div>
                <strong>{policyFile?.name ?? "Default forbidden list"}</strong>
                <span>{policyLoading ? "Parsing ingredients…" : policyData ? `${policyData.summary.entries} ingredients parsed` : "Choose a policy file"}</span>
              </div>
              <span className="picker-action"><Icon name="upload" size={15} /> {policyFile ? "Replace" : "Upload"}</span>
            </div>
            <input ref={policyInputRef} className="visually-hidden" type="file" accept=".csv,.json,.txt" onChange={(event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) inspectPolicy(file); }} />

            <AnimatePresence mode="wait">
              {policyData && !policyLoading && (
                <motion.div
                  className="policy-preview"
                  key={policyData.version}
                  initial={reduceMotion ? false : { opacity: 0, clipPath: "inset(0 0 12% 0)" }}
                  animate={{ opacity: 1, clipPath: "inset(0 0 0% 0)" }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: reduceMotion ? 0 : 0.28, ease: [0.25, 1, 0.5, 1] }}
                >
                  <div className="preview-heading"><span><Icon name="check" size={14} /> Parsed successfully</span>{policyFile && <button type="button" onClick={restoreDefaultPolicy}>Use default list</button>}</div>
                  <div className="policy-list" aria-label="Forbidden ingredients">
                    {policyData.entries.map((entry, index) => (
                      <motion.span
                        key={entry.name}
                        initial={reduceMotion ? false : { opacity: 0, scale: 0.96 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.2, delay: reduceMotion ? 0 : Math.min(index, 10) * 0.025 }}
                        className={entry.status === "unresolved" ? "unresolved" : ""}
                      >
                        {entry.name}{entry.status === "unresolved" && <Icon name="warning" size={12} />}
                      </motion.span>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            {policyError && <div className="inline-error" role="alert"><Icon name="warning" size={17} />{policyError}</div>}
          </section>

          <section className="form-section product-section" aria-labelledby="product-heading">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div><h2 id="product-heading">Product label</h2><p>Text, PDF or image up to 20 MB</p></div>
            </div>

            <AnimatePresence mode="wait">
              {productFile ? (
                <motion.div className="selected-product" key={productFile.name} initial={reduceMotion ? false : { opacity: 0, y: 7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.2 }}>
                  <span className="picker-icon"><Icon name={getFileIcon(productFile)} size={20} /></span>
                  <div><strong>{productFile.name}</strong><span>{getFileKind(productFile).toUpperCase()} · {formatBytes(productFile.size)}</span></div>
                  <span className="file-ready"><Icon name="check" size={14} /> Ready</span>
                  <button className="icon-button" type="button" aria-label={`Remove ${productFile.name}`} onClick={() => { setProductFile(null); if (productInputRef.current) productInputRef.current.value = ""; clearResult(); }}><Icon name="close" size={18} /></button>
                </motion.div>
              ) : (
                <motion.div
                  className={`product-picker ${dragTarget === "product" ? "dragging" : ""}`}
                  key="picker" role="button" tabIndex={0}
                  initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }}
                  onClick={() => productInputRef.current?.click()}
                  onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") productInputRef.current?.click(); }}
                  onDragEnter={(event) => { event.preventDefault(); setDragTarget("product"); }}
                  onDragOver={(event) => event.preventDefault()}
                  onDragLeave={() => setDragTarget(null)}
                  onDrop={onProductDrop}
                >
                  <span><Icon name="upload" size={23} /></span>
                  <div><strong>{dragTarget === "product" ? "Release to add this label" : "Drop a product label here"}</strong><p>or <em>browse your computer</em></p><small>TXT, CSV, Markdown, PDF, PNG or JPEG</small></div>
                </motion.div>
              )}
            </AnimatePresence>
            <input ref={productInputRef} className="visually-hidden" type="file" accept={ACCEPTED_PRODUCT_TYPES} onChange={(event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) chooseProduct(file); }} />

            {error && viewState === "error" && <div className="inline-error" role="alert"><Icon name="warning" size={17} />{error}</div>}
          </section>

          {/* No engine picker. The pipeline already chooses the cheapest reader that can handle
              the file and escalates only when the chemistry it produced is impossible, which
              beat every fixed choice on the sample labels. Forcing one engine stays available
              through the API and the CLI, where it belongs as a diagnostic. All that is left
              here is a note when a reader the pipeline would have used is not configured. */}
          {readerNotice && (
            <p className="reader-notice">
              <Icon name="warning" size={15} />
              {readerNotice}
            </p>
          )}

          <div className="action-row">
            <span><Icon name="shield" size={15} /> Processed only for this check</span>
            <motion.button className="primary-button" type="button" disabled={!productFile || !policyData || policyLoading || viewState === "loading"} onClick={runScreening} whileTap={reduceMotion ? undefined : { scale: 0.98 }}>
              {viewState === "loading" ? <><span className="spinner" />Reading label…</> : <>Run compliance check <Icon name="arrow" size={17} /></>}
            </motion.button>
          </div>
        </motion.div>

        <AnimatePresence mode="wait">
          {viewState === "loading" && <AnalysisProgress reduceMotion={Boolean(reduceMotion)} />}
          {viewState === "success" && verdict && <ResultView verdict={verdict} resultRef={resultRef} reduceMotion={Boolean(reduceMotion)} onReset={() => { setProductFile(null); setVerdict(null); setViewState("idle"); if (productInputRef.current) productInputRef.current.value = ""; window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" }); }} />}
        </AnimatePresence>
      </main>
    </div>
  );
}

function AnalysisProgress({ reduceMotion }: { reduceMotion: boolean }) {
  return (
    <motion.section className="analysis-progress" initial={reduceMotion ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
      <span className="analysis-icon"><Icon name="scan" size={22} /></span>
      <div><strong>Reading and comparing ingredients</strong><p>Reading the label, then checking every ingredient against your forbidden list.</p></div>
      <span className="progress-line"><motion.span initial={{ x: "-100%" }} animate={{ x: "270%" }} transition={{ duration: reduceMotion ? 0.01 : 1.4, repeat: Infinity, ease: "easeInOut" }} /></span>
    </motion.section>
  );
}

function ResultView({ verdict, resultRef, reduceMotion, onReset }: { verdict: Verdict; resultRef: React.RefObject<HTMLElement | null>; reduceMotion: boolean; onReset: () => void }) {
  const matchedIngredients = new Set(verdict.evidence.map((item) => item.ingredient.toLowerCase()));
  const statusClass = verdict.status.toLowerCase().replaceAll(" ", "-");
  const statusIcon: IconName = verdict.status === "Accepted" ? "check" : verdict.status === "Rejected" ? "close" : "warning";

  return (
    <motion.section ref={resultRef} className="results" initial={reduceMotion ? false : { opacity: 0, y: 16, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: reduceMotion ? 0 : 0.38, ease: [0.22, 1, 0.36, 1] }}>
      <div className="result-header">
        <span className="result-file-icon"><Icon name="document" size={21} /></span>
        <div><p>Analysis complete</p><h2>{verdict.product_name || "Unnamed product"}</h2><span>Extracted with {sourceNames[verdict.extraction_source ?? ""] ?? verdict.extraction_source ?? "automatic selection"}</span></div>
        <button className="secondary-button" type="button" onClick={onReset}><Icon name="refresh" size={15} /> Check another file</button>
      </div>

      <Tabs.Root className="result-tabs" defaultValue="ingredients">
        <Tabs.List aria-label="Analysis details">
          <Tabs.Trigger value="ingredients">All ingredients <span>{verdict.ingredients.length}</span></Tabs.Trigger>
          <Tabs.Trigger value="matches">Forbidden matches <span className={verdict.evidence.length ? "has-matches" : ""}>{verdict.evidence.length}</span></Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="ingredients">
          <div className="ingredients-heading"><div><h3>Ingredients found in the label</h3><p>Matched ingredients are highlighted.</p></div><span>{verdict.ingredients.length} detected</span></div>
          <div className="detected-list">
            {verdict.ingredients.map((ingredient, index) => {
              const matched = matchedIngredients.has(ingredient.toLowerCase());
              return <motion.span key={`${ingredient}-${index}`} className={matched ? "matched" : ""} initial={reduceMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: reduceMotion ? 0 : Math.min(index, 12) * 0.025 }}><span>{ingredient}</span>{matched && <Icon name="warning" size={13} />}</motion.span>;
            })}
            {!verdict.ingredients.length && <div className="empty-detail">No readable ingredients were extracted.</div>}
          </div>
          {verdict.evidence.length > 0 && (
            <div className="match-summary" aria-label="Ingredients matched with the forbidden list">
              <div className="match-summary-label"><span><Icon name="warning" size={14} /></span><div><strong>Matched with forbidden list</strong><small>{verdict.evidence.length} {verdict.evidence.length === 1 ? "ingredient" : "ingredients"} caused this result</small></div></div>
              <div className="match-pairs">
                {verdict.evidence.map((item, index) => (
                  <motion.span className="match-pair" key={`${item.ingredient}-${item.forbidden_entry}-${index}`} initial={reduceMotion ? false : { opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: reduceMotion ? 0 : 0.08 + index * 0.035 }}>
                    <code>{item.ingredient}</code><Icon name="arrow" size={13} /><code>{item.forbidden_entry}</code>
                  </motion.span>
                ))}
              </div>
            </div>
          )}
        </Tabs.Content>

        <Tabs.Content value="matches">
          {verdict.evidence.length ? (
            <div className="match-list">
              {verdict.evidence.map((item, index) => {
                const match = getMatchDescription(item);
                return (
                  <motion.div className="match-row" key={`${item.ingredient}-${index}`} initial={reduceMotion ? false : { opacity: 0, x: -7 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: reduceMotion ? 0 : index * 0.04 }}>
                    <span className="match-alert"><Icon name="warning" size={17} /></span>
                    <div className="match-main"><span><code>{item.ingredient}</code><Icon name="arrow" size={14} /><code>{item.forbidden_entry}</code></span><strong>{match.label}</strong><p>{match.detail}</p></div>
                    <div className="match-badges"><span className={match.exact ? "exact" : "normalized"}>{match.exact ? "Exact" : "Not exact"}</span><span>{item.confidence}</span></div>
                  </motion.div>
                );
              })}
            </div>
          ) : (
            <div className="no-matches"><span><Icon name="check" size={22} /></span><div><strong>No forbidden matches</strong><p>None of the extracted ingredients matched this policy.</p></div></div>
          )}
        </Tabs.Content>
      </Tabs.Root>

      <motion.div className={`verdict-panel ${statusClass}`} initial={reduceMotion ? false : { opacity: 0, scale: 0.985 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: reduceMotion ? 0 : 0.12, duration: 0.3 }}>
        <motion.span className="verdict-icon" initial={reduceMotion ? false : { scale: 0.8, rotate: -6 }} animate={{ scale: 1, rotate: 0 }} transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}><Icon name={statusIcon} size={27} /></motion.span>
        <div className="verdict-copy"><span>Final decision</span><h3>{verdict.status}</h3><p>{verdict.status === "Accepted" ? "No forbidden ingredients were found." : verdict.status === "Rejected" ? "This product contains ingredients prohibited by the active policy." : "The result needs a person to verify it before release."}</p></div>
        <div className="reason-list">
          <strong>Reason</strong>
          {verdict.reason.length ? verdict.reason.map((reason) => <p key={reason}><span />{reason}</p>) : <p><span />No policy violations found.</p>}
        </div>
      </motion.div>
    </motion.section>
  );
}

export default App;
