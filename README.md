# 🔊 Sound Monitor — Passive RLC BPF + Arduino + PyQt5 GUI

서강대학교 실험물리학1 (03분반) 팀 프로젝트

카페·독서실 등에서 소음이 임계값을 초과할 때 서보모터와 LED로 실시간 경고를 주는 장치입니다.
Passive RLC 대역통과필터(BPF)가 특정 주파수 대역 외 잡음을 걸러내고, Arduino UNO R3가 ADC로
디지털 변환 후 PC에 전송하면, PyQt5 GUI가 원형 게이지·히스토리 바로 소음 레벨을 시각화합니다.

## 신호 흐름

```
① 마이크 (MAX4466)
        ↓
② Passive RLC BPF  (f₀ ≈ 1073 Hz)
        ↓
③ Arduino ADC       (전압 → 디지털, SAMPLES=64)
        ↓
④ Python            (P2P·MAX·MIN 계산 → dB 추정)
        ↓
⑤ PyQt5 GUI          (원형 게이지 + 히스토리 바)
        ↓
⑥ 출력               (서보모터 × 2 · LED · 7-segment)
```

## 하드웨어

| 부품명 | 역할 | 핀 연결 | 비고 |
|---|---|---|---|
| Arduino UNO R3 | ADC 변환 + Serial 전송 + 출력 제어 | — | 메인 보드 |
| MAX4466 마이크 모듈 | 소리 → 아날로그 전압 | A0 | 증폭 내장 |
| Passive RLC BPF | 대역통과 필터링 | 마이크 → A0 사이 | R=171Ω, L=22mH, C=1μF |
| SG90 서보모터 × 2 | 소음 단계 시각 경고 | D9, D10 | DEBOUNCE_MS=800ms |
| 적색 LED | 임계값 초과 경고 | 회로 연결 | — |
| 1-digit 7-segment | 현재 상태 숫자 표시 | — | QUIET/MID/LOUD |

### Passive RLC BPF 설계

기존 Op-Amp Active BPF(LM358)에서 Passive RLC BPF로 설계 변경.

| 항목 | 값 |
|---|---|
| 저항 R | 171 Ω |
| 인덕터 L | 22 mH |
| 커패시터 C | 1 μF |
| 공진 주파수 f₀ | 1/(2π√LC) ≈ 1073 Hz |
| -3dB 대역폭 | 451 ~ 1688 Hz |
| 통과 주파수 범위 (회로 설계 기준) | 159 Hz ~ 2610 Hz |

**Active vs Passive 선택 이유**: Op-Amp 방식은 신호 세기를 유지할 수 있지만 부품 수가 많고 별도 전원이
필요합니다. Passive RLC는 신호 감쇠는 있지만 부품이 적고 Arduino 5V 단전원에서 안정적으로 동작하여
최종 채택했습니다.

## 소프트웨어

- **soum_monitor.ino** — 아두이노 측 ADC 샘플링(SAMPLES=64, SRAM 2KB 제약), 서보 디바운스
  상태머신(DEBOUNCE_MS=800ms), 7-segment 출력, `t1,t2` 포맷의 임계값 수신 로직
- **soum_main_v5_final.py** — PyQt5 GUI (Catppuccin Mocha 테마), 총 597줄
  - `SerialReader` (별도 스레드) — Serial 수신을 메인 스레드와 분리해 GUI 프리징 방지
  - `CircularGauge` — `paintEvent` 오버라이딩으로 직접 구현한 원형 게이지
  - `HistoryBar` — 최근 80프레임 `deque(maxlen=80)` 기반 막대그래프, 오래된 데이터는 흐리게 표시
  - `MainWindow` — 30ms(≈33fps) QTimer 기반 갱신, 장소 모드별 임계값 프리셋(카페/독서실/집/테스트)

### 실행

```bash
pip install pyqt5 pyserial numpy matplotlib
python soum_main_v5_final.py            # 실제 아두이노 연결
python soum_main_v5_final.py --test     # 아두이노 없이 테스트 모드 (사인파 + 노이즈 모사)
```

## 주요 설계 결정

- **왜 dB가 아니라 raw P2P 값으로 표시했나요?**
  dB 변환은 V_REF(무음 기준 전압) 보정값에 따라 오차가 크게 발생합니다. raw P2P는 ADC 직접
  측정값이라 변환 오차가 없고, 사용자가 임계값을 직관적으로 조절할 수 있어 이 방식을 채택했습니다.

- **Queue maxsize=5로 설정한 이유?**
  maxsize가 크면 GUI 갱신이 느릴 때 오래된 데이터가 쌓여 실시간성이 떨어집니다. 5로 제한하면
  큐가 가득 찼을 때 새 데이터를 버려 항상 최신 데이터만 표시됩니다.

- **30ms 타이머 주기는 어떻게 결정했나요?**
  30ms ≈ 초당 33프레임. 사람 눈이 자연스럽게 느끼는 최소 갱신 속도(24~30fps) 기준입니다.

## 미해결 항목 (발표 후 기준)

| 항목 | 상태 | 비고 |
|---|---|---|
| V_REF 실제 캘리브레이션 | ⏳ 미완료 | 현재 V_REF=0.00631 (하드코딩), 실측 필요 |
| 서보 디바운스 파인튜닝 | ⏳ 미완료 | DEBOUNCE_MS=800ms, 실환경 테스트 필요 |
| "평균 5s" 라벨 수정 | ⏳ 미완료 | 실제 동작은 ≈3초 (maxlen=100 × 30ms) |

## 참고 링크

- [MAX4466 Datasheet — Analog Devices](https://www.analog.com/en/products/max4466.html)
- [Arduino 공식 레퍼런스](https://www.arduino.cc/reference/en/)
- [PyQt5 공식 문서](https://www.riverbankcomputing.com/static/Docs/PyQt5/)
- [pyserial 공식 문서](https://pyserial.readthedocs.io/)

---
서강대학교 물리학과 신효산 (Hyosan Shin)
