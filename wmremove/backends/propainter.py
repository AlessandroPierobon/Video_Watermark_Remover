"""Backend ProPainter: inpainting video con propagazione del flusso ottico.

E' il backend di qualita' migliore perche' non inventa i pixel mancanti: li va
a prendere dagli altri frame seguendo il movimento della scena. Nel nostro caso
funziona particolarmente bene, visto che ogni angolo e' pulito nei momenti in
cui la filigrana si trova altrove.

Richiede una GPU: 16 GB di VRAM bastano per 720p con ``--fp16`` e
``subvideo_length`` intorno a 32. Vedi il README per l'installazione su
Windows con ROCm.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

from .. import compose, video_io
from . import Options, resolve_device, resolve_propainter_dir

SCRIPT_NAME = "inference_propainter.py"


def build_command(
    script: Path, frames_dir: Path, masks_dir: Path, results_dir: Path, options: Options
) -> list[str]:
    """Riga di comando per ``inference_propainter.py``.

    I percorsi sono assoluti perche' il processo gira dentro la cartella di
    ProPainter, non in quella del progetto.
    """
    python = options.propainter_python or sys.executable
    command = [
        python,
        str(script.resolve()),
        "--video", str(frames_dir.resolve()),
        "--mask", str(masks_dir.resolve()),
        "--output", str(results_dir.resolve()),
        "--mask_dilation", str(options.propainter_mask_dilation),
        "--subvideo_length", str(options.subvideo_length),
        "--neighbor_length", str(options.neighbor_length),
        "--ref_stride", str(options.ref_stride),
        "--raft_iter", str(options.raft_iter),
        "--save_frames",
    ]
    if options.resize_ratio != 1.0:
        command += ["--resize_ratio", str(options.resize_ratio)]
    if options.fp16:
        command.append("--fp16")
    return command


def _environment(device: str) -> dict[str, str]:
    env = os.environ.copy()
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["HIP_VISIBLE_DEVICES"] = ""
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    # Evita che variabili PYTHON* di sistema dirottino l'interprete del figlio.
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    return env


def _run_logged(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    log: Callable[[str], None],
) -> None:
    """Esegue il comando inoltrando stdout/stderr riga per riga al log."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        if text:
            log(text)
    code = process.wait()
    if code != 0:
        raise RuntimeError(
            f"ProPainter e' terminato con codice {code}.\n"
            "Se compare un errore di memoria GPU, abbassa --subvideo-length "
            "(es. 16) oppure usa --resize-ratio 0.5."
        )


def _find_output_frames(results_dir: Path, frames_dir: Path) -> Path:
    """Trova la cartella di PNG prodotta da inference_propainter.py."""
    candidates = [
        results_dir / frames_dir.name / "frames",
        results_dir / "frames",
        results_dir / frames_dir.name,
        results_dir,
    ]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.png")):
            return candidate

    nested = sorted(results_dir.rglob("*.png"))
    if nested:
        return nested[0].parent

    raise RuntimeError(
        f"ProPainter non ha prodotto fotogrammi in {results_dir}.\n"
        "Controlla i messaggi sopra: di solito manca un checkpoint in "
        "weights/ oppure la GPU ha esaurito la memoria."
    )


def run(
    frames: np.ndarray, detection, options: Options, log: Callable[[str], None] = print
) -> Iterator[np.ndarray]:
    propainter_dir = resolve_propainter_dir(options.propainter_dir)
    script = propainter_dir / SCRIPT_NAME
    if not script.exists() and not options.dry_run:
        root = Path(__file__).resolve().parents[2]
        raise FileNotFoundError(
            f"{script} non trovato.\n"
            "Installa ProPainter con uno di questi comandi:\n"
            f"    python scripts/setup_propainter.py\n"
            f"    python scripts/installa_gpu.py   (Windows + GPU AMD, Python 3.12)\n"
            f"Cartelle cercate: {propainter_dir}, "
            f"{root / 'gpu' / 'ProPainter'}, {root / 'third_party' / 'ProPainter'}"
        )

    device = resolve_device(options.device)
    if device == "cpu":
        log("attenzione: ProPainter su CPU e' estremamente lento")
    elif device == "mps":
        log("attenzione: su Apple Silicon ProPainter puo' richiedere ore o esaurire la memoria")

    work = Path(options.work_dir) / "propainter"
    frames_dir = work / "video"
    masks_dir = work / "video_mask"
    results_dir = work / "risultati"
    for directory in (frames_dir, masks_dir, results_dir):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    log(f"preparo {len(frames)} frame e maschere in {work}")
    video_io.dump_frames(frames, frames_dir)
    masks_dir.mkdir(parents=True, exist_ok=True)
    for t in range(len(frames)):
        mask = detection.frame_mask(t, options.mask_threshold, options.mask_dilation)
        cv2.imwrite(str(masks_dir / f"{t:05d}.png"), mask)

    command = build_command(script, frames_dir, masks_dir, results_dir, options)
    log("comando: " + " ".join(command))
    log(f"cartella ProPainter: {propainter_dir}")

    if options.dry_run:
        log("dry-run: ProPainter non viene eseguito, il video resta invariato")
        yield from frames
        return

    results_dir.mkdir(parents=True, exist_ok=True)
    _run_logged(
        command,
        cwd=str(propainter_dir.resolve()),
        env=_environment(device),
        log=log,
    )

    output_frames = _find_output_frames(results_dir, frames_dir)
    log(f"leggo i frame ricostruiti da {output_frames}")

    log("ricompongo l'area ricostruita sui frame originali")
    for t, filled in enumerate(video_io.iter_frames_dir(output_frames)):
        if filled is None:
            raise RuntimeError(f"frame {t} illeggibile in {output_frames}")
        original = frames[t]
        mask = detection.frame_mask(t, options.mask_threshold, options.mask_dilation)
        yield compose.blend_masked(original, filled, mask, options.feather)

    if not options.keep_work:
        shutil.rmtree(work, ignore_errors=True)
