"""Compara modelos locales como REDACTORES: misma pregunta, mismos fragmentos, distinto modelo.

    py evaluacion/comparar_modelos.py                           todos, en el dispositivo auto
    py evaluacion/comparar_modelos.py --dispositivo cpu --n 8   simula un PC sin tarjeta
    py evaluacion/comparar_modelos.py --modelos LFM2-1.2B-RAG-Q4_K_M.gguf

Mide al redactor, no al buscador: solo entran preguntas cuyo artículo esperado está entre
los fragmentos que recibe el modelo. Si el buscador falla, ningún modelo puede acertar, y
eso ya lo mide `evaluar.py`.

Por cada respuesta: segundos, ¿contiene el dato clave?, ¿cita la fuente esperada?, avisos
del verificador, y en las de fuera de alcance, ¿dice que no lo encuentra?
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "evaluacion"))

from chatbot import motor_local  # noqa: E402
from chatbot.buscador import Buscador  # noqa: E402
from chatbot.responder import (FRAGMENTOS_AL_MODELO, limpiar, mensajes_para_modelo,  # noqa: E402
                               preparar_fuentes, verificar)
from chatbot.texto import sin_tildes  # noqa: E402
from evaluar import cargar_bateria, coincide, contiene_dato  # noqa: E402

PUERTO = 8016


def elegir_preguntas(b: Buscador, preguntas: list[dict], n: int) -> list[tuple[dict, list]]:
    elegidas, fuera = [], []
    for p in preguntas:
        if p["categoria"] == "fuera_de_alcance":
            fuera.append(p)
            continue
        if not p.get("dato_clave") or not p.get("esperado"):
            continue
        c, res = b.buscar(p["pregunta"], k=6)
        fuentes = preparar_fuentes(c, res, b)
        al_modelo = fuentes[:FRAGMENTOS_AL_MODELO]
        if any(coincide(b.unidades[f.unidad_id], e) for f in al_modelo if f.unidad_id >= 0
               for e in p["esperado"]):
            elegidas.append((p, fuentes))
    paso = max(1, len(elegidas) // n)
    muestra = elegidas[::paso][:n]
    for p in fuera[:3]:
        c, res = b.buscar(p["pregunta"], k=6)
        muestra.append((p, preparar_fuentes(c, res, b)))
    return muestra


def correr(modelo: Path, dispositivo: str, muestra, b: Buscador) -> dict:
    cfg = dict(motor_local.CONFIG_POR_DEFECTO, puerto_chat=PUERTO, dispositivo=dispositivo,
               modelo_chat=modelo.name)
    m = motor_local.MotorLocal(cfg)
    t0 = time.time()
    if not m.chat.asegurar(espera=300):
        return {"modelo": modelo.name, "error": m.chat.ultimo_error}
    carga = time.time() - t0
    filas = []
    try:
        for p, fuentes in muestra:
            t = time.time()
            try:
                texto = limpiar(m.redactar(mensajes_para_modelo(p["pregunta"], fuentes)))
            except Exception as e:  # noqa: BLE001
                filas.append({"id": p["id"], "error": str(e)})
                continue
            seg = time.time() - t
            v = verificar(texto, fuentes)
            fila = {"id": p["id"], "categoria": p["categoria"], "segundos": round(seg, 1),
                    "avisos": v["avisos"], "texto": texto}
            if p["categoria"] == "fuera_de_alcance":
                fila["dice_no_encuentra"] = "no encuentro" in sin_tildes(texto.lower())
            else:
                fila["dato"] = contiene_dato(texto, p["dato_clave"])
                esperadas = {f.n for f in fuentes if f.unidad_id >= 0 and any(
                    coincide(b.unidades[f.unidad_id], e) for e in p["esperado"])}
                fila["cita_correcta"] = bool(esperadas & set(v["citas"]))
            filas.append(fila)
            print(f"    {p['id']} {seg:5.1f}s dato={fila.get('dato')} cita={fila.get('cita_correcta')} "
                  f"no_encuentra={fila.get('dice_no_encuentra')} avisos={len(v['avisos'])}",
                  flush=True)
    finally:
        m.chat.parar()
    normales = [f for f in filas if "dato" in f]
    fuera = [f for f in filas if "dice_no_encuentra" in f]
    return {
        "modelo": modelo.name, "dispositivo": m.dispositivo.descripcion,
        "carga_s": round(carga, 1),
        "segundos_medios": round(sum(f["segundos"] for f in filas if "segundos" in f)
                                 / max(1, len([f for f in filas if "segundos" in f])), 1),
        "dato_ok": sum(f["dato"] for f in normales), "cita_ok": sum(f["cita_correcta"] for f in normales),
        "normales": len(normales),
        "sin_avisos": sum(1 for f in normales if not f["avisos"]),
        "fuera_ok": sum(f["dice_no_encuentra"] for f in fuera), "fuera": len(fuera),
        "filas": filas,
    }


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--modelos", nargs="*")
    partes.add_argument("--dispositivo", default="auto")
    partes.add_argument("--n", type=int, default=12)
    args = partes.parse_args()

    b = Buscador()
    muestra = elegir_preguntas(b, cargar_bateria(RAIZ / "evaluacion" / "preguntas.yaml"), args.n)
    print(f"\n  {len(muestra)} preguntas ({args.n} con dato clave + fuera de alcance)\n")
    nombres = args.modelos or list(motor_local.PREFERENCIA_CHAT)
    resumen = []
    for nombre in nombres:
        ruta = motor_local.MODELOS / nombre
        if not ruta.exists():
            print(f"  (falta {nombre})")
            continue
        print(f"  == {nombre}")
        r = correr(ruta, args.dispositivo, muestra, b)
        resumen.append(r)
        if "error" not in r:
            print(f"     {r['dispositivo']} · carga {r['carga_s']} s · {r['segundos_medios']} s/resp · "
                  f"dato {r['dato_ok']}/{r['normales']} · cita {r['cita_ok']}/{r['normales']} · "
                  f"sin avisos {r['sin_avisos']}/{r['normales']} · fuera {r['fuera_ok']}/{r['fuera']}\n")
        else:
            print(f"     ERROR {r['error']}\n")
    salida = RAIZ / "evaluacion" / "resultados"
    salida.mkdir(exist_ok=True)
    destino = salida / f"modelos-{time.strftime('%Y%m%d-%H%M')}-{args.dispositivo}.json"
    destino.write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  Detalle en {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
