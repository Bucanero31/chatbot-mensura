"""Mide el buscador contra la batería de preguntas, y audita la batería.

    py evaluacion/evaluar.py                 resumen
    py evaluacion/evaluar.py --fallos        además, cada pregunta que no acierta
    py evaluacion/evaluar.py --auditar       solo comprueba la batería contra el corpus
    py evaluacion/evaluar.py --sin-memoria   sin lo aprendido (mide el buscador desnudo)

**Dos cosas distintas, y las dos hacen falta:**

1. **Auditar la batería.** La redactó un modelo más barato a partir de las citas verificadas
   de los YAML del Revisor X. Antes de fiarse de un acierto hay que fiarse de la pregunta:
   ¿existe el artículo esperado en el corpus? ¿su texto contiene el dato clave? Una pregunta
   mal anotada hace que el buscador parezca fallar cuando acierta (o al revés).
2. **Medir el buscador**: acierto@1, @3, @5 y @8 — ¿está el artículo esperado entre los N
   primeros? Es lo que importa, porque el modelo solo ve lo que el buscador le da: si el
   artículo no está entre los fragmentos, la mejor redacción del mundo responde mal.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from chatbot.buscador import Buscador  # noqa: E402
from chatbot.texto import sin_tildes  # noqa: E402

CORTES = (1, 3, 5, 8)


def cargar_bateria(ruta: Path) -> list[dict]:
    import yaml

    return yaml.safe_load(ruta.read_text(encoding="utf-8"))["preguntas"]


def coincide(unidad, esperado: dict) -> bool:
    """¿Esta unidad es la esperada? Tolera las formas de anotar de la batería."""
    if unidad.norma != esperado["norma"]:
        return False
    art = esperado.get("articulo")
    if art is None:
        return True                               # basta con la norma
    art = str(art).strip()
    m = re.match(r"Secci[oó]n (\d+)", art)
    if m:
        # DNMC-DT-2023-001 numera los trámites dentro del Art. 23: «Sección 5» = apartado 5.
        return unidad.articulo == "Art. 23" and unidad.apartado.startswith(f"{m.group(1)}.")
    if art.lower().startswith("considerando"):
        return unidad.articulo == "Preámbulo"
    base = re.sub(r" \((resolutivo|bloque \d+|\d+)\)$", "", unidad.articulo)
    return base == art


def contiene_dato(texto: str, dato: str) -> bool:
    t = sin_tildes(" ".join(texto.lower().split()))
    d = sin_tildes(" ".join(str(dato).lower().split()))
    return d in t or d.replace(" ", "") in t.replace(" ", "")


def auditar(b: Buscador, preguntas: list[dict]) -> list[str]:
    problemas = []
    for p in preguntas:
        for e in p.get("esperado") or []:
            unidades = [u for u in b.unidades if coincide(u, e)]
            if not unidades:
                estado = b.normas.get(e["norma"], {}).get("estado", "¿no está en el catálogo?")
                problemas.append(f"{p['id']}: {e['norma']} {e.get('articulo')} no está en el "
                                 f"corpus (norma: {estado})")
                continue
            if p.get("dato_clave") and e.get("articulo") and not any(
                    contiene_dato(u.texto, p["dato_clave"]) for u in unidades):
                problemas.append(f"{p['id']}: «{p['dato_clave']}» no aparece en "
                                 f"{e['norma']} {e['articulo']}")
    return problemas


def medir(b: Buscador, preguntas: list[dict], ver_fallos: bool) -> dict:
    aciertos = {c: 0 for c in CORTES}
    por_categoria = defaultdict(lambda: [0, 0])
    total = 0
    fallos = []
    fuera = []
    confianzas = []
    evidencia = [0, 0]
    sin_evidencia = []
    from chatbot.responder import FRAGMENTOS_AL_MODELO, cobertura, preparar_fuentes
    for p in preguntas:
        esperados = p.get("esperado") or []
        consulta, res = b.buscar(p["pregunta"], k=max(CORTES))
        cob = cobertura(consulta, res[0].unidad, b) if res else 0.0
        sim = max(consulta.similitudes.values(), default=0.0)
        if not esperados:
            if p["categoria"] == "fuera_de_alcance":
                fuera.append((p["id"], cob, sim, p["pregunta"]))
            continue
        confianzas.append((cob, sim))
        total += 1
        posicion = next((i + 1 for i, r in enumerate(res)
                         if any(coincide(r.unidad, e) for e in esperados)), None)
        for c in CORTES:
            if posicion and posicion <= c:
                aciertos[c] += 1
        # ¿Le llega al modelo la frase que responde? (el techo de cualquier redacción)
        if p.get("dato_clave"):
            fuentes = preparar_fuentes(consulta, res[:6], b)
            leido = " ".join((f.contexto_amplio if i == 0 else f.contexto)
                             for i, f in enumerate(fuentes[:FRAGMENTOS_AL_MODELO]))
            evidencia[0] += contiene_dato(leido, p["dato_clave"])
            evidencia[1] += 1
            if not contiene_dato(leido, p["dato_clave"]):
                sin_evidencia.append(p["id"])
        cat = por_categoria[p["categoria"]]
        cat[1] += 1
        if posicion and posicion <= 5:
            cat[0] += 1
        else:
            fallos.append((p, posicion, res[:3], consulta))

    print(f"\n  {total} preguntas con artículo esperado\n")
    for c in CORTES:
        print(f"  acierto@{c:<2} {aciertos[c]:>4}  {100 * aciertos[c] / max(1, total):5.1f} %")
    print(f"\n  evidencia al modelo: {evidencia[0]}/{evidencia[1]} "
          f"({100 * evidencia[0] / max(1, evidencia[1]):.1f} %) de las preguntas con dato clave "
          "lo tienen en el texto que lee el redactor")
    if ver_fallos and sin_evidencia:
        print(f"    sin el dato: {', '.join(sin_evidencia)}")
    print("\n  por categoría (acierto@5):")
    for cat, (a, t) in sorted(por_categoria.items(), key=lambda x: x[1][0] / x[1][1]):
        print(f"    {cat:<24} {a:>3}/{t:<3} {100 * a / t:5.1f} %")
    if fuera:
        # Calibra el umbral de «no lo encuentro»: las de dentro deberían quedar por encima.
        coberturas = sorted(x[0] for x in confianzas)
        sims = sorted(x[1] for x in confianzas)
        pct = lambda xs, q: xs[int(q * (len(xs) - 1))] if xs else 0.0  # noqa: E731
        print(f"\n  cobertura de las de DENTRO: p10 {pct(coberturas, .1):.2f} · "
              f"p25 {pct(coberturas, .25):.2f} · mediana {pct(coberturas, .5):.2f}"
              f"   similitud p10 {pct(sims, .1):.2f} · mediana {pct(sims, .5):.2f}")
        print("  fuera de alcance (cobertura · similitud del primero; deberían ser bajas):")
        for ident, cob, sim, texto in fuera:
            print(f"    {ident} cob {cob:.2f} · sim {sim:.2f}  {texto[:66]}")
    if ver_fallos:
        print("\n  FALLOS (no está entre los 5 primeros):")
        for p, pos, top, c in fallos:
            esp = "; ".join(f"{e['norma']} {e.get('articulo')}" for e in p["esperado"])
            print(f"\n  {p['id']} [{p['categoria']}] {p['pregunta']}")
            print(f"     esperado: {esp}   posición: {pos or '>8'}")
            print(f"     entendió: {c.terminos} +{c.expandidos} normas={c.normas_nombradas}")
            for r in top:
                print(f"     → {r.unidad.referencia} [{r.puntaje:.1f}] {r.unidad.titulo[:50]}")
    return {"total": total, **{f"a{c}": aciertos[c] for c in CORTES}}


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--fallos", action="store_true")
    partes.add_argument("--auditar", action="store_true")
    partes.add_argument("--sin-memoria", action="store_true")
    partes.add_argument("--vectores", action="store_true",
                        help="fusionar con la búsqueda por significado (arranca el modelo)")
    partes.add_argument("--peso", type=float, help="peso de la búsqueda por significado")
    partes.add_argument("--bateria", default="preguntas.yaml",
                        help="preguntas.yaml (de las reglas) o preguntas_oficina.yaml (coloquial)")
    args = partes.parse_args()
    if args.peso is not None:
        import chatbot.buscador as mod
        mod.PESO_SIGNIFICADO = args.peso

    memoria = None
    if not args.sin_memoria:
        try:
            from chatbot.memoria import Memoria
            memoria = Memoria()
        except ImportError:
            memoria = None
    b = Buscador(memoria=memoria)
    if args.vectores:
        from chatbot.motor_local import motor
        from chatbot.vectores import Vectores
        b.vectores = Vectores(motor(), b)
        # Como en el servidor (precarga): el modelo de embeddings, arrancado ANTES de preguntar.
        # La consulta ya no espera a que arranque, y sin esto las primeras preguntas de la
        # batería se medían solo por palabras.
        motor().emb.asegurar()
        print(f"  vectores: {'listos' if b.vectores.listos else 'NO disponibles'}")
    preguntas = cargar_bateria(RAIZ / "evaluacion" / args.bateria)

    problemas = auditar(b, preguntas)
    print(f"\n  Auditoría de la batería: {len(problemas)} problema(s)")
    for x in problemas:
        print(f"    - {x}")
    if args.auditar:
        return 1 if problemas else 0
    medir(b, preguntas, args.fallos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
