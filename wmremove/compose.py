"""Reincollaggio della sola area ricostruita sul frame originale.

Tutti i backend, chi piu' chi meno, rielaborano l'intero frame: ProPainter lo
ridimensiona a multipli di 8 e lo ricostruisce, LaMa lo ricodifica. Per non
degradare il 97% di immagine che non contiene filigrana si prende dal risultato
solo l'area mascherata, con un bordo sfumato per evitare il gradino.
"""

from __future__ import annotations

import cv2
import numpy as np


def feather_weight(mask: np.ndarray, radius: int = 8) -> np.ndarray:
    """Peso in [0, 1]: pieno dentro la maschera, in dissolvenza appena fuori.

    La sfumatura viene costruita *espandendo* la maschera prima di sfocarla,
    cosi' la zona di transizione cade fuori dalla filigrana, dove il frame
    originale e' pulito.
    """
    binary = (mask > 0).astype(np.float32)
    if radius <= 0:
        return binary
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    grown = cv2.dilate(binary, kernel)
    size = 2 * radius + 1
    weight = cv2.GaussianBlur(grown, (size, size), radius / 2.0)
    return np.clip(np.maximum(weight, binary), 0.0, 1.0)


def blend(original: np.ndarray, filled: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Fonde ``filled`` dentro ``original`` seguendo il peso sfumato."""
    if filled.shape != original.shape:
        filled = cv2.resize(
            filled, (original.shape[1], original.shape[0]), interpolation=cv2.INTER_LANCZOS4
        )
    w = weight[..., None].astype(np.float32)
    out = original.astype(np.float32) * (1.0 - w) + filled.astype(np.float32) * w
    return np.clip(out, 0, 255).astype(np.uint8)


def blend_masked(
    original: np.ndarray, filled: np.ndarray, mask: np.ndarray, feather: int = 8
) -> np.ndarray:
    """Scorciatoia: calcola il peso sfumato dalla maschera e fonde."""
    if not mask.any():
        return original
    return blend(original, filled, feather_weight(mask, feather))
