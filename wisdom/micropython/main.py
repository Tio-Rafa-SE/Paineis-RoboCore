# =====================================================================
#  Painel Wisdom v2.1 - FIRMWARE (MicroPython)
#  Placa: RoboCore BlackBoard Wisdom (ESP32 classico, USB-Serial CP2102)
#  Ambiente: Thonny -> interpretador "MicroPython (ESP32)"
#  Salvar NA PLACA com o nome  main.py
#
#  A placa ja liga com a pinagem padrao da Wisdom (PINAGEM_PADRAO).
#  A tela (painel_wisdom.py) pode trocar qualquer pino pela aba "Pinagem",
#  guarda em wisdom_pinos.json e envia para a placa ao conectar.
#
#  Tio Rafa - @prof.tiorafa
# =====================================================================

import sys
import time
import select
import framebuf
from machine import Pin, PWM, ADC, I2C, SoftI2C

# ---------------------------------------------------------------------
# Hardware confirmado pela RoboCore
# ---------------------------------------------------------------------
PINO_SDA = 21
PINO_SCL = 22

# GPIOs do ESP32 classico usados na descoberta de pinagem.
# 1/3 = USB-serial, 6 a 11 = memoria flash: nunca mexer.
SAIDAS_CANDIDATAS = (0, 2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 23, 25, 26, 27, 32, 33)
ENTRADAS_CANDIDATAS = SAIDAS_CANDIDATAS + (34, 35, 36, 39)
ADC_CANDIDATOS = (32, 33, 34, 35, 36, 39, 0, 2, 4, 12, 13, 14, 15, 25, 26, 27)
SO_ENTRADA = (34, 35, 36, 39)

INTERVALO_RAPIDO_MS = 100
INTERVALO_LENTO_MS = 1500
INTERVALO_PISCA_MS = 250

# ---------------------------------------------------------------------
# PINAGEM PADRAO da BlackBoard Wisdom
# Descoberta com o assistente da aba "Pinagem" numa placa real.
# A placa ja liga com ela; a tela pode trocar qualquer pino (comando CFG).
# Servo e rele ficam desligados (-1) ate serem escolhidos na tela.
# ---------------------------------------------------------------------
PINAGEM_PADRAO = (
    ("LED0", 13), ("LED1", 4), ("LED2", 16), ("LED3", 17),
    ("RGBR", 19), ("RGBG", 23), ("RGBB", 18),
    ("BTN", 27), ("POT", 39), ("SOM", 36),
)

# ---------------------------------------------------------------------
# Configuracao atual (comando CFG). -1 = nao configurado.
# ---------------------------------------------------------------------
cfg = {
    "LED0": -1, "LED1": -1, "LED2": -1, "LED3": -1,
    "RGBR": -1, "RGBG": -1, "RGBB": -1,
    "BTN": -1, "POT": -1, "SOM": -1, "SERVO": -1, "RELE": -1,
    "LEDINV": 0,    # 1 = LEDs acendem com nivel baixo
    "RGBINV": 0,    # 1 = RGB anodo comum
    "BTNLOW": 1,    # 1 = botao liga o pino ao GND
    "OLEDSH": 0,    # 0 = SSD1306, 1 = SH1106
}


def enviar(tipo, texto):
    sys.stdout.write(">" + tipo + " " + texto + "\n")


def erro(texto):
    enviar("ERR", texto)


# =====================================================================
#  Display OLED (SSD1306 ou SH1106, embutido)
# =====================================================================
class OLED(framebuf.FrameBuffer):
    def __init__(self, i2c, endereco, sh1106):
        self.i2c = i2c
        self.end = endereco
        self.sh1106 = sh1106
        self.buf = bytearray(1024)          # 128 x 64 / 8
        super().__init__(self.buf, 128, 64, framebuf.MONO_VLSB)
        for c in (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
                  0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
                  0xDB, 0x40, 0xA4, 0xA6, 0xAF):
            self._cmd(c)
        self.fill(0)
        self.show()

    def _cmd(self, c):
        self.i2c.writeto(self.end, bytes((0x80, c)))

    def show(self):
        if self.sh1106:
            for p in range(8):              # SH1106: pagina a pagina, coluna 2
                self._cmd(0xB0 + p)
                self._cmd(0x02)
                self._cmd(0x10)
                self.i2c.writeto(self.end, b"\x40" + self.buf[p * 128:(p + 1) * 128])
        else:
            for c in (0x21, 0, 127, 0x22, 0, 7):
                self._cmd(c)
            self.i2c.writeto(self.end, b"\x40" + self.buf)


# =====================================================================
#  Sensores I2C
# =====================================================================
class HTU21D:
    END = 0x40

    def __init__(self, i2c):
        self.i2c = i2c

    def _ler(self, comando, espera_ms):
        self.i2c.writeto(self.END, bytes((comando,)))
        time.sleep_ms(espera_ms)
        d = self.i2c.readfrom(self.END, 3)
        return ((d[0] << 8) | d[1]) & 0xFFFC

    def medir(self):
        t = -46.85 + 175.72 * self._ler(0xF3, 55) / 65536
        h = -6 + 125 * self._ler(0xF5, 20) / 65536
        return t, h


class MMA8452Q:
    def __init__(self, i2c, endereco):
        self.i2c = i2c
        self.end = endereco
        quem = i2c.readfrom_mem(endereco, 0x0D, 1)[0]
        if quem != 0x2A:
            raise OSError("WHO_AM_I=0x%02X (esperado 0x2A)" % quem)
        i2c.writeto_mem(endereco, 0x2A, b"\x00")   # standby para configurar
        i2c.writeto_mem(endereco, 0x0E, b"\x00")   # escala +-2 g
        i2c.writeto_mem(endereco, 0x2A, b"\x01")   # ativo

    def ler_g(self):
        d = self.i2c.readfrom_mem(self.end, 0x01, 6)
        eixos = []
        for i in range(3):
            v = ((d[i * 2] << 8) | d[i * 2 + 1]) >> 4
            if v > 2047:
                v -= 4096
            eixos.append(v / 1024)
        return eixos


class APDS9960:
    END = 0x39

    def __init__(self, i2c):
        self.i2c = i2c
        quem = i2c.readfrom_mem(self.END, 0x92, 1)[0]
        if quem not in (0xAB, 0xA8, 0x9C):
            raise OSError("ID=0x%02X (esperado 0xAB)" % quem)
        w = lambda reg, val: i2c.writeto_mem(self.END, reg, bytes((val,)))
        w(0x80, 0x00)    # desliga para configurar
        w(0x81, 0xDB)    # ATIME: ~100 ms de integracao da luz
        w(0x8E, 0x87)    # PPULSE: 16 us, 8 pulsos de IR
        w(0x8F, 0x09)    # CONTROL: LED 100 mA, ganho prox 4x, ganho luz 4x
        w(0x80, 0x07)    # liga: PON + AEN (luz) + PEN (proximidade)

    def luz(self):
        d = self.i2c.readfrom_mem(self.END, 0x94, 2)
        return d[0] | (d[1] << 8)

    def proximidade(self):
        return self.i2c.readfrom_mem(self.END, 0x9C, 1)[0]


NOMES_I2C = {0x3C: "OLED", 0x3D: "OLED", 0x40: "HTU21D", 0x1C: "MMA8452Q",
             0x1D: "MMA8452Q", 0x39: "APDS9960"}

# =====================================================================
#  Estado do hardware
# =====================================================================
i2c = I2C(0, sda=Pin(PINO_SDA), scl=Pin(PINO_SCL), freq=400000)
enderecos = []
oled = htu = acel = apds = None
estado_i2c = {}          # nome -> "ok" / "ausente" / "falhou: ..."

leds = [None] * 4
rgb = [None] * 3
servo = rele = pot = som = botao = None
pino_piscando = -1
nivel_pisca = 0


def iniciar_i2c():
    global enderecos, oled, htu, acel, apds
    enderecos = i2c.scan()
    oled = htu = acel = apds = None
    estado_i2c.clear()

    end_oled = 0x3C if 0x3C in enderecos else (0x3D if 0x3D in enderecos else 0)
    if end_oled:
        try:
            oled = OLED(i2c, end_oled, cfg["OLEDSH"] == 1)
            estado_i2c["OLED"] = "ok"
        except Exception as e:
            estado_i2c["OLED"] = "falhou: %s" % e
    else:
        estado_i2c["OLED"] = "ausente"

    if HTU21D.END in enderecos:
        htu = HTU21D(i2c)
        estado_i2c["HTU21D"] = "ok"
    else:
        estado_i2c["HTU21D"] = "ausente"

    estado_i2c["MMA8452Q"] = "ausente"
    for end in (0x1D, 0x1C):
        if end in enderecos:
            try:
                acel = MMA8452Q(i2c, end)
                estado_i2c["MMA8452Q"] = "ok"
            except Exception as e:
                estado_i2c["MMA8452Q"] = "falhou: %s" % e
            break

    if APDS9960.END in enderecos:
        try:
            apds = APDS9960(i2c)
            estado_i2c["APDS9960"] = "ok"
        except Exception as e:
            estado_i2c["APDS9960"] = "falhou: %s" % e
    else:
        estado_i2c["APDS9960"] = "ausente"


def soltar(pino):
    """Volta o pino para entrada (estado seguro)."""
    if pino >= 0:
        try:
            Pin(pino, Pin.IN)
        except Exception:
            pass


def nivel_led(ligado, invertido):
    return (0 if ligado else 1) if invertido else (1 if ligado else 0)


def aplicar(chave):
    """Reconfigura so o recurso que mudou."""
    global servo, rele, pot, som, botao
    v = cfg[chave]
    if chave.startswith("LED") and chave != "LEDINV":
        i = int(chave[3])
        leds[i] = Pin(v, Pin.OUT, value=nivel_led(0, cfg["LEDINV"])) if v >= 0 else None
    elif chave == "LEDINV":
        for p in leds:
            if p:
                p.value(nivel_led(0, v))
    elif chave in ("RGBR", "RGBG", "RGBB"):
        i = "RGB".index(chave[3])
        if rgb[i]:
            rgb[i].deinit()
        rgb[i] = None
        if v >= 0:
            rgb[i] = PWM(Pin(v), freq=1000)
            rgb[i].duty_u16(65535 if cfg["RGBINV"] else 0)
    elif chave == "RGBINV":
        definir_rgb(0, 0, 0)
    elif chave == "SERVO":
        if servo:
            servo.deinit()
        servo = PWM(Pin(v), freq=50) if v >= 0 else None
    elif chave == "RELE":
        rele = Pin(v, Pin.OUT, value=0) if v >= 0 else None
    elif chave in ("POT", "SOM"):
        a = None
        if v >= 0:
            a = ADC(Pin(v))
            a.atten(ADC.ATTN_11DB)
        if chave == "POT":
            pot = a
        else:
            som = a
    elif chave in ("BTN", "BTNLOW"):
        p = cfg["BTN"]
        botao = None
        if p >= 0:
            if p in SO_ENTRADA:         # 34-39 nao tem resistor interno
                botao = Pin(p, Pin.IN)
            else:
                botao = Pin(p, Pin.IN, Pin.PULL_UP if cfg["BTNLOW"] else Pin.PULL_DOWN)
    elif chave == "OLEDSH":
        iniciar_i2c()
        oled_tela_inicial()


# =====================================================================
#  Acoes
# =====================================================================
def definir_rgb(r, g, b):
    for canal, valor in zip(rgb, (r, g, b)):
        if canal:
            valor = max(0, min(255, valor))
            if cfg["RGBINV"]:
                valor = 255 - valor
            canal.duty_u16(valor * 257)


def definir_servo(angulo):
    if servo is None:
        erro("servo sem pino")
        return
    angulo = max(0, min(180, angulo))
    servo.duty_u16(int(65535 * (0.5 + angulo / 90) / 20))   # 0,5 a 2,5 ms


def oled_texto(texto):
    if oled is None:
        erro("OLED nao encontrado")
        return
    oled.fill(0)
    linhas = []
    for bloco in texto.split("|"):
        while len(bloco) > 16:
            linhas.append(bloco[:16])
            bloco = bloco[16:]
        linhas.append(bloco)
    for i, ln in enumerate(linhas[:6]):
        oled.text(ln, 0, i * 10, 1)
    oled.show()


def oled_tela_inicial():
    if oled is None:
        return
    oled.fill(0)
    oled.text("PAINEL WISDOM", 12, 8, 1)
    oled.hline(0, 20, 128, 1)
    oled.text("Tio Rafa", 32, 30, 1)
    oled.text("aguardando PC...", 0, 48, 1)
    oled.show()


def ler_adc_u16(pino):
    try:
        a = ADC(Pin(pino))
        a.atten(ADC.ATTN_11DB)
        return a.read_u16()
    except Exception:
        return None


def pico_a_pico(adc, amostras=40):
    mn, mx = 65535, 0
    for _ in range(amostras):
        v = adc.read_u16()
        mn = min(mn, v)
        mx = max(mx, v)
    return mx - mn


def botao_apertado():
    v = botao.value()
    return int(v == 0) if cfg["BTNLOW"] else v


def descrever_hardware():
    lista = ",".join("0x%02X" % e for e in enderecos)
    enviar("I2C", lista if lista else "nenhum")
    enviar("SENS", ";".join("%s=%s" % (k, v) for k, v in estado_i2c.items()))


# =====================================================================
#  Descoberta de pinagem
# =====================================================================
def pinos_configurados():
    return [v for k, v in cfg.items() if k not in ("LEDINV", "RGBINV", "BTNLOW", "OLEDSH") and v >= 0]


def todos_adc():
    partes = []
    for p in ADC_CANDIDATOS:
        if p == pino_piscando:
            continue
        v = ler_adc_u16(p)
        partes.append("%d=%s" % (p, "null" if v is None else v))
    enviar("ADCS", ";".join(partes))


def todos_adc_pp():
    partes = []
    for p in ADC_CANDIDATOS:
        if p == pino_piscando:
            continue
        try:
            a = ADC(Pin(p))
            a.atten(ADC.ATTN_11DB)
            partes.append("%d=%d" % (p, pico_a_pico(a, 30)))
        except Exception:
            partes.append("%d=null" % p)
    enviar("ADCPP", ";".join(partes))


def todas_entradas():
    partes = []
    for p in ENTRADAS_CANDIDATAS:
        if p in (PINO_SDA, PINO_SCL) or p == pino_piscando:
            continue
        pino = Pin(p, Pin.IN) if p in SO_ENTRADA else Pin(p, Pin.IN, Pin.PULL_UP)
        partes.append("%d=%d" % (p, pino.value()))
    enviar("IN", ";".join(partes))


def buscar_i2c():
    """Procura outros barramentos I2C: linhas com resistor de pull-up externo."""
    reservados = pinos_configurados()
    candidatos = []
    for p in SAIDAS_CANDIDATAS:
        if p in reservados or p == 0 or p == pino_piscando:
            continue
        pino = Pin(p, Pin.IN, Pin.PULL_DOWN)
        time.sleep_ms(2)
        if pino.value() == 1:            # alto mesmo puxando para baixo
            candidatos.append(p)
        Pin(p, Pin.IN)
    enviar("INFO", "linhas com pull-up externo: %s" % (candidatos or "nenhuma"))
    achou = 0
    for sda in candidatos:
        for scl in candidatos:
            if sda == scl:
                continue
            try:
                barr = SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=100000, timeout=2000)
                ends = barr.scan()
            except Exception:
                ends = []
            if 0 < len(ends) < 20:
                achou += 1
                enviar("I2CBUS", "sda=%d;scl=%d;end=%s" % (
                    sda, scl, ",".join("0x%02X" % e for e in ends)))
            Pin(sda, Pin.IN)
            Pin(scl, Pin.IN)
    enviar("INFO", "busca I2C concluida: %d barramento(s) extra" % achou)


# =====================================================================
#  Comandos
# =====================================================================
def executar(linha):
    global pino_piscando
    partes = linha.split(",", 1)
    cmd = partes[0].strip().upper()
    arg = partes[1] if len(partes) > 1 else ""
    try:
        if cmd == "PING":
            enviar("PONG", "ok")
        elif cmd == "SCAN":
            iniciar_i2c()
            oled_tela_inicial()
            descrever_hardware()
        elif cmd == "CFG":
            chave, valor = arg.split(",")
            chave = chave.strip().upper()
            if chave not in cfg:
                erro("CFG desconhecido: " + chave)
                return
            novo = int(valor)
            antigo = cfg[chave]
            if chave in ("LEDINV", "RGBINV", "BTNLOW", "OLEDSH"):
                cfg[chave] = 1 if novo else 0
            else:
                if novo in (1, 3, 6, 7, 8, 9, 10, 11, PINO_SDA, PINO_SCL) or novo > 39:
                    erro("GPIO %d nao permitido para %s" % (novo, chave))
                    return
                if antigo >= 0 and antigo != novo:
                    soltar(antigo)
                cfg[chave] = novo
            aplicar(chave)
            enviar("CFG", "%s=%d" % (chave, cfg[chave]))
        elif cmd == "LED":
            i, v = [int(x) for x in arg.split(",")]
            if 0 <= i < 4 and leds[i]:
                leds[i].value(nivel_led(v, cfg["LEDINV"]))
            else:
                erro("LED %d sem pino" % i)
        elif cmd == "RGB":
            r, g, b = [int(x) for x in arg.split(",")]
            definir_rgb(r, g, b)
        elif cmd == "OLED":
            oled_texto(arg)
        elif cmd == "OLEDCLR":
            if oled:
                oled.fill(0)
                oled.show()
        elif cmd == "SERVO":
            definir_servo(int(arg))
        elif cmd == "RELE":
            if rele is None:
                erro("rele sem pino")
            else:
                rele.value(1 if int(arg) else 0)
        elif cmd == "GPIO":
            n, v = [int(x) for x in arg.split(",")]
            if n not in SAIDAS_CANDIDATAS:
                erro("GPIO %d nao pode ser saida" % n)
            else:
                Pin(n, Pin.OUT).value(1 if v else 0)
                enviar("GPIO", "%d=%d" % (n, v))
        elif cmd == "PISCAR":
            n = int(arg)
            soltar(pino_piscando)
            pino_piscando = -1
            if n >= 0:
                if n not in SAIDAS_CANDIDATAS:
                    erro("GPIO %d nao pode piscar" % n)
                else:
                    pino_piscando = n
            enviar("PISCAR", str(pino_piscando))
        elif cmd == "ADCALL":
            todos_adc()
        elif cmd == "ADCPP":
            todos_adc_pp()
        elif cmd == "INALL":
            todas_entradas()
        elif cmd == "I2CHUNT":
            buscar_i2c()
        else:
            erro("comando desconhecido: " + cmd)
    except Exception as e:
        erro(cmd + " " + str(e))


leitor = select.poll()
leitor.register(sys.stdin, select.POLLIN)
buffer_cmd = ""


def ler_comandos():
    global buffer_cmd
    while leitor.poll(0):
        c = sys.stdin.read(1)
        if c in ("\n", "\r"):
            if buffer_cmd:
                executar(buffer_cmd)
            buffer_cmd = ""
        else:
            buffer_cmd += c
            if len(buffer_cmd) > 200:
                buffer_cmd = ""


def num(v, casas=0):
    if v is None:
        return "null"
    return ("%." + str(casas) + "f") % v if casas else str(v)


# =====================================================================
#  Loop principal
# =====================================================================
iniciar_i2c()
for chave, pino in PINAGEM_PADRAO:
    executar("CFG,%s,%d" % (chave, pino))
oled_tela_inicial()
enviar("INFO", "Painel Wisdom v2.1 pronto (MicroPython)")
descrever_hardware()

t_rapido = t_lento = t_pisca = time.ticks_ms()

while True:
    ler_comandos()
    agora = time.ticks_ms()

    if pino_piscando >= 0 and time.ticks_diff(agora, t_pisca) >= INTERVALO_PISCA_MS:
        t_pisca = agora
        nivel_pisca ^= 1
        Pin(pino_piscando, Pin.OUT).value(nivel_pisca)

    if time.ticks_diff(agora, t_rapido) >= INTERVALO_RAPIDO_MS:
        t_rapido = agora
        p = pot.read_u16() if pot else None
        b = botao_apertado() if botao else None
        s = pico_a_pico(som) if som else None
        enviar("F", "pot=%s;btn=%s;som=%s" % (num(p), num(b), num(s)))

    if time.ticks_diff(agora, t_lento) >= INTERVALO_LENTO_MS:
        t_lento = agora
        t = h = luz = prox = ax = ay = az = None
        try:
            if htu:
                t, h = htu.medir()
        except Exception as e:
            erro("HTU21D " + str(e))
        try:
            if apds:
                luz = apds.luz()
                prox = apds.proximidade()
        except Exception as e:
            erro("APDS9960 " + str(e))
        try:
            if acel:
                ax, ay, az = acel.ler_g()
        except Exception as e:
            erro("MMA8452Q " + str(e))
        enviar("S", "t=%s;h=%s;luz=%s;prox=%s;ax=%s;ay=%s;az=%s" % (
            num(t, 1), num(h, 1), num(luz), num(prox), num(ax, 2), num(ay, 2), num(az, 2)))

    time.sleep_ms(5)
