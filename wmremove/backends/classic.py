"""Backend classico: inversione dell'alpha compositing piu' inpainting.

Non serve nessuna rete neurale. Sfrutta il fatto che una filigrana
semitrasparente non distrugge l'informazione sottostante: la attenua e basta.
Dal modello

    osservato = (1 - alpha) * sfondo + alpha * colore

si ricava direttamente

    sfondo = (osservato - alpha * colore) / (1 - alpha)

che e' esatto finche' ``alpha`` non e' troppo vicina a 1. Solo i pixel quasi
opachi, dove la divisione esploderebbe, vengono ricostruiti con ``cv2.inpaint``.
"""

from __future__ import annotations

from typing import Callable, Iterator

import cv2
import numpy as np

from .. import compose
from . import Options

_INPAINT_METHODS = {"telea": cv2.INPAINT_TELEA, "ns": cv2.INPAINT_NS}


def _inpaint(image: np.ndarray, mask: np.ndarray, options: Options) -> np.ndarray:
    method = _INPAINT_METHODS.get(options.inpaint_method, cv2.INPAINT_TELEA)
    return cv2.inpaint(image, mask, options.inpaint_radius, method)


def unblend_frame(
    frame: np.ndarray, alpha: np.ndarray, color: np.ndarray, options: Options
) -> np.ndarray:
    """Recupera lo sfondo sotto la filigrana per un singolo frame."""
    a = alpha[..., None].astype(np.float32)
    # Dove alpha supera la soglia la divisione amplificherebbe troppo il
    # rumore: si blocca il denominatore e si lascia il lavoro all'inpainting.
    denominator = np.maximum(1.0 - a, 1.0 - options.unblend_max)
    recovered = (frame.astype(np.float32) - a * color.reshape(1, 1, 3)) / denominator
    recovered = np.clip(recovered, 0, 255).astype(np.uint8)

    hard = (alpha >= options.unblend_max).astype(np.uint8) * 255
    if hard.any():
        hard = cv2.dilate(hard, np.ones((3, 3), np.uint8))
        recovered = _inpaint(recovered, hard, options)

    # L'inversione amplifica il rumore di compressione di un fattore 1/(1-alpha):
    # si compensa con una lisciatura crescente nelle zone piu' opache. L'obiettivo
    # e' pareggiare la grana di quello che sta intorno, non azzerarla: lisciare
    # troppo lascia una macchia piatta ben visibile.
    if options.unblend_denoise > 0:
        smoothed = cv2.bilateralFilter(recovered, 7, 35, 7)
        mix = np.clip(alpha / max(options.unblend_max, 1e-3), 0.0, 1.0)
        mix = (mix * options.unblend_denoise)[..., None]
        recovered = np.clip(
            recovered.astype(np.float32) * (1.0 - mix) + smoothed.astype(np.float32) * mix,
            0,
            255,
        ).astype(np.uint8)
    return recovered


def run(
    frames: np.ndarray, detection, options: Options, log: Callable[[str], None] = print
) -> Iterator[np.ndarray]:
    """Elabora il video frame per frame, senza tenere l'uscita in memoria."""
    log(f"backend classic, modalita' {options.classic_mode}")

    for t, frame in enumerate(frames):
        if not detection.is_dirty(t):
            yield frame
            continue

        if options.classic_mode == "inpaint":
            mask = detection.frame_mask(t, options.mask_threshold, options.mask_dilation)
            filled = _inpaint(frame, mask, options)
            yield compose.blend_masked(frame, filled, mask, options.feather)
            continue

        alpha = detection.frame_alpha(t)
        filled = unblend_frame(frame, alpha, detection.color, options)
        mask = (alpha > 0).astype(np.uint8) * 255
        yield compose.blend_masked(frame, filled, mask, options.feather)
