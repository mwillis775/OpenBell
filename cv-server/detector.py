"""
OpenBell CV Server — YOLOv8 person detector

Loads the YOLOv8n model and provides a simple inference API.
"""

import logging
from typing import List, Tuple

import numpy as np
from ultralytics import YOLO

from config import (
    DETECT_CLASSES,
    DEVICE,
    MIN_ASPECT_RATIO,
    MIN_BOX_AREA_FRACTION,
    MODEL_PATH,
    NMS_IOU_THRESHOLD,
    PERSON_CONF_THRESHOLD,
)

log = logging.getLogger("openbell.cv.detector")


class Detection:
    """A single person detection."""

    __slots__ = ("x1", "y1", "x2", "y2", "confidence", "class_id")

    def __init__(self, x1: float, y1: float, x2: float, y2: float, confidence: float, class_id: int):
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.confidence = confidence
        self.class_id = class_id

    @property
    def area(self) -> float:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    def to_dict(self) -> dict:
        return {
            "x1": round(self.x1, 1),
            "y1": round(self.y1, 1),
            "x2": round(self.x2, 1),
            "y2": round(self.y2, 1),
            "confidence": round(self.confidence, 3),
            "class_id": self.class_id,
        }

    def __repr__(self) -> str:
        return (
            f"Detection(person conf={self.confidence:.2f} "
            f"box=[{self.x1:.0f},{self.y1:.0f},{self.x2:.0f},{self.y2:.0f}])"
        )


class PersonDetector:
    """YOLOv8 person detector wrapper."""

    def __init__(self):
        log.info("Loading YOLO model from %s (device=%s)", MODEL_PATH, DEVICE)
        self.model = YOLO(MODEL_PATH)
        self.model.to(DEVICE)
        log.info("Model loaded on %s", DEVICE)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        Run inference on a BGR frame, return person detections.

        Args:
            frame: OpenCV BGR image (H, W, 3) uint8

        Returns:
            List of Detection objects for persons above confidence threshold.
        """
        results = self.model.predict(
            frame,
            conf=PERSON_CONF_THRESHOLD,
            iou=NMS_IOU_THRESHOLD,
            classes=DETECT_CLASSES,
            device=DEVICE,
            verbose=False,
        )

        detections: List[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())
                cls = int(box.cls[0].cpu().numpy())
                detections.append(Detection(
                    x1=float(xyxy[0]),
                    y1=float(xyxy[1]),
                    x2=float(xyxy[2]),
                    y2=float(xyxy[3]),
                    confidence=conf,
                    class_id=cls,
                ))

        # Post-filter: reject tiny boxes and wrong aspect ratios
        if detections:
            h, w = frame.shape[:2]
            frame_area = float(h * w)
            filtered = []
            for d in detections:
                box_w = d.x2 - d.x1
                box_h = d.y2 - d.y1
                if d.area < frame_area * MIN_BOX_AREA_FRACTION:
                    continue
                if box_h < 1 or (box_h / max(box_w, 1)) < MIN_ASPECT_RATIO:
                    continue
                filtered.append(d)
            detections = filtered

        return detections
