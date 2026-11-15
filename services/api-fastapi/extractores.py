import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser

logger = logging.getLogger("worker.extractores")

MAX_PAGINAS = 2000
MAX_DESCOMPRIMIDO_BYTES = 200 * 1024 * 1024
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ErrorPermanente(Exception):
    """Error que no se resuelve reintentando (archivo corrupto, cifrado, sin texto, inexistente)."""


class ErrorExtraccion(ErrorPermanente):
    pass


@dataclass(frozen=True)
class Extraccion:
    paginas: list[tuple[int, str]]
    num_paginas: int
    extractor: str


def _limpiar(texto: str) -> str:
    return _RE_CONTROL.sub("", texto).encode("utf-8", "ignore").decode("utf-8")


def _decodificar(datos: bytes) -> str:
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("latin-1")


def _extraer_pdf(datos: bytes) -> Extraccion:
    from pypdf import PdfReader

    try:
        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted and not lector.decrypt(""):
            raise ErrorExtraccion("El PDF está cifrado y no puede leerse sin contraseña.")
        total = len(lector.pages)
        if total > MAX_PAGINAS:
            raise ErrorExtraccion(f"El PDF excede el máximo de {MAX_PAGINAS} páginas.")
        paginas = [(n, p.extract_text() or "") for n, p in enumerate(lector.pages, start=1)]
    except ErrorPermanente:
        raise
    except Exception as exc:
        raise ErrorExtraccion(f"PDF ilegible: {type(exc).__name__}") from exc
    return Extraccion(paginas, total, "pypdf")


def _extraer_docx(datos: bytes) -> Extraccion:
    from docx import Document

    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as zf:
            if sum(i.file_size for i in zf.infolist()) > MAX_DESCOMPRIMIDO_BYTES:
                raise ErrorExtraccion("El DOCX excede el tamaño descomprimido permitido.")
        doc = Document(io.BytesIO(datos))
        partes = [p.text for p in doc.paragraphs if p.text.strip()]
        for tabla in doc.tables:
            for fila in tabla.rows:
                celdas = [c.text.strip() for c in fila.cells if c.text.strip()]
                if celdas:
                    partes.append(" | ".join(celdas))
    except ErrorPermanente:
        raise
    except Exception as exc:
        raise ErrorExtraccion(f"DOCX ilegible: {type(exc).__name__}") from exc
    return Extraccion([(1, "\n".join(partes))], 1, "python-docx")


class _HTMLTexto(HTMLParser):
    _OMITIR = {"script", "style", "noscript", "template"}
    _BLOQUES = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
                "table", "ul", "ol", "pre", "blockquote", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._omitir = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._OMITIR:
            self._omitir += 1
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self._OMITIR:
            self._omitir = max(0, self._omitir - 1)
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._omitir:
            self.partes.append(data)


def _extraer_html(datos: bytes) -> Extraccion:
    parser = _HTMLTexto()
    try:
        parser.feed(_decodificar(datos))
        parser.close()
    except Exception as exc:
        raise ErrorExtraccion(f"HTML ilegible: {type(exc).__name__}") from exc
    return Extraccion([(1, "".join(parser.partes))], 1, "html.parser")


def _extraer_texto(datos: bytes) -> Extraccion:
    return Extraccion([(1, _decodificar(datos))], 1, "texto-plano")


_DESPACHO = {
    ".pdf": _extraer_pdf,
    ".docx": _extraer_docx,
    ".html": _extraer_html,
    ".txt": _extraer_texto,
    ".md": _extraer_texto,
}


def extraer_paginas(datos: bytes, nombre_archivo: str) -> Extraccion:
    extension = os.path.splitext(nombre_archivo)[1].lower()
    extractor = _DESPACHO.get(extension)
    if extractor is None:
        raise ErrorExtraccion(f"Extensión no soportada para extracción: {extension or 'sin extensión'}")
    bruto = extractor(datos)
    paginas = []
    for numero, contenido in bruto.paginas:
        limpio = _limpiar(contenido)
        if limpio.strip():
            paginas.append((numero, limpio))
    if not paginas:
        raise ErrorExtraccion("El documento no contiene texto extraíble (¿PDF escaneado sin OCR?).")
    return Extraccion(paginas, bruto.num_paginas, bruto.extractor)
