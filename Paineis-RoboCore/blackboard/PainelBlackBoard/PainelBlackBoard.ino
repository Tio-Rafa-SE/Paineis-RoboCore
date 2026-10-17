// =====================================================================
//  Projeto: Painel BlackBoard + Radar Ultrassonico - FIRMWARE
//  Placa: RoboCore BlackBoard UNO R3 (ATmega328P, compativel Arduino UNO)
//  IDE: Arduino IDE -> Placa "Arduino Uno"
//  Bibliotecas: Servo (ja vem com a Arduino IDE)
//
//  Recebe comandos de texto pela USB (115200) e devolve leituras.
//  A tela e o painel_blackboard.py, no computador.
//
//  Tio Rafa - @prof.tiorafa
// =====================================================================

#include <Arduino.h>
#include <Servo.h>

// =====================================================================
// 1) MONTAGEM DO RADAR (pode trocar, so atualize aqui)
// =====================================================================
//  HC-SR04  VCC -> 5V   GND -> GND   TRIG -> D7   ECHO -> D8
//  Servo    marrom -> GND   vermelho -> 5V   laranja (sinal) -> D9
//
// ATENCAO: a biblioteca Servo usa o Timer1 do ATmega328P.
// Com o servo ligado, o PWM (analogWrite) dos pinos D9 e D10 para de
// funcionar. Por isso o servo fica no D9 e o D10 vira so digital.
const uint8_t PINO_TRIG = 7;
const uint8_t PINO_ECHO = 8;
const uint8_t PINO_SERVO = 9;

// =====================================================================
// 2) CONFIGURACAO DO PROJETO
// =====================================================================
const unsigned long BAUD = 115200;
const unsigned long INTERVALO_ANALOG_MS = 100;   // envio de A0..A5 e entradas
const unsigned long INTERVALO_MANUAL_MS = 100;   // medicao com servo parado
const unsigned long TIMEOUT_ECO_US = 30000;      // ~5 m; acima disso = sem eco
const uint8_t PRIMEIRO_PINO = 2;                 // D0 e D1 sao a USB
const uint8_t ULTIMO_PINO = 13;

// Pinos com PWM no UNO, menos D9/D10 (Timer1 ocupado pelo Servo)
bool pinoTemPwm(uint8_t p) {
  return p == 3 || p == 5 || p == 6 || p == 11;
}

bool pinoReservado(uint8_t p) {
  return p == PINO_TRIG || p == PINO_ECHO || p == PINO_SERVO;
}

bool pinoLivre(uint8_t p) {
  return p >= PRIMEIRO_PINO && p <= ULTIMO_PINO && !pinoReservado(p);
}

// =====================================================================
// 3) ESTADO
// =====================================================================
enum ModoRadar { PARADO, VARRENDO, MANUAL };

Servo servo;
ModoRadar modo = PARADO;
int anguloAtual = 90;
int anguloMin = 0;
int anguloMax = 180;
int passo = 2;
int direcao = 1;
unsigned long intervaloVarredura = 50;   // tempo do servo chegar + eco sumir

unsigned long tAnalog = 0;
unsigned long tRadar = 0;

char bufferCmd[48];
uint8_t tamCmd = 0;

// =====================================================================
// 4) RESPOSTAS: toda linha comeca com ">" para o painel reconhecer
// =====================================================================
void enviar(const char *tipo, const char *texto) {
  Serial.print('>');
  Serial.print(tipo);
  Serial.print(' ');
  Serial.println(texto);
}

void erro(const char *texto) { enviar("ERR", texto); }

// =====================================================================
// 5) ULTRASSOM
// =====================================================================
// Retorna a distancia em cm ou -1 se nao houve eco (fora de alcance)
int medirDistanciaCm() {
  digitalWrite(PINO_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PINO_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PINO_TRIG, LOW);
  unsigned long duracao = pulseIn(PINO_ECHO, HIGH, TIMEOUT_ECO_US);
  if (duracao == 0) return -1;
  return (int)(duracao / 58UL);   // ida e volta, ~29 us por cm
}

void enviarLeituraRadar() {
  char txt[32];
  snprintf(txt, sizeof(txt), "ang=%d;cm=%d", anguloAtual, medirDistanciaCm());
  enviar("R", txt);
}

void moverServo(int angulo) {
  anguloAtual = constrain(angulo, 0, 180);
  servo.write(anguloAtual);
}

// Um passo da varredura: le no angulo atual e ja manda o servo ao proximo.
// Ida e volta entre anguloMin e anguloMax, sem travar o loop.
void passoVarredura() {
  enviarLeituraRadar();
  int proximo = anguloAtual + direcao * passo;
  if (proximo > anguloMax || proximo < anguloMin) {
    direcao = -direcao;
    proximo = anguloAtual + direcao * passo;
  }
  moverServo(proximo);
}

// =====================================================================
// 6) TELEMETRIA DOS PINOS
// =====================================================================
// A0..A5 (0..1023) e um "mapa de bits" com o nivel de D2..D13
void enviarPinos() {
  unsigned int mapa = 0;
  for (uint8_t p = PRIMEIRO_PINO; p <= ULTIMO_PINO; p++) {
    if (digitalRead(p)) mapa |= (1U << p);
  }
  char txt[72];
  snprintf(txt, sizeof(txt), "a0=%d;a1=%d;a2=%d;a3=%d;a4=%d;a5=%d;d=%u",
           analogRead(A0), analogRead(A1), analogRead(A2),
           analogRead(A3), analogRead(A4), analogRead(A5), mapa);
  enviar("A", txt);
}

void enviarInfo() {
  char txt[64];
  snprintf(txt, sizeof(txt), "trig=%d;echo=%d;servo=%d;pwm=3,5,6,11",
           PINO_TRIG, PINO_ECHO, PINO_SERVO);
  enviar("HW", txt);
}

// =====================================================================
// 7) INTERPRETADOR DE COMANDOS
//   MODE,p,OUT|IN|PUP   DW,p,v   PWM,p,0-255
//   SERVO,a (mira manual)   VARRER,min,max,passo,ms   PARAR
//   DIST   INFO   PING
// =====================================================================
void executar(char *linha) {
  char *arg = strchr(linha, ',');
  if (arg) *arg++ = '\0'; else arg = (char *)"";
  for (char *c = linha; *c; c++) *c = toupper(*c);

  char msg[40];
  int a = 0, b = 0, c = 0, d = 0;

  if (!strcmp(linha, "PING")) {
    enviar("PONG", "ok");

  } else if (!strcmp(linha, "INFO")) {
    enviarInfo();

  } else if (!strcmp(linha, "MODE")) {
    char tipo[5] = "";
    if (sscanf(arg, "%d,%4s", &a, tipo) == 2 && pinoLivre(a)) {
      for (char *t = tipo; *t; t++) *t = toupper(*t);
      if (!strcmp(tipo, "OUT")) { pinMode(a, OUTPUT); digitalWrite(a, LOW); }
      else if (!strcmp(tipo, "PUP")) pinMode(a, INPUT_PULLUP);
      else pinMode(a, INPUT);
      snprintf(msg, sizeof(msg), "%d=%s", a, tipo);
      enviar("MODE", msg);
    } else {
      snprintf(msg, sizeof(msg), "pino %d nao disponivel", a);
      erro(msg);
    }

  } else if (!strcmp(linha, "DW")) {
    if (sscanf(arg, "%d,%d", &a, &b) == 2 && pinoLivre(a)) {
      pinMode(a, OUTPUT);
      digitalWrite(a, b ? HIGH : LOW);
    } else {
      snprintf(msg, sizeof(msg), "pino %d nao disponivel", a);
      erro(msg);
    }

  } else if (!strcmp(linha, "PWM")) {
    if (sscanf(arg, "%d,%d", &a, &b) == 2 && pinoLivre(a) && pinoTemPwm(a)) {
      pinMode(a, OUTPUT);
      analogWrite(a, constrain(b, 0, 255));
    } else {
      snprintf(msg, sizeof(msg), "pino %d sem PWM", a);
      erro(msg);
    }

  } else if (!strcmp(linha, "SERVO")) {
    modo = MANUAL;
    moverServo(atoi(arg));

  } else if (!strcmp(linha, "VARRER")) {
    if (sscanf(arg, "%d,%d,%d,%d", &a, &b, &c, &d) == 4) {
      anguloMin = constrain(min(a, b), 0, 180);
      anguloMax = constrain(max(a, b), 0, 180);
      passo = constrain(c, 1, 30);
      intervaloVarredura = constrain(d, 20, 500);
    }
    direcao = 1;
    moverServo(anguloMin);
    tRadar = millis() + 300;   // tempo para o servo chegar ao inicio
    modo = VARRENDO;
    enviar("INFO", "varredura iniciada");

  } else if (!strcmp(linha, "PARAR")) {
    modo = PARADO;
    enviar("INFO", "radar parado");

  } else if (!strcmp(linha, "DIST")) {
    enviarLeituraRadar();

  } else {
    snprintf(msg, sizeof(msg), "comando desconhecido: %.16s", linha);
    erro(msg);
  }
}

// Le a USB sem travar o loop
void lerComandos() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (tamCmd > 0) {
        bufferCmd[tamCmd] = '\0';
        executar(bufferCmd);
      }
      tamCmd = 0;
    } else if (tamCmd < sizeof(bufferCmd) - 1) {
      bufferCmd[tamCmd++] = ch;
    } else {
      tamCmd = 0;   // linha grande demais: descarta
    }
  }
}

// =====================================================================
// 8) SETUP
// =====================================================================
void setup() {
  Serial.begin(BAUD);

  pinMode(PINO_TRIG, OUTPUT);
  digitalWrite(PINO_TRIG, LOW);
  pinMode(PINO_ECHO, INPUT);

  // Pinos livres comecam como entrada (estado seguro: nada liga sozinho)
  for (uint8_t p = PRIMEIRO_PINO; p <= ULTIMO_PINO; p++) {
    if (!pinoReservado(p)) pinMode(p, INPUT);
  }

  servo.attach(PINO_SERVO);
  moverServo(90);

  enviar("INFO", "Painel BlackBoard pronto");
  enviarInfo();
}

// =====================================================================
// 9) LOOP
// =====================================================================
void loop() {
  lerComandos();

  unsigned long agora = millis();

  if (agora - tAnalog >= INTERVALO_ANALOG_MS) {
    tAnalog = agora;
    enviarPinos();
  }

  if (modo == VARRENDO && (long)(agora - tRadar) >= (long)intervaloVarredura) {
    tRadar = agora;
    passoVarredura();
  } else if (modo == MANUAL && agora - tRadar >= INTERVALO_MANUAL_MS) {
    tRadar = agora;
    enviarLeituraRadar();
  }
}
