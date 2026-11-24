"""El catálogo de normas del chatbot = tres capas, de menos a más reciente.

1. `corpus/catalogo_revisor.yaml` — copia del CATALOGO.yaml del Revisor X, donde una persona
   decide las vigencias dudosas. Se refresca solo si el Revisor X está en el mismo PC.
2. `corpus/catalogo_extra.yaml` — lo que el chatbot añade a mano (la Ley 108-05, la 51-07).
3. `corpus/catalogo_auto.json` — lo que el actualizador encontró solo en ri.gob.do: normas
   nuevas y derogaciones expresas leídas de su texto.

El resultado, `corpus/catalogo.yaml`, es GENERADO: no se edita a mano.
"""

from __future__ import annotations

import json
import re

from chatbot import rutas

# Campos del catálogo del Revisor X que cuentan DÓNDE se descubrió una norma: citan oficios
# y expedientes de clientes. El repositorio del chatbot es público: no entran.
CAMPOS_PRIVADOS = ("descubierta_en", "evidencia_cliente", "usada_por")
RE_EXPEDIENTE = re.compile(r"\b66\d{11}\b|\b\d{13}\b")


def sanear(texto_yaml: str) -> str:
    """El CATALOGO.yaml del Revisor X, sin rastro de expedientes ni oficios de clientes."""
    import yaml

    datos = yaml.safe_load(texto_yaml) or {}

    def limpiar(valor):
        if isinstance(valor, dict):
            return {k: limpiar(v) for k, v in valor.items() if k not in CAMPOS_PRIVADOS}
        if isinstance(valor, list):
            return [limpiar(v) for v in valor]
        if isinstance(valor, str):
            valor = RE_EXPEDIENTE.sub("[expediente]", valor)
            return re.sub(r"[Oo]ficio de observaci[óo]n[^.;]*", "un oficio de observación", valor)
        return valor

    return yaml.safe_dump(limpiar(datos), allow_unicode=True, sort_keys=False, width=100)

GRUPOS = ("leyes", "resoluciones", "disposiciones_tecnicas_dnmc", "disposiciones_tecnicas_dnrt",
          "documentos_oficiales")
GRUPO_DE_TIPO = {"LEY": "leyes", "RES": "resoluciones", "DNMC": "disposiciones_tecnicas_dnmc",
                 "DNRT": "disposiciones_tecnicas_dnrt", "FO": "documentos_oficiales",
                 "PL": "documentos_oficiales", "GUIA": "documentos_oficiales"}


def fundir() -> list[str]:
    import yaml

    base = rutas.CORPUS / "catalogo_revisor.yaml"
    datos = yaml.safe_load(base.read_text(encoding="utf-8")) if base.exists() else {}
    avisos: list[str] = []

    def poner(grupo: str, ficha: dict, origen: str) -> None:
        lista = datos.setdefault(grupo, []) or []
        datos[grupo] = lista
        for i, n in enumerate(lista):
            if n["id"] == ficha["id"]:
                lista[i] = {**n, **ficha}
                avisos.append(f"{ficha['id']}: {origen} completa la ficha")
                return
        lista.append(ficha)

    extra = rutas.CORPUS / "catalogo_extra.yaml"
    if extra.exists():
        for grupo, normas in (yaml.safe_load(extra.read_text(encoding="utf-8")) or {}).items():
            for n in normas or []:
                poner(grupo, n, "catalogo_extra")

    auto_ruta = rutas.CORPUS / "catalogo_auto.json"
    if auto_ruta.exists():
        auto = json.loads(auto_ruta.read_text(encoding="utf-8"))
        for n in auto.get("normas", []):
            poner(GRUPO_DE_TIPO.get(n["id"].split("-")[0], "resoluciones"), dict(n), "actualizador")
        for d in auto.get("derogaciones", []):
            for grupo in GRUPOS:
                for n in datos.get(grupo) or []:
                    if n["id"] != d["norma"] or n.get("estado") == "derogada":
                        continue
                    # Lo que una PERSONA declaró vigente (catálogo del Revisor X o extra) no lo
                    # tumba una deducción automática: queda como aviso para revisar. Solo se
                    # aplica sola sobre lo que el propio actualizador incorporó, o lo que
                    # estaba «por confirmar» (hallazgo 1 de la revisión).
                    humana = n.get("estado") == "vigente" and not n.get("grupo_auto")
                    if humana:
                        n["aviso_derogacion"] = (
                            f"{d['por']} parece derogarla (detectado automáticamente el "
                            f"{d['fecha']}): confírmalo en el catálogo del Revisor X.")
                        avisos.append(f"{n['id']}: posible derogación por {d['por']} — "
                                      "pendiente de que una persona la confirme")
                        continue
                    n["estado"] = "derogada"
                    n["reemplazada_por"] = d["por"]
                    n["nota"] = (f"Derogada por {d['por']} (detectado automáticamente el "
                                 f"{d['fecha']} en su artículo de derogaciones). "
                                 + str(n.get("nota", "")))

    cabecera = ("# GENERADO por chatbot/catalogo.py — NO EDITAR A MANO.\n"
                "# Capas: catalogo_revisor.yaml + catalogo_extra.yaml + catalogo_auto.json\n\n")
    from chatbot.archivos import escribir
    escribir(rutas.catalogo(),
             cabecera + yaml.safe_dump(datos, allow_unicode=True, sort_keys=False, width=100))
    return avisos
