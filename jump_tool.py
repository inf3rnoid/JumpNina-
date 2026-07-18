# ===== Auto install required packages =====
import os
import sys
import subprocess
import importlib

_REQUIRED_PACKAGES = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "mss": "mss",
    "PIL": "Pillow",
    "pynput": "pynput",
}

_installed = False
for _module, _package in _REQUIRED_PACKAGES.items():
    try:
        importlib.import_module(_module)
    except ImportError:
        print(f"[INFO] Installing {_package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", _package])
        _installed = True

if _installed:
    print("[INFO] Restarting application...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ==========================================

"""
CHARACTER + PLATFORM DETECTOR CHECK (Windows)

Mục đích:
- Detect nhân vật từ các PNG trong thư mục templates/.
- Detect platform có thể co giãn toàn bộ chiều ngang.
- Vẽ overlay click-through:
    xanh lá  : nhân vật + điểm A ở giữa cạnh dưới
    cam      : platform + đường mặt trên
    vàng     : điểm giữa mặt trên platform

Platform không dựa vào màu vì dễ nhầm với bánh.
Thay vào đó dùng:
    tạo lát dọc từ màu trung vị từng hàng của platform_template.png
    -> match lát dọc trên toàn màn hình
    -> nối các điểm khớp liên tiếp theo chiều ngang
    -> chỉ giữ dải đủ dài để xem là platform

Phím:
- B   : chọn hai góc vùng tìm platform
- R   : chọn platform gần con trỏ và nhảy
- U/I : dịch điểm B sang trái/phải 10 px
- F   : lấy con trỏ làm điểm mong muốn và ghi sai lệch
- W   : đánh dấu đúng thời điểm nhân vật đáp đất
- T   : auto thường sau 3s / nhấn lại để hủy
- Y   : auto chỉ chọn platform đứng yên
- G   : xuất dữ liệu hiệu chỉnh ra TXT
- F8  : bật/tắt overlay
- F9  : in thông số
- F10 : bật/tắt hiển thị platform
- O/P : giảm/tăng threshold platform
- [ ] : giảm/tăng threshold nhân vật
- ESC : thoát

Cài:
    pip install opencv-python mss numpy pynput PyQt5

Cấu trúc:
    character_platform_detector_check.py
    templates/
        character_idle.png
        character_jump.png
"""



import ctypes
import sys
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import mss
import numpy as np
from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PyQt5.QtWidgets import QApplication, QWidget
from pynput import keyboard, mouse


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PLATFORM_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "platform_template.png"
)

JUMP_DATA_DIR = (
    Path(__file__).resolve().parent / "jump_platform_measurements"
)
JUMP_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Công thức hiệu chỉnh từ dữ liệu platform mới.
# Loại mẫu abs(error)>300, mẫu chạm trần 1.5s và các cú có
# khoảng cách động thay đổi quá 30px trong lúc giữ.
HOLD_TIME_SLOPE = 0.00119458
HOLD_TIME_INTERCEPT = 0.21240875

MIN_HOLD_SECONDS = 0.05
MAX_HOLD_SECONDS = 3.00
JUMP_UPDATE_INTERVAL = 0.005

# Điều chỉnh vị trí B so với tâm platform.
TARGET_OFFSET_STEP_PX = 10
TARGET_SAFE_MARGIN_PX = 12

# Theo dõi chuyển động platform mục tiêu.
PLATFORM_TRACK_MAX_JUMP_PER_FRAME = 100
PLATFORM_HISTORY_SECONDS = 8.0
PLATFORM_MIN_HISTORY_SAMPLES = 12
PLATFORM_PEAK_MIN_SEPARATION_SECONDS = 0.18
PLATFORM_STATIONARY_RANGE_PX = 6.0

# Khi chưa có dữ liệu thời gian bay, hệ thống vẫn căn lúc thả chuột
# vào x_max của platform.
DEFAULT_FLIGHT_TIME_SECONDS = 0.50

# Thời gian chờ tối đa trước khi bắt đầu giữ chuột.
MAX_PRE_JUMP_WAIT_SECONDS = 5.0

# Số mẫu thời gian bay gần nhất dùng để tính trung bình.
MAX_FLIGHT_TIME_SAMPLES = 30

# Theo dõi dao động ngang của nhân vật trước khi nhảy.
PLAYER_HISTORY_SECONDS = 8.0
PLAYER_MIN_HISTORY_SAMPLES = 12
PLAYER_STATIONARY_RANGE_PX = 5.0

# Chỉ dùng dự đoán x_max khi biên độ dao động đủ lớn.
# Chuyển động nhỏ hơn 30 px có thể chỉ do đầu/animation nhân vật.
PLAYER_MIN_MEANINGFUL_RANGE_PX = 30.0
PLAYER_MAX_JUMP_PER_SAMPLE = 100.0

# Sau khi thả chuột:
# - mặc định nhân vật bay khoảng 0.5s;
# - chờ thêm 1.0s để camera ổn định.
CAMERA_SETTLE_AFTER_LANDING_SECONDS = 1.0

# Auto mode.
AUTO_START_DELAY_SECONDS = 3.0
AUTO_OBSERVE_SECONDS = 5.0
AUTO_LOOP_POLL_SECONDS = 0.02

# Bù độ trễ detect/xử lý:
# bắt đầu giữ và thả chuột sớm hơn 0.20 giây so với dự đoán x_max.
PLAYER_XMAX_SYNC_ADVANCE_SECONDS = 0.20

# Auto Y: chỉ chọn platform gần như đứng yên.
STATIONARY_AUTO_START_DELAY_SECONDS = 3.0
STATIONARY_PLATFORM_CHECK_SECONDS = 2.0
STATIONARY_PLATFORM_MAX_X_RANGE_PX = 30.0
STATIONARY_PLATFORM_MAX_ATTEMPTS = 3

TARGET_FPS = 20
FRAME_INTERVAL = 1.0 / TARGET_FPS
MONITOR_INDEX = 1

# Character detector
CHARACTER_TOTAL_THRESHOLD = 0.36
CHARACTER_MIN_EDGE_SCORE = 0.22
EDGE_WEIGHT = 0.68
COLOR_WEIGHT = 0.32
TRACK_MARGIN_X = 180
TRACK_MARGIN_Y = 140
MAX_TRACK_MISSES = 3
FULL_SCAN_SCALE = 0.40
REFINE_MARGIN = 40
ALPHA_THRESHOLD = 20

# Platform detector bằng lát dọc:
# Lấy màu trung vị theo từng hàng của ảnh mẫu để tạo một strip hẹp.
# Strip này không phụ thuộc chiều dài platform. Các vị trí khớp liên tiếp
# theo chiều ngang sẽ được gộp thành một platform.
PLATFORM_MIN_WIDTH = 60
PLATFORM_MAX_RESPONSE_HEIGHT = 10
PLATFORM_SAFE_MARGIN = 18

# Threshold mặc định cho strip matching. O/P thay đổi trực tiếp giá trị này.
PLATFORM_MIN_TOTAL_SCORE = 0.80

# Chiều rộng strip nhân tạo. Dùng profile màu theo chiều dọc lặp lại.
PLATFORM_STRIP_WIDTH = 8

# Cho phép platform trên màn hình cao/thấp hơn template một chút.
PLATFORM_HEIGHT_SCALES = (0.85, 1.00, 1.15)

# Nối các điểm khớp bị đứt nhẹ theo chiều ngang.
PLATFORM_CLOSE_GAP = 25
PLATFORM_OPEN_WIDTH = 9

# Bỏ taskbar hoặc thanh trạng thái sát đáy màn hình.
PLATFORM_BOTTOM_EXCLUSION_PX = 35

RECTANGLE_WIDTH = 3
CENTER_DOT_RADIUS = 4


@dataclass
class CharacterTemplate:
    name: str
    image_bgr: np.ndarray
    mask: Optional[np.ndarray]
    edges: np.ndarray
    coarse_edges: np.ndarray
    width: int
    height: int


@dataclass
class CharacterDetection:
    template_name: str
    total_score: float
    edge_score: float
    color_score: float
    left: int
    top: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def feet(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height


@dataclass
class PlatformDetection:
    left: int
    top: int
    width: int
    height: int
    total_score: float
    image_score: float
    gradient_score: float
    column_coverage: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def top_center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top

    @property
    def safe_left(self) -> int:
        return self.left + min(PLATFORM_SAFE_MARGIN, self.width // 4)

    @property
    def safe_right(self) -> int:
        return self.right - min(PLATFORM_SAFE_MARGIN, self.width // 4)



@dataclass
class PlatformMotionTracker:
    """
    Lưu lịch sử tâm X của một platform và ước lượng:
    - x_min / x_max
    - chu kỳ dao động
    - thời gian từ x_min tới x_max
    - thời điểm x_max kế tiếp
    """
    history: list[tuple[float, float, int, int]] = field(
        default_factory=list
    )
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    period_seconds: Optional[float] = None
    min_to_max_seconds: Optional[float] = None
    last_peak_time: Optional[float] = None
    previous_peaks: list[float] = field(default_factory=list)
    previous_troughs: list[float] = field(default_factory=list)
    last_velocity: float = 0.0

    def clear(self) -> None:
        self.history.clear()
        self.x_min = None
        self.x_max = None
        self.period_seconds = None
        self.min_to_max_seconds = None
        self.last_peak_time = None
        self.previous_peaks.clear()
        self.previous_troughs.clear()
        self.last_velocity = 0.0

    def add(
        self,
        timestamp: float,
        center_x: float,
        width: int,
        top_y: int,
    ) -> None:
        self.history.append(
            (timestamp, center_x, width, top_y)
        )

        cutoff = timestamp - PLATFORM_HISTORY_SECONDS
        self.history = [
            item for item in self.history
            if item[0] >= cutoff
        ]

        if len(self.history) >= 2:
            t1, x1, _, _ = self.history[-2]
            t2, x2, _, _ = self.history[-1]
            dt = max(t2 - t1, 1e-6)
            self.last_velocity = (x2 - x1) / dt

        self._recalculate()

    def _recalculate(self) -> None:
        if len(self.history) < PLATFORM_MIN_HISTORY_SAMPLES:
            return

        xs = np.array(
            [item[1] for item in self.history],
            dtype=np.float64,
        )
        times = np.array(
            [item[0] for item in self.history],
            dtype=np.float64,
        )

        # Percentile giúp giảm ảnh hưởng một frame detect nhảy sai.
        self.x_min = float(np.percentile(xs, 3))
        self.x_max = float(np.percentile(xs, 97))

        if self.x_max - self.x_min <= PLATFORM_STATIONARY_RANGE_PX:
            self.period_seconds = None
            self.min_to_max_seconds = None
            return

        # Làm mượt nhẹ trước khi tìm cực trị.
        window = min(5, len(xs))
        if window >= 3:
            kernel = np.ones(window) / window
            smooth = np.convolve(xs, kernel, mode="same")
        else:
            smooth = xs

        peaks: list[float] = []
        troughs: list[float] = []

        for i in range(2, len(smooth) - 2):
            current = smooth[i]

            if (
                current >= smooth[i - 1]
                and current >= smooth[i + 1]
                and current >= smooth[i - 2]
                and current >= smooth[i + 2]
                and current >= self.x_max - 5
            ):
                if (
                    not peaks
                    or times[i] - peaks[-1]
                    >= PLATFORM_PEAK_MIN_SEPARATION_SECONDS
                ):
                    peaks.append(float(times[i]))

            if (
                current <= smooth[i - 1]
                and current <= smooth[i + 1]
                and current <= smooth[i - 2]
                and current <= smooth[i + 2]
                and current <= self.x_min + 5
            ):
                if (
                    not troughs
                    or times[i] - troughs[-1]
                    >= PLATFORM_PEAK_MIN_SEPARATION_SECONDS
                ):
                    troughs.append(float(times[i]))

        if peaks:
            self.previous_peaks = peaks[-6:]
            self.last_peak_time = peaks[-1]

        if troughs:
            self.previous_troughs = troughs[-6:]

        if len(peaks) >= 2:
            intervals = np.diff(peaks)
            valid = intervals[
                (intervals >= 0.25)
                & (intervals <= 10.0)
            ]
            if valid.size:
                self.period_seconds = float(np.median(valid))

        # Ghép mỗi trough với peak kế tiếp.
        half_cycles: list[float] = []
        for trough in troughs:
            following = [
                peak for peak in peaks
                if peak > trough
            ]
            if following:
                value = following[0] - trough
                if 0.1 <= value <= 5.0:
                    half_cycles.append(value)

        if half_cycles:
            self.min_to_max_seconds = float(
                np.median(half_cycles)
            )
        elif self.period_seconds is not None:
            self.min_to_max_seconds = (
                self.period_seconds / 2.0
            )

    @property
    def motion_mid_x(self) -> Optional[float]:
        if self.x_min is None or self.x_max is None:
            return None
        return (self.x_min + self.x_max) / 2.0

    @property
    def motion_range(self) -> Optional[float]:
        if self.x_min is None or self.x_max is None:
            return None
        return self.x_max - self.x_min

    @property
    def has_meaningful_motion(self) -> bool:
        motion_range = self.motion_range

        return (
            motion_range is not None
            and motion_range >= PLAYER_MIN_MEANINGFUL_RANGE_PX
            and self.period_seconds is not None
            and self.last_peak_time is not None
        )

    def seconds_until_next_xmax(
        self,
        now: float,
    ) -> Optional[float]:
        if (
            self.period_seconds is None
            or self.last_peak_time is None
        ):
            return None

        elapsed = now - self.last_peak_time
        period = self.period_seconds

        if elapsed < 0:
            return -elapsed

        cycles = int(elapsed // period)
        next_peak = (
            self.last_peak_time
            + (cycles + 1) * period
        )

        wait = next_peak - now

        if wait < 0:
            wait += period

        return wait

    def seconds_until_next_middle(
        self,
        now: float,
    ) -> Optional[float]:
        """
        Trả về thời gian đến lần platform đi qua giữa quỹ đạo tiếp theo.

        Với dao động gần tuần hoàn:
        - x_max xảy ra tại phase 0
        - qua giữa theo chiều sang trái tại phase 1/4 chu kỳ
        - x_min tại phase 1/2
        - qua giữa theo chiều sang phải tại phase 3/4 chu kỳ

        Vì vậy platform đi qua giữa hai lần trong mỗi chu kỳ.
        """
        if (
            self.period_seconds is None
            or self.last_peak_time is None
        ):
            return None

        period = self.period_seconds
        half_period = period / 2.0

        if period <= 0:
            return None

        first_middle = self.last_peak_time + period / 4.0

        # Các lần qua giữa cách nhau nửa chu kỳ.
        elapsed = now - first_middle

        if elapsed <= 0:
            return -elapsed

        steps = int(elapsed // half_period) + 1
        next_middle = first_middle + steps * half_period

        return max(0.0, next_middle - now)




@dataclass
class PlayerMotionTracker:
    """
    Theo dõi dao động X của điểm chân nhân vật trước khi nhảy.

    Dùng để ước lượng:
    - player_x_min
    - player_x_max
    - chu kỳ
    - thời điểm x_max kế tiếp
    """
    history: list[tuple[float, float, float]] = field(
        default_factory=list
    )
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    period_seconds: Optional[float] = None
    last_peak_time: Optional[float] = None
    previous_peaks: list[float] = field(default_factory=list)

    def clear(self) -> None:
        self.history.clear()
        self.x_min = None
        self.x_max = None
        self.period_seconds = None
        self.last_peak_time = None
        self.previous_peaks.clear()

    def add(
        self,
        timestamp: float,
        x: float,
        y: float,
    ) -> None:
        if self.history:
            last_x = self.history[-1][1]
            if abs(x - last_x) > PLAYER_MAX_JUMP_PER_SAMPLE:
                return

        self.history.append((timestamp, x, y))

        cutoff = timestamp - PLAYER_HISTORY_SECONDS
        self.history = [
            item for item in self.history
            if item[0] >= cutoff
        ]

        self._recalculate()

    def _recalculate(self) -> None:
        if len(self.history) < PLAYER_MIN_HISTORY_SAMPLES:
            return

        times = np.array(
            [item[0] for item in self.history],
            dtype=np.float64,
        )
        xs = np.array(
            [item[1] for item in self.history],
            dtype=np.float64,
        )

        self.x_min = float(np.percentile(xs, 3))
        self.x_max = float(np.percentile(xs, 97))

        if self.x_max - self.x_min <= PLAYER_STATIONARY_RANGE_PX:
            self.period_seconds = None
            self.last_peak_time = None
            self.previous_peaks.clear()
            return

        window = min(5, len(xs))
        if window >= 3:
            smooth = np.convolve(
                xs,
                np.ones(window) / window,
                mode="same",
            )
        else:
            smooth = xs

        peaks: list[float] = []

        for i in range(2, len(smooth) - 2):
            current = smooth[i]

            if (
                current >= smooth[i - 1]
                and current >= smooth[i + 1]
                and current >= smooth[i - 2]
                and current >= smooth[i + 2]
                and current >= self.x_max - 4
            ):
                if (
                    not peaks
                    or times[i] - peaks[-1]
                    >= PLATFORM_PEAK_MIN_SEPARATION_SECONDS
                ):
                    peaks.append(float(times[i]))

        if peaks:
            self.previous_peaks = peaks[-6:]
            self.last_peak_time = peaks[-1]

        if len(peaks) >= 2:
            intervals = np.diff(peaks)
            valid = intervals[
                (intervals >= 0.20)
                & (intervals <= 10.0)
            ]

            if valid.size:
                self.period_seconds = float(
                    np.median(valid)
                )

    @property
    def motion_range(self) -> Optional[float]:
        """Độ rộng dao động ngang đã quan sát được."""
        if self.x_min is None or self.x_max is None:
            return None

        return self.x_max - self.x_min

    @property
    def has_meaningful_motion(self) -> bool:
        """
        Chỉ bật đồng bộ x_max khi:
        - đã đo đủ x_min/x_max;
        - biên độ ít nhất 30 px;
        - đã xác định được chu kỳ và đỉnh gần nhất.
        """
        motion_range = self.motion_range

        return (
            motion_range is not None
            and motion_range >= PLAYER_MIN_MEANINGFUL_RANGE_PX
            and self.period_seconds is not None
            and self.last_peak_time is not None
        )

    def seconds_until_next_xmax(
        self,
        now: float,
    ) -> Optional[float]:
        if not self.has_meaningful_motion:
            return None

        elapsed = now - self.last_peak_time
        period = self.period_seconds

        if elapsed < 0:
            return -elapsed

        cycles = int(elapsed // period)
        next_peak = (
            self.last_peak_time
            + (cycles + 1) * period
        )

        return max(0.0, next_peak - now)


def imread_unicode(path: Path) -> Optional[np.ndarray]:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except (OSError, ValueError, cv2.error):
        return None


class DetectorOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()

        cv2.setUseOptimized(True)

        self.templates = self.load_character_templates()
        (
            self.platform_template_bgr,
            self.platform_strips,
        ) = self.load_platform_template()
        self.character_threshold = CHARACTER_TOTAL_THRESHOLD
        self.platform_threshold = PLATFORM_MIN_TOTAL_SCORE

        self.mouse_controller = mouse.Controller()

        # Vùng chỉ định để tìm platform, dùng tọa độ màn hình toàn cục.
        self.platform_roi_first_point: Optional[tuple[int, int]] = None
        self.platform_search_rect: Optional[
            tuple[int, int, int, int]
        ] = None

        # Trạng thái chọn platform và tự nhảy.
        self.selected_target_platform: Optional[
            PlatformDetection
        ] = None
        self.jump_in_progress = False
        self.current_jump_distance: Optional[float] = None
        self.current_required_hold: Optional[float] = None
        self.current_elapsed_hold = 0.0

        # Offset ngang của B so với tâm platform.
        # Âm = trái, dương = phải.
        self.target_offset_x = -20

        # Platform gần con trỏ đang được chú ý.
        self.focused_platform: Optional[
            PlatformDetection
        ] = None

        # True sau khi người dùng nhấn E để khóa platform mục tiêu.
        self.focused_platform_locked = False

        self.motion_tracker = PlatformMotionTracker()
        self.focused_platform_last_seen: Optional[float] = None

        # Theo dõi dao động của nhân vật khi còn đứng trên platform.
        self.player_motion_tracker = PlayerMotionTracker()

        # Dữ liệu thời gian bay, W đánh dấu lúc đáp.
        self.flight_time_samples: list[float] = []
        self.last_mouse_release_time: Optional[float] = None
        self.last_jump_press_time: Optional[float] = None
        self.last_landing_mark_time: Optional[float] = None
        self.last_pre_jump_wait: float = 0.0

        # Camera settling:
        # thời điểm sau đó mới được phép dùng lại player tracker.
        self.camera_settle_until: float = 0.0

        # Sau khi thả chuột, player tracker bị khóa và lịch sử bị xóa ngay.
        # Chỉ mở lại sau flight time + 1 giây.
        self.player_tracker_locked = False
        self.player_tracker_reset_pending = False

        # Auto mode.
        self.auto_mode_enabled = False
        self.auto_mode_pending = False
        self.auto_cancel_event = threading.Event()
        self.auto_thread: Optional[threading.Thread] = None
        self.auto_cycle_index = 0
        self.auto_status = "OFF"

        # "normal" cho T, "stationary" cho Y.
        self.auto_mode_type = "normal"

        # Dữ liệu của cú nhảy gần nhất.
        self.last_jump_start_a: Optional[tuple[int, int]] = None
        self.last_jump_initial_target_b: Optional[
            tuple[int, int]
        ] = None
        self.last_jump_target_width: Optional[int] = None
        self.last_jump_target_height: Optional[int] = None
        self.last_jump_initial_distance: Optional[float] = None
        self.last_jump_final_dynamic_distance: Optional[float] = None
        self.last_jump_actual_hold: Optional[float] = None
        self.last_jump_final_required_hold: Optional[float] = None
        self.last_jump_direction: Optional[int] = None
        self.last_jump_frozen_launch_a: Optional[
            tuple[int, int]
        ] = None
        self.last_jump_landing_target_middle_x: Optional[float] = None
        self.last_jump_motion_xmin: Optional[float] = None
        self.last_jump_motion_xmax: Optional[float] = None
        self.last_jump_motion_mid_x: Optional[float] = None
        self.last_jump_motion_period: Optional[float] = None
        self.last_jump_min_to_max_seconds: Optional[float] = None
        self.last_jump_player_xmin: Optional[float] = None
        self.last_jump_player_xmax: Optional[float] = None
        self.last_jump_player_period: Optional[float] = None
        self.last_jump_player_a_used: Optional[float] = None

        # Dữ liệu hiệu chỉnh.
        self.measurement_records: list[
            dict[str, float | int | str]
        ] = []

        # Chống key repeat.
        self.pressed_hotkeys: set[str] = set()

        self.running = True
        self.overlay_enabled = True
        self.platform_overlay_enabled = True

        self.latest_character: Optional[CharacterDetection] = None
        self.last_good_character: Optional[CharacterDetection] = None
        self.latest_platforms: list[PlatformDetection] = []

        self.track_misses = 0
        self.mode = "FULL"
        self.actual_fps = 0.0
        self.last_scan_ms = 0.0

        self.lock = threading.Lock()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        screen = QApplication.instance().primaryScreen()
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.exclude_overlay_from_capture()

        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release,
        )
        self.keyboard_listener.start()

        self.detector_thread = threading.Thread(
            target=self.detection_loop,
            daemon=True,
        )
        self.detector_thread.start()

        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(self.update)
        self.repaint_timer.start(16)

    def exclude_overlay_from_capture(self) -> None:
        try:
            success = ctypes.windll.user32.SetWindowDisplayAffinity(
                int(self.winId()),
                0x00000011,
            )
            if success:
                print("[INFO] Overlay đã được loại khỏi ảnh chụp.")
            else:
                print("[WARNING] Windows không áp dụng capture exclusion.")
        except Exception as error:
            print(f"[WARNING] Capture exclusion thất bại: {error}")

    @staticmethod
    def crop_alpha(
        image: np.ndarray,
        mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        points = cv2.findNonZero(mask)
        if points is None:
            return image, mask
        x, y, w, h = cv2.boundingRect(points)
        return image[y:y+h, x:x+w], mask[y:y+h, x:x+w]

    @staticmethod
    def make_edges(
        gray: np.ndarray,
        mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 45, 130)
        if mask is not None:
            edges = cv2.bitwise_and(edges, mask)
        return edges

    @staticmethod
    def normalize_vector(values: np.ndarray) -> Optional[np.ndarray]:
        vector = values.astype(np.float32).reshape(-1)
        vector -= vector.mean()
        norm = float(np.linalg.norm(vector))

        if norm <= 1e-8:
            return None

        return vector / norm

    @classmethod
    def load_platform_template(
        cls,
    ) -> tuple[np.ndarray, list[tuple[float, np.ndarray]]]:
        if not PLATFORM_TEMPLATE_PATH.exists():
            raise RuntimeError(
                "Thiếu ảnh mẫu platform:\n"
                f"{PLATFORM_TEMPLATE_PATH}\n"
                "Đặt ảnh platform tên platform_template.png cạnh file Python."
            )

        raw = imread_unicode(PLATFORM_TEMPLATE_PATH)

        if raw is None:
            raise RuntimeError(
                f"Không đọc được ảnh platform: {PLATFORM_TEMPLATE_PATH}"
            )

        if raw.ndim == 2:
            image = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)

        elif raw.ndim == 3 and raw.shape[2] == 4:
            image = raw[:, :, :3]
            alpha = raw[:, :, 3]
            _, alpha_mask = cv2.threshold(
                alpha,
                ALPHA_THRESHOLD,
                255,
                cv2.THRESH_BINARY,
            )
            points = cv2.findNonZero(alpha_mask)

            if points is not None:
                x, y, w, h = cv2.boundingRect(points)
                image = image[y:y+h, x:x+w]

        else:
            image = raw[:, :, :3]

        # Mỗi hàng lấy màu trung vị trên toàn chiều rộng.
        # Khi platform bị kéo giãn theo X, profile theo Y vẫn gần như giữ nguyên.
        row_profile = np.median(
            image.astype(np.float32),
            axis=1,
        ).astype(np.uint8)

        strips: list[tuple[float, np.ndarray]] = []

        for scale in PLATFORM_HEIGHT_SCALES:
            scaled_height = max(
                8,
                int(round(image.shape[0] * scale)),
            )

            scaled_profile = cv2.resize(
                row_profile.reshape(image.shape[0], 1, 3),
                (1, scaled_height),
                interpolation=cv2.INTER_LINEAR,
            )

            strip = np.repeat(
                scaled_profile,
                PLATFORM_STRIP_WIDTH,
                axis=1,
            )

            strips.append((scale, strip))

        print(
            f"[INFO] Đã tải platform template: "
            f"{image.shape[1]}x{image.shape[0]} | "
            f"{len(strips)} height scales"
        )

        return image, strips

    @classmethod
    def load_character_templates(cls) -> list[CharacterTemplate]:
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        supported = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        result: list[CharacterTemplate] = []

        for path in sorted(TEMPLATE_DIR.iterdir()):
            if path.suffix.lower() not in supported:
                continue

            raw = imread_unicode(path)
            if raw is None:
                print(f"[WARNING] Không đọc được {path.name}")
                continue

            mask = None

            if raw.ndim == 2:
                image = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
            elif raw.ndim == 3 and raw.shape[2] == 4:
                image = raw[:, :, :3]
                _, mask = cv2.threshold(
                    raw[:, :, 3],
                    ALPHA_THRESHOLD,
                    255,
                    cv2.THRESH_BINARY,
                )
                image, mask = cls.crop_alpha(image, mask)
            elif raw.ndim == 3 and raw.shape[2] == 3:
                image = raw
            else:
                continue

            h, w = image.shape[:2]
            if w < 8 or h < 8:
                continue

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cls.make_edges(gray, mask)

            if cv2.countNonZero(edges) < 10:
                continue

            coarse = cv2.resize(
                edges,
                None,
                fx=FULL_SCAN_SCALE,
                fy=FULL_SCAN_SCALE,
                interpolation=cv2.INTER_AREA,
            )
            _, coarse = cv2.threshold(coarse, 25, 255, cv2.THRESH_BINARY)

            result.append(
                CharacterTemplate(
                    name=path.name,
                    image_bgr=image,
                    mask=mask,
                    edges=edges,
                    coarse_edges=coarse,
                    width=w,
                    height=h,
                )
            )

        if not result:
            raise RuntimeError(f"Không có template nhân vật trong {TEMPLATE_DIR}")

        print(f"[INFO] Đã tải {len(result)} template nhân vật.")
        return result

    @staticmethod
    def edge_match(
        screen_edges: np.ndarray,
        template_edges: np.ndarray,
    ) -> tuple[float, tuple[int, int]]:
        if (
            template_edges.shape[1] > screen_edges.shape[1]
            or template_edges.shape[0] > screen_edges.shape[0]
        ):
            return -1.0, (0, 0)

        response = cv2.matchTemplate(
            screen_edges,
            template_edges,
            cv2.TM_CCOEFF_NORMED,
        )
        response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, location = cv2.minMaxLoc(response)
        return float(score), location

    @staticmethod
    def color_score(
        roi: np.ndarray,
        template: CharacterTemplate,
        location: tuple[int, int],
    ) -> float:
        x, y = location
        patch = roi[y:y+template.height, x:x+template.width]

        if patch.shape[:2] != (template.height, template.width):
            return -1.0

        p = patch.astype(np.float32)
        t = template.image_bgr.astype(np.float32)

        if template.mask is not None:
            valid = template.mask > 0
            p = p[valid]
            t = t[valid]
        else:
            p = p.reshape(-1)
            t = t.reshape(-1)

        p = p.reshape(-1)
        t = t.reshape(-1)

        if p.size != t.size:
            return -1.0

        p -= p.mean()
        t -= t.mean()
        denominator = float(np.linalg.norm(p) * np.linalg.norm(t))

        if denominator <= 1e-8:
            return -1.0

        return (float(np.dot(p, t) / denominator) + 1.0) * 0.5

    def detect_character_in_roi(
        self,
        screenshot: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> Optional[CharacterDetection]:
        roi = screenshot[y1:y2, x1:x2]
        if roi.size == 0:
            return None

        edges = self.make_edges(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY))
        best = None

        for template in self.templates:
            edge_score, location = self.edge_match(edges, template.edges)
            if edge_score < CHARACTER_MIN_EDGE_SCORE:
                continue

            color = self.color_score(roi, template, location)
            if color < 0:
                continue

            total = EDGE_WEIGHT * max(edge_score, 0) + COLOR_WEIGHT * max(color, 0)

            candidate = CharacterDetection(
                template_name=template.name,
                total_score=total,
                edge_score=edge_score,
                color_score=color,
                left=x1 + location[0],
                top=y1 + location[1],
                width=template.width,
                height=template.height,
            )

            if best is None or candidate.total_score > best.total_score:
                best = candidate

        if best is None or best.total_score < self.character_threshold:
            return None
        return best

    def detect_character_full(
        self,
        screenshot: np.ndarray,
    ) -> Optional[CharacterDetection]:
        full_edges = self.make_edges(
            cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        )
        coarse_screen = cv2.resize(
            full_edges,
            None,
            fx=FULL_SCAN_SCALE,
            fy=FULL_SCAN_SCALE,
            interpolation=cv2.INTER_AREA,
        )
        _, coarse_screen = cv2.threshold(
            coarse_screen,
            25,
            255,
            cv2.THRESH_BINARY,
        )

        best = None

        for template in self.templates:
            coarse_score, coarse_location = self.edge_match(
                coarse_screen,
                template.coarse_edges,
            )
            if coarse_score < 0.10:
                continue

            approx_x = int(coarse_location[0] / FULL_SCAN_SCALE)
            approx_y = int(coarse_location[1] / FULL_SCAN_SCALE)

            x1 = max(0, approx_x - REFINE_MARGIN)
            y1 = max(0, approx_y - REFINE_MARGIN)
            x2 = min(
                screenshot.shape[1],
                approx_x + template.width + REFINE_MARGIN,
            )
            y2 = min(
                screenshot.shape[0],
                approx_y + template.height + REFINE_MARGIN,
            )

            candidate = self.detect_character_in_roi(
                screenshot, x1, y1, x2, y2
            )
            if candidate is not None and (
                best is None or candidate.total_score > best.total_score
            ):
                best = candidate

        return best

    def detect_character(
        self,
        screenshot: np.ndarray,
    ) -> Optional[CharacterDetection]:
        with self.lock:
            previous = self.last_good_character

        if previous is not None and self.track_misses < MAX_TRACK_MISSES:
            self.mode = "TRACK"
            cx, cy = previous.center

            detection = self.detect_character_in_roi(
                screenshot,
                max(0, cx - TRACK_MARGIN_X),
                max(0, cy - TRACK_MARGIN_Y),
                min(screenshot.shape[1], cx + TRACK_MARGIN_X),
                min(screenshot.shape[0], cy + TRACK_MARGIN_Y),
            )

            if detection is not None:
                self.track_misses = 0
                return detection

            self.track_misses += 1
            if self.track_misses < MAX_TRACK_MISSES:
                return None

        self.mode = "FULL"
        detection = self.detect_character_full(screenshot)

        if detection is not None:
            self.track_misses = 0

        return detection

    @staticmethod
    def merge_platform_boxes(
        boxes: list[PlatformDetection],
    ) -> list[PlatformDetection]:
        """
        Gộp các box gần cùng Y và chồng lấn theo X.
        """
        if not boxes:
            return []

        boxes.sort(
            key=lambda item: (
                item.top,
                item.left,
            )
        )

        merged: list[PlatformDetection] = []

        for candidate in boxes:
            matched = None

            for kept in merged:
                vertical_close = abs(
                    candidate.top - kept.top
                ) <= 8

                overlap_left = max(
                    candidate.left,
                    kept.left,
                )
                overlap_right = min(
                    candidate.right,
                    kept.right,
                )
                overlap = max(
                    0,
                    overlap_right - overlap_left,
                )

                horizontal_close = (
                    overlap > 0
                    or candidate.left <= kept.right + 25
                    and candidate.right >= kept.left - 25
                )

                if vertical_close and horizontal_close:
                    matched = kept
                    break

            if matched is None:
                merged.append(candidate)
                continue

            new_left = min(
                matched.left,
                candidate.left,
            )
            new_top = min(
                matched.top,
                candidate.top,
            )
            new_right = max(
                matched.right,
                candidate.right,
            )
            new_bottom = max(
                matched.bottom,
                candidate.bottom,
            )

            matched.left = new_left
            matched.top = new_top
            matched.width = new_right - new_left
            matched.height = new_bottom - new_top
            matched.total_score = max(
                matched.total_score,
                candidate.total_score,
            )
            matched.image_score = max(
                matched.image_score,
                candidate.image_score,
            )
            matched.gradient_score = max(
                matched.gradient_score,
                candidate.gradient_score,
            )
            matched.column_coverage = max(
                matched.column_coverage,
                candidate.column_coverage,
            )

        return merged

    def select_platform_search_area(self) -> None:
        """
        B lần 1: lưu góc thứ nhất.
        B lần 2: lưu góc thứ hai và tạo hình chữ nhật tìm platform.
        B lần 3: bắt đầu chọn một vùng mới.
        """
        x, y = self.mouse_controller.position
        point = (int(x), int(y))

        with self.lock:
            if (
                self.platform_roi_first_point is None
                or self.platform_search_rect is not None
            ):
                self.platform_roi_first_point = point
                self.platform_search_rect = None

                print(
                    f"[PLATFORM ROI] Điểm 1 = {point}. "
                    "Đưa chuột tới góc đối diện rồi nhấn B lần nữa."
                )
                return

            first_x, first_y = self.platform_roi_first_point

            left = min(first_x, point[0])
            top = min(first_y, point[1])
            right = max(first_x, point[0])
            bottom = max(first_y, point[1])

            if right - left < 20 or bottom - top < 20:
                print(
                    "[PLATFORM ROI] Vùng quá nhỏ. "
                    "Hãy nhấn B để chọn lại điểm 1."
                )
                self.platform_roi_first_point = None
                self.platform_search_rect = None
                return

            self.platform_search_rect = (
                left,
                top,
                right,
                bottom,
            )
            self.platform_roi_first_point = None

        print(
            f"[PLATFORM ROI] Đã đặt vùng: "
            f"({left}, {top}) -> ({right}, {bottom}), "
            f"kích thước={right-left}x{bottom-top}"
        )

    def detect_platforms(
        self,
        screenshot: np.ndarray,
    ) -> list[PlatformDetection]:
        """
        Match một strip dọc hẹp trên toàn màn hình.

        Một platform dài tạo ra nhiều điểm match liền nhau theo X.
        Chữ hoặc vật thể nhỏ có thể tạo vài điểm match nhưng không tạo
        một dải liên tục đủ dài để vượt PLATFORM_MIN_WIDTH.
        """
        screen_height, screen_width = screenshot.shape[:2]
        candidates: list[PlatformDetection] = []

        for scale, strip in self.platform_strips:
            strip_height, strip_width = strip.shape[:2]

            if (
                strip_height > screen_height
                or strip_width > screen_width
            ):
                continue

            response = cv2.matchTemplate(
                screenshot,
                strip,
                cv2.TM_CCOEFF_NORMED,
            )

            response = np.nan_to_num(
                response,
                nan=-1.0,
                posinf=-1.0,
                neginf=-1.0,
            )

            binary = np.uint8(
                response >= self.platform_threshold
            ) * 255

            # Chỉ nối theo chiều ngang, không nối các dòng chữ khác Y.
            close_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (PLATFORM_CLOSE_GAP, 1),
            )
            open_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (PLATFORM_OPEN_WIDTH, 1),
            )

            binary = cv2.morphologyEx(
                binary,
                cv2.MORPH_CLOSE,
                close_kernel,
            )
            binary = cv2.morphologyEx(
                binary,
                cv2.MORPH_OPEN,
                open_kernel,
            )

            contours, _ = cv2.findContours(
                binary,
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)

                detected_width = w + strip_width - 1
                detected_height = h + strip_height - 1

                if detected_width < PLATFORM_MIN_WIDTH:
                    continue

                if h > PLATFORM_MAX_RESPONSE_HEIGHT:
                    continue

                bottom = y + detected_height

                if (
                    PLATFORM_BOTTOM_EXCLUSION_PX > 0
                    and bottom >= (
                        screen_height
                        - PLATFORM_BOTTOM_EXCLUSION_PX
                    )
                ):
                    continue

                score_region = response[
                    y:y+h,
                    x:x+w,
                ]

                if score_region.size == 0:
                    continue

                maximum_score = float(
                    np.max(score_region)
                )
                mean_score = float(
                    np.mean(
                        score_region[
                            score_region
                            >= self.platform_threshold
                        ]
                    )
                ) if np.any(
                    score_region
                    >= self.platform_threshold
                ) else maximum_score

                # Tỷ lệ cột trong candidate có ít nhất một điểm match.
                local_binary = binary[
                    y:y+h,
                    x:x+w,
                ]
                column_hits = np.any(
                    local_binary > 0,
                    axis=0,
                )
                coverage = float(
                    np.count_nonzero(column_hits)
                ) / max(w, 1)

                total_score = (
                    0.70 * maximum_score
                    + 0.20 * mean_score
                    + 0.10 * coverage
                )

                candidates.append(
                    PlatformDetection(
                        left=x,
                        top=y,
                        width=detected_width,
                        height=detected_height,
                        total_score=total_score,
                        image_score=maximum_score,
                        gradient_score=mean_score,
                        column_coverage=coverage,
                    )
                )

        merged = self.merge_platform_boxes(
            candidates
        )

        # Ưu tiên platform dài và score tốt.
        merged.sort(
            key=lambda item: (
                item.total_score,
                item.width,
            ),
            reverse=True,
        )

        return merged

    def detection_loop(self) -> None:
        with mss.MSS() as capture:
            monitors = capture.monitors
            monitor = (
                monitors[MONITOR_INDEX]
                if MONITOR_INDEX < len(monitors)
                else monitors[1]
            )

            monitor_left = int(monitor["left"])
            monitor_top = int(monitor["top"])

            fps_start = time.perf_counter()
            frames = 0

            while self.running:
                started = time.perf_counter()

                try:
                    shot = np.array(capture.grab(monitor))
                    screenshot = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

                    character = self.detect_character(screenshot)

                    with self.lock:
                        search_rect = self.platform_search_rect

                    platform_offset_x = 0
                    platform_offset_y = 0
                    platform_source = screenshot

                    if search_rect is not None:
                        global_left, global_top, global_right, global_bottom = (
                            search_rect
                        )

                        local_left = max(
                            0,
                            global_left - monitor_left,
                        )
                        local_top = max(
                            0,
                            global_top - monitor_top,
                        )
                        local_right = min(
                            screenshot.shape[1],
                            global_right - monitor_left,
                        )
                        local_bottom = min(
                            screenshot.shape[0],
                            global_bottom - monitor_top,
                        )

                        if (
                            local_right > local_left
                            and local_bottom > local_top
                        ):
                            platform_source = screenshot[
                                local_top:local_bottom,
                                local_left:local_right,
                            ]
                            platform_offset_x = local_left
                            platform_offset_y = local_top
                        else:
                            platform_source = screenshot[0:0, 0:0]

                    if platform_source.size > 0:
                        platforms = self.detect_platforms(
                            platform_source
                        )
                    else:
                        platforms = []

                    if character is not None:
                        character.left += monitor_left
                        character.top += monitor_top

                    for platform in platforms:
                        platform.left += (
                            monitor_left + platform_offset_x
                        )
                        platform.top += (
                            monitor_top + platform_offset_y
                        )

                    with self.lock:
                        self.latest_character = character
                        self.latest_platforms = platforms

                        if character is not None:
                            self.last_good_character = CharacterDetection(
                                template_name=character.template_name,
                                total_score=character.total_score,
                                edge_score=character.edge_score,
                                color_score=character.color_score,
                                left=character.left - monitor_left,
                                top=character.top - monitor_top,
                                width=character.width,
                                height=character.height,
                            )

                    self.update_focused_platform()
                    self.update_player_motion()

                except Exception as error:
                    print(f"[ERROR] Detection: {error}")

                elapsed = time.perf_counter() - started
                self.last_scan_ms = elapsed * 1000

                frames += 1
                now = time.perf_counter()

                if now - fps_start >= 1:
                    self.actual_fps = frames / (now - fps_start)
                    frames = 0
                    fps_start = now

                remaining = FRAME_INTERVAL - elapsed
                if remaining > 0:
                    time.sleep(remaining)

    @staticmethod
    def calculate_hold_time(distance_px: float) -> float:
        hold = (
            HOLD_TIME_SLOPE * max(0.0, distance_px)
            + HOLD_TIME_INTERCEPT
        )
        return min(
            MAX_HOLD_SECONDS,
            max(MIN_HOLD_SECONDS, hold),
        )

    def get_character_a(
        self,
        allow_last_known: bool = True,
    ) -> Optional[tuple[int, int]]:
        with self.lock:
            current = self.latest_character
            previous = self.last_good_character

        if current is not None:
            return current.feet

        if allow_last_known and previous is not None:
            return previous.feet

        return None

    def get_adjusted_platform_target(
        self,
        platform: PlatformDetection,
    ) -> tuple[int, int]:
        """
        Lấy B từ tâm platform cộng offset U/I.

        B luôn bị giới hạn bên trong platform, cách mép một khoảng an toàn.
        """
        center_x, top_y = platform.top_center

        motion_mid = self.motion_tracker.motion_mid_x
        if motion_mid is not None:
            center_x = int(round(motion_mid))

        safe_left = platform.left + min(
            TARGET_SAFE_MARGIN_PX,
            max(1, platform.width // 4),
        )
        safe_right = platform.right - min(
            TARGET_SAFE_MARGIN_PX,
            max(1, platform.width // 4),
        )

        adjusted_x = center_x + self.target_offset_x
        adjusted_x = max(
            safe_left,
            min(safe_right, adjusted_x),
        )

        return int(adjusted_x), int(top_y)

    def shift_target_offset(self, delta: int) -> None:
        """
        U/I: dịch điểm B sang trái hoặc phải.
        """
        self.target_offset_x += delta

        print(
            f"[TARGET OFFSET] {self.target_offset_x:+d}px "
            f"(U=trái, I=phải)"
        )

    def select_focused_platform_with_e(self) -> None:
        """
        E: chọn platform gần con trỏ làm platform cần chú ý.

        Khi chọn platform mới:
        - khóa platform này làm mục tiêu;
        - xóa toàn bộ lịch sử chuyển động của platform cũ;
        - bắt đầu thu thập x_min, x_max và chu kỳ từ đầu;
        - R sẽ chỉ nhảy vào platform đã chọn này.
        """
        if self.jump_in_progress:
            print(
                "[BUSY] Không thể đổi platform khi đang nhảy."
            )
            return

        cursor_x, cursor_y = self.mouse_controller.position

        with self.lock:
            platforms = list(self.latest_platforms)

        if not platforms:
            print(
                "[ERROR] Hiện không detect được platform nào."
            )
            return

        selected = min(
            platforms,
            key=lambda p: (
                (p.top_center[0] - cursor_x) ** 2
                + (p.top_center[1] - cursor_y) ** 2
            ),
        )

        now = time.perf_counter()

        with self.lock:
            self.focused_platform = selected
            self.focused_platform_locked = True
            self.focused_platform_last_seen = now
            self.selected_target_platform = selected

        self.motion_tracker.clear()

        center_x, top_y = selected.top_center
        self.motion_tracker.add(
            now,
            float(center_x),
            selected.width,
            top_y,
        )

        print(
            f"[E] Đã chọn platform mục tiêu | "
            f"center={selected.top_center} | "
            f"size={selected.width}x{selected.height} | "
            f"threshold={self.platform_threshold:.2f}"
        )
        print(
            "[E] Đang thu thập lại lịch sử chuyển động. "
            "Chờ vài giây rồi nhấn R để đồng bộ tốt hơn."
        )

    def get_player_a_for_jump(
        self,
        current_point: tuple[int, int],
    ) -> tuple[int, int]:
        """
        Nếu dao động ngang của nhân vật >=30 px, dùng x_max làm A.
        Nếu nhỏ hơn 30 px, xem đó là animation/nhiễu và dùng X hiện tại.
        """
        # Khi tracker bị khóa hoặc camera settling, luôn dùng X hiện tại.
        if self.player_tracker_locked or self.is_camera_settling():
            return current_point

        if self.player_motion_tracker.has_meaningful_motion:
            player_xmax = self.player_motion_tracker.x_max

            if player_xmax is not None:
                return (
                    int(round(player_xmax)),
                    current_point[1],
                )

        return current_point

    def is_camera_settling(self) -> bool:
        return (
            self.player_tracker_locked
            or time.perf_counter() < self.camera_settle_until
        )

    def remaining_camera_settle_seconds(self) -> float:
        return max(
            0.0,
            self.camera_settle_until - time.perf_counter(),
        )

    def reset_motion_observation(self) -> None:
        """
        Xóa lịch sử dao động trước khi quan sát một target mới.
        """
        self.motion_tracker.clear()
        self.player_motion_tracker.clear()

    def select_nearest_platform_for_auto(self) -> bool:
        """
        Chọn platform gần con trỏ nhất và khóa làm target.
        """
        cursor_x, cursor_y = self.mouse_controller.position

        with self.lock:
            platforms = list(self.latest_platforms)

        if not platforms:
            return False

        selected = min(
            platforms,
            key=lambda p: (
                (p.top_center[0] - cursor_x) ** 2
                + (p.top_center[1] - cursor_y) ** 2
            ),
        )

        now = time.perf_counter()

        with self.lock:
            self.focused_platform = selected
            self.focused_platform_locked = True
            self.focused_platform_last_seen = now
            self.selected_target_platform = selected

        self.reset_motion_observation()

        center_x, top_y = selected.top_center
        self.motion_tracker.add(
            now,
            float(center_x),
            selected.width,
            top_y,
        )

        print(
            f"[AUTO TARGET] center={selected.top_center} | "
            f"size={selected.width}x{selected.height}"
        )

        return True

    def wait_cancelable(self, seconds: float) -> bool:
        """
        Chờ nhưng cho phép T hủy auto ngay.
        Trả về False nếu bị hủy.
        """
        deadline = time.perf_counter() + max(0.0, seconds)

        while (
            self.running
            and not self.auto_cancel_event.is_set()
        ):
            remaining = deadline - time.perf_counter()

            if remaining <= 0:
                return True

            time.sleep(
                min(AUTO_LOOP_POLL_SECONDS, remaining)
            )

        return False

    @staticmethod
    def platform_identity_cost(
        candidate: PlatformDetection,
        reference: PlatformDetection,
    ) -> float:
        """
        Điểm giống nhau để theo dõi cùng một platform qua nhiều frame.
        """
        cx, cy = candidate.top_center
        rx, ry = reference.top_center

        return (
            abs(cx - rx)
            + abs(cy - ry) * 2.0
            + abs(candidate.width - reference.width) * 1.2
            + abs(candidate.height - reference.height) * 3.0
        )

    def find_same_platform_candidate(
        self,
        reference: PlatformDetection,
    ) -> Optional[PlatformDetection]:
        """
        Tìm platform hiện tại tương ứng với platform đang kiểm tra.
        """
        with self.lock:
            platforms = list(self.latest_platforms)

        if not platforms:
            return None

        rx, ry = reference.top_center

        candidates = [
            platform
            for platform in platforms
            if abs(platform.top_center[0] - rx)
            <= PLATFORM_TRACK_MAX_JUMP_PER_FRAME
            and abs(platform.top_center[1] - ry) <= 50
        ]

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda item: self.platform_identity_cost(
                item,
                reference,
            ),
        )

    def get_ranked_platforms_near_cursor(
        self,
        excluded_keys: set[tuple[int, int, int]],
    ) -> list[PlatformDetection]:
        """
        Xếp platform theo khoảng cách đến con trỏ, bỏ các platform đã thử.
        """
        cursor_x, cursor_y = self.mouse_controller.position

        with self.lock:
            platforms = list(self.latest_platforms)

        candidates = []

        for platform in platforms:
            key = (
                round(platform.top_center[0] / 20),
                round(platform.top_center[1] / 20),
                round(platform.width / 20),
            )

            if key in excluded_keys:
                continue

            candidates.append(platform)

        candidates.sort(
            key=lambda platform: (
                (platform.top_center[0] - cursor_x) ** 2
                + (platform.top_center[1] - cursor_y) ** 2
            )
        )

        return candidates

    def check_platform_stationary(
        self,
        initial_platform: PlatformDetection,
    ) -> tuple[bool, Optional[PlatformDetection], float]:
        """
        Theo dõi platform tối đa 2 giây.

        Nếu range X vượt 30 px thì trả về False ngay lập tức.
        """
        started = time.perf_counter()
        current_platform = initial_platform
        x_values = [float(initial_platform.top_center[0])]

        while (
            self.running
            and not self.auto_cancel_event.is_set()
        ):
            elapsed = time.perf_counter() - started

            if elapsed >= STATIONARY_PLATFORM_CHECK_SECONDS:
                break

            tracked = self.find_same_platform_candidate(
                current_platform
            )

            if tracked is None:
                time.sleep(AUTO_LOOP_POLL_SECONDS)
                continue

            current_platform = tracked
            x_values.append(float(tracked.top_center[0]))

            current_range = max(x_values) - min(x_values)

            self.auto_status = (
                f"Y_CHECK_{elapsed:.1f}s_"
                f"RANGE_{current_range:.1f}px"
            )

            if (
                current_range
                > STATIONARY_PLATFORM_MAX_X_RANGE_PX
            ):
                return (
                    False,
                    current_platform,
                    current_range,
                )

            time.sleep(AUTO_LOOP_POLL_SECONDS)

        if self.auto_cancel_event.is_set():
            return False, current_platform, 0.0

        if not x_values:
            return False, current_platform, 0.0

        final_range = max(x_values) - min(x_values)

        return (
            final_range
            <= STATIONARY_PLATFORM_MAX_X_RANGE_PX,
            current_platform,
            final_range,
        )

    def select_stationary_platform_for_auto(self) -> bool:
        """
        Thử tối đa 3 platform gần con trỏ.

        Mỗi platform được check tối đa 2 giây.
        """
        excluded_keys: set[tuple[int, int, int]] = set()

        for attempt in range(
            1,
            STATIONARY_PLATFORM_MAX_ATTEMPTS + 1,
        ):
            if self.auto_cancel_event.is_set():
                return False

            ranked = self.get_ranked_platforms_near_cursor(
                excluded_keys
            )

            if not ranked:
                print(
                    f"[AUTO Y] Lần {attempt}: "
                    "không còn platform để thử."
                )
                return False

            candidate = ranked[0]

            key = (
                round(candidate.top_center[0] / 20),
                round(candidate.top_center[1] / 20),
                round(candidate.width / 20),
            )
            excluded_keys.add(key)

            self.auto_status = (
                f"Y_CHECK_PLATFORM_{attempt}/"
                f"{STATIONARY_PLATFORM_MAX_ATTEMPTS}"
            )

            print(
                f"[AUTO Y] Kiểm tra platform {attempt}/"
                f"{STATIONARY_PLATFORM_MAX_ATTEMPTS} | "
                f"center={candidate.top_center} | "
                f"size={candidate.width}x{candidate.height}"
            )

            stationary, tracked, x_range = (
                self.check_platform_stationary(candidate)
            )

            if self.auto_cancel_event.is_set():
                return False

            if not stationary:
                print(
                    f"[AUTO Y] Loại platform: "
                    f"X range={x_range:.2f}px > "
                    f"{STATIONARY_PLATFORM_MAX_X_RANGE_PX:.1f}px"
                )
                continue

            if tracked is None:
                print(
                    "[AUTO Y] Mất platform trong lúc kiểm tra."
                )
                continue

            now = time.perf_counter()

            with self.lock:
                self.focused_platform = tracked
                self.focused_platform_locked = True
                self.focused_platform_last_seen = now
                self.selected_target_platform = tracked

            self.motion_tracker.clear()

            center_x, top_y = tracked.top_center
            self.motion_tracker.add(
                now,
                float(center_x),
                tracked.width,
                top_y,
            )

            print(
                f"[AUTO Y] Đã xác nhận platform đứng yên | "
                f"center={tracked.top_center} | "
                f"X range={x_range:.2f}px"
            )

            return True

        print(
            "[AUTO Y] Đã thử 3 platform nhưng không có "
            "platform đứng yên. Tự thoát auto."
        )

        return False

    def toggle_stationary_auto_mode(self) -> None:
        """
        Y lần 1:
            bắt đầu auto stationary sau 3 giây.

        Y hoặc T lần nữa:
            hủy auto.
        """
        if self.auto_mode_enabled or self.auto_mode_pending:
            self.auto_cancel_event.set()
            self.auto_mode_enabled = False
            self.auto_mode_pending = False
            self.auto_status = "CANCELLED"

            print("[AUTO Y] Đã yêu cầu hủy auto.")
            return

        self.auto_cancel_event.clear()
        self.auto_mode_pending = True
        self.auto_mode_type = "stationary"
        self.auto_status = "Y_STARTING_IN_3S"

        self.auto_thread = threading.Thread(
            target=self.auto_worker,
            daemon=True,
        )
        self.auto_thread.start()

        print(
            "[AUTO Y] Sẽ bắt đầu sau 3 giây. "
            "Nhấn Y hoặc T để hủy."
        )

    def toggle_auto_mode(self) -> None:
        """
        T lần 1:
            sau 3 giây bắt đầu auto.

        T lần nữa:
            hủy ngay cả khi đang đếm ngược, quan sát hoặc chờ chu kỳ.
        """
        if self.auto_mode_enabled or self.auto_mode_pending:
            self.auto_cancel_event.set()
            self.auto_mode_enabled = False
            self.auto_mode_pending = False
            self.auto_status = "CANCELLED"

            print("[AUTO] Đã yêu cầu hủy auto.")
            return

        self.auto_cancel_event.clear()
        self.auto_mode_pending = True
        self.auto_mode_type = "normal"
        self.auto_status = "STARTING_IN_3S"

        self.auto_thread = threading.Thread(
            target=self.auto_worker,
            daemon=True,
        )
        self.auto_thread.start()

        print(
            "[AUTO] Sẽ bắt đầu sau 3 giây. "
            "Nhấn T lần nữa để hủy."
        )

    def auto_worker(self) -> None:
        try:
            if not self.wait_cancelable(
                AUTO_START_DELAY_SECONDS
            ):
                return

            self.auto_mode_pending = False
            self.auto_mode_enabled = True
            self.auto_status = "ACTIVE"
            self.auto_cycle_index = 0

            print("[AUTO] Đã vào trạng thái auto.")

            while (
                self.running
                and self.auto_mode_enabled
                and not self.auto_cancel_event.is_set()
            ):
                self.auto_cycle_index += 1

                # Nếu vừa đáp, chờ camera hoàn toàn ổn định.
                settle_remaining = (
                    self.remaining_camera_settle_seconds()
                )

                if settle_remaining > 0:
                    self.auto_status = (
                        f"CAMERA_SETTLE_{settle_remaining:.2f}s"
                    )

                    if not self.wait_cancelable(
                        settle_remaining
                    ):
                        break

                if self.auto_cancel_event.is_set():
                    break

                self.auto_status = "SELECT_TARGET"

                if self.auto_mode_type == "stationary":
                    selected = (
                        self.select_stationary_platform_for_auto()
                    )

                    if not selected:
                        self.auto_status = "Y_NO_STATIONARY_PLATFORM"
                        break

                    # Platform đã được check 2 giây.
                    # Không cần quan sát platform thêm 5 giây;
                    # vẫn cho player tracker thêm một khoảng ngắn ổn định.
                    self.auto_status = "Y_CONFIRM_AND_PREPARE"

                    if not self.wait_cancelable(0.25):
                        break

                else:
                    # Có thể detector chưa kịp có platform ngay frame đầu.
                    selected = False
                    select_deadline = time.perf_counter() + 3.0

                    while (
                        time.perf_counter() < select_deadline
                        and not self.auto_cancel_event.is_set()
                    ):
                        if self.select_nearest_platform_for_auto():
                            selected = True
                            break

                        time.sleep(AUTO_LOOP_POLL_SECONDS)

                    if not selected:
                        print(
                            "[AUTO] Không tìm thấy platform gần con trỏ; "
                            "thử lại."
                        )
                        self.auto_status = "WAIT_PLATFORM"

                        if not self.wait_cancelable(0.5):
                            break
                        continue

                    # Auto T quan sát đủ 5 giây.
                    self.auto_status = "OBSERVE_5S"

                    if not self.wait_cancelable(
                        AUTO_OBSERVE_SECONDS
                    ):
                        break

                    if self.auto_cancel_event.is_set():
                        break

                self.auto_status = "JUMPING"

                # Dùng chung logic R.
                started = self.request_jump(
                    initiated_by_auto=True
                )

                if not started:
                    print(
                        "[AUTO] Không thể bắt đầu cú nhảy; "
                        "sẽ thử lại."
                    )

                    if not self.wait_cancelable(0.5):
                        break
                    continue

                # Chờ jump worker kết thúc.
                while (
                    self.running
                    and self.jump_in_progress
                    and not self.auto_cancel_event.is_set()
                ):
                    time.sleep(AUTO_LOOP_POLL_SECONDS)

                if self.auto_cancel_event.is_set():
                    break

                # Sau release, camera_settle_until đã gồm:
                # flight time + 1 giây camera settling.
                self.auto_status = "WAIT_LANDING_AND_CAMERA"

                settle_remaining = (
                    self.remaining_camera_settle_seconds()
                )

                if settle_remaining > 0:
                    if not self.wait_cancelable(
                        settle_remaining
                    ):
                        break

                # Mở tracker và bắt đầu lịch sử player hoàn toàn mới.
                self.unlock_player_tracker_if_ready()

                # Lặp: chọn platform gần con trỏ tại thời điểm mới.
                self.auto_status = "NEXT_CYCLE"

        except Exception as error:
            print(f"[AUTO ERROR] {error}")

        finally:
            self.auto_mode_enabled = False
            self.auto_mode_pending = False

            if self.auto_cancel_event.is_set():
                self.auto_status = "OFF"
            else:
                self.auto_status = "STOPPED"

            print("[AUTO] Đã dừng.")

    def unlock_player_tracker_if_ready(self) -> None:
        """
        Mở lại player tracker khi đã qua flight time + 1 giây.

        Lịch sử đã được xóa ngay lúc thả chuột, nên khi mở lại tracker
        sẽ thu thập hoàn toàn từ đầu.
        """
        if not self.player_tracker_locked:
            return

        if time.perf_counter() < self.camera_settle_until:
            return

        self.player_tracker_locked = False
        self.player_tracker_reset_pending = False

        # Xóa lần nữa để đảm bảo không có sample nào lọt vào trong lúc khóa.
        self.player_motion_tracker.clear()

        print(
            "[PLAYER TRACKER] Đã mở lại sau camera settling. "
            "Bắt đầu thu thập lịch sử mới từ đầu."
        )

    def update_player_motion(self) -> None:
        """
        Chỉ thu thập dao động nhân vật khi chưa bắt đầu giữ chuột.

        Sau khi nhảy, vị trí nhân vật trong không trung không được đưa vào
        lịch sử dao động của platform xuất phát.
        """
        if self.jump_in_progress:
            return

        self.unlock_player_tracker_if_ready()

        # Không học player trong lúc tracker đang bị khóa sau cú nhảy.
        if self.player_tracker_locked:
            return

        # Bảo vệ bổ sung nếu camera vẫn đang settle.
        if self.is_camera_settling():
            return

        point_a = self.get_character_a(
            allow_last_known=False
        )

        if point_a is None:
            return

        now = time.perf_counter()

        self.player_motion_tracker.add(
            now,
            float(point_a[0]),
            float(point_a[1]),
        )

    def update_focused_platform(self) -> None:
        """
        Theo dõi liên tục platform gần con trỏ.

        Nếu đã có platform đang theo dõi, ưu tiên candidate gần vị trí
        trước đó và không cho tâm nhảy quá 100 px mỗi frame.
        """
        cursor_x, cursor_y = self.mouse_controller.position

        with self.lock:
            platforms = list(self.latest_platforms)
            previous = self.focused_platform

        if not platforms:
            return

        with self.lock:
            locked = self.focused_platform_locked

        if previous is None:
            if locked:
                return

            selected = min(
                platforms,
                key=lambda p: (
                    (p.top_center[0] - cursor_x) ** 2
                    + (p.top_center[1] - cursor_y) ** 2
                ),
            )
        else:
            old_x, old_y = previous.top_center

            valid = [
                p for p in platforms
                if abs(p.top_center[0] - old_x)
                <= PLATFORM_TRACK_MAX_JUMP_PER_FRAME
                and abs(p.top_center[1] - old_y) <= 50
            ]

            if valid:
                selected = min(
                    valid,
                    key=lambda p: (
                        abs(p.top_center[0] - old_x)
                        + abs(p.top_center[1] - old_y) * 2
                        + abs(p.width - previous.width)
                    ),
                )
            elif locked:
                # Khi đã khóa bằng E, không tự chuyển sang platform
                # gần con trỏ khác. Giữ platform cũ và chờ detect lại.
                return
            else:
                selected = min(
                    platforms,
                    key=lambda p: (
                        (p.top_center[0] - cursor_x) ** 2
                        + (p.top_center[1] - cursor_y) ** 2
                    ),
                )
                self.motion_tracker.clear()

        now = time.perf_counter()

        with self.lock:
            self.focused_platform = selected
            self.focused_platform_last_seen = now

        center_x, top_y = selected.top_center
        self.motion_tracker.add(
            now,
            float(center_x),
            selected.width,
            top_y,
        )

    def average_flight_time(self) -> float:
        if not self.flight_time_samples:
            return DEFAULT_FLIGHT_TIME_SECONDS

        values = self.flight_time_samples[
            -MAX_FLIGHT_TIME_SAMPLES:
        ]
        return float(np.median(values))

    def mark_landing_with_w(self) -> None:
        """
        W: đánh dấu chính xác lúc nhân vật đáp đất.

        Flight time được tính từ lúc thả chuột tới lúc nhấn W.
        """
        now = time.perf_counter()
        self.last_landing_mark_time = now

        if self.last_mouse_release_time is None:
            print(
                "[W] Đã đánh dấu đáp đất, nhưng chưa có "
                "thời điểm thả chuột của cú nhảy."
            )
            return

        flight_time = now - self.last_mouse_release_time

        if not 0.02 <= flight_time <= 5.0:
            print(
                f"[W] Flight time không hợp lệ: "
                f"{flight_time:.4f}s"
            )
            return

        self.flight_time_samples.append(flight_time)

        # Khi W cho biết landing thật, vẫn giữ tracker khóa thêm đúng 1s.
        self.camera_settle_until = (
            now + CAMERA_SETTLE_AFTER_LANDING_SECONDS
        )
        self.player_tracker_locked = True
        self.player_tracker_reset_pending = True
        self.player_motion_tracker.clear()

        if (
            len(self.flight_time_samples)
            > MAX_FLIGHT_TIME_SAMPLES
        ):
            self.flight_time_samples = (
                self.flight_time_samples[
                    -MAX_FLIGHT_TIME_SAMPLES:
                ]
            )

        print(
            f"[W] Đã ghi thời gian bay={flight_time:.6f}s | "
            f"median={self.average_flight_time():.6f}s | "
            f"samples={len(self.flight_time_samples)}"
        )

    def calculate_pre_jump_wait(
        self,
        hold_time: float,
    ) -> float:
        """
        Căn thời điểm thả chuột đúng lúc X của nhân vật đạt x_max.

        Vì:
            release_time = wait + hold_time

        nên:
            wait = time_until_player_xmax
                   - hold_time
                   - sync_advance

        Nếu lần x_max gần nhất không đủ thời gian, dùng x_max của chu kỳ sau.
        """
        # Nếu R được nhấn khi camera còn di chuyển sau cú trước,
        # bỏ dự đoán player x_max và nhảy ngay.
        if self.player_tracker_locked or self.is_camera_settling():
            return 0.0

        now = time.perf_counter()

        until_player_xmax = (
            self.player_motion_tracker.seconds_until_next_xmax(
                now
            )
        )

        player_period = (
            self.player_motion_tracker.period_seconds
        )

        if (
            not self.player_motion_tracker.has_meaningful_motion
            or until_player_xmax is None
            or player_period is None
        ):
            # Biên độ dưới 30 px: bỏ dự đoán dao động và nhảy ngay.
            return 0.0

        # Trừ thêm offset để toàn bộ cú nhảy xảy ra sớm hơn,
        # bù cho độ trễ detect, xử lý và input.
        wait = (
            until_player_xmax
            - hold_time
            - PLAYER_XMAX_SYNC_ADVANCE_SECONDS
        )

        while wait < 0:
            wait += player_period

        return min(
            MAX_PRE_JUMP_WAIT_SECONDS,
            max(0.0, wait),
        )


    def choose_platform_nearest_cursor(
        self,
    ) -> Optional[PlatformDetection]:
        """
        Chọn platform có tâm mặt trên gần con trỏ nhất.
        """
        cursor_x, cursor_y = self.mouse_controller.position

        with self.lock:
            focused = self.focused_platform
            platforms = list(self.latest_platforms)

        if focused is not None:
            return focused

        if not platforms:
            return None

        return min(
            platforms,
            key=lambda p: (
                (p.top_center[0] - cursor_x) ** 2
                + (p.top_center[1] - cursor_y) ** 2
            ),
        )

    def choose_platform_nearest_character(
        self,
    ) -> Optional[PlatformDetection]:
        """
        Sau khi đáp, platform gần chân nhân vật nhất được xem là
        platform vừa nhảy tới.
        """
        point_a = self.get_character_a(allow_last_known=True)

        if point_a is None:
            return None

        with self.lock:
            platforms = list(self.latest_platforms)

        if not platforms:
            return None

        ax, ay = point_a

        return min(
            platforms,
            key=lambda p: (
                (p.top_center[0] - ax) ** 2
                + (p.top - ay) ** 2
            ),
        )

    def request_jump(
        self,
        initiated_by_auto: bool = False,
    ) -> bool:
        if self.jump_in_progress:
            print("[BUSY] Cú nhảy đang được thực hiện.")
            return False

        point_a_current = self.get_character_a(
            allow_last_known=True
        )

        if point_a_current is None:
            print(
                "[ERROR] Chưa có vị trí nhân vật hiện tại "
                "hoặc gần nhất."
            )
            return False

        point_a = self.get_player_a_for_jump(
            point_a_current
        )

        if self.is_camera_settling():
            print(
                f"[JUMP MODE] CAMERA_SETTLING: "
                f"bỏ player x_max, dùng current_x={point_a[0]} | "
                f"remaining={self.remaining_camera_settle_seconds():.3f}s"
            )

        with self.lock:
            target = self.focused_platform
            target_locked = self.focused_platform_locked

        if not target_locked or target is None:
            print(
                "[ERROR] Chưa chọn platform mục tiêu. "
                "Đưa con trỏ gần platform và nhấn E trước."
            )
            return False

        self.selected_target_platform = target
        target_b = self.get_adjusted_platform_target(target)

        distance = abs(target_b[0] - point_a[0])
        required = self.calculate_hold_time(distance)

        self.current_jump_distance = distance
        self.current_required_hold = required
        self.current_elapsed_hold = 0.0

        print(
            f"[TARGET] A={point_a} | B={target_b} | "
            f"platform={target.width}x{target.height} | "
            f"distance={distance:.1f}px | "
            f"hold={required:.4f}s"
        )

        self.last_pre_jump_wait = self.calculate_pre_jump_wait(
            required
        )

        if self.player_tracker_locked:
            player_motion_mode = "CURRENT_X_TRACKER_LOCKED"
        else:
            player_motion_mode = (
                "XMAX_SYNC"
                if self.player_motion_tracker.has_meaningful_motion
                else "CURRENT_X_NO_SYNC"
            )

        print(
            f"[SYNC] player_mode={player_motion_mode} | "
            f"pre_wait={self.last_pre_jump_wait:.4f}s | "
            f"hold={required:.4f}s | "
            f"sync_advance={PLAYER_XMAX_SYNC_ADVANCE_SECONDS:.3f}s | "
            f"player_xmin={self.player_motion_tracker.x_min} | "
            f"player_xmax={self.player_motion_tracker.x_max} | "
            f"player_range={self.player_motion_tracker.motion_range} | "
            f"min_required={PLAYER_MIN_MEANINGFUL_RANGE_PX:.1f}px | "
            f"player_period={self.player_motion_tracker.period_seconds} | "
            f"platform_middle={self.motion_tracker.motion_mid_x}"
        )

        self.jump_in_progress = True

        threading.Thread(
            target=self.jump_worker,
            daemon=True,
        ).start()

        return True

    def find_current_target_platform(
        self,
        last_target: PlatformDetection,
    ) -> Optional[PlatformDetection]:
        """
        Theo dõi platform mục tiêu qua từng frame.

        Ưu tiên platform có kích thước gần giống và tâm gần vị trí cũ.
        """
        with self.lock:
            platforms = list(self.latest_platforms)

        if not platforms:
            return None

        old_x, old_y = last_target.top_center

        def score(platform: PlatformDetection) -> float:
            px, py = platform.top_center
            position_cost = (
                (px - old_x) ** 2
                + (py - old_y) ** 2
            ) ** 0.5

            width_cost = abs(
                platform.width - last_target.width
            ) * 1.5

            height_cost = abs(
                platform.height - last_target.height
            ) * 4.0

            return position_cost + width_cost + height_cost

        return min(platforms, key=score)

    def jump_worker(self) -> None:
        pressed = False

        try:
            point_a_current = self.get_character_a(
                allow_last_known=True
            )
            target = self.selected_target_platform

            if point_a_current is None or target is None:
                print("[ERROR] Thiếu A hoặc platform mục tiêu.")
                return

            point_a = self.get_player_a_for_jump(
                point_a_current
            )

            initial_b = self.get_adjusted_platform_target(target)
            initial_distance = abs(
                initial_b[0] - point_a[0]
            )
            initial_required = self.calculate_hold_time(
                initial_distance
            )

            direction = (
                1
                if initial_b[0] >= point_a[0]
                else -1
            )

            self.last_jump_start_a = point_a
            self.last_jump_initial_target_b = initial_b
            self.last_jump_target_width = target.width
            self.last_jump_target_height = target.height
            self.last_jump_initial_distance = initial_distance
            self.last_jump_final_dynamic_distance = (
                initial_distance
            )
            self.last_jump_actual_hold = None
            self.last_jump_final_required_hold = (
                initial_required
            )
            self.last_jump_direction = direction
            self.last_jump_landing_target_middle_x = (
                self.motion_tracker.motion_mid_x
            )
            self.last_jump_motion_xmin = (
                self.motion_tracker.x_min
            )
            self.last_jump_motion_xmax = (
                self.motion_tracker.x_max
            )
            self.last_jump_motion_mid_x = (
                self.motion_tracker.motion_mid_x
            )
            self.last_jump_motion_period = (
                self.motion_tracker.period_seconds
            )
            self.last_jump_min_to_max_seconds = (
                self.motion_tracker.min_to_max_seconds
            )
            self.last_jump_player_xmin = (
                self.player_motion_tracker.x_min
            )
            self.last_jump_player_xmax = (
                self.player_motion_tracker.x_max
            )
            self.last_jump_player_period = (
                self.player_motion_tracker.period_seconds
            )
            self.last_jump_player_a_used = float(
                point_a[0]
            )

            current_target = target
            last_b = initial_b
            last_a = point_a
            required = initial_required
            distance = initial_distance

            print(
                f"[JUMP] A_pre_wait={point_a} | B={initial_b} | "
                f"distance={distance:.1f}px | "
                f"required={required:.4f}s | "
                f"wait={self.last_pre_jump_wait:.4f}s"
            )

            wait_started = time.perf_counter()
            planned_wait = self.last_pre_jump_wait

            # Trong lúc chờ, A và B vẫn được cập nhật.
            while self.running:
                waited = time.perf_counter() - wait_started

                if waited >= planned_wait:
                    break

                detected_a = self.get_character_a(
                    allow_last_known=True
                )
                tracked_target = self.focused_platform

                if detected_a is not None:
                    last_a = self.get_player_a_for_jump(
                        detected_a
                    )

                if tracked_target is not None:
                    current_target = tracked_target
                    last_b = self.get_adjusted_platform_target(
                        tracked_target
                    )

                distance = abs(last_b[0] - last_a[0])
                required = self.calculate_hold_time(distance)

                # Điều chỉnh lại để thời điểm đáp vẫn trùng lần qua giữa mới nhất.
                recalculated_wait = self.calculate_pre_jump_wait(
                    required
                )

                # Không tăng vô hạn sau khi đã chờ lâu;
                # chỉ cho phép điều chỉnh nhẹ quanh kế hoạch ban đầu.
                planned_wait = min(
                    MAX_PRE_JUMP_WAIT_SECONDS,
                    max(
                        waited,
                        min(
                            planned_wait + 0.25,
                            recalculated_wait,
                        ),
                    ),
                )

                self.current_jump_distance = distance
                self.current_required_hold = required
                self.current_elapsed_hold = -max(
                    0.0,
                    planned_wait - waited,
                )

                time.sleep(JUMP_UPDATE_INTERVAL)

            # Chốt lại A/B ngay trước khi nhấn.
            detected_a = self.get_character_a(
                allow_last_known=True
            )
            if detected_a is not None:
                last_a = self.get_player_a_for_jump(
                    detected_a
                )

            if self.focused_platform is not None:
                current_target = self.focused_platform
                last_b = self.get_adjusted_platform_target(
                    current_target
                )

            distance = abs(last_b[0] - last_a[0])
            required = self.calculate_hold_time(distance)

            # Khóa A ngay tại thời điểm bắt đầu giữ chuột.
            self.last_jump_frozen_launch_a = last_a

            started = time.perf_counter()
            self.last_jump_press_time = started
            self.mouse_controller.press(mouse.Button.left)
            pressed = True

            while self.running:
                elapsed = time.perf_counter() - started

                # Sau khi chuột đã được nhấn, A được khóa tại vị trí
                # cất cánh. Không dùng vị trí nhân vật đang bay để tính
                # lại khoảng cách, vì điều đó làm thời gian giữ thay đổi sai.
                tracked_target = (
                    self.find_current_target_platform(
                        current_target
                    )
                )

                if tracked_target is not None:
                    current_target = tracked_target
                    last_b = self.get_adjusted_platform_target(
                        tracked_target
                    )

                distance = abs(last_b[0] - last_a[0])
                required = self.calculate_hold_time(distance)

                self.selected_target_platform = current_target
                self.current_jump_distance = distance
                self.current_required_hold = required
                self.current_elapsed_hold = elapsed

                self.last_jump_final_dynamic_distance = (
                    distance
                )
                self.last_jump_final_required_hold = required

                if elapsed >= required:
                    break

                if elapsed >= MAX_HOLD_SECONDS:
                    print(
                        "[WARNING] Đạt giới hạn giữ chuột."
                    )
                    break

                time.sleep(JUMP_UPDATE_INTERVAL)

            self.mouse_controller.release(mouse.Button.left)
            self.last_mouse_release_time = time.perf_counter()

            # Xóa lịch sử player NGAY LẬP TỨC khi thả chuột.
            # Không cho bất kỳ dữ liệu lúc bay/camera chạy lọt vào tracker.
            self.player_motion_tracker.clear()
            self.player_tracker_locked = True
            self.player_tracker_reset_pending = True

            # Mở lại tracker sau:
            # flight time ước lượng + 1 giây camera ổn định.
            estimated_flight = self.average_flight_time()
            self.camera_settle_until = (
                self.last_mouse_release_time
                + estimated_flight
                + CAMERA_SETTLE_AFTER_LANDING_SECONDS
            )

            print(
                f"[PLAYER TRACKER] Đã xóa và khóa ngay sau release | "
                f"unlock_after={estimated_flight + CAMERA_SETTLE_AFTER_LANDING_SECONDS:.3f}s"
            )

            pressed = False

            actual_hold = (
                time.perf_counter() - started
            )
            self.last_jump_actual_hold = actual_hold
            self.current_elapsed_hold = actual_hold

            print(
                f"[DONE] held={actual_hold:.6f}s | "
                f"final_required={required:.6f}s | "
                f"final_distance={distance:.2f}px"
            )
            print(
                "Sau khi nhân vật đáp, nhấn F để lưu "
                "platform thực tế và sai lệch."
            )

        except Exception as error:
            print(f"[ERROR] Auto jump: {error}")

        finally:
            if pressed:
                try:
                    self.mouse_controller.release(
                        mouse.Button.left
                    )
                except Exception:
                    pass

            self.jump_in_progress = False

    def record_landing_with_f(self) -> None:
        """
        F: lấy vị trí con trỏ hiện tại làm điểm mong muốn và lấy
        vị trí chân nhân vật hiện tại làm điểm thực tế.

        Cách này dùng cùng một frame sau khi camera đã dịch chuyển,
        nên không bị sai do B cũ còn nằm trong hệ tọa độ trước đó.
        """
        if self.last_jump_actual_hold is None:
            print(
                "[ERROR] Chưa có cú nhảy hoàn tất để ghi."
            )
            return

        landing_a = self.get_character_a(
            allow_last_known=True
        )

        if landing_a is None:
            print(
                "[ERROR] Không có vị trí nhân vật hiện tại "
                "hoặc gần nhất."
            )
            return

        cursor_x, cursor_y = self.mouse_controller.position
        expected_point_f = (
            int(cursor_x),
            int(cursor_y),
        )

        actual_x, actual_y = landing_a
        expected_x, expected_y = expected_point_f

        signed_screen_error_x = actual_x - expected_x

        direction = self.last_jump_direction or 1

        # Dương = nhân vật đi quá xa theo hướng nhảy.
        # Âm = nhân vật chưa tới điểm F theo hướng nhảy.
        directional_error_x = (
            signed_screen_error_x * direction
        )

        absolute_error_x = abs(signed_screen_error_x)
        signed_error_y = actual_y - expected_y
        euclidean_error = (
            signed_screen_error_x ** 2
            + signed_error_y ** 2
        ) ** 0.5

        record = {
            "timestamp": datetime.now().isoformat(
                timespec="seconds"
            ),
            "start_a_x": (
                self.last_jump_start_a[0]
                if self.last_jump_start_a
                else -1
            ),
            "start_a_y": (
                self.last_jump_start_a[1]
                if self.last_jump_start_a
                else -1
            ),
            "frozen_launch_a_x": (
                self.last_jump_frozen_launch_a[0]
                if self.last_jump_frozen_launch_a
                else -1
            ),
            "frozen_launch_a_y": (
                self.last_jump_frozen_launch_a[1]
                if self.last_jump_frozen_launch_a
                else -1
            ),
            "initial_target_b_x": (
                self.last_jump_initial_target_b[0]
                if self.last_jump_initial_target_b
                else -1
            ),
            "initial_target_b_y": (
                self.last_jump_initial_target_b[1]
                if self.last_jump_initial_target_b
                else -1
            ),
            "landing_a_x": actual_x,
            "landing_a_y": actual_y,
            "expected_f_x": expected_x,
            "expected_f_y": expected_y,
            "initial_distance_px": float(
                self.last_jump_initial_distance or 0.0
            ),
            "final_dynamic_distance_px": float(
                self.last_jump_final_dynamic_distance or 0.0
            ),
            "actual_hold_seconds": float(
                self.last_jump_actual_hold
            ),
            "final_required_hold_seconds": float(
                self.last_jump_final_required_hold or 0.0
            ),
            "jump_direction": int(direction),
            "signed_screen_error_x_px": float(
                signed_screen_error_x
            ),
            "directional_error_x_px": float(
                directional_error_x
            ),
            "absolute_error_x_px": float(
                absolute_error_x
            ),
            "signed_error_y_px": float(
                signed_error_y
            ),
            "euclidean_error_px": float(
                euclidean_error
            ),
            "target_platform_width": int(
                self.last_jump_target_width or 0
            ),
            "target_offset_x_px": int(
                self.target_offset_x
            ),
            "pre_jump_wait_seconds": float(
                self.last_pre_jump_wait
            ),
            "flight_time_median_seconds": float(
                self.average_flight_time()
            ),
            "motion_xmin": float(
                self.last_jump_motion_xmin
                if self.last_jump_motion_xmin is not None
                else -1.0
            ),
            "motion_xmax": float(
                self.last_jump_motion_xmax
                if self.last_jump_motion_xmax is not None
                else -1.0
            ),
            "motion_mid_x": float(
                self.last_jump_motion_mid_x
                if self.last_jump_motion_mid_x is not None
                else -1.0
            ),
            "landing_target_middle_x": float(
                self.last_jump_landing_target_middle_x
                if self.last_jump_landing_target_middle_x is not None
                else -1.0
            ),
            "motion_period_seconds": float(
                self.last_jump_motion_period
                if self.last_jump_motion_period is not None
                else -1.0
            ),
            "motion_min_to_max_seconds": float(
                self.last_jump_min_to_max_seconds
                if self.last_jump_min_to_max_seconds is not None
                else -1.0
            ),
            "player_motion_xmin": float(
                self.last_jump_player_xmin
                if self.last_jump_player_xmin is not None
                else -1.0
            ),
            "player_motion_xmax": float(
                self.last_jump_player_xmax
                if self.last_jump_player_xmax is not None
                else -1.0
            ),
            "player_motion_period_seconds": float(
                self.last_jump_player_period
                if self.last_jump_player_period is not None
                else -1.0
            ),
            "player_a_used_x": float(
                self.last_jump_player_a_used
                if self.last_jump_player_a_used is not None
                else -1.0
            ),
            "player_motion_range_px": float(
                self.player_motion_tracker.motion_range
                if self.player_motion_tracker.motion_range is not None
                else -1.0
            ),
            "player_xmax_sync_used": int(
                self.player_motion_tracker.has_meaningful_motion
                and not self.is_camera_settling()
            ),
            "camera_settling_at_jump": int(
                self.is_camera_settling()
            ),
            "player_tracker_locked_at_jump": int(
                self.player_tracker_locked
            ),
            "auto_cycle_index": int(
                self.auto_cycle_index
                if self.auto_mode_enabled
                else 0
            ),
            "player_xmax_sync_advance_seconds": float(
                PLAYER_XMAX_SYNC_ADVANCE_SECONDS
            ),
            "auto_mode_type": (
                self.auto_mode_type
                if self.auto_mode_enabled
                else "manual"
            ),
        }

        self.measurement_records.append(record)
        index = len(self.measurement_records)

        print("\n" + "=" * 78)
        print(f"[MEASUREMENT {index}]")
        print(f"A bắt đầu             : {self.last_jump_start_a}")
        print(
            f"B ban đầu             : "
            f"{self.last_jump_initial_target_b}"
        )
        print(f"Nhân vật hiện tại     : {landing_a}")
        print(f"Điểm F mong muốn      : {expected_point_f}")
        print(
            f"Thời gian giữ         : "
            f"{self.last_jump_actual_hold:.6f}s"
        )
        print(
            f"Sai lệch ngang        : "
            f"{signed_screen_error_x:+.2f}px"
        )
        print(
            f"Sai lệch theo hướng   : "
            f"{directional_error_x:+.2f}px"
        )
        print(
            "  Dương = quá xa, âm = chưa tới điểm F"
        )
        print(
            f"Sai lệch dọc          : "
            f"{signed_error_y:+.2f}px"
        )
        print(
            f"Sai lệch Euclid       : "
            f"{euclidean_error:.2f}px"
        )
        print("=" * 78)

    def export_measurements(self) -> None:
        if not self.measurement_records:
            print("[ERROR] Chưa có dữ liệu để xuất.")
            return

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        output_file = (
            JUMP_DATA_DIR
            / f"jump_platform_records_{timestamp}.txt"
        )

        lines = [
            "JUMP PLATFORM CALIBRATION DATA",
            f"created_at={datetime.now().isoformat(timespec='seconds')}",
            f"record_count={len(self.measurement_records)}",
            (
                "current_formula="
                f"{HOLD_TIME_SLOPE:.8f}*distance_px+"
                f"{HOLD_TIME_INTERCEPT:.8f}"
            ),
            f"platform_threshold={self.platform_threshold:.2f}",
            "",
            (
                "index,timestamp,start_a_x,start_a_y,"
                "frozen_launch_a_x,frozen_launch_a_y,"
                "initial_target_b_x,initial_target_b_y,"
                "landing_a_x,landing_a_y,"
                "expected_f_x,expected_f_y,"
                "initial_distance_px,"
                "final_dynamic_distance_px,"
                "actual_hold_seconds,"
                "final_required_hold_seconds,"
                "jump_direction,"
                "signed_screen_error_x_px,"
                "directional_error_x_px,"
                "absolute_error_x_px,"
                "signed_error_y_px,"
                "euclidean_error_px,"
                "target_platform_width,"
                "target_offset_x_px,"
                "pre_jump_wait_seconds,"
                "flight_time_median_seconds,"
                "motion_xmin,motion_xmax,motion_mid_x,"
                "landing_target_middle_x,"
                "motion_period_seconds,"
                "motion_min_to_max_seconds,"
                "player_motion_xmin,player_motion_xmax,"
                "player_motion_period_seconds,"
                "player_a_used_x,"
                "player_motion_range_px,"
                "player_xmax_sync_used,"
                "camera_settling_at_jump,"
                "player_tracker_locked_at_jump,"
                "auto_cycle_index,"
                "player_xmax_sync_advance_seconds,"
                "auto_mode_type"
            ),
        ]

        for index, record in enumerate(
            self.measurement_records,
            start=1,
        ):
            lines.append(
                f"{index},"
                f"{record['timestamp']},"
                f"{record['start_a_x']},"
                f"{record['start_a_y']},"
                f"{record['frozen_launch_a_x']},"
                f"{record['frozen_launch_a_y']},"
                f"{record['initial_target_b_x']},"
                f"{record['initial_target_b_y']},"
                f"{record['landing_a_x']},"
                f"{record['landing_a_y']},"
                f"{record['expected_f_x']},"
                f"{record['expected_f_y']},"
                f"{record['initial_distance_px']:.3f},"
                f"{record['final_dynamic_distance_px']:.3f},"
                f"{record['actual_hold_seconds']:.6f},"
                f"{record['final_required_hold_seconds']:.6f},"
                f"{record['jump_direction']},"
                f"{record['signed_screen_error_x_px']:.3f},"
                f"{record['directional_error_x_px']:.3f},"
                f"{record['absolute_error_x_px']:.3f},"
                f"{record['signed_error_y_px']:.3f},"
                f"{record['euclidean_error_px']:.3f},"
                f"{record['target_platform_width']},"
                f"{record['target_offset_x_px']},"
                f"{record['pre_jump_wait_seconds']:.6f},"
                f"{record['flight_time_median_seconds']:.6f},"
                f"{record['motion_xmin']:.3f},"
                f"{record['motion_xmax']:.3f},"
                f"{record['motion_mid_x']:.3f},"
                f"{record['landing_target_middle_x']:.3f},"
                f"{record['motion_period_seconds']:.6f},"
                f"{record['motion_min_to_max_seconds']:.6f},"
                f"{record['player_motion_xmin']:.3f},"
                f"{record['player_motion_xmax']:.3f},"
                f"{record['player_motion_period_seconds']:.6f},"
                f"{record['player_a_used_x']:.3f},"
                f"{record['player_motion_range_px']:.3f},"
                f"{record['player_xmax_sync_used']},"
                f"{record['camera_settling_at_jump']},"
                f"{record['player_tracker_locked_at_jump']},"
                f"{record['auto_cycle_index']},"
                f"{record['player_xmax_sync_advance_seconds']:.6f},"
                f"{record['auto_mode_type']}"
            )

        lines.extend(
            [
                "",
                "FOCUSED PLATFORM MOTION HISTORY",
                "time_seconds,center_x,width,top_y",
            ]
        )

        if self.motion_tracker.history:
            base_time = self.motion_tracker.history[0][0]
            for timestamp_value, center_x, width, top_y in (
                self.motion_tracker.history
            ):
                lines.append(
                    f"{timestamp_value - base_time:.6f},"
                    f"{center_x:.3f},"
                    f"{width},"
                    f"{top_y}"
                )

        lines.extend(
            [
                "",
                "PLAYER MOTION HISTORY",
                "time_seconds,player_x,player_y",
            ]
        )

        if self.player_motion_tracker.history:
            player_base_time = (
                self.player_motion_tracker.history[0][0]
            )

            for timestamp_value, player_x, player_y in (
                self.player_motion_tracker.history
            ):
                lines.append(
                    f"{timestamp_value - player_base_time:.6f},"
                    f"{player_x:.3f},"
                    f"{player_y:.3f}"
                )

        lines.extend(
            [
                "",
                "FLIGHT TIME SAMPLES",
                "index,flight_seconds",
            ]
        )

        for index, flight_value in enumerate(
            self.flight_time_samples,
            start=1,
        ):
            lines.append(
                f"{index},{flight_value:.6f}"
            )

        output_file.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        print("\n" + "=" * 78)
        print(
            f"[EXPORT] Đã lưu "
            f"{len(self.measurement_records)} bản ghi."
        )
        print(f"File: {output_file}")
        print("=" * 78)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        if not self.overlay_enabled:
            return

        with self.lock:
            character = self.latest_character
            platforms = list(self.latest_platforms)
            search_rect = self.platform_search_rect
            search_rect = self.platform_search_rect
            first_roi_point = self.platform_roi_first_point

        # Vùng giới hạn tìm platform.
        roi_color = QColor(0, 180, 255)

        if search_rect is not None:
            roi_left, roi_top, roi_right, roi_bottom = search_rect

            painter.setPen(QPen(roi_color, 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(
                QRect(
                    roi_left,
                    roi_top,
                    roi_right - roi_left,
                    roi_bottom - roi_top,
                )
            )

            painter.setFont(QFont("Arial", 10, QFont.Bold))
            painter.drawText(
                roi_left,
                max(15, roi_top - 6),
                "PLATFORM SEARCH AREA",
            )

        elif first_roi_point is not None:
            first_x, first_y = first_roi_point
            painter.setPen(QPen(roi_color, 2))
            painter.setBrush(QBrush(roi_color))
            painter.drawEllipse(
                first_x - 5,
                first_y - 5,
                10,
                10,
            )
            painter.drawText(
                first_x + 8,
                first_y - 8,
                "ROI point 1",
            )

        if self.platform_overlay_enabled:
            orange = QColor(255, 145, 0)
            yellow = QColor(255, 235, 0)

            for index, platform in enumerate(platforms, start=1):
                painter.setPen(QPen(orange, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(
                    QRect(
                        platform.left,
                        platform.top,
                        platform.width,
                        platform.height,
                    )
                )

                # Mặt trên platform.
                painter.setPen(QPen(yellow, 3))
                painter.drawLine(
                    platform.safe_left,
                    platform.top,
                    platform.safe_right,
                    platform.top,
                )

                px, py = platform.top_center
                painter.setBrush(QBrush(yellow))
                painter.drawEllipse(px - 4, py - 4, 8, 8)

                painter.setFont(QFont("Arial", 9, QFont.Bold))
                painter.drawText(
                    platform.left,
                    max(14, platform.top - 5),
                    (
                        f"P{index} {platform.width}x{platform.height} "
                        f"T:{platform.total_score:.2f} "
                        f"max:{platform.image_score:.2f} "
                        f"avg:{platform.gradient_score:.2f} "
                        f"cov:{platform.column_coverage:.2f}"
                    ),
                )

        if character is not None:
            green = QColor(0, 255, 0)

            painter.setPen(QPen(green, RECTANGLE_WIDTH))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(
                QRect(
                    character.left,
                    character.top,
                    character.width,
                    character.height,
                )
            )

            ax, ay = character.feet
            painter.setBrush(QBrush(green))
            painter.drawEllipse(
                ax - CENTER_DOT_RADIUS,
                ay - CENTER_DOT_RADIUS,
                CENTER_DOT_RADIUS * 2,
                CENTER_DOT_RADIUS * 2,
            )

            painter.setFont(QFont("Arial", 9, QFont.Bold))
            painter.drawText(
                character.left,
                max(14, character.top - 5),
                (
                    f"A({ax},{ay}) "
                    f"T:{character.total_score:.2f}"
                ),
            )

        # Platform gần con trỏ đang được chú ý.
        if self.focused_platform is not None:
            focus = self.focused_platform
            dark_green = QColor(0, 130, 0)

            painter.setPen(QPen(dark_green, 4))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(
                QRect(
                    focus.left,
                    focus.top,
                    focus.width,
                    focus.height,
                )
            )

            painter.setFont(QFont("Arial", 10, QFont.Bold))
            painter.drawText(
                focus.left,
                max(15, focus.top - 7),
                (
                    "E TARGET"
                    if self.focused_platform_locked
                    else "AUTO FOCUS"
                ),
            )

            fx, fy = focus.top_center
            painter.setBrush(QBrush(dark_green))
            painter.drawEllipse(
                fx - 5,
                fy - 5,
                10,
                10,
            )

            motion_mid = self.motion_tracker.motion_mid_x
            if motion_mid is not None:
                mid_x = int(round(motion_mid))
                painter.setBrush(QBrush(QColor(0, 210, 0)))
                painter.drawEllipse(
                    mid_x - 5,
                    fy - 5,
                    10,
                    10,
                )
                painter.drawText(
                    mid_x + 8,
                    fy - 8,
                    "motion middle",
                )

        # Platform được chọn làm đích nhảy.
        if self.selected_target_platform is not None:
            target = self.selected_target_platform
            magenta = QColor(255, 0, 255)

            painter.setPen(QPen(magenta, 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(
                QRect(
                    target.left,
                    target.top,
                    target.width,
                    target.height,
                )
            )

            bx, by = self.get_adjusted_platform_target(target)
            painter.setBrush(QBrush(magenta))
            painter.drawEllipse(
                bx - 6,
                by - 6,
                12,
                12,
            )
            painter.drawText(
                bx + 9,
                by - 9,
                f"B ({bx},{by})",
            )

        if (
            self.current_jump_distance is not None
            and self.current_required_hold is not None
        ):
            painter.setPen(QPen(QColor(0, 255, 255), 1))
            painter.setFont(QFont("Arial", 10, QFont.Bold))

            jump_text = (
                f"distance={self.current_jump_distance:.1f}px "
                f"required={self.current_required_hold:.4f}s"
            )

            if self.jump_in_progress:
                jump_text += (
                    f" elapsed={self.current_elapsed_hold:.4f}s"
                )

            painter.drawText(18, 50, jump_text)

        painter.setPen(QPen(QColor(180, 255, 180), 1))
        painter.setFont(QFont("Arial", 10, QFont.Bold))

        if self.player_tracker_locked:
            player_mode = "TRACKER_LOCKED"
        elif self.is_camera_settling():
            player_mode = "CURRENT_X_CAMERA_SETTLE"
        else:
            player_mode = (
                "XMAX_SYNC"
                if self.player_motion_tracker.has_meaningful_motion
                else "CURRENT_X"
            )

        motion_text = (
            f"P.xmin={self.motion_tracker.x_min} "
            f"P.xmax={self.motion_tracker.x_max} "
            f"P.mid={self.motion_tracker.motion_mid_x} "
            f"A.xmin={self.player_motion_tracker.x_min} "
            f"A.xmax={self.player_motion_tracker.x_max} "
            f"A.range={self.player_motion_tracker.motion_range} "
            f"A.mode={player_mode} "
            f"sync=-{PLAYER_XMAX_SYNC_ADVANCE_SECONDS:.2f}s "
            f"AUTO={self.auto_status} "
            f"TYPE={self.auto_mode_type} "
            f"tracker={'LOCKED' if self.player_tracker_locked else 'ACTIVE'} "
            f"settle={self.remaining_camera_settle_seconds():.2f}s"
        )
        painter.drawText(18, 72, motion_text)

        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.drawText(
            18,
            28,
            (
                f"FPS={self.actual_fps:.1f} "
                f"scan={self.last_scan_ms:.1f}ms "
                f"platforms={len(platforms)} "
                f"P.threshold={self.platform_threshold:.2f} "
                f"B.offset={self.target_offset_x:+d}px "
                f"TARGET={'LOCKED' if self.focused_platform_locked else 'AUTO'} "
                f"ROI={'ON' if search_rect is not None else 'FULL'} "
                f"mode={self.mode}"
            ),
        )

    def print_status(self) -> None:
        with self.lock:
            character = self.latest_character
            platforms = list(self.latest_platforms)

        if character is None:
            print(
                f"[STATUS] character=None | platforms={len(platforms)} | "
                f"FPS={self.actual_fps:.1f} | scan={self.last_scan_ms:.1f}ms | "
                f"platform_threshold={self.platform_threshold:.2f}"
            )
        else:
            print(
                f"[STATUS] A={character.feet} | "
                f"character={character.total_score:.4f} | "
                f"platforms={len(platforms)} | "
                f"FPS={self.actual_fps:.1f} | scan={self.last_scan_ms:.1f}ms | "
                f"platform_threshold={self.platform_threshold:.2f}"
            )

        if search_rect is None:
            print("  Platform search area: FULL SCREEN")
        else:
            left, top, right, bottom = search_rect
            print(
                f"  Platform search area: "
                f"({left},{top}) -> ({right},{bottom}) "
                f"size={right-left}x{bottom-top}"
            )

        for i, p in enumerate(platforms, start=1):
            print(
                f"  P{i}: box=({p.left},{p.top},{p.width},{p.height}) | "
                f"top_center={p.top_center} | "
                f"total={p.total_score:.3f} | "
                f"max={p.image_score:.3f} | "
                f"avg={p.gradient_score:.3f} | "
                f"coverage={p.column_coverage:.3f}"
            )

    @staticmethod
    def key_to_char(key) -> Optional[str]:
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        return None

    def on_key_press(self, key):
        if key == keyboard.Key.esc:
            self.running = False
            self.auto_cancel_event.set()
            self.auto_mode_enabled = False
            self.auto_mode_pending = False

            try:
                self.mouse_controller.release(
                    mouse.Button.left
                )
            except Exception:
                pass

            QApplication.instance().quit()
            return False

        char = self.key_to_char(key)

        if char in {"b", "e", "r", "f", "g", "w", "t", "y", "o", "p", "u", "i", "[", "]"}:
            if char in self.pressed_hotkeys:
                return None
            self.pressed_hotkeys.add(char)

        if key == keyboard.Key.f8:
            self.overlay_enabled = not self.overlay_enabled
            print(
                "[OVERLAY]",
                "Bật" if self.overlay_enabled else "Tắt",
            )

        elif key == keyboard.Key.f9:
            self.print_status()

        elif key == keyboard.Key.f10:
            self.platform_overlay_enabled = (
                not self.platform_overlay_enabled
            )
            print(
                "[PLATFORM OVERLAY]",
                (
                    "Bật"
                    if self.platform_overlay_enabled
                    else "Tắt"
                ),
            )

        elif char == "b":
            self.select_platform_search_area()

        elif char == "e":
            self.select_focused_platform_with_e()

        elif char == "r":
            self.request_jump()

        elif char == "t":
            self.toggle_auto_mode()

        elif char == "y":
            self.toggle_stationary_auto_mode()

        elif char == "f":
            self.record_landing_with_f()

        elif char == "w":
            self.mark_landing_with_w()

        elif char == "g":
            self.export_measurements()

        elif char == "u":
            self.shift_target_offset(
                -TARGET_OFFSET_STEP_PX
            )

        elif char == "i":
            self.shift_target_offset(
                TARGET_OFFSET_STEP_PX
            )

        elif char == "o":
            self.platform_threshold = max(
                0.00,
                round(
                    self.platform_threshold - 0.02,
                    2,
                ),
            )
            print(
                f"[PLATFORM THRESHOLD] "
                f"{self.platform_threshold:.2f}"
            )

        elif char == "p":
            self.platform_threshold = min(
                1.00,
                round(
                    self.platform_threshold + 0.02,
                    2,
                ),
            )
            print(
                f"[PLATFORM THRESHOLD] "
                f"{self.platform_threshold:.2f}"
            )

        elif char == "[":
            self.character_threshold = max(
                0.10,
                round(
                    self.character_threshold - 0.02,
                    2,
                ),
            )
            print(
                f"[CHARACTER THRESHOLD] "
                f"{self.character_threshold:.2f}"
            )

        elif char == "]":
            self.character_threshold = min(
                0.99,
                round(
                    self.character_threshold + 0.02,
                    2,
                ),
            )
            print(
                f"[CHARACTER THRESHOLD] "
                f"{self.character_threshold:.2f}"
            )

        return None

    def on_key_release(self, key):
        char = self.key_to_char(key)

        if char is not None:
            self.pressed_hotkeys.discard(char)

    def closeEvent(self, event) -> None:
        self.running = False
        self.auto_cancel_event.set()
        self.auto_mode_enabled = False
        self.auto_mode_pending = False
        try:
            self.keyboard_listener.stop()
        except Exception:
            pass
        event.accept()


def main() -> None:
    print("=" * 76)
    print("CHARACTER + PLATFORM DETECTOR CHECK")
    print("=" * 76)
    print(f"Character threshold : {CHARACTER_TOTAL_THRESHOLD:.2f}")
    print(f"Platform template  : {PLATFORM_TEMPLATE_PATH.name}")
    print(f"Strip threshold    : {PLATFORM_MIN_TOTAL_SCORE:.2f}")
    print(f"Strip width        : {PLATFORM_STRIP_WIDTH}")
    print(f"Height scales      : {PLATFORM_HEIGHT_SCALES}")
    print("F8                  : Bật/tắt overlay")
    print("F9                  : In thông số")
    print("F10                 : Bật/tắt platform overlay")
    print("B                    : Chọn 2 góc vùng tìm platform")
    print("E                    : Chọn/đổi platform mục tiêu")
    print("R                    : Nhảy vào platform đã chọn")
    print("U / I                : Dịch B trái/phải 10 px")
    print("F                    : Con trỏ=điểm mong muốn, ghi sai lệch")
    print("W                    : Đánh dấu thời điểm đáp đất")
    print("T                    : Auto thường sau 3s / hủy")
    print("Y                    : Auto chọn platform đứng yên")
    print("G                    : Xuất dữ liệu TXT")
    print("O / P               : Giảm/tăng platform threshold")
    print("[ / ]               : Chỉnh character threshold")
    print("ESC                 : Thoát")
    print("=" * 76)

    app = QApplication(sys.argv)

    try:
        overlay = DetectorOverlay()
        overlay.show()
        sys.exit(app.exec_())
    except Exception as error:
        print(f"\n[FATAL ERROR] {error}")
        input("\nNhấn Enter để đóng...")


if __name__ == "__main__":
    main()
