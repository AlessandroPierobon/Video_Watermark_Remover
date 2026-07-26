#!/usr/bin/env python3
"""Disegna l'icona dell'applicazione e la salva come .ico multi-risoluzione.

Il disegno e' fatto a codice cosi' l'icona si puo' rigenerare senza dipendere
da file binari nel repository. Le immagini dentro il .ico sono in formato BMP
non compresso: e' la variante piu' vecchia e piu' compatibile, l'unica che
tutti gli strumenti (NSIS compreso) accettano senza discutere.

Uso:  python installer/crea_icona.py [destinazione.ico]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import cv2
import numpy as np

MISURE = (16, 24, 32, 48, 64, 128, 256)
LATO = 256


def _rettangolo_arrotondato(
    tela: np.ndarray, x1: int, y1: int, x2: int, y2: int, raggio: int, valore
) -> None:
    cv2.rectangle(tela, (x1 + raggio, y1), (x2 - raggio, y2), valore, -1, cv2.LINE_AA)
    cv2.rectangle(tela, (x1, y1 + raggio), (x2, y2 - raggio), valore, -1, cv2.LINE_AA)
    for cx, cy in ((x1 + raggio, y1 + raggio), (x2 - raggio, y1 + raggio),
                   (x1 + raggio, y2 - raggio), (x2 - raggio, y2 - raggio)):
        cv2.circle(tela, (cx, cy), raggio, valore, -1, cv2.LINE_AA)


def disegna() -> np.ndarray:
    """Ritorna l'icona 256x256 in BGRA: una filigrana che si dissolve."""
    colore = np.zeros((LATO, LATO, 3), np.float32)
    alto = np.array([104, 62, 44], np.float32)   # BGR: indaco chiaro
    basso = np.array([48, 26, 18], np.float32)   # BGR: blu notte
    for y in range(LATO):
        colore[y, :] = alto + (basso - alto) * (y / (LATO - 1))

    sagoma = np.zeros((LATO, LATO), np.float32)
    _rettangolo_arrotondato(sagoma, 6, 6, LATO - 7, LATO - 7, 52, 1.0)

    # Cornice del fotogramma video.
    cornice = np.zeros((LATO, LATO), np.float32)
    _rettangolo_arrotondato(cornice, 40, 62, LATO - 41, LATO - 63, 18, 1.0)
    interno = np.zeros((LATO, LATO), np.float32)
    _rettangolo_arrotondato(interno, 52, 74, LATO - 53, LATO - 75, 10, 1.0)
    cornice = np.clip(cornice - interno, 0, 1)
    colore = colore * (1 - cornice[..., None]) + np.float32([238, 232, 226]) * cornice[..., None]

    # La "filigrana": due barre come una scritta, che sfumano da destra a sinistra.
    filigrana = np.zeros((LATO, LATO), np.float32)
    _rettangolo_arrotondato(filigrana, 78, 116, 178, 134, 9, 1.0)
    _rettangolo_arrotondato(filigrana, 78, 146, 146, 164, 9, 1.0)
    dissolvenza = np.clip((np.arange(LATO, dtype=np.float32) - 74) / 96.0, 0, 1)
    filigrana *= dissolvenza[None, :]
    colore = colore * (1 - filigrana[..., None] * 0.92) + np.float32(
        [255, 255, 255]
    ) * (filigrana[..., None] * 0.92)

    # Scintille sul bordo in cui la filigrana sparisce.
    for (cx, cy, raggio) in ((92, 100, 5), (74, 152, 4), (104, 176, 3)):
        cv2.circle(colore, (cx, cy), raggio, (150, 214, 250), -1, cv2.LINE_AA)

    bgra = np.dstack([np.clip(colore, 0, 255).astype(np.uint8),
                      (sagoma * 255).astype(np.uint8)])
    return bgra


def _bmp(immagine: np.ndarray) -> bytes:
    """Confeziona un BGRA come immagine BMP interna a un .ico."""
    altezza, larghezza = immagine.shape[:2]
    intestazione = struct.pack(
        "<IiiHHIIiiII", 40, larghezza, altezza * 2, 1, 32, 0,
        larghezza * altezza * 4, 2835, 2835, 0, 0,
    )
    pixel = np.flipud(immagine).tobytes()
    riga_maschera = ((larghezza + 31) // 32) * 4
    maschera = b"\x00" * (riga_maschera * altezza)
    return intestazione + pixel + maschera


def scrivi_ico(destinazione: Path, base: np.ndarray) -> None:
    immagini = []
    for misura in MISURE:
        ridotta = cv2.resize(base, (misura, misura), interpolation=cv2.INTER_AREA)
        immagini.append((misura, _bmp(ridotta)))

    inizio = 6 + 16 * len(immagini)
    voci, corpo = b"", b""
    for misura, dati in immagini:
        voci += struct.pack(
            "<BBBBHHII", misura % 256, misura % 256, 0, 0, 1, 32,
            len(dati), inizio + len(corpo),
        )
        corpo += dati
    destinazione.write_bytes(struct.pack("<HHH", 0, 1, len(immagini)) + voci + corpo)


def main() -> int:
    destinazione = Path(sys.argv[1] if len(sys.argv) > 1 else "installer/icona.ico")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    base = disegna()
    scrivi_ico(destinazione, base)
    anteprima = destinazione.with_suffix(".png")
    cv2.imwrite(str(anteprima), base)
    print(f"scritto {destinazione} ({destinazione.stat().st_size / 1024:.0f} KB)")
    print(f"scritto {anteprima}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
