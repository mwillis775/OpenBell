"""
OpenBell CV Server - Face recognizer

Identifies known people using face embeddings.

Two modes of building the reference database:

1. Pre-loaded: Place clean, unannotated face photos in the faces/
   directory. Filename convention: "michael.jpg", "keith2.jpg", etc.

2. Auto-learn: When a person is detected and the system can find a
   face in the raw (unannotated) frame, it saves a face crop to faces/
   and learns it for next time. Requires a human to name the files
   (rename "unknown_20260401_1234.jpg" -> "michael4.jpg").

Files matching "person_*", "gnome*", "fire hydrant*" are skipped.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("openbell.cv.recognizer")

# Lazy import - face_recognition pulls in dlib which is heavy.
# The library also calls sys.exit() if face_recognition_models is
# missing, so we must catch SystemExit as well as ImportError.
_fr = None
_import_failed = False


def _load_face_recognition():
    global _fr, _import_failed
    if _import_failed:
        return None
    if _fr is not None:
        return _fr
    try:
        import face_recognition
        _fr = face_recognition
    except (ImportError, SystemExit, Exception) as e:
        _import_failed = True
        log.warning("face_recognition not available: %s - person identification disabled", e)
        return None
    return _fr


# Filename prefixes to skip (false positives and auto-captured)
_SKIP_PREFIXES = ("person_", "gnome", "gmone", "fire hydrant", "fire_hydrant",
                  "unknown_")


def _parse_name(filename: str) -> Optional[str]:
    """Extract the person name from a filename like 'keith5.jpg' -> 'Keith'."""
    stem = Path(filename).stem.lower()

    for prefix in _SKIP_PREFIXES:
        if stem.startswith(prefix):
            return None

    # Strip trailing digits: "michael7" -> "michael"
    name = re.sub(r'\d+$', '', stem).strip().rstrip('-').rstrip('_').strip()

    if not name:
        return None

    return name.title().replace('_', ' ').replace('-', ' ')


class FaceRecognizer:
    """
    Identifies known people by comparing face embeddings against
    reference images from the faces/ directory.
    """

    def __init__(self, reference_dir: str, tolerance: float = 0.55):
        self.tolerance = tolerance
        self._known_names: List[str] = []
        self._known_encodings: List[np.ndarray] = []
        self._loaded = False
        self._reference_dir = reference_dir

    def load(self):
        """Load and encode all labeled reference images."""
        fr = _load_face_recognition()
        if fr is None:
            return

        ref_dir = Path(self._reference_dir)
        if not ref_dir.exists():
            os.makedirs(ref_dir, exist_ok=True)
            log.info("Created face reference directory: %s", ref_dir)

        # Group files by person name
        name_files: Dict[str, List[Path]] = {}
        for f in sorted(ref_dir.iterdir()):
            if f.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                continue
            name = _parse_name(f.name)
            if name is None:
                continue
            name_files.setdefault(name, []).append(f)

        if not name_files:
            log.info(
                "No labeled reference images found in %s - "
                "auto-learn mode active. Unrecognised faces will be saved "
                "as unknown_*.jpg for you to rename.",
                ref_dir,
            )
            self._loaded = True  # still mark loaded so auto-learn works
            return

        log.info(
            "Loading face references for %d people: %s",
            len(name_files), list(name_files.keys()),
        )

        for name, files in name_files.items():
            encodings_for_person = 0
            for fpath in files:
                try:
                    image = fr.load_image_file(str(fpath))
                    # Try HOG first (fast), then upsample if nothing found
                    face_locs = fr.face_locations(image, model="hog")
                    if not face_locs:
                        face_locs = fr.face_locations(
                            image, number_of_times_to_upsample=2, model="hog",
                        )
                    if not face_locs:
                        log.warning("No face found in %s - skipping", fpath.name)
                        continue
                    encodings = fr.face_encodings(image, face_locs)
                    for enc in encodings:
                        self._known_names.append(name)
                        self._known_encodings.append(enc)
                        encodings_for_person += 1
                except Exception as e:
                    log.warning("Failed to process %s: %s", fpath.name, e)

            log.info(
                "  %s: %d face encoding(s) from %d image(s)",
                name, encodings_for_person, len(files),
            )

        self._loaded = True
        unique_names = sorted(set(self._known_names))
        log.info(
            "Face recognizer ready: %d encodings for %d people (%s)",
            len(self._known_encodings), len(unique_names),
            ", ".join(unique_names) if unique_names else "none yet",
        )

    def identify(self, frame: np.ndarray) -> List[Tuple[str, float]]:
        """
        Try to identify faces in a raw (unannotated) frame.

        Args:
            frame: BGR image (OpenCV format) - MUST be raw, not annotated

        Returns:
            List of (name, distance) for each face found.
            Unrecognised faces return ("unknown", distance).
        """
        if not self._loaded:
            return []

        fr = _load_face_recognition()
        if fr is None:
            return []

        # Convert BGR -> RGB for face_recognition
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Detect faces - upsample 2x for doorbell camera distance
        face_locations = fr.face_locations(rgb, number_of_times_to_upsample=2, model="hog")
        if not face_locations:
            return []

        face_encodings = fr.face_encodings(rgb, face_locations)

        results = []
        for i, encoding in enumerate(face_encodings):
            if not self._known_encodings:
                # No known people yet - auto-save for later labeling
                self._save_unknown_face(rgb, face_locations[i])
                results.append(("unknown", 1.0))
                continue

            distances = fr.face_distance(self._known_encodings, encoding)

            best_idx = int(np.argmin(distances))
            best_distance = float(distances[best_idx])

            if best_distance <= self.tolerance:
                results.append((self._known_names[best_idx], best_distance))
            else:
                # Unrecognised - save for manual labeling
                self._save_unknown_face(rgb, face_locations[i])
                results.append(("unknown", best_distance))

        identified = [r for r in results if r[0] != "unknown"]
        if identified:
            log.info(
                "Recognised: %s",
                ", ".join(f"{n} (dist={d:.2f})" for n, d in identified),
            )

        return results

    def _save_unknown_face(self, rgb: np.ndarray, face_location: tuple):
        """Save an unknown face crop for manual labeling later."""
        try:
            top, right, bottom, left = face_location
            h, w = rgb.shape[:2]

            # Generous padding around face
            pad_h = int((bottom - top) * 0.5)
            pad_w = int((right - left) * 0.5)
            crop = rgb[
                max(0, top - pad_h):min(h, bottom + pad_h),
                max(0, left - pad_w):min(w, right + pad_w),
            ]
            crop_bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)

            ts = time.strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(self._reference_dir, f"unknown_{ts}.jpg")
            # Avoid overwriting if multiple faces in same second
            if os.path.exists(out_path):
                out_path = os.path.join(
                    self._reference_dir,
                    f"unknown_{ts}_{int(time.time()*1000) % 1000}.jpg",
                )
            cv2.imwrite(out_path, crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            log.info("Saved unknown face crop: %s - rename to identify", out_path)
        except Exception as e:
            log.debug("Failed to save unknown face: %s", e)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def known_names(self) -> List[str]:
        return sorted(set(self._known_names))
