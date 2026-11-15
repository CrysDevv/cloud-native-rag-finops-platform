import re
from collections import Counter
from dataclasses import dataclass, field

TOKEN_RUT = "[RUT_ANONIMIZADO]"
TOKEN_EMAIL = "[EMAIL_ANONIMIZADO]"
TOKEN_TELEFONO = "[TELEFONO_ANONIMIZADO]"
TOKEN_TARJETA = "[TARJETA_ANONIMIZADA]"
TOKEN_CUENTA = "[CUENTA_ANONIMIZADA]"

_RE_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
_RE_RUT = re.compile(r"(?<![\w.])(?:\d{1,2}(?:\.\d{3}){2}|\d{7,8})\s?-\s?[\dkK](?![\w])")
_RE_TARJETA = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?!\d)")
_RE_CUENTA = re.compile(
    r"(?i)(\bcuenta(?:\s+(?:corriente|vista|rut|de\s+ahorros?|bancaria))?\s*(?:n[°º.o]*\s*)?[:#]?\s*)(\d[\d\s-]{6,22}\d)(?!\d)"
)
_RE_TELEFONO = re.compile(
    r"(?<![\w.])(?:\+?56[\s-]?)?(?:9[\s-]?\d{4}[\s-]?\d{4}|2[\s-]?\d{4}[\s-]?\d{4}"
    r"|(?:3[2-5]|4[1-3]|5[1-8]|6[1-5]|7[1-5])[\s-]?\d{3}[\s-]?\d{4})(?!\d)"
)


def validar_rut(valor: str) -> bool:
    limpio = re.sub(r"[^0-9kK]", "", valor).upper()
    if not 8 <= len(limpio) <= 9:
        return False
    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit():
        return False
    suma = 0
    for i, digito in enumerate(reversed(cuerpo)):
        suma += int(digito) * (2 + i % 6)
    resto = 11 - (suma % 11)
    esperado = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    return dv == esperado


def validar_luhn(valor: str) -> bool:
    digitos = [int(c) for c in re.sub(r"\D", "", valor)]
    if not 13 <= len(digitos) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digitos)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class ResultadoPII:
    texto: str
    total: int
    por_tipo: dict[str, int] = field(default_factory=dict)

    @property
    def detectada(self) -> bool:
        return self.total > 0

    def resumen(self) -> dict:
        return {"detectada": self.detectada, "total": self.total, "por_tipo": dict(self.por_tipo)}


def _sustituir(patron: re.Pattern, texto: str, token: str, tipo: str, conteo: Counter, validador=None) -> str:
    def repl(m: re.Match) -> str:
        if validador is not None and not validador(m.group(0)):
            return m.group(0)
        conteo[tipo] += 1
        return token

    return patron.sub(repl, texto)


def anonimizar(texto: str, habilitado: bool = True) -> ResultadoPII:
    conteo: Counter = Counter()
    salida = _sustituir(_RE_EMAIL, texto, TOKEN_EMAIL, "email", conteo)
    salida = _sustituir(_RE_RUT, salida, TOKEN_RUT, "rut", conteo, validar_rut)
    salida = _sustituir(_RE_TARJETA, salida, TOKEN_TARJETA, "tarjeta", conteo, validar_luhn)

    def repl_cuenta(m: re.Match) -> str:
        conteo["cuenta"] += 1
        return m.group(1) + TOKEN_CUENTA

    salida = _RE_CUENTA.sub(repl_cuenta, salida)
    salida = _sustituir(_RE_TELEFONO, salida, TOKEN_TELEFONO, "telefono", conteo)
    total = sum(conteo.values())
    return ResultadoPII(texto=salida if habilitado else texto, total=total, por_tipo=dict(conteo))
