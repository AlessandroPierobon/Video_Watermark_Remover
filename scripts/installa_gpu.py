#!/usr/bin/env python3
"""Aggiunge il motore ProPainter con accelerazione GPU AMD su Windows.

Scarica e installa, nell'ambiente Python del programma:

  1. ROCm 7.2.1 per Windows (arriva come pacchetti pip: dalla 7.2 in poi
     l'HIP SDK a parte non serve piu');
  2. PyTorch 2.9.1 e torchvision 0.24.1 compilati per ROCm 7.2.1;
  3. le librerie richieste da ProPainter;
  4. il codice di ProPainter e i tre checkpoint dell'inferenza.

Sono circa 2,5 GB di download e 8 GB su disco. Serve il driver AMD Adrenalin
26.2.2 o piu' recente, e Python 3.12: le ruote ROCm per Windows esistono solo
per cp312.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
REPO_ROCM = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"

ROCM_SDK = (
    f"{REPO_ROCM}/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
    f"{REPO_ROCM}/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
    f"{REPO_ROCM}/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
    f"{REPO_ROCM}/rocm-7.2.1.tar.gz",
)
TORCH = ("torch==2.9.1+rocm7.2.1", "torchvision==0.24.1+rocm7.2.1")

# Dipendenze di ProPainter meno quelle gia' presenti e meno opencv-python, che
# installerebbe un secondo modulo cv2 in conflitto con quello headless.
DIPENDENZE = (
    "av", "addict", "einops", "future", "scipy", "matplotlib",
    "scikit-image", "pyyaml", "requests", "timm", "yapf",
)

SPAZIO_RICHIESTO_GB = 10


def titolo(testo: str) -> None:
    print(f"\n{'=' * 68}\n  {testo}\n{'=' * 68}", flush=True)


def pip(*argomenti: str) -> None:
    comando = [sys.executable, "-m", "pip", "install", "--no-cache-dir", *argomenti]
    print("> " + " ".join(comando[3:]), flush=True)
    esito = subprocess.run(comando)
    if esito.returncode != 0:
        raise SystemExit(
            f"\nl'installazione si e' fermata (codice {esito.returncode}).\n"
            "Controlla la connessione e riprova: i pacchetti gia' scaricati "
            "non vengono riscaricati."
        )


def controlla_ambiente(forza: bool) -> None:
    if sys.platform != "win32" and not forza:
        raise SystemExit(
            "Questo script serve per Windows con GPU AMD.\n"
            "Su altri sistemi installa PyTorch come indicato nel README."
        )
    if sys.version_info[:2] != (3, 12) and not forza:
        raise SystemExit(
            f"Serve Python 3.12, qui gira il {sys.version_info[0]}.{sys.version_info[1]}.\n"
            "Le ruote ROCm per Windows esistono solo per cp312."
        )
    libero = shutil.disk_usage(RADICE).free / 1e9
    if libero < SPAZIO_RICHIESTO_GB:
        raise SystemExit(
            f"Servono almeno {SPAZIO_RICHIESTO_GB} GB liberi, "
            f"ce ne sono {libero:.1f}."
        )


def conferma() -> None:
    print(__doc__)
    print(f"Cartella di installazione: {RADICE}")
    risposta = input("\nProcedo? [s/N] ").strip().lower()
    if risposta not in {"s", "si", "sì", "y", "yes"}:
        raise SystemExit("annullato.")


def verifica() -> None:
    titolo("Verifica della GPU")
    codice = (
        "import torch;"
        "print('PyTorch', torch.__version__);"
        "d=torch.cuda.is_available();"
        "print('GPU disponibile:', d);"
        "print('scheda:', torch.cuda.get_device_name(0)) if d else "
        "print('nessuna GPU rilevata: aggiorna il driver AMD Adrenalin alla 26.2.2')"
    )
    subprocess.run([sys.executable, "-c", codice])


def main() -> int:
    analizzatore = argparse.ArgumentParser(description=__doc__)
    analizzatore.add_argument(
        "--dir", type=Path, default=RADICE / "gpu" / "ProPainter",
        help="cartella in cui mettere ProPainter",
    )
    analizzatore.add_argument(
        "--si", action="store_true", help="non chiedere conferma"
    )
    analizzatore.add_argument(
        "--forza", action="store_true",
        help="salta i controlli su sistema operativo e versione di Python",
    )
    argomenti = analizzatore.parse_args()

    controlla_ambiente(argomenti.forza)
    if not argomenti.si:
        conferma()

    titolo("1/4  ROCm 7.2.1 per Windows (circa 1,3 GB)")
    pip(*ROCM_SDK)

    titolo("2/4  PyTorch per ROCm (circa 800 MB)")
    # La forma con -f e' necessaria: le ruote di torch dipendono dal pacchetto
    # "rocm", che non sta su PyPI ma solo nel repository AMD.
    pip("-f", f"{REPO_ROCM}/", *TORCH)

    titolo("3/4  Librerie richieste da ProPainter")
    pip(*DIPENDENZE)

    titolo("4/4  Codice e pesi di ProPainter (circa 500 MB)")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import setup_propainter

    setup_propainter.clone(argomenti.dir)
    setup_propainter.patch_for_rocm(argomenti.dir)
    setup_propainter.download_weights(argomenti.dir)

    verifica()
    print(
        "\nFatto. Riapri il programma: nella tendina \"Motore\" ora puoi "
        "scegliere ProPainter."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
