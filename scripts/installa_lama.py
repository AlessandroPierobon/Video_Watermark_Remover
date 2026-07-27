#!/usr/bin/env python3
"""Installa PyTorch (CPU) per il backend LaMa, senza iopaint.

Il backend LaMa del programma carica direttamente il modello TorchScript
Big-LaMa: non usa la CLI ``iopaint``, che su Python recenti e' rotta.

Se PyTorch e' gia' presente (per esempio dopo il supporto GPU AMD) non
scarica nulla. I pesi della rete (~200 MB) arrivano al primo utilizzo.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def titolo(testo: str) -> None:
    print(f"\n{'=' * 68}\n  {testo}\n{'=' * 68}", flush=True)


def torch_ok() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def pip_install(*argomenti: str) -> None:
    comando = [sys.executable, "-m", "pip", "install", "--no-cache-dir", *argomenti]
    print("> " + " ".join(comando[3:]), flush=True)
    esito = subprocess.run(comando)
    if esito.returncode != 0:
        raise SystemExit(
            f"\nl'installazione si e' fermata (codice {esito.returncode}).\n"
            "Controlla la connessione e riprova."
        )


def main() -> int:
    analizzatore = argparse.ArgumentParser(description=__doc__)
    analizzatore.add_argument(
        "--si", action="store_true", help="non chiedere conferma"
    )
    argomenti = analizzatore.parse_args()

    print(__doc__)
    if torch_ok():
        import torch

        print(f"PyTorch e' gia' installato ({torch.__version__}): nulla da fare.")
        print("Puoi scegliere il motore LaMa dal programma.")
        return 0

    if not argomenti.si:
        risposta = input(
            "\nScarico PyTorch (CPU, qualche centinaio di MB)? [s/N] "
        ).strip().lower()
        if risposta not in {"s", "si", "sì", "y", "yes"}:
            raise SystemExit("annullato.")

    titolo("Installazione di PyTorch (CPU)")
    # Indice CPU ufficiale: evita di prendere per sbaglio ruote CUDA enormi.
    pip_install(
        "--index-url",
        "https://download.pytorch.org/whl/cpu",
        "torch",
    )

    if not torch_ok():
        raise SystemExit("PyTorch risulta ancora assente dopo l'installazione.")

    import torch

    print(f"\nFatto. PyTorch {torch.__version__} e' pronto.")
    print(
        "Riapri il programma e scegli il motore LaMa: al primo avvio "
        "scarichera' i pesi Big-LaMa (~200 MB)."
    )
    print("Non serve (e non va usato) iopaint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
