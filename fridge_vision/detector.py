"""문 닫힘(장면 안정) 감지기.

ESP32 없이 카메라 영상만으로 '관람객이 식재료를 바꾸고 문을 닫았다'를 판단합니다.

  IDLE     : 기준 화면·기준 밝기를 기억하고 대기
  ACTIVE   : 움직임(손) 또는 밝기 변화(문 열림)가 생김
  SETTLING : 움직임이 멈추고 밝기가 기준으로 돌아옴 → SETTLE_SECONDS 동안 유지되면 촬영
  COOLDOWN : 촬영 직후 중복 촬영 방지
"""
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

import config


class State(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    SETTLING = "SETTLING"
    COOLDOWN = "COOLDOWN"


@dataclass
class Metrics:
    motion: float = 0.0          # 직전 프레임 대비 변한 픽셀 비율
    brightness: float = 0.0      # 평균 밝기 (0~255)
    base_brightness: float = 0.0 # 기준 밝기
    scene_change: float = 0.0    # 기준 화면 대비 변한 픽셀 비율


@dataclass
class Event:
    kind: str                    # "capture" | "no_change"
    frame: np.ndarray            # 원본 해상도 프레임
    metrics: Metrics


def _prep(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = config.ANALYSIS_WIDTH / w
    small = cv2.resize(frame, (config.ANALYSIS_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def _changed_ratio(a: np.ndarray, b: np.ndarray) -> float:
    diff = cv2.absdiff(a, b)
    return float(np.count_nonzero(diff > config.PIXEL_DIFF_THRESHOLD)) / diff.size


class DoorEventDetector:
    def __init__(self) -> None:
        self.state = State.IDLE
        self.metrics = Metrics()
        self._prev = None
        self._base = None
        self._base_frame = None
        self._stable_since = None
        self._cooldown_until = 0.0

    # 외부에서 기준 화면을 지정 (시작 시 before.jpg, 확정 후 after.jpg)
    def set_baseline(self, frame: np.ndarray) -> None:
        g = _prep(frame)
        self._base = g
        self._base_frame = frame.copy()
        self.metrics.base_brightness = float(g.mean())

    @property
    def baseline_frame(self):
        return self._base_frame

    def update(self, frame: np.ndarray, t: float):
        """프레임 1장을 처리하고, 촬영 시점이면 Event 를 돌려줍니다. t 는 초 단위 시각."""
        g = _prep(frame)
        if self._base is None:
            self.set_baseline(frame)
        if self._prev is None:
            self._prev = g

        m = self.metrics
        m.motion = _changed_ratio(g, self._prev)
        m.brightness = float(g.mean())
        m.scene_change = _changed_ratio(g, self._base)
        self._prev = g

        bright_ok = abs(m.brightness - m.base_brightness) < config.BRIGHTNESS_DELTA
        still = m.motion < config.MOTION_STILL_RATIO

        if self.state == State.COOLDOWN:
            if t >= self._cooldown_until:
                self.state = State.IDLE
            return None

        if self.state == State.IDLE:
            if m.motion > config.MOTION_START_RATIO or not bright_ok:
                self.state = State.ACTIVE
            elif still:
                # 전시장 조명이 천천히 바뀌는 것만 따라감
                r = config.BASELINE_ADAPT_RATE
                m.base_brightness = (1 - r) * m.base_brightness + r * m.brightness
            return None

        if self.state == State.ACTIVE:
            if still and bright_ok:
                self.state = State.SETTLING
                self._stable_since = t
            return None

        if self.state == State.SETTLING:
            if not (still and bright_ok):
                self.state = State.ACTIVE
                return None
            if t - self._stable_since >= config.SETTLE_SECONDS:
                self.state = State.COOLDOWN
                self._cooldown_until = t + config.COOLDOWN_SECONDS
                kind = "no_change" if m.scene_change < config.NO_CHANGE_RATIO else "capture"
                return Event(kind=kind, frame=frame.copy(), metrics=Metrics(**vars(m)))
        return None

    def force_capture(self, frame: np.ndarray, t: float) -> Event:
        """iPad '다 넣었어요' 버튼 / 키보드 c 키에 해당하는 수동 촬영."""
        self.state = State.COOLDOWN
        self._cooldown_until = t + config.COOLDOWN_SECONDS
        return Event(kind="capture", frame=frame.copy(), metrics=Metrics(**vars(self.metrics)))
