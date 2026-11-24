"""El plan de recursos: qué hace el chatbot con la GPU, el procesador y la RAM que encuentre."""

from pathlib import Path

import pytest

import chatbot.motor_local as ml
from chatbot.motor_local import CONFIG_POR_DEFECTO, Equipo, Tarjeta, planificar


TAMANOS = {"gemma-3-4b-it-Q4_K_M.gguf": 2500, "granite-4.0-h-micro-Q4_K_M.gguf": 1900}


@pytest.fixture
def modelos(tmp_path, monkeypatch):
    """Dos modelos falsos: uno de 2,5 GB (el preferido) y uno de 1,9 GB (el ligero).

    Archivos VACÍOS con el tamaño simulado: la primera versión los creaba de su tamaño real
    (f.truncate) y en NTFS eso ocupa el disco de verdad — llenó C: el 24-nov-2026."""
    monkeypatch.setattr(ml, "MODELOS", tmp_path)
    for nombre in TAMANOS:
        (tmp_path / nombre).write_bytes(b"")
    monkeypatch.setattr(ml, "_mb", lambda ruta: TAMANOS.get(Path(ruta).name, 0) if ruta else 0)
    return tmp_path


def _eq(vram_libre=None, ram_libre=12000, nombre="NVIDIA GeForce GTX 1050"):
    tarjetas = [Tarjeta("Vulkan0", "Intel(R) UHD Graphics 630", 12000, 11000)]
    if vram_libre is not None:
        tarjetas.append(Tarjeta("Vulkan1", nombre, 4096, vram_libre))
    return Equipo(nucleos=4, hilos=8, ram_total_mb=24000, ram_libre_mb=ram_libre, tarjetas=tarjetas)


def test_cabe_entero_en_la_gpu(modelos):
    p = planificar(_eq(vram_libre=4000), dict(CONFIG_POR_DEFECTO))
    assert p.modo == "GPU" and "Vulkan1" in p.argumentos and "--fit" in p.argumentos
    assert Path(p.modelo).name.startswith("gemma")


def test_no_cabe_entero_reparte_gpu_y_cpu(modelos):
    p = planificar(_eq(vram_libre=2500), dict(CONFIG_POR_DEFECTO))
    assert p.modo == "GPU+CPU" and "--fit-target" in p.argumentos


def test_nunca_la_integrada(modelos):
    p = planificar(_eq(vram_libre=None), dict(CONFIG_POR_DEFECTO))
    assert p.modo == "CPU" and "Vulkan0" not in p.argumentos


def test_cpu_usa_nucleos_para_escribir_e_hilos_para_leer(modelos):
    p = planificar(_eq(vram_libre=None), dict(CONFIG_POR_DEFECTO))
    a = p.argumentos
    assert a[a.index("-t") + 1] == "4" and a[a.index("-tb") + 1] == "8"


def test_sobra_vram_dos_respuestas_a_la_vez(modelos):
    p = planificar(_eq(vram_libre=8000, nombre="NVIDIA RTX 3060"), dict(CONFIG_POR_DEFECTO))
    assert p.contexto == 8192 and p.paralelo == 2


def test_poca_ram_elige_el_modelo_ligero(modelos):
    p = planificar(_eq(vram_libre=None, ram_libre=3300), dict(CONFIG_POR_DEFECTO))
    assert Path(p.modelo).name.startswith("granite")


def test_forzar_cpu_tras_un_fallo(modelos):
    p = planificar(_eq(vram_libre=4000), dict(CONFIG_POR_DEFECTO), forzar_cpu=True)
    assert p.modo == "CPU"
