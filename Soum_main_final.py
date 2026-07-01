"""
soum_main_v5_final.py — 소음 측정기 GUI 최종본 (raw P2P 표시 통일판)
서강대학교 실험물리학1 03분반 팀 프로젝트

v5 수정 사항:
  - 표시 기준을 dB → raw P2P 값(0~1000)으로 통일 (게이지/히스토리/슬라이더/상태판정 단위 일치)
  - 게이지 채움 비율 버그 수정 (db/1023 → val/DISPLAY_MAX)
  - 경고1/2 슬라이더 범위 0~1000으로 확장
  - dB 히스토리 높이 스케일을 DISPLAY_MAX 기준으로 조정 (값이 위로만 붙던 문제 해결)
  - 장소별 임계값(raw) 재설정: 카페 500/800, 집·독서실 200/500, 게임 10/30
"""

import sys, time, threading, queue, argparse
import numpy as np
from collections import deque

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QComboBox, QFrame
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QPainter, QColor, QPen, QBrush

import matplotlib
matplotlib.use("Qt5Agg")
# Matplotlib 한글 폰트 안 깨지도록 시스템 폰트(맑은 고딕) 강제 지정
matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# ─────────────────────────────────────────────────────────────
#  설정 상수
# ─────────────────────────────────────────────────────────────
PORT        = 'COM3'       # 본인의 아두이노 포트 번호에 맞게 세팅
BAUD        = 115200       # 아두이노 Serial.begin(115200)과 일치
SAMPLES     = 512
SAMPLE_RATE = 8000
DB_HISTORY  = 80

# ADC → dBSPL 변환 상수 (참고용 — 표시는 raw 기준)
ADC_MAX   = 1023.0
V_SUPPLY  = 5.0        # V
V_REF     = 0.00631    # V  (실측 보정 기준 전압)
OFFSET_DB = 94.0       # dBSPL 기준 오프셋

# 표시 및 슬라이더 최대 범위 (raw P2P 기준)
DISPLAY_MAX = 1000.0

# 장소별 (임계값1, 임계값2) 쌍 (raw P2P 단위)
MODE_THRESHOLD = {
    "카페":   (500, 800),
    "독서실": (50, 200),
    "집":     (200, 500),
    "작동 테스트":   (10, 30),
}

# Catppuccin Mocha 컬러 팔레트
M = {
    "base":     "#1e1e2e", "mantle":   "#181825", "crust":    "#11111b",
    "surface0": "#313244", "surface1": "#45475a", "surface2": "#585b70",
    "overlay0": "#6c7086", "overlay1": "#7f849c", "overlay2": "#9399b2",
    "text":     "#cdd6f4", "subtext1": "#bac2de", "subtext0": "#a6adc8",
    "blue":     "#89b4fa", "lavender": "#b4befe", "sapphire": "#74c7ec",
    "sky":      "#89dceb", "teal":     "#94e2d5", "green":    "#a6e3a1",
    "yellow":   "#f9e2af", "peach":    "#fab387", "maroon":   "#eba0ac",
    "red":      "#f38ba8", "mauve":    "#cba6f7", "pink":     "#f5c2e7",
}

def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def mcolor(key, alpha=255):
    r, g, b = hex2rgb(M[key])
    return QColor(r, g, b, alpha)

# ─────────────────────────────────────────────────────────────
#  P2P 값 기반 정밀 dBSPL 데시벨 변환 함수 (참고용)
# ─────────────────────────────────────────────────────────────
def compute_p2p_db(p2p_val) -> float:
    v_p2p = (float(p2p_val) / ADC_MAX) * V_SUPPLY
    v_rms = (v_p2p / 2.0) / np.sqrt(2)  # 정현파 모델 기반 실효 전압 연산
    if v_rms < 1e-10:
        return 0.0
    db = 20.0 * np.log10(v_rms / V_REF) + OFFSET_DB
    return float(np.clip(db, 0.0, 120.0))

# ─────────────────────────────────────────────────────────────
#  Serial 리더 스레드 (오리지널 요약 텍스트 파싱)
# ─────────────────────────────────────────────────────────────
class SerialReader(threading.Thread):
    def __init__(self, port, baud, q):
        super().__init__(daemon=True)
        self.port    = port
        self.baud    = baud
        self.q       = q
        self.running = True
        self._ser    = None
        self._lock   = threading.Lock()

    def run(self):
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)   # 아두이노 리셋 안정화 대기
            while self.running:
                line = self._ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                # 아두이노 시리얼 포맷 분할 파싱
                if "P2P:" in line and "|" in line:
                    try:
                        parts = line.split("|")
                        p2p = int(parts[0].split(":")[1].strip())
                        max_val = int(parts[1].split(":")[1].strip())
                        min_val = int(parts[2].split(":")[1].strip())
                        self.q.put((p2p, max_val, min_val))
                    except (ValueError, IndexError):
                        pass
        except Exception as e:
            print(f"[Serial 오류] {e}")

    def send_threshold(self, t1: float, t2: float):
        """ 사용자가 제어하는 raw 임계값(0~1000)을 아두이노용 정수(0~1023)로 보정해 전송 """
        if self._ser and self._ser.is_open:
            with self._lock:
                try:
                    adc_thresh1 = int(max(0, min(1023, round(t1))))
                    adc_thresh2 = int(max(0, min(1023, round(t2))))
                    send_data = f"{adc_thresh1},{adc_thresh2}"
                    self._ser.write(send_data.encode())
                except Exception as e:
                    print(f"[send_threshold 오류] {e}")

    def stop(self):
        self.running = False
        if self._ser:
            self._ser.close()

# ─────────────────────────────────────────────────────────────
#  테스트 모드 리더
# ─────────────────────────────────────────────────────────────
class FakeReader(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.running = True

    def run(self):
        while self.running:
            p2p = int(450 + 350 * np.sin(time.time() * 0.8) + np.random.randint(-40, 40))
            p2p = max(10, min(1023, p2p))
            max_val = 512 + p2p // 2
            min_val = 512 - p2p // 2
            self.q.put((p2p, max_val, min_val))
            time.sleep(0.1)

    def send_threshold(self, t1: float, t2: float):
        print(f"[테스트] 임계값 전송 → T1={t1:.0f} (raw)")

    def stop(self):
        self.running = False

# ─────────────────────────────────────────────────────────────
#  커스텀 위젯: 원형 게이지 (raw P2P 표시)
# ─────────────────────────────────────────────────────────────
class CircularGauge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.val     = 0.0
        self.thresh1 = 500.0
        self.thresh2 = 800.0
        self.setMinimumSize(280, 280)

    def setValue(self, val, thresh1, thresh2):
        self.val     = val
        self.thresh1 = thresh1
        self.thresh2 = thresh2
        self.update()

    def paintEvent(self, event):
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h   = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r      = min(w, h) // 2 - 35

        pen = QPen(mcolor("surface1"), 14)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(cx - r, cy - r, 2*r, 2*r, 225*16, -270*16)

        if self.val >= self.thresh2:
            arc_color = M["red"]
        elif self.val >= self.thresh1:
            arc_color = M["yellow"]
        else:
            arc_color = M["teal"]

        ratio = min(self.val / DISPLAY_MAX, 1.0)
        span  = int(-270 * 16 * ratio)
        r2, g2, b2 = hex2rgb(arc_color)
        pen2 = QPen(QColor(r2, g2, b2), 14)
        pen2.setCapStyle(Qt.RoundCap)
        painter.setPen(pen2)
        if span != 0:
            painter.drawArc(cx - r, cy - r, 2*r, 2*r, 225*16, span)

        for thresh, col in [(self.thresh1, "yellow"), (self.thresh2, "red")]:
            angle = 225 - 270 * (thresh / DISPLAY_MAX)
            rad   = math.radians(angle)
            mx    = cx + (r - 7) * math.cos(rad)
            my    = cy - (r - 7) * math.sin(rad)
            painter.setPen(QPen(mcolor(col), 2))
            painter.setBrush(QBrush(mcolor(col)))
            painter.drawEllipse(int(mx) - 4, int(my) - 4, 8, 8)

        painter.setPen(QPen(mcolor("text")))
        painter.setFont(QFont("Malgun Gothic", 26, QFont.Bold))
        painter.drawText(cx - 75, cy - 30, 150, 60, Qt.AlignCenter, f"{self.val:.0f}")
        painter.setPen(QPen(mcolor("subtext0")))
        painter.setFont(QFont("Malgun Gothic", 10))
        painter.drawText(cx - 40, cy + 30, 80, 30, Qt.AlignCenter, "P2P")

        painter.setPen(QPen(mcolor("overlay1")))
        painter.setFont(QFont("Malgun Gothic", 9))
        painter.drawText(cx - r - 25, cy + r//2 - 10, 50, 20, Qt.AlignCenter, "0")
        painter.drawText(cx + r - 20, cy + r//2 - 10, 50, 20, Qt.AlignCenter, "1000")

# ─────────────────────────────────────────────────────────────
#  커스텀 위젯: 히스토리 바 그래프 (raw P2P 표시)
# ─────────────────────────────────────────────────────────────
class HistoryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = deque([0.0] * DB_HISTORY, maxlen=DB_HISTORY)
        self.thresh1 = 500.0
        self.thresh2 = 800.0
        self.setMinimumHeight(80)

    def update_data(self, val, thresh1, thresh2):
        self.history.append(val)
        self.thresh1 = thresh1
        self.thresh2 = thresh2
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h  = self.width(), self.height()
        n     = len(self.history)
        bar_w = max(1, (w - 8) / n)

        for thresh, col in [(self.thresh1, "yellow"), (self.thresh2, "red")]:
            ty  = h - 4 - (thresh / DISPLAY_MAX) * (h - 8)
            pen = QPen(mcolor(col, 180), 1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(4, int(ty), w - 4, int(ty))

        for i, val in enumerate(self.history):
            bh        = max(2, (val / DISPLAY_MAX) * (h - 8))
            bx        = 4 + i * bar_w
            by        = h - 4 - bh
            age_alpha = int(80 + 175 * (i / n))

            if val >= self.thresh2:
                r, g, b = hex2rgb(M["red"])
            elif val >= self.thresh1:
                r, g, b = hex2rgb(M["yellow"])
            else:
                r, g, b = hex2rgb(M["teal"])

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(r, g, b, age_alpha)))
            painter.drawRoundedRect(int(bx), int(by), max(1, int(bar_w) - 1), int(bh), 1, 1)

# ─────────────────────────────────────────────────────────────
#  FFT 캔버스
# ─────────────────────────────────────────────────────────────
class FFTCanvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(5, 2.2), facecolor=M["base"])
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self._setup_ax()
        self.freqs = np.fft.rfftfreq(SAMPLES, d=1.0 / SAMPLE_RATE)
        self.line, = self.ax.plot(self.freqs, np.zeros(len(self.freqs)),
                                  color=M["mauve"], linewidth=1.0, alpha=0.9)
        self.fig.tight_layout(pad=0.6)

    def _setup_ax(self):
        ax = self.ax
        ax.set_facecolor(M["surface0"])
        ax.tick_params(colors=M["overlay2"], labelsize=8)
        ax.set_xlabel("주파수 (Hz)", color=M["subtext0"], fontsize=9)
        ax.set_ylabel("크기 (dB)",   color=M["subtext0"], fontsize=9)
        ax.set_xlim(0, SAMPLE_RATE // 2)
        ax.set_ylim(0, 80)
        for spine in ax.spines.values():
            spine.set_edgecolor(M["surface2"])
            spine.set_linewidth(0.5)
        ax.axvspan(159, 2610, alpha=0.06, color=M["teal"])
        ax.axvline(159,  color=M["teal"], linewidth=0.6, linestyle='--', alpha=0.5)
        ax.axvline(2610, color=M["teal"], linewidth=0.6, linestyle='--', alpha=0.5)

    def update_fft(self, raw_data):
        windowed = np.array(raw_data, dtype=float) * np.hanning(SAMPLES)
        mag      = np.abs(np.fft.rfft(windowed))
        mag_db   = 20 * np.log10(mag + 1e-9)
        mag_db  -= mag_db.min()
        mag_db   = np.clip(mag_db, 0, 80)
        self.line.set_ydata(mag_db)
        for coll in self.ax.collections:
            coll.remove()
        self.ax.fill_between(self.freqs, mag_db, color=M["mauve"], alpha=0.12)
        self.ax.axvspan(159, 2610, alpha=0.06, color=M["teal"])
        self.draw()

# ─────────────────────────────────────────────────────────────
#  상태 카드 위젯
# ─────────────────────────────────────────────────────────────
class StatusCard(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(130)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Malgun Gothic", 9))
        lbl_title.setStyleSheet(f"color: {M['subtext0']}; letter-spacing: 1px;")

        self.lbl_value = QLabel("--")
        self.lbl_value.setFont(QFont("Malgun Gothic", 18, QFont.Bold))
        self.lbl_value.setStyleSheet(f"color: {M['text']};")

        layout.addWidget(lbl_title)
        layout.addWidget(self.lbl_value)
        self.setStyleSheet(f"""
            StatusCard {{
                background: {M['surface0']};
                border-radius: 8px;
                border: 1px solid {M['surface1']};
            }}
        """)

    def set_value(self, val, color_key="text"):
        self.lbl_value.setText(str(val))
        self.lbl_value.setStyleSheet(f"color: {M[color_key]};")

# ─────────────────────────────────────────────────────────────
#  메인 윈도우
# ─────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, test_mode=False):
        super().__init__()
        self.setWindowTitle("소음 측정기 v5 (raw P2P 표시 통일 버전)")
        self.resize(1040, 700)
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {M['base']}; color: {M['text']}; font-family: 'Malgun Gothic';
            }}
            QLabel {{ background: transparent; }}
            QComboBox {{
                background: {M['surface0']}; color: {M['text']};
                border: 1px solid {M['surface1']}; border-radius: 6px;
                padding: 6px 14px; font-size: 20px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QSlider::groove:horizontal {{
                background: {M['surface1']}; height: 4px; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {M['blue']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {M['blue']}; border-radius: 2px; }}
        """)

        self.data_queue = queue.Queue(maxsize=5)
        self.thresh1    = 500.0   # raw 기본값 (카페)
        self.thresh2    = 800.0

        # 첫 데이터 들어오는 시점에 바로 싱크 맞추기 위한 전송 플래그 변수
        self.initial_threshold_sent = False

        self._build_ui()

        if test_mode:
            print("[테스트 모드] 가짜 신호 실행 중...")
            self.reader = FakeReader(self.data_queue)
        else:
            self.reader = SerialReader(PORT, BAUD, self.data_queue)
        self.reader.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self._update)
        self.timer.start(30)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(10)

        # 헤더
        header = QHBoxLayout()
        title = QLabel("소음 측정기")
        title.setFont(QFont("Malgun Gothic", 16, QFont.Bold))
        title.setStyleSheet(f"color: {M['mauve']};")

        self.lbl_status = QLabel("대기 중")
        self.lbl_status.setFont(QFont("Malgun Gothic", 12))
        self.lbl_status.setStyleSheet(f"color: {M['subtext0']}; background: {M['surface0']}; border-radius: 6px; padding: 3px 12px;")

        mode_label = QLabel("장소 모드")
        mode_label.setStyleSheet(f"color: {M['subtext0']}; font-size: 20px;")
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(list(MODE_THRESHOLD.keys()))
        self.combo_mode.currentTextChanged.connect(self._on_mode_change)

        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(self.lbl_status)
        header.addStretch()
        header.addWidget(mode_label)
        header.addWidget(self.combo_mode)
        root_layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {M['surface1']}; margin: 0px;")
        root_layout.addWidget(sep)

        # 레이아웃 분할
        content = QHBoxLayout()
        content.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)

        self.gauge = CircularGauge()
        self.gauge.setValue(0, self.thresh1, self.thresh2)
        gauge_wrap = QWidget()
        gauge_wrap.setStyleSheet(f"background: {M['surface0']}; border-radius: 12px;")
        gw_layout = QVBoxLayout(gauge_wrap)
        gw_layout.setContentsMargins(10, 10, 10, 10)
        gw_layout.addWidget(self.gauge, alignment=Qt.AlignCenter)
        left.addWidget(gauge_wrap)

        cards_layout = QGridLayout()
        cards_layout.setSpacing(8)
        self.card_peak   = StatusCard("최대값")
        self.card_avg    = StatusCard("평균 (5s)")
        self.card_thresh = StatusCard("경고1 임계값")
        self.card_state  = StatusCard("상태")
        cards_layout.addWidget(self.card_peak,   0, 0)
        cards_layout.addWidget(self.card_avg,    0, 1)
        cards_layout.addWidget(self.card_thresh, 1, 0)
        cards_layout.addWidget(self.card_state,  1, 1)
        left.addLayout(cards_layout)

        # 슬라이더 영역
        slider_wrap = QWidget()
        slider_wrap.setStyleSheet(f"background: {M['surface0']}; border-radius: 8px;")
        sl_layout = QVBoxLayout(slider_wrap)
        sl_layout.setContentsMargins(14, 10, 14, 10)
        sl_layout.setSpacing(8)

        t1_header = QHBoxLayout()
        t1_label  = QLabel("경고1 임계값")
        t1_label.setStyleSheet(f"color: {M['yellow']}; font-size: 20px; letter-spacing:1px;")
        self.lbl_t1_val = QLabel(f"{int(self.thresh1)}")
        self.lbl_t1_val.setStyleSheet(f"color: {M['yellow']}; font-weight: bold; font-size: 20px;")
        t1_header.addWidget(t1_label)
        t1_header.addStretch()
        t1_header.addWidget(self.lbl_t1_val)

        # 드래그 범위 0 ~ 1000 (raw P2P 기준)
        self.slider_t1 = QSlider(Qt.Horizontal)
        self.slider_t1.setRange(0, 1000)
        self.slider_t1.setValue(int(self.thresh1))
        self.slider_t1.valueChanged.connect(self._on_t1_change)

        t2_header = QHBoxLayout()
        t2_label  = QLabel("경고2 임계값")
        t2_label.setStyleSheet(f"color: {M['red']}; font-size: 20px; letter-spacing:1px;")
        self.lbl_t2_val = QLabel(f"{int(self.thresh2)}")
        self.lbl_t2_val.setStyleSheet(f"color: {M['red']}; font-weight: bold; font-size: 20px;")
        t2_header.addWidget(t2_label)
        t2_header.addStretch()
        t2_header.addWidget(self.lbl_t2_val)

        # 드래그 범위 0 ~ 1000 (raw P2P 기준)
        self.slider_t2 = QSlider(Qt.Horizontal)
        self.slider_t2.setRange(0, 1000)
        self.slider_t2.setValue(int(self.thresh2))
        self.slider_t2.valueChanged.connect(self._on_t2_change)

        sl_layout.addLayout(t1_header)
        sl_layout.addWidget(self.slider_t1)
        sl_layout.addLayout(t2_header)
        sl_layout.addWidget(self.slider_t2)
        left.addWidget(slider_wrap)

        content.addLayout(left, stretch=4)

        # 우측 그래프 라인
        right = QVBoxLayout()
        right.setSpacing(10)

        fft_wrap = QWidget()
        fft_wrap.setStyleSheet(f"background: {M['surface0']}; border-radius: 12px;")
        fw_layout = QVBoxLayout(fft_wrap)
        fw_layout.setContentsMargins(10, 8, 10, 8)
        fft_title = QLabel("FFT 스펙트럼 (P2P 연동 가상 시뮬레이션)")
        fft_title.setStyleSheet(f"color: {M['subtext0']}; font-size: 20px; letter-spacing:1px;")
        self.fft_canvas = FFTCanvas()
        fw_layout.addWidget(fft_title)
        fw_layout.addWidget(self.fft_canvas)
        right.addWidget(fft_wrap, stretch=3)

        hist_wrap = QWidget()
        hist_wrap.setStyleSheet(f"background: {M['surface0']}; border-radius: 12px;")
        hw_layout = QVBoxLayout(hist_wrap)
        hw_layout.setContentsMargins(10, 8, 10, 8)
        hist_title = QLabel("P2P 히스토리")
        hist_title.setStyleSheet(f"color: {M['subtext0']}; font-size: 20px; letter-spacing:1px;")
        self.hist_bar = HistoryBar()
        hw_layout.addWidget(hist_title)
        hw_layout.addWidget(self.hist_bar)
        right.addWidget(hist_wrap, stretch=2)

        bpf_wrap = QWidget()
        bpf_wrap.setStyleSheet(f"background: {M['surface0']}; border-radius: 8px;")
        bw_layout = QHBoxLayout(bpf_wrap)
        bw_layout.setContentsMargins(14, 8, 14, 8)
        bpf_info = QLabel(f"<span style='color:{M['teal']};'>BPF | 159 Hz ~ 2610 Hz ||</span><span style='color:{M['subtext0']};'> || Cutoff_freq | 451~1688Hz  </span>")
        bpf_info.setFont(QFont("Malgun Gothic", 7))
        bw_layout.addWidget(bpf_info)
        right.addWidget(bpf_wrap)

        content.addLayout(right, stretch=5)
        root_layout.addLayout(content)

        statusbar = QHBoxLayout()
        self.lbl_port    = QLabel(f"포트: {PORT}")
        self.lbl_port.setStyleSheet(f"color: {M['overlay1']}; font-size: 20px;")
        statusbar.addWidget(self.lbl_port)
        statusbar.addStretch()
        root_layout.addLayout(statusbar)

        self._avg_buf       = deque(maxlen=100)
        self._peak_val      = 0.0
        self._frame_count   = 0
        self._last_fps_time = time.time()

    def _on_mode_change(self, mode):
        t1, t2 = MODE_THRESHOLD[mode]
        self.thresh1 = float(t1)
        self.thresh2 = float(t2)
        self.slider_t1.blockSignals(True)
        self.slider_t2.blockSignals(True)
        self.slider_t1.setValue(t1)
        self.slider_t2.setValue(t2)
        self.slider_t1.blockSignals(False)
        self.slider_t2.blockSignals(False)
        self.lbl_t1_val.setText(f"{t1}")
        self.lbl_t2_val.setText(f"{t2}")
        self.reader.send_threshold(self.thresh1, self.thresh2)

    def _on_t1_change(self, val):
        self.thresh1 = float(val)
        self.lbl_t1_val.setText(f"{val}")
        self.card_thresh.set_value(f"{val}", "yellow")
        self.reader.send_threshold(self.thresh1, self.thresh2)

    def _on_t2_change(self, val):
        self.thresh2 = float(val)
        self.lbl_t2_val.setText(f"{val}")
        self.reader.send_threshold(self.thresh1, self.thresh2)

    def _update(self):
        if self.data_queue.empty():
            return
        try:
            p2p, max_val, min_val = self.data_queue.get_nowait()
        except queue.Empty:
            return

        # 첫 소음 데이터 블록이 온 시점에 초기 임계값을 아두이노 보드로 자동 동기화
        if not self.initial_threshold_sent:
            self.reader.send_threshold(self.thresh1, self.thresh2)
            self.initial_threshold_sent = True

        val = float(p2p)   # 표시는 raw P2P 기준
        self._avg_buf.append(val)
        self._peak_val   = max(self._peak_val * 0.995, val)
        self._frame_count += 1

        self.gauge.setValue(val, self.thresh1, self.thresh2)
        self.hist_bar.update_data(val, self.thresh1, self.thresh2)

        t_arr = np.linspace(0, SAMPLES / SAMPLE_RATE, SAMPLES)
        fake_raw = (512 + (p2p / 2.2) * np.sin(2 * np.pi * 440 * t_arr) + (p2p / 5.0) * np.random.randn(SAMPLES))
        fake_raw = fake_raw.clip(0, 1023).astype(int).tolist()

        if self._frame_count % 2 == 0:
            self.fft_canvas.update_fft(fake_raw)

        if val >= self.thresh2:
            status, scolor = "경고  LOUD", "red"
        elif val >= self.thresh1:
            status, scolor = "주의  MID",  "yellow"
        else:
            status, scolor = "정상  QUIET", "teal"

        self.lbl_status.setText(status)
        self.lbl_status.setStyleSheet(f"color: {M[scolor]}; background: {M['surface0']}; border-radius: 6px; padding: 3px 12px; font-weight: bold;")

        self.card_peak.set_value(f"{self._peak_val:.0f}", "red" if self._peak_val >= self.thresh2 else "text")
        avg = np.mean(list(self._avg_buf)) if self._avg_buf else 0.0
        self.card_avg.set_value(f"{avg:.0f}")
        self.card_thresh.set_value(f"{int(self.thresh1)}", "yellow")

        state_map = {"경고  LOUD": ("경고", "red"), "주의  MID": ("주의", "yellow"), "정상  QUIET": ("정상", "teal")}
        sv, sc = state_map.get(status, ("--", "text"))
        self.card_state.set_value(sv, sc)

        now = time.time()
        if now - self._last_fps_time >= 1.0:
            self._frame_count   = 0
            self._last_fps_time = now

    def closeEvent(self, event):
        self.timer.stop()
        self.reader.stop()
        event.accept()

if __name__ == "__main__":
    import os
    os.environ["QT_SCALE_FACTOR"] = "1.5"
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="테스트 모드")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow(test_mode=args.test)
    win.show()
    sys.exit(app.exec_())
