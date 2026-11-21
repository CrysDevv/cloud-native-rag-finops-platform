import { StrictMode, useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import * as api from "./api";

type Pestana = "consulta" | "documentos" | "finops";

const LIMITE_PAGINA = 10;
const ESTADOS: api.EstadoDocumento[] = ["PENDIENTE", "PROCESANDO", "INDEXADO", "ERROR", "DLQ"];
const COLOR_ESTADO: Record<api.EstadoDocumento, string> = {
  INDEXADO: "bg-emerald-100 text-emerald-800",
  PENDIENTE: "bg-amber-100 text-amber-800",
  PROCESANDO: "bg-sky-100 text-sky-800",
  ERROR: "bg-red-100 text-red-800",
  DLQ: "bg-purple-100 text-purple-800",
};

const aProblema = (e: unknown): api.ProblemaError =>
  e instanceof api.ProblemaError
    ? e
    : new api.ProblemaError(0, "Error inesperado", e instanceof Error ? e.message : String(e));

const formatoBytes = (n: number | null | undefined): string => {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
};
const formatoFecha = (iso: string): string => new Date(iso).toLocaleString("es-CL");
const usd = (n: number): string => `US$ ${n.toFixed(4)}`;

function Tarjeta({ titulo, children }: { titulo?: string; children: ReactNode }) {
  return (
    <section className="surface p-5">
      {titulo && <h2 className="mb-4 text-sm font-semibold text-slate-700">{titulo}</h2>}
      {children}
    </section>
  );
}

function Insignia({ clase, children }: { clase: string; children: ReactNode }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${clase}`}>{children}</span>;
}

function Alerta({ error }: { error: api.ProblemaError | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <p className="font-semibold">
        {error.titulo}
        {error.status > 0 && <span className="ml-2 font-normal">(HTTP {error.status})</span>}
      </p>
      <p>{error.detalle}</p>
      {error.status === 401 && <p className="mt-1">Ingrese una API key válida en el campo superior.</p>}
      {error.reintentarEn !== null && <p className="mt-1">Reintente en {error.reintentarEn} s.</p>}
      {error.correlationId && <p className="mt-1 font-mono text-xs text-red-600">X-Correlation-ID: {error.correlationId}</p>}
    </div>
  );
}

function Salud() {
  const [salud, setSalud] = useState<api.Salud | null>(null);
  useEffect(() => {
    let vivo = true;
    const consultar = async () => {
      const r = await api.salud();
      if (vivo) setSalud(r);
    };
    void consultar();
    const t = setInterval(() => void consultar(), 30000);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, []);
  const color = salud?.status === "ok" ? "bg-emerald-500" : salud === null ? "bg-slate-300" : "bg-red-500";
  const detalle = salud ? Object.entries(salud.componentes).map(([k, v]) => `${k}: ${v}`).join(" · ") : "Verificando…";
  return (
    <span className="health-copy inline-flex items-center gap-2 text-sm text-slate-600" title={detalle}>
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} aria-hidden="true" />
      API: {salud?.status ?? "verificando"}
    </span>
  );
}

function ClaveApi({ alGuardar }: { alGuardar: () => void }) {
  const [valor, setValor] = useState("");
  const [activa, setActiva] = useState(api.getApiKey() !== "");
  const guardar = (e: FormEvent) => {
    e.preventDefault();
    api.setApiKey(valor.trim());
    setActiva(valor.trim() !== "");
    setValor("");
    alGuardar();
  };
  const limpiar = () => {
    api.setApiKey("");
    setActiva(false);
    alGuardar();
  };
  return (
    <form onSubmit={guardar} className="flex items-center gap-2">
      <label htmlFor="api-key" className="sr-only">Clave de API</label>
      <input
        id="api-key"
        type="password"
        autoComplete="off"
        value={valor}
        onChange={(e) => setValor(e.target.value)}
        placeholder={activa ? "Clave activa" : "API key"}
        className="field hidden w-56 px-3 py-2 text-sm sm:block"
      />
      <button type="submit" className="secondary-button !min-h-9" aria-label="Guardar clave de API">
        Guardar
      </button>
      {activa && (
        <button type="button" onClick={limpiar} className="secondary-button !min-h-9" aria-label="Quitar clave de API">
          Quitar
        </button>
      )}
    </form>
  );
}

function Consulta() {
  const [pregunta, setPregunta] = useState("");
  const [topK, setTopK] = useState("");
  const [resultado, setResultado] = useState<api.ConsultaResponse | null>(null);
  const [error, setError] = useState<api.ProblemaError | null>(null);
  const [cargando, setCargando] = useState(false);

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (pregunta.trim().length < 3) return;
    setCargando(true);
    setError(null);
    try {
      setResultado(await api.consultar(pregunta.trim(), topK ? Number(topK) : undefined));
    } catch (err) {
      setResultado(null);
      setError(aProblema(err));
    } finally {
      setCargando(false);
    }
  };

  return (
    <div className="space-y-4">
      <Tarjeta titulo="Consulta RAG">
        <form onSubmit={enviar} className="space-y-3">
          <textarea
            value={pregunta}
            onChange={(e) => setPregunta(e.target.value)}
            maxLength={2000}
            rows={3}
            required
            aria-label="Pregunta"
            placeholder="Escriba su pregunta sobre los documentos indexados…"
            className="w-full rounded border border-slate-300 p-2 text-sm"
          />
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm text-slate-600">
              Fragmentos (top_k){" "}
              <input
                type="number"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(e.target.value)}
                placeholder="5"
                className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
            <button
              type="submit"
              disabled={cargando || pregunta.trim().length < 3}
              className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {cargando ? "Consultando…" : "Consultar"}
            </button>
          </div>
        </form>
      </Tarjeta>
      <Alerta error={error} />
      {resultado && (
        <>
          <Tarjeta titulo="Respuesta">
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{resultado.respuesta}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <Insignia clase={resultado.cache_hit ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-700"}>
                {resultado.cache_hit ? "Caché semántica" : "Sin caché"}
              </Insignia>
              {resultado.pii_detectada && <Insignia clase="bg-amber-100 text-amber-800">PII anonimizada</Insignia>}
              <span>{resultado.fragmentos_recuperados} fragmentos</span>
              <span>{resultado.latencia_ms} ms</span>
              <span className="font-mono">{resultado.correlation_id}</span>
            </div>
            {resultado.pii_detectada && (
              <p className="mt-2 text-xs text-slate-500">
                Pregunta procesada: <span className="font-mono">{resultado.pregunta_anonimizada}</span>
              </p>
            )}
          </Tarjeta>
          <Tarjeta titulo={`Citas (${resultado.citas.length})`}>
            {resultado.citas.length === 0 ? (
              <p className="text-sm text-slate-500">No se encontraron fragmentos relevantes.</p>
            ) : (
              <ul className="space-y-3">
                {resultado.citas.map((c) => (
                  <li key={`${c.documento_id}-${c.indice_fragmento}`} className="rounded border border-slate-200 p-3 text-sm">
                    <p className="font-medium">
                      {c.nombre_archivo}{" "}
                      <span className="font-normal text-slate-500">
                        · pág. {c.pagina} · fragmento {c.indice_fragmento} · score {c.score.toFixed(4)}
                      </span>
                    </p>
                    <p className="mt-1 text-slate-700">{c.extracto}</p>
                  </li>
                ))}
              </ul>
            )}
          </Tarjeta>
        </>
      )}
    </div>
  );
}

function Documentos() {
  const [datos, setDatos] = useState<api.DocumentoLista | null>(null);
  const [estado, setEstado] = useState<api.EstadoDocumento | "">("");
  const [offset, setOffset] = useState(0);
  const [cargando, setCargando] = useState(false);
  const [subiendo, setSubiendo] = useState(false);
  const [error, setError] = useState<api.ProblemaError | null>(null);
  const [aviso, setAviso] = useState("");

  const cargar = useCallback(
    async (silencioso = false) => {
      if (!silencioso) setCargando(true);
      try {
        setDatos(await api.listarDocumentos({ estado: estado || undefined, limit: LIMITE_PAGINA, offset }));
        setError(null);
      } catch (e) {
        setError(aProblema(e));
      } finally {
        if (!silencioso) setCargando(false);
      }
    },
    [estado, offset],
  );

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const hayActivos = datos?.items.some((d) => d.estado === "PENDIENTE" || d.estado === "PROCESANDO") ?? false;
  useEffect(() => {
    if (!hayActivos) return;
    const t = setInterval(() => void cargar(true), 5000);
    return () => clearInterval(t);
  }, [hayActivos, cargar]);

  const subir = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formulario = e.currentTarget;
    const entrada = new FormData(formulario);
    const archivo = entrada.get("archivo");
    if (!(archivo instanceof File) || archivo.size === 0) return;
    setSubiendo(true);
    setError(null);
    setAviso("");
    try {
      const creado = await api.subirDocumento(archivo, String(entrada.get("titulo") ?? ""));
      setAviso(`Documento encolado (${creado.estado}). ID: ${creado.documento_id}`);
      formulario.reset();
      setOffset(0);
      await cargar(true);
    } catch (err) {
      setError(aProblema(err));
    } finally {
      setSubiendo(false);
    }
  };

  const total = datos?.total ?? 0;
  return (
    <div className="space-y-4">
      <Tarjeta titulo="Subir documento">
        <form onSubmit={subir} className="flex flex-wrap items-end gap-3">
          <label className="text-sm text-slate-600">
            Archivo
            <input name="archivo" type="file" required className="mt-1 block text-sm" />
          </label>
          <label className="text-sm text-slate-600">
            Título (opcional)
            <input name="titulo" type="text" maxLength={300} className="mt-1 block w-64 rounded border border-slate-300 px-2 py-1 text-sm" />
          </label>
          <button
            type="submit"
            disabled={subiendo}
            className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {subiendo ? "Subiendo…" : "Subir"}
          </button>
        </form>
        {aviso && <p className="mt-3 text-sm text-emerald-700">{aviso}</p>}
      </Tarjeta>
      <Alerta error={error} />
      <Tarjeta titulo={`Documentos (${total})`}>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <label className="text-sm text-slate-600">
            Estado{" "}
            <select
              value={estado}
              onChange={(e) => {
                setEstado(e.target.value as api.EstadoDocumento | "");
                setOffset(0);
              }}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">Todos</option>
              {ESTADOS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void cargar()} className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100">
            Actualizar
          </button>
          {cargando && <span className="text-sm text-slate-500">Cargando…</span>}
          {hayActivos && <span className="text-sm text-sky-700">Actualización automática activa</span>}
        </div>
        {datos && datos.items.length === 0 ? (
          <p className="text-sm text-slate-500">No hay documentos para mostrar.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {datos?.items.map((d) => (
              <li key={d.id} className="py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{d.titulo || d.nombre_archivo}</span>
                  <Insignia clase={COLOR_ESTADO[d.estado]}>{d.estado}</Insignia>
                  {d.pii_detectada?.detectada === true && <Insignia clase="bg-amber-100 text-amber-800">PII</Insignia>}
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {d.nombre_archivo} · {formatoBytes(d.tamano_bytes)} · {d.num_paginas ?? "—"} pág. · {d.total_fragmentos ?? 0} fragmentos ·
                  intentos {d.intentos ?? 0} · {formatoFecha(d.creado_en)}
                </p>
                {d.mensaje_error && <p className="mt-1 text-xs text-red-700">{d.mensaje_error}</p>}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(offset - LIMITE_PAGINA, 0))}
            className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
          >
            Anterior
          </button>
          <span className="text-slate-500">
            {total === 0 ? 0 : offset + 1}–{Math.min(offset + LIMITE_PAGINA, total)} de {total}
          </span>
          <button
            type="button"
            disabled={offset + LIMITE_PAGINA >= total}
            onClick={() => setOffset(offset + LIMITE_PAGINA)}
            className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
          >
            Siguiente
          </button>
        </div>
      </Tarjeta>
    </div>
  );
}

function Finops() {
  const [dias, setDias] = useState(30);
  const [resumen, setResumen] = useState<api.FinopsResumen | null>(null);
  const [error, setError] = useState<api.ProblemaError | null>(null);
  const [cargando, setCargando] = useState(false);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      setResumen(await api.resumenFinops(dias));
      setError(null);
    } catch (e) {
      setError(aProblema(e));
    } finally {
      setCargando(false);
    }
  }, [dias]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const excedido = resumen?.estado_presupuesto === "PRESUPUESTO_EXCEDIDO";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-600">
          Ventana{" "}
          <select value={dias} onChange={(e) => setDias(Number(e.target.value))} className="rounded border border-slate-300 px-2 py-1 text-sm">
            {[7, 30, 90, 365].map((d) => (
              <option key={d} value={d}>
                {d} días
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void cargar()} className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100">
          Actualizar
        </button>
        {cargando && <span className="text-sm text-slate-500">Cargando…</span>}
      </div>
      <Alerta error={error} />
      {resumen && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Tarjeta titulo="Presupuesto">
              <p className="text-2xl font-semibold">{usd(resumen.presupuesto_usd)}</p>
            </Tarjeta>
            <Tarjeta titulo="Costo acumulado">
              <p className="text-2xl font-semibold">{usd(resumen.costo_total_usd)}</p>
            </Tarjeta>
            <Tarjeta titulo="Estado">
              <Insignia clase={excedido ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800"}>
                {excedido ? "Presupuesto excedido" : "Dentro del presupuesto"}
              </Insignia>
              <p className="mt-2 text-xs text-slate-500">Generado: {formatoFecha(resumen.generado_en)}</p>
            </Tarjeta>
          </div>
          <Tarjeta titulo={`Colas SQS (reintentos máx. ${resumen.sqs_max_receive_count})`}>
            <div className="grid gap-4 sm:grid-cols-2">
              {Object.entries(resumen.colas).map(([nombre, atributos]) => (
                <div key={nombre}>
                  <p className="mb-1 text-sm font-medium uppercase">{nombre}</p>
                  <dl className="text-sm text-slate-600">
                    {Object.entries(atributos).map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-4 border-b border-slate-100 py-0.5">
                        <dt>{k}</dt>
                        <dd className="font-mono">{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </Tarjeta>
          <Tarjeta titulo="Consumo por servicio y capa gratuita">
            {resumen.servicios.length === 0 ? (
              <p className="text-sm text-slate-500">Sin registros de consumo en la ventana seleccionada.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr>
                      <th className="py-1 pr-3">Periodo</th>
                      <th className="py-1 pr-3">Servicio</th>
                      <th className="py-1 pr-3">Consumo</th>
                      <th className="py-1 pr-3">Costo</th>
                      <th className="py-1">Capa gratuita</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resumen.servicios.map((s) => (
                      <tr key={`${s.periodo}-${s.servicio}`} className="border-t border-slate-100">
                        <td className="py-1 pr-3">{s.periodo}</td>
                        <td className="py-1 pr-3">{s.servicio}</td>
                        <td className="py-1 pr-3 font-mono">{s.consumo_total}</td>
                        <td className="py-1 pr-3 font-mono">{usd(s.costo_total_usd)}</td>
                        <td className="py-1">
                          {s.uso_capa_gratuita_pct === null ? (
                            "—"
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="h-2 w-28 overflow-hidden rounded bg-slate-200">
                                <div
                                  className={`h-2 ${s.alerta ? "bg-red-500" : "bg-emerald-500"}`}
                                  style={{ width: `${Math.min(s.uso_capa_gratuita_pct, 100)}%` }}
                                />
                              </div>
                              <span className={s.alerta ? "font-semibold text-red-700" : ""}>{s.uso_capa_gratuita_pct}%</span>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Tarjeta>
        </>
      )}
    </div>
  );
}

const PESTANAS: { id: Pestana; etiqueta: string }[] = [
  { id: "consulta", etiqueta: "Consulta" },
  { id: "documentos", etiqueta: "Documentos" },
  { id: "finops", etiqueta: "FinOps" },
];

const DESCRIPCIONES: Record<Pestana, { titulo: string; descripcion: string }> = {
  consulta: {
    titulo: "Consulta RAG",
    descripcion: "Obtén respuestas verificables a partir de tus documentos.",
  },
  documentos: {
    titulo: "Biblioteca documental",
    descripcion: "Carga, seguimiento e indexación de fuentes.",
  },
  finops: {
    titulo: "Control de costos",
    descripcion: "Supervisa consumo, presupuesto y colas de procesamiento.",
  },
};

function Icono({ tipo }: { tipo: Pestana }) {
  if (tipo === "consulta") {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />
        <path d="M8 9h8M8 13h5" />
      </svg>
    );
  }
  if (tipo === "documentos") {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
        <path d="M14 2v6h6M8 13h8M8 17h6" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M3 3v18h18" />
      <path d="m7 16 4-4 3 3 5-7" />
    </svg>
  );
}

function App() {
  const [pestana, setPestana] = useState<Pestana>("consulta");
  const [version, setVersion] = useState(0);
  const encabezado = DESCRIPCIONES[pestana];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block mb-8 flex items-center gap-3 px-2">
          <span className="brand-mark">R</span>
          <div>
            <p className="m-0 text-sm font-bold text-slate-900">RAG FinOps</p>
            <p className="mt-0.5 text-xs text-slate-500">Plataforma local</p>
          </div>
        </div>
        <p className="nav-label mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Espacio de trabajo</p>
        <nav aria-label="Secciones">
          {PESTANAS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setPestana(p.id)}
              aria-current={pestana === p.id ? "page" : undefined}
              className={`nav-button ${pestana === p.id ? "nav-button-active" : ""}`}
              title={p.etiqueta}
            >
              <Icono tipo={p.id} />
              <span>{p.etiqueta}</span>
            </button>
          ))}
        </nav>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <Salud />
          <ClaveApi alGuardar={() => setVersion((v) => v + 1)} />
        </header>
        <main className="content">
          <div className="page-heading">
            <h2>{encabezado.titulo}</h2>
            <p>{encabezado.descripcion}</p>
          </div>
          {pestana === "consulta" && <Consulta key={`c-${version}`} />}
          {pestana === "documentos" && <Documentos key={`d-${version}`} />}
          {pestana === "finops" && <Finops key={`f-${version}`} />}
        </main>
      </div>
    </div>
  );
}

const raiz = document.getElementById("root");
if (!raiz) throw new Error("No se encontró el elemento #root");
createRoot(raiz).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
