"""Rilevamento automatico di una filigrana che cambia angolo nel tempo.

L'idea di fondo e' che tra due frame consecutivi lo sfondo di un video cambia
pochissimo, mentre una filigrana che compare o scompare produce una differenza
grande e con bordi netti. I frame di transizione rivelano quindi la forma
esatta della filigrana senza bisogno di disegnare maschere a mano.

Il modello di composizione usato ovunque nel modulo e'

    osservato = (1 - alpha) * sfondo + alpha * colore_filigrana

dove ``alpha`` e' la mappa di opacita' (la *matte*) da stimare e
``colore_filigrana`` e' un colore costante, in pratica quasi bianco.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

CORNERS: tuple[str, ...] = ("tl", "tr", "bl", "br")

CORNER_LABELS = {
    "tl": "alto-sinistra",
    "tr": "alto-destra",
    "bl": "basso-sinistra",
    "br": "basso-destra",
}

_CORNER_COLORS = {
    "tl": (80, 80, 255),
    "tr": (80, 220, 255),
    "bl": (120, 255, 120),
    "br": (255, 160, 80),
}


@dataclass(frozen=True)
class Roi:
    """Rettangolo di ricerca ancorato a un angolo del frame."""

    name: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def slice(self) -> tuple[slice, slice]:
        return slice(self.y0, self.y1), slice(self.x0, self.x1)

    @property
    def size(self) -> tuple[int, int]:
        return self.y1 - self.y0, self.x1 - self.x0


@dataclass
class Event:
    """Comparsa (``sign`` = +1) o scomparsa (``sign`` = -1) della filigrana.

    ``start`` e' l'ultimo frame con lo stato precedente, ``end`` il primo frame
    con il nuovo stato. Se la transizione dura piu' di un frame (dissolvenza)
    i frame intermedi sono considerati incerti e vengono mascherati comunque.
    """

    corner: str
    start: int
    end: int
    sign: int
    strength: float
    purity: float = 1.0


@dataclass
class Detection:
    """Esito del rilevamento su un video."""

    shape: tuple[int, int]
    rois: dict[str, Roi]
    alpha: dict[str, np.ndarray]
    color: np.ndarray
    presence: dict[str, np.ndarray]
    events: list[Event]
    curves: dict[str, np.ndarray] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def n_frames(self) -> int:
        return len(next(iter(self.presence.values())))

    def active_corners(self, t: int) -> list[str]:
        return [c for c in CORNERS if self.presence[c][t]]

    def is_dirty(self, t: int) -> bool:
        return bool(self.active_corners(t))

    def frame_alpha(self, t: int) -> np.ndarray:
        """Matte di opacita' a piena risoluzione per il frame ``t``."""
        out = np.zeros(self.shape, np.float32)
        for corner in self.active_corners(t):
            ys, xs = self.rois[corner].slice
            np.maximum(out[ys, xs], self.alpha[corner], out=out[ys, xs])
        return out

    def frame_mask(self, t: int, threshold: float = 0.05, dilation: int = 6) -> np.ndarray:
        """Maschera binaria 0/255 dei pixel da ricostruire."""
        mask = (self.frame_alpha(t) >= threshold).astype(np.uint8) * 255
        if dilation > 0 and mask.any():
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * dilation + 1, 2 * dilation + 1)
            )
            mask = cv2.dilate(mask, kernel)
        return mask

    def intervals(self) -> list[tuple[str, int, int]]:
        """Intervalli ``(angolo, primo_frame, ultimo_frame)`` di presenza."""
        out: list[tuple[str, int, int]] = []
        for corner in CORNERS:
            present = self.presence[corner]
            start = None
            for t, value in enumerate(present):
                if value and start is None:
                    start = t
                elif not value and start is not None:
                    out.append((corner, start, t - 1))
                    start = None
            if start is not None:
                out.append((corner, start, len(present) - 1))
        return sorted(out, key=lambda item: item[1])

    def summary(self, fps: float) -> str:
        lines = [
            f"colore filigrana stimato (BGR): "
            f"{self.color[0]:.0f}, {self.color[1]:.0f}, {self.color[2]:.0f}",
            f"transizioni rilevate: {len(self.events)}",
        ]
        for corner, first, last in self.intervals():
            area = int((self.alpha[corner] >= 0.05).sum())
            lines.append(
                f"  {CORNER_LABELS[corner]:<16} frame {first:>4}-{last:<4} "
                f"({first / fps:5.2f}s - {last / fps:5.2f}s), {area} px coperti"
            )
        return "\n".join(lines)


def corner_rois(
    height: int, width: int, frac: tuple[float, float] = (0.5, 0.28)
) -> dict[str, Roi]:
    """Rettangoli di ricerca nei quattro angoli, espressi in frazione di frame."""
    rw = int(round(width * frac[0]))
    rh = int(round(height * frac[1]))
    return {
        "tl": Roi("tl", 0, 0, rw, rh),
        "tr": Roi("tr", width - rw, 0, width, rh),
        "bl": Roi("bl", 0, height - rh, rw, height),
        "br": Roi("br", width - rw, height - rh, width, height),
    }


def _gray_stack(frames: np.ndarray, roi: Roi) -> np.ndarray:
    ys, xs = roi.slice
    rh, rw = roi.size
    out = np.empty((len(frames), rh, rw), np.uint8)
    for i, frame in enumerate(frames):
        out[i] = cv2.cvtColor(frame[ys, xs], cv2.COLOR_BGR2GRAY)
    return out


def _diff_curve(stack: np.ndarray) -> np.ndarray:
    """Differenza media assoluta tra frame consecutivi: ``d[t]`` lega t e t+1."""
    current = stack[:-1].astype(np.int16)
    following = stack[1:].astype(np.int16)
    return np.abs(following - current).mean(axis=(1, 2)).astype(np.float32)


def _threshold(curve: np.ndarray, mad_k: float, min_ratio: float) -> float:
    """Soglia robusta: mediana piu' k deviazioni assolute mediane."""
    median = float(np.median(curve))
    mad = float(np.median(np.abs(curve - median)))
    robust_sigma = 1.4826 * mad
    return max(median + mad_k * robust_sigma, median * min_ratio, 1.5)


def _runs_above(curve: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Raggruppa gli indici sopra soglia in tratti contigui."""
    above = curve > threshold
    runs: list[tuple[int, int]] = []
    start = None
    for i, value in enumerate(above):
        if value and start is None:
            start = i
        elif not value and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(above) - 1))
    return runs


def _event_sign(before: np.ndarray, after: np.ndarray) -> tuple[int, float, float]:
    """Classifica un salto tra due frame come comparsa, scomparsa o rumore.

    Sovrapporre un colore chiaro puo' solo schiarire i pixel: una comparsa
    produce quindi differenze quasi tutte positive e una scomparsa differenze
    quasi tutte negative. Un dettaglio dello sfondo che si sposta, al contrario,
    schiarisce da una parte e scurisce dall'altra in misura simile.

    La "purezza" restituita e' la frazione di variazione positiva: vicina a 1 o
    a 0 per una vera transizione, vicina a 0,5 per il semplice movimento.
    """
    delta = after.astype(np.float32) - before.astype(np.float32)
    peak = float(np.abs(delta).max())
    if peak <= 0:
        return 0, 0.0, 0.5
    strong = np.abs(delta) > 0.3 * peak
    positive = float(delta[strong & (delta > 0)].sum())
    negative = float(-delta[strong & (delta < 0)].sum())
    total = positive + negative
    if total <= 0:
        return 0, 0.0, 0.5
    purity = positive / total
    sign = 1 if positive >= negative else -1
    return sign, max(positive, negative), purity


def _alpha_from_event(
    frames: np.ndarray, roi: Roi, event: Event, color: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Stima ``alpha`` da una singola transizione.

    Invertendo il modello di composizione, ``alpha = (sporco - pulito) /
    (colore - pulito)``. Il peso tiene conto del fatto che dove lo sfondo e'
    gia' quasi del colore della filigrana la stima e' mal condizionata.
    """
    ys, xs = roi.slice
    before = frames[event.start][ys, xs].astype(np.float32)
    after = frames[event.end][ys, xs].astype(np.float32)
    clean, dirty = (before, after) if event.sign > 0 else (after, before)

    denominator = color.reshape(1, 1, 3) - clean
    delta = dirty - clean
    weight = np.clip(denominator, 0.0, None) / 255.0
    alpha_per_channel = np.clip(delta / np.maximum(denominator, 1.0), 0.0, 1.0)

    numerator = (alpha_per_channel * weight).sum(axis=2)
    total = weight.sum(axis=2)
    alpha = numerator / np.maximum(total, 1e-6)
    return alpha, total


def _consistency_gate(
    per_event: list[tuple[np.ndarray, np.ndarray]],
    mean_alpha: np.ndarray,
    ratio: float = 0.35,
    weight_floor: float = 0.3,
) -> np.ndarray:
    """Tiene solo i pixel su cui tutte le transizioni sono d'accordo.

    La filigrana e' la stessa a ogni comparsa, quindi risulta opaca in tutte le
    transizioni di quell'angolo. Un dettaglio dello sfondo che si sposta invece
    compare in una transizione sola: confrontando gli eventi tra loro sparisce.
    I pixel dove lo sfondo e' gia' quasi bianco (peso basso) non fanno testo,
    perche' li' la stima non e' affidabile.
    """
    if len(per_event) < 2:
        return np.ones_like(mean_alpha, bool)

    agree = np.zeros(mean_alpha.shape, np.int32)
    checked = np.zeros(mean_alpha.shape, np.int32)
    for alpha, weight in per_event:
        usable = weight > weight_floor
        checked += usable
        agree += usable & (alpha >= ratio * mean_alpha)
    # Senza eventi affidabili ci si affida alla media, altrimenti serve
    # l'accordo di almeno il 60% delle transizioni utilizzabili.
    required = np.maximum(1, np.ceil(0.6 * checked)).astype(np.int32)
    return np.where(checked == 0, True, agree >= required)


def _clean_alpha(alpha: np.ndarray, threshold: float, min_area: int = 24) -> np.ndarray:
    """Azzera il rumore residuo dovuto al movimento dello sfondo."""
    out = np.where(alpha >= threshold, alpha, 0.0).astype(np.float32)
    binary = (out > 0).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    keep = np.zeros_like(binary)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= min_area:
            keep[labels == index] = 1
    return out * keep


def _estimate_color(
    frames: np.ndarray,
    rois: dict[str, Roi],
    events: list[Event],
    alphas: dict[str, np.ndarray],
) -> np.ndarray:
    """Ricava il colore della filigrana dai pixel piu' opachi.

    Dal modello di composizione, ``colore = (sporco - (1-alpha)*pulito) / alpha``:
    dove ``alpha`` e' alta questa formula e' stabile.
    """
    samples: list[np.ndarray] = []
    for event in events:
        alpha = alphas[event.corner]
        selection = alpha > 0.5
        if int(selection.sum()) < 50:
            continue
        ys, xs = rois[event.corner].slice
        before = frames[event.start][ys, xs].astype(np.float32)
        after = frames[event.end][ys, xs].astype(np.float32)
        clean, dirty = (before, after) if event.sign > 0 else (after, before)
        a = alpha[..., None]
        color = (dirty - (1.0 - a) * clean) / np.maximum(a, 1e-3)
        samples.append(color[selection])

    if not samples:
        return np.full(3, 255.0, np.float32)
    median = np.median(np.concatenate(samples), axis=0)
    return np.clip(median, 180.0, 255.0).astype(np.float32)


def _presence_from_events(
    n_frames: int, events: list[Event]
) -> tuple[np.ndarray, list[str]]:
    """Ricostruisce lo stato presente/assente frame per frame."""
    present = np.zeros(n_frames, bool)
    warnings: list[str] = []
    if not events:
        return present, warnings

    # Se la prima transizione e' una scomparsa, la filigrana c'era gia'
    # all'inizio del video.
    state = events[0].sign < 0
    cursor = 0
    previous_sign = 0
    for event in events:
        if previous_sign == event.sign:
            warnings.append(
                f"due transizioni consecutive con lo stesso segno al frame {event.start}"
            )
        present[cursor : event.start + 1] = state
        # I frame interni a una dissolvenza sono incerti: li mascheriamo.
        present[event.start + 1 : event.end] = True
        state = event.sign > 0
        cursor = event.end
        previous_sign = event.sign
    present[cursor:] = state
    return present, warnings


def _dilate_time(present: np.ndarray, radius: int) -> np.ndarray:
    """Allarga di qualche frame gli intervalli di presenza, per sicurezza."""
    if radius <= 0:
        return present
    out = present.copy()
    for shift in range(1, radius + 1):
        out[shift:] |= present[:-shift]
        out[:-shift] |= present[shift:]
    return out


def detect(
    frames: np.ndarray,
    roi_frac: tuple[float, float] = (0.5, 0.28),
    alpha_threshold: float = 0.06,
    mad_k: float = 6.0,
    min_ratio: float = 2.5,
    min_purity: float = 0.88,
    temporal_dilation: int = 1,
    log=print,
) -> Detection:
    """Individua posizione, forma e intervalli temporali della filigrana."""
    n_frames, height, width = frames.shape[:3]
    rois = corner_rois(height, width, roi_frac)

    curves: dict[str, np.ndarray] = {}
    thresholds: dict[str, float] = {}
    events: list[Event] = []
    impure = 0

    for corner, roi in rois.items():
        stack = _gray_stack(frames, roi)
        curve = _diff_curve(stack)
        threshold = _threshold(curve, mad_k, min_ratio)
        curves[corner] = curve
        thresholds[corner] = threshold

        for start, end in _runs_above(curve, threshold):
            # d[i] confronta i frame i e i+1: il tratto [start, end] significa
            # che lo stato passa dal frame start al frame end+1.
            sign, strength, purity = _event_sign(stack[start], stack[end + 1])
            if sign == 0:
                continue
            if max(purity, 1.0 - purity) < min_purity:
                impure += 1
                continue
            events.append(Event(corner, start, end + 1, sign, strength, purity))

    events.sort(key=lambda item: item.start)
    if impure:
        log(f"scartati {impure} salti dovuti al solo movimento dello sfondo")
    log(f"transizioni candidate: {len(events)}")

    # Prima passata con filigrana ipotizzata bianca, poi si stima il colore
    # vero e si rifa' la stima della matte.
    color = np.full(3, 255.0, np.float32)
    alphas: dict[str, np.ndarray] = {}
    for _ in range(2):
        alphas = {}
        for corner, roi in rois.items():
            corner_events = [e for e in events if e.corner == corner]
            if not corner_events:
                alphas[corner] = np.zeros(roi.size, np.float32)
                continue
            per_event = [
                _alpha_from_event(frames, roi, event, color) for event in corner_events
            ]
            accumulated = np.zeros(roi.size, np.float32)
            weights = np.zeros(roi.size, np.float32)
            for alpha, weight in per_event:
                accumulated += alpha * weight
                weights += weight
            mean_alpha = accumulated / np.maximum(weights, 1e-6)
            mean_alpha *= _consistency_gate(per_event, mean_alpha)
            alphas[corner] = _clean_alpha(mean_alpha, alpha_threshold)
        color = _estimate_color(frames, rois, events, alphas)

    # Un angolo senza pixel sopravvissuti alla pulizia non conteneva la
    # filigrana: le sue transizioni erano solo movimento di sfondo.
    valid_corners = {c for c, a in alphas.items() if a.max() > 0}
    dropped = [e for e in events if e.corner not in valid_corners]
    if dropped:
        log(f"scartate {len(dropped)} transizioni senza forma coerente")
    events = [e for e in events if e.corner in valid_corners]

    presence: dict[str, np.ndarray] = {}
    for corner in CORNERS:
        corner_events = [e for e in events if e.corner == corner]
        present, warnings = _presence_from_events(n_frames, corner_events)
        for warning in warnings:
            log(f"attenzione ({CORNER_LABELS[corner]}): {warning}")
        presence[corner] = _dilate_time(present, temporal_dilation)

    return Detection(
        shape=(height, width),
        rois=rois,
        alpha=alphas,
        color=color,
        presence=presence,
        events=events,
        curves=curves,
        thresholds=thresholds,
    )


def manual_detection(
    shape: tuple[int, int], box: tuple[int, int, int, int], n_frames: int
) -> Detection:
    """Rilevamento sostituito da un rettangolo fisso ``(x, y, w, h)``."""
    height, width = shape
    x, y, w, h = box
    x1, y1 = min(x + w, width), min(y + h, height)
    roi = Roi("tl", x, y, x1, y1)
    alpha = np.ones((y1 - y, x1 - x), np.float32)
    presence = {c: np.zeros(n_frames, bool) for c in CORNERS}
    presence["tl"] = np.ones(n_frames, bool)
    return Detection(
        shape=shape,
        rois={"tl": roi, "tr": roi, "bl": roi, "br": roi},
        alpha={"tl": alpha, "tr": alpha, "bl": alpha, "br": alpha},
        color=np.full(3, 255.0, np.float32),
        presence=presence,
        events=[],
    )


def _draw_curves(detection: Detection, path: Path) -> None:
    """Grafico delle curve di differenza, disegnato direttamente con OpenCV."""
    row_h, width, pad = 150, 1180, 50
    canvas = np.full((row_h * 4 + pad, width + 2 * pad, 3), 250, np.uint8)

    for index, corner in enumerate(CORNERS):
        curve = detection.curves.get(corner)
        if curve is None or len(curve) == 0:
            continue
        top = pad // 2 + index * row_h
        base = top + row_h - 40
        scale = (row_h - 60) / max(float(curve.max()), 1e-6)

        cv2.line(canvas, (pad, base), (pad + width, base), (200, 200, 200), 1)
        points = [
            (pad + int(i * width / max(len(curve) - 1, 1)), base - int(v * scale))
            for i, v in enumerate(curve)
        ]
        cv2.polylines(canvas, [np.array(points, np.int32)], False, (90, 90, 90), 1)

        threshold_y = base - int(detection.thresholds[corner] * scale)
        cv2.line(canvas, (pad, threshold_y), (pad + width, threshold_y), (60, 60, 230), 1)

        for event in detection.events:
            if event.corner != corner:
                continue
            x = pad + int(event.start * width / max(len(curve) - 1, 1))
            cv2.line(canvas, (x, base), (x, top + 10), _CORNER_COLORS[corner], 2)
            label = "comparsa" if event.sign > 0 else "scomparsa"
            cv2.putText(
                canvas, label, (x + 4, top + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1, cv2.LINE_AA,
            )

        cv2.putText(
            canvas, f"{CORNER_LABELS[corner]} (soglia in rosso)", (pad, top + 12),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA,
        )

    cv2.imwrite(str(path), canvas)


def overlay_frame(
    frame: np.ndarray, detection: Detection, t: int, fps: float,
    threshold: float = 0.05, dilation: int = 6,
) -> np.ndarray:
    """Frame con il contorno della maschera e l'angolo attivo evidenziati."""
    out = frame.copy()
    mask = detection.frame_mask(t, threshold, dilation)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, contours, -1, (0, 0, 255), 2)

    active = detection.active_corners(t)
    for corner in active:
        roi = detection.rois[corner]
        cv2.rectangle(
            out, (roi.x0, roi.y0), (roi.x1 - 1, roi.y1 - 1), _CORNER_COLORS[corner], 1
        )

    label = ", ".join(CORNER_LABELS[c] for c in active) if active else "nessuna filigrana"
    text = f"frame {t:04d}  t={t / fps:5.2f}s  {label}"
    cv2.rectangle(out, (0, 0), (out.shape[1], 28), (0, 0, 0), -1)
    cv2.putText(
        out, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA
    )
    return out


def save_debug(
    detection: Detection, frames: np.ndarray, out_dir: Path, fps: float,
    threshold: float = 0.05, dilation: int = 6,
) -> Path:
    """Salva matte, grafico delle transizioni, report e video con overlay."""
    from . import video_io

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for corner, alpha in detection.alpha.items():
        if alpha.max() > 0:
            cv2.imwrite(
                str(out_dir / f"alpha_{corner}.png"),
                np.clip(alpha * 255, 0, 255).astype(np.uint8),
            )
    _draw_curves(detection, out_dir / "transizioni.png")

    report = {
        "colore_filigrana_bgr": [round(float(v), 1) for v in detection.color],
        "eventi": [
            {
                "angolo": e.corner,
                "frame_prima": e.start,
                "frame_dopo": e.end,
                "tipo": "comparsa" if e.sign > 0 else "scomparsa",
                "purezza": round(e.purity, 3),
                "intensita": round(e.strength, 1),
            }
            for e in detection.events
        ],
        "intervalli": [
            {
                "angolo": corner,
                "frame": [first, last],
                "secondi": [round(first / fps, 2), round(last / fps, 2)],
            }
            for corner, first, last in detection.intervals()
        ],
    }
    (out_dir / "rilevamento.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    height, width = detection.shape
    video_io.write_video(
        (
            overlay_frame(frame, detection, t, fps, threshold, dilation)
            for t, frame in enumerate(frames)
        ),
        out_dir / "maschere.mp4",
        fps=fps,
        size=(width, height),
        crf=20,
    )
    return out_dir
