import re

_SEPARADORES = ("\n\n", ". ", "? ", "! ", "; ", "\n", ", ", " ")


def normalizar_espacios(texto: str) -> str:
    t = texto.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def dividir_texto(texto: str, tamano: int, solape: int) -> list[str]:
    if tamano < 50:
        raise ValueError("El tamaño de fragmento debe ser al menos 50 caracteres.")
    solape = min(max(solape, 0), tamano // 2)
    t = normalizar_espacios(texto)
    n = len(t)
    if n == 0:
        return []
    if n <= tamano:
        return [t]

    trozos: list[str] = []
    inicio = 0
    while inicio < n:
        fin = min(inicio + tamano, n)
        if fin < n:
            piso = inicio + tamano // 2
            for sep in _SEPARADORES:
                pos = t.rfind(sep, piso, fin)
                if pos != -1:
                    fin = pos + len(sep)
                    break
        trozo = t[inicio:fin].strip()
        if trozo:
            trozos.append(trozo)
        if fin >= n:
            break
        siguiente = fin - solape
        if siguiente <= inicio:
            siguiente = fin
        if inicio < siguiente < fin and not t[siguiente - 1].isspace():
            espacio = t.find(" ", siguiente, fin)
            if espacio != -1:
                siguiente = espacio + 1
        inicio = siguiente
    return trozos
