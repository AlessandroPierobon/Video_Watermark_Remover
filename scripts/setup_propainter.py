#!/usr/bin/env python3
"""Prepara il backend ProPainter: clone del repository e download dei pesi.

    python scripts/setup_propainter.py

Non installa PyTorch: quello va installato prima, con le ruote giuste per la
propria GPU (vedi README). Qui si scaricano solo il codice di ProPainter e i
tre checkpoint necessari all'inferenza, circa 500 MB in tutto.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_URL = "https://github.com/sczhou/ProPainter.git"
REPO_ZIP = "https://github.com/sczhou/ProPainter/archive/refs/heads/main.zip"

WEIGHTS_BASE = "https://github.com/sczhou/ProPainter/releases/download/v0.1.0"
WEIGHTS = (
    "raft-things.pth",
    "recurrent_flow_completion.pth",
    "ProPainter.pth",
)

_ROCM_VERSION_BLOCK = '''# Patch video-watermark-remover: compatibile con torch+rocm.
_TORCH_VERSION_MATCH = re.match(r"^(\\d+)\\.(\\d+)\\.(\\d+)", torch.__version__ or "0.0.0")
IS_HIGH_VERSION = (
    [int(m) for m in _TORCH_VERSION_MATCH.groups()] >= [1, 12, 0]
    if _TORCH_VERSION_MATCH
    else False
)'''


def _progress(name: str):
    def hook(block_index: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        done = min(block_index * block_size, total)
        percent = 100.0 * done / total
        sys.stdout.write(f"\r  {name}: {percent:5.1f}%  ({done / 1e6:6.1f} MB)")
        sys.stdout.flush()

    return hook


def clone(target: Path) -> None:
    """Porta il codice di ProPainter in ``target``.

    Si prova prima con git, che permette aggiornamenti successivi; se git non
    c'e' (tipico su Windows appena installato) si scarica l'archivio zip del
    ramo principale, che non richiede nulla di installato.
    """
    if (target / "inference_propainter.py").exists():
        print(f"ProPainter gia' presente in {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        print(f"clono {REPO_URL} in {target}")
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, str(target)], check=True
        )
        return
    except (OSError, subprocess.CalledProcessError):
        print("  git non disponibile: scarico l'archivio zip")

    _scarica_zip(target)


def _scarica_zip(target: Path) -> None:
    import shutil
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as temporanea:
        archivio = Path(temporanea) / "propainter.zip"
        urllib.request.urlretrieve(REPO_ZIP, archivio, _progress("codice"))
        print()
        with zipfile.ZipFile(archivio) as zip_file:
            zip_file.extractall(temporanea)
        estratte = [p for p in Path(temporanea).iterdir() if p.is_dir()]
        if not estratte:
            raise RuntimeError("archivio di ProPainter vuoto o non valido")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(estratte[0]), str(target))
    print(f"ProPainter estratto in {target}")


def download_weights(target: Path) -> None:
    weights_dir = target / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    for name in WEIGHTS:
        destination = weights_dir / name
        if destination.exists() and destination.stat().st_size > 0:
            print(f"  {name}: gia' scaricato")
            continue
        url = f"{WEIGHTS_BASE}/{name}"
        print(f"  scarico {url}")
        urllib.request.urlretrieve(url, destination, _progress(name))
        print()


def patch_for_rocm(target: Path) -> None:
    """Adatta ProPainter alle build PyTorch ROCm (es. ``2.9.1+rocm7.2.1``).

    Il ``model/misc.py`` originale usa un regex sulla versione di torch che non
    riconosce il suffisso ``+rocmX.Y.Z`` e fallisce con ``IndexError`` ancora
    prima di partire. Inoltre richiede ``cudnn``, assente o inutile su ROCm.
    """
    misc = target / "model" / "misc.py"
    if not misc.exists():
        print(f"  patch ROCm: {misc} non trovato, salto")
        return

    text = misc.read_text(encoding="utf-8")
    if "_TORCH_VERSION_MATCH = re.match" in text:
        print("  patch ROCm: gia' applicata")
        return

    patched, n = re.subn(
        r"IS_HIGH_VERSION\s*=\s*\[int\(m\) for m in list\(re\.findall\([\s\S]*?\)\[0\]\[:3\]\)\]\s*>=\s*\[1,\s*12,\s*0\]",
        _ROCM_VERSION_BLOCK,
        text,
        count=1,
    )
    if n == 0:
        print("  patch ROCm: blocco versione non riconosciuto, controllo a mano")
        return
    text = patched

    text = text.replace(
        "return True if torch.cuda.is_available() and torch.backends.cudnn.is_available() else False",
        "return bool(torch.cuda.is_available())",
    )
    text = text.replace(
        "return torch.device('cuda'+gpu_str if torch.cuda.is_available() and torch.backends.cudnn.is_available() else 'cpu')",
        "return torch.device('cuda'+gpu_str if torch.cuda.is_available() else 'cpu')",
    )
    misc.write_text(text, encoding="utf-8")
    print(f"  patch ROCm applicata a {misc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "third_party" / "ProPainter",
        help="cartella di destinazione",
    )
    parser.add_argument(
        "--skip-weights", action="store_true", help="clona soltanto, senza scaricare i pesi"
    )
    parser.add_argument(
        "--patch-only",
        action="store_true",
        help="applica solo la patch ROCm a una cartella ProPainter gia' presente",
    )
    args = parser.parse_args()

    if args.patch_only:
        patch_for_rocm(args.dir)
        return 0

    clone(args.dir)
    patch_for_rocm(args.dir)
    if not args.skip_weights:
        download_weights(args.dir)

    print(
        "\nfatto. Verifica le dipendenze Python di ProPainter con:\n"
        f"    pip install -r {args.dir / 'requirements.txt'}\n"
        "(PyTorch va installato a parte, con le ruote adatte alla tua GPU)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
