from __future__ import annotations

import cv2
import numpy as np

from .models import MotionRegion


class MotionDetector:
    def __init__(self, min_area: int = 5000, merge_pixels: int = 36):
        self.min_area = min_area
        self.merge_pixels = merge_pixels
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=400,
            varThreshold=40,
            detectShadows=True,
        )

    def detect(self, frame: np.ndarray) -> tuple[list[MotionRegion], np.ndarray]:
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        mask = self._subtractor.apply(blurred)
        _, threshold = cv2.threshold(mask, 220, 255, cv2.THRESH_BINARY)
        threshold = cv2.erode(threshold, None, iterations=1)
        threshold = cv2.dilate(threshold, None, iterations=3)
        contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        regions: list[MotionRegion] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            regions.append(MotionRegion(bbox=(x, y, w, h), area=area))
        regions = _merge_regions(regions, self.merge_pixels)
        regions.sort(key=lambda region: region.area, reverse=True)
        return [region for region in regions if region.area >= self.min_area], threshold


def _merge_regions(regions: list[MotionRegion], padding: int) -> list[MotionRegion]:
    if not regions:
        return []

    merged: list[MotionRegion] = []
    for region in regions:
        candidate = region
        changed = True
        while changed:
            changed = False
            next_regions: list[MotionRegion] = []
            for existing in merged:
                if _boxes_near(candidate.bbox, existing.bbox, padding):
                    candidate = _union(candidate, existing)
                    changed = True
                else:
                    next_regions.append(existing)
            merged = next_regions
        merged.append(candidate)
    return merged


def _boxes_near(a: tuple[int, int, int, int], b: tuple[int, int, int, int], padding: int) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + padding < bx
        or bx + bw + padding < ax
        or ay + ah + padding < by
        or by + bh + padding < ay
    )


def _union(a: MotionRegion, b: MotionRegion) -> MotionRegion:
    ax, ay, aw, ah = a.bbox
    bx, by, bw, bh = b.bbox
    x1 = min(ax, bx)
    y1 = min(ay, by)
    x2 = max(ax + aw, bx + bw)
    y2 = max(ay + ah, by + bh)
    return MotionRegion(bbox=(x1, y1, x2 - x1, y2 - y1), area=float((x2 - x1) * (y2 - y1)))
