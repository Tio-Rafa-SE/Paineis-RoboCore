// =====================================================================
//  Projeto: Painel Wisdom v2.1 - FIRMWARE (Arduino IDE)
//  Placa: RoboCore BlackBoard Wisdom (ESP32 classico, USB-Serial CP2102)
//  IDE: Arduino IDE 2.x + "esp32 by Espressif Systems" (2.x ou 3.x)
//  Placa na IDE: ESP32 Dev Module
//  Biblioteca externa: U8g2 (oliver) - display OLED
//
//  A placa ja liga com a pinagem padrao da Wisdom (PINAGEM_PADRAO).
//  A tela (painel_wisdom.py) pode trocar qualquer pino pela aba "Pinagem",
//  guarda em wisdom_pinos.json e envia para a placa ao conectar.
//  O protocolo e o mesmo da versao MicroPython.
//
//  Tio Rafa - @prof.tiorafa
// =====================================================================

#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>

// =====================================================================
// 1) HARDWARE CONFIRMADO PELA ROBOCORE
// =====================================================================
const int PINO_SDA = 21;
const int PINO_SCL = 22;

// GPIOs usados na descoberta. 1/3 = USB-serial, 6 a 11 = flash: nunca mexer.
const int SAIDAS_CANDIDATAS[] = {0, 2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 23, 25, 26, 27, 32, 33};
const int SO_ENTRADA[] = {34, 35, 36, 39};
const int ADC_CANDIDATOS[] = {32, 33, 34, 35, 36, 39, 0, 2, 4, 12, 13, 14, 15, 25, 26, 27};

const int N_SAIDAS = sizeof(SAIDAS_CANDIDATAS) / sizeof(int);
const int N_SO_ENTRADA = sizeof(SO_ENTRADA) / sizeof(int);
const int N_ADC = sizeof(ADC_CANDIDATOS) / sizeof(int);

const unsigned long BAUD = 115200;
const unsigned long INTERVALO_RAPIDO_MS = 100;
const unsigned long INTERVALO_LENTO_MS = 1500;
const unsigned long INTERVALO_PISCA_MS = 250;

const uint8_t END_HTU21D = 0x40;
const uint8_t END_APDS = 0x39;

// Canais LEDC (so usados no core 2.x). Servo longe do RGB (timers diferentes).
const int CANAL_RGB[3] = {0, 1, 2};
const int CANAL_SERVO = 4;

// =====================================================================
// PINAGEM PADRAO da BlackBoard Wisdom
// Descoberta com o assistente da aba "Pinagem" numa placa real.
// A placa ja liga com ela; a tela pode trocar qualquer pino (comando CFG).
// Servo e rele ficam desligados (-1) ate serem escolhidos na tela.
// =====================================================================
const char *PINAGEM_PADRAO[] = {
  "LED0,13", "LED1,4", "LED2,16", "LED3,17",
  "RGBR,19", "RGBG,23", "RGBB,18",
  "BTN,27", "POT,39", "SOM,36",
};

// =====================================================================
// 2) CONFIGURACAO ATUAL (comando CFG). -1 = nao configurado
// =====================================================================
struct Config {
  int led[4] = {-1, -1, -1, -1};
  int rgb[3] = {-1, -1, -1};
  int btn = -1, pot = -1, som = -1, servo = -1, rele = -1;
  bool ledInv = false;    // LEDs acendem com nivel baixo
  bool rgbInv = false;    // RGB anodo comum
  bool btnLow = true;     // botao liga o pino ao GND
  bool oledSh = false;    // false = SSD1306, true = SH1106
} cfg;

// =====================================================================
// 3) OLED: os dois drivers ficam prontos e a tela escolhe qual usar
// =====================================================================
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oledSsd(U8G2_R0, U8X8_PIN_NONE, PINO_SCL, PINO_SDA);
U8G2_SH1106_128X64_NONAME_F_HW_I2C oledSh(U8G2_R0, U8X8_PIN_NONE, PINO_SCL, PINO_SDA);
U8G2 *oled = nullptr;

// =====================================================================
// 4) ESTADO
// =====================================================================
uint8_t enderecosI2C[16];
int qtdEnderecos = 0;
uint8_t endAcel = 0;

// estado de cada sensor I2C: 0 ausente, 1 ok, 2 falhou
int stOled = 0, stHtu = 0, stAcel = 0, stApds = 0;
char motivoAcel[32] = "", motivoApds[32] = "";

int pinoPiscando = -1;
bool nivelPisca = false;

char bufferCmd[220];
int tamCmd = 0;
unsigned long tRapido = 0, tLento = 0, tPisca = 0;

// =====================================================================
// 5) UTILITARIOS
// =====================================================================
void enviar(const char *tipo, const char *texto) {
  Serial.print('>');
  Serial.print(tipo);
  Serial.print(' ');
  Serial.println(texto);
}

void erro(const char *texto) { enviar("ERR", texto); }

bool contem(const int *lista, int n, int valor) {
  for (int i = 0; i < n; i++)
    if (lista[i] == valor) return true;
  return false;
}

bool soEntrada(int p) { return contem(SO_ENTRADA, N_SO_ENTRADA, p); }

bool gpioPermitido(int p) {
  return contem(SAIDAS_CANDIDATAS, N_SAIDAS, p) || soEntrada(p);
}

void soltar(int pino) {
  if (pino >= 0) pinMode(pino, INPUT);
}

void anexar(char *dst, size_t tam, const char *parte) {
  strncat(dst, parte, tam - strlen(dst) - 1);
}

void anexarNumero(char *dst, size_t tam, const char *nome, bool valido,
                  float valor, int casas, bool primeiro) {
  char parte[32];
  if (valido) snprintf(parte, sizeof(parte), "%s%s=%.*f", primeiro ? "" : ";", nome, casas, valor);
  else snprintf(parte, sizeof(parte), "%s%s=null", primeiro ? "" : ";", nome);
  anexar(dst, tam, parte);
}

// =====================================================================
// 6) PWM - compativel com core 2.x e 3.x, ligando e desligando em tempo real
// =====================================================================
void pwmLigar(int pino, int canal, uint32_t freq, uint8_t bits) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(pino, freq, bits);
#else
  ledcSetup(canal, freq, bits);
  ledcAttachPin(pino, canal);
#endif
}

void pwmDesligar(int pino) {
  if (pino < 0) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcDetach(pino);
#else
  ledcDetachPin(pino);
#endif
  pinMode(pino, INPUT);
}

void pwmEscrever(int pino, int canal, uint32_t duty) {
  if (pino < 0) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(pino, duty);
#else
  ledcWrite(canal, duty);
#endif
}

// =====================================================================
// 7) I2C E SENSORES
// =====================================================================
bool i2cEscreverReg(uint8_t end, uint8_t reg, uint8_t valor) {
  Wire.beginTransmission(end);
  Wire.write(reg);
  Wire.write(valor);
  return Wire.endTransmission() == 0;
}

bool i2cLerRegs(uint8_t end, uint8_t reg, uint8_t *dst, uint8_t n) {
  Wire.beginTransmission(end);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(end, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) dst[i] = Wire.read();
  return true;
}

bool achouEndereco(uint8_t end) {
  for (int i = 0; i < qtdEnderecos; i++)
    if (enderecosI2C[i] == end) return true;
  return false;
}

void escanearI2C() {
  qtdEnderecos = 0;
  for (uint8_t end = 1; end < 127 && qtdEnderecos < 16; end++) {
    Wire.beginTransmission(end);
    if (Wire.endTransmission() == 0) enderecosI2C[qtdEnderecos++] = end;
  }
}

// HTU21D: medicao sem "hold"
bool htuLer(uint8_t comando, uint16_t esperaMs, uint16_t &bruto) {
  Wire.beginTransmission(END_HTU21D);
  Wire.write(comando);
  if (Wire.endTransmission() != 0) return false;
  delay(esperaMs);   // curto e so a cada 1,5 s
  if (Wire.requestFrom(END_HTU21D, (uint8_t)3) != 3) return false;
  uint8_t msb = Wire.read(), lsb = Wire.read();
  Wire.read();
  bruto = ((msb << 8) | lsb) & 0xFFFC;
  return true;
}

bool htuMedir(float &temp, float &umid) {
  uint16_t raw;
  if (!htuLer(0xF3, 55, raw)) return false;
  temp = -46.85f + 175.72f * raw / 65536.0f;
  if (!htuLer(0xF5, 20, raw)) return false;
  umid = -6.0f + 125.0f * raw / 65536.0f;
  return true;
}

// MMA8452Q: confere WHO_AM_I, standby, escala +-2 g, ativo
int acelIniciar(uint8_t end) {
  uint8_t id = 0;
  if (!i2cLerRegs(end, 0x0D, &id, 1)) {
    snprintf(motivoAcel, sizeof(motivoAcel), "sem resposta");
    return 2;
  }
  if (id != 0x2A) {
    snprintf(motivoAcel, sizeof(motivoAcel), "WHO_AM_I=0x%02X", id);
    return 2;
  }
  i2cEscreverReg(end, 0x2A, 0x00);
  i2cEscreverReg(end, 0x0E, 0x00);
  i2cEscreverReg(end, 0x2A, 0x01);
  return 1;
}

bool acelLer(float g[3]) {
  uint8_t d[6];
  if (!i2cLerRegs(endAcel, 0x01, d, 6)) return false;
  for (int i = 0; i < 3; i++) {
    int16_t v = (int16_t)((d[i * 2] << 8) | d[i * 2 + 1]) >> 4;
    g[i] = v / 1024.0f;
  }
  return true;
}

// APDS-9960: tempo de integracao, pulsos de IR e ganhos ajustados
int apdsIniciar() {
  uint8_t id = 0;
  if (!i2cLerRegs(END_APDS, 0x92, &id, 1)) {
    snprintf(motivoApds, sizeof(motivoApds), "sem resposta");
    return 2;
  }
  if (id != 0xAB && id != 0xA8 && id != 0x9C) {
    snprintf(motivoApds, sizeof(motivoApds), "ID=0x%02X", id);
    return 2;
  }
  i2cEscreverReg(END_APDS, 0x80, 0x00);   // desliga para configurar
  i2cEscreverReg(END_APDS, 0x81, 0xDB);   // ATIME ~100 ms
  i2cEscreverReg(END_APDS, 0x8E, 0x87);   // PPULSE 16 us x 8
  i2cEscreverReg(END_APDS, 0x8F, 0x09);   // LED 100 mA, ganhos 4x
  i2cEscreverReg(END_APDS, 0x80, 0x07);   // PON + AEN + PEN
  return 1;
}

bool apdsLer(uint16_t &luz, uint8_t &prox) {
  uint8_t d[2];
  if (!i2cLerRegs(END_APDS, 0x94, d, 2)) return false;
  luz = d[0] | (d[1] << 8);
  return i2cLerRegs(END_APDS, 0x9C, &prox, 1);
}

void oledTelaInicial() {
  if (!oled) return;
  oled->clearBuffer();
  oled->drawStr(25, 14, "PAINEL WISDOM");
  oled->drawHLine(0, 20, 128);
  oled->drawStr(40, 36, "Tio Rafa");
  oled->drawStr(16, 56, "aguardando PC...");
  oled->sendBuffer();
}

void iniciarI2C() {
  escanearI2C();
  uint8_t endOled = achouEndereco(0x3C) ? 0x3C : (achouEndereco(0x3D) ? 0x3D : 0);
  oled = nullptr;
  stOled = 0;
  if (endOled) {
    U8G2 *escolhido = cfg.oledSh ? (U8G2 *)&oledSh : (U8G2 *)&oledSsd;
    escolhido->setI2CAddress(endOled * 2);   // U8g2 usa o endereco deslocado
    if (escolhido->begin()) {
      escolhido->setFont(u8g2_font_6x10_tf);
      oled = escolhido;
      stOled = 1;
    } else {
      stOled = 2;
    }
  }
  stHtu = achouEndereco(END_HTU21D) ? 1 : 0;

  stAcel = 0;
  endAcel = achouEndereco(0x1D) ? 0x1D : (achouEndereco(0x1C) ? 0x1C : 0);
  if (endAcel) stAcel = acelIniciar(endAcel);

  stApds = achouEndereco(END_APDS) ? apdsIniciar() : 0;
}

const char *textoEstado(int st, const char *motivo, char *buf, size_t tam) {
  if (st == 1) return "ok";
  if (st == 0) return "ausente";
  snprintf(buf, tam, "falhou: %s", motivo);
  return buf;
}

void descreverHardware() {
  char txt[120] = "";
  for (int i = 0; i < qtdEnderecos; i++) {
    char h[8];
    snprintf(h, sizeof(h), "%s0x%02X", i ? "," : "", enderecosI2C[i]);
    anexar(txt, sizeof(txt), h);
  }
  enviar("I2C", qtdEnderecos ? txt : "nenhum");

  char b1[48], b2[48], sens[200];
  snprintf(sens, sizeof(sens), "OLED=%s;HTU21D=%s;MMA8452Q=%s;APDS9960=%s",
           stOled == 1 ? "ok" : (stOled == 0 ? "ausente" : "falhou: begin"),
           stHtu ? "ok" : "ausente",
           textoEstado(stAcel, motivoAcel, b1, sizeof(b1)),
           textoEstado(stApds, motivoApds, b2, sizeof(b2)));
  enviar("SENS", sens);
}

// =====================================================================
// 8) ATUADORES
// =====================================================================
int nivelLed(bool ligado) { return (ligado != cfg.ledInv) ? HIGH : LOW; }

void definirRgb(int r, int g, int b) {
  int v[3] = {constrain(r, 0, 255), constrain(g, 0, 255), constrain(b, 0, 255)};
  for (int i = 0; i < 3; i++)
    pwmEscrever(cfg.rgb[i], CANAL_RGB[i], cfg.rgbInv ? 255 - v[i] : v[i]);
}

void definirServo(int angulo) {
  if (cfg.servo < 0) { erro("servo sem pino"); return; }
  angulo = constrain(angulo, 0, 180);
  float pulsoMs = 0.5f + angulo / 90.0f;            // 0,5 a 2,5 ms
  pwmEscrever(cfg.servo, CANAL_SERVO, (uint32_t)(65535.0f * pulsoMs / 20.0f));
}

void oledTexto(const char *texto) {
  if (!oled) { erro("OLED nao encontrado"); return; }
  oled->clearBuffer();
  char linha[22];
  int lin = 0, col = 0;
  for (const char *p = texto; ; p++) {
    bool fim = (*p == '|' || *p == '\0' || col == 21);
    if (fim) {
      linha[col] = '\0';
      if (lin < 6) oled->drawStr(0, 10 + lin * 10, linha);
      lin++;
      col = 0;
      if (*p == '\0') break;
      if (*p != '|') linha[col++] = *p;
    } else {
      linha[col++] = *p;
    }
  }
  oled->sendBuffer();
}

int lerAdc16(int pino) {
  return min(65535, analogRead(pino) * 16);   // 12 bits -> escala 0..65535
}

int picoAPico(int pino, int amostras) {
  int mn = 4095, mx = 0;
  for (int i = 0; i < amostras; i++) {
    int v = analogRead(pino);
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  return (mx - mn) * 16;
}

int botaoApertado() {
  int v = digitalRead(cfg.btn);
  return cfg.btnLow ? (v == LOW) : (v == HIGH);
}

void configurarBotao() {
  if (cfg.btn < 0) return;
  if (soEntrada(cfg.btn)) pinMode(cfg.btn, INPUT);   // 34-39 sem resistor interno
  else pinMode(cfg.btn, cfg.btnLow ? INPUT_PULLUP : INPUT_PULLDOWN);
}

// =====================================================================
// 9) DESCOBERTA DE PINAGEM
// =====================================================================
bool pinoConfigurado(int p) {
  for (int i = 0; i < 4; i++) if (cfg.led[i] == p) return true;
  for (int i = 0; i < 3; i++) if (cfg.rgb[i] == p) return true;
  return p == cfg.btn || p == cfg.pot || p == cfg.som || p == cfg.servo || p == cfg.rele;
}

void todosAdc(bool picoAPicoModo) {
  char txt[260] = "";
  for (int i = 0; i < N_ADC; i++) {
    int p = ADC_CANDIDATOS[i];
    if (p == pinoPiscando) continue;
    char parte[16];
    int v = picoAPicoModo ? picoAPico(p, 30) : lerAdc16(p);
    snprintf(parte, sizeof(parte), "%s%d=%d", txt[0] ? ";" : "", p, v);
    anexar(txt, sizeof(txt), parte);
  }
  enviar(picoAPicoModo ? "ADCPP" : "ADCS", txt);
}

void todasEntradas() {
  char txt[200] = "";
  for (int i = 0; i < N_SAIDAS + N_SO_ENTRADA; i++) {
    int p = i < N_SAIDAS ? SAIDAS_CANDIDATAS[i] : SO_ENTRADA[i - N_SAIDAS];
    if (p == pinoPiscando) continue;
    pinMode(p, soEntrada(p) ? INPUT : INPUT_PULLUP);
    char parte[10];
    snprintf(parte, sizeof(parte), "%s%d=%d", txt[0] ? ";" : "", p, digitalRead(p));
    anexar(txt, sizeof(txt), parte);
  }
  enviar("IN", txt);
}

// Procura outros barramentos I2C: linhas com resistor de pull-up externo
void buscarI2C() {
  int cand[20];
  int n = 0;
  char lista[80] = "";
  for (int i = 0; i < N_SAIDAS; i++) {
    int p = SAIDAS_CANDIDATAS[i];
    if (p == 0 || p == pinoPiscando || pinoConfigurado(p)) continue;
    pinMode(p, INPUT_PULLDOWN);
    delay(2);
    if (digitalRead(p) == HIGH) {   // alto mesmo puxando para baixo
      cand[n++] = p;
      char parte[6];
      snprintf(parte, sizeof(parte), "%s%d", lista[0] ? "," : "", p);
      anexar(lista, sizeof(lista), parte);
    }
    pinMode(p, INPUT);
  }
  char msg[120];
  snprintf(msg, sizeof(msg), "linhas com pull-up externo: %s", n ? lista : "nenhuma");
  enviar("INFO", msg);

  int achou = 0;
  for (int a = 0; a < n; a++) {
    for (int b = 0; b < n; b++) {
      if (a == b) continue;
      Wire1.begin(cand[a], cand[b], 100000);
      Wire1.setTimeOut(5);
      char ends[100] = "";
      int qtd = 0;
      for (uint8_t end = 0x08; end < 0x78; end++) {
        Wire1.beginTransmission(end);
        if (Wire1.endTransmission() == 0) {
          char h[8];
          snprintf(h, sizeof(h), "%s0x%02X", qtd ? "," : "", end);
          anexar(ends, sizeof(ends), h);
          qtd++;
        }
      }
      Wire1.end();
      pinMode(cand[a], INPUT);
      pinMode(cand[b], INPUT);
      if (qtd > 0 && qtd < 20) {
        achou++;
        char bus[160];
        snprintf(bus, sizeof(bus), "sda=%d;scl=%d;end=%s", cand[a], cand[b], ends);
        enviar("I2CBUS", bus);
      }
    }
  }
  snprintf(msg, sizeof(msg), "busca I2C concluida: %d barramento(s) extra", achou);
  enviar("INFO", msg);
}

// =====================================================================
// 10) CFG: aplica so o recurso que mudou
// =====================================================================
int *campoPino(const char *chave) {
  if (!strncmp(chave, "LED", 3) && chave[3] >= '0' && chave[3] <= '3' && !chave[4])
    return &cfg.led[chave[3] - '0'];
  if (!strcmp(chave, "RGBR")) return &cfg.rgb[0];
  if (!strcmp(chave, "RGBG")) return &cfg.rgb[1];
  if (!strcmp(chave, "RGBB")) return &cfg.rgb[2];
  if (!strcmp(chave, "BTN")) return &cfg.btn;
  if (!strcmp(chave, "POT")) return &cfg.pot;
  if (!strcmp(chave, "SOM")) return &cfg.som;
  if (!strcmp(chave, "SERVO")) return &cfg.servo;
  if (!strcmp(chave, "RELE")) return &cfg.rele;
  return nullptr;
}

void comandoCfg(char *arg) {
  char *virg = strchr(arg, ',');
  if (!virg) { erro("CFG formato: CFG,chave,valor"); return; }
  *virg = '\0';
  char *chave = arg;
  for (char *c = chave; *c; c++) *c = toupper(*c);
  int valor = atoi(virg + 1);
  char msg[64];

  bool *opcao = nullptr;
  if (!strcmp(chave, "LEDINV")) opcao = &cfg.ledInv;
  else if (!strcmp(chave, "RGBINV")) opcao = &cfg.rgbInv;
  else if (!strcmp(chave, "BTNLOW")) opcao = &cfg.btnLow;
  else if (!strcmp(chave, "OLEDSH")) opcao = &cfg.oledSh;

  if (opcao) {
    *opcao = valor != 0;
    if (opcao == &cfg.ledInv) {
      for (int i = 0; i < 4; i++) if (cfg.led[i] >= 0) digitalWrite(cfg.led[i], nivelLed(false));
    } else if (opcao == &cfg.rgbInv) {
      definirRgb(0, 0, 0);
    } else if (opcao == &cfg.btnLow) {
      configurarBotao();
    } else {
      iniciarI2C();
      oledTelaInicial();
    }
    snprintf(msg, sizeof(msg), "%s=%d", chave, valor != 0);
    enviar("CFG", msg);
    return;
  }

  int *campo = campoPino(chave);
  if (!campo) {
    snprintf(msg, sizeof(msg), "CFG desconhecido: %.20s", chave);
    erro(msg);
    return;
  }
  if (valor >= 0 && !gpioPermitido(valor)) {
    snprintf(msg, sizeof(msg), "GPIO %d nao permitido para %s", valor, chave);
    erro(msg);
    return;
  }
  bool ehRgb = campo >= &cfg.rgb[0] && campo <= &cfg.rgb[2];
  bool ehSaida = (campo >= &cfg.led[0] && campo <= &cfg.led[3]) || ehRgb ||
                 campo == &cfg.servo || campo == &cfg.rele;
  if (ehSaida && valor >= 0 && soEntrada(valor)) {
    snprintf(msg, sizeof(msg), "GPIO %d so funciona como entrada", valor);
    erro(msg);
    return;
  }

  // desliga o pino antigo
  int antigo = *campo;
  if (antigo >= 0 && antigo != valor) {
    if (ehRgb || campo == &cfg.servo) pwmDesligar(antigo);
    else soltar(antigo);
  }
  *campo = valor;

  // liga o novo
  if (valor >= 0) {
    if (ehRgb) {
      int i = campo - &cfg.rgb[0];
      pwmLigar(valor, CANAL_RGB[i], 5000, 8);
      pwmEscrever(valor, CANAL_RGB[i], cfg.rgbInv ? 255 : 0);
    } else if (campo == &cfg.servo) {
      pwmLigar(valor, CANAL_SERVO, 50, 16);
    } else if (campo == &cfg.rele) {
      pinMode(valor, OUTPUT);
      digitalWrite(valor, LOW);
    } else if (campo == &cfg.btn) {
      configurarBotao();
    } else if (campo == &cfg.pot || campo == &cfg.som) {
      pinMode(valor, INPUT);
    } else {   // LED do bargraph
      pinMode(valor, OUTPUT);
      digitalWrite(valor, nivelLed(false));
    }
  }
  snprintf(msg, sizeof(msg), "%s=%d", chave, valor);
  enviar("CFG", msg);
}

// =====================================================================
// 11) INTERPRETADOR DE COMANDOS
// =====================================================================
void executar(char *linha) {
  char *arg = strchr(linha, ',');
  if (arg) *arg++ = '\0'; else arg = (char *)"";
  for (char *c = linha; *c; c++) *c = toupper(*c);

  char msg[64];
  int a, b, c;

  if (!strcmp(linha, "PING")) {
    enviar("PONG", "ok");
  } else if (!strcmp(linha, "SCAN")) {
    iniciarI2C();
    oledTelaInicial();
    descreverHardware();
  } else if (!strcmp(linha, "CFG")) {
    comandoCfg(arg);
  } else if (!strcmp(linha, "LED")) {
    if (sscanf(arg, "%d,%d", &a, &b) == 2 && a >= 0 && a < 4 && cfg.led[a] >= 0) {
      digitalWrite(cfg.led[a], nivelLed(b));
    } else {
      snprintf(msg, sizeof(msg), "LED %.10s sem pino", arg);
      erro(msg);
    }
  } else if (!strcmp(linha, "RGB")) {
    if (sscanf(arg, "%d,%d,%d", &a, &b, &c) == 3) definirRgb(a, b, c);
    else erro("RGB formato: RGB,r,g,b");
  } else if (!strcmp(linha, "OLED")) {
    oledTexto(arg);
  } else if (!strcmp(linha, "OLEDCLR")) {
    if (oled) { oled->clearBuffer(); oled->sendBuffer(); }
  } else if (!strcmp(linha, "SERVO")) {
    definirServo(atoi(arg));
  } else if (!strcmp(linha, "RELE")) {
    if (cfg.rele < 0) erro("rele sem pino");
    else digitalWrite(cfg.rele, atoi(arg) ? HIGH : LOW);
  } else if (!strcmp(linha, "GPIO")) {
    if (sscanf(arg, "%d,%d", &a, &b) == 2 && contem(SAIDAS_CANDIDATAS, N_SAIDAS, a)) {
      pinMode(a, OUTPUT);
      digitalWrite(a, b ? HIGH : LOW);
      snprintf(msg, sizeof(msg), "%d=%d", a, b ? 1 : 0);
      enviar("GPIO", msg);
    } else {
      snprintf(msg, sizeof(msg), "GPIO %.10s nao pode ser saida", arg);
      erro(msg);
    }
  } else if (!strcmp(linha, "PISCAR")) {
    a = atoi(arg);
    soltar(pinoPiscando);
    pinoPiscando = -1;
    if (a >= 0) {
      if (contem(SAIDAS_CANDIDATAS, N_SAIDAS, a)) pinoPiscando = a;
      else { snprintf(msg, sizeof(msg), "GPIO %d nao pode piscar", a); erro(msg); }
    }
    snprintf(msg, sizeof(msg), "%d", pinoPiscando);
    enviar("PISCAR", msg);
  } else if (!strcmp(linha, "ADCALL")) {
    todosAdc(false);
  } else if (!strcmp(linha, "ADCPP")) {
    todosAdc(true);
  } else if (!strcmp(linha, "INALL")) {
    todasEntradas();
  } else if (!strcmp(linha, "I2CHUNT")) {
    buscarI2C();
  } else {
    snprintf(msg, sizeof(msg), "comando desconhecido: %.20s", linha);
    erro(msg);
  }
}

void lerComandos() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (tamCmd > 0) {
        bufferCmd[tamCmd] = '\0';
        executar(bufferCmd);
      }
      tamCmd = 0;
    } else if (tamCmd < (int)sizeof(bufferCmd) - 1) {
      bufferCmd[tamCmd++] = ch;
    } else {
      tamCmd = 0;
    }
  }
}

// =====================================================================
// 12) TELEMETRIA
// =====================================================================
void enviarRapido() {
  char t[96] = "";
  anexarNumero(t, sizeof(t), "pot", cfg.pot >= 0, cfg.pot >= 0 ? lerAdc16(cfg.pot) : 0, 0, true);
  anexarNumero(t, sizeof(t), "btn", cfg.btn >= 0, cfg.btn >= 0 ? botaoApertado() : 0, 0, false);
  anexarNumero(t, sizeof(t), "som", cfg.som >= 0, cfg.som >= 0 ? picoAPico(cfg.som, 40) : 0, 0, false);
  enviar("F", t);
}

void enviarLento() {
  float temp = 0, umid = 0, g[3] = {0, 0, 0};
  uint16_t luz = 0;
  uint8_t prox = 0;
  bool okHtu = stHtu == 1 && htuMedir(temp, umid);
  bool okApds = stApds == 1 && apdsLer(luz, prox);
  bool okAcel = stAcel == 1 && acelLer(g);

  char t[160] = "";
  anexarNumero(t, sizeof(t), "t", okHtu, temp, 1, true);
  anexarNumero(t, sizeof(t), "h", okHtu, umid, 1, false);
  anexarNumero(t, sizeof(t), "luz", okApds, luz, 0, false);
  anexarNumero(t, sizeof(t), "prox", okApds, prox, 0, false);
  anexarNumero(t, sizeof(t), "ax", okAcel, g[0], 2, false);
  anexarNumero(t, sizeof(t), "ay", okAcel, g[1], 2, false);
  anexarNumero(t, sizeof(t), "az", okAcel, g[2], 2, false);
  enviar("S", t);
}

// =====================================================================
// 13) SETUP E LOOP
// =====================================================================
void setup() {
  Serial.begin(BAUD);
  analogReadResolution(12);

  Wire.begin(PINO_SDA, PINO_SCL);
  Wire.setClock(400000);
  iniciarI2C();
  for (const char *item : PINAGEM_PADRAO) {
    char tmp[16];
    strncpy(tmp, item, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    comandoCfg(tmp);   // mesmo caminho do comando CFG vindo da tela
  }
  oledTelaInicial();

  enviar("INFO", "Painel Wisdom v2.1 pronto (Arduino)");
  descreverHardware();
}

void loop() {
  lerComandos();
  unsigned long agora = millis();

  if (pinoPiscando >= 0 && agora - tPisca >= INTERVALO_PISCA_MS) {
    tPisca = agora;
    nivelPisca = !nivelPisca;
    pinMode(pinoPiscando, OUTPUT);
    digitalWrite(pinoPiscando, nivelPisca ? HIGH : LOW);
  }
  if (agora - tRapido >= INTERVALO_RAPIDO_MS) {
    tRapido = agora;
    enviarRapido();
  }
  if (agora - tLento >= INTERVALO_LENTO_MS) {
    tLento = agora;
    enviarLento();
  }
}
