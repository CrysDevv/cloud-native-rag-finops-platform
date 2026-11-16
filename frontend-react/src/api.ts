export type EstadoDocumento = "PENDIENTE" | "PROCESANDO" | "INDEXADO" | "ERROR" | "DLQ";

export interface DocumentoOut {
  id: string;
  nombre_archivo: string;
  titulo: string | null;
  tipo_contenido: string | null;
  s3_bucket: string;
  s3_key: string;
  sha256: string;
  tamano_bytes: number | null;
  num_paginas: number | null;
  estado: EstadoDocumento;
  intentos: number | null;
  mensaje_error: string | null;
  correlation_id: string | null;
  pii_detectada: Record<string, unknown> | null;
  creado_en: string;
  actualizado_en: string;
  total_fragmentos: number | null;
}

export interface DocumentoLista {
  total: number;
  limit: number;
  offset: number;
  items: DocumentoOut[];
}

export interface DocumentoCreado {
  documento_id: string;
  estado: EstadoDocumento;
  sha256: string;
  s3_bucket: string;
  s3_key: string;
  sqs_message_id: string;
  correlation_id: string;
}

export interface Cita {
  documento_id: string;
  nombre_archivo: string;
  pagina: number;
  indice_fragmento: number;
  score: number;
  extracto: string;
}

export interface ConsultaResponse {
  respuesta: string;
  citas: Cita[];
  fragmentos_recuperados: number;
  cache_hit: boolean;
  pii_detectada: boolean;
  pii_resumen: Record<string, unknown>;
  pregunta_anonimizada: string;
  latencia_ms: number;
  correlation_id: string;
}

export interface FinopsServicio {
  periodo: string;
  servicio: string;
  consumo_total: number;
  costo_total_usd: number;
  limite_capa_gratuita: number | null;
  uso_capa_gratuita_pct: number | null;
  alerta: boolean;
}

export interface FinopsResumen {
  generado_en: string;
  dias: number;
  presupuesto_usd: number;
  costo_total_usd: number;
  estado_presupuesto: "DENTRO_DEL_PRESUPUESTO" | "PRESUPUESTO_EXCEDIDO";
  servicios: FinopsServicio[];
  colas: Record<string, Record<string, unknown>>;
  sqs_max_receive_count: number;
}

export interface Salud {
  status: "ok" | "degradado" | "inaccesible";
  componentes: Record<string, string>;
}

export class ProblemaError extends Error {
  constructor(
    public readonly status: number,
    public readonly titulo: string,
    public readonly detalle: string,
    public readonly correlationId: string = "",
    public readonly reintentarEn: number | null = null,
  ) {
    super(`${titulo}: ${detalle}`);
    this.name = "ProblemaError";
  }
}

const CLAVE_API = "rag.apiKey";

export function getApiKey(): string {
  try {
    return sessionStorage.getItem(CLAVE_API) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(valor: string): void {
  try {
    if (valor) sessionStorage.setItem(CLAVE_API, valor);
    else sessionStorage.removeItem(CLAVE_API);
  } catch {
    /* almacenamiento no disponible: la clave solo vive en memoria de la sesión actual */
  }
}

function nuevoId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function leerProblema(resp: Response, cid: string): Promise<ProblemaError> {
  let titulo = "Error HTTP";
  let detalle = `La API respondió con el estado ${resp.status}.`;
  let correlacion = resp.headers.get("X-Correlation-ID") ?? cid;
  try {
    const cuerpo = (await resp.json()) as Partial<{ title: string; detail: string; correlation_id: string }>;
    if (cuerpo.title) titulo = cuerpo.title;
    if (cuerpo.detail) detalle = cuerpo.detail;
    if (cuerpo.correlation_id) correlacion = cuerpo.correlation_id;
  } catch {
    /* cuerpo no JSON: se conserva el mensaje genérico */
  }
  const espera = Number(resp.headers.get("Retry-After"));
  return new ProblemaError(resp.status, titulo, detalle, correlacion, Number.isFinite(espera) && espera > 0 ? espera : null);
}

function cabeceras(extra?: HeadersInit): { headers: Headers; cid: string } {
  const headers = new Headers(extra);
  const cid = nuevoId();
  headers.set("X-Correlation-ID", cid);
  headers.set("Accept", "application/json, application/problem+json");
  const clave = getApiKey();
  if (clave) headers.set("X-API-Key", clave);
  return { headers, cid };
}

async function solicitar<T>(ruta: string, init: RequestInit = {}): Promise<T> {
  const { headers, cid } = cabeceras(init.headers);
  let resp: Response;
  try {
    resp = await fetch(ruta, { ...init, headers });
  } catch {
    throw new ProblemaError(0, "Sin conexión", "No fue posible comunicarse con la API.", cid);
  }
  if (!resp.ok) throw await leerProblema(resp, cid);
  return (await resp.json()) as T;
}

export async function salud(): Promise<Salud> {
  try {
    const resp = await fetch("/health", { headers: { Accept: "application/json" } });
    const cuerpo = (await resp.json()) as Partial<Salud>;
    return { status: cuerpo.status ?? "degradado", componentes: cuerpo.componentes ?? {} };
  } catch {
    return { status: "inaccesible", componentes: {} };
  }
}

export function listarDocumentos(p: { estado?: EstadoDocumento; limit: number; offset: number }): Promise<DocumentoLista> {
  const q = new URLSearchParams({ limit: String(p.limit), offset: String(p.offset) });
  if (p.estado) q.set("estado", p.estado);
  return solicitar<DocumentoLista>(`/api/v1/documentos?${q.toString()}`);
}

export function subirDocumento(archivo: File, titulo: string): Promise<DocumentoCreado> {
  const datos = new FormData();
  datos.append("archivo", archivo);
  if (titulo.trim()) datos.append("titulo", titulo.trim());
  return solicitar<DocumentoCreado>("/api/v1/documentos", { method: "POST", body: datos });
}

export function consultar(pregunta: string, topK?: number): Promise<ConsultaResponse> {
  return solicitar<ConsultaResponse>("/api/v1/rag/consultar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(topK ? { pregunta, top_k: topK } : { pregunta }),
  });
}

export function resumenFinops(dias: number): Promise<FinopsResumen> {
  return solicitar<FinopsResumen>(`/api/v1/finops/resumen?dias=${dias}`);
}
