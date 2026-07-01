#include <Servo.h>

const int MIC_PIN = A1; // BPF 출력
const int LED_RED = 4;
const int LED_GREEN = 13;
const int LED_YELLOW = 10;
const int SERVO1_PIN = 9;


int THRESHOLD1 = 100; 
int THRESHOLD2 = 200;
const int SAMPLES = 64;

// 7세그먼트 핀 (A,B,C,D,E,F,G,DP)
const int SEG_A = 2;
const int SEG_B = 3;
const int SEG_C = 5;
const int SEG_D = 6;
const int SEG_E = 7;
const int SEG_F = 8;
const int SEG_G = 11;
const int SEG_DP = 12;

Servo servo1;

unsigned long lastLoopTime = 0;
const unsigned long LOOP_INTERVAL = 1000;

// 공통 캐소드 기준: 1=켜짐, 0=꺼짐 (공통 애노드면 모든 0/1을 반대로 적용)
const byte digitPatterns[10][8] = {
  // A  B  C  D  E  F  G  DP
  {1, 1, 1, 1, 1, 1, 0, 0}, // 0
  {0, 1, 1, 0, 0, 0, 0, 0}, // 1
  {1, 1, 0, 1, 1, 0, 1, 0}, // 2
  {1, 1, 1, 1, 0, 0, 1, 0}, // 3
  {0, 1, 1, 0, 0, 1, 1, 0}, // 4
  {1, 0, 1, 1, 0, 1, 1, 0}, // 5
  {1, 0, 1, 1, 1, 1, 1, 0}, // 6
  {1, 1, 1, 0, 0, 0, 0, 0}, // 7
  {1, 1, 1, 1, 1, 1, 1, 0}, // 8
  {1, 1, 1, 1, 0, 1, 1, 0}  // 9
};

void displayDigit(int d) {
  if (d < 0) d = 0;
  if (d > 9) d = 9;
  digitalWrite(SEG_A, digitPatterns[d][0]);
  digitalWrite(SEG_B, digitPatterns[d][1]);
  digitalWrite(SEG_C, digitPatterns[d][2]);
  digitalWrite(SEG_D, digitPatterns[d][3]);
  digitalWrite(SEG_E, digitPatterns[d][4]);
  digitalWrite(SEG_F, digitPatterns[d][5]);
  digitalWrite(SEG_G, digitPatterns[d][6]);
  digitalWrite(SEG_DP, digitPatterns[d][7]);
}

void setup() {
  Serial.begin(115200); // 통신 속도 115200 확인!
  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  pinMode(SEG_A, OUTPUT);
  pinMode(SEG_B, OUTPUT);
  pinMode(SEG_C, OUTPUT);
  pinMode(SEG_D, OUTPUT);
  pinMode(SEG_E, OUTPUT);
  pinMode(SEG_F, OUTPUT);
  pinMode(SEG_G, OUTPUT);
  pinMode(SEG_DP, OUTPUT);

  servo1.attach(SERVO1_PIN);
  

  servo1.write(0);
  digitalWrite(LED_RED, LOW);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_YELLOW, LOW);
  displayDigit(0);
  delay(100);

  Serial.println("=== BPF 연결 소음 테스트 ===");
  Serial.println("형식: Max값 | 상태");
}

void loop() {
  // --------------------------------------------------
  // [추가됨] 파이썬에서 새 임계값 받아오기
  // --------------------------------------------------
  if (Serial.available() > 0) {
    int newT1 = Serial.parseInt(); 
    int newT2 = Serial.parseInt();

    delay(10);
    while (Serial.available() > 0) {
      Serial.read();
    }

    if (newT1 > 0 && newT2 > 0) {
      THRESHOLD1 = newT1;
      THRESHOLD2 = newT2;
      Serial.print("임계값 변경 완료 -> 경고 1: ");
      Serial.print(THRESHOLD1);
      Serial.print(", 경고2 : ");
      Serial.println(THRESHOLD2);
    }
  }

  // --------------------------------------------------
  // 기존 소리 측정 및 동작 코드
  // --------------------------------------------------
  if (millis() - lastLoopTime >= LOOP_INTERVAL) {
    lastLoopTime = millis();

    int maxVal = 0;
    int minVal = 1023;

    for (int i = 0; i < SAMPLES; i++) {
      int val = analogRead(MIC_PIN);
      if (val > maxVal) maxVal = val;
      if (val < minVal) minVal = val;
      delayMicroseconds(200);
    }

    int peakToPeak = maxVal - minVal;

    Serial.print("P2P: ");
    Serial.print(peakToPeak);
    Serial.print(" | Max: ");
    Serial.print(maxVal);
    Serial.print(" | Min: ");
    Serial.print(minVal);

    if (peakToPeak > THRESHOLD2) { // 이제 업데이트된 THRESHOLD 값과 비교됨!
      digitalWrite(LED_RED, HIGH);
      digitalWrite(LED_GREEN, LOW);
      digitalWrite(LED_YELLOW, LOW);
      servo1.write(90);
      Serial.println(" | 경고!");
    } 
    else if( peakToPeak > THRESHOLD1) {
      digitalWrite(LED_GREEN, LOW);
      digitalWrite(LED_RED, LOW);
      digitalWrite(LED_YELLOW, HIGH);
      servo1.write(0);
      Serial.println(" | 경고!");
    }
    else{
      digitalWrite(LED_GREEN, HIGH);
      digitalWrite(LED_RED, LOW);
      digitalWrite(LED_YELLOW, LOW);
      servo1.write(0);
      Serial.println(" | 조용");
    }
    
    int level = map(peakToPeak, 0, 1023, 0, 9); // 0~9 단계로 압축해서 표시
    displayDigit(level);
  }
}

