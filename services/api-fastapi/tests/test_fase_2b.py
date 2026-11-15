import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from chunker import dividir_texto
from extractores import ErrorExtraccion, extraer_paginas
from pii_guardrail import anonimizar, validar_luhn, validar_rut
from rag_engine import generar_embedding


def test_validacion_rut_modulo_11():
    assert validar_rut("12.345.678-5")
    assert validar_rut("7654321-6")
    assert not validar_rut("12.345.678-9")


def test_anonimiza_rut_correo_telefono_y_tarjeta():
    r = anonimizar("RUT 12.345.678-5, correo juan@example.com, fono +56 9 1234 5678, tarjeta 4111 1111 1111 1111")
    assert "[RUT_ANONIMIZADO]" in r.texto
    assert "[EMAIL_ANONIMIZADO]" in r.texto
    assert "[TELEFONO_ANONIMIZADO]" in r.texto
    assert "[TARJETA_ANONIMIZADA]" in r.texto
    assert "12.345.678" not in r.texto and "juan@example.com" not in r.texto
    assert r.total == 4 and validar_luhn("4111 1111 1111 1111")


def test_rut_con_digito_invalido_no_se_anonimiza():
    assert anonimizar("folio 12.345.678-9").total == 0


def test_embedding_determinista_y_normalizado():
    a = generar_embedding("política de vacaciones del personal", 384)
    b = generar_embedding("política de vacaciones del personal", 384)
    assert a == b and len(a) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)


def test_chunker_respeta_tamano_y_solape():
    texto = ". ".join(f"Oración número {i} del documento de prueba" for i in range(200))
    trozos = dividir_texto(texto, 800, 120)
    assert len(trozos) > 1
    assert all(len(t) <= 800 for t in trozos)
    assert trozos[1][:20] in trozos[0]
    assert trozos[-1].endswith("prueba")


def test_chunker_texto_corto_y_vacio():
    assert dividir_texto("   ", 800, 120) == []
    assert dividir_texto("Texto breve.", 800, 120) == ["Texto breve."]


def test_extraer_texto_plano_elimina_nulos():
    r = extraer_paginas("Hola\x00 mundo con ñ".encode("utf-8"), "nota.txt")
    assert r.num_paginas == 1 and "\x00" not in r.paginas[0][1]


def test_extraer_html_omite_scripts():
    html = b"<html><body><script>var x=1;</script><p>Contenido visible</p></body></html>"
    r = extraer_paginas(html, "pagina.html")
    assert "Contenido visible" in r.paginas[0][1] and "var x" not in r.paginas[0][1]


def test_extension_no_soportada_y_pdf_corrupto():
    with pytest.raises(ErrorExtraccion):
        extraer_paginas(b"datos", "archivo.exe")
    with pytest.raises(ErrorExtraccion):
        extraer_paginas(b"esto no es un pdf", "roto.pdf")
