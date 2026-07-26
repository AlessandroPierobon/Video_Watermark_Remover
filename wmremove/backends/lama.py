"""Backend LaMa: inpainting neurale, un frame alla volta.

Usa direttamente il modello TorchScript Big-LaMa (lo stesso di IOPaint /
lama-cleaner). Non dipende dalla CLI ``iopaint``, che su Python 3.13+ e'
rotta (modulo ``imghdr`` rimosso) e spesso arriva senza le dipendenze.

Installazione: ``pip install torch`` (i pesi, ~200 MB, si scaricano al primo
uso nella cache di Torch Hub).
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

import cv2
import numpy as np

from .. import compose
from . import Options, resolve_device

LAMA_MODEL_URL = os.environ.get(
    "LAMA_MODEL_URL",
    "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt",
)
LAMA_MODEL_MD5 = os.environ.get("LAMA_MODEL_MD5", "e3aa4aaa15225a33ec84f9f4bc47e500")
PAD_MOD = 8


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "Il backend LaMa richiede PyTorch.\n"
            "    pip install torch\n"
            "Su Windows con GPU AMD vedi anche scripts/installa_gpu.py."
        ) from exc
    return torch


def _ceil_modulo(value: int, mod: int) -> int:
    return value if value % mod == 0 else (value // mod + 1) * mod


def _pad_hwc(image: np.ndarray, mod: int) -> np.ndarray:
    """Padding simmetrico su H/W affinche' siano multipli di ``mod``."""
    if image.ndim == 2:
        image = image[:, :, None]
    height, width = image.shape[:2]
    pad_h = _ceil_modulo(height, mod) - height
    pad_w = _ceil_modulo(width, mod) - width
    if pad_h == 0 and pad_w == 0:
        return image
    return np.pad(image, ((0, pad_h), (0, pad_w), (0, 0)), mode="symmetric")


def _model_cache_path(url: str) -> Path:
    torch = _require_torch()
    from torch.hub import get_dir

    filename = os.path.basename(urlparse(url).path) or "big-lama.pt"
    directory = Path(get_dir()) / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_with_progress(url: str, destination: Path, log: Callable[[str], None]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    last_percent = -1

    def hook(block_index: int, block_size: int, total: int) -> None:
        nonlocal last_percent
        if total <= 0:
            return
        done = min(block_index * block_size, total)
        percent = int(100.0 * done / total)
        if percent == last_percent and done < total:
            return
        last_percent = percent
        sys.stdout.write(f"\r  download LaMa: {percent:3d}%  ({done / 1e6:6.1f} MB)")
        sys.stdout.flush()

    log(f"scarico i pesi LaMa da {url}")
    urllib.request.urlretrieve(url, temporary, hook)
    sys.stdout.write("\n")
    sys.stdout.flush()
    temporary.replace(destination)


def ensure_model(log: Callable[[str], None] = print) -> Path:
    """Restituisce il percorso del checkpoint, scaricandolo se manca."""
    override = os.environ.get("LAMA_MODEL")
    if override:
        path = Path(override)
        if not path.exists():
            raise FileNotFoundError(f"LAMA_MODEL punta a un file inesistente: {path}")
        return path

    path = _model_cache_path(LAMA_MODEL_URL)
    if path.exists() and path.stat().st_size > 0:
        if LAMA_MODEL_MD5 and _md5(path) != LAMA_MODEL_MD5:
            log("checkpoint LaMa corrotto: lo riscarico")
            path.unlink(missing_ok=True)
        else:
            return path

    _download_with_progress(LAMA_MODEL_URL, path, log)
    if LAMA_MODEL_MD5 and _md5(path) != LAMA_MODEL_MD5:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            "download LaMa completato ma il checksum non torna. "
            "Controlla la connessione e riprova."
        )
    return path


def load_model(device: str, log: Callable[[str], None] = print):
    torch = _require_torch()
    path = ensure_model(log)
    log(f"carico LaMa da {path} su {device}")
    model = torch.jit.load(str(path), map_location="cpu")
    model.eval()
    model.to(device)
    return model


def inpaint_frame(model, frame_bgr: np.ndarray, mask: np.ndarray, device: str) -> np.ndarray:
    """Ricostruisce ``frame_bgr`` sotto la maschera 0/255. Ritorna BGR."""
    torch = _require_torch()
    if not mask.any():
        return frame_bgr

    height, width = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    rgb_pad = _pad_hwc(rgb, PAD_MOD)
    mask_pad = _pad_hwc(mask, PAD_MOD)[:, :, 0]

    image = np.transpose(rgb_pad.astype(np.float32) / 255.0, (2, 0, 1))
    mask_n = (mask_pad.astype(np.float32) / 255.0)[None, ...]
    mask_n = (mask_n > 0).astype(np.float32)

    image_t = torch.from_numpy(image).unsqueeze(0).to(device)
    mask_t = torch.from_numpy(mask_n).unsqueeze(0).to(device)

    with torch.inference_mode():
        result = model(image_t, mask_t)

    out = result[0].permute(1, 2, 0).detach().cpu().numpy()
    out = np.clip(out * 255.0, 0, 255).astype(np.uint8)
    out = out[:height, :width]
    return cv2.cvtColor(out, cv2.COLOR_RGB2BGR)


def run(
    frames: np.ndarray, detection, options: Options, log: Callable[[str], None] = print
) -> Iterator[np.ndarray]:
    device = resolve_device(options.device)
    if device == "mps":
        # Il JIT di LaMa non ha kernel MPS affidabili.
        log("LaMa su MPS non e' supportato: uso CPU")
        device = "cpu"

    if options.dry_run:
        log(f"dry-run: LaMa girerebbe su {device}, il video resta invariato")
        yield from frames
        return

    model = load_model(device, log)
    total = len(frames)
    log(f"inpainting LaMa su {total} frame ({device})")

    for t in range(total):
        mask = detection.frame_mask(t, options.mask_threshold, options.mask_dilation)
        if mask.any():
            filled = inpaint_frame(model, frames[t], mask, device)
            yield compose.blend_masked(frames[t], filled, mask, options.feather)
        else:
            yield frames[t]
        if (t + 1) % 10 == 0 or t + 1 == total:
            log(f"  LaMa {t + 1}/{total} frame")
