#!/usr/bin/env python3
"""
OpenBell — Snapshot Sorter & Face Trainer

A high-quality standalone tool that re-analyzes ALL existing snapshots using
a large YOLO model + face recognition to:

1. Identify what's actually in each photo (person, gnome, fire hydrant, etc.)
2. Recognize known faces and auto-rename photos accordingly
3. Sort photos into organized subdirectories
4. Learn new face encodings from already-renamed snapshots
5. Prune duplicates / near-identical frames
6. Update the faces/ reference directory with clean face crops

Usage:
    cd cv-server
    venv/bin/python sort_snapshots.py [--dry-run] [--model yolov8x.pt]

It uses the best model available for accuracy (downloads yolov8x if needed),
NOT the lightweight model the live pipeline uses.
"""

import argparse
import hashlib
import logging
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sorter")

# ── Paths ──
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "snapshots"
FACES_DIR = ROOT / "faces"
SORTED_DIR = SNAPSHOT_DIR / "sorted"

# Subdirectories for sorted output
CATEGORIES = {
    "people": "People (identified or unidentified)",
    "gnome": "Garden gnome false positives",
    "fire_hydrant": "Fire hydrant false positives",
    "other_objects": "Other non-person detections",
    "empty": "No detections at all",
    "duplicates": "Near-duplicate frames",
}

# COCO class names we care about
COCO_PERSON = 0
COCO_FIRE_HYDRANT = 10
# Objects commonly confused with people
FALSE_POSITIVE_CLASSES = {
    10: "fire_hydrant",
    13: "bench",
    56: "chair",
    58: "potted_plant",
    73: "book",      # sometimes gnome detections
    75: "vase",      # sometimes gnome-shaped vases
}

# face_recognition lazy import
_fr = None

def _get_fr():
    global _fr
    if _fr is None:
        try:
            import face_recognition
            _fr = face_recognition
        except (ImportError, SystemExit):
            log.warning("face_recognition not available — face ID disabled")
    return _fr


def perceptual_hash(img: np.ndarray, hash_size: int = 16) -> str:
    """Compute a perceptual hash for duplicate detection."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size))
    diff = resized[:, 1:] > resized[:, :-1]
    return hashlib.md5(diff.tobytes()).hexdigest()


def compute_image_hash(path: Path) -> str:
    """Fast file-content hash for exact-duplicate detection."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class SnapshotSorter:
    def __init__(self, model_name: str = "yolov8x.pt", device: str = "cuda",
                 confidence: float = 0.30, dry_run: bool = False):
        self.dry_run = dry_run
        self.device = device
        self.confidence = confidence
        self.model_name = model_name
        self.model: Optional[YOLO] = None
        self.known_names: List[str] = []
        self.known_encodings: List[np.ndarray] = []
        self.face_tolerance = 0.50  # tighter than live pipeline
        self.stats = defaultdict(int)
        self.new_face_crops: Dict[str, List[Tuple[Path, np.ndarray]]] = defaultdict(list)

    def load_model(self):
        """Load the best available YOLO model."""
        model_path = ROOT / self.model_name
        if not model_path.exists():
            log.info("Downloading %s for best accuracy...", self.model_name)
        self.model = YOLO(str(model_path))
        self.model.to(self.device)
        log.info("Model loaded: %s on %s", self.model_name, self.device)

    def load_face_references(self):
        """Load face encodings from the faces/ directory."""
        fr = _get_fr()
        if fr is None:
            return

        SKIP_PREFIXES = ("person_", "gnome", "gmone", "fire hydrant",
                         "fire_hydrant", "unknown_")

        if not FACES_DIR.exists():
            FACES_DIR.mkdir(parents=True, exist_ok=True)

        for f in sorted(FACES_DIR.iterdir()):
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            stem = f.stem.lower()
            if any(stem.startswith(p) for p in SKIP_PREFIXES):
                continue

            name = re.sub(r'\d+$', '', stem).strip().rstrip('-_').strip()
            if not name:
                continue
            name = name.title().replace('_', ' ').replace('-', ' ')

            try:
                image = fr.load_image_file(str(f))
                locs = fr.face_locations(image, model="hog")
                if not locs:
                    locs = fr.face_locations(image, number_of_times_to_upsample=2, model="hog")
                if not locs:
                    continue
                encs = fr.face_encodings(image, locs)
                for enc in encs:
                    self.known_names.append(name)
                    self.known_encodings.append(enc)
            except Exception as e:
                log.warning("  Skip %s: %s", f.name, e)

        log.info("Loaded %d face encodings for %d people",
                 len(self.known_encodings),
                 len(set(self.known_names)))

    def _also_load_from_renamed_snapshots(self):
        """
        Learn faces from snapshots that the user already renamed
        (e.g., 'keith5.jpg', 'michael2.jpg').
        This is the key feature — leverage existing manual labeling work.
        """
        fr = _get_fr()
        if fr is None:
            return

        SKIP_PREFIXES = ("person_", "gnome", "gmone", "fire hydrant",
                         "fire_hydrant", "unknown_", "delivery",
                         "sales", "poolguy", "other")

        count = 0
        for f in sorted(SNAPSHOT_DIR.iterdir()):
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            if f.is_dir():
                continue
            stem = f.stem.lower()
            if any(stem.startswith(p) for p in SKIP_PREFIXES):
                continue

            name = re.sub(r'\d+$', '', stem).strip().rstrip('-_').strip()
            if not name or name == "pam and keith":
                # Can't learn multi-person images easily
                if name == "pam and keith":
                    # But let's try  — face_recognition can find multiple
                    pass
                elif not name:
                    continue

            name_title = name.title().replace('_', ' ').replace('-', ' ')

            try:
                image = fr.load_image_file(str(f))
                # Snapshot images have green bounding boxes drawn on them.
                # Try to find faces anyway — face_recognition is robust to overlays.
                locs = fr.face_locations(image, number_of_times_to_upsample=2, model="hog")
                if not locs:
                    continue
                encs = fr.face_encodings(image, locs)
                for enc in encs:
                    self.known_names.append(name_title)
                    self.known_encodings.append(enc)
                    count += 1
            except Exception:
                pass

        if count:
            log.info("Learned %d additional face encodings from renamed snapshots", count)

    def identify_face(self, image_bgr: np.ndarray) -> List[Tuple[str, float]]:
        """Identify faces in a BGR image. Returns (name, distance) pairs."""
        fr = _get_fr()
        if fr is None or not self.known_encodings:
            return []

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        locs = fr.face_locations(rgb, number_of_times_to_upsample=2, model="hog")
        if not locs:
            return []

        encs = fr.face_encodings(rgb, locs)
        results = []
        for i, enc in enumerate(encs):
            distances = fr.face_distance(self.known_encodings, enc)
            best_idx = int(np.argmin(distances))
            best_dist = float(distances[best_idx])
            if best_dist <= self.face_tolerance:
                results.append((self.known_names[best_idx], best_dist))
            else:
                results.append(("unknown", best_dist))

            # Save face crop for potential reference addition
            top, right, bottom, left = locs[i]
            h, w = rgb.shape[:2]
            pad = 30
            crop = image_bgr[
                max(0, top - pad):min(h, bottom + pad),
                max(0, left - pad):min(w, right + pad)
            ]
            if crop.size > 0:
                name = self.known_names[best_idx] if best_dist <= self.face_tolerance else "unknown"
                self.new_face_crops[name].append((Path("crop"), crop))

        return results

    def analyze_image(self, path: Path) -> dict:
        """Full analysis of a single image: YOLO detection + face recognition."""
        img = cv2.imread(str(path))
        if img is None:
            return {"category": "empty", "detections": [], "faces": [], "error": "unreadable"}

        # Run YOLO with ALL classes (not just person)
        results = self.model.predict(
            img,
            conf=self.confidence,
            iou=0.5,
            device=self.device,
            verbose=False,
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls = int(box.cls[0].cpu().numpy())
                conf = float(box.conf[0].cpu().numpy())
                xyxy = box.xyxy[0].cpu().numpy()
                name = self.model.names.get(cls, f"class_{cls}")
                detections.append({
                    "class_id": cls,
                    "class_name": name,
                    "confidence": conf,
                    "box": xyxy.tolist(),
                })

        # Determine category
        has_person = any(d["class_id"] == COCO_PERSON and d["confidence"] > 0.40 for d in detections)
        has_hydrant = any(d["class_id"] == COCO_FIRE_HYDRANT for d in detections)
        has_other_fp = any(d["class_id"] in FALSE_POSITIVE_CLASSES for d in detections)

        # Face recognition
        faces = self.identify_face(img) if has_person else []
        known_faces = [f for f in faces if f[0] != "unknown"]

        # Categorize
        if has_person and known_faces:
            category = "people"
            identity = known_faces[0][0].lower().replace(' ', '_')
        elif has_person:
            category = "people"
            identity = None
        elif has_hydrant and not has_person:
            category = "fire_hydrant"
            identity = None
        elif has_other_fp and not has_person:
            category = "other_objects"
            identity = None
        elif not detections:
            category = "empty"
            identity = None
        else:
            # Has detections but no person — check what YOLO thinks
            top_det = max(detections, key=lambda d: d["confidence"])
            if top_det["class_name"] in ("potted plant", "vase", "book", "bench"):
                category = "gnome"  # likely gnome mislabeled
            else:
                category = "other_objects"
            identity = None

        return {
            "category": category,
            "identity": identity,
            "detections": detections,
            "faces": faces,
            "known_faces": known_faces,
        }

    def sort_all(self):
        """Sort all snapshots into organized directories."""
        if not SNAPSHOT_DIR.exists():
            log.error("Snapshots directory not found: %s", SNAPSHOT_DIR)
            return

        # Create output dirs
        if not self.dry_run:
            for cat in CATEGORIES:
                (SORTED_DIR / cat).mkdir(parents=True, exist_ok=True)

        files = sorted([
            f for f in SNAPSHOT_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
        ])
        log.info("Found %d images to process", len(files))

        # Phase 1: Detect exact duplicates
        log.info("Phase 1: Checking for duplicates...")
        hashes = {}
        duplicates = set()
        for f in files:
            h = compute_image_hash(f)
            if h in hashes:
                duplicates.add(f)
                log.debug("  Duplicate: %s == %s", f.name, hashes[h].name)
            else:
                hashes[h] = f

        # Phase 2: Detect perceptual duplicates (near-identical frames)
        phashes = {}
        for f in files:
            if f in duplicates:
                continue
            img = cv2.imread(str(f))
            if img is None:
                continue
            ph = perceptual_hash(img)
            if ph in phashes:
                # Keep the one with the better filename (human-renamed > auto)
                existing = phashes[ph]
                if f.name.startswith("person_") and not existing.name.startswith("person_"):
                    duplicates.add(f)
                elif not f.name.startswith("person_") and existing.name.startswith("person_"):
                    duplicates.add(existing)
                    phashes[ph] = f
                else:
                    duplicates.add(f)
            else:
                phashes[ph] = f

        log.info("  Found %d duplicates/near-duplicates", len(duplicates))

        # Phase 3: Analyze and sort
        log.info("Phase 2: Analyzing with %s...", self.model_name)
        for i, f in enumerate(files):
            if f in duplicates:
                self._move(f, "duplicates", f.name)
                self.stats["duplicates"] += 1
                continue

            # If already user-renamed (not person_*), trust the name
            if not f.name.startswith("person_"):
                stem = f.stem.lower()
                if "gnome" in stem or "gmone" in stem:
                    self._move(f, "gnome", f.name)
                    self.stats["gnome"] += 1
                elif "fire hydrant" in stem or "fire_hydrant" in stem:
                    self._move(f, "fire_hydrant", f.name)
                    self.stats["fire_hydrant"] += 1
                else:
                    # Likely a real person already named
                    self._move(f, "people", f.name)
                    self.stats["people_named"] += 1
                continue

            # Auto-captured: run full analysis
            analysis = self.analyze_image(f)
            cat = analysis["category"]

            # Generate a good filename
            if cat == "people" and analysis.get("identity"):
                name = analysis["identity"]
                existing = list((SORTED_DIR / "people").glob(f"{name}*.jpg")) if not self.dry_run else []
                idx = len(existing) + 1
                new_name = f"{name}{idx}.jpg"
            elif cat == "people":
                # Unknown person — try to at least give it a timestamp name
                new_name = f.name  # keep person_YYYYMMDD_HHMMSS.jpg
            else:
                new_name = f.name

            self._move(f, cat, new_name)
            self.stats[cat] += 1

            if (i + 1) % 25 == 0:
                log.info("  Processed %d/%d images...", i + 1, len(files))

        # Phase 4: Save new face crops to faces/ directory
        self._save_new_face_crops()

        # Summary
        log.info("=" * 50)
        log.info("Sorting complete!")
        for k, v in sorted(self.stats.items()):
            log.info("  %-20s %d", k, v)
        log.info("  %-20s %d", "TOTAL", sum(self.stats.values()))
        log.info("=" * 50)
        if self.dry_run:
            log.info("DRY RUN — no files were moved")

    def _move(self, src: Path, category: str, new_name: str):
        """Move a file to the sorted directory."""
        dst = SORTED_DIR / category / new_name
        if dst.exists():
            # Avoid overwriting — append counter
            stem = dst.stem
            ext = dst.suffix
            counter = 1
            while dst.exists():
                dst = SORTED_DIR / category / f"{stem}_{counter}{ext}"
                counter += 1

        if self.dry_run:
            log.info("  [DRY] %s → sorted/%s/%s", src.name, category, dst.name)
        else:
            shutil.move(str(src), str(dst))

    def _save_new_face_crops(self):
        """Save clean face crops to faces/ for future reference."""
        if self.dry_run:
            return

        fr = _get_fr()
        if fr is None:
            return

        for name, crops in self.new_face_crops.items():
            if name == "unknown":
                continue
            # Only save if we don't already have enough references
            existing_count = len(list(FACES_DIR.glob(f"{name.lower()}*.jpg")))
            if existing_count >= 5:
                continue

            # Save the best crops (largest face)
            crops_sorted = sorted(crops, key=lambda x: x[1].shape[0] * x[1].shape[1], reverse=True)
            for i, (_, crop) in enumerate(crops_sorted[:3]):
                idx = existing_count + i + 1
                out_path = FACES_DIR / f"{name.lower()}{idx}.jpg"
                if not out_path.exists():
                    cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    log.info("  Saved face crop: %s", out_path.name)


def main():
    parser = argparse.ArgumentParser(description="Sort doorbell snapshots using AI")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving files")
    parser.add_argument("--model", default="yolov8x.pt", help="YOLO model to use (default: yolov8x.pt)")
    parser.add_argument("--device", default="cuda", help="Inference device (cuda/cpu)")
    parser.add_argument("--confidence", type=float, default=0.30, help="Detection confidence threshold")
    args = parser.parse_args()

    log.info("OpenBell Snapshot Sorter")
    log.info("  Snapshots: %s", SNAPSHOT_DIR)
    log.info("  Faces:     %s", FACES_DIR)
    log.info("  Output:    %s", SORTED_DIR)
    log.info("  Model:     %s", args.model)
    log.info("  Dry run:   %s", args.dry_run)

    sorter = SnapshotSorter(
        model_name=args.model,
        device=args.device,
        confidence=args.confidence,
        dry_run=args.dry_run,
    )

    sorter.load_model()
    sorter.load_face_references()
    sorter._also_load_from_renamed_snapshots()
    sorter.sort_all()


if __name__ == "__main__":
    main()
