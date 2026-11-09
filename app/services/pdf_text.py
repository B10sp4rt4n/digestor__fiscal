"""Bounded local PDF extraction. Tesseract never contacts an external service."""
import csv
from dataclasses import dataclass, field
import io
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata

import cv2
import fitz
import numpy as np

from app.core.config import settings

MAX_PIXELS = 10_000_000
MAX_SIDE = 4200


def raster_scale(rect, dpi=250):
    return min(dpi / 72, MAX_SIDE / max(rect.width, rect.height), math.sqrt(MAX_PIXELS / (rect.width * rect.height)))


def compact(value):
    text = unicodedata.normalize("NFD", str(value or "")).upper()
    return "".join(c for c in text if c.isalnum() and unicodedata.category(c) != "Mn")


@dataclass
class PDFText:
    text: str
    page_count: int
    native_parts: list[str] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    words: list[tuple[str, float]] = field(default_factory=list)

    @property
    def method(self):
        if not self.pages:
            return "native"
        return "mixed" if any(p.strip() for p in self.native_parts) else "ocr"

    def metadata(self):
        scores = [score for _, score in self.words]
        return {"method": self.method, "engine": "tesseract", "language": settings.CSF_OCR_LANGUAGE,
                "pageCount": self.page_count, "ocrPages": self.pages,
                "meanConfidence": round(sum(scores) / len(scores), 1) if scores else None,
                "confidenceScale": "tesseract_0_100_not_probability", "reviewRequired": bool(self.pages)}


def _run_tesseract(png, arguments, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("OCR_TIMEOUT: La lectura tardó demasiado; usa un escaneo más claro o menos páginas.")
    executable = shutil.which("tesseract")
    if not executable:
        raise ValueError("OCR_UNAVAILABLE: El lector de escaneos no está disponible.")
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1"}
    if os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    try:
        result = subprocess.run([executable, "stdin", "stdout", *arguments], input=png,
                                capture_output=True, timeout=min(12, remaining), env=env, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("OCR_TIMEOUT: La lectura tardó demasiado; usa un escaneo más claro o menos páginas.") from None
    except OSError:
        raise ValueError("OCR_UNAVAILABLE: El lector de escaneos no está disponible.") from None
    if b"Error opening data file" in result.stderr or b"Failed loading language" in result.stderr:
        raise ValueError("OCR_UNAVAILABLE: Faltan los idiomas del lector de escaneos.")
    return result


def _png(image):
    ok, data = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("OCR_UNREADABLE: No se pudo preparar la imagen del escaneo.")
    return data.tobytes()


def _tsv(result):
    if result.returncode:
        raise ValueError("OCR_UNREADABLE: No se pudo leer el escaneo. Usa una copia más clara.")
    words = []
    lines = {}
    reader = csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if row.get("level") != "5":
            continue
        text = str(row.get("text") or "").strip()
        try:
            score = float(row.get("conf", "-1"))
        except (ValueError, TypeError):
            continue
        if not text or not math.isfinite(score) or not 0 <= score <= 100:
            continue
        key = tuple(row.get(k) for k in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(text)
        words.append((text, score))
    return "\n".join(" ".join(line) for line in lines.values()), words


def _ocr_page(page, deadline):
    rect = page.rect
    scale = raster_scale(rect)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    if np.count_nonzero(image < 225) < 100:
        return "", [], 0
    arguments = ["-l", settings.CSF_OCR_LANGUAGE, "--oem", "1", "--psm", "3", "tsv"]
    text, words = _tsv(_run_tesseract(_png(image), arguments, deadline))
    if any(marker in compact(text) for marker in ("CONSTANCIADESITUACIONFISCAL", "DATOSDEIDENTIFICACION", "REGIMEN", "OBLIGACIONES")):
        return text, words, 0
    # Only suspect orientations need OSD; its smaller preview avoids unnecessary work.
    factor = min(1, 1800 / max(image.shape))
    preview = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA) if factor < 1 else image
    result = _run_tesseract(_png(preview), ["-l", "osd", "--psm", "0"], deadline)
    match = re.search(rb"Rotate:\s*(0|90|180|270)\b", result.stdout)
    rotation = int(match.group(1)) if match else 0
    if rotation:
        image = np.rot90(image, -(rotation // 90)).copy()
        text, words = _tsv(_run_tesseract(_png(image), arguments, deadline))
    return text, words, rotation


def extract_pdf_text(content):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("PDF_LIMIT: El PDF supera el tamaño admitido para lectura.")
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception:
        raise ValueError("PDF_INVALID: El archivo PDF está dañado o no se puede abrir.") from None
    with document:
        if document.needs_pass:
            raise ValueError("PDF_ENCRYPTED: Guarda una copia del PDF sin contraseña.")
        if len(document) > settings.CSF_PDF_MAX_PAGES:
            raise ValueError("PDF_LIMIT: El documento tiene demasiadas páginas. Separa la constancia.")
        output = PDFText("", len(document))
        parts = []
        deadline = time.monotonic() + settings.CSF_OCR_TIMEOUT_SECONDS
        for index, page in enumerate(document):
            native = page.get_text("text")
            image_area = max((fitz.Rect(info["bbox"]).get_area() for info in page.get_image_info()), default=0)
            image_scan = image_area >= page.rect.get_area() * 0.4
            # A small text overlay on a full-page scan must not suppress OCR.
            needs_ocr = image_scan and len(compact(native)) < 600
            if not native.strip() and not image_scan:
                # Vector-only scans can also have no extractable text.
                needs_ocr = bool(page.get_drawings())
            if not needs_ocr:
                parts.append(native)
                output.native_parts.append(native)
                continue
            if not settings.CSF_OCR_ENABLED:
                raise ValueError("OCR_UNAVAILABLE: El lector de escaneos no está habilitado.")
            if len(output.pages) >= settings.CSF_OCR_MAX_PAGES:
                raise ValueError("PDF_LIMIT: El escaneo tiene demasiadas páginas. Separa la constancia.")
            text, words, rotation = _ocr_page(page, deadline)
            parts.append(text)
            output.words.extend(words)
            output.pages.append({"page": index + 1, "rotation": rotation})
        output.text = "\n\n".join(parts)
        if not output.text.strip():
            raise ValueError("OCR_UNREADABLE: No se encontró texto legible. Usa una copia más clara.")
        return output


def annotate_validation(report, extraction):
    if not extraction.pages:
        return report
    native = compact(" ".join(extraction.native_parts))
    normalized_words = [(compact(word), score) for word, score in extraction.words if compact(word)]
    combined = "".join(word for word, _ in normalized_words)
    for item in report:
        value = compact(item.get("value"))
        if value and value in native:
            item["extraction_method"] = "native"
            continue
        item["extraction_method"] = "ocr"
        start = combined.find(value) if value else -1
        scores = []
        if start >= 0:
            offset = 0
            for word, score in normalized_words:
                if offset < start + len(value) and offset + len(word) > start:
                    scores.append(score)
                offset += len(word)
        confidence = round(min(scores), 1) if scores else None
        item["ocr_confidence"] = confidence
        item["ocr_needs_review"] = confidence is None or confidence < settings.CSF_OCR_MIN_CONFIDENCE
        if item["ocr_needs_review"] and item.get("is_ok"):
            item.update(status="no", is_ok=False, reason="ocr_low_confidence")
    report.append({"field": "__document__", "label": "Lectura del documento", "kind": "extraction_metadata",
                   "status": "no", "is_ok": False, "reason": "ocr_requires_human_review",
                   "extraction": extraction.metadata()})
    return report
